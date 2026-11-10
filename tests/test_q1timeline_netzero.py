import pytest

from q1timeline.analysis.api import analyze_documents, analyze_text


def _warnings(ir):
    return [item for item in ir["diagnostics"] if item["category"] == "possible_netzero_overlap"]


@pytest.mark.parametrize("waits, elapsed_ns", [("", 1000), ("wait 65000\nwait 33996\n", 99996)])
def test_reports_short_rt_gap_once_for_both_play_paths(waits, elapsed_ns):
    ir = analyze_text(f"play_netzero 100000,1000\n{waits}play 0,1,1000\nstop\n")

    warning, = _warnings(ir)
    assert warning["severity"] == "warning"
    assert warning["source"]["line"] == 2 + waits.count("\n")
    assert warning["details"]["maxt_ns"] == 100000
    assert warning["details"]["elapsed_ns"] == elapsed_ns
    assert warning["details"]["shortfall_ns"] == 100000 - elapsed_ns
    events = {event["id"]: event["kind"] for event in ir["events"]}
    assert [events[event_id] for event_id in warning["related_events"]] == ["play_netzero", "play"]


@pytest.mark.parametrize("source", [
    "play_netzero 100000,1000\nwait 65000\nwait 34000\nplay 0,1,1000\nstop\n",
    "play_netzero 100000,1000\nwait 65000\nwait 35000\nplay 0,1,1000\nstop\n",
    "play_netzero 100000,1000\nstop\n",
    "play_netzero R0,1000\nplay 0,1,1000\nstop\n",
    "move 100000,R0\nnop\nmove 0,R0\nplay_netzero R0,1000\nplay 0,1,1000\nstop\n",
    "play_netzero 100000,1000\nset_cond 1,0,0,4\nplay 0,1,4\nstop\n",
    "play_netzero 100000,1000\nwait_trigger 1,4\nplay 0,1,4\nstop\n",
])
def test_no_overlap_warning_without_a_proven_short_gap(source):
    assert not _warnings(analyze_text(source))


def test_captures_register_maxt_and_checks_a_following_compensation_pulse():
    ir = analyze_text("move 8000,R0\nnop\nplay_netzero R0,1000\nmove 0,R0\n"
                      "play_netzero 0,1000\nplay 0,1,1000\nstop\n")

    warning, = _warnings(ir)
    assert warning["source"]["line"] == 5
    assert warning["details"]["shortfall_ns"] == 7000


@pytest.mark.parametrize("count, shortfalls", [(98, [1000]), (99, [])])
def test_counts_all_iterations_of_a_wait_only_loop(count, shortfalls):
    ir = analyze_text(f"move {count},R0\nplay_netzero 100000,1000\n"
                      "hold: wait 1000\nloop R0,@hold\nplay 0,1,1000\nstop\n")

    assert [item["details"]["shortfall_ns"] for item in _warnings(ir)] == shortfalls


def test_checks_the_next_pulse_at_a_compact_loop_back_edge_and_exit():
    ir = analyze_text("move 2,R0\nstart: play 0,1,1000\nplay_netzero 4000,1000\n"
                      "loop R0,@start\nplay 0,1,1000\nstop\n")

    assert [(item["source"]["line"], item["details"]["shortfall_ns"]) for item in _warnings(ir)] == [
        (2, 3000), (5, 3000),
    ]


def test_checks_first_loop_back_edge_when_maxt_changes_between_iterations():
    ir = analyze_text("move 2,R0\nmove 4000,R1\nstart: play 0,1,1000\nplay_netzero R1,1000\n"
                      "sub R1,4000,R1\nloop R0,@start\nplay 0,1,1000\nstop\n")

    warning, = _warnings(ir)
    assert warning["source"]["line"] == 3
    assert warning["details"]["shortfall_ns"] == 3000


def test_compares_each_branch_with_its_own_waits():
    ir = analyze_text("play_netzero 4000,1000\njge R0,1,@early\nwait 3000\n"
                      "early: play 0,1,1000\nstop\n", branch_policy="explore_both_with_depth_limit")

    warning, = _warnings(ir)
    assert warning["details"]["branch_comparison_path"] == "taken"
    assert warning["details"]["shortfall_ns"] == 3000


def test_sequencers_do_not_share_compensation_windows():
    ir = analyze_documents({"a.q1asm": "play_netzero 100000,1000\nstop\n",
                            "b.q1asm": "play 0,1,1000\nstop\n"}, alignment_mode="none")

    assert not _warnings(ir)
