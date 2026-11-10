import assert from "node:assert/strict";

const {
  buildQ1TimelineSharedModel: buildModel,
  buildQ1TimelineSelectionIndex: buildIndex,
} = require("../src/q1timeline/media/timelineAdapter.js");

function event(id: string, kind: string, start: number, meta: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) {
  return {
    id, kind, sequencer_id: "Control", lane: kind === "q1_issue" ? "q1.issue" : `rt.${kind}`,
    t0: { value: start }, t1: { value: start + 4 }, duration: { value: 4 },
    label: kind, source: { file: "Control.q1asm", line: 1, raw: `${kind} 4` }, meta, ...extra,
  };
}

function pending(id: string, issueId: string, field = "frequency") {
  return event(id, "latched_state_pending", 8, { field, value: 100, q1_issue_event_id: issueId }, {
    lane: "state.pending", label: "set_freq",
  });
}

function applied(setterId: string, packetId: string, field = "frequency") {
  return { rt_packet_id: packetId, applied_state: { [field]: 100 }, applied_setter_event_ids: { [field]: setterId } };
}

function selection(index: Map<string, any>, id: string) {
  const entry = index.get(id);
  assert.ok(entry, `Missing selection entry for ${id}`);
  return { relatedEventIds: [...entry.relatedEventIds].sort(), appliedEventIds: [...entry.appliedEventIds].sort() };
}

const empty = { relatedEventIds: [], appliedEventIds: [] };

describe("Q1 timeline latched selection", () => {
  it("selects explicit commits without treating branch snapshot provenance as an application", () => {
    const events = [
      event("first-issue", "q1_issue", 0), pending("first-setter", "first-issue"),
      event("second-issue", "q1_issue", 4), pending("second-setter", "second-issue"),
      event("first-rt", "upd_param", 100, { ...applied("first-setter", "p0"),
        committed_setter_event_ids: { frequency: "first-setter" } }),
    ];
    for (const path of ["taken", "fallthrough"]) {
      const branch = { branch_comparison_branch_id: "branch", branch_comparison_path: path };
      events.push(
        event(`${path}-carry`, "upd_param", 104, { ...branch, ...applied("first-setter", `${path}-p1`),
          committed_setter_event_ids: {} }),
        event(`${path}-commit`, "upd_param", 108, { ...branch, ...applied("second-setter", `${path}-p2`),
          committed_setter_event_ids: { frequency: "second-setter" } }),
        event(`${path}-later`, "upd_param", 112, { ...branch, ...applied("second-setter", `${path}-p3`),
          committed_setter_event_ids: {} }),
      );
    }
    const index = buildIndex({ events });
    assert.deepEqual(selection(index, "first-issue"), { relatedEventIds: ["first-rt"], appliedEventIds: ["first-rt"] });
    assert.deepEqual(selection(index, "second-issue"), {
      relatedEventIds: ["fallthrough-commit", "taken-commit"], appliedEventIds: ["fallthrough-commit", "taken-commit"],
    });
    assert.deepEqual(selection(index, "fallthrough-carry"), empty);
    assert.deepEqual(selection(index, "taken-later"), empty);
  });

  it("links a skipped one-shot phase capture without reporting a parameter application", () => {
    const events = [
      event("delta", "q1_issue", 0), pending("delta-latch", "delta", "phase_delta"),
      event("reset", "q1_issue", 4), pending("reset-latch", "reset", "phase_reset"),
      event("frequency", "q1_issue", 8), pending("frequency-latch", "frequency"),
      event("skip", "conditional_skip", 12, {conditional_skipped: true,
        captured_state: {phase_delta: 100, phase_reset: true, frequency: 100},
        captured_setter_event_ids: {phase_delta: "delta-latch", phase_reset: "reset-latch", frequency: "frequency-latch"},
        applied_state: {}, applied_setter_event_ids: {}, committed_setter_event_ids: {}}),
      event("apply", "upd_param", 16, applied("frequency-latch", "next")),
    ];
    const index = buildIndex({events});
    for (const id of ["delta", "reset"]) {
      assert.deepEqual(selection(index, id), {relatedEventIds: ["skip"], appliedEventIds: []});
      assert.deepEqual(index.get(id).capturedEventIds, ["skip"]);
      assert.equal(index.get(id).rtNavigationEventId, "skip");
    }
    assert.deepEqual(selection(index, "frequency"), {relatedEventIds: ["apply"], appliedEventIds: ["apply"]});
  });

  it("navigates equal-duration conditional RT commands to their own uncertain-output block", () => {
    const events = [event("wait-issue", "q1_issue", 0),
      event("wait", "wait", 100, { q1_issue_event_id: "wait-issue" }),
      event("conditional-issue", "q1_issue", 4),
      event("conditional", "unknown_region", 200, {
        q1_issue_event_id: "conditional-issue", conditional_timing_known: true, output_state_unknown: true,
      }),
      event("setting", "q1_issue", 8)];
    const index = buildIndex({ events });
    assert.equal(index.get("conditional").rtNavigationEventId, "conditional");
    assert.equal(index.get("conditional-issue").rtNavigationEventId, "conditional");
    assert.equal(index.get("setting").rtNavigationEventId, "conditional");
  });

  it("navigates to the next RT instruction in program order and retains the last RT for trailing Q1 commands", () => {
    const events = [
      event("setup", "q1_issue", 0),
      event("issue", "q1_issue", 4, { rt_packet_id: "packet" }),
      event("rt", "wait", 1000, { q1_issue_event_id: "issue" }),
      event("other-issue", "q1_issue", 0, { rt_packet_id: "packet" }, { sequencer_id: "Other" }),
      event("other-rt", "wait", 2000, { q1_issue_event_id: "other-issue" }, { sequencer_id: "Other" }),
      event("setting", "q1_issue", 8),
      event("repeat-issue", "q1_issue", 12, { rt_packet_id: "repeat" }),
      event("repeat-rt", "wait", 1100, { q1_issue_event_id: "repeat-issue" }),
      event("repeat-setting", "q1_issue", 16),
    ];
    const before = JSON.stringify(events);
    const index = buildIndex({ events });
    assert.equal(index.get("setup").rtPreparation, true);
    assert.equal(index.get("setup").rtNavigationEventId, "rt");
    assert.equal(index.get("setting").rtNavigationEventId, "repeat-rt");
    assert.equal(index.get("repeat-setting").rtNavigationEventId, "repeat-rt");
    for (const id of ["rt", "other-rt", "repeat-rt"]) assert.equal(index.get(id).rtNavigationEventId, id);
    assert.equal(JSON.stringify(events), before);
  });

  it("navigates to the next RT in each branch alternative and retains its last RT at the end", () => {
    const events = [event("common-issue", "q1_issue", 0),
      event("common-rt", "wait", 100, { q1_issue_event_id: "common-issue" })];
    for (const path of ["taken", "fallthrough"]) {
      const branch = { branch_comparison_branch_id: "branch", branch_comparison_path: path };
      events.push(event(`${path}-before`, "q1_issue", 4, branch),
        event(`${path}-issue`, "q1_issue", 8, branch),
        event(`${path}-rt`, "wait", 200, { ...branch, q1_issue_event_id: `${path}-issue` },
          { lane: `branch_compare.${path}.rt.wait` }),
        event(`${path}-after`, "q1_issue", 12, branch));
    }
    const index = buildIndex({ events });
    for (const path of ["taken", "fallthrough"]) {
      assert.equal(index.get(`${path}-before`).rtNavigationEventId, `${path}-rt`);
      assert.equal(index.get(`${path}-after`).rtNavigationEventId, `${path}-rt`);
    }
  });

  it("keeps next-RT navigation within each loop preview occurrence and diff version", () => {
    const events = [event("issue", "q1_issue", 0),
      event("rt", "wait", 100, { q1_issue_event_id: "issue" }),
      event("tail", "q1_issue", 4)];
    for (const iteration of [1, 2]) {
      const preview = { loop_preview: "loop", loop_iteration_index: iteration };
      events.push(event(`setup-${iteration}`, "q1_issue", 0, { ...preview, preview_source_event_id: "tail" }),
        event(`issue-${iteration}`, "q1_issue", 4, { ...preview, preview_source_event_id: "issue" }),
        event(`rt-${iteration}`, "wait", 100 + iteration * 4, {
          ...preview, preview_source_event_id: "rt", q1_issue_event_id: `issue-${iteration}`,
        }));
    }
    events.push(event("removed-setup", "q1_issue", 0, { diff_status: "removed" }),
      event("removed-issue", "q1_issue", 4, { diff_status: "removed" }),
      event("removed-rt", "wait", 200, { diff_status: "removed", q1_issue_event_id: "removed-issue" }));
    const index = buildIndex({ events });
    assert.equal(index.get("tail").rtNavigationEventId, "rt");
    for (const iteration of [1, 2]) assert.equal(index.get(`setup-${iteration}`).rtNavigationEventId, `rt-${iteration}`);
    assert.equal(index.get("removed-setup").rtNavigationEventId, "removed-rt");
  });

  it("leaves Q1-only programs without an invented RT target", () => {
    const index = buildIndex({ events: [event("setup", "q1_issue", 0), event("add", "q1_issue", 4)] });
    for (const id of ["setup", "add"]) assert.equal(index.get(id).rtNavigationEventId, undefined);
  });

  it("shows the setter only on Q1 in both layouts and modes without removing IR state", () => {
    const ir = { events: [
      event("setter", "q1_issue", 0, { op: "set_freq" }), pending("pending", "setter"),
      event("play", "play", 100, applied("pending", "packet")),
      event("state", "latched_state_applied", 100, { field: "frequency", value: 100 }, { lane: "state.applied" }),
    ] };
    const before = JSON.stringify(ir);
    for (const layout of ["time", "instruction"]) {
      for (const mode of ["normal", "debug"]) {
        const model = buildModel(ir, { layout, mode, expandedQ1IssueSequencers: ["Control"] });
        const blocks = model.lanes.flatMap((lane: any) => lane.blocks);
        assert.equal(blocks.some((block: any) => block.id === "pending"), false);
        assert.equal(blocks.some((block: any) => block.id === "setter"), true);
        assert.equal(blocks.some((block: any) => block.id === "state"), true);
      }
    }
    assert.equal(JSON.stringify(ir), before);
  });

  it("links exact Q1 and RT occurrences while excluding queue and state effects", () => {
    const ir = { events: [
      event("issue1", "q1_issue", 0, { rt_packet_id: "p1" }),
      event("issue2", "q1_issue", 4, { rt_packet_id: "p2" }),
      event("rt1", "play", 100, { rt_packet_id: "p1" }),
      event("rt2", "play", 104, { q1_issue_event_id: "issue2" }),
      ...["rt_queue_enqueue", "rt_queue_dequeue", "marker_state", "latched_state_applied", "acquisition_result", "sync_wait"].map((kind) =>
        event(kind, kind, 100, { rt_packet_id: "p1", q1_issue_event_id: "issue1" })),
      event("debug", "play", 100, { rt_packet_id: "p1" }, { lane: "debug.preview" }),
    ] };
    const index = buildIndex(ir);
    assert.deepEqual(selection(index, "issue1"), { relatedEventIds: ["rt1"], appliedEventIds: [] });
    assert.deepEqual(selection(index, "rt1"), { relatedEventIds: ["issue1"], appliedEventIds: [] });
    assert.deepEqual(selection(index, "issue2"), { relatedEventIds: ["rt2"], appliedEventIds: [] });
    assert.deepEqual(selection(index, "rt2"), { relatedEventIds: ["issue2"], appliedEventIds: [] });
    for (const item of ir.events.slice(4)) assert.deepEqual(selection(index, item.id), empty);
  });

  it("keeps overwritten and later Q1 setters separate and skips waits and conditional skips", () => {
    const ir = { events: [
      event("overwritten", "q1_issue", 0), pending("old", "overwritten"),
      event("setter1", "q1_issue", 4), pending("set1", "setter1"),
      event("setter2", "q1_issue", 12), pending("set2", "setter2"),
      event("wait", "wait", 40, applied("set1", "wait-packet")),
      event("skip", "conditional_skip", 80, applied("set1", "skip-packet")),
      event("flagged-skip", "play", 84, { ...applied("set1", "flagged-packet"), conditional_skipped: true }),
      event("first", "upd_param", 100, applied("set1", "first-packet")),
      event("second", "upd_param", 104, applied("set2", "second-packet")),
      event("later", "play", 108, applied("set2", "later-packet")),
    ] };
    const index = buildIndex(ir);
    assert.deepEqual(selection(index, "overwritten"), empty);
    assert.deepEqual(selection(index, "setter1"), { relatedEventIds: ["first"], appliedEventIds: ["first"] });
    assert.deepEqual(selection(index, "setter2"), { relatedEventIds: ["second"], appliedEventIds: ["second"] });
    assert.deepEqual(selection(index, "first"), { relatedEventIds: ["setter1"], appliedEventIds: ["first"] });
    assert.equal(index.get("overwritten").rtNavigationEventId, undefined);
    assert.equal(index.get("setter1").rtNavigationEventId, "first");
    assert.equal(index.get("setter2").rtNavigationEventId, "second");
    for (const id of ["wait", "skip", "flagged-skip", "later", "set1", "set2"]) assert.deepEqual(selection(index, id), empty);
  });

  it("preserves both merged play paths at the earliest applying packet and all its setters", () => {
    const first = { ...applied("set-frequency", "first"), q1_issue_event_id: "play-issue",
      applied_state: { frequency: 100, awg_offset: [100, 0] },
      applied_setter_event_ids: { frequency: "set-frequency", awg_offset: "set-offset" } };
    const ir = { events: [
      event("frequency-issue", "q1_issue", 0), pending("set-frequency", "frequency-issue"),
      event("offset-issue", "q1_issue", 4), pending("set-offset", "offset-issue", "awg_offset"),
      event("play-issue", "q1_issue", 8, { rt_packet_id: "first" }),
      event("later", "play", 200, applied("set-frequency", "later")),
      event("path1", "play", 100, first, { lane: "rt.path1" }),
      event("path0", "play", 100, first, { lane: "rt.path0" }),
    ] };
    const before = JSON.stringify(ir);
    const index = buildIndex(ir);
    assert.deepEqual(selection(index, "frequency-issue"), { relatedEventIds: ["path0", "path1"], appliedEventIds: ["path0", "path1"] });
    assert.deepEqual(selection(index, "offset-issue"), selection(index, "frequency-issue"));
    assert.deepEqual(selection(index, "path0"), {
      relatedEventIds: ["frequency-issue", "offset-issue", "play-issue"], appliedEventIds: ["path0", "path1"],
    });
    assert.deepEqual(selection(index, "path1"), selection(index, "path0"));
    assert.deepEqual(selection(index, "play-issue"), { relatedEventIds: ["path0", "path1"], appliedEventIds: [] });
    assert.deepEqual(selection(index, "later"), empty);
    const blocks = buildModel(ir).lanes.flatMap((lane: any) => lane.blocks);
    assert.deepEqual(blocks.find((block: any) => block.id === "merged-play:path0+path1").eventIds, ["path0", "path1"]);
    assert.equal(JSON.stringify(ir), before);
  });

  it("scopes packet IDs by sequencer and rejects setter links across sequencers", () => {
    const ir = { events: [
      event("control-issue", "q1_issue", 0, { rt_packet_id: "same-packet" }),
      event("readout-issue", "q1_issue", 0, { rt_packet_id: "same-packet" }, { sequencer_id: "Readout" }),
      event("control-rt", "play", 100, { rt_packet_id: "same-packet" }),
      event("readout-rt", "play", 100, applied("set", "same-packet"), { sequencer_id: "Readout" }),
      event("setter", "q1_issue", 0), pending("set", "setter"),
      event("foreign-issue", "play", 100, { q1_issue_event_id: "control-issue" }, { sequencer_id: "Readout" }),
    ] };
    const index = buildIndex(ir);
    assert.deepEqual(selection(index, "control-issue"), { relatedEventIds: ["control-rt"], appliedEventIds: [] });
    assert.deepEqual(selection(index, "readout-issue"), { relatedEventIds: ["readout-rt"], appliedEventIds: [] });
    assert.deepEqual(selection(index, "readout-rt"), { relatedEventIds: ["readout-issue"], appliedEventIds: [] });
    assert.deepEqual(selection(index, "setter"), empty);
    assert.deepEqual(selection(index, "foreign-issue"), empty);
  });

  it("does not infer links from matching source, missing applied fields, or preview copies", () => {
    const ir = { events: [
      event("setter", "q1_issue", 0), pending("set", "setter"),
      event("no-provenance", "play", 100, { applied_state: { frequency: 100 } }),
      event("captured-only", "play", 100, { rt_packet_id: "p1", applied_setter_event_ids: { frequency: "set" } }),
      event("preview", "play", 100, { ...applied("set", "p2"), preview_source_event_id: "original" }),
      event("missing-setter", "play", 100, applied("missing", "p3")),
      event("wrong-field", "play", 100, applied("set", "p4", "phase")),
    ] };
    const index = buildIndex(ir);
    for (const item of ir.events) assert.deepEqual(selection(index, item.id), empty);
    assert.deepEqual([...buildIndex({})], []);
  });

  it("does not replace an original Q1 occurrence with a loop preview sharing its packet ID", () => {
    const index = buildIndex({ events: [
      event("issue", "q1_issue", 0, { rt_packet_id: "packet" }),
      event("rt", "play", 100, { rt_packet_id: "packet" }),
      event("preview-issue", "q1_issue", 4, { rt_packet_id: "packet", preview_source_event_id: "issue" }),
      event("preview-rt", "play", 104, { rt_packet_id: "packet", preview_source_event_id: "rt" }),
      event("legacy-preview-rt", "play", 104, { rt_packet_id: "packet", q1_issue_event_id: "issue", preview_source_event_id: "rt" }),
    ] });
    assert.deepEqual(selection(index, "issue"), { relatedEventIds: ["rt"], appliedEventIds: [] });
    assert.deepEqual(selection(index, "rt"), { relatedEventIds: ["issue"], appliedEventIds: [] });
    assert.deepEqual(selection(index, "preview-issue"), empty);
    assert.deepEqual(selection(index, "preview-rt"), empty);
    assert.deepEqual(selection(index, "legacy-preview-rt"), empty);
  });

  it("links loop previews by their remapped occurrence IDs without mixing shared packet IDs", () => {
    const preview = { loop_preview: "loop", loop_iteration_index: 1 };
    const index = buildIndex({ events: [
      event("setter", "q1_issue", 0), pending("set", "setter"),
      event("issue", "q1_issue", 4, { rt_packet_id: "packet" }),
      event("rt", "play", 100, { ...applied("set", "packet"), q1_issue_event_id: "issue" }),
      event("preview-setter", "q1_issue", 8, { ...preview, preview_source_event_id: "setter" }),
      { ...pending("preview-set", "preview-setter"), meta: {
        ...pending("preview-set", "preview-setter").meta, ...preview, preview_source_event_id: "set",
      } },
      event("preview-issue", "q1_issue", 12, { ...preview, rt_packet_id: "packet", preview_source_event_id: "issue" }),
      event("preview-rt", "play", 104, { ...applied("preview-set", "packet"), ...preview,
        q1_issue_event_id: "preview-issue", preview_source_event_id: "rt" }),
    ] });
    assert.deepEqual(selection(index, "issue"), { relatedEventIds: ["rt"], appliedEventIds: [] });
    assert.deepEqual(selection(index, "setter"), { relatedEventIds: ["rt"], appliedEventIds: ["rt"] });
    assert.deepEqual(selection(index, "preview-issue"), { relatedEventIds: ["preview-rt"], appliedEventIds: [] });
    assert.deepEqual(selection(index, "preview-setter"), { relatedEventIds: ["preview-rt"], appliedEventIds: ["preview-rt"] });
    assert.deepEqual(selection(index, "preview-rt"), {
      relatedEventIds: ["preview-issue", "preview-setter"], appliedEventIds: ["preview-rt"],
    });
  });

  it("preserves each branch alternative's first application of shared and path-local setters", () => {
    const events = [event("shared-issue", "q1_issue", 0), pending("shared-set", "shared-issue")];
    for (const path of ["taken", "fallthrough"]) {
      const branch = { branch_comparison_branch_id: "branch", branch_comparison_path: path };
      events.push(
        event(`${path}-setter`, "q1_issue", 4, branch), pending(`${path}-set`, `${path}-setter`, "awg_offset"),
        event(`${path}-issue`, "q1_issue", 8, { ...branch, rt_packet_id: `${path}-packet` }),
        event(`${path}-rt`, "play", 100, { ...branch, rt_packet_id: `${path}-packet`,
          applied_state: { frequency: 100, awg_offset: [100, 0] },
          applied_setter_event_ids: { frequency: "shared-set", awg_offset: `${path}-set` },
        }, { lane: `branch_compare.${path}.rt.play` }),
        event(`${path}-later`, "play", 104, { ...branch, ...applied("shared-set", `${path}-later-packet`) },
          { lane: `branch_compare.${path}.rt.play` }),
      );
    }
    const index = buildIndex({ events });
    assert.deepEqual(selection(index, "shared-issue"), {
      relatedEventIds: ["fallthrough-rt", "taken-rt"], appliedEventIds: ["fallthrough-rt", "taken-rt"],
    });
    for (const path of ["taken", "fallthrough"]) {
      assert.deepEqual(selection(index, `${path}-setter`), { relatedEventIds: [`${path}-rt`], appliedEventIds: [`${path}-rt`] });
      assert.deepEqual(selection(index, `${path}-rt`), {
        relatedEventIds: ["shared-issue", `${path}-issue`, `${path}-setter`].sort(), appliedEventIds: [`${path}-rt`],
      });
      assert.deepEqual(selection(index, `${path}-later`), empty);
    }
  });
});
