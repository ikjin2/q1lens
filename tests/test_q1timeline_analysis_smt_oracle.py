"""Check Analysis's compact loop and branch-join claims by symbolic path execution."""
import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.smt import Query, verify
from q1timeline.analysis.values import Concrete, Unknown
from q1timeline.q1asm.parser import parse_q1asm


pytest.importorskip("z3")


def analyze_symbolic_input(body):
    # Analysis sees an unknown immediate. The SMT executor receives the same
    # instructions, with that immediate ranging over every unsigned 32-bit word.
    source = ".DEF X {}\nmove $X,R7\nnop\n" + body
    analysis = interpret_program(parse_q1asm(source.format("{input_word}")), sequencer_id="seq0")
    assert not [d for d in analysis.diagnostics if d.severity == "error"]
    return analysis, parse_q1asm(source.format(0))


def solve(program, query, low=0, high=0xFFFFFFFF):
    return verify(program, def_name="X", low=low, high=high, query=query)


def check_exact(program, kind, value, *, low=0, high=0xFFFFFFFF, register=None):
    assert isinstance(value, Concrete)
    result = solve(program, Query(kind, "==", value.value, register), low, high)
    assert result.status == "proved", result


def test_compensating_affine_waits_with_counter_dependent_period():
    state, program = analyze_symbolic_input(
        "and R7,15,R1\nnop\nadd R1,4,R1\nnop\nmove 100,R2\nmove 4,R0\nnop\n"
        "body: wait R1\nsub R2,R1,R3\nnop\nadd R3,R0,R3\nnop\nwait R3\n"
        "add R1,4,R1\nnop\nloop R0,@body\nwait 8\nstop"
    )
    loop, = (event for event in state.events if event.kind == "loop_block")
    assert loop.meta["total_only"] and loop.meta["visible_iteration_count"] == 1
    assert loop.meta["period_affine"]["slope"] == -1
    check_exact(program, "rt_time", state.rt_time_ns)
    check_exact(program, "q1_time", state.q1_time_ns)


@pytest.mark.parametrize("count", [1, 31, 32, 33])
def test_wait_only_loop_clocks_across_fifo_capacity(count):
    # At 33 iterations the final stop must wait for a FIFO slot.
    program = parse_q1asm(f".DEF X {count}\nmove $X,R0\nnop\nbody: wait 65532\nloop R0,@body\nstop")
    state = interpret_program(program, sequencer_id="seq0")
    loop, = (event for event in state.events if event.kind == "loop_block")
    assert loop.meta["wait_only"] and loop.meta["visible_iteration_count"] == 1
    check_exact(program, "q1_time", state.q1_time_ns, low=count, high=count)
    check_exact(program, "rt_time", state.rt_time_ns, low=count, high=count)


def test_zero_dwell_prevents_an_exact_compensating_loop_total():
    state, program = analyze_symbolic_input(
        "and R7,12,R1\nnop\nmove 100,R2\nmove 3,R0\nnop\n"
        "body: wait R1\nsub R2,R1,R3\nnop\nwait R3\nadd R1,4,R1\nnop\nloop R0,@body\nstop"
    )
    loop, = (event for event in state.events if event.kind == "loop_block")
    assert "total_duration" not in loop.meta and isinstance(state.rt_time_ns, Unknown)
    # The mask produces only valid dwells. Zero occupies a four-ns dequeue slot.
    for candidate, other in ((300, 304), (304, 300)):
        witness = solve(program, Query("rt_time", "==", candidate))
        assert (witness.status, witness.register_value) == ("counterexample", other)


def test_balanced_join_clocks_hold_for_both_feasible_edges():
    state, program = analyze_symbolic_input("jge R7,2147483648,@join\nnop\nnop\nnop\njoin: wait 100\nstop")
    branch, = (e for e in state.events if e.kind == "q1_issue" and e.meta["op"] == "jge")
    assert branch.meta["branch_decision"] == "runtime_dependent" and branch.meta["branch_join_proven"]
    for taken in (True, False):
        assert solve(program, Query("path", path=((branch.meta["compiled_pc"], taken),))).status == "reachable"
    check_exact(program, "q1_time", state.q1_time_ns)
    check_exact(program, "rt_time", state.rt_time_ns)


def test_rt_only_join_preserves_register_bounds_and_common_bits():
    state, program = analyze_symbolic_input(
        "jge R7,2147483648,@other\nwait 24\nmove 20,R1\nnop\njmp @join\n"
        "other: wait 24\nmove 12,R1\nnop\njoin: and R1,0xffffffe7,R2\nnop\nwait 4\nstop"
    )
    branch, = (e for e in state.events if e.kind == "q1_issue" and e.meta["op"] == "jge")
    assert branch.meta["branch_rt_join_proven"] and not branch.meta["branch_join_proven"]
    check_exact(program, "rt_time", state.rt_time_ns)
    check_exact(program, "register", state.registers["R2"], register="R2")
    low, high = state.register_bounds["R1"]
    for op, bound, stricter in ((">=", low, ">"), ("<=", high, "<")):
        assert solve(program, Query("register", op, bound, "R1")).status == "proved"
        witness = solve(program, Query("register", stricter, bound, "R1"))
        assert (witness.status, witness.register_value) == ("counterexample", bound)
    assert isinstance(state.q1_time_ns, Unknown)
    # The arm taken at X=0 supplies one possible clock. Another input must disagree.
    at_zero = interpret_program(program, sequencer_id="seq0").q1_time_ns
    check_exact(program, "q1_time", at_zero, low=0, high=0)
    assert solve(program, Query("q1_time", "==", at_zero.value)).status == "counterexample"
