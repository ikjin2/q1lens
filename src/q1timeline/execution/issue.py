"""Q1 issue admission, effects and execution records shared by the schedulers."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from q1timeline.analysis.values import (
    Concrete, SymbolTable, Unknown, Value, add_values, resolve_arg_value, subtract_values,
)
from q1timeline.diagnostics import Diagnostic
from q1timeline.q1asm import core, core_rt
from q1timeline.q1asm.ast import Arg, Instr
from q1timeline.q1asm.compiled_addresses import CompiledTarget
from q1timeline.q1asm.core_alu import register_write_delay_slots
from q1timeline.q1asm.instruction_table import (
    STATUS_BRANCH_OPS, InstructionSpec, branch_duration, get_instruction_spec,
)
from q1timeline.execution.state import (
    AnalysisState, BoundInstruction, CLASSICAL_REGISTER_MASK, ILLEGAL_INSTRUCTION_FLAG, PendingRegisterWrite,
    TimelineEvent,
)
from q1timeline.execution.trace import (
    confidence_for_timing, confidence_for_value, emit_event, plain_value, source_to_json,
)
from q1timeline.execution.operands import (
    arg_int, branch_target_metadata, instruction_for_q1_timing, operand_known_bits, operand_scope,
    physical_read_register, register_read_operands, resolve_execution_target, resolve_register_name,
    signed_32_bit_register_value, unsigned_operand_bounds,
)
from q1timeline.execution.registers import (
    apply_register_write, concrete_write_provenance, make_register_provenance, pending_register_write,
    project_classical_words, provenance_step, register_branch_assumptions, schedule_register_write,
    set_alu_flags,
)
from q1timeline.execution.validation import (
    concrete_branch_test, concrete_instruction_ready, invalid_rt_duration, latched_operand_value,
    validate_instruction,
)


@dataclass(frozen=True)
class _Q1Admission:
    spec: InstructionSpec
    scope: SymbolTable
    binding: BoundInstruction | None
    invalid_arguments: bool
    diagnostic_start: int
    duration_error: Diagnostic | None


def admit_q1_issue(
    state: AnalysisState, instruction: Instr, symbols: SymbolTable, *,
    supported_ops: set[str] | None = None, allow_symbolic_operands: bool = False,
) -> _Q1Admission | None:
    binding = state.bound_instructions.get(instruction.pc) if state.interior_pc is None else None
    spec, invalid, diagnostic_start = validate_instruction(state, instruction, symbols, binding)
    needs_scope = state.execution_mode == "concrete" or state.record_trace or spec.emits_rt_packet or instruction.op in core.LOCAL_OPS
    scope = operand_scope(symbols, state) if needs_scope else {}
    duration_error = None if invalid else invalid_rt_duration(instruction, spec, scope)
    if state.execution_mode == "concrete" and not concrete_instruction_ready(
        state, instruction, symbols, spec, invalid, diagnostic_start,
        supported_ops=supported_ops, allow_symbolic_operands=allow_symbolic_operands,
        rt_duration_error=duration_error, scope=scope,
    ):
        return None
    return _Q1Admission(spec, scope, binding, invalid, diagnostic_start, duration_error)


def finish_q1_issue(state: AnalysisState, *, fault: bool = False) -> None:
    state.executed_pcs.add(state.pc)
    state.q1_issue_index += 1
    state.metadata["execution"]["steps"] += 1
    if not fault:
        advance_execution(state)


def project_q1_issue(
    state: AnalysisState, instruction: Instr, symbols: SymbolTable, spec: InstructionSpec,
    effects: core.Issue | None, q1_t0: Value, packet_id: str | None, register_metadata: dict | None,
    *, feedback_words: tuple[int, ...] | None = None, feedback: dict | None = None,
    scheduled: bool = False, branch_choice: bool | None = None, projected_pc: bool = True,
    project_latch: bool = True,
) -> TimelineEvent | None:
    """Project issued register and latch effects, then associate the issue with its branch or packet."""
    pending_latch = None
    if spec.category == "classical" and effects is not None and instruction.op != "nop":
        project_classical_words(state, instruction, effects)
    elif spec.category == "latched" and project_latch:
        pending_latch = _execute_latched(state, instruction, symbols, latch_write=effects.latch_write if effects else None,
                                         condition=effects.condition if effects else None,
                                         event_time=state.q1_time_ns if scheduled else state.rt_time_ns)
    if feedback_words is not None:
        for index, word in zip(core.destination_indexes(instruction.op), feedback_words, strict=True):
            register = resolve_register_name(instruction.args[index], state)
            value = Concrete(word & CLASSICAL_REGISTER_MASK)
            provenance = {}
            if state.record_trace:
                field = "id" if instruction.op == "fb_pull_data" and index == 0 else "payload"
                expression = f"feedback id {feedback['id']}" if feedback else f"{instruction.op} FIFO {field}"
                provenance = make_register_provenance(state, register, expression=expression, value=value,
                                                  steps=[provenance_step(instruction, expression, value)])
                if feedback:
                    provenance["input_event_id"] = feedback["event_id"]
            schedule_register_write(state, instruction, register, value, provenance)
    issue = None
    if (state.record_trace or packet_id is not None or (feedback_words is not None and not scheduled)
            or (spec.category == "latched" and not scheduled)
            or (instruction.op == "stop" and state.latched_state.pending_since)):
        issue = emit_q1_issue(state, instruction, q1_t0, state.q1_time_ns, packet_id,
                               register_metadata=register_metadata)
    if pending_latch is not None and not scheduled:
        pending_latch.meta["q1_issue_event_id"] = issue.id
        if instruction.op == "set_cond":
            issue.meta["conditional_enabled"] = pending_latch.meta["conditional_enabled"]
    if spec.category == "branch":
        execute_concrete_branch(state, instruction, issue, symbols, q1_t0=q1_t0, defer_register_write=scheduled,
                                 forced_decision=branch_choice, branch_issue=effects, projected=projected_pc)
    return issue


def concrete_bound_instruction(instruction: Instr, state: AnalysisState, scope: SymbolTable) -> Instr:
    """Bind aliases and parameters at the issue boundary, preserving physical register operands."""
    bound = instruction_for_q1_timing(instruction, state, {}, scope=scope)
    if any(arg.kind == "label" for arg in bound.args):
        bound = replace(bound, args=[
            Arg("imm", arg_int(arg, scope), arg.raw) if arg.kind == "label" else arg for arg in bound.args
        ])
    if bound.op in core.BRANCH_OPS and instruction.args[-1].raw.startswith("$"):
        alias = instruction.args[-1].raw[1:]
        source = state.symbol_origins.get("defs", {}).get(alias, {}).get("raw", "")
        if source.startswith("@"):
            bound = replace(bound, args=[*bound.args[:-1], replace(bound.args[-1], raw=source)])
    return bound


def resolved_core_issue(
    state: AnalysisState, instruction: Instr, scope: SymbolTable, *, analysis: bool = False,
) -> core.Issue | None:
    """Return the local effect when the operands needed for this issue are resolved."""
    if instruction.op not in core.LOCAL_OPS and (analysis or instruction.op not in core.DEVICE_SETTERS):
        return None
    destinations = core.destination_indexes(instruction.op)
    if analysis:
        # A visible word can be stale while its replacement is in the write pipeline.
        if state.pending_register_writes and any(
            pending_register_write(state, register) is not None
            for _, register in register_read_operands(instruction, state)
        ):
            return None
        if instruction.op == "loop":
            # The counter is read and written. A project parameter with the same
            # name does not initialize it, and its decrement cannot feed the target.
            counter = resolve_register_name(instruction.args[0], state)
            if not isinstance(state.registers.get(counter), Concrete) or counter == resolve_register_name(instruction.args[1], state):
                return None
        # A symbolic destination or overlapping halves of a product has no exact write.
        registers = {resolve_register_name(instruction.args[i], state) for i in destinations}
        if None in registers or len(registers) != len(destinations):
            return None
    words = tuple(arg_int(arg, scope) for i, arg in enumerate(instruction.args) if i not in destinations)
    if any(word is None for index, word in enumerate(words)
           if analysis or instruction.op != "set_cond" or words[0] != 0 or index not in (1, 2)):
        return None
    if (instruction.op in STATUS_BRANCH_OPS or instruction.op == "jge" and len(instruction.args) == 1
            ) and core.branch_on_flags(instruction.op, state.alu_flags) is None:
        return None
    bound = concrete_bound_instruction(instruction, state, scope)
    try:
        return core.issue(bound, words, state.alu_flags)
    except ValueError:
        # Analysis can describe unsupported concrete values (e.g. a shift by 33)
        # with an unknown result or a diagnostic at the instruction that uses them.
        if analysis:
            return None
        raise


def branch_taken_duration(instruction: Instr, state: AnalysisState, symbols: SymbolTable) -> int:
    bound = instruction_for_q1_timing(instruction, state, symbols)
    return get_instruction_spec(instruction.op).q1_time_model(bound)


def begin_execution_step(state: AnalysisState) -> None:
    state.next_pc = state.pc + 1
    state.next_interior_pc = None


def advance_execution(state: AnalysisState) -> None:
    state.pc = state.next_pc
    state.interior_pc = state.next_interior_pc


def set_execution_target(state: AnalysisState, target: CompiledTarget) -> None:
    state.next_pc = target.source_pc
    state.next_interior_pc = target.address if target.interior is not None else None


def execute_concrete_branch(
    state: AnalysisState, instruction: Instr, q1_issue_event: TimelineEvent | None, symbols: SymbolTable,
    *, q1_t0: Value | None = None, defer_register_write: bool = False, forced_decision: bool | None = None,
    branch_issue: core.Issue | None = None, projected: bool = False,
) -> None:
    if forced_decision is not None and (type(forced_decision) is not bool or instruction.op == "jmp"):
        raise ValueError("A forced branch decision must be a boolean for a conditional branch")
    scope = operand_scope(symbols, state)
    target_index = 1 if instruction.op == "loop" else 2 if len(instruction.args) == 3 else 0
    target = resolve_execution_target(state, resolve_arg_value(instruction.args[target_index], scope))
    if branch_issue is not None:
        decision = branch_issue.taken
        flags = branch_issue.flags.as_dict() if branch_issue.flags is not None else None
    else:
        decision, flags = concrete_branch_test(state, instruction, scope)
    if flags is not None:
        set_alu_flags(state, instruction, flags)
    counter_write = None
    if instruction.op == "loop":
        register = resolve_register_name(instruction.args[0], state)
        if branch_issue is None:
            value = Concrete((state.registers[register].value - 1) & CLASSICAL_REGISTER_MASK)
        else:
            value = Concrete(branch_issue.words[0])
        event_id = q1_issue_event.id if q1_issue_event is not None else ""
        provenance = {}
        if state.record_trace:
            provenance = make_register_provenance(
                state, register, expression=f"{register} - 1", value=value,
                steps=[provenance_step(instruction, f"{register} - 1", value)],
            )
            provenance = concrete_write_provenance(state, instruction, provenance, event_id)
        counter_write = PendingRegisterWrite(
            register, value, provenance,
            instruction.source, state.q1_issue_index + 1 + register_write_delay_slots(instruction.op), event_id, {"op": "loop"},
        )
    if forced_decision is not None:
        if q1_issue_event is not None:
            q1_issue_event.meta.update(natural_branch_taken=decision, branch_overridden=True,
                                      assumption_source="user", assumed_branch_path="taken" if forced_decision else "fallthrough")
        decision = forced_decision
    if branch_issue is not None:
        duration = branch_issue.q1_duration
    else:
        duration = branch_duration(branch_taken_duration(instruction, state, symbols), decision)
    if q1_issue_event is not None:
        q1_t0 = q1_issue_event.t0
    assert q1_t0 is not None
    q1_t1 = state.q1_time_ns if projected else add_values(q1_t0, Concrete(duration))
    if q1_issue_event is not None:
        state.events[-1] = replace(q1_issue_event, t1=q1_t1, duration=Concrete(duration),
                                  confidence="assumed" if forced_decision is not None else q1_issue_event.confidence)
        q1_issue_event.meta.update(branch_decision="assumed" if forced_decision is not None else "concrete",
                                   branch_taken=decision, **branch_target_metadata(target, state))
    state.q1_time_ns = q1_t1
    if counter_write is not None:
        if defer_register_write:
            state.pending_register_writes.append(counter_write)
        else:
            apply_register_write(state, counter_write)
    if decision and not projected:
        assert target is not None
        set_execution_target(state, target)
    elif forced_decision is not None and not projected:
        begin_execution_step(state)


def _execute_latched(
    state: AnalysisState, instruction: Instr, symbols: SymbolTable, *,
    latch_write: core_rt.LatchWrite | None = None,
    condition: tuple[int, int | None, int | None, int] | None = None,
    event_time: Value | None = None,
) -> TimelineEvent | None:
    scope = operand_scope(symbols, state)
    field_name: str | None = None
    value: Any = None

    if latch_write is not None or instruction.op in core_rt.LATCH_OPS:
        if latch_write is not None:
            field_name = latch_write.field
            raw = latch_write.value
            if isinstance(raw, tuple):
                value = tuple(Concrete(word) for word in raw)
            else:
                value = raw if isinstance(raw, bool) else Concrete(raw)
        else:
            field_name = core_rt.LATCH_FIELDS[instruction.op]
            arity = len(get_instruction_spec(instruction.op).signatures[0].args)
            if len(instruction.args) < arity:
                return None
            if field_name == "phase_reset":
                value = True
            else:
                values = tuple(latched_operand_value(instruction.op, arg, scope, state)
                               for arg in instruction.args[:arity])
                value = values[0] if arity == 1 else values
        if field_name == "phase_reset":
            state.pending_phase_reset = True
        else:
            setattr(state.latched_state, field_name, value)
    elif instruction.op in {"set_cond", "set_time_ref"}:
        field_name = instruction.op
        value = [resolve_arg_value(arg, scope) for arg in instruction.args]
        if instruction.op == "set_cond":
            value = (tuple(Concrete(word) if word is not None else original
                           for word, original in zip(condition, value, strict=True))
                     if condition is not None else tuple(value))
            state.conditional_enable = value[0]
            state.conditional_mask = value[1]
            state.conditional_operator = value[2]
            state.conditional_enable_bounds = unsigned_operand_bounds(instruction.args[0], value[0], state)
            state.conditional_operator_bounds = unsigned_operand_bounds(instruction.args[2], value[2], state)
            state.conditional_enable_known_bits = operand_known_bits(instruction.args[0], value[0], state)
            state.conditional_operator_known_bits = operand_known_bits(instruction.args[2], value[2], state)
            state.conditional_else_duration = value[3]

    if field_name is not None:
        if field_name not in {"set_cond", "set_time_ref"}:
            state.latched_state.pending_since[field_name] = instruction.source
        event = emit_event(
            state,
            lane="state.pending",
            kind="latched_state_pending",
            t0=state.rt_time_ns if event_time is None else event_time,
            t1=state.rt_time_ns if event_time is None else event_time,
            duration=Concrete(0),
            label=instruction.op,
            confidence=confidence_for_value(value),
            source=instruction.source,
            meta={"field": field_name, "value": plain_value(value)},
        )
        if field_name not in {"set_cond", "set_time_ref"}:
            state.latched_state.pending_event_ids[field_name] = event.id
        elif field_name == "set_cond":
            event.meta["conditional_enabled"] = (
                bool(state.conditional_enable.value) if isinstance(state.conditional_enable, Concrete) else None
            )
        return event
    return None


def emit_q1_issue(
    state: AnalysisState,
    instruction: Instr,
    q1_t0: Value,
    q1_t1: Value,
    rt_packet_id: str | None,
    *,
    register_metadata: dict[str, Any] | None = None,
) -> TimelineEvent:
    meta: dict[str, Any] = {
        "op": instruction.op,
        "emits_rt_packet": rt_packet_id is not None,
        "display_modes": ["debug"],
        **(register_metadata or {}),
    }
    if state.compiled_addresses is not None:
        meta["compiled_pc"] = (state.interior_pc if state.interior_pc is not None
                               else state.compiled_addresses.entries.get(instruction.pc))
        if state.interior_pc is not None:
            meta["source_pc"] = instruction.pc
            meta["macro_source_op"] = state.instructions_by_pc[instruction.pc].op
    if rt_packet_id is not None:
        meta["rt_packet_id"] = rt_packet_id
    return emit_event(
        state,
        lane="debug.q1_issue",
        kind="q1_issue",
        t0=q1_t0,
        t1=q1_t1,
        duration=subtract_values(q1_t1, q1_t0),
        label=instruction.op,
        confidence="exact",
        source=instruction.source,
        meta=meta,
    )


def record_stop_code(state: AnalysisState, instruction: Instr, scope: SymbolTable, *, code: int | None = None) -> Any:
    if code is None:
        value: Value = Concrete(0)
        if instruction.args:
            operand = instruction.args[0]
            register = physical_read_register(operand, state)
            value = resolve_arg_value(operand, scope)
            if register is not None and pending_register_write(state, register) is not None:
                value = Unknown(f"Stop status register {register} is not ready")
        code = signed_32_bit_register_value(value.value) if isinstance(value, Concrete) else plain_value(value)
    if "execution" in state.metadata:
        state.metadata["execution"].update(stop_code=code, stop_source=source_to_json(instruction.source))
    return code


def record_illegal_instruction(
    state: AnalysisState, instruction: Instr, issue: TimelineEvent | None, *, flag: str = ILLEGAL_INSTRUCTION_FLAG,
) -> None:
    reason = "Illegal Q1ASM instruction executed."
    source = source_to_json(instruction.source)
    fault = {"flag": flag, "sequencer_status": "STOPPED",
             "q1_time_ns": plain_value(state.q1_time_ns), "source": source}
    if "execution" in state.metadata:
        execution = state.metadata["execution"]
        execution.update(status="error", reason=reason, source=source, pc=instruction.pc, fault=fault)
        if state.compiled_addresses is not None:
            execution["compiled_pc"] = state.compiled_addresses.entries[instruction.pc]
    state.stopped = True
    assumed = state.execution_mode == "analysis" and (state.branch_explore_depth or register_branch_assumptions(state))
    related = []
    if issue is not None:
        issue.meta["fault"] = fault
        event = emit_event(state, lane="rt.control", kind="illegal", t0=state.q1_time_ns, t1=state.q1_time_ns,
                            duration=Concrete(0), label="Illegal instruction", confidence="assumed" if assumed else
                            confidence_for_timing(Concrete(0), state.q1_time_ns, state.q1_time_ns), source=instruction.source,
                            meta={"fault": fault, "q1_issue_event_id": issue.id})
        related = [event.id, issue.id]
    state.diagnostics.append(Diagnostic(severity="warning" if assumed else "error", category="illegal_instruction",
                                        message=reason, source=instruction.source, related_events=related,
                                        details={"op": instruction.op, "fault": fault}))
