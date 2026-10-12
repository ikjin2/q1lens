from __future__ import annotations

import json
from pathlib import Path

import pytest

from qbstimeline.cli import main


def _project_paths(root: Path) -> dict[str, Path]:
    paths = {
        "ir": root / "qbs.json",
        "q1timeline-ir": root / "q1timeline.json",
        "schedule": root / "schedule.py",
        "out": root / "report.json",
        "bundle": root / "new-output" / "bundle.zip",
    }
    paths["ir"].write_text(json.dumps({
        "schedule": {"name": "path collision fixture"},
        "symbolic_pulses": [],
        "q1asm_provenance": [],
        "q1asm_by_sequencer": {"seq0": "stop\n"},
    }), encoding="utf-8")
    paths["q1timeline-ir"].write_text('{"events": []}\n', encoding="utf-8")
    paths["schedule"].write_bytes(b"raise AssertionError('source must not execute')\n")
    paths["out"].write_bytes(b"Existing report must survive a rejected request.\n")
    return paths


def _snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


def _diagnose(paths: dict[str, Path]) -> int:
    args = ["diagnose"]
    for option, path in paths.items():
        args.extend([f"--{option}", str(path)])
    return main(args)


@pytest.mark.parametrize(("output", "other"), [
    pytest.param("out", "schedule", id="report-over-source"),
    pytest.param("bundle", "schedule", id="bundle-over-source"),
    pytest.param("bundle", "ir", id="bundle-over-qbs-ir"),
    pytest.param("out", "q1timeline-ir", id="report-over-q1-ir"),
    pytest.param("out", "bundle", id="report-and-bundle-same-path"),
])
def test_diagnose_rejects_path_collisions_before_any_write(
    tmp_path: Path, capsys, output: str, other: str,
) -> None:
    paths = _project_paths(tmp_path)
    paths[output] = paths[other]
    before = _snapshot(tmp_path)

    exit_code = _diagnose(paths)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert f"--{output}" in captured.err
    assert f"--{other}" in captured.err
    assert _snapshot(tmp_path) == before


def test_diagnose_rejects_resolved_relative_alias_before_any_write(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    paths = _project_paths(tmp_path)
    (tmp_path / "alias-dir").mkdir()
    monkeypatch.chdir(tmp_path)
    paths["out"] = Path("alias-dir") / ".." / "schedule.py"
    before = _snapshot(tmp_path)

    exit_code = _diagnose(paths)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "--out" in captured.err
    assert "--schedule" in captured.err
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("other", ["schedule", "bundle"])
def test_diagnose_rejects_existing_hardlink_alias_before_any_write(
    tmp_path: Path, capsys, other: str,
) -> None:
    paths = _project_paths(tmp_path)
    if other == "bundle":
        paths["bundle"].parent.mkdir()
        paths["bundle"].write_bytes(b"Existing bundle must survive a rejected request.\n")
    alias = tmp_path / "report-alias.json"
    try:
        alias.hardlink_to(paths[other])
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Hardlinks are unavailable: {exc}")
    paths["out"] = alias
    before = _snapshot(tmp_path)

    exit_code = _diagnose(paths)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "--out" in captured.err
    assert f"--{other}" in captured.err
    assert _snapshot(tmp_path) == before


def test_diagnose_can_replace_unrelated_existing_outputs(tmp_path: Path) -> None:
    paths = _project_paths(tmp_path)
    paths["bundle"].parent.mkdir()
    paths["bundle"].write_bytes(b"Old bundle contents.\n")
    inputs = {paths[key]: paths[key].read_bytes() for key in ("ir", "q1timeline-ir", "schedule")}

    assert _diagnose(paths) == 0

    report = json.loads(paths["out"].read_text(encoding="utf-8"))
    assert report["summary"]["finding_count"] == 0
    assert main(["verify-bundle", "--bundle", str(paths["bundle"])]) == 0
    assert {path: path.read_bytes() for path in inputs} == inputs
