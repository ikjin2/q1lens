# Qblox Scheduler Gallery

This gallery is generated from the local `qblox-scheduler` repository's schedule factory modules:

- `timedomain_schedules.py`
- `spectroscopy_schedules.py`
- `trace_schedules.py`
- `two_qubit_transmon_schedules.py`
- `verification.py`

Generate it with the `qblox-scheduler` virtual environment so the scheduler's optional dependencies are available:

```powershell
cd C:\path\to\q1lens
..\qblox-scheduler\.venv\Scripts\python.exe tools\generate_qblox_scheduler_gallery.py --scheduler-root ..\qblox-scheduler --clean
```

Open the generated gallery:

```text
examples\qblox-scheduler-gallery\.qbs_timeline\index.html
```

The generated `.qbs_timeline` directory is ignored by git. It contains one folder per scheduler example with `qbs_ir.json`, `index.html`, and extracted Q1ASM files when Qblox hardware compilation succeeds.

Current known limitation: `chevron_cz_sched` is included and visualized at the schedule/timing level, but the bundled transmon mock hardware config does not map `q1:res`, so its hardware Q1ASM compilation is reported as failed in the gallery.
