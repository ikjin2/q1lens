import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { getOperationById, getScheduleTitle, parseQbsIrText } from "../src/qbs/qbsIr";

const fixturePath = join(__dirname, "..", "..", "test", "fixtures", "two-qubit-ir.json");

describe("qbsIr", () => {
  it("parses the two-qubit fixture", () => {
    const ir = parseQbsIrText(readFileSync(fixturePath, "utf8"));

    assert.equal(getScheduleTitle(ir), "two-qubit entangling demo");
    assert.equal(ir.operations.length, 8);
    assert.ok(ir.symbolic_pulses.some((block) => block.lane === "q0_q1:flux / cz"));
    assert.ok(ir.q1asm_programs.some((program) => program.sequencer === "cluster0_module4_seq0"));
    assert.equal(ir.project?.root, "<fixture-root>");
  });

  it("finds operations by id", () => {
    const ir = parseQbsIrText(readFileSync(fixturePath, "utf8"));
    const operation = getOperationById(ir, "cz_q0_q1");

    assert.equal(operation.label, "CZ(q0,q1)");
    assert.equal(operation.duration, 88e-9);
  });

  it("rejects IR without operations", () => {
    assert.throws(
      () => parseQbsIrText(JSON.stringify({ schedule: { name: "broken" } })),
      /QBS IR must contain operations\[\]/,
    );
  });

  it("normalizes provenance records for downstream navigation", () => {
    const ir = parseQbsIrText(readFileSync(fixturePath, "utf8"));
    const czMapping = ir.q1asm_provenance.find((mapping) => mapping.operation_id === "cz_q0_q1");

    assert.equal(czMapping?.sequencer, "cluster0_module4_seq0");
    assert.equal(czMapping?.line, 5);
    assert.equal(czMapping?.instruction, "play");
    assert.equal(czMapping?.symbolic_value_id, "value:t_cz");
    assert.equal(czMapping?.expression, "T_CZ - 8 ns setup");
  });

  it("preserves provenance instruction roles for display fallbacks", () => {
    const ir = parseQbsIrText(JSON.stringify({
      operations: [{ id: "measure_q0", label: "Measure(q0)", abs_time: 0, duration: 160e-9 }],
      q1asm_provenance: [{
        sequencer_id: "cluster0_module2_seq0",
        q1asm_line_start: 5,
        q1asm_line_end: 5,
        source_id: "pulse:measure:pulse:0",
        instruction_roles: ["play"],
      }],
    }));

    assert.deepEqual(ir.q1asm_provenance[0].instruction_roles, ["play"]);
  });

  it("preserves top-level provenance line and metadata", () => {
    const ir = parseQbsIrText(JSON.stringify({
      operations: [{ id: "op", label: "Op", abs_time: 0, duration: 1e-9 }],
      q1asm_provenance: [{
        sequencer_id: "seq0",
        line: 7,
        instruction: "play",
        operation_id: "op",
        symbolic_value_id: "value:t",
        confidence: "compiler",
        inference_reason: "native sidecar",
      }],
    }));

    assert.equal(ir.q1asm_provenance[0].line, 7);
    assert.equal(ir.q1asm_provenance[0].instruction, "play");
    assert.equal(ir.q1asm_provenance[0].operation_id, "op");
    assert.equal(ir.q1asm_provenance[0].symbolic_value_id, "value:t");
    assert.equal((ir.q1asm_provenance[0] as any).confidence, "compiler");
    assert.equal((ir.q1asm_provenance[0] as any).inference_reason, "native sidecar");
  });

  it("preserves schedule source maps for navigation", () => {
    const ir = parseQbsIrText(JSON.stringify({
      operations: [{ id: "measure", label: "Measure(q0)", abs_time: 0, duration: 160e-9 }],
      q1asm_provenance: [],
      source_map: {
        schedulables: {
          measure: { file: "schedule.py", line: 42, column: 4, label: "measure" },
        },
      },
    }));

    assert.equal(ir.source_map?.schedulables?.measure.line, 42);
  });

  it("preserves notebook source map metadata", () => {
    const ir = parseQbsIrText(JSON.stringify({
      operations: [{ id: "measure", label: "Measure q0", abs_time: 0, duration: 0 }],
      symbolic_values: [],
      symbolic_pulses: [],
      q1asm_programs: [],
      q1asm_provenance: [],
      source_map: {
        primary: {
          kind: "notebook",
          file: "examples/050_qubit_spectroscopy.ipynb",
          generated_file: ".scratch/probe/schedule.py",
        },
        schedulables: {
          measure: {
            kind: "notebook",
            file: "examples/050_qubit_spectroscopy.ipynb",
            line: 1,
            column: 0,
            label: "measure",
            notebook: {
              file: "examples/050_qubit_spectroscopy.ipynb",
              cell_index: 12,
              cell_id: "cell-12",
              cell_line: 4,
            },
            generated_file: ".scratch/probe/notebook_cells.py",
            generated_line: 45,
          },
        },
      },
    }));

    assert.equal(ir.source_map?.primary?.kind, "notebook");
    assert.equal(ir.source_map?.primary?.generated_file, ".scratch/probe/schedule.py");
    assert.equal(ir.source_map?.schedulables?.measure.kind, "notebook");
    assert.equal(ir.source_map?.schedulables?.measure.notebook?.cell_index, 12);
    assert.equal(ir.source_map?.schedulables?.measure.generated_line, 45);
  });

  it("preserves control-flow blocks and child operation metadata", () => {
    const ir = parseQbsIrText(JSON.stringify({
      operations: [
        { id: "loop", label: "LoopOperation", abs_time: 0, duration: 120e-9 },
        {
          id: "loop/body0",
          label: "X(q0)",
          abs_time: 5e-9,
          duration: 20e-9,
          parent_control_flow_id: "control-flow:loop",
          depth: 1,
        },
      ],
      control_flow_blocks: [
        {
          id: "control-flow:loop",
          kind: "loop",
          label: "Loop x3",
          abs_time: 0,
          duration: 120e-9,
          preview_abs_time: 5e-9,
          preview_duration: 20e-9,
          duration_kind: "expanded",
          preview_kind: "first_iteration",
          iteration: {
            kind: "domain",
            variable: "freq",
            count: 3,
          },
          schedulable_id: "loop",
          operation_id: "loop_operation",
          parent_control_flow_id: "control-flow:outer",
          depth: 1,
          repetitions: 3,
          body_operation_count: 1,
        },
      ],
    }));

    const controlFlowBlocks = ir.control_flow_blocks ?? [];
    assert.equal(controlFlowBlocks[0].kind, "loop");
    assert.equal(controlFlowBlocks[0].preview_abs_time, 5e-9);
    assert.equal(controlFlowBlocks[0].preview_duration, 20e-9);
    assert.equal(controlFlowBlocks[0].duration_kind, "expanded");
    assert.equal(controlFlowBlocks[0].preview_kind, "first_iteration");
    assert.deepEqual(controlFlowBlocks[0].iteration, { kind: "domain", variable: "freq", count: 3 });
    assert.equal(controlFlowBlocks[0].repetitions, 3);
    assert.equal(controlFlowBlocks[0].parent_control_flow_id, "control-flow:outer");
    assert.equal(controlFlowBlocks[0].depth, 1);
    assert.equal(ir.operations[1].parent_control_flow_id, "control-flow:loop");
    assert.equal(ir.operations[1].depth, 1);
  });

  it("preserves structured IR diagnostics", () => {
    const ir = parseQbsIrText(JSON.stringify({
      operations: [{ id: "op", label: "Op", abs_time: 0, duration: 1e-9 }],
      ir_diagnostics: [
        {
          code: "duplicate_id",
          path: "symbolic_pulses",
          message: "id appears twice",
          severity: "error",
        },
        { code: 123, path: "bad", message: "bad" },
      ],
    }));

    assert.deepEqual(ir.ir_diagnostics, [
      {
        code: "duplicate_id",
        path: "symbolic_pulses",
        message: "id appears twice",
        severity: "error",
      },
    ]);
  });
});
