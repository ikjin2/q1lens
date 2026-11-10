from html import escape

import pytest

from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.interpreter import prepare_def_program
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.render.html import render_html
from q1timeline.simulation.config import validate_simulation
from q1timeline.simulation.runtime import simulate_programs


_TICK = 128
_ANALOG = "acquire 0,0,4\nstop"
_EDGE = "acquire_timetags 0,0,1,0,400\nacquire_timetags 0,0,0,0,4\nstop"
_WAIT_COND = "set_latch_en 1,4\nwait_trigger 3,4\nset_cond 1,4,0,8\nupd_param 4\nstop"
_FORWARD = {"forward_trigger_en": True, "forward_trigger_mode": "rising", "forward_trigger_address": 3}


def _run(programs, routes, *, epoch=None, digital=None, inputs=None, prepared=False, hardware=None, other_networks=None):
    programs = {name: parse_q1asm(code, file=f"{name}.q1asm") for name, code in programs.items()}
    memory = {name: {"acquisitions": {"a": {"index": 0, "num_bins": 1}}} for name in programs}
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    states = simulate_programs(programs, sequences=memory, prepared_programs=bindings, hardware_parameters=hardware,
                               simulation={"startup_delay_ns": 100, "trigger_routes": routes,
                                           "trigger_networks": {"rack": {"grid_epoch_ns": 100} if epoch is None else epoch,
                                                                **(other_networks or {})},
                                           "digital": digital or {}, "inputs": inputs or {}})
    return {state.sequencer_id: state for state in states}


def _network(source, target, *, extra=0):
    return {"source": source, "target": target, "address": 3, "network": "rack", "extra_latency_ns": extra}


def _fixed(source, target, latency):
    return {"source": source, "target": target, "address": 3, "latency_ns": latency}


def _analog_input(name):
    return {"sequencer": name, "acquisition_index": 0, "bin_index": 0, "thresholded": 1}


def _pulses(*times):
    return {"initial_level": 0, "transitions": [{"time_ticks": int(time * _TICK) + delta, "level": level}
           for time in times for delta, level in ((0, 1), (1, 0))]}


def _events(state, kind):
    return [event for event in state.events if event.kind == kind]


def _transmissions(state):
    return state.metadata["simulation"]["trigger_network_transmissions"]


@pytest.mark.parametrize("generator", ["analog", "qtm"])
@pytest.mark.parametrize("prepared", [False, True])
def test_request_launch_fanout_wait_and_alignment(generator, prepared):
    if generator == "analog":
        source, hardware, digital = _ANALOG, {"tx": {"integration_length_acq": 1}}, {}
        inputs = {"acquisitions": [_analog_input("tx")]}
    else:
        source, hardware, digital = _EDGE, {}, {"tx": _FORWARD}
        inputs = {"digital": {"tx": _pulses(101)}}
    inputs["triggers"] = [{"target": "rx", "address": 7, "time_ns": 150}]
    states = _run({"tx": source, "rx": _WAIT_COND, "extra": "wait_trigger 3,4\nstop"},
                  [_network("tx", "rx"), _network("tx", "extra", extra=12)], hardware=hardware,
                  digital=digital, inputs=inputs, prepared=prepared)
    assert all(state.metadata["execution"]["status"] == "completed" for state in states.values())
    transmission, = _transmissions(states["tx"])
    assert transmission["status"] == "launched"
    assert (transmission["request_ticks"], transmission["launch_ticks"]) == (101 * _TICK, 128 * _TICK)
    assert [route["arrival_ticks"] for route in transmission["routes"]] == [340 * _TICK, 352 * _TICK]
    assert states["rx"].rt_time_ns.value == 348 and states["extra"].rt_time_ns.value == 356
    if not prepared:
        direct, routed = _events(states["rx"], "trigger_receive")
        assert direct.t0.value == 150 and direct.meta["address"] == 7
        assert routed.meta["source_event_id"] == transmission["launch_event_id"]
        assert routed.meta["request_event_id"] == transmission["request_event_id"]
        source_events = {event.id: event for event in states["tx"].events}
        evidence, = routed.meta["source_event_ids"]
        assert source_events[evidence].kind == ("acquisition_result" if generator == "analog" else "digital_transition")
        for mode, expected in (("first_rt_dequeue", (28, 240)), ("manual:7", (135, 347))):
            align_timelines(list(states.values()), mode=mode)
            ir = timeline_ir_from_states(list(states.values()))
            flow, = [flow for flow in ir["feedback_flows"] if flow["to_event_id"] == routed.id]
            by_id = {event["id"]: event for event in ir["events"]}
            launch, receive = by_id[flow["from_event_id"]], by_id[flow["to_event_id"]]
            assert (launch["meta"]["aligned_t0"], receive["meta"]["aligned_t0"]) == expected


def test_on_grid_requests_exactly_252_ns_apart_both_arrive():
    states = _run({"tx": _EDGE, "rx": "wait 600\nstop"}, [_network("tx", "rx")],
                  digital={"tx": _FORWARD}, inputs={"digital": {"tx": _pulses(128, 380)}})
    assert not states["tx"].diagnostics
    assert [event.t0.value for event in _events(states["rx"], "trigger_receive")] == [340, 592]
    assert [(item["request_ticks"], item["launch_ticks"]) for item in _transmissions(states["tx"])] == [
        (128 * _TICK, 128 * _TICK), (380 * _TICK, 380 * _TICK)]


@pytest.mark.parametrize("offset,launch", [(-1, 128), (1, 156)])
def test_fractional_qtm_request_uses_first_grid_slot_at_or_after_exact_tick(offset, launch):
    states = _run({"tx": _EDGE, "rx": "wait 600\nstop"}, [_network("tx", "rx")],
                  digital={"tx": _FORWARD}, inputs={"digital": {"tx": _pulses(128 + offset / _TICK)}})
    item, = _transmissions(states["tx"])
    assert item["request_ticks"] == 128 * _TICK + offset
    assert item["launch_ticks"] == launch * _TICK
    assert _events(states["rx"], "trigger_receive")[0].t0.value == launch + 212


def test_analog_and_qtm_requests_on_same_grid_slot_do_not_claim_either_arrival():
    states = _run({"analog": _ANALOG, "qtm": _EDGE, "rx": _WAIT_COND, "fixed": "wait_trigger 3,4\nstop"},
                  [_network("analog", "rx"), _network("qtm", "rx"), _fixed("analog", "fixed", 30)],
                  hardware={"analog": {"integration_length_acq": 1}}, digital={"qtm": _FORWARD},
                  inputs={"acquisitions": [_analog_input("analog")], "digital": {"qtm": _pulses(128)}})
    analog, = _transmissions(states["analog"])
    qtm, = _transmissions(states["qtm"])
    for item in (analog, qtm):
        assert item["status"] == "conflicted" and item["launch_ticks"] == 128 * _TICK
        assert "launch_event_id" not in item
        assert item["routes"][0]["status"] == "unknown_conflict" and "arrival_ticks" not in item["routes"][0]
    for name in ("analog", "qtm"):
        assert states[name].metadata["execution"]["status"] == "completed"
        conflict, = [diagnostic for diagnostic in states[name].diagnostics if diagnostic.category == "trigger_network_conflict"]
        assert conflict.details["launch_ticks"] == [128 * _TICK] * 2
        assert {analog["source_event_id"], qtm["source_event_id"]} <= set(conflict.related_events)
        assert len(states[name].metadata["simulation"]["acquisitions"]) == 1
    assert not _events(states["rx"], "trigger_receive") and states["rx"].metadata["execution"]["status"] == "blocked"
    assert any(d.category == "trigger_network_outcome_unknown" for d in states["rx"].diagnostics)
    assert [event.t0.value for event in _events(states["fixed"], "trigger_receive")] == [131]


@pytest.mark.parametrize("alignment,offset", [("first_rt_dequeue", -100), ("manual:-4", -4), ("none", 0)])
@pytest.mark.parametrize("possible", [False, True])
def test_network_conflict_display_uses_shared_aligned_launch_slots(alignment, offset, possible):
    states = _run({"a": _ANALOG, "b": "wait 28\n" + _ANALOG, "rx": "wait_trigger 3,4\nstop"},
                  [_network("a", "rx"), _network("b", "rx")],
                  inputs={"acquisitions": [_analog_input("a"), _iq_only("b") if possible else _analog_input("b")]})
    align_timelines(list(states.values()), mode=alignment)
    ir = timeline_ir_from_states(list(states.values()), project={"alignment_mode": alignment})
    qualifier = "potentially " if possible else ""
    category = "trigger_network_possible_conflict" if possible else "trigger_network_conflict"
    diagnostics = [item for item in ir["diagnostics"] if item["category"] == category]
    assert len(diagnostics) == 2
    html = render_html(ir)
    for diagnostic in diagnostics:
        ticks = diagnostic["details"]["launch_ticks"]
        first, second = [tick // _TICK for tick in ticks]
        prefix = f"Trigger network 'rack' has {qualifier}conflicting launch slots"
        assert diagnostic["message"] == f"{prefix} at {first} ns and {second} ns."
        assert diagnostic["details"]["minimum_spacing_ns"] == 252
        assert escape(f"{prefix} at {first + offset} ns and {second + offset} ns.") in html


def test_later_conflict_preserves_already_observed_fanout_but_invalidates_pending_path():
    states = _run({"tx": _EDGE, "early": "set_latch_en 1,4\nwait 500\nset_cond 1,4,0,8\nupd_param 4\nstop",
                   "late": "wait_trigger 3,4\nstop"},
                  [_network("tx", "early"), _network("tx", "late", extra=48)],
                  digital={"tx": _FORWARD}, inputs={"digital": {"tx": _pulses(128, 352)}})
    first, second = _transmissions(states["tx"])
    assert [route["status"] for route in first["routes"]] == ["delivered", "unknown_conflict"]
    assert [route["status"] for route in second["routes"]] == ["unknown_conflict", "unknown_conflict"]
    assert [event.t0.value for event in _events(states["early"], "trigger_receive")] == [340]
    assert len(_events(states["early"], "upd_param")) == 1
    assert states["late"].metadata["execution"]["status"] == "blocked"


def test_shared_synq_release_can_anchor_the_grid_explicitly():
    states = _run({"tx": "wait 16\nwait_sync 4\n" + _ANALOG, "rx": "wait_sync 4\nwait_trigger 3,4\nstop"},
                  [_network("tx", "rx")], epoch={"grid_epoch": "synq"}, hardware={"tx": {"integration_length_acq": 1}},
                  inputs={"acquisitions": [_analog_input("tx")]})
    item, = _transmissions(states["tx"])
    assert item["grid_epoch_source"] == "synq" and item["grid_epoch_ticks"] == 116 * _TICK
    assert (item["request_ticks"], item["launch_ticks"], item["routes"][0]["arrival_ticks"]) == tuple(
        time * _TICK for time in (121, 144, 356))


@pytest.mark.parametrize("epoch", [{}, {"grid_epoch": "synq"}])
def test_unknown_epoch_is_not_inferred_and_affected_condition_stays_unknown(epoch):
    receiver = "set_latch_en 1,4\nwait 215\nset_cond 1,4,0,8\nupd_param 4\nstop"
    states = _run({"tx": _ANALOG, "rx": receiver, "free": "wait 300\nstop"}, [_network("tx", "rx")],
                  epoch=epoch, hardware={"tx": {"integration_length_acq": 1}}, inputs={"acquisitions": [_analog_input("tx")]})
    item, = _transmissions(states["tx"])
    assert item["status"] == "unknown_epoch" and "launch_ticks" not in item
    assert item["routes"][0]["status"] == "unknown_epoch" and "arrival_ticks" not in item["routes"][0]
    assert any(d.category == "trigger_network_unknown_epoch" for d in states["tx"].diagnostics)
    assert any(d.category == "trigger_network_outcome_unknown" for d in states["rx"].diagnostics)
    assert not _events(states["rx"], "trigger_receive") and not _events(states["rx"], "conditional_skip")
    assert states["free"].metadata["execution"]["status"] == "completed"


def _run_bounded_condition(prefix, *, threshold, invert=False, known_address=None, second_request=False,
                           mask=1, operator=0):
    sources = ["tx", "other_tx"] if second_request else ["tx"]
    receiver = f"{prefix}\nset_cond 1,{mask},{operator},8\nupd_param 4\nstop"
    inputs = {"acquisitions": [_analog_input(name) for name in sources]}
    if known_address is not None:
        inputs["triggers"] = [{"target": "rx", "address": known_address, "time_ns": 200}]
    return _run({**dict.fromkeys(sources, _ANALOG), "rx": receiver},
                [{**_network(name, "rx"), "address": 1} for name in sources], epoch={"grid_epoch": "synq"},
                hardware={**{name: {"integration_length_acq": 4} for name in sources},
                          "rx": {"trigger1_count_threshold": threshold, "trigger1_threshold_invert": invert}},
                inputs=inputs)


def _assert_bounded_condition(rx, outcome):
    assert rx.metadata["execution"]["status"] == ("blocked" if outcome == "unknown" else "completed")
    assert bool(_events(rx, "conditional_skip")) == (outcome == "skip")
    assert bool(_events(rx, "upd_param")) == (outcome == "execute")
    assert any(d.category == "trigger_network_outcome_unknown" for d in rx.diagnostics) == (outcome == "unknown")


@pytest.mark.parametrize("threshold,invert,known_address,second_request,mask,operator,outcome", [
    (2, False, None, False, 1, 0, "skip"),
    (1, False, None, False, 1, 0, "unknown"),
    (2, False, 1, False, 1, 0, "unknown"),
    (1, False, 1, False, 1, 0, "execute"),
    (2, False, None, True, 1, 0, "unknown"),
    (3, False, None, True, 1, 0, "skip"),
    (2, True, None, False, 1, 0, "execute"),
    (1, True, None, False, 1, 0, "unknown"),
    (1, False, None, False, 2, 0, "skip"),
    (1, False, 2, False, 3, 0, "execute"),
    (1, False, None, False, 3, 2, "skip"),
    (2, False, 2, False, 3, 4, "execute"),
])
def test_uncertain_count_bound_controls_condition(threshold, invert, known_address, second_request, mask, operator,
                                                  outcome):
    states = _run_bounded_condition("set_latch_en 1,4\nwait 300", threshold=threshold, invert=invert,
                                    known_address=known_address, second_request=second_request, mask=mask,
                                    operator=operator)
    _assert_bounded_condition(states["rx"], outcome)
    assert [event.meta["address"] for event in _events(states["rx"], "trigger_receive")] == (
        [] if known_address is None else [known_address]
    )
    assert any(d.category == "trigger_network_unknown_epoch" for d in states["tx"].diagnostics)


@pytest.mark.parametrize("prefix,threshold,outcome", [
    ("wait 220\nset_latch_en 1,4\nset_latch_en 0,4\nset_latch_en 1,4\nwait 40", 2, "skip"),
    ("wait 220\nset_latch_en 1,4\nset_latch_en 0,4\nset_latch_en 1,4\nwait 40", 1, "unknown"),
    ("set_latch_en 1,4\nwait 220\nlatch_rst 4\nwait 40", 1, "unknown"),
    ("set_latch_en 1,4\nwait 220\nlatch_rst 4\nwait 40", 2, "skip"),
    ("set_latch_en 1,4\nwait 240\nlatch_rst 4\nwait 40", 1, "skip"),
    ("set_latch_en 1,4\nwait 216\nset_latch_en 0,4\nlatch_rst 4\nset_latch_en 1,4\nwait 40", 1, "unknown"),
    ("set_latch_en 1,4\nwait 216\nset_latch_en 0,4\nlatch_rst 4\nwait 20\nset_latch_en 1,4\nwait 40", 1, "skip"),
])
def test_uncertain_count_windows_respect_latch_changes(prefix, threshold, outcome):
    # The request at 104 ns can arrive from 316 ns up to just before 344 ns.
    states = _run_bounded_condition(prefix, threshold=threshold)
    item, = _transmissions(states["tx"])
    route, = item["routes"]
    assert route["possible_arrival_ticks"] == 316 * _TICK
    _assert_bounded_condition(states["rx"], outcome)


@pytest.mark.parametrize("route", [_network("tx", "rx"), _fixed("tx", "rx", 212)])
@pytest.mark.parametrize("threshold,outcome", [(1, "execute"), (2, "skip")])
def test_known_trigger_delivery_contributes_one_definite_count(route, threshold, outcome):
    receiver = "set_latch_en 1,4\nwait 300\nset_cond 1,4,0,8\nupd_param 4\nstop"
    states = _run({"tx": _ANALOG, "rx": receiver}, [route],
                  hardware={"tx": {"integration_length_acq": 4}, "rx": {"trigger3_count_threshold": threshold}},
                  inputs={"acquisitions": [_analog_input("tx")]})
    received, = _events(states["rx"], "trigger_receive")
    assert received.t0.value == (340 if "network" in route else 316)
    _assert_bounded_condition(states["rx"], outcome)


def _iq_only(name):
    return {"sequencer": name, "acquisition_index": 0, "bin_index": 0, "i": 17, "q": -9}


@pytest.mark.parametrize("route,arrival", [(_fixed("tx", "rx", 8), 112), (_network("tx", "rx"), 340)])
@pytest.mark.parametrize("threshold,bit,outcome", [(1, None, "unknown"), (2, None, "skip"),
                                                   (1, 0, "skip"), (1, 1, "execute")])
def test_iq_only_threshold_routes_preserve_acquisition_and_bound_condition(route, arrival, threshold, bit, outcome):
    input_row = _iq_only("tx")
    if bit is not None:
        input_row["thresholded"] = bit
    receiver = f"set_latch_en 1,4\nwait {arrival - 100}\nset_cond 1,1,0,8\nupd_param 4\nstop"
    states = _run({"tx": _ANALOG, "rx": receiver, "free": "wait 4\nstop"}, [{**route, "address": 1}],
                  inputs={"acquisitions": [input_row]}, hardware={"rx": {"trigger1_count_threshold": threshold}})
    tx, rx, free = (states[name] for name in ("tx", "rx", "free"))
    result, = tx.metadata["simulation"]["acquisitions"]
    assert (result["i"], result["q"]) == (17, -9)
    assert result.get("thresholded") == bit
    assert (result.get("trigger_threshold_status") == "unknown") == (bit is None)
    assert any(d.category == "acquisition_trigger_threshold_unknown" for d in tx.diagnostics) == (bit is None)
    assert tx.metadata["execution"]["status"] == free.metadata["execution"]["status"] == "completed"
    assert rx.metadata["execution"]["status"] == ("blocked" if outcome == "unknown" else "completed")
    assert bool(_events(rx, "conditional_skip")) == (outcome == "skip")
    assert bool(_events(rx, "upd_param")) == (outcome == "execute")
    assert [event.t0.value for event in _events(rx, "trigger_receive")] == ([arrival] if bit == 1 else [])


@pytest.mark.parametrize("route,arrival", [(_fixed("tx", "rx", 8), 112), (_network("tx", "rx"), 340)])
@pytest.mark.parametrize("threshold,outcome", [(1, "unknown"), (2, "skip")])
@pytest.mark.parametrize("prepared", [False, True])
def test_missing_acquisition_still_bounds_routed_threshold(route, arrival, threshold, outcome, prepared):
    receiver = f"set_latch_en 1,4\nwait {arrival - 100}\nset_cond 1,4,0,8\nupd_param 4\nstop"
    states = _run({"tx": _ANALOG, "rx": receiver, "free": "wait 4\nstop"}, [route], prepared=prepared,
                  hardware={"rx": {"trigger3_count_threshold": threshold}})
    tx, rx, free = (states[name] for name in ("tx", "rx", "free"))
    assert tx.metadata["execution"]["status"] == "blocked"
    assert "requires a scenario result" in tx.metadata["execution"]["reason"]
    assert not tx.metadata["simulation"]["acquisitions"]
    missing, = _events(tx, "acquisition_unavailable")
    assert missing.t0.value == 104 and missing.source.line == 1 and missing.lane == "debug.acquisition"
    assert not _events(tx, "acquisition_result")
    assert missing.meta["status"] == "unavailable" and missing.meta["trigger_threshold_status"] == "unknown"
    assert not {"i", "q", "thresholded"}.intersection(missing.meta)
    assert missing.id in tx.diagnostics[-1].related_events
    assert free.metadata["execution"]["status"] == "completed"
    assert rx.metadata["execution"]["status"] == ("blocked" if outcome == "unknown" else "completed")
    assert any(d.category == "acquisition_trigger_outcome_unknown" for d in rx.diagnostics) == (outcome == "unknown")
    update, = [packet for packet in rx.rt_packets if packet.op == "upd_param"]
    assert bool(update.meta.get("conditional_skipped")) == (outcome == "skip")
    assert not _events(rx, "trigger_receive")
    if "network" in route:
        request, = _transmissions(tx)
        assert request["source_event_id"] == missing.id and request["status"] == "optional"
        assert request["routes"][0]["possible_arrival_ticks"] == arrival * _TICK


@pytest.mark.parametrize("receiver_first", [False, True])
def test_missing_interrupted_acquisition_routes_after_same_tick_rt(receiver_first):
    receiver = "set_latch_en 1,20\nset_cond 1,4,0,8\nupd_param 4\nupd_param 4\nstop"
    transmitter = "acquire 0,0,20\nacquire 0,0,4\nstop"
    programs = {"rx": receiver, "tx": transmitter} if receiver_first else {"tx": transmitter, "rx": receiver}
    states = _run(programs, [_fixed("tx", "rx", 0)], hardware={"tx": {"integration_length_acq": 64}})
    tx, rx = states["tx"], states["rx"]
    assert tx.metadata["execution"]["status"] == "blocked"
    missing, = _events(tx, "acquisition_unavailable")
    assert missing.t0.value == 120 and missing.meta["interrupted"] is True
    assert missing.meta["duration_ns"] == 20 and missing.meta["integration_length_ns"] == 64
    skipped, = _events(rx, "conditional_skip")
    assert skipped.t0.value == 120
    assert rx.metadata["execution"]["status"] == "blocked" and rx.rt_time_ns.value == 128
    assert any(d.category == "acquisition_trigger_outcome_unknown" for d in rx.diagnostics)
    assert not _events(rx, "trigger_receive")


def test_missing_acquisition_without_routes_keeps_existing_block_boundary():
    tx = _run({"tx": _ANALOG}, [])["tx"]
    assert tx.metadata["execution"]["status"] == "blocked"
    assert "requires a scenario result" in tx.metadata["execution"]["reason"]
    assert not tx.metadata["simulation"]["acquisitions"]
    unavailable, = [event for event in tx.events if event.kind == "acquisition_unavailable"]
    assert unavailable.t0.value == tx.metadata["execution"]["terminal"]["local_time_ns"] == 104
    assert tx.diagnostics[-1].related_events == [unavailable.id]
    assert not any(event.kind in {"acquisition_result", "trigger_network_request"} for event in tx.events)


@pytest.mark.parametrize("route,arrival", [(_fixed("tx", "rx", 8), 112), (_network("tx", "rx"), 340)])
@pytest.mark.parametrize("arm_offset,blocked", [(-4, True), (0, True), (4, False)])
def test_wait_sees_only_possible_threshold_arrivals_at_or_after_arming(route, arrival, arm_offset, blocked):
    arm = arrival + arm_offset
    receiver = f"wait {arm - 100}\nwait_trigger 3,4\nstop"
    definite = {"target": "rx", "address": 3, "time_ns": arrival + 12}
    states = _run({"tx": _ANALOG, "rx": receiver}, [route],
                  inputs={"acquisitions": [_iq_only("tx")], "triggers": [definite]})
    rx = states["rx"]
    assert rx.metadata["execution"]["status"] == ("blocked" if blocked else "completed")
    assert [event.t0.value for event in _events(rx, "trigger_receive")] == ([] if blocked else [arrival + 12])


@pytest.mark.parametrize("threshold,outcome", [(1, "execute"), (2, "skip")])
def test_opposite_threshold_cases_contribute_at_most_one_count(threshold, outcome):
    receiver = "set_latch_en 1,4\nwait 20\nset_cond 1,4,0,8\nupd_param 4\nstop"
    states = _run({"tx": _ANALOG, "rx": receiver}, [
        {**_fixed("tx", "rx", 8), "thresholded": 0}, {**_fixed("tx", "rx", 12), "thresholded": 1},
    ], inputs={"acquisitions": [_iq_only("tx")]}, hardware={"rx": {"trigger3_count_threshold": threshold}})
    rx = states["rx"]
    _assert_bounded_condition(rx, outcome)
    assert not _events(rx, "trigger_receive")


def test_complementary_fixed_routes_release_wait_once_at_the_shared_edge():
    route = _fixed("tx", "rx", 8)
    states = _run({"tx": _ANALOG, "rx": _WAIT_COND}, [{**route, "thresholded": 0}, {**route, "thresholded": 1}],
                  inputs={"acquisitions": [_iq_only("tx")]})
    rx = states["rx"]
    assert rx.metadata["execution"]["status"] == "completed"
    receive, = _events(rx, "trigger_receive")
    assert receive.t0.value == 112
    assert _events(rx, "upd_param")


def test_known_arrival_wins_the_same_tick_as_an_optional_arrival():
    states = _run({"tx": _ANALOG, "rx": "wait_trigger 3,4\nstop"}, [_fixed("tx", "rx", 8)], inputs={
        "acquisitions": [_iq_only("tx")], "triggers": [{"target": "rx", "address": 3, "time_ns": 112}],
    })
    assert states["rx"].metadata["execution"]["status"] == "completed"
    receive, = _events(states["rx"], "trigger_receive")
    assert receive.t0.value == 112


def test_latch_reset_removes_earlier_threshold_case_evidence():
    receiver = "set_latch_en 1,4\nwait 12\nlatch_rst 4\nwait 12\nset_cond 1,4,0,8\nupd_param 4\nstop"
    states = _run({"tx": _ANALOG, "rx": receiver}, [
        {**_fixed("tx", "rx", 8), "thresholded": 0}, {**_fixed("tx", "rx", 20), "thresholded": 1},
    ], inputs={"acquisitions": [_iq_only("tx")]})
    rx = states["rx"]
    assert rx.metadata["execution"]["status"] == "blocked"
    assert any(d.category == "acquisition_trigger_outcome_unknown" for d in rx.diagnostics)


def test_possible_network_conflict_makes_only_the_shared_delivery_uncertain():
    states = _run({"unknown": _ANALOG, "known": _ANALOG, "rx": "wait_trigger 3,4\nstop",
                   "fixed": "wait_trigger 3,4\nstop"},
                  [_network("unknown", "rx"), _network("known", "rx"), _fixed("known", "fixed", 12)],
                  inputs={"acquisitions": [_iq_only("unknown"), _analog_input("known")]})
    for name in ("unknown", "known"):
        state = states[name]
        assert state.metadata["execution"]["status"] == "completed"
        assert not any(d.category == "trigger_network_conflict" for d in state.diagnostics)
        transmission, = _transmissions(state)
        assert transmission["status"] == "possible_conflict"
        assert "arrival_ticks" not in transmission["routes"][0]
        assert transmission["routes"][0]["possible_arrival_ticks"] == 340 * _TICK
    assert states["rx"].metadata["execution"]["status"] == "blocked"
    assert not _events(states["rx"], "trigger_receive")
    assert [event.t0.value for event in _events(states["fixed"], "trigger_receive")] == [116]


@pytest.mark.parametrize("operator,outcome", [(0, "execute"), (2, "skip"), (4, "execute")])
def test_opposite_threshold_cases_remain_correlated_across_network_addresses(operator, outcome):
    receiver = f"set_latch_en 1,4\nwait 300\nset_cond 1,5,{operator},8\nupd_param 4\nstop"
    states = _run({"tx": _ANALOG, "rx": receiver}, [
        {**_network("tx", "rx"), "thresholded": 0, "address": 1},
        {**_network("tx", "rx"), "thresholded": 1, "address": 3},
    ], inputs={"acquisitions": [_iq_only("tx")]})
    records = _transmissions(states["tx"])
    assert [record["status"] for record in records] == ["optional", "optional"]
    assert [record["routes"][0]["status"] for record in records] == ["possible", "possible"]
    assert all(record["launch_ticks"] == 128 * _TICK and "launch_event_id" not in record for record in records)
    assert not _events(states["rx"], "trigger_receive")
    _assert_bounded_condition(states["rx"], outcome)


def test_later_optional_request_preserves_observed_fanout_and_marks_only_pending_delivery():
    states = _run({"known": _ANALOG, "unknown": "wait 248\n" + _ANALOG, "early": "wait 600\nstop",
                   "late": "wait_trigger 3,4\nstop", "other": "wait 600\nstop"}, [
        _network("known", "early"), _network("known", "late", extra=48), _network("unknown", "other"),
    ], inputs={"acquisitions": [_analog_input("known"), _iq_only("unknown")]})
    record, = _transmissions(states["known"])
    assert record["status"] == "possible_conflict" and "launch_event_id" in record
    assert [route["status"] for route in record["routes"]] == ["delivered", "unknown_possible_conflict"]
    assert [event.t0.value for event in _events(states["early"], "trigger_receive")] == [340]
    assert not _events(states["late"], "trigger_receive")
    assert states["late"].metadata["execution"]["status"] == "blocked"
    assert states["early"].metadata["execution"]["status"] == "completed"


@pytest.mark.parametrize("early_threshold", [None, 0, 1], ids=["unknown", "zero", "one"])
@pytest.mark.parametrize("post_sync_wait,network,launch,conflict", [
    (0, "rack", 156, True), (224, "rack", 380, True), (252, "rack", 408, False), (0, "isolated", 156, False),
])
def test_request_before_synq_can_affect_a_later_known_epoch_delivery(early_threshold, post_sync_wait, network, launch, conflict):
    early_route = _network("earlier", "other")
    early_input = _iq_only("earlier")
    if early_threshold is not None:
        early_route["thresholded"] = early_threshold
        early_input["thresholded"] = early_threshold
    delay = f"wait {post_sync_wait}\n" if post_sync_wait else ""
    states = _run({"earlier": "acquire 0,0,4\nwait_sync 4\nstop", "known": "wait 28\nwait_sync 4\n" + delay + _ANALOG,
                   "other": "wait 500\nstop", "rx": "wait_trigger 3,4\nstop"},
                  [early_route, {**_network("known", "rx"), "network": network}], epoch={"grid_epoch": "synq"},
                  other_networks={"isolated": {"grid_epoch": "synq"}}, hardware={name: {"sync_en": False} for name in ("other", "rx")},
                  inputs={"acquisitions": [early_input, _analog_input("known")]})
    earlier, = _transmissions(states["earlier"])
    known, = _transmissions(states["known"])
    assert earlier["status"] == "unknown_epoch" and earlier["routes"][0]["status"] == "unknown_epoch"
    assert "launch_ticks" not in earlier and not _events(states["earlier"], "trigger_network_launch")
    assert any(d.category == "trigger_network_unknown_epoch" for d in states["earlier"].diagnostics)
    assert known["launch_ticks"] == launch * _TICK
    assert known["status"] == ("possible_conflict" if conflict else "launched")
    assert states["rx"].metadata["execution"]["status"] == ("blocked" if conflict else "completed")
    assert [event.t0.value for event in _events(states["rx"], "trigger_receive")] == ([] if conflict else [launch + 212])
    if conflict:
        assert known["conflicting_request_event_id"] == earlier["request_event_id"]
        assert known["conflicting_launch_ticks"] is None
        warning, = [d for d in states["known"].diagnostics if d.category == "trigger_network_possible_conflict"]
        assert warning.severity == "warning" and earlier["request_event_id"] in warning.related_events
        assert known["routes"][0]["status"] == "unknown_possible_conflict"
        if early_threshold is None and post_sync_wait == 0:
            align_timelines(list(states.values()), mode="first_rt_dequeue")
            ir = timeline_ir_from_states(list(states.values()), project={"alignment_mode": "first_rt_dequeue"})
            assert warning.details["launch_ticks"] == [156 * _TICK, None]
            assert warning.message == "Trigger network 'rack' could conflict with a request whose grid epoch is unknown."
            assert "grid epoch is unknown. Known launch slot at 56 ns." in render_html(ir)
    assert not any(d.category == "trigger_network_conflict" for state in states.values() for d in state.diagnostics)


def test_two_known_launches_still_conflict_with_an_earlier_unresolved_request():
    later = "wait 28\nwait_sync 4\n" + _ANALOG
    sources = {"earlier": "acquire 0,0,4\nwait_sync 4\nstop", "a": later, "b": later, "rx": "wait_trigger 3,4\nstop"}
    states = _run(sources, [_network(name, "rx") for name in ("earlier", "a", "b")], epoch={"grid_epoch": "synq"},
                  hardware={"rx": {"sync_en": False}}, inputs={"acquisitions": [_analog_input(name) for name in ("earlier", "a", "b")]})
    earlier, = _transmissions(states["earlier"])
    assert earlier["status"] == "unknown_epoch" and "launch_ticks" not in earlier
    for name in ("a", "b"):
        record, = _transmissions(states[name])
        assert record["status"] == "conflicted" and record["conflicting_launch_ticks"] == 156 * _TICK
        error, = [d for d in states[name].diagnostics if d.category == "trigger_network_conflict"]
        assert error.severity == "error"


def test_optional_arrival_after_rt_stop_does_not_keep_receiver_running():
    tx, rx = simulate_programs({"tx": parse_q1asm(_ANALOG), "rx": parse_q1asm("wait 4\nstop")},
                              sequences={"tx": {"acquisitions": {"a": {"index": 0, "num_bins": 1}}}}, simulation={
        "startup_delay_ns": 100, "max_time_ns": 200, "trigger_networks": {"rack": {"grid_epoch_ns": 100}},
        "trigger_routes": [_network("tx", "rx")], "inputs": {"acquisitions": [_iq_only("tx")]},
    })
    assert rx.metadata["execution"]["status"] == "completed" and rx.rt_time_ns.value == 104
    route, = _transmissions(tx)[0]["routes"]
    assert route["status"] == "receiver_unavailable"


@pytest.mark.parametrize("route,networks,reason", [
    (_network("a", "b"), {}, "declared trigger network"),
    ({**_network("a", "b"), "latency_ns": 2}, {"rack": {}}, "fixed-latency route"),
    ({**_fixed("a", "b", 2), "extra_latency_ns": 1}, {}, "requires a trigger network"),
    (_network("a", "b"), {"rack": {"grid_epoch_ns": 100, "grid_epoch": "synq"}}, "one grid epoch"),
])
def test_transport_config_rejects_ambiguous_routes_and_grid_sources(route, networks, reason):
    with pytest.raises(ValueError, match=reason):
        validate_simulation({"trigger_routes": [route], "trigger_networks": networks}, ["a", "b"])
