from q1timeline.analysis import interpreter
from q1timeline.q1asm.parser import parse_q1asm


def test_analysis_uses_core_decisions_flags_and_counter_for_resolved_branches(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Resolved branch was evaluated a second time")

    monkeypatch.setattr(interpreter, "_comparison_flags", unexpected)
    monkeypatch.setattr(interpreter, "_status_branch_decision", unexpected)
    monkeypatch.setattr(interpreter, "_concrete_branch_test", unexpected)
    program = parse_q1asm(
        "move -1,R0\nnop\njlt R0,1,@end\njge R0,1,@status\nstop 7\n"
        "status: jb @end\njns @end\nmove 2,R1\nnop\nloop R1,@end\nstop 6\nend: stop R1"
    )
    state = interpreter.interpret_program(program, sequencer_id="s")
    branches = [event for event in state.events if event.kind == "q1_issue" and "branch_taken" in event.meta]
    assert [(event.label, event.meta["branch_taken"], event.duration.value) for event in branches] == [
        ("jlt", False, 16), ("jge", True, 28), ("jb", False, 4), ("jns", False, 4), ("loop", True, 32)
    ]
    assert state.registers["R1"].value == 1
    assert next(event for event in state.events if event.kind == "stop").meta["stop_code"] == 1


def test_loop_requires_an_initialized_counter_for_exact_issue_timing():
    program = parse_q1asm("again: wait 8\nloop R0,@again\nstop")
    state = interpreter.interpret_program(program, sequencer_id="s", params={"R0": 1})
    loop, = [e for e in state.events if e.kind == "q1_issue" and e.label == "loop"]
    assert loop.duration.value == 32
    assert not isinstance(state.q1_time_ns, interpreter.Concrete)
