import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.underflow import analyze_underflow
from q1timeline.analysis.values import Concrete, Unknown
from q1timeline.q1asm.parser import parse_q1asm


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


def test_queue_analysis_stops_at_conditional_rt_boundary() -> None:
    state = analyze("wait 100\nset_cond 1,1,0,20\nplay 0,1,40\nset_cond 0,0,0,4\nwait 8\nstop")

    assert analyze_underflow(state).status == "analysis_incomplete"
    assert not any(item.category in {"definite_underflow", "possible_underflow"} for item in state.diagnostics)
    assert any(event.kind == "analysis_incomplete"
               and event.meta.get("reason") == "conditional_execution_unknown" for event in state.events)
    assert {event.source.line for event in state.events if event.kind == "slack"} == {1}


def test_conditional_boundary_does_not_hide_a_proven_earlier_queue_failure() -> None:
    state = analyze("wait_sync 4\n" + "nop\nnop\nwait 4\n" * 100
                    + "set_cond 1,1,0,20\nplay 0,1,40\nstop")

    assert analyze_underflow(state).status == "definite_underflow"
    warning, = [event for event in state.events if event.kind == "underflow_warning"]
    assert warning.source.line < 302


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
def test_compact_loop_invalidates_a_changing_conditional_enable(initial: int, change: str) -> None:
    source = (f"move {initial},R0\nmove 1,R2\nmove 0,R3\nmove 2,R1\n"
              f"body: set_cond R0,R2,R3,4\n{change}\nnop\nloop R1,@body\nupd_param 4\nstop")
    state = analyze(source)

    assert isinstance(state.conditional_enable, Unknown)
    assert isinstance(state.rt_time_ns, Unknown)
    assert not any(event.kind == "upd_param" for event in state.events)
    unknown, = [event for event in state.events if event.kind == "unknown_region"]
    assert unknown.meta["op"] == "upd_param"


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
