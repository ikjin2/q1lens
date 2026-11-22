"""Single-sequencer RT FIFO with a fixed launch or the simulator's buffered launch."""
from __future__ import annotations

from dataclasses import dataclass, replace

import z3

from q1timeline.analysis.queue_timing import POSSIBLE_INITIAL_BUFFER_NS
from q1timeline.q1asm.core import RT_FIFO_CAPACITY


def _later(a, b):
    return max(a, b) if isinstance(a, int) and isinstance(b, int) else z3.If(a >= b, a, b)


def _spacing(dwell):
    return (dwell or 4) if isinstance(dwell, int) else z3.If(dwell == 0, 4, dwell)


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
            return replace(self, q1=end), failed, self.next_dequeue
        start = _later(end, self.next_dequeue)
        # After a zero-dwell command an empty FIFO waits for its next packet.
        queue = FixedStartQueue(end, start + _spacing(dwell), dwell != 0, releases + (start,))
        return queue, failed, self.next_dequeue


@dataclass(frozen=True)
class BufferedQueue:
    q1: int = 0
    first_enqueue: int | None = None
    dwells: tuple = ()

    def _launch(self, start):
        next_dequeue = start
        releases = []
        for dwell in self.dwells:
            releases.append(next_dequeue)
            next_dequeue += _spacing(dwell)
        return FixedStartQueue(self.q1, next_dequeue, self.dwells[-1] != 0, tuple(releases))

    def issue(self, cost, *, dwell=None, stop=False):
        # Before launch the Q1 clock advances solely by the cost of each
        # concrete instruction/branch on this path. Dwell can be symbolic.
        end = self.q1 + cost
        if self.first_enqueue is not None:
            deadline = self.first_enqueue + POSSIBLE_INITIAL_BUFFER_NS
            if end >= deadline:
                return self._launch(deadline).issue(cost, dwell=dwell, stop=stop)
        if dwell is None:
            # A stop before launch lets the prefilled queue drain to completion.
            return replace(self, q1=end), False, None
        first = self.first_enqueue if self.first_enqueue is not None else end
        pending = BufferedQueue(end, first, self.dwells + (dwell,))
        if len(pending.dwells) == RT_FIFO_CAPACITY:
            return pending._launch(end), False, None
        return pending, False, None
