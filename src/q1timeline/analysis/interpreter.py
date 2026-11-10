from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from itertools import count, product
import json
from typing import Any, Literal

from q1timeline.analysis.playback_loops import ExecutedStep, observed_loops
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
from q1timeline.feedback_fifo import feedback_pop_limitation
from q1timeline.q1asm.ast import Arg, Instr, Program, SourceLocation
from q1timeline.q1asm.compiled_addresses import CompiledAddresses, CompiledTarget, compile_addresses
from q1timeline.q1asm.instruction_table import (
    STATUS_BRANCH_OPS,
    InstructionSpec,
    get_instruction_spec,
    rt_duration_arg_for_instruction,
)
from q1timeline.q1asm.lint import analyze_program_lints
from q1timeline.q1asm.linq_commands import (
    LINQ_CONFIGURATION_STREAMS, MOCK_LIMITATION, decode_linq_command, linq_configuration_limitation, raw_linq_operation,
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
_RT_DURATION_MIN = 4
_AWG_PAIR_SETTERS = {"set_awg_gain", "set_awg_offs"}
_ANALYSIS_LATCH_RANGE_OPS = _AWG_PAIR_SETTERS | {"set_ph", "set_ph_delta"}
_NESTED_INVARIANT_SETTERS = {"set_freq", "set_mrk"} | _AWG_PAIR_SETTERS
_NETZERO_PULSE_OPS = {"play", "play_netzero"}
_ACQUISITION_FEEDBACK_TYPES = {
    "fb_acq_tb_id": "thresholded_bits", "fb_acq_iq_id": "iq_values",
    "fb_llp_tags_id": "low_latency_time_tags", "fb_tdc_tags_id": "high_resolution_time_tags",
    "fb_llp_ttls_id": "ttl_counts", "fb_tdc_tdelta_id": "time_delta",
}
_VALUE_ORIGIN_SERIALS = count(1)
_WRAPPING_CLASSICAL_OPS = {"add", "sub", "and", "or", "xor", "asl", "asr", "lsl", "lsr"}
_ALU_FLAG_NAMES = ("zf", "nf", "cf", "of")
_AluFlagCase = tuple[bool, bool, bool, bool]
ILLEGAL_INSTRUCTION_FLAG = "SEQUENCE_PROCESSOR_RT_EXEC_ILLEGAL_INSTRUCTION"
_CONCRETE_OPS = {
    "nop", "stop", "illegal", "move", "not", "cmp", "test", *_WRAPPING_CLASSICAL_OPS,
    "mulu16", "muls16", "mulu32", "muls32", "mulu32l", "mulu32h", "muls32l", "muls32h",
    "jmp", "jlt", "jge", "loop", *STATUS_BRANCH_OPS,
    "set_mrk", "set_freq", "reset_ph", "set_ph", "set_ph_delta", "set_awg_gain", "set_awg_offs",
    "set_digital", "set_scope_en", "wait", "wait_sync", "upd_param", "play", "acquire", "acquire_weighed", "acquire_weighted",
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
class _NetZeroWindow:
    source: SourceLocation
    event_id: str
    start_ns: int
    maxt_ns: int
    maxt_register: str | None


@dataclass(frozen=True)
class _AnalysisVisitEntry:
    event_start: int
    packet_start: int
    diagnostic_start: int


@dataclass(frozen=True)
class _LoopEvidence:
    events: list[TimelineEvent]
    packets: dict[str, RtPacket]
    diagnostics: list[Diagnostic]
    children: list[TimelineEvent]
    revisited: bool


@dataclass(frozen=True)
class _NestedLoopProof:
    children: dict[int, TimelineEvent]
    count_slopes: dict[int, int]


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
    rt_trigger_epoch: int = 0
    netzero_window: _NetZeroWindow | None = None
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
    feedback_acq_channels: dict[str, str] = field(default_factory=dict)
    alu_flags: dict[str, bool] | None = None
    alu_flags_source: SourceLocation | None = None
    alu_flag_cases: frozenset[_AluFlagCase] | None = None
    alu_flags_read_hazard: bool = False
    loop_counter: int = 0
    q1_issue_index: int = 0
    next_pc: int | None = None
    stopped: bool = False
    executed_pcs: set[int] = field(default_factory=set)
    analysis_back_edge_limit: int | None = None
    analysis_back_edge_issue: TimelineEvent | None = None
    analysis_replayed_steps: int = 0
    analysis_replayed_pcs: set[int] = field(default_factory=set)
    analysis_visit_entries: dict[int, _AnalysisVisitEntry] = field(default_factory=dict)
    branch_policy: str = "collapse_unresolved"
    branch_assumptions: dict[str, BranchAssumptionPath] = field(default_factory=dict)
    branch_explore_depth: int = 0
    loop_preview_counts: dict[str, int] = field(default_factory=dict)
    strict_q1asm: bool = False
    execution_mode: Literal["analysis", "concrete"] = "analysis"
    compiled_addresses: CompiledAddresses | None = None
    interior_pc: int | None = None
    next_interior_pc: int | None = None
    static_diagnostics: list[Diagnostic] = field(default_factory=list)
    register_known_bits: dict[str, tuple[int, int]] = field(default_factory=dict)
    register_value_origins: dict[str, tuple[str, bool]] = field(default_factory=dict, compare=False)
    value_origin_serial: int = field(default=0, compare=False)
    immutable_value_origins: dict[str, tuple[str, bool]] = field(default_factory=dict, compare=False)
    pending_phase_reset: bool | None = False
    nco_phase_uncertain: bool = False
    queue_alternatives: dict[str, AnalysisState] = field(default_factory=dict, compare=False, repr=False)
    queue_alternative_probe: bool = field(default=False, compare=False, repr=False)
    record_trace: bool = True
    bound_instructions: dict[int, _BoundInstruction] = field(default_factory=dict, repr=False)
    event_serial: int = 0
    observed_loop_events: list[TimelineEvent] = field(default_factory=list)


@dataclass(frozen=True)
class _BoundInstruction:
    spec: InstructionSpec
    raw_diagnostics: tuple[Diagnostic, ...]
    duration_ns: int | None
    simple_operands: bool
    reads: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class PreparedDefProgram:
    """Reusable source data for a sweep that changes only named integer DEFs."""

    instructions_by_pc: dict[int, Instr]
    register_aliases: dict[str, str]
    param_symbols: SymbolTable
    revalidate: tuple[Instr, ...] | None
    lints: tuple[Diagnostic, ...]
    bindings: dict[int, _BoundInstruction]
    needs_queue_analysis: bool
    compiled_addresses: CompiledAddresses | None = None


def prepare_def_program(
    program: Program, *, params: dict[str, Any], strict_q1asm: bool, varying: set[str],
) -> PreparedDefProgram:
    state, symbols = _prepare_program(program, sequencer_id="source", params=params, strict_q1asm=strict_q1asm,
                                      execution_mode="concrete", recover_source=False)
    # A placeholder DEF may indirectly reference a swept value. Keep its users
    # and any template operands on the per-case validation path.
    dynamic = varying | {name for name, arg in program.defs.items() if arg.kind == "placeholder"}
    revalidate = None if any(item.severity == "error" for item in state.diagnostics) else tuple(
        instruction for instruction in program.instructions
        if any(arg.kind == "placeholder" or dynamic.intersection(_arg_symbol_lookup_names(arg))
               for arg in instruction.args)
    )
    integer_symbols = {f"${name}" for name, arg in program.defs.items() if arg.kind == "imm"}
    bindings = {}
    for instruction in program.instructions:
        spec = get_instruction_spec(instruction.op)
        raw_diagnostics = tuple(spec.validate(instruction))
        durations = {signature.q1_time_ns for signature in spec.signatures
                     if len(signature.args) == len(instruction.args)}
        if spec.signatures:
            durations.add(spec.signatures[0].q1_time_ns)  # Unmatched signatures use the default.
        fixed_shape = all(arg.kind in {"reg", "imm", "label"} for arg in instruction.args)
        simple_operands = (
            revalidate is not None and not raw_diagnostics
            and (spec.category == "classical" or instruction.op == "stop")
            and all(arg.kind in {"reg", "imm"} or (arg.kind == "symbol" and arg.value in integer_symbols)
                    for arg in instruction.args)
        )
        bindings[instruction.pc] = _BoundInstruction(
            spec, raw_diagnostics,
            spec.q1_time_model(instruction) if fixed_shape or len(durations) <= 1 else None,
            simple_operands, tuple(_register_read_operands(instruction, state)),
        )
    return PreparedDefProgram(state.instructions_by_pc, state.register_aliases, symbol_table_from_params(params),
                              revalidate, tuple(analyze_program_lints(program, known_parameters=symbols)),
                              bindings, any(bound.spec.emits_rt_packet or bound.spec.category == "unknown"
                                            for bound in bindings.values()), state.compiled_addresses)


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
    prepared: PreparedDefProgram | None = None,
) -> AnalysisState:
    if not isinstance(execution_mode, str) or execution_mode not in {"analysis", "concrete"}:
        raise ValueError(f"Invalid execution mode: {execution_mode}")
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
        raise ValueError("max_steps must be a positive integer")
    state, symbols = _prepare_program(
        program, sequencer_id=sequencer_id, params=params, branch_policy=branch_policy,
        branch_assumptions=branch_assumptions, loop_preview_counts=loop_preview_counts,
        strict_q1asm=strict_q1asm, execution_mode=execution_mode, prepared=prepared,
    )
    state.pc = 0
    if execution_mode == "concrete":
        _run_concrete_program(state, symbols, waveform_names or {}, acquisition_names or {}, max_steps)
        _apply_ready_register_writes(state)
    else:
        state.analysis_back_edge_limit = max_steps
        _run_program_loop(state, symbols, waveform_names or {}, acquisition_names or {})
        _flush_pending_register_writes(state)
        _diagnose_static_register_read_hazards(state)
    return state


def diagnose_program_source(program: Program, *, params: dict[str, Any], strict_q1asm: bool) -> list[Diagnostic]:
    state, _ = _prepare_program(program, sequencer_id="source", params=params, strict_q1asm=strict_q1asm,
                                execution_mode="concrete", recover_source=False)
    return state.diagnostics


def _prepare_program(
    program: Program, *, sequencer_id: str, params: dict[str, Any] | None,
    branch_policy: str = "collapse_unresolved", branch_assumptions: Mapping[str, str] | None = None,
    loop_preview_counts: Mapping[str, int] | None = None, strict_q1asm: bool,
    execution_mode: Literal["analysis", "concrete"], recover_source: bool = True,
    prepared: PreparedDefProgram | None = None,
) -> tuple[AnalysisState, SymbolTable]:
    state = AnalysisState(
        sequencer_id=sequencer_id,
        labels=program.labels,
        instructions_by_pc=prepared.instructions_by_pc if prepared else {instruction.pc: instruction for instruction in program.instructions},
        register_aliases=prepared.register_aliases if prepared else _register_aliases(program.defs),
        branch_policy=branch_policy,
        branch_assumptions=_normalise_branch_assumptions(branch_assumptions),
        loop_preview_counts=_normalise_loop_preview_counts(loop_preview_counts),
        strict_q1asm=strict_q1asm,
        execution_mode=execution_mode,
        record_trace=prepared is None or execution_mode != "concrete",
        bound_instructions=prepared.bindings if prepared and execution_mode == "concrete" else {},
    )
    state.compiled_addresses = prepared.compiled_addresses if prepared else compile_addresses(state.instructions_by_pc, state.labels)
    state.diagnostics.extend(program.diagnostics)
    if strict_q1asm:
        _diagnose_strict_q1asm_placeholder_defs(state, program.defs)

    raw_params = params or {}
    symbols = dict(prepared.param_symbols) if prepared else symbol_table_from_params(raw_params)
    value_defs = {name: arg for name, arg in program.defs.items() if arg.kind != "reg"}
    resolved_defs = resolve_def_values(value_defs, symbols, state.diagnostics, labels=_address_labels(state))
    state.symbol_origins = _symbol_origins(raw_params, symbols, value_defs, resolved_defs)
    state.immutable_value_origins = _build_immutable_value_origins(symbols, value_defs, state)
    symbols.update(resolved_defs)
    _diagnose_program(state, symbols, program, recover_source=recover_source, prepared=prepared)
    return state, symbols


def _end_concrete_execution(state: AnalysisState, status: str, reason: str, instruction: Instr | None = None) -> None:
    pc = instruction.pc if instruction is not None else state.pc
    location = {"pc": pc}
    if state.compiled_addresses is not None:
        location["compiled_pc"] = _compiled_pc(state, pc)
    state.metadata["execution"].update(status=status, reason=reason, **location)
    state.stopped = True
    source = instruction.source if instruction is not None else None
    if source is not None:
        state.metadata["execution"]["source"] = _source_to_json(source)
    state.diagnostics.append(Diagnostic(
        severity="error" if status == "error" else "warning",
        category="analysis_incomplete" if status != "error" else "invalid_argument_value",
        message=f"{state.metadata['execution']['mode'].capitalize()} execution {status}: {reason}",
        source=source,
        details={"execution_status": status, **location},
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
        "timing_model": "analytical", "address_model": _address_model(state),
        "assumptions": [
            "Sequencers execute independently; wait_sync uses a local timing origin.",
            "Q1/RT timing is analytical; queue backpressure and external feedback are not simulated.",
            "Source PCs are kept for navigation. Branch operands use compiled Q1ASM addresses."
            if state.compiled_addresses is not None else "PC values index source instructions.",
        ],
    }
    errors = [diagnostic for diagnostic in state.diagnostics
              if diagnostic.severity == "error" and not diagnostic.details.get("source_line_skipped")]
    if errors:
        status = "error" if any(item.category != "unresolved_symbol" for item in errors) else "blocked"
        _end_concrete_execution(state, status, errors[0].message)
        return
    trace: list[ExecutedStep] = []
    while not state.stopped:
        _apply_ready_register_writes(state)
        instruction = _execution_instruction(state)
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
        _begin_execution_step(state)
        if state.record_trace:
            scope = _scope(symbols, state)
            flags_before = dict(state.alu_flags) if state.alu_flags is not None else None
            event_start = len(state.events)
            rt_start = state.rt_time_ns
            continuation = _has_jump_continuation(state, instruction)
            state.current_resolved_args = _resolved_args_for_instruction(instruction, scope, state)
            try:
                _execute_instruction(state, instruction, symbols, waveform_names, acquisition_names)
            finally:
                state.current_resolved_args = []
            branch_taken, branch_target = False, None
            for event in state.events[event_start:]:
                if event.kind == "q1_issue":
                    event.meta.update(
                        source_pc=instruction.pc, execution_index=execution["steps"],
                        flags_before=flags_before,
                        flags_after=dict(state.alu_flags) if state.alu_flags is not None else None,
                    )
                    branch_taken = bool(event.meta.get("branch_taken"))
                    branch_target = event.meta.get("compiled_target_pc", event.meta.get("target_pc"))
            if execution["status"] == "completed" or "fault" in execution:
                trace.append(_executed_step(state, instruction, event_start, rt_start,
                                            branch_taken, branch_target, continuation))
        else:
            _execute_instruction(state, instruction, symbols, waveform_names, acquisition_names)
        if execution["status"] != "completed" and "fault" not in execution:
            break
        state.executed_pcs.add(state.pc)
        state.q1_issue_index += 1
        execution["steps"] += 1
        if "fault" in execution:
            break
        _advance_execution(state)
    state.observed_loop_events = _observed_loop_events(state, trace, mode="concrete")


def _has_jump_continuation(state: AnalysisState, instruction: Instr) -> bool:
    continuation_pc = (state.compiled_addresses.entries.get(instruction.pc + 1)
                       if state.compiled_addresses is not None else instruction.pc + 1)
    return instruction.op == "jmp" and any(
        isinstance(value, Concrete) and value.value == continuation_pc
        for value in [*state.registers.values(), *(write.value for write in state.pending_register_writes)]
    )


def _executed_step(
    state: AnalysisState, instruction: Instr, event_start: int, rt_start: Value,
    branch_taken: bool, branch_target: int | None, continuation: bool,
) -> ExecutedStep:
    jump = instruction.op == "jmp"
    pc, next_pc = state.pc, state.next_pc
    if state.compiled_addresses is not None:
        pc = state.interior_pc if state.interior_pc is not None else state.compiled_addresses.entries[instruction.pc]
        next_pc = (state.next_interior_pc if state.next_interior_pc is not None
                   else state.compiled_addresses.entries.get(state.next_pc))
    return ExecutedStep(
        pc, None if state.stopped else next_pc,
        event_start, len(state.events), state.q1_issue_index, rt_start, state.rt_time_ns,
        branch_taken, branch_target, jump, jump and _register_name(instruction.args[0], state) is not None,
        continuation,
        instruction.op in {"cmp", "test", "nop"} or (
            instruction.op != "loop" and get_instruction_spec(instruction.op).category == "branch"
        ),
        source_pc=instruction.pc,
    )


def _observed_loop_events(
    state: AnalysisState, trace: list[ExecutedStep], *, mode: Literal["concrete", "playback"],
) -> list[TimelineEvent]:
    summaries: list[TimelineEvent] = []
    parents: list[tuple[int, int, dict[str, Any]]] = []
    for loop in observed_loops(state.sequencer_id, trace, stopped=state.stopped, mode=mode):
        first, last = loop["ranges"][0], loop["ranges"][-1]
        start, end = first[0], last[1]
        body = state.events[start:end]
        if mode == "concrete" and not any(event.lane.startswith("rt.") for event in body):
            continue
        members = [event.id for event in body]
        first_size = first[1] - start
        durations = [subtract_values(t1, t0) for _, _, t0, t1 in loop["ranges"]]
        period = durations[0] if all(item == durations[0] for item in durations) else Unknown("Observed iteration durations differ")
        count = len(loop["ranges"])
        start_source = state.instructions_by_pc[loop["start_pc"]].source
        end_source = state.instructions_by_pc[loop["end_pc"]].source
        loop_id = loop["loop_id"]
        meta = {
            "loop_id": loop_id, "loop_preview_key": loop_id,
            "count": count if loop["complete"] else f">={count}", "completed_iterations": count,
            "complete": loop["complete"], "period": value_to_json(period),
            "first_iteration_end": value_to_json(first[3]),
            "first_iteration_event_ids": members[:first_size], "hidden_event_ids": members[first_size:],
            "shown_iterations": [0], "visible_iteration_count": 1,
            "source_start": _source_to_json(start_source), "source_end": _source_to_json(end_source),
        }
        if mode == "playback":
            meta.update(playback=True, playback_event_ids=members)
        else:
            meta["observed"] = True
        summaries.append(TimelineEvent(
            loop_id, state.sequencer_id, "rt.loop", "loop_block", first[2], last[3],
            subtract_values(last[3], first[2]), f"loop x{meta['count']}",
            "assumed" if any(event.confidence == "assumed" for event in body) else "exact", start_source, meta,
        ))
        while parents and end > parents[-1][0]:
            parents.pop()
        for _, first_end, parent_meta in parents:
            if start >= first_end:
                parent_meta["hidden_event_ids"].append(loop_id)
        parents.append((end, first[1], meta))
    return summaries


def _concrete_instruction_ready(
    state: AnalysisState, instruction: Instr, symbols: SymbolTable,
    spec: InstructionSpec, has_invalid_arguments: bool, diagnostic_start: int,
    *, supported_ops: set[str] | None = None, allow_symbolic_operands: bool = False,
    rt_duration_error: Diagnostic | None = None,
) -> bool:
    diagnostics = state.diagnostics[diagnostic_start:]
    errors = [item for item in diagnostics if (item.severity == "error" and item.category != "unresolved_symbol") or item.category == "register_not_ready"]
    if errors:
        reason = errors[0].message
        _end_concrete_execution(state, "error", reason, instruction)
        return False
    if rt_duration_error is not None:
        _end_concrete_execution(state, "error", rt_duration_error.message, instruction)
        state.diagnostics[-1].details.update(rt_duration_error.details)
        return False
    if (instruction.op not in (_CONCRETE_OPS if supported_ops is None else supported_ops)
            and not _set_cond_is_disabled(state, instruction, symbols)):
        status = "error" if spec.category == "unknown" else "blocked"
        _end_concrete_execution(state, status, f"{instruction.op} has no supported concrete execution semantics.", instruction)
        return False
    scope = _scope(symbols, state)
    condition_disabled = _set_cond_is_disabled(state, instruction, symbols)
    if instruction.op in {"mulu32", "muls32"} and not has_invalid_arguments:
        if _register_name(instruction.args[2], state) == _register_name(instruction.args[3], state):
            _end_concrete_execution(state, "blocked", "Full-product multiply destinations resolve to the same register; write priority is unverified.", instruction)
            return False
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
        unsupported = _unsupported_register_operand(state, instruction, scope, condition_disabled=condition_disabled)
        if unsupported is not None:
            _end_concrete_execution(state, "blocked", unsupported.message, instruction)
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
        if state.analysis_back_edge_issue is not None and state.pc in state.executed_pcs:
            if state.analysis_replayed_steps >= state.analysis_back_edge_limit:
                edge = state.analysis_back_edge_issue
                last_issue = next((event for event in reversed(state.events) if event.kind == "q1_issue"
                                   and "preview_source_event_id" not in event.meta
                                   and "branch_comparison_path" not in event.meta), edge)
                _end_analysis_at_issue(
                    state, edge, "analytical_back_edge_limit",
                    f"Reached the {state.analysis_back_edge_limit} repeated instruction limit while following a known backward branch.",
                    q1_boundary=last_issue,
                )
                break
            state.analysis_replayed_steps += 1
            state.analysis_replayed_pcs.add(state.pc)
        if state.analysis_back_edge_issue is not None and state.interior_pc is None:
            state.analysis_visit_entries[state.pc] = _AnalysisVisitEntry(
                len(state.events), len(state.rt_packets), len(state.diagnostics),
            )
        _apply_ready_register_writes(state)
        instruction = _execution_instruction(state)
        assert instruction is not None
        state.executed_pcs.add(state.pc)
        _begin_execution_step(state)
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
        _advance_execution(state)


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


def _diagnose_program(
    state: AnalysisState, symbols: SymbolTable, program: Program, *, recover_source: bool = True,
    prepared: PreparedDefProgram | None = None,
) -> None:
    # Reuse operand rules without inventing runtime register values or readiness.
    probe = AnalysisState(
        sequencer_id=state.sequencer_id,
        labels=state.labels,
        instructions_by_pc=state.instructions_by_pc,
        register_aliases=state.register_aliases,
        symbol_origins=state.symbol_origins,
        strict_q1asm=state.strict_q1asm,
        execution_mode="concrete",
        compiled_addresses=state.compiled_addresses,
    )
    if prepared is not None and prepared.revalidate is not None:
        for instruction in prepared.revalidate:
            _validate_instruction(probe, instruction, symbols, prepared.bindings[instruction.pc], source_check=True)
        if not any(item.severity == "error" for item in probe.diagnostics):
            state.diagnostics.extend(prepared.lints)
            return
        # Preserve ordinary diagnostics and source recovery for invalid inputs.
    # Removing an inline label can invalidate another branch to that label.
    while True:
        probe.labels = state.labels
        probe.instructions_by_pc = state.instructions_by_pc
        probe.compiled_addresses = state.compiled_addresses
        probe.diagnostics = []
        for instruction in state.instructions_by_pc.values():
            _validate_instruction(probe, instruction, symbols)
        errors = [item for item in probe.diagnostics
                  if item.severity == "error" and item not in state.static_diagnostics]
        state.static_diagnostics.extend(errors)
        state.diagnostics.extend(errors)
        if not recover_source or state.strict_q1asm or not _exclude_invalid_source_lines(state):
            break
        defs = {name: arg for name, arg in program.defs.items() if arg.kind != "reg"}
        state.compiled_addresses = compile_addresses(state.instructions_by_pc, state.labels)
        resolved = resolve_def_values(defs, symbols, labels=_address_labels(state))
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
    state.bound_instructions = {}
    return True


def _validate_instruction(
    state: AnalysisState,
    instruction: Instr,
    symbols: SymbolTable,
    binding: _BoundInstruction | None = None,
    *,
    source_check: bool = False,
) -> tuple[InstructionSpec, bool, int]:
    spec = binding.spec if binding is not None else get_instruction_spec(instruction.op)
    diagnostic_start = len(state.diagnostics)
    if binding is not None and binding.simple_operands and not source_check:
        # Source validation checked the literal operands for this case. Pending
        # register writes still depend on the instruction's execution occurrence.
        _diagnose_register_read_hazards(state, instruction, binding.reads)
        return spec, False, diagnostic_start
    validation_diagnostics = binding.raw_diagnostics if binding is not None else spec.validate(instruction)
    state.diagnostics.extend(validation_diagnostics)
    has_raw_invalid_arguments = any(
        diagnostic.category in {"invalid_argument_count", "invalid_argument_type"}
        for diagnostic in validation_diagnostics
    )
    has_invalid_register_operands = _diagnose_invalid_register_operands(state, instruction)
    has_invalid_bare_def_operands = _diagnose_bare_def_symbol_operands(state, instruction)
    has_unresolved_dollar_symbols = _diagnose_unresolved_dollar_symbol_operands(state, instruction)
    has_unresolved_strict_symbols = _diagnose_strict_q1asm_unresolved_operands(state, instruction, symbols)
    has_undefined_labels = _diagnose_undefined_label_operands(state, instruction, symbols, spec)
    if not has_raw_invalid_arguments:
        _diagnose_resolved_argument_types(state, instruction, spec, symbols)
    _diagnose_unresolved_register_operands(state, instruction, spec)
    _diagnose_register_read_hazards(state, instruction, binding.reads if binding is not None else None)
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
    state: AnalysisState, instruction: Instr, symbols: SymbolTable, spec: InstructionSpec,
) -> bool:
    missing_labels: set[str] = set()
    target_index = None
    if spec.category == "branch":
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
    rt_condition_decision: bool | None = None,
) -> None:
    binding = state.bound_instructions.get(instruction.pc) if state.interior_pc is None else None
    spec, has_invalid_arguments, diagnostic_start = _validate_instruction(state, instruction, symbols, binding)
    scope = _scope(symbols, state) if state.record_trace or spec.emits_rt_packet or instruction.op == "set_cond" else {}
    register_metadata = _register_issue_metadata(state, instruction, scope) if state.record_trace else None
    duration_error = None if has_invalid_arguments else _invalid_rt_duration(instruction, spec, scope)

    if state.execution_mode == "concrete":
        if not _concrete_instruction_ready(
            state, instruction, symbols, spec, has_invalid_arguments, diagnostic_start,
            supported_ops=(_CONCRETE_OPS | {instruction.op}) if allow_symbolic_operands else None,
            allow_symbolic_operands=allow_symbolic_operands, rt_duration_error=duration_error,
        ):
            return
        if spec.category == "branch" and not _concrete_branch_destination_ready(state, instruction, _scope(symbols, state)):
            return

    q1_t0 = state.q1_time_ns
    raw_linq_op = raw_linq_operation(_arg_int(instruction.args[0], scope)) if (
        instruction.op == "fb_cmd" and len(instruction.args) == 3
    ) else None
    applies_latched_state = spec.applies_latched_state or raw_linq_op == "upd_thres"
    duration_ns = binding.duration_ns if binding is not None else None
    if raw_linq_op == "upd_thres":
        duration_ns = get_instruction_spec(raw_linq_op).q1_time_model(instruction)
    elif duration_ns is None:
        duration_ns = spec.q1_time_model(_instruction_for_q1_timing(instruction, state, symbols))
    q1_duration = Concrete(duration_ns)
    q1_t1 = add_values(q1_t0, q1_duration)
    state.q1_time_ns = q1_t1

    rt_packet_id: str | None = None
    if has_invalid_arguments:
        _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id, register_metadata=register_metadata)
        return
    if duration_error is not None:
        state.diagnostics.append(duration_error)
        duration = Unknown(duration_error.message)
        if spec.emits_rt_packet:
            rt_packet_id = f"{state.sequencer_id}:p{len(state.rt_packets)}"
            _append_packet(state, instruction, duration, q1_t0, q1_t1, state.rt_time_ns, None, rt_packet_id)
        issue = _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id, register_metadata=register_metadata)
        _emit_event(state, lane="rt.unknown", kind="unknown_region", t0=state.rt_time_ns, t1=None,
                    duration=duration, label=duration_error.message, confidence="unknown", source=instruction.source,
                    meta={**duration_error.details, "rt_packet_id": rt_packet_id, "q1_issue_event_id": issue.id})
        state.rt_time_ns = add_values(state.rt_time_ns, duration)
        state.netzero_window = None
        return

    # Typed operands are validated at queue admission, before the RT condition is evaluated.
    if (state.execution_mode == "analysis" and spec.emits_rt_packet and instruction.op != "fb_cmd"
            and not _is_packed_feedback_register_form(instruction, state)):
        unsupported = _unsupported_register_operand(state, instruction, scope)
        if unsupported is not None:
            issue = _emit_q1_issue(state, instruction, q1_t0, q1_t1, None, register_metadata=register_metadata)
            _end_analysis_at_issue(state, issue, "unsupported_rt_register_value", unsupported.message,
                                   details=unsupported.details)
            return

    if rt_condition_decision is not False and (
        instruction.op != "set_cond" or state.metadata.get("execution", {}).get("mode") != "playback"
    ):
        _diagnose_analysis_incomplete_timing_effect(state, instruction, spec, symbols)

    if spec.category == "unknown":
        _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id, register_metadata=register_metadata)
        return

    if spec.category == "classical":
        _execute_classical(state, instruction, symbols)
        if state.record_trace:
            _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id, register_metadata=register_metadata)
        return

    if spec.category == "latched":
        if state.execution_mode == "analysis" and instruction.op in _ANALYSIS_LATCH_RANGE_OPS:
            unsupported = _unsupported_register_operand(state, instruction, scope)
            if unsupported is not None:
                issue = _emit_q1_issue(state, instruction, q1_t0, q1_t1, None, register_metadata=register_metadata)
                _end_analysis_at_issue(state, issue, "unsupported_latched_register_value", unsupported.message,
                                       details=unsupported.details)
                return
        pending_event = _execute_latched(state, instruction, symbols)
        q1_issue_event = _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id, register_metadata=register_metadata)
        if pending_event is not None:
            pending_event.meta["q1_issue_event_id"] = q1_issue_event.id
            if instruction.op == "set_cond":
                q1_issue_event.meta["conditional_enabled"] = pending_event.meta["conditional_enabled"]
        return

    if spec.category == "feedback":
        if state.execution_mode == "analysis" and instruction.op == "fb_pop_data":
            selector = resolve_arg_value(instruction.args[0], scope)
            limitation = feedback_pop_limitation(selector.value) if isinstance(selector, Concrete) else None
            if limitation is not None:
                issue = _emit_q1_issue(state, instruction, q1_t0, q1_t1, None, register_metadata=register_metadata)
                _end_analysis_at_issue(
                    state, issue, "unsupported_feedback_selector", limitation,
                    details={"op": instruction.op, "selector": selector.value, "feedback_fifo_incomplete": True},
                )
                return
        _execute_feedback_register_write(state, instruction, symbols)
        q1_issue_event = _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id, register_metadata=register_metadata)
        _emit_feedback_annotation(state, instruction, q1_issue_event, symbols)
        return

    if state.execution_mode == "analysis" and instruction.op == "sw_req":
        issue = _emit_q1_issue(state, instruction, q1_t0, q1_t1, None, register_metadata=register_metadata)
        _end_analysis_at_issue(state, issue, "unverified_instruction", "Execution behavior for sw_req is unverified.",
                               details={"op": instruction.op})
        return

    if instruction.op == "illegal":
        issue = (_emit_q1_issue(state, instruction, q1_t0, q1_t1, None, register_metadata=register_metadata)
                 if state.record_trace else None)
        _record_illegal_instruction(state, instruction, issue)
        return

    if instruction.op == "stop":
        meta = {"output_state_unknown": False} if state.conditional_time_unknown else {}
        meta["stop_code"] = _record_stop_code(state, instruction, _scope(symbols, state))
        if state.record_trace or state.latched_state.pending_since:
            issue = _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id, register_metadata=register_metadata)
            issue.meta["stop_code"] = meta["stop_code"]
            meta["q1_issue_event_id"] = issue.id
        if state.record_trace:
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
                meta=meta,
            )
        state.stopped = True
        return

    if spec.category == "branch" and state.execution_mode == "concrete":
        issue = _emit_q1_issue(state, instruction, q1_t0, q1_t1, None,
                               register_metadata=register_metadata) if state.record_trace else None
        _execute_concrete_branch(state, instruction, issue, symbols, q1_t0=q1_t0)
        return
    if spec.emits_rt_packet:
        rt_packet_id = f"{state.sequencer_id}:p{len(state.rt_packets)}"
    q1_issue_event = _emit_q1_issue(state, instruction, q1_t0, q1_t1, rt_packet_id, register_metadata=register_metadata)

    if spec.category == "branch":
        hazard = next((item for item in state.diagnostics[diagnostic_start:]
                       if item.details.get("internal_loop_target_read")), None)
        if hazard is not None:
            q1_issue_event.meta.update(branch_decision="runtime_dependent", branch_taken="runtime_dependent",
                                       reason=hazard.category)
            _emit_event(state, lane="rt.unknown", kind="unknown_region", t0=state.rt_time_ns, t1=None,
                        duration=Unknown(hazard.message), label=hazard.message, confidence="unknown",
                        source=instruction.source, meta={"reason": hazard.category, "q1_issue_event_id": q1_issue_event.id})
            state.stopped = True
        else:
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
        phase_reset = state.pending_phase_reset if applies_latched_state else False
        nco_pending = set(state.latched_state.pending_since) & {"frequency", "phase", "phase_delta"}
        nco_settings = {
            name: getattr(state.latched_state, name) or Concrete(0)
            for name in {"frequency", "phase"} | nco_pending
        }
        if phase_reset is True and "phase" not in nco_pending:
            nco_settings["phase"] = Concrete(0)
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
            rt_condition_decision=rt_condition_decision,
            raw_linq_op=raw_linq_op,
        )
        if state.stopped:
            return
        if state.rt_packets and state.rt_packets[-1].id == rt_packet_id:
            _advance_after_zero_duration(state, state.rt_packets[-1], event_start - 1)
        if applies_latched_state:
            _finish_nco_issue(state, q1_issue_event, rt_packet_id, event_start,
                              phase_reset, nco_pending, nco_settings)
            _clear_nco_one_shots(state)
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
            and isinstance(nominal, Concrete) and _valid_rt_duration(nominal.value) and nominal == state.conditional_else_duration)


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


def _diagnose_register_read_hazards(
    state: AnalysisState, instruction: Instr, reads: tuple[tuple[int, str], ...] | None = None,
) -> None:
    if instruction.op == "loop" and len(instruction.args) == 2:
        counter = _register_name(instruction.args[0], state)
        if counter is not None and counter == _register_name(instruction.args[1], state):
            # loop lowers to sub followed immediately by a register-target jnz.
            state.diagnostics.append(Diagnostic(
                severity="warning", category="register_not_ready", source=instruction.source,
                message=(f"loop reads {counter} as its branch target before the counter decrement is available. "
                         "Use a separate target register or expand the loop with a gap between sub and jnz."),
                details={"op": "loop", "argument_index": 1, "register": counter,
                         "writer": _source_to_json(instruction.source), "internal_loop_target_read": True},
            ))
    if not state.pending_register_writes:
        return
    for argument_index, register in reads if reads is not None else _register_read_operands(instruction, state):
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
    state.register_value_origins[pending.register] = pending.value_origin if pending.value_origin is not None else _fresh_value_origin(state)
    if state.record_trace:
        state.register_provenance[pending.register] = pending.provenance
        _record_register_write(
            state, pending.register, pending.value, source=pending.source,
            event_id=pending.event_id, time=time, meta=meta,
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
    event_id = f"{state.sequencer_id}:e{len(state.events)}" if state.record_trace else ""
    if state.execution_mode == "concrete" and state.record_trace:
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
            } if state.record_trace else {},
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


def _valid_rt_duration(duration: int) -> bool:
    return duration == 0 or _RT_DURATION_MIN <= duration <= _RT_DURATION_MAX


def _invalid_rt_duration(instruction: Instr, spec: InstructionSpec, scope: SymbolTable) -> Diagnostic | None:
    is_else = instruction.op == "set_cond"
    index = 3 if is_else else rt_duration_arg_for_instruction(spec, instruction)
    if index is None or index >= len(instruction.args) or instruction.op == "fb_acq_tb_mock":
        return None
    arg = instruction.args[index]
    value = resolve_arg_value(arg, scope)
    if not isinstance(value, Concrete) or _valid_rt_duration(value.value):
        return None
    if instruction.op == "fb_cmd" and raw_linq_operation(_arg_int(instruction.args[0], scope)) == "fb_acq_tb_mock":
        return None
    role = "else_duration" if is_else else "duration"
    return Diagnostic(
        severity="error", category="invalid_argument_value", source=instruction.source,
        message=(f"{instruction.op} {role.replace('_', ' ')} {value.value} ns is invalid. "
                 f"RT duration must be zero or {_RT_DURATION_MIN}..{_RT_DURATION_MAX} ns."),
        details={"reason": "invalid_rt_duration", "op": instruction.op, "argument_index": index, "operand": arg.raw,
                 "operand_role": role, "value": value.value, "min_nonzero": _RT_DURATION_MIN, "max": _RT_DURATION_MAX},
    )


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


def _address_model(state: AnalysisState) -> str:
    return "compiled_q1asm" if state.compiled_addresses is not None else "source_instruction_index"


def _address_labels(state: AnalysisState) -> dict[str, int]:
    return state.compiled_addresses.labels if state.compiled_addresses is not None else state.labels


def _compiled_pc(state: AnalysisState, pc: int) -> int | None:
    if pc == state.pc and state.interior_pc is not None:
        return state.interior_pc
    return state.compiled_addresses.entries.get(pc) if state.compiled_addresses is not None else pc


def _execution_instruction(state: AnalysisState) -> Instr | None:
    if state.interior_pc is not None and state.compiled_addresses is not None:
        return state.compiled_addresses.targets[state.interior_pc].interior
    return state.instructions_by_pc.get(state.pc)


def _begin_execution_step(state: AnalysisState) -> None:
    state.next_pc = state.pc + 1
    state.next_interior_pc = None


def _advance_execution(state: AnalysisState) -> None:
    state.pc = state.next_pc
    state.interior_pc = state.next_interior_pc


def _execution_target(state: AnalysisState, value: Value) -> CompiledTarget | None:
    if not isinstance(value, Concrete) or type(value.value) is not int:
        return None
    if state.compiled_addresses is not None:
        return state.compiled_addresses.targets.get(value.value)
    return CompiledTarget(value.value, value.value) if value.value in state.instructions_by_pc else None


def _invalid_branch_target_reason(state: AnalysisState, value: Value) -> str:
    if not isinstance(value, Concrete):
        return "Branch destination is unknown."
    address_space = "Compiled branch target" if state.compiled_addresses is not None else "Branch target"
    return f"{address_space} PC {value.value} has no instruction."


def _set_execution_target(state: AnalysisState, target: CompiledTarget) -> None:
    state.next_pc = target.source_pc
    state.next_interior_pc = target.address if target.interior is not None else None


def _branch_target_metadata(target: CompiledTarget | None, state: AnalysisState) -> dict[str, Any]:
    result = {"target_pc": target.source_pc if target is not None else None}
    if target is not None and state.compiled_addresses is not None:
        result["compiled_target_pc"] = target.address
        if target.interior is not None:
            result["target_interior_op"] = target.interior.op
    return result


def _concrete_branch_test(
    state: AnalysisState, instruction: Instr, scope: SymbolTable,
) -> tuple[bool | None, dict[str, bool] | None]:
    if instruction.op == "loop":
        previous = state.registers[_register_name(instruction.args[0], state)].value
        flags = _sub_flags(previous, 1)
        return not flags["zf"], flags
    if len(instruction.args) == 3:
        left = resolve_arg_value(instruction.args[0], scope).value & _CLASSICAL_REGISTER_MASK
        right = resolve_arg_value(instruction.args[1], scope).value & _CLASSICAL_REGISTER_MASK
        return (left >= right if instruction.op == "jge" else left < right), _sub_flags(left, right)
    return (True if instruction.op == "jmp" else _status_branch_decision(instruction.op, state.alu_flags)), None


def _concrete_branch_destination_ready(
    state: AnalysisState, instruction: Instr, scope: SymbolTable, forced_decision: bool | None = None,
) -> bool:
    natural, _ = _concrete_branch_test(state, instruction, scope)
    taken = natural if forced_decision is None else forced_decision
    if not taken:
        return True
    target_index = 1 if instruction.op == "loop" else 2 if len(instruction.args) == 3 else 0
    value = resolve_arg_value(instruction.args[target_index], scope)
    if _execution_target(state, value) is not None:
        return True
    _end_concrete_execution(state, "error", _invalid_branch_target_reason(state, value), instruction)
    return False


def _execute_concrete_branch(
    state: AnalysisState, instruction: Instr, q1_issue_event: TimelineEvent | None, symbols: SymbolTable,
    *, q1_t0: Value | None = None, defer_register_write: bool = False, forced_decision: bool | None = None,
) -> None:
    if forced_decision is not None and (type(forced_decision) is not bool or instruction.op == "jmp"):
        raise ValueError("A forced branch decision must be a boolean for a conditional branch")
    scope = _scope(symbols, state)
    target_index = 1 if instruction.op == "loop" else 2 if len(instruction.args) == 3 else 0
    target = _execution_target(state, resolve_arg_value(instruction.args[target_index], scope))
    decision, flags = _concrete_branch_test(state, instruction, scope)
    if flags is not None:
        _set_alu_flags(state, instruction, flags)
    counter_write = None
    if instruction.op == "loop":
        register = _register_name(instruction.args[0], state)
        previous = state.registers[register]
        value = Concrete((previous.value - 1) & _CLASSICAL_REGISTER_MASK)
        event_id = q1_issue_event.id if q1_issue_event is not None else ""
        provenance = {}
        if state.record_trace:
            provenance = _register_provenance(
                state, register, expression=f"{register} - 1", value=value,
                steps=[_provenance_step(instruction, f"{register} - 1", value)],
            )
            provenance = _concrete_write_provenance(state, instruction, provenance, event_id)
        counter_write = PendingRegisterWrite(
            register, value, provenance,
            instruction.source, state.q1_issue_index + 1, event_id, {"op": "loop"},
        )
    if forced_decision is not None:
        if q1_issue_event is not None:
            q1_issue_event.meta.update(natural_branch_taken=decision, branch_overridden=True,
                                      assumption_source="user", assumed_branch_path="taken" if forced_decision else "fallthrough")
        decision = forced_decision
    duration = 24 if instruction.op in {"loop", "jlt", "jge"} else 16
    duration = duration if decision else 4
    if q1_issue_event is not None:
        q1_t0 = q1_issue_event.t0
    assert q1_t0 is not None
    q1_t1 = add_values(q1_t0, Concrete(duration))
    if q1_issue_event is not None:
        state.events[-1] = replace(q1_issue_event, t1=q1_t1, duration=Concrete(duration),
                                  confidence="assumed" if forced_decision is not None else q1_issue_event.confidence)
        q1_issue_event.meta.update(branch_decision="assumed" if forced_decision is not None else "concrete",
                                   branch_taken=decision, **_branch_target_metadata(target, state))
    state.q1_time_ns = q1_t1
    if counter_write is not None:
        if defer_register_write:
            state.pending_register_writes.append(counter_write)
        else:
            _apply_register_write(state, counter_write)
    if decision:
        assert target is not None
        _set_execution_target(state, target)
    elif forced_decision is not None:
        _begin_execution_step(state)


def _can_follow_compiled_interior(
    state: AnalysisState, instruction: Instr, target: CompiledTarget, symbols: SymbolTable,
) -> bool:
    branch = target.interior
    assert branch is not None
    current_address = _compiled_pc(state, instruction.pc)
    if current_address is None or target.address <= current_address or any(
        _pending_register_write(state, register) is not None for _, register in _register_read_operands(instruction, state)
    ):
        return False
    decision = _status_branch_decision(branch.op, state.alu_flags, state.alu_flag_cases)
    if decision is None:
        return False
    operand = branch.args[0]
    register = _physical_read_register(operand, state)
    # A jump occupies one issue slot before the lowered branch reads its target.
    writes = [write for write in state.pending_register_writes if write.register == register]
    if any(write.available_issue_index > state.q1_issue_index + 1 for write in writes):
        return False
    value = writes[-1].value if writes else resolve_arg_value(operand, _scope(symbols, state))
    _, _, minimum, maximum = _official_operand_ranges(branch.op)[0]
    if not isinstance(value, Concrete) or type(value.value) is not int or not minimum <= value.value <= maximum:
        return False
    if not decision:
        return True
    successor = _execution_target(state, value)
    return successor is not None and successor.address > target.address


def _execute_branch(
    state: AnalysisState,
    instruction: Instr,
    q1_issue_event: TimelineEvent,
    symbols: SymbolTable,
    waveform_names: dict[int, str],
    acquisition_names: dict[int, str],
) -> None:
    if len(instruction.args) == 1 and (instruction.op in STATUS_BRANCH_OPS or instruction.op == "jge"):
        _execute_status_branch(state, instruction, q1_issue_event, symbols, waveform_names, acquisition_names)
        return
    if state.compiled_addresses is not None and instruction.args and (instruction.op != "loop" or len(instruction.args) >= 2):
        target_index = 1 if instruction.op == "loop" else 2 if len(instruction.args) == 3 else 0
        target_arg = _branch_target_arg(instruction.args[target_index], state, symbols)
        if target_arg.kind == "imm":
            address = int(target_arg.value)
            q1_issue_event.meta["compiled_target_pc"] = address
            target = state.compiled_addresses.targets.get(address)
            if target is not None and target.interior is not None:
                if instruction.op == "jmp" and _can_follow_compiled_interior(state, instruction, target, symbols):
                    q1_issue_event.meta.update(branch_decision="concrete", branch_taken=True,
                                               **_branch_target_metadata(target, state))
                    _set_execution_target(state, target)
                    return
                if instruction.op != "jmp" and not any(
                    _pending_register_write(state, register) is not None
                    for _, register in _register_read_operands(instruction, state)
                ):
                    q1_issue_event.meta.update(source_pc=instruction.pc, **_branch_target_metadata(target, state))
                    if instruction.op in {"jlt", "jge"}:
                        _execute_conditional_branch(state, instruction, q1_issue_event, symbols,
                                                    waveform_names, acquisition_names, interior_target=target)
                        return
                    if instruction.op == "loop":
                        counter = _register_name(instruction.args[0], state)
                        count = state.registers.get(counter)
                        if isinstance(count, Concrete) and (count == Concrete(1) or target.source_pc > instruction.pc):
                            _execute_concrete_branch(state, instruction, q1_issue_event, symbols)
                            if state.next_interior_pc is None or _can_follow_compiled_interior(
                                state, instruction, target, symbols
                            ):
                                return
                _collapse_compiled_interior_target(state, instruction, q1_issue_event, target)
                return
    if instruction.op == "loop" and len(instruction.args) >= 2:
        counter_reg = _register_name(instruction.args[0], state)
        count = state.registers.get(counter_reg, Unknown(counter_reg or "loop_count"))
        condition = f"loop {instruction.args[0].raw}"
        target_arg = _branch_target_arg(instruction.args[1], state, symbols)
        target_label = str(target_arg.value) if target_arg.kind == "label" else None
        target_pc = _numeric_branch_target_pc(target_arg, state)
        if target_label is not None and target_label not in state.labels:
            _emit_undefined_label(state, instruction, target_label)
            _collapse_undefined_label_branch(
                state,
                instruction,
                q1_issue_event,
                condition,
                target_label,
            )
            return
        target = _execution_target(state, resolve_arg_value(instruction.args[1], _scope(symbols, state)))
        falls_through_unloaded = target is None and target_arg.kind == "imm" and count == Concrete(1)
        pending_operand = any(_pending_register_write(state, register)
                              for _, register in _register_read_operands(instruction, state))
        if falls_through_unloaded or (target is not None and target.source_pc > instruction.pc):
            if isinstance(count, Concrete) and not pending_operand:
                _execute_concrete_branch(state, instruction, q1_issue_event, symbols)
                if target_label is not None:
                    q1_issue_event.meta["target_label"] = target_label
                return
            if target is not None:
                q1_issue_event.meta.update(_branch_target_metadata(target, state))
                _collapse_forward_loop_branch(state, instruction, q1_issue_event, condition, target_label, counter_reg,
                                              target_pc=target.source_pc)
                return
        if target_label is not None:
            created = _emit_compact_loop(
                state,
                instruction,
                q1_issue_event=q1_issue_event,
                start_pc=state.labels[target_label],
                target_label=target_label,
                counter_reg=counter_reg,
                count=count,
                forever=False,
                symbols=symbols,
            )
            if not created:
                _collapse_forward_loop_branch(
                    state,
                    instruction,
                    q1_issue_event,
                    condition,
                    target_label,
                    counter_reg,
                )
        elif target_pc is not None:
            target_register = _physical_read_register(instruction.args[1], state)
            if (target is not None and target.interior is None and not pending_operand
                    and (count == Concrete(1) or _loop_target_register_is_stable(state, target_register, target.source_pc, instruction.pc))):
                q1_issue_event.meta.update(_branch_target_metadata(target, state))
                if _emit_compact_loop(
                    state, instruction, q1_issue_event=q1_issue_event, start_pc=target.source_pc, target_label=None,
                    counter_reg=counter_reg, count=count, forever=False, symbols=symbols,
                ):
                    return
            _collapse_backward_numeric_branch(
                state,
                instruction,
                q1_issue_event,
                condition,
                target_pc,
            )
        elif target_arg.kind == "reg":
            _collapse_register_target_branch(
                state,
                instruction,
                q1_issue_event,
                condition,
                target_arg,
            )
    elif instruction.op == "jmp" and instruction.args:
        target_arg = _branch_target_arg(instruction.args[0], state, symbols)
        target_label = str(target_arg.value) if target_arg.kind == "label" else None
        target_pc = _numeric_branch_target_pc(target_arg, state)
        if target_label is not None:
            created = _emit_compact_loop(
                state,
                instruction,
                q1_issue_event=q1_issue_event,
                start_pc=state.labels.get(target_label),
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
            target_register = _physical_read_register(instruction.args[0], state)
            target = _execution_target(state, resolve_arg_value(instruction.args[0], _scope(symbols, state)))
            if (target is not None and target.interior is None and target.source_pc <= instruction.pc
                    and target.source_pc in state.executed_pcs
                    and (target_register is None or _pending_register_write(state, target_register) is None)
                    and _loop_target_register_is_stable(state, target_register, target.source_pc, instruction.pc)):
                q1_issue_event.meta.update(_branch_target_metadata(target, state))
                if _emit_compact_loop(
                    state, instruction, q1_issue_event=q1_issue_event, start_pc=target.source_pc, target_label=None,
                    counter_reg=None, count=None, forever=True,
                ):
                    q1_issue_event.meta.update(branch_decision="concrete", branch_taken=True)
                    state.stopped = True
                    return
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
    elif instruction.op in {"jge", "jlt"} and len(instruction.args) >= 3:
        _execute_conditional_branch(state, instruction, q1_issue_event, symbols, waveform_names, acquisition_names)


def _revise_q1_issue_duration(state: AnalysisState, event: TimelineEvent, duration: int) -> TimelineEvent:
    q1_duration = Concrete(duration)
    q1_t1 = add_values(event.t0, q1_duration)
    updated = replace(event, t1=q1_t1, duration=q1_duration)
    for index in range(len(state.events) - 1, -1, -1):
        if state.events[index].id == event.id:
            state.events[index] = updated
            break
    state.q1_time_ns = q1_t1
    return updated


def _possible_alu_flags(flags: dict[str, bool] | None) -> frozenset[_AluFlagCase]:
    known = flags or {}
    return frozenset(product(*[(known[name],) if name in known else (False, True) for name in _ALU_FLAG_NAMES]))


def _status_branch_decision(
    op: str, flags: dict[str, bool] | None, cases: frozenset[_AluFlagCase] | None = None,
) -> bool | None:
    if cases is not None or (flags is not None and len(flags) < len(_ALU_FLAG_NAMES)):
        possibilities = cases if cases is not None else _possible_alu_flags(flags)
        decisions = {_status_branch_decision(op, dict(zip(_ALU_FLAG_NAMES, values))) for values in possibilities}
        return decisions.pop() if len(decisions) == 1 else None
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


def _refine_status_branch_arm(state: AnalysisState, op: str, *, taken: bool) -> None:
    possibilities = state.alu_flag_cases if state.alu_flag_cases is not None else _possible_alu_flags(state.alu_flags)
    state.alu_flag_cases = frozenset(
        values for values in possibilities if _status_branch_decision(op, dict(zip(_ALU_FLAG_NAMES, values))) is taken
    )


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
    target_pc = _numeric_branch_target_pc(target_arg, state)
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

    compiled_target = None
    if state.compiled_addresses is not None and target_arg.kind == "imm":
        address = int(target_arg.value)
        q1_issue_event.meta["compiled_target_pc"] = address
        candidate = state.compiled_addresses.targets.get(address)
        if candidate is not None and candidate.interior is not None:
            compiled_target = candidate
    if state.interior_pc is not None:
        compiled_target = _execution_target(state, resolve_arg_value(instruction.args[0], _scope(symbols, state)))
    if compiled_target is not None or state.interior_pc is not None:
        q1_issue_event.meta.update(source_pc=instruction.pc, **_branch_target_metadata(compiled_target, state))

    if target_label is not None and target_label not in state.labels:
        _emit_undefined_label(state, instruction, target_label)
        _collapse_undefined_label_branch(state, instruction, q1_issue_event, condition, target_label)
        return

    taken_duration = 24 if instruction.op == "jge" else 16
    decision = _status_branch_decision(instruction.op, state.alu_flags, state.alu_flag_cases)
    if decision is not None:
        q1_issue_event = _revise_q1_issue_duration(
            state,
            q1_issue_event,
            taken_duration if decision else 4,
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
        if compiled_target is not None:
            if compiled_target.interior is not None and not _can_follow_compiled_interior(
                state, instruction, compiled_target, symbols
            ):
                _collapse_compiled_interior_target(state, instruction, q1_issue_event, compiled_target)
            else:
                _set_execution_target(state, compiled_target)
            return
        if target_label is not None:
            if _is_backward_branch_target(state, instruction, target_label):
                if not _follow_analysis_back_edge(state, instruction, q1_issue_event, target_arg, state.labels[target_label]):
                    _collapse_backward_conditional_branch(state, instruction, q1_issue_event, condition, target_label)
                return
            state.next_pc = state.labels[target_label]
            return
        if target_pc is not None:
            if _is_backward_numeric_branch_target(instruction, target_pc):
                if not _follow_analysis_back_edge(state, instruction, q1_issue_event, target_arg, target_pc):
                    _collapse_backward_numeric_branch(state, instruction, q1_issue_event, condition, target_pc)
                return
            if target_pc in state.instructions_by_pc:
                state.next_pc = target_pc
                return
            _collapse_unknown_numeric_branch_target(state, instruction, q1_issue_event, condition, target_pc)
            return
        _collapse_register_target_branch(state, instruction, q1_issue_event, condition, target_arg)
        return

    interior_target = compiled_target if compiled_target is not None and compiled_target.interior is not None else None
    branch_selection_meta: dict[str, Any] = {}
    branch_specific_path = state.branch_assumptions.get(branch_id)
    comparison_limit = ("cannot follow a compiled macro interior" if interior_target is not None
                        else _branch_comparison_limit(state, instruction, target_label, target_pc))
    comparison_requested = _branch_comparison_requested(state, branch_specific_path)
    q1_issue_event.meta["branch_comparison_supported"] = comparison_limit is None
    if interior_target is None and _prove_timing_only_branch_join(
        state, instruction, q1_issue_event, symbols, target_label, target_pc, branch_id,
        taken_duration=taken_duration, waveform_names=waveform_names, acquisition_names=acquisition_names,
    ):
        return
    if comparison_requested and comparison_limit is None and _explore_conditional_branch_both_paths(
        state, instruction, q1_issue_event, symbols, waveform_names or {}, acquisition_names or {},
        condition=condition, target_label=target_label, target_pc=target_pc, branch_id=branch_id,
        taken_duration=taken_duration,
        assumption_source="branch_assumption" if branch_specific_path == "both" else "branch_policy",
    ):
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
    if comparison_requested and comparison_limit is not None:
        state.diagnostics.append(
            Diagnostic(
                severity="info",
                category="analysis_incomplete",
                message=f"Status branch comparison {comparison_limit}.",
                source=instruction.source,
                details={"condition": condition, "target_label": target_label, "target_pc": target_pc, "branch_id": branch_id},
            )
        )

    assumption_source = "branch_assumption" if branch_specific_path in {"taken", "fallthrough"} else "branch_policy"
    assumption = _branch_specific_assumption(state, branch_id)
    if (assumption is None and branch_specific_path not in {"collapsed", "both"}
            and (interior_target is None or state.branch_policy != "collapse_unresolved")
            and not _unresolved_replayed_back_edge(state, instruction, target_label, target_pc)):
        assumption = _branch_assumption(state.branch_policy)
    if assumption is not None:
        assumed_taken, assumed_path = assumption
        q1_issue_event = _revise_q1_issue_duration(
            state, q1_issue_event, taken_duration if assumed_taken else 4,
        )
        _refine_status_branch_arm(state, instruction.op, taken=assumed_taken)
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
        if assumed_taken and interior_target is not None:
            if _can_follow_compiled_interior(state, instruction, interior_target, symbols):
                _set_execution_target(state, interior_target)
            else:
                _collapse_compiled_interior_target(state, instruction, q1_issue_event, interior_target)
            return
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

    if interior_target is not None:
        _collapse_compiled_interior_target(state, instruction, q1_issue_event, interior_target)
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
            "q1_issue_event_id": q1_issue_event.id,
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
    *,
    interior_target: CompiledTarget | None = None,
) -> None:
    target_arg = _branch_target_arg(instruction.args[2], state, symbols)
    target_label = str(target_arg.value) if target_arg.kind == "label" else None
    target_pc = _numeric_branch_target_pc(target_arg, state)
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
        if interior_target is not None:
            if decision:
                if interior_target.source_pc > instruction.pc and _can_follow_compiled_interior(
                    state, instruction, interior_target, symbols
                ):
                    _set_execution_target(state, interior_target)
                else:
                    _collapse_compiled_interior_target(state, instruction, q1_issue_event, interior_target)
            return
        if decision and target_pc is not None:
            if _is_backward_numeric_branch_target(instruction, target_pc):
                if not _follow_analysis_back_edge(state, instruction, q1_issue_event, target_arg, target_pc):
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
                if not _follow_analysis_back_edge(state, instruction, q1_issue_event, target_arg, state.labels[target_label]):
                    _collapse_backward_conditional_branch(state, instruction, q1_issue_event, condition, target_label)
                return
            state.next_pc = state.labels[target_label]
        return

    q1_issue_event.meta["branch_decision"] = "runtime_dependent"
    if isinstance(decision, RuntimeDependent):
        q1_issue_event.meta["runtime_dependency"] = decision.source

    if interior_target is not None:
        _collapse_compiled_interior_target(state, instruction, q1_issue_event, interior_target)
        return

    branch_specific_path = state.branch_assumptions.get(branch_id)
    comparison_limit = _branch_comparison_limit(state, instruction, target_label, target_pc)
    comparison_requested = _branch_comparison_requested(state, branch_specific_path)
    q1_issue_event.meta["branch_comparison_supported"] = comparison_limit is None
    if _prove_timing_only_branch_join(state, instruction, q1_issue_event, symbols, target_label, target_pc, branch_id,
                                  taken_duration=24, waveform_names=waveform_names, acquisition_names=acquisition_names):
        return
    if comparison_requested and comparison_limit is None and _explore_conditional_branch_both_paths(
        state, instruction, q1_issue_event, symbols, waveform_names, acquisition_names,
        condition=condition, target_label=target_label, target_pc=target_pc, branch_id=branch_id,
        taken_duration=24,
        assumption_source="branch_assumption" if branch_specific_path == "both" else "branch_policy",
    ):
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
    if comparison_requested and comparison_limit is not None:
        state.diagnostics.append(
            Diagnostic(
                severity="info",
                category="analysis_incomplete",
                message=f"Branch comparison {comparison_limit}.",
                source=instruction.source,
                details={"condition": condition, "target_label": target_label, "target_pc": target_pc, "branch_id": branch_id},
            )
        )

    assumption_source = "branch_assumption" if branch_specific_path in {"taken", "fallthrough"} else "branch_policy"
    assumption = _branch_specific_assumption(state, branch_id)
    if (assumption is None and branch_specific_path not in {"collapsed", "both"}
            and not _unresolved_replayed_back_edge(state, instruction, target_label, target_pc)):
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
            "q1_issue_event_id": q1_issue_event.id,
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


def _numeric_branch_target_pc(arg: Arg, state: AnalysisState) -> int | None:
    if arg.kind != "imm":
        return None
    address = int(arg.value)
    if state.compiled_addresses is None:
        return address
    target = state.compiled_addresses.targets.get(address)
    if target is None:
        return address
    return target.source_pc if target.interior is None else None


def _is_backward_numeric_branch_target(instruction: Instr, target_pc: int) -> bool:
    return target_pc <= instruction.pc


def _unresolved_replayed_back_edge(
    state: AnalysisState, instruction: Instr, target_label: str | None, target_pc: int | None,
) -> bool:
    if state.branch_policy != "collapse_unresolved" or instruction.pc not in state.analysis_replayed_pcs:
        return False
    destination = state.labels.get(target_label) if target_label is not None else target_pc
    return destination is not None and destination <= instruction.pc


def _follow_analysis_back_edge(
    state: AnalysisState, instruction: Instr, issue: TimelineEvent, target_arg: Arg, target_pc: int,
) -> bool:
    if (state.analysis_back_edge_limit is None or state.queue_alternative_probe
            or target_pc not in state.instructions_by_pc or state.alu_flags_read_hazard
            or any(_pending_register_write(state, register) is not None
                   for _, register in _register_read_operands(instruction, state))):
        return False
    if (target_arg.kind == "imm" and state.compiled_addresses is not None
            and target_arg.value not in state.compiled_addresses.targets):
        return False
    state.analysis_back_edge_issue = issue
    state.analysis_visit_entries.clear()
    state.next_pc = target_pc
    return True


def _end_analysis_at_issue(
    state: AnalysisState, issue: TimelineEvent, reason: str, message: str, *,
    rt_boundary: Value | None = None, q1_boundary: TimelineEvent | None = None, details: dict[str, Any] | None = None,
) -> None:
    details = {**(details or {}), "reason": reason, "q1_issue_event_id": (q1_boundary or issue).id,
               "q1_proof_cutoff": True}
    marker = _emit_event(
        state, lane="rt.unknown", kind="unknown_region", t0=state.rt_time_ns if rt_boundary is None else rt_boundary, t1=None,
        duration=Unknown(message), label=message, confidence="unknown", source=issue.source, meta=details,
    )
    state.diagnostics.append(Diagnostic(
        severity="warning", category="analysis_incomplete", source=issue.source,
        message=message, details=details, related_events=[marker.id],
    ))
    state.stopped = True


def _end_unproved_nested_loop(
    state: AnalysisState, instruction: Instr, issue: TimelineEvent, count: Value | None,
    symbols: SymbolTable | None, message: str,
) -> None:
    if (instruction.op == "loop" and symbols is not None and isinstance(issue.t0, Concrete)
            and isinstance(count, Concrete) and 1 < count.value <= _CLASSICAL_REGISTER_MASK
            and not any(_pending_register_write(state, register) is not None
                        for _, register in _register_read_operands(instruction, state))):
        target = _execution_target(state, resolve_arg_value(instruction.args[1], _scope(symbols, state)))
        if target is not None and target.interior is None:
            # The observed edge can decrement its counter even when the following iteration is unknown.
            _execute_concrete_branch(state, instruction, issue, symbols)
    _end_analysis_at_issue(state, issue, "analytical_nested_loop_counter_varies", message)


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
        start_pc=state.labels.get(target_label),
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
            "q1_issue_event_id": q1_issue_event.id,
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
            "q1_issue_event_id": q1_issue_event.id,
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


def _collapse_compiled_interior_target(
    state: AnalysisState,
    instruction: Instr,
    q1_issue_event: TimelineEvent,
    target: CompiledTarget,
) -> None:
    assert target.interior is not None
    reason = "compiled_interior_branch_target"
    details = {
        "reason": reason, "compiled_target_pc": target.address, "target_interior_op": target.interior.op,
    }
    q1_issue_event.meta.update(details)
    q1_issue_event.meta.setdefault("branch_taken", "runtime_dependent")
    message = f"Compiled branch target PC {target.address} enters a macro at {target.interior.op}; static analysis cannot follow that entry."
    state.diagnostics.append(Diagnostic(
        severity="warning", category="analysis_incomplete", message=message,
        source=instruction.source, details=details,
    ))
    _emit_event(
        state, lane="rt.unknown", kind="unknown_region", t0=state.rt_time_ns, t1=None,
        duration=Unknown(message), label="analysis incomplete at compiled macro interior",
        confidence="unknown", source=instruction.source, meta=details,
    )
    state.next_pc = None
    state.next_interior_pc = None
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
            "q1_issue_event_id": q1_issue_event.id,
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
            "q1_issue_event_id": q1_issue_event.id,
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
    target_label: str | None,
    counter_reg: str | None,
    *,
    target_pc: int | None = None,
) -> None:
    destination = f"@{target_label}" if target_label is not None else str(target_pc)
    target_meta = {"target_label": target_label} if target_label is not None else {"target_pc": target_pc}
    reason = "unsupported_forward_loop"
    details = {"condition": condition, **target_meta, "counter_reg": counter_reg,
               "reason": reason, "branch_policy": state.branch_policy}
    q1_issue_event.meta.update(target_meta, branch_taken="runtime_dependent", reason=reason)
    state.diagnostics.append(
        Diagnostic(
            severity="warning",
            category="analysis_incomplete",
            message=f"Cannot resolve forward loop operands for target {destination}.",
            source=instruction.source,
            details=details,
        )
    )
    _emit_event(
        state,
        lane="rt.branch",
        kind="branch_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(f"{condition} -> {destination}"),
        label=f"unresolved forward loop to {destination}",
        confidence="runtime_dependent",
        source=instruction.source,
        meta={**details, "q1_issue_event_id": q1_issue_event.id},
    )
    _emit_event(
        state,
        lane="rt.unknown",
        kind="unknown_region",
        t0=state.rt_time_ns,
        t1=None,
        duration=Unknown(f"{condition} -> {destination}"),
        label="analysis incomplete after unresolved forward loop",
        confidence="runtime_dependent",
        source=instruction.source,
        meta=details,
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
            "q1_issue_event_id": q1_issue_event.id,
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


def _branch_comparison_requested(state: AnalysisState, branch_specific_path: str | None) -> bool:
    return branch_specific_path == "both" or (
        branch_specific_path is None and state.branch_policy == "explore_both_with_depth_limit"
    )


def _branch_comparison_limit(
    state: AnalysisState, instruction: Instr, target_label: str | None, target_pc: int | None,
) -> str | None:
    if state.branch_explore_depth >= 1:
        return "reached the comparison depth limit"
    if _resolved_forward_branch_target_pc(state, instruction, target_label, target_pc) is None:
        return "needs a known forward target"
    return None


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
    taken_duration: int,
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
            "comparison_q1_durations_ns": {"taken": taken_duration, "fallthrough": 4},
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
            "q1_issue_event_id": q1_issue_event.id,
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
        _revise_q1_issue_duration(path_state, path_issue, taken_duration if path == "taken" else 4)
        if len(instruction.args) == 1:
            _refine_status_branch_arm(path_state, instruction.op, taken=path == "taken")
        path_state.pc = next_pc
        path_state.next_pc = next_pc
        path_state.stopped = False
        path_state.q1_issue_index = state.q1_issue_index + 1
        path_state.branch_explore_depth = state.branch_explore_depth + 1
        _run_program_loop(path_state, symbols, waveform_names, acquisition_names)
        _flush_pending_register_writes(path_state)
        path_events = path_state.events[snapshot_event_count:]
        path_event_ids = {event.id: f"{event.id}:branch-{path}" for event in path_events}
        state.register_history.extend(
            replace(
                write,
                event_id=path_event_ids.get(write.event_id, write.event_id),
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
                event_ids=path_event_ids,
            )
        )
        comparison_events.extend(
            _branch_comparison_events(
                path_events,
                branch_id=branch_id,
                path=path,
                event_ids=path_event_ids,
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
            or _physical_read_register(instruction.args[target_index], state) is not None):
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
                next_pc = state.labels.get(str(target.value)) if target.kind == "label" else _numeric_branch_target_pc(target, state)
                if next_pc is None or next_pc <= pc:
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
        if len(instruction.args) == 1:
            _refine_status_branch_arm(probe, instruction.op, taken=path == "taken")
        else:
            refined_registers.update(_refine_legacy_branch_arm(probe, instruction, symbols, taken=path == "taken"))
        probe.pc = start
        probe.next_pc = start
        probe.q1_issue_index += 1
        _run_program_loop(probe, symbols, waveform_names or {}, acquisition_names or {}, stop_pc=join_pc)
        _apply_ready_register_writes(probe)
        if probe.pc != join_pc or probe.interior_pc is not None or probe.stopped or probe.pending_register_writes or any(
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
    elapsed = {path: sum(_rt_dequeue_spacing_ns(packet.duration.value) for packet in probe.rt_packets[packet_start:])
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
            offset += _rt_dequeue_spacing_ns(packet.duration.value)
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
                    and base.feedback_acq_channels):
                return None
            configuration = event.meta.get("linq_configuration")
            if configuration:
                operands = configuration.get("operands")
                tb_layout = (tuple(operands) if configuration["op"] == "fb_acq_tb_cfg"
                             and not configuration.get("reason") and operands and operands[0] else None)
                feedback.append((starts[packet_id], (configuration["op"], configuration.get("reason"),
                                                     configuration.get("conditional", False), tb_layout)))
            for stream in event.meta.get("feedback_streams", [event.meta.get("feedback")]):
                if stream:
                    feedback.append((starts[packet_id], stream))
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
                    "waveform_index", "path", "acquisition_index", "bin_index", "field", "value", "applied_state",
                    "feedback", "feedback_streams",
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
                    accumulated = accumulated + delta * 4 if accumulated is not None and type(delta) is int else None
            if packet.meta.get("nco_phase_uncertain"):
                accumulated = None
            if accumulated is not None:
                if frequency == 0:
                    pass
                elif frequency is not None and isinstance(packet.duration, Concrete):
                    duration = packet.duration.value
                    spacing = _rt_dequeue_spacing_ns(duration) if packet.meta.get("queue_duration_ns") == 0 else duration
                    accumulated = (accumulated + frequency * spacing) % modulus
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
    state.netzero_window = a.netzero_window if a.netzero_window == b.netzero_window else None
    state.q1_time_ns = a.q1_time_ns if q1_known else Unknown("Q1 arrival is not proven at branch join")
    state.q1_issue_index = a.q1_issue_index - 1
    state.alu_flags = {key: value for key, value in (a.alu_flags or {}).items() if (b.alu_flags or {}).get(key) is value} or None
    if a.alu_flag_cases is not None or b.alu_flag_cases is not None:
        state.alu_flag_cases = ((a.alu_flag_cases or _possible_alu_flags(a.alu_flags))
                                | (b.alu_flag_cases or _possible_alu_flags(b.alu_flags)))
    else:
        state.alu_flag_cases = None
    state.alu_flags_source = instruction.source if state.alu_flags else None
    state.alu_flags_read_hazard = a.alu_flags_read_hazard or b.alu_flags_read_hazard
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
    for name in [*latch_fields, "phase_reset"]:
        if name == "phase_reset" and state.pending_phase_reset is not True:
            state.latched_state.pending_since.pop(name, None)
            state.latched_state.pending_event_ids.pop(name, None)
            continue
        if name not in state.latched_state.pending_since:
            continue
        ids = [probe.latched_state.pending_event_ids.get(name) for probe in probes.values()]
        if ids[0] == ids[1] and ids[0] in entry_event_ids:
            continue
        sources = {path: _source_to_json(probe.latched_state.pending_since[name]) for path, probe in probes.items()}
        value = True if name == "phase_reset" else getattr(state.latched_state, name)
        event = _emit_event(state, lane="state.pending", kind="latched_state_pending", t0=state.rt_time_ns, t1=state.rt_time_ns,
                            duration=Concrete(0), label=f"joined {name}", confidence=_confidence_for_value(value),
                            source=instruction.source, meta={"field": name, "value": _plain_value(value),
                                                            "branch_join_sources": sources, "q1_issue_event_id": q1_issue_event.id})
        state.latched_state.pending_since[name] = instruction.source
        state.latched_state.pending_event_ids[name] = event.id
    if output_unknown:
        latched_sources = {}
        known_phase = (endpoints[0][2] // 4 if not nco_uncertain and endpoints[0] is not None
                       and endpoints[0] == endpoints[1] else None)
        reset_applied = any(event.meta.get("phase_reset") for events in deltas.values() for event in events)
        for name in latch_fields:
            from_reset = name == "phase" and known_phase is not None and reset_applied and state.latched_state.phase is None
            if getattr(state.latched_state, name) is None and not from_reset:
                continue
            latched_sources[name] = {}
            for path, events in deltas.items():
                setter = next((event for event in reversed(events) if event.kind == "latched_state_pending"
                               and event.meta.get("field") == ("phase_reset" if from_reset else name)), None)
                latched_sources[name][path] = _source_to_json(setter.source) if setter is not None else {"before_branch": True}
        latched_at_join = {name: _plain_value(getattr(state.latched_state, name)) for name in latched_sources
                           if name != "phase" or not nco_uncertain}
        if "phase" in latched_at_join and known_phase is not None and "phase" not in state.latched_state.pending_since:
            latched_at_join["phase"] = known_phase
        _emit_event(state, lane="rt.unknown", kind="unknown_region", t0=region.t0, t1=state.rt_time_ns,
                    duration=Concrete(elapsed["taken"]), label="uncertain branch outputs", confidence="unknown",
                    source=instruction.source, meta={"reason": "branch_outputs_unknown", "branch_id": branch_id,
                                                    "output_state_unknown": True, "branch_timing_known": True,
                                                    "nco_phase_uncertain": state.nco_phase_uncertain,
                                                    "pending_phase_reset": state.pending_phase_reset,
                                                    "latched_state_at_join": latched_at_join,
                                                    "latched_sources_at_join": latched_sources,
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
            _begin_execution_step(probe)
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
                    or probe.next_interior_pc is not None
                    or probe.next_pc is None or not probe.stopped and probe.next_pc <= probe.pc):
                return {}
            probe.pc = probe.next_pc
            if len(probe.rt_packets) > 10000:
                return {}
        else:
            return {}
        if (not probe.stopped or probe.events[-1].meta.get("q1_proof_cutoff")
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
    event_ids: Mapping[str, str] | None = None,
) -> list[TimelineEvent]:
    transformed: list[TimelineEvent] = []
    event_ids = event_ids or {event.id: f"{event.id}:branch-{path}" for event in events}
    for event in events:
        meta = dict(event.meta)
        _remap_latched_event_ids(meta, event_ids)
        for key in ("first_iteration_event_ids", "event_ids", "child_loop_event_ids"):
            if key in meta:
                meta[key] = [event_ids.get(event_id, event_id) for event_id in meta[key]]
        if "loop_q1_issue_event_id" in meta:
            issue_id = meta["loop_q1_issue_event_id"]
            meta["loop_q1_issue_event_id"] = event_ids.get(issue_id, issue_id)
        if "child_count_affine" in meta:
            meta["child_count_affine"] = {event_ids.get(event_id, event_id): coefficients
                                          for event_id, coefficients in meta["child_count_affine"].items()}
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
                id=event_ids[event.id],
                lane=f"branch_compare.{path}.{event.lane}",
                label=f"{path}: {event.label}",
                meta=meta,
            )
        )
    return transformed


def _remap_latched_event_ids(meta: dict[str, Any], event_ids: Mapping[str, str]) -> None:
    issue_id = meta.get("q1_issue_event_id")
    if issue_id in event_ids:
        meta["q1_issue_event_id"] = event_ids[issue_id]
    for key in ("applied_setter_event_ids", "captured_setter_event_ids"):
        if key in meta:
            meta[key] = {field: event_ids.get(event_id, event_id) for field, event_id in meta[key].items()}


def _branch_comparison_packets(
    packets: list[RtPacket],
    *,
    branch_id: str,
    path: str,
    event_ids: Mapping[str, str],
) -> list[RtPacket]:
    transformed: list[RtPacket] = []
    for packet in packets:
        meta = dict(packet.meta)
        _remap_latched_event_ids(meta, event_ids)
        packet_id = _branch_comparison_packet_id(packet.id, path)
        if meta.get("rt_packet_id") == packet.id:
            meta["rt_packet_id"] = packet_id
        meta["branch_comparison_branch_id"] = branch_id
        meta["branch_comparison_path"] = path
        transformed.append(
            replace(
                packet,
                id=packet_id,
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
    path_event_ids: Mapping[str, str],
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
                related_events=[path_event_ids.get(event_id, event_id) for event_id in diagnostic.related_events],
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
    state: AnalysisState, body: list[Instr], evidence: _LoopEvidence, count: int,
    counter_reg: str | None, symbols: SymbolTable, *, children: Mapping[int, TimelineEvent] | None = None,
    child_count_slopes: Mapping[int, int] | None = None,
) -> dict[str, Any] | None:
    child_counters = {}
    child_wait_counts = {}
    for edge_pc, child in (children or {}).items():
        child_body = [item for item in body if child.meta["start_pc"] <= item.pc < edge_pc]
        if not child.meta["period_invariant"] or any(item.op not in {"nop", "wait"} for item in child_body):
            return None
        child_counter = child.meta["counter_reg"]
        child_count = child.meta["count"]
        child_slope = (child_count_slopes or {}).get(edge_pc, 0)
        child_waits = [item for item in child_body if item.op == "wait"]
        if (child_count > 1 or child_slope) and any(_physical_read_register(item.args[0], state) == child_counter
                                                  for item in child_waits):
            return None
        child_counters[edge_pc] = child_counter
        child_wait_counts.update({item.pc: (child_count, child_slope) for item in child_waits})
    if count < 2 or state.conditional_enable != Concrete(0) or any(
        item.pc not in child_counters and item.op not in {"nop", "move", "add", "sub", "wait"} for item in body
    ):
        return None
    sources = {item.source for item in body}
    if any(item.source in sources and item.category in {
        "register_not_ready", "invalid_argument_count", "invalid_argument_type", "invalid_argument_value"
    } for item in evidence.diagnostics):
        return None
    issues = {event.source: event for event in evidence.events
              if event.kind == "q1_issue" and "preview_source_event_id" not in event.meta}
    if not body or any(item.source not in issues for item in body):
        return None
    writes: set[str] = set()
    reads = set()
    samples: dict[str, list[tuple[SourceLocation, int | None]]] = {}
    for item in body:
        if item.pc in child_counters:
            writes.add(child_counters[item.pc])
            continue
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
        if item.pc in child_counters:
            ready_definitions.append((index + 1, child_counters[item.pc]))
            continue
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
    wait_counts = {}
    for index, item in enumerate(body):
        for ready, register, value in pending:
            if ready <= index:
                values[register] = value
        pending = [entry for entry in pending if entry[0] > index]
        if item.pc in child_counters:
            pending.append((index + 1, child_counters[item.pc], (0, 0)))
            continue
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
            packet = evidence.packets.get(packet_id)
            if not (_valid_rt_duration(low) and _valid_rt_duration(high)) or packet is None or packet.duration != Concrete(intercept):
                return None
            if low == 0 and slope and (not children or not _valid_rt_duration(abs(slope))):
                # The closest positive operand must also be a legal wait.
                return None
            packets[packet_id] = {"intercept": intercept, "slope": slope}
            wait_counts[packet_id] = child_wait_counts.get(item.pc, (1, 0))
        else:
            modulus = _CLASSICAL_REGISTER_MASK + 1
            destination = _register_name(item.args[1 if item.op == "move" else 2], state)
            if destination is None:
                return None
            quotient = intercept // modulus
            if low // modulus != high // modulus:
                # A final write can wrap after the last dwell. That value may
                # leave the loop, but it cannot participate in a later body read.
                penultimate = intercept + slope * (count - 2)
                if (not children or penultimate // modulus != quotient
                        or any(destination == register for later in body[index + 1:]
                               for _, register in _register_read_operands(later, state))):
                    return None
            result = intercept - quotient * modulus, slope
            pending.append((index + 2, destination, result))
    for ready, register, value in pending:
        if ready > len(body) + 1:
            return None
        values[register] = value
    if any(values.get(register) != (initial + step, step) for register, (initial, step) in induction.items()):
        return None
    intercept = slope = quadratic = 0
    endpoint_spacing: dict[int, int] = {}
    for packet_id, coefficients in packets.items():
        dwell = coefficients["intercept"]
        dwell_slope = coefficients["slope"]
        repetitions, repetition_slope = wait_counts[packet_id]
        if not dwell_slope:
            dwell = _rt_dequeue_spacing_ns(dwell)
        elif dwell == 0 or dwell + dwell_slope * (count - 1) == 0:
            endpoint = 0 if dwell == 0 else count - 1
            spacing = _rt_dequeue_spacing_ns(0) * (repetitions + repetition_slope * endpoint)
            endpoint_spacing[endpoint] = endpoint_spacing.get(endpoint, 0) + spacing
        intercept += dwell * repetitions
        slope += dwell_slope * repetitions + dwell * repetition_slope
        quadratic += dwell_slope * repetition_slope
    index_sum = count * (count - 1) // 2
    square_sum = count * (count - 1) * (2 * count - 1) // 6
    total = count * intercept + slope * index_sum + quadratic * square_sum + sum(endpoint_spacing.values())
    period = {"period_affine": {"intercept": intercept, "slope": slope}}
    if quadratic or endpoint_spacing:
        # The sum is exact for the full count. Keep only a short set of period samples.
        period = {"nonlinear_period": True,
                  "iteration_durations": [intercept + slope * index + quadratic * index**2 + endpoint_spacing.get(index, 0)
                                          for index in range(min(count, 3))]}
    return {**period, "total_duration": value_to_json(Concrete(total)),
            "rt_packet_affine": packets,
            "final_registers": {register: (values[register][0] + values[register][1] * (count - 1)) & _CLASSICAL_REGISTER_MASK
                                for register in writes
                                if register not in child_counters.values()}}


def _bounded_loop_total(
    state: AnalysisState, body: list[Instr], evidence: _LoopEvidence, count: int,
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
           for item in evidence.diagnostics):
        return None
    issues = {event.source: event for event in evidence.events if event.kind == "q1_issue"}
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
                        if not (_valid_rt_duration(low) and _valid_rt_duration(high)):
                            return None
                        if low == 0 < high:
                            return None
                        total = plus(total, result if low else (_rt_dequeue_spacing_ns(0), {}))
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


def _supported_loop_evaluation_instruction(item: Instr) -> bool:
    category = get_instruction_spec(item.op).category
    return (item.op in {"nop", "wait", "cmp", "test"}
            or category == "classical" and bool(_register_destination_argument_indexes(item.op))
            or category == "branch" and item.op != "loop")


def _evaluate_loop_iterations(
    state: AnalysisState, body: list[Instr], edge: Instr, evidence: _LoopEvidence,
    count: int, counter_reg: str | None, symbols: SymbolTable,
) -> tuple[dict[str, Any], dict[str, tuple[Any, ...]]] | None:
    branch_pcs = {item.pc for item in body if item.op != "loop" and get_instruction_spec(item.op).category == "branch"}
    if not 2 <= count <= _CLASSICAL_REGISTER_MASK or counter_reg is None or any(
        not _supported_loop_evaluation_instruction(item) for item in body
    ):
        return None
    sources = {item.source for item in body} | {edge.source}
    if any(item.source in sources and (item.severity == "error" or item.category == "register_not_ready")
           for item in evidence.diagnostics):
        return None
    writes = set().union(*(_register_write_operands(item, state) for item in body))
    if counter_reg in writes:
        return None
    first_issues = [event for event in evidence.events if event.kind == "q1_issue"]
    if branch_pcs and (not first_issues or first_issues[0].source != body[0].source or any(
        event.meta.get("branch_decision") not in (None, "concrete") for event in first_issues
    )):
        return None
    first_packets = list(evidence.packets.values())
    wait_count = (sum(event.meta.get("op") == "wait" for event in first_issues) if branch_pcs
                  else sum(item.op == "wait" for item in body))
    if (len(first_packets) != wait_count
            or any(not isinstance(packet.duration, Concrete) or not _valid_rt_duration(packet.duration.value)
                   for packet in first_packets)):
        return None
    series = {packet.id: [packet.duration.value] for packet in first_packets}
    periods = [sum(_rt_dequeue_spacing_ns(packet.duration.value) for packet in first_packets)]
    probe = replace(state, registers=dict(state.registers), register_bounds=dict(state.register_bounds),
                    register_known_bits=dict(state.register_known_bits), register_value_origins=dict(state.register_value_origins),
                    latched_state=deepcopy(state.latched_state), pending_register_writes=deepcopy(state.pending_register_writes),
                    events=[], rt_packets=[], diagnostics=[], register_history=[], register_provenance={},
                    metadata=deepcopy(state.metadata), executed_pcs=set(state.executed_pcs), branch_assumptions={},
                    current_resolved_args=[], stopped=False)
    probe.metadata["execution"] = {"mode": "concrete"}
    probe.q1_issue_index += 1
    _apply_ready_register_writes(probe)
    probe.registers[counter_reg] = Concrete(count - 1)
    _set_alu_flags(probe, edge, _sub_flags(count, 1))
    live_in = set()
    defined = set()
    for item in body:
        live_in.update(register for _, register in _register_read_operands(item, state) if register not in defined)
        defined.update(_register_write_operands(item, state))

    def signature() -> dict[str, tuple[str, bool] | None]:
        scope = _scope(symbols, probe)
        return {register: _operand_value_origin(Arg("reg", register, register), scope.get(register, Unknown(register)), probe)
                for register in live_in}

    def execute(item: Instr, concrete_branch: bool = False) -> list[RtPacket] | None:
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
        probe.next_interior_pc = None
        probe.current_resolved_args = []
        probe.execution_mode = "concrete" if concrete_branch else "analysis"
        _execute_instruction(probe, item, symbols, {}, {})
        probe.execution_mode = "analysis"
        if probe.stopped or probe.next_interior_pc is not None or any(
            item.severity == "error" or item.category == "register_not_ready" for item in probe.diagnostics
        ):
            return None
        if any(not isinstance(packet.duration, Concrete) or not _valid_rt_duration(packet.duration.value)
               for packet in probe.rt_packets):
            return None
        packets = list(probe.rt_packets)
        probe.q1_issue_index += 1
        _apply_ready_register_writes(probe)
        return packets

    work = 0
    repeats = False
    layout_invariant = True
    first_layout = [(issue.source, issue.duration, issue.meta.get("branch_taken")) for issue in first_issues]
    for _ in range(1, count):
        before = signature() if not branch_pcs else None
        packets = []
        layout = []
        pc = body[0].pc if body else edge.pc
        while pc < edge.pc:
            if work >= 10000:
                return None
            issued = execute(state.instructions_by_pc[pc], pc in branch_pcs)
            if issued is None or probe.next_pc is None or not pc < probe.next_pc <= edge.pc:
                return None
            packets.extend(issued)
            if branch_pcs:
                issue = next(event for event in probe.events if event.kind == "q1_issue")
                layout.append((issue.source, issue.duration, issue.meta.get("branch_taken")))
            work += 1
            pc = probe.next_pc
        if work >= 10000 or execute(edge, True) is None:
            return None
        work += 1
        if branch_pcs:
            layout_invariant = layout_invariant and layout == first_layout and (
                [packet.duration for packet in packets] == [packet.duration for packet in first_packets]
            )
        else:
            if len(packets) != len(first_packets):
                return None
            for original, packet in zip(first_packets, packets):
                series[original.id].append(packet.duration.value)
        periods.append(sum(_rt_dequeue_spacing_ns(packet.duration.value) for packet in packets))
        if not branch_pcs and before == signature():
            repeats = True
            break
    total = sum(periods) + (count - len(periods)) * periods[-1]
    facts = {register: (probe.registers.get(register, Unknown(register)), probe.register_bounds.get(register),
                        probe.register_known_bits.get(register), probe.register_value_origins.get(register))
             for register in writes}
    if branch_pcs:
        q1_hidden = subtract_values(probe.q1_time_ns, state.q1_time_ns)
        if not isinstance(q1_hidden, Concrete):
            return None
        # Replay cannot clone the first iteration's packets when Q1 control flow can change.
        return ({"total_duration": value_to_json(Concrete(total)), "iteration_durations": periods,
                 "branch_layout_invariant": layout_invariant, "q1_hidden_ns": q1_hidden.value}, facts)
    return ({"total_duration": value_to_json(Concrete(total)), "iteration_durations": periods,
             "rt_packet_durations": series, "repeat_last_iteration": repeats}, facts)


def _emit_compact_loop(
    state: AnalysisState,
    instruction: Instr,
    *,
    q1_issue_event: TimelineEvent,
    start_pc: int | None,
    target_label: str | None,
    counter_reg: str | None,
    count: Value | None,
    forever: bool,
    min_visible_iterations: int = 1,
    symbols: SymbolTable | None = None,
) -> bool:
    if start_pc is None or start_pc > instruction.pc:
        return False
    start_instruction = state.instructions_by_pc.get(start_pc)
    if start_instruction is None:
        return False
    body_instructions = [state.instructions_by_pc[pc] for pc in range(start_pc, instruction.pc + 1)]
    evidence = _compact_loop_evidence(state, body_instructions, q1_issue_event)
    if evidence is None:
        _end_analysis_at_issue(
            state, q1_issue_event, "analytical_revisited_compact_loop",
            "The compact loop body was revisited by a backward branch and its first iteration cannot be isolated.",
        )
        return True
    child = next((event for event in evidence.children if not _nested_child_elapsed_known(event)), None)
    if child is not None:
        child_issue = next(event for event in evidence.events if event.id == child.meta["loop_q1_issue_event_id"])
        unknown_count = type(child.meta["count"]) is not int
        _end_analysis_at_issue(
            state, child_issue, "analytical_nested_loop_count_unknown" if unknown_count else "analytical_nested_loop_work_unknown",
            "The child loop count is unresolved." if unknown_count else "The child's elapsed loop work is unresolved.",
            rt_boundary=child.t1 if isinstance(child.t1, Concrete) else child.t0,
        )
        return True
    nested_proof = _prove_nested_loop_children(state, body_instructions[:-1], evidence, counter_reg, count, symbols)
    if nested_proof == "varying_count":
        _end_unproved_nested_loop(
            state, instruction, q1_issue_event, count, symbols,
            "The changing child loop count or its repeated work could not be proven.",
        )
        return True
    if nested_proof is None and evidence.revisited:
        _end_analysis_at_issue(
            state, q1_issue_event, "analytical_revisited_compact_loop",
            "The child loops in this compact-loop visit do not prove the parent period.",
        )
        return True
    nested_children = nested_proof.children if nested_proof is not None else {}
    if target_label is None and start_pc < instruction.pc:
        first_issue = next((event for event in evidence.events if event.kind == "q1_issue"), None)
        if first_issue is None or first_issue.source != start_instruction.source:
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

    loop_q1_jump_runtime_ns = 16 if instruction.op == "jmp" else 24
    loop_q1_continue_runtime_ns = 4
    first_loop_edge_runtime_ns = loop_q1_jump_runtime_ns
    # The docs also describe loop as lowering to sub + jnz; use that behavior when its prose conflicts.
    if not forever and isinstance(count, Concrete) and count.value == 1:
        q1_issue_event = _revise_q1_issue_duration(state, q1_issue_event, loop_q1_continue_runtime_ns)
        first_loop_edge_runtime_ns = loop_q1_continue_runtime_ns

    preview_events = evidence.events
    t0, t1, duration = _loop_timing(preview_events, current_rt_time=state.rt_time_ns)
    has_hidden_iterations = forever or not isinstance(count, Concrete) or count.value > 1
    trigger_epoch_stride = state.rt_trigger_epoch - min(
        (event.meta.get("rt_trigger_epoch", 0) for event in preview_events if event.meta.get("trigger_arrival_unknown")),
        default=state.rt_trigger_epoch,
    )
    written_registers: set[str] = set()
    if has_hidden_iterations:
        for body_instruction in body_instructions:
            written_registers.update(_register_write_operands(body_instruction, state))
            if body_instruction.op == "loop" and body_instruction.args:
                body_counter = _register_name(body_instruction.args[0], state)
                if body_counter is not None:
                    written_registers.add(body_counter)
    single_path = all(item.pc in nested_children or item.op == "nop" or get_instruction_spec(item.op).category in {"classical", "latched", "rt"}
                      for item in body_instructions[:-1])
    has_body_branch = any(item.op != "loop" and get_instruction_spec(item.op).category == "branch"
                          for item in body_instructions[:-1])
    invariant_writes: set[str] = set()
    varying_by_source: dict[SourceLocation, set[str]] = {}
    invariant_duration_pcs: list[int] = []
    varying_duration = False
    read_hazards = {item.source for item in evidence.diagnostics if item.category == "register_not_ready"}
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
        child = nested_children.get(item.pc)
        writes = {child.meta["counter_reg"]} if child is not None else _register_write_operands(item, state)
        reads = {register for _, register in _register_read_operands(item, state)}
        if child is not None or (single_path and item.source not in read_hazards
                                 and spec.category == "classical" and reads.isdisjoint(varying)):
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
    if has_hidden_iterations and condition_setters and not conditional_loop_timing_unknown:
        # Packets before the first setter inherit the preceding iteration's
        # last setter. The first packet's selected duration may not repeat.
        early_sources = {item.source for item in body_instructions if item.pc < condition_setters[0].pc}
        early_ids = {event.meta.get("rt_packet_id") for event in preview_events
                     if event.kind == "q1_issue" and event.source in early_sources}
        next_decision = _conditional_decision(
            state.conditional_enable, state.conditional_mask, state.conditional_operator,
            enable_bounds=state.conditional_enable_bounds, operator_bounds=state.conditional_operator_bounds,
            enable_known_bits=state.conditional_enable_known_bits, operator_known_bits=state.conditional_operator_known_bits,
        )
        conditional_loop_timing_unknown = any(
            next_decision is None or (packet.meta.get("conditional_skipped") is True) != (not next_decision)
            or (not next_decision and packet.duration != state.conditional_else_duration)
            for packet_id, packet in evidence.packets.items() if packet_id in early_ids
        )
    if forever and target_label is None and (
        not single_path or not isinstance(duration, Concrete) or varying_duration
        or conditional_loop_timing_unknown or state.conditional_time_unknown
        or any(event.kind == "unknown_region" or event.meta.get("trigger_arrival_unknown") for event in preview_events)
    ):
        return False
    affine_timing = None
    if (nested_children and nested_proof is not None and varying_duration
            and isinstance(count, Concrete) and isinstance(duration, Concrete) and symbols is not None
            and not (counter_modified or conditional_loop_timing_unknown or state.conditional_time_unknown)):
        candidate = _affine_loop_timing(
            state, body_instructions[:-1], evidence, count.value, counter_reg, symbols, children=nested_children,
            child_count_slopes=nested_proof.count_slopes,
        )
        if candidate is not None:
            first_period = (candidate["iteration_durations"][0] if "iteration_durations" in candidate
                            else candidate["period_affine"]["intercept"])
            if first_period == duration.value:
                affine_timing = candidate
                varying_duration = False
    if evidence.revisited and nested_children and (
        not isinstance(duration, Concrete) or varying_duration or conditional_loop_timing_unknown or state.conditional_time_unknown
    ):
        _end_analysis_at_issue(
            state, q1_issue_event, "analytical_revisited_compact_loop",
            "The current child loops do not establish invariant parent timing.",
        )
        return True
    if nested_proof is not None and nested_proof.count_slopes:
        first_q1_body = _loop_q1_body_period(_loop_q1_period(preview_events, q1_issue_event), first_loop_edge_runtime_ns)
        if (isinstance(count, Concrete) and isinstance(duration, Concrete) and isinstance(first_q1_body, Concrete)
                and not (varying_duration or conditional_loop_timing_unknown or state.conditional_time_unknown)):
            affine_timing = _affine_nested_loop_timing(
                evidence, nested_proof, count.value, duration.value, first_q1_body.value,
                loop_q1_jump_runtime_ns, loop_q1_continue_runtime_ns,
                rt_timing=affine_timing,
            )
        if affine_timing is None:
            _end_unproved_nested_loop(
                state, instruction, q1_issue_event, count, symbols,
                "The changing child loop count does not establish exact parent timing.",
            )
            return True
    state.loop_counter += 1
    for event in preview_events:
        _add_loop_preview_membership(event, loop_id)
        event.meta.setdefault("loop_iteration_index", 0)
    if conditional_loop_timing_unknown:
        # Later iterations may gate RT work differently from the first one.
        duration = Unknown(f"conditional execution in compact loop {loop_id}")
        t1 = add_values(t0, duration)
        state.conditional_time_unknown = True
    evaluated_facts = {}
    if (affine_timing is None and not forever and isinstance(count, Concrete)
            and not counter_modified and symbols is not None and not conditional_loop_timing_unknown):
        affine_timing = _affine_loop_timing(state, body_instructions[:-1], evidence, count.value, counter_reg, symbols)
        if affine_timing is None:
            evaluated = _evaluate_loop_iterations(state, body_instructions[:-1], instruction, evidence,
                                                  count.value, counter_reg, symbols)
            if evaluated is not None:
                affine_timing, evaluated_facts = evaluated
        if affine_timing is None:
            affine_timing = _bounded_loop_total(state, body_instructions[:-1], evidence, count.value, counter_reg, symbols)
        if affine_timing is not None:
            first_period = Concrete(affine_timing["iteration_durations"][0] if "iteration_durations" in affine_timing
                                    else affine_timing["period_affine"]["intercept"])
            if isinstance(duration, Concrete) and duration != first_period:
                affine_timing = None
                evaluated_facts = {}
            else:
                duration = first_period
                t1 = add_values(t0, duration)
                if affine_timing.get("total_only"):
                    state.rt_time_ns = t1
                varying_duration = False
    affine_durations_vary = bool(affine_timing and (
        affine_timing.get("total_only") or affine_timing.get("child_count_affine")
        or any(item["slope"] for item in affine_timing.get("rt_packet_affine", {}).values())
        or any(len(set(items)) > 1 for items in affine_timing.get("rt_packet_durations", {}).values())))
    branch_timing_unknown = has_hidden_iterations and has_body_branch and affine_timing is None
    branch_layout_varies = affine_timing is not None and affine_timing.get("branch_layout_invariant") is False
    if varying_duration and not forever and not branch_timing_unknown:
        duration = Unknown(f"RT duration changes in compact loop {loop_id}")
        t1 = add_values(t0, duration)
    q1_period = _loop_q1_period(preview_events, q1_issue_event)
    q1_body_period = _loop_q1_body_period(q1_period, first_loop_edge_runtime_ns)
    q1_cost = q1_body_period if nested_children else _q1_cost(preview_events)
    loop_preview_key = _loop_preview_key(state, start_instruction.source, instruction.source)
    visible_iteration_count = _visible_loop_iteration_count(state, loop_preview_key, count=count, forever=forever)
    min_visible = max(1, min(int(min_visible_iterations), LOOP_PREVIEW_MAX_VISIBLE_ITERATIONS))
    visible_iteration_count = max(visible_iteration_count, min_visible)
    if not forever and isinstance(count, Concrete):
        visible_iteration_count = min(visible_iteration_count, max(1, count.value))
    if conditional_loop_timing_unknown or varying_duration or affine_durations_vary or branch_timing_unknown or branch_layout_varies:
        visible_iteration_count = 1
    shown_iterations = list(range(visible_iteration_count))
    preview_policy = "first_iteration_only" if visible_iteration_count == 1 else "requested_iterations"
    count_display = "forever" if forever else _display_count(count)
    counter_before = _plain_value(count) if count is not None else None
    counter_after = _counter_after(count) if count is not None else None
    preview_ids = [event.id for event in preview_events]
    preview_iteration_event_ids: dict[str, list[str]] = {"0": preview_ids}
    target_meta = {} if target_label is not None else {
        key: q1_issue_event.meta[key] for key in ("target_pc", "compiled_target_pc") if key in q1_issue_event.meta
    }
    period = (Unknown(f"branch timing in compact loop {loop_id} is not proven")
              if branch_timing_unknown and not forever else duration)
    period_invariant = not (varying_duration or conditional_loop_timing_unknown or branch_timing_unknown)
    if affine_timing is not None:
        if "iteration_durations" in affine_timing:
            period_invariant = (period_invariant and not affine_timing.get("nonlinear_period")
                                and len(set(affine_timing["iteration_durations"])) == 1)
        else:
            period_invariant = period_invariant and affine_timing["period_affine"]["slope"] == 0

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
                **target_meta,
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
        confidence="unknown" if state.conditional_time_unknown or varying_duration or branch_timing_unknown else
        "exact" if forever else _confidence_for_value(count),
        source=start_instruction.source,
        meta={
            "loop_id": loop_id,
            "start_pc": start_pc,
            "end_pc": instruction.pc,
            "label": target_label,
            **target_meta,
            "counter_reg": counter_reg,
            "count": count_display,
            "period": value_to_json(period),
            "period_invariant": period_invariant,
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
    if branch_timing_unknown and forever:
        loop_block.meta["period_basis"] = "first_iteration"
    if affine_timing is not None:
        # A nested sum proves elapsed time, but its packet sequence is still compact.
        loop_block.meta.update({key: value for key, value in affine_timing.items()
                                if key not in {"final_registers", "nonlinear_period"}
                                and not (nested_children and key == "rt_packet_affine")})
    if nested_children:
        loop_block.meta["child_loop_event_ids"] = [
            child.id for child in nested_children.values()
            if not any(other.meta["start_pc"] <= child.meta["start_pc"] and child.meta["end_pc"] < other.meta["end_pc"]
                       for other in nested_children.values())
        ]
        if (isinstance(count, Concrete) and isinstance(period, Concrete)
                and loop_block.meta["period_invariant"]):
            loop_block.meta["total_duration"] = value_to_json(multiply_value(period, count.value))
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
            "q1_cost": value_to_json(q1_cost),
            "register_summary": {
                "counter_reg": counter_reg,
                "before": counter_before,
                "after": counter_after,
            },
            "branch": "backward",
            "queue_slack_summary": "not_computed_yet",
        },
    )
    preview_setter_values = {}
    if visible_iteration_count > 1 and uncertain_setters:
        instructions_by_source = {item.source: item for item in body_instructions}
        unknown = value_to_json(Unknown("setter value in a compact loop preview is not proven"))
        for setter_id in uncertain_setters:
            setter = preview_by_id[setter_id]
            value = setter.meta["value"]
            item = instructions_by_source.get(setter.source)
            if isinstance(value, list) and item is not None:
                varying = varying_by_source.get(setter.source, written_registers)
                unknown_indexes = {index for index, register in _register_read_operands(item, state) if register in varying}
                value = [unknown if index in unknown_indexes else entry for index, entry in enumerate(value)]
            else:
                value = unknown
            preview_setter_values[setter_id] = value
    for iteration_index in range(1, visible_iteration_count):
        shifted_events = _materialize_loop_iteration_preview(
            state,
            preview_events,
            loop_id=loop_id,
            iteration_index=iteration_index,
            rt_period=duration,
            q1_period=q1_period,
            trigger_epoch_stride=trigger_epoch_stride,
            preview_policy=preview_policy,
            shown_iterations=shown_iterations,
            written_registers=written_registers,
            setter_values=preview_setter_values,
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
                "q1_cost": value_to_json(q1_cost),
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
    if has_hidden_iterations and any(item.op in _NETZERO_PULSE_OPS for item in body_instructions):
        window = state.netzero_window
        state.netzero_window = None
        if (window is not None and window.source in varying_by_source and single_path
                and (forever or isinstance(count, Concrete))
                and not conditional_rt_sources and not affine_durations_vary and isinstance(duration, Concrete)):
            first_pulse = next((event for event in preview_events if event.kind in _NETZERO_PULSE_OPS), None)
            if first_pulse is not None and isinstance(first_pulse.t0, Concrete):
                _diagnose_netzero_gap(state, window, first_pulse, first_pulse.t0.value + duration.value)
                if isinstance(count, Concrete) and window.maxt_register not in varying_by_source[window.source]:
                    state.netzero_window = replace(window, start_ns=window.start_ns + (count.value - 1) * duration.value)
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
        if has_hidden_iterations and trigger_epoch_stride:
            hidden_iterations = count.value - 1 if isinstance(count, Concrete) else 1
            state.rt_trigger_epoch += trigger_epoch_stride * hidden_iterations
        _advance_compact_loop_fallthrough_state(
            state,
            loop_id=loop_id,
            counter_reg=counter_reg,
            count=count,
            rt_period=period,
            q1_body_period=period if branch_timing_unknown else q1_body_period,
            loop_q1_jump_runtime_ns=loop_q1_jump_runtime_ns,
            loop_q1_continue_runtime_ns=loop_q1_continue_runtime_ns,
            rt_total=Concrete(affine_timing["total_duration"]["value"]) if affine_timing is not None else None,
            q1_hidden_ns=affine_timing.get("q1_hidden_ns") if affine_timing is not None else None,
        )
        if branch_timing_unknown and counter_reg is not None:
            state.registers[counter_reg] = Unknown(f"{counter_reg} after unproved branch paths in compact loop {loop_id}")
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
                meta={"op": "loop", "loop_id": loop_id, "loop_complete": not branch_timing_unknown},
            )
    if instruction.op == "loop":
        counter_finishes = (isinstance(count, Concrete) and count.value > 0
                            and (count.value == 1 or not counter_modified and (
                                affine_timing is not None or all(item.pc in nested_children or get_instruction_spec(item.op).category != "branch"
                                                                 for item in body_instructions[:-1]))))
        flags = _sub_flags(1, 1) if counter_finishes else None
        _set_alu_flags(state, instruction, flags)
    if branch_timing_unknown and not forever:
        assumed_fallthrough = {event.source for event in preview_events if event.kind == "q1_issue"
                               and event.meta.get("branch_decision") == "runtime_dependent"
                               and event.meta.get("assumption_source") in {"branch_policy", "branch_assumption"}
                               and event.meta.get("assumed_branch_path") in {"fallthrough", "not_taken"}}
        if (not isinstance(count, Concrete) or symbols is None or not _loop_branch_paths_stay_in_body(
            state, body_instructions[:-1], instruction.pc, symbols, assumed_fallthrough,
        )):
            state.stopped = True
        elif assumed_fallthrough:
            loop_block.meta["fallthrough_assumed"] = True
    return True


def _loop_branch_paths_stay_in_body(
    state: AnalysisState, body: list[Instr], edge_pc: int, symbols: SymbolTable,
    assumed_fallthrough: set[SourceLocation],
) -> bool:
    # Keep the nominal suffix if every permitted branch outcome reaches the edge.
    # An explicit fallthrough scenario may include unknown LINQ receipts.
    for item in body:
        if assumed_fallthrough and item.op in {"fb_pop_data", "fb_pull_data"}:
            continue
        if not _supported_loop_evaluation_instruction(item):
            return False
        if get_instruction_spec(item.op).category != "branch" or item.source in assumed_fallthrough:
            continue
        if not item.args:
            return False
        operand = item.args[-1]
        if _physical_read_register(operand, state) is not None:
            return False
        target = _branch_target_arg(operand, state, symbols)
        label = str(target.value) if target.kind == "label" else None
        pc = _resolved_forward_branch_target_pc(state, item, label, _numeric_branch_target_pc(target, state))
        if pc is None or pc > edge_pc:
            return False
    return True


def _loop_target_register_is_stable(state: AnalysisState, register: str | None, start_pc: int, end_pc: int) -> bool:
    if register is None:
        return True
    for pc in range(start_pc, end_pc):
        item = state.instructions_by_pc[pc]
        if (get_instruction_spec(item.op).category == "unknown"
                or register in _register_write_operands(item, state)
                or item.op == "loop" and item.args and _register_name(item.args[0], state) == register):
            return False
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


def _preview_latched_snapshots(
    state: AnalysisState, events: list[TimelineEvent], event_ids: Mapping[str, str],
    previous_ids: Mapping[str, str], setter_values: Mapping[str, Any],
) -> dict[str, tuple[str, dict[str, tuple[str | None, Any]], dict[str, Any]]]:
    """Follow setter origins through a copied iteration without executing its packets."""
    one_shots = ("phase_reset", "phase_delta")
    unknown = value_to_json(Unknown("setter value in a compact loop preview is not proven"))
    setters = {event.id: event for event in events if event.kind == "latched_state_pending"
               and event.meta["field"] not in {"set_cond", "set_time_ref"}}
    pending = {}
    for field, source_id in state.latched_state.pending_event_ids.items():
        value = (state.pending_phase_reset if field == "phase_reset"
                 else _plain_value(getattr(state.latched_state, field)))
        if source_id in previous_ids:
            value = setter_values.get(source_id, value)
        pending[field] = (previous_ids.get(source_id, source_id), value)
    held = {field: _plain_value(getattr(state.latched_state, field)) or 0 for field in ("frequency", "phase")}
    for setter in setters.values():
        field = setter.meta["field"]
        if field in held and setter.id in previous_ids:
            held[field] = setter_values.get(setter.id, setter.meta["value"])

    known_packets = {packet.id for packet in state.rt_packets if isinstance(packet.duration, Concrete)}
    snapshots = {}
    for event in events:
        if event.id in setters:
            field = event.meta["field"]
            value = setter_values.get(event.id, event.meta["value"])
            pending[field] = (event_ids[event.id], value)
            if field in held:
                held[field] = value
        elif event.kind == "q1_issue" and "captured_phase_reset" in event.meta:
            if event.meta.get("conditional_skipped"):
                effect = "captured"
            elif event.meta.get("conditional_execution_unknown") or event.meta.get("rt_packet_id") not in known_packets:
                effect = "unknown"
            else:
                effect = "applied"
            nco = {}
            if effect == "applied" and pending.get("phase_reset", (None, False))[1] is True:
                nco = {**held, "phase": held["phase"] if "phase" in pending else 0}
                if "phase_delta" in pending:
                    nco["phase_delta"] = pending["phase_delta"][1]
            snapshots[event.id] = (effect, dict(pending), nco)
            if effect == "applied":
                pending.clear()
            elif effect == "unknown":
                pending = dict.fromkeys(pending, (None, unknown))
            for field in one_shots:
                pending.pop(field, None)
    return snapshots


def _materialize_loop_iteration_preview(
    state: AnalysisState,
    preview_events: list[TimelineEvent],
    *,
    loop_id: str,
    iteration_index: int,
    rt_period: Value,
    q1_period: Value,
    trigger_epoch_stride: int,
    preview_policy: str,
    shown_iterations: list[int],
    written_registers: set[str],
    setter_values: Mapping[str, Any],
) -> list[TimelineEvent]:
    shifted_events: list[TimelineEvent] = []
    event_ids = {event.id: f"{event.id}:loop-{loop_id}-iter-{iteration_index}" for event in preview_events}
    previous_ids = ({event.id: f"{event.id}:loop-{loop_id}-iter-{iteration_index - 1}" for event in preview_events}
                    if iteration_index > 1 else {})
    snapshots = _preview_latched_snapshots(state, preview_events, event_ids, previous_ids, setter_values)
    for event in preview_events:
        period = q1_period if event.kind == "q1_issue" else rt_period
        meta = dict(event.meta)
        label, confidence = event.label, event.confidence
        if trigger_epoch_stride and (event.lane.startswith("rt.") or event.lane == "state.pending") and "fault" not in meta:
            meta["rt_trigger_epoch"] = meta.get("rt_trigger_epoch", 0) + iteration_index * trigger_epoch_stride
        if "next_dequeue_not_before_ns" in meta and isinstance(rt_period, Concrete):
            meta["next_dequeue_not_before_ns"] += iteration_index * rt_period.value
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
        if event.id in setter_values:
            meta["value"] = setter_values[event.id]
            confidence = "unknown"
        issue_id = event.id if event.kind == "q1_issue" else event.meta.get("q1_issue_event_id")
        if issue_id in snapshots and "captured_phase_reset" in meta:
            effect, pending, nco = snapshots[issue_id]
            meta["captured_phase_reset"] = "phase_reset" in pending
            meta.pop("phase_reset", None)
            meta.pop("nco_state_at_issue", None)
            if nco:
                meta.update(phase_reset=True, nco_state_at_issue=nco)
            if event.kind == "marker_state":
                if "marker" not in pending:
                    continue
                meta["value"] = pending["marker"][1]
                label = f"marker {meta['value']}" if type(meta["value"]) is int else "marker unknown"
                if type(meta["value"]) is not int:
                    confidence = "unknown"
            elif event.kind != "q1_issue" and effect != "unknown":
                state_key = f"{effect}_state"
                ids_key = f"{effect}_setter_event_ids"
                if pending:
                    meta[state_key] = {field: value for field, (_, value) in pending.items()}
                    meta[ids_key] = {field: source_id for field, (source_id, _) in pending.items() if source_id is not None}
                else:
                    meta.pop(state_key, None)
                    meta.pop(ids_key, None)
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
            label=label,
            confidence=confidence,
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
    target_label: str | None,
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
    state.alu_flag_cases = None
    state.alu_flags_source = instruction.source if flags is not None else None
    state.alu_flags_read_hazard = state.analysis_back_edge_limit is not None and bool(state.pending_register_writes) and any(
        _pending_register_write(state, register) is not None for _, register in _register_read_operands(instruction, state)
    )


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
            if product is not None:
                # Reference interpretation: overflow refers to the 32-bit input range.
                # Hardware flag parity remains unverified.
                overflow = not (-(2**31) <= product < 2**31 if signed else 0 <= product < 2**32)
                flags = {"zf": product == 0, "nf": bool(product & (1 << 63)), "cf": overflow, "of": overflow}
            else:
                flags = ({"zf": False} if product_bounds[0] > 0 or product_bounds[1] < 0 or known_bits[0] else None)
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
    if not state.record_trace:
        return {}
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
    if not state.record_trace:
        return {}
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
    if not state.record_trace:
        return {}
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
    register = _register_name(arg, state)
    if register is None:
        return value
    # Decode only the value sent to the latch. Register traces keep the raw word.
    # https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#latched-instructions
    if isinstance(value, Concrete):
        if op in _AWG_PAIR_SETTERS or op == "set_freq":
            return Concrete(_signed_32_bit_register_value(value.value))
        if op == "set_mrk":
            return Concrete(value.value & 0xF)
    elif op == "set_mrk" and isinstance(value, Symbolic) and _pending_register_write(state, register) is None:
        ones, zeros = _operand_known_bits(arg, value, state)
        if (ones | zeros) & 0xF == 0xF:
            return Concrete(ones & 0xF)
    return value


def _unsupported_register_operand(
    state: AnalysisState, instruction: Instr, scope: SymbolTable, *, condition_disabled: bool = False,
) -> Diagnostic | None:
    for index, role, lower, upper in _official_operand_ranges(instruction.op, len(instruction.args)):
        if index >= len(instruction.args) or condition_disabled and index in {1, 2}:
            continue
        arg = instruction.args[index]
        register = _register_name(arg, state)
        if register is None:
            continue
        value = _latched_operand_value(instruction.op, arg, scope, state)
        if (isinstance(value, Concrete) and not lower <= value.value <= upper
                and _pending_register_write(state, register) is None):
            return Diagnostic(
                severity="warning", category="analysis_incomplete", source=instruction.source,
                message=(f"{instruction.op} register {arg.raw} decodes to {value.value}, outside the supported "
                         f"{role} range {lower}..{upper}; truncation or wrapping is not modeled."),
                details={"op": instruction.op, "argument_index": index, "operand": arg.raw, "register": register,
                         "operand_role": role, "min": lower, "max": upper, "value": value.value},
            )
    return None


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
    elif instruction.op == "set_ph":
        field_name = "phase"
        value = resolve_arg_value(instruction.args[0], scope)
        state.latched_state.phase = value
    elif instruction.op == "reset_ph":
        field_name, value = "phase_reset", True
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


def _rt_dequeue_spacing_ns(duration: int) -> int:
    return 4 if duration == 0 else duration


def _advance_after_zero_duration(state: AnalysisState, packet: RtPacket, event_start: int) -> None:
    if packet.op == "wait_trigger" and not packet.meta.get("conditional_skipped"):
        # The duration operand applies after an arrival. Analysis can retain
        # the nominal interval, but only Play has an observed arrival here.
        if packet.meta.get("trigger_assumption", {}).get("duration_ns") != 0:
            return
    elif packet.duration != Concrete(0):
        return
    state.rt_time_ns = add_values(state.rt_time_ns, Concrete(_rt_dequeue_spacing_ns(0)))
    timing = {"queue_duration_ns": 0}
    if isinstance(state.rt_time_ns, Concrete):
        timing["next_dequeue_not_before_ns"] = state.rt_time_ns.value
    packet.meta.update(timing)
    for event in state.events[event_start:]:
        if event.meta.get("rt_packet_id") == packet.id:
            event.meta.update(timing)


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
    *,
    rt_condition_decision: bool | None = None,
    raw_linq_op: str | None = None,
) -> None:
    spec = get_instruction_spec(instruction.op)
    applies_latched_state = spec.applies_latched_state or raw_linq_op == "upd_thres"
    is_linq_mock = "fb_acq_tb_mock" in {instruction.op, raw_linq_op}
    unverified_pulse = instruction.op == "play_pulse" and state.execution_mode == "analysis"
    scope = _scope(symbols, state)
    configuration = _describe_linq_configuration(state, instruction, scope, raw_linq_op)
    duration = Concrete(0)
    t0 = state.rt_time_ns
    t1: Value | None = state.rt_time_ns
    duration_arg: Arg | None = None
    duration_arg_index = rt_duration_arg_for_instruction(spec, instruction)

    if rt_condition_decision is not False and (
        spec.category == "sync" or (spec.timing_effect_status != "known" and instruction.op != "play_netzero")
    ):
        state.netzero_window = None
    if state.conditional_enable != Concrete(0):
        # Execution truth and elapsed time are independent: only the selected
        # duration matters once the predicate is known. Equal durations
        # can also prove time while the execution decision remains unknown.
        candidate = None
        if ((rt_condition_decision is not None or instruction.op == "wait_trigger"
             or spec.category == "rt" and spec.timing_effect_status == "known")
                and duration_arg_index is not None and duration_arg_index < len(instruction.args)):
            candidate = resolve_arg_value(instruction.args[duration_arg_index], scope)
        decision = rt_condition_decision if rt_condition_decision is not None else _conditional_decision(
            state.conditional_enable, state.conditional_mask, state.conditional_operator,
            enable_bounds=state.conditional_enable_bounds, operator_bounds=state.conditional_operator_bounds,
            enable_known_bits=state.conditional_enable_known_bits, operator_known_bits=state.conditional_operator_known_bits,
        )
        if decision is not None and candidate is not None:
            selected_duration = candidate if decision else state.conditional_else_duration
            timing_known = ((not decision or not (is_linq_mock or unverified_pulse))
                            and isinstance(selected_duration, Concrete) and _valid_rt_duration(selected_duration.value))
            q1_issue_event.meta.pop("conditional_execution_unknown", None)
            q1_issue_event.meta.update(conditional_timing_known=timing_known, conditional_skipped=not decision)
            if not decision:
                duration = state.conditional_else_duration
                t1 = add_values(t0, duration)
                packet = _append_packet(state, instruction, duration, q1_t0, q1_t1, t0, t1, rt_packet_id)
                meta = {"op": instruction.op, "rt_packet_id": packet.id, "conditional_skipped": True,
                        "conditional_timing_known": timing_known, "output_state_unknown": False, "applied_state": {}}
                if applies_latched_state:
                    meta["captured_state"] = _consume_applied_state(state, consume=False)
                    meta["captured_setter_event_ids"] = dict(state.latched_state.pending_event_ids)
                packet.meta.update(meta)
                _emit_event(state, lane="rt.branch", kind="conditional_skip", t0=t0, t1=t1,
                            duration=duration, label=f"skip {instruction.op}", confidence=packet.confidence,
                            source=instruction.source, meta=meta)
                state.rt_time_ns = t1
                return
        elif not unverified_pulse:
            if instruction.op in _NETZERO_PULSE_OPS:
                state.netzero_window = None
            duration = Unknown("conditional_execution")
            if not is_linq_mock and instruction.op != "wait_trigger" and _equal_conditional_durations(state, candidate):
                duration = candidate
                q1_issue_event.meta["conditional_timing_known"] = True
            if not isinstance(duration, Concrete):
                state.conditional_time_unknown = True
            t1 = add_values(t0, duration)
            packet = _append_packet(state, instruction, duration, q1_t0, q1_t1, t0, t1, rt_packet_id)
            packet.meta.update(conditional_execution_unknown=True, output_state_unknown=applies_latched_state)
            meta = {"reason": "conditional_execution_unknown", "op": instruction.op,
                    "rt_packet_id": packet.id, "output_state_unknown": applies_latched_state,
                    "conditional_timing_known": isinstance(duration, Concrete)}
            if configuration is not None:
                meta["linq_configuration"] = {**configuration, "conditional": True}
            event = _emit_event(
                state, lane="rt.unknown", kind="unknown_region", t0=t0, t1=t1 if isinstance(duration, Concrete) else None,
                duration=duration, label=f"conditional {instruction.op}", confidence="unknown",
                source=instruction.source, meta=meta,
            )
            if configuration is not None:
                _diagnose_linq_configuration(state, event)
            state.rt_time_ns = t1
            return

    if unverified_pulse:
        reason = "Execution behavior for play_pulse is unverified."
        packet = _append_packet(state, instruction, Unknown(reason), q1_t0, q1_t1, t0, None, rt_packet_id)
        meta = {"op": instruction.op, "rt_packet_id": packet.id, "output_state_unknown": True}
        planned_duration = resolve_arg_value(instruction.args[1], scope)
        if isinstance(planned_duration, Concrete):
            meta["planned_duration_ns"] = planned_duration.value
        packet.meta.update(meta)
        _end_analysis_at_issue(state, q1_issue_event, "unverified_instruction", reason, details=meta)
        state.netzero_window = None
        return

    if is_linq_mock:
        duration = Unknown(MOCK_LIMITATION)
        packet = _append_packet(state, instruction, duration, q1_t0, q1_t1, t0, None, rt_packet_id)
        meta = {"op": instruction.op, "normalized_op": "fb_acq_tb_mock", "reason": MOCK_LIMITATION,
                "rt_packet_id": packet.id}
        packet.meta.update(meta)
        _emit_event(state, lane="rt.unknown", kind="unknown_region", t0=t0, t1=None, duration=duration,
                    label="fb_acq_tb_mock timing unknown", confidence="unknown", source=instruction.source, meta=meta)
        state.diagnostics.append(Diagnostic(severity="warning", category="analysis_incomplete",
                                            message=MOCK_LIMITATION, source=instruction.source))
        state.rt_time_ns = add_values(t0, duration)
        state.netzero_window = None
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
    applied_state = _consume_applied_state(state) if applies_latched_state else {}
    packet = _append_packet(state, instruction, duration, q1_t0, q1_t1, t0, t1, rt_packet_id)
    if state.conditional_time_unknown:
        packet.meta["output_state_unknown"] = applies_latched_state
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
    elif instruction.op == "upd_thres" or raw_linq_op == "upd_thres":
        meta = {"rt_packet_id": packet.id}
        if raw_linq_op:
            meta.update(op=instruction.op, normalized_op=raw_linq_op)
        if applied_state:
            meta["applied_state"] = applied_state
        _emit_event(state, lane="rt.update", kind="upd_thres", t0=t0, t1=t1, duration=duration, label="upd_thres", confidence=_confidence_for_timing(duration, t0, t1), source=instruction.source, meta=meta)
    elif instruction.op in {"wait_sync", "wait_trigger"}:
        unresolved = instruction.op == "wait_trigger" and state.execution_mode == "analysis"
        meta = {"rt_packet_id": packet.id}
        if unresolved:
            timing = {"trigger_arrival_unknown": True, "trigger_wait_duration_ns": _plain_value(duration)}
            meta.update(timing)
            packet.meta.update(timing)
        _emit_event(state, lane="rt.sync", kind=instruction.op, t0=t0, t1=t1, duration=duration,
                    label=instruction.op, confidence="unknown" if unresolved else _confidence_for_timing(duration, t0, t1),
                    source=instruction.source, meta=meta)
        if unresolved:
            state.rt_trigger_epoch += 1
    elif instruction.op in {"set_latch_en", "latch_rst"}:
        _emit_event(state, lane="rt.trigger", kind=instruction.op, t0=t0, t1=t1, duration=duration, label=instruction.op, confidence=_confidence_for_timing(duration, t0, t1), source=instruction.source, meta={"rt_packet_id": packet.id})
    elif instruction.op.startswith("fb_"):
        meta = {"rt_packet_id": packet.id}
        effect = instruction
        if instruction.op == "fb_cmd" and len(instruction.args) == 3:
            effect = _raw_linq_feedback_effect(state, instruction, scope, raw_linq_op, meta)
        kind, label = _feedback_rt_event_kind_and_label(effect.op)
        data_type = _ACQUISITION_FEEDBACK_TYPES.get(effect.op)
        if data_type is not None and effect.args:
            channel = _feedback_channel(effect.args[0], scope)
            # Replace the mapping to isolate shallow analysis probes.
            channels = dict(state.feedback_acq_channels)
            if channel == "0":
                channels.pop(data_type, None)
            else:
                channels[data_type] = channel
            state.feedback_acq_channels = channels
        feedback = _feedback_flow_metadata(effect, direction="send", scope=scope)
        if feedback:
            meta["feedback"] = feedback
        if configuration is not None:
            meta["linq_configuration"] = configuration
        event = _emit_event(state, lane="rt.feedback", kind=kind, t0=t0, t1=t1, duration=duration, label=label,
                            confidence=_confidence_for_timing(duration, t0, t1), source=instruction.source, meta=meta)
        if configuration is not None:
            _diagnose_linq_configuration(state, event)
    else:
        _emit_event(state, lane="rt.generic", kind=instruction.op, t0=t0, t1=t1, duration=duration, label=instruction.op, confidence=_confidence_for_timing(duration, t0, t1), source=instruction.source, meta={"rt_packet_id": packet.id})

    if instruction.op in _NETZERO_PULSE_OPS:
        previous = state.netzero_window
        state.netzero_window = None
        if isinstance(t0, Concrete) and event_start < len(state.events):
            pulse = state.events[event_start]
            if previous is not None:
                _diagnose_netzero_gap(state, previous, pulse, t0.value)
            if instruction.op == "play_netzero":
                maxt_ns = _arg_int(instruction.args[0], scope)
                register = _physical_read_register(instruction.args[0], state)
                if maxt_ns is not None and maxt_ns > 0 and not (register and _pending_register_write(state, register)):
                    state.netzero_window = _NetZeroWindow(instruction.source, pulse.id, t0.value, maxt_ns, register)

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
            event.meta["output_state_unknown"] = applies_latched_state
        if "applied_state" in event.meta:
            event.meta["applied_setter_event_ids"] = {
                field: pending_event_ids[field] for field in applied_state if field in pending_event_ids
            }


def _diagnose_netzero_gap(
    state: AnalysisState, window: _NetZeroWindow, pulse: TimelineEvent, start_ns: int,
) -> None:
    elapsed_ns = start_ns - window.start_ns
    if not 0 <= elapsed_ns < window.maxt_ns:
        return
    shortfall_ns = window.maxt_ns - elapsed_ns
    state.diagnostics.append(Diagnostic(
        severity="warning",
        category="possible_netzero_overlap",
        message=(f"The next pulse starts {shortfall_ns} ns before the maximum net-zero compensation time "
                 "has elapsed and may overlap the compensation pulse."),
        source=pulse.source,
        related_events=[window.event_id, pulse.id],
        details={"maxt_ns": window.maxt_ns, "elapsed_ns": elapsed_ns, "shortfall_ns": shortfall_ns,
                 "compensation_source": _source_to_json(window.source)},
    ))


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
    if instruction.op in {"acquire_weighed", "acquire_weighted"}:
        meta["weight_indices"] = [_arg_int(arg, scope) for arg in instruction.args[2:4]]
    if state.feedback_acq_channels:
        streams = _acquisition_feedback_streams(state.feedback_acq_channels, instruction, scope,
                                                _feedback_acquisition_source(label, bin_index))
        if len(streams) == 1:
            meta["feedback"] = streams[0]
        elif streams:
            meta["feedback_streams"] = streams
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
    if state.compiled_addresses is not None:
        meta["compiled_pc"] = (state.interior_pc if state.interior_pc is not None
                               else state.compiled_addresses.entries.get(instruction.pc))
        if state.interior_pc is not None:
            meta["source_pc"] = instruction.pc
            meta["macro_source_op"] = state.instructions_by_pc[instruction.pc].op
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
    if state.rt_trigger_epoch and (lane.startswith("rt.") or lane == "state.pending") and "fault" not in event_meta:
        event_meta["rt_trigger_epoch"] = state.rt_trigger_epoch
    if state.conditional_time_unknown and kind != "q1_issue":
        event_meta["conditional_time_unknown"] = True
    if state.current_resolved_args and "resolved_args" not in event_meta:
        event_meta["resolved_args"] = state.current_resolved_args
    if state.record_trace:
        event_index = len(state.events)
    else:
        event_index = state.event_serial
        state.event_serial += 1
    event = TimelineEvent(
        id=f"{state.sequencer_id}:e{event_index}",
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
    # Statistics still need the RT alignment anchor and hardware diagnostic evidence.
    if state.record_trace or kind in {"q1_issue", "acquire", "rt_queue_dequeue", "unknown_region", "latched_state_pending"} or (
        "scope_enable" in (event_meta.get("applied_state") or {})
    ):
        state.events.append(event)
    return event


def _loop_preview_events(state: AnalysisState, start_line: int, end_line: int) -> list[TimelineEvent]:
    return [
        event
        for event in state.events
        if start_line <= event.source.line < end_line
        and event.kind not in {"loop_block", "loop_iteration_preview"}
    ]


def _compact_loop_evidence(state: AnalysisState, body: list[Instr], edge: TimelineEvent) -> _LoopEvidence | None:
    start, end = body[0], body[-1]
    revisited = any(start.pc <= pc <= end.pc for pc in state.analysis_replayed_pcs)
    if revisited:
        entry = state.analysis_visit_entries.get(start.pc)
        if entry is None:
            return None
        current_events = state.events[entry.event_start:]
        issues = [event for event in current_events if event.kind == "q1_issue" and "preview_source_event_id" not in event.meta]
        source_pcs = {item.source: item.pc for item in body}
        pcs = [source_pcs.get(issue.source, -1) for issue in issues]
        if (not issues or pcs[0] != start.pc or issues[-1].id != edge.id
                or any(right <= left for left, right in zip(pcs, pcs[1:]))):
            return None
        # The current issues retain the input register values and bounds at each read.
        events = [event for event in current_events if event.source in source_pcs and event.source != end.source
                  and event.kind not in {"loop_block", "loop_iteration_preview"}]
        packets = state.rt_packets[entry.packet_start:]
        diagnostics = [*state.static_diagnostics, *state.diagnostics[entry.diagnostic_start:]]
    else:
        current_events = state.events
        events = _loop_preview_events(state, start.source.line, end.source.line)
        packets = state.rt_packets
        diagnostics = state.diagnostics
    packet_ids = {event.meta.get("rt_packet_id") for event in events if event.kind == "q1_issue"}
    children = []
    if any(item.op == "loop" for item in body[:-1]):
        issue_ids = {event.id for event in events if event.kind == "q1_issue"}
        children = [event for event in current_events if event.kind == "loop_block"
                    and event.meta.get("loop_q1_issue_event_id") in issue_ids]
    return _LoopEvidence(events, {packet.id: packet for packet in packets if packet.id in packet_ids}, diagnostics, children, revisited)


def _prove_nested_loop_children(
    state: AnalysisState, body: list[Instr], evidence: _LoopEvidence, parent_counter: str | None,
    parent_count: Value | None, symbols: SymbolTable | None,
) -> _NestedLoopProof | Literal["varying_count"] | None:
    child_edges = {item.pc for item in body if item.op == "loop"}
    if not child_edges:
        return _NestedLoopProof({}, {})
    children = {event.meta["end_pc"]: event for event in evidence.children}
    if len(children) != len(evidence.children) or children.keys() != child_edges:
        return None
    spans = [(child.meta["start_pc"], edge_pc) for edge_pc, child in children.items()]
    if any(start < other_start <= edge < other_edge for start, edge in spans for other_start, other_edge in spans):
        return None
    issues = {event.source: event for event in evidence.events
              if event.kind == "q1_issue" and "preview_source_event_id" not in event.meta}
    if (any(item.source not in issues for item in body) or any(event.kind == "unknown_region" for event in evidence.events)
            or any(not isinstance(issue.t0, Concrete) or not isinstance(issue.t1, Concrete)
                   or issue.meta.get("conditional_active") for issue in issues.values())):
        return None
    unsafe = {diagnostic.source for diagnostic in evidence.diagnostics
              if diagnostic.severity == "error" or diagnostic.category == "register_not_ready"}
    written = {parent_counter, *(child.meta["counter_reg"] for child in children.values())}
    written.update(register for item in body for register in _register_write_operands(item, state))
    invariant = set()
    dependent = {parent_counter} if parent_counter is not None else set()
    if parent_count == Concrete(1):
        invariant.update(dependent)
        dependent.clear()
    affine = {}
    if parent_counter is not None and isinstance(parent_count, Concrete) and parent_count.value > 1:
        affine[parent_counter] = parent_count.value, -1
    count_slopes = {}
    invariant_setters = set()
    scope = _scope(symbols or {}, state)
    for item in body:
        if item.source in unsafe:
            return None
        child = children.get(item.pc)
        if child is not None:
            meta = child.meta
            count, counter = meta["count"], meta["counter_reg"]
            period = meta["period"]
            if (type(count) is not int or count < 1
                    or not body[0].pc <= meta["start_pc"] <= item.pc
                    or meta["loop_q1_issue_event_id"] != issues[item.source].id):
                return None
            if counter in dependent:
                form = affine.get(counter)
                if (form is None or form[0] != count or not isinstance(parent_count, Concrete)
                        or min(count, count + form[1] * (parent_count.value - 1)) < 1
                        or period["kind"] != "concrete" or not meta["period_invariant"]
                        or not _nested_variable_child_effects_known(
                            state, meta["start_pc"], item.pc, evidence, invariant_setters,
                        )):
                    return "varying_count"
                if form[1]:
                    count_slopes[item.pc] = form[1]
            elif counter not in invariant and parent_count != Concrete(1):
                return "varying_count"
            # The proven child always finishes with its counter at zero.
            invariant.add(counter)
            dependent.discard(counter)
            affine.pop(counter, None)
            continue
        category = get_instruction_spec(item.op).category
        if item.op != "nop" and category not in {"classical", "latched", "rt"}:
            return None
        writes = _register_write_operands(item, state)
        reads = {register for _, register in _register_read_operands(item, state)}
        if item.op in _NESTED_INVARIANT_SETTERS and reads.isdisjoint(written - invariant):
            invariant_setters.add(item.source)
        if writes:
            if reads.intersection(dependent):
                dependent.update(writes)
                form = _nested_count_affine_write(state, item, issues[item.source], scope, affine, written - invariant)
            else:
                dependent.difference_update(writes)
                form = None
            for register in writes:
                affine.pop(register, None)
            if form is not None and isinstance(parent_count, Concrete):
                first, slope = form
                last = first + slope * (parent_count.value - 1)
                modulus = _CLASSICAL_REGISTER_MASK + 1
                quotient = first // modulus
                if last // modulus == quotient:
                    # All visits store the same modular adjustment of this expression.
                    affine.update(dict.fromkeys(writes, (first - quotient * modulus, slope)))
        if category == "classical" and reads.isdisjoint(written - invariant):
            invariant.update(writes)
        else:
            invariant.difference_update(writes)
    if count_slopes and any(item.op in {"reset_ph", "set_ph_delta"} for item in body):
        return "varying_count"
    return _NestedLoopProof(children, count_slopes)


def _nested_child_elapsed_known(child: TimelineEvent) -> bool:
    meta = child.meta
    count = meta["count"]
    return type(count) is int and count > 0 and (
        meta.get("total_duration", {}).get("kind") == "concrete"
        or meta["period"]["kind"] == "concrete" and (count == 1 or meta["period_invariant"])
    )


def _nested_variable_child_effects_known(
    state: AnalysisState, start_pc: int, end_pc: int, evidence: _LoopEvidence, invariant_setters: set[SourceLocation],
) -> bool:
    body = [state.instructions_by_pc[pc] for pc in range(start_pc, end_pc)]
    if any(item.op not in _NESTED_INVARIANT_SETTERS and item.op not in {"nop", "wait", "play", "upd_param"} for item in body):
        return False
    sources = {item.source for item in body}
    child_events = [event for event in evidence.events if event.source in sources and "preview_source_event_id" not in event.meta]
    if any(event.meta.get("captured_phase_reset", False) is not False
           or any("phase_delta" in (event.meta.get(key) or {}) for key in ("captured_state", "applied_state"))
           for event in child_events):
        return False
    setters = {event.source: event for event in child_events if event.kind == "latched_state_pending"}
    applied = {setter_id: event.meta.get("applied_state", {}).get(field)
               for event in child_events if event.meta.get("rt_packet_id") in evidence.packets
               for field, setter_id in event.meta.get("applied_setter_event_ids", {}).items()}
    for item in body:
        if item.op not in _NESTED_INVARIANT_SETTERS:
            continue
        setter = setters.get(item.source)
        if item.source not in invariant_setters or setter is None or setter.id not in applied:
            return False
        value = setter.meta["value"]
        if item.op in _AWG_PAIR_SETTERS:
            if not (isinstance(value, list) and len(value) == 2 and all(type(path) is int for path in value)
                    and applied[setter.id] == value):
                return False
        elif type(value) is not int:
            return False
    return True


def _nested_count_affine_write(
    state: AnalysisState, instruction: Instr, issue: TimelineEvent, scope: SymbolTable,
    affine: dict[str, tuple[int, int]], varying: set[str | None],
) -> tuple[int, int] | None:
    def operand(arg: Arg) -> tuple[int, int] | None:
        register = _physical_read_register(arg, state)
        if register is None:
            resolved = resolve_arg_value(arg, scope)
            value = resolved.value if isinstance(resolved, Concrete) else None
        else:
            if register in affine:
                return affine[register]
            if register in varying:
                return None
            observed = issue.meta.get("register_reads", {}).get(register, {})
            value = observed.get("value") if observed.get("kind") == "concrete" else None
        return (value & _CLASSICAL_REGISTER_MASK, 0) if type(value) is int else None

    if instruction.op == "move":
        return operand(instruction.args[0])
    if instruction.op in {"add", "sub"}:
        left, right = map(operand, _docs_ordered_binary_args(instruction, state))
        if left is not None and right is not None:
            sign = 1 if instruction.op == "add" else -1
            return left[0] + sign * right[0], left[1] + sign * right[1]
    return None


def _affine_nested_loop_timing(
    evidence: _LoopEvidence, proof: _NestedLoopProof, count: int, first_rt: int, first_q1_body: int,
    parent_jump_ns: int, parent_continue_ns: int, *, rt_timing: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    issues = {event.id: event for event in evidence.events if event.kind == "q1_issue"}
    first_child_rt = first_child_q1 = rt_slope = q1_slope = 0
    child_counts = {}
    for pc, slope in proof.count_slopes.items():
        child = proof.children[pc]
        meta = child.meta
        start = next((issues[event_id] for event_id in meta["first_iteration_event_ids"] if event_id in issues), None)
        edge = issues.get(meta["loop_q1_issue_event_id"])
        if start is None or edge is None or not isinstance(start.t0, Concrete) or not isinstance(edge.t0, Concrete):
            return None
        child_body_q1 = edge.t0.value - start.t0.value
        if child_body_q1 < 0:
            return None
        child_count, child_rt = meta["count"], meta["period"]["value"]
        child_taken_q1 = child_body_q1 + meta["loop_q1_jump_runtime_ns"]
        first_child_rt += child_count * child_rt
        first_child_q1 += child_count * child_body_q1 + (child_count - 1) * meta["loop_q1_jump_runtime_ns"]
        first_child_q1 += meta["loop_q1_continue_runtime_ns"]
        rt_slope += slope * child_rt
        q1_slope += slope * child_taken_q1
        child_counts[child.id] = {"intercept": child_count, "slope": slope}
    if first_child_rt > first_rt or first_child_q1 > first_q1_body:
        return None
    # The observed parent edge is already timed. Only sum later parent bodies
    # and their taken/final-continue edge costs into the hidden Q1 work.
    index_sum = count * (count - 1) // 2
    if rt_timing is None:
        rt_timing = {
            "period_affine": {"intercept": first_rt, "slope": rt_slope},
            "total_duration": value_to_json(Concrete(first_rt * count + rt_slope * index_sum)),
        }
    return {
        **rt_timing,
        "q1_hidden_ns": ((count - 1) * first_q1_body + q1_slope * index_sum
                         + (count - 2) * parent_jump_ns + parent_continue_ns),
        "child_count_affine": child_counts,
    }


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
    q1_hidden_ns: int | None = None,
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
    if q1_hidden_ns is not None:
        state.q1_time_ns = add_values(state.q1_time_ns, Concrete(q1_hidden_ns))
    elif isinstance(q1_body_period, Concrete):
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


def _record_stop_code(state: AnalysisState, instruction: Instr, scope: SymbolTable) -> Any:
    value: Value = Concrete(0)
    if instruction.args:
        operand = instruction.args[0]
        register = _physical_read_register(operand, state)
        value = resolve_arg_value(operand, scope)
        if register is not None and _pending_register_write(state, register) is not None:
            value = Unknown(f"Stop status register {register} is not ready")
    code = _signed_32_bit_register_value(value.value) if isinstance(value, Concrete) else _plain_value(value)
    if "execution" in state.metadata:
        state.metadata["execution"].update(stop_code=code, stop_source=_source_to_json(instruction.source))
    return code


def _record_illegal_instruction(state: AnalysisState, instruction: Instr, issue: TimelineEvent | None) -> None:
    reason = "Illegal Q1ASM instruction executed."
    source = _source_to_json(instruction.source)
    fault = {"flag": ILLEGAL_INSTRUCTION_FLAG, "sequencer_status": "STOPPED",
             "q1_time_ns": _plain_value(state.q1_time_ns), "source": source}
    if "execution" in state.metadata:
        execution = state.metadata["execution"]
        execution.update(status="error", reason=reason, source=source, pc=instruction.pc, fault=fault)
        if state.compiled_addresses is not None:
            execution["compiled_pc"] = state.compiled_addresses.entries[instruction.pc]
    state.stopped = True
    assumed = state.execution_mode == "analysis" and (state.branch_explore_depth or _register_branch_assumptions(state))
    related = []
    if issue is not None:
        issue.meta["fault"] = fault
        event = _emit_event(state, lane="rt.control", kind="illegal", t0=state.q1_time_ns, t1=state.q1_time_ns,
                            duration=Concrete(0), label="Illegal instruction", confidence="assumed" if assumed else
                            _confidence_for_timing(Concrete(0), state.q1_time_ns, state.q1_time_ns), source=instruction.source,
                            meta={"fault": fault, "q1_issue_event_id": issue.id})
        related = [event.id, issue.id]
    state.diagnostics.append(Diagnostic(severity="warning" if assumed else "error", category="illegal_instruction",
                                        message=reason, source=instruction.source, related_events=related,
                                        details={"op": instruction.op, "fault": fault}))


def _consume_applied_state(state: AnalysisState, *, consume: bool = True) -> dict[str, Any]:
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
    if state.pending_phase_reset is True:
        applied["phase_reset"] = True
    if "digital" in pending and state.latched_state.digital is not None:
        applied["digital"] = [_plain_value(value) for value in state.latched_state.digital]
    if "scope_enable" in pending and state.latched_state.scope_enable is not None:
        applied["scope_enable"] = _plain_value(state.latched_state.scope_enable)
    if consume:
        pending.clear()
        state.latched_state.pending_event_ids.clear()
        _clear_nco_one_shots(state)
    return applied


def _clear_nco_one_shots(state: AnalysisState) -> None:
    state.pending_phase_reset = False
    state.latched_state.phase_delta = None
    for field in ("phase_reset", "phase_delta"):
        state.latched_state.pending_since.pop(field, None)
        state.latched_state.pending_event_ids.pop(field, None)


def _scope(symbols: SymbolTable, state: AnalysisState) -> SymbolTable:
    label_symbols = {f"@{label}": Concrete(pc) for label, pc in _address_labels(state).items()}
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


def _describe_linq_configuration(
    state: AnalysisState, instruction: Instr, scope: SymbolTable, raw_op: str | None,
) -> dict[str, Any] | None:
    op = raw_op if instruction.op == "fb_cmd" else instruction.op
    if op not in LINQ_CONFIGURATION_STREAMS:
        return None
    configuration = {"op": op, "data_type": LINQ_CONFIGURATION_STREAMS[op], "operands": None}
    operands = []
    for arg in instruction.args[:-1]:
        register = _physical_read_register(arg, state)
        value = None if register and _pending_register_write(state, register) else _arg_int(arg, scope)
        if value is None:
            configuration["reason"] = f"{op} configuration operands are unresolved."
            return configuration
        operands.append(value)
    if instruction.op == "fb_cmd" or len(instruction.args) == 2:
        try:
            _, decoded = decode_linq_command(instruction.op, [*operands, 0])
        except ValueError as error:
            configuration["reason"] = str(error)
            return configuration
        operands = decoded[:-1]
    configuration["operands"] = operands
    limitation = linq_configuration_limitation(op, operands)
    if limitation is not None:
        configuration["reason"] = limitation
    return configuration


def _diagnose_linq_configuration(state: AnalysisState, event: TimelineEvent) -> None:
    configuration = event.meta["linq_configuration"]
    reason = configuration.get("reason")
    if reason is not None and reason != event.meta.get("reason"):
        state.diagnostics.append(Diagnostic(
            severity="warning", category="analysis_incomplete", message=reason, source=event.source,
            related_events=[event.id] if state.record_trace else [],
            details={"analysis_scope": "feedback", "reason": "linq_configuration_unknown",
                     "op": configuration["op"], "data_type": configuration["data_type"]},
        ))


def _raw_linq_feedback_effect(
    state: AnalysisState, instruction: Instr, scope: SymbolTable, op: str | None, meta: dict[str, Any],
) -> Instr:
    command_id = _arg_int(instruction.args[0], scope)
    meta.update(op=instruction.op, linq_command_id=command_id)
    reason = None
    if op is None:
        command = f"0x{command_id:x}" if command_id is not None else instruction.args[0].raw
        reason = f"LINQ command {command} has no verified behavior."
    else:
        payload = _arg_int(instruction.args[1], scope)
        if payload is not None:
            try:
                _, operands = decode_linq_command("fb_cmd", [command_id, payload, 0])
                for index, role, lower, upper in _official_operand_ranges(op, len(operands)):
                    if not lower <= operands[index] <= upper:
                        reason = f"{op} resolved {role} {operands[index]} is outside {lower}..{upper}."
                        break
            except ValueError as error:
                reason = str(error)
    if reason:
        meta["reason"] = reason
        state.diagnostics.append(Diagnostic(severity="warning", category="analysis_incomplete",
                                            message=reason, source=instruction.source))
        return instruction
    meta["normalized_op"] = op
    arguments = instruction.args[1:]
    if op == "fb_com_data":
        channel = command_id - 0x100
        arguments = [Arg("imm", channel, str(channel)), *arguments]
    return replace(instruction, op=op, args=arguments)


def _feedback_rt_event_kind_and_label(op: str) -> tuple[str, str]:
    if op == "fb_com_data":
        return "feedback_com", "feedback commit"
    return op, op


def _acquisition_feedback_streams(channels: dict[str, str], instruction: Instr, scope: SymbolTable,
                                  source: str) -> list[dict[str, Any]]:
    timing = {}
    if instruction.op in {"acquire", "acquire_weighed", "acquire_weighted"}:
        types = ("thresholded_bits", "iq_values")
    elif instruction.op == "acquire_timetags":
        enabled = _arg_int(instruction.args[2], scope)
        if enabled == 1:
            types = ("low_latency_time_tags", "high_resolution_time_tags")
            timing = {"send_time_unknown": True}
        elif enabled == 0:
            types = ("ttl_counts", "time_delta")
            timing = ({"send_on": "command_start"} if _arg_int(instruction.args[3], scope) == 0
                      else {"send_time_unknown": True})
        else:
            return []
    else:
        return []
    streams = []
    for data_type in types:
        if data_type in channels:
            stream = {"direction": "send", "channel": channels[data_type], "source": source,
                      "data_type": data_type, **timing}
            if data_type == "iq_values":
                stream["payload_words"] = ["I", "Q"]
            streams.append(stream)
    return streams


def _feedback_flow_metadata(instruction: Instr, *, direction: str, scope: SymbolTable) -> dict[str, str] | None:
    if instruction.op == "fb_com_data":
        channel = _feedback_channel(instruction.args[0], scope) if instruction.args else "default"
        if channel == "0":
            return None
        return {
            "direction": direction,
            "channel": channel,
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
