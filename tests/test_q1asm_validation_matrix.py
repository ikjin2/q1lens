"""Source validation regressions independently checked with qblox-instruments 1.4.1."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from q1timeline.analysis.api import analyze_documents
from q1timeline.cli import main
from q1timeline.playback import PlaybackSession


def _diagnostics(source: str, execution_mode: str = "analysis", *, params: dict | None = None) -> list[dict]:
    return analyze_documents(
        {"validation.q1asm": source}, params=params,
        alignment_mode="none", execution_mode=execution_mode,
    )["diagnostics"]


def _assert_only_structural_lints(diagnostics: list[dict]) -> None:
    assert all(
        (item["category"], item["severity"]) in {
            ("unreachable_code", "warning"), ("unused_definition", "hint"), ("unused_label", "hint"),
        }
        for item in diagnostics
    ), diagnostics


@pytest.mark.parametrize("execution_mode", ["analysis", "concrete", "simulation"])
@pytest.mark.parametrize("instruction", ["set_scope_en {}", "set_latch_en {},4", "set_cond {},0,0,4", "fb_acq_tb_valid {},4"])
def test_label_aliases_obey_the_same_operand_ranges_as_direct_labels(execution_mode: str, instruction: str) -> None:
    for prefix, operand, line in [("", "@target", 2), (".DEF ENABLE @target\n", "$ENABLE", 3)]:
        diagnostics = _diagnostics(prefix + f"stop\n{instruction.format(operand)}\ntarget: stop\n", execution_mode)
        assert any(
            item["severity"] == "error" and item["category"] == "invalid_argument_value"
            and (item.get("source") or {}).get("line") == line
            and item["details"].get("value") == 2
            for item in diagnostics
        ), diagnostics


@pytest.mark.parametrize("execution_mode", ["analysis", "concrete", "simulation"])
def test_register_named_alias_and_hardware_register_have_separate_values(execution_mode: str) -> None:
    diagnostics = _diagnostics(".DEF R0 65536\nstop\nwait $R0\n", execution_mode)
    assert any(item["category"] == "invalid_argument_value" and item["details"].get("value") == 65536 for item in diagnostics)
    _assert_only_structural_lints(_diagnostics(".DEF R0 65536\nstop\nwait R0\n", execution_mode))
    _assert_only_structural_lints(_diagnostics(".DEF R0 65535\nstop\nwait $R0\n", execution_mode))


@pytest.mark.parametrize("execution_mode", ["analysis", "concrete", "simulation"])
def test_register_named_alias_does_not_replace_a_physical_register_read(execution_mode: str) -> None:
    ir = analyze_documents(
        {"namespace.q1asm": ".DEF R0 R1\nmove 7,R0\nnop\nmove 9,R1\nnop\nmove R0,R2\nnop\nmove $R0,R3\nnop\nstop\n"},
        alignment_mode="none", execution_mode=execution_mode,
    )
    values = {item["register"]: item["value"]["value"] for item in ir["register_history"] if item["register"] in {"R2", "R3"}}
    assert values == {"R2": 7, "R3": 9}
    assert not [item for item in ir["diagnostics"] if item["severity"] == "error"]


@pytest.mark.parametrize("execution_mode", ["analysis", "concrete", "simulation"])
def test_forward_label_alias_can_be_resolved_before_execution(execution_mode: str) -> None:
    ir = analyze_documents(
        {"validation.q1asm": ".DEF END @done\njmp $END\nwait 4\ndone: wait 1000\nstop\n"},
        alignment_mode="none", execution_mode=execution_mode,
    )
    assert not [item for item in ir["diagnostics"] if item["category"] in {"unresolved_symbol", "analysis_incomplete"} or item["severity"] == "error"]
    assert [item["duration"]["value"] for item in ir["events"] if item["kind"] == "wait"] == [1000]


@pytest.mark.parametrize("execution_mode", ["analysis", "concrete", "simulation"])
def test_expanded_loop_accepts_a_direct_label_alias_target(execution_mode: str) -> None:
    ir = analyze_documents(
        {"loop.q1asm": ".DEF BODY @body\nmove 2,R0\nnop\nbody: wait 1000\nloop R0,$BODY\nstop\n"},
        alignment_mode="none", execution_mode=execution_mode,
    )
    assert not [item for item in ir["diagnostics"] if item["severity"] == "error" or item["category"] == "analysis_incomplete"]
    waits = [item["duration"]["value"] for item in ir["events"] if item["kind"] == "wait"]
    if execution_mode == "analysis":
        assert waits == [1000]
        loop, = [item for item in ir["events"] if item["kind"] == "loop_block"]
        assert loop["meta"]["count"] == 2
        assert loop["duration"]["value"] == 1000
    else:
        assert waits == [1000, 1000]
        assert ir["execution"]["loop"]["status"] == "completed"


@pytest.mark.parametrize("execution_mode", ["analysis", "concrete", "simulation"])
@pytest.mark.parametrize("op", ["fb_com_cfg", "fb_acq_tb_cfg", "fb_com_extra", "fb_acq_tb_extra", "fb_acq_tb_mock"])
def test_packed_feedback_register_alias_keeps_the_duration_operand(execution_mode: str, op: str) -> None:
    _assert_only_structural_lints(_diagnostics(f".DEF CFG R0\nstop\n{op} $CFG,1000\n", execution_mode))


@pytest.mark.parametrize("execution_mode", ["analysis", "concrete", "simulation"])
@pytest.mark.parametrize(
    ("source", "line"),
    [
        ("jmp @end\nend:\n", 1),
        ("stop\njmp @end\nend:\n", 2),
        (".DEF END @end\njmp $END\nend:\n", 2),
        (".DEF END @end\nstop\njmp $END\nend:\n", 3),
    ],
)
def test_branch_label_must_point_to_an_instruction_even_in_dead_code(execution_mode: str, source: str, line: int) -> None:
    diagnostics = _diagnostics(source, execution_mode)
    assert any(
        item["severity"] == "error" and item["category"] == "invalid_argument_value"
        and (item.get("source") or {}).get("line") == line
        for item in diagnostics
    ), diagnostics


@pytest.mark.parametrize("execution_mode", ["analysis", "concrete", "simulation"])
def test_end_label_remains_legal_as_an_immediate_value(execution_mode: str) -> None:
    _assert_only_structural_lints(_diagnostics("stop\nmove @end,R0\nend:\n", execution_mode))
    _assert_only_structural_lints(_diagnostics(".DEF PTR @end\nstop\nmove $PTR,R0\nend:\n", execution_mode))
    _assert_only_structural_lints(_diagnostics("stop\njge R0,@end,@done\ndone: stop\nend:\n", execution_mode))


@pytest.mark.parametrize("execution_mode", ["analysis", "concrete", "simulation"])
def test_undefined_bare_operand_is_a_nonfatal_lint_and_bound_parameters_are_silent(execution_mode: str) -> None:
    diagnostics = _diagnostics("stop\nwait COUNT\n", execution_mode)
    assert any(
        item["severity"] == "warning" and item["category"] == "unresolved_symbol"
        and (item.get("source") or {}).get("line") == 2
        for item in diagnostics
    ), diagnostics
    assert not [item for item in diagnostics if item["severity"] == "error"]
    _assert_only_structural_lints(_diagnostics("stop\nwait COUNT\n", execution_mode, params={"COUNT": 4}))
    _assert_only_structural_lints(_diagnostics("stop\nwait {COUNT}\n", execution_mode))


def test_executed_symbolic_operands_do_not_repeat_the_preexecution_lint() -> None:
    diagnostics = _diagnostics("play W0,W1,4\nstop\n")
    unresolved = [item["details"]["symbol"] for item in diagnostics if item["category"] == "unresolved_symbol"]
    assert sorted(unresolved) == ["W0", "W1"]


@pytest.mark.parametrize(
    ("instruction", "valid_values", "invalid_values"),
    [
        ("sw_req {}", (-2147483648, -1, 2147483647), (-2147483649, 2147483648)),
        ("play_pulse {},4", (0, 1, 2047), (-1, 2048)),
        ("play_netzero {},4", (0, 1, 4294967295), (-1, 4294967296)),
        ("fb_com_data 1,{},4", (0, 1, 4294967295), (-1, 4294967296)),
        ("fb_cmd 1,{},4", (0, 1, 4294967295), (-1, 4294967296)),
        ("fb_cmd {},0,4", (0, 256, 511), (-1, 512)),
        ("fb_pop_data {},R1", (0, 256, 65535), (-1, 65536)),
        ("fb_com_data {},0,4", (0, 128, 255), (-1, 256)),
        ("mulu32 {},R0,R1,R2", (0, 1, 4294967295), (-1, 4294967296)),
        ("mulu32 R0,{},R1,R2", (0, 1, 4294967295), (-1, 4294967296)),
    ],
)
def test_assembler_verified_immediate_boundaries(instruction: str, valid_values: tuple[int, ...], invalid_values: tuple[int, ...]) -> None:
    for value in valid_values + invalid_values:
        diagnostics = _diagnostics(f"stop\n{instruction.format(value)}\n")
        errors = [item for item in diagnostics if item["severity"] == "error"]
        if value in valid_values:
            assert errors == [], (instruction, value, errors)
        else:
            assert any(
                item["category"] == "invalid_argument_value" and item["details"].get("value") == value
                for item in errors
            ), (instruction, value, errors)


@pytest.mark.parametrize("op", ["fb_com_cfg", "fb_acq_tb_cfg"])
def test_feedback_configuration_uses_full_assembler_field_widths(op: str) -> None:
    _assert_only_structural_lints(_diagnostics(f"stop\n{op} 3,1023,127,4\n"))
    for operands, invalid_value in [("4,0,1,4", 4), ("0,1024,1,4", 1024), ("0,0,128,4", 128),
                                    ("0,-1,1,4", -1), ("0,0,-1,4", -1)]:
        diagnostics = _diagnostics(f"stop\n{op} {operands}\n")
        assert any(item["category"] == "invalid_argument_value" and item["details"].get("value") == invalid_value for item in diagnostics)


@pytest.mark.parametrize("execution_mode", ["analysis", "concrete", "simulation"])
@pytest.mark.parametrize("instruction", ["play_netzero 0,4", "play_netzero R0,4"])
def test_netzero_has_static_signatures_without_requiring_runtime_support(execution_mode: str, instruction: str) -> None:
    _assert_only_structural_lints(_diagnostics(f"stop\n{instruction}\n", execution_mode))


@pytest.mark.parametrize(
    ("instruction", "category"),
    [("play_netzero 0", "invalid_argument_count"), ("play_netzero R0,R1", "invalid_argument_type"),
     ("mulu32 0,1,R0,R1", "invalid_argument_type")],
)
def test_recognized_instructions_reject_invalid_signatures(instruction: str, category: str) -> None:
    assert any(item["category"] == category for item in _diagnostics(f"stop\n{instruction}\n"))


def test_playback_exposes_bare_name_lint_before_any_instruction_executes() -> None:
    snapshot = PlaybackSession({"s": ("validation.q1asm", "stop\nwait COUNT\n")}).snapshot()
    assert snapshot["playback"]["steps"] == 0
    assert any(item["severity"] == "warning" and item["category"] == "unresolved_symbol" for item in snapshot["diagnostics"])


def test_cli_preserves_symbolic_default_but_strict_q1asm_rejects_undefined_name(tmp_path: Path, capsys) -> None:
    source = tmp_path / "symbolic.q1asm"
    source.write_text("stop\nwait COUNT\n", encoding="utf-8")
    args = ["analyze", str(source), "--format", "vscode-json", "--include-diagnostics"]
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(item["severity"] == "warning" and item["category"] == "unresolved_symbol" for item in payload["diagnostics"])
    assert main(args + ["--q1asm-strict"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert any(item["severity"] == "error" and item["category"] == "unresolved_symbol" for item in payload["diagnostics"])
