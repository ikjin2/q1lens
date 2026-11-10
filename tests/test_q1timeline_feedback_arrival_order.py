import pytest

from q1timeline.analysis.api import analyze_text
from q1timeline.analysis.interpreter import interpret_program
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.q1asm.parser import parse_q1asm


_IQ = "fb_acq_iq_id 5,4\nacquire 0,0,4\n"
_COMMON = "fb_com_data 6,7,4\n"
_PULL = "fb_pull_data R0,R1\n"
_POP_IQ = "fb_pop_data 5,R2\n"
_POP_COMMON = "fb_pop_data 6,R3\n"


def _state(source, sequencer="readout", integration=80):
    state = interpret_program(parse_q1asm(source + "stop", file=f"{sequencer}.q1asm"), sequencer_id=sequencer)
    if integration is not None:
        state.metadata["hardware_parameters"] = {"integration_length_acq": integration}
    return state


def _analyze(source, *, integration=80):
    return timeline_ir_from_states([_state(source, integration=integration)])


def _flow_order(ir):
    return [(flow["channel"], flow.get("payload_word"), flow["timing"]["arrival_ns"]) for flow in ir["feedback_flows"]]


def _ordering_issues(ir):
    return [item for item in ir["diagnostics"] if item["details"].get("reason") == "feedback_ordering_unknown"]


@pytest.mark.parametrize("command", ["fb_com_data 0,17,4", "fb_cmd 0x100,17,4", "fb_com_data $ID,17,4"])
def test_disabled_common_data_id_has_packet_timing_without_a_fifo_word(command):
    ir = analyze_text(f"{command}\nfb_com_data 1,42,4\nwait 100\n{_PULL}stop", params={"ID": 0})
    assert _flow_order(ir) == [("1", None, 64)]
    assert list(ir["feedback_balance"]["channels"]) == ["1"]
    assert ir["feedback_balance"]["status"] == "balanced"
    commands = [event for event in ir["events"] if event["kind"] == "feedback_com"]
    assert [event["t0"]["value"] for event in commands] == [0, 4]
    assert commands[0]["source"]["raw"] == command
    assert commands[0]["duration"]["value"] == 4


@pytest.mark.parametrize("receives", [_PULL * 3, _POP_COMMON + _PULL * 2])
def test_common_data_arrives_before_an_earlier_started_iq_acquisition(receives):
    ir = _analyze(_IQ + _COMMON + "wait 400\n" + receives)
    assert _flow_order(ir) == [("6", None, 68), ("5", "I", 248), ("5", "Q", 248)]
    assert ir["feedback_balance"]["status"] == "balanced"
    assert ir["feedback_balance"]["channels"]["5"]["discarded_payloads"] == 0


def test_selective_pop_discards_known_predecessors_by_arrival():
    ir = _analyze(_IQ + _COMMON + "wait 400\n" + _POP_IQ + _PULL)
    assert _flow_order(ir) == [("5", "I", 248), ("5", "Q", 248)]
    assert ir["feedback_balance"]["channels"]["6"]["discarded_payloads"] == 1
    assert ir["feedback_balance"]["status"] == "balanced"


def test_selective_pop_uses_arrival_order_within_one_self_cast_id():
    ir = _analyze(_IQ + _COMMON.replace("6,7", "5,7") + "wait 400\n" + _POP_IQ * 3)
    assert _flow_order(ir) == [("5", None, 68), ("5", "I", 248), ("5", "Q", 248)]
    assert ir["feedback_balance"]["status"] == "balanced"


@pytest.mark.parametrize("sequence_order", [("a", "b"), ("b", "a")])
def test_shared_self_cast_ids_have_separate_arrival_order_per_sequencer(sequence_order):
    program = _IQ + _COMMON + "wait 400\n" + _PULL * 3
    ir = timeline_ir_from_states([_state(program, sequencer=name) for name in sequence_order])
    flows = ir["feedback_flows"]
    for name in sequence_order:
        owned = [flow for flow in flows if flow["to_event_id"].startswith(f"{name}:")]
        assert [flow["channel"] for flow in owned] == ["6", "5", "5"]
        assert all(flow["from_event_id"].startswith(f"{name}:") for flow in owned)
    assert ir["feedback_balance"]["status"] == "balanced"


@pytest.mark.parametrize("receive,channel,arrival", [(_PULL, "6", 68), (_POP_IQ, "5", 248)])
def test_early_receives_keep_a_known_future_source_and_latency_warning(receive, channel, arrival):
    ir = _analyze(_IQ + _COMMON + receive)
    assert _flow_order(ir) == [(channel, "I" if channel == "5" else None, arrival)]
    warning, = [item for item in ir["diagnostics"] if item["category"] == "feedback_latency_violation"]
    assert warning["details"]["arrival_ns"] == arrival
    assert not _ordering_issues(ir)


@pytest.mark.parametrize("integration,spacing", [(None, "wait 120\n"), (80, "wait 180\n")])
def test_unknown_and_tied_arrivals_do_not_claim_a_fifo_source(integration, spacing):
    ir = _analyze(_IQ + spacing + _COMMON + "wait 400\n" + _PULL * 2, integration=integration)
    assert not ir["feedback_flows"]
    first, second = _ordering_issues(ir)
    assert first["details"]["match_unknown"] and second["details"]["match_unknown"]
    assert first["details"]["channels"] == ["5", "6"]
    balance = ir["feedback_balance"]
    assert balance["status"] == "incomplete"
    assert balance["channels"]["fifo"]["ordering_unknown_receives"] == 2
    assert balance["channels"]["fifo"]["unmatched_receives"] == 0
    assert all(balance["channels"][channel]["unconsumed_payloads"] is None for channel in ("5", "6"))
    assert not [item for item in ir["diagnostics"] if item["category"] == "feedback_fifo_imbalance"]


@pytest.mark.parametrize("integration,spacing", [(None, "wait 120\n"), (80, "wait 180\n")])
def test_selective_pop_preserves_a_unique_target_while_prior_discards_are_uncertain(integration, spacing):
    ir = _analyze(_IQ + spacing + _COMMON + "wait 400\n" + _POP_COMMON + _PULL, integration=integration)
    flow, = ir["feedback_flows"]
    assert flow["channel"] == "6" and flow["timing"]["arrival_ns"] is not None
    discard, subsequent = _ordering_issues(ir)
    assert not discard["details"]["match_unknown"] and subsequent["details"]["match_unknown"]
    assert discard["details"]["channels"] == ["5"]
    assert ir["feedback_balance"]["channels"]["5"]["discarded_payloads"] == 0
    assert ir["feedback_balance"]["channels"]["6"]["status"] == "balanced"


def test_one_unknown_iq_source_keeps_both_ordered_words_and_unknown_arrival_diagnostics():
    ir = _analyze(_IQ + "wait 400\n" + _PULL * 2, integration=None)
    assert _flow_order(ir) == [("5", "I", None), ("5", "Q", None)]
    assert ir["feedback_balance"]["status"] == "balanced"
    assert not _ordering_issues(ir)
    assert sum(item["details"].get("reason") == "feedback_timing_unknown" for item in ir["diagnostics"]) == 2


@pytest.mark.parametrize("receive", [_PULL, _POP_IQ])
@pytest.mark.parametrize("offset,integration", [("wait_trigger 1,4", None), ("wait R9", 80)])
def test_sequential_iq_acquisitions_keep_source_order_with_unknown_arrivals(receive, offset, integration):
    source = (f"fb_acq_iq_id 5,4\n{offset}\nacquire 0,0,4\n"
              "acquire 0,1,4\nwait 400\n" + receive * 4)
    ir = _analyze(source, integration=integration)
    first, second = [event for event in ir["events"] if event["kind"] == "acquire"]
    assert _flow_order(ir) == [("5", "I", None), ("5", "Q", None)] * 2
    assert [flow["from_event_id"] for flow in ir["feedback_flows"]] == [first["id"]] * 2 + [second["id"]] * 2
    assert ir["feedback_balance"]["status"] == "balanced"
    assert not _ordering_issues(ir)


def test_selective_pop_discards_previous_retagged_iq_without_an_absolute_arrival():
    source = ("fb_acq_iq_id 5,4\nwait_trigger 1,4\nacquire 0,0,4\nfb_acq_iq_id 6,4\n"
              "acquire 0,1,4\nwait 400\n" + _POP_COMMON + _PULL)
    ir = _analyze(source, integration=None)
    assert _flow_order(ir) == [("6", "I", None), ("6", "Q", None)]
    assert ir["feedback_balance"]["channels"]["5"]["discarded_payloads"] == 2
    assert ir["feedback_balance"]["status"] == "balanced"


def test_separate_readout_types_do_not_infer_arrival_order_from_acquisition_order():
    source = ("fb_acq_iq_id 5,4\nwait_trigger 1,4\nacquire 0,0,4\nfb_acq_iq_id 0,4\n"
              "fb_acq_tb_id 6,4\nacquire 0,1,4\nwait 400\n" + _PULL)
    ir = _analyze(source, integration=None)
    assert not ir["feedback_flows"]
    issue, = _ordering_issues(ir)
    assert issue["details"]["channels"] == ["5", "6"] and issue["details"]["match_unknown"]


def test_inferred_non_self_source_order_is_unchanged_when_it_competes_with_self_cast():
    ir = analyze_text("fb_com_data 16,11,4\nfb_com_data 5,22,4\nwait 700\n" + _PULL * 2 + "stop")
    assert _flow_order(ir) == [("16", None, 580), ("5", None, 64)]
    assert ir["feedback_balance"]["status"] == "balanced"
