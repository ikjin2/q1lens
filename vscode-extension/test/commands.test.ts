import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import Module = require("node:module");
import { tmpdir } from "node:os";
import { delimiter, join, resolve } from "node:path";
import {
  QBS_ANALYSIS_REQUIRED_MESSAGE,
  QBS_ANALYZE_AND_OPEN_ACTION,
  QBS_AUTO_REFRESH_GLOB,
  analyzeQ1TimelineWithCli,
  createAnalyzeAndOpenHandler,
  ensureQbsAnalysisAvailable,
  isWatchedPathForCurrentProject,
  loadQ1TimelineDiagnosticsFromDisk,
  notebookCellsFromDocument,
  openSourceTarget,
  registerQbsAutoRefresh,
  resolveQ1AsmSourceTarget,
  resolveScheduleSourceTarget,
} from "../src/qbs/commands";
import { createManagedNotebookTimelineProject } from "../src/qbs/notebookProject";
import { deriveOutputPaths, parseProjectConfigLite } from "../src/qbs/projectDiscovery";

class CancellationTokenSource {
  token = { isCancellationRequested: false };
  cancel() { this.token.isCancellationRequested = true; }
  dispose() {}
}

describe("commands", () => {
  it("opens an external schedule's literal source map instead of a same-named file beside the config", async function () {
    this.timeout(30000);
    const root = mkdtempSync(join(tmpdir(), "qbs-external-source-map-"));
    const projectDir = join(root, "config"), sourceDir = join(root, "shared");
    const projectFile = join(projectDir, "qbstimeline.yml"), scheduleFile = join(sourceDir, "schedule.py");
    const originalPythonPath = process.env.PYTHONPATH;
    const pythonSource = resolve(__dirname, "../../../src");
    process.env.PYTHONPATH = originalPythonPath ? `${pythonSource}${delimiter}${originalPythonPath}` : pythonSource;
    try {
      mkdirSync(projectDir);
      mkdirSync(sourceDir);
      writeFileSync(join(projectDir, "schedule.py"), "raise RuntimeError('Unrelated same-named source')\n", "utf8");
      const source = [
        "class Schedule:", '    name = "external source"',
        "    def add(self, operation, *, label):", "        return None",
        "def build_schedule():", "    schedule = Schedule()",
        '    schedule.add(object(), label="measure")', "    return schedule",
        "class Compiler:", "    def compile(self, schedule):",
        '        return {"name": schedule.name, "schedulables": {"measure": {"operation_id": "measure_op", "abs_time": 0.0}}, "operations": {"measure_op": {"name": "Measure(q0)", "duration": 4e-9}}, "compiled_instructions": {}}',
        "def build_compiler():", "    return Compiler()",
      ];
      writeFileSync(scheduleFile, source.join("\n"), "utf8");
      const yaml = "schedule:\n  file: ../shared/schedule.py\nlow_level:\n  q1timeline: false\n";
      writeFileSync(projectFile, yaml, "utf8");
      const paths = deriveOutputPaths({ projectFile, ...parseProjectConfigLite(yaml), overrideOutputDir: null });

      await require("../src/qbs/commands").analyzeWithCli(paths, { pythonPath: process.env.PYTHON ?? "python", pythonArgs: ["-B"], outputDirOverride: null });
      const ir = JSON.parse(readFileSync(paths.irPath, "utf8"));
      const target = resolveScheduleSourceTarget({ ir, projectDir, scheduleFile, selection: { blockId: ir.operations[0].id } });

      assert.equal(target.kind, "file");
      assert.equal(target.file, scheduleFile);
      if (target.kind === "file") assert.equal(target.line, source.indexOf('    schedule.add(object(), label="measure")') + 1);
    } finally {
      if (originalPythonPath === undefined) delete process.env.PYTHONPATH;
      else process.env.PYTHONPATH = originalPythonPath;
      rmSync(root, { recursive: true, force: true });
    }
  });

  for (const scenario of ["file schedule", "file schedule with notebook source map"]) {
    it(`runs a ${scenario} analysis from its original execution directory`, async () => {
      const invocations: any[] = [];
      const root = join(tmpdir(), "qbs-notebook-context");
      const projectDir = join(root, ".qbs_timeline", "notebook");
      const notebookPath = join(root, "experiment.ipynb");
      const paths = {
        projectFile: join(projectDir, "qbstimeline.yml"), projectDir,
        outputDir: join(root, ".qbs_timeline"), irPath: join(root, ".qbs_timeline", "qbs_ir.json"),
        ...(scenario === "file schedule with notebook source map" ? { sourceNotebookPath: notebookPath } : {}),
      };
      const originalLoad = (Module as any)._load;
      (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
        if (request === "./qbsCli") return {
          ...originalLoad.call(this, request, parent, isMain),
          runProcessWithSpawn: async (_command: string, args: string[], options: any) => {
            invocations.push({ args, options });
            return { exitCode: 0, stdout: "", stderr: "" };
          },
        };
        return originalLoad.call(this, request, parent, isMain);
      };
      const modulePath = require.resolve("../src/qbs/commands");
      delete require.cache[modulePath];
      try {
        await require("../src/qbs/commands").analyzeWithCli(paths, { pythonPath: "python", pythonArgs: [], outputDirOverride: null });
        assert.equal(invocations.length, 1);
        assert.equal(invocations[0].options.cwd, projectDir);
        assert.ok(invocations[0].args.includes(paths.projectFile));
      } finally {
        delete require.cache[modulePath];
        (Module as any)._load = originalLoad;
      }
    });
  }

  for (const scenario of ["sibling import", "selected cell relative read", "compiler relative read"]) {
    it(`analyzes the live managed notebook snapshot with its original ${scenario}`, async function () {
      this.timeout(30000);
      const root = mkdtempSync(join(tmpdir(), "qbs-notebook-execution-"));
      const notebookPath = join(root, "experiment.ipynb");
      const originalPythonPath = process.env.PYTHONPATH;
      const pythonSource = resolve(__dirname, "../../../src");
      process.env.PYTHONPATH = originalPythonPath ? `${pythonSource}${delimiter}${originalPythonPath}` : pythonSource;
      try {
        writeFileSync(join(root, "local_helper.py"), "VALUE = 'live snapshot'\n", "utf8");
        writeFileSync(join(root, "calibration.txt"), "live snapshot", "utf8");
        const setup = [
          "from types import SimpleNamespace",
          "class LocalCompiler:",
          "    def compile(self, schedule):",
          ...(scenario === "compiler relative read" ? [
            "        from pathlib import Path",
            "        schedule.name = Path('calibration.txt').read_text()",
          ] : []),
          "        schedule.compiled_instructions = {}",
          "        return schedule",
        ].join("\n");
        const schedule = [
          ...(scenario === "sibling import" ? ["from local_helper import VALUE"] : []),
          ...(scenario === "selected cell relative read" ? ["from pathlib import Path", "VALUE = Path('calibration.txt').read_text()"] : []),
          ...(scenario === "compiler relative read" ? ["VALUE = 'awaiting compiler'"] : []),
          "schedule = SimpleNamespace(name=VALUE, schedulables={}, operations={})",
          "compiler = LocalCompiler()",
        ].join("\n");
        const cells = [
          { id: "ignored", kind: "code" as const, text: "raise RuntimeError('untagged cell must not run')", metadata: {} },
          { id: "setup", kind: "code" as const, text: setup, metadata: { tags: ["qbstimeline-setup"] } },
          { id: "selected", kind: "code" as const, text: schedule, metadata: {} },
        ];
        writeFileSync(notebookPath, JSON.stringify({
          cells: [{ cell_type: "code", source: "raise RuntimeError('persisted original must not run')", metadata: { tags: ["qbstimeline-schedule"] } }],
          metadata: {}, nbformat: 4, nbformat_minor: 5,
        }), "utf8");
        const managed = createManagedNotebookTimelineProject({ notebookPath, selectedCellIndex: 2, scheduleVariable: "schedule", compilerVariable: "compiler", cells });
        mkdirSync(managed.projectDir, { recursive: true });
        writeFileSync(managed.projectFile, managed.projectYaml, "utf8");
        writeFileSync(managed.snapshotFile, managed.snapshotJson, "utf8");
        const config = parseProjectConfigLite(managed.projectYaml);
        const paths = deriveOutputPaths({ projectFile: managed.projectFile, ...config, overrideOutputDir: null });
        await require("../src/qbs/commands").analyzeWithCli(paths, { pythonPath: process.env.PYTHON ?? "python", pythonArgs: ["-B"], outputDirOverride: null });
        assert.equal(JSON.parse(readFileSync(paths.irPath, "utf8")).schedule.name, "live snapshot");
      } finally {
        if (originalPythonPath === undefined) delete process.env.PYTHONPATH;
        else process.env.PYTHONPATH = originalPythonPath;
        rmSync(root, { recursive: true, force: true });
      }
    });
  }

  it("asks to save an untitled notebook before checking its kernel or preparing a managed project", async () => {
    const callbacks: Record<string, (...args: any[]) => Promise<void>> = {};
    const warnings: string[] = [];
    const prompts: string[] = [];
    const subscriptions: Array<{ dispose(): void }> = [];
    let kernelChecks = 0;
    let artifactPreparations = 0;
    const cells: any[] = [];
    const notebook = {
      uri: { scheme: "untitled", fsPath: "Untitled-1.ipynb" }, isUntitled: true,
      getCells: () => cells, cellAt: (index: number) => cells[index],
    };
    cells.push({ notebook, index: 0, kind: 2, metadata: {}, document: { getText: () => "compiler = object()\nschedule = object()\n" } });
    const originalLoad = (Module as any)._load;
    (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
      if (request === "vscode") return {
        Uri: { file: (fsPath: string) => ({ fsPath }) }, NotebookCellKind: { Markup: 1, Code: 2 },
        window: {
          createOutputChannel: () => ({ appendLine() {}, dispose() {} }),
          showWarningMessage: async (message: string) => { warnings.push(message); },
          showInputBox: async (options: any) => { prompts.push(options.title); return options.value; },
          showErrorMessage: async () => undefined,
        },
        workspace: {
          getConfiguration: () => ({ get: (_key: string, fallback: any) => fallback }),
          fs: { createDirectory: async () => { artifactPreparations += 1; throw new Error("An untitled notebook has no managed project directory"); } },
        },
        languages: { createDiagnosticCollection: () => ({ dispose() {} }) },
        commands: { registerCommand: (name: string, callback: any) => { callbacks[name] = callback; return { dispose() {} }; } },
      };
      if (request === "../notebookEnvironment") return { ensureNotebookEnvironment: async () => {
        kernelChecks += 1;
        return { pythonPath: "python", pythonArgs: [] };
      } };
      if (request === "./webview/panel") return { TimelinePanel: class { dispose() {} } };
      if (request === "./diagnostics") return { publishDiagnostics() {} };
      return originalLoad.call(this, request, parent, isMain);
    };
    const modulePath = require.resolve("../src/qbs/commands");
    delete require.cache[modulePath];
    try {
      require("../src/qbs/commands").registerCommands({ extensionUri: {}, subscriptions }, { openQ1TimelineTarget: async () => undefined });
      await callbacks["qbsTimeline.openNotebookTimelineFromCell"](cells[0]);
      assert.equal(kernelChecks, 0);
      assert.deepEqual(prompts, []);
      assert.equal(artifactPreparations, 0);
      assert.deepEqual(warnings, ["Open a saved notebook code cell before opening Q1Lens."]);
    } finally {
      subscriptions.forEach((subscription) => subscription.dispose());
      delete require.cache[modulePath];
      (Module as any)._load = originalLoad;
    }
  });

  it("passes the UI artifact directory to analyze only when an output override is configured", async () => {
    const invocations: any[] = [];
    const originalLoad = (Module as any)._load;
    (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
      if (request === "./qbsCli") return {
        ...originalLoad.call(this, request, parent, isMain),
        buildAnalyzeInvocation: (input: any) => { invocations.push(input); return { command: "python", args: [] }; },
        runProcessWithSpawn: async () => ({ exitCode: 0, stdout: "", stderr: "" }),
      };
      return originalLoad.call(this, request, parent, isMain);
    };
    const modulePath = require.resolve("../src/qbs/commands");
    delete require.cache[modulePath];
    try {
      const { analyzeWithCli } = require("../src/qbs/commands");
      const paths = { projectFile: "C:\\repo\\qbstimeline.yml", projectDir: "C:\\repo", outputDir: "C:\\repo\\generated", irPath: "C:\\repo\\generated\\qbs_ir.json" };
      for (const outputDirOverride of [null, "generated"]) {
        await analyzeWithCli(paths, { pythonPath: "python", pythonArgs: [], outputDirOverride });
      }
      assert.deepEqual(invocations.map((input) => input.outputDir), [undefined, paths.outputDir]);
    } finally {
      delete require.cache[modulePath];
      (Module as any)._load = originalLoad;
    }
  });

  for (const legacy of [false, true]) {
    it(`preserves ${legacy ? "legacy" : "default"} API notebook IDs and setup tags in managed snapshots`, () => {
      const metadata = (id: string, tags: string[]) => {
        const value = { id, metadata: { tags, customSetting: "keep" }, execution_count: 3 };
        return legacy ? { custom: value } : value;
      };
      const cells = notebookCellsFromDocument({ NotebookCellKind: { Markup: 1 } }, {
        getCells: () => [
          { kind: 2, metadata: metadata("setup-cell", ["qbstimeline-setup"]), document: { getText: () => "compiler = 'ready'\n" } },
          { kind: 2, metadata: metadata("schedule-cell", []), document: { getText: () => "schedule = compiler\n" } },
        ],
      });
      const project = createManagedNotebookTimelineProject({
        notebookPath: "C:\\repo\\experiment.ipynb", selectedCellIndex: 1,
        scheduleVariable: "schedule", compilerVariable: "compiler", cells,
      });
      const snapshot = JSON.parse(project.snapshotJson);
      assert.deepEqual(snapshot.cells.map((cell: any) => cell.id), ["setup-cell", "schedule-cell"]);
      assert.deepEqual(snapshot.cells[0].metadata, { tags: ["qbstimeline-setup"], customSetting: "keep" });
      assert.deepEqual(snapshot.cells[1].metadata, { tags: ["qbstimeline-schedule"], customSetting: "keep" });
    });

    it(`marks setup and schedule cells in ${legacy ? "legacy" : "default"} API metadata without losing IDs or saved tags`, async () => {
      const callbacks: Record<string, (...args: any[]) => Promise<void>> = {};
      const wrapped = (id: string, tags: string[]) => {
        const value = { id, metadata: { tags, customSetting: "keep" }, execution_count: 3 };
        return legacy ? { custom: value, attachments: { image: "keep" } } : value;
      };
      const cells: any[] = [
        { kind: 2, metadata: wrapped("setup-cell", []), document: { getText: () => "compiler = 'ready'\n" } },
        { kind: 2, metadata: wrapped("old-schedule", ["qbstimeline-schedule", "other"]), document: { getText: () => "old = 1\n" } },
        { kind: 2, metadata: wrapped("schedule-cell", []), document: { getText: () => "schedule = compiler\n" } },
      ];
      const notebook = { uri: { fsPath: "C:\\repo\\experiment.ipynb" }, getCells: () => cells, cellAt: (index: number) => cells[index] };
      cells.forEach((cell, index) => { cell.notebook = notebook; cell.index = index; });
      const originalLoad = (Module as any)._load;
      (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
        if (request === "vscode") return {
          NotebookCellKind: { Markup: 1, Code: 2 },
          WorkspaceEdit: class { edits: any[] = []; set(_uri: any, edits: any[]) { this.edits = edits; } },
          NotebookEdit: { updateCellMetadata: (index: number, metadata: any) => ({ index, metadata }) },
          window: {
            createOutputChannel: () => ({ dispose() {} }),
            showInformationMessage: async () => undefined,
          },
          workspace: {
            getConfiguration: () => ({ get: (_key: string, fallback: any) => fallback }),
            applyEdit: async (edit: any) => {
              for (const cellEdit of edit.edits) cells[cellEdit.index].metadata = cellEdit.metadata;
              return true;
            },
          },
          languages: { createDiagnosticCollection: () => ({ dispose() {} }) },
          commands: { registerCommand: (name: string, callback: any) => { callbacks[name] = callback; return { dispose() {} }; } },
        };
        if (request === "./webview/panel") return { TimelinePanel: class {} };
        if (request === "./diagnostics") return { publishDiagnostics() {} };
        return originalLoad.call(this, request, parent, isMain);
      };
      const modulePath = require.resolve("../src/qbs/commands");
      delete require.cache[modulePath];
      try {
        require("../src/qbs/commands").registerCommands({ extensionUri: {}, subscriptions: [] }, { openQ1TimelineTarget: async () => undefined });
        await callbacks["qbsTimeline.markNotebookSetupCell"](cells[0]);
        await callbacks["qbsTimeline.markNotebookScheduleCell"](cells[2]);
        const savedCells = cells.map((cell) => legacy ? cell.metadata.custom : cell.metadata);
        assert.deepEqual(savedCells.map((cell) => cell.id), ["setup-cell", "old-schedule", "schedule-cell"]);
        assert.deepEqual(savedCells.map((cell) => cell.metadata.tags), [["qbstimeline-setup"], ["other"], ["qbstimeline-schedule"]]);
        assert.equal(savedCells.every((cell) => cell.execution_count === 3 && cell.metadata.customSetting === "keep"), true);
        if (legacy) assert.deepEqual(cells[0].metadata.attachments, { image: "keep" });
      } finally {
        delete require.cache[modulePath];
        (Module as any)._load = originalLoad;
      }
    });
  }
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

  it("reconfigures exactly one auto-refresh watcher across canonical and legacy setting changes", () => {
    let enabled = false;
    let configurationListener: ((event: { affectsConfiguration(section: string): boolean }) => void) | undefined;
    let created = 0;
    let active = 0;
    let disposed = 0;
    const registration = registerQbsAutoRefresh({
      readEnabled: () => enabled,
      createWatcher: () => {
        created += 1;
        active += 1;
        return {
          onDidChange: () => ({ dispose: () => undefined }),
          onDidCreate: () => ({ dispose: () => undefined }),
          onDidDelete: () => ({ dispose: () => undefined }),
          dispose: () => {
            disposed += 1;
            active -= 1;
          },
        } as any;
      },
      onDidChangeConfiguration: (listener) => {
        configurationListener = listener;
        return { dispose: () => undefined };
      },
      shouldRefresh: () => true,
      run: async () => undefined,
      delayMs: 1,
    });
    const change = (key: string) => configurationListener?.({
      affectsConfiguration: (section) => section === key,
    });

    assert.equal(active, 0);
    enabled = true;
    change("q1lens.qbs.autoRefresh");
    assert.deepEqual({ created, active, disposed }, { created: 1, active: 1, disposed: 0 });

    enabled = false;
    change("qbloxTimeline.qbs.autoRefresh");
    assert.deepEqual({ created, active, disposed }, { created: 1, active: 0, disposed: 1 });

    enabled = true;
    change("qbsTimeline.autoRefresh");
    assert.deepEqual({ created, active, disposed }, { created: 2, active: 1, disposed: 1 });

    change("q1lens.qbs.autoRefresh");
    assert.deepEqual({ created, active, disposed }, { created: 3, active: 1, disposed: 2 });
    registration.dispose();
    assert.deepEqual({ created, active, disposed }, { created: 3, active: 0, disposed: 3 });
  });

  it("only analyzes for a missing artifact after the explicit recovery action", async () => {
    let available = false;
    let analyzes = 0;
    const prompts: Array<[string, string]> = [];
    const cancelResult = await ensureQbsAnalysisAvailable({
      isAvailable: () => available,
      analyze: async () => {
        analyzes += 1;
        available = true;
      },
      prompt: async (message, action) => {
        prompts.push([message, action]);
        return undefined;
      },
    });

    assert.equal(cancelResult, false);
    assert.equal(analyzes, 0);
    assert.deepEqual(prompts, [[QBS_ANALYSIS_REQUIRED_MESSAGE, QBS_ANALYZE_AND_OPEN_ACTION]]);

    const recoveryResult = await ensureQbsAnalysisAvailable({
      isAvailable: () => available,
      analyze: async () => {
        analyzes += 1;
        available = true;
      },
      prompt: async () => QBS_ANALYZE_AND_OPEN_ACTION,
    });

    assert.equal(recoveryResult, true);
    assert.equal(analyzes, 1);
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
              notebook: { file: "examples/050.ipynb", cell_index: 12, cell_line: 4, cell_id: "schedule-cell" },
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
      cellId: "schedule-cell",
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
    const cell = { index: 13, metadata: { id: "schedule-cell" }, document: { uri: { fsPath: "vscode-notebook-cell://cell" } } };
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
              cellCount: 14,
              cellAt: (index: number) => index === 13 ? cell : { index, metadata: { id: `other-${index}` }, document: {} },
            }),
          },
          window: {
            tabGroups: { all: [] },
            showNotebookDocument: async () => ({}),
            showTextDocument: async (document: any) => {
              assert.equal(document, cell.document, "Open source must follow the mapped cell ID after insertion");
              return {
                set selection(value: any) {
                  shownTextSelections.push(value.start);
                },
                revealRange: () => undefined,
              };
            },
          },
          TextEditorRevealType: { InCenter: 1 },
        };
      }
      return originalLoad.call(this, request, parent, isMain);
    };
    try {
      await openSourceTarget({ kind: "notebook", file: "C:\\repo\\notebook.ipynb", cellIndex: 12, cellLine: 4, cellId: "schedule-cell" });
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

  it("includes two-dot-prefixed child directories in QBS project watching without accepting parent paths", () => {
    const projectDir = join(tmpdir(), "q1lens-watched-project");
    assert.equal(isWatchedPathForCurrentProject({ changedFile: join(projectDir, "..experiment", "schedule.py"), projectDir }), true);
    assert.equal(isWatchedPathForCurrentProject({ changedFile: join(projectDir, "..", "other", "schedule.py"), projectDir }), false);
  });

  it("uses the original notebook's current kernel for opening and refreshing a managed project", async () => {
    const root = mkdtempSync(join(tmpdir(), "qbs-notebook-cell-"));
    const notebookPath = join(root, "experiment.ipynb");
    writeFileSync(notebookPath, "{}", "utf8");

    const commands: Record<string, (...args: any[]) => Promise<void>> = {};
    let analyzedProjectFile = "";
    let shownProjectFile = "";
    let panelHandlers: any;
    let panelShows = 0;
    let configurationWrites = 0;
    let variablePrompts = 0;
    let activeNotebookEditor: any;
    let notebookEnvironment: { pythonPath: string; pythonArgs: string[] } | undefined;
    let environmentWait = Promise.resolve();
    let scheduleText = "two_tone_sched = Schedule('demo')\n";
    const environmentNotebooks: string[] = [];
    const invocations: Array<{ stage: string; pythonPath: string; pythonArgs: string[]; q1timelineCommand?: string | null }> = [];
    const originalLoad = (Module as any)._load;
    const vscode = {
      Uri: {
        file: (fsPath: string) => ({ fsPath, toString: () => `file://${fsPath}` }),
      },
      ViewColumn: { Beside: 2 },
      NotebookCellKind: { Markup: 1, Code: 2 },
      CancellationTokenSource,
      window: {
        createOutputChannel: () => ({ appendLine: () => {}, show: () => {}, dispose: () => {} }),
        showWarningMessage: async () => undefined,
        showQuickPick: async (items: any[]) => items[0],
        showInputBox: async (options: any) => {
          variablePrompts += 1;
          if (options.title === "Compiler variable") {
            cells.unshift({
              notebook, kind: vscode.NotebookCellKind.Markup, metadata: {},
              document: { getText: () => "Inserted during the variable prompts" },
            });
            cells.forEach((cell, index) => { cell.index = index; });
            scheduleText = "updated_sched = Schedule('edited during prompts')\n";
          }
          return options.value;
        },
        activeTextEditor: undefined,
        get activeNotebookEditor() {
          return activeNotebookEditor;
        },
      },
      workspace: {
        getConfiguration: (section: string) => ({
          get: (key: string, fallback: any) => {
            if (section === "q1lens") {
              if (key === "pythonPath") return "configured-python";
              if (key === "pythonArgs") return ["--configured-argument"];
              if (key === "q1timelineCommand") return "configured-q1timeline";
            }
            return fallback;
          },
          update: async () => { configurationWrites += 1; },
        }),
        findFiles: async () => [],
        getWorkspaceFolder: () => ({ uri: { fsPath: root } }),
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
        document: { getText: () => scheduleText },
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
      if (request === "../notebookEnvironment") {
        return {
          ensureNotebookEnvironment: async (_vscode: any, uri: any) => {
            environmentNotebooks.push(uri.fsPath);
            await environmentWait;
            return notebookEnvironment;
          },
        };
      }
      if (request === "./webview/panel") {
        return {
          TimelinePanel: class {
            constructor(_extensionUri: any, handlers: any) {
              panelHandlers = handlers;
            }
            show(_ir: any, paths: any) {
              shownProjectFile = paths.projectFile;
              panelShows += 1;
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
        let timelineIrPath = "";
        let diagnosticsPath = "";
        return {
          buildAnalyzeInvocation: (input: any) => {
            invocations.push({ stage: "analyze", ...input });
            analyzedProjectFile = input.projectFile;
            irPath = input.irPath;
            return { command: "mock", args: ["analyze"] };
          },
          buildRenderInvocation: (input: any) => {
            invocations.push({ stage: "render", ...input });
            htmlPath = input.htmlPath;
            return { command: "mock", args: ["render"] };
          },
          buildQ1TimelineAnalyzeInvocation: (input: any) => {
            invocations.push({ stage: "q1timeline", ...input });
            timelineIrPath = input.timelineIrPath;
            diagnosticsPath = input.diagnosticsPath;
            return { command: "mock", args: ["q1timeline"] };
          },
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
                  q1asm_programs: [{ sequencer: "seq0", file: "q1asm/seq0.q1asm" }],
                  q1asm_provenance: [],
                }),
                "utf8",
              );
              writeFileSync(join(root, ".qbs_timeline", "q1timeline.yml"), "sequencers: []\n", "utf8");
            }
            if (args[0] === "render") {
              writeFileSync(htmlPath, "<html></html>", "utf8");
            }
            if (args[0] === "q1timeline") {
              writeFileSync(timelineIrPath, JSON.stringify({ events: [] }), "utf8");
              writeFileSync(diagnosticsPath, "[]", "utf8");
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

      assert.equal(variablePrompts, 0);
      assert.equal(analyzedProjectFile, "");
      assert.equal(existsSync(join(root, ".qbs_timeline", "notebook", "qbstimeline.yml")), false);

      notebookEnvironment = { pythonPath: join(root, "kernel-a", "python.exe"), pythonArgs: [] };
      let resumeEnvironment!: () => void;
      environmentWait = new Promise<void>((resolveDone) => { resumeEnvironment = resolveDone; });
      activeNotebookEditor = { notebook, selection: { start: 1 } };
      const opening = commands["qbsTimeline.openNotebookTimelineFromCell"]();
      scheduleText = "updated_sched = Schedule('edited during setup')\n";
      activeNotebookEditor = { notebook: { uri: vscode.Uri.file(join(root, "other.ipynb")) }, selection: { start: 0 } };
      resumeEnvironment();
      await opening;

      assert.equal(analyzedProjectFile.endsWith(join(".qbs_timeline", "notebook", "qbstimeline.yml")), true);
      assert.equal(shownProjectFile, analyzedProjectFile);
      const projectText = readFileSync(analyzedProjectFile, "utf8");
      assert.match(projectText, /schedule_variable: updated_sched/);
      assert.match(projectText, /compiler_variable: hw_agent/);
      const snapshotText = readFileSync(join(root, ".qbs_timeline", "notebook", "selected.ipynb"), "utf8");
      assert.equal(JSON.parse(snapshotText).metadata.qbstimeline.selected_cell_index, 2);
      assert.equal(JSON.parse(snapshotText).cells[2].metadata.tags.includes("qbstimeline-schedule"), true);
      assert.equal(JSON.parse(snapshotText).cells[2].source.join(""), scheduleText);
      assert.deepEqual(invocations.map(({ stage, pythonPath, pythonArgs }) => ({ stage, pythonPath, pythonArgs })), [
        { stage: "analyze", ...notebookEnvironment },
        { stage: "render", ...notebookEnvironment },
        { stage: "q1timeline", ...notebookEnvironment },
      ]);
      assert.equal(invocations[2].q1timelineCommand, null);

      activeNotebookEditor = { notebook: { uri: vscode.Uri.file(join(root, "other.ipynb")) } };
      notebookEnvironment = { pythonPath: join(root, "kernel-b", "python.exe"), pythonArgs: [] };
      const originalProjectFile = analyzedProjectFile;
      await commands["qbsTimeline.refresh"]();

      assert.equal(analyzedProjectFile, originalProjectFile);
      assert.deepEqual(invocations.slice(3).map(({ stage, pythonPath, pythonArgs }) => ({ stage, pythonPath, pythonArgs })), [
        { stage: "analyze", ...notebookEnvironment },
        { stage: "render", ...notebookEnvironment },
        { stage: "q1timeline", ...notebookEnvironment },
      ]);
      assert.equal(invocations[5].q1timelineCommand, null);

      notebookEnvironment = undefined;
      await panelHandlers.onRefresh();

      assert.deepEqual(environmentNotebooks, [notebookPath, notebookPath, notebookPath, notebookPath]);
      assert.equal(invocations.length, 6);
      assert.equal(panelShows, 2);
      assert.equal(configurationWrites, 0);
    } finally {
      delete require.cache[modulePath];
      (Module as any)._load = originalLoad;
    }
  });

  for (const { stage, unavailable } of ["environment setup", "Schedule variable", "Compiler variable"].flatMap(
    (stage) => ["closed", "deleted"].map((unavailable) => ({ stage, unavailable })),
  )) {
    it(`stops opening a notebook when its original cell is ${unavailable} during ${stage}`, async () => {
      const commands: Record<string, (...args: any[]) => Promise<void>> = {};
      const warnings: string[] = [];
      const cells: any[] = [];
      const notebook = {
        uri: { fsPath: join(tmpdir(), "q1lens-unavailable-notebook.ipynb") },
        isClosed: false,
        getCells: () => cells,
        cellAt: (index: number) => cells[index],
      };
      cells.push({ notebook, index: 0, kind: 2, document: { getText: () => "schedule = Schedule('demo')\n" } });
      const invalidateCell = () => {
        if (unavailable === "closed") notebook.isClosed = true;
        else cells.splice(0, 1);
      };
      const originalLoad = (Module as any)._load;
      (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
        if (request === "vscode") return {
          NotebookCellKind: { Markup: 1, Code: 2 },
          CancellationTokenSource,
          window: {
            createOutputChannel: () => ({ appendLine: () => {}, show: () => {}, dispose: () => {} }),
            showWarningMessage: async (message: string) => { warnings.push(message); },
            showInputBox: async (options: { title: string }) => {
              if (stage === "environment setup") assert.fail("Variable prompts must not open for an unavailable cell");
              if (options.title === stage) invalidateCell();
              return options.title === "Schedule variable" ? "schedule" : "compiler";
            },
          },
          workspace: {
            getConfiguration: () => ({ get: (_key: string, fallback: any) => fallback }),
            fs: {
              createDirectory: async () => { assert.fail("No project artifacts should be created"); },
            },
          },
          languages: {
            createDiagnosticCollection: () => ({ clear: () => {}, set: () => {}, dispose: () => {} }),
          },
          commands: {
            registerCommand: (name: string, callback: (...args: any[]) => Promise<void>) => {
              commands[name] = callback;
              return { dispose: () => {} };
            },
          },
        };
        if (request === "../notebookEnvironment") return {
          ensureNotebookEnvironment: async () => {
            if (stage === "environment setup") invalidateCell();
            return { pythonPath: "notebook-python", pythonArgs: [] };
          },
        };
        if (request === "./notebookProject") return {
          ...originalLoad.call(this, request, parent, isMain),
          createManagedNotebookTimelineProject: () => assert.fail("No project should be generated for an unavailable cell"),
        };
        if (request === "./webview/panel") return { TimelinePanel: class {} };
        if (request === "./diagnostics") return { publishDiagnostics: () => {} };
        return originalLoad.call(this, request, parent, isMain);
      };
      const modulePath = require.resolve("../src/qbs/commands");
      delete require.cache[modulePath];
      try {
        require("../src/qbs/commands").registerCommands(
          { extensionUri: { fsPath: "C:\\extension" }, subscriptions: [] },
          { openQ1TimelineTarget: async () => undefined },
        );
        await commands["qbsTimeline.openNotebookTimelineFromCell"](cells[0]);

        assert.equal(warnings.length, 1);
        assert.match(warnings[0], /original notebook cell is no longer available/);
      } finally {
        delete require.cache[modulePath];
        (Module as any)._load = originalLoad;
      }
    });
  }

  for (const source of ["vscode", "manual"] as const) {
    it(`uses ${source} Python for file schedules and refreshes the original file's environment`, async () => {
      const root = mkdtempSync(join(tmpdir(), "qbs-file-environment-"));
      const projectFile = join(root, "qbstimeline.yml");
      const scheduleFile = join(root, "schedule.py");
      const outputDir = join(root, ".qbs_timeline");
      mkdirSync(outputDir);
      // Source metadata must not turn a file schedule into a notebook runtime.
      writeFileSync(projectFile, "schedule:\n  file: schedule.py\nsource:\n  notebook: source.ipynb\n", "utf8");
      writeFileSync(join(outputDir, "qbs_ir.json"), JSON.stringify({
        operations: [], symbolic_values: [], symbolic_pulses: [],
        q1asm_programs: [{ sequencer: "seq0", file: "q1asm/seq0.q1asm" }], q1asm_provenance: [],
      }), "utf8");
      writeFileSync(join(outputDir, "q1timeline.yml"), "sequencers: []\n", "utf8");
      const commands: Record<string, () => Promise<void>> = {};
      const invocations: Array<{ stage: string; pythonPath: string; pythonArgs: string[]; q1timelineCommand?: string | null }> = [];
      const environmentResources: string[] = [];
      const configurationResources: string[] = [];
      const recoveryCommands: any[][] = [];
      let failAnalysis = false;
      const q1timelineCommand = "custom-q1timeline";
      const expectedQ1timelineCommand = source === "manual" ? q1timelineCommand : null;
      let activeFile = scheduleFile;
      let selectedEnvironment: { pythonPath: string; pythonArgs: string[] } | undefined = {
        pythonPath: source === "manual" ? "custom-launcher" : join(root, ".venv", "bin", "python"),
        pythonArgs: source === "manual" ? ["-3.12"] : [],
      };
      let kernelLookups = 0;
      let panelShows = 0;
      const panelRevealRequests: boolean[] = [];
      let panelHandlers: any;
      const originalLoad = (Module as any)._load;
      (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
        if (request === "vscode") {
          return {
            Uri: { file: (fsPath: string) => ({ fsPath }) },
            window: {
              createOutputChannel: () => ({ appendLine: () => {}, show: () => {}, dispose: () => {} }),
              showErrorMessage: async () => "Check Python Environment",
              get activeTextEditor() { return { document: { uri: { fsPath: activeFile } } }; },
            },
            workspace: {
              getConfiguration: (section: string, resource: any) => {
                if (resource) configurationResources.push(resource.fsPath);
                return {
                  get: (key: string, fallback: any) => {
                    if (section === "q1lens" && key === "pythonPath") return "wrong-default-python";
                    if (section === "q1lens" && key === "pythonArgs") return ["--wrong-argument"];
                    if (section === "q1lens" && key === "q1timelineCommand") return q1timelineCommand;
                    return fallback;
                  },
                };
              },
              findFiles: async () => [{ fsPath: projectFile }],
            },
            languages: {
              createDiagnosticCollection: () => ({ clear: () => {}, set: () => {}, dispose: () => {} }),
            },
            commands: {
              executeCommand: async (...args: any[]) => { recoveryCommands.push(args); },
              registerCommand: (name: string, callback: () => Promise<void>) => {
                commands[name] = callback;
                return { dispose: () => {} };
              },
            },
          };
        }
        if (request === "../notebookEnvironment") {
          return { ensureNotebookEnvironment: async () => { kernelLookups += 1; return undefined; } };
        }
        if (request === "../fileEnvironment") {
          return {
            ensureFilePythonEnvironment: async (_vscode: any, resource: any, kind: string, _output: any, options: any) => {
              assert.equal(kind, "qbs");
              assert.equal(options.cwd, root);
              environmentResources.push(resource.fsPath);
              return selectedEnvironment ? { ...selectedEnvironment, source, sourceLabel: source, resource } : undefined;
            },
          };
        }
        if (request === "./webview/panel") {
          return { TimelinePanel: class {
            constructor(_extensionUri: any, handlers: any) { panelHandlers = handlers; }
            show(_ir: any, _paths: any, reveal: boolean) {
              panelShows += 1;
              panelRevealRequests.push(reveal);
            }
          } };
        }
        if (request === "./diagnostics") {
          return { publishDiagnostics: () => {} };
        }
        if (request === "./qbsCli") {
          const invocation = (stage: string) => (input: any) => {
            invocations.push({ stage, ...input });
            return { command: "mock", args: [] };
          };
          return {
            buildAnalyzeInvocation: invocation("analyze"),
            buildRenderInvocation: invocation("render"),
            buildQ1TimelineAnalyzeInvocation: invocation("q1timeline"),
            runProcessWithSpawn: async () => {
              if (failAnalysis) throw new Error("No module named 'q1lens'");
              return { exitCode: 0, stdout: "", stderr: "" };
            },
          };
        }
        return originalLoad.call(this, request, parent, isMain);
      };
      const modulePath = require.resolve("../src/qbs/commands");
      delete require.cache[modulePath];
      try {
        require("../src/qbs/commands").registerCommands(
          { extensionUri: { fsPath: "C:\\extension" }, subscriptions: [] },
          { openQ1TimelineTarget: async () => undefined },
        );
        await commands["qbsTimeline.analyzeAndOpen"]();
        assert.deepEqual(panelRevealRequests, [true], "Explicit analysis must reveal the successful result");
        assert.deepEqual(invocations.map(({ stage, pythonPath, pythonArgs }) => ({ stage, pythonPath, pythonArgs })), [
          { stage: "analyze", ...selectedEnvironment },
          { stage: "render", ...selectedEnvironment },
          { stage: "q1timeline", ...selectedEnvironment },
        ]);
        assert.equal(invocations[2].q1timelineCommand, expectedQ1timelineCommand);

        activeFile = join(root, "other-project", "qbstimeline.yml");
        selectedEnvironment = { pythonPath: join(root, ".changed-venv", "bin", "python"), pythonArgs: [] };
        await commands["qbsTimeline.refresh"]();
        assert.deepEqual(invocations.slice(3).map(({ stage, pythonPath, pythonArgs }) => ({ stage, pythonPath, pythonArgs })), [
          { stage: "analyze", ...selectedEnvironment },
          { stage: "render", ...selectedEnvironment },
          { stage: "q1timeline", ...selectedEnvironment },
        ]);
        assert.equal(invocations[5].q1timelineCommand, expectedQ1timelineCommand);
        assert.deepEqual(panelRevealRequests, [true, false], "Refresh must update without revealing the panel");

        selectedEnvironment = undefined;
        await panelHandlers.onRefresh();
        assert.equal(invocations.length, 6, "An unavailable environment must not fall back to configured Python");
        assert.equal(kernelLookups, 0);
        assert.deepEqual(environmentResources, Array(3).fill(scheduleFile));
        assert.equal(configurationResources.length, 12);
        assert.equal(configurationResources.every((resource) => resource === scheduleFile), true);
        assert.equal(panelShows, 2);

        activeFile = scheduleFile;
        await commands["qbsTimeline.analyzeAndOpen"]();
        assert.deepEqual(panelRevealRequests, [true, false], "Canceled explicit analysis must not reveal the previous result");

        selectedEnvironment = { pythonPath: "selected-python", pythonArgs: [] };
        failAnalysis = true;
        await commands["qbsTimeline.analyzeAndOpen"]();
        assert.deepEqual(panelRevealRequests, [true, false], "Failed explicit analysis must not reveal the previous result");
        assert.equal(recoveryCommands[0][0], "q1lens.checkEnvironment");
        assert.equal(recoveryCommands[0][2].fsPath, scheduleFile);
      } finally {
        delete require.cache[modulePath];
        (Module as any)._load = originalLoad;
      }
    });
  }

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
    let activeFile: string | undefined;
    const analyzedProjects: string[] = [];
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
          get activeTextEditor() {
            return activeFile ? { document: { uri: { fsPath: activeFile } } } : undefined;
          },
        },
        workspace: {
          getConfiguration: () => ({ get: (_key: string, fallback: any) => fallback }),
          getWorkspaceFolder: () => ({ uri: { fsPath: root } }),
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
        if (request === "../fileEnvironment") {
          return {
            ensureFilePythonEnvironment: async (_vscode: any, resource: any) => ({
              pythonPath: "selected-python", pythonArgs: [], source: "vscode", sourceLabel: "VS Code selection", resource,
            }),
          };
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
            buildAnalyzeInvocation: (input: any) => {
              analyzedProjects.push(input.projectFile);
              return { command: "mock", args: ["analyze"] };
            },
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
      activeFile = join(projectB, "schedule.py");
      const secondRun = commands["qbsTimeline.refresh"]();
      await secondAnalyzeStarted;
      await panelHandlers.onOpenQ1Timeline({ type: "openQ1Timeline", operationId: "op" });

      assert.equal(findFilesCalls, 1);
      assert.equal(analyzedProjects[1], join(projectA, "qbstimeline.yml"));
      assert.equal(openedTargets.length, 0, "The displayed output is being regenerated, so its old source mapping is unavailable");
      releaseSecondAnalyze();
      await secondRun;
      await panelHandlers.onOpenQ1Timeline({ type: "openQ1Timeline", operationId: "op" });
      assert.equal(openedTargets[0].q1asmFile.replace(/\\/g, "/").includes("/project-a/.qbs_timeline/"), true);

      await commands["qbsTimeline.analyzeAndOpen"]();
      assert.equal(findFilesCalls, 2);
      assert.equal(analyzedProjects[2], join(projectB, "qbstimeline.yml"));
    } finally {
      delete require.cache[modulePath];
      (Module as any)._load = originalLoad;
    }
  });
});
