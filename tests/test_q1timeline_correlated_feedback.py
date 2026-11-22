import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.q1asm.parser import parse_q1asm


def compare(source):
    return interpret_program(parse_q1asm(source), sequencer_id="control",
                             branch_policy="explore_both_with_depth_limit")


@pytest.mark.parametrize("first,second", [
    ("jge R7,1,@on", "jlt R7,1,@impossible"),
    ("test R7,1\njnz @on", "test R7,1\njz @impossible"),
    ("and R7,32,R2\njnz @on", "test 32,R7\njz @impossible"),
    ("cmp R7,1\njae @on", "cmp R7,1\njb @impossible"),
])
def test_second_test_of_same_feedback_cannot_reach_opposite_arm(first, second):
    program = f"fb_pop_data 5,R7\nnop\n{first}\nwait 20\nstop\non: nop\n{second}\nwait 24\nstop\nimpossible: wait R9\nstop"
    state = compare(program)
    assert sorted(packet.duration.value for packet in state.rt_packets) == [20, 24]
    diagnostic, = [d for d in state.diagnostics if d.category == "unreachable_branch_path"]
    assert diagnostic.details["excluded_path"] == "taken"
    assert diagnostic.details["status"] == "proved"
    assert not [d for d in state.diagnostics if d.category in {"analysis_incomplete", "unknown_duration"}
                and d.source and d.source.line >= program.splitlines().index("on: nop") + 1]


def test_new_feedback_word_invalidates_the_first_test():
    state = compare("fb_pop_data 5,R7\nnop\njge R7,1,@on\nstop\non: fb_pop_data 5,R7\nnop\njlt R7,1,@possible\n"
                    "wait 8\nstop\npossible: wait 40\nstop")
    assert not [d for d in state.diagnostics if d.category == "unreachable_branch_path"]
    assert any(e.meta.get("op") == "jlt" and e.meta["branch_decision"] == "runtime_dependent" for e in state.events)


def test_proved_impossible_path_preserves_a_warning_on_the_feasible_alternative():
    state = compare("fb_pop_data 5,R7\nnop\nmove R7,R8\nnop\njlt R7,1,@zero\nwait R9\nstop\n"
                    "zero: jge R8,1,@impossible\nwait 12\nstop\nimpossible: wait R10\nstop")
    assert any(e.source.line == 6 and e.meta.get("branch_comparison_path") == "fallthrough" for e in state.events)
    assert any(d.source and d.source.line == 6 and d.category == "unknown_duration" for d in state.diagnostics)
    assert not any(e.source.line == 11 for e in state.events)
    finding, = [d for d in state.diagnostics if d.category == "unreachable_branch_path"]
    assert (finding.source.line, finding.details["prior_branch_line"]) == (8, 5)
