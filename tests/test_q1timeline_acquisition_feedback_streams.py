import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.q1asm.parser import parse_q1asm


_READOUT_CONFIG = "fb_acq_tb_id 5,4\nfb_acq_iq_id 6,4\n"
_READOUT = "acquire 0,0,4\nwait 1200\n"


def _analyze(source, *, integration=80, **options):
    state = interpret_program(parse_q1asm(source, file="readout.q1asm"), sequencer_id="readout", **options)
    if integration is not None:
        state.metadata["hardware_parameters"] = {"integration_length_acq": integration}
    return timeline_ir_from_states([state])


def _pop(*channels):
    return "".join(f"fb_pop_data {channel},R{index}\n" for index, channel in enumerate(channels)) + "stop"


def _acquisitions(ir):
    return [event for event in ir["events"] if event["kind"] == "acquire"]


def _streams(event):
    meta = event["meta"]
    return meta.get("feedback_streams") or ([meta["feedback"]] if "feedback" in meta else [])


@pytest.mark.parametrize("config", [_READOUT_CONFIG, "fb_cmd 0x41,6,4\nfb_cmd 0x40,5,4\n"])
def test_one_acquisition_emits_tb_and_ordered_iq_with_independent_arrivals(config):
    ir = _analyze(config + _READOUT + _pop(5, 6, 6))
    acquire, = _acquisitions(ir)
    assert acquire["t0"]["value"] == 8 and acquire["t1"]["value"] == 12
    assert [(stream["channel"], stream["data_type"]) for stream in _streams(acquire)] == [
        ("5", "thresholded_bits"), ("6", "iq_values"),
    ]
    assert _streams(acquire)[1]["payload_words"] == ["I", "Q"]
    flows = ir["feedback_flows"]
    assert all(flow["from_event_id"] == acquire["id"] and flow["send_available_ns"] == 88 for flow in flows)
    assert [(flow["channel"], flow["timing"]["arrival_ns"], flow.get("payload_word")) for flow in flows] == [
        ("5", 248, None), ("6", 252, "I"), ("6", 252, "Q"),
    ]
    balance = ir["feedback_balance"]
    assert balance["status"] == "balanced"
    assert {channel: (item["sends"], item["send_payloads"], item["matched"])
            for channel, item in balance["channels"].items()} == {"5": (1, 1, 1), "6": (1, 2, 2)}


@pytest.mark.parametrize("command,channels", [
    ("fb_acq_iq_id 0,4", (5,)), ("fb_acq_tb_id 0,4", (6, 6)),
    ("fb_acq_tb_id 7,4", (7, 6, 6)), ("fb_acq_iq_id 8,4", (5, 8, 8)),
    ("fb_acq_tb_valid 0,4", (5, 6, 6)), ("fb_cmd 0x5b,1,4", (5, 6, 6)),
])
def test_retag_disable_and_tb_validation_change_only_the_addressed_setting(command, channels):
    ir = _analyze(_READOUT_CONFIG + command + "\n" + _READOUT + _pop(*channels))
    acquire, = _acquisitions(ir)
    assert [stream["channel"] for stream in _streams(acquire)] == list(dict.fromkeys(map(str, channels)))
    assert [flow["channel"] for flow in ir["feedback_flows"]] == list(map(str, channels))
    assert ir["feedback_balance"]["status"] == "balanced"
    if len(set(channels)) == 1:
        assert "feedback" in acquire["meta"] and "feedback_streams" not in acquire["meta"]


def test_shared_id_can_hold_tb_and_both_iq_words_without_dropping_any():
    ir = _analyze(_READOUT_CONFIG.replace("6,4", "5,4") + _READOUT + _pop(5, 5, 5))
    assert [(flow["timing"]["data_type"], flow.get("payload_word")) for flow in ir["feedback_flows"]] == [
        ("thresholded_bits", None), ("iq_values", "I"), ("iq_values", "Q"),
    ]
    channel = ir["feedback_balance"]["channels"]["5"]
    assert (channel["status"], channel["sends"], channel["send_payloads"], channel["matched"]) == ("balanced", 2, 3, 3)


def test_configuration_after_an_acquire_changes_only_later_acquisitions():
    source = (_READOUT_CONFIG + "acquire 0,0,4\nfb_acq_iq_id 0,4\nwait 1200\n"
              + _pop(5, 6, 6).removesuffix("stop") + _READOUT + _pop(5))
    ir = _analyze(source)
    first, second = _acquisitions(ir)
    assert [stream["channel"] for stream in _streams(first)] == ["5", "6"]
    assert [stream["channel"] for stream in _streams(second)] == ["5"]
    assert [flow["from_event_id"] for flow in ir["feedback_flows"]] == [first["id"]] * 3 + [second["id"]]
    assert ir["feedback_balance"]["status"] == "balanced"


def test_qtm_open_and_close_streams_are_independent_even_when_tag_order_is_unknown():
    source = (_READOUT_CONFIG + "fb_llp_tags_id 10,4\nfb_tdc_tags_id 11,4\nfb_llp_ttls_id 12,4\nfb_tdc_tdelta_id 13,4\n"
              "acquire_timetags 0,0,1,0,4\nwait 80\nacquire_timetags 0,1,0,0,4\nwait 1500\n")
    ir = _analyze(source + _pop(10, 11, 12, 13))
    opening, closing = _acquisitions(ir)
    assert [stream["data_type"] for stream in _streams(opening)] == ["low_latency_time_tags", "high_resolution_time_tags"]
    assert [stream["data_type"] for stream in _streams(closing)] == ["ttl_counts", "time_delta"]
    flow, = ir["feedback_flows"]
    assert flow["from_event_id"] == opening["id"] and flow["timing"]["arrival_ns"] is None
    assert ir["feedback_balance"]["status"] == "incomplete"


@pytest.mark.parametrize("fine_delay", [0, 128])
def test_qtm_count_and_delta_share_close_source_and_preserve_relative_order(fine_delay):
    source = ("fb_llp_ttls_id 12,4\nfb_tdc_tdelta_id 13,4\nacquire_timetags 0,0,1,0,4\nwait 80\n"
              f"acquire_timetags 0,1,0,{fine_delay},4\nwait 1500\n")
    ir = _analyze(source + _pop(12, 13))
    opening, closing = _acquisitions(ir)
    assert not _streams(opening)
    assert [stream["data_type"] for stream in _streams(closing)] == ["ttl_counts", "time_delta"]
    assert [flow["from_event_id"] for flow in ir["feedback_flows"]] == [closing["id"]] * 2
    assert [flow["timing"]["arrival_ns"] for flow in ir["feedback_flows"]] == (
        [238, 1002] if not fine_delay else [None] * 2)
    assert ir["feedback_balance"]["status"] == "balanced"


@pytest.mark.parametrize("source", ["acquire_digital 0,0,4", "acquire_ttl 0,0,1,4", "acquire 0,0,4"])
def test_qtm_streams_do_not_attach_to_other_acquisitions(source):
    ir = _analyze("fb_tdc_tags_id 5,4\nfb_llp_ttls_id 6,4\n" + source + "\nstop")
    event, = _acquisitions(ir)
    assert not _streams(event) and ir["feedback_balance"]["channels"] == {}


def test_compared_branch_arms_cannot_overwrite_other_stream_ids():
    source = (_READOUT_CONFIG + "jlt R0,1,@taken\nfb_acq_tb_id 7,4\n" + _READOUT + _pop(7, 6, 6)
              + "\ntaken: fb_acq_iq_id 0,4\n" + _READOUT + _pop(5))
    ir = _analyze(source, branch_policy="explore_both_with_depth_limit")
    by_path = {event["meta"]["branch_comparison_path"]: event for event in _acquisitions(ir)}
    assert {path: [stream["channel"] for stream in _streams(event)] for path, event in by_path.items()} == {
        "taken": ["5"], "fallthrough": ["7", "6"],
    }
    assert all(item["status"] == "balanced" for item in ir["feedback_balance"]["alternatives"])
    assert all(flow["timing"]["arrival_ns"] is None for flow in ir["feedback_flows"])


def test_without_integration_evidence_both_readout_stream_times_remain_unknown():
    ir = _analyze(_READOUT_CONFIG + _READOUT + _pop(5, 6, 6), integration=None)
    assert [flow["timing"]["arrival_ns"] for flow in ir["feedback_flows"]] == [None] * 3
    assert ir["feedback_balance"]["status"] == "balanced"
