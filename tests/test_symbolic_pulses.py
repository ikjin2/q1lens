from __future__ import annotations

from types import SimpleNamespace

from qbstimeline import SymbolicValue, annotate, sym
from qbstimeline.extract.symbolic_pulses import extract_symbolic_pulse_layer


def test_extracts_pulses_and_acquisitions_from_schedulables() -> None:
    t_total = sym.time("T_TOTAL", 40e-9)
    amp_x = sym.amp("AMP_X", 0.32)
    x_operation = annotate(
        {
            "name": "X(q0)",
            "pulse_info": [
                {
                    "name": "DRAGPulse",
                    "port": "q0:mw",
                    "clock": "q0.01",
                    "t0": 4e-9,
                    "duration": 40e-9,
                    "amp": 0.32,
                    "phase": 0.0,
                }
            ],
        },
        duration=t_total,
        amp=amp_x,
    )
    measure_operation = {
        "name": "Measure(q0)",
        "pulse_info": [
            {
                "name": "SquarePulse",
                "port": "q0:res",
                "clock": "q0.ro",
                "t0": 0.0,
                "duration": 160e-9,
                "amp": 0.25,
            }
        ],
        "acquisition_info": [
            {
                "protocol": "SSBIntegrationComplex",
                "port": "q0:res",
                "clock": "q0.ro",
                "t0": 160e-9,
                "duration": 300e-9,
                "acq_channel": 0,
            }
        ],
    }
    compiled_schedule = SimpleNamespace(
        schedulables={
            "x180": {"operation_id": "x_q0", "abs_time": 20e-9},
            "measure": {"operation_id": "measure_q0", "abs_time": 60e-9},
        },
        operations={"x_q0": x_operation, "measure_q0": measure_operation},
    )

    layer = extract_symbolic_pulse_layer(compiled_schedule)

    assert layer["symbolic_values"] == [
        {
            "id": "value:t_total",
            "label": "T_TOTAL",
            "value": 40e-9,
            "unit": "s",
            "kind": "duration",
        },
        {
            "id": "value:amp_x",
            "label": "AMP_X",
            "value": 0.32,
            "unit": None,
            "kind": "amplitude",
        },
    ]
    assert layer["symbolic_pulses"][0] == {
        "id": "pulse:x180:pulse:0",
        "operation_id": "x_q0",
        "schedulable_id": "x180",
        "kind": "DRAGPulse",
        "display_label": "X(q0)",
        "display_subtitle": "DRAGPulse q0:mw | 40 ns | amp 0.32 | phase 0",
        "role": "pulse",
        "port": "q0:mw",
        "clock": "q0.01",
        "lane": "q0:mw / q0.01",
        "abs_time": 24e-9,
        "duration": 40e-9,
        "duration_value_id": "value:t_total",
        "parameter_value_ids": {"amp": "value:amp_x"},
        "parameters": {"amp": 0.32, "phase": 0.0},
    }
    assert layer["symbolic_pulses"][1]["id"] == "pulse:measure:pulse:0"
    assert layer["symbolic_pulses"][1]["display_label"] == "Measure(q0)"
    assert layer["symbolic_pulses"][1]["display_subtitle"] == "SquarePulse q0:res | 160 ns | amp 0.25"
    assert layer["symbolic_pulses"][2]["id"] == "acq:measure:acquisition:0"
    assert layer["symbolic_pulses"][2]["role"] == "acquisition"
    assert layer["symbolic_pulses"][2]["kind"] == "SSBIntegrationComplex"
    assert layer["symbolic_pulses"][2]["display_label"] == "Measure(q0)"
    assert layer["symbolic_pulses"][2]["display_subtitle"] == "SSBIntegrationComplex q0:res | 300 ns | acq_channel 0"


def test_duration_value_id_uses_semantic_duration_annotation_key() -> None:
    readout_pulse = sym.time("READOUT_PULSE", 160e-9)
    operation = annotate(
        {
            "name": "Measure(q0)",
            "pulse_info": [
                {
                    "name": "SquarePulse",
                    "port": "q0:res",
                    "clock": "q0.ro",
                    "t0": 0.0,
                    "duration": 160e-9,
                }
            ],
        },
        readout_pulse=readout_pulse,
    )
    compiled_schedule = SimpleNamespace(
        schedulables={"measure": {"operation_id": "measure_q0", "abs_time": 0.0}},
        operations={"measure_q0": operation},
    )

    layer = extract_symbolic_pulse_layer(compiled_schedule)

    assert layer["symbolic_pulses"][0]["duration_value_id"] == "value:readout_pulse"


def test_same_valued_parameter_annotation_does_not_cross_attach() -> None:
    offset0 = SymbolicValue(id="value:offset0", label="OFFSET0", value=0.0, unit=None, kind="offset")
    operation = annotate(
        {
            "name": "Offset",
            "pulse_info": [
                {
                    "name": "VoltageOffset",
                    "port": "q0:flux",
                    "clock": "cl0.baseband",
                    "duration": 300e-9,
                    "offset_path_0": 0.0,
                    "offset_path_1": 0.0,
                }
            ],
        },
        offset_path_0=offset0,
    )
    compiled_schedule = SimpleNamespace(
        schedulables={"offset": {"operation_id": "offset_op", "abs_time": 0.0}},
        operations={"offset_op": operation},
    )

    layer = extract_symbolic_pulse_layer(compiled_schedule)

    assert layer["symbolic_pulses"][0]["parameter_value_ids"] == {"offset_path_0": "value:offset0"}


def test_source_order_timing_advances_by_delayed_pulse_extent() -> None:
    compiled_schedule = SimpleNamespace(
        schedulables={
            "op0": {"operation_id": "op0"},
            "op1": {"operation_id": "op1"},
        },
        operations={
            "op0": {
                "name": "Delayed",
                "pulse_info": [
                    {
                        "name": "SquarePulse",
                        "port": "q0:mw",
                        "clock": "q0.01",
                        "t0": 10e-9,
                        "duration": 20e-9,
                    }
                ],
            },
            "op1": {
                "name": "Next",
                "pulse_info": [
                    {
                        "name": "SquarePulse",
                        "port": "q0:mw",
                        "clock": "q0.01",
                        "duration": 20e-9,
                    }
                ],
            },
        },
    )

    layer = extract_symbolic_pulse_layer(compiled_schedule)

    assert [pulse["abs_time"] for pulse in layer["symbolic_pulses"]] == [10e-9, 30e-9]


def test_generated_uuid_schedulable_paths_get_human_display_labels() -> None:
    schedulable_id = (
        "74b81819-088c-43e3-b5da-7a2c301bdd55/"
        "0499843c-d624-4373-a07e-24aa8072b460/"
        "46193053-ad19-4ccc-812b-7e4ee9ae2eb9"
    )
    operation_id = "c572b503-2439-4eca-aefa-0a1d93272733"
    compiled_schedule = SimpleNamespace(
        schedulables={schedulable_id: {"operation_id": operation_id, "abs_time": 0.0}},
        operations={
            operation_id: {
                "pulse_info": [
                    {
                        "name": "SquarePulse",
                        "port": "q0:mw",
                        "clock": "q0.01",
                        "duration": 20e-9,
                        "amp": 0.5,
                    }
                ],
            }
        },
    )

    layer = extract_symbolic_pulse_layer(compiled_schedule)

    pulse = layer["symbolic_pulses"][0]
    assert pulse["id"] == f"pulse:{schedulable_id}:pulse:0"
    assert pulse["display_label"] == "SquarePulse q0:mw"
    assert pulse["display_subtitle"] == "20 ns | amp 0.5"
    assert "74b81819" not in pulse["display_label"]


def test_missing_port_and_clock_use_stable_fallback_lane() -> None:
    compiled_schedule = SimpleNamespace(
        schedulables={"unknown0": {"operation_id": "op0", "abs_time": 1e-9}},
        operations={
            "op0": {
                "name": "UnknownPulse",
                "pulse_info": [{"name": "CustomPulse", "duration": 8e-9}],
            }
        },
    )

    layer = extract_symbolic_pulse_layer(compiled_schedule)

    assert layer["symbolic_pulses"][0]["lane"] == "unassigned / no_clock"
    assert layer["symbolic_pulses"][0]["port"] is None
    assert layer["symbolic_pulses"][0]["clock"] is None


def test_extracts_only_first_iteration_pulses_from_control_flow_body() -> None:
    loop_body = SimpleNamespace(
        schedulables={"drive": {"operation_id": "drive_pulse", "abs_time": 5e-9}},
        operations={
            "drive_pulse": {
                "name": "SquarePulse",
                "pulse_info": [
                    {
                        "name": "SquarePulse",
                        "port": "q0:mw",
                        "clock": "q0.01",
                        "t0": 2e-9,
                        "duration": 20e-9,
                    }
                ],
            }
        },
    )
    compiled_schedule = SimpleNamespace(
        schedulables={"loop": {"operation_id": "loop_operation", "abs_time": 10e-9}},
        operations={
            "loop_operation": {
                "name": "LoopOperation",
                "control_flow_info": {
                    "body": loop_body,
                    "repetitions": 3,
                    "t0": 1e-9,
                },
            }
        },
    )

    layer = extract_symbolic_pulse_layer(compiled_schedule)

    assert [pulse["id"] for pulse in layer["symbolic_pulses"]] == ["pulse:loop/drive:pulse:0"]
    assert layer["symbolic_pulses"][0]["abs_time"] == 18e-9
    assert layer["symbolic_pulses"][0]["parent_control_flow_id"] == "control-flow:loop"
    assert layer["symbolic_pulses"][0]["depth"] == 1


def test_operation_body_without_control_flow_metadata_is_not_marked_as_loop_pulse() -> None:
    pulse_compensation_body = SimpleNamespace(
        schedulables={"measure": {"operation_id": "measure_op", "abs_time": 5e-9}},
        operations={
            "measure_op": {
                "name": "Measure cs0",
                "pulse_info": [
                    {
                        "name": "SquarePulse",
                        "port": "cs0:gt",
                        "clock": "cs0.baseband",
                        "duration": 800e-9,
                    }
                ],
            }
        },
    )

    class PulseCompensationLike:
        body = pulse_compensation_body
        data = {"name": "PulseCompensation"}

    compiled_schedule = SimpleNamespace(
        schedulables={"pc": {"operation_id": "pulse_compensation", "abs_time": 0.0}},
        operations={"pulse_compensation": PulseCompensationLike()},
    )

    layer = extract_symbolic_pulse_layer(compiled_schedule)

    assert [pulse["id"] for pulse in layer["symbolic_pulses"]] == ["pulse:pc/measure:pulse:0"]
    assert "parent_control_flow_id" not in layer["symbolic_pulses"][0]
    assert "depth" not in layer["symbolic_pulses"][0]


def test_large_repeated_nested_operation_pulses_are_compacted_as_manual_sweep() -> None:
    nested_body = SimpleNamespace(
        schedulables={"measure": {"operation_id": "measure_op", "abs_time": 5e-9}},
        operations={
            "measure_op": {
                "name": "Measure cs0",
                "pulse_info": [
                    {
                        "name": "SquarePulse",
                        "port": "cs0:gt",
                        "clock": "cs0.baseband",
                        "duration": 800e-9,
                    }
                ],
            }
        },
    )

    class PulseCompensationLike:
        body = nested_body
        data = {"name": "PulseCompensation"}

    schedulables = {f"pc{index}": {"operation_id": f"pulse_compensation_{index}", "abs_time": 0.0} for index in range(60)}
    operations = {f"pulse_compensation_{index}": PulseCompensationLike() for index in range(60)}
    compiled_schedule = SimpleNamespace(schedulables=schedulables, operations=operations)

    layer = extract_symbolic_pulse_layer(compiled_schedule)

    assert [pulse["id"] for pulse in layer["symbolic_pulses"]] == ["pulse:pc0/measure:pulse:0"]
    assert layer["symbolic_pulses"][0]["parent_control_flow_id"] == "control-flow:pc0"
    assert layer["symbolic_pulses"][0]["depth"] == 1


def test_repeated_nested_schedule_pulses_use_source_order_timing_and_bracket_parent() -> None:
    nested_body = SimpleNamespace(
        schedulables={
            "square0": {"operation_id": "square0_op"},
            "ramp": {"operation_id": "ramp_op"},
            "square1": {"operation_id": "square1_op"},
        },
        operations={
            "square0_op": {
                "name": "SquarePulse",
                "pulse_info": {
                    "name": "SquarePulse",
                    "port": "q0:gt",
                    "clock": "cl0.baseband",
                    "duration": 300e-9,
                },
            },
            "ramp_op": {
                "name": "RampPulse",
                "pulse_info": {
                    "name": "RampPulse",
                    "port": "q0:gt",
                    "clock": "cl0.baseband",
                    "duration": 400e-9,
                },
            },
            "square1_op": {
                "name": "SquarePulse",
                "pulse_info": {
                    "name": "SquarePulse",
                    "port": "q0:gt",
                    "clock": "cl0.baseband",
                    "duration": 100e-9,
                },
            },
        },
    )

    def pulse_sequence() -> SimpleNamespace:
        return SimpleNamespace(name="pulse_sequence", schedulables=nested_body.schedulables, operations=nested_body.operations)

    schedule = SimpleNamespace(
        repetitions=1_000_000,
        schedulables={
            f"point{index}": {"operation_id": f"pulse_sequence_{index}"}
            for index in range(10)
        },
        operations={f"pulse_sequence_{index}": pulse_sequence() for index in range(10)},
    )

    layer = extract_symbolic_pulse_layer(schedule)

    assert [pulse["kind"] for pulse in layer["symbolic_pulses"]] == ["SquarePulse", "RampPulse", "SquarePulse"]
    assert [pulse["abs_time"] for pulse in layer["symbolic_pulses"]] == [0.0, 300e-9, 700e-9]
    assert {pulse["parent_control_flow_id"] for pulse in layer["symbolic_pulses"]} == {"control-flow:point0"}
    assert {pulse["depth"] for pulse in layer["symbolic_pulses"]} == {2}


def test_distinct_abs_time_repeated_nested_schedule_pulses_are_not_compacted() -> None:
    nested_body = SimpleNamespace(
        schedulables={"measure": {"operation_id": "measure_op", "abs_time": 0.0}},
        operations={
            "measure_op": {
                "name": "Measure cs0",
                "pulse_info": {
                    "name": "SquarePulse",
                    "port": "cs0:gt",
                    "clock": "cs0.baseband",
                    "duration": 100e-9,
                },
            }
        },
    )

    def pulse_sequence() -> SimpleNamespace:
        return SimpleNamespace(name="pulse_sequence", schedulables=nested_body.schedulables, operations=nested_body.operations)

    schedule = SimpleNamespace(
        schedulables={
            "point0": {"operation_id": "pulse_sequence_0", "abs_time": 0.0},
            "point1": {"operation_id": "pulse_sequence_1", "abs_time": 1e-6},
        },
        operations={"pulse_sequence_0": pulse_sequence(), "pulse_sequence_1": pulse_sequence()},
    )

    layer = extract_symbolic_pulse_layer(schedule)

    assert [pulse["id"] for pulse in layer["symbolic_pulses"]] == [
        "pulse:point0/measure:pulse:0",
        "pulse:point1/measure:pulse:0",
    ]
    assert [pulse["abs_time"] for pulse in layer["symbolic_pulses"]] == [0.0, 1e-6]
    assert all("parent_control_flow_id" not in pulse for pulse in layer["symbolic_pulses"])


def test_extracts_pulses_from_experiment_wrapped_schedule() -> None:
    loop_body = SimpleNamespace(
        schedulables={"drive": {"operation_id": "drive_pulse", "abs_time": 5e-9}},
        operations={
            "drive_pulse": {
                "name": "SquarePulse",
                "pulse_info": [
                    {
                        "name": "SquarePulse",
                        "port": "q0:mw",
                        "clock": "q0.01",
                        "duration": 20e-9,
                    }
                ],
            }
        },
    )

    class LoopOperationLike:
        body = loop_body
        data = {
            "name": "LoopOperation",
            "control_flow_info": {
                "body": loop_body,
                "repetitions": 3,
                "t0": 0.0,
            },
        }

    nested_schedule = SimpleNamespace(
        schedulables={"loop": {"operation_id": "loop_operation", "abs_time": 0.0}},
        operations={"loop_operation": LoopOperationLike()},
    )

    class ExperimentWrappedSchedule:
        _experiments = [{"steps": [{"schedule_info": {"schedule": nested_schedule}}]}]

        @property
        def schedulables(self):
            raise RuntimeError("unavailable")

        @property
        def operations(self):
            raise RuntimeError("unavailable")

    layer = extract_symbolic_pulse_layer(ExperimentWrappedSchedule())

    assert [pulse["id"] for pulse in layer["symbolic_pulses"]] == [
        "pulse:experiment0/loop/drive:pulse:0"
    ]
    assert layer["symbolic_pulses"][0]["parent_control_flow_id"] == "control-flow:experiment0/loop"


def test_filters_large_waveform_payloads_from_parameters() -> None:
    compiled_schedule = SimpleNamespace(
        schedulables={"x180": {"operation_id": "x_q0", "abs_time": 0.0}},
        operations={
            "x_q0": {
                "name": "X(q0)",
                "pulse_info": [
                    {
                        "name": "DRAGPulse",
                        "duration": 20e-9,
                        "amp": 0.3,
                        "data": [0.0, 1.0],
                        "weights": [0.1] * 16,
                        "short_list": [1, 2, 3],
                    }
                ],
            }
        },
    )

    layer = extract_symbolic_pulse_layer(compiled_schedule)

    assert layer["symbolic_pulses"][0]["parameters"] == {
        "amp": 0.3,
        "short_list": [1, 2, 3],
    }
