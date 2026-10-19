import assert from "node:assert/strict";

const { buildQ1TimelineSharedModel: buildModel } = require("../src/q1timeline/media/timelineAdapter.js");

function event(id: string, kind: string, start: number, end: number, meta: Record<string, unknown> = {}, extra: Record<string, unknown> = {}): any {
  return { id, kind, sequencer_id: "Control", lane: kind === "q1_issue" ? "q1.issue" : "rt.control",
    t0: {kind: "concrete", value: start}, t1: {kind: "concrete", value: end}, confidence: "exact",
    source: {file: "latched.q1asm", line: 3, raw: "set_freq 50"}, meta, ...extra };
}

function setter(id: string, start: number, value: number, meta: Record<string, unknown> = {}): any[] {
  return [event(id, "q1_issue", start, start + 4, {op: "set_freq", ...meta}),
    event(`${id}-pending`, "latched_state_pending", start, start, {field: "frequency", value,
      q1_issue_event_id: id, ...meta}, {lane: "state.pending"})];
}

function apply(id: string, start: number, setterId: string, value: number, meta: Record<string, unknown> = {}): any {
  return event(id, "upd_param", start, start + 4, {rt_packet_id: id, applied_state: {frequency: value},
    applied_setter_event_ids: {frequency: `${setterId}-pending`}, ...meta});
}

function blocks(ir: any, options: Record<string, unknown> = {}): any[] {
  return buildModel(ir, {expandedQ1IssueSequencers: ["Control", "Readout"], ...options}).lanes.flatMap((lane: any) => lane.blocks);
}

function block(items: any[], id: string): any {
  const found = items.find((item) => item.eventId === id || item.eventIds?.includes(id));
  assert.ok(found, `Missing block ${id}`);
  return found;
}

function changes(items: any[], id: string): any[] { return block(items, id).parameterChanges || []; }

describe("Q1 timeline latched parameter markers", () => {
  it("anchors pending at setter completion and applies only at the first exact RT execution", () => {
    const ir = {events: [event("initial", "upd_param", 0, 4, {applied_state: {frequency: 20}}),
      ...setter("setter", 5000, 50), apply("later", 10100, "setter", 50), apply("first", 10000, "setter", 50)]};
    const before = JSON.stringify(ir);
    const items = blocks(ir);
    const pending = changes(items, "setter");
    assert.equal(pending.length, 1);
    assert.deepEqual({field: pending[0].field, setterEventId: pending[0].setterEventId, setterIssueId: pending[0].setterIssueId,
      setterTime: pending[0].setterTime, applyTime: pending[0].applyTime, applyEventIds: pending[0].applyEventIds},
    {field: "frequency", setterEventId: "setter-pending", setterIssueId: "setter", setterTime: 5004,
      applyTime: 10000, applyEventIds: ["first"]});
    assert.equal(typeof pending[0].previousDisplay, "string");
    assert.match(pending[0].previousDisplay, /20/);
    assert.match(pending[0].valueDisplay, /50/);
    assert.deepEqual(changes(items, "first"), pending);
    assert.deepEqual(changes(items, "later"), [], "Retained state is not another application marker");
    assert.equal(JSON.stringify(ir), before);
  });

  it("keeps an older captured value separate from a newer pending setter and an overwritten value", () => {
    const ir = {events: [...setter("overwritten", 0, 5), ...setter("captured", 4, 100), ...setter("newer", 8, 200),
      apply("captured-rt", 100, "captured", 100), apply("newer-rt", 104, "newer", 200)]};
    const items = blocks(ir);
    const overwritten = changes(items, "overwritten");
    assert.equal(overwritten.length, 1);
    assert.deepEqual(overwritten[0].applyEventIds, []);
    assert.equal(overwritten[0].applyTime, undefined);
    assert.deepEqual(changes(items, "captured")[0].applyEventIds, ["captured-rt"]);
    assert.deepEqual(changes(items, "newer")[0].applyEventIds, ["newer-rt"]);
    assert.equal(changes(items, "captured-rt")[0].setterIssueId, "captured");
    assert.match(changes(items, "newer-rt")[0].previousDisplay, /100/,
      "The previous value comes from actual RT state, not the latest Q1 pending value");
  });

  it("does not count waits, skipped commands, or missing state fields as applications", () => {
    const reference = {applied_state: {frequency: 50}, applied_setter_event_ids: {frequency: "setter-pending"}};
    const ir = {events: [...setter("setter", 0, 50),
      ...["wait", "wait_sync", "wait_trigger", "conditional_skip"].map((kind, index) => event(kind, kind, 20 + index * 4, 24 + index * 4, reference)),
      event("skipped", "play", 40, 44, {...reference, conditional_skipped: true}),
      event("missing-field", "upd_param", 44, 48, {...reference, applied_state: {phase: 3}}),
      apply("actual", 100, "setter", 50)]};
    const items = blocks(ir);
    assert.deepEqual(changes(items, "setter")[0].applyEventIds, ["actual"]);
    for (const id of ["wait", "wait_sync", "wait_trigger", "conditional_skip", "skipped", "missing-field"]) {
      assert.deepEqual(changes(items, id), [], id);
    }
  });

  it("creates one application change for merged play paths while retaining both original target IDs", () => {
    const ir = {events: [...setter("setter", 0, 50), ...[1, 0].map((path) => ({
      ...apply(`path${path}`, 100, "setter", 50, {path, rt_packet_id: "play-packet"}),
      kind: "play", lane: `rt.path${path}`,
    }))]};
    const items = blocks(ir);
    const rt = block(items, "path0");
    assert.equal(block(items, "path1"), rt);
    assert.equal(rt.parameterChanges.length, 1);
    assert.deepEqual([...rt.parameterChanges[0].applyEventIds].sort(), ["path0", "path1"]);
    assert.deepEqual(changes(items, "setter"), rt.parameterChanges);
  });

  it("keeps Compare alternatives and sequencers independent even when source and packet IDs are shared", () => {
    const events: any[] = [];
    for (const [index, path] of ["taken", "fallthrough"].entries()) {
      const scope = {branch_comparison_branch_id: "branch", branch_comparison_path: path};
      const id = `${path}-setter`;
      events.push(event(`${path}-initial`, "upd_param", 0, 4, {...scope, applied_state: {frequency: 10 + index}},
        {lane: `branch_compare.${path}.rt.control`}),
      ...setter(id, 4, 100 + index, scope).map((item) => ({...item,
        lane: item.kind === "q1_issue" ? `branch_compare.${path}.q1.issue` : "state.pending"})),
      {...apply(`${path}-rt`, 100, id, 100 + index, {...scope, rt_packet_id: "same-packet"}), lane: `branch_compare.${path}.rt.control`});
    }
    events.push(...setter("other-setter", 4, 300).map((item) => ({...item, sequencer_id: "Readout"})),
      {...apply("other-rt", 100, "other-setter", 300, {rt_packet_id: "same-packet"}), sequencer_id: "Readout"});
    const items = blocks({events});
    for (const [index, path] of ["taken", "fallthrough"].entries()) {
      const change = changes(items, `${path}-setter`)[0];
      assert.deepEqual(change.applyEventIds, [`${path}-rt`]);
      assert.match(change.previousDisplay, new RegExp(String(10 + index)));
    }
    assert.deepEqual(changes(items, "other-setter")[0].applyEventIds, ["other-rt"]);
  });

  it("uses remapped loop occurrence identities instead of shared source or packet identities", () => {
    const events: any[] = [];
    for (const iteration of [1, 2]) {
      const id = `setter-${iteration}`;
      const scope = {loop_preview: "loop", loop_iteration_index: iteration, preview_source_event_id: "original"};
      events.push(...setter(id, iteration * 4, iteration * 100, scope),
        event(`issue-${iteration}`, "q1_issue", iteration * 4 + 4, iteration * 4 + 8,
          {...scope, op: "upd_param", rt_packet_id: "same-packet"}),
        apply(`rt-${iteration}`, 100 + iteration * 4, id, iteration * 100,
          {...scope, rt_packet_id: "same-packet", q1_issue_event_id: `issue-${iteration}`}));
    }
    const items = blocks({events});
    for (const iteration of [1, 2]) {
      assert.deepEqual(changes(items, `setter-${iteration}`)[0].applyEventIds, [`rt-${iteration}`]);
      assert.equal(changes(items, `rt-${iteration}`)[0].setterIssueId, `setter-${iteration}`);
    }
  });

  it("retains applied state from hidden loop iterations when showing a later value change", () => {
    const ir = {playback: {status: "completed"}, events: [
      ...setter("first", 0, 100), apply("first-rt", 100, "first", 100),
      ...setter("hidden", 4, 150), apply("hidden-rt", 150, "hidden", 150),
      ...setter("after", 160, 200), apply("after-rt", 200, "after", 200),
      event("loop", "loop_block", 0, 154, {playback: true, loop_id: "loop", loop_preview_key: "loop", count: 2,
        first_iteration_end: 104, first_iteration_event_ids: ["first", "first-pending", "first-rt"],
        hidden_event_ids: ["hidden", "hidden-pending", "hidden-rt"]}),
    ]};
    const before = JSON.stringify(ir);
    const collapsed = blocks(ir);
    const expanded = blocks(ir, {expandedPlaybackLoops: ["loop"]});
    assert.equal(collapsed.some((item) => item.eventId === "hidden-rt"), false);
    assert.ok(block(expanded, "hidden-rt"));
    const change = changes(collapsed, "after")[0];
    assert.equal(change.previousDisplay, "150");
    assert.equal(change.valueDisplay, "200");
    assert.deepEqual(changes(expanded, "after"), [change]);
    assert.deepEqual(changes(collapsed, "after-rt"), [change]);
    assert.equal(JSON.stringify(ir), before);
  });

  it("follows current applied state across diff statuses while keeping removed state separate", () => {
    const steps: [string, number, number, string][] = [
      ["first", 100, 100, "unchanged"], ["changed", 150, 104, "changed"],
      ["removed", 1000, 106, "removed"], ["following", 200, 108, "unchanged"],
      ["added", 250, 112, "added"], ["shifted", 300, 116, "shifted"], ["changed-again", 350, 120, "changed"],
    ];
    const ir = {events: steps.flatMap(([id, value, time, status], index) => [
      ...setter(id, index * 4, value, {diff_status: status}),
      apply(`${id}-rt`, time, id, value, {diff_status: status}),
    ])};
    const before = JSON.stringify(ir);
    const items = blocks(ir);
    for (const [id, previous] of [["changed", "100"], ["following", "150"], ["added", "200"],
      ["shifted", "250"], ["changed-again", "300"]]) {
      const change = changes(items, id)[0];
      assert.equal(change.previousDisplay, previous, id);
      assert.deepEqual(change.applyEventIds, [`${id}-rt`]);
      assert.deepEqual(changes(items, `${id}-rt`), [change]);
    }
    assert.equal(changes(items, "removed")[0].valueDisplay, "1000");
    assert.equal(JSON.stringify(ir), before);
  });

  it("keeps real completion/application times in either layout and time basis without mutating IR", () => {
    const ir = {events: [...setter("setter", 4, 50, {aligned_t0: 1004, aligned_t1: 1008}),
      apply("rt", 100, "setter", 50, {aligned_t0: 1100, aligned_t1: 1104})]};
    const before = JSON.stringify(ir);
    for (const layout of ["time", "instruction"]) {
      for (const timeBasis of ["local", "aligned"]) {
        const items = blocks(ir, {layout, timeBasis});
        const change = changes(items, "setter")[0];
        assert.equal(change.setterTime, timeBasis === "aligned" ? 1008 : 8);
        assert.equal(change.applyTime, timeBasis === "aligned" ? 1100 : 100);
        assert.deepEqual(change.applyEventIds, ["rt"]);
        assert.deepEqual(changes(items, "rt"), [change]);
      }
    }
    assert.equal(JSON.stringify(ir), before);
  });

  it("preserves unknown marker times instead of treating symbolic times as zero", () => {
    const events = [...setter("setter", 4, 50), apply("rt", 100, "setter", 50)];
    events[0].t1 = {kind: "symbolic", expr: "setter-end"};
    events[2].t0 = {kind: "symbolic", expr: "rt-start"};
    const items = blocks({events}, {layout: "instruction"});
    const change = changes(items, "setter")[0];
    assert.equal(change.setterTime, undefined);
    assert.equal(change.applyTime, undefined);
    assert.deepEqual(change.applyEventIds, ["rt"], "Identity remains known even when timing is not");
  });
});
