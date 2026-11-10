from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from q1timeline.analysis.api import analyze_documents, analyze_text
from q1timeline.ir.serialize import timeline_ir_to_json


LOOP_PROGRAM = (
    "move 3,R0\nmove 0,R1\nnop\n"
    "body: add R1,1,R1\nnop\nloop R0,@body\n"
    "move R1,R2\nnop\nstop\n"
)


@pytest.mark.parametrize("first_import", [
    "from q1timeline.ir import timeline_ir_to_json",
    "from q1timeline.analysis import analyze_text",
    "from q1timeline.ir.serialize import timeline_ir_to_json",
])
def test_public_analysis_and_ir_import_independently_in_a_fresh_process(first_import: str) -> None:
    program = f"""\
import json
import sys
sys.path.insert(0, sys.argv[1])
{first_import}
from q1timeline.analysis import analyze_documents, analyze_text
from q1timeline.ir import timeline_ir_to_json
source = 'wait 4\\nstop'
for ir in (analyze_text(source), analyze_documents({{'sample.q1asm': source}}, alignment_mode='none')):
    assert any(event['kind'] == 'wait' for event in json.loads(timeline_ir_to_json(ir))['events'])
"""
    source_root = Path(__file__).resolve().parents[1] / "src"
    result = subprocess.run([sys.executable, "-c", program, str(source_root)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


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
    assert result["value"]["kind"] == "concrete" and result["value"]["value"] == 3
    assert any(write["meta"].get("invalidated") for write in ir["register_history"])
    assert any(write["register"] == "R1" and write["meta"].get("affine") for write in ir["register_history"])


@pytest.mark.parametrize(("max_steps", "count", "complete"), [(100, 3, True), (11, ">=2", False)])
def test_concrete_rt_loop_from_backward_conditional_branch(max_steps: int, count: int | str, complete: bool) -> None:
    ir = analyze_text(
        "move 3,R0\nnop\nbody: play 0,1,100\nsub R0,1,R0\nnop\njnz @body\nstop",
        execution_mode="concrete", max_steps=max_steps,
    )
    loop, = [event for event in ir["events"] if event["kind"] == "loop_block"]
    assert loop["meta"]["count"] == count
    assert loop["meta"]["complete"] is complete
    assert loop["t1"]["value"] == (300 if complete else 200)


def test_concrete_does_not_group_identical_commands_at_distinct_source_locations() -> None:
    ir = analyze_text("wait 100\nwait 100\nwait 100\nstop", execution_mode="concrete")
    assert not any(event["kind"] == "loop_block" for event in ir["events"])


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
