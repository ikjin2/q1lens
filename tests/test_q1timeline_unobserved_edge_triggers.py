import pytest

from q1timeline.analysis.interpreter import prepare_def_program
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


_TICK = 128
_CONDITION = "set_cond 1,4,0,8\nupd_param 4\nstop"
_WAIT = "wait_trigger 3,4\nstop"
_GATES = {
    "qtm": "acquire_timetags 0,0,1,0,40\nacquire_timetags 0,0,0,0,4\nstop",
    "ttl": "acquire_ttl 0,0,1,40\nacquire_ttl 0,0,0,4\nstop",
}


def _run(kind, receiver, *, source=None, latency=5, trace=None, triggers=(), enabled=True, threshold=1,
         prepared=False, receiver_first=False):
    sources = [("tx", source or _GATES[kind]), ("rx", receiver)]
    programs = {name: parse_q1asm(text, file=f"{name}.q1asm") for name, text in (reversed(sources) if receiver_first else sources)}
    route = {"source": "tx", "target": "rx", "address": 3, "latency_ns": latency}
    if kind == "ttl":
        route["source_kind"] = "ttl_edge"
    digital = {"tx": {"forward_trigger_en": enabled, "forward_trigger_mode": "rising", "forward_trigger_address": 3}}
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    states = simulate_programs(
        programs, prepared_programs=bindings, hardware_parameters={"rx": {"trigger3_count_threshold": threshold}},
        sequences={"tx": {"acquisitions": {"counts": {"index": 0, "num_bins": 1}}}}, simulation={
            "startup_delay_ns": 100, "trigger_routes": [route], "digital": digital,
            "inputs": {"digital": {} if trace is None else {"tx": trace}, "triggers": list(triggers)},
        },
    )
    return {state.sequencer_id: state for state in states}


def _events(state, kind):
    return [event for event in state.events if event.kind == kind]


def _status(state):
    return state.metadata["execution"]["status"]


def _condition_outcome(state):
    packet, = [packet for packet in state.rt_packets if packet.op == "upd_param"]
    return "unknown" if packet.rt_t0 is None else "skip" if packet.meta.get("conditional_skipped") else "execute"


@pytest.mark.parametrize("kind", ["qtm", "ttl"])
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("missing", [False, True])
def test_missing_gate_trace_can_affect_condition_but_flat_trace_cannot(kind, prepared, missing):
    states = _run(kind, "set_latch_en 1,20\n" + _CONDITION, prepared=prepared,
                  trace=None if missing else {"initial_level": 0, "transitions": []})
    tx, rx = states["tx"], states["rx"]
    assert _status(tx) == "completed" and not _events(tx, "trigger_send") and not _events(rx, "trigger_receive")
    result, = tx.metadata["simulation"]["acquisitions"]
    assert result["status"] == ("unavailable" if missing else "modeled")
    assert result.get("count") == (None if missing else 0)
    assert _condition_outcome(rx) == ("unknown" if missing else "skip")
    if missing:
        edge_window, = _events(tx, "trigger_edge_window")
        assert edge_window.source.line == 1 and edge_window.source.file == "tx.q1asm"
        assert (edge_window.meta["start_ticks"], edge_window.meta["end_ticks"]) == (100 * _TICK, 140 * _TICK)
        assert edge_window.meta["source_rt_packet_ids"] == [result["open_packet_id"]]
        diagnostic, = [item for item in rx.diagnostics if item.category == "digital_edge_trigger_outcome_unknown"]
        assert diagnostic.related_events == [edge_window.id]
    else:
        assert not _events(tx, "trigger_edge_window")


@pytest.mark.parametrize("kind", ["qtm", "ttl"])
@pytest.mark.parametrize("arm,latency,blocked", [(100, 5, True), (120, 5, True), (144, 5, True),
                                                 (145, 5, False), (149, 5, False), (140, 0, False)])
def test_wait_sees_the_half_open_arrival_window(kind, arm, latency, blocked):
    receiver = (f"wait {arm - 100}\n" if arm > 100 else "") + _WAIT
    states = _run(kind, receiver, latency=latency, receiver_first=True,
                  triggers=[{"target": "rx", "address": 3, "time_ns": 160}])
    rx = states["rx"]
    wait, = _events(rx, "trigger_wait")
    assert _status(rx) == ("blocked" if blocked else "completed")
    assert wait.t0.value == arm and wait.t1.value == (max(105, arm) if blocked else 160)
    assert [item.t0.value for item in _events(rx, "trigger_receive")] == ([] if blocked else [160])


@pytest.mark.parametrize("kind", ["qtm", "ttl"])
@pytest.mark.parametrize("receiver_first", [False, True])
def test_zero_delay_window_does_not_affect_same_tick_rt_but_can_affect_next_packet(kind, receiver_first):
    receiver = "set_latch_en 1,8\nset_cond 1,4,0,8\nupd_param 4\nupd_param 4\nstop"
    states = _run(kind, receiver, source="wait 8\n" + _GATES[kind], latency=0, receiver_first=receiver_first)
    rx = states["rx"]
    first, second = [packet for packet in rx.rt_packets if packet.op == "upd_param"]
    assert first.rt_t0.value == 108 and first.meta["conditional_skipped"]
    assert second.rt_t0 is None and rx.rt_time_ns.value == 116 and _status(rx) == "blocked"


@pytest.mark.parametrize("kind", ["qtm", "ttl"])
def test_positive_delay_window_can_affect_rt_at_the_earliest_tick(kind):
    states = _run(kind, "set_latch_en 1,5\n" + _CONDITION, receiver_first=True)
    rx = states["rx"]
    assert _condition_outcome(rx) == "unknown" and rx.rt_time_ns.value == 105


@pytest.mark.parametrize("latency", [0, 5])
def test_known_arrival_at_earliest_tick_resolves_wait(latency):
    states = _run("ttl", _WAIT, latency=latency,
                  triggers=[{"target": "rx", "address": 3, "time_ns": 100 + latency}])
    rx = states["rx"]
    assert _status(rx) == "completed" and rx.rt_time_ns.value == 104 + latency
    receive, = _events(rx, "trigger_receive")
    assert receive.t0.value == 100 + latency


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("known", [False, True])
def test_trigger_interval_refusal_clips_q1_and_keeps_existing_evidence(prepared, pending, known):
    prefix = "wait_trigger 3,4\n" if pending else "set_latch_en 1,5\nset_cond 1,4,0,8\nupd_param 4\n"
    receiver = prefix + "wait 100\nmove 3,R0\n" + "nop\n" * (22 if pending else 20) + "add R0,1,R1\nstop"
    trace = {"initial_level": 0, "transitions": [{"time_ticks": 100 * _TICK, "level": 1}]} if known else None
    states = _run("qtm", receiver, prepared=prepared, trace=trace)
    tx, rx = states["tx"], states["rx"]
    refused = rx.rt_packets[0 if pending else 1]
    assert _status(tx) == "completed"
    if known:
        assert _status(rx) == "completed" and rx.rt_time_ns.value == 209 and rx.registers["R1"].value == 4
        assert refused.rt_t0.value == (100 if pending else 105) and refused.rt_t1.value == 109
        assert not rx.diagnostics
        return
    assert _status(rx) == "blocked" and rx.q1_time_ns.value == 105 and "R1" not in rx.registers
    assert refused.rt_t0 is None and refused.rt_t1 is None
    diagnostic, = rx.diagnostics
    window, = _events(tx, "trigger_edge_window")
    assert diagnostic.source.line == (1 if pending else 3) and diagnostic.related_events == [window.id]
    assert diagnostic.category == "digital_edge_trigger_outcome_unknown"
    assert not _events(rx, "trigger_receive") and not _events(rx, "conditional_skip")
    pending_write, = rx.pending_register_writes
    assert pending_write.register == "R1" and pending_write.meta["scheduled_ready_ns"] == 116
    if not prepared:
        add = _events(rx, "q1_issue")[-1]
        assert add.meta["op"] == "add" and (add.t0.value, add.t1.value) == (100, 105)
        assert add.meta["planned_q1_end_ns"] == 112 and add.meta["pending_register_ready_ns"] == {"R1": 116}
        if pending:
            wait, = _events(rx, "trigger_wait")
            assert (wait.t0.value, wait.t1.value, wait.meta["pending"]) == (100, 105, True)


@pytest.mark.parametrize("prepared", [False, True])
def test_possible_acquisition_trigger_refusal_preserves_result_evidence_and_clips_q1(prepared):
    receiver = "wait_trigger 3,4\nwait 100\nmove 3,R0\n" + "nop\n" * 22 + "add R0,1,R1\nstop"
    programs = {name: parse_q1asm(source, file=f"{name}.q1asm")
                for name, source in {"tx": "acquire 0,0,4\nstop", "rx": receiver}.items()}
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    tx, rx = simulate_programs(programs, prepared_programs=bindings, simulation={
        "startup_delay_ns": 100,
        "inputs": {"acquisitions": [{"sequencer": "tx", "acquisition_index": 0, "bin_index": 0, "i": 1, "q": 0}]},
        "trigger_routes": [{"source": "tx", "target": "rx", "address": 3, "latency_ns": 1}],
    })
    result, = _events(tx, "acquisition_result")
    diagnostic, = rx.diagnostics
    assert diagnostic.category == "acquisition_trigger_outcome_unknown" and diagnostic.related_events == [result.id]
    assert rx.q1_time_ns.value == 105 and "R1" not in rx.registers
    if not prepared:
        wait, = _events(rx, "trigger_wait")
        add = _events(rx, "q1_issue")[-1]
        assert add.t1 == wait.t1 and add.meta["planned_q1_end_ns"] == 112


def test_late_known_arrival_resolves_wait_at_earliest_tick_of_an_early_window():
    programs = {name: parse_q1asm(text) for name, text in {
        "unknown": _GATES["ttl"], "known": "wait 5\n" + _GATES["ttl"], "rx": _WAIT,
    }.items()}
    states = simulate_programs(programs, sequences={
        name: {"acquisitions": {"counts": {"index": 0, "num_bins": 1}}} for name in ("unknown", "known")
    }, simulation={
        "startup_delay_ns": 100,
        "trigger_routes": [{"source": name, "source_kind": "ttl_edge", "target": "rx", "address": 3, "latency_ns": delay}
                           for name, delay in (("unknown", 5), ("known", 0))],
        "inputs": {"digital": {"known": {"initial_level": 0, "transitions": [{"time_ticks": 105 * _TICK, "level": 1}]}}},
    })
    rx = states[-1]
    assert _status(rx) == "completed" and rx.rt_time_ns.value == 109
    receive, = _events(rx, "trigger_receive")
    assert receive.t0.value == 105 and receive.meta["source_sequencer_id"] == "known"


@pytest.mark.parametrize("prefix,latency,outcome", [
    ("wait 45\nset_latch_en 1,4", 5, "skip"),
    ("wait 44\nset_latch_en 1,4", 5, "unknown"),
    ("set_latch_en 1,20\nlatch_rst 4", 5, "unknown"),
    ("set_latch_en 1,45\nlatch_rst 4", 5, "skip"),
    ("set_latch_en 1,20\nset_latch_en 0,25\nlatch_rst 4", 5, "skip"),
    ("set_latch_en 1,20\nset_latch_en 0,25", 5, "unknown"),
    ("wait 40\nset_latch_en 1,4", 0, "skip"),
])
def test_latch_changes_only_count_possible_edges_during_enabled_epoch(prefix, latency, outcome):
    states = _run("ttl", prefix + "\n" + _CONDITION, latency=latency, receiver_first=True)
    assert _condition_outcome(states["rx"]) == outcome


def test_known_count_can_decide_a_condition_with_unobserved_edges():
    states = _run("qtm", "set_latch_en 1,20\n" + _CONDITION,
                  triggers=[{"target": "rx", "address": 3, "time_ns": 110}])
    assert _condition_outcome(states["rx"]) == "execute"


def test_unobserved_edges_do_not_affect_an_unrelated_address():
    states = _run("qtm", "set_latch_en 1,20\nset_cond 1,2,0,8\nupd_param 4\nstop")
    assert _condition_outcome(states["rx"]) == "skip"


@pytest.mark.parametrize("kind,enabled,skip", [("qtm", False, False), ("qtm", True, True), ("ttl", True, True)])
def test_unopened_or_disabled_gate_has_no_possible_trigger_interval(kind, enabled, skip):
    source = "set_cond 1,0,0,4\n" + _GATES[kind] if skip else _GATES[kind]
    states = _run(kind, "set_latch_en 1,20\n" + _CONDITION, source=source, enabled=enabled)
    assert _condition_outcome(states["rx"]) == "skip"
    assert not _events(states["tx"], "trigger_edge_window")


def test_fine_delayed_qtm_gate_uses_exact_effective_interval_boundaries():
    source = "acquire_timetags 0,0,1,1,40\nacquire_timetags 0,0,0,2,4\nstop"
    states = _run("qtm", _WAIT, source=source)
    edge_window, = _events(states["tx"], "trigger_edge_window")
    assert (edge_window.meta["start_ticks"], edge_window.meta["end_ticks"]) == (100 * _TICK + 1, 140 * _TICK + 2)
    wait, = _events(states["rx"], "trigger_wait")
    assert wait.t1.value == 105 + 1 / _TICK


def test_pending_fine_delayed_close_keeps_the_window_open_past_rt_stop():
    source = "acquire_timetags 0,0,1,0,40\nacquire_timetags 0,0,0,1024,4\nstop"
    states = _run("qtm", "wait 46\n" + _WAIT, source=source, latency=0)
    edge_window, = _events(states["tx"], "trigger_edge_window")
    assert states["tx"].rt_time_ns.value == 144 and edge_window.meta["end_ticks"] == 148 * _TICK
    wait, = _events(states["rx"], "trigger_wait")
    assert _status(states["rx"]) == "blocked" and wait.t0.value == wait.t1.value == 146


@pytest.mark.parametrize("kind", ["qtm", "ttl"])
def test_open_gate_interval_ends_when_source_rt_stops(kind):
    source = _GATES[kind].splitlines()[0] + "\nstop"
    states = _run(kind, "wait 45\n" + _WAIT, source=source,
                  triggers=[{"target": "rx", "address": 3, "time_ns": 160}])
    assert _status(states["tx"]) == "blocked" and _status(states["rx"]) == "completed"
    edge_window, = _events(states["tx"], "trigger_edge_window")
    assert edge_window.meta["end_ticks"] == 140 * _TICK


@pytest.mark.parametrize("receiver_first", [False, True])
def test_late_self_wait_failure_preserves_same_tick_edge_opportunity_for_peers(receiver_first):
    sources = [("tx", "acquire_ttl 0,0,1,4\nwait_trigger 3,4\nacquire_ttl 0,0,0,4\nstop"),
               ("rx", "wait 4\n" + _WAIT)]
    states = simulate_programs(
        {name: parse_q1asm(text) for name, text in (reversed(sources) if receiver_first else sources)},
        sequences={"tx": {"acquisitions": {"counts": {"index": 0, "num_bins": 1}}}}, simulation={
            "startup_delay_ns": 100,
            "trigger_routes": [{"source": "tx", "source_kind": "ttl_edge", "target": name, "address": 3, "latency_ns": 0}
                               for name in ("tx", "rx")],
        },
    )
    for state in states:
        assert any(item.category == "digital_edge_trigger_outcome_unknown" for item in state.diagnostics)
        wait, = _events(state, "trigger_wait")
        assert wait.t0.value == wait.t1.value == 104
