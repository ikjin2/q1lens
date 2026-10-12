import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { isSupportedCoreVersion, SUPPORTED_CORE_VERSION_RANGE } from "../../src/q1timeline/coreVersionCompatibility";

describe("q1timeline core version compatibility", () => {
  it("accepts the matching release and keeps the existing 0.1.x API compatible", () => {
    const manifest = JSON.parse(readFileSync(join(__dirname, "../../../package.json"), "utf8"));
    for (const version of [manifest.version, "0.1.0", "0.1.1", "0.1.99", "0.2.0", "0.2.1", "0.2.99"]) {
      assert.equal(isSupportedCoreVersion(version), true, version);
    }
    assert.equal(SUPPORTED_CORE_VERSION_RANGE, "0.1.x or 0.2.x");
  });

  it("rejects undeclared API versions and malformed version values", () => {
    for (const version of [undefined, null, 0.2, {}, "", "0.0.9", "0.3.0", "1.0.0", "0.2", "0.2.0-rc.1", "v0.2.0"]) {
      assert.equal(isSupportedCoreVersion(version), false, String(version));
    }
  });
});
