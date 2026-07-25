from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


_ARRAY_FIELDS = (
    "operations",
    "control_flow_blocks",
    "timing_table",
    "symbolic_values",
    "symbolic_pulses",
    "q1asm_provenance",
    "q1asm_programs",
    "ir_diagnostics",
)
_TOLERANCE_SECONDS = 1e-15


@dataclass(frozen=True)
class IrDiagnostic:
    code: str
    path: str
    message: str
    severity: str = "warning"

    def to_warning(self) -> str:
        return f"{self.code} at {self.path}: {self.message}"

    def to_ir(self) -> dict[str, str]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "severity": self.severity,
        }


def validate_qbs_ir(ir: Mapping[str, Any]) -> list[IrDiagnostic]:
    diagnostics: list[IrDiagnostic] = []
    diagnostics.extend(_validate_top_level_containers(ir))

    operations = _list(ir.get("operations"))
    control_flow_blocks = _list(ir.get("control_flow_blocks"))
    symbolic_values = _list(ir.get("symbolic_values"))
    symbolic_pulses = _list(ir.get("symbolic_pulses"))
    q1asm_programs = _list(ir.get("q1asm_programs"))
    q1asm_provenance = _list(ir.get("q1asm_provenance"))
    q1asm_by_sequencer = ir.get("q1asm_by_sequencer")
    if not isinstance(q1asm_by_sequencer, Mapping):
        q1asm_by_sequencer = {}

    diagnostics.extend(_validate_unique_ids("operations", operations, "id"))
    diagnostics.extend(_validate_unique_ids("control_flow_blocks", control_flow_blocks, "id"))
    diagnostics.extend(_validate_unique_ids("symbolic_pulses", symbolic_pulses, "id"))
    diagnostics.extend(_validate_unique_ids("symbolic_values", symbolic_values, "id"))
    diagnostics.extend(_validate_unique_ids("q1asm_programs", q1asm_programs, "sequencer_id"))
    diagnostics.extend(
        _validate_references(
            operations=operations,
            control_flow_blocks=control_flow_blocks,
            symbolic_values=symbolic_values,
            symbolic_pulses=symbolic_pulses,
            q1asm_programs=q1asm_programs,
            q1asm_provenance=q1asm_provenance,
            q1asm_by_sequencer=q1asm_by_sequencer,
        )
    )
    diagnostics.extend(
        _validate_timing(
            operations=operations,
            control_flow_blocks=control_flow_blocks,
            symbolic_pulses=symbolic_pulses,
        )
    )
    diagnostics.extend(
        _validate_q1asm_ranges(
            q1asm_programs=q1asm_programs,
            q1asm_by_sequencer=q1asm_by_sequencer,
            q1asm_provenance=q1asm_provenance,
        )
    )
    diagnostics.extend(_validate_control_flow(operations, control_flow_blocks))
    return diagnostics


def _validate_top_level_containers(ir: Mapping[str, Any]) -> list[IrDiagnostic]:
    diagnostics: list[IrDiagnostic] = []
    for field in _ARRAY_FIELDS:
        if not isinstance(ir.get(field), list):
            diagnostics.append(
                IrDiagnostic(
                    code="invalid_container",
                    path=field,
                    message=f"{field} must be an array",
                )
            )
    if not isinstance(ir.get("q1asm_by_sequencer"), Mapping):
        diagnostics.append(
            IrDiagnostic(
                code="invalid_container",
                path="q1asm_by_sequencer",
                message="q1asm_by_sequencer must be an object",
            )
        )
    return diagnostics


def _validate_unique_ids(
    collection_name: str,
    rows: list[Any],
    key: str,
) -> list[IrDiagnostic]:
    diagnostics: list[IrDiagnostic] = []
    values: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            continue
        value = row.get(key)
        if not _has_text(value):
            diagnostics.append(
                IrDiagnostic(
                    code="missing_id",
                    path=f"{collection_name}[{index}].{key}",
                    message=f"{collection_name} row must define {key}",
                )
            )
            continue
        values.append(str(value))
    counts = Counter(values)
    for value, count in sorted(counts.items()):
        if count > 1:
            diagnostics.append(
                IrDiagnostic(
                    code="duplicate_id",
                    path=collection_name,
                    message=f"{key} {value!r} appears {count} times",
                )
            )
    return diagnostics


def _validate_references(
    *,
    operations: list[Any],
    control_flow_blocks: list[Any],
    symbolic_values: list[Any],
    symbolic_pulses: list[Any],
    q1asm_programs: list[Any],
    q1asm_provenance: list[Any],
    q1asm_by_sequencer: Mapping[Any, Any],
) -> list[IrDiagnostic]:
    diagnostics: list[IrDiagnostic] = []
    operation_ids = _id_set(operations, "id")
    operation_operation_ids = _id_set(operations, "operation_id")
    control_flow_ids = _id_set(control_flow_blocks, "id")
    symbolic_value_ids = _id_set(symbolic_values, "id")
    symbolic_pulse_ids = _id_set(symbolic_pulses, "id")
    sequencer_ids = _id_set(q1asm_programs, "sequencer_id")

    for index, pulse in enumerate(symbolic_pulses):
        if not isinstance(pulse, Mapping):
            continue
        schedulable_id = _text(pulse.get("schedulable_id"))
        if schedulable_id and operation_ids and schedulable_id not in operation_ids:
            diagnostics.append(
                IrDiagnostic(
                    code="missing_operation_reference",
                    path=f"symbolic_pulses[{index}].schedulable_id",
                    message=f"schedulable_id {schedulable_id!r} does not match operations[].id",
                )
            )
        operation_id = _text(pulse.get("operation_id"))
        if operation_id and operation_operation_ids and operation_id not in operation_operation_ids:
            diagnostics.append(
                IrDiagnostic(
                    code="missing_operation_reference",
                    path=f"symbolic_pulses[{index}].operation_id",
                    message=f"operation_id {operation_id!r} does not match operations[].operation_id",
                )
            )
        duration_value_id = _text(pulse.get("duration_value_id"))
        if duration_value_id and duration_value_id not in symbolic_value_ids:
            diagnostics.append(
                IrDiagnostic(
                    code="missing_symbolic_value_reference",
                    path=f"symbolic_pulses[{index}].duration_value_id",
                    message=f"duration_value_id {duration_value_id!r} does not match symbolic_values[].id",
                )
            )
        parameter_value_ids = pulse.get("parameter_value_ids")
        if isinstance(parameter_value_ids, Mapping):
            for key, value in parameter_value_ids.items():
                value_id = _text(value)
                if value_id and value_id not in symbolic_value_ids:
                    diagnostics.append(
                        IrDiagnostic(
                            code="missing_symbolic_value_reference",
                            path=f"symbolic_pulses[{index}].parameter_value_ids.{key}",
                            message=f"parameter value id {value_id!r} does not match symbolic_values[].id",
                        )
                    )

    for index, row in enumerate(q1asm_provenance):
        if not isinstance(row, Mapping):
            continue
        source_id = _text(row.get("source_id"))
        if source_id and symbolic_pulse_ids and source_id not in symbolic_pulse_ids:
            diagnostics.append(
                IrDiagnostic(
                    code="missing_symbolic_pulse_reference",
                    path=f"q1asm_provenance[{index}].source_id",
                    message=f"source_id {source_id!r} does not match symbolic_pulses[].id",
                )
            )
        sequencer_id = _text(row.get("sequencer_id") or row.get("sequencer"))
        if sequencer_id and sequencer_id not in sequencer_ids:
            diagnostics.append(
                IrDiagnostic(
                    code="missing_q1asm_program_reference",
                    path=f"q1asm_provenance[{index}].sequencer_id",
                    message=f"sequencer_id {sequencer_id!r} does not match q1asm_programs[].sequencer_id",
                )
            )

    q1asm_text_ids = {str(key) for key in q1asm_by_sequencer.keys()}
    for sequencer_id in sorted(sequencer_ids - q1asm_text_ids):
        diagnostics.append(
            IrDiagnostic(
                code="missing_q1asm_text",
                path="q1asm_by_sequencer",
                message=f"missing Q1ASM text for sequencer {sequencer_id!r}",
            )
        )
    for sequencer_id in sorted(q1asm_text_ids - sequencer_ids):
        diagnostics.append(
            IrDiagnostic(
                code="orphan_q1asm_text",
                path=f"q1asm_by_sequencer.{sequencer_id}",
                message=f"Q1ASM text has no q1asm_programs row for sequencer {sequencer_id!r}",
            )
        )

    for index, block in enumerate(control_flow_blocks):
        if not isinstance(block, Mapping):
            continue
        parent_id = _text(block.get("parent_control_flow_id"))
        if parent_id and parent_id not in control_flow_ids:
            diagnostics.append(
                IrDiagnostic(
                    code="missing_control_flow_parent",
                    path=f"control_flow_blocks[{index}].parent_control_flow_id",
                    message=f"parent_control_flow_id {parent_id!r} does not match control_flow_blocks[].id",
                )
            )
    return diagnostics


def _validate_timing(
    *,
    operations: list[Any],
    control_flow_blocks: list[Any],
    symbolic_pulses: list[Any],
) -> list[IrDiagnostic]:
    diagnostics: list[IrDiagnostic] = []
    for collection_name, rows in (
        ("operations", operations),
        ("control_flow_blocks", control_flow_blocks),
        ("symbolic_pulses", symbolic_pulses),
    ):
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            diagnostics.extend(_validate_span_fields(collection_name, index, row))

    operation_by_id = {
        str(row["id"]): row
        for row in operations
        if isinstance(row, Mapping) and _has_text(row.get("id"))
    }
    for index, pulse in enumerate(symbolic_pulses):
        if not isinstance(pulse, Mapping):
            continue
        parent = operation_by_id.get(str(pulse.get("schedulable_id")))
        if parent is None:
            continue
        parent_duration = _number(parent.get("duration"))
        if parent_duration is None or parent_duration <= 0:
            continue
        parent_span = _span(parent)
        pulse_span = _span(pulse)
        if parent_span is None or pulse_span is None:
            continue
        if pulse_span[0] < parent_span[0] - _TOLERANCE_SECONDS or pulse_span[1] > parent_span[1] + _TOLERANCE_SECONDS:
            diagnostics.append(
                IrDiagnostic(
                    code="pulse_outside_operation_window",
                    path=f"symbolic_pulses[{index}]",
                    message=(
                        f"pulse {pulse.get('id')!r} spans {_format_span(pulse_span)} outside "
                        f"operation {parent.get('id')!r} span {_format_span(parent_span)}"
                    ),
                )
            )
    return diagnostics


def _validate_q1asm_ranges(
    *,
    q1asm_programs: list[Any],
    q1asm_by_sequencer: Mapping[Any, Any],
    q1asm_provenance: list[Any],
) -> list[IrDiagnostic]:
    diagnostics: list[IrDiagnostic] = []
    program_lines: dict[str, list[str]] = {}
    for program in q1asm_programs:
        if not isinstance(program, Mapping):
            continue
        sequencer_id = _text(program.get("sequencer_id"))
        text = q1asm_by_sequencer.get(sequencer_id) if sequencer_id is not None else None
        if sequencer_id and isinstance(text, str):
            program_lines[sequencer_id] = text.splitlines()

    for index, row in enumerate(q1asm_provenance):
        if not isinstance(row, Mapping):
            continue
        sequencer_id = _text(row.get("sequencer_id") or row.get("sequencer"))
        start_line = _int(row.get("q1asm_line_start", row.get("line")))
        end_line = _int(row.get("q1asm_line_end", row.get("line_end", row.get("line"))))
        if start_line is None or end_line is None:
            diagnostics.append(
                IrDiagnostic(
                    code="missing_q1asm_line_range",
                    path=f"q1asm_provenance[{index}]",
                    message="provenance row must define q1asm_line_start and q1asm_line_end",
                )
            )
            continue
        if start_line > end_line:
            diagnostics.append(
                IrDiagnostic(
                    code="invalid_q1asm_line_range",
                    path=f"q1asm_provenance[{index}]",
                    message=f"q1asm_line_start {start_line} is greater than q1asm_line_end {end_line}",
                )
            )
            continue

        lines = program_lines.get(sequencer_id or "")
        if lines is not None and (start_line < 1 or end_line > len(lines)):
            diagnostics.append(
                IrDiagnostic(
                    code="q1asm_line_range_out_of_bounds",
                    path=f"q1asm_provenance[{index}]",
                    message=f"line range {start_line}-{end_line} exceeds program length {len(lines)}",
                )
            )

        operand_mappings = row.get("operand_mappings")
        if not isinstance(operand_mappings, list):
            continue
        for mapping_index, mapping in enumerate(operand_mappings):
            if not isinstance(mapping, Mapping):
                continue
            mapping_line = _int(mapping.get("line"))
            mapping_line_end = _int(mapping.get("line_end", mapping.get("line")))
            if mapping_line is None or mapping_line_end is None:
                diagnostics.append(
                    IrDiagnostic(
                        code="invalid_operand_mapping_range",
                        path=f"q1asm_provenance[{index}].operand_mappings[{mapping_index}]",
                        message="operand mapping must define integer line and line_end",
                    )
                )
                continue
            if mapping_line < start_line or mapping_line_end > end_line or mapping_line > mapping_line_end:
                diagnostics.append(
                    IrDiagnostic(
                        code="operand_mapping_outside_provenance_range",
                        path=f"q1asm_provenance[{index}].operand_mappings[{mapping_index}]",
                        message=f"operand mapping line range {mapping_line}-{mapping_line_end} is outside {start_line}-{end_line}",
                    )
                )
            if lines is not None and (mapping_line < 1 or mapping_line_end > len(lines)):
                diagnostics.append(
                    IrDiagnostic(
                        code="operand_mapping_line_out_of_bounds",
                        path=f"q1asm_provenance[{index}].operand_mappings[{mapping_index}]",
                        message=f"operand mapping line range {mapping_line}-{mapping_line_end} exceeds program length {len(lines)}",
                    )
                )
            instruction = _text(mapping.get("instruction"))
            if instruction and lines is not None and 1 <= mapping_line <= len(lines):
                actual_line = lines[mapping_line - 1].strip()
                if not actual_line.startswith(instruction):
                    diagnostics.append(
                        IrDiagnostic(
                            code="q1asm_instruction_mismatch",
                            path=f"q1asm_provenance[{index}].operand_mappings[{mapping_index}].instruction",
                            message=f"expected instruction {instruction!r} at line {mapping_line}, found {actual_line!r}",
                        )
                    )
    return diagnostics


def _validate_control_flow(
    operations: list[Any],
    control_flow_blocks: list[Any],
) -> list[IrDiagnostic]:
    diagnostics: list[IrDiagnostic] = []
    control_flow_by_id = {
        str(block["id"]): block
        for block in control_flow_blocks
        if isinstance(block, Mapping) and _has_text(block.get("id"))
    }
    for index, block in enumerate(control_flow_blocks):
        if not isinstance(block, Mapping):
            continue
        block_id = _text(block.get("id"))
        if block_id and not block_id.startswith("control-flow:"):
            diagnostics.append(
                IrDiagnostic(
                    code="control_flow_id_namespace",
                    path=f"control_flow_blocks[{index}].id",
                    message=f"control-flow block id {block_id!r} must start with 'control-flow:'",
                )
            )
        parent_id = _text(block.get("parent_control_flow_id"))
        if parent_id and parent_id in control_flow_by_id:
            diagnostics.extend(
                _validate_depth(
                    row=block,
                    row_path=f"control_flow_blocks[{index}]",
                    parent=control_flow_by_id[parent_id],
                )
            )
            diagnostics.extend(
                _validate_child_inside_parent(
                    row=block,
                    row_path=f"control_flow_blocks[{index}]",
                    parent=control_flow_by_id[parent_id],
                )
            )
    diagnostics.extend(_validate_control_flow_cycles(control_flow_by_id))

    for index, operation in enumerate(operations):
        if not isinstance(operation, Mapping):
            continue
        parent_id = _text(operation.get("parent_control_flow_id"))
        if not parent_id:
            continue
        parent = control_flow_by_id.get(parent_id)
        if parent is None:
            diagnostics.append(
                IrDiagnostic(
                    code="missing_control_flow_parent",
                    path=f"operations[{index}].parent_control_flow_id",
                    message=f"parent_control_flow_id {parent_id!r} does not match control_flow_blocks[].id",
                )
            )
            continue
        diagnostics.extend(
            _validate_depth(
                row=operation,
                row_path=f"operations[{index}]",
                parent=parent,
            )
        )
        diagnostics.extend(
            _validate_child_inside_parent(
                row=operation,
                row_path=f"operations[{index}]",
                parent=parent,
            )
        )
    return diagnostics


def _validate_control_flow_cycles(control_flow_by_id: Mapping[str, Mapping[str, Any]]) -> list[IrDiagnostic]:
    diagnostics: list[IrDiagnostic] = []
    for block_id in control_flow_by_id:
        seen: set[str] = set()
        current: str | None = block_id
        while current:
            if current in seen:
                diagnostics.append(
                    IrDiagnostic(
                        code="control_flow_cycle",
                        path=f"control_flow_blocks.{block_id}",
                        message=f"control-flow parent chain for {block_id!r} contains a cycle",
                    )
                )
                break
            seen.add(current)
            parent_id = _text(control_flow_by_id.get(current, {}).get("parent_control_flow_id"))
            current = parent_id if parent_id in control_flow_by_id else None
    return diagnostics


def _validate_depth(
    *,
    row: Mapping[str, Any],
    row_path: str,
    parent: Mapping[str, Any],
) -> list[IrDiagnostic]:
    row_depth = _int(row.get("depth", 0))
    parent_depth = _int(parent.get("depth", 0))
    if row_depth is None or parent_depth is None:
        return [
            IrDiagnostic(
                code="invalid_control_flow_depth",
                path=row_path,
                message="control-flow child and parent depth must be integers",
            )
        ]
    expected = parent_depth + 1
    if row_depth != expected:
        return [
            IrDiagnostic(
                code="control_flow_depth_mismatch",
                path=f"{row_path}.depth",
                message=f"depth {row_depth} should be parent depth {parent_depth} + 1",
            )
        ]
    return []


def _validate_child_inside_parent(
    *,
    row: Mapping[str, Any],
    row_path: str,
    parent: Mapping[str, Any],
) -> list[IrDiagnostic]:
    child_span = _span(row)
    parent_span = _control_flow_span(parent)
    if child_span is None or parent_span is None:
        return []
    if child_span[0] < parent_span[0] - _TOLERANCE_SECONDS or child_span[1] > parent_span[1] + _TOLERANCE_SECONDS:
        return [
            IrDiagnostic(
                code="control_flow_child_outside_parent_window",
                path=row_path,
                message=f"child span {_format_span(child_span)} is outside parent span {_format_span(parent_span)}",
            )
        ]
    return []


def _validate_span_fields(
    collection_name: str,
    index: int,
    row: Mapping[str, Any],
) -> list[IrDiagnostic]:
    diagnostics: list[IrDiagnostic] = []
    abs_time = row.get("abs_time")
    duration = row.get("duration")
    if abs_time is not None and _number(abs_time) is None:
        diagnostics.append(
            IrDiagnostic(
                code="invalid_abs_time",
                path=f"{collection_name}[{index}].abs_time",
                message="abs_time must be a finite number when present",
            )
        )
    duration_number = _number(duration)
    if duration is not None and duration_number is None:
        diagnostics.append(
            IrDiagnostic(
                code="invalid_duration",
                path=f"{collection_name}[{index}].duration",
                message="duration must be a finite number when present",
            )
        )
    elif duration_number is not None and duration_number < 0:
        diagnostics.append(
            IrDiagnostic(
                code="invalid_duration",
                path=f"{collection_name}[{index}].duration",
                message="duration must be non-negative",
            )
        )
    return diagnostics


def _control_flow_span(row: Mapping[str, Any]) -> tuple[float, float] | None:
    start = _number(row.get("preview_abs_time", row.get("abs_time")))
    duration = _number(row.get("duration"))
    preview_duration = _number(row.get("preview_duration"))
    durations = [value for value in (duration, preview_duration) if value is not None and value >= 0]
    if start is None or not durations:
        return None
    max_duration = max(durations)
    if max_duration <= 0:
        return None
    return (start, start + max_duration)


def _span(row: Mapping[str, Any]) -> tuple[float, float] | None:
    start = _number(row.get("abs_time"))
    duration = _number(row.get("duration"))
    if start is None or duration is None or duration < 0:
        return None
    return (start, start + duration)


def _format_span(span: tuple[float, float]) -> str:
    return f"{span[0]:.12g}-{span[1]:.12g}s"


def _id_set(rows: list[Any], key: str) -> set[str]:
    return {
        str(row[key])
        for row in rows
        if isinstance(row, Mapping) and _has_text(row.get(key))
    }


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    if _has_text(value):
        return str(value)
    return None


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) and math.isfinite(value):
        return float(value)
    return None
