from __future__ import annotations

import operator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from q1timeline.analysis.expr import Expr
from q1timeline.diagnostics import Diagnostic
from q1timeline.q1asm.ast import Arg, SourceLocation
from q1timeline.q1asm.parser import parameter_integer


CompareOp = Literal["<", "<=", "==", "!=", ">=", ">"]
SymbolTable = dict[str, "Value"]


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
    tree: Expr | None = field(default=None)

    def __post_init__(self) -> None:
        if self.tree is None:
            try:
                object.__setattr__(self, "tree", Expr.parse(self.expr))
            except ValueError:
                # Runtime provenance and unsupported ALU operations are display-only.
                pass

    def with_tree(self, tree: Expr) -> Symbolic:
        return Symbolic(self.expr, self.unresolved_symbols, tree)

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


def multiply_value(value: Value, factor: int | Value) -> Value:
    return _binary_op(value, "*", Concrete(factor) if isinstance(factor, int) else factor)


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
        return {"kind": "symbolic", "expr": value.expr, "display": value.tree.display() if value.tree is not None else str(value),
                **({"tree": value.tree.to_json()} if value.tree is not None else {})}
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
        word = parameter_integer(raw_value)
        symbols[key] = Concrete(word) if word is not None else Symbolic(str(raw_value))
    return symbols


def resolve_def_values(
    defs: dict[str, Arg],
    symbols: SymbolTable,
    diagnostics: list[Diagnostic] | None = None,
    *,
    labels: dict[str, int] | None = None,
) -> SymbolTable:
    diagnostic_sink = diagnostics if diagnostics is not None else []
    resolved: SymbolTable = {}
    scope = dict(symbols)
    scope.update({f"@{name}": Concrete(pc) for name, pc in (labels or {}).items()})
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
    unresolved_index: int | None = None,
) -> Value:
    if diagnostics is None:
        diagnostics = []

    if arg.kind == "imm":
        return Concrete(int(arg.value))
    if arg.kind == "reg":
        return symbols.get(str(arg.value), Unknown(str(arg.value)))
    if arg.kind == "label":
        label_name = f"@{arg.value}"
        return symbols[label_name] if label_name in symbols else Symbolic(label_name)
    if arg.kind in {"symbol", "placeholder"}:
        name = str(arg.value)
        for lookup_name in _symbol_lookup_names(name):
            if lookup_name in symbols:
                value = symbols[lookup_name]
                if required and isinstance(value, Symbolic):
                    for unresolved in value.unresolved_symbols:
                        _diagnose_unresolved_symbol(unresolved, diagnostics, source, unresolved_index)
                return value
        if required:
            _diagnose_unresolved_symbol(name, diagnostics, source, unresolved_index)
        return _unresolved_symbol(name.removeprefix("$"))
    return Symbolic(arg.raw)


@lru_cache(maxsize=256)
def _unresolved_symbol(name: str) -> Symbolic:
    return Symbolic(name, (name,))


@lru_cache(maxsize=256)
def _duration_symbol_tree(symbol: Expr) -> Expr:
    return symbol.in_range(4, 65535)


def _symbol_lookup_names(name: str) -> tuple[str, ...]:
    if name.startswith("$") and len(name) > 1:
        return (name, name[1:])
    return (name,)


def resolve_duration_arg(
    arg: Arg,
    symbols: SymbolTable,
    diagnostics: list[Diagnostic] | None = None,
    *,
    source: SourceLocation | None = None,
    unresolved_index: int | None = None,
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

    value = resolve_arg_value(arg, symbols, diagnostics, required=True, source=source, unresolved_index=unresolved_index)
    if isinstance(value, Symbolic):
        if value.tree is not None:
            tree = value.tree
            value = value.with_tree(_duration_symbol_tree(tree) if tree.op == "symbol" else tree.in_range(4, 65535))
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


def _binary_op(left: Value, op: Literal["+", "-", "*"], right: Value) -> Value:
    if isinstance(left, Range):
        if op == "*" and not isinstance(right, Concrete):
            return Unknown("product of unresolved ranges")
        low, high = left.min_value, left.max_value
        if op == "*" and right.value < 0:
            low, high = high, low
        return Range(
            _binary_op(low, op, right) if low is not None else None,
            _binary_op(high, op, right) if high is not None else None,
        )
    if isinstance(right, Range):
        if op == "*":
            return _binary_op(right, op, left)
        return Range(
            _binary_op(left, op, right.min_value) if right.min_value is not None else None,
            _binary_op(left, op, right.max_value) if right.max_value is not None else None,
        )
    if isinstance(left, Concrete) and isinstance(right, Concrete):
        if op == "+":
            return Concrete(left.value + right.value)
        if op == "*":
            return Concrete(left.value * right.value)
        return Concrete(left.value - right.value)
    if isinstance(left, RuntimeDependent) or isinstance(right, RuntimeDependent):
        return RuntimeDependent(f"{_expr(left)} {op} {_expr(right)}")
    if isinstance(left, Unknown) or isinstance(right, Unknown):
        return Unknown(f"{_expr(left)} {op} {_expr(right)} is unknown")
    a = left.tree if isinstance(left, Symbolic) else Expr.const(left.value) if type(left.value) is int else None
    b = right.tree if isinstance(right, Symbolic) else Expr.const(right.value) if type(right.value) is int else None
    if a is not None and b is not None:
        tree = {"+": operator.add, "-": operator.sub, "*": operator.mul}[op](a, b)
        return Symbolic(str(tree), _unresolved_symbols(left, right), tree)
    return Symbolic(f"{_expr(left)} {op} {_expr(right)}", _unresolved_symbols(left, right))


def _diagnose_unresolved_symbol(
    name: str,
    diagnostics: list[Diagnostic],
    source: SourceLocation | None,
    index: int | None,
) -> None:
    diagnostic = Diagnostic(severity="warning", category="unresolved_symbol", message=f"Unresolved symbol: {name}",
                            source=source, details={"symbol": name})
    # Probes can clear or replace their diagnostics after source validation.
    if index is not None and 0 <= index < len(diagnostics) and diagnostics[index] == diagnostic:
        return
    if diagnostic not in diagnostics:
        diagnostics.append(diagnostic)


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
