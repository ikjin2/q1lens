import { dirname } from "node:path";
import type * as vscodeTypes from "vscode";
import { probePythonEnvironment } from "./environment";
import { showNotebookSetup, type NotebookEnvironmentState } from "./notebookEnvironment";
import { resolveFilePythonEnvironment, selectVSCodeInterpreter, type PythonEnvironmentKind, type ResolvedPythonEnvironment } from "./pythonEnvironment";
import { beginPythonEnvironmentCheck, publishPythonEnvironment } from "./pythonEnvironmentStatus";
import { q1timelineConfigValue } from "./q1timeline/settings";

function waitForEnvironmentAction<T>(action: () => PromiseLike<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return Promise.resolve(action());
  if (signal.aborted) return Promise.reject(signal.reason);
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason);
    signal.addEventListener("abort", abort, { once: true });
    Promise.resolve().then(() => {
      signal.throwIfAborted();
      return action();
    }).then((result) => {
      signal.removeEventListener("abort", abort);
      resolve(result);
    }, (error) => {
      signal.removeEventListener("abort", abort);
      reject(error);
    });
  });
}

export async function ensureFilePythonEnvironment(
  vscode: typeof vscodeTypes,
  resource: vscodeTypes.Uri,
  kind: PythonEnvironmentKind,
  output: vscodeTypes.OutputChannel,
  options: { cwd?: string; env?: NodeJS.ProcessEnv; signal?: AbortSignal } = {},
): Promise<ResolvedPythonEnvironment | undefined> {
  if (options.signal?.aborted) return undefined;
  let selected: ResolvedPythonEnvironment | undefined;
  let request = beginPythonEnvironmentCheck();
  let checked = false;
  try {
    if (vscode.workspace.isTrusted === false) throw new Error("Trust this workspace before Q1Lens runs Python.");
    const check = async (): Promise<NotebookEnvironmentState> => {
      if (checked) request = beginPythonEnvironmentCheck();
      checked = true;
      const checking = request;
      selected = await waitForEnvironmentAction(() => resolveFilePythonEnvironment(vscode, resource, kind), options.signal);
      options.signal?.throwIfAborted();
      publishPythonEnvironment({ ...selected, kind, cwd: options.cwd }, checking);
      output.appendLine(`Python source: ${selected.sourceLabel}\nAnalysis target: ${resource.fsPath}\nPython command: ${JSON.stringify([selected.pythonPath, ...selected.pythonArgs])}`);
      const env = options.env ?? (kind === "q1asm" ? q1timelineConfigValue(
        vscode.workspace.getConfiguration("qbloxTimeline", resource), vscode.workspace.getConfiguration("q1timeline", resource),
        "analyzer.env", {}, vscode.workspace.getConfiguration("q1lens", resource),
      ) : {});
      const assertCurrentSelection = selected.assertCurrentSelection;
      const report = await waitForEnvironmentAction(() => probePythonEnvironment(selected!.pythonPath, selected!.pythonArgs, { cwd: options.cwd ?? dirname(resource.fsPath), env }), options.signal).finally(() => {
        if (!options.signal?.aborted) assertCurrentSelection?.();
      });
      options.signal?.throwIfAborted();
      publishPythonEnvironment({ ...selected, report, kind, cwd: options.cwd }, checking);
      if (!report.supported) throw new Error(`Python ${report.version} is too old. Select Python 3.10 or newer for this project.`);
      return { environment: { executable: report.executable }, report, sourceLabel: selected.sourceLabel };
    };
    let current = await check();
    while (current.report.qbsError || current.report.q1asmError) {
      const reason = current.report.q1lensInstalled === false ? "Q1Lens is not installed" : "Q1Lens could not be imported";
      const action = await waitForEnvironmentAction(() => vscode.window.showInformationMessage(
        `${reason} in this file's Python environment (${current.environment.executable}). Selected by: ${current.sourceLabel}.`,
        "Show Setup", "Check Again", "Select Python Environment",
      ), options.signal);
      options.signal?.throwIfAborted();
      if (action === "Check Again") { current = await check(); continue; }
      if (action === "Select Python Environment") {
        if (selected?.source === "vscode") await waitForEnvironmentAction(() => selectVSCodeInterpreter(vscode, resource, options.signal), options.signal);
        else await waitForEnvironmentAction(() => vscode.commands.executeCommand("q1lens.selectPythonEnvironment", undefined, resource), options.signal);
        current = await check();
        continue;
      }
      if (action !== "Show Setup") return undefined;
      const version = vscode.extensions.getExtension("q1lens.q1lens")?.packageJSON.version;
      if (typeof version !== "string" || !/^\d+\.\d+\.\d+(?:[-+][\w.-]+)?$/.test(version)) throw new Error("Cannot determine the Q1Lens version for the suggested install command.");
      const ready = await showNotebookSetup(vscode, resource, current, version, check,
        () => vscode.window.showTextDocument(resource), true, options.signal);
      return ready ? selected : undefined;
    }
    return selected;
  } catch (error) {
    if (options.signal?.aborted) return undefined;
    const message = error instanceof Error ? error.message : String(error);
    output.appendLine(message);
    publishPythonEnvironment({ ...(selected ?? { pythonPath: "", pythonArgs: [], resource, source: "vscode", sourceLabel: "VS Code selected interpreter" }), error: message, kind, cwd: options.cwd }, request);
    try {
      const action = await waitForEnvironmentAction(() => vscode.window.showErrorMessage(`Q1Lens: ${message}`, "Select Python Environment", "Show Output"), options.signal);
      options.signal?.throwIfAborted();
      if (action === "Select Python Environment") await waitForEnvironmentAction(() => vscode.commands.executeCommand("q1lens.selectPythonEnvironment", undefined, resource), options.signal);
      if (action === "Show Output") output.show(true);
    } catch (error) {
      if (!options.signal?.aborted) throw error;
    }
    return undefined;
  }
}
