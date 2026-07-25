from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SymbolicValue:
    id: str
    label: str
    value: int | float | str | bool | None
    unit: str | None
    kind: str

    def to_ir(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "value": self.value,
            "unit": self.unit,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class AnnotatedOperation:
    operation: Any
    annotations: dict[str, SymbolicValue]


class _SymbolFactory:
    def time(self, label: str, value: int | float) -> SymbolicValue:
        return SymbolicValue(
            id=_value_id(label),
            label=label,
            value=value,
            unit="s",
            kind="duration",
        )

    def amp(self, label: str, value: int | float) -> SymbolicValue:
        return SymbolicValue(
            id=_value_id(label),
            label=label,
            value=value,
            unit=None,
            kind="amplitude",
        )


sym = _SymbolFactory()


def annotate(operation: Any, **fields: SymbolicValue) -> Any:
    annotations = dict(fields)
    if isinstance(operation, dict):
        copied = dict(operation)
        copied["__qbstimeline_annotations__"] = annotations
        return copied

    try:
        setattr(operation, "__qbstimeline_annotations__", annotations)
    except Exception:
        return AnnotatedOperation(operation=operation, annotations=annotations)
    return operation


def symbolic_values_to_ir(values: list[SymbolicValue]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for value in values:
        if value.id in seen:
            continue
        seen.add(value.id)
        rows.append(value.to_ir())
    return rows


def _value_id(label: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").lower()
    return f"value:{normalized or 'value'}"
