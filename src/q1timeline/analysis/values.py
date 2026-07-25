from __future__ import annotations

import operator
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from q1timeline.diagnostics import Diagnostic
from q1timeline.q1asm.ast import Arg, SourceLocation


CompareOp = Literal["<", "<=", "==", "!=", ">=", ">"]
SymbolTable = dict[str, "Value"]
_INTEGER_TEXT_RE = re.compile(r"^[+-]?(?:0[xX][0-9a-fA-F]+|0[bB][01]+|\d+)$")


class Value:
    pass


@dataclass(frozen=True)
class Concrete(Value):
    value: int

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class Symbolic(Value):
    expr: str
    unresolved_symbols: tuple[str, ...] = field(default_factory=tuple, compare=False)

    def __str__(self) -> str:
        return self.expr


@dataclass(frozen=True)
class Range(Value):
    min_value: Value | None
    max_value: Value | None

    def __str__(self) -> str:
        return f"range({_display(self.min_value)}, {_display(self.max_value)})"


@dataclass(frozen=True)
class Unknown(Value):
    reason: str

    def __str__(self) -> str:
        return f"unknown({self.reason})"


@dataclass(frozen=True)
class RuntimeDependent(Value):
    source: str

    def __str__(self) -> str:
        return f"runtime_dependent({self.source})"


@dataclass(frozen=True)
class DurationResolution:
    value: Value
    requires_unknown_region: bool = False


def add_values(left: Value, right: Value) -> Value:
    return _binary_op(left, "+", right)


def subtract_values(left: Value, right: Value) -> Value:
    return _binary_op(left, "-", right)


def multiply_value(value: Value, factor: int) -> Value:
    if isinstance(value, Concrete):
        return Concrete(value.value * factor)
    if isinstance(value, Range):
        return Range(
            multiply_value(value.min_value, factor) if value.min_value is not None else None,
            multiply_value(value.max_value, factor) if value.max_value is not None else None,
        )
    if isinstance(value, RuntimeDependent):
        return RuntimeDependent(f"{value.source} * {factor}")
    if isinstance(value, Unknown):
        return Unknown(f"{value.reason} * {factor} is unknown")
    return Symbolic(f"{_expr(value)} * {factor}", _unresolved_symbols(value))


def compare_values(left: Value, op: CompareOp, right: Value) -> bool | RuntimeDependent:
    if isinstance(left, Concrete) and isinstance(right, Concrete):
        operations = {
            "<": operator.lt,
            "<=": operator.le,
            "==": operator.eq,
            "!=": operator.ne,
            ">=": operator.ge,
            ">": operator.gt,
        }
        return operations[op](left.value, right.value)
    return RuntimeDependent(f"{_expr(left)} {op} {_expr(right)}")


def value_to_json(value: Value) -> dict[str, Any]:
    if isinstance(value, Concrete):
        return {"kind": "concrete", "value": value.value, "display": str(value)}
    if isinstance(value, Symbolic):
        return {"kind": "symbolic", "expr": value.expr, "display": str(value)}
    if isinstance(value, Range):
        return {
            "kind": "range",
            "min": value_to_json(value.min_value) if value.min_value is not None else None,
            "max": value_to_json(value.max_value) if value.max_value is not None else None,
            "display": str(value),
        }
    if isinstance(value, Unknown):
        return {"kind": "unknown", "reason": value.reason, "display": str(value)}
    if isinstance(value, RuntimeDependent):
        return {"kind": "runtime_dependent", "source": value.source, "display": str(value)}
    raise TypeError(f"Unsupported value type: {type(value)!r}")


def symbol_table_from_params(params: dict[str, Any]) -> SymbolTable:
    symbols: SymbolTable = {}
    for key, raw_value in params.items():
        if isinstance(raw_value, int) and not isinstance(raw_value, bool):
            symbols[key] = Concrete(raw_value)
        elif isinstance(raw_value, str) and _INTEGER_TEXT_RE.match(raw_value.strip()):
            text = raw_value.strip()
            base = 0 if text.lower().lstrip("+-").startswith(("0x", "0b")) else 10
            symbols[key] = Concrete(int(text, base))
        else:
            symbols[key] = Symbolic(str(raw_value))
    return symbols


def resolve_def_values(
    defs: dict[str, Arg],
    symbols: SymbolTable,
    diagnostics: list[Diagnostic] | None = None,
) -> SymbolTable:
    diagnostic_sink = diagnostics if diagnostics is not None else []
    resolved: SymbolTable = {}
    scope = dict(symbols)
    for name, arg in defs.items():
        value = resolve_arg_value(arg, scope, diagnostic_sink, required=False)
        resolved[name] = value
        scope[name] = value
    return resolved


def resolve_arg_value(
    arg: Arg,
    symbols: SymbolTable,
    diagnostics: list[Diagnostic] | None = None,
    *,
    required: bool = False,
    source: SourceLocation | None = None,
) -> Value:
    if diagnostics is None:
        diagnostics = []

    if arg.kind == "imm":
        return Concrete(int(arg.value))
    if arg.kind == "reg":
        return symbols.get(str(arg.value), Unknown(str(arg.value)))
    if arg.kind == "label":
        label_name = f"@{arg.value}"
        return symbols.get(label_name, Symbolic(label_name))
    if arg.kind in {"symbol", "placeholder"}:
        name = str(arg.value)
        for lookup_name in _symbol_lookup_names(name):
            if lookup_name in symbols:
                value = symbols[lookup_name]
                if required:
                    _diagnose_unresolved_symbolic_value(value, diagnostics, source)
                return value
        if required:
            diagnostics.append(
                Diagnostic(
                    severity="warning",
                    category="unresolved_symbol",
                    message=f"Unresolved symbol: {name}",
                    source=source,
                    details={"symbol": name},
                )
            )
        return Symbolic(name, (name,))
    return Symbolic(arg.raw)


def _symbol_lookup_names(name: str) -> tuple[str, ...]:
    if name.startswith("$") and len(name) > 1:
        return (name[1:], name)
    return (name,)


def resolve_duration_arg(
    arg: Arg,
    symbols: SymbolTable,
    diagnostics: list[Diagnostic] | None = None,
    *,
    source: SourceLocation | None = None,
) -> DurationResolution:
    if diagnostics is None:
        diagnostics = []

    if arg.kind == "reg" and str(arg.value) not in symbols:
        diagnostics.append(
            Diagnostic(
                severity="warning",
                category="unknown_duration",
                message=f"Duration register is unknown: {arg.value}",
                source=source,
                details={"register": str(arg.value)},
            )
        )
        return DurationResolution(value=Unknown(str(arg.value)), requires_unknown_region=True)

    value = resolve_arg_value(arg, symbols, diagnostics, required=True, source=source)
    if isinstance(value, Symbolic):
        diagnostics.append(
            Diagnostic(
                severity="info",
                category="symbolic_duration",
                message=f"Duration remains symbolic: {value.expr}",
                source=source,
                details={"expression": value.expr},
            )
        )
    return DurationResolution(value=value, requires_unknown_region=isinstance(value, Unknown))


def _binary_op(left: Value, op: Literal["+", "-"], right: Value) -> Value:
    if isinstance(left, Range):
        return Range(
            _binary_op(left.min_value, op, right) if left.min_value is not None else None,
            _binary_op(left.max_value, op, right) if left.max_value is not None else None,
        )
    if isinstance(right, Range):
        return Range(
            _binary_op(left, op, right.min_value) if right.min_value is not None else None,
            _binary_op(left, op, right.max_value) if right.max_value is not None else None,
        )
    if isinstance(left, Concrete) and isinstance(right, Concrete):
        if op == "+":
            return Concrete(left.value + right.value)
        return Concrete(left.value - right.value)
    if isinstance(left, RuntimeDependent) or isinstance(right, RuntimeDependent):
        return RuntimeDependent(f"{_expr(left)} {op} {_expr(right)}")
    if isinstance(left, Unknown) or isinstance(right, Unknown):
        return Unknown(f"{_expr(left)} {op} {_expr(right)} is unknown")
    return Symbolic(f"{_expr(left)} {op} {_expr(right)}", _unresolved_symbols(left, right))


def _diagnose_unresolved_symbolic_value(
    value: Value,
    diagnostics: list[Diagnostic],
    source: SourceLocation | None,
) -> None:
    if not isinstance(value, Symbolic):
        return
    for symbol in value.unresolved_symbols:
        diagnostics.append(
            Diagnostic(
                severity="warning",
                category="unresolved_symbol",
                message=f"Unresolved symbol: {symbol}",
                source=source,
                details={"symbol": symbol},
            )
        )


def _unresolved_symbols(*values: Value) -> tuple[str, ...]:
    symbols: list[str] = []
    for value in values:
        if isinstance(value, Symbolic):
            symbols.extend(value.unresolved_symbols)
    return tuple(dict.fromkeys(symbols))


def _expr(value: Value) -> str:
    if isinstance(value, Concrete):
        return str(value.value)
    if isinstance(value, Symbolic):
        return value.expr
    if isinstance(value, Unknown):
        return value.reason
    if isinstance(value, RuntimeDependent):
        return value.source
    return str(value)


def _display(value: Value | None) -> str:
    if value is None:
        return "unknown"
    return _expr(value)
