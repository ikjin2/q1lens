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
  const checkTitle = `After installing, check ${input.fileEnvironment ? "the original file's Python environment" : "the original notebook's current kernel"}. Q1Lens continues your analysis when it is ready.`;
  return `<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
<title>Q1Lens Python Setup</title>
<style nonce="${nonce}">
body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); padding: 18px 24px; line-height: 1.5; }
main { max-width: 760px; margin: auto; display: flex; flex-direction: column; gap: 10px; }
h1 { font-size: 16px; font-weight: 600; margin: 0; display: flex; gap: 8px; align-items: baseline; overflow-wrap: anywhere; }
h1 .mark { color: var(--vscode-editorWarning-foreground); }
p { margin: 0; }
.muted { color: var(--vscode-descriptionForeground); }
.environment { overflow-wrap: anywhere; }
dl { display: grid; grid-template-columns: auto 1fr; gap: 6px 16px; margin: 8px 0; }
dt { color: var(--vscode-descriptionForeground); } dd { margin: 0; overflow-wrap: anywhere; }
pre, code { font-family: var(--vscode-editor-font-family); }
pre { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; }
.command-row { display: flex; align-items: center; gap: 8px; padding: 6px 8px; background: var(--vscode-textCodeBlock-background); border: 1px solid var(--vscode-panel-border); border-radius: 4px; }
.command-row pre { flex: 1; min-width: 0; }
details pre { background: var(--vscode-textCodeBlock-background); margin: 8px 0; padding: 12px; border: 1px solid var(--vscode-panel-border); border-radius: 4px; }
details p { margin: 8px 0; }
summary { cursor: pointer; color: var(--vscode-descriptionForeground); }
select { background: var(--vscode-dropdown-background); color: var(--vscode-dropdown-foreground); border: 1px solid var(--vscode-dropdown-border); padding: 4px 6px; font: inherit; }
button { font: inherit; padding: 4px 11px; border: 1px solid transparent; border-radius: 2px; cursor: pointer; white-space: nowrap; background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
button:hover { background: var(--vscode-button-hoverBackground); }
button.secondary { background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); }
button.secondary:hover { background: var(--vscode-button-secondaryHoverBackground); }
button:focus-visible, select:focus-visible, summary:focus-visible { outline: 1px solid var(--vscode-focusBorder); outline-offset: 2px; }
button:disabled { opacity: .5; cursor: default; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; }
#status { min-height: 20px; color: var(--vscode-descriptionForeground); }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
[hidden] { display: none !important; }
@media (max-width: 520px) { .command-row { flex-wrap: wrap; } .command-row pre { flex-basis: 100%; order: -1; } }
</style></head><body><main>
<h1><span class="mark" aria-hidden="true">⚠</span><span>${escape(input.issue)}</span></h1>
<p class="muted environment">Python ${escape(input.report.version)} · <code>${escape(input.pythonPath)}</code> · ${escape(input.sourceLabel ?? "Notebook kernel")}</p>
<div class="command-row">
<label class="sr-only" for="installer">Package manager</label>
<select id="installer"><option value="pip"${input.installer === "pip" ? " selected" : ""}>pip</option><option value="uv"${input.installer === "uv" ? " selected" : ""}>uv pip</option></select>
<pre id="pipCommand"${input.installer !== "pip" ? " hidden" : ""}>${escape(notebookInstallCommand(input.pythonPath, input.version, "pip"))}</pre>
<pre id="uvCommand"${input.installer !== "uv" ? " hidden" : ""}>${escape(notebookInstallCommand(input.pythonPath, input.version, "uv"))}</pre>
<button class="secondary command" data-action="copyCommand">Copy</button>
<button class="secondary command" data-action="insertCommand" title="Fills in the terminal only. Review the command and press Enter to run it yourself.">Insert in terminal</button>
</div>
<p id="pipMissing" hidden>pip is not available in this environment. Choose uv pip, or prepare pip using your own workflow.</p>
<p id="uvHint" class="muted" hidden>Requires uv on your PATH. This command targets the selected environment directly.</p>
<div class="actions"><button data-action="checkAgain" title="${checkTitle}">Check again</button><button class="secondary" data-action="openNotebook">${input.fileEnvironment ? "Open file" : "Open notebook"}</button></div>
<p id="status" role="status" aria-live="polite">${escape(input.notice)}</p>
<details${error && input.report.q1lensInstalled !== false ? " open" : ""}><summary>Details</summary>
<dl><dt>${input.fileEnvironment ? "File" : "Notebook"}</dt><dd>${escape(input.notebook)}</dd>
<dt>Package</dt><dd><code>q1lens==${escape(input.version)}</code>, matching this extension. You can edit the command before running it.</dd>
<dt>Shell</dt><dd>${shell}. The Python path selects the environment shown above.</dd></dl>
${error ? `<pre>${escape(error)}</pre>` : ""}
<p>${checkTitle}</p>
<p>Managing dependencies yourself? Add Q1Lens through your existing requirements or project dependency workflow, then check again here. The commands above do not update your project's dependency or lock files.</p>
</details>
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
