"""Source validation and Q1 operand admission for both execution engines."""
from __future__ import annotations

from dataclasses import replace

from q1timeline.analysis.values import (
    Concrete, Symbolic, SymbolTable, Value, resolve_arg_value, resolve_def_values, value_to_json,
)
from q1timeline.diagnostics import Diagnostic
from q1timeline.q1asm import core, core_rt
from q1timeline.q1asm.ast import Arg, Instr, Program
from q1timeline.q1asm.compiled_addresses import compile_addresses
from q1timeline.q1asm.core import branch_on_flags as _status_branch_decision
from q1timeline.q1asm.core_alu import operand_ranges as _alu_operand_ranges
from q1timeline.q1asm.instruction_table import (
    STATUS_BRANCH_OPS, InstructionSpec, get_instruction_spec, rt_duration_arg_for_instruction,
)
from q1timeline.q1asm.lint import analyze_program_lints
from q1timeline.q1asm.linq_commands import raw_linq_operation
from q1timeline.execution.state import (
    AnalysisState, BoundInstruction, LOCAL_EXECUTION_OPS, PreparedDefProgram, RT_DURATION_MAX,
)
from q1timeline.execution.trace import source_to_json
from q1timeline.execution.operands import (
    address_labels, arg_int, arg_symbol_lookup_names, build_immutable_value_origins,
    docs_ordered_binary_args, instruction_for_q1_timing, is_valid_register_arg, operand_known_bits,
    operand_scope, register_read_operands, resolve_execution_target,
    resolve_register_name, set_cond_is_disabled,
)
from q1timeline.execution.registers import pending_register_write

_RT_DURATION_MIN = 4


def end_concrete_execution(
    state: AnalysisState, status: str, reason: str, instruction: Instr | None = None, *, category: str | None = None,
) -> None:
    pc = instruction.pc if instruction is not None else state.pc
    location = {"pc": pc}
    if state.compiled_addresses is not None:
        location["compiled_pc"] = compiled_pc(state, pc)
    state.metadata["execution"].update(status=status, reason=reason, **location)
    state.stopped = True
    source = instruction.source if instruction is not None else None
    if source is not None:
        state.metadata["execution"]["source"] = source_to_json(source)
    state.diagnostics.append(Diagnostic(
        severity="error" if status == "error" else "warning",
        category=category or ("analysis_incomplete" if status != "error" else "invalid_argument_value"),
        message=f"{state.metadata['execution']['mode'].capitalize()} execution {status}: {reason}",
        source=source,
        details={"execution_status": status, **location},
    ))


def concrete_instruction_ready(
    state: AnalysisState, instruction: Instr, symbols: SymbolTable,
    spec: InstructionSpec, has_invalid_arguments: bool, diagnostic_start: int,
    *, supported_ops: set[str] | None = None, allow_symbolic_operands: bool = False,
    rt_duration_error: Diagnostic | None = None, scope: SymbolTable | None = None,
) -> bool:
    diagnostics = state.diagnostics[diagnostic_start:]
    errors = [item for item in diagnostics if (item.severity == "error" and item.category != "unresolved_symbol") or item.category == "register_not_ready"]
    if errors:
        reason = errors[0].message
        end_concrete_execution(state, "error", reason, instruction)
        return False
    if rt_duration_error is not None:
        end_concrete_execution(state, "error", rt_duration_error.message, instruction)
        state.diagnostics[-1].details.update(rt_duration_error.details)
        return False
    if (instruction.op not in (LOCAL_EXECUTION_OPS if supported_ops is None else supported_ops)
            and not set_cond_is_disabled(state, instruction, symbols)):
        status = "error" if spec.category == "unknown" else "blocked"
        end_concrete_execution(state, status, f"{instruction.op} has no supported concrete execution semantics.", instruction)
        return False
    if scope is None:
        scope = operand_scope(symbols, state)
    condition_disabled = set_cond_is_disabled(state, instruction, symbols)
    if instruction.op in {"mulu32", "muls32"} and not has_invalid_arguments:
        if resolve_register_name(instruction.args[2], state) == resolve_register_name(instruction.args[3], state):
            end_concrete_execution(state, "blocked", "Full-product multiply destinations resolve to the same register; write priority is unverified.", instruction)
            return False
    destinations = core.destination_indexes(instruction.op)
    for index, arg in enumerate(instruction.args):
        if index in destinations or (condition_disabled and index in {1, 2}):
            continue
        value = resolve_arg_value(arg, scope)
        if not isinstance(value, Concrete):
            if allow_symbolic_operands and arg.kind != "label":
                continue
            status = "error" if arg.kind == "label" else "blocked"
            end_concrete_execution(state, status, f"Operand {arg.raw} is unresolved: {value}.", instruction)
            return False
    if has_invalid_arguments:
        end_concrete_execution(state, "error", "Invalid instruction operands.", instruction)
        return False
    if spec.category == "latched":
        unsupported = unsupported_register_operand(state, instruction, scope, condition_disabled=condition_disabled)
        if unsupported is not None:
            end_concrete_execution(state, "blocked", unsupported.message, instruction)
            return False
    if instruction.op in STATUS_BRANCH_OPS or (instruction.op == "jge" and len(instruction.args) == 1):
        if _status_branch_decision(instruction.op, state.alu_flags) is None:
            end_concrete_execution(state, "blocked", "Branch status flags are unknown.", instruction)
            return False
    if instruction.op in {"asl", "asr", "lsl", "lsr"}:
        _, shift_arg = docs_ordered_binary_args(instruction, state)
        shift = resolve_arg_value(shift_arg, scope)
        if allow_symbolic_operands and not isinstance(shift, Concrete):
            return True
        if not 0 <= shift.value <= 32:
            end_concrete_execution(state, "blocked", "Shift count is outside the supported range 0..32.", instruction)
            return False
    return True


def diagnose_program(
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
            validate_instruction(probe, instruction, symbols, prepared.bindings[instruction.pc], source_check=True)
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
            validate_instruction(probe, instruction, symbols)
        errors = [item for item in probe.diagnostics
                  if item.severity == "error" and item not in state.static_diagnostics]
        state.static_diagnostics.extend(errors)
        state.diagnostics.extend(errors)
        if not recover_source or state.strict_q1asm or not _exclude_invalid_source_lines(state):
            break
        defs = {name: arg for name, arg in program.defs.items() if arg.kind != "reg"}
        state.compiled_addresses = compile_addresses(state.instructions_by_pc, state.labels)
        resolved = resolve_def_values(defs, symbols, labels=address_labels(state))
        symbols.update(resolved)
        for name, value in resolved.items():
            state.symbol_origins["defs"][name]["value"] = value_to_json(value)
        state.immutable_value_origins = build_immutable_value_origins(symbols, defs, state)
    if not state.strict_q1asm:
        scope = operand_scope(symbols, probe)
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


def validate_instruction(
    state: AnalysisState,
    instruction: Instr,
    symbols: SymbolTable,
    binding: BoundInstruction | None = None,
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
        if resolve_register_name(arg, state) is not None:
            continue
        resolved = branch_target_arg(arg, state, symbols)
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
        emit_undefined_label(state, instruction, label)
    return bool(missing_labels)


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
        if resolve_register_name(arg, state) is not None:
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
        if arg.kind != "reg" or is_valid_register_arg(arg):
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
    scope = operand_scope(symbols, state)
    for index, arg in enumerate(instruction.args):
        if arg.kind not in {"symbol", "placeholder"}:
            continue
        if arg.kind == "symbol" and str(arg.value).startswith("$"):
            continue
        if resolve_register_name(arg, state) is not None:
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


def diagnose_strict_q1asm_placeholder_defs(state: AnalysisState, defs: dict[str, Arg]) -> None:
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

    resolved_instruction = instruction_for_q1_timing(instruction, state, symbols)
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
        counter = resolve_register_name(instruction.args[0], state)
        if counter is not None and counter == resolve_register_name(instruction.args[1], state):
            # loop lowers to sub followed immediately by a register-target jnz.
            state.diagnostics.append(Diagnostic(
                severity="warning", category="register_not_ready", source=instruction.source,
                message=(f"loop reads {counter} as its branch target before the counter decrement is available. "
                         "Use a separate target register or expand the loop with a gap between sub and jnz."),
                details={"op": "loop", "argument_index": 1, "register": counter,
                         "writer": source_to_json(instruction.source), "internal_loop_target_read": True},
            ))
    if not state.pending_register_writes:
        return
    for argument_index, register in reads if reads is not None else register_read_operands(instruction, state):
        pending = pending_register_write(state, register)
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
                    "writer": source_to_json(pending.source),
                    "available_issue_index": pending.available_issue_index,
                    "current_issue_index": state.q1_issue_index,
                },
            )
        )


def _register_only_argument_indexes(spec: InstructionSpec, actual_count: int) -> set[int]:
    candidates = [signature for signature in spec.signatures if len(signature.args) == actual_count]
    return {
        index
        for index in range(actual_count)
        if candidates and all(signature.args[index] == frozenset({"R"}) for signature in candidates)
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
    duration = resolve_arg_value(duration_arg, operand_scope(symbols, state))
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
    if resolve_register_name(duration_arg, state) is not None:
        return False
    duration = resolve_arg_value(duration_arg, operand_scope(symbols, state))
    if not isinstance(duration, Concrete) or duration.value <= RT_DURATION_MAX:
        return False
    state.diagnostics.append(
        Diagnostic(
            severity="error",
            category="invalid_argument_value",
            message=f"{instruction.op} duration must be in range 0..{RT_DURATION_MAX}, got {duration.value}.",
            source=instruction.source,
            details={
                "op": instruction.op,
                "argument_index": duration_arg_index,
                "operand": duration_arg.raw,
                "operand_role": "duration",
                "constraint": "range",
                "min": 0,
                "max": RT_DURATION_MAX,
                "value": duration.value,
            },
        )
    )
    return True


def valid_rt_duration(duration: int) -> bool:
    return duration == 0 or _RT_DURATION_MIN <= duration <= RT_DURATION_MAX


def invalid_rt_duration(instruction: Instr, spec: InstructionSpec, scope: SymbolTable) -> Diagnostic | None:
    is_else = instruction.op == "set_cond"
    index = 3 if is_else else rt_duration_arg_for_instruction(spec, instruction)
    if index is None or index >= len(instruction.args) or instruction.op == "fb_acq_tb_mock":
        return None
    arg = instruction.args[index]
    value = resolve_arg_value(arg, scope)
    if not isinstance(value, Concrete) or valid_rt_duration(value.value):
        return None
    if instruction.op == "fb_cmd" and raw_linq_operation(arg_int(instruction.args[0], scope)) == "fb_acq_tb_mock":
        return None
    role = "else_duration" if is_else else "duration"
    return Diagnostic(
        severity="error", category="invalid_argument_value", source=instruction.source,
        message=(f"{instruction.op} {role.replace('_', ' ')} {value.value} ns is invalid. "
                 f"RT duration must be zero or {_RT_DURATION_MIN}..{RT_DURATION_MAX} ns."),
        details={"reason": "invalid_rt_duration", "op": instruction.op, "argument_index": index, "operand": arg.raw,
                 "operand_role": role, "value": value.value, "min_nonzero": _RT_DURATION_MIN, "max": RT_DURATION_MAX},
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
        value = resolve_arg_value(arg, operand_scope(symbols, state))
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
        if operand_role == "feedback_source" and resolve_register_name(arg, state) is not None:
            continue
        if _should_reject_unresolved_rt_index_operand(instruction.op, operand_role) and diagnose_unresolved_index_operand(
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
        for index, role, _lower, _upper in official_operand_ranges(op)
    )


def _diagnose_official_operand_ranges(
    state: AnalysisState,
    instruction: Instr,
    symbols: SymbolTable,
) -> bool:
    if is_packed_feedback_register_form(instruction, state):
        return False
    emitted = False
    scope = operand_scope(symbols, state)
    for argument_index, operand_role, lower, upper in official_operand_ranges(instruction.op, len(instruction.args)):
        if argument_index >= len(instruction.args):
            continue
        arg = instruction.args[argument_index]
        value: Value
        if operand_role == "branch_target":
            target_arg = branch_target_arg(arg, state, symbols)
            if target_arg.kind != "imm":
                continue
            value = Concrete(int(target_arg.value))
        elif resolve_register_name(arg, state) is not None:
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


def is_packed_feedback_register_form(instruction: Instr, state: AnalysisState) -> bool:
    return (
        instruction.op in {"fb_com_cfg", "fb_acq_tb_cfg", "fb_com_extra", "fb_acq_tb_extra", "fb_acq_tb_mock"}
        and len(instruction.args) == 2
        and resolve_register_name(instruction.args[0], state) is not None
    )


def official_operand_ranges(op: str, argument_count: int = 1) -> list[tuple[int, str, int, int]]:
    signed_32 = (-2147483648, 2147483647)
    classical_imm = (-2147483648, 0xFFFFFFFF)
    if op in core.DEVICE_RANGES:
        return list(core.DEVICE_RANGES[op])
    if op in core_rt.OPERAND_RANGES:
        return list(core_rt.OPERAND_RANGES[op])
    if op == "stop":
        return [(0, "stop_status", *signed_32)]
    if op == "sw_req":
        return [(0, "software_request", *signed_32)]
    if op == "fb_pop_data":
        return [(0, "feedback_pop_tag", 0, 65535)]
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
    return _alu_operand_ranges(op)


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


def _resolved_branch_target_arg(arg: Arg, value: Value) -> Arg | None:
    if isinstance(value, Concrete):
        return Arg(kind="imm", value=value.value, raw=arg.raw)
    if isinstance(value, Symbolic) and value.expr.startswith("@") and len(value.expr) > 1:
        return Arg(kind="label", value=value.expr[1:], raw=arg.raw)
    return None


def branch_target_arg(arg: Arg, state: AnalysisState, symbols: SymbolTable) -> Arg:
    if arg.kind in {"label", "imm"}:
        return arg
    if arg.kind == "symbol" and str(arg.value).startswith("$"):
        origin = state.symbol_origins.get("defs", {}).get(str(arg.value)[1:], {})
        raw = origin.get("raw", "")
        if raw.startswith("@"):
            return Arg(kind="label", value=raw[1:], raw=arg.raw)
    scope = operand_scope(symbols, state)
    if arg.kind == "reg":
        return _resolved_branch_target_arg(arg, resolve_arg_value(arg, scope)) or arg
    register = resolve_register_name(arg, state)
    if register is not None:
        register_arg = Arg(kind="reg", value=register, raw=arg.raw)
        return _resolved_branch_target_arg(register_arg, resolve_arg_value(register_arg, scope)) or register_arg
    value = resolve_arg_value(arg, scope)
    return _resolved_branch_target_arg(arg, value) or arg


def compiled_pc(state: AnalysisState, pc: int) -> int | None:
    if pc == state.pc and state.interior_pc is not None:
        return state.interior_pc
    return state.compiled_addresses.entries.get(pc) if state.compiled_addresses is not None else pc


def invalid_branch_target_reason(state: AnalysisState, value: Value) -> str:
    if not isinstance(value, Concrete):
        return "Branch destination is unknown."
    address_space = "Compiled branch target" if state.compiled_addresses is not None else "Branch target"
    return f"{address_space} PC {value.value} has no instruction."


def concrete_branch_test(
    state: AnalysisState, instruction: Instr, scope: SymbolTable,
) -> tuple[bool | None, dict[str, bool] | None]:
    if instruction.op == "loop":
        words = (state.registers[resolve_register_name(instruction.args[0], state)].value,)
    else:
        words = tuple(resolve_arg_value(arg, scope).value for arg in instruction.args[:-1])
    decision, _, flags = core.branch_effect(instruction, words, state.alu_flags)
    return decision, flags


def concrete_branch_destination_ready(
    state: AnalysisState, instruction: Instr, scope: SymbolTable, taken: bool | None = None,
) -> bool:
    if taken is None:
        taken, _ = concrete_branch_test(state, instruction, scope)
    if not taken:
        return True
    target_index = 1 if instruction.op == "loop" else 2 if len(instruction.args) == 3 else 0
    value = resolve_arg_value(instruction.args[target_index], scope)
    if resolve_execution_target(state, value) is not None:
        return True
    end_concrete_execution(state, "error", invalid_branch_target_reason(state, value), instruction)
    return False


def emit_undefined_label(state: AnalysisState, instruction: Instr, target_label: str) -> None:
    diagnostic = Diagnostic(
        severity="error",
        category="undefined_label",
        message=f"Undefined label: @{target_label}",
        source=instruction.source,
        details={"label": target_label, "op": instruction.op},
    )
    if diagnostic not in state.static_diagnostics:
        state.diagnostics.append(diagnostic)


def latched_operand_value(op: str, arg: Arg, scope: SymbolTable, state: AnalysisState) -> Value:
    value = resolve_arg_value(arg, scope)
    register = resolve_register_name(arg, state)
    if register is None:
        return value
    # Decode only the value sent to the latch. Register traces keep the raw word.
    # https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#latched-instructions
    if isinstance(value, Concrete):
        return Concrete(core_rt.decode_register(op, value.value))
    elif op == "set_mrk" and isinstance(value, Symbolic) and pending_register_write(state, register) is None:
        ones, zeros = operand_known_bits(arg, value, state)
        if (ones | zeros) & 0xF == 0xF:
            return Concrete(ones & 0xF)
    return value


def unsupported_register_operand(
    state: AnalysisState, instruction: Instr, scope: SymbolTable, *, condition_disabled: bool = False,
) -> Diagnostic | None:
    for index, role, lower, upper in official_operand_ranges(instruction.op, len(instruction.args)):
        if index >= len(instruction.args) or condition_disabled and index in {1, 2}:
            continue
        arg = instruction.args[index]
        register = resolve_register_name(arg, state)
        if register is None:
            continue
        value = latched_operand_value(instruction.op, arg, scope, state)
        if (isinstance(value, Concrete) and not lower <= value.value <= upper
                and pending_register_write(state, register) is None):
            return Diagnostic(
                severity="warning", category="analysis_incomplete", source=instruction.source,
                message=(f"{instruction.op} register {arg.raw} decodes to {value.value}, outside the supported "
                         f"{role} range {lower}..{upper}; truncation or wrapping is not modeled."),
                details={"op": instruction.op, "argument_index": index, "operand": arg.raw, "register": register,
                         "operand_role": role, "min": lower, "max": upper, "value": value.value},
            )
    return None


def diagnose_unresolved_index_operand(
    state: AnalysisState,
    instruction: Instr,
    arg: Arg,
    operand_index: int,
    operand_role: str,
) -> bool:
    if arg.kind not in {"symbol", "placeholder"}:
        return False
    symbol = arg_symbol_lookup_names(arg)[0]
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
