from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from qbstimeline.artifacts import generate_native_artifacts


class FakeFigure:
    def __init__(self, content: str) -> None:
        self.content = content

    def savefig(self, path: Path, format: str) -> None:
        path.write_text(f"{format}:{self.content}", encoding="utf-8")


class FakeSchedule:
    def plot_circuit_diagram(self) -> FakeFigure:
        return FakeFigure("circuit")


class FakeCompiledSchedule:
    def plot_pulse_diagram(self) -> FakeFigure:
        return FakeFigure("pulse")


def test_generate_native_artifacts_is_empty_when_disabled(tmp_path: Path) -> None:
    config = SimpleNamespace(
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=False,
    )
    stale = tmp_path / "artifacts" / "circuit.svg"
    stale.parent.mkdir(parents=True)
    stale.write_text("old circuit", encoding="utf-8")

    artifacts, warnings = generate_native_artifacts(
        schedule=FakeSchedule(),
        compiled_schedule=FakeCompiledSchedule(),
        output_dir=tmp_path,
        config=config,
    )

    assert artifacts == {}
    assert warnings == []
    assert not stale.exists()


def test_generate_native_artifacts_writes_opted_in_svgs(tmp_path: Path) -> None:
    config = SimpleNamespace(
        artifacts_circuit_diagram=True,
        artifacts_analog_pulse_diagram=True,
    )

    artifacts, warnings = generate_native_artifacts(
        schedule=FakeSchedule(),
        compiled_schedule=FakeCompiledSchedule(),
        output_dir=tmp_path,
        config=config,
    )

    assert artifacts == {
        "circuit_diagram": {
            "status": "ok",
            "type": "svg",
            "file": "artifacts/circuit.svg",
        },
        "analog_pulse_diagram": {
            "status": "ok",
            "type": "svg",
            "file": "artifacts/pulse.svg",
        },
    }
    assert warnings == []
    assert (tmp_path / "artifacts" / "circuit.svg").read_text(encoding="utf-8") == "svg:circuit"
    assert (tmp_path / "artifacts" / "pulse.svg").read_text(encoding="utf-8") == "svg:pulse"


def test_generate_native_artifacts_records_plot_errors(tmp_path: Path) -> None:
    class BrokenCompiledSchedule:
        def plot_pulse_diagram(self) -> FakeFigure:
            raise RuntimeError("matplotlib backend missing")

    config = SimpleNamespace(
        artifacts_circuit_diagram=False,
        artifacts_analog_pulse_diagram=True,
    )

    artifacts, warnings = generate_native_artifacts(
        schedule=FakeSchedule(),
        compiled_schedule=BrokenCompiledSchedule(),
        output_dir=tmp_path,
        config=config,
    )

    assert artifacts["analog_pulse_diagram"]["status"] == "error"
    assert "matplotlib backend missing" in artifacts["analog_pulse_diagram"]["error"]
    assert warnings == ["analog_pulse_diagram artifact failed: matplotlib backend missing"]
