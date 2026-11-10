from __future__ import annotations

import json
from pathlib import Path

from q1timeline.analysis.api import analyze_documents, analyze_text
from q1timeline.analysis.interpreter import interpret_program
from q1timeline.ir.serialize import timeline_ir_from_states, timeline_ir_to_json
from q1timeline.q1asm.parser import parse_q1asm


def test_project_ir_keeps_explicit_modules_for_hardware_view(tmp_path: Path) -> None:
    from q1timeline.cli import main

    (tmp_path / "seq.q1asm").write_text("move 7,R0\nnop\nstop")
    project = tmp_path / "q1timeline.yml"
    project.write_text(
        "sequencers:\n"
        "  - id: seq0\n    file: seq.q1asm\n    module: QRM0\n"
        "  - id: seq1\n    file: seq.q1asm\n"
    )
    output = tmp_path / "ir.json"
    assert main(["analyze", "--project", str(project), "--out", str(output), "--no-render"]) == 0
    ir = json.loads(output.read_text())
    assert ir["sequencers"] == [{"id": "seq0", "module": "QRM0"}, {"id": "seq1"}]
    assert {write["sequencer_id"] for write in ir["register_history"]} == {"seq0", "seq1"}


def _writes(ir: dict, register: str) -> list[dict]:
    return [write for write in ir["register_history"] if write["register"] == register]


def test_register_history_uses_readable_time_and_original_q1_source() -> None:
    ir = analyze_text("move 7,R0\nnop\nadd R0,1,R1\nnop\nstop", file="registers.q1asm")
    writes = ir["register_history"]
    assert [(write["register"], write["time"]["value"], write["value"]["value"]) for write in writes] == [
        ("R0", 8, 7),
        ("R1", 24, 8),
    ]
    events = {event["id"]: event for event in ir["events"]}
    for write in writes:
        event = events[write["event_id"]]
        assert event["kind"] == "q1_issue"
        assert write["source"] == event["source"]
        assert write["time"]["value"] > event["t1"]["value"]
        assert write["confidence"] == "exact"
    assert all(event["kind"] != "register_write" for event in ir["events"])


def test_register_read_hazard_does_not_retroactively_resolve_history() -> None:
    ir = analyze_text("move 7,R0\nmove R0,R1\nnop\nstop")
    assert _writes(ir, "R0")[0]["value"]["value"] == 7
    assert _writes(ir, "R1")[0]["value"]["kind"] == "unknown"
    assert any(diagnostic["category"] == "register_not_ready" for diagnostic in ir["diagnostics"])


def test_last_write_without_following_instruction_has_unknown_availability() -> None:
    ir = analyze_text("move 7,R0")
    write = ir["register_history"][0]
    assert write["value"]["value"] == 7
    assert write["time"]["kind"] == "unknown"
    assert write["confidence"] == "unknown"


def test_runtime_and_unmodeled_register_results_remain_nonconcrete() -> None:
    ir = analyze_text("fb_pop_data 0,R0\nnop\nmulu16 R0,2,R1\nnop\nstop")
    assert _writes(ir, "R0")[0]["value"]["kind"] == "runtime_dependent"
    assert _writes(ir, "R0")[0]["confidence"] == "runtime_dependent"
    assert _writes(ir, "R1")[0]["value"]["kind"] == "unknown"


def test_compact_loop_keeps_an_unknown_history_gap_then_restores_proven_affine_values() -> None:
    ir = analyze_text(
        "move 3,R0\nmove 0,R1\nnop\nbody: add R1,1,R1\nnop\n"
        "loop R0,@body\nmove R1,R2\nnop\nstop"
    )
    counter = _writes(ir, "R0")
    assert [write["value"]["kind"] for write in counter] == ["concrete", "unknown", "concrete"]
    assert counter[-1]["value"]["value"] == 0
    assert counter[-1]["time"]["value"] == 112
    arithmetic = _writes(ir, "R1")
    assert arithmetic[1]["value"]["value"] == 1
    invalidated = next(write for write in arithmetic if write["meta"].get("invalidated"))
    assert invalidated["value"]["kind"] == "unknown"
    assert invalidated["time"]["value"] == 52
    assert arithmetic[-1]["meta"]["affine"] is True
    assert arithmetic[-1]["value"]["value"] == 3
    assert arithmetic[-1]["time"]["value"] == 112
    assert _writes(ir, "R2")[0]["value"]["value"] == 3


def test_one_iteration_loop_keeps_body_value_and_records_zero_counter() -> None:
    ir = analyze_text("move 1,R0\nnop\nbody: move 9,R1\nloop R0,@body\nstop")
    assert _writes(ir, "R1")[0]["value"]["value"] == 9
    assert _writes(ir, "R1")[0]["time"]["value"] == 16
    assert _writes(ir, "R0")[-1]["value"]["value"] == 0
    assert not any(write["meta"].get("invalidated") for write in ir["register_history"])


def test_unreset_inner_loop_preserves_observed_decrements_before_unknown_tail() -> None:
    source = "move 2,R0\nmove 2,R1\nnop\nouter: nop\ninner: nop\nloop R1,@inner\nloop R0,@outer\nstop"
    ir = analyze_text(source)
    child, = [event for event in ir["events"] if event["kind"] == "loop_block"]
    assert child["meta"]["count"] == 2
    inner = _writes(ir, "R1")
    first_completion, = [write for write in inner if write["meta"].get("loop_complete")]
    assert first_completion["event_id"] == child["meta"]["loop_q1_issue_event_id"]
    assert (first_completion["value"]["value"], first_completion["time"]["value"]) == (0, 52)

    outer = _writes(ir, "R0")
    first_decrement, = [write for write in outer if write["meta"].get("op") == "loop"]
    assert (first_decrement["value"]["value"], first_decrement["time"]["value"]) == (1, 76)
    assert not first_decrement["meta"].get("loop_complete")
    outer_issue = next(event for event in ir["events"] if event["id"] == first_decrement["event_id"])
    assert (outer_issue["t0"]["value"], outer_issue["t1"]["value"], outer_issue["meta"]["branch_taken"]) == (52, 76, True)
    boundary, = [event for event in ir["events"] if event["kind"] == "unknown_region"]
    assert boundary["source"]["line"] == 7 and boundary["t0"]["value"] == 0
    assert boundary["meta"]["q1_issue_event_id"] == first_decrement["event_id"]
    assert boundary["meta"]["reason"] == "analytical_nested_loop_counter_varies"
    assert boundary["meta"]["q1_proof_cutoff"] is True
    assert any(item["category"] == "analysis_incomplete" and boundary["id"] in item["related_events"]
               for item in ir["diagnostics"])
    assert all(writes[-1]["meta"]["invalidated"] and writes[-1]["time"]["value"] == 76 for writes in (inner, outer))
    assert not any(event["kind"] == "stop" for event in ir["events"])

    concrete = analyze_text(source, execution_mode="concrete", max_steps=16)
    execution, = concrete["execution"].values()
    assert execution["status"] == "step_limit"
    assert _writes(concrete, "R0")[-1]["value"]["value"] == 1
    wrap, = [write for write in _writes(concrete, "R1") if write["value"].get("value") == 0xFFFFFFFF]
    assert wrap["meta"]["previous_value"]["value"] == 0 and wrap["time"]["value"] == 108
    assert not any(event["kind"] == "stop" for event in concrete["events"])


def test_loop_counter_history_carries_assumed_branch_context() -> None:
    ir = analyze_text(
        "move 1,R0\nnop\njlt R5,1,@body\nstop\nbody: nop\nloop R0,@body\nstop",
        branch_policy="assume_true",
    )
    counter = _writes(ir, "R0")[-1]
    assert counter["value"]["value"] == 0
    assert counter["confidence"] == "assumed"
    assert counter["meta"]["branch_assumptions"][0]["assumed_branch_path"] == "taken"


def test_comparison_paths_keep_register_history_separate_and_link_matching_events() -> None:
    ir = analyze_text(
        "move 7,R0\nnop\njlt R5,1,@done\nmove 2,R0\nnop\ndone: stop",
        branch_policy="explore_both_with_depth_limit",
    )
    path_write = next(write for write in _writes(ir, "R0") if write["meta"].get("branch_comparison_path"))
    assert path_write["meta"]["branch_comparison_path"] == "fallthrough"
    assert path_write["confidence"] == "assumed"
    assert any(event["id"] == path_write["event_id"] for event in ir["events"])
    assert any(write["meta"].get("invalidated") for write in _writes(ir, "R0"))


def test_collapsed_branch_invalidates_prior_register_values() -> None:
    source = "move 7,R0\nnop\njlt R5,1,@done\nmove 2,R0\nnop\ndone: stop"
    branch_id = "seq0:branch:branch.q1asm:3:jlt:done"
    state = interpret_program(
        parse_q1asm(source, file="branch.q1asm"),
        sequencer_id="seq0",
        branch_assumptions={branch_id: "collapsed"},
    )
    ir = timeline_ir_from_states([state])
    invalidation = _writes(ir, "R0")[-1]
    assert invalidation["value"]["kind"] == "unknown"
    assert invalidation["meta"]["invalidated"] is True
    assert invalidation["time"]["value"] == 32


def test_register_history_serializes_each_sequencer_and_keeps_local_time() -> None:
    ir = analyze_documents(
        {"seq0.q1asm": "move 7,R0\nnop\nstop", "seq1.q1asm": "move 8,R0\nnop\nstop"},
        alignment_mode="manual:100",
    )
    serialized = json.loads(timeline_ir_to_json(ir))
    assert {write["sequencer_id"] for write in serialized["register_history"]} == {"seq0", "seq1"}
    assert {write["time"]["value"] for write in serialized["register_history"]} == {8}
    assert all(item["offset"] == 100 for item in serialized["alignment"].values())
