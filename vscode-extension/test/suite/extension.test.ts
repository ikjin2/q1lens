import assert from "node:assert/strict";
import { join, relative } from "node:path";
import * as vscode from "vscode";

async function waitFor<T>(description: string, check: () => Promise<T | undefined>): Promise<T> {
  const deadline = Date.now() + 25000;
  let lastError: unknown;
  while (Date.now() < deadline) {
    try {
      const value = await check();
      if (value !== undefined) return value;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for ${description}. Last read: ${String(lastError || "condition not met")}`);
}

describe("Q1Lens extension host", function () {
  this.timeout(60000);

  it("registers user-facing commands", async () => {
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const commands = await vscode.commands.getCommands(true);

    assert.ok(commands.includes("qbsTimeline.analyzeAndOpen"));
    assert.ok(commands.includes("qbsTimeline.refresh"));
    assert.ok(commands.includes("qbsTimeline.openIr"));
    assert.ok(commands.includes("qbsTimeline.openRenderedHtml"));
    assert.ok(commands.includes("qbsTimeline.openQ1Timeline"));
    assert.ok(commands.includes("qbsTimeline.openQ1asmFolder"));
    assert.ok(commands.includes("q1timeline.openPreview"));
    assert.ok(commands.includes("q1timeline.refreshPreview"));
  });

  it("preserves notebook cell IDs and saved tags through real notebook marking", async () => {
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
    assert.ok(workspaceRoot);
    const cellMetadata = (cell: vscode.NotebookCell) => cell.metadata.custom?.metadata ?? cell.metadata.metadata ?? cell.metadata;
    const file = vscode.Uri.joinPath(workspaceRoot, `notebook-metadata-${Date.now()}.ipynb`);
    await vscode.workspace.fs.writeFile(file, Buffer.from(JSON.stringify({
      nbformat: 4, nbformat_minor: 5, metadata: {}, cells: [
        { id: "setup-cell", cell_type: "code", source: ["compiler = object()\n"], metadata: { tags: ["keep-me"], note: "setup" }, execution_count: null, outputs: [] },
        { id: "schedule-cell", cell_type: "code", source: ["schedule = object()\n"], metadata: { note: "schedule" }, execution_count: null, outputs: [] },
      ],
    })));
    const notebook = await vscode.workspace.openNotebookDocument(file);
    await vscode.window.showNotebookDocument(notebook);
    console.log("Native notebook metadata before marking:", JSON.stringify(notebook.getCells().map((cell) => cell.metadata)));
    let marking: Thenable<unknown> | undefined;
    try {
      const { notebookCellsFromDocument } = require("../../src/qbs/commands") as typeof import("../../src/qbs/commands");
      const { createManagedNotebookTimelineProject } = require("../../src/qbs/notebookProject") as typeof import("../../src/qbs/notebookProject");
      const snapshot = JSON.parse(createManagedNotebookTimelineProject({
        notebookPath: file.fsPath, selectedCellIndex: 1, scheduleVariable: "schedule", compilerVariable: "compiler",
        cells: notebookCellsFromDocument(vscode, notebook),
      }).snapshotJson);
      assert.deepEqual(snapshot.cells.map((cell: any) => cell.id), ["setup-cell", "schedule-cell"]);
      assert.deepEqual(snapshot.cells[0].metadata.tags, ["keep-me"]);
      assert.equal(snapshot.cells[0].metadata.metadata, undefined, "A snapshot must contain ipynb metadata rather than the API wrapper");
      marking = vscode.commands.executeCommand("qbsTimeline.markNotebookSetupCell", notebook.cellAt(0));
      await waitFor("the marked notebook setup cell", async () => cellMetadata(notebook.cellAt(0)).tags?.includes("qbstimeline-setup") ? true : undefined);
      await notebook.save();
      const saved = JSON.parse(Buffer.from(await vscode.workspace.fs.readFile(file)).toString("utf8"));
      assert.deepEqual(saved.cells.map((cell: any) => cell.id), ["setup-cell", "schedule-cell"], "Marking must preserve existing cell identities");
      assert.deepEqual(saved.cells[0].metadata.tags, ["keep-me", "qbstimeline-setup"], "The serializer must save tags in cell metadata");
      assert.equal(saved.cells[0].metadata.note, "setup");
      assert.equal(saved.cells[1].metadata.note, "schedule");
    } finally {
      if (marking) {
        await vscode.commands.executeCommand("notifications.clearAll");
        await marking;
      }
      if (notebook.isDirty) await notebook.save();
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  });

  it("opens the original notebook source cell by ID after a cell is inserted", async () => {
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
    assert.ok(workspaceRoot);
    const file = vscode.Uri.joinPath(workspaceRoot, `notebook-source-${Date.now()}.ipynb`);
    await vscode.workspace.fs.writeFile(file, Buffer.from(JSON.stringify({
      nbformat: 4, nbformat_minor: 5, metadata: {}, cells: [
        { id: "setup-cell", cell_type: "code", source: ["compiler = object()\n"], metadata: {}, execution_count: null, outputs: [] },
        { id: "schedule-cell", cell_type: "code", source: ["schedule = object()\n"], metadata: {}, execution_count: null, outputs: [] },
      ],
    })));
    const notebook = await vscode.workspace.openNotebookDocument(file);
    const { openSourceTarget, resolveScheduleSourceTarget } = require("../../src/qbs/commands") as typeof import("../../src/qbs/commands");
    const target = resolveScheduleSourceTarget({
      ir: { operations: [], symbolic_values: [], symbolic_pulses: [], q1asm_programs: [], q1asm_provenance: [],
        source_map: { schedulables: { selected: { notebook: { file: file.fsPath, cell_index: 1, cell_id: "schedule-cell", cell_line: 1 } } } } },
      sourceNotebookFile: file.fsPath, selection: { schedulableId: "selected" },
    });
    try {
      const edit = new vscode.WorkspaceEdit();
      edit.set(file, [vscode.NotebookEdit.insertCells(0, [new vscode.NotebookCellData(vscode.NotebookCellKind.Markup, "Inserted notes", "markdown")])]);
      assert.equal(await vscode.workspace.applyEdit(edit), true);
      assert.equal(notebook.cellAt(2).metadata.id ?? notebook.cellAt(2).metadata.custom?.id, "schedule-cell");
      await openSourceTarget(target);
      await waitFor("the original notebook source text editor", async () => vscode.window.activeTextEditor?.document === notebook.cellAt(2).document ? true : undefined);
      assert.equal(vscode.window.activeTextEditor?.selection.active.line, 0);
    } finally {
      if (notebook.isDirty) await notebook.save();
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  });

  it("executes a configured QBS task after expanding workspace variables", async () => {
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const folder = vscode.workspace.workspaceFolders?.[0];
    assert.ok(folder);
    const tasksFile = vscode.Uri.joinPath(folder.uri, ".vscode", "tasks.json");
    let original: Uint8Array | undefined;
    try { original = await vscode.workspace.fs.readFile(tasksFile); }
    catch (error) { if (!(error instanceof vscode.FileSystemError) || error.code !== "FileNotFound") throw error; }
    const label = `Q1Lens variable regression ${Date.now()}`;
    const output = `task-output-${Date.now()}`;
    let execution: vscode.TaskExecution | undefined;
    let ended = false;
    const subscription = vscode.tasks.onDidEndTask((event) => { if (event.execution.task.name === label) ended = true; });
    try {
      await vscode.workspace.fs.writeFile(tasksFile, Buffer.from(JSON.stringify({ version: "2.0.0", tasks: [{
        label, type: "qbs-timeline", command: "analyze",
        project: "${workspaceFolder}/examples/two-qubit-entangling/qbstimeline.yml", outputDir: output,
        presentation: { reveal: "never", focus: false },
      }] })));
      const task = await waitFor("the configured variable task", async () => (
        (await vscode.tasks.fetchTasks({ type: "qbs-timeline" })).find((candidate) => candidate.name === label)
      ));
      execution = await vscode.tasks.executeTask(task);
      await waitFor("the variable task to finish", async () => ended ? true : undefined);
      const artifact = vscode.Uri.joinPath(folder.uri, "examples", "two-qubit-entangling", output, "qbs_ir.json");
      const ir = JSON.parse(Buffer.from(await vscode.workspace.fs.readFile(artifact)).toString("utf8"));
      assert.ok(ir.operations.length > 0);
      assert.ok(ir.q1asm_programs.length > 0);
      const artifactRoot = vscode.Uri.joinPath(folder.uri, "examples", "two-qubit-entangling", output);
      for (const program of ir.q1asm_programs) {
        assert.ok((await vscode.workspace.fs.stat(vscode.Uri.joinPath(artifactRoot, program.file))).size > 0,
          "Generated Q1ASM must be available relative to the task IR");
      }
      await vscode.workspace.fs.stat(vscode.Uri.joinPath(artifactRoot, "q1timeline.yml"));
    } finally {
      subscription.dispose();
      if (!ended) execution?.terminate();
      if (original) await vscode.workspace.fs.writeFile(tasksFile, original);
      else await vscode.workspace.fs.delete(tasksFile);
    }
  });

  it("reuses a discovered QBS task with current output settings and YAML for IR, HTML and generated sources", async () => {
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const folder = vscode.workspace.workspaceFolders?.[0];
    assert.ok(folder);
    const project = vscode.Uri.joinPath(folder.uri, "examples", "two-qubit-entangling", "qbstimeline.yml");
    const config = vscode.workspace.getConfiguration("q1lens", project);
    const original = config.inspect("qbs.outputDirOverride")?.workspaceFolderValue;
    const originalProject = await vscode.workspace.fs.readFile(project);
    const yamlOutput = `yaml-reused-task-${Date.now()}`;
    let execution: vscode.TaskExecution | undefined;
    let ended = false;
    let subscription: vscode.Disposable | undefined;
    try {
      await config.update("qbs.outputDirOverride", null, vscode.ConfigurationTarget.WorkspaceFolder);
      const task = await waitFor("the discovered combined task", async () => (
        (await vscode.tasks.fetchTasks({ type: "qbs-timeline" })).find((candidate) =>
          candidate.definition.command === "analyzeAndRender" &&
          candidate.definition.project.replace(/\\/g, "/") === "examples/two-qubit-entangling/qbstimeline.yml")
      ));
      subscription = vscode.Disposable.from(
        vscode.tasks.onDidStartTask((event) => { if (event.execution.task.name === task.name) execution = event.execution; }),
        vscode.tasks.onDidEndTask((event) => { if (event.execution.task.name === task.name) ended = true; }),
      );
      for (const [index, output] of [`reused-task-${Date.now()}`, null, yamlOutput].entries()) {
        await config.update("qbs.outputDirOverride", index === 2 ? null : output, vscode.ConfigurationTarget.WorkspaceFolder);
        if (index === 2) {
          const originalText = Buffer.from(originalProject).toString("utf8");
          const updated = originalText.replace("dir: .qbs_timeline", `dir: ${yamlOutput}`);
          assert.notEqual(updated, originalText);
          await vscode.workspace.fs.writeFile(project, Buffer.from(updated));
        }
        ended = false;
        if (index === 2) await vscode.commands.executeCommand("workbench.action.tasks.reRunTask");
        else execution = await vscode.tasks.executeTask(task);
        await waitFor("the reused task to finish", async () => ended ? true : undefined);
        const root = vscode.Uri.joinPath(folder.uri, "examples", "two-qubit-entangling", output ?? ".qbs_timeline");
        const ir = JSON.parse(Buffer.from(await vscode.workspace.fs.readFile(vscode.Uri.joinPath(root, "qbs_ir.json"))).toString("utf8"));
        assert.ok(ir.operations.length > 0);
        assert.ok(ir.q1asm_programs.length > 0);
        assert.ok((await vscode.workspace.fs.stat(vscode.Uri.joinPath(root, "index.html"))).size > 0);
        for (const program of ir.q1asm_programs) {
          assert.ok((await vscode.workspace.fs.stat(vscode.Uri.joinPath(root, program.file))).size > 0);
        }
        await vscode.workspace.fs.stat(vscode.Uri.joinPath(root, "q1timeline.yml"));
      }
    } finally {
      subscription?.dispose();
      if (!ended) execution?.terminate();
      await vscode.workspace.fs.writeFile(project, originalProject);
      await config.update("qbs.outputDirOverride", original, vscode.ConfigurationTarget.WorkspaceFolder);
    }
  });

  it("keeps QBS generated sources and inline analysis in the configured output override", async () => {
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
    assert.ok(workspaceRoot);
    const project = vscode.Uri.joinPath(workspaceRoot, "examples", "two-qubit-entangling", "qbstimeline.yml");
    const config = vscode.workspace.getConfiguration("q1lens", project);
    const original = config.inspect("qbs.outputDirOverride")?.workspaceFolderValue;
    const output = `override-output-${Date.now()}`;
    const artifactRoot = vscode.Uri.joinPath(workspaceRoot, "examples", "two-qubit-entangling", output);
    try {
      await config.update("qbs.outputDirOverride", output, vscode.ConfigurationTarget.WorkspaceFolder);
      await vscode.window.showTextDocument(project);
      await vscode.commands.executeCommand("qbsTimeline.analyzeAndOpen");
      const ir = JSON.parse(Buffer.from(await vscode.workspace.fs.readFile(vscode.Uri.joinPath(artifactRoot, "qbs_ir.json"))).toString("utf8"));
      assert.ok(ir.q1asm_programs.length > 0);
      for (const program of ir.q1asm_programs) {
        assert.ok((await vscode.workspace.fs.stat(vscode.Uri.joinPath(artifactRoot, program.file))).size > 0);
      }
      await vscode.workspace.fs.stat(vscode.Uri.joinPath(artifactRoot, "q1timeline.yml"));
      const inline = JSON.parse(Buffer.from(await vscode.workspace.fs.readFile(vscode.Uri.joinPath(artifactRoot, ".q1timeline", "timeline_ir.json"))).toString("utf8"));
      assert.ok(inline.events.length > 0, "The inline analyzer must use Q1ASM generated in the overridden directory");
      assert.ok(!vscode.languages.getDiagnostics(project).some((diagnostic) => diagnostic.source === "qbsTimeline" && diagnostic.code === "QBST005"));
    } finally {
      await config.update("qbs.outputDirOverride", original, vscode.ConfigurationTarget.WorkspaceFolder);
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  });

  it("analyzes the two-qubit example and reopens its hidden timeline without moving it", async () => {
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    assert.ok(workspaceRoot);

    const projectUri = vscode.Uri.file(join(workspaceRoot, "examples", "two-qubit-entangling", "qbstimeline.yml"));
    await vscode.window.showTextDocument(projectUri);
    const groupCountBeforeOpen = vscode.window.tabGroups.all.length;
    await vscode.commands.executeCommand("qbsTimeline.analyzeAndOpen");
    await waitFor("the QBS timeline tab", async () => vscode.window.tabGroups.activeTabGroup.activeTab?.input instanceof vscode.TabInputWebview ? true : undefined);
    assert.equal(vscode.window.tabGroups.all.length, groupCountBeforeOpen + 1, "Opening QBS should add one timeline group");

    const outputUri = vscode.Uri.file(join(workspaceRoot, "examples", "two-qubit-entangling", ".qbs_timeline", "qbs_ir.json"));
    await vscode.workspace.fs.stat(outputUri);
    const diagnostics = vscode.languages.getDiagnostics(projectUri).filter((diagnostic) => diagnostic.source === "qbsTimeline");
    assert.equal(diagnostics.length, 0, diagnostics.map((diagnostic) => `${diagnostic.code}: ${diagnostic.message}`).join("\n"));

    const timelineGroup = vscode.window.tabGroups.activeTabGroup;
    const timelineTab = timelineGroup.activeTab;
    assert.ok(timelineTab?.input instanceof vscode.TabInputWebview);
    const timelineViewType = timelineTab.input.viewType;
    const timelineColumn = timelineGroup.viewColumn;
    await vscode.window.showTextDocument(projectUri, { viewColumn: timelineColumn });
    await waitFor("the source tab hiding QBS", async () => timelineGroup.activeTab?.input instanceof vscode.TabInputText ? true : undefined);
    await vscode.commands.executeCommand("qbsTimeline.refresh");
    assert.ok(timelineGroup.activeTab?.input instanceof vscode.TabInputText, "Refresh must keep the source editor active");

    await vscode.commands.executeCommand("qbsTimeline.analyzeAndOpen");
    await waitFor("the reopened QBS tab", async () => {
      const input = vscode.window.tabGroups.activeTabGroup.activeTab?.input;
      return input instanceof vscode.TabInputWebview && input.viewType === timelineViewType ? true : undefined;
    });
    assert.equal(vscode.window.tabGroups.activeTabGroup.viewColumn, timelineColumn);
    const timelineTabs = vscode.window.tabGroups.all.flatMap((group) => group.tabs)
      .filter((tab) => tab.input instanceof vscode.TabInputWebview && tab.input.viewType === timelineViewType);
    assert.equal(timelineTabs.length, 1, "Explicit open must reuse the existing timeline tab");
  });

  it("withdraws a stale quick fix after another line is saved and restores it after Refresh", async () => {
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const folder = vscode.workspace.workspaceFolders?.[0];
    assert.ok(folder);
    const root = vscode.Uri.joinPath(folder.uri, `quick-fix-${Date.now()}`);
    const source = vscode.Uri.joinPath(root, "seq0.q1asm");
    const project = vscode.Uri.joinPath(root, "q1timeline.yml");
    await vscode.workspace.fs.createDirectory(root);
    await vscode.workspace.fs.writeFile(source, Buffer.from("set_freq 100\nwait 4\nstop\n"));
    await vscode.workspace.fs.writeFile(project, Buffer.from("sequencers:\n  - id: seq0\n    file: seq0.q1asm\n"));
    const config = vscode.workspace.getConfiguration("q1lens", source);
    const keys = ["q1timeline.updateMode", "q1timeline.analyzer.extraArgs"];
    const original = keys.map((key) => config.inspect(key)?.workspaceFolderValue);
    const title = "Apply pending hardware state before stop";
    const pending = () => vscode.languages.getDiagnostics(source).some((item) => item.source === "q1timeline" &&
      String(typeof item.code === "object" ? item.code.value : item.code).startsWith("pending_hardware_state"));
    const fixes = async () => ((await vscode.commands.executeCommand<vscode.CodeAction[]>("vscode.executeCodeActionProvider",
      source, new vscode.Range(2, 0, 2, 4), vscode.CodeActionKind.QuickFix.value)) ?? []).filter((action) => action.title === title);
    try {
      await config.update(keys[0], "manual", vscode.ConfigurationTarget.WorkspaceFolder);
      await config.update(keys[1], ["--hardware-readiness"], vscode.ConfigurationTarget.WorkspaceFolder);
      const document = await vscode.workspace.openTextDocument(source);
      await vscode.languages.setTextDocumentLanguage(document, "q1asm");
      await vscode.window.showTextDocument(document);
      await vscode.commands.executeCommand("q1timeline.openPreview", source);
      await waitFor("the hardware-readiness warning and quick fix", async () => pending() && (await fixes()).length === 1 ? true : undefined);
      for (const instruction of ["upd_param 4", "wait 4"]) {
        const edit = new vscode.WorkspaceEdit();
        edit.replace(source, new vscode.Range(1, 0, 1, document.lineAt(1).text.length), instruction);
        assert.equal(await vscode.workspace.applyEdit(edit), true);
        await document.save();
        assert.equal(document.lineAt(2).text, "stop");
        assert.equal(document.isDirty, false);
        assert.equal(pending(), true, "Manual mode retains the previous diagnostic until Refresh");
        assert.equal((await fixes()).length, 0, "A saved edit must withdraw the old insertion even when stop is unchanged");
      }
      await vscode.commands.executeCommand("q1timeline.refreshPreview");
      await waitFor("a fresh quick fix after explicit Refresh", async () => pending() && (await fixes()).length === 1 ? true : undefined);
    } finally {
      for (let index = 0; index < keys.length; index += 1) await config.update(keys[index], original[index], vscode.ConfigurationTarget.WorkspaceFolder);
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  });

  for (const scenario of ["03-signals", "04-underflow"]) {
    it(`opens ${scenario} through the registered Q1ASM preview command`, async () => {
      await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
      const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      assert.ok(workspaceRoot);
      const projectRoot = join(workspaceRoot, "examples", "simulation-verification", scenario);
      const sourceFile = scenario === "03-signals" ? "controller.q1asm" : "starved.q1asm";
      const sourceUri = vscode.Uri.file(join(projectRoot, sourceFile));
      await vscode.window.showTextDocument(sourceUri);
      await vscode.commands.executeCommand("q1timeline.openPreview", sourceUri);

      const outputRoot = join(projectRoot, ".q1timeline");
      const ir = JSON.parse(Buffer.from(await vscode.workspace.fs.readFile(vscode.Uri.file(join(outputRoot, "timeline_ir.json")))).toString("utf8"));
      const html = Buffer.from(await vscode.workspace.fs.readFile(vscode.Uri.file(join(outputRoot, "timeline.html")))).toString("utf8");
      assert.ok(html.includes("timeline"), "registered command should render a preview");
      if (scenario === "03-signals") {
        assert.equal(ir.execution.controller.mode, "simulation");
        assert.equal(ir.execution.controller.status, "completed");
        assert.equal(ir.execution.controller.timing_model, "event_driven");
        const writes = ir.register_history.filter((item: any) => item.sequencer_id === "controller" && item.register === "R0");
        assert.equal(writes[writes.length - 1].value.value, 3);
        assert.ok(ir.simulation.readout.signals.traces.some((trace: any) => trace.stage === "adc"));
        assert.ok(ir.events.some((event: any) => event.kind === "acquisition_result" && event.meta.thresholded));
        const hardware = require("../../src/q1timeline/media/hardwareState.js");
        const hardwareIndex = hardware.createIndex(ir);
        const state = hardware.snapshot(hardwareIndex, "readout", ir.simulation.readout.end_time_ns);
        assert.equal(state.bins.total, 1);
        assert.equal(state.bins.records[0].result.thresholded, 1);
        assert.ok(!state.warnings.includes("Some acquisition targets are unresolved."));

        const previewGroup = await waitFor("the Q1ASM preview tab", async () => {
          const group = vscode.window.tabGroups.activeTabGroup;
          const input = group.activeTab?.input;
          return input instanceof vscode.TabInputWebview && input.viewType.includes("q1timeline.preview") ? group : undefined;
        });
        const previewColumn = previewGroup.viewColumn;
        const sourceGroup = vscode.window.tabGroups.all.find((group) => group.viewColumn !== previewColumn);
        assert.ok(sourceGroup, "The source editor should remain in a separate group");
        await vscode.window.showTextDocument(sourceUri, { viewColumn: sourceGroup.viewColumn });
        await vscode.commands.executeCommand("q1timeline.openPreview", sourceUri);
        await waitFor("the reopened Q1ASM preview tab", async () => {
          const input = vscode.window.tabGroups.activeTabGroup.activeTab?.input;
          return input instanceof vscode.TabInputWebview && input.viewType.includes("q1timeline.preview") ? true : undefined;
        });
        assert.equal(vscode.window.tabGroups.activeTabGroup.viewColumn, previewColumn, "Q1ASM reopening must retain its original group");
      } else {
        assert.equal(ir.execution.starved.mode, "simulation");
        assert.equal(ir.execution.starved.status, "error");
        assert.ok(ir.events.some((event: any) => event.kind === "underflow_warning" && event.meta.simulated));
        const diagnostics = vscode.languages.getDiagnostics(sourceUri);
        assert.ok(diagnostics.some((diagnostic) => diagnostic.severity === vscode.DiagnosticSeverity.Error));
      }
    });
  }

  describe("real workspace file and settings lifecycle", function () {
    this.timeout(60000);
    let workspaceRoot: string;
    let fixtureRoot: string;
    let fixtureNumber = 0;
    const originalInputs = new Map<string, Uint8Array>();
    const settings = ["q1timeline.updateMode", "q1timeline.executionMode", "q1timeline.maxSteps"];
    const originalSettings = new Map<string, unknown>();

    const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
    const uri = (path: string) => vscode.Uri.file(path);
    const readText = async (path: string) => Buffer.from(await vscode.workspace.fs.readFile(uri(path))).toString("utf8");
    const outputPath = (project: string, name = "timeline_ir.json") => join(project, ".q1timeline", name);
    const configure = (key: string, value: unknown) => vscode.workspace.getConfiguration("q1lens").update(key, value, vscode.ConfigurationTarget.Global);
    const lastRegister = (ir: any, register: string) => ir.register_history?.filter((write: any) => write.register === register).at(-1)?.value?.value;
    const acquisition = (ir: any) => ir.simulation?.readout?.signals?.acquisitions?.[0];

    async function waitForIr(project: string, description: string, matches: (ir: any) => boolean): Promise<any> {
      return waitFor(description, async () => {
        const irPath = outputPath(project);
        const ir = JSON.parse(await readText(irPath));
        if (!matches(ir)) return undefined;
        const [irStat, htmlStat] = await Promise.all([
          vscode.workspace.fs.stat(uri(irPath)),
          vscode.workspace.fs.stat(uri(outputPath(project, "timeline.html"))),
        ]);
        // A current IR without its corresponding render is still an in-flight run.
        if (htmlStat.mtime < irStat.mtime) return undefined;
        return ir;
      });
    }

    async function copyFixture(scenario: string): Promise<string> {
      const source = join(workspaceRoot, "examples", "simulation-verification", scenario);
      const target = join(fixtureRoot, `${++fixtureNumber}-${scenario}`);
      await vscode.workspace.fs.createDirectory(uri(target));
      for (const [name, kind] of await vscode.workspace.fs.readDirectory(uri(source))) {
        if (kind !== vscode.FileType.File || !/\.(q1asm|json|ya?ml)$/.test(name)) continue;
        const path = join(source, name);
        const bytes = await vscode.workspace.fs.readFile(uri(path));
        originalInputs.set(path, bytes);
        await vscode.workspace.fs.writeFile(uri(join(target, name)), bytes);
      }
      return target;
    }

    async function open(project: string, filename: string): Promise<void> {
      const source = uri(join(project, filename));
      await vscode.window.showTextDocument(source, { preview: false });
      await vscode.commands.executeCommand("q1timeline.openPreview", source);
    }

    async function save(path: string, change: (text: string) => string): Promise<void> {
      const document = await vscode.workspace.openTextDocument(uri(path));
      const previous = document.getText();
      const edit = new vscode.WorkspaceEdit();
      edit.replace(document.uri, new vscode.Range(document.positionAt(0), document.positionAt(previous.length)), change(previous));
      assert.equal(await vscode.workspace.applyEdit(edit), true);
      assert.equal(await document.save(), true);
    }

    async function refreshFresh(project: string): Promise<void> {
      // Remove only generated artifacts in this test-owned fixture. An old result
      // cannot satisfy a refresh assertion even if the expected mode is unchanged.
      for (const name of ["timeline_ir.json", "timeline.html"]) {
        try { await vscode.workspace.fs.delete(uri(outputPath(project, name))); } catch (error) {
          if (!(error instanceof vscode.FileSystemError) || error.code !== "FileNotFound") throw error;
        }
      }
      await vscode.commands.executeCommand("q1timeline.refreshPreview");
    }

    before(async () => {
      const folder = vscode.workspace.workspaceFolders?.[0];
      assert.ok(folder);
      workspaceRoot = folder.uri.fsPath;
      fixtureRoot = join(workspaceRoot, "host-fixtures", `${Date.now()}-${process.pid}`);
      await vscode.workspace.fs.createDirectory(uri(fixtureRoot));
      const configuration = vscode.workspace.getConfiguration("q1lens");
      for (const key of settings) originalSettings.set(key, configuration.inspect(key)?.globalValue);
      console.log(`Real host lifecycle fixture directory: ${fixtureRoot}`);
    });

    beforeEach(async () => {
      await configure("q1timeline.updateMode", "manual");
      await configure("q1timeline.executionMode", "project");
      await configure("q1timeline.maxSteps", 10000);
    });

    after(async () => {
      await configure("q1timeline.updateMode", "manual");
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
      for (const key of settings) await configure(key, originalSettings.get(key));
      for (const [path, original] of originalInputs) {
        assert.deepEqual(await vscode.workspace.fs.readFile(uri(path)), original, `Original fixture changed: ${path}`);
      }
    });

    it("switches the active preview from project A to B without stale sequencers", async () => {
      const a = await copyFixture("03-signals");
      const b = await copyFixture("05-supplied-input");
      await open(a, "controller.q1asm");
      await waitForIr(a, "project A controller result", (ir) => lastRegister(ir, "R0") === 3);
      await open(b, "readout.q1asm");
      const result = await waitForIr(b, "project B supplied input", (ir) => acquisition(ir)?.i === 1);
      assert.deepEqual(result.sequencers.map((item: any) => item.id), ["readout"]);
      assert.equal(result.execution.controller, undefined);
      assert.equal(acquisition(result).source.kind, "supplied_trace");
      assert.ok(result.events.every((event: any) => !event.source?.file || relative(b, event.source.file) === "readout.q1asm"));
    });

    it("automatically refreshes after a project YAML save", async () => {
      const project = await copyFixture("05-supplied-input");
      await open(project, "readout.q1asm");
      await waitForIr(project, "initial threshold", (ir) => acquisition(ir)?.threshold === 0.8);
      await configure("q1timeline.updateMode", "onSave");
      await save(join(project, "q1timeline.yml"), (text) => text.replace("threshold: 0.8", "threshold: 1.2"));
      const result = await waitForIr(project, "saved YAML threshold", (ir) => acquisition(ir)?.threshold === 1.2);
      assert.equal(acquisition(result).thresholded, 0);
      assert.equal(acquisition(result).i, 1);
    });

    it("analyzes a real editor save once and still follows external changes and explicit Refresh", async () => {
      const project = await copyFixture("01-digital");
      const sourcePath = join(project, "counter.q1asm");
      await open(project, "counter.q1asm");
      await waitForIr(project, "initial save-count counter", (ir) => lastRegister(ir, "R2") === 3);
      await configure("q1timeline.updateMode", "onSave");
      const source = uri(sourcePath);
      const launches: string[] = [];
      let saves = 0;
      let watches = 0;
      const textEvents: Array<{ version: number; dirty: boolean; changes: number }> = [];
      const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(project, "counter.q1asm"));
      const subscriptions = [
        watcher,
        watcher.onDidChange(() => { watches += 1; }),
        vscode.workspace.onDidSaveTextDocument((document) => { if (document.uri.toString() === source.toString()) saves += 1; }),
        vscode.workspace.onDidChangeTextDocument((event) => {
          if (event.document.uri.toString() === source.toString()) textEvents.push({
            version: event.document.version, dirty: event.document.isDirty, changes: event.contentChanges.length,
          });
        }),
      ];
      const childProcess = require("node:child_process");
      const originalExecFile = childProcess.execFile;
      childProcess.execFile = function (command: string, args: string[], ...rest: any[]) {
        if (Array.isArray(args) && args.includes("q1timeline")) {
          if (args.includes("analyze") && args.includes(join(project, "q1timeline.yml"))) launches.push("analyzer");
          if (args.includes("render") && args.includes(outputPath(project))) launches.push("renderer");
        }
        return originalExecFile.call(this, command, args, ...rest);
      };
      const count = (stage: string) => launches.filter((entry) => entry === stage).length;
      try {
        await save(sourcePath, (text) => text.replace("move 3,R0", "move 4,R0"));
        await waitFor("both native save and watcher events", async () => saves > 0 && watches > 0 ? true : undefined);
        await waitForIr(project, "saved counter without duplicate analysis", (ir) => lastRegister(ir, "R2") === 4);
        await pause(600);
        console.log("Native editor save analysis counts:", JSON.stringify({ saves, watches, launches, textEvents }));
        assert.equal(count("analyzer"), 1, "One editor save must launch one analyzer despite both native events");
        assert.equal(count("renderer"), 1);

        const previousWatches = watches;
        await vscode.workspace.fs.writeFile(source, Buffer.from((await readText(sourcePath)).replace("move 4,R0", "move 5,R0")));
        await waitFor("the native external-change event", async () => watches > previousWatches ? true : undefined);
        await waitForIr(project, "externally updated counter", (ir) => lastRegister(ir, "R2") === 5);
        await waitFor("the external edit in the open document", async () => (
          (await vscode.workspace.openTextDocument(source)).getText().includes("move 5,R0") ? true : undefined
        ));
        await pause(600);
        assert.equal(count("analyzer"), 2);
        assert.equal(count("renderer"), 2);

        await vscode.commands.executeCommand("q1timeline.refreshPreview");
        assert.equal(count("analyzer"), 3, "Explicit Refresh must run even when the disk bytes are unchanged");
        assert.equal(count("renderer"), 3);
        await waitForIr(project, "explicit same-content refresh", (ir) => lastRegister(ir, "R2") === 5);
      } finally {
        childProcess.execFile = originalExecFile;
        for (const subscription of subscriptions) subscription.dispose();
        await configure("q1timeline.updateMode", "manual");
      }
    });

    it("automatically refreshes changed waveform memory after a sequence JSON save", async () => {
      const project = await copyFixture("03-signals");
      await open(project, "controller.q1asm");
      await waitForIr(project, "initial waveform", (ir) => acquisition(ir)?.i === 31.953125);
      await configure("q1timeline.updateMode", "onSave");
      await save(join(project, "drive.sequence.json"), (text) => {
        const sequence = JSON.parse(text);
        sequence.waveforms.flat.data.fill(0.5);
        return JSON.stringify(sequence, null, 2);
      });
      const result = await waitForIr(project, "saved waveform memory", (ir) => {
        const waveform = ir.simulation?.drive?.signals?.traces?.find((item: any) => item.stage === "waveform" && item.path === "i");
        return waveform?.samples[204] === 0.5;
      });
      const gained = result.simulation.drive.signals.traces.find((item: any) => item.stage === "gain_offset" && item.path === "i");
      assert.equal(gained.samples[204], 0.25);
      assert.equal(acquisition(result).thresholded, 0);
      // A direct filesystem write emits no TextDocument.save call, exercising
      // the real workspace watcher in addition to the document-save handler.
      const memoryPath = join(project, "drive.sequence.json");
      const sequence = JSON.parse(await readText(memoryPath));
      sequence.waveforms.flat.data.fill(0.25);
      await vscode.workspace.fs.writeFile(uri(memoryPath), Buffer.from(JSON.stringify(sequence, null, 2)));
      await waitForIr(project, "external waveform file change", (ir) => {
        const waveform = ir.simulation?.drive?.signals?.traces?.find((item: any) => item.stage === "waveform" && item.path === "i");
        return waveform?.samples[204] === 0.25;
      });
    });

    it("coalesces a native watcher delivered after the saved file's analyzer has started", async () => {
      const project = await copyFixture("01-digital");
      const sourcePath = join(project, "counter.q1asm");
      const extension = vscode.extensions.getExtension("q1lens.q1lens");
      assert.ok(extension);
      const subscriptions: vscode.Disposable[] = [];
      const stored = new Map<string, unknown>();
      const { TimelineController } = require("../../src/q1timeline/controller");
      const controller = new TimelineController({
        extensionPath: extension.extensionPath, extensionUri: extension.extensionUri, subscriptions,
        workspaceState: { get: (key: string, fallback: unknown) => stored.get(key) ?? fallback,
          update: async (key: string, value: unknown) => { stored.set(key, value); } },
      });
      let startAnalyzer!: () => void;
      const analyzerStarted = new Promise<void>((resolve) => { startAnalyzer = resolve; });
      let sawWatch = false;
      const launches: string[] = [];
      try {
        await vscode.window.showTextDocument(uri(sourcePath), { preview: false });
        await controller.openPreview({ sourceUri: uri(sourcePath) });
        await waitForIr(project, "initial delayed-watch counter", (ir) => lastRegister(ir, "R2") === 3);
        await configure("q1timeline.updateMode", "onSave");
        const onWatchedFile = controller.onWatchedFile.bind(controller);
        controller.onWatchedFile = async (changed: vscode.Uri, change?: string) => {
          if (change !== "save" && changed.toString() === uri(sourcePath).toString()) {
            sawWatch = true;
            await analyzerStarted;
          }
          return onWatchedFile(changed, change);
        };
        const execFile = controller.execFile.bind(controller);
        controller.execFile = (...args: any[]) => {
          launches.push(args[5]);
          if (args[5] === "analyzer") startAnalyzer();
          return execFile(...args);
        };
        await save(sourcePath, (text) => text.replace("move 3,R0", "move 4,R0"));
        await waitFor("the delayed native watcher", async () => sawWatch && launches.includes("analyzer") ? true : undefined);
        await waitForIr(project, "delayed-watch final counter", (ir) => lastRegister(ir, "R2") === 4);
        await waitFor("the delayed-watch analysis to settle", async () => !controller.analysisInFlight && !controller.analysisQueued ? true : undefined);
        console.log("Delayed native watcher analysis counts:", JSON.stringify(launches));
        assert.equal(launches.filter((stage) => stage === "analyzer").length, 1);
        assert.equal(launches.filter((stage) => stage === "renderer").length, 1);
        await vscode.workspace.fs.writeFile(uri(sourcePath), Buffer.from((await readText(sourcePath)).replace("move 4,R0", "move 5,R0")));
        await waitFor("the delayed-watch document reload", async () => (
          (await vscode.workspace.openTextDocument(uri(sourcePath))).getText().includes("move 5,R0") ? true : undefined
        ));
        await waitForIr(project, "external update after coalescing", (ir) => lastRegister(ir, "R2") === 5);
        await waitFor("external analysis after coalescing", async () => !controller.analysisInFlight && !controller.analysisQueued ? true : undefined);
        assert.equal(controller.sourceRevision, controller.analyzedSourceRevision, "The externally reloaded document must remain fresh for source navigation");
        assert.equal(launches.filter((stage) => stage === "analyzer").length, 2);
        assert.equal(launches.filter((stage) => stage === "renderer").length, 2);
        const document = await vscode.workspace.openTextDocument(uri(sourcePath));
        await vscode.window.showTextDocument(document, { preview: false });
        const revision = controller.sourceRevision;
        try {
          const edit = new vscode.WorkspaceEdit();
          edit.replace(document.uri, new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)),
            document.getText().replace("move 5,R0", "move 6,R0"));
          assert.equal(await vscode.workspace.applyEdit(edit), true);
          await waitFor("an unsaved edit after the snapshot was cached", async () => document.isDirty ? true : undefined);
          await pause(100);
          assert.ok(controller.sourceRevision > revision, "A real unsaved edit must invalidate the source even before the dirty notification arrives");
        } finally {
          await save(sourcePath, (text) => text.replace("move 6,R0", "move 5,R0"));
        }
        await waitFor("restored saved source freshness", async () => !controller.analysisInFlight &&
          controller.sourceRevision === controller.analyzedSourceRevision ? true : undefined);
      } finally {
        startAnalyzer();
        controller.dispose();
        for (const subscription of subscriptions) subscription.dispose();
        await configure("q1timeline.updateMode", "manual");
      }
    });

    it("keeps manual mode unchanged until the public Refresh command", async () => {
      const project = await copyFixture("01-digital");
      await open(project, "counter.q1asm");
      await waitForIr(project, "initial counter", (ir) => lastRegister(ir, "R2") === 3);
      const originalIr = await readText(outputPath(project));
      await save(join(project, "counter.q1asm"), (text) => text.replace("move 3,R0", "move 4,R0"));
      await pause(600);
      assert.equal(await readText(outputPath(project)), originalIr, "manual save must not run analysis");
      await refreshFresh(project);
      const result = await waitForIr(project, "manual refreshed counter", (ir) => lastRegister(ir, "R2") === 4);
      assert.equal(result.execution.counter.status, "completed");
    });

    it("retains the newest of rapid successive saves after queued analysis", async () => {
      const project = await copyFixture("01-digital");
      await open(project, "counter.q1asm");
      await waitForIr(project, "initial rapid-edit counter", (ir) => lastRegister(ir, "R2") === 3);
      await configure("q1timeline.updateMode", "onSave");
      for (const count of [4, 5, 6]) {
        await save(join(project, "counter.q1asm"), (text) => text.replace(/move \d+,R0/, `move ${count},R0`));
      }
      await waitForIr(project, "final rapid-save value", (ir) => lastRegister(ir, "R2") === 6);
      await pause(800);
      const result = await waitForIr(project, "stable newest result", (ir) => lastRegister(ir, "R2") === 6);
      assert.deepEqual(result.register_history.filter((write: any) => write.register === "R1").map((write: any) => write.value.value), [0, 1, 2, 3, 4, 5, 6]);
    });

    it("clears generated artifacts on configuration failure and recovers on save", async () => {
      const project = await copyFixture("01-digital");
      const projectUri = uri(join(project, "q1timeline.yml"));
      await open(project, "counter.q1asm");
      await waitForIr(project, "pre-failure output", (ir) => lastRegister(ir, "R2") === 3);
      await configure("q1timeline.updateMode", "onSave");
      await save(projectUri.fsPath, (text) => text.replace("queue_capacity: 2", "queue_capacity: 0"));
      await waitFor("configuration error in real Problems collection", async () => {
        const diagnostics = vscode.languages.getDiagnostics(projectUri);
        return diagnostics.some((item) => item.source === "q1timeline" && item.severity === vscode.DiagnosticSeverity.Error && String(item.code).startsWith("invalid_simulation_config")) ? true : undefined;
      });
      for (const name of ["timeline_ir.json", "timeline.html"]) {
        await assert.rejects(async () => vscode.workspace.fs.stat(uri(outputPath(project, name))), "failure must not expose old generated artifacts");
      }
      await save(projectUri.fsPath, (text) => text.replace("queue_capacity: 0", "queue_capacity: 2"));
      await waitForIr(project, "recovered current output", (ir) => ir.execution?.counter?.status === "completed" && lastRegister(ir, "R2") === 3);
      await waitFor("cleared failure diagnostics", async () => vscode.languages.getDiagnostics(projectUri).some((item) => item.source === "q1timeline" && item.severity === vscode.DiagnosticSeverity.Error) ? undefined : true);
    });

    it("honors all project modes and explicit execution overrides with their step budgets", async () => {
      const project = await copyFixture("01-digital");
      await open(project, "counter.q1asm");
      await waitForIr(project, "initial mode", (ir) => ir.execution?.counter?.mode === "simulation");
      const matchesMode = (ir: any, mode: string) => mode === "analysis"
        ? !ir.execution && ir.events.some((event: any) => event.kind === "loop_block")
        : ir.execution?.counter?.mode === mode && ir.execution.counter.status === "completed";
      for (const mode of ["analysis", "concrete", "simulation"]) {
        await save(join(project, "q1timeline.yml"), (text) => text.replace(/execution_mode: \w+/, `execution_mode: ${mode}`));
        await refreshFresh(project);
        await waitForIr(project, `project mode ${mode}`, (ir) => matchesMode(ir, mode));
      }
      for (const mode of ["analysis", "concrete", "simulation"]) {
        await configure("q1timeline.executionMode", mode);
        await refreshFresh(project);
        const result = await waitForIr(project, `override mode ${mode}`, (ir) => matchesMode(ir, mode));
        if (mode !== "analysis") assert.equal(result.execution.counter.max_steps, 10000);
      }
      await configure("q1timeline.executionMode", "concrete");
      await configure("q1timeline.maxSteps", 4);
      await refreshFresh(project);
      await waitForIr(project, "override bounded execution", (ir) => ir.execution?.counter?.status === "step_limit" && ir.execution.counter.steps === 4);
      await configure("q1timeline.executionMode", "project");
      await refreshFresh(project);
      const restored = await waitForIr(project, "restored project budget", (ir) => matchesMode(ir, "simulation"));
      assert.equal(restored.execution.counter.max_steps, 1000);
    });
  });
});
