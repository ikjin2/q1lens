import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.underflow import analyze_underflow
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm


def analyze(source: str, **kwargs):
    return interpret_program(parse_q1asm(source, file="multiply.q1asm"), sequencer_id="seq0", **kwargs)


@pytest.mark.parametrize("op,left,right,expected", [
    ("mulu16", 123, 45, 5535),
    ("mulu16", 0x10001, 65535, 65535),
    ("mulu16", 0xFFFFFFFF, 65535, 65535 * 65535),
    ("muls16", 0xFFFF8000, -1, 32768),
    ("muls16", 65535, 2, 0xFFFFFFFE),
    ("muls16", 32768, -32768, 0x40000000),
    ("mulu32l", 0xFFFFFFFF, 2, 0xFFFFFFFE),
    ("mulu32h", 0xFFFFFFFF, 2, 1),
    ("muls32l", 0xFFFFFFFF, -2, 2),
    ("muls32h", 0xFFFFFFFF, 2, 0xFFFFFFFF),
    ("muls32h", 0x80000000, -1, 0),
])
def test_known_multiplication_matches_documented_operand_width(op, left, right, expected):
    source = f"move {left},R0\nnop\n{op} R0,{right},R1\nnop\nstop\n"
    analysis = analyze(source)
    concrete = analyze(source, execution_mode="concrete")

    assert analysis.registers["R1"] == concrete.registers["R1"] == Concrete(expected)
    assert analysis.alu_flags == concrete.alu_flags == {
        "zf": expected == 0, "nf": bool(expected & 0x80000000), "cf": False, "of": False,
    }
    assert concrete.metadata["execution"]["status"] == "completed"


@pytest.mark.parametrize("op", ["mulu16", "muls16", "mulu32l", "mulu32h", "muls32l", "muls32h"])
@pytest.mark.parametrize("operands", ["R7,0", "0,R7", "R7,R0"])
def test_zero_product_resolves_timing_without_known_other_operand(op, operands):
    zero = 65536 if op.endswith("16") else 0
    state = analyze(f"move {zero},R0\nnop\n{op} {operands},R1\nnop\nadd R1,100,R1\nnop\nwait R1\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert state.rt_packets[0].duration == Concrete(100)
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


def test_unsigned_multiply_retains_proven_small_bounds_for_branch():
    state = analyze("and R7,15,R0\nnop\nmulu16 R0,2,R1\nnop\njlt R1,31,@known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("op", ["mulu16", "muls16"])
def test_multiply_by_one_does_not_preserve_untruncated_unknown_value(op):
    state = analyze(f"{op} R7,1,R1\nnop\nsub R7,R1,R2\nnop\njz @maybe\nwait 200\nstop\nmaybe: wait 100\nstop\n")

    assert any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_multiply_write_still_obeys_register_latency():
    state = analyze("mulu16 R7,0,R1\nwait R1\nstop\n")

    assert any(item.category == "register_not_ready" for item in state.diagnostics)
    assert not isinstance(state.rt_packets[0].duration, Concrete)


@pytest.mark.parametrize("op,left,right,low,high", [
    ("mulu32", 0xFFFFFFFF, 2, 0xFFFFFFFE, 1),
    ("muls32", 0x80000000, -1, 0x80000000, 0),
    ("muls32", 0xFFFFFFFF, 2, 0xFFFFFFFE, 0xFFFFFFFF),
])
def test_full_product_tracks_both_words_and_snapshots_overlapping_source(op, left, right, low, high):
    state = analyze(f"move {left},R0\nnop\n{op} R0,{right},R0,R1\nnop\nstop\n")

    assert state.registers["R0"] == Concrete(low)
    assert state.registers["R1"] == Concrete(high)


@pytest.mark.parametrize("op", ["mulu32", "muls32"])
def test_full_zero_product_resolves_both_words(op):
    state = analyze(f"{op} R7,0,R0,R1\nnop\nadd R0,100,R0\nnop\nwait R0\nstop\n")

    assert state.registers["R1"] == Concrete(0)
    assert state.rt_time_ns == Concrete(100)


@pytest.mark.parametrize("op", ["mulu32", "muls32"])
def test_aliased_full_product_destinations_remain_unknown(op):
    state = analyze(f"move 1,R0\nnop\n{op} R0,2,R1,R1\nnop\nstop\n")

    assert not isinstance(state.registers["R1"], Concrete)


@pytest.mark.parametrize("op", ["mulu32l", "muls32l"])
def test_low_word_multiply_by_one_keeps_operand_bounds(op):
    state = analyze(f"and R7,15,R0\nnop\n{op} R0,1,R1\nnop\njlt R1,16,@known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)
    assert state.rt_time_ns == Concrete(100)


def test_unsigned_high_word_by_one_is_zero_without_known_input():
    state = analyze("mulu32h R7,1,R1\nnop\nadd R1,100,R1\nnop\nwait R1\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not state.diagnostics


@pytest.mark.parametrize("op,threshold", [("mulu32l", 31), ("mulu32h", 1)])
def test_unsigned_32_bit_product_bounds_resolve_comparison(op, threshold):
    state = analyze(f"and R7,15,R0\nnop\n{op} R0,2,R1\nnop\njlt R1,{threshold},@known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)
    assert state.rt_time_ns == Concrete(100)


def test_full_unsigned_product_can_prove_high_word_without_known_low_word():
    state = analyze("mulu32 R7,1,R0,R1\nnop\nadd R1,100,R1\nnop\nwait R1\nstop\n")

    assert not isinstance(state.registers["R0"], Concrete)
    assert state.registers["R1"] == Concrete(100)
    assert state.rt_time_ns == Concrete(100)


@pytest.mark.parametrize("op", ["mulu16", "muls16", "mulu32l", "mulu32h", "muls32l", "muls32h"])
def test_single_product_overflow_flag_is_clear_without_known_operands(op):
    state = analyze(f"{op} R7,2,R1\nnop\njno @known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("op,threshold", [("muls16", 31), ("muls32l", 31), ("muls32h", 1)])
def test_signed_product_preserves_nonnegative_input_bounds(op, threshold):
    state = analyze(f"and R7,15,R0\nnop\n{op} R0,2,R1\nnop\njlt R1,{threshold},@known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_16_bit_truncation_preserves_a_uniform_high_word_interval():
    state = analyze("or R7,4294967280,R0\nnop\nmulu16 R0,2,R1\nnop\njge R1,131040,@known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("op", ["mulu16", "muls16"])
def test_known_zero_low_word_proves_product_zero_with_two_unknown_words(op):
    state = analyze(f"and R7,4294901760,R0\nnop\n{op} R0,R8,R1\nnop\nwait R1\nstop\n")

    assert state.registers["R1"] == Concrete(0)
    assert state.rt_time_ns == Concrete(0)


@pytest.mark.parametrize("op", ["mulu32l", "muls32l"])
@pytest.mark.parametrize("factor,mask", [(1, 2), (2, 4), (4, 8)])
def test_low_word_power_of_two_product_retains_sparse_bit_facts(op, factor, mask):
    state = analyze(f"and R7,5,R0\nnop\n{op} R0,{factor},R1\nnop\nand R1,{mask},R2\nnop\nwait R2\nstop\n")

    assert state.registers["R2"] == Concrete(0)
    assert state.rt_time_ns == Concrete(0)


@pytest.mark.parametrize("op", ["mulu32", "muls32"])
def test_full_nonzero_product_has_known_zero_flag_without_assuming_other_flags(op):
    state = analyze(f"move 5,R0\nnop\n{op} R0,4,R1,R2\nnop\njnz @known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("op", ["mulu16", "muls16", "mulu32l", "muls32l"])
def test_even_factor_proves_low_product_bit_zero(op):
    state = analyze(f"{op} R7,2,R1\nnop\ntest R1,1\njz @known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("op", ["mulu16", "muls16", "mulu32l", "muls32l"])
def test_two_unknown_odd_operands_prove_product_odd(op):
    state = analyze(f"or R7,1,R0\nor R8,1,R1\nnop\n{op} R0,R1,R2\nnop\ntest R2,1\njnz @known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("prefix,op", [
    ("move R7,R0", "muls16"),
    ("not R7,R0", "mulu16"),
    ("not R7,R0", "mulu32h"),
])
def test_copied_or_complemented_operand_relation_proves_nonnegative_product(prefix, op):
    state = analyze(f"{prefix}\nnop\n{op} R7,R0,R1\nnop\njns @known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("op", ["mulu16", "muls16", "mulu32l", "muls32l"])
def test_complement_product_is_even_without_known_input_bits(op):
    state = analyze(f"not R7,R0\nnop\n{op} R7,R0,R1\nnop\ntest R1,1\njz @known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_overwritten_source_does_not_keep_a_copied_square_relation():
    state = analyze("move R7,R0\nnop\nmove R8,R7\nnop\nmuls16 R7,R0,R1\nnop\njns @maybe\nwait 200\nstop\nmaybe: wait 100\nstop\n")

    assert any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_full_64_bit_product_zero_flag_includes_its_high_word():
    state = analyze("move 65536,R0\nnop\nmulu32 R0,65536,R1,R2\nnop\njnz @known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.registers["R1"] == Concrete(0)
    assert state.registers["R2"] == Concrete(1)
    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("op", ["mulu32l", "muls32l"])
def test_low_word_uses_signed_equivalence_when_that_proves_a_tighter_interval(op):
    state = analyze(f"and R7,15,R0\nnop\nor R0,1,R0\nmove -2,R2\nnop\n{op} R0,R2,R1\nnop\njs @known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_low_word_copied_negative_square_keeps_its_nonnegative_interval():
    state = analyze("or R7,4294967280,R0\nnop\nmove R0,R2\nnop\nmuls32l R0,R2,R1\nnop\njns @known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("op,destination", [("mulu32l", "R1"), ("muls32l", "R1"), ("mulu32", "R1,R5"), ("muls32", "R1,R5")])
def test_constant_low_word_product_and_shift_preserve_the_same_modular_value(op, destination):
    state = analyze(f"{op} R7,2,{destination}\nlsl R7,1,R2\nnop\nxor R1,R2,R3\nnop\nwait R3\nstop\n")
    assert state.registers["R3"] == Concrete(0)
    assert state.rt_time_ns == Concrete(0)


def test_constant_product_origin_captures_the_operand_before_a_later_overwrite():
    state = analyze("mulu32l R7,2,R1\nmove R8,R7\nnop\nlsl R7,1,R2\nnop\nxor R1,R2,R3\nnop\nstop\n")
    assert not isinstance(state.registers["R3"], Concrete)


def test_high_word_does_not_inherit_the_low_word_linear_relation():
    state = analyze("mulu32 R7,2,R1,R2\nlsl R7,1,R3\nnop\nxor R2,R3,R4\nnop\nstop\n")
    assert not isinstance(state.registers["R4"], Concrete)


@pytest.mark.parametrize("op,inverse", [("mulu32l", 2863311531), ("muls32l", -1431655765)])
def test_inverse_low_product_recovers_peer_facts_before_setting_result_flags(op, inverse):
    state = analyze(f"and R7,5,R1\nnop\n{op} R1,3,R2\nnop\n{op} R2,{inverse},R3\nnop\n"
                    "jns @known\nwait 200\nstop\nknown: wait 100\nstop\n")
    assert state.register_bounds["R3"] == (0, 5)
    assert state.alu_flags["nf"] is False
    assert state.rt_time_ns == Concrete(100)
