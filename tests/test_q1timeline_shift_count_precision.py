import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm


def analyze(source: str, **kwargs):
    return interpret_program(parse_q1asm(source, file="shift-count.q1asm"), sequencer_id="seq0", **kwargs)


@pytest.mark.parametrize("op", ["asl", "lsl", "asr", "lsr"])
@pytest.mark.parametrize("mask", [1, 31, 32])
def test_zero_input_stays_zero_for_every_proven_valid_shift_count(op, mask):
    state = analyze(f"move 0,R1\nand R7,{mask},R0\nnop\n{op} R1,R0,R2\nnop\nstop\n")

    assert state.registers["R2"] == Concrete(0)
    assert state.alu_flags == {"zf": True, "nf": False, "cf": False, "of": False}


def test_arithmetic_right_shift_minus_one_keeps_unknown_carry_when_zero_count_is_possible():
    state = analyze("move -1,R1\nand R7,31,R0\nnop\nasr R1,R0,R2\nnop\nstop\n")

    assert state.registers["R2"] == Concrete(0xFFFFFFFF)
    assert state.alu_flags == {"zf": False, "nf": True, "of": False}


def test_arithmetic_right_shift_minus_one_proves_carry_for_only_positive_counts():
    state = analyze("move -1,R1\nand R7,15,R0\nnop\nadd R0,1,R0\nnop\nasr R1,R0,R2\nnop\nstop\n")

    assert state.registers["R2"] == Concrete(0xFFFFFFFF)
    assert state.alu_flags == {"zf": False, "nf": True, "cf": True, "of": False}


def test_sparse_count_facts_exclude_a_carry_producing_intermediate_count():
    state = analyze("move 1,R1\nand R7,2,R0\nnop\nlsr R1,R0,R2\nnop\nstop\n")

    assert not isinstance(state.registers["R2"], Concrete)
    assert state.register_bounds["R2"] == (0, 1)
    assert state.alu_flags == {"nf": False, "cf": False, "of": False}


def test_variable_count_keeps_carry_and_overflow_unknown_when_counts_disagree():
    state = analyze("move 1,R1\nand R7,32,R0\nnop\nlsl R1,R0,R2\nnop\nstop\n")

    assert not isinstance(state.registers["R2"], Concrete)
    assert state.register_bounds["R2"] == (0, 1)
    assert state.alu_flags == {"nf": False}


def test_variable_shift_count_preserves_result_bounds_for_a_later_branch():
    state = analyze("and R8,15,R1\nand R7,1,R0\nnop\nlsl R1,R0,R2\nnop\njlt R2,31,@known\n"
                    "wait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_variable_arithmetic_shift_keeps_a_proven_sign_for_status_branch():
    state = analyze("or R8,2147483648,R1\nand R7,1,R0\nnop\nasr R1,R0,R2\nnop\njs @known\n"
                    "wait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_variable_shift_constant_result_can_supply_rt_duration():
    state = analyze("move 0,R1\nand R7,31,R0\nnop\nlsl R1,R0,R2\nnop\nadd R2,100,R2\nnop\nwait R2\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not state.diagnostics


@pytest.mark.parametrize("count_source", ["move R7,R0", "and R7,33,R0"])
def test_a_possible_out_of_model_count_prevents_finite_count_proof(count_source):
    state = analyze(f"move 0,R1\n{count_source}\nnop\nlsl R1,R0,R2\nnop\nstop\n")

    assert not isinstance(state.registers["R2"], Concrete)


def test_variable_count_evidence_is_not_read_before_register_write_is_ready():
    state = analyze("move 0,R1\nnop\nand R7,1,R0\nlsl R1,R0,R2\nnop\nstop\n")

    assert any(item.category == "register_not_ready" for item in state.diagnostics)
    assert not isinstance(state.registers["R2"], Concrete)


def test_the_same_snapshot_as_input_and_count_keeps_their_correlation():
    state = analyze("and R7,31,R0\nnop\nlsr R0,R0,R2\nnop\nstop\n")

    assert state.registers["R2"] == Concrete(0)
    assert state.alu_flags == {"zf": True, "nf": False, "of": False}


def test_complement_snapshot_as_input_keeps_its_relation_to_count():
    state = analyze("and R7,31,R0\nnop\nnot R0,R1\nnop\nasr R1,R0,R2\nnop\nstop\n")

    assert state.registers["R2"] == Concrete(0xFFFFFFFF)
    assert state.alu_flags == {"zf": False, "nf": True, "of": False}
