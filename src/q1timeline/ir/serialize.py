from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import replace
from datetime import date, datetime, time
from itertools import product
from pathlib import Path
from typing import Any

from q1timeline.analysis.critical_path import critical_path_from_timeline
from q1timeline.analysis.acquisition_windows import (
    CONFIGURED_INTEGRATION_SOURCES, acquisition_windows, qrm_scope_key,
    resolve_qrm_scope_settings, scope_acquisition_windows,
)
from q1timeline.analysis.interpreter import AnalysisState, TimelineEvent
from q1timeline.analysis.hardware_readiness import hardware_readiness_summary
from q1timeline.analysis.queue_timing import synchronization_participants
from q1timeline.analysis.values import value_to_json
from q1timeline.diagnostics import Diagnostic
from q1timeline.ir.control_flow_graph import control_flow_graph_from_states
from q1timeline.q1asm.ast import SourceLocation
from q1timeline.q1asm.linq_commands import LINQ_CONFIGURATION_STREAMS


TIMELINE_IR_SCHEMA_VERSION = "0.1.0"
LINQ_FEEDBACK_LATENCY_SOURCE = (
    "https://docs.qblox.com/en/main/products/architecture/sequencers/"
    "linq_based_feedback.html#latencies-of-linq-based-feedback-data-types"
)
LINQ_FEEDBACK_ROUTE_SOURCE = (
    "https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#data-routing"
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
    feedback_routes: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    _validate_unique_sequencer_ids(states)
    events = []
    sequencers = []
    simulated = bool(states) and all("simulation" in state.metadata for state in states)
    scope_settings, scope_conflicts = resolve_qrm_scope_settings(states) if not simulated else ({}, {})
    for state in states:
        sequencer = {"id": state.sequencer_id}
        if state.metadata.get("module"):
            sequencer["module"] = state.metadata["module"]
        if state.metadata.get("hardware_parameters"):
            sequencer["hardware_parameters"] = _jsonable(state.metadata["hardware_parameters"])
        sequencers.append(sequencer)
        windows = acquisition_windows(state)
        scope_key = qrm_scope_key(state)
        scope_windows = scope_acquisition_windows(state, scope_settings.get(scope_key, {})) if scope_key not in scope_conflicts else {}
        for event in state.events:
            serialized = _event_to_dict(event)
            if event.id in windows:
                serialized["meta"]["acquisition_window"] = windows[event.id]
            if event.id in scope_windows:
                serialized["meta"]["scope_acquisition_window"] = scope_windows[event.id]
            events.append(serialized)
    input_diagnostics = (
        list(diagnostics)
        if diagnostics is not None
        else [diagnostic for state in states for diagnostic in state.diagnostics]
    )
    if simulated:
        feedback_flows = _simulation_feedback_flows(events)
        feedback_balance = {"status": "simulated", "channels": {}}
        generated_diagnostics = []
    else:
        feedback_flows, feedback_balance, generated_diagnostics = _analytical_feedback(
            events, {item["id"]: item.get("module") for item in sequencers},
            (project or {}).get("feedback_transport_delay_ns", 200),
            tuple(feedback_routes), synchronization_participants(
                (item["id"] for item in sequencers),
                {item["id"]: item.get("hardware_parameters", {}) for item in sequencers},
            ),
        )
        for (kind, name), fields in sorted(scope_conflicts.items()):
            generated_diagnostics.append(Diagnostic(
                severity="warning", category="analysis_incomplete",
                message=f"Conflicting scope settings for {kind} {name!r}. The scope capture window is unknown.",
                details={kind: name, "conflicting_scope_settings": sorted(fields)},
            ))
    alignment = {
        state.sequencer_id: _jsonable(state.metadata.get("alignment", {}))
        for state in states
    }
    if diagnostics is not None:
        diagnostics.extend(generated_diagnostics)
    serialized_diagnostics = [
        _diagnostic_to_dict(diagnostic)
        for diagnostic in [*input_diagnostics, *generated_diagnostics]
    ]
    critical_path = critical_path_from_timeline(
        events,
        feedback_flows,
        alignment=alignment,
        diagnostics=serialized_diagnostics,
    )
    # These annotations summarize events already counted by the timing analyses.
    events.extend(_event_to_dict(event) for state in states for event in state.observed_loop_events)
    ir = {
        "version": TIMELINE_IR_SCHEMA_VERSION,
        "project": _jsonable(project or {}),
        "analysis": {
            "event_kinds": sorted(REQUIRED_EVENT_KINDS | {event["kind"] for event in events}),
            "feedback_latencies_ns": LINQ_FEEDBACK_LATENCIES_NS,
        },
        "sequencers": sequencers,
        "events": events,
        "register_history": [
            {
                "sequencer_id": write.sequencer_id,
                "register": write.register,
                "time": value_to_json(write.time),
                "value": value_to_json(write.value),
                "source": _source_to_dict(write.source),
                "event_id": write.event_id,
                "confidence": write.confidence,
                "meta": _jsonable(write.meta),
            }
            for state in states
            for write in state.register_history
        ],
        "diagnostics": serialized_diagnostics,
        "source_map": _source_map(events),
        "feedback_flows": feedback_flows,
        "feedback_balance": feedback_balance,
        "critical_path": critical_path,
        "control_flow_graph": control_flow_graph_from_states(states),
        "alignment": alignment,
        "assumptions": _assumptions(states),
    }
    execution = {
        state.sequencer_id: _jsonable(state.metadata["execution"])
        for state in states
        if "execution" in state.metadata
    }
    if execution:
        ir["execution"] = execution
    simulation = {
        state.sequencer_id: _jsonable(state.metadata["simulation"])
        for state in states
        if "simulation" in state.metadata
    }
    if simulation:
        ir["simulation"] = simulation
    readiness = hardware_readiness_summary(states)
    if readiness is not None:
        ir["hardware_readiness"] = readiness
    return ir


def _simulation_feedback_flows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {event["id"]: event for event in events}
    consumers = {}
    for event in events:
        meta = event.get("meta", {})
        if not (event["kind"] == "wait_trigger"
                or event["kind"] == "q1_issue" and meta.get("op") in {"fb_pop_data", "fb_pull_data"}):
            continue
        branch = meta.get("branch_comparison_branch_id"), meta.get("branch_comparison_path")
        for input_id in meta.get("input_event_ids", []):
            consumers[input_id, branch] = event["id"]
    flows = []
    for event in events:
        if event["kind"] not in {"feedback_receive", "trigger_receive"}:
            continue
        meta = event.get("meta", {})
        source = by_id.get(meta.get("source_event_id"))
        if source is None:
            continue
        channel = str(meta.get("id", meta.get("address", "")))
        flow = {
            "id": f"simulation-flow-{len(flows)}",
            "from_event_id": source["id"], "to_event_id": event["id"],
            "channel": channel, "source": source["sequencer_id"], "target": event["sequencer_id"],
            "label": f"Simulated {event['kind']}: {source['sequencer_id']} -> {event['sequencer_id']}",
            "timing_model": "event_driven",
        }
        branch = meta.get("branch_comparison_branch_id"), meta.get("branch_comparison_path")
        consumer_id = consumers.get((event["id"], branch))
        if consumer_id is not None:
            flow["consumer_event_id"] = consumer_id
        if event["kind"] == "feedback_receive" and meta.get("write_combine"):
            source_ids = [event_id for event_id in meta.get("source_event_ids", ()) if event_id in by_id]
            if source_ids:
                flow["source_event_ids"] = source_ids
        flows.append(flow)
    return flows


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


def _analytical_feedback(
    events: list[dict[str, Any]], modules: dict[str, str | None], transport_delay_ns: int,
    feedback_routes: tuple[dict[str, Any], ...], participants: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[Diagnostic]]:
    all_entries = _feedback_entries(events)
    branch_ids = {
        str(entry["event"]["meta"]["branch_comparison_branch_id"])
        for entry in all_entries
        if entry["event"]["meta"].get("branch_comparison_branch_id")
    }
    send_streams = {(entry["event"]["sequencer_id"], entry["feedback"].get("data_type"))
                    for entry in all_entries if entry["direction"] == "send"}
    for event in events:
        meta = event["meta"]
        configuration = meta.get("linq_configuration")
        branch_id = meta.get("branch_comparison_branch_id")
        if branch_id and (meta.get("feedback_fifo_incomplete") or (
            configuration and (event["sequencer_id"], configuration.get("data_type")) in send_streams
        )):
            branch_ids.add(str(branch_id))
    branch_ids = sorted(branch_ids)
    # Independent sequencer comparisons multiply the alternatives. Keep feedback
    # analysis bounded and explicitly unknown beyond sixteen combinations.
    if len(branch_ids) > 4:
        count = 2 ** len(branch_ids)
        return [], {"status": "unknown", "channels": {}, "reason": "branch_comparison_limit", "alternative_count": count}, [
            Diagnostic(
                severity="info", category="analysis_incomplete",
                message=f"Feedback analysis is unavailable for {count} branch combinations (limit: 16).",
                details={"analysis_scope": "feedback", "reason": "branch_comparison_limit", "alternative_count": count},
            )
        ]
    combined_flows: dict[tuple, dict[str, Any]] = {}
    alternatives = []
    diagnostics: list[Diagnostic] = []
    for paths in product(("taken", "fallthrough"), repeat=len(branch_ids)):
        assumptions = dict(zip(branch_ids, paths))
        selected_events = [
            event for event in events
            if not event.get("meta", {}).get("branch_comparison_branch_id")
            or assumptions.get(str(event["meta"]["branch_comparison_branch_id"])) == event["meta"].get("branch_comparison_path")
        ] if branch_ids else events
        entries = _feedback_entries(selected_events) if branch_ids else all_entries
        entries, sync_arms, round_diagnostics = _project_tb_rounds(entries, selected_events, feedback_routes, participants)
        cutoff_receivers = {event["sequencer_id"] for event in selected_events
                            if event["meta"].get("feedback_fifo_incomplete")}
        flows, discarded_payloads, ordering, cutoff_channels = _feedback_flows(
            entries, modules, transport_delay_ns, cutoff_receivers, sync_arms,
        )
        balance = _feedback_balance(entries, flows, discarded_payloads, ordering, cutoff_channels,
                                    has_cutoff=bool(cutoff_receivers))
        findings = [
            *round_diagnostics,
            *ordering,
            *_feedback_route_mismatch_diagnostics(selected_events, entries, flows),
            *_feedback_latency_diagnostics(selected_events, flows),
            *_feedback_balance_diagnostics(balance),
        ]
        if not branch_ids:
            return flows, balance, findings
        alternatives.append({"assumptions": assumptions, **balance})
        label = paths[0].capitalize() if len(paths) == 1 else f"Comparison alternative {len(alternatives)}"
        diagnostics.extend(replace(
            diagnostic, message=f"{label}: {diagnostic.message}",
            details={**diagnostic.details, "comparison_assumptions": assumptions},
        ) for diagnostic in findings)
        for flow in flows:
            key = (flow["from_event_id"], flow["to_event_id"], flow["channel"],
                   flow["timing"]["data_type"], flow.get("payload_word"), tuple(flow.get("source_event_ids", ())))
            if key not in combined_flows:
                combined_flows[key] = {**flow, "id": f"feedback-flow-{len(combined_flows)}", "comparison_assumptions": []}
            combined_flows[key]["comparison_assumptions"].append(assumptions)
    # Flat counts would count mutually exclusive receives together and make a
    # common-prefix payload appear consumed twice. Retain each scenario instead.
    return list(combined_flows.values()), {"status": "comparison", "channels": {}, "alternatives": alternatives}, diagnostics


def _feedback_flows(
    entries: list[dict[str, Any]], modules: dict[str, str | None], transport_delay_ns: int,
    cutoff_receivers: set[str], sync_arms: dict[str, int],
) -> tuple[list[dict[str, Any]], dict[str, int], list[Diagnostic], set[str]]:
    queues: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    flows: list[dict[str, Any]] = []
    discarded_payloads: dict[str, int] = {}
    ordering: list[Diagnostic] = []
    receive_channels_by_sequencer = _feedback_receive_channels_by_sequencer(entries)
    has_unverified_sends = any(_feedback_send_unknown(entry) for entry in entries)

    def enqueue(entry: dict[str, Any], order: int) -> None:
        event, channel = entry["event"], entry["channel"]
        key = _feedback_queue_key(channel, event["sequencer_id"])
        latency = entry.get("route_latency")
        if latency is not None:
            key = channel, entry["route_target"]
        unverified = _feedback_send_unknown(entry)
        queues.setdefault(key, []).append({
            "event": event, "remaining": None if unverified else _feedback_send_capacity(event),
            "ordering_unknown": unverified, "channel": channel, "order": order, "event_order": entry["index"],
            "self_cast_timing": (_feedback_timing(event, event, channel, modules, transport_delay_ns)
                                 if _linq_feedback_route_scope(channel) == "self_cast" else None),
            "projected_sources": entry.get("projected_sources", ()),
            "completion": entry.get("completion"), "route_latency": latency,
        })

    ordered = sorted(entries, key=_feedback_entry_sort_key)
    # A receive can block for a proven routed word even when display alignment
    # puts the sending command later than the receive in the nominal trace.
    for order, entry in enumerate(ordered):
        if entry.get("route_latency") is not None or entry.get("tb_round_unknown"):
            enqueue(entry, order)

    for order, entry in enumerate(ordered):
        event = entry["event"]
        feedback = entry["feedback"]
        channel = entry["channel"]
        direction = entry["direction"]
        sequencer_id = event["sequencer_id"]
        queue_key = _feedback_queue_key(channel, sequencer_id)
        if direction == "send":
            if entry.get("route_latency") is None and not entry.get("tb_round_unknown"):
                enqueue(entry, order)
            continue

        fifo = entry.get("receive_mode") == "fifo"
        if fifo:
            candidates = _fifo_feedback_candidates(queues, sequencer_id, has_unverified_sends)
        elif queue_key[1] is not None:
            candidates = _first_timed_feedback_sends(_pending_feedback_sends(queues.get(queue_key, [])))
        else:
            candidates = _possible_first_by_issue(
                _pending_feedback_sends(queues.get(queue_key, [])), has_unverified_sends,
            ) + _first_timed_feedback_sends(_pending_feedback_sends(queues.get((channel, sequencer_id), [])))
        if not candidates:
            continue
        match_unknown = len(candidates) != 1 or bool(candidates[0].get("ordering_unknown"))
        uncertain = candidates.copy() if match_unknown else []
        if not fifo:
            uncertain.extend(_discard_preceding_feedback(
                queues, candidates, sequencer_id, receive_channels_by_sequencer.get(sequencer_id, set()), discarded_payloads,
            ))
        if uncertain:
            for item in uncertain:
                item["ordering_unknown"] = True
            related = [event["id"]]
            for item in candidates + uncertain:
                source_event = item["event"]
                related.extend(item["projected_sources"] or (source_event["id"],))
                related.extend(source_event["meta"]["feedback"].get("unknown_configuration_event_ids", ()))
            ordering.append(Diagnostic(
                severity="warning", category="analysis_incomplete", source=_event_source_location(event),
                message=("Feedback FIFO source order is unknown." if match_unknown
                         else "The feedback source is known, but the order of preceding payloads is unknown."),
                related_events=list(dict.fromkeys(related)),
                details={"analysis_scope": "feedback", "reason": "feedback_ordering_unknown",
                         "receive_event_id": event["id"], "match_unknown": match_unknown,
                         "channels": sorted({item["channel"] for item in uncertain})},
            ))
        if match_unknown:
            continue
        send_entry = candidates[0]
        channel = send_entry["channel"]
        send_entry["remaining"] -= 1
        send = send_entry["event"]
        if send is None:
            continue
        send_feedback = send.get("meta", {}).get("feedback", {})
        source = str(send_feedback.get("source", "feedback"))
        target = str(feedback.get("target", "consumer"))
        flow_id = f"feedback-flow-{len(flows)}"
        timing = send_entry["self_cast_timing"] or (
            _routed_acquisition_timing(send_entry, event, sync_arms) if send_entry["route_latency"] is not None
            else _feedback_timing(send, event, channel, modules, transport_delay_ns)
        )
        flow = {
            "id": flow_id,
            "from_event_id": send["id"],
            "to_event_id": event["id"],
            "channel": channel,
            "source": source,
            "target": target,
            "label": f"feedback ch {channel}: {source} -> {target}",
            "timing": timing,
        }
        if len(send_entry["projected_sources"]) > 1:
            flow["source_event_ids"] = send_entry["projected_sources"]
        if send.get("meta", {}).get("acquisition_window", {}).get("source") in CONFIGURED_INTEGRATION_SOURCES:
            flow["send_available_ns"] = timing["send_available_ns"]
        payload_words = send_feedback.get("payload_words")
        payload_index = _feedback_send_capacity(send) - send_entry["remaining"] - 1
        if isinstance(payload_words, list) and payload_index < len(payload_words):
            flow["payload_word"] = payload_words[payload_index]
        flows.append(flow)
    # Keep completed matches. Only residual queues that could reach a cutoff receiver are uncertain.
    cutoff_channels = {
        channel for (channel, owner), queue in queues.items()
        if (owner is None or owner in cutoff_receivers) and any(_pending_feedback_sends(queue))
    } if cutoff_receivers else set()
    return flows, discarded_payloads, ordering, cutoff_channels


def _feedback_entries(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    active_configurations: dict[str, dict[str, tuple[str, tuple[int, int] | None, bool]]] = {}
    send_configurations: dict[str, dict[str, tuple[str, tuple[int, int] | None, bool]]] = {}
    for index, event in enumerate(events):
        meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
        sequencer_id = event["sequencer_id"]
        active = active_configurations.get(sequencer_id, {})
        configuration = meta.get("linq_configuration")
        preview_source = meta.get("preview_source_event_id")
        # A preview copies the original send's configuration without changing the recorded path.
        if isinstance(configuration, dict) and preview_source is None:
            op = configuration.get("op")
            if op in LINQ_CONFIGURATION_STREAMS:
                operands = configuration.get("operands")
                layout = ((operands[1], operands[2]) if op == "fb_acq_tb_cfg"
                          and not configuration.get("reason") and operands and operands[0] else None)
                previous = active.get(op)
                if configuration.get("reason") is not None or (
                    configuration.get("conditional") and op == "fb_acq_tb_cfg"
                    and layout != (previous[1] if previous else None)
                ):
                    active = {**active, op: (event["id"], None, True)}
                elif not configuration.get("conditional"):
                    active = {key: value for key, value in active.items() if key != op}
                    if layout is not None:
                        active[op] = (event["id"], layout, False)
                active_configurations[sequencer_id] = active
        streams = meta.get("feedback_streams")
        multiple = isinstance(streams, list)
        candidates = streams if multiple else (meta.get("feedback"),)
        for feedback in candidates:
            if not isinstance(feedback, dict) or feedback.get("direction") not in {"send", "receive"}:
                continue
            unknown_ids = []
            tb_layout = None
            if feedback["direction"] == "send":
                settings = send_configurations.get(preview_source, active)
                send_configurations[event["id"]] = settings
                data_type = feedback.get("data_type")
                unknown_ids = [event_id for op, (event_id, _layout, unknown) in settings.items()
                               if unknown and LINQ_CONFIGURATION_STREAMS[op] == data_type]
                if data_type == "thresholded_bits" and not unknown_ids and "fb_acq_tb_cfg" in settings:
                    tb_layout = settings["fb_acq_tb_cfg"][1]
                if unknown_ids:
                    feedback = {**feedback, "send_time_unknown": True, "unknown_configuration_event_ids": unknown_ids}
            # Use each stream for timing and capacity while retaining the source event ID.
            source = {**event, "meta": {**meta, "feedback": feedback}} if multiple or unknown_ids else event
            entries.append({
                "index": index,
                "event": source,
                "feedback": feedback,
                "channel": str(feedback.get("channel", "default")),
                "direction": feedback["direction"],
                "receive_mode": feedback.get("receive_mode"),
                **({"tb_layout": tb_layout} if tb_layout is not None else {}),
            })
    return entries


def _tb_sync_arms(events: list[dict[str, Any]], participants: set[str]) -> dict[str, int]:
    """Use the first reached barrier's release as a shared clock, independently of display alignment."""
    reached: set[str] = set()
    arms: dict[str, int] = {}
    blocked: set[str] = set()
    event_arms: dict[str, int] = {}
    for event in events:
        sequencer = event["sequencer_id"]
        if sequencer not in participants or sequencer in blocked:
            continue
        meta = event["meta"]
        if not event["lane"].startswith("rt.") or any(meta.get(key) for key in (
            "loop_preview", "preview_source_event_id", "branch_comparison_branch_id",
        )):
            continue
        if (event["kind"] in {"branch_region", "unknown_region", "illegal"}
                or meta.get("rt_trigger_epoch") or meta.get("conditional_time_unknown")):
            blocked.add(sequencer)
            continue
        if event["kind"] == "wait_sync":
            start = _concrete_event_time(event, "t0")
            if (sequencer in reached or start is None or _concrete_event_time(event, "t1") is None
                    or event["confidence"] != "exact" or meta.get("conditional_execution_unknown")
                    or meta.get("assumed_branch_path")):
                blocked.add(sequencer)
                continue
            reached.add(sequencer)
            arms[sequencer] = start
        if sequencer in arms:
            event_arms[event["id"]] = arms[sequencer]
    return event_arms if participants == reached else {}


def _acquisition_completion(event: dict[str, Any], sync_arms: dict[str, int]) -> tuple[tuple, int] | None:
    meta = event["meta"]
    window = meta.get("acquisition_window", {})
    end = window.get("end_ns")
    if (window.get("source") not in CONFIGURED_INTEGRATION_SOURCES or type(end) is not int
            or meta.get("conditional_time_unknown") or meta.get("conditional_execution_unknown")):
        return None
    if event["id"] in sync_arms:
        return (), end - sync_arms[event["id"]]
    return (event["sequencer_id"], meta.get("rt_trigger_epoch", 0)), end


def _tb_rounds_by_completion(entries: list[dict[str, Any]]) -> Iterable[list[dict[str, Any]]]:
    times = [entry["completion"] for entry in entries]
    if None in times or len({time[0] for time in times}) > 1:
        # Distinct clocks or an unobserved end may describe the same physical completion.
        yield entries
        return
    by_time: dict[int, list[dict[str, Any]]] = {}
    for entry in entries:
        by_time.setdefault(entry["completion"][1], []).append(entry)
    yield from by_time.values()


def _project_tb_rounds(
    entries: list[dict[str, Any]], events: list[dict[str, Any]], routes: tuple[dict[str, Any], ...],
    participants: set[str],
) -> tuple[list[dict[str, Any]], dict[str, int], list[Diagnostic]]:
    if not any("tb_layout" in entry for entry in entries):
        return entries, {}, []
    sync_arms = _tb_sync_arms(events, participants)
    direct, diagnostics = [], []
    by_destination: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    routes_by_sender: dict[str, list[dict[str, Any]]] = {}
    for route in routes:
        routes_by_sender.setdefault(route["source"], []).append(route)

    def destinations_for(entry: dict[str, Any]) -> list[tuple[str, int]]:
        sender, channel = entry["event"]["sequencer_id"], entry["channel"]
        return [(route["target"], route["latency_ns"]) for route in routes_by_sender.get(sender, ())
                if str(route.get("id", channel)) == channel]

    for entry in entries:
        if "tb_layout" not in entry:
            continue
        event, channel = entry["event"], entry["channel"]
        sender = event["sequencer_id"]
        if _linq_feedback_route_scope(channel) == "self_cast":
            destinations = [(sender, None)]
        else:
            destinations = destinations_for(entry)
        for target, latency in destinations or [(None, None)]:
            by_destination.setdefault((channel, target), []).append({
                **entry, "completion": _acquisition_completion(event, sync_arms),
                "route_target": target, "route_latency": latency,
            })
    combine_targets = {target for (_channel, target), contributions in by_destination.items()
                       if target is not None and contributions[0]["route_latency"] is not None}
    for entry in entries:
        if "tb_layout" in entry:
            continue
        # Independent IQ still shares the recipient's FIFO. Where the project
        # proves both routes, order it by arrival without merging its words.
        iq_routes = (destinations_for(entry) if entry["direction"] == "send"
                     and entry["feedback"].get("data_type") == "iq_values"
                     and _linq_feedback_route_scope(entry["channel"]) == "intra_or_multi_cast" else [])
        if any(target in combine_targets for target, _ in iq_routes):
            event = entry["event"]
            direct.extend({**entry, "completion": _acquisition_completion(event, sync_arms),
                           "route_target": target, "route_latency": latency, "projected_sources": [event["id"]]}
                          for target, latency in iq_routes)
        else:
            direct.append(entry)
    routed_times: dict[str, set[tuple | None]] = {}
    for (channel, target), contributions in by_destination.items():
        if target is not None:
            routed_times.setdefault(channel, set()).update(item["completion"] for item in contributions)
    for (channel, target), contributions in by_destination.items():
        other_times = (routed_times.get(channel, set()) if target is None else
                       {item["completion"] for item in by_destination.get((channel, None), ())})
        other_clocks = {time[0] for time in other_times if time is not None}
        for group in _tb_rounds_by_completion(contributions):
            first = group[0]
            completion = first["completion"]
            uncertain = len(group) > 1 and (
                target is None or completion is None or any(item["completion"] != completion for item in group)
            )
            if other_times and (completion is None or None in other_times or completion in other_times
                                or other_clocks - {completion[0]}):
                uncertain = True
            occupied = 0
            for item in group:
                position, length = item["tb_layout"]
                mask = 3 << position
                if occupied & mask or length != first["tb_layout"][1] or item["route_latency"] != first["route_latency"]:
                    uncertain = True
                occupied |= mask
            source_ids = list(dict.fromkeys(item["event"]["id"] for item in group))
            direct.append({**first, "projected_sources": source_ids, "tb_round_unknown": uncertain})
            if uncertain:
                diagnostics.append(Diagnostic(
                    severity="warning", category="analysis_incomplete", source=_event_source_location(first["event"]),
                    message=f"TB write-combine FIFO words on channel {channel} cannot be determined.",
                    related_events=source_ids,
                    details={"analysis_scope": "feedback", "reason": "tb_write_combine_unknown", "channel": channel},
                ))
    return direct, sync_arms, diagnostics


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


def _feedback_balance(entries: list[dict[str, Any]], flows: list[dict[str, Any]],
                      discarded_payloads: dict[str, int], ordering: list[Diagnostic],
                      cutoff_channels: set[str], *, has_cutoff: bool) -> dict[str, Any]:
    channels: dict[str, dict[str, Any]] = {}
    unknown_send_channels = cutoff_channels | {channel for issue in ordering for channel in issue.details["channels"]}
    unknown_receives = {issue.details["receive_event_id"] for issue in ordering if issue.details["match_unknown"]}
    fifo_receive_ids = set()
    fifo_receives = 0
    fifo_matched = 0
    projected_sends: set[tuple[str, str, str]] = set()

    for entry in entries:
        direction, channel = entry["direction"], entry["channel"]
        if direction == "receive" and entry["event"]["id"] in unknown_receives:
            summary = _feedback_balance_channel(channels, "fifo" if entry["receive_mode"] == "fifo" else channel)
            summary["ordering_unknown_receives"] = summary.get("ordering_unknown_receives", 0) + 1
        if direction == "receive" and entry["receive_mode"] == "fifo":
            fifo_receive_ids.add(entry["event"].get("id"))
            fifo_receives += 1
            continue
        summary = _feedback_balance_channel(channels, channel)
        if direction == "send":
            if "projected_sources" in entry:
                for event_id in entry["projected_sources"]:
                    contribution = channel, event_id, entry["feedback"]["data_type"]
                    if contribution not in projected_sends:
                        summary["sends"] += 1
                        projected_sends.add(contribution)
            else:
                summary["sends"] += 1
            if _feedback_send_unknown(entry):
                unknown_send_channels.add(channel)
                summary["send_payloads"] = None
            elif summary["send_payloads"] is not None:
                summary["send_payloads"] += _feedback_send_capacity(entry["event"])
        else:
            summary["receives"] += 1

    for flow in flows:
        channel = str(flow.get("channel", "default"))
        summary = _feedback_balance_channel(channels, channel)
        summary["matched"] += 1
        if flow.get("to_event_id") in fifo_receive_ids:
            summary["receives"] += 1
            fifo_matched += 1

    if fifo_receives > fifo_matched:
        _feedback_balance_channel(channels, "fifo")["receives"] += fifo_receives - fifo_matched

    for channel, count in discarded_payloads.items():
        _feedback_balance_channel(channels, channel)["discarded_payloads"] += count

    for channel, summary in channels.items():
        unknown_count = summary.get("ordering_unknown_receives", 0)
        summary["unmatched_receives"] = max(0, summary["receives"] - summary["matched"] - unknown_count)
        if channel in unknown_send_channels:
            summary["unconsumed_payloads"] = None
        else:
            summary["unconsumed_payloads"] = max(0, summary["send_payloads"] - summary["matched"] - summary["discarded_payloads"])
        summary["status"] = ("incomplete" if channel in unknown_send_channels or unknown_count
                             else _feedback_balance_status(summary))

    statuses = {summary["status"] for summary in channels.values()}
    if has_cutoff or "incomplete" in statuses:
        status = "incomplete"
    elif "under_produced" in statuses:
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
        if status in {"balanced", "incomplete"}:
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


def _feedback_route_mismatch_diagnostics(
    events: list[dict[str, Any]], entries: list[dict[str, Any]], flows: list[dict[str, Any]],
) -> list[Diagnostic]:
    events_by_id = {event["id"]: event for event in events}
    diagnostics: list[Diagnostic] = []
    for flow in flows:
        if flow["timing"]["route"] == "project_route":
            continue
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
    matched_receives = {flow["to_event_id"] for flow in flows}
    self_cast_senders: dict[str, dict[str, str]] = {}
    for entry in entries:
        if entry["direction"] == "send" and _linq_feedback_route_scope(entry["channel"]) == "self_cast":
            send = entry["event"]
            self_cast_senders.setdefault(entry["channel"], {}).setdefault(send["sequencer_id"], send["id"])
    for entry in entries:
        if entry["direction"] != "receive" or entry.get("receive_mode") == "fifo":
            continue
        receive = entry["event"]
        channel = entry["channel"]
        receiver = receive["sequencer_id"]
        senders = self_cast_senders.get(channel)
        if receive["id"] in matched_receives or not senders or receiver in senders:
            continue
        sources = sorted(senders)
        diagnostics.append(Diagnostic(
            severity="warning", category="feedback_route_mismatch", source=_event_source_location(receive),
            message=(f"Feedback channel {channel} returns only to its sender. {receiver} has no local send. "
                     "For delivery from another sequencer, configure a route with an ID from 16 to 255."),
            related_events=[*(senders[source] for source in sources), receive["id"]],
            details={"actual_scope": "cross_sequencer", "channel": channel, "configured_route": "self_cast",
                     "expected_channel_range": "16-255", "expected_route": "intra_or_multi_cast",
                     "receive_sequencer_id": receiver, "route_source": LINQ_FEEDBACK_ROUTE_SOURCE,
                     "send_sequencer_id": sources[0], "send_sequencer_ids": sources,
                     "reason": "remote_only_self_cast"},
        ))
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
        timing = flow["timing"]
        route = timing["route"]
        data_type = timing["data_type"]
        required_latency = timing["latency_ns"]
        if required_latency is None or timing["arrival_ns"] is None:
            diagnostics.append(Diagnostic(
                severity="warning", category="analysis_incomplete",
                message=f"Feedback arrival on channel {channel} is unknown; packet emission time or data type is unresolved.",
                source=_event_source_location(receive), related_events=[receive["id"], send["id"]],
                details={"analysis_scope": "feedback", "reason": "feedback_timing_unknown", "channel": channel},
            ))
            continue
        receive_time = _feedback_receive_time(receive)
        if receive_time is None:
            continue
        actual_gap = receive_time - timing["arrival_ns"] + required_latency
        if actual_gap >= required_latency:
            continue
        missing_wait = required_latency - actual_gap
        op = receive.get("meta", {}).get("op", "fb_pop_data")
        latency_description = (f"{required_latency} ns configured project route" if route == "project_route" else
                               f"{route.replace('_', ' ')}: {timing['base_latency_ns']} ns LINQ + "
                               f"{timing['transport_delay_ns']} ns configured transport delay")
        diagnostics.append(
            Diagnostic(
                severity="warning",
                category="feedback_latency_violation",
                message=(
                    f"{op} on channel {channel} may block for {missing_wait} ns before estimated feedback arrival "
                    f"({latency_description} for {data_type.replace('_', ' ')})."
                ),
                source=_event_source_location(receive),
                related_events=[send["id"], receive["id"]],
                details={
                    "actual_gap_ns": actual_gap,
                    "channel": channel,
                    "data_type": data_type,
                    "latency_source": "project_route" if route == "project_route" else LINQ_FEEDBACK_LATENCY_SOURCE,
                    "missing_wait_ns": missing_wait,
                    "required_latency_ns": required_latency,
                    "route": route,
                    "base_latency_ns": timing["base_latency_ns"],
                    "transport_delay_ns": timing["transport_delay_ns"],
                    "arrival_ns": timing["arrival_ns"],
                    "receive_op": op,
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


def _linq_feedback_route(channel: str, send: dict[str, Any], receive: dict[str, Any], modules: dict[str, str | None]) -> str | None:
    try:
        channel_id = int(channel, 10)
    except ValueError:
        return None
    if 1 <= channel_id <= 15:
        return "self_cast"
    if 16 <= channel_id <= 255:
        source_module = modules.get(send["sequencer_id"])
        target_module = modules.get(receive["sequencer_id"])
        if source_module and target_module and source_module == target_module:
            return "intra_cast"
        return "multi_cast_worst_case"
    return None


def _feedback_timing(send: dict[str, Any], receive: dict[str, Any], channel: str,
                     modules: dict[str, str | None], transport_delay_ns: int) -> dict[str, Any]:
    route = _linq_feedback_route(channel, send, receive, modules)
    data_type = _linq_feedback_data_type(send)
    base = LINQ_FEEDBACK_LATENCIES_NS.get(data_type, {}).get(route)
    extra = 0 if route == "self_cast" else transport_delay_ns
    latency = base + extra if base is not None else None
    send_time = _feedback_send_available_time(send)
    return {"route": route, "data_type": data_type, "base_latency_ns": base,
            "transport_delay_ns": extra, "latency_ns": latency,
            "send_available_ns": send_time,
            "arrival_ns": send_time + latency if send_time is not None and latency is not None else None}


def _routed_acquisition_timing(item: dict[str, Any], receive: dict[str, Any], sync_arms: dict[str, int]) -> dict[str, Any]:
    send, latency = item["event"], item["route_latency"]
    completion = item["completion"]
    arrival = None
    if completion is not None:
        clock, relative_ns = completion
        arm = sync_arms.get(receive["id"]) if not clock else None
        if clock == (receive["sequencer_id"], receive["meta"].get("rt_trigger_epoch", 0)):
            arm = 0
        receive_local, receive_display = _concrete_event_time(receive, "t0"), _aligned_event_time(receive)
        if arm is not None and receive_local is not None:
            offset = receive_display - receive_local if receive_display is not None else 0
            arrival = arm + relative_ns + latency + offset
    return {"route": "project_route", "data_type": _linq_feedback_data_type(send), "base_latency_ns": latency,
            "transport_delay_ns": 0, "latency_ns": latency, "arrival_ns": arrival,
            "send_available_ns": _feedback_send_available_time(send, relative=True)}


def _linq_feedback_data_type(send: dict[str, Any]) -> str:
    meta = send.get("meta") if isinstance(send.get("meta"), dict) else {}
    feedback = meta.get("feedback") if isinstance(meta.get("feedback"), dict) else {}
    data_type = feedback.get("data_type")
    if isinstance(data_type, str) and data_type:
        return data_type
    if send.get("kind") == "feedback_com":
        return "q1_register_or_immediate"
    return "unknown"


def _feedback_send_available_time(event: dict[str, Any], *, relative: bool = False) -> int | None:
    meta = event.get("meta", {})
    feedback = meta.get("feedback", {})
    if (meta.get("conditional_time_unknown") or feedback.get("send_time_unknown")
            or (not relative and (meta.get("rt_trigger_epoch") or meta.get("aligned_time_nominal")))):
        return None
    if event.get("kind") == "acquire":
        if feedback.get("send_on") == "command_start":
            aligned_t0 = _aligned_event_time(event)
            return aligned_t0 if aligned_t0 is not None else _concrete_event_time(event, "t0")
        window = meta.get("acquisition_window")
        if window is not None and window.get("source") in CONFIGURED_INTEGRATION_SOURCES:
            end = window.get("end_ns")
            if window.get("status") == "unknown" or not isinstance(end, int):
                return None
            local_start = _concrete_event_time(event, "t0")
            aligned_start = _aligned_event_time(event)
            return end + aligned_start - local_start if aligned_start is not None and local_start is not None else end
        return None
    aligned_t0 = _aligned_event_time(event)
    return aligned_t0 if aligned_t0 is not None else _concrete_event_time(event, "t0")


def _feedback_receive_time(event: dict[str, Any]) -> int | None:
    meta = event.get("meta", {})
    if meta.get("rt_trigger_epoch") or meta.get("aligned_time_nominal"):
        return None
    aligned_t0 = _aligned_event_time(event)
    return aligned_t0 if aligned_t0 is not None else _concrete_event_time(event, "t0")


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


def _feedback_send_unknown(entry: dict[str, Any]) -> bool:
    return bool(entry.get("tb_round_unknown") or entry["feedback"].get("unknown_configuration_event_ids"))


def _pending_feedback_sends(queue: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    while queue and queue[0]["remaining"] is not None and queue[0]["remaining"] <= 0:
        queue.pop(0)
    return (item for item in queue if item["remaining"] is None or item["remaining"] > 0)


def _feedback_queue_key(channel: str, sequencer_id: str) -> tuple[str, str | None]:
    return channel, sequencer_id if _linq_feedback_route_scope(channel) == "self_cast" else None


def _possible_first_by_issue(sends: Iterable[dict[str, Any]], has_unverified_sends: bool) -> list[dict[str, Any]]:
    candidates = []
    found_certain = False
    for item in sends:
        if not found_certain or item["remaining"] is None:
            candidates.append(item)
        if not item.get("ordering_unknown"):
            if not has_unverified_sends:
                break
            found_certain = True
    return candidates


def _fifo_feedback_candidates(queues: dict[tuple[str, str | None], list[dict[str, Any]]],
                              sequencer_id: str, has_unverified_sends: bool) -> list[dict[str, Any]]:
    by_issue = sorted((
        item
        for (_, owner), queue in queues.items()
        if owner is None or owner == sequencer_id
        for item in _possible_first_by_issue((item for item in _pending_feedback_sends(queue)
                                              if item["route_latency"] is None), has_unverified_sends)
    ), key=lambda item: item["order"])
    legacy = _possible_first_by_issue(by_issue, has_unverified_sends)
    routed = [item for item in legacy if item["self_cast_timing"] is None]
    # Compare self-cast words by arrival within the local queue. Competition
    # with inferred non-self routes keeps the existing source-order rule.
    include_self_cast = len(routed) != len(legacy)
    local = _first_timed_feedback_sends(item for (_, owner), queue in queues.items() if owner == sequencer_id
                                       for item in _pending_feedback_sends(queue)
                                       if include_self_cast or item["route_latency"] is not None)
    return local + routed


def _feedback_arrival_orders(item: dict[str, Any]) -> dict[tuple, int]:
    if item["remaining"] is None:
        return {}
    if item["route_latency"] is not None:
        completion = item["completion"]
        return {("routed_acquisition", completion[0]): completion[1] + item["route_latency"]} if completion is not None else {}
    timing = item["self_cast_timing"]
    data_type = timing["data_type"]
    latency = timing["latency_ns"]
    event = item["event"]
    orders = {}
    if timing["arrival_ns"] is not None:
        orders[("estimated",)] = timing["arrival_ns"]
    meta = event.get("meta", {})
    epoch = meta.get("rt_trigger_epoch", 0)
    if (epoch or meta.get("aligned_time_nominal")) and latency is not None:
        # Sends after the same unresolved wait share the same unknown offset.
        relative_time = _feedback_send_available_time(event, relative=True)
        if relative_time is not None:
            orders[("relative_rt", event["sequencer_id"], epoch)] = relative_time + latency
    if event["kind"] == "feedback_com" and data_type == "q1_register_or_immediate" and latency is not None:
        orders[("common_data", event["sequencer_id"], latency)] = item["event_order"]
    if latency is not None and data_type in {"thresholded_bits", "iq_values", "ttl_counts", "time_delta"}:
        # Streams from one readout integration or timetag close share an
        # emission time, even when its absolute estimate is unavailable.
        orders[("emission", event["id"])] = latency
    if event["kind"] == "acquire" and data_type in {"thresholded_bits", "iq_values"} and latency is not None:
        # The next readout acquire interrupts or follows the preceding
        # integration. The same data type keeps that order through transport.
        orders[("readout", event["sequencer_id"], data_type, latency)] = item["event_order"]
    return orders


def _relative_feedback_order(left: dict[tuple, int], right: dict[tuple, int]) -> int | None:
    difference = None
    for group, rank in left.items():
        if group in right:
            difference = rank - right[group]
            if difference:
                break
    return difference


def _first_timed_feedback_sends(sends: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = [(item, _feedback_arrival_orders(item)) for item in sends]
    first_certain: dict[tuple, int] = {}
    for item, orders in ranked:
        if not item.get("ordering_unknown"):
            for group, rank in orders.items():
                first_certain[group] = min(rank, first_certain.get(group, rank))
    return [item for item, orders in ranked if all(rank <= first_certain.get(group, rank) for group, rank in orders.items())]


def _discard_preceding_feedback(
    queues: dict[tuple[str, str | None], list[dict[str, Any]]],
    candidates: list[dict[str, Any]],
    sequencer_id: str,
    receive_channels: set[str],
    discarded: dict[str, int],
) -> list[dict[str, Any]]:
    uncertain = []
    target_orders = {item["order"] for item in candidates}
    target_ranks = [_feedback_arrival_orders(item) if item["self_cast_timing"] is not None or item["route_latency"] is not None
                    else None for item in candidates]
    target_exists = any(not item.get("ordering_unknown") for item in candidates)
    for (channel, owner), queue in queues.items():
        if owner is not None and owner != sequencer_id:
            continue
        # Self-cast arrives regardless of the IDs the program selects. For
        # other routes, only the existing inferred receiver set is known.
        if owner is None and receive_channels and "*" not in receive_channels and channel not in receive_channels:
            continue
        for item in _pending_feedback_sends(queue):
            if item["order"] in target_orders:
                continue
            differences = []
            ranks = _feedback_arrival_orders(item) if owner is not None else None
            for target, target_rank in zip(candidates, target_ranks):
                if item["remaining"] is None or target["remaining"] is None:
                    differences.append(None)
                elif ranks is not None and target_rank is not None:
                    differences.append(_relative_feedback_order(ranks, target_rank))
                elif item["route_latency"] is not None or target["route_latency"] is not None:
                    differences.append(None)
                else:
                    differences.append(item["order"] - target["order"])
            if target_exists and all(delta is not None and delta < 0 for delta in differences):
                if not item.get("ordering_unknown"):
                    discarded[channel] = discarded.get(channel, 0) + item["remaining"]
                item["remaining"] = 0
            elif any(delta is None or delta <= 0 for delta in differences):
                uncertain.append(item)
    return uncertain


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
