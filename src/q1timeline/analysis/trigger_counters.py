"""Receiver schedules for execution and analytical queue replay."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from q1timeline.q1asm.trigger_counters import (
    TRIGGER_COUNTER_PARAMETERS, TriggerCounters, selected_addresses, trigger_counter_settings_valid,
)


@dataclass(frozen=True)
class TriggerSchedule:
    addresses: frozenset[int]
    arrivals: tuple[tuple[int, int, str], ...]
    settings: Mapping


@dataclass
class ScheduledTriggerCounters:
    schedule: TriggerSchedule
    counters: TriggerCounters = field(init=False)
    cursor: int = 0
    time_ns: int | None = None
    unknown_arrivals: bool = False
    counted_ids: dict[int, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.counters = TriggerCounters(self.schedule.settings)

    def advance(self, time_ns: int) -> None:
        if self.counters.enabled and self.time_ns is not None and time_ns > self.time_ns:
            self.unknown_arrivals = True
        self.time_ns = time_ns
        arrivals = self.schedule.arrivals
        while self.cursor < len(arrivals) and arrivals[self.cursor][0] <= time_ns:
            _, address, event_id = arrivals[self.cursor]
            self.cursor += 1
            if self.counters.enabled:
                self.counters.receive(address)
                self.counted_ids.setdefault(address, []).append(event_id)

    def uncertain_addresses(self) -> frozenset[int]:
        return frozenset(range(1, 16)) - self.schedule.addresses if self.unknown_arrivals else frozenset()

    def decide(self, mask: int, operator: int) -> bool | None:
        return self.counters.decide(mask, operator, dict.fromkeys(self.uncertain_addresses()))

    def apply(self, op: str, enable: int | None = None) -> None:
        if op == "set_latch_en":
            self.counters.enabled = bool(enable)
        elif op == "latch_rst":
            self.counters.counts.clear()
            self.counted_ids.clear()
            self.unknown_arrivals = False
