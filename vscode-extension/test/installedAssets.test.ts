import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

interface InstalledAssetMismatch {
  file: string;
  reason: string;
}

interface InstalledAssetVerifier {
  assetFiles: string[];
  compareInstalledAssets(input: {
    extensionRoot: string;
    installedRoot: string;
    files?: string[];
  }): {
    ok: boolean;
    mismatches: InstalledAssetMismatch[];
  };
}

function verifier(): InstalledAssetVerifier {
  return require(join(__dirname, "..", "..", "scripts", "verify-installed-assets.js"));
}

function writeFixtureFile(root: string, file: string, content: string): void {
  const target = join(root, file);
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, content);
}

describe("installed asset verification", () => {
  let tempDir = "";

  afterEach(() => {
    if (tempDir) {
      rmSync(tempDir, { recursive: true, force: true });
      tempDir = "";
    }
  });

  it("hashes the runtime image and webview assets that must stay fresh after local install", () => {
    const { assetFiles } = verifier();

    assert.deepEqual(assetFiles, [
      "media/q1lens-icon.png",
      "media/qbs-timeline.svg",
      "out/src/q1timeline/media/timeline.css",
      "out/src/q1timeline/media/timeline.js",
      "out/src/q1timeline/media/timelineAdapter.js",
      "out/src/shared/timeline/renderer.css",
      "out/src/shared/timeline/renderer.js",
      "out/src/qbs/webview/assets/timeline.css",
      "out/src/qbs/webview/assets/timeline.js",
      "out/src/qbs/webview/assets/timelineModel.js",
    ]);
  });

  it("passes when installed runtime assets match the built extension assets", () => {
    tempDir = mkdtempSync(join(tmpdir(), "q1lens-installed-assets-"));
    const extensionRoot = join(tempDir, "extension");
    const installedRoot = join(tempDir, "installed");
    const files = ["media/q1lens-icon.png", "out/src/q1timeline/media/timeline.css"];

    for (const file of files) {
      writeFixtureFile(extensionRoot, file, `fresh ${file}`);
      writeFixtureFile(installedRoot, file, `fresh ${file}`);
    }

    const result = verifier().compareInstalledAssets({ extensionRoot, installedRoot, files });

    assert.equal(result.ok, true);
    assert.deepEqual(result.mismatches, []);
  });

  it("fails when an installed runtime asset is stale", () => {
    tempDir = mkdtempSync(join(tmpdir(), "q1lens-installed-assets-"));
    const extensionRoot = join(tempDir, "extension");
    const installedRoot = join(tempDir, "installed");
    const files = ["media/q1lens-icon.png", "out/src/q1timeline/media/timeline.css"];

    writeFixtureFile(extensionRoot, files[0], "fresh icon");
    writeFixtureFile(installedRoot, files[0], "fresh icon");
    writeFixtureFile(extensionRoot, files[1], "fresh timeline css");
    writeFixtureFile(installedRoot, files[1], "stale timeline css");

    const result = verifier().compareInstalledAssets({ extensionRoot, installedRoot, files });

    assert.equal(result.ok, false);
    assert.deepEqual(result.mismatches, [
      {
        file: "out/src/q1timeline/media/timeline.css",
        reason: "hash-mismatch",
      },
    ]);
  });
});
