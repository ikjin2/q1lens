"""Source locations and inclusive integer domains for verification comments."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from fractions import Fraction

from q1timeline.analysis.smt import Query
from q1timeline.q1asm.ast import Arg, Program, numeric_defs
from q1timeline.q1asm.compiled_addresses import compile_addresses
from q1timeline.q1asm.parser import parse_integer_literal


MIN_DEF = -(1 << 31)
MAX_DEF = (1 << 32) - 1
_NAME = r"[A-Za-z_][A-Za-z_0-9]*"
_INT = r"-?(?:0[xX][a-fA-F0-9]+|0|[1-9]\d*)"
_OP = r"(<=|>=|==|!=|<|>)"


@dataclass(frozen=True)
class OutputQuery:
    kind: str
    port: str | None = None
    limit: Fraction = Fraction(1)


@dataclass(frozen=True)
class Assertion:
    line: int
    text: str
    subject: str
    query: Query | OutputQuery


class AnnotationError(ValueError):
    def __init__(self, line: int, message: str):
        self.line = line
        super().__init__(f"Line {line}: {message}")


def _assumption(expression: str) -> tuple[str, int, int]:
    # Both halves of a chained comparison constrain the same input.
    match = re.fullmatch(rf"(?:({_INT})\s*{_OP}\s*)?\$?({_NAME})(?:\s*{_OP}\s*({_INT}))?", expression)
    if not match or not (match[1] or match[5]):
        raise ValueError("Use an integer constraint such as '1 <= N <= 1000'.")
    left, first, name, second, right = match.groups()
    reverse = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "==": "==", "!=": "!="}
    low, high = MIN_DEF, MAX_DEF
    for op, raw in [(reverse.get(first), left), (second, right)]:
        if raw is None:
            continue
        number = parse_integer_literal(raw)
        if op in {">", ">=", "=="}:
            low = max(low, number + (op == ">"))
        if op in {"<", "<=", "=="}:
            high = min(high, number - (op == "<"))
        if op == "!=":
            raise ValueError("The assumed DEF domain must be one contiguous interval.")
    return name, low, high


def annotations(source_text: str, program: Program) -> tuple[dict[str, tuple[int, int]], list[Assertion]]:
    domains = {}
    checks = []
    layout = compile_addresses({item.pc: item for item in program.instructions}, program.labels)
    labels = layout.labels if layout else program.labels
    input_names = {str(arg.value).lstrip("$") for item in program.instructions for arg in item.args
                   if arg.kind == "symbol"} - program.defs.keys()
    for line, raw in enumerate(source_text.splitlines(), 1):
        directive = re.fullmatch(r"\s*(?:;|#|//)\s*@(assume|assert)\b(.*)", raw.lstrip("\ufeff"))
        if not directive:
            continue
        kind, expression = directive[1], directive[2].strip()
        try:
            if kind == "assume":
                name, low, high = _assumption(expression)
                if name not in numeric_defs(program) and name not in input_names:
                    raise ValueError(f"{name} must name an editable numeric DEF or a referenced parameter.")
                prior = domains.get(name, (MIN_DEF, MAX_DEF))
                domains[name] = max(low, prior[0]), min(high, prior[1])
                if domains[name][0] > domains[name][1]:
                    raise ValueError(f"No value of {name} satisfies the assumptions.")
                continue
            if expression in ("no_clip", "net_zero"):
                checks.append(Assertion(line, expression, expression, OutputQuery(expression)))
                continue
            peak = re.fullmatch(r"peak\s*\(\s*(out[0-9]+)\s*\)\s*<=\s*(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,2})?)", expression)
            if peak:
                checks.append(Assertion(line, expression, "peak", OutputQuery("peak", peak[1], Fraction(peak[2]))))
                continue
            match = re.fullmatch(rf"(R\d+|q1_time|rt_time|stop_code)\s*{_OP}\s*({_INT})(?:\s+at\s+@({_NAME}))?", expression)
            if not match:
                raise ValueError("Compare a register, clock or stop_code with an integer, optionally at @label.")
            subject, op, raw_value, label = match.groups()
            if label is not None and (label not in labels or program.labels[label] >= len(program.instructions)):
                raise ValueError(f"@{label} must label an executable instruction.")
            if subject == "stop_code" and label is not None:
                raise ValueError("stop_code is available only at termination.")
            kind = "register" if subject.startswith("R") else "stop" if subject == "stop_code" else subject
            query = Query(kind, op, parse_integer_literal(raw_value), subject if kind == "register" else None,
                          labels.get(label))
            checks.append(Assertion(line, expression, subject, query))
        except ValueError as exc:
            raise AnnotationError(line, str(exc)) from exc
    return domains, checks


def with_assumed_parameters(program: Program, ranges: dict[str, tuple[int, int]]) -> Program:
    """Bind explicitly ranged input symbols through the existing DEF sweep engine."""
    names = ranges.keys() - program.defs.keys()
    if not names:
        return program
    defs = dict(program.defs)
    locations = dict(program.def_sources)
    instructions = []
    for item in program.instructions:
        args = []
        for arg in item.args:
            name = str(arg.value).removeprefix("$")
            if arg.kind == "symbol" and name in names:
                default = ranges[name][0]
                defs[name] = Arg("imm", default, str(default))
                locations.setdefault(name, item.source)
                arg = Arg("symbol", f"${name}", f"${name}")
            args.append(arg)
        instructions.append(replace(item, args=args))
    return replace(program, defs=defs, def_sources=locations, instructions=instructions)
