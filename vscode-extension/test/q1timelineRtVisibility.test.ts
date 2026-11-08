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

  it("flags the same RT command in Normal and Debug without adding queue blocks", () => {
    const ir = { events: [event("rt", "wait", 100, { rt_packet_id: "p1" }),
      event("underflow", "underflow_warning", 100, { rt_packet_id: "p1", status: "definite_underflow" }, { lane: "debug.underflow" }),
      event("incomplete", "analysis_incomplete", 104, { status: "analysis_incomplete" }, { lane: "debug.underflow" }),
      event("slack", "slack", 100, {}, { lane: "debug.slack" })] };
    for (const layout of ["time", "instruction"]) {
      for (const mode of ["normal", "debug"]) {
        const model = buildModel(ir, { layout, mode });
        const blocks = model.lanes.flatMap((lane: any) => lane.blocks);
        assert.deepEqual(blocks.map((block: any) => block.eventId), ["rt"]);
        assert.equal(blocks[0].diagnosticErrors[0].eventId, "underflow");
        if (layout === "instruction") assert.equal(model.totalTime, 1);
        assert.equal(model.lanes.some((lane: any) => lane.label === "RT underflow" || lane.label === "Issues"), false);
      }
    }
  });

  it("shows underflow lateness as a positive duration without exposing normal slack", () => {
    const ir = { events: [
      event("normal", "slack", 0, { slack_ns: 24 }, { lane: "debug.slack" }),
      event("late", "slack", 4, { slack_ns: -24 }, { lane: "debug.slack" }),
      event("underflow", "underflow_warning", 4, { status: "definite_underflow", slack_ns: -24 }),
      event("possible", "underflow_warning", 8, { status: "possible_underflow", slack_ns: -8 }),
      event("unknown", "underflow_warning", 12, { simulated: true }),
    ] };
    const before = JSON.stringify(ir);
    for (const mode of ["normal", "debug"]) {
      for (const layout of ["time", "instruction"]) {
        const blocks = buildModel(ir, { mode, layout }).lanes.flatMap((lane: any) => lane.blocks);
        assert.deepEqual(blocks.map((block: any) => block.eventId), ["underflow", "unknown"]);
        assert.deepEqual(blocks.map((block: any) => block.label), [
          "RT underflow · 24 ns late", "RT underflow",
        ]);
        assert.ok(blocks.every((block: any) => !/slack|-[0-9]/.test(block.title)));
      }
    }
    assert.equal(JSON.stringify(ir), before);
  });

  it("marks the RT command within the matching Compare alternative", () => {
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
      assert.equal(rt.diagnosticErrors[0].eventId, `${lane.branchComparisonPath}-warning`);
    }
  });

  it("anchors packet warnings to the command instead of a later physical effect", () => {
    const ir = { events: [event("acq", "acquire", 100, { rt_packet_id: "p1" }),
      event("result", "acquisition_result", 200, { rt_packet_id: "p1" }),
      event("warning", "underflow_warning", 300, { rt_packet_id: "p1" }, { lane: "debug.underflow" })] };
    const model = buildModel(ir, { layout: "instruction" });
    const blocks = model.lanes.flatMap((lane: any) => lane.blocks);
    assert.deepEqual(blocks.filter((block: any) => block.diagnosticErrors).map((block: any) => block.eventId), ["acq"]);
  });
});
