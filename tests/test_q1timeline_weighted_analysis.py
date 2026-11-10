import json
from argparse import Namespace
from dataclasses import replace

import pytest

from q1timeline.analysis.api import analyze_documents, analyze_text
from q1timeline.analysis.interpreter import interpret_program, prepare_def_program
from q1timeline.analysis.values import Concrete
from q1timeline.cli import main
from q1timeline.def_runs import RunSnapshot
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.playback import PlaybackSession
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.sequence import sequence_weight_lengths
from q1timeline.watch.watcher import run_watch_once


_PROGRAM = "fb_acq_tb_id 5,4\nacquire_weighted 0,0,0,1,4\nwait 200\nfb_pop_data 5,R0\nnop\nstop"


def _memory(*lengths):
    return {"weights": {f"w{index}": {"index": index, "data": [0.5] * length} for index, length in enumerate(lengths)},
            "acquisitions": {"result": {"index": 0, "num_bins": 4}}}


def _acquires(ir):
    return [event for event in ir["events"] if event["kind"] == "acquire"]


def _project(tmp_path, program, *, mode="analysis"):
    (tmp_path / "readout.q1asm").write_text(program, encoding="utf-8")
    (tmp_path / "sequence.json").write_text(json.dumps(_memory(80, 80, 120, 120)), encoding="utf-8")
    config = tmp_path / "q1timeline.yml"
    config.write_text(f"""alignment:
  mode: none
analysis:
  execution_mode: {mode}
sequencers:
  - id: readout
    file: readout.q1asm
    sequence_json: sequence.json
    hardware_parameters:
      integration_length_acq: 300
""", encoding="utf-8")
    return config


def _assert_known_feedback(ir):
    event, = _acquires(ir)
    assert (event["t0"]["value"], event["t1"]["value"], event["duration"]["value"]) == (4, 8, 4)
    assert event["meta"]["acquisition_window"] == {
        "start_ns": 4, "end_ns": 84, "configured_duration_ns": 80,
        "effective_duration_ns": 80, "source": "weight_memory", "status": "completed",
    }
    flow, = ir["feedback_flows"]
    assert flow["send_available_ns"] == flow["timing"]["send_available_ns"] == 84
    assert flow["timing"]["arrival_ns"] == 244


def test_cli_watch_play_and_api_agree_on_weight_memory_and_feedback(tmp_path, capsys):
    config = _project(tmp_path, _PROGRAM)
    assert main(["analyze", "--project", str(config), "--format", "vscode-json", "--include-timeline-ir", "--no-render"]) == 0
    cli = json.loads(capsys.readouterr().out)["timeline_ir"]
    watch = run_watch_once(config, tmp_path / "watch")
    assert watch.ok
    watched = json.loads(watch.timeline_ir.read_text(encoding="utf-8"))
    playback = PlaybackSession.from_project(config)
    assert playback.run(max_steps=100)["status"] == "completed"
    for ir in (cli, watched, playback.snapshot(), analyze_text(_PROGRAM, sequence=_memory(80, 80))):
        _assert_known_feedback(ir)


@pytest.mark.parametrize("mode", ["analysis", "concrete"])
@pytest.mark.parametrize("op", ["acquire_weighed", "acquire_weighted"])
def test_register_weights_are_captured_for_each_occurrence_and_retrigger_first(mode, op):
    program = (f"move 0,R0\nmove 0,R1\nmove 1,R2\nnop\n{op} 0,R0,R1,R2,4\n"
               f"move 2,R1\nmove 3,R2\nnop\nwait 16\n{op} 0,R0,R1,R2,4\nwait 120\nstop")
    ir = analyze_text(program, execution_mode=mode, sequence=_memory(80, 80, 120, 120))
    first, second = _acquires(ir)
    assert first["meta"]["weight_indices"] == [0, 1]
    assert second["meta"]["weight_indices"] == [2, 3]
    first_window, second_window = [event["meta"]["acquisition_window"] for event in (first, second)]
    assert first_window["status"] == "interrupted" and first_window["end_ns"] == 20
    assert first_window["effective_duration_ns"] == 20 and first_window["interrupted_by_event_id"] == second["id"]
    assert second_window["end_ns"] == 140 and second_window["configured_duration_ns"] == 120


def test_multiple_document_api_keeps_weight_metadata_local_to_each_sequencer():
    program = "acquire_weighted 0,0,0,1,4\nwait 200\nstop"
    ir = analyze_documents({"left.q1asm": program, "right.q1asm": program}, execution_mode="concrete",
                           sequences={"left": _memory(80, 80), "right": _memory(120, 120)}, alignment_mode="none")
    assert {event["sequencer_id"]: event["meta"]["acquisition_window"]["end_ns"] for event in _acquires(ir)} == {
        "left": 80, "right": 120,
    }


@pytest.mark.parametrize("wait_operand", ["R0", "{DELAY}"])
def test_concrete_rt_prefix_keeps_only_acquisitions_that_finish_before_unresolved_suffix(wait_operand):
    program = f"acquire 0,0,4\nwait 100\nwait {wait_operand}\njmp R1\nstop"
    completed, = _acquires(analyze_text(program))
    assert completed["meta"]["acquisition_window"] == {
        "start_ns": 0, "end_ns": 4, "configured_duration_ns": None,
        "effective_duration_ns": 4, "source": "instruction_duration_fallback", "status": "completed",
    }
    state = interpret_program(parse_q1asm(program), sequencer_id="readout")
    state.metadata["hardware_parameters"] = {"integration_length_acq": 200}
    pending, = _acquires(timeline_ir_from_states([state]))
    window = pending["meta"]["acquisition_window"]
    assert window["configured_duration_ns"] == 200 and window["status"] == "unknown" and window["end_ns"] is None


def test_later_symbolic_acquisition_cannot_interrupt_completed_readout():
    first, second = _acquires(analyze_text("acquire 0,0,4\nwait 100\nwait {DELAY}\nacquire 0,1,4\nstop"))
    assert first["meta"]["acquisition_window"]["end_ns"] == 4
    assert first["meta"]["acquisition_window"]["status"] == "completed"
    assert second["meta"]["acquisition_window"]["start_ns"] is None


@pytest.mark.parametrize("mode", ["analysis", "concrete", "playback"])
@pytest.mark.parametrize("terminal", ["illegal", "stop"])
def test_weighted_window_keeps_planned_length_but_requires_nonfaulted_path(tmp_path, mode, terminal):
    program = f"acquire_weighed 0,0,0,1,4\n{terminal}"
    memory = _memory(100, 100)
    if mode == "playback":
        config = _project(tmp_path, program)
        (tmp_path / "sequence.json").write_text(json.dumps(memory), encoding="utf-8")
        playback = PlaybackSession.from_project(config)
        assert playback.run()["status"] == ("error" if terminal == "illegal" else "completed")
        ir = playback.snapshot()
    else:
        ir = analyze_text(program, execution_mode=mode, sequence=memory)
    event, = _acquires(ir)
    window = event["meta"]["acquisition_window"]
    assert window["start_ns"] == 0 and window["configured_duration_ns"] == 100 and window["source"] == "weight_memory"
    assert (event["t0"]["value"], event["t1"]["value"]) == (0, 4)
    if terminal == "illegal":
        fault, = [item for item in ir["events"] if item["kind"] == "illegal"]
        assert fault["meta"]["fault"]["q1_time_ns"] == 8
        assert window["planned_duration_ns"] == 100 and window["status"] == "unknown"
        assert window["end_ns"] is None and window["effective_duration_ns"] is None
    else:
        assert window["status"] == "completed" and window["end_ns"] == 100


def test_faulted_analytical_path_cannot_estimate_scope_retrigger_or_linq_availability():
    program = "fb_acq_tb_id 5,4\nacquire_weighted 0,0,0,1,4\nfb_pop_data 5,R0\nnop\nacquire 0,1,4\nillegal"
    state = interpret_program(parse_q1asm(program), sequencer_id="readout")
    state.metadata.update(module_type="QRM", sequencer_index=0, weight_lengths={0: 100, 1: 100},
                          hardware_parameters={"scope_acq_sequencer_select": 0, "scope_acq_trigger_mode_path0": "sequencer"})
    ir = timeline_ir_from_states([state])
    first, second = _acquires(ir)
    for event in (first, second):
        for field in ("acquisition_window", "scope_acquisition_window"):
            window = event["meta"][field]
            assert window["status"] == "unknown" and window["end_ns"] is None
            assert "interrupted_by_event_id" not in window
    assert first["meta"]["scope_acquisition_window"]["planned_duration_ns"] == 16_384
    assert second["meta"]["acquisition_window"]["planned_duration_ns"] == 4
    flow, = ir["feedback_flows"]
    assert flow.get("send_available_ns") is None and flow["timing"]["arrival_ns"] is None


def test_alternate_only_illegal_does_not_invalidate_recorded_acquisition():
    program = "acquire_weighed 0,0,0,1,4\nwait 200\ncmp R0,5\njz @end\nillegal\nend: stop"
    ir = analyze_text(program, sequence=_memory(100, 100), branch_policy="explore_both_with_depth_limit")
    fault, = [event for event in ir["events"] if event["kind"] == "illegal"]
    assert fault["meta"]["branch_comparison_path"] == "fallthrough"
    event, = _acquires(ir)
    assert event["meta"]["acquisition_window"]["end_ns"] == 100


def test_loop_preview_fault_does_not_invalidate_recorded_acquisition():
    state = interpret_program(parse_q1asm("acquire_weighed 0,0,0,1,4\nstop"), sequencer_id="readout")
    state.metadata["weight_lengths"] = {0: 100, 1: 100}
    alternate = interpret_program(parse_q1asm("illegal"), sequencer_id="readout")
    fault, = [event for event in alternate.events if event.kind == "illegal"]
    state.events.append(replace(fault, id="fault-preview", meta={**fault.meta, "preview_source_event_id": fault.id}))
    event, = _acquires(timeline_ir_from_states([state]))
    assert event["meta"]["acquisition_window"]["end_ns"] == 100


def test_prepared_concrete_fault_keeps_window_unknown_without_a_fault_event():
    program = parse_q1asm("acquire_weighed 0,0,0,1,4\nillegal")
    prepared = prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
    state = interpret_program(program, sequencer_id="readout", execution_mode="concrete", prepared=prepared)
    state.metadata["weight_lengths"] = {0: 100, 1: 100}
    assert not any(event.kind == "illegal" for event in state.events)
    assert "fault" in state.metadata["execution"]
    event, = _acquires(timeline_ir_from_states([state]))
    assert event["meta"]["acquisition_window"]["end_ns"] is None


def test_paused_play_keeps_unfinished_acquisition_unresolved_until_normal_completion():
    playback = PlaybackSession({"readout": ("readout.q1asm", "acquire 0,0,4\nwait 200\nstop")},
                               hardware_parameters={"readout": {"integration_length_acq": 100}})
    assert playback.run(max_steps=1)["status"] == "paused"
    paused, = _acquires(playback.snapshot())
    assert paused["meta"]["acquisition_window"]["end_ns"] is None
    assert playback.run()["status"] == "completed"
    completed, = _acquires(playback.snapshot())
    assert completed["meta"]["acquisition_window"]["end_ns"] == 100


@pytest.mark.parametrize("wait,send_time", [(16, None), (100, 84)])
def test_weighted_linq_send_time_depends_on_ordered_rt_prefix_before_unknown_region(wait, send_time):
    program = (f"fb_acq_tb_id 5,4\nacquire_weighted 0,0,0,1,4\nwait {wait}\nfb_pop_data 5,R0\n"
               "nop\nwait {DELAY}\njmp R1\nstop")
    ir = analyze_text(program, sequence=_memory(80, 80))
    acquisition, = _acquires(ir)
    assert acquisition["meta"]["acquisition_window"]["end_ns"] == send_time
    flow, = ir["feedback_flows"]
    assert flow["timing"]["send_available_ns"] == send_time
    assert flow.get("send_available_ns") == send_time


@pytest.mark.parametrize("lane,meta", [
    ("debug.q1_issue", {}), ("branch_compare.taken.rt.wait", {"branch_comparison_branch_id": "branch"}),
    ("rt.wait", {"loop_preview": "loop", "preview_source_event_id": "original"}),
    ("rt.wait", {"rt_trigger_epoch": 1}),
])
def test_ambiguous_or_incomparable_events_do_not_extend_acquisition_horizon(lane, meta):
    state = interpret_program(parse_q1asm("acquire 0,0,4\nwait {DELAY}\njmp R1\nstop"), sequencer_id="readout")
    state.metadata["hardware_parameters"] = {"integration_length_acq": 200}
    acquire, = [event for event in state.events if event.kind == "acquire"]
    position = next(index for index, event in enumerate(state.events) if event.kind == "branch_region")
    state.events.insert(position, replace(acquire, id="unusable-horizon", lane=lane, kind="wait",
                                          t0=Concrete(4), t1=Concrete(304), duration=Concrete(300),
                                          meta={"rt_packet_id": "unusable-packet", **meta}))
    event, = _acquires(timeline_ir_from_states([state]))
    assert event["meta"]["acquisition_window"]["end_ns"] is None


def test_prepared_def_and_play_use_loaded_metadata_with_resolved_weights(tmp_path):
    config = _project(tmp_path, ".DEF W 0\nacquire_weighed 0,0,$W,$W,4\nwait 200\nstop", mode="concrete")
    run = RunSnapshot(Namespace(project=config, execution_mode=None, max_steps=None, q1asm_strict=False,
                                hardware_readiness=False, params=None, set_param=[]))
    prepared = run.prepare_statistics("readout", {"W": 2}, {"W"})
    case = run.run("readout", "R0", {"W": 2}, detail=True, prepared=prepared)
    prepared_event, = _acquires(case["ir"])
    assert prepared_event["meta"]["weight_indices"] == [2, 2]
    assert prepared_event["meta"]["acquisition_window"]["end_ns"] == 120
    playback = PlaybackSession.from_project(config)
    assert playback.run(max_steps=100)["status"] == "completed"
    playback_event, = _acquires(playback.snapshot())
    assert playback_event["meta"]["weight_indices"] == [0, 0]
    assert playback_event["meta"]["acquisition_window"]["end_ns"] == 80


@pytest.mark.parametrize("memory", [None, _memory(80), _memory(80, 120),
    {"weights": {**_memory(80, 80)["weights"], "duplicate": {"index": 1, "data": [0.5] * 80}}},
    {"weights": {**_memory(80, 80)["weights"], "duplicate": {"index": 1}}}])
def test_unreliable_weight_lengths_leave_window_and_linq_arrival_unknown(memory):
    ir = analyze_text(_PROGRAM, sequence=memory)
    event, = _acquires(ir)
    window = event["meta"]["acquisition_window"]
    assert window["source"] == window["status"] == "unknown" and window["end_ns"] is None
    flow, = ir["feedback_flows"]
    assert "send_available_ns" not in flow and flow["timing"]["arrival_ns"] is None


def test_only_normalized_nonempty_and_explicitly_indexed_samples_count_as_weights():
    memory = {"weights": {
        "valid": {"index": 63, "data": [-1, 0, 1.0]},
        "bool_index": {"index": True, "data": [1]}, "negative_index": {"index": -1, "data": [1]},
        "too_large": {"index": 64, "data": [1]}, "string_index": {"index": "3", "data": [1]},
        "empty": {"index": 4, "data": []}, "oversize": {"index": 5, "data": [0] * 16_385},
        "bool_sample": {"index": 6, "data": [False]}, "out_of_range": {"index": 7, "data": [1.5]},
        "nan": {"index": 8, "data": [float("nan")]}, "not_samples": {"index": 9, "data": 80},
    }}
    assert sequence_weight_lengths(memory) == {63: 3}
