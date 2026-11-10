from __future__ import annotations

from copy import deepcopy

import pytest

from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.interpreter import prepare_def_program
from q1timeline.analysis.values import Concrete
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.render.html import render_html
from q1timeline.simulation.config import validate_simulation
from q1timeline.simulation.runtime import simulate_programs


@pytest.mark.parametrize(("config", "field", "unknown"), [
    ({"routes": [{"source": "seq", "target": "seq", "latency_ns": 0, "idd": 5}]}, "routes[0]", "idd"),
    ({"trigger_routes": [{"source": "seq", "target": "seq", "latency_ns": 0, "address": 1, "threshold": 0}]}, "trigger_routes[0]", "threshold"),
    ({"inputs": {"feedback": [{"target": "seq", "time_ns": 0, "id": 5, "value": 7, "vallue": 8}]}}, "inputs.feedback[0]", "vallue"),
    ({"inputs": {"triggers": [{"target": "seq", "time_ns": 0, "address": 1, "adress": 2}]}}, "inputs.triggers[0]", "adress"),
    ({"inputs": {"acquisitions": [{"sequencer": "seq", "acquisition_index": 0, "bin_index": 0, "i": 1, "threshholded": 0}]}}, "inputs.acquisitions[0]", "threshholded"),
])
def test_nested_config_rejects_unknown_fields_before_they_change_meaning(config, field, unknown) -> None:
    original = deepcopy(config)
    with pytest.raises(ValueError) as error:
        validate_simulation(config, ["seq"])
    assert f"simulation.{field}" in str(error.value)
    assert unknown in str(error.value)
    assert config == original


def test_nested_config_accepts_declared_fields_without_mutating_input() -> None:
    config = {
        "inputs": {
            "feedback": [{"target": "seq", "time_ns": 0, "id": 255, "value": 0xFFFFFFFF}],
            "triggers": [{"target": "seq", "time_ns": 0, "address": 15}],
            "acquisitions": [{"sequencer": "seq", "acquisition_index": 0, "bin_index": 0, "i": 1, "q": -1, "thresholded": 0}],
        },
        "routes": [{"source": "seq", "target": "seq", "id": 255, "latency_ns": 0}],
        "trigger_routes": [{"source": "seq", "target": "seq", "address": 15, "latency_ns": 0, "thresholded": 0}],
    }
    original = deepcopy(config)
    normalized = validate_simulation(config, ["seq"])
    assert config == original
    assert normalized["inputs"] == original["inputs"]
    assert normalized["trigger_routes"][0]["thresholded"] == 0


def run(source: str | dict[str, str], *, sequences=None, **config):
    documents = source if isinstance(source, dict) else {"seq": source}
    config = {"startup_delay_ns": 200, **config}  # Keep the clock used by the boundary inputs.
    return simulate_programs({name: parse_q1asm(text, file=name) for name, text in documents.items()}, simulation=config, sequences=sequences)


def events(state, kind):
    return [event for event in state.events if event.kind == kind]


def status(state):
    return state.metadata["execution"]["status"]


@pytest.mark.parametrize(("startup", "expected"), [(3, "error"), (4, "completed")])
def test_enqueue_at_exact_rt_start_precedes_dequeue(startup, expected) -> None:
    state, = run("wait 4\nstop", startup_delay_ns=startup)
    assert status(state) == expected
    if expected == "error":
        assert events(state, "underflow_warning")[0].t0 == Concrete(3)
        assert state.diagnostics[-1].category == "rt_queue_underflow"
    else:
        enqueue, = events(state, "rt_queue_enqueue")
        dequeue, = events(state, "rt_queue_dequeue")
        assert enqueue.t0 == dequeue.t0 == Concrete(4)
        assert state.events.index(enqueue) < state.events.index(dequeue)
        assert state.rt_time_ns == Concrete(8)


@pytest.mark.parametrize("capacity", [1, 2, 32])
def test_queue_reservations_preserve_capacity_fifo_and_exactly_once_delivery(capacity) -> None:
    state, = run("wait 4\n" * 40 + "stop", queue_capacity=capacity)
    assert status(state) == "completed"
    enqueues = events(state, "rt_queue_enqueue")
    dequeues = events(state, "rt_queue_dequeue")
    packet_ids = [packet.id for packet in state.rt_packets]
    assert [event.meta["rt_packet_id"] for event in enqueues] == packet_ids
    assert [event.meta["rt_packet_id"] for event in dequeues] == packet_ids
    assert len(set(packet_ids)) == 40
    assert all(0 <= event.meta["depth"] <= capacity for event in enqueues + dequeues)
    assert [event.t0.value for event in dequeues] == list(range(200, 360, 4))
    assert len({event.id for event in state.events}) == len(state.events)
    issue_ids = {event.id for event in events(state, "q1_issue")}
    assert all(write.event_id in issue_ids for write in state.register_history)


def test_feedback_at_rt_deadline_cannot_replace_a_missing_rt_packet() -> None:
    state, = run("wait 4\nfb_pop_data 5,R0\nwait 4\nstop", startup_delay_ns=20,
        inputs={"feedback": [{"target": "seq", "time_ns": 24, "id": 5, "value": 7}]})
    assert status(state) == "error"
    receive, = events(state, "feedback_receive")
    underflow, = events(state, "underflow_warning")
    assert receive.t0 == underflow.t0 == Concrete(24)
    assert state.events.index(receive) < state.events.index(underflow)
    assert len(state.rt_packets) == 1
    assert state.register_history == []


@pytest.mark.parametrize(("arrival", "expected", "end"), [(207, "blocked", None), (208, "completed", 212), (209, "completed", 213)])
def test_trigger_one_ns_before_at_and_after_wait_start(arrival, expected, end) -> None:
    state, = run("wait 8\nwait_trigger 1,4\nstop", inputs={"triggers": [
        {"target": "seq", "time_ns": arrival, "address": 1},
    ]})
    assert status(state) == expected
    if end is not None:
        assert state.rt_time_ns == Concrete(end)
        packet = state.rt_packets[1]
        assert packet.meta["input_event_ids"] == [events(state, "trigger_receive")[0].id]
    else:
        assert events(state, "trigger_wait")[0].meta["pending"] is True


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("instruction,kind,reason,end", [
    ("wait_trigger 3,4", "trigger_wait", "No scheduled input can resume trigger_wait.", 120),
    ("wait_sync 4", "sync_wait", "No matching wait_sync barrier for: peer.", 150),
])
def test_quiescent_rt_wait_diagnostic_resolves_to_the_pending_wait(instruction, kind, reason, end, prepared):
    sources = {"seq": f"wait 20\n{instruction}\nwait 8\nstop"}
    if kind == "sync_wait":
        sources["peer"] = "wait 50\nstop"
    programs = {name: parse_q1asm(code, file=f"{name}.q1asm") for name, code in sources.items()}
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    states = simulate_programs(programs, simulation={"startup_delay_ns": 100}, prepared_programs=bindings)
    state = next(item for item in states if item.sequencer_id == "seq")
    ir = timeline_ir_from_states(states)
    assert ir["execution"]["seq"]["status"] == "blocked"
    assert ir["execution"]["seq"]["reason"] == reason and ir["execution"]["seq"]["pc"] == 1
    diagnostic, = [item for item in ir["diagnostics"] if item["category"] == "analysis_incomplete"]
    assert diagnostic["source"]["line"] == 2 and diagnostic["source"]["raw"] == instruction
    assert diagnostic["source"] == ir["execution"]["seq"]["source"]
    if prepared:
        assert not events(state, kind)
        assert not diagnostic["related_events"]
    else:
        wait, = events(state, kind)
        assert (wait.t0.value, wait.t1.value, wait.duration.value) == (120, end, end - 120)
        assert wait.meta["pending"] is True and wait.meta["rt_packet_id"] == state.rt_packets[1].id
        issue, = [event for event in events(state, "q1_issue") if event.source.line == 2]
        assert wait.meta["q1_issue_event_id"] == issue.id
        assert "applied_state" not in wait.meta
        assert diagnostic["related_events"] == [wait.id]
        assert diagnostic["source"] == ir["source_map"]["by_event_id"][wait.id]


@pytest.mark.parametrize("rt_wait", [False, True])
def test_quiescent_q1_feedback_stall_preserves_its_own_source(rt_wait):
    prefix = "wait_trigger 2,4\n" if rt_wait else ""
    program = parse_q1asm(f"{prefix}fb_pull_data R0,R1\nstop", file="q1.q1asm")
    state, = simulate_programs({"seq": program}, simulation={"queue_capacity": 1})
    diagnostic, = state.diagnostics
    assert state.metadata["execution"]["reason"] == "No scheduled input can resume feedback_input."
    assert diagnostic.source.line == (2 if rt_wait else 1) and diagnostic.source.raw == "fb_pull_data R0,R1"
    assert not diagnostic.related_events
    stall, = events(state, "q1_stall")
    assert stall.source == diagnostic.source and stall.meta["pending"] is True
    if rt_wait:
        pending, = events(state, "trigger_wait")
        assert pending.source.line == 1 and pending.meta["pending"] is True


@pytest.mark.parametrize("prepared", [False, True])
def test_full_q1_queue_reports_the_pending_rt_wait_as_its_blocker(prepared):
    program = parse_q1asm("wait_trigger 2,4\nwait 4\nwait 4\nwait 4\nstop", file="queue.q1asm")
    bindings = ({"seq": prepare_def_program(program, params={}, strict_q1asm=False, varying=set())}
                if prepared else None)
    state, = simulate_programs({"seq": program}, simulation={"queue_capacity": 1}, prepared_programs=bindings)
    ir = timeline_ir_from_states([state])
    execution = ir["execution"]["seq"]
    assert execution["status"] == "blocked" and execution["pc"] == 0
    assert execution["reason"] == "No scheduled input can resume trigger_wait."
    diagnostic, = [item for item in ir["diagnostics"] if item["category"] == "analysis_incomplete"]
    assert diagnostic["source"] == execution["source"]
    assert diagnostic["source"]["line"] == 1 and diagnostic["source"]["raw"] == "wait_trigger 2,4"
    if prepared:
        assert not diagnostic["related_events"]
        assert not events(state, "trigger_wait") and not events(state, "q1_stall")
    else:
        pending, = events(state, "trigger_wait")
        stall, = events(state, "q1_stall")
        assert diagnostic["related_events"] == [pending.id]
        assert ir["source_map"]["by_event_id"][pending.id] == diagnostic["source"]
        assert pending.meta["pending"] is True
        assert stall.source.line == 3 and stall.source.raw == "wait 4"
        assert stall.meta == {"reason": "queue_full", "pending": True}


def test_trigger_resumes_both_cores_after_a_full_q1_queue_stalls():
    program = parse_q1asm("wait_trigger 2,4\nwait 4\nwait 4\nwait 4\nstop", file="queue.q1asm")
    state, = simulate_programs({"seq": program}, simulation={"queue_capacity": 1, "inputs": {
        "triggers": [{"target": "seq", "time_ns": 100, "address": 2}],
    }})
    assert status(state) == "completed"
    trigger, = events(state, "wait_trigger")
    stall, = events(state, "q1_stall")
    stop, = events(state, "stop")
    assert trigger.t1 == stall.t1 == Concrete(104)
    assert stall.source.line == 3 and stall.meta["reason"] == "queue_full"
    assert not stall.meta.get("pending")
    assert stop.source.line == 5 and stop.t0 == Concrete(116)
    assert not state.diagnostics


@pytest.mark.parametrize("prepared", [False, True])
def test_time_limit_reports_the_pending_rt_wait_and_keeps_the_local_limit(prepared):
    programs = {"seq": parse_q1asm("wait 20\nwait_trigger 2,4\nwait 8\nstop", file="wait.q1asm"),
                "peer": parse_q1asm("wait 40\nstop", file="peer.q1asm")}
    options = {"startup_delay_ns": 100, "max_time_ns": 160,
               "inputs": {"triggers": [{"target": "seq", "time_ns": 200, "address": 2}]}}
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    states = simulate_programs(programs, simulation=options, prepared_programs=bindings)
    align_timelines(states)
    ir = timeline_ir_from_states(states)
    execution = ir["execution"]["seq"]
    assert execution["status"] == "step_limit" and execution["pc"] == 1
    assert execution["reason"] == "Reached simulation time limit 160 ns."
    assert execution["source"]["line"] == 2 and execution["source"]["raw"] == "wait_trigger 2,4"
    assert ir["execution"]["peer"]["status"] == "completed"
    diagnostic, = [item for item in ir["diagnostics"] if item["category"] == "simulation_time_limit"]
    assert diagnostic["source"] == execution["source"]
    assert diagnostic["message"] == "Simulation execution step_limit: Reached simulation time limit 160 ns."
    assert diagnostic["details"]["time_limit_local_ns"] == 160
    assert diagnostic["details"]["display_message"] == "Reached simulation time limit."
    if prepared:
        assert not diagnostic["related_events"]
    else:
        wait, = [event for event in ir["events"] if event["kind"] == "trigger_wait"]
        assert diagnostic["related_events"] == [wait["id"]]
        assert wait["meta"]["pending"] is True
        assert (wait["t0"]["value"], wait["t1"]["value"]) == (120, 160)
        assert (wait["meta"]["aligned_t0"], wait["meta"]["aligned_t1"]) == (20, 60)
        stop, = [event for event in ir["events"] if event["kind"] == "stop"]
        assert stop["sequencer_id"] == "peer" and stop["t0"]["value"] == 140
        html = render_html(ir)
        assert 'data-diagnostic-category="simulation_time_limit"' in html
        assert f'data-related-event-id="{wait["id"]}"' in html
        assert "observation cutoff 60 ns" in html
        assert ">Reached simulation time limit.</p>" in html


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize(("operation", "kind", "release", "planned_end", "paths"), [
    ("wait_trigger 2,40", "wait_trigger", 150, 190, 1),
    ("wait 100", "wait", 120, 220, 1),
    ("play 0,1,100", "play", 120, 220, 2),
])
def test_time_limit_caps_only_the_active_rt_command_and_keeps_its_effect(operation, kind, release, planned_end, paths, prepared):
    programs = {"seq": parse_q1asm(f"wait 20\n{operation}\nwait 8\nstop", file="cutoff.q1asm"),
                "peer": parse_q1asm("wait 40\nstop", file="peer.q1asm")}
    options = {"startup_delay_ns": 100, "max_time_ns": 160}
    if kind == "wait_trigger":
        options["inputs"] = {"triggers": [{"target": "seq", "time_ns": 150, "address": 2}]}
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    states = simulate_programs(programs, simulation=options, prepared_programs=bindings)
    state = next(item for item in states if item.sequencer_id == "seq")
    peer = next(item for item in states if item.sequencer_id == "peer")
    assert status(state) == "step_limit" and state.rt_time_ns == Concrete(160)
    assert status(peer) == "completed" and peer.rt_time_ns == Concrete(140)
    first, active, queued = state.rt_packets
    assert first.rt_t1 == Concrete(120) and "observation_cutoff_ns" not in first.meta
    assert queued.rt_t0 is None and queued.rt_t1 is None
    assert active.rt_t0 == Concrete(120) and active.rt_t1 == Concrete(160) and active.duration == Concrete(40)
    assert "observation_cutoff_ns" not in peer.rt_packets[0].meta
    assert active.meta["planned_rt_end_ns"] == planned_end and active.meta["observation_cutoff_ns"] == 160
    assert active.meta["rt_execution_ns"] == release
    assert active.meta["next_dequeue_not_before_ns"] == planned_end
    assert "interrupted_by_fault" not in active.meta
    align_timelines(states)
    ir = timeline_ir_from_states(states)
    diagnostic, = [item for item in ir["diagnostics"] if item["category"] == "simulation_time_limit"]
    assert diagnostic["source"]["line"] == 2 and diagnostic["source"]["raw"] == operation
    assert diagnostic["details"]["time_limit_local_ns"] == 160
    observed = [event for event in ir["events"] if event["kind"] == kind and event["meta"].get("rt_packet_id") == active.id]
    if prepared:
        assert not observed and not diagnostic["related_events"]
    else:
        assert len(observed) == paths
        assert diagnostic["related_events"] == [observed[-1]["id"]]
        for event in observed:
            assert event["meta"]["aligned_t1"] == 60 and event["t1"]["value"] == 160
            assert event["meta"]["planned_rt_end_ns"] == planned_end and event["meta"]["observation_cutoff_ns"] == 160
            assert not event["meta"].get("pending") and "interrupted_by_fault" not in event["meta"]
        if kind == "wait_trigger":
            arrival, = [event for event in ir["events"] if event["kind"] == "trigger_receive"]
            assert arrival["meta"]["aligned_t0"] == 50 and active.meta["input_event_ids"] == [arrival["id"]]
        if kind == "play":
            assert [(event["meta"]["path"], event["meta"]["waveform_index"]) for event in observed] == [(0, 0), (1, 1)]


def test_q1_instruction_limit_is_distinct_from_the_simulation_time_limit():
    program = parse_q1asm("wait 20\nwait_trigger 2,4\nwait 8\nstop", file="steps.q1asm")
    state, = simulate_programs({"seq": program}, max_steps=2, simulation={
        "startup_delay_ns": 100, "max_time_ns": 160,
        "inputs": {"triggers": [{"target": "seq", "time_ns": 200, "address": 2}]},
    })
    assert status(state) == "step_limit"
    assert state.metadata["execution"]["reason"] == "Reached the 2 instruction limit."
    diagnostic, = state.diagnostics
    assert diagnostic.category == "analysis_incomplete" and diagnostic.source.line == 3
    assert "display_message" not in diagnostic.details and not diagnostic.related_events
    assert all("observation_cutoff_ns" not in event.meta for event in events(state, "q1_issue"))


def test_time_limit_before_first_rt_instruction_keeps_display_metadata_without_evidence():
    state, = run("wait 100\nstop", startup_delay_ns=100, max_time_ns=10)
    packet, = state.rt_packets
    diagnostic, = state.diagnostics
    assert status(state) == "step_limit" and state.metadata["execution"]["reason"] == "Reached simulation time limit 10 ns."
    assert packet.rt_t0 is None and not events(state, "rt_queue_dequeue")
    assert diagnostic.category == "simulation_time_limit" and not diagnostic.related_events
    assert diagnostic.details["display_message"] == "Reached simulation time limit."
    assert diagnostic.details["time_limit_local_ns"] == 10


def test_time_limit_before_first_q1_issue_keeps_the_stalled_instruction_source():
    state, = run("fb_pop_data 16,R0\nstop", startup_delay_ns=100, max_time_ns=50, inputs={"feedback": [
        {"target": "seq", "time_ns": 200, "id": 16, "value": 42},
    ]})
    diagnostic, = state.diagnostics
    assert status(state) == "step_limit" and state.metadata["execution"]["steps"] == 0
    assert state.metadata["execution"]["reason"] == "Reached simulation time limit 50 ns."
    assert diagnostic.category == "simulation_time_limit" and diagnostic.source.raw == "fb_pop_data 16,R0"
    assert diagnostic.details["display_message"] == "Reached simulation time limit."
    assert diagnostic.details["time_limit_local_ns"] == 50 and not diagnostic.related_events
    assert not events(state, "q1_issue") and not events(state, "feedback_receive")


@pytest.mark.parametrize("prepared", [False, True])
def test_time_limit_caps_q1_issue_and_preserves_pending_write_readiness(prepared):
    program = parse_q1asm("move 3,R0\nnop\nadd R0,1,R1\nstop", file="alu.q1asm")
    bindings = ({"seq": prepare_def_program(program, params={}, strict_q1asm=False, varying=set())}
                if prepared else None)
    state, = simulate_programs({"seq": program}, simulation={"max_time_ns": 12}, prepared_programs=bindings)
    execution = state.metadata["execution"]
    assert execution["status"] == "step_limit" and execution["steps"] == 3
    assert execution["pc"] == 2 and execution["source"]["line"] == 3
    assert execution["reason"] == "Reached simulation time limit 12 ns."
    assert state.q1_time_ns == Concrete(12)
    assert state.registers == {"R0": Concrete(3)}
    pending, = state.pending_register_writes
    assert pending.register == "R1" and pending.meta["scheduled_ready_ns"] == 24
    diagnostic, = state.diagnostics
    assert diagnostic.category == "simulation_time_limit" and diagnostic.source.raw == "add R0,1,R1"
    assert diagnostic.source.line == 3 and diagnostic.details["time_limit_local_ns"] == 12
    if prepared:
        assert not events(state, "q1_issue") and not diagnostic.related_events
        assert not state.register_history
    else:
        issue = events(state, "q1_issue")[-1]
        assert (issue.t0, issue.t1, issue.duration) == (Concrete(8), Concrete(12), Concrete(4))
        assert issue.meta["planned_q1_end_ns"] == 20 and issue.meta["observation_cutoff_ns"] == 12
        assert issue.meta["pending_register_ready_ns"] == {"R1": 24}
        assert diagnostic.related_events == [issue.id] and issue.source == diagnostic.source
        assert [(write.register, write.time, write.value) for write in state.register_history] == [
            ("R0", Concrete(8), Concrete(3)),
        ]


def test_time_limit_clips_both_cores_but_keeps_the_active_rt_command_as_evidence():
    state, = run("wait 100\nmove 3,R0\nnop\nadd R0,1,R1\nstop", startup_delay_ns=4, max_time_ns=16)
    rt, = events(state, "wait")
    q1 = events(state, "q1_issue")[-1]
    assert rt.t1 == q1.t1 == Concrete(16)
    assert rt.meta["planned_rt_end_ns"] == 104
    assert q1.t0 == Concrete(12) and q1.meta["planned_q1_end_ns"] == 24
    assert q1.meta["pending_register_ready_ns"] == {"R1": 28}
    diagnostic, = state.diagnostics
    assert diagnostic.source.line == 1 and diagnostic.related_events == [rt.id]
    assert state.metadata["execution"]["source"]["raw"] == "wait 100"


@pytest.mark.parametrize("prepared", [False, True])
def test_underflow_clips_q1_issue_without_publishing_a_pending_register_write(prepared):
    programs = {
        "seq": parse_q1asm("move 3,R0\nnop\nwait 4\nadd R0,1,R1\nwait 4\nstop", file="underflow.q1asm"),
        "peer": parse_q1asm("wait 20\nstop", file="peer.q1asm"),
    }
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    states = simulate_programs(programs, simulation={"startup_delay_ns": 12}, prepared_programs=bindings)
    state = next(item for item in states if item.sequencer_id == "seq")
    peer = next(item for item in states if item.sequencer_id == "peer")
    assert status(state) == "error" and state.q1_time_ns == Concrete(16)
    assert state.metadata["execution"]["reason"] == "RT queue underflow at 16 ns."
    assert status(peer) == "completed" and peer.rt_time_ns == Concrete(32)
    assert state.registers == {"R0": Concrete(3)}
    pending, = state.pending_register_writes
    assert pending.register == "R1" and pending.meta["scheduled_ready_ns"] == 28
    diagnostic, = state.diagnostics
    assert diagnostic.category == "rt_queue_underflow" and diagnostic.source.line == 5
    if prepared:
        assert not diagnostic.related_events
        assert all(event.meta.get("op") != "add" for event in events(state, "q1_issue"))
    else:
        marker, = events(state, "underflow_warning")
        issue = events(state, "q1_issue")[-1]
        assert diagnostic.related_events == [marker.id] and marker.t0 == Concrete(16)
        assert (issue.t0, issue.t1, issue.duration) == (Concrete(12), Concrete(16), Concrete(4))
        assert issue.meta["planned_q1_end_ns"] == 24 and issue.meta["observation_cutoff_ns"] == 16
        assert issue.meta["pending_register_ready_ns"] == {"R1": 28}
        assert [write.register for write in state.register_history] == ["R0"]


def test_underflow_on_the_planned_q1_end_keeps_the_finished_issue():
    state, = run("move 3,R0\nnop\nwait 4\nnop\nwait 4\nstop", startup_delay_ns=12)
    issue = events(state, "q1_issue")[-1]
    marker, = events(state, "underflow_warning")
    assert issue.source.line == 4 and issue.t1 == marker.t0 == Concrete(16)
    assert "planned_q1_end_ns" not in issue.meta and "observation_cutoff_ns" not in issue.meta


def test_linq_overflow_clips_q1_issue_but_keeps_rejected_input_as_evidence():
    state, = run("move 3,R0\nnop\nadd R0,1,R1\nstop", feedback_queue_capacity=1, inputs={"feedback": [
        {"target": "seq", "time_ns": 12, "id": 1, "value": 8}, {"target": "seq", "time_ns": 12, "id": 2, "value": 9},
    ]})
    issue = events(state, "q1_issue")[-1]
    rejected, = events(state, "feedback_rejected")
    diagnostic, = state.diagnostics
    assert issue.t0 == Concrete(8) and issue.t1 == rejected.t0 == Concrete(12)
    assert issue.meta["planned_q1_end_ns"] == 20 and issue.meta["pending_register_ready_ns"] == {"R1": 24}
    assert state.registers == {"R0": Concrete(3)}
    assert diagnostic.category == "feedback_queue_overflow" and diagnostic.source == rejected.source
    assert diagnostic.related_events == [rejected.id]


def test_runtime_error_preserves_only_the_observed_same_field_latch():
    state, = run("wait 100\nset_freq 1000000\nset_freq 4000000\nstop", startup_delay_ns=4, feedback_queue_capacity=1,
                 inputs={"feedback": [{"target": "seq", "time_ns": 10, "id": key, "value": key} for key in (1, 2)]})
    first, = events(state, "latched_state_pending")
    setter = events(state, "q1_issue")[-1]
    assert first.t0 == Concrete(8) and first.meta["value"] == 1000000
    assert state.latched_state.frequency == Concrete(1000000)
    assert state.latched_state.pending_event_ids["frequency"] == first.id
    assert setter.t0 == Concrete(8) and setter.t1 == Concrete(10)
    assert setter.meta["planned_latch_changes"] == [{"field": "frequency", "value": 4000000, "ready_local_ns": 12}]
    assert len({event.id for event in state.events}) == len(state.events)


@pytest.mark.parametrize("command", ["wait 100", "play 0,1,100"])
def test_linq_overflow_clips_active_rt_packet_and_keeps_rejection_primary(command):
    states = run({"seq": f"{command}\nstop", "peer": "wait 40\nstop"}, startup_delay_ns=100,
                 feedback_queue_capacity=1, inputs={"feedback": [
                     {"target": "seq", "time_ns": 120, "id": key, "value": key} for key in (1, 2)
                 ]})
    state, peer = states
    align_timelines(states)
    packet, = state.rt_packets
    accepted, = events(state, "feedback_receive")
    rejected, = events(state, "feedback_rejected")
    diagnostic, = state.diagnostics
    assert status(state) == "error" and state.rt_time_ns == rejected.t0 == Concrete(120)
    assert accepted.meta["id"] == 1 and rejected.meta["id"] == 2
    assert diagnostic.category == "feedback_queue_overflow" and diagnostic.source == rejected.source
    assert diagnostic.related_events == [rejected.id]
    assert (packet.rt_t0, packet.rt_t1, packet.duration) == (Concrete(100), Concrete(120), Concrete(20))
    assert packet.meta["planned_rt_end_ns"] == 200 and packet.meta["observation_cutoff_ns"] == 120
    assert status(peer) == "completed" and peer.rt_time_ns == Concrete(140)
    assert "observation_cutoff_ns" not in peer.rt_packets[0].meta
    spans = events(state, packet.op)
    assert [span.lane for span in spans] == (["rt.wait"] if packet.op == "wait" else ["rt.path0", "rt.path1"])
    for span in spans:
        assert span.meta["rt_packet_id"] == packet.id
        assert span.t1 == rejected.t0 and span.meta["aligned_t1"] == rejected.meta["aligned_t0"] == 20
        assert span.meta["planned_rt_end_ns"] == 200 and span.meta["observation_cutoff_ns"] == 120
    if packet.op == "play":
        assert [span.meta["waveform_index"] for span in spans] == [0, 1]


def test_linq_overflow_at_rt_packet_end_preserves_completed_packet_and_queued_successor():
    state, = run("wait 20\nwait 4\nstop", startup_delay_ns=100, feedback_queue_capacity=1, inputs={"feedback": [
        {"target": "seq", "time_ns": 120, "id": key, "value": key} for key in (1, 2)
    ]})
    completed, queued = state.rt_packets
    wait, = events(state, "wait")
    rejected, = events(state, "feedback_rejected")
    assert completed.rt_t1 == wait.t1 == rejected.t0 == Concrete(120)
    assert "observation_cutoff_ns" not in completed.meta and "observation_cutoff_ns" not in wait.meta
    assert queued.rt_t0 is None and queued.rt_t1 is None


@pytest.mark.parametrize("prepared", [False, True])
def test_linq_overflow_closes_both_cores_and_hides_unobserved_latch(prepared):
    program = parse_q1asm("wait 100\nset_freq 4000000\nstop", file="latch.q1asm")
    bindings = ({"seq": prepare_def_program(program, params={}, strict_q1asm=False, varying=set())}
                if prepared else None)
    state, = simulate_programs({"seq": program}, simulation={
        "startup_delay_ns": 4, "feedback_queue_capacity": 1, "inputs": {"feedback": [
            {"target": "seq", "time_ns": 6, "id": key, "value": key} for key in (1, 2)
        ]},
    }, prepared_programs=bindings)
    packet, = state.rt_packets
    rejected, = events(state, "feedback_rejected")
    assert state.q1_time_ns == state.rt_time_ns == packet.rt_t1 == rejected.t0 == Concrete(6)
    assert packet.meta["planned_rt_end_ns"] == 104
    assert state.latched_state.frequency is None and not events(state, "latched_state_pending")
    assert state.diagnostics[-1].related_events == [rejected.id]
    if not prepared:
        setter = events(state, "q1_issue")[-1]
        assert setter.t1 == Concrete(6) and setter.meta["planned_q1_end_ns"] == 8
        assert setter.meta["planned_latch_changes"] == [{"field": "frequency", "value": 4000000, "ready_local_ns": 8}]


@pytest.mark.parametrize("prepared", [False, True])
def test_simulation_exports_each_terminal_edge_separately_from_project_horizon(prepared):
    programs = {
        "seq": parse_q1asm("wait_trigger 2,4\nstop", file="receiver.q1asm"),
        "peer": parse_q1asm("wait 100\nstop", file="peer.q1asm"),
    }
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    states = simulate_programs(programs, prepared_programs=bindings, simulation={
        "startup_delay_ns": 100, "feedback_queue_capacity": 1,
        "inputs": {"feedback": [{"target": "seq", "time_ns": 120, "id": key, "value": key} for key in (1, 2)]},
    })
    align_timelines(states)
    ir = timeline_ir_from_states(states)
    receiver, peer = ir["execution"]["seq"], ir["execution"]["peer"]
    assert receiver["reason"] == "Feedback queue overflow at 120 ns."
    assert receiver["terminal"] == {"local_time_ns": 120, "category": "feedback_queue_overflow",
                                    "display_reason": "LINQ feedback queue overflow (capacity 1)."}
    assert receiver["stop_code"] == 0 and peer["status"] == "completed" and "terminal" not in peer
    assert ir["simulation"]["seq"]["end_time_ns"] == ir["simulation"]["peer"]["end_time_ns"] == 200
    if not prepared:
        wait, = events(states[0], "trigger_wait")
        assert wait.meta["pending"] and wait.meta["aligned_t1"] == 20 and wait.t1 == Concrete(120)


def test_simulation_exports_terminal_for_q1_only_fault_and_unresolved_stall():
    fault, = run("illegal", startup_delay_ns=100)
    assert fault.metadata["execution"]["terminal"] == {
        "local_time_ns": 4, "category": "illegal_instruction", "display_reason": "Illegal Q1ASM instruction executed.",
    }
    program = parse_q1asm("fb_pull_data R0,R1\nstop", file="stalled.q1asm")
    stalled, = simulate_programs({"seq": program})
    assert status(stalled) == "blocked" and not events(stalled, "q1_issue")
    assert stalled.metadata["execution"]["terminal"] == {
        "local_time_ns": 0, "category": "analysis_incomplete", "display_reason": "No scheduled input can resume feedback_input.",
    }


@pytest.mark.parametrize("prepared", [False, True])
def test_q1_register_readiness_failure_clips_active_rt_and_preserves_source(prepared):
    programs = {
        "seq": parse_q1asm("wait 100\nmove 3,R0\nadd R0,1,R1\nstop", file="validation.q1asm"),
        "peer": parse_q1asm("wait 40\nstop", file="peer.q1asm"),
    }
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    state, peer = simulate_programs(programs, simulation={"startup_delay_ns": 4}, prepared_programs=bindings)
    packet, = state.rt_packets
    assert status(state) == "error" and state.q1_time_ns == state.rt_time_ns == Concrete(8)
    assert (packet.rt_t0, packet.rt_t1, packet.duration) == (Concrete(4), Concrete(8), Concrete(4))
    assert packet.meta["planned_rt_end_ns"] == 104 and packet.meta["observation_cutoff_ns"] == 8
    assert "interrupted_by_fault" not in packet.meta
    assert state.executed_pcs == {0, 1} and "R1" not in state.registers
    assert not any(event.source.line == 3 for event in events(state, "q1_issue"))
    assert not any(write.register == "R1" for write in state.pending_register_writes)
    readiness, terminal = state.diagnostics
    assert readiness.category == "register_not_ready" and terminal.category == "invalid_argument_value"
    assert readiness.source == terminal.source and terminal.source.raw == "add R0,1,R1"
    assert state.metadata["execution"]["source"]["raw"] == terminal.source.raw
    if prepared:
        assert not terminal.related_events
    else:
        wait, = events(state, "wait")
        assert wait.t1 == packet.rt_t1 and wait.meta["observation_cutoff_ns"] == 8
        assert terminal.related_events == [wait.id]
    assert status(peer) == "completed" and peer.rt_packets[0].rt_t1 == Concrete(44)


@pytest.mark.parametrize(("instruction", "reason"), [
    ("sw_req 0", "sw_req has no supported concrete execution semantics."),
    ("set_cond 1,1,6,4", "Conditional operators 6 and 7 have no supported semantics."),
    ("fb_pop_data 257,R0", "fb_pop_data selector 257 exceeds the documented 8-bit LINQ range."),
])
def test_q1_semantic_refusal_cuts_rt_without_issuing_the_unmodeled_op(instruction, reason):
    state, = run(f"wait 100\n{instruction}\nstop", startup_delay_ns=4)
    packet, = state.rt_packets
    wait, = events(state, "wait")
    issue, = events(state, "q1_issue")
    diagnostic, = state.diagnostics
    assert status(state) == "blocked" and state.q1_time_ns == state.rt_time_ns == Concrete(4)
    assert issue.source.line == 1 and state.executed_pcs == {0}
    assert state.metadata["execution"]["reason"].startswith(reason)
    assert diagnostic.source.raw == instruction and diagnostic.related_events == [wait.id]
    assert wait.t0 == wait.t1 == packet.rt_t1 == Concrete(4)
    assert wait.meta["planned_rt_end_ns"] == 104 and wait.meta["observation_cutoff_ns"] == 4


def test_q1_validation_preserves_rt_packet_completed_at_the_same_edge():
    state, = run("wait 8\nwait_trigger 2,4\nmove 3,R0\nadd R0,1,R1\nstop", startup_delay_ns=4)
    completed, armed = state.rt_packets
    wait, = events(state, "wait")
    pending, = events(state, "trigger_wait")
    assert status(state) == "error" and state.metadata["execution"]["source"]["line"] == 4
    assert completed.rt_t1 == wait.t1 == pending.t0 == pending.t1 == state.q1_time_ns == Concrete(12)
    assert "observation_cutoff_ns" not in completed.meta and "observation_cutoff_ns" not in wait.meta
    assert pending.meta["pending"] and armed.rt_t1 is None
    assert not state.diagnostics[-1].related_events


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize(("source", "max_steps", "expected_status", "category", "line", "reason"), [
    ("wait 100", 10000, "error", "invalid_argument_value", 1, "Execution reached a PC without an instruction or stop."),
    ("wait 100\nnop\nstop", 1, "step_limit", "analysis_incomplete", 2, "Reached the 1 instruction limit."),
])
def test_q1_program_boundary_clips_active_rt(source, max_steps, expected_status, category, line, reason, prepared):
    programs = {"seq": parse_q1asm(source, file="boundary.q1asm"), "peer": parse_q1asm("stop", file="peer.q1asm")}
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    state, peer = simulate_programs(programs, simulation={"startup_delay_ns": 4}, max_steps=max_steps,
                                    prepared_programs=bindings)
    packet, = state.rt_packets
    assert state.q1_time_ns == state.rt_time_ns == packet.rt_t0 == packet.rt_t1 == Concrete(4)
    assert packet.duration == Concrete(0) and state.executed_pcs == {0}
    assert packet.meta["planned_rt_end_ns"] == 104 and packet.meta["observation_cutoff_ns"] == 4
    assert "interrupted_by_fault" not in packet.meta
    assert status(state) == expected_status and state.metadata["execution"]["reason"] == reason
    diagnostic = state.diagnostics[-1]
    assert diagnostic.category == category and diagnostic.source.line == line
    assert diagnostic.source.file == "boundary.q1asm"
    assert any(item.category == "missing_stop" for item in state.diagnostics) == (expected_status == "error")
    if prepared:
        assert not events(state, "wait") and not diagnostic.related_events
    else:
        wait, = events(state, "wait")
        assert wait.t0 == wait.t1 == Concrete(4) and diagnostic.related_events == [wait.id]
        assert wait.meta["planned_rt_end_ns"] == 104 and wait.meta["observation_cutoff_ns"] == 4
    assert status(peer) == "completed" and peer.q1_time_ns == Concrete(4)
    assert peer.rt_time_ns == Concrete(0) and not peer.rt_packets
    assert not events(peer, "rt_queue_dequeue") and not events(peer, "stop")


def test_q1_instruction_limit_keeps_a_packet_that_completed_at_the_boundary():
    program = parse_q1asm("wait 4\nwait_trigger 2,4\nnop\nstop", file="boundary.q1asm")
    state, = simulate_programs({"seq": program}, simulation={"startup_delay_ns": 4}, max_steps=2)
    completed, armed = state.rt_packets
    wait, = events(state, "wait")
    pending, = events(state, "trigger_wait")
    assert status(state) == "step_limit" and state.diagnostics[-1].source.raw == "nop"
    assert state.q1_time_ns == completed.rt_t1 == wait.t1 == pending.t0 == pending.t1 == Concrete(8)
    assert "observation_cutoff_ns" not in completed.meta and "observation_cutoff_ns" not in wait.meta
    assert armed.rt_t1 is None and pending.meta["pending"]
    assert not state.diagnostics[-1].related_events


def test_q1_exhaustion_retains_latched_rt_effect_observed_at_the_boundary():
    state, = run("set_freq 4000000\nupd_param 100", startup_delay_ns=8)
    update, = events(state, "upd_param")
    packet, = state.rt_packets
    assert status(state) == "error" and update.t0 == update.t1 == packet.rt_t1 == Concrete(8)
    assert update.meta["applied_state"]["frequency"] == packet.meta["applied_state"]["frequency"] == 4000000
    assert update.meta["planned_rt_end_ns"] == 108 and update.meta["observation_cutoff_ns"] == 8


@pytest.mark.parametrize(("source", "ends"), [("wait 100\nwait 4\nstop", [104, 108]), ("wait 100\nadd R0,R1", [104])])
def test_q1_stop_and_malformed_source_recovery_still_drain_rt(source, ends):
    state, = run(source, startup_delay_ns=4)
    assert status(state) == "completed" and state.rt_time_ns == Concrete(ends[-1])
    assert [packet.rt_t1.value for packet in state.rt_packets] == ends
    assert all("observation_cutoff_ns" not in packet.meta for packet in state.rt_packets)
    if "add" in source:
        assert state.diagnostics[0].details["source_line_skipped"] is True


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize(("command", "expected_status", "category", "reason"), [
    ("fb_com_cfg 1,0,4,100", "blocked", "analysis_incomplete", "requires a reference encoding vector"),
    ("play 0,1,100", "error", "invalid_argument_value", "Missing waveform data for seq play"),
])
def test_rt_dispatch_refusal_closes_both_cores_and_preserves_attempt(command, expected_status, category, reason, prepared):
    source = f"wait 16\n{command}\nmove 3,R0\nnop\nadd R0,1,R1\nstop"
    programs = {"seq": parse_q1asm(source, file="dispatch.q1asm"), "peer": parse_q1asm("wait 40\nstop", file="peer.q1asm")}
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    state, peer = simulate_programs(programs, simulation={"startup_delay_ns": 4, "physical": {"sequencers": {"seq": {}}}},
                                    prepared_programs=bindings)
    completed, refused = state.rt_packets
    diagnostic, = state.diagnostics
    assert status(state) == expected_status and state.q1_time_ns == state.rt_time_ns == Concrete(20)
    assert diagnostic.category == category and diagnostic.source.raw == command and reason in diagnostic.message
    assert completed.rt_t1 == refused.rt_t0 == refused.rt_t1 == Concrete(20)
    assert refused.duration == Concrete(0) and "observation_cutoff_ns" not in completed.meta
    assert refused.meta["planned_rt_end_ns"] == 120 and refused.meta["observation_cutoff_ns"] == 20
    assert "interrupted_by_fault" not in refused.meta
    assert state.registers == {"R0": Concrete(3)} and not events(state, "feedback_send")
    pending, = state.pending_register_writes
    assert pending.register == "R1" and pending.meta["scheduled_ready_ns"] == 32
    if prepared:
        assert not diagnostic.related_events and not events(state, refused.op)
    else:
        spans = events(state, refused.op)
        expected_lanes = ["rt.path0", "rt.path1"] if refused.op == "play" else ["rt.fb_com_cfg"]
        assert [span.lane for span in spans] == expected_lanes
        assert all(span.t0 == span.t1 == Concrete(20) and span.meta["planned_rt_end_ns"] == 120 for span in spans)
        assert diagnostic.related_events == [spans[-1].id]
        add = events(state, "q1_issue")[-1]
        assert add.source.line == 5 and (add.t0, add.t1) == (Concrete(16), Concrete(20))
        assert add.meta["planned_q1_end_ns"] == 28 and add.meta["pending_register_ready_ns"] == {"R1": 32}
        assert len(events(state, "rt_queue_dequeue")) == 2
    assert status(peer) == "completed" and peer.rt_time_ns == Concrete(44)


def test_rt_dispatch_refusal_retains_future_q1_latch_only_as_planned():
    state, = run("wait 14\nfb_com_cfg 1,0,4,100\nnop\nnop\nset_freq 4000000\nstop", startup_delay_ns=4)
    setter = events(state, "q1_issue")[-1]
    assert status(state) == "blocked" and state.latched_state.frequency is None
    assert (setter.t0, setter.t1) == (Concrete(16), Concrete(18))
    assert setter.meta["planned_latch_changes"] == [{"field": "frequency", "value": 4000000, "ready_local_ns": 20}]
    assert not events(state, "latched_state_pending")


def test_rt_dispatch_cutoff_does_not_change_skipped_or_unadmitted_commands():
    source = "set_cond 1,1,0,4\nfb_com_cfg 1,0,4,100\nset_cond 0,0,0,0\nwait 8\nstop"
    skipped, = run(source, startup_delay_ns=20)
    skip, = events(skipped, "conditional_skip")
    assert status(skipped) == "completed" and (skip.t0, skip.t1) == (Concrete(20), Concrete(24))
    assert "observation_cutoff_ns" not in skip.meta
    unadmitted, = run("wait 4\nfb_cmd 3,1,4\nstop", startup_delay_ns=8)
    completed, refused = unadmitted.rt_packets
    assert status(unadmitted) == "blocked" and completed.rt_t1 == Concrete(12)
    assert refused.rt_t0 is None and refused.rt_t1 is None and not events(unadmitted, "fb_cmd")
    assert not unadmitted.diagnostics[-1].related_events


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize(("command", "hardware", "reason"), [
    ("fb_acq_tb_mock 1,1,1,100", {}, "duration versus count"),
    ("wait_sync 4", {"seq": {"sync_en": False}}, "sync_en disabled"),
])
def test_rt_refusal_before_admission_clips_q1_without_adding_an_rt_span(command, hardware, reason, prepared):
    program = parse_q1asm(f"wait 16\n{command}\nmove 3,R0\nnop\nadd R0,1,R1\nstop", file="refused.q1asm")
    bindings = ({"seq": prepare_def_program(program, params={}, strict_q1asm=False, varying=set())}
                if prepared else None)
    state, = simulate_programs({"seq": program}, simulation={"startup_delay_ns": 4}, hardware_parameters=hardware,
                               prepared_programs=bindings)
    completed, refused = state.rt_packets
    diagnostic, = state.diagnostics
    assert status(state) == "blocked" and state.q1_time_ns == state.rt_time_ns == Concrete(20)
    assert diagnostic.category == "analysis_incomplete" and diagnostic.source.raw == command and reason in diagnostic.message
    assert completed.rt_t1 == Concrete(20) and "observation_cutoff_ns" not in completed.meta
    assert refused.rt_t0 is None and refused.rt_t1 is None and not events(state, refused.op)
    assert "rt_execution_event_id" not in refused.meta and not events(state, "feedback_send")
    assert state.registers == {"R0": Concrete(3)} and not diagnostic.related_events
    pending, = state.pending_register_writes
    assert pending.register == "R1" and pending.meta["scheduled_ready_ns"] == 32
    issue = next(event for event in events(state, "q1_issue") if event.id == refused.meta["q1_issue_event_id"])
    assert issue.meta["rt_packet_id"] == refused.id
    if not prepared:
        dequeued = events(state, "rt_queue_dequeue")[-1]
        add = events(state, "q1_issue")[-1]
        assert dequeued.meta["rt_packet_id"] == refused.id and dequeued.t0 == Concrete(20)
        assert add.source.line == 5 and (add.t0, add.t1) == (Concrete(16), Concrete(20))
        assert add.meta["planned_q1_end_ns"] == 28 and add.meta["pending_register_ready_ns"] == {"R1": 32}


@pytest.mark.parametrize("setup", ["wait_sync 20", "set_cond 1,1,0,20\nfb_acq_tb_mock 1,1,1,100"])
def test_enabled_sync_and_skipped_mock_keep_full_q1_observation(setup):
    source = f"wait 16\n{setup}\nmove 3,R0\nnop\nadd R0,1,R1\nstop"
    state, = run(source, startup_delay_ns=4)
    assert status(state) == "completed" and state.rt_time_ns == Concrete(40)
    assert state.registers["R1"] == Concrete(4)
    add = next(event for event in events(state, "q1_issue") if event.meta["op"] == "add")
    assert add.duration == Concrete(12) and "observation_cutoff_ns" not in add.meta
    packet = state.rt_packets[-1]
    assert (packet.rt_t0, packet.rt_t1) == (Concrete(20), Concrete(40))
    assert (packet.meta.get("conditional_skipped") is True) == ("fb_acq_tb_mock" in setup)


@pytest.mark.parametrize("prepared", [False, True])
def test_time_limit_keeps_future_latch_as_planned_and_outside_the_observed_state(prepared):
    program = parse_q1asm("wait 100\nset_freq 4000000\nstop", file="latch.q1asm")
    bindings = ({"seq": prepare_def_program(program, params={}, strict_q1asm=False, varying=set())}
                if prepared else None)
    state, = simulate_programs({"seq": program}, simulation={"startup_delay_ns": 4, "max_time_ns": 6},
                               prepared_programs=bindings)
    assert status(state) == "step_limit" and state.q1_time_ns == state.rt_time_ns == Concrete(6)
    assert state.latched_state.frequency is None
    assert not state.latched_state.pending_since and not state.latched_state.pending_event_ids
    ir = timeline_ir_from_states([state])
    assert not any(event["kind"] == "latched_state_pending" for event in ir["events"])
    assert all(event["t1"]["value"] <= 6 for event in ir["events"] if event["t1"] is not None)
    diagnostic, = ir["diagnostics"]
    assert diagnostic["source"]["raw"] == "wait 100"
    if prepared:
        assert not diagnostic["related_events"]
        assert all(event.meta.get("op") != "set_freq" for event in events(state, "q1_issue"))
    else:
        wait, = events(state, "wait")
        setter = events(state, "q1_issue")[-1]
        assert diagnostic["related_events"] == [wait.id]
        assert setter.t0 == Concrete(4) and setter.t1 == Concrete(6)
        assert setter.meta["planned_latch_changes"] == [{"field": "frequency", "value": 4000000, "ready_local_ns": 8}]


@pytest.mark.parametrize("committed", [False, True])
def test_future_latch_does_not_replace_an_observed_same_field_setter(committed):
    apply = "upd_param 100\n" if committed else ""
    state, = run(f"set_freq 1000000\n{apply}set_freq 4000000\nstop", startup_delay_ns=8 if committed else 200,
                 max_time_ns=10 if committed else 6)
    first, = events(state, "latched_state_pending")
    assert first.source.line == 1 and first.meta["value"] == 1000000
    assert state.latched_state.frequency == Concrete(1000000)
    setter = events(state, "q1_issue")[-1]
    assert setter.meta["planned_latch_changes"][0]["value"] == 4000000
    if committed:
        applied, = events(state, "upd_param")
        assert applied.meta["applied_state"]["frequency"] == 1000000
        assert applied.meta["committed_setter_event_ids"]["frequency"] == first.id
        assert not state.latched_state.pending_since
    else:
        assert state.latched_state.pending_since["frequency"] == first.source
        assert state.latched_state.pending_event_ids["frequency"] == first.id


def test_latch_reaching_its_ready_edge_exactly_at_the_limit_is_observed():
    state, = run("wait 100\nset_freq 4000000\nstop", startup_delay_ns=4, max_time_ns=8)
    pending, = events(state, "latched_state_pending")
    setter, = [event for event in events(state, "q1_issue") if event.meta["op"] == "set_freq"]
    assert pending.t0 == pending.t1 == setter.t1 == Concrete(8)
    assert pending.meta["q1_issue_event_id"] == setter.id
    assert "planned_latch_changes" not in setter.meta
    assert state.latched_state.frequency == Concrete(4000000)
    assert state.latched_state.pending_event_ids["frequency"] == pending.id


@pytest.mark.parametrize(("operation", "field", "value"), [
    ("reset_netzero", "netzero_reset", True), ("set_time_ref", "time_ref", True),
    ("set_cond 1,1,0,4", "condition", [1, 1, 0, 4]), ("reset_ph", "phase_reset", True),
])
def test_future_control_latches_do_not_enter_observed_pending_state(operation, field, value):
    state, = run(f"wait 100\n{operation}\nstop", startup_delay_ns=4, max_time_ns=6)
    assert not events(state, "latched_state_pending") and not state.pending_phase_reset
    setter = events(state, "q1_issue")[-1]
    assert setter.meta["planned_latch_changes"] == [{"field": field, "value": value, "ready_local_ns": 8}]
    assert "branch_id" not in setter.meta


def test_future_latch_pruning_keeps_ids_unique_when_a_pending_rt_wait_closes():
    state, = run("wait_trigger 2,4\nset_freq 4000000\nstop", startup_delay_ns=4, max_time_ns=6,
                 inputs={"triggers": [{"target": "seq", "time_ns": 100, "address": 2}]})
    ir = timeline_ir_from_states([state])
    assert len({event["id"] for event in ir["events"]}) == len(ir["events"])
    pending, = events(state, "trigger_wait")
    setter = events(state, "q1_issue")[-1]
    assert pending.id != setter.id and pending.t1 == setter.t1 == Concrete(6)
    assert not events(state, "latched_state_pending")
    diagnostic, = ir["diagnostics"]
    assert diagnostic["related_events"] == [pending.id]
    assert pending.meta["q1_issue_event_id"] in ir["source_map"]["by_event_id"]


@pytest.mark.parametrize("prepared", [False, True])
def test_time_limit_reports_the_lowered_slot_of_an_active_q1_branch(prepared):
    program = parse_q1asm("move 1,R0\nnop\ncmp R0,0\njmp 5\nloop R1,@end\nwait 8\nend: stop", file="lowered.q1asm")
    bindings = ({"seq": prepare_def_program(program, params={}, strict_q1asm=False, varying=set())}
                if prepared else None)
    state, = simulate_programs({"seq": program}, simulation={"max_time_ns": 40}, prepared_programs=bindings)
    execution = state.metadata["execution"]
    diagnostic, = state.diagnostics
    assert execution["pc"] == 4 and execution["compiled_pc"] == diagnostic.details["compiled_pc"] == 5
    assert diagnostic.source.line == 5 and diagnostic.source.raw == "loop R1,@end"
    if prepared:
        assert not diagnostic.related_events
    else:
        branch = events(state, "q1_issue")[-1]
        assert branch.meta["op"] == "jnz" and branch.meta["compiled_pc"] == 5
        assert branch.t0 == Concrete(36) and branch.t1 == Concrete(40)
        assert branch.meta["planned_q1_end_ns"] == 52
        assert diagnostic.related_events == [branch.id]


@pytest.mark.parametrize(("source", "reason", "start", "line", "options"), [
    ("move 3,R0\nnop\nadd R0,1,R1\nfb_pull_data R2,R3\nstop", "feedback_input", 20, 4, {"max_time_ns": 20}),
    ("wait 4\nwait 4\nstop", "queue_full", 4, 2, {"max_time_ns": 12, "queue_capacity": 1}),
])
def test_time_limit_does_not_clip_a_completed_q1_issue_while_q1_is_stalled(source, reason, start, line, options):
    state, = run(source, **options)
    issue = events(state, "q1_issue")[-1]
    stall, = events(state, "q1_stall")
    assert issue.t1 == stall.t0 == Concrete(start)
    assert "planned_q1_end_ns" not in issue.meta and "observation_cutoff_ns" not in issue.meta
    assert stall.t1 == Concrete(options["max_time_ns"]) and stall.meta["reason"] == reason
    diagnostic, = state.diagnostics
    assert diagnostic.source == stall.source and diagnostic.source.line == line
    assert not diagnostic.related_events and diagnostic.details["display_message"] == "Reached simulation time limit."
    assert diagnostic.details["time_limit_local_ns"] == options["max_time_ns"]


@pytest.mark.parametrize("limit", [7, 8])
def test_time_limit_does_not_publish_register_before_readable_time(limit) -> None:
    state, = run("move 7,R0\nnop\nstop", max_time_ns=limit)
    assert status(state) == "step_limit"
    assert state.metadata["simulation"]["end_time_ns"] == limit
    assert [(write.time.value, write.value.value) for write in state.register_history] == ([] if limit == 7 else [(8, 7)])
    assert state.registers == ({} if limit == 7 else {"R0": Concrete(7)})
    diagnostic = state.diagnostics[-1]
    assert diagnostic.category == "simulation_time_limit"
    issue = events(state, "q1_issue")[-1]
    assert issue.meta["op"] == ("nop" if limit == 7 else "stop")
    assert issue.t1 == Concrete(limit) and diagnostic.related_events == [issue.id]
    if limit == 8:
        assert issue.t0 == Concrete(8) and issue.duration == Concrete(0)


@pytest.mark.parametrize("limit", [219, 220])
def test_time_limit_does_not_publish_acquisition_or_feedback_before_completion(limit) -> None:
    readout, control = run({
        "readout": "fb_acq_tb_id 5,4\nacquire 0,0,16\nwait 20\nstop",
        "control": "wait 100\nstop",
    }, max_time_ns=limit,
       inputs={"acquisitions": [{"sequencer": "readout", "acquisition_index": 0, "bin_index": 0, "thresholded": 1}]},
       routes=[{"source": "readout", "target": "control", "id": 5, "latency_ns": 0}])
    assert [status(state) for state in (readout, control)] == ["step_limit", "step_limit"]
    expected_times = [] if limit == 219 else [220]
    assert [event.t0.value for event in events(readout, "acquisition_result")] == expected_times
    assert [event.t0.value for event in events(readout, "feedback_send")] == expected_times
    assert [event.t0.value for event in events(control, "feedback_receive")] == expected_times


@pytest.mark.parametrize("limit", [31, 32, 35, 36])
def test_time_limit_does_not_publish_loop_counter_before_branch_completion(limit) -> None:
    state, = run("move 2,R0\nnop\nagain: loop R0,@again\nstop", max_time_ns=limit)
    expected = [(time, value) for time, value in [(8, 2), (32, 1), (36, 0)] if time <= limit]
    assert status(state) == "step_limit"
    assert [(write.time.value, write.value.value) for write in state.register_history] == expected
    assert state.registers["R0"] == Concrete(expected[-1][1])


def test_overlapping_acquisition_invalidates_old_completion_callback() -> None:
    state, = run("acquire 0,0,4\nacquire 0,1,20\nstop",
        physical={"sequencers": {"seq": {"integration_length_ns": 16}}},
        inputs={"acquisitions": [
            {"sequencer": "seq", "acquisition_index": 0, "bin_index": 0, "i": 4, "q": 0},
            {"sequencer": "seq", "acquisition_index": 0, "bin_index": 1, "i": 16, "q": 0},
        ]})
    assert status(state) == "completed"
    results = events(state, "acquisition_result")
    assert [(event.t0.value, event.meta["duration_ns"], event.meta["interrupted"], event.meta["input_index"]) for event in results] == [
        (204, 4, True, 0), (220, 16, False, 1),
    ]
    assert [event.meta["rt_packet_id"] for event in results] == [packet.id for packet in state.rt_packets]
    assert len(state.metadata["simulation"]["acquisitions"]) == 2


def test_register_rt_operands_keep_their_enqueue_time_snapshot() -> None:
    state, = run("move 12,R0\nnop\nwait R0\nmove 20,R0\nnop\nwait R0\nstop")
    assert status(state) == "completed"
    assert state.registers["R0"] == Concrete(20)
    assert [(packet.rt_t0.value, packet.rt_t1.value, packet.meta["operand_snapshot"]) for packet in state.rt_packets] == [
        (200, 212, [12]), (212, 232, [20]),
    ]
    issues = [event for event in events(state, "q1_issue") if event.meta["op"] == "wait"]
    assert [event.meta["register_reads"]["R0"]["value"] for event in issues] == [12, 20]
    writes = [write for write in state.register_history if write.register == "R0"]
    assert [write.value.value for write in writes] == [12, 20]
    assert all(write.time.value <= issue.t0.value for write, issue in zip(writes, issues))


def test_repeated_barrier_rounds_release_only_their_own_waiters() -> None:
    a, b = run({
        "a": "wait 8\nwait_sync 4\nwait 8\nwait_sync 4\nwait 4\nstop",
        "b": "wait 28\nwait_sync 4\nwait 4\nwait_sync 4\nwait 4\nstop",
    })
    assert status(a) == status(b) == "completed"
    assert [(event.t0.value, event.t1.value) for event in events(a, "sync_wait")] == [(208, 228), (240, 240)]
    assert [(event.t0.value, event.t1.value) for event in events(b, "sync_wait")] == [(228, 228), (236, 240)]
    assert [state.rt_packets[-1].rt_t0.value for state in (a, b)] == [244, 244]
    for state in (a, b):
        auxiliaries = events(state, "sync_wait")
        primaries = events(state, "wait_sync")
        assert [event.meta["rt_packet_id"] for event in auxiliaries] == [event.meta["rt_packet_id"] for event in primaries]
        assert len({event.meta["rt_packet_id"] for event in auxiliaries}) == 2


def test_unsupported_q1_instruction_removes_pending_sync_waiter() -> None:
    a, b = run({
        "a": "wait_sync 4\n" + "nop\n" * 51 + "sw_req 1\nstop",
        "b": "wait 100\nwait_sync 4\nstop",
    })
    assert status(a) == status(b) == "blocked"
    assert a.rt_packets[0].rt_t0 is None
    assert b.rt_packets[1].rt_t0 is None
    wait, = events(a, "sync_wait")
    assert (wait.t0.value, wait.t1.value) == (200, 208)
    assert wait.meta["pending"] is True


# Independent truth table for two selected addresses: OR, NOR, AND, NAND, XOR, XNOR.
@pytest.mark.parametrize(("addresses", "expected"), [
    ([], [False, True, False, True, False, True]),
    ([1], [True, False, False, True, True, False]),
    ([5], [True, False, False, True, True, False]),
    ([1, 5], [True, False, True, False, False, True]),
])
@pytest.mark.parametrize("operator", range(6))
def test_condition_uses_all_selected_trigger_bits(addresses, expected, operator) -> None:
    state, = run(f"set_latch_en 1,4\nwait 8\nset_cond 1,17,{operator},20\nupd_param 4\nstop",
        inputs={"triggers": [{"target": "seq", "time_ns": 212, "address": address} for address in addresses]})
    assert status(state) == "completed"
    assert bool(events(state, "upd_param")) == expected[operator]
    assert bool(events(state, "conditional_skip")) != expected[operator]
    assert state.rt_time_ns == Concrete(216 if expected[operator] else 232)


@pytest.mark.parametrize("enabled", [0, 1])
def test_negative_condition_else_duration_is_excluded_before_rt_execution(enabled: int) -> None:
    state, = run(f"set_cond {enabled},0,0,-1\nupd_param 4\nset_cond 0,0,0,4\nupd_param 4\nstop")
    assert status(state) == "completed"
    assert any(diagnostic.category == "invalid_argument_value"
               and diagnostic.details.get("operand_role") == "else_duration" for diagnostic in state.diagnostics)
    assert all(packet.source.line != 1 for packet in state.rt_packets)
    assert len(events(state, "upd_param")) == 2
    assert all(event.t1.value >= event.t0.value for event in state.events)


def test_latch_reset_and_disabled_interval_control_count_lifetime() -> None:
    state, = run(
        "set_latch_en 1,4\nwait 4\nset_cond 1,1,0,4\nupd_param 4\n"
        "set_cond 0,0,0,4\nlatch_rst 4\nset_latch_en 0,4\nwait 4\n"
        "set_cond 1,1,0,4\nupd_param 4\nset_cond 0,0,0,4\n"
        "set_latch_en 1,4\nwait 4\nset_cond 1,1,0,4\nupd_param 4\nstop",
        inputs={"triggers": [{"target": "seq", "time_ns": time, "address": 1} for time in (205, 221, 233)]})
    assert status(state) == "completed"
    assert [event.t0.value for event in events(state, "upd_param")] == [208, 236]
    assert [event.t0.value for event in events(state, "conditional_skip")] == [224]


def test_pop_discards_only_prefix_and_preserves_matching_word_order() -> None:
    state, = run("wait 100\nfb_pop_data 5,R0\nnop\nfb_pop_data 5,R1\nnop\nstop",
        feedback_queue_capacity=3, inputs={"feedback": [
            {"target": "seq", "time_ns": 0, "id": identifier, "value": value}
            for identifier, value in [(9, 99), (5, 7), (5, 8)]
        ]})
    assert status(state) == "completed"
    received = events(state, "feedback_receive")
    assert [event.meta["depth"] for event in received] == [1, 2, 3]
    assert events(state, "feedback_discard")[0].meta["input_event_ids"] == [received[0].id]
    assert state.registers == {"R0": Concrete(7), "R1": Concrete(8)}
    assert [write.meta["provenance"]["input_event_id"] for write in state.register_history] == [received[1].id, received[2].id]


@pytest.mark.parametrize(("count", "expected"), [(2, "completed"), (3, "error")])
def test_feedback_capacity_is_checked_before_same_time_q1_consumption(count, expected) -> None:
    state, = run("wait 100\nfb_pop_data 5,R0\nnop\nstop", feedback_queue_capacity=2,
        inputs={"feedback": [{"target": "seq", "time_ns": 4, "id": 5, "value": value} for value in range(count)]})
    assert status(state) == expected
    assert [event.meta["depth"] for event in events(state, "feedback_receive")] == [1, 2]
    if expected == "completed":
        assert state.registers["R0"] == Concrete(0)
    else:
        assert state.registers == {}
        assert state.diagnostics[-1].category == "feedback_queue_overflow"


def test_zero_latency_self_route_preserves_send_receive_and_write_causes() -> None:
    state, = run("fb_com_data 5,7,4\nwait 100\nfb_pop_data 5,R0\nnop\nstop",
        routes=[{"source": "seq", "target": "seq", "id": 5, "latency_ns": 0}])
    assert status(state) == "completed"
    send, = events(state, "feedback_send")
    receive, = events(state, "feedback_receive")
    assert send.t0 == receive.t0 == Concrete(200)
    assert state.events.index(send) < state.events.index(receive)
    assert receive.meta["source_event_id"] == send.id
    assert state.register_history[0].meta["provenance"]["input_event_id"] == receive.id
    assert state.registers["R0"] == Concrete(7)


def test_broadcast_delivers_only_to_matching_routes() -> None:
    sender, a, b, filtered = run({
        "sender": "fb_com_data 5,0xFFFFFFFF,4\nstop",
        "a": "wait 100\nfb_pop_data 5,R0\nnop\nstop",
        "b": "wait 100\nfb_pop_data 5,R0\nnop\nstop",
        "filtered": "wait 100\nstop",
    }, routes=[
        {"source": "sender", "target": "a", "id": 5, "latency_ns": 0},
        {"source": "sender", "target": "b", "latency_ns": 12},
        {"source": "sender", "target": "filtered", "id": 6, "latency_ns": 0},
    ])
    assert all(status(state) == "completed" for state in (sender, a, b, filtered))
    send, = events(sender, "feedback_send")
    assert send.meta["targets"] == ["a", "b"]
    assert [events(state, "feedback_receive")[0].t0.value for state in (a, b)] == [200, 212]
    assert a.registers["R0"] == b.registers["R0"] == Concrete(0xFFFFFFFF)
    assert all(events(state, "feedback_receive")[0].meta["source_event_id"] == send.id for state in (a, b))
    assert not events(filtered, "feedback_receive")


@pytest.mark.parametrize("thresholded", [0, 1])
@pytest.mark.parametrize("valid", [0, 1])
def test_threshold_feedback_captures_id_and_valid_at_acquisition_start(thresholded, valid) -> None:
    readout, control = run({
        "readout": f"fb_acq_tb_id 5,4\nfb_acq_tb_valid {valid},4\nacquire 0,0,4\nfb_acq_tb_id 6,4\nfb_acq_tb_valid {1-valid},4\nwait 20\nstop",
        "control": "wait 100\nfb_pop_data 5,R0\nnop\nstop",
    }, physical={"sequencers": {"readout": {"integration_length_ns": 16}}},
       inputs={"acquisitions": [{"sequencer": "readout", "acquisition_index": 0, "bin_index": 0, "thresholded": thresholded}]},
       routes=[{"source": "readout", "target": "control", "latency_ns": 0}])
    assert status(readout) == status(control) == "completed"
    send, = events(readout, "feedback_send")
    assert (send.t0.value, send.meta["id"], send.meta["value"]) == (224, 5, valid * 2 + thresholded)
    assert send.meta["source_event_id"] == events(readout, "acquisition_result")[0].id
    assert control.registers["R0"] == Concrete(valid * 2 + thresholded)


@pytest.mark.parametrize(("shift", "expected"), [(0, [0x80000000, 0x7FFFFFFF]), (31, [0xFFFFFFFF, 0]), (32, [0xFFFFFFFF, 0]), (63, [0xFFFFFFFF, 0])])
def test_iq_feedback_arithmetic_shift_boundaries_keep_i_then_q_order(shift, expected) -> None:
    readout, control = run({
        "readout": f"fb_acq_iq_id 5,4\nfb_acq_iq_shift {shift},4\nacquire 0,0,8\nwait 20\nstop",
        "control": "wait 100\nfb_pop_data 5,R0\nnop\nfb_pop_data 5,R1\nnop\nstop",
    }, inputs={"acquisitions": [{"sequencer": "readout", "acquisition_index": 0, "bin_index": 0, "i": -(2**31), "q": 2**31-1}]},
       routes=[{"source": "readout", "target": "control", "latency_ns": 0}])
    assert status(readout) == status(control) == "completed"
    assert [control.registers[register].value for register in ("R0", "R1")] == expected
    receives = events(control, "feedback_receive")
    assert [event.t0.value for event in receives] == [216, 216]
    assert [write.meta["provenance"]["input_event_id"] for write in control.register_history] == [event.id for event in receives]
