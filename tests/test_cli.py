from __future__ import annotations

import json
import webbrowser
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import qbstimeline.cli as cli_module
from qbstimeline.cli import main


def test_analyze_cli_writes_ir_and_q1asm_files(tmp_path: Path) -> None:
    project_file = _write_fake_project(tmp_path)
    out_file = tmp_path / ".qbs_timeline" / "qbs_ir.json"

    exit_code = main(["analyze", "--project", str(project_file), "--out", str(out_file)])

    assert exit_code == 0
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["version"] == "0.1.0"
    assert payload["symbolic_values"] == []
    assert payload["symbolic_pulses"] == []
    assert payload["q1asm_provenance"] == []
    assert payload["capabilities"]["symbolic_pulses"] is False
    assert payload["artifacts"] == {}
    assert payload["q1asm_programs"][0]["file"] == "q1asm/cluster0_module0_seq0.q1asm"
    assert (tmp_path / ".qbs_timeline" / "q1asm" / "cluster0_module0_seq0.q1asm").exists()


def test_build_cli_uses_configured_output_dir(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_file = _write_fake_project(project_dir, output_dir="generated/q1lens")
    output_dir = project_dir / "generated" / "q1lens"

    exit_code = main(["build", "--project", str(project_file)])

    assert exit_code == 0
    assert json.loads((output_dir / "qbs_ir.json").read_text(encoding="utf-8"))["schedule"]["name"] == "cli demo"
    assert "cli demo" in (output_dir / "index.html").read_text(encoding="utf-8")
    assert (output_dir / "q1asm" / "cluster0_module0_seq0.q1asm").is_file()


def test_build_cli_resolves_relative_override_from_project_root_and_opens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    project_file = _write_fake_project(project_dir, output_dir="configured-output")
    output_dir = project_dir / "overrides" / "build"
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)

    exit_code = main(
        [
            "build",
            "--project",
            str(project_file),
            "--out-dir",
            "overrides/build",
            "--open",
        ]
    )

    assert exit_code == 0
    assert (output_dir / "qbs_ir.json").is_file()
    assert (output_dir / "index.html").is_file()
    assert (output_dir / "q1asm" / "cluster0_module0_seq0.q1asm").is_file()
    assert not (project_dir / "configured-output").exists()
    assert opened == [(output_dir / "index.html").resolve().as_uri()]


def test_build_cli_reports_browser_launch_failure_after_writing_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_file = _write_fake_project(tmp_path)
    monkeypatch.setattr(webbrowser, "open", lambda _url: False)

    exit_code = main(["build", "--project", str(project_file), "--open"])

    assert exit_code == 2
    assert (tmp_path / ".qbs_timeline" / "qbs_ir.json").is_file()
    assert (tmp_path / ".qbs_timeline" / "index.html").is_file()
    assert "could not open the default browser" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("command", "extra_args"),
    [
        ("analyze", ["--out", "qbs_ir.json"]),
        ("build", []),
    ],
)
def test_analysis_commands_report_unexpected_project_errors_as_runtime_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    extra_args: list[str],
) -> None:
    project_file = _write_fake_project(tmp_path)

    def fail_analysis(_config: object) -> None:
        raise ValueError("schedule factory failed")

    monkeypatch.setattr(cli_module, "analyze_project", fail_analysis)

    exit_code = main([command, "--project", str(project_file), *extra_args])

    assert exit_code == 2
    assert "schedule factory failed" in capsys.readouterr().err


@pytest.mark.parametrize("strict_field", ["warnings", "ir_diagnostics"])
def test_analyze_cli_strict_writes_ir_then_returns_one_for_quality_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    strict_field: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_file = _write_fake_project(tmp_path)
    ir = {"version": "0.1.0", "warnings": [], "ir_diagnostics": []}
    ir[strict_field] = [{"message": "quality finding"}]
    monkeypatch.setattr(
        cli_module,
        "analyze_project",
        lambda _config: SimpleNamespace(ir=ir, q1asm_programs=[]),
    )
    default_out = tmp_path / "default.json"
    strict_out = tmp_path / "strict.json"

    default_exit = main(["analyze", "--project", str(project_file), "--out", str(default_out)])
    strict_exit = main(
        ["analyze", "--project", str(project_file), "--out", str(strict_out), "--strict"]
    )

    assert default_exit == 0
    assert strict_exit == 1
    assert json.loads(strict_out.read_text(encoding="utf-8"))[strict_field]
    assert "strict mode found" in capsys.readouterr().err


def test_analyze_cli_strict_returns_zero_for_clean_ir(tmp_path: Path) -> None:
    project_file = _write_fake_project(tmp_path)
    out_file = tmp_path / "strict-clean.json"

    exit_code = main(
        ["analyze", "--project", str(project_file), "--out", str(out_file), "--strict"]
    )

    assert exit_code == 0
    assert out_file.is_file()


def test_render_cli_writes_html_from_ir(tmp_path: Path) -> None:
    ir_file = tmp_path / "qbs_ir.json"
    out_file = tmp_path / "index.html"
    ir_file.write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "status": "ok",
                "schedule": {"name": "cli render demo"},
                "operations": [],
                "timing_table": [],
                "q1asm_programs": [],
                "q1asm_by_sequencer": {},
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["render", "--ir", str(ir_file), "--out", str(out_file)])

    assert exit_code == 0
    assert "cli render demo" in out_file.read_text(encoding="utf-8")


def test_diagnose_cli_writes_consistency_report(tmp_path: Path) -> None:
    ir_file = tmp_path / "qbs_ir.json"
    q1timeline_file = tmp_path / "timeline_ir.json"
    out_file = tmp_path / "report.json"
    ir_file.write_text(json.dumps(_diagnose_qbs_ir()), encoding="utf-8")
    q1timeline_file.write_text(json.dumps(_diagnose_q1timeline_ir()), encoding="utf-8")

    exit_code = main(
        [
            "diagnose",
            "--ir",
            str(ir_file),
            "--q1timeline-ir",
            str(q1timeline_file),
            "--out",
            str(out_file),
        ]
    )

    assert exit_code == 0
    report = json.loads(out_file.read_text(encoding="utf-8"))
    assert report["findings"][0]["kind"] == "span_drift"
    assert report["scheduler_bug_candidates"][0]["confidence"] == "needs_minimal_repro"
    assert report["coverage"]["symbolic_blocks"] == {"total": 1, "mapped": 1, "unmapped": 0}


def test_diagnose_cli_strict_writes_report_then_returns_one_for_findings(tmp_path: Path) -> None:
    ir_file = tmp_path / "qbs_ir.json"
    q1timeline_file = tmp_path / "timeline_ir.json"
    out_file = tmp_path / "report.json"
    ir_file.write_text(json.dumps(_diagnose_qbs_ir()), encoding="utf-8")
    q1timeline_file.write_text(json.dumps(_diagnose_q1timeline_ir()), encoding="utf-8")

    exit_code = main(
        [
            "diagnose",
            "--ir",
            str(ir_file),
            "--q1timeline-ir",
            str(q1timeline_file),
            "--out",
            str(out_file),
            "--strict",
        ]
    )

    assert exit_code == 1
    assert json.loads(out_file.read_text(encoding="utf-8"))["findings"]


def test_diagnose_cli_strict_returns_one_for_coverage_gaps(
    tmp_path: Path,
) -> None:
    ir_file = tmp_path / "qbs_ir.json"
    out_file = tmp_path / "report.json"
    qbs_ir = _diagnose_qbs_ir()
    qbs_ir["q1asm_provenance"] = []
    ir_file.write_text(json.dumps(qbs_ir), encoding="utf-8")

    exit_code = main(
        ["diagnose", "--ir", str(ir_file), "--out", str(out_file), "--strict"]
    )

    assert exit_code == 1
    report = json.loads(out_file.read_text(encoding="utf-8"))
    assert report["findings"] == []
    assert {gap["kind"] for gap in report["coverage_gaps"]} == {
        "unmapped_symbolic_block",
        "orphan_timed_instruction",
    }


def test_diagnose_cli_strict_returns_zero_for_clean_report(tmp_path: Path) -> None:
    ir_file = tmp_path / "qbs_ir.json"
    out_file = tmp_path / "report.json"
    ir_file.write_text(json.dumps(_diagnose_qbs_ir()), encoding="utf-8")

    exit_code = main(
        ["diagnose", "--ir", str(ir_file), "--out", str(out_file), "--strict"]
    )

    assert exit_code == 0
    report = json.loads(out_file.read_text(encoding="utf-8"))
    assert report["findings"] == []
    assert report["coverage_gaps"] == []


def test_diagnose_cli_writes_artifact_bundle(tmp_path: Path) -> None:
    ir_file = tmp_path / "qbs_ir.json"
    q1timeline_file = tmp_path / "timeline_ir.json"
    schedule_file = tmp_path / "schedule.py"
    bundle_file = tmp_path / "candidate.zip"
    ir_file.write_text(json.dumps(_diagnose_qbs_ir()), encoding="utf-8")
    q1timeline_file.write_text(json.dumps(_diagnose_q1timeline_ir()), encoding="utf-8")
    schedule_file.write_text("def build_schedule():\n    return None\n", encoding="utf-8")

    exit_code = main(
        [
            "diagnose",
            "--ir",
            str(ir_file),
            "--q1timeline-ir",
            str(q1timeline_file),
            "--bundle",
            str(bundle_file),
            "--schedule",
            str(schedule_file),
        ]
    )

    assert exit_code == 0
    with zipfile.ZipFile(bundle_file) as bundle:
        names = set(bundle.namelist())
        assert {
            "qbs_ir.json",
            "q1timeline_ir.json",
            "diagnostics_report.json",
            "q1asm/seq0.q1asm",
            "schedule/schedule.py",
            "versions.json",
            "manifest.json",
        }.issubset(names)
        report = json.loads(bundle.read("diagnostics_report.json"))
        assert report["summary"]["scheduler_bug_candidate_count"] == 1


def test_diagnose_cli_bundle_uses_q1asm_program_text_fallback(tmp_path: Path) -> None:
    ir_file = tmp_path / "qbs_ir.json"
    bundle_file = tmp_path / "candidate.zip"
    qbs_ir = _diagnose_qbs_ir()
    qbs_ir.pop("q1asm_by_sequencer")
    qbs_ir["q1asm_programs"] = [{"sequencer_id": "seq0", "file": "q1asm/seq0.q1asm", "text": "play 0,1,4\nstop\n"}]
    ir_file.write_text(json.dumps(qbs_ir), encoding="utf-8")

    exit_code = main(["diagnose", "--ir", str(ir_file), "--bundle", str(bundle_file)])

    assert exit_code == 0
    with zipfile.ZipFile(bundle_file) as bundle:
        assert bundle.read("q1asm/seq0.q1asm").decode("utf-8") == "play 0,1,4\nstop\n"


def _diagnose_qbs_ir() -> dict:
    return {
        "version": "0.1.0",
        "schedule": {"name": "diagnose demo"},
        "operations": [{"id": "op", "operation_id": "op", "label": "Op", "abs_time": 20e-9, "duration": 40e-9}],
        "symbolic_pulses": [
            {
                "id": "pulse:op:pulse:0",
                "operation_id": "op",
                "schedulable_id": "op",
                "kind": "pulse",
                "abs_time": 20e-9,
                "duration": 40e-9,
            }
        ],
        "q1asm_programs": [{"sequencer_id": "seq0", "file": "q1asm/seq0.q1asm"}],
        "q1asm_by_sequencer": {"seq0": "play 0,1,4\nwait 36\nstop\n"},
        "q1asm_provenance": [
            {
                "sequencer_id": "seq0",
                "source_id": "pulse:op:pulse:0",
                "q1asm_line_start": 1,
                "q1asm_line_end": 2,
                "instruction_roles": ["play", "wait"],
                "confidence": "compiler",
            }
        ],
    }


def _write_fake_project(project_dir: Path, *, output_dir: str | None = None) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    schedule_file = project_dir / "schedule.py"
    project_file = project_dir / "qbstimeline.yml"
    schedule_file.write_text(
        """
class FakeCompiler:
    def compile(self, schedule):
        return {
            "name": "cli demo",
            "schedulables": {},
            "operations": {},
            "compiled_instructions": {
                "cluster0": {
                    "module0": {
                        "sequencers": {
                            "seq0": {"sequence": {"program": "wait_sync 4\\nstop\\n"}}
                        }
                    }
                }
            },
        }

def build_schedule():
    return {"name": "cli demo"}

def build_compiler():
    return FakeCompiler()
""".lstrip(),
        encoding="utf-8",
    )
    project_text = "schedule:\n  file: schedule.py\n"
    if output_dir is not None:
        project_text += f"outputs:\n  dir: {output_dir}\n"
    project_file.write_text(project_text, encoding="utf-8")
    return project_file


def _diagnose_q1timeline_ir() -> dict:
    return {
        "events": [
            {
                "id": "seq0:e0",
                "kind": "play",
                "sequencer_id": "seq0",
                "t0": {"kind": "concrete", "value": 24},
                "t1": {"kind": "concrete", "value": 28},
                "source": {"file": "q1asm/seq0.q1asm", "line": 1, "raw": "play 0,1,4"},
            },
            {
                "id": "seq0:e1",
                "kind": "wait",
                "sequencer_id": "seq0",
                "t0": {"kind": "concrete", "value": 28},
                "t1": {"kind": "concrete", "value": 64},
                "source": {"file": "q1asm/seq0.q1asm", "line": 2, "raw": "wait 36"},
            },
        ]
    }
