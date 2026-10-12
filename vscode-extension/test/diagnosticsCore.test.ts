import assert from "node:assert/strict";
import { computeDiagnostics } from "../src/qbs/diagnosticsCore";
import { QbsIr } from "../src/qbs/qbsIr";

function baseIr(): QbsIr {
  return {
    schedule: { name: "unit" },
    operations: [{ id: "op", label: "Op", abs_time: 10, duration: 5 }],
    symbolic_values: [{ id: "value:t", label: "T", value: 5, unit: "s", kind: "duration" }],
    symbolic_pulses: [
      {
        id: "block",
        operation_id: "op",
        lane: "q0",
        kind: "pulse",
        label: "Pulse",
        abs_time: 10,
        duration: 5,
        duration_value_id: "value:t",
      },
    ],
    q1asm_programs: [{ sequencer: "seq0", file: "q1asm/seq0.q1asm", text: "wait 10\nplay 0,1,5\nstop\n" }],
    q1asm_by_sequencer: { seq0: "wait 10\nplay 0,1,5\nstop\n" },
    q1asm_provenance: [{ sequencer: "seq0", line: 2, instruction: "play", operation_id: "op", symbolic_value_id: "value:t" }],
  };
}

function syncAlignedIr(syncDuration = 4, syncStart = 0, waitDuration = 20): QbsIr {
  const ir = baseIr();
  ir.operations = [{ id: "op", label: "Op", abs_time: 0, duration: 100e-9 }];
  ir.symbolic_pulses[0] = { ...ir.symbolic_pulses[0], abs_time: 20e-9, duration: 20e-9 };
  ir.q1asm_by_sequencer = { seq0: `wait_sync ${syncDuration}\nwait ${waitDuration}\nplay 0,1,20\nstop\n` };
  ir.q1asm_programs[0] = { ...ir.q1asm_programs[0], text: ir.q1asm_by_sequencer.seq0 };
  ir.q1asm_provenance[0] = { sequencer: "seq0", source_id: "block", line: 3, instruction: "play" };
  const sync = event("wait_sync", syncStart, syncStart + syncDuration, 1, `wait_sync ${syncDuration}`);
  ir.q1timeline_ir = {
    alignment: { seq0: { mode: "first_wait_sync", anchor_event_id: sync.id, confidence: "exact", unaligned: false } },
    events: [
      sync,
      event("wait", syncStart + syncDuration, syncStart + syncDuration + waitDuration, 2, `wait ${waitDuration}`),
      event("play", syncStart + syncDuration + waitDuration, syncStart + syncDuration + waitDuration + 20, 3, "play 0,1,20"),
    ],
  };
  return ir;
}

function crossLayerWarnings(ir: QbsIr) {
  return computeDiagnostics(ir, {
    outputDir: "/repo/.qbs_timeline",
    existingFiles: new Set(ir.q1asm_programs.map((program) => program.file)),
  }).filter((diagnostic) => diagnostic.code === "QBST007");
}

describe("diagnosticsCore", () => {
  it("reports QBST001 when a symbolic block exceeds its operation window", () => {
    const ir = baseIr();
    ir.symbolic_pulses[0] = { ...ir.symbolic_pulses[0], abs_time: 14, duration: 3 };

    const diagnostics = computeDiagnostics(ir, {
      outputDir: "/repo/.qbs_timeline",
      existingFiles: new Set(["q1asm/seq0.q1asm"]),
    });

    assert.equal(diagnostics[0].code, "QBST001");
  });

  it("reports QBST002 when provenance points past the Q1ASM program", () => {
    const ir = baseIr();
    ir.q1asm_provenance[0] = { ...ir.q1asm_provenance[0], line: 99 };

    const diagnostics = computeDiagnostics(ir, {
      outputDir: "/repo/.qbs_timeline",
      existingFiles: new Set(["q1asm/seq0.q1asm"]),
    });

    assert.equal(diagnostics[0].code, "QBST002");
  });

  it("resolves Q1ASM diagnostics through sequencer_id aliases", () => {
    const ir = baseIr();
    ir.q1asm_programs = [{ sequencer: "pretty-seq", sequencer_id: "seq0", file: "q1asm/seq0.q1asm", text: "stop\n" } as any];
    ir.q1asm_by_sequencer = { seq0: "stop\n" };
    ir.q1asm_provenance = [{ sequencer: "seq0", line: 2, instruction: "play" } as any];

    const diagnostics = computeDiagnostics(ir, {
      outputDir: "/repo/.qbs_timeline",
      existingFiles: new Set(["q1asm/seq0.q1asm"]),
    });

    assert.equal(diagnostics[0].code, "QBST002");
    assert.equal(diagnostics[0].file, "q1asm/seq0.q1asm");
  });

  it("reads embedded Q1ASM text through sequencer_id aliases", () => {
    const ir = baseIr();
    ir.q1asm_programs = [{ sequencer: "pretty-seq", sequencer_id: "seq0", file: "q1asm/seq0.q1asm" } as any];
    ir.q1asm_by_sequencer = { seq0: "wait 4\nplay 0,1,8\nstop\n" };
    ir.q1asm_provenance = [
      {
        sequencer: "pretty-seq",
        q1asm_line_start: 2,
        q1asm_line_end: 2,
        instruction_roles: ["play"],
      } as any,
    ];

    const diagnostics = computeDiagnostics(ir, {
      outputDir: "/repo/.qbs_timeline",
      existingFiles: new Set(["q1asm/seq0.q1asm"]),
    });

    assert.equal(diagnostics.some((diagnostic) => diagnostic.code === "QBST002" || diagnostic.code === "QBST003"), false);
  });

  it("reports QBST003 when the Q1ASM instruction text does not match", () => {
    const ir = baseIr();
    ir.q1asm_provenance[0] = { ...ir.q1asm_provenance[0], instruction: "acquire" };

    const diagnostics = computeDiagnostics(ir, {
      outputDir: "/repo/.qbs_timeline",
      existingFiles: new Set(["q1asm/seq0.q1asm"]),
    });

    assert.equal(diagnostics[0].code, "QBST003");
  });

  it("reports QBST002 for range-only provenance outside the Q1ASM program", () => {
    const ir = baseIr();
    ir.q1asm_by_sequencer = { seq0: "wait 4\nstop\n" };
    ir.q1asm_provenance[0] = {
      sequencer: "seq0",
      q1asm_line_start: 3,
      q1asm_line_end: 3,
      instruction_roles: ["acquire"],
      operation_id: "op",
    };

    const diagnostics = computeDiagnostics(ir, {
      outputDir: "/repo/.qbs_timeline",
      existingFiles: new Set(["q1asm/seq0.q1asm"]),
    });

    assert.equal(diagnostics[0].code, "QBST002");
  });

  it("reports QBST003 for range-only provenance instruction role mismatches", () => {
    const ir = baseIr();
    ir.q1asm_by_sequencer = { seq0: "wait 4\nstop\n" };
    ir.q1asm_provenance[0] = {
      sequencer: "seq0",
      q1asm_line_start: 1,
      q1asm_line_end: 1,
      instruction_roles: ["acquire"],
      operation_id: "op",
    };

    const diagnostics = computeDiagnostics(ir, {
      outputDir: "/repo/.qbs_timeline",
      existingFiles: new Set(["q1asm/seq0.q1asm"]),
    });

    assert.equal(diagnostics[0].code, "QBST003");
  });

  it("accepts range-only provenance when the instruction role appears inside the range", () => {
    const ir = baseIr();
    ir.q1asm_by_sequencer = { seq0: "set_awg_gain 1,0\nplay 0,1,40\nstop\n" };
    ir.q1asm_programs[0] = { ...ir.q1asm_programs[0], text: ir.q1asm_by_sequencer.seq0 };
    ir.q1asm_provenance[0] = {
      sequencer: "seq0",
      q1asm_line_start: 1,
      q1asm_line_end: 2,
      instruction_roles: ["play"],
      operation_id: "op",
    };

    const diagnostics = computeDiagnostics(ir, {
      outputDir: "/repo/.qbs_timeline",
      existingFiles: new Set(["q1asm/seq0.q1asm"]),
    });

    assert.equal(diagnostics.some((diagnostic) => diagnostic.code === "QBST003"), false);
  });

  it("reports QBST003 when any instruction role is missing from a provenance range", () => {
    const ir = baseIr();
    ir.q1asm_by_sequencer = { seq0: "set_awg_gain 100,0\nstop\n" };
    ir.q1asm_programs[0] = { ...ir.q1asm_programs[0], text: ir.q1asm_by_sequencer.seq0 };
    ir.q1asm_provenance[0] = {
      sequencer: "seq0",
      q1asm_line_start: 1,
      q1asm_line_end: 2,
      instruction_roles: ["set_awg_gain", "play"],
      operation_id: "op",
    };

    const diagnostics = computeDiagnostics(ir, {
      outputDir: "/repo/.qbs_timeline",
      existingFiles: new Set(["q1asm/seq0.q1asm"]),
    });

    assert.equal(diagnostics.some((diagnostic) => diagnostic.code === "QBST003"), true);
  });

  it("validates instruction roles against the canonical range when operand line is present", () => {
    const ir = baseIr();
    ir.q1asm_by_sequencer = { seq0: "set_awg_gain 1,0\nplay 0,1,4\nwait 36\nstop\n" };
    ir.q1asm_programs[0] = { ...ir.q1asm_programs[0], text: ir.q1asm_by_sequencer.seq0 };
    ir.q1asm_provenance[0] = {
      sequencer: "seq0",
      line: 2,
      q1asm_line_start: 1,
      q1asm_line_end: 3,
      instruction_roles: ["set_awg_gain", "play", "wait"],
      operand_mappings: [{ line: 2, instruction: "play" }],
      operation_id: "op",
    };

    const diagnostics = computeDiagnostics(ir, {
      outputDir: "/repo/.qbs_timeline",
      existingFiles: new Set(["q1asm/seq0.q1asm"]),
    });

    assert.equal(diagnostics.some((diagnostic) => diagnostic.code === "QBST003"), false);
  });

  it("does not let wait_sync satisfy an expected timed wait instruction", () => {
    const ir = baseIr();
    ir.q1asm_by_sequencer = { seq0: "wait_sync 4\nstop\n" };
    ir.q1asm_programs[0] = { ...ir.q1asm_programs[0], text: ir.q1asm_by_sequencer.seq0 };
    ir.q1asm_provenance[0] = {
      sequencer: "seq0",
      q1asm_line_start: 1,
      q1asm_line_end: 1,
      instruction_roles: ["wait"],
      operation_id: "op",
    };

    const diagnostics = computeDiagnostics(ir, {
      outputDir: "/repo/.qbs_timeline",
      existingFiles: new Set(["q1asm/seq0.q1asm"]),
    });

    assert.equal(diagnostics.some((diagnostic) => diagnostic.code === "QBST003"), true);
  });

  it("reports QBST007 when provenance-mapped q1timeline spans drift from QBS intent", () => {
    const ir = baseIr();
    ir.operations = [{ id: "op", label: "Op", abs_time: 0, duration: 100e-9 }];
    ir.symbolic_pulses[0] = {
      ...ir.symbolic_pulses[0],
      id: "pulse:x180:pulse:0",
      abs_time: 20e-9,
      duration: 40e-9,
    };
    ir.q1asm_by_sequencer = {
      seq0: "wait_sync 4\nupd_param 4\nwait 16\nset_awg_gain 32767,0\nplay 0,1,4\nwait 36\nstop\n",
    };
    ir.q1asm_programs[0] = { ...ir.q1asm_programs[0], text: ir.q1asm_by_sequencer.seq0 };
    ir.q1asm_provenance[0] = {
      sequencer: "seq0",
      source_id: "pulse:x180:pulse:0",
      q1asm_line_start: 4,
      q1asm_line_end: 6,
      instruction_roles: ["set_awg_gain", "play", "wait"],
      operation_id: "op",
    };
    ir.q1timeline_ir = {
      events: [
        event("q1_issue", 12, 16, 4, "set_awg_gain 32767,0"),
        event("play", 24, 28, 5, "play 0,1,4"),
        event("wait", 28, 64, 6, "wait 36"),
      ],
    };

    const diagnostics = computeDiagnostics(ir, {
      outputDir: "/repo/.qbs_timeline",
      existingFiles: new Set(["q1asm/seq0.q1asm"]),
    });

    const diagnostic = diagnostics.find((item) => item.code === "QBST007");
    assert.equal(diagnostic?.severity, "warning");
    assert.equal(diagnostic?.blockId, "pulse:x180:pulse:0");
    assert.equal(diagnostic?.sequencer, "seq0");
    assert.match(diagnostic?.message ?? "", /QBS 20-60 ns, q1timeline 24-64 ns/);
  });

  it("compares schedule time with completion of the declared first wait_sync anchor", () => {
    for (const [duration, start] of [[4, 0], [8, 0], [8, 200]]) {
      const ir = syncAlignedIr(duration, start);
      const before = JSON.stringify(ir.q1timeline_ir);
      assert.deepEqual(crossLayerWarnings(ir), [], `wait_sync ${duration} at ${start} ns`);
      assert.equal(JSON.stringify(ir.q1timeline_ir), before, "comparison must not change runtime or display timestamps");
    }
  });

  it("normalizes local timestamps for after_first_wait_sync without applying display metadata twice", () => {
    const ir = syncAlignedIr(8, 200);
    const timeline: any = ir.q1timeline_ir;
    timeline.alignment.seq0.mode = "after_first_wait_sync";
    timeline.alignment.seq0.offset = -208;
    for (const item of timeline.events) {
      item.meta = { aligned_t0: item.t0.value - 208, aligned_t1: item.t1.value - 208 };
    }
    const before = JSON.stringify(timeline);
    assert.deepEqual(crossLayerWarnings(ir), []);
    assert.equal(JSON.stringify(timeline), before);
    timeline.events[timeline.events.length - 1].t0.value += 4;
    timeline.events[timeline.events.length - 1].t1.value += 4;
    assert.match(crossLayerWarnings(ir)[0].message, /QBS 20-40 ns, q1timeline 24-44 ns/);
  });

  it("continues to report an extra 4 ns wait after synchronization", () => {
    const diagnostics = crossLayerWarnings(syncAlignedIr(8, 200, 24));
    assert.equal(diagnostics.length, 1);
    assert.match(diagnostics[0].message, /QBS 20-40 ns, q1timeline 24-44 ns/);
  });

  it("normalizes each declared sequencer anchor and leaves later synchronization delays intact", () => {
    const ir = syncAlignedIr(8, 200);
    ir.q1asm_programs[0] = { ...ir.q1asm_programs[0], sequencer: "display-name", sequencer_id: "seq0" };
    ir.q1asm_provenance[0].sequencer = "display-name";
    const timeline: any = ir.q1timeline_ir;
    timeline.events.unshift({ ...event("wait_sync", 0, 4, 1, "wait_sync 4"), id: "other:sync", sequencer_id: "other" });
    timeline.alignment.other = { mode: "first_wait_sync", anchor_event_id: "other:sync", confidence: "exact" };
    assert.deepEqual(crossLayerWarnings(ir), []);
    timeline.events.splice(2, 0, event("wait_sync", 228, 232, 2, "wait_sync 4"));
    timeline.events[timeline.events.length - 1] = event("play", 232, 252, 3, "play 0,1,20");
    assert.match(crossLayerWarnings(ir)[0].message, /QBS 20-40 ns, q1timeline 24-44 ns/);
  });

  it("keeps raw-time comparison for IR without a declared post-sync basis", () => {
    for (const mode of [undefined, "none", "first_play"]) {
      const ir = syncAlignedIr();
      const timeline: any = ir.q1timeline_ir;
      if (mode === undefined) delete timeline.alignment;
      else timeline.alignment.seq0.mode = mode;
      assert.match(crossLayerWarnings(ir)[0].message, /QBS 20-40 ns, q1timeline 24-44 ns/);
    }
  });

  it("does not assert a timing mismatch when the declared synchronization anchor is unavailable", () => {
    const cases = ["missing", "unknown-end", "wrong-kind", "wrong-sequencer", "uncertain", "unaligned"];
    for (const kind of cases) {
      const ir = syncAlignedIr();
      const timeline: any = ir.q1timeline_ir;
      if (kind === "missing") timeline.alignment.seq0.anchor_event_id = "missing";
      if (kind === "unknown-end") timeline.events[0].t1 = { kind: "unknown", value: 4 };
      if (kind === "wrong-kind") timeline.events[0].kind = "q1_issue";
      if (kind === "wrong-sequencer") timeline.events[0].sequencer_id = "other";
      if (kind === "uncertain") timeline.alignment.seq0.confidence = "unknown";
      if (kind === "unaligned") timeline.alignment.seq0.unaligned = true;
      assert.deepEqual(crossLayerWarnings(ir), [], kind);
    }
  });

  it("reports QBST004 when a duration symbolic value is missing", () => {
    const ir = baseIr();
    ir.symbolic_pulses[0] = { ...ir.symbolic_pulses[0], duration_value_id: "value:missing" };

    const diagnostics = computeDiagnostics(ir, {
      outputDir: "/repo/.qbs_timeline",
      existingFiles: new Set(["q1asm/seq0.q1asm"]),
    });

    assert.equal(diagnostics[0].code, "QBST004");
  });

  it("reports QBST005 when a generated Q1ASM file is missing", () => {
    const diagnostics = computeDiagnostics(baseIr(), { outputDir: "/repo/.qbs_timeline", existingFiles: new Set() });

    assert.equal(diagnostics[0].code, "QBST005");
  });

  it("includes q1timeline analyzer diagnostics", () => {
    const ir = baseIr() as any;
    ir.q1timeline_diagnostics = [
      {
        category: "possible_underflow",
        message: "possible_underflow: slack = -4 ns.",
        severity: "warning",
        source: { file: "/repo/.qbs_timeline/q1asm/seq0.q1asm", line: 2 },
      },
    ];

    const diagnostics = computeDiagnostics(ir, {
      outputDir: "/repo/.qbs_timeline",
      existingFiles: new Set(["q1asm/seq0.q1asm"]),
    });

    assert.equal(diagnostics[0].code, "possible_underflow");
    assert.equal(diagnostics[0].source, "q1timeline");
    assert.equal(diagnostics[0].file, "/repo/.qbs_timeline/q1asm/seq0.q1asm");
    assert.equal(diagnostics[0].line, 2);
  });

  it("includes top-level QBS IR warnings", () => {
    const ir = baseIr() as any;
    ir.warnings = ["compile failed; rendered compact source preview only: boom"];

    const diagnostics = computeDiagnostics(ir, {
      outputDir: "/repo/.qbs_timeline",
      existingFiles: new Set(["q1asm/seq0.q1asm"]),
    });

    assert.equal(diagnostics.some((diagnostic) => diagnostic.code === "QBST006"), true);
  });

  it("includes structured IR invariant diagnostics", () => {
    const ir = baseIr() as any;
    ir.ir_diagnostics = [
      {
        code: "duplicate_id",
        path: "symbolic_pulses",
        message: "id appears twice",
        severity: "error",
      },
    ];

    const diagnostics = computeDiagnostics(ir, {
      outputDir: "/repo/.qbs_timeline",
      existingFiles: new Set(["q1asm/seq0.q1asm"]),
    });

    const diagnostic = diagnostics.find((item) => item.code === "QBST-IR-duplicate_id");
    assert.equal(diagnostic?.severity, "error");
    assert.equal(diagnostic?.source, "qbsTimeline");
    assert.equal(diagnostic?.message, "symbolic_pulses: id appears twice");
  });
});

function event(kind: string, t0: number, t1: number, line: number, raw: string) {
  return {
    id: `seq0:${kind}:${line}`,
    kind,
    sequencer_id: "seq0",
    t0: { kind: "concrete", value: t0, display: String(t0) },
    t1: { kind: "concrete", value: t1, display: String(t1) },
    duration: { kind: "concrete", value: t1 - t0, display: String(t1 - t0) },
    source: { file: "/repo/.qbs_timeline/q1asm/seq0.q1asm", line, raw },
  };
}
