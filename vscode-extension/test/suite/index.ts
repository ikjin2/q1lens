import { readdir } from "node:fs/promises";
import { resolve } from "node:path";
import Mocha from "mocha";

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
  const files = await collectTestFiles(testsRoot);

  for (const file of files) {
    mocha.addFile(file);
  }

  await new Promise<void>((resolveRun, reject) => {
    mocha.run((failures) => {
      if (failures > 0) {
        reject(new Error(`${failures} extension tests failed`));
      } else {
        resolveRun();
      }
    });
  });
}
