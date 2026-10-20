import { randomUUID } from "node:crypto";
import { basename, dirname } from "node:path";
import type * as vscodeTypes from "vscode";
import type { PythonEnvironmentReport } from "./environment";
import { selectVSCodeInterpreter } from "./pythonEnvironment";

export interface PythonEnvironmentDisplay {
  pythonPath: string;
  pythonArgs: string[];
  source: "vscode" | "manual" | "notebook";
  sourceLabel: string;
  resource: vscodeTypes.Uri;
  kind?: "qbs" | "q1asm";
  cwd?: string;
  report?: PythonEnvironmentReport;
  error?: string;
}

let current: PythonEnvironmentDisplay | undefined;
let update: (() => void) | undefined;

export function lastPythonEnvironment(): PythonEnvironmentDisplay | undefined { return current; }

export function publishPythonEnvironment(environment: PythonEnvironmentDisplay): void {
  current = environment;
  update?.();
}

export function pythonEnvironmentHtml(environment: PythonEnvironmentDisplay): string {
  const escape = (value: string) => value.replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]!);
  const nonce = randomUUID();
  const report = environment.report;
  const error = environment.error || (report && (!report.supported ? "Python 3.10 or newer is required." : report.qbsError || report.q1asmError));
  const state = error ? "Needs attention" : report ? "Ready" : "Checking…";
  const command = JSON.stringify([environment.pythonPath, ...environment.pythonArgs]);
  return `<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
<title>Q1Lens Python Environment</title><style nonce="${nonce}">
body { font-family:var(--vscode-font-family); color:var(--vscode-foreground); padding:28px; line-height:1.6; }
main { max-width:780px; margin:auto; } h1 { font-size:26px; margin:8px 0; } .muted,dt { color:var(--vscode-descriptionForeground); }
.state { display:inline-block; padding:4px 12px; border:1px solid var(--vscode-panel-border); border-radius:12px; }
dl { display:grid; grid-template-columns:110px 1fr; gap:14px 20px; margin:28px 0; } dd { margin:0; overflow-wrap:anywhere; }
code { font-family:var(--vscode-editor-font-family); } .note { padding:14px; background:var(--vscode-textBlockQuote-background); border-left:3px solid var(--vscode-focusBorder); }
button { font:inherit; padding:7px 12px; margin:12px 8px 0 0; cursor:pointer; border:1px solid transparent; background:var(--vscode-button-background); color:var(--vscode-button-foreground); }
button:focus-visible { outline:1px solid var(--vscode-focusBorder); outline-offset:2px; }
</style></head><body><main><div class="muted">Q1LENS · PYTHON ENVIRONMENT</div>
<h1>Your analysis environment</h1><span class="state">${escape(state)}</span>
<dl><dt>Selected by</dt><dd><strong>${escape(environment.sourceLabel)}</strong></dd>
<dt>Analysis target</dt><dd>${escape(environment.resource.fsPath)}</dd>
<dt>Python</dt><dd>${escape(report?.version ?? (error ? "Unavailable" : "Checking"))}</dd>
<dt>Executable</dt><dd><code>${escape(report?.executable || environment.pythonPath || "Not selected")}</code></dd>
${environment.pythonArgs.length ? `<dt>Launch command</dt><dd><code>${escape(command)}</code></dd>` : ""}
<dt>Q1Lens</dt><dd>${escape(report?.packageVersion ?? (report?.q1lensInstalled === false ? "Not installed" : report ? "Available (no version metadata)" : "Checking"))}</dd></dl>
<p class="note">${environment.source === "notebook"
    ? "Refresh follows this notebook's current kernel."
    : environment.source === "manual"
      ? "Your explicit Q1Lens Python settings are preserved. Refresh reads these settings for the same analysis target."
      : "Refresh checks the VS Code Python selection for this analysis target again. Switching to another editor does not change this target."}</p>
${error ? `<p role="alert">${escape(error)}</p>` : ""}
<p class="muted">This is the environment checked for the analysis target above.</p>
<button data-action="check">Check Again</button><button data-action="select">${environment.source === "manual" ? "Open Python Settings" : environment.source === "notebook" ? "Open Notebook" : "Select Interpreter"}</button>
${environment.source === "manual" ? '<button data-action="follow">Follow VS Code Selection</button><p class="muted">Keeps your saved Q1Lens paths and launcher arguments. You can restore them by setting Python Environment to Auto.</p>' : ""}
<script nonce="${nonce}">const vscode=acquireVsCodeApi();document.querySelectorAll('button').forEach(button=>button.addEventListener('click',()=>vscode.postMessage({type:button.dataset.action})));</script>
</main></body></html>`;
}

export function registerPythonEnvironmentStatus(context: vscodeTypes.ExtensionContext): void {
  const vscode = require("vscode") as typeof vscodeTypes;
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 99);
  status.name = "Q1Lens Python Environment";
  status.command = "q1lens.showPythonEnvironment";
  let panel: vscodeTypes.WebviewPanel | undefined;
  update = () => {
    if (!current) return;
    const source = current.source === "vscode" ? "VS Code" : current.source === "manual" ? "Manual" : "Notebook";
    const executable = current.report?.executable ?? current.pythonPath;
    const folder = dirname(executable);
    const name = !executable ? "Not selected" : /^(bin|scripts)$/i.test(basename(folder)) ? basename(dirname(folder)) : basename(executable);
    status.text = `$(python) Q1Lens: ${name} · ${source}`;
    status.tooltip = `${current.sourceLabel}\n${executable}\nAnalysis target: ${current.resource.fsPath}\nClick to inspect this environment.`;
    status.show();
    if (panel) panel.webview.html = pythonEnvironmentHtml(current);
  };
  context.subscriptions.push(status, { dispose: () => { update = undefined; current = undefined; panel?.dispose(); } },
    vscode.commands.registerCommand("q1lens.showPythonEnvironment", async () => {
      if (!current) { await vscode.commands.executeCommand("q1lens.checkEnvironment"); if (!current) return; }
      if (panel) { panel.reveal(); return; }
      panel = vscode.window.createWebviewPanel("q1lens.pythonEnvironment", "Q1Lens: Python Environment", vscode.ViewColumn.Active,
        { enableScripts: true, localResourceRoots: [] });
      panel.webview.html = pythonEnvironmentHtml(current);
      const messages = panel.webview.onDidReceiveMessage(async (message: unknown) => {
        if (!current || !message || typeof message !== "object") return;
        const type = (message as { type?: string }).type;
        if (type === "check") await vscode.commands.executeCommand("q1lens.checkEnvironment", undefined, current.resource);
        if (type === "follow" && current.source === "manual") {
          const resource = current.resource;
          const scope = vscode.workspace.getWorkspaceFolder(resource) ? vscode.ConfigurationTarget.WorkspaceFolder : vscode.ConfigurationTarget.Global;
          await vscode.workspace.getConfiguration("q1lens", resource).update("pythonEnvironment", "vscode", scope);
          await vscode.commands.executeCommand("q1lens.checkEnvironment", undefined, resource);
        }
        if (type === "select") {
          if (current.source === "manual") await vscode.commands.executeCommand("workbench.action.openSettings", "@ext:q1lens.q1lens python");
          else if (current.source === "notebook") await vscode.window.showNotebookDocument(await vscode.workspace.openNotebookDocument(current.resource));
          else await selectVSCodeInterpreter(vscode, current.resource);
        }
      });
      panel.onDidDispose(() => { messages.dispose(); panel = undefined; });
    }));
}
