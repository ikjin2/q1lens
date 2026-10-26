from __future__ import annotations

import json
from pathlib import Path

import pytest

from q1timeline.analysis.api import analyze_documents
from q1timeline.analysis.interpreter import interpret_program
from q1timeline.cli import main
from q1timeline.playback import PlaybackSession
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


@pytest.mark.parametrize("execution_mode", ["analysis", "concrete", "simulation"])
@pytest.mark.parametrize(
    ("source", "category", "line"),
    [
        pytest.param("stop\nunknown_opcode\n", "unknown_instruction", 2, id="unknown-after-stop"),
        pytest.param("play 0,1\nstop\n", "invalid_argument_count", 1, id="count-reachable"),
        pytest.param("jmp @done\nplay 0,1\ndone: stop\n", "invalid_argument_count", 2, id="count-skipped"),
        pytest.param("stop\nmove 1,2\n", "invalid_argument_type", 2, id="type-after-stop"),
        pytest.param("stop\nadd 1,2,R0\n", "invalid_argument_type", 2, id="alu-two-immediates"),
        pytest.param("stop\nplay R0,1,4\n", "invalid_argument_type", 2, id="play-mixed-types"),
        pytest.param("stop\nset_awg_gain R0,1\n", "invalid_argument_type", 2, id="gain-mixed-types"),
        pytest.param("stop\nacquire R0,0,4\n", "invalid_argument_type", 2, id="acquisition-register-index"),
        pytest.param(".DEF REG R0\nstop\nplay 0,$REG,4\n", "invalid_argument_type", 3, id="alias-mixed-types"),
        pytest.param("stop\nmove 1,R64\n", "invalid_argument_type", 2, id="register-range-after-stop"),
        pytest.param("move 1,r0\nstop\n", "invalid_argument_type", 1, id="register-case-reachable"),
        pytest.param("stop\nwait -1\n", "invalid_argument_value", 2, id="negative-duration-after-stop"),
        pytest.param("jmp @done\nwait 65536\ndone: stop\n", "invalid_argument_value", 2, id="duration-range-skipped"),
        pytest.param("stop\njmp @missing\n", "undefined_label", 2, id="branch-label-after-stop"),
        pytest.param("move @missing,R0\nstop\n", "undefined_label", 1, id="value-label-reachable"),
        pytest.param("jmp @done\nplay @missing,1,4\ndone: stop\n", "undefined_label", 2, id="value-label-skipped"),
        pytest.param(
            "move 0,R0\nnop\ncmp R0,1\nnop\njz @bad\nstop\nbad: play 0,1\n",
            "invalid_argument_count",
            7,
            id="count-untaken-branch",
        ),
    ],
)
def test_static_errors_are_reported_independently_of_execution_path(
    execution_mode: str, source: str, category: str, line: int
) -> None:
    ir = analyze_documents(
        {"static.q1asm": source},
        alignment_mode="none",
        execution_mode=execution_mode,
    )

    matches = [
        diagnostic
        for diagnostic in ir["diagnostics"]
        if diagnostic["severity"] == "error"
        and diagnostic["category"] == category
        and (diagnostic.get("source") or {}).get("file") == "static.q1asm"
        and diagnostic["source"]["line"] == line
    ]
    assert len(matches) == 1, ir["diagnostics"]


@pytest.mark.parametrize("execution_mode", ["analysis", "concrete", "simulation"])
@pytest.mark.parametrize(
    "source",
    [
        pytest.param("stop\nwait R0\n", id="uninitialized-register"),
        pytest.param("jmp @done\nplay R0,R1,4\ndone: stop\n", id="register-signature"),
        pytest.param("stop\nmove {int(4 * f_center)},R0\nwait {duration}\n", id="template-operands"),
        pytest.param(".DEF PERIOD {period}\nstop\nwait $PERIOD\n", id="template-def"),
        pytest.param(".DEF BIN R0\nstop\nacquire 0,$BIN,4\n", id="acquisition-register-alias"),
        pytest.param("stop\nacquire {acquisition},{bin_index},4\n", id="acquisition-template"),
        pytest.param("stop\nset_cond R0,R1,R2,4\n", id="unvisited-condition-registers"),
        pytest.param(
            "jmp @done\nset_awg_gain -32768,32767\nwait 65535\nmove 0xffffffff,R63\ndone: stop\n",
            id="literal-boundaries",
        ),
    ],
)
def test_unvisited_valid_operands_do_not_require_runtime_values(execution_mode: str, source: str) -> None:
    ir = analyze_documents(
        {"static.q1asm": source},
        params={"R0": -1, "R1": 65536},
        alignment_mode="none",
        execution_mode=execution_mode,
    )

    diagnostic, = ir["diagnostics"]
    assert (diagnostic["severity"], diagnostic["category"]) == ("warning", "unreachable_code")


@pytest.mark.parametrize("execution_mode", ["analysis", "concrete", "simulation"])
def test_disabled_condition_does_not_require_unused_register_values(execution_mode: str) -> None:
    ir = analyze_documents(
        {"disabled.q1asm": "move 0,R0\nnop\nset_cond R0,R1,R2,4\nstop\n"},
        alignment_mode="none",
        execution_mode=execution_mode,
    )

    assert ir["diagnostics"] == []


@pytest.mark.parametrize("execution_mode", ["concrete", "simulation"])
def test_strict_mode_checks_placeholders_after_stop(execution_mode: str) -> None:
    program = parse_q1asm("stop\nmove {COUNT},R0\n", file="strict.q1asm")
    for strict in (False, True):
        if execution_mode == "concrete":
            state = interpret_program(program, sequencer_id="s", execution_mode="concrete", strict_q1asm=strict)
        else:
            state = simulate_programs({"s": program}, strict_q1asm=strict)[0]
        errors = [diagnostic for diagnostic in state.diagnostics if diagnostic.severity == "error"]
        assert [(item.category, item.source.line) for item in errors] == ([("unresolved_symbol", 2)] if strict else [])


@pytest.mark.parametrize("execution_mode", ["concrete", "simulation"])
@pytest.mark.parametrize("operand,strict", [("$MISSING", False), ("{COUNT}", True)])
def test_known_source_errors_block_execution_before_a_valid_prefix(
    execution_mode: str, operand: str, strict: bool,
) -> None:
    program = parse_q1asm(f"wait 1000\nstop\nmove {operand},R0\n", file="preflight.q1asm")
    if execution_mode == "concrete":
        state = interpret_program(program, sequencer_id="s", execution_mode="concrete", strict_q1asm=strict)
    else:
        state = simulate_programs({"s": program}, strict_q1asm=strict)[0]

    assert state.metadata["execution"]["status"] == "blocked"
    assert state.metadata["execution"]["steps"] == 0
    assert state.events == []
    assert any(item.category == "unresolved_symbol" and item.severity == "error" for item in state.diagnostics)


@pytest.mark.parametrize(
    ("instruction", "valid_values", "invalid_values"),
    [
        ("move {},R0", (-2147483648, 0, 4294967295), (-2147483649, 4294967296)),
        ("set_freq {}", (-2147483648, 0, 2147483647), (-2147483649, 2147483648)),
        ("set_awg_gain {},0", (-32768, 0, 32767), (-32769, 32768)),
        ("wait {}", (0, 4, 65535), (-1, 65536)),
        ("play {},0,4", (0, 1, 1023), (-1, 1024)),
        ("set_cond 1,{},0,4", (0, 1, 32767), (-1, 32768)),
    ],
)
def test_immediate_boundaries_are_checked_even_after_stop(
    instruction: str, valid_values: tuple[int, ...], invalid_values: tuple[int, ...]
) -> None:
    for value in valid_values + invalid_values:
        ir = analyze_documents(
            {"bounds.q1asm": f"stop\n{instruction.format(value)}\n"},
            alignment_mode="none",
        )
        errors = [diagnostic for diagnostic in ir["diagnostics"] if diagnostic["severity"] == "error"]
        if value in valid_values:
            assert errors == [], (instruction, value, errors)
        else:
            diagnostic, = errors
            assert diagnostic["category"] == "invalid_argument_value"
            assert diagnostic["source"]["line"] == 2
            assert diagnostic["details"]["value"] == value


def test_analysis_does_not_repeat_static_error_when_visiting_loop_body() -> None:
    ir = analyze_documents(
        {"loop.q1asm": "move 3,R0\nnop\nbody: play 0,1\nloop R0,@body\nstop\n"},
        alignment_mode="none",
    )

    errors = [diagnostic for diagnostic in ir["diagnostics"] if diagnostic["category"] == "invalid_argument_count"]
    assert len(errors) == 1
    assert errors[0]["source"]["line"] == 3


def test_playback_reports_invalid_dead_code_before_running() -> None:
    session = PlaybackSession({"seq0": ("playback.q1asm", "stop\nplay 0,1\n")})

    snapshot = session.snapshot()

    assert snapshot["playback"]["status"] == "error"
    assert snapshot["playback"]["steps"] == 0
    assert snapshot["events"] == []
    diagnostic, = snapshot["diagnostics"]
    assert diagnostic["category"] == "invalid_argument_count"
    assert diagnostic["source"]["line"] == 2


def test_cli_rejects_invalid_instruction_after_stop(tmp_path: Path, capsys) -> None:
    source_file = tmp_path / "invalid.q1asm"
    source_file.write_text("stop\nplay 0,1\n", encoding="utf-8")

    result = main(["analyze", str(source_file), "--format", "vscode-json"])

    assert result == 2
    payload = json.loads(capsys.readouterr().out)
    assert any(
        diagnostic["category"] == "invalid_argument_count"
        and diagnostic["source"]["line"] == 2
        for diagnostic in payload["diagnostics"]
    )


@pytest.mark.parametrize("branch", ["jmp @missing", "jge R0,1,@missing", "loop R0,@missing"])
def test_undefined_analysis_branch_keeps_unknown_boundary_without_falling_through(branch: str) -> None:
    ir = analyze_documents({"static.q1asm": f"{branch}\nwait 20\nstop"}, alignment_mode="none")

    assert sum(item["category"] == "undefined_label" for item in ir["diagnostics"]) == 1
    assert any(event["kind"] == "unknown_region" and event["meta"].get("reason") == "undefined_label"
               for event in ir["events"])
    assert not any(event["kind"] == "wait" for event in ir["events"])
