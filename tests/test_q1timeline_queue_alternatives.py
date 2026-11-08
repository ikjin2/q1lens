import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm


def analyze(source, **kwargs):
    return interpret_program(parse_q1asm(source), sequencer_id="seq0", **kwargs)


PREFIX = "jge R7,1,@other\nwait 100\njmp @join\nother: wait 100\njoin: "


def test_finite_private_alternatives_preserve_both_packet_arrivals_without_changing_public_q1():
    source = PREFIX + "nop\njmp @done\nwait 4\ndone: wait 40\nstop"
    state = analyze(source)

    assert not isinstance(state.q1_time_ns, Concrete)
    assert set(state.queue_alternatives) == {"taken", "fallthrough"}
    assert any(event.meta.get("queue_alternative_count") == 2 for event in state.events)
    for path, seed in (("taken", 1), ("fallthrough", 0)):
        variant = state.queue_alternatives[path]
        concrete = analyze(f"move {seed},R7\nnop\n" + source, execution_mode="concrete")
        assert variant.stopped and variant.queue_alternative_probe
        assert not variant.queue_alternatives
        assert variant.rt_time_ns == concrete.rt_time_ns == state.rt_time_ns == Concrete(140)
        assert [(packet.duration, packet.q1_issue_t0.value, packet.q1_issue_t1.value, packet.rt_t0, packet.rt_t1)
                for packet in variant.rt_packets] == [
                    (packet.duration, packet.q1_issue_t0.value - 8, packet.q1_issue_t1.value - 8, packet.rt_t0, packet.rt_t1)
                    for packet in concrete.rt_packets]


@pytest.mark.parametrize("suffix", [
    "wait_sync 4\nstop",
    "wait_trigger 0,4\nstop",
    "fb_pop_data 1,R1\nstop",
    "jge R6,1,@later\nwait 20\nstop\nlater: wait 40\nstop",
    "move 3,R0\nbody: wait 40\nloop R0,@body\nstop",
    "back: nop\njmp @back",
    "wait R8\nstop",
    "move 4,R1\nwait R1\nstop",
    "lsl R8,R9,R1\nand R1,0,R1\nnop\nwait 40\nstop",
    "and R8,65535,R1\nnop\nset_awg_gain R1,R1\nupd_param 40\nstop",
    "set_ph R8\nupd_param 40\nstop",
    "play_netzero 0,0,0,4\nstop",
])
def test_private_queue_alternatives_reject_incomplete_or_unsupported_common_suffixes(suffix):
    assert not analyze(PREFIX + suffix).queue_alternatives


@pytest.mark.parametrize("policy", ["assume_true", "assume_false", "both"])
def test_explicit_branch_policies_do_not_create_private_automatic_alternatives(policy):
    assert not analyze(PREFIX + "wait 40\nstop", branch_policy=policy).queue_alternatives


def test_private_queue_proof_work_is_bounded():
    assert not analyze(PREFIX + "nop\n" * 10000 + "wait 40\nstop").queue_alternatives


def test_private_queue_proofs_accept_unknown_latched_operands_with_proved_legal_domains():
    state = analyze(PREFIX + "and R8,255,R1\nnop\nset_awg_gain R1,R1\nupd_param 40\nstop")

    assert len(state.queue_alternatives) == 2
