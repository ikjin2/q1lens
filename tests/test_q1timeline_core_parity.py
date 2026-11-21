"""Compare every issue boundary of short Q1ASM programs to Concrete mode."""
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from q1timeline.analysis.interpreter import interpret_program, prepare_def_program
from q1timeline.q1asm import core
from q1timeline.q1asm.core import CoreState, prepare_program, step
from q1timeline.q1asm.parser import parse_q1asm


MIXED_PROGRAM = """\
.DEF COUNT 2
move $COUNT,R0
move 4,R1
nop
body: add R1,4,R2
set_mrk 3
wait R2
move R2,R1
loop R0,@body
upd_param 4
wait 0
stop R2
"""


@pytest.mark.parametrize("statistics", [False, True], ids=["trace", "prepared-def"])
def test_mixed_concrete_execution_keeps_packet_clocks_and_stop_code(statistics):
    program = parse_q1asm(MIXED_PROGRAM)
    prepared = prepare_def_program(program, params={}, strict_q1asm=False, varying={"COUNT"}) if statistics else None
    state = interpret_program(program, sequencer_id="seq0", execution_mode="concrete", prepared=prepared)
    execution = state.metadata["execution"]
    assert (execution["status"], execution["steps"], execution["stop_code"]) == ("completed", 16, 12)
    assert {reg: word.value for reg, word in state.registers.items()} == {"R0": 0, "R1": 12, "R2": 12}
    assert (state.q1_time_ns.value, state.rt_time_ns.value) == (124, 28)
    assert [
        (p.op, p.q1_issue_t0.value, p.q1_issue_t1.value, p.rt_t0.value, p.rt_t1.value)
        for p in state.rt_packets
    ] == [
        ("wait", 28, 32, 0, 8), ("wait", 84, 88, 8, 20),
        ("upd_param", 112, 116, 20, 24), ("wait", 116, 120, 24, 24),
    ]


@pytest.mark.parametrize("statistics", [False, True], ids=["trace", "prepared-def"])
def test_concrete_issues_supported_instructions_through_core(monkeypatch, statistics):
    program = parse_q1asm(MIXED_PROGRAM)
    prepared = prepare_def_program(program, params={}, strict_q1asm=False, varying={"COUNT"}) if statistics else None
    issued = []
    issue = core.issue

    def record(instruction, operands, flags):
        issued.append((instruction.op, operands))
        return issue(instruction, operands, flags)

    monkeypatch.setattr(core, "issue", record)
    interpret_program(program, sequencer_id="seq0", execution_mode="concrete", prepared=prepared)
    assert issued == [
        ("move", (2,)), ("move", (4,)), ("nop", ()),
        ("add", (4, 4)), ("set_mrk", (3,)), ("wait", (8,)), ("move", (8,)), ("loop", (2, 3)),
        ("add", (8, 4)), ("set_mrk", (3,)), ("wait", (12,)), ("move", (12,)), ("loop", (1, 3)),
        ("upd_param", (4,)), ("wait", (0,)), ("stop", (12,)),
    ]


def test_mixed_trace_links_each_register_move_and_applied_setter_to_its_source():
    state = interpret_program(parse_q1asm(MIXED_PROGRAM), sequencer_id="seq0", execution_mode="concrete")
    issues = [event for event in state.events if event.kind == "q1_issue"]
    assert [event.meta["compiled_pc"] for event in issues] == [0, 1, 2, 3, 4, 5, 6, 7, 3, 4, 5, 6, 7, 9, 10, 11]
    loops = [event for event in issues if event.meta["op"] == "loop"]
    assert [(e.t0.value, e.t1.value, e.meta["branch_taken"], e.meta["flags_after"]["zf"]) for e in loops] == [
        (36, 68, True, False), (92, 112, False, True),
    ]
    adds = [event for event in issues if event.meta["op"] == "add"]
    copies = [w for w in state.register_history if w.register == "R1" and "R2" in w.meta["provenance"]["inputs"]]
    assert [(w.value.value, w.time.value, w.meta["provenance"]["inputs"]["R2"]["event_id"]) for w in copies] == [
        (8, 68, adds[0].id), (12, 112, adds[1].id),
    ]
    setters = [event for event in state.events if event.kind == "latched_state_pending"]
    update = next(event for event in state.events if event.kind == "upd_param")
    assert update.meta["applied_state"]["marker"] == 3
    assert update.meta["applied_setter_event_ids"]["marker"] == setters[-1].id


def check_prefixes(source, *, steps, final_status="completed"):
    program = parse_q1asm(source, file="core-parity.q1asm")
    code = prepare_program(program)
    state = CoreState()
    for issued in range(1, steps + 1):
        reference = interpret_program(program, sequencer_id="seq0", execution_mode="concrete", max_steps=issued)
        execution = reference.metadata["execution"]
        status = execution["status"]
        assert status == (final_status if issued == steps else "step_limit")
        issues = [event for event in reference.events if event.kind == "q1_issue"]
        assert len(issues) == issued
        event = issues[-1]
        assert state.pc == event.meta.get("compiled_pc", event.meta["source_pc"])
        assert code[state.pc].pc == event.meta["source_pc"]
        assert state.q1_ns == event.t0.value
        state, packet = step(state, code)
        assert state.q1_ns == event.t1.value == reference.q1_time_ns.value
        assert state.rt_ns == reference.rt_time_ns.value
        assert state.halted == (status == "completed")
        assert state.stop_code == execution.get("stop_code")
        assert (asdict(state.flags) if state.flags is not None else None) == event.meta["flags_after"]
        if not state.halted:
            assert state.pc == execution.get("compiled_pc", execution["pc"])
        assert len(state.registers) == 64
        assert {f"R{i}": value for i, value in enumerate(state.registers) if value is not None} == {
            register: value.value for register, value in reference.registers.items()
        }
        if event.meta["emits_rt_packet"]:
            expected = reference.rt_packets[-1]
            assert packet is not None
            assert (packet.q1_issue_t0, packet.q1_issue_t1) == (expected.q1_issue_t0.value, expected.q1_issue_t1.value)
            assert (packet.rt_t0, packet.rt_t1) == (expected.rt_t0.value, expected.rt_t1.value)
            assert packet.rt_t1 - packet.rt_t0 == expected.duration.value
        else:
            assert packet is None
    return state, code


@pytest.mark.parametrize("count", [1, 2, 5])
def test_counted_wait_loop_matches_concrete(count):
    source = f"move {count},R0\nnop\nbody: wait 12\nloop R0,@body\nstop"
    check_prefixes(source, steps=3 + 2 * count)


@pytest.mark.parametrize("count", [0, 0x80000000, 0xFFFFFFFF])
def test_large_or_wrapped_loop_matches_bounded_concrete_prefix(count):
    source = f"move {count},R0\nnop\nbody: wait 4\nloop R0,@body\nstop"
    check_prefixes(source, steps=8, final_status="step_limit")


def test_register_operand_wait_and_overlapping_moves_match_concrete():
    check_prefixes("move 8,R63\nmove 12,R2\nmove R63,R1\nwait R2\nwait R1\nstop R63", steps=6)


def test_zero_duration_wait_packet_and_next_dequeue_match_concrete():
    check_prefixes("wait 0\nwait 4\nstop", steps=3)


def test_stop_retires_last_pending_move():
    check_prefixes("move -1,R63\nstop", steps=2)


def test_forward_loop_targets_label_after_compiled_macro():
    check_prefixes("move 2,R0\nnop\nloop R0,@done\nwait 4\ndone: wait 12\nstop", steps=5)


@pytest.mark.parametrize(("example", "steps"), [("01-counted-inline", 10), ("03-nested-counted", 17)])
def test_loop_selection_examples_match_concrete(example, steps):
    path = Path(__file__).resolve().parents[1] / "examples" / "loop-selection-review" / example / "program.q1asm"
    check_prefixes(path.read_text(encoding="utf-8"), steps=steps)


def test_literal_def_and_label_loaded_into_register_use_compiled_address():
    source = ".DEF COUNT 2\nmove @done,R1\nmove $COUNT,R0\nnop\nloop R0,R1\nwait 24\ndone: stop -7"
    check_prefixes(source, steps=5)


@pytest.mark.parametrize(("prefix", "category", "message"), [
    ("move 8,R0", "register_not_ready", "not ready"),
    ("move 8,R0\nnop\nmove 4,R0", "register_not_ready", "not ready"),
    ("move 3,R0\nnop", "invalid_argument_value", "duration"),
    ("move 65536,R0\nnop", "invalid_argument_value", "duration"),
])
def test_refused_wait_issues_no_packet_and_preserves_state(prefix, category, message):
    source = f"{prefix}\nwait R0\nstop"
    issued = len(prefix.splitlines())
    state, code = check_prefixes(source, steps=issued, final_status="step_limit")
    before = asdict(state)
    with pytest.raises(ValueError, match=message):
        step(state, code)
    assert asdict(state) == before
    reference = interpret_program(parse_q1asm(source), sequencer_id="seq0", execution_mode="concrete")
    execution = reference.metadata["execution"]
    assert (execution["status"], execution["steps"]) == ("error", issued)
    assert any(d.category == category for d in reference.diagnostics)
    assert (state.q1_ns, state.rt_ns) == (reference.q1_time_ns.value, reference.rt_time_ns.value)
    assert reference.rt_packets == []


def test_unsupported_instruction_rejected_during_preparation():
    with pytest.raises(ValueError, match="not_a_q1_op"):
        prepare_program(parse_q1asm("not_a_q1_op\nstop"))


def test_loop_can_target_only_the_branch_half_of_another_loop():
    source = "move 2,R0\nnop\nloop R0,5\nloop R1,@done\nstop -9\ndone: wait 4\nstop"
    check_prefixes(source, steps=6)


def test_out_of_range_register_target_is_refused_even_on_loop_fallthrough():
    source = "move 16384,R2\nmove 1,R1\nnop\nloop R1,R2\nstop"
    state, code = check_prefixes(source, steps=3, final_status="step_limit")
    with pytest.raises(ValueError, match="address range"):
        step(state, code)
    reference = interpret_program(parse_q1asm(source), sequencer_id="seq0", execution_mode="concrete")
    assert reference.metadata["execution"]["status"] == "error"
    assert reference.metadata["execution"]["steps"] == 3


@pytest.mark.parametrize(("op", "taken"), [
    ("jmp", True), ("jz", False), ("jnz", True), ("jo", False), ("jno", True),
    ("js", True), ("jns", False), ("jg", False), ("jl", True), ("jle", True),
    ("jge", False), ("ja", False), ("jae", False), ("jb", True), ("jbe", True),
])
def test_status_jump_uses_flags_and_observed_issue_cost(op, taken):
    source = f"move 0,R0\nnop\ncmp R0,1\n{op} @taken\nstop 10\ntaken: stop 20"
    state, _ = check_prefixes(source, steps=5)
    assert state.stop_code == (20 if taken else 10)
    assert state.q1_ns == 24 + (16 if taken else 4)
    assert state.flags == core.Flags(zf=False, nf=True, cf=True, of=False)


@pytest.mark.parametrize(("op", "operand", "taken"), [
    ("jlt", 0, False), ("jge", 0, True), ("jlt", -1, True), ("jge", -1, False),
])
def test_two_slot_comparison_wraps_to_unsigned_and_sets_subtraction_flags(op, operand, taken):
    source = f"move 0,R0\nnop\n{op} R0,{operand},@done\nstop 10\ndone: stop 20"
    state, _ = check_prefixes(source, steps=4)
    assert state.stop_code == (20 if taken else 10)
    assert state.q1_ns == 12 + (28 if taken else 16)
    assert state.flags == core.Flags(zf=operand == 0, nf=False, cf=operand == -1, of=False)


@pytest.mark.parametrize("immediate", [-2147483649, 4294967296])
def test_comparison_rejects_out_of_word_range_immediates(immediate):
    program = parse_q1asm(f"move 0,R0\nnop\njlt R0,{immediate},@done\ndone: stop")
    with pytest.raises(ValueError, match="32-bit immediate range"):
        prepare_program(program)
    live = interpret_program(program, sequencer_id="s", execution_mode="concrete")
    assert any(diagnostic.severity == "error" for diagnostic in live.diagnostics)


def test_illegal_faults_at_end_of_issue_and_retires_the_preceding_write():
    program = parse_q1asm("move 7,R0\nillegal\nstop")
    code = prepare_program(program)
    state, _ = step(CoreState(), code)
    state, packet = step(state, code)
    live = interpret_program(program, sequencer_id="s", execution_mode="concrete")
    fault = live.metadata["execution"]["fault"]
    assert state.halted and state.stop_code is None and packet is None
    assert state.fault == fault["flag"] == "SEQUENCE_PROCESSOR_RT_EXEC_ILLEGAL_INSTRUCTION"
    assert state.q1_ns == fault["q1_time_ns"] == 8
    assert state.pc == live.pc == 1
    assert state.registers[0] == live.registers["R0"].value == 7


@pytest.mark.parametrize("statistics", [False, True], ids=["trace", "prepared-def"])
def test_concrete_follows_the_pc_and_clock_returned_by_step(monkeypatch, statistics):
    program = parse_q1asm("nop\nstop 11\nstop 22")
    prepared = prepare_def_program(program, params={}, strict_q1asm=False, varying=set()) if statistics else None
    original = core.step
    visited = []

    def redirect(state, code, **kwargs):
        visited.append(state.pc)
        transition = original(state, code, **kwargs)
        if state.pc == 0:
            return replace(transition, state=replace(transition.state, pc=2, q1_ns=40))
        return transition

    monkeypatch.setattr(core, "step", redirect)
    live = interpret_program(program, sequencer_id="s", execution_mode="concrete", prepared=prepared)
    assert visited == [0, 2]
    assert live.metadata["execution"]["stop_code"] == 22
    assert live.q1_time_ns.value == 44


def test_device_receipts_and_one_shots_reach_the_concrete_timeline():
    program = parse_q1asm("reset_netzero\nset_time_ref\nwait_trigger 2,0\nfb_pop_data 1,R0\nnop\n"
                          "acquire_timetags 1,2,0,511,8\nstop R0")
    external = core.ExternalInputs(trigger_arrivals=((20, 2),), feedback_arrivals=((70, 1, 0xFFFFFFF9),))
    live = interpret_program(program, sequencer_id="s", execution_mode="concrete", external=external)
    wait, acquisition = live.rt_packets
    assert (wait.rt_t0.value, wait.rt_t1.value, acquisition.rt_t0.value) == (0, 20, 24)
    assert wait.meta["trigger_assumption"]["arrival_ns"] == 20
    assert acquisition.meta["device_operands"] == [1, 2, 0, 511, 8]
    assert acquisition.meta["applied_state"] == {"netzero_reset": True, "time_ref": True}
    setter_ids = {e.meta["field"]: e.id for e in live.events if e.kind == "latched_state_pending"}
    assert acquisition.meta["applied_setter_event_ids"] == setter_ids
    receive, = [event for event in live.events if event.kind == "feedback_pop"]
    assert (receive.t0.value, receive.meta["value"], receive.meta["id"]) == (70, 0xFFFFFFF9, 1)
    assert live.metadata["execution"]["stop_code"] == -7


def test_fifo_overflow_reports_rejected_arrival_at_its_q1_time():
    program = parse_q1asm("nop\nstop")
    external = core.ExternalInputs(feedback_arrivals=((1, 1, 10), (5, 2, 20)), feedback_queue_capacity=1)
    live = interpret_program(program, sequencer_id="s", execution_mode="concrete", external=external)
    result = live.metadata["execution"]
    assert (result["status"], result["steps"], result["terminal"]["local_time_ns"]) == ("error", 1, 5)
    marker, = [event for event in live.events if event.kind == "feedback_rejected"]
    diagnostic, = [item for item in live.diagnostics if item.category == "feedback_queue_overflow"]
    assert marker.t0.value == 5 and marker.meta["id"] == 2
    assert diagnostic.related_events == [marker.id]
