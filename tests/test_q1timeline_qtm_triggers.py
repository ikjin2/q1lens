import pytest

from q1timeline.analysis.interpreter import prepare_def_program
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.config import validate_simulation
from q1timeline.simulation.runtime import simulate_programs


_TICK = 128
_ADDRESS = 3
_QTM = "tagger"
_THRESHOLDS = {"low": 1, "high": 2}
_CLASSIFICATIONS = ("low", "mid", "high", "invalid")
_MODULE = "qtm0"
_SAMPLE = "acquire_digital 0,0,4\nstop"
_WINDOW = "acquire_timetags 0,0,1,0,20\nacquire_timetags 0,0,0,0,4\nstop"
_CONTROL = "set_latch_en 1,4\nwait_trigger 3,4\nset_cond 1,4,0,8\nupd_param 4\nstop"


def _simulate(sources, *, hardware=None, prepared=False, **config):
    programs = {name: parse_q1asm(source, file=f"{name}.q1asm") for name, source in sources.items()}
    memory = {name: {"acquisitions": {"a": {"index": 0, "num_bins": 1}}} for name in programs}
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    states = simulate_programs(programs, sequences=memory, hardware_parameters=hardware, prepared_programs=bindings,
                               simulation={"startup_delay_ns": 100, **config})
    return {state.sequencer_id: state for state in states}


def _events(state, kind):
    return [event for event in state.events if event.kind == kind]


def _forward(mode, *, enabled=True):
    return {"forward_trigger_en": enabled, "forward_trigger_mode": mode, "forward_trigger_address": _ADDRESS}


def _route(source, target, *, latency=0, module=False):
    return {"source_module" if module else "source": source, "target": target, "latency_ns": latency, "address": _ADDRESS}


def _trace(*transitions, initial=0):
    return {"initial_level": initial, "transitions": [{"time_ticks": ticks, "level": level} for ticks, level in transitions]}


def _sample_input(name, level):
    return {"sequencer": name, "kind": "acquire_digital", "acquisition_index": 0, "bin_index": 0, "level": level}


def _truth_inputs():
    return {name: {"module": _MODULE, "channel": channel, "count_thresholds": _THRESHOLDS}
            for name, channel in (("left", 4), ("right", 6))}


def _trigger_meta(state):
    return state.metadata["simulation"]["qtm_triggers"]


@pytest.mark.parametrize("physical_enabled", [False, True])
def test_fractional_rise_releases_its_own_wait_before_fine_delayed_gate_close(physical_enabled):
    source = ("set_latch_en 1,4\nacquire_timetags 0,0,1,1,4\nwait_trigger 3,4\n"
              "acquire_timetags 0,0,0,0,4\nset_cond 1,4,0,8\nupd_param 4\nstop")
    trace = _trace((104 * _TICK + 1, 1), (105 * _TICK, 0), (116 * _TICK + 1, 1))
    physical = {"physical": {"sequencers": {"analog": {}}}} if physical_enabled else {}
    states = _simulate({_QTM: source, "analog": "set_awg_offs 4096,0\nupd_param 32\nstop"},
                       digital={_QTM: _forward("rising")}, inputs={"digital": {_QTM: trace}},
                       trigger_routes=[_route(_QTM, _QTM, latency=8)], **physical)
    seq = states[_QTM]
    assert seq.metadata["execution"]["status"] == "completed"
    send, = _events(seq, "trigger_send")
    receive, = _events(seq, "trigger_receive")
    wait, = _events(seq, "trigger_wait")
    result, = _events(seq, "acquisition_result")
    assert send.t0.value == 104 + 1 / _TICK and receive.t0.value == 112 + 1 / _TICK
    assert wait.t0.value == 108 and wait.t1.value == receive.t0.value
    assert result.t0.value == 116 + 1 / _TICK and result.meta["count"] == 1
    update, = _events(seq, "upd_param")
    stop, = _events(seq, "stop")
    assert update.t0.value == 120 + 1 / _TICK and stop.t0.value == 124 + 1 / _TICK
    edge = next(event for event in seq.events if event.id == send.meta["source_event_ids"][0])
    assert edge.kind == "digital_transition" and edge.t0 == send.t0
    assert receive.meta["source_event_id"] == send.id and receive.meta["source_event_ids"] == [edge.id]
    ir = timeline_ir_from_states([seq])
    assert any(flow["from_event_id"] == send.id and flow["to_event_id"] == receive.id for flow in ir["feedback_flows"])
    analog = states["analog"]
    assert analog.metadata["execution"]["status"] == "completed"
    if physical_enabled:
        dac_i, = (trace for trace in analog.metadata["simulation"]["signals"]["traces"]
                  if trace["stage"] == "dac" and trace["path"] == "i")
        assert dac_i["dt_ns"] == 1
        assert dac_i["samples"][99:132] == [0.0] + [0.125] * 32


def test_falling_edge_arrival_is_retained_after_sender_and_receiver_rt_stop():
    trace = _trace((100 * _TICK, 0), (110 * _TICK, 1), (120 * _TICK, 0), initial=1)
    states = _simulate({_QTM: _WINDOW, "rx": "wait 4\nstop"}, digital={_QTM: _forward("falling")},
                       inputs={"digital": {_QTM: trace}}, trigger_routes=[_route(_QTM, "rx", latency=50)])
    sent, = _events(states[_QTM], "trigger_send")
    received, = _events(states["rx"], "trigger_receive")
    assert sent.t0.value == 100 and received.t0.value == 150
    assert states[_QTM].rt_time_ns.value == 124 and states["rx"].rt_time_ns.value == 104
    assert states[_QTM].metadata["simulation"]["acquisitions"][0]["count"] == 1


@pytest.mark.parametrize("mode,level", [("sampled-high", 1), ("sampled-low", 0)])
@pytest.mark.parametrize("prepared", [False, True])
def test_sample_forwarding_releases_a_wait_at_zero_latency(mode, level, prepared):
    states = _simulate({_QTM: _SAMPLE, "rx": "wait_trigger 3,4\nstop"}, prepared=prepared,
                       digital={_QTM: _forward(mode)}, inputs={"digital_acquisitions": [_sample_input(_QTM, level)]},
                       trigger_routes=[_route(_QTM, "rx")])
    assert all(state.metadata["execution"]["status"] == "completed" for state in states.values())
    transmission, = _trigger_meta(states[_QTM])["transmissions"]
    result, = states[_QTM].metadata["simulation"]["acquisitions"]
    assert transmission["source_event_ids"] == [result["event_id"]] and transmission["time_ticks"] == 100 * _TICK
    if not prepared:
        arrival, = _events(states["rx"], "trigger_receive")
        assert arrival.t0.value == 100 and arrival.meta["source_event_id"] == transmission["event_id"]


@pytest.mark.parametrize("mode,matching_level", [("sampled-high", 1), ("sampled-low", 0)])
@pytest.mark.parametrize("level_case,threshold,mask,outcome", [
    ("missing", 1, 4, "blocked"), ("missing", 2, 4, "conditional_skip"),
    ("missing", 1, 1, "conditional_skip"), ("nonmatch", 1, 4, "conditional_skip"),
    ("match", 1, 4, "upd_param"),
])
def test_sampled_trigger_level_bounds_only_the_configured_channel(mode, matching_level, level_case, threshold, mask, outcome):
    inputs = {} if level_case == "missing" else {"digital_acquisitions": [
        _sample_input(_QTM, matching_level if level_case == "match" else 1 - matching_level),
    ]}
    rx = f"set_latch_en 1,4\nwait 16\nset_cond 1,{mask},0,8\nupd_param 4\nstop"
    states = _simulate({_QTM: _SAMPLE, "rx": rx}, hardware={"rx": {"trigger3_count_threshold": threshold}},
                       digital={_QTM: _forward(mode)}, inputs=inputs, trigger_routes=[_route(_QTM, "rx", latency=8)])
    source = states[_QTM]
    receiver = states["rx"]
    sample, = source.metadata["simulation"]["acquisitions"]
    assert sample["status"] == ("unavailable" if level_case == "missing" else "supplied")
    expected_triggers = int(level_case == "match")
    assert len(_events(source, "trigger_send")) == len(_events(receiver, "trigger_receive")) == expected_triggers
    if outcome == "blocked":
        assert receiver.metadata["execution"]["status"] == "blocked"
        assert any(d.category == "acquisition_trigger_outcome_unknown" for d in receiver.diagnostics)
        assert not _events(receiver, "upd_param") and not _events(receiver, "conditional_skip")
    else:
        assert receiver.metadata["execution"]["status"] == "completed"
        packet, = _events(receiver, outcome)
        assert packet.t0.value == 120


@pytest.mark.parametrize("enabled,skipped", [(False, False), (True, True)])
def test_disabled_or_skipped_unknown_sample_does_not_contribute_an_optional_trigger(enabled, skipped):
    source = "set_cond 1,0,0,4\n" + _SAMPLE if skipped else _SAMPLE
    states = _simulate({_QTM: source, "rx": "set_latch_en 1,4\nwait 16\nset_cond 1,4,0,8\nupd_param 4\nstop"},
                       digital={_QTM: _forward("sampled-high", enabled=enabled)}, trigger_routes=[_route(_QTM, "rx", latency=8)])
    assert all(state.metadata["execution"]["status"] == "completed" for state in states.values())
    assert len(_events(states["rx"], "conditional_skip")) == 1


@pytest.mark.parametrize("latency", [0, 8])
@pytest.mark.parametrize("known", [False, True])
def test_known_sample_wins_an_optional_sample_on_the_same_fixed_route_edge(latency, known):
    rx = (f"wait {latency}\n" if latency else "") + "wait_trigger 3,4\nstop"
    sources = {_QTM: _SAMPLE, "rx": rx}
    routes = [_route(_QTM, "rx", latency=latency)]
    digital = {_QTM: _forward("sampled-high")}
    inputs = {}
    if known:
        sources["known"] = _SAMPLE
        digital["known"] = _forward("sampled-high")
        inputs["digital_acquisitions"] = [_sample_input("known", 1)]
        routes.append(_route("known", "rx", latency=latency))
    states = _simulate(sources, digital=digital, inputs=inputs, trigger_routes=routes)
    receiver = states["rx"]
    assert not _events(states[_QTM], "trigger_send")
    if known:
        arrival, = _events(receiver, "trigger_receive")
        assert arrival.t0.value == 100 + latency and arrival.meta["source_sequencer_id"] == "known"
        assert receiver.metadata["execution"]["status"] == "completed"
    else:
        assert not _events(receiver, "trigger_receive")
        assert receiver.metadata["execution"]["status"] == "blocked"
        assert any(d.category == "acquisition_trigger_outcome_unknown" for d in receiver.diagnostics)
        assert _events(receiver, "rt_queue_dequeue")[-1].t0.value == 100 + latency


@pytest.mark.parametrize("known", [False, True])
def test_unavailable_sample_is_optional_on_shared_trigger_network(known):
    sources = {_QTM: _SAMPLE, "rx": "wait_trigger 3,4\nstop"}
    routes = [{"source": _QTM, "target": "rx", "network": "rack", "address": _ADDRESS}]
    digital = {_QTM: _forward("sampled-low")}
    inputs = {}
    if known:
        sources["known"] = _SAMPLE
        digital["known"] = _forward("sampled-low")
        inputs["digital_acquisitions"] = [_sample_input("known", 0)]
        routes.append({**routes[0], "source": "known"})
    states = _simulate(sources, digital=digital, inputs=inputs, trigger_routes=routes, trigger_networks={"rack": {"grid_epoch_ns": 100}})
    sample, = _events(states[_QTM], "acquisition_result")
    record, = states[_QTM].metadata["simulation"]["trigger_network_transmissions"]
    assert record["source_event_id"] == sample.id
    assert record["status"] == ("possible_conflict" if known else "optional")
    route, = record["routes"]
    assert route["status"] == ("unknown_possible_conflict" if known else "possible")
    assert route["possible_arrival_ticks"] == 312 * _TICK
    assert "launch_event_id" not in record and "arrival_event_id" not in route
    assert not _events(states[_QTM], "trigger_send") and not _events(states["rx"], "trigger_receive")
    assert not any(d.category == "trigger_network_conflict" for state in states.values() for d in state.diagnostics)
    assert states["rx"].metadata["execution"]["status"] == "blocked"


@pytest.mark.parametrize("mode,instruction,enabled", [("rising", _WINDOW, False), ("sampled-high", "acquire_digital 0,0,4\nstop", False),
    ("rising", "set_cond 1,0,0,4\n" + _WINDOW, True),
    ("sampled-high", "set_cond 1,0,0,4\nacquire_digital 0,0,4\nstop", True),
    ("sampled-low", "acquire_digital 0,0,4\nstop", True)])
def test_disabled_skipped_and_unmatched_forwarding_emits_no_trigger(mode, instruction, enabled):
    trace = _trace((105 * _TICK, 0), (106 * _TICK, 1), initial=1)
    states = _simulate({_QTM: instruction, "rx": "wait 40\nstop"}, digital={_QTM: _forward(mode, enabled=enabled)},
                       inputs={"digital": {_QTM: trace}}, trigger_routes=[_route(_QTM, "rx")])
    assert all(state.metadata["execution"]["status"] == "completed" for state in states.values())
    assert not _events(states[_QTM], "trigger_send") and not _events(states["rx"], "trigger_receive")


@pytest.mark.parametrize("threshold,expected_op", [(1, "upd_param"), (2, "conditional_skip")])
@pytest.mark.parametrize("order", [("left", "right"), ("right", "left")])
def test_coincident_high_and_mid_send_once_and_feed_conditional_counter(threshold, expected_op, order):
    traces = {"left": _trace((101 * _TICK, 1), (102 * _TICK, 0), (104 * _TICK, 1)), "right": _trace((108 * _TICK, 1))}
    states = _simulate({**dict.fromkeys(order, _WINDOW), "rx": _CONTROL}, hardware={"rx": {"trigger3_count_threshold": threshold}},
                       digital=_truth_inputs(), inputs={"digital": traces},
                       trigger_truth_tables={_MODULE: [{"when": {4: "high", 6: "mid"}, "address": _ADDRESS}]},
                       trigger_routes=[_route(_MODULE, "rx", module=True, latency=6)])
    assert all(state.metadata["execution"]["status"] == "completed" for state in states.values())
    sent, = [event for state in states.values() for event in _events(state, "trigger_send")]
    received, = _events(states["rx"], "trigger_receive")
    assert sent.t0.value == 120 and received.t0.value == 126
    assert received.meta["address"] == _ADDRESS and received.meta["source_event_id"] == sent.id
    assert sent.meta["source_channels"] == [4, 6]
    sources = [_events(states[name], "acquisition_result")[0] for name in ("left", "right")]
    assert received.meta["source_event_ids"] == [event.id for event in sources]
    assert [event.meta["classification"] for event in sources] == ["high", "mid"]
    round_, = _trigger_meta(states["left"])["truth_rounds"]
    assert round_["rules"][0]["status"] == "matched" and round_["rules"][0]["trigger_event_id"] == sent.id
    assert _events(states["rx"], expected_op)[0].t0.value == 130


@pytest.mark.parametrize("peer,outcome", [("absent", "incomplete_round"), ("different_tick", "incomplete_round"),
    ("unknown", "unavailable"), ("invalid", "matched"), ("low", "no_match")])
def test_truth_round_keeps_missing_unknown_invalid_and_valid_zero_distinct(peer, outcome):
    right = _WINDOW
    if peer == "absent":
        right = "wait 4\nstop"
    elif peer == "different_tick":
        right = right.replace("0,0,0,0,4", "0,0,0,1,4")
    traces = {"left": _trace((101 * _TICK, 1), (102 * _TICK, 0), (104 * _TICK, 1))}
    supplied = []
    if peer == "invalid":
        supplied.append({"sequencer": "right", "kind": "acquire_timetags", "acquisition_index": 0, "bin_index": 0,
                         "count_valid": False})
    elif peer != "unknown":
        traces["right"] = _trace()
    states = _simulate({"left": _WINDOW, "right": right}, digital=_truth_inputs(),
                       inputs={"digital": traces, "digital_acquisitions": supplied},
                       trigger_truth_tables={_MODULE: [{"when": {4: "high", 6: "invalid"}, "address": _ADDRESS}]})
    rounds = _trigger_meta(states["left"])["truth_rounds"]
    assert [round_["rules"][0]["status"] for round_ in rounds] == [outcome] * (2 if peer == "different_tick" else 1)
    if peer == "different_tick":
        assert [round_["rules"][0]["missing_channels"] for round_ in rounds] == [[6], [4]]
    if peer in ("invalid", "low"):
        assert rounds[0]["inputs"][6]["classification"] == peer


@pytest.mark.parametrize("threshold", [1, 2])
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("duplicate_address", [False, True])
def test_compatible_unavailable_truth_rules_contribute_at_most_one_count(threshold, prepared, duplicate_address):
    rules = [{"when": {4: "high", 6: "mid"}, "address": _ADDRESS}]
    if duplicate_address:
        rules.append({"when": {4: "high", 6: "low"}, "address": _ADDRESS})
    receiver = "set_latch_en 1,4\nwait 36\nset_cond 1,4,0,8\nupd_param 4\nstop"
    trace = _trace((101 * _TICK, 1), (102 * _TICK, 0), (104 * _TICK, 1))
    states = _simulate({"left": _WINDOW, "right": _WINDOW, "rx": receiver}, prepared=prepared,
                       hardware={"rx": {"trigger3_count_threshold": threshold}}, digital=_truth_inputs(),
                       inputs={"digital": {"left": trace}}, trigger_truth_tables={_MODULE: rules},
                       trigger_routes=[_route(_MODULE, "rx", module=True, latency=6)])

    round_, = _trigger_meta(states["left"])["truth_rounds"]
    assert all(rule["status"] == "unavailable" and rule["trigger_possible"] for rule in round_["rules"])
    assert not any(_events(state, "trigger_send") for state in states.values())
    rx = states["rx"]
    assert not _events(rx, "trigger_receive")
    if threshold == 1:
        assert rx.metadata["execution"]["status"] == "blocked"
        diagnostic, = [item for item in rx.diagnostics if item.category == "acquisition_trigger_outcome_unknown"]
        assert (diagnostic.source.file, diagnostic.source.line) == ("rx.q1asm", 4)
    else:
        assert rx.metadata["execution"]["status"] == "completed"
        assert rx.rt_packets[-1].meta["conditional_skipped"] is True
        if not prepared:
            skip, = _events(rx, "conditional_skip")
            assert skip.t0.value == 140


@pytest.mark.parametrize("case", ["exhaustive", "partial", "known_invalid"])
@pytest.mark.parametrize("network", [False, True])
@pytest.mark.parametrize("prepared", [False, True])
def test_exhaustive_truth_rules_emit_one_definite_request(case, network, prepared):
    classes = _CLASSIFICATIONS[:-1] if case == "partial" else _CLASSIFICATIONS
    rules = [{"when": {4: "high", 6: kind}, "address": _ADDRESS} for kind in classes]
    trace = _trace((101 * _TICK, 1), (102 * _TICK, 0), (104 * _TICK, 1))
    supplied = ([{"sequencer": "right", "kind": "acquire_timetags", "acquisition_index": 0, "bin_index": 0,
                  "count_valid": False}] if case == "known_invalid" else [])
    route = _route(_MODULE, "rx", module=True, latency=6)
    networks = {}
    if network:
        route.pop("latency_ns")
        route["network"] = "rack"
        networks = {"rack": {"grid_epoch_ns": 100}}
    states = _simulate({"left": _WINDOW, "right": _WINDOW, "rx": _CONTROL}, prepared=prepared, digital=_truth_inputs(),
                       inputs={"digital": {"left": trace}, "digital_acquisitions": supplied},
                       trigger_truth_tables={_MODULE: rules}, trigger_routes=[route], trigger_networks=networks)

    tx, rx = states["left"], states["rx"]
    truth = _trigger_meta(tx)
    round_, = truth["truth_rounds"]
    assert round_["inputs"][6]["classification"] == ("invalid" if supplied else None)
    if case == "partial":
        assert rx.metadata["execution"]["status"] == "blocked"
        assert not truth["transmissions"] and not round_.get("proven_outputs")
        return

    assert rx.metadata["execution"]["status"] == "completed"
    arrival = 340 if network else 126
    assert rx.rt_time_ns.value == arrival + 8
    if not prepared:
        received, = _events(rx, "trigger_receive")
        assert received.t0.value == arrival
        assert received.meta["source_event_ids"] == [round_["inputs"][channel]["event_id"] for channel in (4, 6)]
    sent, = truth["transmissions"]
    assert sent["address"] == _ADDRESS and sent["source_channels"] == [4, 6] and sent["time_ticks"] == 120 * _TICK
    if network:
        launch, = tx.metadata["simulation"]["trigger_network_transmissions"]
        assert launch["status"] == "launched" and launch["launch_ticks"] == 128 * _TICK
        assert launch["routes"][0]["status"] == "delivered"
    if case == "exhaustive":
        assert all(rule["status"] == "unavailable" and "trigger_event_id" not in rule for rule in round_["rules"])
        proof, = round_["proven_outputs"]
        assert proof == {"address": _ADDRESS, "source_channels": [4, 6], "unavailable_channels": [6],
                         "assignment_count": 4, "trigger_event_id": sent["event_id"]}
    else:
        assert round_["rules"][-1]["status"] == "matched"
        assert not round_.get("proven_outputs")


def test_exhaustive_truth_coverage_counts_unspecified_unknown_channels():
    rules = [{"when": {4: "high", 6: "low"}, "address": _ADDRESS}]
    rules.extend({"when": {4: "high", 6: kind, 7: other}, "address": _ADDRESS}
                 for kind in _CLASSIFICATIONS[1:] for other in _CLASSIFICATIONS)
    trace = _trace((101 * _TICK, 1), (102 * _TICK, 0), (104 * _TICK, 1))
    digital = {**_truth_inputs(), "extra": {"module": _MODULE, "channel": 7, "count_thresholds": _THRESHOLDS}}
    states = _simulate({"left": _WINDOW, "right": _WINDOW, "extra": _WINDOW, "rx": _CONTROL}, digital=digital,
                       inputs={"digital": {"left": trace}}, trigger_truth_tables={_MODULE: rules},
                       trigger_routes=[_route(_MODULE, "rx", module=True, latency=6)])

    round_, = _trigger_meta(states["left"])["truth_rounds"]
    proof, = round_["proven_outputs"]
    assert proof["assignment_count"] == 16 and proof["unavailable_channels"] == [6, 7]
    received, = _events(states["rx"], "trigger_receive")
    assert received.meta["source_channels"] == [4, 6, 7]
    assert states["rx"].metadata["execution"]["status"] == "completed"


@pytest.mark.parametrize("peer,expected_status,arrives", [
    ("contradicted", "unavailable", False), ("absent", "incomplete_round", False),
    ("different_tick", "incomplete_round", False), ("known_invalid", "matched", True),
])
def test_truth_routing_requires_compatible_complete_round(peer, expected_status, arrives):
    trace = _trace((101 * _TICK, 1), (102 * _TICK, 0), (104 * _TICK, 1))
    receiver = "set_latch_en 1,4\nwait 36\nset_cond 1,4,0,8\nupd_param 4\nstop"
    right = _WINDOW
    if peer == "absent":
        right = "wait 4\nstop"
    elif peer == "different_tick":
        right = _WINDOW.replace("0,0,0,0,4", "0,0,0,1,4")
    samples = ([{"sequencer": "right", "kind": "acquire_timetags", "acquisition_index": 0, "bin_index": 0,
                 "count_valid": False}] if peer == "known_invalid" else [])
    rule = {"when": {4: "mid" if peer == "contradicted" else "high", 6: "invalid"}, "address": _ADDRESS}
    states = _simulate({"left": _WINDOW, "right": right, "rx": receiver}, digital=_truth_inputs(),
                       inputs={"digital": {"left": trace}, "digital_acquisitions": samples},
                       trigger_truth_tables={_MODULE: [rule]}, trigger_routes=[_route(_MODULE, "rx", module=True, latency=6)])

    assert all(round_["rules"][0]["status"] == expected_status for round_ in _trigger_meta(states["left"])["truth_rounds"])
    rx = states["rx"]
    assert rx.metadata["execution"]["status"] == "completed"
    assert [event.t0.value for event in _events(rx, "trigger_receive")] == ([126] if arrives else [])
    assert rx.rt_packets[-1].meta.get("conditional_skipped", False) == (not arrives)


@pytest.mark.parametrize("latency,known", [(0, False), (6, False), (6, True)])
def test_possible_truth_arrival_blocks_wait_unless_same_edge_is_known(latency, known):
    trace = _trace((101 * _TICK, 1), (102 * _TICK, 0), (104 * _TICK, 1))
    inputs = {"digital": {"left": trace}}
    if known:
        inputs["triggers"] = [{"target": "rx", "address": _ADDRESS, "time_ns": 120 + latency}]
    states = _simulate({"left": _WINDOW, "right": _WINDOW, "rx": "wait_trigger 3,4\nstop"},
                       digital=_truth_inputs(), inputs=inputs,
                       trigger_truth_tables={_MODULE: [{"when": {4: "high", 6: "mid"}, "address": _ADDRESS}]},
                       trigger_routes=[_route(_MODULE, "rx", module=True, latency=latency)])
    rx = states["rx"]
    if known:
        assert rx.metadata["execution"]["status"] == "completed"
        assert [event.t0.value for event in _events(rx, "trigger_receive")] == [120 + latency]
    else:
        assert rx.metadata["execution"]["status"] == "blocked" and not _events(rx, "trigger_receive")
        diagnostic, = [item for item in rx.diagnostics if item.category == "acquisition_trigger_outcome_unknown"]
        assert diagnostic.source.line == 1 and "release this wait_trigger" in diagnostic.message
        wait, = _events(rx, "trigger_wait")
        assert wait.meta["pending"] is True and wait.t1.value == 120 + latency


def test_exclusive_truth_addresses_can_share_a_network_slot_without_conflict():
    rules = [{"when": {4: "high", 6: kind}, "address": address}
             for kind, address in (("mid", 3), ("low", 5))]
    trace = _trace((101 * _TICK, 1), (102 * _TICK, 0), (104 * _TICK, 1))
    receiver = "set_latch_en 1,4\nwait 248\nset_cond 1,20,0,8\nupd_param 4\nstop"
    states = _simulate({"left": _WINDOW, "right": _WINDOW, "rx": receiver}, digital=_truth_inputs(),
                       inputs={"digital": {"left": trace}}, trigger_truth_tables={_MODULE: rules},
                       trigger_networks={"rack": {"grid_epoch_ns": 100}}, trigger_routes=[
                           {"source_module": _MODULE, "target": "rx", "address": rule["address"], "network": "rack"}
                           for rule in rules
                       ])

    tx, rx = states["left"], states["rx"]
    requests = tx.metadata["simulation"]["trigger_network_transmissions"]
    assert [item["address"] for item in requests] == [3, 5]
    assert all(item["status"] == "optional" and item["launch_ticks"] == 128 * _TICK for item in requests)
    assert all(item["routes"][0]["status"] == "possible" and item["routes"][0]["possible_arrival_ticks"] == 340 * _TICK
               for item in requests)
    assert not any("conflict" in diagnostic.category for diagnostic in tx.diagnostics)
    assert all(event.meta["source_channels"] == [4, 6] for event in _events(tx, "trigger_network_request"))
    assert rx.metadata["execution"]["status"] == "blocked" and not _events(rx, "trigger_receive")
    assert any(diagnostic.category == "acquisition_trigger_outcome_unknown" for diagnostic in rx.diagnostics)


def _truth_choices():
    return [{"when": {4: "high", 6: kind}, "address": address}
            for kind, address in zip(_CLASSIFICATIONS, (1, 1, 3, 3))]


def _choice_routes(*, network=False):
    transport = {"network": "rack"} if network else {"latency_ns": 6}
    return [{"source_module": _MODULE, "target": "rx", "address": address, **transport} for address in (1, 3)]


def _truth_choice_run(receiver, *, rules=None, routes=None, extra_inputs=None, prepared=False):
    trace = _trace((101 * _TICK, 1), (102 * _TICK, 0), (104 * _TICK, 1))
    return _simulate({"left": _WINDOW, "right": _WINDOW, "rx": receiver}, prepared=prepared, digital=_truth_inputs(),
                     inputs={"digital": {"left": trace}, **(extra_inputs or {})},
                     trigger_truth_tables={_MODULE: rules if rules is not None else _truth_choices()},
                     trigger_networks={"rack": {"grid_epoch_ns": 100}},
                     trigger_routes=routes if routes is not None else _choice_routes())


@pytest.mark.parametrize("operator,skipped", [(0, False), (2, True), (4, False)])
@pytest.mark.parametrize("network", [False, True])
@pytest.mark.parametrize("prepared", [False, True])
def test_exhaustive_truth_output_choices_resolve_conditions(operator, skipped, network, prepared):
    source = f"set_latch_en 1,4\nwait {248 if network else 36}\nset_cond 1,5,{operator},8\nupd_param 4\nstop"
    states = _truth_choice_run(source, routes=_choice_routes(network=network), prepared=prepared)
    round_, = _trigger_meta(states["left"])["truth_rounds"]
    assert round_["possible_outputs"] == [1, 3]
    assert all(rule["status"] == "unavailable" for rule in round_["rules"])
    rx = states["rx"]
    assert rx.metadata["execution"]["status"] == "completed"
    assert rx.rt_packets[-1].meta.get("conditional_skipped", False) is skipped
    assert not _events(rx, "trigger_receive")
    if network:
        requests = states["left"].metadata["simulation"]["trigger_network_transmissions"]
        assert len(requests) == 2 and all(request["status"] == "optional" for request in requests)
        assert all(request["truth_round_outputs"] == [1, 3] for request in requests)


@pytest.mark.parametrize("case", ["silent", "missing_route", "later_arrival"])
def test_truth_output_choices_do_not_guarantee_unobserved_arrivals(case):
    rules, routes = _truth_choices(), _choice_routes()
    if case == "silent":
        rules.pop()
    elif case == "missing_route":
        routes.pop()
    else:
        routes[-1]["latency_ns"] = 30
    receiver = "set_latch_en 1,4\nwait 36\nset_cond 1,5,0,8\nupd_param 4\nstop"
    states = _truth_choice_run(receiver, rules=rules, routes=routes)

    round_, = _trigger_meta(states["left"])["truth_rounds"]
    assert round_["possible_outputs"] == ([1, 3, None] if case == "silent" else [1, 3])
    rx = states["rx"]
    assert rx.metadata["execution"]["status"] == "blocked"
    assert not _events(rx, "trigger_receive")
    assert any(item.category == "acquisition_trigger_outcome_unknown" for item in rx.diagnostics)


def test_truth_output_choice_leaves_single_address_wait_unresolved():
    rx = _truth_choice_run("wait_trigger 3,4\nstop")["rx"]
    assert rx.metadata["execution"]["status"] == "blocked" and not _events(rx, "trigger_receive")
    diagnostic, = [item for item in rx.diagnostics if item.category == "acquisition_trigger_outcome_unknown"]
    assert "release this wait_trigger" in diagnostic.message


@pytest.mark.parametrize("reset,status", [(128, "blocked"), (144, "completed")])
def test_truth_choices_count_only_within_the_current_latch_epoch(reset, status):
    routes = _choice_routes()
    routes[1]["latency_ns"] = 14
    source = (f"set_latch_en 1,4\nwait {reset - 104}\nlatch_rst 4\nwait {148 - reset}\n"
              "set_cond 1,5,0,8\nupd_param 4\nstop")
    rx = _truth_choice_run(source, routes=routes)["rx"]
    assert rx.metadata["execution"]["status"] == status and not _events(rx, "trigger_receive")
    if status == "completed":
        assert rx.rt_packets[-1].meta["conditional_skipped"] is True


def test_truth_output_choices_preserve_an_independent_known_trigger():
    source = "set_latch_en 1,4\nwait 36\nset_cond 1,7,4,8\nupd_param 4\nstop"
    rx = _truth_choice_run(source, extra_inputs={"triggers": [{"target": "rx", "address": 2, "time_ns": 130}]})["rx"]
    assert rx.metadata["execution"]["status"] == "completed"
    assert [event.meta["address"] for event in _events(rx, "trigger_receive")] == [2]
    assert rx.rt_packets[-1].meta["conditional_skipped"] is True


@pytest.mark.parametrize("operator,status", [(0, "completed"), (2, "blocked"), (4, "blocked")])
def test_repeated_truth_rounds_keep_independent_output_choices(operator, status):
    window = _WINDOW.removesuffix("\nstop")
    source = f"set_latch_en 1,4\nwait 56\nset_cond 1,5,{operator},8\nupd_param 4\nstop"
    trace = _trace(*((time * _TICK, level) for time, level in (
        (101, 1), (102, 0), (104, 1), (125, 0), (126, 1), (127, 0), (128, 1),
    )))
    repeated = f"{window}\n{window}\nstop"
    states = _simulate({"left": repeated, "right": repeated, "rx": source}, digital=_truth_inputs(),
                       inputs={"digital": {"left": trace}}, trigger_truth_tables={_MODULE: _truth_choices()},
                       trigger_routes=_choice_routes())
    rounds = _trigger_meta(states["left"])["truth_rounds"]
    assert [round_["possible_outputs"] for round_ in rounds] == [[1, 3], [1, 3]]
    rx = states["rx"]
    assert rx.metadata["execution"]["status"] == status and not _events(rx, "trigger_receive")
    if status == "completed":
        assert rx.rt_packets[-1].meta.get("conditional_skipped", False) is False


def test_truth_output_choices_keep_network_collision_uncertainty():
    source = "set_latch_en 1,4\nwait 248\nset_cond 1,5,0,8\nupd_param 4\nstop"
    trace = _trace((101 * _TICK, 1), (102 * _TICK, 0), (104 * _TICK, 1))
    states = _simulate({"left": _WINDOW, "right": _WINDOW, "rx": source, "tx": "wait 20\nacquire 0,0,4\nstop"},
                       hardware={"tx": {"integration_length_acq": 1}}, digital=_truth_inputs(),
                       inputs={"digital": {"left": trace}, "acquisitions": [
                           {"sequencer": "tx", "acquisition_index": 0, "bin_index": 0, "thresholded": 1},
                       ]}, trigger_truth_tables={_MODULE: _truth_choices()}, trigger_networks={"rack": {"grid_epoch_ns": 100}},
                       trigger_routes=[*_choice_routes(network=True), {"source": "tx", "target": "rx", "address": 5, "network": "rack"}])
    requests = states["left"].metadata["simulation"]["trigger_network_transmissions"]
    assert all(request["status"] == "possible_conflict" for request in requests)
    rx = states["rx"]
    assert rx.metadata["execution"]["status"] == "blocked" and not _events(rx, "trigger_receive")


@pytest.mark.parametrize("tables,reason", [({"other": [{"address": 1, "when": {4: "mid"}}]}, "unknown digital module"),
    ({_MODULE: [{"address": 1, "when": {7: "high"}}]}, "unknown or duplicate digital channel"),
    ({_MODULE: [{"address": 1, "when": {4: "middle"}}]}, "low, mid, high or invalid"),
    ({_MODULE: [{"address": 1, "when": {4: "mid"}}, {"address": 2, "when": {4: "mid", 6: "high"}}]}, "overlaps")])
def test_invalid_and_ambiguous_truth_configuration_is_rejected(tables, reason):
    with pytest.raises(ValueError, match=reason):
        validate_simulation({"digital": _truth_inputs(), "trigger_truth_tables": tables}, ["left", "right"])
