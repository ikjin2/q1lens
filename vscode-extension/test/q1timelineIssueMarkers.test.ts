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
        const metadata = blocks(model).map(({ start: _start, duration: _duration, ...rest }) => rest);
        if (baseline === undefined) baseline = metadata;
        else assert.deepEqual(metadata, baseline);
        const issue = blocks(model).find((block) => block.eventId === "issue");
        assert.equal(issue.issueMarker, true);
        assert.deepEqual(issue.rtEventIds, ["rt"]);
        if (layout === "time") assert.equal(issue.start, timeBasis === "aligned" ? 1004 : 4);
      }
    }
    assert.equal(JSON.stringify(ir), before);
  });
});
