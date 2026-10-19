import assert from "node:assert/strict";

type Gap = { start: number; end: number; displayStart: number; displayEnd: number; loopKeys?: string[] };
type TimeScale = { gaps: Gap[]; toDisplay: (time: number) => number; fromDisplay: (time: number) => number };
type ScaleOptions = { timeBasis?: string; expandedGaps?: { start: number; end: number }[]; expandedPlaybackLoops?: string[] };
const { buildWaitTimeScale, buildTimelineTimeScale } = require("../src/q1timeline/media/timelineAdapter.js") as {
  buildWaitTimeScale: (ir: any, options?: ScaleOptions) => TimeScale;
  buildTimelineTimeScale: (ir: any, options?: ScaleOptions & { waitGaps?: Gap[] }) => TimeScale;
};

function event(kind: string, start: number, end = start, extra: Record<string, unknown> = {}) {
  return { kind, sequencer_id: "q0", lane: `rt.${kind}`, confidence: "exact",
    t0: { kind: "concrete", value: start }, t1: { kind: "concrete", value: end }, ...extra };
}
function zeroState(start = 0, sequencer_id = "q0") {
  return event("upd_param", start, start + 4, { sequencer_id, meta: { applied_state: { awg_offset: [0, 0], marker: 0 } } });
}
function exactAlignment(offset = 0) { return { offset, mode: `manual:${offset}`, confidence: "exact", unaligned: false }; }
function closeTo(actual: number, expected: number) {
  assert.ok(Math.abs(actual - expected) < 0.000001, `${actual} should equal ${expected}`);
}
function bounds(scale: TimeScale) { return scale.gaps.map(({ start, end }) => ({ start, end })); }

describe("Q1 timeline wait folding", () => {
  it("folds unchanged spans while preserving their real duration", () => {
    const scale = buildWaitTimeScale({ events: [zeroState(), event("wait", 4, 10_004), event("stop", 10_004)] });
    assert.deepEqual(scale.gaps, [{ start: 20, end: 9988, displayStart: 20, displayEnd: 84 }]);
    assert.equal(scale.toDisplay(4), 4);
    assert.equal(scale.toDisplay(10_004), 100);
    assert.equal(scale.toDisplay(10_104) - scale.toDisplay(10_004), 100);
  });

  it("round trips gap interiors and outside times without changing the input", () => {
    const ir = { events: [zeroState(), event("wait", 4, 10_004), event("stop", 10_004)] };
    const before = JSON.stringify(ir);
    const scale = buildWaitTimeScale(ir);
    const times = [-100, 0, 4, 20, 5000, 9988, 10_004, 30_000];
    for (const time of times) closeTo(scale.fromDisplay(scale.toDisplay(time)), time);
    for (let index = 1; index < times.length; index += 1) assert.ok(scale.toDisplay(times[index]) > scale.toDisplay(times[index - 1]));
    for (const gap of scale.gaps) {
      for (const display of [gap.displayStart, (gap.displayStart + gap.displayEnd) / 2, gap.displayEnd]) {
        closeTo(scale.toDisplay(scale.fromDisplay(display)), display);
      }
    }
    assert.equal(JSON.stringify(ir), before);
  });

  it("expands only the selected global gap and recomputes later display coordinates", () => {
    const ir = { alignment: { q0: exactAlignment(), q1: exactAlignment() }, events: [
      event("wait", 0, 3000), event("wait", 0, 3000, { sequencer_id: "q1" }),
      event("marker_state", 1000, 1000, { sequencer_id: "q1" }),
      event("marker_state", 2000, 2000, { sequencer_id: "q1" }),
    ] };
    const options = { expandedGaps: [{ start: 1016, end: 1984 }] };
    const before = JSON.stringify({ ir, options });
    const scale = buildWaitTimeScale(ir, options);
    assert.deepEqual(scale.gaps, [
      { start: 16, end: 984, displayStart: 16, displayEnd: 80 },
      { start: 2016, end: 2984, displayStart: 1112, displayEnd: 1176 },
    ]);
    assert.equal(scale.toDisplay(1984) - scale.toDisplay(1016), 968);
    assert.equal(scale.toDisplay(3000), 1192);
    const times = [-100, 0, 16, 500, 984, 1000, 1016, 1500, 1984, 2000, 2016, 2500, 2984, 3000, 4000];
    for (const time of times) closeTo(scale.fromDisplay(scale.toDisplay(time)), time);
    for (let index = 1; index < times.length; index += 1) assert.ok(scale.toDisplay(times[index]) > scale.toDisplay(times[index - 1]));
    for (const display of [-100, 16, 48, 80, 112, 500, 1080, 1112, 1144, 1176, 1192, 2000]) {
      closeTo(scale.toDisplay(scale.fromDisplay(display)), display);
    }
    assert.equal(JSON.stringify({ ir, options }), before);
  });

  it("returns an identity scale when every detected gap is expanded", () => {
    const ir = { events: [event("wait", 0, 2000), event("marker_state", 1000)] };
    const expandedGaps = bounds(buildWaitTimeScale(ir));
    assert.equal(expandedGaps.length, 2);
    const scale = buildWaitTimeScale(ir, { expandedGaps });
    assert.deepEqual(scale.gaps, []);
    for (const time of [-100, 0, 16, 500, 984, 1000, 1016, 1500, 1984, 2000, 3000]) {
      assert.equal(scale.toDisplay(time), time);
      assert.equal(scale.fromDisplay(time), time);
    }
  });

  it("ignores expansion bounds that do not exactly match a gap in the current time basis", () => {
    const ir = { alignment: { q0: exactAlignment(500) }, events: [event("wait", 0, 2000), event("marker_state", 1000)] };
    const local = buildWaitTimeScale(ir);
    assert.deepEqual(buildWaitTimeScale(ir, { expandedGaps: [
      { start: 16, end: 985 }, { start: 1015, end: 1984 }, { start: 3000, end: 4000 },
    ] }).gaps, local.gaps);
    const aligned = buildWaitTimeScale(ir, { timeBasis: "aligned" });
    assert.deepEqual(buildWaitTimeScale(ir, { timeBasis: "aligned", expandedGaps: bounds(local) }).gaps, aligned.gaps);
    assert.deepEqual(bounds(buildWaitTimeScale(ir, { timeBasis: "aligned", expandedGaps: [bounds(aligned)[0]] })), [bounds(aligned)[1]]);
  });

  it("keeps waits of 128 ns linear and folds longer verified waits", () => {
    const scale = buildWaitTimeScale({ events: [zeroState(), event("wait", 4, 132), zeroState(132), event("wait", 136, 265)] });
    assert.deepEqual(scale.gaps, [{ start: 152, end: 249, displayStart: 152, displayEnd: 216 }]);
    assert.equal(scale.toDisplay(132) - scale.toDisplay(4), 128);
  });

  it("intersects observed extents and merges waits without state changes", () => {
    const scale = buildWaitTimeScale({ sequencers: [{ id: "q0" }, { id: "q1" }],
      alignment: { q0: exactAlignment(), q1: exactAlignment() }, events: [
        zeroState(), event("wait", 4, 10_004),
        event("wait", 1000, 3000, { sequencer_id: "q1" }), event("wait", 5000, 7000, { sequencer_id: "q1" }),
      ] });
    assert.deepEqual(bounds(scale), [{ start: 1016, end: 6984 }]);
    assert.equal(scale.toDisplay(1000), 1000);
    assert.equal(scale.toDisplay(10_004) - scale.toDisplay(7000), 3004);
  });

  it("leaves missing sequencers and unproven omitted iterations unfolded", () => {
    const events = [zeroState(), event("wait", 4, 1004), event("stop", 10_004)];
    for (const extra of [{ sequencers: [{ id: "q0" }, { id: "q1" }] }, { events: [...events, zeroState(0, "q1")] }]) {
      assert.deepEqual(buildWaitTimeScale({ events, alignment: { q0: exactAlignment(), q1: exactAlignment() }, ...extra }).gaps, []);
    }
    const scale = buildWaitTimeScale({ events: [...events,
      event("loop_block", 4, 1004, { meta: { count: 10, visible_iteration_count: 1, preview_policy: "first_iteration_only", period: { kind: "concrete", value: 1000 } } }),
      event("wait", 10_004, 11_004),
    ] });
    assert.deepEqual(bounds(scale), [{ start: 20, end: 988 }]);
    assert.equal(scale.toDisplay(11_004) - scale.toDisplay(1004), 10_000);
    assert.deepEqual(bounds(buildWaitTimeScale({ events: [zeroState(), event("stop", 10_004)] })), [{ start: 20, end: 9988 }]);
  });

  it("never uses a play command duration or stop as proof that its waveform ended", () => {
    const scale = buildWaitTimeScale({ alignment: { q0: exactAlignment(), q1: exactAlignment() }, events: [
      zeroState(), event("wait", 4, 10_004), zeroState(0, "q1"), event("wait", 4, 1000, { sequencer_id: "q1" }),
      event("play", 1000, 1004, { sequencer_id: "q1", meta: { waveform_index: 0, path: 0 } }),
      zeroState(1004, "q1"), event("stop", 1008, 1008, { sequencer_id: "q1" }), event("wait", 1008, 10_004, { sequencer_id: "q1" }),
    ] });
    assert.deepEqual(bounds(scale), [{ start: 20, end: 984 }]);
    assert.equal(scale.toDisplay(10_004) - scale.toDisplay(1000), 9004);
  });

  it("waits for a matched physical acquisition result instead of using the RT duration", () => {
    const events = [zeroState(), event("acquire", 100, 104, { meta: { rt_packet_id: "acq0" } }), event("wait", 104, 1004)];
    for (const integration_length_source of ["configured", "weight_memory"]) {
      const scale = buildWaitTimeScale({ events: [...events, event("acquisition_result", 600, 600, { meta: {
        rt_packet_id: "acq0", start_ns: 100, duration_ns: 500, integration_length_source,
      } })] });
      assert.deepEqual(bounds(scale), [{ start: 616, end: 988 }]);
      assert.equal(scale.toDisplay(600) - scale.toDisplay(100), 500);
    }
    for (const result of [undefined,
      { rt_packet_id: "other", start_ns: 100, duration_ns: 500, integration_length_source: "configured" },
      { rt_packet_id: "acq0", start_ns: 101, duration_ns: 500, integration_length_source: "configured" },
      { rt_packet_id: "acq0", start_ns: 100, duration_ns: 500, integration_length_source: "instruction_duration_fallback" },
    ]) {
      assert.deepEqual(buildWaitTimeScale({ events: result ? [...events, event("acquisition_result", 600, 600, { meta: result })] : events }).gaps, []);
    }
  });

  it("folds unknown initial and constant nonzero levels without treating pending setters as transitions", () => {
    assert.deepEqual(bounds(buildWaitTimeScale({ events: [event("wait", 0, 1000)] })), [{ start: 16, end: 984 }]);
    for (const meta of [{}, { applied_state: { awg_offset: [0, 0] } }, { applied_state: { marker: 0 } },
      { applied_state: { awg_offset: [1, 0], marker: 0 } }, { applied_state: { awg_offset: [0, { kind: "unknown" }], marker: 0 } },
    ]) {
      assert.deepEqual(buildWaitTimeScale({ events: [event("upd_param", 0, 4, { meta }), event("wait", 4, 1004),
        event("latched_state_pending", 100, 100, { lane: "q1.pending", meta: { field: "awg_offset", value: [0, 0] } }),
      ] }).gaps.map(({ start, end }) => ({ start, end })), [{ start: 20, end: 988 }]);
    }
    const scale = buildWaitTimeScale({ events: [
      event("upd_param", 0, 4, { meta: { applied_state: { awg_offset: [1, 0], marker: 0 } } }), event("wait", 4, 1000),
      event("latched_state_pending", 100, 100, { lane: "q1.pending", meta: { field: "awg_offset", value: [0, 0] } }),
      zeroState(1000), event("wait", 1004, 2004),
    ] });
    assert.deepEqual(bounds(scale), [{ start: 20, end: 984 }, { start: 1020, end: 1988 }]);
  });

  it("folds held nonzero markers and offsets while retaining their applied transitions", () => {
    const scale = buildWaitTimeScale({ events: [zeroState(), event("marker_state", 4, 8, { meta: { value: 1 } }),
      event("wait", 8, 1000), event("marker_state", 1000, 1004, { meta: { value: 0 } }), event("wait", 1004, 2004),
    ] });
    assert.deepEqual(bounds(scale), [{ start: 24, end: 984 }, { start: 1020, end: 1988 }]);
    assert.deepEqual(bounds(buildWaitTimeScale({ events: [zeroState(),
      event("upd_param", 4, 8, { meta: { applied_state: { awg_offset: [852, 0], marker: 2 } } }), event("wait", 8, 2004),
    ] })), [{ start: 24, end: 1988 }]);
  });

  it("does not treat unproven preview copies, assumed branches, unknown regions, or symbolic timing as unchanged", () => {
    for (const wait of [event("wait", 4, 1004, { meta: { preview_source_event_id: "original" } }),
      event("wait", 4, 1004, { confidence: "assumed", meta: { assumed_branch_path: "taken" } }),
      event("wait", 4, 1004, { confidence: "unknown" }), event("wait", 4, 1004, { t1: { kind: "symbolic", expr: "T" } }),
    ]) assert.deepEqual(buildWaitTimeScale({ events: [zeroState(), wait] }).gaps, []);
    for (const kind of ["unknown_region", "unknown_physical_operation"]) {
      assert.deepEqual(buildWaitTimeScale({ events: [zeroState(), event(kind, 4, 1004), event("wait", 4, 2004)] }).gaps, []);
    }
  });

  it("uses proven alignment offsets and rejects incompatible or uncertain clocks", () => {
    const events = [zeroState(), event("wait", 4, 2004), zeroState(0, "q1"), event("wait", 4, 2004, { sequencer_id: "q1" })];
    const ir = { events, alignment: { q0: exactAlignment(1000), q1: exactAlignment(2000) } };
    const before = JSON.stringify(ir);
    assert.deepEqual(bounds(buildWaitTimeScale(ir, { timeBasis: "aligned" })), [{ start: 2020, end: 2988 }]);
    assert.deepEqual(buildWaitTimeScale(ir, { timeBasis: "local" }).gaps, []);
    assert.deepEqual(bounds(buildWaitTimeScale({ events, alignment: { q0: exactAlignment(1000), q1: exactAlignment(1000) } })), [{ start: 20, end: 1988 }]);
    for (const q1 of [undefined, { ...exactAlignment(), unaligned: true }, { ...exactAlignment(), confidence: "unknown" }, { ...exactAlignment(), offset: { kind: "unknown" } }]) {
      assert.deepEqual(buildWaitTimeScale({ events, alignment: { q0: exactAlignment(), q1 } }, { timeBasis: "aligned" }).gaps, []);
    }
    assert.equal(JSON.stringify(ir), before);
  });

  it("does not fold after an unresolved conditional execution setter", () => {
    const events = [event("wait", 0, 1000),
      event("latched_state_pending", 1000, 1000, { meta: { field: "set_cond", value: "1,1,0,4" } }),
      event("loop_block", 1000, 66_532, { meta: { count: 6, wait_only: true, period: { kind: "concrete", value: 65_532 } } }),
      event("wait", 1000, 66_532), event("stop", 394_192),
    ];
    assert.deepEqual(bounds(buildWaitTimeScale({ events })), [{ start: 16, end: 984 }]);
  });

  it("keeps long waits foldable after disabled conditional execution", () => {
    const events = [zeroState(),
      event("latched_state_pending", 4, 4, { meta: { field: "set_cond", value: "0,0,0,4", conditional_enabled: false } }),
      event("q1_issue", 0, 4, { meta: { op: "set_cond", conditional_enabled: false } }),
      event("wait", 4, 65_539), event("stop", 65_539),
    ];
    const before = JSON.stringify(events);
    assert.deepEqual(bounds(buildWaitTimeScale({ events })), [{ start: 20, end: 65_523 }]);
    for (const conditional_enabled of [true, null]) {
      const conditional = { ...events[1], meta: { field: "set_cond", conditional_enabled } };
      assert.deepEqual(buildWaitTimeScale({ events: [events[0], conditional, ...events.slice(2)] }).gaps, []);
    }
    assert.equal(JSON.stringify(events), before);
  });

  it("does not recover a concrete wait clock when a conditional region is later disabled", () => {
    const unknown = { kind: "unknown", display: "unknown(conditional_execution)" };
    const events = [event("wait", 0, 1000),
      event("unknown_region", 1000, 1000, { t1: null, duration: unknown, confidence: "unknown",
        meta: { reason: "conditional_execution_unknown", conditional_time_unknown: true } }),
      event("latched_state_pending", 1000, 1000, { t0: unknown, t1: unknown,
        meta: { field: "set_cond", conditional_enabled: false, conditional_time_unknown: true } }),
      event("wait", 1000, 66_535, { t0: unknown, t1: unknown, duration: { kind: "concrete", value: 65_535 },
        meta: { conditional_time_unknown: true } }),
    ];
    assert.deepEqual(buildWaitTimeScale({ events }).gaps, []);
  });

  it("keeps interior point transitions visible even when the endpoint values match", () => {
    const scale = buildWaitTimeScale({
      alignment: { q0: exactAlignment(), q1: exactAlignment() },
      events: [
        event("wait", 0, 3000), event("wait", 0, 3000, { sequencer_id: "q1" }),
        event("marker_state", 0, 0, { sequencer_id: "q1", meta: { value: 1 } }),
        event("marker_state", 1000, 1000, { sequencer_id: "q1", meta: { value: 2 } }),
        event("marker_state", 2000, 2000, { sequencer_id: "q1", meta: { value: 1 } }),
        event("latched_state_pending", 500, 500, { sequencer_id: "q1", lane: "q1.pending", meta: { field: "marker", value: 2 } }),
        event("q1_issue", 1500, 1504, { sequencer_id: "q1", lane: "q1.issue" }),
      ],
    });
    assert.deepEqual(bounds(scale), [
      { start: 16, end: 984 }, { start: 1016, end: 1984 }, { start: 2016, end: 2984 },
    ]);
    for (const boundary of [1000, 2000]) {
      assert.equal(scale.toDisplay(boundary + 16) - scale.toDisplay(boundary - 16), 32);
    }
  });

  it("folds a proven six-iteration wait loop across the first 400 microseconds", () => {
    const loopMeta = {
      count: 6, period: { kind: "concrete", value: 65_532 }, wait_only: true,
      preview_policy: "first_iteration_only", shown_iterations: [0], visible_iteration_count: 1,
    };
    const loop = event("loop_block", 16, 65_548, { meta: loopMeta });
    const ir = {
      alignment: { q0: exactAlignment(), q1: exactAlignment() },
      events: [
        event("wait", 16, 65_548), loop, event("wait", 393_208, 399_992),
        event("wait", 65_548, 131_080, { meta: { preview_source_event_id: "first-wait" } }),
        event("play", 399_992, 399_996, { meta: { waveform_index: 0, path: 0 } }),
        event("wait", 16, 399_992, { sequencer_id: "q1" }),
        event("play", 399_992, 399_996, { sequencer_id: "q1", meta: { waveform_index: 0, path: 0 } }),
      ],
    };
    const scale = buildWaitTimeScale(ir);
    assert.deepEqual(bounds(scale), [{ start: 32, end: 65_532 }, { start: 393_224, end: 399_976 }]);
    assert.equal(scale.toDisplay(399_992), 327_868);
    assert.equal(scale.toDisplay(399_996) - scale.toDisplay(399_992), 4);
    closeTo(scale.fromDisplay(scale.toDisplay(200_000)), 200_000);
    loopMeta.wait_only = false;
    assert.deepEqual(bounds(buildWaitTimeScale(ir)), [{ start: 32, end: 65_532 }]);
    loopMeta.wait_only = true;
    assert.deepEqual(buildWaitTimeScale({ ...ir, events: [
      event("play", 0, 4, { meta: { waveform_index: 0, path: 0 } }), ...ir.events,
    ] }).gaps, []);
  });

  it("ignores queue-analysis diagnostics when folding known unchanged spans", () => {
    const events = [event("wait", 4, 400_004)];
    const diagnostic = event("analysis_incomplete", 100, 100, {
      lane: "debug.underflow", meta: { status: "analysis_incomplete", reason: "sync_start_uncertain" },
    });
    assert.deepEqual(bounds(buildWaitTimeScale({ events: [...events, diagnostic] })), [{ start: 20, end: 399_988 }]);
    assert.deepEqual(buildWaitTimeScale({ events: [...events, diagnostic] }).gaps, buildWaitTimeScale({ events }).gaps);
    assert.deepEqual(bounds(buildWaitTimeScale({ events: [...events, event("unknown_command", 100)] })), []);
  });

  it("preserves possible-underflow annotations but blocks time after actual underflow", () => {
    const events = [event("wait", 4, 1004)];
    const possible = event("underflow_warning", 500, 500, { lane: "debug.underflow", meta: { status: "possible_underflow" } });
    assert.deepEqual(bounds(buildWaitTimeScale({ events: [...events, possible] })), [
      { start: 20, end: 484 }, { start: 516, end: 988 },
    ]);
    const actual = event("underflow_warning", 500, 500, { lane: "debug.underflow", meta: { status: "underflow" } });
    assert.deepEqual(bounds(buildWaitTimeScale({ events: [...events, actual] })), [{ start: 20, end: 484 }]);
  });

  it("does not infer filter settling from applied zero offsets or decimated zero traces", () => {
    const events = [
      event("upd_param", 0, 4, { meta: { applied_state: { awg_offset: [1, 0], marker: 0 } } }),
      event("wait", 4, 1000), zeroState(1000), event("wait", 1004, 2004),
    ];
    const signals = {
      sample_period_ns: 1, export_stride: 1000, sampled_end_ns: 2004, truncated: false,
      traces: [{ stage: "dac", path: "i", t0: 0, dt_ns: 1000, samples: [0, 0, 0] }],
    };
    const identity = { fir: [1], iir: { a: [1], b: [1] } };
    const scale = buildWaitTimeScale({ events, simulation: { q0: {
      physical_enabled: true, signals: { ...signals, parameters: identity },
    } } });
    assert.deepEqual(bounds(scale), [{ start: 20, end: 984 }, { start: 1020, end: 1988 }]);
    for (const parameters of [undefined, {}, { ...identity, fir: [1, 0.5] },
      { ...identity, iir: { a: [1, -0.9], b: [1] } },
    ]) {
      assert.deepEqual(buildWaitTimeScale({ events, simulation: { q0: {
        physical_enabled: true, signals: { ...signals, parameters },
      } } }).gaps, []);
    }
  });

  it("requires a shared clock origin instead of accepting exact but arbitrary alignment", () => {
    const events = [zeroState(), event("wait", 4, 1004), zeroState(0, "q1"), event("wait", 4, 1004, { sequencer_id: "q1" })];
    for (const mode of [undefined, "none", "first_play", "first_acquire"]) {
      const alignment = { q0: { ...exactAlignment(), mode }, q1: { ...exactAlignment(), mode } };
      for (const timeBasis of ["aligned", "local"]) {
        assert.deepEqual(buildWaitTimeScale({ events, alignment }, { timeBasis }).gaps, []);
      }
    }
    const synchronized = { q0: { ...exactAlignment(), mode: "first_wait_sync" }, q1: { ...exactAlignment(), mode: "first_wait_sync" } };
    assert.deepEqual(bounds(buildWaitTimeScale({ events, alignment: synchronized })), [{ start: 20, end: 988 }]);
    const alignment = { q0: { ...exactAlignment(), mode: "none" }, q1: { ...exactAlignment(), mode: "none" } };
    const execution = { q0: { mode: "simulation", timing_model: "event_driven" }, q1: { mode: "simulation", timing_model: "event_driven" } };
    for (const timeBasis of ["aligned", "local"]) {
      assert.deepEqual(bounds(buildWaitTimeScale({ events, alignment, execution }, { timeBasis })), [{ start: 20, end: 988 }]);
      assert.deepEqual(buildWaitTimeScale({ events, alignment, execution: { q0: execution.q0 } }, { timeBasis }).gaps, []);
    }
  });
});

describe("playback wait and repeat boundaries", () => {
  function loop(id: string, start: number, firstEnd: number, end: number, hidden: string[]) {
    return event("loop_block", start, end, { id, meta: {
      playback: true, loop_preview_key: id, first_iteration_end: firstEnd, hidden_event_ids: hidden,
    } });
  }
  function combined(ir: any, options: ScaleOptions = {}) {
    const waits = buildWaitTimeScale(ir, options);
    return { waits, scale: buildTimelineTimeScale(ir, { ...options, waitGaps: waits.gaps }) };
  }

  it("keeps the end of the first 65535 ns wait outside the repeated interval", () => {
    const ir = { playback: {}, events: [
      event("upd_param", 0, 4),
      event("wait", 4, 65_539, { id: "first" }),
      event("wait", 65_539, 131_074, { id: "second" }),
      event("wait", 131_074, 196_609, { id: "third" }),
      loop("loop", 4, 65_539, 196_609, ["second", "third"]),
      event("upd_param", 196_609, 196_613),
    ] };
    const before = JSON.stringify(ir);
    const { waits, scale } = combined(ir);
    assert.deepEqual(bounds(waits), [{ start: 20, end: 65_523 }]);
    assert.deepEqual(scale.gaps.map(({ start, end, loopKeys }) => ({ start, end, loopKeys })), [
      { start: 20, end: 65_523, loopKeys: [] },
      { start: 65_539, end: 196_593, loopKeys: ["loop"] },
    ]);
    assert.equal(scale.toDisplay(65_539) - scale.toDisplay(65_523), 16);
    assert.equal(scale.toDisplay(65_539) - scale.toDisplay(4), 96);
    assert.equal(scale.toDisplay(196_613) - scale.toDisplay(196_609), 4);
    for (const time of [0, 4, 20, 30_000, 65_523, 65_539, 100_000, 196_593, 196_609, 196_613]) {
      closeTo(scale.fromDisplay(scale.toDisplay(time)), time);
    }
    const expanded = combined(ir, { expandedPlaybackLoops: ["loop"] });
    assert.deepEqual(bounds(expanded.waits), [{ start: 20, end: 196_593 }]);
    assert.ok(expanded.scale.gaps.every((gap) => !gap.loopKeys?.length));
    assert.equal(JSON.stringify(ir), before);
  });

  it("keeps a nested child's wait end when its parent expands", () => {
    const ir = { playback: {}, events: [
      event("wait", 0, 1000, { id: "first" }),
      event("wait", 1000, 2000, { id: "child-hidden" }),
      event("wait", 2000, 3000, { id: "tail" }),
      event("wait", 3000, 6000, { id: "parent-hidden" }),
      loop("child", 0, 1000, 2000, ["child-hidden"]),
      loop("parent", 0, 3000, 6000, ["parent-hidden"]),
    ] };
    const collapsed = combined(ir);
    assert.deepEqual(bounds(collapsed.waits), [{ start: 16, end: 984 }, { start: 2016, end: 2984 }]);
    const parentExpanded = combined(ir, { expandedPlaybackLoops: ["parent"] });
    assert.deepEqual(bounds(parentExpanded.waits), [{ start: 16, end: 984 }, { start: 2016, end: 5984 }]);
    assert.equal(parentExpanded.scale.toDisplay(1000) - parentExpanded.scale.toDisplay(984), 16);
    assert.deepEqual(parentExpanded.scale.gaps.filter((gap) => gap.loopKeys?.length).map((gap) => gap.loopKeys), [["child"]]);
    assert.deepEqual(bounds(combined(ir, { expandedPlaybackLoops: ["parent", "child"] }).waits), [{ start: 16, end: 5984 }]);
  });

  it("preserves the same wait end on aligned peer sequencers", () => {
    const ir = { playback: {}, alignment: { q0: exactAlignment(500), q1: exactAlignment(500) }, events: [
      event("wait", 4, 65_539, { id: "first" }),
      event("wait", 65_539, 196_609, { id: "hidden" }),
      loop("loop", 4, 65_539, 196_609, ["hidden"]),
      event("wait", 4, 196_609, { sequencer_id: "q1", id: "peer" }),
    ] };
    const { waits, scale } = combined(ir, { timeBasis: "aligned" });
    assert.deepEqual(bounds(waits), [{ start: 520, end: 66_023 }]);
    assert.equal(scale.toDisplay(66_039) - scale.toDisplay(66_023), 16);
    for (const time of [504, 520, 66_023, 66_039, 100_000, 197_109]) {
      closeTo(scale.fromDisplay(scale.toDisplay(time)), time);
    }
  });
});

describe("static compact loop time folding", () => {
  function loop(id: string, start: number, period: number, count: number, extra: Record<string, unknown> = {}) {
    return event("loop_block", start, start + period, { id, meta: {
      loop_preview_key: id, count, period: { kind: "concrete", value: period },
      preview_policy: "first_iteration_only", shown_iterations: [0], visible_iteration_count: 1, ...extra,
    } });
  }
  function combined(ir: any, options: ScaleOptions = {}) {
    const waits = buildWaitTimeScale(ir, options);
    return { waits, scale: buildTimelineTimeScale(ir, { ...options, waitGaps: waits.gaps }) };
  }

  it("folds the omitted disabled-example iterations while retaining its final RT update", () => {
    const ir = { alignment: { q0: exactAlignment() }, events: [
      event("latched_state_pending", 0, 0, { meta: { field: "set_cond", conditional_enabled: false } }),
      event("upd_param", 0, 4), event("wait", 4, 65_539), loop("disabled-loop", 4, 65_535, 3),
      event("upd_param", 196_609, 196_613), event("stop", 196_613),
    ] };
    const before = JSON.stringify(ir);
    const { waits, scale } = combined(ir);
    assert.deepEqual(bounds(waits), [{ start: 20, end: 65_523 }]);
    assert.deepEqual(scale.gaps.map(({ start, end, loopKeys }) => ({ start, end, loopKeys })), [
      { start: 20, end: 65_523, loopKeys: [] },
      { start: 65_539, end: 196_593, loopKeys: ["disabled-loop"] },
    ]);
    assert.equal(scale.toDisplay(65_539) - scale.toDisplay(65_523), 16);
    assert.equal(scale.toDisplay(196_613), 184);
    assert.equal(scale.toDisplay(196_613) - scale.toDisplay(196_609), 4);
    for (const time of [0, 4, 20, 30_000, 65_523, 65_539, 100_000, 196_593, 196_609, 196_613]) {
      closeTo(scale.fromDisplay(scale.toDisplay(time)), time);
    }
    assert.equal(JSON.stringify(ir), before);
  });

  it("keeps a proven wait-only first iteration separate from its omitted repetitions", () => {
    const ir = { events: [event("wait", 4, 65_536), loop("loop", 4, 65_532, 3, { wait_only: true }),
      event("upd_param", 196_600, 196_604)] };
    const { waits, scale } = combined(ir);
    assert.deepEqual(bounds(waits), [{ start: 20, end: 65_520 }]);
    assert.deepEqual(bounds(scale), [{ start: 20, end: 65_520 }, { start: 65_536, end: 196_584 }]);
    assert.equal(scale.toDisplay(65_536) - scale.toDisplay(65_520), 16);
  });

  it("starts after every requested visible iteration and rejects unresolved loop extents", () => {
    const compact: any = loop("loop", 4, 65_535, 3, {
      preview_policy: "requested_iterations", shown_iterations: [0, 1], visible_iteration_count: 2,
    });
    assert.deepEqual(bounds(buildTimelineTimeScale({ events: [compact] })), [{ start: 131_074, end: 196_609 }]);
    for (const item of [
      { ...compact, confidence: "unknown" },
      { ...compact, meta: { ...compact.meta, count: "forever" } },
      { ...compact, meta: { ...compact.meta, period: { kind: "unknown" } } },
      { ...compact, meta: { ...compact.meta, visible_iteration_count: 3 } },
    ]) assert.deepEqual(buildTimelineTimeScale({ events: [item] }).gaps, []);
  });

  it("preserves aligned peer activity inside an omitted static interval", () => {
    const ir = { alignment: { q0: exactAlignment(500), q1: exactAlignment(500) }, events: [
      event("wait", 4, 65_539), loop("loop", 4, 65_535, 3), event("upd_param", 196_609, 196_613),
      event("wait", 4, 196_609, { sequencer_id: "q1" }),
      event("marker_state", 100_000, 100_000, { sequencer_id: "q1" }),
    ] };
    const { scale } = combined(ir, { timeBasis: "aligned" });
    assert.deepEqual(bounds(scale), [
      { start: 520, end: 66_023 }, { start: 66_039, end: 100_484 }, { start: 100_516, end: 197_093 },
    ]);
    assert.equal(scale.toDisplay(100_516) - scale.toDisplay(100_484), 32);
    for (const time of [504, 66_039, 100_484, 100_500, 100_516, 197_113]) {
      closeTo(scale.fromDisplay(scale.toDisplay(time)), time);
    }
    assert.deepEqual(buildTimelineTimeScale({ ...ir, alignment: { q0: exactAlignment(), q1: { confidence: "unknown" } } }).gaps, []);
  });

  it("keeps nested omissions separate without subtracting repeated time twice", () => {
    const ir = { events: [event("wait", 0, 1000), loop("child", 0, 1000, 2, { wait_only: true }),
      event("wait", 2000, 3000), loop("parent", 0, 3000, 2), event("upd_param", 6000, 6004)] };
    const { scale } = combined(ir);
    assert.deepEqual(bounds(scale), [
      { start: 16, end: 984 }, { start: 1000, end: 2000 },
      { start: 2016, end: 2984 }, { start: 3000, end: 5984 },
    ]);
    for (const time of [0, 16, 984, 1000, 1500, 2000, 2016, 2984, 3000, 5000, 6004]) {
      closeTo(scale.fromDisplay(scale.toDisplay(time)), time);
    }
  });
});
