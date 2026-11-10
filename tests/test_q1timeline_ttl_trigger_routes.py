import pytest

from q1timeline.analysis.interpreter import prepare_def_program
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.config import validate_simulation
from q1timeline.simulation.runtime import simulate_programs


_GATE = "acquire_ttl 0,1,1,40\nacquire_ttl 0,7,0,4\nstop"
_WAIT = "wait_trigger 3,4\nstop"
_TICK = 128
_QTM_FORWARD = {"forward_trigger_en": True, "forward_trigger_mode": "rising", "forward_trigger_address": 3}


def _run(source, *, receiver=_WAIT, routes, inputs=None, prepared=False, hardware=None, receiver_first=False, **simulation):
    sources = (("ttl", source), ("rx", receiver))
    programs = {name: parse_q1asm(code, file=f"{name}.q1asm") for name, code in (reversed(sources) if receiver_first else sources)}
    memory = {"ttl": {"acquisitions": {"counts": {"index": 0, "num_bins": 8}}}}
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    states = simulate_programs(programs, sequences=memory, prepared_programs=bindings, hardware_parameters=hardware,
                               simulation={"startup_delay_ns": 100, "inputs": inputs or {}, "trigger_routes": routes, **simulation})
    return states[::-1] if receiver_first else states


def _route(**fields):
    return {"source": "ttl", "target": "rx", "address": 3, "source_kind": "ttl_edge", **fields}


def _input(*transitions):
    return {"digital": {"ttl": {"initial_level": 0, "transitions": [
        {"time_ticks": ticks, "level": level} for ticks, level in transitions
    ]}}}


def _events(state, kind):
    return [event for event in state.events if event.kind == kind]


def _results(state):
    return state.metadata["simulation"]["acquisitions"]


def _counts(*counts):
    return {"digital_acquisitions": [{"sequencer": "ttl", "kind": "acquire_ttl", "acquisition_index": 0,
                                      "bin_index": 1, "count": count} for count in counts]}


def _condition_outcome(state):
    packet, = [packet for packet in state.rt_packets if packet.op == "upd_param"]
    return "unknown" if packet.rt_t0 is None else "skip" if packet.meta.get("conditional_skipped") else "execute"


_CONDITION = "set_cond 1,4,0,8\nupd_param 4\nstop"


@pytest.mark.parametrize("count,threshold,outcome", [(0, 1, "skip"), (1, 2, "skip"), (1, 1, "unknown")])
@pytest.mark.parametrize("prepared", [False, True])
def test_supplied_count_bounds_routes_during_gate(count, threshold, outcome, prepared):
    tx, rx = _run(_GATE, receiver="set_latch_en 1,20\n" + _CONDITION, routes=[_route(latency_ns=5)],
                  inputs=_counts(count), hardware={"rx": {"trigger3_count_threshold": threshold}}, prepared=prepared)
    result, = _results(tx)
    assert (result["bin_index"], result["count"], result["source"]["input_index"]) == (1, count, 0)
    assert _condition_outcome(rx) == outcome
    assert not _events(rx, "trigger_receive")
    windows = _events(tx, "trigger_edge_window")
    assert [event.meta["count_upper_bound"] for event in windows] == ([count] if count else [])
    if outcome == "unknown":
        diagnostic, = rx.diagnostics
        assert diagnostic.category == "digital_edge_trigger_outcome_unknown" and diagnostic.related_events == [windows[0].id]


@pytest.mark.parametrize("prefix,threshold,outcome", [
    ("set_latch_en 1,45", 1, "execute"),
    ("wait 10\nset_latch_en 1,35", 1, "unknown"),
    ("set_latch_en 1,10\nset_latch_en 0,5\nset_latch_en 1,30", 1, "unknown"),
    ("set_latch_en 1,10\nset_latch_en 0,5\nset_latch_en 1,30", 2, "skip"),
    ("set_latch_en 1,4\nlatch_rst 4\nwait 37", 1, "execute"),
    ("set_latch_en 1,20\nlatch_rst 4\nwait 21", 1, "unknown"),
    ("set_latch_en 1,46\nlatch_rst 4", 1, "skip"),
    ("set_latch_en 1,45\nset_latch_en 0,4", 1, "execute"),
])
def test_fixed_count_becomes_known_only_after_complete_latch_coverage(prefix, threshold, outcome):
    _, rx = _run(_GATE, receiver=prefix + "\n" + _CONDITION, routes=[_route(latency_ns=5)], inputs=_counts(1),
                 hardware={"rx": {"trigger3_count_threshold": threshold}})
    assert _condition_outcome(rx) == outcome
    assert not _events(rx, "trigger_receive")


def test_positive_supplied_count_cannot_locate_an_arrival_for_wait():
    _, rx = _run(_GATE, routes=[_route(latency_ns=5)], inputs=_counts(1))
    wait, = _events(rx, "trigger_wait")
    assert rx.metadata["execution"]["status"] == "blocked" and wait.t1.value == 105
    assert not _events(rx, "trigger_receive")


def test_gate_stopping_without_close_supplies_no_lower_count():
    tx, rx = _run("acquire_ttl 0,1,1,40\nstop", receiver="set_latch_en 1,60\n" + _CONDITION,
                  routes=[_route(latency_ns=5)], inputs=_counts(1))
    assert _results(tx)[0]["status"] == "incomplete"
    assert _condition_outcome(rx) == "unknown"


@pytest.mark.parametrize("first,threshold,outcome", [(0, 1, "execute"), (1, 2, "execute"), (1, 3, "skip")])
def test_supplied_counts_follow_gate_consumption_order(first, threshold, outcome):
    tx, rx = _run(_GATE.removesuffix("stop") + _GATE, receiver="set_latch_en 1,90\n" + _CONDITION,
                  routes=[_route(latency_ns=5)], inputs=_counts(first, 1), hardware={"rx": {"trigger3_count_threshold": threshold}})
    assert [(result["count"], result["source"]["input_index"]) for result in _results(tx)] == [(first, 0), (1, 1)]
    windows = _events(tx, "trigger_edge_window")
    assert [(event.t0.value, event.meta["count_upper_bound"]) for event in windows] == (
        [(100, 1), (144, 1)] if first else [(144, 1)]
    )
    assert _condition_outcome(rx) == outcome


@pytest.mark.parametrize("threshold,outcome", [(1, "execute"), (2, "unknown"), (3, "skip")])
def test_transport_paths_have_separate_upper_counts_but_only_fixed_guarantees_delivery(threshold, outcome):
    _, rx = _run(_GATE, receiver="set_latch_en 1,300\n" + _CONDITION,
                 routes=[_route(latency_ns=5), _route(network="rack")], inputs=_counts(1),
                 hardware={"rx": {"trigger3_count_threshold": threshold}}, trigger_networks={"rack": {"grid_epoch_ns": 100}})
    assert _condition_outcome(rx) == outcome
    assert not _events(rx, "trigger_receive")


def test_explicit_trace_takes_precedence_over_supplied_count_for_routing():
    tx, rx = _run(_GATE, routes=[_route(latency_ns=5)], inputs={**_counts(0), **_input((110 * _TICK, 1))})
    assert _results(tx)[0]["count"] == 0
    assert not _events(tx, "trigger_edge_window")
    receive, = _events(rx, "trigger_receive")
    assert receive.t0.value == 115 and rx.metadata["execution"]["status"] == "completed"


@pytest.mark.parametrize("receiver_first", [False, True])
def test_zero_delay_count_survives_latch_disable_at_exclusive_close(receiver_first):
    receiver = "set_latch_en 1,40\nset_latch_en 0,4\n" + _CONDITION
    _, rx = _run(_GATE, receiver=receiver, routes=[_route(latency_ns=0)], inputs=_counts(1), receiver_first=receiver_first)
    assert _condition_outcome(rx) == "execute" and not _events(rx, "trigger_receive")


def test_zero_count_cannot_interfere_with_a_shared_network_delivery():
    programs = {name: parse_q1asm(source, file=name) for name, source in {
        "ttl": _GATE, "known": "acquire 0,0,4\nstop", "rx": _WAIT,
    }.items()}
    tx, known, rx = simulate_programs(
        programs, sequences={"ttl": {"acquisitions": {"counts": {"index": 0, "num_bins": 8}}}}, simulation={
            "startup_delay_ns": 100, "trigger_networks": {"rack": {"grid_epoch_ns": 100}},
            "trigger_routes": [_route(network="rack"), {"source": "known", "target": "rx", "address": 3, "network": "rack"}],
            "inputs": {**_counts(0), "acquisitions": [{"sequencer": "known", "acquisition_index": 0, "bin_index": 0, "thresholded": 1}]},
        },
    )
    assert not _events(tx, "trigger_edge_window")
    transmission, = known.metadata["simulation"]["trigger_network_transmissions"]
    assert transmission["status"] == "launched" and not known.diagnostics
    receive, = _events(rx, "trigger_receive")
    assert receive.t0.value == 340 and rx.metadata["execution"]["status"] == "completed"


def test_qtm_closing_bin_supplied_count_is_not_used_for_routed_edge_bounds():
    source = "acquire_timetags 0,1,1,0,40\nacquire_timetags 0,7,0,0,4\nstop"
    tx, rx = _run(source, receiver="set_latch_en 1,20\n" + _CONDITION, digital={"ttl": _QTM_FORWARD},
                  routes=[{"source": "ttl", "target": "rx", "address": 3, "latency_ns": 5}],
                  inputs={"digital_acquisitions": [{"sequencer": "ttl", "kind": "acquire_timetags", "acquisition_index": 0,
                                                    "bin_index": 7, "count": 0}]})
    assert _results(tx)[0]["count"] == 0
    window, = _events(tx, "trigger_edge_window")
    assert "count_upper_bound" not in window.meta and _condition_outcome(rx) == "unknown"


@pytest.mark.parametrize("prepared", [False, True])
def test_ttl_edge_releases_wait_before_close_and_keeps_bin_count_and_provenance(prepared):
    tx, rx = _run(_GATE, routes=[_route(latency_ns=5)], prepared=prepared,
                  inputs=_input((99 * _TICK, 1), (105 * _TICK, 0), (110 * _TICK, 1), (130 * _TICK, 0), (140 * _TICK, 1)))
    assert tx.metadata["execution"]["status"] == rx.metadata["execution"]["status"] == "completed"
    assert (tx.rt_time_ns.value, rx.rt_time_ns.value) == (144, 119)
    result, = _results(tx)
    assert (result["bin_index"], result["count"], result["end_ticks"]) == (1, 1, 140 * _TICK)
    sent, = tx.metadata["simulation"]["ttl_trigger_transmissions"]
    assert sent["time_ticks"] == 110 * _TICK and sent["source_kind"] == "ttl_edge"
    assert sent["source_rt_packet_ids"] == [result["open_packet_id"]]
    assert sent["source_event_ids"] == result["input_event_ids"]
    if not prepared:
        receive, = _events(rx, "trigger_receive")
        assert receive.t0.value == 115 and receive.meta["source_event_id"] == sent["event_id"]
        assert receive.meta["source_event_ids"] == result["input_event_ids"]
        edge, = _events(tx, "digital_edge")
        assert edge.id == sent["source_event_ids"][0]
        flow, = timeline_ir_from_states([tx, rx])["feedback_flows"]
        assert flow["from_event_id"] == sent["event_id"] and flow["to_event_id"] == receive.id


@pytest.mark.parametrize("edge,latency", [(100 * _TICK, 0), (110 * _TICK + 1, 5)])
def test_ttl_edge_at_open_and_fractional_edge_use_exact_fixed_transport(edge, latency):
    tx, rx = _run(_GATE, routes=[_route(latency_ns=latency)], inputs=_input((edge, 1)))
    sent, = _events(tx, "trigger_send")
    received, = _events(rx, "trigger_receive")
    assert sent.meta["time_ticks"] == edge
    assert received.meta["time_ticks"] == edge + latency * _TICK
    assert received.t0.value == edge / _TICK + latency
    assert rx.rt_time_ns.value == received.t0.value + 4
    assert _results(tx)[0]["count"] == 1


def test_ttl_edge_requests_use_trigger_network_grid_and_can_arrive_after_gate_closes():
    tx, rx = _run(_GATE, routes=[_route(network="rack", extra_latency_ns=2)],
                  inputs=_input((110 * _TICK + 1, 1)), trigger_networks={"rack": {"grid_epoch_ns": 100}})
    transmission, = tx.metadata["simulation"]["trigger_network_transmissions"]
    assert (transmission["request_ticks"], transmission["launch_ticks"]) == (110 * _TICK + 1, 128 * _TICK)
    received, = _events(rx, "trigger_receive")
    assert received.t0.value == 342 and received.meta["source_kind"] == "ttl_edge"
    assert received.meta["source_rt_packet_ids"] == [_results(tx)[0]["open_packet_id"]]
    assert tx.rt_time_ns.value == 144 and rx.rt_time_ns.value == 346


def test_ttl_network_conflict_does_not_change_binned_counts():
    tx, rx = _run(_GATE, receiver="wait 500\nstop", routes=[_route(network="rack")],
                  inputs=_input((110 * _TICK, 1), (111 * _TICK, 0), (120 * _TICK, 1)),
                  trigger_networks={"rack": {"grid_epoch_ns": 100}})
    assert len(_events(tx, "trigger_send")) == 2 and not _events(rx, "trigger_receive")
    assert [item["status"] for item in tx.metadata["simulation"]["trigger_network_transmissions"]] == ["conflicted"] * 2
    assert _results(tx)[0]["count"] == 2


def test_auto_increment_keeps_each_trigger_linked_to_its_accepted_bin():
    tx, rx = _run(_GATE, receiver="wait 60\nstop", routes=[_route(latency_ns=5)],
                  inputs=_input((110 * _TICK, 1), (111 * _TICK, 0), (120 * _TICK, 1)),
                  digital={"ttl": {"ttl_acq_auto_bin_incr_en": True}})
    assert [(result["bin_index"], result["count"]) for result in _results(tx)] == [(1, 1), (2, 1)]
    assert [(event.meta["bin_index"], event.t0.value) for event in _events(rx, "trigger_receive")] == [(1, 115), (2, 125)]


@pytest.mark.parametrize("source,inputs,settings,expected_count", [
    ("set_cond 1,0,0,4\n" + _GATE, _input((110 * _TICK, 1)), {}, None),
    (_GATE, {"digital_acquisitions": [{"sequencer": "ttl", "kind": "acquire_ttl", "acquisition_index": 0,
                                       "bin_index": 1, "count": 3}]}, {}, 3),
    ("acquire 0,1,4\nstop", {"acquisitions": [{"sequencer": "ttl", "acquisition_index": 0, "bin_index": 1,
                                               "thresholded": 1}]}, {}, None),
    ("acquire_timetags 0,1,1,0,40\nacquire_timetags 0,1,0,0,4\nstop", _input((110 * _TICK, 1)), _QTM_FORWARD, 1),
])
def test_ttl_route_requires_an_accepted_edge_in_a_ttl_gate(source, inputs, settings, expected_count):
    tx, rx = _run(source, receiver="wait 60\nstop", routes=[_route(latency_ns=5)],
                  inputs=inputs, digital={"ttl": settings})
    assert tx.metadata["execution"]["status"] == "completed"
    assert not _events(rx, "trigger_receive")
    assert "ttl_trigger_transmissions" not in tx.metadata["simulation"]
    if expected_count is not None:
        assert _results(tx)[0]["count"] == expected_count


@pytest.mark.parametrize("settings", [{}, _QTM_FORWARD])
def test_default_integrated_and_qtm_routes_do_not_send_ttl_edges(settings):
    tx, rx = _run(_GATE, receiver="wait 60\nstop", routes=[{"source": "ttl", "target": "rx", "address": 3, "latency_ns": 5}],
                  inputs=_input((110 * _TICK, 1)), digital={"ttl": settings})
    assert _results(tx)[0]["count"] == 1
    assert not _events(tx, "trigger_send") and not _events(rx, "trigger_receive")


def test_ttl_and_integrated_routes_can_share_address_without_double_delivery():
    source = _GATE.replace("stop", "acquire 0,1,4\nstop")
    routes = [_route(latency_ns=5), {"source": "ttl", "target": "rx", "address": 3, "latency_ns": 5}]
    inputs = {**_input((110 * _TICK, 1)), "acquisitions": [{"sequencer": "ttl", "acquisition_index": 0,
                                                          "bin_index": 1, "thresholded": 1}]}
    tx, rx = _run(source, receiver="wait 100\nstop", routes=routes, inputs=inputs, digital={"ttl": _QTM_FORWARD})
    assert [(event.t0.value, event.meta.get("source_kind")) for event in _events(rx, "trigger_receive")] == [
        (115, "ttl_edge"), (153, None),
    ]
    assert _results(tx)[0]["count"] == 1


@pytest.mark.parametrize("fields,reason", [({"source_kind": "unknown"}, "source_kind must be ttl_edge"),
    ({"thresholded": 1}, "cannot be combined with source_module or thresholded"),
    ({"source_module": "rack"}, "cannot be combined with source_module or thresholded")])
def test_ttl_route_config_rejects_mixed_selectors(fields, reason):
    with pytest.raises(ValueError, match=reason):
        validate_simulation({"trigger_routes": [_route(latency_ns=5, **fields)]}, ["ttl", "rx"])
