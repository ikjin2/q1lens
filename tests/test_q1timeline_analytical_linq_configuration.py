import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.ir.serialize import _feedback_entries, timeline_ir_from_states
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.render.html import render_html


_COMMON = "wait 8\n{}\nfb_com_data 1,0x12345678,4\nwait 100\nfb_pop_data 1,R0\nstop"


def _analyze(source, *, integration=None, **options):
    state = interpret_program(parse_q1asm(source, file="config.q1asm"), sequencer_id="config", **options)
    if integration is not None:
        state.metadata["hardware_parameters"] = {"integration_length_acq": integration}
    return timeline_ir_from_states([state])


def _configurations(ir):
    return [event for event in ir["events"] if "linq_configuration" in event["meta"]]


def _configuration_diagnostics(ir):
    return [item for item in ir["diagnostics"] if item["details"].get("reason") == "linq_configuration_unknown"]


@pytest.mark.parametrize("prefix,config,op", [
    ("", "fb_com_extra 1,0xA1B2,4", "fb_com_extra"),
    ("", "fb_cmd 0x61,0x1A1B2,4", "fb_com_extra"),
    ("move 0x1A1B2,R5\nnop\n", "fb_com_extra R5,4", "fb_com_extra"),
    ("", "fb_com_cfg 1,0,4,4", "fb_com_cfg"),
])
def test_unsupported_common_configuration_preserves_dwell_without_proving_a_word(prefix, config, op):
    ir = _analyze(prefix + _COMMON.format(config))
    configuration, = _configurations(ir)
    assert (configuration["t0"]["value"], configuration["t1"]["value"]) == (8, 12)
    assert configuration["source"]["raw"] == config
    assert configuration["meta"]["linq_configuration"]["op"] == op
    diagnostic, = _configuration_diagnostics(ir)
    assert diagnostic["source"] == configuration["source"] and diagnostic["related_events"] == [configuration["id"]]
    assert not ir["feedback_flows"]
    assert ir["feedback_balance"]["status"] == "incomplete"
    channel = ir["feedback_balance"]["channels"]["1"]
    assert channel["sends"] == 1 and channel["send_payloads"] is None
    assert channel["matched"] == channel["discarded_payloads"] == 0
    assert channel["unconsumed_payloads"] is None and channel["ordering_unknown_receives"] == 1


@pytest.mark.parametrize("config", [
    "fb_com_extra 0,0xA1B2,4",
    "fb_cmd 0x61,0xA1B2,4",
    "set_cond 1,0,0,4\nfb_com_extra 1,0xA1B2,4\nset_cond 0,0,0,4",
])
def test_disabled_or_proven_skipped_configuration_keeps_the_existing_arrival(config):
    ir = _analyze(_COMMON.format(config))
    flow, = ir["feedback_flows"]
    assert flow["timing"]["arrival_ns"] == 72
    assert ir["feedback_balance"]["status"] == "balanced"
    assert not _configuration_diagnostics(ir)


def test_conditionally_applied_configuration_keeps_equal_dwell_and_taints_the_stream():
    ir = _analyze(_COMMON.format("set_cond 1,1,0,4\nfb_com_extra 1,0xA1B2,4\nset_cond 0,0,0,4"))
    configuration, = _configurations(ir)
    assert configuration["meta"]["linq_configuration"]["conditional"] is True
    send, = [event for event in ir["events"] if event["kind"] == "feedback_com"]
    assert send["t0"]["value"] == 12
    assert not ir["feedback_flows"] and ir["feedback_balance"]["status"] == "incomplete"


def test_optional_reset_does_not_prove_a_fifo_pull():
    setup = "fb_com_extra 1,0xA1B2,4\nset_cond 1,1,0,4\nfb_com_extra 0,0,4\nset_cond 0,0,0,4"
    ir = _analyze(_COMMON.format(setup).replace("fb_pop_data 1,R0", "fb_pull_data R1,R0"))
    assert not ir["feedback_flows"]
    channels = ir["feedback_balance"]["channels"]
    assert channels["1"]["send_payloads"] is None and channels["fifo"]["ordering_unknown_receives"] == 1


def test_unready_packed_configuration_does_not_use_the_old_disabled_value():
    ir = _analyze("move 0,R5\nnop\n" + _COMMON.format("move 0x1A1B2,R5\nfb_com_extra R5,4"))
    configuration, = _configurations(ir)
    assert configuration["meta"]["linq_configuration"]["operands"] is None
    assert any(item["category"] == "register_not_ready" for item in ir["diagnostics"])
    assert not ir["feedback_flows"] and ir["feedback_balance"]["status"] == "incomplete"


def test_setting_slots_and_unsolicited_unknown_sends_stay_independent():
    ir = _analyze("fb_com_cfg 1,0,4,4\nfb_com_extra 0,0,4\nfb_com_data 1,42,4\nstop")
    channel = ir["feedback_balance"]["channels"]["1"]
    assert channel["status"] == "incomplete" and channel["send_payloads"] is None and channel["receives"] == 0
    assert "payloads=unknown" in render_html(ir)


def test_known_reconfiguration_does_not_resolve_an_earlier_unknown_fifo_word():
    prefix = "wait 8\nfb_com_extra 1,0xA1B2,4\n"
    reset = "fb_com_extra 0,0,4\nfb_com_data 1,42,4\nwait 100\nfb_pop_data 1,R0\nstop"
    clean = _analyze(prefix + reset)
    flow, = clean["feedback_flows"]
    assert flow["timing"]["arrival_ns"] == 76

    earlier = _analyze(prefix + "fb_com_data 1,7,4\n" + reset)
    assert not earlier["feedback_flows"]
    channel = earlier["feedback_balance"]["channels"]["1"]
    assert channel["sends"] == 2 and channel["send_payloads"] is None and channel["ordering_unknown_receives"] == 1


def test_nested_preview_inherits_the_original_send_configuration():
    source = ("move 2,R0\nnop\nouter: move 2,R1\ninner: fb_com_extra 1,0xA1B2,4\nfb_com_data 1,42,4\n"
              "fb_com_extra 0,0,4\nloop R1,@inner\nloop R0,@outer\nwait 100\nfb_pop_data 1,R2\nstop")
    ir = _analyze(source, loop_preview_counts={"config:loop:config.q1asm:4:7": 2, "config:loop:config.q1asm:3:8": 2})
    configuration, = _configuration_diagnostics(ir)
    sends = [entry for entry in _feedback_entries(ir["events"]) if entry["direction"] == "send"]
    assert len(sends) == 4
    assert all(entry["feedback"]["unknown_configuration_event_ids"] == configuration["related_events"] for entry in sends)
    assert not ir["feedback_flows"]


def test_unsupported_tb_width_keeps_independent_iq_and_avoids_definite_tb_discard():
    ir = _analyze("fb_acq_tb_id 5,4\nfb_acq_iq_id 6,4\nfb_acq_tb_cfg 1,0,5,4\nacquire 0,0,4\nwait 1200\n"
                  "fb_pop_data 6,R0\nfb_pop_data 6,R1\nfb_pop_data 5,R2\nstop", integration=80)
    assert [(flow["channel"], flow["timing"]["arrival_ns"], flow["payload_word"]) for flow in ir["feedback_flows"]] == [
        ("6", 256, "I"), ("6", 256, "Q"),
    ]
    tb, iq = (ir["feedback_balance"]["channels"][channel] for channel in ("5", "6"))
    assert tb["status"] == "incomplete" and tb["send_payloads"] is None and tb["discarded_payloads"] == 0
    assert tb["ordering_unknown_receives"] == 1
    assert iq["status"] == "balanced" and iq["send_payloads"] == iq["matched"] == 2


def test_alternatives_replay_configuration_on_their_own_paths():
    source = ("cmp R0,1\njz @enabled\nfb_com_extra 0,0,4\njmp @join\nenabled: fb_com_extra 1,0xA1B2,4\n"
              "join: fb_com_data 1,42,4\nwait 100\nfb_pop_data 1,R1\nstop")
    ir = _analyze(source, branch_policy="explore_both_with_depth_limit")
    flow, = ir["feedback_flows"]
    assert flow["timing"]["arrival_ns"] == 64
    assert list(flow["comparison_assumptions"][0].values()) == ["fallthrough"]
    assert [item["status"] for item in ir["feedback_balance"]["alternatives"]] == ["incomplete", "balanced"]

    unsent = _analyze(source.replace("join: fb_com_data 1,42,4\nwait 100\nfb_pop_data 1,R1", "join: wait 100"),
                      branch_policy="explore_both_with_depth_limit")
    assert unsent["feedback_balance"] == {"status": "balanced", "channels": {}}
