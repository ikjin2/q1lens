import pytest

from q1timeline.analysis.interpreter import interpret_program, prepare_def_program
from q1timeline.analysis.underflow import analyze_underflow
from q1timeline.analysis.values import Concrete
from q1timeline.playback import PlaybackSession
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


def run(source, mode, *, prepared=False):
    if mode == "playback":
        playback = PlaybackSession({"seq": ("loop.q1asm", source)})
        playback.run(max_steps=30, budget_ms=1000)
        return playback.runners[0].state
    program = parse_q1asm(source, file="loop.q1asm")
    binding = prepare_def_program(program, params={}, strict_q1asm=False, varying=set()) if prepared else None
    if mode == "simulation":
        return simulate_programs({"seq": program}, prepared_programs={"seq": binding} if prepared else None)[0]
    return interpret_program(program, sequencer_id="seq", execution_mode=mode, prepared=binding)


@pytest.mark.parametrize("mode,prepared", [("analysis", False), ("concrete", False), ("simulation", False),
                                           ("playback", False), ("concrete", True), ("simulation", True)])
@pytest.mark.parametrize("defs,operands", [("", "R0,R0"), (".DEF COUNTER R0\n.DEF TARGET R0\n", "$COUNTER,$TARGET")])
@pytest.mark.parametrize("count", [1, 5])
def test_loop_with_counter_as_target_reports_internal_read_and_stops(mode, prepared, defs, operands, count):
    source = f"{defs}move {count},R0\nnop\nloop {operands}\nwait 20\nwait 4\nstop\n"
    state = run(source, mode, prepared=prepared)

    hazard, = [item for item in state.diagnostics if item.category == "register_not_ready"]
    assert hazard.source.file == "loop.q1asm" and hazard.source.line == len(defs.splitlines()) + 3
    assert hazard.source.raw == f"loop {operands}"
    assert hazard.details["register"] == "R0" and hazard.details["argument_index"] == 1
    assert hazard.details["writer"]["line"] == hazard.source.line
    assert state.registers == {"R0": Concrete(count)}
    assert not state.rt_packets
    assert not any(event.kind in {"wait", "stop"} for event in state.events)
    if mode == "analysis":
        assert analyze_underflow(state).status == "analysis_incomplete"
    else:
        assert state.metadata["execution"]["status"] == "error"
        assert state.metadata["execution"]["pc"] == 2


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation", "playback"])
def test_explicit_loop_lowering_requires_a_gap_before_reading_the_decremented_target(mode):
    source = "move 6,R0\nnop\nsub R0,1,R0\n{gap}jnz R0\nwait 20\nwait 4\nstop\n"
    stale = run(source.format(gap=""), mode)
    ready = run(source.format(gap="nop\n"), mode)

    hazard, = [item for item in stale.diagnostics if item.category == "register_not_ready"]
    assert hazard.source.raw == "jnz R0" and hazard.details["writer"]["line"] == 3
    if mode != "analysis":
        assert stale.metadata["execution"]["status"] == "error"
        assert not stale.rt_packets
    assert not ready.diagnostics
    assert ready.registers == {"R0": Concrete(5)}
    assert [event.duration.value for event in ready.events if event.kind == "wait"] == [20, 4]


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation", "playback"])
def test_loop_with_a_separate_target_register_keeps_its_macro_timing(mode):
    state = run("move 2,R0\nmove 6,R1\nnop\nloop R0,R1\nwait 20\nwait 4\nstop\n", mode)

    issue, = [event for event in state.events if event.kind == "q1_issue" and event.source.line == 4]
    assert issue.meta["op"] == "loop" and issue.duration == Concrete(24)
    assert state.registers == {"R0": Concrete(1), "R1": Concrete(6)}
    assert [event.duration.value for event in state.events if event.kind == "wait"] == [4]
    assert not state.diagnostics
