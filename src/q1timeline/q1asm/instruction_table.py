from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from q1timeline.diagnostics import Diagnostic
from q1timeline.q1asm.ast import Arg, Instr


InstructionCategory = Literal[
    "rt",
    "latched",
    "classical",
    "branch",
    "feedback",
    "sync",
    "control",
    "unknown",
]
ArgType = Literal["I", "R", "L"]
ArgPattern = tuple[frozenset[ArgType], ...]
Q1TimeModel = Callable[[Instr], int]
Validator = Callable[[Instr], list[Diagnostic]]
TimingEffectStatus = Literal["known", "analysis_incomplete"]

STATUS_BRANCH_OPS = frozenset(
    {"jz", "jnz", "jo", "jno", "js", "jns", "jg", "jl", "jle", "ja", "jae", "jb", "jbe"}
)


@dataclass(frozen=True)
class InstructionSignature:
    args: ArgPattern
    q1_time_ns: int = 4


@dataclass(frozen=True)
class InstructionSpec:
    op: str
    category: InstructionCategory
    emits_rt_packet: bool
    advances_rt_time: bool
    rt_duration_arg: int | None
    rt_duration_arg_by_count: dict[int, int] | None
    modifies_latched_state: bool
    applies_latched_state: bool
    timing_effect_status: TimingEffectStatus
    q1_time_model: Q1TimeModel
    validate: Validator
    signatures: tuple[InstructionSignature, ...]


def get_instruction_spec(op: str) -> InstructionSpec:
    normalized = op.lower()
    return INSTRUCTION_TABLE.get(normalized, _unknown_spec(normalized))


def rt_duration_arg(op: str) -> int | None:
    return get_instruction_spec(op).rt_duration_arg


def rt_duration_arg_for_instruction(spec: InstructionSpec, instruction: Instr) -> int | None:
    if spec.rt_duration_arg_by_count is not None:
        indexed = spec.rt_duration_arg_by_count.get(len(instruction.args))
        if indexed is not None:
            return indexed
    return spec.rt_duration_arg


def diagnose_unknown_instruction(op: str) -> Diagnostic:
    return Diagnostic(
        severity="error",
        category="unknown_instruction",
        message=f"Unknown Q1ASM instruction: {op}",
        details={"op": op},
    )


def _make_validator(op: str, signatures: tuple[InstructionSignature, ...]) -> Validator:
    expected_counts = sorted({len(signature.args) for signature in signatures})

    def validate(instruction: Instr) -> list[Diagnostic]:
        actual = len(instruction.args)
        candidates = [signature for signature in signatures if len(signature.args) == actual]
        if not candidates:
            return [
                Diagnostic(
                    severity="error",
                    category="invalid_argument_count",
                    message=f"{op} expects {_count_display(expected_counts)}, got {actual}.",
                    source=instruction.source,
                    details={
                        "op": op,
                        "expected_counts": expected_counts,
                        "actual": actual,
                    },
                )
            ]

        if any(_signature_matches(signature, instruction.args) for signature in candidates):
            branch_target_diagnostic = _unsupported_branch_target_diagnostic(op, instruction, candidates)
            if branch_target_diagnostic is not None:
                return [branch_target_diagnostic]
            return []

        return [
            Diagnostic(
                severity="error",
                category="invalid_argument_type",
                message=f"{op} arguments do not match any supported signature: {_signature_list_display(candidates)}.",
                source=instruction.source,
                details={
                    "op": op,
                    "expected_signatures": [_signature_display(signature) for signature in candidates],
                    "actual": [arg.raw for arg in instruction.args],
                },
            )
        ]

    return validate


def _unsupported_branch_target_diagnostic(
    op: str,
    instruction: Instr,
    candidates: list[InstructionSignature],
) -> Diagnostic | None:
    target_index = {"jmp": 0, "jge": 0 if len(instruction.args) == 1 else 2, "jlt": 2, "loop": 1, **{branch_op: 0 for branch_op in STATUS_BRANCH_OPS}}.get(op)
    if target_index is None or len(instruction.args) <= target_index:
        return None
    target = instruction.args[target_index]
    if target.kind in {"label", "reg"}:
        return None
    if op in {"jmp", "jge", "jlt", "loop", *STATUS_BRANCH_OPS} and target.kind == "imm":
        return None
    if target.kind == "symbol" and str(target.value).startswith("$"):
        return None
    return Diagnostic(
        severity="error",
        category="invalid_argument_type",
        message=(
            f"{op} branch target must use @label syntax or a register target; "
            f"got {target.raw}."
        ),
        source=instruction.source,
        details={
            "op": op,
            "expected_signatures": [_signature_display(signature) for signature in candidates],
            "actual": [arg.raw for arg in instruction.args],
            "argument_index": target_index,
            "target": target.raw,
        },
    )


def _make_q1_time_model(signatures: tuple[InstructionSignature, ...]) -> Q1TimeModel:
    default = signatures[0].q1_time_ns if signatures else 4

    def q1_time(instruction: Instr) -> int:
        matches = [
            signature
            for signature in signatures
            if len(signature.args) == len(instruction.args) and _signature_matches(signature, instruction.args)
        ]
        if not matches:
            return default
        return max(signature.q1_time_ns for signature in matches)

    return q1_time


def _spec(
    op: str,
    category: InstructionCategory,
    signatures: Iterable[InstructionSignature],
    *,
    emits_rt_packet: bool = False,
    advances_rt_time: bool = False,
    rt_duration_arg: int | None = None,
    rt_duration_arg_by_count: dict[int, int] | None = None,
    modifies_latched_state: bool = False,
    applies_latched_state: bool = False,
    timing_effect_status: TimingEffectStatus = "known",
) -> InstructionSpec:
    signature_tuple = tuple(signatures)
    return InstructionSpec(
        op=op,
        category=category,
        emits_rt_packet=emits_rt_packet,
        advances_rt_time=advances_rt_time,
        rt_duration_arg=rt_duration_arg,
        rt_duration_arg_by_count=rt_duration_arg_by_count,
        modifies_latched_state=modifies_latched_state,
        applies_latched_state=applies_latched_state,
        timing_effect_status=timing_effect_status,
        q1_time_model=_make_q1_time_model(signature_tuple),
        validate=_make_validator(op, signature_tuple),
        signatures=signature_tuple,
    )


def _unknown_spec(op: str) -> InstructionSpec:
    def validate(instruction: Instr) -> list[Diagnostic]:
        diagnostic = diagnose_unknown_instruction(op)
        return [
            Diagnostic(
                severity=diagnostic.severity,
                category=diagnostic.category,
                message=diagnostic.message,
                source=instruction.source,
                details=diagnostic.details,
            )
        ]

    return InstructionSpec(
        op=op,
        category="unknown",
        emits_rt_packet=False,
        advances_rt_time=False,
        rt_duration_arg=None,
        rt_duration_arg_by_count=None,
        modifies_latched_state=False,
        applies_latched_state=False,
        timing_effect_status="analysis_incomplete",
        q1_time_model=lambda _instruction: 4,
        validate=validate,
        signatures=(_sig(""),),
    )


def _sig(pattern: str, q1_time_ns: int = 4) -> InstructionSignature:
    if not pattern:
        return InstructionSignature(args=(), q1_time_ns=q1_time_ns)
    return InstructionSignature(
        args=tuple(frozenset(part.strip().split("/")) for part in pattern.split(",")),
        q1_time_ns=q1_time_ns,
    )


def _signature_matches(signature: InstructionSignature, args: list[Arg]) -> bool:
    return all(_arg_matches(accepted, arg) for accepted, arg in zip(signature.args, args, strict=True))


def _arg_matches(accepted: frozenset[ArgType], arg: Arg) -> bool:
    if arg.kind == "imm":
        return "I" in accepted
    if arg.kind == "label":
        return "L" in accepted or "I" in accepted
    if arg.kind == "reg":
        return "R" in accepted
    if arg.kind in {"symbol", "placeholder"}:
        return True
    return False


def _count_display(counts: list[int]) -> str:
    if len(counts) == 1:
        return f"{counts[0]} argument(s)"
    return " or ".join(f"{count} argument(s)" for count in counts)


def _signature_list_display(signatures: list[InstructionSignature]) -> str:
    return "; ".join(_signature_display(signature) for signature in signatures)


def _signature_display(signature: InstructionSignature) -> str:
    if not signature.args:
        return "()"
    return "(" + ", ".join("/".join(sorted(arg_types)) for arg_types in signature.args) + ")"


INSTRUCTION_TABLE: dict[str, InstructionSpec] = {
    "illegal": _spec("illegal", "control", [_sig("")]),
    "stop": _spec("stop", "control", [_sig(""), _sig("I"), _sig("R")]),
    "nop": _spec("nop", "classical", [_sig("")]),
    "jmp": _spec("jmp", "branch", [_sig("I/L", 16), _sig("R", 16)]),
    "jge": _spec("jge", "branch", [_sig("R,I,I/L", 24), _sig("R,I,R", 24), _sig("I/L", 24), _sig("R", 24)]),
    "jlt": _spec("jlt", "branch", [_sig("R,I,I/L", 24), _sig("R,I,R", 24)]),
    "loop": _spec("loop", "branch", [_sig("R,I/L", 24), _sig("R,R", 24)]),
    "move": _spec("move", "classical", [_sig("I,R"), _sig("R,R")]),
    "not": _spec("not", "classical", [_sig("I,R", 12), _sig("R,R", 12)]),
    "add": _spec("add", "classical", [_sig("R,I,R", 12), _sig("R,R,R", 16)]),
    "sub": _spec("sub", "classical", [_sig("R,I,R", 12), _sig("R,R,R", 16)]),
    "and": _spec("and", "classical", [_sig("R,I,R", 12), _sig("R,R,R", 16)]),
    "or": _spec("or", "classical", [_sig("R,I,R", 12), _sig("R,R,R", 16)]),
    "xor": _spec("xor", "classical", [_sig("R,I,R", 12), _sig("R,R,R", 16)]),
    "asl": _spec("asl", "classical", [_sig("R,I,R", 12), _sig("R,R,R", 16)]),
    "asr": _spec("asr", "classical", [_sig("R,I,R", 12), _sig("R,R,R", 16)]),
    "cmp": _spec("cmp", "classical", [_sig("R,I", 12), _sig("R,R", 16), _sig("I,R", 12)]),
    "set_cond": _spec(
        "set_cond",
        "latched",
        [_sig("I,I,I,I"), _sig("R,R,R,I")],
        modifies_latched_state=True,
        timing_effect_status="analysis_incomplete",
    ),
    "set_mrk": _spec("set_mrk", "latched", [_sig("I"), _sig("R")], modifies_latched_state=True),
    "set_freq": _spec("set_freq", "latched", [_sig("I"), _sig("R")], modifies_latched_state=True),
    "reset_ph": _spec("reset_ph", "latched", [_sig("")], modifies_latched_state=True),
    "reset_netzero": _spec("reset_netzero", "latched", [_sig("")], modifies_latched_state=True),
    "set_ph": _spec("set_ph", "latched", [_sig("I"), _sig("R")], modifies_latched_state=True),
    "set_ph_delta": _spec("set_ph_delta", "latched", [_sig("I"), _sig("R")], modifies_latched_state=True),
    "set_awg_gain": _spec(
        "set_awg_gain",
        "latched",
        [_sig("I,I"), _sig("R,R")],
        modifies_latched_state=True,
    ),
    "set_awg_offs": _spec(
        "set_awg_offs",
        "latched",
        [_sig("I,I"), _sig("R,R")],
        modifies_latched_state=True,
    ),
    "set_digital": _spec(
        "set_digital",
        "latched",
        [_sig("I,I,I"), _sig("R,I,R")],
        modifies_latched_state=True,
    ),
    "set_time_ref": _spec("set_time_ref", "latched", [_sig("")], modifies_latched_state=True),
    "set_scope_en": _spec("set_scope_en", "latched", [_sig("I"), _sig("R")], modifies_latched_state=True),
    "set_latch_en": _spec(
        "set_latch_en",
        "rt",
        [_sig("I,I"), _sig("R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=1,
    ),
    "latch_rst": _spec(
        "latch_rst",
        "rt",
        [_sig("I"), _sig("R")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=0,
    ),
    "wait": _spec(
        "wait",
        "rt",
        [_sig("I"), _sig("R")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=0,
    ),
    "wait_sync": _spec(
        "wait_sync",
        "sync",
        [_sig("I"), _sig("R")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=0,
    ),
    "wait_trigger": _spec(
        "wait_trigger",
        "sync",
        [_sig("I,I"), _sig("R,R")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=1,
    ),
    "upd_param": _spec(
        "upd_param",
        "rt",
        [_sig("I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=0,
        applies_latched_state=True,
    ),
    "play": _spec(
        "play",
        "rt",
        [_sig("I,I,I"), _sig("R,R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=2,
        applies_latched_state=True,
    ),
    "acquire": _spec(
        "acquire",
        "rt",
        [_sig("I,I,I"), _sig("I,R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=2,
        applies_latched_state=True,
    ),
    "acquire_weighed": _spec(
        "acquire_weighed",
        "rt",
        [_sig("I,I,I,I,I"), _sig("I,R,R,R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=4,
        applies_latched_state=True,
    ),
    "acquire_ttl": _spec(
        "acquire_ttl",
        "rt",
        [_sig("I,I,I,I"), _sig("I,R,I,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=3,
        applies_latched_state=True,
    ),
    "acquire_timetags": _spec(
        "acquire_timetags",
        "rt",
        [_sig("I,I,I,I,I", 8), _sig("I,R,I,R,I", 8)],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=4,
        applies_latched_state=True,
    ),
    "acquire_digital": _spec(
        "acquire_digital",
        "rt",
        [_sig("I,I,I", 8), _sig("I,R,I", 8)],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=2,
        applies_latched_state=True,
    ),
    "upd_thres": _spec(
        "upd_thres",
        "rt",
        [_sig("I,I,I", 8), _sig("I,R,I", 8)],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=2,
        applies_latched_state=True,
    ),
    "fb_pop_data": _spec("fb_pop_data", "feedback", [_sig("I,R")]),
    "fb_pull_data": _spec("fb_pull_data", "feedback", [_sig("R,R", 8)]),
    "fb_com_data": _spec(
        "fb_com_data",
        "rt",
        [_sig("I,I,I"), _sig("I,R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=2,
    ),
    "fb_com_cfg": _spec(
        "fb_com_cfg",
        "rt",
        [_sig("I,I,I,I"), _sig("R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=3,
        rt_duration_arg_by_count={2: 1, 4: 3},
    ),
    "fb_com_extra": _spec(
        "fb_com_extra",
        "rt",
        [_sig("I,I,I"), _sig("R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=2,
        rt_duration_arg_by_count={2: 1, 3: 2},
    ),
    "fb_acq_tb_id": _spec(
        "fb_acq_tb_id",
        "rt",
        [_sig("I,I"), _sig("R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=1,
    ),
    "fb_acq_tb_cfg": _spec(
        "fb_acq_tb_cfg",
        "rt",
        [_sig("I,I,I,I"), _sig("R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=3,
        rt_duration_arg_by_count={2: 1, 4: 3},
    ),
    "fb_acq_tb_valid": _spec(
        "fb_acq_tb_valid",
        "rt",
        [_sig("I,I"), _sig("R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=1,
    ),
    "fb_acq_tb_extra": _spec(
        "fb_acq_tb_extra",
        "rt",
        [_sig("I,I,I"), _sig("R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=2,
        rt_duration_arg_by_count={2: 1, 3: 2},
    ),
    "fb_acq_tb_mock": _spec(
        "fb_acq_tb_mock",
        "rt",
        [_sig("I,I,I,I"), _sig("R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=3,
        rt_duration_arg_by_count={2: 1, 4: 3},
    ),
    "fb_acq_iq_id": _spec(
        "fb_acq_iq_id",
        "rt",
        [_sig("I,I"), _sig("R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=1,
    ),
    "fb_acq_iq_shift": _spec(
        "fb_acq_iq_shift",
        "rt",
        [_sig("I,I"), _sig("R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=1,
    ),
    "fb_llp_tags_id": _spec(
        "fb_llp_tags_id",
        "rt",
        [_sig("I,I"), _sig("R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=1,
    ),
    "fb_llp_ttls_id": _spec(
        "fb_llp_ttls_id",
        "rt",
        [_sig("I,I"), _sig("R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=1,
    ),
    "fb_tdc_tags_id": _spec(
        "fb_tdc_tags_id",
        "rt",
        [_sig("I,I"), _sig("R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=1,
    ),
    "fb_tdc_tdelta_id": _spec(
        "fb_tdc_tdelta_id",
        "rt",
        [_sig("I,I"), _sig("R,I")],
        emits_rt_packet=True,
        advances_rt_time=True,
        rt_duration_arg=1,
    ),
}

INSTRUCTION_TABLE.update(
    {
        branch_op: _spec(
            branch_op,
            "branch",
            [_sig("I/L", 16), _sig("R", 16)],
        )
        for branch_op in STATUS_BRANCH_OPS
    }
)

INSTRUCTION_TABLE.update(
    {
        "add": _spec("add", "classical", [_sig("I,R,R", 12), _sig("R,I,R", 12), _sig("R,R,R", 12)]),
        "sub": _spec("sub", "classical", [_sig("I,R,R", 12), _sig("R,I,R", 12), _sig("R,R,R", 12)]),
        "and": _spec("and", "classical", [_sig("I,R,R", 12), _sig("R,I,R", 12), _sig("R,R,R", 12)]),
        "or": _spec("or", "classical", [_sig("I,R,R", 12), _sig("R,I,R", 12), _sig("R,R,R", 12)]),
        "xor": _spec("xor", "classical", [_sig("I,R,R", 12), _sig("R,I,R", 12), _sig("R,R,R", 12)]),
        "asl": _spec("asl", "classical", [_sig("I,R,R", 12), _sig("R,I,R", 12), _sig("R,R,R", 12)]),
        "asr": _spec("asr", "classical", [_sig("I,R,R", 12), _sig("R,I,R", 12), _sig("R,R,R", 12)]),
        "lsl": _spec("lsl", "classical", [_sig("I,R,R", 12), _sig("R,I,R", 12), _sig("R,R,R", 12)]),
        "lsr": _spec("lsr", "classical", [_sig("I,R,R", 12), _sig("R,I,R", 12), _sig("R,R,R", 12)]),
        "mulu16": _spec("mulu16", "classical", [_sig("I,R,R", 12), _sig("R,I,R", 12), _sig("R,R,R", 12)]),
        "muls16": _spec("muls16", "classical", [_sig("I,R,R", 12), _sig("R,I,R", 12), _sig("R,R,R", 12)]),
        "mulu32": _spec("mulu32", "classical", [_sig("R,R,R,R", 16)]),
        "muls32": _spec("muls32", "classical", [_sig("I,R,R,R", 24), _sig("R,I,R,R", 24), _sig("R,R,R,R", 24)]),
        "mulu32l": _spec("mulu32l", "classical", [_sig("I,R,R", 20), _sig("R,I,R", 20), _sig("R,R,R", 20)]),
        "mulu32h": _spec("mulu32h", "classical", [_sig("I,R,R", 20), _sig("R,I,R", 20), _sig("R,R,R", 20)]),
        "muls32l": _spec("muls32l", "classical", [_sig("I,R,R", 20), _sig("R,I,R", 20), _sig("R,R,R", 20)]),
        "muls32h": _spec("muls32h", "classical", [_sig("I,R,R", 20), _sig("R,I,R", 20), _sig("R,R,R", 20)]),
        "cmp": _spec("cmp", "classical", [_sig("R,I", 12), _sig("R,R", 12), _sig("I,R", 12)]),
        "test": _spec("test", "classical", [_sig("R,I", 12), _sig("R,R", 12), _sig("I,R", 12)]),
        "sw_req": _spec("sw_req", "control", [_sig("I"), _sig("R")]),
        "set_digital": _spec(
            "set_digital",
            "latched",
            [_sig("I,I,I"), _sig("R,I,R")],
            modifies_latched_state=True,
        ),
        "upd_param": _spec(
            "upd_param",
            "rt",
            [_sig("I"), _sig("R")],
            emits_rt_packet=True,
            advances_rt_time=True,
            rt_duration_arg=0,
            applies_latched_state=True,
        ),
        "acquire_weighted": _spec(
            "acquire_weighted",
            "rt",
            [_sig("I,I,I,I,I"), _sig("I,R,R,R,I")],
            emits_rt_packet=True,
            advances_rt_time=True,
            rt_duration_arg=4,
            applies_latched_state=True,
        ),
        "play_pulse": _spec(
            "play_pulse",
            "rt",
            [_sig("I,I"), _sig("R,I")],
            emits_rt_packet=True,
            advances_rt_time=True,
            rt_duration_arg=1,
            applies_latched_state=True,
        ),
        "fb_cmd": _spec(
            "fb_cmd",
            "rt",
            [_sig("I,I,I"), _sig("I,R,I")],
            emits_rt_packet=True,
            advances_rt_time=True,
            rt_duration_arg=2,
        ),
    }
)

SUPPORTED_OPS = frozenset(INSTRUCTION_TABLE)
