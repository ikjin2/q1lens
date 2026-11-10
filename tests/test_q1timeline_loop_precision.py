import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.underflow import analyze_underflow
from q1timeline.analysis.values import Concrete, Unknown
from q1timeline.playback import PlaybackSession
from q1timeline.q1asm.parser import parse_q1asm


def analyze(source: str, **kwargs):
    return interpret_program(parse_q1asm(source, file="loop-precision.q1asm"), sequencer_id="seq0", **kwargs)


@pytest.mark.parametrize("target,setup,compiled_target", [("@end", "", 5), ("5", "", 5), ("R1", "move 6,R1\n", 6)])
@pytest.mark.parametrize("count,remaining,waits,loop_ns", [
    (2, 1, [4], 24), (1, 0, [20, 4], 4), (0, 0xFFFFFFFF, [4], 24),
])
def test_resolved_forward_loop_matches_concrete_decrement_timing_and_target(target, setup, compiled_target,
                                                                          count, remaining, waits, loop_ns):
    source = f"move {count},R0\n{setup}nop\nloop R0,{target}\nwait 20\nend: wait 4\nstop\n"
    analysis = analyze(source)
    concrete = analyze(source, execution_mode="concrete")

    for state in (analysis, concrete):
        loop, = [event for event in state.events if event.kind == "q1_issue" and event.meta["op"] == "loop"]
        write, = [write for write in state.register_history if write.event_id == loop.id]
        assert state.registers["R0"] == write.value == Concrete(remaining)
        assert write.time == loop.t1 and loop.duration == Concrete(loop_ns)
        assert loop.meta["branch_taken"] is (remaining != 0)
        assert loop.meta["compiled_target_pc"] == compiled_target
        assert loop.meta["target_pc"] == compiled_target - 1
        assert [event.duration.value for event in state.events if event.kind == "wait"] == waits
        assert state.rt_time_ns == Concrete(sum(waits))
        assert state.alu_flags == {"zf": count == 1, "nf": count == 0, "cf": count == 0, "of": False}
        assert not state.diagnostics
    assert analysis.q1_time_ns == concrete.q1_time_ns


@pytest.mark.parametrize("setup,target", [
    ("nop\n", "@end"), ("nop\n", "4"), ("move 5,R1\nnop\n", "R1"),
    ("move 2,R0\nnop\nmove 1,R0\n", "@end"),
])
def test_forward_loop_with_unknown_or_hazardous_operands_stays_unresolved(setup, target):
    state = analyze(f"{setup}loop R0,{target}\nwait 20\nend: wait 4\nstop\n")

    loop, = [event for event in state.events if event.kind == "q1_issue" and event.meta["op"] == "loop"]
    assert loop.meta["branch_taken"] == "runtime_dependent"
    assert any(item.category == "analysis_incomplete" for item in state.diagnostics)
    assert not any(event.kind == "wait" for event in state.events)
    assert not any(write.meta.get("op") == "loop" for write in state.register_history)


@pytest.mark.parametrize("target", ["3", "99", "R1"])
def test_forward_loop_requires_a_supported_target_boundary(target):
    state = analyze(f"move 2,R0\nnop\nloop R0,{target}\nwait 20\nend: wait 4\nstop\n")

    assert any(item.category in {"analysis_incomplete", "unresolved_branch"} for item in state.diagnostics)
    assert not any(event.kind == "wait" for event in state.events)
    assert not any(write.meta.get("op") == "loop" for write in state.register_history)


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


@pytest.mark.parametrize("duration", ["R0", "$OFFSET", "{R0}", "R1", "$VALUE", "{R1}"])
def test_affine_varying_loop_durations_have_proven_totals_without_copied_previews(duration):
    source = (f".DEF OFFSET R0\n.DEF VALUE R1\nmove 3,R2\nmove 7,R0\nmove 100,R1\nnop\n"
              f"body: wait {duration}\nadd R1,4,R1\nsub R0,1,R0\nnop\nloop R2,@body\nwait 40\nstop\n")
    state = analyze(source)

    concrete = analyze(source, execution_mode="concrete")
    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(58 if duration in {"R0", "$OFFSET", "{R0}"} else 352)
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
    assert state.rt_time_ns == Concrete(16)


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
    assert state.rt_time_ns == Concrete(16)


def analyze_branch_loop(source):
    lines = source.splitlines()
    start = next(index for index, line in enumerate(lines, 1) if line.startswith("body:"))
    end = next(index for index, line in enumerate(lines, 1) if line.startswith("tail:"))
    return analyze(source, loop_preview_counts={f"seq0:loop:loop-precision.q1asm:{start}:{end}": 4})


@pytest.mark.parametrize("branch,first_taken,wait,total", [
    ("jge R1,5,@tail", True, 8, 44),
    ("cmp R1,5\njge @tail", True, 8, 44),
    ("jlt R1,5,@tail", False, 8, 28),
    ("cmp R1,5\njl @tail", False, 0, 24),
])
def test_compact_loop_sums_changing_direct_and_status_branch_paths(branch, first_taken, wait, total):
    source = ("move 4,R0\nmove 8,R1\nnop\nbody: wait 4\nsub R1,2,R1\nnop\n"
              f"{branch}\nwait {wait}\ntail: loop R0,@body\nwait 4\nstop")
    analysis = analyze_branch_loop(source)
    concrete = analyze(source, execution_mode="concrete")
    playback = PlaybackSession({"seq0": ("loop-precision.q1asm", source)})
    assert playback.run(budget_ms=1000, max_steps=1000)["status"] == "completed"

    assert analysis.rt_time_ns == concrete.rt_time_ns == playback.runners[0].state.rt_time_ns == Concrete(total)
    assert analysis.q1_time_ns == concrete.q1_time_ns == playback.runners[0].state.q1_time_ns
    assert analysis.registers == concrete.registers == {"R0": Concrete(0), "R1": Concrete(0)}
    assert analysis.alu_flags == concrete.alu_flags
    assert analysis.rt_packets[-1].rt_t0 == concrete.rt_packets[-1].rt_t0 == Concrete(total - 4)
    loop, = [event for event in analysis.events if event.kind == "loop_block"]
    assert loop.meta["shown_iterations"] == [0]
    assert loop.meta["period_invariant"] is False
    issue, = [event for event in analysis.events if event.kind == "q1_issue"
              and event.meta.get("op") == branch.splitlines()[-1].split()[0]]
    assert issue.meta["branch_taken"] is first_taken
    assert analyze_underflow(analysis).status == "analysis_incomplete"


def test_hidden_status_branch_reads_flags_from_preceding_loop_edge():
    source = ("move 4,R0\nmove 0,R1\nnop\ncmp R1,0\nbody: wait 4\njz @tail\nwait 8\n"
              "tail: loop R0,@body\nwait 4\nstop")
    analysis = analyze_branch_loop(source)
    concrete = analyze(source, execution_mode="concrete")

    assert analysis.rt_time_ns == concrete.rt_time_ns == Concrete(44)
    assert analysis.q1_time_ns == concrete.q1_time_ns
    loop, = [event for event in analysis.events if event.kind == "loop_block"]
    assert loop.meta["iteration_durations"] == [4, 12, 12, 12]
    assert loop.meta["shown_iterations"] == [0]


def test_loop_counter_can_change_layout_without_changing_rt_period():
    source = ("move 4,R0\nnop\nbody: wait 4\njge R0,4,@fast\nwait 8\njmp @tail\nfast: wait 4\nwait 4\n"
              "tail: loop R0,@body\nwait 4\nstop")
    analysis = analyze_branch_loop(source)
    concrete = analyze(source, execution_mode="concrete")

    assert analysis.rt_time_ns == concrete.rt_time_ns == Concrete(52)
    assert analysis.q1_time_ns == concrete.q1_time_ns
    loop, = [event for event in analysis.events if event.kind == "loop_block"]
    assert loop.meta["period_invariant"] is True
    assert loop.meta["shown_iterations"] == [0]


def test_invariant_branch_keeps_requested_loop_previews():
    source = ("move 4,R0\nmove 8,R1\nnop\nbody: wait 4\nmove R1,R2\nnop\njge R2,5,@tail\nwait 8\n"
              "tail: loop R0,@body\nwait 4\nstop")
    analysis = analyze_branch_loop(source)
    concrete = analyze(source, execution_mode="concrete")

    assert analysis.rt_time_ns == concrete.rt_time_ns == Concrete(20)
    assert analysis.q1_time_ns == concrete.q1_time_ns
    loop, = [event for event in analysis.events if event.kind == "loop_block"]
    assert loop.meta["shown_iterations"] == [0, 1, 2, 3]
    assert loop.meta["period_invariant"] is True
    issues = [event for event in analysis.events if event.kind == "q1_issue" and event.meta.get("op") == "jge"]
    assert [event.t0 for event in issues] == [event.t0 for event in concrete.events
                                               if event.kind == "q1_issue" and event.meta.get("op") == "jge"]
    assert all(event.meta["branch_taken"] is True for event in issues)


@pytest.mark.parametrize("count,write", [(4, "move R7,R1"), (2001, "sub R1,2,R1")])
def test_unproved_hidden_branch_keeps_only_observed_timing_and_values(count, write):
    source = (f"move {count},R0\nmove 8,R1\nnop\nbody: wait 4\njge R1,5,@write\nwait 8\nwrite: {write}\nnop\n"
              "tail: loop R0,@body\nwait 4\nstop")
    state = analyze_branch_loop(source)

    assert isinstance(state.rt_time_ns, Unknown)
    assert isinstance(state.q1_time_ns, Unknown)
    assert isinstance(state.registers["R0"], Unknown)
    assert isinstance(state.registers["R1"], Unknown)
    loop, = [event for event in state.events if event.kind == "loop_block"]
    assert loop.meta["shown_iterations"] == [0]
    assert loop.meta["period"]["kind"] == "unknown"
    assert state.rt_packets[0].rt_t0 == Concrete(0)
    assert isinstance(state.rt_packets[-1].rt_t0, Unknown)


@pytest.mark.parametrize("branch,body", [
    ("jlt R0,4,@escape", "wait 8"),
    ("jge R0,4,@tail", "set_mrk 3"),
])
def test_unproved_branch_may_prevent_a_modeled_loop_fallthrough(branch, body):
    source = (f"move 4,R0\nnop\nbody: wait 4\n{branch}\n{body}\ntail: loop R0,@body\n"
              "upd_param 4\nstop\nescape: wait 40\nstop")
    state = analyze_branch_loop(source)

    assert isinstance(state.rt_time_ns, Unknown)
    assert isinstance(state.q1_time_ns, Unknown)
    assert [packet.op for packet in state.rt_packets] == (["wait", "wait"] if body.startswith("wait") else ["wait"])
    assert not any(event.kind == "stop" for event in state.events)


def test_assumed_feedback_retry_keeps_a_nominal_fallthrough():
    state = analyze("move 3,R0\nbody: wait 4\nfb_pop_data 40,R1\nnop\njge R1,1,@accepted\n"
                    "loop R0,@body\nset_mrk 0\nupd_param 4\nstop\naccepted: set_mrk 1\nstop",
                    branch_policy="assume_fallthrough")

    loop, = [event for event in state.events if event.kind == "loop_block"]
    assert loop.meta["fallthrough_assumed"] is True
    assert loop.meta["period"]["kind"] == "unknown"
    assert loop.meta["shown_iterations"] == [0]
    pending, = [event for event in state.events if event.kind == "latched_state_pending"]
    assert pending.meta["value"] == 0 and isinstance(pending.t0, Unknown)
    assert isinstance(state.rt_time_ns, Unknown) and isinstance(state.q1_time_ns, Unknown)
    assert isinstance(state.registers["R1"], Unknown)


def test_unproved_forever_branch_keeps_the_observed_first_period():
    state = analyze("move 8,R1\nnop\nbody: wait 4\nsub R1,2,R1\nnop\njge R1,5,@tail\nwait 8\ntail: jmp @body",
                    loop_preview_counts={"seq0:loop:loop-precision.q1asm:3:8": 4})

    loop, = [event for event in state.events if event.kind == "loop_block"]
    assert loop.duration == Concrete(4) and loop.meta["period"]["value"] == 4
    assert loop.meta["period_basis"] == "first_iteration"
    assert loop.meta["period_invariant"] is False
    assert loop.meta["shown_iterations"] == [0]
    assert "total_duration" not in loop.meta
