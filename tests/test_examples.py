from __future__ import annotations

import json
from pathlib import Path

from qbstimeline.cli import main


def test_basic_transmon_example_analyzes() -> None:
    root = Path(__file__).resolve().parents[1]
    project = root / "examples" / "basic-transmon" / "qbstimeline.yml"
    out = root / "examples" / "basic-transmon" / ".qbs_timeline" / "qbs_ir.json"

    exit_code = main(["analyze", "--project", str(project), "--out", str(out)])

    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "ok"
    assert payload["schedule"]["name"] == "basic transmon demo"
    assert payload["timing_table"][0]["operation"] == "Reset(q0)"
    assert payload["q1asm_programs"][0]["sequencer_id"] == "cluster0_module2_seq0"
    assert "cluster0_module2_seq0" in payload["q1asm_by_sequencer"]


def test_basic_transmon_example_emits_symbolic_pulses(tmp_path: Path) -> None:
    project_file = Path("examples/basic-transmon/qbstimeline.yml")
    out_file = tmp_path / "qbs_ir.json"

    exit_code = main(["analyze", "--project", str(project_file), "--out", str(out_file)])

    assert exit_code == 0
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["symbolic_values"][0]["label"] == "T_TOTAL"
    assert any(row["kind"] == "DRAGPulse" for row in payload["symbolic_pulses"])
    assert any(row["role"] == "acquisition" for row in payload["symbolic_pulses"])
    assert (
        payload["q1asm_provenance"][0]["operand_mappings"][1]["source_expression"]
        == "T_TOTAL - 4 ns"
    )


def test_two_qubit_entangling_example_emits_multiple_symbolic_lanes(tmp_path: Path) -> None:
    payload = _analyze_project_to_payload(
        Path("examples/two-qubit-entangling/qbstimeline.yml"),
        tmp_path,
    )
    lanes = {row["lane"] for row in payload["symbolic_pulses"]}
    value_labels = {row["label"] for row in payload["symbolic_values"]}
    provenance_expressions = [
        mapping["source_expression"]
        for row in payload["q1asm_provenance"]
        for mapping in row["operand_mappings"]
        if "source_expression" in mapping
    ]

    assert payload["schedule"]["name"] == "two-qubit entangling demo"
    assert {"T_X90", "T_CZ", "READOUT_DURATION", "AMP_CZ"} <= value_labels
    assert {"q0:mw / q0.01", "q1:mw / q1.01", "q0_q1:flux / cz", "q0:res / q0.ro", "q1:res / q1.ro"} <= lanes
    assert any(row["kind"] == "CZFluxPulse" for row in payload["symbolic_pulses"])
    assert sum(row["role"] == "acquisition" for row in payload["symbolic_pulses"]) == 2
    assert "T_CZ - 8 ns setup" in provenance_expressions


def test_two_qubit_entangling_symbolic_blocks_fit_operation_windows(tmp_path: Path) -> None:
    payload = _analyze_project_to_payload(
        Path("examples/two-qubit-entangling/qbstimeline.yml"),
        tmp_path,
    )
    operations_by_schedulable = {row["id"]: row for row in payload["operations"]}

    for block in payload["symbolic_pulses"]:
        operation = operations_by_schedulable[block["schedulable_id"]]
        operation_start = operation["abs_time"]
        operation_end = operation_start + operation["duration"]
        block_start = block["abs_time"]
        block_end = block_start + block["duration"]

        assert operation_start <= block_start, block
        assert block_end <= operation_end, block


def test_two_qubit_entangling_provenance_lines_match_q1asm_instructions(tmp_path: Path) -> None:
    payload = _analyze_project_to_payload(
        Path("examples/two-qubit-entangling/qbstimeline.yml"),
        tmp_path,
    )

    for row in payload["q1asm_provenance"]:
        program_lines = payload["q1asm_by_sequencer"][row["sequencer_id"]].splitlines()
        for mapping in row["operand_mappings"]:
            assert program_lines[mapping["line"] - 1].startswith(mapping["instruction"]), mapping


def _analyze_project_to_payload(project_file: Path, tmp_path: Path) -> dict:
    out_file = tmp_path / "qbs_ir.json"

    exit_code = main(["analyze", "--project", str(project_file), "--out", str(out_file)])

    assert exit_code == 0
    return json.loads(out_file.read_text(encoding="utf-8"))
