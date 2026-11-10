import assert from "node:assert/strict";

const {
  buildQ1TimelineSharedModel: buildModel,
  buildQ1TimelineSelectionIndex: buildIndex,
} = require("../src/q1timeline/media/timelineAdapter.js");

function event(id: string, kind: string, start: number, meta: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) {
  return {
    id, kind, sequencer_id: "Control", lane: kind === "q1_issue" ? "q1.issue" : `rt.${kind}`,
    t0: { kind: "concrete", value: start }, t1: { kind: "concrete", value: start + 4 },
    duration: { kind: "concrete", value: 4 }, confidence: "exact", label: kind,
    source: { file: "Control.q1asm", line: 3, column: 1, raw: "play 0,1,4" }, meta, ...extra,
  };
}

function blocks(model: any): any[] { return model.lanes.flatMap((lane: any) => lane.blocks); }
function sorted(ids: string[]): string[] { return [...ids].sort(); }

function assertTargets(events: ReturnType<typeof event>[], expected: Record<string, string[]>) {
  const ir = { events };
  const before = JSON.stringify(ir);
  const index = buildIndex(ir);
  const model = buildModel(ir, { expandedQ1IssueSequencers: ["Control", "Readout"] });
  for (const [id, targets] of Object.entries(expected)) {
    const entry = index.get(id);
    assert.ok(entry, `Missing selection entry ${id}`);
    assert.ok(Array.isArray(entry.rtEventIds), `Missing issued RT IDs for ${id}`);
    assert.deepEqual(sorted(entry.rtEventIds), sorted(targets), id);
    const block = blocks(model).find((item) => item.eventId === id);
    assert.ok(block, `Missing Q1 block ${id}`);
    assert.equal(Boolean(block.issueMarker), targets.length > 0, id);
    assert.deepEqual(sorted(block.rtEventIds || []), sorted(targets), id);
  }
  for (const item of events.filter((item) => item.kind !== "q1_issue")) {
    assert.deepEqual(index.get(item.id)?.rtEventIds || [], [], `${item.id} must not issue RT commands`);
  }
  assert.equal(JSON.stringify(ir), before, "Building marker metadata must not mutate the source IR");
  return { index, model };
}

describe("Q1 timeline issue markers", () => {
  it("marks a simulated trigger arrival with its address and displayed time", () => {
    const stop = { file: "Control.q1asm", line: 19, column: 1, raw: "stop" };
    const model = buildModel({ events: [
      event("trigger", "trigger_receive", 90, { address: 1, aligned_t0: 30, aligned_t1: 30 }, { source: stop }),
      event("end", "stop", 140, { aligned_t0: 80, aligned_t1: 80 }, { source: stop }),
    ] }, { layout: "time", timeBasis: "aligned" });
    const [trigger, end] = blocks(model);
    assert.equal(trigger.start, 30);
    assert.equal(trigger.pointMarker, true);
    assert.equal(trigger.title, "Trigger address 1 · 30 ns");
    assert.equal(end.title, "stop | 80 ns -> 80 ns");
  });

  it("groups only coincident arrivals in the same sequencer and comparison context", () => {
    const arrival = (id: string, address: number, time = 120, meta = {}, extra = {}) =>
      event(id, "trigger_receive", time, { address, aligned_t0: time - 100, aligned_t1: time - 100, ...meta }, extra);
    const comparison = { branch_comparison_kind: "rt_condition", branch_comparison_branch_id: "first" };
    const ir = { events: [
      event("wait", "wait", 100, { aligned_t0: 0, aligned_t1: 100 }, {
        t1: { kind: "concrete", value: 200 }, duration: { kind: "concrete", value: 100 },
      }),
      arrival("one", 1), arrival("two", 2), arrival("duplicate", 1), arrival("later", 3, 140),
      arrival("peer", 4, 120, {}, { sequencer_id: "Peer" }),
      arrival("taken", 5, 120, { ...comparison, branch_comparison_path: "taken" }),
      arrival("skipped", 6, 120, { ...comparison, branch_comparison_path: "fallthrough" }),
      arrival("other-taken", 7, 120, { ...comparison, branch_comparison_branch_id: "other", branch_comparison_path: "taken" }),
      event("stop", "stop", 200, { aligned_t0: 100, aligned_t1: 100 }),
    ] };
    const original = JSON.stringify(ir);
    for (const layout of ["time", "instruction"]) for (const timeBasis of ["aligned", "local"]) {
      const model = buildModel(ir, { layout, timeBasis });
      const points = blocks(model).filter((block) => block.kind === "trigger_receive");
      assert.deepEqual(sorted(points.map((block) => block.id)), ["later", "one", "other-taken", "peer", "skipped", "taken"]);
      const point = points.find((block) => block.id === "one");
      assert.deepEqual(point.eventIds, ["one", "two", "duplicate"]);
      assert.deepEqual(point.domain.eventIds, point.eventIds);
      assert.equal(point.duration, 0);
      assert.equal(point.start, layout === "instruction" ? 0.2 : timeBasis === "aligned" ? 20 : 120);
      assert.equal(point.title, `Trigger addresses 1 ×2, 2 · ${timeBasis === "aligned" ? 20 : 120} ns`);
      assert.equal(points.find((block) => block.id === "later").title, `Trigger address 3 · ${timeBasis === "aligned" ? 40 : 140} ns`);
      if (layout === "instruction") assert.equal(blocks(model).find((block) => block.id === "stop").start, 1);
    }
    assert.equal(JSON.stringify(ir), original);
  });

  it("defaults to the unique consumed arrival only in its own comparison context", () => {
    const arrivals = [event("one", "trigger_receive", 20, { address: 1 }), event("two", "trigger_receive", 20, { address: 2 })];
    for (const alternate of [false, true]) {
      const comparison = alternate ? { branch_comparison_kind: "rt_condition", branch_comparison_branch_id: "branch",
        branch_comparison_path: "taken" } : {};
      const consumer = event("consumer", "wait_trigger", 20, { input_event_ids: ["two"], ...comparison });
      const point = blocks(buildModel({ events: [...arrivals, consumer] })).find((block) => block.pointMarker);
      assert.deepEqual(point.eventIds, alternate ? ["one", "two"] : ["two", "one"]);
      assert.equal(point.title, "Trigger addresses 1, 2 · 20 ns");
    }
  });

  it("keeps arrival metadata out of Q1 issue targets and RT latched state", () => {
    const packet = { q1_issue_event_id: "issue", rt_packet_id: "packet" };
    const events = [
      event("issue", "q1_issue", 0, { op: "play", rt_packet_id: "packet" }),
      event("prior", "play", 4, { applied_state: { frequency: 10 } }),
      event("trigger", "trigger_receive", 8, { ...packet, address: 1, applied_state: { frequency: 20 } }),
      event("reject", "feedback_rejected", 12, { ...packet, id: 42, applied_state: { frequency: 30 } }),
      event("rt", "play", 16, { ...packet, applied_state: { frequency: 10 } }),
    ];
    const { model } = assertTargets(events, { issue: ["rt"] });
    const baseline = buildModel({ events: events.filter((item) => !["trigger", "reject"].includes(item.id)) });
    const find = (model: any, id: string) => blocks(model).find((item) => item.eventId === id);
    assert.deepEqual(find(model, "rt").rtDetails, find(baseline, "rt").rtDetails);
    assert.deepEqual(find(model, "rt").parameterChanges, find(baseline, "rt").parameterChanges);
    for (const id of ["trigger", "reject"]) {
      assert.equal(find(model, id).rtDetails, undefined);
      assert.equal(find(model, id).parameterChanges, undefined);
    }
  });

  it("shows the initial buffer only in the Q1 lane label", () => {
    const events = [event("issue", "q1_issue", 0, { op: "wait" }), event("rt", "wait", 4)];
    const options = { mode: "debug" };
    const baseline = buildModel({ events }, options);
    const buffered = buildModel({ events, simulation: { Control: {
      initial_buffer: { assumed_ns: 1_000_000, possible_ns: 2_000_000, used_ns: 0 },
    } } }, options);
    assert.equal(buffered.lanes.find((lane: any) => lane.kind === "q1-issue").label, "Q1 core · 1–2 ms buffer");
    assert.deepEqual(blocks(buffered), blocks(baseline));
    assert.deepEqual(buffered.annotations, baseline.annotations);
    assert.deepEqual(buffered.viewport, baseline.viewport);
  });

  it("marks only issues with RT commands, preserving setter and Q1-only labels", () => {
    const events = [
      event("setter", "q1_issue", 0, { op: "set_freq" }),
      event("pending", "latched_state_pending", 4, { q1_issue_event_id: "setter", field: "frequency", value: 100 }, { lane: "state.pending" }),
      event("issue", "q1_issue", 4, { op: "play", rt_packet_id: "packet" }),
      event("move", "q1_issue", 8, { op: "move" }),
      event("unresolved", "q1_issue", 12, { op: "play", rt_packet_id: "missing" }),
      event("queue-only", "q1_issue", 16, { op: "play", rt_packet_id: "queue" }),
      event("rt", "play", 100, { q1_issue_event_id: "issue", rt_packet_id: "packet",
        applied_state: { frequency: 100 }, applied_setter_event_ids: { frequency: "pending" } }),
      event("enqueue", "rt_queue_enqueue", 20, { q1_issue_event_id: "queue-only", rt_packet_id: "queue" }, { lane: "debug.rt_queue" }),
    ];
    const { index, model } = assertTargets(events, { setter: [], issue: ["rt"], move: [], unresolved: [], "queue-only": [] });
    assert.deepEqual(index.get("setter").appliedEventIds, ["rt"], "Existing setter application selection remains available");
    for (const [id, label] of [["setter", "set_freq"], ["move", "move"], ["unresolved", "play"]]) {
      assert.equal(blocks(model).find((block) => block.eventId === id).label, label);
    }
  });

  it("retains both original RT path IDs when play is rendered as one block", () => {
    const events = [event("issue", "q1_issue", 4, { op: "play", rt_packet_id: "packet" })];
    for (const path of [1, 0]) events.push(event(`path${path}`, "play", 100,
      { q1_issue_event_id: "issue", rt_packet_id: "packet", path }, { lane: `rt.path${path}` }));
    const { model } = assertTargets(events, { issue: ["path0", "path1"] });
    const merged = blocks(model).find((block) => block.domain.meta.merged_play_paths);
    assert.ok(merged);
    assert.deepEqual(sorted(merged.eventIds), ["path0", "path1"]);
    assert.equal(Boolean(merged.issueMarker), false);
  });

  it("keeps repeated source instructions and same packet IDs on another sequencer separate", () => {
    assertTargets([
      event("first", "q1_issue", 0, { op: "play", rt_packet_id: "p1" }),
      event("second", "q1_issue", 4, { op: "play", rt_packet_id: "p2" }),
      event("other", "q1_issue", 0, { op: "play", rt_packet_id: "p1" }, { sequencer_id: "Readout" }),
      event("first-rt", "play", 100, { rt_packet_id: "p1" }),
      event("second-rt", "play", 104, { rt_packet_id: "p2" }),
      event("other-rt", "play", 100, { rt_packet_id: "p1" }, { sequencer_id: "Readout" }),
    ], { first: ["first-rt"], second: ["second-rt"], other: ["other-rt"] });
  });

  it("scopes packet fallback by both Compare branch and alternative", () => {
    const events: ReturnType<typeof event>[] = [];
    const expected: Record<string, string[]> = {};
    for (const branch of ["branch1", "branch2"]) {
      for (const path of ["taken", "fallthrough"]) {
        const id = `${branch}-${path}`;
        const meta = { rt_packet_id: "same-packet", branch_comparison_branch_id: branch, branch_comparison_path: path };
        events.push(event(id, "q1_issue", 4, { ...meta, op: "play" }, { lane: `branch_compare.${path}.q1.issue` }),
          event(`${id}-rt`, "play", 100, meta, { lane: `branch_compare.${path}.rt.play` }));
        expected[id] = [`${id}-rt`];
      }
    }
    assertTargets(events, expected);
  });

  it("uses remapped loop-preview occurrences instead of their shared source or packet", () => {
    const events = [
      event("issue", "q1_issue", 0, { op: "play", rt_packet_id: "packet" }),
      event("rt", "play", 100, { rt_packet_id: "packet" }),
    ];
    const expected: Record<string, string[]> = { issue: ["rt"] };
    for (const iteration of [1, 2]) {
      const id = `preview-${iteration}`;
      const scope = { loop_preview: "loop", loop_iteration_index: iteration, rt_packet_id: "packet" };
      events.push(event(id, "q1_issue", iteration * 4, { ...scope, op: "play", preview_source_event_id: "issue" }),
        event(`${id}-rt`, "play", 100 + iteration * 4, { ...scope, preview_source_event_id: "rt", q1_issue_event_id: id }));
      expected[id] = [`${id}-rt`];
    }
    events.push(event("legacy-preview-rt", "play", 120, {
      rt_packet_id: "packet", preview_source_event_id: "rt", q1_issue_event_id: "issue",
    }));
    assertTargets(events, expected);
  });

  it("does not guess a packet fallback when multiple issue occurrences match", () => {
    assertTargets([
      event("first", "q1_issue", 0, { op: "play", rt_packet_id: "packet" }),
      event("second", "q1_issue", 4, { op: "play", rt_packet_id: "packet" }),
      event("rt", "play", 100, { rt_packet_id: "packet" }),
    ], { first: [], second: [] });
  });

  it("prefers explicit issue identity over conflicting or ambiguous packet fallback", () => {
    assertTargets([
      event("explicit", "q1_issue", 0, { op: "play", rt_packet_id: "original-packet" }),
      event("fallback1", "q1_issue", 4, { op: "play", rt_packet_id: "other-packet" }),
      event("fallback2", "q1_issue", 8, { op: "play", rt_packet_id: "other-packet" }),
      event("rt", "play", 100, { q1_issue_event_id: "explicit", rt_packet_id: "other-packet" }),
      event("stale-reference", "play", 104, { q1_issue_event_id: "missing-issue", rt_packet_id: "original-packet" }),
    ], { explicit: ["rt"], fallback1: [], fallback2: [] });
  });

  it("preserves marker identity and metadata in both layouts and time bases without changing source IR", () => {
    const ir = { events: [
      event("issue", "q1_issue", 4, { op: "play", rt_packet_id: "packet", aligned_t0: 1004, aligned_t1: 1008 }),
      event("move", "q1_issue", 8, { op: "move", aligned_t0: 1008, aligned_t1: 1012 }),
      event("rt", "play", 100, { q1_issue_event_id: "issue", rt_packet_id: "packet", aligned_t0: 1100, aligned_t1: 1104 }),
    ] };
    const before = JSON.stringify(ir);
    let baseline: unknown;
    for (const layout of ["time", "instruction"]) {
      for (const timeBasis of ["local", "aligned"]) {
        const model = buildModel(ir, { layout, timeBasis, expandedQ1IssueSequencers: ["Control"] });
        const metadata = blocks(model).map(({ start: _start, duration: _duration, title: _title, ...rest }) => rest);
        if (baseline === undefined) baseline = metadata;
        else assert.deepEqual(metadata, baseline);
        const issue = blocks(model).find((block) => block.eventId === "issue");
        assert.equal(issue.issueMarker, true);
        assert.deepEqual(issue.rtEventIds, ["rt"]);
        assert.match(issue.title, new RegExp(`start: ${timeBasis === "aligned" ? 1004 : 4} ns`));
        if (layout === "time") assert.equal(issue.start, timeBasis === "aligned" ? 1004 : 4);
      }
    }
    assert.equal(JSON.stringify(ir), before);
  });
});
