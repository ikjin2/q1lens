"""Fixed-start, single-sequencer FIFO for the symbolic local instruction model."""
from __future__ import annotations

from dataclasses import dataclass, replace

import z3

from q1timeline.q1asm.core import RT_FIFO_CAPACITY


def _later(a, b):
    return max(a, b) if isinstance(a, int) and isinstance(b, int) else z3.If(a >= b, a, b)


@dataclass(frozen=True)
class FixedStartQueue:
    q1: int | z3.ArithRef
    next_dequeue: int | z3.ArithRef
    guarded: bool | z3.BoolRef = True
    slot_releases: tuple = ()

    @classmethod
    def start(cls, startup_ns: int) -> FixedStartQueue:
        return cls(0, startup_ns)

    def issue(self, cost, *, dwell=None, stop=False):
        # Simulation reserves a FIFO slot before fetching a packet. Classical
        # issues and stop continue independently of those reservations.
        begin = self.q1
        releases = self.slot_releases
        if dwell is not None and len(releases) == RT_FIFO_CAPACITY:
            begin = _later(begin, releases[0])
            releases = releases[1:]
        end = begin + cost
        # At an exact tie, enqueue/stop completes before the RT dequeue.
        late = end > self.next_dequeue if dwell is not None or stop else end >= self.next_dequeue
        failed = z3.And(self.guarded, late)
        if dwell is None:
            return replace(self, q1=end), failed
        start = _later(end, self.next_dequeue)
        # After a zero-dwell command an empty FIFO waits for its next packet.
        spacing = (dwell or 4) if isinstance(dwell, int) else z3.If(dwell == 0, 4, dwell)
        return FixedStartQueue(end, start + spacing, dwell != 0, releases + (start,)), failed
