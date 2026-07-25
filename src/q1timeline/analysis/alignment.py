from __future__ import annotations

from dataclasses import dataclass, field

from q1timeline.analysis.interpreter import AnalysisState, Confidence, TimelineEvent
from q1timeline.analysis.values import Concrete, Value, add_values, multiply_value
from q1timeline.diagnostics import Diagnostic


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
    mode: str = "first_wait_sync",
    anchor_kinds: list[str] | tuple[str, ...] | None = None,
) -> AlignmentResult:
    normalized_anchor_kinds = tuple(anchor_kinds or ())
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
    if mode == "none" or mode.startswith("manual:"):
        return None
    if mode in {"first_wait_sync", "after_first_wait_sync"}:
        return _first_event(state, lambda event: event.kind == "wait_sync")
    if mode == "first_wait_trigger":
        return _first_event(state, lambda event: event.kind == "wait_trigger")
    if mode == "first_anchor":
        anchor_kind_set = set(anchor_kinds)
        return _first_event(state, lambda event: event.kind in anchor_kind_set)
    if mode == "first_marker_rise":
        marker_state_anchor = _first_marker_state_rise(state)
        if marker_state_anchor is not None:
            return marker_state_anchor
        return _first_event(state, _is_pending_marker_rise)
    if mode == "first_play":
        return _first_event(state, lambda event: event.kind == "play")
    if mode == "first_acquire":
        return _first_event(state, lambda event: event.kind == "acquire")
    if mode.startswith("label:"):
        label = _label_alignment_payload(mode)
        pc = state.labels.get(label)
        if pc is None:
            return None
        instruction = state.instructions_by_pc.get(pc)
        if instruction is None:
            return None
        return _first_event(
            state,
            lambda event: event.source.line == instruction.source.line
            and event.kind != "q1_issue",
        )
    return None


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
    alignment_metadata = {
        "mode": mode,
        "offset": _plain_offset(offset),
        "anchor_event_id": anchor.id if anchor is not None else None,
        "confidence": "unknown" if anchor_confidence_unknown else "exact",
        "unaligned": anchor is None and _mode_requires_anchor(mode),
    }
    if anchor_kinds:
        alignment_metadata["anchor_kinds"] = list(anchor_kinds)
    state.metadata["alignment"] = alignment_metadata
    for event in state.events:
        event.meta["local_t0"] = _plain_time(event.t0)
        event.meta["aligned_t0"] = _plain_time(add_values(event.t0, offset))
        if event.t1 is not None:
            event.meta["local_t1"] = _plain_time(event.t1)
            event.meta["aligned_t1"] = _plain_time(add_values(event.t1, offset))


def _first_event(state: AnalysisState, predicate) -> TimelineEvent | None:
    return next((event for event in state.events if predicate(event)), None)


def _anchor_confidence_unknown(mode: str, anchor: TimelineEvent | None) -> bool:
    if not _mode_requires_anchor(mode) or anchor is None:
        return _mode_requires_anchor(mode)
    anchor_time = anchor.t1 if mode == "after_first_wait_sync" else anchor.t0
    return not isinstance(anchor_time, Concrete)


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
