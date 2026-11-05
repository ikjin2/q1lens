"""The event-driven engine preserves natural predicates when selecting a path."""
import pytest

from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


def test_forcing_loop_fallthrough_preserves_decrement_and_flags():
    program = parse_q1asm("move 3,R0\nnop\nbody: wait 8\nloop R0,@body\nstop")
    natural, = simulate_programs({"seq": program})
    changed, = simulate_programs({"seq": program}, branch_choices={("seq", 3, 2): False})
    loop_events = lambda state: [event for event in state.events if event.kind == "q1_issue" and event.meta["op"] == "loop"]
    normal_loops, changed_loops = loop_events(natural), loop_events(changed)
    assert len(normal_loops) == 3 and len(changed_loops) == 2
    assert changed_loops[0] == normal_loops[0]
    forced = changed_loops[1]
    assert forced.meta["natural_branch_taken"] is True and forced.meta["branch_taken"] is False
    assert forced.meta["flags_after"] == normal_loops[1].meta["flags_after"]
    assert forced.duration == Concrete(4)
    assert changed.registers["R0"] == Concrete(1)
    write = next(item for item in changed.register_history if item.event_id == forced.id)
    assert write.time == forced.t1
    assert write.confidence == "assumed"


@pytest.mark.parametrize("choices", [
    {("seq", 3, 0): True}, {("seq", 3, True): True}, {("seq", 3, 1): 1},
    {("missing", 3, 1): True}, {("seq", 0, 1): True}, {("seq", 99, 1): True},
    {("seq", 3, 4): True}, {("seq", 3, 1): False, ("seq", 3, 2): True},
])
def test_invalid_or_unreachable_engine_choices_are_rejected(choices):
    program = parse_q1asm("move 3,R0\nnop\nbody: wait 8\nloop R0,@body\nstop")
    with pytest.raises(ValueError):
        simulate_programs({"seq": program}, branch_choices=choices)
