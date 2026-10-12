from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


EXAMPLES = Path(__file__).parents[1] / "examples" / "simulation-verification"
CASES = ("01-digital", "02-communication", "03-signals", "04-underflow", "05-supplied-input")


@pytest.mark.parametrize("source", [EXAMPLES / "verify.py", Path(__file__).parents[1] / "tools/verify_simulation_wheel.py"])
def test_optimized_python_cannot_report_unchecked_success(tmp_path, source):
    script = tmp_path / "verify.py"
    script.write_bytes(source.read_bytes())
    run = subprocess.run([sys.executable, "-O", str(script)], text=True, capture_output=True)
    assert run.returncode != 0
    assert "requires Python assertions" in run.stderr
    assert not (tmp_path / ".verification" / "report.json").exists()


@pytest.mark.parametrize("wheel_count", [0, 2])
def test_wheel_verification_invalid_selection_clears_old_success(tmp_path, wheel_count):
    for index in range(wheel_count):
        (tmp_path / f"q1lens-{index}.whl").write_bytes(b"unused")
    output = tmp_path / "evidence"
    output.mkdir()
    report = output / "report.json"
    report.write_text('{"passed": true}', encoding="utf-8")
    script = Path(__file__).parents[1] / "tools/verify_simulation_wheel.py"
    result = subprocess.run([sys.executable, str(script), "--wheel", str(tmp_path / "*.whl"), "--out", str(output)],
                            text=True, capture_output=True)
    assert result.returncode != 0
    assert "exactly one" in result.stderr
    assert json.loads(report.read_text(encoding="utf-8"))["passed"] is False


@pytest.fixture
def verifier(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("simulation_verify", EXAMPLES / "verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    for case in CASES:
        directory = tmp_path / case
        directory.mkdir()
        (directory / "q1timeline.yml").write_text("# mocked CLI input\n", encoding="utf-8")
    return module


@pytest.fixture
def fixtures(tmp_path):
    # The harness tests need valid analyzer output; the real examples are checked
    # separately against independent numerical expectations in verify.check().
    from q1timeline.cli import main

    result = {}
    for case in CASES:
        output = tmp_path / f"fixture-{case}.json"
        code = main(["analyze", "--project", str(EXAMPLES / case / "q1timeline.yml"),
                     "--out", str(output), "--format", "vscode-json", "--summary-only"])
        assert code == (2 if case == "04-underflow" else 0)
        result[case] = json.loads(output.read_text(encoding="utf-8"))
    return result


def mock_cli(verifier, monkeypatch, fixtures, fault=None):
    outputs = []

    def run(command, **kwargs):
        project = Path(command[command.index("--project") + 1])
        output = Path(command[command.index("--out") + 1])
        outputs.append(output)
        case = project.parent.name
        if fault and case == "04-underflow":
            if fault == "timeout":
                raise subprocess.TimeoutExpired(command, 60)
            if fault == "start_failure":
                raise OSError("cannot start analyzer")
            if fault == "missing_output":
                return subprocess.CompletedProcess(command, 2, "configuration failed", "")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("broken json" if fault == "bad_json" and case == "04-underflow"
                          else json.dumps(fixtures[case]), encoding="utf-8")
        code = 2 if case == "04-underflow" else 0
        if fault == "wrong_exit" and case == "04-underflow":
            code = 1
        return subprocess.CompletedProcess(command, code, "", "")

    monkeypatch.setattr(verifier.subprocess, "run", run)
    return outputs


@pytest.mark.parametrize("fault", ["missing_output", "bad_json", "timeout", "start_failure", "wrong_exit"])
def test_harness_records_cli_failures_even_with_stale_underflow_ir(verifier, fixtures, monkeypatch, fault):
    stale = verifier.ROOT / "04-underflow" / ".q1timeline" / "timeline_ir.json"
    stale.parent.mkdir()
    stale.write_text(json.dumps(fixtures["04-underflow"]), encoding="utf-8")
    mock_cli(verifier, monkeypatch, fixtures, fault)
    assert verifier.main() == 1
    report = json.loads((verifier.ROOT / ".verification" / "report.json").read_text(encoding="utf-8"))
    assert len(report) == 5
    assert next(item for item in report if item["case"] == "04-underflow")["passed"] is False


def test_harness_requires_every_named_example(verifier, fixtures, monkeypatch):
    (verifier.ROOT / "05-supplied-input" / "q1timeline.yml").unlink()
    mock_cli(verifier, monkeypatch, fixtures)
    assert verifier.main() == 1


def test_each_repetition_uses_a_new_output_and_keeps_verified_preview(verifier, fixtures, monkeypatch):
    outputs = mock_cli(verifier, monkeypatch, fixtures)
    assert verifier.main() == 0
    assert len(outputs) == len(set(outputs)) == 10
    for case in CASES:
        ir = json.loads((verifier.ROOT / case / ".q1timeline" / "timeline_ir.json").read_text(encoding="utf-8"))
        assert verifier.check(case, ir)


def test_interrupted_rerun_replaces_old_success_report(verifier, monkeypatch):
    report_path = verifier.ROOT / ".verification" / "report.json"
    report_path.parent.mkdir()
    report_path.write_text('[{"passed": true}]', encoding="utf-8")

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(verifier.subprocess, "run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        verifier.main()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert [row["case"] for row in report] == list(CASES)
    assert not any(row["passed"] for row in report)
    assert report[0]["status"] == "interrupted"


def test_signal_golden_rejects_bypassed_filter(verifier, fixtures):
    ir = fixtures["03-signals"]
    ir["simulation"]["readout"]["acquisitions"][0].update(i=32, mean_i=0.5)
    with pytest.raises(AssertionError):
        verifier.check("03-signals", ir)


def test_underflow_requires_specific_diagnostic_and_target(verifier, fixtures):
    ir = fixtures["04-underflow"]
    ir["diagnostics"][0]["category"] = "invalid_configuration"
    with pytest.raises(AssertionError):
        verifier.check("04-underflow", ir)
