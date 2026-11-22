"""Symbolic baseband sequencer output."""
from .build import from_state
from .model import Output, Repeat, Segment

__all__ = ["Output", "Repeat", "Segment", "from_state", "output"]


def output(seq_dict, params=None):
    """Analyze an in-memory Qblox sequence and optionally bind its integer parameters."""
    from q1timeline.analysis.interpreter import interpret_program
    from q1timeline.q1asm.parser import parse_q1asm

    if not isinstance(seq_dict, dict) or not isinstance(seq_dict.get("program"), str):
        raise ValueError("Expected a sequence dictionary with a Q1ASM program string")
    state = interpret_program(parse_q1asm(seq_dict["program"], file="<sequence>"), sequencer_id="seq")
    model = from_state(state, seq_dict)
    return model if params is None else model.bind(params)
