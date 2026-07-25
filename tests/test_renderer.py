from __future__ import annotations

from pathlib import Path

from qbstimeline.render.html import render_ir_file, render_ir_to_html


def _sample_ir() -> dict:
    return {
        "version": "0.1.0",
        "status": "ok",
        "schedule": {"name": "basic transmon demo"},
        "operations": [
            {
                "id": "x180",
                "operation_id": "x_q0",
                "label": "X(q0)",
                "abs_time": 20e-9,
                "duration": 40e-9,
            }
        ],
        "timing_table": [
            {
                "operation": "X(q0)",
                "port": "q0:mw",
                "clock": "q0.01",
                "abs_time": 20e-9,
                "duration": 40e-9,
                "is_acquisition": False,
            }
        ],
        "symbolic_values": [
            {
                "id": "value:t_total",
                "label": "T_TOTAL",
                "value": 40e-9,
                "unit": "s",
                "kind": "duration",
            }
        ],
        "symbolic_pulses": [
            {
                "id": "pulse:x180:pulse:0",
                "operation_id": "x_q0",
                "schedulable_id": "x180",
                "kind": "DRAGPulse",
                "display_label": "Drive q0",
                "display_subtitle": "DRAGPulse q0:mw | 40 ns | amp 0.32 | phase 0",
                "role": "pulse",
                "port": "q0:mw",
                "clock": "q0.01",
                "lane": "q0:mw / q0.01",
                "abs_time": 20e-9,
                "duration": 40e-9,
                "duration_value_id": "value:t_total",
                "parameters": {"amp": 0.32, "phase": 0.0},
            }
        ],
        "q1asm_provenance": [
            {
                "source_id": "pulse:x180:pulse:0",
                "source_kind": "pulse",
                "schedulable_id": "x180",
                "sequencer_id": "cluster0_module2_seq0",
                "q1asm_line_start": 2,
                "q1asm_line_end": 3,
                "operand_mappings": [
                    {
                        "line": 3,
                        "instruction": "wait",
                        "operand_index": 0,
                        "role": "remaining_duration",
                        "numeric_value": 36,
                        "unit": "ns",
                        "source_value_id": "value:t_total",
                        "source_expression": "T_TOTAL - 4 ns",
                    }
                ],
            }
        ],
        "capabilities": {
            "operations": True,
            "symbolic_pulses": True,
            "q1asm": True,
            "artifacts": False,
        },
        "warnings": [],
        "artifacts": {},
        "q1asm_programs": [
            {
                "sequencer_id": "cluster0_module2_seq0",
                "file": "q1asm/cluster0_module2_seq0.q1asm",
                "path": ["cluster0", "module2", "seq0"],
            }
        ],
        "q1asm_by_sequencer": {
            "cluster0_module2_seq0": "wait_sync 4\nplay 0,1,4\nstop\n"
        },
    }


def test_render_ir_to_html_contains_debugger_sections() -> None:
    html = render_ir_to_html(_sample_ir())

    assert "basic transmon demo" in html
    assert "Compile status" in html
    assert 'data-operation-index="0"' in html
    assert "X(q0)" in html
    assert "q0:mw" in html
    assert "cluster0_module2_seq0" in html
    assert "play 0,1,4" in html
    assert "selectOperation" in html
    assert 'onclick="selectSequencer(&quot;cluster0_module2_seq0&quot;)"' in html
    assert 'onclick="selectSequencer("cluster0_module2_seq0")"' not in html
    assert "Symbolic pulse timeline" in html
    assert "q0:mw / q0.01" in html
    assert "Drive q0" in html
    assert "DRAGPulse" in html
    assert "T_TOTAL" in html
    assert 'data-symbolic-pulse-id="pulse:x180:pulse:0"' in html
    assert 'data-symbolic-pulse-ids="[&quot;pulse:x180:pulse:0&quot;]"' in html
    assert "highlightSymbolicPulses" in html
    assert "T_TOTAL - 4 ns" in html


def test_render_ir_to_html_uses_text_nodes_for_operation_detail() -> None:
    payload = _sample_ir()
    payload["operations"][0]["label"] = '<img src=x onerror="window.__xss=1">'

    html = render_ir_to_html(payload)

    assert 'document.getElementById("operation-detail").innerHTML' not in html
    assert "detailLabel.textContent" in html
    assert "operationDetail.replaceChildren" in html


def test_render_ir_to_html_shows_loop_control_flow_blocks() -> None:
    payload = _sample_ir()
    payload["operations"] = [
        {
            "id": "loop",
            "operation_id": "loop_operation",
            "label": "LoopOperation",
            "abs_time": 0.0,
            "duration": 120e-9,
        },
        {
            "id": "loop/body0",
            "operation_id": "body_pulse",
            "label": "X(q0)",
            "abs_time": 5e-9,
            "duration": 20e-9,
            "parent_control_flow_id": "control-flow:loop",
            "depth": 1,
        },
    ]
    payload["control_flow_blocks"] = [
        {
            "id": "control-flow:loop",
            "kind": "loop",
            "label": "Loop x3",
            "abs_time": 0.0,
            "duration": 120e-9,
            "schedulable_id": "loop",
            "operation_id": "loop_operation",
            "repetitions": 3,
            "body_operation_count": 1,
        }
    ]

    html = render_ir_to_html(payload)

    assert "Control-flow brackets" in html
    assert "loop-bracket" in html
    assert "Loop x3" in html
    assert "Loop body: X(q0)" in html
    assert '"controlFlowBlocks"' in html


def test_render_ir_to_html_shows_sweep_bracket_as_control_flow() -> None:
    payload = _sample_ir()
    payload["operations"] = [
        {
            "id": "sweep",
            "operation_id": "sweep_operation",
            "label": "SweepOperation",
            "abs_time": 0.0,
            "duration": 0.0,
        },
        {
            "id": "sweep/body0",
            "operation_id": "drive_pulse",
            "label": "SquarePulse",
            "abs_time": 5e-9,
            "duration": 20e-9,
            "parent_control_flow_id": "control-flow:sweep",
            "depth": 1,
        },
    ]
    payload["control_flow_blocks"] = [
        {
            "id": "control-flow:sweep",
            "kind": "sweep",
            "label": "Sweep x100",
            "abs_time": 0.0,
            "duration": 0.0,
            "schedulable_id": "sweep",
            "operation_id": "sweep_operation",
            "repetitions": 100,
            "body_operation_count": 1,
        }
    ]

    html = render_ir_to_html(payload)

    assert "Sweep x100" in html
    assert "Loop body: SquarePulse" in html


def test_render_ir_file_writes_static_html(tmp_path: Path) -> None:
    out = tmp_path / "index.html"

    render_ir_file(_sample_ir(), out)

    html = out.read_text(encoding="utf-8")
    assert "<!doctype html>" in html
    assert "qbs-timeline-app" in html


def test_render_ir_to_html_shows_compile_error_message() -> None:
    payload = _sample_ir()
    payload["status"] = "error"
    payload["error"] = "hardware compile failed"

    html = render_ir_to_html(payload)

    assert "hardware compile failed" in html
    assert "compile-error" in html
