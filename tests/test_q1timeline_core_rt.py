import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.q1asm.core import CoreState, prepare_program, step
from q1timeline.q1asm.parser import parse_q1asm


def execute_both(source):
    program = parse_q1asm(source)
    reference = interpret_program(program, sequencer_id="s", execution_mode="concrete")
    assert reference.metadata["execution"]["status"] == "completed"
    issues = [e for e in reference.events if e.kind == "q1_issue"]
    code = prepare_program(program)
    state = CoreState()
    applied = []
    for event in issues:
        assert state.pc == event.meta.get("compiled_pc", event.meta["source_pc"])
        item = code[state.pc]
        state, packet = step(state, code)
        assert state.q1_ns == event.t1.value
        if packet is None:
            assert not event.meta["emits_rt_packet"]
            continue
        expected = reference.rt_packets[len(applied)]
        assert expected.id == event.meta["rt_packet_id"]
        assert packet.op == expected.op
        assert packet.operands == tuple(event.meta["register_reads"][arg.value]["value"]
                                        if arg.kind == "reg" else arg.value for arg in item.args)
        assert (packet.q1_issue_t0, packet.q1_issue_t1, packet.rt_t0, packet.rt_t1) == (
            expected.q1_issue_t0.value, expected.q1_issue_t1.value, expected.rt_t0.value, expected.rt_t1.value)
        changes = {w.field: list(w.value) if isinstance(w.value, tuple) else w.value for w in packet.applied}
        emitted = [e.meta["applied_state"] for e in reference.events
                   if e.meta.get("rt_packet_id") == expected.id and "applied_state" in e.meta]
        assert all(result == changes for result in emitted)
        assert changes == {} or emitted
        applied.append((expected.op, changes))
    assert len(applied) == len(reference.rt_packets)
    assert state.halted and state.rt_ns == reference.rt_time_ns.value
    return state, applied, reference


def test_latches_cross_wait_and_sync_and_commit_one_shots_once():
    source = """set_mrk 3
set_mrk 7
reset_ph
set_ph 11
set_ph_delta 13
wait 4
wait_sync 8
play 1,2,16
acquire 0,2,4
set_scope_en 1
acquire_weighed 0,1,2,3,4
set_digital 1,3,0
acquire_weighted 0,1,2,3,4
upd_param 0
stop"""
    state, packets, _ = execute_both(source)
    assert packets == [
        ("wait", {}), ("wait_sync", {}),
        ("play", {"marker": 7, "phase": 11, "phase_delta": 13, "phase_reset": True}),
        ("acquire", {}), ("acquire_weighed", {"scope_enable": 1}),
        ("acquire_weighted", {"digital": [1, 3, 0]}), ("upd_param", {}),
    ]
    assert (state.q1_ns, state.rt_ns) == (60, 44)
    assert state.latches.pending == ()
    assert {w.field: w.value for w in state.latches.active} == {
        "marker": 7, "phase": 11, "scope_enable": 1, "digital": (1, 3, 0),
    }


def test_register_latches_decode_words_and_preserve_register_bits():
    source = """.DEF WORD R3
move -1,R3
nop
set_freq $WORD
set_awg_gain R3,R3
set_awg_offs $WORD,$WORD
set_mrk $WORD
upd_param 4
stop"""
    state, packets, _ = execute_both(source)
    assert state.registers[3] == 0xFFFFFFFF
    assert packets == [("upd_param", {"frequency": -1, "awg_gain": [-1, -1], "awg_offset": [-1, -1], "marker": 15})]


def test_phase_reset_clears_an_earlier_offset_on_commit():
    state, packets, reference = execute_both("set_ph 17\nupd_param 4\nreset_ph\nwait_sync 4\nupd_param 4\nstop")
    assert packets[-1] == ("upd_param", {"phase_reset": True})
    assert {w.field: w.value for w in state.latches.active} == {"phase": 0}
    assert reference.rt_packets[-1].meta["nco_state_at_issue"]["phase"] == 0


def test_disabled_condition_preserves_unknown_inputs_and_concrete_wait():
    state, packets, reference = execute_both("move 0,R0\nnop\nset_cond R0,R1,R2,4\nwait 8\nstop")
    assert state.condition == (0, None, None, 4)
    assert packets == [("wait", {})]
    assert reference.conditional_mask.reason == "R1" and reference.conditional_operator.reason == "R2"


@pytest.mark.parametrize(("args", "sources"), [
    ("0,UNKNOWN,OTHER,4", ("UNKNOWN", "OTHER")), ("0,{MASK},{OP},4", ("MASK", "OP")),
])
def test_disabled_condition_preserves_ignored_symbolic_operands_in_permissive_concrete(args, sources):
    program = parse_q1asm(f"set_cond {args}\nwait 8\nstop")
    state = interpret_program(program, sequencer_id="s", execution_mode="concrete")
    execution = state.metadata["execution"]
    assert (execution["status"], execution["steps"], state.rt_time_ns.value) == ("completed", 3, 8)
    assert (str(state.conditional_mask), str(state.conditional_operator)) == sources
    assert [e.meta["op"] for e in state.events if e.kind == "q1_issue"] == ["set_cond", "wait", "stop"]


def test_resolved_labels_supply_valid_scope_enable_and_condition_enable_values():
    source = "zero: set_cond @zero,1,2,4\none: set_scope_en @one\nupd_param 4\nstop"
    state, packets, _ = execute_both(source)
    assert state.condition == (0, 1, 2, 4)
    assert packets == [("upd_param", {"scope_enable": 1})]


@pytest.mark.parametrize(("source", "status"), [
    ("move 65535,R0\nnop\nset_awg_gain R0,R0\nstop", "blocked"),
    ("move -1,R0\nnop\nset_ph R0\nstop", "blocked"),
    ("set_cond 1,1,6,4\nstop", "blocked"),
    ("set_cond 0,32768,0,4\nstop", "error"),
    ("move 0,R0\nnop\nmove 7,R1\nset_cond R0,R1,R1,4\nstop", "error"),
    ("set_cond 0,0,0,3\nstop", "error"),
    ("play 1024,1,4\nstop", "error"),
])
def test_unsupported_or_invalid_rt_inputs_refuse_execution(source, status):
    program = parse_q1asm(source)
    reference = interpret_program(program, sequencer_id="s", execution_mode="concrete", strict_q1asm=True)
    assert reference.metadata["execution"]["status"] == status
    with pytest.raises(ValueError):
        code = prepare_program(program)
        state = CoreState()
        for _ in program.instructions:
            state, _ = step(state, code)
