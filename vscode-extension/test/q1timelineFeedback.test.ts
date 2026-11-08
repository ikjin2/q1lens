import assert from "node:assert/strict";

const { buildQ1TimelineSharedModel, buildQ1TimelineSelectionIndex } = require("../src/q1timeline/media/timelineAdapter.js");

const feedbackEvent = (id: string, kind: string, start: number, end: number, meta = {}) => ({
  id, kind, sequencer_id: "seq", lane: kind === "q1_issue" ? "q1.issue" : "rt.feedback",
  t0: { value: start }, t1: { value: end }, meta,
  source: { file: "seq.q1asm", line: kind === "feedback_com" ? 2 : 4,
    raw: kind === "feedback_com" ? "fb_com_data 4,7,80" : "fb_pop_data 4,R0" },
});

describe("Feedback RT anchors", () => {
  const timing = (route: string, base: number, send = 100) => {
    const extra = route === "self_cast" ? 0 : 200;
    return { route, base_latency_ns: base, transport_delay_ns: extra, latency_ns: base + extra,
      send_available_ns: send, arrival_ns: send + base + extra };
  };

  it("places pop and pull reception at transmission plus route latency independently of Q1 consumption", () => {
    for (const op of ["fb_pop_data", "fb_pull_data"]) for (const consumedAt of [120, 1000]) {
      for (const [target, module, route, base, latency] of [["seq", "QCM0", "self_cast", 60, 60],
        ["intra", "QCM0", "intra_cast", 150, 350], ["multi", "QCM1", "multi_cast_worst_case", 380, 580]] as const) {
        const ir = {
          sequencers: [{ id: "seq", module: "QCM0" }, { id: target, module }], events: [
            feedbackEvent("send", "feedback_com", 100, 180),
            { ...feedbackEvent("wait", "wait", target === "seq" ? 180 : 100, target === "seq" ? 1180 : 1100), sequencer_id: target, lane: "rt.control" },
            { ...feedbackEvent("pop", "feedback_pop", consumedAt, consumedAt, { op }), sequencer_id: target,
              source: { file: "receiver.q1asm", line: 4, raw: op === "fb_pop_data" ? "fb_pop_data 4,R0" : "fb_pull_data R0,R1" } },
          ], feedback_flows: [{ id: "flow", from_event_id: "send", to_event_id: "pop", channel: target === "seq" ? "4" : "16",
            timing: timing(route, base) }] };
        for (const mode of ["normal", "debug"]) for (const layout of ["time", "instruction"]) {
          const model = buildQ1TimelineSharedModel(ir, { mode, layout });
          const annotation = model.annotations[0];
          const expected = layout === "time" ? 100 + latency : target === "seq"
            ? latency / 80 : latency / 1000;
          assert.equal(annotation.toTime, expected, `${op}/${target}/${mode}/${layout}`);
          assert.match(annotation.targetTitle, /Receive \(queue\).*estimated feedback queue arrival/);
          assert.ok(annotation.targetTitle.includes(`+${latency} ns`));
          assert.ok(annotation.targetTitle.includes(`Consumer: ${op}`));
          assert.equal(annotation.toEventId, "pop", "The dot still navigates to its consumer");
        }
      }
    }
  });

  it("uses multicast worst-case latency when receiver module is unknown", () => {
    const model = buildQ1TimelineSharedModel({ events: [
      feedbackEvent("send", "feedback_com", 100, 180),
      { ...feedbackEvent("pop", "feedback_pop", 1000, 1000), sequencer_id: "other" },
    ], feedback_flows: [{ id: "flow", from_event_id: "send", to_event_id: "pop", channel: "16", timing: timing("multi_cast_worst_case", 380) }] });
    assert.equal(model.annotations[0].toTime, 680);
    assert.match(model.annotations[0].targetTitle, /multi cast worst case/);
  });

  it("converts estimated arrival between sender and receiver clocks", () => {
    const ir = { alignment: { seq: { offset: -100 }, other: { offset: -200 } }, events: [
      feedbackEvent("send", "feedback_com", 300, 380, { aligned_t0: 200, aligned_t1: 280 }),
      { ...feedbackEvent("pop", "feedback_pop", 1000, 1000, { aligned_t0: 800 }), sequencer_id: "other" },
    ], feedback_flows: [{ id: "flow", from_event_id: "send", to_event_id: "pop", channel: "16", timing: timing("multi_cast_worst_case", 380, 200) }] };
    assert.equal(buildQ1TimelineSharedModel(ir).annotations[0].toTime, 980);
    assert.equal(buildQ1TimelineSharedModel(ir, { timeBasis: "aligned" }).annotations[0].toTime, 780);
  });

  it("starts acquisition delivery latency at integration end", () => {
    const model = buildQ1TimelineSharedModel({ events: [
      { ...feedbackEvent("acq", "acquire", 100, 104), lane: "rt.acquire", meta: {
        feedback: { data_type: "thresholded_bits" }, acquisition_window: { start_ns: 100, end_ns: 1100,
          configured_duration_ns: 1000, effective_duration_ns: 1000, source: "hardware_parameters", status: "completed" },
      } }, feedbackEvent("pop", "feedback_pop", 2000, 2000),
    ], feedback_flows: [{ id: "flow", from_event_id: "acq", to_event_id: "pop", channel: "4", timing: timing("self_cast", 160, 1100) }] });
    assert.equal(model.annotations[0].fromTime, 1100);
    assert.equal(model.annotations[0].toTime, 1260);
  });

  it("shows unresolved packet timing only as instruction order, without a numeric time marker", () => {
    const ir = { events: [feedbackEvent("send", "feedback_com", 100, 180), feedbackEvent("pop", "feedback_pop", 1000, 1000)],
      feedback_flows: [{ id: "flow", from_event_id: "send", to_event_id: "pop", channel: "4",
        timing: { ...timing("self_cast", 160), send_available_ns: null, arrival_ns: null } }] };
    assert.equal(buildQ1TimelineSharedModel(ir).annotations.length, 0);
    const model = buildQ1TimelineSharedModel(ir, { layout: "instruction" });
    assert.match(model.annotations[0].targetTitle, /estimated feedback queue arrival.*time unknown/);
    assert.doesNotMatch(model.annotations[0].targetTitle, /1000 ns/);
  });

  it("keeps early pop warnings in diagnostics without adding lane marks", () => {
    const ir = { events: [feedbackEvent("send", "feedback_com", 100, 180),
      { ...feedbackEvent("wait", "wait", 0, 300), sequencer_id: "receiver", lane: "rt.control" },
      { ...feedbackEvent("pop", "feedback_pop", 300, 300), sequencer_id: "receiver" }],
      diagnostics: [{ category: "feedback_latency_violation", severity: "warning", message: "fb_pop_data may block for 380 ns",
        related_events: ["send", "pop"] }] };
    for (const mode of ["normal", "debug"]) for (const layout of ["time", "instruction"]) {
      const model = buildQ1TimelineSharedModel(ir, { mode, layout });
      const blocks = model.lanes.flatMap((lane: any) => lane.blocks);
      assert.ok(!blocks.some((block: any) => block.diagnosticErrors || block.diagnosticPlacement));
    }
  });

  it("connects the RT send start and the recorded receive rather than Q1 issue times or RT duration ends", () => {
    const ir = { events: [
      feedbackEvent("issue-send", "q1_issue", 0, 4, { op: "fb_com_data" }),
      feedbackEvent("issue-pop", "q1_issue", 4, 8, { op: "fb_pop_data" }),
      feedbackEvent("send", "feedback_com", 100, 180, { q1_issue_event_id: "issue-send" }),
      feedbackEvent("pop", "feedback_pop", 500, 500, { q1_issue_event_id: "issue-pop" }),
    ], feedback_flows: [{ id: "local", from_event_id: "send", to_event_id: "pop", channel: "4" }] };
    for (const mode of ["normal", "debug"]) {
      for (const layout of ["time", "instruction"]) {
        const model = buildQ1TimelineSharedModel(ir, { mode, layout });
        const annotation = model.annotations[0];
        const lane = model.lanes.find((item: any) => item.id === annotation.laneId);
        const send = lane.blocks.find((item: any) => item.eventId === "send");
        assert.equal(lane.kind, "runtime");
        assert.equal(annotation.fromTime, send.start, `${mode}/${layout}: send at RT start`);
        assert.equal(annotation.toTime, layout === "time" ? 500 : 1, `${mode}/${layout}: recorded receive`);
        assert.ok(!lane.blocks.some((item: any) => item.eventId === "pop"));
        assert.equal(annotation.fromEventId, "send");
        assert.equal(annotation.toEventId, "pop");
        assert.match(annotation.sourceTitle, /Send.*RT.*fb_com_data.*100 ns.*seq\.q1asm:2/);
        assert.match(annotation.targetTitle, /Receive.*Q1.*fb_pop_data.*500 ns.*seq\.q1asm:4/);
      }
    }
  });

  it("keeps Q1 feedback pops out of RT commands and projects reception inside an ongoing RT wait", () => {
    const ir={events:[feedbackEvent("send","feedback_com",100,180),
      {...feedbackEvent("wait","wait",180,1000),lane:"rt.control"},
      feedbackEvent("issue-pop","q1_issue",4,8,{op:"fb_pop_data"}),
      feedbackEvent("pop","feedback_pop",500,500,{q1_issue_event_id:"issue-pop"})],
      feedback_flows:[{id:"local",from_event_id:"send",to_event_id:"pop",channel:"4"}]};
    for(const mode of ["normal","debug"]) for(const layout of ["time","instruction"]) {
      const model=buildQ1TimelineSharedModel(ir,{mode,layout});
      const rt=model.lanes.find((lane:any)=>lane.kind==="runtime");
      assert.deepEqual(rt.blocks.map((block:any)=>block.eventId),["send","wait"]);
      assert.equal(model.annotations[0].toTime,layout==="time" ? 500 : 1+(500-180)/820);
      const q1=model.lanes.find((lane:any)=>lane.kind==="q1-issue");
      assert.equal(q1.hidden,mode!=="debug");
      assert.equal(q1.blocks[0].commandToken,"fb_pop_data");
      assert.ok(!q1.blocks[0].issueMarker,"A Q1 pop does not enqueue an RT command");
    }
    assert.deepEqual(buildQ1TimelineSelectionIndex(ir).get("issue-pop").rtEventIds,[]);
  });

  it("keeps acquisition feedback at the end of integration rather than its RT wait", () => {
    const model = buildQ1TimelineSharedModel({ events: [
      { ...feedbackEvent("acq", "acquire", 100, 104), lane: "rt.acquire", source: {file:"seq.q1asm",line:2,raw:"acquire 0,0,4"}, meta: { acquisition_window: {
        start_ns: 100, end_ns: 1100, configured_duration_ns: 1000,
        effective_duration_ns: 1000, source: "hardware_parameters", status: "completed",
      } } },
      feedbackEvent("pop", "feedback_pop", 1300, 1300),
    ], feedback_flows: [{ id: "acquisition", from_event_id: "acq", to_event_id: "pop", channel: "4" }] });
    assert.equal(model.annotations[0].fromTime, 1100);
    assert.equal(model.annotations[0].toTime, 1300);
    assert.match(model.annotations[0].sourceTitle, /acquire integration end.*1100 ns/);
  });

  it("keeps aligned RT clocks and distinguishes queue arrival from a Q1 pop", () => {
    const model = buildQ1TimelineSharedModel({events:[
      feedbackEvent("send", "feedback_com", 100, 180, {aligned_t0:1100,aligned_t1:1180}),
      {...feedbackEvent("arrival", "feedback_receive", 260, 260, {aligned_t0:1260,aligned_t1:1260}),source:undefined},
    ],feedback_flows:[{id:"arrival",from_event_id:"send",to_event_id:"arrival",channel:"4"}]}, {timeBasis:"aligned"});
    assert.equal(model.annotations[0].fromTime,1100);
    assert.equal(model.annotations[0].toTime,1260);
    assert.match(model.annotations[0].sourceTitle,/1100 ns \(aligned\)/);
    assert.match(model.annotations[0].targetTitle,/Receive \(queue\).*feedback queue arrival.*1260 ns/);
    assert.doesNotMatch(model.annotations[0].targetTitle,/fb_pop_data/);
  });

  it("does not present an unknown feedback receive time as its symbolic lower bound", () => {
    const model=buildQ1TimelineSharedModel({events:[feedbackEvent("send","feedback_com",100,180),
      {...feedbackEvent("pop","feedback_pop",500,500),t0:{kind:"symbolic",expr:"arrival + 4"}}],
      feedback_flows:[{id:"unknown",from_event_id:"send",to_event_id:"pop",channel:"4"}]},{layout:"instruction"});
    assert.match(model.annotations[0].targetTitle,/Receive \(Q1\).*time unknown/);
    assert.doesNotMatch(model.annotations[0].targetTitle,/4 ns/);
  });
});
