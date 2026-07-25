from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigLoadError(ValueError):
    """Raised when a qbstimeline project file cannot be loaded."""


@dataclass(frozen=True)
class NotebookScheduleConfig:
    notebook: Path
    setup_tags: tuple[str, ...]
    schedule_tag: str
    schedule_variable: str
    compiler_variable: str


@dataclass(frozen=True)
class ProjectConfig:
    root: Path
    project_file: Path
    schedule_file: Path | None
    schedule_entrypoint: str
    compiler_entrypoint: str
    output_dir: Path
    low_level_q1timeline: bool
    artifacts_circuit_diagram: bool
    artifacts_analog_pulse_diagram: bool
    source_notebook: Path | None = None
    notebook_schedule: NotebookScheduleConfig | None = None


def load_project_config(path: str | Path) -> ProjectConfig:
    project_file = Path(path).resolve()
    root = project_file.parent
    try:
        raw = yaml.safe_load(project_file.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigLoadError(f"Could not read project file: {project_file}") from exc
    except yaml.YAMLError as exc:
        raise ConfigLoadError(f"Invalid YAML in project file: {project_file}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigLoadError("Project file must contain a YAML mapping")

    schedule = _mapping(raw.get("schedule"), "schedule")
    schedule_file_value = _optional_string(schedule, "schedule.file")
    schedule_notebook_value = _optional_string(schedule, "schedule.notebook")
    if (schedule_file_value is None) == (schedule_notebook_value is None):
        raise ConfigLoadError("Project config must define exactly one of schedule.file or schedule.notebook")

    outputs = _mapping(raw.get("outputs", {}), "outputs")
    low_level = _mapping(raw.get("low_level", {}), "low_level")
    artifacts = _mapping(raw.get("artifacts", {}), "artifacts")
    source = _mapping(raw.get("source", {}), "source")

    schedule_file = (root / schedule_file_value).resolve() if schedule_file_value is not None else None
    notebook_schedule = None
    if schedule_notebook_value is not None:
        notebook_path = (root / schedule_notebook_value).resolve()
        notebook_schedule = NotebookScheduleConfig(
            notebook=notebook_path,
            setup_tags=_string_tuple(schedule, "schedule.setup_tags"),
            schedule_tag=_string_value(schedule, "schedule.schedule_tag", "qbstimeline-schedule"),
            schedule_variable=_string_value(schedule, "schedule.schedule_variable", "schedule"),
            compiler_variable=_string_value(schedule, "schedule.compiler_variable", "compiler"),
        )

    source_notebook_value = _optional_string(source, "source.notebook")
    if source_notebook_value is not None:
        source_notebook = (root / source_notebook_value).resolve()
    elif notebook_schedule is not None:
        source_notebook = notebook_schedule.notebook
    else:
        source_notebook = None

    return ProjectConfig(
        root=root,
        project_file=project_file,
        schedule_file=schedule_file,
        schedule_entrypoint=_string_value(schedule, "entrypoint", "build_schedule"),
        compiler_entrypoint=_string_value(schedule, "compiler", "build_compiler"),
        output_dir=(root / _string_value(outputs, "dir", ".qbs_timeline")).resolve(),
        low_level_q1timeline=_bool_value(low_level, "q1timeline", True),
        artifacts_circuit_diagram=_bool_value(artifacts, "circuit_diagram", False),
        artifacts_analog_pulse_diagram=_bool_value(artifacts, "analog_pulse_diagram", False),
        source_notebook=source_notebook,
        notebook_schedule=notebook_schedule,
    )


def _mapping(value: Any, key: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigLoadError(f"Config value '{key}' must be a mapping")
    return value


def _string_value(mapping: dict[str, Any], key: str, default: str) -> str:
    value = mapping.get(key.rsplit(".", 1)[-1], default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigLoadError(f"Config value '{key}' must be a non-empty string")
    return value


def _optional_string(mapping: dict[str, Any], key: str) -> str | None:
    value = mapping.get(key.rsplit(".", 1)[-1])
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigLoadError(f"Config value '{key}' must be a non-empty string")
    return value


def _string_tuple(mapping: dict[str, Any], key: str) -> tuple[str, ...]:
    value = mapping.get(key.rsplit(".", 1)[-1], [])
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ConfigLoadError(f"Config value '{key}' must be a list of non-empty strings")
    return tuple(value)


def _bool_value(mapping: dict[str, Any], key: str, default: bool) -> bool:
    value = mapping.get(key, default)
    if not isinstance(value, bool):
        raise ConfigLoadError(f"Config value '{key}' must be true or false")
    return value
