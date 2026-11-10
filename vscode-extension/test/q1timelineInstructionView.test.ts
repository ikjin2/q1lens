import assert from "node:assert/strict";

const { buildQ1TimelineSharedModel: buildModel, buildQ1TimelineSelectionIndex } = require("../src/q1timeline/media/timelineAdapter.js");

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
  it("pairs RT wait auxiliaries by packet occurrence and comparison path with a unique legacy fallback", () => {
    const compared = (path: string, packet?: string) => ({
      lane: `branch_compare.${path}.rt.wait_trigger`,
      meta: { branch_comparison_path: path, branch_comparison_branch_id: "gate",
        branch_comparison_kind: "rt_condition", rt_packet_id: packet },
    });
    const ir = { events: [
      event("first", "wait_trigger", 0, 24, {meta: {rt_packet_id: "p1"}}),
      event("first-aux", "trigger_wait", 0, 20, {meta: {rt_packet_id: "p1"}}),
      event("second", "wait_trigger", 30, 44, {meta: {rt_packet_id: "p2"}}),
      event("second-legacy", "trigger_wait", 30, 40),
      event("wrong-packet", "trigger_wait", 30, 40, {meta: {rt_packet_id: "missing"}}),
      event("execute", "wait_trigger", 50, 64, compared("taken", "p3")),
      event("execute-aux", "trigger_wait", 50, 60, compared("taken", "p3")),
      event("skip-aux", "trigger_wait", 50, 60, compared("fallthrough", "p3")),
      event("ambiguous-1", "wait_trigger", 70, 84),
      event("ambiguous-2", "wait_trigger", 70, 88),
      event("ambiguous-aux", "trigger_wait", 70, 80),
    ] };
    const original = JSON.stringify(ir);
    for (const layout of ["instruction", "time"]) {
      const blocks = new Map(allBlocks(buildModel(ir, {layout})).map((block) => [block.id, block]));
      for (const [primary, aux] of [["first", "first-aux"], ["second", "second-legacy"], ["execute", "execute-aux"]]) {
        assert.deepEqual(blocks.get(primary).eventIds, [primary, aux]);
        assert.equal(blocks.has(aux), false);
      }
      for (const id of ["wrong-packet", "skip-aux", "ambiguous-aux"]) assert.ok(blocks.has(id));
    }
    assert.equal(buildQ1TimelineSelectionIndex(ir).get("second-legacy").rtNavigationEventId, "second");
    assert.equal(JSON.stringify(ir), original);
  });

  it("navigates a pending RT wait from its own Q1 issue without treating it as an application", () => {
    const ir = { events: [
      event("prior", "wait", 0, 20),
      event("issue", "q1_issue", 4, 8, {meta: {rt_packet_id: "p", op: "wait_trigger"}}),
      event("pending", "trigger_wait", 20, 40, {meta: {rt_packet_id: "p", q1_issue_event_id: "issue", pending: true}}),
    ] };
    const index = buildQ1TimelineSelectionIndex(ir);
    assert.deepEqual(index.get("issue").rtEventIds, ["pending"]);
    assert.deepEqual(index.get("issue").appliedEventIds, []);
    assert.equal(index.get("issue").rtNavigationEventId, "pending");
    const pending = allBlocks(buildModel(ir, {mode: "debug"})).find((block) => block.id === "pending");
    assert.equal(pending.rtDetails, undefined);
  });

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

  it("places selectable trigger and rejected LINQ arrivals inside the wait without adding slots", () => {
    const aligned = (start: number, end = start, meta = {}) => ({ meta: { aligned_t0: start, aligned_t1: end, ...meta } });
    const message = "Feedback queue overflow at 115 ns.";
    const ir = {
      events: [
        event("wait", "wait", 60, 160, aligned(0, 100)),
        event("trigger", "trigger_receive", 90, 94, aligned(30, 30, { address: 7 })),
        event("reject", "feedback_rejected", 115, 115, aligned(55, 55, { id: 42, source_event_id: "send" })),
        event("stop", "stop", 160, 160, aligned(100)),
      ],
      diagnostics: [
        { severity: "error", category: "feedback_queue_overflow", related_events: ["reject"], message },
        { severity: "error", category: "other_error", related_events: ["stop"], message: "Stop error" },
      ],
    };
    const before = JSON.stringify(ir);
    for (const layout of ["instruction", "time"]) {
      const model = buildModel(ir, { layout, timeBasis: "aligned" });
      const [wait, trigger, reject, stop] = lane(model).blocks;
      assert.deepEqual([wait, trigger, reject, stop].map((block) => [block.id, block.start, block.duration]), layout === "instruction"
        ? [["wait", 0, 1], ["trigger", 0.3, 0], ["reject", 0.55, 0], ["stop", 1, 1]]
        : [["wait", 0, 100], ["trigger", 30, 0], ["reject", 55, 0], ["stop", 100, 0]]);
      if (layout === "instruction") assert.deepEqual(model.viewport, { start: 0, end: 2 });
      assert.equal(trigger.pointMarker, true);
      assert.equal(trigger.title, "Trigger address 7 · 30 ns");
      assert.equal(reject.pointMarker, true);
      assert.equal(reject.title, "Rejected LINQ arrival · ID 42 · 55 ns");
      assert.equal(reject.domain.meta.source_event_id, "send");
      assert.deepEqual(reject.eventIds, ["reject"]);
      assert.deepEqual(reject.diagnosticErrors, [{ eventId: "reject", message }]);
      assert.deepEqual(stop.diagnosticErrors, [{ eventId: "stop", message: "Stop error" }]);
    }
    assert.equal(JSON.stringify(ir), before);
  });

  it("anchors arrivals at a comparison command boundary and past its last instruction", () => {
    const scope = { branch_comparison_branch_id: "branch", branch_comparison_path: "taken" };
    const model = buildModel({ events: [
      event("branch", "branch_region", 0, 0, { meta: { branch_id: "branch" } }),
      event("wait", "wait", 0, 100, { meta: scope }),
      event("boundary", "trigger_receive", 100, 100, { meta: scope }),
      event("stop", "stop", 100, 100, { meta: scope }),
      event("after", "feedback_rejected", 150, 150, { meta: scope }),
    ] }, { layout: "instruction" });
    const comparison = model.lanes.find((item: any) => item.branchComparisonPath === "taken");
    assert.deepEqual(comparison.blocks.map((block: any) => [block.id, block.start, block.duration]), [
      ["wait", 1, 1], ["boundary", 2, 0], ["stop", 2, 1], ["after", 3, 0],
    ]);
    assert.deepEqual(model.viewport, { start: 0, end: 3 });
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
