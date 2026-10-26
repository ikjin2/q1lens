import assert from "node:assert/strict";

const { buildQ1TimelineSharedModel: buildModel } = require("../src/q1timeline/media/timelineAdapter.js");

const branchId = "Control:branch:Control.q1asm:2:jge:taken";

function event(id: string, kind: string, start: number, duration: number, meta: Record<string, unknown> = {}, lane?: string) {
  return {
    id, kind, sequencer_id: "Control", lane: lane || (kind === "q1_issue" ? "q1.issue" : `rt.${kind}`),
    t0: { kind: "concrete", value: start }, t1: { kind: "concrete", value: start + duration },
    duration: { kind: "concrete", value: duration }, label: kind, confidence: "assumed",
    source: { file: "Control.q1asm", line: 3, column: 1, raw: "play 0,1,16" }, meta,
  };
}

function pathEvent(id: string, kind: string, path: string, start: number, duration: number, extra: Record<string, unknown> = {}, lane?: string, comparisonId = branchId) {
  return event(id, kind, start, duration, { branch_comparison_branch_id: comparisonId, branch_comparison_path: path, ...extra },
    `branch_compare.${path}.${lane || (kind === "q1_issue" ? "q1.issue" : `rt.${kind}`)}`);
}

function comparison() {
  return { events: [
    event("prefix-q1", "q1_issue", 0, 4, { op: "wait" }),
    event("prefix-rt", "wait", 0, 8),
    event("branch-q1", "q1_issue", 4, 4, { op: "jge", branch_id: branchId, assumed_branch_path: "both" }),
    event("branch-rt", "branch_region", 8, 0, { branch_id: branchId, assumed_branch_path: "both" }),
    // Deliberately list Continue first: display order must express alternatives consistently.
    pathEvent("continue-q1", "q1_issue", "fallthrough", 8, 4, { op: "upd_param" }),
    pathEvent("continue-rt", "upd_param", "fallthrough", 8, 16),
    pathEvent("continue-stop", "stop", "fallthrough", 24, 0),
    pathEvent("jump-q1", "q1_issue", "taken", 8, 4, { op: "upd_param" }),
    pathEvent("jump-rt", "upd_param", "taken", 8, 28),
    pathEvent("jump-stop", "stop", "taken", 36, 0),
  ] };
}

function blocks(model: any): any[] { return model.lanes.flatMap((lane: any) => lane.blocks); }
function pathLanes(model: any): any[] { return model.lanes.filter((lane: any) => lane.branchComparisonPath); }

describe("Q1 timeline branch comparison lanes", () => {
  it("keeps the common prefix once and groups Jump and Continue separately in either layout and mode", () => {
    for (const layout of ["time", "instruction"]) {
      for (const mode of ["normal", "debug"]) {
        const model = buildModel(comparison(), { layout, mode, expandedQ1IssueSequencers: ["Control"] });
        const branches = pathLanes(model);
        assert.deepEqual(branches.map((lane: any) => [lane.branchComparisonPath, lane.kind, lane.label]), [
          ["taken", "branch-runtime", "Jump · RT"], ["taken", "q1-issue", "Jump · Q1"],
          ["fallthrough", "branch-runtime", "Continue · RT"], ["fallthrough", "q1-issue", "Continue · Q1"],
        ]);
        for (const lane of branches) {
          assert.equal(lane.branchComparisonId, branchId);
          assert.equal(lane.parentGroupId, "sequencer:Control");
          assert.equal(lane.sequencer, "Control");
          assert.equal(Boolean(lane.hidden), lane.kind === "q1-issue" && mode !== "debug");
          assert.ok(lane.blocks.every((block: any) => block.domain.meta.branch_comparison_path === lane.branchComparisonPath));
        }
        const common = model.lanes.filter((lane: any) => !lane.branchComparisonPath).flatMap((lane: any) => lane.blocks);
        assert.deepEqual(common.map((block: any) => block.id).sort(), ["branch-q1", "branch-rt", "prefix-q1", "prefix-rt"]);
        assert.equal(new Set(blocks(model).flatMap((block: any) => block.eventIds)).size, comparison().events.length);
        const folded = buildModel(comparison(), { layout, mode, expandedQ1IssueSequencers: [] });
        assert.ok(pathLanes(folded).filter((lane: any) => lane.kind === "q1-issue").every((lane: any) => lane.hidden === (mode !== "debug")));
      }
    }
  });

  it("retains real timestamps in Time and starts instruction alternatives after their shared branch point", () => {
    const ir = comparison();
    const timed = buildModel(ir, { layout: "time" });
    for (const lane of pathLanes(timed)) {
      for (const block of lane.blocks) {
        const original = ir.events.find((candidate) => candidate.id === block.id)!;
        assert.equal(block.start, original.t0.value);
        assert.equal(block.duration, original.duration.value);
      }
    }
    const instruction = buildModel(ir, { layout: "instruction" });
    for (const lane of pathLanes(instruction)) {
      const shared = instruction.lanes.find((candidate: any) => !candidate.branchComparisonPath &&
        (lane.kind === "q1-issue" ? candidate.kind === "q1-issue" : candidate.kind === "runtime"));
      const marker = shared.blocks.find((block: any) => block.id === (lane.kind === "q1-issue" ? "branch-q1" : "branch-rt"));
      assert.equal(lane.blocks[0].start, marker.start + marker.duration);
      assert.deepEqual(lane.blocks.map((block: any) => [block.start, block.duration]),
        lane.blocks.map((_block: any, index: number) => [2 + index, 1]));
    }
    assert.equal(instruction.totalTime, 4);
  });

  it("merges prefixed AWG paths within each comparison only, even when packet, source and timing match", () => {
    const events = [];
    for (const comparisonId of [branchId, "Control:branch:second"]) {
      for (const path of ["taken", "fallthrough"]) {
        for (const awgPath of [1, 0]) {
          events.push(pathEvent(`${comparisonId}:${path}:p${awgPath}`, "play", path, 8, 16,
            { rt_packet_id: "same-packet", waveform_index: awgPath }, `rt.path${awgPath}`, comparisonId));
        }
      }
    }
    const model = buildModel({ events });
    const lanes = pathLanes(model);
    assert.equal(lanes.length, 4);
    for (const lane of lanes) {
      assert.equal(lane.kind, "branch-runtime");
      assert.equal(lane.blocks.length, 1);
      const block = lane.blocks[0];
      assert.equal(block.domain.meta.merged_play_paths, true);
      assert.deepEqual(block.eventIds, [0, 1].map((path) => `${lane.branchComparisonId}:${lane.branchComparisonPath}:p${path}`));
    }
  });

  it("hides prefixed debug events in Normal and keeps them in the matching comparison in Debug", () => {
    const ir = comparison();
    ir.events.push(pathEvent("jump-debug", "slack", "taken", 8, 4, {}, "debug.slack"));
    for (const layout of ["time", "instruction"]) {
      assert.equal(blocks(buildModel(ir, { layout, mode: "normal" })).some((block: any) => block.id === "jump-debug"), false);
      const debug = buildModel(ir, { layout, mode: "debug" });
      const lane = debug.lanes.find((candidate: any) => candidate.blocks.some((block: any) => block.id === "jump-debug"));
      assert.ok(lane);
      assert.equal(lane.branchComparisonPath, "taken");
      assert.equal(lane.branchComparisonId, branchId);
    }
  });

  it("leaves no comparison rows when switching to a single assumption or collapsing the branch", () => {
    const ir = comparison();
    buildModel(ir, { layout: "instruction" });
    for (const assumedPath of ["taken", "fallthrough", "collapsed"]) {
      const events = ir.events.filter((item) => !item.meta.branch_comparison_path).map((item) => ({
        ...item, meta: { ...item.meta, ...(item.meta.branch_id ? { assumed_branch_path: assumedPath } : {}) },
      }));
      if (assumedPath !== "collapsed") events.push(event("current-rt", "upd_param", 8, 16, { assumed_branch_path: assumedPath }));
      for (const layout of ["time", "instruction"]) {
        const model = buildModel({ events }, { layout });
        assert.deepEqual(pathLanes(model), []);
        assert.equal(blocks(model).some((block: any) => block.id === "current-rt"), assumedPath !== "collapsed");
      }
    }
  });

  it("preserves event identity, IR metadata and caller options when building comparison rows", () => {
    const ir = comparison();
    const options = { layout: "instruction", mode: "debug", expandedQ1IssueSequencers: ["Control"] };
    const before = JSON.stringify({ ir, options });
    const model = buildModel(ir, options);
    for (const block of blocks(model)) {
      const original = ir.events.find((item) => item.id === block.eventId)!;
      assert.ok(original);
      assert.deepEqual(block.domain.source, original.source);
      assert.deepEqual(block.domain.meta, original.meta);
    }
    assert.equal(JSON.stringify({ ir, options }), before);
    assert.deepEqual(buildModel(ir, options), model);
  });

  it("retains historical comparison paths as removed ghosts without mixing in the current single path", () => {
    const ir = comparison();
    ir.events = ir.events.map((item) => item.meta.branch_comparison_path
      ? { ...item, id: `${item.id}:removed`, meta: { ...item.meta, diff_status: "removed" } }
      : { ...item, meta: { ...item.meta, ...(item.meta.branch_id ? { assumed_branch_path: "taken" } : {}) } });
    ir.events.push(event("current-rt", "upd_param", 8, 28, { assumed_branch_path: "taken" }));
    for (const layout of ["time", "instruction"]) {
      const model = buildModel(ir, { layout });
      assert.equal(pathLanes(model).length, 4);
      assert.ok(pathLanes(model).flatMap((lane: any) => lane.blocks).every((block: any) => /diff-removed/.test(block.classNames)));
      const currentLane = model.lanes.find((lane: any) => lane.blocks.some((block: any) => block.id === "current-rt"));
      assert.equal(currentLane.branchComparisonPath, undefined);
      assert.equal(currentLane.kind, "runtime");
    }
  });

  it("connects shared feedback to the receiving alternative's row and keeps same-row feedback inline", () => {
    const ir = { ...comparison(), feedback_flows: [
      { id: "shared-to-jump", from_event_id: "shared-send", to_event_id: "jump-receive", channel: "1" },
      { id: "jump-local", from_event_id: "jump-receive", to_event_id: "jump-send", channel: "1" },
    ] };
    ir.events.push(event("shared-send", "feedback_com", 0, 4));
    ir.events.push(pathEvent("jump-receive", "feedback_pop", "taken", 12, 4));
    ir.events.push(pathEvent("jump-send", "feedback_com", "taken", 16, 4));
    for (const layout of ["time", "instruction"]) {
      const model = buildModel(ir, { layout });
      const cross = model.annotations.find((item: any) => item.id === "shared-to-jump");
      const inline = model.annotations.find((item: any) => item.id === "jump-local");
      const receiver = model.lanes.find((lane: any) => lane.blocks.some((block: any) => block.eventId === "jump-receive"));
      assert.equal(cross.type, "feedback-cross");
      assert.equal(cross.fromLaneId, "sequencer:Control");
      assert.equal(cross.toLaneId, receiver.id);
      assert.equal(inline.type, "feedback-inline");
      assert.equal(inline.laneId, receiver.id);
    }
  });

  it("keeps a loop-only alternative visible in Time even when Q1 is folded", () => {
    const ir = { events: [
      event("branch", "branch_region", 0, 0, { branch_id: branchId }),
      pathEvent("jump-q1", "q1_issue", "taken", 0, 4),
      pathEvent("jump-loop", "loop_block", "taken", 0, 20, { first_iteration_event_ids: ["jump-q1"], count: "forever" }),
      pathEvent("continue-stop", "stop", "fallthrough", 0, 0),
    ] };
    const model = buildModel(ir, { layout: "time", expandedQ1IssueSequencers: [] });
    const loop = model.annotations.find((item: any) => item.eventId === "jump-loop");
    const row = model.lanes.find((lane: any) => lane.id === loop.laneId);
    assert.ok(row);
    assert.equal(row.branchComparisonPath, "taken");
    assert.equal(Boolean(row.hidden), false);
  });
});
