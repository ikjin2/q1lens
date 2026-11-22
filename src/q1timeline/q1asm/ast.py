from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from q1timeline.diagnostics import Diagnostic


ArgKind = Literal["imm", "reg", "symbol", "label", "placeholder", "raw"]


@dataclass(frozen=True)
class SourceLocation:
    file: str
    line: int
    column: int = 1
    raw: str = ""


@dataclass(frozen=True)
class Arg:
    kind: ArgKind
    value: int | str
    raw: str


@dataclass(frozen=True)
class Instr:
    pc: int
    label: str | None
    op: str
    args: list[Arg]
    source: SourceLocation


@dataclass(frozen=True)
class Program:
    file: str
    defs: dict[str, Arg] = field(default_factory=dict)
    labels: dict[str, int] = field(default_factory=dict)
    instructions: list[Instr] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    def_sources: dict[str, SourceLocation] = field(default_factory=dict)


def duplicate_def_names(program: Program) -> set[str]:
    return {item.details["name"] for item in program.diagnostics
            if item.message.startswith("Duplicate .DEF name:") and "name" in item.details}


def numeric_defs(program: Program) -> dict[str, Arg]:
    """Return the unambiguous integer literal declarations."""
    duplicates = duplicate_def_names(program)
    return {name: arg for name, arg in program.defs.items()
            if arg.kind == "imm" and name in program.def_sources and name not in duplicates}


def with_def_values(program: Program, values: dict[str, int]) -> Program:
    changes = {name: Arg("imm", value, str(value)) for name, value in values.items()
               if value != program.defs[name].value}
    return replace(program, defs={**program.defs, **changes}) if changes else program
