const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execSync } = require("node:child_process");

const root = path.resolve(__dirname, "..", "..");
const output = path.join(root, "output", "loop-selection-review");
const resultsPath = path.join(output, "host-results.json");

async function main() {
  fs.mkdirSync(output, { recursive: true });
  const started = new Date().toISOString();
  fs.writeFileSync(resultsPath, JSON.stringify({ status: "starting", started_at: started, generated_at: started, results: [], failures: [] }, null, 2));
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, "cases.json"), "utf8"));
  for (const item of manifest.cases) {
    const modes = new Set([
      ...item.checks.map((check) => check.mode), ...item.points.map((point) => point.mode), ...item.no_loop_modes,
    ]);
    for (const mode of modes) {
      const fixture = path.join(output, "fixtures", item.id, `${mode}.json`);
      if (!fs.existsSync(fixture)) throw new Error(`Missing ${fixture}. Run python examples/loop-selection-review/verify.py first.`);
    }
  }
  const executable = [
    process.env.Q1LENS_VSCODE_EXECUTABLE,
    path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"), "Programs", "Microsoft VS Code", "Code.exe"),
    process.env.ProgramFiles && path.join(process.env.ProgramFiles, "Microsoft VS Code", "Code.exe"),
  ].find((candidate) => candidate && fs.existsSync(candidate));
  if (!executable) throw new Error("Installed VS Code was not found. Set Q1LENS_VSCODE_EXECUTABLE to Code.exe; this runner does not download VS Code.");

  execSync("npm run compile", { cwd: path.join(root, "vscode-extension"), stdio: "inherit", windowsHide: true });
  const { runTests } = require(path.join(root, "vscode-extension", "node_modules", "@vscode", "test-electron"));
  const runDirectory = fs.mkdtempSync(path.join(output, "host-run-"));
  const userData = path.join(runDirectory, "user-data");
  const extensions = path.join(runDirectory, "extensions");
  const workspace = path.join(runDirectory, "workspace");
  const qaExtension = path.join(runDirectory, "qa-extension");
  for (const directory of [path.join(userData, "User"), extensions, workspace, qaExtension]) fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(path.join(userData, "User", "settings.json"), JSON.stringify({
    "telemetry.telemetryLevel": "off", "extensions.autoUpdate": false, "update.mode": "none",
    "workbench.startupEditor": "none", "window.restoreWindows": "none", "extensions.ignoreRecommendations": true,
  }, null, 2));
  fs.writeFileSync(path.join(qaExtension, "package.json"), JSON.stringify({
    name: "q1lens-loop-selection-qa", publisher: "q1lens-qa", version: "0.0.0", engines: { vscode: "^1.90.0" },
    contributes: { languages: [{ id: "q1asm", extensions: [".q1asm"] }] },
  }, null, 2));
  fs.writeFileSync(resultsPath, JSON.stringify({ status: "starting", started_at: started, generated_at: started, executable,
    run_directory: runDirectory, results: [], failures: [] }, null, 2));
  const exitCode = await runTests({
    vscodeExecutablePath: executable,
    extensionDevelopmentPath: qaExtension,
    extensionTestsPath: path.join(__dirname, "host-suite.cjs"),
    launchArgs: [workspace, "--disable-extensions", "--user-data-dir", userData, "--extensions-dir", extensions,
      "--disable-gpu", "--skip-welcome", "--skip-release-notes", "--new-window"],
    extensionTestsEnv: { Q1LENS_LOOP_REVIEW_OUTPUT: output },
  });
  console.log(`Real VS Code verification exited ${exitCode}. Report: ${resultsPath}`);
}

main().catch((error) => {
  fs.mkdirSync(output, { recursive: true });
  const report = fs.existsSync(resultsPath) ? JSON.parse(fs.readFileSync(resultsPath, "utf8")) : {};
  report.status = "failed";
  report.launch_error = error.message;
  report.completed_at = new Date().toISOString();
  report.generated_at = report.completed_at;
  report.failures ||= [];
  report.failures.push({ check: "host launcher", error: error.message });
  fs.writeFileSync(resultsPath, JSON.stringify(report, null, 2));
  console.error(error);
  process.exitCode = 1;
});
