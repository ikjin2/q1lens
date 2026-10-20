import { randomUUID } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { hostname, tmpdir } from "node:os";
import { basename, dirname, isAbsolute, join } from "node:path";
import type * as vscodeTypes from "vscode";
import { probePythonEnvironment, type PythonEnvironmentReport } from "./environment";
import { notebookInstallCommand, type NotebookInstaller } from "./notebookInstallCommand";
import { notebookSetupHtml } from "./notebookSetupHtml";
import { publishPythonEnvironment } from "./pythonEnvironmentStatus";

interface NotebookKernel {
  readonly language: string;
  executeCode(code: string, token: vscodeTypes.CancellationToken): AsyncIterable<{
    items: Array<{ mime: string; data: Uint8Array }>;
  }>;
}

interface JupyterApi {
  kernels?: { getKernel(uri: vscodeTypes.Uri): PromiseLike<NotebookKernel | undefined> };
}

export interface NotebookPythonEnvironment {
  pythonPath: string;
  pythonArgs: string[];
}

interface KernelEnvironment {
  executable: string;
  hostname: string;
  proof: string;
}

async function kernelEnvironment(
  vscode: typeof vscodeTypes,
  kernel: NotebookKernel,
): Promise<KernelEnvironment> {
  const directory = await mkdtemp(join(tmpdir(), "q1lens-kernel-"));
  const nonce = randomUUID();
  const proofFile = join(directory, "host-proof");
  const marker = `Q1LENS_ENV_${randomUUID()}:`;
  // Run in an isolated namespace, without leaving imports or variables in the notebook.
  const source = `import json, pathlib, socket, sys
try:
    proof = pathlib.Path(${JSON.stringify(proofFile)}).read_text()
except OSError:
    proof = ""
print(${JSON.stringify(marker)} + json.dumps({"executable": sys.executable, "hostname": socket.gethostname(), "proof": proof}))`;
  try {
    await writeFile(proofFile, nonce, { mode: 0o600 });
    return await vscode.window.withProgress({
      location: vscode.ProgressLocation.Notification,
      title: "Q1Lens: Checking notebook environment…", cancellable: true,
    }, async (_progress, token) => {
      const cancellation = new vscode.CancellationTokenSource();
      let timer: NodeJS.Timeout | undefined;
      let subscription: vscodeTypes.Disposable | undefined;
      try {
        const stopped = new Promise<never>((_resolve, reject) => {
          subscription = token.onCancellationRequested(() => {
            cancellation.cancel();
            reject(new vscode.CancellationError());
          });
          timer = setTimeout(() => {
            cancellation.cancel();
            reject(new Error("The notebook kernel did not respond within 60 seconds. Allow Jupyter kernel access for Q1Lens if prompted, or wait for the current cell to finish, then try again."));
          }, 60000);
          if (token.isCancellationRequested) {
            cancellation.cancel();
            reject(new vscode.CancellationError());
          }
        });
        const execution = (async () => {
          let stdout = "";
          for await (const output of kernel.executeCode(`exec(${JSON.stringify(source)}, {})`, cancellation.token)) {
            for (const item of output.items) {
              if (item.mime === "application/vnd.code.notebook.stdout" || item.mime === "text/plain") {
                stdout += Buffer.from(item.data).toString("utf8");
                if (stdout.length > 1024 * 1024) throw new Error("The notebook returned too much environment output.");
              }
            }
          }
          const line = stdout.split(/\r?\n/).find((value) => value.startsWith(marker));
          if (!line) throw new Error("The Python kernel did not return its environment. Check Jupyter's kernel access permission for Q1Lens and try again.");
          const report = JSON.parse(line.slice(marker.length)) as KernelEnvironment;
          if (report.proof !== nonce || report.hostname?.toLowerCase() !== hostname().toLowerCase()) {
            throw new Error("This notebook kernel runs on a different host. Open the project in VS Code on that host (for example with Remote SSH), then select its Python kernel.");
          }
          if (typeof report.executable !== "string" || !isAbsolute(report.executable)) {
            throw new Error("The notebook kernel did not provide a usable Python executable.");
          }
          return report;
        })();
        return await Promise.race([execution, stopped]);
      } finally {
        if (timer) clearTimeout(timer);
        subscription?.dispose();
        cancellation.cancel();
        cancellation.dispose();
      }
    });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

export interface NotebookEnvironmentState {
  environment: { executable: string };
  report: PythonEnvironmentReport;
  sourceLabel?: string;
}

function environmentIssue(report: PythonEnvironmentReport): string {
  return report.q1lensInstalled === false
    ? "Q1Lens is not installed in this notebook's Python environment."
    : "Q1Lens could not be imported in this notebook's Python environment.";
}

export async function showNotebookSetup(
  vscode: typeof vscodeTypes,
  notebookUri: vscodeTypes.Uri,
  initial: NotebookEnvironmentState,
  version: string,
  check: () => Promise<NotebookEnvironmentState>,
  openNotebook: () => PromiseLike<unknown>,
  fileEnvironment = false,
): Promise<NotebookPythonEnvironment | undefined> {
  const panel = vscode.window.createWebviewPanel(
    "q1lens.notebookSetup", fileEnvironment ? "Q1Lens: Python Setup" : "Q1Lens: Notebook Setup", vscode.ViewColumn.Active,
    { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [] },
  );
  let current = initial;
  let installer: NotebookInstaller = initial.report.pipAvailable === false ? "uv" : "pip";
  const render = (notice = "") => {
    panel.webview.html = notebookSetupHtml({
      notebook: basename(notebookUri.fsPath), pythonPath: current.environment.executable,
      report: current.report, issue: fileEnvironment
        ? environmentIssue(current.report).replace("this notebook's", "this file's") : environmentIssue(current.report),
      version, installer, notice, fileEnvironment, sourceLabel: current.sourceLabel,
    });
  };
  try {
    render();
  } catch (error) {
    panel.dispose();
    throw error;
  }
  return new Promise((resolve) => {
    let closed = false;
    let busy = false;
    const disposal = panel.onDidDispose(() => {
      closed = true;
      messages.dispose();
      disposal.dispose();
      resolve(undefined);
    });
    const messages = panel.webview.onDidReceiveMessage(async (message: unknown) => {
      if (closed || busy || !message || typeof message !== "object") return;
      const { type, installer: choice } = message as { type?: string; installer?: unknown };
      if (!["copyCommand", "insertCommand", "checkAgain", "openNotebook"].includes(type ?? "")) return;
      if ((type === "copyCommand" || type === "insertCommand") && choice !== "pip" && choice !== "uv") return;
      if (choice === "pip" || choice === "uv") installer = choice;
      busy = true;
      try {
        if (type === "openNotebook") {
          await openNotebook();
          return;
        }
        const updated = await check();
        if (closed) return;
        const changed = updated.environment.executable !== current.environment.executable;
        current = updated;
        if (!current.report.qbsError && !current.report.q1asmError) {
          resolve({ pythonPath: current.environment.executable, pythonArgs: [] });
          panel.dispose();
          return;
        }
        if (changed) {
          render(`${fileEnvironment ? "The selected Python environment" : "The notebook kernel"} changed. Review the new environment and command before continuing.`);
          return;
        }
        if (type === "checkAgain") {
          render("Checked again: this environment still needs attention. See the diagnosis below.");
          return;
        }
        if (installer === "pip" && current.report.pipAvailable === false) {
          render("pip is unavailable in this environment. Choose uv pip or prepare pip using your own workflow.");
          return;
        }
        const command = notebookInstallCommand(current.environment.executable, version, installer);
        if (type === "copyCommand") {
          await vscode.env.clipboard.writeText(command);
          render("Command copied. Run it in the indicated shell, then choose Check Again.");
        } else {
          // A fresh, explicitly chosen shell avoids pasting into another command or shell dialect.
          const terminal = vscode.window.createTerminal({
            name: fileEnvironment ? "Q1Lens Python Setup" : "Q1Lens Notebook Setup", cwd: dirname(notebookUri.fsPath),
            shellPath: process.platform === "win32"
              ? join(process.env.SystemRoot || "C:\\Windows", "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
              : "/bin/sh",
            shellArgs: process.platform === "win32" ? ["-NoLogo", "-NoProfile"] : [],
          });
          terminal.sendText(command, false);
          terminal.show();
          render("Command entered in a new terminal. Review it and press Enter yourself, then choose Check Again.");
        }
      } catch (error) {
        if (!closed) {
          const message = error instanceof vscode.CancellationError ? "Environment check cancelled."
            : error instanceof Error ? error.message : String(error);
          await panel.webview.postMessage({ type: "checkError", message });
        }
      } finally {
        busy = false;
        if (!closed) void panel.webview.postMessage({ type: "idle" });
      }
    });
  });
}

/** Resolve the original notebook again on every analysis/refresh; never change Python settings. */
export async function ensureNotebookEnvironment(
  vscode: typeof vscodeTypes,
  notebookUri: vscodeTypes.Uri,
  output: vscodeTypes.OutputChannel,
): Promise<NotebookPythonEnvironment | undefined> {
  const openNotebook = async () => vscode.window.showNotebookDocument(await vscode.workspace.openNotebookDocument(notebookUri));
  try {
    if (vscode.workspace.isTrusted === false) throw new Error("Trust this workspace before Q1Lens uses the notebook kernel.");
    const extension = vscode.extensions.getExtension<JupyterApi>("ms-toolsai.jupyter");
    if (!extension) {
      const action = await vscode.window.showErrorMessage("Q1Lens needs the Microsoft Jupyter extension to use this notebook's Python environment.", "Open Jupyter Extension");
      if (action) await vscode.commands.executeCommand("extension.open", "ms-toolsai.jupyter");
      return undefined;
    }
    const api = await extension.activate();
    if (!api.kernels?.getKernel) throw new Error("Update the Microsoft Jupyter extension to use the notebook's Python environment.");
    const check = async (): Promise<NotebookEnvironmentState> => {
      const kernel = await api.kernels!.getKernel(notebookUri);
      if (!kernel) throw new Error("Select a Python kernel for this notebook and run a cell once, then open Q1Lens again.");
      if (kernel.language.toLowerCase() !== "python") throw new Error("Select a Python kernel for this notebook, run a cell, then open Q1Lens again.");
      const environment = await kernelEnvironment(vscode, kernel);
      output.appendLine(`Notebook ${notebookUri.toString()}: Python ${environment.executable}`);
      const report = await probePythonEnvironment(environment.executable, [], { cwd: tmpdir(), env: { PYTHONPATH: "" } });
      publishPythonEnvironment({ pythonPath: environment.executable, pythonArgs: [], source: "notebook",
        sourceLabel: "Notebook kernel", resource: notebookUri, report });
      if (!report.supported) throw new Error(`The notebook uses Python ${report.version} (${environment.executable}). Q1Lens requires Python 3.10 or newer; select a compatible notebook kernel.`);
      if (report.qbsError || report.q1asmError) output.appendLine(report.qbsError || report.q1asmError || "");
      return { environment, report };
    };
    let current = await check();
    while (current.report.qbsError || current.report.q1asmError) {
      const action = await vscode.window.showInformationMessage(
        `${environmentIssue(current.report)} Notebook: ${basename(notebookUri.fsPath)}. Python: ${current.environment.executable}`,
        "Show Setup", "Check Again", "Open Notebook",
      );
      if (action === "Open Notebook") await openNotebook();
      if (action === "Check Again") {
        current = await check();
        continue;
      }
      if (action !== "Show Setup") return undefined;
      const version = vscode.extensions.getExtension("q1lens.q1lens")?.packageJSON.version;
      if (typeof version !== "string" || !/^\d+\.\d+\.\d+(?:[-+][\w.-]+)?$/.test(version)) {
        throw new Error("Cannot determine the Q1Lens extension version for the suggested install command.");
      }
      return await showNotebookSetup(vscode, notebookUri, current, version, check, openNotebook);
    }
    return { pythonPath: current.environment.executable, pythonArgs: [] };
  } catch (error) {
    if (error instanceof vscode.CancellationError) return undefined;
    const message = error instanceof Error ? error.message : String(error);
    output.appendLine(message);
    const action = await vscode.window.showErrorMessage(`Q1Lens: ${message}`, "Open Notebook", "Show Output");
    if (action === "Open Notebook") await openNotebook();
    if (action === "Show Output") output.show(true);
    return undefined;
  }
}
