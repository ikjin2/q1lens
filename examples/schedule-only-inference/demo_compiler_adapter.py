from __future__ import annotations

from typing import Any

from demo_scheduler_api import Measure, Schedule, X180
from qbstimeline import annotate
from qbstimeline.symbols import SymbolicValue


class DemoTableData:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records

    def to_dict(self, orient: str) -> list[dict[str, Any]]:
        if orient != "records":
            raise ValueError("DemoTableData only supports orient='records'")
        return list(self._records)


class DemoTimingTable:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.data = DemoTableData(records)


class DemoCompiledSchedule(dict):
    def __init__(self, data: dict[str, Any], timing_records: list[dict[str, Any]]) -> None:
        super().__init__(data)
        self._timing_records = timing_records

    @property
    def timing_table(self) -> DemoTimingTable:
        return DemoTimingTable(self._timing_records)


class DemoCompiler:
    def compile(self, schedule: Schedule) -> DemoCompiledSchedule:
        schedulables: dict[str, dict[str, Any]] = {}
        operations: dict[str, dict[str, Any]] = {}
        timing_records: list[dict[str, Any]] = []
        q1asm_lines = ["wait_sync 4"]
        current_time_ns = 0

        for entry in sorted(schedule.entries, key=lambda item: item.abs_time):
            operation_id = _operation_id(entry.label)
            schedulables[entry.label] = {"operation_id": operation_id, "abs_time": entry.abs_time}
            wait_ns = _seconds_to_ns(entry.abs_time) - current_time_ns
            if wait_ns > 0:
                q1asm_lines.append(f"wait {wait_ns}")
                current_time_ns += wait_ns

            if isinstance(entry.operation, X180):
                operation = _x180_operation(entry.operation)
                duration_ns = _seconds_to_ns(_value(entry.operation.duration))
                q1asm_lines.append(f"set_awg_gain {_awg_gain(entry.operation.amp)},0")
                q1asm_lines.append(f"play 0,1,{duration_ns}")
                current_time_ns += duration_ns
                timing_records.append(
                    _timing(
                        operation["name"],
                        f"{entry.operation.qubit}:mw",
                        f"{entry.operation.qubit}.01",
                        entry.abs_time,
                        _value(entry.operation.duration),
                    )
                )
            elif isinstance(entry.operation, Measure):
                operation = _measure_operation(entry.operation)
                pulse_duration_ns = _seconds_to_ns(_value(entry.operation.pulse_duration))
                integration_duration_ns = _seconds_to_ns(_value(entry.operation.integration_duration))
                q1asm_lines.append(f"play 2,3,{pulse_duration_ns}")
                q1asm_lines.append(f"acquire {entry.operation.acq_channel},0,{integration_duration_ns}")
                current_time_ns += pulse_duration_ns + integration_duration_ns
                timing_records.append(
                    _timing(
                        operation["name"],
                        f"{entry.operation.qubit}:res",
                        f"{entry.operation.qubit}.ro",
                        entry.abs_time,
                        _value(entry.operation.pulse_duration) + _value(entry.operation.integration_duration),
                        is_acquisition=True,
                    )
                )
            else:
                raise TypeError(f"Unsupported demo operation: {entry.operation!r}")

            operations[operation_id] = operation

        q1asm_lines.append("stop")
        return DemoCompiledSchedule(
            {
                "name": schedule.name,
                "schedulables": schedulables,
                "operations": operations,
                "compiled_instructions": {
                    "cluster0": {
                        "module2": {
                            "sequencers": {
                                "seq0": {
                                    "sequence": {
                                        "program": "\n".join(q1asm_lines) + "\n",
                                        "waveforms": {
                                            "x180_i": {"index": 0, "data": [0.0, 1.0, 0.0]},
                                            "x180_q": {"index": 1, "data": [0.0, 0.0, 0.0]},
                                            "readout_i": {"index": 2, "data": [0.25, 0.25]},
                                            "readout_q": {"index": 3, "data": [0.0, 0.0]},
                                        },
                                    }
                                }
                            }
                        }
                    }
                },
            },
            timing_records,
        )


def _x180_operation(operation: X180) -> dict[str, Any]:
    payload = {
        "name": f"X180({operation.qubit})",
        "duration": _value(operation.duration),
        "pulse_info": [
            {
                "name": "DRAGPulse",
                "port": f"{operation.qubit}:mw",
                "clock": f"{operation.qubit}.01",
                "t0": 0.0,
                "duration": _value(operation.duration),
                "amp": _value(operation.amp),
                "phase": operation.phase,
            }
        ],
    }
    annotations = _symbolic_kwargs(duration=operation.duration, amp=operation.amp)
    return annotate(payload, **annotations) if annotations else payload


def _measure_operation(operation: Measure) -> dict[str, Any]:
    payload = {
        "name": f"Measure({operation.qubit})",
        "duration": _value(operation.pulse_duration) + _value(operation.integration_duration),
        "pulse_info": [
            {
                "name": "SquarePulse",
                "port": f"{operation.qubit}:res",
                "clock": f"{operation.qubit}.ro",
                "t0": 0.0,
                "duration": _value(operation.pulse_duration),
                "amp": operation.amp,
            }
        ],
        "acquisition_info": [
            {
                "protocol": "SSBIntegrationComplex",
                "port": f"{operation.qubit}:res",
                "clock": f"{operation.qubit}.ro",
                "t0": _value(operation.pulse_duration),
                "duration": _value(operation.integration_duration),
                "acq_channel": operation.acq_channel,
            }
        ],
    }
    annotations = _symbolic_kwargs(
        duration=operation.integration_duration,
        readout_pulse=operation.pulse_duration,
    )
    return annotate(payload, **annotations) if annotations else payload


def _timing(
    operation: str,
    port: str,
    clock: str,
    abs_time: float,
    duration: float,
    *,
    is_acquisition: bool = False,
) -> dict[str, Any]:
    return {
        "operation": operation,
        "port": port,
        "clock": clock,
        "abs_time": abs_time,
        "duration": duration,
        "is_acquisition": is_acquisition,
    }


def _symbolic_kwargs(**values: Any) -> dict[str, SymbolicValue]:
    return {key: value for key, value in values.items() if isinstance(value, SymbolicValue)}


def _value(value: Any) -> float:
    return float(value.value if isinstance(value, SymbolicValue) else value)


def _seconds_to_ns(value: float) -> int:
    return round(value * 1_000_000_000)


def _awg_gain(value: Any) -> int:
    return round(_value(value) * 32767)


def _operation_id(label: str) -> str:
    return f"{label}_q0" if label == "measure" else label.replace("-", "_")
