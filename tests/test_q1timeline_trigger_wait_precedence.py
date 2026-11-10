import pytest

from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


def _run(transport, *, arm_at_arrival=False, reverse=False, known_address=3, known_delay=0,
         receiver=None, threshold=1):
    arrival = 340 if transport == "network" else 104
    if receiver is None:
        arm = f"wait {arrival - 100}\n" if arm_at_arrival else ""
        receiver = arm + "wait_trigger 3,4\nstop"
    sources = {"analog": "acquire 0,0,4\nstop", "rx": receiver}
    routes = [{"source": "analog", "target": "rx", "address": 3,
               **({"network": "rack"} if transport == "network" else {"latency_ns": 0})}]
    inputs = {"acquisitions": [{"sequencer": "analog", "acquisition_index": 0, "bin_index": 0, "i": 1, "q": 0}]}
    digital = {}
    if known_address is not None:
        sources["qtm"] = f"wait {arrival - 100 + known_delay}\nacquire_digital 0,0,4\nstop"
        routes.append({"source": "qtm", "target": "rx", "address": known_address, "latency_ns": 0})
        digital["qtm"] = {"forward_trigger_en": True, "forward_trigger_mode": "sampled-high",
                          "forward_trigger_address": known_address}
        inputs["digital_acquisitions"] = [{"sequencer": "qtm", "kind": "acquire_digital", "acquisition_index": 0,
                                           "bin_index": 0, "level": 1}]
    order = reversed(sources) if reverse else sources
    programs = {name: parse_q1asm(sources[name], file=f"{name}.q1asm") for name in order}
    states = simulate_programs(
        programs, sequences={name: {"acquisitions": {"a": {"index": 0, "num_bins": 1}}} for name in programs},
        hardware_parameters={"rx": {"trigger3_count_threshold": threshold}}, simulation={
            "startup_delay_ns": 100, "trigger_routes": routes, "trigger_networks": {"rack": {"grid_epoch_ns": 100}},
            "inputs": inputs, "digital": digital,
        },
    )
    return next(state for state in states if state.sequencer_id == "rx"), arrival


@pytest.mark.parametrize("transport", ["fixed", "network"])
@pytest.mark.parametrize("arm_at_arrival", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_late_definite_trigger_wins_an_early_possible_arrival(transport, arm_at_arrival, reverse):
    receiver, arrival = _run(transport, arm_at_arrival=arm_at_arrival, reverse=reverse)
    assert receiver.metadata["execution"]["status"] == "completed" and receiver.rt_time_ns.value == arrival + 4
    receive, = [event for event in receiver.events if event.kind == "trigger_receive"]
    assert receive.t0.value == arrival and receive.meta["source_sequencer_id"] == "qtm"
    wait, = [event for event in receiver.events if event.kind == "trigger_wait"]
    assert (wait.t0.value, wait.t1.value) == (arrival if arm_at_arrival else 100, arrival)
    assert wait.meta["input_event_ids"] == [receive.id]


@pytest.mark.parametrize("transport", ["fixed", "network"])
@pytest.mark.parametrize("known_address,known_delay", [(None, 0), (4, 0), (3, 4)])
def test_unmatched_possible_arrival_stops_the_wait_on_its_own_tick(transport, known_address, known_delay):
    receiver, arrival = _run(transport, known_address=known_address, known_delay=known_delay)
    assert receiver.metadata["execution"]["status"] == "blocked"
    diagnostic, = receiver.diagnostics
    assert diagnostic.category == "acquisition_trigger_outcome_unknown"
    wait, = [event for event in receiver.events if event.kind == "trigger_wait"]
    assert wait.t0.value == 100 and wait.t1.value == arrival and wait.meta["pending"]
    assert [event.meta["address"] for event in receiver.events if event.kind == "trigger_receive"] == (
        [known_address] if known_address == 4 else []
    )


@pytest.mark.parametrize("threshold", [1, 2])
def test_same_tick_conditions_still_see_early_counts_before_late_definite_arrival(threshold):
    receiver, arrival = _run("fixed", receiver="set_latch_en 1,4\nset_cond 1,4,0,8\nupd_param 4\nupd_param 4\nstop",
                             threshold=threshold)
    first, second = [packet for packet in receiver.rt_packets if packet.op == "upd_param"]
    assert receiver.metadata["execution"]["status"] == "blocked" and second.rt_t0 is None
    assert any(item.category == "acquisition_trigger_outcome_unknown" for item in receiver.diagnostics)
    if threshold == 1:
        assert first.rt_t0 is None and receiver.rt_time_ns.value == arrival
    else:
        assert first.rt_t0.value == arrival and first.meta["conditional_skipped"]
        assert receiver.rt_time_ns.value == arrival + 8
