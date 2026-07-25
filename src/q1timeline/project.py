from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from q1timeline.diagnostics import Diagnostic
from q1timeline.q1asm.ast import SourceLocation


VALID_BRANCH_POLICIES = frozenset(
    {
        "collapse_unresolved",
        "assume_true",
        "assume_false",
        "assume_fallthrough",
        "explore_both_with_depth_limit",
    }
)


@dataclass(frozen=True)
class SequencerConfig:
    id: str
    name: str
    file: Path
    module: str | None = None
    sequence_json: Path | None = None


@dataclass(frozen=True)
class ViewConfig:
    default_mode: str = "normal"
    show_q1_issue: bool = False
    show_queue: bool = False
    show_slack: bool = False
    show_loop_preview: bool = True


@dataclass(frozen=True)
class AnalysisConfig:
    loop_policy: str = "compact_first_iteration"
    branch_policy: str = "collapse_unresolved"
    underflow_policy: str = "confidence_levels"


@dataclass(frozen=True)
class ProjectConfig:
    root: Path
    sequencers: list[SequencerConfig]
    params_file: Path | None = None
    display_file: Path | None = None
    alignment_mode: str = "first_wait_sync"
    alignment_anchor_kinds: tuple[str, ...] = ()
    view: ViewConfig = field(default_factory=ViewConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    params: dict[str, Any] = field(default_factory=dict)
    display: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)


class ConfigLoadError(Exception):
    def __init__(self, message: str, diagnostics: list[Diagnostic]) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_mapping_without_duplicate_keys(
    loader: yaml.SafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _value_node in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            continue
        key = loader.construct_object(key_node, deep=deep)
        try:
            hash(key)
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found unhashable key",
                key_node.start_mark,
            ) from exc
        if key in seen:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key: {key}",
                key_node.start_mark,
            )
        seen.add(key)
    loader.flatten_mapping(node)
    return yaml.constructor.SafeConstructor.construct_mapping(loader, node, deep=deep)


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_without_duplicate_keys,
)


def load_project_config(project_file: str | Path) -> ProjectConfig:
    project_path = Path(project_file).resolve()
    if not project_path.exists():
        diagnostic = Diagnostic(
            severity="error",
            category="missing_required_file",
            message=f"Project file does not exist: {project_path}",
        )
        raise ConfigLoadError(diagnostic.message, [diagnostic])
    if not project_path.is_file():
        diagnostic = _invalid_config_path_diagnostic("project", project_path)
        raise ConfigLoadError(diagnostic.message, [diagnostic])

    root = project_path.parent
    data = _load_yaml_mapping(project_path, mapping_label="project")
    diagnostics: list[Diagnostic] = []
    errors: list[Diagnostic] = []

    sequencers = _load_sequencers(data.get("sequencers"), root, project_path, errors)
    diagnostics.extend(errors)
    _validate_structured_project_sections(data, project_path, errors)

    params_file, params = _load_optional_json_mapping(
        data.get("params"),
        root,
        project_path,
        "params",
        diagnostics,
        section_present="params" in data,
    )
    display_file, display = _load_optional_yaml_mapping(
        data.get("display"),
        root,
        project_path,
        "display",
        diagnostics,
        section_present="display" in data,
    )
    alignment_mode = _nested_get(data, "alignment", "mode", default="first_wait_sync")
    alignment_anchor_kinds = _load_alignment_anchor_kinds(data.get("alignment"), project_path, errors)
    view_config = _load_view_config(data.get("view"), project_path, errors)
    analysis_config = _load_analysis_config(data.get("analysis"))
    if not _is_valid_alignment_mode(alignment_mode):
        errors.append(
            Diagnostic(
                severity="error",
                category="invalid_alignment_policy",
                message=f"Invalid alignment policy: {alignment_mode}",
                source=_source_for_yaml_key(project_path, "mode", alignment_mode),
                details={"alignment_mode": alignment_mode},
            )
        )
    if not _is_valid_branch_policy(analysis_config.branch_policy):
        errors.append(
            Diagnostic(
                severity="error",
                category="invalid_branch_policy",
                message=f"Invalid branch policy: {analysis_config.branch_policy}",
                source=_source_for_yaml_key(project_path, "branch_policy", analysis_config.branch_policy),
                details={"branch_policy": analysis_config.branch_policy},
            )
        )
    if not _is_valid_loop_policy(analysis_config.loop_policy):
        errors.append(
            Diagnostic(
                severity="error",
                category="invalid_loop_policy",
                message=f"Invalid loop policy: {analysis_config.loop_policy}",
                source=_source_for_yaml_key(project_path, "loop_policy", analysis_config.loop_policy),
                details={"loop_policy": analysis_config.loop_policy},
            )
        )
    if not _is_valid_underflow_policy(analysis_config.underflow_policy):
        errors.append(
            Diagnostic(
                severity="error",
                category="invalid_underflow_policy",
                message=f"Invalid underflow policy: {analysis_config.underflow_policy}",
                source=_source_for_yaml_key(project_path, "underflow_policy", analysis_config.underflow_policy),
                details={"underflow_policy": analysis_config.underflow_policy},
            )
        )
    if not _is_valid_view_mode(view_config.default_mode):
        errors.append(
            Diagnostic(
                severity="error",
                category="invalid_view_mode",
                message=f"Invalid view mode: {view_config.default_mode}. Valid values are normal and debug.",
                source=_source_for_yaml_key(project_path, "default_mode", view_config.default_mode),
                details={"default_mode": view_config.default_mode, "valid_modes": ["normal", "debug"]},
            )
        )
    if alignment_mode == "first_anchor" and not alignment_anchor_kinds:
        errors.append(
            Diagnostic(
                severity="error",
                category="invalid_alignment_anchor_kinds",
                message="alignment.anchor_kinds must list at least one event kind when alignment.mode is first_anchor.",
                source=_source_for_yaml_key(project_path, "anchor_kinds"),
                details={"alignment_mode": alignment_mode},
            )
        )

    if errors:
        raise ConfigLoadError("Project configuration contains errors", errors)

    return ProjectConfig(
        root=root,
        sequencers=sequencers,
        params_file=params_file,
        display_file=display_file,
        alignment_mode=alignment_mode,
        alignment_anchor_kinds=alignment_anchor_kinds,
        view=view_config,
        analysis=analysis_config,
        params=params,
        display=display,
        diagnostics=diagnostics,
    )


def load_single_file_config(q1asm_file: str | Path) -> ProjectConfig:
    file_path = Path(q1asm_file).resolve()
    if not file_path.exists():
        diagnostic = Diagnostic(
            severity="error",
            category="missing_required_file",
            message=f"Q1ASM file does not exist: {file_path}",
        )
        raise ConfigLoadError(diagnostic.message, [diagnostic])
    if not file_path.is_file():
        diagnostic = _invalid_config_path_diagnostic("q1asm", file_path)
        raise ConfigLoadError(diagnostic.message, [diagnostic])

    sequencer = SequencerConfig(
        id=_default_sequencer_id(file_path.stem),
        name=file_path.stem,
        file=file_path,
    )
    return ProjectConfig(root=file_path.parent, sequencers=[sequencer])


def _load_sequencers(
    sequencer_data: Any,
    root: Path,
    project_path: Path,
    errors: list[Diagnostic],
) -> list[SequencerConfig]:
    if not isinstance(sequencer_data, list) or not sequencer_data:
        errors.append(
            Diagnostic(
                severity="error",
                category="missing_required_field",
                message="Project must define at least one sequencer.",
                source=_source_for_yaml_key(project_path, "sequencers"),
            )
        )
        return []

    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    sequencers: list[SequencerConfig] = []
    for index, item in enumerate(sequencer_data):
        if not isinstance(item, dict):
            errors.append(
                Diagnostic(
                    severity="error",
                    category="invalid_sequencer",
                    message=f"Sequencer entry {index} must be a mapping.",
                    source=_source_for_yaml_key(project_path, "sequencers"),
                )
            )
            continue

        sequencer_id = str(item.get("id", "")).strip()
        if not sequencer_id:
            sequencer_id = f"seq{index}"
        if sequencer_id in seen_ids:
            errors.append(
                Diagnostic(
                    severity="error",
                    category="duplicate_sequencer_id",
                    message=f"Duplicate sequencer id: {sequencer_id}",
                    source=_source_for_yaml_key(project_path, "id", sequencer_id),
                    details={"sequencer_id": sequencer_id},
                )
            )
        seen_ids.add(sequencer_id)

        sequencer_name = str(item.get("name") or sequencer_id)
        if sequencer_name in seen_names:
            errors.append(
                Diagnostic(
                    severity="error",
                    category="duplicate_sequencer_name",
                    message=f"Duplicate sequencer name: {sequencer_name}",
                    source=_source_for_yaml_key(project_path, "name", sequencer_name),
                    details={"sequencer_name": sequencer_name},
                )
            )
        seen_names.add(sequencer_name)

        raw_file = item.get("file")
        if not raw_file:
            errors.append(
                Diagnostic(
                    severity="error",
                    category="missing_required_field",
                    message=f"Sequencer {sequencer_id} is missing required file.",
                    source=_source_for_yaml_key(project_path, "id", sequencer_id),
                    details={"sequencer_id": sequencer_id},
                )
            )
            continue

        q1asm_file = _resolve_path(root, raw_file)
        if not q1asm_file.exists():
            errors.append(
                Diagnostic(
                    severity="error",
                    category="missing_required_file",
                    message=f"Q1ASM file does not exist: {q1asm_file}",
                    source=_source_for_yaml_key(project_path, "file", raw_file),
                    details={"sequencer_id": sequencer_id, "file": str(q1asm_file)},
                )
            )
        elif not q1asm_file.is_file():
            errors.append(
                Diagnostic(
                    severity="error",
                    category="invalid_config_path",
                    message=f"Configured q1asm path is not a readable file: {q1asm_file}",
                    source=_source_for_yaml_key(project_path, "file", raw_file),
                    details={"sequencer_id": sequencer_id, "file": str(q1asm_file), "kind": "q1asm"},
                )
            )

        sequence_json = None
        if item.get("sequence_json"):
            sequence_json = _resolve_path(root, item["sequence_json"])

        sequencers.append(
            SequencerConfig(
                id=sequencer_id,
                name=sequencer_name,
                file=q1asm_file,
                module=item.get("module"),
                sequence_json=sequence_json,
            )
        )

    return sequencers


def _load_view_config(data: Any, project_path: Path, errors: list[Diagnostic]) -> ViewConfig:
    if not isinstance(data, dict):
        data = {}
    view_defaults = ViewConfig()
    return ViewConfig(
        default_mode=str(data.get("default_mode", "normal")),
        show_q1_issue=_load_view_bool(data, "show_q1_issue", view_defaults.show_q1_issue, project_path, errors),
        show_queue=_load_view_bool(data, "show_queue", view_defaults.show_queue, project_path, errors),
        show_slack=_load_view_bool(data, "show_slack", view_defaults.show_slack, project_path, errors),
        show_loop_preview=_load_view_bool(data, "show_loop_preview", view_defaults.show_loop_preview, project_path, errors),
    )


def _load_view_bool(
    data: dict[str, Any],
    key: str,
    default: bool,
    project_path: Path,
    errors: list[Diagnostic],
) -> bool:
    if key not in data:
        return default
    value = data[key]
    if type(value) is bool:
        return value
    errors.append(
        Diagnostic(
            severity="error",
            category="invalid_yaml",
            message=f"view.{key} must be a boolean, got {type(value).__name__}.",
            source=_source_for_yaml_key(project_path, key, value),
            details={
                "field": f"view.{key}",
                "expected": "boolean",
                "actual": type(value).__name__,
            },
        )
    )
    return default


def _load_analysis_config(data: Any) -> AnalysisConfig:
    if not isinstance(data, dict):
        data = {}
    return AnalysisConfig(
        loop_policy=str(data.get("loop_policy", "compact_first_iteration")),
        branch_policy=str(data.get("branch_policy", "collapse_unresolved")),
        underflow_policy=str(data.get("underflow_policy", "confidence_levels")),
    )


def _load_optional_json_mapping(
    data: Any,
    root: Path,
    project_path: Path,
    label: str,
    diagnostics: list[Diagnostic],
    *,
    section_present: bool = False,
) -> tuple[Path | None, dict[str, Any]]:
    _validate_optional_section_mapping(data, project_path, label, section_present=section_present)
    optional_file = _optional_file_from_section(data, root)
    if optional_file is None:
        return None, {}
    if not optional_file.exists():
        diagnostics.append(_missing_optional_file(label, optional_file, _source_for_yaml_key(project_path, "file", optional_file.name)))
        return optional_file, {}
    if not optional_file.is_file():
        diagnostic = _invalid_config_path_diagnostic(
            label,
            optional_file,
            _source_for_yaml_key(project_path, "file", optional_file.name),
        )
        raise ConfigLoadError(diagnostic.message, [diagnostic])
    try:
        with optional_file.open("r", encoding="utf-8-sig") as handle:
            loaded = json.load(
                handle,
                parse_constant=_reject_non_finite_json_constant,
                parse_float=_parse_finite_json_float,
            )
    except json.JSONDecodeError as exc:
        diagnostic = _invalid_json_diagnostic(optional_file, exc)
        raise ConfigLoadError(diagnostic.message, [diagnostic]) from exc
    except ValueError as exc:
        diagnostic = _invalid_json_value_diagnostic(optional_file, exc)
        raise ConfigLoadError(diagnostic.message, [diagnostic]) from exc
    except UnicodeDecodeError as exc:
        diagnostic = _invalid_json_decode_diagnostic(optional_file, exc)
        raise ConfigLoadError(diagnostic.message, [diagnostic]) from exc
    except OSError as exc:
        diagnostic = _invalid_config_path_diagnostic(label, optional_file)
        raise ConfigLoadError(diagnostic.message, [diagnostic]) from exc
    if not isinstance(loaded, dict):
        diagnostic = _invalid_top_level_mapping_diagnostic(optional_file, "invalid_json", label, loaded)
        raise ConfigLoadError(diagnostic.message, [diagnostic])
    if label == "params":
        try:
            validate_params_json_mapping(loaded)
        except ValueError as exc:
            diagnostic = _invalid_json_value_diagnostic(optional_file, exc)
            raise ConfigLoadError(diagnostic.message, [diagnostic]) from exc
    return optional_file, loaded


def validate_params_json_mapping(params: dict[str, Any]) -> None:
    boolean_path = _first_boolean_json_path(params)
    if boolean_path is not None:
        raise ValueError(f"boolean JSON value is not valid for params at {boolean_path}")
    fractional_path = _first_fractional_json_number_path(params)
    if fractional_path is not None:
        raise ValueError(f"non-integer JSON number is not valid for params at {fractional_path}")


def validate_yaml_mapping_values(mapping: dict[str, Any], path: Path, label: str) -> None:
    non_finite = _first_non_finite_yaml_number_path(mapping)
    if non_finite is None:
        return
    value_path, key = non_finite
    diagnostic = _invalid_yaml_value_diagnostic(
        path,
        f"non-finite YAML number is not valid for {label} at {value_path}",
        key,
    )
    raise ConfigLoadError(diagnostic.message, [diagnostic])


def _first_non_finite_yaml_number_path(value: Any, path: str = "$") -> tuple[str, str | None] | None:
    if isinstance(value, float) and not math.isfinite(value):
        return path, _last_mapping_key(path)
    if isinstance(value, dict):
        for key, child in value.items():
            non_finite = _first_non_finite_yaml_number_path(child, f"{path}.{key}")
            if non_finite is not None:
                return non_finite
    if isinstance(value, list):
        for index, child in enumerate(value):
            non_finite = _first_non_finite_yaml_number_path(child, f"{path}[{index}]")
            if non_finite is not None:
                return non_finite
    return None


def _last_mapping_key(path: str) -> str | None:
    if "." not in path:
        return None
    tail = path.rsplit(".", 1)[1]
    return tail.split("[", 1)[0] or None


def _first_boolean_json_path(value: Any, path: str = "$") -> str | None:
    if isinstance(value, bool):
        return path
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = _first_boolean_json_path(child, f"{path}.{key}")
            if child_path is not None:
                return child_path
    if isinstance(value, list):
        for index, child in enumerate(value):
            child_path = _first_boolean_json_path(child, f"{path}[{index}]")
            if child_path is not None:
                return child_path
    return None


def _first_fractional_json_number_path(value: Any, path: str = "$") -> str | None:
    if isinstance(value, float) and not value.is_integer():
        return path
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = _first_fractional_json_number_path(child, f"{path}.{key}")
            if child_path is not None:
                return child_path
    if isinstance(value, list):
        for index, child in enumerate(value):
            child_path = _first_fractional_json_number_path(child, f"{path}[{index}]")
            if child_path is not None:
                return child_path
    return None


def _load_optional_yaml_mapping(
    data: Any,
    root: Path,
    project_path: Path,
    label: str,
    diagnostics: list[Diagnostic],
    *,
    section_present: bool = False,
) -> tuple[Path | None, dict[str, Any]]:
    _validate_optional_section_mapping(data, project_path, label, section_present=section_present)
    optional_file = _optional_file_from_section(data, root)
    if optional_file is None:
        return None, {}
    if not optional_file.exists():
        diagnostics.append(_missing_optional_file(label, optional_file, _source_for_yaml_key(project_path, "file", optional_file.name)))
        return optional_file, {}
    if not optional_file.is_file():
        diagnostic = _invalid_config_path_diagnostic(
            label,
            optional_file,
            _source_for_yaml_key(project_path, "file", optional_file.name),
        )
        raise ConfigLoadError(diagnostic.message, [diagnostic])
    loaded = _load_yaml_mapping(optional_file, mapping_label=label)
    validate_yaml_mapping_values(loaded, optional_file, label)
    return optional_file, loaded


def _optional_file_from_section(data: Any, root: Path) -> Path | None:
    if not isinstance(data, dict) or not data.get("file"):
        return None
    return _resolve_path(root, data["file"])


def _validate_optional_section_mapping(
    data: Any,
    project_path: Path,
    label: str,
    *,
    section_present: bool,
) -> None:
    if not section_present or isinstance(data, dict):
        return
    diagnostic = _invalid_project_section_mapping_diagnostic(project_path, label, data)
    raise ConfigLoadError(diagnostic.message, [diagnostic])


def _validate_structured_project_sections(
    data: dict[str, Any],
    project_path: Path,
    errors: list[Diagnostic],
) -> None:
    for label in ("view", "analysis", "alignment"):
        if label in data and not isinstance(data[label], dict):
            errors.append(_invalid_project_section_mapping_diagnostic(project_path, label, data[label]))


def _missing_optional_file(label: str, path: Path, source: SourceLocation | None = None) -> Diagnostic:
    return Diagnostic(
        severity="warning",
        category="missing_optional_file",
        message=f"Optional {label} file does not exist: {path}",
        source=source,
        details={"file": str(path), "kind": label},
    )


def _invalid_config_path_diagnostic(
    label: str,
    path: Path,
    source: SourceLocation | None = None,
) -> Diagnostic:
    return Diagnostic(
        severity="error",
        category="invalid_config_path",
        message=f"Configured {label} path is not a readable file: {path}",
        source=source,
        details={"file": str(path), "kind": label},
    )


def _source_for_yaml_key(path: Path, key: str, value: Any | None = None) -> SourceLocation | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    key_prefix = f"{key}:"
    value_text = None if value is None else str(value)
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped.startswith(key_prefix):
            continue
        if value_text is not None and value_text not in line:
            continue
        column = line.find(key)
        return SourceLocation(
            file=str(path),
            line=line_number,
            column=column + 1 if column >= 0 else 1,
            raw=line,
        )
    return SourceLocation(file=str(path), line=1, column=1, raw=lines[0] if lines else "")


def _load_yaml_mapping(path: Path, *, mapping_label: str | None = None) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = yaml.load(handle, Loader=_UniqueKeySafeLoader)
        if loaded is None:
            loaded = {}
    except yaml.YAMLError as exc:
        diagnostic = _invalid_yaml_diagnostic(path, exc)
        raise ConfigLoadError(diagnostic.message, [diagnostic]) from exc
    except UnicodeDecodeError as exc:
        diagnostic = _invalid_yaml_diagnostic(path, exc)
        raise ConfigLoadError(diagnostic.message, [diagnostic]) from exc
    except OSError as exc:
        diagnostic = _invalid_config_path_diagnostic("yaml", path)
        raise ConfigLoadError(diagnostic.message, [diagnostic]) from exc
    if not isinstance(loaded, dict) and mapping_label is not None:
        diagnostic = _invalid_top_level_mapping_diagnostic(path, "invalid_yaml", mapping_label, loaded)
        raise ConfigLoadError(diagnostic.message, [diagnostic])
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _invalid_top_level_mapping_diagnostic(
    path: Path,
    category: str,
    label: str,
    loaded: Any,
) -> Diagnostic:
    raw = ""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        raw = lines[0] if lines else ""
    except (OSError, UnicodeDecodeError):
        pass
    syntax_name = "JSON" if category == "invalid_json" else "YAML"
    diagnostic = Diagnostic(
        severity="error",
        category=category,
        message=(
            f"Invalid {syntax_name} in {path}: top-level {label} config must be a mapping/object, "
            f"got {type(loaded).__name__}."
        ),
        source=SourceLocation(file=str(path.resolve()), line=1, column=1, raw=raw),
        details={"file": str(path.resolve()), "kind": label},
    )
    return diagnostic


def _invalid_project_section_mapping_diagnostic(
    path: Path,
    label: str,
    loaded: Any,
) -> Diagnostic:
    actual_type = type(loaded).__name__
    return Diagnostic(
        severity="error",
        category="invalid_yaml",
        message=f"Invalid YAML in {path}: {label} section must be a mapping/object, got {actual_type}.",
        source=_source_for_yaml_key(path, label),
        details={
            "file": str(path.resolve()),
            "kind": label,
            "expected": "mapping",
            "actual": actual_type,
        },
    )


def _invalid_yaml_diagnostic(path: Path, exc: Exception) -> Diagnostic:
    mark = getattr(exc, "problem_mark", None)
    line = int(getattr(mark, "line", 0)) + 1 if mark is not None else 1
    column = int(getattr(mark, "column", 0)) + 1 if mark is not None else 1
    raw = ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if 1 <= line <= len(lines):
            raw = lines[line - 1]
    except (OSError, UnicodeDecodeError):
        pass
    problem = getattr(exc, "problem", None) or str(exc)
    diagnostic = Diagnostic(
        severity="error",
        category="invalid_yaml",
        message=f"Invalid YAML in {path}: {problem}",
        source=SourceLocation(file=str(path), line=line, column=column, raw=raw),
        details={"file": str(path)},
    )
    return diagnostic


def _invalid_yaml_value_diagnostic(path: Path, problem: str, key: str | None = None) -> Diagnostic:
    return Diagnostic(
        severity="error",
        category="invalid_yaml",
        message=f"Invalid YAML in {path}: {problem}",
        source=_source_for_yaml_key(path, key) if key else SourceLocation(file=str(path), line=1, column=1, raw=""),
        details={"file": str(path.resolve())},
    )


def _invalid_json_decode_diagnostic(path: Path, exc: UnicodeDecodeError) -> Diagnostic:
    diagnostic = Diagnostic(
        severity="error",
        category="invalid_json",
        message=f"Invalid JSON in {path}: {exc}",
        source=SourceLocation(file=str(path), line=1, column=1, raw=""),
        details={"file": str(path)},
    )
    return diagnostic


def _invalid_json_diagnostic(path: Path, exc: json.JSONDecodeError) -> Diagnostic:
    line = int(getattr(exc, "lineno", 1) or 1)
    column = int(getattr(exc, "colno", 1) or 1)
    raw = ""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        if 1 <= line <= len(lines):
            raw = lines[line - 1]
    except (OSError, UnicodeDecodeError):
        pass
    diagnostic = Diagnostic(
        severity="error",
        category="invalid_json",
        message=f"Invalid JSON in {path}: {exc.msg}",
        source=SourceLocation(file=str(path), line=line, column=column, raw=raw),
        details={"file": str(path)},
    )
    return diagnostic


def _invalid_json_value_diagnostic(path: Path, exc: ValueError) -> Diagnostic:
    raw = ""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        raw = lines[0] if lines else ""
    except (OSError, UnicodeDecodeError):
        pass
    diagnostic = Diagnostic(
        severity="error",
        category="invalid_json",
        message=f"Invalid JSON in {path}: {exc}",
        source=SourceLocation(file=str(path.resolve()), line=1, column=1, raw=raw),
        details={"file": str(path.resolve())},
    )
    return diagnostic


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _parse_finite_json_float(value: str) -> int:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    if not parsed.is_integer():
        raise ValueError(f"non-integer JSON number: {value}")
    return int(parsed)


def _nested_get(data: dict[str, Any], section: str, key: str, *, default: str) -> str:
    section_data = data.get(section)
    if not isinstance(section_data, dict):
        return default
    return str(section_data.get(key, default))


def is_valid_alignment_mode(mode: str) -> bool:
    if mode in {
        "first_wait_sync",
        "after_first_wait_sync",
        "first_wait_trigger",
        "first_anchor",
        "first_marker_rise",
        "first_play",
        "first_acquire",
        "none",
    }:
        return True
    if mode.startswith("label:"):
        return bool(mode.split(":", 1)[1].strip())
    if mode.startswith("manual:"):
        try:
            int(mode.split(":", 1)[1])
        except ValueError:
            return False
        return True
    return False


def _is_valid_alignment_mode(mode: str) -> bool:
    return is_valid_alignment_mode(mode)


def is_valid_branch_policy(branch_policy: str) -> bool:
    return branch_policy in VALID_BRANCH_POLICIES


def _is_valid_branch_policy(branch_policy: str) -> bool:
    return is_valid_branch_policy(branch_policy)


def _is_valid_loop_policy(loop_policy: str) -> bool:
    return loop_policy == "compact_first_iteration"


def _is_valid_underflow_policy(underflow_policy: str) -> bool:
    return underflow_policy == "confidence_levels"


def _is_valid_view_mode(mode: str) -> bool:
    return mode in {"normal", "debug"}


def _load_alignment_anchor_kinds(
    alignment_data: Any,
    project_path: Path,
    errors: list[Diagnostic],
) -> tuple[str, ...]:
    if not isinstance(alignment_data, dict):
        return ()
    if "anchor_kinds" in alignment_data:
        raw_anchor_kinds = alignment_data.get("anchor_kinds")
    elif "anchors" in alignment_data:
        raw_anchor_kinds = alignment_data.get("anchors")
    else:
        return ()
    if raw_anchor_kinds in (None, ""):
        return ()
    if isinstance(raw_anchor_kinds, str):
        raw_items = [raw_anchor_kinds]
    elif isinstance(raw_anchor_kinds, list):
        raw_items = raw_anchor_kinds
    else:
        errors.append(
            Diagnostic(
                severity="error",
                category="invalid_alignment_anchor_kinds",
                message="alignment.anchor_kinds must be a string or list of strings.",
                source=_source_for_yaml_key(project_path, "anchor_kinds"),
                details={"raw": raw_anchor_kinds},
            )
        )
        return ()

    anchor_kinds: list[str] = []
    invalid_items: list[Any] = []
    for item in raw_items:
        if not isinstance(item, str) or not item.strip():
            invalid_items.append(item)
            continue
        anchor_kinds.append(item.strip())

    if invalid_items:
        errors.append(
            Diagnostic(
                severity="error",
                category="invalid_alignment_anchor_kinds",
                message="alignment.anchor_kinds entries must be non-empty strings.",
                source=_source_for_yaml_key(project_path, "anchor_kinds"),
                details={"invalid_items": invalid_items},
            )
        )
    return tuple(dict.fromkeys(anchor_kinds))


def _resolve_path(root: Path, path_value: Any) -> Path:
    path = Path(str(path_value))
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _default_sequencer_id(stem: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").lower()
    if not normalized:
        return "seq"
    if normalized[0].isdigit():
        return f"seq_{normalized}"
    return normalized
