from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable, Iterable
from typing import Any

from q1timeline.analysis.interpreter import AnalysisState, TimelineEvent
from q1timeline.analysis.values import Concrete


READOUT_OPS = {"acquire", "acquire_weighed", "acquire_weighted"}
CONFIGURED_INTEGRATION_SOURCES = {"hardware_parameters", "weight_memory"}
QRM_SCOPE_SAMPLES = 16_384
QRM_MODULE_TYPES = {"QRM", "QRM-RF"}
_SCOPE_SETTINGS = ("scope_acq_sequencer_select", "scope_acq_trigger_mode_path0", "scope_acq_trigger_mode_path1")
_RT_BARRIERS = {"unknown_region", "branch_region"}


def acquisition_windows(state: AnalysisState) -> dict[str, dict[str, Any]]:
    """Describe integration separately from instruction spacing, without changing events."""
    if "simulation" in state.metadata:
        return {}
    settings = state.metadata.get("hardware_parameters", {})
    weight_lengths = state.metadata.get("weight_lengths", {})
    configured = settings.get("integration_length_acq")
    if type(configured) is not int or configured <= 0:
        configured = None
    ops = {(item.source.file, item.source.line, item.source.column): item.op
           for item in state.instructions_by_pc.values()}
    acquisitions = [event for event in state.events if event.kind == "acquire"
                    and _op(event, ops) in READOUT_OPS]
    following, barriers, lower_bound = _window_order(state.events, acquisitions)
    faulted = _has_recorded_fault(state)
    windows = {}
    for event in acquisitions:
        duration = None
        source = "hardware_parameters"
        if _op(event, ops) == "acquire":
            duration = configured
            if duration is None and isinstance(event.duration, Concrete) and event.duration.value > 0:
                duration = event.duration.value
                source = "instruction_duration_fallback"
        else:
            indices = event.meta.get("weight_indices", [])
            if len(indices) == 2:
                first, second = (weight_lengths.get(index) for index in indices)
                if first is not None and first == second:
                    duration, source = first, "weight_memory"
        windows[event.id] = _window(state, event, following.get(event.id), duration, barriers, lower_bound,
                                    faulted=faulted, source=source)
    return windows


def qrm_scope_key(state: AnalysisState) -> tuple[str, str]:
    module = state.metadata.get("module")
    return ("module", module) if module else ("sequencer", state.sequencer_id)


def resolve_qrm_scope_settings(states: Iterable[AnalysisState]) -> tuple[
    dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], set[str]],
]:
    settings = {}
    conflicts = {}
    for state in states:
        key = qrm_scope_key(state)
        merged = settings.setdefault(key, {})
        hardware = state.metadata.get("hardware_parameters", {})
        for field in _SCOPE_SETTINGS:
            if field in hardware and merged.setdefault(field, hardware[field]) != hardware[field]:
                conflicts.setdefault(key, set()).add(field)
    return settings, conflicts


def scope_acquisition_windows(state: AnalysisState, settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Only sequencer-triggered, explicitly selected readout scope captures are timed."""
    if "simulation" in state.metadata:
        return {}
    paths = scope_trigger_paths(state.metadata.get("sequencer_index"), settings)
    if not paths:
        return {}
    # QRM/QRM-RF record 16384 samples at 1 GS/s; other hardware stays unknown.
    duration = QRM_SCOPE_SAMPLES if state.metadata.get("module_type") in QRM_MODULE_TYPES else None
    ops = {(item.source.file, item.source.line, item.source.column): item.op
           for item in state.instructions_by_pc.values()}
    acquisitions = [event for event in state.events if event.kind == "acquire" and _op(event, ops) in READOUT_OPS]
    following, barriers, lower_bound = _window_order(state.events, acquisitions)
    faulted = _has_recorded_fault(state)
    windows = {}
    for event in acquisitions:
        window = _window(state, event, following.get(event.id), duration, barriers, lower_bound, faulted=faulted)
        if window["status"] == "interrupted":
            window["status"] = "retriggered"
        windows[event.id] = {**window, "paths": paths}
    return windows


def scope_trigger_paths(index: int | None, settings: dict[str, Any]) -> list[int]:
    selected = settings.get("scope_acq_sequencer_select")
    if type(index) is not int or type(selected) is not int or index != selected:
        return []
    return [path for path in (0, 1) if settings.get(f"scope_acq_trigger_mode_path{path}") == "sequencer"]


def _window_order(events: list[TimelineEvent], acquisitions: list[TimelineEvent]) -> tuple[
    dict[str, TimelineEvent | None], list[TimelineEvent], Callable[[TimelineEvent, int], int | None],
]:
    following = {}
    next_event = None
    for event in reversed(acquisitions):
        if _baseline_rt(event):
            following[event.id] = next_event
            next_event = event
    positions = {}
    horizons: dict[int, list[tuple[int, int]]] = {}
    barriers = []
    for position, event in enumerate(events):
        if not _baseline_rt(event):
            continue
        positions[event.id] = position
        if event.kind in _RT_BARRIERS:
            barriers.append(event)
        elif (event.meta.get("rt_packet_id") and _unambiguous(event) and not event.meta.get("conditional_time_unknown")
              and isinstance(event.t0, Concrete) and isinstance(event.t1, Concrete) and event.t0.value <= event.t1.value):
            points = horizons.setdefault(_rt_epoch(event), [])
            if not points or points[-1][1] < event.t1.value:
                points.append((position, event.t1.value))

    def lower_bound(event: TimelineEvent, epoch: int) -> int | None:
        # Later nominal clocks do not change a prefix already established in this epoch.
        points = horizons.get(epoch, [])
        index = bisect_left(points, (positions[event.id],)) - 1
        return points[index][1] if index >= 0 else None

    return following, barriers, lower_bound


def _window(state: AnalysisState, event: TimelineEvent, next_event: TimelineEvent | None,
            duration: int | None, barriers: list[TimelineEvent], lower_bound: Callable[[TimelineEvent, int], int | None], *,
            faulted: bool, source: str = "hardware_parameters") -> dict[str, Any]:
    start = event.t0.value if isinstance(event.t0, Concrete) else None
    window: dict[str, Any] = {
        "start_ns": start, "end_ns": None,
        "configured_duration_ns": duration if source in CONFIGURED_INTEGRATION_SOURCES else None,
        "effective_duration_ns": None,
        "source": source if duration is not None else "unknown", "status": "unknown",
    }
    if event.meta.get("rt_trigger_epoch"):
        window["time_basis"] = "nominal"
    if faulted:
        # The recorded fault uses Q1 time. Analytical RT has no verified offset to that clock.
        if duration is not None:
            window["planned_duration_ns"] = duration
        return window
    if start is None or duration is None or not _baseline_rt(event) or not _unambiguous(event):
        return window
    epoch = _rt_epoch(event)
    end = start + duration
    if next_event is not None:
        if _rt_epoch(next_event) == epoch and isinstance(next_event.t0, Concrete):
            next_start = next_event.t0.value
            if next_start < start:
                return window
            if next_start < end:
                if not _unambiguous(next_event):
                    return window
                end = next_start
                window["interrupted_by_event_id"] = next_event.id
        else:
            bound = lower_bound(next_event, epoch)
            if bound is None or bound < end:
                return window
    execution = state.metadata.get("execution", {})
    if (any(_barrier_can_intersect(barrier, epoch, start, end, lower_bound) for barrier in barriers)
            or (execution.get("mode") == "playback" and not state.stopped
                and (not isinstance(state.rt_time_ns, Concrete) or end > state.rt_time_ns.value))):
        window.pop("interrupted_by_event_id", None)
        return window
    window.update(end_ns=end, effective_duration_ns=end - start,
                  status="interrupted" if "interrupted_by_event_id" in window else "completed")
    return window


def _barrier_can_intersect(barrier: TimelineEvent, epoch: int, start: int, end: int,
                           lower_bound: Callable[[TimelineEvent, int], int | None]) -> bool:
    barrier_epoch = _rt_epoch(barrier)
    if barrier_epoch < epoch:
        return False
    if barrier_epoch == epoch and isinstance(barrier.t0, Concrete):
        return start <= barrier.t0.value < end
    bound = lower_bound(barrier, epoch)
    return bound is None or bound < end


def _rt_epoch(event: TimelineEvent) -> int:
    return event.meta.get("rt_trigger_epoch", 0)


def _baseline_rt(event: TimelineEvent) -> bool:
    return event.lane.startswith("rt.") and not any(event.meta.get(key) for key in (
        "loop_preview", "preview_source_event_id", "branch_comparison_branch_id",
    ))


def _has_recorded_fault(state: AnalysisState) -> bool:
    return "fault" in state.metadata.get("execution", {}) or any(
        event.kind == "illegal" and "fault" in event.meta and _baseline_rt(event) for event in state.events
    )


def _op(event: TimelineEvent, ops: dict[tuple[str, int, int], str]) -> str | None:
    return ops.get((event.source.file, event.source.line, event.source.column), event.meta.get("op"))


def _unambiguous(event: TimelineEvent) -> bool:
    return event.confidence == "exact" and not any(event.meta.get(key) for key in (
        "loop_preview", "loop_previews", "preview_source_event_id", "assumed_branch_path",
        "branch_comparison_branch_id", "comparison_paths", "conditional_execution_unknown",
    ))
