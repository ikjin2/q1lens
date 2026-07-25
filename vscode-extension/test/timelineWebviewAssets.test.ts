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
    minSpanRatio?: number,
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
  const bodyStart = script.indexOf("{", start);
  if (bodyStart < 0) {
    throw new Error(`Function ${name} has no body`);
  }
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

describe("timeline webview assets", () => {
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
      "#8bcf9a",
    );
    assert.equal(
      model.lanes.flatMap((lane: any) => lane.blocks).find((block: any) => block.id === "seq0:q1").accentColor,
      "#b9a7dc",
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
    }, { mode: "normal" });

    const blocks = model.lanes.flatMap((lane: any) => lane.blocks);

    assert.equal(model.annotations.filter((annotation: any) => annotation.type === "loop-range").length, 1);
    assert.equal(blocks.some((block: any) => block.kind === "loop_block"), false);
    assert.equal(blocks.some((block: any) => block.kind === "loop_iteration_preview"), false);
    assert.equal(blocks.some((block: any) => block.kind === "play"), true);
    assert.match(sharedCss, /\.loop-range-label\s*{[^}]*opacity:\s*0/s);
    assert.match(sharedCss, /\.timeline-loop-range:hover \.loop-range-label,[\s\S]*\.timeline-loop-range\.is-selected \.loop-range-label\s*{[^}]*opacity:\s*1/s);
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
    assert.match(sharedRenderer, /const chipCenterY = feedbackEndpointAnchorY\(rowHeight\)/);
    assert.match(sharedRenderer, /const visibleLanes = visibleTimelineLanes\(model\)/);
    assert.match(sharedRenderer, /new Map\(visibleLanes\.map/);
    assert.match(sharedRenderer, /visibleLanes\.length \* rowHeight/);
    assert.match(sharedRenderer, /const y1 = fromLaneIndex \* rowHeight \+ chipCenterY/);
    assert.match(sharedRenderer, /const y2 = toLaneIndex \* rowHeight \+ chipCenterY/);
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
    assert.match(q1TimelineCss, /body\.vscode-dark \.shared-timeline-stage \.timeline-block\.kind-q1_issue\s*{[^}]*background:\s*color-mix\(in srgb, #8bcf9a 16%, transparent\);/s);
    assert.match(q1TimelineCss, /--q1timeline-feedback-accent:\s*#ff6fb3;/);
    assert.match(q1TimelineCss, /body\.vscode-dark \.shared-timeline-stage \.timeline-block\.kind-feedback_pop,[\s\S]*body\.vscode-dark \.shared-timeline-stage \.timeline-block\.kind-fb_com_extra\s*{[^}]*background:\s*color-mix\(in srgb, var\(--q1timeline-feedback-accent\) 18%, transparent\);/s);
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
    assert.deepEqual(timelineWindowForRange(500, 500, { min: 0, max: 1000 }), { min: 490, max: 510 });
    assert.deepEqual(timelineWindowForRange(5, 5, { min: 0, max: 1000 }, 0.1), { min: 0, max: 100 });
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
    assert.match(script, /\["zoom", "zoom", "Zoom around branch"\]/);
    assert.match(script, /zoomTimelineToEvent\(event\);\s*closeTimelineControlPopover\(\);/);
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

  it("keeps zoomed-out shared micro blocks from looking overlapped", () => {
    const sharedRenderer = readFileSync(join(__dirname, "..", "src", "shared", "timeline", "renderer.js"), "utf-8");
    const sharedCss = readFileSync(join(__dirname, "..", "src", "shared", "timeline", "renderer.css"), "utf-8");

    assert.match(sharedRenderer, /const visibleWidth = Math\.max\(visibleEnd - visibleStart, 0\)/);
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
    assert.match(script, /selectEventNode\(target, \{ notify: false \}\)/);
  });

  it("chooses a visible q1timeline event for programmatic highlights", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");

    assert.match(script, /function findVisibleEventNodeByIds\(eventIds\)/);
    assert.match(script, /candidateNodes\.find\(isTimelineEventVisible\)/);
    assert.match(script, /const target = findVisibleEventNodeByIds\(highlightEventIds\)/);
  });

  it("expands q1 issue lanes and zooms tiny source highlights", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const { highlightedSpanNeedsZoom } = loadQ1TimelineHighlightFunctions(script);

    assert.equal(highlightedSpanNeedsZoom(10, 11, { min: 0, max: 1000 }, 500), true);
    assert.equal(highlightedSpanNeedsZoom(10, 100, { min: 0, max: 1000 }, 500), false);
    assert.equal(highlightedSpanNeedsZoom(Number.NaN, 100, { min: 0, max: 1000 }, 500), false);

    assert.match(script, /function eventIsQ1Issue\(event\)/);
    assert.match(script, /function prioritizedHighlightEventIds\(eventIds\)/);
    assert.match(script, /function expandQ1IssueLanesForHighlight\(eventIds\)/);
    assert.match(script, /expandedQ1IssueSequencers\.add\(sequencer\)/);
    assert.match(script, /function zoomTimelineToHighlightedEvent\(event\)/);
    assert.match(script, /const highlightEventIds = prioritizedHighlightEventIds\(event\.data\.highlightEventIds \|\| \[\]\)/);
    assert.match(script, /expandQ1IssueLanesForHighlight\(highlightEventIds\)/);
    assert.match(script, /const highlightedEvent = highlightedEventForIds\(highlightEventIds\)/);
    assert.match(script, /zoomTimelineToHighlightedEvent\(highlightedEvent\)/);
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
    assert.match(script, /button\.setAttribute\("aria-label", `\$\{expanded \? "Hide" : "Show"\} Q1 issue/);
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
    assert.match(q1TimelineCss, /\.shared-timeline-stage \.timeline-block\.kind-q1_issue\s*{/);
    assert.match(q1TimelineCss, /\.shared-timeline-stage \.timeline-block\.kind-upd_param\s*,/);
    assert.match(q1TimelineCss, /\.shared-timeline-stage \.timeline-block\.kind-upd_thres\s*{/);
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

  it("opens branch action popovers with source-line labels and loop action popovers from lanes", () => {
    const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf-8");
    const css = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.css"), "utf-8");

    assert.match(script, /function installTimelineControlChips\(\)/);
    assert.match(script, /function ensureTimelineControlOverlay\(\)/);
    assert.match(script, /function openTimelineControlPopover\(chip, event, actionKind\)/);
    assert.match(script, /function positionTimelineControlPopover\(popover, chip\)/);
    assert.match(script, /className = "q1timeline-control-chip q1timeline-control-chip-branch"/);
    assert.match(script, /function branchAssumptionIconName\(meta\)/);
    assert.match(script, /function branchSourceJumpLabel\(event\)/);
    assert.match(script, /Line \$\{line\}: \$\{raw\}/);
    assert.match(script, /function branchPathSource\(event, path\)/);
    assert.match(script, /function postSourceJump\(source\)/);
    assert.match(script, /chip\.setAttribute\("aria-label", branchSourceJumpLabel\(event\)\)/);
    assert.match(script, /chip\.innerHTML = timelineControlSvgIcon\(branchAssumptionIconName\(meta\)\)/);
    assert.doesNotMatch(script, /chip\.innerHTML = timelineControlSvgIcon\("branch"\)/);
    assert.doesNotMatch(script, /Branch path: condition/);
    assert.match(script, /selectEventNode\(sourceNode\)/);
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
    assert.match(script, /\["taken", "taken", "Condition true: jump target"\]/);
    assert.match(script, /\["fallthrough", "fallthrough", "Condition false: continue"\]/);
    assert.doesNotMatch(script, /Compare true and false/);
    assert.doesNotMatch(script, /\["both", "both"/);
    assert.doesNotMatch(script, /Show taken path/);
    assert.doesNotMatch(script, /Show fallthrough path/);
    assert.match(script, /vscode\.postMessage\(\{ type: "setBranchAssumption", branchId, path \}\)/);
    assert.match(script, /postSourceJump\(branchPathSource\(event, path\)\)/);
    assert.match(script, /vscode\.postMessage\(\{ type: "sourceClick", file: source\.file, line: source\.line, column: source\.column \}\)/);
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
});
