"""Concrete Q1 latch writes and analytical, single-sequencer RT packet effects."""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, replace

from q1timeline.q1asm.ast import Instr
from q1timeline.q1asm.core_devices import CoreBlocked, DEVICE_SETTERS


LATCH_FIELDS = {
    "set_mrk": "marker", "set_awg_gain": "awg_gain", "set_awg_offs": "awg_offset",
    "set_freq": "frequency", "set_ph": "phase", "set_ph_delta": "phase_delta",
    "reset_ph": "phase_reset", "set_digital": "digital", "set_scope_en": "scope_enable",
}
LATCH_OPS = frozenset(LATCH_FIELDS)
RT_OPS = frozenset({"wait", "wait_sync", "upd_param", "play", "acquire", "acquire_weighed", "acquire_weighted"})
ONE_SHOTS = ("phase_reset", "phase_delta", *DEVICE_SETTERS.values())

# Index and role are also used in source and runtime diagnostics.
_ACQUIRE = ((0, "acquisition_index", 0, 31), (1, "bin_index", 0, 16777215))
_WEIGHTED = (*_ACQUIRE, (2, "weight_index", 0, 63), (3, "weight_index", 0, 63))
OPERAND_RANGES = {
    "set_mrk": ((0, "marker", 0, 15),),
    "set_awg_gain": ((0, "awg_gain", -32768, 32767), (1, "awg_gain", -32768, 32767)),
    "set_awg_offs": ((0, "awg_offset", -32768, 32767), (1, "awg_offset", -32768, 32767)),
    "set_freq": ((0, "frequency", -(1 << 31), (1 << 31) - 1),),
    "set_ph": ((0, "phase", 0, 1000000000),),
    "set_ph_delta": ((0, "phase_delta", 0, 1000000000),),
    "set_digital": ((0, "digital_value", 0, 255), (1, "digital_mask", 0, 255), (2, "digital_delay", 0, 2047)),
    "set_scope_en": ((0, "scope_enable", 0, 1),),
    "set_cond": ((0, "condition_enable", 0, 1), (1, "condition_mask", 0, 32767),
                 (2, "condition_operator", 0, 7), (3, "else_duration", 0, 65535)),
    "play": ((0, "waveform_index", 0, 1023), (1, "waveform_index", 0, 1023)),
    "acquire": _ACQUIRE, "acquire_weighed": _WEIGHTED, "acquire_weighted": _WEIGHTED,
}


@dataclass(frozen=True)
class LatchWrite:
    field: str
    value: int | bool | tuple[int, ...]


def latch_commit_fields(pending: Collection[str], phase_reset: bool, *,
                        skipped: bool = False) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the fields applied to this RT packet and those still awaiting a commit."""
    if skipped:
        return (), tuple(name for name in pending if name not in ONE_SHOTS)
    fields = (*LATCH_FIELDS.values(), *DEVICE_SETTERS.values())
    return tuple(name for name in fields if (phase_reset if name == "phase_reset" else name in pending)), ()


@dataclass(frozen=True)
class Latches:
    """Last applied settings and writes waiting for a latch-updating packet."""

    active: tuple[LatchWrite, ...] = ()
    pending: tuple[LatchWrite, ...] = ()

    def write(self, update: LatchWrite) -> Latches:
        writes = {value.field: value for value in self.pending}
        writes[update.field] = update
        return replace(self, pending=tuple(writes.values()))

    def commit(self, *, skipped: bool = False) -> tuple[Latches, tuple[LatchWrite, ...]]:
        pending = {value.field: value for value in self.pending}
        fields, retained = latch_commit_fields(pending, "phase_reset" in pending, skipped=skipped)
        if skipped:
            return replace(self, pending=tuple(pending[name] for name in retained)), ()
        applied = tuple(pending[name] for name in fields)
        active = {value.field: value for value in self.active}
        if "phase_reset" in pending:
            # Reset the phase offset before applying any explicit offset in this packet.
            active["phase"] = LatchWrite("phase", 0)
        active.update((value.field, value) for value in applied if value.field not in ONE_SHOTS)
        return Latches(active=tuple(active.values())), applied


def decode_register(op: str, word: int) -> int:
    if op == "set_mrk":
        return word & 0xF
    if op in {"set_awg_gain", "set_awg_offs", "set_freq"}:
        return ((word & 0xFFFFFFFF) ^ 0x80000000) - 0x80000000
    return word


def _check_ranges(item: Instr, values: tuple[int | None, ...], *, include_registers: bool = False) -> None:
    for i, role, low, high in OPERAND_RANGES.get(item.op, ()):
        if item.args[i].kind == "reg" and not include_registers:
            continue
        value = values[i]
        if item.op == "set_cond" and values[0] == 0 and i in (1, 2) and value is None:
            continue
        if value is None or not low <= value <= high:
            raise ValueError(f"{item.op} {role} must be in range {low}..{high}, got {value}")


def latch_write(item: Instr, operands: tuple[int, ...]) -> LatchWrite:
    values = tuple(decode_register(item.op, word) if arg.kind == "reg" else word
                   for arg, word in zip(item.args, operands, strict=True))
    _check_ranges(item, values, include_registers=True)
    if item.op == "reset_ph":
        return LatchWrite("phase_reset", True)
    return LatchWrite(LATCH_FIELDS[item.op], values[0] if len(values) == 1 else values)


def _duration(op: str, value: int | None) -> int:
    if value is None or value != 0 and not 4 <= value <= 65535:
        raise ValueError(f"Invalid {op} duration {value}")
    return value


def rt_duration(item: Instr, operands: tuple[int, ...]) -> int:
    _check_ranges(item, operands)
    # The last operand is the duration on every supported RT instruction.
    return _duration(item.op, operands[-1])


def validate_condition(item: Instr, operands: tuple[int | None, ...]) -> None:
    _check_ranges(item, operands)
    enable, mask, operator, duration = operands
    if enable not in (0, 1):
        raise ValueError("set_cond enable must be 0 or 1")
    if enable:
        if mask is None or not 0 <= mask <= 32767 or operator is None or not 0 <= operator <= 7:
            raise ValueError("Enabled set_cond requires a 15-bit mask and a three-bit operator")
        if operator > 5:
            raise CoreBlocked("Conditional operators 6 and 7 have no supported semantics")
    _duration(item.op, duration)
