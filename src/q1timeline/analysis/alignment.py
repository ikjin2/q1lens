from __future__ import annotations

from dataclasses import dataclass, field
from itertools import chain

from q1timeline.analysis.interpreter import AnalysisState, Confidence, TimelineEvent
from q1timeline.analysis.values import Concrete, Value, add_values, multiply_value
from q1timeline.diagnostics import Diagnostic


SIMULATION_ALIGNMENT_MODE = "first_rt_dequeue"


@dataclass(frozen=True)
class AlignmentResult:
    mode: str
    sequencer_offsets: dict[str, Value]
    anchor_events: dict[str, str | None]
    confidence: Confidence
    diagnostics: list[Diagnostic] = field(default_factory=list)


def align_timelines(
    states: list[AnalysisState],
    *,
    mode: str | None = None,
    anchor_kinds: list[str] | tuple[str, ...] | None = None,
) -> AlignmentResult:
    normalized_anchor_kinds = tuple(anchor_kinds or ())
    if states and all("simulation" in state.metadata for state in states):
        return _align_simulation(states, mode or SIMULATION_ALIGNMENT_MODE, normalized_anchor_kinds)
    mode = mode or "first_wait_sync"
    offsets: dict[str, Value] = {}
    anchor_events: dict[str, str | None] = {}
    diagnostics: list[Diagnostic] = []
    missing_anchor = False
    incomplete_anchor_time = False

    for state in states:
        anchor = _find_anchor(state, mode, normalized_anchor_kinds)
        anchor_required = _mode_requires_anchor(mode)
        anchor_time_unknown = anchor is not None and anchor_required and _anchor_confidence_unknown(mode, anchor)
        anchor_events[state.sequencer_id] = anchor.id if anchor is not None else None
        if anchor is None and anchor_required:
            missing_anchor = True
            offset = Concrete(0)
            diagnostics.append(
                Diagnostic(
                    severity="warning",
                    category="alignment_missing",
                    message=f"Alignment anchor not found for sequencer {state.sequencer_id}: {mode}",
                    source=_fallback_source(state),
                    details=_alignment_diagnostic_details(
                        state.sequencer_id,
                        mode,
                        normalized_anchor_kinds,
                    ),
                )
            )
        else:
            offset = _offset_for_mode(mode, anchor)
            if anchor_time_unknown:
                incomplete_anchor_time = True

        offsets[state.sequencer_id] = offset
        _apply_alignment_metadata(state, mode, offset, anchor, normalized_anchor_kinds)

    confidence: Confidence = "unknown" if missing_anchor or incomplete_anchor_time else "exact"
    result = AlignmentResult(
        mode=mode,
        sequencer_offsets=offsets,
        anchor_events=anchor_events,
        confidence=confidence,
        diagnostics=diagnostics,
    )
    for state in states:
        state.diagnostics.extend(diagnostics_for_state(diagnostics, state.sequencer_id))
    return result


def _align_simulation(states: list[AnalysisState], mode: str, anchor_kinds: tuple[str, ...]) -> AlignmentResult:
    # Compare the actual anchor edge on the shared clock. Sequencer and event
    # IDs break ties independently of input order. Alternative paths do not
    # set the clock origin for the recorded baseline.
    def earliest(anchor_mode: str) -> TimelineEvent | None:
        candidates = (event for state in states for event in _anchor_candidates(state, anchor_mode, anchor_kinds, simulation=True)
                      if not event.meta.get("branch_comparison_path") and not _anchor_confidence_unknown(anchor_mode, event))
        return min(candidates, key=lambda event: (_anchor_time(anchor_mode, event).value, event.sequencer_id, event.id), default=None)

    anchor = earliest(mode) if _mode_requires_anchor(mode) else None
    diagnostics = []
    offset_mode = mode
    if anchor is None and _mode_requires_anchor(mode) and mode != SIMULATION_ALIGNMENT_MODE:
        offset_mode = SIMULATION_ALIGNMENT_MODE
        anchor = earliest(offset_mode)
        owner = min(states, key=lambda state: state.sequencer_id)
        fallback = "the first RT dequeue" if anchor else "simulation time zero"
        diagnostic = Diagnostic(
            severity="warning", category="alignment_missing", source=_fallback_source(owner),
            message=f"No concrete project-wide alignment anchor for {mode}. Using {fallback}.",
            details={**_alignment_diagnostic_details(owner.sequencer_id, mode, anchor_kinds), "fallback": offset_mode},
        )
        diagnostics.append(diagnostic)
        owner.diagnostics.append(diagnostic)
    offset = _offset_for_mode(offset_mode, anchor)
    for state in states:
        _apply_alignment_metadata(state, mode, offset, anchor, anchor_kinds)
        state.metadata["alignment"].update(clock="shared_simulation", confidence="exact", unaligned=False,
                                           anchor_sequencer_id=anchor.sequencer_id if anchor else None)
        if offset_mode != mode:
            state.metadata["alignment"]["fallback"] = offset_mode
    return AlignmentResult(mode, {state.sequencer_id: offset for state in states},
                           {state.sequencer_id: anchor.id if anchor else None for state in states}, "exact", diagnostics)


def diagnostics_for_state(diagnostics: list[Diagnostic], sequencer_id: str) -> list[Diagnostic]:
    return [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.details.get("sequencer_id") == sequencer_id
    ]


def _fallback_source(state: AnalysisState):
    if state.events:
        return state.events[0].source
    if state.instructions_by_pc:
        first_pc = min(state.instructions_by_pc)
        return state.instructions_by_pc[first_pc].source
    return None


def _find_anchor(
    state: AnalysisState,
    mode: str,
    anchor_kinds: tuple[str, ...] = (),
) -> TimelineEvent | None:
    return next(iter(_anchor_candidates(state, mode, anchor_kinds)), None)


def _anchor_candidates(state: AnalysisState, mode: str, anchor_kinds: tuple[str, ...], *, simulation: bool = False):
    if mode == "none" or mode.startswith("manual:"):
        return ()
    if mode == SIMULATION_ALIGNMENT_MODE:
        return (event for event in state.events if event.kind == "rt_queue_dequeue")
    if mode == "after_first_wait_sync":
        return (event for event in state.events if event.kind == "wait_sync" and (
            not simulation or ("observation_cutoff_ns" not in event.meta and not event.meta.get("interrupted_by_fault"))
        ))
    if mode == "first_wait_sync":
        return (event for event in state.events if event.kind == "wait_sync" or (
            simulation and event.kind == "sync_wait" and event.meta.get("pending") is True
        ))
    if mode == "first_wait_trigger":
        return (event for event in state.events if event.kind == "wait_trigger" or (
            simulation and event.kind == "trigger_wait" and event.meta.get("pending") is True
        ))
    if mode == "first_anchor":
        anchor_kind_set = set(anchor_kinds)
        return (event for event in state.events if event.kind in anchor_kind_set)
    if mode == "first_marker_rise":
        marker_state_anchor = _first_marker_state_rise(state)
        if marker_state_anchor is not None:
            return (marker_state_anchor,)
        return () if simulation else (event for event in state.events if _is_pending_marker_rise(event))
    if mode == "first_play":
        return (event for event in state.events if event.kind == "play")
    if mode == "first_acquire":
        return (event for event in state.events if event.kind == "acquire")
    if mode.startswith("label:"):
        label = _label_alignment_payload(mode)
        pc = state.labels.get(label)
        if pc is None:
            return ()
        instruction = state.instructions_by_pc.get(pc)
        if instruction is None:
            return ()
        return (
            event for event in state.events
            if event.kind != "q1_issue"
            and event.source.file == instruction.source.file and event.source.line == instruction.source.line
            and (not simulation or (
                event.lane.startswith("rt.")
                and ("rt_execution_ns" in event.meta or event.kind in {"stop", "sync_wait", "trigger_wait"})
            ))
        )
    return ()


def _label_alignment_payload(mode: str) -> str:
    return mode.split(":", 1)[1].strip()


def _offset_for_anchor(anchor: TimelineEvent, mode: str) -> Value:
    return _offset_for_mode(mode, anchor)


def _offset_for_mode(mode: str, anchor: TimelineEvent | None = None) -> Value:
    if mode == "none":
        return Concrete(0)
    if mode.startswith("manual:"):
        return Concrete(int(mode.split(":", 1)[1]))
    if anchor is None:
        return Concrete(0)
    if mode == "after_first_wait_sync" and anchor.t1 is not None:
        return multiply_value(anchor.t1, -1)
    if isinstance(anchor.t0, Concrete):
        return Concrete(-anchor.t0.value)
    return Concrete(0)


def _mode_requires_anchor(mode: str) -> bool:
    return mode != "none" and not mode.startswith("manual:")


def _is_marker_high(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value != 0


def _is_pending_marker_rise(event: TimelineEvent) -> bool:
    return (
        event.kind == "latched_state_pending"
        and event.meta.get("field") == "marker"
        and _is_marker_high(event.meta.get("value"))
    )


def _first_marker_state_rise(state: AnalysisState) -> TimelineEvent | None:
    previous_high = False
    for event in state.events:
        if event.kind != "marker_state" or event.meta.get("field") != "marker":
            continue
        current_high = _is_marker_high(event.meta.get("value"))
        if current_high and not previous_high:
            return event
        previous_high = current_high
    return None


def _apply_alignment_metadata(
    state: AnalysisState,
    mode: str,
    offset: Value,
    anchor: TimelineEvent | None,
    anchor_kinds: tuple[str, ...] = (),
) -> None:
    anchor_confidence_unknown = _anchor_confidence_unknown(mode, anchor)
    nominal_anchor = bool(anchor and anchor.meta.get("rt_trigger_epoch"))
    alignment_metadata = {
        "mode": mode,
        "offset": _plain_offset(offset),
        "anchor_event_id": anchor.id if anchor is not None else None,
        "confidence": "unknown" if anchor_confidence_unknown else "exact",
        "unaligned": anchor is None and _mode_requires_anchor(mode),
    }
    if anchor_kinds:
        alignment_metadata["anchor_kinds"] = list(anchor_kinds)
    if nominal_anchor:
        alignment_metadata["time_basis"] = "nominal"
    state.metadata["alignment"] = alignment_metadata
    for event in chain(state.events, getattr(state, "observed_loop_events", ())):
        if nominal_anchor and (event.lane.startswith("rt.") or event.lane == "state.pending") and "fault" not in event.meta:
            event.meta["aligned_time_nominal"] = True
        else:
            event.meta.pop("aligned_time_nominal", None)
        event.meta["local_t0"] = _plain_time(event.t0)
        event.meta["aligned_t0"] = _plain_time(add_values(event.t0, offset))
        if event.t1 is not None:
            event.meta["local_t1"] = _plain_time(event.t1)
            event.meta["aligned_t1"] = _plain_time(add_values(event.t1, offset))


def _anchor_time(mode: str, anchor: TimelineEvent) -> Value | None:
    return anchor.t1 if mode == "after_first_wait_sync" else anchor.t0


def _anchor_confidence_unknown(mode: str, anchor: TimelineEvent | None) -> bool:
    if not _mode_requires_anchor(mode) or anchor is None:
        return _mode_requires_anchor(mode)
    return bool(anchor.meta.get("rt_trigger_epoch")) or not isinstance(_anchor_time(mode, anchor), Concrete)


def _plain_offset(value: Value):
    return value.value if isinstance(value, Concrete) else str(value)


def _plain_time(value: Value):
    return value.value if isinstance(value, Concrete) else str(value)


def _alignment_diagnostic_details(
    sequencer_id: str,
    mode: str,
    anchor_kinds: tuple[str, ...],
) -> dict[str, object]:
    details: dict[str, object] = {"sequencer_id": sequencer_id, "mode": mode}
    if anchor_kinds:
        details["anchor_kinds"] = list(anchor_kinds)
    return details
