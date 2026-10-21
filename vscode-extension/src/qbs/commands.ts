import { existsSync } from "node:fs";
import { access, mkdir, readdir, readFile, rm } from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { computeDiagnostics } from "./diagnosticsCore";
import { buildAnalyzeInvocation, buildQ1TimelineAnalyzeInvocation, buildRenderInvocation, runProcessWithSpawn } from "./qbsCli";
import { OutputPaths, ProjectConfigLite, chooseProjectFile, deriveOutputPaths, isProjectFile, parseProjectConfigLite } from "./projectDiscovery";
import { parseQbsIrText, Q1TimelineDiagnostic, QbsIr } from "./qbsIr";
import { QbsTimelineSettings, readSettings } from "./settings";
import { ensureNotebookEnvironment } from "../notebookEnvironment";
import { ensureFilePythonEnvironment } from "../fileEnvironment";
import type * as vscodeTypes from "vscode";
import { openFileAtLine, openFileBelowAtLine, openNotebookAtCell, resolveGeneratedFile } from "./navigation";
import { Q1TimelineOpenTarget } from "../q1timeline/api";
import { fallbackAnalyzerDiagnostic, normalizeAnalyzerDiagnostics } from "../q1timeline/diagnosticFallback";
import { resolveQbsSelectionToQ1TimelineTarget, Q1TimelineSelection } from "../integration/q1timelineTarget";
import { DebouncedRefreshQueue } from "./watcher";
import {
  createManagedNotebookTimelineProject,
  inferNotebookTimelineVariables,
  NotebookTimelineCell,
  QBS_NOTEBOOK_SCHEDULE_TAG,
  QBS_NOTEBOOK_SETUP_TAG,
  withNotebookCellTag,
  withoutNotebookCellTag,
} from "./notebookProject";

const QBS_SUBPROCESS_TIMEOUT_MS = 30000;
export const QBS_AUTO_REFRESH_GLOB = "**/{qbstimeline.yml,qbstimeline.yaml,*.py,*.ipynb}";
export const QBS_AUTO_REFRESH_CONFIGURATION_KEYS = [
  "q1lens.qbs.autoRefresh",
  "qbloxTimeline.qbs.autoRefresh",
  "qbsTimeline.autoRefresh",
] as const;
export const QBS_ANALYSIS_REQUIRED_MESSAGE = "Run Q1Lens: Analyze and Open first.";
export const QBS_ANALYZE_AND_OPEN_ACTION = "Analyze and Open";

interface AutoRefreshConfigurationEvent {
  affectsConfiguration(section: string, resource?: vscodeTypes.Uri): boolean;
}

interface AutoRefreshWatcher {
  onDidChange(listener: (uri: vscodeTypes.Uri) => void): vscodeTypes.Disposable;
  onDidCreate(listener: (uri: vscodeTypes.Uri) => void): vscodeTypes.Disposable;
  onDidDelete(listener: (uri: vscodeTypes.Uri) => void): vscodeTypes.Disposable;
  dispose(): void;
}

function createSourceWatcher(vscode: typeof vscodeTypes, file: string): AutoRefreshWatcher {
  const directory = dirname(file);
  type Change = "change" | "create" | "delete";
  const listeners: Partial<Record<Change, (uri: vscodeTypes.Uri) => void>> = {};
  let watcher: vscodeTypes.FileSystemWatcher | undefined;
  let generation = 0;
  let disposed = false;
  const bind = () => {
    const currentGeneration = ++generation;
    watcher?.dispose();
    let base = directory;
    while (!existsSync(base) && dirname(base) !== base) base = dirname(base);
    const missingParent = base !== directory;
    watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(vscode.Uri.file(base), missingParent ? "**/*" : "*"));
    const receive = (change: Change) => (uri: vscodeTypes.Uri) => {
      if (disposed || generation !== currentGeneration) return;
      const descendant = relative(uri.fsPath, file);
      const deletedAncestor = change === "delete" && descendant !== "" && descendant !== ".." &&
        !descendant.startsWith(`..${sep}`) && !isAbsolute(descendant);
      if (relative(file, uri.fsPath) !== "" && !deletedAncestor) return;
      if (deletedAncestor || (change === "delete" && !existsSync(directory)) || (missingParent && change === "create")) bind();
      listeners[change]?.(deletedAncestor ? vscode.Uri.file(file) : uri);
    };
    watcher.onDidChange(receive("change"));
    watcher.onDidCreate(receive("create"));
    watcher.onDidDelete(receive("delete"));
  };
  const subscribe = (change: Change, listener: (uri: vscodeTypes.Uri) => void) => {
    listeners[change] = listener;
    return { dispose: () => { if (listeners[change] === listener) delete listeners[change]; } };
  };
  bind();
  return {
    onDidChange: (listener) => subscribe("change", listener),
    onDidCreate: (listener) => subscribe("create", listener),
    onDidDelete: (listener) => subscribe("delete", listener),
    dispose: () => { disposed = true; generation += 1; watcher?.dispose(); },
  };
}

export interface RegisterQbsAutoRefreshDeps {
  resource?: () => vscodeTypes.Uri | undefined;
  readEnabled: () => boolean;
  createWatcher: () => AutoRefreshWatcher | AutoRefreshWatcher[];
  onDidChangeConfiguration: (
    listener: (event: AutoRefreshConfigurationEvent) => void,
  ) => vscodeTypes.Disposable;
  shouldRefresh: (uri: vscodeTypes.Uri) => boolean;
  run: (isCancelled: () => boolean) => Promise<void>;
  delayMs: number;
}

export function registerQbsAutoRefresh(deps: RegisterQbsAutoRefreshDeps): vscodeTypes.Disposable & { update(): void } {
  let active: vscodeTypes.Disposable | undefined;
  let disposed = false;
  const stopWatching = () => {
    active?.dispose();
    active = undefined;
  };
  const syncWatcher = () => {
    stopWatching();
    if (disposed || !deps.readEnabled()) {
      return;
    }
    let cancelled = false;
    const queue = new DebouncedRefreshQueue(deps.delayMs, () => deps.run(() => cancelled));
    const created = deps.createWatcher();
    const watchers = Array.isArray(created) ? created : [created];
    const requestIfCurrentProject = (uri: vscodeTypes.Uri) => {
      if (deps.shouldRefresh(uri)) {
        queue.request();
      }
    };
    for (const watcher of watchers) {
      watcher.onDidChange(requestIfCurrentProject);
      watcher.onDidCreate(requestIfCurrentProject);
      watcher.onDidDelete(requestIfCurrentProject);
    }
    active = {
      dispose: () => {
        cancelled = true;
        queue.dispose();
        for (const watcher of watchers) watcher.dispose();
      },
    };
  };
  syncWatcher();
  const configurationChanges = deps.onDidChangeConfiguration((event) => {
    const resource = deps.resource?.();
    if (QBS_AUTO_REFRESH_CONFIGURATION_KEYS.some((key) => event.affectsConfiguration(key, resource))) {
      syncWatcher();
    }
  });
  return {
    update: syncWatcher,
    dispose: () => {
      disposed = true;
      configurationChanges.dispose();
      stopWatching();
    },
  };
}

export async function ensureQbsAnalysisAvailable(input: {
  isAvailable: () => boolean;
  analyze: () => Promise<void>;
  prompt: (message: string, action: string) => Promise<string | undefined>;
}): Promise<boolean> {
  if (input.isAvailable()) {
    return true;
  }
  const action = await input.prompt(QBS_ANALYSIS_REQUIRED_MESSAGE, QBS_ANALYZE_AND_OPEN_ACTION);
  if (action !== QBS_ANALYZE_AND_OPEN_ACTION) {
    return false;
  }
  await input.analyze();
  return input.isAvailable();
}

export interface AnalyzeAndOpenDeps {
  isDisposed?: () => boolean;
  resolveProject: () => Promise<string | undefined>;
  readProjectConfig: (projectFile: string) => Promise<ProjectConfigLite>;
  derivePaths: (input: {
    projectFile: string;
    scheduleFile?: string;
    scheduleNotebook?: string;
    sourceNotebook?: string;
    outputDir: string;
    overrideOutputDir: string | null;
  }) => OutputPaths;
  analyze: (paths: OutputPaths, settings: QbsTimelineSettings) => Promise<void>;
  render: (paths: OutputPaths, settings: QbsTimelineSettings) => Promise<void>;
  loadIr: (paths: OutputPaths) => Promise<QbsIr>;
  analyzeQ1Timeline?: (paths: OutputPaths, settings: QbsTimelineSettings) => Promise<boolean | void>;
  loadQ1TimelineIr?: (paths: OutputPaths) => Promise<Record<string, unknown> | undefined>;
  loadQ1TimelineDiagnostics?: (paths: OutputPaths) => Promise<Q1TimelineDiagnostic[]>;
  reportQ1TimelineWarning?: (message: string) => void;
  listExistingQ1asmFiles: (paths: OutputPaths) => Promise<Set<string>>;
  publishDiagnostics: (paths: OutputPaths, ir: QbsIr, existingFiles: Set<string>) => void;
  showPanel: (ir: QbsIr, paths: OutputPaths) => void;
  readSettings: (projectFile: string, config: ProjectConfigLite) => QbsTimelineSettings | undefined | Promise<QbsTimelineSettings | undefined>;
  showOutput: () => void;
}

export interface RegisterCommandsDeps {
  openQ1TimelineTarget: (target: Q1TimelineOpenTarget) => Promise<void>;
}

export interface Q1AsmSourceSelection {
  sequencer?: string;
  line?: number;
}

export interface ScheduleSourceSelection {
  schedulableId?: string;
  operationId?: string;
  blockId?: string;
}

export type SourceTarget =
  | { kind: "file"; file: string; line: number }
  | { kind: "notebook"; file: string; cellIndex: number; cellLine?: number; cellId?: string };

export function resolveQ1AsmSourceTarget(input: {
  ir: QbsIr;
  outputDir: string;
  sequencer?: string;
  line?: number;
}): Extract<SourceTarget, { kind: "file" }> {
  const sequencer = input.sequencer ?? input.ir.q1asm_programs[0]?.sequencer;
  if (!sequencer) {
    throw new Error("No Q1ASM sequencer was provided");
  }
  const program = input.ir.q1asm_programs.find(
    (candidate) => candidate.sequencer === sequencer || candidate.sequencer_id === sequencer,
  );
  if (!program) {
    throw new Error(`No Q1ASM program found for sequencer ${sequencer}`);
  }
  return {
    kind: "file",
    file: resolveGeneratedFile({ outputDir: input.outputDir, relativeFile: program.file }),
    line: input.line ?? 1,
  };
}

export function resolveScheduleSourceTarget(input: {
  ir: QbsIr;
  projectDir?: string;
  scheduleFile?: string;
  sourceNotebookFile?: string;
  selection: ScheduleSourceSelection;
}): SourceTarget {
  const candidates = [
    input.selection.blockId,
    input.selection.schedulableId,
    input.selection.operationId,
  ].filter((value): value is string => typeof value === "string" && value.length > 0);
  for (const candidate of candidates) {
    const location = input.ir.source_map?.schedulables?.[candidate];
    if (location?.notebook) {
      return {
        kind: "notebook",
        file: input.sourceNotebookFile ?? resolveSourceMapFile(location.notebook.file ?? location.file ?? "", input.projectDir),
        cellIndex: location.notebook.cell_index,
        ...(location.notebook.cell_id ? { cellId: location.notebook.cell_id } : {}),
        ...(typeof location.notebook.cell_line === "number" ? { cellLine: location.notebook.cell_line } : {}),
      };
    }
    if (typeof location?.line === "number") {
      const locationFile = typeof location.file === "string" ? location.file : "";
      const mappedFile = locationFile && (isAbsolute(locationFile) || input.projectDir)
        ? resolveSourceMapFile(locationFile, input.projectDir)
        : input.scheduleFile;
      if (!mappedFile) {
        throw new Error("No generated schedule file is available for this source location");
      }
      return { kind: "file", file: mappedFile, line: location.line };
    }
  }
  const primary = input.ir.source_map?.primary;
  if (primary?.kind === "notebook" && primary.file) {
    return { kind: "notebook", file: input.sourceNotebookFile ?? resolveSourceMapFile(primary.file, input.projectDir), cellIndex: 0 };
  }
  if (!input.scheduleFile) {
    throw new Error("No generated schedule file is available");
  }
  return { kind: "file", file: input.scheduleFile, line: 1 };
}

function resolveSourceMapFile(file: string, projectDir?: string): string {
  if (!file || isAbsolute(file) || !projectDir) {
    return file;
  }
  return join(projectDir, file);
}

export function isWatchedPathForCurrentProject(input: { changedFile: string; projectDir: string }): boolean {
  const changed = resolve(input.changedFile);
  const projectDir = resolve(input.projectDir);
  const rel = relative(projectDir, changed);
  return rel !== ".." && !rel.startsWith(`..${sep}`) && !isAbsolute(rel);
}

export async function openSourceTarget(target: SourceTarget): Promise<void> {
  if (target.kind === "notebook") {
    await openNotebookAtCell(target.file, target.cellIndex, target.cellLine, target.cellId);
    return;
  }
  await openFileAtLine(target.file, target.line);
}

interface NotebookCellContext {
  notebook: any;
  selectedCellIndex: number;
  notebookPath: string;
  cells: NotebookTimelineCell[];
}

interface ManagedNotebookPreparation {
  projectFile: string;
  snapshotFile: string;
  sourceNotebookPath: string;
  run: () => Promise<void>;
}

export function createAnalyzeAndOpenHandler(deps: AnalyzeAndOpenDeps): () => Promise<void> {
  return async () => {
    if (deps.isDisposed?.()) return;
    const projectFile = await deps.resolveProject();
    if (deps.isDisposed?.() || !projectFile) {
      return;
    }

    const config = await deps.readProjectConfig(projectFile);
    if (deps.isDisposed?.()) return;
    const settings = await deps.readSettings(projectFile, config);
    if (deps.isDisposed?.() || !settings) {
      return;
    }
    const currentConfig = await deps.readProjectConfig(projectFile);
    if (deps.isDisposed?.()) return;
    const projectDir = dirname(projectFile);
    if ((["scheduleFile", "scheduleNotebook", "sourceNotebook", "outputDir"] as const).some((key) => {
      const originalValue = key === "outputDir" ? settings.outputDirOverride ?? config[key] : config[key];
      const currentValue = key === "outputDir" ? settings.outputDirOverride ?? currentConfig[key] : currentConfig[key];
      return originalValue === undefined || currentValue === undefined ? originalValue !== currentValue
        : relative(resolve(projectDir, originalValue), resolve(projectDir, currentValue)) !== "";
    })) {
      throw new Error("Q1Lens project source or output directory changed while selecting Python. Run Analyze and Open again.");
    }
    if (settings.revealOutputChannel) {
      deps.showOutput();
    }

    const paths = deps.derivePaths({
      projectFile,
      scheduleFile: config.scheduleFile,
      scheduleNotebook: config.scheduleNotebook,
      sourceNotebook: config.sourceNotebook,
      outputDir: config.outputDir,
      overrideOutputDir: settings.outputDirOverride,
    });

    await deps.analyze(paths, settings);
    if (deps.isDisposed?.()) return;
    await deps.render(paths, settings);
    if (deps.isDisposed?.()) return;
    const ir = await deps.loadIr(paths);
    if (deps.isDisposed?.()) return;
    if (deps.analyzeQ1Timeline && deps.loadQ1TimelineIr && ir.q1asm_programs.length > 0) {
      try {
        const analyzed = await deps.analyzeQ1Timeline(paths, settings);
        if (deps.isDisposed?.()) return;
        if (analyzed !== false) {
          const q1timelineIr = await deps.loadQ1TimelineIr(paths);
          if (deps.isDisposed?.()) return;
          if (q1timelineIr) {
            ir.q1timeline_ir = q1timelineIr;
          }
          if (deps.loadQ1TimelineDiagnostics) {
            ir.q1timeline_diagnostics = await deps.loadQ1TimelineDiagnostics(paths);
            if (deps.isDisposed?.()) return;
          }
        }
      } catch (error) {
        if (deps.isDisposed?.()) return;
        ir.q1timeline_diagnostics = [
          fallbackAnalyzerDiagnostic(
            paths.projectFile,
            `q1timeline inline analysis failed: ${String(error)}`,
            error instanceof SyntaxError ? "invalid_analyzer_json" : "q1timeline_inline_analysis_failed",
          ),
        ] as Q1TimelineDiagnostic[];
        deps.reportQ1TimelineWarning?.(`q1timeline inline analysis failed: ${String(error)}`);
      }
    }
    const existingFiles = await deps.listExistingQ1asmFiles(paths);
    if (deps.isDisposed?.()) return;
    deps.publishDiagnostics(paths, ir, existingFiles);
    deps.showPanel(ir, paths);
  };
}

export async function readProjectConfigFromDisk(projectFile: string): Promise<ProjectConfigLite> {
  return parseProjectConfigLite(await readFile(projectFile, "utf8"));
}

export async function loadIrFromDisk(paths: OutputPaths): Promise<QbsIr> {
  return parseQbsIrText(await readFile(paths.irPath, "utf8"));
}

export async function analyzeWithCli(paths: OutputPaths, settings: QbsTimelineSettings): Promise<void> {
  const invocation = buildAnalyzeInvocation({
    pythonPath: settings.pythonPath,
    pythonArgs: settings.pythonArgs,
    projectFile: paths.projectFile,
    irPath: paths.irPath,
    ...(settings.outputDirOverride == null ? {} : { outputDir: paths.outputDir }),
  });
  const notebookPath = paths.scheduleNotebookPath && (paths.sourceNotebookPath ?? paths.scheduleNotebookPath);
  const cwd = notebookPath ? dirname(notebookPath) : paths.projectDir;
  const result = await runProcessWithSpawn(invocation.command, invocation.args, { cwd, timeoutMs: QBS_SUBPROCESS_TIMEOUT_MS });
  if (result.exitCode !== 0) {
    throw new Error(result.stderr || result.stdout || "Q1Lens analyze failed");
  }
}

export async function renderWithCli(paths: OutputPaths, settings: QbsTimelineSettings): Promise<void> {
  const invocation = buildRenderInvocation({
    pythonPath: settings.pythonPath,
    pythonArgs: settings.pythonArgs,
    irPath: paths.irPath,
    htmlPath: paths.htmlPath,
  });
  const result = await runProcessWithSpawn(invocation.command, invocation.args, { cwd: paths.projectDir, timeoutMs: QBS_SUBPROCESS_TIMEOUT_MS });
  if (result.exitCode !== 0) {
    throw new Error(result.stderr || result.stdout || "Q1Lens render failed");
  }
}

function q1timelineProjectPath(paths: OutputPaths): string {
  return join(paths.outputDir, "q1timeline.yml");
}

function q1timelineOutputDir(paths: OutputPaths): string {
  return join(paths.outputDir, ".q1timeline");
}

export async function analyzeQ1TimelineWithCli(paths: OutputPaths, settings: QbsTimelineSettings): Promise<boolean> {
  const projectFile = q1timelineProjectPath(paths);
  try {
    await access(projectFile);
  } catch {
    await rm(q1timelineOutputDir(paths), { recursive: true, force: true });
    return false;
  }
  const outputDir = q1timelineOutputDir(paths);
  await mkdir(outputDir, { recursive: true });
  await rm(join(outputDir, "timeline_ir.json"), { force: true });
  await rm(join(outputDir, "diagnostics.json"), { force: true });
  const invocation = buildQ1TimelineAnalyzeInvocation({
    pythonPath: settings.pythonPath,
    pythonArgs: settings.pythonArgs,
    q1timelineCommand: settings.q1timelineCommand,
    projectFile,
    timelineIrPath: join(outputDir, "timeline_ir.json"),
    diagnosticsPath: join(outputDir, "diagnostics.json"),
  });
  const result = await runProcessWithSpawn(invocation.command, invocation.args, { cwd: paths.outputDir, timeoutMs: QBS_SUBPROCESS_TIMEOUT_MS });
  if (result.exitCode !== 0) {
    throw new Error(result.stderr || result.stdout || "q1timeline analyze failed");
  }
  return true;
}

export async function loadQ1TimelineIrFromDisk(paths: OutputPaths): Promise<Record<string, unknown> | undefined> {
  try {
    return JSON.parse(await readFile(join(q1timelineOutputDir(paths), "timeline_ir.json"), "utf8")) as Record<string, unknown>;
  } catch (error: any) {
    if (error?.code === "ENOENT") {
      return undefined;
    }
    throw error;
  }
}

export async function loadQ1TimelineDiagnosticsFromDisk(paths: OutputPaths): Promise<Q1TimelineDiagnostic[]> {
  try {
    const parsed = JSON.parse(await readFile(join(q1timelineOutputDir(paths), "diagnostics.json"), "utf8"));
    return normalizeAnalyzerDiagnostics(
      parsed,
      q1timelineProjectPath(paths),
    ).filter((item) => typeof item === "object" && item !== null) as Q1TimelineDiagnostic[];
  } catch (error: any) {
    if (error?.code === "ENOENT") {
      return [];
    }
    throw error;
  }
}

async function resolveProjectWithVscode(target?: vscodeTypes.Uri, isDisposed?: () => boolean): Promise<string | undefined> {
  if (isDisposed?.()) return undefined;
  const vscode = require("vscode") as typeof vscodeTypes;
  const activeFile = target?.fsPath ?? vscode.window.activeTextEditor?.document.uri.fsPath ?? null;
  const discovered = await vscode.workspace.findFiles("**/qbstimeline.y{ml,aml}", "**/.qbs_timeline/**");
  if (isDisposed?.()) return undefined;
  const targetProject = target && !isProjectFile(target.fsPath)
    ? discovered.filter((uri) => isWatchedPathForCurrentProject({ changedFile: target.fsPath, projectDir: dirname(uri.fsPath) }))
      .sort((left, right) => dirname(right.fsPath).length - dirname(left.fsPath).length)[0]
    : undefined;
  return chooseProjectFile({
    activeFile: targetProject?.fsPath ?? activeFile,
    discoveredFiles: discovered.map((uri) => uri.fsPath),
    choose: async (items) => {
      const picked = await vscode.window.showQuickPick(items, {
        title: "Select Q1Lens project",
        placeHolder: "qbstimeline.yml",
      });
      return picked?.path;
    },
  });
}

async function listExistingQ1asmFilesFromDisk(paths: OutputPaths): Promise<Set<string>> {
  const files = new Set<string>();
  try {
    const entries = await readdir(paths.q1asmDir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isFile() && entry.name.endsWith(".q1asm")) {
        files.add(`q1asm/${entry.name}`);
      }
    }
  } catch {
    // Missing q1asm folder becomes QBST005 diagnostics.
  }
  return files;
}

function resolveNotebookCellContext(vscode: any, cellArg?: unknown): NotebookCellContext | undefined {
  const explicitCell = explicitNotebookCell(cellArg);
  const activeEditor = vscode.window.activeNotebookEditor;
  const notebook = explicitCell?.notebook ?? activeEditor?.notebook;
  const selectedCellIndex = explicitCell?.index ?? activeEditor?.selection?.start;
  if (!notebook?.uri?.fsPath || typeof selectedCellIndex !== "number") {
    return undefined;
  }
  const cells = notebookCellsFromDocument(vscode, notebook);
  if (cells[selectedCellIndex]?.kind !== "code") {
    return undefined;
  }
  return {
    notebook,
    selectedCellIndex,
    notebookPath: notebook.uri.fsPath,
    cells,
  };
}

async function firstQ1asmFileInFolder(folder: string): Promise<string | undefined> {
  try {
    const entries = await readdir(folder, { withFileTypes: true });
    return entries
      .filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith(".q1asm"))
      .map((entry) => join(folder, entry.name))
      .sort((left, right) => left.localeCompare(right))[0];
  } catch {
    return undefined;
  }
}

function currentFolderForQ1asmSelection(vscode: typeof vscodeTypes): string | undefined {
  const activeFile = vscode.window.activeTextEditor?.document.uri.fsPath;
  if (activeFile) {
    return dirname(activeFile);
  }
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

function explicitNotebookCell(cellArg: unknown): { notebook: any; index: number } | undefined {
  if (!cellArg || typeof cellArg !== "object") {
    return undefined;
  }
  const candidate = cellArg as { notebook?: any; index?: unknown };
  if (candidate.notebook?.uri?.fsPath && typeof candidate.index === "number") {
    return { notebook: candidate.notebook, index: candidate.index };
  }
  return undefined;
}

export function notebookCellsFromDocument(vscode: any, notebook: any): NotebookTimelineCell[] {
  const cells = typeof notebook.getCells === "function"
    ? notebook.getCells()
    : Array.from({ length: Number(notebook.cellCount || 0) }, (_value, index) => notebook.cellAt(index));
  return cells.map((cell: any) => {
    const apiMetadata = isRecord(cell.metadata) ? cell.metadata : {};
    const notebookMetadata = isRecord(apiMetadata.custom) ? apiMetadata.custom : apiMetadata;
    return {
      ...(typeof notebookMetadata.id === "string" ? { id: notebookMetadata.id } : {}),
      kind: cell.kind === vscode.NotebookCellKind?.Markup ? "markdown" : "code",
      text: typeof cell.document?.getText === "function" ? cell.document.getText() : "",
      metadata: isRecord(notebookMetadata.metadata) ? { ...notebookMetadata.metadata } : { ...notebookMetadata },
    };
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function registerCommands(context: vscodeTypes.ExtensionContext, deps: RegisterCommandsDeps): void {
  const vscode = require("vscode") as typeof vscodeTypes;
  const { publishDiagnostics: publishVscodeDiagnostics } = require("./diagnostics") as typeof import("./diagnostics");
  const { TimelinePanel } = require("./webview/panel") as typeof import("./webview/panel");
  const output = vscode.window.createOutputChannel("Q1Lens");
  const diagnostics = vscode.languages.createDiagnosticCollection("qbsTimeline");
  let disposed = false;
  let lastPaths: OutputPaths | undefined;
  let lastIr: QbsIr | undefined;
  let generatedSourceCurrent = true;
  let openRequestId = 0;
  let notebookOpenEnvironmentCheck: AbortController | undefined;
  let analysisQueue = Promise.resolve();
  let pendingAnalysis: { project: Promise<string | undefined>; prepareProject?: ManagedNotebookPreparation } | undefined;
  let lastPrepareProject: ManagedNotebookPreparation | undefined;
  const managedArtifactFiles = new Set<string>();
  let autoRefresh: ReturnType<typeof registerQbsAutoRefresh> | undefined;
  const settingsResourcePath = (paths?: OutputPaths) => paths && (paths.scheduleNotebookPath
    ? paths.sourceNotebookPath ?? paths.scheduleNotebookPath
    : paths.schedulePath ?? paths.projectFile);
  let analyzeHandler: (target?: vscodeTypes.Uri) => Promise<void>;
  let refreshHandler: (isCancelled?: () => boolean) => Promise<void>;
  const ensureAnalysis = () => ensureQbsAnalysisAvailable({
    isAvailable: () => Boolean(lastPaths && lastIr),
    analyze: () => analyzeHandler(),
    prompt: async (message, action) => vscode.window.showWarningMessage(message, action),
  });
  const openQ1TimelineSelection = async (selection: Q1TimelineSelection) => {
    if (!(await ensureAnalysis())) {
      return;
    }
    if (!generatedSourceCurrent) {
      await vscode.window.showWarningMessage("Generated Q1ASM files no longer match this QBS result. Wait for analysis to finish or run Analyze and Open again.");
      return;
    }
    try {
      const target = resolveQbsSelectionToQ1TimelineTarget({ ir: lastIr!, selection, outputDir: lastPaths!.outputDir });
      await deps.openQ1TimelineTarget(target);
    } catch (error) {
      output.appendLine(`q1timeline target failed: ${String(error)}`);
      await vscode.window.showWarningMessage("Q1ASM timeline target failed. Check the Q1Lens output for details.");
    }
  };
  const panel = new TimelinePanel(context.extensionUri, {
    onRefresh: async () => {
      await refreshHandler();
    },
    onOpenQ1Timeline: async (message) => {
      await openQ1TimelineSelection(message);
    },
    onOpenQ1AsmSource: async (message) => {
      if (!lastPaths || !lastIr) {
        await vscode.window.showWarningMessage("Run Q1Lens: Analyze and Open before opening Q1ASM source.");
        return;
      }
      if (!generatedSourceCurrent) {
        await vscode.window.showWarningMessage("Generated Q1ASM files no longer match this QBS result. Wait for analysis to finish or run Analyze and Open again.");
        return;
      }
      try {
        const target = resolveQ1AsmSourceTarget({
          ir: lastIr,
          outputDir: lastPaths.outputDir,
          sequencer: message.sequencer,
          line: message.line,
        });
        await openFileBelowAtLine(target.file, target.line);
      } catch (error) {
        output.appendLine(`Q1ASM source target failed: ${String(error)}`);
        await vscode.window.showWarningMessage("Q1ASM source target failed. Check the Q1Lens output for details.");
      }
    },
    onOpenScheduleSource: async (message) => {
      if (!lastPaths || !lastIr) {
        await vscode.window.showWarningMessage("Run Q1Lens: Analyze and Open before opening schedule source.");
        return;
      }
      const target = resolveScheduleSourceTarget({
        ir: lastIr,
        projectDir: lastPaths.projectDir,
        scheduleFile: lastPaths.schedulePath,
        sourceNotebookFile: lastPaths.sourceNotebookPath ?? lastPaths.scheduleNotebookPath,
        selection: message,
      });
      await openSourceTarget(target);
    },
    onOpenIr: async () => {
      if (lastPaths) {
        await openFileAtLine(lastPaths.irPath, 1);
      }
    },
    onOpenProjectFile: async () => {
      if (lastPaths) {
        await openFileAtLine(lastPaths.projectFile, 1);
      }
    },
    onOpenScheduleFile: async () => {
      if (lastPaths?.schedulePath) {
        await openSourceTarget({ kind: "file", file: lastPaths.schedulePath, line: 1 });
      }
    },
    onOpenNotebookFile: async () => {
      if (!lastPaths || !lastIr) {
        await vscode.window.showWarningMessage("Run Q1Lens: Analyze and Open before opening the notebook.");
        return;
      }
      const target = resolveScheduleSourceTarget({
        ir: lastIr,
        projectDir: lastPaths.projectDir,
        scheduleFile: lastPaths.schedulePath,
        sourceNotebookFile: lastPaths.sourceNotebookPath ?? lastPaths.scheduleNotebookPath,
        selection: {},
      });
      await openSourceTarget(target);
    },
  });
  context.subscriptions.push({ dispose: () => {
    disposed = true;
    notebookOpenEnvironmentCheck?.abort();
    pendingAnalysis = undefined;
    lastPaths = undefined;
    lastIr = undefined;
    lastPrepareProject = undefined;
    panel.dispose();
  } });

  const createHandler = (
    resolveProject: () => Promise<string | undefined>,
    notebookEnvironment?: { pythonPath: string; pythonArgs: string[] },
    onEnvironmentResource?: (resource: vscodeTypes.Uri) => void,
    revealPanel = false,
    prepareProject?: ManagedNotebookPreparation,
  ) => createAnalyzeAndOpenHandler({
      isDisposed: () => disposed,
      resolveProject,
      readProjectConfig: readProjectConfigFromDisk,
      derivePaths: (input) => {
        return deriveOutputPaths(input);
      },
      analyze: async (paths, settings) => {
        if (lastPaths && relative(lastPaths.outputDir, paths.outputDir) === "") generatedSourceCurrent = false;
        output.appendLine(`Analyzing ${paths.projectFile}`);
        await analyzeWithCli(paths, settings);
      },
      render: async (paths, settings) => {
        output.appendLine(`Rendering ${paths.irPath}`);
        await renderWithCli(paths, settings);
      },
      loadIr: loadIrFromDisk,
      analyzeQ1Timeline: async (paths, settings) => {
        output.appendLine(`Analyzing q1timeline events for ${join(paths.outputDir, "q1timeline.yml")}`);
        await analyzeQ1TimelineWithCli(paths, settings);
      },
      loadQ1TimelineIr: loadQ1TimelineIrFromDisk,
      loadQ1TimelineDiagnostics: loadQ1TimelineDiagnosticsFromDisk,
      reportQ1TimelineWarning: (message) => {
        output.appendLine(message);
      },
      listExistingQ1asmFiles: listExistingQ1asmFilesFromDisk,
      publishDiagnostics: (paths, ir, existingFiles) => {
        publishVscodeDiagnostics({
          collection: diagnostics,
          diagnostics: computeDiagnostics(ir, { outputDir: paths.outputDir, existingFiles }),
          projectFile: paths.projectFile,
          outputDir: paths.outputDir,
        });
      },
      showPanel: (ir, paths) => {
        const changedProject = lastPaths?.projectFile !== paths.projectFile || settingsResourcePath(lastPaths) !== settingsResourcePath(paths) ||
          lastPaths?.schedulePath !== paths.schedulePath || lastPaths?.scheduleNotebookPath !== paths.scheduleNotebookPath ||
          Boolean(lastPrepareProject) !== Boolean(prepareProject);
        panel.show(ir, paths, revealPanel);
        lastPaths = paths;
        lastIr = ir;
        generatedSourceCurrent = true;
        lastPrepareProject = prepareProject;
        if (changedProject) autoRefresh?.update();
      },
      readSettings: async (projectFile, config) => {
        if (!config.scheduleNotebook) {
          const resource = vscode.Uri.file(config.scheduleFile
            ? resolve(dirname(projectFile), config.scheduleFile)
            : projectFile);
          onEnvironmentResource?.(resource);
          const settings = readSettings(resource);
          const environment = await ensureFilePythonEnvironment(vscode, resource, "qbs", output, { cwd: dirname(projectFile) });
          return environment ? {
            ...settings,
            ...environment,
            q1timelineCommand: environment.source === "vscode" ? null : settings.q1timelineCommand,
          } : undefined;
        }
        const notebookPath = config.sourceNotebook ?? config.scheduleNotebook;
        const resource = vscode.Uri.file(resolve(dirname(projectFile), notebookPath));
        onEnvironmentResource?.(resource);
        const settings = readSettings(resource);
        const environment = notebookEnvironment ?? await ensureNotebookEnvironment(
          vscode, resource, output,
        );
        if (disposed) return undefined;
        if (environment) await prepareProject?.run();
        return environment ? { ...settings, ...environment, q1timelineCommand: null } : undefined;
      },
      showOutput: () => output.show(true),
    });
  const runAnalyzeAndOpenForProject = async (
    projectFile?: string | Promise<string | undefined>,
    notebookEnvironment?: { pythonPath: string; pythonArgs: string[] },
    revealPanel = true,
    prepareProject?: ManagedNotebookPreparation,
    isCancelled?: () => boolean,
  ) => {
    if (disposed) return;
    const request = { project: typeof projectFile === "string" ? Promise.resolve(projectFile) : projectFile ?? resolveProjectWithVscode(undefined, () => disposed), prepareProject };
    const projectResult = request.project.then((projectFile) => ({ projectFile }), (error: unknown) => ({ error }));
    pendingAnalysis = request;
    let resource: vscodeTypes.Uri | undefined;
    const job = analysisQueue.then(async () => {
      try {
        if (disposed || isCancelled?.()) return;
        if (!notebookEnvironment) {
          const previousPrepare = prepareProject ?? lastPrepareProject;
          prepareProject = undefined;
          if (previousPrepare) {
            const result = await projectResult;
            if (disposed || isCancelled?.()) return;
            if ("projectFile" in result && result.projectFile && relative(result.projectFile, previousPrepare.projectFile) === "") {
              let config: ProjectConfigLite | undefined;
              try {
                config = await readProjectConfigFromDisk(result.projectFile);
              } catch (error) {
                if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
              }
              if (disposed || isCancelled?.()) return;
              if (!config || (config.scheduleNotebook &&
                  relative(resolve(dirname(result.projectFile), config.scheduleNotebook), previousPrepare.snapshotFile) === "" &&
                  config.sourceNotebook &&
                  relative(resolve(dirname(result.projectFile), config.sourceNotebook), previousPrepare.sourceNotebookPath) === "")) {
                prepareProject = previousPrepare;
              }
            }
          }
          request.prepareProject = prepareProject;
        }
        await prepareProject?.run();
        if (disposed || isCancelled?.()) return;
        const runner = createHandler(
          async () => {
            const result = await projectResult;
            if ("error" in result) throw result.error;
            return result.projectFile;
          },
          notebookEnvironment,
          (resolvedResource) => { resource = resolvedResource; },
          revealPanel,
          prepareProject,
        );
        await runner();
      } finally {
        if (pendingAnalysis === request) pendingAnalysis = undefined;
      }
    });
    analysisQueue = job.catch(() => undefined);
    try {
      await job;
    } catch (error) {
      if (disposed) return;
      const processError = error as NodeJS.ErrnoException;
      const missingPython = processError.code === "ENOENT" && processError.syscall?.startsWith("spawn");
      output.appendLine(String(error));
      if (!missingPython && !/No module named ['"]?(q1lens|qbstimeline|q1timeline)\b/.test(String(error))) {
        void vscode.window.showErrorMessage(`Q1Lens analysis failed: ${error instanceof Error ? error.message : String(error)}`);
        return;
      }
      const action = await vscode.window.showErrorMessage(
        "Q1Lens is unavailable in the selected Python environment.",
        "Check Python Environment",
      );
      if (disposed) return;
      if (action === "Check Python Environment") {
        const result = await projectResult;
        if (disposed) return;
        const retryTarget = "projectFile" in result && result.projectFile ? vscode.Uri.file(result.projectFile) : resource;
        await vscode.commands.executeCommand("q1lens.checkEnvironment", "qbsTimeline.analyzeAndOpen", resource, retryTarget);
      }
    }
  };
  analyzeHandler = (target) => {
    openRequestId += 1;
    notebookOpenEnvironmentCheck?.abort();
    return runAnalyzeAndOpenForProject(target ? resolveProjectWithVscode(target, () => disposed) : undefined);
  };
  refreshHandler = (isCancelled) => runAnalyzeAndOpenForProject(pendingAnalysis?.project ?? lastPaths?.projectFile, undefined, false,
    pendingAnalysis ? pendingAnalysis.prepareProject : lastPrepareProject, isCancelled);

  const openNotebookTimelineFromCell = async (cellArg?: unknown) => {
    if (disposed) return;
    let notebookContext = resolveNotebookCellContext(vscode, cellArg);
    if (!notebookContext || notebookContext.notebook.isUntitled) {
      await vscode.window.showWarningMessage("Open a saved notebook code cell before opening Q1Lens.");
      return;
    }
    const requestId = ++openRequestId;
    notebookOpenEnvironmentCheck?.abort();
    const environmentCheck = new AbortController();
    notebookOpenEnvironmentCheck = environmentCheck;
    let inputCancellation: vscodeTypes.CancellationTokenSource | undefined;
    const abortInput = () => inputCancellation?.cancel();
    environmentCheck.signal.addEventListener("abort", abortInput, { once: true });
    try {
      const selectedCell = notebookContext.notebook.cellAt(notebookContext.selectedCellIndex);
      const environment = await ensureNotebookEnvironment(vscode, notebookContext.notebook.uri, output, undefined, environmentCheck.signal);
      if (disposed || requestId !== openRequestId || !environment) return;
      notebookContext = notebookContext.notebook.isClosed || !notebookContext.notebook.getCells().includes(selectedCell)
        ? undefined
        : resolveNotebookCellContext(vscode, selectedCell);
      if (!notebookContext) {
        await vscode.window.showWarningMessage("The original notebook cell is no longer available. Open Q1Lens again from a saved notebook code cell.");
        return;
      }
      inputCancellation = new vscode.CancellationTokenSource();
      const defaults = inferNotebookTimelineVariables(notebookContext.cells, notebookContext.selectedCellIndex);
      const scheduleVariable = await vscode.window.showInputBox({
        title: "Schedule variable",
        prompt: "Name of the notebook variable containing the Qblox schedule.",
        value: defaults.scheduleVariable,
        ignoreFocusOut: true,
      }, inputCancellation.token);
      if (disposed || requestId !== openRequestId || !scheduleVariable) {
        return;
      }
      const compilerVariable = await vscode.window.showInputBox({
        title: "Compiler variable",
        prompt: "Name of the notebook variable containing the compiler or hardware agent.",
        value: defaults.compilerVariable,
        ignoreFocusOut: true,
      }, inputCancellation.token);
      if (disposed || requestId !== openRequestId || !compilerVariable) {
        return;
      }
      notebookContext = notebookContext.notebook.isClosed || !notebookContext.notebook.getCells().includes(selectedCell)
        ? undefined
        : resolveNotebookCellContext(vscode, selectedCell);
      if (!notebookContext) {
        await vscode.window.showWarningMessage("The original notebook cell is no longer available. Open Q1Lens again from a saved notebook code cell.");
        return;
      }
      const projectInput = {
        notebookPath: notebookContext.notebookPath,
        selectedCellIndex: notebookContext.selectedCellIndex,
        scheduleVariable,
        compilerVariable,
        cells: notebookContext.cells,
      };
      const project = createManagedNotebookTimelineProject(projectInput);
      managedArtifactFiles.add(project.projectFile);
      managedArtifactFiles.add(project.snapshotFile);
      let projectInitialized = false;
      const prepareProject = async () => {
        if (disposed) return;
        const current = selectedCell.notebook.isClosed || !selectedCell.notebook.getCells().includes(selectedCell)
          ? undefined : resolveNotebookCellContext(vscode, selectedCell);
        if (!current) throw new Error("The original notebook cell is no longer available. Open Q1Lens again from a saved notebook code cell.");
        const snapshot = createManagedNotebookTimelineProject({
          ...projectInput, selectedCellIndex: current.selectedCellIndex, cells: current.cells,
        });
        await vscode.workspace.fs.createDirectory(vscode.Uri.file(snapshot.projectDir));
        if (disposed) return;
        await vscode.workspace.fs.writeFile(vscode.Uri.file(snapshot.snapshotFile), Buffer.from(snapshot.snapshotJson, "utf8"));
        if (disposed) return;
        let initializeProject = !projectInitialized;
        if (!initializeProject) {
          try {
            await access(snapshot.projectFile);
          } catch (error) {
            if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
            initializeProject = true;
          }
          if (disposed) return;
        }
        if (initializeProject) {
          await vscode.workspace.fs.writeFile(vscode.Uri.file(snapshot.projectFile), Buffer.from(snapshot.projectYaml, "utf8"));
          projectInitialized = true;
        }
      };
      return runAnalyzeAndOpenForProject(project.projectFile, environment, true, {
        projectFile: project.projectFile,
        snapshotFile: project.snapshotFile,
        sourceNotebookPath: projectInput.notebookPath,
        run: prepareProject,
      });
    } finally {
      environmentCheck.signal.removeEventListener("abort", abortInput);
      inputCancellation?.dispose();
      if (notebookOpenEnvironmentCheck === environmentCheck) notebookOpenEnvironmentCheck = undefined;
    }
  };

  const markNotebookCell = async (tag: string, cellArg?: unknown) => {
    const notebookContext = resolveNotebookCellContext(vscode, cellArg);
    if (!notebookContext) {
      await vscode.window.showWarningMessage("Open a saved notebook code cell before marking it for Q1Lens.");
      return;
    }
    if (!vscode.WorkspaceEdit || !vscode.NotebookEdit?.updateCellMetadata || !vscode.workspace.applyEdit) {
      await vscode.window.showWarningMessage("This VS Code version cannot edit notebook cell metadata.");
      return;
    }
    const notebookCells = notebookCellsFromDocument(vscode, notebookContext.notebook);
    const edits = notebookCells.map((cell, index) => {
      const withoutSchedule = tag === QBS_NOTEBOOK_SCHEDULE_TAG
        ? withoutNotebookCellTag(cell.metadata, QBS_NOTEBOOK_SCHEDULE_TAG)
        : (cell.metadata ?? {});
      const metadata = index === notebookContext.selectedCellIndex
        ? withNotebookCellTag(withoutSchedule, tag)
        : withoutSchedule;
      const apiMetadata = notebookContext.notebook.cellAt(index).metadata;
      const updatedMetadata = isRecord(apiMetadata?.custom)
        ? { ...apiMetadata, custom: { ...apiMetadata.custom, metadata } }
        : isRecord(apiMetadata?.metadata) || typeof apiMetadata?.id === "string"
          ? { ...apiMetadata, metadata }
          : metadata;
      return vscode.NotebookEdit.updateCellMetadata(index, updatedMetadata);
    });
    const edit = new vscode.WorkspaceEdit();
    edit.set(notebookContext.notebook.uri, edits);
    await vscode.workspace.applyEdit(edit);
    await vscode.window.showInformationMessage(
      tag === QBS_NOTEBOOK_SETUP_TAG ? "Marked cell as QBS setup." : "Marked cell as QBS schedule.",
    );
  };

  context.subscriptions.push(output, diagnostics);
  context.subscriptions.push(vscode.commands.registerCommand("qbsTimeline.analyzeAndOpen", analyzeHandler));
  context.subscriptions.push(vscode.commands.registerCommand("qbsTimeline.refresh", () => refreshHandler()));
  context.subscriptions.push(
    vscode.commands.registerCommand("qbsTimeline.openIr", async () => {
      if (!(await ensureAnalysis())) {
        return;
      }
      await openFileAtLine(lastPaths!.irPath, 1);
    }),
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("qbsTimeline.openRenderedHtml", async () => {
      if (!(await ensureAnalysis())) {
        return;
      }
      await vscode.env.openExternal(vscode.Uri.file(lastPaths!.htmlPath));
    }),
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("qbsTimeline.openQ1asmFolder", async () => {
      if (!(await ensureAnalysis())) {
        return;
      }
      await vscode.commands.executeCommand("revealFileInOS", vscode.Uri.file(lastPaths!.q1asmDir));
    }),
  );
  context.subscriptions.push(vscode.commands.registerCommand("qbsTimeline.openQ1Timeline", async () => openQ1TimelineSelection({})));
  context.subscriptions.push(
    vscode.commands.registerCommand("qbsTimeline.openCurrentFolderQ1Timeline", async () => {
      const folder = currentFolderForQ1asmSelection(vscode);
      if (!folder) {
        await vscode.window.showWarningMessage("Open a folder or file before opening current-folder Q1ASM.");
        return;
      }
      const q1asmFile = await firstQ1asmFileInFolder(folder);
      if (!q1asmFile) {
        await vscode.window.showWarningMessage("No Q1ASM files found in the current folder.");
        return;
      }
      await vscode.commands.executeCommand("q1timeline.openQ1asmFilesInFolder", vscode.Uri.file(q1asmFile));
    }),
  );
  context.subscriptions.push(vscode.commands.registerCommand("qbsTimeline.openNotebookTimelineFromCell", openNotebookTimelineFromCell));
  context.subscriptions.push(
    vscode.commands.registerCommand("qbsTimeline.markNotebookSetupCell", (cellArg?: unknown) => (
      markNotebookCell(QBS_NOTEBOOK_SETUP_TAG, cellArg)
    )),
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("qbsTimeline.markNotebookScheduleCell", (cellArg?: unknown) => (
      markNotebookCell(QBS_NOTEBOOK_SCHEDULE_TAG, cellArg)
    )),
  );

  const onDidChangeConfiguration = typeof vscode.workspace.onDidChangeConfiguration === "function"
    ? (listener: (event: AutoRefreshConfigurationEvent) => void) => vscode.workspace.onDidChangeConfiguration(listener)
    : () => ({ dispose: () => undefined });
  const autoRefreshResource = () => {
    const resource = settingsResourcePath(lastPaths);
    return resource ? vscode.Uri.file(resource) : undefined;
  };
  autoRefresh = registerQbsAutoRefresh({
    resource: autoRefreshResource,
    readEnabled: () => readSettings(autoRefreshResource()).autoRefresh,
    createWatcher: () => {
      const watchers: AutoRefreshWatcher[] = [vscode.workspace.createFileSystemWatcher(QBS_AUTO_REFRESH_GLOB)];
      if (!lastPaths) return watchers;
      const externalProject = !vscode.workspace.getWorkspaceFolder(vscode.Uri.file(lastPaths.projectDir));
      if (externalProject) watchers.push(vscode.workspace.createFileSystemWatcher(
        new vscode.RelativePattern(vscode.Uri.file(lastPaths.projectDir), QBS_AUTO_REFRESH_GLOB),
      ));
      const sources = lastPrepareProject
        ? [lastPaths.sourceNotebookPath ?? lastPaths.scheduleNotebookPath]
        : [lastPaths.schedulePath, lastPaths.scheduleNotebookPath];
      for (const file of sources) {
        if (!file) continue;
        const coveredByGlob = /\.(?:py|ipynb)$/.test(file) || /(?:^|[\\/])qbstimeline\.ya?ml$/.test(file);
        if (coveredByGlob && (vscode.workspace.getWorkspaceFolder(vscode.Uri.file(file)) ||
            (externalProject && isWatchedPathForCurrentProject({ changedFile: file, projectDir: lastPaths.projectDir })))) continue;
        watchers.push(createSourceWatcher(vscode, file));
      }
      return watchers;
    },
    onDidChangeConfiguration,
    shouldRefresh: (uri) => {
      if (!lastPaths) return false;
      if (lastPrepareProject) {
        const source = lastPaths.sourceNotebookPath ?? lastPaths.scheduleNotebookPath;
        if (source && relative(source, uri.fsPath) === "") return true;
      }
      if ([...managedArtifactFiles].some((file) => relative(file, uri.fsPath) === "")) return false;
      if (lastPrepareProject) return false;
      if ([lastPaths.schedulePath, lastPaths.scheduleNotebookPath].some((file) => file && relative(file, uri.fsPath) === "")) return true;
      return isWatchedPathForCurrentProject({ changedFile: uri.fsPath, projectDir: lastPaths.projectDir });
    },
    run: refreshHandler,
    delayMs: 750,
  });
  context.subscriptions.push(autoRefresh);
}
