from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from qbstimeline.symbols import AnnotatedOperation, SymbolicValue


def unwrap(value: Any) -> Any:
    if isinstance(value, AnnotatedOperation):
        return value.operation
    try:
        return getattr(value, "data", value)
    except RuntimeError:
        return value


def get_value(obj: Any, key: str, default: Any = None) -> Any:
    obj = unwrap(obj)
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    try:
        return getattr(obj, key, default)
    except RuntimeError:
        return default


def annotations_for(obj: Any) -> dict[str, SymbolicValue]:
    if isinstance(obj, AnnotatedOperation):
        return obj.annotations
    if isinstance(obj, Mapping):
        annotations = obj.get("__qbstimeline_annotations__", {})
    else:
        annotations = getattr(obj, "__qbstimeline_annotations__", {})
    return annotations if isinstance(annotations, dict) else {}
