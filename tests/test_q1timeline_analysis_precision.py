import pytest

from q1timeline.analysis.interpreter import AnalysisState, _run_concrete_program, _status_branch_decision, interpret_program
from q1timeline.analysis.underflow import analyze_underflow
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


def analyze(source: str, **kwargs):
    return interpret_program(parse_q1asm(source, file="precision.q1asm"), sequencer_id="seq0", **kwargs)


@pytest.mark.parametrize("operation, expected", [
    ("and R7,0,R1", 0),
    ("and 0,R7,R1", 0),
    ("or R7,4294967295,R1", 0xFFFFFFFF),
    ("or -1,R7,R1", 0xFFFFFFFF),
    ("xor R7,R7,R1", 0),
    ("sub R7,R7,R1", 0),
    ("xor R7,$VALUE,R1", 0),
    ("sub R7,$VALUE,R1", 0),
])
def test_constant_alu_results_do_not_require_the_input_register_value(operation: str, expected: int) -> None:
    state = analyze(f".DEF VALUE R7\n{operation}\nnop\nstop\n")

    assert state.registers["R1"] == Concrete(expected)
    assert state.alu_flags == {"zf": expected == 0, "nf": bool(expected & 0x80000000), "cf": False, "of": False}


def test_constant_alu_result_resolves_following_wait_and_queue_timing() -> None:
    state = analyze("and R7,0,R1\nnop\nadd R1,100,R1\nnop\nwait R1\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert state.rt_packets[0].duration == Concrete(100)
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"
    assert not state.diagnostics


@pytest.mark.parametrize("operation", ["cmp R7,R7", "cmp R7,$VALUE", "test R7,0", "test 0,R7", "xor R7,R7,R1", "sub R7,R7,R1"])
def test_proven_flags_resolve_status_branches(operation: str) -> None:
    state = analyze(f".DEF VALUE R7\n{operation}\nnop\njz @known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


@pytest.mark.parametrize("operation", ["xor R0,R1,R2", "sub R0,R1,R2", "cmp R0,R1"])
@pytest.mark.parametrize("prefix", ["", "fb_pop_data 5,R0\nfb_pop_data 5,R1\nnop\n",
                                    "mulu16 R7,1,R0\nnop\nmulu16 R8,1,R1\nnop\n"])
def test_distinct_unknown_registers_do_not_become_equal_from_matching_value_descriptions(operation: str, prefix: str) -> None:
    state = analyze(prefix + operation + "\nnop\njz @maybe\nwait 200\nstop\nmaybe: wait 100\nstop\n")

    assert any(item.category == "unresolved_branch" for item in state.diagnostics)
    if operation.startswith(("xor", "sub")):
        assert not isinstance(state.registers["R2"], Concrete)


@pytest.mark.parametrize("operation, expected", [("jge", 100), ("jlt", 200)])
@pytest.mark.parametrize("operand", ["R7", "$VALUE"])
@pytest.mark.parametrize("prefix", ["", "fb_pop_data 5,R7\nnop\n"])
def test_unsigned_register_lower_bound_resolves_zero_comparison(operation: str, expected: int, operand: str, prefix: str) -> None:
    state = analyze(f".DEF VALUE R7\n{prefix}{operation} {operand},0,@known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(expected)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("operation", ["jge", "jlt"])
@pytest.mark.parametrize("left", [0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF])
@pytest.mark.parametrize("right", [-1, 0, 1, 0x80000000, 0xFFFFFFFF])
def test_legacy_branch_comparisons_match_unsigned_concrete_execution(operation: str, left: int, right: int) -> None:
    source = f"move {left},R7\nnop\n{operation} R7,{right},@taken\nwait 200\nstop\ntaken: wait 100\nstop\n"
    analysis = analyze(source)
    concrete = analyze(source, execution_mode="concrete")

    assert analysis.rt_time_ns == concrete.rt_time_ns


@pytest.mark.parametrize("operation", ["jge", "jlt"])
def test_unknown_register_comparison_with_negative_one_is_not_a_zero_bound(operation: str) -> None:
    state = analyze(f"{operation} R7,-1,@maybe\nwait 200\nstop\nmaybe: wait 100\nstop\n")

    assert any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("operation, branch, threshold, taken", [
    ("and R7,15,R1", "jge", 16, False),
    ("and R7,15,R1", "jlt", 16, True),
    ("or R7,16,R1", "jlt", 16, False),
    ("or R7,16,R1", "jge", 16, True),
    ("or R7,2147483648,R1", "jlt", 2147483648, False),
    ("and R7,2147483647,R1", "jlt", 2147483648, True),
    ("lsr R7,28,R1", "jlt", 16, True),
    ("lsr R7,1,R1", "jge", 2147483648, False),
])
def test_unsigned_mask_and_shift_bounds_prove_branches(operation: str, branch: str, threshold: int, taken: bool) -> None:
    state = analyze(f"{operation}\nnop\n{branch} R1,{threshold},@taken\nwait 200\nstop\ntaken: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100 if taken else 200)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


@pytest.mark.parametrize("operation", [
    "move $SOURCE,R2", "and $SOURCE,4294967295,R2", "and $SOURCE,-1,R2", "and $SOURCE,$SOURCE,R2",
    "or $SOURCE,0,R2", "or $SOURCE,$SOURCE,R2", "xor $SOURCE,0,R2",
    "add $SOURCE,0,R2", "add 0,$SOURCE,R2", "sub $SOURCE,0,R2",
    "asl $SOURCE,0,R2", "asr $SOURCE,0,R2", "lsl $SOURCE,0,R2", "lsr $SOURCE,0,R2",
])
def test_copies_and_identities_preserve_mask_bound_without_inventing_register_values(operation: str) -> None:
    state = analyze(f".DEF SOURCE R1\nand R7,15,R1\nnop\n{operation}\nnop\njlt R2,16,@taken\nwait 200\nstop\ntaken: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not isinstance(state.registers["R2"], Concrete)
    assert state.alu_flags == {"zf": False, "nf": True, "cf": True, "of": False}
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("operation, threshold", [("add R1,16,R2", 32), ("add 16,R1,R2", 32), ("sub R1,0,R2", 16)])
def test_nonwrapping_register_arithmetic_preserves_proven_upper_bound(operation: str, threshold: int) -> None:
    state = analyze(f"and R7,15,R1\nnop\n{operation}\nnop\njlt R2,{threshold},@taken\nwait 200\nstop\ntaken: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_subtraction_uses_the_right_operand_upper_bound_for_its_lower_bound() -> None:
    state = analyze("and R7,15,R1\nmove 20,R2\nnop\nsub R2,R1,R3\nnop\n"
                    "jge R3,5,@taken\nwait 200\nstop\ntaken: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("body", [
    "and R7,15,R1\nnop\njlt R1,8,@maybe",
    "and R7,15,R1\nnop\nadd R1,4294967295,R1\nnop\njlt R1,16,@maybe",
    "and R7,15,R1\nnop\nsub R1,1,R1\nnop\njlt R1,16,@maybe",
    "and R7,15,R1\nnop\nmove R8,R1\nnop\njlt R1,16,@maybe",
    "and R7,15,R1\nnop\nfb_pop_data 5,R1\nnop\njlt R1,16,@maybe",
    "and R7,15,R1\nnop\nxor R1,R8,R1\nnop\njlt R1,16,@maybe",
    "and R7,15,R1\nnop\nmulu16 R8,1,R1\nnop\njlt R1,16,@maybe",
    "and R7,15,R1\nnop\nand R8,15,R2\nnop\nsub R1,R2,R1\nnop\njlt R1,16,@maybe",
    "asr R7,28,R1\nnop\njlt R1,16,@maybe",
    "lsr R7,33,R1\nnop\njlt R1,16,@maybe",
])
def test_bounds_do_not_prove_ambiguous_wrapped_overwritten_or_unexpanded_values(body: str) -> None:
    state = analyze(body + "\nwait 200\nstop\nmaybe: wait 100\nstop\n")

    assert any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_invariant_loop_mask_result_keeps_its_proven_bound_after_the_last_iteration() -> None:
    state = analyze("move 2,R0\nbody: and R7,15,R1\nnop\nloop R0,@body\n"
                    "jlt R1,16,@known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_mask_bound_is_available_only_when_the_register_write_is_ready() -> None:
    state = analyze("move 100,R1\nnop\nand R7,15,R1\njlt R1,16,@taken\nwait 200\nstop\ntaken: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(200)
    assert any(item.category == "register_not_ready" for item in state.diagnostics)


def test_copy_before_source_write_is_ready_does_not_acquire_its_future_bound() -> None:
    state = analyze("and R7,15,R1\nmove R1,R2\nnop\njlt R2,16,@maybe\nwait 200\nstop\nmaybe: wait 100\nstop\n")

    assert any(item.category == "register_not_ready" for item in state.diagnostics)
    assert any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_copy_bound_is_a_snapshot_and_survives_later_source_overwrite() -> None:
    state = analyze("and R7,15,R1\nnop\nmove R1,R2\nnop\nmove R8,R1\nnop\n"
                    "jlt R2,16,@taken\nwait 200\nstop\ntaken: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_untouched_register_bound_survives_a_compact_loop() -> None:
    state = analyze("and R7,15,R1\nmove 2,R0\nnop\nbody: wait 4\nloop R0,@body\n"
                    "jlt R1,16,@taken\nwait 200\nstop\ntaken: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(108)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_masked_feedback_value_can_prove_a_branch_but_not_feedback_arrival() -> None:
    state = analyze("wait 100\nfb_pop_data 5,R7\nnop\nand R7,15,R1\nnop\n"
                    "jlt R1,16,@taken\nwait 200\nstop\ntaken: wait 100\nstop\n")

    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)
    assert analyze_underflow(state).status == "analysis_incomplete"


def test_register_bounds_do_not_turn_variable_waits_into_exact_durations() -> None:
    state = analyze("and R7,15,R1\nnop\nwait R1\nstop\n")

    assert not isinstance(state.rt_time_ns, Concrete)
    assert analyze_underflow(state).status == "analysis_incomplete"


def test_logical_shift_by_word_width_proves_zero_without_inventing_carry_flags() -> None:
    state = analyze("lsr R7,32,R1\nnop\nadd R1,100,R1\nnop\nwait R1\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    shifted = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "add")
    assert shifted.meta["register_reads"]["R1"]["value"] == 0
    shift_only = analyze("lsr R7,32,R1\nnop\nstop\n")
    assert shift_only.alu_flags == {"zf": True, "nf": False, "of": False}


def test_bound_proven_legacy_branch_does_not_reuse_flags_from_an_earlier_compare() -> None:
    state = analyze("and R7,15,R1\nnop\ncmp R0,R0\nnop\njlt R1,16,@next\nstop\n"
                    "next: jz @maybe\nwait 100\nstop\nmaybe: wait 200\nstop\n")
    branches = [event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") in {"jlt", "jz"}]

    assert branches[0].meta["branch_decision"] == "concrete"
    assert branches[1].meta["branch_decision"] == "concrete"
    assert branches[1].meta["branch_taken"] is False
    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("input_value", [0, 7, 15])
def test_concrete_legacy_comparison_refreshes_flags_for_the_next_status_branch(input_value: int) -> None:
    source = (f"move {input_value},R7\nnop\nand R7,15,R1\nnop\ncmp R0,R0\nnop\n"
              "jlt R1,16,@next\nstop\nnext: jz @wrong\nwait 100\nstop\nwrong: wait 200\nstop\n")
    analysis = analyze(source)
    concrete = analyze("move 0,R0\nnop\n" + source, execution_mode="concrete")

    assert analysis.rt_time_ns == concrete.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in analysis.diagnostics)


@pytest.mark.parametrize("count", [2, 3])
def test_compact_loop_does_not_reuse_alu_flags_from_before_the_loop(count: int) -> None:
    state = analyze(f"move {count},R0\nnop\ncmp R0,0\nbody: wait 100\nloop R0,@body\n"
                    "jz @maybe\nwait 200\nstop\nmaybe: wait 100\nstop\n")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jz")

    assert branch.meta["branch_decision"] == "concrete"
    assert branch.meta["branch_taken"] is True
    assert state.rt_time_ns == Concrete((count + 1) * 100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_single_iteration_loop_updates_flags_from_its_counter_decrement() -> None:
    source = "move 1,R0\nnop\ncmp R0,0\nbody: wait 100\nloop R0,@body\njz @done\nwait 200\nstop\ndone: wait 100\nstop\n"
    analysis = analyze(source)
    concrete = analyze(source, execution_mode="concrete")

    assert analysis.rt_time_ns == concrete.rt_time_ns == Concrete(200)
    assert not any(item.category == "unresolved_branch" for item in analysis.diagnostics)


def test_unconditional_jump_loop_keeps_flags_from_its_body() -> None:
    state = analyze("cmp R0,R0\nbody: wait 4\njmp @body\n")

    assert state.alu_flags == {"zf": True, "nf": False, "cf": False, "of": False}


def test_forward_unconditional_jump_preserves_flags_for_a_status_branch() -> None:
    state = analyze("cmp R0,R0\njmp @after\nwait 200\nafter: jz @known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("prefix, operation, branch, threshold, taken", [
    ("and R7,15,R1", "not R1,R2", "jge", 0xFFFFFFF0, True),
    ("and R7,15,R1", "not R1,R2", "jlt", 0xFFFFFFF0, False),
    ("or R7,4294967280,R1", "not R1,R2", "jlt", 16, True),
    ("and R7,15,R1", "not $SOURCE,R2\nnop\nnot R2,R2", "jlt", 16, True),
])
def test_not_transfers_unsigned_bounds(prefix: str, operation: str, branch: str, threshold: int, taken: bool) -> None:
    state = analyze(f".DEF SOURCE R1\n{prefix}\nnop\n{operation}\nnop\n{branch} R2,{threshold},@taken\nwait 200\nstop\ntaken: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100 if taken else 200)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)
    assert not isinstance(state.registers["R2"], Concrete)


def test_not_bound_is_captured_at_operand_read_and_proves_its_logic_flags() -> None:
    state = analyze("and R7,15,R1\nnop\nnot R1,R2\nnop\nmove R8,R1\nnop\nstop\n")

    assert state.register_bounds["R2"] == (0xFFFFFFF0, 0xFFFFFFFF)
    assert state.alu_flags == {"zf": False, "nf": True, "cf": False, "of": False}


def test_not_pending_write_does_not_publish_its_future_bound() -> None:
    state = analyze("move 0,R2\nand R7,15,R1\nnop\nnot R1,R2\njge R2,4294967280,@taken\nwait 200\nstop\ntaken: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(200)
    assert any(item.category == "register_not_ready" for item in state.diagnostics)


def test_not_bound_is_discarded_on_unknown_destination_overwrite() -> None:
    state = analyze("and R7,15,R1\nnop\nnot R1,R2\nnop\nmove R8,R2\nnop\njge R2,4294967280,@maybe\nwait 200\nstop\nmaybe: wait 100\nstop\n")

    assert any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("operation", ["lsr R1,4,R2", "asr R1,4,R2", "lsl R7,32,R2", "asl R7,32,R2"])
def test_singleton_result_bounds_prove_register_values_and_waits(operation: str) -> None:
    state = analyze(f"and R7,15,R1\nnop\n{operation}\nnop\nwait R2\nstop\n")

    assert state.registers["R2"] == Concrete(0)
    assert state.rt_time_ns == Concrete(4)
    assert state.rt_packets[0].duration == Concrete(0)
    assert "cf" not in state.alu_flags
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


@pytest.mark.parametrize("prefix, operation, branch, threshold", [
    ("and R7,15,R1", "xor R1,4294967295,R2", "jge", 0xFFFFFFF0),
    ("and R7,15,R1", "xor -1,$SOURCE,R2", "jge", 0xFFFFFFF0),
    ("and R7,15,R1", "lsl R1,4,R2", "jlt", 241),
    ("and R7,15,R1", "asl R1,4,R2", "jlt", 241),
    ("and R7,2147483647,R1", "asr R1,28,R2", "jlt", 8),
    ("or R7,2147483648,R1", "asr R1,28,R2", "jge", 0xFFFFFFF8),
    ("and R7,15,R1", "or R1,1,R2", "jlt", 16),
    ("and R7,15,R1", "xor R1,1,R2", "jlt", 16),
])
def test_bit_operation_bounds_resolve_unsigned_branches(prefix: str, operation: str, branch: str, threshold: int) -> None:
    state = analyze(f".DEF SOURCE R1\n{prefix}\nnop\n{operation}\nnop\nmove R8,R1\nnop\n"
                    f"{branch} R2,{threshold},@known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("prefix, operation, threshold", [
    ("or R7,2147483648,R1", "lsl R1,1,R2", 16),
    ("move R7,R1", "asr R1,28,R2", 8),
    ("and R7,15,R1", "or R1,8,R2", 9),
    ("and R7,8,R1", "xor R1,8,R2", 8),
    ("and R7,15,R1", "lsl R1,33,R2", 16),
])
def test_bit_operation_bounds_do_not_prove_wrapping_or_crossing_intervals(prefix: str, operation: str, threshold: int) -> None:
    state = analyze(f"{prefix}\nnop\n{operation}\nnop\njlt R2,{threshold},@maybe\nwait 200\nstop\nmaybe: wait 100\nstop\n")

    assert any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_singleton_proof_does_not_publish_a_pending_result_before_it_is_ready() -> None:
    state = analyze("move 100,R2\nand R7,15,R1\nnop\nlsr R1,4,R2\nwait R2\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert any(item.category == "register_not_ready" for item in state.diagnostics)


@pytest.mark.parametrize("operation, branch, taken", [
    ("or R7,2147483648,R1", "js", True),
    ("or R7,1,R1", "jz", False),
    ("or R7,1,R1", "ja", True),
    ("and R7,15,R1", "js", False),
    ("and R7,15,R1", "jo", False),
    ("xor R7,R8,R1", "jb", False),
    ("not R7,R1", "jo", False),
    ("lsr R7,32,R1", "jz", True),
    ("lsl R7,32,R1", "js", False),
])
def test_partial_result_flags_prove_only_the_status_bits_the_branch_needs(operation: str, branch: str, taken: bool) -> None:
    state = analyze(f"{operation}\nnop\n{branch} @known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100 if taken else 200)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("operation, branch", [("and R7,15,R1", "jz"), ("or R7,1,R1", "js"),
                                              ("lsr R7,32,R1", "jb"), ("lsl R7,32,R1", "jo")])
def test_unproved_status_flag_stays_unknown(operation: str, branch: str) -> None:
    state = analyze(f"cmp R0,R0\n{operation}\nnop\n{branch} @maybe\nwait 200\nstop\nmaybe: wait 100\nstop\n")

    assert any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("branch, flags, expected", [
    ("jg", {"zf": True}, False), ("ja", {"cf": True}, False), ("jle", {"zf": True}, True),
    ("jbe", {"cf": True}, True), ("jl", {"nf": False}, None), ("jge", {"nf": True}, None),
    ("jg", {"zf": False, "nf": True, "of": True}, True),
    ("ja", {"zf": False, "cf": False}, True),
])
def test_compound_status_branches_use_only_proved_flag_completions(branch: str, flags: dict[str, bool], expected: bool | None) -> None:
    assert _status_branch_decision(branch, flags) is expected


@pytest.mark.parametrize("branch, taken_duration", [("jge R7,1,@join", 24), ("jlt R7,1,@join", 24),
                                                   ("jz @join", 16), ("jge @join", 24)])
@pytest.mark.parametrize("taken", [False, True])
def test_assumed_branches_use_the_selected_path_q1_issue_cost(branch: str, taken_duration: int, taken: bool) -> None:
    state = analyze(f"{branch}\nwait 100\nstop\njoin: wait 40\nstop\n",
                    branch_policy="assume_true" if taken else "assume_false")
    issue = next(event for event in state.events if event.kind == "q1_issue")

    assert issue.duration == Concrete(taken_duration if taken else 4)
    assert state.rt_packets[0].q1_issue_t0 == issue.t1


def test_compared_branch_paths_have_their_own_q1_issue_cost() -> None:
    state = analyze("jge R7,1,@join\nnop\njmp @join\njoin: wait 100\nwait 40\nstop\n",
                    branch_policy="explore_both_with_depth_limit")
    by_path = {path: [packet for packet in state.rt_packets if packet.meta.get("branch_comparison_path") == path]
               for path in ("taken", "fallthrough")}

    assert [packet.q1_issue_t0 for packet in by_path["taken"]] == [Concrete(24), Concrete(28)]
    assert [packet.q1_issue_t0 for packet in by_path["fallthrough"]] == [Concrete(24), Concrete(28)]
    assert analyze_underflow(state).status == "analysis_incomplete"


@pytest.mark.parametrize("prefix, operation, branch, taken", [
    ("and R7,15,R1", "cmp R1,16", "jb", True),
    ("and R7,15,R1", "cmp R1,16", "ja", False),
    ("and R7,15,R1", "cmp R1,16", "jl", True),
    ("and R7,15,R1", "add R1,16,R2", "jb", False),
    ("and R7,15,R1", "add R1,16,R2", "jo", False),
    ("and R7,15,R1", "sub R1,16,R2", "jb", True),
    ("and R7,15,R1", "sub R1,16,R2", "js", True),
    ("or R7,4294967280,R1", "add R1,16,R2", "jb", True),
    ("or R7,4294967280,R1", "add R1,16,R2", "js", False),
    ("and R7,15,R1", "sub R1,2147483648,R2", "jo", True),
    ("and R7,15,R1\nnop\nadd R1,1,R1", "add R1,2147483647,R2", "jo", True),
    ("or R7,2147483648,R1", "test R1,R1", "js", True),
])
def test_arithmetic_and_compare_bounds_prove_status_flags(prefix: str, operation: str, branch: str, taken: bool) -> None:
    state = analyze(f"{prefix}\nnop\n{operation}\nnop\n{branch} @known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100 if taken else 200)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("prefix, operation, branch", [
    ("and R7,15,R1", "cmp R1,8", "jb"),
    ("and R7,15,R1", "add R1,2147483647,R2", "jo"),
    ("or R7,4294967280,R1", "sub R1,2147483647,R2", "jo"),
    ("or R7,4294967280,R1", "add R1,16,R2", "jz"),
])
def test_arithmetic_flags_stay_unknown_when_the_interval_crosses_a_flag_boundary(prefix: str, operation: str, branch: str) -> None:
    state = analyze(f"{prefix}\nnop\n{operation}\nnop\n{branch} @maybe\nwait 200\nstop\nmaybe: wait 100\nstop\n")

    assert any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_compare_uses_ready_operand_bounds_without_reading_a_future_result() -> None:
    state = analyze("move 100,R1\nnop\nand R7,15,R1\ncmp R1,16\njb @maybe\nwait 200\nstop\nmaybe: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(200)
    assert any(item.category == "register_not_ready" for item in state.diagnostics)


@pytest.mark.parametrize("operation", ["add R7,0,R1", "add 0,R7,R1", "sub R7,0,R1", "cmp R7,0",
                                     "move -1,R0\nnop\nsub R0,R7,R1"])
def test_unsigned_sign_crossing_intervals_can_still_prove_no_signed_overflow(operation: str) -> None:
    state = analyze(f"{operation}\nnop\njno @known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert state.alu_flags["of"] is False
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("operation", ["add R7,1,R1", "sub R7,1,R1", "cmp R7,1"])
def test_sign_partitioning_does_not_prove_overflow_when_any_partition_can_cross(operation: str) -> None:
    state = analyze(f"{operation}\nnop\njo @maybe\nwait 200\nstop\nmaybe: wait 100\nstop\n")

    assert "of" not in (state.alu_flags or {})
    assert any(item.category == "unresolved_branch" for item in state.diagnostics)


@pytest.mark.parametrize("branch, completed", [("jz", True), ("jb", False), ("jge", False)])
def test_concrete_status_branch_requires_only_its_own_flags_to_be_proved(branch: str, completed: bool) -> None:
    program = parse_q1asm(f"{branch} @known\nwait 200\nstop\nknown: wait 100\nstop\n")
    state = AnalysisState(sequencer_id="seq0", labels=program.labels,
                          instructions_by_pc={item.pc: item for item in program.instructions},
                          execution_mode="concrete", alu_flags={"zf": True})
    _run_concrete_program(state, {}, {}, {}, 100)

    assert state.metadata["execution"]["status"] == ("completed" if completed else "blocked")
    assert state.rt_time_ns == Concrete(100 if completed else 0)
    assert bool(state.rt_packets) is completed


@pytest.mark.parametrize("prefix, expected", [("", 40), ("move 80,R7\nnop\n", 80)])
def test_analysis_alias_and_direct_register_reads_share_effective_parameter_or_write_value(prefix: str, expected: int) -> None:
    state = analyze(f".DEF SOURCE R7\n{prefix}wait R7\nwait $SOURCE\nstop\n", params={"R7": 40})

    assert [packet.duration for packet in state.rt_packets] == [Concrete(expected), Concrete(expected)]
    assert state.rt_time_ns == Concrete(expected * 2)


@pytest.mark.parametrize("operand", ["R7", "$SOURCE"])
def test_concrete_alias_and_direct_register_parameters_do_not_initialize_hardware_registers(operand: str) -> None:
    state = analyze(f".DEF SOURCE R7\nwait {operand}\nstop\n", params={"R7": 40}, execution_mode="concrete")

    assert state.metadata["execution"]["status"] == "blocked"
    assert state.rt_packets == []


def test_concrete_written_register_shadows_parameters_through_aliases_too() -> None:
    state = analyze(".DEF SOURCE R7\nmove 80,R7\nnop\nwait R7\nwait $SOURCE\nstop\n",
                    params={"R7": 40}, execution_mode="concrete")

    assert state.metadata["execution"]["status"] == "completed"
    assert [packet.duration for packet in state.rt_packets] == [Concrete(80), Concrete(80)]
def test_legacy_comparison_preserves_sparse_bit_flags_for_a_following_status_branch():
    state = interpret_program(parse_q1asm("and R7,5,R1\nnop\njlt R1,2,@next\nnop\n"
                                          "next: jz @wrong\nwait 100\nstop\nwrong: wait 200\nstop"),
                              sequencer_id="seq0")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jz")

    assert branch.meta["branch_decision"] == "concrete"
    assert branch.meta["branch_taken"] is False
    assert state.rt_time_ns == Concrete(100)


@pytest.mark.parametrize(("setup", "instruction"), [
    ("", "sw_req 1"),
    ("move 0,R0\nnop\n", "sw_req R0"),
    ("", "play_pulse 0,4"),
    ("move 0,R0\nnop\n", "play_pulse R0,4"),
])
def test_unverified_instruction_preserves_analytical_prefix(setup, instruction):
    state = analyze(f"wait 8\n{setup}set_freq 4000000\n{instruction}\nupd_param 4\nstop")
    op = instruction.split()[0]
    wait, = [event for event in state.events if event.kind == "wait"]
    boundary, = [event for event in state.events if event.kind == "unknown_region"]
    diagnostic, = [item for item in state.diagnostics if item.category == "analysis_incomplete"]
    issue = next(event for event in state.events if event.id == boundary.meta["q1_issue_event_id"])
    pending, = [event for event in state.events if event.kind == "latched_state_pending"]

    assert (wait.t0, wait.t1) == (Concrete(0), Concrete(8))
    assert boundary.t0 == state.rt_time_ns == wait.t1 and boundary.t1 is None
    assert issue.kind == "q1_issue" and issue.meta["op"] == op
    assert issue.source == boundary.source == diagnostic.source
    assert diagnostic.related_events == [boundary.id]
    assert boundary.meta["q1_proof_cutoff"] is True
    assert state.latched_state.pending_event_ids == {"frequency": pending.id}
    assert not any(event.kind in {"play_pulse", "upd_param", "stop"} for event in state.events)
    if op == "play_pulse":
        packet = state.rt_packets[-1]
        assert packet.id == boundary.meta["rt_packet_id"] == issue.meta["rt_packet_id"]
        assert packet.meta["planned_duration_ns"] == boundary.meta["planned_duration_ns"] == 4
        assert packet.rt_t1 is None and packet.confidence == "unknown"
        assert "applied_state" not in boundary.meta and "applied_state" not in packet.meta
    else:
        assert [packet.op for packet in state.rt_packets] == ["wait"]


def test_proven_play_pulse_skip_preserves_else_dwell_and_the_pending_frequency():
    state = analyze("wait 8\nset_freq 4000000\nset_cond 1,0,0,12\nplay_pulse 0,4\n"
                    "set_cond 0,0,0,4\nupd_param 4\nstop")
    skip, = [event for event in state.events if event.kind == "conditional_skip"]
    update, = [event for event in state.events if event.kind == "upd_param"]
    stop, = [event for event in state.events if event.kind == "stop"]

    assert (skip.t0, skip.t1) == (Concrete(8), Concrete(20))
    assert skip.meta["op"] == "play_pulse" and skip.meta["applied_state"] == {}
    assert update.t0 == skip.t1 and update.meta["applied_state"] == {"frequency": 4000000}
    assert stop.t0 == Concrete(24)
    assert not any(event.kind == "unknown_region" for event in state.events)


def test_conditional_pulse_cannot_prove_elapsed_time_from_equal_encoded_dwell():
    state = analyze("wait 8\nset_cond 1,1,0,4\nplay_pulse 0,4\nwait 100\nstop")
    boundary, = [event for event in state.events if event.kind == "unknown_region"]

    assert boundary.t0 == Concrete(8) and boundary.t1 is None
    assert boundary.meta["planned_duration_ns"] == 4
    assert not any(event.kind == "stop" for event in state.events)


@pytest.mark.parametrize(("register_form", "immediate_form", "role", "maximum"), [
    ("set_latch_en R0,4", "set_latch_en {},4", "enable_flag", 1),
    ("wait_trigger R0,R1", "wait_trigger {},4", "trigger_index", 15),
    ("play R0,R1,4", "play {},4,4", "waveform_index", 1023),
    ("acquire 0,R0,4", "acquire 0,{},4", "bin_index", 16777215),
    ("fb_acq_tb_id R0,4", "fb_acq_tb_id {},4", "feedback_acquisition_channel", 255),
])
def test_analytical_rt_admission_requires_a_supported_ready_register(register_form, immediate_form, role, maximum):
    op = register_form.split()[0]
    valid = analyze(f"wait 8\nmove {maximum},R0\nmove 4,R1\nnop\n{register_form}\nupd_param 4\nstop")
    literal = analyze(f"wait 8\n{immediate_form.format(maximum)}\nupd_param 4\nstop")

    for trace in (valid, literal):
        assert [packet.op for packet in trace.rt_packets] == ["wait", op, "upd_param"]
        assert trace.rt_packets[1].rt_t0 == Concrete(8)
        assert trace.rt_packets[1].duration == Concrete(4)
        assert any(event.kind == "stop" for event in trace.events)

    value = maximum + 1
    source = f"wait 8\nmove {value},R0\nmove 4,R1\nnop\n{register_form}\nupd_param 4\nstop"
    simulated, = simulate_programs({"seq0": parse_q1asm(source)}, simulation={"startup_delay_ns": 100})
    assert simulated.metadata["execution"]["status"] == "error"
    assert f"resolved {role} {value} is outside" in simulated.metadata["execution"]["reason"]
    state = analyze(source)
    wait, = [event for event in state.events if event.kind == "wait"]
    boundary, = [event for event in state.events if event.kind == "unknown_region"]
    issue = next(event for event in state.events if event.id == boundary.meta["q1_issue_event_id"])
    diagnostic, = [item for item in state.diagnostics if item.category == "analysis_incomplete"]

    assert (wait.t0, wait.t1) == (Concrete(0), Concrete(8))
    assert boundary.t0 == state.rt_time_ns == wait.t1 and boundary.t1 is None
    assert boundary.meta["q1_proof_cutoff"] is True
    assert diagnostic.related_events == [boundary.id]
    assert issue.kind == "q1_issue" and issue.meta["op"] == op
    assert issue.source == boundary.source == diagnostic.source and issue.source.line == 5
    assert diagnostic.details["operand"] == "R0" and diagnostic.details["register"] == "R0"
    assert diagnostic.details["operand_role"] == role
    assert diagnostic.details["value"] == issue.meta["register_reads"]["R0"]["value"] == value
    assert [packet.op for packet in state.rt_packets] == ["wait"]
    assert not any(event.kind in {op, "upd_param", "stop"} for event in state.events)


@pytest.mark.parametrize("setup", ["", "move 2,R0\nnop\nmove 1,R0\n"], ids=["unresolved", "register-not-ready"])
def test_analytical_rt_range_validation_requires_a_ready_value(setup):
    state = analyze(f"wait 8\n{setup}set_latch_en R0,4\nupd_param 4\nstop")

    assert [packet.op for packet in state.rt_packets] == ["wait", "set_latch_en", "upd_param"]
    assert state.rt_time_ns == Concrete(16)
    assert not any(event.kind == "unknown_region" for event in state.events)
    assert any(item.category == "register_not_ready" for item in state.diagnostics) == bool(setup)


@pytest.mark.parametrize("value", [1, 2])
def test_analytical_rt_register_validation_precedes_a_proven_skip(value):
    state = analyze(f"wait 8\nmove {value},R0\nnop\nset_cond 1,0,0,12\nset_latch_en R0,4\n"
                    "set_cond 0,0,0,4\nupd_param 4\nstop")
    if value == 1:
        skip, = [event for event in state.events if event.kind == "conditional_skip"]
        assert (skip.t0, skip.t1) == (Concrete(8), Concrete(20))
    else:
        boundary, = [event for event in state.events if event.kind == "unknown_region"]
        assert boundary.t0 == Concrete(8) and boundary.source.line == 5
        assert [packet.op for packet in state.rt_packets] == ["wait"]
        assert not any(event.kind in {"conditional_skip", "stop"} for event in state.events)


def test_analytical_rt_register_validation_preserves_duration_validation():
    state = analyze("wait 8\nmove 16,R0\nmove 3,R1\nnop\nwait_trigger R0,R1\nwait 8\nstop")
    boundary, = [event for event in state.events if event.kind == "unknown_region"]

    assert boundary.t0 == Concrete(8) and boundary.meta["reason"] == "invalid_rt_duration"
    assert boundary.meta["operand"] == "R1"
    assert not any(item.details.get("reason") == "unsupported_rt_register_value" for item in state.diagnostics)


@pytest.mark.parametrize(("waveform_index", "expected_alternatives"), [(1023, 2), (1024, 0)])
def test_joined_queue_replay_requires_both_rt_register_admissions(waveform_index, expected_alternatives):
    state = analyze(f"move 0,R1\nnop\nwait 8\njlt R7,1,@taken\nmove {waveform_index},R0\njmp @join\n"
                    "taken: move 1,R0\nnop\njoin: nop\nplay R0,R1,4\nupd_param 4\nstop")
    region, = [event for event in state.events if event.kind == "branch_region"]

    assert region.meta["branch_rt_join_proven"] is True
    assert len(state.queue_alternatives) == expected_alternatives
    for alternative in state.queue_alternatives.values():
        assert [packet.op for packet in alternative.rt_packets] == ["wait", "play", "upd_param"]
