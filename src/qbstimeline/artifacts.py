from __future__ import annotations

from pathlib import Path
from typing import Any


def generate_native_artifacts(
    *,
    schedule: Any,
    compiled_schedule: Any,
    output_dir: Path,
    config: Any,
) -> tuple[dict[str, Any], list[str]]:
    artifacts: dict[str, Any] = {}
    warnings: list[str] = []
    artifact_targets = {
        "circuit_diagram": Path("artifacts/circuit.svg"),
        "analog_pulse_diagram": Path("artifacts/pulse.svg"),
    }
    if getattr(config, "artifacts_circuit_diagram", False):
        _record_artifact(
            artifacts=artifacts,
            warnings=warnings,
            key="circuit_diagram",
            relative_file=artifact_targets["circuit_diagram"],
            plot_owner=schedule,
            method_name="plot_circuit_diagram",
            output_dir=output_dir,
        )
    else:
        _remove_artifact(output_dir / artifact_targets["circuit_diagram"])
    if getattr(config, "artifacts_analog_pulse_diagram", False):
        _record_artifact(
            artifacts=artifacts,
            warnings=warnings,
            key="analog_pulse_diagram",
            relative_file=artifact_targets["analog_pulse_diagram"],
            plot_owner=compiled_schedule,
            method_name="plot_pulse_diagram",
            output_dir=output_dir,
        )
    else:
        _remove_artifact(output_dir / artifact_targets["analog_pulse_diagram"])
    return artifacts, warnings


def _record_artifact(
    *,
    artifacts: dict[str, Any],
    warnings: list[str],
    key: str,
    relative_file: Path,
    plot_owner: Any,
    method_name: str,
    output_dir: Path,
) -> None:
    target = output_dir / relative_file
    _remove_artifact(target)
    try:
        method = getattr(plot_owner, method_name)
        figure = method()
        target.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(figure, "savefig"):
            figure.savefig(target, format="svg")
        else:
            target.write_text(str(figure), encoding="utf-8")
        artifacts[key] = {
            "status": "ok",
            "type": "svg",
            "file": relative_file.as_posix(),
        }
    except Exception as exc:
        message = str(exc)
        artifacts[key] = {"status": "error", "error": message}
        warnings.append(f"{key} artifact failed: {message}")


def _remove_artifact(path: Path) -> None:
    path.unlink(missing_ok=True)
