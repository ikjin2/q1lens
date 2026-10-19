import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

type Q1TimelineAxisFunctions = {
  timelineTicks: (min: number, max: number) => number[];
  formatWindowValue: (value: number) => string;
};

type Q1TimelineLabelFunctions = {
  eventInlineLabel: (event: Record<string, unknown>, width: number) => string;
  eventLabelToken: (event: Record<string, unknown>) => string;
};

type Q1TimelineControlChipFunctions = {
  timelineControlChipLeft: (node: { style?: { left?: string; width?: string } }) => string;
  timelineControlChipStackKey: (node: { style?: { left?: string; width?: string }; closest?: (selector: string) => any }) => string;
  timelineControlChipTop: (stackIndex: number) => string;
};

type Q1TimelineZoomFunctions = {
  timelineWindowForRange: (
    start: number,
    end: number,
    full: { min: number; max: number },
    minSpanNs?: number,
  ) => { min: number; max: number } | undefined;
  isTimelineSelectionTarget: (target: { closest?: (selector: string) => unknown }) => boolean;
};

type Q1TimelineHighlightFunctions = {
  highlightedSpanNeedsZoom: (
    start: number,
    end: number,
    window: { min: number; max: number },
    plotPixelWidth: number,
    minPixels?: number,
  ) => boolean;
};

type Q1TimelineLoopPreviewFunctions = {
  loopPreviewEventIds: (meta: Record<string, unknown>) => string[];
};

function extractFunctionSource(script: string, name: string): string | undefined {
  const start = script.indexOf(`function ${name}(`);
  if (start < 0) {
    return undefined;
  }
  const signatureEnd = script.indexOf(") {", start);
  if (signatureEnd < 0) {
    throw new Error(`Function ${name} has no body`);
  }
  const bodyStart = signatureEnd + 2;
  let depth = 0;
  for (let index = bodyStart; index < script.length; index += 1) {
    const char = script[index];
    if (char === "{") {
      depth += 1;
    } else if (char === "}") {
      depth -= 1;
      if (depth === 0) {
        return script.slice(start, index + 1);
      }
    }
  }
  throw new Error(`Function ${name} body did not close`);
}

function loadQ1TimelineAxisFunctions(script: string): Q1TimelineAxisFunctions {
  const sources = ["snapTimelineNs", "timelineTicks", "formatWindowValue"]
    .map((name) => extractFunctionSource(script, name))
    .filter((source): source is string => Boolean(source));
  const loaded = Function(`${sources.join("\n")}\nreturn { timelineTicks, formatWindowValue };`)();
  return loaded as Q1TimelineAxisFunctions;
}

function loadQ1TimelineLabelFunctions(script: string): Q1TimelineLabelFunctions {
  const sources = ["q1IssueCommandToken", "eventLabelToken", "fitInlineLabel", "estimateLabelWidth", "eventInlineLabel"]
    .map((name) => extractFunctionSource(script, name))
    .filter((source): source is string => Boolean(source));
  const loaded = Function(`${sources.join("\n")}\nreturn { eventInlineLabel, eventLabelToken };`)();
  return loaded as Q1TimelineLabelFunctions;
}

function loadQ1TimelineControlChipFunctions(script: string): Q1TimelineControlChipFunctions {
  const sources = ["timelineControlChipLeft", "timelineControlChipStackKey", "timelineControlChipTop"]
    .map((name) => extractFunctionSource(script, name))
    .filter((source): source is string => Boolean(source));
  const loaded = Function(`${sources.join("\n")}\nreturn { timelineControlChipLeft, timelineControlChipStackKey, timelineControlChipTop };`)();
  return loaded as Q1TimelineControlChipFunctions;
}

function loadQ1TimelineZoomFunctions(script: string): Q1TimelineZoomFunctions {
  const sources = ["clamp", "timelineWindowForRange", "isTimelineSelectionTarget"]
    .map((name) => extractFunctionSource(script, name))
    .filter((source): source is string => Boolean(source));
  const loaded = Function(`${sources.join("\n")}\nreturn { timelineWindowForRange, isTimelineSelectionTarget };`)();
  return loaded as Q1TimelineZoomFunctions;
}

function loadQ1TimelineHighlightFunctions(script: string): Q1TimelineHighlightFunctions {
  const sources = ["highlightedSpanNeedsZoom"]
    .map((name) => extractFunctionSource(script, name))
    .filter((source): source is string => Boolean(source));
  const loaded = Function(`${sources.join("\n")}\nreturn { highlightedSpanNeedsZoom };`)();
  return loaded as Q1TimelineHighlightFunctions;
}

function loadQ1TimelineLoopPreviewFunctions(script: string): Q1TimelineLoopPreviewFunctions {
  const sources = ["loopPreviewEventIds"]
    .map((name) => extractFunctionSource(script, name))
    .filter((source): source is string => Boolean(source));
  const loaded = Function(`${sources.join("\n")}\nreturn { loopPreviewEventIds };`)();
  return loaded as Q1TimelineLoopPreviewFunctions;
}

function loadQ1TimelineFoldRuntime(script: string, timelineIr: any, options: any = {}) {
  const expandedState = script.match(/let timelineExpandedGaps = [\s\S]*?;\r?\n/)?.[0];
  assert.ok(expandedState, "Missing persisted gap initialization");
  const sources = [
    "readPersistedWebviewState", "writePersistedWebviewState", "timelineExtentMatches",
    "snapTimelineNs", "formatWindowValue", "formatFoldDuration", "timelineTimeScale", "timelineWaitTimeScale",
    "displayTimelineTime", "realTimelineTime", "timelineDisplayWindow", "toggleTimelineGap",
    "toggleTimelineFolds", "renderWaitFolds", "clamp", "sharedTimelinePointerAnchorRatio",
    "timelinePointerAnchorRatio", "timelineTimeFromPointer", "renderHardwareCursor", "isTimelineSelectionTarget",
    "eventIsQ1Issue", "sourceRangeTimelineBounds", "zoomTimelineToEventRange",
    "timelineWindowForRange", "setTimelineWindow", "writeTimelineWindowState",
    "setTimelineLayout", "installTimelineMouseInteractions", "zoomTimelineToEvent", "zoomTimelineToHighlightedEvent",
  ].map((name) => {
    const source = extractFunctionSource(script, name);
    assert.ok(source, `Missing ${name}`);
    return source;
  });
  let activeElement: any;
  function element(tagName: string, className = ""): any {
    const node: any = {
      tagName: tagName.toUpperCase(), className, children: [], dataset: {}, style: {}, attributes: {}, listeners: {}, scrollLeft: 0,
      setAttribute(name: string, value: string) { this.attributes[name] = value; },
      getAttribute(name: string) { return this.attributes[name]; },
      focus() { activeElement = this; },
      addEventListener(name: string, listener: (event: any) => void) { this.listeners[name] = listener; },
      appendChild(child: any) { child.parentElement = this; this.children.push(child); return child; },
      append(...children: any[]) { children.forEach((child) => this.appendChild(child)); },
      remove() { this.parentElement.children = this.parentElement.children.filter((child: any) => child !== this); },
      getBoundingClientRect: () => ({ left: 100, width: 1000 }),
      querySelector(selector: string) { return this.children.find((child: any) => child.classList.contains(selector.slice(1))); },
      closest(selector: string) {
        if (this.tagName === "BUTTON" && selector.includes("button")) return this;
        return this.parentElement?.closest(selector);
      },
    };
    node.classList = { contains: (name: string) => node.className.split(" ").includes(name) };
    return node;
  }
  const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
  return Function("timelineIr", "adapter", "options", "element", "getActiveElement", `
    let savedState = options.state || {};
    const vscode = { getState: () => savedState, setState: state => { savedState = state; } };
    const window = { q1timelineTimelineAdapter: adapter };
    const timelineEventsById = new Map((timelineIr.events || []).map(event => [String(event.id), event]));
    let timelineTimeBasis = options.basis || "local";
    let timelineLayout = "time";
    let instructionView = { cellWidth: 112, leftOrdinal: 0 };
    let timelineWaitsCollapsed = savedState.timelineWaitsCollapsed !== false;
    const currentFoldState = true;
    ${expandedState}
    const timelineTimeScales = new Map();
    const expandedPlaybackLoops = new Set();
    const visiblePlaybackEvent = event => event;
    let timelineWindow = options.current || { min: 0, max: 10000 };
    let timelineSelectionRange = { start: 4900, end: 5100 };
    let selectedTimelineEventId = "selected";
    let selectedSourceEventIds = new Set();
    const hardwareView = { getState: () => ({ time: 5050 }) };
    const tracks = [element("div", "ruler"), element("div", "lane-track"), element("div", "lane-track")];
    const toolbar = element("button");
    const document = {
      get activeElement() { return getActiveElement(); },
      createElement: tag => element(tag),
      getElementById: id => id === "q1timeline-wait-fold-button" ? toolbar : undefined,
      querySelector: () => tracks[1],
      querySelectorAll: selector => selector === ".q1timeline-wait-fold"
        ? tracks.flatMap(track => track.children.filter(child => child.classList.contains("q1timeline-wait-fold")))
        : selector === ".q1timeline-fold-toggle"
          ? tracks.flatMap(track => track.children.flatMap(child => child.children.filter(node => node.classList.contains("q1timeline-fold-toggle"))))
          : tracks,
    };
    let renders = 0;
    const sourceSelectionsAtRender = [];
    const scrolledEventIds = [];
    function timelineSvg() {}
    function timelineContainer() { return tracks[1]; }
    function currentTimelineMode() { return "normal"; }
    function renderTimelineLayoutToggle() {}
    function renderSharedTimelineIfAvailable() {
      tracks.forEach(track => { delete track.dataset.timelineMouseInteractions; track.listeners = {}; });
      applyTimelineScale();
    }
    function findVisibleEventNodeByIds(ids) {
      return { scrollIntoView() { scrolledEventIds.push(...ids); } };
    }
    function handleTimelineWheel() {}
    function handleInstructionWheel() {}
    function handleTimelinePointerDown() {}
    function fullTimelineWindow() { return options.full || { min: 0, max: 10000 }; }
    function currentTimelineWindow() { return timelineWindow; }
    function setIconOnlyToolbarButton(node, icon, title) { node.title = title; }
    function applyTimelineScale() {
      renders += 1;
      sourceSelectionsAtRender.push(Array.from(selectedSourceEventIds));
      tracks.forEach(track => { track.children = []; });
      renderWaitFolds();
      renderHardwareCursor();
    }
    ${sources.join("\n")}
    return {
      tracks, toolbar, timelineTimeScale: timelineWaitTimeScale, displayTimelineTime, realTimelineTime, formatFoldDuration,
      timelineTimeFromPointer, renderWaitFolds, renderHardwareCursor, toggleTimelineGap, toggleTimelineFolds, isTimelineSelectionTarget,
      sourceRangeTimelineBounds, zoomTimelineToEventRange,
      setTimelineLayout, installTimelineMouseInteractions, zoomTimelineToHighlightedEvent, scrolledEventIds,
      selectedSources: () => Array.from(selectedSourceEventIds), sourceSelectionsAtRender,
      layout: () => timelineLayout,
      activeElement: () => document.activeElement,
      state: () => savedState,
      preserved: () => ({ window: timelineWindow, selection: timelineSelectionRange, selected: selectedTimelineEventId, time: hardwareView.getState().time }),
      renders: () => renders,
    };
  `)(timelineIr, adapter, options, element, () => activeElement);
}

function loadQ1TimelineZoomRuntime(script: string, events: any[], options: any = {}) {
  const full = options.full || { min: 0, max: 40_160_000 };
  let savedState = options.state || {};
  const sources = [
    "concreteTime", "eventTime", "initialTimelineWindow", "fullTimelineWindow",
    "currentTimelineWindow", "timelineExtentMatches", "persistedTimelineWindowFor",
    "readPersistedWebviewState", "writePersistedWebviewState", "writeTimelineWindowState",
    "snapTimelineNs", "clamp", "timelineWindowForRange", "setTimelineWindow",
    "normalizedTimelineSelectionRange", "zoomTimelineSelection", "eventZoomTime",
    "zoomTimelineToEvent", "zoomTimelineAround", "restoreTimelineZoom",
    "fitTimelineToWindow", "resetTimelineZoom",
    "displayTimelineTime", "realTimelineTime", "timelineDisplayWindow", "setTimelineDisplayWindow",
    "timelineTicks", "visibleTimelineTicks", "formatWindowValue", "sharedTimelineTicks", "scaleTimelineTime",
    "timelineSvgViewBoxWidth", "timelinePlotPixelWidth", "sharedTimelinePlotPixelWidth",
    "timelinePointerAnchorRatio", "sharedTimelinePointerAnchorRatio", "timelineTimeFromPointer",
    "panTimelineByPixels", "panTimeline", "handleTimelineWheel", "renderHardwareCursor",
    "highlightedSpanNeedsZoom", "zoomTimelineToHighlightedEvent",
  ].map((name) => {
    const source = extractFunctionSource(script, name);
    assert.ok(source, `Missing ${name}`);
    return source;
  });
  const vscode = {
    getState: () => savedState,
    setState: (state: any) => { savedState = state; },
  };
  return Function("events", "full", "basis", "vscode", "scale", `
    const window = {};
    const timelineEventsById = new Map(events.map(event => [String(event.id), event]));
    let timelineTimeBasis = basis;
    let timelineLayout = "time";
    let timelineWindow, timelineSelectionRange;
    const cursor = { style: {}, hidden: false };
    const track = {
      getBoundingClientRect: () => ({ left: 100, width: 1000 }),
      querySelector: () => cursor,
    };
    const document = { querySelector: () => track, querySelectorAll: () => [track] };
    function timelineSvg() {}
    function timelineGeometry() { return full; }
    function timelineTimeScale() { return scale; }
    const visiblePlaybackEvent = event => event;
    function applyTimelineScale() {}
    function syncTimelineZoomActionState() {}
    const scrolledEventIds = [];
    function findVisibleEventNodeByIds(ids) {
      return { scrollIntoView() { scrolledEventIds.push(...ids); } };
    }
    ${sources.join("\n")}
    return {
      currentTimelineWindow, restoreTimelineZoom, fitTimelineToWindow, resetTimelineZoom,
      zoomTimelineToEvent, zoomTimelineAround, setTimelineWindow,
      timelineTimeFromPointer, handleTimelineWheel, panTimeline, sharedTimelineTicks, scaleTimelineTime,
      zoomTimelineToHighlightedEvent, renderHardwareCursor,
      cursor: () => cursor,
      setTimeScale(next) { scale = next; },
      setLayout(next) { timelineLayout = next; },
      scrolledEventIds,
      selectRange(start, end) { timelineSelectionRange = { start, end }; zoomTimelineSelection(); },
      selection: () => timelineSelectionRange,
      state: () => vscode.getState(),
    };
  `)(events, full, options.basis || "local", vscode, options.scale);
}

describe("timeline webview assets", () => {
  it("shows readable before/after values and a textual status for changed events", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const sources = ["escapeHtml", "displayInspectorValue", "addInspectorField", "appendDiffInspectorFields"]
      .map((name) => extractFunctionSource(script, name));
    assert.ok(sources.every(Boolean));
    const append = Function(`${sources.join("\n")}\nreturn appendDiffInspectorFields;`)();
    const before = { label: "wait", t0: { value: 20 }, t1: { value: 100 }, duration: { value: 80 }, source: { raw: "wait 80" } };
    const event = { ...before, t1: { value: 60 }, duration: { value: 40 }, source: { raw: "wait <40>" }, meta: { diff_status: "shifted", diff_before: before } };
    const fields: string[] = [];
    append(fields, event);
    const html = fields.join("");
    assert.match(html, /Shifted/);
    assert.match(html, /Before duration \(ns\)<\/dt><dd>80/);
    assert.match(html, /After duration \(ns\)<\/dt><dd>40/);
    assert.match(html, /wait &lt;40&gt;/);
    assert.doesNotMatch(html, /wait <40>/);
    const removed: string[] = [];
    append(removed, { ...before, meta: { diff_status: "removed" } });
    assert.match(removed.join(""), /After<\/dt><dd>Absent/);
    const unchanged: string[] = [];
    append(unchanged, before);
    assert.deepEqual(unchanged, []);
    const css = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.css"), "utf-8");
    for (const status of ["added", "removed", "changed", "shifted"]) {
      assert.ok(css.includes(`.timeline-block.diff-${status}`));
    }
  });

  it("makes nonempty provenance legend categories keyboard-accessible navigation buttons", () => {
    const script = readFileSync(join(__dirname, "..", "src", "qbs", "webview", "assets", "timeline.js"), "utf-8");
    const source = extractFunctionSource(script, "renderProvenanceLegend");
    const targets: string[] = [];
    const element = (tag: string, className: string, text?: string): any => ({
      tag, className, text, dataset: {}, children: [], attributes: {},
      setAttribute(name: string, value: string) { this.attributes[name] = value; },
      appendChild(child: any) { this.children.push(child); },
    });
    const render = Function("element", "button", "revealProvenance", `${source}\nreturn renderProvenanceLegend;`)(
      element, (className: string, text: string, click: () => void) => ({ ...element("button", className, text), click }),
      (status: string) => targets.push(status),
    );
    const legend = render({ ariaLabel: "coverage", entries: [
      { status: "compiler", label: "Compiler", count: 2 },
      { status: "unmapped", label: "Unmapped", count: 0 },
    ] });
    const items = legend.children[1].children;
    const first = items[0].children[0];
    assert.equal(first.tag, "button");
    first.click();
    assert.deepEqual(targets, ["compiler"]);
    assert.equal(items[1].children[0].disabled, true);
  });

  it("reveals and focuses provenance targets after clearing an unrelated viewport", () => {
    const script = readFileSync(join(__dirname, "..", "src", "qbs", "webview", "assets", "timeline.js"), "utf-8");
    const source = extractFunctionSource(script, "revealProvenance");
    const target = { dataset: { blockId: "pulse:test" }, focus: () => { focused = true; }, scrollIntoView: () => { scrolled = true; } };
    let focused = false;
    let scrolled = false;
    const reveal = Function("root", "timelineModel", `${source}
      let currentIr = {}, selectedId = null, viewport = { start: 0, end: 1 }, selectionRange = {}, activeInspectorTab = "summary";
      const expandedGroups = new Set();
      let renderedId;
      const render = (_ir, id) => { renderedId = id; };
      revealProvenance("inferred");
      return { viewport, selectionRange, activeInspectorTab, expandedGroups: [...expandedGroups], renderedId };`)(
      { querySelectorAll: () => [target] },
      { provenanceSelection: () => ({ blockId: "pulse:test", groupId: "target:q0" }) },
    );
    assert.deepEqual(reveal, { viewport: null, selectionRange: null, activeInspectorTab: "lowering", expandedGroups: ["target:q0"], renderedId: "pulse:test" });
    assert.ok(focused && scrolled);
    assert.match(extractFunctionSource(script, "renderBlock") || "", /node\.dataset\.blockId = block\.id/);
  });

  it("distinguishes the displayed baseline from a pending save or clear", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const source = extractFunctionSource(script, "renderComparisonStatus");
    const node = { textContent: "", title: "" };
    const render = Function("document", `${source}\nreturn renderComparisonStatus;`)({ getElementById: () => node });
    render({ mode: "saved", nextMode: "previous", pending: true });
    assert.match(node.textContent, /Current comparison: saved baseline/);
    assert.match(node.textContent, /Next analysis: previous analysis. Reanalyze/);
    render({ mode: "previous" });
    assert.equal(node.textContent, "Current comparison: previous analysis.");
  });

  it("defines the shared renderer asset contract", () => {
    const sharedRenderer = readFileSync(join(__dirname, "..", "src", "shared", "timeline", "renderer.js"), "utf-8");
    const sharedCss = readFileSync(join(__dirname, "..", "src", "shared", "timeline", "renderer.css"), "utf-8");
    const copyScript = readFileSync(join(__dirname, "..", "..", "scripts", "copy-extension-assets.js"), "utf-8");
    const qbsCss = readFileSync(join(__dirname, "..", "src", "qbs", "webview", "assets", "timeline.css"), "utf-8");

    assert.match(sharedRenderer, /q1lensSharedTimeline/);
    assert.match(sharedRenderer, /function renderTimeline/);
    assert.match(sharedRenderer, /function blockStyle/);
    assert.match(sharedRenderer, /renderBlock\(block, model\.viewport, handlers\)/);
    assert.doesNotMatch(sharedRenderer, /function laneViewport\(lane, model\)/);
    assert.match(sharedRenderer, /block\.accentColor/);
    assert.match(sharedRenderer, /--timeline-block-accent/);
    assert.match(sharedRenderer, /module\.exports = api/);
    assert.match(sharedCss, /\.shared-timeline-stage/);
    assert.match(sharedCss, /\.timeline-block/);
    assert.match(sharedCss, /\.timeline-block\s*{[^}]*var\(--timeline-block-accent, var\(--vscode-charts-yellow/s);
    assert.match(sharedCss, /\.timeline-block\s*{[^}]*border-color:\s*color-mix\(in srgb, var\(--timeline-block-accent, var\(--vscode-panel-border\)\) 72%, transparent\);/s);
    assert.match(sharedCss, /var\(--timeline-block-accent, var\(--vscode-charts-purple\)\)/);
    assert.match(sharedCss, /font-family:\s*var\(--vscode-font-family\)/);
    assert.match(sharedCss, /font-size:\s*var\(--vscode-font-size\)/);
    assert.match(qbsCss, /font-family:\s*var\(--vscode-font-family\)/);
    assert.match(qbsCss, /font-size:\s*var\(--vscode-font-size\)/);
    assert.match(copyScript, /src\/shared\/timeline/);
  });

  it("maps q1timeline events to the shared timeline model", () => {
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    const model = adapter.buildQ1TimelineSharedModel({
      events: [
        {
          id: "seq0:play",
          sequencer_id: "seq0",
          lane: "rt.play",
          kind: "play",
          t0: { value: 0 },
          t1: { value: 40 },
          duration: { value: 40 },
          label: "play",
          confidence: "exact",
          source: { file: "seq0.q1asm", line: 1 },
        },
        {
          id: "seq0:q1",
          sequencer_id: "seq0",
          lane: "debug.q1_issue",
          kind: "q1_issue",
          t0: { value: 0 },
          t1: { value: 4 },
          duration: { value: 4 },
          label: "wait",
          confidence: "exact",
          source: { raw: "wait_sync 4", file: "seq0.q1asm", line: 1 },
        },
      ],
    }, { mode: "normal" });

    assert.equal(model.lanes[0].label, "seq0");
    assert.equal(model.lanes.some((lane: any) => lane.kind === "q1-issue"), true);
    assert.equal(model.lanes.find((lane: any) => lane.kind === "q1-issue").hidden, true);
    assert.equal(
      model.lanes.flatMap((lane: any) => lane.blocks).find((block: any) => block.id === "seq0:q1").label,
      "wait_sync",
    );
    assert.equal(
      model.lanes.flatMap((lane: any) => lane.blocks).find((block: any) => block.id === "seq0:play").accentColor,
      "var(--q1asm-rt-color, #8bcf9a)",
    );
    assert.equal(
      model.lanes.flatMap((lane: any) => lane.blocks).find((block: any) => block.id === "seq0:q1").accentColor,
      "var(--q1asm-sync-color, #b9a7dc)",
    );

    const expandedModel = adapter.buildQ1TimelineSharedModel({
      events: [
        {
          id: "seq0:q1",
          sequencer_id: "seq0",
          lane: "debug.q1_issue",
          kind: "q1_issue",
          t0: { value: 0 },
          t1: { value: 4 },
          duration: { value: 4 },
          label: "wait",
          confidence: "exact",
          source: { raw: "wait_sync 4", file: "seq0.q1asm", line: 1 },
        },
      ],
    }, { mode: "normal", expandedQ1IssueSequencers: ["seq0"] });
    assert.equal(expandedModel.lanes.find((lane: any) => lane.kind === "q1-issue").hidden, false);
  });

  it("keeps q1 issue lanes on the actual shared timescale", () => {
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    const model = adapter.buildQ1TimelineSharedModel({
      events: [
        {
          id: "seq0:long-wait",
          sequencer_id: "seq0",
          lane: "rt.wait",
          kind: "wait",
          t0: { value: 0 },
          t1: { value: 10000 },
          duration: { value: 10000 },
          label: "wait",
          confidence: "exact",
          source: { file: "seq0.q1asm", line: 1 },
        },
        {
          id: "seq0:q1-branch",
          sequencer_id: "seq0",
          lane: "debug.q1_issue",
          kind: "q1_issue",
          t0: { value: 60 },
          t1: { value: 76 },
          duration: { value: 16 },
          label: "jl",
          confidence: "assumed",
          source: { raw: "jl @done", file: "seq0.q1asm", line: 15 },
        },
        {
          id: "seq0:q1-target",
          sequencer_id: "seq0",
          lane: "debug.q1_issue",
          kind: "q1_issue",
          t0: { value: 160 },
          t1: { value: 176 },
          duration: { value: 16 },
          label: "set_freq",
          confidence: "exact",
          source: { raw: "set_freq R15", file: "seq0.q1asm", line: 78 },
        },
      ],
    }, { mode: "normal", expandedQ1IssueSequencers: ["seq0"] });

    const q1Lane = model.lanes.find((lane: any) => lane.kind === "q1-issue");
    assert.equal(q1Lane.viewport, undefined);
    assert.equal(q1Lane.scale, undefined);
    assert.equal(model.viewport.end, 10000);
    assert.deepEqual(q1Lane.blocks.map((block: any) => [block.id, block.start, block.duration]), [
      ["seq0:q1-branch", 60, 16],
      ["seq0:q1-target", 160, 16],
    ]);
  });

  it("aliases packet state mirrors to RT commands without hiding independent state events", () => {
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    const event = (id: string, kind: string, start: number, end: number, meta: any = {}, sequencer = "seq0") => ({
      id, kind, sequencer_id: sequencer, lane: `rt.${kind}`, t0: { value: start }, t1: { value: end },
      confidence: "exact", source: { file: "seq0.q1asm", line: 1 }, meta: { rt_packet_id: "p0", ...meta },
    });
    const ir = { events: [
      event("update", "upd_param", 0, 4),
      event("marker", "marker_state", 0, 4),
      event("applied", "latched_state_applied", 0, 4),
      event("other-packet", "marker_state", 0, 4, { rt_packet_id: "p1" }),
      event("other-time", "marker_state", 4, 8),
      event("other-sequencer", "marker_state", 0, 4, {}, "seq1"),
      event("other-branch", "marker_state", 0, 4, { branch_comparison_path: "taken" }),
      event("other-preview", "marker_state", 0, 4, { loop_preview: "L0", loop_iteration_index: 1 }),
    ] };
    const before = JSON.stringify(ir);
    for (const mode of ["normal", "debug"]) {
      for (const layout of ["time", "instruction"]) {
        const model = adapter.buildQ1TimelineSharedModel(ir, { mode, layout });
        const blocks = model.lanes.flatMap((lane: any) => lane.blocks);
        const command = blocks.find((block: any) => block.id === "update");
        assert.deepEqual(command.eventIds, ["update", "marker", "applied"]);
        assert.deepEqual(command.domain.eventIds, command.eventIds);
        assert.equal(blocks.some((block: any) => ["marker", "applied"].includes(block.id)), false);
        for (const id of ["other-packet", "other-time", "other-sequencer", "other-branch", "other-preview"]) {
          assert.ok(blocks.some((block: any) => block.id === id), `${mode}/${layout}: ${id}`);
        }
      }
    }
    assert.equal(JSON.stringify(ir), before);
  });

  it("keeps merged play state aliases within their packet preview occurrence", () => {
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    const event = (id: string, kind: string, iteration: number, path?: number) => ({
      id, kind, sequencer_id: "seq0", lane: path === undefined ? "rt.marker" : `rt.path${path}`,
      t0: { value: iteration * 100 }, t1: { value: iteration * 100 + 4 }, confidence: "exact",
      source: { file: "seq0.q1asm", line: 1 }, meta: { rt_packet_id: "p0", path,
        loop_preview: "L0", loop_iteration_index: iteration },
    });
    const ir = { events: [0, 1].flatMap((iteration) => [
      event(`play${iteration}-0`, "play", iteration, 0), event(`play${iteration}-1`, "play", iteration, 1),
      event(`marker${iteration}`, "marker_state", iteration),
    ]) };
    for (const layout of ["time", "instruction"]) {
      const model = adapter.buildQ1TimelineSharedModel(ir, { mode: "debug", layout });
      const blocks = model.lanes.flatMap((lane: any) => lane.blocks);
      assert.equal(blocks.length, 2);
      for (const iteration of [0, 1]) {
        assert.deepEqual(blocks[iteration].eventIds, [`play${iteration}-0`, `play${iteration}-1`, `marker${iteration}`]);
      }
    }
  });

  it("keeps symbolic runtime events on additive lower-bound times instead of stacking at zero", () => {
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    const model = adapter.buildQ1TimelineSharedModel({
      events: [
        {
          id: "seq0:first-wait",
          sequencer_id: "seq0",
          lane: "rt.wait",
          kind: "wait",
          t0: { kind: "concrete", value: 16, display: "16" },
          t1: { kind: "concrete", value: 65551, display: "65551" },
          duration: { kind: "concrete", value: 65535, display: "65535" },
          label: "wait",
          confidence: "exact",
          source: { raw: "wait 65535", file: "seq0.q1asm", line: 1 },
        },
        {
          id: "seq0:symbolic-tail",
          sequencer_id: "seq0",
          lane: "rt.wait",
          kind: "wait",
          t0: { kind: "concrete", value: 65551, display: "65551" },
          t1: { kind: "symbolic", expr: "65551 + int(ro_duration % 65535)", display: "65551 + int(ro_duration % 65535)" },
          duration: { kind: "symbolic", expr: "int(ro_duration % 65535)", display: "int(ro_duration % 65535)" },
          label: "wait",
          confidence: "symbolic",
          source: { raw: "wait {int(ro_duration % 65535)}", file: "seq0.q1asm", line: 2 },
        },
        {
          id: "seq0:first-buffer",
          sequencer_id: "seq0",
          lane: "rt.wait",
          kind: "wait",
          t0: { kind: "symbolic", expr: "65551 + int(ro_duration % 65535) + 4", display: "65551 + int(ro_duration % 65535) + 4" },
          t1: { kind: "symbolic", expr: "65551 + int(ro_duration % 65535) + 4 + 2000", display: "65551 + int(ro_duration % 65535) + 4 + 2000" },
          duration: { kind: "concrete", value: 2000, display: "2000" },
          label: "wait",
          confidence: "symbolic",
          source: { raw: "wait 2000", file: "seq0.q1asm", line: 3 },
        },
        {
          id: "seq0:second-wait",
          sequencer_id: "seq0",
          lane: "rt.wait",
          kind: "wait",
          t0: {
            kind: "symbolic",
            expr: "65551 + int(ro_duration % 65535) + 4 + 2000 + 4 + 4",
            display: "65551 + int(ro_duration % 65535) + 4 + 2000 + 4 + 4",
          },
          t1: {
            kind: "symbolic",
            expr: "65551 + int(ro_duration % 65535) + 4 + 2000 + 4 + 4 + 65535",
            display: "65551 + int(ro_duration % 65535) + 4 + 2000 + 4 + 4 + 65535",
          },
          duration: { kind: "concrete", value: 65535, display: "65535" },
          label: "wait",
          confidence: "symbolic",
          source: { raw: "wait 65535", file: "seq0.q1asm", line: 4 },
        },
      ],
    }, { mode: "normal" });

    const rtBlocks = model.lanes.find((lane: any) => lane.kind === "runtime").blocks;

    assert.deepEqual(rtBlocks.map((block: any) => [block.id, block.start, block.duration]), [
      ["seq0:first-wait", 16, 65535],
      ["seq0:symbolic-tail", 65551, 0],
      ["seq0:first-buffer", 65555, 2000],
      ["seq0:second-wait", 67563, 65535],
    ]);
    assert.equal(model.viewport.end, 133098);
  });

  it("keeps conditional timing boundaries visible without placing unknown RT commands at zero", () => {
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    const unknown = { kind: "unknown", display: "unknown(conditional_execution)" };
    const make = (id: string, kind: string, t0: any, t1: any, meta: any = {}) => ({
      id, kind, sequencer_id: "q0", lane: kind === "q1_issue" ? "debug.q1_issue" : `rt.${kind}`,
      confidence: kind === "unknown_region" ? "unknown" : "exact", t0, t1, meta,
      source: { file: "q0.q1asm", line: 1, raw: meta.op || kind },
    });
    const ir = { events: [
      make("prefix", "wait", 0, 1000),
      make("conditional", "unknown_region", 1000, null, {
        reason: "conditional_execution_unknown", conditional_time_unknown: true, op: "play", rt_packet_id: "p1",
      }),
      make("disabled-q1", "q1_issue", 12, 16, { op: "set_cond", conditional_enabled: false }),
      make("wait-q1", "q1_issue", 16, 20, { op: "wait", rt_packet_id: "p2" }),
      { ...make("unknown-wait", "wait", unknown, unknown, { conditional_time_unknown: true, rt_packet_id: "p2" }),
        duration: { kind: "concrete", value: 65_535 } },
      make("play-q1", "q1_issue", 20, 24, { op: "play", rt_packet_id: "p3" }),
      { ...make("unknown-play", "play", unknown, unknown, { conditional_time_unknown: true, rt_packet_id: "p3", path: 0 }), lane: "rt.path0" },
    ] };
    const before = JSON.stringify(ir);
    const time = adapter.buildQ1TimelineSharedModel(ir, { layout: "time" });
    const blocks = time.lanes.flatMap((lane: any) => lane.blocks);
    assert.deepEqual(blocks.filter((block: any) => block.kind !== "q1_issue").map((block: any) => block.id), ["prefix", "conditional"]);
    assert.equal(blocks.find((block: any) => block.id === "conditional").start, 1000);
    assert.match(blocks.find((block: any) => block.id === "conditional").title, /-> unknown$/);
    assert.ok(blocks.some((block: any) => block.id === "disabled-q1"));
    assert.equal(time.viewport.end, 1001);
    const instruction = adapter.buildQ1TimelineSharedModel(ir, { layout: "instruction" });
    const ids = instruction.lanes.flatMap((lane: any) => lane.blocks.flatMap((block: any) => block.eventIds));
    assert.ok(ids.includes("unknown-wait"));
    assert.ok(ids.includes("unknown-play"));
    assert.deepEqual(instruction.lanes.find((lane: any) => lane.kind === "runtime").blocks.map((block: any) => block.id),
      ["prefix", "conditional", "unknown-wait"]);
    assert.equal(JSON.stringify(ir), before);
  });

  it("shows branch controls on q1 issue commands and ghost effects on runtime lanes", () => {
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const css = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.css"), "utf-8");
    const branchId = "seq0:branch:seq0.q1asm:15:jl:done";
    const model = adapter.buildQ1TimelineSharedModel({
      events: [
        {
          id: "seq0:q1-branch",
          sequencer_id: "seq0",
          lane: "debug.q1_issue",
          kind: "q1_issue",
          t0: { value: 60 },
          t1: { value: 76 },
          duration: { value: 16 },
          label: "jl",
          confidence: "assumed",
          source: { raw: "jl @done", file: "seq0.q1asm", line: 15 },
          meta: { op: "jl", branch_id: branchId, assumed_branch_path: "fallthrough" },
        },
        {
          id: "seq0:branch-effect",
          sequencer_id: "seq0",
          lane: "rt.control",
          kind: "branch_region",
          t0: { value: 60 },
          t1: { value: 140 },
          duration: { value: 80 },
          label: "branch",
          confidence: "assumed",
          source: { raw: "jl @done", file: "seq0.q1asm", line: 15 },
          meta: { branch_id: branchId, assumed_branch_path: "fallthrough" },
        },
      ],
    }, { mode: "normal", expandedQ1IssueSequencers: ["seq0"] });

    const blocks = model.lanes.flatMap((lane: any) => lane.blocks);
    const q1Branch = blocks.find((block: any) => block.id === "seq0:q1-branch");
    const rtEffect = blocks.find((block: any) => block.id === "seq0:branch-effect");

    assert.equal(q1Branch.visualKind, "q1_issue");
    assert.equal(q1Branch.domain.meta.branch_id, branchId);
    assert.equal(rtEffect.visualKind, "branch_effect");
    assert.match(rtEffect.classNames, /branch-effect-marker/);
    assert.match(rtEffect.title, /Branch effect/);
    assert.match(script, /function eventHasBranchControl\(event\)/);
    assert.match(script, /eventHasBranchControl\(event\)/);
    assert.match(css, /\.shared-timeline-stage \.timeline-block-branch_effect\s*{/);
  });

  it("maps aligned q1timeline times when requested by the shared renderer", () => {
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    const model = adapter.buildQ1TimelineSharedModel({
      events: [
        {
          id: "seq0:play",
          sequencer_id: "seq0",
          lane: "rt.play",
          kind: "play",
          t0: { value: 0 },
          t1: { value: 40 },
          duration: { value: 40 },
          label: "play",
          confidence: "exact",
          source: { file: "seq0.q1asm", line: 1 },
          meta: { aligned_t0: 100, aligned_t1: 160 },
        },
      ],
    }, { mode: "normal", timeBasis: "aligned" });

    const block = model.lanes.flatMap((lane: any) => lane.blocks).find((item: any) => item.id === "seq0:play");

    assert.equal(block.start, 100);
    assert.equal(block.duration, 60);
  });

  it("shows all wait variants as wait in normal q1timeline view", () => {
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    const timeline = {
      events: [
        {
          id: "seq0:derived",
          sequencer_id: "seq0",
          lane: "rt.wait",
          kind: "wait",
          t0: { value: 0 },
          t1: { value: 40 },
          duration: { value: 40 },
          label: "derived wait",
          confidence: "exact",
          meta: { duration_provenance: { role: "derived_wait", symbol: "WAIT_A" } },
        },
        {
          id: "seq0:post",
          sequencer_id: "seq0",
          lane: "rt.wait",
          kind: "wait",
          t0: { value: 40 },
          t1: { value: 80 },
          duration: { value: 40 },
          label: "post wait",
          confidence: "exact",
          meta: { duration_provenance: { role: "post_wait", symbol: "POST_WAIT" } },
        },
        {
          id: "seq0:multicast",
          sequencer_id: "seq0",
          lane: "rt.wait",
          kind: "wait",
          t0: { value: 80 },
          t1: { value: 120 },
          duration: { value: 40 },
          label: "multicast wait",
          confidence: "exact",
          meta: { duration_provenance: { role: "multicast_wait", symbol: "WAIT_FOR_MULTICAST" } },
        },
      ],
    };

    const normalBlocks = adapter.buildQ1TimelineSharedModel(timeline, { mode: "normal" }).lanes.flatMap((lane: any) => lane.blocks);
    const debugBlocks = adapter.buildQ1TimelineSharedModel(timeline, { mode: "debug" }).lanes.flatMap((lane: any) => lane.blocks);

    assert.deepEqual(normalBlocks.map((block: any) => block.label), ["wait", "wait", "wait"]);
    assert.deepEqual(debugBlocks.map((block: any) => block.label), ["derived wait", "post wait", "multicast wait"]);
  });

  it("merges dual-path q1timeline play events into one shared block", () => {
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    const model = adapter.buildQ1TimelineSharedModel({
      events: [
        {
          id: "seq0:play:p0",
          sequencer_id: "seq0",
          lane: "rt.path0",
          kind: "play",
          t0: { value: 20 },
          t1: { value: 60 },
          duration: { value: 40 },
          label: "wf0",
          confidence: "exact",
          source: { file: "seq0.q1asm", line: 4, raw: "play 0,1,40" },
          meta: { path: 0, waveform_index: 0, rt_packet_id: "pkt-play-0" },
        },
        {
          id: "seq0:play:p1",
          sequencer_id: "seq0",
          lane: "rt.path1",
          kind: "play",
          t0: { value: 20 },
          t1: { value: 60 },
          duration: { value: 40 },
          label: "wf1",
          confidence: "exact",
          source: { file: "seq0.q1asm", line: 4, raw: "play 0,1,40" },
          meta: { path: 1, waveform_index: 1, rt_packet_id: "pkt-play-0" },
        },
      ],
    }, { mode: "normal" });

    const laneIds = model.lanes.map((lane: any) => lane.id);
    const blocks = model.lanes.flatMap((lane: any) => lane.blocks);
    const playBlocks = blocks.filter((block: any) => block.kind === "play");

    assert.equal(playBlocks.length, 1);
    assert.equal(laneIds.includes("sequencer:seq0:rt.path0"), false);
    assert.equal(laneIds.includes("sequencer:seq0:rt.path1"), false);
    assert.equal(playBlocks[0].label, "play");
    assert.equal(playBlocks[0].detail, "p0 wf0 / p1 wf1");
    assert.equal(playBlocks[0].eventId, "seq0:play:p0");
    assert.deepEqual(playBlocks[0].eventIds, ["seq0:play:p0", "seq0:play:p1"]);
    assert.deepEqual(playBlocks[0].domain.meta.play_paths.map((path: any) => path.label), ["wf0", "wf1"]);
  });

  it("keeps changed and removed play paths independently selectable from unchanged paths", () => {
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    const makeEvent = (path: number, status?: string) => ({
      id: `seq0:p${path}`, sequencer_id: "seq0", lane: `rt.path${path}`, kind: "play",
      t0: { value: 0 }, t1: { value: 40 }, duration: { value: 40 },
      source: { file: "seq0.q1asm", line: 1 }, meta: { path, rt_packet_id: "packet", diff_status: status },
    });
    for (const status of ["changed", "removed"]) {
      const model = adapter.buildQ1TimelineSharedModel({ events: [makeEvent(0), makeEvent(1, status)] }, { mode: "normal" });
      const blocks = model.lanes.flatMap((lane: any) => lane.blocks);
      assert.equal(blocks.length, 2);
      assert.ok(blocks.find((block: any) => block.eventId === "seq0:p1").classNames.includes(`diff-${status}`));
    }
  });

  it("falls back to q1timeline play path lanes when a path event cannot be merged", () => {
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    const model = adapter.buildQ1TimelineSharedModel({
      events: [
        {
          id: "seq0:play:p0",
          sequencer_id: "seq0",
          lane: "rt.path0",
          kind: "play",
          t0: { value: 20 },
          t1: { value: 60 },
          duration: { value: 40 },
          label: "wf0",
          confidence: "exact",
          source: { file: "seq0.q1asm", line: 4, raw: "play 0,1,40" },
          meta: { path: 0, waveform_index: 0, rt_packet_id: "pkt-play-0" },
        },
      ],
    }, { mode: "normal" });

    const pathLane = model.lanes.find((lane: any) => lane.id === "sequencer:seq0:rt.path0");

    assert.ok(pathLane);
    assert.equal(pathLane.parentGroupId, "sequencer:seq0");
    assert.equal(pathLane.label, "path0");
    assert.equal(pathLane.blocks.length, 1);
    assert.equal(pathLane.blocks[0].label, "wf0");
  });

  it("maps q1timeline feedback flows to compact shared annotations", () => {
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    const model = adapter.buildQ1TimelineSharedModel({
      events: [
        {
          id: "seq0:acquire",
          sequencer_id: "seq0",
          lane: "rt.acquire",
          kind: "acquire",
          t0: { value: 40 },
          t1: { value: 80 },
          duration: { value: 40 },
          label: "acquire",
          confidence: "exact",
          source: { file: "seq0.q1asm", line: 1 },
        },
        {
          id: "seq0:pop",
          sequencer_id: "seq0",
          lane: "rt.feedback",
          kind: "feedback_pop",
          t0: { value: 120 },
          t1: { value: 120 },
          duration: { value: 0 },
          label: "feedback pop",
          confidence: "exact",
          source: { file: "seq0.q1asm", line: 2 },
        },
        {
          id: "seq1:commit",
          sequencer_id: "seq1",
          lane: "rt.feedback",
          kind: "feedback_com",
          t0: { value: 180 },
          t1: { value: 180 },
          duration: { value: 0 },
          label: "feedback commit",
          confidence: "exact",
          source: { file: "seq1.q1asm", line: 3 },
        },
      ],
      feedback_flows: [
        {
          id: "feedback-flow-local",
          from_event_id: "seq0:acquire",
          to_event_id: "seq0:pop",
          channel: "1",
          label: "feedback ch 1: acq#0/bin0 -> $LEFT",
        },
        {
          id: "feedback-flow-cross",
          from_event_id: "seq0:acquire",
          to_event_id: "seq1:commit",
          channel: "grant",
          label: "feedback ch grant: req -> grant",
        },
      ],
    }, { mode: "normal" });

    const laneIds = model.lanes.map((lane: any) => lane.id);
    const local = model.annotations.find((annotation: any) => annotation.id === "feedback-flow-local");
    const cross = model.annotations.find((annotation: any) => annotation.id === "feedback-flow-cross");

    assert.equal(laneIds.includes("sequencer:seq0:feedback"), false);
    assert.equal(local.type, "feedback-inline");
    assert.equal(local.laneId, "sequencer:seq0");
    assert.equal(local.start, 80);
    assert.equal(local.end, 120);
    assert.deepEqual(local.eventIds, ["seq0:pop", "seq0:acquire"]);
    assert.equal(cross.type, "feedback-cross");
    assert.equal(cross.fromLaneId, "sequencer:seq0");
    assert.equal(cross.toLaneId, "sequencer:seq1");
    assert.equal(cross.fromTime, 80);
    assert.equal(cross.toTime, 180);
  });

  it("maps q1timeline loop blocks to shared range annotations", () => {
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    const model = adapter.buildQ1TimelineSharedModel({
      events: [
        {
          id: "seq0:loop",
          sequencer_id: "seq0",
          lane: "rt.loop",
          kind: "loop_block",
          t0: { value: 20 },
          t1: { value: 220 },
          duration: { value: 200 },
          label: "loop L0 x4",
          confidence: "exact",
          source: { file: "seq0.q1asm", line: 8 },
          meta: {
            loop_id: "L0",
            count_display: "4",
            first_iteration_event_ids: ["seq0:play"],
          },
        },
      ],
    }, { mode: "normal" });

    const loop = model.annotations.find((annotation: any) => annotation.type === "loop-range");

    assert.equal(loop.id, "seq0:loop:loop-range");
    assert.equal(loop.laneId, "sequencer:seq0");
    assert.equal(loop.start, 20);
    assert.equal(loop.end, 220);
    assert.equal(loop.label, "L0 x4");
    assert.deepEqual(loop.eventIds, ["seq0:loop", "seq0:play"]);
  });

  it("keeps shared q1timeline loop ranges from covering blocks", () => {
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    const sharedCss = readFileSync(join(__dirname, "..", "src", "shared", "timeline", "renderer.css"), "utf-8");
    const ir = {
      events: [
        {
          id: "seq0:loop",
          sequencer_id: "seq0",
          lane: "rt.loop",
          kind: "loop_block",
          t0: { value: 20 },
          t1: { value: 220 },
          duration: { value: 200 },
          label: "loop L0 forever",
          confidence: "exact",
          meta: { loop_id: "L0", count_display: "forever", first_iteration_event_ids: ["seq0:preview"] },
        },
        {
          id: "seq0:preview",
          sequencer_id: "seq0",
          lane: "rt.loop",
          kind: "loop_iteration_preview",
          t0: { value: 20 },
          t1: { value: 220 },
          duration: { value: 200 },
          label: "iteration 0 preview",
          confidence: "exact",
        },
        {
          id: "seq0:loop-overlay", sequencer_id: "seq0", lane: "control.flow", kind: "control_flow_loop",
          t0: { value: 20 }, t1: { value: 220 }, label: "loop L0 forever",
          meta: { control_flow_source_event_id: "seq0:loop" },
        },
        {
          id: "seq0:branch-overlay", sequencer_id: "seq0", lane: "control.flow", kind: "control_flow_branch",
          t0: { value: 20 }, t1: { value: 220 }, label: "branch overlay",
          meta: { control_flow_source_event_id: "seq0:play" },
        },
        {
          id: "seq0:play",
          sequencer_id: "seq0",
          lane: "rt.play",
          kind: "play",
          t0: { value: 40 },
          t1: { value: 80 },
          duration: { value: 40 },
          label: "p0 wf",
          confidence: "exact",
        },
      ],
    };
    for (const mode of ["normal", "debug"]) {
      for (const layout of ["time", "instruction"]) {
        const model = adapter.buildQ1TimelineSharedModel(ir, { mode, layout });
        const blocks = model.lanes.flatMap((lane: any) => lane.blocks);

        assert.equal(model.annotations.filter((annotation: any) => annotation.type === "loop-range").length, 1);
        assert.equal(blocks.some((block: any) => block.kind === "loop_block"), false);
        assert.equal(blocks.some((block: any) => block.kind === "loop_iteration_preview"), false);
        assert.deepEqual(blocks.map((block: any) => block.kind), ["play"]);
      }
    }
    assert.match(sharedCss, /\.loop-range-label\s*{[^}]*opacity:\s*0/s);
    assert.match(sharedCss, /\.timeline-loop-range:hover \.loop-range-label,[\s\S]*\.timeline-loop-range\.is-selected \.loop-range-label\s*{[^}]*opacity:\s*1/s);
  });

  it("ignores standalone HTML control overlays when folding waits and executed loops", () => {
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    const event = (id: string, kind: string, start: number, end: number, meta: any = {}) => ({
      id, kind, sequencer_id: "seq0", lane: `rt.${kind}`, t0: { kind: "concrete", value: start }, t1: { kind: "concrete", value: end },
      confidence: "exact", meta,
    });
    const overlays = ["control_flow_loop", "control_flow_branch"].map((kind) => ({
      ...event(kind, kind, 0, 196605), lane: "control.flow", meta: { control_flow_source_event_id: "loop" },
    }));
    const ir = { events: [event("wait", "wait", 0, 65535),
      event("loop", "loop_block", 0, 65535, { count: 3, period: { value: 65535 }, wait_only: true }),
      event("stop", "stop", 196605, 196605)] };
    const waitScale = adapter.buildWaitTimeScale(ir);
    assert.ok(waitScale.gaps.length);
    assert.deepEqual(adapter.buildWaitTimeScale({ events: [...ir.events, ...overlays] }).gaps, waitScale.gaps);

    const playback = { playback: { status: "completed" }, events: [
      event("wait0", "wait", 0, 65535), event("wait1", "wait", 65535, 196605),
      event("loop", "loop_block", 0, 196605, { playback: true, loop_preview_key: "L0", count: 3,
        first_iteration_end: 65535, hidden_event_ids: ["wait1"] }),
      event("stop", "stop", 196605, 196605),
    ] };
    const loopScale = adapter.buildTimelineTimeScale(playback);
    assert.ok(loopScale.gaps.length);
    assert.deepEqual(adapter.buildTimelineTimeScale({ ...playback, events: [...playback.events, ...overlays] }).gaps, loopScale.gaps);
  });

  it("renders q1timeline through the shared renderer when assets are available", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");

    assert.match(script, /function renderSharedTimelineIfAvailable/);
    assert.match(script, /q1lensSharedTimeline\.renderTimeline/);
    assert.match(script, /q1timelineTimelineAdapter\.buildQ1TimelineSharedModel/);
    assert.match(script, /timeBasis: timelineTimeBasis/);
    assert.match(script, /model\.viewport = sharedTimelineViewport\(\)/);
    assert.match(script, /model\.ticks = sharedTimelineTicks\(\)/);
  });

  it("renders shared feedback and loop annotations without adding fixed feedback rows", () => {
    const sharedRenderer = readFileSync(join(__dirname, "..", "src", "shared", "timeline", "renderer.js"), "utf-8");
    const sharedCss = readFileSync(join(__dirname, "..", "src", "shared", "timeline", "renderer.css"), "utf-8");
    const q1TimelineCss = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.css"), "utf-8");

    assert.match(sharedRenderer, /function renderAnnotations/);
    assert.match(sharedRenderer, /function renderFeedbackInlineAnnotation/);
    assert.match(sharedRenderer, /function renderFeedbackCrossAnnotation/);
    assert.match(sharedRenderer, /function renderLoopRangeAnnotation/);
    assert.match(sharedRenderer, /handlers\.onAnnotationClick/);
    assert.match(sharedRenderer, /data-feedback-role/);
    assert.match(sharedRenderer, /feedback-connector/);
    assert.match(sharedRenderer, /loop-range-rail/);
    assert.match(sharedRenderer, /loop-range-cap loop-range-cap-start/);
    assert.match(sharedRenderer, /loop-range-cap loop-range-cap-end/);
    assert.match(sharedCss, /\.feedback-inline-capsule\s*{/);
    assert.match(sharedCss, /\.feedback-endpoint\s*{/);
    assert.match(sharedCss, /\.feedback-connector\s*{/);
    assert.match(sharedCss, /\.timeline-loop-range\s*{/);
    assert.match(sharedCss, /\.loop-range-rail\s*{/);
    assert.match(sharedCss, /\.loop-range-cap\s*{/);
    assert.match(sharedCss, /\.loop-range-label\s*{/);
    assert.doesNotMatch(sharedCss, /\.timeline-loop-bracket\s*{/);
    assert.doesNotMatch(sharedCss, /\.timeline-loop-bracket::after\s*{/);
    assert.match(q1TimelineCss, /\.shared-timeline-stage \.feedback-inline-capsule\s*{/);
    assert.match(q1TimelineCss, /\.feedback-inline-capsule\.is-active,[\s\S]*\.feedback-inline-capsule\.is-selected\s*{[^}]*color:\s*var\(--q1timeline-feedback-accent\)/s);
    assert.match(q1TimelineCss, /\.feedback-endpoint\.is-active,[\s\S]*\.feedback-endpoint\.is-selected\s*{[^}]*color:\s*color-mix\(in srgb, var\(--q1timeline-feedback-accent\) 82%, var\(--vscode-focusBorder\)\)/s);
    assert.match(q1TimelineCss, /\.feedback-connector\.is-active,[\s\S]*\.feedback-connector\.is-selected\s*{[^}]*stroke:\s*color-mix\(in srgb, var\(--q1timeline-feedback-accent\) 72%, var\(--vscode-focusBorder\)\)/s);
    assert.match(q1TimelineCss, /\.shared-timeline-stage \.timeline-loop-range\s*{/);
  });

  it("makes shared feedback annotations read as directional bridges", () => {
    const sharedRenderer = readFileSync(join(__dirname, "..", "src", "shared", "timeline", "renderer.js"), "utf-8");
    const sharedCss = readFileSync(join(__dirname, "..", "src", "shared", "timeline", "renderer.css"), "utf-8");

    assert.match(sharedRenderer, /function activateFeedbackAnnotation/);
    assert.match(sharedRenderer, /stage\.querySelectorAll\("\[data-flow-id\]"\)/);
    assert.match(sharedRenderer, /related\.classList\.toggle\("is-active", active\)/);
    assert.match(sharedRenderer, /feedback-mini-track feedback-mini-track-before/);
    assert.match(sharedRenderer, /feedback-mini-track feedback-mini-track-after/);
    assert.match(sharedRenderer, /feedback-mini-arrow/);
    assert.match(sharedRenderer, /feedback-bridge-arrow/);
    assert.match(sharedRenderer, /marker-end/);
    assert.match(sharedRenderer, /fill", "context-stroke"/);
    assert.match(sharedRenderer, /function visibleTimelineLanes\(model\)/);
    assert.match(sharedRenderer, /!\(lane && lane\.hidden\)/);
    assert.match(sharedRenderer, /function feedbackEndpointAnchorY\(rowHeight\)/);
    assert.match(sharedRenderer, /return rowHeight - 4;/);
    assert.match(sharedRenderer, /const visibleLanes = visibleTimelineLanes\(model\)/);
    assert.match(sharedRenderer, /const laneGeometryById = new Map/);
    assert.match(sharedRenderer, /totalHeight = top \+ height/);
    assert.match(sharedRenderer, /const y1 = fromLane\.top \+ feedbackEndpointAnchorY\(fromLane\.height\)/);
    assert.match(sharedRenderer, /const y2 = toLane\.top \+ feedbackEndpointAnchorY\(toLane\.height\)/);
    assert.doesNotMatch(sharedRenderer, /fromLaneIndex \* rowHeight \+ 31/);
    assert.match(sharedRenderer, /const controlOffset = Math\.max\(Math\.abs\(y2 - y1\) \* 0\.45, 12\)/);
    assert.match(sharedRenderer, /path\.setAttribute\("d", `M \$\{x1\} \$\{y1\} C \$\{x1\} \$\{controlY1\} \$\{x2\} \$\{controlY2\} \$\{x2\} \$\{y2\}`\)/);
    assert.doesNotMatch(sharedRenderer, /L \$\{x1\} \$\{midY\} L \$\{x2\} \$\{midY\} L \$\{x2\} \$\{y2\}/);
    assert.match(sharedCss, /\.feedback-mini-track\s*{/);
    assert.match(sharedCss, /\.feedback-mini-arrow\s*{/);
    assert.match(sharedCss, /\.feedback-label,[\s\S]*\.feedback-endpoint-label\s*{[^}]*max-width:\s*0/s);
    assert.match(sharedCss, /\.feedback-label,[\s\S]*\.feedback-endpoint-label\s*{[^}]*opacity:\s*0/s);
    assert.match(sharedCss, /\.feedback-inline-capsule\.is-active \.feedback-label,[\s\S]*\.feedback-endpoint\.is-selected \.feedback-endpoint-label\s*{[^}]*max-width:\s*48px/s);
    assert.match(sharedCss, /\.feedback-inline-capsule\.is-active,[\s\S]*\.feedback-endpoint\.is-selected\s*{[^}]*color:\s*var\(--vscode-focusBorder\)/s);
    assert.match(sharedCss, /\.feedback-connector\s*{[^}]*stroke:\s*var\(--vscode-descriptionForeground\)/s);
    assert.match(sharedCss, /\.feedback-connector\.is-active,[\s\S]*\.feedback-connector\.is-selected\s*{[^}]*stroke:\s*var\(--vscode-focusBorder\)/s);
    assert.match(sharedCss, /\.feedback-connector\s*{[^}]*opacity:\s*0\.[2-9]/s);
    assert.match(sharedCss, /\.feedback-connector\.is-active,[\s\S]*\.feedback-connector\.is-selected\s*{[^}]*opacity:\s*0\.[7-9]/s);
  });

  it("balances normal q1timeline visual weight toward feedback and loop structure", () => {
    const sharedCss = readFileSync(join(__dirname, "..", "src", "shared", "timeline", "renderer.css"), "utf-8");
    const q1TimelineCss = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.css"), "utf-8");

    assert.match(sharedCss, /\.feedback-connector\s*{[^}]*stroke-width:\s*1\.45;/s);
    assert.match(sharedCss, /\.feedback-connector\s*{[^}]*opacity:\s*0\.44;/s);
    assert.match(sharedCss, /\.loop-range-rail\s*{[^}]*height:\s*2px;[^}]*opacity:\s*0\.78;/s);
    assert.match(sharedCss, /\.loop-range-cap\s*{[^}]*width:\s*3px;[^}]*opacity:\s*1;/s);
    assert.match(sharedCss, /\.timeline-block\s*{[^}]*background:\s*color-mix\(in srgb, var\(--timeline-block-accent, var\(--vscode-charts-yellow, var\(--vscode-charts-orange\)\)\) 12%, transparent\);/s);
    assert.match(sharedCss, /\.timeline-block\s*{[^}]*border-color:\s*color-mix\(in srgb, var\(--timeline-block-accent, var\(--vscode-panel-border\)\) 72%, transparent\);/s);
    assert.match(sharedCss, /\.shared-timeline-stage \.timeline-block\.has-command-color\s*{[^}]*background:\s*color-mix\(in srgb, var\(--timeline-block-accent\) 15%, transparent\);/s);
    assert.doesNotMatch(q1TimelineCss, /\.timeline-block\.kind-q1_issue\s*{/);
    assert.match(q1TimelineCss, /--q1timeline-feedback-accent:\s*var\(--q1asm-feedback-color, #ad316c\);/);
    assert.match(q1TimelineCss, /body\.vscode-dark \.shared-timeline-stage \.feedback-connector\s*{[^}]*stroke:\s*var\(--q1timeline-feedback-accent\);[^}]*stroke-width:\s*2\.05;[^}]*opacity:\s*0\.82;/s);
    assert.match(q1TimelineCss, /body\.vscode-dark \.shared-timeline-stage \.feedback-bridge-arrowhead\s*{[^}]*opacity:\s*1;/s);
  });

  it("removes the q1timeline inline feedback capsule box while keeping the feedback marks", () => {
    const q1TimelineCss = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.css"), "utf-8");

    assert.match(q1TimelineCss, /body\.vscode-dark \.shared-timeline-stage \.feedback-inline-capsule\s*{[^}]*background:\s*transparent;[^}]*border-color:\s*transparent;/s);
    assert.match(q1TimelineCss, /body\.vscode-dark \.shared-timeline-stage \.feedback-inline-capsule\.is-active,[\s\S]*body\.vscode-dark \.shared-timeline-stage \.feedback-inline-capsule\.is-selected\s*{[^}]*background:\s*transparent;[^}]*border-color:\s*transparent;/s);
    assert.match(q1TimelineCss, /body\.vscode-dark \.shared-timeline-stage \.feedback-inline-capsule \.feedback-mini-track\s*{[^}]*height:\s*2px;/s);
    assert.match(q1TimelineCss, /body\.vscode-dark \.shared-timeline-stage \.feedback-inline-capsule \.feedback-mini-arrow\s*{[^}]*border-left-color:\s*var\(--q1timeline-feedback-accent\);/s);
  });

  it("keeps q1timeline source jump and zoom controls active in the shared renderer", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const sharedRenderer = readFileSync(join(__dirname, "..", "src", "shared", "timeline", "renderer.js"), "utf-8");

    assert.match(script, /function applySharedTimelineScale\(\)/);
    assert.match(script, /sharedTimelineActive/);
    assert.match(script, /renderSharedTimelineIfAvailable\(currentTimelineMode\(\), \{ preserveSelection: true \}\)/);
    assert.match(script, /function sharedTimelinePlotPixelWidth\(\)/);
    assert.match(script, /function sharedTimelinePointerAnchorRatio\(event\)/);
    assert.match(script, /installTimelineMouseInteractions\(\)/);
    assert.match(script, /window\.dispatchEvent\(new CustomEvent\("q1timeline:eventClick"/);
    assert.match(script, /vscode\.postMessage\(\{ type: "eventClick", eventId \}\)/);
    assert.match(script, /function eventNodeMatchesId\(node, eventId\)/);
    assert.match(script, /node\.dataset\.eventIds/);
    assert.match(sharedRenderer, /node\.dataset\.eventId = String\(annotation\.eventId/);
    assert.match(sharedRenderer, /node\.dataset\.eventIds = block\.eventIds\.map/);
  });

  it("supports q1timeline range selection and event-centered zoom", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const { timelineWindowForRange, isTimelineSelectionTarget } = loadQ1TimelineZoomFunctions(script);

    assert.deepEqual(timelineWindowForRange(100, 20, { min: 0, max: 1000 }), { min: 20, max: 100 });
    assert.deepEqual(timelineWindowForRange(500, 500, { min: 0, max: 1000 }), { min: 499.5, max: 500.5 });
    assert.deepEqual(timelineWindowForRange(5, 5, { min: 0, max: 1000 }, 100), { min: 0, max: 100 });
    assert.equal(timelineWindowForRange(Number.NaN, 10, { min: 0, max: 1000 }), undefined);
    assert.equal(isTimelineSelectionTarget({
      closest: (selector: string) => {
        if (selector.includes("[data-event-id]")) {
          return {};
        }
        if (selector.includes(".shared-timeline-stage .lane-track")) {
          return {};
        }
        return undefined;
      },
    }), true);
    assert.equal(isTimelineSelectionTarget({
      closest: (selector: string) => selector.includes("button") ? {} : undefined,
    }), false);

    assert.match(script, /let timelineSelectionRange = undefined/);
    assert.match(script, /let timelineSelectionDrag = undefined/);
    assert.match(script, /function sharedTimelineSelectionRange\(\)/);
    assert.match(script, /model\.selectionRange = sharedTimelineSelectionRange\(\)/);
    assert.match(script, /function beginTimelineSelectionDrag\(event\)/);
    assert.match(script, /function updateTimelineSelectionDrag\(event\)/);
    assert.match(script, /function endTimelineSelectionDrag\(event\)/);
    assert.match(script, /function updateTimelineSelectionOverlay\(svg, window, geometry\)/);
    assert.match(script, /q1timeline-selection-overlay/);
    assert.match(script, /Shift-drag selects a range/);
    assert.match(script, /function zoomTimelineSelection\(\)/);
    assert.match(script, /function zoomTimelineToSelectedEvent\(\)/);
    assert.match(script, /function zoomTimelineToEvent\(event\)/);
    assert.match(script, /if \(event\.shiftKey\) \{\s*beginTimelineSelectionDrag\(event\);\s*return;\s*\}/s);
    assert.match(script, /id = "q1timeline-zoom-selection-button"/);
    assert.match(script, /id = "q1timeline-zoom-event-button"/);
    assert.match(script, /addTimelineControlAction\(popover, "zoom", "Zoom around branch"/);
    assert.match(script, /zoomTimelineToEvent\(event\);\s*closeTimelineControlPopover\(true\);/);
  });

  it("clears shared timeline aria selection before selecting another block", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const start = script.indexOf("function selectEventNode(eventNode, options = {})");
    const end = script.indexOf("function findVisibleEventNodeByIds", start);
    const selectEventNodeSource = script.slice(start, end);

    assert.ok(start >= 0);
    assert.ok(end > start);
    assert.match(selectEventNodeSource, /node\.classList\.remove\("is-selected"\)/);
    assert.match(selectEventNodeSource, /node\.setAttribute\("aria-selected", "false"\)/);
    assert.match(selectEventNodeSource, /eventNode\.setAttribute\("aria-selected", "true"\)/);
  });

  it("uses a dropdown control for inline Q1 preview and double-click only for source jumps", () => {
    const script = readFileSync(join(__dirname, "..", "src", "qbs", "webview", "assets", "timeline.js"), "utf-8");

    assert.match(script, /window\.q1lensSharedTimeline/);
    assert.match(script, /sharedTimeline\.element/);
    assert.match(script, /sharedTimeline\.button/);
    assert.match(script, /function renderInlineQ1LaneToggle/);
    assert.match(script, /inline-q1-lane-toggle/);
    assert.match(script, /toggleInlineQ1Lane/);
    assert.doesNotMatch(script, /toggleInlineQ1Block/);
    assert.doesNotMatch(script, /renderInlineQ1Toggle/);
    assert.doesNotMatch(script, /node\.appendChild\(renderInlineQ1/);
    assert.match(script, /dblclick/);
    assert.match(script, /q1asmSourceMessage/);
    assert.match(script, /scheduleSourceMessage/);
    assert.match(script, /openQ1AsmSource/);
    assert.match(script, /openScheduleSource/);
    assert.doesNotMatch(script, /Q1 [v>]/);
    assert.match(script, /labelText\.title = lane\.title/);
    assert.match(script, /expandedInlineQ1Lanes/);
  });

  it("keeps timeline blocks shrinkable in narrow views", () => {
    const css = readFileSync(join(__dirname, "..", "src", "qbs", "webview", "assets", "timeline.css"), "utf-8");

    assert.match(css, /\.timeline-block\s*{[^}]*box-sizing:\s*border-box;/s);
    assert.match(css, /\.timeline-block\s*{[^}]*min-width:\s*0;/s);
    assert.match(css, /\.inline-q1-lane-toggle\s*{[^}]*width:\s*18px;/s);
    assert.match(css, /\.inline-q1-lane-toggle\s*{[^}]*min-width:\s*18px;/s);
    assert.match(css, /\.timeline-block-loop\s*{[^}]*border-width:\s*2px 2px 0;/s);
    assert.match(css, /\.timeline-block-sweep\s*{[^}]*border:\s*2px dashed var\(--vscode-charts-purple\);/s);
    assert.match(css, /\.timeline-block-sweep\s*{[^}]*background:\s*color-mix\(in srgb, var\(--vscode-charts-purple\) 12%, transparent\);/s);
    assert.match(css, /\.timeline-row-control-flow \.lane-track\s*{/s);
  });

  it("uses compact Q1ASM-like row spacing in the QBS timeline", () => {
    const css = readFileSync(join(__dirname, "..", "src", "qbs", "webview", "assets", "timeline.css"), "utf-8");

    assert.match(css, /\.timeline-row\s*{[^}]*min-height:\s*36px;/s);
    assert.match(css, /\.lane-track\s*{[^}]*min-height:\s*36px;/s);
    assert.match(css, /\.timeline-row-q1\s*{[^}]*min-height:\s*34px;/s);
    assert.match(css, /\.lane-track-q1\s*{[^}]*min-height:\s*34px;/s);
    assert.match(css, /\.timeline-block\s*{[^}]*top:\s*7px;[^}]*height:\s*22px;/s);
    assert.match(css, /\.timeline-block-q1\s*{[^}]*top:\s*6px;[^}]*height:\s*22px;/s);
  });

  it("colors inline Q1 command blocks from per-command accents", () => {
    const renderer = readFileSync(join(__dirname, "..", "src", "qbs", "webview", "assets", "timeline.js"), "utf-8");
    const css = readFileSync(join(__dirname, "..", "src", "qbs", "webview", "assets", "timeline.css"), "utf-8");

    assert.match(renderer, /block\.accentColor/);
    assert.match(renderer, /--timeline-block-accent/);
    assert.match(css, /\.timeline-block-q1\s*{[^}]*var\(--timeline-block-accent, var\(--vscode-charts-green\)\)/s);
    assert.match(css, /\.timeline-block-q1\s*{[^}]*border-color:\s*var\(--timeline-block-accent, var\(--vscode-charts-green\)\);/s);
  });

  it("renders an accessible provenance overlay, legend, and unmapped notice", () => {
    const renderer = readFileSync(join(__dirname, "..", "src", "qbs", "webview", "assets", "timeline.js"), "utf-8");
    const css = readFileSync(join(__dirname, "..", "src", "qbs", "webview", "assets", "timeline.css"), "utf-8");

    assert.match(renderer, /node\.dataset\.provenance = block\.provenanceStatus/);
    assert.match(renderer, /node\.setAttribute\(\s*"aria-label"/s);
    assert.match(renderer, /function renderProvenanceLegend\(coverage\)/);
    assert.match(renderer, /legend\.setAttribute\("aria-label", coverage\.ariaLabel\)/);
    assert.match(renderer, /items\.setAttribute\("role", "list"\)/);
    assert.match(renderer, /notice\.setAttribute\("role", "note"\)/);
    assert.match(css, /\.timeline-block-pulse\[data-provenance="compiler"\]/);
    assert.match(css, /\.timeline-block-pulse\[data-provenance="inferred"\]/);
    assert.match(css, /\.timeline-block-pulse\[data-provenance="unmapped"\]/);
    assert.match(css, /\.provenance-legend-item\[data-provenance="unmapped"\] \.provenance-swatch/);
  });

  it("keeps zoomed-out shared micro blocks from looking overlapped", () => {
    const sharedRenderer = readFileSync(join(__dirname, "..", "src", "shared", "timeline", "renderer.js"), "utf-8");
    const sharedCss = readFileSync(join(__dirname, "..", "src", "shared", "timeline", "renderer.css"), "utf-8");

    assert.match(sharedRenderer, /const visibleWidth = Math\.max\(timeCoordinate\(visibleEnd, viewport\) - timeCoordinate\(visibleStart, viewport\), 0\)/);
    assert.match(sharedRenderer, /const minMicroWidth = span \* 0\.0005/);
    assert.match(sharedRenderer, /tiny: visibleWidth < span \* 0\.0025/);
    assert.match(sharedRenderer, /compact: visibleWidth < span \* 0\.075/);
    assert.match(sharedRenderer, /node\.classList\.add\("is-tiny"\)/);
    assert.match(sharedRenderer, /node\.classList\.add\("is-compact"\)/);
    assert.doesNotMatch(sharedRenderer, /Math\.max\(visibleEnd - visibleStart, span \* 0\.0025\)/);
    assert.match(sharedCss, /\.timeline-block\.is-compact\s*{[^}]*padding:\s*0;/s);
    assert.match(sharedCss, /\.timeline-block\.is-tiny\s*{[^}]*padding:\s*0;/s);
    assert.match(sharedCss, /\.timeline-block\.is-compact \.block-label,[\s\S]*\.timeline-block\.is-compact \.block-detail\s*{[^}]*display:\s*none;/s);
    assert.match(sharedCss, /\.timeline-block\.is-tiny \.block-label,[\s\S]*\.timeline-block\.is-tiny \.block-detail\s*{[^}]*display:\s*none;/s);
  });

  it("uses left-drag for timeline panning and shift-drag for time selection", () => {
    const script = readFileSync(join(__dirname, "..", "src", "qbs", "webview", "assets", "timeline.js"), "utf-8");

    assert.match(script, /function beginTimelineDrag/);
    assert.match(script, /function beginPanDrag/);
    assert.match(script, /function updatePanDrag/);
    assert.match(script, /timelineModel\.panViewport/);
    assert.match(script, /event\.shiftKey/);
    assert.match(script, /beginSelectionDrag\(event\)/);
    assert.match(script, /suppressNextBlockClick/);
  });

  it("does not post source-open event clicks for programmatic highlights", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");

    assert.match(script, /function selectEventNode\(eventNode, options = \{\}\)/);
    assert.match(script, /options\.notify !== false/);
    assert.match(script, /selectEventNode\(target, \{ notify: false, syncHardware: visibleEvent === highlightedEvent \}\)/);
  });

  it("marks hardware source jumps to preserve the readable-time cursor", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const hardwareInitialization = extractFunctionSource(script, "initializeHardwareView");
    assert.match(hardwareInitialization || "", /type: "sourceClick"[^\n]+preserveHardwareCursor: true/);
  });

  it("persists local replay detail before requesting its execution trace", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const sources = [
      "readPersistedWebviewState", "writePersistedWebviewState", "hasAlignedTimelineTimes",
      "initialTimelineTimeBasis", "setTimelineTimeBasis", "initializeHardwareView",
    ].map((name) => {
      const source = extractFunctionSource(script, name);
      assert.ok(source, `Missing ${name}`);
      return source;
    });
    let savedState: any = {
      timelineTimeBasis: "aligned", analysisDetailsOpen: true,
      hardwareState: { sequencerId: "seq0", basis: "aligned", level: "modules", replayOpen: false, time: 104 },
    };
    let hardwareState: any;
    let mountOptions: any;
    const calls: any[] = [];
    const timers = new Map<number, () => void>();
    let nextTimer = 1;
    const vscode = {
      getState: () => savedState,
      setState(state: any) {
        savedState = JSON.parse(JSON.stringify(state));
        calls.push({ type: "state" });
      },
      postMessage(message: any) {
        calls.push({ type: "post", message, state: JSON.parse(JSON.stringify(savedState)) });
      },
    };
    const window = { q1lensHardwareView: { mount(_root: any, _ir: any, options: any) {
      mountOptions = options;
      hardwareState = { ...options.initialState };
      return {
        getState: () => ({ ...hardwareState }),
        setTimeBasis(basis: string) {
          hardwareState.basis = basis;
          options.onStateChange({ ...hardwareState });
        },
      };
    } } };
    let hardwareMount: any;
    const document = {
      getElementById: () => ({ after: (root: any) => { hardwareMount = root; } }),
      createElement: () => ({ setAttribute: () => {} }),
    };
    const functions = Function("vscode", "window", "document", "setTimeout", "clearTimeout", `
      const timelineIr = { events: [{ id: "q1", meta: { aligned_t0: 100, aligned_t1: 104 } }] };
      const timelineEventsById = new Map(timelineIr.events.map(event => [event.id, event]));
      let hardwareView, hardwarePersistTimer;
      function ensureAnalysisDetailsBody() {}
      function renderHardwareCursor() {}
      function renderTimeBasisToggle() {}
      function resetTimelineZoom() {}
      ${sources.join("\n")}
      let timelineTimeBasis = initialTimelineTimeBasis();
      return { initializeHardwareView, timeBasis: () => timelineTimeBasis };
    `)(vscode, window, document,
      (callback: () => void) => { const id = nextTimer++; timers.set(id, callback); return id; },
      (id: number) => timers.delete(id));

    assert.equal(functions.timeBasis(), "aligned");
    functions.initializeHardwareView();
    assert.equal(hardwareMount.id, "q1timeline-hardware");
    hardwareState = { ...hardwareState, level: "detail", replayOpen: true, sequencerId: "seq0" };
    mountOptions.onStateChange({ ...hardwareState });
    assert.equal(timers.size, 1);
    mountOptions.onPrepareReplay("seq0");

    assert.equal(functions.timeBasis(), "local");
    assert.equal(timers.size, 0);
    assert.deepEqual(calls.map((call) => call.type), ["state", "state", "post"]);
    assert.deepEqual(calls[2].message, { type: "prepareExecutionReplay", sequencerId: "seq0" });
    assert.deepEqual(calls[2].state, {
      timelineTimeBasis: "local", analysisDetailsOpen: true,
      hardwareState: { sequencerId: "seq0", basis: "local", level: "detail", replayOpen: true, time: 104 },
    });
    hardwareState.replayScope = "project";
    mountOptions.onPrepareReplay("seq0", "project");
    const projectRequest = calls[calls.length - 1];
    assert.deepEqual(projectRequest.message, { type: "prepareExecutionReplay", sequencerId: "seq0", scope: "project" });
    assert.equal(projectRequest.state.timelineTimeBasis, "local");
    assert.equal(projectRequest.state.hardwareState.replayScope, "project");
    assert.equal(projectRequest.state.hardwareState.replayOpen, true);
  });

  it("chooses a visible q1timeline event for programmatic highlights", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");

    assert.match(script, /function findVisibleEventNodeByIds\(eventIds\)/);
    assert.match(script, /candidateNodes\.find\(isTimelineEventVisible\)/);
    assert.match(script, /findVisibleEventNodeByIds\(visibleEvent \? \[visibleEvent.id, \.\.\.highlightEventIds\] : highlightEventIds\)/);
  });

  it("expands q1 issue lanes and zooms tiny source highlights", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const { highlightedSpanNeedsZoom } = loadQ1TimelineHighlightFunctions(script);

    assert.equal(highlightedSpanNeedsZoom(10, 11, { min: 0, max: 1000 }, 500), true);
    assert.equal(highlightedSpanNeedsZoom(10, 100, { min: 0, max: 1000 }, 500), false);
    assert.equal(highlightedSpanNeedsZoom(1200, 1600, { min: 0, max: 1000 }, 500), true);
    assert.equal(highlightedSpanNeedsZoom(100, 50, { min: 75, max: 200 }, 500), true);
    assert.equal(highlightedSpanNeedsZoom(Number.NaN, 100, { min: 0, max: 1000 }, 500), false);

    assert.match(script, /function eventIsQ1Issue\(event\)/);
    assert.match(script, /function prioritizedHighlightEventIds\(eventIds\)/);
    assert.match(script, /function expandQ1IssueLanesForHighlight\(eventIds\)/);
    assert.match(script, /expandedQ1IssueSequencers\.add\(sequencer\)/);
    assert.match(script, /function zoomTimelineToHighlightedEvent\(event\)/);
    assert.match(script, /const highlightEventIds = prioritizedHighlightEventIds\(event\.data\.highlightEventIds \|\| \[\]\)/);
    assert.match(script, /expandQ1IssueLanesForHighlight\(highlightEventIds\)/);
    assert.match(script, /const highlightedEvent = highlightedEventForIds\(highlightEventIds\)/);
    assert.match(script, /zoomTimelineToHighlightedEvent\(visibleEvent\)/);
  });

  it("repositions q1timeline diagnostic badges when the timeline window changes", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");

    assert.match(script, /function updateEventDiagnosticBadge\(eventNode, rect, x0, width\)/);
    assert.ok(script.includes('eventNode.querySelector(".diagnostic-badge")'));
    assert.match(script, /updateEventDiagnosticBadge\(eventNode, rect, x0, width\)/);
  });

  it("keeps q1timeline dynamic axis labels on the integer nanosecond grid", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const { timelineTicks, formatWindowValue } = loadQ1TimelineAxisFunctions(script);

    assert.deepEqual(timelineTicks(0, 1), [0, 1]);
    assert.ok(timelineTicks(10.2, 12.7).every((tick) => Number.isInteger(tick)));
    assert.equal(formatWindowValue(10.49), "10");
    assert.equal(formatWindowValue(10.5), "11");
  });

  it("repositions q1timeline loop brackets and branch markers when the timeline window changes", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");

    assert.match(script, /function updateLoopBracket\(eventNode, xStart, xEnd\)/);
    assert.match(script, /function updateBranchMarker\(eventNode, xStart, xEnd\)/);
    assert.match(script, /updateLoopBracket\(eventNode, x0, x1\)/);
    assert.match(script, /updateBranchMarker\(eventNode, x0, x1\)/);
  });

  it("uses explicit Q1 issue lane roles for sequencer disclosure", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const css = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.css"), "utf-8");

    assert.match(script, /const expandedQ1IssueSequencers = new Set\(initialExpandedQ1IssueSequencers\(\)\)/);
    assert.match(script, /function currentTimelineMode\(\)/);
    assert.doesNotMatch(script, /querySelectorAll\("\[data-mode\]"\)/);
    assert.doesNotMatch(script, /split\(" \/ ", 2\)\[1\]/);
    assert.match(script, /laneNode\.dataset\.laneRole === "q1-issue"/);
    assert.match(script, /laneNode\.classList\.contains\("q1-issue-lane"\)/);
    assert.match(script, /function isQ1IssueLaneNode\(laneNode\)/);
    assert.match(script, /function sequencerHasQ1IssueLane\(sequencer\)/);
    assert.match(script, /function sequencerQ1IssueVisible\(sequencer\)/);
    assert.match(script, /function toggleSequencerFoldControl\(sequencer\)/);
    assert.match(script, /setSequencerQ1IssueExpanded\(sequencer, !sequencerQ1IssueVisible\(sequencer\)\)/);
    assert.match(script, /q1-issue-expanded/);
    assert.match(script, /if \(isQ1IssueLaneNode\(laneNode\) && !sequencerQ1IssueExpanded\(sequencer\)\)/);
    assert.match(script, /!sequencerHasQ1IssueLane\(sequencer\) && sequencerCollapsed\(sequencer\)/);
    assert.match(script, /function installSharedQ1IssueControls\(\)/);
    assert.match(script, /shared-q1-issue-toggle/);
    assert.match(script, /button\.setAttribute\("aria-label", `\$\{expanded \? "Hide" : "Show"\} Q1 core/);
    assert.match(script, /button\.textContent = ""/);
    assert.doesNotMatch(script, /button\.textContent = expanded \? "Hide Q1" : "Show Q1"/);
    assert.match(script, /syncSharedSequencerFoldControls\(\)/);
    assert.match(css, /\[data-lane-role="q1-issue"\]\s*{\s*display:\s*none;/s);
    assert.match(css, /\[data-lane-role="q1-issue"\]\.q1-issue-expanded\s*{\s*display:\s*inline;/s);
    assert.match(css, /\.shared-timeline-stage \[data-lane-role="q1-issue"\]\.q1-issue-expanded\s*{\s*display:\s*grid;/s);
    assert.match(css, /\.shared-q1-issue-toggle::before\s*{/);
    assert.match(css, /\.shared-q1-issue-toggle\[aria-expanded="true"\]::before\s*{/);
  });

  it("colors q1timeline-specific shared DOM blocks before selection", () => {
    const sharedCss = readFileSync(join(__dirname, "..", "src", "shared", "timeline", "renderer.css"), "utf-8");
    const q1TimelineCss = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.css"), "utf-8");

    assert.match(sharedCss, /\.shared-timeline-stage \.timeline-block\s*{[^}]*background:/s);
    assert.match(sharedCss, /\.shared-timeline-stage \.timeline-block\s*{[^}]*border-color:/s);
    assert.match(sharedCss, /\.shared-timeline-stage \.timeline-block\.has-command-color\s*{[^}]*border-color:\s*var\(--timeline-block-accent\);/s);
  });

  it("keeps q1timeline controls visible and reports when the webview script is ready", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const css = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.css"), "utf-8");
    const toolbarSource = extractFunctionSource(script, "ensureToolbarNode");

    assert.ok(toolbarSource);
    assert.match(toolbarSource, /document\.getElementById\("timeline-root"\)/);
    assert.match(toolbarSource, /timelineRoot\.parentElement\.insertBefore\(node, timelineRoot\)/);
    assert.match(script, /function toolbarSvgIcon\(name\)/);
    assert.match(script, /function setIconOnlyToolbarButton\(button, iconName, label\)/);
    assert.match(script, /function setToolbarStatusIcon\(node, iconName, label\)/);
    assert.match(toolbarSource, /node\.className = "q1timeline-toolbar"/);
    assert.match(toolbarSource, /setIconOnlyToolbarButton\(refreshButton, "refresh", "Refresh"\)/);
    assert.match(toolbarSource, /setToolbarStatusIcon\(autoUpdateIndicator, "sync", `Auto-update: \$\{initialUpdateMode\}`\)/);
    assert.match(toolbarSource, /setIconOnlyToolbarButton\(basisButton, basis === "aligned" \? "aligned" : "local", basis === "aligned" \? "Aligned time basis" : "Local time basis"\)/);
    assert.match(toolbarSource, /setIconOnlyToolbarButton\(normalButton, "normal", "Normal view"\)/);
    assert.match(toolbarSource, /setIconOnlyToolbarButton\(debugButton, "debug", "Debug view"\)/);
    assert.match(toolbarSource, /setIconOnlyToolbarButton\(zoomSelectionButton, "zoomSelection", "Zoom selection"\)/);
    assert.match(toolbarSource, /setIconOnlyToolbarButton\(zoomEventButton, "zoomEvent", "Zoom selected event"\)/);
    assert.doesNotMatch(toolbarSource, /textContent = "Refresh"/);
    assert.doesNotMatch(toolbarSource, /textContent = "Zoom selection"/);
    assert.doesNotMatch(toolbarSource, /textContent = "Zoom event"/);
    assert.doesNotMatch(toolbarSource, /textContent = "Pan </);
    assert.match(css, /\.q1timeline-toolbar-button\s*{/);
    assert.match(css, /\.q1timeline-toolbar-button svg,/);
    assert.match(script, /vscode\.postMessage\(\{ type: "webviewReady" \}\)/);
  });

  it("remembers the parameters disclosure without replacing other webview state", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const sources = ["readPersistedWebviewState", "writePersistedWebviewState", "installParametersPersistence"]
      .map((name) => {
        const source = extractFunctionSource(script, name);
        assert.ok(source);
        return source;
      });
    const load = Function("document", "vscode", `${sources.join("\n")}\nreturn installParametersPersistence;`);
    assert.match(script, /installParametersPersistence\(\);\s*installAnalysisDetailsPersistence\(\);/);

    for (const rememberedOpen of [undefined, true, false]) {
      let state: Record<string, unknown> = { analysisDetailsOpen: true, timelineTimeBasis: "local" };
      if (rememberedOpen !== undefined) {
        state.parametersOpen = rememberedOpen;
      }
      const listeners: Record<string, () => void> = {};
      const details = {
        open: false,
        dataset: {} as Record<string, string>,
        querySelector: () => ({ addEventListener: () => {} }),
        addEventListener: (name: string, listener: () => void) => { listeners[name] = listener; },
      };
      const install = load(
        { getElementById: (id: string) => id === "q1timeline-parameters" ? details : null },
        { getState: () => state, setState: (nextState: Record<string, unknown>) => { state = nextState; } },
      );

      install();
      assert.equal(details.open, rememberedOpen ?? false);
      const toggleListener = listeners.toggle;
      install();
      assert.equal(listeners.toggle, toggleListener);
      details.open = !details.open;
      listeners.toggle();
      assert.deepEqual(state, {
        analysisDetailsOpen: true,
        timelineTimeBasis: "local",
        parametersOpen: details.open,
      });
    }

    const installWithoutDetails = load({ getElementById: () => null }, {});
    assert.doesNotThrow(() => installWithoutDetails());
  });

  describe("inline analysis parameter editing", () => {
    class Node {
      dataset: Record<string, string> = {};
      attributes: Record<string, string> = {};
      listeners: Record<string, (event: any) => void> = {};
      children: Node[] = [];
      className = "";
      textContent = "";
      type = "";
      value = "";
      hidden = false;
      disabled = false;
      focused = false;
      selected = false;
      constructor(readonly tag: string) {}
      append(...nodes: Node[]) { this.children.push(...nodes); }
      setAttribute(name: string, value: string) { this.attributes[name] = value; }
      addEventListener(name: string, listener: (event: any) => void) { this.listeners[name] = listener; }
      focus() { this.focused = true; }
      select() { this.selected = true; }
      emit(name: string, extra: Record<string, unknown> = {}) {
        let prevented = false;
        this.listeners[name]?.({ preventDefault: () => { prevented = true; }, ...extra });
        return prevented;
      }
      querySelectorAll(selector: string): Node[] {
        return this.children.flatMap((child) => [
          ...(selector === "[data-parameter-index]" ? child.dataset.parameterIndex !== undefined
            : selector.startsWith(".") ? child.className === selector.slice(1)
              : child.tag === selector) ? [child] : [],
          ...child.querySelectorAll(selector),
        ]);
      }
      querySelector(selector: string) { return this.querySelectorAll(selector)[0]; }
    }

    function setup(options: { parameters?: any[]; projectFile?: string; overrideNames?: string[]; oldIr?: boolean } = {}) {
      const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
      const source = extractFunctionSource(script, "installParameterEditors");
      assert.ok(source);
      assert.match(script, /installAnalysisDetailsPersistence\(\);\s*installParameterEditors\(\);/);
      const parameters = options.parameters || [
        { name: "DRIVE_DURATION", value_json: "128", base_value_json: "64", overridden: true },
        { name: "FREQ_HZ", value_json: "50000000", overridden: false },
      ];
      const snapshot = { analysis: options.oldIr ? {} : { parameters } };
      const initialSnapshot = JSON.stringify(snapshot);
      const details = new Node("details");
      for (const [index, parameter] of parameters.entries()) {
        const row = new Node("tr");
        row.dataset.parameterIndex = String(index);
        const cell = new Node("td");
        const value = new Node("code");
        value.textContent = parameter.value_json;
        const annotation = new Node("span");
        annotation.className = "parameter-base";
        annotation.textContent = parameter.base_value_json ? ` ← base ${parameter.base_value_json}` : "";
        cell.append(value, annotation);
        row.append(cell);
        details.append(row);
      }
      const messages: any[] = [];
      const listeners: Record<string, (event: any) => void> = {};
      const install = Function("document", "window", "vscode", "timelineIr", "state", `${source}\nreturn installParameterEditors;`)(
        { getElementById: () => details, createElement: (tag: string) => new Node(tag) },
        { addEventListener: (type: string, listener: (event: any) => void) => { listeners[type] = listener; } },
        { postMessage: (message: unknown) => { messages.push(message); } },
        snapshot,
        { parameterProjectFile: options.projectFile ?? "C:/project/q1timeline.yml", parameterOverrideNames: options.overrideNames ?? ["DRIVE_DURATION"] },
      );
      install();
      return {
        details, messages, snapshot, initialSnapshot, install,
        row: (index = 0) => details.children[index],
        error: (name: string, message: string) => listeners.message({ data: { type: "analysisParameterError", name, message } }),
      };
    }

    it("opens a focused editor and cancels without changing analyzed values or source annotations", () => {
      const test = setup();
      const row = test.row();
      row.querySelector(".parameter-edit").emit("click");
      const input = row.querySelector(".parameter-input");
      assert.equal(input.value, "128");
      assert.equal(input.type, "text");
      assert.equal(input.attributes["aria-label"], "DRIVE_DURATION JSON value");
      assert.equal(input.focused && input.selected, true);
      assert.equal(row.querySelector(".parameter-editor").hidden, false);
      assert.equal(test.row(1).querySelector(".parameter-edit").disabled, true);
      input.value = "256";
      test.row(1).querySelector(".parameter-edit").emit("click");
      assert.equal(test.row(1).querySelector(".parameter-editor").hidden, true);
      row.querySelector(".parameter-cancel").emit("click");
      assert.equal(row.querySelector(".parameter-editor").hidden, true);
      assert.equal(test.row(1).querySelector(".parameter-edit").disabled, false);
      assert.equal(row.querySelector("code").textContent, "128");
      assert.equal(row.querySelector(".parameter-base").textContent, " ← base 64");
      assert.deepEqual(test.messages, []);
      assert.equal(JSON.stringify(test.snapshot), test.initialSnapshot);
      row.querySelector(".parameter-edit").emit("click");
      assert.equal(input.value, "128");
      assert.equal(input.emit("keydown", { key: "Escape" }), true);
      assert.equal(row.querySelector(".parameter-editor").hidden, true);
    });

    it("submits the Apply/Enter form without navigation and preserves exact raw JSON", () => {
      for (const raw of ["9007199254740993", '"0016"', '{"expr":"4 * 64"}']) {
        const test = setup();
        const row = test.row();
        row.querySelector(".parameter-edit").emit("click");
        row.querySelector(".parameter-input").value = `  ${raw}  `;
        assert.equal(row.querySelector(".parameter-apply").type, "submit");
        assert.equal(row.querySelector(".parameter-editor").emit("submit"), true);
        assert.deepEqual(test.messages, [{
          type: "setAnalysisParameter", name: "DRIVE_DURATION", valueJson: raw, projectFile: "C:/project/q1timeline.yml",
        }]);
        assert.equal(row.querySelector(".parameter-status").textContent, "Applying…");
        assert.equal(row.querySelector(".parameter-input").disabled, true);
        assert.equal(row.querySelector(".parameter-cancel").disabled, true);
        assert.equal(test.row(1).querySelector(".parameter-edit").disabled, true);
        row.querySelector(".parameter-editor").emit("submit");
        assert.equal(test.messages.length, 1);
        assert.equal(row.querySelector("code").textContent, "128");
        assert.equal(JSON.stringify(test.snapshot), test.initialSnapshot);
      }
    });

    it("shows validation errors inline and permits retry without losing the draft or applied value", () => {
      const test = setup();
      const row = test.row();
      row.querySelector(".parameter-edit").emit("click");
      const input = row.querySelector(".parameter-input");
      input.value = "invalid json";
      row.querySelector(".parameter-editor").emit("submit");
      test.error("FREQ_HZ", "Unrelated error");
      assert.equal(input.disabled, true);
      test.error("DRIVE_DURATION", "Enter a valid JSON value.");
      assert.equal(input.disabled, false);
      assert.equal(input.value, "invalid json");
      assert.equal(row.querySelector(".parameter-apply").disabled, false);
      assert.equal(row.querySelector(".parameter-error").attributes.role, "alert");
      assert.equal(row.querySelector(".parameter-error").textContent, "Enter a valid JSON value.");
      assert.equal(row.querySelector(".parameter-error").hidden, false);
      assert.equal(row.querySelector(".parameter-status").textContent, "");
      assert.equal(row.querySelector("code").textContent, "128");
      assert.equal(test.row(1).querySelector(".parameter-edit").disabled, true);
      input.value = "-1";
      row.querySelector(".parameter-editor").emit("submit");
      assert.equal(test.messages[1].valueJson, "-1");
      assert.equal(row.querySelector(".parameter-error").hidden, true);
      test.error("DRIVE_DURATION", "Duration must not be negative.");
      assert.equal(input.value, "-1");
      assert.equal(row.querySelector(".parameter-apply").disabled, false);
      assert.equal(row.querySelector("code").textContent, "128");
      input.value = "256";
      row.querySelector(".parameter-editor").emit("submit");
      assert.equal(test.messages[2].valueJson, "256");
    });

    it("resets only session overrides and restores controls after a reset failure", () => {
      const test = setup();
      const row = test.row();
      assert.equal(row.querySelector(".parameter-reset").hidden, false);
      assert.equal(test.row(1).querySelector(".parameter-reset").hidden, true);
      test.row(1).querySelector(".parameter-reset").emit("click");
      assert.equal(test.messages.length, 0);
      row.querySelector(".parameter-reset").emit("click");
      assert.deepEqual(test.messages, [{ type: "resetAnalysisParameter", name: "DRIVE_DURATION", projectFile: "C:/project/q1timeline.yml" }]);
      assert.equal(row.querySelector(".parameter-status").textContent, "Resetting…");
      assert.equal(row.querySelector(".parameter-edit").disabled, true);
      test.error("DRIVE_DURATION", "Analysis failed.");
      assert.equal(row.querySelector(".parameter-edit").disabled, false);
      assert.equal(row.querySelector(".parameter-reset").disabled, false);
      assert.equal(row.querySelector(".parameter-error").hidden, false);
      assert.equal(row.querySelector(".parameter-editor").hidden, true);
      assert.equal(row.querySelector("code").textContent, "128");
      assert.equal(JSON.stringify(test.snapshot), test.initialSnapshot);
    });

    it("stays read-only for old snapshots, missing projects, and unsupported parameter names", () => {
      for (const options of [
        { oldIr: true }, { projectFile: "" },
        { parameters: ["", " spaced", "trailing ", "name=value"].map((name) => ({ name, value_json: "4" })) },
      ]) {
        const test = setup(options);
        assert.equal(test.details.querySelectorAll(".parameter-edit").length, 0);
        assert.deepEqual(test.messages, []);
      }
      const test = setup({ overrideNames: [] });
      assert.equal(test.row().querySelector(".parameter-reset").hidden, true);
      const editorCount = test.details.querySelectorAll(".parameter-edit").length;
      test.install();
      assert.equal(test.details.querySelectorAll(".parameter-edit").length, editorCount);
    });
  });

  it("opens branch action popovers with source-line labels and loop action popovers from lanes", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const css = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.css"), "utf-8");

    assert.match(script, /function installTimelineControlChips\(\)/);
    assert.match(script, /function ensureTimelineControlOverlay\(\)/);
    assert.match(script, /function openTimelineControlPopover\(chip, event, actionKind\)/);
    assert.match(script, /function positionTimelineControlPopover\(popover, chip\)/);
    assert.match(script, /className = `q1timeline-control-chip q1timeline-control-chip-\$\{actionKind\}`/);
    assert.match(script, /function branchAssumptionIconName\(meta\)/);
    assert.match(script, /function branchSourceJumpLabel\(event\)/);
    assert.match(script, /Line \$\{line\}: \$\{raw\}/);
    assert.match(script, /function branchPathDescriptor\(event, path\)/);
    assert.match(script, /chip\.setAttribute\("aria-label", isFeedback \?.* : branchSourceJumpLabel\(event\)\)/);
    assert.match(script, /chip\.innerHTML = timelineControlSvgIcon\(branchAssumptionIconName\(meta\)\)/);
    assert.doesNotMatch(script, /chip\.innerHTML = timelineControlSvgIcon\("branch"\)/);
    assert.doesNotMatch(script, /Branch path: condition/);
    assert.match(script, /selectEventNode\(sourceNode, \{ notify: false \}\)/);
    assert.match(script, /openTimelineControlPopover\(chip, event, actionKind\)/);
    assert.match(script, /function installLoopLaneTimelineControls\(\)/);
    assert.match(script, /node\.classList\.add\("q1timeline-control-loop-lane"\)/);
    assert.match(script, /node\.setAttribute\("role", "button"\)/);
    assert.match(script, /node\.tabIndex = 0/);
    assert.match(script, /openTimelineControlPopover\(node, event, "loop"\)/);
    assert.doesNotMatch(script, /q1timeline-control-chip q1timeline-control-chip-loop/);
    assert.match(script, /document\.body\.append\(overlay\)/);
    assert.match(script, /chip\.getBoundingClientRect\(\)/);
    assert.match(script, /Math\.max\(8, Math\.min\(left,/);
    assert.match(script, /function renderBranchControls\(popover, event\)/);
    assert.match(script, /vscode\.postMessage\(\{ type: playback \? "setExecutionBranch" : "setBranchAssumption", branchId, path, eventId: String\(event.id\) \}\)/);
    assert.doesNotMatch(script, /postSourceJump/);
    assert.match(script, /vscode\.postMessage\(\{ type: "setLoopPreview", loopKey, visibleIterations \}\)/);
    assert.match(css, /\.q1timeline-control-overlay\s*{/);
    assert.match(css, /\.q1timeline-control-chip\s*{/);
    assert.match(css, /\.q1timeline-control-loop-lane\s*{/);
    assert.match(css, /\.q1timeline-control-loop-lane::before\s*{/);
    assert.match(css, /\.q1timeline-control-loop-lane\[aria-expanded="true"\]/);
    assert.match(css, /\.q1timeline-control-popover\s*{/);
    assert.match(css, /\.q1timeline-control-action\s*{/);
    assert.match(css, /\.q1timeline-control-chip\s*{[^}]*background:\s*transparent;/s);
    assert.match(css, /\.q1timeline-control-popover\[data-placement="top"\]::before\s*{/);
  });

  it("stacks overlapping branch controls at the same timeline position", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const css = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.css"), "utf-8");
    const controls = loadQ1TimelineControlChipFunctions(script);

    const laneNode = { dataset: { lane: "sequencer:seq0" } };
    const blockNode = {
      style: { left: "10%", width: "0.05%" },
      closest: (selector: string) => selector === "[data-lane]" ? laneNode : undefined,
    };

    assert.equal(controls.timelineControlChipLeft(blockNode), "10.025%");
    assert.equal(controls.timelineControlChipStackKey(blockNode), "sequencer:seq0@10.025%");
    assert.equal(controls.timelineControlChipTop(0), "1px");
    assert.equal(controls.timelineControlChipTop(1), "18px");
    assert.match(script, /const chipStackCounts = new Map\(\)/);
    assert.match(script, /chip\.style\.top = timelineControlChipTop\(stackIndex\)/);
    assert.match(script, /const track = sourceNode\.closest\("\.lane-track"\)/);
    assert.match(script, /track\.append\(chip\)/);
    assert.match(css, /\.q1timeline-control-chip\s*{[^}]*transform:\s*translateX\(-50%\) translateY\(var\(--timeline-loop-offset, 0px\)\);/s);
  });

  it("optimistically collapses loop previews when reset is clicked", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");

    assert.match(script, /function applyLoopPreviewResetOptimistically\(loopKey\)/);
    assert.match(script, /new RegExp\(`:loop-\$\{escapeRegExp\(loopId\)\}-iter-\\\\d\+\$`\)/);
    assert.match(script, /timelineIr\.events = events\.filter/);
    assert.match(script, /timelineIr\.feedback_flows = timelineIr\.feedback_flows\.filter/);
    assert.match(script, /renderSharedTimelineIfAvailable\(currentTimelineMode\(\), \{ preserveSelection: true \}\)/);
    assert.match(script, /applyLoopPreviewResetOptimistically\(loopKey\);\s*vscode\.postMessage\(\{ type: "setLoopPreview", loopKey, visibleIterations \}\)/);
  });

  it("expands Q1 issue lanes when showing another loop iteration", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const { loopPreviewEventIds } = loadQ1TimelineLoopPreviewFunctions(script);

    assert.deepEqual(
      loopPreviewEventIds({
        first_iteration_event_ids: ["seq0:q1-0", "seq0:play-0"],
        preview_iteration_event_ids: {
          0: ["seq0:q1-0", "seq0:play-0"],
          1: ["seq0:q1-1", "seq0:play-1"],
        },
      }),
      ["seq0:q1-0", "seq0:play-0", "seq0:q1-1", "seq0:play-1"],
    );
    assert.match(script, /const expandedQ1IssueSequencers = new Set\(initialExpandedQ1IssueSequencers\(\)\)/);
    assert.match(script, /function expandQ1IssueLanesForLoopPreview\(meta\)/);
    assert.match(script, /expandQ1IssueLanesForLoopPreview\(meta\);\s*const visibleIterations = visible \+ 1;/);
    assert.match(script, /writePersistedWebviewState\(\{ expandedQ1IssueSequencers: Array\.from\(expandedQ1IssueSequencers\) \}\)/);
  });

  it("labels zoomed Q1 issue blocks with command names only", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const { eventLabelToken } = loadQ1TimelineLabelFunctions(script);

    assert.equal(eventLabelToken({ kind: "q1_issue", source: { raw: "    wait_sync 4" } }), "wait_sync");
    assert.equal(eventLabelToken({ kind: "q1_issue", meta: { op: "set_mrk" } }), "set_mrk");
  });

  it("keeps short RT command labels visible in narrow zoomed blocks", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const { eventInlineLabel } = loadQ1TimelineLabelFunctions(script);

    assert.equal(eventInlineLabel({ kind: "play" }, 30), "play");
    assert.equal(eventInlineLabel({ kind: "upd_param" }, 24), "upd");
  });

  it("keeps normal-mode q1timeline feedback instruction blocks hidden behind flow overlays", () => {
    const css = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.css"), "utf-8");
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");

    assert.match(css, /body\.mode-normal\s+\.event\.normal-feedback-collapsed\s*{\s*display:\s*none;/s);
    assert.match(script, /eventNode\.classList\.contains\("normal-feedback-collapsed"\)/);
    assert.match(script, /currentTimelineMode\(\) === "normal"/);
  });

  it("suppresses q1timeline branch and feedback label halos in dark webviews", () => {
    const css = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.css"), "utf-8");

    assert.match(css, /body\.vscode-dark\s+\.branch-marker\s+text,[\s\S]*body\.vscode-dark\s+\.feedback-flow-label,[\s\S]*\{[^}]*paint-order:\s*normal;/s);
    assert.match(css, /body\.vscode-dark\s+\.branch-marker\s+text,[\s\S]*body\.vscode-dark\s+\.feedback-flow-label,[\s\S]*\{[^}]*stroke:\s*none;/s);
  });

  it("does not let q1timeline confidence fills color branch marker hitboxes", () => {
    const css = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.css"), "utf-8");

    assert.doesNotMatch(css, /body\.vscode-dark\s+\.confidence-assumed\s+rect/);
    assert.doesNotMatch(css, /body\.vscode-dark\s+\.confidence-unknown\s+rect/);
    assert.match(css, /body\.vscode-dark\s+\.event\.confidence-assumed\s*>\s*rect\s*\{/);
    assert.match(css, /body\.vscode-dark\s+\.event\.confidence-unknown\s*>\s*rect\s*,/);
  });

  it("keeps q1timeline preview labels compact and suppresses overlaps after zoom", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const visibleHelperIndex = script.indexOf("function isTimelineEventVisible(eventNode)");
    const initializeIndex = script.indexOf("function initializeRenderedTimeline()");

    assert.match(script, /function eventInlineLabel\(event, width\)/);
    assert.match(script, /function suppressOverlappingEventLabels\(svg\)/);
    assert.match(script, /suppressOverlappingEventLabels\(svg\)/);
    assert.match(script, /function labelCanReserveSpace\(label, eventNode\)/);
    assert.match(script, /\.filter\(\(\{ label, eventNode \}\) => labelCanReserveSpace\(label, eventNode\)\)/);
    assert.ok(visibleHelperIndex >= 0);
    assert.ok(initializeIndex >= 0);
    assert.ok(visibleHelperIndex < initializeIndex);
    assert.match(script, /const laneNode = eventNode\.closest\("\[data-lane\]"\)/);
    assert.match(script, /laneNode && isTimelineNodeHidden\(laneNode\)/);
    assert.match(script, /event\.kind === "acquire"[\s\S]*return "acq";/);
    assert.match(script, /event\.kind === "wait_trigger"[\s\S]*return "trig";/);
  });

  it("keeps q1timeline overlay labels compact in the VS Code webview", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const css = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.css"), "utf-8");

    assert.match(script, /function feedbackFlowVisibleLabel\(flowNode\)/);
    assert.match(script, /label\.textContent = feedbackFlowVisibleLabel\(flowNode\)/);
    assert.match(script, /function suppressOverlappingOverlayLabels\(svg\)/);
    assert.match(script, /suppressOverlappingOverlayLabels\(svg\)/);
    assert.match(css, /\.branch-marker-condition\s*\{[^}]*display:\s*none;/s);
  });

  it("makes q1timeline diagnostic badges hoverable and selectable", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const css = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.css"), "utf-8");

    assert.match(script, /function installDiagnosticBadgeInteractions\(\)/);
    assert.match(script, /badge\.setAttribute\("tabindex", "0"\)/);
    assert.match(script, /selectEventNode\(eventNode\)/);
    assert.match(css, /\.diagnostic-badge\s*\{[^}]*pointer-events:\s*all;/s);
    assert.match(css, /\.diagnostic-badge\s*\{[^}]*cursor:\s*help;/s);
  });

  it("uses the rich standalone diagnostics panel without duplicate webview diagnostics chrome", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");

    assert.doesNotMatch(script, /q1timeline-diagnostics-list/);
    assert.doesNotMatch(script, /function renderDiagnosticsList\(diagnostics\)/);
    assert.doesNotMatch(script, /renderDiagnosticsList\(initialDiagnostics\)/);
    assert.doesNotMatch(script, /q1timeline-diagnostics-toggle/);
    assert.doesNotMatch(script, /q1timeline-analysis-status/);
    assert.doesNotMatch(script, /q1timeline-diagnostics-summary/);
    assert.doesNotMatch(script, /q1timeline-confidence-legend/);
    assert.doesNotMatch(script, /renderAnalysisStatus\(initialAnalysisStatus\)/);
    assert.doesNotMatch(script, /renderDiagnosticSummary\(initialDiagnosticSummary\)/);
    assert.doesNotMatch(script, /renderConfidenceLegend\(\)/);
  });

  it("starts both timeline renderers at the displayed nested and multi-sequencer loop previews", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const event = (id: string, kind: string, start: number, end: number, meta: any = {}) => ({
      id, kind, sequencer_id: id.split(":")[0],
      t0: { kind: "concrete", value: start }, t1: { kind: "concrete", value: end },
      meta: { aligned_t0: start + 100, aligned_t1: end + 100, ...meta },
    });
    const events = [
      event("seq0:inner", "loop_block", 20, 40, {
        count: 6, visible_iteration_count: 1, preview_policy: "first_iteration_only",
        first_iteration_event_ids: ["seq0:wait"],
      }),
      event("seq0:wait", "wait", 20, 40),
      event("seq0:outer", "loop_block", 20, 600, {
        count: 100, visible_iteration_count: 1, preview_policy: "first_iteration_only",
        first_iteration_event_ids: ["seq0:inner", "seq0:wait", "seq0:play"],
      }),
      event("seq0:play", "play", 580, 600),
      event("seq1:outer", "loop_block", 20, 800, {
        count: 100, visible_iteration_count: 1, preview_policy: "first_iteration_only",
        first_iteration_event_ids: ["seq1:issue"],
      }),
      event("seq1:issue", "q1_issue", 800, 820),
      event("seq0:stop", "stop", 40_000_000, 40_000_000),
      event("seq0:stop-issue", "q1_issue", 40_000_000, 40_000_004, { op: "stop" }),
    ];
    for (const basis of ["local", "aligned"]) {
      const expected = { min: 0, max: basis === "local" ? 820 : 920 };
      assert.deepEqual(loadQ1TimelineZoomRuntime(script, events, { basis }).currentTimelineWindow(), expected);
      const svg = loadQ1TimelineZoomRuntime(script, events, { basis });
      svg.restoreTimelineZoom();
      assert.deepEqual(svg.currentTimelineWindow(), expected);
      assert.deepEqual(svg.state(), {});
    }

    events[4].meta.preview_policy = "requested_iterations";
    events[4].meta.visible_iteration_count = 2;
    events[4].meta.preview_iteration_event_ids = { 0: ["seq1:issue"], 1: ["seq1:next"] };
    events.push(event("seq1:next", "q1_issue", 1600, 1700));
    assert.deepEqual(loadQ1TimelineZoomRuntime(script, events).currentTimelineWindow(), { min: 0, max: 1700 });
    assert.deepEqual(
      loadQ1TimelineZoomRuntime(script, events, { full: { min: 200, max: 1600 } }).currentTimelineWindow(),
      { min: 200, max: 1600 },
    );
  });

  it("preserves saved zoom and keeps full-range defaults, Fit, and Reset available", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const full = { min: 0, max: 40_160_000 };
    const events = [{
      id: "loop", kind: "loop_block",
      t0: { kind: "concrete", value: 0 }, t1: { kind: "concrete", value: 800 },
      meta: { count: 100, visible_iteration_count: 1, preview_policy: "first_iteration_only" },
    }];
    const state = { timelineWindow: { min: 120, max: 180, fullMin: full.min, fullMax: full.max, basis: "local" } };
    const shared = loadQ1TimelineZoomRuntime(script, events, { state });
    assert.deepEqual(shared.currentTimelineWindow(), { min: 120, max: 180 });
    const svg = loadQ1TimelineZoomRuntime(script, events, { state });
    svg.restoreTimelineZoom();
    assert.deepEqual(svg.currentTimelineWindow(), { min: 120, max: 180 });
    assert.deepEqual(svg.state(), state);
    for (const savedWindow of [
      { ...state.timelineWindow, fullMax: 10_000 },
      { ...state.timelineWindow, basis: "aligned" },
    ]) {
      assert.deepEqual(
        loadQ1TimelineZoomRuntime(script, events, { state: { timelineWindow: savedWindow } }).currentTimelineWindow(),
        { min: 0, max: 800 },
      );
    }
    shared.fitTimelineToWindow();
    assert.deepEqual(shared.currentTimelineWindow(), full);
    svg.resetTimelineZoom();
    assert.deepEqual(svg.currentTimelineWindow(), full);
    assert.deepEqual(loadQ1TimelineZoomRuntime(script, []).currentTimelineWindow(), full);
    events[0].meta.count = 1;
    assert.deepEqual(loadQ1TimelineZoomRuntime(script, events).currentTimelineWindow(), full);
  });

  it("includes displayed pulses after hidden wait iterations with or without a fully shown outer loop", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const event = (id: string, kind: string, start: number, end: number, meta: any = {}) => ({
      id, kind, t0: { kind: "concrete", value: start }, t1: { kind: "concrete", value: end }, meta,
    });
    const events = [
      event("inner", "loop_block", 0, 100, {
        count: 6, visible_iteration_count: 1, preview_policy: "first_iteration_only",
        first_iteration_event_ids: ["wait"],
      }),
      event("wait", "wait", 0, 100),
      event("play", "play", 600, 620),
      event("stop", "stop", 40_000_000, 40_000_000),
      event("stop-issue", "q1_issue", 40_000_000, 40_000_004, { op: "stop" }),
    ];
    assert.deepEqual(loadQ1TimelineZoomRuntime(script, events).currentTimelineWindow(), { min: 0, max: 620 });
    events.push(event("outer", "loop_block", 0, 620, {
      count: 1, visible_iteration_count: 1, preview_policy: "first_iteration_only",
      first_iteration_event_ids: ["wait", "play"],
    }));
    const svg = loadQ1TimelineZoomRuntime(script, events);
    svg.restoreTimelineZoom();
    assert.deepEqual(svg.currentTimelineWindow(), { min: 0, max: 620 });
  });

  it("zooms nanosecond events and selections independently of a long execution extent", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const runtime = loadQ1TimelineZoomRuntime(script, []);
    const event = (start: number, end: number) => ({
      t0: { kind: "concrete", value: start }, t1: { kind: "concrete", value: end },
    });
    for (const [start, end, min, max] of [
      [400, 404, 386, 418],
      [400, 420, 380, 440],
      [100, 100, 84, 116],
      [0, 4, 0, 32],
      [40_159_996, 40_160_000, 40_159_968, 40_160_000],
    ]) {
      runtime.zoomTimelineToEvent(event(start, end));
      assert.deepEqual(runtime.currentTimelineWindow(), { min, max });
    }
    runtime.selectRange(404, 400);
    assert.deepEqual(runtime.currentTimelineWindow(), { min: 400, max: 404 });
    assert.equal(runtime.selection(), undefined);
    runtime.zoomTimelineAround(2);
    assert.deepEqual(runtime.currentTimelineWindow(), { min: 401, max: 403 });
    runtime.zoomTimelineAround(8);
    assert.equal(runtime.currentTimelineWindow().max - runtime.currentTimelineWindow().min, 1);
    runtime.setTimelineWindow(-2, -1);
    assert.deepEqual(runtime.currentTimelineWindow(), { min: 0, max: 1 });
    runtime.setTimelineWindow(40_160_001, 40_160_002);
    assert.deepEqual(runtime.currentTimelineWindow(), { min: 40_159_999, max: 40_160_000 });
  });

  it("keeps folded-time navigation, ticks, source highlights, and hardware cursor in real nanoseconds", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const scale = {
      gaps: [{ start: 1000, end: 99_000, displayStart: 1000, displayEnd: 1100 }],
      toDisplay: (time: number) => time <= 1000 ? time : time < 99_000 ? 1000 + (time - 1000) / 980 : time - 97_900,
      fromDisplay: (time: number) => time <= 1000 ? time : time < 1100 ? 1000 + (time - 1000) * 980 : time + 97_900,
    };
    const full = { min: 0, max: 100_000 };
    const runtime = loadQ1TimelineZoomRuntime(script, [], { full, scale });
    assert.equal(runtime.timelineTimeFromPointer({ clientX: 600 }), 50_000);
    assert.equal(runtime.scaleTimelineTime(50_000, full, { left: 100, right: 1100 }), 600);
    assert.deepEqual(runtime.sharedTimelineTicks(), [
      { leftPercent: 0, label: "0 ns" },
      { leftPercent: 47.619, label: "1000 ns" },
      { leftPercent: 95.238, label: "99900 ns" },
      { leftPercent: 100, label: "100000 ns" },
    ]);
    runtime.renderHardwareCursor({ time: 50_000 });
    assert.equal(runtime.cursor().style.left, "50%");
    assert.equal(runtime.cursor().hidden, false);

    const anchorBefore = runtime.timelineTimeFromPointer({ clientX: 850 });
    runtime.handleTimelineWheel({ clientX: 850, deltaY: -1, preventDefault() {} });
    assert.deepEqual(runtime.currentTimelineWindow(), { min: 315, max: 99_895 });
    assert.equal(runtime.timelineTimeFromPointer({ clientX: 850 }), anchorBefore);

    runtime.setTimelineWindow(500, 99_500);
    runtime.handleTimelineWheel({ deltaX: 100, preventDefault() {} });
    assert.deepEqual(runtime.currentTimelineWindow(), { min: 610, max: 99_610 });
    runtime.setTimelineWindow(500, 99_500);
    runtime.panTimeline(1);
    assert.deepEqual(runtime.currentTimelineWindow(), { min: 775, max: 99_775 });

    runtime.resetTimelineZoom();
    runtime.zoomTimelineToHighlightedEvent({
      t0: { kind: "concrete", value: 99_000 }, t1: { kind: "concrete", value: 99_004 },
    });
    assert.deepEqual(runtime.currentTimelineWindow(), { min: 85_280, max: 99_018 });
    assert.equal(runtime.state().timelineWindow.min, 85_280);
    assert.equal(runtime.state().timelineWindow.max, 99_018);
  });

  it("keeps a one-nanosecond view accurate when long waits are folded after zooming", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const scale = {
      gaps: [{ start: 1000, end: 99_000, displayStart: 1000, displayEnd: 1100 }],
      toDisplay: (time: number) => time <= 1000 ? time : time < 99_000 ? 1000 + (time - 1000) / 980 : time - 97_900,
      fromDisplay: (time: number) => time <= 1000 ? time : time < 1100 ? 1000 + (time - 1000) * 980 : time + 97_900,
    };
    const runtime = loadQ1TimelineZoomRuntime(script, [], { full: { min: 0, max: 100_000 } });
    runtime.setTimelineWindow(50_000, 50_001);
    assert.equal(runtime.timelineTimeFromPointer({ clientX: 1100 }), 50_001);
    runtime.setTimeScale(scale);
    assert.ok(Math.abs(runtime.timelineTimeFromPointer({ clientX: 1100 }) - 50_001) < 1e-6);
    assert.equal(runtime.scaleTimelineTime(50_001, runtime.currentTimelineWindow(), { left: 0, right: 1000 }), 1000);
    assert.deepEqual(runtime.sharedTimelineTicks(), [
      { leftPercent: 0, label: "50000 ns" }, { leftPercent: 100, label: "50001 ns" },
    ]);
    runtime.renderHardwareCursor({ time: 50_001 });
    assert.equal(runtime.cursor().style.left, "100%");
    runtime.zoomTimelineToHighlightedEvent({
      t0: { kind: "concrete", value: 50_000 }, t1: { kind: "concrete", value: 50_001 },
    });
    assert.deepEqual(runtime.currentTimelineWindow(), { min: 50_000, max: 50_001 });

    runtime.handleTimelineWheel({ deltaX: 1000, preventDefault() {} });
    assert.deepEqual(runtime.currentTimelineWindow(), { min: 50_001, max: 50_002 });
    runtime.zoomTimelineAround(2);
    assert.equal(runtime.currentTimelineWindow().max - runtime.currentTimelineWindow().min, 1);
    runtime.setTimelineWindow(50_000, 50_004);
    runtime.panTimeline(1);
    assert.deepEqual(runtime.currentTimelineWindow(), { min: 50_001, max: 50_005 });
    runtime.zoomTimelineAround(2);
    assert.deepEqual(runtime.currentTimelineWindow(), { min: 50_002, max: 50_004 });
  });

  it("expands and refolds one shared interval while retaining its controls and real coordinates", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const event = (kind: string, start: number, end: number, sequencer_id: string) => ({
      kind, sequencer_id, confidence: "exact", t0: start, t1: end,
    });
    const runtime = loadQ1TimelineFoldRuntime(script, {
      alignment: Object.fromEntries(["q0", "q1"].map((id) => [id, { offset: 0, mode: "manual:0", confidence: "exact" }])),
      events: [event("wait", 0, 10_000, "q0"), event("wait", 0, 10_000, "q1"), event("marker_state", 5000, 5000, "q0")],
    }, { current: { min: 100, max: 9900 } });
    const preserved = JSON.parse(JSON.stringify(runtime.preserved()));
    runtime.renderWaitFolds();
    runtime.renderHardwareCursor();
    const originalGaps = JSON.parse(JSON.stringify(runtime.timelineTimeScale().gaps));
    const sampleTimes = [0, 100, 1000, 4950, 5050, 9000, 9900, 10_000];
    const originalMapping = sampleTimes.map(runtime.displayTimelineTime);
    const originalCursorStyles = runtime.tracks.map((track: any) => ({ ...track.querySelector(".hardware-time-cursor").style }));
    assert.equal(runtime.displayTimelineTime(10_000), 192);
    const markers = runtime.tracks.map((track: any) => track.children.filter((child: any) => child.classList.contains("q1timeline-wait-fold")));
    assert.deepEqual(markers.map((items: any[]) => items.length), [2, 2, 2]);
    assert.deepEqual(markers[0].map((node: any) => node.style), markers[1].map((node: any) => node.style));
    assert.deepEqual(markers[1].map((node: any) => node.style), markers[2].map((node: any) => node.style));
    const button = markers[1][0].children.find((child: any) => child.tagName === "BUTTON");
    assert.ok(button, "The folded interval must be directly expandable from a sequencer lane");
    assert.equal(button.children.find((node: any) => node.classList.contains("q1timeline-fold-slashes")).textContent, "//");
    assert.equal(button.children.find((node: any) => node.classList.contains("q1timeline-fold-duration")).textContent, "4.97 µs");
    assert.equal(button.getAttribute("aria-expanded"), "false");
    assert.equal(button.dataset.trackIndex, "1");
    assert.equal(runtime.isTimelineSelectionTarget(button), false);
    let pointerStopped = false;
    let clickStopped = false;
    button.focus();
    button.listeners.pointerdown({ stopPropagation() { pointerStopped = true; }, preventDefault() {} });
    button.listeners.click({ stopPropagation() { clickStopped = true; }, preventDefault() {} });
    assert.equal(pointerStopped, true);
    assert.equal(clickStopped, true);
    assert.equal(runtime.renders(), 1);
    assert.deepEqual(runtime.preserved(), preserved);
    assert.deepEqual(runtime.state().timelineExpandedGaps, [{ start: 16, end: 4984, basis: "local", fullMin: 0, fullMax: 10_000 }]);
    assert.deepEqual(runtime.timelineTimeScale().gaps.map(({ start, end }: any) => ({ start, end })), [{ start: 5016, end: 9984 }]);
    assert.equal(runtime.displayTimelineTime(10_000), 5096);
    for (const time of [100, 1000, 4950, 5050, 9000, 9900]) {
      const display = runtime.displayTimelineTime(time);
      assert.ok(Math.abs(runtime.realTimelineTime(display) - time) < 1e-6);
      const ratio = (display - runtime.displayTimelineTime(100)) / (runtime.displayTimelineTime(9900) - runtime.displayTimelineTime(100));
      assert.ok(Math.abs(runtime.timelineTimeFromPointer({ clientX: 100 + ratio * 1000 }) - time) < 1e-6);
    }
    const expandedMarkers = runtime.tracks.map((track: any) => track.children.filter((child: any) => child.classList.contains("q1timeline-wait-fold")));
    assert.deepEqual(expandedMarkers.map((items: any[]) => items.length), [2, 2, 2]);
    assert.deepEqual(expandedMarkers.map((items: any[]) => items.map((node: any) => node.classList.contains("is-expanded"))), [[true, false], [true, false], [true, false]]);
    assert.deepEqual(expandedMarkers[0].map((node: any) => node.style), expandedMarkers[1].map((node: any) => node.style));
    assert.deepEqual(expandedMarkers[1].map((node: any) => node.style), expandedMarkers[2].map((node: any) => node.style));
    for (const items of expandedMarkers) {
      const expandedButton = items[0].children.find((child: any) => child.tagName === "BUTTON");
      assert.equal(expandedButton.getAttribute("aria-expanded"), "true");
      assert.equal(expandedButton.children.some((node: any) => node.classList.contains("q1timeline-fold-slashes")), false);
      assert.equal(expandedButton.children.find((node: any) => node.classList.contains("q1timeline-fold-duration")).textContent, "4.97 µs expanded · collapse");
    }
    const collapseButton = expandedMarkers[1][0].children.find((child: any) => child.tagName === "BUTTON");
    assert.equal(runtime.activeElement(), collapseButton);
    assert.equal(collapseButton.dataset.timeStart, "16");
    assert.equal(collapseButton.dataset.timeEnd, "4984");
    assert.equal(collapseButton.dataset.trackIndex, "1");
    assert.equal(runtime.toolbar.title, "Expand all unchanged intervals");

    collapseButton.listeners.click({ stopPropagation() {}, preventDefault() {} });
    assert.equal(runtime.renders(), 2);
    assert.deepEqual(runtime.preserved(), preserved);
    assert.deepEqual(runtime.state().timelineExpandedGaps, []);
    assert.deepEqual(runtime.timelineTimeScale().gaps, originalGaps);
    assert.deepEqual(sampleTimes.map(runtime.displayTimelineTime), originalMapping);
    assert.deepEqual(runtime.tracks.map((track: any) => track.querySelector(".hardware-time-cursor").style), originalCursorStyles);
    const refoldedMarkers = runtime.tracks.map((track: any) => track.children.filter((child: any) => child.classList.contains("q1timeline-wait-fold")));
    assert.deepEqual(refoldedMarkers.map((items: any[]) => items.map((node: any) => node.classList.contains("is-expanded"))), [[false, false], [false, false], [false, false]]);
    const refoldedButton = refoldedMarkers[1][0].children.find((child: any) => child.tagName === "BUTTON");
    assert.equal(runtime.activeElement(), refoldedButton);
    assert.equal(refoldedButton.getAttribute("aria-expanded"), "false");
    assert.equal(refoldedButton.children.find((node: any) => node.classList.contains("q1timeline-fold-slashes")).textContent, "//");
  });

  it("restores per-gap choices only for matching coordinates and can expand or refold every interval", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const ir = { events: [
      { kind: "wait", sequencer_id: "q0", confidence: "exact", t0: 0, t1: 10_000 },
      { kind: "marker_state", sequencer_id: "q0", confidence: "exact", t0: 5000, t1: 5000 },
    ] };
    const state = { timelineWaitsCollapsed: true, timelineExpandedGaps: [
      { start: 16, end: 4984, basis: "local", fullMin: 0, fullMax: 10_000 },
    ] };
    const runtime = loadQ1TimelineFoldRuntime(script, ir, { state });
    assert.deepEqual([32, 4968, 1_250_000].map(runtime.formatFoldDuration), ["32 ns", "4.97 µs", "1.25 ms"]);
    assert.equal(runtime.timelineTimeScale().gaps.length, 1);
    assert.equal(runtime.timelineTimeScale(true).gaps.length, 2);
    runtime.toggleTimelineFolds();
    assert.equal(runtime.state().timelineWaitsCollapsed, false);
    assert.deepEqual(runtime.state().timelineExpandedGaps, []);
    assert.equal(runtime.displayTimelineTime(10_000), 10_000);
    assert.equal(runtime.toolbar.title, "Fold all unchanged intervals");
    assert.equal(runtime.toolbar.disabled, false);
    runtime.toggleTimelineFolds();
    assert.equal(runtime.state().timelineWaitsCollapsed, true);
    assert.equal(runtime.timelineTimeScale().gaps.length, 2);
    assert.equal(runtime.displayTimelineTime(10_000), 192);
    for (const gap of [...runtime.timelineTimeScale(true).gaps]) runtime.toggleTimelineGap(gap);
    assert.equal(runtime.timelineTimeScale().gaps.length, 0);
    assert.equal(runtime.toolbar.title, "Fold all unchanged intervals");
    assert.equal(runtime.toolbar.disabled, false);
    runtime.toggleTimelineFolds();
    assert.equal(runtime.timelineTimeScale().gaps.length, 2);
    assert.deepEqual(runtime.state().timelineExpandedGaps, []);
    assert.equal(loadQ1TimelineFoldRuntime(script, ir, { state, basis: "aligned" }).timelineTimeScale().gaps.length, 2);
    assert.equal(loadQ1TimelineFoldRuntime(script, ir, { state, full: { min: 0, max: 12_000 } }).timelineTimeScale().gaps.length, 2);
    const changed = { events: [ir.events[0], { ...ir.events[1], t0: 4000, t1: 4000 }] };
    assert.equal(loadQ1TimelineFoldRuntime(script, changed, { state }).timelineTimeScale().gaps.length, 2);
    const malformed = { timelineExpandedGaps: [null, {}, { ...state.timelineExpandedGaps[0], end: -1 }] };
    assert.equal(loadQ1TimelineFoldRuntime(script, ir, { state: malformed }).timelineTimeScale().gaps.length, 2);
  });

  it("refolds one interval after expanding all and restores the remaining controls after reload", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const ir = { events: [
      { kind: "wait", sequencer_id: "q0", confidence: "exact", t0: 0, t1: 15_000 },
      { kind: "marker_state", sequencer_id: "q0", confidence: "exact", t0: 5000, t1: 5000 },
      { kind: "marker_state", sequencer_id: "q0", confidence: "exact", t0: 10_000, t1: 10_000 },
    ] };
    const options = { current: { min: 0, max: 15_000 }, full: { min: 0, max: 15_000 } };
    const runtime = loadQ1TimelineFoldRuntime(script, ir, options);
    const preserved = JSON.parse(JSON.stringify(runtime.preserved()));
    const markers = (view: any) => view.tracks[1].children.filter((node: any) => node.classList.contains("q1timeline-wait-fold"));
    const click = (marker: any) => marker.children.find((node: any) => node.tagName === "BUTTON").listeners.click({ stopPropagation() {}, preventDefault() {} });
    runtime.toggleTimelineFolds();
    assert.equal(runtime.displayTimelineTime(15_000), 15_000);
    assert.deepEqual(markers(runtime).map((node: any) => node.classList.contains("is-expanded")), [true, true, true]);
    click(markers(runtime)[1]);
    assert.deepEqual(runtime.timelineTimeScale().gaps.map(({ start, end }: any) => ({ start, end })), [{ start: 5016, end: 9984 }]);
    assert.deepEqual(markers(runtime).map((node: any) => node.classList.contains("is-expanded")), [true, false, true]);
    assert.equal(runtime.displayTimelineTime(4984) - runtime.displayTimelineTime(16), 4968);
    assert.equal(runtime.displayTimelineTime(9984) - runtime.displayTimelineTime(5016), 64);
    assert.equal(runtime.displayTimelineTime(14_984) - runtime.displayTimelineTime(10_016), 4968);
    assert.deepEqual(runtime.preserved(), preserved);

    const reloaded = loadQ1TimelineFoldRuntime(script, ir, { ...options, state: JSON.parse(JSON.stringify(runtime.state())) });
    reloaded.renderWaitFolds();
    assert.deepEqual(reloaded.timelineTimeScale().gaps, runtime.timelineTimeScale().gaps);
    assert.deepEqual(markers(reloaded).map((node: any) => node.classList.contains("is-expanded")), [true, false, true]);
    const expandedButton = markers(reloaded)[0].children.find((node: any) => node.tagName === "BUTTON");
    assert.equal(expandedButton.getAttribute("aria-expanded"), "true");
    click(markers(reloaded)[0]);
    assert.deepEqual(reloaded.timelineTimeScale().gaps.map(({ start, end }: any) => ({ start, end })), [
      { start: 16, end: 4984 }, { start: 5016, end: 9984 },
    ]);
    assert.deepEqual(markers(reloaded).map((node: any) => node.classList.contains("is-expanded")), [false, false, true]);
    assert.equal(reloaded.displayTimelineTime(14_984) - reloaded.displayTimelineTime(10_016), 4968);
    assert.deepEqual(reloaded.preserved(), preserved);
    const refoldedReload = loadQ1TimelineFoldRuntime(script, ir, { ...options, state: JSON.parse(JSON.stringify(reloaded.state())) });
    assert.deepEqual(refoldedReload.timelineTimeScale().gaps, reloaded.timelineTimeScale().gaps);
  });

  it("preserves real-time state and per-gap choices while browsing equal-width instructions", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const state = {
      timelineWaitsCollapsed: true,
      timelineExpandedGaps: [{ start: 16, end: 4984, basis: "local", fullMin: 0, fullMax: 10_000 }],
      expandedQ1IssueSequencers: ["q0"],
    };
    const runtime = loadQ1TimelineFoldRuntime(script, { events: [
      { id: "wait", kind: "wait", sequencer_id: "q0", confidence: "exact", t0: 0, t1: 10_000 },
      { id: "marker", kind: "marker_state", sequencer_id: "q0", confidence: "exact", t0: 5000, t1: 5000 },
    ] }, { state, current: { min: 4500, max: 5500 } });
    const preserved = JSON.parse(JSON.stringify(runtime.preserved()));
    const scale = runtime.timelineTimeScale();
    runtime.setTimelineLayout("instruction");
    assert.equal(runtime.layout(), "instruction");
    assert.deepEqual(runtime.preserved(), preserved);
    assert.deepEqual(runtime.state(), { ...state, timelineLayout: "instruction" });
    assert.equal(runtime.timelineTimeScale(), scale, "Changing layout must not rebuild or alter the real-time scale");
    assert.ok(runtime.tracks.every((track: any) => track.children.length === 0), "Instruction slots must not receive real-time fold or hardware overlays");

    runtime.installTimelineMouseInteractions();
    assert.deepEqual(Object.keys(runtime.tracks[1].listeners).sort(), ["scroll", "wheel"], "Instruction zoom and native scroll must not install real-time pointer handlers");
    assert.equal(runtime.tracks[1].style.touchAction, undefined);
    runtime.zoomTimelineToHighlightedEvent({ id: "marker", t0: 5000, t1: 5000 });
    assert.deepEqual(runtime.scrolledEventIds, ["marker"]);
    assert.deepEqual(runtime.preserved(), preserved);

    runtime.setTimelineLayout("time");
    assert.deepEqual(runtime.preserved(), preserved);
    assert.deepEqual(runtime.state(), { ...state, timelineLayout: "time" });
    assert.equal(runtime.timelineTimeScale(), scale);
    assert.ok(runtime.tracks.every((track: any) => track.querySelector(".hardware-time-cursor")));
    assert.ok(runtime.tracks.every((track: any) => track.querySelector(".q1timeline-wait-fold")));
    runtime.installTimelineMouseInteractions();
    assert.deepEqual(Object.keys(runtime.tracks[1].listeners).sort(), ["pointerdown", "wheel"]);
  });

  it("highlights all source-range instruction aliases without zooming time or moving the hardware cursor", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const handlerStart = script.indexOf('\n  window.addEventListener("message", (event) => {');
    assert.ok(handlerStart >= 0);
    const handlerSource = script.slice(handlerStart, script.lastIndexOf("\n})();"));
    const scrolled: string[] = [];
    const fitted: string[][] = [];
    const blocks = [
      { eventId: "q1", eventIds: "q1" },
      { eventId: "play-path0", eventIds: "play-path0 play-path1" },
      { eventId: "unrelated", eventIds: "unrelated" },
    ].map((dataset) => ({
      dataset, selected: false, attributes: {} as Record<string, string>, classList: {} as any,
      setAttribute(name: string, value: string) { this.attributes[name] = value; },
      scrollIntoView() { scrolled.push(dataset.eventId); },
    }));
    blocks.forEach((block) => { block.classList.toggle = (_name: string, selected: boolean) => { block.selected = selected; }; });
    const summary = { hidden: true, textContent: "", setAttribute() {} };
    let handler: (event: any) => void = () => { throw new Error("Message handler was not registered"); };
    const sourceSelection = Function("window", "document", "summary", "fitInstructionSelection", `
      let timelineLayout = "instruction";
      let selectedTimelineEventId = "previous";
      let selectedSourceEventIds = new Set();
      function renderSharedEventInspector() {}
      const visiblePlaybackEvent = event => event;
      const timelineEventsById = {get: id => ({id})};
      function syncTimelineRelatedSelection() {}
      function ensureRelatedBlockSummaryNode() { return summary; }
      function zoomTimelineToEventRange() { throw new Error("Instruction highlight changed the real-time window"); }
      const hardwareView = { selectEvent() { throw new Error("Range highlight moved the hardware cursor"); } };
      ${handlerSource}
      return () => Array.from(selectedSourceEventIds);
    `)({ addEventListener: (_name: string, listener: (event: any) => void) => { handler = listener; } },
      { querySelectorAll: () => blocks }, summary, (selected: typeof blocks) => { fitted.push(selected.map((block) => block.dataset.eventId)); });
    handler({ data: { type: "highlightEventIds", zoomToRange: true, highlightEventIds: ["q1", "play-path1"] } });
    assert.deepEqual(blocks.map((block) => block.selected), [true, true, false]);
    assert.deepEqual(blocks.map((block) => block.attributes["aria-selected"]), ["true", "true", "false"]);
    assert.deepEqual(scrolled, []);
    assert.deepEqual(fitted, [["q1", "play-path0"]]);
    assert.deepEqual(sourceSelection(), ["q1", "play-path1"]);
    assert.match(summary.textContent, /2 blocks.*instruction order/);
    handler({ data: { type: "highlightEventIds", zoomToRange: true, highlightEventIds: ["missing"] } });
    assert.deepEqual(blocks.map((block) => block.selected), [false, false, false]);
    assert.deepEqual(fitted, [["q1", "play-path0"], []]);
    assert.match(summary.textContent, /no blocks/);
  });

  it("renders instruction coordinates independently while restoring the saved time viewport and source selection", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const renderSource = extractFunctionSource(script, "renderSharedTimelineIfAvailable");
    assert.ok(renderSource);
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    let rendered: any;
    const stage = { scrollLeft: 88, dataset: {}, style: { setProperty() {} }, querySelectorAll: () => [] };
    const root = { querySelector: (selector: string) => selector === ".shared-timeline-stage" ? stage : undefined };
    const noopNames = [
      "renderTimelineLayoutToggle", "renderTimelineWindowStatus", "renderTimeBasisToggle", "installSharedQ1IssueControls",
      "syncSharedSequencerFoldControls", "installTimelineControlChips", "installTimelineMouseInteractions", "applySharedSelection",
      "renderWaitFolds", "renderPlaybackFolds", "renderHardwareCursor",
      "setInstructionView",
    ];
    const runtime = Function("window", "document", `
      let timelineLayout = "time", timelineTimeBasis = "local", sharedTimelineActive = false;
      let instructionView = { cellWidth: 112, leftOrdinal: 0 };
      let selectedTimelineEventId;
      const selectedSourceEventIds = new Set(["play-path1"]);
      const expandedQ1IssueSequencers = new Set();
      const expandedPlaybackLoops = new Set();
      const timelineIr = { events: [
        { id: "q1", kind: "q1_issue", sequencer_id: "q0", t0: 8, t1: 12, source: { raw: "play 0,1,4" } },
        { id: "play", kind: "play", sequencer_id: "q0", t0: 400016, t1: 400020,
          meta: { path_event_ids: ["play-path0", "play-path1"] } },
      ] };
      function currentTimelineMode() { return "normal"; }
      function currentTimelineWindow() { return { min: 400000, max: 400060 }; }
      function sharedTimelineViewport() { return { start: 400000, end: 400060 }; }
      function sharedTimelineTicks() { return [{ leftPercent: 0, label: "400000 ns" }]; }
      function sharedTimelineSelectionRange() { return { start: 400016, end: 400020 }; }
      ${noopNames.map((name) => `function ${name}() {}`).join("\n")}
      ${renderSource}
      return {
        render(layout) { timelineLayout = layout; return renderSharedTimelineIfAvailable(); },
        q1Preference: () => Array.from(expandedQ1IssueSequencers),
      };
    `)({ q1timelineTimelineAdapter: adapter, q1lensSharedTimeline: { renderTimeline: (_root: any, model: any) => { rendered = model; } } },
      { getElementById: () => root });
    assert.equal(runtime.render("instruction"), true);
    assert.deepEqual(rendered.viewport, { start: 0, end: 1 });
    assert.equal(rendered.selectionRange, undefined);
    assert.equal(rendered.lanes.find((lane: any) => lane.kind === "q1-issue").hidden, false);
    assert.equal(rendered.lanes.flatMap((lane: any) => lane.blocks).find((block: any) => block.eventIds.includes("play-path1")).selected, true);
    assert.deepEqual(runtime.q1Preference(), []);
    assert.equal(runtime.render("time"), true);
    assert.deepEqual(rendered.viewport, { start: 400000, end: 400060 });
    assert.deepEqual(rendered.selectionRange, { start: 400016, end: 400020 });
    assert.deepEqual(rendered.ticks, [{ leftPercent: 0, label: "400000 ns" }]);
    assert.equal(rendered.lanes.find((lane: any) => lane.kind === "q1-issue").hidden, true);
    assert.equal(rendered.lanes.flatMap((lane: any) => lane.blocks).find((block: any) => block.eventIds.includes("play-path1")).selected, true);
    assert.deepEqual(runtime.q1Preference(), []);
  });

  it("fits selected Q1 and RT source-range bounds without enclosing loop or control-flow summaries", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const event = (id: string, kind: string, start: number, end: number, extra = {}) => ({
      id, kind, sequencer_id: "Control", lane: `rt.${kind}`, confidence: "exact", t0: start, t1: end, ...extra,
    });
    const runtime = loadQ1TimelineFoldRuntime(script, { events: [
      event("issue", "q1_issue", 200, 204), event("gain", "latched_state_pending", 400_016, 400_016),
      event("playI", "play", 400_016, 400_020), event("playQ", "play", 400_016, 400_020),
      event("wait", "wait", 400_020, 400_060), event("repeat", "play", 800_016, 800_020),
      event("loop", "loop_block", 12, 40_160_000), event("preview", "loop_iteration_preview", 12, 401_548),
      event("branch", "branch_region", 0, 40_160_000), event("debug", "slack", 0, 40_160_000, { lane: "debug.slack" }),
      event("controlLoop", "control_flow_loop", 0, 40_160_000), event("controlBranch", "control_flow_branch", 0, 40_160_000),
      event("removed", "play", 0, 4, { meta: { diff_status: "removed" } }),
    ] });
    assert.deepEqual(runtime.sourceRangeTimelineBounds([
      "issue", "loop", "preview", "branch", "debug", "controlLoop", "controlBranch", "gain", "playI", "playI", "playQ", "wait", "removed", "missing",
    ]), { start: 200, end: 400_060, eventIds: ["issue", "gain", "playI", "playQ", "wait"], clock: "Q1 core + RT" });
    assert.deepEqual(runtime.sourceRangeTimelineBounds(["playI", "repeat"]), {
      start: 400_016, end: 800_020, eventIds: ["playI", "repeat"], clock: "RT",
    });
    assert.deepEqual(runtime.sourceRangeTimelineBounds(["issue", "loop", "preview", "debug"]), {
      start: 200, end: 204, eventIds: ["issue"], clock: "Q1 core",
    });
    assert.equal(runtime.sourceRangeTimelineBounds(["loop", "preview", "branch", "debug", "controlLoop", "controlBranch", "removed"]), undefined);
  });

  it("includes every selected Q1 instruction and only the selected RT packet before a long wait", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const issue = (index: number) => ({ id: `q${index}`, kind: "q1_issue", sequencer_id: "disabled", lane: "debug.q1_issue",
      t0: { kind: "concrete", value: index * 4 }, t1: { kind: "concrete", value: (index + 1) * 4 } });
    const ir = { events: [
      ...Array.from({ length: 6 }, (_unused, index) => issue(index)),
      { id: "update", kind: "upd_param", sequencer_id: "disabled", lane: "rt.update", t0: 0, t1: 4 },
      { id: "wait", kind: "wait", sequencer_id: "disabled", lane: "rt.wait", t0: 4, t1: 65_539 },
      { id: "loop", kind: "loop_block", sequencer_id: "disabled", lane: "control.loop", t0: 4, t1: 196_609 },
    ] };
    const runtime = loadQ1TimelineFoldRuntime(script, ir, { full: { min: 0, max: 196_613 } });
    const ids = ["q0", "q1", "q2", "q3", "q4", "q5", "update"];
    assert.deepEqual(runtime.zoomTimelineToEventRange(ids), {
      start: 0, end: 24, eventIds: ids, clock: "Q1 core + RT",
    });
    assert.deepEqual(runtime.selectedSources(), ids);
    assert.deepEqual(runtime.sourceSelectionsAtRender, [ids]);
    assert.deepEqual(runtime.preserved().selection, { start: 0, end: 24 });
    assert.deepEqual(runtime.preserved().window, { min: 0, max: 26 });
    assert.equal(runtime.state().timelineExpandedGaps, undefined);
  });

  it("rejects unknown source-range endpoints and never mixes aligned and local clocks", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const play = { id: "play", kind: "play", sequencer_id: "q0", lane: "rt.play", confidence: "exact", t0: 400_016, t1: 400_020 };
    const issue = { ...play, id: "issue", kind: "q1_issue", t0: 200, t1: 204 };
    const valid = { ...play, meta: { aligned_t0: 401_016, aligned_t1: 401_020 } };
    const alignedIssue = { ...issue, meta: { aligned_t0: 1200, aligned_t1: 1204 } };
    const aligned = loadQ1TimelineFoldRuntime(script, { events: [valid, alignedIssue] }, { basis: "aligned" });
    assert.deepEqual(aligned.sourceRangeTimelineBounds(["issue", "play"]), {
      start: 1200, end: 401_020, eventIds: ["issue", "play"], clock: "Q1 core + RT",
    });
    const cases = [
      { basis: "local", event: { ...play, t1: { kind: "symbolic", expr: "T" } } },
      { basis: "local", event: { ...play, t0: { kind: "concrete", value: null } } },
      { basis: "local", event: { ...play, t1: undefined } },
      { basis: "local", event: { ...play, t1: 400_015 } },
      { basis: "aligned", event: play },
      { basis: "aligned", event: { ...play, meta: { aligned_t0: null, aligned_t1: 401_020 } } },
      { basis: "aligned", event: { ...play, meta: { aligned_t0: 401_016, aligned_t1: "401020" } } },
      { basis: "aligned", event: { ...play, meta: { aligned_t0: 401_016, aligned_t1: NaN } } },
    ];
    for (const { basis, event } of cases) {
      const runtime = loadQ1TimelineFoldRuntime(script, { events: [event, issue, { ...valid, id: "other" }] }, { basis });
      const before = JSON.parse(JSON.stringify(runtime.preserved()));
      assert.equal(runtime.sourceRangeTimelineBounds(["play", "other", "issue"]), undefined);
      assert.equal(runtime.zoomTimelineToEventRange(["play", "other", "issue"]), undefined);
      assert.deepEqual(runtime.preserved(), before);
      assert.equal(runtime.renders(), 0);
    }
    for (const invalidIssue of [
      { ...issue, t1: { kind: "unknown", reason: "Q1 timing unresolved" } },
      { ...issue, t1: 199 },
    ]) {
      const runtime = loadQ1TimelineFoldRuntime(script, { events: [play, invalidIssue] });
      assert.equal(runtime.sourceRangeTimelineBounds(["play", "issue"]), undefined);
      assert.equal(runtime.zoomTimelineToEventRange(["play", "issue"]), undefined);
      assert.deepEqual(runtime.selectedSources(), []);
      assert.equal(runtime.renders(), 0);
    }
    const missingAlignedIssue = loadQ1TimelineFoldRuntime(script, { events: [valid, issue] }, { basis: "aligned" });
    assert.equal(missingAlignedIssue.sourceRangeTimelineBounds(["issue", "play"]), undefined);
  });

  it("zooms a source range in displayed coordinates without expanding any folds", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const event = (id: string, kind: string, start: number, end: number, sequencer_id = "q0") => ({
      id, kind, sequencer_id, lane: `rt.${kind}`, confidence: "exact", t0: start, t1: end,
      meta: { aligned_t0: start + 1000, aligned_t1: end + 1000 },
    });
    const ir = {
      alignment: Object.fromEntries(["q0", "q1"].map(id => [id, { offset: 1000, mode: "manual:1000", confidence: "exact" }])),
      events: [event("wait0", "wait", 0, 20_000), event("wait1", "wait", 0, 20_000, "q1"),
        event("selected", "upd_param", 5000, 5400), event("change1", "marker_state", 10_000, 10_000),
        event("change2", "marker_state", 15_000, 15_000)],
    };
    for (const basis of ["local", "aligned"]) {
      const offset = basis === "aligned" ? 1000 : 0;
      const full = { min: offset, max: 20_000 + offset };
      const runtime = loadQ1TimelineFoldRuntime(script, ir, { basis, full });
      assert.equal(runtime.timelineTimeScale().gaps.length, 4);
      assert.deepEqual(runtime.zoomTimelineToEventRange(["selected"]), {
        start: 5000 + offset, end: 5400 + offset, eventIds: ["selected"], clock: "RT",
      });
      const view = runtime.preserved();
      assert.deepEqual(runtime.sourceSelectionsAtRender, [["selected"]]);
      assert.deepEqual(view.selection, {start: 5000 + offset, end: 5400 + offset});
      assert.equal(view.selected, undefined);
      assert.equal(view.time, 5050);
      assert.equal(runtime.timelineTimeScale().gaps.length, 4);
      assert.ok(Math.abs(runtime.displayTimelineTime(view.window.max) - runtime.displayTimelineTime(view.window.min) - 440) < 0.05);
      assert.ok(Math.abs(runtime.timelineTimeFromPointer({ clientX: 600 }) - (5200 + offset)) < 0.05);
      assert.deepEqual(runtime.state().timelineWindow, {
        min: view.window.min, max: view.window.max, fullMin: full.min, fullMax: full.max, basis,
      });
      assert.equal(runtime.state().timelineExpandedGaps, undefined);
    }
    const linear = loadQ1TimelineFoldRuntime(script, ir, { full: { min: 0, max: 20_000 }, state: { timelineWaitsCollapsed: false } });
    linear.zoomTimelineToEventRange(["selected"]);
    assert.equal(linear.state().timelineWaitsCollapsed, false);
    assert.equal(linear.timelineTimeScale(), undefined);
    assert.equal(linear.displayTimelineTime(20_000), 20_000);
    const point = loadQ1TimelineFoldRuntime(script, ir, { full: { min: 0, max: 20_000 } });
    point.zoomTimelineToEventRange(["change1"]);
    assert.deepEqual(point.preserved().window, { min: 9998, max: 10_002 });
    assert.deepEqual(point.preserved().selection, { start: 10_000, end: 10_000 });
  });

  it("preserves another sequencer's pulse width when shared blocks use the wait-fold scale", () => {
    const adapter = require(join(__dirname, "..", "src", "q1timeline", "media", "timelineAdapter.js"));
    const renderer = require(join(__dirname, "..", "src", "shared", "timeline", "renderer.js"));
    const event = (kind: string, start: number, end: number, sequencer_id: string, meta = {}) => ({
      kind, sequencer_id, lane: `rt.${kind}`, confidence: "exact", meta,
      t0: { kind: "concrete", value: start }, t1: { kind: "concrete", value: end },
    });
    const timeScale = adapter.buildWaitTimeScale({
      alignment: {
        q0: { offset: 0, mode: "manual:0", confidence: "exact" },
        q1: { offset: 0, mode: "manual:0", confidence: "exact" },
      },
      events: [
        event("upd_param", 0, 4, "q0", { applied_state: { awg_offset: [0, 0], marker: 0 } }),
        event("upd_param", 0, 4, "q1", { applied_state: { awg_offset: [0, 0], marker: 0 } }),
        event("wait", 4, 20_000, "q0"), event("wait", 4, 4000, "q1"),
        event("play", 20_000, 20_100, "q0"), event("play", 4000, 7000, "q1"),
        event("wait", 7000, 20_100, "q1"),
      ],
    });
    const viewport = { start: 0, end: 20_100, timeScale };
    const otherPulse = renderer.blockStyle({ start: 4000, duration: 3000 }, viewport);
    const lastPulse = renderer.blockStyle({ start: 20_000, duration: 100 }, viewport);
    assert.equal(timeScale.gaps.length, 1);
    assert.ok(timeScale.gaps[0].end < 4000);
    assert.equal(timeScale.toDisplay(20_100) - timeScale.toDisplay(4000), 16_100);
    const displaySpan = timeScale.toDisplay(viewport.end) - timeScale.toDisplay(viewport.start);
    assert.equal(timeScale.toDisplay(7000) - timeScale.toDisplay(4000), 3000);
    assert.equal(timeScale.toDisplay(20_100) - timeScale.toDisplay(20_000), 100);
    assert.ok(Math.abs(otherPulse.widthPercent - 3000 / displaySpan * 100) < 0.001);
    assert.ok(Math.abs(lastPulse.widthPercent - 100 / displaySpan * 100) < 0.001);
    assert.ok(otherPulse.leftPercent + otherPulse.widthPercent < lastPulse.leftPercent);
    assert.ok(Math.abs(lastPulse.leftPercent + lastPulse.widthPercent - 100) < 0.002);
  });
});
