import assert from "node:assert/strict";

const { buildQ1TimelineSharedModel: model, buildWaitTimeScale } = require("../src/q1timeline/media/timelineAdapter.js");
const hardware = require("../src/q1timeline/media/hardwareState.js");
const concrete = (value: number) => ({ kind: "concrete", value });
function event(id: string, start: number, end: number | null = start + 1000, extra: any = {}): any {
  return { id, kind: "acquire", lane: "rt.acquire", sequencer_id: "readout", confidence: "exact",
    t0: concrete(start), t1: concrete(start + 4), duration: concrete(4),
    source: { file: "readout.q1asm", line: 1, raw: "acquire 0,0,4" },
    meta: { rt_packet_id: id, acquisition_index: 0, bin_index: 0, acquisition_window: {
      start_ns: start, end_ns: end, configured_duration_ns: end === null ? null : 1000,
      effective_duration_ns: end === null ? null : end - start,
      source: end === null ? "unknown" : "hardware_parameters",
      status: end === null ? "unknown" : end < start + 1000 ? "interrupted" : "completed",
    }, ...extra } };
}

describe("Q1 timeline acquisition windows", () => {
  it("adds one ACQ row only to sequencers with acquisition commands and preserves RT waits", () => {
    const ir = { sequencers: [{ id: "readout" }, { id: "drive", hardware_parameters: { integration_length_acq: 1000 } }],
      events: [event("a", 100, 700), event("b", 700), event("c", 2000, null),
        { ...event("drive", 0), kind: "wait", sequencer_id: "drive", meta: {} }] };
    for (const mode of ["normal", "debug"]) {
      const result = model(ir, { mode });
      const rows = result.lanes.filter((lane: any) => lane.role === "acquisition");
      assert.equal(rows.length, 1);
      assert.equal(rows[0].parentGroupId, "sequencer:readout");
      assert.deepEqual(rows[0].blocks.map((block: any) => [block.eventId, block.duration]), [["a", 600], ["b", 1000], ["c", 0]]);
      assert.match(rows[0].blocks[0].classNames, /acquisition-interrupted/);
      assert.equal(rows[0].blocks[2].label, "?");
      const rt = result.lanes.find((lane: any) => lane.id === "sequencer:readout");
      assert.deepEqual(rt.blocks.map((block: any) => block.duration), [4, 4, 4]);
      assert.ok(rows[0].blocks.every((block: any) => !block.rtDetails));
    }
    assert.equal(model(ir, { layout: "instruction" }).lanes.filter((lane: any) => lane.role === "acquisition").length, 0);
  });

  it("aligns the window and includes integration beyond the last RT command", () => {
    const ir = { events: [event("a", 100, 1100, { aligned_t0: 120, aligned_t1: 124 })] };
    const result = model(ir, { timeBasis: "aligned" });
    const block = result.lanes.find((lane: any) => lane.role === "acquisition").blocks[0];
    assert.deepEqual([block.start, block.duration], [120, 1000]);
    assert.equal(result.totalTime, 1120);
  });

  it("keeps comparison alternatives in their own Jump and Continue ACQ rows", () => {
    const events = ["taken", "fallthrough"].map(path => event(path, 100, null, {
      branch_comparison_branch_id: "branch", branch_comparison_path: path,
    }));
    const rows = model({ events }).lanes.filter((lane: any) => lane.role === "acquisition");
    assert.deepEqual(rows.map((lane: any) => lane.label), ["Jump · Acquisition", "Continue · Acquisition"]);
    assert.deepEqual(rows.map((lane: any) => lane.blocks[0].eventId), ["taken", "fallthrough"]);
  });

  it("folds a configured integration independently while preserving its boundaries and real time", () => {
    const wait = { ...event("wait", 104), kind: "wait", t1: concrete(2000), meta: {}, duration: concrete(1896) };
    const ir = { events: [event("a", 100, 1100), wait] };
    const before = JSON.stringify(ir);
    const scale = buildWaitTimeScale(ir);
    assert.deepEqual(scale.gaps.map((gap: any) => [gap.start, gap.end]), [[120, 1084], [1116, 1984]]);
    assert.deepEqual(scale.gaps[0].acquisition, { start: 100, end: 1100 });
    assert.equal(scale.toDisplay(104) - scale.toDisplay(100), 4);
    for (const time of [100, 104, 120, 500, 1084, 1100, 1500, 2000]) {
      assert.ok(Math.abs(scale.fromDisplay(scale.toDisplay(time)) - time) < 1e-6);
    }
    const expanded = buildWaitTimeScale(ir, { expandedGaps: [scale.gaps[0]] });
    assert.equal(expanded.toDisplay(1100) - expanded.toDisplay(100), 1000);
    assert.equal(expanded.gaps.length, 1);
    assert.equal(JSON.stringify(ir), before);
    assert.equal(buildWaitTimeScale({ events: [event("a", 100, null), wait] }).gaps.length, 0);
  });

  it("preserves a peer command inside integration and labels partial spans by their actual length", () => {
    const ir = { alignment: { readout: { mode: "first_wait_sync", offset: 20, confidence: "exact" },
      drive: { mode: "first_wait_sync", offset: 20, confidence: "exact" } }, events: [
      event("a", 100, 1100),
      { ...event("wait", 104), kind: "wait", t1: concrete(2000), meta: {} },
      { ...event("peer", 100), kind: "wait", sequencer_id: "drive", t1: concrete(2000), meta: {} },
      { ...event("change", 500), kind: "upd_param", sequencer_id: "drive", meta: {} },
    ] };
    const scale = buildWaitTimeScale(ir, { timeBasis: "aligned" });
    assert.deepEqual(scale.gaps.map((gap: any) => [gap.start, gap.end]), [[140, 504], [540, 1104], [1136, 2004]]);
    assert.ok(scale.gaps.every((gap: any) => !gap.acquisition));
    assert.equal(scale.toDisplay(524) - scale.toDisplay(520), 4);
  });

  it("keeps different simultaneous acquisition endpoints separate", () => {
    const ir = { alignment: { readout: { mode: "first_wait_sync", offset: 0, confidence: "exact" },
      other: { mode: "first_wait_sync", offset: 0, confidence: "exact" } }, events: [
      event("a", 100, 1100), { ...event("b", 100, 1700), sequencer_id: "other" },
      ...["readout", "other"].map(id => ({ ...event(`wait-${id}`, 104), kind: "wait", sequencer_id: id, t1: concrete(2000), meta: {} })),
    ] };
    for (const playback of [undefined, {}]) {
      const scale = buildWaitTimeScale({ ...ir, playback });
      assert.deepEqual(scale.gaps.map((gap: any) => [gap.start, gap.end]), [[120, 1084], [1116, 1684], [1716, 1984]]);
      assert.deepEqual(scale.gaps[0].acquisition, { start: 100, end: 1100 });
      assert.ok(!scale.gaps[1].acquisition);
    }
  });

  it("keeps Hardware integration active after RT wait and uses aligned completion", () => {
    const ir = { sequencers: [{ id: "readout" }], events: [event("a", 100, 700)],
      alignment: { readout: { offset: 20, confidence: "exact" } } };
    const index = hardware.createIndex(ir);
    assert.equal(hardware.snapshot(index, "readout", 104).rt.status, "last-command");
    assert.equal(hardware.snapshot(index, "readout", 104).acquisition.status, "active");
    assert.equal(hardware.snapshot(index, "readout", 699).acquisition.status, "active");
    assert.equal(hardware.snapshot(index, "readout", 700).acquisition.status, "last-command");
    assert.equal(hardware.snapshot(index, "readout", 719, "aligned").acquisition.status, "active");
    assert.equal(hardware.snapshot(index, "readout", 720, "aligned").acquisition.status, "last-command");
    assert.equal(ir.events[0].t1.value, 104);
    const unknown = hardware.createIndex({ events: [event("u", 100, null)] });
    assert.equal(hardware.snapshot(unknown, "readout", 104).acquisition.status, "unknown");
  });

  it("uses duration labels and distinguishes modes without inventing weighted lengths", () => {
    const weighted = event("weighted", 2000, null, { op: "acquire_weighed" });
    const row = model({ events: [event("a", 100, 700), event("b", 700), weighted] }).lanes.find((lane: any) => lane.role === "acquisition");
    assert.equal(row.label, "Acquisition");
    assert.deepEqual(row.blocks.map((block: any) => [block.label, block.acquisitionMode]),
      [["600 ns", "integration"], ["1 μs", "integration"], ["?", "weighted"]]);
    assert.ok(row.blocks.every((block: any) => !block.accentColor));
  });

  it("keeps scope in a second compact track and preserves both ends when folding", () => {
    const acquisition = event("a", 100, 1100, { aligned_t0: 120, aligned_t1: 124, scope_acquisition_window: {
      start_ns: 100, end_ns: 16484, configured_duration_ns: 16384, effective_duration_ns: 16384,
      status: "completed", source: "hardware_parameters", paths: [0, 1],
    } });
    const result = model({ events: [acquisition] }, { timeBasis: "aligned" });
    const row = result.lanes.find((lane: any) => lane.role === "acquisition");
    assert.equal(row.acquisitionTracks, 2);
    assert.deepEqual(row.blocks.map((block: any) => [block.eventId, block.start, block.duration, block.acquisitionTrack]),
      [["a", 120, 1000, 0], ["a", 120, 16384, 1]]);
    assert.equal(row.blocks[1].acquisitionMode, "scope");
    assert.equal(row.blocks[1].label, "16.384 μs");
    assert.equal(result.totalTime, 16504);
    const wait = { ...event("wait", 104), kind: "wait", t1: concrete(20000), duration: concrete(19896), meta: {} };
    const scale = buildWaitTimeScale({ events: [acquisition, wait] });
    assert.deepEqual(scale.gaps.map((gap: any) => [gap.start, gap.end]), [[120, 1084], [1116, 16468], [16500, 19984]]);
    assert.deepEqual(scale.gaps[0].acquisition, { start: 100, end: 1100 });
  });
});
