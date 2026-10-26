"""Deterministic malformed-input and non-evaluation checks for arbitrary source."""
import itertools
import json

import pytest

from q1timeline.analysis.api import analyze_documents
from q1timeline.q1asm.parser import parse_q1asm


@pytest.mark.parametrize("execution_mode", ["analysis", "concrete", "simulation"])
def test_short_operand_mutations_preserve_recovery_and_serializable_diagnostics(execution_mode: str) -> None:
    alphabet = "{}()[],$@'0"
    tokens = ("".join(parts) for size in (1, 2, 3) for parts in itertools.product(alphabet, repeat=size))
    for token in tokens:
        source = f"stop\nmove {token},R0\nrecovery_stop: stop"
        program = parse_q1asm(source, file="mutation.q1asm")
        assert program.labels["recovery_stop"] == len(program.instructions) - 1, token
        assert [instruction.pc for instruction in program.instructions] == list(range(len(program.instructions))), token
        ir = analyze_documents({"mutation.q1asm": source}, alignment_mode="none", execution_mode=execution_mode)
        assert json.loads(json.dumps(ir, allow_nan=False)) == ir, token
        assert all(item.get("source") is None or item["source"]["file"] == "mutation.q1asm"
                   for item in ir["diagnostics"]), token


@pytest.mark.parametrize("execution_mode", ["analysis", "concrete", "simulation"])
@pytest.mark.parametrize("expression", [
    "1 / 0",
    "(_ for _ in ()).throw(AssertionError('templates must not execute'))",
    "R" + "1" * 5000,
    "(" * 1000 + "VALUE" + ")" * 1000,
], ids=["division", "exception", "long-identifier", "deep-expression"])
def test_template_inspection_never_evaluates_or_crashes(execution_mode: str, expression: str) -> None:
    source = "stop\nwait {" + expression + "}\n"
    ir = analyze_documents({"template.q1asm": source}, alignment_mode="none", execution_mode=execution_mode)

    assert json.loads(json.dumps(ir, allow_nan=False)) == ir
    assert not any(item["severity"] == "error" for item in ir["diagnostics"])


@pytest.mark.parametrize("execution_mode", ["analysis", "concrete", "simulation"])
def test_supplied_opaque_parameter_key_does_not_require_python_expression_syntax(execution_mode: str) -> None:
    ir = analyze_documents(
        {"parameter.q1asm": "wait {pulse duration}\nstop"},
        params={"pulse duration": 4}, alignment_mode="none", execution_mode=execution_mode,
    )

    assert ir["diagnostics"] == []
    assert [event["duration"]["value"] for event in ir["events"] if event["kind"] == "wait"] == [4]
