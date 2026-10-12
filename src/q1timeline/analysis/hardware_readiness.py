from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from q1timeline.analysis.interpreter import AnalysisState, TimelineEvent
from q1timeline.diagnostics import Diagnostic
from q1timeline.q1asm.ast import SourceLocation


_STATUS_PRIORITY = {"ready": 0, "incomplete": 1, "info": 2, "warning": 3}


def analyze_hardware_readiness(state: AnalysisState) -> dict[str, Any]:
    """Run conservative, opt-in checks over one completed interpreter state."""

    ambiguous = _has_ambiguous_execution(state)
    findings: list[Diagnostic] = []

    pending_fields = sorted(state.latched_state.pending_since)
    reused_bins: list[dict[str, int]] = []
    scope_without_acquisition = False

    if ambiguous:
        terminal_status = "skipped"
        scope_status = "skipped"
        bins_status = "skipped"
    else:
        terminal_status = "pass"
        if pending_fields:
            diagnostic = _pending_hardware_state_diagnostic(state, pending_fields)
            state.diagnostics.append(diagnostic)
            findings.append(diagnostic)
            terminal_status = "warning"

        scope_event = _enabled_scope_without_acquisition(state)
        scope_status = "pass"
        if scope_event is not None:
            diagnostic = Diagnostic(
                severity="info",
                category="scope_without_acquisition",
                message="Scope acquisition is enabled at program end, but no later acquisition is modeled.",
                source=_scope_setting_source(state, scope_event),
                related_events=[scope_event.id],
                details={"scope_enable": scope_event.meta["applied_state"]["scope_enable"]},
            )
            state.diagnostics.append(diagnostic)
            findings.append(diagnostic)
            scope_without_acquisition = True
            scope_status = "info"

        reused_bins, reuse_diagnostics = _reused_acquisition_bins(state)
        bins_status = "pass"
        if reuse_diagnostics:
            state.diagnostics.extend(reuse_diagnostics)
            findings.extend(reuse_diagnostics)
            bins_status = "info"

    counts = _diagnostic_counts(findings)
    status = _summary_status(counts, incomplete=ambiguous)
    summary: dict[str, Any] = {
        "status": status,
        "diagnostic_counts": counts,
        "checks": {
            "terminal_latched_state": {
                "status": terminal_status,
                "pending_fields": pending_fields if not ambiguous else [],
            },
            "scope_acquisition": {
                "status": scope_status,
                "scope_enabled_without_acquisition": scope_without_acquisition,
            },
            "acquisition_bins": {
                "status": bins_status,
                "reused_bins": reused_bins,
            },
        },
    }
    if ambiguous:
        summary["skipped_reason"] = "invalid input or runtime-dependent or incomplete control flow"
    state.metadata["hardware_readiness"] = summary
    return summary


def hardware_readiness_summary(states: list[AnalysisState]) -> dict[str, Any] | None:
    sequencers = []
    for state in states:
        summary = state.metadata.get("hardware_readiness")
        if isinstance(summary, dict):
            sequencers.append({"id": state.sequencer_id, **summary})
    if not sequencers:
        return None

    counts = {
        severity: sum(int(item["diagnostic_counts"].get(severity, 0)) for item in sequencers)
        for severity in ("error", "warning", "info", "hint")
    }
    status = max(
        (str(item.get("status", "incomplete")) for item in sequencers),
        key=lambda value: _STATUS_PRIORITY.get(value, _STATUS_PRIORITY["incomplete"]),
    )
    return {
        "enabled": True,
        "status": status,
        "diagnostic_counts": counts,
        "sequencers": sequencers,
    }


def _pending_hardware_state_diagnostic(
    state: AnalysisState,
    pending_fields: list[str],
) -> Diagnostic:
    stop_source = _safe_terminal_stop_source(state, pending_fields)
    source = stop_source or _latest_pending_source(state, pending_fields)
    details: dict[str, Any] = {
        "pending_fields": pending_fields,
        "pending_sources": {
            field: state.latched_state.pending_since[field]
            for field in pending_fields
        },
    }
    if stop_source is not None:
        point = {"line": stop_source.line, "column": stop_source.column}
        details["suggested_edit"] = {
            "title": "Apply pending hardware state before stop",
            "file": stop_source.file,
            "range": {"start": point, "end": dict(point)},
            "new_text": "upd_param 4\n",
        }
    return Diagnostic(
        severity="warning",
        category="pending_hardware_state",
        message=(
            "Program ends before applying pending hardware state: "
            + ", ".join(pending_fields)
            + "."
        ),
        source=source,
        related_events=_pending_event_ids(state, pending_fields),
        details=details,
    )


def _safe_terminal_stop_source(
    state: AnalysisState,
    pending_fields: list[str],
) -> SourceLocation | None:
    if not state.stopped or any(diagnostic.severity == "error" for diagnostic in state.diagnostics):
        return None
    q1_issue_events = [event for event in state.events if event.kind == "q1_issue"]
    if not q1_issue_events:
        return None
    terminal = q1_issue_events[-1]
    if terminal.meta.get("op") != "stop" or terminal.confidence != "exact":
        return None
    source = terminal.source
    if Path(source.file).suffix.lower() != ".q1asm":
        return None
    if any(
        not _same_file(state.latched_state.pending_since[field].file, source.file)
        for field in pending_fields
    ):
        return None
    if source.line < 1 or source.column < 1 or not _source_is_plain_stop(source):
        return None
    return source


def _source_is_plain_stop(source: SourceLocation) -> bool:
    tail = source.raw[source.column - 1 :]
    tail = tail.split("#", 1)[0].split("//", 1)[0].strip()
    return tail == "stop"


def _same_file(left: str, right: str) -> bool:
    if left.startswith("<") or right.startswith("<"):
        return left == right
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _latest_pending_source(state: AnalysisState, pending_fields: list[str]) -> SourceLocation:
    pending = set(pending_fields)
    for event in reversed(state.events):
        if event.kind == "latched_state_pending" and event.meta.get("field") in pending:
            return event.source
    return state.latched_state.pending_since[pending_fields[-1]]


def _pending_event_ids(state: AnalysisState, pending_fields: list[str]) -> list[str]:
    pending = set(pending_fields)
    latest_by_field: dict[str, str] = {}
    for event in state.events:
        field = event.meta.get("field")
        if event.kind == "latched_state_pending" and field in pending:
            latest_by_field[str(field)] = event.id
    return [latest_by_field[field] for field in pending_fields if field in latest_by_field]


def _enabled_scope_without_acquisition(state: AnalysisState) -> TimelineEvent | None:
    latest_scope_event: tuple[int, TimelineEvent] | None = None
    for index, event in enumerate(state.events):
        applied_state = event.meta.get("applied_state")
        if not isinstance(applied_state, dict) or "scope_enable" not in applied_state:
            continue
        value = applied_state["scope_enable"]
        if isinstance(value, int) and not isinstance(value, bool):
            latest_scope_event = (index, event)
        else:
            latest_scope_event = None
    if latest_scope_event is None:
        return None
    event_index, event = latest_scope_event
    if event.meta["applied_state"]["scope_enable"] == 0:
        return None
    if any(
        candidate.kind == "q1_issue"
        and str(candidate.meta.get("op", "")).startswith("acquire")
        for candidate in state.events[event_index:]
    ):
        return None
    if any(candidate.kind == "acquire" for candidate in state.events[event_index:]):
        return None
    return event


def _scope_setting_source(state: AnalysisState, applied_event: TimelineEvent) -> SourceLocation:
    applied_index = state.events.index(applied_event)
    for event in reversed(state.events[: applied_index + 1]):
        if event.kind == "latched_state_pending" and event.meta.get("field") == "scope_enable":
            return event.source
    return applied_event.source


def _reused_acquisition_bins(
    state: AnalysisState,
) -> tuple[list[dict[str, int]], list[Diagnostic]]:
    uses: dict[tuple[int, int], list[TimelineEvent]] = {}
    for event in state.events:
        if event.kind != "acquire":
            continue
        acquisition_index = event.meta.get("acquisition_index")
        bin_index = event.meta.get("bin_index")
        if (
            not isinstance(acquisition_index, int)
            or isinstance(acquisition_index, bool)
            or not isinstance(bin_index, int)
            or isinstance(bin_index, bool)
        ):
            continue
        uses.setdefault((acquisition_index, bin_index), []).append(event)

    reused: list[dict[str, int]] = []
    diagnostics: list[Diagnostic] = []
    for (acquisition_index, bin_index), events in sorted(uses.items()):
        if len(events) < 2:
            continue
        reused.append(
            {
                "acquisition_index": acquisition_index,
                "bin_index": bin_index,
                "use_count": len(events),
            }
        )
        diagnostics.append(
            Diagnostic(
                severity="info",
                category="acquisition_bin_reuse",
                message=(
                    f"Acquisition {acquisition_index} reuses concrete bin {bin_index} "
                    f"{len(events)} times on the modeled path."
                ),
                source=events[1].source,
                related_events=[event.id for event in events],
                details={
                    "acquisition_index": acquisition_index,
                    "bin_index": bin_index,
                    "use_count": len(events),
                    "sources": [event.source for event in events],
                },
            )
        )
    return reused, diagnostics


def _has_ambiguous_execution(state: AnalysisState) -> bool:
    if any(event.kind == "unknown_region" for event in state.events):
        return True
    if any(event.meta.get("branch_comparison_path") for event in state.events):
        return True
    if any(
        event.kind == "branch_region" and event.confidence != "exact"
        for event in state.events
    ):
        return True
    return any(
        diagnostic.severity == "error"
        or diagnostic.category in {"analysis_incomplete", "loop_truncated", "unresolved_branch"}
        for diagnostic in state.diagnostics
    )


def _diagnostic_counts(diagnostics: list[Diagnostic]) -> dict[str, int]:
    return {
        severity: sum(diagnostic.severity == severity for diagnostic in diagnostics)
        for severity in ("error", "warning", "info", "hint")
    }


def _summary_status(counts: dict[str, int], *, incomplete: bool) -> str:
    if counts["error"] or counts["warning"]:
        return "warning"
    if counts["info"] or counts["hint"]:
        return "info"
    return "incomplete" if incomplete else "ready"
