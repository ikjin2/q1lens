from __future__ import annotations

from typing import Any

from q1timeline.analysis.interpreter import AnalysisState, TimelineEvent
from q1timeline.analysis.values import Concrete


READOUT_OPS = {"acquire", "acquire_weighed", "acquire_weighted"}


def acquisition_windows(state: AnalysisState) -> dict[str, dict[str, Any]]:
    """Describe integration separately from instruction spacing, without changing events."""
    if "simulation" in state.metadata:
        return {}
    settings = state.metadata.get("hardware_parameters", {})
    configured = settings.get("integration_length_acq")
    if type(configured) is not int or configured <= 0:
        configured = None
    ops = {(item.source.file, item.source.line, item.source.column): item.op
           for item in state.instructions_by_pc.values()}
    acquisitions = [event for event in state.events if event.kind == "acquire"
                    and _op(event, ops) in READOUT_OPS]
    barriers = [event for event in state.events if event.kind in {"unknown_region", "branch_region"}]
    windows = {}
    for index, event in enumerate(acquisitions):
        duration = configured if _op(event, ops) == "acquire" else None
        next_event = acquisitions[index + 1] if index + 1 < len(acquisitions) else None
        windows[event.id] = _window(state, event, next_event, duration, barriers)
    return windows


def scope_acquisition_windows(state: AnalysisState) -> dict[str, dict[str, Any]]:
    """Only sequencer-triggered, explicitly selected readout scope captures are timed."""
    if "simulation" in state.metadata:
        return {}
    settings = state.metadata.get("hardware_parameters", {})
    index = state.metadata.get("sequencer_index")
    selected = settings.get("scope_acq_sequencer_select")
    if type(index) is not int or type(selected) is not int or index != selected:
        return {}
    paths = [path for path in (0, 1) if settings.get(f"scope_acq_trigger_mode_path{path}") == "sequencer"]
    if not paths:
        return {}
    # QRM/QRM-RF record 16384 samples at 1 GS/s; other hardware stays unknown.
    duration = 16384 if state.metadata.get("module_type") in {"QRM", "QRM-RF"} else None
    ops = {(item.source.file, item.source.line, item.source.column): item.op
           for item in state.instructions_by_pc.values()}
    acquisitions = [event for event in state.events if event.kind == "acquire" and _op(event, ops) in READOUT_OPS]
    barriers = [event for event in state.events if event.kind in {"unknown_region", "branch_region"}]
    windows = {}
    for index, event in enumerate(acquisitions):
        next_event = acquisitions[index + 1] if index + 1 < len(acquisitions) else None
        window = _window(state, event, next_event, duration, barriers)
        if window["status"] == "interrupted":
            window["status"] = "retriggered"
        windows[event.id] = {**window, "paths": paths}
    return windows


def _window(state: AnalysisState, event: TimelineEvent, next_event: TimelineEvent | None,
            duration: int | None, barriers: list[TimelineEvent]) -> dict[str, Any]:
    start = event.t0.value if isinstance(event.t0, Concrete) else None
    window: dict[str, Any] = {
        "start_ns": start, "end_ns": None,
        "configured_duration_ns": duration, "effective_duration_ns": None,
        "source": "hardware_parameters" if duration is not None else "unknown", "status": "unknown",
    }
    if start is None or duration is None or not _unambiguous(event):
        return window
    end = start + duration
    if next_event is not None:
        next_start = next_event.t0.value if isinstance(next_event.t0, Concrete) else None
        if next_start is None or next_start < start:
            return window
        if next_start < end:
            if not _unambiguous(next_event):
                return window
            end = next_start
            window["interrupted_by_event_id"] = next_event.id
    execution = state.metadata.get("execution", {})
    if (any(not isinstance(barrier.t0, Concrete) or start <= barrier.t0.value < end for barrier in barriers)
            or (execution.get("mode") == "playback" and not state.stopped
                and (not isinstance(state.rt_time_ns, Concrete) or end > state.rt_time_ns.value))):
        window.pop("interrupted_by_event_id", None)
        return window
    window.update(end_ns=end, effective_duration_ns=end - start,
                  status="interrupted" if "interrupted_by_event_id" in window else "completed")
    return window


def _op(event: TimelineEvent, ops: dict[tuple[str, int, int], str]) -> str | None:
    return ops.get((event.source.file, event.source.line, event.source.column), event.meta.get("op"))


def _unambiguous(event: TimelineEvent) -> bool:
    return event.confidence == "exact" and not any(event.meta.get(key) for key in (
        "loop_preview", "loop_previews", "preview_source_event_id", "assumed_branch_path",
        "branch_comparison_branch_id", "comparison_paths", "conditional_execution_unknown",
    ))
