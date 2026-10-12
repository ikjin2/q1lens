import assert from "node:assert/strict";

const hardware = require("../src/q1timeline/media/hardwareState.js");
const concrete = (value: number) => ({ kind: "concrete", value, display: String(value) });
const source = (line: number) => ({ file: "seq0.q1asm", line, column: 1 });
function event(id: string, kind: string, start: number, end: number | null, line: number, meta: any = {}, lane?: string): any {
  return { id, kind, sequencer_id: "seq0", t0: concrete(start), t1: end === null ? null : concrete(end), source: source(line), meta, lane: lane || (kind === "q1_issue" ? "debug.q1_issue" : `rt.${kind}`), confidence: "exact" };
}
function write(register: string, time: number, value: any, line: number, extra: any = {}): any {
  return { sequencer_id: "seq0", register, time: concrete(time), value: typeof value === "number" ? concrete(value) : value, source: source(line), event_id: `q${line}`, ...extra };
}
function fixture(): any {
  return {
    sequencers: [{ id: "seq0", label: "Readout", module: "qrm0" }],
    alignment: { seq0: { offset: -20, confidence: "exact" } },
    events: [
      event("q1", "q1_issue", 0, 4, 1, { op: "move" }),
      event("set", "latched_state_pending", 100, 100, 2, { field: "frequency", value: 120 }),
      event("q2", "q1_issue", 4, 8, 2, { op: "set_freq" }),
      event("q3", "q1_issue", 8, 12, 3, { op: "acquire", rt_packet_id: "p1" }),
      event("a1", "acquire", 100, 104, 3, { acquisition_index: 0, bin_index: 0, applied_state: { frequency: 120 }, rt_packet_id: "p1" }),
      event("q4", "q1_issue", 12, 16, 4, { op: "move" }),
      event("a2", "acquire", 104, 108, 5, { acquisition_index: 0, bin_index: 0, rt_packet_id: "p2" }),
      event("a3", "acquire", 108, 112, 6, { acquisition_index: 0, bin_index: 1, rt_packet_id: "p3" }),
      event("play0", "play", 112, 116, 7, { waveform_index: 4, path: 0, rt_packet_id: "p4" }, "rt.path0"),
      event("play1", "play", 112, 116, 7, { waveform_index: 7, path: 1, rt_packet_id: "p4" }, "rt.path1"),
      event("stop", "stop", 120, 120, 8),
    ],
    register_history: [write("R0", 8, 0, 1), write("R0", 20, 1, 4)],
  };
}

function replayFixture(): any {
  return {
    sequencers: [{ id: "seq0" }],
    execution: { seq0: { mode: "concrete", status: "blocked", reason: "Missing input" } },
    alignment: { seq0: { offset: -20, confidence: "exact" } },
    events: [
      event("third", "q1_issue", 8, 12, 5, { execution_index: 2, source_pc: 4 }),
      event("preview", "q1_issue", 0, 4, 99, { execution_index: 0, preview_source_event_id: "first" }),
      event("first", "q1_issue", 0, 4, 5, { execution_index: 0, source_pc: 4 }),
      event("second", "q1_issue", 4, 8, 6, { execution_index: 1, source_pc: 5 }),
    ],
    register_history: [
      write("R0", 8, 1, 5, { event_id: "first" }),
      write("R0", 16, 2, 5, { event_id: "third" }),
    ],
  };
}

function projectReplayFixture(): any {
  const events: any[] = [];
  const register_history: any[] = [];
  for (const [sequencerId, arrival, value] of [["early", 208, 1], ["late", 228, 2]] as const) {
    const add = (id: string, kind: string, start: number, end: number, line: number, meta: any = {}) => {
      events.push({ ...event(`${sequencerId}:${id}`, kind, start, end, line, meta), sequencer_id: sequencerId,
        source: { file: `${sequencerId}.q1asm`, line, column: 1 } });
    };
    for (const [execution_index, op, start, end] of [
      [0, "move", 0, 4], [1, "jmp", 4, 20], [2, "wait", 20, 24],
      [3, "wait_sync", 24, 28], [4, "wait", 28, 32], [5, "stop", 32, 36],
    ] as const) add(`q${execution_index}`, "q1_issue", start, end, execution_index + 1, { execution_index, op });
    add("initial-wait", "wait", 200, arrival, 3, { rt_packet_id: `${sequencerId}:p0` });
    add("barrier", "sync_wait", arrival, 228, 4);
    add("wait-sync", "wait_sync", arrival, 232, 4, { rt_packet_id: `${sequencerId}:p1` });
    add("resumed", "wait", 232, 240, 5, { rt_packet_id: `${sequencerId}:p2` });
    add("stop", "stop", 240, 240, 6);
    register_history.push(write("R0", 8, value, 1, { sequencer_id: sequencerId,
      event_id: `${sequencerId}:q0`, source: { file: `${sequencerId}.q1asm`, line: 1, column: 1 } }));
  }
  return {
    sequencers: [{ id: "early" }, { id: "late" }], events, register_history,
    execution: Object.fromEntries(["early", "late"].map((id) => [id, { mode: "simulation", timing_model: "event_driven", status: "completed" }])),
    alignment: { early: { offset: -208, confidence: "exact" }, late: { offset: -228, confidence: "exact" } },
    simulation: { early: { queue_capacity: 32, end_time_ns: 240 }, late: { queue_capacity: 32, end_time_ns: 240 } },
  };
}

describe("instruction replay trace", () => {
  it("keeps repeated source occurrences in execution order and retains delayed writes", () => {
    const ir = replayFixture();
    const index = hardware.createIndex(ir);
    const trace = hardware.executionTrace(index, "seq0");
    assert.equal(trace.reason, null);
    assert.deepEqual(trace.instructions.map((item: any) => item.event.id), ["first", "second", "third"]);
    assert.deepEqual(trace.sourceLines.map((item: any) => item.line), [5, 6]);
    const write = trace.instructions[0].writes[0];
    assert.equal(write.register, "R0");
    assert.equal(write.time, 8);
    assert.equal(write, hardware.registerWrite(index, "seq0", "R0", "first"));
    assert.equal(hardware.registerAt(index, "seq0", "R0", 7), null);
    assert.equal(hardware.registerAt(index, "seq0", "R0", 8).value.value, 1);
    assert.equal(trace.instructions[2].writes[0].time, 16);
    assert.equal(index.execution.seq0.status, "blocked");
    ir.events = new Proxy([], { get() { throw new Error("Replay rescanned input events"); } });
    assert.equal(hardware.executionTrace(index, "seq0"), trace);
  });

  it("applies negative alignment offsets to instruction ends and write readiness", () => {
    const trace = hardware.executionTrace(hardware.createIndex(replayFixture()), "seq0", "aligned");
    assert.deepEqual(trace.instructions.map((item: any) => item.end), [-16, -12, -8]);
    assert.equal(trace.instructions[0].writes[0].time, -12);
  });

  it("does not invent replay from static, ambiguous, or untimed traces", () => {
    const mutations = [
      (ir: any) => { ir.execution.seq0.mode = "analysis"; },
      (ir: any) => { delete ir.register_history; },
      (ir: any) => { ir.events.find((item: any) => item.id === "second").meta.execution_index = 0; },
      (ir: any) => { ir.events.find((item: any) => item.id === "third").t1 = null; },
      (ir: any) => { ir.events.find((item: any) => item.id === "third").t1 = concrete(6); },
    ];
    for (const mutate of mutations) {
      const ir = replayFixture();
      mutate(ir);
      const trace = hardware.executionTrace(hardware.createIndex(ir), "seq0");
      assert.equal(trace.instructions.length, 0);
      assert.ok(trace.reason);
    }
    const ir = replayFixture();
    ir.alignment.seq0.confidence = "unknown";
    const index = hardware.createIndex(ir);
    assert.equal(hardware.executionTrace(index, "seq0").instructions.length, 3);
    assert.match(hardware.executionTrace(index, "seq0", "aligned").reason, /Local time/);
    ir.register_history = [];
    assert.equal(hardware.executionTrace(hardware.createIndex(ir), "seq0").instructions.length, 3);
  });
});

describe("project execution replay trace", () => {
  it("merges shared Local frames through asymmetric sync waits and RT completion", () => {
    const index = hardware.createIndex(projectReplayFixture());
    const replay = hardware.projectExecutionTrace(index);
    assert.equal(replay.reason, null);
    assert.deepEqual(replay.traces.map((item: any) => item.sequencerId), ["early", "late"]);
    assert.deepEqual(replay.frames, [0, 4, 8, 20, 24, 28, 32, 36, 200, 208, 228, 232, 240]);
    assert.equal(replay.traces[0].trace, hardware.executionTrace(index, "early", "local"));
    assert.equal(replay.traces[0].trace.instructions[3].time, 24);
    assert.equal(hardware.executionTrace(index, "late", "aligned").instructions[3].time, -204);
    assert.equal(replay.traces[0].trace.instructions[0].writes[0].time, 8);
    assert.equal(hardware.registerAt(index, "early", "R0", 7), null);
    assert.equal(hardware.registerAt(index, "early", "R0", 8).value.value, 1);
    assert.equal(hardware.registerAt(index, "late", "R0", 8).value.value, 2);
    assert.equal(hardware.snapshot(index, "early", 208).rt.status, "waiting");
    assert.equal(hardware.snapshot(index, "late", 208).rt.status, "active");
    assert.equal(hardware.snapshot(index, "early", 228).rt.event.kind, "wait_sync");
    assert.equal(hardware.snapshot(index, "late", 228).rt.event.kind, "wait_sync");
    assert.equal(hardware.snapshot(index, "early", 232).rt.event.id, "early:resumed");
    assert.equal(hardware.snapshot(index, "late", 232).rt.event.id, "late:resumed");
    assert.equal(hardware.snapshot(index, "early", 36).q1.status, "stopped");
    assert.equal(hardware.snapshot(index, "early", 240).rt.status, "stopped");
    assert.equal(hardware.snapshot(index, "late", 240).rt.status, "stopped");
    index.sequencers = new Proxy([], { get() { throw new Error("Project replay rescanned sequencers"); } });
    assert.equal(hardware.projectExecutionTrace(index), replay);
  });

  it("requires shared event-driven simulation instead of synchronizing separate concrete traces", () => {
    for (const mutate of [
      (ir: any) => { ir.execution.early.mode = ir.execution.late.mode = "concrete"; },
      (ir: any) => { ir.execution.late.mode = "concrete"; },
      (ir: any) => { delete ir.execution.late; },
      (ir: any) => { delete ir.execution.late.mode; },
      (ir: any) => { delete ir.execution.late.timing_model; },
    ]) {
      const ir = projectReplayFixture();
      mutate(ir);
      const replay = hardware.projectExecutionTrace(hardware.createIndex(ir));
      assert.deepEqual(replay.frames, []);
      assert.equal(replay.traces.length, 2);
      assert.match(replay.reason, /Generate a project simulation trace/);
    }
  });

  it("retains blocked prefixes without inventing a release and identifies missing instructions", () => {
    const ir = projectReplayFixture();
    ir.execution.early = { ...ir.execution.early, status: "blocked", reason: "Missing sync participant" };
    ir.events = ir.events.filter((item: any) => item.sequencer_id !== "early" || item.kind === "q1_issue" || item.id === "early:initial-wait");
    ir.events.push({ ...event("early:pending-sync", "sync_wait", 208, 240, 4, { pending: true }), sequencer_id: "early" });
    const index = hardware.createIndex(ir);
    const replay = hardware.projectExecutionTrace(index);
    assert.equal(replay.reason, null);
    assert.equal(index.execution.early.status, "blocked");
    assert.ok(replay.frames.includes(208));
    assert.equal(hardware.snapshot(index, "early", replay.frames[replay.frames.length - 1]).rt.status, "waiting");
    ir.events = ir.events.filter((item: any) => item.sequencer_id !== "late" || item.kind !== "q1_issue");
    const missing = hardware.projectExecutionTrace(hardware.createIndex(ir));
    assert.deepEqual(missing.frames, []);
    assert.match(missing.reason, /late: No recorded instructions/);
    assert.equal(missing.traces[0].trace.instructions.length, 6);
    assert.equal(missing.traces[1].trace.instructions.length, 0);
  });

  it("rejects ambiguous instruction ordering while retaining single-sequencer concrete replay", () => {
    const ir = projectReplayFixture();
    ir.events.find((item: any) => item.id === "late:q2").meta.execution_index = 0;
    const replay = hardware.projectExecutionTrace(hardware.createIndex(ir));
    assert.deepEqual(replay.frames, []);
    assert.match(replay.reason, /late: Instruction order is incomplete or ambiguous/);
    const single = hardware.projectExecutionTrace(hardware.createIndex(replayFixture()));
    assert.equal(single.reason, null);
    assert.deepEqual(single.frames, [0, 4, 8, 12, 16]);
  });
});

describe("hardware state cursor model", () => {
  it("tracks simulated queue occupancy and stalls without treating telemetry as RT commands", () => {
    const ir = fixture();
    ir.simulation = { seq0: { queue_capacity: 2, startup_delay_ns: 100, end_time_ns: 120 } };
    ir.execution = { seq0: { mode: "simulation", timing_model: "event_driven", status: "completed" } };
    ir.events.push(
      event("enq1", "rt_queue_enqueue", 12, 12, 3, { depth: 1, capacity: 2, rt_packet_id: "p1" }),
      event("enq2", "rt_queue_enqueue", 16, 16, 4, { depth: 2, capacity: 2, rt_packet_id: "p2" }),
      event("stall", "q1_stall", 16, 100, 4, { reason: "queue_full" }),
      event("deq", "rt_queue_dequeue", 100, 100, 3, { depth: 1, capacity: 2, rt_packet_id: "p1" }),
    );
    const index = hardware.createIndex(ir);
    assert.equal(hardware.snapshot(index, "seq0", 11).simulation.queue.depth, 0);
    assert.equal(hardware.snapshot(index, "seq0", 12).simulation.queue.depth, 1);
    const stalled = hardware.snapshot(index, "seq0", 99);
    assert.equal(stalled.simulation.queue.depth, 2);
    assert.equal(stalled.q1.status, "stalled");
    assert.equal(stalled.simulation.stall.event.meta.reason, "queue_full");
    assert.equal(stalled.rt.status, "not-started");
    const resumed = hardware.snapshot(index, "seq0", 100);
    assert.equal(resumed.simulation.queue.depth, 1);
    assert.equal(resumed.simulation.stall, null);
    assert.equal(resumed.rt.event.id, "a1");
    assert.notEqual(resumed.q1.status, "stalled");
    assert.equal(resumed.execution.timing_model, "event_driven");
  });

  it("retains a newer pending setter when an earlier queued packet applies", () => {
    const ir = fixture();
    ir.events = [
      event("set1", "latched_state_pending", 8, 8, 1, { field: "frequency", value: 100, q1_issue_event_id: "q1" }),
      event("q1", "q1_issue", 0, 8, 1),
      event("set2", "latched_state_pending", 16, 16, 1, { field: "frequency", value: 200, q1_issue_event_id: "q2" }),
      event("q2", "q1_issue", 8, 16, 1),
      event("p1", "upd_param", 100, 104, 2, { rt_packet_id: "p1", applied_state: { frequency: 100 }, applied_setter_event_ids: { frequency: "set1" } }),
      event("p2", "upd_param", 104, 108, 2, { rt_packet_id: "p2", applied_state: { frequency: 200 }, applied_setter_event_ids: { frequency: "set2" } }),
    ];
    const index = hardware.createIndex(ir);
    const parameter = (time: number) => hardware.snapshot(index, "seq0", time).parameters.find((item: any) => item.field === "frequency");
    assert.equal(parameter(100).pending.value, 200);
    assert.equal(parameter(100).pending.issueEventId, "q2");
    assert.equal(parameter(100).applied.value, 100);
    assert.equal(parameter(100).applied.issueEventId, "q1");
    assert.equal(parameter(100).applied.setterEventId, "set1");
    assert.equal(parameter(100).applied.appliedAt, 100);
    assert.equal(parameter(100).applied.eventId, "p1");
    assert.equal(parameter(104).pending, null);
    assert.equal(parameter(104).applied.value, 200);
    assert.equal(parameter(104).applied.issueEventId, "q2");
  });

  it("keeps the original parameter application time when later RT packets retain its state", () => {
    const ir = fixture();
    ir.events.push(event("later", "play", 116, 120, 7, {
      rt_packet_id: "p5", applied_state: { frequency: 120 }, applied_setter_event_ids: { frequency: "set" },
    }));
    const applied = hardware.snapshot(hardware.createIndex(ir), "seq0", 116, "aligned").parameters.find((item: any) => item.field === "frequency").applied;
    assert.equal(applied.issueEventId, "q2");
    assert.equal(applied.setterEventId, "set");
    assert.equal(applied.appliedAt, 80);
    assert.equal(applied.time, 96);
    assert.equal(applied.eventId, "later");
  });

  it("finds the exact register writer at its readable time rather than its issue time", () => {
    const index = hardware.createIndex(fixture());
    const first = hardware.registerWrite(index, "seq0", "R0", "q1");
    const second = hardware.registerWrite(index, "seq0", "R0", "q4", "aligned");
    assert.equal(first.time, 8);
    assert.equal(first.event.t0.value, 0);
    assert.equal(first.value.value, 0);
    assert.equal(second.time, 0);
    assert.equal(second.value.value, 1);
    assert.equal(hardware.registerWrite(index, "seq0", "R1", "q1"), null);
    assert.equal(hardware.registerAt(index, "seq0", "R0", 19).eventId, "q1");
    assert.equal(hardware.registerAt(index, "seq0", "R0", 20).eventId, "q4");
    assert.equal(hardware.registerAt(index, "seq0", "R0", 7), null);

    const uncertain = fixture();
    uncertain.alignment.seq0 = { confidence: "unknown", unaligned: true };
    assert.equal(hardware.registerWrite(hardware.createIndex(uncertain), "seq0", "R0", "q1", "aligned"), null);
  });

  it("exposes only arrived inputs and completed acquisition results with provenance", () => {
    const ir = fixture();
    ir.simulation = { seq0: { queue_capacity: 32 } };
    const inputSource = { kind: "measured_input", label: "supplied ADC trace" };
    ir.events.push(
      event("feedback", "feedback_receive", 101, 101, 3, { id: 4, value: 8, source_event_id: "sender", input_index: 0 }),
      event("result", "acquisition_result", 106, 106, 3, { acquisition_index: 0, bin_index: 0, i: 0.5, q: -0.2, thresholded: true, source: inputSource, rt_packet_id: "p1" }),
    );
    const index = hardware.createIndex(ir);
    const before = hardware.snapshot(index, "seq0", 100);
    assert.equal(before.bins.records[0].result, "unknown");
    assert.equal(before.simulation.communications.count, 0);
    assert.equal(before.simulation.communications.next.event.id, "feedback");
    const arrived = hardware.snapshot(index, "seq0", 105);
    assert.equal(arrived.simulation.communications.entries[0].event.meta.source_event_id, "sender");
    assert.equal(arrived.bins.records[0].result, "unknown");
    const result = hardware.snapshot(index, "seq0", 106).bins.records[0].result;
    assert.equal(result.i, 0.5);
    assert.equal(result.q, -0.2);
    assert.equal(result.thresholded, true);
    assert.deepEqual(result.source, inputSource);
    assert.equal(result.readyTime, 106);
    assert.equal(result.eventId, "result");
  });

  it("aligns indexed physical samples with the cursor without holding values beyond trace bounds", () => {
    const ir = fixture();
    const trace = { stage: "adc", path: "i", t0: 100, dt_ns: 2, samples: [0.1, 0.2, 0.3], source: { kind: "supplied_trace", label: "input waveform" } };
    ir.simulation = { seq0: { queue_capacity: 32, signals: { traces: [trace] } } };
    const index = hardware.createIndex(ir);
    const local = hardware.signalAt(index, "seq0", "adc", 102);
    const aligned = hardware.signalAt(index, "seq0", "adc", 82, "aligned");
    assert.equal(local.values[0].value, 0.2);
    assert.equal(aligned.values[0].value, 0.2);
    assert.equal(aligned.values[0].sampleTime, 82);
    assert.equal(aligned.localTime, 102);
    assert.equal(hardware.signalAt(index, "seq0", "adc", 99).values[0].value, undefined);
    assert.equal(hardware.signalAt(index, "seq0", "adc", 106).values[0].value, undefined);
    assert.equal(hardware.signalAt(index, "seq0", "dac", 102), null);
    trace.samples.map = () => { throw new Error("must not rescan samples at cursor"); };
    for (let i = 0; i < 10; i++) assert.equal(hardware.signalAt(index, "seq0", "adc", 102).traces[0].segments, local.traces[0].segments);
    ir.alignment.seq0 = { unaligned: true, confidence: "unknown" };
    assert.equal(hardware.signalAt(index, "seq0", "adc", 82, "aligned").values[0].value, undefined);
    assert.equal(hardware.snapshot(index, "seq0", 82, "aligned").simulation.queue.depth, undefined);
  });

  it("keeps unavailable signal samples as gaps while preserving real zero samples", () => {
    const ir = fixture();
    const samples = [0.5, 0.5, null, undefined, NaN, Infinity, -Infinity, 0, 0];
    ir.simulation = { seq0: { signals: { traces: [
      { stage: "adc", path: "i", t0: 0, dt_ns: 1, samples },
      { stage: "adc", path: "q", t0: 0, dt_ns: 1, samples: Array(9).fill(0) },
    ] } } };
    const index = hardware.createIndex(ir);
    const signal = hardware.signalAt(index, "seq0", "adc", 7);
    assert.deepEqual(signal.traces[0].segments, ["0.00,10.00 66.67,10.00", "466.67,150.00 533.33,150.00"]);
    assert.equal(signal.traces[1].segments.length, 1);
    assert.equal(signal.traces[1].segments[0].split(" ").length, 9);
    assert.equal(signal.values[0].value, 0);
    for (let time = 2; time <= 6; time++) {
      assert.equal(hardware.signalAt(index, "seq0", "adc", time).values[0].value, undefined);
    }
  });

  it("bounds decimated signal samples by the exported sampling horizon in either time basis", () => {
    const ir = fixture();
    ir.simulation = { seq0: { signals: { sampled_end_ns: 9, traces: [
      { stage: "adc", path: "i", t0: 0, dt_ns: 4, samples: [0.1, 0.2, 0] },
    ] } } };
    const index = hardware.createIndex(ir);
    assert.equal(hardware.signalAt(index, "seq0", "adc", 8.99).values[0].value, 0);
    assert.equal(hardware.signalAt(index, "seq0", "adc", 8).end, 9);
    assert.equal(hardware.signalAt(index, "seq0", "adc", -11.01, "aligned").values[0].value, 0);
    for (const time of [9, 10, 12]) {
      assert.equal(hardware.signalAt(index, "seq0", "adc", time).values[0].value, undefined);
      assert.equal(hardware.signalAt(index, "seq0", "adc", time).values[0].sampleTime, undefined);
      assert.equal(hardware.signalAt(index, "seq0", "adc", time - 20, "aligned").values[0].value, undefined);
    }
  });

  it("reports simulated underflow as a runtime failure rather than an analytical estimate", () => {
    const ir = fixture();
    ir.events.push(event("underflow", "underflow_warning", 105, 105, 3, { simulated: true, reason: "RT queue empty" }));
    assert.ok(hardware.snapshot(hardware.createIndex(ir), "seq0", 105).warnings.includes("Simulation reports underflow: RT queue empty."));
  });

  it("keeps unresolved waits visible and distinguishes skipped RT packets from their captured settings", () => {
    const ir = fixture();
    ir.simulation = { seq0: { queue_capacity: 32 } };
    ir.events.push(
      event("pending", "q1_stall", 120, 120, 8, { reason: "feedback_input", pending: true }),
      event("skip", "conditional_skip", 124, 128, 9, { rt_packet_id: "p5", applied_state: {}, captured_state: { frequency: 999 }, conditional_skipped: true }),
    );
    const state = hardware.snapshot(hardware.createIndex(ir), "seq0", 126);
    assert.equal(state.q1.status, "stalled");
    assert.equal(state.simulation.stall.event.meta.pending, true);
    assert.equal(state.rt.event.kind, "conditional_skip");
    assert.equal(state.rt.status, "active");
    assert.equal(state.parameters.find((item: any) => item.field === "frequency").applied.value, 120);
  });

  it("separates Q1 register availability from captured RT acquisition operands", () => {
    const index = hardware.createIndex(fixture());
    assert.equal(hardware.snapshot(index, "seq0", 7).registers[0].known, false);
    assert.equal(hardware.snapshot(index, "seq0", 8).registers[0].value.value, 0);
    const state = hardware.snapshot(index, "seq0", 100);
    assert.equal(state.registers[0].value.value, 1);
    assert.equal(state.registers[0].source.line, 4);
    assert.equal(state.acquisition.event.meta.bin_index, 0);
    assert.equal(state.rt.status, "active");
    assert.equal(state.registers[63].display, "unknown");
    assert.equal(hardware.snapshot(index, "seq0", 20).registers[0].changed, true);
    assert.equal(hardware.snapshot(index, "seq0", 120).rt.status, "stopped");
  });

  it("uses Q1 setter completion for pending state and RT consumption for applied state", () => {
    const index = hardware.createIndex(fixture());
    const parameter = (time: number) => hardware.snapshot(index, "seq0", time).parameters.find((item: any) => item.field === "frequency");
    assert.equal(parameter(7).pending, null);
    assert.equal(parameter(8).pending.value, 120);
    assert.equal(parameter(8).pending.source.line, 2);
    assert.equal(parameter(99).applied, null);
    assert.equal(parameter(100).pending, null);
    assert.equal(parameter(100).applied.value, 120);
    assert.equal(parameter(100).applied.eventId, "a1");
  });

  it("preserves register changes, provenance and the exact instruction that produced them", () => {
    const ir = fixture();
    const provenance = { register: "R0", symbol: "$BIN", expression: "R0 + 1", value: concrete(1), event_id: "q4", execution_index: 3, inputs: { R0: { value: concrete(0), event_id: "q1" } }, steps: [{ source: source(4), expression: "0 + 1", value: concrete(1) }] };
    const meta = { previous_value: concrete(0), provenance, branch_assumptions: [{ branch_id: "branch1", assumed_branch_path: "taken" }] };
    ir.register_history[1].meta = meta;
    ir.register_history[1].confidence = "assumed";
    ir.events.find((entry: any) => entry.id === "q4").meta = { op: "add", execution_index: 3, register_reads: { R0: concrete(0) }, flags_before: null, flags_after: { zf: false, nf: false, cf: false, of: false } };
    const index = hardware.createIndex(ir);
    const register = hardware.snapshot(index, "seq0", 20).registers[0];
    assert.deepEqual(register.meta, meta);
    assert.equal(register.confidence, "assumed");
    assert.equal(register.event.meta.execution_index, 3);
    assert.equal(register.event.meta.register_reads.R0.value, 0);
    assert.equal(register.event.meta.flags_after.zf, false);
    assert.equal(register.meta.provenance.inputs.R0.value.value, 0);
    assert.equal(index.eventsById.get(register.meta.provenance.inputs.R0.event_id).source.line, 1);
    assert.equal(register.localTime, 20);
    assert.equal(register.time, 20);
    assert.equal(hardware.snapshot(index, "seq0", 0, "aligned").registers[0].time, 0);
    assert.equal(hardware.snapshot(index, "seq0", 0, "aligned").registers[0].localTime, 20);
  });

  it("navigates only the selected register's changes and preserves each execution occurrence", () => {
    const ir = fixture();
    ir.events.push(event("q4-repeat", "q1_issue", 24, 28, 4, { op: "add", execution_index: 6 }));
    ir.register_history.push(write("R0", 32, 2, 4, { event_id: "q4-repeat" }), write("R1", 36, 9, 4));
    const index = hardware.createIndex(ir);
    const before = hardware.registerHistory(index, "seq0", "R0", 0);
    assert.equal(before.previous, null);
    assert.equal(before.next.time, 8);
    assert.equal(before.count, 0);
    const exact = hardware.registerHistory(index, "seq0", "R0", 20);
    assert.equal(exact.previous.time, 8);
    assert.equal(exact.next.time, 32);
    const between = hardware.registerHistory(index, "seq0", "R0", 30);
    assert.equal(between.previous.time, 20);
    assert.equal(between.next.time, 32);
    const final = hardware.registerHistory(index, "seq0", "R0", 40);
    assert.equal(final.total, 3);
    assert.equal(final.next, null);
    assert.equal(final.entries[2].event.meta.execution_index, 6);
    assert.equal(final.entries[2].eventId, "q4-repeat");
    const aligned = hardware.registerHistory(index, "seq0", "R0", 0, "aligned");
    assert.equal(aligned.previous.time, -12);
    assert.equal(aligned.next.time, 12);
    assert.deepEqual(hardware.registerHistory(index, "seq0", "R63", 100).entries, []);
  });

  it("keeps unavailable write evidence without presenting its value as readable", () => {
    const ir = fixture();
    ir.register_history.push(write("R1", 30, 9, 4, {
      time: { kind: "unknown", reason: "trace ended before availability" },
      meta: { previous_value: concrete(8), provenance: { expression: "8 + 1" } },
    }));
    const register = hardware.snapshot(hardware.createIndex(ir), "seq0", 100).registers[1];
    assert.equal(register.known, false);
    assert.equal(register.confidence, "unknown");
    assert.equal(register.meta.previous_value.value, 8);
    assert.equal(register.meta.provenance.expression, "8 + 1");
    assert.equal(register.meta.invalidated, true);
    assert.match(register.meta.reason, /availability is unresolved/);
  });

  it("exposes concrete execution completion and blocking reasons without changing the captured RT operands", () => {
    for (const status of ["completed", "blocked", "step_limit", "error"]) {
      const ir = fixture();
      ir.execution = { seq0: { mode: "concrete", status, steps: 12, max_steps: 100, timing_model: "analytical", ...(status === "completed" ? {} : { reason: "Trace cannot continue" }) } };
      const state = hardware.snapshot(hardware.createIndex(ir), "seq0", 100);
      assert.deepEqual(state.execution, ir.execution.seq0);
      assert.equal(state.registers[0].value.value, 1);
      assert.equal(state.acquisition.event.meta.bin_index, 0);
    }
    assert.equal(hardware.snapshot(hardware.createIndex(fixture()), "seq0", 100).execution, null);
  });

  it("applies the same alignment offset to events, register availability and steps", () => {
    const index = hardware.createIndex(fixture());
    const local = hardware.snapshot(index, "seq0", 100);
    const aligned = hardware.snapshot(index, "seq0", 80, "aligned");
    assert.equal(aligned.rt.event.id, local.rt.event.id);
    assert.equal(aligned.registers[0].value.value, local.registers[0].value.value);
    assert.equal(aligned.bins.records[0].lastTime, 80);
    assert.equal(aligned.registers[0].time, 0);
    assert.ok(hardware.stepTimes(index, "seq0", "aligned").includes(-12));
    assert.equal(index.sequencers[0].module, "qrm0");
    assert.equal(hardware.eventTime(index, index.eventsById.get("a1"), "aligned"), 80);
  });

  it("counts represented acquire commands per target and paginates without inventing results", () => {
    const index = hardware.createIndex(fixture());
    const before = hardware.snapshot(index, "seq0", 103).bins;
    assert.equal(before.total, 1);
    assert.equal(before.records[0].count, 1);
    const bins = hardware.snapshot(index, "seq0", 110, "local", { limit: 1 }).bins;
    assert.equal(bins.total, 2);
    assert.equal(bins.records.length, 1);
    assert.equal(bins.records[0].count, 2);
    assert.equal(bins.records[0].result, "unknown");
    assert.equal(bins.records[0].avg_cnt, undefined);
    assert.deepEqual(bins.records[0].history.map((item: any) => item.eventId), ["a1", "a2"]);
    assert.equal(hardware.snapshot(index, "seq0", 110, "local", { offset: 1, limit: 1 }).bins.records[0].binIndex, 1);
    assert.equal(hardware.snapshot(index, "seq0", 118).awg[0].status, "last-command");
  });

  it("does not extrapolate loop previews or merge alternative branch paths", () => {
    const ir = fixture();
    ir.events.push(
      event("preview", "acquire", 130, 134, 3, { acquisition_index: 0, bin_index: 0, preview_source_event_id: "a1", loop_iteration_index: 1 }),
      event("alternative", "acquire", 130, 134, 4, { acquisition_index: 0, bin_index: 5, branch_comparison_path: "taken" }),
      event("loop", "loop_block", 100, 112, 3, { count: 10, first_iteration_event_ids: ["q3"], source_end: source(3) }),
    );
    ir.register_history.push(write("R0", 130, 999, 4, { meta: { branch_comparison_path: "taken" } }));
    const index = hardware.createIndex(ir);
    const state = hardware.snapshot(index, "seq0", 135);
    assert.equal(state.bins.total, 2);
    assert.equal(state.bins.records[0].count, 2);
    assert.equal(state.bins.partial, true);
    assert.notEqual(state.registers[0].value?.value, 999);
    assert.equal(state.parameters.find((item: any) => item.field === "frequency").applied.known, false);
    assert.ok(state.warnings.some((message: string) => message.includes("Collapsed loop")));
  });

  it("preserves symbolic register values and never treats symbolic times as concrete", () => {
    const ir = fixture();
    ir.register_history.push(write("R1", 16, { kind: "runtime_dependent", display: "runtime_dependent(feedback)" }, 4));
    const unknown = event("unknown", "wait", 200, null, 9, { rt_packet_id: "p9" });
    unknown.t0 = { kind: "symbolic", expr: "200 + trigger", display: "200 + trigger" };
    ir.events.push(unknown);
    const index = hardware.createIndex(ir);
    const state = hardware.snapshot(index, "seq0", 200);
    assert.equal(state.registers[1].known, false);
    assert.equal(state.registers[1].display, "runtime_dependent(feedback)");
    assert.equal(hardware.eventTime(index, unknown), undefined);
    assert.equal(state.rt.status, "unknown");
    assert.equal(state.parameters.find((item: any) => item.field === "frequency").applied.known, false);
    ir.alignment.seq0 = { offset: "trigger", confidence: "unknown" };
    const uncertain = hardware.snapshot(hardware.createIndex(ir), "seq0", 100, "aligned");
    assert.equal(uncertain.registers[0].known, false);
    assert.equal(uncertain.bins.total, 0);
    assert.ok(uncertain.warnings.some((message: string) => message.includes("Alignment") || message.includes("alignment")));
  });

  it("keeps known Q1 pending timing when the separate RT clock becomes symbolic", () => {
    const ir = fixture();
    const setter = event("set2", "latched_state_pending", 200, 200, 10, { field: "frequency", value: 400 });
    setter.t0 = { kind: "symbolic", expr: "trigger + 200" };
    setter.t1 = setter.t0;
    ir.events.push(setter, event("q10", "q1_issue", 20, 24, 10, { op: "set_freq" }));
    ir.events.push(event("underflow", "underflow_warning", 100, 100, 3, { status: "possible_underflow" }, "debug.underflow"));
    const state = hardware.snapshot(hardware.createIndex(ir), "seq0", 24);
    assert.equal(state.parameters.find((item: any) => item.field === "frequency").pending.value, 400);
    assert.ok(state.warnings.some((message: string) => message.includes("possible underflow")));
  });

  it("invalidates pre-branch applied parameters when comparing both branch paths", () => {
    const ir = fixture();
    ir.events.push(event("q9", "q1_issue", 124, 128, 9, { op: "jlt" }));
    ir.events.push(event("both", "branch_region", 124, 124, 9, { comparison_paths: ["taken", "fallthrough"] }));
    const state = hardware.snapshot(hardware.createIndex(ir), "seq0", 130);
    assert.equal(state.rt.status, "unknown");
    assert.equal(state.q1.status, "unknown");
    assert.equal(state.awg[0].status, "unknown");
    assert.equal(state.parameters.find((item: any) => item.field === "frequency").applied.known, false);
    assert.equal(state.bins.partial, true);
  });

  it("summarizes the same Q1 and RT state as detail for local, aligned and uncertain execution", () => {
    const straight = fixture();
    const loop = fixture();
    loop.events.push(event("loop", "loop_block", 100, 118, 3, { count: 10, first_iteration_event_ids: ["q3"], source_end: source(3) }));
    const branch = fixture();
    branch.events.push(event("q9", "q1_issue", 124, 128, 9, { op: "jlt" }));
    branch.events.push(event("both", "branch_region", 124, 124, 9, { comparison_paths: ["taken", "fallthrough"] }));
    const unknownAlignment = fixture();
    unknownAlignment.alignment.seq0 = { offset: "trigger", confidence: "unknown" };
    for (const ir of [straight, loop, branch, unknownAlignment]) {
      const index = hardware.createIndex(ir);
      for (const basis of ["local", "aligned"]) {
        for (const time of [-21, 0, 10, 20, 100, 119, 130]) {
          const summary = hardware.summarize(index, "seq0", time, basis);
          const detail = hardware.snapshot(index, "seq0", time, basis);
          assert.deepEqual(summary, { q1: detail.q1, rt: detail.rt, warnings: detail.warnings });
        }
      }
    }
    assert.equal(hardware.summarize(hardware.createIndex(loop), "seq0", 119).rt.status, "unknown");
    assert.equal(hardware.summarize(hardware.createIndex(branch), "seq0", 130).q1.status, "unknown");
  });

  it("summarizes without reading register, bin or source event collections", () => {
    const ir = fixture();
    const index = hardware.createIndex(ir);
    const expected = hardware.summarize(index, "seq0", 100);
    const unavailable = () => { throw new Error("Overview accessed a detail collection"); };
    for (const property of ["registers", "pending", "applied", "awg", "acquisitions", "bins", "binOrder"]) {
      Object.defineProperty(index.bySequencer.get("seq0").local, property, { get: unavailable });
    }
    Object.defineProperty(index, "eventsById", { get: unavailable });
    Object.defineProperty(ir, "events", { get: unavailable });
    Object.defineProperty(ir, "register_history", { get: unavailable });
    assert.deepEqual(hardware.summarize(index, "seq0", 100), expected);
  });

  it("does not rescan events during cursor movement and bounds bin history output", () => {
    const ir = fixture();
    for (let number = 0; number < 20000; number += 1) {
      ir.events.push(event(`many${number}`, "acquire", 1000 + number * 4, 1004 + number * 4, 3, { acquisition_index: 0, bin_index: number % 200, rt_packet_id: `packet${number}` }));
      ir.register_history.push(write("R2", 1000 + number * 4, number, 3));
    }
    const index = hardware.createIndex(ir);
    ir.events = new Proxy([], { get() { throw new Error("Cursor rescanned the input events"); } });
    ir.register_history = new Proxy([], { get() { throw new Error("Cursor rescanned the input history"); } });
    const state = hardware.snapshot(index, "seq0", 90000, "local", { offset: 100, limit: 10 });
    assert.equal(state.bins.records.length, 10);
    assert.ok(state.bins.records.every((item: any) => item.history.length <= 8));
    assert.equal(state.registers[2].value.value, 19999);
    assert.equal(state.bins.total, 200);
    const history = hardware.registerHistory(index, "seq0", "R2", 90000);
    assert.equal(history.total, 20000);
    assert.equal(history.entries.length, 8);
    assert.equal(history.entries[7].value.value, 19999);
    assert.equal(history.next, null);
  });
});
