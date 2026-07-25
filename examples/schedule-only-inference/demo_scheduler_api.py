from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ScheduledOperation:
    label: str
    operation: Any
    abs_time: float


@dataclass
class Schedule:
    name: str
    entries: list[ScheduledOperation] = field(default_factory=list)

    def add(self, operation: Any, *, label: str, abs_time: float) -> None:
        self.entries.append(ScheduledOperation(label=label, operation=operation, abs_time=abs_time))


@dataclass(frozen=True)
class X180:
    qubit: str
    duration: Any
    amp: Any
    phase: float = 0.0


@dataclass(frozen=True)
class Measure:
    qubit: str
    pulse_duration: Any
    integration_duration: Any
    amp: float
    acq_channel: int
