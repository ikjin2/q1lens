"""Integer expressions shared by Analysis, output rendering and property proofs."""
from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass, field
from typing import Any, Mapping


_BINARY = {"+": operator.add, "-": operator.sub, "*": operator.mul, "min": min, "max": max}
_WORD = 1 << 32


def _next_term(text: str) -> str:
    return " - " + text[1:] if text.startswith("-") else " + " + text


@dataclass(frozen=True, slots=True)
class Expr:
    op: str
    args: tuple[Expr, ...] = ()
    value: int | str | None = None
    _text: str | None = field(default=None, init=False, repr=False, compare=False)
    _display: str | None = field(default=None, init=False, repr=False, compare=False)

    def __setstate__(self, state):
        # Older frozen-slot pickles contain fewer formatting caches.
        Expr.__init__(self, *state[:3])

    @classmethod
    def const(cls, value: int) -> Expr:
        if type(value) is not int:
            raise ValueError(f"Expected an integer, got {value!r}")
        return cls("const", value=value)

    @classmethod
    def symbol(cls, name: str) -> Expr:
        return cls("symbol", value=name.removeprefix("$"))

    @classmethod
    def parse(cls, text: str) -> Expr:
        """Read the polynomial subset of legacy display strings, without executing them."""
        text = text.strip()
        if re.fullmatch(r"\$?[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", text):
            return cls.symbol(text)

        def visit(node: ast.AST) -> Expr:
            if isinstance(node, ast.Name):
                return cls.symbol(node.id)
            if isinstance(node, ast.Attribute) and isinstance(node.value, (ast.Name, ast.Attribute)):
                # Dots belong to Q1ASM parameter names, with no Python attribute lookup.
                return cls.symbol(f"{visit(node.value).value}.{node.attr}")
            if isinstance(node, ast.Constant):
                return cls.const(node.value)
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                arg = visit(node.operand)
                return arg if isinstance(node.op, ast.UAdd) else -arg
            if isinstance(node, ast.BinOp) and type(node.op) in (ast.Add, ast.Sub, ast.Mult):
                return visit(node.left)._binary({ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}[type(node.op)], visit(node.right))
            raise ValueError(f"Unsupported integer expression: {text!r}")

        try:
            return visit(ast.parse(re.sub(r"\$(?=[A-Za-z_])", "", text), mode="eval").body)
        except (SyntaxError, RecursionError) as error:
            raise ValueError(f"Unsupported integer expression: {text!r}") from error

    @classmethod
    def from_value_json(cls, data: Any) -> Expr:
        if isinstance(data, int):
            return cls.const(data)
        if isinstance(data, str):
            return cls.parse(data)
        if isinstance(data, Mapping):
            if data.get("kind") == "concrete":
                return cls.const(data["value"])
            if data.get("kind") == "symbolic":
                return cls.from_json(data["tree"]) if "tree" in data else cls.parse(data["expr"])
        raise ValueError(f"Expected an integer or symbolic expression, got {data!r}")

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Expr:
        if data["op"] == "const":
            return cls.const(data["value"])
        if data["op"] == "symbol":
            return cls.symbol(data["value"])
        if data["op"] not in {*_BINARY, "domain", "count", "word", "signed"}:
            raise ValueError(f"Unknown expression operation: {data['op']}")
        return cls(data["op"], tuple(map(cls.from_json, data["args"])))

    def to_json(self) -> dict[str, Any]:
        if self.op in {"const", "symbol"}:
            return {"op": self.op, "value": self.value}
        return {"op": self.op, "args": [arg.to_json() for arg in self.args]}

    def _binary(self, op: str, other: Expr | int) -> Expr:
        other = other if isinstance(other, Expr) else Expr.const(other)
        if self.op == other.op == "const":
            return Expr.const(_BINARY[op](self.value, other.value))
        if (op in {"+", "-"} and other == _ZERO) or (op == "*" and other == _ONE):
            return self
        if (op == "+" and self == _ZERO) or (op == "*" and self == _ONE):
            return other
        if op == "+":
            if self == other:
                return Expr.const(2) * self
            # A long packet stream is a sum, not a recursively nested call per dwell.
            left = self.args if self.op == "+" else (self,)
            right = other.args if other.op == "+" else (other,)
            if left[-1].op == right[0].op == "const":
                total = left[-1] + right[0]
                terms = (*left[:-1], *((total,) if total.value else ()), *right[1:])
                return terms[0] if len(terms) == 1 else Expr("+", terms)
            if self.op == "*" and self.args[0].op == "const" and self.args[1] == other:
                return Expr.const(self.args[0].value + 1) * other
            total = Expr("+", (*left, *right))
            # A trace often appends one dwell to a sum it has already displayed.
            if self.op == "+" and self._text not in (None, "0") and other.op != "+" and other._text is not None:
                object.__setattr__(total, "_text", self._text + _next_term(other._text))
            return total
        if op in {"min", "max"} and self == other:
            return self
        return Expr(op, (self, other))

    def __add__(self, other): return self._binary("+", other)
    def __radd__(self, other): return Expr.const(other) + self
    def __sub__(self, other): return self._binary("-", other)
    def __rsub__(self, other): return Expr.const(other) - self
    def __mul__(self, other): return self._binary("*", other)
    def __rmul__(self, other): return Expr.const(other) * self
    def __neg__(self): return Expr.const(0) - self
    def minimum(self, other): return self._binary("min", other)
    def maximum(self, other): return self._binary("max", other)

    def in_range(self, low: int, high: int) -> Expr:
        return Expr("domain", (self, Expr.const(low), Expr.const(high)))

    def loop_count(self) -> Expr:
        return Expr("count", (self.in_range(0, _WORD - 1),))

    def word(self) -> Expr:
        return self if self.op == "word" else Expr("word", (self,))

    def signed(self) -> Expr:
        return Expr("signed", (self,))

    def free_symbols(self) -> frozenset[str]:
        if self.op == "symbol":
            return frozenset([self.value])
        return frozenset().union(*(arg.free_symbols() for arg in self.args))

    def substitute(self, replacements: Mapping[str, Expr | int]) -> Expr:
        if self.op == "symbol" and self.value in replacements:
            value = replacements[self.value]
            return value if isinstance(value, Expr) else Expr.const(value)
        if not self.args:
            return self
        args = tuple(arg.substitute(replacements) for arg in self.args)
        if self.op == "+":
            return sum(args, Expr.const(0))
        if self.op in _BINARY:
            return args[0]._binary(self.op, args[1])
        return Expr(self.op, args)

    def evaluate(self, params: Mapping[str, int]) -> int:
        if self.op == "const":
            return self.value
        if self.op == "symbol":
            return operator.index(params[self.value])
        args = [arg.evaluate(params) for arg in self.args]
        first = args[0]
        if self.op == "+":
            return sum(args)
        if self.op == "domain":
            if not args[1] <= first <= args[2]:
                raise ValueError(f"{self.args[0]} = {first} is outside [{args[1]}, {args[2]}]")
            return first
        if self.op == "count":
            return first or _WORD
        if self.op == "word":
            return first % _WORD
        if self.op == "signed":
            return (first + _WORD // 2) % _WORD - _WORD // 2
        return _BINARY[self.op](*args)

    def bounds(self, ranges: Mapping[str, tuple[int, int]]) -> tuple[int, int]:
        if self.op == "const":
            return self.value, self.value
        if self.op == "symbol":
            return ranges[self.value]
        if self.op == "domain":
            low, high = self.args[1].value, self.args[2].value
            try:
                a, b = self.args[0].bounds(ranges)
            except KeyError:
                return low, high
            low, high = max(a, low), min(b, high)
            if low > high:
                raise ValueError(f"Empty domain for {self.args[0]}")
            return low, high
        if self.op == "+":
            intervals = [arg.bounds(ranges) for arg in self.args]
            return sum(item[0] for item in intervals), sum(item[1] for item in intervals)
        a, b = self.args[0].bounds(ranges)
        if self.op == "count":
            return (a, b) if a else ((1 if b else _WORD), _WORD)
        if self.op in {"word", "signed"}:
            shift = _WORD // 2 if self.op == "signed" else 0
            if (a + shift) // _WORD != (b + shift) // _WORD:
                return -shift, _WORD - 1 - shift
            return (a + shift) % _WORD - shift, (b + shift) % _WORD - shift
        c, d = self.args[1].bounds(ranges)
        if self.op == "-":
            return a - d, b - c
        if self.op == "*":
            choices = (a*c, a*d, b*c, b*d)
            return min(choices), max(choices)
        f = _BINARY[self.op]
        return f(a, c), f(b, d)

    def to_z3(self, symbols: Mapping[str, Any] | None = None):
        import z3
        if self.op == "const":
            return z3.IntVal(self.value)
        if self.op == "symbol":
            return symbols[self.value] if symbols is not None and self.value in symbols else z3.Int(self.value)
        args = [arg.to_z3(symbols) for arg in self.args]
        if self.op == "+":
            return z3.Sum(*args)
        a = args[0]
        if self.op == "domain":
            return a
        if self.op == "count":
            return z3.If(a == 0, _WORD, a)
        if self.op == "word":
            return a % _WORD
        if self.op == "signed":
            return (a + _WORD // 2) % _WORD - _WORD // 2
        b = args[1]
        if self.op in {"min", "max"}:
            return z3.If(a <= b if self.op == "min" else a >= b, a, b)
        return _BINARY[self.op](a, b)

    def constraints_z3(self, symbols: Mapping[str, Any] | None = None) -> tuple:
        constraints = tuple(item for arg in self.args for item in arg.constraints_z3(symbols))
        if self.op == "domain":
            value, low, high = (arg.to_z3(symbols) for arg in self.args)
            return (*constraints, value >= low, value <= high)
        return constraints

    def __str__(self) -> str:
        """Compact algebraic spelling retained in the legacy JSON expr field."""
        if self._text is None:
            object.__setattr__(self, "_text", self._format(hardware=False))
        return self._text

    def display(self) -> str:
        """Human-readable expression with the hardware's zero-counter semantics."""
        if self._display is None:
            text = self._format(hardware=True)
            object.__setattr__(self, "_display", self._text if text == self._text else text)
        return self._display

    def _format(self, *, hardware: bool) -> str:
        if self.op in {"const", "symbol"}:
            return str(self.value)
        if self.op == "count":
            counter = self.args[0].args[0] if self.args[0].op == "domain" else self.args[0]
            if counter == _ZERO:
                return "4294967296 (0 → 2³²)" if hardware else str(_WORD)
            if hardware:
                return f"loop_count({counter._format(hardware=True)}, 0 → 2³²)"
        if self.op in {"domain", "count", "word", "signed"}:
            return self.args[0]._format(hardware=hardware)
        if self.op == "+":
            terms = [arg.display() if hardware else str(arg) for arg in self.args if arg != _ZERO]
            if not terms:
                return "0"
            first, *rest = terms
            return first + "".join(map(_next_term, rest))
        a, b = self.args
        if self.op in {"min", "max"}:
            return f"{self.op}({a._format(hardware=hardware)}, {b._format(hardware=hardware)})"
        def group(arg: Expr, right: bool) -> str:
            text = arg._format(hardware=hardware)
            while arg.op in {"domain", "word", "signed"} or arg.op == "count" and not hardware:
                arg = arg.args[0]
            if arg.op in {"+", "-"} and (self.op == "*" or right and self.op == "-"):
                return f"({text})"
            return text
        if self.op == "-" and b.op == "const" and b.value < 0:
            return f"{group(a, False)} + {-b.value}"
        if self.op == "-" and a == _ZERO:
            return f"-{group(b, True)}"
        return f"{group(a, False)} {self.op} {group(b, True)}"


_ZERO = Expr.const(0)
_ONE = Expr.const(1)
