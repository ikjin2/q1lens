"""Public verification commands and their CI exit codes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from q1timeline.project import ConfigLoadError


def add_verification_commands(subparsers) -> None:
    verify = subparsers.add_parser("verify", help="verify source assertions, RT queue boundaries or project alignment")
    verify.add_argument("source", type=Path, help="Q1ASM source, or project YAML with --alignment")
    verify.add_argument("--alignment", action="store_true", help="check shared measurement outcomes on a project")
    verify.add_argument("--project", type=Path, help="use this Q1 timeline project for the source")
    verify.add_argument("--sequencer", help="select a project sequencer that uses the source")
    verify.add_argument("--max-paths", type=int, default=256, help="branch exploration budget")
    equiv = subparsers.add_parser("equiv", help="compare the RT traces of two Q1ASM programs")
    equiv.add_argument("before", type=Path)
    equiv.add_argument("after", type=Path)
    compare = subparsers.add_parser("compare-output", help="compare a routed output port of two Q1 timeline projects")
    compare.add_argument("before", type=Path, help="original q1timeline.yml")
    compare.add_argument("after", type=Path, help="updated q1timeline.yml")
    compare.add_argument("--port", required=True, help="module.outN, or outN if unique")
    compare.add_argument("--start-ns", required=True, type=int, help="first sample time in project coordinates")
    compare.add_argument("--end-ns", required=True, type=int, help="exclusive end of the sample window")
    compare.add_argument("--params", type=Path, help="use this shared JSON parameter map in place of project defaults")
    compare.add_argument("--json", action="store_true", help="print a machine-readable report")
    for command in (verify, equiv):
        command.add_argument("--startup-delay-ns", type=int, help="set the fixed RT queue startup delay")
        command.add_argument("--json", action="store_true", help="print a machine-readable report")
        command.add_argument("--include-replay", action="store_true", help="embed counterexample TimelineIR in JSON")
        command.add_argument("--max-steps", type=int, default=10000, help="instruction budget per execution")
        command.add_argument("--max-cases", type=int, default=4096, help="limit enumerated input combinations")
        command.add_argument("--timeout-ms", type=int, default=10000, help="verification time budget in milliseconds")


def run_verification(args: argparse.Namespace) -> int:
    if args.command == "compare-output":
        from q1timeline.cli import _load_json_override
        from q1timeline.output.compare import compare_output

        try:
            params = _load_json_override(args.params) if args.params else None
            report = compare_output(args.before, args.after, port=args.port, start_ns=args.start_ns,
                                    end_ns=args.end_ns, params=params)
        except (OSError, ValueError, ConfigLoadError) as exc:
            message = exc.diagnostics[0].message if isinstance(exc, ConfigLoadError) and exc.diagnostics else str(exc)
            report = {"status": "unknown", "message": message}
        print(json.dumps(report, allow_nan=False) if args.json else f'{report["status"]}  {report["message"]}')
        return {"proved": 0, "counterexample": 1, "unknown": 2}[report["status"]]
    options = {name: getattr(args, name) for name in ("include_replay", "max_steps", "max_cases", "timeout_ms")}
    path = args.before if args.command == "equiv" else args.source
    try:
        if min(args.max_steps, args.max_cases, args.timeout_ms, getattr(args, "max_paths", 1)) < 1:
            raise ValueError("Verification budgets must be positive.")
        if args.startup_delay_ns is not None and args.startup_delay_ns < 0:
            raise ValueError("--startup-delay-ns must be non-negative.")
        if args.command == "equiv":
            from q1timeline.verification_compare import verify_equivalence

            report = verify_equivalence(args.before, args.after, startup_delay_ns=args.startup_delay_ns, **options)
        elif args.alignment:
            from q1timeline.verification_compare import verify_alignment

            if args.project or args.sequencer or args.startup_delay_ns is not None:
                raise ValueError("Pass the project as the positional argument to --alignment and set timing in its YAML.")
            report = verify_alignment(args.source, **options)
        else:
            from q1timeline.verification import verify_file

            if args.sequencer and not args.project:
                raise ValueError("--sequencer requires --project.")
            report = verify_file(args.source, project=args.project, sequencer=args.sequencer,
                                 startup_delay_ns=args.startup_delay_ns, max_paths=args.max_paths, **options)
    except (OSError, ValueError, ConfigLoadError) as exc:
        report = {"status": "unknown", "checks": [{"kind": args.command, "status": "unknown",
                  "file": str(path.resolve()), "line": 1, "message": str(exc)}]}
    if args.json:
        print(json.dumps(report, allow_nan=False))
    else:
        print(report["status"])
        for check in report["checks"]:
            print(f'{check["file"]}:{check["line"]}  {check["status"]}  {check["message"]}')
            if witness := check.get("counterexample"):
                values = ", ".join(f"{k}={v}" for k, v in witness["inputs"].items())
                print(f'  {values}  {witness["file"]}:{witness["line"]}')
    return {"proved": 0, "counterexample": 1, "unknown": 2}[report["status"]]
