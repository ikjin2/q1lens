from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path

from qbstimeline.compile_worker import analyze_project
from qbstimeline.consistency import build_consistency_report, versions_payload
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
    if args.command == "analyze":
        return _analyze_command(args)
    if args.command == "render":
        return _render_command(args)
    if args.command == "diagnose":
        return _diagnose_command(args)
    if args.command == "q1timeline":
        return run_q1timeline_cli(args.q1timeline_args)
    parser.print_help()
    return 2


def _build_parser(*, prog: str = "qbstimeline") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=DESCRIPTION)
    subparsers = parser.add_subparsers(dest="command")

    analyze = subparsers.add_parser("analyze", help="Compile a qbstimeline project into QBS IR")
    analyze.add_argument("--project", required=True, help="Path to qbstimeline.yml")
    analyze.add_argument("--out", required=True, help="Output QBS IR JSON path")

    render = subparsers.add_parser("render", help="Render QBS IR to static HTML")
    render.add_argument("--ir", required=True, help="Input QBS IR JSON path")
    render.add_argument("--out", required=True, help="Output HTML path")

    diagnose = subparsers.add_parser("diagnose", help="Build a cross-layer diagnostics report")
    diagnose.add_argument("--ir", required=True, help="Input QBS IR JSON path")
    diagnose.add_argument("--q1timeline-ir", help="Optional q1timeline IR JSON path")
    diagnose.add_argument("--out", help="Output diagnostics report JSON path")
    diagnose.add_argument("--bundle", help="Output artifact bundle ZIP path")
    diagnose.add_argument("--schedule", help="Optional schedule source file to include in the bundle")

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
        result = analyze_project(config)
        write_qbs_ir(result.ir, Path(args.out))
    except (ConfigLoadError, RuntimeError, OSError) as exc:
        print(f"{args.prog} analyze: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote QBS IR: {args.out}")
    print(f"Wrote {len(result.q1asm_programs)} Q1ASM program(s): {config.output_dir / 'q1asm'}")
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
        qbs_ir_path = Path(args.ir)
        qbs_ir = json.loads(qbs_ir_path.read_text(encoding="utf-8"))
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
            _write_diagnostics_bundle(
                bundle_path=Path(args.bundle),
                qbs_ir=qbs_ir,
                qbs_ir_path=qbs_ir_path,
                q1timeline_ir=q1timeline_ir,
                q1timeline_ir_path=q1timeline_ir_path,
                report_json=report_json,
                schedule_path=Path(args.schedule) if args.schedule else None,
            )
            print(f"Wrote diagnostics bundle: {args.bundle}")
        if not args.out and not args.bundle:
            print(report_json, end="")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"{args.prog} diagnose: {exc}", file=sys.stderr)
        return 2
    return 0


def _write_diagnostics_bundle(
    *,
    bundle_path: Path,
    qbs_ir: dict,
    qbs_ir_path: Path,
    q1timeline_ir: dict | None,
    q1timeline_ir_path: Path | None,
    report_json: str,
    schedule_path: Path | None,
) -> None:
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(qbs_ir_path, "qbs_ir.json")
        if q1timeline_ir is not None and q1timeline_ir_path is not None:
            bundle.write(q1timeline_ir_path, "q1timeline_ir.json")
        if schedule_path is not None and schedule_path.is_file():
            bundle.write(schedule_path, "schedule.py")
        bundle.writestr("diagnostics_report.json", report_json)
        bundle.writestr("versions.json", _json_payload(versions_payload()))
        for sequencer_id, text in _embedded_q1asm(qbs_ir).items():
            bundle.writestr(f"q1asm/{sequencer_id}.q1asm", text)


def _embedded_q1asm(qbs_ir: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    q1asm_by_sequencer = qbs_ir.get("q1asm_by_sequencer")
    if isinstance(q1asm_by_sequencer, dict):
        for key, value in q1asm_by_sequencer.items():
            if isinstance(key, str) and isinstance(value, str):
                result[key] = value
    q1asm_programs = qbs_ir.get("q1asm_programs")
    if isinstance(q1asm_programs, list):
        for program in q1asm_programs:
            if not isinstance(program, dict):
                continue
            sequencer = program.get("sequencer_id") or program.get("sequencer")
            text = program.get("text")
            if isinstance(sequencer, str) and isinstance(text, str):
                result.setdefault(sequencer, text)
    return result


def _json_payload(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
