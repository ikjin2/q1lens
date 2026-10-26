import json
import sys

import pytest

from q1timeline.analysis.api import analyze_documents
from q1timeline.q1asm.ast import SourceLocation
from q1timeline.q1asm.parser import parse_q1asm


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_labels_comments_and_missing_final_newline_preserve_pc_and_source(newline: str) -> None:
    lines = [
        ".DEF DELAY 8",
        "# Standalone labels can precede comments and blank lines.",
        "entry:",
        "// Comment before the first instruction.",
        "",
        "    wait $DELAY # body",
        "  again : jmp @entry",
    ]
    program = parse_q1asm(newline.join(lines), file="labels.q1asm")

    assert program.diagnostics == []
    assert program.labels == {"entry": 0, "again": 1}
    assert [(instruction.pc, instruction.op) for instruction in program.instructions] == [(0, "wait"), (1, "jmp")]
    assert program.instructions[0].source == SourceLocation("labels.q1asm", 6, 5, lines[5])
    assert program.instructions[1].source == SourceLocation("labels.q1asm", 7, 11, lines[6])
    assert program.instructions[1].label == "again"
    assert program.instructions[1].args[0].kind == "label"
    assert program.instructions[1].args[0].value == "entry"


@pytest.mark.parametrize(
    ("operand", "kind", "value"),
    [
        ("-0XfF", "imm", -255),
        ("r0xF", "reg", "R15"),
        ("$DURATION", "symbol", "$DURATION"),
        ("{ max(DELAY, 4) + {OFFSET} // 2 }", "placeholder", "max(DELAY, 4) + {OFFSET} // 2"),
    ],
)
def test_operand_normalization_preserves_original_spelling(operand: str, kind: str, value: int | str) -> None:
    program = parse_q1asm(f"move {operand},R0\n")

    assert program.diagnostics == []
    assert len(program.instructions[0].args) == 2
    argument = program.instructions[0].args[0]
    assert (argument.kind, argument.value, argument.raw) == (kind, value, operand)


@pytest.mark.parametrize(
    "instruction",
    [
        "move 1 R0",
        "move 1,,R0",
        "move ,1,R0",
        "wait +4",
        "wait 0b100",
        "wait 0xGG",
        "move 1,R0xGG",
        "WAIT 4",
        "wait $",
        "wait {}",
        "wait {COUNT",
        "wait @",
        "move @123,R0",
    ],
)
def test_rejected_labeled_instruction_does_not_bind_label_or_consume_pc(instruction: str) -> None:
    raw = f"  bad: {instruction}"
    program = parse_q1asm(f"{raw}\nnext: stop", file="invalid.q1asm")

    assert program.labels == {"next": 0}
    assert [(item.pc, item.op) for item in program.instructions] == [(0, "stop")]
    diagnostic, = program.diagnostics
    assert (diagnostic.severity, diagnostic.category) == ("error", "syntax_error")
    assert diagnostic.source.file == "invalid.q1asm"
    assert diagnostic.source.line == 1
    assert diagnostic.source.raw == raw


def test_duplicate_label_keeps_first_target_and_reports_duplicate_source() -> None:
    program = parse_q1asm("entry: wait 4\nentry: wait 8\nstop", file="duplicate.q1asm")

    assert program.labels == {"entry": 0}
    diagnostic, = program.diagnostics
    assert diagnostic.category == "syntax_error"
    assert diagnostic.details == {"label": "entry"}
    assert diagnostic.source == SourceLocation("duplicate.q1asm", 2, 1, "entry: wait 8")


def test_duplicate_def_keeps_first_value_and_reports_duplicate_source() -> None:
    program = parse_q1asm(".DEF DELAY 4\n.DEF DELAY 8\nwait $DELAY", file="duplicate.q1asm")

    assert program.defs["DELAY"].value == 4
    diagnostic, = program.diagnostics
    assert diagnostic.category == "syntax_error"
    assert diagnostic.details == {"name": "DELAY"}
    assert diagnostic.source == SourceLocation("duplicate.q1asm", 2, 1, ".DEF DELAY 8")


@pytest.mark.parametrize(
    "directive",
    [
        ".DEF DELAY",
        ".DEF 1DELAY 4",
        ".DEFAULT DELAY 4",
        ".DEF DELAY 4 8",
        ".DEF DELAY R64",
        ".DEF DELAY r0",
        ".DEF DELAY other_name",
    ],
)
def test_malformed_def_does_not_create_binding_or_consume_pc(directive: str) -> None:
    program = parse_q1asm(f"{directive}\nstop")

    assert program.defs == {}
    assert [(item.pc, item.op) for item in program.instructions] == [(0, "stop")]
    diagnostic, = program.diagnostics
    assert (diagnostic.severity, diagnostic.category) == ("error", "syntax_error")
    assert diagnostic.source.line == 1
    assert diagnostic.source.raw == directive


def test_def_register_and_template_values_remain_operands_without_advancing_pc() -> None:
    program = parse_q1asm(
        ".DEF COUNTER R63\n"
        ".DEF DELAY { max(BASE, 4) // 2 }\n"
        "entry: move 1,$COUNTER\n"
        "wait $DELAY\n"
    )

    assert program.diagnostics == []
    assert program.labels == {"entry": 0}
    assert (program.defs["COUNTER"].kind, program.defs["COUNTER"].value) == ("reg", "R63")
    assert (program.defs["DELAY"].kind, program.defs["DELAY"].value) == ("placeholder", "max(BASE, 4) // 2")
    assert [instruction.pc for instruction in program.instructions] == [0, 1]


@pytest.mark.parametrize("value", ["4", "R63", "{duration}"])
def test_alias_must_be_defined_before_use_even_in_unreachable_code(value: str) -> None:
    program = parse_q1asm(f"stop\nmove $VALUE,R0\n.DEF VALUE {value}\n", file="alias.q1asm")

    diagnostic, = program.diagnostics
    assert (diagnostic.severity, diagnostic.category) == ("error", "syntax_error")
    assert diagnostic.source == SourceLocation("alias.q1asm", 2, 1, "move $VALUE,R0")
    assert diagnostic.details == {"name": "VALUE"}


def test_external_parameter_reference_does_not_require_a_def() -> None:
    program = parse_q1asm("wait $DURATION\nstop")

    assert program.diagnostics == []


def test_repeated_forward_alias_on_one_line_has_one_diagnostic() -> None:
    program = parse_q1asm("set_awg_gain $GAIN,$GAIN\n.DEF GAIN 4\nstop")

    diagnostic, = program.diagnostics
    assert diagnostic.category == "syntax_error"
    assert diagnostic.details == {"name": "GAIN"}


@pytest.mark.parametrize("body", [".DEF VALUE 4", ".DEF VALUE"])
def test_labeled_directive_does_not_leave_a_phantom_label(body: str) -> None:
    program = parse_q1asm(f"bad: {body}\nnext: stop")

    assert program.labels == {"next": 0}
    assert program.defs == {}
    assert [item.category for item in program.diagnostics] == ["syntax_error"]


def test_def_label_allows_forward_reference_but_requires_a_definition() -> None:
    valid = parse_q1asm(".DEF DEST @later\njmp $DEST\nlater: stop")
    invalid = parse_q1asm(".DEF DEST @missing\nstop", file="alias.q1asm")

    assert valid.diagnostics == []
    diagnostic, = invalid.diagnostics
    assert (diagnostic.severity, diagnostic.category) == ("error", "undefined_label")
    assert diagnostic.source == SourceLocation("alias.q1asm", 1, 1, ".DEF DEST @missing")


@pytest.mark.parametrize("operand", [
    "{{COUNT}", "{COUNT}}", "{COUNT}suffix", "{LEFT} {RIGHT}",
    "{ fn(1, 2 }", "{ [1, 2) }", "{ {'key': [1, 2} }", "{ 'unterminated }",
    "COUNT}", "[COUNT]", "(COUNT)",
    "$1COUNT", "$$COUNT", "$COUNT+4", "$COUNT..value", "@target.extra",
])
def test_malformed_delimiters_and_references_recover_at_next_source_line(operand: str) -> None:
    raw = f"bad: move {operand},R0"
    program = parse_q1asm(f"{raw}\nnext: stop", file="delimiters.q1asm")

    assert program.labels == {"next": 0}
    assert [(item.pc, item.op) for item in program.instructions] == [(0, "stop")]
    diagnostic, = program.diagnostics
    assert (diagnostic.severity, diagnostic.category) == ("error", "syntax_error")
    assert diagnostic.source == SourceLocation("delimiters.q1asm", 1, 6, raw)


@pytest.mark.parametrize("expression", [
    "fn([1, 2], {'key': 4})",
    "{INNER} + 4",
    "[{'nested': [1, 2]}, 4][0]['nested'][1]",
    "lookup('},#;(', 4)",
    'lookup("\\\"},#;(", 4)',
    "lookup('''},#;(''', 4)",
    "DURATION // 2",
])
@pytest.mark.parametrize("directive", [False, True])
def test_nested_and_quoted_template_tokens_remain_one_operand(expression: str, directive: bool) -> None:
    operand = "{ " + expression + " }"
    raw = f".DEF VALUE {operand}" if directive else f"move {operand},R0 # comment"
    source = raw + ("\nmove $VALUE,R0" if directive else "")
    program = parse_q1asm(source, file="templates.q1asm")

    assert program.diagnostics == []
    argument = program.defs["VALUE"] if directive else program.instructions[0].args[0]
    assert (argument.kind, argument.value, argument.raw) == ("placeholder", expression, operand)
    assert len(program.instructions[0].args) == 2


@pytest.mark.parametrize("body", ["wait,4", "wait(4)", "bad.op 4", "9wait 4", "second: wait 4", ".WORD 4", ".def VALUE 4"])
def test_malformed_opcode_or_directive_does_not_bind_preceding_label(body: str) -> None:
    program = parse_q1asm(f"bad: {body}\nnext: stop")

    assert program.labels == {"next": 0}
    assert [(item.pc, item.op) for item in program.instructions] == [(0, "stop")]
    assert [item.category for item in program.diagnostics] == ["syntax_error"]


@pytest.mark.parametrize("source", ["nop,,,\nstop", "wait 4,,,\nstop", "stop,garbage"])
def test_assembler_empty_comma_and_stop_suffix_leniency_is_preserved(source: str) -> None:
    program = parse_q1asm(source)

    assert program.diagnostics == []
    assert [instruction.op for instruction in program.instructions][-1] == "stop"


def test_valid_dotted_parameter_tokens_remain_symbolic_operands() -> None:
    program = parse_q1asm("move $scenario.value,R0\nwait scenario.duration")

    assert program.diagnostics == []
    assert [(item.args[0].kind, item.args[0].value) for item in program.instructions] == [
        ("symbol", "$scenario.value"), ("symbol", "scenario.duration"),
    ]


def test_def_sources_keep_first_successful_definition_and_exact_source() -> None:
    program = parse_q1asm(
        "  .DEF VALUE 4 # original\n.DEF VALUE 8\n.DEF BROKEN\nwait $VALUE",
        file="definitions.q1asm",
    )

    assert program.def_sources == {"VALUE": SourceLocation("definitions.q1asm", 1, 3, "  .DEF VALUE 4 # original")}


@pytest.mark.parametrize("operand", [
    "1" * 5000, "R" + "1" * 5000, "0x" + "f" * 5000, "R0x" + "f" * 5000,
], ids=["oversized-integer", "oversized-register", "oversized-hex-integer", "oversized-hex-register"])
def test_oversized_numeric_tokens_are_rejected_without_crashing(operand: str) -> None:
    source = f"bad: move {operand},R0\nnext: stop"
    original_limit = sys.get_int_max_str_digits() if hasattr(sys, "get_int_max_str_digits") else None
    limits = [original_limit, 0] if original_limit not in (None, 0) else [original_limit]
    try:
        for limit in limits:
            if limit is not None:
                sys.set_int_max_str_digits(limit)
            program = parse_q1asm(source, file="oversized.q1asm")

            assert program.labels["next"] == len(program.instructions) - 1
            assert [item.pc for item in program.instructions] == list(range(len(program.instructions)))
            ir = analyze_documents({"oversized.q1asm": source}, alignment_mode="none")
            errors = [item for item in ir["diagnostics"] if item["severity"] == "error"]
            assert errors
            assert all(item["category"] in {"syntax_error", "invalid_argument_type", "invalid_argument_value"}
                       and item["source"]["line"] == 1 for item in errors)
            assert json.loads(json.dumps(ir, allow_nan=False)) == ir
    finally:
        if original_limit is not None:
            sys.set_int_max_str_digits(original_limit)
