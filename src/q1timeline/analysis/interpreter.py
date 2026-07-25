from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from q1timeline.analysis.values import (
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
_WRAPPING_CLASSICAL_OPS = {"add", "sub", "and", "or", "xor", "asl", "asr", "lsl", "lsr"}


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
class PendingRegisterWrite:
    register: str
    value: Value
    provenance: dict[str, Any]
    source: SourceLocation
    available_issue_index: int


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


@dataclass
class AnalysisState:
    sequencer_id: str
    labels: dict[str, int] = field(default_factory=dict)
    instructions_by_pc: dict[int, Instr] = field(default_factory=dict)
    register_aliases: dict[str, str] = field(default_factory=dict)
    pc: int = 0
    registers: SymbolTable = field(default_factory=dict)
    latched_state: LatchedState = field(default_factory=LatchedState)
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
) -> AnalysisState:
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
    )
    state.diagnostics.extend(program.diagnostics)
    if strict_q1asm:
        _diagnose_strict_q1asm_placeholder_defs(state, program.defs)

    raw_params = params or {}
    symbols = symbol_table_from_params(raw_params)
    value_defs = {name: arg for name, arg in program.defs.items() if arg.kind != "reg"}
    resolved_defs = resolve_def_values(value_defs, symbols, state.diagnostics)
    state.symbol_origins = _symbol_origins(raw_params, symbols, value_defs, resolved_defs)
    symbols.update(resolved_defs)

    state.pc = 0
    _run_program_loop(state, symbols, waveform_names or {}, acquisition_names or {})
    _flush_pending_register_writes(state)
    _diagnose_static_register_read_hazards(state, program)
    return state


def _run_program_loop(
    state: AnalysisState,
    symbols: SymbolTable,
    waveform_names: dict[int, str],
    acquisition_names: dict[int, str],
) -> None:
    while state.pc < len(state.instructions_by_pc):
        if state.stopped:
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


def _execute_instruction(
    state: AnalysisState,
    instruction: Instr,
    symbols: SymbolTable,
    waveform_names: dict[int, str],
    acquisition_names: dict[int, str],
) -> None:
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
    if not has_raw_invalid_arguments:
        _diagnose_resolved_argument_types(state, instruction, spec, symbols)
    _diagnose_unresolved_register_operands(state, instruction, spec)
    _diagnose_register_read_hazards(state, instruction)
    has_invalid_arguments = any(
        diagnostic.category in {"invalid_argument_count", "invalid_argument_type"}
        for diagnostic in state.diagnostics[diagnostic_start:]
    ) or has_invalid_register_operands or has_invalid_bare_def_operands or has_unresolved_dollar_symbols or has_unresolved_strict_symbols
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

    q1_t0 = state.q1_time_ns
    q1_duration = Concrete(spec.q1_time_model(_instruction_for_q1_timing(instruction, state, symbols)))
    q1_t1 = add_values(q1_t0, q1_duration)
    state.q1_time_ns = q1_t1

    rt_packet_id: str | None = None
    if has_invalid_arguments:
        _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id)
        return

    _diagnose_analysis_incomplete_timing_effect(state, instruction, spec)

    if spec.category == "unknown":
        _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id)
        return

    if spec.category == "classical":
        _execute_classical(state, instruction, symbols)
        _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id)
        return

    if spec.category == "latched":
        _execute_latched(state, instruction, symbols)
        _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id)
        return

    if spec.category == "feedback":
        _execute_feedback_register_write(state, instruction, symbols)
        q1_issue_event = _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id)
        _emit_feedback_annotation(state, instruction, q1_issue_event, symbols)
        return

    if instruction.op in {"stop", "illegal"}:
        _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id)
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
            confidence="exact",
            source=instruction.source,
        )
        state.stopped = True
        return

    if spec.emits_rt_packet:
        rt_packet_id = f"{state.sequencer_id}:p{len(state.rt_packets)}"
    q1_issue_event = _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id)

    if spec.category == "branch":
        _execute_branch(state, instruction, q1_issue_event, symbols, waveform_names, acquisition_names)
        return

    if spec.category in {"rt", "sync"}:
        _execute_rt_instruction(
            state,
            instruction,
            symbols,
            waveform_names,
            acquisition_names,
            q1_t0,
            q1_t1,
            rt_packet_id,
        )


def _diagnose_analysis_incomplete_timing_effect(
    state: AnalysisState,
    instruction: Instr,
    spec: InstructionSpec,
) -> None:
    if spec.timing_effect_status != "analysis_incomplete":
        return
    state.diagnostics.append(
        Diagnostic(
            severity="warning",
            category="analysis_incomplete",
            message=f"Timing effect is unknown for instruction: {instruction.op}",
            source=instruction.source,
            details={"op": instruction.op},
        )
    )


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


def _diagnose_static_register_read_hazards(state: AnalysisState, program: Program) -> None:
    previous_instruction: Instr | None = None
    previous_writes: set[str] = set()

    for instruction in program.instructions:
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


def _register_read_operands(instruction: Instr, state: AnalysisState) -> list[tuple[int, str]]:
    destination_indexes = _register_destination_argument_indexes(instruction.op)
    reads: list[tuple[int, str]] = []
    for index, arg in enumerate(instruction.args):
        if index in destination_indexes:
            continue
        register = _register_name(arg, state)
        if register is not None:
            reads.append((index, register))
    return reads


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


def _flush_pending_register_writes(state: AnalysisState) -> None:
    if not state.pending_register_writes:
        return
    for pending in sorted(state.pending_register_writes, key=lambda item: item.available_issue_index):
        _apply_register_write(state, pending)
    state.pending_register_writes = []


def _apply_register_write(state: AnalysisState, pending: PendingRegisterWrite) -> None:
    state.registers[pending.register] = pending.value
    state.register_provenance[pending.register] = pending.provenance


def _schedule_register_write(
    state: AnalysisState,
    instruction: Instr,
    register: str,
    value: Value,
    provenance: dict[str, Any],
) -> None:
    state.pending_register_writes.append(
        PendingRegisterWrite(
            register=register,
            value=value,
            provenance=provenance,
            source=instruction.source,
            available_issue_index=state.q1_issue_index + 2,
        )
    )


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
    if _is_packed_feedback_register_form(instruction):
        return False
    emitted = False
    scope = _scope(symbols, state)
    for argument_index, operand_role, lower, upper in _official_operand_ranges(instruction.op):
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
        if value.value < lower and _range_lower_bound_is_validated_elsewhere(instruction.op, operand_role):
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


def _is_packed_feedback_register_form(instruction: Instr) -> bool:
    return (
        instruction.op in {"fb_com_cfg", "fb_acq_tb_cfg", "fb_com_extra", "fb_acq_tb_extra", "fb_acq_tb_mock"}
        and len(instruction.args) == 2
        and instruction.args[0].kind == "reg"
    )


def _range_lower_bound_is_validated_elsewhere(op: str, operand_role: str) -> bool:
    return (op, operand_role) in {
        ("set_cond", "else_duration"),
        ("fb_com_cfg", "bit_position"),
        ("fb_com_cfg", "payload_length"),
        ("fb_acq_tb_cfg", "bit_position"),
        ("fb_acq_tb_cfg", "payload_length"),
    }


def _official_operand_ranges(op: str) -> list[tuple[int, str, int, int]]:
    signed_16 = (-32768, 32767)
    signed_32 = (-2147483648, 2147483647)
    classical_imm = (-2147483648, 0xFFFFFFFF)
    acquisition_idx = (0, 31)
    bin_idx = (0, 16777215)
    feedback_idx = (0, 255)
    if op == "stop":
        return [(0, "stop_status", *signed_32)]
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
        return [(0, "feedback_pop_tag", 0, 255)]
    if op in {"fb_com_data", "fb_cmd"}:
        return [(0, "feedback_channel", *feedback_idx)]
    if op in {"fb_com_cfg", "fb_acq_tb_cfg"}:
        return [(0, "write_combine_flag", 0, 1), (1, "bit_position", 0, 511), (2, "payload_length", 0, 63)]
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
    if op == "jmp":
        return [(0, "branch_target", 0, 16383)]
    if op in {"jge", "jlt"}:
        return [
            (0, "branch_target", 0, 16383),
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
    if op in {"mulu32l", "mulu32h"}:
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
    scope = _scope(symbols, state)
    if arg.kind == "reg":
        return _resolved_branch_target_arg(arg, resolve_arg_value(arg, scope)) or arg
    register = _register_name(arg, state)
    if register is not None:
        register_arg = Arg(kind="reg", value=register, raw=arg.raw)
        return _resolved_branch_target_arg(register_arg, resolve_arg_value(register_arg, scope)) or register_arg
    value = resolve_arg_value(arg, scope)
    return _resolved_branch_target_arg(arg, value) or arg


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
        _execute_status_branch(state, instruction, q1_issue_event, symbols)
    elif instruction.op in {"jge", "jlt"} and len(instruction.args) >= 3:
        _execute_conditional_branch(state, instruction, q1_issue_event, symbols, waveform_names, acquisition_names)
    elif instruction.op in STATUS_BRANCH_OPS and instruction.args:
        _execute_status_branch(state, instruction, q1_issue_event, symbols)


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
    op = ">=" if instruction.op == "jge" else "<"
    decision = compare_values(left, op, right)
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
    base_path_state = deepcopy(state)
    comparison_packets: list[RtPacket] = []
    comparison_events: list[TimelineEvent] = []
    comparison_diagnostics: list[Diagnostic] = []
    path_specs = (("taken", taken_pc), ("fallthrough", instruction.pc + 1))
    for path, next_pc in path_specs:
        path_state = deepcopy(base_path_state)
        path_state.pc = next_pc
        path_state.next_pc = next_pc
        path_state.stopped = False
        path_state.q1_issue_index = state.q1_issue_index + 1
        path_state.branch_explore_depth = state.branch_explore_depth + 1
        _run_program_loop(path_state, symbols, waveform_names, acquisition_names)
        _flush_pending_register_writes(path_state)
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
            )
        )
    state.rt_packets.extend(comparison_packets)
    state.events.extend(comparison_events)
    state.diagnostics.extend(comparison_diagnostics)
    state.stopped = True
    return True


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
    for event in events:
        meta = dict(event.meta)
        meta["branch_comparison_branch_id"] = branch_id
        meta["branch_comparison_path"] = path
        if isinstance(meta.get("rt_packet_id"), str):
            meta["rt_packet_id"] = _branch_comparison_packet_id(str(meta["rt_packet_id"]), path)
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
                related_events=list(diagnostic.related_events),
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
    state.diagnostics.append(
        Diagnostic(
            severity="error",
            category="undefined_label",
            message=f"Undefined label: @{target_label}",
            source=instruction.source,
            details={"label": target_label, "op": instruction.op},
        )
    )


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

    loop_id = f"L{state.loop_counter}"
    state.loop_counter += 1

    loop_q1_jump_runtime_ns = 24
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
    q1_period = _loop_q1_period(preview_events, q1_issue_event)
    q1_body_period = _loop_q1_body_period(q1_period, first_loop_edge_runtime_ns)
    loop_preview_key = _loop_preview_key(state, start_instruction.source, instruction.source)
    visible_iteration_count = _visible_loop_iteration_count(state, loop_preview_key, count=count, forever=forever)
    min_visible = max(1, min(int(min_visible_iterations), LOOP_PREVIEW_MAX_VISIBLE_ITERATIONS))
    visible_iteration_count = max(visible_iteration_count, min_visible)
    if not forever and isinstance(count, Concrete):
        visible_iteration_count = min(visible_iteration_count, max(1, count.value))
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
        confidence="exact" if forever else _confidence_for_value(count),
        source=start_instruction.source,
        meta={
            "loop_id": loop_id,
            "start_pc": start_pc,
            "end_pc": instruction.pc,
            "label": target_label,
            "counter_reg": counter_reg,
            "count": count_display,
            "period": value_to_json(duration),
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
            "source_start": _source_to_json(start_instruction.source),
            "source_end": _source_to_json(instruction.source),
            "first_iteration_event_ids": preview_ids,
            "preview_iteration_event_ids": preview_iteration_event_ids,
        },
    )
    _emit_event(
        state,
        lane="rt.loop.preview",
        kind="loop_iteration_preview",
        t0=t0,
        t1=t1,
        duration=duration,
        label=f"{loop_id} iteration 0 preview",
        confidence="exact" if forever else _confidence_for_value(count),
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
            confidence="exact" if forever else _confidence_for_value(count),
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
        )
    return True


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
) -> list[TimelineEvent]:
    shifted_events: list[TimelineEvent] = []
    for event in preview_events:
        period = q1_period if event.kind == "q1_issue" else rt_period
        meta = dict(event.meta)
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
    if not isinstance(left, Concrete) or not isinstance(right, Concrete) or not isinstance(result, Concrete):
        return None
    if op == "add":
        return _add_flags(left.value, right.value)
    if op == "sub":
        return _sub_flags(left.value, right.value)
    if op in {"asl", "asr", "lsl", "lsr"}:
        return _shift_flags(op, left.value, right.value, result.value)
    return _logic_flags(result.value)


def _execute_classical(state: AnalysisState, instruction: Instr, symbols: SymbolTable) -> None:
    scope = _scope(symbols, state)
    destination = _register_name(instruction.args[1], state) if len(instruction.args) >= 2 else None
    if instruction.op == "move" and destination is not None:
        value = resolve_arg_value(instruction.args[0], scope)
        value = _wrap_concrete_classical_register_value(value)
        provenance = _move_provenance(state, instruction, instruction.args[0], destination, value)
        _schedule_register_write(state, instruction, destination, value, provenance)
    elif instruction.op == "not" and destination is not None:
        source = resolve_arg_value(instruction.args[0], scope)
        value = _unary_classical_value("not", source)
        provenance = _unary_provenance(state, instruction, instruction.args[0], destination, value)
        _schedule_register_write(state, instruction, destination, value, provenance)
        _set_alu_flags(state, instruction, _logic_flags(value.value) if isinstance(value, Concrete) else None)
    elif instruction.op in {"add", "sub", "and", "or", "xor", "asl", "asr", "lsl", "lsr"} and len(instruction.args) >= 3:
        destination = _register_name(instruction.args[2], state)
        if destination is None:
            return
        left_arg, right_arg = _docs_ordered_binary_args(instruction, state)
        left = resolve_arg_value(left_arg, scope)
        right = resolve_arg_value(right_arg, scope)
        if instruction.op == "add":
            value = add_values(left, right)
        elif instruction.op == "sub":
            value = subtract_values(left, right)
        else:
            value = _binary_classical_value(instruction.op, left, right)
        if instruction.op in _WRAPPING_CLASSICAL_OPS:
            value = _wrap_concrete_classical_register_value(value)
        _set_alu_flags(state, instruction, _binary_alu_flags(instruction.op, left, right, value))
        provenance = _binary_provenance(
            state,
            instruction,
            left_arg,
            right_arg,
            destination,
            value,
        )
        _schedule_register_write(state, instruction, destination, value, provenance)
    elif instruction.op in {"cmp", "test"} and len(instruction.args) >= 2:
        left_arg, right_arg = _docs_ordered_binary_args(instruction, state)
        left = resolve_arg_value(left_arg, scope)
        right = resolve_arg_value(right_arg, scope)
        if instruction.op == "cmp":
            flags = _sub_flags(left.value, right.value) if isinstance(left, Concrete) and isinstance(right, Concrete) else None
        else:
            value = _binary_classical_value("and", left, right)
            flags = _logic_flags(value.value) if isinstance(value, Concrete) else None
        _set_alu_flags(state, instruction, flags)
    elif instruction.op in {"mulu16", "muls16", "mulu32", "muls32", "mulu32l", "mulu32h", "muls32l", "muls32h"}:
        _set_alu_flags(state, instruction, None)
        for destination_index in sorted(_register_destination_argument_indexes(instruction.op)):
            if destination_index >= len(instruction.args):
                continue
            destination = _register_name(instruction.args[destination_index], state)
            if destination is None:
                continue
            value = Unknown(f"{instruction.op} result")
            provenance = _register_provenance(
                state,
                destination,
                expression=f"{instruction.op} result",
                value=value,
                steps=[_provenance_step(instruction, f"{instruction.op} result", value)],
            )
            _schedule_register_write(state, instruction, destination, value, provenance)


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


def _execute_latched(state: AnalysisState, instruction: Instr, symbols: SymbolTable) -> None:
    scope = _scope(symbols, state)
    field_name: str | None = None
    value: Any = None

    if instruction.op == "set_mrk" and instruction.args:
        field_name = "marker"
        value = resolve_arg_value(instruction.args[0], scope)
        state.latched_state.marker = value
    elif instruction.op == "set_awg_gain" and len(instruction.args) >= 2:
        field_name = "awg_gain"
        value = (
            resolve_arg_value(instruction.args[0], scope),
            resolve_arg_value(instruction.args[1], scope),
        )
        state.latched_state.awg_gain = value
    elif instruction.op == "set_awg_offs" and len(instruction.args) >= 2:
        field_name = "awg_offset"
        value = (
            resolve_arg_value(instruction.args[0], scope),
            resolve_arg_value(instruction.args[1], scope),
        )
        state.latched_state.awg_offset = value
    elif instruction.op == "set_freq" and instruction.args:
        field_name = "frequency"
        value = resolve_arg_value(instruction.args[0], scope)
        state.latched_state.frequency = value
    elif instruction.op in {"set_ph", "reset_ph"}:
        field_name = "phase"
        value = resolve_arg_value(instruction.args[0], scope) if instruction.args else Concrete(0)
        state.latched_state.phase = value
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

    if field_name is not None:
        if field_name not in {"set_cond", "set_time_ref"}:
            state.latched_state.pending_since[field_name] = instruction.source
        _emit_event(
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


def _execute_rt_instruction(
    state: AnalysisState,
    instruction: Instr,
    symbols: SymbolTable,
    waveform_names: dict[int, str],
    acquisition_names: dict[int, str],
    q1_t0: Value,
    q1_t1: Value,
    rt_packet_id: str | None,
) -> None:
    spec = get_instruction_spec(instruction.op)
    scope = _scope(symbols, state)
    duration = Concrete(0)
    t0 = state.rt_time_ns
    t1: Value | None = state.rt_time_ns
    duration_arg: Arg | None = None
    duration_arg_index = rt_duration_arg_for_instruction(spec, instruction)

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

    applied_state = _consume_applied_state(state) if spec.applies_latched_state else {}
    packet = _append_packet(state, instruction, duration, q1_t0, q1_t1, t0, t1, rt_packet_id)

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
            if instruction.op in {"fb_acq_iq_id", "fb_acq_tb_id"} and channel == "0":
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
            continue
        if waveform_index < 0:
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
            label=waveform_names.get(waveform_index, f"wf#{waveform_index}"),
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
        meta["feedback"] = {
            "direction": "send",
            "channel": state.feedback_acq_channel,
            "source": _feedback_acquisition_source(label, bin_index),
        }
        if state.feedback_acq_data_type is not None:
            meta["feedback"]["data_type"] = state.feedback_acq_data_type
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
    state.diagnostics.append(
        Diagnostic(
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
    )
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
        confidence=_confidence_for_value(state.latched_state.marker),
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
        confidence="exact",
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
    )
    state.rt_packets.append(packet)
    return packet


def _emit_q1_issue(
    state: AnalysisState,
    instruction: Instr,
    q1_t0: Value,
    q1_t1: Value,
    rt_packet_id: str | None,
) -> None:
    meta: dict[str, Any] = {
        "op": instruction.op,
        "emits_rt_packet": rt_packet_id is not None,
        "display_modes": ["debug"],
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
        if event.kind != "q1_issue" and event.t1 is not None
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
) -> None:
    if not isinstance(count, Concrete):
        state.rt_time_ns = Unknown(f"compact loop {loop_id} iteration count is not concrete")
        state.q1_time_ns = Unknown(f"compact loop {loop_id} iteration count is not concrete")
        if counter_reg is not None:
            state.registers[counter_reg] = Unknown(f"{counter_reg} after compact loop {loop_id}")
        return

    hidden_iterations = max(count.value - 1, 0)
    if isinstance(rt_period, Concrete):
        state.rt_time_ns = add_values(state.rt_time_ns, multiply_value(rt_period, hidden_iterations))
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
    return applied


def _scope(symbols: SymbolTable, state: AnalysisState) -> SymbolTable:
    label_symbols = {f"@{label}": Concrete(pc) for label, pc in state.labels.items()}
    scope = {**symbols, **label_symbols, **state.registers}
    for alias, register in state.register_aliases.items():
        scope[alias] = state.registers.get(register, Unknown(register))
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


def _arg_register_provenance(arg: Arg, state: AnalysisState) -> dict[str, Any] | None:
    register = _register_name(arg, state)
    if register is None:
        return None
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
        "expression": expression,
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
    for argument_index, operand_role in _rt_index_operand_roles(op):
        if operand_role == "feedback_acquisition_channel":
            return argument_index
    return None


def _feedback_acquisition_data_type(op: str) -> str | None:
    if op == "fb_acq_iq_id":
        return "iq_values"
    if op in {"fb_acq_tb_id", "fb_acq_tb_valid"}:
        return "thresholded_bits"
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
