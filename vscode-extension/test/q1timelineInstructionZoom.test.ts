import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

function functionSource(script: string, name: string) {
  const start = script.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `Missing ${name}`);
  const body = script.indexOf(") {", start) + 2;
  let depth = 0;
  for (let index = body; index < script.length; index += 1) {
    if (script[index] === "{") depth += 1;
    if (script[index] === "}" && --depth === 0) return script.slice(start, index + 1);
  }
  throw new Error(`Unclosed ${name}`);
}

function closeTo(actual: number, expected: number) {
  assert.ok(Math.abs(actual - expected) < 0.000001, `${actual} should equal ${expected}`);
}

function loadInstructionRuntime(options: { count?: number; clientWidth?: number; labelWidth?: number; missingLabel?: boolean } = {}) {
  const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf8");
  const adapter = require("../src/q1timeline/media/timelineAdapter.js");
  function createStage(count: number, blocks: any[] = []) {
    const properties: Record<string, string> = { "--instruction-track-width": `${count * 112}px` };
    const labelWidth = options.labelWidth ?? 160;
    let scrollLeft = 0;
    const stage: any = {
      dataset: { instructionCount: String(count) }, clientWidth: options.clientWidth ?? 960, isConnected: true,
      properties, listeners: {}, nodes: blocks.map((block) => ({
        dataset: { blockId: block.id }, style: {}, classList: { remove() {} },
      })),
      style: { setProperty(name: string, value: string) { properties[name] = value; } },
      querySelector: () => options.missingLabel ? undefined : { getBoundingClientRect: () => ({ width: labelWidth }) },
      querySelectorAll() { return this.nodes; },
      getBoundingClientRect: () => ({ left: 40, width: options.clientWidth ?? 960 }),
      addEventListener(name: string, callback: (event: any) => void) { this.listeners[name] = callback; },
    };
    Object.defineProperty(stage, "scrollLeft", {
      get: () => scrollLeft,
      set(value: number) {
        const plotWidth = Math.max(1, stage.clientWidth - (options.missingLabel ? 168 : labelWidth));
        scrollLeft = Math.max(0, Math.min(value, Math.max(0, parseFloat(properties["--instruction-track-width"]) - plotWidth)));
      },
    });
    return stage;
  }
  const names = ["clamp", "instructionPlotWidth", "setInstructionView", "fitInstructionSelection", "handleInstructionWheel", "installTimelineMouseInteractions", "renderSharedTimelineIfAvailable", "applyTimelineEventFilter", "setTimelineLayout", "timelineControlChipLeft"];
  const noops = ["renderTimelineLayoutToggle", "renderTimelineWindowStatus", "renderTimeBasisToggle", "installSharedQ1IssueControls", "syncSharedSequencerFoldControls", "installTimelineControlChips", "applySharedSelection", "renderWaitFolds", "renderPlaybackFolds", "renderHardwareCursor"];
  return Function("createStage", "adapter", "count", `
    let stage = createStage(count);
    let instructionView = { cellWidth: 112, leftOrdinal: 0 };
    let timelineLayout = "instruction", timelineTimeBasis = "local", sharedTimelineActive = false;
    let selectedTimelineEventId = "rt-4";
    const selectedSourceEventIds = new Set(["rt-4", "q1-9"]);
    const expandedQ1IssueSequencers = new Set();
    const expandedPlaybackLoops = new Set();
    const realState = { window: { min: 400000, max: 401000 }, selection: { start: 400200, end: 400400 }, cursor: 400300 };
    const revealed = [];
    const timelineIr = { events: Array.from({ length: count }, (_, index) => ({
      id: "rt-" + index, kind: "wait", sequencer_id: "Control", t0: index * 1000, t1: (index + 1) * 1000,
    })).concat(Array.from({ length: Math.ceil(count / 2) }, (_, index) => ({
      id: "q1-" + index, kind: "q1_issue", sequencer_id: "Control", t0: index * 4, t1: (index + 1) * 4,
    }))) };
    const root = { querySelector: selector => selector === ".shared-timeline-stage" ? stage : undefined };
    const document = { getElementById: id => id === "timeline-root" ? root : undefined };
    const window = { q1timelineTimelineAdapter: adapter, q1lensSharedTimeline: {
      renderTimeline(_root, model) {
        stage.isConnected = false;
        stage = createStage(model.viewport.end, model.lanes.flatMap(lane => lane.blocks));
        if (timelineLayout !== "instruction") delete stage.dataset.instructionCount;
      },
    } };
    function timelineContainer() { return stage; }
    function currentTimelineMode() { return "normal"; }
    function currentTimelineWindow() { return realState.window; }
    function sharedTimelineViewport() { return { start: realState.window.min, end: realState.window.max }; }
    function sharedTimelineTicks() { return []; }
    function sharedTimelineSelectionRange() { return realState.selection; }
    function writePersistedWebviewState() {}
    function handleTimelineWheel() { throw new Error("Instruction gesture reached real-time zoom"); }
    function handleTimelinePointerDown() { throw new Error("Instruction gesture reached hardware cursor"); }
    ${noops.map((name) => `function ${name}() {}`).join("\n")}
    ${names.map((name) => functionSource(script, name)).join("\n")}
    return {
      stage: () => stage, view: () => ({ ...instructionView }), realState: () => realState,
      plotWidth: () => instructionPlotWidth(stage),
      set(cellWidth, leftOrdinal) { setInstructionView(stage, cellWidth, leftOrdinal); },
      fit(indices) { fitInstructionSelection(indices.map(index => ({
        dataset: { instructionIndex: String(index) }, scrollIntoView(options) { revealed.push({ index, options }); },
      }))); },
      revealed, switchLayout: setTimelineLayout, chipLeft: timelineControlChipLeft,
      install: installTimelineMouseInteractions,
      wheel(values) {
        let prevented = false;
        handleInstructionWheel({ currentTarget: stage, clientX: 600, deltaY: 0, deltaX: 0, ...values, preventDefault() { prevented = true; } });
        return prevented;
      },
      render(layout = "instruction") { timelineLayout = layout; return renderSharedTimelineIfAvailable(); },
    };
  `)(createStage, adapter, options.count ?? 20);
}

describe("Q1 timeline instruction zoom", () => {
  it("clamps full-range and single-instruction zoom to the native plot width", () => {
    const runtime = loadInstructionRuntime();
    assert.equal(runtime.plotWidth(), 800);
    runtime.set(1, 900);
    assert.deepEqual(runtime.view(), { cellWidth: 40, leftOrdinal: 0 });
    assert.equal(runtime.stage().properties["--instruction-track-width"], "800px");
    runtime.set(10_000, 900);
    assert.deepEqual(runtime.view(), { cellWidth: 800, leftOrdinal: 19 });
    assert.equal(runtime.stage().properties["--instruction-cell-width"], "800px");
    assert.equal(runtime.stage().scrollLeft, 15_200);
    runtime.set(100, -100);
    assert.deepEqual(runtime.view(), { cellWidth: 100, leftOrdinal: 0 });
    assert.equal(loadInstructionRuntime({ missingLabel: true }).plotWidth(), 792);
    assert.equal(loadInstructionRuntime({ clientWidth: 100 }).plotWidth(), 1);
  });

  it("fits the union of selected Q1 and RT slots with padding and leaves empty selections unchanged", () => {
    const runtime = loadInstructionRuntime();
    runtime.set(100, 10);
    runtime.fit([12, 2, 12]);
    closeTo(runtime.view().cellWidth, 800 / 12.1);
    closeTo(runtime.view().leftOrdinal, 1.45);
    const left = runtime.view().leftOrdinal;
    const right = left + runtime.plotWidth() / runtime.view().cellWidth;
    assert.ok(left < 2 && right > 13, "All selected lane slots must be visible, including the complete last block");
    const before = runtime.view();
    runtime.fit([]);
    assert.deepEqual(runtime.view(), before);
    assert.deepEqual(runtime.revealed, [{ index: 12, options: { block: "nearest", inline: "nearest" } }]);
    runtime.fit([0, 19]);
    assert.deepEqual(runtime.view(), { cellWidth: 40, leftOrdinal: 0 });
    runtime.fit([19]);
    assert.ok(runtime.view().leftOrdinal + runtime.plotWidth() / runtime.view().cellWidth >= 20);
  });

  it("anchors wheel zoom under the pointer after excluding the sticky lane labels", () => {
    const runtime = loadInstructionRuntime();
    runtime.set(100, 2);
    const anchor = runtime.view().leftOrdinal + 400 / runtime.view().cellWidth;
    assert.equal(runtime.wheel({ deltaY: -100 }), true);
    closeTo(runtime.view().cellWidth, 125);
    closeTo(runtime.view().leftOrdinal + 400 / runtime.view().cellWidth, anchor);
    assert.equal(runtime.wheel({ deltaY: 100 }), true);
    closeTo(runtime.view().cellWidth, 100);
    closeTo(runtime.view().leftOrdinal, 2);
    runtime.wheel({ deltaY: -100, clientX: 80 });
    closeTo(runtime.view().leftOrdinal, 2);
    const before = runtime.view();
    assert.equal(runtime.wheel({ deltaX: 0, deltaY: 0 }), false);
    assert.deepEqual(runtime.view(), before);
  });

  it("pans with shift-wheel or horizontal deltas while native scrolling enforces bounds", () => {
    const runtime = loadInstructionRuntime();
    runtime.set(100, 2);
    runtime.wheel({ deltaY: 120, shiftKey: true });
    closeTo(runtime.view().leftOrdinal, 3.2);
    runtime.wheel({ deltaX: 80, deltaY: -100 });
    closeTo(runtime.view().leftOrdinal, 4);
    assert.equal(runtime.view().cellWidth, 100);
    runtime.wheel({ deltaX: -10_000 });
    assert.equal(runtime.view().leftOrdinal, 0);
    runtime.wheel({ deltaX: 10_000 });
    assert.equal(runtime.view().leftOrdinal, 12);
  });

  it("retains zoom and native scroll through rerenders and time-layout round trips", () => {
    const runtime = loadInstructionRuntime();
    const realState = JSON.parse(JSON.stringify(runtime.realState()));
    runtime.render();
    assert.deepEqual(Object.keys(runtime.stage().listeners).sort(), ["scroll", "wheel"]);
    assert.equal(runtime.stage().style.touchAction, undefined);
    runtime.set(150, 3);
    runtime.stage().scrollLeft = 675;
    runtime.stage().listeners.scroll();
    assert.deepEqual(runtime.view(), { cellWidth: 150, leftOrdinal: 4.5 });
    const previousStage = runtime.stage();
    runtime.render();
    assert.notEqual(runtime.stage(), previousStage);
    assert.equal(runtime.stage().scrollLeft, 675);
    assert.equal(runtime.stage().properties["--instruction-cell-width"], "150px");
    previousStage.scrollLeft = 0;
    previousStage.listeners.scroll();
    assert.deepEqual(runtime.view(), { cellWidth: 150, leftOrdinal: 4.5 }, "A detached stage must not overwrite current scroll memory");
    const selectedNode = runtime.stage().nodes.find((node: any) => node.dataset.blockId === "q1-9");
    assert.equal(selectedNode.dataset.instructionIndex, "9");
    assert.equal(selectedNode.style.left, "calc(var(--instruction-cell-width) * 9)");
    assert.equal(selectedNode.style.width, "var(--instruction-cell-width)");
    runtime.stage().scrollLeft = 720;
    runtime.render();
    closeTo(runtime.view().leftOrdinal, 4.8);
    assert.equal(runtime.stage().scrollLeft, 720, "Rerender must capture scrolling before its native event is delivered");
    runtime.stage().scrollLeft = 750;
    runtime.switchLayout("time");
    assert.deepEqual(Object.keys(runtime.stage().listeners).sort(), ["pointerdown", "wheel"]);
    runtime.switchLayout("instruction");
    assert.deepEqual(runtime.view(), { cellWidth: 150, leftOrdinal: 5 }, "Switching before a queued scroll event must still capture the latest offset");
    assert.equal(runtime.stage().scrollLeft, 750);
    assert.deepEqual(runtime.realState(), realState);
  });

  it("keeps branch controls centered on distinct instruction slots as cell width changes", () => {
    const runtime = loadInstructionRuntime();
    const first = { dataset: { instructionIndex: "2" }, style: { left: "calc(var(--instruction-cell-width) * 2)", width: "var(--instruction-cell-width)" } };
    const second = { dataset: { instructionIndex: "9" }, style: { left: "calc(var(--instruction-cell-width) * 9)", width: "var(--instruction-cell-width)" } };
    const positions = [runtime.chipLeft(first), runtime.chipLeft(second)];
    assert.deepEqual(positions, ["calc(var(--instruction-cell-width) * 2.5)", "calc(var(--instruction-cell-width) * 9.5)"]);
    runtime.set(100, 2);
    runtime.wheel({ deltaY: -100 });
    assert.deepEqual([runtime.chipLeft(first), runtime.chipLeft(second)], positions);
    assert.equal(runtime.stage().properties["--instruction-cell-width"], "125px");
  });
});
