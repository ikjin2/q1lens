import assert from "node:assert/strict";
import { resolveQbsSelectionToQ1TimelineTarget } from "../../src/integration/q1timelineTarget";
import { QbsIr } from "../../src/qbs/qbsIr";

function ir(): QbsIr {
  return {
    schedule: { name: "unit" },
    operations: [{ id: "cz_q0_q1", label: "CZ(q0,q1)", abs_time: 44e-9, duration: 88e-9 }],
    symbolic_values: [],
    symbolic_pulses: [
      {
        id: "pulse:cz_q0_q1:pulse:0",
        operation_id: "cz_q0_q1",
        lane: "q0_q1:flux / cz",
        kind: "pulse",
        label: "CZFluxPulse",
        abs_time: 44e-9,
        duration: 88e-9,
      },
    ],
    q1asm_programs: [{ sequencer: "cluster0_module4_seq0", file: "q1asm/cluster0_module4_seq0.q1asm" }],
    q1asm_by_sequencer: { cluster0_module4_seq0: "wait_sync 4\nwait 44\nplay 0,0,8\nstop\n" },
    q1asm_provenance: [
      {
        sequencer: "cluster0_module4_seq0",
        line: 3,
        instruction: "play",
        operation_id: "cz_q0_q1",
        symbolic_value_id: "value:t_cz",
      },
    ],
  };
}

describe("QBS to q1timeline target resolution", () => {
  it("builds an explicit q1timeline target from QBS provenance", () => {
    const target = resolveQbsSelectionToQ1TimelineTarget({
      outputDir: "C:\\repo\\.qbs_timeline",
      ir: {
        schedule: { name: "unit" },
        operations: [{ id: "cz", label: "CZ", abs_time: 0, duration: 1e-9 }],
        symbolic_values: [],
        symbolic_pulses: [],
        q1asm_programs: [{ sequencer: "seq0", file: "q1asm/seq0.q1asm" }],
        q1asm_provenance: [{ sequencer: "seq0", line: 5, q1asm_line_start: 3, operation_id: "cz", instruction: "play" }],
      },
      selection: { operationId: "cz" },
    });

    assert.equal(target.projectFile.replace(/\\/g, "/"), "C:/repo/.qbs_timeline/q1timeline.yml");
    assert.equal(target.q1asmFile?.replace(/\\/g, "/"), "C:/repo/.qbs_timeline/q1asm/seq0.q1asm");
    assert.equal(target.sequencer, "seq0");
    assert.equal(target.line, 3);
    assert.equal(target.operationId, "cz");
  });

  it("resolves an operation selection to a q1timeline target", () => {
    const target = resolveQbsSelectionToQ1TimelineTarget({
      ir: ir(),
      selection: { operationId: "cz_q0_q1" },
      outputDir: "C:\\repo\\.qbs_timeline",
    });

    assert.equal(target.projectFile.replace(/\\/g, "/"), "C:/repo/.qbs_timeline/q1timeline.yml");
    assert.equal(target.sequencer, "cluster0_module4_seq0");
    assert.equal(target.line, 3);
    assert.equal(target.q1asmFile?.replace(/\\/g, "/"), "C:/repo/.qbs_timeline/q1asm/cluster0_module4_seq0.q1asm");
  });

  it("uses q1asm_line_start as the q1timeline target line when provenance provides a raw range", () => {
    const payload = ir();
    payload.q1asm_provenance[0] = {
      ...payload.q1asm_provenance[0],
      line: 5,
      q1asm_line_start: 3,
      q1asm_line_end: 4,
    };

    const target = resolveQbsSelectionToQ1TimelineTarget({
      ir: payload,
      selection: { operationId: "cz_q0_q1" },
      outputDir: "C:\\repo\\.qbs_timeline",
    });

    assert.equal(target.line, 3);
  });

  it("prefers the timed operand mapping line inside a provenance range", () => {
    const payload = ir();
    payload.q1asm_provenance[0] = {
      ...payload.q1asm_provenance[0],
      line: 5,
      q1asm_line_start: 2,
      q1asm_line_end: 3,
      operand_mappings: [{ line: 3, instruction: "play" }],
    };

    const target = resolveQbsSelectionToQ1TimelineTarget({
      ir: payload,
      selection: { operationId: "cz_q0_q1" },
      outputDir: "C:\\repo\\.qbs_timeline",
    });

    assert.equal(target.line, 3);
  });

  it("uses acquisition variant operand mapping lines inside a provenance range", () => {
    const payload = ir();
    payload.q1asm_provenance[0] = {
      ...payload.q1asm_provenance[0],
      line: undefined,
      q1asm_line_start: 2,
      q1asm_line_end: 3,
      operand_mappings: [{ line: 3, instruction: "acquire_ttl" }],
    };

    const target = resolveQbsSelectionToQ1TimelineTarget({
      ir: payload,
      selection: { operationId: "cz_q0_q1" },
      outputDir: "C:\\repo\\.qbs_timeline",
    });

    assert.equal(target.line, 3);
  });

  it("uses wait and upd_param operand mapping lines inside a provenance range", () => {
    for (const instruction of ["wait", "upd_param"]) {
      const payload = ir();
      payload.q1asm_provenance[0] = {
        ...payload.q1asm_provenance[0],
        line: undefined,
        q1asm_line_start: 1,
        q1asm_line_end: 3,
        operand_mappings: [{ line: 3, instruction }],
      };

      const target = resolveQbsSelectionToQ1TimelineTarget({
        ir: payload,
        selection: { operationId: "cz_q0_q1" },
        outputDir: "C:\\repo\\.qbs_timeline",
      });

      assert.equal(target.line, 3);
    }
  });

  it("does not let explicit webview start lines bypass exact block provenance", () => {
    const payload = ir();
    payload.q1asm_provenance[0] = {
      ...payload.q1asm_provenance[0],
      source_id: "pulse:cz_q0_q1:pulse:0",
      q1asm_line_start: 2,
      q1asm_line_end: 3,
      operand_mappings: [{ line: 3, instruction: "play" }],
    };

    const target = resolveQbsSelectionToQ1TimelineTarget({
      ir: payload,
      selection: {
        blockId: "pulse:cz_q0_q1:pulse:0",
        operationId: "cz_q0_q1",
        sequencer: "cluster0_module4_seq0",
        line: 2,
      },
      outputDir: "C:\\repo\\.qbs_timeline",
    });

    assert.equal(target.line, 3);
  });

  it("prefers the requested sequencer for block-specific q1timeline targets", () => {
    const payload = ir();
    payload.q1asm_programs = [
      { sequencer: "seq0", file: "q1asm/seq0.q1asm" },
      { sequencer: "seq1", file: "q1asm/seq1.q1asm" },
    ];
    payload.q1asm_provenance = [
      {
        source_id: "pulse:cz_q0_q1:pulse:0",
        sequencer: "seq0",
        q1asm_line_start: 2,
      },
      {
        source_id: "pulse:cz_q0_q1:pulse:0",
        sequencer: "seq1",
        q1asm_line_start: 10,
      },
    ];

    const target = resolveQbsSelectionToQ1TimelineTarget({
      ir: payload,
      selection: {
        blockId: "pulse:cz_q0_q1:pulse:0",
        operationId: "cz_q0_q1",
        sequencer: "seq1",
        line: 10,
      },
      outputDir: "C:\\repo\\.qbs_timeline",
    });

    assert.equal(target.sequencer, "seq1");
    assert.equal(target.line, 10);
    assert.equal(target.q1asmFile?.replace(/\\/g, "/"), "C:/repo/.qbs_timeline/q1asm/seq1.q1asm");
  });

  it("resolves q1asm program files by sequencer_id alias", () => {
    const payload = ir();
    payload.q1asm_programs = [{ sequencer: "pretty-seq", sequencer_id: "seq0", file: "q1asm/seq0.q1asm" } as any];
    payload.q1asm_provenance[0] = {
      ...payload.q1asm_provenance[0],
      sequencer: "seq0",
    };

    const target = resolveQbsSelectionToQ1TimelineTarget({
      ir: payload,
      selection: { operationId: "cz_q0_q1" },
      outputDir: "C:\\repo\\.qbs_timeline",
    });

    assert.equal(target.q1asmFile?.replace(/\\/g, "/"), "C:/repo/.qbs_timeline/q1asm/seq0.q1asm");
    assert.equal(target.sequencer, "seq0");
  });

  it("resolves a symbolic block selection through its operation", () => {
    const target = resolveQbsSelectionToQ1TimelineTarget({
      ir: ir(),
      selection: { blockId: "pulse:cz_q0_q1:pulse:0" },
      outputDir: "C:\\repo\\.qbs_timeline",
    });

    assert.equal(target.operationId, "cz_q0_q1");
    assert.equal(target.line, 3);
  });

  it("resolves generated provenance when schedulable id differs from operation id", () => {
    const payload = ir();
    payload.operations = [{ id: "x180", operation_id: "x_q0", label: "X(q0)", abs_time: 20e-9, duration: 40e-9 }];
    payload.symbolic_pulses = [
      {
        id: "pulse:x180:pulse:0",
        operation_id: "x_q0",
        schedulable_id: "x180",
        lane: "q0:mw / q0.01",
        kind: "pulse",
        label: "DRAGPulse",
        abs_time: 20e-9,
        duration: 40e-9,
      },
    ];
    payload.q1asm_programs = [{ sequencer: "cluster0_module2_seq0", file: "q1asm/cluster0_module2_seq0.q1asm" }];
    payload.q1asm_provenance = [
      {
        sequencer: "cluster0_module2_seq0",
        schedulable_id: "x180",
        source_id: "pulse:x180:pulse:0",
        q1asm_line_start: 4,
        q1asm_line_end: 6,
        symbolic_value_id: "value:t_total",
      },
    ];

    const target = resolveQbsSelectionToQ1TimelineTarget({
      ir: payload,
      selection: { blockId: "pulse:x180:pulse:0" },
      outputDir: "C:\\repo\\.qbs_timeline",
    });

    assert.equal(target.sequencer, "cluster0_module2_seq0");
    assert.equal(target.line, 4);
    assert.equal(target.operationId, "x_q0");
    assert.equal(target.symbolicValueId, "value:t_total");
  });

  it("prioritizes exact source_id provenance over shared operation matches", () => {
    const payload = ir();
    payload.symbolic_pulses = [
      {
        id: "pulse:measure:pulse:0",
        operation_id: "measure",
        schedulable_id: "measure",
        lane: "q0:res",
        kind: "pulse",
        label: "ReadoutPulse",
        abs_time: 0,
        duration: 20e-9,
      },
      {
        id: "acq:measure:acquisition:0",
        operation_id: "measure",
        schedulable_id: "measure",
        lane: "q0:res",
        kind: "acquisition",
        label: "Acquisition",
        abs_time: 20e-9,
        duration: 240e-9,
      },
    ];
    payload.q1asm_programs = [{ sequencer: "seq0", file: "q1asm/seq0.q1asm" }];
    payload.q1asm_provenance = [
      { source_id: "pulse:measure:pulse:0", operation_id: "measure", sequencer: "seq0", q1asm_line_start: 2 },
      { source_id: "acq:measure:acquisition:0", operation_id: "measure", sequencer: "seq0", q1asm_line_start: 4 },
    ];

    const target = resolveQbsSelectionToQ1TimelineTarget({
      ir: payload,
      selection: { blockId: "acq:measure:acquisition:0", operationId: "measure" },
      outputDir: "C:\\repo\\.qbs_timeline",
    });

    assert.equal(target.line, 4);
  });

  it("falls back to the first generated program when provenance is missing", () => {
    const payload = ir();
    payload.q1asm_provenance = [];

    const target = resolveQbsSelectionToQ1TimelineTarget({
      ir: payload,
      selection: { operationId: "cz_q0_q1" },
      outputDir: "C:\\repo\\.qbs_timeline",
    });

    assert.equal(target.sequencer, "cluster0_module4_seq0");
    assert.equal(target.line, 1);
    assert.equal(target.q1asmFile?.replace(/\\/g, "/"), "C:/repo/.qbs_timeline/q1asm/cluster0_module4_seq0.q1asm");
  });

  it("rejects q1timeline targets that escape the generated output directory", () => {
    assert.throws(
      () =>
        resolveQbsSelectionToQ1TimelineTarget({
          ir: ir(),
          selection: { operationId: "cz_q0_q1", file: "../outside.q1asm" },
          outputDir: "C:\\repo\\.qbs_timeline",
        }),
      /escapes output directory/,
    );
  });
});
