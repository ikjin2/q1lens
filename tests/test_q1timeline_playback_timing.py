from dataclasses import replace
from pathlib import Path

import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.playback_timing import annotate_playback_queue_timing
from q1timeline.analysis.queue_timing import replay_queues
from q1timeline.playback import PlaybackSession
from q1timeline.q1asm.parser import parse_q1asm


def _state(source):
    return interpret_program(parse_q1asm(source), sequencer_id="seq0", execution_mode="concrete")


def _finished(playback):
    while playback.run(budget_ms=1000, max_steps=1000)["can_resume"]:
        pass
    return playback.runners[0].state


@pytest.mark.parametrize("operation", ["wait 0", "upd_param 0", "wait_sync 0"])
def test_zero_duration_guard_waits_for_late_enqueue(operation):
    state = _state(f"{operation}\n" + "nop\n" * 20 + "wait 4\nstop")
    result = replay_queues({"seq0": state.rt_packets})["seq0"]

    assert result.failure is None
    assert [record.dequeue_ns for record in result.records] == [4, 88]
    assert result.records[1].enqueue_ns == result.records[1].dequeue_ns


def test_zero_duration_has_four_ns_minimum_even_when_next_packet_is_ready():
    state = _state("wait 0\nwait 0\nwait 4\nstop")
    result = replay_queues({"seq0": state.rt_packets})["seq0"]

    assert result.failure is None
    assert [record.dequeue_ns for record in result.records] == [4, 8, 12]


def test_nonzero_duration_restores_guard_after_suspended_wait():
    state = _state("wait 0\nnop\nnop\nwait 4\nnop\nnop\nwait 4\nstop")
    result = replay_queues({"seq0": state.rt_packets})["seq0"]

    assert [record.dequeue_ns for record in result.records] == [4, 16]
    assert result.failure.packet.id == state.rt_packets[-1].id
    assert result.failure.dequeue_ns == 20
    assert result.failure.enqueue_ns == 28


def test_prefetched_zero_duration_sync_still_preserves_four_ns_minimum():
    state = _state("wait_sync 0\nwait 0\nwait 4\nstop")
    result = replay_queues({"seq0": state.rt_packets}, prefill_sync=True)["seq0"]

    assert result.failure is None
    assert [record.dequeue_ns for record in result.records] == [4, 16, 20]


@pytest.mark.parametrize("first_wait,expected_status", [(100, "definite_underflow"), (0, "not_detected_in_recorded_packets")])
def test_feedback_delay_queue_checks_preserve_raw_coordinates(first_wait, expected_status):
    playback = PlaybackSession({"seq0": ("input.q1asm", f"wait {first_wait}\nfb_pop_data 50,R0\nnop\nupd_param 4\nstop")})
    _finished(playback)
    playback.set_feedback(playback.feedback[0]["feedback_id"], None, 200)
    state = _finished(playback)
    original_events = [(event.id, event.t0, event.t1) for event in state.events]
    original_packets = list(state.rt_packets)
    original_execution = dict(state.metadata["execution"])
    snapshot = replace(state, metadata=dict(state.metadata), events=list(state.events), diagnostics=list(state.diagnostics))

    annotate_playback_queue_timing([snapshot])

    timing = snapshot.metadata["execution"]["queue_timing"]
    assert timing["status"] == expected_status
    assert timing["scope"] == "between_recorded_rt_packets"
    assert timing["terminal_timing"] == "not_modeled"
    assert state.metadata["execution"] == original_execution
    assert [(event.id, event.t0, event.t1) for event in snapshot.events] == original_events
    assert snapshot.rt_packets == original_packets
    assert snapshot.metadata["execution"]["status"] == "completed"
    if first_wait == 0:
        assert timing["zero_duration_waits"]["earliest"][0]["wait_ns"] == 208
        assert not snapshot.diagnostics
    else:
        assert snapshot.diagnostics[-1].category == "playback_queue_underflow"
        assert snapshot.diagnostics[-1].severity == "warning"


def test_sync_dependent_underflow_is_not_reported_as_definite():
    state = _state("wait_sync 4\nwait 4\nnop\nnop\nwait 4\nstop")
    annotate_playback_queue_timing([state])

    timing = state.metadata["execution"]["queue_timing"]
    assert timing["status"] == "analysis_incomplete"
    assert timing["earliest_failure"] is not None
    assert timing["prefetched_failure"] is None
    assert state.diagnostics[-1].category == "playback_queue_timing_uncertain"


def test_partial_trace_cannot_authorize_sync_prefetch():
    playback = PlaybackSession({"seq0": ("input.q1asm", "wait_sync 4\nwait 4\nnop\nnop\nwait 4\nstop")})
    playback.run(max_steps=5)
    state = playback.runners[0].state
    annotate_playback_queue_timing([state])

    timing = state.metadata["execution"]["queue_timing"]
    assert timing["status"] == "analysis_incomplete"
    assert any("incomplete" in reason.lower() for reason in timing["reasons"])
    assert not [item for item in state.diagnostics if item.category == "playback_queue_underflow"]


def test_feedback_after_final_packet_is_explicitly_outside_model():
    playback = PlaybackSession({"seq0": ("input.q1asm", "wait 100\nfb_pop_data 50,R0\nnop\nstop")})
    state = _finished(playback)
    annotate_playback_queue_timing([state])

    timing = state.metadata["execution"]["queue_timing"]
    assert timing["status"] == "analysis_incomplete"
    assert "Feedback waiting after the last recorded RT packet is not checked." in timing["reasons"]


def test_actual_golden_playback_remains_runnable_and_loops_are_not_reexpanded():
    path = Path(__file__).resolve().parents[1] / "examples/golden-section/golden_section.q1asm"
    playback = PlaybackSession.from_project(path)
    state = _finished(playback)
    before_status = playback.summary()
    packets = list(state.rt_packets)
    annotate_playback_queue_timing([state])

    assert playback.summary() == before_status
    assert state.rt_packets == packets
    assert len(playback.feedback) > 0
    assert "queue_timing" in state.metadata["execution"]
