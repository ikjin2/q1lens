from __future__ import annotations

import json
from pathlib import Path

import pytest

from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.interpreter import interpret_program
from q1timeline.cli import main
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.playback import PlaybackSession
from q1timeline.project import ConfigLoadError, load_project_config
from q1timeline.q1asm.parser import parse_q1asm


def state(source: str, duration: int | None = 1000, **options):
    result = interpret_program(parse_q1asm(source, file="readout.q1asm"), sequencer_id="readout", **options)
    result.metadata["hardware_parameters"] = {} if duration is None else {"integration_length_acq": duration}
    return result


def acquires(ir):
    return [event for event in ir["events"] if event["kind"] == "acquire"]


def project(tmp_path: Path, value="1000") -> Path:
    (tmp_path / "readout.q1asm").write_text("wait_sync 4\nacquire 0,0,4\nwait 996\nstop\n", encoding="utf-8")
    path = tmp_path / "q1timeline.yml"
    path.write_text(
        "sequencers:\n  - id: readout\n    file: readout.q1asm\n"
        f"    hardware_parameters:\n      integration_length_acq: {value}\n",
        encoding="utf-8",
    )
    return path


def test_integration_is_independent_of_rt_duration_and_does_not_mutate_state():
    result = state("acquire 0,0,4\nwait 996\nstop\n")
    event = next(event for event in result.events if event.kind == "acquire")
    first = timeline_ir_from_states([result])
    window = acquires(first)[0]["meta"]["acquisition_window"]
    assert window == {
        "start_ns": 0, "end_ns": 1000, "configured_duration_ns": 1000,
        "effective_duration_ns": 1000, "source": "hardware_parameters", "status": "completed",
    }
    assert acquires(first)[0]["duration"]["value"] == 4
    assert acquires(first)[0]["t1"]["value"] == 4
    assert result.rt_time_ns.value == 1000
    assert "acquisition_window" not in event.meta
    assert timeline_ir_from_states([result]) == first


def test_new_acquisition_interrupts_same_engine_even_for_different_indices():
    result = state("acquire 0,0,4\nwait 196\nacquire 7,2,4\nwait 996\nstop\n")
    first, second = acquires(timeline_ir_from_states([result]))
    assert first["meta"]["acquisition_window"] == {
        "start_ns": 0, "end_ns": 200, "configured_duration_ns": 1000,
        "effective_duration_ns": 200, "source": "hardware_parameters", "status": "interrupted",
        "interrupted_by_event_id": second["id"],
    }
    assert second["meta"]["acquisition_window"]["end_ns"] == 1200


def test_sequencers_have_independent_acquisition_engines():
    first = state("acquire 0,0,4\nwait 996\nstop\n")
    second = interpret_program(parse_q1asm("wait 100\nacquire 1,0,4\nstop\n", file="other.q1asm"), sequencer_id="other")
    second.metadata["hardware_parameters"] = {"integration_length_acq": 20}
    windows = acquires(timeline_ir_from_states([first, second]))
    assert [event["meta"]["acquisition_window"]["end_ns"] for event in windows] == [1000, 120]


def test_missing_setting_uses_instruction_duration_but_weighted_stays_unknown():
    missing = acquires(timeline_ir_from_states([state("acquire 0,0,24\nstop\n", duration=None)]))[0]
    assert missing["meta"]["acquisition_window"] == {
        "start_ns": 0, "end_ns": 24, "configured_duration_ns": None,
        "effective_duration_ns": 24, "source": "instruction_duration_fallback", "status": "completed",
    }
    weighted = acquires(timeline_ir_from_states([state("acquire_weighed 0,0,0,1,4\nstop\n")]))[0]
    window = weighted["meta"]["acquisition_window"]
    assert window["status"] == window["source"] == "unknown"
    assert window["end_ns"] is window["effective_duration_ns"] is None


def test_weighted_command_interrupts_previous_normal_acquire():
    result = state("acquire 0,0,4\nacquire_weighed 1,0,0,1,4\nstop\n")
    first, second = acquires(timeline_ir_from_states([result]))
    assert first["meta"]["acquisition_window"]["end_ns"] == 4
    assert first["meta"]["acquisition_window"]["interrupted_by_event_id"] == second["id"]


@pytest.mark.parametrize("source,options", [
    ("move 3,R0\nagain: acquire 0,0,4\nloop R0,@again\nstop\n", {}),
    ("jlt R0,1,@other\nacquire 0,0,4\nstop\nother: acquire 1,0,4\nstop\n",
     {"branch_policy": "explore_both_with_depth_limit"}),
])
def test_compact_loop_previews_and_comparison_paths_are_unknown(source, options):
    events = acquires(timeline_ir_from_states([state(source, **options)]))
    assert events
    assert all(event["meta"]["acquisition_window"]["status"] == "unknown" for event in events)


def test_feedback_availability_uses_integration_end_and_alignment():
    result = state("wait 12\nwait_sync 4\nfb_acq_tb_id 5,4\nacquire 0,0,4\nwait 996\nfb_pop_data 5,R0\nnop\nstop\n")
    align_timelines([result], mode="first_wait_sync")
    ir = timeline_ir_from_states([result])
    diagnostic = next(item for item in ir["diagnostics"] if item["category"] == "feedback_latency_violation")
    assert diagnostic["details"]["actual_gap_ns"] == 0
    assert diagnostic["details"]["required_latency_ns"] == 160
    assert ir["feedback_flows"][0]["send_available_ns"] == 1008
    result.metadata["hardware_parameters"] = {}
    assumed = timeline_ir_from_states([result])
    assert "send_available_ns" not in assumed["feedback_flows"][0]
    assert assumed["feedback_flows"][0]["timing"]["send_available_ns"] is None
    assert not any(item["category"] == "feedback_latency_violation" for item in assumed["diagnostics"])


def test_project_cli_and_playback_preserve_hardware_parameter(tmp_path: Path):
    path = project(tmp_path)
    assert load_project_config(path).sequencers[0].hardware_parameters == {"integration_length_acq": 1000}
    output = tmp_path / "timeline.json"
    assert main(["analyze", "--project", str(path), "--out", str(output), "--no-render"]) == 0
    ir = json.loads(output.read_text(encoding="utf-8"))
    assert ir["sequencers"][0]["hardware_parameters"] == {"integration_length_acq": 1000}
    assert acquires(ir)[0]["meta"]["acquisition_window"]["end_ns"] == 1004
    playback = PlaybackSession.from_project(path)
    playback.run(max_steps=100)
    assert acquires(playback.snapshot())[0]["meta"]["acquisition_window"]["configured_duration_ns"] == 1000


@pytest.mark.parametrize("value", ["true", "0", "-4", "1.5", "'1000'", "null"])
def test_project_rejects_invalid_integration_lengths(tmp_path: Path, value):
    with pytest.raises(ConfigLoadError, match="configuration contains errors"):
        load_project_config(project(tmp_path, value))


def scoped(result, *, selected=0, index=0, module="QRM", path0="sequencer", path1="level"):
    result.metadata.update(sequencer_index=index, module_type=module)
    result.metadata["hardware_parameters"].update(scope_acq_sequencer_select=selected,
        scope_acq_trigger_mode_path0=path0, scope_acq_trigger_mode_path1=path1)
    return result


def test_scope_capture_is_independent_of_integration_and_rt_wait():
    event = acquires(timeline_ir_from_states([scoped(state("acquire 0,0,4\nstop\n"))]))[0]
    assert event["meta"]["acquisition_window"]["end_ns"] == 1000
    assert event["meta"]["scope_acquisition_window"] == {
        "start_ns": 0, "end_ns": 16384, "configured_duration_ns": 16384,
        "effective_duration_ns": 16384, "source": "hardware_parameters", "status": "completed", "paths": [0],
    }
    assert event["duration"]["value"] == 4


@pytest.mark.parametrize("options", [{"selected": 1}, {"index": None}, {"path0": "level"}])
def test_scope_is_not_inferred_for_unselected_or_level_triggered_sequencers(options):
    event = acquires(timeline_ir_from_states([scoped(state("acquire 0,0,4\nstop\n"), **options)]))[0]
    assert "scope_acquisition_window" not in event["meta"]


def test_next_scope_trigger_bounds_previous_capture_and_unknown_hardware_stays_unknown():
    result = scoped(state("acquire 0,0,4\nwait 596\nacquire 1,0,4\nstop\n"), path1="sequencer")
    first, second = acquires(timeline_ir_from_states([result]))
    assert first["meta"]["scope_acquisition_window"]["end_ns"] == 600
    assert first["meta"]["scope_acquisition_window"]["status"] == "retriggered"
    assert first["meta"]["scope_acquisition_window"]["paths"] == [0, 1]
    assert second["meta"]["scope_acquisition_window"]["end_ns"] == 16984
    unknown = scoped(state("acquire 0,0,4\nstop\n"), module=None)
    window = acquires(timeline_ir_from_states([unknown]))[0]["meta"]["scope_acquisition_window"]
    assert window["status"] == "unknown"
    assert window["end_ns"] is None


def test_scope_project_and_playback_preserve_selection_settings(tmp_path: Path):
    path = project(tmp_path)
    path.write_text(path.read_text().replace("    file:", "    sequencer_index: 0\n    module_type: QRM-RF\n    file:")
                    + "      scope_acq_sequencer_select: 0\n      scope_acq_trigger_mode_path0: sequencer\n", encoding="utf-8")
    assert load_project_config(path).sequencers[0].sequencer_index == 0
    output = tmp_path / "timeline.json"
    assert main(["analyze", "--project", str(path), "--out", str(output), "--no-render"]) == 0
    ir = json.loads(output.read_text())
    assert acquires(ir)[0]["meta"]["scope_acquisition_window"]["effective_duration_ns"] == 16384
    playback = PlaybackSession.from_project(path)
    playback.run(max_steps=100)
    assert acquires(playback.snapshot())[0]["meta"]["scope_acquisition_window"]["effective_duration_ns"] == 16384


@pytest.mark.parametrize("setting", ["scope_acq_sequencer_select: true", "scope_acq_sequencer_select: 6",
    "scope_acq_trigger_mode_path0: true", "scope_acq_trigger_mode_path1: invalid"])
def test_scope_project_rejects_invalid_hardware_settings(tmp_path: Path, setting):
    path = project(tmp_path)
    path.write_text(path.read_text() + f"      {setting}\n", encoding="utf-8")
    with pytest.raises(ConfigLoadError):
        load_project_config(path)
