import pytest

from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


_TICKS = 128
_TTL_GATE = "acquire_ttl 0,0,1,40\nacquire_ttl 0,0,0,4\nstop"
_QTM_GATE = "acquire_timetags 0,0,1,0,40\nacquire_timetags 0,0,0,0,4\nstop"
_ACQUIRE = "acquire 0,0,4\nstop"
_WAIT = "wait_trigger 3,4\nstop"


def _route(source, target, *, extra=0, ttl=False):
    route = {"source": source, "target": target, "network": "rack", "address": 3, "extra_latency_ns": extra}
    if ttl:
        route["source_kind"] = "ttl_edge"
    return route


def _run(sources, routes, *, inputs=None, digital=None, epoch=None):
    programs = {name: parse_q1asm(code, file=f"{name}.q1asm") for name, code in sources.items()}
    memory = {name: {"acquisitions": {"a": {"index": 0, "num_bins": 1}}} for name in programs}
    states = simulate_programs(programs, sequences=memory, simulation={
        "startup_delay_ns": 100, "inputs": inputs or {}, "digital": digital or {}, "trigger_routes": routes,
        "trigger_networks": {"rack": {"grid_epoch_ns": 100} if epoch is None else epoch},
    })
    return {state.sequencer_id: state for state in states}


def _events(state, kind):
    return [event for event in state.events if event.kind == kind]


def _status(state):
    return state.metadata["execution"]["status"]


def _known_acquisition(name):
    return {"sequencer": name, "acquisition_index": 0, "bin_index": 0, "thresholded": 1}


@pytest.mark.parametrize("gate,ttl", [(_TTL_GATE, True), (_QTM_GATE, False)])
def test_missing_edge_window_uses_network_slots_and_route_delay(gate, ttl):
    sources = {"edge": gate, "early": _WAIT, "inside": "wait 240\n" + _WAIT,
               "after": "wait 271\n" + _WAIT, "tie": _WAIT}
    routes = [_route("edge", target, extra=2, ttl=ttl) for target in ("early", "inside", "after", "tie")]
    digital = {} if ttl else {"edge": {"forward_trigger_en": True, "forward_trigger_mode": "rising", "forward_trigger_address": 3}}
    states = _run(sources, routes, digital=digital, inputs={"triggers": [
        {"target": "after", "address": 3, "time_ns": 380}, {"target": "tie", "address": 3, "time_ns": 314},
    ]})
    window, = _events(states["edge"], "trigger_edge_window")
    assert (window.meta["start_ticks"], window.meta["end_ticks"]) == (100 * _TICKS, 140 * _TICKS)
    assert not _events(states["edge"], "trigger_send") and not _events(states["edge"], "trigger_network_launch")
    for target in ("early", "inside"):
        state = states[target]
        assert _status(state) == "blocked"
        diagnostic = state.diagnostics[-1]
        assert diagnostic.category == "digital_edge_trigger_outcome_unknown" and diagnostic.related_events == [window.id]
        assert not _events(state, "trigger_receive")
    wait, = _events(states["early"], "trigger_wait")
    assert (wait.t0.value, wait.t1.value) == (100, 314)
    for target, time in (("after", 380), ("tie", 314)):
        receive, = _events(states[target], "trigger_receive")
        assert receive.t0.value == time and _status(states[target]) == "completed"


def test_unobserved_gate_interference_preserves_delivered_and_earlier_pending_routes():
    sources = {"known": _ACQUIRE, "edge": "wait 250\n" + _TTL_GATE,
               "early": _WAIT, "before_slot": _WAIT, "late": _WAIT, "maybe": _WAIT}
    routes = [_route("known", "early"), _route("known", "before_slot", extra=11), _route("known", "late", extra=80),
              _route("edge", "maybe", ttl=True)]
    states = _run(sources, routes, inputs={"acquisitions": [_known_acquisition("known")]})
    sender = states["known"]
    window, = _events(states["edge"], "trigger_edge_window")
    record, = sender.metadata["simulation"]["trigger_network_transmissions"]
    assert record["status"] == "possible_conflict" and record["conflicting_window_event_id"] == window.id
    assert [route["status"] for route in record["routes"]] == ["delivered", "delivered", "unknown_possible_conflict"]
    diagnostic, = [item for item in sender.diagnostics if item.category == "trigger_network_possible_conflict"]
    assert window.id in diagnostic.related_events and diagnostic.severity == "warning"
    for target, time in (("early", 340), ("before_slot", 351)):
        receive, = _events(states[target], "trigger_receive")
        assert receive.t0.value == time and _status(states[target]) == "completed"
    assert _status(states["late"]) == "blocked" and not _events(states["late"], "trigger_receive")
    assert not any(item.category == "trigger_network_conflict" for state in states.values() for item in state.diagnostics)


@pytest.mark.parametrize("wait,launch,conflict", [(272, 380, True), (300, 408, False)])
def test_later_known_launch_uses_last_possible_slot_and_exact_spacing(wait, launch, conflict):
    sources = {"known": f"wait {wait}\n" + _ACQUIRE, "edge": _TTL_GATE, "maybe": _WAIT, "rx": _WAIT}
    routes = [_route("known", "rx"), _route("edge", "maybe", ttl=True)]
    states = _run(sources, routes, inputs={"acquisitions": [_known_acquisition("known")]})
    record, = states["known"].metadata["simulation"]["trigger_network_transmissions"]
    assert record["launch_ticks"] == launch * _TICKS
    assert record["status"] == ("possible_conflict" if conflict else "launched")
    assert _status(states["rx"]) == ("blocked" if conflict else "completed")
    assert [event.t0.value for event in _events(states["rx"], "trigger_receive")] == ([] if conflict else [launch + 212])


def test_possible_edge_window_does_not_downgrade_a_conflict_between_two_known_launches():
    sources = {"a": _ACQUIRE, "b": _ACQUIRE, "edge": _TTL_GATE, "rx": _WAIT, "maybe": _WAIT}
    routes = [_route("a", "rx"), _route("b", "rx"), _route("edge", "maybe", ttl=True)]
    states = _run(sources, routes, inputs={"acquisitions": [_known_acquisition("a"), _known_acquisition("b")]})
    for name in ("a", "b"):
        sender = states[name]
        record, = sender.metadata["simulation"]["trigger_network_transmissions"]
        assert record["status"] == "conflicted" and "conflicting_window_event_id" not in record
        diagnostic, = [item for item in sender.diagnostics if item.category == "trigger_network_conflict"]
        assert diagnostic.severity == "error"
    assert _status(states["rx"]) == "blocked" and not _events(states["rx"], "trigger_receive")


def test_window_with_unresolved_epoch_does_not_invent_a_launch():
    states = _run({"edge": _TTL_GATE, "rx": _WAIT}, [_route("edge", "rx", ttl=True)], epoch={"grid_epoch": "synq"})
    edge = states["edge"]
    window, = _events(edge, "trigger_edge_window")
    diagnostic, = [item for item in edge.diagnostics if item.category == "trigger_network_unknown_epoch"]
    assert diagnostic.related_events == [window.id]
    assert not _events(edge, "trigger_network_launch")
    assert _status(states["rx"]) == "blocked" and not _events(states["rx"], "trigger_receive")
