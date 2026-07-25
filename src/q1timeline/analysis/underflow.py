from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from q1timeline.analysis.interpreter import AnalysisState
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


WAIT_LIKE_RT_OPS = {"wait", "wait_sync", "wait_trigger", "latch_rst", "set_latch_en"}


def analyze_underflow(
    state: AnalysisState,
    *,
    queue_depth_limit: int = 32,
) -> UnderflowResult:
    assumptions = {
        "queue_depth_limit": queue_depth_limit,
        "unknown_branches": "collapsed",
        "wait_trigger": "timeout_or_unknown",
        "alignment": "local_unaligned",
    }

    state.metadata["underflow"] = {
        "assumptions": assumptions,
        "status": "not_detected_under_current_assumptions",
    }

    status: UnderflowStatus = "not_detected_under_current_assumptions"
    for packet in state.rt_packets:
        if _packet_has_unknown_rt_timing(packet):
            _emit_analysis_incomplete(state, assumptions, packet=packet)
            _attach_loop_queue_slack_summaries(state)
            return UnderflowResult(status="analysis_incomplete", assumptions=assumptions)
        if packet.op in WAIT_LIKE_RT_OPS:
            continue
        if not isinstance(packet.rt_t0, Concrete) or not isinstance(packet.q1_issue_t1, Concrete):
            _emit_analysis_incomplete(state, assumptions, packet=packet)
            _attach_loop_queue_slack_summaries(state)
            return UnderflowResult(status="analysis_incomplete", assumptions=assumptions)

        slack = packet.rt_t0.value - packet.q1_issue_t1.value
        _emit_queue_depth(state, packet_id=packet.id, t0=packet.rt_t0, source=packet.source)
        _emit_slack(state, packet_id=packet.id, slack_ns=slack, t0=packet.rt_t0, source=packet.source)
        if slack < 0:
            status = _status_for_negative_slack(state, packet)
            _emit_underflow_warning(
                state,
                packet_id=packet.id,
                slack_ns=slack,
                t0=packet.rt_t0,
                source=packet.source,
                assumptions=assumptions,
                status=status,
            )
            break
        if slack == 0 and status != "definite_underflow":
            status = "possible_underflow"
            _emit_underflow_warning(
                state,
                packet_id=packet.id,
                slack_ns=slack,
                t0=packet.rt_t0,
                source=packet.source,
                assumptions=assumptions,
                status=status,
            )
            break

    state.metadata["underflow"]["status"] = status
    _attach_loop_queue_slack_summaries(state)
    return UnderflowResult(status=status, assumptions=assumptions)


def _packet_has_unknown_rt_timing(packet) -> bool:
    return packet.confidence == "unknown" or isinstance(packet.duration, Unknown)


def _emit_queue_depth(state: AnalysisState, *, packet_id: str, t0: Concrete, source) -> None:
    state.events.append(
        _event_like(
            state,
            lane="debug.queue_depth",
            kind="queue_depth",
            t0=t0,
            source=source,
            label="queue depth",
            meta={"rt_packet_id": packet_id, "estimated_depth": 0},
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


def _status_for_negative_slack(state: AnalysisState, packet) -> UnderflowStatus:
    if _has_sync_or_trigger_before_packet(state, packet.id):
        return "possible_underflow"
    return "definite_underflow"


def _has_sync_or_trigger_before_packet(state: AnalysisState, packet_id: str) -> bool:
    for packet in state.rt_packets:
        if packet.id == packet_id:
            return False
        if packet.op in {"wait_sync", "wait_trigger"}:
            return True
    return False


def _emit_analysis_incomplete(state: AnalysisState, assumptions: dict[str, object], *, packet=None) -> None:
    source = packet.source if packet is not None else state.rt_packets[0].source if state.rt_packets else None
    t0 = packet.rt_t0 if packet is not None and isinstance(packet.rt_t0, Concrete) else Concrete(0)
    meta: dict[str, Any] = {
        "status": "analysis_incomplete",
        "reason": "unknown_rt_timing",
        "assumptions": assumptions,
    }
    details: dict[str, Any] = {"assumptions": assumptions}
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
            message="Underflow analysis incomplete because RT timing is unknown.",
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
