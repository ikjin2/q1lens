from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

from q1timeline.analysis.interpreter import AnalysisState
from q1timeline.analysis.queue_timing import replay_queues
from q1timeline.analysis.values import Concrete, Unknown, subtract_values
from q1timeline.diagnostics import Diagnostic


UnderflowStatus = Literal[
    "definite_underflow",
    "possible_underflow",
    "not_detected_under_current_assumptions",
    "analysis_incomplete",
]


@dataclass(frozen=True)
class UnderflowResult:
    status: UnderflowStatus
    assumptions: dict[str, object]


def analyze_underflow(
    state: AnalysisState,
    *,
    queue_depth_limit: int = 32,
) -> UnderflowResult:
    return analyze_underflows([state], queue_depth_limit=queue_depth_limit)[0]


def analyze_underflows(
    states: list[AnalysisState],
    *,
    queue_depth_limit: int = 32,
) -> list[UnderflowResult]:
    """Compare earliest SYNQ release with maximum finite-FIFO prefetch.

    A failure in only the earliest case depends on unknown SYNQ dwell, not a
    proven empty queue. Original Q1/RT coordinates remain presentation data.
    """
    prepared = {state.sequencer_id: _queue_packets(state) for state in states}
    streams = {}
    for name, (packets, reason, boundary_packet) in prepared.items():
        streams[name] = list(packets)
        # A truncated trace is not a completed Q1 program: in particular it
        # cannot authorize an optimistic SYNQ release as if prefetch finished.
        boundary = boundary_packet or (packets[-1] if packets else None)
        if reason and boundary is not None:
            streams[name].append(replace(boundary, duration=Unknown(reason)))
    earliest = replay_queues(streams, queue_depth_limit=queue_depth_limit)
    prefetched = replay_queues(streams, queue_depth_limit=queue_depth_limit, prefill_sync=True)
    results = []
    assumptions = {
        "queue_depth_limit": queue_depth_limit,
        "startup": "first_packet_enqueue",
        "same_time_order": "enqueue_before_dequeue",
        "sync": "all_supplied_sequencers; earliest_release_to_maximum_prefetch",
        "unknown_branches": "incomplete",
        "wait_trigger": "external_arrival_unknown",
        "alignment": "queue_model_separate_from_display_coordinates",
    }
    for state in states:
        name = state.sequencer_id
        low, high = earliest[name], prefetched[name]
        _packets, reason, boundary_packet = prepared[name]
        failure = high.failure if low.failure and high.failure else None
        replay = high if failure else low
        if failure:
            status = "definite_underflow"
        elif low.failure:
            status = "analysis_incomplete"
            reason = reason or high.incomplete_reason or "sync_start_uncertain"
            boundary_packet = boundary_packet or low.failure.packet
        elif reason or low.incomplete_reason or high.incomplete_reason:
            status = "analysis_incomplete"
            reason = reason or low.incomplete_reason or high.incomplete_reason
        else:
            status = "not_detected_under_current_assumptions"
        state.metadata["underflow"] = {"assumptions": assumptions, "status": status}
        for record in replay.records:
            packet = record.packet
            _emit_queue_depth(state, packet_id=packet.id, t0=packet.rt_t0,
                              source=packet.source, depth=record.depth)
            _emit_slack(state, packet_id=packet.id, slack_ns=record.dequeue_ns-record.enqueue_ns,
                        t0=packet.rt_t0, source=packet.source)
            state.events[-1].meta.update(enqueue_ns=record.enqueue_ns, dequeue_ns=record.dequeue_ns)
        if failure:
            packet = failure.packet
            _emit_queue_depth(state, packet_id=packet.id, t0=packet.rt_t0, source=packet.source, depth=0)
            _emit_slack(state, packet_id=packet.id, slack_ns=failure.slack_ns, t0=packet.rt_t0, source=packet.source)
            _emit_underflow_warning(state, packet_id=packet.id, slack_ns=failure.slack_ns,
                                    t0=packet.rt_t0, source=packet.source, assumptions=assumptions, status=status)
        elif status == "analysis_incomplete":
            _emit_analysis_incomplete(state, assumptions, packet=boundary_packet, reason=reason)
        _attach_loop_queue_slack_summaries(state)
        results.append(UnderflowResult(status=status, assumptions=assumptions))
    return results


def _queue_packets(state: AnalysisState):
    """Expand constant loop timing; stop before an unmodeled timing boundary."""
    packets = list(state.rt_packets)
    if not packets:
        return [], None, None
    cutoff = None
    reason = None
    for event in state.events:
        if event.kind != "q1_issue" or not isinstance(event.t0, Concrete):
            continue
        boundary_reason = None
        if event.meta.get("op") in {"fb_pop_data", "fb_pull_data"}:
            boundary_reason = "feedback_arrival_unknown"
        elif event.meta.get("branch_decision") == "runtime_dependent":
            boundary_reason = "runtime_branch_unknown"
        if boundary_reason and (cutoff is None or event.t0.value < cutoff):
            cutoff, reason = event.t0.value, boundary_reason
    events_by_id = {event.id: event for event in state.events}
    for loop in (event for event in state.events if event.kind == "loop_block"):
        meta = loop.meta
        body = [events_by_id[event_id] for event_id in meta.get("first_iteration_event_ids", [])
                if event_id in events_by_id]
        q1 = [event for event in body if event.kind == "q1_issue"]
        if not q1 or not all(isinstance(event.t0, Concrete) and isinstance(event.t1, Concrete) for event in q1):
            continue
        start = min(event.t0.value for event in q1)
        if cutoff is not None and start >= cutoff:
            continue
        packet_ids = {event.meta.get("rt_packet_id") for event in body}
        loop_packets = [packet for packet in packets if packet.id in packet_ids]
        count, period = meta.get("count"), meta.get("period", {}).get("value")
        instructions = [instruction for pc, instruction in state.instructions_by_pc.items()
                        if meta.get("start_pc", -1) <= pc < meta.get("end_pc", -1)]
        # Register-dependent waits or internal control flow can change the next
        # iteration's timing. UI preview copies are not evidence of execution.
        repeatable = all(instruction.op in {"wait", "upd_param", "play", "nop", "set_mrk",
                         "set_awg_gain", "set_awg_offs", "set_freq", "reset_ph", "set_ph", "set_ph_delta"}
                         and all(arg.kind != "reg" for arg in instruction.args) for instruction in instructions)
        if (not isinstance(count, int) or count < 1 or not isinstance(period, int) or
                not repeatable or any(event.kind == "loop_block" for event in body) or
                len(packets) + len(loop_packets) * (count-1) > 10000):
            cutoff, reason = start, "compact_loop_timing_unexpanded"
            continue
        q1_period = max(event.t1.value for event in q1)-start + meta.get("loop_q1_jump_runtime_ns", 24)
        for iteration in range(1, count):
            for packet in loop_packets:
                if not all(isinstance(value, Concrete) for value in
                           (packet.q1_issue_t0, packet.q1_issue_t1, packet.rt_t0, packet.rt_t1)):
                    cutoff, reason = start, "unknown_rt_timing"
                    break
                packets.append(replace(packet, id=f"{packet.id}:iteration{iteration}",
                    q1_issue_t0=Concrete(packet.q1_issue_t0.value+iteration*q1_period),
                    q1_issue_t1=Concrete(packet.q1_issue_t1.value+iteration*q1_period),
                    rt_t0=Concrete(packet.rt_t0.value+iteration*period),
                    rt_t1=Concrete(packet.rt_t1.value+iteration*period)))
    packets.sort(key=lambda packet: packet.q1_issue_t0.value if isinstance(packet.q1_issue_t0, Concrete) else float("inf"))
    known = []
    for packet in packets:
        if cutoff is not None and (not isinstance(packet.q1_issue_t0, Concrete) or packet.q1_issue_t0.value >= cutoff):
            return known, reason, packet
        if packet.op == "wait_trigger":
            return known, "trigger_arrival_unknown", packet
        if _packet_has_unknown_rt_timing(packet) or not all(isinstance(value, Concrete) for value in
                (packet.q1_issue_t0, packet.q1_issue_t1, packet.rt_t0, packet.rt_t1)):
            return known, "unknown_rt_timing", packet
        if known and packet.rt_t0.value != known[-1].rt_t1.value:
            return known, "unrepresented_rt_commands", packet
        known.append(packet)
    return known, reason, None


def _packet_has_unknown_rt_timing(packet) -> bool:
    return packet.confidence == "unknown" or isinstance(packet.duration, Unknown)


def _emit_queue_depth(state: AnalysisState, *, packet_id: str, t0: Concrete, source, depth: int) -> None:
    state.events.append(
        _event_like(
            state,
            lane="debug.queue_depth",
            kind="queue_depth",
            t0=t0,
            source=source,
            label="queue depth",
            meta={"rt_packet_id": packet_id, "estimated_depth": depth},
        )
    )


def _emit_slack(state: AnalysisState, *, packet_id: str, slack_ns: int, t0: Concrete, source) -> None:
    state.events.append(
        _event_like(
            state,
            lane="debug.slack",
            kind="slack",
            t0=t0,
            source=source,
            label=f"slack {slack_ns} ns",
            meta={"rt_packet_id": packet_id, "slack_ns": slack_ns},
        )
    )


def _emit_underflow_warning(
    state: AnalysisState,
    *,
    packet_id: str,
    slack_ns: int,
    t0: Concrete,
    source,
    assumptions: dict[str, object],
    status: UnderflowStatus,
) -> None:
    loop_context = _loop_context_for_packet(state, packet_id)
    meta: dict[str, Any] = {
        "status": status,
        "rt_packet_id": packet_id,
        "slack_ns": slack_ns,
        "assumptions": assumptions,
    }
    details: dict[str, Any] = {
        "rt_packet_id": packet_id,
        "slack_ns": slack_ns,
        "assumptions": assumptions,
    }
    if loop_context is not None:
        meta["loop_context"] = loop_context
        details["loop_context"] = loop_context
    event = _event_like(
        state,
        lane="debug.underflow",
        kind="underflow_warning",
        t0=t0,
        source=source,
        label=status,
        meta=meta,
    )
    state.events.append(event)
    state.diagnostics.append(
        Diagnostic(
            severity="warning",
            category=status,
            message=f"{status}: slack = {slack_ns} ns.",
            source=source,
            related_events=[event.id],
            details=details,
        )
    )


def _emit_analysis_incomplete(state: AnalysisState, assumptions: dict[str, object], *, packet=None, reason="unknown_rt_timing") -> None:
    source = packet.source if packet is not None else state.rt_packets[0].source if state.rt_packets else None
    t0 = packet.rt_t0 if packet is not None and isinstance(packet.rt_t0, Concrete) else Concrete(0)
    meta: dict[str, Any] = {
        "status": "analysis_incomplete",
        "reason": reason,
        "assumptions": assumptions,
    }
    details: dict[str, Any] = {"assumptions": assumptions, "reason": reason}
    if reason in {"sync_start_uncertain", "feedback_arrival_unknown", "runtime_branch_unknown", "compact_loop_timing_unexpanded"}:
        # Queue replay may stop where the event dependency graph remains usable.
        # Unknown RT timing, triggers, and unmatched barriers remain execution uncertainty.
        meta["analysis_scope"] = details["analysis_scope"] = "rt_queue"
    if packet is not None:
        meta["rt_packet_id"] = packet.id
        details["rt_packet_id"] = packet.id
    event = _event_like(
        state,
        lane="debug.underflow",
        kind="analysis_incomplete",
        t0=t0,
        source=source,
        label="analysis incomplete",
        meta=meta,
    )
    state.events.append(event)
    state.metadata["underflow"]["status"] = "analysis_incomplete"
    state.diagnostics.append(
        Diagnostic(
            severity="warning",
            category="analysis_incomplete",
            message=("Underflow depends on the unknown synchronization/startup delay; the earliest-release queue can starve, but a prefetched queue does not."
                     if reason == "sync_start_uncertain" else f"Underflow analysis incomplete: {reason.replace('_', ' ')}."),
            source=source,
            related_events=[event.id],
            details=details,
        )
    )


def _attach_loop_queue_slack_summaries(state: AnalysisState) -> None:
    events_by_id = {event.id: event for event in state.events}
    for preview in [event for event in state.events if event.kind == "loop_iteration_preview"]:
        packet_ids = _preview_packet_ids(preview.meta.get("event_ids", []), events_by_id)
        queue_events = _events_for_packets(state, kind="queue_depth", packet_ids=packet_ids)
        slack_events = _events_for_packets(state, kind="slack", packet_ids=packet_ids)
        warning_events = _events_for_packets(state, kind="underflow_warning", packet_ids=packet_ids)
        slack_values = [
            event.meta["slack_ns"]
            for event in slack_events
            if isinstance(event.meta.get("slack_ns"), int)
        ]
        queue_depths = [
            event.meta["estimated_depth"]
            for event in queue_events
            if isinstance(event.meta.get("estimated_depth"), int)
        ]
        preview.meta["queue_slack_summary"] = {
            "rt_packet_ids": packet_ids,
            "queue_depth_event_ids": [event.id for event in queue_events],
            "slack_event_ids": [event.id for event in slack_events],
            "status": _preview_underflow_status(state, warning_events, slack_values),
            "min_slack_ns": min(slack_values) if slack_values else None,
            "max_queue_depth": max(queue_depths) if queue_depths else None,
        }


def _loop_context_for_packet(state: AnalysisState, packet_id: str) -> dict[str, Any] | None:
    events_by_id = {event.id: event for event in state.events}
    for preview in [event for event in state.events if event.kind == "loop_iteration_preview"]:
        for event_id in preview.meta.get("event_ids", []):
            event = events_by_id.get(event_id)
            if event is None or event.kind == "q1_issue":
                continue
            if event.meta.get("rt_packet_id") == packet_id:
                return {
                    "loop_id": preview.meta.get("loop_id"),
                    "iteration_index": preview.meta.get("iteration_index"),
                    "preview_event_id": preview.id,
                }
    return None


def _preview_packet_ids(event_ids: list[str], events_by_id: dict[str, Any]) -> list[str]:
    packet_ids: list[str] = []
    for event_id in event_ids:
        event = events_by_id.get(event_id)
        if event is None or event.kind in {"q1_issue", "wait"}:
            continue
        packet_id = event.meta.get("rt_packet_id")
        if isinstance(packet_id, str) and packet_id not in packet_ids:
            packet_ids.append(packet_id)
    return packet_ids


def _events_for_packets(state: AnalysisState, *, kind: str, packet_ids: list[str]):
    packet_id_set = set(packet_ids)
    return [
        event
        for event in state.events
        if event.kind == kind and event.meta.get("rt_packet_id") in packet_id_set
    ]


def _preview_underflow_status(
    state: AnalysisState,
    warning_events,
    slack_values: list[int],
) -> UnderflowStatus:
    for event in warning_events:
        status = event.meta.get("status")
        if status in {
            "definite_underflow",
            "possible_underflow",
            "not_detected_under_current_assumptions",
            "analysis_incomplete",
        }:
            return status
    if slack_values:
        return "not_detected_under_current_assumptions"
    if state.metadata.get("underflow", {}).get("status") == "analysis_incomplete":
        return "analysis_incomplete"
    return "not_detected_under_current_assumptions"


def _event_like(state: AnalysisState, *, lane: str, kind: str, t0: Concrete, source, label: str, meta: dict):
    from q1timeline.analysis.interpreter import TimelineEvent

    return TimelineEvent(
        id=f"{state.sequencer_id}:e{len(state.events)}",
        sequencer_id=state.sequencer_id,
        lane=lane,
        kind=kind,
        t0=t0,
        t1=t0,
        duration=subtract_values(t0, t0),
        label=label,
        confidence="exact" if kind != "analysis_incomplete" else "unknown",
        source=source,
        meta=meta,
    )
