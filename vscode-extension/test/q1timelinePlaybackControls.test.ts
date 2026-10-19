import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const { buildQ1TimelineSharedModel: buildModel, buildTimelineTimeScale, buildWaitTimeScale } = require("../src/q1timeline/media/timelineAdapter.js");
const sharedAlignment = {seq: {mode: "manual:0", confidence: "exact", offset: 0}, other: {mode: "manual:0", confidence: "exact", offset: 0}};

function sourceFunction(script: string, name: string) {
  const start = script.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `Missing ${name}`);
  let depth = 0;
  for (let index = script.indexOf(") {", start) + 2; index < script.length; index++) {
    if (script[index] === "{") depth++;
    if (script[index] === "}" && --depth === 0) return script.slice(start, index + 1);
  }
  throw new Error(`Unclosed ${name}`);
}

function controls(initial: any) {
  const script = readFileSync(join(__dirname, "..", "src/q1timeline/media/timeline.js"), "utf8");
  const node = () => ({dataset: {}, attributes: {} as Record<string, string>, title: "", innerHTML: "",
    classList: {add() {}}, setAttribute(name: string, value: string) {this.attributes[name] = value;} });
  const button = node(), status = node(), messages: any[] = [];
  const runtime = Function("document", "vscode", "timelineExecutionState", `
    ${["toolbarSvgIcon", "setIconOnlyToolbarButton", "renderExecutionControls", "requestExecutionControl"].map(name => sourceFunction(script, name)).join("\n")}
    return {render: renderExecutionControls, click: requestExecutionControl,
      update(state) {timelineExecutionState = state; renderExecutionControls();}};
  `)({getElementById: (id: string) => id.endsWith("button") ? button : status}, {postMessage: (message: any) => messages.push(message)}, initial);
  runtime.render();
  return {runtime, button, status, messages};
}

describe("timeline bounded execution controls", () => {
  it("uses play and pause icons with the host's execution state", () => {
    const view = controls({status: "idle"});
    assert.equal(view.button.attributes["aria-label"], "Run execution");
    view.runtime.click();
    view.runtime.update({status: "running", steps: 25});
    assert.equal(view.button.attributes["aria-label"], "Pause execution");
    assert.equal(view.button.attributes["aria-pressed"], "true");
    assert.match(view.status.title, /Running.*25 instructions/);
    view.runtime.click();
    assert.deepEqual(view.messages, [{type: "executionControl", action: "play"}, {type: "executionControl", action: "pause"}]);
  });

  it("keeps paused, blocked and limited execution resumable with the reason in a tooltip", () => {
    for (const status of ["paused", "blocked", "limit", "error"]) {
      const view = controls({status, steps: 100, reason: "Execution detail"});
      assert.equal(view.button.attributes["aria-label"], "Run execution");
      assert.equal(view.button.attributes["aria-pressed"], "false");
      assert.match(view.status.title, /100 instructions.*Execution detail/);
      view.runtime.click();
      assert.deepEqual(view.messages, [{type: "executionControl", action: "play"}]);
    }
    const completed = controls({status: "completed", steps: 7});
    assert.equal(completed.button.attributes["aria-label"], "Execution complete");
    assert.equal((completed.button as any).disabled, true);
    assert.match(completed.button.title, /Change a branch/);
    completed.runtime.click();
    assert.deepEqual(completed.messages, []);
  });

  it("disables a blocked or exhausted trace that the host cannot resume", () => {
    for (const status of ["blocked", "limit"]) {
      const view = controls({status, can_resume: false, reason: "No further modeled steps."});
      assert.equal((view.button as any).disabled, true);
      assert.match(view.button.title, /No further modeled steps.*Change a branch/);
      view.runtime.click();
      assert.deepEqual(view.messages, []);
    }
    const retry = controls({status: "error", can_resume: true});
    assert.equal((retry.button as any).disabled, false);
    retry.runtime.click();
    assert.deepEqual(retry.messages, [{type: "executionControl", action: "play"}]);
  });
});

function playbackEvent(id: string, kind: string, start: number, end: number, meta: any = {}) {
  return {id, kind, label: kind, sequencer_id: "seq", lane: kind === "q1_issue" ? "q1.issue" : "rt.control",
    t0: start, t1: end, confidence: "exact", source: {file: "loop.q1asm", line: 2}, meta};
}

function loopTrace() {
  return {playback: {status: "paused"}, events: [
    playbackEvent("q0", "q1_issue", 0, 4, {op: "wait"}), playbackEvent("w0", "wait", 0, 100),
    playbackEvent("q1", "q1_issue", 4, 8, {op: "wait"}), playbackEvent("w1", "wait", 100, 200),
    playbackEvent("q2", "q1_issue", 8, 12, {op: "wait"}), playbackEvent("w2", "wait", 200, 300),
    playbackEvent("live", "q1_issue", 12, 16, {op: "jlt", playback_branch_id: "branch:3"}),
    playbackEvent("loop", "loop_block", 0, 300, {playback: true, loop_id: "loop", loop_preview_key: "loop:preview",
      count: ">=3", completed_iterations: 3, complete: false, first_iteration_end: 100, first_iteration_event_ids: ["q0", "w0"],
      playback_event_ids: ["q0", "w0", "q1", "w1", "q2", "w2"], hidden_event_ids: ["q1", "w1", "q2", "w2"]}),
  ]};
}

describe("executed loop display", () => {
  const ids = (model: any) => model.lanes.flatMap((lane: any) => lane.blocks.map((block: any) => block.id)).sort();

  it("compacts only completed iterations and keeps the live occurrence selectable in both layouts", () => {
    const ir = loopTrace(), before = JSON.stringify(ir);
    for (const layout of ["time", "instruction"]) {
      const model = buildModel(ir, {mode: "debug", layout, expandedQ1IssueSequencers: ["seq"]});
      assert.deepEqual(ids(model), ["live", "q0", "w0"]);
      const loop = model.annotations.find((annotation: any) => annotation.eventId === "loop");
      assert.ok(loop);
      assert.equal(loop.label, "×>=3");
      assert.ok(model.lanes.some((lane: any) => lane.id === loop.laneId));
      assert.deepEqual(loop.eventIds, ["loop", "q0", "w0"]);
    }
    assert.equal(JSON.stringify(ir), before, "Hardware retains every executed event and its original metadata");
  });

  it("expands all observed iterations without creating preview events or changing the trace", () => {
    const ir = loopTrace();
    const model = buildModel(ir, {mode: "debug", expandedQ1IssueSequencers: ["seq"], expandedPlaybackLoops: ["loop:preview"]});
    assert.deepEqual(ids(model), ["live", "q0", "q1", "q2", "w0", "w1", "w2"]);
    assert.equal(model.annotations.filter((annotation: any) => annotation.type === "loop-range").length, 1);
    assert.equal(ir.events.length, 8);
  });

  it("does not apply execution compaction to legacy analysis", () => {
    const ir: any = loopTrace();
    delete ir.playback;
    assert.deepEqual(ids(buildModel(ir, {mode: "debug"})), ["live", "q0", "q1", "q2", "w0", "w1", "w2"]);
  });

  it("retains a lane for a loop containing only Q1 instructions", () => {
    const ir = loopTrace();
    ir.events = ir.events.filter((event) => !event.id.startsWith("w"));
    const model = buildModel(ir, {mode: "normal", expandedQ1IssueSequencers: ["seq"]});
    const loop = model.annotations.find((annotation: any) => annotation.eventId === "loop");
    assert.ok(loop && model.lanes.some((lane: any) => lane.id === loop.laneId));
  });

  it("resolves hidden nested occurrences to their visible container without changing expansion state", () => {
    const script = readFileSync(join(__dirname, "..", "src/q1timeline/media/timeline.js"), "utf8");
    const first = playbackEvent("first", "q1_issue", 0, 4);
    const hidden = playbackEvent("hidden", "q1_issue", 100, 104);
    const child = playbackEvent("child", "loop_block", 100, 300, {playback: true, loop_preview_key: "child",
      first_iteration_event_ids: ["first"], hidden_event_ids: ["hidden"]});
    const parent = playbackEvent("parent", "loop_block", 0, 500, {playback: true, loop_preview_key: "parent",
      first_iteration_event_ids: ["first"], hidden_event_ids: ["child", "hidden"]});
    const events = [first, hidden, child, parent];
    const expanded = new Set();
    const resolve = Function("timelineIr", "expandedPlaybackLoops", "timelineLayout", "timelineEventsById", "sameSourceLine", `
      ${sourceFunction(script, "visiblePlaybackEvent")}; return visiblePlaybackEvent;
    `)({playback: {}, events}, expanded, "time", new Map(events.map((event) => [event.id, event])), () => true);
    assert.equal(resolve(hidden), parent);
    assert.equal(expanded.size, 0);
    expanded.add("parent");
    assert.equal(resolve(hidden), child);
    assert.deepEqual([...expanded], ["parent"]);
    expanded.add("child");
    assert.equal(resolve(hidden), hidden);
  });
});

describe("executed loop time scale", () => {
  const close = (actual: number, expected: number) => assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} != ${expected}`);
  const loop = (id: string, start: number, firstEnd: number, end: number) => playbackEvent(id, "loop_block", start, end,
    {playback: true, loop_preview_key: id, first_iteration_end: firstEnd, hidden_event_ids: [`${id}:hidden`]});

  it("folds the omitted iterations in a continuous reversible scale without changing real times", () => {
    const ir = loopTrace(), before = JSON.stringify(ir);
    const scale = buildTimelineTimeScale(ir);
    assert.equal(scale.gaps.length, 1);
    assert.deepEqual([scale.gaps[0].start, scale.gaps[0].end], [100, 300]);
    close(scale.toDisplay(100), 100);
    close(scale.toDisplay(300), 120);
    close(scale.toDisplay(400), 220);
    for (const time of [0, 99.5, 100, 101, 200, 299.5, 300, 400]) close(scale.fromDisplay(scale.toDisplay(time)), time);
    const expanded = buildTimelineTimeScale(ir, {expandedPlaybackLoops: ["loop:preview"]});
    assert.equal(expanded.gaps.length, 0);
    close(expanded.toDisplay(400), 400);
    assert.equal(JSON.stringify(ir), before, "Time folding only changes display coordinates");
  });

  it("moves the first-iteration boundary with the aligned loop offset", () => {
    const ir = loopTrace();
    const event = ir.events.find((item) => item.id === "loop")!;
    event.meta.aligned_t0 = {kind: "concrete", value: 1000};
    event.meta.aligned_t1 = {kind: "concrete", value: 1300};
    const scale = buildTimelineTimeScale(ir, {timeBasis: "aligned"});
    assert.deepEqual([scale.gaps[0].start, scale.gaps[0].end], [1100, 1300]);
    close(scale.toDisplay(1400), 1220);
    close(scale.fromDisplay(1220), 1400);
    assert.deepEqual(buildTimelineTimeScale(ir, {timeBasis: "local"}).gaps.map((gap: any) => [gap.start, gap.end]), [[100, 300]]);
  });

  it("merges nested and overlapping omissions once across sequencers", () => {
    const events = [loop("outer", 0, 100, 300), loop("inner", 100, 150, 250),
      {...loop("parallel", 50, 200, 400), sequencer_id: "other"}];
    const scale = buildTimelineTimeScale({playback: {}, alignment: sharedAlignment, events});
    assert.equal(scale.gaps.length, 1);
    assert.deepEqual(scale.gaps[0].loopKeys.sort(), ["inner", "outer", "parallel"]);
    assert.deepEqual([scale.gaps[0].start, scale.gaps[0].end], [100, 400]);
    close(scale.toDisplay(500), 230);
    close(scale.fromDisplay(230), 500);
  });

  it("keeps a collapsed child compressed when only its parent expands", () => {
    const ir = {playback: {}, events: [loop("parent", 0, 100, 300), loop("child", 0, 20, 80)]};
    const scale = buildTimelineTimeScale(ir, {expandedPlaybackLoops: ["parent"]});
    assert.deepEqual(scale.gaps.map((gap: any) => gap.loopKeys), [["child"]]);
    close(scale.toDisplay(300), 246);
    assert.equal(buildTimelineTimeScale(ir, {expandedPlaybackLoops: ["parent", "child"]}).gaps.length, 0);
  });

  it("combines wait folds with loop folds without making the wait toggle expand loops", () => {
    const ir = loopTrace();
    const both = buildTimelineTimeScale(ir, {waitGaps: [{start: 150, end: 500}]});
    assert.equal(both.gaps.length, 1);
    assert.deepEqual([both.gaps[0].start, both.gaps[0].end], [100, 500]);
    close(both.toDisplay(600), 240);
    const waitsExpanded = buildTimelineTimeScale(ir, {waitGaps: []});
    close(waitsExpanded.toDisplay(600), 420);
    assert.deepEqual(waitsExpanded.gaps[0].loopKeys, ["loop:preview"]);
    const loopExpanded = buildTimelineTimeScale(ir, {waitGaps: [{start: 400, end: 1000}], expandedPlaybackLoops: ["loop:preview"]});
    assert.deepEqual(loopExpanded.gaps.map((gap: any) => [gap.start, gap.end, gap.loopKeys]), [[400, 1000, []]]);
  });

  it("uses the same transform for simultaneous events on every lane", () => {
    const ir = loopTrace();
    ir.events.push(playbackEvent("after", "wait", 350, 400),
      {...playbackEvent("other", "wait", 350, 400), sequencer_id: "other"});
    const model = buildModel(ir, {mode: "debug"});
    const blocks = model.lanes.flatMap((lane: any) => lane.blocks).filter((block: any) => ["after", "other"].includes(block.id));
    assert.equal(blocks.length, 2);
    const scale = buildTimelineTimeScale({...ir, alignment: sharedAlignment});
    for (const block of blocks) {
      assert.equal(block.start, 350);
      close(scale.toDisplay(block.start), 170);
    }
  });

  it("does not invent an RT time gap for Q1-only or unmeasured iterations", () => {
    const events = [loop("zero", 100, 100, 100), loop("unknown", 0, 100, 300)];
    delete events[1].meta.first_iteration_end;
    assert.equal(buildTimelineTimeScale({playback: {}, events}).gaps.length, 0);
    assert.equal(buildTimelineTimeScale({events: [loop("static", 0, 100, 300)]}).gaps.length, 0);
  });

  it("preserves visible peer activity, markers and uncertain tails inside a loop omission", () => {
    const events: any[] = [loop("loop", 0, 100, 1000),
      {...playbackEvent("pulse", "play", 400, 500), sequencer_id: "other"},
      {...playbackEvent("mark", "marker_state", 700, 700), sequencer_id: "other"}];
    const ir = {playback: {}, alignment: sharedAlignment, events};
    const scale = buildTimelineTimeScale(ir);
    assert.deepEqual(scale.gaps.map((gap: any) => [gap.start, gap.end]), [[100, 384], [516, 684], [716, 1000]]);
    close(scale.toDisplay(500) - scale.toDisplay(400), 100);
    close(scale.fromDisplay(scale.toDisplay(700)), 700);
    events[1].t1 = {kind: "symbolic", expr: "500 + duration"};
    assert.deepEqual(buildTimelineTimeScale(ir).gaps.map((gap: any) => [gap.start, gap.end]), [[100, 384]]);
    events[1].t0 = {kind: "unknown", display: "400 + unknown"};
    assert.equal(buildTimelineTimeScale(ir).gaps.length, 0);
  });

  it("does not fold independent or missing peer clocks", () => {
    const events = [loop("loop", 0, 100, 1000)];
    assert.equal(buildTimelineTimeScale({playback: {}, sequencers: [{id: "seq"}, {id: "other"}], events}).gaps.length, 0);
    assert.equal(buildTimelineTimeScale({playback: {}, sequencers: [{id: "seq"}, {id: "other"}], alignment: sharedAlignment, events}).gaps.length, 0);
    const peer = {...playbackEvent("other", "wait", 0, 1000), sequencer_id: "other"};
    assert.equal(buildTimelineTimeScale({playback: {}, events: [...events, peer]}).gaps.length, 0);
  });
});

describe("playback explicit wait compression", () => {
  it("folds long modeled waits despite unknown acquisition completion and assumed data", () => {
    const ir: any = {playback: {}, events: [playbackEvent("acquire", "acquire", 0, 4),
      {...playbackEvent("wait", "wait", 4, 65539), confidence: "assumed", meta: {feedback_assumptions: ["feedback:1"]}},
      playbackEvent("next", "upd_param", 65539, 65543)]};
    const before = JSON.stringify(ir);
    const scale = buildWaitTimeScale(ir);
    assert.deepEqual(scale.gaps.map((gap: any) => [gap.start, gap.end]), [[20, 65523]]);
    assert.equal(scale.toDisplay(65539) - scale.toDisplay(4), 96);
    assert.equal(JSON.stringify(ir), before);
    delete ir.playback;
    assert.equal(buildWaitTimeScale(ir).gaps.length, 0, "Static unchanged-state analysis remains conservative");
  });

  it("keeps feedback and branch controls visible without reserving every Q1 arithmetic instruction", () => {
    const ir = {playback: {}, events: [playbackEvent("wait", "wait", 0, 10000),
      playbackEvent("math", "q1_issue", 4, 8000, {op: "add"}),
      playbackEvent("feedback", "q1_issue", 5000, 5004, {playback_feedback_id: "feedback:1"}),
      playbackEvent("branch", "q1_issue", 7000, 7004, {playback_branch_id: "branch:1"})]};
    const scale = buildWaitTimeScale(ir);
    assert.deepEqual(scale.gaps.map((gap: any) => [gap.start, gap.end]), [[16, 4984], [5020, 6984], [7020, 9984]]);
    assert.equal(scale.toDisplay(5004) - scale.toDisplay(5000), 4);
  });

  it("intersects known-clock waits and preserves another sequencer's active pulse interval", () => {
    const other = (id: string, kind: string, start: number, end: number) => ({...playbackEvent(id, kind, start, end), sequencer_id: "other"});
    const ir = {playback: {}, alignment: sharedAlignment, events: [playbackEvent("wait", "wait", 0, 10000),
      other("before", "wait", 0, 4000), other("pulse", "play", 4000, 7000), other("after", "wait", 7000, 10000)]};
    const scale = buildWaitTimeScale(ir);
    assert.deepEqual(scale.gaps.map((gap: any) => [gap.start, gap.end]), [[16, 3984], [7016, 9984]]);
    assert.equal(scale.toDisplay(7000) - scale.toDisplay(4000), 3000);
    assert.equal(scale.fromDisplay(scale.toDisplay(5050)), 5050);
    assert.equal(buildWaitTimeScale({...ir, alignment: undefined}).gaps.length, 0);
  });
});
