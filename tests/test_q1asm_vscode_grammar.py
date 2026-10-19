import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from q1timeline.q1asm.instruction_table import INSTRUCTION_TABLE, SUPPORTED_OPS
from q1timeline.q1asm.parser import parse_q1asm


FAMILY_SCOPES = {
    "q1": "keyword.other.q1.q1asm",
    "flow": "keyword.control.flow.q1asm",
    "latched": "storage.modifier.latched.q1asm",
    "rt": "entity.name.function.rt.q1asm",
    "sync": "constant.language.sync.q1asm",
    "feedback": "string.other.feedback.q1asm",
}


def _grammar() -> dict:
    return json.loads(
        Path("vscode-extension/src/q1timeline/syntaxes/q1asm.tmLanguage.json").read_text(encoding="utf-8")
    )


def _instruction_patterns() -> list[tuple[str, re.Pattern]]:
    return [
        (pattern["captures"]["2"]["name"], re.compile(pattern["match"]))
        for pattern in _grammar()["repository"]["instructions"]["patterns"]
    ]


def _grammar_tokens(text: str) -> list[tuple[str, str]]:
    grammar = _grammar()

    def rules_for(patterns: list[dict]) -> list[tuple[dict, re.Pattern]]:
        expanded = [
            child
            for pattern in patterns
            for child in (grammar["repository"][pattern["include"][1:]]["patterns"] if "include" in pattern else [pattern])
        ]
        return [(pattern, re.compile(pattern.get("match", pattern.get("begin")))) for pattern in expanded]

    def region_end(pattern: dict, offset: int) -> int:
        while offset < len(text):
            end = re.compile(pattern["end"]).search(text, offset)
            nested = [
                (match.start(), child, match)
                for child, regex in rules_for(pattern.get("patterns", []))
                if (match := regex.search(text, offset))
            ]
            first = min(nested, key=lambda candidate: candidate[0]) if nested else None
            if end is None:
                return len(text)
            if first is None or end.start() <= first[0]:
                return end.end()
            offset = region_end(first[1], first[2].end())
        return len(text)

    rules = rules_for(grammar["patterns"])
    tokens = []
    offset = 0
    while offset < len(text):
        candidates = [
            (match.start(), index, pattern, match)
            for index, (pattern, regex) in enumerate(rules)
            if (match := regex.search(text, offset))
        ]
        if not candidates:
            break
        _start, _index, pattern, match = min(candidates, key=lambda candidate: candidate[:2])
        end = region_end(pattern, match.end()) if "begin" in pattern else match.end()
        if "name" in pattern:
            tokens.append((text[match.start():end], pattern["name"]))
        for group, capture in pattern.get("captures", {}).items():
            if match.group(int(group)) is not None:
                tokens.append((match.group(int(group)), capture["name"]))
        offset = end
    return tokens


def _family(op: str) -> str:
    category = INSTRUCTION_TABLE[op].category
    if op.startswith("fb_"):
        return "feedback"
    if category == "branch" or op == "stop":
        return "flow"
    return "q1" if category in {"classical", "control"} else category


def test_integrated_q1asm_grammar_highlights_supported_opcodes() -> None:
    patterns = _instruction_patterns()
    missing = sorted(op for op in SUPPORTED_OPS if not any(pattern.search(op) for _, pattern in patterns))
    assert missing == []


def test_integrated_q1asm_grammar_groups_opcodes_by_execution_family() -> None:
    patterns = _instruction_patterns()
    assert {scope for scope, _ in patterns} == set(FAMILY_SCOPES.values())
    for op in SUPPORTED_OPS:
        assert [scope for scope, pattern in patterns if pattern.search(op)] == [FAMILY_SCOPES[_family(op)]]


def test_integrated_q1asm_grammar_colors_only_opcode_capture() -> None:
    patterns = _instruction_patterns()
    grammar = _grammar()
    assert grammar["patterns"].index({"include": "#instructions"}) < grammar["patterns"].index({"include": "#labels"})
    for op in SUPPORTED_OPS:
        for label in ["again:", "play :"]:
            prefix = f"  {label} "
            text = f"{prefix}{op.upper()} R0, 4 # play set_freq"
            matches = [pattern.search(text) for _, pattern in patterns]
            matches = [match for match in matches if match]
            assert len(matches) == 1
            assert matches[0].group(1) == label.removesuffix(":").strip()
            assert matches[0].group(2) == op.upper()
            assert matches[0].span(2) == (len(prefix), len(prefix) + len(op))
    for text in ["# play 0,1,4", "// set_freq 1", "play:", "play :", ".word play", "unknown set_freq 1"]:
        assert not any(pattern.search(text) for _, pattern in patterns)
    assert [match.group(2) for _, pattern in patterns if (match := pattern.search("move play, R0 // set_freq 1"))] == ["move"]


def test_integrated_q1asm_grammar_colors_only_supported_directive_token() -> None:
    directive = _grammar()["repository"]["directives"]["patterns"][0]
    pattern = re.compile(directive["match"])
    assert directive["captures"]["1"]["name"] == "keyword.control.directive.q1asm"
    assert directive["captures"]["2"]["name"] == "constant.other.symbol.q1asm"
    for text, start in [(".DEF play 4", 0), ("  .def gain R0", 2), ("\t .DeF acquire 4 // play", 2)]:
        match = pattern.search(text)
        assert match is not None
        assert match.group(1).lower() == ".def"
        assert match.span(1) == (start, start + 4)
        assert not any(instruction.search(text) for _, instruction in _instruction_patterns())
    for text in ["# .DEF play 4", "// .def gain 4", "move .DEF, R0", ".DEFAULT gain 4", ".word play"]:
        assert pattern.search(text) is None


@pytest.mark.parametrize("register", ["R0", "r0", "R1", "r128", "R0xA", "r0XaF"])
def test_integrated_q1asm_grammar_accepts_supported_register_forms(register: str) -> None:
    assert _grammar_tokens(f"move {register},R2")[1:] == [
        (register, "variable.other.register.q1asm"),
        ("R2", "variable.other.register.q1asm"),
    ]


@pytest.mark.parametrize("number", ["0", "-0", "4", "-42", "0xA", "-0XfF"])
def test_integrated_q1asm_grammar_captures_complete_supported_numbers(number: str) -> None:
    assert _grammar_tokens(f"wait {number}")[1:] == [(number, "constant.numeric.q1asm")]


@pytest.mark.parametrize("operand", [
    "R01", "R0xGG", "R1_extra", "0b10", "00", "-01", "+4", "--4", "0xGG", "1.5", "gain42", "$", "@",
])
def test_integrated_q1asm_grammar_does_not_color_malformed_operand_fragments(operand: str) -> None:
    assert _grammar_tokens(f"move {operand}") == [("move", FAMILY_SCOPES["q1"])]


def test_integrated_q1asm_grammar_preserves_symbol_placeholder_and_label_scopes() -> None:
    assert _grammar_tokens("play : move $R0, { R0 + $GAIN4 * -0xA } # R1 42 @other") == [
        ("play", "entity.name.label.q1asm"),
        ("move", FAMILY_SCOPES["q1"]),
        ("$R0", "constant.other.symbol.q1asm"),
        ("{ R0 + $GAIN4 * -0xA }", "constant.other.placeholder.q1asm"),
        ("# R1 42 @other", "comment.line.number-sign.q1asm"),
    ]
    assert _grammar_tokens("loop R0xA,@play") == [
        ("loop", FAMILY_SCOPES["flow"]),
        ("R0xA", "variable.other.register.q1asm"),
        ("@play", "entity.name.label.q1asm"),
    ]
    assert _grammar_tokens("  again :") == [("again", "entity.name.label.q1asm")]
    for name in ["play", "R0", "GAIN4"]:
        assert _grammar_tokens(f".DEF {name} -4") == [
            (".DEF", "keyword.control.directive.q1asm"),
            (name, "constant.other.symbol.q1asm"),
            ("-4", "constant.numeric.q1asm"),
        ]
        assert _grammar_tokens(f"wait ${name}")[1:] == [(f"${name}", "constant.other.symbol.q1asm")]


def test_integrated_q1asm_grammar_keeps_nested_templates_in_one_scope() -> None:
    template = _grammar()["repository"]["templates"]["patterns"][0]
    assert template["patterns"] == [{"include": "#templates"}]
    for expression in ["{ DURATION // 2 }", "{ {inner} + 4 }", "{ {{{inner}}} // 4 }"]:
        assert _grammar_tokens(f"wait {expression}") == [
            ("wait", FAMILY_SCOPES["rt"]),
            (expression, "constant.other.placeholder.q1asm"),
        ]
    assert _grammar_tokens("wait { DURATION # comment }")[-1] == ("# comment }", "comment.line.number-sign.q1asm")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required to compare source argument colors")
def test_argument_scopes_match_parser_and_shared_javascript_tokens() -> None:
    program = parse_q1asm(
        ".DEF GAIN4 12\n.DEF DURATION { BASE // 2 }\nagain: move -0xA, r0xF\nloop R0,@again\n"
        "set_freq $GAIN4\nwait { DURATION + 4 }\nwait { DURATION // 2 }\nwait { {inner} + 4 }"
    )
    assert program.diagnostics == []
    instruction_lines = [instruction.source.raw[instruction.source.column - 1:] for instruction in program.instructions]
    lines = [*instruction_lines, ".DEF DURATION { BASE // 2 }"]
    result = subprocess.run(
        ["node", "-e", "const c = require('./vscode-extension/src/shared/timeline/instructionColors.js'); "
         "process.stdout.write(JSON.stringify({styles:c.argumentStyles, lines:JSON.parse(process.argv[1]).map("
         "line => c.sourceTokens(line).filter(t => t.kind !== 'command').map("
         "t => [line.slice(t.start,t.end),c.argumentStyles[t.kind].scope]))}));", json.dumps(lines)],
        check=True, capture_output=True, text=True,
    )
    payload = json.loads(result.stdout)
    kinds = {"reg": "register", "imm": "number", "symbol": "symbol", "label": "label", "placeholder": "placeholder"}
    for instruction, line, javascript_tokens in zip(program.instructions, lines, payload["lines"]):
        expected = [(arg.raw, payload["styles"][kinds[arg.kind]]["scope"]) for arg in instruction.args]
        assert _grammar_tokens(line)[1:] == expected
        assert javascript_tokens == [list(token) for token in expected]
    definition_expected = [
        ("DURATION", payload["styles"]["symbol"]["scope"]),
        (program.defs["DURATION"].raw, payload["styles"]["placeholder"]["scope"]),
    ]
    assert _grammar_tokens(lines[-1])[1:] == definition_expected
    assert payload["lines"][-1] == [list(token) for token in definition_expected]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required to load the extension color families")
def test_shared_javascript_colors_match_supported_opcodes_and_grammar() -> None:
    result = subprocess.run(
        ["node", "-e", "process.stdout.write(JSON.stringify(require('./vscode-extension/src/shared/timeline/instructionColors.js').families))"],
        check=True, capture_output=True, text=True,
    )
    families = json.loads(result.stdout)
    directive = families.pop("directive")
    assert directive["ops"] == [".def"]
    assert directive["scope"] == _grammar()["repository"]["directives"]["patterns"][0]["captures"]["1"]["name"]
    assert set(families) == set(FAMILY_SCOPES)
    assigned = [op for family in families.values() for op in family["ops"]]
    assert len(assigned) == len(set(assigned))
    assert set(assigned) == SUPPORTED_OPS
    for name, family in families.items():
        assert family["scope"] == FAMILY_SCOPES[name]
        assert set(family["ops"]) == {op for op in SUPPORTED_OPS if _family(op) == name}
