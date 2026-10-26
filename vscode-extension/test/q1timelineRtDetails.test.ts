import assert from "node:assert/strict";

const { buildQ1TimelineSharedModel: buildModel } = require("../src/q1timeline/media/timelineAdapter.js");
const value = (number: number): any => ({kind: "concrete", value: number, display: String(number)});
const unknown = {kind: "unknown", reason: "unresolved", display: "?"};

function event(id: string, kind: string, time: number, meta: any = {}, extra: any = {}): any {
  return {id, kind, sequencer_id: "Control", lane: kind === "q1_issue" ? "q1.issue" : `rt.${kind}`,
    t0: value(time), t1: value(time + 4), duration: value(4), confidence: "exact",
    source: {file: "Control.q1asm", line: 1, raw: kind}, meta, ...extra};
}

function command(id: string, kind: string, time: number, reads: any, snapshot: any, meta: any = {}, scope: any = {}): any[] {
  const lanePrefix = scope.branch_comparison_path ? `branch_compare.${scope.branch_comparison_path}.` : "";
  return [event(`${id}-issue`, "q1_issue", time, {...scope, rt_packet_id: id, op: kind,
    register_reads: reads, register_snapshot: snapshot}, {lane: `${lanePrefix}q1.issue`}),
  event(id, kind, time, {...scope, rt_packet_id: id, q1_issue_event_id: `${id}-issue`, ...meta}, {lane: `${lanePrefix}rt.${kind}`})];
}

function details(events: any[], options: any = {}, extra: any = {}): Map<string, any> {
  return new Map(buildModel({events, ...extra}, options).lanes.flatMap((lane: any) => lane.blocks)
    .filter((block: any) => block.rtDetails).map((block: any) => [block.eventId, block.rtDetails]));
}

describe("Q1 timeline RT block values", () => {
  it("shows only consumed registers and compares their values against the previous complete RT issue snapshot", () => {
    const events = [
      ...command("initial", "wait", 0, {R0: value(4)}, {R0: value(4), R1: value(7), R2: value(9)}),
      ...command("first-use", "play", 4, {R1: value(7), R2: value(11)}, {R0: value(4), R1: value(7), R2: value(11)}),
      ...command("retained", "play", 8, {R2: value(11)}, {R0: value(4), R1: value(7), R2: value(11)}),
    ];
    const before = JSON.stringify(events);
    const items = details(events);
    assert.deepEqual(items.get("first-use").registers, [
      {name: "R1", valueDisplay: "7", changed: false},
      {name: "R2", valueDisplay: "11", changed: true, previousDisplay: "9"},
    ]);
    assert.deepEqual(items.get("initial").registers, [{name: "R0", valueDisplay: "4", changed: false}]);
    assert.deepEqual(items.get("retained").registers, [{name: "R2", valueDisplay: "11", changed: false}]);
    assert.equal(JSON.stringify(events), before);
  });

  it("carries applied parameters across waits and skips while excluding pending settings and unchanged setters", () => {
    const events = [
      ...command("initial", "upd_param", 0, {}, {}, {applied_state: {frequency: 100, awg_gain: [1, 2], scope_enable: 1}}),
      event("pending", "latched_state_pending", 2, {field: "frequency", value: 200}, {lane: "state.pending"}),
      ...command("wait", "wait", 4, {}, {}, {applied_state: {frequency: 200}}),
      ...command("skip", "conditional_skip", 8, {}, {}, {conditional_skipped: true, applied_state: {frequency: 200}}),
      ...command("same", "play", 12, {}, {}, {applied_state: {frequency: 100, awg_gain: [1, 2]}}),
      ...command("changed", "play", 16, {}, {}, {applied_state: {awg_gain: [3, 2]}}),
      ...command("hold", "wait", 20, {}, {}),
    ];
    const items = details(events);
    for (const id of ["wait", "skip", "same"]) {
      assert.deepEqual(items.get(id).parameters, [
        {name: "frequency", valueDisplay: "100", changed: false},
        {name: "awg_gain", valueDisplay: "1, 2", changed: false},
      ]);
    }
    assert.deepEqual(items.get("changed").parameters, [
      {name: "frequency", valueDisplay: "100", changed: false},
      {name: "awg_gain", valueDisplay: "3, 2", changed: true, previousDisplay: "1, 2"},
    ]);
    assert.ok(items.get("hold").parameters.every((row: any) => !row.changed));
  });

  it("keeps the previous RT register snapshot across a Q1-only feedback boundary", () => {
    const events = [
      ...command("before", "wait", 0, {R0: value(4)}, {R0: value(4)}),
      event("feedback-issue", "q1_issue", 4, {op: "fb_pop_data", register_reads: {}, register_snapshot: {R0: value(8)}}),
      event("feedback", "feedback_pop", 4, {q1_issue_event_id: "feedback-issue"}, {lane: "rt.feedback"}),
      ...command("after", "wait", 4, {R0: value(8)}, {R0: value(8)}),
    ];
    assert.deepEqual(details(events).get("after").registers,
      [{name: "R0", valueDisplay: "8", changed: true, previousDisplay: "4"}]);
  });

  it("inherits the common prefix separately for Jump and Continue and never mixes sequencers or removed state", () => {
    const events = [...command("prefix", "play", 0, {R0: value(1)}, {R0: value(1), R1: value(5)},
      {applied_state: {frequency: 100, marker: 1}})];
    for (const [index, path] of ["taken", "fallthrough"].entries()) {
      const scope = {branch_comparison_branch_id: "branch", branch_comparison_path: path};
      events.push(...command(`${path}-first`, "play", 4, {R1: value(5 + index)}, {R1: value(5 + index)},
        {applied_state: {frequency: 100 + index}}, scope),
      ...command(`${path}-second`, "wait", 8, {R1: value(5 + index)}, {R1: value(5 + index)}, {}, scope));
    }
    events.push(...command("removed", "play", 6, {R1: value(999)}, {R1: value(999)}, {applied_state: {frequency: 999}}, {diff_status: "removed"}),
      ...command("other", "play", 4, {R1: value(8)}, {R1: value(8)}, {applied_state: {frequency: 300}})
        .map((item) => ({...item, sequencer_id: "Readout"})));
    const items = details(events);
    assert.deepEqual(items.get("taken-first").registers, [{name: "R1", valueDisplay: "5", changed: false}]);
    assert.deepEqual(items.get("fallthrough-first").registers, [{name: "R1", valueDisplay: "6", changed: true, previousDisplay: "5"}]);
    assert.deepEqual(items.get("taken-first").parameters, [
      {name: "frequency", valueDisplay: "100", changed: false}, {name: "marker", valueDisplay: "1", changed: false},
    ]);
    assert.equal(items.get("fallthrough-first").parameters[0].previousDisplay, "100");
    assert.equal(items.get("taken-second").parameters[0].valueDisplay, "100");
    assert.equal(items.get("fallthrough-second").parameters[0].valueDisplay, "101");
    assert.equal(items.get("other").registers[0].changed, false);
    assert.equal(items.get("removed").parameters[0].changed, false);
  });

  it("uses hidden executed loop iterations for the next baseline and preserves the result when expanded", () => {
    const events = [
      ...command("first", "play", 0, {R0: value(0)}, {R0: value(0)}, {applied_state: {frequency: 100}}),
      ...command("hidden", "play", 4, {R0: value(1)}, {R0: value(1)}, {applied_state: {frequency: 200}},
        {loop_preview: "loop", loop_iteration_index: 1}),
      ...command("after", "play", 8, {R0: value(1)}, {R0: value(1)}, {applied_state: {frequency: 200}}),
      event("loop", "loop_block", 0, {playback: true, loop_id: "loop", loop_preview_key: "loop", count: 2,
        first_iteration_event_ids: ["first", "first-issue"], hidden_event_ids: ["hidden", "hidden-issue"]}),
    ];
    const collapsed = details(events, {}, {playback: {status: "completed"}});
    const expanded = details(events, {expandedPlaybackLoops: ["loop"]}, {playback: {status: "completed"}});
    assert.equal(collapsed.has("hidden"), false);
    assert.equal(expanded.get("hidden").registers[0].changed, true);
    assert.deepEqual(collapsed.get("after"), expanded.get("after"));
    assert.ok(collapsed.get("after").registers.every((row: any) => !row.changed));
    assert.ok(collapsed.get("after").parameters.every((row: any) => !row.changed));
  });

  it("shares one operand snapshot for merged waveform paths without counting the packet twice", () => {
    const events = [...command("before", "wait", 0, {}, {R0: value(1)}),
      ...command("play", "play", 4, {R0: value(2)}, {R0: value(2)}, {applied_state: {frequency: 100}})];
    const original = events.pop();
    events.push({...original, id: "path0", lane: "rt.path0", meta: {...original.meta, path: 0}},
      {...original, id: "path1", lane: "rt.path1", meta: {...original.meta, path: 1}});
    const model = buildModel({events});
    const merged = model.lanes.flatMap((lane: any) => lane.blocks).find((block: any) => block.eventIds.includes("path0"));
    assert.deepEqual(merged.eventIds, ["path0", "path1"]);
    assert.deepEqual(merged.rtDetails.registers, [{name: "R0", valueDisplay: "2", changed: true, previousDisplay: "1"}]);
  });

  it("invalidates values changed by unexpanded compact-loop iterations before inspecting following RT blocks", () => {
    const events = [
      ...command("first", "play", 0, {R0: value(3)}, {R0: value(3)}, {applied_state: {frequency: 100, marker: 1}}),
      event("loop", "loop_block", 0, {count: 3, first_iteration_event_ids: ["first", "first-issue"],
        uncertain_latched_fields: ["frequency"], uncertain_registers: ["R0"]}),
      ...command("after", "wait", 12, {R0: value(0)}, {R0: value(0)}),
      ...command("restored", "play", 16, {}, {}, {applied_state: {frequency: 200}}),
    ];
    const items = details(events);
    assert.deepEqual(items.get("after").registers, [{name: "R0", valueDisplay: "0", changed: false}]);
    assert.deepEqual(items.get("after").parameters, [
      {name: "frequency", valueDisplay: "? (hidden loop iterations)", changed: false},
      {name: "marker", valueDisplay: "1", changed: false},
    ]);
    assert.deepEqual(items.get("restored").parameters[0], {name: "frequency", valueDisplay: "200", changed: false});
  });

  it("does not guess changes across unknown register values or unresolved output execution", () => {
    const events = [
      ...command("before", "play", 0, {}, {R0: unknown}, {applied_state: {frequency: 100}}),
      ...command("unknown", "unknown_region", 4, {R0: unknown}, {R0: unknown}, {output_state_unknown: true}),
      ...command("after", "play", 8, {R0: value(2)}, {R0: value(2)}, {applied_state: {marker: 1}}),
      ...command("restored", "play", 12, {}, {}, {applied_state: {frequency: 200}, applied_state_mode: "snapshot"}),
    ];
    const items = details(events);
    assert.equal(items.get("unknown").registers[0].changed, false);
    assert.deepEqual(items.get("unknown").parameters, []);
    assert.deepEqual(items.get("after").registers, [{name: "R0", valueDisplay: "2", changed: false}]);
    assert.deepEqual(items.get("after").parameters, [{name: "marker", valueDisplay: "1", changed: false}]);
    assert.match(items.get("after").parameterNote, /unknown/);
    assert.equal(items.get("restored").parameterNote, undefined);
  });

  it("keeps a phase increment on its applying packet and does not replay a retained simulation increment", () => {
    const events = [
      ...command("initial", "upd_param", 0, {}, {}, {applied_state: {phase: 100, frequency: 100}}),
      ...command("apply", "play", 4, {}, {}, {applied_state: {phase_delta: 50}, applied_updated_fields: ["phase_delta"]}),
      ...command("wait", "wait", 8, {}, {}),
      ...command("retained", "play", 12, {}, {}, {applied_state: {phase: 100, phase_delta: 50, frequency: 100}, applied_state_mode: "snapshot", applied_updated_fields: []}),
    ];
    const items = details(events);
    assert.deepEqual(items.get("apply").parameters, [
      {name: "phase", valueDisplay: "100", changed: false},
      {name: "frequency", valueDisplay: "100", changed: false},
      {name: "phase_delta", valueDisplay: "50", changed: false},
    ], "Absolute phase is the last programmed absolute set, separately from this command's increment");
    for (const id of ["wait", "retained"]) assert.deepEqual(items.get(id).parameters, [
      {name: "phase", valueDisplay: "100", changed: false}, {name: "frequency", valueDisplay: "100", changed: false},
    ]);
  });

  it("marks unresolved control flow as unknown while preserving held values at a blocked playback boundary", () => {
    const prefix = command("initial", "upd_param", 0, {}, {}, {applied_state: {frequency: 100}});
    const staticBoundary = event("jump", "unknown_region", 4, {reason: "register_branch_target", condition: "jmp", target: "R9"});
    const unresolved = details([...prefix, staticBoundary]).get("jump");
    assert.deepEqual(unresolved.parameters, []);
    assert.match(unresolved.parameterNote, /unknown/);
    const playbackBoundary = event("blocked", "unknown_region", 4, {playback: true, reason: "playback_boundary"});
    const blocked = details([...prefix, playbackBoundary]).get("blocked");
    assert.deepEqual(blocked.parameters, [{name: "frequency", valueDisplay: "100", changed: false}]);
    assert.match(blocked.parameterNote, /blocked.*last known applied/);
  });

  it("labels unavailable operand data and invalidates copied loop values that depend on an unknown register", () => {
    const scope = {loop_preview: "loop", loop_iteration_index: 1, preview_source_event_id: "original"};
    const events = [event("missing", "play", 0, {rt_packet_id: "missing"}),
      event("setter-issue", "q1_issue", 2, {...scope, register_reads: {R0: unknown}}),
      event("setter", "latched_state_pending", 2, {...scope, q1_issue_event_id: "setter-issue", field: "frequency", value: 100}, {lane: "state.pending"}),
      ...command("copy", "play", 4, {R0: unknown}, {R0: unknown},
        {applied_state: {frequency: 100}, applied_setter_event_ids: {frequency: "setter"}}, scope)];
    const items = details(events);
    assert.deepEqual(items.get("missing").registers, []);
    assert.match(items.get("missing").registerNote, /unavailable/);
    assert.deepEqual(items.get("copy").parameters, [{name: "frequency", valueDisplay: "? (loop iteration)", changed: false}]);
  });

  it("distinguishes unlinked legacy RT operands from a known empty register read set", () => {
    const items = details([event("old-wait", "wait", 0), event("old-play", "play", 4),
      ...command("immediate", "play", 8, {}, {})]);
    for (const id of ["old-wait", "old-play"]) {
      assert.deepEqual(items.get(id).registers, []);
      assert.match(items.get(id).registerNote, /unavailable/);
    }
    assert.deepEqual(items.get("immediate").registers, []);
    assert.equal(items.get("immediate").registerNote, undefined);
  });

  it("preserves known execution order under symbolic timing in either layout without stale model state", () => {
    const events = [
      ...command("before", "play", 0, {}, {R0: value(1)}, {applied_state: {frequency: 100}}),
      ...command("after", "play", 4, {R0: value(2)}, {R0: value(2)}, {applied_state: {frequency: 200}}),
    ];
    events[3].t0 = {kind: "symbolic", expr: "external_start"};
    const instruction = details(events, {layout: "instruction"});
    assert.equal(instruction.get("after").registers[0].previousDisplay, "1");
    assert.equal(instruction.get("after").parameters[0].previousDisplay, "100");
    assert.deepEqual(details(events), instruction);
    assert.deepEqual(details(events.slice(2), {layout: "instruction"}).get("after").registers,
      [{name: "R0", valueDisplay: "2", changed: false}], "A new path cannot retain the previous model's baseline");
  });
});
