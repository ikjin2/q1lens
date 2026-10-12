from __future__ import annotations

from copy import deepcopy

import pytest

from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.config import validate_simulation
from q1timeline.simulation.runtime import simulate_programs


@pytest.mark.parametrize(("config", "field", "unknown"), [
    ({"routes": [{"source": "seq", "target": "seq", "latency_ns": 0, "idd": 5}]}, "routes[0]", "idd"),
    ({"trigger_routes": [{"source": "seq", "target": "seq", "latency_ns": 0, "address": 1, "threshold": 0}]}, "trigger_routes[0]", "threshold"),
    ({"inputs": {"feedback": [{"target": "seq", "time_ns": 0, "id": 5, "value": 7, "vallue": 8}]}}, "inputs.feedback[0]", "vallue"),
    ({"inputs": {"triggers": [{"target": "seq", "time_ns": 0, "address": 1, "adress": 2}]}}, "inputs.triggers[0]", "adress"),
    ({"inputs": {"acquisitions": [{"sequencer": "seq", "acquisition_index": 0, "bin_index": 0, "i": 1, "threshholded": 0}]}}, "inputs.acquisitions[0]", "threshholded"),
])
def test_nested_config_rejects_unknown_fields_before_they_change_meaning(config, field, unknown) -> None:
    original = deepcopy(config)
    with pytest.raises(ValueError) as error:
        validate_simulation(config, ["seq"])
    assert f"simulation.{field}" in str(error.value)
    assert unknown in str(error.value)
    assert config == original


def test_nested_config_accepts_declared_fields_without_mutating_input() -> None:
    config = {
        "inputs": {
            "feedback": [{"target": "seq", "time_ns": 0, "id": 255, "value": 0xFFFFFFFF}],
            "triggers": [{"target": "seq", "time_ns": 0, "address": 15}],
            "acquisitions": [{"sequencer": "seq", "acquisition_index": 0, "bin_index": 0, "i": 1, "q": -1, "thresholded": 0}],
        },
        "routes": [{"source": "seq", "target": "seq", "id": 255, "latency_ns": 0}],
        "trigger_routes": [{"source": "seq", "target": "seq", "address": 15, "latency_ns": 0, "thresholded": 0}],
    }
    original = deepcopy(config)
    normalized = validate_simulation(config, ["seq"])
    assert config == original
    assert normalized["inputs"] == original["inputs"]
    assert normalized["trigger_routes"][0]["thresholded"] == 0


def run(source: str | dict[str, str], *, sequences=None, **config):
    documents = source if isinstance(source, dict) else {"seq": source}
    return simulate_programs({name: parse_q1asm(text, file=name) for name, text in documents.items()}, simulation=config, sequences=sequences)


def events(state, kind):
    return [event for event in state.events if event.kind == kind]


def status(state):
    return state.metadata["execution"]["status"]


@pytest.mark.parametrize(("startup", "expected"), [(3, "error"), (4, "completed")])
def test_enqueue_at_exact_rt_start_precedes_dequeue(startup, expected) -> None:
    state, = run("wait 4\nstop", startup_delay_ns=startup)
    assert status(state) == expected
    if expected == "error":
        assert events(state, "underflow_warning")[0].t0 == Concrete(3)
        assert state.diagnostics[-1].category == "rt_queue_underflow"
    else:
        enqueue, = events(state, "rt_queue_enqueue")
        dequeue, = events(state, "rt_queue_dequeue")
        assert enqueue.t0 == dequeue.t0 == Concrete(4)
        assert state.events.index(enqueue) < state.events.index(dequeue)
        assert state.rt_time_ns == Concrete(8)


@pytest.mark.parametrize("capacity", [1, 2, 32])
def test_queue_reservations_preserve_capacity_fifo_and_exactly_once_delivery(capacity) -> None:
    state, = run("wait 4\n" * 40 + "stop", queue_capacity=capacity)
    assert status(state) == "completed"
    enqueues = events(state, "rt_queue_enqueue")
    dequeues = events(state, "rt_queue_dequeue")
    packet_ids = [packet.id for packet in state.rt_packets]
    assert [event.meta["rt_packet_id"] for event in enqueues] == packet_ids
    assert [event.meta["rt_packet_id"] for event in dequeues] == packet_ids
    assert len(set(packet_ids)) == 40
    assert all(0 <= event.meta["depth"] <= capacity for event in enqueues + dequeues)
    assert [event.t0.value for event in dequeues] == list(range(200, 360, 4))
    assert len({event.id for event in state.events}) == len(state.events)
    issue_ids = {event.id for event in events(state, "q1_issue")}
    assert all(write.event_id in issue_ids for write in state.register_history)


def test_feedback_at_rt_deadline_cannot_replace_a_missing_rt_packet() -> None:
    state, = run("wait 4\nfb_pop_data 5,R0\nwait 4\nstop", startup_delay_ns=20,
        inputs={"feedback": [{"target": "seq", "time_ns": 24, "id": 5, "value": 7}]})
    assert status(state) == "error"
    receive, = events(state, "feedback_receive")
    underflow, = events(state, "underflow_warning")
    assert receive.t0 == underflow.t0 == Concrete(24)
    assert state.events.index(receive) < state.events.index(underflow)
    assert len(state.rt_packets) == 1
    assert state.register_history == []


@pytest.mark.parametrize(("arrival", "expected", "end"), [(207, "blocked", None), (208, "completed", 212), (209, "completed", 213)])
def test_trigger_one_ns_before_at_and_after_wait_start(arrival, expected, end) -> None:
    state, = run("wait 8\nwait_trigger 1,4\nstop", inputs={"triggers": [
        {"target": "seq", "time_ns": arrival, "address": 1},
    ]})
    assert status(state) == expected
    if end is not None:
        assert state.rt_time_ns == Concrete(end)
        packet = state.rt_packets[1]
        assert packet.meta["input_event_ids"] == [events(state, "trigger_receive")[0].id]
    else:
        assert events(state, "trigger_wait")[0].meta["pending"] is True


@pytest.mark.parametrize("limit", [7, 8])
def test_time_limit_does_not_publish_register_before_readable_time(limit) -> None:
    state, = run("move 7,R0\nnop\nstop", max_time_ns=limit)
    assert status(state) == "step_limit"
    assert state.metadata["simulation"]["end_time_ns"] == limit
    assert [(write.time.value, write.value.value) for write in state.register_history] == ([] if limit == 7 else [(8, 7)])
    assert state.registers == ({} if limit == 7 else {"R0": Concrete(7)})
    assert state.diagnostics[-1].category == "simulation_time_limit"


@pytest.mark.parametrize("limit", [219, 220])
def test_time_limit_does_not_publish_acquisition_or_feedback_before_completion(limit) -> None:
    readout, control = run({
        "readout": "fb_acq_tb_id 5,4\nacquire 0,0,16\nwait 20\nstop",
        "control": "wait 100\nstop",
    }, max_time_ns=limit,
       inputs={"acquisitions": [{"sequencer": "readout", "acquisition_index": 0, "bin_index": 0, "thresholded": 1}]},
       routes=[{"source": "readout", "target": "control", "id": 5, "latency_ns": 0}])
    assert [status(state) for state in (readout, control)] == ["step_limit", "step_limit"]
    expected_times = [] if limit == 219 else [220]
    assert [event.t0.value for event in events(readout, "acquisition_result")] == expected_times
    assert [event.t0.value for event in events(readout, "feedback_send")] == expected_times
    assert [event.t0.value for event in events(control, "feedback_receive")] == expected_times


@pytest.mark.parametrize("limit", [31, 32, 35, 36])
def test_time_limit_does_not_publish_loop_counter_before_branch_completion(limit) -> None:
    state, = run("move 2,R0\nnop\nagain: loop R0,@again\nstop", max_time_ns=limit)
    expected = [(time, value) for time, value in [(8, 2), (32, 1), (36, 0)] if time <= limit]
    assert status(state) == "step_limit"
    assert [(write.time.value, write.value.value) for write in state.register_history] == expected
    assert state.registers["R0"] == Concrete(expected[-1][1])


def test_overlapping_acquisition_invalidates_old_completion_callback() -> None:
    state, = run("acquire 0,0,4\nacquire 0,1,20\nstop",
        physical={"sequencers": {"seq": {"integration_length_ns": 16}}},
        inputs={"acquisitions": [
            {"sequencer": "seq", "acquisition_index": 0, "bin_index": 0, "i": 4, "q": 0},
            {"sequencer": "seq", "acquisition_index": 0, "bin_index": 1, "i": 16, "q": 0},
        ]})
    assert status(state) == "completed"
    results = events(state, "acquisition_result")
    assert [(event.t0.value, event.meta["duration_ns"], event.meta["interrupted"], event.meta["input_index"]) for event in results] == [
        (204, 4, True, 0), (220, 16, False, 1),
    ]
    assert [event.meta["rt_packet_id"] for event in results] == [packet.id for packet in state.rt_packets]
    assert len(state.metadata["simulation"]["acquisitions"]) == 2


def test_register_rt_operands_keep_their_enqueue_time_snapshot() -> None:
    state, = run("move 12,R0\nnop\nwait R0\nmove 20,R0\nnop\nwait R0\nstop")
    assert status(state) == "completed"
    assert state.registers["R0"] == Concrete(20)
    assert [(packet.rt_t0.value, packet.rt_t1.value, packet.meta["operand_snapshot"]) for packet in state.rt_packets] == [
        (200, 212, [12]), (212, 232, [20]),
    ]
    issues = [event for event in events(state, "q1_issue") if event.meta["op"] == "wait"]
    assert [event.meta["register_reads"]["R0"]["value"] for event in issues] == [12, 20]
    writes = [write for write in state.register_history if write.register == "R0"]
    assert [write.value.value for write in writes] == [12, 20]
    assert all(write.time.value <= issue.t0.value for write, issue in zip(writes, issues))


def test_repeated_barrier_rounds_release_only_their_own_waiters() -> None:
    a, b = run({
        "a": "wait 8\nwait_sync 4\nwait 8\nwait_sync 4\nwait 4\nstop",
        "b": "wait 28\nwait_sync 4\nwait 4\nwait_sync 4\nwait 4\nstop",
    })
    assert status(a) == status(b) == "completed"
    assert [(event.t0.value, event.t1.value) for event in events(a, "sync_wait")] == [(208, 228), (240, 240)]
    assert [(event.t0.value, event.t1.value) for event in events(b, "sync_wait")] == [(228, 228), (236, 240)]
    assert [state.rt_packets[-1].rt_t0.value for state in (a, b)] == [244, 244]


def test_unsupported_q1_instruction_removes_pending_sync_waiter() -> None:
    a, b = run({
        "a": "wait_sync 4\n" + "nop\n" * 51 + "upd_thres 0,0,4\nstop",
        "b": "wait 100\nwait_sync 4\nstop",
    })
    assert status(a) == status(b) == "blocked"
    assert a.rt_packets[0].rt_t0 is None
    assert b.rt_packets[1].rt_t0 is None
    wait, = events(a, "sync_wait")
    assert (wait.t0.value, wait.t1.value) == (200, 208)
    assert wait.meta["pending"] is True


# Independent truth table for two selected addresses: OR, NOR, AND, NAND, XOR, XNOR.
@pytest.mark.parametrize(("addresses", "expected"), [
    ([], [False, True, False, True, False, True]),
    ([1], [True, False, False, True, True, False]),
    ([5], [True, False, False, True, True, False]),
    ([1, 5], [True, False, True, False, False, True]),
])
@pytest.mark.parametrize("operator", range(6))
def test_condition_uses_all_selected_trigger_bits(addresses, expected, operator) -> None:
    state, = run(f"set_latch_en 1,4\nwait 8\nset_cond 1,17,{operator},20\nupd_param 4\nstop",
        inputs={"triggers": [{"target": "seq", "time_ns": 212, "address": address} for address in addresses]})
    assert status(state) == "completed"
    assert bool(events(state, "upd_param")) == expected[operator]
    assert bool(events(state, "conditional_skip")) != expected[operator]
    assert state.rt_time_ns == Concrete(216 if expected[operator] else 232)


def test_latch_reset_and_disabled_interval_control_count_lifetime() -> None:
    state, = run(
        "set_latch_en 1,4\nwait 4\nset_cond 1,1,0,4\nupd_param 4\n"
        "set_cond 0,0,0,4\nlatch_rst 4\nset_latch_en 0,4\nwait 4\n"
        "set_cond 1,1,0,4\nupd_param 4\nset_cond 0,0,0,4\n"
        "set_latch_en 1,4\nwait 4\nset_cond 1,1,0,4\nupd_param 4\nstop",
        inputs={"triggers": [{"target": "seq", "time_ns": time, "address": 1} for time in (205, 221, 233)]})
    assert status(state) == "completed"
    assert [event.t0.value for event in events(state, "upd_param")] == [208, 236]
    assert [event.t0.value for event in events(state, "conditional_skip")] == [224]


def test_pop_discards_only_prefix_and_preserves_matching_word_order() -> None:
    state, = run("wait 100\nfb_pop_data 5,R0\nnop\nfb_pop_data 5,R1\nnop\nstop",
        feedback_queue_capacity=3, inputs={"feedback": [
            {"target": "seq", "time_ns": 0, "id": identifier, "value": value}
            for identifier, value in [(9, 99), (5, 7), (5, 8)]
        ]})
    assert status(state) == "completed"
    received = events(state, "feedback_receive")
    assert [event.meta["depth"] for event in received] == [1, 2, 3]
    assert events(state, "feedback_discard")[0].meta["input_event_ids"] == [received[0].id]
    assert state.registers == {"R0": Concrete(7), "R1": Concrete(8)}
    assert [write.meta["provenance"]["input_event_id"] for write in state.register_history] == [received[1].id, received[2].id]


@pytest.mark.parametrize(("count", "expected"), [(2, "completed"), (3, "error")])
def test_feedback_capacity_is_checked_before_same_time_q1_consumption(count, expected) -> None:
    state, = run("wait 100\nfb_pop_data 5,R0\nnop\nstop", feedback_queue_capacity=2,
        inputs={"feedback": [{"target": "seq", "time_ns": 4, "id": 5, "value": value} for value in range(count)]})
    assert status(state) == expected
    assert [event.meta["depth"] for event in events(state, "feedback_receive")] == [1, 2]
    if expected == "completed":
        assert state.registers["R0"] == Concrete(0)
    else:
        assert state.registers == {}
        assert state.diagnostics[-1].category == "feedback_queue_overflow"


def test_zero_latency_self_route_preserves_send_receive_and_write_causes() -> None:
    state, = run("fb_com_data 5,7,4\nwait 100\nfb_pop_data 5,R0\nnop\nstop",
        routes=[{"source": "seq", "target": "seq", "id": 5, "latency_ns": 0}])
    assert status(state) == "completed"
    send, = events(state, "feedback_send")
    receive, = events(state, "feedback_receive")
    assert send.t0 == receive.t0 == Concrete(200)
    assert state.events.index(send) < state.events.index(receive)
    assert receive.meta["source_event_id"] == send.id
    assert state.register_history[0].meta["provenance"]["input_event_id"] == receive.id
    assert state.registers["R0"] == Concrete(7)


def test_broadcast_delivers_only_to_matching_routes() -> None:
    sender, a, b, filtered = run({
        "sender": "fb_com_data 5,0xFFFFFFFF,4\nstop",
        "a": "wait 100\nfb_pop_data 5,R0\nnop\nstop",
        "b": "wait 100\nfb_pop_data 5,R0\nnop\nstop",
        "filtered": "wait 100\nstop",
    }, routes=[
        {"source": "sender", "target": "a", "id": 5, "latency_ns": 0},
        {"source": "sender", "target": "b", "latency_ns": 12},
        {"source": "sender", "target": "filtered", "id": 6, "latency_ns": 0},
    ])
    assert all(status(state) == "completed" for state in (sender, a, b, filtered))
    send, = events(sender, "feedback_send")
    assert send.meta["targets"] == ["a", "b"]
    assert [events(state, "feedback_receive")[0].t0.value for state in (a, b)] == [200, 212]
    assert a.registers["R0"] == b.registers["R0"] == Concrete(0xFFFFFFFF)
    assert all(events(state, "feedback_receive")[0].meta["source_event_id"] == send.id for state in (a, b))
    assert not events(filtered, "feedback_receive")


@pytest.mark.parametrize("thresholded", [0, 1])
@pytest.mark.parametrize("valid", [0, 1])
def test_threshold_feedback_captures_id_and_valid_at_acquisition_start(thresholded, valid) -> None:
    readout, control = run({
        "readout": f"fb_acq_tb_id 5,4\nfb_acq_tb_valid {valid},4\nacquire 0,0,4\nfb_acq_tb_id 6,4\nfb_acq_tb_valid {1-valid},4\nwait 20\nstop",
        "control": "wait 100\nfb_pop_data 5,R0\nnop\nstop",
    }, physical={"sequencers": {"readout": {"integration_length_ns": 16}}},
       inputs={"acquisitions": [{"sequencer": "readout", "acquisition_index": 0, "bin_index": 0, "thresholded": thresholded}]},
       routes=[{"source": "readout", "target": "control", "latency_ns": 0}])
    assert status(readout) == status(control) == "completed"
    send, = events(readout, "feedback_send")
    assert (send.t0.value, send.meta["id"], send.meta["value"]) == (224, 5, valid * 2 + thresholded)
    assert send.meta["source_event_id"] == events(readout, "acquisition_result")[0].id
    assert control.registers["R0"] == Concrete(valid * 2 + thresholded)


@pytest.mark.parametrize(("shift", "expected"), [(0, [0x80000000, 0x7FFFFFFF]), (31, [0xFFFFFFFF, 0]), (32, [0xFFFFFFFF, 0]), (63, [0xFFFFFFFF, 0])])
def test_iq_feedback_arithmetic_shift_boundaries_keep_i_then_q_order(shift, expected) -> None:
    readout, control = run({
        "readout": f"fb_acq_iq_id 5,4\nfb_acq_iq_shift {shift},4\nacquire 0,0,8\nwait 20\nstop",
        "control": "wait 100\nfb_pop_data 5,R0\nnop\nfb_pop_data 5,R1\nnop\nstop",
    }, inputs={"acquisitions": [{"sequencer": "readout", "acquisition_index": 0, "bin_index": 0, "i": -(2**31), "q": 2**31-1}]},
       routes=[{"source": "readout", "target": "control", "latency_ns": 0}])
    assert status(readout) == status(control) == "completed"
    assert [control.registers[register].value for register in ("R0", "R1")] == expected
    receives = events(control, "feedback_receive")
    assert [event.t0.value for event in receives] == [216, 216]
    assert [write.meta["provenance"]["input_event_id"] for write in control.register_history] == [event.id for event in receives]
