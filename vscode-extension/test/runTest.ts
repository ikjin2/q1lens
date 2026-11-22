import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { basename, delimiter, join, resolve } from "node:path";
import { createServer } from "node:net";
import { runTests, runVSCodeCommand } from "@vscode/test-electron";

async function main(): Promise<void> {
  let extensionDevelopmentPath = resolve(__dirname, "..", "..");
  const extensionTestsPath = resolve(__dirname, "suite", "index");
  const repositoryPath = resolve(__dirname, "..", "..", "..");
  const pythonSourcePath = resolve(repositoryPath, "src");
  const vsix = process.argv[2];
  if (vsix) delete process.env.PYTHONPATH;
  else process.env.PYTHONPATH = process.env.PYTHONPATH ? `${pythonSourcePath}${delimiter}${process.env.PYTHONPATH}` : pythonSourcePath;
  const outputPath = resolve(process.env.Q1LENS_EXTENSION_HOST_OUTPUT ?? join(repositoryPath, "output", "ci", "extension-host"));
  process.env.Q1LENS_EXTENSION_HOST_OUTPUT = outputPath;
  mkdirSync(outputPath, { recursive: true });
  const workspacePath = mkdtempSync(join(outputPath, "workspace-"));
  const userDataPath = mkdtempSync(join(outputPath, "profile-"));
  const examples = vsix ? ["verification-demo"] : ["two-qubit-entangling", "simulation-verification", "def-register-sweep", "verification-demo", "symbolic-output"];
  for (const example of examples) {
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
  const version = process.env.Q1LENS_VSCODE_TEST_VERSION;
  // test-electron invokes both the Code CLI and test host through cmd.exe on Windows.
  const pathArg = (path: string) => process.platform === "win32" ? `"${path}"` : path;
  const launchArgs = ["--user-data-dir", pathArg(userDataPath), `--remote-debugging-port=${debugPort}`, pathArg(workspaceFile)];
  let installedExtensionsDir: string | undefined;
  if (vsix) {
    installedExtensionsDir = mkdtempSync(join(outputPath, "extensions-"));
    launchArgs.push("--extensions-dir", pathArg(installedExtensionsDir));
    const install = await runVSCodeCommand(["--user-data-dir", pathArg(userDataPath), "--extensions-dir", pathArg(installedExtensionsDir),
      "--install-extension", pathArg(resolve(vsix))], { version });
    console.log(install.stdout);
    // Only the test driver is a development extension. Q1Lens loads from the installed VSIX.
    extensionDevelopmentPath = mkdtempSync(join(outputPath, "driver-"));
    writeFileSync(join(extensionDevelopmentPath, "package.json"), JSON.stringify({
      name: "installed-q1lens-test", publisher: "local", version: "0.0.1", engines: { vscode: "^1.90.0" },
    }));
  } else launchArgs.push("--disable-extensions");
  await runTests({
    version,
    extensionDevelopmentPath: pathArg(extensionDevelopmentPath),
    extensionTestsPath: pathArg(extensionTestsPath),
    launchArgs,
    extensionTestsEnv: { Q1LENS_DEF_WEBVIEW_CDP_PORT: String(debugPort),
      ...(installedExtensionsDir && { Q1LENS_INSTALLED_EXTENSIONS_DIR: installedExtensionsDir }) },
  });
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
