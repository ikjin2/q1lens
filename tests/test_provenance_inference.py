from __future__ import annotations

from types import SimpleNamespace

from qbstimeline.provenance_inference import infer_q1asm_provenance


def _program(sequencer_id: str, program: str) -> SimpleNamespace:
    return SimpleNamespace(sequencer_id=sequencer_id, program=program)


def test_infers_simple_play_provenance_from_unique_schedule_match() -> None:
    programs = [
        _program(
            "cluster0_module2_seq0",
            "wait_sync 4\nwait 20\nset_awg_gain 1,0\nplay 0,1,40\nstop\n",
        )
    ]
    blocks = [
        {
            "id": "pulse:x180:pulse:0",
            "operation_id": "x_q0",
            "schedulable_id": "x180",
            "role": "pulse",
            "abs_time": 20e-9,
            "duration": 40e-9,
            "duration_value_id": "value:t_total",
        }
    ]

    rows = infer_q1asm_provenance(blocks, programs)

    assert rows == [
        {
            "source_id": "pulse:x180:pulse:0",
            "source_kind": "pulse",
            "operation_id": "x_q0",
            "schedulable_id": "x180",
            "sequencer_id": "cluster0_module2_seq0",
            "q1asm_line_start": 3,
            "q1asm_line_end": 4,
            "instruction_roles": ["set_awg_gain", "play"],
            "operand_mappings": [
                {
                    "line": 4,
                    "instruction": "play",
                    "operand_index": 2,
                    "role": "duration",
                    "numeric_value": 40,
                    "unit": "ns",
                    "source_value_id": "value:t_total",
                }
            ],
            "confidence": "inferred",
            "inference_reason": "unique play event matched pulse time and duration",
        }
    ]


def test_infers_acquire_provenance_from_unique_schedule_match() -> None:
    programs = [
        _program(
            "cluster0_module6_seq0",
            "wait 164\nplay 0,0,160\nacquire 0,0,240\nstop\n",
        )
    ]
    blocks = [
        {
            "id": "acq:measure:acquisition:0",
            "operation_id": "measure_q0",
            "schedulable_id": "measure",
            "role": "acquisition",
            "abs_time": 324e-9,
            "duration": 240e-9,
        }
    ]

    rows = infer_q1asm_provenance(blocks, programs)

    assert rows[0]["sequencer_id"] == "cluster0_module6_seq0"
    assert rows[0]["q1asm_line_start"] == 3
    assert rows[0]["q1asm_line_end"] == 3
    assert rows[0]["instruction_roles"] == ["acquire"]
    assert rows[0]["confidence"] == "inferred"


def test_infers_acquire_provenance_by_acquisition_channel_when_times_overlap() -> None:
    programs = [
        _program("cluster0_module6_seq0", "wait 100\nacquire 0,0,240\nstop\n"),
        _program("cluster0_module6_seq1", "wait 100\nacquire 1,0,240\nstop\n"),
    ]
    blocks = [
        {
            "id": "acq:measure_q0:acquisition:0",
            "operation_id": "measure_q0",
            "schedulable_id": "measure",
            "role": "acquisition",
            "abs_time": 100e-9,
            "duration": 240e-9,
            "parameters": {"acq_channel": 0},
        },
        {
            "id": "acq:measure_q1:acquisition:0",
            "operation_id": "measure_q1",
            "schedulable_id": "measure",
            "role": "acquisition",
            "abs_time": 100e-9,
            "duration": 240e-9,
            "parameters": {"acq_channel": 1},
        },
    ]

    rows = infer_q1asm_provenance(blocks, programs)

    assert [(row["source_id"], row["sequencer_id"]) for row in rows] == [
        ("acq:measure_q0:acquisition:0", "cluster0_module6_seq0"),
        ("acq:measure_q1:acquisition:0", "cluster0_module6_seq1"),
    ]


def test_infers_acquisition_variant_provenance() -> None:
    programs = [
        _program("cluster0_module6_seq0", "wait 100\nacquire_ttl 0,0,240\nstop\n"),
    ]
    blocks = [
        {
            "id": "acq:measure:acquisition:0",
            "operation_id": "measure_q0",
            "schedulable_id": "measure",
            "role": "acquisition",
            "abs_time": 100e-9,
            "duration": 240e-9,
            "parameters": {"acq_channel": 0},
        }
    ]

    rows = infer_q1asm_provenance(blocks, programs)

    assert [(row["source_id"], row["q1asm_line_start"], row["instruction_roles"]) for row in rows] == [
        ("acq:measure:acquisition:0", 2, ["acquire_ttl"]),
    ]


def test_infers_readout_pulse_from_paired_acquisition_channel_context() -> None:
    programs = [
        _program("cluster0_module6_seq0", "wait 100\nplay 0,0,20\nacquire 0,0,240\nstop\n"),
        _program("cluster0_module6_seq1", "wait 100\nplay 0,0,20\nacquire 1,0,240\nstop\n"),
    ]
    blocks = [
        {
            "id": "pulse:measure_q0:pulse:0",
            "operation_id": "measure_q0",
            "schedulable_id": "measure_q0",
            "role": "pulse",
            "abs_time": 100e-9,
            "duration": 20e-9,
        },
        {
            "id": "acq:measure_q0:acquisition:0",
            "operation_id": "measure_q0",
            "schedulable_id": "measure_q0",
            "role": "acquisition",
            "abs_time": 120e-9,
            "duration": 240e-9,
            "parameters": {"acq_channel": 0},
        },
        {
            "id": "pulse:measure_q1:pulse:0",
            "operation_id": "measure_q1",
            "schedulable_id": "measure_q1",
            "role": "pulse",
            "abs_time": 100e-9,
            "duration": 20e-9,
        },
        {
            "id": "acq:measure_q1:acquisition:0",
            "operation_id": "measure_q1",
            "schedulable_id": "measure_q1",
            "role": "acquisition",
            "abs_time": 120e-9,
            "duration": 240e-9,
            "parameters": {"acq_channel": 1},
        },
    ]

    rows = infer_q1asm_provenance(blocks, programs)

    assert [(row["source_id"], row["sequencer_id"]) for row in rows] == [
        ("pulse:measure_q0:pulse:0", "cluster0_module6_seq0"),
        ("acq:measure_q0:acquisition:0", "cluster0_module6_seq0"),
        ("pulse:measure_q1:pulse:0", "cluster0_module6_seq1"),
        ("acq:measure_q1:acquisition:0", "cluster0_module6_seq1"),
    ]


def test_infers_split_pulse_ranges_from_qblox_scheduler_lowering() -> None:
    programs = [
        _program(
            "cluster0_module2_seq0",
            "\n".join(
                [
                    "loop4:",
                    " set_awg_offs 0,0 # setting offset for VoltageOffset",
                    " upd_param 4",
                    " wait 292 # auto generated wait (292 ns)",
                    " set_awg_offs 0,0 # setting offset for VoltageOffset",
                    " upd_param 4 # SquarePulse has too low amplitude to be played, updating parameters instead",
                    " set_awg_gain 654,0 # setting gain for RampPulse",
                    " play 0,0,4 # play RampPulse (400 ns)",
                    " wait 396 # auto generated wait (396 ns)",
                    " set_awg_offs 655,0 # setting offset for VoltageOffset",
                    " upd_param 4",
                    " wait 92 # auto generated wait (92 ns)",
                    " set_awg_offs 0,0 # setting offset for VoltageOffset",
                    " set_awg_gain 655,0 # setting gain for SquarePulse",
                    " play 1,1,4 # play SquarePulse (4 ns)",
                    " loop R0,@loop4",
                    " stop",
                ]
            ),
        )
    ]
    blocks = [
        {
            "id": "pulse:point0/square0:pulse:0",
            "operation_id": "square0_op",
            "schedulable_id": "point0/square0",
            "role": "pulse",
            "kind": "square",
            "abs_time": 0.0,
            "duration": 300e-9,
        },
        {
            "id": "pulse:point0/ramp:pulse:0",
            "operation_id": "ramp_op",
            "schedulable_id": "point0/ramp",
            "role": "pulse",
            "kind": "ramp",
            "abs_time": 300e-9,
            "duration": 400e-9,
            "duration_value_id": "value:ramp_duration",
        },
        {
            "id": "pulse:point0/square1:pulse:0",
            "operation_id": "square1_op",
            "schedulable_id": "point0/square1",
            "role": "pulse",
            "kind": "square",
            "abs_time": 700e-9,
            "duration": 100e-9,
        },
    ]

    rows = infer_q1asm_provenance(blocks, programs)

    assert [(row["source_id"], row["q1asm_line_start"], row["q1asm_line_end"]) for row in rows] == [
        ("pulse:point0/square0:pulse:0", 2, 6),
        ("pulse:point0/ramp:pulse:0", 7, 9),
        ("pulse:point0/square1:pulse:0", 10, 15),
    ]
    assert rows[0]["instruction_roles"] == [
        "set_awg_offs",
        "upd_param",
        "wait",
        "set_awg_offs",
        "upd_param",
    ]
    assert rows[1]["instruction_roles"] == ["set_awg_gain", "play", "wait"]
    assert rows[2]["instruction_roles"] == [
        "set_awg_offs",
        "upd_param",
        "wait",
        "set_awg_offs",
        "set_awg_gain",
        "play",
    ]
    assert {row["confidence"] for row in rows} == {"inferred"}
    ramp_row = next(row for row in rows if row["source_id"] == "pulse:point0/ramp:pulse:0")
    assert ramp_row["operand_mappings"] == [
        {
            "line": 8,
            "line_end": 9,
            "instruction": "play",
            "operand_index": 2,
            "role": "duration_range",
            "numeric_value": 400,
            "unit": "ns",
            "source_value_id": "value:ramp_duration",
        }
    ]


def test_infers_symbolic_amplitude_operand_from_setup_gain() -> None:
    rows = infer_q1asm_provenance(
        [
            {
                "id": "pulse:x180:pulse:0",
                "operation_id": "x_q0",
                "schedulable_id": "x180",
                "role": "pulse",
                "kind": "DRAGPulse",
                "abs_time": 20e-9,
                "duration": 40e-9,
                "parameters": {"amp": 0.32},
                "parameter_value_ids": {"amp": "value:amp_x"},
            }
        ],
        [
            _program(
                "cluster0_module2_seq0",
                "wait 20\nset_awg_gain 3277,0 # setting gain for DRAGPulse\nplay 0,1,40 # play DRAGPulse\nstop\n",
            )
        ],
    )

    assert rows[0]["operand_mappings"] == [
        {
            "line": 2,
            "instruction": "set_awg_gain",
            "operand_index": 0,
            "role": "amplitude",
            "numeric_value": 0.32,
            "unit": None,
            "source_value_id": "value:amp_x",
        }
    ]


def test_infers_symbolic_offset_operands_from_setup_offsets() -> None:
    rows = infer_q1asm_provenance(
        [
            {
                "id": "pulse:offset:pulse:0",
                "operation_id": "offset_op",
                "schedulable_id": "offset",
                "role": "pulse",
                "kind": "VoltageOffset",
                "abs_time": 0.0,
                "duration": 300e-9,
                "parameters": {"offset_path_0": 0.1, "offset_path_1": -0.2},
                "parameter_value_ids": {
                    "offset_path_0": "value:offset0",
                    "offset_path_1": "value:offset1",
                },
            }
        ],
        [
            _program(
                "cluster0_module2_seq0",
                "set_awg_offs 123,-246 # setting offset for VoltageOffset\nupd_param 4 # VoltageOffset\nwait 296\nstop\n",
            )
        ],
    )

    assert rows[0]["operand_mappings"] == [
        {
            "line": 1,
            "instruction": "set_awg_offs",
            "operand_index": 0,
            "role": "offset",
            "numeric_value": 0.1,
            "unit": None,
            "source_value_id": "value:offset0",
        },
        {
            "line": 1,
            "instruction": "set_awg_offs",
            "operand_index": 1,
            "role": "offset",
            "numeric_value": -0.2,
            "unit": None,
            "source_value_id": "value:offset1",
        },
    ]


def test_infers_exact_upd_param_duration_operand_from_lowered_pulse() -> None:
    rows = infer_q1asm_provenance(
        [
            {
                "id": "pulse:square:pulse:0",
                "operation_id": "square_op",
                "schedulable_id": "square",
                "role": "pulse",
                "kind": "SquarePulse",
                "abs_time": 0.0,
                "duration": 10e-9,
                "duration_value_id": "value:t_square",
            }
        ],
        [
            _program(
                "cluster0_module2_seq0",
                "upd_param 10 # SquarePulse has too low amplitude to be played\nstop\n",
            )
        ],
    )

    assert rows[0]["operand_mappings"] == [
        {
            "line": 1,
            "instruction": "upd_param",
            "operand_index": 0,
            "role": "duration",
            "numeric_value": 10,
            "unit": "ns",
            "source_value_id": "value:t_square",
        }
    ]


def test_infers_first_iteration_when_split_pulse_pattern_repeats_in_one_sequencer() -> None:
    first_iteration = "\n".join(
        [
            " set_awg_offs 0,0",
            " upd_param 4",
            " wait 292",
            " upd_param 4 # SquarePulse has too low amplitude to be played, updating parameters instead",
            " set_awg_gain 654,0 # setting gain for RampPulse",
            " play 0,0,4 # play RampPulse (400 ns)",
            " wait 396",
            " upd_param 4",
            " wait 92",
            " set_awg_gain 655,0 # setting gain for SquarePulse",
            " play 1,1,4 # play SquarePulse (4 ns)",
        ]
    )
    programs = [_program("cluster0_module2_seq0", f"{first_iteration}\n{first_iteration}\nstop\n")]
    blocks = [
        {
            "id": "pulse:point0/square0:pulse:0",
            "operation_id": "square0_op",
            "schedulable_id": "point0/square0",
            "role": "pulse",
            "kind": "square",
            "abs_time": 0.0,
            "duration": 300e-9,
        },
        {
            "id": "pulse:point0/ramp:pulse:0",
            "operation_id": "ramp_op",
            "schedulable_id": "point0/ramp",
            "role": "pulse",
            "kind": "ramp",
            "abs_time": 300e-9,
            "duration": 400e-9,
        },
        {
            "id": "pulse:point0/square1:pulse:0",
            "operation_id": "square1_op",
            "schedulable_id": "point0/square1",
            "role": "pulse",
            "kind": "square",
            "abs_time": 700e-9,
            "duration": 100e-9,
        },
    ]

    rows = infer_q1asm_provenance(blocks, programs)

    assert [(row["q1asm_line_start"], row["q1asm_line_end"]) for row in rows] == [
        (1, 4),
        (5, 7),
        (8, 11),
    ]


def test_inference_does_not_reuse_one_q1asm_instruction_for_multiple_blocks() -> None:
    programs = [_program("cluster0_module2_seq0", "wait 20\nplay 0,1,40\nstop\n")]
    blocks = [
        {
            "id": "pulse:x0:pulse:0",
            "operation_id": "x0_op",
            "schedulable_id": "x0",
            "role": "pulse",
            "abs_time": 20e-9,
            "duration": 40e-9,
        },
        {
            "id": "pulse:x1:pulse:0",
            "operation_id": "x1_op",
            "schedulable_id": "x1",
            "role": "pulse",
            "abs_time": 20e-9,
            "duration": 40e-9,
        },
    ]

    rows = infer_q1asm_provenance(blocks, programs)

    assert [(row["source_id"], row["q1asm_line_start"], row["q1asm_line_end"]) for row in rows] == [
        ("pulse:x0:pulse:0", 2, 2),
    ]


def test_omits_ambiguous_matches_across_sequencers() -> None:
    programs = [
        _program("cluster0_module2_seq0", "wait 20\nplay 0,1,40\nstop\n"),
        _program("cluster0_module2_seq1", "wait 20\nplay 0,1,40\nstop\n"),
    ]
    blocks = [
        {
            "id": "pulse:x180:pulse:0",
            "operation_id": "x_q0",
            "schedulable_id": "x180",
            "role": "pulse",
            "abs_time": 20e-9,
            "duration": 40e-9,
        }
    ]

    assert infer_q1asm_provenance(blocks, programs) == []


def test_infers_split_pulse_ranges_independently_across_sequencers() -> None:
    programs = [
        _program(
            "cluster0_module2_seq0",
            "\n".join(
                [
                    "set_awg_gain 1,0 # setting gain for RampPulse",
                    "play 0,0,4 # play RampPulse (400 ns)",
                    "wait 396",
                    "stop",
                ]
            ),
        ),
        _program(
            "cluster0_module4_seq0",
            "\n".join(
                [
                    "set_awg_gain 1,0 # setting gain for SquarePulse",
                    "play 0,0,4 # play SquarePulse (100 ns)",
                    "wait 96",
                    "stop",
                ]
            ),
        ),
    ]
    blocks = [
        {
            "id": "pulse:ramp:pulse:0",
            "operation_id": "ramp_op",
            "schedulable_id": "ramp",
            "role": "pulse",
            "kind": "ramp",
            "abs_time": 0.0,
            "duration": 400e-9,
        },
        {
            "id": "pulse:square:pulse:0",
            "operation_id": "square_op",
            "schedulable_id": "square",
            "role": "pulse",
            "kind": "square",
            "abs_time": 0.0,
            "duration": 100e-9,
        },
    ]

    rows = infer_q1asm_provenance(blocks, programs)

    assert [(row["source_id"], row["sequencer_id"], row["q1asm_line_start"], row["q1asm_line_end"]) for row in rows] == [
        ("pulse:ramp:pulse:0", "cluster0_module2_seq0", 1, 3),
        ("pulse:square:pulse:0", "cluster0_module4_seq0", 1, 3),
    ]


def test_split_lowering_range_does_not_match_wrong_absolute_start() -> None:
    programs = [
        _program(
            "cluster0_module2_seq0",
            "\n".join(
                [
                    "wait 100",
                    "set_awg_gain 1,0 # setting gain for RampPulse",
                    "play 0,0,4 # play RampPulse (400 ns)",
                    "wait 396",
                    "stop",
                ]
            ),
        )
    ]
    blocks = [
        {
            "id": "pulse:ramp:pulse:0",
            "operation_id": "ramp_op",
            "schedulable_id": "ramp",
            "role": "pulse",
            "kind": "ramp",
            "abs_time": 0.0,
            "duration": 400e-9,
        },
    ]

    assert infer_q1asm_provenance(blocks, programs) == []


def test_multi_block_split_lowering_sequence_uses_absolute_start_time() -> None:
    programs = [
        _program(
            "cluster0_module2_seq0",
            "\n".join(
                [
                    "upd_param 4 # SquarePulse low amplitude lowering",
                    "wait 6",
                    "upd_param 4 # SquarePulse low amplitude lowering",
                    "wait 6",
                    "wait 80",
                    "upd_param 4 # SquarePulse low amplitude lowering",
                    "wait 6",
                    "upd_param 4 # SquarePulse low amplitude lowering",
                    "wait 6",
                    "stop",
                ]
            ),
        )
    ]
    blocks = [
        {
            "id": "pulse:square0:pulse:0",
            "operation_id": "square0_op",
            "schedulable_id": "square0",
            "role": "pulse",
            "kind": "square",
            "abs_time": 100e-9,
            "duration": 10e-9,
        },
        {
            "id": "pulse:square1:pulse:0",
            "operation_id": "square1_op",
            "schedulable_id": "square1",
            "role": "pulse",
            "kind": "square",
            "abs_time": 110e-9,
            "duration": 10e-9,
        },
    ]

    rows = infer_q1asm_provenance(blocks, programs)

    assert [(row["source_id"], row["q1asm_line_start"], row["q1asm_line_end"]) for row in rows] == [
        ("pulse:square0:pulse:0", 6, 7),
        ("pulse:square1:pulse:0", 8, 9),
    ]
