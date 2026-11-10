from dataclasses import replace
from pathlib import Path

import pytest

from q1timeline.analysis.api import analyze_text
from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.playback_timing import annotate_playback_queue_timing
from q1timeline.analysis.queue_timing import replay_queues
from q1timeline.analysis.values import Concrete
from q1timeline.playback import PlaybackSession
from q1timeline.q1asm.parser import parse_q1asm


def _state(source):
    return interpret_program(parse_q1asm(source), sequencer_id="seq0", execution_mode="concrete")


def _finished(playback):
    while playback.run(budget_ms=1000, max_steps=1000)["can_resume"]:
        pass
    return playback.runners[0].state


def test_initial_prefill_contains_commands_instead_of_a_fixed_time_budget():
    state = _state("wait 4\nwait 4\nstop")
    early = replay_queues({"seq0": state.rt_packets})["seq0"]
    full = replay_queues({"seq0": state.rt_packets}, prefill_startup=True)["seq0"]

    assert [record.dequeue_ns for record in early.records] == [4, 8]
    assert [record.dequeue_ns for record in full.records] == [8, 12]
    assert [record.depth for record in full.records] == [1, 0]


@pytest.mark.parametrize("duration", [4, 1000])
def test_prefilled_commands_are_removed_and_executed_one_at_a_time(duration):
    state = _state(f"wait {duration}\nwait {duration}\nwait {duration}\nstop")
    result = replay_queues({"seq0": state.rt_packets}, prefill_startup=True)["seq0"]

    assert [record.dequeue_ns for record in result.records] == [12, 12 + duration, 12 + 2 * duration]
    assert [record.depth for record in result.records] == [2, 1, 0]
    assert result.failure is None


def test_initial_prefill_releases_when_finite_fifo_fills():
    state = _state("wait 4\n" * 40 + "stop")
    result = replay_queues({"seq0": state.rt_packets}, prefill_startup=True)["seq0"]

    assert result.records[0].dequeue_ns == 32 * 4
    assert result.records[0].depth == 31
    assert len(result.records) == 40
    assert all(record.depth < 32 for record in result.records)
    assert result.failure is None


def test_unknown_packet_prefix_cannot_authorize_complete_initial_prefill():
    from q1timeline.analysis.values import Unknown

    state = _state("wait 4\nwait 4\nstop")
    state.rt_packets[1] = replace(state.rt_packets[1], duration=Unknown("Incomplete playback"))
    result = replay_queues({"seq0": state.rt_packets}, prefill_startup=True)["seq0"]

    assert result.records == []
    assert result.incomplete_reason is not None


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


@pytest.mark.parametrize("mode", ["analysis", "concrete", "playback", "simulation"])
@pytest.mark.parametrize("register_wait", [False, True])
def test_zero_packet_timeline_and_nominal_duration_agree_across_modes(mode, register_wait):
    prefix = "move 0,R7\nnop\n" if register_wait else ""
    first_wait = "wait R7" if register_wait else "wait 0"
    source = f"{prefix}{first_wait}\nwait 0\nupd_param 4\nstop"
    if mode == "playback":
        playback = PlaybackSession({"seq0": ("zero.q1asm", source)})
        _finished(playback)
        ir = playback.snapshot()
    else:
        ir = analyze_text(source, sequencer_id="seq0", execution_mode=mode)
    rt = [event for event in ir["events"] if event["kind"] in {"wait", "upd_param", "stop"}]
    assert [(event["kind"], event["meta"]["aligned_t0"], event["meta"]["aligned_t1"], event["duration"]["value"])
            for event in rt] == [("wait", 0, 0, 0), ("wait", 4, 4, 0), ("upd_param", 8, 12, 4), ("stop", 12, 12, 0)]
    assert [event["meta"]["queue_duration_ns"] for event in rt[:2]] == [0, 0]
    assert [event["meta"]["next_dequeue_not_before_ns"] - event["t1"]["value"] for event in rt[:2]] == [4, 4]
    issues = [event["t0"]["value"] for event in ir["events"] if event["kind"] == "q1_issue"]
    assert issues == ([0, 4, 8, 12, 16, 20] if register_wait else [0, 4, 8, 12])


def test_static_zero_trigger_preserves_unknown_arrival():
    ir = analyze_text("wait_trigger 1,0\nwait 4\nstop", sequencer_id="seq0")
    wait, = [event for event in ir["events"] if event["kind"] == "wait_trigger"]
    assert wait["duration"]["value"] == 0
    assert "next_dequeue_not_before_ns" not in wait["meta"]
    assert any(item["category"] == "analysis_incomplete" and item["details"]["reason"] == "trigger_arrival_unknown"
               for item in ir["diagnostics"])


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
    playback = PlaybackSession({"seq0": ("input.q1asm", f"wait {first_wait}\n" * 32 + "fb_pop_data 50,R0\nnop\nupd_param 4\nstop")})
    _finished(playback)
    playback.set_feedback(playback.feedback[0]["feedback_id"], None, 3_000_000)
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
        assert timing["zero_duration_waits"]["earliest"][0]["wait_ns"] == 3_000_008
        assert not snapshot.diagnostics
    else:
        assert snapshot.diagnostics[-1].category == "playback_queue_underflow"
        assert snapshot.diagnostics[-1].severity == "error"


def test_initial_buffer_covers_short_post_sync_housekeeping():
    state = _state("wait_sync 4\nwait 4\nnop\nnop\nwait 4\nstop")
    annotate_playback_queue_timing([state])

    timing = state.metadata["execution"]["queue_timing"]
    assert timing["status"] == "not_detected_in_recorded_packets"
    assert timing["earliest_failure"] is None
    assert timing["prefetched_failure"] is None
    assert not state.diagnostics


def test_initial_buffer_covers_early_playback_feedback_gap():
    playback = PlaybackSession({"seq0": ("input.q1asm", "wait 4\nfb_pop_data 50,R0\nnop\nupd_param 4\nstop")})
    _finished(playback)
    playback.set_feedback(playback.feedback[0]["feedback_id"], None, 500_000)
    state = _finished(playback)
    annotate_playback_queue_timing([state])

    timing = state.metadata["execution"]["queue_timing"]
    assert timing["status"] == "not_detected_in_recorded_packets"
    assert "startup_delay_ns" not in timing["assumptions"]
    assert timing["initial_buffer"]["status"] == "assumed"
    assert "never_replenished" in timing["assumptions"]["buffer_duration"]
    assert timing["earliest_failure"] is None
    assert timing["prefetched_failure"] is None
    assert not [item for item in state.diagnostics if item.category == "playback_queue_underflow"]


def test_partial_trace_cannot_authorize_sync_prefetch():
    playback = PlaybackSession({"seq0": ("input.q1asm", "wait_sync 4\nwait 4\nnop\nnop\nwait 4\nstop")})
    playback.run(max_steps=5)
    state = playback.runners[0].state
    annotate_playback_queue_timing([state])

    timing = state.metadata["execution"]["queue_timing"]
    assert timing["status"] == "analysis_incomplete"
    assert any("incomplete" in reason.lower() for reason in timing["reasons"])
    assert not [item for item in state.diagnostics if item.category == "playback_queue_underflow"]


@pytest.mark.parametrize("capacity,expected", [(1, "error"), (2, "completed")])
@pytest.mark.parametrize("paired", [False, True])
def test_failed_stop_cannot_authorize_sync_prefetch(capacity, expected, paired):
    # Finish the unaffected peer's stop first so only the overflow runner is incomplete.
    documents = {"peer": ("peer.q1asm", "wait_sync 4\nstop")} if paired else {}
    documents["seq"] = ("input.q1asm", "wait_sync 4\nstop" if paired else "wait 100\nstop")
    playback = PlaybackSession(documents, feedback_queue_capacity=capacity, external_inputs={
        "feedback": [{"target": "seq", "arrivals": [
            {"time_ns": 5, "id": 9, "value": 81}, {"time_ns": 6, "id": 5, "value": 25},
        ]}],
    })
    _finished(playback)
    assert all(runner.state.stopped for runner in playback.runners)
    ir = playback.snapshot()
    execution = ir["execution"]["seq"]
    timing = execution["queue_timing"]
    assert ir["playback"]["status"] == execution["status"] == expected
    assert timing["earliest_failure"] is timing["prefetched_failure"] is None
    overflows = [item for item in ir["diagnostics"] if item["category"] == "feedback_queue_overflow"]
    if expected == "error":
        assert timing["status"] == "analysis_incomplete"
        assert "The recorded execution is incomplete." in timing["reasons"]
        overflow, = overflows
        assert overflow["source"]["line"] == 2
        assert not any(event["kind"] == "stop" and event["sequencer_id"] == "seq" for event in ir["events"])
    else:
        assert timing["status"] == "not_detected_in_recorded_packets"
        assert not timing["reasons"] and not overflows
        assert execution["stop_code"] == 0
    if paired:
        peer = ir["execution"]["peer"]
        assert peer["status"] == "completed"
        assert peer["queue_timing"]["status"] == timing["status"]
        if expected == "error":
            assert peer["queue_timing"]["reasons"] == ["Maximal prefetch before wait_sync peer:p0 is unknown."]
            sync, = [item for item in ir["diagnostics"] if item["category"] == "analysis_incomplete"
                     and item.get("source", {}).get("file") == "peer.q1asm"]
            assert sync["details"]["replay_scenarios"] == ["prefetched"]


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


def _trigger_state(*, arrival_ns=100, duration_ns=8, prefix="", tail="wait 4\nstop"):
    state = _state(f"{prefix}wait {arrival_ns + duration_ns}\n{tail}")
    packet = state.rt_packets[0]
    state.rt_packets[0] = replace(packet, op="wait_trigger", meta={
        **packet.meta,
        "trigger_assumption": {
            "source_id": "shot_start", "event_id": "shot_start:1", "address": 1,
            "armed_at_ns": 0, "arrival_ns": arrival_ns,
            "resume_ns": arrival_ns + duration_ns, "duration_ns": duration_ns,
            "status": "received",
        },
    })
    return state


def test_shared_trigger_queue_timing_uses_absolute_arrival_for_each_receiver():
    early = _trigger_state()
    later = _trigger_state(prefix="nop\n" * 5)

    results = replay_queues({"early": early.rt_packets, "later": later.rt_packets})

    assert [record.dequeue_ns for record in results["early"].records] == [4, 108]
    assert [record.dequeue_ns for record in results["later"].records] == [24, 108]
    assert all(result.failure is None and result.incomplete_reason is None for result in results.values())
    assert early.rt_packets[0].duration == Concrete(108)


def test_initial_prefill_preserves_shared_trigger_arrival():
    early = _trigger_state()
    later = _trigger_state(prefix="nop\n" * 5)
    results = replay_queues({"early": early.rt_packets, "later": later.rt_packets}, prefill_startup=True)

    assert [record.dequeue_ns for record in results["early"].records] == [8, 108]
    assert [record.dequeue_ns for record in results["later"].records] == [28, 108]
    assert all(result.failure is None and result.incomplete_reason is None for result in results.values())


def test_trigger_at_dequeue_time_is_accepted():
    state = _trigger_state(arrival_ns=4)
    result = replay_queues({"seq0": state.rt_packets})["seq0"]

    assert [record.dequeue_ns for record in result.records] == [4, 12]
    assert result.failure is None
    assert result.incomplete_reason is None


@pytest.mark.parametrize("arrival", [1, 4])
def test_playback_reports_trigger_rejected_before_first_dequeue_at_its_source(arrival):
    playback = PlaybackSession({"a": ("a.q1asm", "wait_trigger 1,4\nwait 4\nstop")}, external_inputs={
        "triggers": [{"id": "edge", "address": 1, "targets": ["a"], "times_ns": [arrival]}],
    })
    _finished(playback)
    ir = playback.snapshot()
    assert ir["playback"]["status"] == "completed"
    wait, = [event for event in ir["events"] if event["kind"] == "wait_trigger"]
    assert (wait["t0"]["value"], wait["t1"]["value"]) == (0, arrival + 4)
    assert wait["meta"]["trigger_assumption"]["event_id"] == "external:edge:0"
    timing = ir["execution"]["a"]["queue_timing"]
    diagnostics = [item for item in ir["diagnostics"] if item["category"] == "analysis_incomplete"]
    if arrival == 4:
        assert timing["status"] == "not_detected_in_recorded_packets"
        assert diagnostics == []
    else:
        assert timing["status"] == "analysis_incomplete"
        diagnostic, = diagnostics
        assert diagnostic["source"] == wait["source"] and diagnostic["source"]["line"] == 1
        assert diagnostic["details"]["rt_packet_id"] == wait["meta"]["rt_packet_id"] == "a:p0"
        assert (diagnostic["details"]["arrival_ns"], diagnostic["details"]["dequeue_ns"]) == (1, 4)
        assert diagnostic["details"]["replay_scenarios"] == ["earliest", "prefetched"]
        assert diagnostic["message"] == timing["reasons"][0]


def test_playback_distinguishes_prefetched_only_trigger_rejection():
    playback = PlaybackSession({"a": ("a.q1asm", "wait_sync 4\nwait_trigger 1,4\nwait 4\nstop"),
                                "b": ("b.q1asm", "wait_sync 4\nstop")}, external_inputs={
        "triggers": [{"id": "edge", "address": 1, "targets": ["a"], "times_ns": [10]}],
    })
    _finished(playback)
    ir = playback.snapshot()
    assert ir["playback"]["status"] == "completed"
    diagnostic, = [item for item in ir["diagnostics"] if item["category"] == "analysis_incomplete"]
    assert (diagnostic["source"]["file"], diagnostic["source"]["line"]) == ("a.q1asm", 2)
    assert "prefetched replay" in diagnostic["message"]
    details = diagnostic["details"]
    assert (details["rt_packet_id"], details["arrival_ns"], details["dequeue_ns"]) == ("a:p1", 10, 16)
    assert details["replay_scenarios"] == ["prefetched"]
    assert details["replay_statuses"] == {"earliest": "not_detected_in_recorded_packets", "prefetched": "analysis_incomplete"}


@pytest.mark.parametrize("paired", [True, False])
def test_playback_unmatched_sync_diagnostic_points_to_the_waiting_barrier(paired):
    playback = PlaybackSession({"a": ("a.q1asm", "nop\nwait_sync 4\nwait 4\nstop"),
                                "b": ("b.q1asm", "wait_sync 4\nstop" if paired else "wait 4\nstop")})
    _finished(playback)
    ir = playback.snapshot()
    assert ir["playback"]["status"] == "completed"
    diagnostics = [item for item in ir["diagnostics"] if item["category"] == "analysis_incomplete"]
    assert ir["execution"]["b"]["queue_timing"]["status"] == "not_detected_in_recorded_packets"
    if paired:
        assert diagnostics == []
        assert ir["execution"]["a"]["queue_timing"]["status"] == "not_detected_in_recorded_packets"
    else:
        diagnostic, = diagnostics
        assert (diagnostic["source"]["file"], diagnostic["source"]["line"]) == ("a.q1asm", 2)
        assert diagnostic["details"]["rt_packet_id"] == "a:p0"
        assert diagnostic["details"]["missing_sync_participants"] == ["b"]
        assert diagnostic["details"]["replay_scenarios"] == ["earliest", "prefetched"]
        assert "no matching reachable barrier for: b" in diagnostic["message"]


def test_unmatched_sync_preserves_earlier_peer_rejection():
    playback = PlaybackSession({"a": ("a.q1asm", "wait_sync 4\nstop"),
                                "b": ("b.q1asm", "wait_trigger 1,4\nwait_sync 4\nstop")}, external_inputs={
        "triggers": [{"id": "edge", "address": 1, "targets": ["b"], "times_ns": [1]}],
    })
    _finished(playback)
    ir = playback.snapshot()
    diagnostics = {item["source"]["file"]: item for item in ir["diagnostics"] if item["category"] == "analysis_incomplete"}
    assert set(diagnostics) == {"a.q1asm", "b.q1asm"}
    assert diagnostics["a.q1asm"]["details"]["missing_sync_participants"] == ["b"]
    assert "arrived at 1 ns" in diagnostics["b.q1asm"]["message"]
    for seq in ("a", "b"):
        diagnostic = diagnostics[f"{seq}.q1asm"]
        assert diagnostic["details"]["rt_packet_id"] == f"{seq}:p0"
        assert diagnostic["source"]["line"] == 1
        assert diagnostic["message"] == ir["execution"][seq]["queue_timing"]["reasons"][0]


def test_unmatched_sync_diagnostics_preserve_waiting_and_partial_trace_reasons():
    playback = PlaybackSession({"a": ("a.q1asm", "wait_sync 4\nstop"),
                                "b": ("b.q1asm", "wait_trigger 1,4\nwait_sync 4\nstop")})
    assert playback.run(max_steps=1)["status"] == "paused"
    partial = playback.snapshot()
    reasons = partial["execution"]["a"]["queue_timing"]["reasons"]
    assert any("Unknown packet timing" in reason for reason in reasons)
    assert "The recorded execution is incomplete." in reasons
    _finished(playback)
    waiting = playback.snapshot()
    assert waiting["playback"]["status"] == "waiting"
    assert "No connected trigger source" in waiting["execution"]["b"]["reason"]
    diagnostic, = [item for item in waiting["diagnostics"] if item["category"] == "analysis_incomplete"]
    assert diagnostic["source"]["file"] == "a.q1asm"
    assert diagnostic["details"]["missing_sync_participants"] == ["b"]


def test_missed_trigger_stops_queue_check_without_stopping_independent_peer():
    missed = _trigger_state(arrival_ns=0)
    peer = _state("wait 8\nwait 4\nstop")
    results = replay_queues({"missed": missed.rt_packets, "peer": peer.rt_packets})

    assert [record.dequeue_ns for record in results["missed"].records] == [4]
    assert results["missed"].failure is None
    assert "arrived at 0 ns" in results["missed"].incomplete_reason
    assert [record.dequeue_ns for record in results["peer"].records] == [4, 12]
    assert results["peer"].incomplete_reason is None


def test_trigger_without_received_scenario_keeps_queue_timing_incomplete():
    state = _trigger_state()
    state.rt_packets[0] = replace(state.rt_packets[0], meta={})
    result = replay_queues({"seq0": state.rt_packets})["seq0"]

    assert result.records == []
    assert "External trigger timing is unknown" in result.incomplete_reason


def test_trigger_queue_annotation_preserves_analytical_coordinates_and_names_assumption():
    state = _trigger_state()
    packets = list(state.rt_packets)
    events = [(event.id, event.t0, event.t1) for event in state.events]
    annotate_playback_queue_timing([state])

    timing = state.metadata["execution"]["queue_timing"]
    assert timing["status"] == "not_detected_in_recorded_packets"
    assert "scheduled_receiver_arrival" in timing["assumptions"]["trigger"]
    assert state.rt_packets == packets
    assert [(event.id, event.t0, event.t1) for event in state.events] == events


def test_trigger_missed_by_maximum_sync_prefetch_is_reported_incomplete():
    state = _state("wait_sync 4\nwait 100\nwait 4\nstop")
    trigger = _trigger_state(arrival_ns=10).rt_packets[0]
    state.rt_packets[1] = replace(state.rt_packets[1], op="wait_trigger", meta=trigger.meta)
    annotate_playback_queue_timing([state])

    timing = state.metadata["execution"]["queue_timing"]
    assert timing["status"] == "analysis_incomplete"
    assert any("arrived at 10 ns" in reason for reason in timing["reasons"])
    assert timing["earliest_failure"] is None
    assert timing["prefetched_failure"] is None


def test_zero_trigger_duration_waits_for_late_enqueue_from_arrival():
    state = _trigger_state(arrival_ns=100, duration_ns=0, tail="nop\n" * 30 + "wait 4\nstop")
    result = replay_queues({"seq0": state.rt_packets})["seq0"]
    annotate_playback_queue_timing([state])

    assert [record.dequeue_ns for record in result.records] == [4, 128]
    assert result.failure is None
    assert state.metadata["execution"]["queue_timing"]["zero_duration_waits"]["earliest"] == [{
        "after_packet_id": state.rt_packets[0].id, "next_packet_id": state.rt_packets[1].id,
        "wait_ns": 24,
    }]
