import pytest

from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.api import analyze_documents, analyze_text
from q1timeline.analysis.interpreter import interpret_program
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.playback import PlaybackSession
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.render.html import render_html


def test_unresolved_wait_preserves_nominal_layout_and_marks_html_timing():
    ir = analyze_text("wait_trigger 1,4\nwait 100\nstop", sequencer_id="seq")
    trigger, wait, stop = [event for event in ir["events"] if event["kind"] in {"wait_trigger", "wait", "stop"}]
    assert (trigger["t0"]["value"], trigger["duration"]["value"]) == (0, 4)
    assert trigger["meta"]["trigger_arrival_unknown"]
    assert trigger["meta"]["trigger_wait_duration_ns"] == 4
    assert "rt_trigger_epoch" not in trigger["meta"]
    assert [(event["t0"]["value"], event["meta"]["rt_trigger_epoch"]) for event in (wait, stop)] == [(4, 1), (104, 1)]
    assert wait["duration"]["value"] == 100
    issues = [event for event in ir["events"] if event["kind"] == "q1_issue"]
    assert [(event["t0"]["value"], event["t1"]["value"]) for event in issues] == [(0, 4), (4, 8), (8, 12)]
    assert all("rt_trigger_epoch" not in event["meta"] for event in issues)
    assert any(diagnostic["details"].get("reason") == "trigger_arrival_unknown" for diagnostic in ir["diagnostics"])
    html = render_html(ir)
    assert "time: Arm 0 → waiting for trigger (completion unresolved)" in html
    assert "wait after trigger (ns): 4" in html
    assert "time: Nominal RT 4 → 104 (trigger arrival unresolved)" in html
    assert "time: Nominal RT 104 → 104 (trigger arrival unresolved)" in html
    assert "rt-time-nominal" in html


def test_known_rt_prefix_and_other_sequencer_keep_exact_positions_across_repeated_waits():
    ir = analyze_documents({"a.q1asm": "wait 20\nwait_trigger 1,4\nwait_trigger 2,8\nwait 12\nstop",
                            "b.q1asm": "wait 100\nstop"}, alignment_mode="none")
    a = [event for event in ir["events"] if event["sequencer_id"] == "a" and event["kind"] in {"wait", "wait_trigger", "stop"}]
    b = [event for event in ir["events"] if event["sequencer_id"] == "b"]
    assert [event["t0"]["value"] for event in a] == [0, 20, 24, 32, 44]
    assert [event["meta"].get("rt_trigger_epoch") for event in a] == [None, None, 1, 2, 2]
    assert [event["meta"].get("trigger_arrival_unknown", False) for event in a] == [False, True, True, False, False]
    assert all("rt_trigger_epoch" not in event["meta"] for event in b)


@pytest.mark.parametrize("operator", [0, 1])
def test_only_executed_conditional_trigger_wait_creates_nominal_dependency(operator):
    ir = analyze_text(f"set_cond 1,0,{operator},8\nwait_trigger 1,4\nset_cond 0,0,0,4\nwait 12\nstop", sequencer_id="seq")
    wait, = [event for event in ir["events"] if event["kind"] == "wait"]
    assert wait["t0"]["value"] == (4 if operator else 8)
    assert bool(wait["meta"].get("rt_trigger_epoch")) is bool(operator)
    assert any(event["kind"] == "conditional_skip" for event in ir["events"]) is not bool(operator)
    assert any(diagnostic["details"].get("reason") == "trigger_arrival_unknown" for diagnostic in ir["diagnostics"]) is bool(operator)


def test_compact_loop_preview_and_branch_alternatives_keep_separate_trigger_dependencies():
    source = ("cmp R7,5\njz @trigger\nwait 8\nstop\ntrigger: move 3,R0\nnop\n"
              "body: wait_trigger 1,4\nloop R0,@body\nwait 100\nstop")
    state = interpret_program(parse_q1asm(source, file="trigger.q1asm"), sequencer_id="seq",
                              branch_policy="explore_both_with_depth_limit",
                              loop_preview_counts={"seq:loop:trigger.q1asm:7:8": 2})
    triggers = [event for event in state.events if event.kind == "wait_trigger"]
    assert [event.meta.get("rt_trigger_epoch") for event in triggers] == [None, 1]
    assert all(event.meta["branch_comparison_path"] == "taken" for event in triggers)
    waits = {event.meta["branch_comparison_path"]: event for event in state.events if event.kind == "wait"}
    assert waits["taken"].meta["rt_trigger_epoch"] == 3
    assert "rt_trigger_epoch" not in waits["fallthrough"].meta


def test_alignment_keeps_nominal_offset_without_certifying_post_trigger_anchor():
    ir = analyze_text("wait 8\nwait_trigger 1,4\nanchor: wait 100\nstop", sequencer_id="seq", alignment_mode="label:anchor")
    alignment = ir["alignment"]["seq"]
    assert (alignment["offset"], alignment["confidence"], alignment["time_basis"]) == (-12, "unknown", "nominal")
    prefix, anchor = [event for event in ir["events"] if event["kind"] == "wait"]
    assert prefix["meta"]["aligned_time_nominal"] and "rt_trigger_epoch" not in prefix["meta"]
    assert anchor["meta"]["aligned_t0"] == 0 and anchor["meta"]["rt_trigger_epoch"] == 1
    ir = analyze_text("wait 8\nwait_trigger 1,4\nwait 100\nstop", sequencer_id="seq", alignment_mode="first_wait_trigger")
    assert ir["alignment"]["seq"]["confidence"] == "exact"


def test_nominal_acquisition_preserves_relative_fifo_order_without_absolute_arrivals():
    source = ("fb_acq_iq_id 5,4\nwait_trigger 1,4\nacquire 0,0,4\nfb_com_data 6,7,4\n"
              "wait 400\nfb_pull_data R0,R1\nfb_pull_data R0,R1\nfb_pull_data R0,R1\nstop")
    state = interpret_program(parse_q1asm(source), sequencer_id="seq")
    state.metadata["hardware_parameters"] = {"integration_length_acq": 80}
    align_timelines([state], mode="none")
    ir = timeline_ir_from_states([state])
    acquisition, = [event for event in ir["events"] if event["kind"] == "acquire"]
    window = acquisition["meta"]["acquisition_window"]
    assert window["time_basis"] == "nominal" and window["effective_duration_ns"] == 80
    flows = ir["feedback_flows"]
    assert [(flow["channel"], flow.get("payload_word")) for flow in flows] == [("6", None), ("5", "I"), ("5", "Q")]
    assert all(flow["timing"]["send_available_ns"] is None and flow["timing"]["arrival_ns"] is None for flow in flows)


def test_nominal_receiver_does_not_produce_a_false_early_latency_warning():
    ir = analyze_text("fb_com_data 5,9,4\nwait_trigger 1,4\nfb_pop_data 5,R0\nstop")
    flow, = ir["feedback_flows"]
    assert flow["timing"]["arrival_ns"] == 60
    assert not any(diagnostic["category"] == "feedback_latency_violation" for diagnostic in ir["diagnostics"])


@pytest.mark.parametrize("mode", ["playback", "simulation"])
def test_observed_arrival_leaves_no_nominal_flag_and_completes_wait(mode):
    source = "wait_trigger 1,4\nwait 100\nstop"
    if mode == "playback":
        playback = PlaybackSession({"seq": ("trigger.q1asm", source)}, external_inputs={
            "triggers": [{"id": "edge", "address": 1, "targets": ["seq"], "times_ns": [20]}],
        })
        playback.run()
        ir = playback.snapshot()
    else:
        ir = analyze_text(source, sequencer_id="seq", alignment_mode="none", execution_mode="simulation", simulation={
            "startup_delay_ns": 4, "inputs": {"triggers": [{"target": "seq", "address": 1, "time_ns": 20}]},
        })
    trigger, wait = [event for event in ir["events"] if event["kind"] in {"wait_trigger", "wait"}]
    assert trigger["t1"]["value"] == wait["t0"]["value"] == 24
    assert not any(event["meta"].get("trigger_arrival_unknown") or event["meta"].get("rt_trigger_epoch") for event in ir["events"])
