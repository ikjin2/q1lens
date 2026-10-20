import { dirname } from "node:path";
import type * as vscodeTypes from "vscode";
import { probePythonEnvironment } from "./environment";
import { showNotebookSetup, type NotebookEnvironmentState } from "./notebookEnvironment";
import { resolveFilePythonEnvironment, selectVSCodeInterpreter, type PythonEnvironmentKind, type ResolvedPythonEnvironment } from "./pythonEnvironment";
import { publishPythonEnvironment } from "./pythonEnvironmentStatus";
import { q1timelineConfigValue } from "./q1timeline/settings";

export async function ensureFilePythonEnvironment(
  vscode: typeof vscodeTypes,
  resource: vscodeTypes.Uri,
  kind: PythonEnvironmentKind,
  output: vscodeTypes.OutputChannel,
  options: { cwd?: string; env?: NodeJS.ProcessEnv } = {},
): Promise<ResolvedPythonEnvironment | undefined> {
  let selected: ResolvedPythonEnvironment | undefined;
  try {
    if (vscode.workspace.isTrusted === false) throw new Error("Trust this workspace before Q1Lens runs Python.");
    const check = async (): Promise<NotebookEnvironmentState> => {
      selected = await resolveFilePythonEnvironment(vscode, resource, kind);
      publishPythonEnvironment({ ...selected, kind, cwd: options.cwd });
      output.appendLine(`Python source: ${selected.sourceLabel}\nAnalysis target: ${resource.fsPath}\nPython command: ${JSON.stringify([selected.pythonPath, ...selected.pythonArgs])}`);
      const env = options.env ?? (kind === "q1asm" ? q1timelineConfigValue(
        vscode.workspace.getConfiguration("qbloxTimeline", resource), vscode.workspace.getConfiguration("q1timeline", resource),
        "analyzer.env", {}, vscode.workspace.getConfiguration("q1lens", resource),
      ) : {});
      const report = await probePythonEnvironment(selected.pythonPath, selected.pythonArgs, { cwd: options.cwd ?? dirname(resource.fsPath), env });
      publishPythonEnvironment({ ...selected, report, kind, cwd: options.cwd });
      if (!report.supported) throw new Error(`Python ${report.version} is too old. Select Python 3.10 or newer for this project.`);
      return { environment: { executable: report.executable }, report, sourceLabel: selected.sourceLabel };
    };
    let current = await check();
    while (current.report.qbsError || current.report.q1asmError) {
      const reason = current.report.q1lensInstalled === false ? "Q1Lens is not installed" : "Q1Lens could not be imported";
      const action = await vscode.window.showInformationMessage(
        `${reason} in this file's Python environment (${current.environment.executable}). Selected by: ${current.sourceLabel}.`,
        "Show Setup", "Check Again", "Select Python Environment",
      );
      if (action === "Check Again") { current = await check(); continue; }
      if (action === "Select Python Environment") {
        if (selected?.source === "vscode") await selectVSCodeInterpreter(vscode, resource);
        else await vscode.commands.executeCommand("q1lens.selectPythonEnvironment", undefined, resource);
        current = await check();
        continue;
      }
      if (action !== "Show Setup") return undefined;
      const version = vscode.extensions.getExtension("q1lens.q1lens")?.packageJSON.version;
      if (typeof version !== "string" || !/^\d+\.\d+\.\d+(?:[-+][\w.-]+)?$/.test(version)) throw new Error("Cannot determine the Q1Lens version for the suggested install command.");
      const ready = await showNotebookSetup(vscode, resource, current, version, check,
        () => vscode.window.showTextDocument(resource), true);
      return ready ? selected : undefined;
    }
    return selected;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    output.appendLine(message);
    publishPythonEnvironment({ ...(selected ?? { pythonPath: "", pythonArgs: [], resource, source: "vscode", sourceLabel: "VS Code selected interpreter" }), error: message, kind, cwd: options.cwd });
    const action = await vscode.window.showErrorMessage(`Q1Lens: ${message}`, "Select Python Environment", "Show Output");
    if (action === "Select Python Environment") await vscode.commands.executeCommand("q1lens.selectPythonEnvironment", undefined, resource);
    if (action === "Show Output") output.show(true);
    return undefined;
  }
}
