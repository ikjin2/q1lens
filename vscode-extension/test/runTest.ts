import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { basename, delimiter, join, resolve } from "node:path";
import { createServer } from "node:net";
import { runTests } from "@vscode/test-electron";

async function main(): Promise<void> {
  const extensionDevelopmentPath = resolve(__dirname, "..", "..");
  const extensionTestsPath = resolve(__dirname, "suite", "index");
  const repositoryPath = resolve(__dirname, "..", "..", "..");
  const pythonSourcePath = resolve(repositoryPath, "src");
  process.env.PYTHONPATH = process.env.PYTHONPATH ? `${pythonSourcePath}${delimiter}${process.env.PYTHONPATH}` : pythonSourcePath;
  const outputPath = resolve(process.env.Q1LENS_EXTENSION_HOST_OUTPUT ?? join(repositoryPath, "output", "ci", "extension-host"));
  process.env.Q1LENS_EXTENSION_HOST_OUTPUT = outputPath;
  mkdirSync(outputPath, { recursive: true });
  const workspacePath = mkdtempSync(join(outputPath, "workspace-"));
  const userDataPath = mkdtempSync(join(outputPath, "profile-"));
  for (const example of ["two-qubit-entangling", "simulation-verification", "def-register-sweep"]) {
    cpSync(join(repositoryPath, "examples", example), join(workspacePath, "examples", example), {
      recursive: true,
      filter: (source) => ![".q1timeline", ".qbs_timeline", ".verification", "__pycache__"].includes(basename(source)),
    });
  }
  const python = execFileSync(process.env.PYTHON ?? "python", ["-c", "import sys; print(sys.executable)"], { encoding: "utf8", windowsHide: true }).trim();
  mkdirSync(join(workspacePath, ".vscode"));
  const settings = {
    "q1lens.pythonPath": python,
    "q1lens.q1timeline.pythonPath": python,
  };
  writeFileSync(join(workspacePath, ".vscode", "settings.json"), JSON.stringify(settings, null, 2));
  // A saved workspace supports adding another folder without restarting the test host.
  const workspaceFile = join(outputPath, `${basename(workspacePath)}.code-workspace`);
  writeFileSync(workspaceFile, JSON.stringify({ folders: [{ path: workspacePath }], settings }, null, 2));

  const debugPort = await new Promise<number>((resolvePort, reject) => {
    const server = createServer().once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (address && typeof address === "object") server.close(() => resolvePort(address.port));
    });
  });
  await runTests({
    ...(process.env.Q1LENS_VSCODE_TEST_VERSION ? { version: process.env.Q1LENS_VSCODE_TEST_VERSION } : {}),
    extensionDevelopmentPath,
    extensionTestsPath,
    launchArgs: ["--disable-extensions", "--user-data-dir", userDataPath, `--remote-debugging-port=${debugPort}`, workspaceFile],
    extensionTestsEnv: { Q1LENS_DEF_WEBVIEW_CDP_PORT: String(debugPort) },
  });
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
