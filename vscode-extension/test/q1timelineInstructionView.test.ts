import assert from "node:assert/strict";

const { buildQ1TimelineSharedModel: buildModel } = require("../src/q1timeline/media/timelineAdapter.js");

function event(id: string, kind: string, start: number, end = start, extra: Record<string, unknown> = {}) {
  return {
    id, kind, sequencer_id: "Control", lane: kind === "q1_issue" ? "q1.issue" : `rt.${kind}`,
    t0: { value: start }, t1: { value: end }, duration: { value: end - start },
    label: id, confidence: "exact", source: { file: "Control.q1asm", line: 1, raw: `${kind} 4` }, ...extra,
  };
}
function lane(model: any, id = "sequencer:Control") {
  return model.lanes.find((candidate: any) => candidate.id === id);
}
function allBlocks(model: any): any[] { return model.lanes.flatMap((item: any) => item.blocks); }

describe("Q1 timeline instruction layout", () => {
  it("gives each lane consecutive equal blocks while keeping Q1 and RT separate", () => {
    const ir = { events: [
      event("stop", "stop", 400_004), event("q1-last", "q1_issue", 12, 16),
      event("long-wait", "wait", 0, 400_000), event("q1-first", "q1_issue", 0, 4),
      event("pulse", "play", 400_000, 400_004),
      event("other-wait", "wait", 50_000, 60_000, { sequencer_id: "Readout" }),
    ] };
    const options = { layout: "instruction", expandedQ1IssueSequencers: [] };
    const before = JSON.stringify({ ir, options });
    const model = buildModel(ir, options);

    assert.deepEqual(lane(model).blocks.map((block: any) => [block.id, block.start, block.duration]), [
      ["long-wait", 0, 1], ["pulse", 1, 1], ["stop", 2, 1],
    ]);
    const q1 = lane(model, "sequencer:Control:q1_issue");
    assert.equal(q1.hidden, true);
    assert.equal(q1.parentGroupId, "sequencer:Control");
    assert.deepEqual(q1.blocks.map((block: any) => [block.id, block.start, block.duration]), [
      ["q1-first", 0, 1], ["q1-last", 1, 1],
    ]);
    assert.equal(lane(model, "sequencer:Readout").blocks[0].start, 0);
    assert.equal(lane(model).label, "Control · RT");
    assert.deepEqual(model.viewport, { start: 0, end: 3 });
    assert.equal(model.totalTime, 3);
    assert.match(model.subtitle, /Independent instruction order per lane/);
    assert.match(model.subtitle, /do not show simultaneous time/);
    assert.deepEqual(model.ticks.map((tick: any) => tick.label), ["#0", "#1", "#2", "#3"]);
    model.ticks.forEach((tick: any, index: number) => assert.ok(Math.abs(tick.leftPercent - index * 100 / 3) < 0.000001));
    assert.equal(JSON.stringify({ ir, options }), before);
  });

  it("preserves event identities, inspection metadata and time layout after switching back", () => {
    const ir = { events: [
      event("issue", "q1_issue", 12, 16, { meta: { op: "play" } }),
      event("wait", "wait", 0, 1000),
      event("pulse", "play", 1000, 1004, { title: "Original pulse title", meta: { waveform_index: 7 } }),
    ] };
    const normal = buildModel(ir);
    const instruction = buildModel(ir, { layout: "instruction" });
    for (const block of allBlocks(instruction)) {
      const { start: _start, duration: _duration, ...properties } = block;
      const original = allBlocks(normal).find((candidate) => candidate.id === block.id);
      const { start: _originalStart, duration: _originalDuration, ...originalProperties } = original;
      assert.deepEqual(properties, originalProperties);
    }
    assert.equal(lane(normal, "sequencer:Control:q1_issue").hidden, true);
    assert.equal(lane(normal).label, "Control");
    assert.deepEqual(normal.viewport, { start: 0, end: 1004 });
    assert.ok(normal.ticks.every((tick: any) => tick.label.endsWith(" ns")));
    assert.deepEqual(buildModel(ir), normal);
    assert.deepEqual(buildModel(ir, { layout: "time" }), normal);
  });

  it("uses IR order for ties, including merged play blocks appended by the adapter", () => {
    const ir = { events: [
      event("path1", "play", 100, 104, { lane: "rt.path1", meta: { rt_packet_id: "packet" } }),
      event("marker", "marker_state", 100),
      event("path0", "play", 100, 104, { lane: "rt.path0", meta: { rt_packet_id: "packet" } }),
      event("earlier", "wait", 0, 100),
      event("later", "upd_param", 104, 108),
    ] };
    const model = buildModel(ir, { layout: "instruction" });
    assert.deepEqual(lane(model).blocks.map((block: any) => block.id), [
      "earlier", "merged-play:path0+path1", "marker", "later",
    ]);
    assert.deepEqual(lane(model).blocks.map((block: any) => [block.start, block.duration]), [[0, 1], [1, 1], [2, 1], [3, 1]]);
    assert.deepEqual(lane(model).blocks[1].eventIds, ["path0", "path1"]);
    assert.equal(lane(model).blocks[1].domain.meta.merged_play_paths, true);
    assert.deepEqual(buildModel(ir, { layout: "instruction" }), model);
  });

  it("keeps unmerged waveform paths in their own lanes and supports Q1-only programs", () => {
    const model = buildModel({ events: [
      event("path0", "play", 1000, 2000, { lane: "rt.path0" }),
      event("path0-next", "play", 3000, 3004, { lane: "rt.path0" }),
      event("issue", "q1_issue", 50, 54, { sequencer_id: "OnlyQ1" }),
    ] }, { layout: "instruction" });
    assert.deepEqual(lane(model, "sequencer:Control:rt.path0").blocks.map((block: any) => [block.start, block.duration]), [[0, 1], [1, 1]]);
    assert.equal(lane(model, "sequencer:OnlyQ1").label, "OnlyQ1 · RT");
    assert.equal(lane(model, "sequencer:OnlyQ1:q1_issue").hidden, true);
    assert.deepEqual(model.viewport, { start: 0, end: 2 });
  });

  it("maps feedback annotations to referenced block edges in each independent lane", () => {
    const ir = { events: [
      event("wait", "wait", 0, 10_000), event("acq", "acquire", 10_000, 11_000),
      event("gap", "wait", 11_000, 20_000), event("pop", "feedback_pop", 20_000),
      event("commit", "feedback_com", 500_000, 500_000, { sequencer_id: "Readout" }),
    ], feedback_flows: [
      { id: "local", from_event_id: "acq", to_event_id: "pop", channel: "1" },
      { id: "cross", from_event_id: "acq", to_event_id: "commit", channel: "2" },
    ] };
    const before = JSON.stringify(ir);
    const normal = buildModel(ir);
    const model = buildModel(ir, { layout: "instruction" });
    const local = model.annotations.find((annotation: any) => annotation.id === "local");
    const cross = model.annotations.find((annotation: any) => annotation.id === "cross");
    assert.deepEqual([local.start, local.end, local.fromTime, local.toTime], [2, 3, 2, 3]);
    assert.deepEqual([cross.fromTime, cross.toTime], [2, 0]);
    assert.equal(cross.fromLaneId, "sequencer:Control");
    assert.equal(cross.toLaneId, "sequencer:Readout");
    for (const annotation of model.annotations) {
      const original = normal.annotations.find((candidate: any) => candidate.id === annotation.id);
      assert.deepEqual(annotation.eventIds, original.eventIds);
      assert.deepEqual(annotation.domain, original.domain);
    }
    assert.equal(JSON.stringify(ir), before);
  });

  it("maps loop controls to visible members, including merged paths and Q1-only members", () => {
    const model = buildModel({ events: [
      event("wait", "wait", 0, 10_000),
      event("path0", "play", 10_000, 10_004, { lane: "rt.path0" }),
      event("path1", "play", 10_000, 10_004, { lane: "rt.path1" }),
      event("member", "wait", 10_004, 20_000),
      event("issue", "q1_issue", 100, 104),
      event("loop", "loop_block", 10_000, 50_000, { meta: {
        loop_id: "L0", count: 4, first_iteration_event_ids: ["issue", "path0", "path1", "member"],
      } }),
      event("q1-loop", "loop_block", 100, 10_000, { meta: { first_iteration_event_ids: ["issue"] } }),
      event("preview", "loop_iteration_preview", 20_000, 70_000),
      event("omitted-loop", "loop_block", 20_000, 70_000, { meta: { first_iteration_event_ids: ["preview"] } }),
    ] }, { layout: "instruction" });
    const loop = model.annotations.find((annotation: any) => annotation.eventId === "loop");
    const q1 = model.annotations.find((annotation: any) => annotation.eventId === "q1-loop");
    const omitted = model.annotations.find((annotation: any) => annotation.eventId === "omitted-loop");
    assert.deepEqual([loop.laneId, loop.start, loop.end], ["sequencer:Control", 1, 3]);
    assert.deepEqual(loop.eventIds, ["loop", "issue", "path0", "path1", "member"]);
    assert.equal(loop.label, "L0 x4");
    assert.deepEqual([q1.laneId, q1.start, q1.end], ["sequencer:Control", 1, 1]);
    assert.deepEqual([omitted.laneId, omitted.start, omitted.end], ["sequencer:Control", 3, 3]);
    assert.ok(allBlocks(model).every((block) => !["loop_block", "loop_iteration_preview"].includes(block.kind)));
    assert.ok(model.annotations.every((annotation: any) => annotation.start >= 0 && annotation.end <= model.viewport.end));
  });

  it("uses a finite instruction viewport for empty or annotation-only input", () => {
    for (const ir of [{ events: [] }, { events: [event("loop", "loop_block", 400_000, 800_000)] }]) {
      const model = buildModel(ir, { layout: "instruction" });
      assert.deepEqual(model.viewport, { start: 0, end: 1 });
      assert.deepEqual(model.ticks, [{ leftPercent: 0, label: "#0" }, { leftPercent: 100, label: "#1" }]);
      assert.ok(allBlocks(model).every((block) => block.duration === 1));
    }
  });
});
