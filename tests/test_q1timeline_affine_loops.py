import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.queue_timing import replay_queues
from q1timeline.analysis.underflow import _queue_packets, analyze_underflow
from q1timeline.analysis.values import Concrete, Unknown
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


def analyze(source: str, **kwargs):
    return interpret_program(parse_q1asm(source, file="affine.q1asm"), sequencer_id="seq0", **kwargs)


def compensated_source(*, initial=40, step=4, count=3, suffix="stop", ready=True):
    return (f".DEF VALUE R1\nmove {initial},R1\nmove 100,R2\nmove {count},R0\n" + ("nop\n" if ready else "")
            + f"body: wait $VALUE\nsub R2,R1,R3\nnop\nwait R3\nadd R1,{step},R1\nnop\nloop R0,@body\n{suffix}\n")


@pytest.mark.parametrize("masked_each_iteration", [False, True])
def test_bounded_unknown_waits_can_prove_their_canceling_total_without_packet_times(masked_each_iteration):
    positive_wait = "and R7,15,R1\nnop\nadd R1,4,R1\nnop\n"
    prefix = "" if masked_each_iteration else positive_wait
    body = positive_wait if masked_each_iteration else ""
    changed = "R7" if masked_each_iteration else "R1"
    source = (prefix + "move 100,R2\nmove 3,R0\nnop\nbody: " + body
              + f"wait R1\nsub R2,R1,R3\nnop\nwait R3\nadd {changed},4,{changed}\nnop\nloop R0,@body\nwait 8\nstop")
    state = analyze(source)
    loop = next(event for event in state.events if event.kind == "loop_block")

    assert state.rt_time_ns == Concrete(308)
    assert not isinstance(state.registers[changed], Concrete)
    assert loop.meta["total_only"]
    assert loop.meta["total_duration"]["value"] == 300
    assert loop.meta["period_affine"] == {"intercept": 100, "slope": 0}
    assert loop.meta["visible_iteration_count"] == 1
    assert "rt_packet_affine" not in loop.meta and "rt_packet_durations" not in loop.meta
    assert not isinstance(state.rt_packets[0].duration, Concrete)
    assert analyze_underflow(state).status == "analysis_incomplete"
    for seed in (0, 1, 7, 15, 0x80000000, 0xFFFFFFFF):
        concrete = analyze(f"move {seed},R7\nnop\n" + source, execution_mode="concrete")
        assert concrete.rt_time_ns == state.rt_time_ns


def test_a_canceling_total_can_depend_on_the_known_iteration_counter():
    source = ("move 3,R0\nmove 100,R2\nnop\nbody: and R7,15,R1\nnop\nadd R1,4,R1\nnop\nwait R1\n"
              "sub R2,R1,R3\nnop\nadd R3,R0,R3\nnop\nwait R3\nadd R7,4,R7\nnop\nloop R0,@body\nstop")
    state = analyze(source)
    loop = next(event for event in state.events if event.kind == "loop_block")

    assert state.rt_time_ns == Concrete(306)
    assert loop.meta["period_affine"] == {"intercept": 103, "slope": -1}
    assert not loop.meta["period_invariant"]
    assert analyze_underflow(state).status == "analysis_incomplete"


@pytest.mark.parametrize("change", [
    "sub R2,R8,R3",  # Independent words cannot cancel just because both are unknown.
    "sub R1,R2,R3",  # Word subtraction is not integer cancellation after underflow.
])
def test_total_only_loop_proofs_reject_independent_inputs_and_modular_cancellation(change):
    state = analyze("and R7,15,R1\nand R9,15,R8\nmove 100,R2\nmove 3,R0\nnop\n"
                    f"body: wait R1\n{change}\nnop\nwait R3\nadd R1,4,R1\nnop\nloop R0,@body\nstop")

    assert not isinstance(state.rt_time_ns, Concrete)
    assert not next(event for event in state.events if event.kind == "loop_block").meta.get("total_only")


def test_total_only_proof_does_not_recover_an_unknown_loop_entry_time():
    state = analyze("wait R9\nand R7,15,R1\nnop\nadd R1,4,R1\nmove 100,R2\nmove 3,R0\nnop\n"
                    "body: wait R1\nsub R2,R1,R3\nnop\nwait R3\nadd R1,4,R1\nnop\nloop R0,@body\nstop")
    loop = next(event for event in state.events if event.kind == "loop_block")

    assert loop.meta["total_duration"]["value"] == 300
    assert not isinstance(loop.t0, Concrete)
    assert not isinstance(state.rt_time_ns, Concrete)


def test_bounded_canceling_sum_cannot_ignore_a_possible_zero_wait():
    source = ("and R7,15,R1\nnop\nmove 100,R2\nmove 3,R0\nnop\n"
              "body: wait R1\nsub R2,R1,R3\nnop\nwait R3\nadd R1,4,R1\nnop\nloop R0,@body\nstop")
    state = analyze(source)
    loop, = [event for event in state.events if event.kind == "loop_block"]
    assert isinstance(state.rt_time_ns, Unknown)
    assert "total_duration" not in loop.meta
    assert [analyze(f"move {seed},R7\nnop\n" + source, execution_mode="concrete").rt_time_ns.value
            for seed in (0, 4)] == [304, 300]


def test_bounded_total_rejects_a_later_invalid_wait_even_when_the_formal_sum_cancels():
    state = analyze("and R7,15,R1\nnop\nmove 20,R2\nmove 5,R0\nnop\n"
                    "body: wait R1\nsub R2,R1,R3\nnop\nwait R3\nadd R1,4,R1\nnop\nloop R0,@body\nstop")

    assert not isinstance(state.rt_time_ns, Concrete)
    assert not next(event for event in state.events if event.kind == "loop_block").meta.get("total_only")


def test_bounded_total_cannot_hide_a_future_unsupported_shift_behind_masked_cancellation():
    source = ("move 3,R0\nmove 2,R1\nmove 1,R7\nmove 100,R2\nbody: lsl R7,R1,R4\nnop\n"
              "and R4,15,R4\nnop\nwait R4\nsub R2,R4,R3\nnop\nwait R3\nadd R1,20,R1\nnop\nloop R0,@body\nstop")
    state = analyze(source)
    concrete = analyze(source, execution_mode="concrete")

    assert not isinstance(state.rt_time_ns, Concrete)
    assert not next(event for event in state.events if event.kind == "loop_block").meta.get("total_only")
    assert concrete.metadata["execution"]["status"] == "blocked"
    assert concrete.rt_time_ns == Concrete(204)


@pytest.mark.parametrize("initial,step,count", [(40, 4, 3), (60, -4, 5), (80, -1, 4), (8, 4, 10)])
def test_compensating_affine_waits_have_exact_constant_periods_and_ready_final_registers(initial, step, count):
    source = compensated_source(initial=initial, step=step, count=count, suffix="wait R1\nstop")
    state = analyze(source)
    concrete = analyze(source, execution_mode="concrete")
    packets, reason, _ = _queue_packets(state)
    loop = next(event for event in state.events if event.kind == "loop_block")

    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(count * 100 + initial + count * step)
    assert state.registers["R1"] == concrete.registers["R1"] == Concrete(initial + count * step)
    assert state.registers["R3"] == concrete.registers["R3"] == Concrete(100 - initial - (count - 1) * step)
    assert loop.meta["period_affine"] == {"intercept": 100, "slope": 0}
    assert loop.meta["total_duration"] == {"kind": "concrete", "value": count * 100, "display": str(count * 100)}
    assert loop.meta["period_invariant"]
    assert loop.meta["visible_iteration_count"] == 1
    assert reason is None
    assert [(packet.duration, packet.q1_issue_t0, packet.q1_issue_t1, packet.rt_t0, packet.rt_t1) for packet in packets] == [
        (packet.duration, packet.q1_issue_t0, packet.q1_issue_t1, packet.rt_t0, packet.rt_t1) for packet in concrete.rt_packets]
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


def test_affine_wait_durations_match_the_declared_simulator_operand_snapshots():
    source = compensated_source()
    state = analyze(source)
    packets, reason, _ = _queue_packets(state)
    simulated, = simulate_programs({"seq0": parse_q1asm(source)}, simulation={"startup_delay_ns": 200})

    assert reason is None
    assert [packet.duration for packet in packets] == [packet.duration for packet in simulated.rt_packets] == [
        Concrete(value) for value in (40, 60, 44, 56, 48, 52)]
    assert not any(item.severity == "error" for item in simulated.diagnostics)


@pytest.mark.parametrize("body,first,step", [
    ("wait R1\nadd R1,4,R1\nnop", 40, 4),
    ("add R1,4,R1\nnop\nwait R1", 44, 4),
    ("add R1,4,R1\nnop\nmove R1,R3\nnop\nwait R3", 44, 4),
    ("wait R1\nsub R1,4,R1\nnop", 40, -4),
])
def test_affine_changing_periods_use_the_sum_of_actual_periods(body, first, step):
    source = f"move 3,R0\nmove 40,R1\nnop\nbody: {body}\nloop R0,@body\nwait 8\nstop\n"
    state = analyze(source, loop_preview_counts={"seq0:loop:affine.q1asm:4:7": 3})
    concrete = analyze(source, execution_mode="concrete")
    packets, reason, _ = _queue_packets(state)
    loop = next(event for event in state.events if event.kind == "loop_block")

    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(3 * first + 3 * step + 8)
    assert loop.meta["period_affine"] == {"intercept": first, "slope": step}
    assert not loop.meta["period_invariant"]
    assert loop.meta["visible_iteration_count"] == 1
    assert reason is None
    assert [packet.duration for packet in packets] == [packet.duration for packet in concrete.rt_packets]
    assert [packet.rt_t0 for packet in packets] == [packet.rt_t0 for packet in concrete.rt_packets]


def test_affine_proof_restores_a_final_value_even_when_no_duration_in_the_body_reads_it():
    source = "move 3,R0\nmove 40,R1\nnop\nbody: wait 100\nadd R1,4,R1\nnop\nloop R0,@body\nwait R1\nstop\n"
    state = analyze(source)
    concrete = analyze(source, execution_mode="concrete")

    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(352)
    assert state.registers["R1"] == Concrete(52)
    assert next(write for write in reversed(state.register_history) if write.register == "R1").meta["affine"]
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


def test_pending_counter_at_entry_is_safe_when_only_the_later_real_loop_edge_reads_it():
    state = analyze(compensated_source(ready=False))

    assert state.rt_time_ns == Concrete(300)
    assert next(event for event in state.events if event.kind == "loop_block").meta["total_duration"]["value"] == 300


def test_pending_counter_at_entry_can_be_read_after_it_is_ready_inside_the_body():
    source = "move 3,R0\nbody: nop\nadd R0,4,R1\nnop\nwait R1\nloop R0,@body\nstop\n"
    state = analyze(source)
    concrete = analyze(source, execution_mode="concrete")
    packets, reason, _ = _queue_packets(state)

    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(18)
    assert reason is None
    assert [packet.duration for packet in packets] == [Concrete(7), Concrete(6), Concrete(5)]
    assert not any(item.category == "register_not_ready" for item in state.diagnostics)


@pytest.mark.parametrize("duration", ["R1", "$VALUE", "{R1}"])
def test_affine_live_in_can_mature_before_its_first_ready_body_read(duration):
    source = (".DEF VALUE R1\nmove 3,R0\nmove 40,R1\n"
              f"body: nop\nwait {duration}\nadd R1,4,R1\nnop\nloop R0,@body\nstop\n")
    state = analyze(source)
    concrete = analyze(source, execution_mode="concrete")

    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(132)
    assert state.registers["R1"] == Concrete(52)
    assert not any(item.category == "register_not_ready" for item in state.diagnostics)
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


def test_immutable_entry_input_can_mature_before_all_of_its_equal_ready_reads():
    source = ("move 3,R0\nmove 40,R1\nmove 100,R2\nbody: nop\nwait R1\nsub R2,R1,R3\nnop\n"
              "wait R3\nadd R1,4,R1\nnop\nloop R0,@body\nstop\n")
    state = analyze(source)

    assert state.rt_time_ns == Concrete(300)
    assert state.registers["R1"] == Concrete(52)


@pytest.mark.parametrize("initial,step,count", [(65532, 4, 3), (4, -4, 3), (99, 1, 3)])
def test_later_invalid_duration_or_wrap_quotient_change_does_not_get_an_affine_proof(initial, step, count):
    state = analyze(compensated_source(initial=initial, step=step, count=count))
    loop = next(event for event in state.events if event.kind == "loop_block")

    assert "period_affine" not in loop.meta
    assert isinstance(state.rt_time_ns, Unknown)
    assert analyze_underflow(state).status == "analysis_incomplete"


def test_word_wrap_uses_bounded_evaluation_when_an_affine_interval_crosses_quotients():
    state = analyze("move 3,R0\nmove 4294967285,R1\nnop\nbody: wait 100\nadd R1,4,R1\nnop\nloop R0,@body\nstop\n")
    loop = next(event for event in state.events if event.kind == "loop_block")

    assert "period_affine" not in loop.meta
    assert state.registers["R1"] == Concrete(1)
    assert loop.meta["iteration_durations"] == [100, 100, 100]
    assert state.rt_time_ns == Concrete(300)


@pytest.mark.parametrize("body", ["wait R1\nadd R1,R5,R1\nnop"])
def test_unknown_iteration_inputs_remain_conservative(body):
    state = analyze(f"move 3,R0\nmove 40,R1\nmove 8,R3\nnop\nbody: {body}\nloop R0,@body\nstop\n")

    assert "period_affine" not in next(event for event in state.events if event.kind == "loop_block").meta
    assert isinstance(state.rt_time_ns, Unknown)


@pytest.mark.parametrize("body,expected", [
    ("wait R1\nmulu16 R1,2,R1\nnop", 280),
    ("wait R3\nmove 100,R3\nnop\nadd R1,4,R1\nnop", 208),
])
def test_nonlinear_and_startup_transitions_use_evaluated_packet_durations(body, expected):
    source = f"move 3,R0\nmove 40,R1\nmove 8,R3\nnop\nbody: {body}\nloop R0,@body\nstop\n"
    state = analyze(source)
    concrete = analyze(source, execution_mode="concrete")

    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(expected)
    assert "rt_packet_durations" in next(event for event in state.events if event.kind == "loop_block").meta


def test_entry_pending_duration_input_is_not_replaced_with_its_future_value():
    source = "move 3,R0\nmove 40,R1\nnop\nmove 44,R1\nbody: wait R1\nadd R1,4,R1\nnop\nloop R0,@body\nstop\n"
    state = analyze(source)

    assert any(item.category == "register_not_ready" for item in state.diagnostics)
    assert "period_affine" not in next(event for event in state.events if event.kind == "loop_block").meta


@pytest.mark.parametrize("duration", ["R2", "$VALUE", "{R2}"])
def test_unwritten_pending_input_is_not_seeded_from_a_stale_first_read(duration):
    source = (".DEF VALUE R2\nmove 3,R0\nmove 40,R2\nnop\nmove 44,R2\n"
              f"body: wait {duration}\nloop R0,@body\nstop\n")
    state = analyze(source)

    assert any(item.category == "register_not_ready" and item.details["register"] == "R2" for item in state.diagnostics)
    assert "period_affine" not in next(event for event in state.events if event.kind == "loop_block").meta
    assert isinstance(state.rt_time_ns, Unknown)


def test_braced_physical_alias_keeps_the_pending_read_hazard():
    state = analyze(".DEF SOURCE R2\nmove 3,R0\nmove 40,R2\nnop\nmove 44,R2\n"
                    "body: wait {SOURCE}\nloop R0,@body\nstop\n")

    assert any(item.category == "register_not_ready" and item.details["register"] == "R2" for item in state.diagnostics)
    assert isinstance(state.rt_time_ns, Unknown)


@pytest.mark.parametrize("operand", ["$R2", "{$R2}"])
def test_immutable_dollar_parameter_is_not_reclassified_as_a_physical_register_read(operand):
    source = f"move 3,R0\nmove 44,R2\nbody: wait {operand}\nloop R0,@body\nstop\n"
    state = analyze(source, params={"R2": 40})
    concrete = analyze(source, params={"R2": 40}, execution_mode="concrete")

    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(120)
    assert not any(item.category == "register_not_ready" for item in state.diagnostics)
    assert not any(event.meta.get("register_reads") for event in state.events if event.kind == "q1_issue" and event.meta.get("op") == "wait")


@pytest.mark.parametrize("operand", ["$R2", "{$R2}"])
def test_undeclared_dollar_alias_is_excluded_while_placeholder_readiness_is_retained(operand):
    state = analyze("move 20,R2\nmove 3,R0\nnop\nmove 44,R2\n"
                    f"body: wait {operand}\nloop R0,@body\nstop\n")

    if operand == "$R2":
        assert state.rt_time_ns == Concrete(0)
        assert any(item.details.get("source_line_skipped") for item in state.diagnostics)
        assert not any(event.kind == "wait" for event in state.events)
    else:
        assert any(item.category == "register_not_ready" and item.details["register"] == "R2" for item in state.diagnostics)
        assert isinstance(state.rt_time_ns, Unknown)


@pytest.mark.parametrize("gap", ["", "nop\n"])
def test_bare_physical_alias_is_excluded(gap):
    source = (".DEF SOURCE R2\nmove 20,R2\nmove 3,R0\nnop\nmove 44,R2\n"
              f"body: {gap}wait SOURCE\nloop R0,@body\nstop\n")
    state = analyze(source)

    assert not any(event.kind == "q1_issue" and event.meta.get("op") == "wait" for event in state.events)
    assert any(item.category == "invalid_argument_type" and item.details.get("source_line_skipped")
               for item in state.diagnostics)


def test_affine_queue_expansion_retains_real_zero_duration_guard_behavior():
    source = "move 3,R0\nmove 0,R1\nnop\nbody: wait R1\nadd R1,4,R1\nnop\nloop R0,@body\nstop\n"
    state = analyze(source)
    concrete = analyze(source, execution_mode="concrete")
    packets, reason, _ = _queue_packets(state)
    replay = replay_queues({"seq0": packets})["seq0"]
    actual = replay_queues({"seq0": concrete.rt_packets})["seq0"]

    assert reason is None
    loop, = [event for event in state.events if event.kind == "loop_block"]
    assert loop.meta["iteration_durations"] == [4, 4, 8]
    assert loop.meta["total_duration"]["value"] == 16
    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(16)
    assert [packet.duration for packet in packets] == [Concrete(0), Concrete(4), Concrete(8)]
    assert [packet.rt_t0 for packet in packets] == [Concrete(0), Concrete(4), Concrete(8)]
    assert replay.required_buffer_ns == actual.required_buffer_ns
    assert (replay.failure.slack_ns if replay.failure else None) == (actual.failure.slack_ns if actual.failure else None)


def test_compact_constant_zero_loop_keeps_zero_packets_and_four_ns_period():
    state = analyze("move 3,R0\nbody: wait 0\nloop R0,@body\nupd_param 4\nstop",
                    loop_preview_counts={"seq0:loop:affine.q1asm:2:3": 3})
    loop, = [event for event in state.events if event.kind == "loop_block"]
    packets, reason, _ = _queue_packets(state)
    assert loop.meta["period"]["value"] == 4 and loop.meta["total_duration"]["value"] == 12
    assert [(packet.rt_t0.value, packet.rt_t1.value) for packet in packets] == [(0, 0), (4, 4), (8, 8), (12, 16)]
    assert [event.meta["next_dequeue_not_before_ns"] for event in state.events if event.kind == "wait"] == [4, 8, 12]
    assert state.rt_time_ns == Concrete(16)
    assert reason is None
    assert replay_queues({"seq0": packets})["seq0"].failure is None


def test_exact_total_does_not_bypass_the_private_packet_expansion_limit():
    state = analyze("move 10001,R0\nmove 4,R1\nnop\nbody: wait R1\nadd R1,1,R1\nnop\nloop R0,@body\nstop\n")

    assert state.rt_time_ns == Concrete(10001 * 4 + 10001 * 10000 // 2)
    assert state.registers["R1"] == Concrete(10005)
    assert analyze_underflow(state).status == "analysis_incomplete"


def test_affine_total_does_not_recover_an_unknown_absolute_prefix():
    state = analyze("wait R9\n" + compensated_source())
    loop = next(event for event in state.events if event.kind == "loop_block")

    assert loop.meta["total_duration"]["value"] == 300
    assert isinstance(state.rt_time_ns, Unknown)
    assert analyze_underflow(state).status == "analysis_incomplete"


def test_compared_affine_loops_keep_duration_coefficients_attached_to_their_own_packet_ids():
    source = "move 3,R0\nmove 40,R1\nnop\njge R7,1,@body\nwait 8\nbody: wait R1\nadd R1,4,R1\nnop\nloop R0,@body\nstop\n"
    state = analyze(source, branch_policy="explore_both_with_depth_limit")
    packets = {packet.id: packet for packet in state.rt_packets}
    loops = [event for event in state.events if event.kind == "loop_block"]

    assert len(loops) == 2
    for loop in loops:
        for packet_id in loop.meta["rt_packet_affine"]:
            assert packet_id in packets
            assert packets[packet_id].meta["branch_comparison_path"] == loop.meta["branch_comparison_path"]


@pytest.mark.parametrize("updates,step", [
    ("add R1,4,R1\nnop\nadd R1,4,R1\nnop", 8),
    ("add R1,8,R1\nnop\nsub R1,4,R1", 4),
])
def test_multiple_induction_updates_are_verified_as_one_ready_transition(updates, step):
    source = f"move 3,R0\nmove 40,R1\nnop\nbody: wait R1\n{updates}\nloop R0,@body\nwait R1\nstop\n"
    state = analyze(source)
    concrete = analyze(source, execution_mode="concrete")
    packets, reason, _ = _queue_packets(state)

    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(3 * 40 + 3 * step + 40 + 3 * step)
    assert state.registers["R1"] == Concrete(40 + 3 * step)
    assert reason is None
    assert [packet.duration for packet in packets] == [packet.duration for packet in concrete.rt_packets]


def test_coupled_changing_steps_use_evaluation_after_the_affine_transition_rejects():
    source = ("move 3,R0\nmove 40,R1\nmove 4,R2\nnop\nbody: wait R1\nadd R1,R2,R1\nnop\n"
              "add R2,4,R2\nnop\nloop R0,@body\nstop\n")
    state = analyze(source)

    assert "period_affine" not in next(event for event in state.events if event.kind == "loop_block").meta
    assert state.rt_time_ns == Concrete(136)
    assert state.registers["R1"] == Concrete(64)
    assert state.registers["R2"] == Concrete(16)
