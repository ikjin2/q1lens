from __future__ import annotations

import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.render.html import render_html


def analyze(source: str, previews: dict[str, int]):
    return interpret_program(
        parse_q1asm(source, file="branch-loops.q1asm"),
        sequencer_id="seq0",
        branch_policy="explore_both_with_depth_limit",
        loop_preview_counts=previews,
    )


@pytest.mark.parametrize("visible_iterations", [1, 2])
def test_compared_loop_members_and_preview_origins_reference_their_own_path(visible_iterations: int) -> None:
    state = analyze(
        "move 2,R1\nnop\njlt R0,1,@taken\n"
        "continued: set_freq 4\nupd_param 4\nloop R1,@continued\njmp @end\n"
        "taken: set_freq 8\nupd_param 8\nloop R1,@taken\nend: stop",
        {
            "seq0:loop:branch-loops.q1asm:4:6": visible_iterations,
            "seq0:loop:branch-loops.q1asm:8:10": visible_iterations,
        },
    )
    by_id = {event.id: event for event in state.events}
    loops = [event for event in state.events if event.kind == "loop_block"]
    assert len(loops) == 2
    for loop in loops:
        path = loop.meta["branch_comparison_path"]
        assert loop.meta["shown_iterations"] == list(range(visible_iterations))
        assert loop.meta["first_iteration_event_ids"] == loop.meta["preview_iteration_event_ids"]["0"]
        for iteration, members in loop.meta["preview_iteration_event_ids"].items():
            assert members
            for event_id in members:
                member = by_id[event_id]
                assert event_id.endswith(f":branch-{path}")
                assert member.meta["branch_comparison_path"] == path
                assert member.meta["loop_iteration_index"] == int(iteration)
        summaries = [event for event in state.events if event.kind == "loop_iteration_preview"
                     and event.meta["branch_comparison_path"] == path]
        assert len(summaries) == visible_iterations
        for summary in summaries:
            assert summary.meta["event_ids"] == loop.meta["preview_iteration_event_ids"][str(summary.meta["iteration_index"])]

    previews = [event for event in state.events if "preview_source_event_id" in event.meta]
    assert bool(previews) == (visible_iterations > 1)
    for preview in previews:
        original = by_id[preview.meta["preview_source_event_id"]]
        assert original.source == preview.source
        assert original.kind == preview.kind
        assert original.meta["loop_iteration_index"] == 0
        assert original.meta["branch_comparison_path"] == preview.meta["branch_comparison_path"]


def test_same_source_loop_in_both_paths_keeps_one_preview_key_but_distinct_event_members() -> None:
    preview_key = "seq0:loop:branch-loops.q1asm:5:7"
    state = analyze(
        "move 2,R1\nnop\njlt R0,1,@body\nwait 4\n"
        "body: set_freq 4\nupd_param 4\nloop R1,@body\nstop",
        {preview_key: 2},
    )
    loops = [event for event in state.events if event.kind == "loop_block"]
    assert len(loops) == 2
    assert {loop.meta["loop_preview_key"] for loop in loops} == {preview_key}
    assert all(loop.meta["shown_iterations"] == [0, 1] for loop in loops)
    members = [{event_id for ids in loop.meta["preview_iteration_event_ids"].values() for event_id in ids}
               for loop in loops]
    assert members[0].isdisjoint(members[1])
    assert members[0] | members[1] <= {event.id for event in state.events}


def test_loop_members_that_precede_the_branch_keep_their_shared_event_ids() -> None:
    state = analyze(
        "move 2,R1\nnop\nbody: set_freq 4\njlt R0,1,@target\n"
        "upd_param 4\ntarget: wait 4\nloop R1,@body\nstop",
        {"seq0:loop:branch-loops.q1asm:3:7": 2},
    )
    by_id = {event.id: event for event in state.events}
    shared = {event.id for event in state.events if event.source.line == 3 and "branch_comparison_path" not in event.meta}
    assert shared
    loops = [event for event in state.events if event.kind == "loop_block"]
    assert len(loops) == 2
    for loop in loops:
        assert shared <= set(loop.meta["first_iteration_event_ids"])
        for members in loop.meta["preview_iteration_event_ids"].values():
            for event_id in members:
                member = by_id[event_id]
                assert member.meta.get("branch_comparison_path") in (None, loop.meta["branch_comparison_path"])


@pytest.mark.parametrize("loop_spans_branch", [False, True])
def test_compared_loop_diagnostics_reference_existing_events_and_render(loop_spans_branch: bool) -> None:
    source = (
        "move 2,R1\nnop\nbody: set_freq 4\njlt R0,1,@target\n"
        "upd_param 4\ntarget: wait 4\nloop R1,@body\nstop"
        if loop_spans_branch else
        "move 2,R1\nnop\njlt R0,1,@body\nwait 4\n"
        "body: set_freq 4\nupd_param 4\nloop R1,@body\nstop"
    )
    state = analyze(source, {})
    by_id = {event.id: event for event in state.events}
    shared = {event.id for event in state.events if event.source.line == 3 and "branch_comparison_path" not in event.meta}
    diagnostics = [diagnostic for diagnostic in state.diagnostics if diagnostic.category == "loop_truncated"]
    assert len(diagnostics) == 2
    for diagnostic in diagnostics:
        path = diagnostic.details["branch_comparison_path"]
        assert diagnostic.related_events
        assert any(event_id.endswith(f":branch-{path}") for event_id in diagnostic.related_events)
        if loop_spans_branch:
            assert shared <= set(diagnostic.related_events)
        for event_id in diagnostic.related_events:
            assert by_id[event_id].meta.get("branch_comparison_path") in (None, path)

    assert "<html" in render_html(timeline_ir_from_states([state]))
