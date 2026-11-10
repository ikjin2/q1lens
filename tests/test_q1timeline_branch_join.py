import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.underflow import analyze_underflow
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm


def analyze(source: str, **kwargs):
    return interpret_program(parse_q1asm(source, file="join.q1asm"), sequencer_id="seq0", **kwargs)


@pytest.mark.parametrize("prefix", ["", "wait 100\n"])
@pytest.mark.parametrize("target", ["@join", "4"])
def test_side_effect_free_equal_q1_paths_resume_a_common_exact_suffix(prefix: str, target: str) -> None:
    target = str(int(target) + 1) if prefix and target.isdigit() else target
    source = prefix + f"jge R7,1,{target}\nnop\njmp @join\njoin: wait 100\nwait 40\nstop"
    state = analyze(source)
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert branch.meta["branch_decision"] == "runtime_dependent"
    assert branch.meta["branch_taken"] == "runtime_dependent"
    assert branch.meta["branch_join_proven"]
    assert state.rt_time_ns == Concrete(240 if prefix else 140)
    assert [packet.q1_issue_t0 for packet in state.rt_packets[-2:]] == [Concrete(28 if prefix else 24), Concrete(32 if prefix else 28)]
    assert not state.metadata.get("branches", {}).get("assumptions")
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


@pytest.mark.parametrize("seed", [0, 1, 0xFFFFFFFF])
def test_proven_join_packet_stream_matches_concrete_execution_for_both_paths(seed: int) -> None:
    source = "jge R7,1,@join\nnop\njmp @join\njoin: wait 100\nwait 40\nstop"
    state = analyze(source)
    concrete = analyze(f"move {seed},R7\nnop\n" + source, execution_mode="concrete")

    assert [(packet.duration, packet.rt_t0, packet.rt_t1) for packet in state.rt_packets] == [
        (packet.duration, packet.rt_t0, packet.rt_t1) for packet in concrete.rt_packets]
    assert [packet.q1_issue_t0.value for packet in state.rt_packets] == [packet.q1_issue_t0.value - 8 for packet in concrete.rt_packets]


def test_status_branch_can_prove_a_common_q1_only_join_without_proving_its_condition() -> None:
    state = analyze("cmp R7,0\njz @join\nnop\nnop\nnop\njoin: wait 100\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jz")

    assert branch.meta["branch_join_proven"]
    assert branch.meta["branch_decision"] == "runtime_dependent"
    assert state.rt_packets[0].q1_issue_t0 == Concrete(28)
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


@pytest.mark.parametrize("fallthrough", [
    "jmp @join\n", "wait 100\nnop\njmp @join\n", "move 100,R1\nnop\njmp @join\n",
    "set_mrk 3\nnop\njmp @join\n", "stop\n", "nop\njmp R8\n", "nop\njmp @missing\n",
    "again: nop\njmp @again\n",
])
def test_side_effects_unknown_targets_and_different_q1_clocks_do_not_form_a_proven_join(fallthrough: str) -> None:
    state = analyze(f"jge R7,1,@join\n{fallthrough}join: wait 100\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert not branch.meta.get("branch_join_proven")
    queue_safe = fallthrough in {"jmp @join\n", "move 100,R1\nnop\njmp @join\n"}
    assert analyze_underflow(state).status == ("not_detected_under_current_assumptions" if queue_safe else "analysis_incomplete")
    assert bool(state.metadata["underflow"].get("all_paths_safe")) == queue_safe


def test_join_proof_does_not_flush_a_pending_write_to_manufacture_agreement() -> None:
    state = analyze("move 100,R1\njge R7,1,@join\nnop\njmp @join\njoin: wait 100\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert not branch.meta.get("branch_join_proven")
    assert analyze_underflow(state).status == "analysis_incomplete"


@pytest.mark.parametrize("choice", ["collapsed", "taken", "fallthrough", "both"])
def test_explicit_branch_selection_keeps_its_requested_presentation(choice: str) -> None:
    state = analyze("jge R7,1,@join\nnop\njmp @join\njoin: wait 100\nstop",
                    branch_assumptions={"seq0:branch:join.q1asm:1:jge:join": choice})
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert not branch.meta.get("branch_join_proven")
    assert branch.meta["assumed_branch_path"] == choice
    if choice == "both":
        assert {packet.meta["branch_comparison_path"] for packet in state.rt_packets} == {"taken", "fallthrough"}


@pytest.mark.parametrize("prefix", ["fb_pop_data 5,R8\nnop\n", "wait R8\n"])
def test_common_join_does_not_remove_prior_feedback_or_unknown_duration_boundaries(prefix: str) -> None:
    state = analyze(prefix + "jge R7,1,@join\nnop\njmp @join\njoin: wait 100\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert branch.meta["branch_join_proven"]
    assert analyze_underflow(state).status == "analysis_incomplete"
    if prefix.startswith("wait"):
        assert not isinstance(state.rt_packets[-1].rt_t0, Concrete)


def test_equal_wait_path_durations_prove_common_rt_time_without_inventing_q1_arrival() -> None:
    state = analyze("jge R7,1,@other\nwait 100\njmp @join\nother: wait 100\njoin: wait 40\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert branch.meta["branch_rt_join_proven"]
    assert branch.meta["branch_join_proven"] is False
    assert branch.meta["join_q1_time_ns"] is None
    assert state.rt_time_ns == Concrete(140)
    assert state.rt_packets[-1].rt_t0 == Concrete(100)
    assert not isinstance(state.rt_packets[-1].q1_issue_t0, Concrete)
    assert not state.metadata.get("branches", {}).get("assumptions")
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"
    assert state.metadata["underflow"]["all_paths_safe"]


@pytest.mark.parametrize("wait_first", [False, True])
def test_equal_join_q1_time_proves_queue_timing_only_when_wait_packet_arrivals_also_match(wait_first: bool) -> None:
    fallthrough = "wait 100\n" + "nop\n" * 5 if wait_first else "nop\n" * 5 + "wait 100\n"
    source = f"jge R7,1,@other\n{fallthrough}jmp @join\nother: wait 100\n" + "nop\n" * 4 + "join: wait 40\nstop"
    state = analyze(source)
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert branch.meta["branch_rt_join_proven"]
    assert branch.meta["branch_join_proven"] == (not wait_first)
    assert branch.meta["join_q1_time_ns"] == 44
    assert state.rt_time_ns == Concrete(140)
    assert state.rt_packets[-1].q1_issue_t0 == Concrete(44)
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"
    assert bool(state.metadata["underflow"].get("all_paths_safe")) == wait_first


def test_different_zero_guard_packet_sequences_require_separate_safe_queue_replays() -> None:
    state = analyze("jge R7,1,@other\nwait 0\nwait 100\njmp @join\nother: wait 104\njoin: wait 40\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert branch.meta["branch_rt_join_proven"]
    assert not branch.meta["branch_join_proven"]
    assert branch.meta["join_rt_duration_ns"] == 104
    assert state.rt_time_ns == Concrete(144)
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"
    assert state.metadata["underflow"]["all_paths_safe"]


def test_zero_guard_spacing_can_prevent_an_rt_time_join() -> None:
    state = analyze("jge R7,1,@other\nwait 0\nwait 100\njmp @join\nother: wait 100\njoin: wait 40\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")
    assert not branch.meta.get("branch_rt_join_proven")
    assert analyze_underflow(state).status == "analysis_incomplete"


@pytest.mark.parametrize("nominal", [100, "{NOMINAL}"])
def test_wait_join_uses_a_known_conditional_skip_duration_on_both_paths(nominal) -> None:
    state = analyze(f"set_cond 1,0,0,20\njge R7,1,@other\nwait {nominal}\njmp @join\nother: wait 40\njoin: wait 40\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert branch.meta["branch_rt_join_proven"]
    assert state.rt_time_ns == Concrete(40)
    assert all(event.duration == Concrete(20) for event in state.events if event.kind == "conditional_skip")


def test_timing_join_marks_both_side_arms_visited_before_a_later_numeric_register_return() -> None:
    state = analyze("jge R7,1,@join\nnop\njmp @join\njoin: wait 100\nmove 1,R0\nnop\njmp R0\nstop")
    returned = next(event for event in state.events if event.kind == "q1_issue" and event.source.raw == "jmp R0")

    assert {1, 2} <= state.executed_pcs
    assert not returned.meta.get("register_return")
    assert state.stopped


def test_equal_ready_literal_register_writes_prove_a_common_register_duration() -> None:
    state = analyze(".DEF VALUE 4\n.DEF DEST R1\njge R7,1,@other\nmove $VALUE,$DEST\nnop\nwait 100\njmp @join\n"
                    "other: move 4,R1\nnop\nwait 100\njoin: wait R1\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")
    proof = state.register_provenance["R1"]
    joined, = [write for write in state.register_history if write.meta.get("branch_join_proven")]

    assert branch.meta["branch_rt_join_proven"]
    assert branch.meta["join_registers"] == {"R1": 4}
    assert state.registers["R1"] == Concrete(4)
    assert state.rt_time_ns == Concrete(104)
    assert proof["steps"][0]["branch_join_sources"].keys() == {"taken", "fallthrough"}
    assert joined.value == Concrete(4)
    assert any(write.confidence == "assumed" and write.meta.get("branch_join_path") == "taken" for write in state.register_history)
    assert not state.pending_register_writes
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"
    assert state.metadata["underflow"]["all_paths_safe"]


def test_one_arm_can_reestablish_an_unchanged_initial_concrete_register_value() -> None:
    state = analyze("move 4,R1\nnop\njge R7,1,@join\nmove 4,R1\nnop\njmp @join\njoin: wait R1\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert branch.meta["branch_rt_join_proven"]
    assert state.rt_time_ns == Concrete(4)
    assert state.register_provenance["R1"]["steps"][0]["branch_join_sources"]["taken"] == {"before_branch": True}


@pytest.mark.parametrize("taken_arm, fallthrough_arm, proven", [
    ("move 4,R1\nnop\n", "move 8,R1\nnop\n", True),
    ("move 4,R1\n", "move 4,R1\nnop\n", False),
    ("move 4,R1\nnop\n", "", True),
    ("move 4,R0\nnop\nmove 4,R1\nnop\n", "move 8,R0\nnop\nmove {R0},R1\nnop\n", True),
    ("move 4,R0\nnop\nwait 4\n", "move 8,R0\nnop\nwait {R0}\n", False),
])
def test_register_joins_merge_disagreement_without_claiming_unready_or_different_rt_paths(taken_arm: str, fallthrough_arm: str, proven: bool) -> None:
    state = analyze(f"jge R7,1,@other\n{fallthrough_arm}jmp @join\nother: {taken_arm}join: wait 100\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert bool(branch.meta.get("branch_rt_join_proven")) == proven
    if proven:
        assert not isinstance(state.registers["R1"], Concrete)
        assert state.rt_time_ns == Concrete(100)
    assert analyze_underflow(state).status == ("not_detected_under_current_assumptions" if proven else "analysis_incomplete")


def test_common_ready_register_values_do_not_reuse_side_arm_provenance_as_the_only_source() -> None:
    state = analyze("jge R7,1,@other\nmove 4,R1\nnop\njmp @join\nother: move 4,R1\nnop\njoin: wait R1\nstop")
    wait = next(event for event in state.events if event.kind == "wait")

    assert wait.duration == Concrete(4)
    assert wait.meta["duration_provenance"]["steps"][0]["branch_join_sources"].keys() == {"taken", "fallthrough"}


def test_copies_of_one_untouched_canonical_source_preserve_sparse_facts_across_a_join() -> None:
    state = analyze(".DEF SOURCE R8\n.DEF DEST R1\nand R9,5,R8\nnop\njge R7,1,@other\n"
                    "move $SOURCE,$DEST\nnop\nnop\njmp @join\nother: move R8,R1\nnop\n"
                    "join: move R6,R8\nnop\nand R1,2,R2\nnop\nadd R2,100,R2\nnop\nwait R2\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")
    step = state.register_provenance["R1"]["steps"][0]

    assert branch.meta["branch_join_proven"]
    assert state.rt_time_ns == Concrete(100)
    assert state.registers["R2"] == Concrete(100)
    assert state.register_bounds["R1"] == (0, 5)
    assert state.register_known_bits["R1"] == (0, 0xFFFFFFFA)
    assert step["branch_join_source_register"] == "R8"
    assert step["source_provenance"]["steps"][-1]["op"] == "and"
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


def test_copies_of_one_unknown_source_agree_by_identity_without_inventing_its_value() -> None:
    state = analyze("jge R7,1,@other\nmove R8,R1\nnop\njmp @join\nother: move R8,R1\nnop\njoin: wait 100\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert branch.meta["branch_rt_join_proven"]
    assert not isinstance(state.registers["R1"], Concrete)
    assert state.register_provenance["R1"]["steps"][0]["branch_join_source_register"] == "R8"
    assert state.rt_time_ns == Concrete(100)


@pytest.mark.parametrize("prefix, fallthrough_arm, taken_arm, proven", [
    ("", "move R8,R1\nnop\n", "move R9,R1\nnop\n", True),
    ("mulu16 R6,1,R8\nnop\nmulu16 R5,1,R9\nnop\n", "move R8,R1\nnop\n", "move R9,R1\nnop\n", True),
    ("", "move R8,R1\nnop\nmove 4,R8\nnop\n", "move R8,R1\nnop\nmove 4,R8\nnop\n", True),
    ("move 4,R8\nnop\n", "move 8,R8\nnop\nmove R8,R1\nnop\nmove 4,R8\nnop\n", "move R8,R1\nnop\n", True),
    ("", "move R8,R1\nnop\n", "move R8,R1\n", False),
    ("", "move R8,R8\nnop\n", "move R8,R8\nnop\n", True),
])
def test_copy_joins_use_semantic_snapshots_and_reject_unready_destinations(prefix: str, fallthrough_arm: str, taken_arm: str, proven: bool) -> None:
    state = analyze(prefix + f"jge R7,1,@other\n{fallthrough_arm}jmp @join\nother: {taken_arm}join: wait 100\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert bool(branch.meta.get("branch_rt_join_proven")) == proven
    if proven:
        assert not isinstance(state.registers.get("R1"), Concrete)


def test_copy_join_uses_the_same_analysis_register_parameter_through_direct_and_alias_sources() -> None:
    state = analyze(".DEF SOURCE R8\njge R7,1,@other\nmove $SOURCE,R1\nnop\njmp @join\n"
                    "other: move R8,R1\nnop\njoin: wait R1\nstop", params={"R8": 4})
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert branch.meta["branch_rt_join_proven"]
    assert state.rt_time_ns == Concrete(4)


@pytest.mark.parametrize("disable_at_join", [False, True])
def test_unknown_trigger_predicate_with_equal_positive_wait_costs_can_reach_a_common_time(disable_at_join: bool) -> None:
    suffix = "set_cond 0,0,0,4\nwait 40" if disable_at_join else "wait 40"
    state = analyze("set_mrk 3\nset_cond 1,1,0,100\njge R7,1,@other\nwait 100\njmp @join\n"
                    f"other: wait 100\njoin: {suffix}\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")
    region = next(event for event in state.events if event.kind == "unknown_region")

    assert branch.meta["branch_rt_join_proven"]
    assert branch.meta["join_rt_duration_ns"] == 100
    assert region.duration == Concrete(100)
    assert region.meta["conditional_timing_known"]
    assert region.meta["output_state_unknown"] is False
    assert not any(event.kind in {"play", "marker_state"} for event in state.events)
    assert "marker" in state.latched_state.pending_event_ids
    assert isinstance(state.rt_time_ns, Concrete) == disable_at_join
    assert analyze_underflow(state).status == ("not_detected_under_current_assumptions" if disable_at_join else "analysis_incomplete")


@pytest.mark.parametrize("operator, duration, else_duration", [(6, 100, 100), (7, 100, 100), (0, 100, 40), (0, 0, 4)])
def test_equal_wait_join_does_not_expand_reserved_or_different_conditional_costs(operator: int, duration: int, else_duration: int) -> None:
    state = analyze(f"set_cond 1,1,{operator},{else_duration}\njge R7,1,@other\nwait {duration}\njmp @join\n"
                    f"other: wait {duration}\njoin: set_cond 0,0,0,4\nwait 40\nstop")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert not branch.meta.get("branch_rt_join_proven")
    assert analyze_underflow(state).status == "analysis_incomplete"


def test_equal_conditional_wait_join_can_prove_an_identical_packet_supply_prefix() -> None:
    source = ("set_cond 1,1,0,100\njge R7,1,@other\n" + "nop\n" * 5 + "wait 100\njmp @join\n"
              "other: wait 100\n" + "nop\n" * 4 + "join: set_cond 0,0,0,4\nwait 40\nstop")
    state = analyze(source)
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert branch.meta["branch_join_proven"]
    assert state.rt_time_ns == Concrete(140)
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"
