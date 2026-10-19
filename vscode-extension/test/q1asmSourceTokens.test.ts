import assert from "node:assert/strict";

const { sourceTokens } = require("../src/shared/timeline/instructionColors.js");

function argumentsIn(text: string) {
  return sourceTokens(text).filter((token: any) => token.kind !== "command")
    .map((token: any) => [text.slice(token.start, token.end), token.kind]);
}

describe("Q1ASM source token rules", () => {
  it("uses syntax kind independently of command and operand position", () => {
    for (const command of ["move", "add", "play", "fb_com_data"]) {
      for (const operands of ["R0, -16, $GAIN, @start, {N * 4}", "{N * 4}, @start, $GAIN, -16, R0"]) {
        assert.deepEqual(argumentsIn(`${command} ${operands}`).sort(), [
          ["R0", "register"], ["-16", "number"], ["$GAIN", "symbol"], ["@start", "label"], ["{N * 4}", "placeholder"],
        ].sort());
      }
    }
  });

  it("keeps declarations and references in the same symbol or label categories", () => {
    assert.deepEqual(argumentsIn(".DEF R0 {R1 + 4}"), [["R0", "symbol"], ["{R1 + 4}", "placeholder"]]);
    assert.deepEqual(argumentsIn(".DEF play 16"), [["play", "symbol"], ["16", "number"]]);
    assert.deepEqual(argumentsIn("play : move $play, $R0"), [["play", "label"], ["$play", "symbol"], ["$R0", "symbol"]]);
    assert.deepEqual(argumentsIn("loop R0,@play"), [["R0", "register"], ["@play", "label"]]);
    assert.deepEqual(argumentsIn("R0 :"), [["R0", "label"]]);
  });

  it("matches complete valid literals and skips invalid fragments", () => {
    assert.deepEqual(argumentsIn("move R0xA, r3, -0xFF, 0X10, -42, 0"), [
      ["R0xA", "register"], ["r3", "register"], ["-0xFF", "number"], ["0X10", "number"], ["-42", "number"], ["0", "number"],
    ]);
    assert.deepEqual(argumentsIn("move R01, 0b10, +3, 004, 1suffix, plain_name"), []);
  });

  it("keeps template expressions atomic and excludes comments and separators", () => {
    const text = "  entry : play 0, 1, {max(N, 4) * 2} // R0, 9, $GAIN, @entry";
    assert.deepEqual(argumentsIn(text), [["entry", "label"], ["0", "number"], ["1", "number"], ["{max(N, 4) * 2}", "placeholder"]]);
    let previousEnd = 0;
    for (const token of sourceTokens(text)) {
      assert.ok(token.start >= previousEnd && token.end > token.start && token.end <= text.indexOf("//"));
      previousEnd = token.end;
    }
    for (const comment of ["", "# .DEF play R0, 4", "  // set_freq $GAIN"]) {
      assert.deepEqual(sourceTokens(comment), []);
    }
  });

  it("keeps floor division and nested braces inside complete placeholder tokens", () => {
    for (const expression of ["{ DURATION // 2 }", "{ {inner} + 4 }", "{ {{inner}} // 4 }"]) {
      assert.deepEqual(argumentsIn(`wait ${expression} // R0, 16`), [[expression, "placeholder"]]);
      assert.deepEqual(argumentsIn(`.DEF DURATION ${expression}`), [["DURATION", "symbol"], [expression, "placeholder"]]);
    }
    assert.deepEqual(argumentsIn("wait { DURATION, 4"), []);
  });
});
