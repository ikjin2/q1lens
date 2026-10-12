import assert from "node:assert/strict";
import { parseWebviewMessage } from "../src/q1timeline/webviewMessageValidation";

describe("q1timeline webview message validation", () => {
  it("accepts the webview ready handshake", () => {
    assert.deepEqual(parseWebviewMessage({ type: "webviewReady" }), { valid: true, type: "webviewReady" });
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
