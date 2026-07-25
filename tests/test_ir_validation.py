from __future__ import annotations

from qbstimeline.ir.validation import IrDiagnostic, validate_qbs_ir


def valid_ir() -> dict:
    return {
        "version": "0.1.0",
        "status": "ok",
        "operations": [
            {
                "id": "x90",
                "operation_id": "x_q0",
                "label": "X90(q0)",
                "abs_time": 10e-9,
                "duration": 40e-9,
            }
        ],
        "control_flow_blocks": [],
        "timing_table": [],
        "symbolic_values": [
            {
                "id": "value:t_x90",
                "label": "T_X90",
                "value": 40e-9,
                "unit": "s",
                "kind": "duration",
            }
        ],
        "symbolic_pulses": [
            {
                "id": "pulse:x90:pulse:0",
                "operation_id": "x_q0",
                "schedulable_id": "x90",
                "kind": "DRAGPulse",
                "role": "pulse",
                "lane": "q0:mw / q0.01",
                "port": "q0:mw",
                "clock": "q0.01",
                "abs_time": 12e-9,
                "duration": 20e-9,
                "duration_value_id": "value:t_x90",
            }
        ],
        "q1asm_programs": [
            {
                "sequencer_id": "cluster0_module2_seq0",
                "file": "q1asm/cluster0_module2_seq0.q1asm",
                "path": ["cluster0", "module2", "seq0"],
            }
        ],
        "q1asm_by_sequencer": {
            "cluster0_module2_seq0": "set_awg_gain 1,1\nplay 0,1,20\nstop\n",
        },
        "q1asm_provenance": [
            {
                "source_id": "pulse:x90:pulse:0",
                "source_kind": "pulse",
                "schedulable_id": "x90",
                "operation_id": "x_q0",
                "sequencer_id": "cluster0_module2_seq0",
                "q1asm_line_start": 1,
                "q1asm_line_end": 2,
                "operand_mappings": [
                    {
                        "line": 2,
                        "instruction": "play",
                        "operand_index": 2,
                        "role": "duration",
                        "numeric_value": 20,
                        "unit": "ns",
                    }
                ],
            }
        ],
        "ir_diagnostics": [],
    }


def codes(ir: dict) -> list[str]:
    return [diagnostic.code for diagnostic in validate_qbs_ir(ir)]


def test_valid_ir_has_no_invariant_diagnostics() -> None:
    assert validate_qbs_ir(valid_ir()) == []


def test_ir_diagnostic_serializes_to_stable_ir_shape() -> None:
    diagnostic = IrDiagnostic(
        code="duplicate_id",
        path="symbolic_pulses",
        message="id appears twice",
        severity="error",
    )

    assert diagnostic.to_ir() == {
        "code": "duplicate_id",
        "path": "symbolic_pulses",
        "message": "id appears twice",
        "severity": "error",
    }


def test_reports_duplicate_ids() -> None:
    ir = valid_ir()
    ir["symbolic_pulses"].append(dict(ir["symbolic_pulses"][0]))

    assert "duplicate_id" in codes(ir)


def test_reports_broken_symbolic_references() -> None:
    ir = valid_ir()
    ir["symbolic_pulses"][0]["schedulable_id"] = "missing"
    ir["symbolic_pulses"][0]["duration_value_id"] = "value:missing"

    diagnostic_codes = codes(ir)

    assert "missing_operation_reference" in diagnostic_codes
    assert "missing_symbolic_value_reference" in diagnostic_codes


def test_reports_symbolic_pulse_outside_operation_window() -> None:
    ir = valid_ir()
    ir["symbolic_pulses"][0]["abs_time"] = 60e-9

    assert "pulse_outside_operation_window" in codes(ir)


def test_reports_invalid_q1asm_ranges_and_operand_mappings() -> None:
    ir = valid_ir()
    ir["q1asm_provenance"][0]["q1asm_line_start"] = 2
    ir["q1asm_provenance"][0]["q1asm_line_end"] = 5
    ir["q1asm_provenance"][0]["operand_mappings"][0]["line"] = 1

    diagnostic_codes = codes(ir)

    assert "q1asm_line_range_out_of_bounds" in diagnostic_codes
    assert "operand_mapping_outside_provenance_range" in diagnostic_codes


def test_reports_control_flow_parent_and_depth_mismatch() -> None:
    ir = valid_ir()
    ir["control_flow_blocks"] = [
        {
            "id": "control-flow:loop",
            "kind": "loop",
            "label": "Loop x3",
            "abs_time": 0.0,
            "duration": 100e-9,
            "operation_id": "loop_op",
            "schedulable_id": "loop",
            "repetitions": 3,
            "body_operation_count": 1,
        }
    ]
    ir["operations"][0]["parent_control_flow_id"] = "control-flow:missing"
    ir["operations"][0]["depth"] = 1

    assert "missing_control_flow_parent" in codes(ir)

    ir["operations"][0]["parent_control_flow_id"] = "control-flow:loop"
    ir["operations"][0]["depth"] = 3

    assert "control_flow_depth_mismatch" in codes(ir)
