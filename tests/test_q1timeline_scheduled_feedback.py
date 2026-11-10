import pytest

from q1timeline.external_inputs import validate_external_inputs
from q1timeline.playback import PlaybackSession


def schedule(*arrivals):
    return {"feedback": [{"target": "seq", "arrivals": [
        {"time_ns": time, "id": identifier, "value": value} for time, identifier, value in arrivals
    ]}]}


def session(source, external_inputs=None, **kwargs):
    return PlaybackSession({"seq": ("linq.q1asm", source)}, external_inputs=external_inputs, **kwargs)


def execute(playback):
    while playback.run()["status"] == "paused":
        pass
    return playback.snapshot()


def test_late_fifo_pull_and_selective_pop_preserve_both_destinations_and_raw_word():
    playback = session("wait 100\nfb_pull_data R1,R2\nfb_pop_data 5,R3\nnop\nwait 8\nstop", schedule((16, 9, -1), (16, 5, 77)))
    ir = execute(playback)
    assert ir["playback"]["status"] == "completed"
    pull, pop = ir["playback"]["feedback"]
    assert (pull["id"], pull["armed_at_ns"], pull["arrival_ns"], pull["available_at_ns"], pull["completed_at_ns"]) == (9, 4, 16, 16, 24)
    assert (pop["id"], pop["arrival_ns"], pop["available_at_ns"], pop["completed_at_ns"]) == (5, 16, 24, 28)
    assert [pull["discarded_input_event_ids"], pop["discarded_input_event_ids"]] == [[], []]
    inputs = ir["playback"]["external_inputs"]["feedback"][0]["arrivals"]
    assert [pull["input_event_id"], pop["input_event_id"]] == [entry["event_id"] for entry in inputs]
    assert inputs[0]["value"] == -1
    writes = ir["register_history"]
    assert [(write["register"], write["value"]["value"], write["event_id"], write["meta"]["provenance"]["input_event_id"])
            for write in writes] == [("R1", 9, pull["event_id"], pull["input_event_id"]),
                                     ("R2", 0xFFFFFFFF, pull["event_id"], pull["input_event_id"]),
                                     ("R3", 77, pop["event_id"], pop["input_event_id"])]
    assert all(ir["source_map"]["by_event_id"][write["event_id"]] == write["source"] for write in writes)
    first, second = playback.runners[0].state.rt_packets
    assert (first.rt_t0.value, first.rt_t1.value, first.q1_issue_t0.value) == (0, 100, 0)
    assert (second.rt_t0.value, second.q1_issue_t0.value) == (100, 32)
    with pytest.raises(ValueError, match="Configure"):
        playback.set_feedback(pull["feedback_id"], 7, 0)


def test_selective_pop_discards_only_older_words_then_pull_consumes_the_next():
    playback = session("fb_pop_data 5,R0\nfb_pull_data R1,R2\nnop\nstop", schedule((6, 9, 81), (8, 5, 25), (8, 11, 121)))
    ir = execute(playback)
    pop, pull = ir["playback"]["feedback"]
    arrivals = ir["playback"]["external_inputs"]["feedback"][0]["arrivals"]
    assert pop["discarded_input_event_ids"] == [arrivals[0]["event_id"]]
    assert (pop["input_event_id"], pop["completed_at_ns"]) == (arrivals[1]["event_id"], 12)
    assert (pull["id"], pull["input_event_id"], pull["completed_at_ns"]) == (11, arrivals[2]["event_id"], 20)
    assert ir["execution"]["seq"]["queue_timing"]["status"] == "analysis_incomplete"


@pytest.mark.parametrize("selector", [1, 257])
@pytest.mark.parametrize("parameterized", [False, True])
def test_scheduled_play_blocks_unverified_selector_without_discarding_pending_word(selector, parameterized):
    operand = "SELECTOR" if parameterized else str(selector)
    playback = session(f"fb_pop_data {operand},R0\nnop\nstop", schedule((0, 1, 42)), params={"SELECTOR": selector})
    ir = execute(playback)
    pending = playback.runners[0].scheduled_feedback.pending

    if selector == 1:
        assert ir["playback"]["status"] == "completed" and not pending
        receipt, = ir["playback"]["feedback"]
        assert receipt["id"] == 1 and receipt["value"] == 42 and not receipt["discarded_input_event_ids"]
    else:
        assert ir["playback"]["status"] == "blocked" and ir["playback"]["steps"] == 0
        assert [(entry["id"], entry["value"]) for entry in pending] == [(1, 42)]
        assert not ir["playback"]["feedback"] and not ir["register_history"]
        diagnostic, = [item for item in ir["diagnostics"] if item["category"] == "analysis_incomplete"]
        assert (diagnostic["source"]["file"], diagnostic["source"]["line"]) == ("linq.q1asm", 1)
        assert "selector 257" in diagnostic["message"] and "unverified" in diagnostic["message"]


def test_manual_play_receipt_still_accepts_an_encodable_wide_selector():
    playback = session("fb_pop_data 257,R0\nnop\nstop")
    receipt, = execute(playback)["playback"]["feedback"]
    playback.set_feedback(receipt["feedback_id"], 42, 0)
    ir = execute(playback)
    assert ir["playback"]["status"] == "completed"
    assert ir["playback"]["feedback"][0]["assumption_source"] == "user"
    assert ir["register_history"][0]["value"]["value"] == 42


def test_empty_receiver_stalls_q1_and_reset_restores_the_manual_assumption():
    playback = session("fb_pop_data 5,R0\nnop\nstop")
    base = execute(playback)
    feedback_id = base["playback"]["feedback"][0]["feedback_id"]
    playback.set_feedback(feedback_id, 33, 1)
    playback.set_external_inputs(schedule())
    waiting = execute(playback)
    assert waiting["playback"]["status"] == "waiting"
    assert "LINQ ID 5" in waiting["playback"]["reason"]
    assert "Configure" in waiting["playback"]["reason"]
    assert waiting["playback"]["external_inputs"]["feedback"][0]["arrivals"] == []
    assert waiting["register_history"] == []
    assert any("Feedback waiting" in reason for reason in waiting["execution"]["seq"]["queue_timing"]["reasons"])
    playback.set_external_inputs(schedule((0, 5, 72)))
    assert execute(playback)["register_history"][0]["value"]["value"] == 72
    playback.set_external_inputs(None)
    restored = execute(playback)
    assert restored["playback"]["feedback"][0]["assumption_source"] == "user"
    assert restored["register_history"][0]["value"]["value"] == 33


def test_project_capacity_rejects_a_simultaneous_overflow(tmp_path):
    program = tmp_path / "linq.q1asm"
    program.write_text("fb_pull_data R0,R1\nstop", encoding="utf-8")
    project = tmp_path / "q1timeline.yml"
    project.write_text("""version: 1
sequencers:
  - {id: seq, file: linq.q1asm}
simulation:
  feedback_queue_capacity: 1
external_inputs:
  feedback:
    - target: seq
      arrivals:
        - {time_ns: 8, id: 9, value: 81}
        - {time_ns: 8, id: 5, value: 25}
""", encoding="utf-8")
    ir = execute(PlaybackSession.from_project(project))
    assert ir["playback"]["status"] == "error"
    overflow, = [item for item in ir["diagnostics"] if item["category"] == "feedback_queue_overflow"]
    assert "capacity 1" in overflow["message"]
    assert overflow["details"]["input_event_id"] == "external-linq:seq:1"
    assert not ir["register_history"]


def test_shifted_q1_timing_before_a_scheduled_receipt_makes_queue_check_incomplete():
    playback = session("wait 10000\n" + "wait 4\n" * 33 + "fb_pull_data R0,R1\nnop\nwait 4\nstop", schedule((200, 9, 81)))
    ir = execute(playback)
    assert ir["playback"]["status"] == "completed"
    timing = ir["execution"]["seq"]["queue_timing"]
    assert timing["status"] == "analysis_incomplete"
    assert any("Shifted Q1 timing" in reason for reason in timing["reasons"])


@pytest.mark.parametrize("program,line", [
    ("fb_pull_data R0,R1\nstop", 1),
    ("nop\nstop 7", 2),
    ("nop\nnop\nwait 100\nstop", 3),
])
def test_scheduled_fifo_overflow_records_the_rejected_input_at_its_own_edge(program, line):
    playback = session(program, schedule((8, 9, 81), (8, 5, 25)), alignment_mode="manual:-4",
                       feedback_queue_capacity=1)
    ir = execute(playback)
    rejection, = [item for item in ir["events"] if item["kind"] == "feedback_rejected"]
    overflow, = [item for item in ir["diagnostics"] if item["category"] == "feedback_queue_overflow"]
    assert rejection["t0"] == rejection["t1"]
    assert rejection["t0"]["value"] == 8 and rejection["duration"]["value"] == 0
    meta = rejection["meta"]
    assert (meta["local_t0"], meta["aligned_t0"], meta["capacity"], meta["reason"]) == (8, 4, 1, "queue_full")
    rejected = ir["playback"]["external_inputs"]["feedback"][0]["arrivals"][1]
    assert (meta["id"], meta["input_event_id"]) == (rejected["id"], rejected["event_id"])
    assert overflow["related_events"] == [rejection["id"]]
    assert rejection["source"] == overflow["source"] == ir["source_map"]["by_event_id"][rejection["id"]]
    assert rejection["source"]["line"] == line
    assert overflow["details"]["input_event_id"] == rejected["event_id"]
    assert overflow["details"]["display_message"] == "LINQ feedback queue overflow (capacity 1)."
    assert ir["playback"]["status"] == ir["execution"]["seq"]["status"] == "error"
    assert ir["playback"]["reason"] == ir["execution"]["seq"]["reason"] == overflow["message"]
    assert overflow["message"] == "LINQ feedback queue overflow at Q1 8 ns (capacity 1)."
    assert not ir["playback"]["feedback"] and not ir["register_history"]
    assert "stop_code" not in ir["execution"]["seq"]
    assert not any(item["kind"] in {"stop", "unknown_region"} for item in ir["events"])
    assert [item["id"] for item in playback.runners[0].scheduled_feedback.pending] == [9]


@pytest.mark.parametrize("first,arrival,capacity,expected_steps", [
    (4, 4, 1, 1),
    (5, 6, 1, 2),
    (8, 8, 1, 2),
    (5, 9, 1, None),
    (5, 6, 2, None),
])
def test_scheduled_fifo_accepts_arrivals_until_stop_completion(first, arrival, capacity, expected_steps):
    playback = session("nop\nstop 7", schedule((first, 9, 81), (arrival, 5, 25)), feedback_queue_capacity=capacity)
    ir = execute(playback)
    execution = ir["execution"]["seq"]
    overflows = [item for item in ir["diagnostics"] if item["category"] == "feedback_queue_overflow"]
    stops = [item for item in ir["events"] if item["kind"] == "stop"]
    if expected_steps is None:
        assert ir["playback"]["status"] == execution["status"] == "completed"
        assert not overflows
        assert len(stops) == 1 and execution["stop_code"] == 7
        assert "terminal" not in execution
    else:
        assert ir["playback"]["status"] == execution["status"] == "error"
        assert ir["playback"]["steps"] == execution["steps"] == expected_steps
        assert not stops and "stop_code" not in execution and "stop_source" not in execution
        overflow, = overflows
        assert overflow["source"]["line"] == 2
        assert overflow["details"]["input_event_id"] == "external-linq:seq:1"
        assert f"{arrival} ns" in overflow["message"]
        assert execution["terminal"] == {"local_time_ns": arrival, "category": "feedback_queue_overflow",
                                         "display_reason": overflow["details"]["display_message"], "core": "q1"}
        issues = [item for item in ir["events"] if item["kind"] == "q1_issue"]
        assert [item["meta"]["op"] for item in issues] == ["nop", "stop"][:expected_steps]
        assert all("stop_code" not in item["meta"] for item in issues)
        assert issues[-1]["t1"]["value"] == arrival
        if expected_steps == 2 and arrival < 8:
            assert issues[-1]["meta"]["planned_q1_end_ns"] == 8 and issues[-1]["meta"]["observation_cutoff_ns"] == arrival
        else:
            assert "observation_cutoff_ns" not in issues[-1]["meta"]


@pytest.mark.parametrize("arrival", [16, 20])
def test_play_feedback_overflow_clips_inflight_q1_but_keeps_register_history(arrival):
    source = "move 3,R0\nmove 7,R2\nadd R0,1,R1\nstop"
    playback = session(source, schedule((arrival, 9, 81), (arrival, 5, 25)), alignment_mode="manual:-4",
                       feedback_queue_capacity=1)
    ir = execute(playback)
    issues = [item for item in ir["events"] if item["kind"] == "q1_issue"]
    rejection, = [item for item in ir["events"] if item["kind"] == "feedback_rejected"]
    overflow, = [item for item in ir["diagnostics"] if item["category"] == "feedback_queue_overflow"]
    assert [issue["meta"]["op"] for issue in issues] == ["move", "move", "add"]
    add = issues[-1]
    assert (add["t0"]["value"], add["t1"]["value"], add["duration"]["value"]) == (8, arrival, arrival - 8)
    assert add["meta"]["aligned_t0"] == 4 and add["meta"]["aligned_t1"] == rejection["meta"]["aligned_t0"] == arrival - 4
    if arrival == 16:
        assert add["meta"]["planned_q1_end_ns"] == 20 and add["meta"]["observation_cutoff_ns"] == 16
    else:
        assert "planned_q1_end_ns" not in add["meta"] and "observation_cutoff_ns" not in add["meta"]
    assert "pending_register_ready_ns" not in add["meta"]
    assert [(item["register"], item["value"]["value"]) for item in ir["register_history"]] == [("R0", 3)]
    state = playback.runners[0].state
    assert state.q1_time_ns.value == arrival
    assert {item.register for item in state.pending_register_writes} == {"R1", "R2"}
    assert overflow["source"]["line"] == 4 and overflow["related_events"] == [rejection["id"]]
    assert overflow["message"] == ir["playback"]["reason"] == f"LINQ feedback queue overflow at Q1 {arrival} ns (capacity 1)."


def test_play_feedback_overflow_before_first_issue_does_not_create_one():
    playback = session("move 3,R0\nstop", schedule((0, 9, 81), (0, 5, 25)), feedback_queue_capacity=1)
    ir = execute(playback)
    assert ir["playback"]["status"] == "error" and ir["playback"]["steps"] == 0
    assert not any(item["kind"] == "q1_issue" for item in ir["events"])
    rejection, = [item for item in ir["events"] if item["kind"] == "feedback_rejected"]
    assert rejection["t0"]["value"] == 0 and rejection["source"]["line"] == 1
    assert ir["execution"]["seq"]["terminal"] == {"local_time_ns": 0, "category": "feedback_queue_overflow",
                                                    "display_reason": "LINQ feedback queue overflow (capacity 1).", "core": "q1"}


def test_play_feedback_overflow_keeps_the_analytical_rt_schedule():
    playback = session("wait 100\nnop\nstop", schedule((2, 9, 81), (2, 5, 25)), feedback_queue_capacity=1)
    ir = execute(playback)
    issue, = [item for item in ir["events"] if item["kind"] == "q1_issue"]
    wait, = [item for item in ir["events"] if item["kind"] == "wait"]
    packet, = playback.runners[0].state.rt_packets
    assert issue["t1"]["value"] == 2 and issue["meta"]["planned_q1_end_ns"] == 4
    assert wait["t1"]["value"] == packet.rt_t1.value == 100 and packet.q1_issue_t1.value == 4
    assert "observation_cutoff_ns" not in wait["meta"]


def test_play_q1_terminal_exports_raw_edge_without_ending_analytical_rt():
    source = "wait 100\nmove 3,R0\nnop\nadd R0,1,R1\nstop"
    ir = execute(session(source, schedule((16, 9, 81), (16, 5, 25)), alignment_mode="manual:-4", feedback_queue_capacity=1))
    execution = ir["execution"]["seq"]
    terminal = execution["terminal"]
    assert execution["mode"] == "playback" and execution["status"] == "error"
    assert terminal == {"local_time_ns": 16, "category": "feedback_queue_overflow",
                        "display_reason": "LINQ feedback queue overflow (capacity 1).", "core": "q1"}
    assert execution["reason"] == ir["playback"]["reason"] == "LINQ feedback queue overflow at Q1 16 ns (capacity 1)."
    wait, = [item for item in ir["events"] if item["kind"] == "wait"]
    add, = [item for item in ir["events"] if item["kind"] == "q1_issue" and item["meta"]["op"] == "add"]
    reject, = [item for item in ir["events"] if item["kind"] == "feedback_rejected"]
    assert add["meta"]["aligned_t0"] == 8 and add["meta"]["aligned_t1"] == reject["meta"]["aligned_t0"] == 12
    assert wait["t1"]["value"] == 100 and wait["meta"]["aligned_t1"] == 96
    assert [(write["register"], write["value"]["value"]) for write in ir["register_history"]] == [("R0", 3)]
    diagnostic, = [item for item in ir["diagnostics"] if item["category"] == terminal["category"]]
    assert diagnostic["related_events"] == [reject["id"]] and diagnostic["source"]["line"] == 5


def test_scheduled_feedback_keeps_register_readiness():
    ir = execute(session("fb_pull_data R0,R1\nmove R1,R2\nstop", schedule((20, 9, 81))))
    assert ir["playback"]["status"] == "error"
    assert any(item["category"] == "register_not_ready" for item in ir["diagnostics"])
    execution = ir["execution"]["seq"]
    assert execution["source"]["line"] == 2
    assert execution["terminal"] == {"local_time_ns": 28, "category": "playback_boundary",
                                     "display_reason": execution["reason"], "core": "q1"}


@pytest.mark.parametrize("invalid", [
    {"target": "other"}, {"arrivals": None}, {"arrivals": [{"time_ns": -1, "id": 1, "value": 2}]},
    {"arrivals": [{"time_ns": 2**53, "id": 1, "value": 2}]},
    {"arrivals": [{"time_ns": 0, "id": 256, "value": 2}]},
    {"arrivals": [{"time_ns": 0, "id": 1, "value": True}]},
    {"arrivals": [{"time_ns": 0, "id": 1, "value": 2**32}]},
])
def test_invalid_feedback_arrivals_leave_active_scenario_unchanged(invalid):
    playback = session("fb_pull_data R0,R1\nnop\nstop", schedule((0, 9, 81)))
    before = execute(playback)
    with pytest.raises(ValueError, match=r"external_inputs\.feedback"):
        playback.set_external_inputs({"feedback": [{**schedule()["feedback"][0], **invalid}]})
    assert playback.snapshot() == before


def test_duplicate_receiver_is_rejected_and_unsorted_times_are_supported():
    with pytest.raises(ValueError, match="duplicates"):
        validate_external_inputs({"feedback": schedule()["feedback"] * 2}, ["seq"])
    ir = execute(session("fb_pull_data R0,R1\nfb_pull_data R2,R3\nnop\nstop", schedule((12, 9, 81), (4, 5, 25))))
    assert [item["id"] for item in ir["playback"]["feedback"]] == [5, 9]
