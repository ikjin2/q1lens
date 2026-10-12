from __future__ import annotations

import json
from pathlib import Path

import pytest

from q1timeline.cli import _parse_param_overrides, main


def _write_project(tmp_path: Path) -> tuple[Path, Path]:
    program_file = tmp_path / "drive.q1asm"
    params_file = tmp_path / "params.json"
    project_file = tmp_path / "q1timeline.yml"
    program_file.write_text(
        ".DEF WAIT_NS {WAIT_NS}\n"
        ".DEF PLAY_NS {PLAY_NS}\n"
        "wait_sync 4\n"
        "wait $WAIT_NS\n"
        "play 0,1,$PLAY_NS\n"
        "stop\n",
        encoding="utf-8",
    )
    params_file.write_text('{"WAIT_NS": 12, "PLAY_NS": 20}\n', encoding="utf-8")
    project_file.write_text(
        "sequencers:\n"
        "  - id: drive\n"
        "    file: drive.q1asm\n"
        "params:\n"
        "  file: params.json\n",
        encoding="utf-8",
    )
    return project_file, params_file


def _duration_for(payload: dict, kind: str) -> int:
    event = next(event for event in payload["events"] if event["kind"] == kind)
    return event["duration"]["value"]


def test_set_param_merges_over_project_params_without_mutating_files(tmp_path: Path) -> None:
    project_file, params_file = _write_project(tmp_path)
    original_files = {path: path.read_bytes() for path in (project_file, params_file, tmp_path / "drive.q1asm")}
    out_file = tmp_path / "timeline.json"

    exit_code = main(
        [
            "analyze",
            "--project",
            str(project_file),
            "--out",
            str(out_file),
            "--no-render",
            "--set-param",
            "WAIT_NS=24",
            "--set-param",
            "WAIT_NS=36",
        ]
    )

    assert exit_code == 0
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert _duration_for(payload, "wait") == 36
    assert _duration_for(payload, "play") == 20
    assert payload["analysis"]["parameters"] == [
        {"name": "PLAY_NS", "value_json": "20", "overridden": False},
        {"name": "WAIT_NS", "value_json": "36", "overridden": True, "base_value_json": "12"},
    ]
    assert {path: path.read_bytes() for path in original_files} == original_files


def test_set_param_merges_over_params_file_override(tmp_path: Path) -> None:
    project_file, params_file = _write_project(tmp_path)
    params_file.write_text('{"WAIT_NS": 12, "PLAY_NS": 20, "PROJECT_ONLY": 99}\n', encoding="utf-8")
    override_file = tmp_path / "scenario.json"
    override_file.write_text('{"WAIT_NS": 44, "PLAY_NS": 52}\n', encoding="utf-8")
    original_files = {
        path: path.read_bytes() for path in (project_file, params_file, override_file, tmp_path / "drive.q1asm")
    }
    out_file = tmp_path / "timeline.json"

    exit_code = main(
        [
            "analyze",
            "--project",
            str(project_file),
            "--params",
            str(override_file),
            "--set-param",
            "PLAY_NS=60",
            "--out",
            str(out_file),
            "--no-render",
        ]
    )

    assert exit_code == 0
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert _duration_for(payload, "wait") == 44
    assert _duration_for(payload, "play") == 60
    assert payload["analysis"]["parameters"] == [
        {"name": "PLAY_NS", "value_json": "60", "overridden": True, "base_value_json": "52"},
        {"name": "WAIT_NS", "value_json": "44", "overridden": False},
    ]
    assert {path: path.read_bytes() for path in original_files} == original_files


def test_analysis_parameter_snapshot_without_overrides(tmp_path: Path) -> None:
    project_file, _params_file = _write_project(tmp_path)
    out_file = tmp_path / "timeline.json"

    assert main(["analyze", "--project", str(project_file), "--out", str(out_file), "--no-render"]) == 0

    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["analysis"]["parameters"] == [
        {"name": "PLAY_NS", "value_json": "20", "overridden": False},
        {"name": "WAIT_NS", "value_json": "12", "overridden": False},
    ]


def test_analysis_parameter_snapshot_without_params(tmp_path: Path) -> None:
    program_file = tmp_path / "drive.q1asm"
    program_file.write_text("stop\n", encoding="utf-8")
    out_file = tmp_path / "timeline.json"

    assert main(["analyze", str(program_file), "--out", str(out_file), "--no-render"]) == 0

    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["analysis"]["parameters"] == []


def test_analysis_parameter_snapshot_includes_added_and_equal_overrides(tmp_path: Path) -> None:
    project_file, _params_file = _write_project(tmp_path)
    out_file = tmp_path / "timeline.json"

    assert main(
        [
            "analyze",
            "--project",
            str(project_file),
            "--out",
            str(out_file),
            "--no-render",
            "--set-param",
            "WAIT_NS=12",
            "--set-param",
            "UNUSED=7",
        ]
    ) == 0

    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["analysis"]["parameters"] == [
        {"name": "PLAY_NS", "value_json": "20", "overridden": False},
        {"name": "UNUSED", "value_json": "7", "overridden": True},
        {"name": "WAIT_NS", "value_json": "12", "overridden": True, "base_value_json": "12"},
    ]


def test_analysis_parameter_snapshot_preserves_nested_null_and_large_values(tmp_path: Path) -> None:
    project_file, params_file = _write_project(tmp_path)
    params_file.write_text(
        '{"WAIT_NS":12,"PLAY_NS":20,"UNUSED":{"samples":[9007199254740993,null]},'
        '"BIG":9007199254740993,"NULL":null}\n',
        encoding="utf-8",
    )
    original_files = {path: path.read_bytes() for path in (project_file, params_file, tmp_path / "drive.q1asm")}
    out_file = tmp_path / "timeline.json"

    assert main(
        [
            "analyze",
            "--project",
            str(project_file),
            "--out",
            str(out_file),
            "--no-render",
            "--set-param",
            "BIG=9007199254740995",
        ]
    ) == 0

    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["analysis"]["parameters"] == [
        {
            "name": "BIG",
            "value_json": "9007199254740995",
            "overridden": True,
            "base_value_json": "9007199254740993",
        },
        {"name": "NULL", "value_json": "null", "overridden": False},
        {"name": "PLAY_NS", "value_json": "20", "overridden": False},
        {"name": "UNUSED", "value_json": '{"samples": [9007199254740993, null]}', "overridden": False},
        {"name": "WAIT_NS", "value_json": "12", "overridden": False},
    ]
    assert {path: path.read_bytes() for path in original_files} == original_files


def test_set_param_keeps_dotted_names_literal_and_accepts_json_containers() -> None:
    overrides = _parse_param_overrides(
        [
            'scenario.value={"samples":[1,2,null]}',
            "scenario.value=3",
        ]
    )

    assert overrides == {"scenario.value": 3}


@pytest.mark.parametrize(
    "override",
    [
        "=1",
        "MISSING_EQUALS",
        "VALUE=not-json",
        "FLAG=true",
        "DURATION=1.5",
        'NESTED={"enabled":false}',
    ],
)
def test_set_param_rejects_invalid_json_and_param_values(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    override: str,
) -> None:
    project_file, _params_file = _write_project(tmp_path)

    exit_code = main(
        [
            "analyze",
            "--project",
            str(project_file),
            "--format",
            "vscode-json",
            "--set-param",
            override,
        ]
    )

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["kind"] == "config_error"
    assert payload["diagnostics"][0]["category"] == "invalid_param_override"
