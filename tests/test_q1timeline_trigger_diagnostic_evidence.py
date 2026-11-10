import pytest

from q1timeline.analysis.interpreter import prepare_def_program
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


_ANALOG = "acquire 0,0,4\nstop"
_WAIT = "wait_trigger 3,4\nstop"
_CONDITION = "set_cond 1,12,0,8\nupd_param 4\nstop"
_GATE = "acquire_timetags 0,0,1,0,20\nacquire_timetags 0,0,0,0,4\nstop"


def _route(source, *, address=3, **transport):
    return {"source": source, "target": "rx", "address": address, **(transport or {"latency_ns": 0})}


def _input(source, **result):
    return {"sequencer": source, "acquisition_index": 0, "bin_index": 0, **(result or {"i": 1, "q": 0})}


def _run(sources, routes, *, inputs, prepared=False, networks=None, digital=None, tables=None, hardware=None):
    programs = {name: parse_q1asm(text, file=f"{name}.q1asm") for name, text in sources.items()}
    prepared_names = programs if prepared is True else (prepared or ())
    bindings = {name: prepare_def_program(programs[name], params={}, strict_q1asm=False, varying=set()) for name in prepared_names}
    states = simulate_programs(
        programs, prepared_programs=bindings, hardware_parameters=hardware,
        sequences={name: {"acquisitions": {"result": {"index": 0, "num_bins": 1}}} for name in programs},
        simulation={"startup_delay_ns": 100, "inputs": inputs, "trigger_routes": routes,
                    "trigger_networks": networks or {}, "digital": digital or {}, "trigger_truth_tables": tables or {}},
    )
    return {state.sequencer_id: state for state in states}


def _events_by_id(states):
    return {event["id"]: event for event in timeline_ir_from_states(list(states.values()))["events"]}


def _links(states, *, category="acquisition_trigger_outcome_unknown", line=1):
    rx = states["rx"]
    assert rx.metadata["execution"]["status"] == "blocked"
    assert not any(event.kind == "trigger_receive" for event in rx.events)
    diagnostic, = [item for item in rx.diagnostics if item.category == category]
    assert (diagnostic.source.file, diagnostic.source.line) == ("rx.q1asm", line)
    events = _events_by_id(states)
    assert diagnostic.related_events and all(event_id in events for event_id in diagnostic.related_events)
    return [events[event_id] for event_id in diagnostic.related_events]


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("network", [False, True])
def test_wait_links_to_analog_result_before_any_network_request(prepared, network):
    route = _route("tx", network="rack") if network else _route("tx")
    states = _run({"tx": _ANALOG, "rx": _WAIT}, [route], inputs={"acquisitions": [_input("tx")]},
                  prepared=prepared, networks={"rack": {"grid_epoch_ns": 100}})
    links = _links(states)
    assert links[0]["kind"] == "acquisition_result" and links[0]["sequencer_id"] == "tx"
    assert [event["kind"] for event in links[1:]] == (["trigger_network_request"] if network and not prepared else [])


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("epoch_known", [False, True])
def test_network_uncertainty_links_to_retained_evidence(prepared, epoch_known):
    senders = ("a", "b") if epoch_known else ("a",)
    states = _run({**dict.fromkeys(senders, _ANALOG), "rx": _WAIT}, [_route(name, network="rack") for name in senders],
                  inputs={"acquisitions": [_input(name, thresholded=1) for name in senders]}, prepared=prepared,
                  networks={"rack": {"grid_epoch_ns": 100} if epoch_known else {}})
    links = _links(states, category="trigger_network_outcome_unknown")
    assert links[0]["kind"] == ("acquire" if prepared else "acquisition_result")
    assert links[0]["sequencer_id"] in senders
    if not prepared:
        assert {event["sequencer_id"] for event in links if event["kind"] == "trigger_network_request"} == set(senders)


@pytest.mark.parametrize("prepared", [False, True])
def test_possible_truth_round_links_to_its_acquisition_sources(prepared):
    states = _run({"left": _GATE, "right": _GATE, "rx": _WAIT},
                  [{"source_module": "qtm0", "target": "rx", "address": 3, "latency_ns": 0}],
                  inputs={"digital_acquisitions": [{**_input("left", count=2), "kind": "acquire_timetags"}]}, prepared=prepared,
                  digital={name: {"module": "qtm0", "channel": channel, "count_thresholds": {"low": 1, "high": 2}}
                           for name, channel in (("left", 4), ("right", 6))},
                  tables={"qtm0": [{"when": {4: "high", 6: "low"}, "address": 3}]})
    links = _links(states)
    assert {event["sequencer_id"] for event in links} == {"left", "right"}
    assert {event["kind"] for event in links} == ({"acquire"} if prepared else {"acquisition_result"})


@pytest.mark.parametrize("prepared", [False, True])
def test_condition_links_only_bounded_selected_address_evidence_since_reset(prepared):
    senders = {"expired": _ANALOG, "first": "wait 12\n" + _ANALOG, "later": "wait 16\n" + _ANALOG,
               "unselected": "wait 18\n" + _ANALOG}
    states = _run({**senders, "rx": "set_latch_en 1,12\nlatch_rst 4\nwait 8\n" + _CONDITION},
                  [_route(name, address=address) for name, address in (("expired", 3), ("first", 4), ("later", 4), ("unselected", 2))],
                  inputs={"acquisitions": [_input(name) for name in senders]}, prepared=prepared)
    links = _links(states, line=5)
    assert [(event["sequencer_id"], event["kind"]) for event in links] == [("later", "acquisition_result")]


def test_condition_drops_evidence_for_selected_address_with_exact_count():
    states = _run({"exact": _ANALOG, "optional": _ANALOG, "rx": "set_latch_en 1,20\n" + _CONDITION},
                  [_route("exact", latency_ns=4, thresholded=0), _route("exact", latency_ns=8, thresholded=1),
                   _route("optional", address=4)], inputs={"acquisitions": [_input("exact"), _input("optional")]},
                  hardware={"rx": {"trigger3_count_threshold": 2}})
    assert [event["sequencer_id"] for event in _links(states, line=3)] == ["optional"]


def _sender_links(states, name, category):
    events = _events_by_id(states)
    linked = []
    for diagnostic in states[name].diagnostics:
        if diagnostic.category == category:
            assert diagnostic.source.file == f"{name}.q1asm"
            assert diagnostic.related_events and all(event_id in events for event_id in diagnostic.related_events)
            linked.append((diagnostic, [events[event_id] for event_id in diagnostic.related_events]))
    return linked


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("digital_source", [False, True])
def test_sender_unknown_epoch_has_valid_diagnostic_links(prepared, digital_source):
    source, inputs, digital = _ANALOG, {"acquisitions": [_input("tx", thresholded=1)]}, {}
    if digital_source:
        source = _GATE
        inputs = {"digital": {"tx": {"initial_level": 0, "transitions": [{"time_ticks": 110 * 128, "level": 1}]}}}
        digital = {"tx": {"forward_trigger_en": True, "forward_trigger_mode": "rising", "forward_trigger_address": 3}}
    states = _run({"tx": source, "rx": _WAIT}, [_route("tx", network="rack")], inputs=inputs,
                  prepared=prepared, networks={"rack": {"grid_epoch": "synq"}}, digital=digital)
    (diagnostic, links), = _sender_links(states, "tx", "trigger_network_unknown_epoch")
    request, = states["tx"].metadata["simulation"]["trigger_network_transmissions"]
    assert diagnostic.severity == "warning" and request["status"] == "unknown_epoch"
    if prepared:
        assert [event["kind"] for event in links] == ["acquire"]
        assert request["request_event_id"] not in _events_by_id(states)
    else:
        assert diagnostic.related_events == [request["request_event_id"], request["source_event_id"]]
        assert [event["kind"] for event in links] == ["trigger_network_request", "trigger_send" if digital_source else "acquisition_result"]
    assert _links(states, category="trigger_network_outcome_unknown")


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("possible", [False, True])
@pytest.mark.parametrize("same_sender", [False, True])
def test_sender_collision_links_include_both_requests_or_anchors(prepared, possible, same_sender):
    senders = ("a", "a") if same_sender else ("a", "b")
    sources = {"a": "acquire 0,0,4\n" + _ANALOG} if same_sender else dict.fromkeys(senders, _ANALOG)
    routes = [_route(name, network="rack") for name in sources]
    inputs = {"acquisitions": [_input(senders[0], thresholded=1),
                               _input(senders[1]) if possible else _input(senders[1], thresholded=1)]}
    states = _run({**sources, "rx": _WAIT}, routes, inputs=inputs, prepared=prepared,
                  networks={"rack": {"grid_epoch_ns": 100}})
    records = [record for name in sources for record in states[name].metadata["simulation"]["trigger_network_transmissions"]]
    assert {record["status"] for record in records} == {"possible_conflict" if possible else "conflicted"}
    for name in sources:
        category = "trigger_network_possible_conflict" if possible else "trigger_network_conflict"
        linked = _sender_links(states, name, category)
        assert len(linked) == (2 if same_sender else 1)
        for diagnostic, links in linked:
            assert diagnostic.severity == ("warning" if possible else "error")
            if prepared:
                assert len(links) == 2 and {event["sequencer_id"] for event in links} == set(senders)
                assert {event["kind"] for event in links} == ({"acquire", "acquisition_result"} if possible else {"acquire"})
                if same_sender:
                    assert {event["source"]["line"] for event in links} == {1, 2}
            else:
                assert set(diagnostic.related_events) == {record[key] for record in records
                                                          for key in ("request_event_id", "source_event_id")}


def test_sender_collision_with_one_prepared_peer_keeps_retained_traced_links():
    states = _run({"a": _ANALOG, "b": _ANALOG, "rx": _WAIT}, [_route(name, network="rack") for name in ("a", "b")],
                  inputs={"acquisitions": [_input(name, thresholded=1) for name in ("a", "b")]}, prepared=("b",),
                  networks={"rack": {"grid_epoch_ns": 100}})
    for name in ("a", "b"):
        (diagnostic, links), = _sender_links(states, name, "trigger_network_conflict")
        assert diagnostic.severity == "error"
        assert [(event["sequencer_id"], event["kind"]) for event in links] == [
            ("a", "acquisition_result"), ("b", "acquire"), ("a", "trigger_network_request"),
        ]


@pytest.mark.parametrize("prepared", [False, True, ("tx",)])
def test_sender_window_interference_retains_the_window_and_acquisition(prepared):
    source = "acquire_ttl 0,0,1,40\nacquire_ttl 0,0,0,4\nstop"
    states = _run({"tx": _ANALOG, "window": source, "rx": _WAIT},
                  [_route("tx", network="rack"), _route("window", network="rack", source_kind="ttl_edge")],
                  inputs={"acquisitions": [_input("tx", thresholded=1)]}, prepared=prepared,
                  networks={"rack": {"grid_epoch_ns": 100}})
    (diagnostic, links), = _sender_links(states, "tx", "trigger_network_possible_conflict")
    request, = states["tx"].metadata["simulation"]["trigger_network_transmissions"]
    assert diagnostic.severity == "warning" and request["status"] == "possible_conflict"
    assert request["conflicting_window_event_id"] in diagnostic.related_events
    if prepared:
        assert [event["kind"] for event in links] == ["acquire", "trigger_edge_window"]
    else:
        assert diagnostic.related_events == [request["request_event_id"], request["conflicting_window_event_id"], request["source_event_id"]]
