"""Register write scheduling, provenance and projection of resolved ALU words."""
from __future__ import annotations

from dataclasses import replace
from itertools import count
from typing import Any

from q1timeline.analysis.values import (
    _symbol_lookup_names, Concrete, RuntimeDependent, Symbolic, SymbolTable, Unknown, Value,
    resolve_arg_value, value_to_json,
)
from q1timeline.q1asm import core
from q1timeline.q1asm.ast import Arg, Instr, SourceLocation
from q1timeline.q1asm.core_alu import register_write_delay_slots
from q1timeline.execution.state import (
    AnalysisState, CLASSICAL_REGISTER_MASK, PendingRegisterWrite, RegisterWrite,
)
from q1timeline.execution.trace import confidence_for_value, source_to_json
from q1timeline.execution.operands import (
    bounds_known_bits, docs_ordered_binary_args, physical_read_register, refine_bounds_with_bits,
    register_read_operands, resolve_register_name, unsigned_operand_bounds,
)

_VALUE_ORIGIN_SERIALS = count(1)


def register_issue_metadata(state: AnalysisState, instruction: Instr, scope: SymbolTable) -> dict[str, Any]:
    reads = {
        register: value_to_json(resolve_arg_value(Arg("reg", register, register), scope))
        for _, register in register_read_operands(instruction, state)
    }
    # Keep values that the preceding RT command did not itself read, so first
    # displaying a register does not falsely imply that its value just changed.
    snapshot = {register: value_to_json(value) for register, value in state.registers.items()}
    snapshot.update(reads)
    bounds = {register: list(unsigned_operand_bounds(Arg("reg", register, register), scope.get(register, Unknown(register)), state))
              for register in reads}
    return {"register_reads": reads, "register_snapshot": snapshot, "register_read_bounds": bounds}


def pending_register_write(state: AnalysisState, register: str) -> PendingRegisterWrite | None:
    pending_writes = [
        pending
        for pending in state.pending_register_writes
        if pending.register == register and pending.available_issue_index > state.q1_issue_index
    ]
    if not pending_writes:
        return None
    return min(pending_writes, key=lambda item: item.available_issue_index)


def apply_ready_register_writes(state: AnalysisState) -> None:
    if not state.pending_register_writes:
        return
    remaining: list[PendingRegisterWrite] = []
    for pending in state.pending_register_writes:
        if pending.available_issue_index <= state.q1_issue_index:
            apply_register_write(state, pending)
        else:
            remaining.append(pending)
    state.pending_register_writes = remaining


def retire_register_write(
    state: AnalysisState, pending: PendingRegisterWrite, *, time: Value | None = None, value: Concrete | None = None,
) -> None:
    """Commit a write at the issue boundary or clock edge that released it."""
    state.pending_register_writes.remove(pending)
    if value is not None:
        pending = replace(pending, value=value)
    apply_register_write(state, pending, time=time)


def flush_pending_register_writes(
    state: AnalysisState,
    *,
    completed_issue_index: int | None = None,
) -> None:
    if not state.pending_register_writes:
        return
    for pending in sorted(state.pending_register_writes, key=lambda item: item.available_issue_index):
        completed = state.q1_issue_index if completed_issue_index is None else completed_issue_index
        time = state.q1_time_ns if pending.available_issue_index <= completed else Unknown(
            "register write availability follows an instruction outside the analyzed trace"
        )
        apply_register_write(state, pending, time=time)
    state.pending_register_writes = []


def fresh_value_origin(state: AnalysisState) -> tuple[str, bool]:
    state.value_origin_serial = next(_VALUE_ORIGIN_SERIALS)
    return f"write:{state.value_origin_serial}", False


def apply_register_write(state: AnalysisState, pending: PendingRegisterWrite, *, time: Value | None = None) -> None:
    if state.record_trace:
        meta = pending.meta
        if state.execution_mode == "concrete":
            meta = {
                **meta,
                "previous_value": value_to_json(state.registers.get(pending.register, Unknown(pending.register))),
                "provenance": pending.provenance,
            }
    state.registers[pending.register] = pending.value
    if pending.unsigned_bounds is None:
        state.register_bounds.pop(pending.register, None)
    else:
        state.register_bounds[pending.register] = pending.unsigned_bounds
    if pending.known_bits is None:
        state.register_known_bits.pop(pending.register, None)
    else:
        state.register_known_bits[pending.register] = pending.known_bits
    state.register_value_origins[pending.register] = pending.value_origin if pending.value_origin is not None else fresh_value_origin(state)
    if state.record_trace:
        state.register_provenance[pending.register] = pending.provenance
        record_register_write(
            state, pending.register, pending.value, source=pending.source,
            event_id=pending.event_id, time=time, meta=meta,
        )


def record_register_write(
    state: AnalysisState,
    register: str,
    value: Value,
    *,
    source: SourceLocation,
    event_id: str | None,
    time: Value | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    write_time = state.q1_time_ns if time is None else time
    write_meta = dict(meta or {})
    write_meta.setdefault("branch_assumptions", register_branch_assumptions(state))
    confidence = confidence_for_value((value, write_time))
    if confidence == "exact" and write_meta.get("branch_assumptions"):
        confidence = "assumed"
    state.register_history.append(
        RegisterWrite(state.sequencer_id, register, write_time, value, source, event_id, confidence, write_meta)
    )


def register_branch_assumptions(state: AnalysisState) -> list[dict[str, str]]:
    return [
        {"branch_id": entry["branch_id"], "assumed_branch_path": entry["assumed_branch_path"]}
        for entry in state.metadata.get("branches", {}).get("assumptions", [])
    ]


def invalidate_register_history(
    state: AnalysisState,
    registers: set[str],
    *,
    source: SourceLocation,
    event_id: str,
    reason: str,
    meta: dict[str, Any] | None = None,
) -> None:
    for register in sorted(registers):
        state.register_bounds.pop(register, None)
        state.register_known_bits.pop(register, None)
        state.register_value_origins[register] = fresh_value_origin(state)
        record_register_write(
            state,
            register,
            Unknown(reason),
            source=source,
            event_id=event_id,
            meta={**(meta or {}), "invalidated": True, "reason": reason},
        )


def schedule_register_write(
    state: AnalysisState,
    instruction: Instr,
    register: str,
    value: Value,
    provenance: dict[str, Any],
    *,
    unsigned_bounds: tuple[int, int] | None = None,
    known_bits: tuple[int, int] | None = None,
    value_origin: tuple[str, bool] | None = None,
) -> None:
    # Display expressions are separate from the versioned numeric proof facts.
    if not isinstance(value, Concrete):
        unsigned_bounds, known_bits = origin_peer_facts(state, value_origin, unsigned_bounds, known_bits)
        if unsigned_bounds is not None and unsigned_bounds[0] == unsigned_bounds[1]:
            value = Concrete(unsigned_bounds[0])
            provenance = {**provenance, "value": value_to_json(value)}
    if isinstance(value, (Symbolic, RuntimeDependent, Unknown)) and len(str(value)) > 1024:
        reference = f"{register} after {instruction.op} (line {instruction.source.line})"
        if isinstance(value, Symbolic):
            value = Symbolic(reference, value.unresolved_symbols)
        elif isinstance(value, RuntimeDependent):
            value = RuntimeDependent(reference)
        else:
            value = Unknown(reference)
        provenance = {**provenance, "value": value_to_json(value)}
    event_id = f"{state.sequencer_id}:e{len(state.events)}" if state.record_trace else ""
    if state.execution_mode == "concrete" and state.record_trace:
        provenance = concrete_write_provenance(state, instruction, provenance, event_id)
    state.pending_register_writes.append(
        PendingRegisterWrite(
            register=register,
            value=value,
            provenance=provenance,
            source=instruction.source,
            available_issue_index=state.q1_issue_index + 1 + register_write_delay_slots(instruction.op),
            event_id=event_id,
            meta={
                "op": instruction.op,
                "branch_assumptions": register_branch_assumptions(state),
            } if state.record_trace else {},
            unsigned_bounds=unsigned_bounds if not isinstance(value, Concrete) else None,
            known_bits=known_bits if not isinstance(value, Concrete) and known_bits != (0, 0) else None,
            value_origin=value_origin if value_origin is not None else fresh_value_origin(state),
        )
    )


def concrete_write_provenance(
    state: AnalysisState, instruction: Instr, provenance: dict[str, Any], event_id: str,
) -> dict[str, Any]:
    return {
        **provenance,
        "event_id": event_id,
        "execution_index": state.q1_issue_index,
        "inputs": {
            register: {
                "value": value_to_json(state.registers.get(register, Unknown(register))),
                "event_id": state.register_provenance.get(register, {}).get("event_id"),
            }
            for _, register in register_read_operands(instruction, state)
        },
    }


def set_alu_flags(state: AnalysisState, instruction: Instr, flags: dict[str, bool] | None) -> None:
    state.alu_flags = flags
    state.alu_flag_cases = None
    state.flag_expression = None
    state.alu_flags_source = instruction.source if flags is not None else None
    state.alu_flags_read_hazard = state.analysis_back_edge_limit is not None and bool(state.pending_register_writes) and any(
        pending_register_write(state, register) is not None for _, register in register_read_operands(instruction, state)
    )


def operand_value_origin(arg: Arg, value: Value, state: AnalysisState) -> tuple[str, bool] | None:
    if isinstance(value, Concrete):
        return f"constant:{value.value & CLASSICAL_REGISTER_MASK}", False
    register = physical_read_register(arg, state)
    if register is not None:
        return state.register_value_origins.get(register, (f"initial:{register}", False))
    if arg.kind in {"symbol", "placeholder"}:
        declared = set(state.symbol_origins.get("defs", {})) | set(state.symbol_origins.get("params", {}))
        for name in _symbol_lookup_names(str(arg.value)):
            if name in state.immutable_value_origins:
                return state.immutable_value_origins[name]
            if name.startswith("$") and name[1:] in state.immutable_value_origins:
                return state.immutable_value_origins[name[1:]]
            if name in declared:
                return f"binding:{name}", False
            if name.startswith("$") and name[1:] in declared:
                return f"binding:{name[1:]}", False
        return f"template:{arg.value}", False
    return None


def origin_peer_facts(
    state: AnalysisState, origin: tuple[str, bool] | None,
    bounds: tuple[int, int] | None, known_bits: tuple[int, int] | None,
) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    if origin is None:
        return bounds, known_bits
    for register in state.register_bounds.keys() | state.register_known_bits.keys():
        value = state.registers.get(register, Unknown(register))
        peer = state.register_value_origins.get(register, operand_value_origin(Arg("reg", register, register), value, state))
        if peer is None or peer[0] != origin[0]:
            continue
        interval = state.register_bounds.get(register, (0, CLASSICAL_REGISTER_MASK))
        ones, zeros = state.register_known_bits.get(register, (0, 0))
        if isinstance(value, Concrete):
            word = value.value & CLASSICAL_REGISTER_MASK
            interval, (ones, zeros) = (word, word), (word, CLASSICAL_REGISTER_MASK ^ word)
        prefix_ones, prefix_zeros = bounds_known_bits(interval)
        ones, zeros = ones | prefix_ones, zeros | prefix_zeros
        if peer[1] != origin[1]:
            interval = CLASSICAL_REGISTER_MASK - interval[1], CLASSICAL_REGISTER_MASK - interval[0]
            ones, zeros = zeros, ones
        current = bounds or (0, CLASSICAL_REGISTER_MASK)
        bounds = max(current[0], interval[0]), min(current[1], interval[1])
        old_ones, old_zeros = known_bits or (0, 0)
        known_bits = old_ones | ones, old_zeros | zeros
    if bounds is not None or known_bits not in {None, (0, 0)}:
        bounds = refine_bounds_with_bits(bounds, known_bits or (0, 0))
    return bounds, known_bits


def project_classical_words(state: AnalysisState, instruction: Instr, core_issue: core.Issue) -> None:
    if core_issue.flags is not None and instruction.op != "not":
        set_alu_flags(state, instruction, core_issue.flags.as_dict())
    destinations = core.destination_indexes(instruction.op)
    for index, word in zip(destinations, core_issue.words, strict=True):
        destination = resolve_register_name(instruction.args[index], state)
        assert destination is not None
        value = Concrete(word)
        if instruction.op == "move":
            provenance = move_provenance(state, instruction, instruction.args[0], destination, value)
        elif instruction.op == "not":
            provenance = unary_provenance(state, instruction, instruction.args[0], destination, value)
        else:
            left, right = docs_ordered_binary_args(instruction, state)
            provenance = binary_provenance(state, instruction, left, right, destination, value)
        # The core supplies the exact word. The trace above records its source operands.
        schedule_register_write(state, instruction, destination, value, provenance,
                                 value_origin=(f"constant:{word}", False))
    if instruction.op == "not" and core_issue.flags is not None:
        # Preserve Analysis's read-hazard tracking for an in-place NOT.
        set_alu_flags(state, instruction, core_issue.flags.as_dict())


def move_provenance(
    state: AnalysisState,
    instruction: Instr,
    source_arg: Arg,
    destination: str,
    value: Value,
) -> dict[str, Any]:
    if not state.record_trace:
        return {}
    source_provenance = arg_register_provenance(source_arg, state)
    expression = _arg_provenance_expression(source_arg, state)
    steps = copied_provenance_steps(source_provenance)
    steps.append(provenance_step(instruction, expression, value))
    return make_register_provenance(
        state,
        destination,
        expression=expression,
        value=value,
        steps=steps,
    )


def unary_provenance(
    state: AnalysisState,
    instruction: Instr,
    source_arg: Arg,
    destination: str,
    value: Value,
) -> dict[str, Any]:
    if not state.record_trace:
        return {}
    source_provenance = arg_register_provenance(source_arg, state)
    source_expression = _arg_provenance_expression(source_arg, state)
    expression = f"{instruction.op} {source_expression}"
    steps = copied_provenance_steps(source_provenance)
    steps.append(provenance_step(instruction, expression, value))
    return make_register_provenance(
        state,
        destination,
        expression=expression,
        value=value,
        steps=steps,
    )


def binary_provenance(
    state: AnalysisState,
    instruction: Instr,
    left_arg: Arg,
    right_arg: Arg,
    destination: str,
    value: Value,
) -> dict[str, Any]:
    if not state.record_trace:
        return {}
    left_provenance = arg_register_provenance(left_arg, state)
    right_provenance = arg_register_provenance(right_arg, state)
    left_expression = _arg_provenance_expression(left_arg, state)
    right_expression = _arg_provenance_expression(right_arg, state)
    expression = _binary_provenance_expression(instruction.op, left_expression, right_expression)
    steps = copied_provenance_steps(left_provenance)
    if right_provenance is not None:
        steps.extend(copied_provenance_steps(right_provenance))
    steps.append(provenance_step(instruction, expression, value))
    steps = _ordered_unique_provenance_steps(steps)
    return make_register_provenance(
        state,
        destination,
        expression=expression,
        value=value,
        steps=steps,
    )


def arg_register_provenance(arg: Arg, state: AnalysisState) -> dict[str, Any] | None:
    register = resolve_register_name(arg, state)
    if register is None:
        return None
    if state.execution_mode == "concrete":
        return {"register": register, "expression": register, "steps": []}
    return state.register_provenance.get(register)


def _arg_provenance_expression(arg: Arg, state: AnalysisState) -> str:
    provenance = arg_register_provenance(arg, state)
    if provenance is not None:
        expression = provenance.get("expression")
        if isinstance(expression, str) and expression:
            return expression
    if arg.kind in {"symbol", "placeholder"}:
        name = str(arg.value)
        return name[1:] if name.startswith("$") and len(name) > 1 else name
    return arg.raw


def make_register_provenance(
    state: AnalysisState,
    register: str,
    *,
    expression: str,
    value: Value,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    symbol = _register_symbol_for_register(state, register)
    return {
        "register": register,
        "symbol": symbol,
        "expression": expression if len(expression) <= 1024 else register,
        "value": value_to_json(value),
        "steps": steps,
    }


def _register_symbol_for_register(state: AnalysisState, register: str) -> str:
    aliases = sorted(alias for alias, target in state.register_aliases.items() if target == register)
    return aliases[0] if aliases else register


def copied_provenance_steps(provenance: dict[str, Any] | None) -> list[dict[str, Any]]:
    if provenance is None:
        return []
    steps = provenance.get("steps")
    if not isinstance(steps, list):
        return []
    return [dict(step) for step in steps if isinstance(step, dict)]


def _ordered_unique_provenance_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique_steps: dict[tuple[str, int, int, str, str], dict[str, Any]] = {}
    for index, step in enumerate(steps):
        source = step.get("source") if isinstance(step.get("source"), dict) else {}
        key = (
            str(source.get("file", "")),
            int(source.get("line", 0)),
            int(source.get("column", 0)),
            str(step.get("op", "")),
            str(step.get("expression", "")),
        )
        unique_steps.setdefault(key, {**step, "_order": index})
    ordered = sorted(
        unique_steps.values(),
        key=lambda step: (
            str(step.get("source", {}).get("file", "")),
            int(step.get("source", {}).get("line", 0)),
            int(step.get("source", {}).get("column", 0)),
            int(step.get("_order", 0)),
        ),
    )
    return [{key: value for key, value in step.items() if key != "_order"} for step in ordered]


def provenance_step(instruction: Instr, expression: str, value: Value) -> dict[str, Any]:
    return {
        "op": instruction.op,
        "source": source_to_json(instruction.source),
        "expression": expression,
        "value": value_to_json(value),
    }


def _binary_provenance_expression(op: str, left: str, right: str) -> str:
    operators = {
        "add": "+",
        "sub": "-",
        "and": "&",
        "or": "|",
        "xor": "^",
        "asl": "<<",
        "asr": ">>",
        "lsl": "<<",
        "lsr": ">>",
    }
    right_expression = _parenthesized_right_expression(op, right)
    return f"{left} {operators.get(op, op)} {right_expression}"


def _parenthesized_right_expression(op: str, expression: str) -> str:
    if op in {"sub", "asl", "asr", "lsl", "lsr"} and _is_composite_expression(expression):
        return f"({expression})"
    return expression


def _is_composite_expression(expression: str) -> bool:
    return any(operator in expression for operator in (" + ", " - ", " & ", " | ", " ^ ", " << ", " >> "))
