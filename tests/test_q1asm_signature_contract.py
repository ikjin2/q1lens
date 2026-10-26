"""Check a saved assembler oracle without requiring Qblox software in CI."""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from q1timeline.analysis.api import analyze_documents
from q1timeline.q1asm.instruction_table import INSTRUCTION_TABLE


CONTRACT = json.loads((Path(__file__).parent / "fixtures" / "q1asm_signatures.json").read_text(encoding="utf-8"))
ACCEPTED = CONTRACT["accepted"]


def test_signature_contract_covers_every_supported_opcode() -> None:
    assert set(ACCEPTED) == set(INSTRUCTION_TABLE)
    for op, spec in INSTRUCTION_TABLE.items():
        assert {len(signature.args) for signature in spec.signatures} == {len(pattern) for pattern in ACCEPTED[op]}, op
    assert all(patterns and len(patterns) == len(set(patterns)) for patterns in ACCEPTED.values())
    assert all(set(pattern) <= {"I", "R"} for patterns in ACCEPTED.values() for pattern in patterns)
    case_count = sum(sum(2 ** count for count in range(max(map(len, patterns)) + 2)) for patterns in ACCEPTED.values())
    assert case_count == CONTRACT["reference"]["case_count"]


@pytest.mark.parametrize("op", sorted(ACCEPTED))
def test_source_signatures_match_the_independent_assembler_contract(op: str) -> None:
    accepted = set(ACCEPTED[op])
    accepted_counts = {len(pattern) for pattern in accepted}
    for count in range(max(map(len, accepted)) + 2):
        for kinds in itertools.product("IR", repeat=count):
            pattern = "".join(kinds)
            operands = ",".join("0" if kind == "I" else "R1" for kind in kinds)
            ir = analyze_documents(
                {"contract.q1asm": f"stop\n{op} {operands}\n"},
                alignment_mode="none",
            )
            errors = [item for item in ir["diagnostics"] if item["severity"] == "error"]
            if pattern in accepted:
                assert errors == [], (op, pattern, errors)
            else:
                category = "invalid_argument_count" if count not in accepted_counts else "invalid_argument_type"
                assert any(item["category"] == category for item in errors), (op, pattern, errors)
