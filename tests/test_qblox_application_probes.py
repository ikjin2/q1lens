from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_probe_tool():
    path = Path(__file__).resolve().parents[1] / "tools" / "prepare_qblox_application_probes.py"
    spec = importlib.util.spec_from_file_location("prepare_qblox_application_probes", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_notebook(path: Path, code_cells: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "cells": [
                    {"cell_type": "code", "source": cell.splitlines(keepends=True), "metadata": {}}
                    for cell in code_cells
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )


def _write_manifest(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "entries": [
            {
                "page_slug": "spin/spin/001_time_of_flight",
                "page_url": "https://example.test/tof",
                "page_title": "Time of flight",
                "extracted_dir": "extracted/spin/spin/001_time_of_flight",
                "files": ["001_time_of_flight.ipynb"],
            },
            {
                "page_slug": "spin/spin/000_spin_setup",
                "page_url": "https://example.test/setup",
                "page_title": "Spin setup",
                "extracted_dir": "extracted/spin/spin/000_spin_setup",
                "files": ["000_spin_setup.ipynb"],
            },
        ]
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_prepare_probes_creates_qbstimeline_projects_for_notebooks(tmp_path: Path) -> None:
    tool = _load_probe_tool()
    examples_root = tmp_path / "examples" / "qblox_application_examples"
    manifest_path = _write_manifest(examples_root)
    _write_notebook(
        examples_root / "extracted" / "spin" / "spin" / "001_time_of_flight" / "001_time_of_flight.ipynb",
        [
            "from qblox_scheduler import HardwareAgent, Schedule\nhw_agent = HardwareAgent('hw.json', 'dev.yaml')\n",
            "tof_schedule = Schedule('Trace measurement schedule')\n",
        ],
    )
    _write_notebook(
        examples_root / "extracted" / "spin" / "spin" / "000_spin_setup" / "000_spin_setup.ipynb",
        ["hardware_config = {'cluster0': {}}\n"],
    )

    manifest = tool.prepare_probes(manifest_path, tmp_path / ".scratch" / "probes", clean=True)

    assert len(manifest["probes"]) == 2
    probe = manifest["probes"][0]
    probe_dir = Path(probe["probe_dir"])
    project_text = (probe_dir / "qbstimeline.yml").read_text(encoding="utf-8")
    assert project_text.startswith("schedule:\n  file: schedule.py\n")
    assert "source:\n  notebook:" in project_text
    assert "001_time_of_flight.ipynb" in project_text
    assert "# %% qbstimeline notebook cell 1" in (probe_dir / "notebook_cells.py").read_text(encoding="utf-8")
    schedule_py = (probe_dir / "schedule.py").read_text(encoding="utf-8")
    assert 'SCHEDULE_CANDIDATES = ("tof_schedule",)' in schedule_py
    assert 'COMPILER_CANDIDATES = ("hw_agent",)' in schedule_py
    assert probe["notebook"] == str(
        examples_root / "extracted" / "spin" / "spin" / "001_time_of_flight" / "001_time_of_flight.ipynb"
    )
    assert probe["analysis"]["code_cell_count"] == 2
    assert probe["analysis"]["schedule_candidates"] == ["tof_schedule"]
    assert probe["analysis"]["compiler_candidates"] == ["hw_agent"]


def test_prepare_probes_preserves_original_notebook_cell_indexes_in_markers(tmp_path: Path) -> None:
    tool = _load_probe_tool()
    examples_root = tmp_path / "examples" / "qblox_application_examples"
    manifest_path = _write_manifest(examples_root)
    notebook = examples_root / "extracted" / "spin" / "spin" / "001_time_of_flight" / "001_time_of_flight.ipynb"
    notebook.parent.mkdir(parents=True, exist_ok=True)
    notebook.write_text(
        json.dumps(
            {
                "cells": [
                    {"cell_type": "markdown", "source": ["# setup"], "metadata": {}},
                    {
                        "cell_type": "code",
                        "source": [
                            "from qblox_scheduler import HardwareAgent, Schedule\n"
                            "hw_agent = HardwareAgent('hw.json', 'dev.yaml')\n"
                        ],
                        "metadata": {},
                    },
                    {
                        "cell_type": "code",
                        "source": ["tof_schedule = Schedule('Trace measurement schedule')\n"],
                        "metadata": {},
                    },
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )
    _write_notebook(
        examples_root / "extracted" / "spin" / "spin" / "000_spin_setup" / "000_spin_setup.ipynb",
        ["hardware_config = {'cluster0': {}}\n"],
    )

    manifest = tool.prepare_probes(manifest_path, tmp_path / ".scratch" / "probes", clean=True)

    notebook_cells = Path(manifest["probes"][0]["probe_dir"], "notebook_cells.py").read_text(encoding="utf-8")
    assert "# %% qbstimeline notebook cell 2" in notebook_cells
    assert "# %% qbstimeline notebook cell 3" in notebook_cells
    assert "# %% qbstimeline notebook cell 1" not in notebook_cells


def test_prepare_probes_detects_schedule_factory_assignments(tmp_path: Path) -> None:
    tool = _load_probe_tool()
    examples_root = tmp_path / "examples" / "qblox_application_examples"
    manifest_path = _write_manifest(examples_root)
    _write_notebook(
        examples_root / "extracted" / "spin" / "spin" / "001_time_of_flight" / "001_time_of_flight.ipynb",
        [
            (
                "from qblox_scheduler import HardwareAgent\n"
                "from dependencies.utils import randomized_benchmarking_schedule\n"
                "hw_agent = HardwareAgent('hw.json', 'dev.yaml')\n"
                "sched = randomized_benchmarking_schedule('q0')\n"
            ),
        ],
    )
    _write_notebook(
        examples_root / "extracted" / "spin" / "spin" / "000_spin_setup" / "000_spin_setup.ipynb",
        ["hardware_config = {'cluster0': {}}\n"],
    )

    manifest = tool.prepare_probes(manifest_path, tmp_path / ".scratch" / "probes", clean=True)

    probe = manifest["probes"][0]
    assert probe["analysis"]["schedule_candidates"] == ["sched"]
    schedule_py = Path(probe["probe_dir"], "schedule.py").read_text(encoding="utf-8")
    assert 'SCHEDULE_CANDIDATES = ("sched",)' in schedule_py


def test_prepare_probes_marks_notebooks_without_schedule_candidates(tmp_path: Path) -> None:
    tool = _load_probe_tool()
    examples_root = tmp_path / "examples" / "qblox_application_examples"
    manifest_path = _write_manifest(examples_root)
    _write_notebook(
        examples_root / "extracted" / "spin" / "spin" / "001_time_of_flight" / "001_time_of_flight.ipynb",
        ["setup_only = True\n"],
    )
    _write_notebook(
        examples_root / "extracted" / "spin" / "spin" / "000_spin_setup" / "000_spin_setup.ipynb",
        ["hardware_config = {'cluster0': {}}\n"],
    )

    manifest = tool.prepare_probes(manifest_path, tmp_path / ".scratch" / "probes", clean=True)

    assert all(probe["analysis"]["schedule_candidates"] == [] for probe in manifest["probes"])
    assert all(
        any("No Schedule(...) assignment or schedule factory detected" in warning for warning in probe["warnings"])
        for probe in manifest["probes"]
    )
    schedule_py = Path(manifest["probes"][0]["probe_dir"], "schedule.py").read_text(encoding="utf-8")
    assert "if not SCHEDULE_CANDIDATES:" in schedule_py


def test_generated_probe_compiler_uses_compact_preview_for_large_control_flow() -> None:
    tool = _load_probe_tool()
    code = tool._render_schedule_wrapper(
        notebook_dir=Path("notebooks"),
        schedule_candidates=["sched"],
        compiler_candidates=["hw_agent"],
    )
    namespace: dict[str, object] = {"__file__": str(Path("probe") / "schedule.py")}
    exec(code, namespace)

    class TargetCompiler:
        called = False

        def compile(self, schedule):
            self.called = True
            return {"compiled": schedule}

    class Domain:
        num = 400

    class SweepDomain:
        num = 100

    sweep_body = type("SweepBody", (), {"schedulables": {}, "operations": {}})()
    sweep_operation = type(
        "SweepOperation",
        (),
        {
            "data": {
                "control_flow_info": {
                    "body": sweep_body,
                    "domain": {"freq": SweepDomain()},
                    "repetitions": 100,
                }
            }
        },
    )()
    loop_body = type(
        "LoopBody",
        (),
        {
            "schedulables": {"sweep": {"operation_id": "sweep_operation"}},
            "operations": {"sweep_operation": sweep_operation},
        },
    )()
    loop_operation = type(
        "LoopOperation",
        (),
        {
            "data": {
                "control_flow_info": {
                    "body": loop_body,
                    "domain": {"rep": Domain()},
                    "repetitions": 400,
                }
            }
        },
    )()
    schedule = type(
        "Schedule",
        (),
        {
            "schedulables": {"loop": {"operation_id": "loop_operation"}},
            "operations": {"loop_operation": loop_operation},
        },
    )()
    target = TargetCompiler()
    compiler = namespace["_HardwareAgentCompiler"](target)

    assert compiler.compile(schedule) is schedule
    assert target.called is False


def test_generated_probe_compiler_counts_control_flow_inside_nested_schedule() -> None:
    tool = _load_probe_tool()
    code = tool._render_schedule_wrapper(
        notebook_dir=Path("notebooks"),
        schedule_candidates=["sched"],
        compiler_candidates=["hw_agent"],
    )
    namespace: dict[str, object] = {"__file__": str(Path("probe") / "schedule.py")}
    exec(code, namespace)

    class TargetCompiler:
        called = False

        def compile(self, schedule):
            self.called = True
            return {"compiled": schedule}

    class RepDomain:
        num = 400

    class SweepDomain:
        num = 100

    sweep_body = type("SweepBody", (), {"schedulables": {}, "operations": {}})()
    sweep_operation = type(
        "SweepOperation",
        (),
        {
            "data": {
                "control_flow_info": {
                    "body": sweep_body,
                    "domain": {"freq": SweepDomain()},
                    "repetitions": 100,
                }
            }
        },
    )()
    nested_schedule = type(
        "NestedSchedule",
        (),
        {
            "schedulables": {"sweep": {"operation_id": "sweep_operation"}},
            "operations": {"sweep_operation": sweep_operation},
        },
    )()
    loop_body = type(
        "LoopBody",
        (),
        {
            "schedulables": {"nested": {"operation_id": "nested_schedule"}},
            "operations": {"nested_schedule": nested_schedule},
        },
    )()
    loop_operation = type(
        "LoopOperation",
        (),
        {
            "data": {
                "control_flow_info": {
                    "body": loop_body,
                    "domain": {"rep": RepDomain()},
                    "repetitions": 400,
                }
            }
        },
    )()
    schedule = type(
        "Schedule",
        (),
        {
            "schedulables": {"loop": {"operation_id": "loop_operation"}},
            "operations": {"loop_operation": loop_operation},
        },
    )()
    target = TargetCompiler()
    compiler = namespace["_HardwareAgentCompiler"](target)

    assert compiler.compile(schedule) is schedule
    assert target.called is False


def test_generated_probe_compiler_does_not_treat_plain_body_as_control_flow() -> None:
    tool = _load_probe_tool()
    code = tool._render_schedule_wrapper(
        notebook_dir=Path("notebooks"),
        schedule_candidates=["sched"],
        compiler_candidates=["hw_agent"],
    )
    namespace: dict[str, object] = {"__file__": str(Path("probe") / "schedule.py")}
    exec(code, namespace)

    class TargetCompiler:
        called = False

        def compile(self, schedule):
            self.called = True
            return {"compiled": schedule}

    nested_body = type("NestedBody", (), {"schedulables": {}, "operations": {}})()
    pulse_compensation = type(
        "PulseCompensation",
        (),
        {
            "body": nested_body,
            "data": {"name": "PulseCompensation"},
        },
    )()
    schedule = type(
        "Schedule",
        (),
        {
            "schedulables": {"pc": {"operation_id": "pulse_compensation"}},
            "operations": {"pulse_compensation": pulse_compensation},
        },
    )()
    target = TargetCompiler()
    compiler = namespace["_HardwareAgentCompiler"](target)

    assert compiler.compile(schedule) == {"compiled": schedule}
    assert target.called is True


def test_generated_probe_compiler_compiles_representative_schedule_for_large_manual_sweep() -> None:
    tool = _load_probe_tool()
    code = tool._render_schedule_wrapper(
        notebook_dir=Path("notebooks"),
        schedule_candidates=["sched"],
        compiler_candidates=["hw_agent"],
    )
    namespace: dict[str, object] = {"__file__": str(Path("probe") / "schedule.py")}
    exec(code, namespace)

    class TargetCompiler:
        called = False
        compiled_schedule = None

        def compile(self, schedule):
            self.called = True
            self.compiled_schedule = schedule
            return {"compiled": schedule}

    nested_body = type("NestedBody", (), {"schedulables": {"measure": {"operation_id": "measure"}}, "operations": {"measure": object()}})()

    def make_pulse_compensation():
        return type(
            "PulseCompensation",
            (),
            {
                "body": nested_body,
                "name": "PulseCompensation",
                "data": {"name": "PulseCompensation"},
            },
        )()

    schedule = type(
        "Schedule",
        (),
        {
            "repetitions": 101,
            "schedulables": {
                f"pc{index}": {"operation_id": f"pulse_compensation_{index}"}
                for index in range(60)
            },
            "operations": {
                f"pulse_compensation_{index}": make_pulse_compensation()
                for index in range(60)
            },
        },
    )()
    target = TargetCompiler()
    compiler = namespace["_HardwareAgentCompiler"](target)

    result = compiler.compile(schedule)

    assert result == {"compiled": target.compiled_schedule}
    assert target.called is True
    assert target.compiled_schedule is not schedule
    assert target.compiled_schedule.repetitions == 1
    assert schedule.repetitions == 101


def test_generated_probe_compiler_counts_experiment_wrapped_control_flow() -> None:
    tool = _load_probe_tool()
    code = tool._render_schedule_wrapper(
        notebook_dir=Path("notebooks"),
        schedule_candidates=["sched"],
        compiler_candidates=["hw_agent"],
    )
    namespace: dict[str, object] = {"__file__": str(Path("probe") / "schedule.py")}
    exec(code, namespace)

    class TargetCompiler:
        called = False

        def compile(self, schedule):
            self.called = True
            return {"compiled": schedule}

    class Domain:
        num = 2000

    loop_body = type("LoopBody", (), {"schedulables": {}, "operations": {}})()
    loop_operation = type(
        "LoopOperation",
        (),
        {
            "body": loop_body,
            "data": {
                "control_flow_info": {
                    "body": loop_body,
                    "domain": {"freq": Domain()},
                    "repetitions": 2000,
                }
            },
        },
    )()
    nested_schedule = type(
        "NestedSchedule",
        (),
        {
            "schedulables": {"loop": {"operation_id": "loop_operation"}},
            "operations": {"loop_operation": loop_operation},
        },
    )()

    class ExperimentWrappedSchedule:
        _experiments = [{"steps": [{"schedule_info": {"schedule": nested_schedule}}]}]

        @property
        def schedulables(self):
            raise RuntimeError("unavailable")

        @property
        def operations(self):
            raise RuntimeError("unavailable")

    target = TargetCompiler()
    compiler = namespace["_HardwareAgentCompiler"](target)
    schedule = ExperimentWrappedSchedule()

    assert compiler.compile(schedule) is schedule
    assert target.called is False


def test_generated_probe_compiler_ignores_unavailable_untimed_schedulables() -> None:
    tool = _load_probe_tool()
    code = tool._render_schedule_wrapper(
        notebook_dir=Path("notebooks"),
        schedule_candidates=["sched"],
        compiler_candidates=["hw_agent"],
    )
    namespace: dict[str, object] = {"__file__": str(Path("probe") / "schedule.py")}
    exec(code, namespace)

    class UntimedSchedule:
        @property
        def schedulables(self):
            raise RuntimeError("`schedulables` dict unavailable on schedule with untimed operations")

    class TargetCompiler:
        called = False

        def compile(self, schedule):
            self.called = True
            return {"compiled": schedule}

    target = TargetCompiler()
    compiler = namespace["_HardwareAgentCompiler"](target)
    schedule = UntimedSchedule()

    assert compiler.compile(schedule) == {"compiled": schedule}
    assert target.called is True


def test_prepare_probes_skips_cells_after_notebook_execution_boundary(tmp_path: Path) -> None:
    tool = _load_probe_tool()
    examples_root = tmp_path / "examples" / "qblox_application_examples"
    manifest_path = _write_manifest(examples_root)
    _write_notebook(
        examples_root / "extracted" / "spin" / "spin" / "001_time_of_flight" / "001_time_of_flight.ipynb",
        [
            "from qblox_scheduler import HardwareAgent, Schedule\nhw_agent = HardwareAgent('hw.json', 'dev.yaml')\n",
            "tof_schedule = Schedule('Trace measurement schedule')\nqs_data = hw_agent.run(tof_schedule)\n",
            "qs_analysis = QubitSpectroscopyAnalysis(qs_data).run()\n",
            "qubit.clock_freqs.f01 = qs_analysis.quantities_of_interest['frequency_01'].nominal_value\n",
        ],
    )
    _write_notebook(
        examples_root / "extracted" / "spin" / "spin" / "000_spin_setup" / "000_spin_setup.ipynb",
        ["hardware_config = {'cluster0': {}}\n"],
    )

    manifest = tool.prepare_probes(manifest_path, tmp_path / ".scratch" / "probes", clean=True)

    probe_dir = Path(manifest["probes"][0]["probe_dir"])
    notebook_cells = (probe_dir / "notebook_cells.py").read_text(encoding="utf-8")
    assert "tof_schedule = Schedule" in notebook_cells
    assert "qbstimeline probe stopped before execution statement" in notebook_cells
    assert "qbstimeline probe skipped cell after execution boundary" in notebook_cells
    assert "\nqs_data = hw_agent.run(tof_schedule)" not in notebook_cells
    assert "\nqs_analysis = QubitSpectroscopyAnalysis(qs_data).run()" not in notebook_cells
    assert (
        "\nqubit.clock_freqs.f01 = qs_analysis.quantities_of_interest"
        not in notebook_cells
    )


def test_prepare_probes_comments_live_cluster_connections(tmp_path: Path) -> None:
    tool = _load_probe_tool()
    examples_root = tmp_path / "examples" / "qblox_application_examples"
    manifest_path = _write_manifest(examples_root)
    _write_notebook(
        examples_root / "extracted" / "spin" / "spin" / "001_time_of_flight" / "001_time_of_flight.ipynb",
        [
            "from qblox_scheduler import HardwareAgent, Schedule\nhw_agent = HardwareAgent('hw.json', 'dev.yaml')\nhw_agent.connect_clusters()\n",
            "tof_schedule = Schedule('Trace measurement schedule')\n",
        ],
    )
    _write_notebook(
        examples_root / "extracted" / "spin" / "spin" / "000_spin_setup" / "000_spin_setup.ipynb",
        ["hardware_config = {'cluster0': {}}\n"],
    )

    manifest = tool.prepare_probes(manifest_path, tmp_path / ".scratch" / "probes", clean=True)

    notebook_cells = Path(manifest["probes"][0]["probe_dir"], "notebook_cells.py").read_text(encoding="utf-8")
    assert "qbstimeline probe skipped live/dummy cluster connection" in notebook_cells
    assert "\nhw_agent.connect_clusters()" not in notebook_cells


def test_prepare_probes_comments_unused_hardware_introspection(tmp_path: Path) -> None:
    tool = _load_probe_tool()
    examples_root = tmp_path / "examples" / "qblox_application_examples"
    manifest_path = _write_manifest(examples_root)
    _write_notebook(
        examples_root / "extracted" / "spin" / "spin" / "001_time_of_flight" / "001_time_of_flight.ipynb",
        [
            (
                "from qblox_scheduler import HardwareAgent, Schedule\n"
                "hw_agent = HardwareAgent('hw.json', 'dev.yaml')\n"
                "tof_schedule = Schedule('Trace measurement schedule')\n"
                "hw_opts = hw_agent.hardware_configuration.hardware_options\n"
                "cluster = hw_agent.get_clusters()['cluster0']\n"
            ),
        ],
    )
    _write_notebook(
        examples_root / "extracted" / "spin" / "spin" / "000_spin_setup" / "000_spin_setup.ipynb",
        ["hardware_config = {'cluster0': {}}\n"],
    )

    manifest = tool.prepare_probes(manifest_path, tmp_path / ".scratch" / "probes", clean=True)

    notebook_cells = Path(manifest["probes"][0]["probe_dir"], "notebook_cells.py").read_text(encoding="utf-8")
    assert "qbstimeline probe skipped unused hardware introspection" in notebook_cells
    assert "\nhw_opts = hw_agent.hardware_configuration.hardware_options" not in notebook_cells
    assert "\ncluster = hw_agent.get_clusters()" not in notebook_cells


def test_prepare_probes_keeps_used_hardware_introspection(tmp_path: Path) -> None:
    tool = _load_probe_tool()
    examples_root = tmp_path / "examples" / "qblox_application_examples"
    manifest_path = _write_manifest(examples_root)
    _write_notebook(
        examples_root / "extracted" / "spin" / "spin" / "001_time_of_flight" / "001_time_of_flight.ipynb",
        [
            (
                "from qblox_scheduler import HardwareAgent, Schedule\n"
                "hw_agent = HardwareAgent('hw.json', 'dev.yaml')\n"
                "tof_schedule = Schedule('Trace measurement schedule')\n"
                "hw_opts = hw_agent.hardware_configuration.hardware_options\n"
                "tof_schedule.add_resource(hw_opts)\n"
            ),
        ],
    )
    _write_notebook(
        examples_root / "extracted" / "spin" / "spin" / "000_spin_setup" / "000_spin_setup.ipynb",
        ["hardware_config = {'cluster0': {}}\n"],
    )

    manifest = tool.prepare_probes(manifest_path, tmp_path / ".scratch" / "probes", clean=True)

    notebook_cells = Path(manifest["probes"][0]["probe_dir"], "notebook_cells.py").read_text(encoding="utf-8")
    assert "try:" in notebook_cells
    assert "hw_opts = hw_agent.hardware_configuration.hardware_options" in notebook_cells
    assert "except TypeError:" in notebook_cells
    assert "output_att=__import__('collections').defaultdict(lambda: None)" in notebook_cells
    assert "tof_schedule.add_resource(hw_opts)" in notebook_cells


def test_run_probe_projects_builds_analyze_commands_and_writes_report(tmp_path: Path) -> None:
    tool = _load_probe_tool()
    first = tmp_path / "probes" / "first" / "qbstimeline.yml"
    second = tmp_path / "probes" / "second" / "qbstimeline.yml"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("schedule:\n  file: schedule.py\n", encoding="utf-8")
    second.write_text("schedule:\n  file: schedule.py\n", encoding="utf-8")
    manifest_path = tmp_path / "probe_manifest.json"
    manifest_path.write_text(
        json.dumps({"probes": [{"project_file": str(first)}, {"project_file": str(second)}]}),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_runner(command, *, cwd, timeout):
        calls.append([*command, f"cwd={cwd}", f"timeout={timeout}"])
        return tool.CompletedProbeRun(exit_code=0, stdout="ok", stderr="", duration_seconds=0.01)

    report = tool.run_probe_projects(manifest_path, timeout_seconds=7, runner=fake_runner)

    assert len(report["runs"]) == 2
    assert calls[0][:3] == [tool.sys.executable, "-m", "qbstimeline"]
    assert calls[0][3:5] == ["analyze", "--project"]
    assert calls[0][-2:] == [f"cwd={first.parent}", "timeout=7"]
    written = (manifest_path.parent / "probe_run_report.json").read_text(encoding="utf-8")
    assert '"exit_code": 0' in written


def test_run_probe_projects_removes_stale_ir_and_html_before_analyze(tmp_path: Path) -> None:
    tool = _load_probe_tool()
    project = tmp_path / "probes" / "stale" / "qbstimeline.yml"
    timeline_dir = project.parent / ".qbs_timeline"
    timeline_dir.mkdir(parents=True)
    project.write_text("schedule:\n  file: schedule.py\n", encoding="utf-8")
    (timeline_dir / "qbs_ir.json").write_text('{"stale": true}', encoding="utf-8")
    (timeline_dir / "index.html").write_text("<html>stale</html>", encoding="utf-8")
    manifest_path = tmp_path / "probe_manifest.json"
    manifest_path.write_text(
        json.dumps({"probes": [{"project_file": str(project)}]}),
        encoding="utf-8",
    )

    def fake_runner(command, *, cwd, timeout):
        return tool.CompletedProbeRun(exit_code=124, stdout="", stderr="timeout", duration_seconds=1.0)

    tool.run_probe_projects(manifest_path, timeout_seconds=7, runner=fake_runner)

    assert not (timeline_dir / "qbs_ir.json").exists()
    assert not (timeline_dir / "index.html").exists()


def test_run_probe_projects_skips_probes_without_schedule_candidates(tmp_path: Path) -> None:
    tool = _load_probe_tool()
    project = tmp_path / "probes" / "setup_only" / "qbstimeline.yml"
    project.parent.mkdir(parents=True)
    project.write_text("schedule:\n  file: schedule.py\n", encoding="utf-8")
    manifest_path = tmp_path / "probe_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "probes": [
                    {
                        "project_file": str(project),
                        "notebook": "setup.ipynb",
                        "analysis": {"schedule_candidates": []},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_runner(command, *, cwd, timeout):
        calls.append(list(command))
        return tool.CompletedProbeRun(exit_code=0, stdout="unexpected", stderr="", duration_seconds=0.01)

    report = tool.run_probe_projects(manifest_path, timeout_seconds=7, runner=fake_runner)

    assert calls == []
    assert report["ok_count"] == 0
    assert report["skip_count"] == 1
    assert report["fail_count"] == 0
    assert report["runs"][0]["skipped"] is True
