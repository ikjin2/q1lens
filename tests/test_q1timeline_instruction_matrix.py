"""Instruction/signature coverage and independent Q1 integer oracles.

Semantics source (read 2026-10-03):
https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html
The jge description says >= although its displayed action repeats jg's formula;
the test explicitly follows the description, not the contradictory action line.
This is the declared source-PC model, not assembled firmware address parity.
"""
from __future__ import annotations

import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.instruction_table import INSTRUCTION_TABLE
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import _SUPPORTED, simulate_programs


BRANCHES = ("jz", "jnz", "jo", "jno", "js", "jns", "jg", "jge", "jl", "jle", "ja", "jae", "jb", "jbe")
ALU = ("add", "sub", "and", "or", "xor", "asl", "asr", "lsl", "lsr", "mulu16", "muls16")

# Explicit operand examples, not generated from the production signature table.
# The table is used only as a coverage inventory guard, never as a numeric oracle.
SIGNATURE_EXAMPLES = {
    "nop": [""], "stop": ["", "1", "R1"], "move": ["1,R5", "R0,R5"], "not": ["1,R5", "R0,R5"],
    "jmp": ["@done", "9", "R4"],
    "loop": ["R1,@done", "R1,9", "R1,R4"],
    "jlt": ["R0,1,@done", "R0,1,9", "R0,1,R4"],
    "cmp": ["R0,1", "R0,R1", "1,R0"], "test": ["R0,1", "R0,R1", "1,R0"],
    "reset_ph": [""],
    "set_mrk": ["8", "R0"], "set_freq": ["8", "R0"], "set_ph": ["8", "R0"], "set_ph_delta": ["8", "R0"],
    "set_awg_gain": ["8,1", "R0,R1"], "set_awg_offs": ["8,1", "R0,R1"],
    "set_digital": ["1,1,0", "R1,1,R3"], "set_scope_en": ["1", "R1"],
    "set_cond": ["0,1,0,4", "R3,R1,R3,4"],
    "set_latch_en": ["1,4", "R1,4"], "latch_rst": ["4", "R2"],
    "wait": ["4", "R2"], "wait_sync": ["4", "R2"], "wait_trigger": ["1,4", "R1,R2"],
    "upd_param": ["4", "R2"], "play": ["0,1,4", "R3,R1,4"],
    "acquire": ["0,0,4", "0,R3,4"],
    "acquire_weighed": ["0,0,0,1,4", "0,R3,R3,R1,4"],
    "acquire_weighted": ["0,0,0,1,4", "0,R3,R3,R1,4"],
    "fb_pop_data": ["1,R5"], "fb_pull_data": ["R1,R5"],
    "fb_com_data": ["1,8,4", "1,R0,4"],
    "fb_acq_tb_id": ["1,4", "R1,4"], "fb_acq_tb_valid": ["1,4", "R1,4"],
    "fb_acq_iq_id": ["1,4", "R1,4"], "fb_acq_iq_shift": ["1,4", "R1,4"],
}
for _op in ALU:
    SIGNATURE_EXAMPLES[_op] = ["1,R0,R5", "R0,1,R5", "R0,R1,R5"]
for _op in BRANCHES:
    SIGNATURE_EXAMPLES[_op] = ["@done", "9", "R4"]
SIGNATURE_EXAMPLES["jge"] += ["R0,1,@done", "R0,1,9", "R0,1,R4"]


def execute(source, mode="simulation", **options):
    program = parse_q1asm(source, file="matrix.q1asm")
    if mode == "concrete":
        return interpret_program(program, sequencer_id="s", execution_mode=mode, max_steps=1000)
    return simulate_programs({"s": program}, max_steps=1000, **options)[0]


def status(state):
    return state.metadata["execution"]["status"]


def issue(state, op):
    return next(event for event in state.events if event.kind == "q1_issue" and event.meta["op"] == op)


def signed(word):
    return word if word < 2**31 else word - 2**32


def comparison_expectation(a, b):
    difference = signed(a) - signed(b)
    raw = (a-b) % 2**32
    overflow = not -(2**31) <= difference < 2**31
    return {
        "jz": a == b, "jnz": a != b, "jo": overflow, "jno": not overflow,
        "js": raw >= 2**31, "jns": raw < 2**31,
        "jg": signed(a) > signed(b), "jge": signed(a) >= signed(b),
        "jl": signed(a) < signed(b), "jle": signed(a) <= signed(b),
        "ja": a > b, "jae": a >= b, "jb": a < b, "jbe": a <= b,
    }


def test_every_runtime_supported_opcode_and_signature_has_an_explicit_case():
    assert set(SIGNATURE_EXAMPLES) == _SUPPORTED
    for op, examples in SIGNATURE_EXAMPLES.items():
        kinds = [tuple({"imm": "I", "reg": "R", "label": "L"}[arg.kind]
                       for arg in parse_q1asm(f"{op} {args}").instructions[0].args) for args in examples]
        for signature in INSTRUCTION_TABLE[op].signatures:
            assert any(len(actual) == len(signature.args) and all(kind in accepted for kind, accepted in zip(actual, signature.args))
                       for actual in kinds), (op, signature.args)


@pytest.mark.parametrize("op,args", [(op, args) for op, examples in SIGNATURE_EXAMPLES.items() for args in examples])
def test_supported_signature_executes_or_explicitly_blocks_compiled_address_form(op, args):
    # Subject at PC7, done at PC9. No label-value expression is needed in setup,
    # so legacy direct-label branches do not depend on compiled address support.
    source = f"move 8,R0\nmove 1,R1\nmove 4,R2\nmove 0,R3\nmove 9,R4\nnop\ncmp R0,1\n{op} {args}\nnop\ndone: stop\n"
    state = execute(source, simulation={"inputs": {
        "feedback": [{"target": "s", "time_ns": 0, "id": 1, "value": 42}],
        "triggers": [{"target": "s", "time_ns": 200, "address": 1}],
        "acquisitions": [{"sequencer": "s", "acquisition_index": 0, "bin_index": 0, "i": 2, "q": -1}],
    }})
    expanded = op in {"loop", "jlt"} or (op == "jge" and args.count(",") == 2)
    if expanded and "@done" not in args:
        assert status(state) == "blocked"
        assert "Compiled instruction-address mapping" in state.metadata["execution"]["reason"]
        return
    assert status(state) == "completed", state.metadata["execution"]
    assert any(event.kind == "q1_issue" and event.source.line == 8 for event in state.events)
    if op == "move":
        assert state.registers["R5"] == Concrete(1 if args.startswith("1,") else 8)
    if op == "not":
        assert state.registers["R5"] == Concrete(0xFFFFFFFE if args.startswith("1,") else 0xFFFFFFF7)
    if op in ALU:
        expected = {"add": 9, "sub": 7, "and": 0, "or": 9, "xor": 9,
                    "asl": 16, "lsl": 16, "asr": 4, "lsr": 4, "mulu16": 8, "muls16": 8}[op]
        assert state.registers["R5"] == Concrete(expected)
    if op in {"fb_pop_data", "fb_pull_data"}:
        assert state.registers["R5"] == Concrete(42)
    if op == "fb_com_data":
        assert next(event for event in state.events if event.kind == "feedback_send").meta["value"] == 8
    if op.startswith("acquire"):
        result = next(event for event in state.events if event.kind == "acquisition_result")
        assert (result.t0, result.meta["i"], result.meta["q"]) == (Concrete(204), 2, -1)


@pytest.mark.parametrize("mode", ["concrete", "simulation"])
@pytest.mark.parametrize("target", ["@taken", "9", "R4"])
@pytest.mark.parametrize("a,b", [(0, 0), (2, 1), (1, 2), (0x80000000, 1), (0x7FFFFFFF, 0xFFFFFFFF), (0xFFFFFFFF, 0)])
@pytest.mark.parametrize("op", BRANCHES)
def test_branch_truth_table_uses_signed_unsigned_comparisons_and_overflow(op, a, b, target, mode):
    expected = comparison_expectation(a, b)[op]
    state = execute(f"move {a},R0\nmove {b},R1\nmove 9,R4\nnop\ncmp R0,R1\n{op} {target}\nmove 0,R5\nnop\nstop\ntaken: move 1,R5\nnop\nstop", mode)
    assert status(state) == "completed", state.metadata["execution"]
    assert state.registers["R5"] == Concrete(int(expected))
    branch = issue(state, op)
    assert branch.meta["branch_taken"] is expected
    assert branch.duration == Concrete((24 if op == "jge" else 16) if expected else 4)


@pytest.mark.parametrize("mode", ["concrete", "simulation"])
@pytest.mark.parametrize("op", ["jge", "jlt"])
@pytest.mark.parametrize("word", [0xFFFFFC18, 0x80000000, 0x7FFFFFFF])
def test_legacy_comparison_register_is_a_full_unsigned_word_not_an_address(mode, op, word):
    state = execute(
        f"move {word},R0\nnop\n{op} R0,0x80000000,@taken\n"
        "move 0,R5\nnop\nstop\ntaken: move 1,R5\nnop\nstop", mode,
    )
    expected = word >= 0x80000000 if op == "jge" else word < 0x80000000
    assert status(state) == "completed", state.metadata["execution"]
    assert state.registers["R5"] == Concrete(int(expected))
    assert issue(state, op).meta["branch_taken"] is expected


@pytest.mark.parametrize("mode", ["concrete", "simulation"])
@pytest.mark.parametrize("form", ["I,R,R", "R,I,R", "R,R,R"])
@pytest.mark.parametrize("shift", [0, 1, 31, 32])
@pytest.mark.parametrize("op", ["asl", "lsl", "asr", "lsr"])
def test_shift_forms_preserve_signed32_or_zero_fill(op, shift, form, mode):
    word = 0x80000001
    args = {"I,R,R": f"{shift},R0,R5", "R,I,R": f"R0,{shift},R5", "R,R,R": "R0,R1,R5"}[form]
    if op in {"asl", "lsl"}:
        expected = (word * 2**shift) % 2**32
    else:
        expected = ((signed(word) if op == "asr" else word) // 2**shift) % 2**32
    state = execute(f"move {word},R0\nmove {shift},R1\nnop\n{op} {args}\nnop\nstop", mode)
    assert status(state) == "completed"
    assert state.registers["R5"] == Concrete(expected)
    if shift:
        # Last bit shifted out, read independently from the 32-character word.
        bits = f"{word:032b}"
        carry = bits[shift-1] == "1" if op in {"asl", "lsl"} else bits[-shift] == "1"
        flags = issue(state, op).meta["flags_after"]
        assert flags["cf"] is carry
        assert flags["zf"] is (expected == 0)
        assert flags["nf"] is (expected >= 2**31)


@pytest.mark.parametrize("mode", ["concrete", "simulation"])
@pytest.mark.parametrize("form", ["I,R,R", "R,I,R", "R,R,R"])
@pytest.mark.parametrize("op,expected", [("add", 0x80000002), ("sub", 0x80000000), ("and", 1), ("or", 0x80000001), ("xor", 0x80000000)])
def test_alu_forms_use_documented_immediate_order_and_uint32_words(op, expected, form, mode):
    args = {"I,R,R": "1,R0,R5", "R,I,R": "R0,1,R5", "R,R,R": "R0,R1,R5"}[form]
    state = execute(f"move 0x80000001,R0\nmove 1,R1\nnop\n{op} {args}\nnop\nstop", mode)
    assert status(state) == "completed"
    assert state.registers["R5"] == Concrete(expected)


@pytest.mark.parametrize("mode", ["concrete", "simulation"])
@pytest.mark.parametrize("args", ["1,R0", "R0,1", "R0,R1"])
@pytest.mark.parametrize("op,expected", [
    ("cmp", {"zf": False, "nf": False, "cf": False, "of": True}),
    ("test", {"zf": True, "nf": False, "cf": False, "of": False}),
])
def test_compare_and_test_operand_forms_set_flags_without_writing_sources(op, expected, args, mode):
    # Signed -2^31 minus one overflows to +2^31-1; bitwise AND with one is zero.
    state = execute(f"move 0x80000000,R0\nmove 1,R1\nnop\n{op} {args}\nstop", mode)
    assert status(state) == "completed"
    assert state.alu_flags == expected
    assert state.registers == {"R0": Concrete(0x80000000), "R1": Concrete(1)}


@pytest.mark.parametrize("op,source,operand,expected", [("muls16", 0xFFFF8000, -1, 32768), ("mulu16", 0x10001, 65535, 65535)])
@pytest.mark.parametrize("form", ["I,R,R", "R,I,R", "R,R,R"])
def test_multiply_forms_use_only_low_16_bits_with_declared_signedness(op, source, operand, expected, form):
    args = {"I,R,R": f"{operand},R0,R5", "R,I,R": f"R0,{operand},R5", "R,R,R": "R0,R1,R5"}[form]
    state = execute(f"move {source},R0\nmove {operand},R1\nnop\n{op} {args}\nnop\nstop")
    assert status(state) == "completed"
    assert state.registers["R5"] == Concrete(expected)


@pytest.mark.parametrize("op", ["acquire_weighed", "acquire_weighted"])
@pytest.mark.parametrize("register_operands", [False, True])
def test_weighted_aliases_and_operand_forms_share_independent_integral(op, register_operands):
    args = "0,R0,R1,R2,20" if register_operands else "0,1,0,1,20"
    source = f"move 1,R0\nmove 0,R1\nmove 1,R2\nnop\n{op} {args}\nstop"
    state = execute(source, sequences={"s": {
        "weights": {"i": {"index": 0, "data": [1, -1, 1, -1]}, "q": {"index": 1, "data": [1, 1, 1, 1]}},
        "acquisitions": {"result": {"index": 0, "num_bins": 2}},
    }}, simulation={"physical": {"sequencers": {"s": {"input": {"kind": "constant", "i": 0.5, "q": 0.25}}}}})
    assert status(state) == "completed"
    result = next(event for event in state.events if event.kind == "acquisition_result")
    assert (result.t0, result.meta["i"], result.meta["q"], result.meta["bin_index"]) == (Concrete(204), 0, 1, 1)
    assert result.meta["weights"] == [0, 1]
    assert state.rt_time_ns == Concrete(220)
    assert next(event for event in state.events if event.kind == "acquire").meta["op"] == op


@pytest.mark.parametrize("mode", ["concrete", "simulation"])
@pytest.mark.parametrize("op", BRANCHES)
def test_status_branch_requires_flags_instead_of_assuming_false(op, mode):
    state = execute(f"{op} @done\ndone: stop", mode)
    assert status(state) == "blocked"
    assert state.metadata["execution"]["steps"] == 0
    assert "flags are unknown" in state.metadata["execution"]["reason"]


@pytest.mark.parametrize("mode", ["concrete", "simulation"])
@pytest.mark.parametrize("op", ["asl", "asr", "lsl", "lsr"])
@pytest.mark.parametrize("shift", [-1, 33])
def test_unsupported_shift_counts_never_silently_mask_to_five_bits(op, shift, mode):
    state = execute(f"move 1,R0\nmove {shift},R1\nnop\n{op} R0,R1,R5\nstop", mode)
    assert status(state) == "blocked"
    assert "Shift count" in state.metadata["execution"]["reason"]
    assert "R5" not in state.registers


@pytest.mark.parametrize("instruction,expected", [
    ("reset_netzero", "blocked"), ("set_time_ref", "blocked"), ("sw_req 1", "blocked"),
    ("mulu32 R0,R1,R2,R3", "blocked"), ("acquire_ttl 0,0,1,4", "blocked"),
    ("illegal", "error"), ("unknown_matrix_instruction", "error"), ("add R0,R1", "error"),
])
def test_recognized_but_unimplemented_and_malformed_instructions_are_not_success(instruction, expected):
    state = execute(f"move 1,R0\nmove 1,R1\nnop\n{instruction}\nstop")
    assert status(state) == expected
