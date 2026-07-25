import json
import re
from pathlib import Path

from q1timeline.q1asm.instruction_table import SUPPORTED_OPS


def test_integrated_q1asm_grammar_highlights_supported_opcodes() -> None:
    grammar = json.loads(
        Path("vscode-extension/src/q1timeline/syntaxes/q1asm.tmLanguage.json").read_text(encoding="utf-8")
    )
    instruction_patterns = [
        (pattern["name"], re.compile(pattern["match"]))
        for pattern in grammar["repository"]["instructions"]["patterns"]
    ]

    missing = sorted(
        op
        for op in SUPPORTED_OPS
        if not any(pattern.search(op) for _, pattern in instruction_patterns)
    )

    assert missing == []


def test_integrated_q1asm_grammar_groups_opcodes_by_command_family() -> None:
    grammar = json.loads(
        Path("vscode-extension/src/q1timeline/syntaxes/q1asm.tmLanguage.json").read_text(encoding="utf-8")
    )
    instruction_patterns = [
        (pattern["name"], re.compile(pattern["match"]))
        for pattern in grammar["repository"]["instructions"]["patterns"]
    ]

    expected_scopes = {
        "entity.name.function.q1asm",
        "constant.language.timing.q1asm",
        "string.other.feedback.q1asm",
        "storage.modifier.state.q1asm",
        "keyword.control.flow.q1asm",
        "keyword.operator.alu.q1asm",
        "support.constant.system.q1asm",
    }
    scopes = {scope for scope, _ in instruction_patterns}

    assert expected_scopes <= scopes


def test_integrated_q1asm_grammar_assigns_each_opcode_to_one_command_family() -> None:
    grammar = json.loads(
        Path("vscode-extension/src/q1timeline/syntaxes/q1asm.tmLanguage.json").read_text(encoding="utf-8")
    )
    instruction_patterns = [
        (pattern["name"], re.compile(pattern["match"]))
        for pattern in grammar["repository"]["instructions"]["patterns"]
    ]

    duplicate_matches = {
        op: [scope for scope, pattern in instruction_patterns if pattern.search(op)]
        for op in SUPPORTED_OPS
    }
    duplicate_matches = {
        op: scopes
        for op, scopes in duplicate_matches.items()
        if len(scopes) != 1
    }

    assert duplicate_matches == {}
