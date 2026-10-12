import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, delimiter, join, resolve } from "node:path";
import { runTests } from "@vscode/test-electron";

async function main(): Promise<void> {
  const extensionDevelopmentPath = resolve(__dirname, "..", "..");
  const extensionTestsPath = resolve(__dirname, "suite", "index");
  const repositoryPath = resolve(__dirname, "..", "..", "..");
  const pythonSourcePath = resolve(repositoryPath, "src");
  process.env.PYTHONPATH = process.env.PYTHONPATH ? `${pythonSourcePath}${delimiter}${process.env.PYTHONPATH}` : pythonSourcePath;
  const testRoot = mkdtempSync(join(tmpdir(), "q1lens-extension-test-"));
  const workspacePath = join(testRoot, "workspace");
  const userDataPath = join(testRoot, "profile");
  cpSync(join(repositoryPath, "examples", "two-qubit-entangling"), join(workspacePath, "examples", "two-qubit-entangling"), {
    recursive: true,
    filter: (source) => ![".q1timeline", ".qbs_timeline", "__pycache__"].includes(basename(source)),
  });
  const python = execFileSync(process.env.PYTHON ?? "python", ["-c", "import sys; print(sys.executable)"], { encoding: "utf8", windowsHide: true }).trim();
  mkdirSync(join(workspacePath, ".vscode"));
  writeFileSync(join(workspacePath, ".vscode", "settings.json"), JSON.stringify({
    "q1lens.pythonPath": python,
  }, null, 2));

  await runTests({
    ...(process.env.Q1LENS_VSCODE_TEST_VERSION ? { version: process.env.Q1LENS_VSCODE_TEST_VERSION } : {}),
    extensionDevelopmentPath,
    extensionTestsPath,
    launchArgs: ["--disable-extensions", "--user-data-dir", userDataPath, workspacePath],
  });
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
