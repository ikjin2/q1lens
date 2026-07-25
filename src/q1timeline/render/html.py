from __future__ import annotations

import base64
import json
import math
import re
from html import escape
from pathlib import Path
from typing import Any

from q1timeline.diagnostic_catalog import describe_diagnostic
from q1timeline.q1asm.instruction_table import STATUS_BRANCH_OPS


DEBUG_LANES = {
    "debug.q1_issue",
    "debug.queue_depth",
    "debug.slack",
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
DIAGNOSTIC_OVERLAY_CATEGORIES = {
    "alignment_missing",
    "analysis_incomplete",
    "definite_underflow",
    "feedback_latency_violation",
    "feedback_route_mismatch",
    "loop_truncated",
    "possible_underflow",
    "register_not_ready",
    "runtime_dependent_timing",
    "symbolic_duration",
    "sync_mismatch",
    "unknown_duration",
    "unresolved_branch",
    "unresolved_symbol",
}
BROAD_INFO_DIAGNOSTIC_OVERLAY_CATEGORIES = {
    "loop_truncated",
}
FEEDBACK_OVERLAY_CATEGORIES = {
    "feedback_latency_violation",
    "feedback_route_mismatch",
}
NORMAL_FEEDBACK_COLLAPSED_EVENT_KINDS = frozenset(
    {
        "feedback_pop",
        "feedback_com",
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
    control_flow_events = _control_flow_events(source_events) if default_mode == "debug" else []
    render_events = list(source_events) + control_flow_events
    events, lanes = _group_events(render_events, mode=default_mode)
    event_dom_ids = _event_dom_ids(events)
    diagnostics = ir.get("diagnostics", [])
    diagnostic_overlays_by_event = _diagnostic_overlays_by_event(diagnostics, events, default_mode=default_mode)
    width = 1100
    lane_height = 34
    top = 72
    left = 190
    height = top + lane_height * max(1, len(lanes)) + 40
    time_basis = _default_time_basis(events)
    t_min, t_max = _time_extent(events, time_basis=time_basis)
    local_min, local_max = _time_extent(events, time_basis="local")
    aligned_min, aligned_max = _time_extent(events, time_basis="aligned")
    ticks = _ticks(t_min, t_max)
    svg_parts = [
        f'<svg class="timeline-svg" viewBox="0 0 {width} {height}" role="img" aria-label="Shared x-axis timeline" data-time-min="{t_min}" data-time-max="{t_max}" data-time-basis="{time_basis}" data-local-time-min="{local_min}" data-local-time-max="{local_max}" data-aligned-time-min="{aligned_min}" data-aligned-time-max="{aligned_max}" data-plot-left="{left}" data-plot-right="{width - 24}">',
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
        if sequencer != current_sequencer:
            current_sequencer = sequencer
            svg_parts.append(
                f'<text class="sequencer-label" data-sequencer-label="{escape(sequencer, quote=True)}" x="40" y="{y + 15}">{escape(sequencer)}</text>'
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
        lane_visibility = ' hidden style="display:none"' if is_q1_issue_lane else ""
        svg_parts.append(
            f'<g class="{lane_class}" data-lane="{escape(lane, quote=True)}" data-sequencer-id="{escape(sequencer_id, quote=True)}"{q1_issue_attrs}{lane_visibility}>'
        )
        if lane != sequencer:
            svg_parts.append(f'<text class="lane-label" x="16" y="{y + 20}">{escape(_lane_display_label(lane))}</text>')
        svg_parts.append(f'<line class="lane-rule" x1="{left}" y1="{y + 10}" x2="{width - 24}" y2="{y + 10}" />')
        lane_events = sorted(
            [event for event in events if event.get("lane") == lane],
            key=_event_draw_order,
        )
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

    embedded_view_ir = dict(ir)
    embedded_view_ir["events"] = render_events
    embedded_ir = _json_script(embedded_view_ir)
    analysis_panels = "\n        ".join(
        panel
        for panel in (
            _confidence_legend(_visible_confidences(events, default_mode=default_mode)),
            _control_flow_graph_panel(ir.get("control_flow_graph", {}), source_events),
            _feedback_balance_panel(ir.get("feedback_balance", {})),
            _diagnostics_panel(diagnostics, event_dom_ids),
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
    <section id="timeline-root" class="timeline" aria-label="Timeline">
      {''.join(svg_parts)}
    </section>
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
        {analysis_panels}
      </div>
    </details>
    <aside id="event-inspector" class="event-inspector" aria-live="polite">
      <h2>Selected event</h2>
      <dl id="event-inspector-fields"></dl>
    </aside>
  </main>
  <script id="timeline-ir" type="application/json">{embedded_ir}</script>
  <script>{_js()}</script>
</body>
</html>
"""


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
    x = _scale(t0, t_min, t_max, left, right)
    x1 = _scale(t1, t_min, t_max, left, right)
    width = max(6, x1 - x)
    y = top + lane_index * lane_height
    confidence = event.get("confidence", "unknown")
    kind = event.get("kind", "event")
    display_label = _event_display_label(event)
    inline_label = _event_inline_label(event, width)
    label = escape(inline_label)
    event_id = escape(str(event.get("id", "")))
    dom_id = escape(_event_dom_id(str(event.get("id", "")), event_dom_ids))
    event_diagnostics = diagnostic_overlays_by_event.get(str(event.get("id", "")), [])
    classes = _event_classes(event, confidence=confidence, kind=kind, selected=str(event.get("id")) == highlight_event_id)
    classes = _with_diagnostic_classes(classes, event_diagnostics)
    label_markup = f'<text class="event-label" x="{x + INLINE_LABEL_X_PADDING:.2f}" y="{y + 15}">{label}</text>' if inline_label else ""
    if width < MIN_INLINE_LABEL_WIDTH or not inline_label:
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
    tooltip = escape(_tooltip_with_diagnostics(_tooltip(event), event_diagnostics))
    diagnostic_badge = _diagnostic_badge_svg(event_diagnostics, x + max(8.0, width - 8.0), y + 4.0)
    return (
        f'<g id="{dom_id}" class="{classes}" data-event-id="{event_id}" {data_attribute_text} data-search="{search_text}" aria-label="{aria_label}" tabindex="0">'
        f"<title>{tooltip}</title>"
        f'<rect x="{x:.2f}" y="{y}" width="{width:.2f}" height="22" rx="3" />'
        f"{label_markup}"
        f"{diagnostic_badge}"
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
    x_start = _scale(t0, t_min, t_max, left, right)
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
    start_classes = _with_diagnostic_classes(f"loop-bracket loop-bracket-start{selected_class}", loop_diagnostics)
    end_classes = _with_diagnostic_classes(f"loop-bracket loop-bracket-end{selected_class}", loop_diagnostics)
    diagnostic_attributes = " ".join(_diagnostic_data_attributes(loop_diagnostics))
    if diagnostic_attributes:
        diagnostic_attributes = f" {diagnostic_attributes}"
    badge_y = bracket_top - 9
    start_badge = _diagnostic_badge_svg(loop_diagnostics, x_start + 10.0, badge_y)
    end_badge = _diagnostic_badge_svg(loop_diagnostics, x_end + 10.0, badge_y)
    loop_attr = escape(loop_id, quote=True)
    title = escape(_tooltip_with_diagnostics(f"loop {loop_id}, repeat {count_label}", loop_diagnostics))
    aria_start = escape(f"loop {loop_id} starts, repeats {count_label}", quote=True)
    aria_end = escape(f"loop {loop_id} ends, repeats {count_label}", quote=True)
    id_text = escape(loop_id)
    count_text = escape(count_label)
    start_hitbox_x = x_start - 10
    end_hitbox_x = x_end - 20

    start = (
        f'<g id="{dom_id}-loop-start" class="{start_classes}" '
        f'data-event-id="{event_id}" data-kind="loop_block" data-loop-id="{loop_attr}" '
        f'data-loop-bracket-edge="start" data-t0-x="{x_start:.2f}" data-search="{search_text}" '
        f'data-loop-bracket-badge-y="{badge_y:.2f}"{diagnostic_attributes} '
        f'aria-label="{aria_start}" tabindex="0">'
        f"<title>{title}</title>"
        f'<line class="loop-bracket-guide" x1="{x_start:.2f}" y1="{guide_y:.2f}" x2="{x_end:.2f}" y2="{guide_y:.2f}" />'
        f'<rect class="loop-bracket-hitbox" x="{start_hitbox_x:.2f}" y="{bracket_top - 12:.2f}" width="48" height="{bracket_bottom - bracket_top + 20:.2f}" rx="2" />'
        f'<line class="loop-bracket-stem" x1="{x_start:.2f}" y1="{bracket_top:.2f}" x2="{x_start:.2f}" y2="{bracket_bottom:.2f}" />'
        f'<line class="loop-bracket-cap loop-bracket-cap-top" x1="{x_start:.2f}" y1="{bracket_top:.2f}" x2="{x_start + cap:.2f}" y2="{bracket_top:.2f}" />'
        f'<line class="loop-bracket-cap loop-bracket-cap-bottom" x1="{x_start:.2f}" y1="{bracket_bottom:.2f}" x2="{x_start + cap:.2f}" y2="{bracket_bottom:.2f}" />'
        f'<text class="loop-bracket-id" x="{x_start + 12:.2f}" y="{bracket_top - 3:.2f}">{id_text}</text>'
        f"{start_badge}"
        "</g>"
    )
    end = (
        f'<g id="{dom_id}-loop-end" class="{end_classes}" '
        f'data-event-id="{event_id}" data-kind="loop_block" data-loop-id="{loop_attr}" '
        f'data-loop-bracket-edge="end" data-t0-x="{x_end:.2f}" data-search="{search_text}" '
        f'data-loop-bracket-badge-y="{badge_y:.2f}"{diagnostic_attributes} '
        f'aria-label="{aria_end}" tabindex="0">'
        f"<title>{title}</title>"
        f'<rect class="loop-bracket-hitbox" x="{end_hitbox_x:.2f}" y="{bracket_top - 12:.2f}" width="48" height="{bracket_bottom - bracket_top + 20:.2f}" rx="2" />'
        f'<line class="loop-bracket-stem" x1="{x_end:.2f}" y1="{bracket_top:.2f}" x2="{x_end:.2f}" y2="{bracket_bottom:.2f}" />'
        f'<line class="loop-bracket-cap loop-bracket-cap-top" x1="{x_end:.2f}" y1="{bracket_top:.2f}" x2="{x_end - cap:.2f}" y2="{bracket_top:.2f}" />'
        f'<line class="loop-bracket-cap loop-bracket-cap-bottom" x1="{x_end:.2f}" y1="{bracket_bottom:.2f}" x2="{x_end - cap:.2f}" y2="{bracket_bottom:.2f}" />'
        f'<text class="loop-bracket-count" x="{x_end + 4:.2f}" y="{bracket_top - 3:.2f}">{count_text}</text>'
        f"{end_badge}"
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
    x_start = _scale(t0, t_min, t_max, left, right)
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
        f"branch-marker branch-marker-unresolved{confidence_class}{selected_class}",
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
    diagnostic_badge = _diagnostic_badge_svg(marker_diagnostics, x_start + 10.0, center_y - 8.0)
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
            f'aria-label="{aria_label}" tabindex="0">'
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
            f"{diagnostic_badge}"
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


def _event_draw_order(event: dict[str, Any]) -> tuple[int, str]:
    kind = str(event.get("kind", ""))
    if kind == "loop_block":
        return (0, str(event.get("id", "")))
    if kind == "loop_iteration_preview":
        return (1, str(event.get("id", "")))
    return (2, str(event.get("id", "")))


def _tooltip(event: dict[str, Any]) -> str:
    source = event.get("source") if isinstance(event.get("source"), dict) else {}
    meta = event.get("meta", {})
    source_display = f"{source.get('file')}:{source.get('line')}" if source.get("file") else "unavailable"
    raw_display = str(source.get("raw")) if source.get("raw") else "unavailable"
    lines = [
        f"label: {event.get('label', event.get('kind'))}",
        f"kind: {event.get('kind')}",
        f"sequencer: {event.get('sequencer_id')}",
        f"lane: {event.get('_base_lane', event.get('lane'))}",
        f"time: {_display_value(event.get('t0'))} -> {_display_value(event.get('t1'))}",
        f"duration: {_display_value(event.get('duration'))}",
        f"confidence: {event.get('confidence')}",
    ]
    if isinstance(meta, dict) and ("aligned_t0" in meta or "aligned_t1" in meta):
        lines.append(f"aligned time: {meta.get('aligned_t0')} -> {meta.get('aligned_t1')}")
        lines.append(f"local time: {meta.get('local_t0')} -> {meta.get('local_t1')}")
    if isinstance(meta, dict) and meta.get("status"):
        lines.append(f"status: {meta.get('status')}")
    lines.extend(
        [
            f"source: {source_display}",
            f"raw: {raw_display}",
            f"meta: {json.dumps(meta, sort_keys=True)}",
        ]
    )
    return "\n".join(lines)


def _group_events(events: list[dict[str, Any]], *, mode: str) -> tuple[list[dict[str, Any]], list[str]]:
    grouped_events: list[dict[str, Any]] = []
    lanes: list[str] = []
    for event in events:
        sequencer = event.get("sequencer_id", "")
        lane = str(event.get("lane", ""))
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
                elif _is_debug_lane(lane):
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
        grouped_events.append(copied)
    return grouped_events, lanes


def _lane_display_label(lane: str) -> str:
    base_lane = _debug_lane_candidate(lane)
    if base_lane in {NORMAL_Q1_ISSUE_LANE, "debug.q1_issue"}:
        return "Q1 issue"
    if base_lane.startswith("If "):
        return base_lane
    parsed = _branch_comparison_lane_parts(lane)
    if parsed is None:
        return lane
    path, detail_lane = parsed
    return f"{_branch_comparison_path_label(path)} / {detail_lane}"


def _event_display_label(event: dict[str, Any]) -> str:
    label = str(event.get("label", event.get("kind", "event")))
    meta = event.get("meta", {}) if isinstance(event.get("meta"), dict) else {}
    path = meta.get("branch_comparison_path")
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
    source = event.get("source", {}) if isinstance(event.get("source"), dict) else {}
    raw = str(source.get("raw", "")).strip()
    if raw:
        return raw.split(None, 1)[0]
    meta = event.get("meta", {}) if isinstance(event.get("meta"), dict) else {}
    op = str(meta.get("op", "")).strip()
    if op:
        return op
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
    return _branch_comparison_path_label(path)


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
        x2 = _scale(_event_time_number(to_event, "t0", fallback=t_min, time_basis=time_basis), t_min, t_max, left, right)
        y1 = top + from_lane_index * lane_height + 11
        y2 = top + to_lane_index * lane_height + 11
        delta = x2 - x1
        direction = 1 if delta >= 0 else -1
        control = max(24.0, abs(delta) * 0.45)
        c1 = x1 + direction * control
        c2 = x2 - direction * control
        label = str(flow.get("label", "feedback flow"))
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


def _diagnostics_panel(diagnostics: list[dict[str, Any]], event_dom_ids: dict[str, str]) -> str:
    counts = {severity: 0 for severity in ("error", "warning", "info", "hint")}
    for diagnostic in diagnostics:
        severity = str(diagnostic.get("severity", "info"))
        counts[severity] = counts.get(severity, 0) + 1
    summary = " ".join(f"{severity}={counts.get(severity, 0)}" for severity in ("error", "warning", "info", "hint"))
    error_items = "".join(_diagnostic_item(diagnostic, event_dom_ids) for diagnostic in diagnostics if diagnostic.get("severity") == "error")
    secondary_diagnostics = [diagnostic for diagnostic in diagnostics if diagnostic.get("severity") != "error"]
    secondary_items = "".join(_diagnostic_item(diagnostic, event_dom_ids) for diagnostic in secondary_diagnostics[:25])
    critical_html = f'<ol class="diagnostics-critical">{error_items}</ol>' if error_items else ""
    secondary_html = ""
    if secondary_items:
        secondary_count = len(secondary_diagnostics)
        secondary_label = f"{secondary_count} non-error diagnostic" + ("" if secondary_count == 1 else "s")
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
    for field in ("unmatched_receives", "unconsumed_payloads"):
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
    send_payloads = escape(str(channel.get("send_payloads", 0)))
    receives = escape(str(channel.get("receives", 0)))
    matched = escape(str(channel.get("matched", 0)))
    discarded_payloads = escape(str(channel.get("discarded_payloads", 0)))
    unmatched_receives = escape(str(channel.get("unmatched_receives", 0)))
    unconsumed_payloads = escape(str(channel.get("unconsumed_payloads", 0)))
    return (
        f'<li class="feedback-balance-channel status-{status}">'
        f"<strong>ch {channel_id}</strong> "
        f"<code>{status}</code> "
        f"matched {matched}/{receives}; sends={sends}; payloads={send_payloads}; "
        f"discarded payloads={discarded_payloads}; unmatched receives={unmatched_receives}; "
        f"unconsumed payloads={unconsumed_payloads}"
        "</li>"
    )


def _diagnostic_item(diagnostic: dict[str, Any], event_dom_ids: dict[str, str]) -> str:
    severity = escape(str(diagnostic.get("severity", "info")))
    presentation = describe_diagnostic(diagnostic)
    category = escape(presentation["category"])
    title = escape(presentation["title"])
    summary = escape(presentation["summary"])
    fix = escape(presentation["fix"])
    fix_html = f'<p class="diagnostic-fix">Fix: {fix}</p>' if fix else ""
    source = diagnostic.get("source") if isinstance(diagnostic.get("source"), dict) else {}
    location = ""
    if source:
        location = f" {escape(str(source.get('file')))}:{escape(str(source.get('line')))}"
    related = "".join(_related_event_link(event_id, event_dom_ids) for event_id in diagnostic.get("related_events", []))
    return (
        f'<li class="diagnostic severity-{severity}">'
        f"<strong>{severity}</strong> <span class=\"diagnostic-title\">{title}</span> "
        f"<code>{category}</code>{location}"
        f'<p class="diagnostic-message">{summary}</p>'
        f"{fix_html}"
        f'<span class="related-events">{related}</span>'
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


def _control_flow_graph_panel(graph: dict[str, Any], events: list[Any]) -> str:
    sequencers = graph.get("sequencers", []) if isinstance(graph, dict) else []
    if not isinstance(sequencers, list):
        return ""
    branch_decisions = _branch_decisions_by_source(events)
    rendered = "".join(_control_flow_graph_sequencer_panel(sequencer, branch_decisions) for sequencer in sequencers)
    if not rendered:
        return ""
    node_count = sum(len(sequencer.get("nodes", [])) for sequencer in sequencers if isinstance(sequencer, dict))
    edge_count = sum(len(sequencer.get("edges", [])) for sequencer in sequencers if isinstance(sequencer, dict))
    summary = f"{node_count} nodes - {edge_count} edges"
    return (
        '<details class="control-flow-graph" open data-default-open="true" aria-label="Control-flow graph">'
        f'<summary><span class="panel-title">Control-flow graph</span> <span class="panel-summary">{escape(summary)}</span></summary>'
        f"{rendered}"
        "</details>"
    )


def _control_flow_graph_sequencer_panel(
    sequencer: Any,
    branch_decisions: dict[tuple[str, int], dict[str, Any]],
) -> str:
    if not isinstance(sequencer, dict):
        return ""
    nodes = sequencer.get("nodes", [])
    edges = sequencer.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list) or not nodes:
        return ""
    node_by_id = {
        str(node.get("id", "")): node
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    node_items = "".join(_control_flow_node_item(node) for node in nodes if isinstance(node, dict))
    edge_items = "".join(
        _control_flow_edge_item(edge, node_by_id)
        for edge in edges
        if isinstance(edge, dict)
    )
    graph_svg = _control_flow_graph_svg(nodes, edges, node_by_id)
    branch_map = _control_flow_branch_map(edges, node_by_id, branch_decisions)
    sequencer_id = escape(str(sequencer.get("sequencer_id", "sequencer")))
    return (
        '<section class="cfg-sequencer">'
        f"<h2>{sequencer_id}</h2>"
        f"{branch_map}"
        f"{graph_svg}"
        '<div class="cfg-grid">'
        f'<div><h3>Blocks</h3><ol class="cfg-nodes">{node_items}</ol></div>'
        f'<div><h3>Edges</h3><ol class="cfg-edges">{edge_items}</ol></div>'
        "</div>"
        "</section>"
    )


def _control_flow_branch_map(
    edges: list[Any],
    node_by_id: dict[str, dict[str, Any]],
    branch_decisions: dict[tuple[str, int], dict[str, Any]],
) -> str:
    branches = _control_flow_branch_groups(edges)
    if not branches:
        return ""
    items = "".join(_control_flow_branch_item(branch, node_by_id, branch_decisions) for branch in branches)
    return f'<div class="cfg-branch-map"><h3>Branch map</h3><ol class="cfg-branch-list">{items}</ol></div>'


def _control_flow_branch_groups(edges: list[Any]) -> list[dict[str, dict[str, Any]]]:
    groups: dict[tuple[str, str, int | None, str], dict[str, dict[str, Any]]] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        kind = str(edge.get("kind", ""))
        if kind not in {"branch_taken", "branch_fallthrough"}:
            continue
        source = edge.get("source")
        key = (
            str(edge.get("from_node_id", "")),
            str(source.get("file", "")) if isinstance(source, dict) else "",
            source.get("line") if isinstance(source, dict) and type(source.get("line")) is int else None,
            str(edge.get("op", "")),
        )
        groups.setdefault(key, {})[kind] = edge
    return [group for group in groups.values() if "branch_taken" in group and "branch_fallthrough" in group]


def _control_flow_branch_item(
    branch: dict[str, dict[str, Any]],
    node_by_id: dict[str, dict[str, Any]],
    branch_decisions: dict[tuple[str, int], dict[str, Any]],
) -> str:
    taken = branch["branch_taken"]
    fallthrough = branch["branch_fallthrough"]
    source = taken.get("source") if isinstance(taken.get("source"), dict) else fallthrough.get("source")
    decision = branch_decisions.get(_source_key(source), {})
    condition = str(decision.get("condition") or _branch_condition_fallback(taken, source))
    raw = str(source.get("raw", taken.get("label", "branch")) if isinstance(source, dict) else taken.get("label", "branch"))
    status = _control_flow_branch_status(decision, taken, fallthrough)
    source_label = _source_range_label(source, None)
    assumed_path = str(decision.get("assumed_branch_path", ""))
    return (
        '<li class="cfg-branch-card">'
        '<div class="cfg-branch-header">'
        f'<div><code>{escape(raw)}</code><span class="cfg-source">{escape(source_label)}</span></div>'
        f'<span class="cfg-branch-status">{escape(status)}</span>'
        "</div>"
        f'<div class="cfg-branch-condition">{escape(condition)}</div>'
        '<div class="cfg-branch-paths">'
        f'{_control_flow_branch_path_item(taken, node_by_id, "taken", assumed_path)}'
        f'{_control_flow_branch_path_item(fallthrough, node_by_id, "fallthrough", assumed_path)}'
        "</div>"
        "</li>"
    )


def _control_flow_branch_path_item(
    edge: dict[str, Any],
    node_by_id: dict[str, dict[str, Any]],
    path: str,
    assumed_path: str,
) -> str:
    kind = str(edge.get("kind", "edge"))
    edge_id = escape(str(edge.get("id", "")), quote=True)
    class_name = escape(_class_token(kind), quote=True)
    selected_class = " is-assumed" if assumed_path in {path, "both"} else ""
    event_ids_attr = _cfg_event_ids_attribute(edge)
    target = _control_flow_edge_node_label(edge.get("to_node_id"), node_by_id)
    if not target:
        target = str(edge.get("target_label") or edge.get("target") or edge.get("target_pc") or "?")
    label = "Condition true" if path == "taken" else "Condition false"
    action = "jump to" if path == "taken" else "continue to"
    return (
        f'<div class="cfg-branch-path cfg-edge-{class_name}{selected_class}" '
        f'data-cfg-edge-id="{edge_id}"{event_ids_attr} tabindex="0">'
        f'<span class="cfg-branch-path-label">{escape(label)}</span>'
        f'<span class="cfg-branch-path-target">{escape(action)} <code>{escape(target)}</code></span>'
        f'<span class="cfg-edge-label">{escape(str(edge.get("label", "")))}</span>'
        "</div>"
    )


def _control_flow_branch_status(
    decision: dict[str, Any],
    taken: dict[str, Any],
    fallthrough: dict[str, Any],
) -> str:
    assumed_path = decision.get("assumed_branch_path")
    if assumed_path == "taken":
        return "shown path: condition true"
    if assumed_path == "fallthrough":
        return "shown path: condition false"
    if assumed_path == "both":
        return "shown paths: both"
    if assumed_path == "collapsed":
        return "shown path: collapsed"
    if taken.get("event_ids") or fallthrough.get("event_ids"):
        return "reached in current timeline"
    return "not reached in current timeline"


def _control_flow_graph_svg(
    nodes: list[Any],
    edges: list[Any],
    node_by_id: dict[str, dict[str, Any]],
) -> str:
    graph_nodes = [node for node in nodes if isinstance(node, dict) and node.get("id")]
    if not graph_nodes:
        return ""
    layout = _control_flow_graph_layout(graph_nodes)
    width = 620
    height = max(92, 36 + len(graph_nodes) * 64)
    edge_parts = [
        _control_flow_graph_edge_svg(edge, layout, node_by_id)
        for edge in edges
        if isinstance(edge, dict)
    ]
    node_parts = [_control_flow_graph_node_svg(node, layout[str(node.get("id"))]) for node in graph_nodes]
    return (
        f'<svg class="cfg-graph" viewBox="0 0 {width} {height}" role="img" aria-label="Control-flow graph diagram">'
        '<defs><marker id="cfg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>'
        f"{''.join(edge_parts)}"
        f"{''.join(node_parts)}"
        "</svg>"
    )


def _control_flow_graph_layout(nodes: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    layout: dict[str, dict[str, float]] = {}
    for index, node in enumerate(nodes):
        layout[str(node.get("id"))] = {
            "x": 24.0,
            "y": 24.0 + index * 64.0,
            "width": 188.0,
            "height": 38.0,
        }
    return layout


def _control_flow_graph_node_svg(node: dict[str, Any], box: dict[str, float]) -> str:
    node_id = escape(str(node.get("id", "")), quote=True)
    label = escape(str(node.get("label", "block")))
    pc_range = escape(f"pc {node.get('start_pc', '?')}-{node.get('end_pc', '?')}")
    title = escape(f"{node.get('label', 'block')} {pc_range}")
    event_ids_attr = _cfg_event_ids_attribute(node)
    x = box["x"]
    y = box["y"]
    width = box["width"]
    height = box["height"]
    return (
        f'<g class="cfg-graph-node" data-cfg-node-id="{node_id}"{event_ids_attr} tabindex="0">'
        f"<title>{title}</title>"
        f'<rect x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" height="{height:.2f}" rx="4" />'
        f'<text class="cfg-graph-node-label" x="{x + 10:.2f}" y="{y + 17:.2f}">{label}</text>'
        f'<text class="cfg-graph-node-meta" x="{x + 10:.2f}" y="{y + 31:.2f}">{pc_range}</text>'
        "</g>"
    )


def _control_flow_graph_edge_svg(
    edge: dict[str, Any],
    layout: dict[str, dict[str, float]],
    node_by_id: dict[str, dict[str, Any]],
) -> str:
    from_id = str(edge.get("from_node_id", ""))
    to_id = str(edge.get("to_node_id", ""))
    from_box = layout.get(from_id)
    to_box = layout.get(to_id)
    if from_box is None:
        return ""
    edge_id = escape(str(edge.get("id", "")), quote=True)
    kind = str(edge.get("kind", "edge"))
    class_name = escape(_class_token(kind), quote=True)
    label = escape(str(edge.get("label", "")))
    event_ids_attr = _cfg_event_ids_attribute(edge)
    title = escape(
        f"{_control_flow_edge_kind_label(kind)}: "
        f"{_control_flow_edge_node_label(from_id, node_by_id)} -> "
        f"{_control_flow_edge_node_label(to_id, node_by_id) or edge.get('target', '?')}"
    )
    path = _control_flow_graph_edge_path(from_box, to_box)
    text = _control_flow_graph_edge_label(edge, from_box, to_box)
    return (
        f'<g class="cfg-graph-edge-group cfg-edge-{class_name}" data-cfg-edge-id="{edge_id}"{event_ids_attr} tabindex="0">'
        f"<title>{title}</title>"
        f'<path class="cfg-graph-edge cfg-edge-{class_name}" data-cfg-edge-id="{edge_id}"{event_ids_attr} d="{path}" marker-end="url(#cfg-arrow)" />'
        f"{text if label else ''}"
        "</g>"
    )


def _control_flow_graph_edge_path(
    from_box: dict[str, float],
    to_box: dict[str, float] | None,
) -> str:
    x1 = from_box["x"] + from_box["width"]
    y1 = from_box["y"] + from_box["height"] / 2
    if to_box is None:
        x2 = x1 + 180
        y2 = y1
        return f"M {x1:.2f} {y1:.2f} C {x1 + 70:.2f} {y1:.2f}, {x2 - 50:.2f} {y2:.2f}, {x2:.2f} {y2:.2f}"
    x2 = to_box["x"] + to_box["width"]
    y2 = to_box["y"] + to_box["height"] / 2
    if abs(y1 - y2) < 1:
        loop_x = x1 + 118
        return (
            f"M {x1:.2f} {y1:.2f} "
            f"C {loop_x:.2f} {y1 - 42:.2f}, {loop_x:.2f} {y1 + 42:.2f}, {x2:.2f} {y2:.2f}"
        )
    bend_x = x1 + 118
    return f"M {x1:.2f} {y1:.2f} C {bend_x:.2f} {y1:.2f}, {bend_x:.2f} {y2:.2f}, {x2:.2f} {y2:.2f}"


def _control_flow_graph_edge_label(
    edge: dict[str, Any],
    from_box: dict[str, float],
    to_box: dict[str, float] | None,
) -> str:
    label = escape(str(edge.get("label", "")))
    x = from_box["x"] + from_box["width"] + 128
    y = from_box["y"] + from_box["height"] / 2 - 5
    if to_box is not None:
        y = (from_box["y"] + to_box["y"]) / 2 + 8
    return f'<text class="cfg-graph-edge-label" x="{x:.2f}" y="{y:.2f}">{label}</text>'


def _control_flow_node_item(node: dict[str, Any]) -> str:
    node_id = escape(str(node.get("id", "")), quote=True)
    label = escape(str(node.get("label", "block")))
    start_pc = escape(str(node.get("start_pc", "?")))
    end_pc = escape(str(node.get("end_pc", "?")))
    source = _source_range_label(node.get("source"), node.get("source_end"))
    event_ids_attr = _cfg_event_ids_attribute(node)
    return (
        f'<li class="cfg-node" data-cfg-node-id="{node_id}"{event_ids_attr}>'
        f"<code>{label}</code> "
        f'<span class="cfg-meta">pc {start_pc}-{end_pc}</span> '
        f'<span class="cfg-source">{escape(source)}</span>'
        "</li>"
    )


def _control_flow_edge_item(edge: dict[str, Any], node_by_id: dict[str, dict[str, Any]]) -> str:
    edge_id = escape(str(edge.get("id", "")), quote=True)
    kind = str(edge.get("kind", "edge"))
    kind_label = escape(_control_flow_edge_kind_label(kind))
    label = escape(str(edge.get("label", "")))
    event_ids_attr = _cfg_event_ids_attribute(edge)
    from_label = _control_flow_edge_node_label(edge.get("from_node_id"), node_by_id)
    to_label = _control_flow_edge_node_label(edge.get("to_node_id"), node_by_id)
    if not to_label:
        to_label = str(edge.get("target", "") or "?")
    route = escape(f"{from_label} -> {to_label}")
    source = _source_range_label(edge.get("source"), None)
    return (
        f'<li class="cfg-edge cfg-edge-{escape(_class_token(kind), quote=True)}" data-cfg-edge-id="{edge_id}"{event_ids_attr}>'
        f'<span class="cfg-edge-kind">{kind_label}</span> '
        f"<code>{route}</code> "
        f'<span class="cfg-edge-label">{label}</span> '
        f'<span class="cfg-source">{escape(source)}</span>'
        "</li>"
    )


def _cfg_event_ids_attribute(item: dict[str, Any]) -> str:
    event_ids = item.get("event_ids", [])
    if not isinstance(event_ids, list):
        return ""
    ids = [str(event_id) for event_id in event_ids if isinstance(event_id, str) and event_id]
    if not ids:
        return ""
    return f' data-cfg-event-ids="{escape(" ".join(ids), quote=True)}"'


def _branch_decisions_by_source(events: list[Any]) -> dict[tuple[str, int], dict[str, Any]]:
    decisions: dict[tuple[str, int], dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        meta = event.get("meta", {})
        if not isinstance(meta, dict) or not meta.get("branch_id"):
            continue
        key = _source_key(event.get("source"))
        if not key:
            continue
        decision = decisions.setdefault(key, {})
        for name in (
            "condition",
            "branch_decision",
            "branch_policy",
            "assumed_branch_path",
            "assumed_branch_taken",
            "target_label",
        ):
            value = meta.get(name)
            if value is not None and value != "":
                decision[name] = value
    return decisions


def _source_key(source: Any) -> tuple[str, int] | None:
    if not isinstance(source, dict):
        return None
    file = source.get("file")
    line = source.get("line")
    if not file or type(line) is not int:
        return None
    return (str(file), line)


def _branch_source_comment(source: Any) -> str:
    if not isinstance(source, dict):
        return ""
    raw = str(source.get("raw", ""))
    if "#" not in raw:
        return ""
    return raw.split("#", 1)[1].strip()


def _branch_condition_fallback(edge: dict[str, Any], source: Any) -> str:
    comment = _branch_source_comment(source)
    if comment:
        return comment
    op = str(edge.get("op", "branch"))
    if op in {"jge", "jlt", *STATUS_BRANCH_OPS}:
        return f"{op} status flags"
    return f"{op} condition"


def _control_flow_edge_node_label(node_id: Any, node_by_id: dict[str, dict[str, Any]]) -> str:
    if not node_id:
        return ""
    node = node_by_id.get(str(node_id))
    if not isinstance(node, dict):
        return str(node_id)
    return str(node.get("label") or node_id)


def _control_flow_edge_kind_label(kind: str) -> str:
    return kind.replace("_", " ")


def _source_range_label(start: Any, end: Any) -> str:
    if not isinstance(start, dict):
        return ""
    file = str(start.get("file", ""))
    line = start.get("line")
    end_line = end.get("line") if isinstance(end, dict) else None
    if line is None:
        return file
    if end_line is not None and end_line != line:
        return f"{file}:{line}-{end_line}"
    return f"{file}:{line}"


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


def _related_event_link(event_id: Any, event_dom_ids: dict[str, str]) -> str:
    raw_id = str(event_id)
    return (
        f' <a href="#{escape(_event_dom_id(raw_id, event_dom_ids), quote=True)}" '
        f'data-related-event-id="{escape(raw_id, quote=True)}">{escape(raw_id)}</a>'
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
    for index, event in enumerate(ir.get("events", [])):
        _validate_event_required_fields(event, index=index)
        concrete_times = {
            field: _validate_concrete_time_value(event.get(field))
            for field in ("t0", "t1", "duration")
        }
        _validate_event_time_range(event, concrete_times)
        _validate_event_time_metadata(event)
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


def _validate_concrete_time_value(value: Any) -> int | None:
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
    if not isinstance(raw_value, int):
        raise RenderError(f"TimelineIR concrete time value must be integer nanoseconds, got {raw_value!r}")
    return raw_value


def _validate_event_time_range(event: dict[str, Any], concrete_times: dict[str, int | None]) -> None:
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


def _validate_event_time_metadata(event: dict[str, Any]) -> None:
    meta = event.get("meta")
    if not isinstance(meta, dict):
        return
    for field in ("aligned_t0", "aligned_t1", "local_t0", "local_t1"):
        if field in meta:
            edge = "t0" if field.endswith("_t0") else "t1"
            _validate_integer_time_metadata_value(meta[field], field=field, source_value=event.get(edge))
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
    start = meta.get(start_field)
    end = meta.get(end_field)
    if type(start) is int and type(end) is int and end < start:
        raise RenderError(f"TimelineIR meta.{end_field} must not be before meta.{start_field}: {end} < {start}")


def _validate_integer_time_metadata_value(value: Any, *, field: str, source_value: Any) -> None:
    if type(value) is not int:
        if not _is_symbolic_time_metadata_string(value, source_value=source_value):
            raise RenderError(f"TimelineIR meta.{field} time value must be integer nanoseconds, got {value!r}")


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


def _time_extent(events: list[dict[str, Any]], *, time_basis: str) -> tuple[int, int]:
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


def _has_aligned_time(event: dict[str, Any]) -> bool:
    meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
    return _plain_number(meta.get("aligned_t0")) is not None or _plain_number(meta.get("aligned_t1")) is not None


def _ticks(t_min: int, t_max: int) -> list[int]:
    if t_max <= t_min:
        return [t_min]
    step = max(1, (t_max - t_min) // 4)
    ticks = [t_min + step * index for index in range(5)]
    if ticks[-1] != t_max:
        ticks.append(t_max)
    return sorted(set(ticks))


def _scale(value: int, t_min: int, t_max: int, left: int, right: int) -> float:
    if t_max <= t_min:
        return float(left)
    return left + (right - left) * ((value - t_min) / (t_max - t_min))


def _value_number(value: Any, *, fallback: int) -> int:
    if isinstance(value, dict) and value.get("kind") == "concrete":
        try:
            raw_value = value["value"]
            if isinstance(raw_value, bool):
                raise TypeError
            if not isinstance(raw_value, (int, float)):
                raise TypeError
            if not isinstance(raw_value, int):
                raise ValueError("fractional time")
            return raw_value
        except (KeyError, TypeError, ValueError) as exc:
            raw_value = value.get("value")
            if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
                raise RenderError(
                    f"TimelineIR concrete time value must be integer nanoseconds, got {raw_value!r}"
                ) from exc
            raise RenderError(f"TimelineIR concrete time value must be numeric, got {raw_value!r}") from exc
    return fallback


def _event_time_number(event: dict[str, Any], edge: str, *, fallback: int, time_basis: str) -> int:
    if time_basis == "aligned":
        meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
        aligned = _plain_number(meta.get(f"aligned_{edge}"))
        if aligned is not None:
            return aligned
    return _value_number(event.get(edge), fallback=fallback)


def _plain_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


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


def _is_normal_feedback_collapsed_kind(kind: Any) -> bool:
    return str(kind) in NORMAL_FEEDBACK_COLLAPSED_EVENT_KINDS


def _diagnostic_overlays_by_event(
    diagnostics: Any,
    events: list[dict[str, Any]],
    *,
    default_mode: str,
) -> dict[str, list[dict[str, Any]]]:
    overlays: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(diagnostics, list):
        return overlays
    event_by_id = {str(event.get("id", "")): event for event in events}
    for diagnostic in diagnostics:
        if not _is_timeline_overlay_diagnostic(diagnostic):
            continue
        related_events = diagnostic.get("related_events", [])
        if not isinstance(related_events, list):
            continue
        overlay_event_ids = _diagnostic_overlay_event_ids(
            diagnostic,
            [event_id for event_id in related_events if isinstance(event_id, str)],
            event_by_id,
            default_mode=default_mode,
        )
        for event_id in overlay_event_ids:
            overlays.setdefault(event_id, []).append(diagnostic)
    return overlays


def _diagnostic_overlay_event_ids(
    diagnostic: dict[str, Any],
    related_event_ids: list[str],
    event_by_id: dict[str, dict[str, Any]],
    *,
    default_mode: str,
) -> list[str]:
    category = str(diagnostic.get("category", ""))
    severity = str(diagnostic.get("severity", "info"))
    if category in BROAD_INFO_DIAGNOSTIC_OVERLAY_CATEGORIES and severity in {"hint", "info"}:
        representative = _representative_diagnostic_event_id(related_event_ids, event_by_id, default_mode=default_mode)
        return [representative] if representative else []
    return related_event_ids


def _representative_diagnostic_event_id(
    event_ids: list[str],
    event_by_id: dict[str, dict[str, Any]],
    *,
    default_mode: str,
) -> str | None:
    for event_id in event_ids:
        event = event_by_id.get(event_id)
        if event and _is_preferred_diagnostic_overlay_event(event, default_mode=default_mode):
            return event_id
    return event_ids[0] if event_ids else None


def _is_preferred_diagnostic_overlay_event(event: dict[str, Any], *, default_mode: str) -> bool:
    kind = str(event.get("kind", ""))
    if kind == "q1_issue":
        return False
    if default_mode == "normal":
        lane = str(event.get("lane", ""))
        if _is_debug_lane(lane):
            return False
        if _is_normal_feedback_collapsed_kind(kind):
            return False
    return True


def _is_timeline_overlay_diagnostic(diagnostic: Any) -> bool:
    if not isinstance(diagnostic, dict):
        return False
    return str(diagnostic.get("category", "")) in DIAGNOSTIC_OVERLAY_CATEGORIES


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
    return [
        f'data-diagnostic-severity="{escape(severity, quote=True)}"',
        f'data-diagnostic-categories="{escape(categories, quote=True)}"',
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


def _diagnostic_badge_svg(diagnostics: list[dict[str, Any]], x: float, y: float) -> str:
    if not diagnostics:
        return ""
    severity = _diagnostic_severity(diagnostics)
    categories = ",".join(_diagnostic_categories(diagnostics))
    label = str(min(len(diagnostics), 9)) if len(diagnostics) > 1 else ("!" if severity in {"warning", "error"} else "i")
    title = escape(_tooltip_with_diagnostics("Timeline diagnostic", diagnostics))
    severity_class = escape(_class_token(severity), quote=True)
    category_attr = escape(categories, quote=True)
    return (
        f'<g class="diagnostic-badge severity-{severity_class}" data-diagnostic-count="{len(diagnostics)}" '
        f'data-diagnostic-categories="{category_attr}" transform="translate({x:.2f},{y:.2f})" '
        f'role="button" tabindex="0" aria-label="{title}">'
        f"<title>{title}</title>"
        '<circle class="diagnostic-badge-dot" cx="0" cy="0" r="6" />'
        f'<text class="diagnostic-badge-label" x="0" y="3">{escape(label)}</text>'
        "</g>"
    )


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
body.vscode-light { color-scheme: light; }
body.vscode-dark { color-scheme: dark; background: #1e1e1e !important; background-color: #1e1e1e !important; }
body.vscode-high-contrast { color-scheme: dark; }
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
.diagnostics { margin: 16px 16px 0; padding: 10px 12px; border: 1px solid var(--vscode-panel-border, #d8dee6); }
.diagnostics ol { margin: 8px 0 0; padding-left: 20px; }
.diagnostics-secondary { margin-top: 8px; }
.diagnostics-secondary > summary,
.confidence-legend > summary,
.control-flow-graph > summary,
.feedback-balance > summary { cursor: pointer; font-size: 12px; }
.panel-title { font-weight: 700; font-size: 14px; }
.panel-summary { margin-left: 6px; color: var(--vscode-descriptionForeground, #4b5563); font-size: 12px; }
.diagnostic { margin: 4px 0; font-size: 12px; }
.diagnostic-summary { margin: 0; color: var(--vscode-descriptionForeground, #4b5563); font-size: 12px; }
.diagnostic-title { font-weight: 700; }
.diagnostic-message, .diagnostic-fix { margin: 3px 0 0; }
.diagnostic-fix { color: var(--vscode-descriptionForeground, #4b5563); }
.severity-error strong { color: #c72c2c; }
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
.control-flow-graph { margin: 16px 16px 0; padding: 10px 12px; border: 1px solid var(--vscode-panel-border, #d8dee6); }
.cfg-sequencer { margin-top: 10px; }
.cfg-sequencer h2 { margin-top: 0; }
.cfg-sequencer h3 { margin: 0 0 6px; font-size: 12px; color: var(--vscode-descriptionForeground, #4b5563); }
.cfg-branch-map { margin: 8px 0 12px; }
.cfg-branch-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.cfg-branch-card { border: 1px solid var(--vscode-panel-border, #d8dee6); border-radius: 4px; padding: 8px; background: var(--vscode-editorWidget-background, #f8fafc); }
.cfg-branch-header { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; align-items: start; font-size: 12px; }
.cfg-branch-header code { overflow-wrap: anywhere; }
.cfg-branch-status { color: #1d4ed8; font-weight: 700; white-space: nowrap; }
.cfg-branch-condition { margin-top: 4px; color: var(--vscode-descriptionForeground, #4b5563); font-size: 12px; }
.cfg-branch-paths { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 8px; margin-top: 8px; }
.cfg-branch-path { cursor: pointer; border: 1px solid #94a3b8; border-left-width: 4px; border-radius: 4px; padding: 7px 8px; display: grid; gap: 3px; background: var(--vscode-editor-background, #ffffff); font-size: 12px; }
.cfg-branch-path.cfg-edge-branch_taken { border-left-color: #7c3aed; }
.cfg-branch-path.cfg-edge-branch_fallthrough { border-left-color: #64748b; }
.cfg-branch-path.is-assumed { border-color: #2563eb; box-shadow: inset 0 0 0 1px #2563eb; }
.cfg-branch-path-label { font-weight: 700; }
.cfg-branch-path-target { color: var(--vscode-foreground, #17202a); }
.cfg-graph { width: 100%; min-height: 220px; max-height: 720px; margin: 8px 0 12px; border: 1px solid var(--vscode-panel-border, #d8dee6); background: var(--vscode-editor-background, #ffffff); }
.cfg-graph-node rect { fill: var(--vscode-editorWidget-background, #f8fafc); stroke: #64748b; stroke-width: 1.2; }
.cfg-graph-node text { fill: var(--vscode-foreground, #17202a); font-size: 11px; pointer-events: none; }
.cfg-graph-node, .cfg-graph-edge-group, .cfg-node, .cfg-edge, .cfg-branch-path { cursor: pointer; }
.cfg-graph-node-label { font-weight: 700; }
.cfg-graph-node-meta { fill: var(--vscode-descriptionForeground, #4b5563); }
.cfg-graph-edge { fill: none; stroke: #64748b; stroke-width: 1.4; opacity: 0.9; }
.cfg-edge-branch_taken .cfg-graph-edge { stroke: #7c3aed; }
.cfg-edge-branch_fallthrough .cfg-graph-edge { stroke: #64748b; stroke-dasharray: 4 4; }
.cfg-edge-jump .cfg-graph-edge { stroke: #0369a1; }
.cfg-graph-edge-label { fill: var(--vscode-descriptionForeground, #4b5563); font-size: 10px; }
.cfg-graph-node.is-selected rect, .cfg-graph-node.is-related rect { stroke: #2563eb; stroke-width: 2.5; }
.cfg-graph-edge-group.is-selected .cfg-graph-edge, .cfg-graph-edge-group.is-related .cfg-graph-edge { stroke: #2563eb; stroke-width: 3; opacity: 1; }
.cfg-node.is-selected, .cfg-node.is-related, .cfg-edge.is-selected, .cfg-edge.is-related, .cfg-branch-path.is-selected, .cfg-branch-path.is-related { outline: 1px solid #2563eb; outline-offset: 2px; }
.cfg-grid { display: grid; grid-template-columns: minmax(180px, 1fr) minmax(260px, 2fr); gap: 14px; }
.cfg-nodes, .cfg-edges { margin: 0; padding-left: 20px; font-size: 12px; }
.cfg-node, .cfg-edge { margin: 4px 0; overflow-wrap: anywhere; }
.cfg-edge-kind { display: inline-block; min-width: 96px; color: var(--vscode-descriptionForeground, #4b5563); }
.cfg-meta, .cfg-source, .cfg-edge-label { color: var(--vscode-descriptionForeground, #4b5563); }
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
body.vscode-high-contrast .control-flow-graph,
body.vscode-high-contrast .feedback-balance,
body.vscode-high-contrast .semantic-annotations,
body.vscode-high-contrast .event-inspector { border-color: var(--vscode-contrastBorder, #ffffff); }
.lane-label, .tick-label { fill: var(--vscode-descriptionForeground, #4b5563); font-size: 12px; }
.sequencer-label { fill: var(--vscode-foreground, #17202a); font-size: 12px; font-weight: 700; }
.lane-rule, .grid { stroke: #d7dde4; stroke-width: 1; }
.event rect { stroke: rgba(0, 0, 0, 0.38); stroke-width: 1; fill: #cdbb96; }
body.vscode-high-contrast .event rect { stroke: var(--vscode-contrastActiveBorder, #ffffff); stroke-width: 2; }
.event text { pointer-events: none; fill: #0f1720; font-size: 11px; }
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
.loop-bracket-guide { stroke: #32613a; stroke-width: 1.2; stroke-dasharray: 3 4; opacity: 0.5; pointer-events: none; }
.loop-bracket-stem, .loop-bracket-cap { stroke: #32613a; stroke-width: 2.2; stroke-linecap: square; pointer-events: none; }
.loop-bracket text { fill: #32613a; font-size: 11px; font-weight: 700; paint-order: normal; stroke: none; stroke-width: 0; pointer-events: none; }
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
.branch-marker-guide { stroke: #b45309; stroke-width: 1.1; stroke-dasharray: 2 5; opacity: 0.45; pointer-events: none; }
.branch-marker-diamond { fill: #fde68a; stroke: #b45309; stroke-width: 1.8; pointer-events: none; }
.branch-marker.confidence-symbolic .branch-marker-diamond { fill: #f7d774; stroke-dasharray: 5 3; }
.branch-marker.confidence-assumed .branch-marker-diamond { fill: #b8c2cc; stroke-dasharray: 4 3; }
.branch-marker.confidence-unknown .branch-marker-diamond { fill: #c9ced6; stroke-dasharray: 2 2; }
.branch-marker.confidence-runtime_dependent .branch-marker-diamond { fill: #e5a0a0; stroke-dasharray: 6 2; }
.branch-marker text { fill: #92400e; font-size: 11px; font-weight: 700; paint-order: normal; stroke: none; stroke-width: 0; pointer-events: none; }
.branch-marker-path-icon { fill: none; stroke: #92400e; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; pointer-events: none; }
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
.event.has-diagnostic rect { stroke: #d97706; stroke-width: 3; }
.event.diagnostic-error rect { stroke: #dc2626; stroke-width: 3.2; }
.event.diagnostic-info rect,
.event.diagnostic-hint rect { stroke: #2563eb; stroke-width: 2.5; stroke-dasharray: 4 2; }
.event.is-selected.has-diagnostic rect { stroke: #111827; stroke-width: 3.2; }
.event.is-related.has-diagnostic rect { stroke: #2563eb; stroke-width: 3.2; }
.branch-marker.has-diagnostic .branch-marker-diamond { stroke: #d97706; stroke-width: 3; }
.branch-marker.diagnostic-error .branch-marker-diamond { stroke: #dc2626; stroke-width: 3.2; }
.branch-marker.diagnostic-info .branch-marker-diamond,
.branch-marker.diagnostic-hint .branch-marker-diamond { stroke: #2563eb; stroke-width: 2.6; }
.diagnostic-badge { pointer-events: all; cursor: help; }
.diagnostic-badge-dot { fill: #d97706; stroke: #ffffff; stroke-width: 1.4; }
.diagnostic-badge.severity-error .diagnostic-badge-dot { fill: #dc2626; }
.diagnostic-badge.severity-info .diagnostic-badge-dot,
.diagnostic-badge.severity-hint .diagnostic-badge-dot { fill: #2563eb; }
.diagnostic-badge text,
.event .diagnostic-badge text,
.branch-marker .diagnostic-badge text { fill: #ffffff; font-size: 9px; font-weight: 700; text-anchor: middle; paint-order: normal; stroke: none; pointer-events: none; }
.control-flow-connector { fill: none; stroke: #32613a; stroke-width: 2; opacity: 0.78; pointer-events: none; }
.control-flow-connector.is-related { stroke: #2563eb; stroke-width: 3; opacity: 1; }
#control-flow-arrow path { fill: #32613a; }
.event-inspector { margin: 0 16px 16px; padding: 10px 12px; border: 1px solid var(--vscode-panel-border, #d8dee6); }
.event-inspector dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 10px; margin: 0; }
.event-inspector dt { color: var(--vscode-descriptionForeground, #374151); font-weight: 700; }
.event-inspector dd { margin: 0; overflow-wrap: anywhere; }
.event-inspector button { margin-top: 8px; }
.branch-actions { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 10px; }
.branch-actions-title, .branch-actions-condition, .branch-actions-status { flex-basis: 100%; font-size: 12px; }
.branch-actions-title { font-weight: 700; color: var(--vscode-foreground, #17202a); }
.branch-actions-condition { color: var(--vscode-descriptionForeground, #374151); }
.branch-actions-status { color: var(--vscode-descriptionForeground, #4b5563); }
.event.confidence-symbolic > rect { fill: #f7d774; stroke-dasharray: 5 3; }
.event.confidence-assumed > rect { fill: #b8c2cc; stroke-dasharray: 4 3; }
.event.confidence-unknown > rect { fill: #c9ced6; stroke-dasharray: 2 2; }
.event.confidence-runtime_dependent > rect { fill: #e5a0a0; stroke-dasharray: 6 2; }
.kind-play rect { fill: #8bcf9a; }
.kind-wait rect { fill: #cdbb96; }
.kind-acquire rect { fill: #e2b36f; }
.kind-wait_sync rect, .kind-wait_trigger rect { fill: #b9a7dc; }
.kind-upd_param rect, .kind-upd_thres rect { fill: #bfd87a; }
.kind-marker_state rect { fill: #8bcf9a; }
.kind-feedback_pop rect, .kind-feedback_com rect,
.kind-fb_acq_iq_id rect, .kind-fb_acq_iq_shift rect,
.kind-fb_acq_tb_id rect, .kind-fb_acq_tb_cfg rect, .kind-fb_acq_tb_valid rect, .kind-fb_acq_tb_extra rect,
.kind-fb_llp_tags_id rect, .kind-fb_llp_ttls_id rect,
.kind-fb_tdc_tags_id rect, .kind-fb_tdc_tdelta_id rect,
.kind-fb_com_cfg rect, .kind-fb_com_extra rect { fill: #cf9bd8; }
.kind-loop_block rect { fill: #aacd88; stroke-width: 2; }
.kind-control_flow_loop rect { fill: #aacd88; stroke: #32613a; stroke-width: 2; }
.kind-control_flow_branch rect { fill: #fde68a; stroke: #b45309; stroke-width: 2; stroke-dasharray: 7 4; }
.branch-path-taken rect { fill: #9dd8b3; stroke: #23754a; stroke-width: 2; }
.branch-path-fallthrough rect { fill: #f4c17d; stroke: #9a5b17; stroke-width: 2; }
.kind-loop_iteration_preview rect { fill: #cfe4bc; stroke-dasharray: 4 2; }
.kind-unknown_region rect { fill: #aab2bd; stroke-dasharray: 2 2; }
.kind-underflow_warning rect { fill: #e26d6d; }
.kind-analysis_incomplete rect { fill: #8a8f98; }
.status-definite_underflow rect { fill: #c72c2c; }
.status-possible_underflow rect { fill: #f08c3a; stroke-dasharray: 6 2; }
.status-analysis_incomplete rect { fill: #8a8f98; stroke-dasharray: 5 3; }
.status-not_detected_under_current_assumptions rect { fill: #9bb7a5; }
.time-cursor { stroke: #dc2626; stroke-width: 1; pointer-events: none; }
.feedback-flow { fill: none; stroke: #d28be2; stroke-width: 1.8; stroke-dasharray: 5 3; pointer-events: stroke; }
#feedback-arrow path { fill: #d28be2; }
.feedback-flow-label { fill: var(--vscode-foreground, #17202a); font-size: 10px; font-weight: 700; paint-order: normal; stroke: none; stroke-width: 0; }
.feedback-flow-group.has-diagnostic .feedback-flow { stroke: #d97706; stroke-width: 2.7; stroke-dasharray: 7 3; opacity: 0.98; }
.feedback-flow-group.diagnostic-error .feedback-flow { stroke: #dc2626; stroke-width: 3; }
.feedback-flow-group.has-diagnostic .feedback-flow-label { fill: #a45516; font-weight: 800; }
.feedback-flow-group.diagnostic-error .feedback-flow-label { fill: #b91c1c; }
body.vscode-dark .lane-rule, body.vscode-dark .grid { stroke: #353941; }
body.vscode-dark main { background: #1e1e1e !important; background-color: #1e1e1e !important; }
body.vscode-dark .event rect { stroke: rgba(0, 0, 0, 0.38); fill: #cdbb96; }
body.vscode-dark .event.diff-shifted rect { stroke: #d28be2; }
body.vscode-dark .event.confidence-assumed > rect { fill: #aab2bd; }
body.vscode-dark .event.confidence-unknown > rect { fill: #aab2bd; }
body.vscode-dark .kind-play rect { fill: #8bcf9a; }
body.vscode-dark .kind-wait rect { fill: #cdbb96; }
body.vscode-dark .kind-wait_sync rect, body.vscode-dark .kind-wait_trigger rect { fill: #b9a7dc; }
body.vscode-dark .kind-acquire rect { fill: #e2b36f; }
body.vscode-dark .kind-upd_param rect, body.vscode-dark .kind-upd_thres rect { fill: #bfd87a; }
body.vscode-dark .kind-marker_state rect { fill: #8bcf9a; }
body.vscode-dark .kind-feedback_pop rect, body.vscode-dark .kind-feedback_com rect,
body.vscode-dark .kind-fb_acq_iq_id rect, body.vscode-dark .kind-fb_acq_iq_shift rect,
body.vscode-dark .kind-fb_acq_tb_id rect, body.vscode-dark .kind-fb_acq_tb_cfg rect, body.vscode-dark .kind-fb_acq_tb_valid rect, body.vscode-dark .kind-fb_acq_tb_extra rect,
body.vscode-dark .kind-fb_llp_tags_id rect, body.vscode-dark .kind-fb_llp_ttls_id rect,
body.vscode-dark .kind-fb_tdc_tags_id rect, body.vscode-dark .kind-fb_tdc_tdelta_id rect,
body.vscode-dark .kind-fb_com_cfg rect, body.vscode-dark .kind-fb_com_extra rect { fill: #cf9bd8; }
body.vscode-dark .kind-loop_block rect { fill: #aacd88; }
body.vscode-dark .kind-loop_iteration_preview rect { fill: #cfe4bc; }
body.vscode-dark .kind-unknown_region rect { fill: #aab2bd; }
body.mode-normal .debug-lane { display: none; }
body.mode-debug .debug-lane { display: inline; }
[data-lane-role="q1-issue"] { display: none; }
.q1-issue-lane { display: none; }
[data-lane-role="q1-issue"].q1-issue-expanded { display: inline; }
.q1-issue-lane.q1-issue-expanded { display: inline; }
body.mode-normal .event.q1-dense-collapsed { display: none; }
body.mode-normal .event.loop-collapsed { display: none; }
body.mode-normal .event.normal-feedback-collapsed { display: none; }
.event.branch-collapsed { display: none; }
.event.q1-branch-collapsed { display: none; }
"""


def _js() -> str:
    return """
const modeButtons = document.querySelectorAll('[data-mode]');
const timelineIrNode = document.getElementById('timeline-ir');
let timelineIr = { events: [] };
try {
  timelineIr = JSON.parse(timelineIrNode ? timelineIrNode.textContent : '{}');
} catch (error) {
  timelineIr = { events: [] };
}
const eventsById = new Map((timelineIr.events || []).map((event) => [String(event.id), event]));
const timelineEventSelector = '.event, .loop-bracket, .branch-marker';
const selectedTimelineEventSelector = '.event.is-selected, .loop-bracket.is-selected, .branch-marker.is-selected';
const relatedTimelineEventSelector = '.event.is-related, .loop-bracket.is-related, .branch-marker.is-related';
const cfgElementSelector = '.cfg-graph-node, .cfg-graph-edge-group, .cfg-node, .cfg-edge, .cfg-branch-path';
const selectedCfgElementSelector = '.cfg-graph-node.is-selected, .cfg-graph-edge-group.is-selected, .cfg-node.is-selected, .cfg-edge.is-selected, .cfg-branch-path.is-selected';
const relatedCfgElementSelector = '.cfg-graph-node.is-related, .cfg-graph-edge-group.is-related, .cfg-node.is-related, .cfg-edge.is-related, .cfg-branch-path.is-related';
const eventInspector = document.getElementById('event-inspector');
const inspectorFields = document.getElementById('event-inspector-fields');
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
function cfgElementNodes() {
  return querySelectorAllWithFallback(cfgElementSelector);
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
function addInspectorField(fields, label, value) {
  const displayed = displayInspectorValue(value);
  if (!displayed) {
    return;
  }
  fields.push(`<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(displayed)}</dd>`);
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
function canShowNextLoopIteration(meta) {
  const visible = loopPreviewVisibleIterations(meta);
  const total = loopPreviewTotalIterations(meta);
  const cap = loopPreviewCap(meta);
  return visible < cap && (total === undefined || visible < total);
}
function renderInspector(eventId) {
  if (!inspectorFields) {
    return;
  }
  const event = eventsById.get(String(eventId));
  if (!event) {
    inspectorFields.innerHTML = '<dt>Selected event</dt><dd>None</dd>';
    renderLoopPreviewAction(null);
    renderBranchActions(null);
    clearRelatedControlFlow();
    return;
  }
  const meta = event.meta || {};
  const source = event.source || {};
  const fields = [];
  addInspectorField(fields, 'Label', event.label || event.kind);
  addInspectorField(fields, 'Sequencer', event.sequencer_id);
  addInspectorField(fields, 'Lane', event.lane);
  addInspectorField(fields, 'Time range', `${displayInspectorValue(event.t0)} -> ${displayInspectorValue(event.t1)}`);
  addInspectorField(fields, 'Source', source.file ? `${source.file}:${source.line || 1}` : 'unavailable');
  addInspectorField(fields, 'Confidence', event.confidence);
  addInspectorField(fields, 'Q1 issue', event.kind === 'q1_issue' ? `${displayInspectorValue(event.t0)} -> ${displayInspectorValue(event.t1)}` : meta.q1_issue_event_id);
  addInspectorField(fields, 'RT packet', meta.rt_packet_id);
  addInspectorField(fields, 'Queue depth', meta.estimated_depth);
  addInspectorField(fields, 'Slack', meta.slack_ns !== undefined ? `${meta.slack_ns} ns` : '');
  addInspectorField(fields, 'Branch condition', meta.condition);
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
  renderBranchActions(event);
}
function findEventNodeById(eventId) {
  const matches = [];
  for (const node of document.querySelectorAll('[data-event-id]')) {
    if (node.dataset.eventId === String(eventId)) {
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
  if (!previewEventIds.length && !meta.loop_preview_key) {
    return;
  }
  const group = document.createElement('div');
  group.id = 'q1timeline-loop-preview-actions';
  group.className = 'loop-preview-actions';
  if (previewEventIds.length) {
    const button = document.createElement('button');
    button.id = 'q1timeline-open-loop-preview';
    button.type = 'button';
    button.textContent = 'Open first iteration';
    button.dataset.previewEventIds = JSON.stringify(previewEventIds);
    button.addEventListener('click', () => {
      const previewNode = findEventNodeById(previewEventIds[0]);
      if (!previewNode) {
        return;
      }
      selectEventNode(previewNode);
      previewNode.scrollIntoView({ block: 'center', inline: 'center' });
    });
    group.append(button);
  }
  if (meta.loop_preview_key) {
    const nextVisibleIterations = loopPreviewVisibleIterations(meta) + 1;
    const nextButton = document.createElement('button');
    nextButton.id = 'q1timeline-show-next-loop-iteration';
    nextButton.type = 'button';
    nextButton.textContent = 'Show next iteration';
    nextButton.disabled = !canShowNextLoopIteration(meta);
    if (nextButton.disabled) {
      nextButton.title = 'All available preview iterations are visible.';
    }
    nextButton.addEventListener('click', () => {
      window.dispatchEvent(new CustomEvent('q1timeline:setLoopPreviewDepth', {
        detail: { loopKey: meta.loop_preview_key, visibleIterations: nextVisibleIterations }
      }));
    });
    group.append(nextButton);
  }
  eventInspector.append(group);
}
function renderBranchActions(event) {
  const existing = document.getElementById('q1timeline-branch-actions');
  if (existing) {
    existing.remove();
  }
  const branchId = event && event.meta && event.meta.branch_id;
  if (!eventInspector || !branchId) {
    return;
  }
  const meta = event.meta || {};
  const group = document.createElement('div');
  group.id = 'q1timeline-branch-actions';
  group.className = 'branch-actions';
  const title = document.createElement('div');
  title.className = 'branch-actions-title';
  title.textContent = 'Branch decision';
  group.append(title);
  const condition = document.createElement('div');
  condition.className = 'branch-actions-condition';
  condition.textContent = meta.condition ? `Condition: ${meta.condition}` : 'Condition: runtime dependent';
  group.append(condition);
  const status = document.createElement('div');
  status.className = 'branch-actions-status';
  status.textContent = branchDecisionStatus(meta);
  group.append(status);
  const actions = [
    ['taken', 'Condition true: jump target'],
    ['fallthrough', 'Condition false: continue'],
    ['collapsed', 'Clear branch override'],
  ];
  for (const [path, label] of actions) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.addEventListener('click', () => {
      window.dispatchEvent(new CustomEvent('q1timeline:setBranchAssumption', {
        detail: { branchId: event.meta.branch_id, path }
      }));
    });
    group.append(button);
  }
  eventInspector.append(group);
}
function branchDecisionStatus(meta) {
  if (!meta || !meta.assumed_branch_path) {
    return 'Choose how to continue from this runtime-dependent branch.';
  }
  if (meta.assumed_branch_path === 'both') {
    return 'Showing true and false comparison.';
  }
  if (meta.assumed_branch_path === 'taken') {
    return 'Showing condition true: jump target.';
  }
  if (meta.assumed_branch_path === 'fallthrough') {
    return 'Showing condition false: continue.';
  }
  return 'Branch is collapsed until a path is selected.';
}
function clearRelatedControlFlow() {
  querySelectorAllWithFallback(relatedTimelineEventSelector).forEach((node) => node.classList.remove('is-related'));
  document.querySelectorAll('.control-flow-connector.is-related').forEach((node) => node.classList.remove('is-related'));
  querySelectorAllWithFallback(relatedCfgElementSelector).forEach((node) => node.classList.remove('is-related'));
}
function clearSelectedCfgElements() {
  querySelectorAllWithFallback(selectedCfgElementSelector).forEach((node) => node.classList.remove('is-selected'));
}
function cfgEventIds(node) {
  const raw = node && node.dataset ? node.dataset.cfgEventIds || '' : '';
  return raw.split(/\\s+/).map((value) => value.trim()).filter(Boolean);
}
function cfgElementMatchesEventId(node, eventId) {
  return cfgEventIds(node).includes(String(eventId));
}
function selectFirstVisibleTimelineEvent(eventIds) {
  for (const eventId of eventIds) {
    const eventNode = findEventNodeById(eventId);
    if (eventNode && isTimelineEventVisible(eventNode)) {
      selectEventNode(eventNode, { clearCfgSelection: false });
      eventNode.scrollIntoView({ block: 'center', inline: 'center' });
      return true;
    }
  }
  return false;
}
function selectCfgElement(cfgNode) {
  if (!cfgNode) {
    clearSelectedCfgElements();
    return;
  }
  clearSelectedCfgElements();
  cfgNode.classList.add('is-selected');
  const cfgNodeId = cfgNode.dataset.cfgNodeId;
  const cfgEdgeId = cfgNode.dataset.cfgEdgeId;
  for (const peer of cfgElementNodes()) {
    if (
      (cfgNodeId && peer.dataset.cfgNodeId === cfgNodeId) ||
      (cfgEdgeId && peer.dataset.cfgEdgeId === cfgEdgeId)
    ) {
      peer.classList.add('is-selected');
    }
  }
  const eventIds = cfgEventIds(cfgNode);
  clearRelatedControlFlow();
  for (const eventId of eventIds) {
    const eventNode = findEventNodeById(eventId);
    if (eventNode) {
      eventNode.classList.add('is-related');
    }
  }
  selectFirstVisibleTimelineEvent(eventIds);
}
function highlightRelatedCfgElementsForEvent(eventId) {
  if (!eventId) {
    return;
  }
  for (const cfgNode of cfgElementNodes()) {
    if (cfgElementMatchesEventId(cfgNode, eventId)) {
      cfgNode.classList.add('is-related');
    }
  }
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
  const branchId = eventNode.dataset.branchId;
  const loopId = eventNode.dataset.loopId;
  for (const node of timelineEventNodes()) {
    if (
      (eventId && node.dataset.controlFlowSourceEventId === eventId) ||
      (sourceEventId && node.dataset.eventId === sourceEventId) ||
      (branchId && node.dataset.branchId === branchId) ||
      (loopId && node.dataset.loopId === loopId) ||
      (loopId && node.dataset.loopPreviewId === loopId)
    ) {
      node.classList.add('is-related');
    }
  }
  highlightRelatedControlFlowConnectors(eventNode);
  highlightRelatedCfgElementsForEvent(eventId);
}
function selectEventNode(eventNode, options = {}) {
  const clearCfgSelection = options.clearCfgSelection !== false;
  if (!eventNode) {
    querySelectorAllWithFallback(selectedTimelineEventSelector).forEach((node) => node.classList.remove('is-selected'));
    if (clearCfgSelection) {
      clearSelectedCfgElements();
    }
    clearRelatedControlFlow();
    renderInspector(null);
    return;
  }
  if (!isTimelineEventVisible(eventNode)) {
    return;
  }
  querySelectorAllWithFallback(selectedTimelineEventSelector).forEach((node) => node.classList.remove('is-selected'));
  if (clearCfgSelection) {
    clearSelectedCfgElements();
  }
  eventNode.classList.add('is-selected');
  highlightRelatedControlFlow(eventNode);
  const eventId = eventNode.dataset.eventId;
  renderInspector(eventId);
  window.dispatchEvent(new CustomEvent('q1timeline:eventClick', { detail: { eventId } }));
}
function setMode(mode) {
  document.body.classList.toggle('mode-debug', mode === 'debug');
  document.body.classList.toggle('mode-normal', mode === 'normal');
  document.body.dataset.mode = mode;
  for (const button of modeButtons) {
    button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
  }
}
function isTimelineEventVisible(eventNode) {
  if (!eventNode) {
    return false;
  }
  if (eventNode.closest('.debug-lane') && document.body.dataset.mode === 'normal') {
    return false;
  }
  if (eventNode.classList.contains('q1-dense-collapsed') && document.body.dataset.mode === 'normal') {
    return false;
  }
  if (eventNode.classList.contains('loop-collapsed') && document.body.dataset.mode === 'normal') {
    return false;
  }
  if (eventNode.classList.contains('normal-feedback-collapsed') && document.body.dataset.mode === 'normal') {
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
const timeCursor = document.getElementById('time-cursor');
function moveTimeCursor(eventNode) {
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
if (filterInput) {
  filterInput.addEventListener('input', () => {
    const query = filterInput.value.trim().toLowerCase();
    for (const eventNode of timelineEventNodes()) {
      const searchable = eventNode.dataset.search || '';
      eventNode.classList.toggle('is-filtered', query !== '' && !searchable.includes(query));
    }
  });
}
for (const eventNode of timelineEventNodes()) {
  eventNode.addEventListener('mouseenter', () => {
    moveTimeCursor(eventNode);
  });
  eventNode.addEventListener('focus', () => {
    moveTimeCursor(eventNode);
  });
  eventNode.addEventListener('mouseleave', () => {
    hideTimeCursor();
  });
  eventNode.addEventListener('blur', () => {
    hideTimeCursor();
  });
  eventNode.addEventListener('click', () => {
    selectEventNode(eventNode);
  });
}
for (const cfgNode of cfgElementNodes()) {
  cfgNode.addEventListener('click', () => {
    selectCfgElement(cfgNode);
  });
  cfgNode.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      selectCfgElement(cfgNode);
    }
  });
}
for (const link of document.querySelectorAll('[data-related-event-id]')) {
  link.addEventListener('click', () => {
    const eventId = link.dataset.relatedEventId;
    const eventNode = findEventNodeById(eventId);
    if (eventNode) {
      selectEventNode(eventNode);
    }
  });
}
for (const link of document.querySelectorAll('[data-semantic-event-id]')) {
  link.addEventListener('click', () => {
    const eventId = link.dataset.semanticEventId;
    const eventNode = findEventNodeById(eventId);
    if (eventNode) {
      selectEventNode(eventNode);
    }
  });
}
for (const details of document.querySelectorAll('details[data-default-open="false"]')) {
  details.open = false;
}
renderInspector(null);
"""
