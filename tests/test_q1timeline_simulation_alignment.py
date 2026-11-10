import json
import re
from copy import deepcopy
from html import unescape
from pathlib import Path

import pytest

from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.api import analyze_documents, analyze_text
from q1timeline.analysis.interpreter import prepare_def_program
from q1timeline.analysis.values import Concrete
from q1timeline.cli import main
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.render.html import RenderError, render_html
from q1timeline.simulation.runtime import simulate_programs
from q1timeline.watch.watcher import run_watch_once


SYNC_PROGRAMS = {
    "a.q1asm": "wait_sync 4\nwait 4\nstop",
    "b.q1asm": "wait 20\nwait_sync 4\nwait 4\nstop",
}
TRIGGER_WAIT_PROGRAM = "wait 20\nwait_trigger 3,4\nwait 8\nstop"


def test_default_simulation_origin_preserves_post_barrier_simultaneity():
    ir = analyze_documents(SYNC_PROGRAMS, execution_mode="simulation", simulation={"startup_delay_ns": 32})

    assert ir["project"]["alignment_mode"] == "first_rt_dequeue"
    assert ir["alignment"]["a"] == ir["alignment"]["b"]
    alignment = ir["alignment"]["a"]
    assert alignment["offset"] == -32
    assert alignment["anchor_sequencer_id"] == "a"
    assert alignment["clock"] == "shared_simulation"
    anchor = next(event for event in ir["events"] if event["id"] == alignment["anchor_event_id"])
    assert anchor["kind"] == "rt_queue_dequeue"
    after_barrier = [event for event in ir["events"] if event["kind"] == "wait" and event["t0"]["value"] == 56]
    assert {event["sequencer_id"] for event in after_barrier} == {"a", "b"}
    assert {(event["meta"]["aligned_t0"], event["meta"]["aligned_t1"]) for event in after_barrier} == {(24, 28)}
    normal = render_html(ir)
    assert 'data-time-min="0"' in normal
    assert '>0 ns</text>' in normal
    debug = render_html(ir, default_mode="debug")
    assert 'data-time-min="-32"' in debug
    assert '>-32 ns</text>' in debug


@pytest.mark.parametrize("mode,kinds,offset", [
    ("none", (), 0), ("manual:7", (), 7), ("first_wait_sync", (), -32),
    ("after_first_wait_sync", (), -56), ("first_anchor", ("rt_queue_dequeue",), -32),
])
def test_explicit_modes_use_one_offset_and_keep_pre_anchor_rt(mode, kinds, offset):
    ir = analyze_documents(SYNC_PROGRAMS, execution_mode="simulation", simulation={"startup_delay_ns": 32},
                           alignment_mode=mode, alignment_anchor_kinds=kinds)
    assert {row["offset"] for row in ir["alignment"].values()} == {offset}
    assert ir["project"]["alignment_mode"] == mode
    if mode in {"first_wait_sync", "after_first_wait_sync"}:
        anchor = next(event for event in ir["events"] if event["id"] == ir["alignment"]["a"]["anchor_event_id"])
        assert anchor["kind"] == "wait_sync" and not anchor["meta"].get("pending")
        assert (anchor["t0"]["value"], anchor["t1"]["value"]) == (32, 56)
    if mode == "after_first_wait_sync":
        before = next(event for event in ir["events"] if event["sequencer_id"] == "b" and event["t0"]["value"] == 32
                      and event["kind"] == "wait")
        assert before["meta"]["aligned_t0"] == -24
        assert '>-24 ns</text>' in render_html(ir)


@pytest.mark.parametrize(("mode", "kinds", "anchor_kind", "offset"), [
    ("first_wait_trigger", (), "trigger_wait", -120),
    ("first_anchor", ("trigger_wait",), "trigger_wait", -120),
    ("first_anchor", ("wait_trigger",), "rt_queue_dequeue", -100),
])
def test_shared_pending_trigger_wait_can_anchor_at_its_arm(mode, kinds, anchor_kind, offset):
    ir = analyze_text(TRIGGER_WAIT_PROGRAM, sequencer_id="a", execution_mode="simulation",
                      alignment_mode=mode, alignment_anchor_kinds=kinds, simulation={"startup_delay_ns": 100})
    alignment = ir["alignment"]["a"]
    anchor = next(event for event in ir["events"] if event["id"] == alignment["anchor_event_id"])
    assert alignment["offset"] == offset
    assert anchor["kind"] == anchor_kind
    first, = [event for event in ir["events"] if event["kind"] == "wait"]
    pending, = [event for event in ir["events"] if event["kind"] == "trigger_wait"]
    assert first["t0"]["value"] == 100
    assert first["meta"]["aligned_t0"] == 100 + offset
    assert pending["meta"]["pending"] is True
    assert (pending["t0"]["value"], pending["t1"]["value"]) == (120, 120)
    assert pending["meta"]["aligned_t0"] == 120 + offset
    assert ir["execution"]["a"]["status"] == "blocked"
    assert any(item["category"] == "alignment_missing" for item in ir["diagnostics"]) == (anchor_kind == "rt_queue_dequeue")


@pytest.mark.parametrize(("mode", "anchor_kind", "offset"), [
    ("first_wait_sync", "sync_wait", -120),
    ("after_first_wait_sync", "rt_queue_dequeue", -100),
])
def test_pending_sync_wait_qualifies_as_arm_and_not_as_completed_barrier(mode, anchor_kind, offset):
    docs = {"a.q1asm": "wait 20\nwait_sync 4\nwait 8\nstop", "b.q1asm": "jmp @end\nwait_sync 4\nend: stop"}
    ir = analyze_documents(docs, execution_mode="simulation", alignment_mode=mode, simulation={"startup_delay_ns": 100})
    alignment = ir["alignment"]["a"]
    anchor = next(event for event in ir["events"] if event["id"] == alignment["anchor_event_id"])
    assert alignment == ir["alignment"]["b"]
    assert alignment["offset"] == offset
    assert anchor["kind"] == anchor_kind
    pending, = [event for event in ir["events"] if event["kind"] == "sync_wait"]
    assert pending["meta"]["pending"] is True
    assert (pending["t0"]["value"], pending["t1"]["value"]) == (120, 120)
    assert pending["meta"]["aligned_t0"] == 120 + offset
    assert ir["execution"]["a"]["status"] == "blocked"
    assert any(item["category"] == "alignment_missing" for item in ir["diagnostics"]) == (mode == "after_first_wait_sync")


@pytest.mark.parametrize("fault", [False, True])
def test_truncated_sync_can_anchor_to_arm_but_not_an_unobserved_completion(fault):
    suffix = "nop\n" * 37 + "illegal" if fault else "wait 8\nstop"
    docs = {"a.q1asm": f"wait 20\nwait_sync 40\n{suffix}", "b.q1asm": "wait 50\nwait_sync 40\nstop"}
    config = {"startup_delay_ns": 100, **({} if fault else {"max_time_ns": 160})}
    start = analyze_documents(docs, execution_mode="simulation", alignment_mode="first_wait_sync", simulation=config)
    ended = analyze_documents(docs, execution_mode="simulation", alignment_mode="after_first_wait_sync", simulation=config)
    wait = next(event for event in start["events"] if event["sequencer_id"] == "a" and event["kind"] == "wait_sync")
    assert start["alignment"]["a"]["anchor_event_id"] == wait["id"]
    assert start["alignment"]["a"]["offset"] == -120
    assert (wait["t0"]["value"], wait["t1"]["value"]) == (120, 160)
    if fault:
        assert wait["meta"]["interrupted_by_fault"] is True
        assert ended["alignment"]["a"]["offset"] == -190 and ended["alignment"]["a"]["anchor_sequencer_id"] == "b"
    else:
        assert wait["meta"]["observation_cutoff_ns"] == 160
        assert ended["alignment"]["a"]["offset"] == -100 and ended["alignment"]["a"]["fallback"] == "first_rt_dequeue"


def test_completed_trigger_wait_keeps_its_rt_command_as_the_anchor():
    ir = analyze_text(TRIGGER_WAIT_PROGRAM, sequencer_id="a", execution_mode="simulation",
                      alignment_mode="first_wait_trigger", simulation={
                          "startup_delay_ns": 100,
                          "inputs": {"triggers": [{"target": "a", "time_ns": 150, "address": 3}]},
                      })
    alignment = ir["alignment"]["a"]
    anchor = next(event for event in ir["events"] if event["id"] == alignment["anchor_event_id"])
    assert alignment["offset"] == -120
    assert anchor["kind"] == "wait_trigger" and not anchor["meta"].get("pending")
    assert (anchor["t0"]["value"], anchor["t1"]["value"]) == (120, 154)
    assert anchor["meta"]["rt_execution_ns"] == 150


@pytest.mark.parametrize(("pending_before_arm", "anchor_sequencer", "anchor_kind", "offset"), [
    (20, "a", "trigger_wait", -120), (40, "b", "wait_trigger", -132),
])
def test_shared_trigger_anchor_uses_earliest_arm_across_pending_and_completed_waits(
    pending_before_arm, anchor_sequencer, anchor_kind, offset,
):
    docs = {"a.q1asm": f"wait {pending_before_arm}\nwait_trigger 3,4\nstop",
            "b.q1asm": "wait 32\nwait_trigger 4,4\nstop"}
    ir = analyze_documents(docs, execution_mode="simulation", alignment_mode="first_wait_trigger", simulation={
        "startup_delay_ns": 100, "inputs": {"triggers": [{"target": "b", "time_ns": 150, "address": 4}]},
    })
    alignment = ir["alignment"]["a"]
    anchor = next(event for event in ir["events"] if event["id"] == alignment["anchor_event_id"])
    assert alignment == ir["alignment"]["b"]
    assert alignment["offset"] == offset
    assert (anchor["sequencer_id"], anchor["kind"]) == (anchor_sequencer, anchor_kind)
    assert ir["execution"]["a"]["status"] == "blocked"
    assert ir["execution"]["b"]["status"] == "completed"
    assert not any(item["category"] == "alignment_missing" for item in ir["diagnostics"])


def test_shared_earliest_explicit_anchor_suffices_and_missing_anchor_falls_back_once():
    docs = {"z.q1asm": "wait 20\nupd_param 4\nstop", "b.q1asm": "upd_param 4\nstop", "a.q1asm": "wait 4\nstop"}
    config = {"startup_delay_ns": 32}
    ir = analyze_documents(docs, execution_mode="simulation", simulation=config,
                           alignment_mode="first_anchor", alignment_anchor_kinds=["upd_param"])
    assert {row["anchor_sequencer_id"] for row in ir["alignment"].values()} == {"b"}
    assert not [item for item in ir["diagnostics"] if item["category"] == "alignment_missing"]

    missing = analyze_documents(docs, execution_mode="simulation", simulation=config, alignment_mode="first_play")
    diagnostics = [item for item in missing["diagnostics"] if item["category"] == "alignment_missing"]
    assert len(diagnostics) == 1
    assert diagnostics[0]["details"]["fallback"] == "first_rt_dequeue"
    assert {row["offset"] for row in missing["alignment"].values()} == {-32}


def test_route_latency_preserved_across_unequal_rt_prefill():
    ir = analyze_documents({"sender.q1asm": "fb_com_data 16,7,4\nstop",
                            "receiver.q1asm": "nop\n" * 4 + "wait 100\nstop"}, execution_mode="simulation",
                           simulation={"routes": [{"source": "sender", "target": "receiver", "id": 16, "latency_ns": 60}]})
    by_id = {event["id"]: event for event in ir["events"]}
    flow, = ir["feedback_flows"]
    source, target = by_id[flow["from_event_id"]], by_id[flow["to_event_id"]]
    assert (source["t0"]["value"], target["t0"]["value"]) == (8, 68)
    assert (source["meta"]["aligned_t0"], target["meta"]["aligned_t0"]) == (0, 60)
    receiver, = [event for event in ir["events"] if event["kind"] == "wait"]
    assert receiver["meta"]["aligned_t0"] == 16


def test_q1_only_keeps_zero_origin_without_missing_anchor():
    ir = analyze_text("move 5,R0\nnop\nstop", execution_mode="simulation")
    alignment, = ir["alignment"].values()
    assert (alignment["offset"], alignment["anchor_event_id"], alignment["confidence"]) == (0, None, "exact")
    assert not [item for item in ir["diagnostics"] if item["category"] == "alignment_missing"]


@pytest.mark.parametrize("mode,kinds", [(None, ()), ("first_anchor", ("rt_queue_dequeue",))])
def test_prepared_simulation_retains_its_first_dequeue_as_alignment_anchor(mode, kinds):
    programs = {"fast": parse_q1asm("wait 4\nwait 4\nstop"),
                "slow": parse_q1asm("nop\nnop\nwait 4\nwait 4\nstop")}
    prepared = {name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                for name, program in programs.items()}
    full = simulate_programs(programs)
    sparse = simulate_programs(programs, prepared_programs=prepared)
    assert align_timelines(full, mode=mode, anchor_kinds=kinds).sequencer_offsets == (
        align_timelines(sparse, mode=mode, anchor_kinds=kinds).sequencer_offsets
    ) == {"fast": Concrete(-12), "slow": Concrete(-12)}
    for state in sparse:
        first, = [event for event in state.events if event.kind == "rt_queue_dequeue"]
        assert first.t0 == state.rt_packets[0].rt_t0
        assert state.metadata["alignment"]["anchor_sequencer_id"] == "fast"


def test_cli_and_watch_use_the_shared_default_with_an_independent_lane(tmp_path, capsys):
    (tmp_path / "a.q1asm").write_text("wait_sync 4\nwait 4\nstop\n", encoding="utf-8")
    (tmp_path / "b.q1asm").write_text("wait 12\nstop\n", encoding="utf-8")
    path = tmp_path / "q1timeline.yml"
    path.write_text("""analysis:
  execution_mode: simulation
sequencers:
  - id: a
    file: a.q1asm
  - id: b
    file: b.q1asm
    hardware_parameters:
      sync_en: false
""", encoding="utf-8")
    args = ["analyze", "--project", str(path), "--format", "vscode-json", "--include-timeline-ir", "--no-render"]
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    ir = payload["timeline_ir"]
    assert payload["alignment_policy"] == "first_rt_dequeue"
    assert ir["alignment"]["a"] == ir["alignment"]["b"]
    assert not [item for item in ir["diagnostics"] if item["category"] == "alignment_missing"]
    assert run_watch_once(path, tmp_path / "watch").ok
    assert main([*args, "--execution-mode", "analysis"]) == 0
    assert json.loads(capsys.readouterr().out)["alignment_policy"] == "first_wait_sync"
    with path.open("a", encoding="utf-8") as project:
        project.write("alignment:\n  mode: first_wait_sync\n")
    assert main(args) == 0
    explicit = json.loads(capsys.readouterr().out)
    assert explicit["alignment_policy"] == "first_wait_sync"
    assert explicit["timeline_ir"]["alignment"]["a"] == explicit["timeline_ir"]["alignment"]["b"]


def test_fractional_digital_time_is_visible_without_changing_raw_clock():
    tick = 1 / 128
    ir = analyze_text("wait 20\nstop", sequencer_id="s", execution_mode="simulation", simulation={
        "startup_delay_ns": 100,
        "inputs": {"digital": {"s": {"initial_level": 0, "transitions": [{"level": 1, "time_ticks": 104*128 + 1}]}}},
    })
    edge, = [event for event in ir["events"] if event["kind"] == "digital_transition"]
    assert edge["t0"]["value"] == 104 + tick
    assert edge["meta"]["aligned_t0"] == 4 + tick
    assert edge["source"]["raw"] == ""
    assert "aligned time: 4.0078125" in render_html(ir)
    edge["meta"]["aligned_t0"] = 4.01
    with pytest.raises(RenderError, match="exact simulation ticks"):
        render_html(ir)


def _svg_event_title(html, event_id):
    match = re.search(rf'<g\b[^>]*data-event-id="{re.escape(event_id)}"[^>]*>\s*<title>(.*?)</title>', html, re.S)
    return unescape(match.group(1))


@pytest.mark.parametrize("mode", ["normal", "debug"])
def test_baseline_underflow_diagnostic_links_its_aligned_marker(mode):
    source = "wait 4\n" + "nop\n" * 50 + "stop"
    ir = analyze_text(source, file="underflow.q1asm", execution_mode="simulation", simulation={"startup_delay_ns": 100})
    marker, = [event for event in ir["events"] if event["kind"] == "underflow_warning"]
    diagnostic, = [item for item in ir["diagnostics"] if item["category"] == "rt_queue_underflow"]
    execution = ir["execution"][marker["sequencer_id"]]
    assert (marker["t0"]["value"], marker["meta"]["aligned_t0"]) == (104, 4)
    assert diagnostic["related_events"] == [marker["id"]] and diagnostic["source"] == marker["source"]
    assert execution["status"] == "error" and execution["reason"] == "RT queue underflow at 104 ns."
    assert diagnostic["message"] == "Simulation execution error: RT queue underflow at 104 ns."

    html = render_html(ir, default_mode=mode)
    svg = re.search(r'<svg class="timeline-svg"[\s\S]*?</svg>', html).group()
    assert svg.count(f'data-event-id="{marker["id"]}"') == 1
    assert svg.count('class="timeline-error-mark"') == 1
    assert 'q1-error-marker' not in svg
    row = re.search(r'<li [^>]*data-diagnostic-category="rt_queue_underflow"[\s\S]*?</li>', html).group()
    assert f'data-related-event-id="{marker["id"]}"' in row
    assert "The RT queue ran out of commands." in row and "104 ns" not in row
    tooltip = _svg_event_title(svg, marker["id"])
    assert "aligned time: 4 ns" in tooltip and "104" not in tooltip


def test_prepared_underflow_has_no_marker_link():
    program = parse_q1asm("wait 4\n" + "nop\n" * 50 + "stop", file="underflow.q1asm")
    binding = prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
    state, = simulate_programs({"seq": program}, prepared_programs={"seq": binding}, simulation={"startup_delay_ns": 100})

    diagnostic, = [item for item in state.diagnostics if item.category == "rt_queue_underflow"]
    assert not diagnostic.related_events
    assert not any(event.kind == "underflow_warning" for event in state.events)
    assert diagnostic.source.file == "underflow.q1asm" and "104 ns" in diagnostic.message
    assert state.metadata["execution"]["status"] == "error"


def test_standalone_wait_tooltip_uses_one_time_basis():
    ir = analyze_text("wait 40\nstop", execution_mode="simulation", simulation={"startup_delay_ns": 100})
    wait, = [event for event in ir["events"] if event["kind"] == "wait"]
    original = deepcopy(ir)
    for mode in ("normal", "debug"):
        tooltip = _svg_event_title(render_html(ir, default_mode=mode), wait["id"])
        assert "aligned time: 0 -> 40" in tooltip
        assert "local time" not in tooltip and "100" not in tooltip and "meta:" not in tooltip
        assert "duration: 40" in tooltip and "raw: wait 40" in tooltip
    assert ir == original

    for event in ir["events"]:
        for key in ("aligned_t0", "aligned_t1"):
            event["meta"].pop(key, None)
    tooltip = _svg_event_title(render_html(ir), wait["id"])
    assert "local time: 100 -> 140" in tooltip and "aligned time" not in tooltip


def test_standalone_tooltip_preserves_symbolic_and_nominal_trigger_timing():
    ir = analyze_text("wait_trigger 1,4\nwait 20\nstop")
    wait, = [event for event in ir["events"] if event["kind"] == "wait"]
    # The geometry can have an aligned start while its end stays symbolic.
    wait["t1"] = {"kind": "symbolic", "expr": "RT_END", "display": "RT_END"}
    wait["meta"].pop("aligned_t1", None)
    wait["meta"].pop("local_t1", None)
    tooltip = _svg_event_title(render_html(ir), wait["id"])
    assert "Nominal RT 4 → RT_END (trigger arrival unresolved)" in tooltip
    assert "local time" not in tooltip and "meta:" not in tooltip


def test_simulation_label_aligns_to_the_labeled_wait_on_the_shared_rt_clock():
    ir = analyze_documents({"a.q1asm": "wait 20\nanchor: wait 4\nstop", "b.q1asm": "wait 12\nstop"},
                           execution_mode="simulation", alignment_mode="label:anchor",
                           simulation={"startup_delay_ns": 100})
    first, anchor = [event for event in ir["events"] if event["sequencer_id"] == "a" and event["kind"] == "wait"]
    assert ir["alignment"]["a"] == ir["alignment"]["b"]
    assert ir["alignment"]["a"]["anchor_event_id"] == anchor["id"]
    assert ir["alignment"]["a"]["offset"] == -120
    assert [(event["t0"]["value"], event["meta"]["aligned_t0"]) for event in (first, anchor)] == [(100, -20), (120, 0)]
    rendered = render_html(ir)
    assert '>-20 ns</text>' in rendered
    assert f'data-event-id="{first["id"]}"' in rendered


def test_shared_simulation_label_uses_earliest_rt_event_with_stable_ties():
    docs = {"z.q1asm": "anchor: wait 4\nstop", "a.q1asm": "wait 20\nanchor: wait 4\nstop",
            "b.q1asm": "anchor: wait 4\nstop"}
    ir = analyze_documents(docs, execution_mode="simulation", alignment_mode="label:anchor",
                           simulation={"startup_delay_ns": 100})
    assert {row["offset"] for row in ir["alignment"].values()} == {-100}
    assert {row["anchor_sequencer_id"] for row in ir["alignment"].values()} == {"b"}


def test_shared_simulation_label_ignores_an_earlier_comparison_event():
    source = "set_cond 1,0,1,4\nwait 20\nset_cond 0,0,0,4\nanchor: wait 4\nstop"
    ir = analyze_text(source, sequencer_id="s", execution_mode="simulation", alignment_mode="label:anchor",
                      simulation={"startup_delay_ns": 100}, branch_assumptions={"s:set_cond:0:1": "both"})
    waits = {event["meta"].get("branch_comparison_path"): event for event in ir["events"]
             if event["kind"] == "wait" and event["source"]["line"] == 4}
    assert waits["fallthrough"]["t0"]["value"] == 104
    assert waits[None]["t0"]["value"] == 120
    assert ir["alignment"]["s"]["anchor_event_id"] == waits[None]["id"]
    assert ir["alignment"]["s"]["offset"] == -120


@pytest.mark.parametrize(("suffix", "offset", "anchor_kind"), [
    ("upd_param 8\nstop", -100, "rt_queue_dequeue"), ("stop", 0, None),
])
def test_q1_setter_label_falls_back_when_no_rt_event_matches(suffix, offset, anchor_kind):
    ir = analyze_text(f"anchor: set_freq 5000000\n{suffix}", sequencer_id="s", execution_mode="simulation",
                      alignment_mode="label:anchor", simulation={"startup_delay_ns": 100})
    alignment = ir["alignment"]["s"]
    assert alignment["offset"] == offset
    assert alignment["fallback"] == "first_rt_dequeue"
    if anchor_kind is None:
        assert alignment["anchor_event_id"] is None
    else:
        anchor = next(event for event in ir["events"] if event["id"] == alignment["anchor_event_id"])
        assert anchor["kind"] == anchor_kind
    assert len([item for item in ir["diagnostics"] if item["category"] == "alignment_missing"]) == 1


def test_shared_simulation_label_can_anchor_to_rt_stop():
    ir = analyze_text("wait 20\nanchor: stop 17", sequencer_id="s", execution_mode="simulation",
                      alignment_mode="label:anchor", simulation={"startup_delay_ns": 100})
    alignment = ir["alignment"]["s"]
    stop, = [event for event in ir["events"] if event["kind"] == "stop"]
    assert alignment["anchor_event_id"] == stop["id"]
    assert alignment["offset"] == -120
    assert (stop["t0"]["value"], stop["meta"]["aligned_t0"]) == (120, 0)


def test_simulation_first_anchor_can_explicitly_select_enqueue():
    ir = analyze_text("wait 20\nanchor: wait 4\nstop", sequencer_id="s", execution_mode="simulation",
                      alignment_mode="first_anchor", alignment_anchor_kinds=["rt_queue_enqueue"],
                      simulation={"startup_delay_ns": 100})
    alignment = ir["alignment"]["s"]
    anchor = next(event for event in ir["events"] if event["id"] == alignment["anchor_event_id"])
    assert alignment["offset"] == -4
    assert anchor["kind"] == "rt_queue_enqueue"


def test_explicit_label_anchor_ignores_unrelated_simulation_source_line():
    ir = analyze_text("start: wait 20\nstop", file="label.q1asm", sequencer_id="s",
                      execution_mode="simulation", alignment_mode="label:start", simulation={
        "startup_delay_ns": 100,
        "inputs": {"digital": {"s": {"initial_level": 0, "transitions": [{"time_ticks": 2 * 128, "level": 1}]}}},
    })
    alignment = ir["alignment"]["s"]
    anchor = next(event for event in ir["events"] if event["id"] == alignment["anchor_event_id"])
    transition, = [event for event in ir["events"] if event["kind"] == "digital_transition"]
    assert transition["source"]["line"] == anchor["source"]["line"] == 1
    assert transition["source"]["file"] == "<simulation>"
    assert alignment["offset"] == -100
    assert anchor["kind"] == "wait" and anchor["source"]["file"] == "label.q1asm"


def test_trigger_example_compares_paths_at_the_same_rt_origin(capsys):
    path = Path(__file__).parents[1] / "examples" / "set-cond-review" / "02-trigger-execution" / "q1timeline.yml"
    args = ["analyze", "--project", str(path), "--align", "first_rt_dequeue", "--format", "vscode-json",
            "--include-timeline-ir", "--no-render"]
    assert main(args) == 0
    ir = json.loads(capsys.readouterr().out)["timeline_ir"]
    control, = [event for event in ir["events"] if event["meta"].get("rt_condition_control")]
    assert main([*args, "--branch-assumption", f"{control['meta']['branch_id']}=both"]) == 0
    ir = json.loads(capsys.readouterr().out)["timeline_ir"]
    assert {event["meta"].get("branch_comparison_path"): event["meta"]["aligned_t0"]
            for event in ir["events"] if event["kind"] == "stop"} == {None: 80, "taken": 56, "fallthrough": 104}
    assert 'data-time-min="0"' in render_html(ir)
    assert 'data-time-min="-60"' in render_html(ir, default_mode="debug")
