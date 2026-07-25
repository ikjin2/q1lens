from __future__ import annotations

from pathlib import Path

import pytest

from qbstimeline.project import ConfigLoadError, load_project_config


def test_load_project_config_resolves_paths_and_defaults(tmp_path: Path) -> None:
    project = tmp_path / "demo" / "qbstimeline.yml"
    project.parent.mkdir()
    project.write_text(
        """
schedule:
  file: schedule.py
""".lstrip(),
        encoding="utf-8",
    )

    config = load_project_config(project)

    assert config.root == project.parent
    assert config.schedule_file == project.parent / "schedule.py"
    assert config.schedule_entrypoint == "build_schedule"
    assert config.compiler_entrypoint == "build_compiler"
    assert config.output_dir == project.parent / ".qbs_timeline"
    assert config.low_level_q1timeline is True


def test_load_project_config_uses_explicit_values(tmp_path: Path) -> None:
    project = tmp_path / "qbstimeline.yml"
    project.write_text(
        """
schedule:
  file: src/my_schedule.py
  entrypoint: make_schedule
  compiler: make_compiler
outputs:
  dir: build/qbs
low_level:
  q1timeline: false
""".lstrip(),
        encoding="utf-8",
    )

    config = load_project_config(project)

    assert config.schedule_file == tmp_path / "src" / "my_schedule.py"
    assert config.schedule_entrypoint == "make_schedule"
    assert config.compiler_entrypoint == "make_compiler"
    assert config.output_dir == tmp_path / "build" / "qbs"
    assert config.low_level_q1timeline is False


def test_load_project_config_disables_native_artifacts_by_default(tmp_path: Path) -> None:
    project = tmp_path / "qbstimeline.yml"
    project.write_text("schedule:\n  file: schedule.py\n", encoding="utf-8")

    config = load_project_config(project)

    assert config.artifacts_circuit_diagram is False
    assert config.artifacts_analog_pulse_diagram is False


def test_load_project_config_reads_artifact_opt_in(tmp_path: Path) -> None:
    project = tmp_path / "qbstimeline.yml"
    project.write_text(
        """
schedule:
  file: schedule.py
artifacts:
  circuit_diagram: true
  analog_pulse_diagram: true
""".lstrip(),
        encoding="utf-8",
    )

    config = load_project_config(project)

    assert config.artifacts_circuit_diagram is True
    assert config.artifacts_analog_pulse_diagram is True


def test_load_project_config_accepts_generated_schedule_with_source_notebook(tmp_path: Path) -> None:
    project = tmp_path / "qbstimeline.yml"
    project.write_text(
        """
schedule:
  file: schedule.py
  entrypoint: build_schedule
  compiler: build_compiler
source:
  notebook: examples/050_qubit_spectroscopy.ipynb
outputs:
  dir: .qbs_timeline
""".lstrip(),
        encoding="utf-8",
    )

    config = load_project_config(project)

    assert config.schedule_file == (tmp_path / "schedule.py").resolve()
    assert config.source_notebook == (
        tmp_path / "examples" / "050_qubit_spectroscopy.ipynb"
    ).resolve()
    assert config.notebook_schedule is None


def test_load_project_config_accepts_direct_notebook_schedule(tmp_path: Path) -> None:
    project = tmp_path / "qbstimeline.yml"
    project.write_text(
        """
schedule:
  notebook: experiments/tuneup.ipynb
  setup_tags:
    - qbstimeline-setup
  schedule_tag: qbstimeline-schedule
  schedule_variable: two_tone_sched
  compiler_variable: hw_agent
outputs:
  dir: .qbs_timeline
""".lstrip(),
        encoding="utf-8",
    )

    config = load_project_config(project)

    assert config.schedule_file is None
    assert config.notebook_schedule is not None
    assert config.notebook_schedule.notebook == (
        tmp_path / "experiments" / "tuneup.ipynb"
    ).resolve()
    assert config.notebook_schedule.setup_tags == ("qbstimeline-setup",)
    assert config.notebook_schedule.schedule_tag == "qbstimeline-schedule"
    assert config.notebook_schedule.schedule_variable == "two_tone_sched"
    assert config.notebook_schedule.compiler_variable == "hw_agent"
    assert config.source_notebook == (tmp_path / "experiments" / "tuneup.ipynb").resolve()


def test_load_project_config_rejects_missing_schedule_file_key(tmp_path: Path) -> None:
    project = tmp_path / "qbstimeline.yml"
    project.write_text("schedule: {}\n", encoding="utf-8")

    with pytest.raises(ConfigLoadError, match="schedule.file or schedule.notebook"):
        load_project_config(project)


def test_load_project_config_rejects_missing_file_and_notebook(tmp_path: Path) -> None:
    project = tmp_path / "qbstimeline.yml"
    project.write_text("schedule:\n  entrypoint: build_schedule\n", encoding="utf-8")

    with pytest.raises(ConfigLoadError, match="schedule.file or schedule.notebook"):
        load_project_config(project)


def test_load_project_config_rejects_file_and_notebook_together(tmp_path: Path) -> None:
    project = tmp_path / "qbstimeline.yml"
    project.write_text(
        """
schedule:
  file: schedule.py
  notebook: experiments/tuneup.ipynb
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigLoadError, match="schedule.file or schedule.notebook"):
        load_project_config(project)
