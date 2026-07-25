from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


class RenderError(ValueError):
    """Raised when QBS IR cannot be rendered."""


def render_ir_file(ir: dict[str, Any], out: str | Path) -> None:
    output_path = Path(out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_ir_to_html(ir), encoding="utf-8")


def render_ir_path(ir_path: str | Path, out: str | Path) -> None:
    try:
        ir = json.loads(Path(ir_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RenderError(f"Could not read QBS IR: {ir_path}") from exc
    if not isinstance(ir, dict):
        raise RenderError("QBS IR must be a JSON object")
    render_ir_file(ir, out)


def render_ir_to_html(ir: dict[str, Any]) -> str:
    schedule = ir.get("schedule") if isinstance(ir.get("schedule"), dict) else {}
    schedule_name = str(schedule.get("name", "schedule"))
    status = str(ir.get("status", "unknown"))
    error_message = ir.get("error")
    error_html = _render_error(error_message if isinstance(error_message, str) else "")
    operations = _list(ir.get("operations"))
    control_flow_blocks = _list(ir.get("control_flow_blocks"))
    timing_rows = _list(ir.get("timing_table"))
    symbolic_values = _list(ir.get("symbolic_values"))
    symbolic_pulses = _list(ir.get("symbolic_pulses"))
    q1asm_provenance = _list(ir.get("q1asm_provenance"))
    q1asm_programs = _list(ir.get("q1asm_programs"))
    q1asm_by_sequencer = ir.get("q1asm_by_sequencer")
    if not isinstance(q1asm_by_sequencer, dict):
        q1asm_by_sequencer = {}

    data_json = _safe_json(
        {
            "operations": operations,
            "controlFlowBlocks": control_flow_blocks,
            "symbolicValues": symbolic_values,
            "symbolicPulses": symbolic_pulses,
            "q1asmProvenance": q1asm_provenance,
            "q1asmPrograms": q1asm_programs,
            "q1asmBySequencer": q1asm_by_sequencer,
        }
    )
    first_program = _first_q1asm(q1asm_programs, q1asm_by_sequencer)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(schedule_name)} - Q1Lens</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fa;
      --surface: #ffffff;
      --surface-2: #f0f3f7;
      --text: #18202b;
      --muted: #657184;
      --line: #d8dee8;
      --accent: #0f766e;
      --accent-soft: #d9f3ef;
      --code-bg: #111827;
      --code-text: #e5e7eb;
      --radius: 8px;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); }}
    .qbs-timeline-app {{ min-height: 100vh; display: flex; flex-direction: column; }}
    header {{
      display: flex; align-items: center; justify-content: space-between; gap: 16px;
      padding: 18px 24px; border-bottom: 1px solid var(--line); background: var(--surface);
    }}
    h1 {{ margin: 0; font-size: 20px; font-weight: 650; letter-spacing: 0; }}
    .status {{ display: inline-flex; align-items: center; gap: 8px; font-size: 13px; color: var(--muted); }}
    .status strong {{ color: var(--accent); text-transform: uppercase; font-size: 12px; letter-spacing: .04em; }}
    .compile-error {{ margin: 12px 16px 0; padding: 10px 12px; border: 1px solid #f2b8b5; border-radius: var(--radius); background: #fff4f3; color: #8a1f17; font-size: 13px; }}
    main {{ display: grid; grid-template-columns: minmax(260px, 360px) minmax(360px, 1fr); gap: 16px; padding: 16px; }}
    section {{ background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }}
    section h2 {{ margin: 0; padding: 12px 14px; font-size: 13px; font-weight: 650; border-bottom: 1px solid var(--line); }}
    .stack {{ display: grid; gap: 12px; }}
    .operation-list {{ display: grid; gap: 8px; padding: 12px; }}
    .operation-button {{
      width: 100%; text-align: left; background: var(--surface); color: var(--text);
      border: 1px solid var(--line); border-radius: 7px; padding: 10px 11px; cursor: pointer;
      font: inherit; display: grid; gap: 4px;
    }}
    .operation-button:hover, .operation-button.is-selected {{ border-color: var(--accent); background: var(--accent-soft); }}
    .op-label {{ font-size: 14px; font-weight: 650; }}
    .op-meta {{ font-size: 12px; color: var(--muted); }}
    .control-flow-timeline {{ display: grid; gap: 10px; padding: 12px; border-bottom: 1px solid var(--line); background: var(--surface-2); }}
    .control-flow-title {{ color: var(--muted); font-size: 11px; font-weight: 650; text-transform: uppercase; }}
    .loop-row {{ display: grid; grid-template-columns: minmax(120px, 180px) 1fr; gap: 10px; align-items: start; }}
    .loop-label {{ color: var(--muted); font-size: 12px; padding-top: 6px; overflow-wrap: anywhere; }}
    .loop-track {{ display: grid; gap: 7px; min-width: 0; }}
    .loop-bracket {{
      min-height: 30px; padding: 5px 8px 3px; border: 2px solid var(--accent); border-bottom: 0;
      background: var(--surface); color: var(--text); font-size: 12px; font-weight: 650;
    }}
    .loop-body {{ padding-left: 10px; border-left: 2px solid var(--accent-soft); color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }}
    .pulse-timeline {{ display: grid; gap: 10px; padding: 12px; }}
    .pulse-lane {{ display: grid; grid-template-columns: minmax(120px, 180px) 1fr; gap: 10px; align-items: stretch; }}
    .pulse-lane-label {{ font-size: 12px; color: var(--muted); padding-top: 8px; overflow-wrap: anywhere; }}
    .pulse-lane-track {{ min-height: 44px; border-left: 2px solid var(--line); padding-left: 10px; display: flex; flex-wrap: wrap; gap: 8px; }}
    .pulse-block {{ min-width: 160px; max-width: 260px; border: 1px solid var(--line); border-radius: 7px; background: var(--surface); color: var(--text); padding: 8px 10px; text-align: left; display: grid; gap: 3px; font: inherit; }}
    .pulse-block:hover, .pulse-block.is-selected {{ border-color: var(--accent); background: var(--accent-soft); }}
    .pulse-kind {{ font-size: 13px; font-weight: 650; }}
    .pulse-meta {{ font-size: 11px; color: var(--muted); overflow-wrap: anywhere; }}
    .detail {{ padding: 12px; font-size: 13px; color: var(--muted); border-top: 1px solid var(--line); }}
    .content-grid {{ display: grid; grid-template-rows: auto minmax(280px, 1fr); gap: 16px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ padding: 9px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 650; background: var(--surface-2); }}
    .sequencer-bar {{ display: flex; flex-wrap: wrap; gap: 8px; padding: 12px; border-bottom: 1px solid var(--line); }}
    .sequencer-button {{
      border: 1px solid var(--line); background: var(--surface); border-radius: 999px; padding: 7px 10px;
      font-size: 12px; color: var(--text); cursor: pointer;
    }}
    .sequencer-button:hover, .sequencer-button.is-selected {{ border-color: var(--accent); color: var(--accent); }}
    pre {{ margin: 0; padding: 14px; min-height: 280px; overflow: auto; background: var(--code-bg); color: var(--code-text); font-size: 12px; line-height: 1.55; }}
    @media (max-width: 900px) {{ main {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class="qbs-timeline-app">
    <header>
      <h1>{html.escape(schedule_name)}</h1>
      <div class="status">Compile status <strong>{html.escape(status)}</strong></div>
    </header>
    {error_html}
    <main>
      <div class="stack">
        <section>
          <h2>High-level schedule blocks</h2>
          {_render_control_flow_timeline(control_flow_blocks, operations)}
          <div class="operation-list">
            {_render_operation_buttons(operations, symbolic_pulses)}
          </div>
          <div class="detail" id="operation-detail">Select an operation block to inspect its metadata.</div>
        </section>
      </div>
      <div class="content-grid">
        <section>
          <h2>Pulse timing table</h2>
          {_render_timing_table(timing_rows)}
        </section>
        <section>
          <h2>Symbolic pulse timeline</h2>
          {_render_symbolic_pulse_timeline(symbolic_pulses, symbolic_values, q1asm_provenance)}
        </section>
        <section>
          <h2>Sequencer Q1ASM</h2>
          <div class="sequencer-bar">{_render_sequencer_buttons(q1asm_programs)}</div>
          <pre id="q1asm-panel">{html.escape(first_program)}</pre>
        </section>
      </div>
    </main>
  </div>
  <script>
    const QBS_DATA = {data_json};
    let selectedSequencer = QBS_DATA.q1asmPrograms[0]?.sequencer_id || "";

    function formatTime(value) {{
      if (typeof value !== "number") return "n/a";
      return `${{(value * 1e9).toFixed(1)}} ns`;
    }}

    function selectOperation(index) {{
      const operation = QBS_DATA.operations[index];
      if (!operation) return;
      document.querySelectorAll(".operation-button").forEach((button) => button.classList.remove("is-selected"));
      const operationButton = document.querySelector(`[data-operation-index="${{index}}"]`);
      operationButton?.classList.add("is-selected");
      const pulseIds = JSON.parse(operationButton?.dataset.symbolicPulseIds || "[]");
      highlightSymbolicPulses(pulseIds);
      const operationDetail = document.getElementById("operation-detail");
      const detailLabel = document.createElement("strong");
      detailLabel.textContent = operation.label || operation.operation_id || "n/a";
      const operationIdLine = document.createElement("div");
      operationIdLine.textContent = `operation_id: ${{operation.operation_id || "n/a"}}`;
      const timingLine = document.createElement("div");
      timingLine.textContent = `start: ${{formatTime(operation.abs_time)}} - duration: ${{formatTime(operation.duration)}}`;
      const mappingLine = document.createElement("div");
      mappingLine.textContent = "Q1ASM mapping: all sequencer programs shown until provenance is available.";
      operationDetail.replaceChildren(detailLabel, operationIdLine, timingLine, mappingLine);
    }}

    function highlightSymbolicPulses(ids) {{
      const selected = new Set(ids);
      document.querySelectorAll(".pulse-block").forEach((button) => {{
        button.classList.toggle("is-selected", selected.has(button.dataset.symbolicPulseId));
      }});
    }}

    function selectSequencer(id) {{
      selectedSequencer = id;
      document.querySelectorAll(".sequencer-button").forEach((button) => button.classList.remove("is-selected"));
      document.querySelector(`[data-sequencer-id="${{CSS.escape(id)}}"]`)?.classList.add("is-selected");
      document.getElementById("q1asm-panel").textContent = QBS_DATA.q1asmBySequencer[id] || "";
    }}

    if (selectedSequencer) selectSequencer(selectedSequencer);
  </script>
</body>
</html>
"""


def _render_operation_buttons(operations: list[Any], symbolic_pulses: list[Any]) -> str:
    if not operations:
        return '<div class="detail">No operations found.</div>'
    rows = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            continue
        label = html.escape(str(operation.get("label", operation.get("operation_id", "operation"))))
        start = html.escape(_format_seconds(operation.get("abs_time")))
        duration = html.escape(_format_seconds(operation.get("duration")))
        pulse_ids = _symbolic_pulse_ids_for_operation(operation, symbolic_pulses)
        pulse_ids_attr = html.escape(json.dumps(pulse_ids), quote=True)
        rows.append(
            f'<button class="operation-button" data-operation-index="{index}" '
            f'data-symbolic-pulse-ids="{pulse_ids_attr}" onclick="selectOperation({index})">'
            f'<span class="op-label">{label}</span>'
            f'<span class="op-meta">start {start} - duration {duration}</span>'
            "</button>"
        )
    return "\n".join(rows)


def _render_control_flow_timeline(blocks: list[Any], operations: list[Any]) -> str:
    block_rows = [block for block in blocks if isinstance(block, dict)]
    if not block_rows:
        return ""
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        parent_id = operation.get("parent_control_flow_id")
        if isinstance(parent_id, str):
            children_by_parent.setdefault(parent_id, []).append(operation)

    rows = []
    for block in block_rows:
        block_id = str(block.get("id", ""))
        label = str(block.get("label", "Loop"))
        duration = _format_seconds(block.get("duration"))
        children = children_by_parent.get(block_id, [])
        child_labels = ", ".join(
            str(child.get("label", child.get("operation_id", "operation")))
            for child in children
        )
        if not child_labels:
            child_labels = f'{block.get("body_operation_count", 0)} operation(s)'
        rows.append(
            '<div class="loop-row">'
            f'<div class="loop-label">{html.escape(label)}</div>'
            '<div class="loop-track">'
            f'<div class="loop-bracket">{html.escape(label)} - {html.escape(duration)}</div>'
            f'<div class="loop-body">Loop body: {html.escape(child_labels)}</div>'
            "</div>"
            "</div>"
        )
    return (
        '<div class="control-flow-timeline" aria-label="Control-flow brackets">'
        '<div class="control-flow-title">Control-flow brackets</div>'
        + "".join(rows)
        + "</div>"
    )


def _symbolic_pulse_ids_for_operation(operation: dict[str, Any], symbolic_pulses: list[Any]) -> list[str]:
    operation_id = operation.get("operation_id")
    schedulable_id = operation.get("id")
    ids: list[str] = []
    for pulse in symbolic_pulses:
        if not isinstance(pulse, dict):
            continue
        if pulse.get("operation_id") == operation_id or pulse.get("schedulable_id") == schedulable_id:
            pulse_id = pulse.get("id")
            if pulse_id is not None:
                ids.append(str(pulse_id))
    return ids


def _render_symbolic_pulse_timeline(
    pulses: list[Any],
    values: list[Any],
    provenance: list[Any],
) -> str:
    if not pulses:
        return '<div class="detail">No symbolic pulse blocks found.</div>'
    value_by_id = {
        value.get("id"): value for value in values if isinstance(value, dict)
    }
    provenance_by_source = {
        row.get("source_id"): row for row in provenance if isinstance(row, dict)
    }
    lanes: dict[str, list[dict[str, Any]]] = {}
    for pulse in pulses:
        if isinstance(pulse, dict):
            lanes.setdefault(str(pulse.get("lane", "unassigned / no_clock")), []).append(pulse)
    rows = []
    for lane, lane_pulses in lanes.items():
        blocks = "".join(
            _render_symbolic_pulse_block(pulse, value_by_id, provenance_by_source)
            for pulse in lane_pulses
        )
        rows.append(
            f'<div class="pulse-lane"><div class="pulse-lane-label">{html.escape(lane)}</div>'
            f'<div class="pulse-lane-track">{blocks}</div></div>'
        )
    return '<div class="pulse-timeline">' + "".join(rows) + "</div>"


def _render_symbolic_pulse_block(
    pulse: dict[str, Any],
    value_by_id: dict[Any, dict[str, Any]],
    provenance_by_source: dict[Any, dict[str, Any]],
) -> str:
    pulse_id = str(pulse.get("id", ""))
    label = str(pulse.get("display_label") or pulse.get("label") or pulse.get("kind", "Pulse"))
    duration_label = _symbolic_duration_label(pulse, value_by_id)
    subtitle = str(pulse.get("display_subtitle") or "")
    parameters = pulse.get("parameters")
    parameter_label = _parameter_label(parameters if isinstance(parameters, dict) else {})
    provenance = provenance_by_source.get(pulse_id)
    provenance_label = _provenance_label(provenance if isinstance(provenance, dict) else {})
    meta_label = subtitle or duration_label
    return (
        f'<button class="pulse-block" data-symbolic-pulse-id="{html.escape(pulse_id)}">'
        f'<span class="pulse-kind">{html.escape(label)}</span>'
        f'<span class="pulse-meta">{html.escape(meta_label)}</span>'
        f'<span class="pulse-meta">{html.escape(parameter_label if not subtitle else "")}</span>'
        f'<span class="pulse-meta">{html.escape(provenance_label)}</span>'
        "</button>"
    )


def _symbolic_duration_label(pulse: dict[str, Any], value_by_id: dict[Any, dict[str, Any]]) -> str:
    value_id = pulse.get("duration_value_id")
    symbolic_value = value_by_id.get(value_id)
    if isinstance(symbolic_value, dict):
        return f'{symbolic_value.get("label")} = {_format_seconds(symbolic_value.get("value"))}'
    return _format_seconds(pulse.get("duration"))


def _parameter_label(parameters: dict[str, Any]) -> str:
    parts = [f"{key}={value}" for key, value in sorted(parameters.items())]
    return " | ".join(parts)


def _provenance_label(provenance: dict[str, Any]) -> str:
    mappings = provenance.get("operand_mappings", [])
    if not isinstance(mappings, list):
        return ""
    expressions = [
        str(item.get("source_expression"))
        for item in mappings
        if isinstance(item, dict) and item.get("source_expression")
    ]
    return " | ".join(expressions)


def _render_error(message: str) -> str:
    if not message:
        return ""
    return f'<div class="compile-error">{html.escape(message)}</div>'


def _render_timing_table(rows: list[Any]) -> str:
    if not rows:
        return '<div class="detail">No timing rows found.</div>'
    headers = ["operation", "port", "clock", "abs_time", "duration", "is_acquisition"]
    body = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cells = []
        for header in headers:
            value = row.get(header, "")
            if header in {"abs_time", "duration"}:
                value = _format_seconds(value)
            cells.append(f"<td>{html.escape(str(value))}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    header_html = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    return f"<table><thead><tr>{header_html}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _render_sequencer_buttons(programs: list[Any]) -> str:
    if not programs:
        return '<span class="op-meta">No sequencer programs found.</span>'
    buttons = []
    for program in programs:
        if not isinstance(program, dict):
            continue
        sequencer_id = str(program.get("sequencer_id", "sequencer"))
        sequencer_arg = html.escape(json.dumps(sequencer_id), quote=True)
        buttons.append(
            f'<button class="sequencer-button" data-sequencer-id="{html.escape(sequencer_id)}" '
            f'onclick="selectSequencer({sequencer_arg})">{html.escape(sequencer_id)}</button>'
        )
    return "\n".join(buttons)


def _format_seconds(value: Any) -> str:
    if isinstance(value, int | float):
        return f"{value * 1e9:.1f} ns"
    return "n/a"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first_q1asm(programs: list[Any], q1asm_by_sequencer: dict[Any, Any]) -> str:
    for program in programs:
        if isinstance(program, dict):
            sequencer_id = program.get("sequencer_id")
            q1asm = q1asm_by_sequencer.get(sequencer_id)
            if isinstance(q1asm, str):
                return q1asm
    return ""


def _safe_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False).replace("</", "<\\/")
