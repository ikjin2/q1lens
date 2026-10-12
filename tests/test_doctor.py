from __future__ import annotations

import json
from pathlib import Path

import pytest

from q1lens.cli import main as q1lens_main
from qbstimeline.cli import main


def _write_python_project(tmp_path: Path, source: str) -> Path:
    schedule_file = tmp_path / "schedule.py"
    schedule_file.write_text(source, encoding="utf-8")
    project_file = tmp_path / "qbstimeline.yml"
    project_file.write_text("schedule:\n  file: schedule.py\n", encoding="utf-8")
    return project_file


def _write_notebook_project(tmp_path: Path, cells: list[tuple[str, list[str] | None]]) -> Path:
    (tmp_path / "experiment.ipynb").write_text(
        json.dumps({"cells": [
            {"cell_type": "code", "source": source, "metadata": {"tags": tags}}
            for source, tags in cells
        ], "nbformat": 4}),
        encoding="utf-8",
    )
    project_file = tmp_path / "qbstimeline.yml"
    project_file.write_text(
        "schedule:\n  notebook: experiment.ipynb\n"
        "  setup_tags: [setup]\n  schedule_tag: schedule\n",
        encoding="utf-8",
    )
    return project_file


def test_doctor_json_passes_without_importing_user_code_or_writing(tmp_path: Path, capsys) -> None:
    project_file = _write_python_project(
        tmp_path,
        "raise RuntimeError('doctor must not import this module')\n"
        "\n"
        "def build_schedule():\n"
        "    return None\n"
        "\n"
        "build_compiler = lambda: None\n",
    )

    exit_code = main(["doctor", "--project", str(project_file), "--json"])

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["version"] == "1.0"
    assert report["status"] == "pass"
    assert report["summary"] == {"failed": 0, "passed": 4, "total": 4}
    assert [check["id"] for check in report["checks"]] == [
        "project.config",
        "schedule.source",
        "schedule.entrypoints",
        "output.writable_ancestor",
    ]
    assert report["environment"]["python"]["executable"]
    assert report["environment"]["package"]["name"] == "q1lens"
    assert not (tmp_path / ".qbs_timeline").exists()


def test_q1lens_doctor_human_output_uses_q1lens_name(tmp_path: Path, capsys) -> None:
    project_file = _write_python_project(
        tmp_path,
        "def build_schedule():\n    return None\n"
        "def build_compiler():\n    return None\n",
    )

    exit_code = q1lens_main(["doctor", "--project", str(project_file)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out.startswith("q1lens doctor: PASS\n")
    assert "[PASS] schedule.entrypoints" in captured.out
    assert captured.out.endswith("Checks: 4 passed, 0 failed, 4 total\n")


def test_doctor_reports_invalid_config_as_check_failure(tmp_path: Path, capsys) -> None:
    project_file = tmp_path / "qbstimeline.yml"
    project_file.write_text("schedule: [not, a, mapping]\n", encoding="utf-8")

    exit_code = main(["doctor", "--project", str(project_file), "--json"])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 1
    assert captured.err == ""
    assert report["status"] == "fail"
    assert report["checks"][0]["id"] == "project.config"
    assert report["checks"][0]["status"] == "fail"


def test_doctor_reports_missing_python_entrypoint(tmp_path: Path, capsys) -> None:
    project_file = _write_python_project(
        tmp_path,
        "def build_schedule():\n    return None\n"
        "build_compiler: object\n",
    )

    exit_code = main(["doctor", "--project", str(project_file), "--json"])

    report = json.loads(capsys.readouterr().out)
    entrypoint_check = next(
        check for check in report["checks"] if check["id"] == "schedule.entrypoints"
    )
    assert exit_code == 1
    assert entrypoint_check["status"] == "fail"
    assert entrypoint_check["details"]["missing"] == ["build_compiler"]


@pytest.mark.parametrize(("redefinition", "expected_kind"), [
    pytest.param("", None, id="assigned-then-deleted"),
    pytest.param("def build_compiler():\n    return None\n", "function", id="deleted-then-redefined"),
])
def test_doctor_preserves_python_entrypoint_deletion_semantics(
    tmp_path: Path, capsys, redefinition: str, expected_kind: str | None,
) -> None:
    project_file = _write_python_project(
        tmp_path,
        "raise RuntimeError('doctor must not execute project code')\n"
        "def build_schedule():\n    return None\n"
        "build_compiler = lambda: None\ndel build_compiler\n" + redefinition,
    )
    original_files = {path: path.read_bytes() for path in tmp_path.iterdir()}

    exit_code = main(["doctor", "--project", str(project_file), "--json"])

    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    entrypoints = next(check for check in report["checks"] if check["id"] == "schedule.entrypoints")
    assert exit_code == (1 if expected_kind is None else 0)
    assert entrypoints["status"] == ("fail" if expected_kind is None else "pass")
    assert entrypoints["details"]["missing"] == (["build_compiler"] if expected_kind is None else [])
    assert entrypoints["details"]["found"].get("build_compiler") == expected_kind
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == original_files


def test_doctor_checks_direct_notebook_tags_and_variables(tmp_path: Path, capsys) -> None:
    notebook = tmp_path / "experiment.ipynb"
    notebook.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "source": ["hw_agent = object()\n"],
                        "metadata": {"tags": ["qbstimeline-setup"]},
                    },
                    {
                        "cell_type": "code",
                        "source": ["two_tone_sched = object()\n"],
                        "metadata": {"tags": ["qbstimeline-schedule"]},
                    },
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )
    project_file = tmp_path / "qbstimeline.yml"
    project_file.write_text(
        "schedule:\n"
        "  notebook: experiment.ipynb\n"
        "  setup_tags: [qbstimeline-setup]\n"
        "  schedule_variable: two_tone_sched\n"
        "  compiler_variable: hw_agent\n",
        encoding="utf-8",
    )

    exit_code = main(["doctor", "--project", str(project_file), "--json"])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert [check["id"] for check in report["checks"]] == [
        "project.config",
        "schedule.source",
        "notebook.json",
        "notebook.tags",
        "notebook.variables",
        "output.writable_ancestor",
    ]
    variables = next(
        check for check in report["checks"] if check["id"] == "notebook.variables"
    )
    assert variables["details"]["found"]["hw_agent"]["cell_index"] == 0
    assert variables["details"]["found"]["two_tone_sched"]["cell_index"] == 1


def test_doctor_reports_missing_notebook_tag_before_variable_check(tmp_path: Path, capsys) -> None:
    notebook = tmp_path / "experiment.ipynb"
    notebook.write_text(json.dumps({"cells": [], "nbformat": 4}), encoding="utf-8")
    project_file = tmp_path / "qbstimeline.yml"
    project_file.write_text(
        "schedule:\n"
        "  notebook: experiment.ipynb\n"
        "  setup_tags: [qbstimeline-setup]\n",
        encoding="utf-8",
    )

    exit_code = main(["doctor", "--project", str(project_file), "--json"])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert report["checks"][-2]["id"] == "notebook.tags"
    assert report["checks"][-2]["status"] == "fail"
    assert all(check["id"] != "notebook.variables" for check in report["checks"])


@pytest.mark.parametrize(("schedule_source", "compiler_cell"), [
    pytest.param("del compiler\nschedule = object()\n", None, id="delete-earlier-binding"),
    pytest.param("compiler = object()\ndel compiler\nschedule = object()\n", None, id="define-then-delete"),
    pytest.param("del compiler\ncompiler = object()\nschedule = object()\n", 2, id="delete-then-redefine"),
    pytest.param("schedule = object()\n", 0, id="ignore-deletion-in-unselected-cell"),
])
def test_doctor_tracks_notebook_bindings_across_selected_cells(
    tmp_path: Path, capsys, schedule_source: str, compiler_cell: int | None,
) -> None:
    project_file = _write_notebook_project(tmp_path, [
        ("raise RuntimeError('doctor must not execute notebook cells')\ncompiler = object()\n", ["setup"]),
        ("del compiler\n", ["ignored"]),
        (schedule_source, ["schedule"]),
    ])
    original_files = {path: path.read_bytes() for path in tmp_path.iterdir()}

    exit_code = main(["doctor", "--project", str(project_file), "--json"])

    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    variables = next(check for check in report["checks"] if check["id"] == "notebook.variables")
    assert exit_code == (1 if compiler_cell is None else 0)
    assert variables["status"] == ("fail" if compiler_cell is None else "pass")
    if compiler_cell is None:
        assert variables["details"]["missing"] == ["compiler"]
        assert "compiler" not in variables["details"]["found"]
    else:
        assert variables["details"]["found"]["compiler"]["cell_index"] == compiler_cell
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == original_files


def test_doctor_reports_null_notebook_tags_as_json_check_failure(tmp_path: Path, capsys) -> None:
    project_file = _write_notebook_project(tmp_path, [
        ("raise RuntimeError('doctor must not execute notebook cells')\n", None),
        ("schedule = compiler = object()\n", ["schedule"]),
    ])
    original_files = {path: path.read_bytes() for path in tmp_path.iterdir()}

    exit_code = main(["doctor", "--project", str(project_file), "--json"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report["version"] == "1.0"
    assert report["status"] == "fail"
    notebook_check = next(check for check in report["checks"] if check["id"] == "notebook.json")
    assert notebook_check["status"] == "fail"
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == original_files


@pytest.mark.parametrize("json_output", [True, False], ids=["json", "human"])
def test_doctor_reports_project_encoding_error_as_check_failure(tmp_path: Path, capsys, json_output: bool) -> None:
    project_file = _write_python_project(tmp_path, "raise RuntimeError('doctor must not execute project code')\n")
    project_file.write_text(project_file.read_text(encoding="utf-8"), encoding="utf-16")
    original_files = {path: path.read_bytes() for path in tmp_path.iterdir()}

    exit_code = main(["doctor", "--project", str(project_file), *(["--json"] if json_output else [])])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err == ""
    if json_output:
        report = json.loads(captured.out)
        assert report["version"] == "1.0"
        assert report["status"] == "fail"
        assert report["checks"][0]["id"] == "project.config"
        assert report["checks"][0]["status"] == "fail"
        message = report["checks"][0]["message"]
    else:
        assert "[FAIL] project.config" in captured.out
        message = captured.out
    assert "utf-8" in message.lower() or "encoding" in message.lower()
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == original_files


def test_doctor_fails_when_a_file_blocks_the_output_path(tmp_path: Path, capsys) -> None:
    project_file = _write_python_project(
        tmp_path,
        "def build_schedule():\n    return None\n"
        "def build_compiler():\n    return None\n",
    )
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    project_file.write_text(
        "schedule:\n  file: schedule.py\noutputs:\n  dir: blocked/child\n",
        encoding="utf-8",
    )

    exit_code = main(["doctor", "--project", str(project_file), "--json"])

    report = json.loads(capsys.readouterr().out)
    output_check = report["checks"][-1]
    assert exit_code == 1
    assert output_check["id"] == "output.writable_ancestor"
    assert output_check["status"] == "fail"
    assert output_check["details"]["existing_path"] == str(blocker)


def test_doctor_returns_two_for_unexpected_runtime_error(tmp_path: Path, capsys, monkeypatch) -> None:
    project_file = tmp_path / "qbstimeline.yml"
    monkeypatch.setattr(
        "qbstimeline.cli.inspect_project",
        lambda path: (_ for _ in ()).throw(RuntimeError("unexpected")),
    )

    exit_code = main(["doctor", "--project", str(project_file)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "qbstimeline doctor: unexpected\n"
