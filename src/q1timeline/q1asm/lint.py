from __future__ import annotations

import ast
import re
from collections import deque
from collections.abc import Iterable

from q1timeline.diagnostics import Diagnostic
from q1timeline.q1asm.ast import Arg, Instr, Program
from q1timeline.q1asm.instruction_table import get_instruction_spec


_REGISTER_NAME_RE = re.compile(r"R(?:[0-9]|[1-5][0-9]|6[0-3])")


def analyze_program_lints(program: Program, *, known_parameters: Iterable[str] = ()) -> list[Diagnostic]:
    """Report source-level concerns without requiring runtime register values."""
    if any(item.severity == "error" for item in program.diagnostics):
        return []

    used = {name for instruction in program.instructions for arg in instruction.args
            for name in _definition_references(arg, program)}
    pending = list(used)
    while pending:
        for name in _definition_references(program.defs[pending.pop()], program) - used:
            used.add(name)
            pending.append(name)
    diagnostics = [
        Diagnostic(
            severity="hint", category="unused_definition",
            message=f".DEF alias {name} is never referenced by an instruction.",
            source=program.def_sources.get(name), details={"name": name},
        )
        for name in program.defs if name not in used
    ]
    parameters = set(known_parameters)
    operands = [(arg, program.def_sources.get(name)) for name, arg in program.defs.items()]
    operands.extend((arg, instruction.source) for instruction in program.instructions for arg in instruction.args)
    for arg, source in operands:
        if arg.kind != "placeholder" or str(arg.value) in parameters:
            continue
        if re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*(?:[!:].*)?", str(arg.value)):
            continue
        if _template_tree(arg) is None:
            diagnostics.append(Diagnostic(
                severity="warning", category="template_expression_syntax",
                message=f"Template operand is not a valid Python expression or f-string field: {arg.raw}.",
                source=source, details={"operand": arg.raw},
            ))

    successors = _successors(program)
    if successors is None or not program.instructions:
        return diagnostics
    instructions = {instruction.pc: instruction for instruction in program.instructions}
    reachable: set[int] = set()
    pending = [0]
    while pending:
        pc = pending.pop()
        if pc in reachable:
            continue
        reachable.add(pc)
        pending.extend(target for target in successors[pc] if target is not None and target not in reachable)

    regions: list[tuple[Instr, Instr]] = []
    for instruction in program.instructions:
        if instruction.pc in reachable:
            continue
        if regions and regions[-1][1].pc + 1 == instruction.pc:
            regions[-1] = (regions[-1][0], instruction)
        else:
            regions.append((instruction, instruction))
    for first, last in regions:
        diagnostics.append(Diagnostic(
            severity="warning", category="unreachable_code",
            message="Instructions in this region cannot be reached from program entry.",
            source=first.source,
            details={"start_pc": first.pc, "end_pc": last.pc, "end_line": last.source.line},
        ))

    for pc in sorted(reachable):
        if None in successors[pc]:
            diagnostics.append(Diagnostic(
                severity="warning", category="missing_stop",
                message="A reachable path falls past the end of the program without stop.",
                source=instructions[pc].source, details={"pc": pc},
            ))

    effects = {pc: _register_effects(instruction, program) for pc, instruction in instructions.items()}
    # Registers in this set are certainly overwritten before their next read.
    # Empty terminal sets keep final register values observable to the caller.
    overwritten: dict[int, set[str]] = {pc: set() for pc in reachable}
    predecessors: dict[int, set[int]] = {pc: set() for pc in reachable}
    for pc in reachable:
        for target in successors[pc]:
            if target is not None:
                predecessors[target].add(pc)
    pending_pcs = deque(sorted(reachable, reverse=True))
    queued = set(reachable)
    while pending_pcs:
        pc = pending_pcs.popleft()
        queued.remove(pc)
        paths = [overwritten[target] if target is not None else set() for target in successors[pc]]
        following = set.intersection(*paths) if paths else set()
        reads, writes = effects[pc]
        before = (following | writes) - reads
        if before != overwritten[pc]:
            overwritten[pc] = before
            for predecessor in predecessors[pc] - queued:
                pending_pcs.append(predecessor)
                queued.add(predecessor)
    for pc in sorted(reachable):
        paths = [overwritten[target] if target is not None else set() for target in successors[pc]]
        following = set.intersection(*paths) if paths else set()
        for register in sorted(effects[pc][1] & following):
            diagnostics.append(Diagnostic(
                severity="warning", category="overwritten_register",
                message=f"The value written to {register} is overwritten on every path before it is read.",
                source=instructions[pc].source, details={"register": register},
            ))
    return diagnostics


def _definition_references(arg: Arg, program: Program) -> set[str]:
    if arg.kind == "symbol" and str(arg.value).startswith("$"):
        name = str(arg.value)[1:]
        return {name} if name in program.defs else set()
    if arg.kind == "placeholder" and program.defs:
        return {name.removeprefix("$") for name in _template_names(arg)
                if not _REGISTER_NAME_RE.fullmatch(name)} & program.defs.keys()
    return set()


def _template_tree(arg: Arg) -> ast.AST | None:
    sources = (str(arg.value), "f" + repr(arg.raw), 'f"""' + arg.raw + '"""', "f'''" + arg.raw + "'''")
    for source in sources:
        try:
            return ast.parse(source, mode="eval")
        except (SyntaxError, ValueError, RecursionError):
            continue
    return None


def _template_names(arg: Arg) -> set[str]:
    tree = _template_tree(arg)
    if tree is not None:
        return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    # Opaque parameters and $NAME template extensions can still reference aliases.
    return set(re.findall(r"\$?[A-Za-z_][A-Za-z0-9_]*", str(arg.value)))


def _alias_value(arg: Arg, program: Program) -> Arg:
    if arg.kind == "symbol" and str(arg.value).startswith("$"):
        return program.defs.get(str(arg.value)[1:], arg)
    return arg


def _successors(program: Program) -> dict[int, set[int | None]] | None:
    instructions = {instruction.pc: instruction for instruction in program.instructions}
    if any(get_instruction_spec(item.op).category == "unknown" or get_instruction_spec(item.op).validate(item)
           for item in program.instructions):
        return None
    result: dict[int, set[int | None]] = {}
    pending = [0] if instructions else []
    while pending:
        pc = pending.pop()
        if pc in result:
            continue
        instruction = instructions[pc]
        spec = get_instruction_spec(instruction.op)
        target_index = None
        if spec.category == "branch":
            target_index = 1 if instruction.op == "loop" else 2 if len(instruction.args) == 3 else 0
        for index, arg in enumerate(instruction.args):
            if index != target_index and _alias_value(arg, program).kind == "label":
                return None
        following = instruction.pc + 1 if instruction.pc + 1 in instructions else None
        if instruction.op in {"stop", "illegal"}:
            result[instruction.pc] = set()
        elif target_index is not None:
            target = _alias_value(instruction.args[target_index], program)
            # Numeric addresses can differ from source PCs after assembler expansion.
            # Indirect or unresolved destinations could enter any source region.
            if target.kind != "label" or str(target.value) not in program.labels:
                return None
            target_pc = program.labels[str(target.value)]
            if target_pc not in instructions:
                return None
            result[instruction.pc] = {target_pc}
            if instruction.op != "jmp":
                result[instruction.pc].add(following)
        else:
            result[instruction.pc] = {following}
        pending.extend(target for target in result[instruction.pc] if target is not None and target not in result)
    return result


def _register_effects(instruction: Instr, program: Program) -> tuple[set[str], set[str]]:
    if instruction.op in {"move", "not", "fb_pop_data"}:
        destinations = {1}
    elif instruction.op in {"add", "sub", "and", "or", "xor", "asl", "asr", "lsl", "lsr",
                             "mulu16", "muls16", "mulu32l", "mulu32h", "muls32l", "muls32h"}:
        destinations = {2}
    elif instruction.op in {"mulu32", "muls32"}:
        destinations = {2, 3}
    elif instruction.op == "fb_pull_data":
        destinations = {0, 1}
    elif instruction.op == "loop":
        destinations = {0}
    else:
        destinations = set()
    reads: set[str] = set()
    writes: set[str] = set()
    for index, arg in enumerate(instruction.args):
        value = _alias_value(arg, program)
        if value.kind == "reg":
            if index in destinations:
                writes.add(str(value.value))
            if index not in destinations or instruction.op == "loop":
                reads.add(str(value.value))
        if arg.kind == "placeholder" and index not in destinations:
            for name in _template_names(arg):
                if _REGISTER_NAME_RE.fullmatch(name):
                    reads.add(name)
                else:
                    alias = program.defs.get(name.removeprefix("$"))
                    if alias is not None and alias.kind == "reg":
                        reads.add(str(alias.value))
    return reads, writes
