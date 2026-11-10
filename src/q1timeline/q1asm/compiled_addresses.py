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
