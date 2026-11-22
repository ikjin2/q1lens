"""32-bit Q1 classical ALU semantics on Python integers or Z3 bit-vectors."""

from __future__ import annotations

import operator
from typing import TYPE_CHECKING, Union

from q1timeline.q1asm.ast import Instr

if TYPE_CHECKING:
    from z3 import BitVecRef, BoolRef

Word = Union[int, "BitVecRef"]
Flag = Union[bool, "BoolRef"]


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


def _unsigned_lt(left: Word, right: Word) -> Flag:
    if isinstance(left, int) and isinstance(right, int):
        return left < right
    from z3 import ULT
    return ULT(left, right)


def flag_not(flag: Flag) -> Flag:
    if isinstance(flag, bool):
        return not flag
    from z3 import Not
    return Not(flag)


def flag_and(left: Flag, right: Flag) -> Flag:
    if isinstance(left, bool):
        return right if left else False
    if isinstance(right, bool):
        return left if right else False
    from z3 import And
    return And(left, right)


def flag_or(left: Flag, right: Flag) -> Flag:
    if isinstance(left, bool):
        return True if left else right
    if isinstance(right, bool):
        return True if right else left
    from z3 import Or
    return Or(left, right)


def _flags(word: Word, carry: Flag, overflow: Flag) -> dict[str, Flag]:
    word &= _MASK
    if not isinstance(word, int):
        from z3 import BoolVal
        carry = BoolVal(carry, ctx=word.ctx) if isinstance(carry, bool) else carry
        overflow = BoolVal(overflow, ctx=word.ctx) if isinstance(overflow, bool) else overflow
    return {"zf": word == 0, "nf": (word & _SIGN) != 0, "cf": carry, "of": overflow}


def logic_flags(word: Word) -> dict[str, Flag]:
    return _flags(word, False, False)


def add_flags(left: Word, right: Word) -> dict[str, Flag]:
    left &= _MASK
    right &= _MASK
    total = (left + right) & _MASK
    return _flags(total, _unsigned_lt(total, left), (~(left ^ right) & (left ^ total) & _SIGN) != 0)


def sub_flags(left: Word, right: Word) -> dict[str, Flag]:
    left &= _MASK
    right &= _MASK
    difference = left - right
    return _flags(difference, _unsigned_lt(left, right), ((left ^ right) & (left ^ difference) & _SIGN) != 0)


def shift_flags(op: str, left: Word, shift: Word, result: Word) -> dict[str, Flag]:
    shift &= _MASK
    active = flag_and(shift != 0, _unsigned_lt(shift, 33))
    if op in {"asl", "lsl"}:
        carry_bit = (left & (1 << ((32 - shift) & 31))) != 0
        overflow = carry_bit != ((result & _SIGN) != 0)
        return _flags(result, flag_and(active, carry_bit), flag_and(active, overflow))
    carry_bit = (left & (1 << ((shift - 1) & 31))) != 0
    return _flags(result, flag_and(active, carry_bit), False)


def _signed(word: int, width: int) -> int:
    sign = 1 << (width - 1)
    return (word ^ sign) - sign


def _multiply_operand(word: Word, width: int, signed: bool) -> Word:
    if isinstance(word, int):
        word &= (1 << width) - 1
        return _signed(word, width) if signed else word
    from z3 import Extract, SignExt, ZeroExt
    return (SignExt if signed else ZeroExt)(64 - width, Extract(width - 1, 0, word))


def _multiply(op: str, left: Word, right: Word) -> tuple[tuple[Word, ...], dict[str, Flag]]:
    width = 16 if op.endswith("16") else 32
    signed = op.startswith("muls")
    product = _multiply_operand(left, width, signed) * _multiply_operand(right, width, signed)
    if isinstance(product, int):
        low, high = product & _MASK, (product >> 32) & _MASK
    else:
        from z3 import Extract
        low, high = Extract(31, 0, product), Extract(63, 32, product)
    if op in {"mulu32", "muls32"}:
        # Concrete's reference convention compares the full product with the
        # 32-bit input range. Hardware parity for these flags is unverified.
        overflow = product != _multiply_operand(low, 32, signed)
        flags = {"zf": product == 0, "nf": (high & _SIGN) != 0, "cf": overflow, "of": overflow}
        return (low, high), flags
    word = high if op.endswith("h") else low
    return (word,), logic_flags(word)


def _register_is_second(instruction: Instr) -> bool:
    return instruction.args[0].kind != "reg" and instruction.args[1].kind == "reg"


def shift_count(instruction: Instr, inputs: tuple[Word, ...]) -> Word:
    """The immediate is the shift count even when it precedes the register."""
    return inputs[0] if _register_is_second(instruction) else inputs[1]


def evaluate(instruction: Instr, inputs: tuple[Word, ...]) -> tuple[tuple[Word, ...], dict[str, Flag]]:
    """Evaluate operands in source order. Symbolic callers must guard shift counts in 0..32."""
    op = instruction.op
    if op == "not":
        word = ~inputs[0] & _MASK
        return (word,), logic_flags(word)
    left, right = inputs
    # For these instructions the register is the left operand of an
    # immediate/register form, including when the immediate is written first.
    if ((op in _SHIFTS or op in {"sub", "cmp"})
            and _register_is_second(instruction)):
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
        if isinstance(right, int) and not 0 <= right <= 32:
            raise ValueError(f"Invalid shift count {right}")
        left &= _MASK
        if op in {"asl", "lsl"}:
            word = (left << right) & _MASK
        elif op == "asr":
            signed_left = _signed(left, 32) if isinstance(left, int) else left
            word = (signed_left >> right) & _MASK
        elif isinstance(left, int) and isinstance(right, int):
            word = left >> right
        else:
            from z3 import LShR
            word = LShR(left, right)
        return (word,), shift_flags(op, left, right, word)
    if op in _MULTIPLY:
        return _multiply(op, left, right)
    raise ValueError(f"Unsupported ALU instruction {op}")
