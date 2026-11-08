import assert from "node:assert/strict";

const { buildQ1TimelineSharedModel: buildModel } = require("../src/q1timeline/media/timelineAdapter.js");
const ns = (value: number) => ({ kind: "concrete", value });
const event = (id: string, kind: string, start: number, end: number, meta: any = {}, extra: any = {}) => ({
  id, kind, sequencer_id: "seq0", lane: kind === "q1_issue" ? "debug.q1_issue" : `rt.${kind}`,
  t0: ns(start), t1: ns(end), duration: ns(end - start), confidence: "exact", meta,
  source: { file: "seq0.q1asm", line: 1 }, ...extra,
});

describe("Q1 timeline diagnostic placement", () => {
  for (const mode of ["normal", "debug"]) {
    for (const layout of ["time", "instruction"]) {
      it(`marks existing command blocks and groups hidden Q1 errors in ${mode}/${layout}`, () => {
        const ir = { events: [
          event("q1", "q1_issue", 0, 4, { op: "add" }),
          event("q2", "q1_issue", 4, 8, { op: "loop" }),
          event("play", "play", 100, 120, { rt_packet_id: "p0" }),
          event("warning", "underflow_warning", 100, 100, { rt_packet_id: "p0", slack_ns: -4 }, { lane: "debug.underflow" }),
        ], diagnostics: [
          { severity: "error", category: "register_hazard", message: "Register is not ready.", related_events: ["q1"] },
          { severity: "error", category: "invalid_loop_count", message: "Invalid count.", related_events: ["q2"] },
          { severity: "error", category: "definite_underflow", message: "RT underflow.", related_events: ["warning", "q1"] },
        ] };
        const before = JSON.stringify(ir);
        const model = buildModel(ir, { mode, layout });
        const q1 = model.lanes.find((lane: any) => lane.kind === "q1-issue");
        const rt = model.lanes.find((lane: any) => lane.kind === "runtime");
        assert.equal(q1.hidden, mode === "normal");
        assert.equal(q1.blocks.find((block: any) => block.id === "q1").diagnosticErrors[0].message, "Register is not ready.");
        assert.deepEqual(rt.q1Errors?.map((error: any) => error.eventId), mode === "normal" ? ["q1", "q2"] : undefined);
        const blocks = model.lanes.flatMap((lane: any) => lane.blocks);
        const command = blocks.find((block: any) => block.id === "play");
        assert.deepEqual(command.diagnosticErrors, [{ eventId: "warning", message: "RT underflow · 4 ns late" }]);
        assert.ok(command.eventIds.includes("warning"));
        assert.ok(!blocks.some((block: any) => block.diagnosticPlacement || block.id === "warning"));
        assert.equal(model.lanes.some((lane: any) => lane.label === "Issues" || lane.label === "RT underflow"), false);
        assert.equal(JSON.stringify(ir), before);
      });
    }
  }

  it("draws a point only for an unattached definite underflow", () => {
    const model = buildModel({ events: [event("wait", "wait", 0, 100),
      event("underflow", "underflow_warning", 10, 10, { status: "definite_underflow" }, { lane: "debug.underflow" }),
      event("possible", "underflow_warning", 15, 15, { status: "possible_underflow" }, { lane: "debug.underflow" }),
      event("unknown", "analysis_incomplete", 20, 20, { reason: "feedback_arrival_unknown" }, { lane: "debug.underflow" })] });
    const blocks = model.lanes.flatMap((lane: any) => lane.blocks);
    const mark = blocks.find((block: any) => block.id === "underflow");
    assert.equal(mark.diagnosticPlacement, "point");
    assert.deepEqual([mark.start, mark.duration], [10, 0]);
    assert.deepEqual(blocks.map((block: any) => block.id), ["wait", "underflow"]);
  });

  it("anchors underflow to its own branch and loop occurrence", () => {
    const events = [0, 1].flatMap((iteration) => [
      event(`play${iteration}`, "play", iteration * 100, iteration * 100 + 20, {
        rt_packet_id: "p0", loop_preview: "L0", loop_iteration_index: iteration,
      }),
    ]);
    events.push(event("warning", "underflow_warning", 100, 100, {
      rt_packet_id: "p0", loop_preview: "L0", loop_iteration_index: 1,
    }));
    const model = buildModel({ events });
    const marked = model.lanes.flatMap((lane: any) => lane.blocks).filter((block: any) => block.diagnosticErrors);
    assert.deepEqual(marked.map((block: any) => block.id), ["play1"]);
  });

  it("uses the RT command even when its later acquisition result is listed first", () => {
    const model = buildModel({ events: [
      event("result", "acquisition_result", 200, 200, { rt_packet_id: "p0" }),
      event("acquire", "acquire", 100, 120, { rt_packet_id: "p0" }),
      event("warning", "underflow_warning", 100, 100, { rt_packet_id: "p0" }),
    ] });
    const marked = model.lanes.flatMap((lane: any) => lane.blocks).filter((block: any) => block.diagnosticErrors);
    assert.deepEqual(marked.map((block: any) => block.id), ["acquire"]);
  });
});
