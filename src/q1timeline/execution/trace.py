"""Timeline events, RT packet records and applied latch snapshots."""
from __future__ import annotations

from typing import Any

from q1timeline.analysis.values import Concrete, Unknown, Value, value_to_json
from q1timeline.diagnostics import Diagnostic
from q1timeline.q1asm import core_rt
from q1timeline.q1asm.ast import Instr, SourceLocation
from q1timeline.q1asm.instruction_table import get_instruction_spec
from q1timeline.execution.state import AnalysisState, Confidence, NetZeroWindow, RtPacket, TimelineEvent


def trace_q1_issue(state: AnalysisState, instruction: Instr, event: TimelineEvent, flags_before: dict | None) -> None:
    event.meta.update(source_pc=instruction.pc, execution_index=state.q1_issue_index,
                      flags_before=flags_before, flags_after=dict(state.alu_flags) if state.alu_flags is not None else None)


def diagnose_netzero_gap(
    state: AnalysisState, window: NetZeroWindow, pulse: TimelineEvent, start_ns: int,
) -> None:
    elapsed_ns = start_ns - window.start_ns
    if not 0 <= elapsed_ns < window.maxt_ns:
        return
    shortfall_ns = window.maxt_ns - elapsed_ns
    state.diagnostics.append(Diagnostic(
        severity="warning",
        category="possible_netzero_overlap",
        message=(f"The next pulse starts {shortfall_ns} ns before the maximum net-zero compensation time "
                 "has elapsed and may overlap the compensation pulse."),
        source=pulse.source,
        related_events=[window.event_id, pulse.id],
        details={"maxt_ns": window.maxt_ns, "elapsed_ns": elapsed_ns, "shortfall_ns": shortfall_ns,
                 "compensation_source": source_to_json(window.source)},
    ))


def emit_marker_state_event(
    state: AnalysisState,
    instruction: Instr,
    *,
    duration: Value,
    t0: Value,
    t1: Value | None,
    packet_id: str,
    value: Any,
) -> None:
    emit_event(
        state,
        lane="rt.marker",
        kind="marker_state",
        t0=t0,
        t1=t1,
        duration=duration,
        label=f"marker {value}",
        confidence=confidence_for_value((state.latched_state.marker, t0, t1)),
        source=instruction.source,
        meta={"field": "marker", "value": value, "rt_packet_id": packet_id},
    )


def append_packet(
    state: AnalysisState,
    instruction: Instr,
    duration: Value,
    q1_t0: Value,
    q1_t1: Value,
    rt_t0: Value | None,
    rt_t1: Value | None,
    packet_id: str | None,
    *, confidence: Confidence | None = None,
) -> RtPacket:
    packet = RtPacket(
        id=packet_id or f"{state.sequencer_id}:p{len(state.rt_packets)}",
        sequencer_id=state.sequencer_id,
        source=instruction.source,
        op=instruction.op,
        duration=duration,
        q1_issue_t0=q1_t0,
        q1_issue_t1=q1_t1,
        rt_t0=rt_t0,
        rt_t1=rt_t1,
        confidence=confidence or confidence_for_timing(duration, rt_t0, rt_t1),
        meta={"conditional_time_unknown": True,
              "output_state_unknown": get_instruction_spec(instruction.op).applies_latched_state}
        if state.conditional_time_unknown else {},
    )
    state.rt_packets.append(packet)
    if state.q1_fifo_stall_ns:
        packet.meta["q1_fifo_stall_ns"] = state.q1_fifo_stall_ns
    return packet


def emit_event(
    state: AnalysisState,
    *,
    lane: str,
    kind: str,
    t0: Value,
    t1: Value | None,
    duration: Value,
    label: str,
    confidence: Confidence,
    source: SourceLocation,
    meta: dict[str, Any] | None = None,
) -> TimelineEvent:
    event_meta = dict(meta or {})
    if state.rt_trigger_epoch and (lane.startswith("rt.") or lane == "state.pending") and "fault" not in event_meta:
        event_meta["rt_trigger_epoch"] = state.rt_trigger_epoch
    if state.conditional_time_unknown and kind != "q1_issue":
        event_meta["conditional_time_unknown"] = True
    if state.current_resolved_args and "resolved_args" not in event_meta:
        event_meta["resolved_args"] = state.current_resolved_args
    if state.record_trace:
        event_index = len(state.events)
    else:
        event_index = state.event_serial
        state.event_serial += 1
    event = TimelineEvent(
        id=f"{state.sequencer_id}:e{event_index}",
        sequencer_id=state.sequencer_id,
        lane=lane,
        kind=kind,
        t0=t0,
        t1=t1,
        duration=duration,
        label=label,
        confidence=confidence,
        source=source,
        meta=event_meta,
    )
    # Statistics still need the RT alignment anchor and hardware diagnostic evidence.
    if state.record_trace or kind in {"q1_issue", "acquire", "rt_queue_dequeue", "unknown_region", "latched_state_pending"} or (
        "scope_enable" in (event_meta.get("applied_state") or {})
    ):
        state.events.append(event)
    return event


def source_to_json(source: SourceLocation) -> dict[str, Any]:
    return {
        "file": source.file,
        "line": source.line,
        "column": source.column,
        "raw": source.raw,
    }


def consume_applied_state(state: AnalysisState, *, consume: bool = True) -> dict[str, Any]:
    applied: dict[str, Any] = {}
    pending = state.latched_state.pending_since
    fields, _ = core_rt.latch_commit_fields(pending, state.pending_phase_reset is True)
    for name in fields:
        value = True if name == "phase_reset" else getattr(state.latched_state, name)
        if value is not None:
            applied[name] = plain_value(value)
    if consume:
        pending.clear()
        state.latched_state.pending_event_ids.clear()
        clear_nco_one_shots(state)
    return applied


def clear_nco_one_shots(state: AnalysisState) -> None:
    pending = state.latched_state.pending_since.keys() | state.latched_state.pending_event_ids.keys()
    _, retained = core_rt.latch_commit_fields(pending, state.pending_phase_reset is True, skipped=True)
    state.pending_phase_reset = False
    state.latched_state.phase_delta = None
    for field in pending.difference(retained):
        state.latched_state.pending_since.pop(field, None)
        state.latched_state.pending_event_ids.pop(field, None)


def confidence_for_value(value: Any) -> Confidence:
    if isinstance(value, tuple):
        confidences = {confidence_for_value(item) for item in value}
        if "unknown" in confidences:
            return "unknown"
        if "runtime_dependent" in confidences:
            return "runtime_dependent"
        if "symbolic" in confidences:
            return "symbolic"
        return "exact"
    if isinstance(value, Concrete):
        return "exact"
    if isinstance(value, Unknown):
        return "unknown"
    if value.__class__.__name__ == "RuntimeDependent":
        return "runtime_dependent"
    if value.__class__.__name__ == "Symbolic":
        return "symbolic"
    return "unknown"


def confidence_for_timing(duration: Value, t0: Value | None, t1: Value | None) -> Confidence:
    return confidence_for_value((t0, t1, duration))


def plain_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return [plain_value(item) for item in value]
    if isinstance(value, Concrete):
        return value.value
    if isinstance(value, Value):
        return value_to_json(value)
    return value
