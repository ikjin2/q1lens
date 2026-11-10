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
    assert.ok(!hardware.snapshot(index, "seq0", 16).warnings.some((message: string) => message.includes("Collapsed loop")));
    assert.equal(index.execution.seq0.status, "blocked");
    ir.events = new Proxy([], { get() { throw new Error("Replay rescanned input events"); } });
    assert.equal(hardware.executionTrace(index, "seq0"), trace);
  });

  it("applies negative alignment offsets to instruction ends and write readiness", () => {
    const trace = hardware.executionTrace(hardware.createIndex(replayFixture()), "seq0", "aligned");
    assert.deepEqual(trace.instructions.map((item: any) => item.end), [-16, -12, -8]);
    assert.equal(trace.instructions[0].writes[0].time, -12);
  });

  it("keeps every executed playback occurrence available behind compact timeline loops", () => {
    const ir = replayFixture();
    ir.playback = {status: "blocked"};
    ir.execution.seq0.mode = "playback";
    ir.events.push(event("loop", "loop_block", 0, 12, 5, {playback: true, hidden_event_ids: ["second", "third"]}));
    const index = hardware.createIndex(ir);
    const trace = hardware.executionTrace(index, "seq0");
    assert.equal(trace.reason, null);
    assert.deepEqual(trace.instructions.map((item: any) => item.event.id), ["first", "second", "third"]);
    assert.equal(trace.instructions[2].writes[0].time, 16);
    const project = projectReplayFixture();
    for (const execution of Object.values(project.execution) as any[]) execution.mode = "playback";
    assert.ok(hardware.projectExecutionTrace(hardware.createIndex(project)).reason, "separate path execution must not imply a shared simulation clock");
  });

  it("identifies feedback assumptions without misreporting them as branch assumptions", () => {
    const ir = replayFixture();
    const first = ir.events.find((item: any) => item.id === "first");
    first.confidence = "assumed";
    first.meta.feedback_assumption = {value: null, delay_ns: 0, source: "default"};
    const second = ir.events.find((item: any) => item.id === "second");
    second.confidence = "assumed";
    second.meta.feedback_assumptions = ["receive:first"];
    const warnings = hardware.snapshot(hardware.createIndex(ir), "seq0", 16).warnings;
    assert.ok(warnings.includes("State depends on assumed feedback values or timing."));
    assert.ok(!warnings.some((message: string) => message.includes("branch assumptions")));
    second.meta.branch_assumptions = [{branch_id: "branch:1", assumed_branch_path: "fallthrough"}];
    assert.ok(hardware.snapshot(hardware.createIndex(ir), "seq0", 16).warnings.some((message: string) => message.includes("branch assumptions")));
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
  it("excludes removed comparison events from current state, cursor steps and replay", () => {
    const current = replayFixture();
    current.events.push(event("current-rt", "upd_param", 20, 24, 7, {
      rt_packet_id: "current", applied_state: { frequency: 120 },
    }));
    const ir = structuredClone(current);
    const removed = { diff_status: "removed" };
    ir.events.push(
      event("removed-issue", "q1_issue", 100, 104, 8, { ...removed, execution_index: 1 }),
      event("removed-acquire", "acquire", 200, 204, 9, {
        ...removed, rt_packet_id: "old", acquisition_index: 0, bin_index: 0, applied_state: { frequency: 700 },
      }),
      event("removed-play", "play", 204, 208, 10, { ...removed, rt_packet_id: "old-play" }),
      event("removed-region", "unknown_region", 208, null, 11, removed),
      { ...event("removed-sequencer", "q1_issue", 500, 504, 12, removed), sequencer_id: "deleted" },
    );
    const before = JSON.stringify(ir);
    const expected = hardware.createIndex(current);
    const index = hardware.createIndex(ir);
    assert.deepEqual(index.sequencers, expected.sequencers);
    assert.equal(index.eventsById.has("removed-acquire"), false);
    for (const basis of ["local", "aligned"]) {
      assert.deepEqual(hardware.stepTimes(index, "seq0", basis), hardware.stepTimes(expected, "seq0", basis));
      assert.deepEqual(hardware.snapshot(index, "seq0", 600, basis), hardware.snapshot(expected, "seq0", 600, basis));
      assert.deepEqual(hardware.executionTrace(index, "seq0", basis), hardware.executionTrace(expected, "seq0", basis));
    }
    assert.deepEqual(hardware.projectExecutionTrace(index), hardware.projectExecutionTrace(expected));
    assert.equal(JSON.stringify(ir), before);
  });

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

  it("keeps each latest digital acquisition outcome and only timestamps available data as ready", () => {
    const ir = fixture();
    ir.simulation = { seq0: { queue_capacity: 32 } };
    const result = (id: string, time: number, meta: any) => event(id, "acquisition_result", time, time, 3,
      { acquisition_index: 0, bin_index: 0, kind: "acquire_timetags", ...meta });
    ir.events.push(result("level", 100, { kind: "acquire_digital", status: "supplied", level: 0 }),
      result("unavailable", 104, { kind: "acquire_digital", status: "unavailable", reason: "No digital input" }),
      result("incomplete", 105, { status: "incomplete", count: 1, reason: "Gate stayed open" }),
      result("discarded", 106, { status: "discarded", count: 0, timetag_reason: "Missing edge" }),
      result("invalid", 107, { status: "invalid", count: 1, timetag_reason: "Missing reference" }),
      result("invalid-count", 108, { status: "supplied", count_valid: false, valid: false, classification_reason: "Invalid count" }),
      result("count", 109, { status: "modeled", count: 0, valid: false, timetag_ticks: null }),
      result("delta", 110, { status: "supplied", count_valid: false, valid: true, timetag_ticks: -129 }));
    const before = JSON.stringify(ir);
    const index = hardware.createIndex(ir);
    const outcomeAt = (time: number) => hardware.snapshot(index, "seq0", time - 20, "aligned").bins.records[0].result;

    assert.equal(outcomeAt(103).readyTime, 80);
    assert.equal(outcomeAt(103).digital.level, 0);
    for (const [time, id, status] of [[104, "unavailable", "unavailable"], [105, "incomplete", "incomplete"],
      [106, "discarded", "discarded"], [107, "invalid", "invalid"], [108, "invalid-count", "invalid"]] as const) {
      const outcome = outcomeAt(time);
      assert.equal(outcome.eventId, id);
      assert.equal(outcome.status, status);
      assert.equal(outcome.outcomeTime, time - 20);
      assert.equal(Object.hasOwn(outcome, "readyTime"), false);
    }
    assert.equal(outcomeAt(104).reason, "No digital input");
    assert.equal(outcomeAt(109).readyTime, 89);
    assert.equal(outcomeAt(109).digital.edgeCount, 0);
    assert.equal(outcomeAt(109).digital.timetagTicks, undefined);
    assert.equal(outcomeAt(110).readyTime, 90);
    assert.equal(outcomeAt(110).digital.edgeCount, undefined);
    assert.equal(outcomeAt(110).digital.timetagTicks, -129);
    assert.equal(JSON.stringify(ir), before);
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

  it("keeps compact loop uncertainty scoped to the latest applicable fields", () => {
    const ir = fixture();
    const loopA = event("loop-a", "loop_block", 116, 120, 10, { count: 3, loop_q1_issue_event_id: "q-loop-a",
      source_end: source(10), uncertain_registers: ["R0"], uncertain_latched_fields: ["frequency"] });
    ir.events.push(
      event("marker", "latched_state_pending", 118, 118, 9, { field: "marker", value: 7, q1_issue_event_id: "q-marker" }),
      event("q-marker", "q1_issue", 16, 20, 9, { op: "set_mrk" }),
      event("q-loop-a", "q1_issue", 24, 44, 10, { op: "loop" }), loopA,
      event("q-loop-b", "q1_issue", 48, 72, 11, { op: "loop" }),
      event("loop-b", "loop_block", 124, 128, 11, { count: 3, loop_q1_issue_event_id: "q-loop-b",
        source_end: source(11), uncertain_registers: ["R1"], uncertain_latched_fields: ["marker"] }),
      event("updated", "upd_param", 160, 164, 12, { rt_packet_id: "p6", applied_state: { frequency: 200 } }),
    );
    ir.register_history.push(write("R5", 12, 77, 4), write("R0", 76, 0, 4));
    const index = hardware.createIndex(ir);
    for (const basis of ["local", "aligned"]) {
      const time = (local: number) => local + (basis === "aligned" ? -20 : 0);
      const at = (local: number) => hardware.snapshot(index, "seq0", time(local), basis);
      const parameter = (local: number, field: string) => at(local).parameters.find((item: any) => item.field === field);
      assert.equal(at(43).registers[0].value.value, 1);
      assert.equal(at(44).registers[0].known, false);
      assert.equal(at(73).registers[0].known, false);
      assert.equal(at(76).registers[0].value.value, 0);
      assert.equal(hardware.registerAt(index, "seq0", "r0", time(73), basis), null);
      assert.equal(at(73).registers[5].value.value, 77);
      assert.equal(hardware.registerAt(index, "seq0", "r5", time(73), basis).value.value, 77);
      assert.equal(parameter(45, "marker").pending.value, 7);
      assert.equal(parameter(72, "marker").pending.known, false);
      assert.equal(parameter(119, "frequency").applied.value, 120);
      assert.equal(parameter(129, "frequency").applied.known, false);
      assert.equal(parameter(160, "frequency").applied.value, 200);
      assert.equal(at(129).rt.status, "unknown");
      assert.equal(at(129).bins.partial, true);
    }

    delete loopA.meta.uncertain_registers;
    delete loopA.meta.uncertain_latched_fields;
    const legacy = hardware.snapshot(hardware.createIndex(ir), "seq0", 45);
    assert.equal(legacy.registers[5].known, false);
    assert.equal(legacy.parameters.find((item: any) => item.field === "marker").pending.known, false);
  });

  it("keeps RT and bin state exact only for a proven Q1-only compact loop", () => {
    const ir = fixture();
    const body = event("body", "q1_issue", 0, 4, 3, { op: "nop", emits_rt_packet: false });
    const jump = event("jump", "q1_issue", 4, 20, 4, { op: "jmp", emits_rt_packet: false });
    const loop = event("loop", "loop_block", 0, 0, 3, { count: "forever", period: concrete(0), period_invariant: true,
      first_iteration_event_ids: [body.id], loop_q1_issue_event_id: jump.id, uncertain_registers: ["R0"] });
    ir.events = [body, jump, loop];
    ir.register_history = [write("R0", 8, 3, 1)];
    const at = (time: number) => hardware.snapshot(hardware.createIndex(ir), "seq0", time);
    assert.equal(at(19).q1.status, "active");
    assert.equal(at(19).rt.status, "not-started");
    assert.equal(at(20).q1.status, "unknown");
    assert.equal(at(20).registers[0].known, false);
    assert.equal(at(20).bins.partial, false);
    assert.ok(at(20).warnings.some((warning: string) => warning.includes("Q1 state")));

    ir.events.unshift(event("earlier", "acquire", -4, 0, 2, { rt_packet_id: "p0", acquisition_index: 0, bin_index: 0 }));
    assert.equal(at(19).rt.event.id, "earlier");
    assert.equal(at(19).bins.records[0].count, 1);
    assert.equal(at(19).bins.partial, false);

    delete body.meta.emits_rt_packet;
    assert.equal(at(19).rt.status, "unknown");
    assert.equal(at(19).bins.partial, true);
    body.meta.emits_rt_packet = true;
    body.meta.rt_packet_id = "p1";
    assert.equal(at(19).rt.status, "unknown");
    assert.equal(at(19).bins.partial, true);

    body.meta.emits_rt_packet = false;
    delete body.meta.rt_packet_id;
    ir.events.push(event("separate-risk", "unknown_region", 12, null, 5));
    assert.equal(at(19).rt.status, "unknown");
    assert.equal(at(19).bins.partial, true);
  });

  it("uses a separate Q1 boundary for each compact loop visit and ignores previews", () => {
    const ir = fixture();
    const loops = [event("first-loop", "loop_block", 0, 4, 4, { count: 2, loop_q1_issue_event_id: "first-edge",
      source_end: source(5), first_iteration_event_ids: ["first-body"] }),
    event("second-loop", "loop_block", 8, 12, 4, { count: 2, loop_q1_issue_event_id: "second-edge",
      source_end: source(5), first_iteration_event_ids: ["second-body"] })];
    ir.events = [
      event("first-body", "q1_issue", 12, 16, 4, { op: "wait" }),
      event("first-edge", "q1_issue", 16, 40, 5, { op: "loop" }),
      loops[0],
      event("preview-edge", "q1_issue", 40, 48, 5, { op: "loop", preview_source_event_id: "first-edge" }),
      event("first-next", "q1_issue", 48, 60, 6, { op: "sub" }),
      event("second-body", "q1_issue", 84, 88, 4, { op: "wait" }),
      event("second-edge", "q1_issue", 88, 112, 5, { op: "loop" }),
      loops[1],
      event("second-next", "q1_issue", 120, 132, 6, { op: "sub" }),
    ];
    for (const legacy of [false, true]) {
      if (legacy) for (const loop of loops) delete loop.meta.loop_q1_issue_event_id;
      const index = hardware.createIndex(ir);
      for (const basis of ["local", "aligned"]) {
        const status = (local: number) => hardware.summarize(index, "seq0", local + (basis === "aligned" ? -20 : 0), basis).q1.status;
        for (const [before, start, last, resume] of [[39, 40, 47, 48], [111, 112, 119, 120]]) {
          assert.equal(status(before), "active");
          assert.equal(status(start), "unknown");
          assert.equal(status(last), "unknown");
          assert.equal(status(resume), "active");
        }
      }
    }
    // The older unambiguous source lookup remains available without summary membership.
    ir.events = ir.events.filter((item: any) => !item.id.startsWith("second-"));
    delete loops[0].meta.first_iteration_event_ids;
    assert.equal(hardware.summarize(hardware.createIndex(ir), "seq0", 40).q1.status, "unknown");
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

  it("separates conditional RT timing from held output state and preserves known Q1 state", () => {
    for (const output_state_unknown of [false, true]) {
      const ir = fixture();
      const unknown = { kind: "unknown", display: "unknown(conditional_execution)" };
      ir.events.push(
        event("new-setting", "latched_state_pending", 124, 124, 9, { field: "frequency", value: 999 }),
        event("q9", "q1_issue", 24, 28, 9, { op: "set_freq" }),
        event("q10", "q1_issue", 28, 32, 10, { op: output_state_unknown ? "play" : "wait" }),
        { ...event("conditional", "unknown_region", 124, null, 10, {
          rt_packet_id: "conditional-packet", reason: "conditional_execution_unknown", conditional_time_unknown: true,
          output_state_unknown,
        }), confidence: "unknown", duration: unknown },
        event("q11", "q1_issue", 32, 36, 11, { op: "set_cond", conditional_enabled: false }),
        { ...event("disabled", "latched_state_pending", 124, 124, 11, {
          field: "set_cond", value: "0,0,0,4", conditional_enabled: false, conditional_time_unknown: true,
        }), t0: unknown, t1: unknown },
        { ...event("later-wait", "wait", 124, 128, 12, {
          rt_packet_id: "later-packet", conditional_time_unknown: true, output_state_unknown: false,
        }), t0: unknown, t1: unknown },
      );
      const before = JSON.stringify(ir);
      const index = hardware.createIndex(ir);
      for (const basis of ["local", "aligned"]) {
        const offset = basis === "aligned" ? -20 : 0;
        assert.equal(hardware.snapshot(index, "seq0", 123 + offset, basis).parameters.find((item: any) => item.field === "frequency").applied.value, 120);
        const state = hardware.snapshot(index, "seq0", 124 + offset, basis);
        const frequency = state.parameters.find((item: any) => item.field === "frequency");
        assert.equal(state.rt.status, "unknown");
        assert.notEqual(state.q1.status, "unknown");
        assert.equal(state.registers[0].value.value, 1);
        assert.equal(frequency.pending.value, 999);
        assert.equal(frequency.applied.known, !output_state_unknown);
        assert.equal(frequency.applied.value, output_state_unknown ? null : 120);
        assert.equal(state.awg[0].status === "unknown", output_state_unknown);
        assert.equal(state.bins.partial, output_state_unknown);
        assert.equal(state.bins.total, 2);
        assert.equal(hardware.eventTime(index, ir.events.at(-1), basis), undefined);
      }
      assert.equal(JSON.stringify(ir), before);
    }
  });

  it("does not invalidate Hardware state for disabled conditional execution", () => {
    const ir = fixture();
    ir.events.push(
      event("disabled", "latched_state_pending", 124, 124, 9, { field: "set_cond", value: "0,0,0,4", conditional_enabled: false }),
      event("q9", "q1_issue", 24, 28, 9, { op: "set_cond", conditional_enabled: false }),
      event("wait", "wait", 124, 65_659, 10, { rt_packet_id: "p5" }),
    );
    const state = hardware.snapshot(hardware.createIndex(ir), "seq0", 1000);
    assert.equal(state.rt.status, "active");
    assert.equal(state.rt.event.id, "wait");
    assert.equal(state.parameters.find((item: any) => item.field === "frequency").applied.value, 120);
    assert.equal(state.bins.partial, false);
    assert.deepEqual(state.warnings, []);
  });

  it("restores only proved active settings at the end of uncertain branch outputs", () => {
    const ir = fixture();
    const sources = {taken: source(11), fallthrough: source(13)};
    ir.events.push(event("branch", "unknown_region", 124, 140, 10, {
      output_state_unknown: true, branch_timing_known: true,
      latched_state_at_join: {frequency: 200, marker: 1, awg_gain: [2, 3]},
      latched_sources_at_join: {frequency: sources, marker: sources, awg_gain: sources},
      pending_fields_at_join: ["frequency"],
    }));
    const index = hardware.createIndex(ir);
    const during = hardware.snapshot(index, "seq0", 130);
    assert.equal(during.parameters.find((item: any) => item.field === "marker").applied.known, false);
    const after = hardware.snapshot(index, "seq0", 140);
    assert.equal(after.parameters.find((item: any) => item.field === "frequency").applied.known, false);
    const marker = after.parameters.find((item: any) => item.field === "marker").applied;
    assert.equal(marker.value, 1);
    assert.deepEqual(marker.branchJoinSources, sources);
    assert.deepEqual(after.parameters.find((item: any) => item.field === "awg_gain").applied.value, [2, 3]);
  });

  it("keeps known NCO settings while hiding uncertain physical phase until an applied reset", () => {
    const ir = fixture();
    ir.events.push(
      event("uncertain", "play", 124, 128, 10, {rt_packet_id: "uncertain", output_state_unknown: true,
        nco_phase_uncertain: true, applied_state: {frequency: 200, phase: 0}}),
      event("set-phase", "play", 128, 132, 11, {rt_packet_id: "set-phase", output_state_unknown: true,
        nco_phase_uncertain: true, applied_state: {phase: 50}}),
      event("reset", "play", 132, 136, 12, {rt_packet_id: "reset", phase_reset: true, applied_state: {phase: 0}}),
    );
    const index = hardware.createIndex(ir);
    for (const time of [124, 128]) {
      const parameters = hardware.snapshot(index, "seq0", time).parameters;
      assert.equal(parameters.find((item: any) => item.field === "frequency").applied.value, 200);
      assert.equal(parameters.find((item: any) => item.field === "phase").applied.known, false);
    }
    assert.equal(hardware.snapshot(index, "seq0", 132).parameters.find((item: any) => item.field === "phase").applied.value, 0);
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

  it("anchors unresolved branch Q1 risk to the recorded issue, independently of RT", () => {
    const ir = fixture();
    const region = event("unresolved", "branch_region", 8, null, 7, { q1_issue_event_id: "second-jz" });
    ir.events = [
      event("wait", "wait", 4, 8, 3, { rt_packet_id: "p0" }),
      event("first-jz", "q1_issue", 36, 52, 7, { op: "jz" }),
      event("body", "q1_issue", 52, 56, 3, { op: "wait" }),
      event("second-jz", "q1_issue", 80, 96, 7, { op: "jz" }),
      event("preview-jz", "q1_issue", 100, 116, 7, { op: "jz", preview_source_event_id: "second-jz" }),
      region,
    ];
    ir.register_history = [write("R5", 12, 77, 1)];
    const index = hardware.createIndex(ir);
    for (const basis of ["local", "aligned"]) {
      const at = (local: number) => hardware.snapshot(index, "seq0", local + (basis === "aligned" ? -20 : 0), basis);
      assert.equal(at(52).q1.event.id, "body");
      assert.equal(at(52).registers[5].value.value, 77);
      assert.equal(at(95).q1.status, "active");
      assert.equal(at(96).q1.status, "unknown");
      assert.equal(at(7).rt.status, "active");
      assert.equal(at(8).rt.status, "unknown");
    }

    region.t0 = { kind: "symbolic", expr: "DELAY", display: "DELAY" };
    const symbolic = hardware.createIndex(ir);
    assert.equal(hardware.summarize(symbolic, "seq0", 96).q1.status, "unknown");
    assert.equal(hardware.summarize(symbolic, "seq0", 96).rt.status, "last-command");

    region.t0 = concrete(8);
    delete region.meta.q1_issue_event_id;
    const ambiguous = hardware.createIndex(ir);
    assert.equal(hardware.summarize(ambiguous, "seq0", 52).q1.status, "active");
    assert.equal(hardware.summarize(ambiguous, "seq0", 96).q1.status, "last-command");
    assert.equal(hardware.summarize(ambiguous, "seq0", 8).rt.status, "unknown");
    ir.events = ir.events.filter((item: any) => item.id !== "first-jz");
    assert.equal(hardware.summarize(hardware.createIndex(ir), "seq0", 96).q1.status, "unknown");
  });

  it("uses an explicit Q1 proof cutoff without invalidating recorded register values", () => {
    const ir = fixture();
    const cutoff = event("cutoff", "unknown_region", 0, null, 7, { q1_issue_event_id: "outer", q1_proof_cutoff: true });
    ir.events = [event("outer", "q1_issue", 52, 76, 7, { op: "loop" }), cutoff];
    ir.register_history = [write("R0", 8, 2, 1), write("R0", 76, 1, 7, { event_id: "outer" })];
    const index = hardware.createIndex(ir);
    for (const basis of ["local", "aligned"]) {
      const time = (local: number) => local + (basis === "aligned" ? -20 : 0);
      const at = (local: number) => hardware.snapshot(index, "seq0", time(local), basis);
      assert.equal(at(75).q1.event.id, "outer");
      assert.equal(at(75).q1.status, "active");
      assert.equal(at(76).q1.status, "unknown");
      assert.equal(at(-1).rt.status, "not-started");
      assert.equal(at(0).rt.status, "unknown");
      assert.equal(at(76).registers[0].value.value, 1);
      assert.equal(hardware.registerAt(index, "seq0", "R0", time(76), basis).eventId, "outer");
      assert.equal(hardware.registerHistory(index, "seq0", "R0", time(76), basis).entries[1].value.value, 1);
    }

    cutoff.t0 = { kind: "symbolic", expr: "DELAY", display: "DELAY" };
    assert.equal(hardware.summarize(hardware.createIndex(ir), "seq0", 76).q1.status, "unknown");

    cutoff.t0 = concrete(0);
    delete cutoff.meta.q1_proof_cutoff;
    const unmarked = hardware.summarize(hardware.createIndex(ir), "seq0", 76);
    assert.equal(unmarked.q1.status, "last-command");
    assert.equal(unmarked.rt.status, "unknown");
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

  it("ends pending cores at the recorded observation edge while retaining committed history", () => {
    const ir = fixture();
    const terminal = { local_time_ns: 120, category: "feedback_queue_overflow", display_reason: "LINQ feedback queue overflow." };
    ir.execution = { seq0: { mode: "simulation", status: "error", reason: "Feedback queue overflow at 120 ns.", terminal } };
    ir.simulation = { seq0: { queue_capacity: 1, end_time_ns: 200 } };
    ir.alignment.seq0.offset = -100;
    ir.events = [event("stop", "q1_issue", 8, 12, 2, { op: "stop" }),
      event("prior", "wait", 100, 104, 3, { rt_packet_id: "p0" }),
      event("pending", "trigger_wait", 104, 120, 4, { pending: true })];
    const snapshot = (time: number, basis = "aligned") => hardware.snapshot(hardware.createIndex(ir), "seq0", time, basis);
    assert.equal(snapshot(19).rt.status, "waiting");
    for (const time of [20, 75]) {
      const state = snapshot(time);
      assert.equal(state.rt.status, "observation-ended");
      assert.equal(state.rt.event.id, "prior");
      assert.equal(state.q1.status, "stopped");
      assert.equal(state.registers[0].value.value, 1);
      assert.equal(state.simulation.endTime, 100);
      assert.equal(state.execution.reason, "Feedback queue overflow at 120 ns.");
      assert.deepEqual(state.terminal, { ...terminal, time: 20 });
    }
    assert.equal(snapshot(119, "local").rt.status, "waiting");
    assert.equal(snapshot(120, "local").terminal.time, 120);
    assert.deepEqual(hardware.summarize(hardware.createIndex(ir), "seq0", 75, "aligned").rt, snapshot(75).rt);

    ir.alignment.seq0 = { offset: "unknown", confidence: "unknown" };
    assert.equal(snapshot(75).terminal.time, undefined);
    assert.notEqual(snapshot(75).rt.status, "observation-ended");
    assert.equal(snapshot(120, "local").rt.status, "observation-ended");
    delete ir.execution.seq0.terminal;
    assert.equal(snapshot(200, "local").rt.status, "waiting");
    assert.equal(snapshot(200, "local").terminal, undefined);
  });

  it("distinguishes a clipped stop and an unresolved stall from completed Q1 state", () => {
    const ir = fixture();
    ir.execution = { seq0: { mode: "simulation", status: "step_limit", terminal: {
      local_time_ns: 12, category: "simulation_time_limit", display_reason: "Reached simulation time limit.",
    } } };
    ir.simulation = { seq0: { end_time_ns: 12 } };
    ir.events = [event("stop", "q1_issue", 8, 12, 2, { op: "stop", planned_q1_end_ns: 16, observation_cutoff_ns: 12 })];
    let index = hardware.createIndex(ir);
    assert.equal(hardware.snapshot(index, "seq0", 11).q1.status, "active");
    assert.equal(hardware.snapshot(index, "seq0", 12).q1.status, "observation-ended");
    assert.equal(hardware.snapshot(index, "seq0", 12).q1.event.id, "stop");
    delete ir.events[0].meta.planned_q1_end_ns;
    assert.equal(hardware.snapshot(hardware.createIndex(ir), "seq0", 12).q1.status, "stopped");

    ir.events = [event("stall", "q1_stall", 0, 12, 1, { pending: true, reason: "feedback_input" })];
    index = hardware.createIndex(ir);
    assert.equal(hardware.snapshot(index, "seq0", 11).q1.status, "stalled");
    assert.equal(hardware.snapshot(index, "seq0", 12).q1.status, "observation-ended");
    assert.equal(hardware.snapshot(index, "seq0", 12).simulation.stall, null);
    ir.events = [];
    ir.register_history = [];
    index = hardware.createIndex(ir);
    assert.deepEqual(hardware.stepTimes(index, "seq0"), [12]);
    assert.equal(hardware.snapshot(index, "seq0", 12).q1.event, null);
    ir.execution.seq0.mode = "concrete";
    assert.equal(hardware.snapshot(hardware.createIndex(ir), "seq0", 12).terminal, undefined);
  });

  it("carries accepted QTM bin snapshots past discards without leaking future or alternative results", () => {
    const ir = fixture();
    const result = (id: string, time: number, bin: number, meta: any) => event(id, "acquisition_result", time, time, 3,
      { kind: "acquire_timetags", acquisition_index: 0, bin_index: bin, ...meta });
    const first = { avg_cnt: 1, count: 1, count_sum: 1, timetag_ticks: 9, timetag_ticks_sum: 9, valid: true };
    const recordedZero = { avg_cnt: 2, count: 2, count_sum: 4, timetag_ticks: 4.5, timetag_ticks_sum: 9, valid: false };
    const unknown = { avg_cnt: 1, count: null, count_sum: null, timetag_ticks: null, timetag_ticks_sum: null, valid: false, count_valid: false };
    ir.events.push(result("first", 103, 0, { status: "modeled", count: 1, timetag_ticks: 9, bin_aggregate: first }),
      result("zero", 107, 0, { status: "supplied", count: 3, timetag_ticks: 0, valid: false, bin_aggregate: recordedZero }),
      result("discard", 110, 0, { status: "discarded" }), result("unknown", 111, 1, { status: "supplied", bin_aggregate: unknown }),
      result("alternative", 109, 0, { status: "modeled", branch_comparison_path: "taken", bin_aggregate: { ...first, avg_cnt: 100 } }));
    const index = hardware.createIndex(ir);
    const records = (time: number, basis = "local") => hardware.snapshot(index, "seq0", time, basis).bins.records;
    assert.equal(records(102)[0].aggregate, undefined);
    assert.deepEqual(records(103)[0].aggregate, first);
    assert.deepEqual(records(87, "aligned")[0].aggregate, recordedZero);
    assert.equal(records(107)[0].result.digital.edgeCount, 3);
    assert.equal(records(107)[0].result.digital.timetagTicks, undefined);
    assert.deepEqual(records(110)[0].aggregate, recordedZero);
    assert.equal(records(110)[0].result.status, "discarded");
    assert.deepEqual(records(111)[1].aggregate, unknown);
    assert.deepEqual(records(111)[0].history.map((item: any) => item.eventId), ["a1", "a2"]);
  });

  it("keeps the accepted I/Q aggregate when a later scenario or unavailable outcome arrives", () => {
    const ir = fixture();
    ir.simulation = { seq0: { queue_capacity: 32 } };
    const identity = { acquisition_index: 0, bin_index: 0 };
    const aggregate = { kind: "iq", count: 1, mean_i: 2, mean_q: -1, thresholded_count: 0, units: "normalized full scale * ns" };
    ir.events.push(
      event("modeled", "acquisition_result", 106, 106, 3, { ...identity, i: 2, q: -1, bin_aggregate: aggregate }),
      event("scenario", "acquisition_result", 110, 110, 4, { ...identity, i: 17, q: -9, source: { kind: "scenario" } }),
      event("unavailable", "acquisition_unavailable", 114, 114, 5, { ...identity, reason: "No input" }),
    );
    const index = hardware.createIndex(ir);
    const at = (time: number) => hardware.snapshot(index, "seq0", time, "aligned");
    assert.equal(at(85).bins.records[0].aggregate, undefined);
    for (const [time, id] of [[86, "modeled"], [90, "scenario"], [94, "unavailable"]] as const) {
      const bin = at(time).bins.records[0];
      assert.deepEqual(bin.aggregate, aggregate);
      assert.equal(bin.result.eventId, id);
    }
    const snapshot = at(94);
    assert.deepEqual(snapshot.bins.records[0].result, { ...identity, eventId: "unavailable", outcomeTime: 94, reason: "No input", status: "unavailable" });
    assert.equal(snapshot.simulation.communications.entries.at(-1).event.id, "unavailable");
  });

  it("keeps observed acquisition windows independent of digital samples and RT packet ends", () => {
    const ir = fixture();
    ir.simulation = { seq0: { queue_capacity: 32 } };
    const timing = { digital_gate_timing: "effective" };
    const identity = { acquisition_index: 0, bin_index: 0 };
    ir.events = [
      event("analog", "acquire", 100, 104, 1, { ...identity, op: "acquire", rt_packet_id: "p0",
        acquisition_window: { end_ns: 120, status: "completed" } }),
      event("gate", "acquire", 104, 108, 2, { ...identity, ...timing, op: "acquire_timetags",
        rt_packet_id: "p1", operand_snapshot: [0, 0, 1], digital_gate_window: {
          start_ticks: 104 * 128 + 1, end_ticks: 116 * 128 + 3, status: "closed",
        } }),
      event("sample-command", "acquire", 108, 112, 3, { ...identity, op: "acquire_digital", rt_packet_id: "p2" }),
      event("sample", "acquisition_result", 108, 108, 3, { ...identity, kind: "acquire_digital", level: 1, status: "modeled" }),
      event("close", "acquire", 116, 120, 4, { ...identity, ...timing, op: "acquire_timetags", rt_packet_id: "p3", operand_snapshot: [0, 0, 0] }),
      event("legacy", "acquire", 124, 128, 5, { ...identity, op: "acquire_ttl", rt_packet_id: "p4", operand_snapshot: [0, 0, 1] }),
    ];
    const index = hardware.createIndex(ir);
    const at = (time: number) => hardware.snapshot(index, "seq0", time, "aligned");
    assert.equal(at(84).acquisition.event.id, "analog");
    const opening = at(84 + 1 / 128);
    assert.equal(opening.acquisition.event.id, "gate");
    assert.equal(opening.acquisition.status, "active");
    assert.deepEqual(opening.acquisition.gate, { start: 84 + 1 / 128, end: 96 + 3 / 128, status: "closed" });
    assert.equal(at(89).acquisition.event.id, "gate");
    assert.equal(at(89).rt.event.id, "sample-command");
    assert.equal(at(96 + 2 / 128).acquisition.event.id, "gate");
    assert.equal(at(96 + 3 / 128).acquisition.event.id, "analog");
    assert.equal(at(96 + 3 / 128).rt.event.id, "close");
    assert.equal(at(101).acquisition.status, "last-command");
    assert.equal(at(105).acquisition.event.id, "legacy");
    assert.equal(at(105).acquisition.status, "active");
    assert.equal(at(105).acquisition.gate, undefined);
    assert.ok(hardware.stepTimes(index, "seq0", "aligned").includes(96 + 3 / 128));
  });

  it("separates explicit digital bin kinds and keeps analog and untyped identities", () => {
    const ir = fixture();
    const target = (id: string, op: string, time: number, meta = {}) => event(id, "acquire", time, time + 4, 3,
      { acquisition_index: 0, bin_index: 0, op, rt_packet_id: id, ...meta });
    const result = (id: string, kind: string, time: number, values: object) => event(id, "acquisition_result", time, time, 3,
      { acquisition_index: 0, bin_index: 0, kind, status: "modeled", ...values });
    ir.events = [target("analog", "acquire", 100), target("weighted", "acquire_weighed", 104),
      target("tag", "acquire_timetags", 108), target("level", "acquire_digital", 112),
      target("legacy-ttl", "acquire_ttl", 116), result("analog-result", "acquire", 104, { i: 1, q: 2 }),
      result("tag-result", "acquire_timetags", 112, { count: 1, timetag_ticks: 20 }),
      result("level-result", "acquire_digital", 112, { level: 0 }), result("ttl-result", "acquire_ttl", 120, { count: 3 })];
    const bins = hardware.snapshot(hardware.createIndex(ir), "seq0", 120).bins.records;
    assert.deepEqual(bins.map((bin: any) => [bin.key, bin.count, bin.result.eventId]), [
      ["0:0", 2, "analog-result"], ["0:0:acquire_timetags", 1, "tag-result"],
      ["0:0:acquire_digital", 1, "level-result"], ["0:0:acquire_ttl", 1, "ttl-result"],
    ]);
    assert.deepEqual(bins[0].history.map((entry: any) => entry.eventId), ["analog", "weighted"]);
    assert.equal(bins[3].gate, undefined);
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
