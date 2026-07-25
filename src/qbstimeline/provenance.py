from __future__ import annotations

from typing import Any

from qbstimeline._access import get_value


class ProvenanceRecorder:
    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    def record_emission(
        self,
        *,
        source_id: str,
        source_kind: str,
        schedulable_id: str,
        sequencer_id: str,
        q1asm_line_start: int,
        q1asm_line_end: int,
        instruction_roles: list[str] | None = None,
        operand_mappings: list[dict[str, Any]] | None = None,
    ) -> None:
        self._records.append(
            _normalize_record(
                {
                    "source_id": source_id,
                    "source_kind": source_kind,
                    "schedulable_id": schedulable_id,
                    "sequencer_id": sequencer_id,
                    "q1asm_line_start": q1asm_line_start,
                    "q1asm_line_end": q1asm_line_end,
                    "instruction_roles": instruction_roles or [],
                    "operand_mappings": operand_mappings or [],
                }
            )
        )

    def to_ir(self) -> list[dict[str, Any]]:
        return list(self._records)


def normalize_q1asm_provenance(compiled_schedule: Any) -> list[dict[str, Any]]:
    sidecar = get_value(compiled_schedule, "qbstimeline_provenance", [])
    if isinstance(sidecar, ProvenanceRecorder):
        return sidecar.to_ir()
    if not isinstance(sidecar, list):
        return []
    rows: list[dict[str, Any]] = []
    for record in sidecar:
        if not isinstance(record, dict):
            continue
        try:
            rows.append(_normalize_record(record))
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    sequencer_id = record.get("sequencer_id", record.get("sequencer"))
    row = {
        "source_id": str(record["source_id"]),
        "source_kind": str(record.get("source_kind", "unknown")),
        "schedulable_id": str(record.get("schedulable_id", "")),
        "sequencer_id": str(sequencer_id),
        "q1asm_line_start": int(record["q1asm_line_start"]),
        "q1asm_line_end": int(record["q1asm_line_end"]),
        "instruction_roles": [
            str(item) for item in record.get("instruction_roles", []) if isinstance(item, str)
        ],
        "operand_mappings": [
            _normalize_operand_mapping(item)
            for item in record.get("operand_mappings", [])
            if isinstance(item, dict)
        ],
    }
    for key in ("operation_id", "confidence", "inference_reason"):
        if key in record:
            row[key] = str(record[key])
    return row


def _normalize_operand_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    row = {
        "line": int(mapping["line"]),
        "instruction": str(mapping["instruction"]),
        "operand_index": int(mapping["operand_index"]),
        "role": str(mapping["role"]),
        "numeric_value": mapping.get("numeric_value"),
        "unit": mapping.get("unit"),
    }
    if "source_value_id" in mapping:
        row["source_value_id"] = str(mapping["source_value_id"])
    if "source_expression" in mapping:
        row["source_expression"] = str(mapping["source_expression"])
    if "line_end" in mapping:
        row["line_end"] = int(mapping["line_end"])
    return row
