from __future__ import annotations

from types import SimpleNamespace

from qbstimeline.provenance import ProvenanceRecorder, normalize_q1asm_provenance


def test_provenance_recorder_emits_operand_mappings() -> None:
    recorder = ProvenanceRecorder()

    recorder.record_emission(
        source_id="pulse:x180:pulse:0",
        source_kind="pulse",
        schedulable_id="x180",
        sequencer_id="cluster0_module2_seq0",
        q1asm_line_start=12,
        q1asm_line_end=15,
        instruction_roles=["set_awg_gain", "play", "wait"],
        operand_mappings=[
            {
                "line": 14,
                "instruction": "wait",
                "operand_index": 0,
                "role": "remaining_duration",
                "numeric_value": 36,
                "unit": "ns",
                "source_value_id": "value:t_total",
                "source_expression": "T_TOTAL - 4 ns",
            }
        ],
    )

    assert recorder.to_ir() == [
        {
            "source_id": "pulse:x180:pulse:0",
            "source_kind": "pulse",
            "schedulable_id": "x180",
            "sequencer_id": "cluster0_module2_seq0",
            "q1asm_line_start": 12,
            "q1asm_line_end": 15,
            "instruction_roles": ["set_awg_gain", "play", "wait"],
            "operand_mappings": [
                {
                    "line": 14,
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
    ]


def test_normalize_q1asm_provenance_reads_schedule_sidecar() -> None:
    compiled_schedule = SimpleNamespace(
        qbstimeline_provenance=[
            {
                "source_id": "pulse:x180:pulse:0",
                "source_kind": "pulse",
                "schedulable_id": "x180",
                "sequencer_id": "cluster0_module2_seq0",
                "q1asm_line_start": 12,
                "q1asm_line_end": 15,
                "operand_mappings": [
                    {
                        "line": 14,
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
        ]
    )

    rows = normalize_q1asm_provenance(compiled_schedule)

    assert rows[0]["source_id"] == "pulse:x180:pulse:0"
    assert rows[0]["operand_mappings"][0]["source_value_id"] == "value:t_total"


def test_normalize_q1asm_provenance_accepts_sequencer_alias() -> None:
    compiled_schedule = SimpleNamespace(
        qbstimeline_provenance=[
            {
                "source_id": "pulse:x180:pulse:0",
                "source_kind": "pulse",
                "schedulable_id": "x180",
                "sequencer": "cluster0_module2_seq0",
                "q1asm_line_start": 12,
                "q1asm_line_end": 15,
            }
        ]
    )

    rows = normalize_q1asm_provenance(compiled_schedule)

    assert rows[0]["sequencer_id"] == "cluster0_module2_seq0"


def test_normalize_q1asm_provenance_preserves_operand_line_end() -> None:
    compiled_schedule = SimpleNamespace(
        qbstimeline_provenance=[
            {
                "source_id": "pulse:ramp:pulse:0",
                "source_kind": "pulse",
                "schedulable_id": "ramp",
                "sequencer_id": "cluster0_module2_seq0",
                "q1asm_line_start": 2,
                "q1asm_line_end": 4,
                "operand_mappings": [
                    {
                        "line": 3,
                        "line_end": 4,
                        "instruction": "play",
                        "operand_index": 2,
                        "role": "duration_range",
                    }
                ],
            }
        ]
    )

    rows = normalize_q1asm_provenance(compiled_schedule)

    assert rows[0]["operand_mappings"][0]["line_end"] == 4


def test_normalize_q1asm_provenance_preserves_sidecar_metadata() -> None:
    compiled_schedule = SimpleNamespace(
        qbstimeline_provenance=[
            {
                "source_id": "pulse:x180:pulse:0",
                "source_kind": "pulse",
                "operation_id": "x_q0",
                "schedulable_id": "x180",
                "sequencer_id": "cluster0_module2_seq0",
                "q1asm_line_start": 12,
                "q1asm_line_end": 15,
                "confidence": "compiler",
                "inference_reason": "provided by compiler lowering pass",
            }
        ]
    )

    rows = normalize_q1asm_provenance(compiled_schedule)

    assert rows[0]["operation_id"] == "x_q0"
    assert rows[0]["confidence"] == "compiler"
    assert rows[0]["inference_reason"] == "provided by compiler lowering pass"


def test_normalize_q1asm_provenance_skips_invalid_sidecar_rows() -> None:
    compiled_schedule = SimpleNamespace(
        qbstimeline_provenance=[
            {"source_id": "pulse:x180:pulse:0"},
            {
                "source_id": "pulse:y90:pulse:0",
                "source_kind": "pulse",
                "schedulable_id": "y90",
                "sequencer_id": "cluster0_module2_seq0",
                "q1asm_line_start": 20,
                "q1asm_line_end": 21,
            },
        ]
    )

    rows = normalize_q1asm_provenance(compiled_schedule)

    assert [row["source_id"] for row in rows] == ["pulse:y90:pulse:0"]
