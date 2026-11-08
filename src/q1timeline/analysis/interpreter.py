from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from itertools import count, product
import json
from typing import Any, Literal

from q1timeline.analysis.values import (
    _symbol_lookup_names,
    Concrete,
    RuntimeDependent,
    Symbolic,
    SymbolTable,
    Unknown,
    Value,
    add_values,
    compare_values,
    multiply_value,
    resolve_arg_value,
    resolve_def_values,
    resolve_duration_arg,
    subtract_values,
    symbol_table_from_params,
    value_to_json,
)
from q1timeline.diagnostics import Diagnostic
from q1timeline.q1asm.ast import Arg, Instr, Program, SourceLocation
from q1timeline.q1asm.instruction_table import (
    STATUS_BRANCH_OPS,
    InstructionSpec,
    get_instruction_spec,
    rt_duration_arg_for_instruction,
)
from q1timeline.q1asm.lint import analyze_program_lints


Confidence = Literal["exact", "symbolic", "assumed", "unknown", "runtime_dependent"]
BranchAssumptionPath = Literal["collapsed", "taken", "fallthrough", "both"]

VALID_BRANCH_ASSUMPTION_PATHS = {"collapsed", "taken", "fallthrough", "both"}
LOOP_PREVIEW_MAX_VISIBLE_ITERATIONS = 10

FEEDBACK_ANNOTATIONS = {
    "fb_pop_data": ("feedback_pop", "feedback pop"),
    "fb_pull_data": ("feedback_pop", "feedback pull"),
}

_CLASSICAL_REGISTER_MASK = 0xFFFFFFFF
_REGISTER_MAX_INDEX = 63
_RT_DURATION_MAX = 65535
_VALUE_ORIGIN_SERIALS = count(1)
_WRAPPING_CLASSICAL_OPS = {"add", "sub", "and", "or", "xor", "asl", "asr", "lsl", "lsr"}
_CONCRETE_OPS = {
    "nop", "stop", "move", "not", "cmp", "test", *_WRAPPING_CLASSICAL_OPS,
    "mulu16", "muls16", "mulu32l", "mulu32h", "muls32l", "muls32h",
    "jmp", "jlt", "jge", "loop", *STATUS_BRANCH_OPS,
    "set_mrk", "set_freq", "reset_ph", "set_ph", "set_ph_delta", "set_awg_gain", "set_awg_offs",
    "set_digital", "set_scope_en", "wait", "wait_sync", "upd_param", "play", "acquire",
}


@dataclass(frozen=True)
class TimelineEvent:
    id: str
    sequencer_id: str
    lane: str
    kind: str
    t0: Value
    t1: Value | None
    duration: Value
    label: str
    confidence: Confidence
    source: SourceLocation
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RtPacket:
    id: str
    sequencer_id: str
    source: SourceLocation
    op: str
    duration: Value
    q1_issue_t0: Value
    q1_issue_t1: Value
    rt_t0: Value | None
    rt_t1: Value | None
    confidence: Confidence
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RegisterWrite:
    sequencer_id: str
    register: str
    time: Value
    value: Value
    source: SourceLocation
    event_id: str | None
    confidence: Confidence
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PendingRegisterWrite:
    register: str
    value: Value
    provenance: dict[str, Any]
    source: SourceLocation
    available_issue_index: int
    event_id: str
    meta: dict[str, Any] = field(default_factory=dict)
    unsigned_bounds: tuple[int, int] | None = None
    known_bits: tuple[int, int] | None = None
    value_origin: tuple[str, bool] | None = field(default=None, compare=False)


@dataclass
class LatchedState:
    marker: Value | None = None
    awg_gain: tuple[Value, Value] | None = None
    awg_offset: tuple[Value, Value] | None = None
    frequency: Value | None = None
    phase: Value | None = None
    phase_delta: Value | None = None
    digital: tuple[Value, Value, Value] | None = None
    scope_enable: Value | None = None
    pending_since: dict[str, SourceLocation] = field(default_factory=dict)
    pending_event_ids: dict[str, str] = field(default_factory=dict)


@dataclass
class AnalysisState:
    sequencer_id: str
    labels: dict[str, int] = field(default_factory=dict)
    instructions_by_pc: dict[int, Instr] = field(default_factory=dict)
    register_aliases: dict[str, str] = field(default_factory=dict)
    pc: int = 0
    registers: SymbolTable = field(default_factory=dict)
    register_bounds: dict[str, tuple[int, int]] = field(default_factory=dict)
    latched_state: LatchedState = field(default_factory=LatchedState)
    conditional_enable: Value = field(default_factory=lambda: Concrete(0))
    conditional_mask: Value = field(default_factory=lambda: Concrete(0))
    conditional_operator: Value = field(default_factory=lambda: Concrete(0))
    conditional_enable_bounds: tuple[int, int] | None = None
    conditional_operator_bounds: tuple[int, int] | None = None
    conditional_enable_known_bits: tuple[int, int] | None = None
    conditional_operator_known_bits: tuple[int, int] | None = None
    conditional_else_duration: Value = field(default_factory=lambda: Concrete(0))
    conditional_time_unknown: bool = False
    q1_time_ns: Value = field(default_factory=lambda: Concrete(0))
    rt_time_ns: Value = field(default_factory=lambda: Concrete(0))
    loop_stack: list[Any] = field(default_factory=list)
    rt_packets: list[RtPacket] = field(default_factory=list)
    events: list[TimelineEvent] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    symbol_origins: dict[str, Any] = field(default_factory=dict)
    register_provenance: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending_register_writes: list[PendingRegisterWrite] = field(default_factory=list)
    register_history: list[RegisterWrite] = field(default_factory=list)
    current_resolved_args: list[dict[str, Any]] = field(default_factory=list)
    feedback_acq_channel: str | None = None
    feedback_acq_data_type: str | None = None
    alu_flags: dict[str, bool] | None = None
    alu_flags_source: SourceLocation | None = None
    loop_counter: int = 0
    q1_issue_index: int = 0
    next_pc: int | None = None
    stopped: bool = False
    executed_pcs: set[int] = field(default_factory=set)
    branch_policy: str = "collapse_unresolved"
    branch_assumptions: dict[str, BranchAssumptionPath] = field(default_factory=dict)
    branch_explore_depth: int = 0
    loop_preview_counts: dict[str, int] = field(default_factory=dict)
    strict_q1asm: bool = False
    execution_mode: Literal["analysis", "concrete"] = "analysis"
    has_expanded_instructions: bool = False
    static_diagnostics: list[Diagnostic] = field(default_factory=list)
    register_known_bits: dict[str, tuple[int, int]] = field(default_factory=dict)
    register_value_origins: dict[str, tuple[str, bool]] = field(default_factory=dict, compare=False)
    value_origin_serial: int = field(default=0, compare=False)
    immutable_value_origins: dict[str, tuple[str, bool]] = field(default_factory=dict, compare=False)
    pending_phase_reset: bool | None = False
    nco_phase_uncertain: bool = False
    queue_alternatives: dict[str, AnalysisState] = field(default_factory=dict, compare=False, repr=False)
    queue_alternative_probe: bool = field(default=False, compare=False, repr=False)


def interpret_program(
    program: Program,
    *,
    sequencer_id: str,
    params: dict[str, Any] | None = None,
    waveform_names: dict[int, str] | None = None,
    acquisition_names: dict[int, str] | None = None,
    branch_policy: str = "collapse_unresolved",
    branch_assumptions: Mapping[str, str] | None = None,
    loop_preview_counts: Mapping[str, int] | None = None,
    strict_q1asm: bool = False,
    execution_mode: Literal["analysis", "concrete"] = "analysis",
    max_steps: int = 10000,
) -> AnalysisState:
    if not isinstance(execution_mode, str) or execution_mode not in {"analysis", "concrete"}:
        raise ValueError(f"Invalid execution mode: {execution_mode}")
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
        raise ValueError("max_steps must be a positive integer")
    register_aliases = _register_aliases(program.defs)
    state = AnalysisState(
        sequencer_id=sequencer_id,
        labels=program.labels,
        instructions_by_pc={instruction.pc: instruction for instruction in program.instructions},
        register_aliases=register_aliases,
        branch_policy=branch_policy,
        branch_assumptions=_normalise_branch_assumptions(branch_assumptions),
        loop_preview_counts=_normalise_loop_preview_counts(loop_preview_counts),
        strict_q1asm=strict_q1asm,
        execution_mode=execution_mode,
    )
    state.diagnostics.extend(program.diagnostics)
    if strict_q1asm:
        _diagnose_strict_q1asm_placeholder_defs(state, program.defs)

    raw_params = params or {}
    symbols = symbol_table_from_params(raw_params)
    value_defs = {name: arg for name, arg in program.defs.items() if arg.kind != "reg"}
    resolved_defs = resolve_def_values(value_defs, symbols, state.diagnostics, labels=program.labels)
    state.symbol_origins = _symbol_origins(raw_params, symbols, value_defs, resolved_defs)
    state.immutable_value_origins = _build_immutable_value_origins(symbols, value_defs, state)
    symbols.update(resolved_defs)
    _diagnose_program(state, symbols, program)

    state.pc = 0
    if execution_mode == "concrete":
        _run_concrete_program(state, symbols, waveform_names or {}, acquisition_names or {}, max_steps)
        _apply_ready_register_writes(state)
    else:
        _run_program_loop(state, symbols, waveform_names or {}, acquisition_names or {})
        _flush_pending_register_writes(state)
        _diagnose_static_register_read_hazards(state)
    return state


def _end_concrete_execution(state: AnalysisState, status: str, reason: str, instruction: Instr | None = None) -> None:
    state.metadata["execution"].update(status=status, reason=reason, pc=state.pc)
    state.stopped = True
    source = instruction.source if instruction is not None else None
    if source is not None:
        state.metadata["execution"]["source"] = _source_to_json(source)
    state.diagnostics.append(Diagnostic(
        severity="error" if status == "error" else "warning",
        category="analysis_incomplete" if status != "error" else "invalid_argument_value",
        message=f"{state.metadata['execution']['mode'].capitalize()} execution {status}: {reason}",
        source=source,
        details={"execution_status": status, "pc": state.pc},
    ))


def _run_concrete_program(
    state: AnalysisState,
    symbols: SymbolTable,
    waveform_names: dict[int, str],
    acquisition_names: dict[int, str],
    max_steps: int,
) -> None:
    execution = state.metadata["execution"] = {
        "mode": "concrete", "status": "completed", "steps": 0, "max_steps": max_steps,
        "timing_model": "analytical", "address_model": "source_instruction_index",
        "assumptions": [
            "Sequencers execute independently; wait_sync uses a local timing origin.",
            "Q1/RT timing is analytical; queue backpressure and external feedback are not simulated.",
            "PC values index source instructions; assembler instruction expansion is not modeled.",
        ],
    }
    state.has_expanded_instructions = any(
        instruction.op in {"loop", "jlt"} or (instruction.op == "jge" and len(instruction.args) == 3)
        for instruction in state.instructions_by_pc.values()
    )
    errors = [diagnostic for diagnostic in state.diagnostics
              if diagnostic.severity == "error" and not diagnostic.details.get("source_line_skipped")]
    if errors:
        status = "error" if any(item.category != "unresolved_symbol" for item in errors) else "blocked"
        _end_concrete_execution(state, status, errors[0].message)
        return
    while not state.stopped:
        _apply_ready_register_writes(state)
        instruction = state.instructions_by_pc.get(state.pc)
        if instruction is None:
            if state.pc == len(state.instructions_by_pc) and any(
                item.details.get("source_line_skipped") for item in state.diagnostics
            ):
                state.stopped = True
                break
            _end_concrete_execution(state, "error", "Execution reached a PC without an instruction or stop.")
            break
        if execution["steps"] >= max_steps:
            _end_concrete_execution(state, "step_limit", f"Reached the {max_steps} instruction limit.", instruction)
            break
        state.next_pc = state.pc + 1
        scope = _scope(symbols, state)
        flags_before = dict(state.alu_flags) if state.alu_flags is not None else None
        event_start = len(state.events)
        state.current_resolved_args = _resolved_args_for_instruction(instruction, scope, state)
        try:
            _execute_instruction(state, instruction, symbols, waveform_names, acquisition_names)
        finally:
            state.current_resolved_args = []
        if execution["status"] != "completed":
            break
        for event in state.events[event_start:]:
            if event.kind == "q1_issue":
                event.meta.update(
                    source_pc=instruction.pc, execution_index=execution["steps"],
                    flags_before=flags_before,
                    flags_after=dict(state.alu_flags) if state.alu_flags is not None else None,
                )
        state.executed_pcs.add(state.pc)
        state.q1_issue_index += 1
        execution["steps"] += 1
        state.pc = state.next_pc


def _concrete_instruction_ready(
    state: AnalysisState, instruction: Instr, symbols: SymbolTable,
    spec: InstructionSpec, has_invalid_arguments: bool, diagnostic_start: int,
    *, supported_ops: set[str] | None = None, allow_symbolic_operands: bool = False,
) -> bool:
    diagnostics = state.diagnostics[diagnostic_start:]
    errors = [item for item in diagnostics if (item.severity == "error" and item.category != "unresolved_symbol") or item.category == "register_not_ready"]
    if errors:
        reason = errors[0].message
        _end_concrete_execution(state, "error", reason, instruction)
        return False
    if (instruction.op not in (_CONCRETE_OPS if supported_ops is None else supported_ops)
            and not _set_cond_is_disabled(state, instruction, symbols)):
        status = "error" if spec.category == "unknown" or instruction.op == "illegal" else "blocked"
        _end_concrete_execution(state, status, f"{instruction.op} has no supported concrete execution semantics.", instruction)
        return False
    target_index = (1 if instruction.op == "loop" else 2 if len(instruction.args) == 3 else 0) if spec.category == "branch" else None
    if state.has_expanded_instructions:
        uses_numeric_target = target_index is not None and _branch_target_arg(instruction.args[target_index], state, symbols).kind != "label"
        uses_label_value = any(_branch_target_arg(arg, state, symbols).kind == "label" and index != target_index for index, arg in enumerate(instruction.args))
        if uses_numeric_target or uses_label_value:
            _end_concrete_execution(
                state, "blocked",
                "Compiled instruction-address mapping is unsupported for numeric/register branch targets "
                "or label values in programs containing expanded loop/jlt/jge instructions.", instruction,
            )
            return False
    scope = _scope(symbols, state)
    condition_disabled = _set_cond_is_disabled(state, instruction, symbols)
    destinations = _register_destination_argument_indexes(instruction.op)
    for index, arg in enumerate(instruction.args):
        if index in destinations or (condition_disabled and index in {1, 2}):
            continue
        value = resolve_arg_value(arg, scope)
        if not isinstance(value, Concrete):
            if allow_symbolic_operands and arg.kind != "label":
                continue
            status = "error" if arg.kind == "label" else "blocked"
            _end_concrete_execution(state, status, f"Operand {arg.raw} is unresolved: {value}.", instruction)
            return False
    if has_invalid_arguments:
        _end_concrete_execution(state, "error", "Invalid instruction operands.", instruction)
        return False
    if spec.category == "latched":
        for index, role, lower, upper in _official_operand_ranges(instruction.op):
            if condition_disabled and index in {1, 2}:
                continue
            arg = instruction.args[index]
            if _register_name(arg, state) is None:
                continue
            value = _latched_operand_value(instruction.op, arg, scope, state)
            if allow_symbolic_operands and not isinstance(value, Concrete):
                continue
            if not lower <= value.value <= upper:
                _end_concrete_execution(
                    state, "blocked",
                    f"{instruction.op} register {arg.raw} decodes to {value.value}, outside the supported "
                    f"{role} range {lower}..{upper}; truncation or wrapping is not modeled.", instruction,
                )
                return False
    if spec.category == "branch":
        target = resolve_arg_value(instruction.args[target_index], scope)
        if target.value not in state.instructions_by_pc:
            _end_concrete_execution(state, "error", f"Branch target PC {target.value} has no instruction.", instruction)
            return False
    if instruction.op in STATUS_BRANCH_OPS or (instruction.op == "jge" and len(instruction.args) == 1):
        if _status_branch_decision(instruction.op, state.alu_flags) is None:
            _end_concrete_execution(state, "blocked", "Branch status flags are unknown.", instruction)
            return False
    if instruction.op in {"asl", "asr", "lsl", "lsr"}:
        _, shift_arg = _docs_ordered_binary_args(instruction, state)
        shift = resolve_arg_value(shift_arg, scope)
        if allow_symbolic_operands and not isinstance(shift, Concrete):
            return True
        if not 0 <= shift.value <= 32:
            _end_concrete_execution(state, "blocked", "Shift count is outside the supported range 0..32.", instruction)
            return False
    return True


def _run_program_loop(
    state: AnalysisState,
    symbols: SymbolTable,
    waveform_names: dict[int, str],
    acquisition_names: dict[int, str],
    *,
    stop_pc: int | None = None,
) -> None:
    while state.pc < len(state.instructions_by_pc):
        if state.stopped or state.pc == stop_pc:
            break
        _apply_ready_register_writes(state)
        instruction = state.instructions_by_pc[state.pc]
        state.executed_pcs.add(state.pc)
        state.next_pc = state.pc + 1
        state.current_resolved_args = _resolved_args_for_instruction(
            instruction,
            _scope(symbols, state),
            state,
        )
        try:
            _execute_instruction(
                state,
                instruction,
                symbols,
                waveform_names or {},
                acquisition_names or {},
            )
        finally:
            state.current_resolved_args = []
        state.q1_issue_index += 1
        if state.next_pc is None:
            break
        state.pc = state.next_pc


def _symbol_origins(
    params: dict[str, Any],
    param_symbols: SymbolTable,
    defs: dict[str, Arg],
    resolved_defs: SymbolTable,
) -> dict[str, Any]:
    param_origins = {
        name: {
            "kind": "param",
            "name": name,
            "raw_value": raw_value,
            "value": value_to_json(param_symbols[name]),
        }
        for name, raw_value in params.items()
        if name in param_symbols
    }
    def_origins = {
        name: {
            "kind": "def",
            "name": name,
            "raw": arg.raw,
            "value": value_to_json(resolved_defs[name]),
            "references": _arg_symbol_lookup_names(arg),
        }
        for name, arg in defs.items()
        if name in resolved_defs
    }
    return {"params": param_origins, "defs": def_origins}


def _resolved_args_for_instruction(
    instruction: Instr,
    scope: SymbolTable,
    state: AnalysisState,
) -> list[dict[str, Any]]:
    resolved_args: list[dict[str, Any]] = []
    has_resolution_chain = False
    for index, arg in enumerate(instruction.args):
        value = resolve_arg_value(arg, scope)
        entry: dict[str, Any] = {
            "index": index,
            "raw": arg.raw,
            "kind": arg.kind,
            "value": value_to_json(value),
        }
        chain = _resolution_chain_for_arg(arg, state.symbol_origins)
        if chain:
            entry["chain"] = chain
            has_resolution_chain = True
        resolved_args.append(entry)
    return resolved_args if has_resolution_chain else []


def _resolution_chain_for_arg(arg: Arg, symbol_origins: dict[str, Any]) -> list[dict[str, Any]]:
    params = symbol_origins.get("params", {})
    defs = symbol_origins.get("defs", {})
    for lookup_name in _arg_symbol_lookup_names(arg):
        if lookup_name in defs:
            origin = dict(defs[lookup_name])
            references = list(origin.pop("references", []))
            chain = [origin]
            chain.extend(params[name] for name in references if name in params)
            return chain
        if lookup_name in params:
            return [params[lookup_name]]
    return []


def _arg_symbol_lookup_names(arg: Arg) -> list[str]:
    if arg.kind not in {"symbol", "placeholder"}:
        return []
    name = str(arg.value)
    if name.startswith("$") and len(name) > 1:
        return [name[1:], name]
    return [name]


def _diagnose_program(state: AnalysisState, symbols: SymbolTable, program: Program) -> None:
    # Reuse operand rules without inventing runtime register values or readiness.
    probe = AnalysisState(
        sequencer_id=state.sequencer_id,
        labels=state.labels,
        instructions_by_pc=state.instructions_by_pc,
        register_aliases=state.register_aliases,
        symbol_origins=state.symbol_origins,
        strict_q1asm=state.strict_q1asm,
        execution_mode="concrete",
    )
    # Removing an inline label can invalidate another branch to that label.
    while True:
        probe.labels = state.labels
        probe.instructions_by_pc = state.instructions_by_pc
        probe.diagnostics = []
        for instruction in state.instructions_by_pc.values():
            _validate_instruction(probe, instruction, symbols)
        errors = [item for item in probe.diagnostics
                  if item.severity == "error" and item not in state.static_diagnostics]
        state.static_diagnostics.extend(errors)
        state.diagnostics.extend(errors)
        if state.strict_q1asm or not _exclude_invalid_source_lines(state):
            break
        defs = {name: arg for name, arg in program.defs.items() if arg.kind != "reg"}
        resolved = resolve_def_values(defs, symbols, labels=state.labels)
        symbols.update(resolved)
        for name, value in resolved.items():
            state.symbol_origins["defs"][name]["value"] = value_to_json(value)
        state.immutable_value_origins = _build_immutable_value_origins(symbols, defs, state)
    if not state.strict_q1asm:
        scope = _scope(symbols, probe)
        for instruction in state.instructions_by_pc.values():
            for arg in instruction.args:
                if (arg.kind == "symbol" and not str(arg.value).startswith("$")
                        and not _is_bare_defined_symbol(arg, probe)):
                    diagnostics = []
                    resolve_arg_value(arg, scope, diagnostics, required=True, source=instruction.source)
                    state.static_diagnostics.extend(diagnostics)
                    state.diagnostics.extend(diagnostics)
    recovered_program = replace(program, labels=state.labels, instructions=list(state.instructions_by_pc.values()))
    state.diagnostics.extend(analyze_program_lints(recovered_program, known_parameters=symbols))


def _exclude_invalid_source_lines(state: AnalysisState) -> bool:
    categories = {"syntax_error", "unknown_instruction", "invalid_argument_count",
                  "invalid_argument_type", "invalid_argument_value", "undefined_label"}
    sources = {
        (item.source.file, item.source.line)
        for item in state.diagnostics
        if item.severity == "error" and item.source is not None
        and (item.category in categories or (item.category == "unresolved_symbol"
             and str(item.details.get("operand", "")).startswith("$")))
    }
    if not sources:
        return False
    state.diagnostics = [
        replace(item, details={**item.details, "source_line_skipped": True})
        if item.source is not None and (item.source.file, item.source.line) in sources else item
        for item in state.diagnostics
    ]
    instructions = {}
    pc_map = {}
    removed_labels = set()
    for pc, instruction in state.instructions_by_pc.items():
        pc_map[pc] = len(instructions)
        if (instruction.source.file, instruction.source.line) in sources:
            if state.labels.get(instruction.label) == pc:
                removed_labels.add(instruction.label)
        else:
            instructions[len(instructions)] = replace(instruction, pc=len(instructions))
    pc_map[len(state.instructions_by_pc)] = len(instructions)
    if len(instructions) == len(state.instructions_by_pc):
        return False
    state.instructions_by_pc = instructions
    state.labels = {label: pc_map[pc] for label, pc in state.labels.items() if label not in removed_labels}
    return True


def _validate_instruction(
    state: AnalysisState,
    instruction: Instr,
    symbols: SymbolTable,
) -> tuple[InstructionSpec, bool, int]:
    spec = get_instruction_spec(instruction.op)
    diagnostic_start = len(state.diagnostics)
    validation_diagnostics = spec.validate(instruction)
    state.diagnostics.extend(validation_diagnostics)
    has_raw_invalid_arguments = any(
        diagnostic.category in {"invalid_argument_count", "invalid_argument_type"}
        for diagnostic in validation_diagnostics
    )
    has_invalid_register_operands = _diagnose_invalid_register_operands(state, instruction)
    has_invalid_bare_def_operands = _diagnose_bare_def_symbol_operands(state, instruction)
    has_unresolved_dollar_symbols = _diagnose_unresolved_dollar_symbol_operands(state, instruction)
    has_unresolved_strict_symbols = _diagnose_strict_q1asm_unresolved_operands(state, instruction, symbols)
    has_undefined_labels = _diagnose_undefined_label_operands(state, instruction, symbols)
    if not has_raw_invalid_arguments:
        _diagnose_resolved_argument_types(state, instruction, spec, symbols)
    _diagnose_unresolved_register_operands(state, instruction, spec)
    _diagnose_register_read_hazards(state, instruction)
    has_invalid_arguments = any(
        diagnostic.category in {"invalid_argument_count", "invalid_argument_type"}
        for diagnostic in state.diagnostics[diagnostic_start:]
    ) or has_invalid_register_operands or has_invalid_bare_def_operands or has_unresolved_dollar_symbols or has_unresolved_strict_symbols
    if has_undefined_labels and (state.execution_mode != "analysis" or spec.category != "branch"):
        has_invalid_arguments = True
    has_invalid_arguments = _diagnose_negative_rt_duration_operand(
        state,
        instruction,
        spec,
        symbols,
    ) or has_invalid_arguments
    has_invalid_arguments = _diagnose_official_rt_duration_range(
        state,
        instruction,
        spec,
        symbols,
    ) or has_invalid_arguments
    has_invalid_arguments = _diagnose_invalid_rt_index_operands(
        state,
        instruction,
        symbols,
    ) or has_invalid_arguments
    has_invalid_arguments = _diagnose_official_operand_ranges(
        state,
        instruction,
        symbols,
    ) or has_invalid_arguments

    if state.static_diagnostics:
        state.diagnostics[diagnostic_start:] = [
            item for item in state.diagnostics[diagnostic_start:]
            if item not in state.static_diagnostics
        ]
    return spec, has_invalid_arguments, diagnostic_start


def _diagnose_undefined_label_operands(
    state: AnalysisState, instruction: Instr, symbols: SymbolTable,
) -> bool:
    missing_labels: set[str] = set()
    target_index = None
    if get_instruction_spec(instruction.op).category == "branch":
        target_index = 1 if instruction.op == "loop" else 2 if len(instruction.args) == 3 else 0
    for index, arg in enumerate(instruction.args):
        if _register_name(arg, state) is not None:
            continue
        resolved = _branch_target_arg(arg, state, symbols)
        if resolved.kind == "label" and str(resolved.value) not in state.labels:
            missing_labels.add(str(resolved.value))
        elif (resolved.kind == "label" and index == target_index
              and state.labels[str(resolved.value)] not in state.instructions_by_pc):
            state.diagnostics.append(Diagnostic(
                severity="error", category="invalid_argument_value",
                message=f"Branch label @{resolved.value} does not point to an instruction.",
                source=instruction.source, details={"op": instruction.op, "label": str(resolved.value)},
            ))
    for label in sorted(missing_labels):
        _emit_undefined_label(state, instruction, label)
    return bool(missing_labels)


def _execute_instruction(
    state: AnalysisState,
    instruction: Instr,
    symbols: SymbolTable,
    waveform_names: dict[int, str],
    acquisition_names: dict[int, str],
    *,
    allow_symbolic_operands: bool = False,
) -> None:
    register_metadata = _register_issue_metadata(state, instruction, _scope(symbols, state))
    spec, has_invalid_arguments, diagnostic_start = _validate_instruction(state, instruction, symbols)

    if state.execution_mode == "concrete" and not _concrete_instruction_ready(
        state, instruction, symbols, spec, has_invalid_arguments, diagnostic_start,
        supported_ops=(_CONCRETE_OPS | {instruction.op}) if allow_symbolic_operands and instruction.op != "illegal" else None,
        allow_symbolic_operands=allow_symbolic_operands,
    ):
        return

    q1_t0 = state.q1_time_ns
    q1_duration = Concrete(spec.q1_time_model(_instruction_for_q1_timing(instruction, state, symbols)))
    q1_t1 = add_values(q1_t0, q1_duration)
    state.q1_time_ns = q1_t1

    rt_packet_id: str | None = None
    if has_invalid_arguments:
        _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id, register_metadata=register_metadata)
        return

    _diagnose_analysis_incomplete_timing_effect(state, instruction, spec, symbols)

    if spec.category == "unknown":
        _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id, register_metadata=register_metadata)
        return

    if spec.category == "classical":
        _execute_classical(state, instruction, symbols)
        _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id, register_metadata=register_metadata)
        return

    if spec.category == "latched":
        pending_event = _execute_latched(state, instruction, symbols)
        q1_issue_event = _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id, register_metadata=register_metadata)
        if pending_event is not None:
            pending_event.meta["q1_issue_event_id"] = q1_issue_event.id
            if instruction.op == "set_cond":
                q1_issue_event.meta["conditional_enabled"] = pending_event.meta["conditional_enabled"]
        return

    if spec.category == "feedback":
        _execute_feedback_register_write(state, instruction, symbols)
        q1_issue_event = _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id, register_metadata=register_metadata)
        _emit_feedback_annotation(state, instruction, q1_issue_event, symbols)
        return

    if instruction.op in {"stop", "illegal"}:
        _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id, register_metadata=register_metadata)
        if instruction.op == "illegal":
            state.diagnostics.append(
                Diagnostic(
                    severity="warning",
                    category="illegal_instruction",
                    message="Illegal Q1ASM instruction executed.",
                    source=instruction.source,
                    details={"op": instruction.op},
                )
            )
        _emit_event(
            state,
            lane="rt.control",
            kind=instruction.op,
            t0=state.rt_time_ns,
            t1=state.rt_time_ns,
            duration=Concrete(0),
            label=instruction.op,
            confidence=_confidence_for_timing(Concrete(0), state.rt_time_ns, state.rt_time_ns),
            source=instruction.source,
            meta={"output_state_unknown": False} if state.conditional_time_unknown else None,
        )
        state.stopped = True
        return

    if spec.emits_rt_packet:
        rt_packet_id = f"{state.sequencer_id}:p{len(state.rt_packets)}"
    q1_issue_event = _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id, register_metadata=register_metadata)

    if spec.category == "branch":
        if state.execution_mode == "concrete":
            _execute_concrete_branch(state, instruction, q1_issue_event, symbols)
            return
        _execute_branch(state, instruction, q1_issue_event, symbols, waveform_names, acquisition_names)
        if state.stopped:
            _flush_pending_register_writes(state, completed_issue_index=state.q1_issue_index + 1)
            _invalidate_register_history(
                state,
                set(state.registers),
                source=instruction.source,
                event_id=q1_issue_event.id,
                reason="register state after unresolved branch",
                meta={"branch_id": q1_issue_event.meta.get("branch_id")},
            )
        return

    if spec.category in {"rt", "sync"}:
        if state.conditional_enable != Concrete(0):
            q1_issue_event.meta["conditional_execution_unknown"] = True
            q1_issue_event.meta["conditional_active"] = True
        event_start = len(state.events)
        phase_reset = state.pending_phase_reset if spec.applies_latched_state else False
        nco_pending = set(state.latched_state.pending_since) & {"frequency", "phase", "phase_delta"}
        nco_settings = {
            name: getattr(state.latched_state, name) or Concrete(0)
            for name in {"frequency", "phase"} | nco_pending
        }
        if spec.applies_latched_state:
            state.pending_phase_reset = False
        _execute_rt_instruction(
            state,
            instruction,
            symbols,
            waveform_names,
            acquisition_names,
            q1_t0,
            q1_t1,
            rt_packet_id,
            q1_issue_event,
        )
        if spec.applies_latched_state:
            _finish_nco_issue(state, q1_issue_event, rt_packet_id, event_start,
                              phase_reset, nco_pending, nco_settings)
        for event in state.events[event_start:]:
            if event.meta.get("rt_packet_id") == rt_packet_id:
                event.meta["q1_issue_event_id"] = q1_issue_event.id


def _finish_nco_issue(
    state: AnalysisState, issue: TimelineEvent, packet_id: str | None, event_start: int,
    phase_reset: bool | None, pending: set[str], settings: dict[str, Value],
) -> None:
    events = [event for event in state.events[event_start:] if event.meta.get("rt_packet_id") == packet_id]
    skipped = bool(issue.meta.get("conditional_skipped"))
    unknown_execution = bool(issue.meta.get("conditional_execution_unknown")) or any(
        event.kind == "unknown_region" for event in events
    )
    packet = state.rt_packets[-1] if state.rt_packets and state.rt_packets[-1].id == packet_id else None
    known_settings = all(isinstance(value, Concrete) for value in settings.values())
    if not skipped:
        if unknown_execution:
            state.nco_phase_uncertain |= bool(pending) or phase_reset is not False
        elif phase_reset is True and known_settings:
            state.nco_phase_uncertain = False
        elif phase_reset is None or any(not isinstance(settings[name], Concrete) for name in pending):
            state.nco_phase_uncertain = True
    if packet is not None and not isinstance(packet.duration, Concrete) and settings["frequency"] != Concrete(0):
        state.nco_phase_uncertain = True
    meta: dict[str, Any] = {"captured_phase_reset": phase_reset}
    if not skipped and not unknown_execution and phase_reset is True:
        meta["phase_reset"] = True
        meta["nco_state_at_issue"] = {name: _plain_value(value) for name, value in settings.items()}
    if state.nco_phase_uncertain:
        meta["nco_phase_uncertain"] = True
        if not skipped:
            meta["output_state_unknown"] = True
    issue.meta.update(meta)
    if packet is not None:
        packet.meta.update(meta)
    for event in events:
        event.meta.update(meta)


def _diagnose_analysis_incomplete_timing_effect(
    state: AnalysisState,
    instruction: Instr,
    spec: InstructionSpec,
    symbols: SymbolTable,
) -> None:
    if spec.timing_effect_status != "analysis_incomplete" or _set_cond_is_disabled(state, instruction, symbols):
        return
    if instruction.op == "set_cond":
        values = [resolve_arg_value(arg, _scope(symbols, state)) for arg in instruction.args[:3]]
        if len(values) == 3 and _conditional_decision(
            *values, enable_bounds=_unsigned_operand_bounds(instruction.args[0], values[0], state),
            operator_bounds=_unsigned_operand_bounds(instruction.args[2], values[2], state),
            enable_known_bits=_operand_known_bits(instruction.args[0], values[0], state),
            operator_known_bits=_operand_known_bits(instruction.args[2], values[2], state),
        ) is not None:
            return
    state.diagnostics.append(
        Diagnostic(
            severity="warning",
            category="analysis_incomplete",
            message=("Conditional execution depends on trigger state." if instruction.op == "set_cond"
                     else f"Timing effect is unknown for instruction: {instruction.op}"),
            source=instruction.source,
            details={"op": instruction.op},
        )
    )


def _set_cond_is_disabled(state: AnalysisState, instruction: Instr, symbols: SymbolTable) -> bool:
    return (instruction.op == "set_cond" and bool(instruction.args)
            and resolve_arg_value(instruction.args[0], _scope(symbols, state)) == Concrete(0))


def _conditional_control_values(
    value: Value, bounds: tuple[int, int] | None, maximum: int, known_bits: tuple[int, int] | None = None,
) -> tuple[int, ...] | None:
    if isinstance(value, Concrete):
        return (value.value,) if 0 <= value.value <= maximum else None
    ones, zeros = known_bits or (0, 0)
    if known_bits is not None:
        bounds = _refine_bounds_with_bits(bounds, known_bits)
    if bounds is None or not 0 <= bounds[0] <= bounds[1] < 1 << maximum.bit_length():
        return None
    values = tuple(item for item in range(bounds[0], bounds[1] + 1) if item & ones == ones and not item & zeros)
    return values if values and max(values) <= maximum else None


def _conditional_decision(
    enable: Value, mask: Value, operator: Value, *, enable_bounds: tuple[int, int] | None = None,
    operator_bounds: tuple[int, int] | None = None, enable_known_bits: tuple[int, int] | None = None,
    operator_known_bits: tuple[int, int] | None = None,
) -> bool | None:
    enables = _conditional_control_values(enable, enable_bounds, 1, enable_known_bits)
    if enables == (0,):
        return True
    operators = _conditional_control_values(operator, operator_bounds, 5, operator_known_bits)
    if enables is None or mask != Concrete(0) or operators is None:
        return None
    # An empty trigger selection makes OR/NAND/XOR false and NOR/AND/XNOR true.
    decisions = {enabled == 0 or operation in {1, 2, 5} for enabled in enables for operation in operators}
    return decisions.pop() if len(decisions) == 1 else None


def _equal_conditional_durations(state: AnalysisState, nominal: Value | None) -> bool:
    return (_conditional_control_values(state.conditional_operator, state.conditional_operator_bounds, 5,
                                        state.conditional_operator_known_bits) is not None
            and isinstance(nominal, Concrete) and nominal.value > 0 and nominal == state.conditional_else_duration)


def _diagnose_unresolved_register_operands(
    state: AnalysisState,
    instruction: Instr,
    spec: InstructionSpec,
) -> None:
    for index in _register_only_argument_indexes(spec, len(instruction.args)):
        arg = instruction.args[index]
        if arg.kind not in {"symbol", "placeholder"}:
            continue
        if _is_bare_defined_symbol(arg, state):
            continue
        if _register_name(arg, state) is not None:
            continue
        state.diagnostics.append(
            Diagnostic(
                severity="error",
                category="invalid_argument_type",
                message=(
                    f"{instruction.op} argument {index + 1} must be a register or defined "
                    f"register alias, got {arg.raw}."
                ),
                source=instruction.source,
                details={
                    "op": instruction.op,
                    "argument_index": index,
                    "operand": arg.raw,
                    "expected": "register",
                },
            )
        )


def _diagnose_invalid_register_operands(state: AnalysisState, instruction: Instr) -> bool:
    emitted = False
    for index, arg in enumerate(instruction.args):
        if arg.kind != "reg" or _is_valid_register_arg(arg):
            continue
        state.diagnostics.append(
            Diagnostic(
                severity="error",
                category="invalid_argument_type",
                message=f"{instruction.op} argument {index + 1} must use an uppercase register R0..R63, got {arg.raw}.",
                source=instruction.source,
                details={
                    "op": instruction.op,
                    "argument_index": index,
                    "operand": arg.raw,
                    "expected": "R0..R63",
                },
            )
        )
        emitted = True
    return emitted


def _diagnose_bare_def_symbol_operands(state: AnalysisState, instruction: Instr) -> bool:
    emitted = False
    for index, arg in enumerate(instruction.args):
        if not _is_bare_defined_symbol(arg, state):
            continue
        state.diagnostics.append(
            Diagnostic(
                severity="error",
                category="invalid_argument_type",
                message=f"{instruction.op} argument {index + 1} must reference .DEF symbols with $NAME syntax, got {arg.raw}.",
                source=instruction.source,
                details={
                    "op": instruction.op,
                    "argument_index": index,
                    "operand": arg.raw,
                    "expected": "$NAME",
                },
            )
        )
        emitted = True
    return emitted


def _diagnose_unresolved_dollar_symbol_operands(state: AnalysisState, instruction: Instr) -> bool:
    emitted = False
    defined_symbols = (
        set(state.symbol_origins.get("defs", {}))
        | set(state.symbol_origins.get("params", {}))
        | set(state.register_aliases)
    )
    for index, arg in enumerate(instruction.args):
        if arg.kind != "symbol":
            continue
        raw_name = str(arg.value)
        if not raw_name.startswith("$") or len(raw_name) <= 1:
            continue
        name = raw_name[1:]
        if name in defined_symbols or raw_name in defined_symbols:
            continue
        state.diagnostics.append(
            Diagnostic(
                severity="error",
                category="unresolved_symbol",
                message=f"Unresolved .DEF symbol: {name}.",
                source=instruction.source,
                details={
                    "op": instruction.op,
                    "argument_index": index,
                    "operand": raw_name,
                    "symbol": name,
                },
            )
        )
        emitted = True
    return emitted


def _diagnose_strict_q1asm_unresolved_operands(
    state: AnalysisState,
    instruction: Instr,
    symbols: SymbolTable,
) -> bool:
    if not state.strict_q1asm:
        return False
    emitted = False
    scope = _scope(symbols, state)
    for index, arg in enumerate(instruction.args):
        if arg.kind not in {"symbol", "placeholder"}:
            continue
        if arg.kind == "symbol" and str(arg.value).startswith("$"):
            continue
        if _register_name(arg, state) is not None:
            continue
        value = resolve_arg_value(arg, scope)
        if not isinstance(value, Symbolic):
            continue
        state.diagnostics.append(
            Diagnostic(
                severity="error",
                category="unresolved_symbol",
                message=f"Unresolved Q1ASM operand in strict mode: {arg.raw}.",
                source=instruction.source,
                details={
                    "op": instruction.op,
                    "argument_index": index,
                    "operand": arg.raw,
                    "symbol": str(arg.value),
                    "strict_q1asm": True,
                },
            )
        )
        emitted = True
    return emitted


def _diagnose_strict_q1asm_placeholder_defs(state: AnalysisState, defs: dict[str, Arg]) -> None:
    for name, arg in defs.items():
        if arg.kind != "placeholder":
            continue
        state.diagnostics.append(
            Diagnostic(
                severity="error",
                category="unresolved_symbol",
                message=f"Unresolved .DEF placeholder in strict mode: {arg.raw}.",
                details={
                    "symbol": name,
                    "operand": arg.raw,
                    "strict_q1asm": True,
                },
            )
        )


def _is_bare_defined_symbol(arg: Arg, state: AnalysisState) -> bool:
    if arg.kind != "symbol":
        return False
    name = str(arg.value)
    if name.startswith("$"):
        return False
    defined_symbols = set(state.symbol_origins.get("defs", {})) | set(state.register_aliases)
    return name in defined_symbols


def _diagnose_official_label_operand_roles(state: AnalysisState, instruction: Instr) -> bool:
    emitted = False
    for argument_index, operand_role in _label_disallowed_operand_roles(instruction.op):
        if argument_index >= len(instruction.args):
            continue
        arg = instruction.args[argument_index]
        if arg.kind != "label":
            continue
        state.diagnostics.append(
            Diagnostic(
                severity="error",
                category="invalid_argument_type",
                message=f"{instruction.op} {operand_role.replace('_', ' ')} does not accept label operands: {arg.raw}.",
                source=instruction.source,
                details={
                    "op": instruction.op,
                    "argument_index": argument_index,
                    "operand": arg.raw,
                    "operand_role": operand_role,
                    "expected": "integer",
                },
            )
        )
        emitted = True
    return emitted


def _is_valid_register_arg(arg: Arg) -> bool:
    if arg.kind != "reg" or not isinstance(arg.value, str):
        return False
    raw = arg.raw
    if not raw.startswith("R"):
        return False
    index = _register_index_from_raw(raw)
    return index is not None and 0 <= index <= _REGISTER_MAX_INDEX


def _register_index_from_raw(raw: str) -> int | None:
    text = raw[1:]
    if not text:
        return None
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        if text.isdigit():
            if len(text) > 1 and text.startswith("0"):
                return int(text, 8)
            return int(text, 10)
    except ValueError:
        return None
    return None


def _diagnose_resolved_argument_types(
    state: AnalysisState,
    instruction: Instr,
    spec: InstructionSpec,
    symbols: SymbolTable,
) -> None:
    if not instruction.args:
        return
    candidates = [signature for signature in spec.signatures if len(signature.args) == len(instruction.args)]
    if not candidates:
        return

    resolved_instruction = _instruction_for_q1_timing(instruction, state, symbols)
    if resolved_instruction.args == instruction.args:
        return
    actual_types = [_resolved_arg_type(arg) for arg in resolved_instruction.args]
    if any(arg_type is None for arg_type in actual_types):
        return
    if any(_resolved_signature_matches(signature, actual_types) for signature in candidates):
        return

    state.diagnostics.append(
        Diagnostic(
            severity="error",
            category="invalid_argument_type",
            message=(
                f"{instruction.op} resolved arguments do not match any supported signature: "
                f"{_resolved_signature_list_display(candidates)}."
            ),
            source=instruction.source,
            details={
                "op": instruction.op,
                "expected_signatures": [_resolved_signature_display(signature) for signature in candidates],
                "actual": [arg.raw for arg in instruction.args],
                "actual_resolved_types": actual_types,
            },
        )
    )


def _resolved_arg_type(arg: Arg) -> str | None:
    if arg.kind == "imm":
        return "I"
    if arg.kind == "reg":
        return "R"
    if arg.kind == "label":
        return "L"
    return None


def _resolved_signature_matches(signature, actual_types: list[str | None]) -> bool:
    return all(
        arg_type in accepted or (arg_type == "L" and "I" in accepted)
        for accepted, arg_type in zip(signature.args, actual_types, strict=True)
    )


def _resolved_signature_list_display(signatures) -> str:
    return "; ".join(_resolved_signature_display(signature) for signature in signatures)


def _resolved_signature_display(signature) -> str:
    if not signature.args:
        return "()"
    return "(" + ", ".join("/".join(sorted(arg_types)) for arg_types in signature.args) + ")"


def _diagnose_register_read_hazards(state: AnalysisState, instruction: Instr) -> None:
    if not state.pending_register_writes:
        return
    for argument_index, register in _register_read_operands(instruction, state):
        pending = _pending_register_write(state, register)
        if pending is None:
            continue
        state.diagnostics.append(
            Diagnostic(
                severity="warning",
                category="register_not_ready",
                message=(
                    f"{instruction.op} reads {register} before the previous write is available; "
                    "insert an instruction gap such as nop."
                ),
                source=instruction.source,
                details={
                    "op": instruction.op,
                    "argument_index": argument_index,
                    "register": register,
                    "writer": _source_to_json(pending.source),
                    "available_issue_index": pending.available_issue_index,
                    "current_issue_index": state.q1_issue_index,
                },
            )
        )


def _diagnose_static_register_read_hazards(state: AnalysisState) -> None:
    previous_instruction: Instr | None = None
    previous_writes: set[str] = set()

    for instruction in state.instructions_by_pc.values():
        reads = _register_read_operands(instruction, state)
        if previous_instruction is not None:
            read_by_register = {register: argument_index for argument_index, register in reads}
            for register in sorted(previous_writes & read_by_register.keys()):
                if _has_register_not_ready_diagnostic(
                    state,
                    instruction.source,
                    register,
                    previous_instruction.source,
                ):
                    continue
                state.diagnostics.append(
                    Diagnostic(
                        severity="warning",
                        category="register_not_ready",
                        message=(
                            f"{instruction.op} reads {register} immediately after "
                            f"{previous_instruction.op}; insert an instruction gap such as nop."
                        ),
                        source=instruction.source,
                        details={
                            "op": instruction.op,
                            "argument_index": read_by_register[register],
                            "register": register,
                            "writer": _source_to_json(previous_instruction.source),
                            "analysis": "static",
                        },
                    )
                )

        previous_instruction = instruction
        previous_writes = _register_write_operands(instruction, state)


def _has_register_not_ready_diagnostic(
    state: AnalysisState,
    source: SourceLocation,
    register: str,
    writer_source: SourceLocation,
) -> bool:
    for diagnostic in state.diagnostics:
        if diagnostic.category != "register_not_ready" or diagnostic.source is None:
            continue
        if diagnostic.source.line != source.line or diagnostic.source.file != source.file:
            continue
        if diagnostic.details.get("register") != register:
            continue
        writer = diagnostic.details.get("writer")
        if not isinstance(writer, dict):
            continue
        if writer.get("line") == writer_source.line and writer.get("file") == writer_source.file:
            return True
    return False


def _physical_read_register(arg: Arg, state: AnalysisState) -> str | None:
    register = _register_name(arg, state)
    if register is not None or arg.kind not in {"symbol", "placeholder"}:
        return register
    name = str(arg.value)
    if name.startswith("$"):
        alias = state.register_aliases.get(name[1:])
        if alias is not None:
            return alias
        declared = set(state.symbol_origins.get("params", {})) | set(state.symbol_origins.get("defs", {}))
        if name in declared or name[1:] in declared:
            return None
        name = name[1:]
    index = _register_index_from_raw(name) if name.startswith("R") else None
    if index is not None and 0 <= index <= _REGISTER_MAX_INDEX and name == f"R{index}":
        return name
    if not _is_valid_register_arg(Arg("reg", name, name)):
        return state.register_aliases.get(name)
    return None


def _register_read_operands(instruction: Instr, state: AnalysisState) -> list[tuple[int, str]]:
    destination_indexes = _register_destination_argument_indexes(instruction.op)
    reads: list[tuple[int, str]] = []
    for index, arg in enumerate(instruction.args):
        if index in destination_indexes:
            continue
        register = _physical_read_register(arg, state)
        if register is not None:
            reads.append((index, register))
    return reads


def _register_issue_metadata(state: AnalysisState, instruction: Instr, scope: SymbolTable) -> dict[str, Any]:
    reads = {
        register: value_to_json(resolve_arg_value(Arg("reg", register, register), scope))
        for _, register in _register_read_operands(instruction, state)
    }
    # Keep values that the preceding RT command did not itself read, so first
    # displaying a register does not falsely imply that its value just changed.
    snapshot = {register: value_to_json(value) for register, value in state.registers.items()}
    snapshot.update(reads)
    bounds = {register: list(_unsigned_operand_bounds(Arg("reg", register, register), scope.get(register, Unknown(register)), state))
              for register in reads}
    return {"register_reads": reads, "register_snapshot": snapshot, "register_read_bounds": bounds}


def _register_write_operands(instruction: Instr, state: AnalysisState) -> set[str]:
    writes: set[str] = set()
    for index in _register_destination_argument_indexes(instruction.op):
        if index >= len(instruction.args):
            continue
        register = _register_name(instruction.args[index], state)
        if register is not None:
            writes.add(register)
    return writes


def _register_destination_argument_indexes(op: str) -> set[int]:
    if op in {"move", "not"}:
        return {1}
    if op in {"add", "sub", "and", "or", "xor", "asl", "asr", "lsl", "lsr", "mulu16", "muls16", "mulu32l", "mulu32h", "muls32l", "muls32h"}:
        return {2}
    if op in {"mulu32", "muls32"}:
        return {2, 3}
    if op == "fb_pop_data":
        return {1}
    if op == "fb_pull_data":
        return {0, 1}
    return set()


def _pending_register_write(state: AnalysisState, register: str) -> PendingRegisterWrite | None:
    pending_writes = [
        pending
        for pending in state.pending_register_writes
        if pending.register == register and pending.available_issue_index > state.q1_issue_index
    ]
    if not pending_writes:
        return None
    return min(pending_writes, key=lambda item: item.available_issue_index)


def _register_only_argument_indexes(spec: InstructionSpec, actual_count: int) -> set[int]:
    candidates = [signature for signature in spec.signatures if len(signature.args) == actual_count]
    return {
        index
        for index in range(actual_count)
        if candidates and all(signature.args[index] == frozenset({"R"}) for signature in candidates)
    }


def _register_aliases(defs: dict[str, Arg]) -> dict[str, str]:
    return {name: str(arg.value).upper() for name, arg in defs.items() if arg.kind == "reg"}


def _apply_ready_register_writes(state: AnalysisState) -> None:
    if not state.pending_register_writes:
        return
    remaining: list[PendingRegisterWrite] = []
    for pending in state.pending_register_writes:
        if pending.available_issue_index <= state.q1_issue_index:
            _apply_register_write(state, pending)
        else:
            remaining.append(pending)
    state.pending_register_writes = remaining


def _flush_pending_register_writes(
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
        _apply_register_write(state, pending, time=time)
    state.pending_register_writes = []


def _fresh_value_origin(state: AnalysisState) -> tuple[str, bool]:
    state.value_origin_serial = next(_VALUE_ORIGIN_SERIALS)
    return f"write:{state.value_origin_serial}", False


def _apply_register_write(state: AnalysisState, pending: PendingRegisterWrite, *, time: Value | None = None) -> None:
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
    state.register_value_origins[pending.register] = pending.value_origin if pending.value_origin is not None else _fresh_value_origin(state)
    state.register_provenance[pending.register] = pending.provenance
    _record_register_write(
        state,
        pending.register,
        pending.value,
        source=pending.source,
        event_id=pending.event_id,
        time=time,
        meta=meta,
    )


def _record_register_write(
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
    write_meta.setdefault("branch_assumptions", _register_branch_assumptions(state))
    confidence = _confidence_for_value((value, write_time))
    if confidence == "exact" and write_meta.get("branch_assumptions"):
        confidence = "assumed"
    state.register_history.append(
        RegisterWrite(state.sequencer_id, register, write_time, value, source, event_id, confidence, write_meta)
    )


def _register_branch_assumptions(state: AnalysisState) -> list[dict[str, str]]:
    return [
        {"branch_id": entry["branch_id"], "assumed_branch_path": entry["assumed_branch_path"]}
        for entry in state.metadata.get("branches", {}).get("assumptions", [])
    ]


def _invalidate_register_history(
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
        state.register_value_origins[register] = _fresh_value_origin(state)
        _record_register_write(
            state,
            register,
            Unknown(reason),
            source=source,
            event_id=event_id,
            meta={**(meta or {}), "invalidated": True, "reason": reason},
        )


def _schedule_register_write(
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
        unsigned_bounds, known_bits = _origin_peer_facts(state, value_origin, unsigned_bounds, known_bits)
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
    event_id = f"{state.sequencer_id}:e{len(state.events)}"
    if state.execution_mode == "concrete":
        provenance = _concrete_write_provenance(state, instruction, provenance, event_id)
    state.pending_register_writes.append(
        PendingRegisterWrite(
            register=register,
            value=value,
            provenance=provenance,
            source=instruction.source,
            available_issue_index=state.q1_issue_index + 2,
            event_id=event_id,
            meta={
                "op": instruction.op,
                "branch_assumptions": _register_branch_assumptions(state),
            },
            unsigned_bounds=unsigned_bounds if not isinstance(value, Concrete) else None,
            known_bits=known_bits if not isinstance(value, Concrete) and known_bits != (0, 0) else None,
            value_origin=value_origin if value_origin is not None else _fresh_value_origin(state),
        )
    )


def _concrete_write_provenance(
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
            for _, register in _register_read_operands(instruction, state)
        },
    }


def _diagnose_negative_rt_duration_operand(
    state: AnalysisState,
    instruction: Instr,
    spec: InstructionSpec,
    symbols: SymbolTable,
) -> bool:
    duration_arg_index = rt_duration_arg_for_instruction(spec, instruction)
    if duration_arg_index is None or duration_arg_index >= len(instruction.args):
        return False
    duration_arg = instruction.args[duration_arg_index]
    duration = resolve_arg_value(duration_arg, _scope(symbols, state))
    if not isinstance(duration, Concrete) or duration.value >= 0:
        return False
    state.diagnostics.append(
        Diagnostic(
            severity="error",
            category="invalid_argument_value",
            message=f"{instruction.op} duration must be non-negative, got {duration.value}.",
            source=instruction.source,
            details={
                "op": instruction.op,
                "argument_index": duration_arg_index,
                "operand": duration_arg.raw,
                "constraint": "non_negative",
                "value": duration.value,
            },
        )
    )
    return True


def _diagnose_official_rt_duration_range(
    state: AnalysisState,
    instruction: Instr,
    spec: InstructionSpec,
    symbols: SymbolTable,
) -> bool:
    duration_arg_index = rt_duration_arg_for_instruction(spec, instruction)
    if duration_arg_index is None or duration_arg_index >= len(instruction.args):
        return False
    duration_arg = instruction.args[duration_arg_index]
    if _register_name(duration_arg, state) is not None:
        return False
    duration = resolve_arg_value(duration_arg, _scope(symbols, state))
    if not isinstance(duration, Concrete) or duration.value <= _RT_DURATION_MAX:
        return False
    state.diagnostics.append(
        Diagnostic(
            severity="error",
            category="invalid_argument_value",
            message=f"{instruction.op} duration must be in range 0..{_RT_DURATION_MAX}, got {duration.value}.",
            source=instruction.source,
            details={
                "op": instruction.op,
                "argument_index": duration_arg_index,
                "operand": duration_arg.raw,
                "operand_role": "duration",
                "constraint": "range",
                "min": 0,
                "max": _RT_DURATION_MAX,
                "value": duration.value,
            },
        )
    )
    return True


def _diagnose_invalid_rt_index_operands(
    state: AnalysisState,
    instruction: Instr,
    symbols: SymbolTable,
) -> bool:
    emitted = False
    for argument_index, operand_role in _non_negative_operand_roles(instruction.op):
        if argument_index >= len(instruction.args):
            continue
        arg = instruction.args[argument_index]
        value = resolve_arg_value(arg, _scope(symbols, state))
        if isinstance(value, Concrete):
            if _has_official_operand_range(instruction.op, argument_index, operand_role):
                continue
            if value.value >= 0:
                continue
            state.diagnostics.append(
                Diagnostic(
                    severity="error",
                    category="invalid_argument_value",
                    message=f"{instruction.op} {operand_role.replace('_', ' ')} must be non-negative, got {value.value}.",
                    source=instruction.source,
                    details={
                        "op": instruction.op,
                        "argument_index": argument_index,
                        "operand": arg.raw,
                        "operand_role": operand_role,
                        "constraint": "non_negative",
                        "value": value.value,
                    },
                )
            )
            emitted = True
            continue
        if operand_role == "feedback_source" and _register_name(arg, state) is not None:
            continue
        if _should_reject_unresolved_rt_index_operand(instruction.op, operand_role) and _diagnose_unresolved_index_operand(
            state,
            instruction,
            arg,
            argument_index,
            operand_role,
        ):
            emitted = True
    return emitted


def _has_official_operand_range(op: str, argument_index: int, operand_role: str) -> bool:
    return any(
        index == argument_index and role == operand_role
        for index, role, _lower, _upper in _official_operand_ranges(op)
    )


def _diagnose_official_operand_ranges(
    state: AnalysisState,
    instruction: Instr,
    symbols: SymbolTable,
) -> bool:
    if _is_packed_feedback_register_form(instruction, state):
        return False
    emitted = False
    scope = _scope(symbols, state)
    for argument_index, operand_role, lower, upper in _official_operand_ranges(instruction.op, len(instruction.args)):
        if argument_index >= len(instruction.args):
            continue
        arg = instruction.args[argument_index]
        value: Value
        if operand_role == "branch_target":
            target_arg = _branch_target_arg(arg, state, symbols)
            if target_arg.kind != "imm":
                continue
            value = Concrete(int(target_arg.value))
        elif _register_name(arg, state) is not None:
            continue
        else:
            value = resolve_arg_value(arg, scope)
        if not isinstance(value, Concrete):
            continue
        if lower <= value.value <= upper:
            continue
        state.diagnostics.append(
            Diagnostic(
                severity="error",
                category="invalid_argument_value",
                message=(
                    f"{instruction.op} {operand_role.replace('_', ' ')} must be in range "
                    f"{lower}..{upper}, got {value.value}."
                ),
                source=instruction.source,
                details={
                    "op": instruction.op,
                    "argument_index": argument_index,
                    "operand": arg.raw,
                    "operand_role": operand_role,
                    "constraint": "range",
                    "min": lower,
                    "max": upper,
                    "value": value.value,
                },
            )
        )
        emitted = True
    return emitted


def _is_packed_feedback_register_form(instruction: Instr, state: AnalysisState) -> bool:
    return (
        instruction.op in {"fb_com_cfg", "fb_acq_tb_cfg", "fb_com_extra", "fb_acq_tb_extra", "fb_acq_tb_mock"}
        and len(instruction.args) == 2
        and _register_name(instruction.args[0], state) is not None
    )


def _official_operand_ranges(op: str, argument_count: int = 1) -> list[tuple[int, str, int, int]]:
    signed_16 = (-32768, 32767)
    signed_32 = (-2147483648, 2147483647)
    classical_imm = (-2147483648, 0xFFFFFFFF)
    acquisition_idx = (0, 31)
    bin_idx = (0, 16777215)
    feedback_idx = (0, 255)
    if op == "stop":
        return [(0, "stop_status", *signed_32)]
    if op == "sw_req":
        return [(0, "software_request", *signed_32)]
    if op == "play_pulse":
        return [(0, "fine_output_delay", 0, 2047)]
    if op == "play_netzero":
        return [(0, "maximum_compensation_length", 0, 0xFFFFFFFF)]
    if op == "set_mrk":
        return [(0, "marker", 0, 15)]
    if op == "set_awg_gain":
        return [(0, "awg_gain", *signed_16), (1, "awg_gain", *signed_16)]
    if op == "set_awg_offs":
        return [(0, "awg_offset", *signed_16), (1, "awg_offset", *signed_16)]
    if op == "set_freq":
        return [(0, "frequency", *signed_32)]
    if op == "set_ph":
        return [(0, "phase", 0, 1000000000)]
    if op == "set_ph_delta":
        return [(0, "phase_delta", 0, 1000000000)]
    if op == "set_digital":
        return [(0, "digital_value", 0, 255), (1, "digital_mask", 0, 255), (2, "digital_delay", 0, 2047)]
    if op == "set_scope_en":
        return [(0, "scope_enable", 0, 1)]
    if op == "set_cond":
        return [
            (0, "condition_enable", 0, 1),
            (1, "condition_mask", 0, 32767),
            (2, "condition_operator", 0, 7),
            (3, "else_duration", 0, _RT_DURATION_MAX),
        ]
    if op == "set_latch_en":
        return [(0, "enable_flag", 0, 1)]
    if op == "wait_trigger":
        return [(0, "trigger_index", 0, 15)]
    if op == "upd_thres":
        return [(0, "threshold_index", 0, 3), (1, "threshold_value", 0, 0xFFFFFFFF)]
    if op == "play":
        return [(0, "waveform_index", 0, 1023), (1, "waveform_index", 0, 1023)]
    if op in {"acquire", "acquire_digital"}:
        return [(0, "acquisition_index", *acquisition_idx), (1, "bin_index", *bin_idx)]
    if op in {"acquire_weighed", "acquire_weighted"}:
        return [
            (0, "acquisition_index", *acquisition_idx),
            (1, "bin_index", *bin_idx),
            (2, "weight_index", 0, 63),
            (3, "weight_index", 0, 63),
        ]
    if op == "acquire_ttl":
        return [
            (0, "acquisition_index", *acquisition_idx),
            (1, "bin_index", *bin_idx),
            (2, "input_index", 0, 1),
        ]
    if op == "acquire_timetags":
        return [
            (0, "acquisition_index", *acquisition_idx),
            (1, "bin_index", *bin_idx),
            (2, "input_index", 0, 1),
            (3, "tag_index", 0, 2047),
        ]
    if op == "fb_pop_data":
        return [(0, "feedback_pop_tag", 0, 65535)]
    if op == "fb_cmd":
        return [(0, "feedback_channel", 0, 511), (1, "feedback_source", 0, 0xFFFFFFFF)]
    if op == "fb_com_data":
        return [(0, "feedback_channel", *feedback_idx), (1, "feedback_source", 0, 0xFFFFFFFF)]
    if op in {"fb_com_cfg", "fb_acq_tb_cfg"}:
        return [(0, "write_combine_flag", 0, 3), (1, "bit_position", 0, 1023), (2, "payload_length", 0, 127)]
    if op in {"fb_com_extra", "fb_acq_tb_extra"}:
        return [(0, "enable_flag", 0, 1), (1, "extra_payload_bytes", 0, 65535)]
    if op in {"fb_acq_iq_id", "fb_acq_tb_id"}:
        return [(0, "feedback_acquisition_channel", 0, 255)]
    if op == "fb_acq_tb_valid":
        return [(0, "feedback_acquisition_channel", 0, 1)]
    if op in {"fb_llp_tags_id", "fb_llp_ttls_id", "fb_tdc_tags_id", "fb_tdc_tdelta_id"}:
        return [(0, "feedback_channel", 0, 255)]
    if op == "fb_acq_iq_shift":
        return [(0, "shift_count", 0, 63)]
    if op == "fb_acq_tb_mock":
        return [(0, "enable_flag", 0, 1), (1, "valid_bit", 0, 1), (2, "mock_data", 0, 1)]
    if op == "jmp" or (op == "jge" and argument_count == 1):
        return [(0, "branch_target", 0, 16383)]
    if op in {"jge", "jlt"}:
        return [
            (1, "branch_compare_immediate", *classical_imm),
            (2, "branch_target", 0, 16383),
        ]
    if op in STATUS_BRANCH_OPS:
        return [(0, "branch_target", 0, 16383)]
    if op == "loop":
        return [(1, "branch_target", 0, 16383)]
    if op in {"move", "not"}:
        return [(0, "classical_immediate", *classical_imm)]
    if op in {"add", "sub", "and", "or", "xor"}:
        return [(0, "classical_immediate", *classical_imm), (1, "classical_immediate", *classical_imm)]
    if op in {"asl", "asr"}:
        return [
            (0, "signed_shift_immediate", *signed_32),
            (1, "unsigned_shift_immediate", 0, 0xFFFFFFFF),
        ]
    if op in {"lsl", "lsr"}:
        return [
            (0, "unsigned_shift_immediate", 0, 0xFFFFFFFF),
            (1, "unsigned_shift_immediate", 0, 0xFFFFFFFF),
        ]
    if op == "cmp":
        return [(0, "classical_immediate", *classical_imm), (1, "classical_immediate", *classical_imm)]
    if op == "mulu16":
        return [(0, "unsigned_16_immediate", 0, 0xFFFF), (1, "unsigned_16_immediate", 0, 0xFFFF)]
    if op == "muls16":
        return [(0, "signed_16_immediate", -32768, 32767), (1, "signed_16_immediate", -32768, 32767)]
    if op in {"mulu32", "mulu32l", "mulu32h"}:
        return [(0, "unsigned_32_immediate", 0, 0xFFFFFFFF), (1, "unsigned_32_immediate", 0, 0xFFFFFFFF)]
    if op in {"muls32", "muls32l", "muls32h"}:
        return [
            (0, "signed_32_immediate", -2147483648, 2147483647),
            (1, "signed_32_immediate", -2147483648, 2147483647),
        ]
    if op == "test":
        return [(0, "classical_immediate", *classical_imm), (1, "classical_immediate", *classical_imm)]
    return []


def _non_negative_operand_roles(op: str) -> list[tuple[int, str]]:
    roles = list(_rt_index_operand_roles(op))
    if op == "set_cond":
        roles.append((3, "else_duration"))
    elif op == "wait_trigger":
        roles.append((0, "trigger_index"))
    elif op == "set_latch_en":
        roles.append((0, "enable_flag"))
    elif op == "upd_thres":
        roles.append((0, "threshold_index"))
    elif op == "fb_acq_iq_shift":
        roles.append((0, "shift_count"))
    elif op in {"fb_com_cfg", "fb_acq_tb_cfg"}:
        roles.extend([(0, "write_combine_flag"), (1, "bit_position"), (2, "payload_length")])
    elif op in {"fb_com_extra", "fb_acq_tb_extra"}:
        roles.extend([(0, "enable_flag"), (1, "extra_payload_bytes")])
    elif op == "fb_acq_tb_mock":
        roles.extend([(0, "enable_flag"), (1, "valid_bit"), (2, "mock_data")])
    return roles


def _label_disallowed_operand_roles(op: str) -> list[tuple[int, str]]:
    if op in {"acquire_ttl", "acquire_timetags"}:
        return [(2, "input_index")]
    if op == "upd_thres":
        return [(0, "threshold_index")]
    if op == "set_cond":
        return [(0, "condition")]
    if op == "set_latch_en":
        return [(0, "enable_flag")]
    if op == "set_scope_en":
        return [(0, "scope_enable")]
    if op in {"fb_com_cfg", "fb_acq_tb_cfg"}:
        return [(0, "write_combine_flag")]
    if op in {"fb_com_extra", "fb_acq_tb_extra"}:
        return [(0, "enable_flag")]
    if op == "fb_acq_tb_mock":
        return [(0, "enable_flag"), (1, "valid_bit"), (2, "mock_data")]
    if op == "fb_acq_tb_valid":
        return [(0, "feedback_acquisition_channel")]
    return []


def _rt_index_operand_roles(op: str) -> list[tuple[int, str]]:
    if op == "play":
        return [(0, "waveform_index"), (1, "waveform_index")]
    if op == "acquire":
        return [(0, "acquisition_index"), (1, "bin_index")]
    if op in {"acquire_weighed", "acquire_weighted"}:
        return [
            (0, "acquisition_index"),
            (1, "bin_index"),
            (2, "weight_index"),
            (3, "weight_index"),
        ]
    if op == "acquire_ttl":
        return [(0, "acquisition_index"), (1, "bin_index"), (2, "input_index")]
    if op == "acquire_timetags":
        return [
            (0, "acquisition_index"),
            (1, "bin_index"),
            (2, "input_index"),
            (3, "tag_index"),
        ]
    if op == "acquire_digital":
        return [(0, "acquisition_index"), (1, "bin_index")]
    if op in {"fb_com_data", "fb_cmd"}:
        return [(0, "feedback_channel"), (1, "feedback_source")]
    if op in {"fb_acq_iq_id", "fb_acq_tb_id", "fb_acq_tb_valid"}:
        return [(0, "feedback_acquisition_channel")]
    if op in {"fb_llp_tags_id", "fb_llp_ttls_id", "fb_tdc_tags_id", "fb_tdc_tdelta_id"}:
        return [(0, "feedback_channel")]
    return []


def _should_reject_unresolved_rt_index_operand(op: str, operand_role: str) -> bool:
    concrete_required_roles = {
        "bit_position",
        "enable_flag",
        "extra_payload_bytes",
        "mock_data",
        "payload_length",
        "sample_count",
        "shift_count",
        "threshold_index",
        "trigger_index",
        "valid_bit",
        "write_combine_flag",
    }
    return (
        op.startswith("acquire")
        or operand_role.startswith("feedback_")
        or operand_role in concrete_required_roles
    )


def _instruction_for_q1_timing(instruction: Instr, state: AnalysisState, symbols: SymbolTable) -> Instr:
    scope = _scope(symbols, state)
    args: list[Arg] = []
    changed = False
    for arg in instruction.args:
        timing_arg = _arg_for_q1_timing(arg, state, scope)
        args.append(timing_arg)
        changed = changed or timing_arg != arg
    if not changed:
        return instruction
    return Instr(
        pc=instruction.pc,
        label=instruction.label,
        op=instruction.op,
        args=args,
        source=instruction.source,
    )


def _arg_for_q1_timing(arg: Arg, state: AnalysisState, scope: SymbolTable) -> Arg:
    if arg.kind not in {"symbol", "placeholder"}:
        return arg
    register = _register_name(arg, state)
    if register is not None:
        return Arg(kind="reg", value=register, raw=arg.raw)
    value = resolve_arg_value(arg, scope)
    if isinstance(value, Concrete):
        return Arg(kind="imm", value=value.value, raw=arg.raw)
    return arg


def _resolved_branch_target_arg(arg: Arg, value: Value) -> Arg | None:
    if isinstance(value, Concrete):
        return Arg(kind="imm", value=value.value, raw=arg.raw)
    if isinstance(value, Symbolic) and value.expr.startswith("@") and len(value.expr) > 1:
        return Arg(kind="label", value=value.expr[1:], raw=arg.raw)
    return None


def _branch_target_arg(arg: Arg, state: AnalysisState, symbols: SymbolTable) -> Arg:
    if arg.kind in {"label", "imm"}:
        return arg
    if arg.kind == "symbol" and str(arg.value).startswith("$"):
        origin = state.symbol_origins.get("defs", {}).get(str(arg.value)[1:], {})
        raw = origin.get("raw", "")
        if raw.startswith("@"):
            return Arg(kind="label", value=raw[1:], raw=arg.raw)
    scope = _scope(symbols, state)
    if arg.kind == "reg":
        return _resolved_branch_target_arg(arg, resolve_arg_value(arg, scope)) or arg
    register = _register_name(arg, state)
    if register is not None:
        register_arg = Arg(kind="reg", value=register, raw=arg.raw)
        return _resolved_branch_target_arg(register_arg, resolve_arg_value(register_arg, scope)) or register_arg
    value = resolve_arg_value(arg, scope)
    return _resolved_branch_target_arg(arg, value) or arg


def _execute_concrete_branch(
    state: AnalysisState, instruction: Instr, q1_issue_event: TimelineEvent, symbols: SymbolTable,
    *, defer_register_write: bool = False, forced_decision: bool | None = None,
) -> None:
    if forced_decision is not None and (type(forced_decision) is not bool or instruction.op == "jmp"):
        raise ValueError("A forced branch decision must be a boolean for a conditional branch")
    scope = _scope(symbols, state)
    target_index = 1 if instruction.op == "loop" else 2 if len(instruction.args) == 3 else 0
    target_pc = resolve_arg_value(instruction.args[target_index], scope).value
    counter_write = None
    if instruction.op == "loop":
        register = _register_name(instruction.args[0], state)
        previous = state.registers[register]
        value = Concrete((previous.value - 1) & _CLASSICAL_REGISTER_MASK)
        decision = value.value != 0
        _set_alu_flags(state, instruction, _sub_flags(previous.value, 1))
        provenance = _register_provenance(
            state, register, expression=f"{register} - 1", value=value,
            steps=[_provenance_step(instruction, f"{register} - 1", value)],
        )
        counter_write = PendingRegisterWrite(
            register, value, _concrete_write_provenance(state, instruction, provenance, q1_issue_event.id),
            instruction.source, state.q1_issue_index + 1, q1_issue_event.id, {"op": "loop"},
        )
    elif len(instruction.args) == 3:
        left = resolve_arg_value(instruction.args[0], scope).value & _CLASSICAL_REGISTER_MASK
        right = resolve_arg_value(instruction.args[1], scope).value & _CLASSICAL_REGISTER_MASK
        decision = left >= right if instruction.op == "jge" else left < right
        _set_alu_flags(state, instruction, _sub_flags(left, right))
    else:
        decision = True if instruction.op == "jmp" else _status_branch_decision(instruction.op, state.alu_flags)
    if forced_decision is not None:
        q1_issue_event.meta.update(natural_branch_taken=decision, branch_overridden=True,
                                  assumption_source="user", assumed_branch_path="taken" if forced_decision else "fallthrough")
        decision = forced_decision
    duration = 24 if instruction.op in {"loop", "jlt", "jge"} else 16
    duration = duration if decision else 4
    q1_t1 = add_values(q1_issue_event.t0, Concrete(duration))
    state.events[-1] = replace(q1_issue_event, t1=q1_t1, duration=Concrete(duration),
                              confidence="assumed" if forced_decision is not None else q1_issue_event.confidence)
    state.q1_time_ns = q1_t1
    q1_issue_event.meta.update(branch_decision="assumed" if forced_decision is not None else "concrete",
                               branch_taken=decision, target_pc=target_pc)
    if counter_write is not None:
        if defer_register_write:
            state.pending_register_writes.append(counter_write)
        else:
            _apply_register_write(state, counter_write)
    if decision:
        state.next_pc = target_pc
    elif forced_decision is not None:
        state.next_pc = instruction.pc + 1


def _execute_branch(
    state: AnalysisState,
    instruction: Instr,
    q1_issue_event: TimelineEvent,
    symbols: SymbolTable,
    waveform_names: dict[int, str],
    acquisition_names: dict[int, str],
) -> None:
    if instruction.op == "loop" and len(instruction.args) >= 2:
        counter_reg = _register_name(instruction.args[0], state)
        target_arg = _branch_target_arg(instruction.args[1], state, symbols)
        target_label = str(target_arg.value) if target_arg.kind == "label" else None
        target_pc = _numeric_branch_target_pc(target_arg)
        if target_label is not None and target_label not in state.labels:
            _emit_undefined_label(state, instruction, target_label)
            _collapse_undefined_label_branch(
                state,
                instruction,
                q1_issue_event,
                f"loop {instruction.args[0].raw}",
                target_label,
            )
            return
        if target_label is not None:
            created = _emit_compact_loop(
                state,
                instruction,
                q1_issue_event=q1_issue_event,
                target_label=target_label,
                counter_reg=counter_reg,
                count=state.registers.get(counter_reg, Unknown(counter_reg or "loop_count")),
                forever=False,
                symbols=symbols,
            )
            if not created:
                _collapse_forward_loop_branch(
                    state,
                    instruction,
                    q1_issue_event,
                    f"loop {instruction.args[0].raw}",
                    target_label,
                    counter_reg,
                )
        elif target_pc is not None:
            _collapse_backward_numeric_branch(
                state,
                instruction,
                q1_issue_event,
                f"loop {instruction.args[0].raw}",
                target_pc,
            )
        elif target_arg.kind == "reg":
            _collapse_register_target_branch(
                state,
                instruction,
                q1_issue_event,
                f"loop {instruction.args[0].raw}",
                target_arg,
            )
    elif instruction.op == "jmp" and instruction.args:
        target_arg = _branch_target_arg(instruction.args[0], state, symbols)
        target_label = str(target_arg.value) if target_arg.kind == "label" else None
        target_pc = _numeric_branch_target_pc(target_arg)
        if target_label is not None:
            created = _emit_compact_loop(
                state,
                instruction,
                q1_issue_event=q1_issue_event,
                target_label=target_label,
                counter_reg=None,
                count=None,
                forever=True,
            )
            if created:
                state.stopped = True
            elif target_label in state.labels:
                q1_issue_event.meta["branch_taken"] = True
                q1_issue_event.meta["target_label"] = target_label
                state.next_pc = state.labels[target_label]
            else:
                _emit_undefined_label(state, instruction, target_label)
                _collapse_undefined_label_branch(state, instruction, q1_issue_event, "jmp", target_label)
        elif target_pc is not None:
            _execute_numeric_jmp(
                state,
                instruction,
                q1_issue_event,
                target_pc,
                allow_unvisited_backward_target=_register_name(instruction.args[0], state) is not None,
            )
        elif target_arg.kind == "reg":
            _collapse_register_target_branch(
                state,
                instruction,
                q1_issue_event,
                "jmp",
                target_arg,
            )
    elif instruction.op == "jge" and len(instruction.args) == 1:
        _execute_status_branch(state, instruction, q1_issue_event, symbols, waveform_names, acquisition_names)
    elif instruction.op in {"jge", "jlt"} and len(instruction.args) >= 3:
        _execute_conditional_branch(state, instruction, q1_issue_event, symbols, waveform_names, acquisition_names)
    elif instruction.op in STATUS_BRANCH_OPS and instruction.args:
        _execute_status_branch(state, instruction, q1_issue_event, symbols, waveform_names, acquisition_names)


def _revise_q1_issue_duration(state: AnalysisState, event: TimelineEvent, duration: int) -> TimelineEvent:
    q1_duration = Concrete(duration)
    q1_t1 = add_values(event.t0, q1_duration)
    updated = replace(event, t1=q1_t1, duration=q1_duration)
    for index, candidate in enumerate(state.events):
        if candidate.id == event.id:
            state.events[index] = updated
            break
    state.q1_time_ns = q1_t1
    return updated


def _status_branch_decision(op: str, flags: dict[str, bool] | None) -> bool | None:
    if flags is None:
        return None
    if len(flags) < 4:
        missing = [name for name in ("zf", "nf", "cf", "of") if name not in flags]
        decisions = {_status_branch_decision(op, {**flags, **dict(zip(missing, values))})
                     for values in product((False, True), repeat=len(missing))}
        return decisions.pop() if len(decisions) == 1 else None
    zf = flags["zf"]
    nf = flags["nf"]
    cf = flags["cf"]
    of = flags["of"]
    decisions = {
        "jz": zf,
        "jnz": not zf,
        "jo": of,
        "jno": not of,
        "js": nf,
        "jns": not nf,
        "jg": (not zf) and (nf == of),
        "jl": nf != of,
        "jle": zf or (nf != of),
        # Qblox docs describe jge, but the Core actions line appears to repeat jg.
        "jge": nf == of,
        "ja": (not cf) and (not zf),
        "jae": not cf,
        "jb": cf,
        "jbe": cf or zf,
    }
    return decisions.get(op)


def _emit_concrete_branch_region(
    state: AnalysisState,
    instruction: Instr,
    *,
    condition: str,
    target_label: str | None,
    target_pc: int | None,
    branch_id: str,
    branch_taken: bool,
) -> None:
    _emit_event(
        state,
        lane="rt.branch",
        kind="branch_region",
        t0=state.rt_time_ns,
        t1=state.rt_time_ns,
        duration=Concrete(0),
        label=f"concrete branch: {condition}",
        confidence="exact",
        source=instruction.source,
        meta={
            "condition": condition,
            "target_label": target_label,
            "target_pc": target_pc,
            "branch_id": branch_id,
            "branch_taken": branch_taken,
        },
    )


def _execute_status_branch(
    state: AnalysisState,
    instruction: Instr,
    q1_issue_event: TimelineEvent,
    symbols: SymbolTable,
    waveform_names: dict[int, str] | None = None,
    acquisition_names: dict[int, str] | None = None,
) -> None:
    target_arg = _branch_target_arg(instruction.args[0], state, symbols)
    target_label = str(target_arg.value) if target_arg.kind == "label" else None
    target_pc = _numeric_branch_target_pc(target_arg)
    condition = f"{instruction.op} status flags"
    branch_id = _branch_id(state, instruction, target_label, target_arg.raw)

    q1_issue_event.meta["condition"] = condition
    q1_issue_event.meta["branch_id"] = branch_id
    q1_issue_event.meta["branch_decision"] = "runtime_dependent"
    q1_issue_event.meta["branch_taken"] = "runtime_dependent"
    q1_issue_event.meta["branch_policy"] = state.branch_policy
    if target_label is not None:
        q1_issue_event.meta["target_label"] = target_label
    elif target_pc is not None:
        q1_issue_event.meta["target_pc"] = target_pc
    else:
        q1_issue_event.meta["target"] = target_arg.raw

    if target_label is not None and target_label not in state.labels:
        _emit_undefined_label(state, instruction, target_label)
        _collapse_undefined_label_branch(state, instruction, q1_issue_event, condition, target_label)
        return

    decision = _status_branch_decision(instruction.op, state.alu_flags)
    if decision is not None:
        q1_issue_event = _revise_q1_issue_duration(
            state,
            q1_issue_event,
            24 if decision and instruction.op == "jge" else 16 if decision else 4,
        )
        q1_issue_event.meta["branch_decision"] = "concrete"
        q1_issue_event.meta["branch_taken"] = decision
        _emit_concrete_branch_region(
            state,
            instruction,
            condition=condition,
            target_label=target_label,
            target_pc=target_pc,
            branch_id=branch_id,
            branch_taken=decision,
        )
        if not decision:
            return
        if target_label is not None:
            if _is_backward_branch_target(state, instruction, target_label):
                _collapse_backward_conditional_branch(state, instruction, q1_issue_event, condition, target_label)
                return
            state.next_pc = state.labels[target_label]
            return
        if target_pc is not None:
            if _is_backward_numeric_branch_target(instruction, target_pc):
                _collapse_backward_numeric_branch(state, instruction, q1_issue_event, condition, target_pc)
                return
            if target_pc in state.instructions_by_pc:
                state.next_pc = target_pc
                return
            _collapse_unknown_numeric_branch_target(state, instruction, q1_issue_event, condition, target_pc)
            return
        _collapse_register_target_branch(state, instruction, q1_issue_event, condition, target_arg)
        return

    branch_selection_meta: dict[str, Any] = {}
    branch_specific_path = state.branch_assumptions.get(branch_id)
    if _prove_timing_only_branch_join(state, instruction, q1_issue_event, symbols, target_label, target_pc, branch_id,
                                  taken_duration=24 if instruction.op == "jge" else 16,
                                  waveform_names=waveform_names, acquisition_names=acquisition_names):
        return
    if branch_specific_path in {"collapsed", "both"}:
        branch_selection_meta = {
            "assumption_source": "branch_assumption",
            "assumed_branch_path": branch_specific_path,
        }
        q1_issue_event.meta.update(branch_selection_meta)
        _record_branch_assumption(
            state,
            instruction=instruction,
            condition=condition,
            target_label=target_label,
            branch_id=branch_id,
            assumption_source="branch_assumption",
            assumed_taken=None,
            assumed_path=branch_specific_path,
        )
    if branch_specific_path == "both":
        state.diagnostics.append(
            Diagnostic(
                severity="info",
                category="analysis_incomplete",
                message="Status branch comparison is not implemented in the analyzer; branch remains collapsed.",
                source=instruction.source,
                details={"condition": condition, "target_label": target_label, "target_pc": target_pc, "branch_id": branch_id},
            )
        )

    assumption_source = "branch_assumption" if branch_specific_path in {"taken", "fallthrough"} else "branch_policy"
    assumption = _branch_specific_assumption(state, branch_id)
    if assumption is None and branch_specific_path not in {"collapsed", "both"}:
        assumption = _branch_assumption(state.branch_policy)
    if assumption is not None:
        assumed_taken, assumed_path = assumption
        q1_issue_event = _revise_q1_issue_duration(
            state, q1_issue_event, 24 if assumed_taken and instruction.op == "jge" else 16 if assumed_taken else 4,
        )
        q1_issue_event.meta["branch_taken"] = assumed_taken
        q1_issue_event.meta["branch_policy"] = state.branch_policy
        q1_issue_event.meta["assumed_branch_path"] = assumed_path
        q1_issue_event.meta["assumption_source"] = assumption_source
        _record_branch_assumption(
            state,
            instruction=instruction,
            condition=condition,
            target_label=target_label,
            branch_id=branch_id,
            assumption_source=assumption_source,
            assumed_taken=assumed_taken,
            assumed_path=assumed_path,
        )
        state.diagnostics.append(
            Diagnostic(
                severity="info",
                category="unresolved_branch",
                message=f"Status branch condition assumed {assumed_path}: {instruction.op}",
                source=instruction.source,
                details={
                    "condition": condition,
                    "target_label": target_label,
                    "target_pc": target_pc,
                    "branch_id": branch_id,
                    "branch_policy": state.branch_policy,
                    "assumption_source": assumption_source,
                    "branch_taken": assumed_taken,
                    "assumed_branch_taken": assumed_taken,
                    "assumed_branch_path": assumed_path,
                },
            )
        )
        _emit_event(
            state,
            lane="rt.branch",
            kind="branch_region",
            t0=state.rt_time_ns,
            t1=state.rt_time_ns,
            duration=Concrete(0),
            label=f"assumed status branch {assumed_path}: {instruction.op}",
            confidence="assumed",
            source=instruction.source,
            meta={
                "condition": condition,
                "target_label": target_label,
                "target_pc": target_pc,
                "branch_id": branch_id,
                "branch_policy": state.branch_policy,
                "assumption_source": assumption_source,
                "branch_taken": assumed_taken,
                "assumed_branch_taken": assumed_taken,
                "assumed_branch_path": assumed_path,
            },
        )
        if assumed_taken and target_label is not None:
            if _is_backward_branch_target(state, instruction, target_label):
                if _preview_taken_backward_conditional_branch(state, instruction, q1_issue_event, target_label):
                    return
                _collapse_backward_conditional_branch(state, instruction, q1_issue_event, condition, target_label)
                return
            state.next_pc = state.labels[target_label]
            return
        if assumed_taken and target_pc is not None:
            if _is_backward_numeric_branch_target(instruction, target_pc):
                _collapse_backward_numeric_branch(state, instruction, q1_issue_event, condition, target_pc)
                return
            if target_pc in state.instructions_by_pc:
                state.next_pc = target_pc
                return
            _collapse_unknown_numeric_branch_target(state, instruction, q1_issue_event, condition, target_pc)
            return
        if assumed_taken:
            _collapse_register_target_branch(state, instruction, q1_issue_event, condition, target_arg)
        return

    state.diagnostics.append(
        Diagnostic(
            severity="warning",
            category="analysis_incomplete",
            message=f"Status branch condition is not modeled: {instruction.op}.",
            source=instruction.source,
            details={
                "condition": condition,
                "target_label": target_label,
                "target_pc": target_pc,
                "branch_id": branch_id,
                "branch_policy": state.branch_policy,
                **branch_selection_meta,
            },
        )
    )
    _emit_event(
        state,
        lane="rt.branch",
        kind="branch_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(condition),
        label=f"unresolved status branch: {condition}",
        confidence="runtime_dependent",
        source=instruction.source,
        meta={
            "condition": condition,
            "target_label": target_label,
            "target_pc": target_pc,
            "branch_id": branch_id,
            "branch_policy": state.branch_policy,
            **branch_selection_meta,
        },
    )
    _emit_event(
        state,
        lane="rt.unknown",
        kind="unknown_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(condition),
        label="analysis incomplete after status branch",
        confidence="runtime_dependent",
        source=instruction.source,
        meta={
            "reason": "status_branch",
            "condition": condition,
            "target_label": target_label,
            "target_pc": target_pc,
            "branch_id": branch_id,
            "branch_policy": state.branch_policy,
            **branch_selection_meta,
        },
    )
    state.stopped = True


def _execute_numeric_jmp(
    state: AnalysisState,
    instruction: Instr,
    q1_issue_event: TimelineEvent,
    target_pc: int,
    *,
    allow_unvisited_backward_target: bool = False,
) -> None:
    q1_issue_event.meta["branch_taken"] = True
    q1_issue_event.meta["target_pc"] = target_pc
    if _is_backward_numeric_branch_target(instruction, target_pc):
        if allow_unvisited_backward_target and target_pc in state.instructions_by_pc and target_pc not in state.executed_pcs:
            q1_issue_event.meta["register_return"] = True
            state.next_pc = target_pc
            return
        _collapse_backward_numeric_branch(state, instruction, q1_issue_event, "jmp", target_pc)
        return
    if target_pc in state.instructions_by_pc:
        state.next_pc = target_pc
        return
    _collapse_unknown_numeric_branch_target(state, instruction, q1_issue_event, "jmp", target_pc)


def _execute_conditional_branch(
    state: AnalysisState,
    instruction: Instr,
    q1_issue_event: TimelineEvent,
    symbols: SymbolTable,
    waveform_names: dict[int, str],
    acquisition_names: dict[int, str],
) -> None:
    target_arg = _branch_target_arg(instruction.args[2], state, symbols)
    target_label = str(target_arg.value) if target_arg.kind == "label" else None
    target_pc = _numeric_branch_target_pc(target_arg)
    left = resolve_arg_value(instruction.args[0], _scope(symbols, state))
    right = resolve_arg_value(instruction.args[1], _scope(symbols, state))
    if isinstance(left, Concrete):
        left = Concrete(left.value & _CLASSICAL_REGISTER_MASK)
    if isinstance(right, Concrete):
        right = Concrete(right.value & _CLASSICAL_REGISTER_MASK)
    flags = _comparison_flags(instruction.args[0], left, instruction.args[1], right, state)
    _set_alu_flags(state, instruction, flags)
    op = ">=" if instruction.op == "jge" else "<"
    decision = compare_values(left, op, right)
    bounds = _unsigned_operand_bounds(instruction.args[0], left, state)
    if not isinstance(decision, bool) and bounds is not None and isinstance(right, Concrete):
        if bounds[0] >= right.value:
            decision = instruction.op == "jge"
        elif bounds[1] < right.value:
            decision = instruction.op == "jlt"
    condition = f"{instruction.args[0].raw} {op} {instruction.args[1].raw}"
    branch_id = _branch_id(state, instruction, target_label, target_arg.raw)

    q1_issue_event.meta["condition"] = condition
    q1_issue_event.meta["target_label"] = target_label
    q1_issue_event.meta["branch_id"] = branch_id
    if target_pc is not None:
        q1_issue_event.meta["target_pc"] = target_pc
    if target_label is None:
        q1_issue_event.meta["target"] = target_arg.raw
    if target_label is not None and target_label not in state.labels:
        _emit_undefined_label(state, instruction, target_label)

    if isinstance(decision, bool):
        q1_issue_event = _revise_q1_issue_duration(state, q1_issue_event, 24 if decision else 4)
        q1_issue_event.meta["branch_decision"] = "concrete"
        q1_issue_event.meta["branch_taken"] = decision
        _emit_concrete_branch_region(
            state,
            instruction,
            condition=condition,
            target_label=target_label,
            target_pc=target_pc,
            branch_id=branch_id,
            branch_taken=decision,
        )
        if decision and target_pc is not None:
            if _is_backward_numeric_branch_target(instruction, target_pc):
                _collapse_backward_numeric_branch(state, instruction, q1_issue_event, condition, target_pc)
                return
            if target_pc in state.instructions_by_pc:
                state.next_pc = target_pc
                return
            _collapse_unknown_numeric_branch_target(state, instruction, q1_issue_event, condition, target_pc)
            return
        if decision and target_label is None:
            _collapse_register_target_branch(state, instruction, q1_issue_event, condition, target_arg)
            return
        if decision and target_label not in state.labels:
            _collapse_undefined_label_branch(state, instruction, q1_issue_event, condition, str(target_label))
            return
        if decision and target_label in state.labels:
            if _is_backward_branch_target(state, instruction, target_label):
                _collapse_backward_conditional_branch(state, instruction, q1_issue_event, condition, target_label)
                return
            state.next_pc = state.labels[target_label]
        return

    q1_issue_event.meta["branch_decision"] = "runtime_dependent"
    if isinstance(decision, RuntimeDependent):
        q1_issue_event.meta["runtime_dependency"] = decision.source

    branch_specific_path = state.branch_assumptions.get(branch_id)
    if _prove_timing_only_branch_join(state, instruction, q1_issue_event, symbols, target_label, target_pc, branch_id,
                                  taken_duration=24, waveform_names=waveform_names, acquisition_names=acquisition_names):
        return
    if _should_explore_both_paths(state, branch_specific_path):
        explored = _explore_conditional_branch_both_paths(
            state,
            instruction,
            q1_issue_event,
            symbols,
            waveform_names,
            acquisition_names,
            condition=condition,
            target_label=target_label,
            target_pc=target_pc,
            branch_id=branch_id,
            assumption_source="branch_assumption" if branch_specific_path == "both" else "branch_policy",
        )
        if explored:
            return

    branch_selection_meta: dict[str, Any] = {}
    if branch_specific_path in {"collapsed", "both"}:
        branch_selection_meta = {
            "assumption_source": "branch_assumption",
            "assumed_branch_path": branch_specific_path,
        }
        q1_issue_event.meta.update(branch_selection_meta)
        _record_branch_assumption(
            state,
            instruction=instruction,
            condition=condition,
            target_label=target_label,
            branch_id=branch_id,
            assumption_source="branch_assumption",
            assumed_taken=None,
            assumed_path=branch_specific_path,
        )
    if branch_specific_path == "both":
        state.diagnostics.append(
            Diagnostic(
                severity="info",
                category="analysis_incomplete",
                message="Branch comparison is not implemented in the analyzer; branch remains collapsed.",
                source=instruction.source,
                details={"condition": condition, "target_label": target_label, "branch_id": branch_id},
            )
        )

    assumption_source = "branch_assumption" if branch_specific_path in {"taken", "fallthrough"} else "branch_policy"
    assumption = _branch_specific_assumption(state, branch_id)
    if assumption is None and branch_specific_path not in {"collapsed", "both"}:
        assumption = _branch_assumption(state.branch_policy)
    if assumption is not None:
        assumed_taken, assumed_path = assumption
        q1_issue_event = _revise_q1_issue_duration(state, q1_issue_event, 24 if assumed_taken else 4)
        q1_issue_event.meta["branch_taken"] = assumed_taken
        q1_issue_event.meta["branch_policy"] = state.branch_policy
        q1_issue_event.meta["assumed_branch_path"] = assumed_path
        q1_issue_event.meta["assumption_source"] = assumption_source
        if assumed_taken and target_label is None:
            if target_pc is not None:
                if _is_backward_numeric_branch_target(instruction, target_pc):
                    _collapse_backward_numeric_branch(state, instruction, q1_issue_event, condition, target_pc)
                    return
                if target_pc in state.instructions_by_pc:
                    state.next_pc = target_pc
                    return
                _collapse_unknown_numeric_branch_target(state, instruction, q1_issue_event, condition, target_pc)
                return
            _collapse_register_target_branch(state, instruction, q1_issue_event, condition, target_arg)
            return
        if assumed_taken and target_label not in state.labels:
            _collapse_undefined_label_branch(state, instruction, q1_issue_event, condition, str(target_label))
            return
        _record_branch_assumption(
            state,
            instruction=instruction,
            condition=condition,
            target_label=target_label,
            branch_id=branch_id,
            assumption_source=assumption_source,
            assumed_taken=assumed_taken,
            assumed_path=assumed_path,
        )
        state.diagnostics.append(
            Diagnostic(
                severity="info",
                category="unresolved_branch",
                message=f"Unresolved branch condition assumed {assumed_path}: {condition}",
                source=instruction.source,
                details={
                    "condition": condition,
                    "target_label": target_label,
                    "branch_id": branch_id,
                    "branch_policy": state.branch_policy,
                    "assumption_source": assumption_source,
                    "branch_taken": assumed_taken,
                    "assumed_branch_taken": assumed_taken,
                    "assumed_branch_path": assumed_path,
                },
            )
        )
        if assumed_taken and target_label in state.labels and _is_backward_branch_target(state, instruction, target_label):
            if _preview_taken_backward_conditional_branch(state, instruction, q1_issue_event, target_label):
                return
            _collapse_backward_conditional_branch(state, instruction, q1_issue_event, condition, target_label)
            return
        _emit_event(
            state,
            lane="rt.branch",
            kind="branch_region",
            t0=state.rt_time_ns,
            t1=state.rt_time_ns,
            duration=Concrete(0),
            label=f"assumed branch {assumed_path}: {condition}",
            confidence="assumed",
            source=instruction.source,
            meta={
                "condition": condition,
                "target_label": target_label,
                "branch_id": branch_id,
                "branch_policy": state.branch_policy,
                "assumption_source": assumption_source,
                "branch_taken": assumed_taken,
                "assumed_branch_taken": assumed_taken,
                "assumed_branch_path": assumed_path,
            },
        )
        if assumed_taken and target_label in state.labels:
            state.next_pc = state.labels[target_label]
        return

    if state.branch_policy == "explore_both_with_depth_limit":
        q1_issue_event.meta["branch_policy"] = state.branch_policy
        state.diagnostics.append(
            Diagnostic(
                severity="warning",
                category="analysis_incomplete",
                message="Branch policy explore_both_with_depth_limit is not implemented; unresolved branch collapsed.",
                source=instruction.source,
                details={
                    "condition": condition,
                    "target_label": target_label,
                    "branch_id": branch_id,
                    "unsupported_branch_policy": state.branch_policy,
                },
            )
        )

    q1_issue_event.meta["branch_taken"] = "runtime_dependent"
    q1_issue_event.meta.setdefault("branch_policy", state.branch_policy)
    state.diagnostics.append(
        Diagnostic(
            severity="info",
            category="unresolved_branch",
            message=f"Unresolved branch condition: {condition}",
            source=instruction.source,
            details={
                "condition": condition,
                "target_label": target_label,
                "branch_policy": state.branch_policy,
                **({"branch_id": branch_id} if branch_selection_meta else {}),
                **branch_selection_meta,
            },
        )
    )
    _emit_event(
        state,
        lane="rt.branch",
        kind="branch_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(condition),
        label=f"unresolved branch: {condition}",
        confidence="runtime_dependent",
        source=instruction.source,
        meta={
            "condition": condition,
            "target_label": target_label,
            "branch_id": branch_id,
            "branch_policy": state.branch_policy,
            **branch_selection_meta,
        },
    )
    _emit_event(
        state,
        lane="rt.unknown",
        kind="unknown_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(condition),
        label="analysis incomplete after unresolved branch",
        confidence="runtime_dependent",
        source=instruction.source,
        meta={
            "reason": "unresolved_branch",
            "condition": condition,
            "branch_id": branch_id,
            "branch_policy": state.branch_policy,
            **branch_selection_meta,
        },
    )
    state.stopped = True


def _is_backward_branch_target(state: AnalysisState, instruction: Instr, target_label: str) -> bool:
    return state.labels[target_label] <= instruction.pc


def _numeric_branch_target_pc(arg: Arg) -> int | None:
    if arg.kind != "imm":
        return None
    return int(arg.value)


def _is_backward_numeric_branch_target(instruction: Instr, target_pc: int) -> bool:
    return target_pc <= instruction.pc


def _preview_taken_backward_conditional_branch(
    state: AnalysisState,
    instruction: Instr,
    q1_issue_event: TimelineEvent,
    target_label: str,
) -> bool:
    if q1_issue_event.meta.get("assumption_source") != "branch_assumption":
        return False
    if q1_issue_event.meta.get("assumed_branch_path") != "taken":
        return False
    created = _emit_compact_loop(
        state,
        instruction,
        q1_issue_event=q1_issue_event,
        target_label=target_label,
        counter_reg=None,
        count=None,
        forever=True,
        min_visible_iterations=2,
    )
    if created:
        state.stopped = True
    return created


def _collapse_backward_conditional_branch(
    state: AnalysisState,
    instruction: Instr,
    q1_issue_event: TimelineEvent,
    condition: str,
    target_label: str,
) -> None:
    reason = "unsupported_backward_conditional_branch"
    q1_issue_event.meta["target_label"] = target_label
    q1_issue_event.meta["reason"] = reason
    state.diagnostics.append(
        Diagnostic(
            severity="warning",
            category="analysis_incomplete",
            message=f"Backward conditional branch target is not modeled: @{target_label}",
            source=instruction.source,
            details={
                "condition": condition,
                "target_label": target_label,
                "reason": reason,
                "branch_policy": state.branch_policy,
            },
        )
    )
    _emit_event(
        state,
        lane="rt.branch",
        kind="branch_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(f"{condition} -> @{target_label}"),
        label=f"unsupported backward conditional branch: @{target_label}",
        confidence="runtime_dependent",
        source=instruction.source,
        meta={
            "condition": condition,
            "target_label": target_label,
            "reason": reason,
            "branch_policy": state.branch_policy,
        },
    )
    _emit_event(
        state,
        lane="rt.unknown",
        kind="unknown_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(f"{condition} -> @{target_label}"),
        label="analysis incomplete after unsupported backward conditional branch",
        confidence="runtime_dependent",
        source=instruction.source,
        meta={
            "reason": reason,
            "condition": condition,
            "target_label": target_label,
            "branch_policy": state.branch_policy,
        },
    )
    state.stopped = True


def _collapse_backward_numeric_branch(
    state: AnalysisState,
    instruction: Instr,
    q1_issue_event: TimelineEvent,
    condition: str,
    target_pc: int,
) -> None:
    reason = "unsupported_backward_numeric_branch"
    q1_issue_event.meta["branch_taken"] = "runtime_dependent"
    q1_issue_event.meta["target_pc"] = target_pc
    q1_issue_event.meta["reason"] = reason
    state.diagnostics.append(
        Diagnostic(
            severity="warning",
            category="analysis_incomplete",
            message=f"Backward numeric branch target is not modeled: {target_pc}",
            source=instruction.source,
            details={
                "condition": condition,
                "target_pc": target_pc,
                "reason": reason,
                "branch_policy": state.branch_policy,
            },
        )
    )
    _emit_event(
        state,
        lane="rt.branch",
        kind="branch_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(f"{condition} -> {target_pc}"),
        label=f"unsupported backward numeric branch: {target_pc}",
        confidence="runtime_dependent",
        source=instruction.source,
        meta={
            "condition": condition,
            "target_pc": target_pc,
            "reason": reason,
            "branch_policy": state.branch_policy,
        },
    )
    _emit_event(
        state,
        lane="rt.unknown",
        kind="unknown_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(f"{condition} -> {target_pc}"),
        label="analysis incomplete after unsupported backward numeric branch",
        confidence="runtime_dependent",
        source=instruction.source,
        meta={
            "reason": reason,
            "condition": condition,
            "target_pc": target_pc,
            "branch_policy": state.branch_policy,
        },
    )
    state.stopped = True


def _collapse_unknown_numeric_branch_target(
    state: AnalysisState,
    instruction: Instr,
    q1_issue_event: TimelineEvent,
    condition: str,
    target_pc: int,
) -> None:
    reason = "unknown_numeric_branch_target"
    q1_issue_event.meta.setdefault("branch_taken", "runtime_dependent")
    q1_issue_event.meta["target_pc"] = target_pc
    q1_issue_event.meta["reason"] = reason
    state.diagnostics.append(
        Diagnostic(
            severity="warning",
            category="analysis_incomplete",
            message=f"Numeric branch target is outside the analyzed program: {target_pc}",
            source=instruction.source,
            details={
                "condition": condition,
                "target_pc": target_pc,
                "reason": reason,
                "branch_policy": state.branch_policy,
            },
        )
    )
    _emit_event(
        state,
        lane="rt.branch",
        kind="branch_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(f"{condition} -> {target_pc}"),
        label=f"unknown numeric branch target: {target_pc}",
        confidence="unknown",
        source=instruction.source,
        meta={
            "condition": condition,
            "target_pc": target_pc,
            "reason": reason,
            "branch_policy": state.branch_policy,
        },
    )
    _emit_event(
        state,
        lane="rt.unknown",
        kind="unknown_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(f"{condition} -> {target_pc}"),
        label="analysis incomplete after unknown numeric branch target",
        confidence="unknown",
        source=instruction.source,
        meta={
            "reason": reason,
            "condition": condition,
            "target_pc": target_pc,
            "branch_policy": state.branch_policy,
        },
    )
    state.stopped = True


def _collapse_undefined_label_branch(
    state: AnalysisState,
    instruction: Instr,
    q1_issue_event: TimelineEvent,
    condition: str,
    target_label: str,
) -> None:
    q1_issue_event.meta.setdefault("branch_taken", "runtime_dependent")
    q1_issue_event.meta["target_label"] = target_label
    q1_issue_event.meta["reason"] = "undefined_label"
    _emit_event(
        state,
        lane="rt.branch",
        kind="branch_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(f"{condition} -> @{target_label}"),
        label=f"undefined branch label: @{target_label}",
        confidence="unknown",
        source=instruction.source,
        meta={
            "condition": condition,
            "target_label": target_label,
            "reason": "undefined_label",
            "branch_policy": state.branch_policy,
        },
    )
    _emit_event(
        state,
        lane="rt.unknown",
        kind="unknown_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(f"{condition} -> @{target_label}"),
        label="analysis incomplete after undefined branch label",
        confidence="unknown",
        source=instruction.source,
        meta={
            "reason": "undefined_label",
            "condition": condition,
            "target_label": target_label,
            "branch_policy": state.branch_policy,
        },
    )
    state.stopped = True


def _collapse_forward_loop_branch(
    state: AnalysisState,
    instruction: Instr,
    q1_issue_event: TimelineEvent,
    condition: str,
    target_label: str,
    counter_reg: str | None,
) -> None:
    q1_issue_event.meta["branch_taken"] = "runtime_dependent"
    q1_issue_event.meta["target_label"] = target_label
    q1_issue_event.meta["reason"] = "unsupported_forward_loop"
    state.diagnostics.append(
        Diagnostic(
            severity="warning",
            category="analysis_incomplete",
            message=f"Forward loop target is not modeled: @{target_label}",
            source=instruction.source,
            details={
                "condition": condition,
                "target_label": target_label,
                "counter_reg": counter_reg,
                "reason": "unsupported_forward_loop",
                "branch_policy": state.branch_policy,
            },
        )
    )
    _emit_event(
        state,
        lane="rt.branch",
        kind="branch_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(f"{condition} -> @{target_label}"),
        label=f"unsupported forward loop: @{target_label}",
        confidence="runtime_dependent",
        source=instruction.source,
        meta={
            "condition": condition,
            "target_label": target_label,
            "counter_reg": counter_reg,
            "reason": "unsupported_forward_loop",
            "branch_policy": state.branch_policy,
        },
    )
    _emit_event(
        state,
        lane="rt.unknown",
        kind="unknown_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(f"{condition} -> @{target_label}"),
        label="analysis incomplete after unsupported forward loop",
        confidence="runtime_dependent",
        source=instruction.source,
        meta={
            "reason": "unsupported_forward_loop",
            "condition": condition,
            "target_label": target_label,
            "counter_reg": counter_reg,
            "branch_policy": state.branch_policy,
        },
    )
    state.stopped = True


def _collapse_register_target_branch(
    state: AnalysisState,
    instruction: Instr,
    q1_issue_event: TimelineEvent,
    condition: str,
    target_arg: Arg,
) -> None:
    target = target_arg.raw
    q1_issue_event.meta["branch_taken"] = "runtime_dependent"
    q1_issue_event.meta["target"] = target
    q1_issue_event.meta["reason"] = "register_branch_target"
    state.diagnostics.append(
        Diagnostic(
            severity="info",
            category="unresolved_branch",
            message=f"Unresolved branch target: {target}",
            source=instruction.source,
            details={
                "condition": condition,
                "target": target,
                "reason": "register_branch_target",
                "branch_policy": state.branch_policy,
            },
        )
    )
    _emit_event(
        state,
        lane="rt.branch",
        kind="branch_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(f"{condition} -> {target}"),
        label=f"unresolved branch target: {target}",
        confidence="runtime_dependent",
        source=instruction.source,
        meta={
            "condition": condition,
            "target": target,
            "reason": "register_branch_target",
            "branch_policy": state.branch_policy,
        },
    )
    _emit_event(
        state,
        lane="rt.unknown",
        kind="unknown_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(f"{condition} -> {target}"),
        label="analysis incomplete after register branch target",
        confidence="runtime_dependent",
        source=instruction.source,
        meta={
            "reason": "register_branch_target",
            "condition": condition,
            "target": target,
            "branch_policy": state.branch_policy,
        },
    )
    state.stopped = True


def _branch_assumption(branch_policy: str) -> tuple[bool, str] | None:
    if branch_policy == "collapse_unresolved":
        return True, "taken"
    if branch_policy == "assume_true":
        return True, "taken"
    if branch_policy == "assume_false":
        return False, "not_taken"
    if branch_policy == "assume_fallthrough":
        return False, "fallthrough"
    return None


def _should_explore_both_paths(state: AnalysisState, branch_specific_path: str | None) -> bool:
    if state.branch_explore_depth >= 1:
        return False
    return branch_specific_path == "both" or (
        branch_specific_path is None and state.branch_policy == "explore_both_with_depth_limit"
    )


def _explore_conditional_branch_both_paths(
    state: AnalysisState,
    instruction: Instr,
    q1_issue_event: TimelineEvent,
    symbols: SymbolTable,
    waveform_names: dict[int, str],
    acquisition_names: dict[int, str],
    *,
    condition: str,
    target_label: str | None,
    target_pc: int | None,
    branch_id: str,
    assumption_source: str,
) -> bool:
    taken_pc = _resolved_forward_branch_target_pc(state, instruction, target_label, target_pc)
    if taken_pc is None:
        return False

    q1_issue_event.meta.update(
        {
            "branch_taken": "both",
            "branch_policy": state.branch_policy,
            "assumption_source": assumption_source,
            "assumed_branch_path": "both",
            "comparison_paths": ["taken", "fallthrough"],
        }
    )
    _record_branch_assumption(
        state,
        instruction=instruction,
        condition=condition,
        target_label=target_label,
        branch_id=branch_id,
        assumption_source=assumption_source,
        assumed_taken=None,
        assumed_path="both",
    )
    _emit_event(
        state,
        lane="rt.branch",
        kind="branch_region",
        t0=state.rt_time_ns,
        t1=state.rt_time_ns,
        duration=Concrete(0),
        label=f"compare both branch paths: {condition}",
        confidence="runtime_dependent",
        source=instruction.source,
        meta={
            "condition": condition,
            "target_label": target_label,
            "branch_id": branch_id,
            "branch_policy": state.branch_policy,
            "assumption_source": assumption_source,
            "branch_taken": "both",
            "assumed_branch_path": "both",
            "comparison_paths": ["taken", "fallthrough"],
        },
    )

    snapshot_event_count = len(state.events)
    snapshot_diagnostic_count = len(state.diagnostics)
    snapshot_packet_count = len(state.rt_packets)
    snapshot_register_count = len(state.register_history)
    base_path_state = deepcopy(state)
    comparison_packets: list[RtPacket] = []
    comparison_events: list[TimelineEvent] = []
    comparison_diagnostics: list[Diagnostic] = []
    path_specs = (("taken", taken_pc), ("fallthrough", instruction.pc + 1))
    for path, next_pc in path_specs:
        path_state = deepcopy(base_path_state)
        path_issue = next(event for event in path_state.events if event.id == q1_issue_event.id)
        _revise_q1_issue_duration(path_state, path_issue, 24 if path == "taken" else 4)
        path_state.pc = next_pc
        path_state.next_pc = next_pc
        path_state.stopped = False
        path_state.q1_issue_index = state.q1_issue_index + 1
        path_state.branch_explore_depth = state.branch_explore_depth + 1
        _run_program_loop(path_state, symbols, waveform_names, acquisition_names)
        _flush_pending_register_writes(path_state)
        path_event_ids = {event.id for event in path_state.events[snapshot_event_count:]}
        state.register_history.extend(
            replace(
                write,
                event_id=f"{write.event_id}:branch-{path}" if write.event_id in path_event_ids else write.event_id,
                confidence="assumed" if write.confidence == "exact" else write.confidence,
                meta={
                    **write.meta,
                    "branch_comparison_branch_id": branch_id,
                    "branch_comparison_path": path,
                },
            )
            for write in path_state.register_history[snapshot_register_count:]
        )
        comparison_packets.extend(
            _branch_comparison_packets(
                path_state.rt_packets[snapshot_packet_count:],
                branch_id=branch_id,
                path=path,
            )
        )
        comparison_events.extend(
            _branch_comparison_events(
                path_state.events[snapshot_event_count:],
                branch_id=branch_id,
                path=path,
            )
        )
        comparison_diagnostics.extend(
            _branch_comparison_diagnostics(
                path_state.diagnostics[snapshot_diagnostic_count:],
                branch_id=branch_id,
                path=path,
                path_event_ids=path_event_ids,
            )
        )
    state.rt_packets.extend(comparison_packets)
    state.events.extend(comparison_events)
    state.diagnostics.extend(comparison_diagnostics)
    state.stopped = True
    return True


def _refine_legacy_branch_arm(state: AnalysisState, instruction: Instr, symbols: SymbolTable, *, taken: bool) -> set[str]:
    if instruction.op not in {"jge", "jlt"} or len(instruction.args) != 3:
        return set()
    register = _physical_read_register(instruction.args[0], state)
    if register is None or _pending_register_write(state, register) is not None or any(
        item.source == instruction.source and item.category == "register_not_ready" for item in state.diagnostics
    ):
        return set()
    scope = _scope(symbols, state)
    left, right = [resolve_arg_value(arg, scope) for arg in instruction.args[:2]]
    bounds = _unsigned_operand_bounds(instruction.args[0], left, state)
    if bounds is None or not isinstance(right, Concrete):
        return set()
    threshold = right.value & _CLASSICAL_REGISTER_MASK
    greater_equal = (instruction.op == "jge") == taken
    bounds = (max(bounds[0], threshold), bounds[1]) if greater_equal else (bounds[0], min(bounds[1], threshold - 1))
    if bounds[0] > bounds[1]:
        return set()
    ones, zeros = _operand_known_bits(instruction.args[0], left, state)
    prefix_ones, prefix_zeros = _bounds_known_bits(bounds)
    bits = ones | prefix_ones, zeros | prefix_zeros
    if bits[0] & bits[1]:
        return set()
    bounds = _refine_bounds_with_bits(bounds, bits)
    source_origin = _operand_value_origin(instruction.args[0], left, state)
    refined = set()
    for index in range(_REGISTER_MAX_INDEX + 1):
        peer = f"R{index}"
        if _pending_register_write(state, peer) is not None:
            continue
        arg = Arg("reg", peer, peer)
        value = scope.get(peer, Unknown(peer))
        origin = _operand_value_origin(arg, value, state)
        if origin is None or source_origin is None or origin[0] != source_origin[0]:
            continue
        opposite = origin[1] != source_origin[1]
        peer_bounds = (_CLASSICAL_REGISTER_MASK - bounds[1], _CLASSICAL_REGISTER_MASK - bounds[0]) if opposite else bounds
        current = _unsigned_operand_bounds(arg, value, state)
        peer_bounds = max(current[0], peer_bounds[0]), min(current[1], peer_bounds[1])
        if peer_bounds[0] > peer_bounds[1]:
            continue
        old_ones, old_zeros = _operand_known_bits(arg, value, state)
        new_ones, new_zeros = (bits[1], bits[0]) if opposite else bits
        prefix_ones, prefix_zeros = _bounds_known_bits(peer_bounds)
        peer_bits = old_ones | new_ones | prefix_ones, old_zeros | new_zeros | prefix_zeros
        if peer_bits[0] & peer_bits[1]:
            continue
        peer_bounds = _refine_bounds_with_bits(peer_bounds, peer_bits)
        state.register_bounds[peer] = peer_bounds
        state.register_known_bits[peer] = peer_bits
        state.registers[peer] = Concrete(peer_bounds[0]) if peer_bounds[0] == peer_bounds[1] else value
        refined.add(peer)
    _set_alu_flags(state, instruction, _comparison_flags(instruction.args[0], state.registers[register], instruction.args[1], right, state))
    return refined


def _prove_timing_only_branch_join(
    state: AnalysisState, instruction: Instr, q1_issue_event: TimelineEvent, symbols: SymbolTable,
    target_label: str | None, target_pc: int | None, branch_id: str, *, taken_duration: int,
    waveform_names: dict[int, str] | None = None, acquisition_names: dict[int, str] | None = None,
) -> bool:
    target_index = 2 if len(instruction.args) == 3 else 0
    if (state.queue_alternative_probe or state.branch_policy != "collapse_unresolved" or branch_id in state.branch_assumptions
            or state.pending_register_writes
            or _physical_read_register(instruction.args[target_index], state) is not None
            or state.has_expanded_instructions and target_label is None):
        return False
    taken_pc = _resolved_forward_branch_target_pc(state, instruction, target_label, target_pc)
    if taken_pc is None:
        return False

    def arm(start: int) -> list[int]:
        path = []
        pc = start
        while pc in state.instructions_by_pc:
            path.append(pc)
            item = state.instructions_by_pc[pc]
            spec = get_instruction_spec(item.op)
            if item.op == "jmp" and len(item.args) == 1 and _physical_read_register(item.args[0], state) is None:
                target = _branch_target_arg(item.args[0], state, symbols)
                next_pc = state.labels.get(str(target.value)) if target.kind == "label" else _numeric_branch_target_pc(target)
                if next_pc is None or next_pc <= pc or state.has_expanded_instructions and target.kind != "label":
                    break
            elif (item.op == "nop" or spec.category == "classical"
                  and (_register_destination_argument_indexes(item.op) or item.op in {"cmp", "test"})
                  or spec.category == "latched" and item.op in {
                      "set_mrk", "set_awg_gain", "set_awg_offs", "set_freq", "reset_ph", "set_ph",
                      "set_ph_delta", "set_digital", "set_scope_en", "set_cond",
                  }
                  or spec.category == "rt" and spec.timing_effect_status == "known"
                  and (item.op in {"wait", "play", "upd_param", "upd_thres"} or item.op.startswith("acquire"))):
                next_pc = pc + 1
            else:
                break
            pc = next_pc
        return path

    paths = {"taken": arm(taken_pc), "fallthrough": arm(instruction.pc + 1)}
    taken_nodes = set(paths["taken"])
    join_pc = next((pc for pc in paths["fallthrough"] if pc in taken_nodes), None)
    if join_pc is None:
        return False
    paths = {path: pcs[:pcs.index(join_pc)] for path, pcs in paths.items()}
    prefix_sources = {state.instructions_by_pc[pc].source for pcs in paths.values() for pc in pcs}
    prefix_instructions = {item.source: item for item in state.instructions_by_pc.values() if item.source in prefix_sources}
    base = deepcopy(state)
    region = _emit_event(base, lane="rt.branch", kind="branch_region", t0=state.rt_time_ns, t1=state.rt_time_ns,
                         duration=Concrete(0), label="common state after unresolved branch", confidence="runtime_dependent",
                         source=instruction.source, meta={})
    event_start, packet_start, history_start, diagnostic_start = (
        len(base.events), len(base.rt_packets), len(base.register_history), len(base.diagnostics)
    )
    probes = {}
    refined_registers = set()
    for path, start in (("taken", taken_pc), ("fallthrough", instruction.pc + 1)):
        probe = deepcopy(base)
        issue = next(event for event in probe.events if event.id == q1_issue_event.id)
        _revise_q1_issue_duration(probe, issue, taken_duration if path == "taken" else 4)
        refined_registers.update(_refine_legacy_branch_arm(probe, instruction, symbols, taken=path == "taken"))
        probe.pc = start
        probe.next_pc = start
        probe.q1_issue_index += 1
        _run_program_loop(probe, symbols, waveform_names or {}, acquisition_names or {}, stop_pc=join_pc)
        _apply_ready_register_writes(probe)
        if probe.pc != join_pc or probe.stopped or probe.pending_register_writes or any(
            item.severity == "error" or item.category == "register_not_ready"
            for item in probe.diagnostics if item.source in prefix_sources
        ):
            return False
        if any(not _issue_domains_supported(probe, prefix_instructions[issue.source], issue, symbols)
               for issue in probe.events if issue.kind == "q1_issue" and issue.source in prefix_sources):
            return False
        packets = probe.rt_packets[packet_start:]
        if not all(isinstance(packet.duration, Concrete) and packet.duration.value >= 0 for packet in packets):
            return False
        probes[path] = probe
    a, b = probes["taken"], probes["fallthrough"]
    elapsed = {path: sum(packet.duration.value for packet in probe.rt_packets[packet_start:])
               for path, probe in probes.items()}
    if elapsed["taken"] != elapsed["fallthrough"]:
        return False

    def same_concrete(left: Any, right: Any) -> bool:
        if isinstance(left, Concrete) and isinstance(right, Concrete):
            return left.value == right.value
        if isinstance(left, tuple) and isinstance(right, tuple):
            return len(left) == len(right) and all(same_concrete(x, y) for x, y in zip(left, right))
        return left is None and right is None

    deltas = {path: probe.events[event_start:] for path, probe in probes.items()}
    written_fields = {event.meta["field"] for events in deltas.values() for event in events
                      if event.kind == "latched_state_pending"}
    latch_fields = [name for name in vars(state.latched_state) if name not in {"pending_since", "pending_event_ids"}]
    latched_join = {}

    def meet_latched(left: Any, right: Any, name: str):
        if same_concrete(left, right):
            return left
        if isinstance(left, tuple) and isinstance(right, tuple) and len(left) == len(right):
            return tuple(meet_latched(x, y, name) for x, y in zip(left, right))
        return Unknown(f"{name} at branch join")

    for name in latch_fields:
        if (name in a.latched_state.pending_since) != (name in b.latched_state.pending_since):
            return False
        if name in written_fields:
            latched_join[name] = meet_latched(getattr(a.latched_state, name), getattr(b.latched_state, name), name)
    control_join = {}
    if "set_cond" in written_fields:
        for name in ("conditional_enable", "conditional_mask", "conditional_operator", "conditional_else_duration"):
            left, right = getattr(a, name), getattr(b, name)
            control_join[name] = left if same_concrete(left, right) else Unknown(f"{name} at branch join")
        for name in ("conditional_enable", "conditional_operator"):
            intervals = []
            bits = []
            for probe in (a, b):
                value = getattr(probe, name)
                bounds = ((value.value, value.value) if isinstance(value, Concrete)
                          else getattr(probe, name + "_bounds") or (0, _CLASSICAL_REGISTER_MASK))
                intervals.append(bounds)
                bits.append(getattr(probe, name + "_known_bits") or _bounds_known_bits(bounds))
            control_join[name + "_bounds"] = min(item[0] for item in intervals), max(item[1] for item in intervals)
            control_join[name + "_known_bits"] = bits[0][0] & bits[1][0], bits[0][1] & bits[1][1]

    def payload_known(value: Any) -> bool:
        return type(value) in {int, float, bool} or isinstance(value, (list, tuple)) and all(payload_known(item) for item in value)

    entry_scope = _scope(symbols, base)
    instructions_by_source = {item.source: item for item in state.instructions_by_pc.values()}

    def output_profile(probe: AnalysisState, events: list[TimelineEvent]):
        starts = {}
        payloads = {}
        packet_issues = {event.meta.get("rt_packet_id"): event for event in events if event.kind == "q1_issue"}
        offset = 0
        for index, packet in enumerate(probe.rt_packets[packet_start:]):
            starts[packet.id] = index, offset
            offset += packet.duration.value
            item = instructions_by_source[packet.source]
            issue = packet_issues[packet.id]
            resolved = {entry["index"]: entry["value"] for entry in issue.meta.get("resolved_args", [])}
            operands = []
            for arg_index, arg in enumerate(item.args):
                value = resolved.get(arg_index)
                if value is None:
                    register = _physical_read_register(arg, probe)
                    value = (issue.meta.get("register_reads", {}).get(register, issue.meta.get("register_snapshot", {}).get(register))
                             if register is not None else value_to_json(resolve_arg_value(arg, entry_scope)))
                operands.append(value or value_to_json(Unknown(arg.raw)))
            payloads[packet.id] = operands
        outputs, controls = [], []
        feedback = []
        payload_unknown = False
        seen = set()
        nco_fields = {"frequency", "phase", "phase_delta"}
        for event in events:
            packet_id = event.meta.get("rt_packet_id")
            if packet_id not in starts:
                continue
            if (event.kind == "unknown_region" and str(event.meta.get("op", "")).startswith("acquire")
                    and base.feedback_acq_channel is not None):
                return None
            if event.meta.get("feedback"):
                feedback.append((starts[packet_id], event.meta["feedback"]))
            if "applied_state" in event.meta and packet_id not in seen:
                settings = event.meta["applied_state"]
                nco = {key: value for key, value in settings.items() if key in nco_fields}
                if event.meta.get("phase_reset"):
                    controls.clear()
                    nco = {**event.meta["nco_state_at_issue"], "phase_reset": True}
                elif event.meta.get("captured_phase_reset") is None:
                    nco["phase_reset"] = None
                if nco:
                    payload_unknown |= not all(payload_known(value) for value in nco.values())
                    controls.append((starts[packet_id], nco))
                seen.add(packet_id)
            if event.meta.get("nco_phase_uncertain"):
                payload_unknown = True
            if event.lane.startswith("rt.") and event.kind not in {"wait", "conditional_skip", "unknown_region"}:
                details = {key: value for key, value in event.meta.items() if key in {
                    "waveform_index", "path", "acquisition_index", "bin_index", "field", "value", "applied_state", "feedback",
                }}
                operands = payloads[packet_id] if event.kind != "marker_state" else []
                payload_unknown |= any(value.get("kind") != "concrete" for value in operands)
                outputs.append((starts[packet_id], event.kind, details, operands))
        return outputs, controls, payload_unknown, feedback

    profiles = {path: output_profile(probe, deltas[path]) for path, probe in probes.items()}
    if (any(value is None for value in profiles.values())
            or profiles["taken"][3] != profiles["fallthrough"][3]):
        return False
    def nco_endpoint(probe):
        packet_events = {}
        last_loop = -1
        last_reset = -1
        for index, event in enumerate(probe.events):
            if event.kind == "loop_block" and event.meta.get("count") != 1:
                last_loop = index
            packet_id = event.meta.get("rt_packet_id")
            if packet_id is not None and "applied_state" in event.meta:
                packet_events.setdefault(packet_id, event)
            if event.meta.get("phase_reset"):
                last_reset = index
        if last_loop >= 0 and last_loop >= last_reset:
            return None
        frequency = accumulated = offset = 0
        # One unit is a quarter nanocycle: f-word * ns and phase-word *4
        # share exact integer units, including complete-turn equivalence.
        modulus = 4_000_000_000
        for packet in probe.rt_packets:
            event = packet_events.get(packet.id)
            if event is not None and not event.meta.get("conditional_skipped"):
                settings = event.meta["applied_state"]
                if event.meta.get("phase_reset"):
                    accumulated = offset = 0
                    settings = event.meta["nco_state_at_issue"]
                if "frequency" in settings:
                    frequency = (_signed_32_bit_register_value(settings["frequency"])
                                 if type(settings["frequency"]) is int else None)
                if "phase" in settings:
                    offset = settings["phase"] * 4 if type(settings["phase"]) is int else None
                if "phase_delta" in settings:
                    delta = settings["phase_delta"]
                    offset = offset + delta * 4 if offset is not None and type(delta) is int else None
            if packet.meta.get("nco_phase_uncertain"):
                accumulated = None
            if accumulated is not None:
                if frequency == 0:
                    pass
                elif frequency is not None and isinstance(packet.duration, Concrete):
                    accumulated = (accumulated + frequency * packet.duration.value) % modulus
                else:
                    accumulated = None
        return ((frequency, accumulated % modulus, offset % modulus)
                if frequency is not None and accumulated is not None and offset is not None else None)

    endpoints = [nco_endpoint(probes[path]) for path in ("taken", "fallthrough")]
    same_nco = (profiles["taken"][1] == profiles["fallthrough"][1]
                or endpoints[0] is not None and endpoints[0] == endpoints[1])
    nco_uncertain = (a.nco_phase_uncertain or b.nco_phase_uncertain
                     or not same_nco)
    def unknown_payload(value: Any) -> bool:
        if isinstance(value, dict):
            return value.get("kind") in {"unknown", "symbolic", "runtime_dependent", "range"} or any(unknown_payload(item) for item in value.values())
        return isinstance(value, (list, tuple)) and any(unknown_payload(item) for item in value)

    output_unknown = (nco_uncertain or profiles["taken"][0] != profiles["fallthrough"][0] or profiles["taken"][2] or profiles["fallthrough"][2] or any(
        event.meta.get("output_state_unknown") or unknown_payload(event.meta.get("applied_state"))
        or any(key.endswith("_index") and value is None for key, value in event.meta.items())
        for events in deltas.values() for event in events if event.lane.startswith("rt.")
    ))

    def packet_core(probe: AnalysisState):
        result = []
        for packet in probe.rt_packets[packet_start:]:
            values = (packet.q1_issue_t0, packet.q1_issue_t1, packet.rt_t0, packet.rt_t1)
            if not all(isinstance(value, Concrete) for value in values):
                return None
            result.append((packet.duration.value, *(value.value for value in values)))
        return result

    q1_known = isinstance(a.q1_time_ns, Concrete) and a.q1_time_ns == b.q1_time_ns
    core_a, core_b = packet_core(a), packet_core(b)
    queue_proven = q1_known and core_a is not None and core_a == core_b
    alternatives = (_complete_queue_alternatives(probes, q1_issue_event.id, symbols, waveform_names or {}, acquisition_names or {})
                    if not queue_proven and not state.queue_alternatives else {})
    meta = {"branch_id": branch_id, "condition": q1_issue_event.meta["condition"], "branch_taken": "runtime_dependent",
            "branch_join_proven": queue_proven, "branch_rt_join_proven": True, "join_pc": join_pc,
            "join_q1_time_ns": a.q1_time_ns.value if q1_known else None, "join_rt_duration_ns": elapsed["taken"]}
    if control_join:
        meta["conditional_state_at_join"] = {name: _plain_value(value) for name, value in control_join.items()
                                             if name in {"conditional_enable", "conditional_mask", "conditional_operator", "conditional_else_duration"}}
        meta["conditional_join_sources"] = {}
        for path, events in deltas.items():
            setter = next((event for event in reversed(events) if event.kind == "latched_state_pending"
                           and event.meta.get("field") == "set_cond"), None)
            meta["conditional_join_sources"][path] = _source_to_json(setter.source) if setter is not None else {"before_branch": True}
    q1_issue_event = _revise_q1_issue_duration(state, q1_issue_event, taken_duration)
    q1_issue_event.meta.update(meta)
    if alternatives:
        state.queue_alternatives = alternatives
        q1_issue_event.meta["queue_alternative_count"] = len(alternatives)
    region.meta.update(meta)
    state.events.append(region)
    for event in deltas["taken"]:
        event.meta.update(branch_join_branch_id=branch_id, branch_join_path="taken")
        if output_unknown and event.lane.startswith("rt.") and get_instruction_spec(event.meta.get("op", "play")).applies_latched_state:
            event.meta["output_state_unknown"] = True
        state.events.append(replace(event, confidence="assumed" if event.confidence == "exact" else event.confidence))
    state.rt_packets.extend(a.rt_packets[packet_start:])
    state.diagnostics.extend(a.diagnostics[diagnostic_start:])
    state.register_history.extend(replace(
        write, confidence="assumed" if write.confidence == "exact" else write.confidence,
        meta={**write.meta, "branch_join_branch_id": branch_id, "branch_join_path": "taken"},
    ) for write in a.register_history[history_start:])
    shared_origins = set().union(*(_linear_origin_parts(_operand_value_origin(
        Arg("reg", f"R{index}", f"R{index}"), entry_scope.get(f"R{index}", Unknown(f"R{index}")), state))[1]
        for index in range(_REGISTER_MAX_INDEX + 1)))
    written = set().union(*(_register_write_operands(state.instructions_by_pc[pc], state)
                            for pcs in paths.values() for pc in pcs))
    for name in ("registers", "register_bounds", "register_known_bits", "register_value_origins"):
        merged = dict(getattr(a, name))
        original = getattr(base, name)
        for register in refined_registers - written:
            if register in original:
                merged[register] = original[register]
            else:
                merged.pop(register, None)
        setattr(state, name, merged)
    state.pending_register_writes = []
    state.latched_state = deepcopy(a.latched_state)
    state.pending_phase_reset = a.pending_phase_reset if a.pending_phase_reset == b.pending_phase_reset else None
    state.nco_phase_uncertain = nco_uncertain
    for name, value in latched_join.items():
        setattr(state.latched_state, name, value)
    state.rt_time_ns = add_values(region.t0, Concrete(elapsed["taken"]))
    state.q1_time_ns = a.q1_time_ns if q1_known else Unknown("Q1 arrival is not proven at branch join")
    state.q1_issue_index = a.q1_issue_index - 1
    state.alu_flags = {key: value for key, value in (a.alu_flags or {}).items() if (b.alu_flags or {}).get(key) is value} or None
    state.alu_flags_source = instruction.source if state.alu_flags else None
    for name in ("conditional_enable", "conditional_mask", "conditional_operator", "conditional_else_duration",
                 "conditional_enable_bounds", "conditional_operator_bounds", "conditional_enable_known_bits",
                 "conditional_operator_known_bits", "conditional_time_unknown"):
        setattr(state, name, getattr(a, name))
    for name, value in control_join.items():
        setattr(state, name, value)
    joined = {}
    for register in sorted(written):
        arg = Arg("reg", register, register)
        left = a.registers.get(register, entry_scope.get(register, Unknown(register)))
        right = b.registers.get(register, entry_scope.get(register, Unknown(register)))
        origin_a, origin_b = _operand_value_origin(arg, left, a), _operand_value_origin(arg, right, b)
        agreed = same_concrete(left, right)
        shared = (origin_a is not None and origin_a == origin_b
                  and all(root in shared_origins or root.startswith(("binding:", "template:"))
                          for root in _linear_origin_parts(origin_a)[1]))
        value = left if agreed or shared else Unknown(f"{register} at branch join")
        state.registers[register] = value
        if isinstance(value, Concrete):
            state.register_bounds.pop(register, None)
            state.register_known_bits.pop(register, None)
        else:
            bounds_a, bounds_b = _unsigned_operand_bounds(arg, left, a), _unsigned_operand_bounds(arg, right, b)
            state.register_bounds[register] = min(bounds_a[0], bounds_b[0]), max(bounds_a[1], bounds_b[1])
            ones_a, zeros_a = _operand_known_bits(arg, left, a)
            ones_b, zeros_b = _operand_known_bits(arg, right, b)
            state.register_known_bits[register] = ones_a & ones_b, zeros_a & zeros_b
        state.register_value_origins[register] = origin_a if shared else _fresh_value_origin(state)
        sources = {}
        for path, probe in probes.items():
            writer = next((write for write in reversed(probe.register_history[history_start:]) if write.register == register), None)
            sources[path] = _source_to_json(writer.source) if writer is not None else {"before_branch": True}
        expression = f"{register} after branch join"
        step = {**_provenance_step(instruction, expression, value), "branch_join_sources": sources}
        if shared:
            source_register = next((f"R{index}" for index in range(_REGISTER_MAX_INDEX + 1)
                                    if _operand_value_origin(Arg("reg", f"R{index}", f"R{index}"),
                                                             entry_scope.get(f"R{index}", Unknown(f"R{index}")), base) == origin_a), None)
            if source_register is not None:
                step.update(branch_join_source_register=source_register,
                            source_provenance=deepcopy(base.register_provenance.get(source_register)))
        state.register_provenance[register] = _register_provenance(state, register, expression=expression, value=value, steps=[step])
        _record_register_write(state, register, value, source=instruction.source, event_id=q1_issue_event.id,
                               meta={"branch_join_proven": agreed or shared, "branch_id": branch_id, "branch_join_sources": sources})
        joined[register] = _plain_value(value)
    meta["join_registers"] = joined
    q1_issue_event.meta["join_registers"] = joined
    region.meta["join_registers"] = joined
    entry_event_ids = {event.id for event in base.events[:event_start - 1]}
    for name in latch_fields:
        if name not in state.latched_state.pending_since:
            continue
        ids = [probe.latched_state.pending_event_ids.get(name) for probe in probes.values()]
        if ids[0] == ids[1] and ids[0] in entry_event_ids:
            continue
        sources = {path: _source_to_json(probe.latched_state.pending_since[name]) for path, probe in probes.items()}
        value = getattr(state.latched_state, name)
        event = _emit_event(state, lane="state.pending", kind="latched_state_pending", t0=state.rt_time_ns, t1=state.rt_time_ns,
                            duration=Concrete(0), label=f"joined {name}", confidence=_confidence_for_value(value),
                            source=instruction.source, meta={"field": name, "value": _plain_value(value),
                                                            "branch_join_sources": sources, "q1_issue_event_id": q1_issue_event.id})
        state.latched_state.pending_since[name] = instruction.source
        state.latched_state.pending_event_ids[name] = event.id
    if output_unknown:
        latched_sources = {}
        for name in latch_fields:
            if getattr(state.latched_state, name) is None:
                continue
            latched_sources[name] = {}
            for path, events in deltas.items():
                setter = next((event for event in reversed(events) if event.kind == "latched_state_pending"
                               and event.meta.get("field") == name), None)
                latched_sources[name][path] = _source_to_json(setter.source) if setter is not None else {"before_branch": True}
        _emit_event(state, lane="rt.unknown", kind="unknown_region", t0=region.t0, t1=state.rt_time_ns,
                    duration=Concrete(elapsed["taken"]), label="uncertain branch outputs", confidence="unknown",
                    source=instruction.source, meta={"reason": "branch_outputs_unknown", "branch_id": branch_id,
                                                    "output_state_unknown": True, "branch_timing_known": True,
                                                    "nco_phase_uncertain": state.nco_phase_uncertain,
                                                    "pending_phase_reset": state.pending_phase_reset,
                                                    "latched_state_at_join": {
                                                        name: _plain_value(getattr(state.latched_state, name)) for name in latched_sources
                                                        if name != "phase" or not state.nco_phase_uncertain
                                                    }, "latched_sources_at_join": latched_sources,
                                                    "pending_fields_at_join": sorted(state.latched_state.pending_since)})
    state.executed_pcs.update(pc for pcs in paths.values() for pc in pcs)
    state.next_pc = join_pc
    return True


def _issue_domains_supported(state: AnalysisState, item: Instr, issue: TimelineEvent, symbols: SymbolTable) -> bool:
    scope = _scope(symbols, state)

    def bounds(arg):
        register = _physical_read_register(arg, state)
        if register is not None:
            value = issue.meta.get("register_reads", {}).get(register, {})
            if value.get("kind") == "concrete":
                word = value["value"] & _CLASSICAL_REGISTER_MASK
                return word, word
            interval = issue.meta.get("register_read_bounds", {}).get(register)
            return tuple(interval) if interval is not None else None
        value = resolve_arg_value(arg, scope)
        return (value.value, value.value) if isinstance(value, Concrete) else None

    if item.op in {"asl", "asr", "lsl", "lsr"}:
        _, arg = _docs_ordered_binary_args(item, state)
        interval = bounds(arg)
        return interval is not None and 0 <= interval[0] <= interval[1] <= 32
    if get_instruction_spec(item.op).category != "latched":
        return True
    disabled = item.op == "set_cond" and bounds(item.args[0]) == (0, 0)
    for index, _, low, high in _official_operand_ranges(item.op):
        if disabled and index in {1, 2}:
            continue
        arg = item.args[index]
        if _physical_read_register(arg, state) is not None and item.op in {"set_mrk", "set_freq"}:
            continue
        interval = bounds(arg)
        if interval is None:
            return False
        if _physical_read_register(arg, state) is not None and item.op in {"set_awg_gain", "set_awg_offs"}:
            interval = ((-0x80000000, 0x7FFFFFFF) if interval[0] < 0x80000000 <= interval[1]
                        else tuple(_signed_32_bit_register_value(value) for value in interval))
        if item.op == "set_cond" and index == 2 and not disabled:
            high = 5
        if not low <= interval[0] <= interval[1] <= high:
            return False
    return True


def _complete_queue_alternatives(
    probes: dict[str, AnalysisState], branch_issue_id: str, symbols: SymbolTable,
    waveform_names: dict[int, str], acquisition_names: dict[int, str],
) -> dict[str, AnalysisState]:
    """Retain finite private packet traces; public Q1 clocks stay joined."""
    alternatives = {}
    for path, original in probes.items():
        if (not isinstance(original.q1_time_ns, Concrete)
                or any(event.kind == "loop_block" for event in original.events)
                or any(event.kind == "q1_issue" and event.id != branch_issue_id and (
                    get_instruction_spec(event.meta.get("op", "")).category in {"sync", "feedback", "unknown"}
                    or str(event.meta.get("op", "")).startswith("fb_")
                    or event.meta.get("branch_decision") == "runtime_dependent" and not event.meta.get("branch_join_proven")
                ) for event in original.events)):
            return {}
        probe = deepcopy(original)
        probe.queue_alternatives = {}
        probe.queue_alternative_probe = True
        issue = next(event for event in probe.events if event.id == branch_issue_id)
        issue.meta.update(branch_decision="assumed", queue_alternative_path=path)
        instructions = {item.source: item for item in probe.instructions_by_pc.values()}
        if any(not _issue_domains_supported(probe, instructions[event.source], event, symbols)
               for event in probe.events if event.kind == "q1_issue" and event.source in instructions):
            return {}
        for _ in range(10000):
            if probe.stopped or probe.pc not in probe.instructions_by_pc:
                break
            _apply_ready_register_writes(probe)
            item = probe.instructions_by_pc[probe.pc]
            spec = get_instruction_spec(item.op)
            if (item.op == "loop" or spec.category in {"unknown", "sync", "feedback"}
                    or spec.category == "rt" and (spec.timing_effect_status != "known" or item.op not in {
                        "wait", "play", "upd_param", "upd_thres", "acquire", "acquire_weighed", "acquire_weighted",
                        "acquire_ttl", "acquire_timetags", "acquire_digital",
                    })
                    or spec.category == "latched" and item.op not in {
                        "set_mrk", "set_awg_gain", "set_awg_offs", "set_freq", "reset_ph", "set_ph",
                        "set_ph_delta", "set_digital", "set_scope_en", "set_cond",
                    }):
                return {}
            probe.executed_pcs.add(probe.pc)
            probe.next_pc = probe.pc + 1
            if item.op in {"asl", "asr", "lsl", "lsr"}:
                _, shift_arg = _docs_ordered_binary_args(item, probe)
                shift = resolve_arg_value(shift_arg, _scope(symbols, probe))
                bounds = _unsigned_operand_bounds(shift_arg, shift, probe)
                if bounds is None or not 0 <= bounds[0] <= bounds[1] <= 32:
                    return {}
            probe.current_resolved_args = _resolved_args_for_instruction(item, _scope(symbols, probe), probe)
            _execute_instruction(probe, item, symbols, waveform_names, acquisition_names)
            probe.current_resolved_args = []
            probe.q1_issue_index += 1
            issued = next((event for event in reversed(probe.events) if event.kind == "q1_issue"), None)
            if (issued is None or not _issue_domains_supported(probe, item, issued, symbols)
                    or spec.category == "branch" and (
                    issued.meta.get("branch_taken") is not True if item.op == "jmp"
                    else issued.meta.get("branch_decision") != "concrete")
                    or probe.next_pc is None or not probe.stopped and probe.next_pc <= probe.pc):
                return {}
            probe.pc = probe.next_pc
            if len(probe.rt_packets) > 10000:
                return {}
        else:
            return {}
        if (not probe.stopped
                or any(item.severity == "error" or item.category == "register_not_ready" for item in probe.diagnostics)
                or any(not isinstance(packet.duration, Concrete) or packet.duration.value < 0 for packet in probe.rt_packets)):
            return {}
        alternatives[path] = probe
    return alternatives


def _resolved_forward_branch_target_pc(
    state: AnalysisState,
    instruction: Instr,
    target_label: str | None,
    target_pc: int | None,
) -> int | None:
    if target_label is not None:
        if target_label not in state.labels or _is_backward_branch_target(state, instruction, target_label):
            return None
        return state.labels[target_label]
    if target_pc is not None and not _is_backward_numeric_branch_target(instruction, target_pc):
        return target_pc if target_pc in state.instructions_by_pc else None
    return None


def _branch_comparison_events(
    events: list[TimelineEvent],
    *,
    branch_id: str,
    path: str,
) -> list[TimelineEvent]:
    transformed: list[TimelineEvent] = []
    event_ids = {event.id: f"{event.id}:branch-{path}" for event in events}
    for event in events:
        meta = dict(event.meta)
        _remap_latched_event_ids(meta, event_ids)
        for key in ("first_iteration_event_ids", "event_ids"):
            if key in meta:
                meta[key] = [event_ids.get(event_id, event_id) for event_id in meta[key]]
        if "preview_iteration_event_ids" in meta:
            meta["preview_iteration_event_ids"] = {
                iteration: [event_ids.get(event_id, event_id) for event_id in members]
                for iteration, members in meta["preview_iteration_event_ids"].items()
            }
        if "preview_source_event_id" in meta:
            source_id = meta["preview_source_event_id"]
            meta["preview_source_event_id"] = event_ids.get(source_id, source_id)
        meta["branch_comparison_branch_id"] = branch_id
        meta["branch_comparison_path"] = path
        if isinstance(meta.get("rt_packet_id"), str):
            meta["rt_packet_id"] = _branch_comparison_packet_id(str(meta["rt_packet_id"]), path)
        if isinstance(meta.get("rt_packet_affine"), dict):
            meta["rt_packet_affine"] = {_branch_comparison_packet_id(packet_id, path): coefficients
                                        for packet_id, coefficients in meta["rt_packet_affine"].items()}
        transformed.append(
            replace(
                event,
                id=f"{event.id}:branch-{path}",
                lane=f"branch_compare.{path}.{event.lane}",
                label=f"{path}: {event.label}",
                meta=meta,
            )
        )
    return transformed


def _remap_latched_event_ids(meta: dict[str, Any], event_ids: dict[str, str]) -> None:
    issue_id = meta.get("q1_issue_event_id")
    if issue_id in event_ids:
        meta["q1_issue_event_id"] = event_ids[issue_id]
    if "applied_setter_event_ids" in meta:
        meta["applied_setter_event_ids"] = {
            field: event_ids.get(event_id, event_id)
            for field, event_id in meta["applied_setter_event_ids"].items()
        }


def _branch_comparison_packets(
    packets: list[RtPacket],
    *,
    branch_id: str,
    path: str,
) -> list[RtPacket]:
    transformed: list[RtPacket] = []
    for packet in packets:
        meta = dict(packet.meta)
        meta["branch_comparison_branch_id"] = branch_id
        meta["branch_comparison_path"] = path
        transformed.append(
            replace(
                packet,
                id=_branch_comparison_packet_id(packet.id, path),
                meta=meta,
            )
        )
    return transformed


def _branch_comparison_packet_id(packet_id: str, path: str) -> str:
    return f"{packet_id}:branch-{path}"


def _branch_comparison_diagnostics(
    diagnostics: list[Diagnostic],
    *,
    branch_id: str,
    path: str,
    path_event_ids: set[str],
) -> list[Diagnostic]:
    transformed: list[Diagnostic] = []
    for diagnostic in diagnostics:
        details = dict(diagnostic.details)
        details["branch_comparison_branch_id"] = branch_id
        details["branch_comparison_path"] = path
        transformed.append(
            Diagnostic(
                severity=diagnostic.severity,
                category=diagnostic.category,
                message=f"{path}: {diagnostic.message}",
                source=diagnostic.source,
                related_events=[
                    f"{event_id}:branch-{path}" if event_id in path_event_ids else event_id
                    for event_id in diagnostic.related_events
                ],
                details=details,
            )
        )
    return transformed


def _branch_id(state: AnalysisState, instruction: Instr, target_label: str | None, target_raw: str) -> str:
    target = target_label if target_label is not None else target_raw
    return f"{state.sequencer_id}:branch:{instruction.source.file}:{instruction.source.line}:{instruction.op}:{target}"


def _normalise_branch_assumptions(
    branch_assumptions: Mapping[str, str] | None,
) -> dict[str, BranchAssumptionPath]:
    if branch_assumptions is None:
        return {}
    if not isinstance(branch_assumptions, Mapping):
        raise ValueError(
            f"Invalid branch assumptions: expected a mapping/object, got {type(branch_assumptions).__name__}"
        )
    normalised: dict[str, BranchAssumptionPath] = {}
    for branch_id, path in branch_assumptions.items():
        if not isinstance(branch_id, str) or not branch_id.strip():
            raise ValueError("branch assumption ids must be non-empty strings")
        if path not in VALID_BRANCH_ASSUMPTION_PATHS:
            raise ValueError(f"Invalid branch assumption for {branch_id}: {path}")
        normalised[branch_id] = path  # type: ignore[assignment]
    return normalised


def _normalise_loop_preview_counts(loop_preview_counts: Mapping[str, int] | None) -> dict[str, int]:
    if loop_preview_counts is None:
        return {}
    normalised: dict[str, int] = {}
    for loop_key, count in loop_preview_counts.items():
        if not isinstance(loop_key, str) or not loop_key.strip():
            raise ValueError("loop preview keys must be non-empty strings")
        if isinstance(count, bool) or not isinstance(count, int):
            raise ValueError(f"Loop preview count for {loop_key} must be an integer")
        normalised[loop_key] = max(1, min(count, LOOP_PREVIEW_MAX_VISIBLE_ITERATIONS))
    return normalised


def _branch_specific_assumption(state: AnalysisState, branch_id: str) -> tuple[bool, str] | None:
    path = state.branch_assumptions.get(branch_id)
    if path == "taken":
        return True, "taken"
    if path == "fallthrough":
        return False, "fallthrough"
    return None


def _record_branch_assumption(
    state: AnalysisState,
    *,
    instruction: Instr,
    condition: str,
    target_label: str | None,
    branch_id: str,
    assumption_source: str,
    assumed_taken: bool | None,
    assumed_path: str,
) -> None:
    branches = state.metadata.setdefault("branches", {})
    assumptions = branches.setdefault("assumptions", [])
    record = {
        "condition": condition,
        "target_label": target_label,
        "branch_id": branch_id,
        "branch_policy": state.branch_policy,
        "assumption_source": assumption_source,
        "assumed_branch_path": assumed_path,
        "source": instruction.source,
    }
    if assumed_taken is not None:
        record["assumed_branch_taken"] = assumed_taken
    assumptions.append(record)


def _emit_undefined_label(state: AnalysisState, instruction: Instr, target_label: str) -> None:
    diagnostic = Diagnostic(
        severity="error",
        category="undefined_label",
        message=f"Undefined label: @{target_label}",
        source=instruction.source,
        details={"label": target_label, "op": instruction.op},
    )
    if diagnostic not in state.static_diagnostics:
        state.diagnostics.append(diagnostic)


def _affine_loop_timing(
    state: AnalysisState, body: list[Instr], events: list[TimelineEvent], count: int,
    counter_reg: str | None, symbols: SymbolTable,
) -> dict[str, Any] | None:
    if count < 2 or state.conditional_enable != Concrete(0) or any(
        item.op not in {"nop", "move", "add", "sub", "wait"} for item in body
    ):
        return None
    sources = {item.source for item in body}
    if any(item.source in sources and item.category in {
        "register_not_ready", "invalid_argument_count", "invalid_argument_type", "invalid_argument_value"
    } for item in state.diagnostics):
        return None
    issues = {event.source: event for event in events if event.kind == "q1_issue"}
    if not body or any(item.source not in issues for item in body):
        return None
    writes: set[str] = set()
    reads = set()
    samples: dict[str, list[tuple[SourceLocation, int | None]]] = {}
    for item in body:
        writes.update(_register_write_operands(item, state))
        destinations = _register_destination_argument_indexes(item.op)
        reads.update(register for _, register in _register_read_operands(item, state))
        event = issues[item.source]
        for index, arg in enumerate(item.args):
            if index in destinations:
                continue
            register = _physical_read_register(arg, state)
            if register is not None:
                value = event.meta.get("register_reads", {}).get(register, event.meta.get("register_snapshot", {}).get(register, {}))
                if value.get("kind") != "concrete":
                    value = next((entry["value"] for entry in event.meta.get("resolved_args", [])
                                  if entry["index"] == index), {})
                samples.setdefault(register, []).append((item.source, value.get("value") if value.get("kind") == "concrete" else None))
    counter_ready = all(value == count for _, value in samples.get(counter_reg, []))
    if counter_reg in writes:
        return None
    scope = _scope(symbols, state)

    values = {}
    for register in reads - writes:
        if register == counter_reg:
            if not counter_ready:
                return None
            values[register] = (count, -1)
            continue
        observed = [value for _, value in samples.get(register, [])]
        if not observed or observed[0] is None or any(value != observed[0] for value in observed):
            return None
        values[register] = (observed[0] & _CLASSICAL_REGISTER_MASK, 0)
    induction = {}
    defined = set()
    ready_definitions = []
    live_in = set()
    for index, item in enumerate(body):
        defined.update(register for ready, register in ready_definitions if ready <= index)
        destinations = _register_destination_argument_indexes(item.op)
        inputs = {register for _, register in _register_read_operands(item, state)}
        live_in.update((inputs & writes) - defined)
        ready_definitions.extend((index + 2, register) for register in _register_write_operands(item, state))
    first_final = deepcopy(state)
    _flush_pending_register_writes(first_final, completed_issue_index=state.q1_issue_index + 1)
    for register in live_in:
        initial = samples.get(register, [(None, None)])[0][1]
        result = first_final.registers.get(register)
        if initial is None or not isinstance(result, Concrete):
            return None
        initial &= _CLASSICAL_REGISTER_MASK
        induction[register] = initial, result.value - initial
        values[register] = induction[register]

    def operand(arg: Arg) -> tuple[int, int] | None:
        register = _physical_read_register(arg, state)
        if register is not None:
            return values.get(register)
        value = resolve_arg_value(arg, scope)
        return (value.value & _CLASSICAL_REGISTER_MASK, 0) if isinstance(value, Concrete) else None

    pending = []
    packets = {}
    for index, item in enumerate(body):
        for ready, register, value in pending:
            if ready <= index:
                values[register] = value
        pending = [entry for entry in pending if entry[0] > index]
        if item.op == "nop":
            continue
        if item.op in {"move", "wait"}:
            result = operand(item.args[0])
        else:
            left_arg, right_arg = _docs_ordered_binary_args(item, state)
            left, right = operand(left_arg), operand(right_arg)
            if left is None or right is None:
                return None
            sign = 1 if item.op == "add" else -1
            result = left[0] + sign * right[0], left[1] + sign * right[1]
        if result is None:
            return None
        intercept, slope = result
        low, high = sorted((intercept, intercept + slope * (count - 1)))
        if item.op == "wait":
            packet_id = issues[item.source].meta.get("rt_packet_id")
            packet = next((packet for packet in state.rt_packets if packet.id == packet_id), None)
            if not 0 <= low <= high <= _RT_DURATION_MAX or packet is None or packet.duration != Concrete(intercept):
                return None
            packets[packet_id] = {"intercept": intercept, "slope": slope}
        else:
            modulus = _CLASSICAL_REGISTER_MASK + 1
            if low // modulus != high // modulus:
                return None
            result = intercept - (low // modulus) * modulus, slope
            destination = _register_name(item.args[1 if item.op == "move" else 2], state)
            if destination is None:
                return None
            pending.append((index + 2, destination, result))
    for ready, register, value in pending:
        if ready > len(body) + 1:
            return None
        values[register] = value
    if any(values.get(register) != (initial + step, step) for register, (initial, step) in induction.items()):
        return None
    intercept = sum(item["intercept"] for item in packets.values())
    slope = sum(item["slope"] for item in packets.values())
    total = count * intercept + slope * count * (count - 1) // 2
    return {"period_affine": {"intercept": intercept, "slope": slope}, "total_duration": value_to_json(Concrete(total)),
            "rt_packet_affine": packets,
            "final_registers": {register: values[register][0] + values[register][1] * (count - 1) for register in writes}}


def _bounded_loop_total(
    state: AnalysisState, body: list[Instr], events: list[TimelineEvent], count: int,
    counter_reg: str | None, symbols: SymbolTable,
) -> dict[str, Any] | None:
    """Prove an elapsed sum without claiming individual unknown packet times."""
    if count < 2 or state.conditional_enable != Concrete(0) or any(
        item.op not in {"nop", "wait", "cmp", "test"}
        and not (get_instruction_spec(item.op).category == "classical" and _register_destination_argument_indexes(item.op))
        for item in body
    ):
        return None
    sources = {item.source for item in body}
    if any(item.source in sources and (item.severity == "error" or item.category == "register_not_ready")
           for item in state.diagnostics):
        return None
    issues = {event.source: event for event in events if event.kind == "q1_issue"}
    if not body or any(item.source not in issues for item in body):
        return None
    writes = set().union(*(_register_write_operands(item, state) for item in body))
    if counter_reg in writes:
        return None
    initial = {}
    live_in = set()
    ready = []
    defined = set()
    for index, item in enumerate(body):
        defined.update(register for when, register in ready if when <= index)
        for _, register in _register_read_operands(item, state):
            if register in defined:
                continue
            live_in.add(register)
            if register not in initial:
                bounds = issues[item.source].meta.get("register_read_bounds", {}).get(register)
                if not isinstance(bounds, list) or len(bounds) != 2:
                    return None
                initial[register] = tuple(bounds)
        ready.extend((index + 2, register) for register in _register_write_operands(item, state))
    if counter_reg in initial and initial[counter_reg] != (count, count):
        return None
    scope = _scope(symbols, state)
    modulus = _CLASSICAL_REGISTER_MASK + 1

    def plus(left, right, sign=1):
        terms = dict(left[1])
        for name, value in right[1].items():
            terms[name] = terms.get(name, 0) + sign * value
            if terms[name] == 0:
                del terms[name]
        return left[0] + sign * right[0], terms

    def scan(headers, domains, *, infer=False):
        values = dict(headers)
        pending = []
        total = (0, {})

        def interval(value):
            low = high = value[0]
            for name, coefficient in value[1].items():
                ends = [coefficient * endpoint for endpoint in domains[name]]
                low += min(ends)
                high += max(ends)
            return low, high

        def operand(arg):
            register = _physical_read_register(arg, state)
            if register is not None:
                return values.get(register)
            value = resolve_arg_value(arg, scope)
            return (value.value & _CLASSICAL_REGISTER_MASK, {}) if isinstance(value, Concrete) else None

        for index, item in enumerate(body):
            for when, register, result in pending:
                if when <= index:
                    values[register] = result
            pending = [entry for entry in pending if entry[0] > index]
            if item.op in {"nop", "cmp", "test"}:
                continue
            if item.op in {"wait", "move"}:
                result = operand(item.args[0])
            elif item.op in {"add", "sub"}:
                left_arg, right_arg = _docs_ordered_binary_args(item, state)
                left, right = operand(left_arg), operand(right_arg)
                result = plus(left, right, 1 if item.op == "add" else -1) if left is not None and right is not None else None
            else:
                result = None
            if item.op == "wait" and result is None:
                return None
            if result is not None:
                if item.op == "wait":
                    if not infer:
                        low, high = interval(result)
                        if not 0 <= low <= high <= _RT_DURATION_MAX:
                            return None
                        total = plus(total, result)
                    continue
                if not infer:
                    low, high = interval(result)
                    if low // modulus == high // modulus:
                        result = result[0] - low // modulus * modulus, result[1]
                    else:
                        result = None
            if infer:
                outputs = {register: result if result is not None else (0, {f"opaque:{index}:{register}": 1})
                           for register in _register_write_operands(item, state)}
            elif result is not None:
                outputs = {register: result for register in _register_write_operands(item, state)}
            else:
                if item.op in {"asl", "asr", "lsl", "lsr"}:
                    _, shift_arg = _docs_ordered_binary_args(item, state)
                    shift = operand(shift_arg)
                    if shift is None or not 0 <= interval(shift)[0] <= interval(shift)[1] <= 32:
                        return None
                probe = replace(state, registers={}, register_bounds={}, register_known_bits={}, register_value_origins={},
                                pending_register_writes=[], register_provenance={}, register_history=[], events=[], diagnostics=[])
                for register, expression in values.items():
                    low, high = interval(expression)
                    probe.registers[register] = Concrete(low) if low == high else Unknown(register)
                    probe.register_bounds[register] = low, high
                _execute_classical(probe, item, symbols)
                _flush_pending_register_writes(probe, completed_issue_index=state.q1_issue_index + 2)
                outputs = {}
                for register in _register_write_operands(item, state):
                    value = probe.registers.get(register, Unknown(register))
                    if isinstance(value, Concrete):
                        outputs[register] = value.value, {}
                    else:
                        atom = f"opaque:{index}:{register}"
                        domains[atom] = probe.register_bounds.get(register, (0, _CLASSICAL_REGISTER_MASK))
                        outputs[register] = 0, {atom: 1}
            pending.extend((index + 2, register, value) for register, value in outputs.items())
        for when, register, value in pending:
            if when > len(body) + 1:
                return None
            values[register] = value
        return values, total

    header = {register: (0, {f"header:{register}": 1}) for register in live_in}
    transition = scan(header, {}, infer=True)
    if transition is None:
        return None
    domains = {"iteration": (0, count - 1)}
    tracked = {}
    for register in live_in:
        atom = f"header:{register}"
        bounds = initial[register]
        expression = transition[0].get(register)
        step = None
        if register == counter_reg:
            header[register] = count, {"iteration": -1}
            continue
        if register not in writes:
            step = 0
        elif expression is not None and expression[1] == {atom: 1}:
            step = expression[0] % modulus
            if step >= modulus // 2:
                step -= modulus
        if step is not None and 0 <= bounds[0] + min(0, step * (count - 1)) <= bounds[1] + max(0, step * (count - 1)) <= _CLASSICAL_REGISTER_MASK:
            header[register] = ((bounds[0], {"iteration": step} if step else {}) if bounds[0] == bounds[1]
                                else (0, {atom: 1, **({"iteration": step} if step else {})}))
            domains[atom] = bounds
            if register in writes:
                tracked[register] = step
        else:
            domains[atom] = (0, _CLASSICAL_REGISTER_MASK)
    checked = scan(header, domains)
    if checked is None:
        return None
    values, period = checked
    if any(values.get(register) != plus(header[register], (step, {})) for register, step in tracked.items()):
        return None
    if any(name != "iteration" for name in period[1]):
        return None
    intercept, slope = period[0], period[1].get("iteration", 0)
    total = count * intercept + slope * count * (count - 1) // 2
    final = {register: expression[0] + expression[1].get("iteration", 0) * (count - 1)
             for register, expression in values.items() if register in writes
             and all(name == "iteration" for name in expression[1])}
    return {"total_duration": value_to_json(Concrete(total)), "period_affine": {"intercept": intercept, "slope": slope},
            "total_only": True, "final_registers": final}


def _evaluate_loop_iterations(
    state: AnalysisState, body: list[Instr], edge: Instr, events: list[TimelineEvent],
    count: int, counter_reg: str | None, symbols: SymbolTable,
) -> tuple[dict[str, Any], dict[str, tuple[Any, ...]]] | None:
    if not 2 <= count <= _CLASSICAL_REGISTER_MASK or counter_reg is None or any(
        item.op not in {"nop", "wait", "cmp", "test"}
        and not (get_instruction_spec(item.op).category == "classical"
                 and _register_destination_argument_indexes(item.op))
        for item in body
    ):
        return None
    sources = {item.source for item in body} | {edge.source}
    if any(item.source in sources and (item.severity == "error" or item.category == "register_not_ready")
           for item in state.diagnostics):
        return None
    writes = set().union(*(_register_write_operands(item, state) for item in body))
    if counter_reg in writes:
        return None
    packet_ids = {event.meta.get("rt_packet_id") for event in events if event.kind == "q1_issue"}
    first_packets = [packet for packet in state.rt_packets if packet.id in packet_ids]
    if (len(first_packets) != sum(item.op == "wait" for item in body)
            or any(not isinstance(packet.duration, Concrete) or not 0 <= packet.duration.value <= _RT_DURATION_MAX
                   for packet in first_packets)):
        return None
    series = {packet.id: [packet.duration.value] for packet in first_packets}
    periods = [sum(packet.duration.value for packet in first_packets)]
    probe = replace(state, registers=dict(state.registers), register_bounds=dict(state.register_bounds),
                    register_known_bits=dict(state.register_known_bits), register_value_origins=dict(state.register_value_origins),
                    latched_state=deepcopy(state.latched_state), pending_register_writes=deepcopy(state.pending_register_writes),
                    events=[], rt_packets=[], diagnostics=[], register_history=[], register_provenance={},
                    metadata=deepcopy(state.metadata), executed_pcs=set(state.executed_pcs), branch_assumptions={},
                    current_resolved_args=[], stopped=False)
    probe.q1_issue_index += 1
    _apply_ready_register_writes(probe)
    probe.registers[counter_reg] = Concrete(count - 1)
    live_in = set()
    defined = set()
    for item in body:
        live_in.update(register for _, register in _register_read_operands(item, state) if register not in defined)
        defined.update(_register_write_operands(item, state))

    def signature() -> dict[str, tuple[str, bool] | None]:
        scope = _scope(symbols, probe)
        return {register: _operand_value_origin(Arg("reg", register, register), scope.get(register, Unknown(register)), probe)
                for register in live_in}

    def execute(item: Instr, concrete_edge: bool = False) -> list[RtPacket] | None:
        _apply_ready_register_writes(probe)
        # Private evaluation must not grow symbolic strings or provenance trees.
        probe.registers = {register: value if isinstance(value, Concrete) else Unknown(f"loop value {register}")
                           for register, value in probe.registers.items()}
        probe.pending_register_writes = [
            replace(write, value=write.value if isinstance(write.value, Concrete) else Unknown(f"loop value {write.register}"),
                    provenance={}) for write in probe.pending_register_writes
        ]
        probe.events.clear()
        probe.rt_packets.clear()
        probe.diagnostics.clear()
        probe.register_history.clear()
        probe.register_provenance.clear()
        probe.pc = item.pc
        probe.next_pc = item.pc + 1
        probe.current_resolved_args = []
        probe.execution_mode = "concrete" if concrete_edge else "analysis"
        _execute_instruction(probe, item, symbols, {}, {})
        probe.execution_mode = "analysis"
        if probe.stopped or any(item.severity == "error" or item.category == "register_not_ready" for item in probe.diagnostics):
            return None
        if any(not isinstance(packet.duration, Concrete) or not 0 <= packet.duration.value <= _RT_DURATION_MAX
               for packet in probe.rt_packets):
            return None
        packets = list(probe.rt_packets)
        probe.q1_issue_index += 1
        _apply_ready_register_writes(probe)
        return packets

    work = 0
    repeats = False
    for _ in range(1, count):
        if work + len(body) + 1 > 10000:
            return None
        before = signature()
        packets = []
        for item in body:
            issued = execute(item)
            if issued is None:
                return None
            packets.extend(issued)
        if len(packets) != len(first_packets) or execute(edge, True) is None:
            return None
        work += len(body) + 1
        for original, packet in zip(first_packets, packets):
            series[original.id].append(packet.duration.value)
        periods.append(sum(packet.duration.value for packet in packets))
        if before == signature():
            repeats = True
            break
    total = sum(periods) + (count - len(periods)) * periods[-1]
    facts = {register: (probe.registers.get(register, Unknown(register)), probe.register_bounds.get(register),
                        probe.register_known_bits.get(register), probe.register_value_origins.get(register))
             for register in writes}
    return ({"total_duration": value_to_json(Concrete(total)), "iteration_durations": periods,
             "rt_packet_durations": series, "repeat_last_iteration": repeats}, facts)


def _emit_compact_loop(
    state: AnalysisState,
    instruction: Instr,
    *,
    q1_issue_event: TimelineEvent,
    target_label: str,
    counter_reg: str | None,
    count: Value | None,
    forever: bool,
    min_visible_iterations: int = 1,
    symbols: SymbolTable | None = None,
) -> bool:
    start_pc = state.labels.get(target_label)
    if start_pc is None or start_pc > instruction.pc:
        return False
    start_instruction = state.instructions_by_pc.get(start_pc)
    if start_instruction is None:
        return False

    if not forever and isinstance(count, Concrete) and count.value <= 0:
        _collapse_invalid_loop_count(
            state,
            instruction,
            q1_issue_event,
            target_label=target_label,
            counter_reg=counter_reg,
            count=count.value,
        )
        return True

    body_instructions = [state.instructions_by_pc[pc] for pc in range(start_pc, instruction.pc + 1)]
    counter_modified = counter_reg is not None and any(
        counter_reg in _register_write_operands(item, state)
        or item.op == "loop" and item.args and _register_name(item.args[0], state) == counter_reg
        for item in body_instructions[:-1]
    )
    if not forever and counter_modified and count != Concrete(1):
        # The value at the first loop edge does not prove the number of later
        # iterations if the body can overwrite that counter on each pass.
        count = Unknown(f"{counter_reg} is written inside the loop body")

    loop_id = f"L{state.loop_counter}"
    state.loop_counter += 1

    loop_q1_jump_runtime_ns = 16 if instruction.op == "jmp" else 24
    loop_q1_continue_runtime_ns = 4
    first_loop_edge_runtime_ns = loop_q1_jump_runtime_ns
    # The docs also describe loop as lowering to sub + jnz; use that behavior when its prose conflicts.
    if not forever and isinstance(count, Concrete) and count.value == 1:
        q1_issue_event = _revise_q1_issue_duration(state, q1_issue_event, loop_q1_continue_runtime_ns)
        first_loop_edge_runtime_ns = loop_q1_continue_runtime_ns

    preview_events = _loop_preview_events(state, start_instruction.source.line, instruction.source.line)
    for event in preview_events:
        _add_loop_preview_membership(event, loop_id)
        event.meta.setdefault("loop_iteration_index", 0)

    t0, t1, duration = _loop_timing(preview_events, current_rt_time=state.rt_time_ns)
    has_hidden_iterations = forever or not isinstance(count, Concrete) or count.value > 1
    written_registers: set[str] = set()
    if has_hidden_iterations:
        for body_instruction in body_instructions:
            written_registers.update(_register_write_operands(body_instruction, state))
            if body_instruction.op == "loop" and body_instruction.args:
                body_counter = _register_name(body_instruction.args[0], state)
                if body_counter is not None:
                    written_registers.add(body_counter)
    single_path = all(item.op == "nop" or get_instruction_spec(item.op).category in {"classical", "latched", "rt"}
                      for item in body_instructions[:-1])
    invariant_writes: set[str] = set()
    varying_by_source: dict[SourceLocation, set[str]] = {}
    invariant_duration_pcs: list[int] = []
    varying_duration = False
    read_hazards = {item.source for item in state.diagnostics if item.category == "register_not_ready"}
    skipped_sources = {event.source for event in preview_events if event.kind == "q1_issue"
                       and event.meta.get("conditional_skipped")}
    for item in body_instructions[:-1]:
        varying = written_registers - invariant_writes
        if item.source in read_hazards:
            varying = varying.union(register for _, register in _register_read_operands(item, state))
        varying_by_source[item.source] = varying
        spec = get_instruction_spec(item.op)
        duration_index = rt_duration_arg_for_instruction(spec, item)
        if duration_index is not None and duration_index < len(item.args):
            if item.source in skipped_sources or _physical_read_register(item.args[duration_index], state) not in varying:
                invariant_duration_pcs.append(item.pc)
            else:
                varying_duration = True
        writes = _register_write_operands(item, state)
        reads = {register for _, register in _register_read_operands(item, state)}
        if single_path and item.source not in read_hazards and spec.category == "classical" and reads.isdisjoint(varying):
            invariant_writes.update(writes)
        else:
            invariant_writes.difference_update(writes)
    invariant_writes.discard(counter_reg)
    preview_by_id = {event.id: event for event in preview_events}
    uncertain_setters = {
        event.id for event in preview_events
        if event.kind == "latched_state_pending"
        and varying_by_source.get(event.source, written_registers).intersection(
            preview_by_id.get(event.meta.get("q1_issue_event_id"), event).meta.get("register_reads", {})
        )
    }
    last_applied_setters = {
        field: setter_id for event in preview_events
        for field, setter_id in event.meta.get("applied_setter_event_ids", {}).items()
    }
    uncertain_latched_fields = sorted(
        field for field, setter_id in last_applied_setters.items() if setter_id in uncertain_setters
    )
    condition_setters = [item for item in body_instructions if item.op == "set_cond" and item.args]
    last_condition_varying = varying_by_source.get(condition_setters[-1].source, written_registers) if condition_setters else set()
    condition_varies = bool(condition_setters and _physical_read_register(condition_setters[-1].args[0], state) in last_condition_varying)
    mask_varies = bool(condition_setters and _physical_read_register(condition_setters[-1].args[1], state) in last_condition_varying)
    operator_varies = bool(condition_setters and _physical_read_register(condition_setters[-1].args[2], state) in last_condition_varying)
    conditional_rt_sources = {event.source for event in preview_events if event.kind == "q1_issue"
                              and event.meta.get("conditional_active")}
    conditional_loop_timing_unknown = False
    for body_instruction in body_instructions:
        if body_instruction.op == "set_cond" and body_instruction.args:
            varying = varying_by_source.get(body_instruction.source, written_registers)
            condition_varies = _physical_read_register(body_instruction.args[0], state) in varying
            mask_varies = _physical_read_register(body_instruction.args[1], state) in varying
            operator_varies = _physical_read_register(body_instruction.args[2], state) in varying
        elif (get_instruction_spec(body_instruction.op).category in {"rt", "sync"}
              and (condition_varies or (mask_varies or operator_varies) and body_instruction.source in conditional_rt_sources)):
            conditional_loop_timing_unknown = True
    if conditional_loop_timing_unknown:
        # Later iterations may gate RT work differently from the first one.
        duration = Unknown(f"conditional execution in compact loop {loop_id}")
        t1 = add_values(t0, duration)
        state.conditional_time_unknown = True
    affine_timing = None
    evaluated_facts = {}
    if not forever and isinstance(count, Concrete) and not counter_modified and symbols is not None and not conditional_loop_timing_unknown:
        affine_timing = _affine_loop_timing(state, body_instructions[:-1], preview_events, count.value, counter_reg, symbols)
        if affine_timing is None:
            evaluated = _evaluate_loop_iterations(state, body_instructions[:-1], instruction, preview_events,
                                                  count.value, counter_reg, symbols)
            if evaluated is not None:
                affine_timing, evaluated_facts = evaluated
        if affine_timing is None:
            affine_timing = _bounded_loop_total(state, body_instructions[:-1], preview_events, count.value, counter_reg, symbols)
        if affine_timing is not None:
            first_period = Concrete(affine_timing["iteration_durations"][0] if "iteration_durations" in affine_timing
                                    else affine_timing["period_affine"]["intercept"])
            if isinstance(duration, Concrete) and duration != first_period:
                affine_timing = None
            else:
                duration = first_period
                t1 = add_values(t0, duration)
                if affine_timing.get("total_only"):
                    state.rt_time_ns = t1
                varying_duration = False
    affine_durations_vary = bool(affine_timing and (
        affine_timing.get("total_only") or any(item["slope"] for item in affine_timing.get("rt_packet_affine", {}).values())
        or any(len(set(items)) > 1 for items in affine_timing.get("rt_packet_durations", {}).values())))
    if varying_duration and not forever:
        duration = Unknown(f"RT duration changes in compact loop {loop_id}")
        t1 = add_values(t0, duration)
    q1_period = _loop_q1_period(preview_events, q1_issue_event)
    q1_body_period = _loop_q1_body_period(q1_period, first_loop_edge_runtime_ns)
    loop_preview_key = _loop_preview_key(state, start_instruction.source, instruction.source)
    visible_iteration_count = _visible_loop_iteration_count(state, loop_preview_key, count=count, forever=forever)
    min_visible = max(1, min(int(min_visible_iterations), LOOP_PREVIEW_MAX_VISIBLE_ITERATIONS))
    visible_iteration_count = max(visible_iteration_count, min_visible)
    if not forever and isinstance(count, Concrete):
        visible_iteration_count = min(visible_iteration_count, max(1, count.value))
    if conditional_loop_timing_unknown or varying_duration or affine_durations_vary:
        visible_iteration_count = 1
    shown_iterations = list(range(visible_iteration_count))
    preview_policy = "first_iteration_only" if visible_iteration_count == 1 else "requested_iterations"
    count_display = "forever" if forever else _display_count(count)
    counter_before = _plain_value(count) if count is not None else None
    counter_after = _counter_after(count) if count is not None else None
    preview_ids = [event.id for event in preview_events]
    preview_iteration_event_ids: dict[str, list[str]] = {"0": preview_ids}

    state.diagnostics.append(
        Diagnostic(
            severity="info",
            category="loop_truncated",
            message=_loop_truncated_message(loop_id, visible_iteration_count),
            source=instruction.source,
            related_events=preview_ids,
            details={
                "loop_id": loop_id,
                "target_label": target_label,
                "count": count_display,
                "shown_iterations": shown_iterations,
            },
        )
    )

    loop_block = _emit_event(
        state,
        lane="rt.loop",
        kind="loop_block",
        t0=t0,
        t1=t1,
        duration=duration,
        label=f"loop {loop_id} {count_display if forever else 'x' + str(count_display)}",
        confidence="unknown" if state.conditional_time_unknown or varying_duration else "exact" if forever else _confidence_for_value(count),
        source=start_instruction.source,
        meta={
            "loop_id": loop_id,
            "start_pc": start_pc,
            "end_pc": instruction.pc,
            "label": target_label,
            "counter_reg": counter_reg,
            "count": count_display,
            "period": value_to_json(duration),
            "period_invariant": not varying_duration and not conditional_loop_timing_unknown
                                and (affine_timing is None or (
                                    len(set(affine_timing["iteration_durations"])) == 1 if "iteration_durations" in affine_timing
                                    else affine_timing["period_affine"]["slope"] == 0)),
            "counter_before": counter_before,
            "counter_after": counter_after,
            "preview_policy": preview_policy,
            "shown_iterations": shown_iterations,
            "visible_iteration_count": visible_iteration_count,
            "loop_preview_key": loop_preview_key,
            "loop_preview_cap": LOOP_PREVIEW_MAX_VISIBLE_ITERATIONS,
            "loop_q1_jump_runtime_ns": loop_q1_jump_runtime_ns,
            "loop_q1_continue_runtime_ns": loop_q1_continue_runtime_ns,
            "loop_q1_first_edge_runtime_ns": first_loop_edge_runtime_ns,
            "loop_q1_issue_event_id": q1_issue_event.id,
            "uncertain_registers": sorted(written_registers),
            "invariant_registers": sorted(invariant_writes),
            "invariant_duration_pcs": invariant_duration_pcs,
            "uncertain_latched_fields": uncertain_latched_fields,
            "source_start": _source_to_json(start_instruction.source),
            "source_end": _source_to_json(instruction.source),
            "first_iteration_event_ids": preview_ids,
            "preview_iteration_event_ids": preview_iteration_event_ids,
        },
    )
    if affine_timing is not None:
        loop_block.meta.update({key: value for key, value in affine_timing.items() if key != "final_registers"})
    if isinstance(t0, Concrete) and isinstance(t1, Concrete) and _is_wait_only_loop(
        state, instruction, start_pc, count, duration
    ):
        loop_block.meta["wait_only"] = True
    _emit_event(
        state,
        lane="rt.loop.preview",
        kind="loop_iteration_preview",
        t0=t0,
        t1=t1,
        duration=duration,
        label=f"{loop_id} iteration 0 preview",
        confidence="unknown" if state.conditional_time_unknown or varying_duration else "exact" if forever else _confidence_for_value(count),
        source=start_instruction.source,
        meta={
            "loop_id": loop_id,
            "iteration_index": 0,
            "preview_policy": preview_policy,
            "shown_iterations": shown_iterations,
            "event_ids": preview_ids,
            "rt_duration": value_to_json(duration),
            "q1_cost": value_to_json(_q1_cost(preview_events)),
            "register_summary": {
                "counter_reg": counter_reg,
                "before": counter_before,
                "after": counter_after,
            },
            "branch": "backward",
            "queue_slack_summary": "not_computed_yet",
        },
    )
    for iteration_index in range(1, visible_iteration_count):
        shifted_events = _materialize_loop_iteration_preview(
            state,
            preview_events,
            loop_id=loop_id,
            iteration_index=iteration_index,
            rt_period=duration,
            q1_period=q1_period,
            preview_policy=preview_policy,
            shown_iterations=shown_iterations,
            written_registers=written_registers,
        )
        shifted_ids = [event.id for event in shifted_events]
        preview_iteration_event_ids[str(iteration_index)] = shifted_ids
        _emit_event(
            state,
            lane="rt.loop.preview",
            kind="loop_iteration_preview",
            t0=_shift_value(t0, duration, iteration_index),
            t1=_shift_value(t1, duration, iteration_index),
            duration=duration,
            label=f"{loop_id} iteration {iteration_index} preview",
            confidence="unknown" if state.conditional_time_unknown else "exact" if forever else _confidence_for_value(count),
            source=start_instruction.source,
            meta={
                "loop_id": loop_id,
                "iteration_index": iteration_index,
                "preview_policy": preview_policy,
                "shown_iterations": shown_iterations,
                "event_ids": shifted_ids,
                "rt_duration": value_to_json(duration),
                "q1_cost": value_to_json(_q1_cost(preview_events)),
                "register_summary": {
                    "counter_reg": counter_reg,
                    "before": counter_before,
                    "after": counter_after,
                },
                "branch": "backward",
                "queue_slack_summary": "not_computed_yet",
            },
        )
    loop_block.meta["preview_iteration_event_ids"] = preview_iteration_event_ids
    # Only the first iteration is interpreted; later previews copy events without
    # executing their arithmetic. Do not expose those copies as register writes.
    _flush_pending_register_writes(state, completed_issue_index=state.q1_issue_index + 1)
    invariant_values = {register: (state.registers.get(register, Unknown(register)), state.register_bounds.get(register),
                                  state.register_known_bits.get(register), state.register_value_origins.get(register))
                        for register in invariant_writes}
    if has_hidden_iterations:
        reason = f"register writes in compact loop {loop_id} are not expanded"
        _invalidate_register_history(
            state,
            written_registers,
            source=instruction.source,
            event_id=q1_issue_event.id,
            reason=reason,
            meta={"loop_id": loop_id},
        )
        for register in written_registers:
            state.registers[register] = Unknown(reason)
            state.register_provenance.pop(register, None)
        last_setters = {event.meta["field"]: event.id for event in preview_events if event.kind == "latched_state_pending"}
        for field, setter_id in last_setters.items():
            if setter_id not in uncertain_setters or not hasattr(state.latched_state, field):
                continue
            value = getattr(state.latched_state, field)
            unknown = Unknown(f"{field} in compact loop {loop_id} is not evaluated")
            setattr(state.latched_state, field, tuple(unknown for _ in value) if isinstance(value, tuple) else unknown)
        if condition_varies:
            state.conditional_enable = Unknown(reason)
            state.conditional_enable_bounds = None
            state.conditional_enable_known_bits = None
        if mask_varies:
            state.conditional_mask = Unknown(reason)
        if operator_varies:
            state.conditional_operator = Unknown(reason)
            state.conditional_operator_bounds = None
            state.conditional_operator_known_bits = None
    if not forever:
        _advance_compact_loop_fallthrough_state(
            state,
            loop_id=loop_id,
            counter_reg=counter_reg,
            count=count,
            rt_period=duration,
            q1_body_period=q1_body_period,
            loop_q1_jump_runtime_ns=loop_q1_jump_runtime_ns,
            loop_q1_continue_runtime_ns=loop_q1_continue_runtime_ns,
            rt_total=Concrete(affine_timing["total_duration"]["value"]) if affine_timing is not None else None,
        )
        if isinstance(count, Concrete):
            for register, (value, bounds, known_bits, value_origin) in invariant_values.items():
                state.registers[register] = value
                if bounds is not None:
                    state.register_bounds[register] = bounds
                if known_bits is not None:
                    state.register_known_bits[register] = known_bits
                if value_origin is not None:
                    state.register_value_origins[register] = value_origin
                _record_register_write(state, register, value, source=instruction.source, event_id=q1_issue_event.id,
                                       meta={"op": "loop", "loop_id": loop_id, "loop_complete": True, "invariant": True})
        if affine_timing is not None:
            for register, word in affine_timing.get("final_registers", {}).items():
                value = Concrete(word)
                state.registers[register] = value
                state.register_bounds.pop(register, None)
                state.register_known_bits.pop(register, None)
                expression = f"{register} after affine loop {loop_id}"
                state.register_provenance[register] = _register_provenance(
                    state, register, expression=expression, value=value, steps=[_provenance_step(instruction, expression, value)],
                )
                _record_register_write(state, register, value, source=instruction.source, event_id=q1_issue_event.id,
                                       meta={"op": "loop", "loop_id": loop_id, "loop_complete": True, "affine": True,
                                             "invariant": register in invariant_values})
        for register, (value, bounds, known_bits, value_origin) in evaluated_facts.items():
            state.registers[register] = value
            for mapping, fact in ((state.register_bounds, bounds), (state.register_known_bits, known_bits)):
                if fact is None:
                    mapping.pop(register, None)
                else:
                    mapping[register] = fact
            if value_origin is not None:
                state.register_value_origins[register] = value_origin
            expression = f"{register} after evaluated loop {loop_id}"
            state.register_provenance[register] = _register_provenance(
                state, register, expression=expression, value=value, steps=[_provenance_step(instruction, expression, value)])
            _record_register_write(state, register, value, source=instruction.source, event_id=q1_issue_event.id,
                                   meta={"op": "loop", "loop_id": loop_id, "loop_complete": True, "evaluated": True,
                                         "invariant": register in invariant_values})
        if counter_reg is not None:
            _record_register_write(
                state,
                counter_reg,
                state.registers[counter_reg],
                source=instruction.source,
                event_id=q1_issue_event.id,
                meta={"op": "loop", "loop_id": loop_id, "loop_complete": True},
            )
    if instruction.op == "loop":
        counter_finishes = (isinstance(count, Concrete) and count.value > 0
                            and (count.value == 1 or not counter_modified and all(
                                get_instruction_spec(item.op).category != "branch" for item in body_instructions[:-1])))
        flags = _sub_flags(1, 1) if counter_finishes else None
        _set_alu_flags(state, instruction, flags)
    return True


def _is_wait_only_loop(
    state: AnalysisState,
    instruction: Instr,
    start_pc: int,
    count: Value | None,
    duration: Value,
) -> bool:
    if instruction.op != "loop" or not isinstance(count, Concrete) or count.value <= 0:
        return False
    if not isinstance(duration, Concrete) or duration.value <= 0:
        return False
    body = [state.instructions_by_pc[pc] for pc in range(start_pc, instruction.pc)]
    if not body:
        return False
    total = 0
    for item in body:
        # Immediate waits cannot change duration when the loop counter decrements.
        if item.op != "wait" or len(item.args) != 1 or item.args[0].kind != "imm":
            return False
        value = item.args[0].value
        if not isinstance(value, int) or not 4 <= value <= _RT_DURATION_MAX or value % 4:
            return False
        total += value
    return total == duration.value


def _loop_preview_key(state: AnalysisState, start_source: SourceLocation, end_source: SourceLocation) -> str:
    return f"{state.sequencer_id}:loop:{start_source.file}:{start_source.line}:{end_source.line}"


def _visible_loop_iteration_count(
    state: AnalysisState,
    loop_preview_key: str,
    *,
    count: Value | None,
    forever: bool,
) -> int:
    requested = state.loop_preview_counts.get(loop_preview_key, 1)
    requested = max(1, min(requested, LOOP_PREVIEW_MAX_VISIBLE_ITERATIONS))
    if forever:
        return requested
    if isinstance(count, Concrete):
        return max(1, min(requested, count.value))
    return requested


def _loop_truncated_message(loop_id: str, visible_iteration_count: int) -> str:
    if visible_iteration_count == 1:
        return f"Loop {loop_id} shown as compact block with iteration 0 preview only."
    return f"Loop {loop_id} shown as compact block with {visible_iteration_count} preview iterations."


def _materialize_loop_iteration_preview(
    state: AnalysisState,
    preview_events: list[TimelineEvent],
    *,
    loop_id: str,
    iteration_index: int,
    rt_period: Value,
    q1_period: Value,
    preview_policy: str,
    shown_iterations: list[int],
    written_registers: set[str],
) -> list[TimelineEvent]:
    shifted_events: list[TimelineEvent] = []
    event_ids = {event.id: f"{event.id}:loop-{loop_id}-iter-{iteration_index}" for event in preview_events}
    for event in preview_events:
        period = q1_period if event.kind == "q1_issue" else rt_period
        meta = dict(event.meta)
        # Later static previews copy timing without executing register writes.
        # Stable operands remain useful; changing operands have no known value.
        for key in ("register_reads", "register_snapshot"):
            if key in meta:
                meta[key] = {
                    register: value_to_json(Unknown(f"{register} in compact loop {loop_id} iteration {iteration_index} is not evaluated"))
                    if register in written_registers else value
                    for register, value in meta[key].items()
                }
        _remap_latched_event_ids(meta, event_ids)
        meta["loop_id"] = loop_id
        meta["loop_preview"] = loop_id
        meta["loop_iteration_index"] = iteration_index
        meta["preview_policy"] = preview_policy
        meta["shown_iterations"] = shown_iterations
        meta["preview_source_event_id"] = event.id
        loop_previews = meta.get("loop_previews")
        if isinstance(loop_previews, list):
            meta["loop_previews"] = list(loop_previews)
        else:
            meta["loop_previews"] = [loop_id]
        if loop_id not in meta["loop_previews"]:
            meta["loop_previews"].append(loop_id)
        shifted = TimelineEvent(
            id=f"{event.id}:loop-{loop_id}-iter-{iteration_index}",
            sequencer_id=event.sequencer_id,
            lane=event.lane,
            kind=event.kind,
            t0=_shift_value(event.t0, period, iteration_index),
            t1=_shift_value(event.t1, period, iteration_index),
            duration=event.duration,
            label=event.label,
            confidence=event.confidence,
            source=event.source,
            meta=meta,
        )
        state.events.append(shifted)
        shifted_events.append(shifted)
    return shifted_events


def _shift_value(value: Value | None, period: Value, iteration_index: int) -> Value | None:
    if value is None:
        return None
    return add_values(value, multiply_value(period, iteration_index))


def _collapse_invalid_loop_count(
    state: AnalysisState,
    instruction: Instr,
    q1_issue_event: TimelineEvent,
    *,
    target_label: str,
    counter_reg: str | None,
    count: int,
) -> None:
    q1_issue_event.meta["branch_taken"] = "runtime_dependent"
    q1_issue_event.meta["target_label"] = target_label
    q1_issue_event.meta["reason"] = "invalid_loop_count"
    state.diagnostics.append(
        Diagnostic(
            severity="error",
            category="invalid_argument_value",
            message=f"loop count must be positive, got {count}.",
            source=instruction.source,
            details={
                "op": instruction.op,
                "counter_reg": counter_reg,
                "target_label": target_label,
                "constraint": "positive",
                "value": count,
            },
        )
    )
    _emit_event(
        state,
        lane="rt.unknown",
        kind="unknown_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(f"invalid loop count: {count}"),
        label="analysis incomplete after invalid loop count",
        confidence="runtime_dependent",
        source=instruction.source,
        meta={
            "reason": "invalid_loop_count",
            "counter_reg": counter_reg,
            "target_label": target_label,
            "count": count,
        },
    )
    state.stopped = True


def _docs_ordered_binary_args(instruction: Instr, state: AnalysisState) -> tuple[Arg, Arg]:
    left_arg = instruction.args[0]
    right_arg = instruction.args[1]
    if instruction.op in {"cmp", "sub", "asl", "asr", "lsl", "lsr"} and _register_name(right_arg, state) is not None:
        if _register_name(left_arg, state) is None:
            return right_arg, left_arg
    return left_arg, right_arg


def _set_alu_flags(state: AnalysisState, instruction: Instr, flags: dict[str, bool] | None) -> None:
    state.alu_flags = flags
    state.alu_flags_source = instruction.source if flags is not None else None


def _sub_flags(left: int, right: int) -> dict[str, bool]:
    mask = _CLASSICAL_REGISTER_MASK
    sign = 0x80000000
    a = left & mask
    b = right & mask
    result = (a - b) & mask
    return {
        "zf": result == 0,
        "nf": bool(result & sign),
        "cf": a < b,
        "of": bool(((a ^ b) & (a ^ result)) & sign),
    }


def _logic_flags(result: int) -> dict[str, bool]:
    result &= _CLASSICAL_REGISTER_MASK
    return {"zf": result == 0, "nf": bool(result & 0x80000000), "cf": False, "of": False}


def _result_bounds_flags(bounds: tuple[int, int] | None) -> dict[str, bool]:
    flags = {}
    if bounds is not None:
        low, high = bounds
        if high == 0 or low > 0:
            flags["zf"] = high == 0
        if high < 0x80000000 or low >= 0x80000000:
            flags["nf"] = low >= 0x80000000
    return flags


def _bounds_known_bits(bounds: tuple[int, int]) -> tuple[int, int]:
    low, high = bounds
    prefix = _CLASSICAL_REGISTER_MASK ^ ((1 << (low ^ high).bit_length()) - 1)
    return low & prefix, ~high & prefix & _CLASSICAL_REGISTER_MASK


def _refine_bounds_with_bits(bounds: tuple[int, int] | None, known_bits: tuple[int, int]) -> tuple[int, int]:
    low, high = known_bits[0], _CLASSICAL_REGISTER_MASK ^ known_bits[1]
    return (low, high) if bounds is None else (max(bounds[0], low), min(bounds[1], high))


def _shift_count_facts(
    op: str, bounds: tuple[int, int] | None, known_bits: tuple[int, int], shift: int,
) -> tuple[tuple[int, int], tuple[int, int], dict[str, bool]]:
    source_ones, source_zeros = known_bits
    result_bounds = bounds if shift == 0 else None
    if op in {"asl", "lsl"}:
        ones = (source_ones << shift) & _CLASSICAL_REGISTER_MASK
        zeros = ((source_zeros << shift) | ((1 << shift) - 1)) & _CLASSICAL_REGISTER_MASK
        if shift == 32:
            result_bounds = 0, 0
        elif bounds is not None and bounds[1] << shift <= _CLASSICAL_REGISTER_MASK:
            result_bounds = bounds[0] << shift, bounds[1] << shift
    else:
        ones, zeros = source_ones >> shift, source_zeros >> shift
        fill = _CLASSICAL_REGISTER_MASK ^ (_CLASSICAL_REGISTER_MASK >> shift)
        if op == "lsr" or source_zeros & 0x80000000:
            zeros |= fill
        elif source_ones & 0x80000000:
            ones |= fill
        if bounds is not None:
            if op == "lsr":
                result_bounds = bounds[0] >> shift, bounds[1] >> shift
            elif bounds[1] < 0x80000000 or bounds[0] >= 0x80000000:
                result_bounds = tuple((_signed_32_bit_register_value(endpoint) >> shift) & _CLASSICAL_REGISTER_MASK
                                      for endpoint in bounds)
    result_bounds = _refine_bounds_with_bits(result_bounds, (ones, zeros))
    flags = _result_bounds_flags(result_bounds)
    if shift == 0:
        flags.update(cf=False, of=False)
    else:
        carry_bit = 1 << ((32 - shift) if op in {"asl", "lsl"} else shift - 1)
        if carry_bit & (source_ones | source_zeros):
            flags["cf"] = bool(carry_bit & source_ones)
        if op in {"asr", "lsr"}:
            flags["of"] = False
        elif "cf" in flags and "nf" in flags:
            flags["of"] = flags["cf"] != flags["nf"]
    return result_bounds, (ones, zeros), flags


def _arithmetic_known_bits(
    op: str, left: tuple[int, int], right: tuple[int, int], *, same_value: bool = False, complementary: bool = False,
) -> tuple[tuple[int, int], dict[str, bool]]:
    subtract = op == "sub"
    carries = {int(subtract)}
    ones, zeros = 0, 0
    overflows = set()
    for position in range(32):
        bit = 1 << position
        left_values = (1,) if left[0] & bit else (0,) if left[1] & bit else (0, 1)
        right_values = (1,) if right[0] & bit else (0,) if right[1] & bit else (0, 1)
        results, next_carries = set(), set()
        for a, b, carry in product(left_values, right_values, carries):
            if same_value and a != b or complementary and a == b:
                continue
            total = a + (b ^ int(subtract)) + carry
            results.add(total & 1)
            next_carries.add(total >> 1)
            if position == 31:
                overflows.add(bool(carry ^ (total >> 1)))
        if results == {1}:
            ones |= bit
        elif results == {0}:
            zeros |= bit
        carries = next_carries
    flags = {}
    if len(carries) == 1:
        flags["cf"] = bool(next(iter(carries))) != subtract
    if len(overflows) == 1:
        flags["of"] = next(iter(overflows))
    return (ones, zeros), flags


def _arithmetic_bounds_flags(
    op: str, left: tuple[int, int] | None, right: tuple[int, int] | None,
) -> dict[str, bool] | None:
    if left is None or right is None:
        return None
    modulus = _CLASSICAL_REGISTER_MASK + 1
    low, high = ((left[0] + right[0], left[1] + right[1]) if op == "add"
                 else (left[0] - right[1], left[1] - right[0]))
    bounds = (low % modulus, high % modulus) if low // modulus == high // modulus else None
    flags = _result_bounds_flags(bounds)
    if op == "add":
        if high < modulus or low >= modulus:
            flags["cf"] = low >= modulus
    elif left[1] < right[0] or left[0] >= right[1]:
        flags["cf"] = left[1] < right[0]
    signed = []
    for lower, upper in (left, right):
        parts = []
        if lower < 0x80000000:
            parts.append((lower, min(upper, 0x7FFFFFFF)))
        if upper >= 0x80000000:
            parts.append((max(lower, 0x80000000) - modulus, upper - modulus))
        signed.append(parts)
    overflows = set()
    for a, b in product(*signed):
        low, high = ((a[0] + b[0], a[1] + b[1]) if op == "add" else (a[0] - b[1], a[1] - b[0]))
        if -0x80000000 <= low <= high < 0x80000000:
            overflows.add(False)
        elif low >= 0x80000000 or high < -0x80000000:
            overflows.add(True)
        else:
            return flags or None
    if len(overflows) == 1:
        flags["of"] = overflows.pop()
    return flags or None


def _add_flags(left: int, right: int) -> dict[str, bool]:
    mask = _CLASSICAL_REGISTER_MASK
    sign = 0x80000000
    a = left & mask
    b = right & mask
    result = (a + b) & mask
    return {
        "zf": result == 0,
        "nf": bool(result & sign),
        "cf": a + b > mask,
        "of": bool((~(a ^ b) & (a ^ result)) & sign),
    }


def _shift_flags(op: str, left: int, shift: int, result: int) -> dict[str, bool]:
    mask = _CLASSICAL_REGISTER_MASK
    result &= mask
    unsigned_left = left & mask
    shift &= mask
    cf = False
    of = False
    if 0 < shift <= 32:
        if op in {"asl", "lsl"}:
            cf = bool(unsigned_left & (1 << (32 - shift)))
            of = bool(cf != bool(result & 0x80000000))
        else:
            cf = bool(unsigned_left & (1 << (shift - 1)))
    return {"zf": result == 0, "nf": bool(result & 0x80000000), "cf": cf, "of": of}


def _binary_alu_flags(op: str, left: Value, right: Value, result: Value) -> dict[str, bool] | None:
    if op in {"and", "or", "xor"} and isinstance(result, Concrete):
        return _logic_flags(result.value)
    if not isinstance(left, Concrete) or not isinstance(right, Concrete) or not isinstance(result, Concrete):
        return None
    if op == "add":
        return _add_flags(left.value, right.value)
    if op == "sub":
        return _sub_flags(left.value, right.value)
    if op in {"asl", "asr", "lsl", "lsr"}:
        return _shift_flags(op, left.value, right.value, result.value)
    return _logic_flags(result.value)


def _build_immutable_value_origins(symbols: SymbolTable, defs: dict[str, Arg], state: AnalysisState) -> dict[str, tuple[str, bool]]:
    origins = {name: (f"binding:param:{name}", False) for name in symbols}
    for name, arg in defs.items():
        origin = None
        if arg.kind == "imm":
            origin = f"constant:{int(arg.value) & _CLASSICAL_REGISTER_MASK}", False
        elif arg.kind in {"symbol", "placeholder"} and _physical_read_register(arg, state) is None:
            origin = next((origins[key] for key in _symbol_lookup_names(str(arg.value)) if key in origins), None)
            if origin is None:
                origin = f"template:{arg.value}", False
        origins[name] = origin if origin is not None else (f"binding:def:{name}", False)
    return origins


def _operand_value_origin(arg: Arg, value: Value, state: AnalysisState) -> tuple[str, bool] | None:
    if isinstance(value, Concrete):
        return f"constant:{value.value & _CLASSICAL_REGISTER_MASK}", False
    register = _physical_read_register(arg, state)
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


def _operand_value_relation(left: Arg, left_value: Value, right: Arg, right_value: Value, state: AnalysisState) -> bool | None:
    a = _operand_value_origin(left, left_value, state)
    b = _operand_value_origin(right, right_value, state)
    return a[1] != b[1] if a is not None and b is not None and a[0] == b[0] else None


def _complement_value_origin(origin: tuple[str, bool] | None) -> tuple[str, bool] | None:
    return (origin[0], not origin[1]) if origin is not None else None


def _linear_origin_parts(origin: tuple[str, bool] | None):
    if origin is None:
        return None
    token, complemented = origin
    if token.startswith("linear:"):
        offset, items = json.loads(token[7:])
        terms = dict(items)
    elif token.startswith("constant:"):
        offset, terms = int(token[9:]), {}
    else:
        offset, terms = 0, {token: 1}
    if complemented:
        offset = _CLASSICAL_REGISTER_MASK - offset
        terms = {name: (-coefficient) & _CLASSICAL_REGISTER_MASK for name, coefficient in terms.items()}
    return offset & _CLASSICAL_REGISTER_MASK, terms


def _canonical_linear_origin(offset: int, terms: dict[str, int]) -> tuple[str, bool] | None:
    offset &= _CLASSICAL_REGISTER_MASK
    terms = {name: coefficient & _CLASSICAL_REGISTER_MASK for name, coefficient in terms.items()
             if coefficient & _CLASSICAL_REGISTER_MASK}
    if not terms:
        return f"constant:{offset}", False
    if len(terms) == 1:
        name, coefficient = next(iter(terms.items()))
        if offset == 0 and coefficient == 1:
            return name, False
        if offset == _CLASSICAL_REGISTER_MASK and coefficient == _CLASSICAL_REGISTER_MASK:
            return name, True
    if len(terms) > 8:
        return None
    direct = json.dumps([offset, sorted(terms.items())], separators=(",", ":"))
    inverse = json.dumps([_CLASSICAL_REGISTER_MASK - offset,
                          sorted((name, (-coefficient) & _CLASSICAL_REGISTER_MASK) for name, coefficient in terms.items())],
                         separators=(",", ":"))
    token, complemented = (direct, False) if direct <= inverse else (inverse, True)
    return ("linear:" + token, complemented) if len(token) <= 4096 else None


def _arithmetic_value_origin(op: str, left: tuple[str, bool] | None, right: tuple[str, bool] | None,
                             shift: int | None = None) -> tuple[str, bool] | None:
    a = _linear_origin_parts(left)
    if a is None:
        return None
    if op in {"lsl", "asl"} and shift is not None:
        factor = 1 << shift
        return _canonical_linear_origin(a[0] * factor, {name: value * factor for name, value in a[1].items()})
    b = _linear_origin_parts(right)
    if op == "multiply_low" and b is not None:
        if not a[1]:
            return _scaled_value_origin(right, a[0])
        if not b[1]:
            return _scaled_value_origin(left, b[0])
        return None
    if op not in {"add", "sub"} or b is None:
        return None
    sign = 1 if op == "add" else -1
    terms = dict(a[1])
    for name, coefficient in b[1].items():
        terms[name] = terms.get(name, 0) + sign * coefficient
    return _canonical_linear_origin(a[0] + sign * b[0], terms)


def _scaled_value_origin(origin: tuple[str, bool] | None, factor: int) -> tuple[str, bool] | None:
    parts = _linear_origin_parts(origin)
    return (_canonical_linear_origin(parts[0] * factor, {name: value * factor for name, value in parts[1].items()})
            if parts is not None else None)


def _origin_peer_facts(
    state: AnalysisState, origin: tuple[str, bool] | None,
    bounds: tuple[int, int] | None, known_bits: tuple[int, int] | None,
) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    if origin is None:
        return bounds, known_bits
    for register in state.register_bounds.keys() | state.register_known_bits.keys():
        value = state.registers.get(register, Unknown(register))
        peer = state.register_value_origins.get(register, _operand_value_origin(Arg("reg", register, register), value, state))
        if peer is None or peer[0] != origin[0]:
            continue
        interval = state.register_bounds.get(register, (0, _CLASSICAL_REGISTER_MASK))
        ones, zeros = state.register_known_bits.get(register, (0, 0))
        if isinstance(value, Concrete):
            word = value.value & _CLASSICAL_REGISTER_MASK
            interval, (ones, zeros) = (word, word), (word, _CLASSICAL_REGISTER_MASK ^ word)
        prefix_ones, prefix_zeros = _bounds_known_bits(interval)
        ones, zeros = ones | prefix_ones, zeros | prefix_zeros
        if peer[1] != origin[1]:
            interval = _CLASSICAL_REGISTER_MASK - interval[1], _CLASSICAL_REGISTER_MASK - interval[0]
            ones, zeros = zeros, ones
        current = bounds or (0, _CLASSICAL_REGISTER_MASK)
        bounds = max(current[0], interval[0]), min(current[1], interval[1])
        old_ones, old_zeros = known_bits or (0, 0)
        known_bits = old_ones | ones, old_zeros | zeros
    if bounds is not None or known_bits not in {None, (0, 0)}:
        bounds = _refine_bounds_with_bits(bounds, known_bits or (0, 0))
    return bounds, known_bits


def _unsigned_operand_bounds(arg: Arg, value: Value, state: AnalysisState) -> tuple[int, int] | None:
    if isinstance(value, Concrete):
        word = value.value & _CLASSICAL_REGISTER_MASK
        return word, word
    register = _physical_read_register(arg, state)
    if register is None:
        return None
    bounds = state.register_bounds.get(register, (0, _CLASSICAL_REGISTER_MASK))
    known_bits = state.register_known_bits.get(register)
    return _refine_bounds_with_bits(bounds, known_bits) if known_bits is not None else bounds


def _operand_known_bits(arg: Arg, value: Value, state: AnalysisState) -> tuple[int, int]:
    bounds = _unsigned_operand_bounds(arg, value, state)
    ones, zeros = _bounds_known_bits(bounds) if bounds is not None else (0, 0)
    if not isinstance(value, Concrete):
        register_bits = state.register_known_bits.get(_physical_read_register(arg, state), (0, 0))
        ones |= register_bits[0]
        zeros |= register_bits[1]
    return ones, zeros


def _comparison_flags(left_arg: Arg, left: Value, right_arg: Arg, right: Value, state: AnalysisState) -> dict[str, bool] | None:
    if isinstance(left, Concrete) and isinstance(right, Concrete):
        return _sub_flags(left.value, right.value)
    relation = _operand_value_relation(left_arg, left, right_arg, right, state)
    if relation is False:
        return _sub_flags(0, 0)
    flags = _arithmetic_bounds_flags("sub", _unsigned_operand_bounds(left_arg, left, state),
                                     _unsigned_operand_bounds(right_arg, right, state)) or {}
    bits, bit_flags = _arithmetic_known_bits("sub", _operand_known_bits(left_arg, left, state),
                                           _operand_known_bits(right_arg, right, state), complementary=relation is True)
    flags.update(bit_flags)
    flags.update(_result_bounds_flags((bits[0], _CLASSICAL_REGISTER_MASK ^ bits[1])))
    difference = _linear_origin_parts(_arithmetic_value_origin("sub", _operand_value_origin(left_arg, left, state),
                                                              _operand_value_origin(right_arg, right, state)))
    if difference is not None and not difference[1]:
        flags.update(_result_bounds_flags((difference[0], difference[0])))
    return flags or None


def _multiply_operand_bounds(arg: Arg, value: Value, state: AnalysisState, width: int, signed: bool) -> tuple[int, int]:
    mask = (1 << width) - 1
    interval = _unsigned_operand_bounds(arg, value, state)
    if interval is not None and interval[0] >> width == interval[1] >> width:
        low, high = interval[0] & mask, interval[1] & mask
    else:
        low, high = 0, mask
    ones, zeros = _operand_known_bits(arg, value, state)
    low, high = max(low, ones & mask), min(high, mask ^ (zeros & mask))
    if signed:
        sign = 1 << (width - 1)
        if low >= sign:
            low, high = low - (1 << width), high - (1 << width)
        elif high >= sign:
            low, high = -sign, sign - 1
    return low, high


def _multiply_bounds(left: tuple[int, int], right: tuple[int, int], relation: bool | None, complement_sum: int) -> tuple[int, int]:
    low, high = left
    if relation is False:
        return (0 if low <= 0 <= high else min(low * low, high * high)), max(low * low, high * high)
    if relation is True:
        candidates = [low, high, *(point for point in (complement_sum // 2, complement_sum // 2 + 1) if low <= point <= high)]
        products = [point * (complement_sum - point) for point in candidates]
    else:
        products = [a * b for a in left for b in right]
    return min(products), max(products)


def _word_interval(bounds: tuple[int, int]) -> tuple[int, int] | None:
    low, high = bounds
    return (low & _CLASSICAL_REGISTER_MASK, high & _CLASSICAL_REGISTER_MASK) if low >> 32 == high >> 32 else None


def _execute_classical(state: AnalysisState, instruction: Instr, symbols: SymbolTable) -> None:
    scope = _scope(symbols, state)
    destination = _register_name(instruction.args[1], state) if len(instruction.args) >= 2 else None
    if instruction.op == "move" and destination is not None:
        value = resolve_arg_value(instruction.args[0], scope)
        value = _wrap_concrete_classical_register_value(value)
        value_origin = _operand_value_origin(instruction.args[0], value, state)
        known_bits = _operand_known_bits(instruction.args[0], value, state)
        bounds = _refine_bounds_with_bits(_unsigned_operand_bounds(instruction.args[0], value, state), known_bits)
        if bounds[0] == bounds[1]:
            value = Concrete(bounds[0])
        provenance = _move_provenance(state, instruction, instruction.args[0], destination, value)
        _schedule_register_write(state, instruction, destination, value, provenance,
                                 unsigned_bounds=bounds, known_bits=known_bits, value_origin=value_origin)
    elif instruction.op == "not" and destination is not None:
        source = resolve_arg_value(instruction.args[0], scope)
        value_origin = _complement_value_origin(_operand_value_origin(instruction.args[0], source, state))
        value = _unary_classical_value("not", source)
        source_bounds = _unsigned_operand_bounds(instruction.args[0], source, state)
        bounds = (_CLASSICAL_REGISTER_MASK - source_bounds[1], _CLASSICAL_REGISTER_MASK - source_bounds[0]) if source_bounds is not None else None
        source_ones, source_zeros = _operand_known_bits(instruction.args[0], source, state)
        known_bits = source_zeros, source_ones
        bounds = _refine_bounds_with_bits(bounds, known_bits)
        if bounds is not None and bounds[0] == bounds[1]:
            value = Concrete(bounds[0])
        provenance = _unary_provenance(state, instruction, instruction.args[0], destination, value)
        _schedule_register_write(state, instruction, destination, value, provenance, unsigned_bounds=bounds, known_bits=known_bits, value_origin=value_origin)
        flags = _logic_flags(value.value) if isinstance(value, Concrete) else {**_result_bounds_flags(bounds), "cf": False, "of": False}
        _set_alu_flags(state, instruction, flags)
    elif instruction.op in {"add", "sub", "and", "or", "xor", "asl", "asr", "lsl", "lsr"} and len(instruction.args) >= 3:
        destination = _register_name(instruction.args[2], state)
        if destination is None:
            return
        left_arg, right_arg = _docs_ordered_binary_args(instruction, state)
        left = resolve_arg_value(left_arg, scope)
        right = resolve_arg_value(right_arg, scope)
        relation = _operand_value_relation(left_arg, left, right_arg, right, state)
        same_value, complementary = relation is False, relation is True
        if complementary and instruction.op in {"and", "or", "xor", "add"}:
            value = Concrete(0 if instruction.op == "and" else _CLASSICAL_REGISTER_MASK)
        elif same_value and instruction.op in {"sub", "xor"}:
            value = Concrete(0)
        elif same_value and instruction.op in {"and", "or"}:
            value = left
        elif instruction.op == "add":
            value = left if right == Concrete(0) else right if left == Concrete(0) else add_values(left, right)
        elif instruction.op == "sub":
            value = left if right == Concrete(0) else subtract_values(left, right)
        else:
            value = _binary_classical_value(instruction.op, left, right)
        if instruction.op in _WRAPPING_CLASSICAL_OPS:
            value = _wrap_concrete_classical_register_value(value)
        bounds = None
        left_bounds = _unsigned_operand_bounds(left_arg, left, state)
        right_bounds = _unsigned_operand_bounds(right_arg, right, state)
        left_ones, left_zeros = _operand_known_bits(left_arg, left, state)
        right_ones, right_zeros = _operand_known_bits(right_arg, right, state)
        if left_bounds is not None and right_bounds is not None:
            if instruction.op in {"and", "or"} and same_value:
                bounds = left_bounds
            elif instruction.op == "and":
                if left_bounds == (_CLASSICAL_REGISTER_MASK, _CLASSICAL_REGISTER_MASK):
                    bounds = right_bounds
                elif right_bounds == (_CLASSICAL_REGISTER_MASK, _CLASSICAL_REGISTER_MASK):
                    bounds = left_bounds
                else:
                    bounds = 0, min(left_bounds[1], right_bounds[1])
            elif instruction.op in {"or", "xor"}:
                if left == Concrete(0):
                    bounds = right_bounds
                elif right == Concrete(0):
                    bounds = left_bounds
                elif instruction.op == "xor" and left_bounds == (_CLASSICAL_REGISTER_MASK, _CLASSICAL_REGISTER_MASK):
                    bounds = _CLASSICAL_REGISTER_MASK - right_bounds[1], _CLASSICAL_REGISTER_MASK - right_bounds[0]
                elif instruction.op == "xor" and right_bounds == (_CLASSICAL_REGISTER_MASK, _CLASSICAL_REGISTER_MASK):
                    bounds = _CLASSICAL_REGISTER_MASK - left_bounds[1], _CLASSICAL_REGISTER_MASK - left_bounds[0]
                else:
                    high = (1 << max(left_bounds[1].bit_length(), right_bounds[1].bit_length())) - 1
                    bounds = (max(left_bounds[0], right_bounds[0]) if instruction.op == "or" else 0), high
            elif instruction.op in {"add", "sub"}:
                low, high = ((left_bounds[0] + right_bounds[0], left_bounds[1] + right_bounds[1]) if instruction.op == "add"
                             else (left_bounds[0] - right_bounds[1], left_bounds[1] - right_bounds[0]))
                modulus = _CLASSICAL_REGISTER_MASK + 1
                if low // modulus == high // modulus:
                    bounds = low % modulus, high % modulus
        known_bits = None
        arithmetic_bit_flags = {}
        shift_counts = None
        shift_flags = None
        if instruction.op == "and":
            known_bits = left_ones & right_ones, left_zeros | right_zeros
        elif instruction.op == "or":
            known_bits = left_ones | right_ones, left_zeros & right_zeros
        elif instruction.op == "xor":
            known_bits = (left_ones & right_zeros) | (left_zeros & right_ones), (left_ones & right_ones) | (left_zeros & right_zeros)
        elif instruction.op in {"add", "sub"} and not isinstance(value, Concrete):
            known_bits, arithmetic_bit_flags = _arithmetic_known_bits(
                instruction.op, (left_ones, left_zeros), (right_ones, right_zeros), same_value=same_value, complementary=complementary,
            )
        elif instruction.op in {"asl", "asr", "lsl", "lsr"}:
            if isinstance(right, Concrete):
                shift_counts = [right.value] if 0 <= right.value <= 32 else None
            elif right_bounds is not None and 0 <= right_bounds[0] <= right_bounds[1] <= 32:
                shift_counts = [value for value in range(right_bounds[0], right_bounds[1] + 1)
                                if value & right_ones == right_ones and value & right_zeros == 0]
            if shift_counts:
                facts = []
                for shift in shift_counts:
                    input_bounds, input_bits = left_bounds, (left_ones, left_zeros)
                    if relation is not None:
                        word = shift ^ (_CLASSICAL_REGISTER_MASK if complementary else 0)
                        input_bounds, input_bits = (word, word), (word, _CLASSICAL_REGISTER_MASK ^ word)
                    facts.append(_shift_count_facts(instruction.op, input_bounds, input_bits, shift))
                bounds = min(item[0][0] for item in facts), max(item[0][1] for item in facts)
                ones, zeros = _CLASSICAL_REGISTER_MASK, _CLASSICAL_REGISTER_MASK
                for _, bits, _ in facts:
                    ones &= bits[0]
                    zeros &= bits[1]
                known_bits = ones, zeros
                shift_flags = {name: value for name, value in facts[0][2].items()
                               if all(item[2].get(name) == value for item in facts)}
        if known_bits is not None and known_bits != (0, 0):
            bounds = _refine_bounds_with_bits(bounds, known_bits)
        if bounds is not None and bounds[0] == bounds[1]:
            value = Concrete(bounds[0])
        left_origin = _operand_value_origin(left_arg, left, state)
        right_origin = _operand_value_origin(right_arg, right, state)
        arithmetic_origin = _arithmetic_value_origin(instruction.op, left_origin, right_origin,
                                                     shift_counts[0] if shift_counts and len(shift_counts) == 1 else None)
        if arithmetic_origin is not None and not isinstance(value, Concrete):
            bounds, known_bits = _origin_peer_facts(state, arithmetic_origin, bounds, known_bits)
        parts = _linear_origin_parts(arithmetic_origin)
        if parts is not None and not parts[1]:
            value = Concrete(parts[0])
            bounds = value.value, value.value
            known_bits = value.value, _CLASSICAL_REGISTER_MASK ^ value.value
        elif bounds is not None and bounds[0] == bounds[1]:
            value = Concrete(bounds[0])
        flags = (_sub_flags(0, 0) if same_value and instruction.op == "sub"
                 else _logic_flags(_CLASSICAL_REGISTER_MASK) if complementary and instruction.op == "add"
                 else _binary_alu_flags(instruction.op, left, right, value))
        if flags is None:
            flags = (_arithmetic_bounds_flags(instruction.op, left_bounds, right_bounds) or {}
                     if instruction.op in {"add", "sub"} else {})
            flags.update(arithmetic_bit_flags)
            flags.update(_result_bounds_flags((value.value, value.value) if isinstance(value, Concrete) else bounds))
            if instruction.op in {"and", "or", "xor"}:
                flags.update(cf=False, of=False)
            elif shift_flags is not None:
                flags.update(shift_flags)
        _set_alu_flags(state, instruction, flags or None)
        left_word = left.value & _CLASSICAL_REGISTER_MASK if isinstance(left, Concrete) else None
        right_word = right.value & _CLASSICAL_REGISTER_MASK if isinstance(right, Concrete) else None
        value_origin = None
        if (same_value and instruction.op in {"and", "or"}
                or right_word == 0 and instruction.op in {"add", "sub", "or", "xor", "asl", "asr", "lsl", "lsr"}
                or shift_counts == [0]
                or right_word == _CLASSICAL_REGISTER_MASK and instruction.op == "and"):
            value_origin = left_origin
        elif (left_word == 0 and instruction.op in {"add", "or", "xor"}
              or left_word == _CLASSICAL_REGISTER_MASK and instruction.op == "and"):
            value_origin = right_origin
        elif instruction.op == "xor" and right_word == _CLASSICAL_REGISTER_MASK:
            value_origin = _complement_value_origin(left_origin)
        elif instruction.op == "xor" and left_word == _CLASSICAL_REGISTER_MASK:
            value_origin = _complement_value_origin(right_origin)
        if arithmetic_origin is not None:
            value_origin = arithmetic_origin
        provenance = _binary_provenance(state, instruction, left_arg, right_arg, destination, value)
        _schedule_register_write(state, instruction, destination, value, provenance, unsigned_bounds=bounds, known_bits=known_bits, value_origin=value_origin)
    elif instruction.op in {"cmp", "test"} and len(instruction.args) >= 2:
        left_arg, right_arg = _docs_ordered_binary_args(instruction, state)
        left = resolve_arg_value(left_arg, scope)
        right = resolve_arg_value(right_arg, scope)
        relation = _operand_value_relation(left_arg, left, right_arg, right, state)
        if instruction.op == "cmp":
            flags = _comparison_flags(left_arg, left, right_arg, right, state)
        else:
            value = Concrete(0) if relation is True else _binary_classical_value("and", left, right)
            bounds = _unsigned_operand_bounds(left_arg, left, state) if relation is False else None
            if bounds is None:
                left_bounds = _unsigned_operand_bounds(left_arg, left, state)
                right_bounds = _unsigned_operand_bounds(right_arg, right, state)
                if left_bounds is not None and right_bounds is not None:
                    left_ones, left_zeros = _operand_known_bits(left_arg, left, state)
                    right_ones, right_zeros = _operand_known_bits(right_arg, right, state)
                    bounds = (left_ones & right_ones,
                              min(left_bounds[1], right_bounds[1], ~(left_zeros | right_zeros) & _CLASSICAL_REGISTER_MASK))
            flags = (_logic_flags(value.value) if isinstance(value, Concrete)
                     else {**_result_bounds_flags(bounds), "cf": False, "of": False})
        _set_alu_flags(state, instruction, flags)
    elif instruction.op in {"mulu16", "muls16", "mulu32", "muls32", "mulu32l", "mulu32h", "muls32l", "muls32h"}:
        width = 16 if instruction.op.endswith("16") else 32
        full_product = instruction.op in {"mulu32", "muls32"}
        left, right = [resolve_arg_value(arg, scope) for arg in instruction.args[:2]]
        signed = instruction.op.startswith("muls") and not instruction.op.endswith("32l")
        operand_bounds = [_multiply_operand_bounds(arg, item, state, width, signed)
                          for arg, item in zip(instruction.args, (left, right))]
        operands = [low if low == high else None for low, high in operand_bounds]
        if 0 in operands:
            product = 0
        elif all(item is not None for item in operands):
            product = operands[0] * operands[1]
        else:
            product = None
        value = (Concrete(((product >> 32) if instruction.op.endswith("h") else product) & _CLASSICAL_REGISTER_MASK)
                 if product is not None else Unknown(f"{instruction.op} result"))
        if product is None and instruction.op.endswith("32l"):
            value = left if right == Concrete(1) else right if left == Concrete(1) else value
        relation = _operand_value_relation(instruction.args[0], left, instruction.args[1], right, state)
        product_origin = (_arithmetic_value_origin(
            "multiply_low", _operand_value_origin(instruction.args[0], left, state),
            _operand_value_origin(instruction.args[1], right, state),
        ) if width == 32 and not instruction.op.endswith("h") else None)
        product_bounds = _multiply_bounds(*operand_bounds, relation, -1 if signed else (1 << width) - 1)
        low_bounds = _word_interval(product_bounds)
        high_bounds = _word_interval(tuple(endpoint >> 32 for endpoint in product_bounds))
        if instruction.op.endswith("32l"):
            signed_bounds = [_multiply_operand_bounds(arg, item, state, 32, True)
                             for arg, item in zip(instruction.args, (left, right))]
            alternate = _word_interval(_multiply_bounds(*signed_bounds, relation, -1))
            if alternate is not None:
                low_bounds = alternate if low_bounds is None else (max(low_bounds[0], alternate[0]), min(low_bounds[1], alternate[1]))
        bounds = high_bounds if instruction.op.endswith("h") else low_bounds
        if not full_product and bounds is not None and bounds[0] == bounds[1]:
            value = Concrete(bounds[0])
        known_bits = None
        if not instruction.op.endswith("h"):
            operand_bits = [_operand_known_bits(arg, item, state) for arg, item in zip(instruction.args, (left, right))]
            if width == 16:
                for index, (ones, zeros) in enumerate(operand_bits):
                    ones, zeros = ones & 0xFFFF, zeros & 0xFFFF
                    if not signed or zeros & 0x8000:
                        zeros |= 0xFFFF0000
                    elif ones & 0x8000:
                        ones |= 0xFFFF0000
                    operand_bits[index] = ones, zeros
            zero_counts = [next((bit for bit in range(32) if not zeros & (1 << bit)), 32) for _, zeros in operand_bits]
            prefix_counts = [next((bit for bit in range(32) if not (ones | zeros) & (1 << bit)), 32) for ones, zeros in operand_bits]
            prefix_mask = (1 << min(prefix_counts)) - 1
            prefix = (operand_bits[0][0] * operand_bits[1][0]) & prefix_mask
            known_bits = prefix, (prefix_mask ^ prefix) | ((1 << min(32, sum(zero_counts))) - 1)
            if relation is True:
                known_bits = known_bits[0], known_bits[1] | 1
            for index, operand in enumerate(operands):
                word = operand & _CLASSICAL_REGISTER_MASK if operand is not None else 0
                if word > 0 and word & (word - 1) == 0:
                    shift = word.bit_length() - 1
                    ones, zeros = operand_bits[1 - index]
                    known_bits = (ones << shift) & _CLASSICAL_REGISTER_MASK, ((zeros << shift) | ((1 << shift) - 1)) & _CLASSICAL_REGISTER_MASK
                    break
            bounds = _refine_bounds_with_bits(bounds, known_bits)
            if not full_product and bounds[0] == bounds[1]:
                value = Concrete(bounds[0])
        if not full_product and product_origin is not None:
            bounds, known_bits = _origin_peer_facts(state, product_origin, bounds, known_bits)
            if bounds is not None and bounds[0] == bounds[1]:
                value = Concrete(bounds[0])
        if full_product:
            flags = (_logic_flags(0) if product == 0 else {"zf": False}
                     if product is not None or product_bounds[0] > 0 or product_bounds[1] < 0 or known_bits[0] else None)
        else:
            flags = _logic_flags(value.value) if isinstance(value, Concrete) else {**_result_bounds_flags(bounds), "cf": False, "of": False}
        _set_alu_flags(state, instruction, flags)
        destinations = [_register_name(instruction.args[index], state) for index in sorted(_register_destination_argument_indexes(instruction.op))
                        if index < len(instruction.args)]
        for destination_index in sorted(_register_destination_argument_indexes(instruction.op)):
            if destination_index >= len(instruction.args):
                continue
            destination = _register_name(instruction.args[destination_index], state)
            if destination is None:
                continue
            result = value
            result_bounds = bounds
            if full_product:
                result = (Concrete((product >> (32 if destination_index == 3 else 0)) & _CLASSICAL_REGISTER_MASK)
                          if product is not None and len(set(destinations)) == len(destinations)
                          else Unknown(f"{instruction.op} result"))
                if len(set(destinations)) != len(destinations):
                    result_bounds = None
                else:
                    result_bounds = high_bounds if destination_index == 3 else bounds
                if result_bounds is not None and result_bounds[0] == result_bounds[1]:
                    result = Concrete(result_bounds[0])
            provenance = _binary_provenance(state, instruction, instruction.args[0], instruction.args[1], destination, result)
            result_bits = known_bits if not full_product or destination_index == 2 and len(set(destinations)) == len(destinations) else None
            result_origin = product_origin if not full_product or destination_index == 2 and len(set(destinations)) == len(destinations) else None
            _schedule_register_write(state, instruction, destination, result, provenance, unsigned_bounds=result_bounds,
                                     known_bits=result_bits, value_origin=result_origin)


def _execute_feedback_register_write(
    state: AnalysisState,
    instruction: Instr,
    symbols: SymbolTable,
) -> None:
    if instruction.op not in {"fb_pop_data", "fb_pull_data"} or len(instruction.args) < 2:
        return

    if instruction.op == "fb_pull_data":
        for argument_index, expression in (
            (0, "fb_pull_data fifo id"),
            (1, "fb_pull_data fifo payload"),
        ):
            destination = _register_name(instruction.args[argument_index], state)
            if destination is None:
                continue
            value = RuntimeDependent(expression)
            provenance = _register_provenance(
                state,
                destination,
                expression=expression,
                value=value,
                steps=[_provenance_step(instruction, expression, value)],
            )
            _schedule_register_write(state, instruction, destination, value, provenance)
        return

    destination = _register_name(instruction.args[1], state)
    if destination is None:
        return
    scope = _scope(symbols, state)
    channel = _feedback_channel(instruction.args[0], scope)
    expression = f"{instruction.op} channel {channel}"
    value = RuntimeDependent(expression)
    provenance = _register_provenance(
        state,
        destination,
        expression=expression,
        value=value,
        steps=[_provenance_step(instruction, expression, value)],
    )
    _schedule_register_write(state, instruction, destination, value, provenance)


def _wrap_concrete_classical_register_value(value: Value) -> Value:
    if isinstance(value, Concrete):
        return Concrete(value.value & _CLASSICAL_REGISTER_MASK)
    return value


def _move_provenance(
    state: AnalysisState,
    instruction: Instr,
    source_arg: Arg,
    destination: str,
    value: Value,
) -> dict[str, Any]:
    source_provenance = _arg_register_provenance(source_arg, state)
    expression = _arg_provenance_expression(source_arg, state)
    steps = _copied_provenance_steps(source_provenance)
    steps.append(_provenance_step(instruction, expression, value))
    return _register_provenance(
        state,
        destination,
        expression=expression,
        value=value,
        steps=steps,
    )


def _unary_provenance(
    state: AnalysisState,
    instruction: Instr,
    source_arg: Arg,
    destination: str,
    value: Value,
) -> dict[str, Any]:
    source_provenance = _arg_register_provenance(source_arg, state)
    source_expression = _arg_provenance_expression(source_arg, state)
    expression = f"{instruction.op} {source_expression}"
    steps = _copied_provenance_steps(source_provenance)
    steps.append(_provenance_step(instruction, expression, value))
    return _register_provenance(
        state,
        destination,
        expression=expression,
        value=value,
        steps=steps,
    )


def _binary_provenance(
    state: AnalysisState,
    instruction: Instr,
    left_arg: Arg,
    right_arg: Arg,
    destination: str,
    value: Value,
) -> dict[str, Any]:
    left_provenance = _arg_register_provenance(left_arg, state)
    right_provenance = _arg_register_provenance(right_arg, state)
    left_expression = _arg_provenance_expression(left_arg, state)
    right_expression = _arg_provenance_expression(right_arg, state)
    expression = _binary_provenance_expression(instruction.op, left_expression, right_expression)
    steps = _copied_provenance_steps(left_provenance)
    if right_provenance is not None:
        steps.extend(_copied_provenance_steps(right_provenance))
    steps.append(_provenance_step(instruction, expression, value))
    steps = _ordered_unique_provenance_steps(steps)
    return _register_provenance(
        state,
        destination,
        expression=expression,
        value=value,
        steps=steps,
    )


def _latched_operand_value(op: str, arg: Arg, scope: SymbolTable, state: AnalysisState) -> Value:
    value = resolve_arg_value(arg, scope)
    if state.execution_mode != "concrete" or _register_name(arg, state) is None or not isinstance(value, Concrete):
        return value
    # Q1ASM latched fields have instruction-specific signedness. Keep the raw
    # 32-bit pattern in register traces; decode only the value sent to the latch.
    # https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#latched-instructions
    if op in {"set_freq", "set_awg_gain", "set_awg_offs"}:
        return Concrete(_signed_32_bit_register_value(value.value))
    if op == "set_mrk":
        return Concrete(value.value & 0xF)
    return value


def _execute_latched(state: AnalysisState, instruction: Instr, symbols: SymbolTable) -> TimelineEvent | None:
    scope = _scope(symbols, state)
    field_name: str | None = None
    value: Any = None

    if instruction.op == "set_mrk" and instruction.args:
        field_name = "marker"
        value = _latched_operand_value(instruction.op, instruction.args[0], scope, state)
        state.latched_state.marker = value
    elif instruction.op == "set_awg_gain" and len(instruction.args) >= 2:
        field_name = "awg_gain"
        value = (
            _latched_operand_value(instruction.op, instruction.args[0], scope, state),
            _latched_operand_value(instruction.op, instruction.args[1], scope, state),
        )
        state.latched_state.awg_gain = value
    elif instruction.op == "set_awg_offs" and len(instruction.args) >= 2:
        field_name = "awg_offset"
        value = (
            _latched_operand_value(instruction.op, instruction.args[0], scope, state),
            _latched_operand_value(instruction.op, instruction.args[1], scope, state),
        )
        state.latched_state.awg_offset = value
    elif instruction.op == "set_freq" and instruction.args:
        field_name = "frequency"
        value = _latched_operand_value(instruction.op, instruction.args[0], scope, state)
        state.latched_state.frequency = value
    elif instruction.op in {"set_ph", "reset_ph"}:
        field_name = "phase"
        value = resolve_arg_value(instruction.args[0], scope) if instruction.args else Concrete(0)
        state.latched_state.phase = value
        if instruction.op == "reset_ph":
            state.pending_phase_reset = True
    elif instruction.op == "set_ph_delta" and instruction.args:
        field_name = "phase_delta"
        value = resolve_arg_value(instruction.args[0], scope)
        state.latched_state.phase_delta = value
    elif instruction.op == "set_digital" and len(instruction.args) >= 3:
        field_name = "digital"
        value = (
            resolve_arg_value(instruction.args[0], scope),
            resolve_arg_value(instruction.args[1], scope),
            resolve_arg_value(instruction.args[2], scope),
        )
        state.latched_state.digital = value
    elif instruction.op == "set_scope_en" and instruction.args:
        field_name = "scope_enable"
        value = resolve_arg_value(instruction.args[0], scope)
        state.latched_state.scope_enable = value
    elif instruction.op in {"set_cond", "set_time_ref"}:
        field_name = instruction.op
        value = [resolve_arg_value(arg, scope) for arg in instruction.args]
        if instruction.op == "set_cond":
            value = tuple(value)
            state.conditional_enable = value[0]
            state.conditional_mask = value[1]
            state.conditional_operator = value[2]
            state.conditional_enable_bounds = _unsigned_operand_bounds(instruction.args[0], value[0], state)
            state.conditional_operator_bounds = _unsigned_operand_bounds(instruction.args[2], value[2], state)
            state.conditional_enable_known_bits = _operand_known_bits(instruction.args[0], value[0], state)
            state.conditional_operator_known_bits = _operand_known_bits(instruction.args[2], value[2], state)
            state.conditional_else_duration = value[3]

    if field_name is not None:
        if field_name not in {"set_cond", "set_time_ref"}:
            state.latched_state.pending_since[field_name] = instruction.source
        event = _emit_event(
            state,
            lane="state.pending",
            kind="latched_state_pending",
            t0=state.rt_time_ns,
            t1=state.rt_time_ns,
            duration=Concrete(0),
            label=instruction.op,
            confidence=_confidence_for_value(value),
            source=instruction.source,
            meta={"field": field_name, "value": _plain_value(value)},
        )
        if field_name not in {"set_cond", "set_time_ref"}:
            state.latched_state.pending_event_ids[field_name] = event.id
        elif field_name == "set_cond":
            event.meta["conditional_enabled"] = (
                bool(state.conditional_enable.value) if isinstance(state.conditional_enable, Concrete) else None
            )
        return event
    return None


def _execute_rt_instruction(
    state: AnalysisState,
    instruction: Instr,
    symbols: SymbolTable,
    waveform_names: dict[int, str],
    acquisition_names: dict[int, str],
    q1_t0: Value,
    q1_t1: Value,
    rt_packet_id: str | None,
    q1_issue_event: TimelineEvent,
) -> None:
    spec = get_instruction_spec(instruction.op)
    scope = _scope(symbols, state)
    duration = Concrete(0)
    t0 = state.rt_time_ns
    t1: Value | None = state.rt_time_ns
    duration_arg: Arg | None = None
    duration_arg_index = rt_duration_arg_for_instruction(spec, instruction)

    if state.conditional_enable != Concrete(0):
        # Execution truth and elapsed time are independent: only the selected
        # duration matters once the predicate is known. Equal positive durations
        # can also prove time while the execution decision remains unknown.
        candidate = None
        if (spec.category == "rt" and spec.timing_effect_status == "known"
                and duration_arg_index is not None and duration_arg_index < len(instruction.args)):
            candidate = resolve_arg_value(instruction.args[duration_arg_index], scope)
        decision = _conditional_decision(
            state.conditional_enable, state.conditional_mask, state.conditional_operator,
            enable_bounds=state.conditional_enable_bounds, operator_bounds=state.conditional_operator_bounds,
            enable_known_bits=state.conditional_enable_known_bits, operator_known_bits=state.conditional_operator_known_bits,
        )
        if decision is not None and candidate is not None:
            selected_duration = candidate if decision else state.conditional_else_duration
            timing_known = isinstance(selected_duration, Concrete) and selected_duration.value >= 0
            q1_issue_event.meta.pop("conditional_execution_unknown", None)
            q1_issue_event.meta.update(conditional_timing_known=timing_known, conditional_skipped=not decision)
            if not decision:
                duration = state.conditional_else_duration
                t1 = add_values(t0, duration)
                packet = _append_packet(state, instruction, duration, q1_t0, q1_t1, t0, t1, rt_packet_id)
                meta = {"op": instruction.op, "rt_packet_id": packet.id, "conditional_skipped": True,
                        "conditional_timing_known": timing_known, "output_state_unknown": False, "applied_state": {}}
                packet.meta.update(meta)
                _emit_event(state, lane="rt.branch", kind="conditional_skip", t0=t0, t1=t1,
                            duration=duration, label=f"skip {instruction.op}", confidence=packet.confidence,
                            source=instruction.source, meta=meta)
                state.rt_time_ns = t1
                return
        else:
            duration = Unknown("conditional_execution")
            if _equal_conditional_durations(state, candidate):
                duration = candidate
                q1_issue_event.meta["conditional_timing_known"] = True
            if not isinstance(duration, Concrete):
                state.conditional_time_unknown = True
            t1 = add_values(t0, duration)
            packet = _append_packet(state, instruction, duration, q1_t0, q1_t1, t0, t1, rt_packet_id)
            packet.meta.update(conditional_execution_unknown=True, output_state_unknown=spec.applies_latched_state)
            _emit_event(
                state, lane="rt.unknown", kind="unknown_region", t0=t0, t1=t1 if isinstance(duration, Concrete) else None,
                duration=duration, label=f"conditional {instruction.op}", confidence="unknown",
                source=instruction.source,
                meta={"reason": "conditional_execution_unknown", "op": instruction.op,
                      "rt_packet_id": packet.id, "output_state_unknown": spec.applies_latched_state,
                      "conditional_timing_known": isinstance(duration, Concrete)},
            )
            state.rt_time_ns = t1
            return

    if duration_arg_index is not None and duration_arg_index < len(instruction.args):
        duration_arg = instruction.args[duration_arg_index]
        resolution = resolve_duration_arg(
            duration_arg,
            scope,
            state.diagnostics,
            source=instruction.source,
        )
        duration = resolution.value
        if resolution.requires_unknown_region:
            _emit_unknown_region(state, instruction, duration)
            t1 = add_values(t0, duration)
            state.rt_time_ns = t1
            _append_packet(state, instruction, duration, q1_t0, q1_t1, t0, t1, rt_packet_id)
            return
        t1 = add_values(t0, duration)
        state.rt_time_ns = t1

    pending_event_ids = dict(state.latched_state.pending_event_ids)
    applied_state = _consume_applied_state(state) if spec.applies_latched_state else {}
    packet = _append_packet(state, instruction, duration, q1_t0, q1_t1, t0, t1, rt_packet_id)
    event_start = len(state.events)

    if instruction.op == "wait":
        duration_provenance = (
            _duration_provenance_for_arg(state, duration_arg, duration)
            if duration_arg is not None
            else None
        )
        meta = {"rt_packet_id": packet.id}
        if duration_provenance is not None:
            meta["duration_provenance"] = duration_provenance
        _emit_event(
            state,
            lane="rt.wait",
            kind="wait",
            t0=t0,
            t1=t1,
            duration=duration,
            label=_wait_label_for_provenance(duration_provenance),
            confidence=_confidence_for_timing(duration, t0, t1),
            source=instruction.source,
            meta=meta,
        )
    elif instruction.op == "play":
        _emit_play_events(state, instruction, duration, t0, t1, waveform_names, applied_state, packet.id, scope)
    elif instruction.op.startswith("acquire"):
        _emit_acquire_event(state, instruction, duration, t0, t1, acquisition_names, applied_state, packet.id, scope)
    elif instruction.op == "upd_param":
        meta = {"rt_packet_id": packet.id}
        if applied_state:
            meta["applied_state"] = applied_state
        _emit_event(state, lane="rt.update", kind="upd_param", t0=t0, t1=t1, duration=duration, label="upd_param", confidence=_confidence_for_timing(duration, t0, t1), source=instruction.source, meta=meta)
    elif instruction.op == "upd_thres":
        meta = {"rt_packet_id": packet.id}
        if applied_state:
            meta["applied_state"] = applied_state
        _emit_event(state, lane="rt.update", kind="upd_thres", t0=t0, t1=t1, duration=duration, label="upd_thres", confidence=_confidence_for_timing(duration, t0, t1), source=instruction.source, meta=meta)
    elif instruction.op in {"wait_sync", "wait_trigger"}:
        _emit_event(state, lane="rt.sync", kind=instruction.op, t0=t0, t1=t1, duration=duration, label=instruction.op, confidence=_confidence_for_timing(duration, t0, t1), source=instruction.source, meta={"rt_packet_id": packet.id})
    elif instruction.op in {"set_latch_en", "latch_rst"}:
        _emit_event(state, lane="rt.trigger", kind=instruction.op, t0=t0, t1=t1, duration=duration, label=instruction.op, confidence=_confidence_for_timing(duration, t0, t1), source=instruction.source, meta={"rt_packet_id": packet.id})
    elif instruction.op.startswith("fb_"):
        kind, label = _feedback_rt_event_kind_and_label(instruction.op)
        meta = {"rt_packet_id": packet.id}
        feedback_acq_channel_index = _feedback_acquisition_channel_arg_index(instruction.op)
        if feedback_acq_channel_index is not None and feedback_acq_channel_index < len(instruction.args):
            channel = _feedback_channel(instruction.args[feedback_acq_channel_index], scope)
            if channel == "0":
                state.feedback_acq_channel = None
                state.feedback_acq_data_type = None
            else:
                state.feedback_acq_channel = channel
                state.feedback_acq_data_type = _feedback_acquisition_data_type(instruction.op)
        feedback = _feedback_flow_metadata(instruction, direction="send", scope=scope)
        if feedback:
            meta["feedback"] = feedback
        _emit_event(state, lane="rt.feedback", kind=kind, t0=t0, t1=t1, duration=duration, label=label, confidence=_confidence_for_timing(duration, t0, t1), source=instruction.source, meta=meta)
    else:
        _emit_event(state, lane="rt.generic", kind=instruction.op, t0=t0, t1=t1, duration=duration, label=instruction.op, confidence=_confidence_for_timing(duration, t0, t1), source=instruction.source, meta={"rt_packet_id": packet.id})

    if "marker" in applied_state:
        _emit_marker_state_event(
            state,
            instruction,
            duration=duration,
            t0=t0,
            t1=t1,
            packet_id=packet.id,
            value=applied_state["marker"],
        )
    for event in state.events[event_start:]:
        if state.conditional_time_unknown:
            event.meta["output_state_unknown"] = spec.applies_latched_state
        if "applied_state" in event.meta:
            event.meta["applied_setter_event_ids"] = {
                field: pending_event_ids[field] for field in applied_state if field in pending_event_ids
            }


def _emit_play_events(
    state: AnalysisState,
    instruction: Instr,
    duration: Value,
    t0: Value,
    t1: Value | None,
    waveform_names: dict[int, str],
    applied_state: dict[str, Any],
    packet_id: str,
    scope: SymbolTable,
) -> None:
    for path, arg_index in enumerate((0, 1)):
        if arg_index >= len(instruction.args):
            continue
        arg = instruction.args[arg_index]
        waveform_index = _arg_int(arg, scope)
        if waveform_index is None:
            _diagnose_unresolved_index_operand(state, instruction, arg, arg_index, "waveform_index")
        if waveform_index is not None and waveform_index < 0:
            continue
        meta = {"waveform_index": waveform_index, "path": path, "rt_packet_id": packet_id}
        if applied_state:
            meta["applied_state"] = applied_state
        _emit_event(
            state,
            lane=f"rt.path{path}",
            kind="play",
            t0=t0,
            t1=t1,
            duration=duration,
            label=waveform_names.get(waveform_index, f"wf#{waveform_index}") if waveform_index is not None else "wf#?",
            confidence=_confidence_for_timing(duration, t0, t1),
            source=instruction.source,
            meta=meta,
        )


def _emit_acquire_event(
    state: AnalysisState,
    instruction: Instr,
    duration: Value,
    t0: Value,
    t1: Value | None,
    acquisition_names: dict[int, str],
    applied_state: dict[str, Any],
    packet_id: str,
    scope: SymbolTable,
) -> None:
    acquisition_arg = instruction.args[0] if instruction.args else None
    bin_arg = instruction.args[1] if len(instruction.args) > 1 else None
    acquisition_index = _arg_int(acquisition_arg, scope) if acquisition_arg is not None else None
    bin_index = _arg_int(bin_arg, scope) if bin_arg is not None else None
    has_unresolved_index = False
    if acquisition_index is None and acquisition_arg is not None:
        has_unresolved_index = _diagnose_unresolved_index_operand(
            state,
            instruction,
            acquisition_arg,
            0,
            "acquisition_index",
        )
    if bin_index is None and bin_arg is not None:
        has_unresolved_index = _diagnose_unresolved_index_operand(
            state,
            instruction,
            bin_arg,
            1,
            "bin_index",
        ) or has_unresolved_index
    if has_unresolved_index:
        return
    label = acquisition_names.get(acquisition_index, f"acq#{acquisition_index}")
    meta: dict[str, Any] = {
        "acquisition_index": acquisition_index,
        "bin_index": bin_index,
        "rt_packet_id": packet_id,
    }
    if state.feedback_acq_channel is not None:
        feedback = {
            "direction": "send",
            "channel": state.feedback_acq_channel,
            "source": _feedback_acquisition_source(label, bin_index),
        }
        if state.feedback_acq_data_type is not None:
            feedback["data_type"] = state.feedback_acq_data_type
        if instruction.op == "acquire_timetags":
            enabled = _arg_int(instruction.args[2], scope)
            streaming = state.feedback_acq_data_type in {"high_resolution_time_tags", "low_latency_time_tags"}
            if streaming and enabled == 1:
                feedback["send_time_unknown"] = True
                meta["feedback"] = feedback
            elif not streaming and enabled == 0:
                if _arg_int(instruction.args[3], scope) == 0:
                    feedback["send_on"] = "command_start"
                else:
                    feedback["send_time_unknown"] = True
                meta["feedback"] = feedback
        else:
            meta["feedback"] = feedback
    if applied_state:
        meta["applied_state"] = applied_state
    _emit_event(
        state,
        lane="rt.acquire",
        kind="acquire",
        t0=t0,
        t1=t1,
        duration=duration,
        label=label,
        confidence=_confidence_for_timing(duration, t0, t1),
        source=instruction.source,
        meta=meta,
    )


def _diagnose_unresolved_index_operand(
    state: AnalysisState,
    instruction: Instr,
    arg: Arg,
    operand_index: int,
    operand_role: str,
) -> bool:
    if arg.kind not in {"symbol", "placeholder"}:
        return False
    symbol = _arg_symbol_lookup_names(arg)[0]
    diagnostic = Diagnostic(
        severity="warning",
        category="unresolved_symbol",
        message=f"{operand_role.replace('_', ' ')} must resolve to a concrete integer: {arg.raw}",
        source=instruction.source,
        details={
            "symbol": symbol,
            "operand": arg.raw,
            "operand_index": operand_index,
            "operand_role": operand_role,
            "op": instruction.op,
        },
    )
    for index, existing in enumerate(state.diagnostics):
        if (existing.category == "unresolved_symbol" and existing.severity == "warning"
                and existing.source == instruction.source and existing.details == {"symbol": symbol}):
            state.diagnostics[index] = diagnostic
            return True
    if diagnostic not in state.diagnostics:
        state.diagnostics.append(diagnostic)
    return True


def _emit_marker_state_event(
    state: AnalysisState,
    instruction: Instr,
    *,
    duration: Value,
    t0: Value,
    t1: Value | None,
    packet_id: str,
    value: Any,
) -> None:
    _emit_event(
        state,
        lane="rt.marker",
        kind="marker_state",
        t0=t0,
        t1=t1,
        duration=duration,
        label=f"marker {value}",
        confidence=_confidence_for_value((state.latched_state.marker, t0, t1)),
        source=instruction.source,
        meta={"field": "marker", "value": value, "rt_packet_id": packet_id},
    )


def _emit_feedback_annotation(
    state: AnalysisState,
    instruction: Instr,
    q1_issue_event: TimelineEvent,
    symbols: SymbolTable,
) -> None:
    annotation = FEEDBACK_ANNOTATIONS.get(instruction.op)
    if annotation is None:
        return
    kind, label = annotation
    meta: dict[str, Any] = {
        "op": instruction.op,
        "q1_issue_event_id": q1_issue_event.id,
    }
    if state.conditional_time_unknown:
        meta["output_state_unknown"] = False
    feedback = _feedback_flow_metadata(instruction, direction="receive", scope=_scope(symbols, state))
    if feedback:
        meta["feedback"] = feedback
    if instruction.args:
        meta["feedback_arg"] = instruction.args[0].raw
    _emit_event(
        state,
        lane="rt.feedback",
        kind=kind,
        t0=state.rt_time_ns,
        t1=state.rt_time_ns,
        duration=Concrete(0),
        label=label,
        confidence=_confidence_for_timing(Concrete(0), state.rt_time_ns, state.rt_time_ns),
        source=instruction.source,
        meta=meta,
    )


def _emit_unknown_region(state: AnalysisState, instruction: Instr, duration: Value) -> None:
    _emit_event(
        state,
        lane="rt.unknown",
        kind="unknown_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=duration,
        label=str(duration),
        confidence="unknown",
        source=instruction.source,
    )


def _append_packet(
    state: AnalysisState,
    instruction: Instr,
    duration: Value,
    q1_t0: Value,
    q1_t1: Value,
    rt_t0: Value | None,
    rt_t1: Value | None,
    packet_id: str | None,
) -> RtPacket:
    packet = RtPacket(
        id=packet_id or f"{state.sequencer_id}:p{len(state.rt_packets)}",
        sequencer_id=state.sequencer_id,
        source=instruction.source,
        op=instruction.op,
        duration=duration,
        q1_issue_t0=q1_t0,
        q1_issue_t1=q1_t1,
        rt_t0=rt_t0,
        rt_t1=rt_t1,
        confidence=_confidence_for_timing(duration, rt_t0, rt_t1),
        meta={"conditional_time_unknown": True,
              "output_state_unknown": get_instruction_spec(instruction.op).applies_latched_state}
        if state.conditional_time_unknown else {},
    )
    state.rt_packets.append(packet)
    return packet


def _emit_q1_issue(
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
    if rt_packet_id is not None:
        meta["rt_packet_id"] = rt_packet_id
    return _emit_event(
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


def _emit_event(
    state: AnalysisState,
    *,
    lane: str,
    kind: str,
    t0: Value,
    t1: Value | None,
    duration: Value,
    label: str,
    confidence: Confidence,
    source: SourceLocation,
    meta: dict[str, Any] | None = None,
) -> TimelineEvent:
    event_meta = dict(meta or {})
    if state.conditional_time_unknown and kind != "q1_issue":
        event_meta["conditional_time_unknown"] = True
    if state.current_resolved_args and "resolved_args" not in event_meta:
        event_meta["resolved_args"] = state.current_resolved_args
    event = TimelineEvent(
        id=f"{state.sequencer_id}:e{len(state.events)}",
        sequencer_id=state.sequencer_id,
        lane=lane,
        kind=kind,
        t0=t0,
        t1=t1,
        duration=duration,
        label=label,
        confidence=confidence,
        source=source,
        meta=event_meta,
    )
    state.events.append(event)
    return event


def _loop_preview_events(state: AnalysisState, start_line: int, end_line: int) -> list[TimelineEvent]:
    return [
        event
        for event in state.events
        if start_line <= event.source.line < end_line
        and event.kind not in {"loop_block", "loop_iteration_preview"}
    ]


def _add_loop_preview_membership(event: TimelineEvent, loop_id: str) -> None:
    existing_previews = event.meta.get("loop_previews")
    if isinstance(existing_previews, list):
        loop_previews = list(existing_previews)
    else:
        loop_previews = []
        existing_preview = event.meta.get("loop_preview")
        if isinstance(existing_preview, str):
            loop_previews.append(existing_preview)
    if loop_id not in loop_previews:
        loop_previews.append(loop_id)
    event.meta["loop_previews"] = loop_previews
    event.meta.setdefault("loop_preview", loop_id)


def _loop_timing(
    events: list[TimelineEvent],
    *,
    current_rt_time: Value | None = None,
) -> tuple[Value, Value, Value]:
    rt_events = [
        event
        for event in events
        if event.kind != "q1_issue" and (event.t1 is not None or event.kind == "unknown_region")
    ]
    if not rt_events:
        if current_rt_time is not None:
            return current_rt_time, current_rt_time, Concrete(0)
        return Concrete(0), Concrete(0), Concrete(0)

    concrete_t0 = [event.t0.value for event in rt_events if isinstance(event.t0, Concrete)]
    concrete_t1 = [event.t1.value for event in rt_events if isinstance(event.t1, Concrete)]
    if len(concrete_t0) == len(rt_events) and len(concrete_t1) == len(rt_events):
        t0 = Concrete(min(concrete_t0))
        t1 = Concrete(max(concrete_t1))
        return _loop_timing_with_current_end(t0, t1, current_rt_time)

    first = rt_events[0]
    last = rt_events[-1]
    t1 = last.t1 if last.t1 is not None else first.t0
    return _loop_timing_with_current_end(first.t0, t1, current_rt_time)


def _loop_timing_with_current_end(
    t0: Value,
    event_t1: Value,
    current_rt_time: Value | None,
) -> tuple[Value, Value, Value]:
    if current_rt_time is not None:
        duration = subtract_values(current_rt_time, t0)
        if not isinstance(duration, Concrete) or duration.value >= 0:
            return t0, current_rt_time, duration
    return t0, event_t1, subtract_values(event_t1, t0)


def _loop_q1_period(preview_events: list[TimelineEvent], q1_issue_event: TimelineEvent) -> Value:
    q1_events = [event for event in preview_events if event.kind == "q1_issue"]
    q1_start = q1_events[0].t0 if q1_events else q1_issue_event.t0
    q1_end = q1_issue_event.t1 if q1_issue_event.t1 is not None else q1_issue_event.t0
    return subtract_values(q1_end, q1_start)


def _loop_q1_body_period(q1_period: Value, loop_edge_runtime_ns: int) -> Value:
    return subtract_values(q1_period, Concrete(loop_edge_runtime_ns))


def _advance_compact_loop_fallthrough_state(
    state: AnalysisState,
    *,
    loop_id: str,
    counter_reg: str | None,
    count: Value | None,
    rt_period: Value,
    q1_body_period: Value,
    loop_q1_jump_runtime_ns: int,
    loop_q1_continue_runtime_ns: int,
    rt_total: Concrete | None = None,
) -> None:
    if not isinstance(count, Concrete):
        state.rt_time_ns = Unknown(f"compact loop {loop_id} iteration count is not concrete")
        state.q1_time_ns = Unknown(f"compact loop {loop_id} iteration count is not concrete")
        if counter_reg is not None:
            state.registers[counter_reg] = Unknown(f"{counter_reg} after compact loop {loop_id}")
        return

    hidden_iterations = max(count.value - 1, 0)
    if isinstance(rt_period, Concrete):
        hidden = subtract_values(rt_total, rt_period) if rt_total is not None else multiply_value(rt_period, hidden_iterations)
        state.rt_time_ns = add_values(state.rt_time_ns, hidden)
    else:
        state.rt_time_ns = Unknown(f"compact loop {loop_id} RT period is not concrete")
    if isinstance(q1_body_period, Concrete):
        if count.value <= 1:
            q1_hidden = Concrete(0)
        else:
            taken_hidden_iterations = max(count.value - 2, 0)
            taken_period = add_values(q1_body_period, Concrete(loop_q1_jump_runtime_ns))
            final_period = add_values(q1_body_period, Concrete(loop_q1_continue_runtime_ns))
            q1_hidden = add_values(multiply_value(taken_period, taken_hidden_iterations), final_period)
        state.q1_time_ns = add_values(state.q1_time_ns, q1_hidden)
    else:
        state.q1_time_ns = Unknown(f"compact loop {loop_id} Q1 period is not concrete")
    if counter_reg is not None:
        state.registers[counter_reg] = Concrete(0)


def _q1_cost(events: list[TimelineEvent]) -> Value:
    total: Value = Concrete(0)
    for event in events:
        if event.kind == "q1_issue":
            total = add_values(total, event.duration)
    return total


def _display_count(count: Value | None) -> Any:
    if isinstance(count, Concrete):
        return count.value
    if count is None:
        return "unknown"
    return str(count)


def _counter_after(count: Value) -> Any:
    if isinstance(count, Concrete):
        return count.value - 1
    return None


def _source_to_json(source: SourceLocation) -> dict[str, Any]:
    return {
        "file": source.file,
        "line": source.line,
        "column": source.column,
        "raw": source.raw,
    }


def _consume_applied_state(state: AnalysisState) -> dict[str, Any]:
    applied: dict[str, Any] = {}
    pending = state.latched_state.pending_since
    if "marker" in pending and state.latched_state.marker is not None:
        applied["marker"] = _plain_value(state.latched_state.marker)
    if "awg_gain" in pending and state.latched_state.awg_gain is not None:
        applied["awg_gain"] = [_plain_value(value) for value in state.latched_state.awg_gain]
    if "awg_offset" in pending and state.latched_state.awg_offset is not None:
        applied["awg_offset"] = [_plain_value(value) for value in state.latched_state.awg_offset]
    if "frequency" in pending and state.latched_state.frequency is not None:
        applied["frequency"] = _plain_value(state.latched_state.frequency)
    if "phase" in pending and state.latched_state.phase is not None:
        applied["phase"] = _plain_value(state.latched_state.phase)
    if "phase_delta" in pending and state.latched_state.phase_delta is not None:
        applied["phase_delta"] = _plain_value(state.latched_state.phase_delta)
    if "digital" in pending and state.latched_state.digital is not None:
        applied["digital"] = [_plain_value(value) for value in state.latched_state.digital]
    if "scope_enable" in pending and state.latched_state.scope_enable is not None:
        applied["scope_enable"] = _plain_value(state.latched_state.scope_enable)
    pending.clear()
    state.latched_state.pending_event_ids.clear()
    state.pending_phase_reset = False
    return applied


def _scope(symbols: SymbolTable, state: AnalysisState) -> SymbolTable:
    label_symbols = {f"@{label}": Concrete(pc) for label, pc in state.labels.items()}
    scope = {**symbols, **label_symbols, **state.registers}
    for name, value in symbols.items():
        if not name.startswith("$"):
            scope.setdefault(f"${name}", value)
    if state.execution_mode == "concrete":
        for name in symbols:
            if _is_valid_register_arg(Arg("reg", name, name)):
                scope[name] = state.registers.get(name, Unknown(name))
    for alias, register in state.register_aliases.items():
        value = scope.get(register, Unknown(register))
        scope[f"${alias}"] = value
        if not _is_valid_register_arg(Arg("reg", alias, alias)):
            scope[alias] = value
    return scope


def _register_name(arg: Arg, state: AnalysisState) -> str | None:
    if arg.kind == "reg":
        return str(arg.value).upper()
    if arg.kind != "symbol":
        return None
    name = str(arg.value)
    if not name.startswith("$") or len(name) <= 1:
        return None
    return state.register_aliases.get(name[1:])


def _arg_register_references(arg: Arg, state: AnalysisState) -> set[str]:
    references = {_register_name(arg, state)}
    for name in _arg_symbol_lookup_names(arg):
        if _is_valid_register_arg(Arg("reg", name.upper(), name)):
            references.add(name.upper())
        references.add(state.register_aliases.get(name.lstrip("$")))
    return references - {None}


def _arg_register_provenance(arg: Arg, state: AnalysisState) -> dict[str, Any] | None:
    register = _register_name(arg, state)
    if register is None:
        return None
    if state.execution_mode == "concrete":
        return {"register": register, "expression": register, "steps": []}
    return state.register_provenance.get(register)


def _arg_provenance_expression(arg: Arg, state: AnalysisState) -> str:
    provenance = _arg_register_provenance(arg, state)
    if provenance is not None:
        expression = provenance.get("expression")
        if isinstance(expression, str) and expression:
            return expression
    if arg.kind in {"symbol", "placeholder"}:
        name = str(arg.value)
        return name[1:] if name.startswith("$") and len(name) > 1 else name
    return arg.raw


def _register_provenance(
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


def _copied_provenance_steps(provenance: dict[str, Any] | None) -> list[dict[str, Any]]:
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


def _provenance_step(instruction: Instr, expression: str, value: Value) -> dict[str, Any]:
    return {
        "op": instruction.op,
        "source": _source_to_json(instruction.source),
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


def _duration_provenance_for_arg(
    state: AnalysisState,
    arg: Arg,
    value: Value,
) -> dict[str, Any] | None:
    provenance = _arg_register_provenance(arg, state)
    if provenance is None:
        return None
    result = dict(provenance)
    result["steps"] = _copied_provenance_steps(provenance)
    result["role"] = _duration_provenance_role(result)
    result["value"] = value_to_json(value)
    return result


def _duration_provenance_role(provenance: dict[str, Any]) -> str:
    symbol = str(provenance.get("symbol", "")).upper()
    if symbol == "POST_WAIT":
        return "post_wait"
    if symbol == "WAIT_FOR_MULTICAST":
        return "multicast_wait"
    return "derived_wait"


def _wait_label_for_provenance(provenance: dict[str, Any] | None) -> str:
    if provenance is None:
        return "wait"
    role = provenance.get("role")
    if role == "post_wait":
        return "post wait"
    if role == "multicast_wait":
        return "multicast wait"
    return "derived wait"


def _unary_classical_value(op: str, value: Value) -> Value:
    if isinstance(value, Concrete):
        if op == "not":
            return Concrete((~value.value) & 0xFFFFFFFF)
    return Symbolic(f"{op} {_value_expr(value)}")


def _binary_classical_value(op: str, left: Value, right: Value) -> Value:
    if op == "and" and any(isinstance(value, Concrete) and value.value & _CLASSICAL_REGISTER_MASK == 0 for value in (left, right)):
        return Concrete(0)
    if op == "or" and any(isinstance(value, Concrete) and value.value & _CLASSICAL_REGISTER_MASK == _CLASSICAL_REGISTER_MASK for value in (left, right)):
        return Concrete(_CLASSICAL_REGISTER_MASK)
    if op == "and":
        if isinstance(left, Concrete) and left.value & _CLASSICAL_REGISTER_MASK == _CLASSICAL_REGISTER_MASK:
            return right
        if isinstance(right, Concrete) and right.value & _CLASSICAL_REGISTER_MASK == _CLASSICAL_REGISTER_MASK:
            return left
    if op in {"or", "xor"}:
        if left == Concrete(0):
            return right
        if right == Concrete(0):
            return left
    if op in {"asl", "asr", "lsl", "lsr"} and right == Concrete(0):
        return left
    if op == "lsr" and right == Concrete(32):
        return Concrete(0)
    if isinstance(left, Concrete) and isinstance(right, Concrete):
        if op in {"asl", "asr", "lsl", "lsr"}:
            return _shift_classical_value(op, left.value, right.value)
        operations = {
            "and": lambda a, b: a & b,
            "or": lambda a, b: a | b,
            "xor": lambda a, b: a ^ b,
        }
        return Concrete(operations[op](left.value, right.value))
    return Symbolic(f"{_value_expr(left)} {op} {_value_expr(right)}")


def _shift_classical_value(op: str, left: int, shift: int) -> Value:
    if shift < 0 or shift > 32:
        return Unknown(f"{op} shift count outside concrete emulation range: {shift}")
    if op in {"asl", "lsl"}:
        return Concrete((left << shift) & 0xFFFFFFFF)
    if op == "asr":
        return Concrete(_signed_32_bit_register_value(left) >> shift)
    return Concrete((left & 0xFFFFFFFF) >> shift)


def _signed_32_bit_register_value(value: int) -> int:
    value &= _CLASSICAL_REGISTER_MASK
    if value & 0x80000000:
        return value - 0x100000000
    return value


def _feedback_rt_event_kind_and_label(op: str) -> tuple[str, str]:
    if op in {"fb_com_data", "fb_cmd"}:
        return "feedback_com", "feedback commit"
    return op, op


def _feedback_acquisition_channel_arg_index(op: str) -> int | None:
    if op in {"fb_llp_tags_id", "fb_llp_ttls_id", "fb_tdc_tags_id", "fb_tdc_tdelta_id"}:
        return 0
    for argument_index, operand_role in _rt_index_operand_roles(op):
        if operand_role == "feedback_acquisition_channel":
            return argument_index
    return None


def _feedback_acquisition_data_type(op: str) -> str | None:
    if op == "fb_acq_iq_id":
        return "iq_values"
    if op in {"fb_acq_tb_id", "fb_acq_tb_valid"}:
        return "thresholded_bits"
    if op == "fb_llp_tags_id":
        return "low_latency_time_tags"
    if op == "fb_llp_ttls_id":
        return "ttl_counts"
    if op == "fb_tdc_tags_id":
        return "high_resolution_time_tags"
    if op == "fb_tdc_tdelta_id":
        return "time_delta"
    return None


def _feedback_flow_metadata(instruction: Instr, *, direction: str, scope: SymbolTable) -> dict[str, str] | None:
    if instruction.op in {"fb_com_data", "fb_cmd"}:
        return {
            "direction": direction,
            "channel": _feedback_channel(instruction.args[0], scope) if len(instruction.args) > 0 else "default",
            "source": instruction.args[1].raw if len(instruction.args) > 1 else "unknown",
            "data_type": "q1_register_or_immediate",
        }
    if instruction.op == "fb_pop_data":
        return {
            "direction": direction,
            "channel": _feedback_channel(instruction.args[0], scope) if len(instruction.args) > 0 else "default",
            "target": instruction.args[1].raw if len(instruction.args) > 1 else "unknown",
        }
    if instruction.op == "fb_pull_data":
        return {
            "direction": direction,
            "receive_mode": "fifo",
            "id_target": instruction.args[0].raw if len(instruction.args) > 0 else "unknown",
            "target": instruction.args[1].raw if len(instruction.args) > 1 else "unknown",
        }
    return None


def _feedback_channel(arg: Arg, scope: SymbolTable) -> str:
    value = resolve_arg_value(arg, scope)
    if isinstance(value, Concrete):
        return str(value.value)
    return arg.raw


def _feedback_acquisition_source(label: str, bin_index: int | None) -> str:
    if bin_index is None:
        return label
    return f"{label}/bin{bin_index}"


def _value_expr(value: Value) -> str:
    if isinstance(value, Concrete):
        return str(value.value)
    if isinstance(value, Symbolic):
        return value.expr
    if isinstance(value, Unknown):
        return value.reason
    return str(value)


def _arg_int(arg: Arg, scope: SymbolTable | None = None) -> int | None:
    if arg.kind == "imm":
        return int(arg.value)
    if scope is None:
        return None
    value = resolve_arg_value(arg, scope)
    if isinstance(value, Concrete):
        return value.value
    return None


def _confidence_for_value(value: Any) -> Confidence:
    if isinstance(value, tuple):
        confidences = {_confidence_for_value(item) for item in value}
        if "unknown" in confidences:
            return "unknown"
        if "runtime_dependent" in confidences:
            return "runtime_dependent"
        if "symbolic" in confidences:
            return "symbolic"
        return "exact"
    if isinstance(value, Concrete):
        return "exact"
    if isinstance(value, Unknown):
        return "unknown"
    if value.__class__.__name__ == "RuntimeDependent":
        return "runtime_dependent"
    if value.__class__.__name__ == "Symbolic":
        return "symbolic"
    return "unknown"


def _confidence_for_timing(duration: Value, t0: Value | None, t1: Value | None) -> Confidence:
    return _confidence_for_value((t0, t1, duration))


def _plain_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_plain_value(item) for item in value]
    if isinstance(value, Concrete):
        return value.value
    if isinstance(value, Value):
        return value_to_json(value)
    return value
