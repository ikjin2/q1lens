from __future__ import annotations

from demo_compiler_adapter import DemoCompiler
from demo_scheduler_api import Measure, Schedule, X180
from qbstimeline import sym


T_X180 = sym.time("T_X180", 40e-9)
READOUT_PULSE = sym.time("READOUT_PULSE", 160e-9)
READOUT_INTEGRATION = sym.time("READOUT_INTEGRATION", 240e-9)
AMP_X180 = sym.amp("AMP_X180", 0.32)


def build_schedule() -> Schedule:
    schedule = Schedule("schedule-only inference demo")
    schedule.add(
        X180("q0", duration=T_X180, amp=AMP_X180),
        label="x180",
        abs_time=20e-9,
    )
    schedule.add(
        Measure(
            "q0",
            pulse_duration=READOUT_PULSE,
            integration_duration=READOUT_INTEGRATION,
            amp=0.25,
            acq_channel=0,
        ),
        label="measure",
        abs_time=60e-9,
    )
    return schedule


def build_compiler() -> DemoCompiler:
    return DemoCompiler()
