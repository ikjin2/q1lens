"""Independent word and flag expectations for the concrete ALU core."""

import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.q1asm import core
from q1timeline.q1asm.core_alu import evaluate
from q1timeline.q1asm.parser import parse_q1asm


@pytest.mark.parametrize(("source", "inputs", "words", "set_flags"), [
    ("not R1,R0", (0,), (0xFFFFFFFF,), {"nf"}),
    ("add R1,R2,R0", (0x7FFFFFFF, 1), (0x80000000,), {"nf", "of"}),
    ("add R1,R2,R0", (0xFFFFFFFF, 1), (0,), {"zf", "cf"}),
    ("sub 1,R1,R0", (1, 0), (0xFFFFFFFF,), {"nf", "cf"}),
    ("cmp 1,R1", (1, 0x80000000), (), {"of"}),
    ("test 1,R1", (1, 0x80000000), (), {"zf"}),
    ("and R1,R2,R0", (0xFFFFFFFF, 0x80000000), (0x80000000,), {"nf"}),
    ("or R1,R2,R0", (0x25, 0x12), (0x37,), set()),
    ("xor R1,R2,R0", (0xFFFFFFFE, 1), (0xFFFFFFFF,), {"nf"}),
    ("lsl 1,R1,R0", (1, 0x40000000), (0x80000000,), {"nf", "of"}),
    ("asl R1,32,R0", (1, 32), (0,), {"zf", "cf", "of"}),
    ("asr R1,32,R0", (0x80000001, 32), (0xFFFFFFFF,), {"nf", "cf"}),
    ("lsr R1,32,R0", (0x80000001, 32), (0,), {"zf", "cf"}),
    ("lsr R1,0,R0", (0x80000001, 0), (0x80000001,), {"nf"}),
    ("muls16 R1,R2,R0", (0xFFFFFFFF, 2), (0xFFFFFFFE,), {"nf"}),
    ("mulu16 R1,R2,R0", (0x10001, 0x10002), (2,), set()),
    ("muls32l R1,R2,R0", (0xFFFFFFFF, 2), (0xFFFFFFFE,), {"nf"}),
    ("mulu32l R1,R2,R0", (0x80000000, 2), (0,), {"zf"}),
    ("muls32h R1,R2,R0", (0xFFFFFFFF, 2), (0xFFFFFFFF,), {"nf"}),
    ("mulu32h R1,R2,R0", (0xFFFFFFFF, 2), (1,), set()),
    ("mulu32 R1,R2,R3,R4", (0xFFFFFFFF, 0xFFFFFFFF), (1, 0xFFFFFFFE), {"nf", "cf", "of"}),
    ("muls32 R1,R2,R3,R4", (0x80000000, 0xFFFFFFFF), (0x80000000, 0), {"cf", "of"}),
    ("muls32 R1,R2,R3,R4", (0xFFFFFFFF, 2), (0xFFFFFFFE, 0xFFFFFFFF), {"nf"}),
])
def test_alu_words_and_all_four_flags(source, inputs, words, set_flags):
    instruction, = parse_q1asm(source).instructions
    result, flags = evaluate(instruction, inputs)
    assert result == words
    assert flags == {name: name in set_flags for name in ("zf", "nf", "cf", "of")}


def test_full_product_trace_preserves_paired_writes_and_alias_reads():
    source = ".DEF LO R2\n.DEF HI R3\nmove -1,R0\nnop\nmulu32 R0,2,$LO,$HI\nnop\nsub 1,$HI,R4\nstop"
    state = interpret_program(parse_q1asm(source), sequencer_id="seq0", execution_mode="concrete")
    assert state.metadata["execution"]["status"] == "completed"
    lo, hi = [w for w in state.register_history if w.source.line == 5]
    assert (lo.register, lo.value.value, hi.register, hi.value.value) == ("R2", 0xFFFFFFFE, "R3", 1)
    assert lo.event_id == hi.event_id and lo.time == hi.time
    last = state.register_history[-1]
    assert (last.register, last.value.value) == ("R4", 0)
    assert last.meta["provenance"]["inputs"]["R3"]["event_id"] == hi.event_id


def test_full_product_words_become_readable_at_the_same_boundary():
    code = core.prepare_program(parse_q1asm("move -1,R0\nnop\nmulu32 R0,2,R2,R3\nnop\nstop"))
    state = core.CoreState()
    for _ in range(3):
        state, _ = core.step(state, code)
    assert state.registers[2:4] == (None, None)
    assert [(w.index, w.value) for w in state.pending_writes] == [(2, 0xFFFFFFFE), (3, 1)]
    state, _ = core.step(state, code)
    assert state.registers[2:4] == (0xFFFFFFFE, 1)
    assert not state.pending_writes


def test_multiply_overflow_selects_the_marker_committed_by_each_play():
    code = core.prepare_program(parse_q1asm(
        "move 2,R0\nmove 0x40000000,R1\nnop\n"
        "body: mulu32 R1,2,R2,R3\njo @carry\nmove R2,R1\nset_mrk 2\njmp @send\n"
        "carry: set_mrk 5\nsend: wait 0\nplay 0,1,8\nloop R0,@body\nstop R3"
    ))
    state = core.CoreState()
    packets = []
    for _ in range(18):
        state, packet = core.step(state, code)
        if packet is not None:
            packets.append((packet.op, packet.q1_issue_t0, packet.rt_t0, packet.rt_t1,
                            {w.field: w.value for w in packet.applied}))
    assert packets == [
        ("wait", 64, 0, 0, {}), ("play", 68, 4, 12, {"marker": 2}),
        ("wait", 148, 12, 12, {}), ("play", 152, 16, 24, {"marker": 5}),
    ]
    assert state.registers[:4] == (0, 0x80000000, 0, 1)
    assert (state.q1_ns, state.rt_ns, state.stop_code) == (180, 24, 1)


@pytest.mark.parametrize("operation", [
    "mulu16 R0,65536,R2", "muls16 -32769,R0,R2", "muls32 R0,2147483648,R2,R3",
    "mulu32l R0,-1,R2", "cmp R0,4294967296", "asr R0,33,R2",
])
def test_standalone_core_refuses_invalid_alu_operands(operation):
    program = parse_q1asm(f"move 1,R0\nnop\n{operation}\nstop")
    with pytest.raises(ValueError):
        code = core.prepare_program(program)
        state = core.CoreState()
        for _ in program.instructions:
            state, _ = core.step(state, code)
