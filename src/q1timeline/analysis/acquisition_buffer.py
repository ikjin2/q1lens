from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from q1timeline.analysis.interpreter import AnalysisState, TimelineEvent
from q1timeline.analysis.values import Concrete
from q1timeline.diagnostics import Diagnostic
from q1timeline.q1asm.instruction_table import get_instruction_spec


FAQ = "https://docs.qblox.com/en/2026.10.1/products/qblox_instruments/faq.html"
BURST_CREDITS = 7
READOUT_OPS = {"acquire", "acquire_weighted", "acquire_weighed"}
_REPEATABLE_OPS = READOUT_OPS | {
    "wait", "upd_param", "play", "nop", "set_mrk", "set_awg_gain", "set_awg_offs",
    "set_freq", "set_ph", "set_ph_delta", "reset_ph",
}


@dataclass
class _Span:
    start: int
    acquisitions: list[tuple[int, TimelineEvent]]
    period: int = 0
    count: int | None = 1


def analyze_acquisition_buffer(
    state: AnalysisState, *, module_type: str | None = None, acquisition_sequencers: int | None = None,
) -> tuple[dict[str, Any] | None, list[Diagnostic]]:
    """Check a seven-acquisition burst envelope, not an undocumented FIFO layout.

    A finite excess is a warning: the FAQ does not specify an upper bound on
    physical capacity or its exact service phase. Only a repeatable infinite
    overload on a confirmed QRM profile proves eventual exhaustion.
    """
    ops = {(i.source.file, i.source.line, i.source.column): i.op for i in state.instructions_by_pc.values()}
    acquisitions = [e for e in state.events if e.kind == "acquire"
                    and ops.get((e.source.file, e.source.line, e.source.column)) in READOUT_OPS]
    if not acquisitions:
        return None, []
    if any(d.severity == "error" for d in state.diagnostics):
        return {"status": "skipped", "reason": "invalid program or earlier execution error"}, []

    profile_confirmed = module_type in {"QRM", "QRM-RF"}
    peers_known = type(acquisition_sequencers) is int and 1 <= acquisition_sequencers <= 6
    # Other sequencers may stop or become idle. Their presence can reduce the
    # guaranteed share, but cannot prove this sequencer stays at that slow rate.
    fastest = 300
    slowest = 300 * ((acquisition_sequencers + 1) // 2) if peers_known else 900
    spans, uncertain = _acquisition_spans(state, acquisitions)
    _, low_loop, _ = _required_work(spans, fastest)
    high, high_loop, peak_source = _required_work(spans, slowest)
    credits = None if high is None else (high + slowest - 1) // slowest
    burst_bound = _burst_bound(state, acquisitions) if uncertain else None
    if burst_bound is not None and burst_bound <= BURST_CREDITS:
        # Even zero recovery between these requests fits the documented burst.
        credits, uncertain, low_loop, high_loop = burst_bound, None, None, None
    details: dict[str, Any] = {
        "module_type": module_type, "profile_confirmed": profile_confirmed,
        "acquisition_sequencers": acquisition_sequencers if peers_known else None,
        "service_period_ns": fastest, "conservative_service_period_ns": slowest,
        "guaranteed_burst_acquisitions": BURST_CREDITS,
        "required_burst_credits": credits,
        "model": "constant_service_burst_envelope", "reference": FAQ,
        "assumptions": [
            "Initially empty acquisition backlog; service runs while work is pending.",
            "Seven acquisitions are a documented burst allowance, not a measured FIFO capacity.",
            "Service rates refer to acquisition sequencers in the supplied module workload.",
            "The optimistic rate allows other sequencers to become idle; the conservative rate includes their contention.",
            "Times are modeled RT acquisition starts; finite FIFO overflow times are not predicted.",
        ],
    }
    if uncertain:
        details["uncertain_reason"] = uncertain
    if low_loop is not None and profile_confirmed and not uncertain:
        details.update(period_ns=low_loop.period, acquisitions_per_period=len(low_loop.acquisitions))
        diagnostic = Diagnostic(
            severity="error", category="acquisition_buffer_sustained_overload",
            message=(f"Repeating acquisition load ({len(low_loop.acquisitions)} per {low_loop.period} ns) "
                     f"exceeds the documented sustained service rate of one per {fastest} ns; "
                     "continued execution eventually exhausts any finite acquisition buffer."),
            source=low_loop.acquisitions[0][1].source,
            related_events=[e.id for _, e in low_loop.acquisitions], details=details,
        )
    elif uncertain or high_loop is not None or credits is not None and credits > BURST_CREDITS:
        diagnostic = Diagnostic(
            severity="warning", category="acquisition_buffer_pressure",
            message=("Acquisition buffer safety is uncertain: " + uncertain + "." if uncertain else
                     "Acquisition demand exceeds the documented seven-acquisition burst envelope "
                     "under the modeled service rate; FIFO overflow is not established."),
            source=(peak_source or acquisitions[-1]).source,
            related_events=[e.id for e in acquisitions], details=details,
        )
    else:
        return {"status": "pass", **details}, []
    return {"status": diagnostic.severity, **details}, [diagnostic]


def _burst_bound(state: AnalysisState, acquisitions: list[TimelineEvent]) -> int | None:
    if state.metadata.get("execution", {}).get("status", "completed") != "completed":
        return None
    loops = [e for e in state.events if e.kind == "loop_block"]
    ends = {loop.meta["end_pc"] for loop in loops}
    if any(get_instruction_spec(i.op).category == "branch" and pc not in ends
           for pc, i in state.instructions_by_pc.items()):
        return None
    ids = {e.id for e in acquisitions}
    events = {e.id: e for e in state.events}
    total = sum(not (e.meta.get("loop_previews") or e.meta.get("loop_preview")) for e in acquisitions)
    for loop in loops:
        body = [events[eid] for eid in loop.meta.get("first_iteration_event_ids", []) if eid in events]
        count = loop.meta.get("count")
        if type(count) is not int or count < 1 or any(e.kind == "loop_block" for e in body):
            return None
        if any(i.op not in _REPEATABLE_OPS for pc, i in state.instructions_by_pc.items()
               if loop.meta["start_pc"] <= pc < loop.meta["end_pc"]):
            return None
        total += count * sum(e.id in ids for e in body)
    return total


def _acquisition_spans(state: AnalysisState, acquisitions: list[TimelineEvent]) -> tuple[list[_Span], str | None]:
    by_id = {e.id: e for e in state.events}
    acquisition_ids = {e.id for e in acquisitions}
    spans: list[_Span] = []
    uncertain = None
    for loop in (e for e in state.events if e.kind == "loop_block"):
        body = [by_id[eid] for eid in loop.meta.get("first_iteration_event_ids", []) if eid in by_id]
        measured = [e for e in body if e.id in acquisition_ids]
        if not measured:
            continue
        period = loop.meta.get("period", {}).get("value")
        count = loop.meta.get("count")
        instructions = [i for pc, i in state.instructions_by_pc.items()
                        if loop.meta["start_pc"] <= pc < loop.meta["end_pc"]]
        repeatable = all(i.op in _REPEATABLE_OPS and
                         (i.op not in READOUT_OPS | {"wait", "play", "upd_param"} or i.args[-1].kind != "reg")
                         for i in instructions)
        valid = (isinstance(loop.t0, Concrete) and type(period) is int and period > 0
                 and (type(count) is int and count > 0 or count == "forever")
                 and repeatable and not any(e.kind == "loop_block" for e in body)
                 and all(isinstance(e.t0, Concrete) and e.confidence == "exact" for e in measured))
        q1 = [e for e in body if e.kind == "q1_issue"]
        if valid and q1 and all(isinstance(e.t0, Concrete) and isinstance(e.t1, Concrete) for e in q1):
            cost = max(e.t1.value for e in q1) - min(e.t0.value for e in q1) + loop.meta.get("loop_q1_jump_runtime_ns", 24)
            valid = cost <= period
        if not valid:
            uncertain = "loop acquisition timing or continued execution is not established"
            continue
        spans.append(_Span(loop.t0.value, [(e.t0.value-loop.t0.value, e) for e in measured],
                           period, None if count == "forever" else count))
    for event in acquisitions:
        # Preview copies describe the same body; they are not additional requests.
        if event.meta.get("loop_previews") or event.meta.get("loop_preview"):
            continue
        if not isinstance(event.t0, Concrete) or event.confidence != "exact":
            uncertain = "acquisition start times depend on runtime values"
            continue
        spans.append(_Span(event.t0.value, [(0, event)]))
    if any(e.kind == "unknown_region" or e.meta.get("branch_comparison_path")
           or e.kind == "branch_region" and e.confidence != "exact" for e in state.events):
        uncertain = "control flow is unresolved or represents alternative paths"
    if any(d.category == "unresolved_branch" or d.category == "analysis_incomplete"
           and d.details.get("analysis_scope") != "rt_queue" for d in state.diagnostics):
        uncertain = "execution is incomplete or depends on runtime input"
    if state.metadata.get("execution", {}).get("status", "completed") != "completed":
        uncertain = "only a partial execution trace is available"
    return sorted(spans, key=lambda span: span.start), uncertain


def _required_work(spans: list[_Span], service: int) -> tuple[int | None, _Span | None, TimelineEvent | None]:
    """Replay queued service work in ns; idle time drains it across loop wraps.

    A cycle maps q to max(q + count*service - period, floor). This permits
    exact finite-repeat arithmetic without expanding billions of acquisitions.
    """
    work = peak = end = 0
    peak_source = None
    for span in spans:
        work = max(0, work - (span.start-end))

        def cycle(initial: int) -> tuple[int, int, TimelineEvent | None]:
            current, maximum, previous = initial, initial, 0
            source = None
            for offset, event in span.acquisitions:
                current = max(0, current - (offset-previous)) + service
                if current > maximum:
                    maximum, source = current, event
                previous = offset
            return max(0, current - (span.period-previous)), maximum, source

        first, maximum, source = cycle(work)
        if maximum > peak:
            peak, peak_source = maximum, source
        delta = len(span.acquisitions)*service - span.period
        if span.count is None:
            if delta > 0:
                return None, span, span.acquisitions[0][1]
            _, maximum, source = cycle(first)
            return (maximum, None, source) if maximum > peak else (peak, None, peak_source)
        if span.count > 1:
            floor = cycle(0)[0]
            last_start = max(work + (span.count-1)*delta, floor + max(0, (span.count-2)*delta))
            _, maximum, source = cycle(last_start)
            if maximum > peak:
                peak, peak_source = maximum, source
            work = max(work + span.count*delta, floor + max(0, (span.count-1)*delta))
        else:
            work = first
        end = span.start + span.period*span.count
    return peak, None, peak_source
