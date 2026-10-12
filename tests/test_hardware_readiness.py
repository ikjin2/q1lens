from __future__ import annotations

import json
from pathlib import Path

import pytest

from q1timeline.analysis.api import analyze_documents, analyze_text
from q1timeline.cli import main


READINESS_CATEGORIES = {
    "acquisition_bin_reuse",
    "pending_hardware_state",
    "scope_without_acquisition",
}


def _readiness_diagnostics(ir: dict) -> list[dict]:
    return [
        diagnostic
        for diagnostic in ir["diagnostics"]
        if diagnostic["category"] in READINESS_CATEGORIES
    ]


def test_hardware_readiness_is_opt_in_and_preserves_default_schema() -> None:
    source = "set_freq 100\nstop\n"

    default_ir = analyze_text(source, file="seq0.q1asm")
    explicitly_disabled_ir = analyze_text(
        source,
        file="seq0.q1asm",
        hardware_readiness=False,
    )

    assert default_ir == explicitly_disabled_ir
    assert "hardware_readiness" not in default_ir
    assert _readiness_diagnostics(default_ir) == []


def test_pending_terminal_state_has_deterministic_summary_and_safe_stop_edit() -> None:
    ir = analyze_text(
        "set_freq 100\nstop\n",
        file="seq0.q1asm",
        hardware_readiness=True,
    )

    assert ir["hardware_readiness"] == {
        "enabled": True,
        "status": "warning",
        "diagnostic_counts": {"error": 0, "warning": 1, "info": 0, "hint": 0},
        "sequencers": [
            {
                "id": "seq0",
                "status": "warning",
                "diagnostic_counts": {"error": 0, "warning": 1, "info": 0, "hint": 0},
                "checks": {
                    "terminal_latched_state": {
                        "status": "warning",
                        "pending_fields": ["frequency"],
                    },
                    "scope_acquisition": {
                        "status": "pass",
                        "scope_enabled_without_acquisition": False,
                    },
                    "acquisition_bins": {"status": "pass", "reused_bins": []},
                },
            }
        ],
    }
    diagnostic = _readiness_diagnostics(ir)[0]
    assert diagnostic["severity"] == "warning"
    assert diagnostic["category"] == "pending_hardware_state"
    assert diagnostic["source"] == {
        "file": "seq0.q1asm",
        "line": 2,
        "column": 1,
        "raw": "stop",
    }
    assert diagnostic["details"]["suggested_edit"] == {
        "title": "Apply pending hardware state before stop",
        "file": "seq0.q1asm",
        "range": {
            "start": {"line": 2, "column": 1},
            "end": {"line": 2, "column": 1},
        },
        "new_text": "upd_param 4\n",
    }


def test_rt_instruction_applies_latched_state_without_requiring_upd_param() -> None:
    ir = analyze_text(
        "set_freq 100\nplay 0,1,4\nstop\n",
        file="seq0.q1asm",
        hardware_readiness=True,
    )

    assert ir["hardware_readiness"]["status"] == "ready"
    assert _readiness_diagnostics(ir) == []


def test_pending_state_without_terminal_stop_has_no_suggested_edit() -> None:
    ir = analyze_text(
        "set_freq 100\n",
        file="seq0.q1asm",
        hardware_readiness=True,
    )

    diagnostic = _readiness_diagnostics(ir)[0]
    assert diagnostic["category"] == "pending_hardware_state"
    assert "suggested_edit" not in diagnostic["details"]


def test_ambiguous_branch_skips_claims_about_terminal_state() -> None:
    ir = analyze_text(
        "set_freq 100\njlt R0,1,@done\nupd_param 4\ndone: stop\n",
        file="seq0.q1asm",
        hardware_readiness=True,
    )

    sequencer = ir["hardware_readiness"]["sequencers"][0]
    assert sequencer["status"] == "incomplete"
    assert sequencer["checks"]["terminal_latched_state"] == {
        "status": "skipped",
        "pending_fields": [],
    }
    assert _readiness_diagnostics(ir) == []


def test_scope_and_concrete_acquisition_bin_hints_are_informational() -> None:
    scope_ir = analyze_text(
        "set_scope_en 1\nplay 0,1,4\nstop\n",
        file="scope.q1asm",
        hardware_readiness=True,
    )
    bins_ir = analyze_text(
        "acquire 0,2,4\nacquire 0,2,4\nstop\n",
        file="bins.q1asm",
        hardware_readiness=True,
    )

    scope_diagnostic = _readiness_diagnostics(scope_ir)[0]
    assert (scope_diagnostic["severity"], scope_diagnostic["category"]) == (
        "info",
        "scope_without_acquisition",
    )
    bins_diagnostic = _readiness_diagnostics(bins_ir)[0]
    assert (bins_diagnostic["severity"], bins_diagnostic["category"]) == (
        "info",
        "acquisition_bin_reuse",
    )
    assert bins_diagnostic["details"] == {
        "acquisition_index": 0,
        "bin_index": 2,
        "use_count": 2,
        "sources": [
            {"file": "bins.q1asm", "line": 1, "column": 1, "raw": "acquire 0,2,4"},
            {"file": "bins.q1asm", "line": 2, "column": 1, "raw": "acquire 0,2,4"},
        ],
    }


def test_symbolic_acquisition_bins_do_not_produce_reuse_claims() -> None:
    ir = analyze_text(
        "acquire 0,{BIN},4\nacquire 0,{BIN},4\nstop\n",
        file="bins.q1asm",
        hardware_readiness=True,
    )

    assert not any(
        diagnostic["category"] == "acquisition_bin_reuse"
        for diagnostic in ir["diagnostics"]
    )


def test_symbolic_later_acquisition_suppresses_scope_absence_hint() -> None:
    ir = analyze_text(
        "set_scope_en 1\nplay 0,1,4\nacquire 0,{BIN},4\nstop\n",
        file="scope.q1asm",
        hardware_readiness=True,
    )

    assert not any(
        diagnostic["category"] == "scope_without_acquisition"
        for diagnostic in ir["diagnostics"]
    )


@pytest.mark.parametrize("final_scope, expected_hint", [("1", True), ("0", False), ("R1", False)])
def test_scope_hint_uses_latest_applied_value_after_symbolic_setting(
    final_scope: str, expected_hint: bool,
) -> None:
    ir = analyze_text(
        "set_scope_en R0\nupd_param 4\n"
        f"set_scope_en {final_scope}\nupd_param 4\nstop\n",
        file="scope.q1asm",
        hardware_readiness=True,
    )

    hints = [
        diagnostic for diagnostic in _readiness_diagnostics(ir)
        if diagnostic["category"] == "scope_without_acquisition"
    ]
    assert bool(hints) is expected_hint
    if expected_hint:
        assert hints[0]["source"]["line"] == 3


def test_invalid_instruction_skips_hardware_readiness_claims() -> None:
    ir = analyze_text(
        "set_freq 100\nplay 1\nstop\n",
        file="invalid.q1asm",
        hardware_readiness=True,
    )

    assert any(diagnostic["severity"] == "error" for diagnostic in ir["diagnostics"])
    assert ir["hardware_readiness"]["status"] == "incomplete"
    assert all(
        check["status"] == "skipped"
        for check in ir["hardware_readiness"]["sequencers"][0]["checks"].values()
    )
    assert _readiness_diagnostics(ir) == []


@pytest.mark.parametrize("execution_mode", ["concrete", "simulation"])
@pytest.mark.parametrize("multiple_documents", [False, True])
def test_readiness_preserves_execution_and_critical_path_metadata(
    execution_mode: str, multiple_documents: bool,
) -> None:
    source = "set_freq 100\nstop\n"
    options = {"execution_mode": execution_mode, "hardware_readiness": True, "alignment_mode": "none"}
    if multiple_documents:
        ir = analyze_documents({"seq0.q1asm": source, "seq1.q1asm": "stop\n"}, **options)
    else:
        ir = analyze_text(source, file="seq0.q1asm", **options)

    assert ir["execution"]["seq0"]["status"] == "completed"
    assert ir["execution"]["seq0"]["mode"] == execution_mode
    assert ir["hardware_readiness"]["status"] == "warning"
    assert len(ir["hardware_readiness"]["sequencers"]) == (2 if multiple_documents else 1)
    assert [item["category"] for item in _readiness_diagnostics(ir)] == ["pending_hardware_state"]
    assert "critical_path" in ir
    if execution_mode == "simulation":
        assert "seq0" in ir["simulation"]


@pytest.mark.parametrize("execution_mode", ["analysis", "concrete", "simulation"])
def test_cli_flag_serializes_readiness_and_controls_strict_failure(tmp_path: Path, execution_mode: str) -> None:
    source_file = tmp_path / "seq0.q1asm"
    disabled_out = tmp_path / "disabled.json"
    enabled_out = tmp_path / "enabled.json"
    source_file.write_text("set_freq 100\nstop\n", encoding="utf-8")

    assert main(
        ["analyze", str(source_file), "--out", str(disabled_out), "--align", "none", "--strict", "--execution-mode", execution_mode]
    ) == 0
    assert main(
        [
            "analyze",
            str(source_file),
            "--out",
            str(enabled_out),
            "--hardware-readiness",
            "--align",
            "none",
            "--strict",
            "--execution-mode",
            execution_mode,
        ]
    ) == 2

    disabled_ir = json.loads(disabled_out.read_text(encoding="utf-8"))
    enabled_ir = json.loads(enabled_out.read_text(encoding="utf-8"))
    assert "hardware_readiness" not in disabled_ir
    assert enabled_ir["hardware_readiness"]["enabled"] is True
    diagnostic = next(
        item for item in enabled_ir["diagnostics"]
        if item["category"] == "pending_hardware_state"
    )
    assert diagnostic["details"]["suggested_edit"]["range"] == {
        "start": {"line": 2, "column": 1},
        "end": {"line": 2, "column": 1},
    }
