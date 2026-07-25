import { access, mkdir, readdir, readFile, rm } from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { computeDiagnostics } from "./diagnosticsCore";
import { buildAnalyzeInvocation, buildQ1TimelineAnalyzeInvocation, buildRenderInvocation, runProcessWithSpawn } from "./qbsCli";
import { OutputPaths, ProjectConfigLite, chooseProjectFile, deriveOutputPaths, parseProjectConfigLite } from "./projectDiscovery";
import { parseQbsIrText, Q1TimelineDiagnostic, QbsIr } from "./qbsIr";
import { QbsTimelineSettings, readSettings } from "./settings";
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

export interface AnalyzeAndOpenDeps {
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
  readSettings: () => QbsTimelineSettings;
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
  | { kind: "notebook"; file: string; cellIndex: number; cellLine?: number };

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
  return rel === "" || (!!rel && !rel.startsWith("..") && !isAbsolute(rel));
}

export async function openSourceTarget(target: SourceTarget): Promise<void> {
  if (target.kind === "notebook") {
    await openNotebookAtCell(target.file, target.cellIndex, target.cellLine);
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

export function createAnalyzeAndOpenHandler(deps: AnalyzeAndOpenDeps): () => Promise<void> {
  return async () => {
    const projectFile = await deps.resolveProject();
    if (!projectFile) {
      return;
    }

    const settings = deps.readSettings();
    if (settings.revealOutputChannel) {
      deps.showOutput();
    }

    const config = await deps.readProjectConfig(projectFile);
    const paths = deps.derivePaths({
      projectFile,
      scheduleFile: config.scheduleFile,
      scheduleNotebook: config.scheduleNotebook,
      sourceNotebook: config.sourceNotebook,
      outputDir: config.outputDir,
      overrideOutputDir: settings.outputDirOverride,
    });

    await deps.analyze(paths, settings);
    await deps.render(paths, settings);
    const ir = await deps.loadIr(paths);
    if (deps.analyzeQ1Timeline && deps.loadQ1TimelineIr && ir.q1asm_programs.length > 0) {
      try {
        const analyzed = await deps.analyzeQ1Timeline(paths, settings);
        if (analyzed !== false) {
          const q1timelineIr = await deps.loadQ1TimelineIr(paths);
          if (q1timelineIr) {
            ir.q1timeline_ir = q1timelineIr;
          }
          if (deps.loadQ1TimelineDiagnostics) {
            ir.q1timeline_diagnostics = await deps.loadQ1TimelineDiagnostics(paths);
          }
        }
      } catch (error) {
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
  });
  const result = await runProcessWithSpawn(invocation.command, invocation.args, { cwd: paths.projectDir, timeoutMs: QBS_SUBPROCESS_TIMEOUT_MS });
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

async function resolveProjectWithVscode(): Promise<string | undefined> {
  const vscode = require("vscode") as typeof vscodeTypes;
  const activeFile = vscode.window.activeTextEditor?.document.uri.fsPath ?? null;
  const discovered = await vscode.workspace.findFiles("**/qbstimeline.y{ml,aml}", "**/.qbs_timeline/**");
  return chooseProjectFile({
    activeFile,
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

function notebookCellsFromDocument(vscode: any, notebook: any): NotebookTimelineCell[] {
  const cells = typeof notebook.getCells === "function"
    ? notebook.getCells()
    : Array.from({ length: Number(notebook.cellCount || 0) }, (_value, index) => notebook.cellAt(index));
  return cells.map((cell: any) => ({
    kind: cell.kind === vscode.NotebookCellKind?.Markup ? "markdown" : "code",
    text: typeof cell.document?.getText === "function" ? cell.document.getText() : "",
    metadata: isRecord(cell.metadata) ? { ...cell.metadata } : {},
  }));
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
  let lastPaths: OutputPaths | undefined;
  let lastIr: QbsIr | undefined;
  let handler: () => Promise<void>;
  const openQ1TimelineSelection = async (selection: Q1TimelineSelection) => {
    if (!lastPaths || !lastIr) {
      await vscode.window.showWarningMessage("Run Q1Lens: Analyze and Open before opening the Q1ASM timeline.");
      return;
    }
    try {
      const target = resolveQbsSelectionToQ1TimelineTarget({ ir: lastIr, selection, outputDir: lastPaths.outputDir });
      await deps.openQ1TimelineTarget(target);
    } catch (error) {
      output.appendLine(`q1timeline target failed: ${String(error)}`);
      await vscode.window.showWarningMessage("Q1ASM timeline target failed. Check the Q1Lens output for details.");
    }
  };
  const panel = new TimelinePanel(context.extensionUri, {
    onRefresh: async () => {
      await handler();
    },
    onOpenQ1Timeline: async (message) => {
      await openQ1TimelineSelection(message);
    },
    onOpenQ1AsmSource: async (message) => {
      if (!lastPaths || !lastIr) {
        await vscode.window.showWarningMessage("Run Q1Lens: Analyze and Open before opening Q1ASM source.");
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

  const createHandler = (resolveProject: () => Promise<string | undefined>) => createAnalyzeAndOpenHandler({
      resolveProject,
      readProjectConfig: readProjectConfigFromDisk,
      derivePaths: (input) => {
        return deriveOutputPaths(input);
      },
      analyze: async (paths, settings) => {
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
        lastPaths = paths;
        lastIr = ir;
        panel.show(ir, paths);
      },
      readSettings,
      showOutput: () => output.show(true),
    });
  const runAnalyzeAndOpenForProject = async (projectFile?: string) => {
    const runner = createHandler(projectFile ? async () => projectFile : resolveProjectWithVscode);
    await runner();
  };
  handler = () => runAnalyzeAndOpenForProject();

  const openNotebookTimelineFromCell = async (cellArg?: unknown) => {
    const notebookContext = resolveNotebookCellContext(vscode, cellArg);
    if (!notebookContext) {
      await vscode.window.showWarningMessage("Open a saved notebook code cell before opening Q1Lens.");
      return;
    }
    const defaults = inferNotebookTimelineVariables(notebookContext.cells, notebookContext.selectedCellIndex);
    const scheduleVariable = await vscode.window.showInputBox({
      title: "Schedule variable",
      prompt: "Name of the notebook variable containing the Qblox schedule.",
      value: defaults.scheduleVariable,
      ignoreFocusOut: true,
    });
    if (!scheduleVariable) {
      return;
    }
    const compilerVariable = await vscode.window.showInputBox({
      title: "Compiler variable",
      prompt: "Name of the notebook variable containing the compiler or hardware agent.",
      value: defaults.compilerVariable,
      ignoreFocusOut: true,
    });
    if (!compilerVariable) {
      return;
    }
    const project = createManagedNotebookTimelineProject({
      notebookPath: notebookContext.notebookPath,
      selectedCellIndex: notebookContext.selectedCellIndex,
      scheduleVariable,
      compilerVariable,
      cells: notebookContext.cells,
    });
    await vscode.workspace.fs.createDirectory(vscode.Uri.file(project.projectDir));
    await vscode.workspace.fs.writeFile(vscode.Uri.file(project.snapshotFile), Buffer.from(project.snapshotJson, "utf8"));
    await vscode.workspace.fs.writeFile(vscode.Uri.file(project.projectFile), Buffer.from(project.projectYaml, "utf8"));
    await runAnalyzeAndOpenForProject(project.projectFile);
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
      return vscode.NotebookEdit.updateCellMetadata(index, metadata);
    });
    const edit = new vscode.WorkspaceEdit();
    edit.set(notebookContext.notebook.uri, edits);
    await vscode.workspace.applyEdit(edit);
    await vscode.window.showInformationMessage(
      tag === QBS_NOTEBOOK_SETUP_TAG ? "Marked cell as QBS setup." : "Marked cell as QBS schedule.",
    );
  };

  context.subscriptions.push(output, diagnostics);
  context.subscriptions.push(vscode.commands.registerCommand("qbsTimeline.analyzeAndOpen", handler));
  context.subscriptions.push(vscode.commands.registerCommand("qbsTimeline.refresh", handler));
  context.subscriptions.push(
    vscode.commands.registerCommand("qbsTimeline.openIr", async () => {
      if (lastPaths) {
        await openFileAtLine(lastPaths.irPath, 1);
      }
    }),
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("qbsTimeline.openRenderedHtml", async () => {
      if (lastPaths) {
        await vscode.env.openExternal(vscode.Uri.file(lastPaths.htmlPath));
      }
    }),
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("qbsTimeline.openQ1asmFolder", async () => {
      if (lastPaths) {
        await vscode.commands.executeCommand("revealFileInOS", vscode.Uri.file(lastPaths.q1asmDir));
      }
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

  if (readSettings().autoRefresh) {
    const queue = new DebouncedRefreshQueue(750, handler);
    const watcher = vscode.workspace.createFileSystemWatcher(QBS_AUTO_REFRESH_GLOB);
    const requestIfCurrentProject = (uri: vscodeTypes.Uri) => {
      if (lastPaths && isWatchedPathForCurrentProject({ changedFile: uri.fsPath, projectDir: lastPaths.projectDir })) {
        queue.request();
      }
    };
    watcher.onDidChange(requestIfCurrentProject);
    watcher.onDidCreate(requestIfCurrentProject);
    watcher.onDidDelete(requestIfCurrentProject);
    context.subscriptions.push(watcher, { dispose: () => queue.dispose() });
  }
}
