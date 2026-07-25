from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from q1timeline.analysis.interpreter import AnalysisState, TimelineEvent
from q1timeline.analysis.values import value_to_json
from q1timeline.diagnostics import Diagnostic
from q1timeline.ir.control_flow_graph import control_flow_graph_from_states
from q1timeline.q1asm.ast import SourceLocation


TIMELINE_IR_SCHEMA_VERSION = "0.1.0"
LINQ_FEEDBACK_LATENCY_SOURCE = (
    "https://docs.qblox.com/en/main/products/architecture/sequencers/"
    "linq_based_feedback.html#latencies-of-linq-based-feedback-data-types"
)
LINQ_FEEDBACK_ROUTE_SOURCE = (
    "https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QRM/"
    "422_linq_acquisition.html#sharing-measurement-results-with-linq-based-feedback"
)
LINQ_FEEDBACK_LATENCIES_NS = {
    "thresholded_bits": {
        "self_cast": 160,
        "intra_cast": 250,
        "multi_cast_worst_case": 472,
    },
    "iq_values": {
        "self_cast": 164,
        "intra_cast": 270,
        "multi_cast_worst_case": 492,
    },
    "high_resolution_time_tags": {
        "self_cast": 910,
        "intra_cast": 1000,
        "multi_cast_worst_case": 1260,
    },
    "time_delta": {
        "self_cast": 910,
        "intra_cast": 1000,
        "multi_cast_worst_case": 1260,
    },
    "ttl_counts": {
        "self_cast": 146,
        "intra_cast": 236,
        "multi_cast_worst_case": 480,
    },
    "low_latency_time_tags": {
        "self_cast": 146,
        "intra_cast": 236,
        "multi_cast_worst_case": 480,
    },
    "q1_register_or_immediate": {
        "self_cast": 60,
        "intra_cast": 150,
        "multi_cast_worst_case": 380,
    },
}

REQUIRED_EVENT_KINDS = {
    "wait",
    "wait_sync",
    "wait_trigger",
    "play",
    "acquire",
    "upd_param",
    "marker_state",
    "latched_state_pending",
    "latched_state_applied",
    "feedback_pop",
    "feedback_com",
    "q1_issue",
    "loop_block",
    "loop_iteration_preview",
    "branch_region",
    "unknown_region",
    "underflow_warning",
    "queue_depth",
    "slack",
    "stop",
}


def timeline_ir_from_states(
    states: list[AnalysisState],
    *,
    project: dict[str, Any] | None = None,
    diagnostics: list[Diagnostic] | None = None,
) -> dict[str, Any]:
    _validate_unique_sequencer_ids(states)
    events = [_event_to_dict(event) for state in states for event in state.events]
    input_diagnostics = (
        list(diagnostics)
        if diagnostics is not None
        else [diagnostic for state in states for diagnostic in state.diagnostics]
    )
    source_map = _source_map(events)
    feedback_flows = _feedback_flows(events)
    feedback_balance = _feedback_balance(events, feedback_flows)
    feedback_route_diagnostics = _feedback_route_mismatch_diagnostics(events, feedback_flows)
    feedback_latency_diagnostics = _feedback_latency_diagnostics(events, feedback_flows)
    feedback_balance_diagnostics = _feedback_balance_diagnostics(feedback_balance)
    generated_diagnostics = [
        *feedback_route_diagnostics,
        *feedback_latency_diagnostics,
        *feedback_balance_diagnostics,
    ]
    if diagnostics is not None:
        diagnostics.extend(generated_diagnostics)
    serialized_diagnostics = [
        _diagnostic_to_dict(diagnostic)
        for diagnostic in [*input_diagnostics, *generated_diagnostics]
    ]
    return {
        "version": TIMELINE_IR_SCHEMA_VERSION,
        "project": _jsonable(project or {}),
        "analysis": {
            "event_kinds": sorted(REQUIRED_EVENT_KINDS | {event["kind"] for event in events}),
        },
        "sequencers": [{"id": state.sequencer_id} for state in states],
        "events": events,
        "diagnostics": serialized_diagnostics,
        "source_map": source_map,
        "feedback_flows": feedback_flows,
        "feedback_balance": feedback_balance,
        "control_flow_graph": control_flow_graph_from_states(states),
        "alignment": {
            state.sequencer_id: _jsonable(state.metadata.get("alignment", {}))
            for state in states
        },
        "assumptions": _assumptions(states),
    }


def _validate_unique_sequencer_ids(states: list[AnalysisState]) -> None:
    seen: set[str] = set()
    for state in states:
        if state.sequencer_id in seen:
            raise ValueError(f"Duplicate sequencer_id in TimelineIR states: {state.sequencer_id}")
        seen.add(state.sequencer_id)


def timeline_ir_to_json(ir: dict[str, Any]) -> str:
    return json.dumps(ir, indent=2, sort_keys=True, allow_nan=False) + "\n"


def write_timeline_ir(ir: dict[str, Any], path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(timeline_ir_to_json(ir), encoding="utf-8")


def diagnostics_to_json(diagnostics: list[Diagnostic]) -> str:
    return json.dumps([_diagnostic_to_dict(diagnostic) for diagnostic in diagnostics], indent=2, sort_keys=True, allow_nan=False) + "\n"


def _event_to_dict(event: TimelineEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "sequencer_id": event.sequencer_id,
        "lane": event.lane,
        "kind": event.kind,
        "t0": value_to_json(event.t0),
        "t1": value_to_json(event.t1) if event.t1 is not None else None,
        "duration": value_to_json(event.duration),
        "label": event.label,
        "confidence": event.confidence,
        "source": _source_to_dict(event.source),
        "meta": _jsonable(event.meta),
    }


def _diagnostic_to_dict(diagnostic: Diagnostic) -> dict[str, Any]:
    return {
        "severity": diagnostic.severity,
        "category": diagnostic.category,
        "message": diagnostic.message,
        "source": _source_to_dict(diagnostic.source) if diagnostic.source is not None else None,
        "related_events": diagnostic.related_events,
        "details": _jsonable(diagnostic.details),
    }


def _source_map(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_event_id: dict[str, Any] = {}
    by_source: dict[str, list[str]] = {}
    for event in events:
        source = event["source"]
        by_event_id[event["id"]] = source
        for mapped_source in _source_map_sources(event):
            key = f"{mapped_source['file']}:{mapped_source['line']}"
            event_ids = by_source.setdefault(key, [])
            if event["id"] not in event_ids:
                event_ids.append(event["id"])
    return {"by_event_id": by_event_id, "by_source": by_source}


def _source_map_sources(event: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [event["source"]]
    meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
    for field in ("source_start", "source_end"):
        source = meta.get(field)
        if isinstance(source, dict) and "file" in source and "line" in source:
            sources.append(source)
    return sources


def _feedback_flows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending_sends_by_channel: dict[str, list[dict[str, Any]]] = {}
    flows: list[dict[str, Any]] = []
    send_order = 0
    feedback_entries = _feedback_entries(events)
    receive_channels_by_sequencer = _feedback_receive_channels_by_sequencer(feedback_entries)

    for entry in sorted(feedback_entries, key=_feedback_entry_sort_key):
        event = entry["event"]
        feedback = entry["feedback"]
        channel = entry["channel"]
        direction = entry["direction"]
        if direction == "send":
            pending_sends_by_channel.setdefault(channel, []).append(
                {
                    "event": event,
                    "remaining": _feedback_send_capacity(event),
                    "channel": channel,
                    "order": send_order,
                }
            )
            send_order += 1
            continue

        if entry.get("receive_mode") == "fifo":
            send_entry = _next_pending_fifo_feedback_send(pending_sends_by_channel)
        else:
            send_entry = _next_pending_feedback_send(pending_sends_by_channel.get(channel, []))
            if send_entry is not None:
                _discard_pending_feedback_before_order(
                    pending_sends_by_channel,
                    send_entry["order"],
                    receive_event=event,
                    receive_channels_by_sequencer=receive_channels_by_sequencer,
                )
        if send_entry is None:
            continue
        channel = str(send_entry.get("channel", channel))
        send_entry["remaining"] -= 1
        send = send_entry["event"]
        if send is None:
            continue
        send_feedback = send.get("meta", {}).get("feedback", {})
        source = str(send_feedback.get("source", "feedback"))
        target = str(feedback.get("target", "consumer"))
        flow_id = f"feedback-flow-{len(flows)}"
        flows.append(
            {
                "id": flow_id,
                "from_event_id": send["id"],
                "to_event_id": event["id"],
                "channel": channel,
                "source": source,
                "target": target,
                "label": f"feedback ch {channel}: {source} -> {target}",
            }
        )
    return flows


def _feedback_entries(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
        feedback = meta.get("feedback") if isinstance(meta.get("feedback"), dict) else None
        if not feedback:
            continue
        channel = str(feedback.get("channel", "default"))
        direction = feedback.get("direction")
        if direction not in {"send", "receive"}:
            continue
        entries.append(
            {
                "index": index,
                "event": event,
                "feedback": feedback,
                "channel": channel,
                "direction": direction,
                "receive_mode": feedback.get("receive_mode"),
            }
        )
    return entries


def _feedback_receive_channels_by_sequencer(entries: list[dict[str, Any]]) -> dict[str, set[str]]:
    channels_by_sequencer: dict[str, set[str]] = {}
    for entry in entries:
        if entry["direction"] != "receive":
            continue
        sequencer_id = str(entry["event"].get("sequencer_id", "unknown"))
        channels = channels_by_sequencer.setdefault(sequencer_id, set())
        if entry.get("receive_mode") == "fifo":
            channels.add("*")
        else:
            channels.add(entry["channel"])
    return channels_by_sequencer


def _feedback_balance(events: list[dict[str, Any]], flows: list[dict[str, Any]]) -> dict[str, Any]:
    channels: dict[str, dict[str, Any]] = {}
    discarded_payloads = _feedback_discarded_payloads(events)
    events_by_id = {event.get("id"): event for event in events}
    fifo_receives = 0
    fifo_matched = 0

    for event in events:
        meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
        feedback = meta.get("feedback") if isinstance(meta.get("feedback"), dict) else None
        if not feedback:
            continue
        direction = feedback.get("direction")
        if direction not in {"send", "receive"}:
            continue
        channel = str(feedback.get("channel", "default"))
        if direction == "receive" and feedback.get("receive_mode") == "fifo":
            fifo_receives += 1
            continue
        summary = _feedback_balance_channel(channels, channel)
        if direction == "send":
            summary["sends"] += 1
            summary["send_payloads"] += _feedback_send_capacity(event)
        else:
            summary["receives"] += 1

    for flow in flows:
        channel = str(flow.get("channel", "default"))
        summary = _feedback_balance_channel(channels, channel)
        summary["matched"] += 1
        receive = events_by_id.get(flow.get("to_event_id"))
        receive_meta = receive.get("meta") if isinstance(receive, dict) and isinstance(receive.get("meta"), dict) else {}
        receive_feedback = receive_meta.get("feedback") if isinstance(receive_meta.get("feedback"), dict) else None
        if isinstance(receive_feedback, dict) and receive_feedback.get("receive_mode") == "fifo":
            summary["receives"] += 1
            fifo_matched += 1

    if fifo_receives > fifo_matched:
        _feedback_balance_channel(channels, "fifo")["receives"] += fifo_receives - fifo_matched

    for channel, count in discarded_payloads.items():
        _feedback_balance_channel(channels, channel)["discarded_payloads"] += count

    for summary in channels.values():
        summary["unmatched_receives"] = max(0, summary["receives"] - summary["matched"])
        summary["unconsumed_payloads"] = max(
            0,
            summary["send_payloads"] - summary["matched"] - summary["discarded_payloads"],
        )
        summary["status"] = _feedback_balance_status(summary)

    statuses = {summary["status"] for summary in channels.values()}
    if "under_produced" in statuses:
        status = "under_produced"
    elif "mismatched" in statuses:
        status = "mismatched"
    elif "over_produced" in statuses:
        status = "over_produced"
    else:
        status = "balanced"
    return {"status": status, "channels": dict(sorted(channels.items()))}


def _feedback_balance_channel(channels: dict[str, dict[str, Any]], channel: str) -> dict[str, Any]:
    return channels.setdefault(
        channel,
        {
            "channel": channel,
            "sends": 0,
            "send_payloads": 0,
            "discarded_payloads": 0,
            "receives": 0,
            "matched": 0,
            "unmatched_receives": 0,
            "unconsumed_payloads": 0,
            "status": "balanced",
        },
    )


def _feedback_balance_status(summary: dict[str, Any]) -> str:
    has_unmatched_receives = summary["unmatched_receives"] > 0
    has_unconsumed_payloads = summary["unconsumed_payloads"] > 0
    if has_unmatched_receives and has_unconsumed_payloads:
        return "mismatched"
    if has_unmatched_receives:
        return "under_produced"
    if has_unconsumed_payloads:
        return "over_produced"
    return "balanced"


def _feedback_balance_diagnostics(feedback_balance: dict[str, Any]) -> list[Diagnostic]:
    channels = feedback_balance.get("channels")
    if not isinstance(channels, dict):
        return []
    diagnostics: list[Diagnostic] = []
    for channel in sorted(
        channels.values(),
        key=lambda item: str(item.get("channel", "")) if isinstance(item, dict) else "",
    ):
        if not isinstance(channel, dict):
            continue
        status = str(channel.get("status", "balanced"))
        if status == "balanced":
            continue
        diagnostics.append(
            Diagnostic(
                severity="warning",
                category="feedback_fifo_imbalance",
                message=_feedback_balance_diagnostic_message(channel),
                details={
                    "channel": str(channel.get("channel", "default")),
                    "matched": channel.get("matched", 0),
                    "receives": channel.get("receives", 0),
                    "send_payloads": channel.get("send_payloads", 0),
                    "sends": channel.get("sends", 0),
                    "status": status,
                    "unconsumed_payloads": channel.get("unconsumed_payloads", 0),
                    "unmatched_receives": channel.get("unmatched_receives", 0),
                },
            )
        )
    return diagnostics


def _feedback_balance_diagnostic_message(channel: dict[str, Any]) -> str:
    channel_id = str(channel.get("channel", "default"))
    status = str(channel.get("status", "balanced"))
    unconsumed_payloads = channel.get("unconsumed_payloads", 0)
    unmatched_receives = channel.get("unmatched_receives", 0)
    if status == "over_produced":
        return f"Feedback channel {channel_id} leaves {unconsumed_payloads} payload(s) unconsumed."
    if status == "under_produced":
        return f"Feedback channel {channel_id} has {unmatched_receives} receive(s) without a matching payload."
    return (
        f"Feedback channel {channel_id} has {unconsumed_payloads} unconsumed payload(s) "
        f"and {unmatched_receives} unmatched receive(s)."
    )


def _feedback_route_mismatch_diagnostics(events: list[dict[str, Any]], flows: list[dict[str, Any]]) -> list[Diagnostic]:
    events_by_id = {event["id"]: event for event in events}
    diagnostics: list[Diagnostic] = []
    for flow in flows:
        send = events_by_id.get(flow.get("from_event_id"))
        receive = events_by_id.get(flow.get("to_event_id"))
        if send is None or receive is None:
            continue
        channel = str(flow.get("channel", "default"))
        configured_route = _linq_feedback_route_scope(channel)
        if configured_route is None:
            continue
        send_sequencer_id = str(send.get("sequencer_id", "unknown"))
        receive_sequencer_id = str(receive.get("sequencer_id", "unknown"))
        actual_scope = "same_sequencer" if send_sequencer_id == receive_sequencer_id else "cross_sequencer"
        expected_route = "self_cast" if actual_scope == "same_sequencer" else "intra_or_multi_cast"
        if configured_route == expected_route:
            continue
        expected_channel_range = "1-15" if expected_route == "self_cast" else "16-255"
        diagnostics.append(
            Diagnostic(
                severity="warning",
                category="feedback_route_mismatch",
                message=(
                    f"Feedback channel {channel} uses {configured_route.replace('_', ' ')}, "
                    f"but the flow is {actual_scope.replace('_', ' ')} from "
                    f"{send_sequencer_id} to {receive_sequencer_id}."
                ),
                source=_event_source_location(receive),
                related_events=[send["id"], receive["id"]],
                details={
                    "actual_scope": actual_scope,
                    "channel": channel,
                    "configured_route": configured_route,
                    "expected_channel_range": expected_channel_range,
                    "expected_route": expected_route,
                    "receive_sequencer_id": receive_sequencer_id,
                    "route_source": LINQ_FEEDBACK_ROUTE_SOURCE,
                    "send_sequencer_id": send_sequencer_id,
                },
            )
        )
    return diagnostics


def _feedback_latency_diagnostics(events: list[dict[str, Any]], flows: list[dict[str, Any]]) -> list[Diagnostic]:
    events_by_id = {event["id"]: event for event in events}
    diagnostics: list[Diagnostic] = []
    for flow in flows:
        send = events_by_id.get(flow.get("from_event_id"))
        receive = events_by_id.get(flow.get("to_event_id"))
        if send is None or receive is None:
            continue
        channel = str(flow.get("channel", "default"))
        route = _linq_feedback_route(channel)
        if route is None:
            continue
        data_type = _linq_feedback_data_type(send)
        required_latency = LINQ_FEEDBACK_LATENCIES_NS.get(data_type, {}).get(route)
        if required_latency is None:
            continue
        send_time = _feedback_send_available_time(send)
        receive_time = _feedback_receive_time(receive)
        if send_time is None or receive_time is None:
            continue
        actual_gap = receive_time - send_time
        if actual_gap >= required_latency:
            continue
        missing_wait = required_latency - actual_gap
        diagnostics.append(
            Diagnostic(
                severity="warning",
                category="feedback_latency_violation",
                message=(
                    f"Feedback receive on channel {channel} occurs {missing_wait} ns before "
                    f"the official LINQ {route.replace('_', ' ')} latency for {data_type.replace('_', ' ')}."
                ),
                source=_event_source_location(receive),
                related_events=[send["id"], receive["id"]],
                details={
                    "actual_gap_ns": actual_gap,
                    "channel": channel,
                    "data_type": data_type,
                    "latency_source": LINQ_FEEDBACK_LATENCY_SOURCE,
                    "missing_wait_ns": missing_wait,
                    "required_latency_ns": required_latency,
                    "route": route,
                },
            )
        )
    return diagnostics


def _linq_feedback_route_scope(channel: str) -> str | None:
    try:
        channel_id = int(channel, 10)
    except ValueError:
        return None
    if 1 <= channel_id <= 15:
        return "self_cast"
    if 16 <= channel_id <= 255:
        return "intra_or_multi_cast"
    return None


def _linq_feedback_route(channel: str) -> str | None:
    try:
        channel_id = int(channel, 10)
    except ValueError:
        return None
    if 1 <= channel_id <= 15:
        return "self_cast"
    if 16 <= channel_id <= 255:
        return "multi_cast_worst_case"
    return None


def _linq_feedback_data_type(send: dict[str, Any]) -> str:
    meta = send.get("meta") if isinstance(send.get("meta"), dict) else {}
    feedback = meta.get("feedback") if isinstance(meta.get("feedback"), dict) else {}
    data_type = feedback.get("data_type")
    if isinstance(data_type, str) and data_type:
        return data_type
    if send.get("kind") == "feedback_com":
        return "q1_register_or_immediate"
    return "unknown"


def _feedback_send_available_time(event: dict[str, Any]) -> int | None:
    if event.get("kind") == "acquire":
        aligned_t1 = _event_aligned_t1(event)
        return aligned_t1 if aligned_t1 is not None else _concrete_event_time(event, "t1")
    aligned_t0 = _aligned_event_time(event)
    return aligned_t0 if aligned_t0 is not None else _concrete_event_time(event, "t0")


def _feedback_receive_time(event: dict[str, Any]) -> int | None:
    aligned_t0 = _aligned_event_time(event)
    return aligned_t0 if aligned_t0 is not None else _concrete_event_time(event, "t0")


def _event_aligned_t1(event: dict[str, Any]) -> int | None:
    aligned_t0 = _aligned_event_time(event)
    duration = _concrete_event_time(event, "duration")
    if aligned_t0 is None or duration is None:
        return None
    return aligned_t0 + duration


def _event_source_location(event: dict[str, Any]) -> SourceLocation:
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    return SourceLocation(
        file=str(source.get("file", "")),
        line=_source_int(source.get("line"), default=1),
        column=_source_int(source.get("column"), default=1),
        raw=str(source.get("raw", "")),
    )


def _source_int(value: Any, *, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _feedback_entry_sort_key(entry: dict[str, Any]) -> tuple[int, int, int, int]:
    t0 = _aligned_event_time(entry["event"])
    if t0 is None:
        t0 = _concrete_event_time(entry["event"], "t0")
    if t0 is None:
        return (1, entry["index"], 0, entry["index"])
    direction_priority = 0 if entry["direction"] == "send" else 1
    return (0, t0, direction_priority, entry["index"])


def _aligned_event_time(event: dict[str, Any]) -> int | None:
    meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
    value = meta.get("aligned_t0")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _concrete_event_time(event: dict[str, Any], field: str) -> int | None:
    time_value = event.get(field)
    if not isinstance(time_value, dict) or time_value.get("kind") != "concrete":
        return None
    value = time_value.get("value")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _feedback_send_capacity(event: dict[str, Any]) -> int:
    meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
    feedback = meta.get("feedback") if isinstance(meta.get("feedback"), dict) else {}
    payload_count = feedback.get("payload_count")
    if isinstance(payload_count, int) and payload_count > 0:
        return payload_count
    if event.get("kind") == "acquire":
        data_type = feedback.get("data_type")
        if data_type == "iq_values":
            return 2
        if data_type == "thresholded_bits":
            return 1
    return 1


def _next_pending_feedback_send(queue: list[dict[str, Any]]) -> dict[str, Any] | None:
    while queue and queue[0]["remaining"] <= 0:
        queue.pop(0)
    return queue[0] if queue else None


def _next_pending_fifo_feedback_send(queues: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    candidates = [
        send_entry
        for queue in queues.values()
        for send_entry in [_next_pending_feedback_send(queue)]
        if send_entry is not None
    ]
    return min(candidates, key=lambda item: item["order"]) if candidates else None


def _discard_pending_feedback_before_order(
    queues: dict[str, list[dict[str, Any]]],
    order: int,
    *,
    receive_event: dict[str, Any] | None = None,
    receive_channels_by_sequencer: dict[str, set[str]] | None = None,
) -> dict[str, int]:
    discarded: dict[str, int] = {}
    for channel, queue in queues.items():
        if not _feedback_channel_is_candidate_for_receive_queue(
            channel,
            receive_event,
            receive_channels_by_sequencer,
        ):
            continue
        while True:
            send_entry = _next_pending_feedback_send(queue)
            if send_entry is None or send_entry["order"] >= order:
                break
            discarded[channel] = discarded.get(channel, 0) + send_entry["remaining"]
            queue.pop(0)
    return discarded


def _feedback_channel_is_candidate_for_receive_queue(
    channel: str,
    receive_event: dict[str, Any] | None,
    receive_channels_by_sequencer: dict[str, set[str]] | None,
) -> bool:
    if receive_event is None or receive_channels_by_sequencer is None:
        return True
    sequencer_id = str(receive_event.get("sequencer_id", "unknown"))
    candidates = receive_channels_by_sequencer.get(sequencer_id)
    if not candidates:
        return True
    return "*" in candidates or channel in candidates


def _feedback_discarded_payloads(events: list[dict[str, Any]]) -> dict[str, int]:
    pending_sends_by_channel: dict[str, list[dict[str, Any]]] = {}
    discarded: dict[str, int] = {}
    send_order = 0
    feedback_entries = _feedback_entries(events)
    receive_channels_by_sequencer = _feedback_receive_channels_by_sequencer(feedback_entries)
    for entry in sorted(feedback_entries, key=_feedback_entry_sort_key):
        channel = entry["channel"]
        direction = entry["direction"]
        if direction == "send":
            pending_sends_by_channel.setdefault(channel, []).append(
                {
                    "event": entry["event"],
                    "remaining": _feedback_send_capacity(entry["event"]),
                    "channel": channel,
                    "order": send_order,
                }
            )
            send_order += 1
            continue

        if entry.get("receive_mode") == "fifo":
            send_entry = _next_pending_fifo_feedback_send(pending_sends_by_channel)
        else:
            send_entry = _next_pending_feedback_send(pending_sends_by_channel.get(channel, []))
            if send_entry is not None:
                for discard_channel, count in _discard_pending_feedback_before_order(
                    pending_sends_by_channel,
                    send_entry["order"],
                    receive_event=entry["event"],
                    receive_channels_by_sequencer=receive_channels_by_sequencer,
                ).items():
                    discarded[discard_channel] = discarded.get(discard_channel, 0) + count
        if send_entry is not None:
            send_entry["remaining"] -= 1
    return discarded


def _assumptions(states: list[AnalysisState]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for state in states:
        underflow = state.metadata.get("underflow")
        if isinstance(underflow, dict) and "assumptions" in underflow:
            merged["underflow"] = underflow["assumptions"]
        branches = state.metadata.get("branches")
        if isinstance(branches, dict):
            branch_assumptions = branches.get("assumptions")
            if branch_assumptions:
                merged.setdefault("branches", {})[state.sequencer_id] = _jsonable(branch_assumptions)
    return merged


def _source_to_dict(source: SourceLocation) -> dict[str, Any]:
    return {
        "file": source.file,
        "line": source.line,
        "column": source.column,
        "raw": source.raw,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return [_jsonable(item) for item in sorted(value, key=repr)]
    if isinstance(value, SourceLocation):
        return _source_to_dict(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)
