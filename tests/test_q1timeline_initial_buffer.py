from dataclasses import replace

import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.playback_timing import annotate_playback_queue_timing
from q1timeline.analysis.queue_timing import replay_queues
from q1timeline.analysis.underflow import analyze_underflow
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm


def _state(gaps, *, first_duration=4):
    state = interpret_program(
        parse_q1asm(f"wait {first_duration}\n" + "wait 4\n" * len(gaps) + "stop"),
        sequencer_id="seq0", execution_mode="concrete",
    )
    offset = 0
    for index, gap in enumerate(gaps, 1):
        offset += gap
        packet = state.rt_packets[index]
        state.rt_packets[index] = replace(packet,
            q1_issue_t0=Concrete(packet.q1_issue_t0.value + offset),
            q1_issue_t1=Concrete(packet.q1_issue_t1.value + offset))
    return state


@pytest.mark.parametrize("gap,expected", [
    (999_996, "assumed"), (1_000_000, "assumed"),
    (1_000_004, "tentative"), (2_000_000, "tentative"), (2_000_004, "exhausted"),
])
@pytest.mark.parametrize("mode", ["analysis", "playback"])
def test_initial_buffer_policy_boundaries(gap, expected, mode):
    state = _state([gap])
    original_packets = list(state.rt_packets)
    original_events = [(event.id, event.t0, event.t1) for event in state.events]
    if mode == "analysis":
        analyze_underflow(state)
        timing = state.metadata["underflow"]
    else:
        annotate_playback_queue_timing([state])
        timing = state.metadata["execution"]["queue_timing"]

    assert timing["initial_buffer"]["status"] == expected
    assert timing["initial_buffer"]["required_ns"] == gap
    assert timing["assumptions"]["initial_buffer_assumed_ns"] == 1_000_000
    assert timing["assumptions"]["initial_buffer_possible_ns"] == 2_000_000
    warnings = [item for item in state.diagnostics if item.severity == "warning"]
    estimates = [item for item in state.diagnostics if item.category == "initial_rt_buffer_estimate"]
    assert bool(warnings) == (expected == "exhausted")
    assert bool(estimates) == (expected == "tentative")
    if estimates:
        assert estimates[0].severity == "info"
        assert timing["status"] == "possibly_safe_with_initial_buffer"
    assert state.rt_packets == original_packets
    assert [(event.id, event.t0, event.t1) for event in state.events[:len(original_events)]] == original_events


@pytest.mark.parametrize("budget", [1_000_000, 2_000_000])
def test_initial_buffer_is_spent_cumulatively_and_does_not_hide_later_underflow(budget):
    state = _state([budget // 2, budget // 2, 4])
    result = replay_queues({"seq0": state.rt_packets}, initial_buffer_ns=budget)["seq0"]

    assert result.required_buffer_ns == budget + 4
    assert len(result.records) == 3
    assert result.failure.packet.id == state.rt_packets[3].id
    assert result.records[0].dequeue_ns == result.records[0].enqueue_ns == 4


def test_long_rt_instruction_does_not_spend_buffer_when_q1_keeps_up():
    state = _state([1000], first_duration=65535)
    result = replay_queues({"seq0": state.rt_packets}, initial_buffer_ns=1_000_000)["seq0"]

    assert result.failure is None
    assert result.required_buffer_ns == 0
    assert [record.dequeue_ns for record in result.records] == [4, 65539]


def test_zero_duration_wait_does_not_spend_or_replenish_initial_buffer():
    state = _state([600_000, 3_000_000, 500_000])
    state.rt_packets[1] = replace(state.rt_packets[1], duration=Concrete(0))
    result = replay_queues({"seq0": state.rt_packets}, initial_buffer_ns=1_000_000)["seq0"]

    assert result.required_buffer_ns == 1_100_000
    assert result.failure.packet.id == state.rt_packets[3].id
    assert len(result.records) == 3


def test_each_sequencer_has_its_own_initial_buffer():
    first = _state([750_000])
    second = _state([750_000])
    results = replay_queues({"first": first.rt_packets, "second": second.rt_packets}, initial_buffer_ns=1_000_000)

    assert all(result.required_buffer_ns == 750_000 and result.failure is None for result in results.values())


def test_buffer_credit_does_not_shift_or_restore_a_missed_trigger():
    state = _state([1000, 0])
    packet = state.rt_packets[1]
    state.rt_packets[1] = replace(packet, op="wait_trigger", meta={"trigger_assumption": {
        "status": "received", "arrival_ns": 100, "duration_ns": 4,
    }})
    result = replay_queues({"seq0": state.rt_packets}, initial_buffer_ns=1_000_000)["seq0"]

    assert result.required_buffer_ns == 1000
    assert result.failure is None
    assert "arrived at 100 ns" in result.incomplete_reason
    assert len(result.records) == 2


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_initial_buffer_rejects_invalid_credit(value):
    with pytest.raises(ValueError, match="initial_buffer_ns"):
        replay_queues({}, initial_buffer_ns=value)
