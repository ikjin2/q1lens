"""Replay a symbolic witness using the same concrete DEF binding as a sweep."""
from __future__ import annotations

import operator

from q1timeline.analysis.api import _sequencer_id_from_file
from q1timeline.analysis.hardware_readiness import analyze_hardware_readiness
from q1timeline.analysis.interpreter import AnalysisState, interpret_program
from q1timeline.analysis.smt import Query, VerificationResult
from q1timeline.analysis.underflow import analyze_underflows
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.ast import Program, numeric_defs, with_def_values


_COMPARE = {"<": operator.lt, "<=": operator.le, "==": operator.eq,
            "!=": operator.ne, ">=": operator.ge, ">": operator.gt}


def replay_execution(program: Program, *, symbol: str, value: int, max_steps: int,
                     prefix: bool = False) -> AnalysisState:
    """Execute the witness through its last required issue, including loop occurrences."""
    if symbol not in numeric_defs(program):
        raise ValueError(f"{symbol} is not an editable numeric DEF.")
    bound = with_def_values(program, {symbol: value})
    state = interpret_program(bound, sequencer_id=_sequencer_id_from_file(program.file),
                              execution_mode="concrete", max_steps=max_steps)
    execution = state.metadata["execution"]
    if execution["status"] != "completed" and not (prefix and execution["status"] == "step_limit"):
        raise ValueError(f"Concrete replay {execution['status']}: {execution.get('reason', '')}")
    for diagnostic in state.diagnostics:
        if diagnostic.severity == "error":
            raise ValueError(f"Concrete replay: {diagnostic.message}")
    analyze_underflows([state])
    analyze_hardware_readiness(state, include_optional_checks=False)
    return state


def replay_register(program: Program, *, symbol: str, value: int, register: str,
                    max_steps: int | None = None) -> tuple[int, AnalysisState]:
    if max_steps is None:
        max_steps = max(1, len(program.instructions))
    state = replay_execution(program, symbol=symbol, value=value, max_steps=max_steps)
    word = state.registers.get(register)
    if not isinstance(word, Concrete) or any(w.register == register for w in state.pending_register_writes):
        raise ValueError(f"Concrete replay has no readable final value for {register}.")
    return word.value & 0xFFFFFFFF, state


def _confirm_prefix(state: AnalysisState, query: Query, steps: int) -> dict:
    issues = [event.meta for event in state.events if event.kind == "q1_issue"]
    if len(issues) != steps:
        raise ValueError(f"Concrete issued {len(issues)} instructions, solver predicted {steps}.")
    pcs = [row.get("compiled_pc", row["source_pc"]) for row in issues]
    branches = [(pc, row["branch_taken"]) for pc, row in zip(pcs, issues) if "branch_taken" in row]
    if query.kind == "reachable" and query.pc != pcs[-1]:
        raise ValueError(f"Concrete ended at compiled PC {pcs[-1]}, solver predicted {query.pc}.")
    if query.kind == "path":
        visited = iter(branches)
        for requested in query.path:
            if not any(edge == requested for edge in visited):
                raise ValueError("Concrete did not take the requested branch sequence.")
    details = {"mode": "concrete", "steps": steps, "last_pc": pcs[-1]}
    if query.kind != "underflow":
        details["branches"] = [{"pc": pc, "taken": taken} for pc, taken in branches]
    return details


def confirm_witness(program: Program, symbol: str, query: Query, result: VerificationResult) -> tuple[dict, AnalysisState]:
    assert result.def_value is not None and result.steps is not None
    args = dict(symbol=symbol, value=result.def_value, max_steps=result.steps)
    if query.kind == "register":
        observed, state = replay_register(program, register=query.register, **args)
        details = {"register": query.register}
    else:
        prefix = query.kind in {"reachable", "path", "underflow"}
        state = replay_execution(program, prefix=prefix, **args)
        if prefix:
            details = _confirm_prefix(state, query, result.steps)
            if query.kind == "underflow":
                from q1timeline.simulation.runtime import simulate_programs

                bound = with_def_values(program, {symbol: result.def_value})
                scenario = {"startup_delay_ns": query.value, "max_time_ns": result.register_value + 1}
                state, = simulate_programs({state.sequencer_id: bound}, max_steps=result.steps + 1, simulation=scenario)
                if not any(e.kind == "underflow_warning" and e.t0.value == result.register_value for e in state.events):
                    raise ValueError("Simulation did not reproduce the predicted RT queue underflow.")
                details.update(underflow_ns=result.register_value, timing_mode="simulation")
            return details, state
        if query.kind == "stop":
            observed = state.metadata["execution"]["stop_code"]
        else:
            observed = state.q1_time_ns.value if query.kind == "q1_time" else state.rt_time_ns.value
        details = {}
    if observed != result.register_value or _COMPARE[query.op](observed, query.value):
        raise ValueError(f"Concrete read {observed}, solver predicted a violation at {result.register_value}.")
    return {"mode": "concrete", "value": observed, **details}, state
