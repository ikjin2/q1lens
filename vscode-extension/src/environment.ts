import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type * as vscodeTypes from "vscode";
import { readSettings } from "./qbs/settings";
import { q1timelineConfigValue } from "./q1timeline/settings";

export interface PythonEnvironmentReport {
  executable: string;
  version: string;
  supported: boolean;
  packagePath?: string;
  packageVersion?: string;
  q1asmPath?: string;
  qbsError?: string;
  q1asmError?: string;
}

const PROBE = `import contextlib, io, json, sys
r = {"executable": sys.executable, "version": ".".join(map(str, sys.version_info[:3])), "supported": sys.version_info >= (3, 10)}
if r["supported"]:
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            import q1lens, q1lens.cli
            r["packagePath"] = q1lens.__file__
            from importlib.metadata import version, PackageNotFoundError
            try:
                r["packageVersion"] = version("q1lens")
            except PackageNotFoundError:
                pass
        except Exception as e:
            r["qbsError"] = str(e)
        if "qbsError" not in r:
            try:
                from qbstimeline.q1timeline_bridge import import_q1timeline_cli
                r["q1asmPath"] = import_q1timeline_cli().__file__
            except Exception as e:
                r["q1asmError"] = str(e)
print(json.dumps(r))`;

export function probePythonEnvironment(
  pythonPath: string,
  pythonArgs: string[],
  options: { cwd?: string; env?: NodeJS.ProcessEnv; timeoutMs?: number } = {},
): Promise<PythonEnvironmentReport> {
  return new Promise((resolve, reject) => {
    execFile(pythonPath, [...pythonArgs, "-c", PROBE], {
      cwd: options.cwd,
      env: { ...process.env, ...options.env },
      timeout: options.timeoutMs ?? 15000,
      maxBuffer: 1024 * 1024,
      windowsHide: true,
    }, (error, stdout, stderr) => {
      if (error) {
        const reason = error.code === "ENOENT" ? "Python executable was not found."
          : error.killed ? "Python environment check timed out."
          : stderr.trim() || `Python exited with ${error.code ?? error.signal}.`;
        reject(new Error(`${pythonPath}: ${reason}`));
        return;
      }
      try {
        const report = JSON.parse(stdout) as PythonEnvironmentReport;
        if (typeof report.executable !== "string" || typeof report.version !== "string" || typeof report.supported !== "boolean") {
          throw new Error("missing Python environment details");
        }
        resolve(report);
      } catch {
        reject(new Error(`${pythonPath}: Python did not return a valid environment report.`));
      }
    });
  });
}

interface PythonChoice extends vscodeTypes.QuickPickItem {
  pythonPath?: string;
  pythonArgs?: string[];
  action?: "browse" | "enter";
}

export function registerEnvironmentCommands(context: vscodeTypes.ExtensionContext): void {
  const vscode = require("vscode") as typeof vscodeTypes;
  const output = vscode.window.createOutputChannel("Q1Lens Python Environment");
  const selectLabel = "Select Python Environment";

  function environments() {
    const qbs = readSettings();
    const primary = vscode.workspace.getConfiguration("q1lens");
    const qblox = vscode.workspace.getConfiguration("qbloxTimeline");
    const legacy = vscode.workspace.getConfiguration("q1timeline");
    const get = <T>(key: string, fallback: T) => q1timelineConfigValue(qblox, legacy, key, fallback, primary);
    return [
      { label: "QBS", pythonPath: qbs.pythonPath, pythonArgs: qbs.pythonArgs, env: {} },
      {
        label: "Q1ASM", pythonPath: get("pythonPath", "python"), pythonArgs: get<string[]>("pythonArgs", []),
        env: get<Record<string, string>>("analyzer.env", {}),
      },
    ];
  }

  const cwd = () => vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;

  async function checkEnvironment(retryCommand?: string) {
    output.clear();
    output.show(true);
    output.appendLine("Q1Lens Python environment check (Python 3.10 or newer required)");
    output.appendLine("Checks the Q1Lens CLI and Q1ASM bridge imports. Schedule-specific dependencies are checked when analyzing a project.");
    const results = await Promise.all(environments().map(async (environment) => {
      try {
        const report = await probePythonEnvironment(environment.pythonPath, environment.pythonArgs, { cwd: cwd(), env: environment.env });
        const error = !report.supported ? "Python 3.10 or newer is required."
          : report.qbsError || report.q1asmError;
        return { ...environment, report, error };
      } catch (error) {
        return { ...environment, report: undefined, error: String(error) };
      }
    }));
    for (const result of results) {
      output.appendLine(`\n${result.label}: ${result.error ? "NOT READY" : "READY"}`);
      output.appendLine(`Configured command: ${JSON.stringify([result.pythonPath, ...result.pythonArgs])}`);
      if (Object.keys(result.env).length) output.appendLine(`Environment override keys: ${Object.keys(result.env).join(", ")}`);
      if (result.report) {
        output.appendLine(`Python: ${result.report.executable} (${result.report.version})`);
        output.appendLine(`Q1Lens: ${result.report.packageVersion ?? "no installed distribution metadata"}`);
        output.appendLine(`Package location: ${result.report.packagePath ?? "unavailable"}`);
        output.appendLine(`Q1ASM bridge: ${result.report.q1asmPath ?? "unavailable"}`);
      }
      if (result.error) {
        output.appendLine(result.error);
        if (result.report?.supported) {
          const executable = "'" + result.report.executable.replace(/'/g, process.platform === "win32" ? "''" : "'\"'\"'") + "'";
          output.appendLine(`Install the Q1Lens release wheel into this environment, then check again:\n${process.platform === "win32" ? "& " : ""}${executable} -m pip install '<path-to-q1lens.whl>'`);
        } else output.appendLine("Select an installed Python 3.10+ environment that runs your project.");
      }
    }
    const ready = results.every((result) => !result.error);
    const retryLabel = retryCommand ? "Retry Timeline" : "Open Timeline";
    const message = ready
      ? "Q1Lens Python environments are ready. See Q1Lens Python Environment for paths and versions."
      : "Q1Lens Python environment needs attention. See Q1Lens Python Environment for the selected paths and next steps.";
    const notification = ready
      ? vscode.window.showInformationMessage(message, retryLabel, selectLabel)
      : vscode.window.showErrorMessage(message, selectLabel, "Check Again");
    void notification.then(async (action) => {
      if (action === selectLabel) await selectEnvironment(retryCommand);
      else if (action === "Check Again") await checkEnvironment(retryCommand);
      else if (action === retryLabel) {
        const source = vscode.window.activeTextEditor?.document.uri;
        await vscode.commands.executeCommand(retryCommand ?? (source?.fsPath.endsWith(".q1asm") ? "q1timeline.openPreview" : "qbsTimeline.analyzeAndOpen"), source);
      }
    });
    return results;
  }

  async function selectEnvironment(retryCommand?: string) {
    const choices: PythonChoice[] = environments().map((item) => ({
      label: `Current ${item.label} Python`, description: item.pythonPath,
      detail: item.pythonArgs.length ? `Arguments: ${JSON.stringify(item.pythonArgs)}` : undefined,
      pythonPath: item.pythonPath, pythonArgs: item.pythonArgs,
    }));
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      for (const name of [".venv", "venv"]) {
        const executable = join(folder.uri.fsPath, name, ...(process.platform === "win32" ? ["Scripts", "python.exe"] : ["bin", "python"]));
        if (existsSync(executable)) choices.push({ label: `${folder.name}/${name}`, description: executable, pythonPath: executable });
      }
    }
    choices.push(
      { label: "Python on PATH", pythonPath: "python", description: "python" },
      { label: "Browse for Python executable…", action: "browse" },
      { label: "Enter Python path…", action: "enter" },
    );
    const choice = await vscode.window.showQuickPick(choices, {
      title: "Q1Lens: Select Python Environment",
      placeHolder: "Use this Python for QBS and Q1ASM in this workspace",
      ignoreFocusOut: true,
    });
    if (!choice) return;
    let pythonPath = choice.pythonPath;
    if (choice.action === "browse") {
      pythonPath = (await vscode.window.showOpenDialog({
        title: "Select Python executable", canSelectFiles: true, canSelectFolders: false, canSelectMany: false,
        openLabel: "Use Python",
      }))?.[0]?.fsPath;
    } else if (choice.action === "enter") {
      pythonPath = await vscode.window.showInputBox({
        title: "Q1Lens Python executable", prompt: "Executable path only, without quotes or arguments (for example C:\\project\\.venv\\Scripts\\python.exe).",
        ignoreFocusOut: true,
      });
    }
    if (!pythonPath?.trim()) return;
    pythonPath = pythonPath.trim();
    const pythonArgs = choice.pythonArgs ?? [];
    try {
      const report = await probePythonEnvironment(pythonPath, pythonArgs, { cwd: cwd() });
      if (!report.supported) throw new Error(`Python ${report.version} is too old. Select Python 3.10 or newer.`);
    } catch (error) {
      void vscode.window.showErrorMessage(String(error), selectLabel).then((action) => {
        if (action === selectLabel) return selectEnvironment(retryCommand);
        return undefined;
      });
      return;
    }
    const config = vscode.workspace.getConfiguration("q1lens");
    const target = vscode.workspace.workspaceFolders?.length ? vscode.ConfigurationTarget.Workspace : vscode.ConfigurationTarget.Global;
    await config.update("pythonPath", pythonPath, target);
    await config.update("pythonArgs", pythonArgs, target);
    await config.update("q1timeline.pythonPath", pythonPath, target);
    await config.update("q1timeline.pythonArgs", pythonArgs, target);
    return checkEnvironment(retryCommand);
  }

  context.subscriptions.push(
    output,
    vscode.commands.registerCommand("q1lens.checkEnvironment", checkEnvironment),
    vscode.commands.registerCommand("q1lens.selectPythonEnvironment", selectEnvironment),
  );
}
