from __future__ import annotations

import sysconfig
from pathlib import Path
from types import SimpleNamespace

from qbstimeline.source_tracing import (
    SourceTraceLocation,
    _should_replace_location,
    traced_schedule_adds,
)


class FakeSchedule:
    def __init__(self) -> None:
        self.schedulables = {}

    def add(self, operation, *, label=None):
        key = label or f"op{len(self.schedulables)}"
        self.schedulables[key] = {"operation_id": operation.name}
        return key


class UntimedSchedule:
    def add(self, operation, *, label=None):
        return label

    @property
    def schedulables(self):
        raise RuntimeError("`schedulables` dict unavailable on schedule with untimed operations")


def test_traced_schedule_adds_records_schedulable_source() -> None:
    schedule = FakeSchedule()

    with traced_schedule_adds() as trace:
        schedule.add(SimpleNamespace(name="measure"), label="measure")

    location = trace.locations_by_schedulable_id["measure"]

    assert location.file.endswith("test_source_tracing.py")
    assert location.line > 0
    assert location.label == "measure"


def test_traced_schedule_adds_records_schedule_constructor_source() -> None:
    with traced_schedule_adds() as trace:
        schedule = FakeSchedule()

    location = trace.locations_by_schedule_id[id(schedule)]

    assert location.file.endswith("test_source_tracing.py")
    assert location.line > 0


def test_traced_schedule_adds_handles_unavailable_schedulables() -> None:
    schedule = UntimedSchedule()

    with traced_schedule_adds() as trace:
        schedule.add(SimpleNamespace(name="measure"), label="measure")

    assert trace.locations_by_schedulable_id["measure"].label == "measure"
    assert trace.locations_by_operation_id == {}


def test_trace_locations_prefer_user_code_over_library_frames() -> None:
    user_location = SourceTraceLocation(file=r"C:\repo\.scratch\probe\notebook_cells.py", line=10, column=0)
    library_location = SourceTraceLocation(
        file=r"C:\Python312\site-packages\qblox_scheduler\schedule.py",
        line=674,
        column=0,
    )

    assert _should_replace_location(library_location, user_location) is True
    assert _should_replace_location(user_location, library_location) is False


def test_trace_locations_prefer_user_code_over_stdlib_frames() -> None:
    user_location = SourceTraceLocation(file=r"C:\repo\.scratch\probe\notebook_cells.py", line=10, column=0)
    stdlib_location = SourceTraceLocation(
        file=str(Path(sysconfig.get_paths()["stdlib"]) / "contextlib.py"),
        line=144,
        column=0,
    )

    assert _should_replace_location(stdlib_location, user_location) is True
    assert _should_replace_location(user_location, stdlib_location) is False
