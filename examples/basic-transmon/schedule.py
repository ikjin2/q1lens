from __future__ import annotations

from qbstimeline import annotate, sym
from qbstimeline.provenance import ProvenanceRecorder


T_TOTAL = sym.time("T_TOTAL", 40e-9)
AMP_X = sym.amp("AMP_X", 0.32)


class DemoTableData:
    def to_dict(self, orient: str) -> list[dict]:
        if orient != "records":
            raise ValueError("DemoTableData only supports orient='records'")
        return [
            {
                "operation": "Reset(q0)",
                "port": "q0:mw",
                "clock": "q0.01",
                "abs_time": 0.0,
                "duration": 20e-9,
                "is_acquisition": False,
            },
            {
                "operation": "X(q0)",
                "port": "q0:mw",
                "clock": "q0.01",
                "abs_time": 20e-9,
                "duration": 40e-9,
                "is_acquisition": False,
            },
            {
                "operation": "Measure(q0)",
                "port": "q0:res",
                "clock": "q0.ro",
                "abs_time": 60e-9,
                "duration": 300e-9,
                "is_acquisition": True,
            },
        ]


class DemoTimingTable:
    data = DemoTableData()


class DemoCompiledSchedule(dict):
    @property
    def timing_table(self) -> DemoTimingTable:
        return DemoTimingTable()


class DemoCompiler:
    def compile(self, schedule: dict) -> dict:
        provenance = ProvenanceRecorder()
        provenance.record_emission(
            source_id="pulse:x180:pulse:0",
            source_kind="pulse",
            schedulable_id="x180",
            sequencer_id="cluster0_module2_seq0",
            q1asm_line_start=4,
            q1asm_line_end=6,
            instruction_roles=["set_awg_gain", "play", "wait"],
            operand_mappings=[
                {
                    "line": 5,
                    "instruction": "play",
                    "operand_index": 2,
                    "role": "trigger_duration",
                    "numeric_value": 4,
                    "unit": "ns",
                },
                {
                    "line": 6,
                    "instruction": "wait",
                    "operand_index": 0,
                    "role": "remaining_duration",
                    "numeric_value": 36,
                    "unit": "ns",
                    "source_value_id": "value:t_total",
                    "source_expression": "T_TOTAL - 4 ns",
                },
            ],
        )
        return DemoCompiledSchedule({
            "name": schedule["name"],
            "schedulables": {
                "reset": {"operation_id": "reset_q0", "abs_time": 0.0},
                "x180": {"operation_id": "x_q0", "abs_time": 20e-9},
                "measure": {"operation_id": "measure_q0", "abs_time": 60e-9},
            },
            "operations": {
                "reset_q0": {"name": "Reset(q0)", "duration": 20e-9},
                "x_q0": annotate(
                    {
                        "name": "X(q0)",
                        "duration": 40e-9,
                        "pulse_info": [
                            {
                                "name": "DRAGPulse",
                                "port": "q0:mw",
                                "clock": "q0.01",
                                "t0": 0.0,
                                "duration": 40e-9,
                                "amp": 0.32,
                                "phase": 0.0,
                            }
                        ],
                    },
                    duration=T_TOTAL,
                    amp=AMP_X,
                ),
                "measure_q0": {
                    "name": "Measure(q0)",
                    "duration": 300e-9,
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
                },
            },
            "compiled_instructions": {
                "cluster0": {
                    "module2": {
                        "sequencers": {
                            "seq0": {
                                "sequence": {
                                    "program": (
                                        "wait_sync 4\n"
                                        "upd_param 4\n"
                                        "wait 16\n"
                                        "set_awg_gain 32767,0\n"
                                        "play 0,1,4\n"
                                        "wait 36\n"
                                        "acquire 0,0,4\n"
                                        "stop\n"
                                    ),
                                    "waveforms": {
                                        "x_q0_i": {"index": 0, "data": [0.0, 1.0, 0.0]},
                                        "x_q0_q": {"index": 1, "data": [0.0, 0.0, 0.0]},
                                    },
                                }
                            }
                        }
                    }
                }
            },
            "qbstimeline_provenance": provenance,
        })


def build_schedule() -> dict:
    return {"name": "basic transmon demo"}


def build_compiler() -> DemoCompiler:
    return DemoCompiler()
