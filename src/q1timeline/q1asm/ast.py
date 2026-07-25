from __future__ import annotations

from dataclasses import dataclass, field
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
