from __future__ import annotations

from copy import deepcopy

import pytest

from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.api import analyze_documents
from q1timeline.analysis.interpreter import prepare_def_program
from q1timeline.diagnostics import Diagnostic
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import _append_condition_comparison, simulate_programs
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.render.html import render_html


def run(source, *, compare=(), **simulation):
    assumptions = {f"a:set_cond:{pc}:1": "both" for pc in compare}
    states = simulate_programs(
        {"a": parse_q1asm(source, file="compare.q1asm")}, branch_assumptions=assumptions,
        simulation={"startup_delay_ns": 20, **simulation},
        sequences={"a": {"acquisitions": {"data": {"index": 0, "num_bins": 4}}}},
    )
    return states[0]


def events(state, kind, path=None):
    return [event for event in state.events if event.kind == kind and event.meta.get("branch_comparison_path") == path]


@pytest.mark.parametrize("rewrite", [False, True])
def test_compare_frequency_commit_tracks_setter_identity_across_skipped_packets(rewrite):
    source = ("set_freq 4000000\nupd_param 4\nset_cond 1,1,0,4\n"
              + ("set_freq 4000000\n" if rewrite else "")
              + "upd_param 8\nset_cond 0,0,0,0\nupd_param 4\nstop")
    state = run(source, compare=(2,), startup_delay_ns=100)
    setters = [event for event in events(state, "latched_state_pending") if event.meta["field"] == "frequency"]
    assert len(setters) == (2 if rewrite else 1)
    common, recorded_later = events(state, "upd_param")
    execute, execute_later = events(state, "upd_param", "taken")
    skipped, = events(state, "conditional_skip", "fallthrough")
    skip_later, = events(state, "upd_param", "fallthrough")
    assert common.meta["committed_setter_event_ids"] == {"frequency": setters[0].id}
    for event in (execute, execute_later, skip_later, recorded_later):
        assert event.meta["applied_state"]["frequency"] == 4000000
        assert event.meta["applied_setter_event_ids"] == {"frequency": setters[-1].id}
    assert skipped.meta["captured_setter_event_ids"] == {"frequency": setters[-1].id}
    assert skipped.meta["committed_setter_event_ids"] == execute_later.meta["committed_setter_event_ids"] == {}
    for event, time in ((execute, 104), (skip_later, 108), (recorded_later, 108)):
        assert event.t0.value == time
        assert event.meta["committed_setter_event_ids"] == ({"frequency": setters[1].id} if rewrite else {})
    assert skip_later.meta["applied_updated_fields"] == []


def test_dynamic_same_line_frequency_setters_each_commit_to_their_own_packet():
    source = "move 2,R0\nagain: set_freq 4000000\nupd_param 4\nloop R0,@again\nstop"
    state = run(source, startup_delay_ns=100)
    setters = [event for event in events(state, "latched_state_pending") if event.meta["field"] == "frequency"]
    updates = events(state, "upd_param")
    assert len(setters) == len(updates) == 2
    assert setters[0].source == setters[1].source and setters[0].id != setters[1].id
    assert [packet.meta["committed_setter_event_ids"] for packet in state.rt_packets] == [
        {"frequency": setter.id} for setter in setters
    ]
    assert [event.meta["committed_setter_event_ids"] for event in updates] == [
        {"frequency": setter.id} for setter in setters
    ]


def test_compare_commit_remaps_a_setter_that_only_executes_in_one_rerun():
    source = ("set_cond 1,1,0,4\nupd_param 80\nset_cond 0,0,0,0\n" + "nop\n" * 8
              + "set_freq 4000000\nupd_param 4\nstop")
    state = run(source, compare=(0,))
    setter, = [event for event in events(state, "latched_state_pending", "taken") if event.meta["field"] == "frequency"]
    issue, = [event for event in events(state, "q1_issue", "taken") if event.meta["op"] == "set_freq"]
    assert setter.meta["q1_issue_event_id"] == issue.id
    execute, = [event for event in events(state, "upd_param", "taken") if event.source.line == setter.source.line + 1]
    assert execute.meta["committed_setter_event_ids"] == execute.meta["applied_setter_event_ids"] == {"frequency": setter.id}
    assert not any(event.meta.get("field") == "frequency" for event in events(state, "latched_state_pending", "fallthrough"))


@pytest.mark.parametrize("setter_op,field", [("set_ph_delta 250000000", "phase_delta"), ("reset_ph", "phase_reset")])
def test_compare_one_shot_phase_commit_and_skipped_capture_stay_distinct(setter_op, field):
    source = f"set_cond 1,1,0,4\n{setter_op}\nupd_param 4\nset_cond 0,0,0,0\nupd_param 4\nstop"
    state = run(source, compare=(0,), startup_delay_ns=100)
    setter, = [event for event in events(state, "latched_state_pending") if event.meta["field"] == field]
    first, later = events(state, "upd_param", "taken")
    skipped, = events(state, "conditional_skip", "fallthrough")
    skip_later, = events(state, "upd_param", "fallthrough")
    assert first.meta["committed_setter_event_ids"] == {field: setter.id}
    assert skipped.meta["captured_setter_event_ids"] == {field: setter.id}
    assert all(event.meta["committed_setter_event_ids"] == {} for event in (later, skipped, skip_later))


def test_compare_common_data_links_execute_packet_and_keeps_recorded_hardware():
    source = "set_cond 1,1,0,8\nfb_com_data 1,42,4\nset_cond 0,0,0,4\nwait 20\nstop"
    options = {"routes": [{"source": "a", "target": "a", "id": 1, "latency_ns": 4}]}
    recorded = run(source, **options)
    compared = run(source, compare=(0,), **options)
    send, = events(compared, "feedback_send", "taken")
    receive, = events(compared, "feedback_receive", "taken")
    command, = events(compared, "fb_com_data", "taken")
    assert command.meta["rt_packet_id"] != recorded.rt_packets[0].id
    assert send.meta["source_packet_id"] == command.meta["rt_packet_id"]
    assert receive.meta["source_event_id"] == send.id
    flow, = timeline_ir_from_states([compared])["feedback_flows"]
    assert (flow["from_event_id"], flow["to_event_id"]) == (send.id, receive.id)
    assert command.meta["q1_issue_event_id"] == send.meta["q1_issue_event_id"] == recorded.rt_packets[0].meta["q1_issue_event_id"]
    assert compared.rt_packets == recorded.rt_packets
    assert compared.metadata["simulation"] == recorded.metadata["simulation"]
    assert not events(compared, "feedback_send")


def test_feedback_arrival_consumer_respects_each_comparison_path():
    source = ("set_cond 1,1,0,8\nwait 4\nset_cond 0,0,0,4\nfb_com_data 1,42,4\n"
              "wait 100\nfb_pop_data 1,R0\nnop\nstop")
    state = run(source, compare=(0,), routes=[{"source": "a", "target": "a", "id": 1, "latency_ns": 4}])
    flows = timeline_ir_from_states([state])["feedback_flows"]
    assert len(flows) == 3
    for path in (None, "taken", "fallthrough"):
        arrival, = events(state, "feedback_receive", path)
        flow, = [flow for flow in flows if flow["to_event_id"] == arrival.id]
        consumer, = [event for event in events(state, "q1_issue", path) if event.meta["op"] == "fb_pop_data"]
        assert consumer.meta["input_event_ids"] == [arrival.id]
        assert flow["consumer_event_id"] == consumer.id
        assert arrival.source.file == "<simulation>"


def test_compare_combined_tb_rewrites_nested_provenance_and_shares_setter():
    source = ("set_freq 4000000\nfb_acq_tb_id 16,4\nfb_acq_tb_cfg 1,2,1,4\nset_cond 1,1,0,8\n"
              "acquire 0,0,16\nset_cond 0,0,0,4\nwait 40\nstop")
    state = run(source, compare=(3,), routes=[{"source": "a", "target": "a", "id": 16, "latency_ns": 4}],
                inputs={"acquisitions": [{"sequencer": "a", "acquisition_index": 0, "bin_index": 0, "thresholded": 1}]})
    setter = next(event for event in events(state, "latched_state_pending") if event.meta["field"] == "frequency")
    acquire, = events(state, "acquire", "taken")
    result, = events(state, "acquisition_result", "taken")
    contribution, = events(state, "feedback_contribution", "taken")
    send, = events(state, "feedback_send", "taken")
    receive, = events(state, "feedback_receive", "taken")
    assert acquire.meta["applied_setter_event_ids"]["frequency"] == setter.id
    assert acquire.meta["applied_state"]["frequency"] == 4000000
    assert send.meta["value"] == receive.meta["value"] == 12
    assert send.meta["contributors"] == receive.meta["contributors"] == [{
        "sequencer": "a", "source_event_id": contribution.id, "acquisition_event_id": result.id,
        "rt_packet_id": acquire.meta["rt_packet_id"], "value": 3, "bit_position": 2, "length": 1, "latency_ns": 4,
    }]


def test_compare_qtm_result_retains_shared_open_packet_and_input_event():
    source = ("acquire_timetags 0,0,1,0,16\nset_cond 1,1,0,8\nacquire_timetags 0,0,0,0,4\n"
              "set_cond 0,0,0,4\nwait 20\nstop")
    state = run(source, compare=(1,), inputs={"digital": {"a": {
        "initial_level": 0, "transitions": [{"time_ticks": 24 * 128, "level": 1}],
    }}})
    open_event, = events(state, "acquire")
    close, = events(state, "acquire", "taken")
    result, = events(state, "acquisition_result", "taken")
    assert result.meta["open_packet_id"] == open_event.meta["rt_packet_id"]
    assert result.meta["rt_packet_id"] == close.meta["rt_packet_id"]
    assert result.meta["source_event_id"] == close.id
    input_id, = result.meta["input_event_ids"]
    assert input_id == result.meta["time_source_event_id"]
    assert next(event for event in state.events if event.id == input_id).meta.get("branch_comparison_path") is None


def test_compare_truth_table_links_alternate_and_shared_inputs_across_sequencers():
    sources = {
        "left": ("acquire_timetags 0,0,1,0,20\nset_cond 1,1,0,8\nacquire_timetags 0,0,0,0,4\n"
                 "set_cond 0,0,0,4\nwait 20\nstop"),
        "right": "acquire_timetags 0,0,1,0,20\nacquire_timetags 0,0,0,0,4\nstop",
        "control": "wait_trigger 3,4\nstop",
    }
    states = simulate_programs(
        {name: parse_q1asm(source, file=f"{name}.q1asm") for name, source in sources.items()},
        sequences={name: {"acquisitions": {"data": {"index": 0, "num_bins": 2}}} for name in ("left", "right")},
        branch_assumptions={"left:set_cond:1:1": "both"},
        simulation={
            "startup_delay_ns": 100,
            "digital": {name: {"module": "qtm0", "channel": channel, "count_thresholds": {"low": 1, "high": 2}}
                        for name, channel in (("left", 4), ("right", 6))},
            "inputs": {"digital": {
                "left": {"initial_level": 0, "transitions": [{"time_ticks": time * 128, "level": level}
                                                            for time, level in ((104, 1), (105, 0), (108, 1))]},
                "right": {"initial_level": 0, "transitions": []},
            }},
            "trigger_truth_tables": {"qtm0": [{"when": {4: "high", 6: "low"}, "address": 3}]},
            "trigger_routes": [{"source_module": "qtm0", "target": "control", "address": 3, "latency_ns": 4}],
        },
    )
    left, right, control = states
    left_result, = events(left, "acquisition_result", "taken")
    right_result, = events(right, "acquisition_result")
    send, = events(left, "trigger_send", "taken")
    receive, = events(control, "trigger_receive", "taken")
    assert receive.meta["source_event_id"] == send.id
    assert send.meta["source_event_ids"] == receive.meta["source_event_ids"] == [left_result.id, right_result.id]
    assert send.meta["source_rt_packet_ids"] == receive.meta["source_rt_packet_ids"] == [
        left_result.meta["rt_packet_id"], right_result.meta["rt_packet_id"],
    ]


def test_simultaneous_compare_controls_get_disjoint_packet_identities():
    source = ("set_cond 1,1,0,8\nwait 4\nset_cond 0,0,0,4\nset_cond 1,2,0,8\nwait 4\n"
              "set_cond 0,0,0,4\nwait 20\nstop")
    state = run(source, compare=(0, 3))
    controls = [event for event in events(state, "q1_issue") if event.meta.get("rt_condition_control")]
    assert {event.meta["branch_id"] for event in controls} == {"a:set_cond:0:1", "a:set_cond:3:1"}
    suffixes = [event for event in state.events if event.kind == "wait" and event.source.line == 7]
    copies = [event for event in suffixes if event.meta.get("branch_comparison_path")]
    assert len(copies) == 4
    assert len({event.meta["rt_packet_id"] for event in suffixes}) == 5
    assert len({event.id for event in state.events}) == len(state.events)


def test_copying_nested_comparison_references_does_not_mutate_reruns():
    recorded = run("wait 4\nstop")
    true_path = deepcopy(recorded)
    false_path = deepcopy(recorded)
    wait, = events(true_path, "wait")
    packet, = true_path.rt_packets
    wait.meta["truth_round"] = {"inputs": {"0": {"event_id": wait.id, "rt_packet_id": packet.id}},
                                "rules": [{"trigger_event_id": wait.id, "label": wait.id}],
                                "value": packet.id}
    before = deepcopy(true_path.events)
    original = deepcopy(recorded.events)
    _append_condition_comparison([recorded], [list(recorded.events)], [true_path], [false_path], "branch")
    copied, = events(recorded, "wait", "taken")
    assert copied.meta["truth_round"] == {"inputs": {"0": {"event_id": copied.id, "rt_packet_id": copied.meta["rt_packet_id"]}},
                                         "rules": [{"trigger_event_id": copied.id, "label": wait.id}],
                                         "value": packet.id}
    assert true_path.events == before
    assert recorded.events[:len(original)] == original


def _unclosed_gate_run(gate, *, compare=False, prepared=False):
    programs = {
        "a": parse_q1asm(f"set_cond 1,1,0,4\n{gate}\nstop", file="gate.q1asm"),
        "peer": parse_q1asm("wait 200\nstop", file="peer.q1asm"),
    }
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    return simulate_programs(
        programs, simulation={"startup_delay_ns": 100}, prepared_programs=bindings,
        branch_assumptions={"a:set_cond:0:1": "both" if compare else "taken"},
        sequences={"a": {"acquisitions": {"gate": {"index": 0, "num_bins": 1}}}},
    )


@pytest.mark.parametrize("gate,fine_delay", [("acquire_ttl 0,0,1,4", 0), ("acquire_timetags 0,0,1,3,4", 3)])
def test_compare_unclosed_gate_terminal_uses_local_stop_and_links_incomplete_result(gate, fine_delay):
    state, peer = _unclosed_gate_run(gate, compare=True)
    terminal, = events(state, "branch_terminal", "taken")
    result, = events(state, "acquisition_result", "taken")
    execute_stop, = events(state, "stop", "taken")
    skip_stop, = events(state, "stop", "fallthrough")
    assert terminal.t0.value == terminal.t1.value == result.t0.value == execute_stop.t0.value == skip_stop.t0.value == 104
    assert result.meta["start_ticks"] == 100 * 128 + fine_delay and result.meta["end_ticks"] == 104 * 128
    assert result.meta["status"] == "incomplete"
    assert terminal.source.line == 2 and terminal.source.raw == gate
    assert terminal.meta["comparison_terminal"] == {"status": "blocked", "reason": "Digital acquisition gate was not closed.",
                                                     "source_event_ids": [result.id]}
    assert state.metadata["execution"]["status"] == peer.metadata["execution"]["status"] == "completed"
    assert not state.diagnostics and not peer.diagnostics
    assert peer.rt_time_ns.value == 300 and state.metadata["simulation"]["end_time_ns"] == 300
    assert not events(state, "branch_terminal", "fallthrough")


@pytest.mark.parametrize("gate", ["acquire_ttl 0,0,1,4", "acquire_timetags 0,0,1,3,4"])
@pytest.mark.parametrize("prepared", [False, True])
def test_unclosed_gate_diagnostic_links_to_retained_evidence(gate, prepared):
    state, peer = _unclosed_gate_run(gate, prepared=prepared)
    diagnostic, = state.diagnostics
    assert state.metadata["execution"]["status"] == "blocked" and state.rt_time_ns.value == 104
    assert peer.metadata["execution"]["status"] == "completed" and peer.rt_time_ns.value == 300
    assert diagnostic.source.file == "gate.q1asm" and diagnostic.source.line == 2
    result, = state.metadata["simulation"]["acquisitions"]
    assert result["status"] == "incomplete" and result["time_ns"] == 104
    event_id, = diagnostic.related_events
    evidence = next(event for event in state.events if event.id == event_id)
    if prepared:
        assert evidence.kind == "acquire" and evidence.id == result["source_event_id"]
    else:
        assert evidence.kind == "acquisition_result" and evidence.id == result["event_id"]


def test_comparison_terminal_keeps_only_retained_evidence_in_diagnostic_order():
    recorded = run("wait 4\nstop")
    execute = deepcopy(recorded)
    skip = deepcopy(recorded)
    discarded = execute.events.pop()
    wait, = events(execute, "wait")
    execute.metadata["execution"].update(status="blocked", reason="Blocked after the shared wait.")
    diagnostic = Diagnostic("warning", "analysis_incomplete", "Blocked after the shared wait.", wait.source,
                            [discarded.id, wait.id, "missing-event", wait.id])
    execute.diagnostics.append(diagnostic)
    _append_condition_comparison([recorded], [list(recorded.events)], [execute], [skip], "branch",
                                 terminal_edges={"taken": {"a": (wait.t1, wait.source, diagnostic.category)}})
    terminal, = events(recorded, "branch_terminal", "taken")
    assert terminal.meta["comparison_terminal"]["source_event_ids"] == [wait.id]
    assert diagnostic.related_events == [discarded.id, wait.id, "missing-event", wait.id]
    assert not recorded.diagnostics


def _threshold_compare(programs, *, compare, bins):
    options = {"startup_delay_ns": 100,
               "trigger_routes": [{"source": "tx", "target": "rx", "address": 3, "latency_ns": 8}],
               "inputs": {"acquisitions": [{"sequencer": "tx", "acquisition_index": 0, "bin_index": index, "i": 1}
                                           for index in range(bins)],
                          "triggers": [{"target": "rx", "address": 3, "time_ns": 150}]}}
    return simulate_programs({name: parse_q1asm(source, file=f"{name}.q1asm") for name, source in programs.items()},
                             simulation=options, branch_assumptions={branch_id: "both" for branch_id in compare},
                             sequences={"tx": {"acquisitions": {"readout": {"index": 0, "num_bins": bins}}}})


@pytest.mark.parametrize("second_comparison", [False, True])
def test_comparison_terminal_links_cross_sequencer_evidence_from_its_own_rerun(second_comparison):
    tx = "set_cond 1,1,0,4\nacquire 0,0,4\nset_cond 0,0,0,4\n"
    branches = {"tx:set_cond:0:1": 0}
    if second_comparison:
        tx += "set_cond 1,2,0,4\nacquire 0,1,4\nset_cond 0,0,0,4\n"
        branches["tx:set_cond:3:1"] = 1
    programs = {"tx": tx + "stop", "rx": "wait_trigger 3,4\nstop"}
    baseline = _threshold_compare(programs, compare=(), bins=len(branches))
    compared = _threshold_compare(programs, compare=branches, bins=len(branches))
    by_id = {event.id: event for state in compared for event in state.events}
    terminals = events(compared[1], "branch_terminal", "taken")
    assert len(terminals) == len(branches)
    for terminal in terminals:
        branch = terminal.meta["branch_comparison_branch_id"]
        outcome = terminal.meta["comparison_terminal"]
        assert outcome["status"] == "blocked"
        source_id, = outcome["source_event_ids"]
        source = by_id[source_id]
        assert source.sequencer_id == "tx" and source.kind == "acquisition_result"
        assert source.meta["bin_index"] == branches[branch]
        assert source.meta["branch_comparison_path"] == "taken" and source.meta["branch_comparison_branch_id"] == branch
    assert {terminal.meta["branch_comparison_branch_id"] for terminal in terminals} == branches.keys()
    assert len({terminal.meta["comparison_terminal"]["source_event_ids"][0] for terminal in terminals}) == len(branches)
    assert events(compared[1], "trigger_receive") and events(compared[1], "trigger_receive", "fallthrough")
    for original, state in zip(baseline, compared):
        assert state.metadata["execution"] == original.metadata["execution"]
        assert state.metadata["simulation"] == original.metadata["simulation"]
        assert state.diagnostics == original.diagnostics and state.rt_packets == original.rt_packets


def test_comparison_terminal_can_link_shared_prefix_evidence_from_another_sequencer():
    tx, rx = _threshold_compare({"tx": "acquire 0,0,4\nstop",
                                 "rx": "set_cond 1,1,0,4\nwait_trigger 3,4\nset_cond 0,0,0,4\nstop"},
                                compare=("rx:set_cond:0:1",), bins=1)
    shared, = events(tx, "acquisition_result")
    terminal, = events(rx, "branch_terminal", "taken")
    assert terminal.meta["comparison_terminal"]["source_event_ids"] == [shared.id]
    assert not events(tx, "acquisition_result", "taken")
    assert not rx.diagnostics and rx.metadata["execution"]["status"] == "completed"


@pytest.mark.parametrize("operation,status,reason", [
    ("fb_acq_tb_mock 1,1,1,4", "blocked", "duration versus count"),
    ("wait_trigger 2,4", "blocked", "No scheduled input can resume trigger_wait"),
    ("fb_cmd 0x42,0x80,4", "error", "reserved bits"),
])
def test_incomplete_execute_keeps_terminal_reason_separate_from_recorded_run(operation, status, reason):
    source = f"set_cond 1,1,0,8\n{operation}\nset_cond 0,0,0,4\nwait 20\nstop"
    recorded = run(source)
    compared = run(source, compare=(0,))
    terminal, = events(compared, "branch_terminal", "taken")
    assert terminal.lane == "branch_compare.taken.rt.simulation"
    assert terminal.source.file == "compare.q1asm"
    assert terminal.source.line == 2 and terminal.source.raw == operation
    assert terminal.t0.value == terminal.t1.value == 20 and terminal.duration.value == 0
    assert terminal.meta["comparison_terminal"]["status"] == status
    assert reason in terminal.meta["comparison_terminal"]["reason"]
    if operation.startswith("wait_trigger"):
        wait, = events(compared, "trigger_wait", "taken")
        assert wait.source == terminal.source and wait.meta["pending"] is True
        assert terminal.meta["comparison_terminal"]["source_event_ids"] == [wait.id]
        assert not events(compared, "trigger_wait", "fallthrough")
    assert terminal.meta["branch_comparison_branch_id"] == "a:set_cond:0:1"
    assert events(compared, "wait", "fallthrough") and events(compared, "stop", "fallthrough")
    assert compared.metadata["execution"] == recorded.metadata["execution"]
    assert compared.metadata["execution"]["status"] == "completed"
    assert compared.diagnostics == recorded.diagnostics
    assert compared.rt_packets == recorded.rt_packets
    assert compared.metadata["simulation"] == recorded.metadata["simulation"]


def test_compare_execute_full_q1_queue_retains_pending_trigger_wait_evidence():
    source = "set_cond 1,1,0,8\nwait_trigger 2,4\nset_cond 0,0,0,4\nwait 4\nwait 4\nwait 4\nstop"
    recorded = run(source, queue_capacity=1)
    compared = run(source, compare=(0,), queue_capacity=1)
    wait, = events(compared, "trigger_wait", "taken")
    terminal, = events(compared, "branch_terminal", "taken")
    stalls = [event for event in events(compared, "q1_stall", "taken") if event.meta.get("pending")]
    stall, = stalls
    assert wait.source == terminal.source and terminal.source.line == 2
    assert wait.meta["pending"] is True
    assert stall.source.line == 5 and stall.meta["reason"] == "queue_full"
    assert terminal.meta["comparison_terminal"] == {"status": "blocked",
                                                    "reason": "No scheduled input can resume trigger_wait.",
                                                    "source_event_ids": [wait.id]}
    assert compared.metadata["execution"] == recorded.metadata["execution"]
    assert compared.metadata["execution"]["status"] == "completed"
    assert compared.diagnostics == recorded.diagnostics
    assert events(compared, "stop", "fallthrough")


def test_compare_execute_time_limit_keeps_the_pending_wait_and_raw_local_reason():
    source = "set_cond 1,1,0,4\nwait_trigger 2,4\nset_cond 0,0,0,4\nwait 20\nstop"
    scenario = {"startup_delay_ns": 100, "max_time_ns": 160,
                "inputs": {"triggers": [{"target": "a", "time_ns": 200, "address": 2}]}}
    recorded = run(source, **scenario)
    compared = run(source, compare=(0,), **scenario)
    wait, = events(compared, "trigger_wait", "taken")
    terminal, = events(compared, "branch_terminal", "taken")
    assert terminal.source == wait.source and terminal.source.line == 2
    assert terminal.t0 == terminal.t1 == wait.t1 and wait.t1.value == 160
    assert wait.meta["pending"] is True
    assert terminal.meta["comparison_terminal"] == {
        "status": "step_limit", "reason": "Reached simulation time limit.",
        "raw_reason": "Reached simulation time limit 160 ns.", "time_limit_local_ns": 160,
        "source_event_ids": [wait.id],
    }
    assert compared.metadata["execution"] == recorded.metadata["execution"]
    assert compared.metadata["execution"]["status"] == "completed"
    assert compared.diagnostics == recorded.diagnostics
    align_timelines([compared])
    assert terminal.meta["aligned_t0"] == 60
    html = render_html(timeline_ir_from_states([compared]))
    assert "Execution step limit · 60 ns · Reached simulation time limit." in html


def test_compare_netzero_time_limit_links_the_observed_signal_and_uses_display_reason():
    source = "set_awg_offs 16384,0\nupd_param 8\nset_awg_offs 0,0\nset_cond 1,1,0,0\nplay_netzero 100,0\nstop"
    physical = {"sequencers": {"a": {"netzero": {"module_type": "QCM", "outputs": [{
        "output": 0, "path": 0, "enabled": True, "ihp_enabled": True, "amplitude": 0.5, "slew_rate_per_s": 2.5e8,
    }]}}}}
    compared = run(source, compare=(3,), startup_delay_ns=100, max_time_ns=112, physical=physical)
    terminal, = events(compared, "branch_terminal", "taken")
    result, = events(compared, "netzero_result", "taken")
    assert terminal.source == result.source and terminal.source.raw == "play_netzero 100,0"
    assert terminal.t0 == result.t1 and terminal.t0.value == 112
    assert terminal.meta["comparison_terminal"] == {
        "status": "step_limit", "reason": "Reached simulation time limit.",
        "raw_reason": "Reached simulation time limit 112 ns.", "time_limit_local_ns": 112,
        "source_event_ids": [result.id],
    }
    assert compared.metadata["execution"]["status"] == "completed" and not compared.diagnostics
    align_timelines([compared])
    assert result.meta["aligned_t1"] == terminal.meta["aligned_t0"] == 12
    assert "Execution step limit · 12 ns · Reached simulation time limit." in render_html(timeline_ir_from_states([compared]))


def test_compare_qtm_bin_aggregate_preserves_the_recorded_write_history():
    window = "acquire_timetags 0,0,1,0,8\nacquire_timetags 0,0,0,0,4\n"
    source = window + "set_cond 1,1,0,4\n" + window + "set_cond 0,0,0,4\n" + window + "stop"
    transitions = [{"time_ticks": time * 128 + offset, "level": level}
                   for time in (101, 113, 126) for offset, level in ((0, 1), (1, 0))]
    state = run(source, compare=(2,), startup_delay_ns=100,
                inputs={"digital": {"a": {"initial_level": 0, "transitions": transitions}}})
    ir = timeline_ir_from_states([state])
    recorded = [event for event in ir["events"] if event["kind"] == "acquisition_result"
                and "branch_comparison_path" not in event["meta"]]
    execute = [event for event in ir["events"] if event["kind"] == "acquisition_result"
               and event["meta"].get("branch_comparison_path") == "taken"]
    assert [event["meta"]["bin_aggregate"]["avg_cnt"] for event in recorded] == [1, 2]
    assert [event["meta"]["bin_aggregate"]["avg_cnt"] for event in execute] == [2, 3]
    assert [row["bin_aggregate"] for row in ir["simulation"]["a"]["acquisitions"]] == [
        event["meta"]["bin_aggregate"] for event in recorded
    ]
    assert ir["simulation"]["a"]["digital_bins"][0]["avg_cnt"] == 2


def test_compare_execute_time_limit_links_the_clipped_released_rt_command():
    source = "wait 20\nset_cond 1,1,0,4\nwait_trigger 2,40\nset_cond 0,0,0,4\nwait 8\nstop"
    scenario = {"startup_delay_ns": 100, "max_time_ns": 160,
                "inputs": {"triggers": [{"target": "a", "time_ns": 150, "address": 2}]}}
    recorded = run(source, **scenario)
    compared = run(source, compare=(1,), **scenario)
    wait, = events(compared, "wait_trigger", "taken")
    terminal, = events(compared, "branch_terminal", "taken")
    assert terminal.source == wait.source and terminal.source.line == 3
    assert wait.t0.value == 120 and wait.t1.value == terminal.t0.value == 160
    assert wait.meta["rt_execution_ns"] == 150 and wait.meta["planned_rt_end_ns"] == 190
    assert wait.meta["observation_cutoff_ns"] == 160
    assert terminal.meta["comparison_terminal"]["source_event_ids"] == [wait.id]
    assert terminal.meta["comparison_terminal"]["reason"] == "Reached simulation time limit."
    assert compared.metadata["execution"] == recorded.metadata["execution"]
    assert compared.metadata["execution"]["status"] == "completed"
    assert compared.rt_packets == recorded.rt_packets
    assert compared.diagnostics == recorded.diagnostics
    align_timelines([compared])
    assert wait.meta["aligned_t1"] == terminal.meta["aligned_t0"] == 60


def test_existing_alternative_fault_carries_reason_without_extra_terminal_point():
    programs = {
        "a": parse_q1asm("set_cond 1,1,0,8\nfb_com_data 1,42,4\nstop", file="sender.q1asm"),
        "b": parse_q1asm("wait 0\nfb_pop_data 1,R0\nillegal", file="receiver.q1asm"),
    }
    options = {"startup_delay_ns": 20, "routes": [{"source": "a", "target": "b", "id": 1, "latency_ns": 4}]}
    _, recorded = simulate_programs(programs, simulation=options)
    _, compared = simulate_programs(programs, simulation=options, branch_assumptions={"a:set_cond:0:1": "both"})
    fault, = events(compared, "illegal", "taken")
    issue, = [event for event in events(compared, "q1_issue", "taken") if event.meta["op"] == "illegal"]
    assert fault.meta["comparison_terminal"] == {"status": "error", "reason": "Illegal Q1ASM instruction executed.",
                                                 "source_event_ids": [issue.id]}
    assert fault.source.line == 3 and fault.source.file == "receiver.q1asm"
    assert not events(compared, "branch_terminal", "taken")
    assert compared.metadata["execution"] == recorded.metadata["execution"]
    assert compared.diagnostics == recorded.diagnostics


def test_alternate_fifo_rejection_reuses_arrival_and_displays_aligned_failure_edge():
    ir = analyze_documents({
        "a.q1asm": "set_cond 1,1,0,8\nfb_com_data 1,42,4\nstop",
        "b.q1asm": "wait 100\nstop",
    }, execution_mode="simulation", branch_assumptions={"a:set_cond:0:1": "both"}, simulation={
        "startup_delay_ns": 20, "feedback_queue_capacity": 1,
        "routes": [{"source": "a", "target": "b", "id": 1, "latency_ns": 4}],
        "inputs": {"feedback": [{"target": "b", "time_ns": 0, "id": 2, "value": 8}]},
    })
    rejection, = [event for event in ir["events"] if "comparison_terminal" in event["meta"]]
    assert rejection["kind"] == "feedback_rejected" and rejection["sequencer_id"] == "b"
    assert rejection["meta"]["branch_comparison_path"] == "taken"
    assert rejection["meta"]["aligned_t0"] == 4
    waits = [event for event in ir["events"] if event["kind"] == "wait" and event["sequencer_id"] == "b"]
    alternate, = [event for event in waits if event["meta"].get("branch_comparison_path") == "taken"]
    baseline, = [event for event in waits if "branch_comparison_path" not in event["meta"]]
    assert alternate["meta"]["local_t0"] == 20 and alternate["meta"]["local_t1"] == 24
    assert alternate["meta"]["planned_rt_end_ns"] == 120 and alternate["meta"]["observation_cutoff_ns"] == 24
    assert baseline["meta"]["local_t1"] == 120 and "observation_cutoff_ns" not in baseline["meta"]
    assert ir["execution"]["b"]["status"] == "completed"
    assert "terminal" not in ir["execution"]["b"]
    assert not ir["diagnostics"]
    assert "Execution error · 4 ns · Feedback queue overflow · LINQ ID 1</title>" in render_html(ir)


def test_comparison_q1_validation_links_only_the_clipped_rt_alternative():
    state = run("set_cond 1,1,0,0\nwait 100\nmove 3,R0\nadd R0,1,R1\nstop", compare=(0,), startup_delay_ns=8)
    executed, = events(state, "wait", "taken")
    skipped, = events(state, "conditional_skip", "fallthrough")
    execute_terminal, = events(state, "branch_terminal", "taken")
    skip_terminal, = events(state, "branch_terminal", "fallthrough")
    assert executed.t0.value == 8 and executed.t1.value == execute_terminal.t0.value == skip_terminal.t0.value == 12
    assert executed.meta["planned_rt_end_ns"] == 108 and executed.meta["observation_cutoff_ns"] == 12
    assert skipped.t0 == skipped.t1 == executed.t0 and "observation_cutoff_ns" not in skipped.meta
    assert execute_terminal.source.raw == skip_terminal.source.raw == "add R0,1,R1"
    assert execute_terminal.meta["comparison_terminal"]["source_event_ids"] == [executed.id]
    assert "source_event_ids" not in skip_terminal.meta["comparison_terminal"]
    assert all(event.meta.get("op") != "add" for event in state.events if event.kind == "q1_issue")


def test_incomplete_alternative_can_end_before_a_recorded_q1_branch_choice():
    program = parse_q1asm("move 1,R0\nnop\nset_cond 1,1,0,8\nfb_acq_tb_mock 1,1,1,4\n"
                          "set_cond 0,0,0,4\nloop R0,@end\nwait 20\nend: stop", file="compare.q1asm")
    compared, = simulate_programs({"a": program}, simulation={"startup_delay_ns": 20},
                                  branch_assumptions={"a:set_cond:2:1": "both"}, branch_choices={("a", 5, 1): False})
    terminal, = events(compared, "branch_terminal", "taken")
    assert terminal.source.line == 4
    assert compared.metadata["execution"]["status"] == "completed"
    loop_issue, = [event for event in events(compared, "q1_issue") if event.meta["op"] == "loop"]
    assert loop_issue.meta["branch_taken"] is False


def test_multiple_comparisons_keep_distinct_terminal_ids_and_display_selected_time_basis():
    source = ("set_cond 1,1,0,8\nfb_acq_tb_mock 1,1,1,4\nset_cond 0,0,0,4\n"
              "set_cond 1,2,0,8\nfb_acq_tb_mock 1,1,1,4\nset_cond 0,0,0,4\nwait 20\nstop")
    assumptions = {f"a:set_cond:{pc}:1": "both" for pc in (0, 3)}
    for alignment, offset in (("first_rt_dequeue", -28), ("none", 0)):
        ir = analyze_documents({"a.q1asm": source}, execution_mode="simulation", alignment_mode=alignment,
                               branch_assumptions=assumptions, simulation={"startup_delay_ns": 28})
        terminals = [event for event in ir["events"] if event["kind"] == "branch_terminal"]
        assert len(terminals) == len({event["id"] for event in terminals}) == 2
        assert {event["meta"]["branch_comparison_branch_id"] for event in terminals} == set(assumptions)
        html = render_html(ir)
        assert "Execute · RT" in html and "Skip · RT" in html
        for terminal in terminals:
            time = terminal["t0"]["value"] + offset
            assert terminal["meta"]["aligned_t0"] == time
            assert f"Execution blocked · {time} ns" in html
