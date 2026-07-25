import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import Module = require("node:module");
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  QBS_AUTO_REFRESH_GLOB,
  analyzeQ1TimelineWithCli,
  createAnalyzeAndOpenHandler,
  isWatchedPathForCurrentProject,
  loadQ1TimelineDiagnosticsFromDisk,
  openSourceTarget,
  resolveQ1AsmSourceTarget,
  resolveScheduleSourceTarget,
} from "../src/qbs/commands";

describe("commands", () => {
  it("runs analyze, render, load, diagnostics, and Webview update in order", async () => {
    const calls: string[] = [];
    const handler = createAnalyzeAndOpenHandler({
      resolveProject: async () => "C:\\repo\\qbstimeline.yml",
      readProjectConfig: async () => ({ scheduleFile: "schedule.py", outputDir: ".qbs_timeline" }),
      derivePaths: () => ({
        projectFile: "C:\\repo\\qbstimeline.yml",
        projectDir: "C:\\repo",
        schedulePath: "C:\\repo\\schedule.py",
        outputDir: "C:\\repo\\.qbs_timeline",
        irPath: "C:\\repo\\.qbs_timeline\\qbs_ir.json",
        htmlPath: "C:\\repo\\.qbs_timeline\\index.html",
        q1asmDir: "C:\\repo\\.qbs_timeline\\q1asm",
      }),
      analyze: async () => {
        calls.push("analyze");
      },
      render: async () => {
        calls.push("render");
      },
      loadIr: async () => {
        calls.push("loadIr");
        return {
          schedule: { name: "unit" },
          operations: [],
          symbolic_values: [],
          symbolic_pulses: [],
          q1asm_programs: [],
          q1asm_provenance: [],
        };
      },
      listExistingQ1asmFiles: async () => new Set<string>(),
      publishDiagnostics: () => {
        calls.push("diagnostics");
      },
      showPanel: () => {
        calls.push("panel");
      },
      readSettings: () => ({
        pythonPath: "python",
        pythonArgs: [],
        autoRefresh: false,
        outputDirOverride: null,
        revealOutputChannel: false,
        q1timelineCommand: null,
      }),
      showOutput: () => {
        calls.push("output");
      },
    });

    await handler();

    assert.deepEqual(calls, ["analyze", "render", "loadIr", "diagnostics", "panel"]);
  });

  it("attaches q1timeline analyzer output before showing the Webview", async () => {
    const calls: string[] = [];
    let shownIr: any;
    const handler = createAnalyzeAndOpenHandler({
      resolveProject: async () => "C:\\repo\\qbstimeline.yml",
      readProjectConfig: async () => ({ scheduleFile: "schedule.py", outputDir: ".qbs_timeline" }),
      derivePaths: () => ({
        projectFile: "C:\\repo\\qbstimeline.yml",
        projectDir: "C:\\repo",
        schedulePath: "C:\\repo\\schedule.py",
        outputDir: "C:\\repo\\.qbs_timeline",
        irPath: "C:\\repo\\.qbs_timeline\\qbs_ir.json",
        htmlPath: "C:\\repo\\.qbs_timeline\\index.html",
        q1asmDir: "C:\\repo\\.qbs_timeline\\q1asm",
      }),
      analyze: async () => {
        calls.push("analyze");
      },
      render: async () => {
        calls.push("render");
      },
      loadIr: async () => {
        calls.push("loadIr");
        return {
          schedule: { name: "unit" },
          operations: [],
          symbolic_values: [],
          symbolic_pulses: [],
          q1asm_programs: [{ sequencer: "seq0", file: "q1asm/seq0.q1asm" }],
          q1asm_provenance: [],
        };
      },
      analyzeQ1Timeline: async () => {
        calls.push("analyzeQ1Timeline");
      },
      loadQ1TimelineIr: async () => {
        calls.push("loadQ1TimelineIr");
        return {
          events: [
            {
              id: "seq0:e0",
              sequencer_id: "seq0",
              kind: "play",
              source: { line: 4 },
              t0: { value: 20 },
              t1: { value: 40 },
              duration: { value: 20 },
            },
          ],
        };
      },
      listExistingQ1asmFiles: async () => new Set<string>(),
      publishDiagnostics: () => {
        calls.push("diagnostics");
      },
      showPanel: (ir) => {
        shownIr = ir;
        calls.push("panel");
      },
      readSettings: () => ({
        pythonPath: "python",
        pythonArgs: [],
        autoRefresh: false,
        outputDirOverride: null,
        revealOutputChannel: false,
        q1timelineCommand: null,
      }),
      showOutput: () => {
        calls.push("output");
      },
    });

    await handler();

    assert.deepEqual(calls, ["analyze", "render", "loadIr", "analyzeQ1Timeline", "loadQ1TimelineIr", "diagnostics", "panel"]);
    assert.equal(shownIr.q1timeline_ir.events[0].kind, "play");
  });

  it("attaches q1timeline diagnostics before publishing diagnostics", async () => {
    const calls: string[] = [];
    let diagnosticIr: any;
    const handler = createAnalyzeAndOpenHandler({
      resolveProject: async () => "C:\\repo\\qbstimeline.yml",
      readProjectConfig: async () => ({ scheduleFile: "schedule.py", outputDir: ".qbs_timeline" }),
      derivePaths: () => ({
        projectFile: "C:\\repo\\qbstimeline.yml",
        projectDir: "C:\\repo",
        schedulePath: "C:\\repo\\schedule.py",
        outputDir: "C:\\repo\\.qbs_timeline",
        irPath: "C:\\repo\\.qbs_timeline\\qbs_ir.json",
        htmlPath: "C:\\repo\\.qbs_timeline\\index.html",
        q1asmDir: "C:\\repo\\.qbs_timeline\\q1asm",
      }),
      analyze: async () => {
        calls.push("analyze");
      },
      render: async () => {
        calls.push("render");
      },
      loadIr: async () => ({
        schedule: { name: "unit" },
        operations: [],
        symbolic_values: [],
        symbolic_pulses: [],
        q1asm_programs: [{ sequencer: "seq0", file: "q1asm/seq0.q1asm" }],
        q1asm_provenance: [],
      }),
      analyzeQ1Timeline: async () => {
        calls.push("analyzeQ1Timeline");
      },
      loadQ1TimelineIr: async () => ({ events: [] }),
      loadQ1TimelineDiagnostics: async () => [
        {
          category: "possible_underflow",
          message: "possible_underflow: slack = -4 ns.",
          severity: "warning",
          source: { file: "C:\\repo\\.qbs_timeline\\q1asm\\seq0.q1asm", line: 2 },
        },
      ],
      listExistingQ1asmFiles: async () => new Set<string>(["q1asm/seq0.q1asm"]),
      publishDiagnostics: (_paths, ir) => {
        diagnosticIr = ir;
        calls.push("diagnostics");
      },
      showPanel: () => {
        calls.push("panel");
      },
      readSettings: () => ({
        pythonPath: "python",
        pythonArgs: [],
        autoRefresh: false,
        outputDirOverride: null,
        revealOutputChannel: false,
        q1timelineCommand: null,
      }),
      showOutput: () => {
        calls.push("output");
      },
    });

    await handler();

    assert.deepEqual(calls, ["analyze", "render", "analyzeQ1Timeline", "diagnostics", "panel"]);
    assert.equal(diagnosticIr.q1timeline_diagnostics[0].category, "possible_underflow");
  });

  it("turns malformed inline q1timeline diagnostics JSON into a published diagnostic", async () => {
    let diagnosticIr: any;
    const handler = createAnalyzeAndOpenHandler({
      resolveProject: async () => "C:\\repo\\qbstimeline.yml",
      readProjectConfig: async () => ({ scheduleFile: "schedule.py", outputDir: ".qbs_timeline" }),
      derivePaths: () => ({
        projectFile: "C:\\repo\\qbstimeline.yml",
        projectDir: "C:\\repo",
        schedulePath: "C:\\repo\\schedule.py",
        outputDir: "C:\\repo\\.qbs_timeline",
        irPath: "C:\\repo\\.qbs_timeline\\qbs_ir.json",
        htmlPath: "C:\\repo\\.qbs_timeline\\index.html",
        q1asmDir: "C:\\repo\\.qbs_timeline\\q1asm",
      }),
      analyze: async () => {},
      render: async () => {},
      loadIr: async () => ({
        schedule: { name: "unit" },
        operations: [],
        symbolic_values: [],
        symbolic_pulses: [],
        q1asm_programs: [{ sequencer: "seq0", file: "q1asm/seq0.q1asm" }],
        q1asm_provenance: [],
      }),
      analyzeQ1Timeline: async () => {},
      loadQ1TimelineIr: async () => ({ events: [] }),
      loadQ1TimelineDiagnostics: async () => {
        throw new SyntaxError("Unexpected token");
      },
      listExistingQ1asmFiles: async () => new Set<string>(["q1asm/seq0.q1asm"]),
      publishDiagnostics: (_paths, ir) => {
        diagnosticIr = ir;
      },
      showPanel: () => {},
      readSettings: () => ({
        pythonPath: "python",
        pythonArgs: [],
        autoRefresh: false,
        outputDirOverride: null,
        revealOutputChannel: false,
        q1timelineCommand: null,
      }),
      showOutput: () => {},
    });

    await handler();

    assert.equal(diagnosticIr.q1timeline_diagnostics[0].category, "invalid_analyzer_json");
  });

  it("turns non-array inline q1timeline diagnostics JSON into a fallback diagnostic", async () => {
    const outputDir = mkdtempSync(join(tmpdir(), "qbs-inline-q1-"));
    mkdirSync(join(outputDir, ".q1timeline"));
    writeFileSync(join(outputDir, ".q1timeline", "diagnostics.json"), "{\"message\":\"schema drift\"}", "utf8");

    const diagnostics = await loadQ1TimelineDiagnosticsFromDisk({
      projectFile: join(outputDir, "qbstimeline.yml"),
      projectDir: outputDir,
      schedulePath: join(outputDir, "schedule.py"),
      outputDir,
      irPath: join(outputDir, "qbs_ir.json"),
      htmlPath: join(outputDir, "index.html"),
      q1asmDir: join(outputDir, "q1asm"),
    });

    assert.equal(diagnostics[0].category, "invalid_analyzer_json");
  });

  it("removes stale inline q1timeline outputs before analyzer execution", async () => {
    const outputDir = mkdtempSync(join(tmpdir(), "qbs-inline-stale-"));
    const q1timelineDir = join(outputDir, ".q1timeline");
    mkdirSync(q1timelineDir);
    writeFileSync(join(outputDir, "q1timeline.yml"), "sequencers: []\n", "utf8");
    writeFileSync(join(q1timelineDir, "timeline_ir.json"), "{\"events\":[{\"id\":\"stale\"}]}", "utf8");
    writeFileSync(join(q1timelineDir, "diagnostics.json"), "[]", "utf8");
    const scriptPath = join(outputDir, "noop-analyzer.js");
    writeFileSync(scriptPath, "process.exit(0);\n", "utf8");

    await analyzeQ1TimelineWithCli(
      {
        projectFile: join(outputDir, "qbstimeline.yml"),
        projectDir: outputDir,
        schedulePath: join(outputDir, "schedule.py"),
        outputDir,
        irPath: join(outputDir, "qbs_ir.json"),
        htmlPath: join(outputDir, "index.html"),
        q1asmDir: join(outputDir, "q1asm"),
      },
      {
        pythonPath: process.execPath,
        pythonArgs: [scriptPath],
        autoRefresh: false,
        outputDirOverride: null,
        revealOutputChannel: false,
        q1timelineCommand: null,
      },
    );

    assert.equal(existsSync(join(q1timelineDir, "timeline_ir.json")), false);
    assert.equal(existsSync(join(q1timelineDir, "diagnostics.json")), false);
  });

  it("watches notebook schedules for QBS auto-refresh", () => {
    assert.equal(QBS_AUTO_REFRESH_GLOB.includes("*.ipynb"), true);
  });

  it("skips q1timeline inline analysis when the QBS IR has no Q1ASM programs", async () => {
    const calls: string[] = [];
    const warnings: string[] = [];
    const handler = createAnalyzeAndOpenHandler({
      resolveProject: async () => "C:\\repo\\qbstimeline.yml",
      readProjectConfig: async () => ({ scheduleFile: "schedule.py", outputDir: ".qbs_timeline" }),
      derivePaths: () => ({
        projectFile: "C:\\repo\\qbstimeline.yml",
        projectDir: "C:\\repo",
        schedulePath: "C:\\repo\\schedule.py",
        outputDir: "C:\\repo\\.qbs_timeline",
        irPath: "C:\\repo\\.qbs_timeline\\qbs_ir.json",
        htmlPath: "C:\\repo\\.qbs_timeline\\index.html",
        q1asmDir: "C:\\repo\\.qbs_timeline\\q1asm",
      }),
      analyze: async () => {
        calls.push("analyze");
      },
      render: async () => {
        calls.push("render");
      },
      loadIr: async () => {
        calls.push("loadIr");
        return {
          schedule: { name: "unit" },
          operations: [],
          symbolic_values: [],
          symbolic_pulses: [],
          q1asm_programs: [],
          q1asm_provenance: [],
        };
      },
      analyzeQ1Timeline: async () => {
        calls.push("analyzeQ1Timeline");
      },
      loadQ1TimelineIr: async () => {
        calls.push("loadQ1TimelineIr");
        return { events: [] };
      },
      listExistingQ1asmFiles: async () => new Set<string>(),
      publishDiagnostics: () => {
        calls.push("diagnostics");
      },
      showPanel: () => {
        calls.push("panel");
      },
      readSettings: () => ({
        pythonPath: "python",
        pythonArgs: [],
        autoRefresh: false,
        outputDirOverride: null,
        revealOutputChannel: false,
        q1timelineCommand: null,
      }),
      showOutput: () => {
        calls.push("output");
      },
      reportQ1TimelineWarning: (message) => {
        warnings.push(message);
      },
    });

    await handler();

    assert.deepEqual(calls, ["analyze", "render", "loadIr", "diagnostics", "panel"]);
    assert.deepEqual(warnings, []);
  });

  it("does not attach stale q1timeline IR when inline analysis is skipped", async () => {
    const calls: string[] = [];
    let shownIr: any;
    const handler = createAnalyzeAndOpenHandler({
      resolveProject: async () => "C:\\repo\\qbstimeline.yml",
      readProjectConfig: async () => ({ scheduleFile: "schedule.py", outputDir: ".qbs_timeline" }),
      derivePaths: () => ({
        projectFile: "C:\\repo\\qbstimeline.yml",
        projectDir: "C:\\repo",
        schedulePath: "C:\\repo\\schedule.py",
        outputDir: "C:\\repo\\.qbs_timeline",
        irPath: "C:\\repo\\.qbs_timeline\\qbs_ir.json",
        htmlPath: "C:\\repo\\.qbs_timeline\\index.html",
        q1asmDir: "C:\\repo\\.qbs_timeline\\q1asm",
      }),
      analyze: async () => {
        calls.push("analyze");
      },
      render: async () => {
        calls.push("render");
      },
      loadIr: async () => ({
        schedule: { name: "unit" },
        operations: [],
        symbolic_values: [],
        symbolic_pulses: [],
        q1asm_programs: [{ sequencer: "seq0", file: "q1asm/seq0.q1asm" }],
        q1asm_provenance: [],
      }),
      analyzeQ1Timeline: async () => {
        calls.push("analyzeQ1Timeline");
        return false;
      },
      loadQ1TimelineIr: async () => {
        calls.push("loadQ1TimelineIr");
        return { events: [{ kind: "stale" }] };
      },
      listExistingQ1asmFiles: async () => new Set<string>(),
      publishDiagnostics: () => {
        calls.push("diagnostics");
      },
      showPanel: (ir) => {
        shownIr = ir;
        calls.push("panel");
      },
      readSettings: () => ({
        pythonPath: "python",
        pythonArgs: [],
        autoRefresh: false,
        outputDirOverride: null,
        revealOutputChannel: false,
        q1timelineCommand: null,
      }),
      showOutput: () => {
        calls.push("output");
      },
    });

    await handler();

    assert.deepEqual(calls, ["analyze", "render", "analyzeQ1Timeline", "diagnostics", "panel"]);
    assert.equal(shownIr.q1timeline_ir, undefined);
  });

  it("resolves generated Q1ASM source targets by sequencer", () => {
    const target = resolveQ1AsmSourceTarget({
      ir: {
        schedule: { name: "unit" },
        operations: [],
        symbolic_values: [],
        symbolic_pulses: [],
        q1asm_programs: [{ sequencer: "cluster0_module4_seq0", file: "q1asm/cluster0_module4_seq0.q1asm" }],
        q1asm_provenance: [],
      },
      outputDir: "C:\\repo\\.qbs_timeline",
      sequencer: "cluster0_module4_seq0",
      line: 7,
    });

    assert.equal(target.file.replace(/\\/g, "/"), "C:/repo/.qbs_timeline/q1asm/cluster0_module4_seq0.q1asm");
    assert.equal(target.line, 7);
  });

  it("resolves schedule source targets from source_map and falls back to line 1", () => {
    const ir: any = {
      schedule: { name: "unit" },
      operations: [],
      symbolic_values: [],
      symbolic_pulses: [],
      q1asm_programs: [],
      q1asm_provenance: [],
      source_map: {
        schedulables: {
          measure: { file: "schedule.py", line: 21, column: 4, label: "measure" },
        },
      },
    };

    assert.deepEqual(
      resolveScheduleSourceTarget({
        ir,
        scheduleFile: "C:\\repo\\schedule.py",
        selection: { schedulableId: "measure", operationId: "measure_q0", blockId: "pulse:measure:pulse:0" },
      }),
      { kind: "file", file: "C:\\repo\\schedule.py", line: 21 },
    );
    assert.deepEqual(
      resolveScheduleSourceTarget({
        ir,
        scheduleFile: "C:\\repo\\schedule.py",
        selection: { schedulableId: "missing" },
      }),
      { kind: "file", file: "C:\\repo\\schedule.py", line: 1 },
    );
  });

  it("prefers exact block source-map entries over broad operation entries", () => {
    const target = resolveScheduleSourceTarget({
      ir: {
        schedule: { name: "unit" },
        operations: [],
        symbolic_values: [],
        symbolic_pulses: [],
        q1asm_programs: [],
        q1asm_provenance: [],
        source_map: {
          schedulables: {
            measure_q0: { file: "schedule.py", line: 10, column: 4 },
            "acq:measure:acquisition:0": { file: "schedule.py", line: 42, column: 8 },
          },
        },
      },
      scheduleFile: "C:\\repo\\schedule.py",
      selection: { operationId: "measure_q0", blockId: "acq:measure:acquisition:0" },
    });

    assert.deepEqual(target, { kind: "file", file: "C:\\repo\\schedule.py", line: 42 });
  });

  it("resolves non-notebook schedule source targets to the mapped source file", () => {
    const target = resolveScheduleSourceTarget({
      ir: {
        schedule: { name: "unit" },
        operations: [],
        symbolic_values: [],
        symbolic_pulses: [],
        q1asm_programs: [],
        q1asm_provenance: [],
        source_map: {
          schedulables: {
            measure: { file: "helpers.py", line: 8, column: 4 },
          },
        },
      },
      projectDir: "C:\\repo",
      scheduleFile: "C:\\repo\\schedule.py",
      selection: { schedulableId: "measure" },
    });

    assert.deepEqual(target, { kind: "file", file: "C:\\repo\\helpers.py", line: 8 });
  });

  it("resolves notebook schedule source target from source map", () => {
    const target = resolveScheduleSourceTarget({
      ir: {
        operations: [],
        symbolic_values: [],
        symbolic_pulses: [],
        q1asm_programs: [],
        q1asm_provenance: [],
        source_map: {
          primary: { kind: "notebook", file: "examples/050.ipynb" },
          schedulables: {
            measure: {
              kind: "notebook",
              file: "examples/050.ipynb",
              notebook: { file: "examples/050.ipynb", cell_index: 12, cell_line: 4 },
            },
          },
        },
      },
      scheduleFile: "C:\\repo\\.scratch\\schedule.py",
      selection: { schedulableId: "measure" },
    });

    assert.deepEqual(target, {
      kind: "notebook",
      file: "examples/050.ipynb",
      cellIndex: 12,
      cellLine: 4,
    });
  });

  it("resolves relative notebook source targets against the project directory", () => {
    const target = resolveScheduleSourceTarget({
      ir: {
        operations: [],
        symbolic_values: [],
        symbolic_pulses: [],
        q1asm_programs: [],
        q1asm_provenance: [],
        source_map: {
          primary: { kind: "notebook", file: "examples/050.ipynb" },
          schedulables: {
            measure: {
              kind: "notebook",
              file: "examples/050.ipynb",
              notebook: { file: "examples/050.ipynb", cell_index: 12, cell_line: 4 },
            },
          },
        },
      },
      projectDir: "C:\\repo",
      scheduleFile: "C:\\repo\\.scratch\\schedule.py",
      selection: { schedulableId: "measure" },
    });

    assert.deepEqual(target, {
      kind: "notebook",
      file: "C:\\repo\\examples\\050.ipynb",
      cellIndex: 12,
      cellLine: 4,
    });
  });

  it("opens notebook source targets at the mapped cell line", async () => {
    const shownTextSelections: Array<{ line: number; character: number }> = [];
    const originalLoad = (Module as any)._load;
    (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
      if (request === "vscode") {
        return {
          Uri: { file: (fsPath: string) => ({ fsPath }) },
          ViewColumn: { Beside: 2 },
          NotebookRange: class {
            constructor(public start: number, public end: number) {}
          },
          Range: class {
            start: any;
            constructor(public startLine: number, public startColumn: number) {
              this.start = { line: startLine, character: startColumn };
            }
          },
          Selection: class {
            constructor(public start: any, public end: any) {}
          },
          workspace: {
            openNotebookDocument: async () => ({
              cellAt: () => ({ document: { uri: { fsPath: "vscode-notebook-cell://cell" } } }),
            }),
          },
          window: {
            showNotebookDocument: async () => ({}),
            showTextDocument: async () => ({
              set selection(value: any) {
                shownTextSelections.push(value.start);
              },
              revealRange: () => undefined,
            }),
          },
          TextEditorRevealType: { InCenter: 1 },
        };
      }
      return originalLoad.call(this, request, parent, isMain);
    };
    try {
      await openSourceTarget({ kind: "notebook", file: "C:\\repo\\notebook.ipynb", cellIndex: 12, cellLine: 4 });
    } finally {
      (Module as any)._load = originalLoad;
    }

    assert.deepEqual(shownTextSelections[0], { line: 3, character: 0 });
  });

  it("falls back to primary notebook when no exact source map match exists", () => {
    const target = resolveScheduleSourceTarget({
      ir: {
        operations: [],
        symbolic_values: [],
        symbolic_pulses: [],
        q1asm_programs: [],
        q1asm_provenance: [],
        source_map: {
          primary: { kind: "notebook", file: "examples/050.ipynb" },
          schedulables: {},
        },
      },
      scheduleFile: "C:\\repo\\.scratch\\schedule.py",
      selection: { schedulableId: "missing" },
    });

    assert.deepEqual(target, {
      kind: "notebook",
      file: "examples/050.ipynb",
      cellIndex: 0,
    });
  });

  it("only auto-refreshes for files inside the current project directory", () => {
    assert.equal(
      isWatchedPathForCurrentProject({
        changedFile: "C:\\repo\\project-a\\schedule.py",
        projectDir: "C:\\repo\\project-a",
      }),
      true,
    );
    assert.equal(
      isWatchedPathForCurrentProject({
        changedFile: "C:\\repo\\project-b\\schedule.py",
        projectDir: "C:\\repo\\project-a",
      }),
      false,
    );
  });

  it("opens Q1Lens from a notebook cell through a managed project", async () => {
    const root = mkdtempSync(join(tmpdir(), "qbs-notebook-cell-"));
    const notebookPath = join(root, "experiment.ipynb");
    writeFileSync(notebookPath, "{}", "utf8");

    const commands: Record<string, (...args: any[]) => Promise<void>> = {};
    let analyzedProjectFile = "";
    let shownProjectFile = "";
    const originalLoad = (Module as any)._load;
    const vscode = {
      Uri: {
        file: (fsPath: string) => ({ fsPath, toString: () => `file://${fsPath}` }),
      },
      ViewColumn: { Beside: 2 },
      NotebookCellKind: { Markup: 1, Code: 2 },
      window: {
        createOutputChannel: () => ({ appendLine: () => {}, show: () => {}, dispose: () => {} }),
        showWarningMessage: async () => undefined,
        showQuickPick: async (items: any[]) => items[0],
        showInputBox: async (options: any) => options.value,
        activeTextEditor: undefined,
        activeNotebookEditor: undefined,
      },
      workspace: {
        getConfiguration: () => ({ get: (_key: string, fallback: any) => fallback }),
        findFiles: async () => [],
        fs: {
          createDirectory: async (uri: any) => mkdirSync(uri.fsPath, { recursive: true }),
          writeFile: async (uri: any, bytes: Uint8Array) => writeFileSync(uri.fsPath, Buffer.from(bytes)),
        },
        createFileSystemWatcher: () => ({
          onDidChange: () => ({ dispose: () => {} }),
          onDidCreate: () => ({ dispose: () => {} }),
          onDidDelete: () => ({ dispose: () => {} }),
          dispose: () => {},
        }),
      },
      languages: {
        createDiagnosticCollection: () => ({ clear: () => {}, set: () => {}, dispose: () => {} }),
      },
      commands: {
        registerCommand: (name: string, callback: (...args: any[]) => Promise<void>) => {
          commands[name] = callback;
          return { dispose: () => {} };
        },
        executeCommand: async () => undefined,
      },
      env: { openExternal: async () => undefined },
    };
    const cells: any[] = [
      {
        index: 0,
        kind: vscode.NotebookCellKind.Code,
        metadata: { tags: ["qbstimeline-setup"] },
        document: { getText: () => "hw_agent = build_compiler()\n" },
      },
      {
        index: 1,
        kind: vscode.NotebookCellKind.Code,
        metadata: {},
        document: { getText: () => "two_tone_sched = Schedule('demo')\n" },
      },
    ];
    const notebook = {
      uri: vscode.Uri.file(notebookPath),
      metadata: {},
      getCells: () => cells,
      cellAt: (index: number) => cells[index],
    };
    for (const cell of cells) {
      cell.notebook = notebook;
    }

    (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
      if (request === "vscode") {
        return vscode;
      }
      if (request === "./webview/panel") {
        return {
          TimelinePanel: class {
            show(_ir: any, paths: any) {
              shownProjectFile = paths.projectFile;
            }
          },
        };
      }
      if (request === "./diagnostics") {
        return { publishDiagnostics: () => {} };
      }
      if (request === "./qbsCli") {
        let irPath = "";
        let htmlPath = "";
        return {
          buildAnalyzeInvocation: (input: any) => {
            analyzedProjectFile = input.projectFile;
            irPath = input.irPath;
            return { command: "mock", args: ["analyze"] };
          },
          buildRenderInvocation: (input: any) => {
            htmlPath = input.htmlPath;
            return { command: "mock", args: ["render"] };
          },
          buildQ1TimelineAnalyzeInvocation: () => ({ command: "mock", args: ["q1timeline"] }),
          runProcessWithSpawn: (_command: string, args: string[]) => {
            if (args[0] === "analyze") {
              mkdirSync(join(root, ".qbs_timeline"), { recursive: true });
              writeFileSync(
                irPath,
                JSON.stringify({
                  schedule: { name: "notebook" },
                  operations: [],
                  symbolic_values: [],
                  symbolic_pulses: [],
                  q1asm_programs: [],
                  q1asm_provenance: [],
                }),
                "utf8",
              );
            }
            if (args[0] === "render") {
              writeFileSync(htmlPath, "<html></html>", "utf8");
            }
            return Promise.resolve({ exitCode: 0, stdout: "", stderr: "" });
          },
        };
      }
      return originalLoad.call(this, request, parent, isMain);
    };

    const modulePath = require.resolve("../src/qbs/commands");
    delete require.cache[modulePath];
    try {
      const freshCommands = require("../src/qbs/commands");
      freshCommands.registerCommands(
        { extensionUri: { fsPath: "C:\\extension" }, subscriptions: [] },
        { openQ1TimelineTarget: async () => undefined },
      );

      await commands["qbsTimeline.openNotebookTimelineFromCell"](cells[1]);

      assert.equal(analyzedProjectFile.endsWith(join(".qbs_timeline", "notebook", "qbstimeline.yml")), true);
      assert.equal(shownProjectFile, analyzedProjectFile);
      const projectText = readFileSync(analyzedProjectFile, "utf8");
      assert.match(projectText, /schedule_variable: two_tone_sched/);
      assert.match(projectText, /compiler_variable: hw_agent/);
      const snapshotText = readFileSync(join(root, ".qbs_timeline", "notebook", "selected.ipynb"), "utf8");
      assert.equal(JSON.parse(snapshotText).cells[1].metadata.tags.includes("qbstimeline-schedule"), true);
    } finally {
      delete require.cache[modulePath];
      (Module as any)._load = originalLoad;
    }
  });

  it("opens current folder Q1ASM files through the all-files q1timeline opener", async () => {
    const root = mkdtempSync(join(tmpdir(), "qbs-current-folder-q1asm-"));
    const folder = join(root, "standalone");
    mkdirSync(folder, { recursive: true });
    writeFileSync(join(folder, "schedule.py"), "", "utf8");
    writeFileSync(join(folder, "beta.q1asm"), "wait_sync 4\nstop\n", "utf8");
    writeFileSync(join(folder, "alpha.q1asm"), "wait_sync 4\nstop\n", "utf8");

    const commands: Record<string, (...args: any[]) => Promise<void>> = {};
    const executedCommands: Array<[string, any]> = [];
    const warnings: string[] = [];
    const originalLoad = (Module as any)._load;
    const vscode = {
      Uri: {
        file: (fsPath: string) => ({ fsPath, toString: () => `file://${fsPath}` }),
      },
      window: {
        activeTextEditor: { document: { uri: { fsPath: join(folder, "schedule.py") } } },
        createOutputChannel: () => ({ appendLine: () => {}, show: () => {}, dispose: () => {} }),
        showWarningMessage: async (message: string) => {
          warnings.push(message);
          return undefined;
        },
      },
      workspace: {
        workspaceFolders: [{ uri: { fsPath: root } }],
        getConfiguration: () => ({ get: (_key: string, fallback: any) => fallback }),
      },
      languages: {
        createDiagnosticCollection: () => ({ clear: () => {}, set: () => {}, dispose: () => {} }),
      },
      commands: {
        registerCommand: (name: string, callback: (...args: any[]) => Promise<void>) => {
          commands[name] = callback;
          return { dispose: () => {} };
        },
        executeCommand: async (name: string, arg: any) => {
          executedCommands.push([name, arg]);
        },
      },
      env: { openExternal: async () => undefined },
    };

    (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
      if (request === "vscode") {
        return vscode;
      }
      if (request === "./webview/panel") {
        return { TimelinePanel: class {} };
      }
      if (request === "./diagnostics") {
        return { publishDiagnostics: () => {} };
      }
      return originalLoad.call(this, request, parent, isMain);
    };

    const modulePath = require.resolve("../src/qbs/commands");
    delete require.cache[modulePath];
    try {
      const freshCommands = require("../src/qbs/commands");
      freshCommands.registerCommands(
        { extensionUri: { fsPath: "C:\\extension" }, subscriptions: [] },
        { openQ1TimelineTarget: async () => undefined },
      );

      await commands["qbsTimeline.openCurrentFolderQ1Timeline"]();

      assert.deepEqual(warnings, []);
      assert.equal(executedCommands[0][0], "q1timeline.openQ1asmFilesInFolder");
      assert.equal(executedCommands[0][1].fsPath, join(folder, "alpha.q1asm"));
    } finally {
      delete require.cache[modulePath];
      (Module as any)._load = originalLoad;
    }
  });

  it("keeps QBS webview open actions paired with the committed IR paths during refresh", async () => {
    const root = mkdtempSync(join(tmpdir(), "qbs-state-skew-"));
    const projectA = join(root, "project-a");
    const projectB = join(root, "project-b");
    mkdirSync(join(projectA, ".qbs_timeline"), { recursive: true });
    mkdirSync(join(projectB, ".qbs_timeline"), { recursive: true });
    writeFileSync(join(projectA, "schedule.py"), "", "utf8");
    writeFileSync(join(projectB, "schedule.py"), "", "utf8");
    writeFileSync(join(projectA, "qbstimeline.yml"), "schedule:\n  file: schedule.py\noutputs:\n  dir: .qbs_timeline\n", "utf8");
    writeFileSync(join(projectB, "qbstimeline.yml"), "schedule:\n  file: schedule.py\noutputs:\n  dir: .qbs_timeline\n", "utf8");
    const irA = {
      schedule: { name: "A" },
      operations: [{ id: "op", label: "Op", abs_time: 0, duration: 1e-9 }],
      symbolic_values: [],
      symbolic_pulses: [],
      q1asm_programs: [{ sequencer: "seq0", file: "q1asm/seq0.q1asm" }],
      q1asm_provenance: [{ sequencer: "seq0", line: 1, instruction: "play", operation_id: "op" }],
    };
    const irB = { ...irA, schedule: { name: "B" } };
    writeFileSync(join(projectA, ".qbs_timeline", "qbs_ir.json"), JSON.stringify(irA), "utf8");
    writeFileSync(join(projectB, ".qbs_timeline", "qbs_ir.json"), JSON.stringify(irB), "utf8");

    const commands: Record<string, () => Promise<void>> = {};
    const openedTargets: any[] = [];
    let panelHandlers: any;
    let findFilesCalls = 0;
    let analyzeCalls = 0;
    let releaseSecondAnalyze!: () => void;
    const originalLoad = (Module as any)._load;
    const secondAnalyzeStarted = new Promise<void>((resolve) => {
      const vscode = {
        Uri: {
          file: (fsPath: string) => ({ fsPath, toString: () => `file://${fsPath}` }),
          joinPath: (base: any, ...parts: string[]) => ({ fsPath: [base.fsPath, ...parts].join("\\") }),
        },
        ViewColumn: { Beside: 2 },
        window: {
          createOutputChannel: () => ({ appendLine: () => {}, show: () => {}, dispose: () => {} }),
          showWarningMessage: async () => undefined,
          showQuickPick: async (items: any[]) => items[0],
          activeTextEditor: undefined,
        },
        workspace: {
          getConfiguration: () => ({ get: (_key: string, fallback: any) => fallback }),
          findFiles: async () => {
            findFilesCalls += 1;
            return [{ fsPath: findFilesCalls === 1 ? join(projectA, "qbstimeline.yml") : join(projectB, "qbstimeline.yml") }];
          },
          createFileSystemWatcher: () => ({
            onDidChange: () => ({ dispose: () => {} }),
            onDidCreate: () => ({ dispose: () => {} }),
            onDidDelete: () => ({ dispose: () => {} }),
            dispose: () => {},
          }),
        },
        languages: {
          createDiagnosticCollection: () => ({ clear: () => {}, set: () => {}, dispose: () => {} }),
        },
        commands: {
          registerCommand: (name: string, callback: () => Promise<void>) => {
            commands[name] = callback;
            return { dispose: () => {} };
          },
          executeCommand: async () => undefined,
        },
        env: { openExternal: async () => undefined },
      };
      (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
        if (request === "vscode") {
          return vscode;
        }
        if (request === "./webview/panel") {
          return {
            TimelinePanel: class {
              constructor(_extensionUri: any, handlers: any) {
                panelHandlers = handlers;
              }
              show() {}
            },
          };
        }
        if (request === "./diagnostics") {
          return { publishDiagnostics: () => {} };
        }
        if (request === "./qbsCli") {
          return {
            buildAnalyzeInvocation: () => ({ command: "mock", args: ["analyze"] }),
            buildRenderInvocation: () => ({ command: "mock", args: ["render"] }),
            buildQ1TimelineAnalyzeInvocation: () => ({ command: "mock", args: ["q1timeline"] }),
            runProcessWithSpawn: (_command: string, args: string[]) => {
              if (args[0] === "analyze") {
                analyzeCalls += 1;
                if (analyzeCalls === 2) {
                  resolve();
                  return new Promise((done) => {
                    releaseSecondAnalyze = () => done({ exitCode: 0, stdout: "", stderr: "" });
                  });
                }
              }
              return Promise.resolve({ exitCode: 0, stdout: "", stderr: "" });
            },
          };
        }
        return originalLoad.call(this, request, parent, isMain);
      };
    });

    const modulePath = require.resolve("../src/qbs/commands");
    delete require.cache[modulePath];
    try {
      const freshCommands = require("../src/qbs/commands");
      freshCommands.registerCommands(
        { extensionUri: { fsPath: "C:\\extension" }, subscriptions: [] },
        { openQ1TimelineTarget: async (target: any) => openedTargets.push(target) },
      );

      await commands["qbsTimeline.analyzeAndOpen"]();
      const secondRun = commands["qbsTimeline.analyzeAndOpen"]();
      await secondAnalyzeStarted;
      await panelHandlers.onOpenQ1Timeline({ type: "openQ1Timeline", operationId: "op" });

      assert.equal(openedTargets[0].q1asmFile.replace(/\\/g, "/").includes("/project-a/.qbs_timeline/"), true);
      releaseSecondAnalyze();
      await secondRun;
    } finally {
      delete require.cache[modulePath];
      (Module as any)._load = originalLoad;
    }
  });
});
