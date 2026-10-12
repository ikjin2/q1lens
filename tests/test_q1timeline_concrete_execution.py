from __future__ import annotations

import json

import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.values import Concrete, Unknown
from q1timeline.q1asm.parser import parse_q1asm


def execute(source: str, **kwargs):
    return interpret_program(parse_q1asm(source, file="execution.q1asm"), sequencer_id="seq0", execution_mode="concrete", **kwargs)


def issues(state):
    return [event for event in state.events if event.kind == "q1_issue"]


def test_loop_executes_each_iteration_and_links_register_causes() -> None:
    state = execute("move 3,R0\nmove 0,R1\nnop\nbody: add R1,1,R1\nnop\nloop R0,@body\nstop")
    assert state.metadata["execution"]["status"] == "completed"
    assert state.metadata["execution"]["steps"] == 13
    assert state.registers == {"R0": Concrete(0), "R1": Concrete(3)}
    trace = issues(state)
    assert [event.meta["execution_index"] for event in trace] == list(range(13))
    assert [event.meta["source_pc"] for event in trace] == [0, 1, 2, 3, 4, 5, 3, 4, 5, 3, 4, 5, 6]
    adds = [event for event in trace if event.meta["op"] == "add"]
    assert [event.meta["register_reads"]["R1"]["value"] for event in adds] == [0, 1, 2]
    writes = [write for write in state.register_history if write.register == "R1"]
    assert writes[0].meta["previous_value"]["kind"] == "unknown"
    for previous, write in zip(writes, writes[1:]):
        assert write.meta["previous_value"]["value"] == previous.value.value
        provenance = write.meta["provenance"]
        assert provenance["inputs"]["R1"]["event_id"] == previous.event_id
        assert provenance["inputs"]["R1"]["value"]["value"] == previous.value.value
        assert len(provenance["steps"]) == 1
        assert provenance["event_id"] == write.event_id
    assert len({event.id for event in state.events}) == len(state.events)
    assert not any(event.kind == "loop_region" for event in state.events)


def test_backward_status_branch_and_forward_jump_follow_the_pc() -> None:
    state = execute("move 0,R0\nnop\nbody: add R0,1,R0\nnop\ncmp R0,3\njnz @body\njmp @end\nmove 99,R0\nend: stop")
    assert state.metadata["execution"]["status"] == "completed"
    assert state.registers["R0"] == Concrete(3)
    branches = [event for event in issues(state) if event.meta["op"] == "jnz"]
    assert [event.meta["branch_taken"] for event in branches] == [True, True, False]
    assert [event.meta["flags_before"]["zf"] for event in branches] == [False, False, True]
    assert all(event.source.line != 8 for event in issues(state))


def test_legacy_comparison_wraps_immediate_to_unsigned_register_width() -> None:
    state = execute("move 0,R0\nnop\njlt R0,-1,@end\nmove 9,R0\nend: stop")
    assert state.metadata["execution"]["status"] == "completed"
    assert state.registers["R0"] == Concrete(0)
    assert issues(state)[2].meta["branch_taken"] is True


def test_nested_loops_execute_without_collapsing() -> None:
    state = execute("move 2,R0\nnop\nouter: move 2,R1\nnop\ninner: nop\nloop R1,@inner\nloop R0,@outer\nstop")
    assert state.metadata["execution"]["status"] == "completed"
    assert state.registers["R0"] == state.registers["R1"] == Concrete(0)
    assert sum(event.source.line == 5 for event in issues(state)) == 4


def test_register_target_and_label_value_work_without_expanded_instructions() -> None:
    state = execute("move @end,R0\nnop\njmp R0\nmove 9,R1\nend: stop")
    assert state.metadata["execution"]["status"] == "completed"
    assert state.registers == {"R0": Concrete(4)}
    assert [event.meta["source_pc"] for event in issues(state)] == [0, 1, 2, 4]


@pytest.mark.parametrize("source", [
    "jmp 3\nloop R0,@end\nmove 7,R1\nend: stop",
    "move @end,R0\nnop\njmp @end\nloop R1,@end\nend: stop",
    "jmp R0\njlt R1,1,@end\nend: stop",
    "add R0,@end,R1\njge R1,1,@end\nend: stop",
])
def test_expanded_instruction_addresses_block_before_creating_false_values(source: str) -> None:
    state = execute(source)
    assert state.metadata["execution"]["status"] == "blocked"
    assert state.metadata["execution"]["steps"] == 0
    assert "Compiled instruction-address mapping is unsupported" in state.metadata["execution"]["reason"]
    assert state.register_history == []


@pytest.mark.parametrize(("source", "expected"), [
    ("move -1,R0\nnop\nadd R0,1,R1\nstop", 0),
    ("move 0,R0\nnop\nsub R0,1,R1\nstop", 0xFFFFFFFF),
    ("move 0x80000000,R0\nnop\nasr R0,1,R1\nstop", 0xC0000000),
    ("move 0x80000000,R0\nnop\nlsr R0,1,R1\nstop", 0x40000000),
    ("move 1,R0\nnop\nlsl R0,32,R1\nstop", 0),
    ("move 0,R0\nnop\nnot R0,R1\nstop", 0xFFFFFFFF),
])
def test_32_bit_arithmetic_and_shift_boundaries(source: str, expected: int) -> None:
    state = execute(source)
    assert state.metadata["execution"]["status"] == "completed"
    assert state.registers["R1"] == Concrete(expected)


def test_trace_records_overflow_flags_before_and_after() -> None:
    state = execute("move 0x7fffffff,R0\nnop\nadd R0,1,R1\nstop")
    event = issues(state)[2]
    assert event.meta["flags_before"] is None
    assert event.meta["flags_after"] == {"zf": False, "nf": True, "cf": False, "of": True}


@pytest.mark.parametrize("source", [
    "wait R0\nstop", "move $MISSING,R0\nstop", "move {COUNT},R0\nstop",
    "jz @done\ndone: stop", "fb_pop_data 0,R0\nstop", "wait_trigger 0,4\nstop",
    "set_cond 1,1,0,4\nstop", "mulu16 R1,3,R0\nstop", "acquire 0,R0,4\nstop",
])
def test_unknown_inputs_and_unimplemented_semantics_block_before_execution(source: str) -> None:
    state = execute(source, branch_policy="assume_true")
    assert state.metadata["execution"]["status"] == "blocked"
    assert state.metadata["execution"]["steps"] == 0
    assert state.metadata["execution"]["reason"]
    assert state.registers == {}
    assert issues(state) == []


@pytest.mark.parametrize("source", [
    "move 1,R64\nstop", "add R0\nstop", "bogus 1\nstop", "illegal\nstop",
    "jmp @missing\nstop", "jmp 99\nstop", "wait -1\nstop", "move 4294967296,R0\nstop",
])
def test_invalid_programs_error_without_executing_instruction(source: str) -> None:
    state = execute(source)
    assert state.metadata["execution"]["status"] == "error"
    assert state.metadata["execution"]["steps"] == 0
    assert issues(state) == []


def test_unresolved_placeholder_in_strict_mode_remains_blocked() -> None:
    state = execute(".DEF COUNT {COUNT}\nmove $COUNT,R0\nstop", strict_q1asm=True)
    assert state.metadata["execution"]["status"] == "blocked"
    assert state.metadata["execution"]["steps"] == 0


def test_parameter_named_like_register_does_not_initialize_hardware_state() -> None:
    state = execute("body: loop R0,@body\nstop", params={"R0": 3})
    assert state.metadata["execution"]["status"] == "blocked"
    assert state.registers == {}


def test_repeated_rt_commands_have_unique_packet_ids_and_accumulated_time() -> None:
    state = execute("move 2,R0\nnop\nbody: play 0,1,8\nloop R0,@body\nstop")
    assert state.metadata["execution"]["status"] == "completed"
    assert state.rt_time_ns == Concrete(16)
    assert len({packet.id for packet in state.rt_packets}) == len(state.rt_packets) == 2
    assert len({event.id for event in state.events}) == len(state.events)


def test_raw_hazard_stops_without_fabricating_write_availability() -> None:
    state = execute("move 7,R0\nmove R0,R1\nstop")
    assert state.metadata["execution"]["status"] == "error"
    assert state.metadata["execution"]["steps"] == 1
    assert any(item.category == "register_not_ready" for item in state.diagnostics)
    assert state.registers == {}
    assert state.register_history == []
    assert len(state.pending_register_writes) == 1


def test_instruction_gap_and_stop_retire_writes_at_readable_time() -> None:
    state = execute("move 7,R0\nnop\nmove R0,R1\nstop")
    assert state.metadata["execution"]["status"] == "completed"
    assert state.registers == {"R0": Concrete(7), "R1": Concrete(7)}
    assert [write.time for write in state.register_history] == [Concrete(8), Concrete(16)]
    assert state.pending_register_writes == []


def test_missing_stop_keeps_the_last_write_pending() -> None:
    state = execute("move 7,R0")
    assert state.metadata["execution"]["status"] == "error"
    assert state.metadata["execution"]["steps"] == 1
    assert state.registers == {}
    assert state.register_history == []
    assert state.pending_register_writes[0].value == Concrete(7)


def test_step_limit_returns_an_exact_prefix_and_zero_counter_wraps() -> None:
    state = execute("move 0,R0\nnop\nbody: loop R0,@body", max_steps=5)
    assert state.metadata["execution"]["status"] == "step_limit"
    assert state.metadata["execution"]["steps"] == 5
    assert state.registers["R0"] == Concrete(0xFFFFFFFD)
    assert len(issues(state)) == 5
    assert execute("stop", max_steps=1).metadata["execution"]["status"] == "completed"


def test_default_budget_keeps_provenance_bounded_per_write() -> None:
    state = execute("move 0,R0\nnop\nbody: add R0,1,R0\nnop\njmp @body")
    assert state.metadata["execution"]["status"] == "step_limit"
    assert state.metadata["execution"]["steps"] == 10000
    assert len(issues(state)) == 10000
    assert all(len(write.meta["provenance"]["steps"]) == 1 for write in state.register_history)
    assert max(len(json.dumps(write.meta["provenance"])) for write in state.register_history) < 1000


def test_rt_packets_and_local_sync_keep_separate_analytical_clocks() -> None:
    state = execute("wait_sync 4\nset_mrk 3\nupd_param 8\nplay 0,1,16\nacquire 0,0,12\nstop")
    execution = state.metadata["execution"]
    assert execution["status"] == "completed"
    assert execution["timing_model"] == "analytical"
    assert execution["address_model"] == "source_instruction_index"
    assert any("PC values index source instructions" in assumption for assumption in execution["assumptions"])
    assert state.rt_time_ns == Concrete(40)
    assert state.q1_time_ns != state.rt_time_ns
    assert [packet.op for packet in state.rt_packets] == ["wait_sync", "upd_param", "play", "acquire"]
    update = next(event for event in state.events if event.kind == "upd_param")
    assert update.meta["applied_state"]["marker"] == 3


@pytest.mark.parametrize(("op", "field", "value", "paired"), [
    ("set_freq", "frequency", -4, False),
    ("set_freq", "frequency", -2000000000, False),
    ("set_freq", "frequency", 2000000000, False),
    ("set_awg_gain", "awg_gain", -1, True),
    ("set_awg_gain", "awg_gain", -32768, True),
    ("set_awg_gain", "awg_gain", 32767, True),
    ("set_awg_offs", "awg_offset", -1, True),
    ("set_awg_offs", "awg_offset", -32768, True),
    ("set_awg_offs", "awg_offset", 32767, True),
])
def test_signed_latched_fields_decode_register_bits_like_immediates(op: str, field: str, value: int, paired: bool) -> None:
    registers = "R0,R0" if paired else "R0"
    immediates = f"{value},{value}" if paired else str(value)
    source = f"move {value},R0\nnop\n{op} {registers}\nupd_param 4\nstop"
    state = execute(source)
    immediate_state = execute(f"{op} {immediates}\nupd_param 4\nstop")
    assert state.metadata["execution"]["status"] == immediate_state.metadata["execution"]["status"] == "completed"
    expected = [value, value] if paired else value
    for result in (state, immediate_state):
        pending = next(event for event in result.events if event.kind == "latched_state_pending")
        applied = next(event for event in result.events if event.kind == "upd_param")
        assert pending.meta["value"] == expected
        assert applied.meta["applied_state"][field] == expected
    setter = next(event for event in issues(state) if event.meta["op"] == op)
    assert setter.meta["register_reads"]["R0"]["value"] == value & 0xFFFFFFFF
    assert state.registers["R0"] == Concrete(value & 0xFFFFFFFF)


@pytest.mark.parametrize(("op", "operand"), [
    ("set_awg_gain", 65535), ("set_awg_gain", 32768), ("set_awg_offs", -32769),
    ("set_ph", 1000000001), ("set_ph_delta", -1),
])
def test_undocumented_latched_register_truncation_blocks(op: str, operand: int) -> None:
    args = "R0,R0" if op in {"set_awg_gain", "set_awg_offs"} else "R0"
    state = execute(f"move {operand},R0\nnop\n{op} {args}\nupd_param 4\nstop")
    assert state.metadata["execution"]["status"] == "blocked"
    assert state.metadata["execution"]["steps"] == 2
    assert "truncation or wrapping is not modeled" in state.metadata["execution"]["reason"]
    assert not any(event.kind == "latched_state_pending" for event in state.events)


def test_marker_bits_are_unsigned_and_phase_stays_unsigned() -> None:
    state = execute("move -1,R0\nmove 1000000000,R1\nset_mrk R0\nset_ph R1\nupd_param 4\nstop")
    assert state.metadata["execution"]["status"] == "completed"
    update = next(event for event in state.events if event.kind == "upd_param")
    assert update.meta["applied_state"] == {"marker": 15, "phase": 1000000000}
    marker = next(event for event in issues(state) if event.meta["op"] == "set_mrk")
    assert marker.meta["register_reads"]["R0"]["value"] == 0xFFFFFFFF


def test_analysis_mode_preserves_existing_latched_register_interpretation() -> None:
    state = interpret_program(parse_q1asm("move -4,R0\nnop\nset_freq R0\nupd_param 4\nstop"), sequencer_id="seq0")
    update = next(event for event in state.events if event.kind == "upd_param")
    assert update.meta["applied_state"]["frequency"] == 4294967292


def test_default_analysis_preserves_compact_loop_and_unknown_following_value() -> None:
    source = "move 3,R0\nmove 0,R1\nnop\nbody: add R1,1,R1\nnop\nloop R0,@body\nmove R1,R2\nnop\nstop"
    program = parse_q1asm(source)
    default = interpret_program(program, sequencer_id="seq0")
    explicit = interpret_program(program, sequencer_id="seq0", execution_mode="analysis")
    assert default == explicit
    assert "execution" not in default.metadata
    assert isinstance(default.registers["R2"], Unknown)
    assert any(write.meta.get("invalidated") for write in default.register_history)


@pytest.mark.parametrize("options", [{"max_steps": 0}, {"max_steps": True}, {"max_steps": 1.5}])
def test_execution_budget_requires_positive_integer(options: dict) -> None:
    with pytest.raises(ValueError, match="max_steps"):
        execute("stop", **options)


@pytest.mark.parametrize("mode", ["simulator", []])
def test_execution_mode_requires_supported_string(mode) -> None:
    with pytest.raises(ValueError, match="execution mode"):
        interpret_program(parse_q1asm("stop"), sequencer_id="seq0", execution_mode=mode)
