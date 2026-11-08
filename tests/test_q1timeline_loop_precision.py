import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.underflow import analyze_underflow
from q1timeline.analysis.values import Concrete, Unknown
from q1timeline.q1asm.parser import parse_q1asm


def analyze(source: str, **kwargs):
    return interpret_program(parse_q1asm(source, file="loop-precision.q1asm"), sequencer_id="seq0", **kwargs)


@pytest.mark.parametrize("count", [2, 3, 10])
@pytest.mark.parametrize("body", ["wait 100", "add R1,1,R1\nnop\nwait 100"])
def test_stable_finite_loop_final_decrement_resolves_status_branch(count, body):
    source = (f"move {count},R0\nmove 0,R1\nnop\nbody: {body}\nloop R0,@body\n"
              "jz @done\nwait 200\nstop\ndone: wait 100\nstop\n")
    analysis = analyze(source)
    concrete = analyze(source, execution_mode="concrete")

    assert analysis.rt_time_ns == concrete.rt_time_ns == Concrete((count + 1) * 100)
    assert analysis.alu_flags == concrete.alu_flags == {"zf": True, "nf": False, "cf": False, "of": False}
    assert not any(item.category == "unresolved_branch" for item in analysis.diagnostics)


@pytest.mark.parametrize("write", ["move 2,R0", "move 2,$COUNTER", "add R0,1,R0", "mulu32 R1,4294967295,R2,$COUNTER"])
def test_hidden_counter_writes_do_not_claim_finite_completion(write):
    source = (f".DEF COUNTER R0\nmove 3,R0\nmove 100,R1\nnop\nbody: {write}\nnop\n"
              "wait 100\nloop R0,@body\nwait 40\nstop\n")
    state = analyze(source)

    assert isinstance(state.registers["R0"], Unknown)
    assert isinstance(state.rt_time_ns, Unknown)
    assert isinstance(state.q1_time_ns, Unknown)
    assert state.alu_flags is None
    assert analyze_underflow(state).status == "analysis_incomplete"


def test_a_counter_write_that_exits_on_the_first_edge_remains_exact():
    source = "move 3,R0\nbody: move 1,R0\nnop\nwait 100\nloop R0,@body\nwait 40\nstop\n"
    analysis = analyze(source)
    concrete = analyze(source, execution_mode="concrete")

    assert analysis.rt_time_ns == concrete.rt_time_ns == Concrete(140)
    assert analysis.registers["R0"] == Concrete(0)
    assert analyze_underflow(analysis).status == "not_detected_under_current_assumptions"


@pytest.mark.parametrize("body", [
    "move 100,R1\nnop\nwait R1",
    "move 50,R2\nnop\nadd R2,50,R1\nnop\nwait R1",
    "move 100,R1\nnop\nwait R1\nadd R1,4,R1\nnop",
])
def test_reset_before_each_read_proves_repeated_duration_and_final_value(body):
    source = f"move 3,R0\nmove 8,R1\nnop\nbody: {body}\nloop R0,@body\nwait R1\nstop\n"
    analysis = analyze(source)
    concrete = analyze(source, execution_mode="concrete")

    assert analysis.rt_time_ns == concrete.rt_time_ns
    assert analysis.registers["R1"] == concrete.registers["R1"]
    assert analyze_underflow(analysis).status == "not_detected_under_current_assumptions"


@pytest.mark.parametrize("duration", ["R0", "$COUNTER", "{R0}", "R1", "$VALUE", "{R1}"])
def test_affine_varying_loop_durations_have_proven_totals_without_copied_previews(duration):
    source = (f".DEF COUNTER R0\n.DEF VALUE R1\nmove 3,R0\nmove 100,R1\nnop\n"
              f"body: wait {duration}\nadd R1,4,R1\nnop\nloop R0,@body\nwait 40\nstop\n")
    state = analyze(source)

    concrete = analyze(source, execution_mode="concrete")
    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(46 if duration in {"R0", "$COUNTER", "{R0}"} else 352)
    loop = next(event for event in state.events if event.kind == "loop_block")
    assert isinstance(loop.duration, Concrete)
    assert loop.confidence == "exact"
    assert not loop.meta["period_invariant"]
    assert loop.meta["visible_iteration_count"] == 1
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


def test_reset_after_read_preserves_the_distinct_startup_iteration():
    state = analyze("move 3,R0\nmove 8,R1\nnop\nbody: wait R1\nmove 100,R1\nnop\nloop R0,@body\nwait R1\nstop\n")

    assert state.registers["R1"] == Concrete(100)
    assert state.rt_time_ns == Concrete(308)
    loop = next(event for event in state.events if event.kind == "loop_block")
    assert loop.meta["iteration_durations"] == [8, 100]
    assert loop.meta["repeat_last_iteration"]


def test_reset_condition_controls_and_skipped_nominal_duration_are_loop_invariant():
    state = analyze("move 3,R0\nmove 1,R1\nmove 100,R2\nmove 1,R3\nmove 0,R4\nnop\nbody: move 0,R1\nnop\n"
                    "set_cond R3,R1,R4,20\nwait R2\nadd R2,4,R2\nnop\nloop R0,@body\n"
                    "set_cond 0,0,0,4\nupd_param 4\nstop\n")

    assert state.rt_time_ns == Concrete(64)
    assert not state.conditional_time_unknown


def test_invariant_final_value_is_restored_only_after_unknown_loop_history():
    state = analyze("move 3,R0\nmove 8,R1\nnop\nbody: move 100,R1\nnop\nwait R1\nloop R0,@body\nstop\n")
    writes = [write for write in state.register_history if write.register == "R1"]

    assert any(write.meta.get("invalidated") for write in writes)
    assert writes[-1].value == Concrete(100)
    assert writes[-1].meta["invariant"]


def test_pending_reset_does_not_prove_the_stale_first_duration_repeats():
    state = analyze("move 20,R1\nmove 3,R0\nnop\nbody: move 100,R1\nwait R1\nloop R0,@body\nstop\n")

    assert any(item.category == "register_not_ready" for item in state.diagnostics)
    assert isinstance(state.rt_time_ns, Unknown)
    assert analyze_underflow(state).status == "analysis_incomplete"


def test_pending_invariant_source_does_not_prove_a_derived_final_value():
    state = analyze("move 20,R1\nmove 3,R0\nnop\nbody: move 100,R1\nmove R1,R2\nnop\n"
                    "wait 100\nloop R0,@body\nwait R2\nstop\n")

    assert any(item.category == "register_not_ready" for item in state.diagnostics)
    assert isinstance(state.registers["R2"], Unknown)
    assert isinstance(state.rt_time_ns, Unknown)


def test_entry_pending_write_cannot_make_a_stale_first_wait_invariant():
    state = analyze("move 20,R1\nmove 3,R0\nnop\nmove 100,R1\nbody: wait R1\nloop R0,@body\nstop\n")

    assert any(item.category == "register_not_ready" for item in state.diagnostics)
    assert isinstance(state.rt_time_ns, Unknown)
    assert analyze_underflow(state).status == "analysis_incomplete"


def test_entry_pending_setter_operand_remains_unknown_across_hidden_iterations():
    state = analyze("move 0,R1\nmove 3,R0\nnop\nmove 3,R1\nbody: set_mrk R1\nupd_param 4\nloop R0,@body\nupd_param 4\nstop\n")

    assert any(item.category == "register_not_ready" for item in state.diagnostics)
    assert isinstance(state.latched_state.marker, Unknown)


def test_invariant_final_value_restores_sparse_bit_facts():
    state = analyze("move 3,R0\nbody: and R7,5,R1\nnop\nwait 4\nloop R0,@body\n"
                    "and R1,2,R2\nnop\nwait R2\nstop\n")

    assert state.registers["R2"] == Concrete(0)
    assert state.rt_time_ns == Concrete(12)


def test_forever_loop_keeps_observed_first_period_without_extrapolating_it():
    state = analyze("move 4,R1\nnop\nbody: wait R1\nadd R1,4,R1\nnop\njmp @body\n")
    loop = next(event for event in state.events if event.kind == "loop_block")

    assert loop.duration == Concrete(4)
    assert loop.meta["period_invariant"] is False
    assert loop.meta["shown_iterations"] == [0]
    assert analyze_underflow(state).status == "analysis_incomplete"


def test_an_unknown_first_wait_is_not_reported_as_a_zero_length_loop():
    state = analyze("move 3,R0\nbody: wait R7\nloop R0,@body\nstop\n")
    loop = next(event for event in state.events if event.kind == "loop_block")

    assert isinstance(loop.duration, Unknown)
    assert isinstance(state.rt_time_ns, Unknown)


def test_invariant_final_copy_restores_its_value_origin():
    state = analyze("move 3,R0\nbody: move R7,R1\nnop\nwait 4\nloop R0,@body\n"
                    "xor R7,R1,R2\nnop\nwait R2\nstop\n")

    assert state.registers["R2"] == Concrete(0)
    assert state.rt_time_ns == Concrete(12)
