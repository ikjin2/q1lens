"""32-bit Q1 classical ALU results and status flags for concrete inputs."""

from __future__ import annotations

import operator

from q1timeline.q1asm.ast import Instr


_MASK = 0xFFFFFFFF
_SIGN = 0x80000000
_BITWISE = {"and": operator.and_, "or": operator.or_, "xor": operator.xor, "test": operator.and_}
_SHIFTS = {"asl", "asr", "lsl", "lsr"}
_MULTIPLY = {"mulu16", "muls16", "mulu32", "muls32", "mulu32l", "mulu32h", "muls32l", "muls32h"}
ALU_OPS = frozenset({"not", "cmp", "sub", "add", *_BITWISE, *_SHIFTS, *_MULTIPLY})


def register_write_delay_slots(op: str) -> int:
    """Extra Q1 issue slots before a new register value becomes readable."""
    return 0 if op == "loop" else 1


def operand_ranges(op: str) -> list[tuple[int, str, int, int]]:
    """Assembler ranges for immediate inputs, in source operand order."""
    if op in {"move", "not"}:
        return [(0, "classical_immediate", -_SIGN, _MASK)]
    if op in {"asl", "asr"}:
        return [(0, "signed_shift_immediate", -_SIGN, _SIGN - 1), (1, "unsigned_shift_immediate", 0, _MASK)]
    if op in {"lsr", "lsl"}:
        role, low, high = "unsigned_shift_immediate", 0, _MASK
    elif op in {"add", "sub", "cmp"} or op in _BITWISE:
        role, low, high = "classical_immediate", -_SIGN, _MASK
    elif op in _MULTIPLY:
        width = 16 if op.endswith("16") else 32
        if op.startswith("muls"):
            role = f"signed_{width}_immediate"
            low, high = -(1 << (width - 1)), (1 << (width - 1)) - 1
        else:
            role = f"unsigned_{width}_immediate"
            low, high = 0, (1 << width) - 1
    else:
        return []
    return [(0, role, low, high), (1, role, low, high)]


def _flags(word: int, carry: bool, overflow: bool) -> dict[str, bool]:
    word &= _MASK
    return {"zf": word == 0, "nf": bool(word & _SIGN), "cf": carry, "of": overflow}


def logic_flags(word: int) -> dict[str, bool]:
    return _flags(word, False, False)


def add_flags(left: int, right: int) -> dict[str, bool]:
    left &= _MASK
    right &= _MASK
    total = left + right
    return _flags(total, total > _MASK, bool(~(left ^ right) & (left ^ total) & _SIGN))


def sub_flags(left: int, right: int) -> dict[str, bool]:
    left &= _MASK
    right &= _MASK
    difference = left - right
    return _flags(difference, left < right, bool((left ^ right) & (left ^ difference) & _SIGN))


def shift_flags(op: str, left: int, shift: int, result: int) -> dict[str, bool]:
    shift &= _MASK
    if not 0 < shift <= 32:
        return logic_flags(result)
    if op in {"asl", "lsl"}:
        carry = bool(left & (1 << (32 - shift)))
        return _flags(result, carry, carry != bool(result & _SIGN))
    return _flags(result, bool(left & (1 << (shift - 1))), False)


def _signed(word: int, width: int) -> int:
    sign = 1 << (width - 1)
    return (word ^ sign) - sign


def _multiply(op: str, left: int, right: int) -> tuple[tuple[int, ...], dict[str, bool]]:
    width = 16 if op.endswith("16") else 32
    mask = (1 << width) - 1
    left &= mask
    right &= mask
    signed = op.startswith("muls")
    if signed:
        left, right = _signed(left, width), _signed(right, width)
    product = left * right
    low, high = product & _MASK, (product >> 32) & _MASK
    if op in {"mulu32", "muls32"}:
        # Concrete's reference convention compares the full product with the
        # 32-bit input range. Hardware parity for these flags is unverified.
        overflow = not (-_SIGN <= product < _SIGN if signed else 0 <= product <= _MASK)
        flags = {"zf": product == 0, "nf": bool(high & _SIGN), "cf": overflow, "of": overflow}
        return (low, high), flags
    word = high if op.endswith("h") else low
    return (word,), logic_flags(word)


def evaluate(instruction: Instr, inputs: tuple[int, ...]) -> tuple[tuple[int, ...], dict[str, bool]]:
    """Evaluate validated operands in source order, returning destination words and flags."""
    op = instruction.op
    if op == "not":
        word = ~inputs[0] & _MASK
        return (word,), logic_flags(word)
    left, right = inputs
    # For these instructions the register is the left operand of an
    # immediate/register form, including when the immediate is written first.
    if ((op in _SHIFTS or op in {"sub", "cmp"})
            and instruction.args[0].kind != "reg" and instruction.args[1].kind == "reg"):
        left, right = right, left
    if op == "add":
        return ((left + right) & _MASK,), add_flags(left, right)
    if op in {"sub", "cmp"}:
        words = ((left - right) & _MASK,) if op == "sub" else ()
        return words, sub_flags(left, right)
    if op in _BITWISE:
        word = _BITWISE[op](left, right) & _MASK
        return (() if op == "test" else (word,)), logic_flags(word)
    if op in _SHIFTS:
        if not 0 <= right <= 32:
            raise ValueError(f"Invalid shift count {right}")
        left &= _MASK
        if op in {"asl", "lsl"}:
            word = (left << right) & _MASK
        elif op == "asr":
            word = (_signed(left, 32) >> right) & _MASK
        else:
            word = left >> right
        return (word,), shift_flags(op, left, right, word)
    if op in _MULTIPLY:
        return _multiply(op, left, right)
    raise ValueError(f"Unsupported ALU instruction {op}")
