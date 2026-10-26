"""Replay saved immediate-field assembler results without a vendor CI dependency."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from q1timeline.analysis.api import analyze_documents
from q1timeline.q1asm.instruction_table import INSTRUCTION_TABLE


FIXTURES = Path(__file__).parent / "fixtures"
CONTRACT = json.loads((FIXTURES / "q1asm_operand_boundaries.json").read_text(encoding="utf-8"))
SIGNATURES = json.loads((FIXTURES / "q1asm_signatures.json").read_text(encoding="utf-8"))
ROWS_BY_OP = {op: [row for row in CONTRACT["rows"] if row["opcode"] == op] for op in SIGNATURES["accepted"]}


def _instruction(row: dict, value: int) -> str:
    args = ["0" if kind == "I" else "R1" for kind in row["pattern"]]
    args[row["argument_index"]] = str(value)
    return row["opcode"] + " " + ",".join(args)


def test_operand_contract_covers_every_immediate_signature_slot() -> None:
    assert set(ROWS_BY_OP) == set(INSTRUCTION_TABLE)
    expected = {(op, pattern, index) for op, patterns in SIGNATURES["accepted"].items()
                for pattern in patterns for index, kind in enumerate(pattern) if kind == "I"}
    actual = {(row["opcode"], row["pattern"], row["argument_index"]) for row in CONTRACT["rows"]}
    assert actual == expected
    assert len(CONTRACT["rows"]) == len(actual) == CONTRACT["reference"]["slot_count"]
    assert CONTRACT["reference"]["executable_sha256"] == SIGNATURES["reference"]["executable_sha256"]
    assert all(set(row["accepted_values"]) <= set(CONTRACT["candidate_values"]) for row in CONTRACT["rows"])
    sources: dict[str, bool] = {}
    for row in CONTRACT["rows"]:
        for value in CONTRACT["candidate_values"]:
            source = _instruction(row, value)
            accepted = value in row["accepted_values"]
            assert sources.setdefault(source, accepted) == accepted, source
    assert len(sources) == CONTRACT["reference"]["case_count"]


@pytest.mark.parametrize("op", sorted(op for op, rows in ROWS_BY_OP.items() if rows))
def test_immediate_operands_match_the_independent_assembler_contract(op: str) -> None:
    errors_by_source: dict[str, list[dict]] = {}
    for row in ROWS_BY_OP[op]:
        accepted = set(row["accepted_values"])
        for value in CONTRACT["candidate_values"]:
            source = _instruction(row, value)
            if source not in errors_by_source:
                ir = analyze_documents({"contract.q1asm": f"stop\n{source}\n"}, alignment_mode="none")
                errors_by_source[source] = [item for item in ir["diagnostics"] if item["severity"] == "error"]
            errors = errors_by_source[source]
            context = (op, row["pattern"], row["argument_index"], value, errors)
            if value in accepted:
                assert errors == [], context
            else:
                assert any(item["category"] == "invalid_argument_value" for item in errors), context
