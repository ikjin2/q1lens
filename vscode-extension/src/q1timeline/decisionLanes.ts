import { randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";
import * as path from "node:path";
import type * as vscodeTypes from "vscode";
import { prependPythonArgs } from "./invocation";
import { findProjectFileCandidatesUpward } from "./projectDiscovery";
import { q1timelineConfigValue } from "./settings";

export const DECISION_LANES_COMMAND = "q1timeline.openDecisionLanes";
const INPUTS_STATE_KEY = "q1lens.decisionLanes.inputs";
const MAX_FEEDBACK_VALUES = 4096;
const MAX_FLIP_REQUESTS = 64;
const FLIP_BUDGET_MS = 200;
const REMEMBERED_TARGETS = 50;
const HISTORY_LENGTH = 8;

export type Feedback = number[] | null;
export type DecisionLanesMessage =
  | { type: "ready" }
  | { type: "refresh" }
  | { type: "open"; sequencer: string; feedback?: Feedback }
  | { type: "flips"; token: number | null; requests: { run: string; step: number }[] }
  | { type: "reveal"; line: number };

interface SequencerInfo { id: string; name: string; file: string; scheduled: boolean }
interface RememberedInputs { feedback: Feedback; history: string[] }
export interface WorkerClient {
  failure?: Error;
  request(payload: Record<string, unknown>, timeoutMs?: number): Promise<any>;
  dispose(): void;
}
export interface DecisionLanesDependencies {
  /** The Python command for this file, or undefined when the user did not finish setup. */
  ensureEnvironment(resource: vscodeTypes.Uri, options: { cwd: string; env: Record<string, string> }):
    Promise<{ pythonPath: string; pythonArgs: string[] } | undefined>;
  createClient(pythonPath: string, args: string[], options: { cwd: string; env: Record<string, string>; onStderr(text: string): void }): WorkerClient;
  /** The project file whose sequencers include this Q1ASM file, if there is one. */
  findProject(source: vscodeTypes.Uri): Promise<vscodeTypes.Uri | undefined>;
  /** Files whose saving changes the record of this project file or Q1ASM file. */
  relatedFiles(target: string): Promise<string[]>;
}

const fileKey = (file: string) => process.platform === "win32" ? path.resolve(file).toLowerCase() : path.resolve(file);
const isObject = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const hasOnlyKeys = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).every((key) => keys.includes(key));
const isWord = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= -(2 ** 31) && (value as number) <= 2 ** 32 - 1;

export function validFeedback(value: unknown): value is Feedback {
  return value === null || (Array.isArray(value) && value.length >= 1 && value.length <= MAX_FEEDBACK_VALUES && value.every(isWord));
}

/** Accepts only the messages the Decision lanes page sends, in exactly their shape. */
export function parseDecisionLanesMessage(message: unknown): DecisionLanesMessage | undefined {
  if (!isObject(message)) return undefined;
  if ((message.type === "ready" || message.type === "refresh") && hasOnlyKeys(message, ["type"])) return { type: message.type };
  if (message.type === "open" && typeof message.sequencer === "string" && message.sequencer.length > 0 && message.sequencer.length <= 256 &&
      (message.feedback === undefined || validFeedback(message.feedback)) && hasOnlyKeys(message, ["type", "sequencer", "feedback"])) {
    return message.feedback === undefined ? { type: "open", sequencer: message.sequencer }
      : { type: "open", sequencer: message.sequencer, feedback: message.feedback };
  }
  if (message.type === "flips" && (message.token === null || Number.isSafeInteger(message.token)) && Array.isArray(message.requests) &&
      message.requests.length >= 1 && message.requests.length <= MAX_FLIP_REQUESTS && hasOnlyKeys(message, ["type", "token", "requests"]) &&
      message.requests.every((item) => isObject(item) && typeof item.run === "string" && item.run.length > 0 && item.run.length <= 4096 &&
        Number.isSafeInteger(item.step) && (item.step as number) >= 0 && hasOnlyKeys(item, ["run", "step"]))) {
    return { type: "flips", token: message.token as number | null,
      requests: message.requests.map((item: { run: string; step: number }) => ({ run: item.run, step: item.step })) };
  }
  if (message.type === "reveal" && Number.isSafeInteger(message.line) && (message.line as number) >= 1 && hasOnlyKeys(message, ["type", "line"])) {
    return { type: "reveal", line: message.line as number };
  }
  return undefined;
}

export function decisionLanesHtml(options: { cspSource: string; nonce: string; styleUri: string; scriptUris: string[] }): string {
  const { cspSource, nonce, styleUri, scriptUris } = options;
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}'">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="${styleUri}"><title>Decision lanes</title></head>
<body><main id="decision-lanes"></main>
${scriptUris.map((uri) => `<script nonce="${nonce}" src="${uri}"></script>`).join("\n")}</body></html>`;
}

/** Worker arguments for a project file or a single Q1ASM file. */
export function decisionLanesArgs(target: string, executionMode: string, maxSteps: unknown): string[] {
  const args = ["-u", "-m", "q1timeline.decision_lanes", "--project", target];
  // As for DEF runs: the step limit setting applies only when the mode setting overrides the project.
  if (executionMode !== "project" && Number.isSafeInteger(maxSteps) && (maxSteps as number) >= 1) {
    args.push("--max-steps", String(Math.min(maxSteps as number, 100000)));
  }
  return args;
}

/** The sequencer to show: the one asked for, else the one written in this file, else the first. */
export function chooseSequencer(sequencers: SequencerInfo[], wanted: string | undefined, sourceFile: string | undefined): SequencerInfo | undefined {
  return sequencers.find((item) => item.id === wanted)
    ?? (sourceFile ? sequencers.find((item) => fileKey(item.file) === fileKey(sourceFile)) : undefined)
    ?? sequencers[0];
}

export function rememberInputs(stored: Record<string, RememberedInputs>, key: string, feedback: Feedback): Record<string, RememberedInputs> {
  const previous = stored[key]?.history ?? [];
  const text = feedback ? feedback.join(", ") : undefined;
  const history = text === undefined ? previous : [text, ...previous.filter((item) => item !== text)].slice(0, HISTORY_LENGTH);
  const next: Record<string, RememberedInputs> = {};
  // Most recent last; the oldest targets fall out first.
  for (const [name, value] of Object.entries(stored)) if (name !== key) next[name] = value;
  next[key] = { feedback, history };
  return Object.fromEntries(Object.entries(next).slice(-REMEMBERED_TARGETS));
}

export function workerFailureMessage(error: unknown, stderr: string): string {
  const message = error instanceof Error ? error.message : String(error);
  if (/No module named ['"]?q1timeline\.decision_lanes/.test(stderr)) {
    return "The Q1Lens Python package in the selected environment does not include Decision lanes. Update the q1lens package, then refresh.";
  }
  const last = stderr.trim().split(/\r?\n/).pop();
  return /process exited/.test(message) && last ? `${message} ${last}` : message;
}

export class DecisionLanes {
  private panel: vscodeTypes.WebviewPanel | undefined;
  private ready = false;
  private pending: Record<string, unknown> | undefined;
  private source: vscodeTypes.Uri | undefined;
  private target: string | undefined;
  private sequencer: string | undefined;
  private sourceFile: string | undefined;
  private client: WorkerClient | undefined;
  private clientKey: string | undefined;
  private stderr = "";
  private loadId = 0;
  private token: number | null = null;
  private related = new Set<string>();
  private staleShown = false;
  private reloadTimer: ReturnType<typeof setTimeout> | undefined;
  private readonly disposables: vscodeTypes.Disposable[] = [];

  constructor(
    private readonly vscode: typeof vscodeTypes,
    private readonly context: vscodeTypes.ExtensionContext,
    private readonly output: { appendLine(text: string): void },
    private readonly dependencies: DecisionLanesDependencies,
    private readonly reloadDelayMs = 150,
  ) {}

  private config<T>(key: string, fallback: T): T {
    const resource = this.source, vscode = this.vscode;
    return q1timelineConfigValue(vscode.workspace.getConfiguration("qbloxTimeline", resource),
      vscode.workspace.getConfiguration("q1timeline", resource), key, fallback, vscode.workspace.getConfiguration("q1lens", resource));
  }

  private post(message: Record<string, unknown>): void {
    // The page announces itself with "ready"; until then only the latest state is kept.
    if (message.type !== "runs") this.pending = message;
    if (this.ready) void this.panel?.webview.postMessage(message);
  }

  async open(uri?: vscodeTypes.Uri): Promise<void> {
    const vscode = this.vscode;
    const source = uri?.fsPath ? uri : vscode.window.activeTextEditor?.document.uri;
    if (!source || source.scheme !== "file" || path.extname(source.fsPath).toLowerCase() !== ".q1asm") {
      void vscode.window.showWarningMessage("Open a saved .q1asm file to show its decision lanes.");
      return;
    }
    if (vscode.workspace.isTrusted === false) {
      void vscode.window.showWarningMessage("Trust this workspace before Q1Lens runs Python.");
      return;
    }
    const project = await this.dependencies.findProject(source);
    const target = project?.fsPath ?? source.fsPath;
    if (!this.target || fileKey(this.target) !== fileKey(target)) this.stopWorker();
    if (!this.source || fileKey(this.source.fsPath) !== fileKey(source.fsPath)) this.sequencer = undefined;
    this.source = source;
    this.target = target;
    this.showPanel();
    await this.load();
  }

  private showPanel(): void {
    const vscode = this.vscode;
    if (this.panel) {
      this.panel.reveal(this.panel.viewColumn, true);
      return;
    }
    const media = vscode.Uri.joinPath(this.context.extensionUri, "out", "src", "q1timeline", "media");
    const panel = vscode.window.createWebviewPanel("q1timeline.decisionLanes", "Decision lanes",
      { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [media] });
    const asset = (name: string) => panel.webview.asWebviewUri(vscode.Uri.joinPath(media, name)).toString();
    this.panel = panel;
    this.ready = false;
    this.pending = undefined;
    panel.webview.html = decisionLanesHtml({ cspSource: panel.webview.cspSource, nonce: randomBytes(16).toString("base64"),
      styleUri: asset("decisionLanes.css"), scriptUris: [asset("decisionLanesModel.js"), asset("decisionLanes.js")] });
    panel.webview.onDidReceiveMessage((message) => void this.receive(message), undefined, this.disposables);
    panel.onDidDispose(() => {
      this.panel = undefined;
      this.ready = false;
      this.pending = undefined;
      this.loadId += 1;
      this.token = null;
      if (this.reloadTimer) clearTimeout(this.reloadTimer);
      this.reloadTimer = undefined;
      this.stopWorker();
      for (const item of this.disposables.splice(0)) item.dispose();      // this panel's two listeners
    }, undefined, this.disposables);
  }

  private async receive(raw: unknown): Promise<void> {
    const message = parseDecisionLanesMessage(raw);
    if (!message) {
      this.output.appendLine("Decision lanes: ignored a malformed webview message.");
      return;
    }
    if (message.type === "ready") {
      this.ready = true;
      if (this.pending) void this.panel?.webview.postMessage(this.pending);
    } else if (message.type === "refresh") await this.load();
    else if (message.type === "open") await this.load({ sequencer: message.sequencer, feedback: message.feedback });
    else if (message.type === "flips") await this.flips(message.token, message.requests);
    else await this.reveal(message.line);
  }

  private stopWorker(): void {
    const client = this.client;
    this.client = undefined;
    this.clientKey = undefined;
    client?.dispose();
  }

  private async worker(): Promise<WorkerClient> {
    const source = this.source!, target = this.target!;
    const rawEnv = this.config<Record<string, unknown>>("analyzer.env", {});
    const env = Object.fromEntries(Object.entries(isObject(rawEnv) ? rawEnv : {})
      .filter((entry): entry is [string, string] => typeof entry[1] === "string"));
    const args = decisionLanesArgs(target, this.config("executionMode", "project"), this.config("maxSteps", 10000));
    const cwd = path.dirname(target);
    const key = JSON.stringify({ args, env });
    if (this.client && !this.client.failure && this.clientKey === key) return this.client;
    this.stopWorker();
    const python = await this.dependencies.ensureEnvironment(source, { cwd, env });
    if (!python) throw new Error("Select a Python environment and complete setup, then refresh.");
    this.stderr = "";
    this.client = this.dependencies.createClient(python.pythonPath, prependPythonArgs(python.pythonArgs, args), {
      cwd, env, onStderr: (text) => {
        this.stderr = (this.stderr + text).slice(-4000);
        this.output.appendLine(`Decision lanes: ${text.trimEnd()}`);
      },
    });
    this.clientKey = key;
    return this.client;
  }

  private remembered(): Record<string, RememberedInputs> {
    const stored = this.context.workspaceState.get<Record<string, RememberedInputs>>(INPUTS_STATE_KEY, {});
    return isObject(stored) ? stored as Record<string, RememberedInputs> : {};
  }

  async load(request: { sequencer?: string; feedback?: Feedback } = {}): Promise<void> {
    if (!this.panel || !this.source || !this.target) return;
    const id = ++this.loadId, target = this.target, current = () => id === this.loadId && !!this.panel;
    const timeout = this.config("analyzer.timeoutMs", 30000);
    this.token = null;
    this.post({ type: "loading", text: "Recording…" });
    try {
      const client = await this.worker();
      if (!current()) return;
      const { info } = await client.request({ command: "info" }, timeout) as { info: { sequencers: SequencerInfo[] } };
      if (!current()) return;
      const chosen = chooseSequencer(info.sequencers, request.sequencer ?? this.sequencer, this.source.fsPath);
      if (!chosen) throw new Error("The project has no sequencers.");
      const key = `${fileKey(target)}::${chosen.id}`, stored = this.remembered()[key];
      const remembered = stored && validFeedback(stored.feedback) ? stored.feedback : undefined;
      // Without a remembered choice: the project's LINQ schedule when it has one, else every word reads 0.
      let feedback: Feedback = request.feedback !== undefined ? request.feedback : remembered !== undefined ? remembered : chosen.scheduled ? null : [0];
      if (feedback === null && !chosen.scheduled) feedback = [0];
      const { lanes } = await client.request({ command: "open", sequencer: chosen.id, feedback }, timeout);
      if (!current()) return;
      this.sequencer = chosen.id;
      this.sourceFile = lanes.sourceFile;
      this.token = id;
      const next = rememberInputs(this.remembered(), key, feedback);
      await this.context.workspaceState.update(INPUTS_STATE_KEY, next);
      if (!current()) return;
      this.related = new Set([target, lanes.sourceFile, ...await this.dependencies.relatedFiles(target)].map(fileKey));
      if (!current()) return;
      this.staleShown = false;
      this.panel.title = `Decision lanes · ${path.basename(lanes.sourceFile)}`;
      const unsaved = this.vscode.workspace.textDocuments.some((document) => document.isDirty && this.related.has(fileKey(document.uri.fsPath)));
      this.post({ type: "data", token: id, payload: lanes, history: [...new Set([...next[key].history, "0", "1"])],
        notice: unsaved ? "Unsaved changes are not included. Save the file to record again." : "" });
    } catch (error) {
      if (!current()) return;
      const message = workerFailureMessage(error, this.stderr);
      if (this.client?.failure) this.stopWorker();
      this.output.appendLine(`Decision lanes failed: ${message}`);
      this.post({ type: "error", message });
    }
  }

  private async flips(token: number | null, requests: { run: string; step: number }[]): Promise<void> {
    const client = this.client;
    if (token === null || token !== this.token) return;          // a request of a record that is no longer shown
    let answer: { runs?: Record<string, unknown>; failed?: unknown[] };
    try {
      // The page waits for an answer to every request of the record it shows, also when the worker is gone.
      if (!client) throw new Error("The Python worker is not running. Refresh to record again.");
      answer = await client.request({ command: "flips", requests, budget_ms: FLIP_BUDGET_MS }, this.config("analyzer.timeoutMs", 30000));
    } catch (error) {
      const reason = workerFailureMessage(error, this.stderr);
      answer = { runs: {}, failed: requests.map((item) => ({ ...item, reason })) };
    }
    if (token !== this.token) return;
    this.post({ type: "runs", token, runs: answer.runs ?? {}, failed: answer.failed ?? [] });
  }

  private async reveal(line: number): Promise<void> {
    const vscode = this.vscode, file = this.sourceFile;
    if (!file) return;
    try {
      const document = await vscode.workspace.openTextDocument(vscode.Uri.file(file));
      const visible = vscode.window.visibleTextEditors.find((editor) => fileKey(editor.document.uri.fsPath) === fileKey(file));
      const viewColumn = visible?.viewColumn ?? (this.panel?.viewColumn === vscode.ViewColumn.One ? vscode.ViewColumn.Two : vscode.ViewColumn.One);
      const range = document.lineAt(Math.min(Math.max(0, line - 1), document.lineCount - 1)).range;
      const editor = await vscode.window.showTextDocument(document, { viewColumn, preserveFocus: true, preview: false, selection: range });
      editor.revealRange(range, vscode.TextEditorRevealType.InCenterIfOutsideViewport);
    } catch (error) {
      this.output.appendLine(`Decision lanes: could not open the source line: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  /** A project or source file that changed on disk is recorded again; changes close together count once. */
  onFileChanged(file: string): void {
    if (!this.panel || !this.related.has(fileKey(file))) return;
    if (this.reloadTimer) clearTimeout(this.reloadTimer);
    this.reloadTimer = setTimeout(() => {
      this.reloadTimer = undefined;
      void this.load();
    }, this.reloadDelayMs);
  }

  /**
   * An unsaved edit only marks the panel. VS Code reports the dirty flag in a document change event
   * of its own, after the one that carries the edit, so every change event is looked at.
   */
  onDocumentChanged(document: vscodeTypes.TextDocument): void {
    if (!this.panel || this.staleShown || !document.isDirty || this.token === null || !this.related.has(fileKey(document.uri.fsPath))) return;
    this.staleShown = true;
    if (this.ready) void this.panel.webview.postMessage({ type: "stale", message: "Unsaved changes are not included. Save the file to record again." });
  }

  onEnvironmentChanged(): void {
    this.stopWorker();
    if (this.panel) void this.load();
  }

  dispose(): void {
    this.loadId += 1;
    if (this.reloadTimer) clearTimeout(this.reloadTimer);
    this.stopWorker();
    this.panel?.dispose();
    for (const item of this.disposables.splice(0)) item.dispose();
  }
}

export function registerDecisionLanes(context: vscodeTypes.ExtensionContext): void {
  const vscode = require("vscode") as typeof vscodeTypes;
  const output = vscode.window.createOutputChannel("Q1Lens Decision Lanes");
  const lanes = new DecisionLanes(vscode, context, output, {
    ensureEnvironment: (resource, options) => {
      const { ensureFilePythonEnvironment } = require("../fileEnvironment") as typeof import("../fileEnvironment");
      return ensureFilePythonEnvironment(vscode, resource, "q1asm", output, options);
    },
    createClient: (pythonPath, args, options) => {
      const { PlaybackClient } = require("./playbackClient");
      return new PlaybackClient(pythonPath, args, { ...options, label: "Decision lanes" }) as WorkerClient;
    },
    findProject: async (source) => {
      const { collectProjectRelatedPaths } = require("./projectRelatedFiles");
      const root = vscode.workspace.getWorkspaceFolder(source)?.uri.fsPath || path.parse(source.fsPath).root;
      const projectFile = q1timelineConfigValue(vscode.workspace.getConfiguration("qbloxTimeline", source),
        vscode.workspace.getConfiguration("q1timeline", source), "projectFile", "q1timeline.yml", vscode.workspace.getConfiguration("q1lens", source));
      for (const file of findProjectFileCandidatesUpward(source.fsPath, [projectFile, "q1timeline.yaml"], root)) {
        try {
          const related = collectProjectRelatedPaths(file, await readFile(file, "utf8")) as string[];
          if (related.some((item) => fileKey(item) === fileKey(source.fsPath))) return vscode.Uri.file(file);
        } catch {
          // An unreadable candidate is not this file's project.
        }
      }
      return undefined;
    },
    relatedFiles: async (target) => {
      if (path.extname(target).toLowerCase() === ".q1asm") return [];
      try {
        const { collectProjectRelatedPaths } = require("./projectRelatedFiles");
        return collectProjectRelatedPaths(target, await readFile(target, "utf8")) as string[];
      } catch {
        return [];
      }
    },
  });
  // Saving in VS Code and a change made outside it (another editor, a checkout) both end up here.
  const watcher = vscode.workspace.createFileSystemWatcher("**/*.{q1asm,yml,yaml,json}");
  context.subscriptions.push(
    output, lanes, watcher,
    vscode.commands.registerCommand(DECISION_LANES_COMMAND, (uri?: vscodeTypes.Uri) => lanes.open(uri)),
    watcher.onDidChange((uri) => lanes.onFileChanged(uri.fsPath)),
    watcher.onDidCreate((uri) => lanes.onFileChanged(uri.fsPath)),
    watcher.onDidDelete((uri) => lanes.onFileChanged(uri.fsPath)),
    vscode.workspace.onDidSaveTextDocument((document) => lanes.onFileChanged(document.uri.fsPath)),
    vscode.workspace.onDidChangeTextDocument((event) => lanes.onDocumentChanged(event.document)),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (["q1lens.pythonEnvironment", "q1lens.pythonPath", "q1lens.pythonArgs", "q1lens.q1timeline.pythonPath", "q1lens.q1timeline.pythonArgs",
        "q1lens.q1timeline.analyzer", "q1lens.q1timeline.executionMode", "q1lens.q1timeline.maxSteps", "qbloxTimeline", "q1timeline", "python"]
        .some((key) => event.affectsConfiguration(key))) lanes.onEnvironmentChanged();
    }),
  );
}
