from __future__ import annotations

import argparse
import json
import math
import sys
import uuid
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from q1timeline import __version__
from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.interpreter import LOOP_PREVIEW_MAX_VISIBLE_ITERATIONS, interpret_program
from q1timeline.analysis.underflow import analyze_underflow
from q1timeline.diagnostics import (
    DIAGNOSTICS_SCHEMA_VERSION,
    Diagnostic,
    format_diagnostic,
    format_summary,
    has_fatal_diagnostics,
    has_strict_failure,
)
from q1timeline.ir.serialize import diagnostics_to_json, timeline_ir_from_states, write_timeline_ir
from q1timeline.project import (
    ConfigLoadError,
    load_project_config,
    load_single_file_config,
    validate_params_json_mapping,
    validate_yaml_mapping_values,
)
from q1timeline.q1asm.ast import SourceLocation
from q1timeline.q1asm.parser import parse_q1asm_file
from q1timeline.render.html import RenderError, render_ir_file
from q1timeline.sequence import load_sequence_names
from q1timeline.watch.watcher import run_watch_once, watch_project


DESCRIPTION = "Q1ASM Live Timeline Debugger"
VSCODE_JSON_SCHEMA_VERSION = "0.2.0"
VALID_BRANCH_ASSUMPTION_PATHS = {"collapsed", "taken", "fallthrough", "both"}


def _render_command(args: argparse.Namespace) -> int:
    if not args.ir or not args.out:
        print("q1timeline render: provide --ir and --out", file=sys.stderr)
        return 2
    try:
        render_ir_file(args.ir, args.out, mode=args.mode)
    except (OSError, json.JSONDecodeError, UnicodeEncodeError, RenderError) as exc:
        print(f"q1timeline render: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote HTML timeline: {args.out}")
    if args.verbose:
        print(f"Render mode: {args.mode}")
    if args.open:
        webbrowser.open(Path(args.out).resolve().as_uri())
    return 0


def _watch_command(args: argparse.Namespace) -> int:
    if not args.project:
        print("q1timeline watch: provide --project", file=sys.stderr)
        return 2
    for option, value in (("--debounce-ms", args.debounce_ms), ("--poll-ms", args.poll_ms)):
        if value < 0:
            print(f"q1timeline watch: {option} must be non-negative", file=sys.stderr)
            return 2
    out_dir = args.out_dir or Path(".q1timeline")
    if args.once:
        result = run_watch_once(args.project, out_dir)
        stream = sys.stdout if result.ok else sys.stderr
        print(result.message, file=stream)
        if result.diagnostics.exists():
            print(f"Wrote diagnostics: {result.diagnostics}", file=stream)
        if result.ok:
            print(f"Wrote TimelineIR: {result.timeline_ir}", file=stream)
            print(f"Wrote HTML timeline: {result.html}", file=stream)
            return 0
        return 2

    print(f"Watching {args.project} -> {out_dir}")
    try:
        watch_project(
            args.project,
            out_dir,
            debounce_seconds=args.debounce_ms / 1000,
            poll_interval_seconds=args.poll_ms / 1000,
        )
    except KeyboardInterrupt:
        print("Stopped watch mode")
    return 0


def _parse_branch_assumptions(items: list[str] | None) -> dict[str, str]:
    assumptions: dict[str, str] = {}
    for item in items or []:
        branch_id, separator, path = item.rpartition("=")
        if not separator or not branch_id.strip() or path not in VALID_BRANCH_ASSUMPTION_PATHS:
            diagnostic = Diagnostic(
                severity="error",
                category="invalid_branch_policy",
                message=f"Invalid branch assumption: {item}",
            )
            raise ConfigLoadError(diagnostic.message, [diagnostic])
        assumptions[branch_id] = path
    return assumptions


def _parse_loop_preview_counts(items: list[str] | None) -> dict[str, int]:
    preview_counts: dict[str, int] = {}
    for item in items or []:
        loop_key, separator, raw_count = item.rpartition("=")
        if not separator or not loop_key.strip():
            diagnostic = Diagnostic(
                severity="error",
                category="invalid_loop_preview",
                message=f"Invalid loop preview request: {item}",
            )
            raise ConfigLoadError(diagnostic.message, [diagnostic])
        try:
            count = int(raw_count)
        except ValueError as exc:
            diagnostic = Diagnostic(
                severity="error",
                category="invalid_loop_preview",
                message=f"Invalid loop preview count: {item}",
            )
            raise ConfigLoadError(diagnostic.message, [diagnostic]) from exc
        if count < 1 or count > LOOP_PREVIEW_MAX_VISIBLE_ITERATIONS:
            diagnostic = Diagnostic(
                severity="error",
                category="invalid_loop_preview",
                message=(
                    f"Loop preview count must be between 1 and {LOOP_PREVIEW_MAX_VISIBLE_ITERATIONS}: {item}"
                ),
            )
            raise ConfigLoadError(diagnostic.message, [diagnostic])
        preview_counts[loop_key] = count
    return preview_counts


def _print_diagnostic_report(diagnostics: list[Diagnostic]) -> None:
    print(format_summary(diagnostics))
    for diagnostic in diagnostics:
        if diagnostic.severity in {"error", "warning"}:
            print(format_diagnostic(diagnostic))


def _diagnostics_payload(diagnostics: list[Diagnostic]) -> list[dict]:
    return json.loads(diagnostics_to_json(diagnostics))


def _vscode_error_payload(kind: str, message: str, diagnostics: list[Diagnostic] | None = None) -> dict:
    return {
        "schema_version": VSCODE_JSON_SCHEMA_VERSION,
        "diagnostics_schema_version": DIAGNOSTICS_SCHEMA_VERSION,
        "analysis_id": str(uuid.uuid4()),
        "time_unit": "ns",
        "core_version": __version__,
        "status": "error",
        "error": {
            "kind": kind,
            "message": message,
        },
        "diagnostics": _diagnostics_payload(diagnostics or []),
    }


def _print_vscode_payload(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))


def _global_time_range(ir: dict | None) -> dict[str, int]:
    if not ir:
        return {"t0": 0, "t1": 0}
    bounds = [
        value
        for event in ir.get("events", [])
        if isinstance(event, dict)
        for value in (event.get("meta", {}).get("aligned_t0"), event.get("meta", {}).get("aligned_t1"))
        if isinstance(value, int)
    ]
    if not bounds:
        return {"t0": 0, "t1": 0}
    return {"t0": min(bounds), "t1": max(bounds)}


def _lanes(ir: dict | None) -> list[str]:
    if not ir:
        return []
    return sorted(
        {
            event["lane"]
            for event in ir.get("events", [])
            if isinstance(event, dict) and isinstance(event.get("lane"), str)
        }
    )


def _loops(ir: dict | None) -> list[dict]:
    if not ir:
        return []
    return [
        event
        for event in ir.get("events", [])
        if isinstance(event, dict) and event.get("kind") == "loop_block"
    ]


def _vscode_analyzer_payload(
    *,
    args: argparse.Namespace,
    config,
    states,
    ir: dict | None,
    diagnostics: list[Diagnostic],
) -> dict:
    fatal = has_fatal_diagnostics(diagnostics)
    strict_failure = args.strict and has_strict_failure(diagnostics)
    error_diagnostic = next((diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"), None)
    payload = {
        "schema_version": VSCODE_JSON_SCHEMA_VERSION,
        "diagnostics_schema_version": DIAGNOSTICS_SCHEMA_VERSION,
        "analysis_id": str(uuid.uuid4()),
        "time_unit": "ns",
        "core_version": __version__,
        "project_root": str(config.root),
        "mode": args.mode,
        "alignment_policy": args.align or config.alignment_mode,
        "alignment_anchor_kinds": list(_alignment_anchor_kinds(args, config)),
        "global_time_range": _global_time_range(ir),
        "status": "error" if fatal or strict_failure else "ok",
        "diagnostics": _diagnostics_payload(diagnostics) if args.include_diagnostics or fatal or strict_failure else [],
        "stats": {
            "event_count": len(ir["events"]) if ir else 0,
            "sequencer_count": len(states),
        },
    }
    if not args.summary_only:
        payload.update(
            {
                "sequencers": ir.get("sequencers", []) if ir else [],
                "lanes": _lanes(ir),
                "events": ir.get("events", []) if ir else [],
                "loops": _loops(ir),
            }
        )
    if fatal and error_diagnostic is not None:
        payload["error"] = {
            "kind": error_diagnostic.category,
            "message": error_diagnostic.message,
        }
    elif strict_failure:
        payload["error"] = {
            "kind": "strict_failure",
            "message": "Strict mode failed because diagnostics include warnings.",
        }
    if args.include_timeline_ir and ir is not None:
        payload["timeline_ir"] = ir
    if args.include_source_map and ir is not None:
        payload["source_map"] = ir.get("source_map", {})
    return payload


def _alignment_anchor_kinds(args: argparse.Namespace, config) -> tuple[str, ...]:
    anchor_kinds, _invalid_items = _alignment_anchor_kinds_with_invalids(args, config)
    return anchor_kinds


def _alignment_anchor_kinds_with_invalids(
    args: argparse.Namespace,
    config,
) -> tuple[tuple[str, ...], list[Any]]:
    cli_anchor_kinds = getattr(args, "align_anchor_kinds", None)
    raw_anchor_kinds = cli_anchor_kinds if cli_anchor_kinds is not None else config.alignment_anchor_kinds
    return _normalize_alignment_anchor_kinds(raw_anchor_kinds)


def _normalize_alignment_anchor_kinds(raw_anchor_kinds: Any) -> tuple[tuple[str, ...], list[Any]]:
    if raw_anchor_kinds in (None, ""):
        return (), []
    if isinstance(raw_anchor_kinds, str):
        raw_items = [raw_anchor_kinds]
    else:
        try:
            raw_items = list(raw_anchor_kinds)
        except TypeError:
            raw_items = [raw_anchor_kinds]

    anchor_kinds: list[str] = []
    invalid_items: list[Any] = []
    for item in raw_items:
        if not isinstance(item, str) or not item.strip():
            invalid_items.append(item)
            continue
        anchor_kinds.append(item.strip())
    return tuple(dict.fromkeys(anchor_kinds)), invalid_items


def _is_valid_alignment_mode(mode: str) -> bool:
    if mode in {
        "first_wait_sync",
        "first_wait_trigger",
        "first_anchor",
        "first_marker_rise",
        "first_play",
        "first_acquire",
        "none",
    }:
        return True
    if mode.startswith("label:"):
        return bool(mode.split(":", 1)[1].strip())
    if mode.startswith("manual:"):
        try:
            int(mode.split(":", 1)[1])
        except ValueError:
            return False
        return True
    return False


def _invalid_alignment_diagnostic(mode: str) -> Diagnostic:
    return Diagnostic(
        severity="error",
        category="invalid_alignment_policy",
        message=f"Invalid alignment policy: {mode}",
        source=SourceLocation(file="<cli>", line=1, column=1, raw=f"--align {mode}"),
        details={"alignment_mode": mode},
    )


def _invalid_alignment_anchor_kinds_diagnostic(anchor_kinds: tuple[str, ...], invalid_items: list[Any]) -> Diagnostic:
    if invalid_items and anchor_kinds:
        message = "alignment.anchor_kinds entries must be non-empty strings."
    else:
        message = "alignment.anchor_kinds must list at least one event kind when alignment.mode is first_anchor."
    details: dict[str, Any] = {
        "alignment_mode": "first_anchor",
        "anchor_kinds": list(anchor_kinds),
    }
    if invalid_items:
        details["invalid_items"] = [str(item) for item in invalid_items]
    return Diagnostic(
        severity="error",
        category="invalid_alignment_anchor_kinds",
        message=message,
        source=SourceLocation(file="<cli>", line=1, column=1, raw="--align first_anchor"),
        details=details,
    )


def _override_parse_diagnostic(path: Path, category: str, message: str) -> Diagnostic:
    resolved = path.resolve()
    return Diagnostic(
        severity="error",
        category=category,
        message=message,
        source=SourceLocation(file=str(resolved), line=1, column=1, raw=""),
        details={"file": str(resolved)},
    )


def _override_shape_diagnostic(path: Path, category: str, label: str, loaded) -> Diagnostic:
    resolved = path.resolve()
    raw = ""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        raw = lines[0] if lines else ""
    except (OSError, UnicodeDecodeError):
        pass
    syntax_name = "JSON" if category == "invalid_json" else "YAML"
    return Diagnostic(
        severity="error",
        category=category,
        message=(
            f"Invalid {syntax_name} in {resolved}: top-level {label} config must be a mapping/object, "
            f"got {type(loaded).__name__}."
        ),
        source=SourceLocation(file=str(resolved), line=1, column=1, raw=raw),
        details={"file": str(resolved), "kind": label},
    )


def _override_path_diagnostic(path: Path, label: str, exc: OSError) -> Diagnostic:
    resolved = path.resolve()
    missing = isinstance(exc, FileNotFoundError) or not path.exists()
    category = "missing_required_file" if missing else "invalid_config_path"
    if missing:
        message = f"Required {label} file does not exist: {resolved}"
    else:
        message = f"Configured {label} path is not a readable file: {resolved}"
    return Diagnostic(
        severity="error",
        category=category,
        message=message,
        source=SourceLocation(file=str(resolved), line=1, column=1, raw=""),
        details={"file": str(resolved), "kind": label},
    )


def _load_json_override(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8-sig")
        loaded = json.loads(
            raw,
            parse_constant=_reject_non_finite_json_constant,
            parse_float=_parse_finite_json_float,
        )
    except json.JSONDecodeError as exc:
        diagnostic = _override_parse_diagnostic(path, "invalid_json", f"Invalid JSON in {path.resolve()}: {exc.msg}")
        raise ConfigLoadError(diagnostic.message, [diagnostic]) from exc
    except ValueError as exc:
        diagnostic = _override_parse_diagnostic(path, "invalid_json", f"Invalid JSON in {path.resolve()}: {exc}")
        raise ConfigLoadError(diagnostic.message, [diagnostic]) from exc
    except UnicodeDecodeError as exc:
        diagnostic = _override_parse_diagnostic(path, "invalid_json", f"Invalid JSON in {path.resolve()}: {exc}")
        raise ConfigLoadError(diagnostic.message, [diagnostic]) from exc
    except OSError as exc:
        diagnostic = _override_path_diagnostic(path, "params", exc)
        raise ConfigLoadError(diagnostic.message, [diagnostic]) from exc
    if not isinstance(loaded, dict):
        diagnostic = _override_shape_diagnostic(path, "invalid_json", "params", loaded)
        raise ConfigLoadError(diagnostic.message, [diagnostic])
    try:
        validate_params_json_mapping(loaded)
    except ValueError as exc:
        diagnostic = _override_parse_diagnostic(path, "invalid_json", f"Invalid JSON in {path.resolve()}: {exc}")
        raise ConfigLoadError(diagnostic.message, [diagnostic]) from exc
    return loaded


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _parse_finite_json_float(value: str) -> int:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    if not parsed.is_integer():
        raise ValueError(f"non-integer JSON number: {value}")
    return int(parsed)


def _load_yaml_override(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8-sig")
        loaded = yaml.safe_load(raw)
        if loaded is None:
            loaded = {}
    except yaml.YAMLError as exc:
        problem = getattr(exc, "problem", None) or str(exc)
        diagnostic = _override_parse_diagnostic(path, "invalid_yaml", f"Invalid YAML in {path.resolve()}: {problem}")
        raise ConfigLoadError(diagnostic.message, [diagnostic]) from exc
    except UnicodeDecodeError as exc:
        diagnostic = _override_parse_diagnostic(path, "invalid_yaml", f"Invalid YAML in {path.resolve()}: {exc}")
        raise ConfigLoadError(diagnostic.message, [diagnostic]) from exc
    except OSError as exc:
        diagnostic = _override_path_diagnostic(path, "display", exc)
        raise ConfigLoadError(diagnostic.message, [diagnostic]) from exc
    if not isinstance(loaded, dict):
        diagnostic = _override_shape_diagnostic(path, "invalid_yaml", "display", loaded)
        raise ConfigLoadError(diagnostic.message, [diagnostic])
    validate_yaml_mapping_values(loaded, path.resolve(), "display")
    return loaded


def _analyze_command(args: argparse.Namespace) -> int:
    if args.stdin_overlay:
        message = "--stdin-overlay is reserved for future unsaved-buffer support and is not implemented yet."
        if args.format == "vscode-json":
            _print_vscode_payload(_vscode_error_payload("unsupported_option", message))
            return 2
        print(f"q1timeline analyze: {message}", file=sys.stderr)
        return 2
    if args.project and args.input_file:
        if args.format == "vscode-json":
            _print_vscode_payload(_vscode_error_payload("usage_error", "Use either --project or a Q1ASM file."))
            return 2
        print("q1timeline analyze: use either --project or a Q1ASM file", file=sys.stderr)
        return 2
    if not args.project and not args.input_file:
        if args.format == "vscode-json":
            _print_vscode_payload(_vscode_error_payload("usage_error", "Provide --project or a Q1ASM file."))
            return 2
        print("q1timeline analyze: provide --project or a Q1ASM file", file=sys.stderr)
        return 2
    if args.out and args.diagnostics and Path(args.out).resolve() == Path(args.diagnostics).resolve():
        message = f"--out and --diagnostics must be different files: {Path(args.out).resolve()}"
        if args.format == "vscode-json":
            _print_vscode_payload(_vscode_error_payload("usage_error", message))
            return 2
        print(f"q1timeline analyze: {message}", file=sys.stderr)
        return 2

    try:
        if args.project:
            config = load_project_config(args.project)
        else:
            config = load_single_file_config(args.input_file)
    except ConfigLoadError as exc:
        if args.format == "vscode-json":
            message = exc.diagnostics[0].message if exc.diagnostics else str(exc)
            _print_vscode_payload(_vscode_error_payload("config_error", message, exc.diagnostics))
            return 2
        for diagnostic in exc.diagnostics:
            print(f"{diagnostic.severity}: {diagnostic.message}", file=sys.stderr)
        return 2

    diagnostics = list(config.diagnostics)
    alignment_mode = args.align or config.alignment_mode
    alignment_anchor_kinds, invalid_alignment_anchor_items = _alignment_anchor_kinds_with_invalids(args, config)
    if not _is_valid_alignment_mode(alignment_mode):
        diagnostic = _invalid_alignment_diagnostic(alignment_mode)
        diagnostics.append(diagnostic)
        if args.format == "vscode-json":
            _print_vscode_payload(_vscode_error_payload("config_error", diagnostic.message, diagnostics))
            return 2
        print(format_diagnostic(diagnostic), file=sys.stderr)
        return 2
    if alignment_mode == "first_anchor" and (invalid_alignment_anchor_items or not alignment_anchor_kinds):
        diagnostic = _invalid_alignment_anchor_kinds_diagnostic(
            alignment_anchor_kinds,
            invalid_alignment_anchor_items,
        )
        diagnostics.append(diagnostic)
        if args.format == "vscode-json":
            _print_vscode_payload(_vscode_error_payload("config_error", diagnostic.message, diagnostics))
            return 2
        print(format_diagnostic(diagnostic), file=sys.stderr)
        return 2
    params = config.params
    display = config.display
    display_file = config.display_file
    try:
        branch_assumptions = _parse_branch_assumptions(args.branch_assumptions)
        loop_preview_counts = _parse_loop_preview_counts(args.loop_previews)
        if args.params:
            params = _load_json_override(args.params)
        if args.display:
            display = _load_yaml_override(args.display)
            display_file = args.display.resolve()
    except ConfigLoadError as exc:
        diagnostics.extend(exc.diagnostics)
        message = exc.diagnostics[0].message if exc.diagnostics else str(exc)
        if args.format == "vscode-json":
            _print_vscode_payload(_vscode_error_payload("config_error", message, diagnostics))
            return 2
        print(f"q1timeline analyze: {message}", file=sys.stderr)
        return 2
    except OSError as exc:
        if args.format == "vscode-json":
            _print_vscode_payload(_vscode_error_payload("config_error", str(exc), diagnostics))
            return 2
        print(f"q1timeline analyze: {exc}", file=sys.stderr)
        return 2
    if args.format != "vscode-json":
        print(f"Loaded project metadata: {len(config.sequencers)} sequencer(s)")
    states = []
    ir = None
    for sequencer in config.sequencers:
        sequence_names = load_sequence_names(sequencer.sequence_json) if sequencer.sequence_json else None
        if sequence_names is not None:
            diagnostics.extend(sequence_names.diagnostics)
        try:
            program = parse_q1asm_file(sequencer.file)
        except (OSError, UnicodeDecodeError) as exc:
            diagnostic = Diagnostic(
                severity="error",
                category="q1asm_read_error",
                message=f"Could not read Q1ASM file {sequencer.file}: {exc}",
                source=SourceLocation(file=str(sequencer.file), line=1, column=1, raw=""),
                details={"file": str(sequencer.file)},
            )
            diagnostics.append(diagnostic)
            if args.format == "vscode-json":
                _print_vscode_payload(_vscode_error_payload("q1asm_read_error", diagnostic.message, diagnostics))
                return 2
            print(format_diagnostic(diagnostic), file=sys.stderr)
            return 2
        state = interpret_program(
            program,
            sequencer_id=sequencer.id,
            params=params,
            waveform_names=sequence_names.waveforms if sequence_names else None,
            acquisition_names=sequence_names.acquisitions if sequence_names else None,
            branch_policy=config.analysis.branch_policy,
            branch_assumptions=branch_assumptions,
            loop_preview_counts=loop_preview_counts,
            strict_q1asm=args.q1asm_strict,
        )
        analyze_underflow(state)
        states.append(state)
    align_timelines(states, mode=alignment_mode, anchor_kinds=alignment_anchor_kinds)
    diagnostics.extend(diagnostic for state in states for diagnostic in state.diagnostics)
    ir = timeline_ir_from_states(
        states,
        project={
            "root": str(config.root),
            "alignment_mode": alignment_mode,
            "alignment_anchor_kinds": list(alignment_anchor_kinds),
            "display_file": str(display_file) if display_file else None,
            "display": display,
        },
        diagnostics=diagnostics,
    )
    output_target: Path | None = None
    try:
        if args.out:
            output_target = Path(args.out)
            write_timeline_ir(ir, args.out)
            if args.format != "vscode-json":
                print(f"Wrote TimelineIR: {args.out}")
        if args.diagnostics:
            output_target = Path(args.diagnostics)
            Path(args.diagnostics).parent.mkdir(parents=True, exist_ok=True)
            Path(args.diagnostics).write_text(diagnostics_to_json(diagnostics), encoding="utf-8")
            if args.format != "vscode-json":
                print(f"Wrote diagnostics: {args.diagnostics}")
        if args.verbose and args.format != "vscode-json":
            print(f"Events: {len(ir['events'])}")
            print(f"Sequencers: {len(states)}")
        if args.format == "vscode-json":
            _print_vscode_payload(
                _vscode_analyzer_payload(args=args, config=config, states=states, ir=ir, diagnostics=diagnostics)
            )
            if has_fatal_diagnostics(diagnostics):
                return 2
            if args.strict and has_strict_failure(diagnostics):
                return 2
            return 0
    except (TypeError, ValueError) as exc:
        message = f"Analyzer result is not strict JSON: {exc}"
        if args.format == "vscode-json":
            _print_vscode_payload(_vscode_error_payload("serialization_error", message))
            return 2
        print(f"q1timeline analyze: {message}", file=sys.stderr)
        return 2
    except OSError as exc:
        target = f" to {output_target}" if output_target is not None else ""
        message = f"Could not write analyzer output{target}: {exc}"
        if args.format == "vscode-json":
            _print_vscode_payload(_vscode_error_payload("output_error", message))
            return 2
        print(f"q1timeline analyze: {message}", file=sys.stderr)
        return 2
    _print_diagnostic_report(diagnostics)
    if has_fatal_diagnostics(diagnostics):
        return 2
    if args.strict and has_strict_failure(diagnostics):
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="q1timeline",
        description=DESCRIPTION,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"q1timeline {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    analyze = subparsers.add_parser(
        "analyze",
        prog="q1timeline analyze",
        help="analyze Q1ASM input and write TimelineIR",
        description="Analyze Q1ASM input and write TimelineIR.",
    )
    analyze.add_argument("input_file", nargs="?", type=Path, help="single Q1ASM file")
    analyze.add_argument("--project", type=Path, help="q1timeline.yml project file")
    analyze.add_argument("--out", type=Path, help="write TimelineIR JSON")
    analyze.add_argument("--diagnostics", type=Path, help="write diagnostics JSON")
    analyze.add_argument("--format", choices=("text", "vscode-json"), default="text", help="output format")
    analyze.add_argument("--include-timeline-ir", action="store_true", help="include TimelineIR in vscode-json output")
    analyze.add_argument("--include-diagnostics", action="store_true", help="include diagnostics in vscode-json output")
    analyze.add_argument("--include-source-map", action="store_true", help="include source map in vscode-json output")
    analyze.add_argument("--summary-only", action="store_true", help="omit large event payloads from vscode-json stdout")
    analyze.add_argument("--mode", choices=("normal", "debug"), default="normal", help="analysis view mode metadata")
    analyze.add_argument("--stdin-overlay", action="store_true", help="reserved for future unsaved-buffer overlay support")
    analyze.add_argument("--no-render", action="store_false", dest="render", help="do not render HTML from analyze")
    analyze.add_argument("--strict", action="store_true", help="return nonzero when warnings are present")
    analyze.add_argument("--q1asm-strict", action="store_true", help="reject unresolved symbolic operands like q1asm_windows.exe")
    analyze.add_argument("--align", help="override alignment mode")
    analyze.add_argument(
        "--align-anchor-kind",
        action="append",
        dest="align_anchor_kinds",
        help="event kind to use as a first_anchor candidate; repeat for multiple kinds",
    )
    analyze.add_argument(
        "--branch-assumption",
        action="append",
        dest="branch_assumptions",
        default=[],
        metavar="BRANCH_ID=PATH",
        help="branch assumption override; PATH must be collapsed, taken, fallthrough, or both",
    )
    analyze.add_argument(
        "--loop-preview",
        action="append",
        dest="loop_previews",
        default=[],
        metavar="LOOP_KEY=COUNT",
        help=(
            "materialize compact loop preview iterations; "
            f"COUNT must be 1..{LOOP_PREVIEW_MAX_VISIBLE_ITERATIONS}"
        ),
    )
    analyze.add_argument("--params", type=Path, help="override params JSON file")
    analyze.add_argument("--display", type=Path, help="override display YAML file")
    analyze.add_argument("--verbose", action="store_true", help="print additional analysis details")
    analyze.set_defaults(render=True)
    analyze.set_defaults(handler=_analyze_command)

    render = subparsers.add_parser(
        "render",
        prog="q1timeline render",
        help="render TimelineIR to HTML/SVG",
        description="Render TimelineIR to HTML/SVG.",
    )
    render.add_argument("--ir", type=Path, help="TimelineIR JSON input")
    render.add_argument("--out", type=Path, help="HTML output path")
    render.add_argument("--mode", choices=("normal", "debug"), default="normal", help="initial view mode")
    render.add_argument("--open", action="store_true", help="open the rendered HTML in the default browser")
    render.add_argument("--no-open", action="store_false", dest="open", help="do not open the rendered HTML")
    render.add_argument("--verbose", action="store_true", help="print additional render details")
    render.set_defaults(open=False)
    render.set_defaults(handler=_render_command)

    watch = subparsers.add_parser(
        "watch",
        prog="q1timeline watch",
        help="watch project files and refresh timeline output",
        description="Watch project files and refresh timeline output.",
    )
    watch.add_argument("--project", type=Path, help="q1timeline.yml project file")
    watch.add_argument("--out-dir", type=Path, default=Path(".q1timeline"), help="output directory")
    watch.add_argument("--once", action="store_true", help="run one analyze/render cycle and exit")
    watch.add_argument("--debounce-ms", type=int, default=300, help="debounce changes by this many milliseconds")
    watch.add_argument("--poll-ms", type=int, default=100, help="poll watched files every this many milliseconds")
    watch.set_defaults(handler=_watch_command)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0

    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
