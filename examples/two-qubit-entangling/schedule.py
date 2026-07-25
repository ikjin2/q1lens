from __future__ import annotations

from qbstimeline import annotate, sym
from qbstimeline.provenance import ProvenanceRecorder


T_X90 = sym.time("T_X90", 20e-9)
T_CZ = sym.time("T_CZ", 80e-9)
READOUT_DURATION = sym.time("READOUT_DURATION", 240e-9)
AMP_X90 = sym.amp("AMP_X90", 0.21)
AMP_CZ = sym.amp("AMP_CZ", 0.18)


class ComplexTableData:
    def to_dict(self, orient: str) -> list[dict]:
        if orient != "records":
            raise ValueError("ComplexTableData only supports orient='records'")
        return [
            _timing("Reset(q0)", "q0:mw", "q0.01", 0.0, 20e-9),
            _timing("Reset(q1)", "q1:mw", "q1.01", 0.0, 20e-9),
            _timing("X90(q0)", "q0:mw", "q0.01", 20e-9, 20e-9),
            _timing("X90(q1)", "q1:mw", "q1.01", 20e-9, 20e-9),
            _timing("CZ(q0,q1)", "q0_q1:flux", "cz", 44e-9, 88e-9),
            _timing("X90(q0) echo", "q0:mw", "q0.01", 132e-9, 20e-9),
            _timing("Measure(q0)", "q0:res", "q0.ro", 164e-9, 400e-9, is_acquisition=True),
            _timing("Measure(q1)", "q1:res", "q1.ro", 164e-9, 400e-9, is_acquisition=True),
        ]


class ComplexTimingTable:
    data = ComplexTableData()


class ComplexCompiledSchedule(dict):
    @property
    def timing_table(self) -> ComplexTimingTable:
        return ComplexTimingTable()


class ComplexCompiler:
    def compile(self, schedule: dict) -> dict:
        provenance = ProvenanceRecorder()
        provenance.record_emission(
            source_id="pulse:x90_q0:pulse:0",
            source_kind="pulse",
            schedulable_id="x90_q0",
            sequencer_id="cluster0_module2_seq0",
            q1asm_line_start=3,
            q1asm_line_end=4,
            instruction_roles=["set_awg_gain", "play"],
            operand_mappings=[
                {
                    "line": 4,
                    "instruction": "play",
                    "operand_index": 2,
                    "role": "trigger_duration",
                    "numeric_value": 20,
                    "unit": "ns",
                    "source_value_id": "value:t_x90",
                    "source_expression": "T_X90",
                }
            ],
        )
        provenance.record_emission(
            source_id="pulse:cz_q0_q1:pulse:0",
            source_kind="pulse",
            schedulable_id="cz_q0_q1",
            sequencer_id="cluster0_module4_seq0",
            q1asm_line_start=4,
            q1asm_line_end=7,
            instruction_roles=["set_awg_gain", "play", "wait"],
            operand_mappings=[
                {
                    "line": 5,
                    "instruction": "play",
                    "operand_index": 2,
                    "role": "flux_settle",
                    "numeric_value": 8,
                    "unit": "ns",
                },
                {
                    "line": 6,
                    "instruction": "wait",
                    "operand_index": 0,
                    "role": "interaction_duration",
                    "numeric_value": 72,
                    "unit": "ns",
                    "source_value_id": "value:t_cz",
                    "source_expression": "T_CZ - 8 ns setup",
                },
            ],
        )
        provenance.record_emission(
            source_id="acq:measure_q0:acquisition:0",
            source_kind="acquisition",
            schedulable_id="measure_q0",
            sequencer_id="cluster0_module6_seq0",
            q1asm_line_start=4,
            q1asm_line_end=4,
            instruction_roles=["acquire"],
            operand_mappings=[
                {
                    "line": 4,
                    "instruction": "acquire",
                    "operand_index": 2,
                    "role": "integration_duration",
                    "numeric_value": 240,
                    "unit": "ns",
                    "source_value_id": "value:readout_duration",
                    "source_expression": "READOUT_DURATION",
                }
            ],
        )
        return ComplexCompiledSchedule(
            {
                "name": schedule["name"],
                "schedulables": {
                    "reset_q0": {"operation_id": "reset_q0", "abs_time": 0.0},
                    "reset_q1": {"operation_id": "reset_q1", "abs_time": 0.0},
                    "x90_q0": {"operation_id": "x90_q0", "abs_time": 20e-9},
                    "x90_q1": {"operation_id": "x90_q1", "abs_time": 20e-9},
                    "cz_q0_q1": {"operation_id": "cz_q0_q1", "abs_time": 44e-9},
                    "echo_q0": {"operation_id": "echo_q0", "abs_time": 132e-9},
                    "measure_q0": {"operation_id": "measure_q0", "abs_time": 164e-9},
                    "measure_q1": {"operation_id": "measure_q1", "abs_time": 164e-9},
                },
                "operations": {
                    "reset_q0": {"name": "Reset(q0)", "duration": 20e-9},
                    "reset_q1": {"name": "Reset(q1)", "duration": 20e-9},
                    "x90_q0": _x90_operation("X90(q0)", "q0:mw", "q0.01", 0.21, 0.0),
                    "x90_q1": _x90_operation("X90(q1)", "q1:mw", "q1.01", 0.19, 90.0),
                    "cz_q0_q1": _cz_operation(),
                    "echo_q0": _x90_operation("X90(q0) echo", "q0:mw", "q0.01", -0.21, 180.0),
                    "measure_q0": _measure_operation("Measure(q0)", "q0:res", "q0.ro", 0, 0.24),
                    "measure_q1": _measure_operation("Measure(q1)", "q1:res", "q1.ro", 1, 0.23),
                },
                "compiled_instructions": {
                    "cluster0": {
                        "module2": {
                            "sequencers": {
                                "seq0": {
                                    "sequence": {
                                        "program": (
                                            "wait_sync 4\n"
                                            "wait 20\n"
                                            "set_awg_gain 17203,0\n"
                                            "play 0,1,20\n"
                                            "wait 92\n"
                                            "set_awg_gain -17203,0\n"
                                            "play 2,3,20\n"
                                            "stop\n"
                                        )
                                    }
                                },
                                "seq1": {
                                    "sequence": {
                                        "program": (
                                            "wait_sync 4\n"
                                            "wait 20\n"
                                            "set_awg_gain 15565,0\n"
                                            "play 0,1,20\n"
                                            "stop\n"
                                        )
                                    }
                                },
                            }
                        },
                        "module4": {
                            "sequencers": {
                                "seq0": {
                                    "sequence": {
                                        "program": (
                                            "wait_sync 4\n"
                                            "wait 44\n"
                                            "upd_param 4\n"
                                            "set_awg_gain 14746,0\n"
                                            "play 0,0,8\n"
                                            "wait 72\n"
                                            "upd_param 4\n"
                                            "stop\n"
                                        )
                                    }
                                }
                            }
                        },
                        "module6": {
                            "sequencers": {
                                "seq0": {
                                    "sequence": {
                                        "program": (
                                            "wait_sync 4\n"
                                            "wait 164\n"
                                            "play 0,0,160\n"
                                            "acquire 0,0,240\n"
                                            "stop\n"
                                        )
                                    }
                                },
                                "seq1": {
                                    "sequence": {
                                        "program": (
                                            "wait_sync 4\n"
                                            "wait 164\n"
                                            "play 0,0,160\n"
                                            "acquire 1,0,240\n"
                                            "stop\n"
                                        )
                                    }
                                },
                            }
                        },
                    }
                },
                "qbstimeline_provenance": provenance,
            }
        )


def build_schedule() -> dict:
    return {"name": "two-qubit entangling demo"}


def build_compiler() -> ComplexCompiler:
    return ComplexCompiler()


def _timing(
    operation: str,
    port: str,
    clock: str,
    abs_time: float,
    duration: float,
    *,
    is_acquisition: bool = False,
) -> dict:
    return {
        "operation": operation,
        "port": port,
        "clock": clock,
        "abs_time": abs_time,
        "duration": duration,
        "is_acquisition": is_acquisition,
    }


def _x90_operation(label: str, port: str, clock: str, amp: float, phase: float) -> dict:
    return annotate(
        {
            "name": label,
            "duration": 20e-9,
            "pulse_info": [
                {
                    "name": "DRAGPulse",
                    "port": port,
                    "clock": clock,
                    "t0": 0.0,
                    "duration": 20e-9,
                    "amp": amp,
                    "phase": phase,
                    "sigma": 4e-9,
                }
            ],
        },
        duration=T_X90,
        amp=AMP_X90,
    )


def _cz_operation() -> dict:
    return annotate(
        {
            "name": "CZ(q0,q1)",
            "duration": 88e-9,
            "pulse_info": [
                {
                    "name": "CZFluxPulse",
                    "port": "q0_q1:flux",
                    "clock": "cz",
                    "t0": 4e-9,
                    "duration": 80e-9,
                    "amp": 0.18,
                    "detuning": -220e6,
                    "rise_time": 8e-9,
                }
            ],
        },
        duration=T_CZ,
        amp=AMP_CZ,
    )


def _measure_operation(label: str, port: str, clock: str, channel: int, amp: float) -> dict:
    return annotate(
        {
            "name": label,
            "duration": 400e-9,
            "pulse_info": [
                {
                    "name": "SquarePulse",
                    "port": port,
                    "clock": clock,
                    "t0": 0.0,
                    "duration": 160e-9,
                    "amp": amp,
                }
            ],
            "acquisition_info": [
                {
                    "protocol": "SSBIntegrationComplex",
                    "port": port,
                    "clock": clock,
                    "t0": 160e-9,
                    "duration": 240e-9,
                    "acq_channel": channel,
                }
            ],
        },
        duration=READOUT_DURATION,
    )
