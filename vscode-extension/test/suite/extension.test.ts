import assert from "node:assert/strict";
import { join, relative } from "node:path";
import * as vscode from "vscode";

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

  it("analyzes the two-qubit example and publishes no QBST diagnostics", async () => {
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    assert.ok(workspaceRoot);

    const projectUri = vscode.Uri.file(join(workspaceRoot, "examples", "two-qubit-entangling", "qbstimeline.yml"));
    await vscode.window.showTextDocument(projectUri);
    await vscode.commands.executeCommand("qbsTimeline.analyzeAndOpen");

    const outputUri = vscode.Uri.file(join(workspaceRoot, "examples", "two-qubit-entangling", ".qbs_timeline", "qbs_ir.json"));
    await vscode.workspace.fs.stat(outputUri);
    const diagnostics = vscode.languages.getDiagnostics(projectUri).filter((diagnostic) => diagnostic.source === "qbsTimeline");
    assert.equal(diagnostics.length, 0, diagnostics.map((diagnostic) => `${diagnostic.code}: ${diagnostic.message}`).join("\n"));
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
        await pause(100);
      }
      throw new Error(`Timed out waiting for ${description}. Last read: ${String(lastError || "condition not met")}`);
    }

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
