from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from q1timeline.q1asm.instruction_table import INSTRUCTION_TABLE
from q1timeline.diagnostic_catalog import describe_diagnostic
from q1timeline.render.html import render_html


def _ns(value: int) -> dict:
    return {"kind": "concrete", "value": value}


def test_rt_ruler_excludes_q1_prep_in_normal_but_keeps_a_labeled_debug_axis() -> None:
    from q1timeline.analysis.api import analyze_text

    ir = analyze_text("set_mrk 1\nupd_param 4\nstop", execution_mode="simulation",
                      alignment_mode="first_anchor", alignment_anchor_kinds=["upd_param"])
    normal = render_html(ir).split("<template", 1)[0]
    debug = render_html(ir, default_mode="debug").split("<template", 1)[0]
    assert 'data-time-min="0"' in normal and 'data-time-min="-12"' in debug
    assert 'data-lane-role="q1-issue"' in debug
    assert re.findall(r'class="tick-label"[^>]*>(-?\d+) ns</text>', normal) == ["0", "1", "2", "3", "4"]
    assert re.findall(r'class="tick-label"[^>]*>(-?\d+) ns</text>', debug) == ["-12", "-8", "-4", "0", "4"]
    assert ' / debug.rt_queue' not in normal and ' / debug.rt_queue' not in debug
    assert {"rt_queue_enqueue", "rt_queue_dequeue"} <= {event["kind"] for event in ir["events"]}


def test_rt_origin_simulation_parks_the_other_geometry_with_one_embedded_ir(capsys) -> None:
    from q1timeline.cli import main

    project = Path(__file__).parents[1] / "examples" / "set-cond-review" / "02-trigger-execution" / "q1timeline.yml"
    assert main(["analyze", "--project", str(project), "--branch-assumption", "trigger_demo:set_cond:4:1=both",
                 "--format", "vscode-json", "--include-timeline-ir", "--no-render"]) == 0
    ir = json.loads(capsys.readouterr().out)["timeline_ir"]
    for mode, other, minimum, other_minimum in (("normal", "debug", 0, -60), ("debug", "normal", -60, 0)):
        html = render_html(ir, default_mode=mode)
        active = html.split("<template", 1)[0]
        parked = re.search(rf'<template data-timeline-mode="{other}">([\s\S]*?)</template>', html).group(1)
        assert f'data-time-min="{minimum}"' in active
        assert f'data-time-min="{other_minimum}"' in parked
        assert active.count('id="time-cursor"') == parked.count('id="time-cursor"') == 1
        assert f'<template data-timeline-mode="{mode}"></template>' in html
        assert html.count('id="timeline-ir"') == 1
        assert "Execute · RT" in html and "Skip · RT" in html
        assert 'aria-label="Trigger address 1 · 30 ns"' in active and 'aria-label="Trigger address 1 · 30 ns"' in parked
        assert ' / debug.rt_queue' not in html


@pytest.mark.parametrize("program,anchor_kind,offset,minimum,debug_minimum", [
    ("nop\nwait 20\nstop", "rt_queue_dequeue", -100, 0, -100),
    ("nop\nwait 20\nplay 0,1,4\nstop", "play", -120, -20, -120),
    ("nop\nstop", None, 0, 0, None),
])
def test_simulation_rt_origin_uses_the_recorded_anchor(program, anchor_kind, offset, minimum, debug_minimum):
    from q1timeline.analysis.api import analyze_text

    ir = analyze_text(program, sequencer_id="seq", execution_mode="simulation", alignment_mode="first_play",
                      simulation={"startup_delay_ns": 100})
    alignment = ir["alignment"]["seq"]
    anchor = next((event for event in ir["events"] if event["id"] == alignment["anchor_event_id"]), None)
    assert (anchor["kind"] if anchor else None) == anchor_kind
    assert alignment["offset"] == offset and alignment["clock"] == "shared_simulation"
    assert ir["project"]["alignment_mode"] == alignment["mode"] == "first_play"
    warnings = [item for item in ir["diagnostics"] if item["category"] == "alignment_missing"]
    if anchor_kind != "play":
        warning, = warnings
        assert warning["details"]["fallback"] == alignment["fallback"] == "first_rt_dequeue"
        assert ("simulation time zero" if anchor is None else "first RT dequeue") in warning["message"]
    else:
        assert not warnings and "fallback" not in alignment
    html = render_html(ir)
    assert re.search(r'<svg class="timeline-svg"[^>]*data-time-min="([^"]+)"', html).group(1) == str(minimum)
    if debug_minimum is not None:
        parked = re.search(r'<template data-timeline-mode="debug">([\s\S]*?)</template>', html).group(1)
        assert f'data-time-min="{debug_minimum}"' in parked
    else:
        assert '<template data-timeline-mode=' not in html
    wait = next((event for event in ir["events"] if event["kind"] == "wait"), None)
    if wait:
        assert (wait["t0"]["value"], wait["meta"]["aligned_t0"]) == (100, 100 + offset)
        if anchor_kind == "rt_queue_dequeue":
            assert re.search(rf'data-event-id="{wait["id"]}"[^>]*data-t0-x="190\.00"', html)


@pytest.mark.parametrize("mode,anchors,arrival,expected_anchor,normal_min,debug_min", [
    ("after_first_wait_sync", [], None, "wait_sync", -24, -124),
    ("after_first_wait_sync", [], 90, "wait_sync", -34, -124),
    ("first_wait_sync", [], None, "wait_sync", 0, -100),
    ("first_anchor", ["rt_queue_dequeue", "play"], None, "rt_queue_dequeue", 0, -100),
    ("first_anchor", ["rt_queue_dequeue", "q1_issue"], None, "q1_issue", 0, None),
])
def test_shared_simulation_axis_uses_the_selected_rt_anchor(mode, anchors, arrival, expected_anchor, normal_min, debug_min):
    from q1timeline.analysis.api import analyze_text

    program = "wait_sync 4\nwait 8\nstop"
    if mode != "first_wait_sync":
        program = "wait 20\n" + program
    simulation = {"startup_delay_ns": 100}
    if arrival is not None:
        simulation["inputs"] = {"triggers": [{"target": "seq", "address": 1, "time_ns": arrival}]}
    ir = analyze_text(program, sequencer_id="seq", execution_mode="simulation", alignment_mode=mode,
                      alignment_anchor_kinds=anchors, simulation=simulation)
    anchor_id = ir["alignment"]["seq"]["anchor_event_id"]
    anchor, = [event for event in ir["events"] if event["id"] == anchor_id]
    assert anchor["kind"] == expected_anchor and "fallback" not in ir["alignment"]["seq"]
    html = render_html(ir)
    active = html.split("<template", 1)[0]
    assert f'data-time-min="{normal_min}"' in active
    if debug_min is None:
        assert '<template data-timeline-mode=' not in html
        assert re.search(r'class="tick-label"[^>]*>100 ns</text>', active)
    else:
        debug = re.search(r'<template data-timeline-mode="debug">([\s\S]*?)</template>', html).group(1)
        for geometry, edge in ((active, normal_min), (debug, debug_min)):
            assert f'data-time-min="{edge}"' in geometry
            assert re.search(r'class="tick-label"[^>]*>(-?\d+) ns</text>', geometry).group(1) == str(edge)
        first = next(event for event in ir["events"] if event["kind"] in {"wait", "wait_sync", "trigger_receive"})
        assert first["t0"]["value"] == (100 if arrival is None else arrival)
        assert first["meta"]["aligned_t0"] == normal_min
        assert re.search(rf'data-event-id="{first["id"]}"[^>]*data-t0-x="190\.00"', active)


@pytest.mark.parametrize("program,mode,comparison,normal,debug", [
    ("wait 20\nstop", None, False, (0, 20), (-8, 396)),
    ("wait 20\nplay 0,1,4\nstop", "first_play", False, (-20, 4), (-32, 372)),
    ("set_cond 1,1,0,4\nwait 60\nstop", None, True, (0, 60), (-12, 392)),
])
def test_shared_simulation_normal_extent_keeps_rt_and_ignores_q1_tail(program, mode, comparison, normal, debug):
    from q1timeline.analysis.api import analyze_documents

    ir = analyze_documents({"a.q1asm": program, "peer.q1asm": "nop\n" * 100 + "stop"},
                           execution_mode="simulation", alignment_mode=mode,
                           branch_assumptions={"a:set_cond:0:1": "both"} if comparison else None)
    peer_issues = [event for event in ir["events"] if event["sequencer_id"] == "peer" and event["kind"] == "q1_issue"]
    assert len(peer_issues) == 101 and peer_issues[-1]["meta"]["aligned_t1"] == debug[1]
    assert not any(event["lane"].startswith("rt.") for event in ir["events"] if event["sequencer_id"] == "peer")
    html = render_html(ir)
    active, parked = html.split('<template data-timeline-mode="debug">', 1)
    assert f'data-time-min="{normal[0]}" data-time-max="{normal[1]}"' in active
    assert f'data-time-min="{debug[0]}" data-time-max="{debug[1]}"' in parked
    assert peer_issues[-1]["id"] in json.loads(
        re.search(r'<script id="timeline-ir" type="application/json">([\s\S]*?)</script>', html).group(1)
    )["source_map"]["by_event_id"]
    if comparison:
        late_stop, = [event for event in ir["events"] if event["kind"] == "stop"
                      and event["meta"].get("branch_comparison_path") == "taken"]
        assert f'data-event-id="{late_stop["id"]}"' in active


@pytest.mark.parametrize("compare", [False, True])
def test_standalone_normal_hides_network_sender_debug_traffic_but_keeps_the_arrival(compare):
    from q1timeline.analysis.api import analyze_documents

    source = "acquire 0,0,4\nstop"
    if compare:
        source = "set_cond 1,1,0,4\nacquire 0,0,4\nset_cond 0,0,0,4\nstop"
    ir = analyze_documents({"tx.q1asm": source, "rx.q1asm": "wait 400\nstop"}, execution_mode="simulation",
                           branch_assumptions={"tx:set_cond:0:1": "both"} if compare else None, simulation={
                               "startup_delay_ns": 100, "trigger_networks": {"rack": {"grid_epoch_ns": 100}},
                               "trigger_routes": [{"source": "tx", "target": "rx", "address": 3, "network": "rack"}],
                               "inputs": {"acquisitions": [{"sequencer": "tx", "acquisition_index": 0, "bin_index": 0,
                                                           "thresholded": 1}]},
                           })
    request, launch, arrival = (next(event for event in ir["events"] if event["kind"] == kind)
                                for kind in ("trigger_network_request", "trigger_network_launch", "trigger_receive"))
    expected_prefix = "branch_compare.taken." if compare else ""
    assert request["lane"] == launch["lane"] == f"{expected_prefix}debug.trigger_send"
    assert arrival["lane"] == f"{expected_prefix}rt.trigger" and arrival["meta"]["aligned_t0"] == 240
    normal = render_html(ir, default_mode="normal")
    normal_svg = normal.split("<template", 1)[0]
    debug_svg = render_html(ir, default_mode="debug").split("<template", 1)[0]
    for event in (request, launch):
        marker = f'data-event-id="{event["id"]}"'
        assert marker not in normal_svg and marker in debug_svg
    for svg in (normal_svg, debug_svg):
        assert f'data-event-id="{arrival["id"]}"' in svg
        assert 'aria-label="Trigger address 3 · 240 ns"' in svg
    assert 'data-time-min="0"' in normal_svg
    embedded = json.loads(re.search(r'<script id="timeline-ir" type="application/json">([\s\S]*?)</script>', normal).group(1))
    assert {request["id"], launch["id"], arrival["id"]} <= {event["id"] for event in embedded["events"]}
    assert embedded["feedback_flows"] == ir["feedback_flows"]
    flow, = embedded["feedback_flows"]
    assert (flow["from_event_id"], flow["to_event_id"]) == (launch["id"], arrival["id"])


def test_standalone_unobserved_trigger_edge_window_is_debug_only():
    from q1timeline.analysis.api import analyze_documents

    ir = analyze_documents({"tx.q1asm": "acquire_ttl 0,0,1,40\nacquire_ttl 0,0,0,4\nstop",
                            "rx.q1asm": "wait 400\nstop"}, execution_mode="simulation",
                           sequences={"tx": {"acquisitions": {"edges": {"index": 0, "num_bins": 1}}}}, simulation={
                               "startup_delay_ns": 100, "trigger_networks": {"rack": {"grid_epoch_ns": 100}},
                               "trigger_routes": [{"source": "tx", "target": "rx", "address": 3, "network": "rack",
                                                   "source_kind": "ttl_edge"}],
                           })
    window, = [event for event in ir["events"] if event["kind"] == "trigger_edge_window"]
    marker = f'data-event-id="{window["id"]}"'
    assert marker not in render_html(ir, default_mode="normal").split("<template", 1)[0]
    assert marker in render_html(ir, default_mode="debug").split("<template", 1)[0]


def test_analytical_render_keeps_its_single_geometry():
    from q1timeline.analysis.api import analyze_text

    html = render_html(analyze_text("wait 4\nstop"))
    assert html.count('<svg class="timeline-svg"') == 1
    assert '<template data-timeline-mode=' not in html


@pytest.mark.parametrize("legacy", [False, True])
def test_completed_rt_waits_share_the_primary_svg_event_but_remain_in_the_ir(legacy):
    from q1timeline.analysis.api import analyze_text

    ir = analyze_text("wait 20\nwait_trigger 3,4\nwait_sync 4\nwait 8\nstop", sequencer_id="seq",
                      execution_mode="simulation", simulation={"startup_delay_ns": 100, "inputs": {"triggers": [
                          {"target": "seq", "time_ns": 150, "address": 3},
                      ]}})
    by_kind = {event["kind"]: event for event in ir["events"]}
    for auxiliary_kind, primary_kind in (("trigger_wait", "wait_trigger"), ("sync_wait", "wait_sync")):
        auxiliary, primary = by_kind[auxiliary_kind], by_kind[primary_kind]
        assert auxiliary["meta"]["rt_packet_id"] == primary["meta"]["rt_packet_id"]
        if legacy:
            auxiliary["meta"].pop("rt_packet_id")
        for mode in ("normal", "debug"):
            active = render_html(ir, default_mode=mode).split("<template", 1)[0]
            assert f'data-event-id="{auxiliary["id"]}"' not in active
            assert f'data-event-id="{primary["id"]}" data-event-ids="{auxiliary["id"]}"' in active
    auxiliary = by_kind["trigger_wait"]
    assert auxiliary["meta"]["input_event_ids"] == [by_kind["trigger_receive"]["id"]]
    assert auxiliary["duration"]["value"] == 30 and by_kind["wait_trigger"]["duration"]["value"] == 34
    embedded = json.loads(re.search(r'<script id="timeline-ir" type="application/json">([\s\S]*?)</script>',
                                    render_html(ir)).group(1))
    assert auxiliary in embedded["events"]


@pytest.mark.parametrize("program,arrivals,compare,expected", [
    ("move 2,R0\nagain: wait_trigger 3,4\nloop R0,@again\nstop", (110, 120), False, 2),
    ("set_cond 1,1,0,4\nwait_trigger 3,4\nset_cond 0,0,0,4\nwait 20\nstop", (150,), True, 1),
])
def test_wait_projection_retains_dynamic_occurrence_and_comparison_identity(program, arrivals, compare, expected):
    from q1timeline.analysis.api import analyze_text

    ir = analyze_text(program, sequencer_id="seq", execution_mode="simulation",
                      branch_assumptions={"seq:set_cond:0:1": "both"} if compare else None,
                      simulation={"startup_delay_ns": 100, "inputs": {"triggers": [
                          {"target": "seq", "time_ns": time, "address": 3} for time in arrivals
                      ]}})
    primary = {event["meta"]["rt_packet_id"]: event for event in ir["events"] if event["kind"] == "wait_trigger"}
    auxiliaries = [event for event in ir["events"] if event["kind"] == "trigger_wait"]
    assert len(primary) == len(auxiliaries) == expected
    active = render_html(ir).split("<template", 1)[0]
    for auxiliary in auxiliaries:
        owner = primary[auxiliary["meta"]["rt_packet_id"]]
        assert owner["meta"].get("branch_comparison_path") == ("taken" if compare else None)
        assert f'data-event-id="{owner["id"]}" data-event-ids="{auxiliary["id"]}"' in active
        assert f'data-event-id="{auxiliary["id"]}"' not in active


def test_rt_origin_normal_ir_contains_parked_debug_inspector_event():
    from q1timeline.analysis.api import analyze_text

    ir = analyze_text("wait 4\nstop", execution_mode="simulation")
    wait = next(event for event in ir["events"] if event["kind"] == "wait")
    ir["events"].append({**wait, "id": "loop", "kind": "loop_block", "lane": "rt.loop",
                         "label": "Loop", "meta": {**wait["meta"], "loop_id": "L0"}})
    html = render_html(ir)
    active, parked = html.split('<template data-timeline-mode="debug">', 1)
    assert 'data-event-id="loop:control-flow"' not in active
    assert 'data-event-id="loop:control-flow"' in parked
    embedded = re.search(r'<script id="timeline-ir" type="application/json">([\s\S]*?)</script>', html).group(1)
    chip, = [event for event in json.loads(embedded)["events"] if event["id"] == "loop:control-flow"]
    assert chip["kind"] == "control_flow_loop"
    assert chip["meta"]["control_flow_source_event_id"] == "loop"


def test_diagnostics_are_outside_details_with_compact_paths_and_no_repeated_codes_or_fixes() -> None:
    html = render_html({"version": "0.1.0", "events": [], "diagnostics": [{
        "severity": "warning", "category": "analysis_incomplete",
        "message": "Underflow analysis incomplete: runtime input is missing.",
        "source": {"file": r"C:\project\readout.q1asm", "line": 12},
    }]})
    assert html.index('class="diagnostics"') < html.index('id="q1timeline-analysis-details"') < html.index('id="event-inspector"')
    assert re.search(r'<details id="q1timeline-analysis-details"[\s\S]*<aside id="event-inspector"[\s\S]*</aside>[\s\S]*</details>', html)
    row = re.search(r'<li class="diagnostic severity-warning"[\s\S]*?</li>', html).group()
    visible = re.sub(r"<[^>]+>", "", row)
    assert "Analysis is incomplete" in visible
    assert "analysis_incomplete" not in visible
    assert "C:\\project" not in visible
    assert "readout.q1asm:12" in visible
    assert "runtime input is missing." in visible
    assert "Fix:" not in visible
    assert "<code>" not in row


def test_diagnostic_summary_does_not_repeat_its_title() -> None:
    html = render_html({"version": "0.1.0", "events": [], "diagnostics": [{
        "severity": "warning", "category": "analysis_incomplete", "message": "Analysis is incomplete.",
    }]})
    row = re.search(r'<li class="diagnostic severity-warning"[\s\S]*?</li>', html).group()
    assert "diagnostic-message" not in row


@pytest.mark.parametrize("mode", ["normal", "debug"])
def test_underflow_labels_hide_queue_samples_and_slack_and_show_positive_lateness(mode: str) -> None:
    events = [
        {"id": event_id, "kind": kind, "label": kind, "source": {}, "sequencer_id": "seq0", "lane": lane,
         "t0": _ns(0), "t1": _ns(0), "duration": _ns(0), "confidence": "exact", "meta": meta}
        for event_id, kind, lane, meta in [
            ("queue", "queue_depth", "debug.queue_depth", {"estimated_depth": 0}),
            ("normal", "slack", "debug.slack", {"slack_ns": 24}),
            ("late", "slack", "debug.slack", {"slack_ns": -24}),
            ("warning", "underflow_warning", "debug.underflow", {"status": "definite_underflow", "slack_ns": -24}),
            ("possible", "underflow_warning", "debug.underflow", {"status": "possible_underflow", "slack_ns": -8}),
        ]
    ]
    before = json.dumps(events)
    html = render_html({"version": "0.1.0", "events": events}, default_mode=mode)
    assert 'data-event-id="queue"' not in html
    assert ' / debug.queue_depth' not in html
    assert 'data-event-id="normal"' not in html
    assert 'data-event-id="late"' not in html
    assert 'aria-label="RT underflow · 24 ns late' in html
    assert 'data-event-id="possible"' not in html
    assert re.search(r'data-event-id="warning"[^>]*data-diagnostic-placement="point"[^>]*>.*?<text class="timeline-error-mark"[^>]*>!</text>', html, re.S)
    assert json.dumps(events) == before
    presentation = describe_diagnostic({"category": "definite_underflow", "details": {"slack_ns": -24}})
    assert presentation["title"] == "RT underflow"
    assert "24 ns late" in presentation["summary"]
    assert "slack" not in presentation["summary"]


@pytest.mark.parametrize("mode", ["normal", "debug"])
def test_eventual_underflow_proof_has_a_diagnostic_without_a_timed_failure_block(mode):
    from q1timeline.analysis.api import analyze_text
    ir = analyze_text("body: wait 4\njmp @body\n")
    warning = next(event for event in ir["events"] if event["kind"] == "underflow_warning")
    loop = next(event for event in ir["events"] if event["kind"] == "loop_block")
    diagnostic = next(item for item in ir["diagnostics"] if item["category"] == "definite_underflow")
    assert warning["t0"]["kind"] == "unknown"
    assert diagnostic["related_events"] == [loop["id"]]
    html = render_html(ir, default_mode=mode)
    assert f'data-event-id="{warning["id"]}"' not in html
    assert "Infinite loop eventually exhausts any finite initial buffer" in html


@pytest.mark.parametrize("mode", ["normal", "debug"])
def test_errors_fill_commands_and_group_hidden_q1_errors(mode: str) -> None:
    def event(event_id, kind, start, end, lane, meta=None):
        return {"id": event_id, "kind": kind, "label": kind, "sequencer_id": "seq0", "lane": lane,
                "t0": _ns(start), "t1": _ns(end), "duration": _ns(end - start), "confidence": "exact",
                "source": {"file": "seq0.q1asm", "line": 1}, "meta": meta or {}}

    events = [event("q1", "q1_issue", 0, 4, "debug.q1_issue", {"op": "add"}),
              event("play", "play", 100, 120, "rt.path0", {"rt_packet_id": "p0"}),
              event("warning", "underflow_warning", 100, 100, "debug.underflow", {"rt_packet_id": "p0"}),
              event("unknown", "analysis_incomplete", 120, 120, "debug.underflow")]
    diagnostics = [{"severity": "error", "category": "register_not_ready", "message": "Register is not ready.", "related_events": ["q1"]},
                   {"severity": "error", "category": "definite_underflow", "message": "RT underflow.", "related_events": ["warning", "q1"]}]
    before = json.dumps(events)
    html = render_html({"version": "0.1.0", "events": events, "diagnostics": diagnostics}, default_mode=mode)
    svg = re.search(r'<svg class="timeline-svg"[\s\S]*?</svg>', html).group()
    assert 'data-event-id="warning"' not in svg
    assert 'data-event-id="unknown"' not in svg
    assert re.search(r'class="[^"]*diagnostic-error[^"]*" data-event-id="play" data-event-ids="warning"', svg)
    assert re.search(r'class="[^"]*diagnostic-error[^"]*" data-event-id="q1"', svg)
    assert svg.count('class="event q1-error-marker timeline-error-mark"') == 1
    assert svg.count('aria-hidden="true">!</text>') == 2
    assert 'data-diagnostic-placement=' not in svg
    assert " / debug.underflow" not in svg
    assert '>Issues<' not in html
    assert json.dumps(events) == before


def test_svg_command_families_cover_all_supported_instructions() -> None:
    events = [
        {
            "id": op,
            "sequencer_id": "seq0",
            "lane": "debug.q1_issue",
            "kind": "q1_issue",
            "t0": _ns(index * 4),
            "t1": _ns(index * 4 + 4),
            "duration": _ns(4),
            "label": op,
            "confidence": "exact",
            "source": {},
            "meta": {"op": op.upper()},
        }
        for index, op in enumerate(INSTRUCTION_TABLE)
    ]
    html = render_html({"version": "0.1.0", "events": events}, default_mode="debug")

    for op, spec in INSTRUCTION_TABLE.items():
        if op.startswith("fb_"):
            family = "feedback"
        elif spec.category == "branch" or op == "stop":
            family = "flow"
        elif spec.category in {"classical", "control"}:
            family = "q1"
        else:
            family = spec.category
        match = re.search(rf'class="([^"]+)" data-event-id="{op}"', html)
        assert match is not None, op
        assert f"command-family-{family}" in match[1].split(), op


def test_svg_families_follow_source_commands_and_preserve_synthetic_overlays() -> None:
    cases = [
        ("set", "q1_issue", "label: SET_SCOPE_EN 1", {}, "latched"),
        ("feedback", "feedback_com", "FB_COM_DATA 0,1,4", {}, "feedback"),
        ("acquire", "acquire", "acquire_weighed 0,0,0,0,4", {}, "rt"),
        ("marker", "marker_state", "upd_param 4", {}, "rt"),
        ("meta", "q1_issue", "wait 4", {"op": "wait_sync"}, "sync"),
        ("loop", "loop_block", "body: play 0,0,4", {}, "flow"),
        ("control-loop", "control_flow_loop", "play 0,0,4", {"op": "play"}, "flow"),
        ("generated-feedback", "feedback_pop", "", {}, "feedback"),
        ("unknown", "unknown_region", "play 0,0,4", {}, None),
        ("underflow", "underflow_warning", "wait 4", {}, None),
        ("incomplete", "analysis_incomplete", "fb_com_cfg 0,4", {}, None),
        ("unrecognized", "q1_issue", "future_op 4", {}, None),
    ]
    events = [
        {
            "id": event_id,
            "sequencer_id": "seq0",
            "lane": "debug.q1_issue",
            "kind": kind,
            "t0": _ns(index * 4),
            "t1": _ns(index * 4 + 4),
            "duration": _ns(4),
            "label": event_id,
            "confidence": "unknown",
            "source": {"raw": raw},
            "meta": meta,
        }
        for index, (event_id, kind, raw, meta, _family) in enumerate(cases)
    ]
    html = render_html({"version": "0.1.0", "events": events}, default_mode="debug")

    for event_id, _kind, _raw, _meta, family in cases:
        match = re.search(rf'class="([^"]+)" data-event-id="{event_id}"', html)
        if event_id == "incomplete":
            assert match is None
            continue
        assert match is not None, event_id
        family_classes = [name for name in match[1].split() if name.startswith("command-family-")]
        assert family_classes == ([f"command-family-{family}"] if family else []), event_id


def test_svg_command_palette_uses_family_hues_without_kind_fill_overrides() -> None:
    html = render_html({"version": "0.1.0", "events": []})

    for family, light, dark in [
        ("q1", "#245fa8", "#80b7ff"),
        ("flow", "#9a4c18", "#f4a261"),
        ("latched", "#176b7e", "#6ec6d9"),
        ("rt", "#27763c", "#8bcf9a"),
        ("sync", "#7251a3", "#b9a7dc"),
        ("feedback", "#ad316c", "#ff6fb3"),
    ]:
        assert f"--q1asm-{family}-color: {light};" in html
        assert f"--q1asm-{family}-color: {dark};" in html
        assert f".command-family-{family} {{ --q1asm-command-color: var(--q1asm-{family}-color); }}" in html
    assert "fill: var(--q1asm-event-fill" in html
    assert "stroke: var(--q1asm-event-stroke" in html
    assert re.search(r"\.feedback-flow\s*\{[^}]*stroke: var\(--q1asm-feedback-color\)", html)
    for kind in ["play", "wait", "acquire", "upd_param", "feedback_pop", "control_flow_loop"]:
        assert not re.search(rf"\.kind-{kind} rect\s*\{{[^}}]*fill:", html)
    assert not re.search(r"\.branch-path-(?:taken|fallthrough) rect\s*\{[^}]*fill:", html)
    assert ".event text.timeline-error-mark { fill: var(--q1timeline-error-color);" in html
    assert ".diagnostic-error-message { color: var(--q1timeline-error-color);" in html
    assert ".event.diagnostic-error { --q1asm-event-fill: #f6d5d4;" in html


def test_svg_error_fill_keeps_the_command_label_and_selection() -> None:
    rows = [
        ("play", "play", {}),
        ("underflow", "underflow_warning", {"status": "definite_underflow"}),
        ("incomplete", "analysis_incomplete", {"status": "analysis_incomplete"}),
    ]
    events = [
        {
            "id": event_id, "sequencer_id": "seq0", "lane": "rt.play", "kind": kind,
            "t0": _ns(index * 100), "t1": _ns(index * 100 + 100), "duration": _ns(100),
            "label": kind, "confidence": "unknown", "source": {"raw": "play 0,0,100"}, "meta": meta,
        }
        for index, (event_id, kind, meta) in enumerate(rows)
    ]
    html = render_html({"version": "0.1.0", "events": events, "diagnostics": [
        {"severity": "error", "category": "definite_underflow", "message": "Underflow", "related_events": ["play"]},
    ]}, highlight_event_id="play")

    play = re.search(r'class="([^"]+)" data-event-id="play"[^>]*>([\s\S]*?)</g>', html)
    assert play is not None
    assert {"command-family-rt", "is-selected", "has-diagnostic", "diagnostic-error"} <= set(play[1].split())
    assert '<text class="event-label"' in play[2]
    assert "--q1asm-event-label-color: var(--q1asm-command-color);" in html
    assert re.search(r"\.event text\s*\{[^}]*fill: var\(--q1asm-event-label-color, #0f1720\)", html)
    assert "var(--q1asm-command-color) 15%" in html
    assert ".event.is-selected rect { stroke: #111827;" in html
    assert re.search(r'class="timeline-error-mark"[^>]*>!</text>', play[2])
    assert 'data-event-id="incomplete"' not in html
    assert ".status-definite_underflow rect { fill: #c72c2c; }" in html
    assert ".status-analysis_incomplete rect { fill: #8a8f98; stroke-dasharray: 5 3; }" in html


def test_parameters_panel_is_collapsed_between_timeline_and_analysis_details() -> None:
    html = render_html({"version": "0.1.0", "events": [], "analysis": {"parameters": [
        {"name": "DRIVE_DURATION", "value_json": "128", "overridden": True, "base_value_json": "64"},
        {"name": "GAIN_I", "value_json": "10000", "overridden": False},
    ]}})

    assert '<details id="q1timeline-parameters" class="analysis-parameters">' in html
    assert '<span class="parameters-title">Parameters · 2 · 1 overridden</span>' in html
    assert html.index('id="timeline-root"') < html.index('id="q1timeline-parameters"')
    assert html.index('id="q1timeline-parameters"') < html.index('id="q1timeline-analysis-details"')
    assert '<code>DRIVE_DURATION</code>' in html
    assert '<dl class="parameters-list">' in html
    assert '<div class="parameter-row" data-parameter-index="0"><dt><code>DRIVE_DURATION</code></dt>' in html
    assert '<div class="parameter-row" data-parameter-index="1"><dt><code>GAIN_I</code></dt>' in html
    assert '<dd class="parameter-value"><code>128</code>' in html
    assert '<code>128</code>' in html
    assert '← base 64' in html
    assert '<code>GAIN_I</code>' in html
    assert '<code>10000</code>' in html
    assert html.count('<span class="parameter-override">') == 1
    assert 'not live hardware or register values' in html
    assert '.parameters-list-scroll { max-height: 240px; overflow: auto; }' in html


def test_parameters_panel_has_accessible_search_controls() -> None:
    html = render_html({"version": "0.1.0", "events": [], "analysis": {"parameters": [
        {"name": "GAIN_I", "value_json": "10000", "overridden": False},
    ]}})

    assert re.search(
        r'<summary>.*<button class="parameters-search-toggle" type="button" aria-label="Search parameters" '
        r'aria-controls="q1timeline-parameter-search" aria-expanded="false" hidden>.*</button></summary>', html,
    )
    assert '<div id="q1timeline-parameter-search" class="parameters-search" hidden>' in html
    assert re.search(r'<input class="parameters-search-input" type="search"[^>]+aria-label="Search parameters by name">', html)
    assert '<button class="parameters-search-clear" type="button" aria-label="Clear parameter search">Clear</button>' in html
    assert '<span class="parameters-search-count" role="status" aria-live="polite"></span>' in html
    assert '<p class="parameters-empty" hidden>No matching parameters.</p>' in html


def test_parameters_panel_preserves_exact_values_and_escapes_html() -> None:
    html = render_html({"version": "0.1.0", "events": [], "analysis": {"parameters": [
        {"name": "<script>name</script>", "value_json": '"</code><script>alert(1)</script>"',
         "overridden": True, "base_value_json": '"<base>"'},
        {"name": "LARGE", "value_json": "9007199254740993", "overridden": False},
        {"name": "NESTED", "value_json": '{"values": [1, null]}', "overridden": False},
    ]}})

    assert '&lt;script&gt;name&lt;/script&gt;' in html
    assert '&quot;&lt;/code&gt;&lt;script&gt;alert(1)&lt;/script&gt;&quot;' in html
    assert '← base &quot;&lt;base&gt;&quot;' in html
    assert '<script>alert(1)</script>' not in html
    assert '<div class="parameter-row" data-parameter-index="0">' in html
    assert '<code>9007199254740993</code>' in html
    assert '<code>{&quot;values&quot;: [1, null]}</code>' in html


def test_parameters_panel_marks_added_and_equal_value_overrides() -> None:
    html = render_html({"version": "0.1.0", "events": [], "analysis": {"parameters": [
        {"name": "NEW", "value_json": "null", "overridden": True},
        {"name": "SAME", "value_json": "64", "overridden": True, "base_value_json": "64"},
    ]}})

    assert '<span class="parameters-title">Parameters · 2 · 2 overridden</span>' in html
    assert 'added for this analysis' in html
    assert '<code>null</code>' in html
    assert '← base 64' in html


def test_parameters_panel_distinguishes_empty_snapshot_from_older_ir() -> None:
    html = render_html({"version": "0.1.0", "events": [], "analysis": {"parameters": []}})
    assert '<span class="parameters-title">Parameters · 0</span>' in html
    assert 'No parameters configured for this analysis.' in html
    assert 'id="q1timeline-parameters"' not in render_html({"version": "0.1.0", "events": []})


def test_branch_region_renders_marker_without_block_in_debug_mode() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:e1",
                    "sequencer_id": "seq0",
                    "lane": "debug.q1_issue",
                    "kind": "q1_issue",
                    "t0": _ns(40),
                    "t1": _ns(64),
                    "duration": _ns(24),
                    "label": "jge",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                    "meta": {
                        "op": "jge",
                        "branch_id": "seq0:branch:1",
                        "condition": "R0 >= 1",
                        "assumed_branch_path": "taken",
                    },
                },
                {
                    "id": "seq0:branch:1",
                    "sequencer_id": "seq0",
                    "lane": "rt.branch",
                    "kind": "branch_region",
                    "t0": _ns(40),
                    "t1": _ns(40),
                    "duration": _ns(0),
                    "label": "assumed branch taken: R0 >= 1",
                    "confidence": "assumed",
                    "source": {"file": "seq0.q1asm", "line": 2, "column": 1, "raw": "jge @done # branch"},
                    "meta": {
                        "branch_id": "seq0:branch:1",
                        "condition": "R0 >= 1",
                        "assumed_branch_path": "taken",
                    },
                }
            ],
            "diagnostics": [],
        },
        default_mode="debug",
    )

    assert 'class="branch-marker' in html
    assert re.search(r'<g[^>]*data-event-id="seq0:e1"[^>]*role="button" tabindex="0">', html)
    assert re.search(r'<g[^>]*class="branch-marker [^>]*data-event-id="seq0:branch:1"[^>]*role="button" tabindex="0">', html)
    assert "q1-branch-collapsed" in html
    assert ".event.q1-branch-collapsed { display: none; }" in html
    assert ".event.branch-collapsed { display: none; }" in html
    assert "body.mode-normal .event.branch-collapsed" not in html
    assert "eventNode.classList.contains('branch-collapsed'))" in html
    assert "eventNode.classList.contains('q1-branch-collapsed'))" in html
    assert "'Recorded branch path'" in html
    assert "q1timeline:setBranchAssumption" not in html
    assert "Compare true and false" not in html
    assert re.search(
        r'<g class="branch-marker-path-icon branch-marker-path-icon-taken"[^>]*aria-label="shown path: condition true"',
        html,
    )
    assert "Line 2: jge @done" in html
    assert "branch-marker-status" not in html
    assert "Show taken path" not in html
    assert "Show fallthrough path" not in html


@pytest.mark.parametrize("mode", ["normal", "debug"])
def test_control_flow_graph_is_retained_without_rendering_a_panel(mode: str) -> None:
    ir = {
        "version": "0.1.0",
        "events": [
            {
                "id": "seq0:e0",
                "sequencer_id": "seq0",
                "lane": "debug.q1_issue",
                "kind": "q1_issue",
                "t0": _ns(0),
                "t1": _ns(24),
                "duration": _ns(24),
                "label": "jl",
                "confidence": "exact",
                "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                "meta": {
                    "op": "jl",
                    "branch_id": "seq0:branch:0",
                    "condition": "jl status flags",
                    "assumed_branch_path": "taken",
                },
            },
        ],
        "diagnostics": [],
        "control_flow_graph": {
            "sequencers": [
                {
                    "sequencer_id": "seq0",
                    "nodes": [
                        {
                            "id": "seq0:cfg:n0",
                            "label": "start",
                            "start_pc": 0,
                            "end_pc": 1,
                            "event_ids": ["seq0:e0"],
                            "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                        },
                        {
                            "id": "seq0:cfg:n1",
                            "label": "done",
                            "start_pc": 2,
                            "end_pc": 2,
                            "event_ids": [],
                            "source": {"file": "seq0.q1asm", "line": 3, "column": 1},
                        },
                        {
                            "id": "seq0:cfg:n2",
                            "label": "done",
                            "start_pc": 3,
                            "end_pc": 3,
                            "event_ids": [],
                            "source": {"file": "seq0.q1asm", "line": 4, "column": 1},
                        },
                    ],
                    "edges": [
                        {
                            "id": "seq0:cfg:e0",
                            "from_node_id": "seq0:cfg:n0",
                            "to_node_id": "seq0:cfg:n2",
                            "kind": "branch_taken",
                            "op": "jl",
                            "label": "jl @done",
                            "event_ids": ["seq0:e0"],
                            "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                        },
                        {
                            "id": "seq0:cfg:e1",
                            "from_node_id": "seq0:cfg:n0",
                            "to_node_id": "seq0:cfg:n1",
                            "kind": "branch_fallthrough",
                            "op": "jl",
                            "label": "else",
                            "event_ids": ["seq0:e0"],
                            "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                        },
                    ],
                }
            ]
        },
    }
    html = render_html(ir, default_mode=mode)

    for removed_ui in (
        'class="control-flow-graph"',
        "Control-flow graph",
        "Branch map",
        "<h3>Blocks</h3>",
        "<h3>Edges</h3>",
        'class="cfg-graph"',
        "data-cfg-",
    ):
        assert removed_ui not in html

    embedded_json = re.search(r'<script id="timeline-ir" type="application/json">(.*?)</script>', html, re.DOTALL)
    assert embedded_json is not None
    embedded_ir = json.loads(embedded_json[1])
    assert embedded_ir["control_flow_graph"] == ir["control_flow_graph"]
    assert embedded_ir["events"][0] == ir["events"][0]
    assert "'Recorded branch path'" in html
    assert "q1timeline:setBranchAssumption" not in html


@pytest.mark.parametrize("source_kind,source_value", [("acquire", "acq#0/bin0"), ("feedback_com", "R0")])
def test_normal_mode_shows_feedback_source_block_and_collapses_pop(source_kind: str, source_value: str) -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:send",
                    "sequencer_id": "seq0",
                    "lane": f"rt.{source_kind}",
                    "kind": source_kind,
                    "t0": _ns(40),
                    "t1": _ns(80),
                    "duration": _ns(40),
                    "label": source_kind,
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {
                        "feedback": {
                            "channel": "1",
                            "direction": "send",
                            "source": source_value,
                        }
                    },
                },
                {
                    "id": "seq0:pop",
                    "sequencer_id": "seq0",
                    "lane": "rt.feedback",
                    "kind": "feedback_pop",
                    "t0": _ns(120),
                    "t1": _ns(120),
                    "duration": _ns(0),
                    "label": "feedback pop",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                    "meta": {
                        "feedback": {
                            "channel": "1",
                            "direction": "receive",
                            "target": "$LEFT",
                        }
                    },
                },
            ],
            "feedback_flows": [
                {
                    "id": "feedback-flow-0",
                    "from_event_id": "seq0:send",
                    "to_event_id": "seq0:pop",
                    "channel": "1",
                    "label": f"feedback ch 1: {source_value} -> $LEFT",
                }
            ],
            "diagnostics": [],
        },
        default_mode="normal",
    )

    assert 'class="feedback-flow-group"' in html
    assert f"feedback ch 1: {source_value} -&gt; $LEFT" in html
    source_block = re.search(r'class="([^"]+)" data-event-id="seq0:send"', html)
    assert source_block is not None
    assert f"kind-{source_kind}" in source_block[1]
    assert "normal-feedback-collapsed" not in source_block[1]
    assert re.search(r'class="[^"]*kind-feedback_pop[^"]*normal-feedback-collapsed', html)
    assert "body.mode-normal .event.normal-feedback-collapsed { display: none; }" in html
    assert "eventNode.classList.contains('normal-feedback-collapsed')" in html


def test_normal_mode_renders_q1_issue_as_expandable_detail_lane() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:q1-wait",
                    "sequencer_id": "seq0",
                    "lane": "debug.q1_issue",
                    "kind": "q1_issue",
                    "t0": _ns(0),
                    "t1": _ns(4),
                    "duration": _ns(4),
                    "label": "wait",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1, "raw": "    wait_sync 4"},
                    "meta": {"op": "wait_sync"},
                },
                {
                    "id": "seq0:wait",
                    "sequencer_id": "seq0",
                    "lane": "rt.wait",
                    "kind": "wait",
                    "t0": _ns(0),
                    "t1": _ns(20),
                    "duration": _ns(20),
                    "label": "wait",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {},
                },
            ],
            "diagnostics": [],
        },
        default_mode="normal",
    )

    assert 'data-lane="sequencer:seq0"' in html
    assert 'data-lane="sequencer:seq0 / q1_issue"' in html
    assert html.index('data-lane="sequencer:seq0"') < html.index('data-lane="sequencer:seq0 / q1_issue"')
    assert re.search(
        r'<g class="[^"]*q1-issue-lane[^"]*"[^>]*data-lane="sequencer:seq0 / q1_issue"[^>]*data-lane-role="q1-issue"[^>]*data-parent-lane="sequencer:seq0"[^>]*hidden[^>]*style="display:none"',
        html,
    )
    assert re.search(r'<text class="lane-label"[^>]*>Q1 core</text>', html)
    assert re.search(r'id="event-seq0-q1-wait"[^>]*class="[^"]*kind-q1_issue', html)
    assert re.search(r'id="event-seq0-q1-wait"[\s\S]*?<text class="event-label"[^>]*>wait_sync</text>', html)
    assert not re.search(r'id="event-seq0-q1-wait"[\s\S]*?<text class="event-label"[^>]*>wait_sync 4</text>', html)
    assert not re.search(r'id="event-seq0-q1-wait"[^>]*q1-dense-collapsed', html)
    assert '[data-lane-role="q1-issue"] { display: none; }' in html
    assert '[data-lane-role="q1-issue"].q1-issue-expanded { display: inline; }' in html


def test_q1_issue_block_labels_use_the_command_after_source_labels() -> None:
    rows = [
        ("source", "entry : play 0,0,100 # wait", {}, "play"),
        ("metadata", "entry: play 0,0,100", {"op": "wait"}, "wait"),
        ("latched", "entry: set_mrk 1 // play", {}, "set_mrk"),
        ("fallback", "entry : # play", {}, "fallback"),
    ]
    events = [
        {
            "id": event_id, "sequencer_id": "seq0", "lane": "debug.q1_issue", "kind": "q1_issue",
            "t0": _ns(index * 100), "t1": _ns(index * 100 + 100), "duration": _ns(100),
            "label": event_id, "confidence": "exact", "source": {"raw": raw}, "meta": meta,
        }
        for index, (event_id, raw, meta, _expected) in enumerate(rows)
    ]
    html = render_html({"version": "0.1.0", "events": events}, default_mode="debug")

    for event_id, _raw, _meta, expected in rows:
        event = re.search(rf'data-event-id="{event_id}"[^>]*>([\s\S]*?)</g>', html)
        assert event is not None
        assert re.search(rf'<text class="event-label"[^>]*>{expected}</text>', event[1])
        assert not re.search(r'<text class="event-label"[^>]*>entry', event[1])


def test_debug_mode_shows_q1_issue_lane_with_its_source_role() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:q1-wait",
                    "sequencer_id": "seq0",
                    "lane": "debug.q1_issue",
                    "kind": "q1_issue",
                    "t0": _ns(0),
                    "t1": _ns(4),
                    "duration": _ns(4),
                    "label": "wait",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {"op": "wait"},
                }
            ],
            "diagnostics": [],
        },
        default_mode="debug",
    )

    assert 'data-lane="sequencer:seq0 / debug.q1_issue"' in html
    assert re.search(
        r'<g class="[^"]*q1-issue-lane[^"]*"[^>]*data-lane="sequencer:seq0 / debug.q1_issue"[^>]*data-lane-role="q1-issue"[^>]*data-parent-lane="sequencer:seq0">',
        html,
    )
    assert re.search(r'<text class="lane-label"[^>]*>Q1 core</text>', html)


def test_flow_and_branch_labels_do_not_render_boxy_text_halos() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:branch",
                    "sequencer_id": "seq0",
                    "lane": "rt.branch",
                    "kind": "branch_region",
                    "t0": _ns(40),
                    "t1": _ns(40),
                    "duration": _ns(0),
                    "label": "assumed branch fallthrough: $CURSOR_GAIN >= $MAX_GAIN",
                    "confidence": "assumed",
                    "source": {"file": "seq0.q1asm", "line": 3, "column": 1, "raw": "jl @stop_sequencer"},
                    "meta": {
                        "branch_id": "seq0:branch",
                        "condition": "$CURSOR_GAIN >= $MAX_GAIN",
                        "assumed_branch_path": "fallthrough",
                    },
                }
            ],
            "diagnostics": [],
        },
        default_mode="normal",
    )

    assert re.search(r"\.branch-marker text\s*\{[^}]*stroke:\s*none;", html)
    assert re.search(r"\.feedback-flow-label\s*\{[^}]*stroke:\s*none;", html)
    assert not re.search(r"\.branch-marker text\s*\{[^}]*paint-order:\s*stroke;", html)
    assert not re.search(r"\.feedback-flow-label\s*\{[^}]*paint-order:\s*stroke;", html)


def test_confidence_fill_styles_do_not_color_branch_marker_hitboxes() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:branch",
                    "sequencer_id": "seq0",
                    "lane": "rt.branch",
                    "kind": "branch_region",
                    "t0": _ns(40),
                    "t1": _ns(40),
                    "duration": _ns(0),
                    "label": "assumed branch fallthrough: $CURSOR_GAIN >= $MAX_GAIN",
                    "confidence": "assumed",
                    "source": {"file": "seq0.q1asm", "line": 3, "column": 1, "raw": "jl @stop_sequencer"},
                    "meta": {
                        "branch_id": "seq0:branch",
                        "condition": "$CURSOR_GAIN >= $MAX_GAIN",
                        "assumed_branch_path": "fallthrough",
                    },
                }
            ],
            "diagnostics": [],
        },
        default_mode="normal",
    )

    assert ".confidence-assumed rect" not in html
    assert "body.vscode-dark .confidence-assumed rect" not in html
    assert ".event.confidence-assumed > rect" in html
    assert "body.vscode-dark .event.confidence-assumed > rect" in html


def test_event_inline_labels_use_short_tokens_without_losing_tooltip_detail() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:play",
                    "sequencer_id": "seq0",
                    "lane": "rt.play",
                    "kind": "play",
                    "t0": _ns(0),
                    "t1": _ns(80),
                    "duration": _ns(80),
                    "label": "play a very long waveform name that should not be inline",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {},
                },
                {
                    "id": "seq0:acquire",
                    "sequencer_id": "seq0",
                    "lane": "rt.acquire",
                    "kind": "acquire",
                    "t0": _ns(90),
                    "t1": _ns(170),
                    "duration": _ns(80),
                    "label": "acquire with verbose integration label",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                    "meta": {},
                },
            ],
            "diagnostics": [],
        },
        default_mode="normal",
    )

    assert re.search(r'<text class="event-label"[^>]*>play</text>', html)
    assert re.search(r'<text class="event-label"[^>]*>acq</text>', html)
    assert "play a very long waveform name that should not be inline" in html
    assert not re.search(r'<text class="event-label"[^>]*>play a very long waveform', html)


def test_short_rt_command_labels_fit_in_zoomed_widths() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:play",
                    "sequencer_id": "seq0",
                    "lane": "rt.play",
                    "kind": "play",
                    "t0": _ns(0),
                    "t1": _ns(20),
                    "duration": _ns(20),
                    "label": "wf#0",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {},
                },
                {
                    "id": "seq0:upd",
                    "sequencer_id": "seq0",
                    "lane": "rt.update",
                    "kind": "upd_param",
                    "t0": _ns(24),
                    "t1": _ns(42),
                    "duration": _ns(18),
                    "label": "upd_param",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                    "meta": {},
                },
                {
                    "id": "seq0:span",
                    "sequencer_id": "seq0",
                    "lane": "rt.wait",
                    "kind": "wait",
                    "t0": _ns(0),
                    "t1": _ns(620),
                    "duration": _ns(620),
                    "label": "wait",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 3, "column": 1},
                    "meta": {},
                },
            ],
            "diagnostics": [],
        },
        default_mode="normal",
    )

    assert re.search(r'id="event-seq0-play"[\s\S]*?<text class="event-label"[^>]*>play</text>', html)
    assert re.search(r'id="event-seq0-upd"[\s\S]*?<text class="event-label"[^>]*>upd</text>', html)


def test_timing_info_stays_in_diagnostics() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:wait",
                    "sequencer_id": "seq0",
                    "lane": "rt.wait",
                    "kind": "wait",
                    "t0": _ns(0),
                    "t1": _ns(40),
                    "duration": _ns(40),
                    "label": "wait",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {},
                },
            ],
            "diagnostics": [
                {
                    "severity": "info",
                    "category": "runtime_dependent_timing",
                    "message": "Timing depends on trigger arrival.",
                    "related_events": ["seq0:wait"],
                }
            ],
        },
        default_mode="normal",
    )

    assert "Timing depends on trigger arrival." in html
    assert 'data-diagnostic-categories="runtime_dependent_timing"' not in html


def test_auxiliary_panels_collapse_details_while_errors_remain_visible() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:wait",
                    "sequencer_id": "seq0",
                    "lane": "rt.wait",
                    "kind": "wait",
                    "t0": _ns(0),
                    "t1": _ns(40),
                    "duration": _ns(40),
                    "label": "wait",
                    "confidence": "assumed",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {},
                },
            ],
            "diagnostics": [
                {
                    "severity": "error",
                    "category": "invalid_instruction",
                    "message": "Invalid instruction.",
                    "related_events": ["seq0:wait"],
                },
                {
                    "severity": "info",
                    "category": "loop_truncated",
                    "message": "Loop L0 shown as compact block with iteration 0 preview only.",
                    "related_events": ["seq0:wait"],
                },
            ],
            "feedback_balance": {
                "status": "balanced",
                "channels": {
                    "1": {
                        "channel": "1",
                        "status": "balanced",
                        "matched": 1,
                        "receives": 1,
                        "sends": 1,
                        "send_payloads": 1,
                        "unmatched_receives": 0,
                        "unconsumed_payloads": 0,
                    }
                },
            },
        },
        default_mode="normal",
    )

    assert '<details class="feedback-balance"' in html
    assert '<details class="feedback-balance" data-default-open="false"' in html
    assert '<details class="feedback-balance" open' not in html
    assert "Feedback FIFO" in html
    assert "balanced" in html
    assert "1 channel" in html
    assert '<details id="confidence-legend" class="confidence-legend"' in html
    assert '<details id="confidence-legend" class="confidence-legend" data-default-open="false"' in html
    assert '<details id="confidence-legend" class="confidence-legend" open' not in html
    assert '<ol class="diagnostics-critical">' in html
    assert re.search(r'<ol class="diagnostics-critical">[\s\S]*severity-error[\s\S]*Invalid instruction', html)
    assert '<details class="diagnostics-secondary" data-default-open="false">' in html
    assert re.search(
        r'<details class="diagnostics-secondary" data-default-open="false">[\s\S]*severity-info[\s\S]*Loop preview was truncated',
        html,
    )
    assert "details[data-default-open=\"false\"]" in html


def test_feedback_balance_details_open_for_unbalanced_channels() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [],
            "diagnostics": [],
            "feedback_balance": {
                "status": "unbalanced",
                "channels": {
                    "1": {
                        "channel": "1",
                        "status": "under_produced",
                        "matched": 0,
                        "receives": 1,
                        "sends": 0,
                        "send_payloads": 0,
                        "unmatched_receives": 1,
                        "unconsumed_payloads": 0,
                    }
                },
            },
        },
        default_mode="normal",
    )

    assert '<details class="feedback-balance" open' in html
    assert "under_produced" in html
    assert "unmatched receives=1" in html


def test_feedback_balance_item_shows_discarded_payloads() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [],
            "diagnostics": [],
            "feedback_balance": {
                "status": "balanced",
                "channels": {
                    "16": {
                        "channel": "16",
                        "status": "balanced",
                        "matched": 0,
                        "receives": 0,
                        "sends": 1,
                        "send_payloads": 1,
                        "discarded_payloads": 1,
                        "unmatched_receives": 0,
                        "unconsumed_payloads": 0,
                    }
                },
            },
        },
        default_mode="normal",
    )

    assert "discarded payloads=1" in html


def test_feedback_balance_shows_uncertain_order_and_unknown_payload_count():
    html = render_html({
        "version": "0.1.0", "events": [], "feedback_balance": {"status": "incomplete", "channels": {
            "5": {"channel": "5", "status": "incomplete", "receives": 1,
                  "ordering_unknown_receives": 1, "unconsumed_payloads": None},
        }},
    })
    assert '<details class="feedback-balance" open' in html
    assert "unconsumed payloads=unknown" in html
    assert "receive ordering unknown=1" in html


def test_loop_truncated_info_stays_in_diagnostics() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:q1-issue",
                    "sequencer_id": "seq0",
                    "lane": "debug.q1_issue",
                    "kind": "q1_issue",
                    "t0": _ns(0),
                    "t1": _ns(8),
                    "duration": _ns(8),
                    "label": "move",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {},
                },
                {
                    "id": "seq0:wait-a",
                    "sequencer_id": "seq0",
                    "lane": "rt.wait",
                    "kind": "wait",
                    "t0": _ns(8),
                    "t1": _ns(48),
                    "duration": _ns(40),
                    "label": "wait",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                    "meta": {},
                },
                {
                    "id": "seq0:wait-b",
                    "sequencer_id": "seq0",
                    "lane": "rt.wait",
                    "kind": "wait",
                    "t0": _ns(56),
                    "t1": _ns(96),
                    "duration": _ns(40),
                    "label": "wait",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 3, "column": 1},
                    "meta": {},
                },
            ],
            "diagnostics": [
                {
                    "severity": "info",
                    "category": "loop_truncated",
                    "message": "Loop L0 shown as compact block with iteration 0 preview only.",
                    "related_events": ["seq0:q1-issue", "seq0:wait-a", "seq0:wait-b"],
                }
            ],
        },
        default_mode="normal",
    )

    assert "Loop L0 shown as compact block with iteration 0 preview only." in html
    assert 'data-diagnostic-categories="loop_truncated"' not in html


def test_flow_and_branch_labels_use_compact_visible_text_with_full_tooltips() -> None:
    html = render_html(
        {
            "version": "0.1.0",
            "events": [
                {
                    "id": "seq0:acquire",
                    "sequencer_id": "seq0",
                    "lane": "rt.acquire",
                    "kind": "acquire",
                    "t0": _ns(40),
                    "t1": _ns(80),
                    "duration": _ns(40),
                    "label": "acquire",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 1, "column": 1},
                    "meta": {},
                },
                {
                    "id": "seq0:pop",
                    "sequencer_id": "seq0",
                    "lane": "rt.feedback",
                    "kind": "feedback_pop",
                    "t0": _ns(120),
                    "t1": _ns(120),
                    "duration": _ns(0),
                    "label": "feedback pop",
                    "confidence": "exact",
                    "source": {"file": "seq0.q1asm", "line": 2, "column": 1},
                    "meta": {},
                },
                {
                    "id": "seq0:branch",
                    "sequencer_id": "seq0",
                    "lane": "rt.branch",
                    "kind": "branch_region",
                    "t0": _ns(90),
                    "t1": _ns(90),
                    "duration": _ns(0),
                    "label": "assumed branch fallthrough: $CURSOR_GAIN >= $MAX_GAIN",
                    "confidence": "assumed",
                    "source": {"file": "seq0.q1asm", "line": 3, "column": 1, "raw": "jl @stop_sequencer"},
                    "meta": {
                        "branch_id": "seq0:branch",
                        "condition": "$CURSOR_GAIN >= $MAX_GAIN",
                        "assumed_branch_path": "fallthrough",
                    },
                },
            ],
            "feedback_flows": [
                {
                    "id": "feedback-flow-0",
                    "from_event_id": "seq0:acquire",
                    "to_event_id": "seq0:pop",
                    "channel": "1",
                    "label": "feedback ch 1: acq#0/bin0 -> $LEFT",
                },
                {
                    "id": "feedback-flow-1",
                    "from_event_id": "seq0:acquire",
                    "to_event_id": "seq0:pop",
                    "channel": "1",
                    "label": "feedback ch 1: acq#0/bin0 -> $GAIN",
                }
            ],
            "diagnostics": [],
        },
        default_mode="normal",
    )

    assert len(re.findall(r'<text class="feedback-flow-label"[^>]*>fb ch 1</text>', html)) == 1
    assert re.search(
        r'<g class="branch-marker-path-icon branch-marker-path-icon-fallthrough"[^>]*aria-label="shown path: condition false"',
        html,
    )
    assert "Line 3: jl @stop_sequencer" in html
    assert not re.search(r'<text class="branch-marker-condition"', html)
    assert "feedback ch 1: acq#0/bin0 -&gt; $LEFT" in html
    assert "unresolved branch $CURSOR_GAIN &gt;= $MAX_GAIN" in html
