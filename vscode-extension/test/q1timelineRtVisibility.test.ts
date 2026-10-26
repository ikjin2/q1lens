import assert from "node:assert/strict";

const { buildQ1TimelineSharedModel: buildModel } = require("../src/q1timeline/media/timelineAdapter.js");

function event(id: string, kind: string, start: number, meta: Record<string, unknown> = {}, extra: Record<string, unknown> = {}): any {
  return { id, kind, sequencer_id: "Control", lane: kind === "q1_issue" ? "q1.issue" : `rt.${kind}`,
    t0: { value: start }, t1: { value: start + 4 }, duration: { value: 4 }, confidence: "exact", meta, ...extra };
}

describe("RT timeline visibility", () => {
  it("shows Q1 only in Debug for both layouts, independently of old fold state", () => {
    const ir = { events: [event("rt", "wait", 0), ...Array.from({ length: 10 }, (_, index) =>
      event(`q1-${index}`, "q1_issue", 1000 + index * 4))] };
    const before = JSON.stringify(ir);
    for (const layout of ["time", "instruction"]) {
      for (const mode of ["normal", "debug"]) {
        for (const expandedQ1IssueSequencers of [[], ["Control"]]) {
          const model = buildModel(ir, { layout, mode, expandedQ1IssueSequencers });
          const q1 = model.lanes.find((lane: any) => lane.kind === "q1-issue");
          assert.equal(q1.hidden, mode !== "debug");
          assert.equal(q1.blocks.length, 10, "Keep Q1 identities available for debugging and selection");
          if (mode === "normal") assert.equal(model.totalTime, layout === "time" ? 4 : 1);
        }
      }
    }
    assert.equal(JSON.stringify(ir), before);
  });

  it("flags underflow and incomplete analysis in Normal while other queue details stay in Debug", () => {
    const ir = { events: [event("rt", "wait", 100, { rt_packet_id: "p1" }),
      event("underflow", "underflow_warning", 100, { rt_packet_id: "p1", status: "definite_underflow" }, { lane: "debug.underflow" }),
      event("incomplete", "analysis_incomplete", 104, { status: "analysis_incomplete" }, { lane: "debug.underflow" }),
      event("slack", "slack", 100, {}, { lane: "debug.slack" })] };
    for (const layout of ["time", "instruction"]) {
      const model = buildModel(ir, { layout });
      const rt = model.lanes.find((lane: any) => lane.id === "sequencer:Control");
      const underflow = rt.blocks.find((block: any) => block.eventId === "underflow");
      const incomplete = rt.blocks.find((block: any) => block.eventId === "incomplete");
      assert.ok(underflow);
      assert.ok(incomplete);
      assert.equal(underflow.domain.meta.status, "definite_underflow");
      assert.equal(incomplete.domain.meta.status, "analysis_incomplete");
      assert.ok(model.lanes.every((lane: any) => lane.blocks.every((block: any) => block.eventId !== "slack")));
      if (layout === "instruction") {
        assert.deepEqual([underflow.start, underflow.duration], [0, 0]);
        assert.deepEqual([incomplete.start, incomplete.duration], [1, 0]);
        assert.equal(model.totalTime, 1, "Warnings mark boundaries and do not add RT instructions");
      }
      const debug = buildModel(ir, { layout, mode: "debug" });
      const warnings = debug.lanes.find((lane: any) => lane.id === "sequencer:Control:debug.underflow");
      assert.deepEqual(warnings.blocks.map((block: any) => block.eventId), ["underflow", "incomplete"]);
      assert.ok(debug.lanes.some((lane: any) => lane.blocks.some((block: any) => block.eventId === "slack")));
    }
  });

  it("anchors warning markers within the matching Compare alternative", () => {
    const events = [event("prefix", "wait", 0), event("branch", "branch_region", 4, { branch_id: "branch" })];
    for (const [index, path] of ["taken", "fallthrough"].entries()) {
      const scope = { branch_comparison_branch_id: "branch", branch_comparison_path: path, rt_packet_id: "shared" };
      if (index === 1) events.push(event("continue-prefix", "wait", 4, { ...scope, rt_packet_id: "other" }, { lane: `branch_compare.${path}.rt.wait` }));
      events.push(event(`${path}-rt`, "upd_param", 8, scope, { lane: `branch_compare.${path}.rt.control` }),
        event(`${path}-warning`, "underflow_warning", 8, scope, { lane: `branch_compare.${path}.debug.underflow` }));
    }
    const model = buildModel({ events }, { layout: "instruction" });
    for (const lane of model.lanes.filter((item: any) => item.branchComparisonPath)) {
      const rt = lane.blocks.find((block: any) => block.eventId === `${lane.branchComparisonPath}-rt`);
      const warning = lane.blocks.find((block: any) => block.eventId === `${lane.branchComparisonPath}-warning`);
      assert.equal(warning.start, rt.start);
      assert.equal(warning.duration, 0);
    }
  });

  it("anchors packet warnings to the command instead of a later physical effect", () => {
    const ir = { events: [event("acq", "acquire", 100, { rt_packet_id: "p1" }),
      event("result", "acquisition_result", 200, { rt_packet_id: "p1" }),
      event("warning", "underflow_warning", 300, { rt_packet_id: "p1" }, { lane: "debug.underflow" })] };
    const model = buildModel(ir, { layout: "instruction" });
    const blocks = model.lanes.flatMap((lane: any) => lane.blocks);
    assert.equal(blocks.find((block: any) => block.eventId === "warning").start,
      blocks.find((block: any) => block.eventId === "acq").start);
  });
});
