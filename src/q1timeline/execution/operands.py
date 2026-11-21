"""Operand bindings, source provenance, compiled addresses and register reads."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from q1timeline.analysis.values import (
    _symbol_lookup_names, Concrete, SymbolTable, Unknown, Value, resolve_arg_value, value_to_json,
)
from q1timeline.q1asm import core
from q1timeline.q1asm.ast import Arg, Instr
from q1timeline.q1asm.compiled_addresses import CompiledTarget
from q1timeline.execution.state import (
    AnalysisState, BranchAssumptionPath, CLASSICAL_REGISTER_MASK, VALID_BRANCH_ASSUMPTION_PATHS,
)

REGISTER_MAX_INDEX = 63


def symbol_origins(
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
            "references": arg_symbol_lookup_names(arg),
        }
        for name, arg in defs.items()
        if name in resolved_defs
    }
    return {"params": param_origins, "defs": def_origins}


def resolved_args_for_instruction(
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
    for lookup_name in arg_symbol_lookup_names(arg):
        if lookup_name in defs:
            origin = dict(defs[lookup_name])
            references = list(origin.pop("references", []))
            chain = [origin]
            chain.extend(params[name] for name in references if name in params)
            return chain
        if lookup_name in params:
            return [params[lookup_name]]
    return []


def arg_symbol_lookup_names(arg: Arg) -> list[str]:
    if arg.kind not in {"symbol", "placeholder"}:
        return []
    name = str(arg.value)
    if name.startswith("$") and len(name) > 1:
        return [name[1:], name]
    return [name]


def set_cond_is_disabled(state: AnalysisState, instruction: Instr, symbols: SymbolTable) -> bool:
    return (instruction.op == "set_cond" and bool(instruction.args)
            and resolve_arg_value(instruction.args[0], operand_scope(symbols, state)) == Concrete(0))


def is_valid_register_arg(arg: Arg) -> bool:
    if arg.kind != "reg" or not isinstance(arg.value, str):
        return False
    raw = arg.raw
    if not raw.startswith("R"):
        return False
    index = _register_index_from_raw(raw)
    return index is not None and 0 <= index <= REGISTER_MAX_INDEX


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


def physical_read_register(arg: Arg, state: AnalysisState) -> str | None:
    register = resolve_register_name(arg, state)
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
    if index is not None and 0 <= index <= REGISTER_MAX_INDEX and name == f"R{index}":
        return name
    if not is_valid_register_arg(Arg("reg", name, name)):
        return state.register_aliases.get(name)
    return None


def register_read_operands(instruction: Instr, state: AnalysisState) -> list[tuple[int, str]]:
    destination_indexes = core.destination_indexes(instruction.op)
    reads: list[tuple[int, str]] = []
    for index, arg in enumerate(instruction.args):
        if index in destination_indexes:
            continue
        register = physical_read_register(arg, state)
        if register is not None:
            reads.append((index, register))
    return reads


def register_write_operands(instruction: Instr, state: AnalysisState) -> set[str]:
    writes: set[str] = set()
    for index in core.destination_indexes(instruction.op):
        if index >= len(instruction.args):
            continue
        register = resolve_register_name(instruction.args[index], state)
        if register is not None:
            writes.add(register)
    return writes


def register_aliases(defs: dict[str, Arg]) -> dict[str, str]:
    return {name: str(arg.value).upper() for name, arg in defs.items() if arg.kind == "reg"}


def instruction_for_q1_timing(
    instruction: Instr, state: AnalysisState, symbols: SymbolTable, *, scope: SymbolTable | None = None,
) -> Instr:
    if not any(arg.kind in {"symbol", "placeholder"} for arg in instruction.args):
        return instruction
    if scope is None:
        scope = operand_scope(symbols, state)
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
    register = resolve_register_name(arg, state)
    if register is not None:
        return Arg(kind="reg", value=register, raw=arg.raw)
    value = resolve_arg_value(arg, scope)
    if isinstance(value, Concrete):
        return Arg(kind="imm", value=value.value, raw=arg.raw)
    return arg


def address_model(state: AnalysisState) -> str:
    return "compiled_q1asm" if state.compiled_addresses is not None else "source_instruction_index"


def address_labels(state: AnalysisState) -> dict[str, int]:
    return state.compiled_addresses.labels if state.compiled_addresses is not None else state.labels


def execution_instruction(state: AnalysisState) -> Instr | None:
    if state.interior_pc is not None and state.compiled_addresses is not None:
        return state.compiled_addresses.targets[state.interior_pc].interior
    return state.instructions_by_pc.get(state.pc)


def resolve_execution_target(state: AnalysisState, value: Value) -> CompiledTarget | None:
    if not isinstance(value, Concrete) or type(value.value) is not int:
        return None
    if state.compiled_addresses is not None:
        return state.compiled_addresses.targets.get(value.value)
    return CompiledTarget(value.value, value.value) if value.value in state.instructions_by_pc else None


def branch_target_metadata(target: CompiledTarget | None, state: AnalysisState) -> dict[str, Any]:
    result = {"target_pc": target.source_pc if target is not None else None}
    if target is not None and state.compiled_addresses is not None:
        result["compiled_target_pc"] = target.address
        if target.interior is not None:
            result["target_interior_op"] = target.interior.op
    return result


def normalise_branch_assumptions(
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


def docs_ordered_binary_args(instruction: Instr, state: AnalysisState) -> tuple[Arg, Arg]:
    left_arg = instruction.args[0]
    right_arg = instruction.args[1]
    if instruction.op in {"cmp", "sub", "asl", "asr", "lsl", "lsr"} and resolve_register_name(right_arg, state) is not None:
        if resolve_register_name(left_arg, state) is None:
            return right_arg, left_arg
    return left_arg, right_arg


def bounds_known_bits(bounds: tuple[int, int]) -> tuple[int, int]:
    low, high = bounds
    prefix = CLASSICAL_REGISTER_MASK ^ ((1 << (low ^ high).bit_length()) - 1)
    return low & prefix, ~high & prefix & CLASSICAL_REGISTER_MASK


def refine_bounds_with_bits(bounds: tuple[int, int] | None, known_bits: tuple[int, int]) -> tuple[int, int]:
    low, high = known_bits[0], CLASSICAL_REGISTER_MASK ^ known_bits[1]
    return (low, high) if bounds is None else (max(bounds[0], low), min(bounds[1], high))


def build_immutable_value_origins(symbols: SymbolTable, defs: dict[str, Arg], state: AnalysisState) -> dict[str, tuple[str, bool]]:
    origins = {name: (f"binding:param:{name}", False) for name in symbols}
    for name, arg in defs.items():
        origin = None
        if arg.kind == "imm":
            origin = f"constant:{int(arg.value) & CLASSICAL_REGISTER_MASK}", False
        elif arg.kind in {"symbol", "placeholder"} and physical_read_register(arg, state) is None:
            origin = next((origins[key] for key in _symbol_lookup_names(str(arg.value)) if key in origins), None)
            if origin is None:
                origin = f"template:{arg.value}", False
        origins[name] = origin if origin is not None else (f"binding:def:{name}", False)
    return origins


def unsigned_operand_bounds(arg: Arg, value: Value, state: AnalysisState) -> tuple[int, int] | None:
    if isinstance(value, Concrete):
        word = value.value & CLASSICAL_REGISTER_MASK
        return word, word
    register = physical_read_register(arg, state)
    if register is None:
        return None
    bounds = state.register_bounds.get(register, (0, CLASSICAL_REGISTER_MASK))
    known_bits = state.register_known_bits.get(register)
    return refine_bounds_with_bits(bounds, known_bits) if known_bits is not None else bounds


def operand_known_bits(arg: Arg, value: Value, state: AnalysisState) -> tuple[int, int]:
    bounds = unsigned_operand_bounds(arg, value, state)
    ones, zeros = bounds_known_bits(bounds) if bounds is not None else (0, 0)
    if not isinstance(value, Concrete):
        register_bits = state.register_known_bits.get(physical_read_register(arg, state), (0, 0))
        ones |= register_bits[0]
        zeros |= register_bits[1]
    return ones, zeros


def operand_scope(symbols: SymbolTable, state: AnalysisState) -> SymbolTable:
    label_symbols = {f"@{label}": Concrete(pc) for label, pc in address_labels(state).items()}
    scope = {**symbols, **label_symbols, **state.registers}
    for name, value in symbols.items():
        if not name.startswith("$"):
            scope.setdefault(f"${name}", value)
    if state.execution_mode == "concrete":
        for name in symbols:
            if is_valid_register_arg(Arg("reg", name, name)):
                scope[name] = state.registers.get(name, Unknown(name))
    for alias, register in state.register_aliases.items():
        value = scope.get(register, Unknown(register))
        scope[f"${alias}"] = value
        if not is_valid_register_arg(Arg("reg", alias, alias)):
            scope[alias] = value
    return scope


def resolve_register_name(arg: Arg, state: AnalysisState) -> str | None:
    if arg.kind == "reg":
        return str(arg.value).upper()
    if arg.kind != "symbol":
        return None
    name = str(arg.value)
    if not name.startswith("$") or len(name) <= 1:
        return None
    return state.register_aliases.get(name[1:])


def signed_32_bit_register_value(value: int) -> int:
    value &= CLASSICAL_REGISTER_MASK
    if value & 0x80000000:
        return value - 0x100000000
    return value


def arg_int(arg: Arg, scope: SymbolTable | None = None) -> int | None:
    if arg.kind == "imm":
        return int(arg.value)
    if scope is None:
        return None
    value = resolve_arg_value(arg, scope)
    if isinstance(value, Concrete):
        return value.value
    return None
