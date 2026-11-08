import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm


def analyze(source: str, **kwargs):
    return interpret_program(parse_q1asm(source, file="bit-precision.q1asm"), sequencer_id="seq0", **kwargs)


@pytest.mark.parametrize("prefix,operation,expected", [
    ("or R7,2147483648,R1", "and R1,2147483648,R2", 0x80000000),
    ("and R7,2147483647,R1", "and R1,2147483648,R2", 0),
    ("or R7,4294967280,R1", "and R1,4294967280,R2", 0xFFFFFFF0),
])
def test_interval_common_bits_resolve_extracted_result(prefix, operation, expected):
    state = analyze(f"{prefix}\nnop\n{operation}\nnop\nstop\n")

    assert state.registers["R2"] == Concrete(expected)


def test_extracted_bit_can_supply_a_known_rt_duration_through_an_alias():
    source = (".DEF INPUT R1\n.DEF RESULT R2\nor R7,2147483648,$INPUT\nnop\n"
              "and $INPUT,2147483648,$RESULT\nnop\nlsr $RESULT,31,$RESULT\nnop\n"
              "add $RESULT,99,$RESULT\nnop\nwait $RESULT\nstop\n")
    state = analyze(source)

    assert state.rt_time_ns == Concrete(100)
    assert not state.diagnostics


def test_varying_low_bits_are_not_mistaken_for_common_bits():
    state = analyze("or R7,4294967280,R1\nnop\nand R1,15,R2\nnop\nstop\n")

    assert not isinstance(state.registers["R2"], Concrete)
    assert state.register_bounds["R2"] == (0, 15)


@pytest.mark.parametrize("prefix,shift,expected_cf,expected_of", [
    ("and R7,15,R1", "lsl R1,4,R2", False, False),
    ("and R7,15,R1", "asl R1,4,R2", False, False),
    ("or R7,2147483648,R1", "lsr R1,32,R2", True, False),
    ("and R7,2147483647,R1", "lsr R1,32,R2", False, False),
    ("or R7,2147483648,R1", "asr R1,32,R2", True, False),
    ("and R7,1,R1", "lsl R1,31,R2", False, None),
])
def test_bounded_shift_carry_uses_the_shifted_out_input_bit(prefix, shift, expected_cf, expected_of):
    state = analyze(f"{prefix}\nnop\n{shift}\nnop\nstop\n")

    assert state.alu_flags["cf"] is expected_cf
    if expected_of is None:
        assert "of" not in state.alu_flags
    else:
        assert state.alu_flags["of"] is expected_of


@pytest.mark.parametrize("branch,expected_time", [("jb", 100), ("jno", 200)])
def test_known_shift_flags_resolve_the_following_status_branch(branch, expected_time):
    source = (f"and R7,15,R1\nnop\nlsl R1,4,R2\nnop\n{branch} @taken\n"
              "wait 100\nstop\ntaken: wait 200\nstop\n")
    state = analyze(source)

    assert state.rt_time_ns == Concrete(expected_time)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("shift", ["lsr R7,32,R2", "lsl R7,32,R2"])
def test_zero_shift_result_does_not_prove_unknown_carry(shift):
    state = analyze(f"{shift}\nnop\njb @maybe\nwait 100\nstop\nmaybe: wait 200\nstop\n")

    assert state.registers["R2"] == Concrete(0)
    assert "cf" not in state.alu_flags
    assert any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("prefix,operation,threshold", [
    ("or R7,4294967280,R1", "add R1,16,R2", 16),
    ("and R7,15,R1", "sub R1,16,R2", 0xFFFFFFEF),
])
def test_uniformly_wrapping_arithmetic_keeps_the_result_interval(prefix, operation, threshold):
    branch = "jlt" if operation.startswith("add") else "jge"
    state = analyze(f"{prefix}\nnop\n{operation}\nnop\n{branch} R2,{threshold},@known\n"
                    "wait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_mixed_wrapping_outcomes_do_not_get_an_invalid_contiguous_interval():
    state = analyze("and R7,15,R1\nnop\nadd R1,4294967295,R2\nnop\njlt R2,16,@maybe\n"
                    "wait 200\nstop\nmaybe: wait 100\nstop\n")

    assert "R2" not in state.register_bounds
    assert any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("prefix,operand,branch,expected_time", [
    ("or R7,2147483648,R1", "2147483648", "js", 200),
    ("or R7,2147483648,R1", "2147483648", "jz", 100),
    ("and R7,15,R1", "2147483648", "jz", 200),
    ("and R7,15,R1", "15", "jns", 200),
    ("move R7,R1", "R8", "jb", 100),
    ("move R7,R1", "R8", "jno", 200),
])
def test_bounded_test_flags_resolve_only_the_proven_status_bits(prefix, operand, branch, expected_time):
    source = (f"{prefix}\nnop\ntest R1,{operand}\nnop\n{branch} @taken\n"
              "wait 100\nstop\ntaken: wait 200\nstop\n")
    state = analyze(source)

    assert state.rt_time_ns == Concrete(expected_time)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_varying_test_zero_flag_stays_unknown():
    state = analyze("and R7,15,R1\nnop\ntest R1,15\nnop\njz @maybe\n"
                    "wait 100\nstop\nmaybe: wait 200\nstop\n")

    assert "zf" not in state.alu_flags
    assert any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_bit_extraction_does_not_reuse_bounds_after_register_replacement():
    state = analyze("or R7,2147483648,R1\nnop\nmove R8,R1\nnop\nand R1,2147483648,R2\nnop\nstop\n")

    assert not isinstance(state.registers["R2"], Concrete)


def test_bit_extraction_keeps_register_latency_visible():
    state = analyze("or R7,2147483648,R1\nand R1,2147483648,R2\nnop\nstop\n")

    assert any(item.category == "register_not_ready" for item in state.diagnostics)
    assert not isinstance(state.registers["R2"], Concrete)


@pytest.mark.parametrize("prefix,operation,expected", [
    ("and R7,5,R1", "and R1,2,R2", 0),
    ("and R7,8,R1", "or R1,8,R2", 8),
    ("or R7,5,R1", "and R1,1,R2", 1),
    ("and R7,5,R1\nnop\nxor R1,2,R1", "and R1,2,R2", 2),
    ("and R7,5,R1\nnop\nor R1,2,R1", "and R1,2,R2", 2),
    ("and R7,5,R1\nnop\nnot R1,R1", "and R1,2,R2", 2),
    ("or R7,5,R1\nnop\nnot R1,R1", "and R1,1,R2", 0),
])
def test_sparse_mask_facts_survive_noncontiguous_value_ranges(prefix, operation, expected):
    state = analyze(f"{prefix}\nnop\n{operation}\nnop\nstop\n")

    assert state.registers["R2"] == Concrete(expected)


def test_sparse_mask_facts_flow_through_move_and_alias_to_rt_duration():
    source = (".DEF SOURCE R1\n.DEF COPY R2\nand R7,5,$SOURCE\nnop\nmove $SOURCE,$COPY\nnop\n"
              "and $COPY,2,R3\nnop\nadd R3,100,R3\nnop\nwait R3\nstop\n")
    state = analyze(source)

    assert state.rt_time_ns == Concrete(100)
    assert not state.diagnostics


@pytest.mark.parametrize("identity", ["add R1,0,R2", "sub R1,0,R2", "lsl R1,0,R2", "asr R1,0,R2"])
def test_sparse_mask_facts_survive_existing_identity_operations(identity):
    state = analyze(f"and R7,5,R1\nnop\n{identity}\nnop\nand R2,2,R3\nnop\nstop\n")

    assert state.registers["R3"] == Concrete(0)


@pytest.mark.parametrize("shift,bit,expected", [
    ("lsl R1,1,R2", 4, 0),
    ("lsr R1,1,R2", 1, 0),
    ("asr R1,1,R2", 1, 0),
    ("asl R1,1,R2", 4, 0),
])
def test_sparse_mask_facts_move_with_shifted_bits(shift, bit, expected):
    state = analyze(f"and R7,5,R1\nnop\n{shift}\nnop\nand R2,{bit},R3\nnop\nstop\n")

    assert state.registers["R3"] == Concrete(expected)


def test_sparse_shift_carry_checks_the_input_bit_even_when_interval_spans_it():
    state = analyze("and R7,5,R1\nnop\nlsl R1,31,R2\nnop\njb @wrong\n"
                    "wait 100\nstop\nwrong: wait 200\nstop\n")

    assert state.alu_flags["cf"] is False
    assert "of" not in state.alu_flags
    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_sparse_test_can_prove_zero_in_a_hole_of_the_interval():
    state = analyze("and R7,5,R1\nnop\ntest R1,2\nnop\njz @known\n"
                    "wait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_pending_copy_snapshots_sparse_facts_before_source_replacement():
    state = analyze("and R7,5,R1\nnop\nmove R1,R2\nmove 255,R1\nnop\nand R2,2,R3\nnop\nstop\n")

    assert state.registers["R3"] == Concrete(0)


@pytest.mark.parametrize("replacement", ["move R8,R1", "fb_pop_data 1,R1"])
def test_unknown_overwrite_clears_old_sparse_facts(replacement):
    state = analyze(f"and R7,5,R1\nnop\n{replacement}\nnop\nand R1,2,R2\nnop\nstop\n")

    assert not isinstance(state.registers["R2"], Concrete)


def test_sparse_facts_are_not_available_before_the_write_is_readable():
    state = analyze("and R7,5,R1\nand R1,2,R2\nnop\nstop\n")

    assert any(item.category == "register_not_ready" for item in state.diagnostics)
    assert not isinstance(state.registers["R2"], Concrete)


def test_matching_sparse_facts_do_not_make_distinct_register_values_equal():
    state = analyze("and R7,5,R1\nand R8,5,R2\nnop\nxor R1,R2,R3\nnop\njz @maybe\n"
                    "wait 100\nstop\nmaybe: wait 200\nstop\n")

    assert not isinstance(state.registers["R3"], Concrete)
    assert any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("prefix,operation,mask,expected", [
    ("or R7,1,R1", "sub R1,1,R2", 1, 0),
    ("and R7,4294967294,R1", "add R1,1,R2", 1, 1),
    ("or R7,3,R1", "sub R1,3,R2", 3, 0),
    ("and R7,4294967292,R1", "add R1,3,R2", 3, 3),
    ("or R7,5,R1", "sub R1,1,R2", 5, 4),
])
def test_sparse_arithmetic_retains_proven_result_bits(prefix, operation, mask, expected):
    state = analyze(f"{prefix}\nnop\n{operation}\nnop\nand R2,{mask},R3\nnop\nstop\n")

    assert state.registers["R3"] == Concrete(expected)


@pytest.mark.parametrize("prefix,operation", [
    ("or R7,1,R1", "sub R1,1,R2"),
    ("and R7,4294967294,R1", "add R1,1,R2"),
])
def test_sparse_arithmetic_can_prove_no_signed_overflow_across_a_mixed_sign_interval(prefix, operation):
    state = analyze(f"{prefix}\nnop\n{operation}\nnop\njo @wrong\nwait 100\nstop\nwrong: wait 200\nstop\n")

    assert state.alu_flags["of"] is False
    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_sparse_arithmetic_result_bits_can_supply_rt_duration():
    state = analyze("or R7,1,R1\nnop\nsub R1,1,R2\nnop\nand R2,1,R3\nnop\nadd R3,100,R3\nnop\nwait R3\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not state.diagnostics


@pytest.mark.parametrize("right", ["R7", "$INPUT"])
def test_self_addition_proves_even_result_without_known_input(right):
    state = analyze(f".DEF INPUT R7\nadd R7,{right},R1\nnop\nand R1,1,R2\nnop\nstop\n")

    assert state.registers["R2"] == Concrete(0)


def test_distinct_unknown_addends_do_not_get_self_addition_correlation():
    state = analyze("add R7,R8,R1\nnop\nand R1,1,R2\nnop\njz @maybe\nwait 100\nstop\nmaybe: wait 200\nstop\n")

    assert not isinstance(state.registers["R2"], Concrete)
    assert any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_arithmetic_with_both_overflow_outcomes_keeps_overflow_unknown():
    state = analyze("and R7,1,R1\nnop\nadd R1,2147483647,R2\nnop\njo @maybe\n"
                    "wait 100\nstop\nmaybe: wait 200\nstop\n")

    assert "of" not in state.alu_flags
    assert any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_sparse_arithmetic_replaces_old_destination_facts():
    state = analyze("or R7,5,R1\nnop\nmove R8,R2\nnop\nadd R2,1,R1\nnop\nand R1,1,R3\nnop\nstop\n")

    assert not isinstance(state.registers["R3"], Concrete)
