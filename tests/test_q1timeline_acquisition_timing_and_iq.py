import json
from argparse import Namespace

import pytest

from q1timeline.analysis.interpreter import prepare_def_program
from q1timeline.cli import main
from q1timeline.def_runs import RunSnapshot
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs
from q1timeline.watch.watcher import run_watch_once


def _simulate(sources, *, hardware=None, memory=None, prepared=False, **config):
    programs = {name: parse_q1asm(source, file=name) for name, source in sources.items()}
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    return simulate_programs(programs, hardware_parameters=hardware, sequences=memory,
                             prepared_programs=bindings,
                             simulation={"startup_delay_ns": 20, **config})


def _iq_result(sequencer="a", bin_index=0, *, i=1, q=0):
    return {"sequencer": sequencer, "acquisition_index": 0, "bin_index": bin_index, "i": i, "q": q}


def _events(state, kind):
    return [event for event in state.events if event.kind == kind]


@pytest.mark.parametrize("hardware,override,rt_ns,length,source", [
    (200, None, 8, 200, "hardware_parameters"),
    (4, None, 20, 4, "hardware_parameters"),
    (200, 16, 8, 16, "configured"),
    (None, None, 8, 8, "instruction_duration_fallback"),
    (200, None, 0, 200, "hardware_parameters"),
])
def test_acquisition_timing_precedence_and_rt_independence(hardware, override, rt_ns, length, source):
    hardware = {"a": {"integration_length_acq": hardware}} if hardware is not None else None
    physical = {"sequencers": {"a": {"integration_length_ns": override}}} if override is not None else {}
    state, = _simulate({"a": f"acquire 0,0,{rt_ns}\nwait 4\nstop"}, hardware=hardware,
                       physical=physical, inputs={"acquisitions": [_iq_result()]})
    assert state.metadata["execution"]["status"] == "completed"
    packet, wait = state.rt_packets
    assert packet.duration.value == rt_ns
    assert wait.rt_t0.value == 20 + (rt_ns or 4)
    result, = _events(state, "acquisition_result")
    assert result.t0.value == 20 + length
    assert result.meta["duration_ns"] == result.meta["integration_length_ns"] == length
    assert result.meta["integration_length_source"] == source
    acquire, = _events(state, "acquire")
    assert (acquire.t0.value, acquire.t1.value) == (20, 20 + rt_ns)
    assert acquire.meta["acquisition_window"] == {
        "start_ns": 20, "end_ns": 20 + length, "planned_duration_ns": length,
        "configured_duration_ns": None if source == "instruction_duration_fallback" else length,
        "effective_duration_ns": length, "source": source, "status": "completed",
    }


def test_long_integration_is_serialized_on_the_shorter_simulated_acquire_block():
    state, = _simulate({"a": "acquire 0,0,20\nstop"}, startup_delay_ns=100,
                       hardware={"a": {"integration_length_acq": 100}}, inputs={"acquisitions": [_iq_result()]})
    ir = timeline_ir_from_states([state])
    pulse, = [event for event in ir["events"] if event["kind"] == "acquire"]
    result, = [event for event in ir["events"] if event["kind"] == "acquisition_result"]
    assert (pulse["t0"]["value"], pulse["t1"]["value"], result["t0"]["value"]) == (100, 120, 200)
    window = pulse["meta"]["acquisition_window"]
    assert (window["start_ns"], window["end_ns"], window["status"]) == (100, 200, "completed")


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("supplied", [False, True])
def test_acquisition_refusal_closes_active_rt_but_keeps_completed_window(prepared, supplied):
    state, peer = _simulate({"a": "acquire 0,0,8\nwait 100\nstop", "peer": "wait 200\nstop"},
                            startup_delay_ns=100, prepared=prepared, hardware={"a": {"integration_length_acq": 20}},
                            inputs={"acquisitions": [_iq_result()] if supplied else []})
    acquisition, packet = state.rt_packets
    pulse, = _events(state, "acquire")
    execution = state.metadata["execution"]
    assert (pulse.t0.value, pulse.t1.value) == (100, 108)
    window = pulse.meta["acquisition_window"]
    assert (window["start_ns"], window["end_ns"], window["status"]) == (100, 120, "completed")
    assert "observation_cutoff_ns" not in acquisition.meta
    assert peer.metadata["execution"]["status"] == "completed" and peer.rt_time_ns.value == 300
    if supplied:
        assert execution["status"] == "completed"
        assert (packet.rt_t0.value, packet.rt_t1.value) == (108, 208)
        assert "observation_cutoff_ns" not in packet.meta
        result, = state.metadata["simulation"]["acquisitions"]
        assert result["i"] == 1 and result["q"] == 0 and result["time_ns"] == 120
    else:
        assert execution["status"] == "blocked"
        assert execution["reason"] == "Acquisition requires a scenario result or configured physical input."
        assert state.rt_time_ns.value == packet.rt_t1.value == 120 and packet.rt_t0.value == 108
        assert packet.meta["planned_rt_end_ns"] == 208 and packet.meta["observation_cutoff_ns"] == 120
        assert not state.metadata["simulation"]["acquisitions"]
        assert not _events(state, "acquisition_result")
        unavailable, = _events(state, "acquisition_unavailable")
        assert (unavailable.t0.value, unavailable.t1.value) == (120, 120)
        assert unavailable.source == pulse.source and unavailable.meta["rt_packet_id"] == acquisition.id
        assert unavailable.meta["status"] == "unavailable"
        diagnostic, = state.diagnostics
        assert diagnostic.source == pulse.source and diagnostic.category == "analysis_incomplete"
        if prepared:
            assert diagnostic.related_events == [unavailable.id]
        else:
            wait, = _events(state, "wait")
            assert diagnostic.related_events == [unavailable.id, wait.id] and wait.t1.value == 120


def test_acquisition_feedback_refusal_keeps_observed_iq_and_clips_active_q1():
    source = "fb_acq_tb_id 7,4\nacquire 0,0,8\nwait 100\n" + "nop\n" * 24 + "move 3,R0\nnop\nadd R0,1,R1\nstop"
    state, = _simulate({"a": source}, startup_delay_ns=100, hardware={"a": {"integration_length_acq": 16}},
                       inputs={"acquisitions": [_iq_result(i=3, q=-2)]})
    execution = state.metadata["execution"]
    assert execution["status"] == "blocked"
    assert execution["reason"] == "Threshold feedback requires a thresholded acquisition result."
    result, = _events(state, "acquisition_result")
    wait, = _events(state, "wait")
    add = _events(state, "q1_issue")[-1]
    assert result.t0.value == wait.t1.value == add.t1.value == 120
    assert (result.meta["i"], result.meta["q"]) == (3, -2)
    assert state.metadata["simulation"]["acquisitions"][0]["event_id"] == result.id
    assert wait.meta["planned_rt_end_ns"] == 212 and wait.meta["observation_cutoff_ns"] == 120
    assert add.meta["op"] == "add" and add.t0.value == 116 and add.meta["planned_q1_end_ns"] == 128
    assert add.meta["pending_register_ready_ns"] == {"R1": 132} and "R1" not in state.registers
    diagnostic, = state.diagnostics
    assert diagnostic.source == result.source and diagnostic.related_events == [wait.id]
    assert not _events(state, "feedback_send")


def test_acquisition_refusal_preserves_rt_packet_ending_at_the_same_tick():
    state, = _simulate({"a": "acquire 0,0,8\nwait 12\nstop"}, startup_delay_ns=100,
                       hardware={"a": {"integration_length_acq": 20}})
    wait, = _events(state, "wait")
    packet = state.rt_packets[-1]
    assert state.metadata["execution"]["status"] == "blocked"
    assert packet.rt_t1 == wait.t1 == state.rt_time_ns and wait.t1.value == 120
    assert "observation_cutoff_ns" not in packet.meta and "observation_cutoff_ns" not in wait.meta
    unavailable, = _events(state, "acquisition_unavailable")
    assert unavailable.t0 == wait.t1 and state.diagnostics[-1].related_events == [unavailable.id]
    assert not _events(state, "stop")


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("configured", [None, 100])
def test_fault_bounds_an_incomplete_integration_without_publishing_its_result(configured, prepared):
    state, peer = _simulate({"a": "fb_acq_tb_id 7,4\nacquire 0,0,40\nillegal", "peer": "wait 200\nstop"},
                            startup_delay_ns=4, prepared=prepared,
                            hardware={"a": {"integration_length_acq": configured}} if configured is not None else None,
                            inputs={"acquisitions": [{**_iq_result(), "thresholded": 1}]},
                            trigger_routes=[{"source": "a", "target": "peer", "address": 3, "latency_ns": 0}])
    assert state.metadata["execution"]["status"] == "error"
    assert peer.metadata["execution"]["status"] == "completed" and peer.rt_time_ns.value == 204
    acquire, = _events(state, "acquire")
    assert (acquire.t0.value, acquire.t1.value) == (8, 12) and acquire.meta["interrupted_by_fault"]
    assert acquire.meta["acquisition_window"] == {
        "start_ns": 8, "end_ns": 12, "planned_duration_ns": configured or 40, "configured_duration_ns": configured,
        "effective_duration_ns": 4, "source": "hardware_parameters" if configured else "instruction_duration_fallback",
        "status": "incomplete",
    }
    assert not state.metadata["simulation"]["acquisitions"]
    assert not any(event.kind in {"acquisition_result", "acquisition_unavailable", "feedback_send"} for event in state.events)
    assert not _events(peer, "trigger_receive")


def test_zero_duration_without_integration_reports_missing_length():
    state, = _simulate({"a": "acquire 0,0,0\nstop"}, inputs={"acquisitions": [_iq_result()]})
    assert state.metadata["execution"]["status"] == "blocked"
    assert "configured integration length" in state.metadata["execution"]["reason"]
    assert not _events(state, "acquisition_result")


def test_physical_integration_uses_the_hardware_length():
    memory = {"a": {"acquisitions": {"a": {"index": 0, "num_bins": 1}}}}
    state, = _simulate({"a": "acquire 0,0,8\nstop"}, memory=memory, hardware={"a": {"integration_length_acq": 200}},
                       physical={"sequencers": {"a": {"input": {"kind": "constant", "i": 0.5, "q": 0.0}}}})
    result, = _events(state, "acquisition_result")
    assert result.t0.value == 220
    assert result.meta["i"] == pytest.approx(100)
    exported, = state.metadata["simulation"]["signals"]["acquisitions"]
    assert exported["integration_length_source"] == "hardware_parameters"


@pytest.fixture
def two_window_iq_model():
    return {
        "memory": {"a": {"acquisitions": {"data": {"index": 0, "num_bins": 1}},
                         "weights": {"wi": {"index": 0, "data": [0.5] * 8}, "wq": {"index": 1, "data": [1] * 8}}}},
        "physical": {"sequencers": {"a": {"threshold": 3.5, "integration_length_ns": 8, "input": {
            "kind": "trace", "source": "Two-window scenario", "start_ns": 100,
            "i": [0.25] * 8 + [0.5] * 8, "q": [0.125] * 8 + [-0.25] * 8,
        }}}},
    }


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("weighted", [False, True])
def test_modeled_iq_bin_snapshots_preserve_integrals_at_each_completion(two_window_iq_model, prepared, weighted):
    second = "acquire_weighed 0,0,0,1,8" if weighted else "acquire 0,0,8"
    state, = _simulate({"a": f"acquire 0,0,8\n{second}\nstop"}, prepared=prepared, startup_delay_ns=100,
                       **two_window_iq_model)
    simulation = state.metadata["simulation"]
    first, latest = simulation["acquisitions"]
    assert state.metadata["execution"]["status"] == "completed"
    assert (first["time_ns"], first["i"], first["q"], first["thresholded"]) == (108, 2, 1, 0)
    assert (latest["time_ns"], latest["i"], latest["q"], latest["thresholded"]) == (116, 2 if weighted else 4, -2, int(not weighted))
    assert first["mean_i"] == 0.25 and latest["mean_i"] == (0.25 if weighted else 0.5)
    units = "normalized full scale * ns"
    expected = [
        {"kind": "iq", "count": 1, "mean_i": 2, "mean_q": 1, "thresholded_count": 0, "units": units},
        {"kind": "iq", "count": 2, "mean_i": 2 if weighted else 3, "mean_q": -0.5,
         "thresholded_count": int(not weighted), "units": units},
    ]
    assert [item["bin_aggregate"] for item in (first, latest)] == expected
    assert [item["bin_aggregate"] for item in simulation["signals"]["acquisitions"]] == expected
    final, = simulation["signals"]["bins"]
    assert all(final[key] == expected[1][key] for key in ("count", "mean_i", "mean_q", "thresholded_count"))
    assert all("bin_aggregate" not in event.meta for event in _events(state, "acquire"))
    if not prepared:
        ir = timeline_ir_from_states([state])
        results = [event for event in ir["events"] if event["kind"] == "acquisition_result"]
        assert [(event["t0"]["value"], event["meta"]["bin_aggregate"]) for event in results] == list(zip((108, 116), expected))


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("routed", [False, True])
def test_scenario_and_unavailable_iq_do_not_write_numerical_bin(two_window_iq_model, prepared, routed):
    source = "acquire 0,0,8\n" * 3 + "stop"
    routes = [{"source": "a", "target": "peer", "address": 3, "latency_ns": 2}] if routed else []
    state, peer = _simulate({"a": source, "peer": "wait 32\nstop"}, prepared=prepared, startup_delay_ns=100,
                            inputs={"acquisitions": [{**_iq_result(i=17, q=-9), "thresholded": 0}]},
                            trigger_routes=routes, **two_window_iq_model)
    simulation = state.metadata["simulation"]
    scenario, modeled = simulation["acquisitions"]
    assert (scenario["time_ns"], scenario["i"], scenario["q"], scenario["source"]["kind"]) == (108, 17, -9, "scenario")
    assert "bin_aggregate" not in scenario
    aggregate = {"kind": "iq", "count": 1, "mean_i": 4, "mean_q": -2, "thresholded_count": 1,
                 "units": "normalized full scale * ns"}
    assert modeled["time_ns"] == 116 and modeled["bin_aggregate"] == aggregate
    assert [item["bin_aggregate"] for item in simulation["signals"]["acquisitions"]] == [aggregate]
    assert simulation["signals"]["bins"][0]["count"] == 1
    assert state.metadata["execution"]["status"] == "blocked"
    assert state.metadata["execution"]["terminal"]["local_time_ns"] == 124
    assert peer.metadata["execution"]["status"] == "completed"
    missing, = _events(state, "acquisition_unavailable")
    assert missing.t0.value == 124 and missing.source.line == 3 and missing.meta["status"] == "unavailable"
    assert "bin_aggregate" not in missing.meta
    terminal, = [diagnostic for diagnostic in state.diagnostics if diagnostic.category == "analysis_incomplete"]
    assert terminal.related_events == [missing.id]
    if routed:
        assert missing.meta["trigger_threshold_status"] == "unknown"
        uncertainty, = [diagnostic for diagnostic in state.diagnostics
                        if diagnostic.category == "acquisition_trigger_threshold_unknown"]
        assert uncertainty.related_events == [missing.id]
    else:
        assert "trigger_threshold_status" not in missing.meta
        assert state.diagnostics == [terminal]


@pytest.mark.parametrize("prepared", [False, True])
def test_missing_iq_without_route_keeps_terminal_evidence_and_prior_result(prepared):
    state, = _simulate({"a": "acquire 0,0,4\nacquire 0,0,4\nstop"}, startup_delay_ns=100, prepared=prepared,
                       inputs={"acquisitions": [{**_iq_result(i=17, q=-9), "thresholded": 0}]})
    result, = state.metadata["simulation"]["acquisitions"]
    assert (result["time_ns"], result["i"], result["q"]) == (104, 17, -9)
    assert state.metadata["execution"]["terminal"]["local_time_ns"] == 108

    ir = timeline_ir_from_states([state])
    missing, = [event for event in ir["events"] if event["kind"] == "acquisition_unavailable"]
    assert missing["t0"]["value"] == missing["t1"]["value"] == 108
    assert missing["source"]["line"] == 2 and missing["meta"]["source"] == {"kind": "missing"}
    assert ir["source_map"]["by_event_id"][missing["id"]] == missing["source"]
    assert missing["id"] != result["event_id"]
    terminal, = [diagnostic for diagnostic in ir["diagnostics"] if diagnostic["category"] == "analysis_incomplete"]
    assert terminal["source"] == missing["source"] and terminal["related_events"] == [missing["id"]]


@pytest.mark.parametrize("physical", [{}, {"sequencers": {"a": {"input": {"kind": "constant", "i": 0.5}}}}])
def test_supplied_weighted_result_uses_weight_memory_with_or_without_physical_model(physical):
    state, = _simulate({"a": "acquire_weighted 0,0,0,1,0\nstop"},
                       hardware={"a": {"integration_length_acq": 200}},
                       memory={"a": {"weights": {"w0": {"index": 0, "data": [1] * 8},
                                                    "w1": {"index": 1, "data": [1] * 12}}}},
                       inputs={"acquisitions": [_iq_result()]}, physical=physical)
    result, = _events(state, "acquisition_result")
    assert result.t0.value == 32
    assert result.meta["integration_length_source"] == "weight_memory"
    acquire, = _events(state, "acquire")
    assert acquire.meta["acquisition_window"] == {"start_ns": 20, "end_ns": 32, "planned_duration_ns": 12,
                                                "configured_duration_ns": 12, "effective_duration_ns": 12,
                                                "source": "weight_memory", "status": "completed"}


@pytest.mark.parametrize("physical", [
    {}, {"sequencers": {"other": {}}}, {"sequencers": {"a": {"input": {"kind": "constant", "i": 0.5}}}},
], ids=["disabled", "unrelated", "same-sequencer"])
def test_supplied_weighted_result_without_weights_preserves_duration_and_precedence(physical):
    state, other = _simulate({"a": "acquire_weighed 0,0,0,1,20\nstop", "other": "wait 32\nstop"},
                             startup_delay_ns=100, physical=physical, inputs={"acquisitions": [_iq_result(i=17, q=-9)]})
    assert state.metadata["execution"]["status"] == other.metadata["execution"]["status"] == "completed"
    result, = _events(state, "acquisition_result")
    assert result.t0.value == 120
    assert (result.meta["i"], result.meta["q"]) == (17, -9)
    assert result.meta["duration_ns"] == result.meta["integration_length_ns"] == 20
    assert result.meta["integration_length_source"] == "instruction_duration_fallback"
    assert result.meta["source"] == {"kind": "scenario", "label": "acquisition input 0"}
    assert result.meta["input_index"] == 0
    if physical:
        assert not state.metadata["simulation"]["signals"]["acquisitions"]


@pytest.mark.parametrize("supplied", [False, True])
@pytest.mark.parametrize("prepared", [False, True])
def test_numerical_weighted_acquisition_still_requires_weights_after_scenario_rows_are_consumed(supplied, prepared):
    weighted = "acquire_weighed 0,0,0,1,20\n"
    state, = _simulate({"a": weighted * (2 if supplied else 1) + "stop"},
                       physical={"sequencers": {"a": {"input": {"kind": "constant", "i": 0.5}}}},
                       inputs={"acquisitions": [_iq_result()] if supplied else []}, prepared=prepared)
    assert state.metadata["execution"]["status"] == "error"
    assert "Missing integration weights" in state.metadata["execution"]["reason"]
    assert state.metadata["execution"]["source"]["line"] == (2 if supplied else 1)
    assert [result["time_ns"] for result in state.metadata["simulation"]["acquisitions"]] == ([40] if supplied else [])


def test_supplied_weighted_result_without_weights_still_requires_nonzero_duration():
    state, = _simulate({"a": "acquire_weighed 0,0,0,1,0\nstop"},
                       physical={"sequencers": {"a": {"input": {"kind": "constant", "i": 0.5}}}},
                       inputs={"acquisitions": [_iq_result()]})
    assert state.metadata["execution"]["status"] == "blocked"
    assert "Zero-duration RT acquisition" in state.metadata["execution"]["reason"]
    assert not _events(state, "acquisition_result")


@pytest.mark.parametrize("physical", [
    {}, {"sequencers": {"rx": {}}}, {"sequencers": {"tx": {"gain": [0.5, 0.5]}}},
], ids=["disabled", "unrelated", "output-only"])
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("weight_memory", [False, True])
def test_weighted_acquisition_without_its_own_input_keeps_completion_edge(physical, prepared, weight_memory):
    memory = {"tx": {"weights": {"i": {"index": 0, "data": [1] * 8}, "q": {"index": 1, "data": [1] * 12}}}} if weight_memory else None
    tx, rx = _simulate({"tx": "acquire_weighed 0,0,0,1,20\nstop", "rx": "wait_trigger 3,4\nstop"},
                       startup_delay_ns=100, prepared=prepared, memory=memory, physical=physical,
                       trigger_routes=[{"source": "tx", "target": "rx", "address": 3, "latency_ns": 5}])
    assert tx.metadata["execution"]["status"] == rx.metadata["execution"]["status"] == "blocked"
    assert "requires a scenario result" in tx.metadata["execution"]["reason"]
    assert not tx.metadata["simulation"]["acquisitions"]
    missing, = _events(tx, "acquisition_unavailable")
    length = 12 if weight_memory else 20
    assert missing.t0.value == 100 + length and missing.source.line == 1
    assert missing.meta["duration_ns"] == missing.meta["integration_length_ns"] == length
    assert missing.meta["integration_length_source"] == ("weight_memory" if weight_memory else "instruction_duration_fallback")
    assert missing.meta["trigger_threshold_status"] == "unknown"
    assert missing.id in tx.diagnostics[-1].related_events
    acquire, = _events(tx, "acquire")
    window = acquire.meta["acquisition_window"]
    assert window["status"] == "completed" and window["end_ns"] == missing.t0.value
    diagnostic, = rx.diagnostics
    assert diagnostic.category == "acquisition_trigger_outcome_unknown" and diagnostic.source.line == 1
    assert rx.metadata["simulation"]["end_time_ns"] == 105 + length
    assert not _events(rx, "trigger_receive")
    if not prepared:
        wait, = _events(rx, "trigger_wait")
        assert wait.t0.value == 100 and wait.t1.value == 105 + length and wait.meta["pending"]


def test_weighted_acquisition_without_own_input_and_zero_duration_requires_weight_memory():
    state, = _simulate({"a": "acquire_weighed 0,0,0,1,0\nstop"},
                       physical={"sequencers": {"a": {"gain": [0.5, 0.5]}}})
    assert state.metadata["execution"]["status"] == "blocked"
    assert "Zero-duration RT acquisition" in state.metadata["execution"]["reason"]
    assert not _events(state, "acquisition_unavailable")


def test_configured_lengths_remain_separate_across_sequencers_and_after_interruption():
    first, second = _simulate({"a": "acquire 0,0,8\nacquire 0,1,8\nstop", "b": "acquire 0,0,8\nstop"},
                              hardware={"a": {"integration_length_acq": 200}, "b": {"integration_length_acq": 31}},
                              inputs={"acquisitions": [_iq_result(), _iq_result(bin_index=1), _iq_result("b")]})
    assert [(event.t0.value, event.meta["interrupted"], event.meta["duration_ns"], event.meta["integration_length_ns"])
            for event in _events(first, "acquisition_result")] == [(28, True, 8, 200), (228, False, 200, 200)]
    assert _events(second, "acquisition_result")[0].t0.value == 51
    earlier, later = _events(first, "acquire")
    window = earlier.meta["acquisition_window"]
    assert window["status"] == "interrupted" and window["end_ns"] == 28 and window["planned_duration_ns"] == 200
    assert window["interrupted_by_event_id"] == later.id
    assert later.meta["acquisition_window"]["status"] == "completed"


def _same_edge_trigger_run(receiver, *, integration=64, possible=False, receiver_first=False, prepared=False,
                           route=None, extra_sender=False):
    sender = "acquire 0,0,20\nacquire 0,1,4\nstop"
    sources = {"tx": sender, "rx": receiver}
    if extra_sender:
        sources["known"] = sender
    order = reversed(sources) if receiver_first else sources
    programs = {name: parse_q1asm(sources[name], file=f"{name}.q1asm") for name in order}
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    senders = [name for name in sources if name != "rx"]
    acquisitions = []
    for name in senders:
        first = _iq_result(name)
        if not possible or name == "known":
            first["thresholded"] = 1
        acquisitions.extend([first, {**_iq_result(name, bin_index=1), "thresholded": 0}])
    states = simulate_programs(programs, prepared_programs=bindings,
                               hardware_parameters={name: {"integration_length_acq": integration} for name in senders},
                               sequences={name: {"acquisitions": {"a": {"index": 0, "num_bins": 2}}} for name in senders},
                               simulation={
                                   "startup_delay_ns": 100, "inputs": {"acquisitions": acquisitions},
                                   "trigger_networks": {"rack": {"grid_epoch_ns": 100}},
                                   "trigger_routes": [{"source": name, "target": "rx", "address": 1,
                                                       **(route or {"latency_ns": 0})} for name in senders],
                               })
    return {state.sequencer_id: state for state in states}


@pytest.mark.parametrize("receiver_first", [False, True])
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("integration,possible", [(64, False), (20, False), (64, True), (20, True)])
def test_same_tick_analog_trigger_observes_completion_phase(integration, possible, receiver_first, prepared):
    receiver = "set_latch_en 1,20\nset_cond 1,1,0,8\nupd_param 4\nupd_param 4\nstop"
    states = _same_edge_trigger_run(receiver, integration=integration, possible=possible,
                                   receiver_first=receiver_first, prepared=prepared)
    tx, rx = states["tx"], states["rx"]
    first, _ = tx.metadata["simulation"]["acquisitions"]
    interrupted = integration == 64
    assert first["time_ns"] == 120 and first["interrupted"] is interrupted
    first_packet, second_packet = [packet for packet in rx.rt_packets if packet.op == "upd_param"]
    assert bool(first_packet.meta.get("conditional_skipped")) is interrupted
    if possible:
        assert rx.metadata["execution"]["status"] == "blocked"
        assert rx.rt_time_ns.value == (128 if interrupted else 120)
        assert any(d.category == "acquisition_trigger_outcome_unknown" for d in rx.diagnostics)
        assert not _events(rx, "trigger_receive")
    else:
        assert rx.metadata["execution"]["status"] == "completed"
        assert first_packet.rt_t0.value == 120
        assert second_packet.rt_t0.value == (128 if interrupted else 124)
        assert not second_packet.meta.get("conditional_skipped")
        if not prepared:
            receive, = _events(rx, "trigger_receive")
            assert receive.t0.value == 120 and receive.meta["source_event_id"] == first["event_id"]


@pytest.mark.parametrize("receiver_first", [False, True])
@pytest.mark.parametrize("possible,extra_sender", [(False, False), (True, False), (True, True)])
def test_same_edge_wait_can_receive_an_interrupted_acquisition_trigger(receiver_first, possible, extra_sender):
    states = _same_edge_trigger_run("wait 20\nwait_trigger 1,4\nstop", possible=possible,
                                   receiver_first=receiver_first, extra_sender=extra_sender)
    rx = states["rx"]
    if possible and not extra_sender:
        assert rx.metadata["execution"]["status"] == "blocked"
        assert not _events(rx, "trigger_receive")
        pending, = _events(rx, "trigger_wait")
        assert pending.t0.value == pending.t1.value == 120
    else:
        assert rx.metadata["execution"]["status"] == "completed"
        receive, = _events(rx, "trigger_receive")
        assert receive.t0.value == 120 and rx.rt_time_ns.value == 124


@pytest.mark.parametrize("route,arrival", [({"latency_ns": 4}, 124), ({"network": "rack"}, 340)])
def test_interrupted_acquisition_keeps_existing_delayed_trigger_delivery(route, arrival):
    receiver = f"set_latch_en 1,{arrival - 100}\nset_cond 1,1,0,8\nupd_param 4\nstop"
    states = _same_edge_trigger_run(receiver, route=route)
    receive, = _events(states["rx"], "trigger_receive")
    command, = _events(states["rx"], "upd_param")
    assert receive.t0.value == command.t0.value == arrival


def test_project_acquisition_timing_reaches_cli_watch_and_prepared_def_runs(tmp_path, capsys):
    (tmp_path / "readout.q1asm").write_text("fb_acq_iq_id 5,4\nacquire 0,0,8\nstop\n", encoding="utf-8")
    (tmp_path / "control.q1asm").write_text("wait 0\nfb_pop_data 5,R2\nnop\nstop\n", encoding="utf-8")
    project = tmp_path / "q1timeline.yml"
    project.write_text("""analysis:
  execution_mode: simulation
alignment:
  mode: none
sequencers:
  - id: readout
    file: readout.q1asm
    hardware_parameters:
      integration_length_acq: 200
  - id: control
    file: control.q1asm
simulation:
  startup_delay_ns: 20
  inputs:
    acquisitions:
      - {sequencer: readout, acquisition_index: 0, bin_index: 0, i: 17, q: 0}
  routes:
    - {source: readout, target: control, id: 5, latency_ns: 12}
""", encoding="utf-8")
    assert main(["analyze", "--project", str(project), "--format", "vscode-json", "--include-timeline-ir", "--no-render"]) == 0
    cli_ir = json.loads(capsys.readouterr().out)["timeline_ir"]
    watch = run_watch_once(project, tmp_path / "watch")
    assert watch.ok
    watch_ir = json.loads(watch.timeline_ir.read_text(encoding="utf-8"))
    run = RunSnapshot(Namespace(project=project, execution_mode=None, max_steps=None, q1asm_strict=False,
                                hardware_readiness=False, params=None, set_param=[]))
    detailed = run.run("control", "R2", {}, detail=True)
    for ir in (cli_ir, watch_ir, detailed["ir"]):
        acquire, = [event for event in ir["events"] if event["kind"] == "acquire"]
        result, = [event for event in ir["events"] if event["kind"] == "acquisition_result"]
        receives = [event["t0"]["value"] for event in ir["events"] if event["kind"] == "feedback_receive"]
        assert acquire["duration"]["value"] == 8
        assert result["t0"]["value"] == 224 and result["meta"]["integration_length_ns"] == 200
        assert receives == [236, 236]
    prepared = run.prepare_statistics("control", {}, set())
    case = run.run("control", "R2", {}, prepared=prepared)
    assert case == {"status": "completed", "engine_completed": True, "word": 17, "q1_ns": 248}


@pytest.mark.parametrize("i,q,shift,scale,expected", [
    (2**35, -(2**35), 8, None, (0x08000000, 0xF8000000)),
    (0x123480000000, -(2**35 + 1), 0, None, (0x80000000, 0xFFFFFFFF)),
    (2**47 - 1, -(2**47), 16, None, (0x7FFFFFFF, 0x80000000)),
    (2**25, -(2**25), 8, 1024, (0x08000000, 0xF8000000)),
])
def test_iq_selects_shifted_words_from_the_48_bit_accumulator(i, q, shift, scale, expected):
    readout, control = _simulate({
        "a": f"fb_acq_iq_id 5,4\nfb_acq_iq_shift {shift},4\nacquire 0,0,8\nfb_acq_iq_shift 0,4\nstop",
        "b": "wait 0\nfb_pop_data 5,R0\nnop\nfb_pop_data 5,R1\nnop\nstop",
    }, hardware={"a": {"integration_length_acq": 40}},
       acquisition_iq_scale={"a": scale} if scale is not None else {},
       inputs={"acquisitions": [_iq_result(i=i, q=q)]}, feedback_queue_capacity=2,
       routes=[{"source": "a", "target": "b", "id": 5, "latency_ns": 4}])
    assert readout.metadata["execution"]["status"] == control.metadata["execution"]["status"] == "completed"
    assert tuple(control.registers[register].value for register in ("R0", "R1")) == expected
    receives = _events(control, "feedback_receive")
    assert [event.meta["depth"] for event in receives] == [1, 2]
    assert [event.t0.value for event in receives] == [72, 72]
    assert [write.meta["provenance"]["input_event_id"] for write in control.register_history] == [event.id for event in receives]
    pulse, = _events(readout, "acquire")
    result, = _events(readout, "acquisition_result")
    sends = _events(readout, "feedback_send")
    assert pulse.t1.value == 36 and pulse.meta["acquisition_window"]["end_ns"] == result.t0.value == 68
    assert [event.t0.value for event in sends] == [68, 68]
    assert [event.meta["source_event_id"] for event in sends] == [result.id, result.id]
    assert all(readout.events.index(result) < readout.events.index(event) for event in sends)


@pytest.mark.parametrize("i", [2**47, -(2**47) - 1])
def test_outside_signed_48_bit_accumulator_range_sends_no_partial_iq_pair(i):
    state, = _simulate({"a": "fb_acq_iq_id 5,4\nfb_acq_iq_shift 8,4\nacquire 0,0,8\nstop"},
                       inputs={"acquisitions": [_iq_result(i=i)]})
    assert state.metadata["execution"]["status"] == "error"
    assert "signed 48-bit range" in state.metadata["execution"]["reason"]
    assert not _events(state, "feedback_send")
