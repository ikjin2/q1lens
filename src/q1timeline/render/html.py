from __future__ import annotations

import base64
import json
import math
import re
from collections import Counter
from html import escape
from pathlib import Path
from typing import Any

from q1timeline.analysis.alignment import SIMULATION_ALIGNMENT_MODE
from q1timeline.diagnostic_catalog import RT_QUEUE_UNDERFLOW_DESCRIPTION, describe_diagnostic, underflow_label
from q1timeline.q1asm.instruction_table import INSTRUCTION_TABLE, STATUS_BRANCH_OPS


DEBUG_LANES = {
    "debug.q1_issue",
    "debug.queue_depth",
    "debug.slack",
    "debug.trigger_send",
    "debug.underflow",
}
NORMAL_Q1_ISSUE_LANE = "q1_issue"
MIN_INLINE_LABEL_WIDTH = 20
INLINE_LABEL_X_PADDING = 2
SUPPORTED_IR_VERSION = "0.1.0"
SUPPORTED_TIME_VALUE_KINDS = {"concrete", "symbolic", "range", "unknown", "runtime_dependent"}
CONFIDENCE_LEGEND_ITEMS = [
    ("symbolic", "symbolic duration"),
    ("assumed", "assumption applied"),
    ("unknown", "unknown timing"),
    ("runtime_dependent", "runtime-dependent branch or trigger"),
]
FEEDBACK_OVERLAY_CATEGORIES = {
    "feedback_latency_violation",
    "feedback_route_mismatch",
}
NORMAL_FEEDBACK_COLLAPSED_EVENT_KINDS = frozenset(
    {
        "feedback_pop",
        "fb_acq_iq_id",
        "fb_acq_iq_shift",
        "fb_acq_tb_id",
        "fb_acq_tb_cfg",
        "fb_acq_tb_valid",
        "fb_acq_tb_extra",
        "fb_llp_tags_id",
        "fb_llp_ttls_id",
        "fb_tdc_tags_id",
        "fb_tdc_tdelta_id",
        "fb_com_cfg",
        "fb_com_extra",
    }
)
DIAGNOSTIC_SEVERITY_RANK = {
    "hint": 0,
    "info": 1,
    "warning": 2,
    "error": 3,
}
Q1_BRANCH_MARKER_OPS = frozenset({"jge", "jlt", *STATUS_BRANCH_OPS})
_INTEGER_TIME_METADATA_TEXT_RE = re.compile(r"^[+-]?(?:0[xX][0-9a-fA-F]+|0[bB][01]+|\d+)$")


class RenderError(ValueError):
    pass


def _reject_non_finite_json_constant(value: str) -> None:
    raise RenderError(f"TimelineIR contains non-finite JSON value: {value}")


def render_ir_file(ir_path: str | Path, out_path: str | Path, *, mode: str = "normal") -> None:
    try:
        text = Path(ir_path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RenderError(f"TimelineIR input is not valid UTF-8: {exc.reason}") from exc
    ir = json.loads(text, parse_constant=_reject_non_finite_json_constant)
    _validate_ir(ir)
    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(ir, default_mode=mode), encoding="utf-8")


def render_html(
    ir: dict[str, Any],
    *,
    default_mode: str = "normal",
    highlight_event_id: str | None = None,
) -> str:
    _validate_ir(ir)
    if default_mode not in {"normal", "debug"}:
        raise RenderError(f"Unsupported render mode: {default_mode!r}")
    source_events = ir.get("events", [])
    q1_only_loop_ids = _q1_only_loop_ids(source_events)
    rt_anchor = _shared_simulation_rt_anchor(ir, source_events)
    project = ir.get("project") or {}
    mode = project.get("alignment_mode")
    rt_origin = (mode == SIMULATION_ALIGNMENT_MODE
                 or mode == "first_anchor" and project.get("alignment_anchor_kinds") == ["rt_queue_dequeue"]
                 or rt_anchor == "rt_queue_dequeue")
    switch_geometry = bool(q1_only_loop_ids) or (rt_origin or rt_anchor is not None) and bool(ir.get("simulation"))
    debug_events = [*source_events, *_control_flow_events(source_events)] if default_mode == "debug" or switch_geometry else source_events
    render_events = debug_events if default_mode == "debug" else source_events
    events, lanes = _group_events(render_events, mode=default_mode)
    if switch_geometry:
        alternate_mode = "debug" if default_mode == "normal" else "normal"
        alternate_source = debug_events if alternate_mode == "debug" else source_events
        alternate_events, alternate_lanes = _group_events(alternate_source, mode=alternate_mode)
        by_id = {event["id"]: event for event in [*events, *alternate_events]}
        event_dom_ids = _event_dom_ids(list(by_id.values()))
    else:
        event_dom_ids = _event_dom_ids(events)
    diagnostics = []
    offset = _shared_simulation_offset(ir) if _default_time_basis(events) == "aligned" else 0
    for index, diagnostic in enumerate(ir.get("diagnostics", [])):
        details = diagnostic.get("details")
        message = details.get("display_message") if isinstance(details, dict) else None
        conflict = _network_conflict_display_message(diagnostic, offset)
        if conflict is not None:
            diagnostics.append({**diagnostic, "message": conflict, "_network_conflict_index": index})
        else:
            diagnostics.append({**diagnostic, "message": message} if message else diagnostic)
    display_ir = {**ir, "diagnostics": diagnostics}
    timeline_markup = _timeline_svg(display_ir, events, lanes, event_dom_ids, default_mode=default_mode,
                                    switch_geometry=switch_geometry, highlight_event_id=highlight_event_id,
                                    rt_origin=rt_origin, rt_anchor=rt_anchor, q1_only_loop_ids=q1_only_loop_ids)
    if switch_geometry:
        alternate_svg = _timeline_svg(display_ir, alternate_events, alternate_lanes, event_dom_ids, default_mode=alternate_mode,
                                       switch_geometry=True, highlight_event_id=highlight_event_id,
                                       rt_origin=rt_origin, rt_anchor=rt_anchor, q1_only_loop_ids=q1_only_loop_ids)
        timeline_markup += (f'<template data-timeline-mode="{default_mode}"></template>'
                            f'<template data-timeline-mode="{alternate_mode}">{alternate_svg}</template>')
    skipped_lines = {(item["source"]["file"], item["source"]["line"]) for item in diagnostics
                     if item.get("source") and item.get("details", {}).get("source_line_skipped")}
    recovery_banner = (
        f'<p class="diagnostics" role="status">Timeline excludes {len(skipped_lines)} invalid source '
        f'line{"s" if len(skipped_lines) != 1 else ""}. Timing and state reflect the remaining lines.</p>'
        if skipped_lines else ""
    )

    embedded_view_ir = dict(ir)
    embedded_view_ir["events"] = debug_events if switch_geometry else render_events
    embedded_ir = _json_script(embedded_view_ir)
    analysis_panels = "\n        ".join(
        panel
        for panel in (
            _confidence_legend(_visible_confidences(events, default_mode=default_mode)),
            _feedback_balance_panel(ir.get("feedback_balance", {})),
            _semantic_panel(ir.get("semantic", {}), event_dom_ids),
        )
        if panel
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Q1ASM Live Timeline</title>
  <style>{_css()}</style>
</head>
<body class="mode-{default_mode}" data-mode="{default_mode}">
  <main>
    {recovery_banner}
    <section id="timeline-root" class="timeline" aria-label="Timeline">
      {timeline_markup}
    </section>
    {_parameters_panel(ir.get("analysis", {}))}
    {_diagnostics_panel(diagnostics, event_dom_ids, source_events)}
    <details id="q1timeline-analysis-details" class="analysis-details">
      <summary>Analysis details</summary>
      <div id="q1timeline-analysis-details-body">
        <header>
          <h1>Q1ASM Live Timeline</h1>
          <div class="controls">
            <input id="event-filter" type="search" placeholder="Filter source or kind" aria-label="Filter source or kind">
            <div class="mode-toggle" role="group" aria-label="View mode">
              <button id="mode-normal" type="button" data-mode="normal" aria-pressed="{str(default_mode == 'normal').lower()}">Normal</button>
              <button id="mode-debug" type="button" data-mode="debug" aria-pressed="{str(default_mode == 'debug').lower()}">Debug</button>
            </div>
          </div>
        </header>
        <aside id="event-inspector" class="event-inspector" aria-live="polite">
          <h2>Selected event</h2>
          <dl id="event-inspector-fields"></dl>
        </aside>
        {analysis_panels}
      </div>
    </details>
  </main>
  <script id="timeline-ir" type="application/json">{embedded_ir}</script>
  <script>{_js()}</script>
</body>
</html>
"""


def _timeline_svg(
    ir: dict[str, Any], events: list[dict[str, Any]], lanes: list[str], event_dom_ids: dict[str, str],
    *, default_mode: str, switch_geometry: bool, highlight_event_id: str | None, rt_origin: bool, rt_anchor: str | None,
    q1_only_loop_ids: set[str],
) -> str:
    diagnostics = ir.get("diagnostics", [])
    hidden_rt_ids = set(q1_only_loop_ids) if default_mode == "normal" else set()
    if hidden_rt_ids:
        hidden_lanes = {event["lane"] for event in events if event["id"] in hidden_rt_ids}
        events = [event for event in events if event["id"] not in hidden_rt_ids]
        occupied_lanes = {event["lane"] for event in events}
        lanes = [lane for lane in lanes if lane not in hidden_lanes or lane in occupied_lanes or lane == _sequencer_from_lane(lane)]
    if switch_geometry and default_mode == "normal":
        lanes = [lane for lane in lanes if not _is_q1_issue_detail_lane(lane)]
    width = 1100
    lane_height = 34
    top = 72
    left = 190
    height = top + lane_height * max(1, len(lanes)) + 40
    time_basis = _default_time_basis(events)
    rt_events = [event for event in events if re.match(r"(?:branch_compare\.(?:taken|fallthrough)\.)?rt\.", event["_base_lane"])]
    extent_events = rt_events if hidden_rt_ids else events
    t_min, t_max = _time_extent(extent_events, time_basis=time_basis)
    local_min, local_max = _time_extent(extent_events, time_basis="local")
    aligned_min, aligned_max = _time_extent(extent_events, time_basis="aligned")
    explicit_rt_anchor = not rt_origin and rt_anchor is not None
    q1_start = min((_event_time_number(event, "t0", fallback=t_max + 1, time_basis=time_basis)
                    for event in events if event.get("kind") == "q1_issue"), default=t_max + 1)
    rt_start = min((_event_time_number(event, "t0", fallback=t_max + 1, time_basis=time_basis)
                    for event in rt_events if not explicit_rt_anchor or _event_confidence_visible(event, default_mode=default_mode)),
                   default=t_max + 1)
    if default_mode == "normal":
        if rt_origin:
            t_min, t_max = 0, max(1, t_max)
        elif explicit_rt_anchor and rt_start <= t_max:
            t_min, t_max = rt_start, max(rt_start + 1, t_max)
        if rt_anchor is not None:
            rt_end = -math.inf
            for event in rt_events:
                # Loop brackets remain visible even when the detailed event is hidden.
                if event.get("kind") != "loop_block" and not _event_confidence_visible(event, default_mode=default_mode):
                    continue
                start = _event_time_number(event, "t0", fallback=-math.inf, time_basis=time_basis)
                rt_end = max(rt_end, _event_time_number(event, "t1", fallback=start, time_basis=time_basis))
            if math.isfinite(rt_end):
                t_max = max(t_min + 1, rt_end)
    if switch_geometry and default_mode == "normal" and rt_origin and time_basis == "aligned":
        overlapping_ids = set()
        for event in rt_events:
            start = _event_time_number(event, "t0", fallback=t_min, time_basis=time_basis)
            if start >= t_min:
                continue
            end = _event_time_number(event, "t1", fallback=start, time_basis=time_basis)
            if end <= t_min:
                hidden_rt_ids.update((event["id"], *event.get("_event_alias_ids", ())))
            else:
                overlapping_ids.add(event["id"])
        events = [{**event, "_plot_t0": t_min} if event["id"] in overlapping_ids else event
                  for event in events if event["id"] not in hidden_rt_ids]
    hidden_rt_attr = (f' data-hidden-rt-event-ids="{escape(json.dumps(sorted(hidden_rt_ids)), quote=True)}"'
                      if hidden_rt_ids else "")
    diagnostic_overlays_by_event = _diagnostic_overlays_by_event(diagnostics, events)
    q1_errors_by_sequencer: dict[str, list[tuple[str, list[dict[str, Any]]]]] = {}
    for event in events:
        event_id = str(event["id"])
        if event.get("kind") == "q1_issue" and event_id in diagnostic_overlays_by_event:
            q1_errors_by_sequencer.setdefault(_sequencer_from_lane(event["lane"]), []).append(
                (event_id, diagnostic_overlays_by_event[event_id])
            )
    ticks = _ticks(rt_start if not (rt_origin or explicit_rt_anchor) and q1_start < rt_start <= t_max else t_min, t_max)
    svg_parts = [
        f'<svg class="timeline-svg" viewBox="0 0 {width} {height}" role="img" aria-label="Shared x-axis timeline" data-time-min="{t_min}" data-time-max="{t_max}" data-time-basis="{time_basis}" data-local-time-min="{local_min}" data-local-time-max="{local_max}" data-aligned-time-min="{aligned_min}" data-aligned-time-max="{aligned_max}" data-plot-left="{left}" data-plot-right="{width - 24}"{hidden_rt_attr}>',
        '<defs><marker id="feedback-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker><marker id="control-flow-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>',
    ]
    for tick in ticks:
        x = _scale(tick, t_min, t_max, left, width - 24)
        svg_parts.append(f'<line class="grid time-tick" x1="{x:.2f}" y1="40" x2="{x:.2f}" y2="{height - 20}" />')
        svg_parts.append(f'<text class="tick-label" x="{x:.2f}" y="56">{tick} ns</text>')
    svg_parts.append(f'<line id="time-cursor" class="time-cursor" x1="0" y1="40" x2="0" y2="{height - 20}" hidden />')

    current_sequencer = None
    for lane_index, lane in enumerate(lanes):
        y = top + lane_index * lane_height
        sequencer = _sequencer_from_lane(lane)
        lane_events = sorted([event for event in events if event.get("lane") == lane],
                             key=lambda event: _event_draw_order(event, mode=default_mode))
        if sequencer != current_sequencer:
            current_sequencer = sequencer
            mark = ""
            if q1_errors := q1_errors_by_sequencer.pop(sequencer, None):
                ids = escape(" ".join(event_id for event_id, _ in q1_errors), quote=True)
                errors = [item for _, items in q1_errors for item in items]
                attributes = " ".join(_diagnostic_data_attributes(errors))
                mark = (f'<tspan class="event q1-error-marker timeline-error-mark" dx="5" '
                        f'data-event-id="{escape(q1_errors[0][0], quote=True)}" data-event-ids="{ids}" {attributes} '
                        f'role="button" tabindex="0" aria-label="{len(errors)} Q1 error{"s" if len(errors) != 1 else ""}">!</tspan>')
            svg_parts.append(
                f'<text class="sequencer-label" data-sequencer-label="{escape(sequencer, quote=True)}" x="40" y="{y + 15}">{escape(sequencer)}{mark}</text>'
            )
        lane_class = "lane debug-lane" if _is_debug_lane(lane) else "lane normal-lane"
        is_q1_issue_lane = _is_q1_issue_detail_lane(lane)
        if is_q1_issue_lane:
            lane_class = f"{lane_class} q1-issue-lane"
        sequencer_id = sequencer.removeprefix("sequencer:")
        q1_issue_attrs = (
            f' data-lane-role="q1-issue" data-parent-lane="{escape(sequencer, quote=True)}"'
            if is_q1_issue_lane
            else ""
        )
        lane_visibility = ' hidden style="display:none"' if is_q1_issue_lane and default_mode != "debug" else ""
        svg_parts.append(
            f'<g class="{lane_class}" data-lane="{escape(lane, quote=True)}" data-sequencer-id="{escape(sequencer_id, quote=True)}"{q1_issue_attrs}{lane_visibility}>'
        )
        if lane != sequencer:
            label = _lane_display_label(lane, lane_events)
            if is_q1_issue_lane:
                simulation = (ir.get("simulation") or {}).get(sequencer_id)
                if simulation is not None:
                    buffer = simulation.get("initial_buffer", {})
                    assumed, possible = buffer.get("assumed_ns"), buffer.get("possible_ns")
                else:
                    underflow = ir.get("assumptions", {}).get("underflow", {})
                    assumed, possible = underflow.get("initial_buffer_assumed_ns"), underflow.get("initial_buffer_possible_ns")
                if isinstance(assumed, int) and isinstance(possible, int):
                    label += f" · {assumed / 1e6:g}–{possible / 1e6:g} ms buffer"
            svg_parts.append(f'<text class="lane-label" x="16" y="{y + 20}">{escape(label)}</text>')
        svg_parts.append(f'<line class="lane-rule" x1="{left}" y1="{y + 10}" x2="{width - 24}" y2="{y + 10}" />')
        structural_events = [
            event for event in lane_events if event.get("kind") in {"loop_block", "branch_region"}
        ]
        if default_mode == "normal":
            for event in structural_events:
                if event.get("kind") == "loop_block":
                    svg_parts.extend(
                        _loop_bracket_svgs(
                            event,
                            lane_index,
                            t_min,
                            t_max,
                            left,
                            width - 24,
                            top,
                            lane_height,
                            highlight_event_id,
                            time_basis,
                            event_dom_ids,
                            diagnostic_overlays_by_event,
                        )
                    )
        for event in lane_events:
            if switch_geometry and default_mode == "normal" and event.get("kind") == "latched_state_pending":
                continue
            svg_parts.append(
                _event_svg(
                    event,
                    lane_index,
                    t_min,
                    t_max,
                    left,
                    width - 24,
                    top,
                    lane_height,
                    highlight_event_id,
                    time_basis,
                    event_dom_ids,
                    diagnostic_overlays_by_event,
                )
            )
        for event in structural_events:
            if event.get("kind") == "branch_region":
                svg_parts.extend(
                    _branch_marker_svgs(
                        event,
                        lane_index,
                        t_min,
                        t_max,
                        left,
                        width - 24,
                        top,
                        lane_height,
                        highlight_event_id,
                        time_basis,
                        event_dom_ids,
                        diagnostic_overlays_by_event,
                    )
                )
        svg_parts.append("</g>")
    if default_mode == "debug":
        svg_parts.extend(_control_flow_connector_svgs(events, lanes, t_min, t_max, left, width - 24, top, lane_height, time_basis))
    svg_parts.extend(
        _feedback_flow_svgs(
            ir.get("feedback_flows", []),
            events,
            lanes,
            t_min,
            t_max,
            left,
            width - 24,
            top,
            lane_height,
            time_basis,
            diagnostics,
        )
    )
    svg_parts.append("</svg>")
    return "".join(svg_parts)


def _event_svg(
    event: dict[str, Any],
    lane_index: int,
    t_min: int,
    t_max: int,
    left: int,
    right: int,
    top: int,
    lane_height: int,
    highlight_event_id: str | None,
    time_basis: str,
    event_dom_ids: dict[str, str],
    diagnostic_overlays_by_event: dict[str, list[dict[str, Any]]],
) -> str:
    t0 = _event_time_number(event, "t0", fallback=t_min, time_basis=time_basis)
    t1 = _event_time_number(event, "t1", fallback=t0, time_basis=time_basis)
    x = _scale(event.get("_plot_t0", t0), t_min, t_max, left, right)
    x1 = _scale(t1, t_min, t_max, left, right)
    width = max(6, x1 - x)
    y = top + lane_index * lane_height
    confidence = event.get("confidence", "unknown")
    kind = event.get("kind", "event")
    display_label = _event_display_label(event)
    event_diagnostics = diagnostic_overlays_by_event.get(str(event.get("id", "")), [])
    label_offset = 8 if event_diagnostics else 0
    inline_label = _event_inline_label(event, width - label_offset)
    label = escape(inline_label)
    event_id = escape(str(event.get("id", "")))
    dom_id = escape(_event_dom_id(str(event.get("id", "")), event_dom_ids))
    aliases = event.get("_event_alias_ids", ())
    selected = str(event.get("id")) == highlight_event_id or highlight_event_id in aliases
    classes = _event_classes(event, confidence=confidence, kind=kind, selected=selected)
    classes = _with_diagnostic_classes(classes, event_diagnostics)
    label_x = x + INLINE_LABEL_X_PADDING + label_offset
    label_markup = f'<text class="event-label" x="{label_x:.2f}" y="{y + 15}">{label}</text>' if inline_label else ""
    if width - label_offset < MIN_INLINE_LABEL_WIDTH or not inline_label:
        classes = f"{classes} lod-small label-hidden"
        label_markup = ""
    search_text = escape(_search_text(event), quote=True)
    confidence_text = escape(str(confidence), quote=True)
    data_attributes = [
        f'data-kind="{escape(str(kind), quote=True)}"',
        f'data-confidence="{confidence_text}"',
        f'data-t0-x="{x:.2f}"',
    ]
    data_attributes.extend(_diagnostic_data_attributes(event_diagnostics))
    aria_label = escape(f"{display_label} {kind} confidence {confidence}", quote=True)
    meta = event.get("meta", {}) if isinstance(event.get("meta"), dict) else {}
    if kind in {"sync_wait", "trigger_wait"} and meta.get("pending") and meta.get("q1_issue_event_id"):
        issue = escape(str(meta["q1_issue_event_id"]), quote=True)
        data_attributes.append(f'data-wait-q1-issue-event-id="{issue}"')
    if meta.get("rt_trigger_epoch") or (time_basis == "aligned" and meta.get("aligned_time_nominal")):
        classes += " rt-time-nominal"
    loop_id = meta.get("loop_id")
    if loop_id:
        data_attributes.append(f'data-loop-id="{escape(str(loop_id), quote=True)}"')
    loop_preview = meta.get("loop_preview")
    if loop_preview:
        data_attributes.append(f'data-loop-preview-id="{escape(str(loop_preview), quote=True)}"')
    branch_id = meta.get("branch_id") or meta.get("branch_comparison_branch_id")
    if branch_id:
        data_attributes.append(f'data-branch-id="{escape(str(branch_id), quote=True)}"')
    branch_comparison_path = meta.get("branch_comparison_path")
    if branch_comparison_path:
        data_attributes.append(f'data-branch-comparison-path="{escape(str(branch_comparison_path), quote=True)}"')
    control_flow_source_event_id = meta.get("control_flow_source_event_id")
    if control_flow_source_event_id:
        data_attributes.append(
            f'data-control-flow-source-event-id="{escape(str(control_flow_source_event_id), quote=True)}"'
        )
    if kind == "loop_block":
        classes = f"{classes} loop-collapsed"
        data_attributes.append('data-collapsed="true"')
    if kind == "branch_region":
        classes = f"{classes} branch-collapsed"
        data_attributes.append('data-collapsed="true"')
    if kind == "q1_issue":
        if not _is_q1_issue_detail_lane(str(event.get("lane", ""))):
            classes = f"{classes} q1-dense-collapsed"
            data_attributes.append('data-normal-collapsed="true"')
        if _is_branch_marker_backed_q1_issue(event):
            classes = f"{classes} q1-branch-collapsed"
            data_attributes.append('data-branch-marker-backed="true"')
    if _is_normal_feedback_collapsed_kind(kind):
        classes = f"{classes} normal-feedback-collapsed"
        data_attributes.append('data-normal-collapsed="feedback"')
    diff_status = meta.get("diff_status")
    if diff_status:
        data_attributes.append(f'data-diff-status="{escape(str(diff_status), quote=True)}"')
    data_attribute_text = " ".join(data_attributes)
    terminal = meta.get("comparison_terminal")
    if kind in {"trigger_receive", "feedback_rejected"} or isinstance(terminal, dict):
        if aliases:
            data_attribute_text += f' data-event-ids="{escape(" ".join(aliases), quote=True)}"'
        if isinstance(terminal, dict):
            status = str(terminal.get("status", "incomplete"))
            identifier = f" · LINQ ID {meta.get('id', 'unknown')}" if kind == "feedback_rejected" else ""
            title = escape(f"Execution {status.replace('_', ' ')} · {t0} ns · {terminal.get('reason', '')}{identifier}")
            fill = "var(--vscode-editorError-foreground, #b3242f)" if status == "error" else "var(--vscode-editorWarning-foreground, #a06016)"
        elif kind == "feedback_rejected":
            title = escape(f"Rejected LINQ arrival · ID {meta.get('id', 'unknown')} · {t0} ns")
            fill = "var(--vscode-editorError-foreground, #b3242f)"
        else:
            arrivals = event.get("_trigger_arrival_addresses")
            if arrivals:
                counts = Counter(str(address) if address is not None else "unknown" for address in arrivals)
                addresses = ", ".join(address if count == 1 else f"{address} ×{count}" for address, count in counts.items())
                title = escape(f"Trigger addresses {addresses} · {t0} ns")
            else:
                title = escape(f"Trigger address {meta.get('address', 'unknown')} · {t0} ns")
            fill = "var(--vscode-editorInfo-foreground, #3794ff)"
        return (
            f'<g id="{dom_id}" class="{classes}" data-event-id="{event_id}" {data_attribute_text} '
            f'data-search="{search_text}" aria-label="{title}" role="button" tabindex="0"><title>{title}</title>'
            f'<circle cx="{x:.2f}" cy="{y + 11}" r="3" fill="{fill}" />'
            '</g>'
        )
    tooltip = escape(_tooltip_with_diagnostics(_tooltip(event, time_basis=time_basis), event_diagnostics))
    if kind == "underflow_warning":
        if event.get("_diagnostic_target"):
            return ""
        return (
            f'<g id="{dom_id}" class="{classes} timing-diagnostic" data-event-id="{event_id}" {data_attribute_text} '
            f'data-diagnostic-placement="point" data-search="{search_text}" '
            f'aria-label="{aria_label}" role="button" tabindex="0"><title>{tooltip}</title>'
            f'<text class="timeline-error-mark" x="{x:.2f}" y="{y + 32}" text-anchor="middle" aria-hidden="true">!</text>'
            '</g>'
        )
    related_ids = " ".join([*aliases,
                            *(str(item["underflow_event_id"]) for item in event_diagnostics if "underflow_event_id" in item)])
    mark = (
        f'<text class="timeline-error-mark" x="{x + INLINE_LABEL_X_PADDING:.2f}" y="{y + 15}" aria-hidden="true">!</text>'
        if event_diagnostics else ""
    )
    rt_lane = re.match(r"(?:branch_compare\.(?:taken|fallthrough)\.)*rt\.", event["_base_lane"])
    role = ' role="button"' if kind == "q1_issue" or rt_lane else ""
    return (
        f'<g id="{dom_id}" class="{classes}" data-event-id="{event_id}" data-event-ids="{escape(related_ids, quote=True)}" '
        f'{data_attribute_text} data-search="{search_text}" aria-label="{aria_label}"{role} tabindex="0">'
        f"<title>{tooltip}</title>"
        f'<rect x="{x:.2f}" y="{y}" width="{width:.2f}" height="22" rx="3" />'
        f"{mark}"
        f"{label_markup}"
        "</g>"
    )


def _loop_bracket_svgs(
    event: dict[str, Any],
    lane_index: int,
    t_min: int,
    t_max: int,
    left: int,
    right: int,
    top: int,
    lane_height: int,
    highlight_event_id: str | None,
    time_basis: str,
    event_dom_ids: dict[str, str],
    diagnostic_overlays_by_event: dict[str, list[dict[str, Any]]],
) -> list[str]:
    t0 = _event_time_number(event, "t0", fallback=t_min, time_basis=time_basis)
    t1 = _event_time_number(event, "t1", fallback=t0, time_basis=time_basis)
    x_start = _scale(event.get("_plot_t0", t0), t_min, t_max, left, right)
    x_end = _scale(t1, t_min, t_max, left, right)
    if x_end <= x_start:
        return []

    y = top + lane_index * lane_height
    bracket_top = y - 5
    bracket_bottom = y + 26
    guide_y = y + 27
    cap = 9
    event_id = escape(str(event.get("id", "")), quote=True)
    dom_id = escape(_event_dom_id(str(event.get("id", "")), event_dom_ids), quote=True)
    meta = event.get("meta", {}) if isinstance(event.get("meta"), dict) else {}
    loop_id = str(meta.get("loop_id") or event.get("id") or "loop")
    count_label = _loop_repeat_count_label(event)
    search_text = escape(_search_text(event), quote=True)
    selected_class = " is-selected" if str(event.get("id")) == highlight_event_id else ""
    loop_diagnostics = diagnostic_overlays_by_event.get(str(event.get("id", "")), [])
    start_classes = _with_diagnostic_classes(f"loop-bracket loop-bracket-start command-family-flow{selected_class}", loop_diagnostics)
    end_classes = _with_diagnostic_classes(f"loop-bracket loop-bracket-end command-family-flow{selected_class}", loop_diagnostics)
    diagnostic_attributes = " ".join(_diagnostic_data_attributes(loop_diagnostics))
    if diagnostic_attributes:
        diagnostic_attributes = f" {diagnostic_attributes}"
    badge_y = bracket_top - 9
    loop_attr = escape(loop_id, quote=True)
    observed = meta.get("observed")
    loop_name = "loop" if observed else f"loop {loop_id}"
    title = escape(_tooltip_with_diagnostics(f"{loop_name}, repeat {count_label}", loop_diagnostics))
    aria_start = escape(f"{loop_name} starts, repeats {count_label}", quote=True)
    aria_end = escape(f"{loop_name} ends, repeats {count_label}", quote=True)
    id_text = escape("loop" if observed else loop_id)
    count_text = escape(f"\u00d7{count_label}" if observed else count_label)
    start_hitbox_x = x_start - 10
    end_hitbox_x = x_end - 20

    start = (
        f'<g id="{dom_id}-loop-start" class="{start_classes}" '
        f'data-event-id="{event_id}" data-kind="loop_block" data-loop-id="{loop_attr}" '
        f'data-loop-bracket-edge="start" data-t0-x="{x_start:.2f}" data-search="{search_text}" '
        f'data-loop-bracket-badge-y="{badge_y:.2f}"{diagnostic_attributes} '
        f'aria-label="{aria_start}" role="button" tabindex="0">'
        f"<title>{title}</title>"
        f'<line class="loop-bracket-guide" x1="{x_start:.2f}" y1="{guide_y:.2f}" x2="{x_end:.2f}" y2="{guide_y:.2f}" />'
        f'<rect class="loop-bracket-hitbox" x="{start_hitbox_x:.2f}" y="{bracket_top - 12:.2f}" width="48" height="{bracket_bottom - bracket_top + 20:.2f}" rx="2" />'
        f'<line class="loop-bracket-stem" x1="{x_start:.2f}" y1="{bracket_top:.2f}" x2="{x_start:.2f}" y2="{bracket_bottom:.2f}" />'
        f'<line class="loop-bracket-cap loop-bracket-cap-top" x1="{x_start:.2f}" y1="{bracket_top:.2f}" x2="{x_start + cap:.2f}" y2="{bracket_top:.2f}" />'
        f'<line class="loop-bracket-cap loop-bracket-cap-bottom" x1="{x_start:.2f}" y1="{bracket_bottom:.2f}" x2="{x_start + cap:.2f}" y2="{bracket_bottom:.2f}" />'
        f'<text class="loop-bracket-id" x="{x_start + 12:.2f}" y="{bracket_top - 3:.2f}">{id_text}</text>'
        "</g>"
    )
    end = (
        f'<g id="{dom_id}-loop-end" class="{end_classes}" '
        f'data-event-id="{event_id}" data-kind="loop_block" data-loop-id="{loop_attr}" '
        f'data-loop-bracket-edge="end" data-t0-x="{x_end:.2f}" data-search="{search_text}" '
        f'data-loop-bracket-badge-y="{badge_y:.2f}"{diagnostic_attributes} '
        f'aria-label="{aria_end}" role="button" tabindex="0">'
        f"<title>{title}</title>"
        f'<rect class="loop-bracket-hitbox" x="{end_hitbox_x:.2f}" y="{bracket_top - 12:.2f}" width="48" height="{bracket_bottom - bracket_top + 20:.2f}" rx="2" />'
        f'<line class="loop-bracket-stem" x1="{x_end:.2f}" y1="{bracket_top:.2f}" x2="{x_end:.2f}" y2="{bracket_bottom:.2f}" />'
        f'<line class="loop-bracket-cap loop-bracket-cap-top" x1="{x_end:.2f}" y1="{bracket_top:.2f}" x2="{x_end - cap:.2f}" y2="{bracket_top:.2f}" />'
        f'<line class="loop-bracket-cap loop-bracket-cap-bottom" x1="{x_end:.2f}" y1="{bracket_bottom:.2f}" x2="{x_end - cap:.2f}" y2="{bracket_bottom:.2f}" />'
        f'<text class="loop-bracket-count" x="{x_end + 4:.2f}" y="{bracket_top - 3:.2f}">{count_text}</text>'
        "</g>"
    )
    return [start, end]


def _is_branch_marker_backed_q1_issue(event: dict[str, Any]) -> bool:
    if event.get("kind") != "q1_issue":
        return False
    meta = event.get("meta", {}) if isinstance(event.get("meta"), dict) else {}
    if not meta.get("branch_id") or not meta.get("assumed_branch_path"):
        return False
    return str(meta.get("op", "")).lower() in Q1_BRANCH_MARKER_OPS


def _branch_marker_svgs(
    event: dict[str, Any],
    lane_index: int,
    t_min: int,
    t_max: int,
    left: int,
    right: int,
    top: int,
    lane_height: int,
    highlight_event_id: str | None,
    time_basis: str,
    event_dom_ids: dict[str, str],
    diagnostic_overlays_by_event: dict[str, list[dict[str, Any]]],
) -> list[str]:
    t0 = _event_time_number(event, "t0", fallback=t_min, time_basis=time_basis)
    t1 = _event_time_number(event, "t1", fallback=t0, time_basis=time_basis)
    x_start = _scale(event.get("_plot_t0", t0), t_min, t_max, left, right)
    x_end = _scale(t1, t_min, t_max, left, right)
    y = top + lane_index * lane_height
    center_y = y + 10
    guide_y = y + 27
    event_id = escape(str(event.get("id", "")), quote=True)
    dom_id = escape(_event_dom_id(str(event.get("id", "")), event_dom_ids), quote=True)
    meta = event.get("meta", {}) if isinstance(event.get("meta"), dict) else {}
    branch_id = meta.get("branch_id")
    branch_attr = f' data-branch-id="{escape(str(branch_id), quote=True)}"' if branch_id else ""
    condition = _branch_condition_label(event)
    search_text = escape(_search_text(event), quote=True)
    selected_class = " is-selected" if str(event.get("id")) == highlight_event_id else ""
    confidence = str(event.get("confidence", "unknown"))
    confidence_class = f" confidence-{_class_token(confidence)}"
    confidence_attr = escape(confidence, quote=True)
    marker_diagnostics = diagnostic_overlays_by_event.get(str(event.get("id", "")), [])
    marker_classes = _with_diagnostic_classes(
        f"branch-marker branch-marker-unresolved command-family-flow{confidence_class}{selected_class}",
        marker_diagnostics,
    )
    diagnostic_attributes = " ".join(_diagnostic_data_attributes(marker_diagnostics))
    if diagnostic_attributes:
        diagnostic_attributes = f" {diagnostic_attributes}"
    status_label = _branch_marker_status_label(event)
    source_label = _branch_marker_source_line_label(event)
    title_parts = [source_label, status_label, f"unresolved branch {condition}"]
    title_text = ": ".join(part for part in title_parts if part)
    title = escape(_tooltip_with_diagnostics(title_text, marker_diagnostics))
    aria_label = escape(title_text, quote=True)
    condition_label = _branch_marker_visible_label(event)
    condition_markup = (
        f'<text class="branch-marker-condition" x="{x_start + 12:.2f}" y="{center_y + 4:.2f}">{escape(condition_label)}</text>'
        if condition_label
        else ""
    )
    status_markup = _branch_marker_path_icon_svg(event, x_start + 10.0, center_y - 7.0)
    diamond = 6
    hitbox_x = x_start - 14
    hitbox_width = 56
    guide = ""
    if x_end > x_start + 1:
        guide = (
            f'<line class="branch-marker-guide" x1="{x_start:.2f}" y1="{guide_y:.2f}" '
            f'x2="{x_end:.2f}" y2="{guide_y:.2f}" />'
        )

    return [
        (
            f'<g id="{dom_id}-branch-marker" class="{marker_classes}" '
            f'data-event-id="{event_id}" data-kind="branch_region" data-confidence="{confidence_attr}"{branch_attr} '
            f'data-t0-x="{x_start:.2f}" data-branch-marker-center-y="{center_y:.2f}" '
            f'data-branch-marker-guide-y="{guide_y:.2f}"{diagnostic_attributes} data-search="{search_text}" '
            f'data-condition="{escape(condition, quote=True)}" '
            f'aria-label="{aria_label}" role="button" tabindex="0">'
            f"<title>{title}</title>"
            f"{guide}"
            f'<rect class="branch-marker-hitbox" x="{hitbox_x:.2f}" y="{y - 10:.2f}" width="{hitbox_width:.2f}" height="44" rx="2" />'
            f'<polygon class="branch-marker-diamond" points="'
            f'{x_start:.2f},{center_y - diamond:.2f} '
            f'{x_start + diamond:.2f},{center_y:.2f} '
            f'{x_start:.2f},{center_y + diamond:.2f} '
            f'{x_start - diamond:.2f},{center_y:.2f}" />'
            f"{status_markup}"
            f"{condition_markup}"
            "</g>"
        )
    ]


def _branch_condition_label(event: dict[str, Any]) -> str:
    meta = event.get("meta", {}) if isinstance(event.get("meta"), dict) else {}
    condition = meta.get("condition")
    if condition:
        return f"{condition} ?"
    label = str(event.get("label", "")).strip()
    prefix = "unresolved branch:"
    if label.lower().startswith(prefix):
        condition_from_label = label[len(prefix) :].strip()
        if condition_from_label:
            return f"{condition_from_label} ?"
    return "branch ?"


def _branch_marker_visible_label(event: dict[str, Any]) -> str:
    return ""


def _branch_marker_status_label(event: dict[str, Any]) -> str:
    meta = event.get("meta", {}) if isinstance(event.get("meta"), dict) else {}
    path = str(meta.get("assumed_branch_path", ""))
    if path == "taken":
        return "shown path: condition true"
    if path == "fallthrough":
        return "shown path: condition false"
    if path == "both":
        return "shown paths: both"
    if path == "collapsed":
        return "branch collapsed"
    return ""


def _branch_marker_source_line_label(event: dict[str, Any]) -> str:
    source = event.get("source", {}) if isinstance(event.get("source"), dict) else {}
    line = source.get("line")
    if type(line) is int and line > 0:
        raw = re.sub(r"\s+", " ", str(source.get("raw", "")).split("#", 1)[0].strip())
        if raw:
            return f"Line {line}: {raw}"
        return f"Line {line}"
    return ""


def _branch_marker_path_icon_name(event: dict[str, Any]) -> str:
    meta = event.get("meta", {}) if isinstance(event.get("meta"), dict) else {}
    path = str(meta.get("assumed_branch_path", ""))
    if path in {"taken", "fallthrough", "both", "collapsed"}:
        return path
    return ""


def _branch_marker_path_icon_svg(event: dict[str, Any], x: float, y: float) -> str:
    icon_name = _branch_marker_path_icon_name(event)
    if not icon_name:
        return ""
    label = _branch_marker_status_label(event)
    path_data = _branch_marker_path_icon_paths(icon_name)
    if not path_data:
        return ""
    paths = "".join(f'<path d="{escape(path, quote=True)}" />' for path in path_data)
    return (
        f'<g class="branch-marker-path-icon branch-marker-path-icon-{escape(icon_name, quote=True)}" '
        f'transform="translate({x:.2f} {y:.2f}) scale(0.58)" '
        f'aria-label="{escape(label, quote=True)}">'
        f"<title>{escape(label)}</title>"
        f"{paths}"
        "</g>"
    )


def _branch_marker_path_icon_paths(icon_name: str) -> tuple[str, ...]:
    if icon_name == "taken":
        return ("M4 18h4a8 8 0 0 0 8-8V5", "m12 9 4-4 4 4")
    if icon_name == "fallthrough":
        return ("M6 6v4a8 8 0 0 0 8 8h4", "m14 14 4 4-4 4")
    if icon_name == "both":
        return (
            "M5 19V5",
            "M5 12h5a5 5 0 0 0 5-5V5",
            "M10 12a5 5 0 0 1 5 5v2",
            "m12 7 3-3 3 3",
            "m12 17 3 3 3-3",
        )
    if icon_name == "collapsed":
        return ("M5 5l14 14", "M19 5 5 19")
    return ()


def _loop_repeat_count_label(event: dict[str, Any]) -> str:
    meta = event.get("meta", {}) if isinstance(event.get("meta"), dict) else {}
    raw_count = meta.get("count")
    if raw_count is None:
        label = str(event.get("label", ""))
        if "forever" in label.lower():
            return "\u221e"
        match = re.search(r"\bx(\d+)\b", label)
        if match:
            return match.group(1)
        return "?"
    if isinstance(raw_count, bool):
        return "?"
    if isinstance(raw_count, int):
        return str(raw_count)
    if isinstance(raw_count, dict):
        display = raw_count.get("display")
        if display is not None:
            return _loop_repeat_count_text(display)
        value = raw_count.get("value")
        if value is not None:
            return _loop_repeat_count_text(value)
        return "?"
    return _loop_repeat_count_text(raw_count)


def _loop_repeat_count_text(value: Any) -> str:
    if isinstance(value, bool):
        return "?"
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    if not text:
        return "?"
    lowered = text.lower()
    if lowered in {"forever", "infinite", "infinity", "inf"}:
        return "\u221e"
    if lowered.isdigit():
        return text
    if lowered.startswith("unknown") or lowered.startswith("runtime") or lowered.startswith("symbolic"):
        return "?"
    return text if len(text) <= 8 else "?"


def _event_draw_order(event: dict[str, Any], *, mode: str) -> tuple[int, str]:
    kind = str(event.get("kind", ""))
    if kind == "loop_block":
        return (0, str(event.get("id", "")))
    if kind == "loop_iteration_preview":
        return (1, str(event.get("id", "")))
    if mode == "normal" and kind == "netzero_result":
        return (2, str(event.get("id", "")))
    return (3, str(event.get("id", "")))


def _tooltip(event: dict[str, Any], *, time_basis: str) -> str:
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
    source_display = f"{source.get('file')}:{source.get('line')}" if source.get("file") else "unavailable"
    raw_display = str(source.get("raw")) if source.get("raw") else "unavailable"
    aligned = time_basis == "aligned"

    def display_edge(edge: str) -> str:
        value = _plain_number(meta.get(f"aligned_{edge}")) if aligned else None
        return _display_value(value if value is not None else event.get(edge))

    start, end = display_edge("t0"), display_edge("t1")
    kind = event.get("kind")
    pending = meta.get("pending") is True and kind in {"trigger_wait", "sync_wait"}
    if pending:
        arm = _event_time_number(event, "t0", fallback=math.nan, time_basis=time_basis)
        cutoff = _event_time_number(event, "t1", fallback=math.nan, time_basis=time_basis)
        target = "trigger" if kind == "trigger_wait" else "synchronization"
        arm_text = f"{arm} ns" if math.isfinite(arm) else start
        timing = f"Arm {arm_text} → waiting for {target} (completion unresolved)"
        if math.isfinite(cutoff):
            timing += f" · observation cutoff {cutoff} ns"
    else:
        timing = (f"{start} ns" if kind == "underflow_warning" and meta.get("diagnostic_message")
                  else _incomplete_q1_timing_text(event, time_basis=time_basis)
                  or _rt_command_timing_text(event, time_basis=time_basis)
                  or _trigger_rt_timing_text(meta, start, end, aligned=aligned))
    observation_cutoff = _metadata_time_in_basis(event, "observation_cutoff_ns", time_basis=time_basis)
    observed = pending or (observation_cutoff is not None and
                           observation_cutoff == _event_time_number(event, "t1", fallback=math.nan, time_basis=time_basis))
    lines = [
        f"label: {underflow_label(meta) if event.get('kind') == 'underflow_warning' else event.get('label', event.get('kind'))}",
        f"kind: {event.get('kind')}",
        f"sequencer: {event.get('sequencer_id')}",
        f"lane: {event.get('_base_lane', event.get('lane'))}",
        f"{time_basis} time: {timing}",
        f"{'observed duration' if observed else 'duration'}: {_display_value(event.get('duration'))}",
        f"confidence: {event.get('confidence')}",
    ]
    if not meta.get("pending") and kind != "q1_issue" and _plain_number(meta.get("queue_duration_ns")) == 0:
        bound = _metadata_time_in_basis(event, "next_dequeue_not_before_ns", time_basis=time_basis)
        if bound is not None:
            label = "Nominal earliest next dequeue" if _rt_time_is_nominal(meta, aligned=aligned) else "Earliest next dequeue"
            lines.append(f"{label}: {bound} ns")
    if meta.get("trigger_arrival_unknown"):
        lines.append(f"wait after trigger (ns): {meta.get('trigger_wait_duration_ns')}")
    if isinstance(meta, dict) and meta.get("status"):
        lines.append(f"status: {meta.get('status')}")
    lines.extend(
        [
            f"source: {source_display}",
            f"raw: {raw_display}",
        ]
    )
    return "\n".join(lines)


def _metadata_time_in_basis(event: dict[str, Any], field: str, *, time_basis: str) -> int | float | None:
    meta = event.get("meta") or {}
    value = _plain_number(meta.get(field))
    if value is None:
        return None
    offset = 0
    if time_basis == "aligned":
        for edge in ("t0", "t1"):
            aligned = _plain_number(meta.get(f"aligned_{edge}"))
            if aligned is not None:
                local = _plain_number(meta.get(f"local_{edge}"))
                if local is None:
                    local = _value_number(event.get(edge), fallback=math.nan)
                offset = aligned - local
                if math.isfinite(offset):
                    break
    projected = value + offset
    return projected if math.isfinite(projected) else None


def _rt_time_is_nominal(meta: dict[str, Any], *, aligned: bool) -> bool:
    return bool(meta.get("rt_trigger_epoch") or (aligned and meta.get("aligned_time_nominal")))


def _incomplete_q1_timing_text(event: dict[str, Any], *, time_basis: str) -> str | None:
    if event.get("kind") != "q1_issue":
        return None
    cutoff = _metadata_time_in_basis(event, "observation_cutoff_ns", time_basis=time_basis)
    planned = _metadata_time_in_basis(event, "planned_q1_end_ns", time_basis=time_basis)
    if cutoff is not None and planned is not None:
        start = _event_time_number(event, "t0", fallback=math.nan, time_basis=time_basis)
        end = _event_time_number(event, "t1", fallback=math.nan, time_basis=time_basis)
        if start <= cutoff == end < planned:
            return f"Issue {start} ns · Observation cutoff {cutoff} ns · Planned end {planned} ns"
    return None


def _released_wait_timing_text(event: dict[str, Any], *, time_basis: str) -> str | None:
    label = {"wait_trigger": "Trigger arrival", "wait_sync": "Barrier release"}.get(event.get("kind"))
    meta = event.get("meta") or {}
    if (label is None or meta.get("pending") or meta.get("conditional_skipped") or meta.get("interrupted_by_fault")
            or meta.get("playback") or meta.get("trigger_assumption") is not None
            or meta.get("trigger_arrival_unknown") or _rt_time_is_nominal(meta, aligned=time_basis == "aligned")):
        return None
    phase = _metadata_time_in_basis(event, "rt_execution_ns", time_basis=time_basis)
    arm = _event_time_number(event, "t0", fallback=math.nan, time_basis=time_basis)
    end = _event_time_number(event, "t1", fallback=math.nan, time_basis=time_basis)
    has_cutoff = "observation_cutoff_ns" in meta
    if has_cutoff and _metadata_time_in_basis(event, "observation_cutoff_ns", time_basis=time_basis) != end:
        return None
    if phase is not None and arm <= phase <= end:
        end_label = "Observation cutoff" if has_cutoff else "Complete"
        return f"Arm {arm} ns · {label} {phase} ns · {end_label} {end} ns"
    return None


def _rt_command_timing_text(event: dict[str, Any], *, time_basis: str) -> str | None:
    released_wait = _released_wait_timing_text(event, time_basis=time_basis)
    meta = event.get("meta") or {}
    lane = str(event.get("_base_lane", event.get("lane", "")))
    if ("planned_rt_end_ns" not in meta or not re.match(r"(?:branch_compare\.(?:taken|fallthrough)\.)*rt\.", lane)
            or meta.get("pending") or meta.get("interrupted_by_fault") or meta.get("trigger_arrival_unknown")
            or _rt_time_is_nominal(meta, aligned=time_basis == "aligned")):
        return released_wait
    cutoff = _metadata_time_in_basis(event, "observation_cutoff_ns", time_basis=time_basis)
    planned = _metadata_time_in_basis(event, "planned_rt_end_ns", time_basis=time_basis)
    start = _event_time_number(event, "t0", fallback=math.nan, time_basis=time_basis)
    end = _event_time_number(event, "t1", fallback=math.nan, time_basis=time_basis)
    if cutoff is not None and planned is not None and start <= cutoff == end < planned:
        observed = released_wait or f"Start {start} ns · Observation cutoff {cutoff} ns"
        return f"{observed} · Planned end {planned} ns"
    return released_wait


def _trigger_rt_timing_text(meta: dict[str, Any], start: Any, end: Any, *, aligned: bool = False) -> str:
    nominal = _rt_time_is_nominal(meta, aligned=aligned)
    if meta.get("trigger_arrival_unknown"):
        return f"{'Nominal arm' if nominal else 'Arm'} {start} → waiting for trigger (completion unresolved)"
    if nominal:
        return f"Nominal RT {start} → {end} (trigger arrival unresolved)"
    return f"{start} -> {end}"


def _wait_projection_key(event: dict[str, Any], kind: str, *, legacy: bool) -> tuple | None:
    meta = event.get("meta") or {}
    scope = (event.get("sequencer_id"), kind, *(meta.get(field) for field in (
        "branch_comparison_kind", "branch_comparison_branch_id", "branch_comparison_path",
        "loop_preview", "loop_iteration_index", "diff_status",
    )))
    if not legacy:
        packet = meta.get("rt_packet_id")
        return (*scope, "packet", packet) if packet is not None else None
    source = event.get("source")
    start = _value_number(event.get("t0"), fallback=math.nan)
    if math.isfinite(start) and isinstance(source, dict) and source.get("file") and type(source.get("line")) is int:
        return (*scope, "source", source["file"], source["line"], start)
    return None


def _completed_wait_aliases(events: list[dict[str, Any]]) -> dict[str, list[str]]:
    primary_kinds = {"trigger_wait": "wait_trigger", "sync_wait": "wait_sync"}
    candidates: dict[tuple, list[tuple[str, int | float]]] = {}
    for event in events:
        kind = event.get("kind")
        if kind not in primary_kinds.values() or (event.get("meta") or {}).get("pending"):
            continue
        end = _value_number(event.get("t1"), fallback=math.nan)
        if not math.isfinite(end):
            continue
        for legacy in (False, True):
            key = _wait_projection_key(event, kind, legacy=legacy)
            if key is not None:
                candidates.setdefault(key, []).append((event["id"], end))
    aliases: dict[str, list[str]] = {}
    for event in events:
        kind = primary_kinds.get(event.get("kind"))
        meta = event.get("meta") or {}
        if kind is None or meta.get("pending"):
            continue
        legacy = meta.get("rt_packet_id") is None
        key = _wait_projection_key(event, kind, legacy=legacy)
        matches = candidates.get(key, [])
        if legacy:
            end = _value_number(event.get("t1"), fallback=math.nan)
            matches = [(primary, limit) for primary, limit in matches if end <= limit]
        if len(matches) == 1:
            aliases.setdefault(matches[0][0], []).append(event["id"])
    return aliases


def _merge_trigger_arrivals(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple, list[dict[str, Any]]] = {}
    consumed: set[tuple[tuple, str]] = set()
    for event in events:
        meta = event.get("meta", {})
        scope = (event.get("sequencer_id"), *(meta.get(key) for key in (
            "branch_comparison_kind", "branch_comparison_branch_id", "branch_comparison_path",
            "loop_preview", "loop_iteration_index", "diff_status",
        )))
        if event.get("kind") == "wait_trigger" and isinstance(meta.get("input_event_ids"), list):
            consumed.update((scope, str(event_id)) for event_id in meta["input_event_ids"])
        if event.get("kind") != "trigger_receive" or any(meta.get(key) for key in (
            "rt_trigger_epoch", "conditional_time_unknown", "comparison_terminal",
        )):
            continue
        time = _value_number(event.get("t0"), fallback=math.nan)
        if math.isfinite(time):
            groups.setdefault((scope, event["lane"], time), []).append(event)

    collapsed_ids: set[str] = set()
    for (scope, _lane, _time), arrivals in groups.items():
        if len(arrivals) < 2:
            continue
        matched = [event for event in arrivals if (scope, event["id"]) in consumed]
        primary = matched[0] if len(matched) == 1 else arrivals[0]
        aliases = [event["id"] for event in arrivals if event is not primary]
        primary["_event_alias_ids"] = aliases
        primary["_trigger_arrival_addresses"] = [event.get("meta", {}).get("address") for event in arrivals]
        collapsed_ids.update(aliases)
    return [event for event in events if event["id"] not in collapsed_ids]


def _q1_only_loop_ids(events: list[dict[str, Any]]) -> set[str]:
    loops = [event for event in events if event.get("kind") == "loop_block"]
    if not loops:
        return set()
    by_id = {event["id"]: event for event in events}
    hidden: set[str] = set()
    preview_members = set()
    for loop in loops:
        meta = loop.get("meta", {})
        period = meta.get("period")
        if (not isinstance(period, dict) or period.get("kind") != "concrete" or _plain_number(period.get("value")) != 0
                or meta.get("rt_packet_affine") or _value_number(loop.get("t0"), fallback=math.nan)
                != _value_number(loop.get("t1"), fallback=math.nan)):
            continue
        if meta.get("playback"):
            member_ids = meta.get("playback_event_ids") if meta.get("complete") is True else None
        else:
            member_ids = meta.get("first_iteration_event_ids") if meta.get("period_invariant") is True else None
        if not isinstance(member_ids, list) or not member_ids:
            continue
        members = [by_id.get(event_id) for event_id in member_ids]
        q1_ids = {event["id"] for event in members if event and event.get("kind") == "q1_issue"
                  and event.get("meta", {}).get("emits_rt_packet") is False}
        if not q1_ids or not all(event and event["sequencer_id"] == loop["sequencer_id"]
                                and event.get("meta", {}).get("rt_packet_id") is None
                                and (event["id"] in q1_ids or event.get("kind") == "latched_state_pending"
                                     and event.get("meta", {}).get("q1_issue_event_id") in q1_ids)
                                for event in members):
            continue
        hidden.add(loop["id"])
        if isinstance(previews := meta.get("preview_iteration_event_ids"), dict):
            preview_members.update((loop["sequencer_id"], meta.get("loop_id"), tuple(ids))
                                   for ids in previews.values() if isinstance(ids, list))
    for event in events:
        meta = event.get("meta", {})
        if (event.get("kind") == "loop_iteration_preview" and isinstance(ids := meta.get("event_ids"), list)
                and (event["sequencer_id"], meta.get("loop_id"), tuple(ids)) in preview_members):
            hidden.add(event["id"])
    return hidden


def _group_events(events: list[dict[str, Any]], *, mode: str) -> tuple[list[dict[str, Any]], list[str]]:
    grouped_events: list[dict[str, Any]] = []
    lanes: list[str] = []
    wait_aliases = _completed_wait_aliases(events)
    collapsed_wait_ids = {alias for aliases in wait_aliases.values() for alias in aliases}
    for event in events:
        if event["id"] in collapsed_wait_ids:
            continue
        if event.get("kind") == "underflow_warning" and (
            event.get("meta", {}).get("eventual") or event.get("meta", {}).get("status") == "possible_underflow"
        ):
            continue
        if event.get("kind") in {"queue_depth", "slack", "analysis_incomplete"}:
            continue
        sequencer = event.get("sequencer_id", "")
        lane = str(event.get("lane", ""))
        if re.fullmatch(r"(?:branch_compare\.(?:taken|fallthrough)\.)?debug\.rt_queue", lane):
            continue
        diagnostic_target = None
        if event.get("kind") == "underflow_warning":
            meta = event.get("meta", {})
            diagnostic_target = next((candidate for candidate in events
                                      if meta.get("rt_packet_id") is not None
                                      and candidate.get("sequencer_id") == sequencer
                                      and str(candidate.get("lane", "")).startswith(("rt.", "branch_compare."))
                                      and candidate.get("kind") in {"play", "wait", "acquire", "upd_param", "upd_thres", "wait_sync", "wait_trigger"}
                                      and candidate.get("meta", {}).get("rt_packet_id") == meta["rt_packet_id"]
                                      and all(candidate.get("meta", {}).get(key) == meta.get(key) for key in
                                              ("branch_comparison_branch_id", "branch_comparison_path", "loop_preview", "loop_iteration_index", "diff_status"))), None)
            lane = str(diagnostic_target["lane"]) if diagnostic_target else "rt.control"
        if mode == "normal":
            branch_comparison_lane = _normal_branch_comparison_lane(event)
            if branch_comparison_lane is not None:
                grouped = f"sequencer:{sequencer} / {branch_comparison_lane}"
            else:
                if _is_normal_q1_issue_event(event):
                    base_lane = f"sequencer:{sequencer}"
                    if base_lane not in lanes:
                        lanes.append(base_lane)
                    grouped = f"{base_lane} / {NORMAL_Q1_ISSUE_LANE}"
                elif _is_debug_lane(lane) and event.get("kind") != "underflow_warning":
                    continue
                else:
                    grouped = f"sequencer:{sequencer}"
        else:
            grouped = f"sequencer:{sequencer} / {lane}"
        if grouped not in lanes:
            lanes.append(grouped)
        copied = dict(event)
        copied["_base_lane"] = lane
        copied["lane"] = grouped
        if event["id"] in wait_aliases:
            copied["_event_alias_ids"] = wait_aliases[event["id"]]
        if diagnostic_target:
            copied["_diagnostic_target"] = diagnostic_target
        grouped_events.append(copied)
    return _merge_trigger_arrivals(grouped_events), lanes


def _lane_display_label(lane: str, events: list[dict[str, Any]]) -> str:
    base_lane = _debug_lane_candidate(lane)
    if base_lane in {NORMAL_Q1_ISSUE_LANE, "debug.q1_issue"}:
        return "Q1 core"
    if base_lane == "debug.underflow":
        return "RT underflow"
    if base_lane.startswith("If ") or base_lane in {"Execute · RT", "Skip · RT"}:
        return base_lane
    parsed = _branch_comparison_lane_parts(lane)
    if parsed is None:
        return lane
    path, detail_lane = parsed
    condition_label = next((_rt_condition_path_label(event, path) for event in events
                            if event.get("meta", {}).get("branch_comparison_kind") == "rt_condition"), None)
    if condition_label:
        return f"{condition_label} / {detail_lane}"
    return f"{_branch_comparison_path_label(path)} / {detail_lane}"


def _event_display_label(event: dict[str, Any]) -> str:
    label = str(event.get("label", event.get("kind", "event")))
    meta = event.get("meta", {}) if isinstance(event.get("meta"), dict) else {}
    if event.get("kind") == "underflow_warning":
        label = underflow_label(meta)
    path = meta.get("branch_comparison_path")
    condition_label = _rt_condition_path_label(event, path)
    if condition_label:
        return f"[{condition_label}] {label}"
    if path == "taken":
        return f"[Taken] {label}"
    if path == "fallthrough":
        return f"[Fallthrough] {label}"
    return label


def _event_inline_label(event: dict[str, Any], width: float) -> str:
    token = _event_label_token(event)
    return _fit_inline_label(token, width)


def _event_label_token(event: dict[str, Any]) -> str:
    kind = str(event.get("kind", ""))
    if kind == "underflow_warning":
        return _event_display_label(event)
    if kind == "q1_issue":
        return _q1_issue_command_token(event)
    if kind == "acquire":
        return "acq"
    if kind == "wait_trigger":
        return "trig"
    if kind == "wait_sync":
        return "sync"
    if kind == "feedback_pop" or kind == "feedback_com" or kind.startswith("fb_"):
        return "fb"
    if kind == "upd_param":
        return "upd"
    if kind == "marker_state":
        return "mark"
    if kind == "branch_region":
        return "branch"
    if kind:
        return kind
    return _event_display_label(event)


def _q1_issue_command_token(event: dict[str, Any]) -> str:
    meta = event.get("meta", {}) if isinstance(event.get("meta"), dict) else {}
    op = str(meta.get("op") or "").strip()
    if op:
        return op
    source = event.get("source", {}) if isinstance(event.get("source"), dict) else {}
    raw = re.split(r"#|//", str(source.get("raw", "")), maxsplit=1)[0].strip()
    raw = re.sub(r"^[A-Za-z_][A-Za-z0-9_]*\s*:\s*", "", raw)
    command = re.match(r"[A-Za-z_][A-Za-z0-9_]*\b", raw)
    if command:
        return command.group()
    return _event_display_label(event)


def _fit_inline_label(text: str, max_width: float) -> str:
    raw = str(text or "")
    if not raw or max_width < _estimate_inline_label_width("fb"):
        return ""
    if _estimate_inline_label_width(raw) <= max_width:
        return raw
    max_chars = int((max_width - 4) // 6)
    if max_chars < 4:
        return ""
    return f"{raw[:max_chars - 1]}..."


def _estimate_inline_label_width(text: str) -> int:
    return len(str(text or "")) * 6 + 4


def _normal_branch_comparison_lane(event: dict[str, Any]) -> str | None:
    path = _branch_comparison_path(event)
    if not path:
        return None
    if _is_branch_comparison_debug_lane(str(event.get("lane", ""))):
        return None
    condition_label = _rt_condition_path_label(event, path)
    if condition_label:
        return f"{condition_label} · RT"
    return _branch_comparison_path_label(path)


def _rt_condition_path_label(event: dict[str, Any], path: str | None) -> str | None:
    if event.get("meta", {}).get("branch_comparison_kind") != "rt_condition":
        return None
    return {"taken": "Execute", "fallthrough": "Skip"}.get(path)


def _branch_comparison_path(event: dict[str, Any]) -> str | None:
    meta = event.get("meta", {}) if isinstance(event.get("meta"), dict) else {}
    path = meta.get("branch_comparison_path")
    if isinstance(path, str) and path:
        return path
    parsed = _branch_comparison_lane_parts(str(event.get("lane", "")))
    if parsed is None:
        return None
    return parsed[0]


def _is_branch_comparison_debug_lane(lane: str) -> bool:
    parsed = _branch_comparison_lane_parts(lane)
    if parsed is None:
        return False
    _path, detail_lane = parsed
    return detail_lane in DEBUG_LANES


def _branch_comparison_lane_parts(lane: str) -> tuple[str, str] | None:
    base_lane = _debug_lane_candidate(lane)
    prefix = "branch_compare."
    if not base_lane.startswith(prefix):
        return None
    remainder = base_lane.removeprefix(prefix)
    path, separator, detail_lane = remainder.partition(".")
    if not path or not separator or not detail_lane:
        return None
    return path, detail_lane


def _branch_comparison_path_label(path: str) -> str:
    return "If fallthrough" if path == "fallthrough" else f"If {path}"


def _control_flow_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chips: list[dict[str, Any]] = []
    occupied_event_ids = {str(event.get("id", "")) for event in events}
    for event in events:
        source_kind = event.get("kind")
        if source_kind == "loop_block":
            control_flow_kind = "loop"
            chip_kind = "control_flow_loop"
        elif source_kind == "branch_region":
            control_flow_kind = "branch"
            chip_kind = "control_flow_branch"
        else:
            continue

        source_event_id = str(event.get("id", ""))
        chip_event_id = _unique_control_flow_event_id(source_event_id, occupied_event_ids)
        occupied_event_ids.add(chip_event_id)
        meta = dict(event.get("meta", {})) if isinstance(event.get("meta"), dict) else {}
        meta.update(
            {
                "control_flow_source_event_id": source_event_id,
                "control_flow_kind": control_flow_kind,
            }
        )
        chip = dict(event)
        chip["id"] = chip_event_id
        chip["lane"] = "control.flow"
        chip["kind"] = chip_kind
        chip["meta"] = meta
        chips.append(chip)
    return chips


def _unique_control_flow_event_id(source_event_id: str, occupied_event_ids: set[str]) -> str:
    base_event_id = f"{source_event_id}:control-flow"
    if base_event_id not in occupied_event_ids:
        return base_event_id
    suffix = 2
    while f"{base_event_id}:{suffix}" in occupied_event_ids:
        suffix += 1
    return f"{base_event_id}:{suffix}"


def _feedback_flow_svgs(
    flows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    lanes: list[str],
    t_min: int,
    t_max: int,
    left: int,
    right: int,
    top: int,
    lane_height: int,
    time_basis: str,
    diagnostics: list[dict[str, Any]],
) -> list[str]:
    event_by_id = {str(event.get("id", "")): event for event in events}
    lane_index_by_id = {lane: index for index, lane in enumerate(lanes)}
    parts: list[str] = []
    occupied_label_rects: list[dict[str, float]] = []
    for flow in flows if isinstance(flows, list) else []:
        from_event = event_by_id.get(str(flow.get("from_event_id", "")))
        to_event = event_by_id.get(str(flow.get("to_event_id", "")))
        if from_event is None or to_event is None:
            continue
        from_lane_index = lane_index_by_id.get(from_event.get("lane"))
        to_lane_index = lane_index_by_id.get(to_event.get("lane"))
        if from_lane_index is None or to_lane_index is None:
            continue
        x1 = _scale(_event_time_number(from_event, "t1", fallback=_event_time_number(from_event, "t0", fallback=t_min, time_basis=time_basis), time_basis=time_basis), t_min, t_max, left, right)
        to_start = _event_time_number(to_event, "t0", fallback=t_min, time_basis=time_basis)
        x2 = _scale(to_event.get("_plot_t0", to_start), t_min, t_max, left, right)
        y1 = top + from_lane_index * lane_height + 11
        y2 = top + to_lane_index * lane_height + 11
        delta = x2 - x1
        direction = 1 if delta >= 0 else -1
        control = max(24.0, abs(delta) * 0.45)
        c1 = x1 + direction * control
        c2 = x2 - direction * control
        label = str(flow.get("label", "feedback flow"))
        if any(event.get("meta", {}).get("rt_trigger_epoch")
               or (time_basis == "aligned" and event.get("meta", {}).get("aligned_time_nominal"))
               for event in (from_event, to_event)):
            label += " · Logical FIFO link at nominal RT positions"
        visible_label = _feedback_flow_visible_label(flow)
        flow_diagnostics = _feedback_flow_diagnostics(flow, diagnostics)
        flow_classes = _with_diagnostic_classes("feedback-flow-group", flow_diagnostics)
        diagnostic_attributes = " ".join(_diagnostic_data_attributes(flow_diagnostics))
        if diagnostic_attributes:
            diagnostic_attributes = f" {diagnostic_attributes}"
        channel = str(flow.get("channel", ""))
        flow_id = escape(str(flow.get("id", "")), quote=True)
        from_id = escape(str(flow.get("from_event_id", "")), quote=True)
        to_id = escape(str(flow.get("to_event_id", "")), quote=True)
        from_lane = escape(str(from_event.get("lane", "")), quote=True)
        to_lane = escape(str(to_event.get("lane", "")), quote=True)
        channel_attr = escape(channel, quote=True)
        label_text = escape(visible_label)
        title_text = escape(_tooltip_with_diagnostics(label, flow_diagnostics))
        label_x = (x1 + x2) / 2
        label_y = min(y1, y2) - 7
        label_rect = _feedback_flow_label_rect(visible_label, label_x, label_y)
        label_markup = ""
        if label_rect and not any(_rects_overlap(label_rect, occupied) for occupied in occupied_label_rects):
            occupied_label_rects.append(label_rect)
            label_markup = f'<text class="feedback-flow-label" x="{label_x:.2f}" y="{label_y:.2f}">{label_text}</text>'
        parts.append(
            f'<g class="{flow_classes}" data-flow-id="{flow_id}" data-from-event-id="{from_id}" data-to-event-id="{to_id}" data-from-lane="{from_lane}" data-to-lane="{to_lane}" data-channel="{channel_attr}" data-label="{escape(label, quote=True)}"{diagnostic_attributes}>'
            f"<title>{title_text}</title>"
            f'<path class="feedback-flow" d="M {x1:.2f} {y1:.2f} C {c1:.2f} {y1:.2f}, {c2:.2f} {y2:.2f}, {x2:.2f} {y2:.2f}" marker-end="url(#feedback-arrow)" />'
            f"{label_markup}"
            "</g>"
        )
    return parts


def _feedback_flow_visible_label(flow: dict[str, Any]) -> str:
    channel = str(flow.get("channel", "")).strip()
    return f"fb ch {channel}" if channel else "fb"


def _feedback_flow_label_rect(label: str, x: float, y: float) -> dict[str, float] | None:
    if not label:
        return None
    return {"x": x, "y": y - 10, "width": len(label) * 6 + 4, "height": 12}


def _rects_overlap(a: dict[str, float], b: dict[str, float]) -> bool:
    padding = 2
    return (
        a["x"] < b["x"] + b["width"] + padding
        and a["x"] + a["width"] + padding > b["x"]
        and a["y"] < b["y"] + b["height"] + padding
        and a["y"] + a["height"] + padding > b["y"]
    )


def _control_flow_connector_svgs(
    events: list[dict[str, Any]],
    lanes: list[str],
    t_min: int,
    t_max: int,
    left: int,
    right: int,
    top: int,
    lane_height: int,
    time_basis: str,
) -> list[str]:
    lane_index_by_id = {lane: index for index, lane in enumerate(lanes)}
    parts: list[str] = []
    for event in events:
        if event.get("kind") != "control_flow_loop":
            continue
        lane_index = lane_index_by_id.get(str(event.get("lane", "")))
        if lane_index is None:
            continue
        meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
        loop_id = meta.get("loop_id")
        source_event_id = meta.get("control_flow_source_event_id") or event.get("id")
        x_start = _scale(_event_time_number(event, "t0", fallback=t_min, time_basis=time_basis), t_min, t_max, left, right)
        x_end = _scale(_event_time_number(event, "t1", fallback=t_min, time_basis=time_basis), t_min, t_max, left, right)
        if x_end <= x_start:
            continue
        y = top + lane_index * lane_height + 11
        arc_y = y - 18
        control = max(24.0, min(80.0, (x_end - x_start) * 0.35))
        source_attr = escape(str(source_event_id), quote=True)
        loop_attr = escape(str(loop_id), quote=True) if loop_id else ""
        label = escape(f"loop-back connector {loop_id or source_event_id}")
        parts.append(
            f'<path class="control-flow-connector control-flow-loop-connector" data-control-flow-source-event-id="{source_attr}" data-loop-id="{loop_attr}" '
            f'd="M {x_end:.2f} {y:.2f} C {x_end + control:.2f} {arc_y:.2f}, {x_start - control:.2f} {arc_y:.2f}, {x_start:.2f} {y:.2f}" '
            f'marker-end="url(#control-flow-arrow)" aria-label="{label}"><title>{label}</title></path>'
        )
    return parts


def _parameters_panel(analysis: dict[str, Any]) -> str:
    parameters = analysis.get("parameters")
    if not isinstance(parameters, list):
        return ""
    override_count = sum(bool(parameter.get("overridden")) for parameter in parameters)
    summary = f"Parameters · {len(parameters)}"
    if override_count:
        summary += f" · {override_count} overridden"
    rows = []
    for index, parameter in enumerate(parameters):
        annotation = ""
        if parameter.get("overridden"):
            base = parameter.get("base_value_json")
            origin = f"← base {base}" if base is not None else "added for this analysis"
            annotation = (
                '<span class="parameter-override">override</span>'
                f'<span class="parameter-base">{escape(origin)}</span>'
            )
        rows.append(
            f'<div class="parameter-row" data-parameter-index="{index}"><dt><code>{escape(parameter["name"])}</code></dt>'
            f'<dd class="parameter-value"><code>{escape(parameter["value_json"])}</code>{annotation}</dd></div>'
        )
    content = (
        '<div class="parameters-list-scroll" tabindex="0" role="region" aria-label="Analysis parameter values">'
        f'<dl class="parameters-list">{"".join(rows)}</dl></div>'
        if rows else '<p>No parameters configured for this analysis.</p>'
    )
    return (
        '<details id="q1timeline-parameters" class="analysis-parameters">'
        f'<summary><span class="parameters-title">{summary}</span>'
        '<button class="parameters-search-toggle" type="button" aria-label="Search parameters" '
        'aria-controls="q1timeline-parameter-search" aria-expanded="false" hidden>'
        '<svg aria-hidden="true" width="12" height="12" viewBox="0 0 16 16" fill="none" '
        'stroke="currentColor" stroke-width="1.5"><circle cx="6.5" cy="6.5" r="4.5"/>'
        '<path d="m10 10 4 4"/></svg> Search</button></summary>'
        '<p class="parameters-help">Values used for this timeline analysis, not live hardware or register values. '
        'Overrides do not change the parameter file.</p>'
        '<div id="q1timeline-parameter-search" class="parameters-search" hidden>'
        '<input class="parameters-search-input" type="search" placeholder="Search parameters" '
        'aria-label="Search parameters by name">'
        '<button class="parameters-search-clear" type="button" aria-label="Clear parameter search">Clear</button>'
        '<span class="parameters-search-count" role="status" aria-live="polite"></span></div>'
        f'{content}<p class="parameters-empty" hidden>No matching parameters.</p></details>'
    )


def _shared_simulation_offset(ir: dict[str, Any]) -> int | float:
    alignment = ir.get("alignment")
    if not isinstance(alignment, dict) or not alignment:
        return 0
    entries = list(alignment.values())
    offset = _plain_number(entries[0].get("offset")) if isinstance(entries[0], dict) else None
    if offset is not None and all(isinstance(entry, dict) and entry.get("clock") == "shared_simulation"
                                  and entry.get("offset") == offset for entry in entries):
        return offset
    return 0


def _network_conflict_display_message(diagnostic: dict[str, Any], offset: int | float) -> str | None:
    category = diagnostic.get("category")
    if category not in {"trigger_network_conflict", "trigger_network_possible_conflict"}:
        return None
    details = diagnostic.get("details")
    if not isinstance(details, dict):
        return None
    ticks = details.get("launch_ticks")
    network = details.get("network")
    if not isinstance(network, str) or not isinstance(ticks, list) or len(ticks) != 2:
        return None
    first, second = ticks
    if type(first) is not int or (second is not None and type(second) is not int):
        return None

    def slot(tick: int) -> str:
        time = tick / 128 + offset
        return f"{int(time) if time.is_integer() else time} ns"

    if second is None:
        return (f"Trigger network '{network}' could conflict with a request whose grid epoch is unknown. "
                f"Known launch slot at {slot(first)}.")
    qualifier = "potentially " if category == "trigger_network_possible_conflict" else ""
    return f"Trigger network '{network}' has {qualifier}conflicting launch slots at {slot(first)} and {slot(second)}."


def _diagnostics_panel(
    diagnostics: list[dict[str, Any]], event_dom_ids: dict[str, str], events: list[dict[str, Any]],
) -> str:
    counts = {severity: 0 for severity in ("error", "warning", "info", "hint")}
    for diagnostic in diagnostics:
        severity = str(diagnostic.get("severity", "info"))
        counts[severity] = counts.get(severity, 0) + 1
    summary = " · ".join(f"{counts[severity]} {severity}" + ("s" if counts[severity] != 1 else "")
                         for severity in ("error", "warning", "info", "hint") if counts[severity]) or "No issues"
    tb_evidence_ids = {event_id for diagnostic in diagnostics if diagnostic.get("category") == "feedback_write_combine"
                       for event_id in diagnostic.get("related_events", [])}
    tb_evidence = {event["id"]: event for event in events if event["id"] in tb_evidence_ids} if tb_evidence_ids else {}
    error_items = "".join(_diagnostic_item(diagnostic, event_dom_ids, tb_evidence)
                          for diagnostic in diagnostics if diagnostic.get("severity") in {"error", "warning"})
    secondary_diagnostics = [diagnostic for diagnostic in diagnostics if diagnostic.get("severity") not in {"error", "warning"}]
    secondary_items = "".join(_diagnostic_item(diagnostic, event_dom_ids, tb_evidence) for diagnostic in secondary_diagnostics[:25])
    critical_html = f'<ol class="diagnostics-critical">{error_items}</ol>' if error_items else ""
    secondary_html = ""
    if secondary_items:
        secondary_count = len(secondary_diagnostics)
        secondary_label = f"{secondary_count} note" + ("" if secondary_count == 1 else "s")
        secondary_html = (
            '<details class="diagnostics-secondary" data-default-open="false">'
            f"<summary>{escape(secondary_label)}</summary>"
            f"<ol>{secondary_items}</ol>"
            "</details>"
        )
    return (
        '<section class="diagnostics" aria-label="Diagnostics">'
        f"<h2>Diagnostics</h2>"
        f'<p class="diagnostic-summary">{escape(summary)}</p>'
        f"{critical_html}"
        f"{secondary_html}"
        "</section>"
    )


def _feedback_balance_panel(feedback_balance: dict[str, Any]) -> str:
    if not isinstance(feedback_balance, dict):
        return ""
    channels = feedback_balance.get("channels")
    if not isinstance(channels, dict) or not channels:
        return ""
    items = "".join(
        _feedback_balance_item(channel)
        for channel in sorted(channels.values(), key=lambda item: str(item.get("channel", "")) if isinstance(item, dict) else "")
        if isinstance(channel, dict)
    )
    if not items:
        return ""
    status = escape(str(feedback_balance.get("status", "balanced")))
    channel_count = sum(1 for channel in channels.values() if isinstance(channel, dict))
    channel_label = f"{channel_count} channel" + ("" if channel_count == 1 else "s")
    should_open = any(
        _feedback_balance_channel_has_issue(channel)
        for channel in channels.values()
        if isinstance(channel, dict)
    )
    open_attribute = " open" if should_open else ""
    default_open = "true" if should_open else "false"
    return (
        f'<details class="feedback-balance"{open_attribute} data-default-open="{default_open}" aria-label="Feedback FIFO balance">'
        f"<summary><span class=\"panel-title\">Feedback FIFO</span> <span class=\"panel-summary\">{status} - {channel_label}</span></summary>"
        f"<ol>{items}</ol>"
        "</details>"
    )


def _feedback_balance_channel_has_issue(channel: dict[str, Any]) -> bool:
    if str(channel.get("status", "balanced")) != "balanced":
        return True
    for field in ("unmatched_receives", "unconsumed_payloads", "ordering_unknown_receives"):
        try:
            if int(channel.get(field, 0)) > 0:
                return True
        except (TypeError, ValueError):
            return True
    return False


def _feedback_balance_item(channel: dict[str, Any]) -> str:
    channel_id = escape(str(channel.get("channel", "default")))
    status = escape(str(channel.get("status", "balanced")))
    sends = escape(str(channel.get("sends", 0)))
    payload_count = channel.get("send_payloads", 0)
    send_payloads = "unknown" if payload_count is None else escape(str(payload_count))
    receives = escape(str(channel.get("receives", 0)))
    matched = escape(str(channel.get("matched", 0)))
    discarded_payloads = escape(str(channel.get("discarded_payloads", 0)))
    unmatched_receives = escape(str(channel.get("unmatched_receives", 0)))
    unconsumed = channel.get("unconsumed_payloads", 0)
    unconsumed_payloads = "unknown" if unconsumed is None else escape(str(unconsumed))
    unknown_receives = channel.get("ordering_unknown_receives", 0)
    uncertainty = f" · receive ordering unknown={escape(str(unknown_receives))}" if unknown_receives else ""
    return (
        f'<li class="feedback-balance-channel status-{status}">'
        f"<strong>ch {channel_id}</strong> "
        f"<code>{status}</code> "
        f"matched {matched}/{receives}; sends={sends}; payloads={send_payloads}; "
        f"discarded payloads={discarded_payloads}; unmatched receives={unmatched_receives}; "
        f"unconsumed payloads={unconsumed_payloads}{uncertainty}"
        "</li>"
    )


def _diagnostic_item(
    diagnostic: dict[str, Any], event_dom_ids: dict[str, str], tb_evidence: dict[str, dict[str, Any]],
) -> str:
    severity = escape(str(diagnostic.get("severity", "info")))
    presentation = describe_diagnostic(diagnostic)
    title = escape(presentation["title"])
    source = diagnostic.get("source") if isinstance(diagnostic.get("source"), dict) else {}
    source_file = str(source.get("file") or "")
    filename = source_file.replace("\\", "/").rsplit("/", 1)[-1]
    location = f'{filename}:{source.get("line") or 1}' if filename else ""
    summary = presentation["summary"].strip()
    if presentation["category"] == "rt_queue_underflow":
        summary = RT_QUEUE_UNDERFLOW_DESCRIPTION
    details = diagnostic.get("details") or {}
    if presentation["category"] == "pending_hardware_state" and isinstance(details, dict) and isinstance(details.get("pending_fields"), list):
        summary = "Not applied: " + ", ".join(str(field) for field in details["pending_fields"])
    if source_file:
        summary = summary.replace(source_file, filename)
    if presentation["category"] == "analysis_incomplete":
        summary = re.sub(r"^(?:Underflow )?analysis incomplete:\s*", "", summary, flags=re.IGNORECASE)
    if summary.casefold().startswith(presentation["title"].casefold() + ":"):
        summary = summary[len(presentation["title"]) + 1:].strip()
    if (re.sub(r"\W+", "", summary).casefold() == re.sub(r"\W+", "", presentation["title"]).casefold()
            or summary == presentation["category"]):
        summary = ""
    conflict_index = diagnostic.get("_network_conflict_index")
    message_attrs = f' data-network-conflict-index="{conflict_index}"' if type(conflict_index) is int else ""
    message = f'<p class="diagnostic-message"{message_attrs}>{escape(summary)}</p>' if summary else ""
    related_events = diagnostic.get("related_events", [])
    related = _related_event_link(related_events, event_dom_ids, label="Show event") if related_events else ""
    if presentation["category"] == "feedback_write_combine" and len(set(related_events)) > 1:
        for event_id in dict.fromkeys(related_events):
            evidence = tb_evidence.get(event_id)
            if evidence is None:
                continue
            meta = evidence.get("meta", {})
            cutoff = _plain_number(meta.get("observation_cutoff_ns"))
            planned = _plain_number(meta.get("planned_rt_end_ns"))
            if evidence["kind"] == "feedback_contribution":
                role = "contribution"
            elif (evidence["lane"].startswith("rt.") and cutoff is not None and planned is not None
                  and cutoff == _value_number(evidence.get("t1"), fallback=math.nan) < planned):
                role = "RT cutoff"
            else:
                continue
            related += _related_event_link([event_id], event_dom_ids,
                                           label=f'Show {evidence["sequencer_id"]} {role}', tb_evidence=True)
    return (
        f'<li class="diagnostic severity-{severity}" data-diagnostic-category="{escape(presentation["category"], quote=True)}">'
        '<div class="diagnostic-heading">'
        f"<strong>{severity}</strong> <span class=\"diagnostic-title\">{title}</span>"
        f'<span class="diagnostic-source">{escape(location)}</span>'
        f'<span class="related-events">{related}</span></div>{message}'
        "</li>"
    )


def _confidence_legend(confidences: list[str]) -> str:
    if not confidences:
        return ""
    selected = set(confidences)
    items = [(confidence, label) for confidence, label in CONFIDENCE_LEGEND_ITEMS if confidence in selected]
    entries = "".join(
        f'<li data-confidence="{confidence}"><span class="legend-swatch confidence-{confidence}"></span>{escape(label)}</li>'
        for confidence, label in items
    )
    return (
        '<details id="confidence-legend" class="confidence-legend" data-default-open="false" aria-label="Confidence legend">'
        '<summary><span class="panel-title">Visible Timeline Confidence</span></summary>'
        f"<ul>{entries}</ul>"
        "</details>"
    )


def _visible_confidences(events: list[dict[str, Any]], *, default_mode: str) -> list[str]:
    visible: set[str] = set()
    for event in events:
        confidence = str(event.get("confidence", "exact"))
        if confidence == "exact" or confidence not in dict(CONFIDENCE_LEGEND_ITEMS):
            continue
        if _event_confidence_visible(event, default_mode=default_mode):
            visible.add(confidence)
    return [confidence for confidence, _label in CONFIDENCE_LEGEND_ITEMS if confidence in visible]


def _event_confidence_visible(event: dict[str, Any], *, default_mode: str) -> bool:
    kind = event.get("kind")
    if default_mode != "normal":
        return True
    if _is_debug_lane(str(event.get("lane", ""))):
        return False
    if kind == "q1_issue":
        return False
    if kind == "loop_block":
        return False
    if _is_normal_feedback_collapsed_kind(kind):
        return False
    if kind == "branch_region":
        return True
    return True


def _semantic_panel(semantic: dict[str, Any], event_dom_ids: dict[str, str]) -> str:
    annotations = semantic.get("annotations", []) if isinstance(semantic, dict) else []
    if not annotations:
        return ""
    items = "".join(_semantic_item(annotation, event_dom_ids) for annotation in annotations[:25])
    return (
        '<section class="semantic-annotations" aria-label="Semantic annotations">'
        "<h2>Semantic Annotations</h2>"
        f"<ol>{items}</ol>"
        "</section>"
    )


def _semantic_item(annotation: dict[str, Any], event_dom_ids: dict[str, str]) -> str:
    label = escape(str(annotation.get("label", "")))
    kind = escape(str(annotation.get("kind", "annotation")))
    links = "".join(_semantic_event_link(event_id, event_dom_ids) for event_id in annotation.get("event_ids", []))
    return f'<li class="semantic-annotation"><code>{kind}</code> {label}<span>{links}</span></li>'


def _semantic_event_link(event_id: Any, event_dom_ids: dict[str, str]) -> str:
    raw_id = str(event_id)
    return (
        f' <a href="#{escape(_event_dom_id(raw_id, event_dom_ids), quote=True)}" '
        f'data-semantic-event-id="{escape(raw_id, quote=True)}">{escape(raw_id)}</a>'
    )


def _related_event_link(
    event_ids: list[str], event_dom_ids: dict[str, str], *, label: str | None = None, tb_evidence: bool = False,
) -> str:
    raw_id = event_ids[0]
    candidates = escape(json.dumps(event_ids), quote=True)
    attrs = " data-tb-evidence hidden" if tb_evidence else ""
    return (
        f' <a href="#{escape(_event_dom_id(raw_id, event_dom_ids), quote=True)}" '
        f'data-related-event-id="{escape(raw_id, quote=True)}" data-related-event-ids="{candidates}"{attrs}>{escape(label or raw_id)}</a>'
    )


def _event_dom_ids(events: list[dict[str, Any]]) -> dict[str, str]:
    event_ids = [str(event.get("id", "")) for event in events]
    base_counts: dict[str, int] = {}
    for event_id in event_ids:
        base = _event_dom_id_base(event_id)
        base_counts[base] = base_counts.get(base, 0) + 1

    mapping: dict[str, str] = {}
    used: set[str] = set()
    for event_id in event_ids:
        base = _event_dom_id_base(event_id)
        dom_id = base
        if base_counts[base] > 1:
            dom_id = f"{base}--{_event_dom_id_suffix(event_id)}"
        unique_dom_id = dom_id
        index = 2
        while unique_dom_id in used:
            unique_dom_id = f"{dom_id}--{index}"
            index += 1
        mapping[event_id] = unique_dom_id
        used.add(unique_dom_id)
    for event in events:
        for alias in event.get("_event_alias_ids", ()):
            mapping[alias] = mapping[event["id"]]
    return mapping


def _event_dom_id(event_id: str, event_dom_ids: dict[str, str] | None = None) -> str:
    if event_dom_ids is not None:
        return event_dom_ids.get(event_id, _event_dom_id_base(event_id))
    return _event_dom_id_base(event_id)


def _event_dom_id_base(event_id: str) -> str:
    return "event-" + re.sub(r"[^A-Za-z0-9_-]+", "-", event_id).strip("-")


def _event_dom_id_suffix(event_id: str) -> str:
    encoded = base64.urlsafe_b64encode(event_id.encode("utf-8")).decode("ascii").rstrip("=")
    return encoded or "empty"


def _validate_ir(ir: dict[str, Any]) -> None:
    if not isinstance(ir, dict):
        raise RenderError(f"TimelineIR must be a JSON object, got {type(ir).__name__}")
    _reject_non_string_object_keys(ir, path="TimelineIR")
    _reject_surrogate_strings(ir, path="TimelineIR")
    _reject_non_finite_numbers(ir, path="TimelineIR")
    version = ir.get("version")
    if version != SUPPORTED_IR_VERSION:
        raise RenderError(f"Unsupported TimelineIR version: {version!r}")
    for field in ("events", "diagnostics", "feedback_flows"):
        if field in ir and not isinstance(ir[field], list):
            raise RenderError(f"TimelineIR field {field!r} must be a list, got {type(ir[field]).__name__}")
        for index, item in enumerate(ir.get(field, [])):
            if not isinstance(item, dict):
                raise RenderError(
                    f"TimelineIR field {field!r} entry {index} must be a JSON object, got {type(item).__name__}"
                )
    valid_event_ids = _validate_unique_event_ids(ir.get("events", []))
    simulations = ir.get("simulation", {})
    for index, event in enumerate(ir.get("events", [])):
        _validate_event_required_fields(event, index=index)
        simulation = simulations.get(event["sequencer_id"]) if isinstance(simulations, dict) else None
        fractional = isinstance(simulation, dict) and simulation.get("clock_tick_ns") == 1 / 128
        concrete_times = {
            field: _validate_concrete_time_value(event.get(field), fractional=fractional)
            for field in ("t0", "t1", "duration")
        }
        _validate_event_time_range(event, concrete_times)
        _validate_event_time_metadata(event, fractional=fractional)
    for index, diagnostic in enumerate(ir.get("diagnostics", [])):
        related_events = diagnostic.get("related_events", [])
        if not isinstance(related_events, list):
            raise RenderError(
                f"TimelineIR diagnostics entry {index} field 'related_events' must be a list, "
                f"got {type(related_events).__name__}"
            )
        for related_index, event_id in enumerate(related_events):
            if not isinstance(event_id, str):
                raise RenderError(
                    "TimelineIR diagnostics entry "
                    f"{index} field 'related_events' item {related_index} must be a string, "
                    f"got {type(event_id).__name__}"
                )
            if event_id not in valid_event_ids:
                raise RenderError(
                    f"TimelineIR diagnostics entry {index} related event {event_id!r} "
                    "does not match any event id"
                )
    for index, flow in enumerate(ir.get("feedback_flows", [])):
        for field in ("from_event_id", "to_event_id"):
            event_id = flow.get(field)
            if not isinstance(event_id, str):
                raise RenderError(
                    f"TimelineIR feedback_flows entry {index} field '{field}' must be a string, "
                    f"got {type(event_id).__name__}"
                )
            if event_id not in valid_event_ids:
                raise RenderError(
                    f"TimelineIR feedback_flows entry {index} field '{field}' event id {event_id!r} "
                    "does not match any event id"
                )
    if "semantic" in ir:
        semantic = ir["semantic"]
        if not isinstance(semantic, dict):
            raise RenderError(f"TimelineIR field 'semantic' must be a JSON object, got {type(semantic).__name__}")
        if "annotations" in semantic:
            annotations = semantic["annotations"]
            if not isinstance(annotations, list):
                raise RenderError(
                    f"TimelineIR field 'semantic.annotations' must be a list, got {type(annotations).__name__}"
                )
            for index, annotation in enumerate(annotations):
                if not isinstance(annotation, dict):
                    raise RenderError(
                        "TimelineIR field 'semantic.annotations' entry "
                        f"{index} must be a JSON object, got {type(annotation).__name__}"
                    )
                annotation_event_ids = annotation.get("event_ids", [])
                if not isinstance(annotation_event_ids, list):
                    raise RenderError(
                        f"TimelineIR semantic.annotations entry {index} field 'event_ids' must be a list, "
                        f"got {type(annotation_event_ids).__name__}"
                    )
                for event_id_index, event_id in enumerate(annotation_event_ids):
                    if not isinstance(event_id, str):
                        raise RenderError(
                            "TimelineIR semantic.annotations entry "
                            f"{index} field 'event_ids' item {event_id_index} must be a string, "
                            f"got {type(event_id).__name__}"
                        )
                    if event_id not in valid_event_ids:
                        raise RenderError(
                            f"TimelineIR semantic.annotations entry {index} event id {event_id!r} "
                            "does not match any event id"
                        )


def _validate_unique_event_ids(events: list[dict[str, Any]]) -> set[str]:
    seen: set[str] = set()
    for index, event in enumerate(events):
        event_id = event.get("id")
        if not isinstance(event_id, str) or event_id == "":
            raise RenderError(
                f"TimelineIR events entry {index} field 'id' must be a non-empty string, "
                f"got {type(event_id).__name__}"
            )
        if event_id in seen:
            raise RenderError(f"TimelineIR events contain duplicate event id {event_id!r} at index {index}")
        seen.add(event_id)
    return seen


def _validate_event_required_fields(event: dict[str, Any], *, index: int) -> None:
    required_fields = {
        "sequencer_id": str,
        "lane": str,
        "kind": str,
        "t0": dict,
        "t1": (dict, type(None)),
        "duration": dict,
        "label": str,
        "confidence": str,
        "source": dict,
        "meta": dict,
    }
    for field, expected_type in required_fields.items():
        if field not in event:
            raise RenderError(f"TimelineIR events entry {index} missing required field '{field}'")
        value = event[field]
        if not isinstance(value, expected_type):
            expected_names = (
                " or ".join(type_.__name__ for type_ in expected_type)
                if isinstance(expected_type, tuple)
                else expected_type.__name__
            )
            raise RenderError(
                f"TimelineIR events entry {index} field '{field}' must be a {expected_names}, "
                f"got {type(value).__name__}"
            )


def _validate_concrete_time_value(value: Any, *, fractional: bool = False) -> int | float | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RenderError(f"TimelineIR time value must be a JSON object or null, got {type(value).__name__}")
    kind = value.get("kind")
    if kind not in SUPPORTED_TIME_VALUE_KINDS:
        raise RenderError(f"unsupported TimelineIR time value kind: {kind!r}")
    if kind != "concrete":
        return None
    raw_value = value.get("value")
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise RenderError(f"TimelineIR concrete time value must be numeric, got {raw_value!r}")
    if not _valid_time_number(raw_value, fractional=fractional):
        unit = "integer nanoseconds or exact simulation ticks" if fractional else "integer nanoseconds"
        raise RenderError(f"TimelineIR concrete time value must be {unit}, got {raw_value!r}")
    return raw_value


def _validate_event_time_range(event: dict[str, Any], concrete_times: dict[str, int | float | None]) -> None:
    t0 = concrete_times["t0"]
    t1 = concrete_times["t1"]
    duration = concrete_times["duration"]
    event_id = event.get("id", "<unknown>")
    if duration is not None and duration < 0:
        raise RenderError(f"TimelineIR event {event_id!r} has negative duration: {duration}")
    if t0 is not None and t1 is not None and t1 < t0:
        raise RenderError(f"TimelineIR event {event_id!r} has t1 before t0: {t1} < {t0}")
    if t0 is not None and t1 is not None and duration is not None and duration != t1 - t0:
        raise RenderError(
            f"TimelineIR event {event_id!r} has duration inconsistent with t1 - t0: "
            f"{duration} != {t1 - t0}"
        )


def _validate_event_time_metadata(event: dict[str, Any], *, fractional: bool) -> None:
    meta = event.get("meta")
    if not isinstance(meta, dict):
        return
    for field in ("aligned_t0", "aligned_t1", "local_t0", "local_t1"):
        if field in meta:
            edge = "t0" if field.endswith("_t0") else "t1"
            _validate_time_metadata_value(meta[field], field=field, source_value=event.get(edge), fractional=fractional)
    _validate_time_metadata_pair(event, meta, start_field="aligned_t0", end_field="aligned_t1")
    _validate_time_metadata_pair(event, meta, start_field="local_t0", end_field="local_t1")
    _validate_time_metadata_range(meta, start_field="aligned_t0", end_field="aligned_t1")
    _validate_time_metadata_range(meta, start_field="local_t0", end_field="local_t1")


def _validate_time_metadata_pair(
    event: dict[str, Any],
    meta: dict[str, Any],
    *,
    start_field: str,
    end_field: str,
) -> None:
    if start_field in meta and end_field not in meta and _is_concrete_time_value_object(event.get("t1")):
        raise RenderError(f"TimelineIR meta.{start_field} and meta.{end_field} must be provided together")
    if end_field in meta and start_field not in meta and _is_concrete_time_value_object(event.get("t0")):
        raise RenderError(f"TimelineIR meta.{start_field} and meta.{end_field} must be provided together")


def _is_concrete_time_value_object(value: Any) -> bool:
    return isinstance(value, dict) and value.get("kind") == "concrete"


def _validate_time_metadata_range(meta: dict[str, Any], *, start_field: str, end_field: str) -> None:
    start = _plain_number(meta.get(start_field))
    end = _plain_number(meta.get(end_field))
    if start is not None and end is not None and end < start:
        raise RenderError(f"TimelineIR meta.{end_field} must not be before meta.{start_field}: {end} < {start}")


def _valid_time_number(value: Any, *, fractional: bool) -> bool:
    return type(value) is int or fractional and type(value) is float and (value * 128).is_integer()


def _validate_time_metadata_value(value: Any, *, field: str, source_value: Any, fractional: bool) -> None:
    if not _valid_time_number(value, fractional=fractional) and not _is_symbolic_time_metadata_string(value, source_value=source_value):
        unit = "integer nanoseconds or exact simulation ticks" if fractional else "integer nanoseconds"
        raise RenderError(f"TimelineIR meta.{field} time value must be {unit}, got {value!r}")


def _is_symbolic_time_metadata_string(value: Any, *, source_value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if _INTEGER_TIME_METADATA_TEXT_RE.match(value.strip()):
        return False
    if not isinstance(source_value, dict):
        return False
    return source_value.get("kind") in {"symbolic", "unknown", "runtime_dependent", "range"}


def _reject_non_string_object_keys(value: Any, *, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise RenderError(f"{path} contains non-string object key {key!r}; object keys must be strings")
            _reject_non_string_object_keys(item, path=f"{path}.{_path_token(key)}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_non_string_object_keys(item, path=f"{path}[{index}]")


def _reject_surrogate_strings(value: Any, *, path: str) -> None:
    if isinstance(value, str):
        if _contains_surrogate(value):
            raise RenderError(f"{path} contains Unicode surrogate code point")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and _contains_surrogate(key):
                raise RenderError(f"{path} contains Unicode surrogate code point in object key")
            _reject_surrogate_strings(item, path=f"{path}.{_path_token(key)}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_surrogate_strings(item, path=f"{path}[{index}]")


def _contains_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(char) <= 0xDFFF for char in value)


def _path_token(value: Any) -> str:
    token = str(value).replace("\n", " ")
    if len(token) > 40:
        return token[:37] + "..."
    return token


def _reject_non_finite_numbers(value: Any, *, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise RenderError(f"{path} contains non-finite numeric value")
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_non_finite_numbers(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_non_finite_numbers(item, path=f"{path}[{index}]")


def _is_debug_lane(lane: str) -> bool:
    candidate = _debug_lane_candidate(lane)
    return candidate in DEBUG_LANES or candidate.startswith("branch_compare.")


def _is_normal_q1_issue_event(event: dict[str, Any]) -> bool:
    return event.get("kind") == "q1_issue" and str(event.get("lane", "")) == "debug.q1_issue"


def _is_q1_issue_detail_lane(lane: str) -> bool:
    return _debug_lane_candidate(lane) in {NORMAL_Q1_ISSUE_LANE, "debug.q1_issue"}


def _debug_lane_candidate(lane: str) -> str:
    if " / " in lane:
        return lane.split(" / ", 1)[1]
    return lane


def _sequencer_from_lane(lane: str) -> str:
    return lane.split(" / ", 1)[0]


def _time_extent(events: list[dict[str, Any]], *, time_basis: str) -> tuple[int | float, int | float]:
    values = []
    for event in events:
        t0 = _event_time_number(event, "t0", fallback=0, time_basis=time_basis)
        values.append(t0)
        values.append(_event_time_number(event, "t1", fallback=t0, time_basis=time_basis))
    if not values:
        return 0, 1
    start = min(values)
    end = max(values)
    if end <= start:
        end = start + 1
    return start, end


def _default_time_basis(events: list[dict[str, Any]]) -> str:
    return "aligned" if any(_has_aligned_time(event) for event in events) else "local"


def _shared_simulation_rt_anchor(ir: dict[str, Any], events: list[dict[str, Any]]) -> str | None:
    alignment = ir.get("alignment")
    if not isinstance(alignment, dict):
        return None
    anchors = {entry["anchor_event_id"] for entry in alignment.values() if isinstance(entry, dict)
               and entry.get("clock") == "shared_simulation"
               and isinstance(entry.get("anchor_event_id"), str)}
    return next((event["kind"] for event in events if event["id"] in anchors
                 and (event.get("kind") == "rt_queue_dequeue" or str(event.get("lane", "")).startswith("rt."))), None)


def _has_aligned_time(event: dict[str, Any]) -> bool:
    meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
    return _plain_number(meta.get("aligned_t0")) is not None or _plain_number(meta.get("aligned_t1")) is not None


def _ticks(t_min: int | float, t_max: int | float) -> list[int | float]:
    if t_max <= t_min:
        return [t_min]
    step = max(1, (t_max - t_min) // 4)
    ticks = [tick for index in range(5) if (tick := t_min + step * index) <= t_max]
    if ticks[-1] != t_max:
        ticks.append(t_max)
    return sorted(set(ticks))


def _scale(value: int, t_min: int, t_max: int, left: int, right: int) -> float:
    if t_max <= t_min:
        return float(left)
    return left + (right - left) * ((value - t_min) / (t_max - t_min))


def _value_number(value: Any, *, fallback: int | float) -> int | float:
    if isinstance(value, dict) and value.get("kind") == "concrete":
        number = _plain_number(value.get("value"))
        if number is None:
            raise RenderError(f"TimelineIR concrete time value must be numeric, got {value.get('value')!r}")
        return number
    return fallback


def _event_time_number(event: dict[str, Any], edge: str, *, fallback: int | float, time_basis: str) -> int | float:
    if time_basis == "aligned":
        meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
        aligned = _plain_number(meta.get(f"aligned_{edge}"))
        if aligned is not None:
            return aligned
    return _value_number(event.get(edge), fallback=fallback)


def _plain_number(value: Any) -> int | float | None:
    return value if type(value) in (int, float) else None


def _display_value(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, dict):
        return str(value.get("display", value))
    return str(value)


def _event_classes(event: dict[str, Any], *, confidence: Any, kind: Any, selected: bool = False) -> str:
    classes = [
        "event",
    ]
    if selected:
        classes.append("is-selected")
    classes.extend([f"confidence-{_class_token(confidence)}", f"kind-{_class_token(kind)}"])
    family = _event_command_family(event)
    if family:
        classes.append(f"command-family-{family}")
    status = event.get("meta", {}).get("status") if isinstance(event.get("meta"), dict) else None
    if status:
        classes.append(f"status-{_class_token(status)}")
    diff_status = event.get("meta", {}).get("diff_status") if isinstance(event.get("meta"), dict) else None
    if diff_status:
        classes.append(f"diff-{_class_token(diff_status)}")
    branch_comparison_path = event.get("meta", {}).get("branch_comparison_path") if isinstance(event.get("meta"), dict) else None
    if branch_comparison_path:
        classes.append(f"branch-path-{_class_token(branch_comparison_path)}")
    return " ".join(classes)


def _event_command_family(event: dict[str, Any]) -> str | None:
    kind = str(event.get("kind", ""))
    if kind in {"unknown_region", "underflow_warning", "analysis_incomplete"}:
        return None
    if kind in {"loop_block", "loop_iteration_preview", "branch_region", "control_flow_loop", "control_flow_branch"}:
        return "flow"
    meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    raw = re.sub(r"^[A-Za-z_][A-Za-z0-9_]*\s*:\s*", "", str(source.get("raw", "")).strip())
    op = str(meta.get("op") or (raw.split(None, 1)[0] if raw else "")).strip().lower()
    if not op:
        if kind in {"feedback_pop", "feedback_com"}:
            return "feedback"
        if kind == "marker_state":
            return "rt"
        op = kind
    spec = INSTRUCTION_TABLE.get(op)
    if spec is None:
        return None
    if op.startswith("fb_"):
        return "feedback"
    if spec.category == "branch" or op == "stop":
        return "flow"
    if spec.category in {"classical", "control"}:
        return "q1"
    return spec.category


def _is_normal_feedback_collapsed_kind(kind: Any) -> bool:
    return str(kind) in NORMAL_FEEDBACK_COLLAPSED_EVENT_KINDS


def _diagnostic_overlays_by_event(
    diagnostics: Any,
    events: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    overlays: dict[str, list[dict[str, Any]]] = {}
    event_by_id = {str(event.get("id", "")): event for event in events}
    for event_id, event in event_by_id.items():
        if event.get("kind") != "underflow_warning":
            continue
        target = str(event.get("_diagnostic_target", event)["id"])
        message = event.get("meta", {}).get("diagnostic_message")
        overlays.setdefault(target, []).append({
            "severity": "error", "category": "rt_queue_underflow" if message else "definite_underflow",
            "message": message or _event_display_label(event), "underflow_event_id": event_id,
        })
    event_by_id.update((alias, event) for event in events for alias in event.get("_event_alias_ids", ()))
    for diagnostic in diagnostics if isinstance(diagnostics, list) else []:
        if not isinstance(diagnostic, dict) or diagnostic.get("severity") != "error":
            continue
        related_events = diagnostic.get("related_events", [])
        if not isinstance(related_events, list):
            continue
        if any(event_by_id.get(str(event_id), {}).get("kind") == "underflow_warning" for event_id in related_events):
            continue
        if not related_events and isinstance(diagnostic.get("source"), dict):
            source = diagnostic["source"]
            related_events = [str(event["id"]) for event in events if event.get("kind") == "q1_issue"
                              and source.get("file") and event.get("source", {}).get("file") == source["file"]
                              and event.get("source", {}).get("line") == source.get("line")]
        event_id = next((event_id for event_id in related_events if isinstance(event_id, str) and event_id in event_by_id), None)
        if event_id:
            overlays.setdefault(event_by_id[event_id]["id"], []).append(diagnostic)
    return overlays


def _feedback_flow_diagnostics(flow: dict[str, Any], diagnostics: Any) -> list[dict[str, Any]]:
    if not isinstance(diagnostics, list):
        return []
    from_event_id = str(flow.get("from_event_id", ""))
    to_event_id = str(flow.get("to_event_id", ""))
    result: list[dict[str, Any]] = []
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            continue
        if str(diagnostic.get("category", "")) not in FEEDBACK_OVERLAY_CATEGORIES:
            continue
        related = diagnostic.get("related_events", [])
        if not isinstance(related, list):
            continue
        related_ids = {event_id for event_id in related if isinstance(event_id, str)}
        if from_event_id in related_ids and to_event_id in related_ids:
            result.append(diagnostic)
    return result


def _with_diagnostic_classes(base_classes: str, diagnostics: list[dict[str, Any]]) -> str:
    classes = [base_classes]
    classes.extend(_diagnostic_class_names(diagnostics))
    return " ".join(class_name for class_name in classes if class_name)


def _diagnostic_class_names(diagnostics: list[dict[str, Any]]) -> list[str]:
    if not diagnostics:
        return []
    severity = _diagnostic_severity(diagnostics)
    classes = ["has-diagnostic", f"diagnostic-{_class_token(severity)}"]
    classes.extend(f"diagnostic-category-{_class_token(category)}" for category in _diagnostic_categories(diagnostics))
    return classes


def _diagnostic_data_attributes(diagnostics: list[dict[str, Any]]) -> list[str]:
    if not diagnostics:
        return []
    severity = _diagnostic_severity(diagnostics)
    categories = ",".join(_diagnostic_categories(diagnostics))
    messages = "\n".join(str(item.get("message") or item.get("category", "")) for item in diagnostics)
    return [
        f'data-diagnostic-severity="{escape(severity, quote=True)}"',
        f'data-diagnostic-categories="{escape(categories, quote=True)}"',
        f'data-diagnostic-message="{escape(messages, quote=True)}"',
    ]


def _diagnostic_severity(diagnostics: list[dict[str, Any]]) -> str:
    severities = [str(diagnostic.get("severity", "info")) for diagnostic in diagnostics if isinstance(diagnostic, dict)]
    if not severities:
        return "info"
    return max(severities, key=lambda severity: DIAGNOSTIC_SEVERITY_RANK.get(severity, DIAGNOSTIC_SEVERITY_RANK["info"]))


def _diagnostic_categories(diagnostics: list[dict[str, Any]]) -> list[str]:
    categories: list[str] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        category = str(diagnostic.get("category", "diagnostic"))
        if category in seen:
            continue
        seen.add(category)
        categories.append(category)
    return categories


def _tooltip_with_diagnostics(base_tooltip: str, diagnostics: list[dict[str, Any]]) -> str:
    if not diagnostics:
        return base_tooltip
    lines = [base_tooltip, "", "Diagnostics:"]
    for diagnostic in diagnostics[:4]:
        presentation = describe_diagnostic(diagnostic)
        lines.append(f"- {presentation['severity']}: {presentation['title']} - {presentation['summary']}")
    if len(diagnostics) > 4:
        lines.append(f"- {len(diagnostics) - 4} more diagnostic(s)")
    return "\n".join(lines)




def _class_token(value: Any) -> str:
    token = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value).strip())
    return token.strip("-") or "unknown"


def _search_text(event: dict[str, Any]) -> str:
    source = event.get("source", {})
    fields = [
        event.get("id", ""),
        event.get("kind", ""),
        event.get("_base_lane", event.get("lane", "")),
        event.get("label", ""),
        event.get("confidence", ""),
        source.get("file", "") if isinstance(source, dict) else "",
        str(source.get("line", "")) if isinstance(source, dict) else "",
        source.get("raw", "") if isinstance(source, dict) else "",
        *event.get("_event_alias_ids", ()),
        *event.get("_trigger_arrival_addresses", ()),
    ]
    meta = event.get("meta", {})
    if isinstance(meta, dict):
        fields.append(json.dumps(meta, sort_keys=True))
    return " ".join(str(field).lower() for field in fields)


def _json_script(value: dict[str, Any]) -> str:
    return (
        json.dumps(value, sort_keys=True, allow_nan=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def _css() -> str:
    return """
body { margin: 0; font-family: Arial, sans-serif; color: var(--vscode-foreground, #17202a); }
body { --q1timeline-error-color: #b3242f; }
body:is(.vscode-dark, .vscode-high-contrast) { --q1timeline-error-color: #ff8187; }
body { --q1asm-q1-color: #245fa8; --q1asm-flow-color: #9a4c18; --q1asm-latched-color: #176b7e; --q1asm-rt-color: #27763c; --q1asm-sync-color: #7251a3; --q1asm-feedback-color: #ad316c; --q1asm-event-background: var(--vscode-editor-background, #ffffff); }
body.vscode-light { color-scheme: light; }
body.vscode-dark { color-scheme: dark; background: #1e1e1e !important; background-color: #1e1e1e !important; }
body.vscode-dark, body.vscode-high-contrast { --q1asm-q1-color: #80b7ff; --q1asm-flow-color: #f4a261; --q1asm-latched-color: #6ec6d9; --q1asm-rt-color: #8bcf9a; --q1asm-sync-color: #b9a7dc; --q1asm-feedback-color: #ff6fb3; --q1asm-event-background: var(--vscode-editor-background, #1e1e1e); }
body.vscode-high-contrast { color-scheme: dark; }
.command-family-q1 { --q1asm-command-color: var(--q1asm-q1-color); }
.command-family-flow { --q1asm-command-color: var(--q1asm-flow-color); }
.command-family-latched { --q1asm-command-color: var(--q1asm-latched-color); }
.command-family-rt { --q1asm-command-color: var(--q1asm-rt-color); }
.command-family-sync { --q1asm-command-color: var(--q1asm-sync-color); }
.command-family-feedback { --q1asm-command-color: var(--q1asm-feedback-color); }
[class*="command-family-"] { --q1asm-event-fill: color-mix(in srgb, var(--q1asm-command-color) 15%, var(--q1asm-event-background)); --q1asm-event-stroke: color-mix(in srgb, var(--q1asm-command-color) 65%, var(--q1asm-event-background)); --q1asm-event-label-color: var(--q1asm-command-color); }
header { display: flex; align-items: center; justify-content: space-between; padding: 14px 18px; border-bottom: 1px solid var(--vscode-panel-border, #d8dee6); }
h1 { font-size: 18px; margin: 0; font-weight: 700; }
h2 { font-size: 14px; margin: 0 0 6px; }
.controls { display: flex; align-items: center; gap: 10px; }
#event-filter { min-width: 220px; border: 1px solid #9aa7b4; border-radius: 4px; padding: 6px 8px; font: inherit; }
button { border: 1px solid #9aa7b4; background: #fff; padding: 6px 10px; border-radius: 4px; cursor: pointer; }
body.vscode-dark button { background: var(--vscode-button-secondaryBackground, #2d2d2d); color: var(--vscode-button-secondaryForeground, #f3f4f6); }
button[aria-pressed="true"] { background: #17202a; color: #fff; }
.analysis-details { margin: 0 16px 16px; }
.analysis-details > summary { cursor: pointer; padding: 8px 0; font-weight: 700; }
.analysis-details .event-inspector, .analysis-details .q1-hardware { margin: 12px 0; }
.analysis-parameters { margin: 0 16px 8px; font-size: 12px; container-type: inline-size; }
.analysis-parameters > summary { cursor: pointer; padding: 8px 0; font-size: 14px; font-weight: 700; }
.analysis-parameters .parameters-search-toggle { float: right; display: inline-flex; align-items: center; gap: 4px; font-size: 12px; font-weight: normal; }
.parameters-help { margin: 0 0 8px; color: var(--vscode-descriptionForeground, #4b5563); }
.parameters-list-scroll { max-height: 240px; overflow: auto; }
.parameters-list { display: grid; grid-template-columns: minmax(0, 1fr); column-gap: 24px; margin: 0; }
.parameter-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-content: start; padding: 4px 0; border-bottom: 1px solid var(--vscode-panel-border, #d8dee6); overflow-wrap: anywhere; }
.parameter-row[hidden], .parameters-search[hidden], .parameters-search-toggle[hidden] { display: none; }
.parameter-row dt { min-width: 0; padding-right: 12px; }
.parameter-value { min-width: 0; margin: 0; }
@container (min-width: 720px) { .parameters-list { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.parameters-search { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 8px; }
.parameters-search-count { margin-left: auto; color: var(--vscode-descriptionForeground, #4b5563); white-space: nowrap; }
.parameters-empty { color: var(--vscode-descriptionForeground, #4b5563); }
.analysis-parameters code { font-family: var(--vscode-editor-font-family, monospace); white-space: pre-wrap; }
.parameter-override { display: inline-block; margin-left: 8px; padding: 0 4px; border: 1px solid var(--vscode-panel-border, #d8dee6); border-radius: 3px; font-size: 11px; }
.parameter-base { display: block; margin-top: 2px; color: var(--vscode-descriptionForeground, #4b5563); white-space: pre-wrap; }
.parameter-actions { display: inline-flex; gap: 6px; margin-left: 8px; }
.analysis-parameters button { padding: 2px 6px; font: inherit; color: var(--vscode-button-secondaryForeground, #17202a); background: var(--vscode-button-secondaryBackground, #f3f4f6); border-color: var(--vscode-panel-border, #9aa7b4); }
.analysis-parameters button:disabled { opacity: 0.5; cursor: default; }
@media (hover: hover) and (pointer: fine) {
  .analysis-parameters button.parameter-edit { opacity: 0; }
  .parameter-row:hover .parameter-edit, .parameter-row:focus-within .parameter-edit { opacity: 1; }
}
.parameter-editor { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 6px; }
.parameter-editor[hidden] { display: none; }
.parameter-input, .parameters-search-input { flex: 1; width: 20ch; min-width: 0; max-width: 28rem; padding: 4px 6px; font-family: var(--vscode-editor-font-family, monospace); color: var(--vscode-input-foreground, #17202a); background: var(--vscode-input-background, #fff); border: 1px solid var(--vscode-input-border, #9aa7b4); border-radius: 3px; }
.parameter-input:focus-visible, .parameters-search-input:focus-visible, .analysis-parameters button:focus-visible { outline: 1px solid var(--vscode-focusBorder, #007acc); outline-offset: 2px; }
.parameter-error { flex-basis: 100%; color: var(--vscode-errorForeground, #c72c2c); }
.parameter-status { display: block; color: var(--vscode-descriptionForeground, #4b5563); }
.diagnostics { margin: 16px 16px 0; padding: 10px 12px; border: 1px solid var(--vscode-panel-border, #d8dee6); }
.diagnostics ol { margin: 8px 0 0; padding: 0; list-style: none; }
.diagnostics-secondary { margin-top: 8px; }
.diagnostics-secondary > summary,
.confidence-legend > summary,
.feedback-balance > summary { cursor: pointer; font-size: 12px; }
.panel-title { font-weight: 700; font-size: 14px; }
.panel-summary { margin-left: 6px; color: var(--vscode-descriptionForeground, #4b5563); font-size: 12px; }
.diagnostic { margin: 4px 0; font-size: 12px; }
.diagnostic-summary { margin: 0; color: var(--vscode-descriptionForeground, #4b5563); font-size: 12px; }
.diagnostic-title { font-weight: 700; }
.diagnostic-heading { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px; }
.diagnostic-source { margin-left: auto; color: var(--vscode-descriptionForeground, #4b5563); }
.diagnostic-message { margin: 3px 0 0; }
.diagnostic.severity-error :is(strong, .diagnostic-message),
.diagnostic-error-message { color: var(--q1timeline-error-color); }
.severity-warning strong { color: #a45516; }
.related-events { margin-left: 6px; }
.confidence-legend { margin: 16px 16px 0; padding: 10px 12px; border: 1px solid var(--vscode-panel-border, #d8dee6); }
.confidence-legend ul { list-style: none; display: flex; flex-wrap: wrap; gap: 12px; padding: 0; margin: 0; font-size: 12px; }
.confidence-legend li { display: inline-flex; align-items: center; gap: 5px; }
.legend-swatch { width: 11px; height: 11px; border: 1px solid #6b7280; display: inline-block; background: #dff3e4; }
.legend-swatch.confidence-symbolic { background: repeating-linear-gradient(45deg, #fff, #fff 3px, #dbeafe 3px, #dbeafe 6px); }
.legend-swatch.confidence-assumed { border-style: dashed; }
.legend-swatch.confidence-unknown { background: #e5e7eb; }
.legend-swatch.confidence-runtime_dependent { background: #fde68a; border-color: #b45309; }
.feedback-balance { margin: 16px 16px 0; padding: 10px 12px; border: 1px solid var(--vscode-panel-border, #d8dee6); }
.feedback-balance ol { margin: 8px 0 0; padding-left: 20px; }
.feedback-balance-channel { margin: 4px 0; font-size: 12px; }
.feedback-balance-summary { margin: 0; color: var(--vscode-descriptionForeground, #4b5563); font-size: 12px; }
.semantic-annotations { margin: 16px 16px 0; padding: 10px 12px; border: 1px solid var(--vscode-panel-border, #d8dee6); }
.semantic-annotations ol { margin: 8px 0 0; padding-left: 20px; }
.semantic-annotation { margin: 4px 0; font-size: 12px; }
.timeline { overflow-x: auto; padding: 16px; }
.timeline-svg { min-width: 960px; width: 100%; border: 1px solid var(--vscode-panel-border, #d8dee6); }
body.vscode-high-contrast .timeline-svg,
body.vscode-high-contrast .diagnostics,
body.vscode-high-contrast .confidence-legend,
body.vscode-high-contrast .feedback-balance,
body.vscode-high-contrast .semantic-annotations,
body.vscode-high-contrast .event-inspector { border-color: var(--vscode-contrastBorder, #ffffff); }
.lane-label, .tick-label { fill: var(--vscode-descriptionForeground, #4b5563); font-size: 12px; }
.sequencer-label { fill: var(--vscode-foreground, #17202a); font-size: 12px; font-weight: 700; }
.lane-rule, .grid { stroke: #d7dde4; stroke-width: 1; }
.event rect { stroke: var(--q1asm-event-stroke, rgba(0, 0, 0, 0.38)); stroke-width: 1; fill: var(--q1asm-event-fill, #cdbb96); }
.event.confidence-exact:not(:where(.kind-loop_block, .kind-loop_iteration_preview, .kind-control_flow_loop,
  .kind-control_flow_branch, .branch-path-taken, .branch-path-fallthrough)) > rect { stroke: none; }
body.vscode-high-contrast .event rect { stroke: var(--vscode-contrastActiveBorder, #ffffff); stroke-width: 2; }
.event text { pointer-events: none; fill: var(--q1asm-event-label-color, #0f1720); font-size: 11px; }
.event.is-selected rect { stroke: #111827; stroke-width: 2; }
.event.is-filtered { display: none; }
.event.lod-small rect { stroke-width: 1.5; }
.event.diff-added rect { stroke: #15803d; stroke-width: 3; }
.event.diff-shifted rect { stroke: #1d4ed8; stroke-width: 3; stroke-dasharray: 5 2; }
.event.diff-removed rect { stroke: #b91c1c; stroke-width: 3; opacity: 0.55; }
.event.is-related rect { stroke: #2563eb; stroke-width: 3; }
.event.is-inactive-path rect { opacity: 0.45; }
.loop-bracket { cursor: pointer; }
.loop-bracket-hitbox { fill: none; stroke: none; pointer-events: all; }
.loop-bracket-guide { stroke: var(--q1asm-flow-color); stroke-width: 1.2; stroke-dasharray: 3 4; opacity: 0.5; pointer-events: none; }
.loop-bracket-stem, .loop-bracket-cap { stroke: var(--q1asm-flow-color); stroke-width: 2.2; stroke-linecap: square; pointer-events: none; }
.loop-bracket text { fill: var(--q1asm-flow-color); font-size: 11px; font-weight: 700; paint-order: normal; stroke: none; stroke-width: 0; pointer-events: none; }
.loop-bracket.is-selected .loop-bracket-stem,
.loop-bracket.is-selected .loop-bracket-cap,
.loop-bracket.is-related .loop-bracket-stem,
.loop-bracket.is-related .loop-bracket-cap { stroke: #2563eb; stroke-width: 3; }
.loop-bracket.is-selected text,
.loop-bracket.is-related text { fill: #1d4ed8; }
.loop-bracket.is-filtered { display: none; }
.loop-bracket.has-diagnostic .loop-bracket-guide { stroke: #d97706; opacity: 0.85; }
.loop-bracket.has-diagnostic .loop-bracket-stem,
.loop-bracket.has-diagnostic .loop-bracket-cap { stroke: #d97706; stroke-width: 3; }
.loop-bracket.diagnostic-error .loop-bracket-guide,
.loop-bracket.diagnostic-error .loop-bracket-stem,
.loop-bracket.diagnostic-error .loop-bracket-cap { stroke: #dc2626; }
.loop-bracket.diagnostic-info .loop-bracket-guide,
.loop-bracket.diagnostic-info .loop-bracket-stem,
.loop-bracket.diagnostic-info .loop-bracket-cap,
.loop-bracket.diagnostic-hint .loop-bracket-guide,
.loop-bracket.diagnostic-hint .loop-bracket-stem,
.loop-bracket.diagnostic-hint .loop-bracket-cap { stroke: #2563eb; }
.loop-bracket.is-selected.has-diagnostic .loop-bracket-stem,
.loop-bracket.is-selected.has-diagnostic .loop-bracket-cap,
.loop-bracket.is-related.has-diagnostic .loop-bracket-stem,
.loop-bracket.is-related.has-diagnostic .loop-bracket-cap { stroke: #2563eb; }
.branch-marker { cursor: pointer; }
.branch-marker-hitbox { fill: none; stroke: none; pointer-events: all; }
.branch-marker-guide { stroke: var(--q1asm-flow-color); stroke-width: 1.1; stroke-dasharray: 2 5; opacity: 0.45; pointer-events: none; }
.branch-marker-diamond { fill: var(--q1asm-event-fill, #fde68a); stroke: var(--q1asm-flow-color); stroke-width: 1.8; pointer-events: none; }
.branch-marker.confidence-symbolic .branch-marker-diamond { stroke-dasharray: 5 3; }
.branch-marker.confidence-assumed .branch-marker-diamond { stroke-dasharray: 4 3; }
.branch-marker.confidence-unknown .branch-marker-diamond { stroke-dasharray: 2 2; }
.branch-marker.confidence-runtime_dependent .branch-marker-diamond { stroke-dasharray: 6 2; }
.branch-marker text { fill: var(--q1asm-flow-color); font-size: 11px; font-weight: 700; paint-order: normal; stroke: none; stroke-width: 0; pointer-events: none; }
.branch-marker-path-icon { fill: none; stroke: var(--q1asm-flow-color); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; pointer-events: none; }
.branch-marker-path-icon-taken { stroke: #6d28d9; }
.branch-marker-path-icon-fallthrough { stroke: #475569; }
.branch-marker-path-icon-both { stroke: #0369a1; }
.branch-marker-path-icon-collapsed { stroke: #991b1b; }
.branch-marker-condition { display: none; }
.branch-marker.is-selected .branch-marker-diamond,
.branch-marker.is-related .branch-marker-diamond { stroke: #2563eb; stroke-width: 2.6; }
.branch-marker.is-selected .branch-marker-guide,
.branch-marker.is-related .branch-marker-guide { stroke: #2563eb; opacity: 0.85; }
.branch-marker.is-selected text,
.branch-marker.is-related text { fill: #1d4ed8; }
.branch-marker.is-selected .branch-marker-path-icon,
.branch-marker.is-related .branch-marker-path-icon { stroke: #1d4ed8; }
.branch-marker.is-filtered { display: none; }
.event.diagnostic-error { --q1asm-event-fill: #f6d5d4; --q1asm-event-label-color: #662126; }
body:is(.vscode-dark, .vscode-high-contrast) .event.diagnostic-error {
  --q1asm-event-fill: #572b32;
  --q1asm-event-label-color: #ffe8e7;
}
.timeline-error-mark,
.event text.timeline-error-mark { fill: var(--q1timeline-error-color); font-size: 13px; font-weight: 700; }
.timing-diagnostic text.timeline-error-mark { pointer-events: all; }
body.mode-debug .q1-error-marker { display: none; }
.control-flow-connector { fill: none; stroke: var(--q1asm-flow-color); stroke-width: 2; opacity: 0.78; pointer-events: none; }
.control-flow-connector.is-related { stroke: #2563eb; stroke-width: 3; opacity: 1; }
#control-flow-arrow path { fill: var(--q1asm-flow-color); }
.event-inspector { margin: 0 16px 16px; padding: 10px 12px; border: 1px solid var(--vscode-panel-border, #d8dee6); }
.event-inspector dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 10px; margin: 0; }
.event-inspector dt { color: var(--vscode-descriptionForeground, #374151); font-weight: 700; }
.event-inspector dd { margin: 0; overflow-wrap: anywhere; }
.event-inspector button { margin-top: 8px; }
.event.confidence-symbolic > rect { fill: var(--q1asm-event-fill, #f7d774); stroke-dasharray: 5 3; }
.event.confidence-assumed > rect { fill: var(--q1asm-event-fill, #b8c2cc); stroke-dasharray: 4 3; }
.event.confidence-unknown > rect { fill: var(--q1asm-event-fill, #c9ced6); stroke-dasharray: 2 2; }
.event.confidence-runtime_dependent > rect { fill: var(--q1asm-event-fill, #e5a0a0); stroke-dasharray: 6 2; }
.event.rt-time-nominal > rect { stroke-dasharray: 4 3; }
.kind-loop_block rect, .kind-control_flow_loop rect { stroke-width: 2; }
.kind-control_flow_branch rect { stroke-width: 2; stroke-dasharray: 7 4; }
.branch-path-taken rect { stroke-width: 2; }
.branch-path-fallthrough rect { stroke-width: 2; stroke-dasharray: 5 3; }
.kind-loop_iteration_preview rect { stroke-dasharray: 4 2; }
.kind-unknown_region rect { fill: #aab2bd; stroke-dasharray: 2 2; }
.status-definite_underflow rect { fill: #c72c2c; }
.status-possible_underflow rect { fill: #f08c3a; stroke-dasharray: 6 2; }
.status-analysis_incomplete rect { fill: #8a8f98; stroke-dasharray: 5 3; }
.status-not_detected_under_current_assumptions rect { fill: #9bb7a5; }
.time-cursor { stroke: #dc2626; stroke-width: 1; pointer-events: none; }
.feedback-flow { fill: none; stroke: var(--q1asm-feedback-color); stroke-width: 1.8; stroke-dasharray: 5 3; pointer-events: stroke; }
#feedback-arrow path { fill: var(--q1asm-feedback-color); }
.feedback-flow-label { fill: var(--vscode-foreground, #17202a); font-size: 10px; font-weight: 700; paint-order: normal; stroke: none; stroke-width: 0; }
.feedback-flow-group.has-diagnostic .feedback-flow { stroke: #d97706; stroke-width: 2.7; stroke-dasharray: 7 3; opacity: 0.98; }
.feedback-flow-group.diagnostic-error .feedback-flow { stroke: #dc2626; stroke-width: 3; }
.feedback-flow-group.has-diagnostic .feedback-flow-label { fill: #a45516; font-weight: 800; }
.feedback-flow-group.diagnostic-error .feedback-flow-label { fill: #b91c1c; }
body.vscode-dark .lane-rule, body.vscode-dark .grid { stroke: #353941; }
body.vscode-dark main { background: #1e1e1e !important; background-color: #1e1e1e !important; }
body.vscode-dark .event.diff-shifted rect { stroke: #d28be2; }
body.vscode-dark .event.confidence-assumed > rect { fill: var(--q1asm-event-fill, #aab2bd); }
body.vscode-dark .event.confidence-unknown > rect { fill: var(--q1asm-event-fill, #aab2bd); }
body.vscode-dark .kind-unknown_region rect { fill: #aab2bd; }
body.mode-normal .debug-lane { display: none; }
body.mode-debug .debug-lane { display: inline; }
[data-lane-role="q1-issue"] { display: none; }
.q1-issue-lane { display: none; }
[data-lane-role="q1-issue"].q1-issue-expanded { display: inline; }
.q1-issue-lane.q1-issue-expanded { display: inline; }
body.mode-debug [data-lane-role="q1-issue"] { display: inline; }
body.mode-normal .event.q1-dense-collapsed { display: none; }
body.mode-normal .event.loop-collapsed { display: none; }
body.mode-normal .event.normal-feedback-collapsed { display: none; }
.event.branch-collapsed { display: none; }
.event.q1-branch-collapsed { display: none; }
"""


def _js() -> str:
    return """
const modeButtons = document.querySelectorAll('button[data-mode]');
const timelineRoot = document.getElementById('timeline-root');
const timelineIrNode = document.getElementById('timeline-ir');
let timelineIr = { events: [] };
try {
  timelineIr = JSON.parse(timelineIrNode ? timelineIrNode.textContent : '{}');
} catch (error) {
  timelineIr = { events: [] };
}
const eventsById = new Map((timelineIr.events || []).map((event) => [String(event.id), event]));
const setterApplicationLinks = buildSetterApplicationLinks();
const timelineEventSelector = '.event, .loop-bracket, .branch-marker';
const selectedTimelineEventSelector = '.event.is-selected, .loop-bracket.is-selected, .branch-marker.is-selected';
const relatedTimelineEventSelector = '.event.is-related, .loop-bracket.is-related, .branch-marker.is-related';
const eventInspector = document.getElementById('event-inspector');
const inspectorFields = document.getElementById('event-inspector-fields');
const boundTimelineEvents = new WeakSet();
let selectedTimelineEvent;
function buildSetterApplicationLinks() {
  const links = new Map();
  const committedScopes = new Set();
  const firstApplications = new Map();
  function link(issueId, applicationId) {
    for (const [source, target] of [[issueId, applicationId], [applicationId, issueId]]) {
      if (!links.has(source)) links.set(source, new Set());
      links.get(source).add(target);
    }
  }
  function sharesPath(source, target) {
    return !source.branch_comparison_path ||
      (source.branch_comparison_path === target.branch_comparison_path &&
       source.branch_comparison_branch_id === target.branch_comparison_branch_id);
  }
  for (const [applicationId, event] of eventsById) {
    const meta = event.meta || {};
    if (!/^(?:branch_compare[.](?:taken|fallthrough)[.])*rt[.]/.test(event.lane) ||
        event.kind === 'conditional_skip' || meta.conditional_skipped) continue;
    const hasCommitEvidence = Object.hasOwn(meta, 'committed_setter_event_ids');
    const setters = hasCommitEvidence ? meta.committed_setter_event_ids : meta.applied_setter_event_ids;
    for (const [field, setterId] of Object.entries(setters || {})) {
      if (!hasCommitEvidence && !Object.hasOwn(meta.applied_state || {}, field)) continue;
      const setter = eventsById.get(String(setterId));
      if (setter?.kind !== 'latched_state_pending' || setter.meta?.field !== field ||
          setter.sequencer_id !== event.sequencer_id || !sharesPath(setter.meta, meta)) continue;
      const issueId = String(setter.meta.q1_issue_event_id);
      const issue = eventsById.get(issueId);
      if (issue?.kind !== 'q1_issue' || issue.sequencer_id !== event.sequencer_id || !sharesPath(issue.meta || {}, meta)) continue;
      const scope = JSON.stringify([setterId, field, meta.branch_comparison_branch_id,
        meta.branch_comparison_path, meta.diff_status === 'removed']);
      if (hasCommitEvidence) {
        committedScopes.add(scope);
        link(issueId, applicationId);
        continue;
      }
      // Legacy snapshots can retain a setter after the first observed application in a path.
      const start = event.t0?.kind === 'concrete' ? event.t0.value : undefined;
      const packet = JSON.stringify([meta.rt_packet_id ?? meta.q1_issue_event_id ?? applicationId,
        meta.loop_preview, meta.loop_iteration_index, start]);
      const previous = firstApplications.get(scope);
      if (!previous || (Number.isFinite(start) && Number.isFinite(previous.start) && start < previous.start)) {
        firstApplications.set(scope, {issueId, start, packet, applications: []});
      }
      const first = firstApplications.get(scope);
      if (first.packet === packet) first.applications.push(applicationId);
    }
  }
  for (const [scope, {issueId, applications}] of firstApplications) {
    if (!committedScopes.has(scope)) for (const applicationId of applications) link(issueId, applicationId);
  }
  return links;
}
function querySelectorAllWithFallback(selector) {
  const nodes = Array.from(document.querySelectorAll(selector));
  if (nodes.length || !selector.includes(',')) {
    return nodes;
  }
  const result = [];
  const seen = new Set();
  for (const part of selector.split(',')) {
    for (const node of document.querySelectorAll(part.trim())) {
      if (!seen.has(node)) {
        seen.add(node);
        result.push(node);
      }
    }
  }
  return result;
}
function timelineEventNodes() {
  return querySelectorAllWithFallback(timelineEventSelector);
}
function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}
function displayInspectorValue(value) {
  if (value === undefined || value === null) {
    return '';
  }
  if (typeof value === 'object') {
    return JSON.stringify(value);
  }
  return String(value);
}
function addInspectorField(fields, label, value, className) {
  const displayed = displayInspectorValue(value);
  if (!displayed) {
    return;
  }
  const attributes = className ? ` class="${className}"` : '';
  fields.push(`<dt>${escapeHtml(label)}</dt><dd${attributes}>${escapeHtml(displayed)}</dd>`);
}
function displayResolvedValue(value) {
  if (value === undefined || value === null) {
    return '';
  }
  if (typeof value === 'object' && Object.prototype.hasOwnProperty.call(value, 'display')) {
    return displayInspectorValue(value.display);
  }
  return displayInspectorValue(value);
}
function displayDurationProvenance(provenance) {
  if (!provenance || typeof provenance !== 'object') {
    return '';
  }
  const expression = provenance.expression ? String(provenance.expression) : '';
  const value = provenance.value ? displayResolvedValue(provenance.value) : '';
  const detail = expression && value ? `${expression} = ${value} ns` : expression || value;
  const symbol = provenance.symbol || provenance.register || '';
  return symbol && detail ? `${symbol}: ${detail}` : detail;
}
function formatBranchAssumption(meta) {
  if (!meta) {
    return undefined;
  }
  const path = meta.assumed_branch_path;
  if (meta.rt_condition_control) {
    if (path === 'both') return 'Execute / Skip';
    if (path === 'taken') return 'Execute';
    if (path === 'fallthrough') return 'Skip';
  }
  if (!path) {
    return meta.branch_taken;
  }
  if (path === 'collapsed' || path === 'both') {
    return path;
  }
  if (meta.assumed_branch_taken === true) {
    return 'true (jump target)';
  }
  if (meta.assumed_branch_taken === false) {
    return 'false (continue)';
  }
  if (path === 'taken') {
    return 'true (jump target)';
  }
  if (path === 'fallthrough') {
    return 'false (continue)';
  }
  return path;
}
function loopPreviewVisibleIterations(meta) {
  const shownIterations = Array.isArray(meta.shown_iterations) ? meta.shown_iterations : [];
  if (shownIterations.length > 0) {
    return shownIterations.length;
  }
  const visibleIterationCount = Number(meta.visible_iteration_count);
  if (Number.isInteger(visibleIterationCount) && visibleIterationCount > 0) {
    return visibleIterationCount;
  }
  return 1;
}
function loopPreviewCap(meta) {
  const cap = Number(meta.loop_preview_cap);
  return Number.isInteger(cap) && cap > 0 ? cap : 10;
}
function loopPreviewTotalIterations(meta) {
  const rawCount = meta.count;
  if (typeof rawCount === 'number' && Number.isInteger(rawCount) && rawCount > 0) {
    return rawCount;
  }
  if (rawCount && typeof rawCount === 'object') {
    const value = rawCount.value ?? rawCount.display;
    const parsed = Number(value);
    if (Number.isInteger(parsed) && parsed > 0) {
      return parsed;
    }
  }
  const parsed = Number(rawCount);
  if (Number.isInteger(parsed) && parsed > 0) {
    return parsed;
  }
  return undefined;
}
function formatLoopPreviewProgress(meta) {
  const visible = loopPreviewVisibleIterations(meta);
  const total = loopPreviewTotalIterations(meta);
  if (total !== undefined) {
    return `${visible}/${total}`;
  }
  return `${visible}/${loopPreviewCap(meta)} cap`;
}
function inspectorTimeEdge(event, edge, timeBasis) {
  const aligned = event.meta?.[`aligned_${edge}`];
  if (timeBasis === 'aligned' && Number.isFinite(aligned)) return aligned;
  const local = event[edge];
  return local?.kind === 'concrete' ? local.value : local?.display;
}
function inspectorMetadataTime(event, field, timeBasis) {
  const meta = event.meta || {};
  const value = meta[field];
  if (!Number.isFinite(value)) return undefined;
  let offset = 0;
  if (timeBasis === 'aligned') {
    for (const edge of ['t0', 't1']) {
      const aligned = meta[`aligned_${edge}`];
      if (!Number.isFinite(aligned)) continue;
      const local = Number.isFinite(meta[`local_${edge}`]) ? meta[`local_${edge}`] : inspectorTimeEdge(event, edge, 'local');
      offset = Number.isFinite(local) ? aligned - local : NaN;
      if (Number.isFinite(offset)) break;
    }
  }
  const projected = value + offset;
  return Number.isFinite(projected) ? projected : undefined;
}
function inspectorRtTimeIsNominal(meta, timeBasis) {
  return Boolean(meta?.rt_trigger_epoch || timeBasis === 'aligned' && meta?.aligned_time_nominal);
}
function inspectorTimeRange(event, timeBasis) {
  const hasModeTemplate = timeBasis && timelineRoot?.querySelector('template[data-timeline-mode]');
  if ((event.kind === 'underflow_warning' && event.meta?.diagnostic_message && !event.meta?.comparison_terminal) ||
      (event.kind === 'feedback_rejected' && !event.meta?.comparison_terminal)) {
    const time = inspectorTimeEdge(event, 't0', timeBasis);
    return Number.isFinite(time) ? `${time} ns` : time || 'unknown';
  }
  if (event.kind === 'q1_issue') {
    const cutoff = inspectorMetadataTime(event, 'observation_cutoff_ns', timeBasis);
    const planned = inspectorMetadataTime(event, 'planned_q1_end_ns', timeBasis);
    const start = inspectorTimeEdge(event, 't0', timeBasis), end = inspectorTimeEdge(event, 't1', timeBasis);
    if ([start, cutoff, planned].every(Number.isFinite) && start <= cutoff && cutoff === end && end < planned) {
      return `Issue ${start} ns · Observation cutoff ${cutoff} ns · Planned end ${planned} ns`;
    }
  }
  const nominal = inspectorRtTimeIsNominal(event.meta, timeBasis);
  const unresolved = event.meta?.trigger_arrival_unknown;
  const pending = event.meta?.pending === true && (event.kind === 'trigger_wait' || event.kind === 'sync_wait');
  const displayEdge = (edge) => {
    const time = inspectorTimeEdge(event, edge, timeBasis);
    if (!hasModeTemplate && !nominal && !unresolved && !pending) {
      return displayInspectorValue(timeBasis === 'aligned' && Number.isFinite(time) ? time : event[edge]);
    }
    return Number.isFinite(time) ? `${time} ns` : time || 'unknown';
  };
  const start = displayEdge('t0'), end = displayEdge('t1');
  if (pending) {
    const target = event.kind === 'trigger_wait' ? 'trigger' : 'synchronization';
    const cutoff = Number.isFinite(inspectorTimeEdge(event, 't1', timeBasis)) ? ` · observation cutoff ${end}` : '';
    return `Arm ${start} → waiting for ${target} (completion unresolved)${cutoff}`;
  }
  if (unresolved) {
    return `${nominal ? 'Nominal arm' : 'Arm'} ${start} → waiting for trigger (completion unresolved)`;
  }
  if (nominal) return `Nominal RT ${start} → ${end} (trigger arrival unresolved)`;
  const meta = event.meta || {};
  const startTime = inspectorTimeEdge(event, 't0', timeBasis), endTime = inspectorTimeEdge(event, 't1', timeBasis);
  const hasCutoff = Object.hasOwn(meta, 'observation_cutoff_ns');
  const cutoff = hasCutoff ? inspectorMetadataTime(event, 'observation_cutoff_ns', timeBasis) : undefined;
  const planned = !meta.pending && !meta.interrupted_by_fault && /^(?:branch_compare[.](?:taken|fallthrough)[.])*rt[.]/.test(event.lane)
    ? inspectorMetadataTime(event, 'planned_rt_end_ns', timeBasis) : undefined;
  const plannedEnd = [startTime, cutoff, planned].every(Number.isFinite) && startTime <= cutoff && cutoff === endTime && endTime < planned
    ? ` · Planned end ${planned} ns` : '';
  if ((event.kind === 'wait_trigger' || event.kind === 'wait_sync') &&
      !meta.pending && !meta.conditional_skipped && !meta.interrupted_by_fault && !meta.playback && meta.trigger_assumption == null) {
    const phase = inspectorMetadataTime(event, 'rt_execution_ns', timeBasis);
    if ([startTime, phase, endTime].every(Number.isFinite) && startTime <= phase && phase <= endTime && (!hasCutoff || cutoff === endTime)) {
      const label = event.kind === 'wait_trigger' ? 'Trigger arrival' : 'Barrier release';
      const endLabel = hasCutoff ? 'Observation cutoff' : 'Complete';
      return `Arm ${startTime} ns · ${label} ${phase} ns · ${endLabel} ${endTime} ns${plannedEnd}`;
    }
  }
  if (plannedEnd) return `Start ${startTime} ns · Observation cutoff ${cutoff} ns${plannedEnd}`;
  if (!hasModeTemplate) return `${start} -> ${end}`;
  return start === end ? start : `${start} → ${end}`;
}
function matchesComparison(evidence, owner) {
  const { branch_comparison_branch_id: branch, branch_comparison_path: path } = evidence.meta || {};
  return (branch == null && path == null) ||
    (branch === owner.meta?.branch_comparison_branch_id && path === owner.meta?.branch_comparison_path);
}
function appendRecordedConditionFields(fields, issue, timeBasis) {
  const meta = issue.meta || {};
  if (!timelineIr.simulation?.[issue.sequencer_id] || issue.kind !== 'q1_issue' || meta.op !== 'set_cond' ||
      !meta.branch_id || meta.branch_comparison_path || meta.diff_status === 'removed') return;
  if (meta.assumed_branch_path === 'taken' || meta.assumed_branch_path === 'fallthrough') {
    addInspectorField(fields, 'Recorded RT decisions', 'Unavailable under a forced assumption');
    return;
  }
  const packets = new Set();
  const buttons = [];
  for (const event of eventsById.values()) {
    const rt = event.meta || {};
    const armed = rt.pending === true && rt.rt_condition_decision === true &&
      (event.kind === 'trigger_wait' || event.kind === 'sync_wait');
    if (event.sequencer_id !== issue.sequencer_id || !event.lane.startsWith('rt.') ||
        rt.rt_condition_issue_event_id !== issue.id || !rt.rt_packet_id || packets.has(rt.rt_packet_id) ||
        (!armed && !Number.isFinite(rt.rt_execution_ns)) || rt.branch_comparison_path || rt.diff_status === 'removed') continue;
    const time = inspectorTimeEdge(event, 't0', timeBasis);
    if (!Number.isFinite(time)) continue;
    packets.add(rt.rt_packet_id);
    const decision = rt.conditional_skipped === true || event.kind === 'conditional_skip' ? 'Skip' : 'Execute';
    buttons.push(`<button type="button" data-related-event-id="${escapeHtml(event.id)}">${decision} at ${time} ns</button>`);
  }
  fields.push(`<dt>Recorded RT decisions</dt><dd>${buttons.length ? buttons.join(' ') : 'None observed'}</dd>`);
}
function netzeroResultActions(command) {
  const meta = command.meta || {};
  if (typeof meta.rt_packet_id !== 'string' || !meta.rt_packet_id || meta.conditional_skipped) return '';
  const buttons = [];
  for (const result of eventsById.values()) {
    const evidence = result.meta || {};
    if (result.kind !== 'netzero_result' || result.sequencer_id !== command.sequencer_id ||
        evidence.source_event_id !== command.id || evidence.rt_packet_id !== meta.rt_packet_id ||
        !matchesComparison(command, result) || (evidence.diff_status === 'removed') !== (meta.diff_status === 'removed')) continue;
    const output = evidence.output == null ? 'result' : `output ${evidence.output}`;
    const paths = evidence.branch_comparison_kind === 'rt_condition'
      ? {taken: 'Execute', fallthrough: 'Skip'} : {taken: 'Taken', fallthrough: 'Fallthrough'};
    const path = !meta.branch_comparison_path && Object.hasOwn(paths, evidence.branch_comparison_path)
      ? ` (${paths[evidence.branch_comparison_path]})` : '';
    buttons.push(`<button type="button" data-related-event-id="${escapeHtml(result.id)}">Show NetZero ${escapeHtml(output + path)}</button>`);
  }
  return buttons.length ? `<dt>NetZero results</dt><dd>${buttons.join(' ')}</dd>` : '';
}
function terminalEvidenceActions(event) {
  const ids = event.meta.comparison_terminal.source_event_ids;
  const excluded = hiddenRtEventIds();
  const evidence = [...new Set(Array.isArray(ids) ? ids.map(String) : [])]
    .filter((id) => !excluded.has(id))
    .map((id) => eventsById.get(id))
    .filter((item) => item && item !== event && matchesComparison(item, event));
  if (!evidence.length) return '';
  const fallback = evidence.map((item) => item.id);
  const buttons = [`<button type="button" data-related-event-id="${escapeHtml(fallback[0])}" data-related-event-ids="${escapeHtml(JSON.stringify(fallback))}">Show evidence</button>`];
  const visible = new Set();
  for (const item of evidence) {
    const node = findEventNodeById(item.id);
    if (!isTimelineEventVisible(node) || visible.has(node)) continue;
    visible.add(node);
    if (visible.size === 1) continue;  // The primary button opens the first visible node.
    const cutoff = Number.isFinite(item.meta?.observation_cutoff_ns);
    const label = item.kind === 'q1_issue' ? (cutoff ? 'Q1 cutoff' : 'Q1 issue')
      : cutoff ? 'RT cutoff' : item.kind.replaceAll('_', ' ');
    buttons.push(`<button type="button" data-related-event-id="${escapeHtml(item.id)}" data-related-visible-only>Show ${escapeHtml(label)}</button>`);
  }
  return `<dt>Evidence</dt><dd>${buttons.join(' ')}</dd>`;
}
function rejectionSenderEvents(rejection) {
  const meta = rejection.meta || {};
  if (typeof meta.source_event_id !== 'string' || meta.input_event_id != null || meta.input_index != null) return [];
  const send = eventsById.get(meta.source_event_id);
  if (send?.kind !== 'feedback_send' || send.meta?.write_combine) return [];
  if (!matchesComparison(send, rejection)) return [];
  const evidence = [];
  const packetId = send.meta?.source_packet_id;
  if (typeof packetId === 'string' && packetId) {
    const commands = [...eventsById.values()].filter((event) => {
      const command = event.meta || {};
      return event.sequencer_id === send.sequencer_id && command.rt_packet_id === packetId &&
        /^(?:branch_compare[.](?:taken|fallthrough)[.])*rt[.]/.test(event.lane) &&
        event.kind !== 'conditional_skip' && !command.conditional_skipped &&
        (Number.isFinite(command.rt_execution_ns) || ['feedback_com', 'fb_com_data'].includes(event.kind)) &&
        matchesComparison(event, send) && isTimelineEventVisible(findEventNodeById(event.id));
    });
    if (commands.length === 1) evidence.push(commands[0].id);
  }
  if (isTimelineEventVisible(findEventNodeById(send.id))) evidence.push(send.id);
  return evidence;
}
function renderInspector(eventId, diagnosticMessage) {
  if (!inspectorFields) {
    return;
  }
  const event = eventsById.get(String(eventId));
  if (!event) {
    inspectorFields.innerHTML = '<dt>Selected event</dt><dd>None</dd>';
    renderLoopPreviewAction(null);
    clearRelatedControlFlow();
    return;
  }
  const meta = event.meta || {};
  const source = event.source || {};
  const fields = [];
  let label = event.label || event.kind;
  if (event.kind === 'underflow_warning') {
    label = meta.status === 'possible_underflow' ? 'Possible RT underflow' : 'RT underflow';
    if (meta.slack_ns < 0) label += ` · ${-meta.slack_ns} ns late`;
  }
  addInspectorField(fields, 'Label', label);
  const terminal = meta.comparison_terminal;
  if (terminal) {
    addInspectorField(fields, 'Execution status', terminal.status?.replaceAll('_', ' '));
    addInspectorField(fields, 'Reason', terminal.reason);
    fields.push(terminalEvidenceActions(event));
  }
  for (const message of diagnosticMessage?.split('\\n') || []) {
    addInspectorField(fields, 'Error', message, 'diagnostic-error-message');
  }
  addInspectorField(fields, 'Sequencer', event.sequencer_id);
  addInspectorField(fields, 'Lane', event.lane);
  const timeBasis = timelineRoot?.querySelector(':scope > svg')?.dataset.timeBasis;
  const timeRange = inspectorTimeRange(event, timeBasis);
  if (event.kind === 'loop_block' && !meta.observed && !meta.playback &&
      ['first_iteration_only', 'requested_iterations'].includes(meta.preview_policy)) {
    const edge = (key) => {
      const time = inspectorTimeEdge(event, key, timeBasis);
      return Number.isFinite(time) ? `${time} ns` : 'Unknown';
    };
    addInspectorField(fields, 'RT preview range', `${edge('t0')} → ${edge('t1')}`);
    const total = meta.total_duration?.kind === 'concrete' ? meta.total_duration.value : undefined;
    const period = meta.period?.kind === 'concrete' ? meta.period.value : undefined;
    const exactForever = event.confidence === 'exact' && meta.count === 'forever' && meta.period_invariant === true;
    let totalText = 'Unknown';
    if (Number.isFinite(total) && total >= 0) totalText = `${total} ns`;
    else if (exactForever && Number.isFinite(period) && period >= 0) totalText = period > 0 ? 'Unbounded' : '0 ns';
    addInspectorField(fields, 'Total RT duration', totalText);
    const slope = meta.period_affine?.slope;
    const durations = Array.isArray(meta.iteration_durations) ? meta.iteration_durations : [];
    const first = period ?? durations[0];
    const count = meta.count;
    const exactFirst = event.confidence === 'exact' && Number.isSafeInteger(count) && count > 0 &&
      Number.isSafeInteger(first) && first >= 0;
    const repeatedTotal = exactFirst ? first * count : undefined;
    const varying = (Number.isFinite(slope) && slope !== 0) || new Set(durations.filter(Number.isFinite)).size > 1 ||
      (count > 1 && Number.isSafeInteger(repeatedTotal) && Number.isSafeInteger(total) && total >= 0 && repeatedTotal !== total);
    const equalSamples = exactFirst && durations.length === count && durations.every((duration) => duration === first);
    const invariantPeriod = meta.period_invariant === true ? period : first;
    addInspectorField(fields, 'RT period', meta.period_invariant === true || equalSamples && !varying
      ? (Number.isFinite(invariantPeriod) && invariantPeriod >= 0 ? `Invariant · ${invariantPeriod} ns` : 'Invariant')
      : varying ? 'Varying' : 'Unknown');
  } else {
    addInspectorField(fields, 'Time range', timeRange);
  }
  if (event.kind === 'unknown_region' && meta.reason === 'unverified_instruction' &&
      Number.isSafeInteger(meta.planned_duration_ns) && meta.planned_duration_ns >= 0) {
    addInspectorField(fields, 'Planned duration', `${meta.planned_duration_ns} ns`);
  }
  if (event.kind === 'play_netzero') fields.push(netzeroResultActions(event));
  if (event.kind === 'netzero_result' && meta.status === 'incomplete') {
    const start = inspectorTimeEdge(event, 't0', timeBasis), end = inspectorTimeEdge(event, 't1', timeBasis);
    const cutoff = inspectorMetadataTime(event, 'observation_cutoff_ns', timeBasis);
    const planned = inspectorMetadataTime(event, 'planned_end_ns', timeBasis);
    if ([start, end, cutoff, planned].every(Number.isFinite) && start <= cutoff && cutoff === end && end <= planned) {
      addInspectorField(fields, 'Compensation timing', `Observation cutoff ${cutoff} ns · Planned end ${planned} ns`);
      if (Number.isFinite(meta.compensation_area)) {
        addInspectorField(fields, 'Observed compensation area', `${meta.compensation_area} normalized full scale × ns`);
      }
      if (Number.isFinite(meta.planned_compensation_area)) {
        addInspectorField(fields, 'Planned compensation area', `${meta.planned_compensation_area} normalized full scale × ns`);
      }
    }
  }
  if (event.kind !== 'q1_issue' && !meta.pending && meta.queue_duration_ns === 0) {
    const bound = inspectorMetadataTime(event, 'next_dequeue_not_before_ns', timeBasis);
    const label = inspectorRtTimeIsNominal(meta, timeBasis) ? 'Nominal earliest next dequeue' : 'Earliest next dequeue';
    if (Number.isFinite(bound)) addInspectorField(fields, label, `${bound} ns`);
  }
  if (event.kind === 'trigger_receive') addInspectorField(fields, 'Trigger address', meta.address);
  if (event.kind === 'feedback_rejected') {
    if (!terminal) {
      addInspectorField(fields, 'Input event', meta.input_event_id);
      addInspectorField(fields, 'LINQ ID', meta.id);
    }
    const sender = rejectionSenderEvents(event);
    if (sender.length) {
      fields.push(`<dt>Sender</dt><dd><button type="button" data-related-event-id="${escapeHtml(sender[0])}" data-related-event-ids="${escapeHtml(JSON.stringify(sender))}" data-related-visible-only>Show sender</button></dd>`);
    }
  }
  if (meta.trigger_arrival_unknown) {
    addInspectorField(fields, 'Wait after trigger (ns)', meta.trigger_wait_duration_ns);
  }
  const integration = meta.acquisition_window;
  if (event.kind === 'acquire' && integration) {
    const duration = integration.effective_duration_ns;
    addInspectorField(fields, 'Effective integration', !Number.isFinite(duration) ? 'Unknown integration length'
      : integration.source === 'instruction_duration_fallback' ? `${duration} ns (assumed from RT duration)`
      : `${duration} ns / ${integration.configured_duration_ns} ns configured`);
  }
  if (event.kind === 'acquire') {
    addInspectorField(fields, 'Integration source', ({
      __proto__: null,
      configured: 'Physical model · integration_length_ns',
      hardware_parameters: 'hardware parameters · integration_length_acq',
      weight_memory: 'Weight memory',
      instruction_duration_fallback: 'Assumed from RT duration',
    })[integration?.source] ?? 'unknown');
  }
  addInspectorField(fields, 'Source', source.file ? `${source.file.replace(/\\\\/g, '/').split('/').pop()}:${source.line || 1}` : 'unavailable');
  addInspectorField(fields, 'Confidence', event.confidence);
  addInspectorField(fields, 'Stop code', meta.stop_code);
  addInspectorField(fields, 'Fault flag', meta.fault?.flag);
  if (meta.fault) {
    addInspectorField(fields, 'Fault edge (ns)', inspectorTimeEdge(event, 't1', timeBasis) ?? meta.fault.q1_time_ns);
  }
  addInspectorField(fields, 'Q1 core', event.kind === 'q1_issue' ? timeRange : meta.q1_issue_event_id);
  addInspectorField(fields, 'RT packet', meta.rt_packet_id);
  addInspectorField(fields, 'Queue depth', meta.estimated_depth ?? meta.depth);
  addInspectorField(fields, 'Late by', event.kind === 'underflow_warning' && meta.slack_ns < 0 ? `${-meta.slack_ns} ns` : '');
  addInspectorField(fields, 'Branch condition', meta.condition || (meta.rt_condition_control ? String(source.raw ?? '').trim() : undefined));
  if (meta.branch_id) addInspectorField(fields, 'Recorded branch path', recordedBranchPath(meta));
  appendRecordedConditionFields(fields, event, timeBasis);
  addInspectorField(fields, 'Branch decision', meta.branch_decision);
  addInspectorField(fields, 'Runtime dependency', meta.runtime_dependency);
  addInspectorField(fields, 'Branch policy', meta.branch_policy);
  addInspectorField(fields, 'Branch assumption', formatBranchAssumption(meta));
  addInspectorField(fields, 'Loop context', meta.loop_context || meta.loop_id);
  addInspectorField(fields, 'Loop iterations', event.kind === 'loop_block' ? formatLoopPreviewProgress(meta) : '');
  addInspectorField(fields, 'Latched state', meta.field ? `${meta.field}=${displayInspectorValue(meta.value)}` : meta.applied_state);
  addInspectorField(fields, 'Duration role', meta.duration_provenance && meta.duration_provenance.role);
  addInspectorField(fields, 'Duration expression', displayDurationProvenance(meta.duration_provenance));
  inspectorFields.innerHTML = fields.join('');
  renderLoopPreviewAction(event);
}
function findEventNodeById(eventId) {
  const matches = [];
  for (const node of document.querySelectorAll('[data-event-id]')) {
    if (node.dataset.eventId === String(eventId) || (node.dataset.eventIds || '').split(' ').includes(String(eventId))) {
      matches.push(node);
    }
  }
  return matches.find((node) => isTimelineEventVisible(node)) || matches[0];
}
function renderLoopPreviewAction(event) {
  const existing = document.getElementById('q1timeline-loop-preview-actions');
  if (existing) {
    existing.remove();
  }
  if (!eventInspector || !event || event.kind !== 'loop_block') {
    return;
  }
  const meta = event.meta || {};
  const previewEventIds = Array.isArray(meta.first_iteration_event_ids) ? meta.first_iteration_event_ids : [];
  const previewEventId = previewEventIds.find((id) => isTimelineEventVisible(findEventNodeById(id)));
  if (!previewEventId) {
    return;
  }
  const group = document.createElement('div');
  group.id = 'q1timeline-loop-preview-actions';
  group.className = 'loop-preview-actions';
  const button = document.createElement('button');
  button.id = 'q1timeline-open-loop-preview';
  button.type = 'button';
  button.textContent = 'Open first iteration';
  button.addEventListener('click', () => {
    const previewNode = findEventNodeById(previewEventId);
    if (!isTimelineEventVisible(previewNode)) return;
    selectEventNode(previewNode, previewEventId);
    previewNode.scrollIntoView({ block: 'center', inline: 'center' });
  });
  group.append(button);
  eventInspector.append(group);
}
function recordedBranchPath(meta) {
  const [taken, fallthrough] = meta.rt_condition_control ? ['Execute', 'Skip'] : ['Jump', 'Continue'];
  if (typeof meta.branch_taken === 'boolean' && (meta.branch_decision === 'concrete' || !meta.assumed_branch_path)) {
    return meta.branch_taken ? taken : fallthrough;
  }
  if (meta.assumed_branch_path === 'taken') return taken;
  if (meta.assumed_branch_path === 'fallthrough') return fallthrough;
  if (meta.assumed_branch_path === 'both') {
    return meta.comparison_paths?.length === 2 ? `${taken} and ${fallthrough}` : 'Comparison unavailable';
  }
  return meta.rt_condition_control ? 'Recorded trigger input' : 'Unresolved';
}
function clearRelatedControlFlow() {
  querySelectorAllWithFallback(relatedTimelineEventSelector).forEach((node) => node.classList.remove('is-related'));
  document.querySelectorAll('.control-flow-connector.is-related').forEach((node) => node.classList.remove('is-related'));
}
function highlightRelatedControlFlowConnectors(eventNode) {
  if (!eventNode) {
    return;
  }
  const eventId = eventNode.dataset.eventId;
  const sourceEventId = eventNode.dataset.controlFlowSourceEventId;
  const loopId = eventNode.dataset.loopId;
  for (const connector of document.querySelectorAll('.control-flow-connector')) {
    if (
      (eventId && connector.dataset.controlFlowSourceEventId === eventId) ||
      (sourceEventId && connector.dataset.controlFlowSourceEventId === sourceEventId) ||
      (loopId && connector.dataset.loopId === loopId)
    ) {
      connector.classList.add('is-related');
    }
  }
}
function highlightRelatedControlFlow(eventNode) {
  clearRelatedControlFlow();
  if (!eventNode) {
    return;
  }
  const eventId = eventNode.dataset.eventId;
  const sourceEventId = eventNode.dataset.controlFlowSourceEventId;
  const waitIssueId = eventNode.dataset.waitQ1IssueEventId;
  const loopId = eventNode.dataset.loopId;
  const setterLinks = setterApplicationLinks.get(eventId);
  // Comparison membership alone does not make a setter or commit a branch control.
  const branchId = setterLinks ? eventsById.get(eventId)?.meta?.branch_id : eventNode.dataset.branchId;
  const setterRelatedIds = document.body.dataset.mode === 'debug' ? setterLinks : undefined;
  for (const node of timelineEventNodes()) {
    if (
      (eventId && node.dataset.controlFlowSourceEventId === eventId) ||
      (sourceEventId && node.dataset.eventId === sourceEventId) ||
      (eventId && node.dataset.waitQ1IssueEventId === eventId) ||
      (waitIssueId && node.dataset.eventId === waitIssueId) ||
      (setterRelatedIds?.has(node.dataset.eventId) && isTimelineEventVisible(node)) ||
      (branchId && node.dataset.branchId === branchId) ||
      (loopId && node.dataset.loopId === loopId) ||
      (loopId && node.dataset.loopPreviewId === loopId)
    ) {
      node.classList.add('is-related');
    }
  }
  highlightRelatedControlFlowConnectors(eventNode);
}
function selectEventNode(eventNode, linkedEventId, announce = true) {
  if (eventNode && !isTimelineEventVisible(eventNode)) return;
  querySelectorAllWithFallback(selectedTimelineEventSelector).forEach((node) => node.classList.remove('is-selected'));
  if (eventNode) {
    eventNode.classList.add('is-selected');
    highlightRelatedControlFlow(eventNode);
  } else {
    clearRelatedControlFlow();
  }
  const nodeId = eventNode?.dataset.eventId;
  const eventId = linkedEventId || nodeId;
  if (!eventId) {
    selectedTimelineEvent = undefined;
    renderInspector(null);
    return;
  }
  selectedTimelineEvent = { nodeId, eventId };
  renderInspector(eventId, eventNode?.dataset.diagnosticMessage);
  const details = document.getElementById('q1timeline-analysis-details');
  if (details) details.open = true;
  if (announce) window.dispatchEvent(new CustomEvent('q1timeline:eventClick', { detail: { eventId } }));
}
function switchTimelineGeometry(mode) {
  const previousMode = document.body.dataset.mode;
  if (!timelineRoot || previousMode === mode) return false;
  const previous = timelineRoot.querySelector(`template[data-timeline-mode="${previousMode}"]`);
  const next = timelineRoot.querySelector(`template[data-timeline-mode="${mode}"]`);
  const activeSvg = timelineRoot.querySelector(':scope > svg');
  const nextSvg = next?.content.firstElementChild;
  if (!previous || !activeSvg || !nextSvg) return false;
  hideTimeCursor();
  previous.content.append(activeSvg);
  timelineRoot.prepend(nextSvg);
  return true;
}
function refreshInspectorNavigation() {
  const { nodeId, eventId } = selectedTimelineEvent || {};
  const event = eventsById.get(eventId);
  if (event?.meta?.comparison_terminal) {
    renderInspector(eventId, findEventNodeById(nodeId)?.dataset.diagnosticMessage);
  } else if (event?.kind === 'loop_block') {
    renderLoopPreviewAction(event);
  }
}
function hiddenRtEventIds() {
  const active = timelineRoot?.querySelector(':scope > svg');
  return new Set(JSON.parse(active?.dataset.hiddenRtEventIds || '[]'));
}
function refreshRelatedEventLinks() {
  const excluded = hiddenRtEventIds();
  const visible = new Map();
  function isVisible(id) {
    if (!visible.has(id)) visible.set(id, isTimelineEventVisible(findEventNodeById(id)));
    return visible.get(id);
  }
  for (const link of document.querySelectorAll('a[data-related-event-id]')) {
    if (link.hasAttribute('data-tb-evidence')) {
      link.hidden = !isVisible(link.dataset.relatedEventId);
    } else if (excluded.size || link.hidden) {
      const candidates = JSON.parse(link.dataset.relatedEventIds || '[]');
      link.hidden = candidates.some((id) => excluded.has(id)) && !candidates.some(isVisible);
    }
  }
}
function isNormalPendingEvidence(eventId) {
  return document.body.dataset.mode === 'normal' && timelineRoot?.querySelector('template[data-timeline-mode]') &&
    eventsById.get(String(eventId))?.kind === 'latched_state_pending';
}
function setMode(mode) {
  const switched = switchTimelineGeometry(mode);
  document.body.classList.toggle('mode-debug', mode === 'debug');
  document.body.classList.toggle('mode-normal', mode === 'normal');
  document.body.dataset.mode = mode;
  for (const lane of document.querySelectorAll('[data-lane-role="q1-issue"]')) {
    lane.hidden = mode !== 'debug';
    lane.style.display = mode === 'debug' ? 'inline' : 'none';
  }
  for (const button of modeButtons) {
    button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
  }
  if (!switched) {
    if (setterApplicationLinks.has(selectedTimelineEvent?.nodeId)) {
      highlightRelatedControlFlow(findEventNodeById(selectedTimelineEvent.nodeId));
    }
    refreshInspectorNavigation();
    refreshRelatedEventLinks();
    return;
  }
  bindTimelineEvents();
  applyEventFilter();
  if (selectedTimelineEvent) {
    const { nodeId, eventId } = selectedTimelineEvent;
    const selected = findEventNodeById(nodeId) || findEventNodeById(eventId);
    if (isTimelineEventVisible(selected) || !selected && isNormalPendingEvidence(eventId)) {
      selectEventNode(selected, eventId, false);
    } else {
      querySelectorAllWithFallback(selectedTimelineEventSelector).forEach((node) => node.classList.remove('is-selected'));
      clearRelatedControlFlow();
      renderInspector(null);
    }
  }
}
function isTimelineEventVisible(eventNode, { allowCollapsedFeedback = false } = {}) {
  if (!eventNode) {
    return false;
  }
  if (document.body.dataset.mode === 'normal' && eventNode.closest('.debug-lane, [data-lane-role="q1-issue"]')) {
    return false;
  }
  if (eventNode.classList.contains('q1-error-marker') && document.body.dataset.mode === 'debug') {
    return false;
  }
  if (eventNode.classList.contains('q1-dense-collapsed') && document.body.dataset.mode === 'normal') {
    return false;
  }
  if (eventNode.classList.contains('loop-collapsed') && document.body.dataset.mode === 'normal') {
    return false;
  }
  if (!allowCollapsedFeedback && eventNode.classList.contains('normal-feedback-collapsed') && document.body.dataset.mode === 'normal') {
    return false;
  }
  if (eventNode.classList.contains('branch-collapsed')) {
    return false;
  }
  if (eventNode.classList.contains('q1-branch-collapsed')) {
    return false;
  }
  return !eventNode.classList.contains('is-filtered');
}
function moveTimeCursor(eventNode) {
  const timeCursor = document.getElementById('time-cursor');
  if (!timeCursor || !eventNode || !eventNode.dataset.t0X) {
    return;
  }
  if (!isTimelineEventVisible(eventNode)) {
    return;
  }
  timeCursor.setAttribute('x1', eventNode.dataset.t0X);
  timeCursor.setAttribute('x2', eventNode.dataset.t0X);
  timeCursor.removeAttribute('hidden');
}
function hideTimeCursor() {
  const timeCursor = document.getElementById('time-cursor');
  if (timeCursor) {
    timeCursor.setAttribute('hidden', '');
  }
}
for (const button of modeButtons) {
  button.addEventListener('click', () => {
    setMode(button.dataset.mode);
  });
}
const filterInput = document.getElementById('event-filter');
function applyEventFilter() {
  const query = filterInput?.value.trim().toLowerCase() || '';
  for (const eventNode of timelineEventNodes()) {
    const searchable = eventNode.dataset.search || '';
    eventNode.classList.toggle('is-filtered', query !== '' && !searchable.includes(query));
  }
  refreshRelatedEventLinks();
}
filterInput?.addEventListener('input', () => {
  applyEventFilter();
  refreshInspectorNavigation();
});
function bindTimelineEvents() {
  for (const eventNode of timelineEventNodes()) {
    if (boundTimelineEvents.has(eventNode)) continue;
    boundTimelineEvents.add(eventNode);
    eventNode.addEventListener('mouseenter', () => moveTimeCursor(eventNode));
    eventNode.addEventListener('focus', () => moveTimeCursor(eventNode));
    eventNode.addEventListener('mouseleave', hideTimeCursor);
    eventNode.addEventListener('blur', hideTimeCursor);
    eventNode.addEventListener('click', () => selectEventNode(eventNode));
    if (eventNode.getAttribute('role') === 'button') {
      eventNode.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        selectEventNode(eventNode);
      });
    }
  }
}
bindTimelineEvents();
refreshRelatedEventLinks();
function relatedEventId(link) {
  const excluded = hiddenRtEventIds();
  const candidates = JSON.parse(link.dataset.relatedEventIds || '[]').filter((id) => !excluded.has(id));
  const primary = link.dataset.relatedEventId;
  return candidates.find((id) => isTimelineEventVisible(findEventNodeById(id))) ?? (excluded.has(primary) ? undefined : primary);
}
for (const link of document.querySelectorAll('[data-related-event-id]')) {
  link.addEventListener('click', (event) => {
    const primary = link.dataset.relatedEventId;
    const eventId = relatedEventId(link);
    if (!eventId) {
      event.preventDefault();
      return;
    }
    const eventNode = findEventNodeById(eventId);
    const exact = link.hasAttribute('data-tb-evidence');
    if (exact && !isTimelineEventVisible(eventNode)) {
      event.preventDefault();
      return;
    }
    const collapsedFeedback = eventId === primary && document.body.dataset.mode === 'normal' &&
      eventNode?.classList.contains('normal-feedback-collapsed') && isTimelineEventVisible(eventNode, { allowCollapsedFeedback: true });
    if ((!eventNode && isNormalPendingEvidence(eventId)) || collapsedFeedback) {
      event.preventDefault();
      selectEventNode(undefined, eventId);
      return;
    }
    if (eventNode) {
      selectEventNode(eventNode, eventId);
      if (exact || eventId !== primary) {
        event.preventDefault();
        eventNode.scrollIntoView({ block: 'center', inline: 'center' });
      }
    }
  });
}
inspectorFields?.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-related-event-id]');
  if (!button) return;
  const eventId = relatedEventId(button);
  if (!eventId) return;
  const eventNode = findEventNodeById(eventId);
  const visibleNode = isTimelineEventVisible(eventNode) ? eventNode : undefined;
  if (button.hasAttribute('data-related-visible-only') && !visibleNode) return;
  selectEventNode(visibleNode, eventId);
  visibleNode?.scrollIntoView({ block: 'center', inline: 'center' });
});
for (const link of document.querySelectorAll('[data-semantic-event-id]')) {
  link.addEventListener('click', () => {
    const eventId = link.dataset.semanticEventId;
    const eventNode = findEventNodeById(eventId);
    if (eventNode) {
      selectEventNode(eventNode, eventId);
    }
  });
}
for (const details of document.querySelectorAll('details[data-default-open="false"]')) {
  details.open = false;
}
renderInspector(null);
"""
