from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "demo-pixi-standalone"
EXAMPLE_DIR = ROOT / "examples" / "three-peak-demo1"


def run_standalone_demo_node(script: str) -> None:
    node_script = f"""
const fs = require("node:fs");
const vm = require("node:vm");
globalThis.window = {{ addEventListener() {{}} }};
vm.runInThisContext(fs.readFileSync({json.dumps(str(DEMO_DIR / "pixi-standalone-demo.js"))}, "utf8"));
const hooks = window.__threePeakTimelineTestHooks;
if (!hooks) {{
  throw new Error("three peak timeline test hooks are unavailable");
}}
{script}
"""
    subprocess.run(
        ["node", "-e", textwrap.dedent(node_script)],
        check=True,
        cwd=ROOT,
    )


def test_three_peak_demo1_example_is_imported() -> None:
    project = yaml.safe_load((EXAMPLE_DIR / "q1timeline.yml").read_text(encoding="utf-8"))
    sequencers = project["sequencers"]

    assert (EXAMPLE_DIR / "README.md").is_file()
    assert (EXAMPLE_DIR / "three-peak-demo.ipynb").is_file()
    assert len(sequencers) == 8
    assert {sequencer["id"] for sequencer in sequencers} >= {
        "qcm_sum_ch1",
        "qcm_ch2_ch3",
        "qrm0_ch1_tracker",
        "qrm0_ch2_tracker",
        "qrm1_ch3_tracker",
    }


def test_three_peak_demo1_generated_ir_contains_feedback_routes() -> None:
    ir = json.loads((EXAMPLE_DIR / ".q1timeline" / "timeline_ir.json").read_text(encoding="utf-8"))

    assert len(ir["events"]) >= 700
    assert len(ir["feedback_flows"]) >= 18
    assert {flow["channel"] for flow in ir["feedback_flows"]} >= {"1", "2", "3"}
    assert any(event["kind"] == "feedback_pop" for event in ir["events"])
    assert any(event["kind"] == "acquire" for event in ir["events"])


def test_standalone_pixi_demo_loads_three_peak_ir_without_legacy_bundle() -> None:
    html = (DEMO_DIR / "index.html").read_text(encoding="utf-8")
    source = (DEMO_DIR / "pixi-standalone-demo.js").read_text(encoding="utf-8")

    assert "Three Peak Demo 1 PixiJS Showcase" in html
    assert "cdn.jsdelivr.net/npm/pixi.js" in html
    assert "pixi-standalone-demo.js" in html
    assert "id=\"pixi-stage\"" in html
    assert "fetch(\"../examples/three-peak-demo1/.q1timeline/timeline_ir.json\")" in source
    assert "q1asm_live_debugger_2" not in html + source
    assert "vscode-extension/dist/pixi-webview.js" not in html + source


def test_standalone_pixi_demo_highlights_feedback_animation() -> None:
    html = (DEMO_DIR / "index.html").read_text(encoding="utf-8")
    source = (DEMO_DIR / "pixi-standalone-demo.js").read_text(encoding="utf-8")

    assert "id=\"showcase-mode\"" in html
    assert "id=\"playback-toggle\"" in html
    assert "id=\"fps-meter\"" in html
    assert "id=\"stress-toggle\"" not in html
    assert "id=\"metric-packets\"" not in html
    assert "Q1 issue to real-time event packets" not in html
    assert "new PIXI.Application()" in source
    assert "app.init" in source
    assert "requestAnimationFrame" in source
    assert "createFeedbackFlows" in source
    assert "drawFeedbackExchange" in source
    assert "createPacketFlows" not in source
    assert "drawPacketFlow" not in source
    assert "drawActiveEventEffects" not in source
    assert "drawStressLayer" not in source


def test_standalone_pixi_demo_reuses_dynamic_pixi_objects_per_frame() -> None:
    source = (DEMO_DIR / "pixi-standalone-demo.js").read_text(encoding="utf-8")
    dynamic_scene = source.split("function drawDynamicScene()", 1)[1].split("function drawPlayhead", 1)[0]
    playhead_scene = source.split("function drawPlayhead", 1)[1].split("function drawFeedbackExchange", 1)[0]

    assert "dynamicGraphics" in source
    assert "playheadText" in source
    assert "clearLayer(state.dynamicLayer)" not in dynamic_scene
    assert "new PIXI.Graphics()" not in dynamic_scene
    assert "makeText(" not in playhead_scene


def test_normal_mode_is_one_compact_row_per_sequencer_with_loop_brackets() -> None:
    source = (DEMO_DIR / "pixi-standalone-demo.js").read_text(encoding="utf-8")

    assert "function isCompactMode" in source
    assert "isCompactMode(mode) ? event.sequencerId : laneKey(event)" in source
    assert "function drawLoopBrackets" in source
    assert "function isLoopOverlayEvent" in source
    assert "drawLoopBrackets(PIXI, root, g)" in source
    assert "if (isCompactMode(state.layout.mode) && isLoopOverlayEvent(event))" in source


def test_horizontal_zoom_uses_time_window_not_canvas_scaling() -> None:
    source = (DEMO_DIR / "pixi-standalone-demo.js").read_text(encoding="utf-8")

    assert "timeWindow: null" in source
    assert "fullTimeExtent: null" in source
    assert "function zoomTimeWindowAt" in source
    assert "function panTimeWindowByPixels" in source
    assert "__threePeakTimelineDebug" in source
    assert "function updateDebugDataset" in source
    assert "timeWindow: state.timeWindow" in source
    assert "state.root.scale.set(1, 1)" in source
    assert "rootScaleY" in source
    assert "state.camera.zoomX" not in source
    assert "state.camera.zoomY" not in source
    assert "state.camera.x" not in source


def test_feedback_packets_depart_on_playhead_emit_and_fly_in_short_window() -> None:
    source = (DEMO_DIR / "pixi-standalone-demo.js").read_text(encoding="utf-8")

    assert "function feedbackPacketProgress" in source
    assert "function feedbackPopProgress" in source
    assert "function feedbackTimelineAgeNs" in source
    assert "function feedbackFlowRole" in source
    assert "FEEDBACK_PACKET_FLIGHT_NS" in source
    assert "FEEDBACK_POP_PULSE_NS" in source
    assert "emitNs" in source
    assert "consumeNs" in source
    assert "fromKind" in source
    assert "toKind" in source
    assert '"feedback_com"' in source
    assert '"feedback_pop"' in source

    feedback_scene = source.split("function drawFeedbackExchange", 1)[1].split("function startAnimationLoop", 1)[0]
    hit_test_scene = source.split("function hitTestFeedback", 1)[1].split("function screenToWorld", 1)[0]
    progress_scene = source.split("function feedbackPacketProgress", 1)[1].split("function isLoopOverlayEvent", 1)[0]

    assert "feedbackPacketProgress(flow)" in feedback_scene
    assert "feedbackPopProgress(flow)" in feedback_scene
    assert "drawFeedbackLaunchMarker(g, from, color, phase)" in feedback_scene
    assert "drawFeedbackPacket(g, from, mid, to, phase, color, radius)" in feedback_scene
    assert "feedbackPacketProgress(flow)" in hit_test_scene
    assert "activeFeedbackCount" in source
    assert "feedbackTimelineAgeNs(emitNs)" in progress_scene
    assert "feedbackTimelineAgeNs(consumeNs)" in progress_scene
    assert "state.playheadNs" in progress_scene
    assert "easeOutCubic" in progress_scene
    assert "consumeNs - emitNs" not in progress_scene
    assert "feedbackClockNs" not in source
    assert "FEEDBACK_PACKET_CLOCK_OFFSET_NS" not in source
    assert "FEEDBACK_PACKET_NS_PER_MS" not in source
    assert "state.playheadNs" not in feedback_scene
    assert "state.playheadNs" not in hit_test_scene
    assert "feedbackFlightProgress" not in source


def test_playhead_moves_fast_enough_for_feedback_demo() -> None:
    source = (DEMO_DIR / "pixi-standalone-demo.js").read_text(encoding="utf-8")

    assert "const PLAYHEAD_NS_PER_MS = 1.5;" in source
    assert "state.sceneTimeMs * PLAYHEAD_NS_PER_MS" in source


def test_compact_event_labels_are_suppressed_when_they_overlap() -> None:
    run_standalone_demo_node(
        """
const events = [
  { id: "play-1", kind: "play", x: 100, y: 40, width: 70, height: 11, labelLayout: { visible: true, text: "play", x: 106, y: 42 } },
  { id: "acq-1", kind: "acquire", x: 116, y: 40, width: 70, height: 11, labelLayout: { visible: true, text: "acq", x: 122, y: 42 } },
  { id: "wait-1", kind: "wait_trigger", x: 240, y: 40, width: 54, height: 10, labelLayout: { visible: true, text: "wait", x: 246, y: 42 } },
];

hooks.applyEventLabelLayout(events);

if (!events[0].labelLayout.visible) {
  throw new Error("the first high-priority label should remain visible");
}
if (events[1].labelLayout.visible) {
  throw new Error("the overlapping acquire label should be hidden");
}
if (!events[2].labelLayout.visible) {
  throw new Error("the separated wait label should remain visible");
}
for (const event of events.filter((item) => item.labelLayout.visible)) {
  const labelWidth = hooks.measureLabelWidth(event.labelLayout.text);
  if (labelWidth > event.width - 6) {
    throw new Error(`label ${event.id} overflows its event box`);
  }
}
"""
    )


def test_badge_and_annotation_hit_targets_are_hoverable() -> None:
    run_standalone_demo_node(
        """
const annotations = [
  { id: "diag-1", type: "diagnostic", x: 45, y: 20, width: 10, height: 10 },
  { id: "loop-1", type: "loop", x: 40, y: 18, width: 60, height: 16 },
];
const annotation = hooks.hitTestAnnotations({ x: 50, y: 24 }, annotations);
if (!annotation || annotation.id !== "loop-1") {
  throw new Error("topmost annotation badge was not hit");
}

const playhead = hooks.hitTestPlayheadBadge(
  { x: 205, y: 35 },
  { timeScale: { tMinNs: 0, tMaxNs: 100, plotLeft: 100, plotRight: 300 } },
  50
);
if (!playhead || playhead.type !== "playhead") {
  throw new Error("playhead badge was not hit");
}
"""
    )
