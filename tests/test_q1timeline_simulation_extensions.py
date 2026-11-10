"""Independent expectations for the instruction simulation implementation spec."""
import json

import pytest

from q1timeline.analysis.api import analyze_documents
from q1timeline.analysis.interpreter import interpret_program, prepare_def_program
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


def run(source, prepared=False, sequences=None, **config):
    documents = source if isinstance(source, dict) else {"s": source}
    programs = {name: parse_q1asm(text, file=name) for name, text in documents.items()}
    bindings = {name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                for name, program in programs.items()} if prepared else None
    return simulate_programs(programs, simulation=config, sequences=sequences, prepared_programs=bindings)


def status(state):
    return state.metadata["execution"]["status"]


def events(state, kind):
    return [event for event in state.events if event.kind == kind]


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("op", ["wait", "upd_param"])
def test_zero_packet_waits_for_late_enqueue_once(op, prepared):
    state, = run(f"{op} 0\n" + "nop\n" * 20 + "wait 4\nstop", prepared, startup_delay_ns=4)
    assert status(state) == "completed"
    assert [packet.rt_t0.value for packet in state.rt_packets] == [4, 88]
    assert state.rt_packets[0].duration == Concrete(0)
    assert state.rt_packets[0].meta["next_dequeue_not_before_ns"] == 8
    assert not events(state, "underflow_warning")
    if not prepared:
        assert len(events(state, "rt_queue_dequeue")) == 2


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("config,expected", [({"startup_delay_ns": 4}, [4, 8, 12]),
                                          ({"startup_delay_ns": 40}, [40, 44, 48]),
                                          ({}, [16, 20, 24])])
def test_zero_packets_and_same_time_enqueue_keep_four_ns_spacing(prepared, config, expected):
    state, = run("wait 0\nwait 0\nwait 4\nstop", prepared, **config)
    assert status(state) == "completed"
    assert [packet.rt_t0.value for packet in state.rt_packets] == expected


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("receiver,ready", [("fb_pop_data 7,R1", 108), ("fb_pull_data R0,R1", 112)])
def test_zero_packet_allows_feedback_wait_and_independent_peer(prepared, receiver, ready):
    state, peer = run({"s": f"wait 0\n{receiver}\nwait 4\nstop", "peer": "wait 120\nstop"}, prepared,
                      startup_delay_ns=4, inputs={"feedback": [{"target": "s", "time_ns": 100, "id": 7, "value": 42}]})
    assert status(state) == status(peer) == "completed"
    assert [packet.rt_t0.value for packet in state.rt_packets] == [4, ready]
    assert state.registers["R1"] == Concrete(42)
    assert peer.rt_time_ns == Concrete(124)


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("suffix,expected", [("nop\n" * 10 + "stop", "completed"),
                                           ("fb_pop_data 7,R0\nstop", "blocked"),
                                           ("fb_pull_data R0,R1\nstop", "blocked")])
def test_empty_zero_queue_drains_or_reports_the_blocking_input(prepared, suffix, expected):
    state, = run("wait 0\n" + suffix, prepared, startup_delay_ns=4)
    assert status(state) == expected
    assert not any(item.category == "rt_queue_underflow" for item in state.diagnostics)
    if expected == "blocked":
        assert "feedback_input" in state.metadata["execution"]["reason"]


@pytest.mark.parametrize("prepared", [False, True])
def test_nonzero_packet_restores_underflow_guard(prepared):
    state, = run("wait 0\nwait 4\n" + "nop\n" * 20 + "wait 4\nstop", prepared, startup_delay_ns=4)
    assert status(state) == "error"
    assert "underflow at 12 ns" in state.metadata["execution"]["reason"]


@pytest.mark.parametrize("duration", [1, 2, 3])
def test_short_nonzero_duration_stays_invalid(duration):
    state, = run(f"wait {duration}\nstop")
    assert status(state) == "error"


@pytest.mark.parametrize("prepared", [False, True])
def test_zero_sync_uses_release_and_zero_trigger_uses_arrival(prepared):
    state, peer = run({"s": "wait_sync 0\nwait 4\nstop", "peer": "wait 20\nwait_sync 0\nwait 4\nstop"},
                      prepared, startup_delay_ns=8)
    assert status(state) == status(peer) == "completed"
    assert [packet.rt_t0.value for packet in state.rt_packets] == [8, 32]
    state, = run("wait_trigger 1,0\nwait 4\nstop", prepared, startup_delay_ns=8,
                 inputs={"triggers": [{"target": "s", "time_ns": 100, "address": 1}]})
    assert status(state) == "completed"
    assert [packet.rt_t0.value for packet in state.rt_packets] == [8, 104]
    assert state.rt_packets[0].meta["queue_duration_ns"] == 0


@pytest.mark.parametrize("prepared", [False, True])
def test_skipped_zero_else_does_not_apply_output_or_feedback(prepared):
    state, = run("set_mrk 3\nset_cond 1,1,0,0\nupd_param 4\nfb_com_data 7,42,4\n" + "nop\n" * 20 + "stop",
                 prepared, startup_delay_ns=12)
    assert status(state) == "completed"
    assert all(packet.meta["conditional_skipped"] for packet in state.rt_packets)
    assert not events(state, "marker") and not events(state, "feedback_send")
    assert not events(state, "underflow_warning")


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation", "prepared"])
@pytest.mark.parametrize("op,left,right,low,high,flags", [
    ("mulu32", 0xFFFFFFFF, 2, 0xFFFFFFFE, 1, (False, False, True, True)),
    ("muls32", -1, 2, 0xFFFFFFFE, 0xFFFFFFFF, (False, True, False, False)),
    ("muls32", -2147483648, -1, 0x80000000, 0, (False, False, True, True)),
    ("mulu32", 0xFFFFFFFF, 0xFFFFFFFF, 1, 0xFFFFFFFE, (False, True, True, True)),
    ("muls32", 0, -1, 0, 0, (True, False, False, False)),
])
@pytest.mark.parametrize("form", ["ir", "ri", "rr"])
def test_full_multiply_words_and_flags_in_every_executor(mode, op, left, right, low, high, flags, form):
    operands = {"ir": f"{left},R1", "ri": f"R0,{right}", "rr": "R0,R1"}[form]
    source = f"move {left},R0\nmove {right},R1\nnop\n{op} {operands},R2,R3\nnop\nstop"
    if mode in {"simulation", "prepared"}:
        state, = run(source, mode == "prepared")
    else:
        state = interpret_program(parse_q1asm(source), sequencer_id="s", execution_mode=mode)
    assert (state.registers["R2"], state.registers["R3"]) == (Concrete(low), Concrete(high))
    assert state.alu_flags == dict(zip(("zf", "nf", "cf", "of"), flags))
    assert not state.rt_packets
    if mode == "simulation":
        multiply = next(event for event in events(state, "q1_issue") if event.meta["op"] == op)
        assert multiply.duration == Concrete(24)


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("destinations", ["R0,R1", "R1,R0"])
def test_full_product_reads_both_sources_before_either_write(prepared, destinations):
    state, = run(f"move 4294967295,R0\nmove 2,R1\nnop\nmulu32 R0,R1,{destinations}\nnop\nstop", prepared)
    low, high = destinations.split(",")
    assert (state.registers[low], state.registers[high]) == (Concrete(0xFFFFFFFE), Concrete(1))


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("gap,expected", [("", "error"), ("nop\n", "completed")])
def test_full_product_readiness_boundary(prepared, gap, expected):
    state, = run("move 1,R0\nnop\nmulu32 R0,2,R1,R2\n" + gap + "move R2,R3\nnop\nstop", prepared)
    assert status(state) == expected
    if expected == "completed":
        assert state.registers["R3"] == Concrete(0)


@pytest.mark.parametrize("prepared", [False, True])
def test_full_product_rejects_destinations_resolving_to_one_register(prepared):
    state, = run("move 1,R0\nnop\nmulu32 R0,2,R1,R1\nstop", prepared)
    assert status(state) == "blocked"
    assert "write priority" in state.metadata["execution"]["reason"]


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation", "prepared"])
@pytest.mark.parametrize("op,left,right,branch", [("mulu32", 0xFFFFFFFF, 2, "jo"), ("mulu32", 0xFFFFFFFF, 2, "jb"),
                                                 ("muls32", -1, 2, "js"), ("muls32", -1, 2, "jno"),
                                                 ("muls32", -2147483648, -1, "jo"), ("mulu32", 65536, 65536, "jnz")])
def test_full_product_flag_branches(mode, op, left, right, branch):
    source = f"move {left},R0\nnop\n{op} R0,{right},R1,R2\n{branch} @yes\nmove 0,R3\nstop\nyes: move 42,R3\nnop\nstop"
    state = (run(source, mode == "prepared")[0] if mode in {"simulation", "prepared"} else
             interpret_program(parse_q1asm(source), sequencer_id="s", execution_mode=mode))
    assert state.registers["R3"] == Concrete(42)


@pytest.mark.parametrize("prepared", [False, True])
def test_full_product_destination_aliases_are_physical(prepared):
    state, = run(".DEF DST R1\nmove 1,R0\nnop\nmulu32 R0,2,R1,$DST\nstop", prepared)
    assert status(state) == "blocked" and "same register" in state.metadata["execution"]["reason"]


def netzero_config(**output):
    return {"sample_period_ns": 1, "max_samples": 20000, "sequencers": {"s": {"netzero": {
        "module_type": "QCM", "outputs": [{"output": 0, "path": 0, "enabled": True, "ihp_enabled": True,
                                          "amplitude": 0.5, "slew_rate_per_s": 2e6, **output}]}}}}


def netzero_results(state):
    return state.metadata["simulation"]["netzero"]


@pytest.mark.parametrize("prepared", [False, True])
def test_netzero_reference_offsets_have_opposite_area_bounded_pulse(prepared):
    # Qblox 202_net_zeroing: approx. 954.5 FS*ns, 0.5 amplitude, 2e6 /s slew,
    # approx. 2.2 us compensation. Calculate exact input area from the three integers.
    source = ("set_awg_offs 16384,16384\nupd_param 1500\nset_awg_offs -8300,-8300\nupd_param 1000\n"
              "set_awg_offs 5000,5000\nupd_param 3000\nset_awg_offs 0,0\nupd_param 1000\n"
              "play_netzero 3000,4\nwait 2996\nstop")
    state, = run(source, prepared, physical=netzero_config())
    assert status(state) == "completed"
    result, = netzero_results(state)
    area = (16384 * 1500 - 8300 * 1000 + 5000 * 3000) / 32768
    assert result["area_before"] == pytest.approx(area)
    assert result["compensation_area"] == pytest.approx(-area)
    assert all(-0.5 <= sample < 0 for sample in result["samples"])
    assert 2100 <= result["end_ns"] - result["start_ns"] <= 2300
    assert result["end_ns"] <= result["start_ns"] + 3000
    samples = [0, *result["samples"], 0]
    assert max(abs(b-a) for a, b in zip(samples, samples[1:])) <= 0.002 + 1e-12
    assert state.metadata["simulation"]["signals"]["netzero"]["areas"][0] == pytest.approx(0, abs=1e-9)


@pytest.mark.parametrize("prepared", [False, True])
def test_netzero_waveform_paths_accumulate_independently_and_outlive_packet(prepared):
    config = netzero_config(slew_rate_per_s=2.5e8)
    config["sequencers"]["s"]["netzero"]["outputs"].append(
        {"output": 1, "path": 1, "enabled": True, "ihp_enabled": False, "amplitude": 0.5, "slew_rate_per_s": 2.5e8})
    state, = run("play 0,1,8\nplay_netzero 100,0\nstop", prepared, physical=config, sequences={"s": {"waveforms": {
        "positive": {"index": 0, "data": [0.5]*8}, "negative": {"index": 1, "data": [-0.25]*8}}}})
    assert status(state) == "completed"
    results = netzero_results(state)
    assert [result["area_before"] for result in results] == [4, -2]
    assert [result["compensation_area"] for result in results] == pytest.approx([-4, 2])
    assert state.metadata["simulation"]["end_time_ns"] > state.rt_packets[-1].rt_t1.value
    dac_traces = [trace for trace in state.metadata["simulation"]["signals"]["traces"] if trace["stage"] == "dac"]
    assert all(abs(sum(trace["samples"])) <= 1 / 32768 for trace in dac_traces)
    if not prepared:
        assert len(events(state, "netzero_complete")) == 2


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize(("period", "cutoff", "planned_end", "samples", "area"), [
    (1, 108, 117, [], 0),
    (1, 112, 117, [-0.25, -0.5, -0.5, -0.5], -1.75),
    (2, 113, 116, [-0.5, -0.5, -0.5], -3),
])
def test_netzero_time_limit_reports_only_observed_samples(prepared, period, cutoff, planned_end, samples, area):
    source = "set_awg_offs 16384,0\nupd_param 8\nset_awg_offs 0,0\nplay_netzero 100,0\nstop"
    physical = netzero_config(slew_rate_per_s=2.5e8)
    physical["sample_period_ns"] = period
    state, = run(source, prepared, physical=physical, startup_delay_ns=100, max_time_ns=cutoff)
    assert status(state) == "step_limit"
    assert state.metadata["execution"]["reason"] == f"Reached simulation time limit {cutoff} ns."
    diagnostic, = state.diagnostics
    assert diagnostic.category == "simulation_time_limit" and diagnostic.source.raw == "play_netzero 100,0"
    assert diagnostic.details["display_message"] == "Reached simulation time limit."
    assert diagnostic.details["time_limit_local_ns"] == cutoff
    result, = netzero_results(state)
    signals = state.metadata["simulation"]["signals"]
    signal_result, = signals["netzero"]["results"]
    observed = {"status": "incomplete", "start_ns": 108, "end_ns": cutoff, "observation_cutoff_ns": cutoff,
                "samples": samples, "compensation_area": area, "sample_period_ns": period,
                "planned_end_ns": planned_end, "planned_compensation_area": -4}
    assert {key: result[key] for key in observed} == observed
    assert {key: signal_result[key] for key in observed} == observed
    assert signals["sampled_end_ns"] == cutoff and signals["netzero"]["areas"][0] == 4 + area
    trace = next(trace for trace in signals["traces"] if trace["stage"] == "netzero" and trace["path"] == "i")
    assert trace["samples"][108 // period:] == samples
    assert not events(state, "netzero_complete")
    if prepared:
        assert not diagnostic.related_events
    else:
        event, = events(state, "netzero_result")
        assert (event.t0, event.t1, event.duration) == (Concrete(108), Concrete(cutoff), Concrete(cutoff - 108))
        assert event.id == result["event_id"]
        assert diagnostic.related_events == [event.id]
        assert {key: event.meta[key] for key in observed} == observed


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("cutoff", [117, 200])
def test_netzero_completes_at_or_before_time_limit_even_after_stop(prepared, cutoff):
    source = "set_awg_offs 16384,0\nupd_param 8\nset_awg_offs 0,0\nplay_netzero 100,0\nstop"
    state, peer = run({"s": source, "peer": "wait 200\nstop"}, prepared,
                      physical=netzero_config(slew_rate_per_s=2.5e8), startup_delay_ns=100, max_time_ns=cutoff)
    assert status(state) == "completed" and status(peer) == "step_limit"
    assert not state.diagnostics
    result, = netzero_results(state)
    signal_result, = state.metadata["simulation"]["signals"]["netzero"]["results"]
    assert state.rt_time_ns == Concrete(112)
    for item in (result, signal_result):
        assert item["status"] == "modeled" and item["end_ns"] == 117
        assert item["compensation_area"] == -4 and len(item["samples"]) == 9
        assert "planned_end_ns" not in item and "observation_cutoff_ns" not in item
    if not prepared:
        complete, = events(state, "netzero_complete")
        event, = events(state, "netzero_result")
        assert event.t1 == complete.t0 == Concrete(117)
        assert complete.meta["source_event_id"] == event.id


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("longer_peer", [False, True])
def test_netzero_failure_uses_local_observation_even_if_the_project_samples_longer(prepared, longer_peer):
    source = "set_awg_offs 16384,0\nupd_param 8\nset_awg_offs 0,0\nplay_netzero 100,0\nstop"
    sources = {"s": source}
    if longer_peer:
        sources["peer"] = "wait 100\nstop"
    physical = netzero_config(slew_rate_per_s=2.5e8)
    states = run(sources, prepared, physical=physical, startup_delay_ns=100, feedback_queue_capacity=1,
                 inputs={"feedback": [{"target": "s", "time_ns": 110, "id": key, "value": key} for key in (1, 2)]})
    state = states[0]
    assert status(state) == "error" and state.diagnostics[-1].category == "feedback_queue_overflow"
    result, = netzero_results(state)
    signal_summary = state.metadata["simulation"]["signals"]
    signal_result, = signal_summary["netzero"]["results"]
    observed = {"status": "incomplete", "start_ns": 108, "end_ns": 110, "observation_cutoff_ns": 110,
                "planned_end_ns": 117, "planned_compensation_area": -4, "compensation_area": -0.75,
                "samples": [-0.25, -0.5]}
    assert {key: result[key] for key in observed} == observed
    assert {key: signal_result[key] for key in observed} == observed
    assert signal_summary["sampled_end_ns"] == (200 if longer_peer else 110)
    assert not events(state, "netzero_complete")
    if longer_peer:
        control, control_peer = run(sources, prepared, physical=physical, startup_delay_ns=100)
        assert status(states[1]) == status(control_peer) == "completed" and states[1].rt_time_ns == Concrete(200)
        control_signals = control.metadata["simulation"]["signals"]
        assert signal_summary["traces"] == control_signals["traces"]
        assert signal_summary["netzero"]["areas"] == control_signals["netzero"]["areas"]
    if not prepared:
        event, = events(state, "netzero_result")
        assert event.t0 == Concrete(108) and event.t1 == Concrete(110)
        assert {key: event.meta[key] for key in observed} == observed


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize(("failure", "completed"), [(117, False), (118, True)])
def test_netzero_completion_requires_its_callback_to_run_before_failure(prepared, failure, completed):
    source = "set_awg_offs 16384,0\nupd_param 8\nset_awg_offs 0,0\nplay_netzero 100,0\nwait 100\nstop"
    state, = run(source, prepared, physical=netzero_config(slew_rate_per_s=2.5e8), startup_delay_ns=100,
                 feedback_queue_capacity=1, inputs={"feedback": [
                     {"target": "s", "time_ns": failure, "id": key, "value": key} for key in (1, 2)
                 ]})
    assert status(state) == "error" and state.rt_time_ns == Concrete(failure)
    result, = netzero_results(state)
    signal_result, = state.metadata["simulation"]["signals"]["netzero"]["results"]
    for item in (result, signal_result):
        assert item["end_ns"] == 117 and item["compensation_area"] == -4 and len(item["samples"]) == 9
        if completed:
            assert item["status"] == "modeled" and "observation_cutoff_ns" not in item
        else:
            assert item["status"] == "incomplete" and item["observation_cutoff_ns"] == item["planned_end_ns"] == 117
    if not prepared:
        event, = events(state, "netzero_result")
        assert event.t1 == Concrete(117) and event.meta["status"] == result["status"]
        assert [item.t0.value for item in events(state, "netzero_complete")] == ([117] if completed else [])


@pytest.mark.parametrize("physical, expected", [(netzero_config(), "zero_area"), ({}, "unmodeled")])
def test_netzero_time_limit_preserves_zero_area_and_unmodeled_results(physical, expected):
    state, = run("play_netzero 100,4\nwait 100\nstop", physical=physical, startup_delay_ns=100, max_time_ns=108)
    result, = netzero_results(state)
    event, = events(state, "netzero_result")
    assert status(state) == "step_limit" and result["status"] == event.meta["status"] == expected
    assert event.t0 == event.t1 == Concrete(100) and "observation_cutoff_ns" not in result
    assert not events(state, "netzero_complete")


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("output,expected", [({"enabled": False}, "disabled"), ({}, "modeled")])
def test_netzero_snapshots_maxt_and_disabled_output(prepared, output, expected):
    source = "move 100,R0\nnop\nset_awg_offs 16384,0\nupd_param 4\nset_awg_offs 0,0\nplay_netzero R0,4\nmove 0,R0\nwait 100\nstop"
    state, = run(source, prepared, physical=netzero_config(slew_rate_per_s=2.5e8, **output))
    result, = netzero_results(state)
    assert result["status"] == expected
    assert result["maxt_ns"] == 100
    assert state.rt_packets[1].duration == Concrete(4)


@pytest.mark.parametrize("prepared", [False, True])
def test_netzero_reset_survives_wait_is_consumed_once_and_needs_ihp(prepared):
    source = ("set_awg_offs 16384,0\nupd_param 8\nreset_netzero\nwait 4\n"
              "set_awg_offs 0,0\nupd_param 4\nplay_netzero 100,4\nstop")
    state, = run(source, prepared, physical=netzero_config())
    assert [result["status"] for result in netzero_results(state)] == ["reset", "zero_area"]
    assert [packet.meta.get("captured_netzero_reset", False) for packet in state.rt_packets] == [False, False, True, False]
    state, = run(source, prepared, physical=netzero_config(ihp_enabled=False, slew_rate_per_s=2.5e8))
    result, = netzero_results(state)
    assert result["status"] == "modeled" and result["area_before"] == 6


@pytest.mark.parametrize("prepared", [False, True])
def test_skipped_netzero_reset_is_discarded(prepared):
    source = ("set_awg_offs 16384,0\nupd_param 8\nreset_netzero\nset_cond 1,1,0,4\n"
              "play_netzero 100,4\nset_cond 0,1,0,4\nset_awg_offs 0,0\nplay_netzero 100,4\nstop")
    state, = run(source, prepared, physical=netzero_config(slew_rate_per_s=2.5e8))
    result, = netzero_results(state)
    assert result["status"] == "modeled" and result["area_before"] == 6
    assert not state.rt_packets[-1].meta["captured_netzero_reset"]


@pytest.mark.parametrize("prepared", [False, True])
def test_netzero_missing_model_and_insufficient_capacity_are_explicit(prepared):
    state, = run("play_netzero 100,4\nplay 0,1,4\nstop", prepared)
    assert netzero_results(state)[0]["status"] == "unmodeled"
    assert any(item.category == "possible_netzero_overlap" for item in state.diagnostics)
    state, = run("set_awg_offs 16384,0\nupd_param 100\nset_awg_offs 0,0\nplay_netzero 4,4\nstop",
                 prepared, physical=netzero_config())
    assert netzero_results(state)[0]["status"] == "insufficient_capacity"


@pytest.mark.parametrize("prepared", [False, True])
def test_netzero_reset_does_not_reset_generic_iir(prepared):
    config = netzero_config()
    config["sequencers"]["s"]["iir"] = {"b": [0.5], "a": [1, -0.5]}
    state, = run("set_awg_offs 16384,0\nupd_param 4\nreset_netzero\nset_awg_offs 0,0\nupd_param 4\nstop",
                 prepared, startup_delay_ns=100, physical=config)
    trace = next(trace for trace in state.metadata["simulation"]["signals"]["traces"] if trace["stage"] == "filtered" and trace["path"] == "i")
    assert trace["samples"][104] == 0.234375
    assert [result["status"] for result in netzero_results(state)] == ["reset"]


@pytest.mark.parametrize("prepared", [False, True])
def test_reset_during_active_compensation_does_not_invent_hardware_behavior(prepared):
    state, = run("set_awg_offs 16384,0\nupd_param 100\nset_awg_offs 0,0\nplay_netzero 1000,4\nreset_netzero\nupd_param 4\nstop",
                 prepared, physical=netzero_config())
    assert status(state) == "error"
    assert "Reset during active netzero compensation requires" in state.metadata["execution"]["reason"]


DIGITAL_MEMORY = {"s": {"acquisitions": {"counts": {"index": 0, "num_bins": 8}}}}


def digital_trace(*rising_ns):
    return {"s": {"initial_level": 0, "transitions": [
        {"time_ticks": int(time * 128) + offset, "level": level}
        for time in rising_ns for offset, level in ((0, 1), (1, 0))]}}


def acquisitions(state):
    return state.metadata["simulation"]["acquisitions"]


@pytest.mark.parametrize("prepared", [False, True])
def test_timetag_window_counts_half_open_edges_and_addresses_close_bin(prepared):
    state, = run("acquire_timetags 0,0,1,0,20\nacquire_timetags 0,3,0,0,4\nstop", prepared,
                 startup_delay_ns=100, sequences=DIGITAL_MEMORY, inputs={"digital": digital_trace(104, 116, 120)})
    result, = acquisitions(state)
    assert status(state) == "completed"
    assert (result["count"], result["timetag_ticks"], result["valid"], result["bin_index"]) == (2, 512, True, 3)
    assert (result["start_ticks"], result["end_ticks"]) == (12800, 15360)
    assert result["edge_times_ticks"] == [13312, 14848]
    assert result["rt_packet_id"] == state.rt_packets[1].id
    assert len(result["input_event_ids"]) == 2


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("trace,expected_count", [(digital_trace(), 0), (None, None)])
def test_known_empty_digital_input_differs_from_missing(prepared, trace, expected_count):
    state, = run("acquire_timetags 0,0,1,0,20\nacquire_timetags 0,0,0,0,4\nstop", prepared,
                 startup_delay_ns=100, sequences=DIGITAL_MEMORY, inputs={"digital": trace} if trace else {})
    result, = acquisitions(state)
    assert result.get("count") == expected_count
    assert result["valid"] is False
    assert result["status"] == ("modeled" if trace else "unavailable")
    if trace is None:
        assert "No digital input" in result["reason"]


@pytest.mark.parametrize("prepared", [False, True])
def test_time_reference_is_one_shot_and_may_apply_on_close(prepared):
    for prefix, before_close, reference, tag in (("set_time_ref\nupd_param 4\n", "", 96, 8),
                                                ("wait 4\n", "set_time_ref\n", 120, -16)):
        state, = run(prefix + "acquire_timetags 0,0,1,0,20\n" + before_close + "acquire_timetags 0,1,0,0,4\nstop",
                     prepared, startup_delay_ns=96, sequences=DIGITAL_MEMORY,
                     inputs={"digital": digital_trace(104, 116)}, digital={"s": {"time_reference": "sequencer"}})
        result, = acquisitions(state)
        assert result["reference_ticks"] == reference * 128
        assert result["timetag_ticks"] == tag * 128
        assert sum(bool(packet.meta.get("captured_time_ref")) for packet in state.rt_packets) == 1


@pytest.mark.parametrize("prepared", [False, True])
def test_skipped_close_keeps_window_open_and_pending_reference_is_discarded(prepared):
    source = ("acquire_timetags 0,0,1,0,20\nset_time_ref\nset_cond 1,1,0,4\n"
              "acquire_timetags 0,1,0,0,4\nset_cond 0,1,0,4\nacquire_timetags 0,2,0,0,4\nstop")
    state, = run(source, prepared, startup_delay_ns=100, sequences=DIGITAL_MEMORY,
                 inputs={"digital": digital_trace(104, 121)}, digital={"s": {"time_reference": "sequencer"}})
    result, = acquisitions(state)
    assert result["count"] == 2 and result["bin_index"] == 2
    assert result["reference_ticks"] is None and not result["valid"]
    state, = run("acquire_timetags 0,0,1,0,20\nset_cond 1,1,0,4\nacquire_timetags 0,0,0,0,4\nstop",
                 prepared, startup_delay_ns=100, sequences=DIGITAL_MEMORY, inputs={"digital": digital_trace(104)})
    assert status(state) == "blocked"
    assert acquisitions(state)[0]["status"] == "incomplete"


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("initial", [0, 1])
def test_digital_samples_initial_level_at_rt_start_independent_of_wait(prepared, initial):
    state, = run("acquire_digital 0,2,20\nstop", prepared, startup_delay_ns=100, sequences=DIGITAL_MEMORY,
                 inputs={"digital": {"s": {"initial_level": initial, "transitions": [{"time_ticks": 101*128, "level": 1-initial}]}}})
    result, = acquisitions(state)
    assert result["level"] == initial and result["sample_ticks"] == 12800
    assert result["sampling"] == "ideal_at_rt_start"
    assert state.rt_time_ns == Concrete(120)


@pytest.mark.parametrize("prepared", [False, True])
def test_exact_ticks_and_open_close_and_sampling_ties(prepared):
    state, = run("acquire_digital 0,0,0\nacquire_timetags 0,0,1,0,4\nacquire_timetags 0,1,0,0,4\nstop", prepared,
                 startup_delay_ns=100, sequences=DIGITAL_MEMORY, inputs={"digital": digital_trace(100, 104, 105+1/128, 108)})
    sample, window = acquisitions(state)
    assert sample["level"] == 1  # Ideal sampling observes the transition at the same timestamp.
    assert window["edge_times_ticks"] == [104*128, 105*128+1]
    assert window["count"] == 2 and window["timetag_ticks"] == 0


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("increment", [False, True])
def test_ttl_gates_span_waits_and_use_enable_bin(prepared, increment):
    state, = run("acquire_ttl 0,2,1,4\nwait 8\nwait 8\nacquire_ttl 0,7,0,4\nstop", prepared,
                 startup_delay_ns=100, sequences=DIGITAL_MEMORY, inputs={"digital": digital_trace(104, 116, 120)},
                 digital={"s": {"ttl_acq_auto_bin_incr_en": increment}})
    assert status(state) == "completed"
    assert [(result["bin_index"], result["count"]) for result in acquisitions(state)] == ([(2, 1), (3, 1)] if increment else [(2, 2)])


@pytest.mark.parametrize("prepared", [False, True])
def test_ttl_unclosed_and_overflow_are_explicit(prepared):
    state, = run("acquire_ttl 0,0,1,20\nstop", prepared, startup_delay_ns=100,
                 sequences=DIGITAL_MEMORY, inputs={"digital": digital_trace(104)})
    assert status(state) == "blocked" and acquisitions(state)[0]["status"] == "incomplete"
    state, = run("acquire_ttl 0,7,1,20\nacquire_ttl 0,0,0,4\nstop", prepared, startup_delay_ns=100,
                 sequences=DIGITAL_MEMORY, inputs={"digital": digital_trace(104, 116)},
                 digital={"s": {"ttl_acq_auto_bin_incr_en": True}})
    assert status(state) == "error" and "out of bounds" in state.metadata["execution"]["reason"]


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("increment", [False, True])
def test_ttl_bin_snapshots_accumulate_at_each_gate_close(prepared, increment):
    source = "acquire_ttl 0,2,1,20\nacquire_ttl 0,7,0,4\n" * 2 + "acquire_ttl 0,4,1,20\nacquire_ttl 0,7,0,4\nstop"
    state, = run(source, prepared, startup_delay_ns=100, sequences=DIGITAL_MEMORY,
                 digital={"s": {"ttl_acq_auto_bin_incr_en": increment}},
                 inputs={"digital": digital_trace(104, 116, 128)})
    expected = ([(120, 2, 1, 1), (120, 3, 1, 1), (144, 2, 1, 2), (168, 4, 0, 0)] if increment
                else [(120, 2, 2, 2), (144, 2, 1, 3), (168, 4, 0, 0)])
    results = acquisitions(state)
    assert [(result["time_ns"], result["bin_index"], result["count"], result["bin_aggregate"]["total_count"])
            for result in results] == expected
    assert all(result["bin_aggregate"]["kind"] == "ttl" for result in results)
    bins = state.metadata["simulation"]["digital_bins"]
    assert {item["bin_index"]: item["count"] for item in bins} == ({2: 2, 3: 1, 4: 0} if increment else {2: 3, 4: 0})
    if not prepared:
        assert [(event.t0.value, event.meta["bin_aggregate"]) for event in events(state, "acquisition_result")] == [
            (result["time_ns"], result["bin_aggregate"]) for result in results
        ]


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("close_gate", [False, True])
def test_incomplete_or_unavailable_ttl_gate_keeps_previous_aggregate(prepared, close_gate):
    opened, closed = "acquire_ttl 0,2,1,20\n", "acquire_ttl 0,7,0,4\n"
    source = opened + closed + opened + (closed if close_gate else "") + "stop"
    supplied = {"sequencer": "s", "kind": "acquire_ttl", "acquisition_index": 0, "bin_index": 2, "count": 2}
    state, = run(source, prepared, startup_delay_ns=100, sequences=DIGITAL_MEMORY,
                 inputs={"digital_acquisitions": [supplied]})
    first, second = acquisitions(state)
    assert (first["status"], first["count"], first["bin_aggregate"]) == ("supplied", 2, {"kind": "ttl", "total_count": 2})
    assert second["status"] == ("unavailable" if close_gate else "incomplete") and "bin_aggregate" not in second
    bin_result, = state.metadata["simulation"]["digital_bins"]
    assert (bin_result["bin_index"], bin_result["count"], bin_result["event_id"]) == (2, 2, first["event_id"])
    if not prepared:
        _, outcome = events(state, "acquisition_result")
        assert outcome.t0.value == second["time_ns"] == 144 and "bin_aggregate" not in outcome.meta


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("count,expected", [(0, "low"), (1, "mid"), (2, "mid"), (3, "high"), (4, "high")])
def test_count_classification_boundaries_and_rt_threshold_updates(prepared, count, expected):
    source = ("acquire_timetags 0,0,1,0,20\nacquire_timetags 0,1,0,0,4\n"
              "upd_thres 0,0,4\nupd_thres 1,1,4\nacquire_timetags 0,0,1,0,20\nacquire_timetags 0,2,0,0,4\nstop")
    edges = [104+2*i for i in range(count)] + [136+2*i for i in range(count)]
    state, = run(source, prepared, startup_delay_ns=100, sequences=DIGITAL_MEMORY,
                 inputs={"digital": digital_trace(*edges)}, digital={"s": {"count_thresholds": {"low": 1, "high": 3}}},
                 trigger_routes=[{"source": "s", "target": "s", "address": 1, "latency_ns": 0}])
    first, second = acquisitions(state)
    assert first["count"] == second["count"] == count
    assert first["classification"] == expected
    assert second["classification"] == ("mid" if count == 0 else "high")
    assert not events(state, "trigger_receive")


@pytest.mark.parametrize("prepared", [False, True])
def test_supplied_digital_result_is_separate_from_iq_and_consumed_once(prepared):
    source = "acquire_timetags 0,0,1,0,20\nacquire_timetags 0,0,0,0,4\n" * 2 + "stop"
    state, = run(source, prepared, sequences=DIGITAL_MEMORY, inputs={
        "acquisitions": [{"sequencer": "s", "acquisition_index": 0, "bin_index": 0, "i": 4, "q": 2}],
        "digital_acquisitions": [{"sequencer": "s", "kind": "acquire_timetags", "acquisition_index": 0,
                                   "bin_index": 0, "count": 2, "timetag_ticks": 512}]})
    first, second = acquisitions(state)
    assert (first["count"], first["timetag_ticks"], first["status"]) == (2, 512, "supplied")
    assert second["status"] == "unavailable" and "count" not in second


@pytest.mark.parametrize("transitions", [[{"time_ticks": 1, "level": 1}, {"time_ticks": 1, "level": 0}],
                                         [{"time_ticks": 0.5, "level": 1}], [{"time_ticks": 1, "level": 2}],
                                         [{"time_ticks": 1, "level": 0}]])
def test_invalid_digital_trace_is_rejected(transitions):
    with pytest.raises(ValueError, match="simulation.inputs.digital.s"):
        run("stop", inputs={"digital": {"s": {"initial_level": 0, "transitions": transitions}}})


@pytest.mark.parametrize("source,reason", [("acquire_timetags 0,0,1,1024,4\nacquire_timetags 0,0,0,0,4\nstop", "effective boundaries"),
                                          ("upd_thres 2,1,4\nstop", "threshold index")])
def test_unmodeled_digital_settings_block_explicitly(source, reason):
    state, = run(source, sequences=DIGITAL_MEMORY)
    assert status(state) == "blocked" and reason in state.metadata["execution"]["reason"]


@pytest.mark.parametrize("prepared", [False, True])
def test_incomplete_gate_ends_at_own_stop_and_keeps_its_known_count(prepared):
    state, peer = run({"s": "acquire_ttl 0,0,1,20\nstop", "peer": "wait 1000\nstop"}, prepared,
                      startup_delay_ns=100, sequences=DIGITAL_MEMORY, inputs={"digital": digital_trace(104, 140)})
    result, = acquisitions(state)
    assert result["end_ticks"] == 120*128 and result["time_ns"] == 120
    assert result["count"] == 1 and status(peer) == "completed"


def tb_scenario(prepared=False, *, combine=1, shift=2, length=1, latency=7, receiver="fb_pop_data 16,R0", second=True, capacity=32):
    sources = {"a": f"fb_acq_tb_id 16,4\nfb_acq_tb_cfg {combine},0,1,4\nacquire 0,0,20\nstop",
               "r": f"wait 0\n{receiver}\nnop\nstop"}
    if second:
        sources["b"] = f"fb_acq_tb_id 16,4\nfb_acq_tb_cfg {combine},{shift},{length},4\nacquire 0,0,20\nstop"
    names = [name for name in sources if name != "r"]
    return run(sources, prepared, startup_delay_ns=100, feedback_queue_capacity=capacity,
               routes=[{"source": name, "target": "r", "id": 16, "latency_ns": latency} for name in names],
               inputs={"acquisitions": [{"sequencer": name, "acquisition_index": 0, "bin_index": 0, "thresholded": int(name == "b")} for name in names]})


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("receiver", ["fb_pop_data 16,R0", "fb_pull_data R1,R0"])
def test_tb_write_combine_emits_one_word_with_all_provenance(prepared, receiver):
    states = tb_scenario(prepared, receiver=receiver, capacity=1)
    assert all(status(state) == "completed" for state in states)
    by_name = {state.sequencer_id: state for state in states}
    assert by_name["r"].registers["R0"] == Concrete(0x0E)
    if "pull" in receiver:
        assert by_name["r"].registers["R1"] == Concrete(16)
    if not prepared:
        received, = events(by_name["r"], "feedback_receive")
        assert received.t0 == Concrete(135)
        assert received.meta["value"] == 14 and len(received.meta["contributors"]) == 2
        assert {item["sequencer"] for item in received.meta["contributors"]} == {"a", "b"}
        assert all(item["rt_packet_id"] and item["acquisition_event_id"] for item in received.meta["contributors"])


@pytest.mark.parametrize("prepared", [False, True])
def test_tb_plain_and_missing_contributors_do_not_fabricate_bits(prepared):
    states = tb_scenario(prepared, combine=0, receiver="fb_pull_data R2,R0\nfb_pop_data 16,R1")
    receiver = next(state for state in states if state.sequencer_id == "r")
    assert status(receiver) == "completed"
    assert (receiver.registers["R0"], receiver.registers["R1"]) == (Concrete(2), Concrete(3))
    states = tb_scenario(prepared, second=False)
    receiver = next(state for state in states if state.sequencer_id == "r")
    assert receiver.registers["R0"] == Concrete(2)  # Missing contributor has no valid bit.


@pytest.mark.parametrize("prepared", [False, True])
def test_uncombined_contributors_use_two_fifo_slots(prepared):
    states = tb_scenario(prepared, combine=0, capacity=1)
    receiver = next(state for state in states if state.sequencer_id == "r")
    assert status(receiver) == "error"
    assert any(item.category == "feedback_queue_overflow" for item in receiver.diagnostics)


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("options", [{"shift": 0}, {"length": 2}])
def test_tb_combine_rejects_collision_and_mismatched_widths(prepared, options):
    states = tb_scenario(prepared, **options)
    assert all(status(state) == "error" for state in states)
    assert all("colliding bit positions, incompatible widths" in state.metadata["execution"]["reason"] for state in states)


@pytest.mark.parametrize("prepared", [False, True])
def test_tb_configuration_is_snapshotted_at_acquisition_and_skips_leave_it_unchanged(prepared):
    source = ("fb_acq_tb_id 16,4\nfb_acq_tb_cfg 1,2,1,4\nacquire 0,0,4\n"
              "fb_acq_tb_id 0,4\nfb_acq_tb_cfg 0,0,0,4\nwait 20\nstop")
    states = run({"s": source, "r": "wait 0\nfb_pop_data 16,R0\nnop\nstop"}, prepared, startup_delay_ns=100,
                 physical={"sequencers": {"s": {"integration_length_ns": 20}}},
                 inputs={"acquisitions": [{"sequencer": "s", "acquisition_index": 0, "bin_index": 0, "thresholded": 1}]},
                 routes=[{"source": "s", "target": "r", "latency_ns": 0}])
    assert all(status(state) == "completed" for state in states)
    assert states[1].registers["R0"] == Concrete(12)
    source = ("fb_acq_tb_id 16,4\nset_cond 1,1,0,4\nfb_acq_tb_cfg 1,2,1,4\n"
              "set_cond 0,1,0,4\nacquire 0,0,20\nstop")
    states = run({"s": source, "r": "wait 0\nfb_pop_data 16,R0\nnop\nstop"}, prepared, startup_delay_ns=100,
                 inputs={"acquisitions": [{"sequencer": "s", "acquisition_index": 0, "bin_index": 0, "thresholded": 1}]},
                 routes=[{"source": "s", "target": "r", "latency_ns": 0}])
    assert states[1].registers["R0"] == Concrete(3)


@pytest.mark.parametrize("source,reason", [
    ("fb_com_cfg 1,0,4,4", "reference encoding"),
    ("fb_acq_tb_cfg 1,0,5,4", "one word"),
    ("fb_com_extra 1,42,4", "extra bytes"), ("fb_acq_tb_extra 1,42,4", "extra bytes"),
    ("fb_acq_tb_mock 1,1,1,4", "duration versus count"),
])
def test_unverified_linq_encodings_are_blocked(source, reason):
    state, = run(source + "\nstop")
    assert status(state) == "blocked" and reason in state.metadata["execution"]["reason"]


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("stream,expected", [("fb_llp_tags_id", [104, 116]), ("fb_tdc_tags_id", [13312, 14848])])
def test_qtm_streams_send_two_tags_and_one_count_with_latency(prepared, stream, expected):
    source = f"{stream} 16,4\nfb_llp_ttls_id 17,4\nacquire_timetags 0,0,1,0,20\nacquire_timetags 0,2,0,0,4\nstop"
    receiver = "wait 0\nfb_pop_data 16,R0\nfb_pull_data R3,R1\nfb_pop_data 17,R2\nnop\nstop"
    states = run({"s": source, "r": receiver}, prepared, startup_delay_ns=92, sequences=DIGITAL_MEMORY,
                 inputs={"digital": digital_trace(104, 116, 120)},
                 digital={"s": {"linq_timestamp_origin_ticks": 0}}, routes=[{"source": "s", "target": "r", "latency_ns": 7}])
    sender, receiver = states
    assert all(status(state) == "completed" for state in states)
    assert [receiver.registers[f"R{i}"].value for i in range(3)] == [*expected, 2]
    assert receiver.registers["R3"] == Concrete(16)
    if not prepared:
        sends = events(sender, "feedback_send")
        assert [event.t0.value for event in sends] == [104, 116, 120]
        assert [event.t0.value for event in events(receiver, "feedback_receive")] == [111, 123, 127]
        assert all(event.meta["source_event_id"] and event.meta["source_packet_id"] for event in sends)


@pytest.mark.parametrize("prepared", [False, True])
def test_qtm_disabled_ids_do_not_send(prepared):
    source = ("fb_tdc_tdelta_id 0,4\nfb_llp_tags_id 0,4\nacquire_timetags 0,0,1,0,20\n"
              "acquire_timetags 0,1,0,0,4\nstop")
    state, = run(source, prepared, startup_delay_ns=92, sequences=DIGITAL_MEMORY, inputs={"digital": digital_trace(104)})
    assert status(state) == "completed" and not events(state, "feedback_send")
    assert acquisitions(state)[0]["timetag_ticks"] == 512


@pytest.mark.parametrize("prepared", [False, True])
def test_qtm_published_llp_absolute_clock_vector(prepared):
    # Official 424_linq_qtm outputs 1325917566 and 1325917666 ns.
    source = "fb_llp_tags_id 16,4\nacquire_timetags 0,0,1,0,120\nacquire_timetags 0,0,0,0,4\nstop"
    states = run({"s": source, "r": "wait 0\nfb_pop_data 16,R0\nfb_pull_data R2,R1\nnop\nstop"}, prepared,
                 startup_delay_ns=96, sequences=DIGITAL_MEMORY, inputs={"digital": digital_trace(104, 204)},
                 digital={"s": {"linq_timestamp_origin_ticks": (1325917566-104)*128}},
                 routes=[{"source": "s", "target": "r", "latency_ns": 7}])
    assert [states[1].registers[f"R{i}"].value for i in (0, 1)] == [1325917566, 1325917666]


@pytest.mark.parametrize("prepared", [False, True])
def test_qtm_published_tdc_word_units_and_signed_clock(prepared):
    # 424_linq_qtm prints -7243781.6953 ns, i.e. -927204057 ticks to its precision.
    # The receiver stores that signed 32-bit value as the raw word 3367763239.
    source = "fb_tdc_tags_id 16,4\nacquire_timetags 0,0,1,0,20\nacquire_timetags 0,0,0,0,4\nstop"
    states = run({"s": source, "r": "wait 0\nfb_pop_data 16,R0\nnop\nstop"}, prepared,
                 startup_delay_ns=96, sequences=DIGITAL_MEMORY, inputs={"digital": digital_trace(104)},
                 digital={"s": {"linq_timestamp_origin_ticks": -927217369}},
                 routes=[{"source": "s", "target": "r", "latency_ns": 7}])
    assert states[1].registers["R0"] == Concrete(3367763239)


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("count", [0, 200])
def test_qtm_count_word_for_empty_and_reference_200_pulses(prepared, count):
    source = "fb_llp_ttls_id 16,4\nacquire_timetags 0,0,1,0,25000\nacquire_timetags 0,0,0,0,4\nstop"
    states = run({"s": source, "r": "wait 0\nfb_pull_data R1,R0\nnop\nstop"}, prepared,
                 startup_delay_ns=96, sequences=DIGITAL_MEMORY,
                 inputs={"digital": digital_trace(*(104+116*i for i in range(count)))},
                 routes=[{"source": "s", "target": "r", "latency_ns": 7}])
    assert states[1].registers["R0"] == Concrete(count)


@pytest.mark.parametrize("prepared", [False, True])
def test_tb_different_completion_times_are_separate_fifo_entries(prepared):
    sources = {"a": "fb_acq_tb_id 16,4\nfb_acq_tb_cfg 1,0,1,4\nacquire 0,0,20\nstop",
               "b": "fb_acq_tb_id 16,4\nfb_acq_tb_cfg 1,2,1,4\nacquire 0,0,24\nstop",
               "r": "wait 0\nfb_pop_data 16,R0\nfb_pull_data R2,R1\nnop\nstop"}
    states = run(sources, prepared, startup_delay_ns=100,
                 inputs={"acquisitions": [{"sequencer": name, "acquisition_index": 0, "bin_index": 0,
                                           "thresholded": int(name == "b")} for name in ("a", "b")]},
                 routes=[{"source": name, "target": "r", "latency_ns": 7} for name in ("a", "b")])
    assert all(status(state) == "completed" for state in states)
    assert [states[2].registers[f"R{i}"].value for i in (0, 1)] == [2, 12]


@pytest.mark.parametrize("prepared", [False, True])
def test_disabled_extras_preserve_common_data_and_tb_streams(prepared):
    source = ("fb_com_cfg 0,0,0,0\nfb_com_extra 0,65535,0\nfb_acq_tb_extra 0,65535,0\n"
              "fb_com_data 16,42,4\nfb_acq_tb_id 17,4\nacquire 0,0,20\nstop")
    states = run({"s": source, "r": "wait 0\nfb_pop_data 16,R0\nfb_pull_data R2,R1\nnop\nstop"}, prepared,
                 startup_delay_ns=100,
                 inputs={"acquisitions": [{"sequencer": "s", "acquisition_index": 0, "bin_index": 0, "thresholded": 1}]},
                 routes=[{"source": "s", "target": "r", "latency_ns": 7}])
    assert all(status(state) == "completed" for state in states)
    assert [states[1].registers[f"R{i}"].value for i in (0, 1, 2)] == [42, 3, 17]


def test_fractional_digital_events_keep_shared_clock_and_display_alignment_in_ir():
    ir = analyze_documents({"s.q1asm": "acquire_timetags 0,0,1,0,20\nacquire_timetags 0,1,0,0,4\nstop"},
                           execution_mode="simulation", alignment_mode="first_anchor", alignment_anchor_kinds=["rt_queue_dequeue"],
                           sequences=DIGITAL_MEMORY, simulation={"startup_delay_ns": 100,
                               "inputs": {"digital": digital_trace(104+1/128)}})
    assert ir["execution"]["s"]["status"] == "completed"
    edge, = [event for event in ir["events"] if event["kind"] == "digital_edge"]
    assert edge["t0"]["value"] == 104+1/128
    assert edge["meta"]["aligned_t0"] == 4+1/128
    result, = ir["simulation"]["s"]["acquisitions"]
    assert result["timetag_ticks"] == 513 and result["start_ticks"] == 12800
    assert result["source_event_id"] in {event["id"] for event in ir["events"]}
    json.dumps(ir, allow_nan=False)


def test_combined_feedback_ir_keeps_entry_and_contributor_provenance():
    from q1timeline.ir.serialize import timeline_ir_from_states

    ir = timeline_ir_from_states(tb_scenario())
    flow, = ir["feedback_flows"]
    by_id = {event["id"]: event for event in ir["events"]}
    received = by_id[flow["to_event_id"]]
    assert received["meta"]["value"] == 14
    assert by_id[flow["from_event_id"]]["kind"] == "feedback_send"
    for contributor in received["meta"]["contributors"]:
        assert by_id[contributor["source_event_id"]]["kind"] == "feedback_contribution"
        assert by_id[contributor["acquisition_event_id"]]["kind"] == "acquisition_result"
    json.dumps(ir, allow_nan=False)


@pytest.mark.parametrize("origin,edge,reason", [(None, 104, "origin"), (0, 104+1/128, "quantization")])
def test_qtm_unknown_origin_and_fractional_scheduling_remain_explicit(origin, edge, reason):
    settings = {} if origin is None else {"linq_timestamp_origin_ticks": origin}
    state, = run("fb_tdc_tags_id 16,4\nacquire_timetags 0,0,1,0,20\nacquire_timetags 0,0,0,0,4\nstop",
                 startup_delay_ns=96, sequences=DIGITAL_MEMORY, inputs={"digital": digital_trace(edge)}, digital={"s": settings})
    assert status(state) == "blocked" and reason in state.metadata["execution"]["reason"]
