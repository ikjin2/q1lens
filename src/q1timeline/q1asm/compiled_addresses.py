"""Address layout for the documented Q1ASM two-instruction macros."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from q1timeline.q1asm.ast import Instr


# Assembled with Qblox Instruments 1.4.1 q1asm -d. The first slot is cmp
# for jlt/jge and sub for loop. Source-level execution still handles the
# complete macro when the first slot is reached.
_BRANCH_INTERIORS = {("jlt", 3): "jb", ("jge", 3): "jae", ("loop", 2): "jnz"}


@dataclass(frozen=True)
class CompiledTarget:
    address: int
    source_pc: int
    interior: Instr | None = None


@dataclass(frozen=True)
class CompiledAddresses:
    entries: dict[int, int]
    targets: dict[int, CompiledTarget]
    labels: dict[str, int]


def next_compiled_pc(pc: int, item: Instr, *, taken: bool | None, target: int | None,
                     fault: bool, code_size: int) -> int:
    """Advance by the assembled instruction width, including entry into a macro interior."""
    if fault:
        return pc
    if taken:
        if target is None or not 0 <= target < code_size:
            raise ValueError(f"No instruction at compiled PC {target}")
        return target
    return pc + (2 if (item.op, len(item.args)) in _BRANCH_INTERIORS else 1)


def source_position(layout: CompiledAddresses | None, address: int, source_count: int) -> tuple[int, int | None]:
    if layout is None:
        return address, None
    if address == len(layout.targets):
        return source_count, None
    target = layout.targets[address]
    return target.source_pc, address if target.interior is not None else None


def next_source_position(layout: CompiledAddresses | None, item: Instr, interior_pc: int | None,
                         source_count: int, *, taken: bool | None = None,
                         target: int | None = None) -> tuple[int, int | None]:
    """Return the source cursor after an admitted Q1 issue in a scheduled engine."""
    pc = layout.entries[item.pc] if layout else item.pc
    if interior_pc is not None:
        pc = interior_pc
    address = next_compiled_pc(pc, item, taken=taken, target=target, fault=False,
                               code_size=len(layout.targets) if layout else source_count)
    return source_position(layout, address, source_count)


def compile_addresses(instructions: Mapping[int, Instr], labels: Mapping[str, int]) -> CompiledAddresses | None:
    if not any((item.op, len(item.args)) in _BRANCH_INTERIORS for item in instructions.values()):
        return None
    entries: dict[int, int] = {}
    targets: dict[int, CompiledTarget] = {}
    address = 0
    for source_pc, instruction in sorted(instructions.items()):
        entries[source_pc] = address
        targets[address] = CompiledTarget(address, source_pc)
        address += 1
        interior = _BRANCH_INTERIORS.get((instruction.op, len(instruction.args)))
        if interior:
            branch = replace(instruction, label=None, op=interior, args=[instruction.args[-1]])
            targets[address] = CompiledTarget(address, source_pc, branch)
            address += 1
    # A trailing label still resolves as a value, even though it cannot be
    # used as an executable branch destination.
    compiled_labels = {name: entries.get(source_pc, address) for name, source_pc in labels.items()}
    return CompiledAddresses(entries, targets, compiled_labels)
