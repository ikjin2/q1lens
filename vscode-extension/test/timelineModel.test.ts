import assert from "node:assert/strict";

function loadModel(): any {
  return require("../src/qbs/webview/assets/timelineModel.js");
}

function ir() {
  return {
    schedule: { name: "unit" },
    operations: [
      { id: "reset_q0", label: "Reset(q0)", abs_time: 0, duration: 40e-9 },
      { id: "cz_q0_q1", label: "CZ(q0,q1)", abs_time: 44e-9, duration: 88e-9 },
    ],
    symbolic_values: [
      { id: "value:t_cz", label: "T_CZ", value: 88e-9, unit: "s", kind: "time" },
    ],
    symbolic_pulses: [
      {
        id: "pulse:cz",
        schedulable_id: "cz",
        operation_id: "cz_q0_q1",
        lane: "q0_q1:flux / cz",
        kind: "pulse",
        label: "CZFluxPulse",
        abs_time: 44e-9,
        duration: 88e-9,
        duration_value_id: "value:t_cz",
      },
    ],
    q1asm_programs: [{ sequencer: "cluster0_module4_seq0", file: "q1asm/cluster0_module4_seq0.q1asm" }],
    q1asm_provenance: [
      { sequencer: "cluster0_module4_seq0", line: 3, instruction: "play", operation_id: "cz_q0_q1", symbolic_value_id: "value:t_cz" },
    ],
  };
}

function loopIr() {
  return {
    schedule: { name: "loop unit" },
    operations: [
      {
        id: "loop",
        operation_id: "loop_operation",
        label: "LoopOperation",
        abs_time: 0,
        duration: 120e-9,
      },
      {
        id: "loop/body0",
        operation_id: "body_pulse",
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
        operation_id: "loop_operation",
        schedulable_id: "loop",
        repetitions: 3,
        body_operation_count: 1,
      },
    ],
    symbolic_values: [],
    symbolic_pulses: [],
    q1asm_programs: [],
    q1asm_provenance: [],
  };
}

function nestedSweepLoopIr() {
  return {
    schedule: { name: "nested sweep loop unit" },
    operations: [
      { id: "sweep", operation_id: "sweep_operation", label: "LoopOperation", abs_time: 0, duration: 220e-9 },
      {
        id: "sweep/set_offset",
        operation_id: "set_offset",
        label: "VoltageOffset",
        abs_time: 0,
        duration: 0,
        parent_control_flow_id: "control-flow:sweep",
        depth: 1,
      },
      {
        id: "sweep/set_freq",
        operation_id: "set_frequency",
        label: "SetClockFrequency",
        abs_time: 0,
        duration: 0,
        parent_control_flow_id: "control-flow:sweep",
        depth: 1,
      },
      {
        id: "sweep/reset_q0",
        operation_id: "reset",
        label: "Reset q0",
        abs_time: 0,
        duration: 40e-9,
        parent_control_flow_id: "control-flow:sweep",
        depth: 1,
      },
      {
        id: "sweep/loop",
        operation_id: "loop_operation",
        label: "LoopOperation",
        abs_time: 45e-9,
        duration: 120e-9,
        parent_control_flow_id: "control-flow:sweep",
        depth: 1,
      },
      {
        id: "sweep/loop/measure_q0",
        operation_id: "measure",
        label: "Measure q0",
        abs_time: 50e-9,
        duration: 80e-9,
        parent_control_flow_id: "control-flow:sweep/loop",
        depth: 2,
      },
      {
        id: "sweep/idle",
        operation_id: "idle",
        label: "IdlePulse",
        abs_time: 170e-9,
        duration: 4e-9,
        parent_control_flow_id: "control-flow:sweep",
        depth: 1,
      },
    ],
    control_flow_blocks: [
      {
        id: "control-flow:sweep",
        kind: "sweep",
        label: "Sweep x200",
        abs_time: 0,
        duration: 220e-9,
        operation_id: "sweep_operation",
        schedulable_id: "sweep",
        repetitions: 200,
        body_operation_count: 5,
      },
      {
        id: "control-flow:sweep/loop",
        kind: "loop",
        label: "Loop x1000",
        abs_time: 45e-9,
        duration: 120e-9,
        operation_id: "loop_operation",
        schedulable_id: "sweep/loop",
        parent_control_flow_id: "control-flow:sweep",
        depth: 1,
        repetitions: 1000,
        body_operation_count: 1,
      },
    ],
    symbolic_values: [],
    symbolic_pulses: [],
    q1asm_programs: [],
    q1asm_provenance: [],
  };
}

function untimedNestedSweepLoopIr() {
  return {
    schedule: { name: "untimed nested sweep loop unit" },
    operations: [
      { id: "sweep", operation_id: "sweep_operation", label: "LoopOperation", abs_time: 0, duration: 0 },
      {
        id: "sweep/set_offset_on",
        operation_id: "set_offset_on",
        label: "VoltageOffset",
        abs_time: 0,
        duration: 0,
        parent_control_flow_id: "control-flow:sweep",
        depth: 1,
      },
      {
        id: "sweep/set_freq",
        operation_id: "set_frequency",
        label: "SetClockFrequency",
        abs_time: 0,
        duration: 0,
        parent_control_flow_id: "control-flow:sweep",
        depth: 1,
      },
      {
        id: "sweep/reset_q0",
        operation_id: "reset",
        label: "Reset q0",
        abs_time: 0,
        duration: 0,
        parent_control_flow_id: "control-flow:sweep",
        depth: 1,
      },
      {
        id: "sweep/loop",
        operation_id: "loop_operation",
        label: "LoopOperation",
        abs_time: 0,
        duration: 0,
        parent_control_flow_id: "control-flow:sweep",
        depth: 1,
      },
      {
        id: "sweep/loop/measure_q0",
        operation_id: "measure",
        label: "Measure q0",
        abs_time: 0,
        duration: 0,
        parent_control_flow_id: "control-flow:sweep/loop",
        depth: 2,
      },
      {
        id: "sweep/set_offset_off",
        operation_id: "set_offset_off",
        label: "VoltageOffset",
        abs_time: 0,
        duration: 0,
        parent_control_flow_id: "control-flow:sweep",
        depth: 1,
      },
      {
        id: "sweep/idle",
        operation_id: "idle",
        label: "IdlePulse",
        abs_time: 0,
        duration: 4e-9,
        parent_control_flow_id: "control-flow:sweep",
        depth: 1,
      },
    ],
    control_flow_blocks: [
      {
        id: "control-flow:sweep",
        kind: "sweep",
        label: "Sweep x200",
        abs_time: 0,
        duration: 0,
        operation_id: "sweep_operation",
        schedulable_id: "sweep",
        repetitions: 200,
        body_operation_count: 6,
      },
      {
        id: "control-flow:sweep/loop",
        kind: "loop",
        label: "Loop x1000",
        abs_time: 0,
        duration: 0,
        operation_id: "loop_operation",
        schedulable_id: "sweep/loop",
        parent_control_flow_id: "control-flow:sweep",
        depth: 1,
        repetitions: 1000,
        body_operation_count: 1,
      },
    ],
    symbolic_values: [],
    symbolic_pulses: [],
    q1asm_programs: [],
    q1asm_provenance: [],
  };
}

function zeroDurationControlFlowBodySpanIr() {
  return {
    schedule: { name: "zero-duration control-flow span unit" },
    operations: [
      { id: "sweep", operation_id: "sweep_operation", label: "SweepOperation", abs_time: 0, duration: 0 },
      {
        id: "sweep/loop",
        operation_id: "loop_operation",
        label: "LoopOperation",
        abs_time: 10e-9,
        duration: 0,
        parent_control_flow_id: "control-flow:sweep",
        depth: 1,
      },
      {
        id: "sweep/loop/body0",
        operation_id: "body_pulse",
        label: "X(q0)",
        abs_time: 10e-9,
        duration: 20e-9,
        parent_control_flow_id: "control-flow:sweep/loop",
        depth: 2,
      },
      {
        id: "sweep/body1",
        operation_id: "idle",
        label: "IdlePulse",
        abs_time: 40e-9,
        duration: 15e-9,
        parent_control_flow_id: "control-flow:sweep",
        depth: 1,
      },
      { id: "after", operation_id: "after", label: "After(q0)", abs_time: 100e-9, duration: 20e-9 },
    ],
    control_flow_blocks: [
      {
        id: "control-flow:sweep",
        kind: "sweep",
        label: "Sweep x4",
        abs_time: 0,
        duration: 180e-9,
        preview_abs_time: 10e-9,
        preview_duration: 45e-9,
        duration_kind: "expanded",
        preview_kind: "first_iteration",
        iteration: { kind: "manual_sweep", variable: "amp", source: "amp_points", count: 4 },
        operation_id: "sweep_operation",
        schedulable_id: "sweep",
        repetitions: 4,
        body_operation_count: 2,
      },
      {
        id: "control-flow:sweep/loop",
        kind: "loop",
        label: "Loop x2",
        abs_time: 10e-9,
        duration: 40e-9,
        preview_abs_time: 10e-9,
        preview_duration: 20e-9,
        duration_kind: "expanded",
        preview_kind: "first_iteration",
        iteration: { kind: "domain", variable: "rep", count: 2 },
        operation_id: "loop_operation",
        schedulable_id: "sweep/loop",
        parent_control_flow_id: "control-flow:sweep",
        depth: 1,
        repetitions: 2,
        body_operation_count: 1,
      },
    ],
    symbolic_values: [],
    symbolic_pulses: [],
    q1asm_programs: [],
    q1asm_provenance: [],
  };
}

function overlappingOperationOnlyIr() {
  return {
    schedule: { name: "overlapping operation-only unit" },
    operations: [
      {
        id: "e686385f-2c26-4143-8587-6dc07278da57/1abdfe79-5077-4757-8bb0-822d885efbe6",
        operation_id: "square_0",
        label: "SquarePulse",
        abs_time: 0,
        duration: 300e-9,
      },
      {
        id: "e686385f-2c26-4143-8587-6dc07278da57/7ba2465c-70df-45fd-b472-e8c109d59f56",
        operation_id: "ramp_0",
        label: "RampPulse",
        abs_time: 0,
        duration: 400e-9,
      },
      {
        id: "e686385f-2c26-4143-8587-6dc07278da57/569d2a25-f629-445d-852b-2da6d02aff76",
        operation_id: "square_1",
        label: "SquarePulse",
        abs_time: 0,
        duration: 100e-9,
      },
    ],
    control_flow_blocks: [],
    symbolic_values: [],
    symbolic_pulses: [],
    q1asm_programs: [],
    q1asm_provenance: [],
  };
}

describe("timelineModel", () => {
  it("formats seconds into readable engineering units", () => {
    const { formatDuration } = loadModel();

    assert.equal(formatDuration(88e-9), "88 ns");
    assert.equal(formatDuration(1.25e-6), "1.25 us");
    assert.equal(formatDuration(0), "0 ns");
  });

  it("builds aligned operation and pulse lanes on one schedule-time axis", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(ir(), "pulse:cz", undefined, { expandedGroups: ["target:q0_q1"] });

    assert.equal(model.totalLabel, "132 ns");
    assert.deepEqual(model.ticks.map((tick: { label: string }) => tick.label), ["0 ns", "33 ns", "66 ns", "99 ns", "132 ns"]);
    assert.ok(model.lanes.some((lane: { label: string }) => lane.label === "Schedule / q0"));
    const pulseLane = model.lanes.find((lane: { label: string }) => lane.label === "q0_q1:flux / cz");
    assert.equal(pulseLane.blocks[0].leftPercent, 33.333);
    assert.equal(pulseLane.blocks[0].widthPercent, 66.667);
  });

  it("renders only high-level schedule-time lanes in the main timeline", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(ir(), "pulse:cz", undefined, { expandedGroups: ["target:q0_q1"] });

    assert.ok(model.lanes.some((lane: { kind: string }) => lane.kind === "target"));
    assert.ok(model.lanes.some((lane: { kind: string }) => lane.kind === "pulse"));
    assert.equal(
      model.lanes.some((lane: { kind: string }) => lane.kind === "provenance"),
      false,
    );
    assert.equal(
      model.lanes.some((lane: { label: string }) => lane.label === "cluster0_module4_seq0"),
      false,
    );
  });

  it("groups operation lanes by parsed operation target when available", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(ir(), "cz_q0_q1");

    assert.ok(model.lanes.some((lane: { label: string }) => lane.label === "Schedule / q0"));
    assert.ok(model.lanes.some((lane: { label: string }) => lane.label === "Schedule / q0_q1"));
    const czLane = model.lanes.find((lane: { label: string }) => lane.label === "Schedule / q0_q1");
    assert.equal(czLane.blocks[0].id, "cz_q0_q1");
  });

  it("builds target lane disclosure rows collapsed by default", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.operations.push({ id: "measure_q0", label: "Measure(q0)", abs_time: 164e-9, duration: 160e-9 });
    payload.symbolic_pulses.push({
      id: "pulse:measure",
      operation_id: "measure_q0",
      lane: "q0:res / q0.ro",
      role: "pulse",
      kind: "SquarePulse",
      label: "Readout(q0)",
      abs_time: 164e-9,
      duration: 160e-9,
    });

    const model = buildTimelineModel(payload, "pulse:cz");
    const q0Target = model.lanes.find((lane: { groupId?: string }) => lane.groupId === "target:q0");

    assert.equal(q0Target.kind, "target");
    assert.equal(q0Target.label, "Schedule / q0");
    assert.equal(q0Target.expandable, true);
    assert.equal(q0Target.expanded, false);
    assert.equal(q0Target.childrenCount, 1);
    assert.equal(model.lanes.some((lane: { label: string }) => lane.label === "q0:res / q0.ro"), false);
  });

  it("builds loop bracket rows while keeping first-iteration operations visible", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(loopIr(), "control-flow:loop");
    const loopLane = model.lanes.find((lane: { groupId?: string }) => lane.groupId === "control-flow:loop");
    const blocks = model.lanes.flatMap((lane: { blocks: any[] }) => lane.blocks);

    assert.equal(model.totalLabel, "120 ns");
    assert.equal(loopLane.kind, "control-flow");
    assert.equal(loopLane.label, "Loop x3");
    assert.equal(loopLane.expandable, false);
    assert.equal(loopLane.expanded, false);
    assert.equal(loopLane.childrenCount, 0);
    assert.equal(loopLane.blocks[0].id, "control-flow:loop");
    assert.equal(loopLane.blocks[0].visualKind, "loop");
    assert.equal(loopLane.blocks[0].selected, true);
    assert.equal(model.lanes.some((lane: { label: string }) => lane.label === "Loop body"), false);
    assert.equal(blocks.some((block: { id: string }) => block.id === "loop/body0"), true);
    assert.equal(
      blocks.some((block: { id: string }) => block.id === "loop"),
      false,
    );
  });

  it("does not duplicate first-iteration body operations from control-flow expansion state", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(loopIr(), "loop/body0", undefined, {
      expandedGroups: ["control-flow:loop"],
    });
    const loopLane = model.lanes.find((lane: { groupId?: string }) => lane.groupId === "control-flow:loop");
    const bodyBlocks = model.lanes
      .flatMap((lane: { blocks: any[] }) => lane.blocks)
      .filter((block: { id: string }) => block.id === "loop/body0");

    assert.equal(loopLane.expanded, false);
    assert.equal(model.lanes.some((lane: { label: string }) => lane.label === "Loop body"), false);
    assert.equal(bodyBlocks.length, 1);
    assert.equal(bodyBlocks[0].parentControlFlowId, "control-flow:loop");
    assert.equal(bodyBlocks[0].selected, true);
  });

  it("renders nested sweep and loop brackets around visible physical first-iteration operations", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(nestedSweepLoopIr(), "sweep/loop/measure_q0");
    const sweepLane = model.lanes.find((lane: { groupId?: string }) => lane.groupId === "control-flow:sweep");
    const loopLane = model.lanes.find((lane: { groupId?: string }) => lane.groupId === "control-flow:sweep/loop");
    const blocks = model.lanes.flatMap((lane: { blocks: any[] }) => lane.blocks);

    assert.equal(sweepLane.label, "Sweep x200");
    assert.equal(sweepLane.blocks[0].visualKind, "sweep");
    assert.equal(loopLane.label, "Loop x1000");
    assert.equal(loopLane.blocks[0].visualKind, "loop");
    assert.equal(loopLane.parentGroupId, "control-flow:sweep");
    assert.equal(loopLane.depth, 1);
    assert.equal(model.lanes.some((lane: { label: string }) => lane.label === "Loop body"), false);
    for (const id of ["sweep/set_offset", "sweep/set_freq", "sweep/reset_q0", "sweep/loop/measure_q0", "sweep/idle"]) {
      assert.equal(blocks.some((block: { id: string }) => block.id === id), true);
    }
    assert.equal(blocks.some((block: { id: string }) => block.id === "sweep"), false);
    assert.equal(blocks.some((block: { id: string }) => block.id === "sweep/loop"), false);
    assert.equal(blocks.find((block: { id: string }) => block.id === "sweep/loop/measure_q0").selected, true);
  });

  it("lays out untimed nested control-flow bodies in source order", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(untimedNestedSweepLoopIr(), "sweep/loop/measure_q0");
    const blocks = new Map<string, any>(
      model.lanes
        .flatMap((lane: { blocks: any[] }) => lane.blocks)
        .map((block: any) => [block.id, block] as [string, any]),
    );

    const setOffsetOn = blocks.get("sweep/set_offset_on");
    const setFrequency = blocks.get("sweep/set_freq");
    const reset = blocks.get("sweep/reset_q0");
    const loop = blocks.get("control-flow:sweep/loop");
    const measure = blocks.get("sweep/loop/measure_q0");
    const setOffsetOff = blocks.get("sweep/set_offset_off");
    const idle = blocks.get("sweep/idle");
    const sweep = blocks.get("control-flow:sweep");

    assert.ok(setOffsetOn.leftPercent < setFrequency.leftPercent);
    assert.ok(setFrequency.leftPercent < reset.leftPercent);
    assert.ok(reset.leftPercent < loop.leftPercent);
    assert.equal(loop.leftPercent, measure.leftPercent);
    assert.ok(measure.leftPercent < setOffsetOff.leftPercent);
    assert.ok(setOffsetOff.leftPercent < idle.leftPercent);
    assert.ok(sweep.widthPercent > idle.widthPercent);
    assert.equal(measure.detail, "untimed");
    assert.equal(loop.detail, "first iteration");
    assert.equal(model.totalLabel, "9 ns");
  });

  it("renders loop and sweep brackets from compact preview span while preserving expanded duration", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(zeroDurationControlFlowBodySpanIr(), "control-flow:sweep");
    const blocks = new Map<string, any>(
      model.lanes
        .flatMap((lane: { blocks: any[] }) => lane.blocks)
        .map((block: any) => [block.id, block] as [string, any]),
    );

    const sweep = blocks.get("control-flow:sweep");
    const loop = blocks.get("control-flow:sweep/loop");

    assert.equal(model.totalLabel, "120 ns");
    assert.equal(sweep.label, "Sweep amp x4");
    assert.equal(sweep.leftPercent, 8.333);
    assert.equal(sweep.widthPercent, 37.5);
    assert.equal(sweep.startLabel, "10 ns");
    assert.equal(sweep.durationLabel, "body 45 ns · total 180 ns");
    assert.equal(sweep.detail, "amp in amp_points");
    assert.equal(loop.label, "Loop rep x2");
    assert.equal(loop.leftPercent, 8.333);
    assert.equal(loop.widthPercent, 16.667);
    assert.equal(loop.startLabel, "10 ns");
    assert.equal(loop.durationLabel, "body 20 ns · total 40 ns");
  });

  it("hides generated Qblox loop variable names in control-flow labels", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = zeroDurationControlFlowBodySpanIr();
    payload.control_flow_blocks[0] = {
      ...payload.control_flow_blocks[0],
      kind: "loop",
      label: "Loop x100",
      repetitions: 100,
      iteration: {
        kind: "domain",
        variable: "Vare50d1570402b488d856129dbb3026738",
        count: 100,
      },
    };
    payload.control_flow_blocks[1] = {
      ...payload.control_flow_blocks[1],
      kind: "sweep",
      label: "Sweep x50",
      repetitions: 50,
      iteration: {
        kind: "domain",
        variable: "Var14f4a9a4ce60450a937901ac18e0cc3b",
        count: 50,
      },
    };

    const model = buildTimelineModel(payload, "control-flow:sweep");
    const blocks = new Map<string, any>(
      model.lanes
        .flatMap((lane: { blocks: any[] }) => lane.blocks)
        .map((block: any) => [block.id, block] as [string, any]),
    );

    assert.equal(blocks.get("control-flow:sweep").label, "Loop x100");
    assert.equal(blocks.get("control-flow:sweep").detail, "body 45 ns");
    assert.equal(blocks.get("control-flow:sweep/loop").label, "Sweep x50");
    assert.equal(blocks.get("control-flow:sweep/loop").detail, "body 20 ns");
  });

  it("lays out untimed symbolic pulse lanes from source-order operation positions", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = untimedNestedSweepLoopIr();
    payload.symbolic_pulses = [
      {
        id: "pulse:set_offset_on",
        operation_id: "set_offset_on",
        schedulable_id: "sweep/set_offset_on",
        lane: "q0:gt / cl0.baseband",
        role: "pulse",
        kind: "offset",
        abs_time: 0,
        duration: 1e-9,
      },
      {
        id: "pulse:reset",
        operation_id: "reset",
        schedulable_id: "sweep/reset_q0",
        lane: "q0:gt / cl0.baseband",
        role: "pulse",
        kind: "reset",
        abs_time: 0,
        duration: 1e-9,
      },
      {
        id: "pulse:idle",
        operation_id: "idle",
        schedulable_id: "sweep/idle",
        lane: "q0:gt / cl0.baseband",
        role: "pulse",
        kind: "idle",
        abs_time: 0,
        duration: 4e-9,
      },
    ];

    const model = buildTimelineModel(payload, "pulse:reset", undefined, { expandedGroups: ["target:q0"] });
    const pulseLane = model.lanes.find((lane: { label: string }) => lane.label === "q0:gt / cl0.baseband");

    assert.equal(pulseLane.blocks.length, 3);
    assert.ok(pulseLane.blocks[0].leftPercent < pulseLane.blocks[1].leftPercent);
    assert.ok(pulseLane.blocks[1].leftPercent < pulseLane.blocks[2].leftPercent);
    assert.equal(new Set(pulseLane.blocks.map((block: { topPx?: number }) => block.topPx)).size, 1);
  });

  it("stacks overlapping operation-only blocks in a shared target lane", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(overlappingOperationOnlyIr(), "ramp_0");
    const scheduleLane = model.lanes.find((lane: { label: string }) => lane.label === "Schedule");

    assert.equal(scheduleLane.kind, "target");
    assert.equal(scheduleLane.blocks.length, 3);
    assert.ok(scheduleLane.trackHeightPx > 48);
    assert.deepEqual(scheduleLane.blocks.map((block: { stackIndex?: number }) => block.stackIndex), [0, 1, 2]);
    assert.equal(new Set(scheduleLane.blocks.map((block: { topPx?: number }) => block.topPx)).size, 3);
  });

  it("expands target disclosure rows from view state", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.operations.push({ id: "measure_q0", label: "Measure(q0)", abs_time: 164e-9, duration: 160e-9 });
    payload.symbolic_pulses.push({
      id: "pulse:measure",
      operation_id: "measure_q0",
      lane: "q0:res / q0.ro",
      role: "pulse",
      kind: "SquarePulse",
      label: "Readout(q0)",
      abs_time: 164e-9,
      duration: 160e-9,
    });

    const model = buildTimelineModel(payload, "pulse:measure", undefined, { expandedGroups: ["target:q0"] });
    const q0Target = model.lanes.find((lane: { groupId?: string }) => lane.groupId === "target:q0");
    const child = model.lanes.find((lane: { label: string }) => lane.label === "q0:res / q0.ro");

    assert.equal(q0Target.expanded, true);
    assert.equal(child.parentGroupId, "target:q0");
    assert.equal(child.depth, 1);
    assert.equal(child.blocks[0].id, "pulse:measure");
  });

  it("marks related operation and symbolic blocks for the current selection", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(ir(), "pulse:cz", undefined, { expandedGroups: ["target:q0_q1"] });
    const blocks = model.lanes.flatMap((lane: { blocks: any[] }) => lane.blocks);

    const operation = blocks.find((block: { id: string }) => block.id === "cz_q0_q1");
    const pulse = blocks.find((block: { id: string }) => block.id === "pulse:cz");

    assert.equal(operation.relatedSelected, true);
    assert.equal(pulse.selected, true);
  });

  it("adds a full q1timeline open message to symbolic blocks with provenance", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(ir(), "pulse:cz", undefined, { expandedGroups: ["target:q0_q1"] });
    const pulse = model.lanes.flatMap((lane: { blocks: any[] }) => lane.blocks).find((block: any) => block.id === "pulse:cz");

    assert.deepEqual(pulse.q1timelineMessage, {
      type: "openQ1Timeline",
      blockId: "pulse:cz",
      operationId: "cz_q0_q1",
      sequencer: "cluster0_module4_seq0",
      line: 3,
    });
  });

  it("adds schedule source messages to QBS operation and symbolic blocks", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(ir(), "pulse:cz", undefined, { expandedGroups: ["target:q0_q1"] });
    const blocks = model.lanes.flatMap((lane: { blocks: any[] }) => lane.blocks);
    const operation = blocks.find((block: any) => block.id === "cz_q0_q1");
    const pulse = blocks.find((block: any) => block.id === "pulse:cz");

    assert.deepEqual(operation.scheduleSourceMessage, {
      type: "openScheduleSource",
      schedulableId: "cz_q0_q1",
      operationId: "cz_q0_q1",
      blockId: "cz_q0_q1",
    });
    assert.deepEqual(pulse.scheduleSourceMessage, {
      type: "openScheduleSource",
      schedulableId: "cz",
      operationId: "cz_q0_q1",
      blockId: "pulse:cz",
    });
  });

  it("uses semantic operation ids for operation-only schedule source messages", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.operations = [
      { id: "generated-node/0", operation_id: "square_0", label: "Square", abs_time: 0, duration: 20e-9 },
    ];
    payload.symbolic_pulses = [];
    payload.q1asm_provenance = [];

    const model = buildTimelineModel(payload, "generated-node/0");
    const operation = model.lanes.flatMap((lane: { blocks: any[] }) => lane.blocks).find((block: any) => block.id === "generated-node/0");

    assert.deepEqual(operation.scheduleSourceMessage, {
      type: "openScheduleSource",
      schedulableId: "square_0",
      operationId: "square_0",
      blockId: "generated-node/0",
    });
  });

  it("builds operation-block Q1ASM preview from schedulable-only provenance", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.operations = [{ id: "x180", operation_id: "x_q0", label: "X(q0)", abs_time: 20e-9, duration: 40e-9 }];
    payload.symbolic_pulses = [
      {
        id: "pulse:x180:pulse:0",
        operation_id: "x_q0",
        schedulable_id: "x180",
        lane: "q0:mw",
        kind: "pulse",
        label: "DRAGPulse",
        abs_time: 20e-9,
        duration: 40e-9,
      },
    ];
    payload.q1asm_by_sequencer = { seq0: "wait 20\nset_awg_gain 1,0\nplay 0,1,40\nstop\n" };
    payload.q1asm_programs = [{ sequencer: "seq0", file: "q1asm/seq0.q1asm" }];
    payload.q1asm_provenance = [
      {
        source_id: "pulse:x180:pulse:0",
        schedulable_id: "x180",
        sequencer: "seq0",
        q1asm_line_start: 3,
        q1asm_line_end: 3,
        instruction_roles: ["play"],
      },
    ];

    const model = buildTimelineModel(payload, "x180");

    assert.equal(model.inspector.q1asmDrilldown.targetLine, 3);
  });

  it("uses operation schedule id in operation-only q1timeline open messages", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = {
      schedule: { name: "operation-only unit" },
      operations: [{ id: "sched-a", operation_id: "semantic-a", label: "Idle", abs_time: 0, duration: 20e-9 }],
      symbolic_values: [],
      symbolic_pulses: [],
      q1asm_programs: [{ sequencer: "seq0", file: "q1asm/seq0.q1asm", text: "wait 20\nstop\n" }],
      q1asm_provenance: [{ source_id: "sched-a", sequencer: "seq0", q1asm_line_start: 7, q1asm_line_end: 7 }],
    };

    const model = buildTimelineModel(payload, "sched-a");

    assert.deepEqual(model.inspector.q1asmDrilldown.openMessage, {
      type: "openQ1Timeline",
      blockId: "sched-a",
      operationId: "semantic-a",
      sequencer: "seq0",
      line: 7,
    });
  });

  it("omits the full q1timeline open message when symbolic provenance is unavailable", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.q1asm_provenance = [];
    const model = buildTimelineModel(payload, "pulse:cz", undefined, { expandedGroups: ["target:q0_q1"] });
    const pulse = model.lanes.flatMap((lane: { blocks: any[] }) => lane.blocks).find((block: any) => block.id === "pulse:cz");

    assert.equal(pulse.q1timelineMessage, undefined);
  });

  it("inserts an inline Q1 preview lane below an expanded symbolic lane", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(ir(), "pulse:cz", undefined, {
      expandedGroups: ["target:q0_q1"],
      expandedInlineQ1Lanes: ["inline-q1:q0_q1:flux / cz"],
    });
    const pulseIndex = model.lanes.findIndex((lane: { label: string }) => lane.label === "q0_q1:flux / cz");
    const pulseLane = model.lanes[pulseIndex];
    const q1Lane = model.lanes[pulseIndex + 1];

    assert.equal(pulseLane.inlineQ1PreviewLaneId, "inline-q1:q0_q1:flux / cz");
    assert.equal(pulseLane.inlineQ1PreviewExpanded, true);
    assert.equal(pulseLane.inlineQ1PreviewLabel, "cluster0_module4_seq0");
    assert.equal(q1Lane.kind, "q1");
    assert.equal(q1Lane.parentGroupId, "target:q0_q1");
    assert.equal(q1Lane.depth, 2);
    assert.equal(q1Lane.label, "cluster0_module4_seq0");
    assert.equal(q1Lane.title, "cluster0_module4_seq0");
    assert.deepEqual(q1Lane.blocks[0], {
      id: "q1:pulse:cz",
      type: "q1",
      visualKind: "q1",
      sourceBlockId: "pulse:cz",
      operationId: "cz_q0_q1",
      sequencer: "cluster0_module4_seq0",
      line: 3,
      targetEndLine: 3,
      lineRangeLabel: "3",
      instruction: "play",
      label: "play",
      detail: "L3",
      accentColor: "#8bcf9a",
      q1asmSourceMessage: {
        type: "openQ1AsmSource",
        sequencer: "cluster0_module4_seq0",
        line: 3,
      },
      startLabel: "44 ns",
      durationLabel: "88 ns",
      leftPercent: 33.333,
      widthPercent: 66.667,
    });
  });

  it("expands inline Q1 preview for every block in a symbolic lane", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.operations.push({ id: "cz2_q0_q1", label: "CZ2(q0,q1)", abs_time: 160e-9, duration: 20e-9 });
    payload.symbolic_pulses.push({
      id: "pulse:cz2",
      schedulable_id: "cz2",
      operation_id: "cz2_q0_q1",
      lane: "q0_q1:flux / cz",
      kind: "pulse",
      label: "CZFluxPulse2",
      abs_time: 160e-9,
      duration: 20e-9,
    });
    payload.q1asm_by_sequencer = { cluster0_module4_seq0: "wait 44\nplay 0,1,88\nplay 2,3,20\nstop\n" };
    payload.q1asm_provenance = [
      {
        sequencer: "cluster0_module4_seq0",
        q1asm_line_start: 2,
        q1asm_line_end: 2,
        source_id: "pulse:cz",
        operation_id: "cz_q0_q1",
      },
      {
        sequencer: "cluster0_module4_seq0",
        q1asm_line_start: 3,
        q1asm_line_end: 3,
        source_id: "pulse:cz2",
        operation_id: "cz2_q0_q1",
      },
    ];

    const model = buildTimelineModel(payload, "pulse:cz", undefined, {
      expandedGroups: ["target:q0_q1"],
      expandedInlineQ1Lanes: ["inline-q1:q0_q1:flux / cz"],
    });
    const q1Lane = model.lanes.find((lane: { kind: string }) => lane.kind === "q1");

    assert.deepEqual(q1Lane.sourceBlockIds, ["pulse:cz", "pulse:cz2"]);
    assert.deepEqual(
      q1Lane.blocks.map((block: { sourceBlockId: string; line: number; q1asmText: string }) => [
        block.sourceBlockId,
        block.line,
        block.q1asmText,
      ]),
      [
        ["pulse:cz", 2, "play 0,1,88"],
        ["pulse:cz2", 3, "play 2,3,20"],
      ],
    );
  });

  it("does not expand inline Q1 preview from block expansion state", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(ir(), "pulse:cz", undefined, {
      expandedGroups: ["target:q0_q1"],
      expandedInlineQ1Blocks: ["pulse:cz"],
    });

    assert.equal(model.lanes.some((lane: { kind: string }) => lane.kind === "q1"), false);
  });

  it("labels inline Q1 preview blocks from the concrete Q1ASM line text", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.q1asm_by_sequencer = { cluster0_module4_seq0: "wait 44\nnop\nplay 2,3,160\nstop\n" };
    payload.q1asm_provenance[0] = {
      sequencer: "cluster0_module4_seq0",
      q1asm_line_start: 3,
      q1asm_line_end: 3,
      source_id: "pulse:cz",
      operation_id: "cz_q0_q1",
    };
    const model = buildTimelineModel(payload, "pulse:cz", undefined, {
      expandedGroups: ["target:q0_q1"],
      expandedInlineQ1Lanes: ["inline-q1:q0_q1:flux / cz"],
    });
    const q1Block = model.lanes.find((lane: { kind: string }) => lane.kind === "q1").blocks[0];

    assert.equal(q1Block.label, "play");
    assert.equal(q1Block.instruction, "play");
    assert.equal(q1Block.detail, "L3");
    assert.equal(q1Block.q1asmText, "play 2,3,160");
  });

  it("uses the timed Q1ASM event line for inline preview blocks when provenance includes setup", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.q1asm_by_sequencer = { cluster0_module4_seq0: "wait 20\nset_awg_gain 17203,0\nplay 0,1,20\nstop\n" };
    payload.q1asm_provenance[0] = {
      sequencer: "cluster0_module4_seq0",
      q1asm_line_start: 2,
      q1asm_line_end: 3,
      source_id: "pulse:cz",
      operation_id: "cz_q0_q1",
      instruction_roles: ["set_awg_gain", "play"],
      operand_mappings: [
        {
          line: 3,
          instruction: "play",
          operand_index: 2,
          role: "duration",
          numeric_value: 20,
          unit: "ns",
        },
      ],
    };
    const model = buildTimelineModel(payload, "pulse:cz", undefined, {
      expandedGroups: ["target:q0_q1"],
      expandedInlineQ1Lanes: ["inline-q1:q0_q1:flux / cz"],
    });
    const q1Block = model.lanes.find((lane: { kind: string }) => lane.kind === "q1").blocks[0];

    assert.equal(q1Block.label, "play");
    assert.equal(q1Block.instruction, "play");
    assert.equal(q1Block.line, 3);
    assert.equal(q1Block.targetEndLine, 3);
    assert.equal(q1Block.detail, "L3");
    assert.equal(q1Block.q1asmText, "play 0,1,20");
    assert.deepEqual(q1Block.q1asmSourceMessage, {
      type: "openQ1AsmSource",
      sequencer: "cluster0_module4_seq0",
      line: 3,
    });
  });

  it("uses acquisition variant operand lines for inline Q1 fallback preview blocks", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.q1asm_by_sequencer = {
      cluster0_module4_seq0: "set_scope_en 1\nwait 20\nacquire_ttl 0,0,240\nstop\n",
    };
    payload.q1asm_provenance[0] = {
      sequencer: "cluster0_module4_seq0",
      q1asm_line_start: 1,
      q1asm_line_end: 3,
      source_id: "pulse:cz",
      operation_id: "cz_q0_q1",
      instruction_roles: ["set_scope_en", "acquire_ttl"],
      operand_mappings: [
        { line: 1, instruction: "set_scope_en", operand_index: 0, role: "setup" },
        { line: 3, instruction: "acquire_ttl", operand_index: 2, role: "duration" },
      ],
    };

    const model = buildTimelineModel(payload, "pulse:cz", undefined, {
      expandedGroups: ["target:q0_q1"],
      expandedInlineQ1Lanes: ["inline-q1:q0_q1:flux / cz"],
    });
    const q1Block = model.lanes.find((lane: { kind: string }) => lane.kind === "q1").blocks[0];

    assert.equal(q1Block.label, "acquire_ttl");
    assert.equal(q1Block.instruction, "acquire_ttl");
    assert.equal(q1Block.line, 3);
    assert.equal(q1Block.q1asmText, "acquire_ttl 0,0,240");
    assert.deepEqual(q1Block.q1asmSourceMessage, {
      type: "openQ1AsmSource",
      sequencer: "cluster0_module4_seq0",
      line: 3,
    });
  });

  it("uses q1timeline timed events for inline Q1 preview blocks when available", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.q1asm_by_sequencer = { cluster0_module4_seq0: "set_awg_gain 17203,0\nplay 0,1,20\nstop\n" };
    payload.q1asm_provenance[0] = {
      sequencer: "cluster0_module4_seq0",
      q1asm_line_start: 1,
      q1asm_line_end: 2,
      source_id: "pulse:cz",
      operation_id: "cz_q0_q1",
    };
    payload.q1timeline_ir = {
      events: [
        {
          id: "state-setup",
          kind: "latched_state_pending",
          label: "set_awg_gain",
          sequencer_id: "cluster0_module4_seq0",
          lane: "state.pending",
          source: { line: 1, raw: "set_awg_gain 17203,0" },
          t0: { value: 44, display: "44 ns" },
          t1: { value: 44, display: "44 ns" },
          duration: { value: 0, display: "0 ns" },
        },
        {
          id: "debug-setup",
          kind: "q1_issue",
          label: "set_awg_gain",
          sequencer_id: "cluster0_module4_seq0",
          lane: "debug.q1_issue",
          source: { line: 1, raw: "set_awg_gain 17203,0" },
          t0: { value: 40, display: "40 ns" },
          t1: { value: 44, display: "44 ns" },
          duration: { value: 4, display: "4 ns" },
          meta: { display_modes: ["debug"] },
        },
        {
          id: "play-path0",
          kind: "play",
          label: "play",
          sequencer_id: "cluster0_module4_seq0",
          lane: "rt.path0",
          source: { line: 2, raw: "play 0,1,20" },
          t0: { value: 44, display: "44 ns" },
          t1: { value: 64, display: "64 ns" },
          duration: { value: 20, display: "20 ns" },
        },
      ],
    };

    const model = buildTimelineModel(payload, "pulse:cz", undefined, {
      expandedGroups: ["target:q0_q1"],
      expandedInlineQ1Lanes: ["inline-q1:q0_q1:flux / cz"],
    });
    const q1Blocks = model.lanes.find((lane: { kind: string }) => lane.kind === "q1").blocks;

    assert.equal(q1Blocks.length, 1);
    assert.equal(q1Blocks[0].source, "q1timeline");
    assert.equal(q1Blocks[0].label, "play");
    assert.equal(q1Blocks[0].instruction, "play");
    assert.equal(q1Blocks[0].line, 2);
    assert.equal(q1Blocks[0].q1asmText, "play 0,1,20");
    assert.equal(q1Blocks[0].durationLabel, "20 ns");
  });

  it("matches q1timeline event line and time aliases for inline Q1 preview blocks", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.q1asm_by_sequencer = { cluster0_module4_seq0: "play 0,1,20\nstop\n" };
    payload.q1asm_provenance[0] = {
      sequencer: "cluster0_module4_seq0",
      q1asm_line_start: 1,
      q1asm_line_end: 1,
      source_id: "pulse:cz",
      operation_id: "cz_q0_q1",
    };
    payload.q1timeline_ir = {
      events: [
        {
          id: "play-path0",
          kind: "play",
          label: "play",
          sequencer_id: "cluster0_module4_seq0",
          lane: "rt.path0",
          q1asm_line_start: 1,
          t0_ns: 44,
          t1_ns: 64,
          duration_ns: 20,
        },
      ],
    };

    const model = buildTimelineModel(payload, "pulse:cz", undefined, {
      expandedGroups: ["target:q0_q1"],
      expandedInlineQ1Lanes: ["inline-q1:q0_q1:flux / cz"],
    });
    const q1Block = model.lanes.find((lane: { kind: string }) => lane.kind === "q1").blocks[0];

    assert.equal(q1Block.source, "q1timeline");
    assert.equal(q1Block.line, 1);
    assert.equal(q1Block.startLabel, "44 ns");
    assert.equal(q1Block.durationLabel, "20 ns");
  });

  it("formats raw q1timeline second scalars without rounding them to zero nanoseconds", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.q1asm_by_sequencer = { cluster0_module4_seq0: "play 0,1,20\nstop\n" };
    payload.q1asm_provenance[0] = {
      sequencer: "cluster0_module4_seq0",
      q1asm_line_start: 1,
      q1asm_line_end: 1,
      source_id: "pulse:cz",
      operation_id: "cz_q0_q1",
    };
    payload.q1timeline_ir = {
      events: [
        {
          id: "play",
          kind: "play",
          sequencer_id: "cluster0_module4_seq0",
          lane: "rt.path0",
          source: { line: 1, raw: "play 0,1,20" },
          t0: 44e-9,
          t1: 64e-9,
          duration: 20e-9,
        },
      ],
    };

    const model = buildTimelineModel(payload, "pulse:cz", undefined, {
      expandedGroups: ["target:q0_q1"],
      expandedInlineQ1Lanes: ["inline-q1:q0_q1:flux / cz"],
    });
    const q1Block = model.lanes.find((lane: { kind: string }) => lane.kind === "q1").blocks[0];

    assert.equal(q1Block.startLabel, "44 ns");
    assert.equal(q1Block.durationLabel, "20 ns");
  });

  it("uses embedded q1asm program text when q1asm_by_sequencer is absent", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    delete payload.q1asm_by_sequencer;
    payload.q1asm_programs = [{ sequencer: "cluster0_module4_seq0", file: "q1asm/seq0.q1asm", text: "wait 44\nplay 0,1,20\nstop\n" }];
    payload.q1asm_provenance[0] = {
      sequencer: "cluster0_module4_seq0",
      q1asm_line_start: 2,
      q1asm_line_end: 2,
      source_id: "pulse:cz",
      operation_id: "cz_q0_q1",
    };

    const model = buildTimelineModel(payload, "pulse:cz", undefined, {
      expandedGroups: ["target:q0_q1"],
      expandedInlineQ1Lanes: ["inline-q1:q0_q1:flux / cz"],
    });
    const q1Block = model.lanes.find((lane: { kind: string }) => lane.kind === "q1").blocks[0];

    assert.equal(model.inspector.q1asmDrilldown.available, true);
    assert.equal(q1Block.label, "play");
    assert.equal(q1Block.q1asmText, "play 0,1,20");
  });

  it("matches inline q1timeline events through sequencer_id aliases", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.q1asm_by_sequencer = { seq0: "play 0,1,20\nstop\n" };
    payload.q1asm_programs = [
      { sequencer: "pretty-seq", sequencer_id: "seq0", file: "q1asm/seq0.q1asm", text: "play 0,1,20\nstop\n" },
    ];
    payload.q1asm_provenance[0] = {
      sequencer: "pretty-seq",
      sequencer_id: "seq0",
      q1asm_line_start: 1,
      q1asm_line_end: 1,
      source_id: "pulse:cz",
      operation_id: "cz_q0_q1",
    };
    payload.q1timeline_ir = {
      events: [
        {
          id: "seq0:play",
          kind: "play",
          sequencer_id: "seq0",
          lane: "rt.path0",
          source: { line: 1, raw: "play 0,1,20" },
          t0: { value: 44, display: "44 ns" },
          t1: { value: 64, display: "64 ns" },
          duration: { value: 20, display: "20 ns" },
        },
      ],
    };

    const model = buildTimelineModel(payload, "pulse:cz", undefined, {
      expandedGroups: ["target:q0_q1"],
      expandedInlineQ1Lanes: ["inline-q1:q0_q1:flux / cz"],
    });
    const q1Block = model.lanes.find((lane: { kind: string }) => lane.kind === "q1").blocks[0];

    assert.equal(q1Block.source, "q1timeline");
    assert.equal(q1Block.eventId, "seq0:play");
    assert.equal(q1Block.q1asmText, "play 0,1,20");
  });

  it("keeps distinct same-line q1timeline events on separate lanes", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.q1asm_by_sequencer = { cluster0_module4_seq0: "play 0,1,20\nstop\n" };
    payload.q1asm_provenance[0] = {
      sequencer: "cluster0_module4_seq0",
      q1asm_line_start: 1,
      q1asm_line_end: 1,
      source_id: "pulse:cz",
      operation_id: "cz_q0_q1",
    };
    payload.q1timeline_ir = {
      events: [
        {
          id: "path0",
          kind: "play",
          label: "play",
          sequencer_id: "cluster0_module4_seq0",
          lane: "rt.path0",
          source: { line: 1, raw: "play 0,1,20" },
          t0: { value: 44 },
          t1: { value: 64 },
          duration: { value: 20 },
        },
        {
          id: "path1",
          kind: "play",
          label: "play",
          sequencer_id: "cluster0_module4_seq0",
          lane: "rt.path1",
          source: { line: 1, raw: "play 0,1,20" },
          t0: { value: 44 },
          t1: { value: 64 },
          duration: { value: 20 },
        },
      ],
    };

    const model = buildTimelineModel(payload, "pulse:cz", undefined, {
      expandedGroups: ["target:q0_q1"],
      expandedInlineQ1Lanes: ["inline-q1:q0_q1:flux / cz"],
    });
    const q1Blocks = model.lanes.find((lane: { kind: string }) => lane.kind === "q1").blocks;

    assert.deepEqual(q1Blocks.map((block: { eventId: string; eventLane: string }) => [block.eventId, block.eventLane]), [
      ["path0", "rt.path0"],
      ["path1", "rt.path1"],
    ]);
  });

  it("splits inline Q1 preview blocks by q1timeline timed events", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.q1asm_by_sequencer = { cluster0_module4_seq0: "play 0,1,8\nwait 72\nstop\n" };
    payload.q1asm_provenance[0] = {
      sequencer: "cluster0_module4_seq0",
      q1asm_line_start: 1,
      q1asm_line_end: 2,
      source_id: "pulse:cz",
      operation_id: "cz_q0_q1",
    };
    payload.q1timeline_ir = {
      events: [
        {
          id: "play",
          kind: "play",
          label: "play",
          sequencer_id: "cluster0_module4_seq0",
          lane: "rt.path0",
          source: { line: 1, raw: "play 0,1,8" },
          t0: { value: 44, display: "44 ns" },
          t1: { value: 52, display: "52 ns" },
          duration: { value: 8, display: "8 ns" },
        },
        {
          id: "wait",
          kind: "wait",
          label: "wait",
          sequencer_id: "cluster0_module4_seq0",
          lane: "rt.wait",
          source: { line: 2, raw: "wait 72" },
          t0: { value: 52, display: "52 ns" },
          t1: { value: 124, display: "124 ns" },
          duration: { value: 72, display: "72 ns" },
        },
      ],
    };

    const model = buildTimelineModel(payload, "pulse:cz", undefined, {
      expandedGroups: ["target:q0_q1"],
      expandedInlineQ1Lanes: ["inline-q1:q0_q1:flux / cz"],
    });
    const q1Blocks = model.lanes.find((lane: { kind: string }) => lane.kind === "q1").blocks;

    assert.deepEqual(q1Blocks.map((block: { label: string }) => block.label), ["play", "wait"]);
    assert.deepEqual(q1Blocks.map((block: { line: number }) => block.line), [1, 2]);
    assert.deepEqual(q1Blocks.map((block: { accentColor: string }) => block.accentColor), ["#8bcf9a", "#b9a7dc"]);
    assert.equal(q1Blocks[0].leftPercent, 33.333);
    assert.equal(q1Blocks[0].widthPercent, 6.061);
    assert.equal(q1Blocks[1].leftPercent, 39.394);
    assert.equal(q1Blocks[1].widthPercent, 54.546);
  });

  it("scales a single q1timeline inline event against the selected QBS block duration", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.symbolic_pulses[0] = {
      ...payload.symbolic_pulses[0],
      lane: "q0:mw",
      duration: 100e-9,
    };
    payload.q1asm_provenance[0] = {
      ...payload.q1asm_provenance[0],
      source_id: "pulse:cz",
      q1asm_line_start: 3,
      q1asm_line_end: 3,
    };
    payload.q1timeline_ir = {
      events: [
        {
          id: "seq0:e3",
          sequencer_id: "cluster0_module4_seq0",
          kind: "play",
          source: { line: 3 },
          t0: { value: 44, display: "44 ns" },
          t1: { value: 48, display: "48 ns" },
          duration: { value: 4, display: "4 ns" },
        },
      ],
    };

    const model = buildTimelineModel(payload, "pulse:cz", undefined, {
      expandedGroups: ["target:q0"],
      expandedInlineQ1Lanes: ["inline-q1:q0:mw"],
    });
    const pulseBlock = model.lanes
      .flatMap((lane: { blocks: any[] }) => lane.blocks)
      .find((block: any) => block.id === "pulse:cz");
    const q1Block = model.lanes.find((lane: { kind: string }) => lane.kind === "q1").blocks[0];

    assert.equal(q1Block.durationLabel, "4 ns");
    assert.equal(Math.round((q1Block.widthPercent / pulseBlock.widthPercent) * 1000) / 1000, 0.04);
  });

  it("preserves a single q1timeline inline event offset inside the QBS block", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.symbolic_pulses[0] = {
      ...payload.symbolic_pulses[0],
      lane: "q0:mw",
      abs_time: 20e-9,
      duration: 40e-9,
    };
    payload.q1asm_provenance[0] = {
      ...payload.q1asm_provenance[0],
      source_id: "pulse:cz",
      q1asm_line_start: 3,
      q1asm_line_end: 3,
    };
    payload.q1timeline_ir = {
      events: [
        {
          id: "seq0:e3",
          sequencer_id: "cluster0_module4_seq0",
          kind: "play",
          source: { line: 3 },
          t0: { value: 24, display: "24 ns" },
          t1: { value: 28, display: "28 ns" },
          duration: { value: 4, display: "4 ns" },
        },
      ],
    };

    const model = buildTimelineModel(payload, "pulse:cz", undefined, {
      expandedGroups: ["target:q0"],
      expandedInlineQ1Lanes: ["inline-q1:q0:mw"],
    });
    const pulseBlock = model.lanes
      .flatMap((lane: { blocks: any[] }) => lane.blocks)
      .find((block: any) => block.id === "pulse:cz");
    const q1Block = model.lanes.find((lane: { kind: string }) => lane.kind === "q1").blocks[0];

    assert.equal(q1Block.leftPercent > pulseBlock.leftPercent, true);
  });

  it("preserves multi-event q1timeline inline offsets inside the QBS block", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.symbolic_pulses[0] = {
      ...payload.symbolic_pulses[0],
      lane: "q0:mw",
      abs_time: 0,
      duration: 100e-9,
    };
    payload.q1asm_provenance[0] = {
      ...payload.q1asm_provenance[0],
      source_id: "pulse:cz",
      q1asm_line_start: 3,
      q1asm_line_end: 4,
    };
    payload.q1timeline_ir = {
      events: [
        {
          id: "seq0:e3",
          sequencer_id: "cluster0_module4_seq0",
          kind: "play",
          source: { line: 3 },
          t0: { value: 24, display: "24 ns" },
          t1: { value: 28, display: "28 ns" },
          duration: { value: 4, display: "4 ns" },
        },
        {
          id: "seq0:e4",
          sequencer_id: "cluster0_module4_seq0",
          kind: "wait",
          source: { line: 4 },
          t0: { value: 40, display: "40 ns" },
          t1: { value: 44, display: "44 ns" },
          duration: { value: 4, display: "4 ns" },
        },
      ],
    };

    const model = buildTimelineModel(payload, "pulse:cz", undefined, {
      expandedGroups: ["target:q0"],
      expandedInlineQ1Lanes: ["inline-q1:q0:mw"],
    });
    const pulseBlock = model.lanes
      .flatMap((lane: { blocks: any[] }) => lane.blocks)
      .find((block: any) => block.id === "pulse:cz");
    const q1Blocks = model.lanes.find((lane: { kind: string }) => lane.kind === "q1").blocks;

    assert.deepEqual(
      q1Blocks.map((block: any) => Math.round(((block.leftPercent - pulseBlock.leftPercent) / pulseBlock.widthPercent) * 100) / 100),
      [0.24, 0.4],
    );
    assert.deepEqual(
      q1Blocks.map((block: any) => Math.round((block.widthPercent / pulseBlock.widthPercent) * 100) / 100),
      [0.04, 0.04],
    );
  });

  it("omits inline q1timeline events outside the selected QBS block", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.symbolic_pulses[0] = {
      ...payload.symbolic_pulses[0],
      lane: "q0:mw",
      abs_time: 20e-9,
      duration: 40e-9,
    };
    payload.q1asm_provenance[0] = {
      ...payload.q1asm_provenance[0],
      source_id: "pulse:cz",
      q1asm_line_start: 2,
      q1asm_line_end: 3,
    };
    payload.q1timeline_ir = {
      events: [
        {
          id: "seq0:setup",
          sequencer_id: "cluster0_module4_seq0",
          kind: "wait",
          source: { line: 2 },
          t0: { value: 0 },
          t1: { value: 20 },
          duration: { value: 20 },
        },
        {
          id: "seq0:play",
          sequencer_id: "cluster0_module4_seq0",
          kind: "play",
          source: { line: 3 },
          t0: { value: 20 },
          t1: { value: 40 },
          duration: { value: 20 },
        },
      ],
    };

    const model = buildTimelineModel(payload, "pulse:cz", undefined, {
      expandedGroups: ["target:q0"],
      expandedInlineQ1Lanes: ["inline-q1:q0:mw"],
    });
    const q1Blocks = model.lanes.find((lane: { kind: string }) => lane.kind === "q1").blocks;

    assert.deepEqual(q1Blocks.map((block: any) => block.eventId), ["seq0:play"]);
  });

  it("falls back to instruction roles for inline Q1 preview labels when Q1ASM text is unavailable", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.q1asm_provenance[0] = {
      sequencer: "cluster0_module4_seq0",
      q1asm_line_start: 3,
      q1asm_line_end: 3,
      source_id: "pulse:cz",
      operation_id: "cz_q0_q1",
      instruction_roles: ["play"],
    };
    const model = buildTimelineModel(payload, "pulse:cz", undefined, {
      expandedGroups: ["target:q0_q1"],
      expandedInlineQ1Lanes: ["inline-q1:q0_q1:flux / cz"],
    });
    const q1Block = model.lanes.find((lane: { kind: string }) => lane.kind === "q1").blocks[0];

    assert.equal(q1Block.label, "play");
    assert.equal(q1Block.instruction, "play");
  });

  it("omits inline Q1 preview lanes until a symbolic block is expanded", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(ir(), "pulse:cz", undefined, { expandedGroups: ["target:q0_q1"] });

    assert.equal(model.lanes.some((lane: { kind: string }) => lane.kind === "q1"), false);
  });

  it("builds an inspector for the selected pulse without opening q1timeline", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(ir(), "pulse:cz");

    assert.deepEqual(model.inspector.actions, [
      { label: "Open Full Q1ASM Timeline", message: { type: "openQ1Timeline", blockId: "pulse:cz", operationId: "cz_q0_q1" } },
    ]);
    assert.equal(model.inspector.title, "CZFluxPulse");
    assert.equal(model.inspector.rows.find((row: { label: string; value: string }) => row.label === "Duration")?.value, "88 ns");
    assert.equal(model.inspector.rows.find((row: { label: string; value: string }) => row.label === "Symbolic duration")?.value, "T_CZ");
  });

  it("keeps the generated view connected to project and schedule source files", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(ir(), "pulse:cz", {
      projectFile: "C:\\repo\\examples\\two-qubit-entangling\\qbstimeline.yml",
      scheduleFile: "C:\\repo\\examples\\two-qubit-entangling\\schedule.py",
      outputDir: "C:\\repo\\examples\\two-qubit-entangling\\.qbs_timeline",
    });

    assert.deepEqual(model.source, {
      projectLabel: "qbstimeline.yml",
      scheduleLabel: "schedule.py",
      outputLabel: ".qbs_timeline",
      actions: [
        { label: "Open qbstimeline.yml", message: { type: "openProjectFile" } },
        { label: "Open schedule.py", message: { type: "openScheduleFile" } },
      ],
    });
    assert.deepEqual(model.inspector.actions.slice(-2), [
      { label: "Open qbstimeline.yml", message: { type: "openProjectFile" } },
      { label: "Open schedule.py", message: { type: "openScheduleFile" } },
    ]);
  });

  it("labels notebook source actions before generated schedule actions", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(ir(), "reset_q0", {
      projectFile: "C:\\repo\\qbstimeline.yml",
      scheduleFile: "C:\\repo\\.scratch\\probe\\schedule.py",
      sourceNotebook: "C:\\repo\\examples\\050_qubit_spectroscopy.ipynb",
      outputDir: "C:\\repo\\.scratch\\probe\\.qbs_timeline",
    });

    assert.equal(model.source.scheduleLabel, "050_qubit_spectroscopy.ipynb");
    assert.deepEqual(model.source.actions.map((action: { label: string }) => action.label), [
      "Open qbstimeline.yml",
      "Open notebook",
      "Open generated schedule.py",
    ]);
    assert.deepEqual(model.source.actions.map((action: { message: { type: string } }) => action.message.type), [
      "openProjectFile",
      "openNotebookFile",
      "openScheduleFile",
    ]);
  });

  it("builds an inline Q1ASM drill-down while preserving full q1timeline action", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.q1asm_by_sequencer = { cluster0_module4_seq0: "wait 44\nnop\nplay 0,0,8\nstop\n" };
    const model = buildTimelineModel(payload, "pulse:cz");

    assert.deepEqual(model.inspector.tabs.map((tab: { id: string }) => tab.id), ["summary", "lowering", "q1asm"]);
    assert.equal(model.inspector.tabs.find((tab: { id: string }) => tab.id === "q1asm")?.message, undefined);
    assert.deepEqual(model.inspector.actions[0], {
      label: "Open Full Q1ASM Timeline",
      message: {
        type: "openQ1Timeline",
        blockId: "pulse:cz",
        operationId: "cz_q0_q1",
      },
    });
    assert.equal(model.inspector.q1asmDrilldown.available, true);
    assert.equal(model.inspector.q1asmDrilldown.sequencer, "cluster0_module4_seq0");
    assert.equal(model.inspector.q1asmDrilldown.targetLine, 3);
    assert.equal(model.inspector.q1asmDrilldown.lineRangeLabel, "3");
    assert.deepEqual(
      model.inspector.q1asmDrilldown.lines.map((line: { number: number; text: string; highlighted: boolean }) => [
        line.number,
        line.text,
        line.highlighted,
      ]),
      [
        [1, "wait 44", false],
        [2, "nop", false],
        [3, "play 0,0,8", true],
        [4, "stop", false],
      ],
    );
    assert.deepEqual(model.inspector.q1asmDrilldown.openMessage, {
      type: "openQ1Timeline",
      blockId: "pulse:cz",
      operationId: "cz_q0_q1",
      sequencer: "cluster0_module4_seq0",
      line: 3,
    });
    assert.equal(model.lanes.some((lane: { kind: string }) => lane.kind === "provenance"), false);
  });

  it("prioritizes exact source_id provenance over shared operation_id matches", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.symbolic_pulses = [
      {
        id: "pulse:measure:pulse:0",
        schedulable_id: "measure",
        operation_id: "measure_q0",
        lane: "q0:res / measure",
        kind: "pulse",
        label: "ReadoutPulse",
        abs_time: 100e-9,
        duration: 20e-9,
      },
      {
        id: "acq:measure:acquisition:0",
        schedulable_id: "measure",
        operation_id: "measure_q0",
        role: "acquisition",
        lane: "q0:res / measure",
        kind: "acquisition",
        label: "Acquisition",
        abs_time: 120e-9,
        duration: 40e-9,
      },
    ];
    payload.q1asm_by_sequencer = {
      seq0: "wait 100\nplay 0,0,20\nwait 0\nacquire 0,0,40\nstop\n",
    };
    payload.q1asm_provenance = [
      {
        source_id: "pulse:measure:pulse:0",
        operation_id: "measure_q0",
        sequencer: "seq0",
        q1asm_line_start: 2,
        q1asm_line_end: 2,
        instruction: "play",
        instruction_roles: ["play"],
      },
      {
        source_id: "acq:measure:acquisition:0",
        operation_id: "measure_q0",
        sequencer: "seq0",
        q1asm_line_start: 4,
        q1asm_line_end: 4,
        instruction: "acquire",
        instruction_roles: ["acquire"],
      },
    ];

    const model = buildTimelineModel(payload, "acq:measure:acquisition:0");

    assert.equal(model.inspector.q1asmDrilldown.targetLine, 4);
    assert.equal(model.inspector.q1asmDrilldown.instruction, "acquire");
  });

  it("uses q1asm_line_start for q1timeline preview targets", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.q1asm_by_sequencer = { cluster0_module4_seq0: "wait 44\n\nplay 0,0,8\nupd_param 4\nstop\n" };
    payload.q1asm_provenance[0] = {
      ...payload.q1asm_provenance[0],
      line: 5,
      q1asm_line_start: 3,
      q1asm_line_end: 4,
    };

    const model = buildTimelineModel(payload, "pulse:cz");
    const q1asmPreviewTab = model.inspector.tabs.find((tab: { id: string }) => tab.id === "q1asm");

    assert.equal(q1asmPreviewTab.message, undefined);
    assert.equal(model.inspector.q1asmDrilldown.targetLine, 3);
    assert.equal(model.inspector.q1asmDrilldown.targetEndLine, 4);
    assert.equal(model.inspector.q1asmDrilldown.lineRangeLabel, "3-4");
    assert.deepEqual(
      model.inspector.loweringRows.find((row: { label: string }) => row.label === "Q1ASM lines"),
      { label: "Q1ASM lines", value: "3-4" },
    );
  });

  it("builds an inline Q1ASM drill-down fallback when embedded text is unavailable", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(ir(), "pulse:cz");

    assert.equal(model.inspector.q1asmDrilldown.available, false);
    assert.equal(model.inspector.q1asmDrilldown.sequencer, "cluster0_module4_seq0");
    assert.equal(model.inspector.q1asmDrilldown.targetLine, 3);
    assert.match(model.inspector.q1asmDrilldown.emptyMessage, /Q1ASM text is not embedded/);
  });

  it("marks acquisition symbolic blocks separately from pulse blocks", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.symbolic_pulses.push({
      id: "acq:q0",
      operation_id: "measure_q0",
      lane: "q0:res / q0.ro",
      role: "acquisition",
      kind: "SSBIntegrationComplex",
      label: "Acquire(q0)",
      abs_time: 164e-9,
      duration: 160e-9,
    });
    payload.operations.push({ id: "measure_q0", label: "Measure(q0)", abs_time: 164e-9, duration: 160e-9 });

    const model = buildTimelineModel(payload, "acq:q0", undefined, { expandedGroups: ["target:q0"] });
    const acquisition = model.lanes.flatMap((lane: { blocks: any[] }) => lane.blocks).find((block: any) => block.id === "acq:q0");

    assert.equal(acquisition.role, "acquisition");
    assert.equal(acquisition.visualKind, "acquisition");
  });

  it("uses symbolic display labels instead of generated ids or raw kinds", () => {
    const { buildTimelineModel } = loadModel();
    const payload: any = ir();
    payload.symbolic_pulses = [
      {
        id: "pulse:74b81819-088c-43e3-b5da-7a2c301bdd55:pulse:0",
        schedulable_id: "74b81819-088c-43e3-b5da-7a2c301bdd55",
        operation_id: "c572b503-2439-4eca-aefa-0a1d93272733",
        lane: "q0:mw / q0.01",
        kind: "SquarePulse",
        display_label: "SquarePulse q0:mw",
        display_subtitle: "20 ns | amp 0.5",
        abs_time: 0,
        duration: 20e-9,
      },
    ];
    payload.operations = [
      {
        id: "c572b503-2439-4eca-aefa-0a1d93272733",
        label: "Generated operation",
        abs_time: 0,
        duration: 20e-9,
      },
    ];

    const model = buildTimelineModel(payload, payload.symbolic_pulses[0].id, undefined, {
      expandedGroups: ["target:q0"],
    });
    const pulseBlock = model.lanes.flatMap((lane: { blocks: any[] }) => lane.blocks).find(
      (block: any) => block.id === payload.symbolic_pulses[0].id,
    );

    assert.equal(pulseBlock.label, "SquarePulse q0:mw");
    assert.equal(pulseBlock.detail, "20 ns | amp 0.5");
    assert.equal(model.inspector.title, "SquarePulse q0:mw");
  });

  it("builds a zoomed viewport and positions blocks relative to the visible time window", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(ir(), "pulse:cz", undefined, {
      viewport: { start: 44e-9, end: 132e-9 },
    });

    assert.equal(model.viewport.startLabel, "44 ns");
    assert.equal(model.viewport.endLabel, "132 ns");
    assert.equal(model.viewport.durationLabel, "88 ns");
    assert.equal(model.viewport.isZoomed, true);
    assert.deepEqual(model.ticks.map((tick: { label: string }) => tick.label), ["44 ns", "66 ns", "88 ns", "110 ns", "132 ns"]);

    const blocks = model.lanes.flatMap((lane: { blocks: any[] }) => lane.blocks);
    const cz = blocks.find((block: any) => block.id === "cz_q0_q1");
    const reset = blocks.find((block: any) => block.id === "reset_q0");

    assert.equal(cz.leftPercent, 0);
    assert.equal(cz.widthPercent, 100);
    assert.equal(reset, undefined);
  });

  it("pans a zoomed viewport by drag distance while clamping to schedule bounds", () => {
    const { panViewport } = loadModel();

    const draggedRight = panViewport(200e-9, { start: 50e-9, end: 150e-9 }, 0.25);
    assert.ok(Math.abs(draggedRight.start - 25e-9) < 1e-18);
    assert.ok(Math.abs(draggedRight.end - 125e-9) < 1e-18);

    const draggedLeftPastEnd = panViewport(200e-9, { start: 50e-9, end: 150e-9 }, -1);
    assert.ok(Math.abs(draggedLeftPastEnd.start - 100e-9) < 1e-18);
    assert.ok(Math.abs(draggedLeftPastEnd.end - 200e-9) < 1e-18);
  });

  it("normalizes selected time ranges for rendering", () => {
    const { buildTimelineModel } = loadModel();
    const model = buildTimelineModel(ir(), "pulse:cz", undefined, {
      viewport: { start: 0, end: 132e-9 },
      selectionRange: { start: 110e-9, end: 44e-9 },
    });

    assert.deepEqual(model.selectionRange, {
      start: 44e-9,
      end: 110e-9,
      startLabel: "44 ns",
      endLabel: "110 ns",
      durationLabel: "66 ns",
      leftPercent: 33.333,
      widthPercent: 50,
    });
  });
});
