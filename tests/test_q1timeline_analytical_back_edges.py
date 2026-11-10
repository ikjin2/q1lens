import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.values import Concrete, Unknown
from q1timeline.q1asm import parse_q1asm


def _run(source, **options):
    return interpret_program(parse_q1asm(source, file="back_edge.q1asm"), sequencer_id="seq0", **options)


def _events(state, kind, op=None):
    return [event for event in state.events if event.kind == kind and (op is None or event.meta.get("op") == op)]


@pytest.mark.parametrize(("branch", "taken_duration"), [
    ("jnz @body", 16), ("jnz 2", 16), ("jge R0,1,@body", 24), ("jge R0,1,2", 24),
])
def test_known_back_edge_replays_analytical_source_and_rt_packet_occurrences(branch, taken_duration):
    source = f"move 3,R0\nnop\nbody: play 0,1,100\nsub R0,1,R0\nnop\n{branch}\nstop"
    analysis = _run(source, max_steps=8)
    concrete = _run(source, execution_mode="concrete")
    for state in (analysis, concrete):
        branches = _events(state, "q1_issue", branch.split()[0])
        assert [(event.source.line, event.duration, event.meta["branch_taken"]) for event in branches] == [
            (6, Concrete(taken_duration), True), (6, Concrete(taken_duration), True), (6, Concrete(4), False),
        ]
        plays = [event for event in _events(state, "play") if event.meta["path"] == 0]
        assert [(event.source.line, event.t0, event.t1) for event in plays] == [
            (3, Concrete(0), Concrete(100)), (3, Concrete(100), Concrete(200)), (3, Concrete(200), Concrete(300)),
        ]
        packets = [event.meta["rt_packet_id"] for event in _events(state, "q1_issue", "play")]
        assert len(set(packets)) == 3
        assert [event.meta["rt_packet_id"] for event in plays] == packets
        stop, = _events(state, "stop")
        assert stop.t0 == Concrete(300)
        assert state.registers["R0"] == Concrete(0)
        assert not _events(state, "unknown_region")
    if branch.endswith("2"):
        assert [event.meta["target_pc"] for event in _events(analysis, "q1_issue", branch.split()[0])] == [2, 2, 2]


def test_status_back_edge_uses_the_compiled_entry_when_a_macro_shifts_the_address():
    state = _run("move 2,R0\nnop\njge R0,1,@body\n"
                 "body: wait 4\nsub R0,1,R0\nnop\njnz 4\nstop")
    taken, continued = _events(state, "q1_issue", "jnz")
    assert [(event.meta["compiled_target_pc"], event.meta["target_pc"], event.meta["branch_taken"])
            for event in (taken, continued)] == [(4, 3, True), (4, 3, False)]
    stop, = _events(state, "stop")
    assert stop.t0 == Concrete(8)


def test_early_flag_operand_read_does_not_support_following_the_back_edge():
    state = _run("move 1,R0\nnop\nbody: wait 4\nmove 2,R0\ncmp R0,1\njz @body\nstop")
    assert len(_events(state, "wait")) == 1
    assert not _events(state, "stop")
    assert any(diagnostic.category == "register_not_ready" and diagnostic.source.line == 5
               for diagnostic in state.diagnostics)


def test_replayed_back_edge_stops_when_its_status_becomes_unknown():
    source = "move 1,R0\nnop\nbody: wait 4\ncmp R0,1\nfb_pull_data R1,R0\nnop\njz @body\nstop"
    state = _run(source)
    first, second = _events(state, "q1_issue", "jz")
    assert (first.duration, first.meta["branch_taken"]) == (Concrete(16), True)
    assert (first.t1, second.t1) == (Concrete(52), Concrete(96))
    assert second.meta["branch_taken"] == "runtime_dependent"
    unresolved, = [event for event in _events(state, "branch_region") if event.t1 is None]
    assert unresolved.t0 == Concrete(8) and unresolved.meta["q1_issue_event_id"] == second.id
    marker, = _events(state, "unknown_region")
    assert (marker.source.line, marker.t0, marker.meta["reason"]) == (7, Concrete(8), "status_branch")
    assert not _events(state, "stop")

    branch_id = first.meta["branch_id"]
    explicit = _run(source, branch_assumptions={branch_id: "fallthrough"})
    first, second = _events(explicit, "q1_issue", "jz")
    assert first.meta["branch_taken"] is True
    assert second.meta["assumed_branch_path"] == "fallthrough"
    assert second.duration == Concrete(4)
    stop, = _events(explicit, "stop")
    assert stop.t0 == Concrete(8)

    comparison = _run(source, branch_assumptions={branch_id: "both"})
    _, unresolved = _events(comparison, "q1_issue", "jz")
    assert unresolved.meta["branch_comparison_supported"] is False
    assert unresolved.meta["branch_taken"] == "runtime_dependent"
    assert any("comparison needs a known forward target" in diagnostic.message for diagnostic in comparison.diagnostics)


@pytest.mark.parametrize("branch", ["jz @body", "jge R0,1,@body"])
def test_single_unresolved_branch_region_links_to_its_issue(branch):
    state = _run(f"body: wait 4\n{branch}\nstop", branch_policy="explore_both_with_depth_limit")
    issue, = _events(state, "q1_issue", branch.split()[0])
    unresolved, = _events(state, "branch_region")
    assert unresolved.meta["q1_issue_event_id"] == issue.id
    assert unresolved.t0 == Concrete(4) and unresolved.t1 is None


def test_branch_region_issue_ids_follow_their_comparison_path():
    state = _run("cmp R0,1\njz @taken\nwait 4\nstop\ntaken: wait 8\ncmp R2,1\njz @done\nwait 12\nstop\ndone: wait 16\nstop",
                 branch_policy="explore_both_with_depth_limit")
    issues = {event.id: event for event in _events(state, "q1_issue", "jz")}
    origin, nested = _events(state, "branch_region")
    assert issues[origin.meta["q1_issue_event_id"]].source.line == 2
    issue = issues[nested.meta["q1_issue_event_id"]]
    assert issue.source.line == 7
    assert issue.meta["branch_comparison_path"] == nested.meta["branch_comparison_path"] == "taken"


def test_replayed_back_edge_has_a_separate_repeat_budget_and_preserves_the_last_decision():
    state = _run("move 1,R0\nnop\nbody: wait 4\ncmp R0,1\njz @body\nstop", max_steps=6)
    branches = _events(state, "q1_issue", "jz")
    assert [(event.duration, event.meta["branch_taken"]) for event in branches] == [(Concrete(16), True)] * 3
    assert [event.t0 for event in _events(state, "wait")] == [Concrete(0), Concrete(4), Concrete(8)]
    marker, = _events(state, "unknown_region")
    assert marker.t0 == Concrete(12) and marker.source == branches[-1].source
    assert marker.meta["reason"] == "analytical_back_edge_limit"
    assert marker.meta["q1_issue_event_id"] == branches[-1].id
    assert marker.meta["q1_proof_cutoff"] is True
    assert any(marker.id in diagnostic.related_events and "6 repeated instruction" in diagnostic.message
               for diagnostic in state.diagnostics)
    assert not _events(state, "stop")

    linear = _run("nop\n" * 20 + "wait 4\nstop", max_steps=1)
    stop, = _events(linear, "stop")
    assert stop.t0 == Concrete(4)


def test_rt_only_region_with_linked_issue_does_not_end_q1_proof():
    state = _run("wait 2\nnop\nstop")
    issue, = _events(state, "q1_issue", "wait")
    region, = _events(state, "unknown_region")
    assert region.meta["q1_issue_event_id"] == issue.id
    assert "q1_proof_cutoff" not in region.meta
    stop, = _events(state, "q1_issue", "stop")
    assert stop.t0 == Concrete(8)


@pytest.mark.parametrize(("budget", "op", "end"), [(1, "wait", 44), (2, "cmp", 56), (3, "jz", 72)])
def test_replay_cutoff_links_the_last_q1_issue_even_partway_through_the_body(budget, op, end):
    state = _run("move 1,R0\nnop\nbody: wait 4\ncmp R0,1\njz @body\nstop", max_steps=budget)
    issue = _events(state, "q1_issue")[-1]
    marker, = _events(state, "unknown_region")
    assert issue.meta["op"] == op and issue.t1 == Concrete(end)
    assert marker.meta["q1_issue_event_id"] == issue.id and marker.meta["q1_proof_cutoff"] is True
    assert marker.t0 == Concrete(8) and marker.source.line == 5
    diagnostic, = [item for item in state.diagnostics if marker.id in item.related_events]
    assert diagnostic.source.line == 5


def test_replay_cutoff_ignores_copied_q1_preview_occurrences():
    state = _run("move 1,R0\nnop\nbody: move 2,R1\ninner: wait 4\nloop R1,@inner\ncmp R0,1\njz @body\nstop",
                 max_steps=3, loop_preview_counts={"seq0:loop:back_edge.q1asm:4:5": 2})
    last_issue, preview = _events(state, "q1_issue")[-2:]
    assert last_issue.meta["op"] == "loop" and preview.meta["preview_source_event_id"]
    marker, = _events(state, "unknown_region")
    assert marker.meta["q1_issue_event_id"] == last_issue.id
    assert marker.t0 == Concrete(16) and marker.source.line == 7


def test_compact_loop_suffix_does_not_consume_the_back_edge_budget():
    state = _run("move 2,R0\nnop\nbody: wait 8\nsub R0,1,R0\nnop\njnz @body\n"
                 "move 3,R1\ninner: wait 4\nloop R1,@inner\nstop", max_steps=4)
    loop, = _events(state, "loop_block")
    assert loop.source.line == 8
    assert state.registers["R1"] == Concrete(0)
    stop, = _events(state, "stop")
    assert stop.t0 == Concrete(28)


@pytest.mark.parametrize(("counter_setup", "counts"), [("move 2,R1", (2, 2)), ("add R0,1,R1", (3, 2))])
def test_revisited_compact_loop_uses_separate_first_iteration_and_preview_evidence(counter_setup, counts):
    source = (f"move 2,R0\nnop\nbody: {counter_setup}\ninner: wait 4\nloop R1,@inner\n"
              "sub R0,1,R0\nnop\njnz @body\nstop")
    preview_key = "seq0:loop:back_edge.q1asm:4:5"
    state = _run(source, max_steps=6, loop_preview_counts={preview_key: 3})
    concrete = _run(source, execution_mode="concrete")
    first, second = _events(state, "loop_block")
    assert first.meta["loop_id"] != second.meta["loop_id"]
    assert state.registers == concrete.registers == {"R0": Concrete(0), "R1": Concrete(0)}
    assert state.q1_time_ns == concrete.q1_time_ns
    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(4 * sum(counts))
    assert _events(state, "stop")[0].t0 == state.rt_time_ns
    assert not _events(state, "unknown_region")

    events = {event.id: event for event in state.events}
    base_ids = set()
    for loop, count, start in zip((first, second), counts, (0, 4 * counts[0])):
        assert loop.t0 == Concrete(start)
        assert loop.meta["count"] == count
        assert loop.meta["total_duration"]["value"] == 4 * count
        assert loop.meta["loop_preview_key"] == preview_key
        iterations = loop.meta["preview_iteration_event_ids"]
        assert list(iterations) == [str(index) for index in range(count)]
        assert iterations["0"] == loop.meta["first_iteration_event_ids"]
        assert base_ids.isdisjoint(iterations["0"])
        base_ids.update(iterations["0"])
        issue, wait = (events[event_id] for event_id in iterations["0"])
        assert issue.kind == "q1_issue" and wait.kind == "wait"
        edge = events[loop.meta["loop_q1_issue_event_id"]]
        assert edge.meta["register_reads"]["R1"]["value"] == count
        assert wait.meta["rt_packet_id"] == issue.meta["rt_packet_id"]
        assert list(loop.meta["rt_packet_affine"]) == [wait.meta["rt_packet_id"]]
        for index in range(1, count):
            preview_issue, preview_wait = (events[event_id] for event_id in iterations[str(index)])
            assert preview_issue.meta["preview_source_event_id"] == issue.id
            assert preview_wait.meta["preview_source_event_id"] == wait.id
            assert preview_wait.t0 == Concrete(start + 4 * index)


def test_revisited_affine_loop_uses_its_current_input_register_and_packets():
    source = ("move 2,R0\nmove 4,R2\nbody: add R0,1,R1\ninner: wait R2\nadd R2,4,R2\nnop\nloop R1,@inner\n"
              "sub R0,1,R0\nnop\njnz @body\nstop")
    state = _run(source)
    concrete = _run(source, execution_mode="concrete")
    assert state.q1_time_ns == concrete.q1_time_ns
    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(60)
    assert state.registers == concrete.registers == {"R0": Concrete(0), "R1": Concrete(0), "R2": Concrete(24)}
    first, second = _events(state, "loop_block")
    assert [(loop.t0, loop.meta["count"], loop.meta["total_duration"]["value"], loop.meta["period_affine"])
            for loop in (first, second)] == [
        (Concrete(0), 3, 24, {"intercept": 4, "slope": 4}),
        (Concrete(24), 2, 36, {"intercept": 16, "slope": 4}),
    ]


def test_revisited_compact_loop_without_its_header_stops_at_the_partial_visit():
    state = _run("move 2,R0\nmove 2,R1\ninner: wait 4\ntail: wait 4\nloop R1,@inner\n"
                 "move 2,R1\nsub R0,1,R0\nnop\njnz @tail\nstop")
    loop, = _events(state, "loop_block")
    assert loop.t0 == Concrete(0) and loop.meta["total_duration"]["value"] == 16
    marker, = _events(state, "unknown_region")
    assert (marker.source.line, marker.t0, marker.meta["reason"]) == (5, Concrete(20), "analytical_revisited_compact_loop")
    assert not _events(state, "stop")


@pytest.mark.parametrize(("setup", "body", "operation", "child_counts"), [
    ("move 2,R2", "wait 4", "wait", (2, 2)),
    ("add R0,1,R2", "wait 4", "wait", (3, 2)),
    ("move 2,R2", "play 0,1,4", "play", (2, 2)),
    ("move 2,R2", "set_freq 100\nupd_param 4", "upd_param", (2, 2)),
])
def test_revisited_nested_compact_loops_compose_the_current_child(setup, body, operation, child_counts):
    source = (f"move 2,R0\nnop\nouter: move 2,R1\nmiddle: {setup}\ninner: {body}\n"
              "loop R2,@inner\nloop R1,@middle\nsub R0,1,R0\nnop\njnz @outer\nstop")
    parent_line = 7 + body.count("\n")
    keys = {f"seq0:loop:back_edge.q1asm:4:{parent_line}": 2, f"seq0:loop:back_edge.q1asm:5:{parent_line - 1}": 3}
    state = _run(source, loop_preview_counts=keys)
    concrete = _run(source, execution_mode="concrete")
    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(8 * sum(child_counts))
    assert state.q1_time_ns == concrete.q1_time_ns
    assert state.registers == concrete.registers == {f"R{i}": Concrete(0) for i in range(3)}
    assert _events(state, "stop")[0].t0 == state.rt_time_ns
    assert not _events(state, "unknown_region")

    loops = _events(state, "loop_block")
    assert len(loops) == 4
    by_id = {event.id: event for event in state.events}
    visited_ids = set()
    for child, parent, child_count, start in zip(loops[::2], loops[1::2], child_counts, (0, 8 * child_counts[0])):
        period = 4 * child_count
        assert (child.source.line, parent.source.line) == (5, 4)
        assert child.t0 == parent.t0 == Concrete(start)
        assert parent.t1 == Concrete(start + period)
        assert child.meta["count"] == child_count and parent.meta["count"] == 2
        assert parent.meta["child_loop_event_ids"] == [child.id]
        assert parent.meta["total_duration"]["value"] == 2 * period
        assert parent.meta["period"]["value"] == period
        assert parent.meta["period_invariant"]
        child_iters = child.meta["preview_iteration_event_ids"]
        parent_iters = parent.meta["preview_iteration_event_ids"]
        assert list(child_iters) == [str(index) for index in range(child_count)]
        assert all(set(ids) <= set(parent_iters["0"]) for ids in child_iters.values())
        assert visited_ids.isdisjoint(parent_iters["0"] + parent_iters["1"])
        visited_ids.update(parent_iters["0"] + parent_iters["1"])
        for index, ids in child_iters.items():
            packet = next(by_id[key] for key in ids if by_id[key].kind == operation)
            clone = next(by_id[key] for key in parent_iters["1"] if by_id[key].meta["preview_source_event_id"] == packet.id)
            assert clone.source == packet.source
            assert packet.t0 == Concrete(start + 4 * int(index))
            assert clone.t0 == Concrete(start + period + 4 * int(index))
            if operation == "upd_param":
                setter = by_id[clone.meta["applied_setter_event_ids"]["frequency"]]
                assert setter.id in parent_iters["1"] and setter.meta["value"] == 100


def test_nested_revisit_without_its_child_header_retains_the_observed_partial_boundary():
    state = _run("move 2,R0\nnop\nouter: move 2,R1\nmiddle: move 2,R2\ninner: wait 4\ntail: wait 4\n"
                 "loop R2,@inner\nloop R1,@middle\nmove 2,R1\nmove 2,R2\nsub R0,1,R0\nnop\njnz @tail\nstop")
    child, parent = _events(state, "loop_block")
    assert parent.meta["child_loop_event_ids"] == [child.id]
    assert parent.meta["total_duration"]["value"] == 32
    marker, = _events(state, "unknown_region")
    assert (marker.source.line, marker.t0, marker.meta["reason"]) == (7, Concrete(36), "analytical_revisited_compact_loop")
    assert not _events(state, "stop")


@pytest.mark.parametrize(("setup", "counts", "rt_end", "q1_end"), [
    ("move R1,R2", (3, 2, 1), 24, 180),
    ("add R1,1,R2\nnop", (4, 3, 2), 36, 300),
])
def test_affine_child_counts_preserve_elapsed_rt_q1_and_register_availability(setup, counts, rt_end, q1_end):
    source = f"move 3,R1\nnop\nmiddle: {setup}\ninner: wait 4\nloop R2,@inner\nloop R1,@middle\nstop"
    parent_line = 6 + setup.count("\n")
    preview = {f"seq0:loop:back_edge.q1asm:3:{parent_line}": 3,
               f"seq0:loop:back_edge.q1asm:{parent_line - 2}:{parent_line - 1}": 4}
    state = _run(source, loop_preview_counts=preview)
    concrete = _run(source, execution_mode="concrete")
    child, parent = _events(state, "loop_block")
    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(rt_end)
    assert state.q1_time_ns == concrete.q1_time_ns == Concrete(q1_end + 4)
    assert state.registers == concrete.registers == {"R1": Concrete(0), "R2": Concrete(0)}
    stop, = _events(state, "q1_issue", "stop")
    assert (stop.t0, stop.t1) == (Concrete(q1_end), Concrete(q1_end + 4))
    assert {write.register for write in state.register_history
            if write.time == stop.t0 and write.value == Concrete(0) and write.meta.get("loop_complete")} == {"R1", "R2"}
    assert child.meta["count"] == counts[0]
    assert child.meta["total_duration"]["value"] == 4 * counts[0]
    assert child.meta["shown_iterations"] == list(range(counts[0]))
    assert parent.meta["child_loop_event_ids"] == [child.id]
    assert parent.meta["child_count_affine"] == {child.id: {"intercept": counts[0], "slope": -1}}
    period = parent.meta["period_affine"]
    assert [period["intercept"] + period["slope"] * index for index in range(3)] == [4 * count for count in counts]
    assert parent.meta["total_duration"]["value"] == rt_end
    assert parent.meta["period_invariant"] is False
    assert parent.meta["shown_iterations"] == [0]
    assert list(parent.meta["preview_iteration_event_ids"]) == ["0"]
    assert not _events(state, "unknown_region")


@pytest.mark.parametrize(("setup", "wait", "boundary", "reason"), [
    ("move R8,R2", "wait 4", 4, "analytical_nested_loop_count_unknown"),
    ("move 2,R2", "wait R8", 0, "analytical_nested_loop_work_unknown"),
    ("sub R1,1,R2\nnop", "wait 4", 8, "analytical_nested_loop_counter_varies"),
    ("add R1,4294967294,R2\nnop", "wait 4", 4, "analytical_nested_loop_counter_varies"),
])
def test_unproved_nested_count_or_period_stops_at_observed_rt_prefix(setup, wait, boundary, reason):
    state = _run(f"move 3,R1\nnop\nmiddle: {setup}\ninner: {wait}\nloop R2,@inner\nloop R1,@middle\nstop")
    child, = _events(state, "loop_block")
    marker = _events(state, "unknown_region")[-1]
    assert marker.t0 == Concrete(boundary) and marker.meta["reason"] == reason
    if reason in {"analytical_nested_loop_count_unknown", "analytical_nested_loop_work_unknown"}:
        assert marker.meta["q1_issue_event_id"] == child.meta["loop_q1_issue_event_id"]
    assert not _events(state, "stop")


def test_nested_count_from_an_unproved_mutating_register_keeps_the_first_child():
    source = ("move 3,R1\nmove 1,R3\nnop\nmiddle: add R3,1,R3\nnop\nmove R3,R2\n"
              "inner: wait 4\nloop R2,@inner\nloop R1,@middle\nstop")
    state = _run(source)
    assert _run(source, execution_mode="concrete").rt_time_ns == Concrete(36)
    child, = _events(state, "loop_block")
    assert child.meta["count"] == 2
    marker, = _events(state, "unknown_region")
    assert marker.t0 == Concrete(8) and marker.meta["reason"] == "analytical_nested_loop_counter_varies"
    assert not _events(state, "stop")


@pytest.mark.parametrize(("initial", "setup", "stop_q1"), [
    (0, "sub R1,4,R2\nnop\nsub R4,R2,R2\nnop", 320),
    (4, "sub R4,R1,R2\nnop", 272),
])
def test_affine_child_count_preserves_words_across_a_fixed_modular_quotient(initial, setup, stop_q1):
    source = (f"move 3,R1\nmove 4,R3\nmove {initial},R4\nnop\nmiddle: {setup}\ninner: wait R3\n"
              "loop R2,@inner\nadd R3,4,R3\nnop\nloop R1,@middle\nstop")
    state = _run(source)
    concrete = _run(source, execution_mode="concrete")
    child, parent = _events(state, "loop_block")
    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(56)
    assert state.q1_time_ns == concrete.q1_time_ns == Concrete(stop_q1 + 4)
    assert _events(state, "q1_issue", "stop")[0].t0 == Concrete(stop_q1)
    assert state.registers == concrete.registers
    assert [state.registers[key] for key in ("R1", "R2", "R3")] == [Concrete(0), Concrete(0), Concrete(16)]
    assert child.meta["count"] == 1 and parent.meta["child_loop_event_ids"] == [child.id]
    assert parent.meta["child_count_affine"] == {child.id: {"intercept": 1, "slope": 1}}
    assert parent.meta["iteration_durations"] == [4, 16, 36]
    assert parent.meta["total_duration"]["value"] == 56 and "period_affine" not in parent.meta
    if initial == 0:
        observed = [write.value for write in state.register_history if write.register == "R2" and write.source.line == 5]
        words = [write.value for write in concrete.register_history if write.register == "R2" and write.source.line == 5]
        assert observed == [Concrete(0xFFFFFFFF)]
        assert words == [Concrete(0xFFFFFFFF), Concrete(0xFFFFFFFE), Concrete(0xFFFFFFFD)]
        subtract = next(event for event in _events(state, "q1_issue", "sub") if event.source.line == 7)
        assert subtract.meta["register_reads"]["R2"]["value"] == 0xFFFFFFFF


@pytest.mark.parametrize(("setup", "prefix", "counts", "concrete_status"), [
    ("sub R1,4,R2\nnop\nadd R2,3,R2\nnop", 8, (2, 1, 0), "step_limit"),
    ("sub R1,2,R2\nnop\nadd R2,2,R2\nnop", 12, (3, 2, 1), "completed"),
])
def test_modular_child_count_stops_for_zero_or_a_changing_quotient(setup, prefix, counts, concrete_status):
    source = (f"move 3,R1\nmove 4,R3\nmove 0,R4\nnop\nmiddle: {setup}\ninner: wait R3\n"
              "loop R2,@inner\nadd R3,4,R3\nnop\nloop R1,@middle\nstop")
    state = _run(source)
    child, = _events(state, "loop_block")
    boundary, = _events(state, "unknown_region")
    assert child.meta["count"] == counts[0] and boundary.t0 == Concrete(prefix)
    assert boundary.meta["reason"] == "analytical_nested_loop_counter_varies"
    assert not _events(state, "stop")
    concrete = _run(source, execution_mode="concrete", max_steps=100)
    assert concrete.metadata["execution"]["status"] == concrete_status
    assert [write.value for write in concrete.register_history if write.register == "R2" and write.source.line == 7] == [
        Concrete(count) for count in counts
    ]


def test_affine_child_summary_ids_are_local_to_the_comparison_path():
    state = _run("cmp R0,1\njz @nested\nwait 4\nstop\nnested: move 3,R1\nnop\n"
                 "middle: move R1,R2\ninner: wait 4\nloop R2,@inner\nloop R1,@middle\nstop",
                 branch_policy="explore_both_with_depth_limit")
    child, parent = _events(state, "loop_block")
    assert child.meta["branch_comparison_path"] == parent.meta["branch_comparison_path"] == "taken"
    assert parent.meta["child_loop_event_ids"] == [child.id]
    assert parent.meta["child_count_affine"] == {child.id: {"intercept": 3, "slope": -1}}
    issues = {event.id: event for event in _events(state, "q1_issue", "loop")}
    assert issues[child.meta["loop_q1_issue_event_id"]].source.line == 9
    assert issues[parent.meta["loop_q1_issue_event_id"]].source.line == 10


@pytest.mark.parametrize(("initial", "step", "periods", "final", "stop_q1"), [
    (4, "add R3,4,R3", (8, 16, 24), 16, 232),
    (12, "sub R3,4,R3", (24, 16, 8), 0, 232),
    (8, "nop", (16, 16, 16), 8, 208),
])
def test_invariant_child_count_composes_affine_dwell_and_register_ready_edge(initial, step, periods, final, stop_q1):
    source = (f"move 3,R1\nmove {initial},R3\nnop\nmiddle: move 2,R2\ninner: wait R3\nloop R2,@inner\n"
              f"{step}\nnop\nloop R1,@middle\nstop")
    previews = {"seq0:loop:back_edge.q1asm:4:9": 3, "seq0:loop:back_edge.q1asm:5:6": 2}
    state = _run(source, loop_preview_counts=previews)
    concrete = _run(source, execution_mode="concrete")
    child, parent = _events(state, "loop_block")
    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(sum(periods))
    assert state.q1_time_ns == concrete.q1_time_ns == Concrete(stop_q1 + 4)
    assert state.registers == concrete.registers == {"R1": Concrete(0), "R2": Concrete(0), "R3": Concrete(final)}
    assert parent.meta["child_loop_event_ids"] == [child.id]
    assert parent.meta["period"]["value"] == periods[0] and parent.meta["total_duration"]["value"] == sum(periods)
    assert "rt_packet_affine" not in parent.meta
    assert child.meta["count"] == 2 and child.meta["period"]["value"] == initial
    by_id = {event.id: event for event in state.events}
    first_wait, next_wait = [next(by_id[event_id] for event_id in child.meta["preview_iteration_event_ids"][index]
                                  if by_id[event_id].kind == "wait") for index in ("0", "1")]
    assert next_wait.meta["preview_source_event_id"] == first_wait.id
    assert next_wait.t0 == Concrete(initial)
    assert next_wait.id in parent.meta["first_iteration_event_ids"]
    if periods[0] == periods[-1]:
        assert parent.meta["shown_iterations"] == [0, 1, 2] and parent.meta["period_invariant"]
    else:
        assert parent.meta["period_affine"] == {"intercept": periods[0], "slope": periods[1] - periods[0]}
        assert parent.meta["shown_iterations"] == [0] and not parent.meta["period_invariant"]
        final_write = next(write for write in reversed(state.register_history) if write.register == "R3")
        assert final_write.value == Concrete(final) and final_write.time == Concrete(stop_q1)


def test_affine_nested_dwell_counts_the_dequeue_spacing_of_a_constant_zero_wait():
    source = ("move 3,R1\nmove 4,R3\nnop\nmiddle: move 2,R2\ninner: wait R3\nwait 0\nloop R2,@inner\n"
              "add R3,4,R3\nnop\nloop R1,@middle\nstop")
    state = _run(source)
    concrete = _run(source, execution_mode="concrete")
    child, parent = _events(state, "loop_block")
    assert child.meta["total_duration"]["value"] == 16
    assert parent.meta["period_affine"] == {"intercept": 16, "slope": 8}
    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(72)
    assert state.q1_time_ns == concrete.q1_time_ns


def test_unproved_nested_dwell_range_keeps_rt_symbolic():
    state = _run("move 3,R1\nmove 12,R3\nnop\nmiddle: move 2,R2\ninner: wait R3\nloop R2,@inner\n"
                 "sub R3,5,R3\nnop\nloop R1,@middle\nstop")
    child, parent = _events(state, "loop_block")
    assert child.meta["total_duration"]["value"] == 24
    assert "total_duration" not in parent.meta and "period_affine" not in parent.meta
    assert isinstance(state.rt_time_ns, Unknown) and isinstance(state.registers["R3"], Unknown)


@pytest.mark.parametrize(("initial", "step", "setup", "periods", "final", "zero_starts"), [
    (0, "add R3,4,R3", "move 2,R2", (8, 8, 16), 12, (0, 4)),
    (8, "sub R3,4,R3", "move 2,R2", (16, 8, 8), 0xFFFFFFFC, (24, 28)),
    (0, "add R3,4,R3", "move R1,R2", (12, 8, 8), 12, (0, 4, 8)),
    (8, "sub R3,4,R3", "move R1,R2", (24, 8, 4), 0xFFFFFFFC, (32,)),
    (0, "add R3,5,R3", "move R1,R2", (12, 10, 10), 15, (0, 4, 8)),
])
def test_nested_zero_dwell_endpoint_spacing_uses_the_actual_child_count(initial, step, setup, periods, final, zero_starts):
    source = (f"move 3,R1\nmove {initial},R3\nnop\nmiddle: {setup}\ninner: wait R3\nloop R2,@inner\n"
              f"{step}\nnop\nloop R1,@middle\nstop")
    previews = {"seq0:loop:back_edge.q1asm:4:9": 3, "seq0:loop:back_edge.q1asm:5:6": 3}
    state = _run(source, loop_preview_counts=previews)
    concrete = _run(source, execution_mode="concrete")
    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(sum(periods))
    assert state.q1_time_ns == concrete.q1_time_ns == Concrete(236)
    assert _events(state, "q1_issue", "stop")[0].t0 == Concrete(232)
    assert state.registers == concrete.registers == {"R1": Concrete(0), "R2": Concrete(0), "R3": Concrete(final)}
    assert [event.t0.value for event in _events(concrete, "wait") if event.duration == Concrete(0)] == list(zero_starts)
    child, parent = _events(state, "loop_block")
    assert parent.meta["child_loop_event_ids"] == [child.id]
    assert parent.meta["iteration_durations"] == list(periods)
    assert parent.meta["total_duration"]["value"] == sum(periods)
    assert "period_affine" not in parent.meta and "rt_packet_affine" not in parent.meta
    assert not parent.meta["period_invariant"] and parent.meta["shown_iterations"] == [0]
    waits = _events(state, "wait")
    packet, = state.rt_packets
    assert packet.duration == Concrete(initial)
    assert all(event.duration == Concrete(initial) and event.t1 == Concrete(event.t0.value + initial) for event in waits)
    assert waits[0].meta["rt_packet_id"] == packet.id
    assert waits[1].meta["preview_source_event_id"] == waits[0].id
    if initial == 0:
        assert [event.t0.value for event in waits] == list(zero_starts)
        assert packet.rt_t0 == packet.rt_t1 == Concrete(0)


@pytest.mark.parametrize("setup", ["move 2,R2", "move R1,R2"])
def test_nested_zero_dwell_does_not_skip_an_invalid_nearest_positive_wait(setup):
    source = (f"move 3,R1\nmove 0,R3\nnop\nmiddle: {setup}\ninner: wait R3\nloop R2,@inner\n"
              "add R3,2,R3\nnop\nloop R1,@middle\nstop")
    state = _run(source)
    concrete = _run(source, execution_mode="concrete")
    assert concrete.metadata["execution"]["status"] == "error"
    assert not _events(concrete, "stop")
    child, *parents = _events(state, "loop_block")
    assert child.meta["total_duration"]["value"] == concrete.rt_time_ns.value
    if setup == "move 2,R2":
        parent, = parents
        assert isinstance(state.rt_time_ns, Unknown)
        assert "total_duration" not in parent.meta
    else:
        assert not parents and not _events(state, "stop")
        boundary, = _events(state, "unknown_region")
        assert boundary.t0 == concrete.rt_time_ns


def test_terminal_dwell_wrap_with_a_later_body_read_remains_unproved():
    state = _run("move 3,R1\nmove 8,R3\nnop\nmiddle: move 2,R2\ninner: wait R3\nloop R2,@inner\n"
                 "sub R3,4,R3\nnop\nmove R3,R5\nnop\nloop R1,@middle\nstop")
    child, parent = _events(state, "loop_block")
    assert child.meta["total_duration"]["value"] == 16
    assert isinstance(state.rt_time_ns, Unknown) and "total_duration" not in parent.meta


@pytest.mark.parametrize(("prelude", "count_setup", "extra_wait", "step", "periods", "stop_q1", "final_dwell"), [
    ("move 4,R3\n", "move R1,R2", "", "add R3,4,R3", (12, 16, 12), 232, 16),
    ("move 4,R3\n", "move R1,R2", "\nwait 0", "add R3,4,R3", (24, 24, 16), 256, 16),
    ("move 12,R3\nmove 4,R4\n", "sub R4,R1,R2\nnop", "", "sub R3,4,R3", (12, 16, 12), 272, 0),
])
def test_joint_child_count_and_dwell_has_exact_nonlinear_rt_sum(prelude, count_setup, extra_wait, step, periods, stop_q1, final_dwell):
    source = (f"move 3,R1\n{prelude}nop\nmiddle: {count_setup}\ninner: wait R3{extra_wait}\nloop R2,@inner\n"
              f"{step}\nnop\nloop R1,@middle\nstop")
    parent_start = source.splitlines().index(f"middle: {count_setup.splitlines()[0]}") + 1
    parent_end = source.splitlines().index("loop R1,@middle") + 1
    state = _run(source, loop_preview_counts={f"seq0:loop:back_edge.q1asm:{parent_start}:{parent_end}": 3})
    concrete = _run(source, execution_mode="concrete")
    child, parent = _events(state, "loop_block")
    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(sum(periods))
    assert state.q1_time_ns == concrete.q1_time_ns == Concrete(stop_q1 + 4)
    assert state.registers == concrete.registers
    assert state.registers["R1"] == state.registers["R2"] == Concrete(0)
    assert state.registers["R3"] == Concrete(final_dwell)
    final_write = next(write for write in reversed(state.register_history) if write.register == "R3")
    assert (final_write.value, final_write.time) == (Concrete(final_dwell), Concrete(stop_q1))
    assert parent.meta["child_loop_event_ids"] == [child.id]
    assert parent.meta["period"]["value"] == periods[0]
    assert parent.meta["iteration_durations"] == list(periods)
    assert parent.meta["total_duration"]["value"] == sum(periods)
    assert not parent.meta["period_invariant"]
    assert "period_affine" not in parent.meta and "rt_packet_affine" not in parent.meta
    assert parent.meta["shown_iterations"] == [0]
    assert set(child.meta["first_iteration_event_ids"]) <= set(parent.meta["first_iteration_event_ids"])


def test_joint_child_count_and_dwell_uses_a_bounded_period_sample_for_large_counts():
    count = 10_000
    source = (f"move {count},R1\nmove 4,R3\nnop\nmiddle: move R1,R2\ninner: wait R3\nloop R2,@inner\n"
              "add R3,4,R3\nnop\nloop R1,@middle\nstop")
    state = _run(source)
    _, parent = _events(state, "loop_block")
    total = 4 * count * (count + 1) * (count + 2) // 6
    assert state.rt_time_ns == Concrete(total) and parent.meta["total_duration"]["value"] == total
    assert parent.meta["iteration_durations"] == [4 * count, 8 * (count - 1), 12 * (count - 2)]
    assert len(_events(state, "wait")) == 1


def test_unproved_joint_child_dwell_keeps_the_observed_prefix():
    source = ("move 3,R1\nmove 12,R3\nnop\nmiddle: move R1,R2\ninner: wait R3\nloop R2,@inner\n"
              "sub R3,5,R3\nnop\nloop R1,@middle\nstop")
    state = _run(source)
    child, = _events(state, "loop_block")
    assert child.meta["total_duration"]["value"] == 36
    boundary, = _events(state, "unknown_region")
    assert boundary.t0 == Concrete(36) and boundary.meta["q1_proof_cutoff"] is True
    assert not _events(state, "stop")


@pytest.mark.parametrize(("setup", "setter", "field", "value", "first_count"), [
    ("move R1,R2", "set_freq 4000000", "frequency", 4000000, 3),
    ("move R1,R2", "set_mrk 7", "marker", 7, 3),
    ("move 2,R2", "set_freq 4000000", "frequency", 4000000, 2),
])
def test_affine_child_count_keeps_invariant_latched_setter_and_commit(setup, setter, field, value, first_count):
    source = (f"move 3,R1\nnop\nmiddle: {setup}\ninner: {setter}\nupd_param 4\n"
              "loop R2,@inner\nloop R1,@middle\nstop")
    previews = {"seq0:loop:back_edge.q1asm:3:7": 3, "seq0:loop:back_edge.q1asm:4:6": 3}
    state = _run(source, loop_preview_counts=previews)
    concrete = _run(source, execution_mode="concrete")
    child, parent = _events(state, "loop_block")
    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(24)
    assert state.q1_time_ns == concrete.q1_time_ns == Concrete(208)
    assert state.registers == concrete.registers == {"R1": Concrete(0), "R2": Concrete(0)}
    assert getattr(state.latched_state, field) == getattr(concrete.latched_state, field) == Concrete(value)
    assert field not in state.latched_state.pending_event_ids
    assert parent.meta["child_loop_event_ids"] == [child.id]
    assert parent.meta["total_duration"]["value"] == 24
    assert child.meta["count"] == first_count
    assert len(_events(concrete, "upd_param")) == 6
    by_id = {event.id: event for event in state.events}
    for index, member_ids in child.meta["preview_iteration_event_ids"].items():
        commit = next(by_id[event_id] for event_id in member_ids if by_id[event_id].kind == "upd_param")
        setter_id = commit.meta["applied_setter_event_ids"][field]
        assert setter_id in member_ids and commit.meta["applied_state"][field] == value
        assert by_id[setter_id].meta["value"] == value
        assert commit.t0 == Concrete(4 * int(index))
    if first_count == 3:
        assert parent.meta["period_affine"] == {"intercept": 12, "slope": -4}
        assert parent.meta["shown_iterations"] == [0]
    else:
        assert parent.meta["period_invariant"] and parent.meta["shown_iterations"] == [0, 1, 2]


@pytest.mark.parametrize(("prelude", "setter", "field", "values"), [
    ("", "set_awg_gain 16384,-16384", "awg_gain", (16384, -16384)),
    ("", "set_awg_offs 1024,-1024", "awg_offset", (1024, -1024)),
    ("move 16384,R3\nmove -16384,R4\n", "set_awg_gain R3,R4", "awg_gain", (16384, -16384)),
    ("move 1024,R3\nmove -1024,R4\n", "set_awg_offs R3,R4", "awg_offset", (1024, -1024)),
])
def test_affine_child_count_preserves_the_signed_awg_pair_and_commit_links(prelude, setter, field, values):
    source = (f"move 3,R1\n{prelude}nop\nmiddle: move R1,R2\ninner: {setter}\nupd_param 4\n"
              "loop R2,@inner\nloop R1,@middle\nstop")
    state = _run(source)
    concrete = _run(source, execution_mode="concrete")
    child, parent = _events(state, "loop_block")
    assert state.rt_time_ns == concrete.rt_time_ns == Concrete(24)
    stop, = _events(state, "q1_issue", "stop")
    assert stop.t0 == _events(concrete, "q1_issue", "stop")[0].t0 == Concrete(204 + 4 * prelude.count("\n"))
    assert state.registers == concrete.registers
    assert state.registers["R1"] == state.registers["R2"] == Concrete(0)
    assert getattr(state.latched_state, field) == getattr(concrete.latched_state, field) == tuple(map(Concrete, values))
    assert field not in state.latched_state.pending_event_ids
    assert parent.meta["child_loop_event_ids"] == [child.id]
    if prelude:
        assert state.registers["R4"] == Concrete(values[1] & 0xFFFFFFFF)
    pending, = _events(state, "latched_state_pending")
    commit, = _events(state, "upd_param")
    packet, = state.rt_packets
    assert pending.meta["value"] == commit.meta["applied_state"][field] == list(values)
    assert commit.meta["applied_setter_event_ids"][field] == pending.id
    assert commit.source == packet.source and commit.source.line == pending.source.line + 1
    assert commit.meta["rt_packet_id"] == packet.id
    assert _events(state, "q1_issue", "upd_param")[0].id == commit.meta["q1_issue_event_id"]


def test_unsupported_awg_gain_cannot_prove_a_nested_loop_total():
    source = ("wait 8\nmove 3,R1\nmove 32768,R5\nmove -1,R6\nnop\nmiddle: move R1,R2\n"
              "inner: set_awg_gain R5,R6\nupd_param 4\nloop R2,@inner\nloop R1,@middle\nstop")
    state = _run(source)
    wait, = _events(state, "wait")
    boundary, = _events(state, "unknown_region")
    setter, = _events(state, "q1_issue", "set_awg_gain")

    assert (wait.t0, wait.t1) == (Concrete(0), Concrete(8))
    assert boundary.t0 == state.rt_time_ns == wait.t1
    assert boundary.source == setter.source and boundary.meta["q1_issue_event_id"] == setter.id
    assert not _events(state, "loop_block")
    assert not _events(state, "upd_param") and not _events(state, "stop")


@pytest.mark.parametrize(("setter", "commit"), [
    ("set_awg_gain R1,R4", "upd_param 4"),
    ("set_awg_offs R3,R8", "upd_param 4"),
    ("set_awg_gain 16384,-16384", "wait 4"),
])
def test_unproved_awg_path_or_missing_commit_keeps_the_first_child(setter, commit):
    state = _run(f"move 3,R1\nmove 16384,R3\nmove -16384,R4\nnop\nmiddle: move R1,R2\ninner: {setter}\n"
                 f"{commit}\nloop R2,@inner\nloop R1,@middle\nstop")
    child, = _events(state, "loop_block")
    assert child.meta["count"] == 3
    boundary, = _events(state, "unknown_region")
    assert boundary.t0 == Concrete(12)
    assert boundary.meta["reason"] == "analytical_nested_loop_counter_varies"
    assert not _events(state, "stop")


@pytest.mark.parametrize(("prelude", "setter", "after_child"), [
    ("", "set_freq R1", ""),
    ("", "reset_ph", ""),
    ("reset_ph\n", "set_freq 4000000", ""),
    ("", "set_freq 4000000", "reset_ph\n"),
])
def test_changing_or_one_shot_effects_do_not_prove_variable_child_replay(prelude, setter, after_child):
    state = _run(f"move 3,R1\nnop\n{prelude}middle: move R1,R2\ninner: {setter}\nupd_param 4\n"
                 f"loop R2,@inner\n{after_child}loop R1,@middle\nstop")
    child, = _events(state, "loop_block")
    boundary, = _events(state, "unknown_region")
    assert child.meta["count"] == 3 and boundary.t0 == Concrete(12)
    assert boundary.meta["reason"] == "analytical_nested_loop_counter_varies"
    assert boundary.meta["q1_proof_cutoff"] is True
    assert not _events(state, "stop")


@pytest.mark.parametrize(("setup", "target", "source_pc", "compiled_pc", "count"), [
    ("move 3,R0\nnop\n", "2", 2, 2, 3),
    ("move 3,R0\nmove 3,R1\nnop\n", "R1", 3, 3, 3),
    ("move 3,R0\nnop\njge R0,1,4\n", "4", 3, 4, 3),
    ("move 1,R0\nnop\n", "2", 2, 2, 1),
])
def test_numeric_compact_loop_matches_label_and_concrete(setup, target, source_pc, compiled_pc, count):
    source = f"{setup}wait 100\nloop R0,{target}\nstop"
    state = _run(source)
    labeled = _run(f"{setup}body: wait 100\nloop R0,@body\nstop")
    concrete = _run(source, execution_mode="concrete")
    loop, = _events(state, "loop_block")
    label_loop, = _events(labeled, "loop_block")
    assert loop.meta["count"] == label_loop.meta["count"] == count
    assert loop.meta["loop_preview_key"] == label_loop.meta["loop_preview_key"]
    assert loop.meta["target_pc"] == loop.meta["start_pc"] == source_pc
    assert loop.meta["compiled_target_pc"] == compiled_pc
    assert state.rt_time_ns == labeled.rt_time_ns == concrete.rt_time_ns == Concrete(100 * count)
    assert state.q1_time_ns == labeled.q1_time_ns == concrete.q1_time_ns
    assert state.registers["R0"] == labeled.registers["R0"] == concrete.registers["R0"] == Concrete(0)
    assert _events(state, "stop")[0].t0 == state.rt_time_ns
    wait, = _events(state, "wait")
    assert wait.id in loop.meta["first_iteration_event_ids"]
    assert wait.source.line == source_pc + 1


@pytest.mark.parametrize("source", [
    "move 3,R0\nmove 3,R1\nnop\nwait 100\nmove 6,R1\nloop R0,R1\nstop",
    "move 3,R0\nmove 4,R1\nnop\nsub R1,1,R1\nwait 100\nloop R0,R1\nstop",
    "move 3,R0\nnop\nwait 100\nloop R0,R0\nstop",
])
def test_numeric_loop_keeps_unreadable_or_changing_register_targets_incomplete(source):
    state = _run(source)
    assert state.rt_time_ns == Concrete(100)
    assert _events(state, "unknown_region")
    assert not _events(state, "loop_block")
    assert not _events(state, "stop")


@pytest.mark.parametrize(("source", "target_pc"), [
    ("body: wait 100\njmp @body", 0),
    ("wait 100\njmp 0", 0),
    ("move 1,R27\nwait 100\njmp R27", 1),
])
def test_stable_numeric_jump_matches_labeled_forever_loop(source, target_pc):
    state = _run(source)
    loop, = _events(state, "loop_block")
    wait, = _events(state, "wait")
    assert loop.meta["count"] == "forever"
    assert loop.meta["start_pc"] == target_pc
    assert loop.meta["period"]["value"] == 100
    assert loop.meta["period_invariant"] is True
    assert wait.id in loop.meta["first_iteration_event_ids"]
    assert not _events(state, "unknown_region")
    assert not _events(state, "stop")


@pytest.mark.parametrize(("source", "reason"), [
    ("move 4,R27\nnop\nwait 100\nsub R27,2,R27\nnop\njmp R27", "unsupported_backward_numeric_branch"),
    ("move 1,R27\nwait 100\nmove 0,R27\njmp R27", "unsupported_backward_numeric_branch"),
    ("wait 100\njmp 99", "unknown_numeric_branch_target"),
    ("move 1,R1\nnop\nloop R1,4\nwait 100\njmp 3", "compiled_interior_branch_target"),
    ("wait R1\njmp 0", "unsupported_backward_numeric_branch"),
    ("move 100,R0\nnop\nadd R0,4,R0\nnop\nwait R0\njmp 2", "unsupported_backward_numeric_branch"),
])
def test_numeric_jump_keeps_unknown_tail_when_target_or_repeat_period_cannot_be_proven(source, reason):
    state = _run(source)
    marker = _events(state, "unknown_region")[-1]
    assert marker.meta["reason"] == reason
    assert not _events(state, "loop_block")
    assert not _events(state, "stop")
