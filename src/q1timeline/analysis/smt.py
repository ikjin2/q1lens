"""Bounded symbolic queries over the local Q1 instruction model."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from q1timeline.analysis.interpreter import diagnose_program_source
from q1timeline.q1asm import core
from q1timeline.q1asm.ast import Program, numeric_defs, with_def_values
from q1timeline.q1asm.compiled_addresses import compile_addresses


@dataclass(frozen=True)
class Query:
    kind: Literal["register", "stop", "q1_time", "rt_time", "reachable", "path", "underflow"]
    op: str = "=="
    value: int = 0
    register: str | None = None
    pc: int | None = None
    path: tuple[tuple[int, bool], ...] = ()


@dataclass(frozen=True)
class VerificationResult:
    status: Literal["proved", "counterexample", "reachable", "unreachable", "unknown"]
    reason: str | None = None
    def_value: int | None = None
    register_value: int | None = None
    steps: int | None = None


def verify(
    program: Program, *, def_name: str, low: int, high: int, query: Query,
    max_steps: int = 10000, max_paths: int = 256,
) -> VerificationResult:
    """Search every feasible path within the bounds, preserving any unresolved frontier."""
    from q1timeline.analysis.smt_execution import Explorer

    if def_name not in numeric_defs(program):
        raise ValueError(f"{def_name} must name an unambiguous numeric .DEF.")
    if not -(1 << 31) <= low <= high < (1 << 32):
        raise ValueError("The inclusive DEF range must lie within -2147483648..4294967295.")
    if max_steps < 1 or max_paths < 1:
        raise ValueError("Step and path limits must be positive.")
    if query.kind == "register" and query.register not in {f"R{i}" for i in range(64)}:
        raise ValueError("Choose an observation register R0 through R63.")
    if query.kind == "register" and not 0 <= query.value <= 1 << 32:
        raise ValueError("The unsigned register bound must lie within 0..4294967296.")
    if query.kind == "underflow" and (type(query.value) is not int or query.value < 0):
        raise ValueError("The RT startup delay must be a non-negative integer.")
    if query.kind not in {"register", "stop", "reachable", "path", "q1_time", "rt_time", "underflow"}:
        raise ValueError(f"Unsupported symbolic query: {query.kind}")
    if query.op not in {"<", "<=", "==", "!=", ">=", ">"}:
        raise ValueError(f"Unsupported comparison: {query.op}")

    for item in program.instructions:
        if any(arg.kind == "placeholder" for arg in item.args):
            return VerificationResult("unknown", f"Template operand on line {item.source.line} is unsupported.")
        if item.op not in core.LOCAL_OPS and any(arg.value == f"${def_name}" for arg in item.args):
            return VerificationResult("unknown", f"Symbolic operands in {item.op} are unsupported.")
    if any(arg.kind == "placeholder" for arg in program.defs.values()):
        return VerificationResult("unknown", "Template DEF values are unsupported.")

    # Direct literal substitution preserves instruction signatures. Static
    # immediate restrictions for local ops are intervals. The separate dynamic
    # guards cover shift counts, branch addresses and RT durations upon issue.
    for endpoint in dict.fromkeys((low, high)):
        case = with_def_values(program, {def_name: endpoint})
        for diagnostic in diagnose_program_source(case, params={}, strict_q1asm=False):
            if diagnostic.severity == "error":
                return VerificationResult("unknown", diagnostic.message)
    try:
        code = core.prepare_program(case)
    except ValueError as error:
        return VerificationResult("unknown", str(error))

    by_pc = {item.pc: item for item in program.instructions}
    layout = compile_addresses(by_pc, program.labels)
    source = ([target.interior or by_pc[target.source_pc] for target in layout.targets.values()]
              if layout else program.instructions)
    if query.kind == "reachable" and (query.pc is None or not 0 <= query.pc < len(code)):
        raise ValueError("The reachability target must be an instruction's compiled PC.")
    if query.kind == "path" and (not query.path or any(
            not 0 <= pc < len(code) or code[pc].op not in core.BRANCH_OPS for pc, _ in query.path)):
        raise ValueError("Each path element must name a branch's compiled PC.")
    symbolic_args = [frozenset(i for i, arg in enumerate(item.args) if arg.value == f"${def_name}") for item in source]
    return Explorer(code, symbolic_args, low, high, query, max_steps, max_paths).run()


def verify_register_bound(
    program: Program, *, def_name: str, low: int, high: int, register: str, bound: int,
) -> VerificationResult:
    return verify(program, def_name=def_name, low=low, high=high, query=Query("register", "<", bound, register))
