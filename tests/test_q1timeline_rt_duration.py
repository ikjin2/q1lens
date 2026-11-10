from __future__ import annotations

import pytest

from q1timeline.analysis.interpreter import diagnose_program_source, interpret_program, prepare_def_program
from q1timeline.analysis.values import Concrete, Unknown
from q1timeline.playback import PlaybackSession
from q1timeline.q1asm.instruction_table import get_instruction_spec
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


def run(source, mode, *, params=None):
    if mode == "playback":
        session = PlaybackSession({"seq": ("duration.q1asm", source)}, params=params)
        session.run(max_steps=100, budget_ms=1000)
        return session.runners[0].state
    program = parse_q1asm(source, file="duration.q1asm")
    if mode in {"simulation", "prepared_simulation"}:
        prepared = None
        if mode == "prepared_simulation":
            prepared = {"seq": prepare_def_program(program, params=params or {}, strict_q1asm=False, varying=set())}
        state, = simulate_programs({"seq": program}, params=params, prepared_programs=prepared)
        return state
    return interpret_program(program, sequencer_id="seq", execution_mode=mode, params=params)


@pytest.mark.parametrize("mode", ["analysis", "concrete", "playback"])
@pytest.mark.parametrize("prefix,params,line,operand,value", [
    ("wait 1", None, 1, "1", 1),
    ("set_freq 10\nupd_param 3", None, 2, "3", 3),
    ("move 2,R0\nnop\nwait R0", None, 3, "R0", 2),
    ("wait {SHORT}", {"SHORT": 2}, 1, "{SHORT}", 2),
    ("set_cond 0,0,0,2", None, 1, "2", 2),
    ("set_cond 1,0,0,1", None, 1, "1", 1),
    ("wait_trigger 1,2", None, 1, "2", 2),
])
def test_known_short_rt_dwell_is_not_an_exact_execution(mode, prefix, params, line, operand, value):
    state = run(f"{prefix}\nwait 8\nstop", mode, params=params)
    diagnostic, = [item for item in state.diagnostics if item.details.get("reason") == "invalid_rt_duration"]
    assert diagnostic.source.line == line and diagnostic.details["operand"] == operand
    assert diagnostic.details["value"] == value
    assert diagnostic.severity == "error" and f"{value} ns" in diagnostic.message
    if mode == "analysis":
        region, = [event for event in state.events if event.meta.get("reason") == "invalid_rt_duration"]
        assert region.kind == "unknown_region" and region.source == diagnostic.source and region.t1 is None
        followup, = [event for event in state.events if event.kind == "wait"]
        assert followup.source.line == line + 1 and isinstance(followup.t0, Unknown)
    else:
        execution = state.metadata["execution"]
        assert execution["status"] == "error" and execution["source"]["line"] == line
        assert not state.rt_packets


@pytest.mark.parametrize("mode", ["analysis", "concrete", "playback", "simulation", "prepared_simulation"])
@pytest.mark.parametrize("duration", [0, 4, 65535])
def test_valid_dwell_boundaries_preserve_packet_width_and_spacing(mode, duration):
    state = run(f"wait {duration}\nwait 8\nstop", mode)
    if mode != "analysis":
        assert state.metadata["execution"]["status"] == "completed"
    first, second = state.rt_packets
    assert first.duration == Concrete(duration)
    assert second.rt_t0.value - first.rt_t0.value == (4 if duration == 0 else duration)


@pytest.mark.parametrize("mode", ["analysis", "concrete", "playback"])
def test_unknown_duration_stays_unresolved(mode):
    state = run("wait R0\nstop", mode)
    assert not [item for item in state.diagnostics if item.details.get("reason") == "invalid_rt_duration"]
    if mode == "analysis":
        assert isinstance(state.rt_packets[0].duration, Unknown)
    else:
        assert state.metadata["execution"]["status"] == "blocked"


@pytest.mark.parametrize("mode", ["simulation", "prepared_simulation"])
@pytest.mark.parametrize("bad,params,value", [
    ("wait 1", None, 1),
    ("set_freq 10\nupd_param 3", None, 3),
    ("move 2,R0\nnop\nwait R0", None, 2),
    ("move 65536,R0\nnop\nwait R0", None, 65536),
    ("wait {SHORT}", {"SHORT": 2}, 2),
    ("set_cond 0,0,0,2", None, 2),
    ("set_cond 1,0,0,1", None, 1),
    ("wait_trigger 1,2", None, 2),
    ("fb_cmd 0x12a,7,3", None, 3),
    ("move 0,R1\nnop\nfb_com_cfg R1,3", None, 3),
])
def test_simulation_rejects_invalid_duration_before_issuing_packet(mode, bad, params, value):
    source = f"wait 4\n{bad}\nwait 8\nstop"
    line = len(bad.splitlines()) + 1
    state = run(source, mode, params=params)
    concrete = run(source, "concrete", params=params)
    playback = run(source, "playback", params=params)
    for result in (state, concrete, playback):
        execution = result.metadata["execution"]
        diagnostic, = [item for item in result.diagnostics if item.details.get("reason") == "invalid_rt_duration"]
        assert execution["status"] == "error" and execution["source"]["line"] == diagnostic.source.line == line
        assert diagnostic.details["value"] == value and execution["reason"] == concrete.metadata["execution"]["reason"]
        assert execution["steps"] == line - 1
    assert state.q1_time_ns == concrete.q1_time_ns == playback.q1_time_ns
    assert state.executed_pcs == set(range(line - 1))
    assert all(event.source.line < line for event in state.events)
    packet, = state.rt_packets
    assert packet.source.line == 1 and packet.duration == Concrete(4)
    assert {event.meta["rt_packet_id"] for event in state.events
            if event.kind == "q1_issue" and "rt_packet_id" in event.meta} == {packet.id}


def test_simulation_keeps_observed_rt_prefix_and_plan_before_bad_q1_issue():
    state, = simulate_programs({"seq": parse_q1asm("wait 4\nwait 1\nstop")}, simulation={"startup_delay_ns": 4})
    packet, = state.rt_packets
    assert (packet.rt_t0, packet.rt_t1) == (Concrete(4), Concrete(4))
    assert packet.meta["planned_rt_end_ns"] == 8 and packet.meta["observation_cutoff_ns"] == 4
    assert state.q1_time_ns == state.rt_time_ns == Concrete(4)
    assert state.metadata["execution"]["steps"] == 1
    assert state.metadata["execution"]["status"] == "error"


@pytest.mark.parametrize("source", ["wait 1", "wait 2", "upd_param 3", "set_cond 1,0,0,2"])
def test_short_duration_remains_encodable_in_source_validation(source):
    program = parse_q1asm(f"{source}\nstop")
    instruction = program.instructions[0]
    assert not get_instruction_spec(instruction.op).validate(instruction)
    assert not [item for item in diagnose_program_source(program, params={}, strict_q1asm=True) if item.severity == "error"]


def test_affine_loop_cannot_prove_a_total_that_contains_a_hidden_short_dwell():
    source = "move 3,R0\nmove 5,R1\nnop\nbody: wait R1\nsub R1,1,R1\nnop\nloop R0,@body\nwait 8\nstop"
    state = run(source, "analysis")
    assert isinstance(state.rt_time_ns, Unknown)
    concrete = run(source, "concrete")
    diagnostic, = [item for item in concrete.diagnostics if item.details.get("reason") == "invalid_rt_duration"]
    assert diagnostic.source.line == 4 and diagnostic.details["value"] == 3
