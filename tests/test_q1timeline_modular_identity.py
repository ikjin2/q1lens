import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm


def analyze(source):
    return interpret_program(parse_q1asm(source), sequencer_id="seq0")


@pytest.mark.parametrize("operations", [
    "add R7,1,R1\nnop\nsub R1,1,R2",
    "sub R7,1,R1\nnop\nadd R1,1,R2",
    "add R7,4294967295,R1\nnop\nadd R1,1,R2",
    "add R7,R8,R1\nnop\nsub R1,R8,R2",
    "sub R7,R8,R1\nnop\nadd R1,R8,R2",
])
def test_modular_inverse_arithmetic_retains_the_original_word_for_every_unsigned_input(operations):
    state = analyze(operations + "\nnop\nxor R2,R7,R3\nnop\nstop")

    assert state.registers["R3"] == Concrete(0)


@pytest.mark.parametrize("shift", [0, 1, 4, 31, 32])
def test_repeated_doubling_and_a_logical_left_shift_have_the_same_modular_word(shift):
    additions = ("move R7,R1\nnop\n" + "add R1,R1,R1\nnop\n" * shift if shift < 5
                 else f"lsl R7,{shift - 1},R1\nnop\nadd R1,R1,R1\nnop\n")
    state = analyze(additions + f"lsl R7,{shift},R2\nnop\nxor R1,R2,R3\nnop\nstop")

    assert state.registers["R3"] == Concrete(0)


def test_linear_forms_flatten_commutativity_and_complements_without_symbolic_text_comparisons():
    state = analyze("add R7,R8,R1\nnop\nadd R8,R7,R2\nnop\nxor R1,R2,R3\nnop\n"
                    "not R1,R4\nnop\nadd R4,R2,R5\nnop\nstop")

    assert state.registers["R3"] == Concrete(0)
    assert state.registers["R5"] == Concrete(0xFFFFFFFF)


def test_a_constant_modular_result_does_not_fabricate_carry_or_signed_overflow_flags():
    state = analyze("add R7,1,R1\nnop\nsub R1,R7,R2\nnop\nstop")

    assert state.registers["R2"] == Concrete(1)
    assert state.alu_flags["zf"] is False and state.alu_flags["nf"] is False
    assert "cf" not in state.alu_flags and "of" not in state.alu_flags


def test_compare_consumes_a_constant_modular_difference_without_inventing_order_flags():
    state = analyze("add R7,1,R1\nnop\ncmp R7,R1\njnz @known\nwait 200\nstop\nknown: wait 100\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jnz")

    assert branch.meta["branch_decision"] == "concrete" and branch.meta["branch_taken"] is True
    assert state.rt_time_ns == Concrete(100)
    assert state.alu_flags["zf"] is False and state.alu_flags["nf"] is True
    assert "cf" not in state.alu_flags and "of" not in state.alu_flags


def test_linear_origins_follow_pending_readiness_and_never_lend_a_future_value_to_a_read():
    state = analyze("move R7,R1\nnop\nadd R1,1,R2\nsub R2,1,R3\nnop\nxor R3,R1,R4\nnop\nstop")

    assert not isinstance(state.registers["R4"], Concrete)
    assert any(item.category == "register_not_ready" for item in state.diagnostics)


def test_linear_snapshots_do_not_follow_a_later_source_overwrite():
    state = analyze("add R7,1,R1\nnop\nmove R8,R7\nnop\nsub R1,1,R2\nnop\nxor R2,R7,R3\nnop\nstop")

    assert not isinstance(state.registers["R3"], Concrete)


def test_independent_feedback_words_with_matching_reason_strings_keep_distinct_linear_roots():
    state = analyze("fb_pop_data 5,R7\nnop\nadd R7,1,R1\nnop\nfb_pop_data 5,R7\nnop\n"
                    "sub R1,1,R2\nnop\nxor R2,R7,R3\nnop\nstop")

    assert not isinstance(state.registers["R3"], Concrete)


def test_both_branch_arms_can_preserve_the_same_linear_form_of_shared_entry_words():
    state = analyze("jge R6,1,@other\nadd R7,1,R1\nnop\njmp @join\n"
                    "other: sub R7,4294967295,R1\nnop\njoin: sub R1,1,R2\nnop\nxor R2,R7,R3\nnop\nstop")

    assert state.registers["R3"] == Concrete(0)
    assert any(event.meta.get("branch_rt_join_proven") for event in state.events)


def test_linear_forms_over_fresh_arm_writes_do_not_become_shared_entry_identity():
    state = analyze("jge R6,1,@other\nmulu16 R7,R8,R1\nnop\nadd R1,1,R2\nnop\njmp @join\n"
                    "other: mulu16 R9,R10,R1\nnop\nadd R1,1,R2\nnop\n"
                    "join: sub R2,1,R3\nnop\nxor R3,R1,R4\nnop\nstop")

    assert not isinstance(state.registers["R4"], Concrete)


@pytest.mark.parametrize("term_count,proved", [(8, True), (9, False)])
def test_linear_identity_proof_has_a_bounded_primitive_term_count(term_count, proved):
    source = "move R0,R20\nnop\n"
    source += "".join(f"add R20,R{index},R20\nnop\n" for index in range(1, term_count))
    source += "".join(f"sub R20,R{index},R20\nnop\n" for index in range(term_count - 1, 0, -1))
    state = analyze(source + "xor R20,R0,R21\nnop\nstop")

    assert (state.registers["R21"] == Concrete(0)) == proved
    assert all(len(origin[0]) <= 4103 for origin in state.register_value_origins.values())


def test_unknown_loop_iterations_invalidate_the_observed_first_iterations_linear_origin():
    state = analyze("move R7,R9\nnop\nbody: wait 100\nadd R7,1,R7\nnop\nloop R0,@body\n"
                    "sub R7,1,R1\nnop\nxor R1,R9,R2\nnop\nstop")

    assert not isinstance(state.registers["R2"], Concrete)


@pytest.mark.parametrize("prefix,expected", [
    ("and R7,5,R1", 0),
    ("or R7,4294967290,R1", 2),
])
def test_inverse_arithmetic_recovers_a_ready_peers_sparse_bits(prefix, expected):
    state = analyze(f"{prefix}\nnop\nadd R1,R8,R2\nnop\nsub R2,R8,R3\nnop\nand R3,2,R4\nnop\nwait R4\nstop")

    assert state.registers["R4"] == Concrete(expected)
    assert state.rt_time_ns == Concrete(expected)


def test_complementary_ready_peer_facts_are_inverted_before_reduction():
    state = analyze("and R7,5,R1\nnop\nadd R1,R8,R2\nnop\nnot R2,R3\nnop\nadd R3,R8,R4\nnop\n"
                    "and R4,2,R5\nnop\nstop")

    assert state.registers["R5"] == Concrete(2)
    assert state.register_bounds["R4"] == (0xFFFFFFFA, 0xFFFFFFFF)


@pytest.mark.parametrize("overwrite", ["sub R2,R8,R3\nmove R9,R1", "move R9,R1\nsub R2,R8,R3"])
def test_reduced_result_facts_are_snapshots_even_when_the_peer_has_a_pending_overwrite(overwrite):
    state = analyze("and R7,5,R1\nnop\nadd R1,R8,R2\nnop\n" + overwrite
                    + "\nnop\nand R3,2,R4\nnop\nstop")

    assert state.registers["R4"] == Concrete(0)
    assert state.register_known_bits["R3"][1] & 2
    assert not state.register_known_bits.get("R1", (0, 0))[1] & 2


def test_future_peer_facts_do_not_refine_a_result_with_the_old_snapshot_origin():
    state = analyze("move R7,R1\nnop\nadd R1,R8,R2\nnop\nand R1,5,R1\nsub R2,R8,R3\nnop\n"
                    "and R3,2,R4\nnop\nstop")

    assert not isinstance(state.registers["R4"], Concrete)


def test_overwritten_peer_facts_do_not_survive_without_a_captured_reduced_result():
    state = analyze("and R7,5,R1\nnop\nadd R1,R8,R2\nnop\nmove R9,R1\nnop\n"
                    "sub R2,R8,R3\nnop\nand R3,2,R4\nnop\nstop")

    assert not isinstance(state.registers["R4"], Concrete)


def test_ready_peer_bounds_refine_result_flags_without_inventing_carry_or_overflow():
    state = analyze("or R7,2147483648,R1\nnop\nadd R1,R8,R2\nnop\nsub R2,R8,R3\nnop\nstop")

    assert state.register_bounds["R3"] == (0x80000000, 0xFFFFFFFF)
    assert state.alu_flags["nf"] is True and state.alu_flags["zf"] is False
    assert "cf" not in state.alu_flags and "of" not in state.alu_flags


def test_branch_promoted_concrete_peer_retains_its_original_snapshot_relation():
    state = analyze("and R7,1,R1\nnop\nadd R1,R8,R2\nnop\njge R1,1,@other\n"
                    "sub R2,R8,R3\nnop\njmp @join\nother: move 0,R3\nnop\njoin: wait R3\nstop")

    assert state.registers["R3"] == Concrete(0)
    assert state.rt_time_ns == Concrete(0)


@pytest.mark.parametrize("iterations", [16, 32])
def test_repeated_unknown_doubling_keeps_display_expressions_bounded(iterations):
    state = analyze("move R7,R1\nnop\n" + "add R1,R1,R1\nnop\n" * iterations + "stop\n")
    assert len(str(state.registers["R1"])) <= 1024
    assert len(state.register_provenance["R1"]["expression"]) <= 1024
    assert all(len(str(write.value)) <= 1024 for write in state.register_history)
    if iterations == 32:
        assert state.registers["R1"] == Concrete(0)


def test_abbreviating_a_symbolic_display_preserves_its_unresolved_inputs():
    from q1timeline.analysis.values import Symbolic
    state = analyze("move {INPUT},R1\nnop\n" + "add R1,R1,R1\nnop\n" * 16 + "stop\n")
    value = state.registers["R1"]
    assert isinstance(value, Symbolic)
    assert value.unresolved_symbols == ("INPUT",)
    assert len(value.expr) <= 1024
