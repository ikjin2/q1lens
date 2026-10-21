import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, extname, join } from "node:path";
import type * as vscodeTypes from "vscode";
import { q1timelineConfigValue } from "./q1timeline/settings";
import { beginPythonEnvironmentSelection, resolveFilePythonEnvironment, selectVSCodeInterpreter, type PythonEnvironmentKind } from "./pythonEnvironment";
import { beginPythonEnvironmentCheck, isCurrentPythonEnvironmentCheck, lastPythonEnvironment, publishPythonEnvironment } from "./pythonEnvironmentStatus";

export interface PythonEnvironmentReport {
  executable: string;
  version: string;
  supported: boolean;
  q1lensInstalled?: boolean;
  pipAvailable?: boolean;
  packagePath?: string;
  packageVersion?: string;
  q1asmPath?: string;
  qbsError?: string;
  q1asmError?: string;
}

const PROBE = `import contextlib, importlib.util, io, json, sys
r = {"executable": sys.executable, "version": ".".join(map(str, sys.version_info[:3])), "supported": sys.version_info >= (3, 10)}
r["q1lensInstalled"] = importlib.util.find_spec("q1lens") is not None
r["pipAvailable"] = importlib.util.find_spec("pip") is not None
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
  action?: "browse" | "enter" | "vscode";
}

export function registerEnvironmentCommands(context: vscodeTypes.ExtensionContext): void {
  const vscode = require("vscode") as typeof vscodeTypes;
  const output = vscode.window.createOutputChannel("Q1Lens Python Environment");
  const selectLabel = "Select Python Environment";

  function targetResource(resource?: vscodeTypes.Uri) {
    const textResource = vscode.window.activeTextEditor?.document.uri;
    const activeResource = textResource?.scheme === "vscode-notebook-cell"
      ? vscode.window.activeNotebookEditor?.notebook.uri ?? textResource
      : textResource ?? vscode.window.activeNotebookEditor?.notebook.uri;
    return resource ?? activeResource ?? lastPythonEnvironment()?.resource
      ?? vscode.workspace.workspaceFolders?.[0]?.uri;
  }

  function workingDirectory(resource: vscodeTypes.Uri) {
    return vscode.workspace.workspaceFolders?.some((folder) => folder.uri.fsPath === resource.fsPath)
      ? resource.fsPath : dirname(resource.fsPath);
  }

  async function environments(resource: vscodeTypes.Uri) {
    const primary = vscode.workspace.getConfiguration("q1lens", resource);
    const qblox = vscode.workspace.getConfiguration("qbloxTimeline", resource);
    const legacy = vscode.workspace.getConfiguration("q1timeline", resource);
    const get = <T>(key: string, fallback: T) => q1timelineConfigValue(qblox, legacy, key, fallback, primary);
    return Promise.all((["qbs", "q1asm"] as PythonEnvironmentKind[]).map(async (kind) => {
      const details = { label: kind === "qbs" ? "QBS" : "Q1ASM", env: kind === "q1asm" ? get<Record<string, string>>("analyzer.env", {}) : {} };
      try { return { ...details, ...await resolveFilePythonEnvironment(vscode, resource, kind), resolutionError: undefined as string | undefined }; }
      catch (error) { return { ...details, resource, pythonPath: "", pythonArgs: [] as string[], source: "vscode" as const,
        sourceLabel: "VS Code selected interpreter", resolutionError: error instanceof Error ? error.message : String(error) }; }
    }));
  }

  async function checkEnvironment(retryCommand?: string, target?: vscodeTypes.Uri, retryTarget?: vscodeTypes.Uri) {
    const resource = targetResource(target);
    if (!resource) { await vscode.window.showInformationMessage("Open a project or a saved file to choose its Python environment."); return []; }
    const request = beginPythonEnvironmentCheck();
    const extension = extname(resource.fsPath).toLowerCase();
    output.clear();
    output.show(true);
    output.appendLine("Q1Lens Python environment check (Python 3.10 or newer required)");
    output.appendLine("Checks the Q1Lens CLI and Q1ASM bridge imports. Schedule-specific dependencies are checked when analyzing a project.");
    if (extension === ".ipynb") {
      const { ensureNotebookEnvironment } = await import("./notebookEnvironment");
      if (!await ensureNotebookEnvironment(vscode, resource, output, request) || !isCurrentPythonEnvironmentCheck(request)) return [];
    }
    const latest = lastPythonEnvironment();
    const selected = latest?.source === "notebook" && latest.resource.fsPath === resource.fsPath
      ? [{ ...latest, label: "Notebook", env: {}, resolutionError: undefined as string | undefined }]
      : await environments(resource);
    const results = await Promise.all(selected.map(async (environment) => {
      try {
        if (environment.resolutionError) throw new Error(environment.resolutionError);
        const report = environment.source === "notebook" && latest?.report ? latest.report
          : await probePythonEnvironment(environment.pythonPath, environment.pythonArgs, {
            cwd: latest?.resource.fsPath === resource.fsPath ? latest.cwd ?? workingDirectory(resource) : workingDirectory(resource), env: environment.env }).finally(() => {
              if ("assertCurrentSelection" in environment) environment.assertCurrentSelection?.();
            });
        const error = !report.supported ? "Python 3.10 or newer is required."
          : report.qbsError || report.q1asmError;
        return { ...environment, report, error };
      } catch (error) {
        return { ...environment, report: undefined, error: String(error) };
      }
    }));
    if (!isCurrentPythonEnvironmentCheck(request)) return results;
    for (const result of results) {
      output.appendLine(`\n${result.label}: ${result.error ? "NOT READY" : "READY"}`);
      output.appendLine(`Selected by: ${result.sourceLabel}\nAnalysis target: ${resource.fsPath}`);
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
    const kind = latest?.resource.fsPath === resource.fsPath && latest.kind ? latest.kind : extension === ".q1asm" ? "q1asm" : "qbs";
    const displayed = results.find((result) => result.label === (kind === "q1asm" ? "Q1ASM" : "QBS")) ?? results[0];
    if (displayed) publishPythonEnvironment({ ...displayed, kind, cwd: latest?.resource.fsPath === resource.fsPath ? latest.cwd : undefined }, request);
    const ready = displayed && !displayed.error;
    const retryLabel = retryCommand ? "Retry Timeline" : "Open Timeline";
    const message = ready
      ? "The selected Q1Lens Python environment is ready. See Q1Lens Python Environment for paths and versions."
      : "Q1Lens Python environment needs attention. See Q1Lens Python Environment for the selected paths and next steps.";
    const notification = ready
      ? vscode.window.showInformationMessage(message, retryLabel, selectLabel)
      : vscode.window.showErrorMessage(message, selectLabel, "Check Again");
    void notification.then(async (action) => {
      if (action === selectLabel) await selectEnvironment(retryCommand, resource, retryTarget);
      else if (action === "Check Again") await checkEnvironment(retryCommand, resource, retryTarget);
      else if (action === retryLabel) {
        if (!retryCommand && extension === ".ipynb") {
          await vscode.window.showNotebookDocument(await vscode.workspace.openNotebookDocument(resource));
          await vscode.commands.executeCommand("qbsTimeline.openNotebookTimelineFromCell");
        } else await vscode.commands.executeCommand(retryCommand ?? (kind === "q1asm" ? "q1timeline.openPreview" : "qbsTimeline.analyzeAndOpen"), retryTarget ?? resource);
      }
    });
    return results;
  }

  async function selectEnvironment(retryCommand?: string, target?: vscodeTypes.Uri, retryTarget?: vscodeTypes.Uri) {
    const resource = targetResource(target);
    if (!resource) { await vscode.window.showInformationMessage("Open a project or saved file before choosing Python."); return; }
    const folder = vscode.workspace.getWorkspaceFolder(resource);
    const scope = folder ? vscode.ConfigurationTarget.WorkspaceFolder
      : vscode.workspace.workspaceFolders?.length ? vscode.ConfigurationTarget.Workspace : vscode.ConfigurationTarget.Global;
    const { isCurrent, updateSettings } = beginPythonEnvironmentSelection(vscode, resource);
    const currentEnvironments = await environments(resource);
    if (!isCurrent()) return;
    const choices: PythonChoice[] = currentEnvironments.filter((item) => !item.resolutionError).map((item) => ({
      label: `Current ${item.label} Python`, description: item.pythonPath,
      detail: `${item.sourceLabel}${item.pythonArgs.length ? ` · Arguments: ${JSON.stringify(item.pythonArgs)}` : ""}`,
      pythonPath: item.pythonPath, pythonArgs: item.pythonArgs,
      action: item.source === "vscode" ? "vscode" : undefined,
    }));
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      for (const name of [".venv", "venv"]) {
        const executable = join(folder.uri.fsPath, name, ...(process.platform === "win32" ? ["Scripts", "python.exe"] : ["bin", "python"]));
        if (existsSync(executable)) choices.push({ label: `${folder.name}/${name}`, description: executable, pythonPath: executable });
      }
    }
    choices.push(
      { label: "Select Interpreter in VS Code…", action: "vscode" },
      { label: "Python on PATH", pythonPath: "python", description: "python" },
      { label: "Browse for Python executable…", action: "browse" },
      { label: "Enter Python path…", action: "enter" },
    );
    const choice = await vscode.window.showQuickPick(choices, {
      title: "Q1Lens: Select Python Environment",
      placeHolder: "Use this Python for QBS and Q1ASM in this workspace",
      ignoreFocusOut: true,
    });
    if (!isCurrent() || !choice) return;
    if (choice.action === "vscode") {
      await selectVSCodeInterpreter(vscode, resource);
      if (!isCurrent()) return;
      if (currentEnvironments.some((item) => item.source === "manual")) {
        const action = await vscode.window.showInformationMessage("Q1Lens still uses your explicit Python settings. Follow the VS Code selection for this project while keeping those settings saved?", "Follow VS Code Selection");
        if (!isCurrent()) return;
        if (action) {
          const followScope = folder ? vscode.ConfigurationTarget.WorkspaceFolder : vscode.ConfigurationTarget.Global;
          if (!await updateSettings(async () => {
            await vscode.workspace.getConfiguration("q1lens", resource).update("pythonEnvironment", "vscode", followScope);
          })) return;
        }
      }
      return checkEnvironment(retryCommand, resource, retryTarget);
    }
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
    if (!isCurrent() || !pythonPath?.trim()) return;
    pythonPath = pythonPath.trim();
    const pythonArgs = choice.pythonArgs ?? [];
    try {
      const report = await probePythonEnvironment(pythonPath, pythonArgs, { cwd: workingDirectory(resource) });
      if (!isCurrent()) return;
      if (!report.supported) throw new Error(`Python ${report.version} is too old. Select Python 3.10 or newer.`);
    } catch (error) {
      if (!isCurrent()) return;
      void vscode.window.showErrorMessage(String(error), selectLabel).then((action) => {
        if (action === selectLabel) return selectEnvironment(retryCommand, resource, retryTarget);
        return undefined;
      });
      return;
    }
    if (!await updateSettings(async () => {
      const config = vscode.workspace.getConfiguration("q1lens", resource);
      await config.update("pythonPath", pythonPath, scope);
      await config.update("pythonArgs", pythonArgs, scope);
      await config.update("q1timeline.pythonPath", pythonPath, scope);
      await config.update("q1timeline.pythonArgs", pythonArgs, scope);
      if (config.get<string>("pythonEnvironment", "auto") === "vscode") await config.update("pythonEnvironment", "auto", scope);
    })) return;
    return checkEnvironment(retryCommand, resource, retryTarget);
  }

  context.subscriptions.push(
    output,
    vscode.commands.registerCommand("q1lens.checkEnvironment", checkEnvironment),
    vscode.commands.registerCommand("q1lens.selectPythonEnvironment", selectEnvironment),
  );
}
