from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

from q1timeline.analysis.interpreter import AnalysisState, _physical_read_register, _register_write_operands
from q1timeline.analysis.queue_timing import ASSUMED_INITIAL_BUFFER_NS, POSSIBLE_INITIAL_BUFFER_NS, replay_queues
from q1timeline.analysis.values import Concrete, Unknown, subtract_values
from q1timeline.diagnostics import Diagnostic
from q1timeline.diagnostic_catalog import underflow_label
from q1timeline.q1asm.instruction_table import get_instruction_spec, rt_duration_arg_for_instruction


UnderflowStatus = Literal[
    "definite_underflow",
    "possible_underflow",
    "not_detected_under_current_assumptions",
    "analysis_incomplete",
    "possibly_safe_with_initial_buffer",
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
    """Check assumed 1 ms and tentative 2 ms initial timing reserves.

    The reserve is consumed by Q1 supply gaps, not added as RT startup idle.
    SYNQ dwell remains uncertain; original Q1/RT coordinates stay unchanged.
    """
    prepared = {state.sequencer_id: _queue_packets(state) for state in states}
    cadences = {}
    all_path_safety = {}
    if len(states) == 1:
        state = states[0]
        safety = _all_path_queue_safety(state, queue_depth_limit)
        if safety is not None:
            all_path_safety[state.sequencer_id] = safety
        certification = _infinite_wait_cadence(state, prepared[state.sequencer_id])
        if certification is not None:
            prepared[state.sequencer_id], cadences[state.sequencer_id] = certification
    streams = {}
    for name, (packets, reason, boundary_packet) in prepared.items():
        streams[name] = list(packets)
        # A truncated trace is not a completed Q1 program: in particular it
        # cannot authorize an optimistic SYNQ release as if prefetch finished.
        boundary = boundary_packet or (packets[-1] if packets else None)
        if reason and boundary is not None:
            streams[name].append(replace(boundary, duration=Unknown(reason)))
    earliest = replay_queues(streams, queue_depth_limit=queue_depth_limit, initial_buffer_ns=ASSUMED_INITIAL_BUFFER_NS)
    prefetched = replay_queues(streams, queue_depth_limit=queue_depth_limit, prefill_sync=True,
                              initial_buffer_ns=POSSIBLE_INITIAL_BUFFER_NS)
    results = []
    assumptions = {
        "queue_depth_limit": queue_depth_limit,
        "startup": "first_packet_enqueue; no_added_idle",
        "initial_buffer_assumed_ns": ASSUMED_INITIAL_BUFFER_NS,
        "initial_buffer_possible_ns": POSSIBLE_INITIAL_BUFFER_NS,
        "initial_buffer_basis": "user_assumption; not_measured",
        "buffer_duration": "one_time_cumulative_q1_supply_gap_credit; never_replenished",
        "same_time_order": "enqueue_before_dequeue",
        "sync": "all_supplied_sequencers; earliest_release_to_maximum_prefetch",
        "unknown_branches": "all_feasible_paths_verified" if all_path_safety else "incomplete",
        "wait_trigger": "external_arrival_unknown",
        "alignment": "queue_model_separate_from_display_coordinates",
    }
    for state in states:
        name = state.sequencer_id
        low, high = earliest[name], prefetched[name]
        _packets, reason, boundary_packet = prepared[name]
        failure = high.failure if low.failure and high.failure else None
        cadence = cadences.get(name)
        safety = all_path_safety.get(name)
        eventual = cadence is not None and cadence["q1_period_ns"] > cadence["rt_period_ns"] and failure is None
        replay = high if low.failure else low
        if failure or eventual:
            status = "definite_underflow"
        elif safety is not None:
            status = "not_detected_under_current_assumptions"
            reason = None
        elif reason or low.incomplete_reason or high.incomplete_reason:
            status = "analysis_incomplete"
            reason = reason or low.incomplete_reason or high.incomplete_reason
        elif low.failure and not high.failure:
            status = "possibly_safe_with_initial_buffer"
        elif high.failure:
            status = "analysis_incomplete"
            reason = "startup_prefill_uncertain"
            boundary_packet = boundary_packet or high.failure.packet
        else:
            status = "not_detected_under_current_assumptions"
        initial_buffer = {
            "assumed_ns": ASSUMED_INITIAL_BUFFER_NS, "possible_ns": POSSIBLE_INITIAL_BUFFER_NS,
            "required_ns": None if eventual else replay.required_buffer_ns,
            **({"required_unbounded": True} if eventual else {}),
            "status": "exhausted" if failure or eventual else "tentative" if status == "possibly_safe_with_initial_buffer"
                      else "incomplete" if status == "analysis_incomplete" else "assumed",
        }
        if safety is not None:
            initial_buffer.update(required_ns=max(safety.values()), required_basis="all_feasible_paths")
        state.metadata["underflow"] = {"assumptions": assumptions, "status": status, "initial_buffer": initial_buffer}
        if safety is not None:
            state.metadata["underflow"]["all_paths_safe"] = {"required_buffer_by_path_ns": safety}
        if cadence is not None:
            state.metadata["underflow"]["infinite_cadence"] = cadence
        if state.record_trace:
            for record in replay.records:
                packet = record.packet
                _emit_queue_depth(state, packet_id=packet.id, t0=packet.rt_t0,
                                  source=packet.source, depth=record.depth)
                _emit_slack(state, packet_id=packet.id, slack_ns=record.dequeue_ns-record.enqueue_ns,
                            t0=packet.rt_t0, source=packet.source)
                state.events[-1].meta.update(enqueue_ns=record.enqueue_ns, dequeue_ns=record.dequeue_ns)
        if failure:
            packet = failure.packet
            if state.record_trace:
                _emit_queue_depth(state, packet_id=packet.id, t0=packet.rt_t0, source=packet.source, depth=0)
                _emit_slack(state, packet_id=packet.id, slack_ns=failure.slack_ns, t0=packet.rt_t0, source=packet.source)
            _emit_underflow_warning(state, packet_id=packet.id, slack_ns=failure.slack_ns,
                                    t0=packet.rt_t0, source=packet.source, assumptions=assumptions, status=status)
        elif eventual:
            loop = next(event for event in state.events if event.id == cadence["loop_event_id"])
            event = replace(_event_like(
                state, lane="debug.underflow", kind="underflow_warning", t0=Unknown("eventual RT underflow time"),
                source=loop.source, label="Eventual RT underflow",
                meta={"status": status, "eventual": True, "analysis_scope": "rt_queue", **cadence, "assumptions": assumptions},
            ), t1=None, duration=Concrete(0))
            state.events.append(event)
            state.diagnostics.append(Diagnostic(
                severity="error", category="definite_underflow",
                message=(f"Infinite loop eventually exhausts any finite initial buffer: Q1 supplies one packet every "
                         f"{cadence['q1_period_ns']} ns while RT consumes it every {cadence['rt_period_ns']} ns. "
                         "The exact failure iteration and time are not determined."),
                source=loop.source, related_events=[loop.id], details=event.meta,
            ))
        elif status == "possibly_safe_with_initial_buffer":
            state.diagnostics.append(Diagnostic(
                severity="info", category="initial_rt_buffer_estimate",
                message="Q1 supply gaps exceed the assumed 1 ms buffer, but may be covered by an initial buffer up to 2 ms.",
                source=low.failure.packet.source,
                details={"analysis_scope": "rt_queue", **initial_buffer},
            ))
        elif status == "analysis_incomplete":
            _emit_analysis_incomplete(state, assumptions, packet=boundary_packet, reason=reason)
        _attach_loop_queue_slack_summaries(state)
        results.append(UnderflowResult(status=status, assumptions=assumptions))
    return results


def _all_path_queue_safety(state: AnalysisState, queue_depth_limit: int):
    if set(state.queue_alternatives) != {"taken", "fallthrough"}:
        return None
    required = {}
    for path, alternative in state.queue_alternatives.items():
        packets, reason, _boundary = _queue_packets(alternative)
        if (not alternative.stopped or reason is not None
                or any(packet.op in {"wait_sync", "wait_trigger"} for packet in packets)):
            return None
        amounts = []
        for initial in (ASSUMED_INITIAL_BUFFER_NS, POSSIBLE_INITIAL_BUFFER_NS):
            replay = replay_queues({state.sequencer_id: packets}, queue_depth_limit=queue_depth_limit,
                                   initial_buffer_ns=initial)[state.sequencer_id]
            if replay.failure is not None or replay.incomplete_reason is not None:
                return None
            amounts.append(replay.required_buffer_ns)
        required[path] = max(amounts)
    return required


def _infinite_wait_cadence(state: AnalysisState, prepared):
    packets, reason, _boundary = prepared
    loops = [event for event in state.events if event.kind == "loop_block"]
    if (reason != "compact_loop_timing_unexpanded" or len(loops) != 1 or not packets
            or any(packet.op in {"wait_sync", "wait_trigger"} for packet in packets)
            or any(item.severity == "error" and item.category != "definite_underflow"
                   or item.category == "register_not_ready" for item in state.diagnostics)):
        return None
    loop = loops[0]
    meta = loop.meta
    edge_instruction = state.instructions_by_pc.get(meta.get("end_pc"))
    body = [instruction for pc, instruction in state.instructions_by_pc.items()
            if meta.get("start_pc", -1) <= pc < meta.get("end_pc", -1)]
    waits = [item for item in body if item.op == "wait"]
    by_id = {event.id: event for event in state.events}
    issues = [by_id[key] for key in meta.get("first_iteration_event_ids", [])
              if key in by_id and by_id[key].kind == "q1_issue"]
    edge = by_id.get(meta.get("loop_q1_issue_event_id"))
    if (meta.get("count") != "forever" or edge_instruction is None or edge_instruction.op != "jmp"
            or len(waits) != 1 or any(item.op not in {"nop", "wait"} for item in body)
            or not issues or edge is None or not isinstance(edge.t1, Concrete)
            or any(not isinstance(issue.t0, Concrete) or issue.meta.get("conditional_active") for issue in issues)):
        return None
    body_packet_ids = {issue.meta.get("rt_packet_id") for issue in issues}
    body_packets = [packet for packet in packets if packet.id in body_packet_ids]
    if len(body_packets) != 1 or body_packets[0] != packets[-1]:
        return None
    packet = body_packets[0]
    q1_period = edge.t1.value - min(issue.t0.value for issue in issues)
    duration = packet.duration.value if isinstance(packet.duration, Concrete) else None
    if q1_period <= 0 or type(duration) is not int or not 0 < duration <= 65535:
        return None
    second = replace(packet, id=f"{packet.id}:cadence1", q1_issue_t0=Concrete(packet.q1_issue_t0.value + q1_period),
                     q1_issue_t1=Concrete(packet.q1_issue_t1.value + q1_period),
                     rt_t0=Concrete(packet.rt_t0.value + duration), rt_t1=Concrete(packet.rt_t1.value + duration))
    cadence = {"loop_event_id": loop.id, "q1_period_ns": q1_period, "rt_period_ns": duration,
               "tail_status": "sustainable" if q1_period <= duration else "eventual_underflow"}
    return (packets + [second], None, None), cadence


def _queue_packets(state: AnalysisState):
    """Expand constant loop timing; stop before an unmodeled timing boundary."""
    packets = list(state.rt_packets)
    if not packets and not any(get_instruction_spec(item.op).emits_rt_packet
                               or get_instruction_spec(item.op).category == "unknown"
                               for item in state.instructions_by_pc.values()):
        return [], None, None
    cutoff = None
    reason = None
    for event in state.events:
        if event.kind != "q1_issue" or not isinstance(event.t0, Concrete):
            continue
        boundary_reason = None
        spec = get_instruction_spec(event.meta.get("op", ""))
        if (spec.category == "unknown"
                or spec.timing_effect_status != "known" and spec.op != "set_cond"):
            boundary_reason = "unmodeled_timing_effect"
        elif event.meta.get("conditional_execution_unknown") and not event.meta.get("conditional_timing_known"):
            boundary_reason = "conditional_execution_unknown"
        elif event.meta.get("op") in {"fb_pop_data", "fb_pull_data"}:
            boundary_reason = "feedback_arrival_unknown"
        elif event.meta.get("branch_decision") == "runtime_dependent" and not event.meta.get("branch_join_proven"):
            boundary_reason = "runtime_branch_unknown"
        elif event.meta.get("reason") in {
            "invalid_loop_count", "register_branch_target", "undefined_label", "unknown_numeric_branch_target",
            "unsupported_backward_conditional_branch", "unsupported_backward_numeric_branch", "unsupported_forward_loop",
        }:
            boundary_reason = event.meta["reason"]
        if boundary_reason and (cutoff is None or event.t0.value < cutoff):
            cutoff, reason = event.t0.value, boundary_reason
    if not packets:
        return [], reason, None
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
        if count == 1:
            # The real first iteration is already in the packet stream.
            continue
        instructions = [instruction for pc, instruction in state.instructions_by_pc.items()
                        if meta.get("start_pc", -1) <= pc < meta.get("end_pc", -1)]
        # Replay needs invariant durations and fixed instruction costs. Output
        # payloads may change without changing either timing quantity.
        constant_ops = {"wait", "upd_param", "play", "nop", "set_mrk",
                        "set_awg_gain", "set_awg_offs", "set_freq", "reset_ph", "set_ph", "set_ph_delta"}
        written_registers = {meta.get("counter_reg")}
        for instruction in instructions:
            written_registers.update(_register_write_operands(instruction, state))
        repeatable = True
        affine_packets = meta.get("rt_packet_affine", {})
        affine_period = meta.get("period_affine", {})
        affine = (bool(affine_packets) and all(packet.id in affine_packets for packet in loop_packets)
                  and all(type(affine_period.get(key)) is int for key in ("intercept", "slope")))
        periods = meta.get("iteration_durations")
        evaluated_packets = meta.get("rt_packet_durations", {})
        evaluated = (isinstance(count, int) and isinstance(periods, list) and 1 < len(periods) <= count
                     and (len(periods) == count or meta.get("repeat_last_iteration") is True)
                     and all(type(value) is int and value >= 0 for value in periods)
                     and all(isinstance(evaluated_packets.get(packet.id), list)
                             and len(evaluated_packets[packet.id]) == len(periods)
                             and all(type(value) is int and 0 <= value <= 65535 for value in evaluated_packets[packet.id])
                             for packet in loop_packets)
                     and all(sum(evaluated_packets[packet.id][index] for packet in loop_packets) == value
                             for index, value in enumerate(periods)))
        for instruction in instructions:
            spec = get_instruction_spec(instruction.op)
            if spec.category == "classical" and len({signature.q1_time_ns for signature in spec.signatures}) == 1:
                if meta.get("counter_reg") not in _register_write_operands(instruction, state):
                    continue
            elif instruction.op in constant_ops:
                duration_index = rt_duration_arg_for_instruction(spec, instruction)
                if duration_index is None:
                    continue
                if duration_index >= len(instruction.args):
                    repeatable = False
                    break
                arg = instruction.args[duration_index]
                if (instruction.pc in meta.get("invariant_duration_pcs", [])
                        or _physical_read_register(arg, state) not in written_registers):
                    continue
            repeatable = False
            break
        if (not isinstance(count, int) or count < 1 or not isinstance(period, int) or
                not (repeatable or affine or evaluated) or any(event.kind == "loop_block" for event in body) or
                len(packets) + len(loop_packets) * (count-1) > 10000):
            edge = events_by_id.get(meta.get("loop_q1_issue_event_id"))
            hidden_start = max(event.t1.value for event in q1)
            if edge is not None and isinstance(edge.t1, Concrete):
                hidden_start = max(hidden_start, edge.t1.value)
            if cutoff is None or hidden_start < cutoff:
                cutoff, reason = hidden_start, "compact_loop_timing_unexpanded"
            continue
        q1_period = max(event.t1.value for event in q1)-start + meta.get("loop_q1_jump_runtime_ns", 24)
        evaluated_elapsed = periods[0] if evaluated else 0
        for iteration in range(1, count):
            prefix_slope = 0
            evaluated_prefix = 0
            for packet in loop_packets:
                if not all(isinstance(value, Concrete) for value in
                           (packet.q1_issue_t0, packet.q1_issue_t1, packet.rt_t0, packet.rt_t1)):
                    cutoff, reason = start, "unknown_rt_timing"
                    break
                duration = packet.duration
                offset = iteration * period
                if affine:
                    coefficients = affine_packets[packet.id]
                    duration = Concrete(coefficients["intercept"] + coefficients["slope"] * iteration)
                    offset = (iteration * affine_period["intercept"]
                              + affine_period["slope"] * iteration * (iteration - 1) // 2 + prefix_slope * iteration)
                    prefix_slope += coefficients["slope"]
                if evaluated:
                    index = min(iteration, len(periods) - 1)
                    duration = Concrete(evaluated_packets[packet.id][index])
                    rt_t0 = Concrete(loop.t0.value + evaluated_elapsed + evaluated_prefix)
                    evaluated_prefix += duration.value
                else:
                    rt_t0 = Concrete(packet.rt_t0.value + offset)
                packets.append(replace(packet, id=f"{packet.id}:iteration{iteration}", duration=duration,
                    q1_issue_t0=Concrete(packet.q1_issue_t0.value+iteration*q1_period),
                    q1_issue_t1=Concrete(packet.q1_issue_t1.value+iteration*q1_period),
                    rt_t0=rt_t0, rt_t1=Concrete(rt_t0.value + duration.value)))
            if evaluated:
                evaluated_elapsed += periods[min(iteration, len(periods) - 1)]
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
        "estimated_depth": 0,
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
        label=underflow_label(meta),
        meta=meta,
    )
    state.events.append(event)
    state.diagnostics.append(
        Diagnostic(
            severity="error" if status == "definite_underflow" else "warning",
            category=status,
            message=f"{underflow_label(meta)}.",
            source=source,
            related_events=[event.id],
            details=details,
        )
    )


def _emit_analysis_incomplete(state: AnalysisState, assumptions: dict[str, object], *, packet=None, reason="unknown_rt_timing") -> None:
    source = (packet.source if packet is not None else state.rt_packets[0].source if state.rt_packets
              else next(event.source for event in reversed(state.events) if event.kind == "q1_issue"))
    t0 = packet.rt_t0 if packet is not None and isinstance(packet.rt_t0, Concrete) else Concrete(0)
    meta: dict[str, Any] = {
        "status": "analysis_incomplete",
        "reason": reason,
        "assumptions": assumptions,
    }
    details: dict[str, Any] = {"assumptions": assumptions, "reason": reason}
    if reason in {"startup_prefill_uncertain", "feedback_arrival_unknown", "runtime_branch_unknown", "compact_loop_timing_unexpanded"}:
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
            message=("Underflow depends on the unknown initial RT queue prefill or synchronization dwell; the startup scenarios disagree."
                     if reason == "startup_prefill_uncertain" else f"Underflow analysis incomplete: {reason.replace('_', ' ')}."),
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


def _event_like(state: AnalysisState, *, lane: str, kind: str, t0: Concrete | Unknown, source, label: str, meta: dict):
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
