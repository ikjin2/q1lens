import assert from "node:assert/strict";

describe("extension scaffold", () => {
  it("runs the local TypeScript test harness", () => {
    assert.equal("Q1Lens".includes("Lens"), true);
  });
});
