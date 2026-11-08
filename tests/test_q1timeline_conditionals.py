import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.queue_timing import replay_queues
from q1timeline.analysis.underflow import analyze_underflow
from q1timeline.analysis.values import Concrete, Unknown
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


def analyze(source: str, *, mode: str = "analysis"):
    return interpret_program(parse_q1asm(source, file="conditional.q1asm"),
                             sequencer_id="seq0", execution_mode=mode)


@pytest.mark.parametrize("mode", ["analysis", "concrete"])
@pytest.mark.parametrize("configuration", [
    "set_cond 0,0,7,4",
    "move 0,R0\nmove 1,R1\nmove 7,R2\nnop\nset_cond R0,R1,R2,4",
])
def test_known_disabled_condition_preserves_exact_execution(mode: str, configuration: str) -> None:
    state = analyze(f"{configuration}\nwait 8\nstop", mode=mode)

    assert state.rt_time_ns == Concrete(8)
    assert state.conditional_enable == Concrete(0)
    assert not state.conditional_time_unknown
    assert not state.diagnostics
    assert state.stopped
    if mode == "concrete":
        assert state.metadata["execution"]["status"] == "completed"
    condition_events = [event for event in state.events if event.source.raw.startswith("set_cond")]
    assert len(condition_events) == 2
    assert all(event.meta["conditional_enabled"] is False for event in condition_events)
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


@pytest.mark.parametrize("enabled", [0, 1])
@pytest.mark.parametrize("mode", ["analysis", "concrete"])
def test_negative_else_duration_is_invalid(enabled: int, mode: str) -> None:
    state = analyze(f"set_cond {enabled},0,0,-1\nstop", mode=mode)

    assert any(item.category == "invalid_argument_value"
               and item.details.get("operand_role") == "else_duration" for item in state.diagnostics)
    assert not any(event.kind == "latched_state_pending" for event in state.events)
    assert state.conditional_enable == Concrete(0)


@pytest.mark.parametrize("configuration, enabled", [
    ("set_cond 1,1,0,20", True), ("set_cond R0,R1,R2,20", None),
])
def test_active_or_unresolved_condition_does_not_assert_rt_outputs(configuration: str, enabled) -> None:
    state = analyze(f"set_mrk 3\nset_awg_offs 100,0\n{configuration}\nplay 0,1,40\nacquire 0,0,8\nstop")

    conditions = [event for event in state.events if event.meta.get("field") == "set_cond"]
    assert conditions[0].meta["conditional_enabled"] is enabled
    regions = [event for event in state.events if event.kind == "unknown_region"]
    assert [event.meta["op"] for event in regions] == ["play", "acquire"]
    assert regions[0].t0 == Concrete(0)
    assert all(event.t1 is None and isinstance(event.duration, Unknown) for event in regions)
    assert all(event.meta["reason"] == "conditional_execution_unknown" for event in regions)
    assert all(event.meta["output_state_unknown"] and event.meta["conditional_time_unknown"] for event in regions)
    assert not any(event.kind in {"play", "acquire", "marker_state"} for event in state.events)
    assert not any("applied_state" in event.meta for event in state.events)
    assert all(isinstance(packet.duration, Unknown) for packet in state.rt_packets)
    assert all(event.confidence == "unknown" for event in state.events if event.lane.startswith("rt."))
    assert {"marker", "awg_offset"} <= state.latched_state.pending_event_ids.keys()
    assert isinstance(state.rt_time_ns, Unknown)
    assert state.q1_time_ns == Concrete(24)


def test_disabling_condition_keeps_elapsed_time_unknown_and_pending_output_settings() -> None:
    state = analyze("set_mrk 3\nset_cond 1,1,0,20\nplay 0,1,40\nset_cond 0,0,0,4\nwait 8\nupd_param 4\nstop")

    assert state.conditional_enable == Concrete(0)
    assert state.conditional_time_unknown
    assert isinstance(state.rt_time_ns, Unknown)
    wait, = [event for event in state.events if event.kind == "wait"]
    assert wait.duration == Concrete(8)
    assert isinstance(wait.t0, Unknown) and isinstance(wait.t1, Unknown)
    assert wait.confidence == "unknown"
    assert wait.meta["conditional_time_unknown"] and wait.meta["output_state_unknown"] is False
    update, = [event for event in state.events if event.kind == "upd_param"]
    assert update.meta["applied_state"] == {"marker": 3}
    assert update.confidence == "unknown"
    assert all(event.confidence == "unknown" for event in state.events if event.lane.startswith("rt."))
    assert len([item for item in state.diagnostics if item.category == "analysis_incomplete"]) == 1


def test_enable_then_disable_without_rt_work_does_not_make_time_unknown() -> None:
    state = analyze("set_cond 1,1,0,20\nset_cond 0,0,0,4\nwait 8\nstop")

    assert state.rt_time_ns == Concrete(8)
    assert not state.conditional_time_unknown
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


def test_conditional_wait_does_not_assert_output_changes() -> None:
    state = analyze("set_mrk 3\nupd_param 4\nset_cond 1,1,0,20\nwait 100\nstop")
    unknown, = [event for event in state.events if event.kind == "unknown_region"]

    assert unknown.meta["output_state_unknown"] is False
    assert unknown.t0 == Concrete(4)


@pytest.mark.parametrize("operation", ["wait 40", "play 0,1,40", "upd_param 40", "acquire 0,0,40"])
def test_equal_conditional_durations_preserve_time_without_asserting_outputs(operation: str) -> None:
    state = analyze(f"set_mrk 3\nset_cond 1,1,0,40\n{operation}\nset_cond 0,0,0,4\nwait 100\nstop")
    unknown, = [event for event in state.events if event.kind == "unknown_region"]

    assert state.rt_time_ns == Concrete(140)
    assert not state.conditional_time_unknown
    assert (unknown.t0, unknown.t1, unknown.duration) == (Concrete(0), Concrete(40), Concrete(40))
    assert unknown.confidence == "unknown"
    assert unknown.meta["conditional_timing_known"]
    assert not unknown.meta.get("conditional_time_unknown")
    assert unknown.meta["output_state_unknown"] == (not operation.startswith("wait"))
    assert "marker" in state.latched_state.pending_event_ids
    assert not any(event.kind in {"play", "acquire", "upd_param", "marker_state"} for event in state.events)
    assert state.rt_packets[0].confidence == "exact"
    assert state.rt_packets[0].meta["conditional_execution_unknown"]
    assert state.rt_packets[0].meta["output_state_unknown"] == unknown.meta["output_state_unknown"]
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"
    assert not any("Timing effect is unknown" in item.message for item in state.diagnostics)


@pytest.mark.parametrize("operation", ["wait_sync 40", "wait_trigger 1,40", "play_netzero 0,40", "wait 0", "play 0,1,4"])
def test_equal_duration_proof_does_not_ignore_blocking_or_zero_duration_semantics(operation: str) -> None:
    else_duration = 4 if operation == "wait 0" else 40 if operation != "play 0,1,4" else 0
    state = analyze(f"set_cond 1,1,0,{else_duration}\n{operation}\nset_cond 0,0,0,4\nwait 100\nstop")

    assert state.conditional_time_unknown
    assert isinstance(state.rt_time_ns, Unknown)
    assert analyze_underflow(state).status == "analysis_incomplete"


def test_later_equal_conditional_duration_does_not_recover_an_unknown_absolute_time() -> None:
    state = analyze("set_cond 1,1,0,20\nplay 0,1,40\nset_cond 1,1,0,40\nplay 0,1,40\nset_cond 0,0,0,4\nwait 100\nstop")
    regions = [event for event in state.events if event.kind == "unknown_region"]

    assert isinstance(state.rt_time_ns, Unknown)
    assert regions[1].duration == Concrete(40)
    assert isinstance(regions[1].t0, Unknown) and isinstance(regions[1].t1, Unknown)
    assert regions[1].meta["conditional_time_unknown"]
    assert analyze_underflow(state).status == "analysis_incomplete"


@pytest.mark.parametrize("configuration", ["set_cond 1,1,6,40", "set_cond 1,1,7,40",
                                           "move 1,R0\nmove 1,R1\nnop\nset_cond R0,R1,R2,40"])
def test_equal_conditional_duration_does_not_assume_unsupported_operator_semantics(configuration: str) -> None:
    state = analyze(f"{configuration}\nwait 40\nset_cond 0,0,0,4\nwait 100\nstop")

    assert isinstance(state.rt_time_ns, Unknown)
    assert analyze_underflow(state).status == "analysis_incomplete"


@pytest.mark.parametrize("body_wait", ["", "wait 40\n"])
def test_changing_conditional_operator_does_not_extrapolate_supported_first_iteration(body_wait: str) -> None:
    state = analyze("move 2,R0\nmove 0,R2\nmove 1,R3\nnop\n"
                    f"body: set_cond R3,R3,R2,40\n{body_wait}add R2,6,R2\nnop\nloop R0,@body\nwait 40\nstop")

    assert isinstance(state.conditional_operator, Unknown)
    assert isinstance(state.rt_time_ns, Unknown)
    assert analyze_underflow(state).status == "analysis_incomplete"


def test_changing_conditional_operator_keeps_known_disabled_execution_exact() -> None:
    state = analyze("move 2,R0\nmove 0,R1\nmove 0,R2\nnop\n"
                    "body: set_cond R1,R1,R2,40\nwait 40\nadd R2,6,R2\nnop\nloop R0,@body\nwait 40\nstop")

    assert isinstance(state.conditional_operator, Unknown)
    assert state.conditional_enable == Concrete(0)
    assert state.rt_time_ns == Concrete(120)


def test_equal_conditional_duration_allows_later_proven_underflow_detection() -> None:
    suffix = "move 8192,R0\nbody: wait 4\n" + "nop\n" * 64 + "loop R0,@body\nstop"
    state = analyze("set_cond 1,1,0,40\nplay 0,1,40\nset_cond 0,0,0,4\n" + suffix)

    assert analyze_underflow(state).status == "definite_underflow"


@pytest.mark.parametrize("operator, executed", [(0, False), (1, True), (2, True), (3, False), (4, False), (5, True)])
def test_zero_mask_predicate_matches_declared_simulation_without_trigger_inputs(operator: int, executed: bool) -> None:
    source = f"set_cond 1,0,{operator},20\nwait 40\nset_cond 0,0,0,4\nwait 100\nstop"
    state = analyze(source)
    simulated, = simulate_programs({"seq0": parse_q1asm(source)}, simulation={"startup_delay_ns": 200})

    assert state.rt_time_ns == Concrete(140 if executed else 120)
    assert sum(packet.duration.value for packet in state.rt_packets) == sum(packet.duration.value for packet in simulated.rt_packets)
    assert len([event for event in state.events if event.kind == "conditional_skip"]) == (not executed)
    assert not any(event.kind == "unknown_region" for event in state.events)
    assert not state.diagnostics
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


@pytest.mark.parametrize("operation", ["play 0,1,40", "upd_param 40", "acquire 0,0,40", "fb_com_data 5,7,40"])
def test_known_false_conditional_skips_outputs_and_keeps_pending_settings(operation: str) -> None:
    state = analyze(f"set_mrk 3\nset_awg_gain 100,100\nset_cond 1,0,0,20\n{operation}\nset_cond 0,0,0,4\nwait 100\nstop")
    skipped, = [event for event in state.events if event.kind == "conditional_skip"]

    assert state.rt_time_ns == Concrete(120)
    assert skipped.duration == Concrete(20)
    assert skipped.meta["conditional_skipped"]
    assert skipped.meta["output_state_unknown"] is False
    assert not skipped.meta.get("applied_state")
    assert not any(event.kind in {"play", "upd_param", "acquire", "feedback_com", "marker_state"} for event in state.events)
    assert {"marker", "awg_gain"} <= state.latched_state.pending_event_ids.keys()
    issue = next(event for event in state.events if event.id == skipped.meta["q1_issue_event_id"])
    assert issue.meta["conditional_skipped"]
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


def test_known_skip_retains_setters_until_the_first_real_application() -> None:
    state = analyze("set_mrk 3\nset_cond 1,0,0,20\nplay 0,1,40\nset_cond 0,0,0,4\nupd_param 4\nstop")
    update, = [event for event in state.events if event.kind == "upd_param"]
    marker, = [event for event in state.events if event.kind == "marker_state"]

    assert update.t0 == marker.t0 == Concrete(20)
    assert update.meta["applied_state"] == {"marker": 3}
    assert not state.latched_state.pending_event_ids


def test_known_true_conditional_uses_normal_output_and_apply_path() -> None:
    state = analyze("set_mrk 3\nset_cond 1,0,1,20\nplay 0,1,40\nset_cond 0,0,0,4\nwait 100\nstop")
    plays = [event for event in state.events if event.kind == "play"]

    assert state.rt_time_ns == Concrete(140)
    assert len(plays) == 2
    assert all(event.confidence == "exact" and event.meta["applied_state"] == {"marker": 3} for event in plays)
    assert not any(event.kind in {"unknown_region", "conditional_skip"} for event in state.events)
    assert not state.latched_state.pending_event_ids
    assert not state.diagnostics


def test_conditional_mask_is_a_snapshot_including_register_aliases() -> None:
    state = analyze(".DEF ENABLE R0\n.DEF MASK R1\n.DEF OP R2\nmove 1,R0\nmove 0,R1\nmove 1,R2\nnop\n"
                    "set_cond $ENABLE,$MASK,$OP,20\nmove 1,R1\nnop\nplay 0,1,40\nstop")

    assert state.conditional_mask == Concrete(0)
    assert state.rt_time_ns == Concrete(40)
    assert len([event for event in state.events if event.kind == "play"]) == 2


def test_pending_mask_write_does_not_resolve_the_predicate_using_its_future_value() -> None:
    state = analyze("move 1,R0\nmove 1,R1\nmove 1,R2\nnop\nmove 0,R1\nset_cond R0,R1,R2,20\nplay 0,1,40\nstop")

    assert state.conditional_mask == Concrete(1)
    assert isinstance(state.rt_time_ns, Unknown)
    assert any(item.category == "register_not_ready" for item in state.diagnostics)


@pytest.mark.parametrize("configuration", ["set_cond 1,1,0,20", "set_cond 1,0,6,20", "set_cond 1,0,7,20",
    "move 0,R1\nmove 1,R2\nnop\nset_cond R0,R1,R2,20",
    "move 1,R0\nmove 1,R2\nnop\nset_cond R0,R1,R2,20",
    "move 1,R0\nmove 0,R1\nnop\nset_cond R0,R1,R2,20"])
def test_unknown_or_unsupported_conditional_controls_remain_conservative(configuration: str) -> None:
    state = analyze(f"{configuration}\nplay 0,1,40\nstop")

    assert isinstance(state.rt_time_ns, Unknown)
    assert not any(event.kind in {"play", "conditional_skip"} for event in state.events)


@pytest.mark.parametrize("operation, else_duration", [("wait_sync 40", 20), ("wait_trigger 1,40", 20),
                                                     ("play_netzero 0,40", 20)])
@pytest.mark.parametrize("operator", [0, 1])
def test_known_predicate_does_not_expand_sync_trigger_or_unsupported_timing(operation: str, else_duration: int, operator: int) -> None:
    state = analyze(f"set_cond 1,0,{operator},{else_duration}\n{operation}\nstop")

    assert isinstance(state.rt_time_ns, Unknown)
    assert not any(event.kind == "conditional_skip" for event in state.events)


@pytest.mark.parametrize("operator", [0, 1])
@pytest.mark.parametrize("nominal, else_duration", [(0, 20), (40, 0), (0, 0)])
def test_known_predicate_uses_only_the_selected_duration(operator: int, nominal: int, else_duration: int) -> None:
    source = f"set_mrk 3\nset_cond 1,0,{operator},{else_duration}\nplay 0,1,{nominal}\nset_cond 0,0,0,4\nupd_param 4\nstop"
    state = analyze(source)
    actual = nominal if operator else else_duration

    assert state.rt_time_ns == Concrete(actual + 4)
    assert [packet.duration for packet in state.rt_packets] == [Concrete(actual), Concrete(4)]
    assert len([event for event in state.events if event.kind == "conditional_skip"]) == (not operator)
    marker, = [event for event in state.events if event.kind == "marker_state"]
    assert marker.t0 == Concrete(0 if operator else actual)
    issue = next(event for event in state.events if event.kind == "q1_issue" and event.source.raw.startswith("play"))
    assert issue.meta["conditional_timing_known"]
    assert not any(event.kind == "unknown_region" for event in state.events)
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


@pytest.mark.parametrize("operator", [0, 1])
def test_known_predicate_with_zero_else_duration_matches_simulation(operator: int) -> None:
    source = f"set_cond 1,0,{operator},0\nwait 40\nset_cond 0,0,0,4\nwait 4\nstop"
    state = analyze(source)
    simulated, = simulate_programs({"seq0": parse_q1asm(source)}, simulation={"startup_delay_ns": 200})

    assert [packet.duration for packet in state.rt_packets] == [packet.duration for packet in simulated.rt_packets]
    assert not any(item.severity == "error" for item in simulated.diagnostics)


@pytest.mark.parametrize("operator, nominal, else_duration", [(1, 0, 20), (0, 20, 0), (1, 4, 0), (0, 0, 4)])
def test_conditional_selected_zero_duration_suspends_the_queue_guard(operator: int, nominal: int, else_duration: int) -> None:
    source = (f"set_cond 1,0,{operator},{else_duration}\nwait {nominal}\n"
              "set_cond 0,0,0,4\nnop\nnop\nnop\nwait 4\nstop")
    state = analyze(source)
    replay = replay_queues({"seq0": state.rt_packets})["seq0"]

    assert replay.incomplete_reason is None
    assert (replay.failure is None) == ((nominal if operator else else_duration) == 0)


def test_known_false_predicate_does_not_require_the_unexecuted_duration_register() -> None:
    state = analyze("set_mrk 3\nset_cond 1,0,0,20\nwait R7\nset_cond 0,0,0,4\nupd_param 4\nstop")
    skipped, = [event for event in state.events if event.kind == "conditional_skip"]

    assert state.rt_time_ns == Concrete(24)
    assert skipped.duration == Concrete(20)
    assert not any(event.kind in {"play", "unknown_region"} for event in state.events)
    assert not any(item.category == "unknown_duration" for item in state.diagnostics)
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"


def test_known_true_predicate_keeps_an_unknown_selected_duration_incomplete() -> None:
    state = analyze("set_cond 1,0,1,0\nwait R7\nstop")
    issue = next(event for event in state.events if event.kind == "q1_issue" and event.source.raw.startswith("wait"))

    assert isinstance(state.rt_time_ns, Unknown)
    assert not issue.meta["conditional_timing_known"]
    assert not issue.meta.get("conditional_execution_unknown")
    assert any(item.category == "unknown_duration" for item in state.diagnostics)
    assert not any(event.kind in {"play", "conditional_skip"} for event in state.events)
    assert analyze_underflow(state).status == "analysis_incomplete"


@pytest.mark.parametrize("operator", [0, 1])
def test_known_predicate_keeps_an_unresolved_else_duration_only_on_its_selected_path(operator: int) -> None:
    state = analyze(f"set_mrk 3\nset_cond 1,0,{operator},{{ELSE}}\nwait 40\nstop")
    issue = next(event for event in state.events if event.kind == "q1_issue" and event.source.raw.startswith("wait"))

    assert issue.meta["conditional_timing_known"] == bool(operator)
    assert isinstance(state.rt_time_ns, Concrete) == bool(operator)
    assert "marker" in state.latched_state.pending_event_ids
    assert analyze_underflow(state).status == ("not_detected_under_current_assumptions" if operator else "analysis_incomplete")


def test_known_skip_keeps_a_pending_duration_read_hazard_visible() -> None:
    state = analyze("set_cond 1,0,0,20\nmove 40,R7\nwait R7\nstop")

    assert state.rt_time_ns == Concrete(20)
    assert any(item.category == "register_not_ready" for item in state.diagnostics)


@pytest.mark.parametrize("offset, executed", [(1, True), (3, False)])
def test_zero_mask_predicate_uses_a_proven_operator_interval(offset: int, executed: bool) -> None:
    state = analyze(f".DEF OP R2\nmove 1,R0\nmove 0,R1\nand R7,1,R2\nnop\nadd R2,{offset},R2\nnop\n"
                    "set_cond R0,R1,$OP,40\nmove R8,R2\nnop\nplay 0,1,100\nstop")

    assert state.rt_time_ns == Concrete(100 if executed else 40)
    assert state.conditional_operator_bounds == (offset, offset + 1)
    assert len([event for event in state.events if event.kind == "play"]) == (2 if executed else 0)
    assert len([event for event in state.events if event.kind == "conditional_skip"]) == (not executed)
    assert not any(item.category == "analysis_incomplete" for item in state.diagnostics)


def test_proven_boolean_enable_interval_executes_when_disabled_and_enabled_paths_agree() -> None:
    state = analyze("and R7,1,R0\nmove 0,R1\nmove 1,R2\nnop\nset_cond R0,R1,R2,40\nplay 0,1,100\nstop")

    assert state.conditional_enable_bounds == (0, 1)
    assert state.rt_time_ns == Concrete(100)
    assert len([event for event in state.events if event.kind == "play"]) == 2
    assert not state.diagnostics


@pytest.mark.parametrize("configuration", [
    "move 1,R0\nmove 0,R1\nand R7,1,R2\nnop\n",
    "move 1,R0\nmove 0,R1\nand R7,1,R2\nnop\nadd R2,5,R2\nnop\n",
    "and R7,1,R0\nmove 0,R1\nmove 0,R2\nnop\n",
    "move 0,R1\nmove 1,R2\nnop\n",
])
def test_conditional_intervals_with_different_or_unsupported_outcomes_stay_unknown(configuration: str) -> None:
    state = analyze(configuration + "set_cond R0,R1,R2,40\nplay 0,1,100\nstop")

    assert not isinstance(state.rt_time_ns, Concrete)
    assert not any(event.kind in {"play", "conditional_skip"} for event in state.events)
    assert analyze_underflow(state).status == "analysis_incomplete"


def test_pending_operator_bounds_do_not_resolve_a_predicate_from_the_future_write() -> None:
    state = analyze("move 1,R0\nmove 0,R1\nand R7,1,R2\nnop\nadd R2,1,R2\n"
                    "set_cond R0,R1,R2,40\nplay 0,1,100\nstop")

    assert state.conditional_operator_bounds == (0, 1)
    assert not isinstance(state.rt_time_ns, Concrete)
    assert any(item.category == "register_not_ready" for item in state.diagnostics)


def test_captured_conditional_bounds_survive_source_writes_inside_a_later_loop() -> None:
    state = analyze("move 2,R3\nmove 1,R0\nmove 0,R1\nand R7,1,R2\nnop\nadd R2,1,R2\nnop\n"
                    "set_cond R0,R1,R2,40\nbody: play 0,1,100\nadd R2,6,R2\nnop\nloop R3,@body\nplay 0,1,100\nstop")

    assert state.conditional_operator_bounds == (1, 2)
    assert state.rt_time_ns == Concrete(300)


@pytest.mark.parametrize("change, field", [("add R2,6,R2", "conditional_operator_bounds"),
                                          ("sub R0,1,R0", "conditional_enable_bounds")])
def test_hidden_loop_control_writes_invalidate_captured_conditional_bounds(change: str, field: str) -> None:
    state = analyze("move 2,R3\nmove 1,R0\nmove 0,R1\nand R7,1,R2\nnop\nadd R2,1,R2\nnop\n"
                    f"body: set_cond R0,R1,R2,40\nplay 0,1,100\n{change}\nnop\nloop R3,@body\nplay 0,1,100\nstop")

    assert getattr(state, field) is None
    assert not isinstance(state.rt_time_ns, Concrete)
    assert state.conditional_time_unknown


@pytest.mark.parametrize("operator_work, executed", [
    ("or R7,1,R2\nnop\nand R2,5,R2", True),
    ("and R7,4,R2", False),
])
def test_sparse_operator_facts_filter_out_infeasible_intermediate_operators(operator_work: str, executed: bool) -> None:
    state = analyze(f"move 1,R0\nmove 0,R1\n{operator_work}\nnop\nset_cond R0,R1,R2,40\n"
                    "move R8,R2\nnop\nplay 0,1,100\nstop")

    assert state.rt_time_ns == Concrete(100 if executed else 40)
    assert state.conditional_operator_known_bits is not None
    assert len([event for event in state.events if event.kind == "play"]) == (2 if executed else 0)
    assert not any(item.category == "analysis_incomplete" for item in state.diagnostics)


@pytest.mark.parametrize("operator_work", ["or R7,1,R2\nnop\nand R2,7,R2", "and R7,6,R2", "or R7,1,R2"])
def test_sparse_control_facts_do_not_ignore_possible_unsupported_or_different_predicates(operator_work: str) -> None:
    state = analyze(f"move 1,R0\nmove 0,R1\n{operator_work}\nnop\nset_cond R0,R1,R2,40\nplay 0,1,100\nstop")

    assert not isinstance(state.rt_time_ns, Concrete)
    assert not any(event.kind in {"play", "conditional_skip"} for event in state.events)


def test_sparse_operator_capture_does_not_read_a_future_bit_fact_write() -> None:
    state = analyze("move 1,R0\nmove 0,R1\nor R7,1,R2\nnop\nand R2,5,R2\n"
                    "set_cond R0,R1,R2,40\nplay 0,1,100\nstop")

    assert not isinstance(state.rt_time_ns, Concrete)
    assert any(item.category == "register_not_ready" for item in state.diagnostics)


def test_hidden_loop_operator_change_clears_captured_sparse_bit_facts() -> None:
    state = analyze("move 2,R3\nmove 1,R0\nmove 0,R1\nor R7,1,R2\nnop\nand R2,5,R2\nnop\n"
                    "body: set_cond R0,R1,R2,40\nplay 0,1,100\nadd R2,6,R2\nnop\nloop R3,@body\nplay 0,1,100\nstop")

    assert state.conditional_operator_known_bits is None
    assert state.conditional_operator_bounds is None
    assert not isinstance(state.rt_time_ns, Concrete)


def test_sparse_operator_configuration_before_a_loop_retains_its_capture() -> None:
    state = analyze("move 2,R3\nmove 1,R0\nmove 0,R1\nor R7,1,R2\nnop\nand R2,5,R2\nnop\n"
                    "set_cond R0,R1,R2,40\nbody: play 0,1,100\nadd R2,6,R2\nnop\nloop R3,@body\nplay 0,1,100\nstop")

    assert state.conditional_operator_known_bits == (1, 0xFFFFFFFA)
    assert state.rt_time_ns == Concrete(300)


def test_unknown_predicate_with_proven_supported_operator_range_keeps_equal_elapsed_time() -> None:
    state = analyze("move 1,R0\nmove 1,R1\nand R7,1,R2\nnop\nset_cond R0,R1,R2,40\nplay 0,1,40\nstop")
    region, = [event for event in state.events if event.kind == "unknown_region"]

    assert state.rt_time_ns == Concrete(40)
    assert region.meta["conditional_timing_known"]
    assert region.meta["output_state_unknown"]
    assert not any(event.kind == "play" for event in state.events)


@pytest.mark.parametrize("change, operator", [("add R1,1,R1", 1), ("sub R2,1,R2", 1), ("sub R3,1,R3", 0)])
@pytest.mark.parametrize("body_rt", ["", "play 0,1,40\n"])
def test_hidden_loop_predicate_changes_do_not_reuse_the_first_known_outcome(change: str, operator: int, body_rt: str) -> None:
    state = analyze(f"move 2,R0\nmove 0,R1\nmove {operator},R2\nmove 1,R3\n"
                    f"body: set_cond R3,R1,R2,20\n{body_rt}{change}\nnop\nloop R0,@body\nplay 0,1,40\nstop")

    assert isinstance(state.rt_time_ns, Unknown)
    assert state.conditional_time_unknown
    assert any(event.kind == "unknown_region" for event in state.events)


def test_known_skip_does_not_recover_unknown_absolute_time_from_an_earlier_conditional_interval() -> None:
    state = analyze("set_cond 1,1,0,20\nplay 0,1,40\nset_cond 1,0,0,20\nplay 0,1,40\nset_cond 0,0,0,4\nwait 100\nstop")
    skipped, = [event for event in state.events if event.kind == "conditional_skip"]

    assert isinstance(state.rt_time_ns, Unknown)
    assert isinstance(skipped.t0, Unknown) and isinstance(skipped.t1, Unknown)
    assert skipped.duration == Concrete(20)
    assert analyze_underflow(state).status == "analysis_incomplete"


@pytest.mark.parametrize("operator", [0, 1])
def test_known_predicate_does_not_suppress_invalid_instruction_operands(operator: int) -> None:
    state = analyze(f"set_cond 1,0,{operator},20\nplay 0,1,-4\nstop")

    assert any(item.category == "invalid_argument_value" for item in state.diagnostics)
    assert not any(event.kind in {"play", "conditional_skip"} for event in state.events)


def test_queue_analysis_stops_at_conditional_rt_boundary() -> None:
    state = analyze("wait 100\nset_cond 1,1,0,20\nplay 0,1,40\nset_cond 0,0,0,4\nwait 8\nstop")

    assert analyze_underflow(state).status == "analysis_incomplete"
    assert not any(item.category in {"definite_underflow", "possible_underflow"} for item in state.diagnostics)
    assert any(event.kind == "analysis_incomplete"
               and event.meta.get("reason") == "conditional_execution_unknown" for event in state.events)
    assert {event.source.line for event in state.events if event.kind == "slack"} == {1}


def test_conditional_boundary_does_not_hide_a_proven_earlier_queue_failure() -> None:
    prefix = "wait_sync 4\nmove 8192,R0\nbody: wait 4\n" + "nop\n" * 64 + "loop R0,@body\n"
    state = analyze(prefix + "set_cond 1,1,0,20\nplay 0,1,40\nstop")

    assert analyze_underflow(state).status == "definite_underflow"
    warning, = [event for event in state.events if event.kind == "underflow_warning"]
    assert warning.source.line < len(prefix.splitlines()) + 1


def test_enabled_condition_stays_an_explicit_concrete_execution_boundary() -> None:
    state = analyze("set_cond 1,1,0,20\nplay 0,1,40\nstop", mode="concrete")

    assert state.metadata["execution"]["status"] == "blocked"
    assert state.metadata["execution"]["steps"] == 0
    assert state.rt_packets == []


@pytest.mark.parametrize("conditional_prefix", ["", "wait 4\n"])
def test_branch_comparison_forks_preserve_condition_and_elapsed_uncertainty(conditional_prefix: str) -> None:
    source = ("set_cond 1,1,0,20\n" + conditional_prefix
              + "jlt R0,1,@off\nplay 0,1,40\nstop\noff: set_cond 0,0,0,4\nwait 8\nstop")
    state = interpret_program(parse_q1asm(source, file="conditional.q1asm"), sequencer_id="seq0",
                              branch_policy="explore_both_with_depth_limit")

    play, = [event for event in state.events if event.meta.get("op") == "play"
             and event.kind == "unknown_region"]
    assert play.meta["branch_comparison_path"] == "fallthrough"
    assert play.meta["conditional_time_unknown"]
    wait, = [event for event in state.events if event.kind == "wait"]
    assert wait.meta["branch_comparison_path"] == "taken"
    assert isinstance(wait.t0, Unknown if conditional_prefix else Concrete)
    assert wait.meta.get("conditional_time_unknown", False) == bool(conditional_prefix)


def test_compact_loop_previews_preserve_condition_uncertainty_after_disable() -> None:
    source = "move 2,R0\nset_cond 1,1,0,20\nbody: wait 100\nloop R0,@body\nset_cond 0,0,0,4\nwait 8\nstop"
    state = interpret_program(parse_q1asm(source, file="conditional.q1asm"), sequencer_id="seq0",
                              loop_preview_counts={"seq0:loop:conditional.q1asm:3:4": 2})

    regions = [event for event in state.events if event.kind == "unknown_region"]
    assert len(regions) == 2
    assert all(event.meta["conditional_time_unknown"] for event in regions)
    assert all(event.meta["output_state_unknown"] is False for event in regions)
    assert state.conditional_enable == Concrete(0)
    wait, = [event for event in state.events if event.kind == "wait"]
    assert isinstance(wait.t0, Unknown)
    assert wait.meta["conditional_time_unknown"]
    assert all(event.confidence == "unknown" for event in state.events
               if event.kind in {"loop_block", "loop_iteration_preview"})


def test_disabled_concrete_condition_ignores_unknown_mask_and_operator_registers() -> None:
    state = analyze("move 0,R0\nnop\nset_cond R0,R1,R2,4\nwait 8\nstop", mode="concrete")

    assert state.metadata["execution"]["status"] == "completed"
    assert state.rt_time_ns == Concrete(8)
    assert state.conditional_enable == Concrete(0)


@pytest.mark.parametrize("initial, change", [(0, "add R0,1,R0"), (1, "sub R0,1,R0")])
@pytest.mark.parametrize("else_duration", [4, 20])
def test_compact_loop_invalidates_a_changing_conditional_enable(initial: int, change: str, else_duration: int) -> None:
    source = (f"move {initial},R0\nmove 1,R2\nmove 0,R3\nmove 2,R1\n"
              f"body: set_cond R0,R2,R3,{else_duration}\n{change}\nnop\nloop R1,@body\nupd_param 4\nstop")
    state = analyze(source)

    assert isinstance(state.conditional_enable, Unknown)
    if else_duration == 4:
        assert state.rt_time_ns == Concrete(4)
    else:
        assert isinstance(state.rt_time_ns, Unknown)
    assert not any(event.kind == "upd_param" for event in state.events)
    unknown, = [event for event in state.events if event.kind == "unknown_region"]
    assert unknown.meta["op"] == "upd_param"
    assert unknown.meta["output_state_unknown"]


def test_compact_loop_does_not_extrapolate_a_changing_condition_into_rt_timing() -> None:
    source = ("move 0,R0\nmove 1,R2\nmove 0,R3\nmove 2,R1\n"
              "body: set_cond R0,R2,R3,20\nwait 8\nadd R0,1,R0\nnop\n"
              "set_cond 0,0,0,4\nloop R1,@body\nwait 4\nstop")
    state = interpret_program(parse_q1asm(source, file="conditional.q1asm"), sequencer_id="seq0",
                              loop_preview_counts={"seq0:loop:conditional.q1asm:5:10": 2})

    assert state.conditional_enable == Concrete(0)
    assert state.conditional_time_unknown
    assert isinstance(state.rt_time_ns, Unknown)
    loop, = [event for event in state.events if event.kind == "loop_block"]
    assert loop.confidence == "unknown"
    assert isinstance(loop.duration, Unknown)
    assert loop.meta["visible_iteration_count"] == 1
    waits = [event for event in state.events if event.kind == "wait"]
    assert waits[0].t0 == Concrete(0) and waits[0].t1 == Concrete(8)
    assert waits[0].confidence == "exact"
    assert isinstance(waits[-1].t0, Unknown)


@pytest.mark.parametrize("configuration", ["set_cond 0,0,0,4", "set_cond R0,R2,R3,4\nset_cond 0,0,0,4"])
def test_compact_loop_preserves_an_explicit_disable_before_rt_work(configuration: str) -> None:
    source = ("move 0,R0\nmove 1,R2\nmove 0,R3\nmove 2,R1\n"
              f"body: {configuration}\nwait 8\nadd R0,1,R0\nnop\nloop R1,@body\nwait 4\nstop")
    state = analyze(source)

    assert state.conditional_enable == Concrete(0)
    assert not state.conditional_time_unknown
    assert state.rt_time_ns == Concrete(20)
    loop, = [event for event in state.events if event.kind == "loop_block"]
    assert loop.confidence == "exact"
