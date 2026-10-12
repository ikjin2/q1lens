from __future__ import annotations

import json
from pathlib import Path

import pytest

from q1timeline.cli import main
from q1timeline.project import ConfigLoadError, load_project_config
from q1timeline.watch.watcher import run_watch_once


PROGRAM = """move 3,R0
move 4,R1
nop
nop
wait_sync 4
again: wait R1
add R1,4,R1
nop
nop
loop R0,@again
stop
"""


def _project(tmp_path: Path, analysis: str = "") -> Path:
    (tmp_path / "seq0.q1asm").write_text(PROGRAM, encoding="utf-8")
    path = tmp_path / "q1timeline.yml"
    path.write_text("sequencers:\n  - id: seq0\n    file: seq0.q1asm\n" + analysis, encoding="utf-8")
    return path


def test_execution_project_defaults_preserve_analysis(tmp_path: Path) -> None:
    config = load_project_config(_project(tmp_path))
    assert config.analysis.execution_mode == "analysis"
    assert config.analysis.max_steps == 10000


@pytest.mark.parametrize("value", ["0", "-1", "true", "1.5", '"12"', "null"])
def test_project_rejects_invalid_execution_step_limits(tmp_path: Path, value: str) -> None:
    project = _project(tmp_path, f"analysis:\n  max_steps: {value}\n")
    with pytest.raises(ConfigLoadError) as error:
        load_project_config(project)
    assert any("analysis.max_steps" in item.message for item in error.value.diagnostics)


def test_project_rejects_unknown_execution_mode(tmp_path: Path) -> None:
    with pytest.raises(ConfigLoadError) as error:
        load_project_config(_project(tmp_path, "analysis:\n  execution_mode: live\n"))
    assert any("analysis.execution_mode" in item.message for item in error.value.diagnostics)


def test_cli_uses_project_concrete_mode_and_retains_execution_summary(tmp_path: Path, capsys) -> None:
    project = _project(tmp_path, "analysis:\n  execution_mode: concrete\n  max_steps: 100\n")
    out = tmp_path / "timeline_ir.json"
    code = main(["analyze", "--project", str(project), "--out", str(out), "--format", "vscode-json", "--summary-only"])
    assert code == 0
    ir = json.loads(out.read_text(encoding="utf-8"))
    summary = json.loads(capsys.readouterr().out)
    assert ir["execution"]["seq0"]["status"] == "completed"
    assert ir["execution"]["seq0"]["max_steps"] == 100
    assert summary["execution"] == ir["execution"]
    assert [event["duration"]["value"] for event in ir["events"] if event["kind"] == "wait"] == [4, 8, 12]


def test_cli_execution_flags_override_project(tmp_path: Path) -> None:
    project = _project(tmp_path, "analysis:\n  execution_mode: analysis\n  max_steps: 100\n")
    out = tmp_path / "timeline_ir.json"
    code = main(["analyze", "--project", str(project), "--execution-mode", "concrete", "--max-steps", "7", "--out", str(out)])
    assert code == 0
    execution = json.loads(out.read_text(encoding="utf-8"))["execution"]["seq0"]
    assert execution["status"] == "step_limit"
    assert execution["steps"] == execution["max_steps"] == 7


def test_cli_can_return_from_project_concrete_to_analysis(tmp_path: Path) -> None:
    project = _project(tmp_path, "analysis:\n  execution_mode: concrete\n")
    out = tmp_path / "timeline_ir.json"
    assert main(["analyze", "--project", str(project), "--execution-mode", "analysis", "--out", str(out)]) == 0
    assert any(event["kind"] == "loop_block" for event in json.loads(out.read_text(encoding="utf-8"))["events"])


@pytest.mark.parametrize("limit", ["0", "-10"])
def test_cli_invalid_step_limit_returns_structured_error(tmp_path: Path, capsys, limit: str) -> None:
    project = _project(tmp_path)
    assert main(["analyze", "--project", str(project), "--max-steps", limit, "--format", "vscode-json"]) == 2
    assert "positive integer" in json.loads(capsys.readouterr().out)["error"]["message"]


def test_watch_respects_project_execution_mode(tmp_path: Path) -> None:
    project = _project(tmp_path, "analysis:\n  execution_mode: concrete\n  max_steps: 100\n")
    result = run_watch_once(project, tmp_path / "watched")
    assert result.ok
    assert json.loads(result.timeline_ir.read_text(encoding="utf-8"))["execution"]["seq0"]["status"] == "completed"
