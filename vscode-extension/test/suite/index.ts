import { readdir } from "node:fs/promises";
import assert from "node:assert/strict";
import { dirname, resolve } from "node:path";
import Mocha from "mocha";
import * as vscode from "vscode";

async function collectTestFiles(root: string): Promise<string[]> {
  const entries = await readdir(root, { withFileTypes: true });
  const files = await Promise.all(
    entries.map(async (entry) => {
      const path = resolve(root, entry.name);
      if (entry.isDirectory()) {
        return collectTestFiles(path);
      }
      return entry.isFile() && entry.name.endsWith(".test.js") ? [path] : [];
    }),
  );
  return files.flat().sort();
}

export async function run(): Promise<void> {
  const mocha = new Mocha({ ui: "bdd", color: true });
  const testsRoot = resolve(__dirname);
  const installed = process.env.Q1LENS_INSTALLED_EXTENSIONS_DIR;
  const files = installed ? [resolve(testsRoot, "verification.test.js")] : await collectTestFiles(testsRoot);
  if (installed) {
    const extension = vscode.extensions.getExtension("q1lens.q1lens");
    assert.ok(extension, "Missing installed Q1Lens extension");
    assert.equal(dirname(extension.extensionPath), installed);
    mocha.grep("opens the queue underflow from Verify Program at 1188 ns");
  }

  for (const file of files) {
    mocha.addFile(file);
  }

  await new Promise<void>((resolveRun, reject) => {
    const runner = mocha.run((failures) => {
      if (failures > 0 || (installed && runner.stats?.passes !== 1)) {
        reject(new Error(`${failures} extension tests failed; ${runner.stats?.passes} passed`));
      } else {
        resolveRun();
      }
    });
  });
}
