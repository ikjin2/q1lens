import json
from argparse import Namespace
from dataclasses import replace

import pytest

from q1timeline.analysis.interpreter import interpret_program, prepare_def_program
from q1timeline.cli import main
from q1timeline.def_runs import RunSnapshot
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.playback import PlaybackSession
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.config import validate_simulation
from q1timeline.simulation.runtime import _same_comparison_prefix_event, simulate_programs
from q1timeline.watch.watcher import run_watch_once


_QRM_HARDWARE = {"integration_length_acq": 200, "scope_acq_sequencer_select": 2,
                 "scope_acq_trigger_mode_path0": "sequencer", "scope_acq_trigger_mode_path1": "level"}


def _run(sources, *, hardware=None, identity=None, prepared=False, **options):
    programs = {name: parse_q1asm(text, file=f"{name}.q1asm") for name, text in sources.items()}
    memory = {name: {"acquisitions": {"a": {"index": 0, "num_bins": 4}}} for name in sources}
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    states = simulate_programs(programs, sequences=memory, hardware_parameters=hardware, sequencer_metadata=identity,
                               prepared_programs=bindings, simulation={"startup_delay_ns": 100, **options})
    return {state.sequencer_id: state for state in states}


def _trace(*rising_ticks):
    return {"initial_level": 0, "transitions": [
        {"time_ticks": tick + offset, "level": level}
        for tick in rising_ticks for offset, level in ((0, 1), (1, 0))
    ]}


def _qrm(state):
    return state.metadata["simulation"]["qrm_scope"]


def _qtm(state):
    return state.metadata["simulation"]["qtm_scope"]


@pytest.mark.parametrize("module_type", ["QRM", "QRM-RF"])
@pytest.mark.parametrize("prepared", [False, True])
def test_qrm_scope_uses_fixed_raw_adc_window_separate_from_integration_and_rt(module_type, prepared):
    # Qblox documents 16384 samples at 1 GS/s independently of the binned timing.
    states = _run({"rx": "acquire 0,0,4\nstop"}, prepared=prepared, hardware={"rx": _QRM_HARDWARE},
                  identity={"rx": {"module": "R0", "module_type": module_type, "sequencer_index": 2}},
                  physical={"max_export_samples": 10000, "sequencers": {"rx": {
                      "input": {"kind": "constant", "i": 0.5, "q": -0.25}, "nco_frequency_hz": 250000000}}})
    rx = states["rx"]
    assert rx.metadata["execution"]["status"] == "completed"
    assert rx.rt_time_ns.value == 104
    result, = rx.metadata["simulation"]["acquisitions"]
    assert result["duration_ns"] == 200 and result["time_ns"] == 300
    capture, = _qrm(rx)["captures"]
    assert capture["window"] == {"start_ns": 100, "end_ns": 16484, "configured_duration_ns": 16384,
                                 "effective_duration_ns": 16384, "status": "completed", "paths": [0],
                                 "source": "hardware_parameters"}
    assert capture["sampled_count"] == 16384 and capture["sample_period_ns"] == 1 and capture["export_stride"] == 2
    assert _qrm(rx)["buffer"]["samples"] == {0: [0.5] * 8192}
    ir = timeline_ir_from_states([rx])
    event, = [event for event in ir["events"] if event["kind"] == "acquire"]
    assert event["meta"]["scope_acquisition_window"] == capture["window"]
    assert event["duration"]["value"] == 4


def test_qrm_scope_retrigger_marks_completion_before_overwrite_and_shares_module_settings():
    sources = {"rx": "acquire 0,0,4\nacquire 0,1,16384\nacquire 0,2,4\nstop", "peer": "acquire 0,0,4\nstop"}
    hardware = {"peer": {**_QRM_HARDWARE, "scope_acq_trigger_mode_path1": "sequencer"}}
    identity = {name: {"module": "R0", "module_type": "QRM", "sequencer_index": index}
                for name, index in (("rx", 2), ("peer", 1))}
    results = [{"sequencer": name, "acquisition_index": 0, "bin_index": bin_index, "i": 1}
               for name, bin_index in (("rx", 0), ("rx", 1), ("rx", 2), ("peer", 0))]
    states = _run(sources, hardware=hardware, identity=identity, inputs={"acquisitions": results})
    first, second, third = _qrm(states["rx"])["captures"]
    assert first["window"]["paths"] == [0, 1]
    assert first["window"]["status"] == "retriggered" and first["window"]["end_ns"] == 104
    assert first["completed_before_overwrite"] is False and first["overwritten_by_event_id"] == second["acquire_event_id"]
    assert second["window"]["status"] == "completed" and second["window"]["end_ns"] == 16488
    assert second["completed_before_overwrite"] is True and second["overwritten_by_event_id"] == third["acquire_event_id"]
    assert third["window"]["status"] == "completed" and third["sample_status"] == "unavailable"
    assert _qrm(states["rx"])["buffer"] is None
    assert "qrm_scope" not in states["peer"].metadata["simulation"]


@pytest.mark.parametrize("prepared", [False, True])
def test_failed_qrm_scope_reports_the_observed_duration_without_a_buffer(prepared):
    states = _run({"rx": "acquire 0,0,40\nillegal", "peer": "wait 200\nstop"}, prepared=prepared, startup_delay_ns=4,
                  hardware={"rx": {**_QRM_HARDWARE, "scope_acq_sequencer_select": 0}},
                  identity={"rx": {"module_type": "QRM", "sequencer_index": 0}},
                  physical={"sequencers": {"rx": {"input": {"kind": "constant", "i": 0.5, "q": 0.0}}}})
    rx, peer = states["rx"], states["peer"]
    assert rx.metadata["execution"]["fault"]["q1_time_ns"] == 8
    assert [diagnostic.category for diagnostic in rx.diagnostics] == ["illegal_instruction"]
    assert peer.metadata["execution"]["status"] == "completed" and peer.rt_time_ns.value == 204
    scope = _qrm(rx)
    capture, = scope["captures"]
    assert capture["window"] == {"start_ns": 4, "end_ns": 8, "configured_duration_ns": 16384,
                                 "effective_duration_ns": 4, "status": "incomplete", "paths": [0],
                                 "source": "hardware_parameters"}
    assert capture["sample_status"] == "unavailable" and scope["buffer"] is None
    if not prepared:
        event, = [event for event in rx.events if event.kind == "acquire"]
        assert event.meta["scope_acquisition_window"] == capture["window"]


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("work", ["integration", "scope", "integration_with_active_rt"])
def test_qrm_time_limit_links_deferred_acquire_unless_rt_is_active(prepared, work):
    scope = work == "scope"
    active_rt = work == "integration_with_active_rt"
    source = "acquire 0,0,8\n" + ("wait 100\n" if active_rt else "") + "stop"
    identity = {"rx": {"module_type": "QRM", "sequencer_index": 2}} if scope else None
    hardware = {"rx": {**_QRM_HARDWARE, "integration_length_acq": 4 if scope else 100}}
    inputs = {"acquisitions": [{"sequencer": "rx", "acquisition_index": 0, "bin_index": 0, "i": 1}]} if scope else {}
    state = _run({"rx": source}, prepared=prepared, identity=identity, hardware=hardware, inputs=inputs, max_time_ns=112)["rx"]
    acquire, = [event for event in state.events if event.kind == "acquire"]
    window = acquire.meta["scope_acquisition_window" if scope else "acquisition_window"]
    assert (window["start_ns"], window["end_ns"], window["status"]) == (100, 112, "incomplete")
    diagnostic, = state.diagnostics
    assert state.metadata["execution"]["status"] == "step_limit" and diagnostic.category == "simulation_time_limit"
    assert diagnostic.details["display_message"] == "Reached simulation time limit."
    assert diagnostic.details["time_limit_local_ns"] == 112
    if active_rt:
        assert diagnostic.source.raw == "wait 100"
        assert diagnostic.related_events == [event.id for event in state.events if event.kind == "wait"]
    else:
        assert diagnostic.source == acquire.source and diagnostic.related_events == [acquire.id]


@pytest.mark.parametrize("dt,status", [(1, "truncated"), (2, "unavailable")])
def test_qrm_scope_keeps_known_window_when_adc_sampling_is_limited(dt, status):
    rx = _run({"rx": "acquire 0,0,4\nstop"}, hardware={"rx": _QRM_HARDWARE},
              identity={"rx": {"module_type": "QRM", "sequencer_index": 2}},
              physical={"sample_period_ns": dt, "max_samples": 500, "max_export_samples": 20,
                        "sequencers": {"rx": {"input": {"kind": "constant", "i": 0.25, "q": 0.0}}}})["rx"]
    capture, = _qrm(rx)["captures"]
    assert capture["window"]["effective_duration_ns"] == 16384
    assert capture["sample_status"] == status
    if dt == 1:
        assert capture["sampled_count"] == 400 and capture["sampled_until_ns"] == 500
        assert len(_qrm(rx)["buffer"]["samples"][0]) == 20
    else:
        assert _qrm(rx)["buffer"] is None


def test_project_scope_identity_reaches_cli_watch_and_def(tmp_path, capsys):
    (tmp_path / "rx.q1asm").write_text("move 7,R0\nnop\nacquire 0,0,4\nstop", encoding="utf-8")
    project = tmp_path / "q1timeline.yml"
    project.write_text("""analysis:
  execution_mode: simulation
sequencers:
  - id: rx
    file: rx.q1asm
    module: R0
    module_type: QRM
    sequencer_index: 2
    hardware_parameters:
      integration_length_acq: 200
      scope_acq_sequencer_select: 2
      scope_acq_trigger_mode_path0: sequencer
simulation:
  startup_delay_ns: 100
  inputs:
    acquisitions:
      - {sequencer: rx, acquisition_index: 0, bin_index: 0, i: 1, q: 0}
""", encoding="utf-8")
    assert main(["analyze", "--project", str(project), "--format", "vscode-json", "--include-timeline-ir", "--no-render"]) == 0
    cli_ir = json.loads(capsys.readouterr().out)["timeline_ir"]
    watch = run_watch_once(project, tmp_path / "watch")
    assert watch.ok
    watch_ir = json.loads(watch.timeline_ir.read_text(encoding="utf-8"))
    run = RunSnapshot(Namespace(project=project, execution_mode=None, max_steps=None, q1asm_strict=False,
                                hardware_readiness=False, params=None, set_param=[]))
    detailed = run.run("rx", "R0", {}, detail=True)
    for ir in (cli_ir, watch_ir, detailed["ir"]):
        event, = [event for event in ir["events"] if event["kind"] == "acquire"]
        assert event["meta"]["scope_acquisition_window"]["end_ns"] == 16484
        capture, = ir["simulation"]["rx"]["qrm_scope"]["captures"]
        assert capture["acquire_event_id"] == event["id"] and capture["sample_status"] == "unavailable"


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation", "playback"])
@pytest.mark.parametrize("conflict", [False, True])
def test_scope_settings_are_shared_only_within_the_named_module(tmp_path, capsys, mode, conflict):
    (tmp_path / "acquire.q1asm").write_text("acquire 0,0,4\nstop", encoding="utf-8")
    (tmp_path / "idle.q1asm").write_text("stop", encoding="utf-8")
    project = tmp_path / "q1timeline.yml"
    project.write_text("""sequencers:
  - id: rx
    file: acquire.q1asm
    module: R0
    module_type: QRM
    sequencer_index: 2
    hardware_parameters:
      integration_length_acq: 200
      scope_acq_trigger_mode_path0: sequencer
  - id: peer
    file: idle.q1asm
    module: R0
    sequencer_index: 1
    hardware_parameters:
      scope_acq_sequencer_select: 2
  - id: unrelated
    file: acquire.q1asm
    module: R1
    module_type: QRM
    sequencer_index: 2
simulation:
  inputs:
    acquisitions:
      - {sequencer: rx, acquisition_index: 0, bin_index: 0, i: 1}
      - {sequencer: unrelated, acquisition_index: 0, bin_index: 0, i: 1}
""".replace("      integration_length_acq: 200\n", "      integration_length_acq: 200\n"
             + ("      scope_acq_sequencer_select: 1\n" if conflict else "")), encoding="utf-8")
    if mode == "playback":
        session = PlaybackSession.from_project(project)
        assert session.run(budget_ms=1000)["status"] == "completed"
        ir = session.snapshot()
    else:
        args = ["analyze", "--project", str(project), "--execution-mode", mode,
                "--format", "vscode-json", "--include-timeline-ir", "--no-render"]
        assert main(args) == (2 if conflict and mode == "simulation" else 0)
        result = json.loads(capsys.readouterr().out)
        if conflict and mode == "simulation":
            assert "Conflicting scope_acq_sequencer_select for QRM scope module 'R0'" in result["error"]["message"]
            return
        ir = result["timeline_ir"]
    acquisitions = {event["sequencer_id"]: event for event in ir["events"] if event["kind"] == "acquire"}
    assert "scope_acquisition_window" not in acquisitions["unrelated"]["meta"]
    meta = acquisitions["rx"]["meta"]
    if conflict:
        assert "scope_acquisition_window" not in meta
        diagnostic, = [item for item in ir["diagnostics"] if "conflicting_scope_settings" in item["details"]]
        assert diagnostic["details"] == {"module": "R0", "conflicting_scope_settings": ["scope_acq_sequencer_select"]}
    else:
        assert meta["scope_acquisition_window"]["effective_duration_ns"] == 16384
        assert meta["scope_acquisition_window"]["paths"] == [0]
        if mode != "simulation":
            assert meta["acquisition_window"]["effective_duration_ns"] == 200


@pytest.mark.parametrize("wait,retrigger,status,end", [
    (16_380, False, "completed", 16_384), (16_376, False, "unknown", None), (100, True, "retriggered", 104),
])
def test_analytical_scope_requires_a_concrete_prefix_through_completion_or_retrigger(wait, retrigger, status, end):
    suffix = ("acquire 0,1,4\n" if retrigger else "") + "wait {DELAY}\njmp R1\nstop"
    state = interpret_program(parse_q1asm(f"acquire 0,0,4\nwait {wait}\n" + suffix), sequencer_id="readout")
    state.metadata.update(module_type="QRM", sequencer_index=2, hardware_parameters=_QRM_HARDWARE)
    first, *later = [event for event in timeline_ir_from_states([state])["events"] if event["kind"] == "acquire"]
    window = first["meta"]["scope_acquisition_window"]
    assert window["status"] == status and window["end_ns"] == end and window["paths"] == [0]
    if retrigger:
        assert window["interrupted_by_event_id"] == later[0]["id"]


@pytest.mark.parametrize("prepared", [False, True])
def test_qtm_scope_windowed_records_exact_open_rise_close_separately_from_bin(prepared):
    program = "set_scope_en 1\nacquire_timetags 0,0,1,1,20\nacquire_timetags 0,1,0,0,4\nstop"
    tags = _run({"tags": program}, prepared=prepared,
                digital={"tags": {"scope_mode": "timetags-windowed"}},
                inputs={"digital": {"tags": _trace(100 * 128 + 1, 104 * 128, 120 * 128)}})["tags"]
    scope = _qtm(tags)
    assert [(entry["kind"], entry["time_ticks"]) for entry in scope["entries"]] == [
        ("OPEN", 100 * 128 + 1), ("RISE", 100 * 128 + 1), ("RISE", 104 * 128), ("CLOSE", 120 * 128),
    ]
    capture, = scope["captures"]
    assert capture["status"] == "completed" and capture["input_status"] == "known"
    result, = tags.metadata["simulation"]["acquisitions"]
    assert result["count"] == 2 and result["bin_index"] == 1
    if not prepared:
        by_id = {event.id: event for event in tags.events}
        assert by_id[capture["setter_event_id"]].source.line == 1
        assert all(entry["source_event_id"] in by_id for entry in scope["entries"])


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize(("opening", "closing", "open_tick", "close_tick", "rejected"), [
    ("acquire_ttl 0,2,1,4", "acquire_ttl 0,7,0,4", 0, 0, False),
    ("acquire_timetags 0,2,1,1,4", "acquire_timetags 0,3,0,3,4", 1, 3, False),
    ("acquire_timetags 0,2,1,1,4", "acquire_timetags 0,7,0,3,4", 1, 3, True),
])
def test_effective_gate_window_belongs_to_executed_opener_even_when_close_bin_rejects(
    prepared, opening, closing, open_tick, close_tick, rejected,
):
    state = _run({"rx": f"{opening}\nwait 16\n{closing}\nstop"}, prepared=prepared,
                 inputs={"digital": {"rx": _trace(101 * 128, 119 * 128)}})["rx"]
    opened, closed = [event for event in state.events if event.kind == "acquire"]
    window = {"start_ticks": 100 * 128 + open_tick, "end_ticks": 120 * 128 + close_tick, "status": "closed"}
    assert opened.meta["digital_gate_window"] == state.rt_packets[0].meta["digital_gate_window"] == window
    assert opened.meta["digital_gate_timing"] == closed.meta["digital_gate_timing"] == "effective"
    assert (opened.t0.value, opened.t1.value) == (100, 104)
    assert closed.t0.value == 120 and "digital_gate_window" not in closed.meta
    if rejected:
        assert state.metadata["execution"]["status"] == "error"
        diagnostic, = state.diagnostics
        assert diagnostic.source == closed.source and "out of bounds" in diagnostic.message
        assert not state.metadata["simulation"]["acquisitions"] and not state.metadata["simulation"]["digital_bins"]
    else:
        assert state.metadata["execution"]["status"] == "completed" and closed.t1.value == 124
        result, = state.metadata["simulation"]["acquisitions"]
        assert (result["start_ticks"], result["end_ticks"], result["count"]) == (window["start_ticks"], window["end_ticks"], 2)
        assert result["open_packet_id"] == state.rt_packets[0].id


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize(("source", "kind", "status"), [
    ("acquire_ttl 0,7,1,20\nstop", "acquire", "error"),
    ("set_cond 1,1,0,4\nacquire_timetags 0,0,1,1,4\nstop", "conditional_skip", "completed"),
])
def test_refused_or_skipped_gate_attempt_does_not_publish_window(prepared, source, kind, status):
    state = _run({"rx": source}, prepared=prepared)["rx"]
    packet, = state.rt_packets
    assert packet.meta["digital_gate_timing"] == "effective" and "digital_gate_window" not in packet.meta
    assert state.metadata["execution"]["status"] == status and not state.metadata["simulation"]["acquisitions"]
    if not prepared or kind == "acquire":
        event, = [event for event in state.events if event.kind == kind]
        assert event.meta["digital_gate_timing"] == "effective" and "digital_gate_window" not in event.meta


@pytest.mark.parametrize("prepared", [False, True])
def test_pending_qtm_close_finalizes_gate_after_rt_drain(prepared):
    source = "acquire_timetags 0,0,1,1,4\nacquire_timetags 0,0,0,1025,4\nstop"
    state = _run({"rx": source}, prepared=prepared,
                 inputs={"digital": {"rx": _trace(104 * 128, 111 * 128)}})["rx"]
    opener, _ = [event for event in state.events if event.kind == "acquire"]
    window = opener.meta["digital_gate_window"]
    result, = state.metadata["simulation"]["acquisitions"]
    assert state.metadata["execution"]["status"] == "completed" and state.rt_time_ns.value == 108
    assert window == {"start_ticks": 100 * 128 + 1, "end_ticks": 112 * 128 + 1, "status": "closed"}
    assert result["end_ticks"] == window["end_ticks"] and result["count"] == 2


def test_gate_prefix_matching_keeps_different_effective_starts_separate():
    state = _run({"rx": "acquire_timetags 0,0,1,1,4\nacquire_timetags 0,0,0,3,4\nstop"})["rx"]
    opener, _ = [event for event in state.events if event.kind == "acquire"]
    window = opener.meta["digital_gate_window"]
    later_cutoff = {**window, "end_ticks": window["end_ticks"] + 1, "status": "incomplete"}
    alternate = replace(opener, meta={**opener.meta, "digital_gate_window": later_cutoff})
    assert _same_comparison_prefix_event(opener, alternate)
    later_open = {**later_cutoff, "start_ticks": window["start_ticks"] + 1}
    alternate = replace(opener, meta={**opener.meta, "digital_gate_window": later_open})
    assert not _same_comparison_prefix_event(opener, alternate)


def test_digital_level_result_is_a_point_without_a_gate_window():
    state = _run({"rx": "acquire_digital 0,0,20\nstop"},
                 inputs={"digital": {"rx": {"initial_level": 1, "transitions": []}}})["rx"]
    acquire, = [event for event in state.events if event.kind == "acquire"]
    result, = [event for event in state.events if event.kind == "acquisition_result"]
    assert (acquire.t0.value, acquire.t1.value) == (100, 120) and "digital_gate_window" not in acquire.meta
    assert result.t0 == result.t1 == acquire.t0 and result.meta["level"] == 1


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("kind,fine_delay", [("ttl", 0), ("qtm", 0), ("qtm", 3)])
def test_failed_digital_capture_ends_at_its_own_underflow_edge(kind, fine_delay, prepared):
    if kind == "ttl":
        start = 4
        source = "acquire_ttl 0,0,1,4\n"
        digital = {}
    else:
        start = 12
        source = f"set_scope_en 1\nacquire_timetags 0,0,1,{fine_delay},4\n"
        digital = {"rx": {"scope_mode": "timetags-windowed"}}
    start_ticks = start * 128 + fine_delay
    end = start + 4
    end_ticks = end * 128
    observed = [start_ticks, end_ticks - 2]
    states = _run({"rx": source + "nop\n" * 20 + "stop", "peer": "wait 200\nstop"},
                  startup_delay_ns=start, prepared=prepared, digital=digital,
                  inputs={"digital": {"rx": _trace(*observed, end_ticks + 2)}})

    rx, peer = states["rx"], states["peer"]
    assert peer.metadata["execution"]["status"] == "completed" and peer.rt_time_ns.value == start + 200
    assert rx.metadata["execution"]["status"] == "error"
    assert rx.metadata["execution"]["reason"] == f"RT queue underflow at {end} ns."
    assert [diagnostic.category for diagnostic in rx.diagnostics] == ["rt_queue_underflow"]
    result, = rx.metadata["simulation"]["acquisitions"]
    assert result["status"] == "incomplete" and result["count"] == 2
    assert (result["start_ticks"], result["end_ticks"], result["time_ns"]) == (start_ticks, end_ticks, end)
    assert result["edge_times_ticks"] == observed
    opener, = [event for event in rx.events if event.kind == "acquire"]
    window = {"start_ticks": start_ticks, "end_ticks": end_ticks, "status": "incomplete"}
    assert opener.meta["digital_gate_window"] == rx.rt_packets[0].meta["digital_gate_window"] == window
    if kind == "qtm":
        scope = _qtm(rx)
        capture, = scope["captures"]
        assert capture["status"] == "incomplete" and capture["end_ticks"] == end_ticks
        expected_entries = [("OPEN", start_ticks)] + [("RISE", tick) for tick in observed]
        assert [(entry["kind"], entry["time_ticks"]) for entry in scope["entries"]] == expected_entries
    if not prepared:
        event, = [event for event in rx.events if event.kind == "acquisition_result"]
        assert event.t0.value == end and event.id == result["event_id"]


@pytest.mark.parametrize("prepared", [False, True])
def test_illegal_fault_preserves_observed_edges_in_an_incomplete_continuous_scope(prepared):
    source = "set_scope_en 1\nacquire_timetags 0,0,1,1,40\nnop\nillegal"
    observed = [12 * 128 + 1, 19 * 128 + 3]
    states = _run({"rx": source, "peer": "wait 200\nstop"}, startup_delay_ns=12, prepared=prepared,
                  digital={"rx": {"scope_mode": "timetags", "scope_end_ticks": 300 * 128}},
                  inputs={"digital": {"rx": _trace(*observed, 21 * 128)}})

    rx, peer = states["rx"], states["peer"]
    assert peer.metadata["execution"]["status"] == "completed" and peer.rt_time_ns.value == 212
    assert rx.metadata["execution"]["status"] == "error"
    assert rx.metadata["execution"]["fault"]["q1_time_ns"] == 20
    assert [diagnostic.category for diagnostic in rx.diagnostics] == ["illegal_instruction"]
    result, = rx.metadata["simulation"]["acquisitions"]
    assert result["status"] == "incomplete" and result["end_ticks"] == 20 * 128
    assert result["edge_times_ticks"] == observed
    scope = _qtm(rx)
    capture, = scope["captures"]
    assert capture["status"] == "incomplete" and capture["end_ticks"] == 20 * 128
    assert [(entry["kind"], entry["time_ticks"]) for entry in scope["entries"]] == [("RISE", tick) for tick in observed]


def test_qtm_continuous_scope_outlives_binned_window_and_rt_with_explicit_horizon():
    # The published continuous tutorial closes after four ns and ends the RT program.
    program = "set_scope_en 1\nacquire_timetags 0,0,1,0,4\nacquire_timetags 0,0,0,0,4\nstop"
    tags = _run({"tags": program}, digital={"tags": {"scope_mode": "timetags", "scope_end_ticks": 115 * 128}},
                inputs={"digital": {"tags": _trace(101 * 128, 110 * 128, 115 * 128, 118 * 128)}})["tags"]
    assert tags.rt_time_ns.value == 108
    scope = _qtm(tags)
    assert [(entry["kind"], entry["time_ticks"]) for entry in scope["entries"]] == [("RISE", 101 * 128), ("RISE", 110 * 128)]
    capture, = scope["captures"]
    assert capture["status"] == "horizon" and capture["end_ticks"] == 115 * 128
    assert tags.metadata["simulation"]["acquisitions"][0]["count"] == 1


@pytest.mark.parametrize("mode,expected_entries", [("timetags", 20), ("timetags-windowed", 10)])
def test_qtm_scope_tutorial_pulse_counts_with_explicit_digital_loopback(mode, expected_entries):
    # Qblox uses 20 pulses with an increasing gap, and 6 us windows separated by a 4.2 us wait.
    tx = ("move 76,R0\nmove 20,R1\nwait_sync 4\nwait 80\nloop:\nset_digital 1,1,0\nupd_param 4\nwait 16\n"
          "set_digital 0,1,0\nupd_param 4\nwait R0\nadd R0,1000,R0\nnop\nsub R1,1,R1\njnz @loop\nstop")
    rx = "wait_sync 4\nset_scope_en 1\nacquire_timetags 0,0,1,0,4\n"
    settings = {"scope_mode": mode}
    if mode == "timetags":
        settings["scope_end_ticks"] = 200_000 * 128
        rx += "acquire_timetags 0,0,0,0,4\n"
    else:
        rx += ("wait 5996\nacquire_timetags 0,0,0,0,4\nwait 4200\n"
               "acquire_timetags 0,1,1,0,4\nwait 5996\nacquire_timetags 0,1,0,0,4\n")
    states = _run({"tx": tx, "rx": rx + "stop"}, digital={"rx": settings}, digital_routes=[{"source": "tx", "target": "rx"}])
    assert all(state.metadata["execution"]["status"] == "completed" for state in states.values())
    entries = _qtm(states["rx"])["entries"]
    assert len(entries) == expected_entries
    rising = [entry for entry in entries if entry["kind"] == "RISE"]
    relative_edges = [80 + 100 * index + 500 * index * (index - 1) for index in range(20)]
    if mode == "timetags-windowed":
        relative_edges = relative_edges[:4] + relative_edges[5:7]
    assert [entry["time_ticks"] for entry in rising] == [(104 + relative) * 128 for relative in relative_edges]
    by_id = {event.id: event for state in states.values() for event in state.events}
    assert all(by_id[by_id[entry["source_event_id"]].meta["source_event_id"]].kind == "digital_output_transition"
               for entry in rising)


def test_disabling_scope_does_not_close_the_timetag_bin_and_skipped_opens_are_ignored():
    program = ("set_scope_en 1\nacquire_timetags 0,0,1,0,20\nset_scope_en 0\nupd_param 4\n"
               "acquire_timetags 0,0,0,0,4\nset_scope_en 1\nset_cond 1,1,0,4\nacquire_timetags 0,1,1,0,4\nstop")
    tags = _run({"tags": program}, digital={"tags": {"scope_mode": "timetags-windowed"}},
                inputs={"digital": {"tags": _trace(101 * 128, 122 * 128, 130 * 128)}})["tags"]
    scope = _qtm(tags)
    assert [entry["kind"] for entry in scope["entries"]] == ["OPEN", "RISE"]
    capture, = scope["captures"]
    assert capture["status"] == "disabled" and capture["end_ticks"] == 120 * 128
    assert next(event for event in tags.events if event.id == capture["disable_event_id"]).source.line == 4
    assert tags.metadata["simulation"]["acquisitions"][0]["count"] == 2


@pytest.mark.parametrize("mode", ["timetags-windowed", "timetags"])
def test_qtm_scope_capacity_does_not_truncate_binned_count(mode):
    program = "set_scope_en 1\nacquire_timetags 0,0,1,0,40\nacquire_timetags 0,0,0,0,4\nstop"
    settings = {"scope_mode": mode}
    if mode == "timetags":
        settings["scope_end_ticks"] = 300 * 128
    tags = _run({"tags": program}, digital={"tags": settings},
                inputs={"digital": {"tags": _trace(*(100 * 128 + 2 * i for i in range(1, 2051)))}})["tags"]
    scope = _qtm(tags)
    assert scope["capacity"] == len(scope["entries"]) == 2048
    assert scope["truncated"]
    if mode == "timetags-windowed":
        assert scope["dropped_entries"] == 4
    else:
        capture, = scope["captures"]
        assert capture["status"] == "capacity" and capture["end_ticks"] == 100 * 128 + 2 * 2048
    assert tags.metadata["simulation"]["acquisitions"][0]["count"] == 2050


@pytest.mark.parametrize("settings,reason", [({"scope_mode": "scope"}, "scope_mode"),
    ({"scope_mode": "timetags"}, "scope_end_ticks"), ({"scope_mode": "timetags", "scope_end_ticks": 1000}, "scope_end_ticks"),
    ({"scope_mode": "timetags-windowed", "scope_end_ticks": 100}, "continuous")])
def test_scope_configuration_is_explicit_and_bounded(settings, reason):
    with pytest.raises(ValueError, match=reason):
        validate_simulation({"max_time_ns": 4, "digital": {"tags": settings}}, ["tags"])
