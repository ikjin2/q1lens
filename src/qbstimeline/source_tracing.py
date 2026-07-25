from __future__ import annotations

import inspect
import sysconfig
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import FrameType
from typing import Any, Callable, Iterator


_STDLIB_PATH = (sysconfig.get_paths().get("stdlib") or "").replace("\\", "/").lower()


@dataclass(frozen=True)
class SourceTraceLocation:
    file: str
    line: int
    column: int
    label: str | None = None


@dataclass
class SourceTrace:
    locations_by_schedulable_id: dict[str, SourceTraceLocation] = field(default_factory=dict)
    locations_by_operation_id: dict[str, SourceTraceLocation] = field(default_factory=dict)
    locations_by_schedule_id: dict[int, SourceTraceLocation] = field(default_factory=dict)


@contextmanager
def traced_schedule_adds() -> Iterator[SourceTrace]:
    trace = SourceTrace()
    patches = _patch_importable_schedule_adds(trace)
    previous_profile = sys.getprofile()

    def profiler(frame: FrameType, event: str, arg: Any) -> Callable[..., Any] | None:
        if previous_profile is not None:
            previous_profile(frame, event, arg)
        if event == "return" and frame.f_code.co_name == "add":
            self_obj = frame.f_locals.get("self")
            if self_obj is not None:
                _record_add_result(
                    trace,
                    schedule=self_obj,
                    result=arg,
                    label=frame.f_locals.get("label"),
                    caller_frame=frame.f_back,
                )
        elif event == "return" and frame.f_code.co_name == "__init__":
            self_obj = frame.f_locals.get("self")
            if _looks_like_schedule(self_obj):
                _record_schedule_constructor(
                    trace,
                    schedule=self_obj,
                    constructor_frame=frame,
                )
        return profiler

    sys.setprofile(profiler)
    try:
        yield trace
    finally:
        sys.setprofile(previous_profile)
        for cls, attribute, original in reversed(patches):
            setattr(cls, attribute, original)


def _patch_importable_schedule_adds(trace: SourceTrace) -> list[tuple[type[Any], str, Any]]:
    patches: list[tuple[type[Any], str, Any]] = []
    for module_name, class_name in (
        ("qblox_scheduler.schedule", "Schedule"),
        ("qblox_scheduler.schedules.schedule", "TimeableSchedule"),
    ):
        try:
            module = __import__(module_name, fromlist=[class_name])
            cls = getattr(module, class_name)
            original = getattr(cls, "add")
            original_init = getattr(cls, "__init__")
        except Exception:
            continue
        if getattr(original, "_qbstimeline_traced", False):
            continue

        def wrapper(self, *args, __original=original, **kwargs):
            caller_frame = _external_caller_frame(inspect.currentframe())
            result = __original(self, *args, **kwargs)
            _record_add_result(
                trace,
                schedule=self,
                result=result,
                label=kwargs.get("label"),
                caller_frame=caller_frame,
            )
            return result

        wrapper._qbstimeline_traced = True
        patches.append((cls, "add", original))
        setattr(cls, "add", wrapper)

        if not getattr(original_init, "_qbstimeline_traced", False):
            def init_wrapper(self, *args, __original=original_init, **kwargs):
                __original(self, *args, **kwargs)
                _record_schedule_constructor(
                    trace,
                    schedule=self,
                    constructor_frame=inspect.currentframe(),
                )

            init_wrapper._qbstimeline_traced = True
            patches.append((cls, "__init__", original_init))
            setattr(cls, "__init__", init_wrapper)
    return patches


def _record_add_result(
    trace: SourceTrace,
    *,
    schedule: Any,
    result: Any,
    label: Any,
    caller_frame: FrameType | None,
) -> None:
    schedulable_id = _schedulable_id(schedule, result)
    if schedulable_id is None:
        return
    location = _location_from_frame(caller_frame, label=label)
    if location is None:
        return
    existing = trace.locations_by_schedulable_id.get(schedulable_id)
    if existing is None or _should_replace_location(existing, location):
        trace.locations_by_schedulable_id[schedulable_id] = location
    operation_id = _operation_id(schedule, schedulable_id)
    if operation_id is not None:
        existing_operation = trace.locations_by_operation_id.get(operation_id)
        if existing_operation is None or _should_replace_location(existing_operation, location):
            trace.locations_by_operation_id[operation_id] = location


def _record_schedule_constructor(
    trace: SourceTrace,
    *,
    schedule: Any,
    constructor_frame: FrameType,
) -> None:
    constructor_location = _location_from_frame(constructor_frame, label="schedule repetitions")
    frame = constructor_frame if constructor_location and _location_score(constructor_location) >= 2 else _external_caller_frame(constructor_frame.f_back)
    location = _location_from_frame(frame, label="schedule repetitions")
    if location is None:
        return
    trace.locations_by_schedule_id[id(schedule)] = location


def _looks_like_schedule(value: Any) -> bool:
    if value is None or not callable(getattr(value, "add", None)):
        return False
    value_type = type(value)
    type_name = value_type.__name__.lower()
    module_name = value_type.__module__.lower()
    if "schedule" not in type_name and ".schedule" not in module_name:
        return False
    return _safe_getattr(value, "schedulables") is not None


def _external_caller_frame(frame: FrameType | None) -> FrameType | None:
    fallback = frame.f_back if frame is not None else None
    candidate = fallback
    while candidate is not None:
        if _location_score(SourceTraceLocation(candidate.f_code.co_filename, 0, 0)) >= 2:
            return candidate
        candidate = candidate.f_back
    return fallback


def _should_replace_location(existing: SourceTraceLocation, candidate: SourceTraceLocation) -> bool:
    return _location_score(candidate) >= _location_score(existing)


def _location_score(location: SourceTraceLocation) -> int:
    normalized = location.file.replace("\\", "/").lower()
    if normalized.startswith("<"):
        return 0
    if "/site-packages/" in normalized:
        return 0
    if _STDLIB_PATH and (normalized == _STDLIB_PATH or normalized.startswith(f"{_STDLIB_PATH}/")):
        return 0
    if normalized.endswith("/qbstimeline/source_tracing.py"):
        return 1
    return 2


def _schedulable_id(schedule: Any, result: Any) -> str | None:
    schedulables = _safe_getattr(schedule, "schedulables")
    if isinstance(result, str):
        return result
    if isinstance(schedulables, dict) and schedulables:
        return str(next(reversed(schedulables)))
    return None


def _operation_id(schedule: Any, schedulable_id: str) -> str | None:
    schedulables = _safe_getattr(schedule, "schedulables")
    if not isinstance(schedulables, dict):
        return None
    schedulable = schedulables.get(schedulable_id)
    if isinstance(schedulable, dict):
        operation_id = schedulable.get("operation_id")
    else:
        operation_id = getattr(schedulable, "operation_id", None)
    return str(operation_id) if operation_id is not None else None


def _safe_getattr(value: Any, name: str) -> Any:
    try:
        return getattr(value, name, None)
    except Exception:
        return None


def _location_from_frame(frame: FrameType | None, *, label: Any) -> SourceTraceLocation | None:
    if frame is None:
        return None
    info = inspect.getframeinfo(frame)
    return SourceTraceLocation(
        file=info.filename,
        line=info.lineno,
        column=0 if info.positions is None else (info.positions.col_offset or 0),
        label=label if isinstance(label, str) else None,
    )
