import { randomUUID } from "node:crypto";
import type { PythonEnvironmentReport } from "./environment";
import { notebookInstallCommand, type NotebookInstaller } from "./notebookInstallCommand";

export function notebookSetupHtml(input: {
  notebook: string; pythonPath: string; report: PythonEnvironmentReport;
  issue: string; version: string; installer: NotebookInstaller; notice: string;
  fileEnvironment?: boolean; sourceLabel?: string;
}): string {
  const escape = (value: string) => value.replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]!);
  const nonce = randomUUID();
  const shell = process.platform === "win32" ? "PowerShell" : "POSIX shell (sh / bash / zsh)";
  const error = input.report.qbsError || input.report.q1asmError || "";
  return `<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
<title>Q1Lens Python Setup</title>
<style nonce="${nonce}">
body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); padding: 18px 24px; line-height: 1.5; }
main { max-width: 760px; margin: auto; }
h1 { font-size: 23px; font-weight: 600; margin: 8px 0; }
h2 { font-size: 16px; margin: 20px 0 8px; }
p { margin: 8px 0; }
.eyebrow, .muted { color: var(--vscode-descriptionForeground); }
.eyebrow { font-size: 12px; letter-spacing: 1px; }
.diagnosis { border-left: 3px solid var(--vscode-editorWarning-foreground); padding: 8px 14px; background: var(--vscode-textBlockQuote-background); }
dl { display: grid; grid-template-columns: auto 1fr; gap: 6px 16px; }
dt { color: var(--vscode-descriptionForeground); } dd { margin: 0; overflow-wrap: anywhere; }
pre, code { font-family: var(--vscode-editor-font-family); }
pre { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--vscode-textCodeBlock-background); margin: 8px 0; padding: 12px; border: 1px solid var(--vscode-panel-border); border-radius: 4px; }
select { background: var(--vscode-dropdown-background); color: var(--vscode-dropdown-foreground); border: 1px solid var(--vscode-dropdown-border); padding: 7px; margin-left: 8px; }
button { font: inherit; padding: 7px 12px; border: 1px solid transparent; cursor: pointer; background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
button:hover { background: var(--vscode-button-hoverBackground); }
button.secondary { background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); }
button:focus-visible, select:focus-visible { outline: 1px solid var(--vscode-focusBorder); outline-offset: 2px; }
button:disabled { opacity: .5; cursor: default; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
#status { min-height: 24px; color: var(--vscode-descriptionForeground); }
[hidden] { display: none !important; }
</style></head><body><main>
<div class="eyebrow">Q1LENS · ${input.fileEnvironment ? "PYTHON" : "NOTEBOOK"} SETUP</div>
<h1>${input.fileEnvironment ? "Use your project's Python environment" : "Use your notebook's Python environment"}</h1>
<p class="diagnosis">${escape(input.issue)}</p>
<dl><dt>${input.fileEnvironment ? "File" : "Notebook"}</dt><dd>${escape(input.notebook)}</dd>
<dt>Selected by</dt><dd>${escape(input.sourceLabel ?? "Notebook kernel")}</dd>
<dt>Python</dt><dd>${escape(input.report.version)}</dd>
<dt>Executable</dt><dd><code>${escape(input.pythonPath)}</code></dd></dl>
<details${input.report.q1lensInstalled === false ? "" : " open"}><summary>Diagnostic details</summary><pre>${escape(error)}</pre></details>
<h2>1. Choose how to install</h2>
<label for="installer">Package manager</label>
<select id="installer"><option value="pip"${input.installer === "pip" ? " selected" : ""}>pip</option><option value="uv"${input.installer === "uv" ? " selected" : ""}>uv pip</option></select>
<p class="muted">Suggested package: <code>q1lens==${escape(input.version)}</code>, matching this extension. You can edit the command before running it.</p>
<p id="pipMissing" hidden>pip is not available in this environment. Choose uv pip, or prepare pip using your own workflow.</p>
<p id="uvHint" hidden>Requires uv on your PATH. This command targets the selected environment directly.</p>
<h2>2. Review and run the command</h2>
<p class="muted">Shell: ${shell}. The Python path explicitly selects the environment shown above.</p>
<pre id="pipCommand"${input.installer !== "pip" ? " hidden" : ""}>${escape(notebookInstallCommand(input.pythonPath, input.version, "pip"))}</pre>
<pre id="uvCommand"${input.installer !== "uv" ? " hidden" : ""}>${escape(notebookInstallCommand(input.pythonPath, input.version, "uv"))}</pre>
<div class="actions"><button class="secondary command" data-action="copyCommand">Copy Command</button><button class="secondary command" data-action="insertCommand">Insert into Terminal</button></div>
<p class="muted">Terminal insertion only fills in the command. Review it and press Enter to run it yourself.</p>
<details><summary>I manage project dependencies myself</summary><p>Add Q1Lens through your existing requirements or project dependency workflow, then check again here. The commands above do not update your project's dependency or lock files.</p></details>
<h2>3. Check and continue</h2>
<p>After installing, check ${input.fileEnvironment ? "the original file's Python environment" : "the original notebook's current kernel"}. Q1Lens will continue your analysis when it is ready.</p>
<div class="actions"><button data-action="checkAgain">Check Again</button><button class="secondary" data-action="openNotebook">${input.fileEnvironment ? "Open File" : "Open Notebook"}</button></div>
<p id="status" role="status" aria-live="polite">${escape(input.notice)}</p>
<script nonce="${nonce}">
const vscode = acquireVsCodeApi();
const installer = document.getElementById("installer");
const status = document.getElementById("status");
let busy = false;
let checkFailed = false;
const pipAvailable = ${input.report.pipAvailable !== false};
function update() {
  const pip = installer.value === "pip";
  document.getElementById("pipCommand").hidden = !pip;
  document.getElementById("uvCommand").hidden = pip;
  document.getElementById("pipMissing").hidden = !pip || pipAvailable;
  document.getElementById("uvHint").hidden = pip;
  installer.disabled = busy;
  document.querySelectorAll("button").forEach(button => {
    button.disabled = busy || (button.classList.contains("command") && (checkFailed || (pip && !pipAvailable)));
  });
}
installer.addEventListener("change", update);
document.querySelectorAll("button").forEach(button => button.addEventListener("click", () => {
  busy = true;
  status.textContent = button.dataset.action === "openNotebook"
    ? "Return to this setup tab after managing your Python environment."
    : "Checking the Python environment…";
  update();
  vscode.postMessage({ type: button.dataset.action, installer: installer.value });
}));
window.addEventListener("message", ({ data }) => {
  if (data.type === "checkError") { checkFailed = true; status.textContent = data.message; }
  if (data.type === "idle") busy = false;
  update();
});
update();
</script></main></body></html>`;
}
