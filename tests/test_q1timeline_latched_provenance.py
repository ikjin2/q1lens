from __future__ import annotations

import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


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


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation"])
def test_register_frequency_and_marker_decode_only_for_latches(mode) -> None:
    source = "move -4000000,R1\nmove 17,R2\nnop\nset_freq R1\nset_mrk R2\nupd_param 4\nstop"
    if mode == "simulation":
        state, = simulate_programs({"seq0": parse_q1asm(source, file="latched.q1asm")})
    else:
        state = analyze(source, execution_mode=mode)
    raw_words = {"R1": 4290967296, "R2": 17}
    expected = {"frequency": -4000000, "marker": 1}
    update, = events(state, "upd_param")
    setters = events(state, "latched_state_pending")
    issues = {event.id: event for event in events(state, "q1_issue")}

    assert {write.register: write.value for write in state.register_history} == {
        register: Concrete(value) for register, value in raw_words.items()
    }
    assert update.meta["applied_state"] == expected
    assert update.meta["applied_setter_event_ids"] == {setter.meta["field"]: setter.id for setter in setters}
    for setter, register, line in zip(setters, raw_words, (4, 5), strict=True):
        assert setter.meta["value"] == expected[setter.meta["field"]]
        issue = issues[setter.meta["q1_issue_event_id"]]
        assert issue.source == setter.source
        assert setter.source.line == line
        assert issue.meta["register_reads"][register]["value"] == raw_words[register]


def test_immediate_frequency_and_marker_match_decoded_register_values() -> None:
    state = analyze("set_freq -4000000\nset_mrk 1\nupd_param 4\nstop")
    update, = events(state, "upd_param")

    assert update.meta["applied_state"] == {"frequency": -4000000, "marker": 1}


def test_compact_loop_keeps_decoded_invariant_register_setters() -> None:
    source = ("move 3,R0\nmove -4000000,R1\nmove 17,R2\nnop\nbody: set_freq R1\nset_mrk R2\n"
              "upd_param 4\nloop R0,@body\nstop")
    state = analyze(source, loop_preview_counts={"seq0:loop:latched.q1asm:5:8": 3})
    setters = {event.id: event for event in events(state, "latched_state_pending")}
    updates = events(state, "upd_param")

    assert len(state.rt_packets) == 1
    assert [update.t0 for update in updates] == [Concrete(0), Concrete(4), Concrete(8)]
    for update in updates:
        assert update.meta["applied_state"] == {"frequency": -4000000, "marker": 1}
        assert set(update.meta["applied_setter_event_ids"]) == {"frequency", "marker"}
        for field, setter_id in update.meta["applied_setter_event_ids"].items():
            setter = setters[setter_id]
            assert setter.meta["value"] == update.meta["applied_state"][field]
            assert setter.meta["loop_iteration_index"] == update.meta["loop_iteration_index"]


def test_unknown_frequency_and_marker_operands_stay_unknown() -> None:
    state = analyze("set_freq R1\nset_mrk R2\nupd_param 4\nstop")
    update, = events(state, "upd_param")
    setters = events(state, "latched_state_pending")

    assert update.meta["applied_setter_event_ids"] == {setter.meta["field"]: setter.id for setter in setters}
    for field, register in (("frequency", "R1"), ("marker", "R2")):
        value = update.meta["applied_state"][field]
        assert value["kind"] == "unknown"
        assert value["reason"] == register


@pytest.mark.parametrize(("mask", "gap", "expected_marker"), [
    ("0xfffffff0", "nop\n", 1),
    ("0xfffffff8", "nop\n", None),
    ("0xfffffff0", "", None),
], ids=["known-mask", "partially-known-mask", "register-not-ready"])
def test_symbolic_marker_requires_all_four_bits_and_a_ready_register(mask, gap, expected_marker) -> None:
    state = analyze(f"and R7,{mask},R1\nnop\nor R1,1,R1\n{gap}set_mrk R1\nupd_param 4\nstop")
    setter, = events(state, "latched_state_pending")
    update, = events(state, "upd_param")
    issue = next(event for event in events(state, "q1_issue") if event.id == setter.meta["q1_issue_event_id"])
    raw_value = issue.meta["register_reads"]["R1"]

    assert raw_value["kind"] == "symbolic"
    assert setter.meta["value"] == (raw_value if expected_marker is None else expected_marker)
    assert update.meta["applied_state"]["marker"] == setter.meta["value"]
    assert update.meta["applied_setter_event_ids"] == {"marker": setter.id}
    assert any(diagnostic.category == "register_not_ready" for diagnostic in state.diagnostics) == (not gap)


@pytest.mark.parametrize(("setter", "first", "second", "register", "decoded"), [
    ("set_awg_gain R5,R6", 32768, -1, "R5", 32768),
    ("set_awg_offs R5,R6", 1, -32769, "R6", -32769),
    ("set_ph R5", 1000000001, 0, "R5", 1000000001),
    ("set_ph_delta R5", -1, 0, "R5", 0xFFFFFFFF),
])
def test_unsupported_latched_register_preserves_the_observed_prefix(setter, first, second, register, decoded):
    source = f"wait 8\nmove {first},R5\nmove {second},R6\nnop\n{setter}\nupd_param 4\nstop"
    state = analyze(source)
    wait, = events(state, "wait")
    boundary, = events(state, "unknown_region")
    issue = next(event for event in events(state, "q1_issue") if event.id == boundary.meta["q1_issue_event_id"])
    diagnostic, = [item for item in state.diagnostics if item.category == "analysis_incomplete"]

    assert (wait.t0, wait.t1) == (Concrete(0), Concrete(8))
    assert boundary.t0 == state.rt_time_ns == wait.t1
    assert boundary.t1 is None and boundary.confidence == "unknown"
    assert boundary.meta["q1_proof_cutoff"] is True
    assert issue.source == boundary.source == diagnostic.source
    assert issue.source.line == 5 and issue.meta["op"] == setter.split()[0]
    assert issue.meta["register_reads"][register]["value"] == decoded & 0xFFFFFFFF
    assert diagnostic.details["register"] == register and diagnostic.details["value"] == decoded
    assert diagnostic.related_events == [boundary.id]
    assert [packet.op for packet in state.rt_packets] == ["wait"]
    assert not events(state, "latched_state_pending")
    assert not events(state, "upd_param") and not events(state, "stop")


@pytest.mark.parametrize(("setter", "first", "second", "expected"), [
    ("set_awg_gain R5,R6", -32768, 32767, {"awg_gain": [-32768, 32767]}),
    ("set_awg_offs R5,R6", 32767, -32768, {"awg_offset": [32767, -32768]}),
    ("set_ph R5", 1000000000, 0, {"phase": 1000000000}),
    ("set_ph_delta R5", 1000000000, 0, {"phase_delta": 1000000000}),
])
def test_latched_register_range_boundaries_still_apply(setter, first, second, expected):
    source = f"wait 8\nmove {first},R5\nmove {second},R6\nnop\n{setter}\nupd_param 4\nstop"
    state = analyze(source)
    concrete = analyze(source, execution_mode="concrete")
    update, = events(state, "upd_param")
    stop, = events(state, "stop")

    assert update.meta["applied_state"] == events(concrete, "upd_param")[0].meta["applied_state"] == expected
    assert stop.t0 == Concrete(12)
    assert not events(state, "unknown_region")


def test_latched_range_check_does_not_use_an_unready_register_write():
    state = analyze("wait 8\nmove 1000000001,R6\nset_ph R6\nupd_param 4\nstop")
    update, = events(state, "upd_param")

    assert update.meta["applied_state"]["phase"]["kind"] == "unknown"
    assert any(item.category == "register_not_ready" and item.details.get("register") == "R6"
               for item in state.diagnostics)
    assert not events(state, "unknown_region")


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


def test_varying_loop_preview_setter_does_not_repeat_first_value() -> None:
    source = ("move 3,R0\nmove 100,R1\nnop\nbody: set_freq R1\nset_mrk 3\nupd_param 8\n"
              "add R1,10,R1\nnop\nloop R0,@body\nstop")
    state = analyze(source, loop_preview_counts={"seq0:loop:latched.q1asm:4:9": 3})
    concrete = analyze(source, execution_mode="concrete")
    by_id = {event.id: event for event in state.events}
    updates = events(state, "upd_param")

    assert [event.t0 for event in updates] == [Concrete(0), Concrete(8), Concrete(16)]
    assert [event.meta["applied_state"]["frequency"] for event in events(concrete, "upd_param")] == [100, 110, 120]
    assert updates[0].meta["applied_state"]["frequency"] == 100
    assert len(state.rt_packets) == 1
    for index, update in enumerate(updates):
        assert update.meta["applied_state"]["marker"] == 3
        setter = by_id[update.meta["applied_setter_event_ids"]["frequency"]]
        assert setter.meta["loop_iteration_index"] == index
        assert setter.meta["value"] == update.meta["applied_state"]["frequency"]
        if index:
            assert setter.meta["value"]["kind"] == "unknown"


@pytest.mark.parametrize("setter,field,value", [("reset_ph", "phase_reset", True),
                                                ("set_ph_delta 250000000", "phase_delta", 250000000),
                                                ("set_freq 100", "frequency", 100)])
@pytest.mark.parametrize("inside_loop", [False, True])
def test_entry_setter_is_not_reapplied_by_copied_loop_updates(setter, field, value, inside_loop):
    body = f"body: {setter}\nupd_param 8" if inside_loop else f"{setter}\nbody: upd_param 8"
    source = f"move 3,R0\n{body}\nloop R0,@body\nstop"
    start = 2 if inside_loop else 3
    state = analyze(source, loop_preview_counts={f"seq0:loop:latched.q1asm:{start}:4": 3})
    concrete = analyze(source, execution_mode="concrete")
    updates = events(state, "upd_param")
    assert len(updates) == 3
    for index, (preview, actual) in enumerate(zip(updates, events(concrete, "upd_param"))):
        applied = {field: value} if index == 0 or inside_loop else {}
        assert preview.meta.get("applied_state", {}) == actual.meta.get("applied_state", {}) == applied
        assert set(preview.meta.get("applied_setter_event_ids", {})) == set(applied)
        issue = next(event for event in state.events if event.id == preview.meta["q1_issue_event_id"])
        for event in (preview, issue):
            assert bool(event.meta.get("phase_reset")) == (field == "phase_reset" and bool(applied))
            assert event.meta["captured_phase_reset"] == (field == "phase_reset" and bool(applied))
            assert ("nco_state_at_issue" in event.meta) == (field == "phase_reset" and bool(applied))


@pytest.mark.parametrize("entry", ["", "set_freq 4\n"])
def test_trailing_setter_links_to_the_preceding_preview_occurrence(entry):
    source = f"move 3,R0\n{entry}body: upd_param 8\nset_freq 8\nloop R0,@body\nstop"
    start = 3 if entry else 2
    state = analyze(source, loop_preview_counts={f"seq0:loop:latched.q1asm:{start}:{start + 2}": 3})
    concrete = analyze(source, execution_mode="concrete")
    updates = events(state, "upd_param")
    setters = [event for event in events(state, "latched_state_pending") if event.meta["value"] == 8]

    assert len(updates) == len(setters) == 3
    assert [event.meta.get("applied_state", {}) for event in updates] == [
        event.meta.get("applied_state", {}) for event in events(concrete, "upd_param")
    ]
    assert [event.meta["applied_setter_event_ids"]["frequency"] for event in updates[1:]] == [
        event.id for event in setters[:2]
    ]


def test_varying_trailing_setter_preserves_only_the_observed_carry_value():
    source = ("move 3,R0\nmove 100,R1\nnop\nbody: upd_param 8\nset_freq R1\n"
              "add R1,10,R1\nnop\nloop R0,@body\nstop")
    state = analyze(source, loop_preview_counts={"seq0:loop:latched.q1asm:4:8": 3})
    first, second, third = events(state, "upd_param")
    original_setter, preview_setter, _ = events(state, "latched_state_pending")

    assert "applied_state" not in first.meta
    assert second.meta["applied_state"]["frequency"] == 100
    assert second.meta["applied_setter_event_ids"]["frequency"] == original_setter.id
    assert third.meta["applied_state"]["frequency"]["kind"] == "unknown"
    assert third.meta["applied_setter_event_ids"]["frequency"] == preview_setter.id


def test_loop_preview_preserves_the_invariant_component_of_a_setter():
    source = ("move 2,R0\nmove 100,R1\nmove 200,R2\nnop\nbody: set_awg_gain R1,R2\nupd_param 8\n"
              "add R1,10,R1\nnop\nloop R0,@body\nstop")
    state = analyze(source, loop_preview_counts={"seq0:loop:latched.q1asm:5:9": 2})
    first, second = events(state, "upd_param")

    assert first.meta["applied_state"]["awg_gain"] == [100, 200]
    left, right = second.meta["applied_state"]["awg_gain"]
    assert left["kind"] == "unknown" and right == 200


def test_skipped_loop_preview_discards_a_captured_entry_phase_action():
    source = ("move 2,R0\nset_freq 100\nreset_ph\nbody: set_cond 1,0,0,8\nupd_param 8\n"
              "set_cond 0,0,0,4\nloop R0,@body\nstop")
    state = analyze(source, loop_preview_counts={"seq0:loop:latched.q1asm:4:7": 2})
    first, second = events(state, "conditional_skip")

    assert first.meta["captured_state"] == {"frequency": 100, "phase_reset": True}
    assert first.meta["captured_phase_reset"] is True
    assert second.meta["captured_state"] == {"frequency": 100}
    assert second.meta["captured_setter_event_ids"] == {"frequency": first.meta["captured_setter_event_ids"]["frequency"]}
    assert second.meta["captured_phase_reset"] is False
    assert second.meta["applied_state"] == {}


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
    state = analyze("set_cond 0,1,0,4\nset_time_ref\nupd_param 4\nstop")
    assert len(events(state, "latched_state_pending")) == 2
    assert all("q1_issue_event_id" in event.meta for event in events(state, "latched_state_pending"))
    assert "applied_setter_event_ids" not in events(state, "upd_param")[0].meta
