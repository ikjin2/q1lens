from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.interpreter import AnalysisState, interpret_program
from q1timeline.analysis.underflow import analyze_underflow
from q1timeline.diagnostics import Diagnostic, has_fatal_diagnostics
from q1timeline.ir.serialize import diagnostics_to_json, timeline_ir_from_states, write_timeline_ir
from q1timeline.project import ConfigLoadError, ProjectConfig, load_project_config
from q1timeline.q1asm.parser import parse_q1asm_file
from q1timeline.render.html import render_html
from q1timeline.sequence import load_sequence_names


@dataclass(frozen=True)
class WatchRunResult:
    ok: bool
    timeline_ir: Path
    diagnostics: Path
    html: Path
    status: Path
    diagnostic_count: int
    message: str


@dataclass
class DebouncedChangeTracker:
    current_snapshot: dict[str, int | None]
    debounce_seconds: float
    pending_snapshot: dict[str, int | None] | None = None
    pending_since: float | None = None

    def update(self, snapshot: dict[str, int | None], *, now: float) -> bool:
        if snapshot == self.current_snapshot:
            self.pending_snapshot = None
            self.pending_since = None
            return False

        if self.debounce_seconds <= 0:
            self.current_snapshot = dict(snapshot)
            self.pending_snapshot = None
            self.pending_since = None
            return True

        if snapshot != self.pending_snapshot:
            self.pending_snapshot = dict(snapshot)
            self.pending_since = now
            return False

        if self.pending_since is not None and now - self.pending_since >= self.debounce_seconds:
            self.current_snapshot = dict(snapshot)
            self.pending_snapshot = None
            self.pending_since = None
            return True

        return False


def collect_watch_paths(project_file: str | Path) -> set[Path]:
    project_path = Path(project_file).resolve()
    try:
        config = load_project_config(project_path)
    except ConfigLoadError:
        return _collect_project_reference_paths(project_path)

    paths = {project_path}
    for sequencer in config.sequencers:
        paths.add(sequencer.file.resolve())
        if sequencer.sequence_json is not None:
            paths.add(sequencer.sequence_json.resolve())
    if config.params_file is not None:
        paths.add(config.params_file.resolve())
    if config.display_file is not None:
        paths.add(config.display_file.resolve())
    return paths


def _collect_project_reference_paths(project_path: Path) -> set[Path]:
    paths = {project_path}
    try:
        loaded = yaml.safe_load(project_path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return paths
    if not isinstance(loaded, dict):
        return paths

    sequencers = loaded.get("sequencers")
    if isinstance(sequencers, list):
        for sequencer in sequencers:
            if not isinstance(sequencer, dict):
                continue
            for key in ("file", "sequence_json"):
                value = sequencer.get(key)
                if isinstance(value, str) and value.strip():
                    paths.add(_resolve_project_reference(project_path, value))

    for section_name in ("params", "display"):
        section = loaded.get(section_name)
        if not isinstance(section, dict):
            continue
        value = section.get("file")
        if isinstance(value, str) and value.strip():
            paths.add(_resolve_project_reference(project_path, value))

    return paths


def _resolve_project_reference(project_path: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project_path.parent / path
    return path.resolve()


def run_watch_once(project_file: str | Path, out_dir: str | Path) -> WatchRunResult:
    project_path = Path(project_file)
    output_dir = Path(out_dir)
    ir_path = output_dir / "timeline_ir.json"
    diagnostics_path = output_dir / "diagnostics.json"
    html_path = output_dir / "timeline.html"
    status_path = output_dir / "status.json"
    if output_dir.exists() and not output_dir.is_dir():
        diagnostic = Diagnostic(
            severity="error",
            category="analysis_incomplete",
            message=f"Watch run failed: output directory is not a directory: {output_dir}",
        )
        return WatchRunResult(
            ok=False,
            timeline_ir=ir_path,
            diagnostics=diagnostics_path,
            html=html_path,
            status=status_path,
            diagnostic_count=1,
            message=diagnostic.message,
        )
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        diagnostic = Diagnostic(
            severity="error",
            category="analysis_incomplete",
            message=f"Watch run failed: could not create output directory {output_dir}: {exc}",
        )
        return WatchRunResult(
            ok=False,
            timeline_ir=ir_path,
            diagnostics=diagnostics_path,
            html=html_path,
            status=status_path,
            diagnostic_count=1,
            message=diagnostic.message,
        )

    leaf_diagnostic = _output_leaf_directory_diagnostic((ir_path, diagnostics_path, html_path, status_path))
    if leaf_diagnostic is not None:
        return _write_failed_run(
            [leaf_diagnostic],
            message=leaf_diagnostic.message,
            ir_path=ir_path,
            diagnostics_path=diagnostics_path,
            html_path=html_path,
            status_path=status_path,
        )

    try:
        config = load_project_config(project_path)
        states = _analyze_config(config)
        align_timelines(
            states,
            mode=config.alignment_mode,
            anchor_kinds=config.alignment_anchor_kinds,
        )
        diagnostics = [*config.diagnostics, *(diagnostic for state in states for diagnostic in state.diagnostics)]
        ir = timeline_ir_from_states(
            states,
            project={
                "root": str(config.root),
                "alignment_mode": config.alignment_mode,
                "alignment_anchor_kinds": list(config.alignment_anchor_kinds),
                "display_file": str(config.display_file) if config.display_file else None,
                "display": config.display,
            },
            diagnostics=diagnostics,
        )
        if has_fatal_diagnostics(diagnostics):
            error_diagnostic = next(diagnostic for diagnostic in diagnostics if diagnostic.severity == "error")
            return _write_failed_run(
                diagnostics,
                message=error_diagnostic.message,
                ir_path=ir_path,
                diagnostics_path=diagnostics_path,
                html_path=html_path,
                status_path=status_path,
            )
        write_timeline_ir(ir, ir_path)
        diagnostics_path.write_text(diagnostics_to_json(diagnostics), encoding="utf-8")
        html_path.write_text(render_html(ir, default_mode=config.view.default_mode), encoding="utf-8")
        return _finish(
            ok=True,
            message=f"updated {html_path}",
            diagnostics=diagnostics,
            ir_path=ir_path,
            diagnostics_path=diagnostics_path,
            html_path=html_path,
            status_path=status_path,
        )
    except ConfigLoadError as exc:
        return _write_failed_run(
            exc.diagnostics,
            message=exc.diagnostics[0].message if exc.diagnostics else str(exc),
            ir_path=ir_path,
            diagnostics_path=diagnostics_path,
            html_path=html_path,
            status_path=status_path,
        )
    except Exception as exc:
        diagnostic = Diagnostic(
            severity="error",
            category="analysis_incomplete",
            message=f"Watch run failed: {exc}",
        )
        return _write_failed_run(
            [diagnostic],
            message=diagnostic.message,
            ir_path=ir_path,
            diagnostics_path=diagnostics_path,
            html_path=html_path,
            status_path=status_path,
        )


def watch_project(
    project_file: str | Path,
    out_dir: str | Path,
    *,
    debounce_seconds: float = 0.3,
    poll_interval_seconds: float = 0.1,
) -> None:
    result = run_watch_once(project_file, out_dir)
    print(_status_line(result))
    tracker = DebouncedChangeTracker(
        _snapshot_paths(collect_watch_paths(project_file)),
        debounce_seconds=debounce_seconds,
    )
    while True:
        time.sleep(poll_interval_seconds)
        snapshot = _snapshot_paths(collect_watch_paths(project_file))
        if tracker.update(snapshot, now=time.monotonic()):
            result = run_watch_once(project_file, out_dir)
            print(_status_line(result))


def _analyze_config(config: ProjectConfig) -> list[AnalysisState]:
    states = []
    for sequencer in config.sequencers:
        sequence_names = load_sequence_names(sequencer.sequence_json) if sequencer.sequence_json else None
        config.diagnostics.extend(sequence_names.diagnostics if sequence_names else [])
        program = parse_q1asm_file(sequencer.file)
        state = interpret_program(
            program,
            sequencer_id=sequencer.id,
            params=config.params,
            waveform_names=sequence_names.waveforms if sequence_names else None,
            acquisition_names=sequence_names.acquisitions if sequence_names else None,
            branch_policy=config.analysis.branch_policy,
        )
        analyze_underflow(state)
        states.append(state)
    return states


def _snapshot_paths(paths: set[Path]) -> dict[str, int | None]:
    snapshot: dict[str, int | None] = {}
    for path in sorted(paths):
        try:
            snapshot[str(path)] = path.stat().st_mtime_ns
        except FileNotFoundError:
            snapshot[str(path)] = None
    return snapshot


def _status_line(result: WatchRunResult) -> str:
    status = "updated" if result.ok else "failed"
    return f"watch {status}: {result.message} ({result.diagnostic_count} diagnostic(s))"


def _output_leaf_directory_diagnostic(paths: tuple[Path, ...]) -> Diagnostic | None:
    for path in paths:
        if path.is_dir():
            return Diagnostic(
                severity="error",
                category="analysis_incomplete",
                message=f"Watch run failed: output path is a directory: {path}",
                details={"path": str(path), "reason": "output_path_is_directory"},
            )
    return None


def _write_failed_run(
    diagnostics: list[Diagnostic],
    *,
    message: str,
    ir_path: Path,
    diagnostics_path: Path,
    html_path: Path,
    status_path: Path,
) -> WatchRunResult:
    _write_text_best_effort(diagnostics_path, diagnostics_to_json(diagnostics))
    return _finish(
        ok=False,
        message=message,
        diagnostics=diagnostics,
        ir_path=ir_path,
        diagnostics_path=diagnostics_path,
        html_path=html_path,
        status_path=status_path,
        allow_status_write_error=True,
    )


def _finish(
    *,
    ok: bool,
    message: str,
    diagnostics: list[Diagnostic],
    ir_path: Path,
    diagnostics_path: Path,
    html_path: Path,
    status_path: Path,
    allow_status_write_error: bool = False,
) -> WatchRunResult:
    status: dict[str, Any] = {
        "ok": ok,
        "message": message,
        "diagnostic_count": len(diagnostics),
        "diagnostics": str(diagnostics_path),
    }
    if ok:
        status["timeline_ir"] = str(ir_path)
        status["html"] = str(html_path)
    elif ir_path.exists():
        status["stale_timeline_ir"] = str(ir_path)
    if not ok and html_path.exists():
        status["stale_html"] = str(html_path)
    try:
        status_path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        if not allow_status_write_error:
            raise
    return WatchRunResult(
        ok=ok,
        timeline_ir=ir_path,
        diagnostics=diagnostics_path,
        html=html_path,
        status=status_path,
        diagnostic_count=len(diagnostics),
        message=message,
    )


def _write_text_best_effort(path: Path, text: str) -> None:
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        pass
