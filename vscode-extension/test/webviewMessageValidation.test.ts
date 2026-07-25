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
});
