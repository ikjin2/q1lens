import assert from "node:assert/strict";
import { parseWebviewMessage } from "../src/q1timeline/webviewMessageValidation";

describe("q1timeline webview message validation", () => {
  it("accepts unknown or 32-bit feedback values and nonnegative integral receive delays", () => {
    for (const value of [null, 0, -2147483648, 4294967295]) {
      const message = { type: "setExecutionFeedback", eventId: "seq:e4", feedbackId: "seq:feedback:3", value, delayNs: 1000 };
      assert.deepEqual(parseWebviewMessage(message), { valid: true, ...message });
    }
    const valid = { type: "setExecutionFeedback", eventId: "seq:e4", feedbackId: "seq:feedback:3", value: null, delayNs: 0 };
    assert.deepEqual(parseWebviewMessage(valid), { valid: true, ...valid });
    for (const change of [
      { value: undefined }, { value: "0" }, { value: true }, { value: NaN }, { value: 0.5 },
      { value: -2147483649 }, { value: 4294967296 },
      { delayNs: -1 }, { delayNs: 0.5 }, { delayNs: Infinity }, { delayNs: "0" }, { delayNs: Number.MAX_SAFE_INTEGER + 1 },
      { eventId: "" }, { feedbackId: " " }, { extra: true },
    ]) assert.equal(parseWebviewMessage({ ...valid, ...change }).valid, false);
  });

  it("accepts bounded execution controls and single-path occurrence choices", () => {
    for (const action of ["play", "pause"]) {
      const message = { type: "executionControl", action };
      assert.deepEqual(parseWebviewMessage(message), { valid: true, ...message });
    }
    for (const path of ["taken", "fallthrough"]) {
      const message = { type: "setExecutionBranch", eventId: "seq:e3", branchId: "seq:step:2", path };
      assert.deepEqual(parseWebviewMessage(message), { valid: true, ...message });
    }
    for (const message of [
      { type: "executionControl", action: "run" },
      { type: "executionControl", action: "play", budget_ms: 600000 },
      { type: "setExecutionBranch", eventId: "seq:e3", branchId: "seq:step:2", path: "both" },
      { type: "setExecutionBranch", eventId: "seq:e3", branchId: "seq:step:2", path: "collapsed" },
      { type: "setExecutionBranch", eventId: "", branchId: "seq:step:2", path: "taken" },
      { type: "setExecutionBranch", eventId: "seq:e3", branchId: " ", path: "taken" },
    ]) assert.equal(parseWebviewMessage(message).valid, false);
  });

  it("accepts the webview ready handshake", () => {
    assert.deepEqual(parseWebviewMessage({ type: "webviewReady" }), { valid: true, type: "webviewReady" });
  });

  it("accepts execution replay preparation for a sequencer", () => {
    const message = { type: "prepareExecutionReplay", sequencerId: "seq0" };
    assert.deepEqual(parseWebviewMessage(message), { valid: true, ...message });
    for (const scope of ["project", "sequencer"]) {
      const scoped = { ...message, scope };
      assert.deepEqual(parseWebviewMessage(scoped), { valid: true, ...scoped });
    }
  });

  it("rejects malformed execution replay preparation requests", () => {
    for (const message of [
      { type: "prepareExecutionReplay" },
      { type: "prepareExecutionReplay", sequencerId: "" },
      { type: "prepareExecutionReplay", sequencerId: "   " },
      { type: "prepareExecutionReplay", sequencerId: 0 },
      { type: "prepareExecutionReplay", sequencerId: "seq0", executionMode: "simulation" },
      { type: "prepareExecutionReplay", sequencerId: "seq0", scope: "module" },
      { type: "prepareExecutionReplay", sequencerId: "seq0", scope: false },
    ]) {
      assert.equal(parseWebviewMessage(message).valid, false);
    }
  });

  it("accepts branch assumption updates from timeline chips", () => {
    assert.deepEqual(
      parseWebviewMessage({ type: "setBranchAssumption", branchId: "seq0:branch:main.q1asm:4:jge:target", path: "taken" }),
      {
        valid: true,
        type: "setBranchAssumption",
        branchId: "seq0:branch:main.q1asm:4:jge:target",
        path: "taken",
      },
    );
    assert.deepEqual(
      parseWebviewMessage({ type: "setBranchAssumption", branchId: "seq0:branch:main.q1asm:4:jge:target", path: "fallthrough" }),
      {
        valid: true,
        type: "setBranchAssumption",
        branchId: "seq0:branch:main.q1asm:4:jge:target",
        path: "fallthrough",
      },
    );
    assert.deepEqual(
      parseWebviewMessage({ type: "setBranchAssumption", branchId: "seq0:branch:main.q1asm:4:jge:target", path: "both" }),
      {
        valid: true,
        type: "setBranchAssumption",
        branchId: "seq0:branch:main.q1asm:4:jge:target",
        path: "both",
      },
    );
    assert.deepEqual(
      parseWebviewMessage({ type: "setBranchAssumption", branchId: "seq0:branch:main.q1asm:4:jge:target", path: "collapsed" }),
      {
        valid: true,
        type: "setBranchAssumption",
        branchId: "seq0:branch:main.q1asm:4:jge:target",
        path: "collapsed",
      },
    );
  });

  it("rejects malformed branch assumption updates", () => {
    assert.equal(parseWebviewMessage({ type: "setBranchAssumption", branchId: "", path: "taken" }).valid, false);
    assert.equal(parseWebviewMessage({ type: "setBranchAssumption", branchId: "branch", path: "maybe" }).valid, false);
    assert.equal(parseWebviewMessage({ type: "setBranchAssumption", branchId: "branch", path: "taken", extra: true }).valid, false);
    for (const eventId of [null, 4, "", " ", {}]) {
      assert.equal(parseWebviewMessage({ type: "setBranchAssumption", branchId: "branch", path: "taken", eventId }).valid, false);
    }
  });

  it("accepts an optional originating event for branch selection restoration", () => {
    const message = { type: "setBranchAssumption", branchId: "branch", path: "both", eventId: "seq0:event:4" };
    assert.deepEqual(parseWebviewMessage(message), { valid: true, ...message });
  });

  it("accepts source jump requests from timeline controls", () => {
    assert.deepEqual(
      parseWebviewMessage({ type: "sourceClick", file: "q1asm/seq0.q1asm", line: 15, column: 1 }),
      {
        valid: true,
        type: "sourceClick",
        file: "q1asm/seq0.q1asm",
        line: 15,
        column: 1,
      },
    );
  });

  it("rejects malformed source jump requests", () => {
    assert.equal(parseWebviewMessage({ type: "sourceClick", file: "", line: 15, column: 1 }).valid, false);
    assert.equal(parseWebviewMessage({ type: "sourceClick", file: "q1asm/seq0.q1asm", line: 0, column: 1 }).valid, false);
    assert.equal(parseWebviewMessage({ type: "sourceClick", file: "q1asm/seq0.q1asm", line: 15, column: 0 }).valid, false);
    assert.equal(parseWebviewMessage({ type: "sourceClick", file: "q1asm/seq0.q1asm", line: 15, column: 1, extra: true }).valid, false);
    assert.equal(parseWebviewMessage({ type: "sourceClick", file: "q1asm/seq0.q1asm", line: 15, column: 1, preserveHardwareCursor: "true" }).valid, false);
  });

  it("accepts cursor preservation only as a boolean on a source jump", () => {
    for (const preserveHardwareCursor of [true, false]) {
      const message = { type: "sourceClick", file: "orange_drive.q1asm", line: 66, column: 5, preserveHardwareCursor };
      assert.deepEqual(parseWebviewMessage(message), { valid: true, ...message });
    }
  });

  it("accepts loop preview updates from timeline chips", () => {
    assert.deepEqual(
      parseWebviewMessage({ type: "setLoopPreview", loopKey: "seq0:loop:main.q1asm:2-8", visibleIterations: 3 }),
      {
        valid: true,
        type: "setLoopPreview",
        loopKey: "seq0:loop:main.q1asm:2-8",
        visibleIterations: 3,
      },
    );
  });

  it("rejects malformed loop preview updates", () => {
    assert.equal(parseWebviewMessage({ type: "setLoopPreview", loopKey: "", visibleIterations: 2 }).valid, false);
    assert.equal(parseWebviewMessage({ type: "setLoopPreview", loopKey: "loop", visibleIterations: 0 }).valid, false);
    assert.equal(parseWebviewMessage({ type: "setLoopPreview", loopKey: "loop", visibleIterations: 2.5 }).valid, false);
    assert.equal(parseWebviewMessage({ type: "setLoopPreview", loopKey: "loop", visibleIterations: 2, extra: true }).valid, false);
  });

  it("accepts project-scoped inline parameter edits without parsing their JSON", () => {
    const message = {
      type: "setAnalysisParameter",
      name: "DURATION",
      valueJson: " 9007199254740993 ",
      projectFile: "C:\\repo\\q1timeline.yml",
    };
    assert.deepEqual(parseWebviewMessage(message), { valid: true, ...message });
    assert.equal(parseWebviewMessage({ ...message, valueJson: "" }).valid, true);
    const reset = { type: "resetAnalysisParameter", name: message.name, projectFile: message.projectFile };
    assert.deepEqual(parseWebviewMessage(reset), { valid: true, ...reset });
  });

  it("rejects malformed or unscoped inline parameter messages", () => {
    const message = {
      type: "setAnalysisParameter",
      name: "DURATION",
      valueJson: "64",
      projectFile: "C:\\repo\\q1timeline.yml",
    };
    for (const invalid of [
      { ...message, name: "" },
      { ...message, name: " " },
      { ...message, name: "DURATION=64" },
      { ...message, name: 64 },
      { ...message, projectFile: undefined },
      { ...message, projectFile: " " },
      { ...message, projectFile: 64 },
      { ...message, valueJson: undefined },
      { ...message, valueJson: 64 },
      { ...message, extra: true },
      { ...message, type: "resetAnalysisParameter" },
      { type: "resetAnalysisParameter", name: "DURATION" },
      { type: "resetAnalysisParameter", name: "DURATION", projectFile: message.projectFile, extra: true },
    ]) {
      assert.equal(parseWebviewMessage(invalid).valid, false, JSON.stringify(invalid));
    }
  });
});
