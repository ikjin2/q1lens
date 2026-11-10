"""Shared trigger-counter rules for execution and analytical queue replay."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import re


_ADDRESSES = range(1, 16)
_THRESHOLDS = {f"trigger{address}_count_threshold" for address in _ADDRESSES}
_INVERSIONS = {f"trigger{address}_threshold_invert" for address in _ADDRESSES}
TRIGGER_COUNTER_PARAMETERS = frozenset(_THRESHOLDS | _INVERSIONS)
_COUNTER_PARAMETER = re.compile(r"trigger\d+_(?:count_threshold|threshold_invert)")


def trigger_counter_settings_valid(settings: Mapping) -> bool:
    return all(
        (type(value) is int and 0 <= value <= 65535) if key in _THRESHOLDS else
        type(value) is bool if key in _INVERSIONS else
        not (isinstance(key, str) and _COUNTER_PARAMETER.fullmatch(key))
        for key, value in settings.items()
    )


def selected_addresses(mask: int) -> list[int]:
    return [address for address in _ADDRESSES if mask & (1 << (address - 1))]


@dataclass
class TriggerCounters:
    settings: Mapping = field(default_factory=dict)
    counts: dict[int, int] = field(default_factory=dict)
    enabled: bool = False

    def __post_init__(self) -> None:
        if not trigger_counter_settings_valid(self.settings):
            raise ValueError("Trigger counter settings require addresses 1..15, thresholds 0..65535, and boolean inversions.")

    def receive(self, address: int) -> None:
        if self.enabled:
            self.counts[address] = self.counts.get(address, 0) + 1

    def decide(self, mask: int, operator: int, possible_counts: Mapping[int, int | None] | None = None) -> bool | None:
        """Evaluate a condition using definite counts and possible extra counts. None means unbounded."""
        possible_counts = possible_counts or {}
        values: list[bool | None] = []
        for address in selected_addresses(mask):
            count = self.counts.get(address, 0)
            threshold = self.settings.get(f"trigger{address}_count_threshold", 1)
            inverted = self.settings.get(f"trigger{address}_threshold_invert", False)
            extra = possible_counts.get(address, 0)
            if count < threshold and (extra is None or count + extra >= threshold):
                values.append(None)
            else:
                values.append((count >= threshold) != inverted)
        if operator in (0, 1):
            result = True if True in values else None if None in values else False
        elif operator in (2, 3):
            result = False if False in values else None if None in values else True
        else:
            result = None if None in values else values.count(True) % 2 == 1
        return not result if result is not None and operator in (1, 3, 5) else result


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
        return frozenset(_ADDRESSES) - self.schedule.addresses if self.unknown_arrivals else frozenset()

    def decide(self, mask: int, operator: int) -> bool | None:
        return self.counters.decide(mask, operator, dict.fromkeys(self.uncertain_addresses()))

    def apply(self, op: str, enable: int | None = None) -> None:
        if op == "set_latch_en":
            self.counters.enabled = bool(enable)
        elif op == "latch_rst":
            self.counters.counts.clear()
            self.counted_ids.clear()
            self.unknown_arrivals = False
