import json
from pathlib import Path

import pytest

from q1timeline.analysis.api import analyze_documents
from q1timeline.cli import main
from q1timeline.diagnostic_catalog import describe_diagnostic
from q1timeline.playback import PlaybackSession
from q1timeline.q1asm.lint import analyze_program_lints
from q1timeline.q1asm.parser import parse_q1asm


def _lints(source: str):
    return analyze_program_lints(parse_q1asm(source, file="lint.q1asm"))


def test_unused_definitions_have_exact_sources_and_do_not_block_execution() -> None:
    diagnostics = _lints(".DEF UNUSED 4\n.DEF USED 8\nwait $USED\nstop")

    diagnostic, = diagnostics
    assert (diagnostic.category, diagnostic.severity) == ("unused_definition", "hint")
    assert diagnostic.details == {"name": "UNUSED"}
    assert (diagnostic.source.file, diagnostic.source.line, diagnostic.source.raw) == (
        "lint.q1asm", 1, ".DEF UNUSED 4",
    )


@pytest.mark.parametrize("source", [
    ".DEF REG R0\nmove 1,$REG\nstop",
    ".DEF BASE 4\n.DEF DURATION {BASE}\nwait $DURATION\nstop",
    ".DEF TARGET @done\njmp $TARGET\ndone: stop",
    ".DEF VALUE 4\nwait {max(VALUE, 8)}\nstop",
])
def test_definitions_referenced_by_aliases_or_templates_are_used(source: str) -> None:
    assert _lints(source) == []


def test_unused_definition_chains_are_not_kept_alive_by_each_other() -> None:
    diagnostics = _lints(".DEF BASE 4\n.DEF UNUSED {BASE}\nstop")

    assert [(item.category, item.details["name"]) for item in diagnostics] == [
        ("unused_definition", "BASE"), ("unused_definition", "UNUSED"),
    ]


@pytest.mark.parametrize("expression, used", [
    ("lookup('VALUE')", False),
    ("{'VALUE': 4}", False),
    ("record.VALUE", False),
    ("lookup(VALUE=4)", False),
    ("lookup(VALUE)", True),
    ("f'{VALUE}'", True),
])
def test_template_definition_references_are_names_not_string_or_attribute_tokens(expression: str, used: bool) -> None:
    diagnostics = _lints(f".DEF VALUE 4\nwait {{{expression}}}\nstop")

    assert [(item.category, item.details.get("name")) for item in diagnostics] == (
        [] if used else [("unused_definition", "VALUE")]
    )


@pytest.mark.parametrize("expression", ["fn(1 2)", "1 +", "[1 2]", "lookup(key=)"])
@pytest.mark.parametrize("definition", [False, True])
def test_malformed_template_expressions_warn_at_the_definition_or_instruction(expression: str, definition: bool) -> None:
    source = (f".DEF VALUE {{{expression}}}\nwait $VALUE\nstop" if definition
              else f"stop\nwait {{{expression}}}")
    diagnostics = _lints(source)
    diagnostic, = [item for item in diagnostics if item.category == "template_expression_syntax"]

    assert diagnostic.severity == "warning"
    assert diagnostic.source.line == (1 if definition else 2)
    assert diagnostic.source.raw == source.splitlines()[diagnostic.source.line - 1]
    assert diagnostic.details == {"operand": "{" + expression + "}"}
    assert not any(item.severity == "error" for item in diagnostics)


@pytest.mark.parametrize("expression", [
    "COUNT:04d", "COUNT!r", "COUNT=", "max(COUNT, 4)", "[COUNT, 4][0]",
    "{'values': [COUNT, 4]}['values'][0]", "lookup('x', \"y\"):04d", "$COUNT",
])
def test_valid_formatted_nested_and_extension_templates_do_not_get_syntax_warnings(expression: str) -> None:
    diagnostics = _lints(f".DEF COUNT 4\nwait {{{expression}}}\nstop")

    assert not any(item.category == "template_expression_syntax" for item in diagnostics)
    if "COUNT" in expression:
        assert not any(item.category == "unused_definition" for item in diagnostics)


def test_exact_supplied_parameter_names_can_be_opaque_template_fields() -> None:
    program = parse_q1asm("wait {pulse duration}\nstop")

    assert [item.category for item in analyze_program_lints(program)] == ["template_expression_syntax"]
    assert analyze_program_lints(program, known_parameters={"pulse duration": 4}) == []


@pytest.mark.parametrize("source, start, end", [
    ("stop\nwait 4\nwait 8", 2, 3),
    ("jmp @done\nwait 4\nwait 8\ndone: stop", 2, 3),
    ("again: jmp @again\nstop", 2, 2),
])
def test_only_provably_unreachable_regions_are_reported(source: str, start: int, end: int) -> None:
    diagnostic, = _lints(source)

    assert (diagnostic.category, diagnostic.severity) == ("unreachable_code", "warning")
    assert diagnostic.source.line == start
    assert diagnostic.details["end_line"] == end


@pytest.mark.parametrize("body", ["jmp R0", "jmp 4", "move @target,R0"])
def test_unreachable_indirect_code_cannot_make_itself_an_entry_point(body: str) -> None:
    diagnostics = _lints(f"stop\n{body}\ntarget: stop")

    diagnostic, = diagnostics
    assert diagnostic.category == "unreachable_code"
    assert diagnostic.source.line == 2
    assert diagnostic.details["end_line"] == 3


@pytest.mark.parametrize("source", [
    "cmp R0,1\njz @other\nwait 4\nother: stop",
    "body: wait 4\nloop R0,@body\nstop",
    "jmp R0\nwait 4\nstop",
    "jmp 2\nwait 4\nstop",
    "jmp {DEST}\nwait 4\nstop",
    "move @routine,R0\nstop\nroutine: wait 4\nstop",
    ".DEF RETURN R27\nmove @done,$RETURN\njmp @routine\ndone: stop\nroutine: jmp $RETURN",
])
def test_conditional_paths_and_possible_indirect_entries_are_not_declared_unreachable(source: str) -> None:
    assert _lints(source) == []


@pytest.mark.parametrize("source, line", [
    ("wait 4", 1),
    ("wait_sync 4\nplay 0,1,4", 2),
    ("body: wait 4\nloop R0,@body", 2),
])
def test_reachable_fallthrough_without_stop_is_reported(source: str, line: int) -> None:
    diagnostic, = _lints(source)

    assert (diagnostic.category, diagnostic.severity) == ("missing_stop", "warning")
    assert diagnostic.source.line == line


@pytest.mark.parametrize("source", ["", "# comment", "stop", "again: wait 4\njmp @again", "illegal"])
def test_empty_programs_terminal_instructions_and_infinite_loops_do_not_need_stop(source: str) -> None:
    assert _lints(source) == []


@pytest.mark.parametrize("source", [
    "move 1,R0\nmove 2,R0\nstop",
    ".DEF REG R0\nmove 1,$REG\nmove 2,$REG\nstop",
    "move 1,R0\ncmp R1,0\njz @other\nmove 2,R0\nstop\nother: move 3,R0\nstop",
])
def test_register_values_overwritten_before_every_possible_read_are_reported(source: str) -> None:
    diagnostic, = _lints(source)

    assert (diagnostic.category, diagnostic.severity) == ("overwritten_register", "warning")
    assert diagnostic.details == {"register": "R0"}
    assert diagnostic.source.raw == "move 1,R0" or diagnostic.source.raw == "move 1,$REG"


@pytest.mark.parametrize("source", [
    "move 1,R0\nstop",
    "move 1,R0\nnop\nadd R0,1,R0\nstop",
    "move 1,R0\nbody: wait 4\nloop R0,@body\nstop",
    "move 1,R0\ncmp R1,0\njz @read\nmove 2,R0\nstop\nread: wait R0\nstop",
    "move 1,R0\ncmp R1,0\njz @done\nmove 2,R0\ndone: stop",
    "move 1,R0\nbody: wait 4\njmp @body",
    "move 1,R0\nagain: cmp R1,0\njz @again\nmove 2,R0\nstop",
    "move 1,R0\njmp R27\nmove 2,R0\nstop",
    ".DEF SOURCE R0\nmove 1,R0\nnop\nwait $SOURCE\nmove 2,R0\nstop",
    "move 1,R0\nnop\nwait {R0}\nmove 2,R0\nstop",
    ".DEF SOURCE R0\nmove 1,R0\nnop\nwait {SOURCE}\nmove 2,R0\nstop",
    ".DEF SOURCE R0\nmove 1,R0\nnop\nwait {$SOURCE}\nmove 2,R0\nstop",
])
def test_observable_register_values_reads_read_modify_writes_and_unknown_paths_are_preserved(source: str) -> None:
    assert _lints(source) == []


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation"])
@pytest.mark.parametrize("operand, duration, expected", [
    ("{R0}", 4, [("unused_definition", 1), ("overwritten_register", 3)]),
    ("{$R0}", 8, [("overwritten_register", 2)]),
])
def test_template_physical_register_and_same_named_alias_have_separate_reads(
    mode: str, operand: str, duration: int, expected: list[tuple[str, int]],
) -> None:
    source = f".DEF R0 R1\nmove 4,R0\nmove 8,R1\nnop\nwait {operand}\nmove 12,R0\nmove 16,R1\nstop"
    ir = analyze_documents({"collision.q1asm": source}, execution_mode=mode, alignment_mode="none")

    assert [(item["category"], item["source"]["line"]) for item in ir["diagnostics"]] == expected
    assert [item["duration"]["value"] for item in ir["events"] if item["kind"] == "wait"] == [duration]


@pytest.mark.parametrize("source", ["WAIT 4\nstop", "stop\nplay 0,1", "stop\nunknown_opcode"])
def test_malformed_programs_do_not_produce_control_flow_lints(source: str) -> None:
    assert _lints(source) == []


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation", "playback"])
def test_public_paths_expose_lints_with_sources_severities_and_catalog_presentations(mode: str) -> None:
    source = (
        ".DEF UNUSED 4\nmove 1,R0\nmove 2,R0\nmove 1,R1\nnop\ncmp R1,0\n"
        "jz @ending\nstop\nwait 4\nending: wait 8\n"
    )
    if mode == "playback":
        ir = PlaybackSession({"seq": ("lint.q1asm", source)}).snapshot()
        assert ir["playback"]["steps"] == 0
        assert ir["playback"]["status"] == "idle"
        assert ir["events"] == []
    else:
        ir = analyze_documents({"lint.q1asm": source}, execution_mode=mode, alignment_mode="none")
        assert ir["events"]
    expected = {
        "unused_definition": ("hint", 1),
        "unreachable_code": ("warning", 9),
        "missing_stop": ("warning", 10),
        "overwritten_register": ("warning", 2),
    }
    assert {item["category"]: (item["severity"], item["source"]["line"])
            for item in ir["diagnostics"]} == expected
    assert len(ir["diagnostics"]) == len(expected)
    for diagnostic in ir["diagnostics"]:
        assert diagnostic["source"]["file"] == "lint.q1asm"
        assert diagnostic["source"]["raw"] == source.splitlines()[diagnostic["source"]["line"] - 1]
        presentation = json.loads(json.dumps(describe_diagnostic(diagnostic), allow_nan=False))
        assert presentation["group"] == "Q1ASM Lint"
        assert presentation["category"] == diagnostic["category"]
        assert presentation["severity"] == diagnostic["severity"]
        assert presentation["summary"] == diagnostic["message"]
        assert presentation["title"]
        assert presentation["fix"]


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("source, category, severity", [
    (".DEF UNUSED 4\nstop", "unused_definition", "hint"),
    ("stop\nwait 4", "unreachable_code", "warning"),
    ("wait 4", "missing_stop", "warning"),
    ("move 1,R0\nmove 2,R0\nstop", "overwritten_register", "warning"),
    ("move {fn(1 2)},R0\nstop", "template_expression_syntax", "warning"),
])
def test_cli_lints_are_nonfatal_unless_strict_requests_warnings(
    tmp_path: Path, capsys, strict: bool, source: str, category: str, severity: str,
) -> None:
    path = tmp_path / "lint.q1asm"
    path.write_text(source, encoding="utf-8")
    args = ["analyze", str(path), "--format", "vscode-json", "--include-diagnostics", "--align", "none", "--no-render"]
    result = main(args + (["--strict"] if strict else []))

    assert result == (2 if strict and severity == "warning" else 0)
    payload = json.loads(capsys.readouterr().out)
    diagnostic, = payload["diagnostics"]
    assert (diagnostic["category"], diagnostic["severity"]) == (category, severity)
    assert diagnostic["source"]["file"] == str(path)


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation", "playback"])
def test_template_syntax_warning_is_visible_before_the_affected_instruction_runs(mode: str) -> None:
    source = "stop\nwait {fn(1 2)}"
    if mode == "playback":
        ir = PlaybackSession({"seq": ("template.q1asm", source)}).snapshot()
        assert ir["playback"]["steps"] == 0
    else:
        ir = analyze_documents({"template.q1asm": source}, execution_mode=mode, alignment_mode="none")
    diagnostic, = [item for item in ir["diagnostics"] if item["category"] == "template_expression_syntax"]

    assert diagnostic["severity"] == "warning"
    assert diagnostic["source"]["line"] == 2
    assert not any(item["severity"] == "error" for item in ir["diagnostics"])
