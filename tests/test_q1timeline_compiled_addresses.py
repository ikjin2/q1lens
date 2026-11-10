from __future__ import annotations

import pytest

from q1timeline.analysis.interpreter import interpret_program, prepare_def_program
from q1timeline.analysis.values import Concrete
from q1timeline.ir.control_flow_graph import control_flow_graph_from_states
from q1timeline.playback import PlaybackSession
from q1timeline.q1asm.compiled_addresses import compile_addresses
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


MODES = ["concrete", "playback", "simulation"]
SPEC_EXAMPLE = """move @done,R0
nop
jmp R0
again: jlt R2,5,@done
loop R3,@again
done: stop 17
"""


def run(source, mode, *, prepared=False, branch_choices=None):
    program = parse_q1asm(source, file="compiled.q1asm")
    bindings = prepare_def_program(program, params={}, strict_q1asm=False, varying=set()) if prepared else None
    if mode == "playback":
        session = PlaybackSession({"seq": ("compiled.q1asm", source)})
        session.run(max_steps=200, budget_ms=1000)
        return session.runners[0].state
    if mode == "simulation":
        states = simulate_programs({"seq": program}, prepared_programs={"seq": bindings} if prepared else None,
                                   branch_choices=branch_choices)
        return states[0]
    return interpret_program(program, sequencer_id="seq", execution_mode=mode, prepared=bindings)


def issues(state):
    return [event for event in state.events if event.kind == "q1_issue"]


def graph_edge(state, op):
    graph = control_flow_graph_from_states([state])["sequencers"][0]
    return next(edge for edge in graph["edges"] if edge["op"] == op)


def test_qblox_1_4_1_assembler_address_golden_vector():
    # q1asm_windows.exe -d, source fixture extended to cover every macro form.
    program = parse_q1asm(SPEC_EXAMPLE.replace("loop R3,@again", "loop R3,@again\njge R2,5,@done"))
    addresses = compile_addresses({item.pc: item for item in program.instructions}, program.labels)
    assert addresses.entries == {0: 0, 1: 1, 2: 2, 3: 3, 4: 5, 5: 7, 6: 9}
    assert addresses.labels == {"again": 3, "done": 9}
    assert [(address, target.source_pc, target.interior.op, target.interior.args[0].raw)
            for address, target in addresses.targets.items() if target.interior is not None] == [
        (4, 3, "jb", "@done"), (6, 4, "jnz", "@again"), (8, 5, "jae", "@done"),
    ]


@pytest.mark.parametrize("mode", MODES)
def test_compiled_example_reaches_terminal_with_address_in_register(mode):
    state = run(SPEC_EXAMPLE, mode)
    assert state.metadata["execution"]["status"] == "completed"
    assert state.metadata["execution"]["address_model"] == "compiled_q1asm"
    assert state.metadata["execution"]["stop_code"] == 17
    assert state.metadata["execution"]["stop_source"]["line"] == 6
    assert state.registers == {"R0": Concrete(7)}
    trace = issues(state)
    assert [event.source.line for event in trace] == [1, 2, 3, 6]
    assert [event.meta["source_pc"] for event in trace] == [0, 1, 2, 5]
    assert [event.meta["compiled_pc"] for event in trace] == [0, 1, 2, 7]
    assert trace[2].meta["target_pc"] == 5
    assert trace[2].meta["compiled_target_pc"] == 7


@pytest.mark.parametrize("mode", ["concrete", "simulation"])
def test_prepared_label_alias_and_interior_use_compiled_addresses(mode):
    source = ".DEF TARGET @end\nmove $TARGET,R0\nnop\ncmp R0,8\njmp 5\njlt R1,7,@end\nmove 9,R2\nend: stop"
    state = run(source, mode, prepared=True)
    assert state.metadata["execution"]["status"] == "completed"
    assert state.registers == {"R0": Concrete(7)}
    assert state.q1_time_ns == run(source, mode).q1_time_ns


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("macro", ["jlt R1,2,@end", "jge R1,1,@end", "loop R1,@end"])
@pytest.mark.parametrize("at_entry", [True, False])
@pytest.mark.parametrize("in_register", [True, False])
def test_numeric_and_register_targets_on_each_side_of_each_macro(mode, macro, at_entry, in_register):
    address = 3 if at_entry else 6
    prefix = f"move 2,R1\nmove {address + 1},R0\nnop\njmp R0" if in_register else f"move 2,R1\nnop\njmp {address}"
    state = run(f"{prefix}\n{macro}\nmove 9,R2\nend: stop", mode)
    assert state.metadata["execution"]["status"] == "completed"
    jump = next(event for event in issues(state) if event.meta["op"] == "jmp")
    assert jump.meta["compiled_target_pc"] == address + int(in_register)
    assert jump.meta["target_pc"] == (3 if at_entry else 5) + int(in_register)
    executed_macro = next((event for event in issues(state) if event.meta["op"] == macro.split()[0]), None)
    assert (executed_macro is not None) is at_entry
    if at_entry and executed_macro.meta["branch_taken"]:
        assert executed_macro.duration == Concrete(24)


@pytest.mark.parametrize("mode", ["analysis", *MODES])
@pytest.mark.parametrize(("macro", "lowered", "compare"), [
    ("jlt R1,7,@end", "jb", 2),
    ("jge R1,7,@end", "jae", 0),
    ("loop R1,@end", "jnz", 0),
])
def test_interior_executes_only_the_lowered_branch(mode, macro, lowered, compare):
    source = f"move 1,R0\nnop\ncmp R0,{compare}\njmp 5\n{macro}\nmove 9,R2\nend: stop"
    state = run(source, mode)
    if mode != "analysis":
        assert state.metadata["execution"]["status"] == "completed"
    assert state.registers == {"R0": Concrete(1)}
    jump, interior, terminal = issues(state)[3:]
    assert jump.meta["target_pc"] == 4
    assert jump.meta["compiled_target_pc"] == 5
    assert jump.meta["target_interior_op"] == lowered
    assert interior.source.line == 5
    assert interior.meta["op"] == lowered
    assert interior.meta["source_pc"] == 4
    assert interior.meta["compiled_pc"] == 5
    assert interior.meta["macro_source_op"] == macro.split()[0]
    assert interior.meta["compiled_target_pc"] == 7
    assert interior.meta["branch_taken"] is True
    assert interior.meta["register_reads"] == {}
    assert interior.duration == Concrete(16)
    assert terminal.source.line == 7


@pytest.mark.parametrize("mode", ["analysis", *MODES])
@pytest.mark.parametrize(("macro", "lowered", "compare"), [
    ("jlt R1,7,@end", "jb", 0),
    ("jge R1,7,@end", "jae", 2),
    ("loop R1,@end", "jnz", 1),
])
def test_interior_fallthrough_starts_at_next_source_entry(mode, macro, lowered, compare):
    source = f"move 1,R0\nmove 5,R2\ncmp R0,{compare}\njmp R2\n{macro}\nwait 8\nend: stop 17"
    state = run(source, mode)
    if mode != "analysis":
        assert state.metadata["execution"]["status"] == "completed"
    assert state.registers == {"R0": Concrete(1), "R2": Concrete(5)}
    interior, wait, terminal = issues(state)[-3:]
    assert interior.meta["op"] == lowered
    assert interior.meta["branch_taken"] is False
    assert interior.meta["register_reads"] == {}
    assert interior.duration == Concrete(4)
    assert [event.meta["compiled_pc"] for event in (interior, wait, terminal)] == [5, 6, 7]
    assert state.rt_packets[0].duration == Concrete(8)


@pytest.mark.parametrize("register_target", [False, True])
@pytest.mark.parametrize(("op", "compare", "taken", "duration"), [
    ("jnz", 1, False, 4), ("jnz", 0, True, 16), ("jge", 2, False, 4), ("jge", 0, True, 24),
])
def test_status_branch_to_interior_matches_concrete_selected_path(register_target, op, compare, taken, duration):
    setup, target = ("move 6,R2", "R2") if register_target else ("nop", "6")
    source = (f"move 1,R0\n{setup}\ncmp R0,{compare}\n{op} {target}\njmp @fallback\n"
              "loop R1,@end\nfallback: wait 8\nend: stop")
    analysis = run(source, "analysis")
    concrete = run(source, "concrete")

    def trace(state):
        return [(event.meta["op"], event.source.line, event.meta["compiled_pc"], event.duration) for event in issues(state)]

    assert trace(analysis) == trace(concrete)
    branch = issues(analysis)[3]
    assert branch.meta["source_pc"] == 3
    assert branch.meta["compiled_target_pc"] == 6
    assert branch.meta["target_interior_op"] == "jnz"
    assert branch.meta["branch_taken"] is taken
    assert branch.duration == Concrete(duration)
    if taken:
        lowered = issues(analysis)[4]
        assert (lowered.meta["source_pc"], lowered.meta["compiled_pc"], lowered.source.line) == (5, 6, 6)
        assert lowered.meta["macro_source_op"] == "loop"
        assert lowered.meta["compiled_target_pc"] == 8
        assert lowered.meta["branch_taken"] is True
    assert analysis.registers == concrete.registers
    assert "R1" not in analysis.registers
    assert [(event.kind, event.source.line) for event in analysis.events if event.kind in {"wait", "stop"}] == (
        [("stop", 8)] if taken else [("wait", 7), ("stop", 8)]
    )
    assert analysis.rt_time_ns == concrete.rt_time_ns == Concrete(0 if taken else 8)


@pytest.mark.parametrize("backward", [False, True])
@pytest.mark.parametrize("compare", [0, 1])
def test_status_branch_only_proves_interior_traversal_for_the_taken_edge(backward, compare):
    if backward:
        source = f"move 1,R0\nnop\njmp @check\nloop R1,R3\ncheck: cmp R0,{compare}\njnz 4\nwait 8\nstop"
    else:
        source = (f"move 1,R0\nnop\ncmp R0,{compare}\njnz 6\njmp @fallback\n"
                  "loop R1,R3\nfallback: wait 8\nstop")
    state = run(source, "analysis")
    branch = next(event for event in issues(state) if event.meta["op"] == "jnz")
    assert branch.meta["branch_taken"] is (compare == 0)
    assert not any(event.meta.get("macro_source_op") == "loop" for event in issues(state))
    if compare == 0:
        assert not state.rt_packets
        assert any(event.meta.get("reason") == "compiled_interior_branch_target" for event in state.events)
    else:
        assert state.rt_packets[0].duration == Concrete(8)
        assert branch.duration == Concrete(4)


@pytest.mark.parametrize("policy", ["collapse_unresolved", "explore_both_with_depth_limit", "assume_fallthrough", "assume_true"])
def test_unknown_status_branch_preserves_policy_at_compiled_interior(policy):
    source = ("move 1,R0\nnop\nnop\njnz 6\njmp @fallback\n"
              "loop R1,@end\nfallback: wait 8\nend: stop")
    state = interpret_program(parse_q1asm(source), sequencer_id="seq", branch_policy=policy)
    branch = issues(state)[3]
    assert branch.meta["compiled_target_pc"] == 6
    assert branch.meta["branch_comparison_supported"] is False
    if policy == "assume_true":
        lowered = issues(state)[4]
        assert branch.meta["assumed_branch_path"] == "taken"
        assert lowered.meta["compiled_pc"] == 6
        assert lowered.meta["branch_taken"] is True
        assert issues(state)[-1].meta["op"] == "stop"
        assert not state.rt_packets
    elif policy == "assume_fallthrough":
        assert branch.meta["assumed_branch_path"] == "fallthrough"
        assert state.rt_packets[0].duration == Concrete(8)
    else:
        assert branch.meta["branch_taken"] == "runtime_dependent"
        assert any(event.meta.get("reason") == "compiled_interior_branch_target" for event in state.events)
        assert not state.queue_alternatives
        assert not state.rt_packets


@pytest.mark.parametrize("target", ["120", "R2"])
@pytest.mark.parametrize("compare", [0, 1])
def test_lowered_status_branch_needs_a_loaded_successor_only_on_taken(compare, target):
    setup = "nop" if target == "120" else "move 120,R2"
    source = f"move 1,R0\n{setup}\ncmp R0,{compare}\njmp 5\nloop R1,{target}\nwait 8\nstop"
    state = run(source, "analysis")
    if compare == 0:
        assert issues(state)[-1].meta["op"] == "jmp"
        assert any(event.meta.get("reason") == "compiled_interior_branch_target" for event in state.events)
        assert not state.rt_packets
    else:
        assert issues(state)[4].meta["op"] == "jnz"
        assert issues(state)[4].meta["branch_taken"] is False
        assert issues(state)[4].duration == Concrete(4)
        assert state.rt_packets[0].duration == Concrete(8)


@pytest.mark.parametrize("register_target", [False, True])
@pytest.mark.parametrize(("macro", "seed", "taken", "lowered_taken"), [
    ("loop R0", 1, False, None), ("loop R0", 2, True, True),
    ("jlt R0,1", 1, False, None), ("jlt R0,1", 0, True, True),
    ("jge R0,1", 0, False, None), ("jge R0,1", 2, True, True), ("jge R0,1", 1, True, False),
])
def test_first_macro_applies_effects_before_selecting_an_interior(register_target, macro, seed, taken, lowered_taken):
    setup, target = ("move 7,R2", "R2") if register_target else ("move 41,R1", "7")
    source = (f"move {seed},R0\n{setup}\nnop\n{macro},{target}\njmp @fallback\n"
              "loop R1,@end\nfallback: wait 8\nend: stop")
    analysis = run(source, "analysis")
    concrete = run(source, "concrete")
    trace = issues(analysis)

    def steps(events):
        return [(event.meta["op"], event.source.line, event.meta["compiled_pc"], event.duration) for event in events]

    assert steps(trace) == steps(issues(concrete))
    first = trace[3]
    assert (first.meta["source_pc"], first.meta["compiled_pc"], first.meta["compiled_target_pc"]) == (3, 3, 7)
    assert first.meta["branch_taken"] is taken
    assert first.duration == Concrete(24 if taken else 4)
    assert analysis.alu_flags == issues(concrete)[3].meta["flags_after"]
    assert analysis.alu_flags_source.line == 4
    if taken:
        lowered = trace[4]
        assert (lowered.meta["source_pc"], lowered.meta["compiled_pc"], lowered.source.line) == (5, 7, 6)
        assert lowered.meta["op"] == "jnz"
        assert lowered.meta["branch_taken"] is lowered_taken
        assert lowered.meta["macro_source_op"] == "loop"
    assert analysis.registers == concrete.registers
    assert analysis.registers["R0"] == Concrete(seed - 1 if macro.startswith("loop") else seed)
    assert analysis.registers.get("R1") == (None if register_target else Concrete(41))
    assert not any(write.register == "R1" and write.source.line == 6 for write in analysis.register_history)
    assert analysis.rt_time_ns == concrete.rt_time_ns == Concrete(0 if lowered_taken else 8)


@pytest.mark.parametrize("mode", ["analysis", "concrete"])
def test_second_branch_reads_counter_after_first_macro_decrements_it(mode):
    source = ("move 9,R0\nnop\nloop R0,6\njmp @fallback\n"
              "loop R1,R0\nfallback: wait 8\nchosen: wait 4\nstop")
    state = run(source, mode)
    lowered = issues(state)[3]
    assert lowered.meta["op"] == "jnz"
    assert lowered.meta["register_reads"]["R0"]["value"] == 8
    assert lowered.meta["compiled_target_pc"] == 8
    assert [packet.duration for packet in state.rt_packets] == [Concrete(4)]
    assert state.registers == {"R0": Concrete(8)}


@pytest.mark.parametrize(("macro", "seed"), [("loop R0", 2), ("jlt R0,1", 0), ("jge R0,1", 2)])
@pytest.mark.parametrize("ready", [False, True])
def test_first_macro_keeps_unsafe_or_unknown_interior_path_incomplete(macro, seed, ready):
    setup = f"move {seed},R0" if ready else "nop"
    source = f"{setup}\nnop\n{macro},6\njmp @fallback\nloop R1,R3\nfallback: wait 8\nstop"
    state = run(source, "analysis")
    first = issues(state)[-1]
    assert first.source.line == 3
    assert first.meta["branch_taken"] == (True if ready else "runtime_dependent")
    if ready:
        assert first.duration == Concrete(24)
        assert state.registers["R0"] == Concrete(seed - 1 if macro.startswith("loop") else seed)
        assert state.alu_flags_source.line == 3
    assert "R1" not in state.registers
    assert not state.rt_packets
    assert any(event.meta.get("reason") == "compiled_interior_branch_target" for event in state.events)


@pytest.mark.parametrize("count", [1, 2])
def test_loop_self_interior_is_ignored_only_on_known_fallthrough(count):
    state = run(f"move {count},R0\nnop\nloop R0,3\nwait 8\nstop", "analysis")
    loop = issues(state)[2]
    assert loop.meta["compiled_target_pc"] == 3
    if count == 1:
        assert loop.meta["branch_taken"] is False
        assert loop.duration == Concrete(4)
        assert state.registers["R0"] == Concrete(0)
        assert state.rt_packets[0].duration == Concrete(8)
    else:
        assert loop.meta["branch_taken"] == "runtime_dependent"
        assert loop.meta["reason"] == "compiled_interior_branch_target"
        assert state.registers["R0"] == Concrete(2)
        assert not any(write.meta.get("op") == "loop" for write in state.register_history)
        assert not state.rt_packets


@pytest.mark.parametrize(("op", "seed", "taken"), [("jlt", 0, True), ("jlt", 1, False),
                                                   ("jge", 1, True), ("jge", 0, False)])
def test_legacy_comparison_does_not_traverse_its_own_branch_slot(op, seed, taken):
    state = run(f"move {seed},R0\nnop\n{op} R0,1,3\nwait 8\nstop", "analysis")
    branch = issues(state)[2]
    assert branch.meta["compiled_target_pc"] == 3
    assert branch.meta["branch_taken"] is taken
    assert branch.duration == Concrete(24 if taken else 4)
    assert not any(event.meta.get("macro_source_op") for event in issues(state))
    if taken:
        assert branch.meta["reason"] == "compiled_interior_branch_target"
        assert not state.rt_packets
    else:
        assert state.rt_packets[0].duration == Concrete(8)


@pytest.mark.parametrize("mode", ["analysis", "concrete"])
def test_lowered_loop_reads_register_as_target_without_a_counter_write(mode):
    source = "move 7,R0\nnop\ncmp R0,0\njmp 5\nloop R0,R0\nwait 8\nend: stop 17"
    state = run(source, mode)
    interior, terminal = issues(state)[-2:]
    assert interior.meta["op"] == "jnz"
    assert interior.meta["branch_taken"] is True
    assert interior.meta["compiled_target_pc"] == 7
    assert terminal.meta["op"] == "stop"
    assert state.registers == {"R0": Concrete(7)}
    assert not any(item.details.get("internal_loop_target_read") for item in state.diagnostics)


@pytest.mark.parametrize("mode", ["analysis", "concrete"])
def test_lowered_target_uses_register_write_ready_at_its_own_issue(mode):
    source = "move 1,R2\nnop\ncmp R2,0\nmove 8,R0\njmp 6\nloop R1,R0\nwait 8\nend: stop 17"
    state = run(source, mode)
    interior, terminal = issues(state)[-2:]
    assert interior.meta["op"] == "jnz"
    assert interior.meta["compiled_target_pc"] == 8
    assert interior.meta["branch_taken"] is True
    assert terminal.meta["compiled_pc"] == 8
    assert not state.rt_packets


@pytest.mark.parametrize("mode", ["analysis", "concrete"])
def test_known_forward_chain_visits_only_the_compiled_branch_slots(mode):
    source = "move 1,R0\nnop\ncmp R0,0\njmp 5\nloop R1,7\nloop R2,@end\nwait 8\nend: stop 17"
    state = run(source, mode)
    trace = issues(state)[3:]
    assert [(event.meta["op"], event.meta["compiled_pc"]) for event in trace] == [
        ("jmp", 3), ("jnz", 5), ("jnz", 7), ("stop", 9),
    ]
    assert [event.meta["compiled_target_pc"] for event in trace[:-1]] == [5, 7, 9]
    assert state.registers == {"R0": Concrete(1)}


def test_source_only_queue_probe_does_not_substitute_the_macro_entry():
    source = """jge R7,1,@other
wait 100
jmp @join
other: wait 100
join: move 1,R0
nop
cmp R0,1
jmp 10
jge R0,2,@done
wait 40
done: stop
"""
    state = run(source, "analysis")
    assert any(event.meta.get("branch_rt_join_proven") for event in issues(state))
    assert issues(state)[-2].meta["op"] == "jae"
    assert state.rt_time_ns == Concrete(100)
    assert not state.queue_alternatives


def test_queue_probe_rejects_a_known_taken_but_unsafe_compiled_interior():
    source = ("jge R7,1,@other\nwait 100\njmp @join\nother: wait 100\njoin: move 1,R0\n"
              "nop\ncmp R0,0\njnz 10\njge R0,2,R3\nwait 40\nstop")
    state = run(source, "analysis")
    assert any(event.meta.get("branch_rt_join_proven") for event in issues(state))
    terminal_branch = issues(state)[-1]
    assert terminal_branch.meta["op"] == "jnz"
    assert terminal_branch.meta["branch_taken"] is True
    assert terminal_branch.meta["reason"] == "compiled_interior_branch_target"
    assert not state.queue_alternatives
    assert state.rt_time_ns == Concrete(100)


@pytest.mark.parametrize("source", [
    "move 1,R2\nnop\ncmp R2,0\njmp 5\nloop R1,0\nstop",
    "move 1,R2\nnop\ncmp R2,0\njmp 5\nloop R1,R0\nstop",
    "move 6,R0\nnop\ncmp R0,0\nmove 7,R0\njmp R0\nloop R1,@end\nend: stop",
])
def test_static_interior_does_not_follow_unsafe_transition(source):
    state = run(source, "analysis")
    assert issues(state)[-1].meta["op"] == "jmp"
    assert any(item.details.get("reason") == "compiled_interior_branch_target" for item in state.diagnostics)
    assert not any(event.kind == "stop" for event in state.events)


def test_playback_does_not_fold_macro_entry_and_interior_as_repeated_pc():
    source = "move 1,R0\nnop\nloop R0,@end\ncmp 1,R0\njmp 3\nend: stop"
    playback = PlaybackSession({"seq": ("compiled.q1asm", source)})
    assert playback.run(max_steps=30, budget_ms=1000)["status"] == "completed"
    assert playback.snapshot()["playback"]["loops"] == []
    trace = issues(playback.runners[0].state)
    assert [event.meta["compiled_pc"] for event in trace if event.source.line == 3] == [2, 3]


@pytest.mark.parametrize("mode", MODES)
def test_missing_compiled_address_reports_the_address_space(mode):
    state = run("jmp 120\nloop R1,@end\nend: stop", mode)
    execution = state.metadata["execution"]
    assert execution["status"] in {"blocked", "error"}
    assert "Compiled branch target PC 120" in execution["reason"]
    assert execution["compiled_pc"] == 0
    assert not state.register_history


@pytest.mark.parametrize("mode", ["analysis", *MODES])
@pytest.mark.parametrize(("branch", "target"), [("jnz", "120"), ("jnz", "R1"), ("loop", "120"), ("loop", "R1"),
                                                   ("jlt", "120"), ("jge", "120")])
def test_unloaded_conditional_target_is_ignored_on_fallthrough(mode, branch, target):
    address = "move 120,R1\n" if target == "R1" else ""
    prefix = f"move 1,R0\n{address}nop\n"
    statement = {"jnz": f"cmp R0,1\njnz {target}", "loop": f"loop R0,{target}",
                 "jlt": f"jlt R0,1,{target}", "jge": f"jge R0,2,{target}"}[branch]
    state = run(f"{prefix}{statement}\nwait 4\nstop 17", mode)
    edge, wait, stop = issues(state)[-3:]
    assert edge.meta["op"] == branch
    assert edge.meta["branch_taken"] is False
    assert edge.duration == Concrete(4)
    assert wait.meta["op"] == "wait"
    assert stop.meta["stop_code"] == 17
    assert state.rt_packets[0].duration == Concrete(4)
    if branch == "loop":
        assert state.registers["R0"] == Concrete(0)
        assert state.alu_flags["zf"] is True


@pytest.mark.parametrize("mode", ["concrete", "simulation"])
def test_unloaded_taken_branch_reports_its_original_and_compiled_source(mode):
    source = "move 1,R0\nnop\ncmp R0,0\njmp @branch\nloop R1,@branch\nbranch: jnz 120\nwait 4\nstop"
    state = run(source, mode)
    execution = state.metadata["execution"]
    assert execution["status"] == "error"
    assert execution["reason"] == "Compiled branch target PC 120 has no instruction."
    assert execution["pc"] == 5
    assert execution["compiled_pc"] == 6
    assert execution["source"]["line"] == 6
    assert state.diagnostics[-1].source.line == 6
    assert state.diagnostics[-1].details["compiled_pc"] == 6
    assert not state.rt_packets
    assert [event.meta["op"] for event in issues(state)] == ["move", "nop", "cmp", "jmp"]


@pytest.mark.parametrize(("compare", "taken"), [(1, True), (0, False)])
def test_simulation_uses_forced_edge_before_checking_unloaded_destination(compare, taken):
    source = f"move 1,R0\nnop\ncmp R0,{compare}\njnz 120\nwait 4\nstop"
    state = run(source, "simulation", branch_choices={("seq", 3, 1): taken})
    execution = state.metadata["execution"]
    assert state.metadata["branches"]["assumptions"][0]["assumed_branch_path"] == ("taken" if taken else "fallthrough")
    if taken:
        assert execution["status"] == "error"
        assert execution["reason"] == "Branch target PC 120 has no instruction."
        assert execution["source"]["line"] == 4
        assert not state.rt_packets
        assert [event.meta["op"] for event in issues(state)] == ["move", "nop", "cmp"]
    else:
        assert execution["status"] == "completed"
        branch = next(event for event in issues(state) if event.meta["op"] == "jnz")
        assert branch.meta["branch_taken"] is False
        assert branch.meta["natural_branch_taken"] is True
        assert branch.duration == Concrete(4)
        assert state.rt_packets[0].duration == Concrete(4)


@pytest.mark.parametrize("mode", ["concrete", "simulation"])
@pytest.mark.parametrize(("source", "reason"), [
    ("move 1,R0\nnop\ncmp R0,1\njnz R1\nwait 4\nstop", "Operand R1 is unresolved"),
    ("move 1,R0\nnop\ncmp R0,1\nmove 120,R1\njnz R1\nwait 4\nstop", "reads R1 before"),
    ("jnz 120\nwait 4\nstop", "Branch status flags are unknown"),
])
def test_unloaded_target_change_preserves_branch_operand_and_flag_readiness(mode, source, reason):
    state = run(source, mode)
    assert reason in state.metadata["execution"]["reason"]
    assert not state.rt_packets
    assert all(event.meta["op"] != "jnz" for event in issues(state))


@pytest.mark.parametrize("mode", ["analysis", *MODES])
def test_untaken_target_still_has_to_fit_the_immediate_encoding(mode):
    state = run("move 1,R0\nnop\ncmp R0,1\njnz 16384\nwait 4\nstop", mode)
    assert any(item.category == "invalid_argument_value" and "0..16383" in item.message for item in state.diagnostics)
    assert all(event.meta["op"] != "jnz" for event in issues(state))


@pytest.mark.parametrize("mode", ["analysis", *MODES])
def test_cfg_numeric_entry_points_to_source_node(mode):
    state = run("jmp 4\nloop R1,@end\nmove 9,R2\nend: stop", mode)
    edge = graph_edge(state, "jmp")
    assert edge["compiled_target_pc"] == 4
    assert edge["target_pc"] == 3
    assert edge["to_node_id"] == "seq:cfg:n3"
    assert not state.register_history


@pytest.mark.parametrize("operand", ["4", "R0"])
def test_static_interior_is_explicitly_incomplete_and_cfg_has_no_source_entry(operand):
    state = run(f"move 4,R0\nnop\njmp {operand}\nloop R1,@end\nend: stop", "analysis")
    diagnostic = next(item for item in state.diagnostics if item.details.get("reason") == "compiled_interior_branch_target")
    assert diagnostic.details["target_interior_op"] == "jnz"
    assert diagnostic.source.line == 3
    edge = graph_edge(state, "jmp")
    assert edge["compiled_target_pc"] == 4
    assert edge["target_interior_op"] == "jnz"
    assert "target_pc" not in edge
    assert "to_node_id" not in edge
