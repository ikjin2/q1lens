import pytest

from q1timeline.analysis.api import analyze_text
from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.values import Concrete
from q1timeline.playback import PlaybackSession
from q1timeline.q1asm import compiled_addresses, core, core_rt
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


def test_analysis_dequeues_resolved_packets_and_preserves_unknown_trigger(monkeypatch):
    calls = []
    execute = core_rt.execute_packet

    def observe(op, operands, selected, latches, devices, **kwargs):
        calls.append((op, selected.skipped, selected.dwell_ns))
        return execute(op, operands, selected, latches, devices, **kwargs)

    monkeypatch.setattr(core_rt, "execute_packet", observe)
    source = ("set_mrk 5\nreset_ph\nset_cond 1,0,0,0\nupd_param 32\nset_cond 0,0,0,4\nupd_param 8\n"
              "set_cond 1,1,0,4\nupd_param 4\nstop")
    state = interpret_program(parse_q1asm(source), sequencer_id="s")
    assert calls == [("upd_param", True, 0), ("upd_param", False, 8)]
    skipped, update, unknown = state.rt_packets
    assert skipped.meta["captured_state"] == {"marker": 5, "phase_reset": True}
    event = next(e for e in state.events if e.kind == "upd_param")
    assert event.meta["applied_state"] == {"marker": 5}
    assert (update.rt_t0, unknown.rt_t0, unknown.duration) == (Concrete(4), Concrete(12), Concrete(4))
    assert unknown.meta["conditional_execution_unknown"]


def test_play_executes_packed_device_aliases_and_keeps_one_shots_until_dequeue():
    source = ("set_mrk 5\nset_time_ref\nreset_netzero\nset_cond 1,0,0,4\nfb_cmd 0x10,123,8\n"
              "set_cond 0,0,0,4\nwait 8\nset_time_ref\nfb_cmd 0x11,217,12\nfb_cmd 0x100,9,8\nstop")
    session = PlaybackSession({"s": ("s.q1asm", source)})
    assert session.run(budget_ms=1000)["status"] == "completed"
    state = session.runners[0].state
    concrete = interpret_program(parse_q1asm(source), sequencer_id="s", execution_mode="concrete")
    assert [(p.rt_t0, p.duration, p.meta.get("device_operands"), p.meta.get("applied_state")) for p in state.rt_packets] == [
        (p.rt_t0, p.duration, p.meta.get("device_operands"), p.meta.get("applied_state")) for p in concrete.rt_packets]
    skip, _, update, send = state.rt_packets
    assert skip.meta["captured_state"] == {"marker": 5, "netzero_reset": True, "time_ref": True}
    assert update.meta["applied_state"] == {"marker": 5, "time_ref": True}
    assert dict(state.playback_devices.thresholds) == {1: 217}
    assert state.playback_devices.time_ref_ns == 12
    assert send.meta["normalized_op"] == "fb_com_data" and send.meta["device_operands"] == [0, 9, 8]


@pytest.mark.parametrize("command,status", [("fb_cmd 3,1,4", "blocked"), ("fb_cmd 0x42,0x80,4", "error")])
def test_play_validates_raw_linq_only_on_the_executed_arm(command, status):
    session = PlaybackSession({"s": ("s.q1asm", f"set_cond 1,0,0,8\n{command}\nset_cond 0,0,0,4\n{command}\nstop")})
    assert session.run(budget_ms=1000)["status"] == status
    state = session.runners[0].state
    skip, = state.rt_packets
    assert skip.meta["conditional_skipped"]
    assert state.metadata["execution"]["source"]["line"] == 4


@pytest.mark.parametrize(("source", "expected"), [
    ("wait 100\n" * 32 + "stop", 132),
    ("wait 100\n" * 33 + "stop", 140),
    ("wait 100\n" * 34 + "stop", 240),
    ("wait_sync 4\n" + "wait 100\n" * 34 + "stop", 444),
    ("wait_sync 4\nwait_sync 12\n" + "wait 100\n" * 34 + "stop", 656),
    ("wait 100\n" * 34 + "nop\n" * 60 + "stop", 384),
    ("move 50,R0\nnop\nbody: wait 200\nnop\nwait 28\nloop R0,@body\nstop", 7792),
    ("wait 1000\nmove 33,R0\nmove 20,R1\nnop\nbody: wait R1\nadd R1,8,R1\nnop\nwait 64\nloop R0,@body\nstop", 3556),
    ("wait 2000\nmove 100,R0\nmove 408,R1\nnop\n" + "wait 92\n" * 36
     + "body: wait R1\nsub R1,4,R1\nnop\nloop R0,@body\nstop", 23984),
    ("move 36,R1\nnop\nbody: wait_sync 4\nwait 88\nloop R1,@body\nstop", 5880),
    ("wait_sync 4\nmove 8,R0\nnop\nouter: move 3,R1\nnop\ninner: wait_sync 24\nwait 144\nloop R1,@inner\n"
     "wait 100\nloop R0,@outer\nstop", 4448),
    ("wait 2000\nmove 3,R0\nnop\nouter: move 3,R1\nnop\ninner: wait 120\nwait 24\nloop R1,@inner\n"
     "wait 100\nloop R0,@outer\nstop", 460),
    ("wait 1600\nmove 12,R0\nnop\nbody: cmp R0,8\njb @short\nwait 200\nshort: wait 92\nloop R0,@body\nstop", 732),
])
def test_queue_elapsed_through_stop_matches_q1simulator_1_4_1(source, expected):
    # Observed Q1Core.clock.core_time minus its -40 ns start, ISA 2, ordinary loop labels.
    program = parse_q1asm(source)
    simulation, = simulate_programs({"s": program})
    play = PlaybackSession({"s": ("program.q1asm", source)})
    assert play.run(budget_ms=5000)["status"] == "completed"
    states = [simulation, play.runners[0].state,
              *(interpret_program(program, sequencer_id="s", execution_mode=mode) for mode in ("concrete", "analysis"))]
    assert all(state.q1_time_ns == Concrete(expected) for state in states)
    assert simulation.metadata["execution"]["q1_elapsed_ns"] == expected
    stop = next(event for event in simulation.events if event.kind == "q1_issue" and event.meta["op"] == "stop")
    assert simulation.metadata["execution"]["scheduled_q1_end_ns"] == stop.t1.value


@pytest.mark.parametrize("mode", ["simulation", "playback"])
def test_schedulers_use_core_effects_and_compiled_cursor_through_a_macro_interior(monkeypatch, mode):
    source = "move 2,R0\nnop\nloop R0,5\nloop R1,@done\nstop -9\ndone: set_mrk 7\nupd_param 8\nstop R0"
    issues, pcs = [], []
    original_issue, original_next = core.issue, compiled_addresses.next_compiled_pc

    def record_issue(instruction, operands, flags):
        issues.append(instruction.op)
        return original_issue(instruction, operands, flags)

    def record_next(pc, instruction, **kwargs):
        address = original_next(pc, instruction, **kwargs)
        pcs.append((pc, address))
        return address

    monkeypatch.setattr(core, "issue", record_issue)
    monkeypatch.setattr(compiled_addresses, "next_compiled_pc", record_next)
    if mode == "simulation":
        ir = analyze_text(source, sequencer_id="s", execution_mode="simulation")
    else:
        session = PlaybackSession({"s": ("program.q1asm", source)})
        assert session.run(max_steps=16, budget_ms=5000)["status"] == "completed"
        ir = session.snapshot()

    recorded = [e["meta"] for e in ir["events"] if e["kind"] == "q1_issue"]
    assert issues == [e["op"] for e in recorded] == ["move", "nop", "loop", "jnz", "set_mrk", "upd_param", "stop"]
    assert pcs == [(0, 1), (1, 2), (2, 5), (5, 7), (7, 8), (8, 9), (9, 10)]
    assert [e["compiled_pc"] for e in recorded] == [pc for pc, _ in pcs]
    assert ir["execution"]["s"]["stop_code"] == 1
    assert next(e for e in ir["events"] if e["kind"] == "upd_param")["meta"]["applied_state"]["marker"] == 7


def test_symbolic_playback_branch_and_replay_use_selected_compiled_successor(monkeypatch):
    source = "jlt R0,1,@done\nstop 3\ndone: stop 9"
    decisions = []
    original = compiled_addresses.next_compiled_pc

    def record(pc, item, **kwargs):
        next_pc = original(pc, item, **kwargs)
        if item.op == "jlt":
            decisions.append((kwargs["taken"], next_pc))
        return next_pc

    monkeypatch.setattr(compiled_addresses, "next_compiled_pc", record)
    session = PlaybackSession({"s": ("choice.q1asm", source)})
    assert session.run()["status"] == "completed"
    assert session.snapshot()["execution"]["s"]["stop_code"] == 3
    branch, = session.branches
    assert branch["calculated_taken"] is None
    session.choose(branch["branch_id"], "taken")
    assert session.run()["status"] == "completed"
    assert session.snapshot()["execution"]["s"]["stop_code"] == 9
    assert decisions == [(False, 2), (True, 3)]


@pytest.mark.parametrize("target,taken_ns,continue_ns", [("$ADDRESS", 28, 16), ("$DEST", 32, 20)])
def test_chosen_branch_timing_uses_encoded_target_after_def_binding(target, taken_ns, continue_ns):
    source = (".DEF ADDRESS @end\n.DEF DEST R4\nmove @end,R4\nmove 0,R0\nnop\n"
              f"jlt R0,1,{target}\nstop 2\nend: stop 3")
    program = parse_q1asm(source)
    session = PlaybackSession({"s": ("chosen.q1asm", source)})
    session.run()

    def branch_cost(state):
        branch, = [event for event in state.events if event.kind == "q1_issue" and event.label == "jlt"]
        return branch.duration.value

    natural, = simulate_programs({"s": program})
    assert branch_cost(session.runners[0].state) == branch_cost(natural) == taken_ns
    session.choose(session.branches[0]["branch_id"], "fallthrough")
    session.run()
    forced, = simulate_programs({"s": program}, branch_choices={("s", 3, 1): False})
    assert branch_cost(session.runners[0].state) == branch_cost(forced) == continue_ns


def test_reference_sync_cost_survives_a_conditionally_skipped_physical_barrier():
    source = "set_cond 1,0,0,4\nwait_sync 12\nset_cond 0,0,0,4\n" + "wait 100\n" * 34 + "stop"
    program = parse_q1asm(source)
    concrete = interpret_program(program, sequencer_id="s", execution_mode="concrete")
    simulation, = simulate_programs({"s": program})
    play = PlaybackSession({"s": ("sync.q1asm", source)})
    assert play.run(budget_ms=5000)["status"] == "completed"
    for state in (concrete, simulation, play.runners[0].state):
        sync = state.rt_packets[0]
        assert sync.meta["conditional_skipped"] and sync.duration == Concrete(4)
        assert state.q1_time_ns == Concrete(452)


def test_shared_launch_then_asymmetric_sync_waits_match_q1simulator_elapsed_times():
    # Q1Simulator 1.4.1, QCM with both sequencers' sync_en set, in either launch order.
    programs = {name: parse_q1asm(f"wait_sync 4\nwait {prior}\nwait_sync 4\n" + "wait 100\n" * 35 + "stop")
                for name, prior in (("a", 100), ("b", 300))}
    first, second = simulate_programs(programs)
    assert first.q1_time_ns == second.q1_time_ns == Concrete(1048)
    assert first.rt_packets[2].duration == Concrete(204)
    assert second.rt_packets[2].duration == Concrete(4)


def test_zero_duration_sync_keeps_its_q1lens_dequeue_slot_without_reference_overhead():
    program = parse_q1asm("wait_sync 0\n" + "wait 100\n" * 34 + "stop")
    simulation, = simulate_programs({"s": program})
    analytical = interpret_program(program, sequencer_id="s")
    concrete = interpret_program(program, sequencer_id="s", execution_mode="concrete")
    assert analytical.q1_time_ns == concrete.q1_time_ns == simulation.q1_time_ns == Concrete(244)
