from __future__ import annotations

import json

import pytest

from q1timeline.analysis.api import analyze_documents, analyze_text
from q1timeline.ir.serialize import timeline_ir_to_json


LOOP_PROGRAM = (
    "move 3,R0\nmove 0,R1\nnop\n"
    "body: add R1,1,R1\nnop\nloop R0,@body\n"
    "move R1,R2\nnop\nstop\n"
)


def test_concrete_api_serializes_executed_iterations_and_register_evidence() -> None:
    ir = json.loads(timeline_ir_to_json(analyze_text(
        LOOP_PROGRAM, file="counter.q1asm", execution_mode="concrete",
    )))
    execution = ir["execution"]["counter"]
    assert execution["status"] == "completed"
    assert execution["timing_model"] == "analytical"
    issues = [event for event in ir["events"] if event["kind"] == "q1_issue"]
    assert execution["steps"] == len(issues)
    assert [event["meta"]["execution_index"] for event in issues] == list(range(len(issues)))
    assert len({event["id"] for event in ir["events"]}) == len(ir["events"])
    increments = [event for event in issues if event["meta"]["op"] == "add"]
    assert [event["meta"]["register_reads"]["R1"]["value"] for event in increments] == [0, 1, 2]
    writes = [write for write in ir["register_history"] if write["register"] == "R1"]
    assert [write["value"]["value"] for write in writes] == [0, 1, 2, 3]
    assert [write["meta"]["previous_value"]["value"] for write in writes[1:]] == [0, 1, 2]
    event_ids = {event["id"] for event in issues}
    assert all(write["event_id"] in event_ids for write in writes)
    assert all(write["meta"].get("provenance") for write in writes)


def test_default_analysis_retains_compact_loop_behavior() -> None:
    ir = analyze_text(LOOP_PROGRAM)
    assert "execution" not in ir
    assert any(event["kind"] == "loop_block" for event in ir["events"])
    result = [write for write in ir["register_history"] if write["register"] == "R2"][-1]
    assert result["value"]["kind"] == "unknown"


def test_document_execution_reports_independent_partial_results() -> None:
    ir = analyze_documents({
        "complete.q1asm": "move 9,R0\nnop\nstop\n",
        "limited.q1asm": "again: nop\njmp @again\n",
        "needs_input.q1asm": "wait R7\nstop\n",
    }, execution_mode="concrete", max_steps=6, alignment_mode="none")
    assert ir["execution"]["complete"]["status"] == "completed"
    assert ir["execution"]["limited"]["status"] == "step_limit"
    assert ir["execution"]["limited"]["steps"] == 6
    assert ir["execution"]["needs_input"]["status"] == "blocked"
    assert ir["execution"]["needs_input"]["steps"] == 0
    assert all(item["timing_model"] == "analytical" for item in ir["execution"].values())
    json.loads(timeline_ir_to_json(ir))


@pytest.mark.parametrize("options", [
    {"execution_mode": "simulator"},
    {"execution_mode": []},
    {"max_steps": 0},
    {"max_steps": -1},
    {"max_steps": True},
    {"max_steps": 1.5},
])
def test_execution_options_are_validated_even_with_no_documents(options: dict) -> None:
    with pytest.raises(ValueError):
        analyze_documents({}, **options)
    with pytest.raises(ValueError):
        analyze_text("stop", **options)
