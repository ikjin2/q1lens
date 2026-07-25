from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from qbstimeline._access import annotations_for, get_value, unwrap
from qbstimeline.symbols import SymbolicValue, symbolic_values_to_ir

_EXCLUDED_PARAMETER_KEYS = {
    "data",
    "samples",
    "waveform",
    "waveforms",
    "weights",
    "t_samples",
    "duration",
    "name",
    "pulse_type",
    "protocol",
    "acq_protocol",
    "port",
    "clock",
    "t0",
}
MANUAL_SWEEP_COMPACT_MIN = 2
SCHEDULE_REPETITION_ID = "__schedule_repetition"
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def extract_symbolic_pulse_layer(compiled_schedule: Any) -> dict[str, list[dict[str, Any]]]:
    values: list[SymbolicValue] = []
    pulses: list[dict[str, Any]] = []
    _collect_symbolic_pulses(
        compiled_schedule,
        values=values,
        pulses=pulses,
        id_prefix="",
        time_offset=0.0,
        parent_control_flow_id=None,
        depth=0,
    )
    _wrap_schedule_repetition_pulses(compiled_schedule, pulses)
    return {
        "symbolic_values": symbolic_values_to_ir(values),
        "symbolic_pulses": pulses,
    }


def _collect_symbolic_pulses(
    schedule: Any,
    *,
    values: list[SymbolicValue],
    pulses: list[dict[str, Any]],
    id_prefix: str,
    time_offset: float,
    parent_control_flow_id: str | None,
    depth: int,
) -> None:
    schedulables = get_value(schedule, "schedulables", {})
    operations = get_value(schedule, "operations", None)
    if operations is None:
        operations = get_value(schedule, "operation_dict", {})
    try:
        has_experiments = isinstance(getattr(schedule, "_experiments", None), list)
    except Exception:
        has_experiments = False
    if not isinstance(schedulables, Mapping) or (not schedulables and has_experiments):
        _collect_experiment_symbolic_pulses(
            schedule,
            values=values,
            pulses=pulses,
            id_prefix=id_prefix,
            time_offset=time_offset,
            parent_control_flow_id=parent_control_flow_id,
            depth=depth,
        )
        return
    if isinstance(operations, Mapping):
        manual_sweep = _manual_sweep_info(schedulables, operations)
        if manual_sweep is not None:
            _collect_manual_sweep_pulses(
                manual_sweep,
                values=values,
                pulses=pulses,
                id_prefix=id_prefix,
                time_offset=time_offset,
                parent_control_flow_id=parent_control_flow_id,
                depth=depth,
            )
            return

    use_source_order_timing = _uses_source_order_timing(schedulables)
    source_order_cursor = 0.0
    for schedulable_id, schedulable in schedulables.items():
        schedulable_data = unwrap(schedulable)
        if not isinstance(schedulable_data, Mapping):
            continue
        operation_id = schedulable_data.get("operation_id")
        operation = operations.get(operation_id) if isinstance(operations, Mapping) else None
        operation_annotations = annotations_for(operation)
        values.extend(operation_annotations.values())
        local_abs_time = _number_or_none(schedulable_data.get("abs_time"))
        if local_abs_time is None:
            local_abs_time = source_order_cursor if use_source_order_timing else 0.0
        sched_abs_time = time_offset + local_abs_time
        row_id = f"{id_prefix}{schedulable_id}"
        operation_label = _operation_label(operation, operation_id)
        pulses.extend(
            _blocks_for_info_list(
                role="pulse",
                id_prefix="pulse",
                info_list=get_value(operation, "pulse_info", []),
                schedulable_id=str(row_id),
                operation_id=str(operation_id),
                operation_label=operation_label,
                sched_abs_time=sched_abs_time,
                annotations=operation_annotations,
                parent_control_flow_id=parent_control_flow_id,
                depth=depth,
            )
        )
        pulses.extend(
            _blocks_for_info_list(
                role="acquisition",
                id_prefix="acq",
                info_list=get_value(operation, "acquisition_info", []),
                schedulable_id=str(row_id),
                operation_id=str(operation_id),
                operation_label=operation_label,
                sched_abs_time=sched_abs_time,
                annotations=operation_annotations,
                parent_control_flow_id=parent_control_flow_id,
                depth=depth,
            )
        )

        control_flow_info = _control_flow_info(operation)
        body = _control_flow_body(operation, control_flow_info)
        if body is not None:
            body_duration = _schedule_preview_duration(body)
            control_flow_id = f"control-flow:{row_id}"
            _collect_symbolic_pulses(
                body,
                values=values,
                pulses=pulses,
                id_prefix=f"{row_id}/",
                time_offset=sched_abs_time + (_number_or_none(control_flow_info.get("t0")) or 0.0),
                parent_control_flow_id=control_flow_id,
                depth=depth + 1,
            )
            source_order_cursor = max(source_order_cursor, local_abs_time + (_operation_duration(operation) or body_duration))
            continue

        nested_body = _nested_schedule_body(operation)
        if nested_body is not None:
            nested_duration = _schedule_preview_duration(nested_body)
            _collect_symbolic_pulses(
                nested_body,
                values=values,
                pulses=pulses,
                id_prefix=f"{row_id}/",
                time_offset=sched_abs_time,
                parent_control_flow_id=parent_control_flow_id,
                depth=depth,
            )
            source_order_cursor = max(source_order_cursor, local_abs_time + (_operation_duration(operation) or nested_duration))
            continue

        source_order_cursor = max(source_order_cursor, local_abs_time + _operation_duration(operation))


def _collect_manual_sweep_pulses(
    manual_sweep: dict[str, Any],
    *,
    values: list[SymbolicValue],
    pulses: list[dict[str, Any]],
    id_prefix: str,
    time_offset: float,
    parent_control_flow_id: str | None,
    depth: int,
) -> None:
    schedulable = manual_sweep["schedulable"]
    schedulable_id = str(manual_sweep["schedulable_id"])
    operation_id = manual_sweep["operation_id"]
    operation = manual_sweep["operation"]
    local_abs_time = _number_or_none(schedulable.get("abs_time")) or 0.0
    sched_abs_time = time_offset + local_abs_time
    row_id = f"{id_prefix}{schedulable_id}"
    operation_annotations = annotations_for(operation)
    values.extend(operation_annotations.values())
    operation_label = _operation_label(operation, operation_id)
    pulses.extend(
        _blocks_for_info_list(
            role="pulse",
            id_prefix="pulse",
            info_list=get_value(operation, "pulse_info", []),
            schedulable_id=str(row_id),
            operation_id=str(operation_id),
            operation_label=operation_label,
            sched_abs_time=sched_abs_time,
            annotations=operation_annotations,
            parent_control_flow_id=parent_control_flow_id,
            depth=depth,
        )
    )
    pulses.extend(
        _blocks_for_info_list(
            role="acquisition",
            id_prefix="acq",
            info_list=get_value(operation, "acquisition_info", []),
            schedulable_id=str(row_id),
            operation_id=str(operation_id),
            operation_label=operation_label,
            sched_abs_time=sched_abs_time,
            annotations=operation_annotations,
            parent_control_flow_id=parent_control_flow_id,
            depth=depth,
        )
    )

    nested_body = _nested_schedule_body(operation)
    if nested_body is not None:
        _collect_symbolic_pulses(
            nested_body,
            values=values,
            pulses=pulses,
            id_prefix=f"{row_id}/",
            time_offset=sched_abs_time,
            parent_control_flow_id=f"control-flow:{row_id}",
            depth=depth + 1,
        )


def _manual_sweep_info(
    schedulables: Mapping[Any, Any],
    operations: Mapping[Any, Any],
) -> dict[str, Any] | None:
    if len(schedulables) < MANUAL_SWEEP_COMPACT_MIN:
        return None

    first: dict[str, Any] | None = None
    labels: set[str] = set()
    signatures: set[tuple[Any, ...]] = set()
    abs_times: list[float] = []
    for schedulable_id, schedulable in schedulables.items():
        schedulable_data = unwrap(schedulable)
        if not isinstance(schedulable_data, Mapping):
            return None
        local_abs_time = _number_or_none(schedulable_data.get("abs_time"))
        if local_abs_time is not None:
            abs_times.append(local_abs_time)
        operation_id = schedulable_data.get("operation_id")
        operation = operations.get(operation_id)
        if _has_control_flow_metadata(_control_flow_info(operation)):
            return None
        nested_body = _nested_schedule_body(operation)
        if nested_body is None:
            return None
        signature = _schedule_shape_signature(nested_body)
        if signature is None:
            return None
        signatures.add(signature)
        label = get_value(operation, "name", None) or str(operation_id)
        labels.add(str(label))
        if first is None:
            first = {
                "schedulable_id": schedulable_id,
                "schedulable": schedulable_data,
                "operation_id": operation_id,
                "operation": operation,
            }
        if len(labels) > 1:
            return None
        if len(signatures) > 1:
            return None

    if first is not None:
        if abs_times:
            if len(abs_times) != len(schedulables):
                return None
            first_abs_time = abs_times[0]
            if any(abs(abs_time - first_abs_time) > 1e-15 for abs_time in abs_times[1:]):
                return None
        first["repetitions"] = len(schedulables)
    return first


def _schedule_shape_signature(schedule: Any) -> tuple[Any, ...] | None:
    schedulables = get_value(schedule, "schedulables", {})
    operations = get_value(schedule, "operations", None)
    if operations is None:
        operations = get_value(schedule, "operation_dict", {})
    if not isinstance(schedulables, Mapping) or not isinstance(operations, Mapping):
        return None
    items: list[Any] = []
    for schedulable in schedulables.values():
        schedulable_data = unwrap(schedulable)
        if not isinstance(schedulable_data, Mapping):
            return None
        operation_id = schedulable_data.get("operation_id")
        operation = operations.get(operation_id)
        nested_body = _nested_schedule_body(operation)
        if nested_body is not None:
            nested_signature = _schedule_shape_signature(nested_body)
            if nested_signature is None:
                return None
            items.append(("schedule", get_value(operation, "name", None) or str(operation_id), nested_signature))
        else:
            items.append(("operation", get_value(operation, "name", None) or str(operation_id), _operation_duration(operation)))
    return tuple(items)


def _wrap_schedule_repetition_pulses(schedule: Any, pulses: list[dict[str, Any]]) -> None:
    repetitions = _schedule_repetitions(schedule)
    if repetitions <= 1:
        return
    control_flow_id = f"control-flow:{SCHEDULE_REPETITION_ID}"
    for pulse in pulses:
        if pulse.get("parent_control_flow_id"):
            pulse["depth"] = int(pulse.get("depth", 0)) + 1
        else:
            pulse["parent_control_flow_id"] = control_flow_id
            pulse["depth"] = 1


def _schedule_preview_duration(schedule: Any) -> float:
    duration = _number_or_none(get_value(schedule, "duration", None))
    if duration is not None:
        return _clean_float(duration)
    schedulables = get_value(schedule, "schedulables", {})
    operations = get_value(schedule, "operations", None)
    if operations is None:
        operations = get_value(schedule, "operation_dict", {})
    if not isinstance(schedulables, Mapping) or not isinstance(operations, Mapping):
        return 0.0
    manual_sweep = _manual_sweep_info(schedulables, operations)
    if manual_sweep is not None:
        repetitions = manual_sweep.get("repetitions")
        nested_body = _nested_schedule_body(manual_sweep.get("operation"))
        if isinstance(repetitions, int | float) and nested_body is not None:
            return _clean_float(_schedule_preview_duration(nested_body) * repetitions)

    use_source_order_timing = _uses_source_order_timing(schedulables)
    source_order_cursor = 0.0
    end = 0.0
    for schedulable in schedulables.values():
        schedulable_data = unwrap(schedulable)
        if not isinstance(schedulable_data, Mapping):
            continue
        operation = operations.get(schedulable_data.get("operation_id"))
        local_abs_time = _number_or_none(schedulable_data.get("abs_time"))
        if local_abs_time is None:
            local_abs_time = source_order_cursor if use_source_order_timing else 0.0
        item_duration = _operation_duration(operation)
        body = _control_flow_body(operation, _control_flow_info(operation))
        nested_body = body if body is not None else _nested_schedule_body(operation)
        if not item_duration and nested_body is not None:
            item_duration = _schedule_preview_duration(nested_body)
        end = max(end, local_abs_time + item_duration)
        source_order_cursor = max(source_order_cursor, local_abs_time + item_duration)
    return _clean_float(end)


def _uses_source_order_timing(schedulables: Mapping[Any, Any]) -> bool:
    rows = [unwrap(schedulable) for schedulable in schedulables.values()]
    mappings = [row for row in rows if isinstance(row, Mapping)]
    if not mappings:
        return False
    if any(_number_or_none(row.get("abs_time")) is not None for row in mappings):
        return False
    return len(mappings) > 1


def _operation_duration(operation: Any) -> float:
    duration = _number_or_none(get_value(operation, "duration", None))
    if duration is not None:
        return duration
    extents = [
        extent
        for info_list in (
            _normalize_info_list(get_value(operation, "pulse_info", [])),
            _normalize_info_list(get_value(operation, "acquisition_info", [])),
        )
        for extent in (_info_extent(raw_info) for raw_info in info_list)
        if extent is not None
    ]
    return max(extents, default=0.0)


def _info_extent(raw_info: Any) -> float | None:
    info = unwrap(raw_info)
    if not isinstance(info, Mapping):
        return None
    duration = _number_or_none(info.get("duration"))
    if duration is None:
        return None
    return (_number_or_none(info.get("t0")) or 0.0) + duration


def _schedule_repetitions(schedule: Any) -> int:
    repetitions = get_value(schedule, "repetitions", 1)
    return int(repetitions) if isinstance(repetitions, int | float) and repetitions > 1 else 1


def _experiment_schedules(schedule: Any) -> list[Any]:
    try:
        experiments = getattr(schedule, "_experiments", None)
    except Exception:
        experiments = None
    if not isinstance(experiments, list):
        return []
    schedules: list[Any] = []
    for experiment in experiments:
        if not isinstance(experiment, Mapping):
            continue
        steps = experiment.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, Mapping):
                continue
            schedule_info = step.get("schedule_info")
            if not isinstance(schedule_info, Mapping):
                continue
            nested_schedule = schedule_info.get("schedule")
            if nested_schedule is not None:
                schedules.append(nested_schedule)
    return schedules


def _collect_experiment_symbolic_pulses(
    schedule: Any,
    *,
    values: list[SymbolicValue],
    pulses: list[dict[str, Any]],
    id_prefix: str,
    time_offset: float,
    parent_control_flow_id: str | None,
    depth: int,
) -> None:
    try:
        experiments = getattr(schedule, "_experiments", None)
    except Exception:
        experiments = None
    if not isinstance(experiments, list):
        return
    for experiment_index, experiment in enumerate(experiments):
        if not isinstance(experiment, Mapping):
            continue
        steps = experiment.get("steps")
        if not isinstance(steps, list):
            continue
        _collect_experiment_step_pulses(
            steps,
            values=values,
            pulses=pulses,
            id_prefix=f"{id_prefix}experiment{experiment_index}/",
            time_offset=time_offset,
            parent_control_flow_id=parent_control_flow_id,
            depth=depth,
        )


def _collect_experiment_step_pulses(
    steps: list[Any],
    *,
    values: list[SymbolicValue],
    pulses: list[dict[str, Any]],
    id_prefix: str,
    time_offset: float,
    parent_control_flow_id: str | None,
    depth: int,
) -> None:
    schedule_step_count = sum(1 for step in steps if _step_schedule(step) is not None)
    for step_index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            continue
        loop_info = step.get("loop_info")
        if isinstance(loop_info, Mapping):
            nested_steps = loop_info.get("steps")
            if isinstance(nested_steps, list):
                _collect_experiment_step_pulses(
                    nested_steps,
                    values=values,
                    pulses=pulses,
                    id_prefix=f"{id_prefix}step{step_index}/",
                    time_offset=time_offset,
                    parent_control_flow_id=f"control-flow:{id_prefix}step{step_index}",
                    depth=depth + 1,
                )
            continue
        nested_schedule = _step_schedule(step)
        if nested_schedule is not None:
            nested_prefix = id_prefix if schedule_step_count == 1 else f"{id_prefix}step{step_index}/"
            _collect_symbolic_pulses(
                nested_schedule,
                values=values,
                pulses=pulses,
                id_prefix=nested_prefix,
                time_offset=time_offset,
                parent_control_flow_id=parent_control_flow_id,
                depth=depth,
            )


def _step_schedule(step: Any) -> Any | None:
    if not isinstance(step, Mapping):
        return None
    schedule_info = step.get("schedule_info")
    if not isinstance(schedule_info, Mapping):
        return None
    return schedule_info.get("schedule")


def _blocks_for_info_list(
    *,
    role: str,
    id_prefix: str,
    info_list: Any,
    schedulable_id: str,
    operation_id: str,
    operation_label: str | None,
    sched_abs_time: float | None,
    annotations: dict[str, SymbolicValue],
    parent_control_flow_id: str | None,
    depth: int,
) -> list[dict[str, Any]]:
    info_list = _normalize_info_list(info_list)
    rows: list[dict[str, Any]] = []
    for index, raw_info in enumerate(info_list):
        info = unwrap(raw_info)
        if not isinstance(info, Mapping):
            continue
        port = _string_or_none(info.get("port"))
        clock = _string_or_none(info.get("clock"))
        t0 = _number_or_none(info.get("t0")) or 0.0
        duration = _number_or_none(info.get("duration"))
        kind = _kind(info, role)
        parameters = _parameters(info)
        display_label, display_subtitle = _display_text(
            kind=kind,
            operation_label=operation_label,
            port=port,
            clock=clock,
            duration=duration,
            parameters=parameters,
        )
        block = {
            "id": f"{id_prefix}:{schedulable_id}:{role}:{index}",
            "operation_id": operation_id,
            "schedulable_id": schedulable_id,
            "kind": kind,
            "display_label": display_label,
            "display_subtitle": display_subtitle,
            "role": role,
            "port": port,
            "clock": clock,
            "lane": _lane(port, clock),
            "abs_time": _clean_float(sched_abs_time + t0) if sched_abs_time is not None else None,
            "duration": duration,
            "parameters": parameters,
        }
        if parent_control_flow_id:
            block["parent_control_flow_id"] = parent_control_flow_id
            block["depth"] = depth
        duration_value = _duration_annotation_for_value(annotations, duration)
        if duration_value is not None and duration == duration_value.value:
            block["duration_value_id"] = duration_value.id
        parameter_value_ids = _parameter_annotations_for_values(annotations, parameters)
        if parameter_value_ids:
            block["parameter_value_ids"] = parameter_value_ids
        rows.append(block)
    return rows


def _parameter_annotations_for_values(
    annotations: dict[str, SymbolicValue],
    parameters: Mapping[str, Any],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, parameter_value in parameters.items():
        annotation = annotations.get(key)
        if annotation is not None and annotation.value == parameter_value:
            result[str(key)] = annotation.id
            continue
        for annotation_key, value in annotations.items():
            if (
                value.kind != "duration"
                and value.value == parameter_value
                and _can_fallback_annotation_to_parameter(annotation_key, value, str(key), parameters)
            ):
                result[str(key)] = value.id
                break
    return result


def _can_fallback_annotation_to_parameter(
    annotation_key: str,
    annotation: SymbolicValue,
    parameter_key: str,
    parameters: Mapping[str, Any],
) -> bool:
    if annotation_key in parameters and annotation_key != parameter_key:
        return False
    if annotation.kind == "amplitude":
        return parameter_key in {"amp", "amplitude"}
    if annotation.kind == "offset":
        return parameter_key.startswith("offset")
    return False


def _duration_annotation_for_value(
    annotations: dict[str, SymbolicValue],
    duration: int | float | None,
) -> SymbolicValue | None:
    duration_value = annotations.get("duration")
    if duration_value is not None:
        return duration_value
    for value in annotations.values():
        if value.kind == "duration" and duration == value.value:
            return value
    return None


def _normalize_info_list(info_list: Any) -> list[Any]:
    if isinstance(info_list, Mapping):
        return [info_list] if info_list else []
    return info_list if isinstance(info_list, list) else []


def _control_flow_info(operation: Any) -> Mapping[str, Any]:
    info = get_value(operation, "control_flow_info", {})
    return info if isinstance(info, Mapping) else {}


def _control_flow_body(operation: Any, control_flow_info: Mapping[str, Any]) -> Any | None:
    body = control_flow_info.get("body")
    if body is not None:
        return body
    if not _has_control_flow_metadata(control_flow_info):
        return None
    try:
        body = getattr(operation, "body", None)
    except Exception:
        body = None
    return body if body is not None else None


def _has_control_flow_metadata(control_flow_info: Mapping[str, Any]) -> bool:
    return any(key in control_flow_info for key in ("repetitions", "domain", "body", "t0"))


def _nested_schedule_body(operation: Any) -> Any | None:
    try:
        body = getattr(operation, "body", None)
    except Exception:
        body = None
    if _is_schedule_like(body):
        return body
    if _is_schedule_like(operation):
        return operation
    return None


def _is_schedule_like(value: Any) -> bool:
    if value is None:
        return False
    schedulables = get_value(value, "schedulables", None)
    operations = get_value(value, "operations", None)
    if operations is None:
        operations = get_value(value, "operation_dict", None)
    return isinstance(schedulables, Mapping) and isinstance(operations, Mapping)


def _kind(info: Mapping[str, Any], role: str) -> str:
    keys = ("name", "pulse_type", "protocol", "acq_protocol", "wf_func")
    for key in keys:
        value = info.get(key)
        if isinstance(value, str) and value:
            return value.rsplit(".", 1)[-1]
    return "Acquisition" if role == "acquisition" else "Pulse"


def _operation_label(operation: Any, operation_id: Any) -> str | None:
    label = get_value(operation, "name", None)
    if isinstance(label, str) and label:
        return label
    if operation_id is None:
        return None
    return str(operation_id)


def _display_text(
    *,
    kind: str,
    operation_label: str | None,
    port: str | None,
    clock: str | None,
    duration: float | None,
    parameters: dict[str, Any],
) -> tuple[str, str]:
    target = _target_label(port, clock)
    descriptor = f"{kind} {target}" if target else kind
    label = operation_label if _is_human_label(operation_label) and operation_label != kind else descriptor

    subtitle_parts: list[str] = []
    if label != descriptor:
        subtitle_parts.append(descriptor)
    duration_label = _format_duration(duration)
    if duration_label:
        subtitle_parts.append(duration_label)
    subtitle_parts.extend(_display_parameter_parts(parameters))
    return label, " | ".join(subtitle_parts)


def _target_label(port: str | None, clock: str | None) -> str:
    if port:
        return port
    if clock and "." in clock:
        return clock.split(".", 1)[0]
    return clock or ""


def _is_human_label(value: str | None) -> bool:
    if not value:
        return False
    parts = [part for part in value.split("/") if part]
    if parts and all(_UUID_RE.fullmatch(part) for part in parts):
        return False
    return not bool(_UUID_RE.fullmatch(value))


def _format_duration(value: float | None) -> str:
    if value is None:
        return ""
    abs_value = abs(value)
    if abs_value < 1e-6:
        return _format_scaled(value * 1e9, "ns")
    if abs_value < 1e-3:
        return _format_scaled(value * 1e6, "us")
    if abs_value < 1:
        return _format_scaled(value * 1e3, "ms")
    return _format_scaled(value, "s")


def _format_scaled(value: float, suffix: str) -> str:
    rounded = round(value, 2)
    if float(rounded).is_integer():
        return f"{int(rounded)} {suffix}"
    return f"{rounded:g} {suffix}"


def _display_parameter_parts(parameters: dict[str, Any]) -> list[str]:
    priority = ("amp", "phase", "frequency", "acq_channel")
    ordered_keys = [key for key in priority if key in parameters]
    ordered_keys.extend(sorted(key for key in parameters if key not in ordered_keys))
    return [f"{key} {_format_display_value(parameters[key])}" for key in ordered_keys]


def _format_display_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _lane(port: str | None, clock: str | None) -> str:
    return f"{port or 'unassigned'} / {clock or 'no_clock'}"


def _parameters(info: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in info.items():
        if key in _EXCLUDED_PARAMETER_KEYS:
            continue
        if _is_compact_json_value(value):
            result[str(key)] = value
    return result


def _is_compact_json_value(value: Any) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list):
        return len(value) <= 8 and all(
            item is None or isinstance(item, str | int | float | bool) for item in value
        )
    return False


def _number_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _clean_float(value: float) -> float:
    return float(f"{value:.15g}")


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
