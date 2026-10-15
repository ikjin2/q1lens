from __future__ import annotations

import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.q1asm.parser import parse_q1asm


def analyze(source: str, **kwargs):
    return interpret_program(parse_q1asm(source, file="latched.q1asm"), sequencer_id="seq0", **kwargs)


def events(state, kind):
    return [event for event in state.events if event.kind == kind]


@pytest.mark.parametrize("mode", ["analysis", "concrete"])
@pytest.mark.parametrize("instruction", ["upd_param 4", "play 0,1,4", "acquire 0,0,4"])
def test_applied_fields_link_to_setters_and_their_q1_issues(mode, instruction) -> None:
    state = analyze(f"set_freq 4\nset_awg_gain 100,200\n{instruction}\nstop", execution_mode=mode)
    by_id = {event.id: event for event in state.events}
    setters = events(state, "latched_state_pending")
    expected_ids = {event.meta["field"]: event.id for event in setters}
    applied = [event for event in state.events if "applied_state" in event.meta]

    assert len(applied) == (2 if instruction.startswith("play") else 1)
    for setter in setters:
        issue = by_id[setter.meta["q1_issue_event_id"]]
        assert issue.kind == "q1_issue"
        assert issue.source == setter.source
    for event in applied:
        assert event.meta["applied_setter_event_ids"] == expected_ids
        assert event.meta["applied_state"] == {"frequency": 4, "awg_gain": [100, 200]}
        issue = by_id[event.meta["q1_issue_event_id"]]
        assert issue.kind == "q1_issue"
        assert issue.meta["rt_packet_id"] == event.meta["rt_packet_id"]
        assert issue.source == event.source


@pytest.mark.parametrize("mode", ["analysis", "concrete"])
def test_overwritten_setter_is_not_applied_and_wait_does_not_consume_it(mode) -> None:
    state = analyze("set_freq 4\nset_freq 8\nwait 12\nupd_param 4\nupd_param 4\nstop", execution_mode=mode)
    first, second = events(state, "latched_state_pending")
    update, unchanged = events(state, "upd_param")

    assert update.meta["applied_setter_event_ids"] == {"frequency": second.id}
    assert first.id not in update.meta["applied_setter_event_ids"].values()
    assert update.meta["applied_state"] == {"frequency": 8}
    assert "applied_setter_event_ids" not in events(state, "wait")[0].meta
    assert "applied_setter_event_ids" not in unchanged.meta
    assert not state.latched_state.pending_event_ids


def test_concrete_loop_links_each_repeated_source_occurrence() -> None:
    state = analyze("move 2,R0\nnop\nbody: set_freq 4\nupd_param 4\nloop R0,@body\nstop", execution_mode="concrete")
    setters = events(state, "latched_state_pending")
    updates = events(state, "upd_param")

    assert len(setters) == len(updates) == 2
    assert setters[0].source == setters[1].source
    assert setters[0].meta["q1_issue_event_id"] != setters[1].meta["q1_issue_event_id"]
    assert [event.meta["applied_setter_event_ids"]["frequency"] for event in updates] == [event.id for event in setters]


def test_loop_preview_links_stay_in_the_same_preview_iteration() -> None:
    state = analyze("move 2,R0\nnop\nbody: set_freq 4\nupd_param 4\nloop R0,@body\nstop",
                    loop_preview_counts={"seq0:loop:latched.q1asm:3:5": 2})
    by_id = {event.id: event for event in state.events}
    updates = events(state, "upd_param")

    assert len(updates) == 2
    for update in updates:
        setter = by_id[update.meta["applied_setter_event_ids"]["frequency"]]
        issue = by_id[setter.meta["q1_issue_event_id"]]
        packet_issue = by_id[update.meta["q1_issue_event_id"]]
        assert setter.meta["loop_iteration_index"] == update.meta["loop_iteration_index"]
        assert issue.meta["loop_iteration_index"] == update.meta["loop_iteration_index"]
        assert packet_issue.meta["loop_iteration_index"] == update.meta["loop_iteration_index"]
        assert packet_issue.source == update.source


def test_branch_comparison_remaps_local_setters_but_preserves_shared_setters() -> None:
    state = analyze("set_freq 4\njlt R0,1,@taken\nset_freq 8\nupd_param 4\njmp @end\ntaken: upd_param 4\nend: stop",
                    branch_policy="explore_both_with_depth_limit")
    by_id = {event.id: event for event in state.events}
    original = events(state, "latched_state_pending")[0]
    updates = events(state, "upd_param")

    assert len(updates) == 2
    for update in updates:
        setter = by_id[update.meta["applied_setter_event_ids"]["frequency"]]
        issue = by_id[setter.meta["q1_issue_event_id"]]
        packet_issue = by_id[update.meta["q1_issue_event_id"]]
        assert issue.source == setter.source
        assert packet_issue.meta["branch_comparison_path"] == update.meta["branch_comparison_path"]
        assert packet_issue.source == update.source
        if update.meta["branch_comparison_path"] == "taken":
            assert setter.id == original.id
        else:
            assert setter.meta["branch_comparison_path"] == "fallthrough"
            assert issue.meta["branch_comparison_path"] == "fallthrough"


def test_unknown_duration_does_not_fabricate_an_application_link() -> None:
    state = analyze("set_freq 4\nupd_param R0\nupd_param 4\nstop")
    setter, = events(state, "latched_state_pending")
    unknown, = events(state, "unknown_region")
    update, = events(state, "upd_param")

    assert "applied_setter_event_ids" not in unknown.meta
    assert update.meta["applied_setter_event_ids"] == {"frequency": setter.id}


def test_control_latches_do_not_get_output_application_links() -> None:
    state = analyze("set_cond 1,1,0,4\nset_time_ref\nupd_param 4\nstop")
    assert len(events(state, "latched_state_pending")) == 2
    assert all("q1_issue_event_id" in event.meta for event in events(state, "latched_state_pending"))
    assert "applied_setter_event_ids" not in events(state, "upd_param")[0].meta
