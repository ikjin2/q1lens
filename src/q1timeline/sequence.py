from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from q1timeline.diagnostics import Diagnostic


@dataclass(frozen=True)
class SequenceNames:
    waveforms: dict[int, str] = field(default_factory=dict)
    acquisitions: dict[int, str] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)


def load_sequence_names(path: str | Path) -> SequenceNames:
    sequence_path = Path(path)
    if not sequence_path.exists():
        return SequenceNames(
            diagnostics=[
                Diagnostic(
                    severity="warning",
                    category="missing_optional_file",
                    message=f"Optional sequence file does not exist: {sequence_path}",
                    details={"file": str(sequence_path), "kind": "sequence"},
                )
            ]
        )
    if not sequence_path.is_file():
        return SequenceNames(
            diagnostics=[
                Diagnostic(
                    severity="warning",
                    category="analysis_incomplete",
                    message=f"Could not parse sequence JSON: {sequence_path} is not a readable file",
                    details={"file": str(sequence_path)},
                )
            ]
        )

    try:
        loaded = json.loads(
            sequence_path.read_text(encoding="utf-8-sig"),
            parse_constant=_reject_non_finite_json_constant,
            parse_float=_parse_finite_json_float,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        return SequenceNames(
            diagnostics=[
                Diagnostic(
                    severity="warning",
                    category="analysis_incomplete",
                    message=f"Could not parse sequence JSON: {exc}",
                    details={"file": str(sequence_path)},
                )
            ]
        )
    except UnicodeDecodeError as exc:
        return SequenceNames(
            diagnostics=[
                Diagnostic(
                    severity="warning",
                    category="analysis_incomplete",
                    message=f"Could not parse sequence JSON: {exc}",
                    details={"file": str(sequence_path)},
                )
            ]
        )
    except OSError as exc:
        return SequenceNames(
            diagnostics=[
                Diagnostic(
                    severity="warning",
                    category="analysis_incomplete",
                    message=f"Could not parse sequence JSON: {exc}",
                    details={"file": str(sequence_path)},
                )
            ]
        )
    if not isinstance(loaded, dict):
        return SequenceNames(
            diagnostics=[
                Diagnostic(
                    severity="warning",
                    category="analysis_incomplete",
                    message=f"Could not parse sequence JSON: top-level sequence JSON value must be an object in {sequence_path}",
                    details={"file": str(sequence_path)},
                )
            ]
        )

    invalid_section_diagnostic = _invalid_section_type_diagnostic(loaded, sequence_path)
    invalid_boolean_indices: list[bool] = []
    negative_waveform_indices: set[int] = set()
    negative_acquisition_indices: set[int] = set()
    fractional_waveform_indices: set[float] = set()
    fractional_acquisition_indices: set[float] = set()
    duplicate_waveform_indices: set[int] = set()
    duplicate_acquisition_indices: set[int] = set()
    waveforms = _index_name_map(
        loaded.get("waveforms"),
        invalid_boolean_indices,
        negative_waveform_indices,
        fractional_waveform_indices,
        duplicate_waveform_indices,
    )
    acquisitions = _index_name_map(
        loaded.get("acquisitions"),
        invalid_boolean_indices,
        negative_acquisition_indices,
        fractional_acquisition_indices,
        duplicate_acquisition_indices,
    )
    diagnostics = [invalid_section_diagnostic] if invalid_section_diagnostic is not None else []
    if invalid_boolean_indices:
        diagnostics.append(_invalid_boolean_index_diagnostic(sequence_path))
    if negative_waveform_indices or negative_acquisition_indices:
        diagnostics.append(
            _negative_index_diagnostic(
                sequence_path,
                negative_waveform_indices,
                negative_acquisition_indices,
            )
        )
    if fractional_waveform_indices or fractional_acquisition_indices:
        diagnostics.append(
            _fractional_index_diagnostic(
                sequence_path,
                fractional_waveform_indices,
                fractional_acquisition_indices,
            )
        )
    if duplicate_waveform_indices or duplicate_acquisition_indices:
        diagnostics.append(
            _duplicate_index_diagnostic(
                sequence_path,
                duplicate_waveform_indices,
                duplicate_acquisition_indices,
            )
        )
    return SequenceNames(
        waveforms=waveforms,
        acquisitions=acquisitions,
        diagnostics=diagnostics,
    )


def _index_name_map(
    section: Any,
    invalid_boolean_indices: list[bool],
    negative_indices: set[int],
    fractional_indices: set[float],
    duplicate_indices: set[int],
) -> dict[int, str]:
    if isinstance(section, dict):
        return _map_from_dict(section, invalid_boolean_indices, negative_indices, fractional_indices, duplicate_indices)
    if isinstance(section, list):
        return _map_from_list(section, invalid_boolean_indices, negative_indices, fractional_indices, duplicate_indices)
    return {}


def _map_from_dict(
    section: dict[Any, Any],
    invalid_boolean_indices: list[bool],
    negative_indices: set[int],
    fractional_indices: set[float],
    duplicate_indices: set[int],
) -> dict[int, str]:
    names: dict[int, str] = {}
    for key, value in section.items():
        index, name = _entry_index_name(key, value, invalid_boolean_indices, negative_indices, fractional_indices)
        _add_sequence_name(names, duplicate_indices, index, name)
    return names


def _map_from_list(
    section: list[Any],
    invalid_boolean_indices: list[bool],
    negative_indices: set[int],
    fractional_indices: set[float],
    duplicate_indices: set[int],
) -> dict[int, str]:
    names: dict[int, str] = {}
    for value in section:
        index, name = _entry_index_name(None, value, invalid_boolean_indices, negative_indices, fractional_indices)
        _add_sequence_name(names, duplicate_indices, index, name)
    return names


def _add_sequence_name(
    names: dict[int, str],
    duplicate_indices: set[int],
    index: int | None,
    name: str | None,
) -> None:
    if index is None or not name:
        return
    if index in duplicate_indices:
        return
    if index in names:
        duplicate_indices.add(index)
        del names[index]
        return
    names[index] = name


def _entry_index_name(
    key: Any,
    value: Any,
    invalid_boolean_indices: list[bool],
    negative_indices: set[int],
    fractional_indices: set[float],
) -> tuple[int | None, str | None]:
    if isinstance(value, dict):
        index = _int_or_none(value.get("index", key), invalid_boolean_indices, negative_indices, fractional_indices)
        name = value.get("name")
        if not name and key is not None and not _looks_like_index(key):
            name = str(key)
        return index, str(name) if name else None
    if isinstance(value, str):
        return _int_or_none(key, invalid_boolean_indices, negative_indices, fractional_indices), value
    return (
        _int_or_none(key, invalid_boolean_indices, negative_indices, fractional_indices),
        str(key) if key is not None and not _looks_like_index(key) else None,
    )


def _int_or_none(
    value: Any,
    invalid_boolean_indices: list[bool] | None = None,
    negative_indices: set[int] | None = None,
    fractional_indices: set[float] | None = None,
) -> int | None:
    if isinstance(value, bool):
        if invalid_boolean_indices is not None:
            invalid_boolean_indices.append(value)
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        if not value.is_integer():
            if fractional_indices is not None:
                fractional_indices.add(value)
            return None
    try:
        index = int(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if index < 0:
        if negative_indices is not None:
            negative_indices.add(index)
        return None
    return index


def _looks_like_index(value: Any) -> bool:
    return _int_or_none(value) is not None


def _invalid_section_type_diagnostic(loaded: dict[Any, Any], path: Path) -> Diagnostic | None:
    invalid_sections = [
        section_name
        for section_name in ("waveforms", "acquisitions")
        if section_name in loaded and not isinstance(loaded[section_name], (dict, list))
    ]
    if not invalid_sections:
        return None
    return Diagnostic(
        severity="warning",
        category="analysis_incomplete",
        message=(
            f"Could not parse sequence JSON: sequence JSON sections must be objects or arrays "
            f"in {path}: {', '.join(invalid_sections)}"
        ),
        details={"file": str(path), "sections": invalid_sections},
    )


def _invalid_boolean_index_diagnostic(path: Path) -> Diagnostic:
    return Diagnostic(
        severity="warning",
        category="analysis_incomplete",
        message=f"Boolean sequence indices are not valid numeric indices in {path}; affected entries were ignored.",
        details={"file": str(path)},
    )


def _negative_index_diagnostic(
    path: Path,
    negative_waveform_indices: set[int],
    negative_acquisition_indices: set[int],
) -> Diagnostic:
    details: dict[str, object] = {"file": str(path)}
    if negative_waveform_indices:
        details["negative_waveform_indices"] = sorted(negative_waveform_indices)
    if negative_acquisition_indices:
        details["negative_acquisition_indices"] = sorted(negative_acquisition_indices)
    return Diagnostic(
        severity="warning",
        category="analysis_incomplete",
        message=f"Negative sequence indices are not valid numeric indices in {path}; affected entries were ignored.",
        details=details,
    )


def _fractional_index_diagnostic(
    path: Path,
    fractional_waveform_indices: set[float],
    fractional_acquisition_indices: set[float],
) -> Diagnostic:
    details: dict[str, object] = {"file": str(path)}
    if fractional_waveform_indices:
        details["fractional_waveform_indices"] = sorted(fractional_waveform_indices)
    if fractional_acquisition_indices:
        details["fractional_acquisition_indices"] = sorted(fractional_acquisition_indices)
    return Diagnostic(
        severity="warning",
        category="analysis_incomplete",
        message=f"Fractional sequence indices are not valid numeric indices in {path}; affected entries were ignored.",
        details=details,
    )


def _duplicate_index_diagnostic(
    path: Path,
    duplicate_waveform_indices: set[int],
    duplicate_acquisition_indices: set[int],
) -> Diagnostic:
    details: dict[str, object] = {"file": str(path)}
    if duplicate_waveform_indices:
        details["duplicate_waveform_indices"] = sorted(duplicate_waveform_indices)
    if duplicate_acquisition_indices:
        details["duplicate_acquisition_indices"] = sorted(duplicate_acquisition_indices)
    return Diagnostic(
        severity="warning",
        category="analysis_incomplete",
        message=f"Duplicate sequence indices in {path}; ambiguous labels were ignored.",
        details=details,
    )


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    return parsed
