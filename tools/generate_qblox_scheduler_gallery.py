from __future__ import annotations

import argparse
import json
import shutil
import sys
import traceback
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


QBS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEDULER_ROOT = QBS_ROOT.parent / "qblox-scheduler"
DEFAULT_OUT = QBS_ROOT / "examples" / "qblox-scheduler-gallery" / ".qbs_timeline"


@dataclass(frozen=True)
class ExampleSpec:
    id: str
    title: str
    category: str
    source: str
    profile: str
    build: Callable[[], Any]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    scheduler_root = Path(args.scheduler_root).resolve()
    out_dir = Path(args.out).resolve()
    _bootstrap_paths(scheduler_root)

    from qbstimeline.render.gallery import write_gallery_index

    _configure_runtime()
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    contexts = _build_contexts()
    results = []
    for spec in _example_specs():
        result = _generate_example(spec, out_dir=out_dir, contexts=contexts)
        results.append(result)
        print(f"{result['status']:>7} {spec.id} ({result['operation_count']} ops, {result['q1asm_program_count']} q1asm)")

    (out_dir / "summary.json").write_text(
        json.dumps(results, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    write_gallery_index(
        results,
        out_dir / "index.html",
        title="Qblox Scheduler Examples - QBS Timeline Gallery",
    )
    _close_instruments()
    print(f"Wrote gallery: {out_dir / 'index.html'}")
    return 0 if any(result["status"] == "ok" for result in results) else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a qbs_timeline gallery from qblox-scheduler schedule factories.")
    parser.add_argument("--scheduler-root", default=str(DEFAULT_SCHEDULER_ROOT), help="Path to the local qblox-scheduler repository.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Gallery output directory.")
    parser.add_argument("--clean", action="store_true", help="Remove the output directory before generating.")
    return parser


def _bootstrap_paths(scheduler_root: Path) -> None:
    scheduler_src = scheduler_root / "src"
    if not scheduler_src.exists():
        raise SystemExit(f"qblox-scheduler source not found: {scheduler_src}")
    sys.path.insert(0, str(QBS_ROOT / "src"))
    sys.path.insert(0, str(scheduler_src))


def _configure_runtime() -> None:
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    warnings.filterwarnings("ignore", message=".*qcodes.utils.helpers.*")
    warnings.filterwarnings("ignore", category=RuntimeWarning, message="Clock .* has conflicting frequency definitions.*")


def _build_contexts() -> dict[str, Any]:
    from qcodes.instrument import Instrument
    from qblox_scheduler.device_under_test.mock_setup import (
        set_standard_params_basic_nv,
        set_standard_params_transmon,
        set_up_mock_basic_nv_setup,
        set_up_mock_transmon_setup,
    )
    from qblox_scheduler.schemas.examples import utils

    Instrument.close_all()

    transmon = set_up_mock_transmon_setup()
    set_standard_params_transmon(transmon)
    transmon["quantum_device"].hardware_config = utils.load_json_example_scheme(
        "qblox_hardware_config_transmon.json"
    )
    transmon_config = transmon["quantum_device"].generate_compilation_config()

    Instrument.close_all()

    nv = set_up_mock_basic_nv_setup()
    set_standard_params_basic_nv(nv)
    nv["quantum_device"].hardware_config = utils.load_json_example_scheme(
        "qblox_hardware_config_nv_center.json"
    )
    nv_config = nv["quantum_device"].generate_compilation_config()

    return {
        "transmon": transmon_config,
        "nv": nv_config,
    }


def _generate_example(spec: ExampleSpec, *, out_dir: Path, contexts: dict[str, Any]) -> dict[str, Any]:
    from qbstimeline.compile_worker import (
        _extract_operations,
        _extract_timing_table,
        _schedule_name,
        _write_q1asm_files,
        extract_q1asm_programs,
    )
    from qbstimeline.ir.serialize import make_qbs_ir, write_qbs_ir
    from qbstimeline.render.html import render_ir_file

    example_dir = out_dir / spec.id
    example_dir.mkdir(parents=True, exist_ok=True)
    q1asm_programs = []
    compile_errors: list[str] = []
    view_warnings: list[str] = []
    compiled = None
    schedule = spec.build()

    try:
        compiled = _compile_for_profile(schedule, spec.profile, contexts)
        q1asm_programs = extract_q1asm_programs(compiled.compiled_instructions)
        _write_q1asm_files(example_dir, q1asm_programs)
        status = "ok"
    except Exception as exc:  # noqa: BLE001 - generation should keep producing the rest of the gallery.
        status = "error"
        compile_errors.append(f"{type(exc).__name__}: {exc}")
        traceback_text = traceback.format_exc()
        (example_dir / "error.txt").write_text(traceback_text, encoding="utf-8")

    try:
        view_schedule = _build_view_schedule(schedule, spec.profile)
    except Exception as exc:  # noqa: BLE001
        view_warnings.append(f"view fallback {type(exc).__name__}: {exc}")
        view_schedule = compiled

    operations = _extract_operations(view_schedule) if view_schedule is not None else []
    timing_table = _extract_timing_table(view_schedule) if view_schedule is not None else []
    schedule_name = _schedule_name(view_schedule, view_schedule) if view_schedule is not None else spec.title

    ir = make_qbs_ir(
        project_root=QBS_ROOT,
        schedule_name=schedule_name,
        operations=operations,
        timing_table=timing_table,
        q1asm_programs=q1asm_programs,
        low_level_q1timeline=False,
    )
    ir["status"] = status
    ir["source"] = spec.source
    ir["category"] = spec.category
    error = "\n".join(compile_errors)
    if compile_errors:
        ir["error"] = error
    if view_warnings:
        ir["warnings"] = view_warnings

    write_qbs_ir(ir, example_dir / "qbs_ir.json")
    render_ir_file(ir, example_dir / "index.html")

    return {
        "id": spec.id,
        "title": spec.title,
        "category": spec.category,
        "href": f"{spec.id}/index.html",
        "status": status,
        "source": spec.source,
        "error": error,
        "warnings": view_warnings,
        "operation_count": len(operations),
        "timing_row_count": len(timing_table),
        "q1asm_program_count": len(q1asm_programs),
    }


def _compile_for_profile(schedule: Any, profile: str, contexts: dict[str, Any]) -> Any:
    from qblox_scheduler.backends import SerialCompiler

    config = contexts["nv"] if profile == "nv" else contexts["transmon"]
    return SerialCompiler("qbs-timeline-gallery").compile(schedule=schedule, config=config)


def _build_view_schedule(schedule: Any, profile: str) -> Any:
    from qblox_scheduler.compilation import _determine_absolute_timing

    try:
        return _determine_absolute_timing(schedule=schedule, time_unit="physical")
    except Exception:
        return _compile_device_only(schedule, profile)


def _compile_device_only(schedule: Any, profile: str) -> Any:
    from qblox_scheduler.backends.circuit_to_device import (
        DeviceCompilationConfig,
        compile_circuit_to_device_with_config_validation,
    )
    from qblox_scheduler.backends.graph_compilation import SerialCompilationConfig
    from qblox_scheduler.compilation import _determine_absolute_timing
    from qblox_scheduler.schemas.examples.device_example_cfgs import example_transmon_cfg

    if profile == "nv":
        raise RuntimeError("device-only NV compilation is not configured for gallery fallback")

    device_config = DeviceCompilationConfig.model_validate(example_transmon_cfg)
    device_schedule = compile_circuit_to_device_with_config_validation(
        schedule=schedule,
        config=SerialCompilationConfig(
            name="qbs-timeline-gallery-device-only",
            device_compilation_config=device_config,
        ),
    )
    return _determine_absolute_timing(schedule=device_schedule, time_unit="physical")


def _example_specs() -> list[ExampleSpec]:
    import numpy as np
    from qblox_scheduler.schedules.spectroscopy_schedules import (
        heterodyne_spec_sched,
        heterodyne_spec_sched_nco,
        nv_dark_esr_sched,
        nv_dark_esr_sched_nco,
        two_tone_spec_sched,
        two_tone_spec_sched_nco,
    )
    from qblox_scheduler.schedules.timedomain_schedules import (
        allxy_sched,
        cpmg_sched,
        echo_sched,
        rabi_pulse_sched,
        rabi_sched,
        ramsey_sched,
        readout_calibration_sched,
        t1_sched,
    )
    from qblox_scheduler.schedules.trace_schedules import (
        trace_schedule,
        trace_schedule_circuit_layer,
        two_tone_trace_schedule,
    )
    from qblox_scheduler.schedules.two_qubit_transmon_schedules import chevron_cz_sched
    from qblox_scheduler.schedules.verification import (
        acquisition_staircase_sched,
        awg_staircase_sched,
        multiplexing_staircase_sched,
    )

    return [
        ExampleSpec("rabi", "Rabi", "timedomain", "src/qblox_scheduler/schedules/timedomain_schedules.py::rabi_sched", "transmon", lambda: rabi_sched(np.array([0.12, 0.24]), 20e-9, 6.02e9, "q0")),
        ExampleSpec("t1", "T1", "timedomain", "src/qblox_scheduler/schedules/timedomain_schedules.py::t1_sched", "transmon", lambda: t1_sched(np.array([40e-9, 80e-9]), "q0")),
        ExampleSpec("ramsey", "Ramsey", "timedomain", "src/qblox_scheduler/schedules/timedomain_schedules.py::ramsey_sched", "transmon", lambda: ramsey_sched(np.array([40e-9, 80e-9]), "q0", artificial_detuning=1e6)),
        ExampleSpec("echo", "Echo", "timedomain", "src/qblox_scheduler/schedules/timedomain_schedules.py::echo_sched", "transmon", lambda: echo_sched(np.array([80e-9, 120e-9]), "q0")),
        ExampleSpec("cpmg", "CPMG", "timedomain", "src/qblox_scheduler/schedules/timedomain_schedules.py::cpmg_sched", "transmon", lambda: cpmg_sched(2, np.array([160e-9]), "q0", variant="XY")),
        ExampleSpec("allxy", "AllXY", "timedomain", "src/qblox_scheduler/schedules/timedomain_schedules.py::allxy_sched", "transmon", lambda: allxy_sched("q0", element_select_idx=[0, 5, 20])),
        ExampleSpec("readout-calibration", "Readout calibration", "timedomain", "src/qblox_scheduler/schedules/timedomain_schedules.py::readout_calibration_sched", "transmon", lambda: readout_calibration_sched("q0", prepared_states=[0, 1])),
        ExampleSpec("rabi-pulse", "Rabi pulse-level", "timedomain", "src/qblox_scheduler/schedules/timedomain_schedules.py::rabi_pulse_sched", "transmon", lambda: rabi_pulse_sched(0.2, 0.0, 6.02e9, "q0.01", "q0:mw", 20e-9, 0.2, 160e-9, 40e-9, "q0:res", "q0.ro", 7.04e9, 120e-9, 300e-9, 1e-6)),
        ExampleSpec("heterodyne-spec", "Heterodyne spectroscopy", "spectroscopy", "src/qblox_scheduler/schedules/spectroscopy_schedules.py::heterodyne_spec_sched", "transmon", lambda: heterodyne_spec_sched(0.2, 300e-9, 7.04e9, 120e-9, 300e-9, "q0:res", "q0.ro", init_duration=1e-6)),
        ExampleSpec("heterodyne-spec-nco", "Heterodyne spectroscopy NCO", "spectroscopy", "src/qblox_scheduler/schedules/spectroscopy_schedules.py::heterodyne_spec_sched_nco", "transmon", lambda: heterodyne_spec_sched_nco(0.2, 300e-9, np.array([7.80e9, 7.82e9]), 120e-9, 300e-9, "q0:res", "q0.ro", init_duration=1e-6)),
        ExampleSpec("two-tone-spec", "Two-tone spectroscopy", "spectroscopy", "src/qblox_scheduler/schedules/spectroscopy_schedules.py::two_tone_spec_sched", "transmon", lambda: two_tone_spec_sched(0.1, 80e-9, "q0:mw", "q0.01", 6.02e9, 0.2, 160e-9, 40e-9, "q0:res", "q0.ro", 7.04e9, 120e-9, 300e-9, init_duration=1e-6)),
        ExampleSpec("two-tone-spec-nco", "Two-tone spectroscopy NCO", "spectroscopy", "src/qblox_scheduler/schedules/spectroscopy_schedules.py::two_tone_spec_sched_nco", "transmon", lambda: two_tone_spec_sched_nco(0.1, 80e-9, "q0:mw", "q0.01", np.array([6.0e9, 6.02e9]), 0.2, 160e-9, 40e-9, "q0:res", "q0.ro", 7.04e9, 120e-9, 300e-9, init_duration=1e-6)),
        ExampleSpec("nv-dark-esr", "NV dark ESR", "spectroscopy", "src/qblox_scheduler/schedules/spectroscopy_schedules.py::nv_dark_esr_sched", "nv", lambda: nv_dark_esr_sched("qe0")),
        ExampleSpec("nv-dark-esr-nco", "NV dark ESR NCO", "spectroscopy", "src/qblox_scheduler/schedules/spectroscopy_schedules.py::nv_dark_esr_sched_nco", "nv", lambda: nv_dark_esr_sched_nco("qe0", "qe0.spec", np.array([2.00e9, 2.02e9]))),
        ExampleSpec("chevron-cz", "Chevron CZ", "two-qubit", "src/qblox_scheduler/schedules/two_qubit_transmon_schedules.py::chevron_cz_sched", "transmon", lambda: chevron_cz_sched("q0", "q1", np.array([0.3, 0.5]), 20e-9)),
        ExampleSpec("trace", "Trace acquisition", "trace", "src/qblox_scheduler/schedules/trace_schedules.py::trace_schedule", "transmon", lambda: trace_schedule(0.2, 160e-9, 40e-9, 7.04e9, 120e-9, 300e-9, "q0:res", "q0.ro", init_duration=1e-6)),
        ExampleSpec("trace-circuit-layer", "Trace acquisition circuit layer", "trace", "src/qblox_scheduler/schedules/trace_schedules.py::trace_schedule_circuit_layer", "transmon", lambda: trace_schedule_circuit_layer("q0")),
        ExampleSpec("two-tone-trace", "Two-tone trace", "trace", "src/qblox_scheduler/schedules/trace_schedules.py::two_tone_trace_schedule", "transmon", lambda: two_tone_trace_schedule(0.1, 80e-9, 6.02e9, "q0:mw", "q0.01", 0.2, 160e-9, 40e-9, "q0:res", "q0.ro", 7.04e9, 120e-9, 300e-9, init_duration=1e-6)),
        ExampleSpec("acquisition-staircase", "Acquisition staircase", "verification", "src/qblox_scheduler/schedules/verification.py::acquisition_staircase_sched", "transmon", lambda: acquisition_staircase_sched(np.array([0.1, 0.2, 0.3]), 160e-9, 7.04e9, 120e-9, 300e-9, "q0:res", "q0.ro", init_duration=1e-6)),
        ExampleSpec("awg-staircase", "AWG staircase", "verification", "src/qblox_scheduler/schedules/verification.py::awg_staircase_sched", "transmon", lambda: awg_staircase_sched(np.array([0.1, 0.2, 0.3]), 80e-9, 7.04e9, 120e-9, 300e-9, "q0:mw", "q0:res", "q0.01", "q0.ro", init_duration=1e-6)),
        ExampleSpec("multiplexing-staircase", "Multiplexing staircase", "verification", "src/qblox_scheduler/schedules/verification.py::multiplexing_staircase_sched", "transmon", lambda: multiplexing_staircase_sched(np.array([0.1, 0.2]), 400e-9, 120e-9, 300e-9, "q0:res", "q0.ro", "q1.ro", 7.04e9, 6.90e9, init_duration=1e-6)),
    ]


def _close_instruments() -> None:
    try:
        from qcodes.instrument import Instrument

        Instrument.close_all()
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
