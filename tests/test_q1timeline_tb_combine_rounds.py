import pytest

from q1timeline.analysis.interpreter import prepare_def_program
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


_A = "fb_acq_tb_id 16,4\nfb_acq_tb_cfg 1,0,1,4\nacquire 0,0,16\nstop"
_B = "fb_acq_tb_id 16,4\nfb_acq_tb_cfg 1,2,1,4\nacquire 0,0,12\nfb_acq_tb_id 0,4\nacquire 0,1,4\nstop"
_POP = "wait 0\nfb_pop_data 16,R0\nnop\nstop"


def _run(sources, *, latency=5, prepared=False, lengths=None, second_bin=False):
    programs = {name: parse_q1asm(text, file=f"{name}.q1asm") for name, text in sources.items()}
    bindings = ({name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                 for name, program in programs.items()} if prepared else None)
    senders = [name for name in sources if name not in {"rx", "peer"}]
    inputs = [{"sequencer": name, "acquisition_index": 0, "bin_index": 0, "thresholded": 1} for name in senders]
    if second_bin:
        inputs.append({"sequencer": "b", "acquisition_index": 0, "bin_index": 1, "thresholded": 1})
    states = simulate_programs(programs, prepared_programs=bindings,
                               hardware_parameters={name: {"integration_length_acq": length} for name, length in (lengths or {}).items()},
                               simulation={"startup_delay_ns": 100, "feedback_queue_capacity": 1,
                                           "inputs": {"acquisitions": inputs},
                                           "routes": [{"source": name, "target": "rx", "id": 16, "latency_ns": latency} for name in senders]})
    return {state.sequencer_id: state for state in states}


def _events(state, kind):
    return [event for event in state.events if event.kind == kind]


def _status(state):
    return state.metadata["execution"]["status"]


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("order", [("a", "b", "rx"), ("rx", "b", "a")])
@pytest.mark.parametrize("latency", [0, 5])
def test_same_tick_completion_and_rt_interruption_share_one_round(prepared, order, latency):
    programs = {"a": _A, "b": _B, "rx": _POP}
    states = _run({name: programs[name] for name in order}, latency=latency, prepared=prepared,
                  lengths={"a": 16, "b": 64}, second_bin=True)
    assert all(_status(state) == "completed" for state in states.values())
    assert states["rx"].registers["R0"] == Concrete(0x0F)
    a, = states["a"].metadata["simulation"]["acquisitions"]
    b, _ = states["b"].metadata["simulation"]["acquisitions"]
    assert a["time_ns"] == b["time_ns"] == 124 and not a["interrupted"] and b["interrupted"]
    if not prepared:
        received, = _events(states["rx"], "feedback_receive")
        assert received.t0 == Concrete(124 + latency) and received.meta["value"] == 0x0F
        provenance = {item["sequencer"]: item for item in received.meta["contributors"]}
        assert set(provenance) == {"a", "b"}
        for name, result in (("a", a), ("b", b)):
            contributor = provenance[name]
            assert contributor["acquisition_event_id"] == result["event_id"]
            assert contributor["rt_packet_id"] == result["rt_packet_id"]
            assert contributor["source_event_id"] == _events(states[name], "feedback_contribution")[0].id


@pytest.mark.parametrize("prepared", [False, True])
def test_later_rt_interruption_stays_in_a_separate_round(prepared):
    rx = "wait 0\nfb_pop_data 16,R0\nfb_pop_data 16,R1\nnop\nstop"
    states = _run({"a": _A, "b": _B.replace("0,0,12", "0,0,16"), "rx": rx}, prepared=prepared,
                  lengths={"a": 16, "b": 64}, second_bin=True)
    assert all(_status(state) == "completed" for state in states.values())
    assert states["rx"].registers == {"R0": Concrete(3), "R1": Concrete(12)}
    if not prepared:
        assert [event.t0.value for event in _events(states["rx"], "feedback_receive")] == [129, 133]


def _odd_configuration(form, enabled):
    value = 0x10100 | enabled
    if form == "named":
        return "", f"fb_acq_tb_cfg {enabled},1,1,4"
    if form == "packed":
        return f"move {value},R1\nnop\n", "fb_acq_tb_cfg R1,4"
    return "", f"fb_cmd 0x42,{value},4"


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("form", ["named", "packed", "raw"])
def test_enabled_odd_bit_position_is_rejected_when_configuring_rt(prepared, form):
    setup, command = _odd_configuration(form, enabled=1)
    source = setup + f"fb_acq_tb_id 16,4\n{command}\nacquire 0,0,20\nstop"
    states = _run({"a": source, "rx": _POP}, prepared=prepared)
    assert _status(states["a"]) == "blocked"
    execution = states["a"].metadata["execution"]
    assert "even bit position" in execution["reason"] and execution["source"]["raw"].strip() == command
    assert not states["a"].metadata["simulation"]["acquisitions"]


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("form", ["named", "packed", "raw"])
@pytest.mark.parametrize("skipped", [False, True])
def test_disabled_and_skipped_odd_configuration_do_not_change_tb_delivery(prepared, form, skipped):
    setup, command = _odd_configuration(form, enabled=int(skipped))
    source = setup + "fb_acq_tb_id 16,4\n"
    if skipped:
        source += "fb_acq_tb_cfg 1,2,1,4\nset_cond 1,0,0,4\n"
    source += command + "\n"
    if skipped:
        source += "set_cond 0,0,0,4\n"
    source += "acquire 0,0,20\nstop"
    states = _run({"a": source, "rx": _POP}, prepared=prepared)
    assert all(_status(state) == "completed" for state in states.values())
    assert states["rx"].registers["R0"] == Concrete(12 if skipped else 3)


def test_completed_contribution_survives_later_same_tick_sender_error():
    source = _A.replace("stop", "fb_acq_tb_cfg 1,1,1,4\nstop")
    states = _run({"a": source, "b": _B, "rx": _POP}, lengths={"a": 16, "b": 64}, second_bin=True)
    assert _status(states["a"]) == "blocked" and _status(states["rx"]) == "completed"
    assert states["rx"].registers["R0"] == Concrete(0x0F)


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("order", [("a", "b", "rx", "peer"), ("peer", "rx", "b", "a")])
@pytest.mark.parametrize("shift", [0, 2])
def test_tb_round_refusal_closes_local_spans_and_links_ordered_contributions(prepared, order, shift):
    sender = "fb_acq_tb_id 16,4\nfb_acq_tb_cfg 1,0,1,4\nacquire 0,0,8\nwait 100\nstop"
    sources = {"a": sender, "b": sender.replace("1,0,1,4", f"1,{shift},1,4"),
               "rx": "wait 100\nstop", "peer": "wait 120\nstop"}
    states = _run({name: sources[name] for name in order}, prepared=prepared)
    assert _status(states["peer"]) == "completed" and states["peer"].rt_time_ns == Concrete(220)
    if shift:
        assert all(_status(state) == "completed" for state in states.values())
        assert states["rx"].rt_time_ns == Concrete(200)
        if not prepared:
            received, = _events(states["rx"], "feedback_receive")
            assert received.t0 == Concrete(121) and received.meta["value"] == 15
        return
    contribution_ids = []
    for name in ("a", "b"):
        result, = states[name].metadata["simulation"]["acquisitions"]
        assert result["thresholded"] == 1 and result["time_ns"] == 116
        if not prepared:
            contribution, = _events(states[name], "feedback_contribution")
            contribution_ids.append(contribution.id)
            assert contribution.meta["source_event_id"] == result["event_id"]
    for name in ("a", "b", "rx"):
        state = states[name]
        diagnostic, = state.diagnostics
        wait = state.rt_packets[-1]
        assert _status(state) == "error" and diagnostic.category == "feedback_write_combine"
        assert (diagnostic.source.file, diagnostic.source.line) == (f"{name}.q1asm", 1 if name == "rx" else 3)
        assert wait.rt_t0 == Concrete(100 if name == "rx" else 116)
        assert state.rt_time_ns == wait.rt_t1 == Concrete(116)
        assert wait.meta["planned_rt_end_ns"] == (200 if name == "rx" else 216)
        assert wait.meta["observation_cutoff_ns"] == 116 and "interrupted_by_fault" not in wait.meta
        assert not _events(state, "feedback_send") and not _events(state, "feedback_receive")
        if prepared:
            assert not diagnostic.related_events
        else:
            span, = _events(state, "wait")
            assert span.t1 == wait.rt_t1 and diagnostic.related_events == [*contribution_ids, span.id]


def test_tb_round_refusal_keeps_an_already_blocked_contributors_diagnostic():
    first = _A.replace("stop", "fb_acq_tb_cfg 1,1,1,4\nstop")
    second = _B.replace("1,2,1,4", "1,0,1,4")
    states = _run({"a": first, "b": second, "rx": "wait 100\nstop"}, lengths={"a": 16, "b": 64}, second_bin=True)
    a, b, rx = (states[name] for name in ("a", "b", "rx"))
    prior, = a.diagnostics
    refused = _events(a, "fb_acq_tb_cfg")[-1]
    assert _status(a) == "blocked" and prior.category == "analysis_incomplete" and "even bit position" in prior.message
    assert prior.source.line == 4 and prior.related_events == [refused.id]
    contributing = [_events(state, "feedback_contribution")[0].id for state in (a, b)]
    assert _status(b) == _status(rx) == "error"
    assert rx.rt_time_ns == Concrete(124) and not _events(rx, "feedback_receive")
    assert rx.diagnostics[-1].related_events == [*contributing, _events(rx, "wait")[0].id]
    assert b.diagnostics[-1].related_events[:2] == contributing
