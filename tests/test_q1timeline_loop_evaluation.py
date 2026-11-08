import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.underflow import _queue_packets, analyze_underflow
from q1timeline.analysis.values import Concrete, Unknown
from q1timeline.q1asm.parser import parse_q1asm


def analyze(source, **options):
    return interpret_program(parse_q1asm(source, file="evaluated-loop.q1asm"), sequencer_id="seq0", **options)


@pytest.mark.parametrize("body,periods", [
    ("wait R1\nmove 100,R1\nnop", [8, 100, 100]),
    ("wait R1\nmove R2,R1\nmove 100,R2\nnop", [8, 20, 100]),
    ("wait R1\nmove 100,R1\nnop\nwait 4", [12, 104, 104]),
    ("wait R1\nmulu16 R1,2,R1\nnop", [8, 16, 32]),
])
def test_startup_and_nonlinear_loops_match_each_concrete_packet(body, periods):
    source = f"move 3,R0\nmove 8,R1\nmove 20,R2\nnop\nbody: {body}\nloop R0,@body\nupd_param 4\nstop\n"
    state = analyze(source)
    concrete = analyze(source, execution_mode="concrete")
    packets, reason, _ = _queue_packets(state)
    loop = next(event for event in state.events if event.kind == "loop_block")

    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(sum(periods) + 4)
    assert state.q1_time_ns == concrete.q1_time_ns
    assert reason is None
    assert [(packet.duration, packet.q1_issue_t0, packet.q1_issue_t1, packet.rt_t0, packet.rt_t1)
            for packet in packets] == [
                (packet.duration, packet.q1_issue_t0, packet.q1_issue_t1, packet.rt_t0, packet.rt_t1)
                for packet in concrete.rt_packets]
    assert loop.meta["total_duration"]["value"] == sum(periods)
    assert loop.meta["shown_iterations"] == [0]
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


@pytest.mark.parametrize("reset", ["and R1,0,R1", "xor R1,R1,R1", "sub R1,R1,R1"])
def test_algebraic_resets_prove_repeated_durations_independently_of_unknown_input(reset):
    state = analyze(f"move 1000,R0\nbody: {reset}\nnop\nadd R1,100,R1\nnop\nwait R1\nloop R0,@body\nstop\n")
    loop = next(event for event in state.events if event.kind == "loop_block")

    assert state.rt_time_ns == Concrete(100_000)
    assert state.registers["R1"] == Concrete(100)
    assert loop.meta["iteration_durations"] == [100, 100]
    assert loop.meta["repeat_last_iteration"]
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


def test_private_evaluation_keeps_unknown_expressions_bounded_until_word_bits_vanish():
    state = analyze("move 1000,R0\nbody: wait 100\nadd R1,R1,R1\nnop\nloop R0,@body\nstop\n")
    loop = next(event for event in state.events if event.kind == "loop_block")

    assert state.rt_time_ns == Concrete(100_000)
    assert state.registers["R1"] == Concrete(0)
    assert len(loop.meta["iteration_durations"]) <= 34
    assert loop.meta["repeat_last_iteration"]


def test_unknown_final_values_keep_proven_bounds_and_sparse_bits_after_evaluation():
    state = analyze("move 3,R0\nbody: lsr R1,1,R1\nnop\nwait 4\nloop R0,@body\n"
                    "jlt R1,536870912,@known\nwait 200\nstop\nknown: wait 100\nstop\n")

    assert not isinstance(state.registers["R1"], Concrete)
    assert state.rt_time_ns == Concrete(112)
    assert not any(item.category == "unresolved_branch" for item in state.diagnostics)


def test_private_duration_failure_does_not_publish_a_partial_proof():
    state = analyze("move 3,R0\nmove 40000,R1\nnop\nbody: wait R1\nlsl R1,1,R1\nnop\nloop R0,@body\nstop\n")
    loop = next(event for event in state.events if event.kind == "loop_block")

    assert "iteration_durations" not in loop.meta
    assert isinstance(state.rt_time_ns, Unknown)
    assert analyze_underflow(state).status == "analysis_incomplete"


def test_an_earlier_unknown_time_is_not_recovered_by_evaluated_loop_durations():
    state = analyze("wait R7\nmove 3,R0\nmove 8,R1\nnop\nbody: wait R1\nmove 100,R1\nnop\nloop R0,@body\nstop\n")

    assert isinstance(state.rt_time_ns, Unknown)
    assert analyze_underflow(state).status == "analysis_incomplete"


def test_summary_provenance_never_exposes_private_event_ids():
    state = analyze("move 3,R0\nmove 8,R1\nnop\nbody: wait R1\nmove 100,R1\nnop\nloop R0,@body\nstop\n")
    writes = [write for write in state.register_history if write.register == "R1" and write.meta.get("evaluated")]

    assert writes
    assert all(write.event_id in {event.id for event in state.events} for write in writes)
    assert writes[-1].value == Concrete(100)
