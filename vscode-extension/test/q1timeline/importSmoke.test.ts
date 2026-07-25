import assert from "node:assert/strict";

describe("q1timeline module imports", () => {
  it("exports project discovery and source-map helpers", async () => {
    const projectDiscovery = await import("../../src/q1timeline/projectDiscovery");
    const sourceMap = await import("../../src/q1timeline/sourceMapLookup");

    assert.equal(typeof projectDiscovery.findProjectFileUpward, "function");
    assert.equal(typeof sourceMap.lookupEventIdsForSourceLine, "function");
  });
});
