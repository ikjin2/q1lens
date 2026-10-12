from __future__ import annotations

import json

import pytest

from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


def run(source: str | dict[str, str], *, sequences=None, **config):
    documents = source if isinstance(source, dict) else {"seq": source}
    return simulate_programs({name: parse_q1asm(text, file=name) for name, text in documents.items()}, simulation=config, sequences=sequences)


def events(state, kind):
    return [event for event in state.events if event.kind == kind]


def status(state):
    return state.metadata["execution"]["status"]


def test_finite_queue_stalls_q1_and_dequeues_at_rt_cadence() -> None:
    state, = run("wait 20\nwait 20\nwait 20\nstop", queue_capacity=1)
    assert status(state) == "completed"
    assert [(event.t0.value, event.t1.value) for event in events(state, "q1_stall")] == [(4, 200), (204, 220)]
    assert [packet.rt_t0.value for packet in state.rt_packets] == [200, 220, 240]
    assert [packet.q1_issue_t0.value for packet in state.rt_packets] == [0, 200, 220]
    assert all(event.meta["depth"] == 1 for event in events(state, "rt_queue_enqueue"))
    assert all(event.meta["depth"] == 0 for event in events(state, "rt_queue_dequeue"))
    assert state.rt_time_ns == Concrete(260)


def test_underflow_is_actual_error_and_stop_disables_underflow_after_drain() -> None:
    failed, = run("wait 4\n" + "nop\n" * 10 + "stop", startup_delay_ns=8)
    assert status(failed) == "error"
    assert events(failed, "underflow_warning")[0].t0 == Concrete(12)
    stopped, = run("wait 4\nstop", startup_delay_ns=8)
    assert status(stopped) == "completed"
    assert not events(stopped, "underflow_warning")


def test_underflow_closes_unresumed_feedback_stall_with_pending_cause() -> None:
    state, = run("wait 4\nfb_pop_data 5,R0\nstop", startup_delay_ns=20)
    assert status(state) == "error"
    stall = events(state, "q1_stall")[0]
    assert (stall.t0.value, stall.t1.value, stall.meta["reason"], stall.meta["pending"]) == (4, 24, "feedback_input", True)
    assert state.diagnostics[-1].category == "rt_queue_underflow"
    assert state.diagnostics[-1].source is not None


def test_register_matures_during_long_instruction_not_at_next_q1_start() -> None:
    state, = run("move 5,R0\nmove 9,R1\nadd R0,1,R2\nstop")
    assert status(state) == "completed"
    assert [(write.register, write.time.value, write.value.value) for write in state.register_history] == [
        ("R0", 8, 5), ("R1", 12, 9), ("R2", 24, 6),
    ]
    assert next(event for event in events(state, "q1_issue") if event.meta["op"] == "add").meta["register_reads"]["R0"]["value"] == 5


def test_raw_hazard_is_not_silently_zero_initialized() -> None:
    state, = run("move 7,R0\nadd R0,1,R1\nstop")
    assert status(state) == "error"
    assert state.registers == {}
    assert state.register_history == []


def test_queued_operands_and_setters_are_snapshots_with_exact_origin_ids() -> None:
    state, = run("set_freq -4\nupd_param 12\nset_freq 8\nupd_param 12\nstop")
    assert status(state) == "completed"
    updates = events(state, "upd_param")
    pending = events(state, "latched_state_pending")
    assert [event.meta["applied_state"]["frequency"] for event in updates] == [-4, 8]
    assert [event.meta["applied_setter_event_ids"]["frequency"] for event in updates] == [event.id for event in pending]
    assert pending[1].t0.value < updates[0].t0.value
    assert len({event.id for event in state.events}) == len(state.events)


def test_wait_sync_is_a_barrier_and_missing_participant_blocks() -> None:
    a, b = run({"a": "wait 8\nwait_sync 4\nwait 8\nstop", "b": "wait 28\nwait_sync 4\nwait 8\nstop"})
    assert status(a) == status(b) == "completed"
    assert events(a, "sync_wait")[0].duration == Concrete(20)
    assert [state.rt_packets[-1].rt_t0.value for state in (a, b)] == [232, 232]
    a, b = run({"a": "wait_sync 4\nstop", "b": "wait 4\nstop"})
    assert status(a) == "blocked"
    assert status(b) == "completed"
    assert events(a, "sync_wait")[0].meta["pending"] is True


def test_feedback_routes_wake_waiting_q1_and_keep_writer_input_provenance() -> None:
    sender, receiver = run({
        "sender": "wait_sync 4\nfb_com_data 5,7,4\nwait 100\nstop",
        "receiver": "wait_sync 4\nwait 100\nfb_pop_data 5,R0\nnop\nadd R0,1,R1\nnop\nstop",
    }, routes=[{"source": "sender", "target": "receiver", "id": 5, "latency_ns": 12}])
    assert status(sender) == status(receiver) == "completed"
    receive = events(receiver, "feedback_receive")[0]
    assert receive.t0 == Concrete(216)
    assert receiver.registers["R1"] == Concrete(8)
    write = next(write for write in receiver.register_history if write.register == "R0")
    assert write.time == Concrete(224)
    assert write.meta["provenance"]["input_event_id"] == receive.id
    assert receive.meta["source_event_id"] == events(sender, "feedback_send")[0].id


def test_feedback_send_links_each_repeated_q1_producer_and_captured_register_value() -> None:
    sender, receiver = run({
        "sender": "move 5528,R15\nmove 2,R0\nsend: fb_com_data 17,R15,4\nadd R15,4,R15\nnop\nloop R0,@send\nstop",
        "receiver": "wait 100\nfb_pop_data 17,R1\nfb_pop_data 17,R2\nnop\nstop",
    }, routes=[{"source": "sender", "target": "receiver", "id": 17, "latency_ns": 12}])
    assert status(sender) == status(receiver) == "completed"
    sends = events(sender, "feedback_send")
    issues = [event for event in events(sender, "q1_issue") if event.meta["op"] == "fb_com_data"]
    assert len(sends) == len(issues) == 2
    assert issues[0].source == issues[1].source
    assert issues[0].id != issues[1].id
    assert sender.registers["R15"] == Concrete(5536)
    assert [event.t0.value for event in sends] == [200, 204]
    assert [event.meta["value"] for event in sends] == [5528, 5532]
    for send, issue, receive, write in zip(sends, issues, events(receiver, "feedback_receive"), receiver.register_history):
        assert send.meta["q1_issue_event_id"] == issue.id
        assert send.meta["source_packet_id"] == issue.meta["rt_packet_id"]
        packet = next(packet for packet in sender.rt_packets if packet.id == send.meta["source_packet_id"])
        assert packet.meta["q1_issue_event_id"] == issue.id
        assert receive.meta["source_event_id"] == send.id
        assert write.meta["provenance"]["input_event_id"] == receive.id
        assert issue.meta["register_reads"]["R15"]["value"] == send.meta["value"]
        producer_write = next(write for write in reversed(sender.register_history)
                              if write.register == "R15" and write.time.value <= issue.t0.value)
        assert producer_write.value.value == send.meta["value"]


def test_scenario_feedback_does_not_invent_a_q1_producer() -> None:
    state, = run("fb_pop_data 17,R1\nnop\nstop", inputs={"feedback": [
        {"target": "seq", "time_ns": 20, "id": 17, "value": 5528},
    ]})
    assert status(state) == "completed"
    receive, = events(state, "feedback_receive")
    assert receive.meta["input_index"] == 0
    assert "q1_issue_event_id" not in receive.meta
    assert "source_packet_id" not in receive.meta
    assert "source_event_id" not in receive.meta
    assert not events(state, "feedback_send")


def test_feedback_pop_discards_preceding_fifo_entries_and_pull_reads_id_register() -> None:
    state, = run("move 5,R0\nnop\nwait 100\nfb_pull_data R0,R1\nnop\nstop", inputs={"feedback": [
        {"target": "seq", "time_ns": 0, "id": 9, "value": 99},
        {"target": "seq", "time_ns": 0, "id": 5, "value": -7},
    ]})
    assert status(state) == "completed"
    assert state.registers == {"R0": Concrete(5), "R1": Concrete(0xFFFFFFF9)}
    assert len(events(state, "feedback_discard")) == 1
    pull = next(event for event in events(state, "q1_issue") if event.meta["op"] == "fb_pull_data")
    assert pull.meta["register_reads"]["R0"]["value"] == 5


def test_feedback_overflow_and_missing_input_do_not_succeed() -> None:
    overflow, = run("wait 100\nstop", feedback_queue_capacity=1, inputs={"feedback": [
        {"target": "seq", "time_ns": 0, "id": 1, "value": 1},
        {"target": "seq", "time_ns": 0, "id": 2, "value": 2},
    ]})
    assert status(overflow) == "error"
    assert "overflow" in overflow.metadata["execution"]["reason"]
    missing, = run("wait_trigger 1,4\nfb_pop_data 5,R0\nstop")
    assert status(missing) == "blocked"


def test_disabled_feedback_id_and_inputs_after_stop_do_not_emit_messages() -> None:
    state, = run("fb_com_data 0,7,4\nstop", inputs={"feedback": [
        {"target": "seq", "time_ns": 10000, "id": 1, "value": 1},
    ]})
    assert status(state) == "completed"
    assert not events(state, "feedback_send")
    assert not events(state, "feedback_receive")
    assert state.metadata["simulation"]["end_time_ns"] == 204


@pytest.mark.parametrize(("operator", "expected"), [(0, True), (1, False), (2, True), (3, False), (4, True), (5, False)])
def test_conditional_execution_uses_trigger_counts_at_rt_time(operator: int, expected: bool) -> None:
    state, = run(f"set_latch_en 1,4\nwait 8\nset_cond 1,1,{operator},20\nset_mrk 3\nupd_param 4\nstop",
        inputs={"triggers": [{"target": "seq", "time_ns": 205, "address": 1}]})
    assert status(state) == "completed"
    assert bool(events(state, "upd_param")) == expected
    assert bool(events(state, "conditional_skip")) != expected
    assert state.rt_time_ns == Concrete(216 if expected else 232)


def test_wait_trigger_ignores_earlier_arrival_and_resumes_on_later_input() -> None:
    state, = run("wait 8\nwait_trigger 1,4\nstop", inputs={"triggers": [
        {"target": "seq", "time_ns": 205, "address": 1},
        {"target": "seq", "time_ns": 240, "address": 1},
    ]})
    assert status(state) == "completed"
    assert events(state, "trigger_wait")[0].duration == Concrete(32)
    assert state.rt_time_ns == Concrete(244)


def test_acquisition_scenario_is_only_emitted_at_completion_and_overrides_physical_input() -> None:
    state, = run("acquire 0,0,20\nstop", inputs={"acquisitions": [
        {"sequencer": "seq", "acquisition_index": 0, "bin_index": 0, "i": 99, "q": 7, "thresholded": 1},
    ]}, physical={"sequencers": {"seq": {"input": {"kind": "constant", "i": 0, "q": 0}}}})
    assert status(state) == "completed"
    result = events(state, "acquisition_result")[0]
    assert result.t0 == Concrete(220)
    assert result.meta["i"] == 99
    assert result.meta["source"]["kind"] == "scenario"
    assert not state.metadata["simulation"]["signals"]["acquisitions"]


def test_acquisition_without_input_is_blocked_instead_of_inventing_zero() -> None:
    state, = run("acquire 0,0,20\nstop")
    assert status(state) == "blocked"
    assert not events(state, "acquisition_result")


def test_acquisition_completion_includes_drive_changes_during_the_window() -> None:
    drive, readout = run({
        "drive": "set_awg_offs 0,0\nupd_param 8\nset_awg_offs 16384,0\nupd_param 20\nstop",
        "readout": "acquire 0,0,16\nwait 12\nstop",
    }, physical={"sequencers": {"readout": {"input": {"kind": "loopback", "sequencer": "drive"}}}},
       sequences={"readout": {"acquisitions": {"result": {"index": 0, "num_bins": 1}}}})
    assert status(drive) == status(readout) == "completed"
    result = events(readout, "acquisition_result")[0]
    assert result.meta["i"] == pytest.approx(4)
    assert result.t0 == Concrete(216)
    assert result.meta["source"]["kind"] == "loopback"


def test_threshold_acquisition_feedback_arrives_after_completion_plus_latency() -> None:
    readout, control = run({
        "readout": "fb_acq_tb_id 5,4\nacquire 0,0,16\nwait 100\nstop",
        "control": "wait 200\nfb_pop_data 5,R0\nnop\nstop",
    }, inputs={"acquisitions": [{"sequencer": "readout", "acquisition_index": 0, "bin_index": 0, "thresholded": 1}]},
       routes=[{"source": "readout", "target": "control", "id": 5, "latency_ns": 12}])
    assert status(readout) == status(control) == "completed"
    result = events(readout, "acquisition_result")[0]
    assert result.t0 == Concrete(220)
    assert events(control, "feedback_receive")[0].t0 == Concrete(232)
    assert control.registers["R0"] == Concrete(3)


def test_time_limit_and_identical_simulations_are_deterministic() -> None:
    source = {"a": "wait_trigger 1,4\nstop", "b": "again: wait 100\njmp @again"}
    first = run(source, max_time_ns=300)
    second = run(source, max_time_ns=300)
    assert first == second
    assert all(status(state) == "step_limit" for state in first)
    assert all(state.metadata["simulation"]["end_time_ns"] == 300 for state in first)
    json.dumps([state.metadata for state in first], allow_nan=False)


def test_digital_simulation_does_not_require_analog_waveform_memory() -> None:
    state, = run("play 0,1,8\nstop")
    assert status(state) == "completed"
    assert state.metadata["simulation"]["physical_enabled"] is False
    assert state.metadata["simulation"]["signals"] == {}
    assert state.rt_packets[0].meta["operand_snapshot"] == [0, 1, 8]


def test_physical_iq_feedback_requires_explicit_scale_and_keeps_word_order() -> None:
    programs = {
        "readout": "fb_acq_iq_id 5,4\nacquire 0,0,8\nwait 100\nstop",
        "control": "wait 200\nfb_pop_data 5,R0\nnop\nfb_pop_data 5,R1\nnop\nstop",
    }
    config = {
        "physical": {"sequencers": {"readout": {"input": {"kind": "constant", "i": 0.5, "q": -0.25}}}},
        "routes": [{"source": "readout", "target": "control", "id": 5, "latency_ns": 0}],
    }
    memory = {"readout": {"acquisitions": {"result": {"index": 0, "num_bins": 1}}}}
    readout, control = run(programs, sequences=memory, acquisition_iq_scale={"readout": 10}, **config)
    assert status(readout) == status(control) == "completed"
    assert control.registers == {"R0": Concrete(40), "R1": Concrete(0xFFFFFFEC)}
    unscaled, _ = run(programs, sequences=memory, **config)
    assert status(unscaled) == "blocked"
    assert "acquisition_iq_scale" in unscaled.metadata["execution"]["reason"]
    assert not events(unscaled, "feedback_send")


def test_threshold_result_can_route_an_explicit_delayed_trigger() -> None:
    readout, control = run({"readout": "acquire 0,0,8\nstop", "control": "wait_trigger 2,4\nstop"},
        inputs={"acquisitions": [{"sequencer": "readout", "acquisition_index": 0, "bin_index": 0, "thresholded": 1}]},
        trigger_routes=[{"source": "readout", "target": "control", "address": 2, "latency_ns": 12, "thresholded": 1}])
    assert status(readout) == status(control) == "completed"
    trigger = events(control, "trigger_receive")[0]
    assert trigger.t0 == Concrete(220)
    assert trigger.meta["source_event_id"] == events(readout, "acquisition_result")[0].id
    assert control.rt_time_ns == Concrete(224)


def test_weighted_acquisition_completes_at_weight_window_not_rt_duration() -> None:
    memory = {"seq": {
        "acquisitions": {"result": {"index": 0, "num_bins": 1}},
        "weights": {"i": {"index": 0, "data": [1, 1, 1, 1]}, "q": {"index": 1, "data": [1, 1, 1, 1]}},
    }}
    state, = run("acquire_weighed 0,0,0,1,20\nstop", sequences=memory,
                physical={"sequencers": {"seq": {"input": {"kind": "constant", "i": 0.5, "q": 0}}}})
    assert status(state) == "completed"
    assert events(state, "acquisition_result")[0].t0 == Concrete(204)
    assert events(state, "acquisition_result")[0].meta["i"] == pytest.approx(2)
    assert state.rt_time_ns == Concrete(220)


def test_conditional_skip_exposes_captured_state_but_does_not_apply_it() -> None:
    state, = run("set_cond 1,1,0,8\nset_mrk 3\nupd_param 4\nset_cond 0,1,0,8\nupd_param 4\nstop")
    assert status(state) == "completed"
    skipped = events(state, "conditional_skip")[0]
    assert skipped.meta["captured_state"]["marker"] == 3
    assert skipped.meta["applied_state"] == {}
    assert skipped.meta["applied_setter_event_ids"] == {}
    assert events(state, "upd_param")[0].meta["applied_state"]["marker"] == 3


@pytest.mark.parametrize(("op", "source", "operand", "expected"), [
    ("muls16", -1, 2, 0xFFFFFFFE),
    ("muls16", 0xFFFF8000, -1, 32768),
    ("mulu16", -1, 65535, 65535 * 65535),
    ("mulu16", 0x10001, 3, 3),
])
def test_documented_16_bit_multiply_semantics(op: str, source: int, operand: int, expected: int) -> None:
    state, = run(f"move {source},R0\nnop\n{op} R0,{operand},R1\nstop")
    assert status(state) == "completed"
    assert state.registers["R1"] == Concrete(expected)
    assert state.register_history[-1].time == Concrete(24)


@pytest.mark.parametrize("programs,options", [
    ([], {}), ({"s": "stop"}, {}), ({1: parse_q1asm("stop")}, {}),
    ({"s": parse_q1asm("stop")}, {"sequences": []}),
    ({"s": parse_q1asm("stop")}, {"sequences": [{}]}),
    ({"s": parse_q1asm("stop")}, {"sequences": {"s": []}}),
    ({"s": parse_q1asm("stop")}, {"sequences": {"missing": {}}}),
    ({"s": parse_q1asm("stop")}, {"params": []}),
    ({"s": parse_q1asm("stop")}, {"params": {"COUNT": True}}),
])
def test_runtime_rejects_invalid_public_input_shapes(programs, options) -> None:
    with pytest.raises(ValueError):
        simulate_programs(programs, **options)


def test_strict_q1asm_reports_placeholder_definitions_and_operands() -> None:
    state, = simulate_programs({"seq": parse_q1asm(".DEF D {DELAY}\nwait {DELAY}\nstop")}, strict_q1asm=True)
    assert status(state) == "blocked"
    assert state.strict_q1asm is True
    assert sum(item.details.get("strict_q1asm", False) for item in state.diagnostics) == 2


def test_finite_iq_inputs_with_overflowing_scale_return_structured_error() -> None:
    state, = run("fb_acq_iq_id 5,4\nacquire 0,0,8\nwait 100\nstop",
        acquisition_iq_scale={"seq": 1e308}, inputs={"acquisitions": [
            {"sequencer": "seq", "acquisition_index": 0, "bin_index": 0, "i": 1e308, "q": 0},
        ]})
    assert status(state) == "error"
    assert "outside signed 32-bit range" in state.metadata["execution"]["reason"]
    assert not events(state, "feedback_send")


def test_phase_reset_remains_pending_during_wait_until_latch_updating_packet() -> None:
    state, = run("set_awg_offs 16384,0\nset_freq 250000000\nupd_param 4\nreset_ph\nwait 4\nupd_param 4\nstop",
                physical={"sequencers": {"seq": {}}})
    assert status(state) == "completed"
    paths = {trace["path"]: trace["samples"] for trace in state.metadata["simulation"]["signals"]["traces"] if trace["stage"] == "nco"}
    assert paths["i"][204] == pytest.approx(0, abs=1e-10)
    assert paths["q"][204] == pytest.approx(0.5)
    assert paths["i"][208] == pytest.approx(0.5)
    assert paths["q"][208] == pytest.approx(0, abs=1e-10)
