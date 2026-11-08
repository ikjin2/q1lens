import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm


def analyze(source: str, **kwargs):
    return interpret_program(parse_q1asm(source, file="branch-refinement.q1asm"), sequencer_id="seq0", **kwargs)


@pytest.mark.parametrize("op,fallthrough,taken", [("jge", "R7", "0"), ("jlt", "0", "R7")])
def test_singleton_branch_arm_can_prove_a_common_result(op, fallthrough, taken):
    source = (f"{op} R7,1,@other\nmove {fallthrough},R1\nnop\njmp @join\n"
              f"other: move {taken},R1\nnop\njoin: add R1,100,R1\nnop\nwait R1\nstop\n")
    state = analyze(source)
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == op)

    assert branch.meta["branch_rt_join_proven"]
    assert state.rt_time_ns == Concrete(100)
    assert state.registers["R1"] == Concrete(100)
    assert not isinstance(state.registers.get("R7"), Concrete)
    assert "R7" not in state.register_bounds


def test_branch_refinement_resolves_the_physical_source_alias():
    state = analyze(".DEF INPUT R7\n.DEF LIMIT 1\njge $INPUT,$LIMIT,@other\nmove $INPUT,R1\nnop\njmp @join\n"
                    "other: move 0,R1\nnop\njoin: add R1,100,R1\nnop\nwait R1\nstop\n")

    assert state.rt_time_ns == Concrete(100)
    assert "R7" not in state.register_bounds


@pytest.mark.parametrize("op,fallthrough,taken", [("jge", "4294967295", "R7"), ("jlt", "R7", "4294967295")])
def test_unsigned_negative_threshold_refines_the_uint32_boundary(op, fallthrough, taken):
    source = (f"{op} R7,-1,@other\nmove {fallthrough},R1\nnop\njmp @join\n"
              f"other: move {taken},R1\nnop\njoin: not R1,R1\nnop\nadd R1,100,R1\nnop\nwait R1\nstop\n")
    state = analyze(source)

    assert state.rt_time_ns == Concrete(100)
    assert not isinstance(state.registers.get("R7"), Concrete)


def test_arm_predicate_does_not_refine_a_copy_of_an_older_source_version():
    source = ("move R7,R2\nnop\nmove R8,R7\nnop\njge R7,1,@other\nmove R2,R1\nnop\njmp @join\n"
              "other: move 0,R1\nnop\njoin: wait R1\nstop\n")
    state = analyze(source)

    assert not isinstance(state.registers["R1"], Concrete)
    assert not isinstance(state.rt_time_ns, Concrete)


def test_an_arm_overwrite_replaces_the_branch_input_fact():
    source = ("jge R7,1,@other\nmove R7,R1\nnop\njmp @join\nother: move 2,R7\nnop\nmove R7,R1\nnop\n"
              "join: wait R1\nstop\n")
    state = analyze(source)

    assert not isinstance(state.registers["R1"], Concrete)
    assert not isinstance(state.rt_time_ns, Concrete)


def test_a_pending_branch_input_does_not_authorize_an_arm_refinement():
    source = ("move 0,R7\njge R7,1,@other\nmove R7,R1\nnop\njmp @join\nother: move 0,R1\nnop\n"
              "join: wait R1\nstop\n")
    state = analyze(source)
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert any(item.category == "register_not_ready" for item in state.diagnostics)
    assert not branch.meta.get("branch_rt_join_proven")


def test_explicit_compare_keeps_its_existing_path_exploration_behavior():
    source = ("jge R7,1,@other\nmove R7,R1\nnop\njmp @join\nother: move 0,R1\nnop\n"
              "join: wait R1\nstop\n")
    state = analyze(source, branch_policy="explore_both_with_depth_limit")
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jge")

    assert branch.meta["branch_taken"] == "both"
    assert not branch.meta.get("branch_rt_join_proven")
    assert any(write.meta.get("branch_comparison_path") == "fallthrough" and not isinstance(write.value, Concrete)
               for write in state.register_history if write.register == "R1")


def test_unmodified_branch_input_keeps_its_existing_bounds_after_the_join():
    source = ("and R8,7,R7\nnop\njge R7,1,@other\nmove R7,R1\nnop\njmp @join\nother: move 0,R1\nnop\n"
              "join: add R1,100,R1\nnop\nwait R1\nstop\n")
    state = analyze(source)

    assert state.rt_time_ns == Concrete(100)
    assert state.register_bounds["R7"] == (0, 7)
    assert not isinstance(state.registers["R7"], Concrete)


def test_a_symbolic_threshold_does_not_authorize_singleton_refinement():
    source = (".DEF LIMIT {LIMIT}\njge R7,$LIMIT,@other\nmove R7,R1\nnop\njmp @join\nother: move 0,R1\nnop\n"
              "join: add R1,100,R1\nnop\nwait R1\nstop\n")
    state = analyze(source)

    assert not isinstance(state.rt_time_ns, Concrete)
    assert not isinstance(state.registers["R1"], Concrete)


def test_explicit_dollar_threshold_binding_keeps_its_actual_lookup_precedence():
    source = (".DEF LIMIT 1\njge R7,$LIMIT,@other\nmove R7,R1\nnop\njmp @join\nother: move 0,R1\nnop\n"
              "join: add R1,100,R1\nnop\nwait R1\nstop\n")
    state = analyze(source, params={"$LIMIT": 2})

    assert not isinstance(state.rt_time_ns, Concrete)
    assert not isinstance(state.registers["R1"], Concrete)


def test_register_shaped_immutable_parameter_does_not_refine_a_hardware_register():
    source = ("move R8,R7\nnop\njge $R7,1,@other\nmove R7,R1\nnop\njmp @join\nother: move 0,R1\nnop\n"
              "join: wait R1\nstop\n")
    state = analyze(source, params={"R7": "immutable_input"})

    assert any(item.severity == "error" for item in state.diagnostics)
    assert not any(event.meta.get("branch_rt_join_proven") for event in state.events)
    assert state.register_bounds.get("R7", (0, 0xFFFFFFFF)) == (0, 0xFFFFFFFF)


def test_join_does_not_publish_a_zero_flag_that_disagrees_between_arms():
    source = ("move 2,R3\nnop\njge R7,1,@other\nmove 4,R1\nnop\njmp @join\n"
              "other: sub R3,2,R2\nmove 4,R1\nnop\njoin: jz @maybe\nwait 100\nstop\nmaybe: wait 200\nstop\n")
    state = analyze(source)
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jz")

    assert state.registers["R1"] == Concrete(4)
    assert branch.meta["branch_decision"] == "runtime_dependent"


@pytest.mark.parametrize("prefix,copy", [("move R7,R8", "move R8,R1"), ("not R7,R8", "not R8,R1")])
def test_branch_predicate_refines_readable_same_snapshot_and_complement_peers(prefix, copy):
    source = (f"{prefix}\nnop\njge R7,1,@other\n{copy}\nnop\njmp @join\nother: move 0,R1\nnop\n"
              "join: add R1,100,R1\nnop\nwait R1\nstop\n")
    state = analyze(source)

    assert state.rt_time_ns == Concrete(100)
    assert not isinstance(state.registers.get("R7"), Concrete)
    assert not isinstance(state.registers["R8"], Concrete)
    assert state.register_bounds["R8"] == (0, 0xFFFFFFFF)


def test_unmodified_refined_peer_stays_unconstrained_at_a_later_global_branch():
    source = ("move R7,R8\nnop\njge R7,1,@other\nmove R8,R1\nnop\njmp @join\nother: move 0,R1\nnop\n"
              "join: jlt R8,1,@maybe\nwait 100\nstop\nmaybe: wait 200\nstop\n")
    state = analyze(source)
    branch = next(event for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "jlt")

    assert branch.meta["branch_decision"] == "runtime_dependent"


def test_overwriting_a_refined_peer_replaces_its_branch_local_fact():
    source = ("move R7,R8\nnop\njge R7,1,@other\nmove R9,R8\nnop\nmove R8,R1\nnop\njmp @join\n"
              "other: move 0,R1\nnop\njoin: wait R1\nstop\n")
    state = analyze(source)

    assert not isinstance(state.registers["R1"], Concrete)
    assert not isinstance(state.rt_time_ns, Concrete)
