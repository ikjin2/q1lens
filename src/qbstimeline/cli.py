from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from qbstimeline.compile_worker import analyze_project
from qbstimeline.consistency import build_consistency_report
from qbstimeline.diagnostic_bundle import verify_diagnostics_bundle, write_diagnostics_bundle
from qbstimeline.doctor import format_report as format_doctor_report
from qbstimeline.doctor import inspect_project
from qbstimeline.ir.serialize import write_qbs_ir
from qbstimeline.project import ConfigLoadError, load_project_config
from qbstimeline.q1timeline_bridge import run_q1timeline_cli
from qbstimeline.render.html import RenderError, render_ir_path


DESCRIPTION = "Q1Lens cross-layer scheduler and Q1ASM debugger"


def main(argv: Sequence[str] | None = None, *, prog: str = "qbstimeline") -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv[:1] == ["q1timeline"]:
        return run_q1timeline_cli(raw_argv[1:])
    parser = _build_parser(prog=prog)
    args = parser.parse_args(raw_argv)
    args.prog = prog
    try:
        if args.command == "analyze":
            return _analyze_command(args)
        if args.command == "build":
            return _build_command(args)
        if args.command == "render":
            return _render_command(args)
        if args.command == "diagnose":
            return _diagnose_command(args)
        if args.command == "verify-bundle":
            return _verify_bundle_command(args)
        if args.command == "doctor":
            return _doctor_command(args)
        if args.command == "q1timeline":
            return run_q1timeline_cli(args.q1timeline_args)
    except Exception as exc:
        print(f"{prog} {args.command}: {exc}", file=sys.stderr)
        return 2
    parser.print_help()
    return 2


def _build_parser(*, prog: str = "qbstimeline") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=DESCRIPTION)
    subparsers = parser.add_subparsers(dest="command")

    analyze = subparsers.add_parser("analyze", help="Compile a qbstimeline project into QBS IR")
    analyze.add_argument("--project", required=True, help="Path to qbstimeline.yml")
    analyze.add_argument("--out", required=True, help="Output QBS IR JSON path")
    analyze.add_argument("--out-dir", type=Path, help="Override the generated artifact directory")
    analyze.add_argument("--strict", action="store_true", help="Return exit code 1 for analysis warnings")

    build = subparsers.add_parser("build", help="Analyze and render a qbstimeline project")
    build.add_argument("--project", required=True, help="Path to qbstimeline.yml")
    build.add_argument("--out-dir", type=Path, help="Override the configured output directory")
    build.add_argument("--open", action="store_true", help="Open the rendered HTML in the default browser")

    render = subparsers.add_parser("render", help="Render QBS IR to static HTML")
    render.add_argument("--ir", required=True, help="Input QBS IR JSON path")
    render.add_argument("--out", required=True, help="Output HTML path")

    diagnose = subparsers.add_parser("diagnose", help="Build a cross-layer diagnostics report")
    diagnose.add_argument("--ir", required=True, help="Input QBS IR JSON path")
    diagnose.add_argument("--q1timeline-ir", help="Optional q1timeline IR JSON path")
    diagnose.add_argument("--out", help="Output diagnostics report JSON path")
    diagnose.add_argument("--bundle", help="Output artifact bundle ZIP path")
    diagnose.add_argument("--schedule", help="Optional schedule source file to include in the bundle")
    diagnose.add_argument("--strict", action="store_true", help="Return exit code 1 for diagnostic findings")

    verify_bundle = subparsers.add_parser(
        "verify-bundle",
        help="Verify a diagnostics bundle manifest and payloads",
    )
    verify_bundle.add_argument("--bundle", required=True, help="Diagnostics bundle ZIP path")

    doctor = subparsers.add_parser("doctor", help="Check a project without executing user code")
    doctor.add_argument("--project", required=True, help="Path to qbstimeline.yml")
    doctor.add_argument("--json", action="store_true", help="Print a machine-readable JSON report")

    q1timeline = subparsers.add_parser(
        "q1timeline",
        add_help=False,
        help="Run the integrated q1timeline CLI",
    )
    q1timeline.add_argument("q1timeline_args", nargs=argparse.REMAINDER)
    return parser


def _analyze_command(args: argparse.Namespace) -> int:
    try:
        config = load_project_config(args.project)
        if args.out_dir is not None:
            output_dir = args.out_dir.expanduser()
            if not output_dir.is_absolute():
                output_dir = config.root / output_dir
            config = replace(config, output_dir=output_dir.resolve())
        result = analyze_project(config)
        write_qbs_ir(result.ir, Path(args.out))
    except (ConfigLoadError, RuntimeError, OSError) as exc:
        print(f"{args.prog} analyze: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote QBS IR: {args.out}")
    print(f"Wrote {len(result.q1asm_programs)} Q1ASM program(s): {config.output_dir / 'q1asm'}")
    warnings = result.ir.get("warnings") or []
    ir_diagnostics = result.ir.get("ir_diagnostics") or []
    if args.strict and (warnings or ir_diagnostics):
        print(
            f"{args.prog} analyze: strict mode found "
            f"{len(warnings)} warning(s) and {len(ir_diagnostics)} IR diagnostic(s)",
            file=sys.stderr,
        )
        return 1
    return 0


def _build_command(args: argparse.Namespace) -> int:
    try:
        config = load_project_config(args.project)
        output_dir = config.output_dir
        if args.out_dir is not None:
            output_dir = args.out_dir.expanduser()
            if not output_dir.is_absolute():
                output_dir = config.root / output_dir
            output_dir = output_dir.resolve()
            config = replace(config, output_dir=output_dir)

        ir_path = output_dir / "qbs_ir.json"
        html_path = output_dir / "index.html"
        result = analyze_project(config)
        write_qbs_ir(result.ir, ir_path)
        render_ir_path(ir_path, html_path)
    except (ConfigLoadError, RuntimeError, OSError, RenderError, json.JSONDecodeError) as exc:
        print(f"{args.prog} build: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote QBS IR: {ir_path}")
    print(f"Wrote {len(result.q1asm_programs)} Q1ASM program(s): {output_dir / 'q1asm'}")
    print(f"Wrote HTML view: {html_path}")
    if args.open and not webbrowser.open(html_path.resolve().as_uri()):
        print(f"{args.prog} build: could not open the default browser", file=sys.stderr)
        return 2
    return 0


def _render_command(args: argparse.Namespace) -> int:
    try:
        render_ir_path(args.ir, Path(args.out))
    except (RenderError, OSError, json.JSONDecodeError) as exc:
        print(f"{args.prog} render: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote HTML view: {args.out}")
    return 0


def _diagnose_command(args: argparse.Namespace) -> int:
    try:
        _validate_diagnostic_output_paths(args)
        qbs_ir_path = Path(args.ir)
        qbs_ir = json.loads(qbs_ir_path.read_text(encoding="utf-8"))
        schedule_path = Path(args.schedule) if args.schedule else None
        if schedule_path is not None and not schedule_path.is_file():
            raise ValueError(f"--schedule must name an existing file: {schedule_path}")
        q1timeline_ir = None
        q1timeline_ir_path = Path(args.q1timeline_ir) if args.q1timeline_ir else None
        if q1timeline_ir_path is not None:
            q1timeline_ir = json.loads(q1timeline_ir_path.read_text(encoding="utf-8"))
        report = build_consistency_report(qbs_ir, q1timeline_ir)
        report_json = _json_payload(report)
        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(report_json, encoding="utf-8")
            print(f"Wrote diagnostics report: {out_path}")
        if args.bundle:
            write_diagnostics_bundle(
                bundle_path=Path(args.bundle),
                qbs_ir=qbs_ir,
                q1timeline_ir=q1timeline_ir,
                report=report,
                schedule_path=schedule_path,
            )
            print(f"Wrote diagnostics bundle: {args.bundle}")
        if not args.out and not args.bundle:
            print(report_json, end="")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"{args.prog} diagnose: {exc}", file=sys.stderr)
        return 2
    findings = report.get("findings") or []
    coverage_gaps = report.get("coverage_gaps") or []
    if args.strict and (findings or coverage_gaps):
        print(
            f"{args.prog} diagnose: strict mode found "
            f"{len(findings)} finding(s) and {len(coverage_gaps)} coverage gap(s)",
            file=sys.stderr,
        )
        return 1
    return 0


def _validate_diagnostic_output_paths(args: argparse.Namespace) -> None:
    protected_paths = [
        (option, Path(value).resolve())
        for option, value in (
            ("--ir", args.ir),
            ("--q1timeline-ir", args.q1timeline_ir),
            ("--schedule", args.schedule),
        )
        if value
    ]
    for option, value in (("--out", args.out), ("--bundle", args.bundle)):
        if not value:
            continue
        path = Path(value).resolve()
        for other_option, other_path in protected_paths:
            if path == other_path or (
                path.exists() and other_path.exists() and path.samefile(other_path)
            ):
                raise ValueError(f"{option} and {other_option} must name different files: {path}")
        protected_paths.append((option, path))


def _verify_bundle_command(args: argparse.Namespace) -> int:
    bundle_path = Path(args.bundle)
    try:
        errors = verify_diagnostics_bundle(bundle_path)
    except OSError as exc:
        print(f"{args.prog} verify-bundle: {exc}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"{args.prog} verify-bundle: {error}", file=sys.stderr)
        return 1
    print(f"Verified diagnostics bundle: {bundle_path}")
    return 0


def _doctor_command(args: argparse.Namespace) -> int:
    try:
        report = inspect_project(args.project)
        if args.json:
            print(_json_payload(report), end="")
        else:
            print(format_doctor_report(report, prog=args.prog), end="")
    except Exception as exc:
        print(f"{args.prog} doctor: {exc}", file=sys.stderr)
        return 2
    return 0 if report["status"] == "pass" else 1


def _json_payload(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
