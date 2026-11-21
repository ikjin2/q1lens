"""Differential and bounded-search checks for the optional local symbolic executor."""
import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.smt import Query, verify
from q1timeline.q1asm.ast import with_def_values
from q1timeline.q1asm.parser import parse_q1asm


pytest.importorskip("z3")


def source(body):
    return parse_q1asm(".DEF X 0\n" + body)


def check(program, query, low=0, high=2, **limits):
    return verify(program, def_name="X", low=low, high=high, query=query, **limits)


def concrete(program, value):
    state = interpret_program(with_def_values(program, {"X": value}), sequencer_id="s", execution_mode="concrete")
    assert state.metadata["execution"]["status"] == "completed", state.diagnostics
    return state


def test_register_jump_can_enter_macro_interior_and_use_prior_flags():
    program = source("move $X,R0\nmove 0,R1\nnop\ncmp R1,1\njmp R0\njlt R1,0,@end\nstop 0\nend: stop 1")
    # Jumping to 5 reevaluates jlt's comparison. Jumping to 6 executes jb
    # with the carry from the earlier cmp.
    result = check(program, Query("stop", "==", 0), 5, 6)
    assert (result.status, result.def_value, result.register_value) == ("counterexample", 6, 1)
    assert concrete(program, 5).metadata["execution"]["stop_code"] == 0
    assert concrete(program, 6).metadata["execution"]["stop_code"] == result.register_value
    witness = check(program, Query("path", path=((4, True), (6, True))), 5, 6)
    assert (witness.status, witness.def_value, witness.steps) == ("reachable", 6, 6)
    assert check(program, Query("reachable", pc=6), 5, 5).status == "unreachable"


def test_ordered_branch_query_distinguishes_visits_to_same_loop():
    program = source("move $X,R0\nnop\nloop_here: loop R0,@loop_here\nstop")
    path = Query("path", path=((2, True), (2, True), (2, False)))
    assert check(program, path, 1, 2).status == "unreachable"
    witness = check(program, path, 1, 3)
    assert (witness.status, witness.def_value, witness.steps) == ("reachable", 3, 5)
    assert check(program, Query("stop", "==", 0), 1, 3, max_steps=5).status == "unknown"
    assert check(program, Query("stop", "==", 0), 1, 3, max_steps=6).status == "proved"
    assert check(program, path, 1, 3, max_paths=1).status == "unknown"


def test_partial_invalid_domain_can_supply_witness_but_cannot_prove():
    program = source("move $X,R0\nnop\nlsl R0,R0,R2\nstop")
    # A legal shift by 32 yields zero; a shift by 33 has no modeled result.
    assert check(program, Query("register", "<", 1, "R2"), 32, 33).status == "unknown"
    result = check(program, Query("register", "!=", 0, "R2"), 32, 33)
    assert (result.status, result.def_value, result.register_value) == ("counterexample", 32, 0)


@pytest.mark.parametrize("body", [
    "move $X,R0\nnop\nmuls32 R0,-1,R2,R3\nnop\nstop R3",
    "move $X,R0\nnop\nsub R0,1,R2\njb @done\nmove 0,R2\ndone: stop",
    "wait_sync $X\n" + "wait 40\n" * 35 + "move $X,R2\nstop",
    "set_cond 1,1,0,8\nwait_sync $X\nupd_param 4\nmove $X,R2\nstop",
])
def test_scalar_witnesses_and_proofs_match_exhaustive_concrete_runs(body):
    program = source(body)
    cases = {}
    for x in range(4, 8):
        state = concrete(program, x)
        cases[x] = {"q1_time": state.q1_time_ns.value, "rt_time": state.rt_time_ns.value,
                    "stop": state.metadata["execution"]["stop_code"], "register": state.registers["R2"].value}
    for kind in ("register", "stop", "q1_time", "rt_time"):
        maximum = max(case[kind] for case in cases.values())
        observe = "R2" if kind == "register" else None
        assert check(program, Query(kind, "<=", maximum, observe), 4, 7).status == "proved"
        witness = check(program, Query(kind, "<", maximum, observe), 4, 7)
        assert witness.status == "counterexample"
        assert witness.register_value == cases[witness.def_value][kind] == maximum


def test_disabled_set_cond_ignores_mask_and_operator_register_values():
    program = source("move 0,R0\nmove -1,R1\nmove -1,R2\nnop\nset_cond R0,R1,R2,4\nwait $X\nstop")
    assert concrete(program, 8).rt_time_ns.value == 8
    assert check(program, Query("rt_time", "<=", 8), 4, 8).status == "proved"


def test_unreachable_device_path_and_reachable_unmodeled_device():
    program = source("move $X,R0\nnop\njlt R0,2,@end\nwait_trigger 1,4\nend: stop")
    assert check(program, Query("stop", "==", 0), 0, 1).status == "proved"
    assert check(program, Query("stop", "==", 0), 0, 2).status == "unknown"


def test_single_register_predicate_keeps_unsigned_word_boundary():
    program = source("move $X,R2\nstop")
    assert check(program, Query("register", "<", 2**32, "R2"), -1, 2**32 - 1).status == "proved"
    with pytest.raises(ValueError, match="unsigned register bound"):
        check(program, Query("register", "<", -1, "R2"))
