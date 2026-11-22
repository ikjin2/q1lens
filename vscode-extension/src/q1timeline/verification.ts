import * as vscode from "vscode";
import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { unwatchFile, watchFile } from "node:fs";
import * as path from "node:path";
import { resolveFilePythonEnvironment } from "../pythonEnvironment";
import { collectProjectRelatedPaths } from "./projectRelatedFiles";
import { findProjectFileCandidatesUpward } from "./projectDiscovery";
import { q1timelineConfigValue } from "./settings";

type Status = "proved" | "counterexample" | "unknown";
type Source = { file: string; line: number };
interface Witness extends Source {
  inputs: Record<string, number>;
  event_id: string;
  time_ns?: number;
  register?: string;
  timeline?: { events: { id: string; source?: Source }[] };
}
interface Check extends Source {
  kind: string;
  status: Status;
  message: string;
  counterexample?: Witness;
}
interface Report { status: Status; checks: Check[] }
interface Job {
  id: string;
  manual: boolean;
  uri: vscode.Uri;
  abort: AbortController;
  dependencies: Set<string>;
  result: Promise<Report>;
}

const command = "q1timeline.verify";
const replayCommand = "q1timeline.openVerificationWitness";
const toggleCommand = "q1timeline.toggleAutoVerification";
const autoRunSetting = "q1lens.q1timeline.verification.autoRun";
const autoRunEnabled = () => vscode.workspace.getConfiguration().get(autoRunSetting, false);
const verdicts = { proved: "✓ Proved", counterexample: "✗ Counterexample", unknown: "? Unknown" };
const fileKey = (file: string) => process.platform === "win32" ? path.resolve(file).toLowerCase() : path.resolve(file);
const sameFile = (a: string, b: string) => fileKey(a) === fileKey(b);
const isQ1asm = (uri?: vscode.Uri) => uri?.scheme === "file" && path.extname(uri.fsPath).toLowerCase() === ".q1asm";
const unknown = (file: string, message: string): Report => ({
  status: "unknown", checks: [{ file, line: 1, kind: "verification", status: "unknown", message }],
});

function config<T>(resource: vscode.Uri, key: string, fallback: T): T {
  return q1timelineConfigValue(vscode.workspace.getConfiguration("qbloxTimeline", resource),
    vscode.workspace.getConfiguration("q1timeline", resource), key, fallback, vscode.workspace.getConfiguration("q1lens", resource));
}

async function nearbyProject(uri: vscode.Uri): Promise<vscode.Uri | undefined> {
  const root = vscode.workspace.getWorkspaceFolder(uri)?.uri.fsPath || path.parse(uri.fsPath).root;
  const candidates = findProjectFileCandidatesUpward(uri.fsPath, [config(uri, "projectFile", "q1timeline.yml"), "q1timeline.yaml"], root);
  for (const file of candidates) {
    const related = collectProjectRelatedPaths(file, await readFile(file, "utf8"));
    if (related.some((item: string) => sameFile(item, uri.fsPath))) return vscode.Uri.file(file);
  }
  return undefined;
}

async function calculate(uri: vscode.Uri, args: string[], signal: AbortSignal): Promise<Report> {
  if (!vscode.workspace.isTrusted) return unknown(uri.fsPath, "Trust this workspace to verify saved programs.");
  const python = await resolveFilePythonEnvironment(vscode, uri, "q1asm");
  signal.throwIfAborted();
  const timeout = config(uri, "analyzer.timeoutMs", 30000);
  const cli = [...python.pythonArgs, "-m", "q1lens", ...args, "--json", "--include-replay", "--timeout-ms", String(timeout)];
  return new Promise((resolve) => {
    execFile(python.pythonPath, cli, { signal, cwd: path.dirname(uri.fsPath), windowsHide: true,
      timeout: timeout + 5000, maxBuffer: 64 * 1024 * 1024, env: { ...process.env, ...config(uri, "analyzer.env", {}) } },
    (error, stdout, stderr) => {
      try {
        const report: Report = JSON.parse(stdout);
        if (!Object.hasOwn(verdicts, report.status) || !Array.isArray(report.checks) ||
            !report.checks.every((c) => Object.hasOwn(verdicts, c.status) && typeof c.file === "string" &&
              Number.isSafeInteger(c.line) && c.line > 0 && typeof c.message === "string")) throw new Error("Invalid verification report.");
        const expectedExit = { proved: 0, counterexample: 1, unknown: 2 }[report.status];
        if ((error?.code ?? 0) !== expectedExit) throw new Error("Unexpected verifier exit status.");
        resolve(report.checks.length ? report : unknown(uri.fsPath, "No checks were reported."));
      } catch {
        const message = error?.code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER" ? "The verified timeline exceeds the editor's 64 MB replay limit." :
          error?.killed ? "Verification timed out." :
          stderr.trim().split(/\r?\n/).pop() || "Could not read the verification report. Check the selected Q1Lens Python installation.";
        resolve(unknown(uri.fsPath, message));
      }
    });
  });
}

function openReplay(context: vscode.ExtensionContext, check: Check): void {
  const witness = check.counterexample;
  if (!witness?.timeline?.events.some((e) => String(e.id) === String(witness.event_id))) {
    void vscode.window.showWarningMessage("The verified timeline has no matching event for this counterexample.");
    return;
  }
  const root = vscode.Uri.joinPath(context.extensionUri, "out", "src");
  const panel = vscode.window.createWebviewPanel("q1timeline.witness", "Q1Lens · Counterexample", vscode.ViewColumn.Beside,
    { enableScripts: true, localResourceRoots: [root], retainContextWhenHidden: true });
  const asset = (name: string) => panel.webview.asWebviewUri(vscode.Uri.joinPath(root, ...name.split("/")));
  const nonce = randomUUID();
  const scripts = ["shared/timeline/instructionColors.js", "shared/timeline/renderer.js", "q1timeline/media/timelineAdapter.js",
    "q1timeline/media/hardwareState.js", "q1timeline/media/hardwareView.js", "q1timeline/media/verificationReplay.js"];
  const styles = ["shared/timeline/renderer.css", "q1timeline/media/timeline.css", "q1timeline/media/hardwareView.css"];
  // Opening source is scoped to an event from the verified snapshot.
  panel.webview.onDidReceiveMessage(async (message) => {
    if (message?.type !== "eventSource" || typeof message.id !== "string") return;
    const source = witness.timeline!.events.find((event) => String(event.id) === message.id)?.source;
    if (!source?.file || !path.isAbsolute(source.file)) return;
    const position = new vscode.Position(Math.max(0, source.line - 1), 0);
    try {
      await vscode.window.showTextDocument(vscode.Uri.file(source.file),
        { preview: false, viewColumn: vscode.ViewColumn.One, selection: new vscode.Range(position, position) });
    } catch (error) { void vscode.window.showWarningMessage(String(error)); }
  });
  const payload = JSON.stringify(check).replace(/</g, "\\u003c");
  panel.webview.html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${panel.webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}'">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    ${styles.map((s) => `<link rel="stylesheet" href="${asset(s)}">`).join("")}
    <style>
      .cx-head { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; margin: 0 0 8px; min-height: 26px;
        color: var(--vscode-foreground); font-family: var(--vscode-font-family); font-size: var(--vscode-font-size, 13px); }
      .cx-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
      .cx-kind { display: inline-flex; align-items: center; gap: 4px; font-weight: 600; color: var(--vscode-errorForeground); }
      .cx-head svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.3; stroke-linecap: round; stroke-linejoin: round; }
      #inputs, #occurrence { font-family: var(--vscode-editor-font-family, monospace); font-variant-numeric: tabular-nums; }
      #finding { color: var(--vscode-descriptionForeground); }
      .cx-grow { flex: 1; }
      .cx-head button { font: inherit; color: inherit; background: none; border: 0; border-radius: 4px; cursor: pointer; }
      .cx-head button:focus-visible { outline: 1px solid var(--vscode-focusBorder); outline-offset: 1px; }
      .cx-head button:disabled { opacity: .5; cursor: default; }
      #source { padding: 0 2px; color: var(--vscode-textLink-foreground); font-family: var(--vscode-editor-font-family, monospace); }
      #source:hover:not(:disabled) { text-decoration: underline; }
      .cx-icon-button { display: inline-grid; place-items: center; width: 24px; height: 24px; padding: 0; }
      .cx-icon-button:hover { background: var(--vscode-toolbar-hoverBackground, var(--vscode-button-secondaryHoverBackground)); }
      [hidden] { display: none !important; }
    </style></head>
    <body><main><header class="cx-head">
      <h1 id="heading" class="cx-sr">Counterexample</h1>
      <span class="cx-kind"><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.5"></circle><path d="M6 6l4 4M10 6l-4 4"></path></svg><span id="kind"></span></span>
      <strong id="inputs"></strong>
      <span id="occurrence"></span>
      <button id="source" type="button" title="Open source"></button>
      <span id="finding"></span>
      <span class="cx-grow"></span>
      <button id="focus" class="cx-icon-button" type="button" title="Focus counterexample" aria-label="Focus counterexample"><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="3.5"></circle><path d="M8 1.5v3M8 11.5v3M1.5 8h3M11.5 8h3"></path></svg></button>
      <button id="full" class="cx-icon-button" type="button" title="Fit full timeline" aria-label="Fit full timeline"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 6V3h3M10 3h3v3M13 10v3h-3M6 13H3v-3"></path></svg></button>
    </header>
    <section id="timeline-root" aria-label="Counterexample timeline"></section>
    <section id="hardware" aria-label="Sequencer hardware state"></section></main>
    <script id="witness" type="application/json" nonce="${nonce}">${payload}</script>
    ${scripts.map((s) => `<script nonce="${nonce}" src="${asset(s)}"></script>`).join("")}</body></html>`;
}

export function registerVerification(context: vscode.ExtensionContext): void {
  const status = vscode.window.createStatusBarItem(toggleCommand, vscode.StatusBarAlignment.Right, 98);
  status.name = "Q1Lens Automatic Z3 Verification";
  status.command = toggleCommand;
  let toggleWrite = Promise.resolve();
  const toggle = () => {
    const target = vscode.workspace.workspaceFolders?.length || vscode.workspace.workspaceFile
      ? vscode.ConfigurationTarget.Workspace : vscode.ConfigurationTarget.Global;
    return vscode.workspace.getConfiguration().update(autoRunSetting, !autoRunEnabled(), target);
  };
  const updateStatus = () => {
    const document = vscode.window.activeTextEditor?.document;
    if (document?.languageId !== "q1asm" || document.uri.scheme !== "file") {
      status.hide();
      return;
    }
    const enabled = autoRunEnabled();
    status.text = `$(shield) Z3 Auto: ${enabled ? "On" : "Off"}`;
    status.tooltip = `Turn ${enabled ? "off" : "on"} automatic Z3 verification for saved Q1ASM files`;
    if (!vscode.workspace.getConfiguration("editor", document).get("codeLens", true)) {
      status.tooltip += "\nEnable Editor: Code Lens for Q1ASM to see automatic results.";
    }
    status.accessibilityInformation = { label: `Automatic Z3 verification ${enabled ? "on" : "off"}`, role: "button" };
    status.show();
  };
  updateStatus();
  context.subscriptions.push(status, vscode.window.onDidChangeActiveTextEditor(updateStatus),
    vscode.commands.registerCommand(toggleCommand, () => {
      const write = toggleWrite.then(toggle);
      toggleWrite = write.catch(() => {});
      return write;
    }));

  const changed = new vscode.EventEmitter<void>();
  const diagnostics = vscode.languages.createDiagnosticCollection("q1lens-verification");
  const diagnosticOwners = new Map<string, string>();
  const jobs = new Map<string, Job>();
  const discard = (key: string, job: Job) => {
    job.abort.abort();
    jobs.delete(key);
    const source = fileKey(job.uri.fsPath);
    if (diagnosticOwners.get(source) === job.id) {
      diagnostics.delete(job.uri);
      diagnosticOwners.delete(source);
    }
  };
  const invalidate = (uri?: vscode.Uri, automaticOnly = false) => {
    let invalidated = false;
    for (const [key, job] of jobs) {
      if ((automaticOnly && job.manual) || (uri && !job.dependencies.has(fileKey(uri.fsPath)))) continue;
      discard(key, job);
      invalidated = true;
    }
    if (invalidated || !uri) changed.fire();
  };
  const watcher = vscode.workspace.createFileSystemWatcher("**/*.{q1asm,yml,yaml,json}");
  context.subscriptions.push(changed, diagnostics, watcher, watcher.onDidChange(invalidate), watcher.onDidDelete(invalidate),
    watcher.onDidCreate((uri) => { if (/^q1timeline\.ya?ml$/i.test(path.basename(uri.fsPath))) invalidate(); else invalidate(uri); }),
    vscode.workspace.onDidChangeTextDocument((e) => { if (e.contentChanges.length) invalidate(e.document.uri); }),
    vscode.workspace.onDidSaveTextDocument((doc) => invalidate(doc.uri)),
    vscode.workspace.onDidCloseTextDocument((doc) => invalidate(doc.uri)),
    vscode.workspace.onDidGrantWorkspaceTrust(() => invalidate()),
    vscode.workspace.onDidChangeConfiguration((event) => {
      const autoRunChanged = event.affectsConfiguration(autoRunSetting);
      if (autoRunChanged || event.affectsConfiguration("editor.codeLens")) updateStatus();
      if (["q1lens.pythonEnvironment", "q1lens.pythonPath", "q1lens.pythonArgs", "q1lens.q1timeline.pythonPath",
        "q1lens.q1timeline.pythonArgs", "q1lens.q1timeline.projectFile", "q1lens.q1timeline.analyzer",
        "qbloxTimeline", "q1timeline", "python"].some((key) => event.affectsConfiguration(key))) invalidate();
      else if (autoRunChanged) {
        if (autoRunEnabled()) changed.fire();
        else invalidate(undefined, true);
      }
    }),
    { dispose() { for (const job of jobs.values()) job.abort.abort(); jobs.clear(); } });

  async function run(uri: vscode.Uri, args: string[], files: string[], project?: vscode.Uri, manual = false) {
    const key = JSON.stringify(args);
    let job = jobs.get(key);
    if (manual && job) job.manual = true;
    if (manual && job && (await job.result).checks.some((check) => check.status === "unknown") && jobs.get(key) === job) {
      discard(key, job);
      job = undefined;
      changed.fire();
    }
    if (!job) {
      const dependencies = new Set(files.map(fileKey));
      const abort = new AbortController();
      job = { id: randomUUID(), uri, manual, abort, dependencies, result: (async () => {
        if (project) {
          dependencies.add(fileKey(project.fsPath));
          for (const file of collectProjectRelatedPaths(project.fsPath, await readFile(project.fsPath, "utf8"))) dependencies.add(fileKey(file));
        }
        abort.signal.throwIfAborted();
        for (const file of dependencies) {
          const target = vscode.Uri.file(file);
          if (/\.(?:q1asm|ya?ml|json)$/.test(file) && vscode.workspace.getWorkspaceFolder(target)) continue;
          const onChange = () => invalidate(target);
          watchFile(file, { persistent: false, interval: 500 }, onChange);
          abort.signal.addEventListener("abort", () => unwatchFile(file, onChange), { once: true });
        }
        if (vscode.workspace.textDocuments.some((d) => d.isDirty && dependencies.has(fileKey(d.uri.fsPath)))) {
          return unknown(uri.fsPath, "Save the edited source and project files to verify these inputs.");
        }
        abort.signal.throwIfAborted();
        return calculate(uri, args, abort.signal);
      })().catch((error) => unknown(uri.fsPath, error instanceof Error ? error.message : String(error))) };
      jobs.set(key, job);
    }
    const report = await job.result;
    if (job.abort.signal.aborted || jobs.get(key) !== job) return undefined;
    return { report, key: job.id };
  }

  async function program(uri: vscode.Uri, project?: vscode.Uri, sequencer?: string, manual = false) {
    project ??= await nearbyProject(uri);
    if (!manual && !autoRunEnabled()) return undefined;
    const args = ["verify", uri.fsPath];
    if (project) args.push("--project", project.fsPath);
    if (sequencer) args.push("--sequencer", sequencer);
    const result = await run(uri, args, [uri.fsPath], project, manual);
    if (result) {
      diagnosticOwners.set(fileKey(uri.fsPath), result.key);
      diagnostics.set(uri, result.report.checks.filter((c) => c.status !== "proved" && sameFile(c.file, uri.fsPath)).map((c) => {
        const line = c.line - 1;
        const d = new vscode.Diagnostic(new vscode.Range(line, 0, line, Number.MAX_SAFE_INTEGER),
          `${verdicts[c.status]} · ${c.message}`, c.status === "unknown" ? vscode.DiagnosticSeverity.Information : vscode.DiagnosticSeverity.Warning);
        d.source = "Q1Lens verification";
        return d;
      }));
    }
    return result;
  }

  async function present(result?: { report: Report; key: string }, inline = false) {
    if (!result) return;
    const { report } = result;
    if (inline) return { key: result.key, checks: report.checks.map((check) => ({
      text: `${verdicts[check.status]} · ${check.message}`, hasReplay: Boolean(check.counterexample),
    })) };
    const check = report.checks.find((item) => item.status === report.status) || report.checks[0];
    const message = `${verdicts[report.status]} · ${check.message}`;
    if (check.counterexample) {
      if (await vscode.window.showWarningMessage(message, "Open counterexample timeline"))
        await vscode.commands.executeCommand(replayCommand, result.key, report.checks.indexOf(check));
    } else void vscode.window.showInformationMessage(message);
    return undefined;
  }

  context.subscriptions.push(vscode.languages.registerCodeLensProvider({ language: "q1asm", scheme: "file" }, {
    onDidChangeCodeLenses: changed.event,
    async provideCodeLenses(document, token) {
      if (!autoRunEnabled() || document.isDirty) return [];
      if (!/(?:^\s*\.DEF\s|(?:[;#]|\/\/)\s*@(?:assert|assume)\b)/im.test(document.getText())) return [];
      const result = await program(document.uri);
      if (!result || token.isCancellationRequested || document.isDirty || !autoRunEnabled()) return [];
      return result.report.checks.flatMap((c, i) => {
        if (!sameFile(c.file, document.uri.fsPath) || c.line > document.lineCount) return [];
        const title = `${verdicts[c.status]} · ${c.message}`;
        return [new vscode.CodeLens(document.lineAt(c.line - 1).range, c.counterexample ?
          { title, tooltip: "Open the counterexample timeline and register history", command: replayCommand, arguments: [result.key, i] } :
          { title, command: c.status === "unknown" ? command : "", arguments: [document.uri], tooltip: c.message })];
      });
    },
  }), vscode.commands.registerCommand(replayCommand, async (key: string, index: number) => {
    const job = Array.from(jobs.values()).find((entry) => entry.id === key);
    const check = (await job?.result)?.checks[index];
    if (!job || job.abort.signal.aborted || !check?.counterexample) {
      void vscode.window.showInformationMessage("Verification inputs changed. Run verification again.");
      return;
    }
    openReplay(context, check);
  }), vscode.commands.registerCommand(command, async (uri: vscode.Uri | undefined, project?: vscode.Uri, sequencer?: string, inline = false) => {
    uri ??= vscode.window.activeTextEditor?.document.uri;
    if (!isQ1asm(uri)) [uri] = await vscode.window.showOpenDialog({ canSelectMany: false,
      title: "Verify Q1ASM program", filters: { Q1ASM: ["q1asm"] } }) || [];
    if (!uri) return;
    const verifySaved = () => program(uri!, project, sequencer, true);
    return vscode.window.withProgress({ location: vscode.ProgressLocation.Window, title: "Verifying program…" }, async () => {
      let result = await verifySaved();
      if (!result) {
        // A save can deliver several disk notifications after the command starts.
        await new Promise((resolve) => setTimeout(resolve, 200));
        result = await verifySaved();
      }
      return present(result, inline);
    });
  }), vscode.commands.registerCommand("q1timeline.verifyAlignment", async (uri?: vscode.Uri, inline = false) => {
    uri ??= vscode.window.activeTextEditor?.document.uri;
    if (uri && isQ1asm(uri)) uri = await nearbyProject(uri);
    if (!uri || !/\.ya?ml$/i.test(uri.fsPath)) [uri] = await vscode.window.showOpenDialog({
      canSelectMany: false, filters: { "Q1Lens project": ["yml", "yaml"] }, title: "Verify sequencer alignment",
    }) || [];
    if (!uri) return;
    const project = uri;
    return vscode.window.withProgress({ location: vscode.ProgressLocation.Window, title: "Checking alignment across measurement outcomes…" },
      async () => present(await run(project, ["verify", project.fsPath, "--alignment"], [], project, true), inline));
  }), vscode.commands.registerCommand("q1timeline.verifyEquivalence", async (before?: vscode.Uri, after?: vscode.Uri) => {
    before ??= vscode.window.activeTextEditor?.document.uri;
    const choose = async (title: string) => (await vscode.window.showOpenDialog({ canSelectMany: false, title, filters: { Q1ASM: ["q1asm"] } }))?.[0];
    if (!isQ1asm(before)) before = await choose("Select the original Q1ASM program");
    if (!before) return;
    after ??= await choose("Compare RT trace with this Q1ASM program");
    if (!after) return;
    const source = before, comparison = after;
    await vscode.window.withProgress({ location: vscode.ProgressLocation.Window, title: "Comparing RT traces…" },
      async () => present(await run(source, ["equiv", source.fsPath, comparison.fsPath], [source.fsPath, comparison.fsPath], undefined, true)));
  }));
}
