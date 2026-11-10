import pytest

from q1timeline.analysis.interpreter import prepare_def_program
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.config import validate_simulation
from q1timeline.simulation.runtime import simulate_programs


def _run(sources, *, prepared=False, memory=None, **options):
    programs = {name: parse_q1asm(source, file=f"{name}.q1asm") for name, source in sources.items()}
    if memory is None:
        memory = {name: {"acquisitions": {"result": {"index": 0, "num_bins": 4}}} for name in sources}
    prepared_programs = {name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                         for name, program in programs.items()} if prepared else None
    states = simulate_programs(programs, sequences=memory, prepared_programs=prepared_programs,
                               simulation={"startup_delay_ns": 100, **options})
    return {state.sequencer_id: state for state in states}


def _trace(*rising_ticks):
    return {"initial_level": 0, "transitions": [
        {"time_ticks": tick + offset, "level": level}
        for tick in rising_ticks for offset, level in ((0, 1), (1, 0))
    ]}


def _events(state, kind):
    return [event for event in state.events if event.kind == kind]


def _results(state):
    return state.metadata["simulation"]["acquisitions"]


@pytest.mark.parametrize("prepared", [False, True])
def test_qblox_published_time_delta_loopback_uses_second_relative_to_first4(prepared):
    # The official QTM LINQ example expects 100 ns between the two pulse starts.
    # It divides the measured receiver word by 128 and allows hardware jitter.
    sender = ("wait_sync 4\nset_digital 1,1,0\nupd_param 4\nset_digital 0,1,0\nupd_param 96\n"
              "set_digital 1,1,0\nupd_param 4\nset_digital 0,1,0\nupd_param 4\nwait 1200\nfb_pop_data 16,R0\nstop")
    receiver = ("fb_tdc_tdelta_id 16,8\nwait_sync 4\nacquire_timetags 0,0,1,0,4\nwait 100\n"
                "acquire_timetags 0,0,0,0,4\nfb_tdc_tdelta_id 0,8\nstop")
    states = _run({"tx": sender, "rx": receiver}, prepared=prepared,
                  digital={"rx": {"module": "QTM0", "channel": 4, "time_reference": "first4",
                                  "time_source": "second", "invalid_time_delta": "record_0"}},
                  digital_routes=[{"source": "tx", "target": "rx"}],
                  routes=[{"source": "rx", "target": "tx", "id": 16, "latency_ns": 1000}])
    tx, rx = states["tx"], states["rx"]
    assert all(state.metadata["execution"]["status"] == "completed" for state in states.values())
    assert tx.registers["R0"].value == 12_800
    result, = _results(rx)
    first, second = result["input_event_ids"]
    assert result["reference_event_id"] == first and result["time_source_event_id"] == second
    assert result["edge_times_ticks"] == [112 * 128, 212 * 128]
    assert result["reference_ticks"] == 112 * 128 and result["timetag_ticks"] == 12_800
    if not prepared:
        sent, = _events(rx, "feedback_send")
        received, = _events(tx, "feedback_receive")
        assert sent.t0.value == 216 and received.t0.value == 1216
        assert sent.meta["source_event_id"] == result["event_id"]
        assert sent.meta["source_packet_id"] == result["rt_packet_id"]
        assert received.meta["source_event_id"] == sent.id


def test_end_reference_sends_exact_negative_twos_complement_word():
    states = _run({"tags": "fb_tdc_tdelta_id 16,4\nacquire_timetags 0,0,1,0,12\nacquire_timetags 0,1,0,0,4\nstop",
                   "reader": "wait 0\nfb_pop_data 16,R0\nnop\nstop"},
                  digital={"tags": {"time_reference": "end"}},
                  inputs={"digital": {"tags": _trace(106 * 128 + 1)}},
                  routes=[{"source": "tags", "target": "reader", "id": 16, "latency_ns": 7}])
    result, = _results(states["tags"])
    assert result["valid"] and result["reference_ticks"] == 116 * 128
    assert result["timetag_ticks"] == -1279
    sent, = _events(states["tags"], "feedback_send")
    assert sent.meta["encoded_time_ticks"] == -1279 and sent.meta["value"] == 0xFFFFFB01
    assert states["reader"].registers["R0"].value == 0xFFFFFB01


def test_independent_effective_gates_finish_after_rt_stop_and_keep_post_stop_edges():
    states = _run({"tags": "fb_tdc_tdelta_id 16,4\nacquire_timetags 0,0,1,1,4\nacquire_timetags 0,3,0,1024,4\nstop",
                   "reader": "wait 0\nfb_pop_data 16,R0\nnop\nstop"},
                  digital={"tags": {"time_source": "last"}},
                  inputs={"digital": {"tags": _trace(104 * 128 - 1, 104 * 128 + 1, 114 * 128 + 7, 116 * 128)}},
                  routes=[{"source": "tags", "target": "reader", "id": 16, "latency_ns": 1}])
    tags = states["tags"]
    result, = _results(tags)
    assert tags.rt_time_ns.value == 112
    assert tags.rt_packets[-1].rt_t0.value == 108 and tags.rt_packets[-1].rt_t1.value == 112
    assert result["start_ticks"] == 104 * 128 + 1 and result["end_ticks"] == 116 * 128
    assert result["edge_times_ticks"] == [104 * 128 + 1, 114 * 128 + 7]
    assert result["bin_index"] == 3 and result["timetag_ticks"] == 1286
    assert result["open_delay_ticks"] == 1 and result["close_delay_ticks"] == 1024
    assert result["time_ns"] == 116 and result["fine_delay_model"] == "nominal_ticks"
    assert _events(tags, "feedback_send")[0].t0.value == 116
    assert states["reader"].registers["R0"].value == 1286


@pytest.mark.parametrize("prepared", [False, True])
def test_delayed_close_without_open_keeps_original_gate_and_closes_both_cores(prepared):
    source = "acquire_timetags 0,0,0,1025,4\nwait 100\n" + "nop\n" * 22 + "move 3,R0\nnop\nadd R0,1,R1\nstop"
    states = _run({"tags": source, "peer": "wait 150\nstop"}, prepared=prepared)
    tags, peer = states["tags"], states["peer"]
    close, wait = tags.rt_packets
    edge = 108 + 1 / 128
    diagnostic, = tags.diagnostics
    assert tags.metadata["execution"]["status"] == "blocked"
    assert tags.metadata["execution"]["reason"] == "Digital acquisition close has no open gate."
    assert diagnostic.source.line == 1 and diagnostic.source.raw == "acquire_timetags 0,0,0,1025,4"
    assert (close.rt_t0.value, close.rt_t1.value) == (100, 104) and "observation_cutoff_ns" not in close.meta
    assert tags.q1_time_ns.value == tags.rt_time_ns.value == wait.rt_t1.value == edge
    assert wait.rt_t0.value == 104 and wait.meta["planned_rt_end_ns"] == 204 and wait.meta["observation_cutoff_ns"] == edge
    assert "R1" not in tags.registers and not _results(tags) and not tags.metadata["simulation"]["digital_bins"]
    assert peer.metadata["execution"]["status"] == "completed" and peer.rt_time_ns.value == 250
    gate, = _events(tags, "acquire")
    assert gate.id == close.meta["rt_execution_event_id"] and gate.t1 == close.rt_t1
    if prepared:
        assert diagnostic.related_events == [gate.id]
    else:
        clipped, = _events(tags, "wait")
        issue = _events(tags, "q1_issue")[-1]
        assert clipped.t1 == issue.t1 == wait.rt_t1 and diagnostic.related_events == [gate.id, clipped.id]
        assert issue.meta["op"] == "add" and issue.t0.value == 108 and issue.meta["planned_q1_end_ns"] == 120
        assert issue.meta["pending_register_ready_ns"] == {"R1": 124}


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("missing_bin", [False, True])
def test_delayed_qtm_close_preserves_observed_scope_when_bin_is_missing(prepared, missing_bin):
    source = "set_scope_en 1\nacquire_timetags 0,0,1,0,8\nacquire_timetags 0,0,0,1025,4\nwait 100\nstop"
    tags = _run({"tags": source}, prepared=prepared, memory={} if missing_bin else None,
                digital={"tags": {"scope_mode": "timetags-windowed"}},
                inputs={"digital": {"tags": _trace(105 * 128 + 1)}})["tags"]
    opened, closed, wait = tags.rt_packets
    opening, gate = _events(tags, "acquire")
    edge_ticks = 116 * 128 + 1
    edge = edge_ticks / 128
    assert opened.rt_t1 == closed.rt_t0 and (closed.rt_t0.value, closed.rt_t1.value) == (108, 112)
    assert gate.t1 == closed.rt_t1 and "observation_cutoff_ns" not in closed.meta
    scope = tags.metadata["simulation"]["qtm_scope"]
    capture, = scope["captures"]
    assert capture["status"] == "completed" and capture["open_event_id"] == opening.id and capture["close_event_id"] == gate.id
    assert [(item["kind"], item["time_ticks"]) for item in scope["entries"]] == [
        ("OPEN", 100 * 128), ("RISE", 105 * 128 + 1), ("CLOSE", edge_ticks),
    ]
    if missing_bin:
        diagnostic, = tags.diagnostics
        assert tags.metadata["execution"]["status"] == "blocked" and "Missing digital acquisition 0 bin definition" in diagnostic.message
        assert diagnostic.source == gate.source and wait.rt_t1.value == edge
        assert wait.meta["planned_rt_end_ns"] == 212 and wait.meta["observation_cutoff_ns"] == edge
        assert diagnostic.related_events == [gate.id, *[event.id for event in _events(tags, "wait")]]
        assert not _results(tags) and not tags.metadata["simulation"]["digital_bins"]
    else:
        result, = _results(tags)
        assert tags.metadata["execution"]["status"] == "completed" and not tags.diagnostics
        assert wait.rt_t1.value == 212 and "observation_cutoff_ns" not in wait.meta
        assert result["time_ns"] == edge and result["count"] == 1
        assert result["event_id"] == tags.metadata["simulation"]["digital_bins"][0]["event_id"]


def test_fractional_close_keeps_local_delta_and_reports_linq_scheduling_limit():
    states = _run({"tags": "fb_tdc_tdelta_id 16,4\nacquire_timetags 0,0,1,0,8\nacquire_timetags 0,0,0,1,4\nstop"},
                  inputs={"digital": {"tags": _trace(107 * 128 + 1)}})
    tags = states["tags"]
    result, = _results(tags)
    assert result["valid"] and result["timetag_ticks"] == 385
    assert result["time_ns"] == 112 + 1 / 128
    assert tags.metadata["execution"]["status"] == "blocked"
    assert "Fractional QTM LINQ" in tags.metadata["execution"]["reason"]
    assert not _events(tags, "feedback_send")


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize(("close_delay", "identifier", "blocked"), [(1025, 16, True), (1024, 16, False), (1025, 0, False)])
def test_delayed_qtm_linq_refusal_preserves_local_result_and_closes_active_wait(prepared, close_delay, identifier, blocked):
    program = (f"fb_tdc_tdelta_id {identifier},4\nacquire_timetags 0,0,1,0,8\n"
               f"acquire_timetags 0,0,0,{close_delay},4\nwait 100\nstop")
    states = _run({"tags": program, "peer": "wait 150\nstop"}, prepared=prepared,
                  inputs={"digital": {"tags": _trace(107 * 128 + 1)}})
    tags, peer = states["tags"], states["peer"]
    result, = _results(tags)
    accepted_bin, = tags.metadata["simulation"]["digital_bins"]
    close, wait = tags.rt_packets[-2:]
    edge = 112 + close_delay / 128
    assert result["status"] == "modeled" and result["valid"] and result["timetag_ticks"] == 385
    assert result["end_ticks"] == 112 * 128 + close_delay and result["time_ns"] == edge
    assert result["rt_packet_id"] == close.id and accepted_bin["event_id"] == result["event_id"]
    assert accepted_bin["timetag_ticks"] == 385 and accepted_bin["count"] == 1
    assert close.rt_t1 == wait.rt_t0 and close.rt_t1.value == 116 and "observation_cutoff_ns" not in close.meta
    assert peer.metadata["execution"]["status"] == "completed" and peer.rt_time_ns.value == 250
    if blocked:
        assert tags.metadata["execution"]["status"] == "blocked" and "Fractional QTM LINQ" in tags.metadata["execution"]["reason"]
        assert wait.rt_t1.value == tags.rt_time_ns.value == edge
        assert wait.meta["planned_rt_end_ns"] == 216 and wait.meta["observation_cutoff_ns"] == edge
        diagnostic, = tags.diagnostics
        assert diagnostic.source.line == 3 and diagnostic.category == "analysis_incomplete"
        assert not _events(tags, "feedback_send")
        if prepared:
            assert not diagnostic.related_events
        else:
            acquisition, = _events(tags, "acquisition_result")
            clipped, = _events(tags, "wait")
            assert clipped.t1 == acquisition.t0 and clipped.t1.value == edge
            assert acquisition.id == result["event_id"] and acquisition.meta["time_source_event_id"] in result["input_event_ids"]
            assert diagnostic.related_events == [acquisition.id, clipped.id]
    else:
        assert tags.metadata["execution"]["status"] == "completed" and wait.rt_t1.value == 216
        assert "observation_cutoff_ns" not in wait.meta and not tags.diagnostics
        if identifier and not prepared:
            send, = _events(tags, "feedback_send")
            assert send.meta["value"] == 385 and send.t0.value == edge
        else:
            assert not _events(tags, "feedback_send")


@pytest.mark.parametrize("prepared", [False, True])
def test_supplied_invalid_qtm_delta_closes_both_cores_and_keeps_invalid_result(prepared):
    source = ("fb_tdc_tdelta_id 16,4\nacquire_timetags 0,0,1,0,8\nacquire_timetags 0,0,0,1025,4\nwait 100\n"
              + "nop\n" * 22 + "move 3,R0\nnop\nadd R0,1,R1\nstop")
    supplied = {"sequencer": "tags", "kind": "acquire_timetags", "acquisition_index": 0, "bin_index": 0, "count": 1}
    tags = _run({"tags": source}, prepared=prepared, digital={"tags": {"invalid_time_delta": "error"}},
                inputs={"digital_acquisitions": [supplied]})["tags"]
    edge = 120 + 1 / 128
    result, = _results(tags)
    assert result["status"] == "invalid" and not result["valid"] and result["count"] == 1
    assert result["source"] == {"kind": "digital_scenario", "input_index": 0} and result["time_ns"] == edge
    assert not tags.metadata["simulation"]["digital_bins"] and not _events(tags, "feedback_send")
    assert tags.metadata["execution"]["status"] == "error"
    assert tags.q1_time_ns.value == tags.rt_time_ns.value == edge
    assert tags.rt_packets[-1].meta["planned_rt_end_ns"] == 216 and "R1" not in tags.registers
    diagnostic, = tags.diagnostics
    assert diagnostic.category == "invalid_time_delta" and diagnostic.source.line == 3
    if prepared:
        assert not diagnostic.related_events
    else:
        acquisition, = _events(tags, "acquisition_result")
        wait, = _events(tags, "wait")
        issue = _events(tags, "q1_issue")[-1]
        assert issue.meta["op"] == "add" and issue.t0.value == 120 and issue.t1.value == edge
        assert issue.meta["planned_q1_end_ns"] == 132 and issue.meta["pending_register_ready_ns"] == {"R1": 136}
        assert wait.t1 == acquisition.t0 == issue.t1 and diagnostic.related_events == [acquisition.id, wait.id]


def test_delayed_close_keeps_captured_streams_and_count_precedes_delta():
    tags = ("fb_llp_ttls_id 16,4\nfb_tdc_tdelta_id 16,4\nacquire_timetags 0,0,1,0,4\n"
            "acquire_timetags 0,0,0,1024,4\nfb_tdc_tdelta_id 0,4\nstop")
    states = _run({"tags": tags, "reader": "wait 0\nfb_pop_data 16,R0\nnop\nfb_pop_data 16,R1\nnop\nstop"},
                  inputs={"digital": {"tags": _trace(109 * 128)}},
                  routes=[{"source": "tags", "target": "reader", "id": 16, "latency_ns": 2}])
    assert [(event.t0.value, event.meta["stream"], event.meta["value"])
            for event in _events(states["tags"], "feedback_send")] == [
                (120, "fb_llp_ttls_id", 1), (120, "fb_tdc_tdelta_id", 128),
            ]
    assert (states["reader"].registers["R0"].value, states["reader"].registers["R1"].value) == (1, 128)


def test_unclosed_timetag_gate_excludes_an_edge_at_its_rt_stop():
    tags = _run({"tags": "acquire_timetags 0,0,1,0,4\nstop"},
                inputs={"digital": {"tags": _trace(104 * 128)}})["tags"]
    result, = _results(tags)
    assert result["status"] == "incomplete" and result["count"] == 0
    assert result["end_ticks"] == 104 * 128
    assert not _events(tags, "digital_edge")


def test_cross_channel_uses_latest_closed_reference_on_the_same_module():
    states = _run({"ref": ("acquire_timetags 0,0,1,0,4\nacquire_timetags 0,0,0,0,4\n"
                           "acquire_timetags 0,1,1,0,4\nacquire_timetags 0,1,0,0,4\nstop"),
                   "tags": "acquire_timetags 0,0,1,0,20\nacquire_timetags 0,0,0,0,4\nstop",
                   "other": "acquire_timetags 0,0,1,0,16\nacquire_timetags 0,0,0,0,4\nstop"},
                  digital={"ref": {"module": "QTM0", "channel": 2},
                           "tags": {"module": "QTM0", "channel": 4, "time_reference": "first2", "time_source": "last"},
                           "other": {"module": "QTM1", "channel": 2}},
                  inputs={"digital": {"ref": _trace(101 * 128, 109 * 128), "tags": _trace(105 * 128, 110 * 128),
                                      "other": _trace(115 * 128)}})
    result, = _results(states["tags"])
    assert result["valid"] and result["reference_ticks"] == 109 * 128 and result["timetag_ticks"] == 128
    assert result["reference_event_id"] == _results(states["ref"])[1]["input_event_ids"][0]


@pytest.mark.parametrize("reference_first", [False, True])
def test_coincident_cross_channel_closes_do_not_choose_a_reference_by_dispatch_order(reference_first):
    program = "acquire_timetags 0,0,1,0,4\nacquire_timetags 0,0,0,0,4\nstop"
    order = ("ref", "tags") if reference_first else ("tags", "ref")
    states = _run(dict.fromkeys(order, program),
                  digital={"ref": {"module": "QTM0", "channel": 2},
                           "tags": {"module": "QTM0", "channel": 4, "time_reference": "first2", "invalid_time_delta": "record_0"}},
                  inputs={"digital": {"ref": _trace(101 * 128), "tags": _trace(102 * 128)}})
    result, = _results(states["tags"])
    assert result["count"] == 1 and not result["valid"] and result["timetag_ticks"] is None
    assert "closed at the same tick" in result["timetag_reason"]


@pytest.mark.parametrize("policy,recorded,status,execution", [
    ("record_0", 0, "modeled", "completed"),
    ("discard", None, "discarded", "completed"),
    ("error", None, "invalid", "error"),
])
def test_observed_missing_second_event_applies_explicit_result_policy(policy, recorded, status, execution):
    program = "acquire_timetags 0,0,1,0,12\nacquire_timetags 0,0,0,0,4\nstop"
    tags = _run({"tags": program}, digital={"tags": {"time_source": "second", "invalid_time_delta": policy}},
                inputs={"digital": {"tags": _trace(101 * 128)}})["tags"]
    result, = _results(tags)
    assert result["status"] == status and result["timetag_ticks"] == recorded
    assert result["count"] == 1 and not result["valid"]
    assert result["invalid_time_delta"] == policy
    assert tags.metadata["execution"]["status"] == execution


def test_invalid_zero_record_does_not_invent_a_linq_word_or_missing_input_result():
    program = "fb_tdc_tdelta_id 16,4\nacquire_timetags 0,0,1,0,8\nacquire_timetags 0,0,0,0,4\nstop"
    options = {"digital": {"tags": {"time_source": "second", "invalid_time_delta": "record_0"}}}
    observed = _run({"tags": program}, **options, inputs={"digital": {"tags": _trace(106 * 128)}})["tags"]
    missing = _run({"tags": program}, **options)["tags"]
    assert _results(observed)[0]["timetag_ticks"] == 0 and _results(observed)[0]["valid"] is False
    assert _results(missing)[0]["status"] == "unavailable" and _results(missing)[0].get("timetag_ticks") is None
    for tags in (observed, missing):
        assert tags.metadata["execution"]["status"] == "blocked"
        assert "wire behavior is unverified" in tags.metadata["execution"]["reason"]
        assert not _events(tags, "feedback_send")


def test_id_zero_keeps_local_delta_without_wire_traffic():
    tags = _run({"tags": "fb_tdc_tdelta_id 0,4\nacquire_timetags 0,0,1,0,8\nacquire_timetags 0,0,0,0,4\nstop"},
                inputs={"digital": {"tags": _trace(106 * 128)}})["tags"]
    assert _results(tags)[0]["timetag_ticks"] == 256
    assert not _events(tags, "feedback_send")


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize(("origin", "fraction", "reason"), [
    (None, 0, "require an explicit linq_timestamp_origin_ticks"),
    (0, 1, "requires a scheduling quantization vector"),
    (0, 0, None),
])
def test_qtm_edge_stream_rejection_closes_active_rt_and_retains_edge(prepared, origin, fraction, reason):
    source = "fb_llp_tags_id 16,4\nacquire_timetags 0,0,1,0,4\nwait 100\nacquire_timetags 0,0,0,0,4\nstop"
    states = _run({"tags": source, "peer": "wait 150\nstop"}, prepared=prepared,
                  digital={"tags": {"linq_timestamp_origin_ticks": origin}} if origin is not None else {},
                  inputs={"digital": {"tags": _trace(120 * 128 + fraction)}},
                  routes=[{"source": "tags", "target": "peer", "id": 16, "latency_ns": 5}])
    tags, peer = states["tags"], states["peer"]
    result, = _results(tags)
    wait = tags.rt_packets[2]
    edge = 120 + fraction / 128
    assert wait.rt_t0.value == 108 and result["edge_times_ticks"] == [120 * 128 + fraction]
    assert peer.metadata["execution"]["status"] == "completed" and peer.rt_time_ns.value == 250
    if reason is None:
        assert tags.metadata["execution"]["status"] == "completed" and wait.rt_t1.value == 208
        assert not tags.diagnostics and "observation_cutoff_ns" not in wait.meta
        if not prepared:
            send, = _events(tags, "feedback_send")
            receive, = _events(peer, "feedback_receive")
            assert send.meta["value"] == 120 and receive.t0.value == 125 and receive.meta["source_event_id"] == send.id
        return
    diagnostic, = tags.diagnostics
    assert tags.metadata["execution"]["status"] == "blocked" and reason in diagnostic.message
    assert diagnostic.source.line == 2 and diagnostic.category == "analysis_incomplete"
    assert wait.rt_t1.value == tags.rt_time_ns.value == edge
    assert wait.meta["planned_rt_end_ns"] == 208 and wait.meta["observation_cutoff_ns"] == edge
    assert result["status"] == "incomplete" and result["end_ticks"] == 120 * 128 + fraction and result["count"] == 1
    assert not _events(tags, "feedback_send") and not _events(peer, "feedback_receive")
    if prepared:
        assert not diagnostic.related_events
    else:
        observed, = _events(tags, "digital_edge")
        clipped, = _events(tags, "wait")
        assert observed.t0.value == clipped.t1.value == edge
        assert diagnostic.related_events == [observed.id, clipped.id]


@pytest.mark.parametrize("prepared", [False, True])
def test_traced_qtm_windows_accumulate_per_bin_without_changing_window_results(prepared):
    source = ("acquire_timetags 0,0,1,0,100\nacquire_timetags 0,0,0,0,4\n"
              "acquire_timetags 0,0,1,0,160\nacquire_timetags 0,0,0,0,4\n"
              "acquire_timetags 0,1,1,0,8\nacquire_timetags 0,1,0,0,4\nstop")
    tags = _run({"tags": source}, prepared=prepared,
                digital={"tags": {"count_thresholds": {"low": 2, "high": 3}}},
                inputs={"digital": {"tags": _trace(*(time * 128 for time in (120, 228, 273, 318, 370)))}})["tags"]
    assert tags.metadata["execution"]["status"] == "completed"
    first, second, third = _results(tags)
    assert [(row["count"], row["timetag_ticks"], row["classification"]) for row in (first, second, third)] == [
        (1, 2560, "low"), (3, 3072, "high"), (1, 256, "low"),
    ]
    assert all("avg_cnt" not in row for row in (first, second, third))
    aggregates = [row["bin_aggregate"] for row in (first, second, third)]
    assert aggregates == [
        {"avg_cnt": 1, "count": 1, "count_sum": 1, "timetag_ticks": 2560, "timetag_ticks_sum": 2560, "valid": True},
        {"avg_cnt": 2, "count": 2, "count_sum": 4, "timetag_ticks": 2816, "timetag_ticks_sum": 5632, "valid": True},
        {"avg_cnt": 1, "count": 1, "count_sum": 1, "timetag_ticks": 256, "timetag_ticks_sum": 256, "valid": True},
    ]
    repeated, single = tags.metadata["simulation"]["digital_bins"]
    assert (repeated["bin_index"], repeated["avg_cnt"], repeated["count_sum"], repeated["count"]) == (0, 2, 4, 2)
    assert (repeated["timetag_ticks_sum"], repeated["timetag_ticks"], repeated["valid"]) == (5632, 2816, True)
    assert repeated["event_id"] == second["event_id"] and repeated["classification"] == "high"
    assert (single["bin_index"], single["avg_cnt"], single["count_sum"], single["count"]) == (1, 1, 1, 1)
    assert (single["timetag_ticks_sum"], single["timetag_ticks"]) == (256, 256)
    if not prepared:
        results = _events(tags, "acquisition_result")
        assert [(event.meta["count"], event.meta["timetag_ticks"]) for event in results] == [(1, 2560), (3, 3072), (1, 256)]
        assert [event.meta["bin_aggregate"] for event in results] == aggregates


def test_supplied_qtm_bins_allow_fractional_means_and_keep_individual_linq_words():
    window = "acquire_timetags 0,0,1,0,8\nacquire_timetags 0,0,0,0,4\n"
    source = "fb_llp_ttls_id 16,4\nfb_tdc_tdelta_id 16,4\n" + window * 2 + "stop"
    supplied = [{"sequencer": "tags", "kind": "acquire_timetags", "acquisition_index": 0, "bin_index": 0,
                 "count": count, "timetag_ticks": delta} for count, delta in ((1, -8), (2, 7))]
    tags = _run({"tags": source}, inputs={"digital_acquisitions": supplied})["tags"]
    assert tags.metadata["execution"]["status"] == "completed"
    summary, = tags.metadata["simulation"]["digital_bins"]
    assert (summary["avg_cnt"], summary["count_sum"], summary["count"]) == (2, 3, 1.5)
    assert (summary["timetag_ticks_sum"], summary["timetag_ticks"]) == (-1, -0.5)
    first, second = _results(tags)
    assert [(row["count"], row["timetag_ticks"]) for row in (first, second)] == [(1, -8), (2, 7)]
    assert [(event.meta["source_event_id"], event.meta["value"]) for event in _events(tags, "feedback_send")] == [
        (first["event_id"], 1), (first["event_id"], 0xFFFFFFF8), (second["event_id"], 2), (second["event_id"], 7),
    ]


@pytest.mark.parametrize(("first", "count_sum", "delta_sum"), [
    ({"count": 1}, 4, None),
    ({"count_valid": False, "timetag_ticks": 7}, None, 15),
])
def test_qtm_bin_unknown_field_stays_unavailable_after_later_known_write(first, count_sum, delta_sum):
    window = "acquire_timetags 0,0,1,0,8\nacquire_timetags 0,0,0,0,4\n"
    supplied = [{"sequencer": "tags", "kind": "acquire_timetags", "acquisition_index": 0, "bin_index": 0, **values}
                for values in (first, {"count": 3, "count_valid": True, "timetag_ticks": 8})]
    tags = _run({"tags": window * 2 + "stop"}, inputs={"digital_acquisitions": supplied})["tags"]
    summary, = tags.metadata["simulation"]["digital_bins"]
    assert summary["avg_cnt"] == 2 and _results(tags)[-1]["valid"] is True
    assert (summary["count_sum"], summary["timetag_ticks_sum"]) == (count_sum, delta_sum)
    assert summary["count"] == (None if count_sum is None else 2)
    assert summary["timetag_ticks"] == (None if delta_sum is None else 7.5)
    assert summary["valid"] is (delta_sum is not None)
    assert summary["count_valid"] is (count_sum is not None)
    aggregate = _results(tags)[-1]["bin_aggregate"]
    assert aggregate == {field: summary[field] for field in aggregate}


@pytest.mark.parametrize(("policy", "result_status", "execution_status"), [
    ("record_0", "supplied", "completed"), ("discard", "discarded", "completed"), ("error", "invalid", "error"),
])
def test_qtm_bin_includes_recorded_zero_and_excludes_discarded_or_invalid_writes(policy, result_status, execution_status):
    window = "acquire_timetags 0,0,1,0,8\nacquire_timetags 0,0,0,0,4\n"
    supplied = [{"sequencer": "tags", "kind": "acquire_timetags", "acquisition_index": 0, "bin_index": 0, **values}
                for values in ({"count": 1, "timetag_ticks": 9}, {"count": 3})]
    tags = _run({"tags": window * 2 + "stop"}, digital={"tags": {"invalid_time_delta": policy}},
                inputs={"digital_acquisitions": supplied})["tags"]
    first, second = _results(tags)
    summary, = tags.metadata["simulation"]["digital_bins"]
    assert tags.metadata["execution"]["status"] == execution_status and second["status"] == result_status
    assert summary["timetag_ticks_sum"] == 9 and not second["valid"]
    if policy == "record_0":
        assert second["timetag_ticks"] == 0 and summary["timetag_ticks"] == 4.5 and not summary["valid"]
        assert (summary["avg_cnt"], summary["count_sum"], summary["count"]) == (2, 4, 2)
        assert summary["event_id"] == second["event_id"]
        assert second["bin_aggregate"]["timetag_ticks"] == 4.5 and not second["bin_aggregate"]["valid"]
    else:
        assert summary["timetag_ticks"] == 9 and summary["valid"]
        assert (summary["avg_cnt"], summary["count_sum"], summary["count"]) == (1, 1, 1)
        assert summary["event_id"] == first["event_id"]
        assert "bin_aggregate" not in second and "bin_aggregate" not in _events(tags, "acquisition_result")[-1].meta
    assert first["bin_aggregate"]["avg_cnt"] == 1 and first["bin_aggregate"]["timetag_ticks"] == 9


@pytest.mark.parametrize("settings,reason", [
    ({"tags": {"time_reference": "first4"}}, "module"),
    ({"tags": {"time_reference": "first4", "module": "QTM0", "channel": 8}}, "channel"),
    ({"tags": {"module": "QTM0", "channel": 4}, "ref": {"module": "QTM0", "channel": 4}}, "duplicates"),
    ({"tags": {"time_source": "third"}}, "time_source"),
    ({"tags": {"invalid_time_delta": "zero"}}, "invalid_time_delta"),
])
def test_qtm_reference_settings_validation(settings, reason):
    with pytest.raises(ValueError, match=reason):
        validate_simulation({"digital": settings}, ["tags", "ref"])
