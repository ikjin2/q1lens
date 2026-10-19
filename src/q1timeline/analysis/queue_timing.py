"""Replay packet queue timing without changing analytical source coordinates."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import heapq
from itertools import count

from q1timeline.analysis.interpreter import RtPacket
from q1timeline.analysis.values import Concrete


@dataclass(frozen=True)
class QueueTimingRecord:
    packet: RtPacket
    enqueue_ns: int
    dequeue_ns: int
    depth: int  # FIFO depth after this packet is dequeued.


@dataclass(frozen=True)
class QueueTimingFailure:
    packet: RtPacket
    enqueue_ns: int
    dequeue_ns: int
    slack_ns: int


@dataclass
class QueueReplayResult:
    records: list[QueueTimingRecord] = field(default_factory=list)
    failure: QueueTimingFailure | None = None
    incomplete_reason: str | None = None


@dataclass
class _Queue:
    packets: list[RtPacket]
    result: QueueReplayResult
    pending: deque = field(default_factory=deque)
    index: int = 0
    ready_ns: int | None = None
    blocked: bool = False
    rt_started: bool = False
    rt_waiting: bool = False
    underflow_suspended: bool = False
    sync: RtPacket | None = None


def replay_queues(
    streams: dict[str, list[RtPacket]],
    *,
    queue_depth_limit: int = 32,
    prefill_sync: bool = False,
) -> dict[str, QueueReplayResult]:
    """Replay earliest startup, optionally holding sync for maximal prefetch.

    When any stream uses wait_sync, all supplied streams participate. Each
    barrier matches their next wait_sync; a missing round is incomplete. This bounded packet replay
    does not infer trigger arrivals, omitted loop iterations, or terminal stops.
    Callers must supply a complete, deterministic packet prefix.
    """
    if isinstance(queue_depth_limit, bool) or not isinstance(queue_depth_limit, int) or queue_depth_limit < 1:
        raise ValueError("queue_depth_limit must be a positive integer")
    results = {name: QueueReplayResult() for name in streams}
    queues: dict[str, _Queue] = {}
    participants = set(streams) if any(packet.op == "wait_sync" for packets in streams.values() for packet in packets) else set()
    for name, packets in streams.items():
        prefix: list[RtPacket] = []
        previous_end = None
        for packet in packets:
            times = (packet.q1_issue_t0, packet.q1_issue_t1, packet.duration)
            if not all(isinstance(value, Concrete) and type(value.value) is int for value in times):
                results[name].incomplete_reason = f"Unknown packet timing at {packet.id}."
                break
            start, end, duration = (value.value for value in times)
            if end <= start or duration < 0 or (previous_end is not None and start < previous_end):
                results[name].incomplete_reason = f"Invalid packet timing at {packet.id}."
                break
            if packet.op == "wait_trigger":
                results[name].incomplete_reason = f"External trigger timing is unknown at {packet.id}."
                break
            prefix.append(packet)
            previous_end = end
        queues[name] = _Queue(prefix, results[name])

    events: list[tuple[int, int, int, str, str]] = []
    order = count()

    def schedule(time: int, phase: int, name: str, action: str) -> None:
        heapq.heappush(events, (time, phase, next(order), name, action))

    def issue_at(name: str, time: int) -> None:
        queue = queues[name]
        packet = queue.packets[queue.index]
        queue.blocked = False
        queue.ready_ns = time + packet.q1_issue_t1.value - packet.q1_issue_t0.value
        schedule(time, 2, name, "issue")

    def release_sync(time: int) -> None:
        if not participants or not all(queues[name].sync is not None for name in participants):
            return
        if prefill_sync and not all(
            queues[name].blocked or (
                queues[name].index == len(queues[name].packets) and queues[name].result.incomplete_reason is None
            )
            for name in participants
        ):
            return
        for name in sorted(participants):
            queue = queues[name]
            duration = queue.sync.duration.value
            queue.sync = None
            schedule(time + (duration or 4), 1, name, "rt")

    for name, queue in queues.items():
        if queue.packets:
            issue_at(name, queue.packets[0].q1_issue_t0.value)

    while events:
        time, _, _, name, action = heapq.heappop(events)
        queue = queues[name]
        if queue.result.failure is not None:
            continue
        if action == "issue":
            if len(queue.pending) >= queue_depth_limit:
                queue.blocked = True
                queue.ready_ns = None
            else:
                queue.blocked = False
                schedule(queue.ready_ns, 0, name, "enqueue")
        elif action == "enqueue":
            packet = queue.packets[queue.index]
            queue.pending.append((packet, time))
            queue.index += 1
            queue.ready_ns = None
            if queue.index < len(queue.packets):
                gap = queue.packets[queue.index].q1_issue_t0.value - packet.q1_issue_t1.value
                issue_at(name, time + gap)
            if not queue.rt_started or queue.rt_waiting:
                queue.rt_started = True
                queue.rt_waiting = False
                schedule(time, 1, name, "rt")
        else:
            if not queue.pending:
                if queue.underflow_suspended:
                    queue.rt_waiting = True
                elif queue.index < len(queue.packets):
                    packet = queue.packets[queue.index]
                    queue.result.failure = QueueTimingFailure(packet, queue.ready_ns, time, time - queue.ready_ns)
                continue
            packet, enqueued = queue.pending.popleft()
            queue.result.records.append(QueueTimingRecord(packet, enqueued, time, len(queue.pending)))
            # A zero duration suspends the guard, but the next pop is still
            # attempted after 4 ns. Any nonzero duration restores the guard.
            queue.underflow_suspended = packet.duration.value == 0
            if queue.blocked:
                issue_at(name, time)
            if packet.op == "wait_sync":
                queue.sync = packet
            else:
                schedule(time + (packet.duration.value or 4), 1, name, "rt")
        release_sync(time)

    for name, queue in queues.items():
        if queue.sync is not None:
            missing = ", ".join(sorted(peer for peer in participants if queues[peer].sync is None))
            reason = (f"wait_sync {queue.sync.id} has no matching reachable barrier for: {missing}." if missing
                      else f"Maximal prefetch before wait_sync {queue.sync.id} is unknown.")
            queue.result.incomplete_reason = queue.result.incomplete_reason or reason
    return results
