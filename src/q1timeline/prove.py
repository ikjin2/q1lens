"""CLI for bounded Q1ASM proofs and replay of solver witnesses."""
from __future__ import annotations

import argparse
import json
import re
import sys

from q1timeline.analysis import smt_replay
from q1timeline.analysis.alignment import align_timelines
from q1timeline.ir.serialize import timeline_ir_from_states, write_timeline_ir
from q1timeline.q1asm.compiled_addresses import compile_addresses
from q1timeline.q1asm.parser import parse_integer_literal, parse_q1asm_file


def _query(program, args):
    from q1timeline.analysis.smt import Query

    if args.assertion is not None:
        match = re.fullmatch(r"\s*(R\d+|stop_code|q1_time|rt_time)\s*(<=|>=|==|!=|<|>)\s*(\S+)\s*", args.assertion)
        if not match:
            raise ValueError("Compare a register, stop_code, q1_time or rt_time with an integer.")
        name, op, raw = match.groups()
        value = parse_integer_literal(raw)
        if name.startswith("R"):
            return Query("register", register=name, op=op, value=value), f"unsigned({name}) {op} {value}"
        return Query("stop" if name == "stop_code" else name, op=op, value=value), f"{name} {op} {value}"
    if args.underflow_at is not None:
        return Query("underflow", value=args.underflow_at), f"RT queue underflow with startup_delay_ns={args.underflow_at}"

    layout = compile_addresses({i.pc: i for i in program.instructions}, program.labels)
    labels = layout.labels if layout else program.labels

    def address(raw):
        name = raw.strip().removeprefix("@")
        if name in labels:
            return labels[name]
        try:
            return parse_integer_literal(name)
        except ValueError:
            raise ValueError(f"Unknown label or compiled PC: {raw}") from None

    if args.reach is not None:
        return Query("reachable", pc=address(args.reach)), f"reach {args.reach}"
    path = []
    for part in args.path.split(","):
        location, _, outcome = part.partition("=")
        outcome = outcome.strip()
        if outcome not in {"taken", "fallthrough"}:
            raise ValueError("Use --path 'LABEL=taken,NEXT=fallthrough' in execution order.")
        path.append((address(location), outcome == "taken"))
    return Query("path", path=tuple(path)), args.path


def run(args: argparse.Namespace) -> int:
    try:
        from q1timeline.analysis.smt import verify

        name, equals, interval = args.def_range.partition("=")
        lower, dots, upper = interval.partition("..")
        if not equals or not dots:
            raise ValueError("Use --range NAME=MIN..MAX.")
        name = name.strip()
        low, high = (parse_integer_literal(raw.strip()) for raw in (lower, upper))
        program = parse_q1asm_file(args.source)
        if args.replay_ir and args.replay_ir.exists() and args.replay_ir.samefile(args.source):
            raise ValueError("--replay-ir must name a different file from the Q1ASM source.")
        query, description = _query(program, args)
        result = verify(program, def_name=name, low=low, high=high, query=query,
                        max_steps=args.max_steps, max_paths=args.max_paths)
        report = {"status": result.status, "range": {name: [low, high]},
                  "assertion" if args.assertion is not None else "query": description}
        if result.reason:
            report["reason"] = result.reason
        if result.status in {"counterexample", "reachable"}:
            try:
                replay, state = smt_replay.confirm_witness(program, name, query, result)
            except ValueError as exc:
                report.update(status="unknown", reason=f"Witness replay failed: {exc}")
            else:
                report["counterexample" if result.status == "counterexample" else "witness"] = {name: result.def_value}
                report["replay"] = replay
                if args.replay_ir:
                    align_timelines([state], mode="none")
                    write_timeline_ir(timeline_ir_from_states([state], project={"alignment_mode": "none", "virtual": True}),
                                      args.replay_ir)
                    replay["ir"] = str(args.replay_ir)
    except ModuleNotFoundError as exc:
        if exc.name != "z3":
            raise
        print("Install the current q1lens package in this Python environment. In a source checkout run: pip install -e .", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"q1timeline prove: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    if query.kind == "underflow":
        return {"unreachable": 0, "reachable": 1, "unknown": 2}[report["status"]]
    return {"proved": 0, "reachable": 0, "counterexample": 1, "unreachable": 1, "unknown": 2}[report["status"]]
