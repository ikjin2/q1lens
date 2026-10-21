import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmdirSync, unlinkSync, writeFileSync } from "node:fs";
import Module = require("node:module");
import { tmpdir } from "node:os";
import { basename, dirname, isAbsolute, join, relative, sep } from "node:path";

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => { resolve = done; });
  return { promise, resolve };
}

const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 40));

interface AnalysisCall { projectFile: string; irPath: string; id: number }

async function withCommands(
  hooks: { analyze?: (call: AnalysisCall) => Promise<void>; render?: (irPath: string) => Promise<void>; autoRefreshFor?: (resource: any) => boolean; fileEnvironment?: (resource: any) => Promise<void>; notebookEnvironment?: (resource: any, signal?: AbortSignal) => Promise<void>; errorMessage?: () => Promise<void>; inputBox?: (title: string, token?: any) => Promise<void>; findFiles?: () => Promise<void> },
  run: (fixture: any) => Promise<void>,
): Promise<void> {
  const root = mkdtempSync(join(tmpdir(), "qbs-concurrency-"));
  const projects = ["a", "b"].map((name) => {
    const dir = join(root, name);
    mkdirSync(join(dir, ".qbs_timeline"), { recursive: true });
    writeFileSync(join(dir, "qbstimeline.yml"), "schedule:\n  file: schedule.py\noutputs:\n  dir: .qbs_timeline\n");
    return join(dir, "qbstimeline.yml");
  });
  const commands: Record<string, (...args: any[]) => Promise<void>> = {};
  const analyzed: AnalysisCall[] = [];
  const shown: Array<{ name: string; projectFile: string }> = [];
  const resources: string[] = [];
  const errors: string[] = [];
  const prompts: string[] = [];
  const activePrompts = new Set<string>();
  const inputCancellations: any[] = [];
  const notebooks = new Map<string, any>();
  const subscriptions: Array<{ dispose(): void }> = [];
  const rendered: string[] = [];
  const published: string[] = [];
  const retries: string[] = [];
  const written: string[] = [];
  const quickPicks: string[] = [];
  const openedTargets: any[] = [];
  const openedSources: any[] = [];
  let panelHandlers: any;
  let panelDisposals = 0;
  const dispose = () => subscriptions.splice(0).forEach((subscription) => subscription.dispose());
  let activeFile = projects[0];
  const watchers: Array<{ pattern: any; changed?: (uri: any) => void; created?: (uri: any) => void; deleted?: (uri: any) => void; disposed: boolean }> = [];
  const contains = (base: string, file: string) => {
    const rel = relative(base, file);
    return rel !== ".." && !rel.startsWith(`..${sep}`) && !isAbsolute(rel);
  };
  const notify = (kind: "changed" | "created" | "deleted", file: string) => {
    const minimatch = require("minimatch");
    for (const watcher of [...watchers]) {
      if (watcher.disposed) continue;
      const base = typeof watcher.pattern === "string" ? root : watcher.pattern.baseUri.fsPath;
      const pattern = typeof watcher.pattern === "string" ? watcher.pattern : watcher.pattern.pattern;
      if (contains(base, file) && (minimatch.minimatch ?? minimatch)(relative(base, file).replace(/\\/g, "/"), pattern, { dot: true })) watcher[kind]?.({ fsPath: file });
    }
  };
  const change = (file: string) => notify("changed", file);
  let configurationChanged: ((event: any) => void) | undefined;
  let watcherCount = 0;
  let environmentAvailable = true;
  let retryEnvironment = false;
  let retryActiveFile: string | undefined;
  let emitWrites = false;
  const vscode: any = {
    Uri: { file: (fsPath: string) => ({ fsPath, toString: () => `file://${fsPath}` }) },
    RelativePattern: class { constructor(public baseUri: any, public pattern: string) {} },
    window: {
      get activeTextEditor() { return { document: { uri: { fsPath: activeFile } } }; },
      createOutputChannel: () => ({ appendLine() {}, show() {}, dispose() {} }),
      showQuickPick: async () => { quickPicks.push("project"); await settle(); return undefined; },
      showWarningMessage: async () => undefined,
      showInputBox: async (options: any, token: any) => {
        prompts.push(options.title);
        activePrompts.add(options.title);
        let listener: any;
        try {
          const input = hooks.inputBox?.(options.title, token);
          if (token) await Promise.race([input, new Promise<void>((resolve) => { listener = token.onCancellationRequested(resolve); })]);
          else await input;
          return token?.isCancellationRequested ? undefined : options.value;
        } finally { listener?.dispose(); activePrompts.delete(options.title); }
      },
      showErrorMessage: async (message: string) => {
        errors.push(message);
        await hooks.errorMessage?.();
        if (retryActiveFile) activeFile = retryActiveFile;
        return retryEnvironment ? "Check Python Environment" : undefined;
      },
    },
    workspace: {
      getWorkspaceFolder: (uri: any) => contains(root, uri.fsPath) ? { uri: { fsPath: root } } : undefined,
      findFiles: async () => { await hooks.findFiles?.(); return projects.map((fsPath) => ({ fsPath })); },
      getConfiguration: (_section: string, resource: any) => ({ get: (key: string, fallback: any) => key === "qbs.autoRefresh" ? hooks.autoRefreshFor?.(resource) ?? true : fallback }),
      onDidChangeConfiguration: (listener: (event: any) => void) => { configurationChanged = listener; return { dispose() {} }; },
      createFileSystemWatcher: (pattern: any) => {
        watcherCount += 1;
        const watcher: typeof watchers[number] = { pattern, disposed: false };
        watchers.push(watcher);
        return {
        onDidChange: (listener: (uri: any) => void) => { watcher.changed = listener; return { dispose() {} }; },
        onDidCreate: (listener: (uri: any) => void) => { watcher.created = listener; return { dispose() {} }; },
        onDidDelete: (listener: (uri: any) => void) => { watcher.deleted = listener; return { dispose() {} }; },
        dispose() { if (!watcher.disposed) { watcherCount -= 1; watcher.disposed = true; } },
        };
      },
      fs: {
        createDirectory: async (uri: any) => { mkdirSync(uri.fsPath, { recursive: true }); },
        writeFile: async (uri: any, bytes: Uint8Array) => { written.push(uri.fsPath); writeFileSync(uri.fsPath, bytes); if (emitWrites) change(uri.fsPath); },
      },
    },
    NotebookCellKind: { Code: 2, Markup: 1 },
    CancellationTokenSource: class {
      listeners = new Set<() => void>();
      disposed = false;
      token = { isCancellationRequested: false, onCancellationRequested: (listener: () => void) => {
        this.listeners.add(listener);
        return { dispose: () => this.listeners.delete(listener) };
      } };
      constructor() { inputCancellations.push(this); }
      cancel() { if (!this.token.isCancellationRequested) { this.token.isCancellationRequested = true; for (const listener of this.listeners) listener(); } }
      dispose() { this.disposed = true; this.listeners.clear(); }
    },
    languages: { createDiagnosticCollection: () => {
      let disposed = false;
      return { clear() { if (disposed) throw new Error("illegal state - object is disposed"); }, set() {}, dispose() { disposed = true; } };
    } },
    commands: {
      registerCommand: (name: string, handler: (...args: any[]) => Promise<void>) => { commands[name] = handler; return { dispose() {} }; },
      executeCommand: async (name: string, retry: string, resource: any, retryTarget?: any) => {
        assert.equal(name, "q1lens.checkEnvironment");
        retries.push(retry);
        await commands[retry](retryTarget ?? resource);
      },
    },
  };
  const notebook = (name: string) => {
    if (!notebooks.has(name)) {
      const notebookFile = join(dirname(projects[0]), `${name}.ipynb`);
      writeFileSync(notebookFile, "{}");
      const cells: any[] = [];
      const document = { uri: vscode.Uri.file(notebookFile), isClosed: false, cellAt: (index: number) => cells[index], getCells: () => cells };
      const cell: any = { get index() { return cells.indexOf(cell); }, kind: 2, metadata: {},
        text: "compiler = object()\nschedule = Schedule()\n", document: { getText: () => cell.text }, notebook: document };
      cells.push(cell);
      notebooks.set(name, { cell, cells, document });
    }
    return notebooks.get(name);
  };
  const originalLoad = (Module as any)._load;
  const modulePath = require.resolve("../src/qbs/commands");
  (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
    if (request === "vscode") return vscode;
    if (request === "../fileEnvironment") return {
      ensureFilePythonEnvironment: async (_vscode: any, resource: any) => {
        resources.push(resource.fsPath);
        await hooks.fileEnvironment?.(resource);
        return environmentAvailable ? { pythonPath: "python", pythonArgs: [], source: "vscode" } : undefined;
      },
    };
    if (request === "../notebookEnvironment") return { ensureNotebookEnvironment: async (_vscode: any, resource: any, _output: any, _owner: any, signal?: AbortSignal) => {
      await hooks.notebookEnvironment?.(resource, signal);
      return { pythonPath: "python", pythonArgs: [] };
    } };
    if (request === "./diagnostics") return { publishDiagnostics(input: any) {
      input.collection.clear();
      published.push(input.projectFile);
    } };
    if (request === "./navigation") return { ...originalLoad.call(this, request, parent, isMain),
      openFileAtLine: async (file: string, line: number) => { openedSources.push({ file, line }); },
      openFileBelowAtLine: async (file: string, line: number) => { openedSources.push({ file, line }); },
    };
    if (request === "./webview/panel") return { TimelinePanel: class {
      constructor(_extensionUri: any, handlers: any) { panelHandlers = handlers; }
      show(ir: any, paths: any) { shown.push({ name: ir.schedule.name, projectFile: paths.projectFile }); }
      dispose() { panelDisposals += 1; }
    } };
    if (request === "./qbsCli") return {
      buildAnalyzeInvocation: (input: any) => ({ command: "mock", args: ["analyze", input.projectFile, input.irPath] }),
      buildRenderInvocation: (input: any) => ({ command: "mock", args: ["render", input.irPath] }),
      runProcessWithSpawn: async (_command: string, args: string[]) => {
        if (args[0] === "analyze") {
          const call = { projectFile: args[1], irPath: args[2], id: analyzed.length + 1 };
          analyzed.push(call);
          await hooks.analyze?.(call);
          writeFileSync(call.irPath, JSON.stringify({ schedule: { name: `${basename(dirname(call.projectFile))}:${call.id}` },
            operations: [], symbolic_values: [], symbolic_pulses: [], q1asm_programs: [], q1asm_provenance: [] }));
        } else { rendered.push(args[1]); await hooks.render?.(args[1]); }
        return { exitCode: 0, stdout: "", stderr: "" };
      },
    };
    return originalLoad.call(this, request, parent, isMain);
  };
  delete require.cache[modulePath];
  try {
    require("../src/qbs/commands").registerCommands({ extensionUri: { fsPath: root }, subscriptions }, { openQ1TimelineTarget: async (target: any) => { openedTargets.push(target); } });
    await run({ commands, projects, analyzed, shown, resources, errors, prompts, activePrompts, inputCancellations, notebook, dispose, rendered, published, retries, written, quickPicks, panelHandlers, openedTargets, openedSources,
      panelDisposals: () => panelDisposals,
      setActive: (file: string) => { activeFile = file; },
      setEnvironment: (available: boolean) => { environmentAvailable = available; },
      enableRetry: (activeFile?: string) => { retryEnvironment = true; retryActiveFile = activeFile; },
      change,
      notify,
      watchers,
      watcherCount: () => watcherCount,
      configurationChanged: () => configurationChanged?.({ affectsConfiguration: () => true }),
      emitWrites: () => { emitWrites = true; },
      openNotebook: (name: string) => commands["qbsTimeline.openNotebookTimelineFromCell"](notebook(name).cell),
    });
  } finally {
    dispose();
    delete require.cache[modulePath];
    (Module as any)._load = originalLoad;
  }
}

describe("QBS analysis concurrency", () => {
  for (const kind of ["file", "notebook"]) {
    for (const notification of ["parent deletion", "source deletion"]) {
      it(`recovers an external ${kind} after ${notification} removes its watcher base`, async () => {
        let source = "";
        await withCommands({ analyze: async () => { readFileSync(source); } }, async (fixture) => {
          const ancestor = mkdtempSync(join(tmpdir(), "qbs-source-parent-"));
          const parent = join(ancestor, "external");
          mkdirSync(parent);
          source = join(parent, kind === "file" ? "schedule.py" : "experiment.notebook");
          writeFileSync(source, "initial");
          writeFileSync(fixture.projects[0], `schedule:\n  ${kind}: ${source.replace(/\\/g, "/")}\noutputs:\n  dir: .qbs_timeline\n`);
          await fixture.commands["qbsTimeline.analyzeAndOpen"]();
          const original = fixture.watchers.find((watcher: any) => !watcher.disposed && watcher.pattern.baseUri?.fsPath === parent);
          assert.ok(original);

          unlinkSync(source); rmdirSync(parent);
          // Both sequences are emitted by real VS Code versions when a watched parent is removed.
          original.deleted({ fsPath: notification === "parent deletion" ? parent : source });
          const fallback = fixture.watchers.find((watcher: any) => !watcher.disposed && watcher.pattern.baseUri?.fsPath === ancestor);
          assert.ok(fallback, "A removed watcher base must rebind to its existing ancestor");
          assert.equal(fallback.pattern.pattern, "**/*");
          await new Promise((resolve) => setTimeout(resolve, 850));
          assert.equal(fixture.analyzed.length, 2, "Deleting an ancestor must refresh the configured source");
          assert.equal(fixture.errors.length, 1);
          assert.equal(fixture.shown.length, 1, "A failed refresh retains the accepted preview");

          mkdirSync(parent); writeFileSync(source, "restored");
          fixture.notify("created", source);
          await new Promise((resolve) => setTimeout(resolve, 850));
          assert.equal(fixture.analyzed.length, 3);
          assert.equal(fixture.shown.length, 2, "The restored source must recover without an explicit Refresh");
          assert.ok(fallback.disposed);
          assert.equal(fixture.watchers.filter((watcher: any) => !watcher.disposed && watcher.pattern.baseUri?.fsPath === parent).length, 1);
          assert.equal(fixture.watcherCount(), 2);
        });
      });
    }
  }

  it("keeps the source refresh queue while rebinding a removed external directory", async () => {
    const started = deferred(), release = deferred();
    await withCommands({ analyze: async (call) => {
      if (call.id === 2) { started.resolve(); await release.promise; }
    } }, async (fixture) => {
      const ancestor = mkdtempSync(join(tmpdir(), "qbs-rebind-queue-")), parent = join(ancestor, "external");
      mkdirSync(parent);
      const source = join(parent, "schedule.py");
      writeFileSync(source, "initial");
      writeFileSync(fixture.projects[0], `schedule:\n  file: ${source.replace(/\\/g, "/")}\n`);
      await fixture.commands["qbsTimeline.analyzeAndOpen"]();
      const original = fixture.watchers.find((watcher: any) => !watcher.disposed && watcher.pattern.baseUri?.fsPath === parent);
      writeFileSync(source, "updated"); fixture.change(source);
      try {
        await started.promise;
        unlinkSync(source); rmdirSync(parent);
        original.deleted({ fsPath: parent });
        mkdirSync(parent); writeFileSync(source, "restored");
        fixture.notify("created", source);
      } finally { release.resolve(); }
      await new Promise((resolve) => setTimeout(resolve, 950));
      assert.equal(fixture.analyzed.length, 3, "Rebinding must preserve the refresh queued behind the active analysis");
      assert.equal(fixture.shown.length, 3);
      original.changed({ fsPath: source }); original.created({ fsPath: source }); original.deleted({ fsPath: parent });
      await new Promise((resolve) => setTimeout(resolve, 850));
      assert.equal(fixture.analyzed.length, 3, "Retained callbacks from the replaced native watcher must be ignored");
      assert.deepEqual(fixture.errors, []);
    });
  }).timeout(5000);

  it("ignores unrelated siblings and prefix folders while recovering an external source", async () => {
    await withCommands({}, async (fixture) => {
      const ancestor = mkdtempSync(join(tmpdir(), "qbs-rebind-filter-")), parent = join(ancestor, "external");
      mkdirSync(parent);
      const source = join(parent, "schedule.py");
      writeFileSync(source, "initial");
      writeFileSync(fixture.projects[0], `schedule:\n  file: ${source.replace(/\\/g, "/")}\n`);
      await fixture.commands["qbsTimeline.analyzeAndOpen"]();
      const original = fixture.watchers.find((watcher: any) => !watcher.disposed && watcher.pattern.baseUri?.fsPath === parent);
      original.deleted({ fsPath: `${parent}-sibling` });
      fixture.notify("created", join(parent, "unrelated.py"));
      await new Promise((resolve) => setTimeout(resolve, 850));
      assert.equal(original.disposed, false);
      assert.equal(fixture.analyzed.length, 1);
      unlinkSync(source); rmdirSync(parent);
      original.deleted({ fsPath: parent });
      const fallback = fixture.watchers.find((watcher: any) => !watcher.disposed && watcher.pattern.baseUri?.fsPath === ancestor);
      const nested = join(ancestor, "unrelated", "nested");
      mkdirSync(nested, { recursive: true });
      writeFileSync(join(nested, "schedule.py"), "unrelated");
      fixture.notify("created", join(nested, "schedule.py"));
      await new Promise((resolve) => setTimeout(resolve, 850));
      assert.equal(fixture.analyzed.length, 2, "Recursive fallback must still refresh only the configured source");
      assert.equal(fallback.disposed, false, "An unrelated create must not narrow the fallback watcher");
    });
  });

  for (const ending of ["retargeting", "disposal", "disabling auto refresh"]) {
    it(`ignores a retained external watcher after ${ending}`, async () => {
      let enabled = true;
      await withCommands({ autoRefreshFor: () => enabled }, async (fixture) => {
        const parent = mkdtempSync(join(tmpdir(), "qbs-rebind-owner-")), source = join(parent, "schedule.py");
        writeFileSync(source, "initial");
        writeFileSync(fixture.projects[0], `schedule:\n  file: ${source.replace(/\\/g, "/")}\n`);
        await fixture.commands["qbsTimeline.analyzeAndOpen"]();
        const original = fixture.watchers.find((watcher: any) => !watcher.disposed && watcher.pattern.baseUri?.fsPath === parent);
        if (ending === "retargeting") {
          fixture.setActive(fixture.projects[1]);
          await fixture.commands["qbsTimeline.analyzeAndOpen"]();
        } else if (ending === "disposal") fixture.dispose();
        else { enabled = false; fixture.configurationChanged(); }
        const count = fixture.analyzed.length, watchers = fixture.watcherCount();
        unlinkSync(source); rmdirSync(parent);
        original.deleted({ fsPath: parent }); original.created({ fsPath: source }); original.changed({ fsPath: source });
        await new Promise((resolve) => setTimeout(resolve, 850));
        assert.equal(fixture.analyzed.length, count);
        assert.equal(fixture.watcherCount(), watchers, "An obsolete callback must not resurrect a native watcher");
        assert.ok(original.disposed);
        if (ending === "disabling auto refresh") {
          mkdirSync(parent); writeFileSync(source, "restored");
          await fixture.commands["qbsTimeline.refresh"]();
          assert.equal(fixture.analyzed.length, count + 1, "Explicit Refresh remains available with auto refresh disabled");
          assert.equal(fixture.watcherCount(), 0);
        }
      });
    });
  }

  for (const { kind, location } of [
    { kind: "file", location: "outside the workspace" },
    { kind: "notebook", location: "outside the workspace" },
    { kind: "notebook", location: "inside the workspace" },
    { kind: "notebook", location: "inside an external project" },
    { kind: "file", location: "inside an external project" },
  ]) {
    it(`watches a configured ${kind} ${location} and rebinds after retargeting it`, async () => {
      await withCommands({}, async (fixture) => {
        const external = location === "inside the workspace" ? dirname(fixture.projects[0]) : mkdtempSync(join(tmpdir(), "qbs-external-watch-"));
        if (location === "inside an external project") {
          fixture.projects[0] = join(external, "qbstimeline.yml");
          mkdirSync(join(external, ".qbs_timeline"));
          fixture.setActive(fixture.projects[0]);
        }
        const extension = kind === "file" ? "py" : "notebook";
        const [first, second] = ["first", "second"].map((name) => join(external, `${name}.${extension}`));
        const unrelatedDir = location === "inside an external project" ? mkdtempSync(join(tmpdir(), "qbs-unrelated-watch-")) : external;
        const unrelated = join(unrelatedDir, `unrelated.${extension}`);
        for (const source of [first, second, unrelated]) writeFileSync(source, "initial");
        const yaml = (source: string) => `schedule:\n  ${kind}: ${source.replace(/\\/g, "/")}\noutputs:\n  dir: .qbs_timeline\n`;
        writeFileSync(fixture.projects[0], yaml(first));
        await fixture.commands["qbsTimeline.analyzeAndOpen"]();
        writeFileSync(first, "updated"); fixture.change(first);
        await new Promise((resolve) => setTimeout(resolve, 850));
        assert.equal(fixture.analyzed.length, 2);
        const expectedWatchers = location === "inside an external project" && kind === "notebook" ? 3 : 2;
        assert.equal(fixture.watcherCount(), expectedWatchers, "Sources outside the existing glob coverage need a filesystem subscription");
        writeFileSync(unrelated, "updated"); fixture.change(unrelated);
        await new Promise((resolve) => setTimeout(resolve, 850));
        assert.equal(fixture.analyzed.length, 2, "Watching the external directory must not refresh for unrelated siblings");
        writeFileSync(fixture.projects[0], yaml(second)); fixture.change(fixture.projects[0]);
        await new Promise((resolve) => setTimeout(resolve, 850));
        assert.equal(fixture.analyzed.length, 3);
        assert.equal(fixture.watcherCount(), expectedWatchers, "Switching source must dispose the previous external subscription");
        writeFileSync(first, "old source"); fixture.change(first);
        await new Promise((resolve) => setTimeout(resolve, 850));
        const expectedCalls = location === "inside an external project" && kind === "file" ? 4 : 3;
        assert.equal(fixture.analyzed.length, expectedCalls, "Project-local Python files remain valid helpers after retargeting the entry point");
        writeFileSync(second, "new source"); fixture.change(second);
        await new Promise((resolve) => setTimeout(resolve, 850));
        assert.equal(fixture.analyzed.length, expectedCalls + 1);
        fixture.dispose();
        assert.equal(fixture.watcherCount(), 0);
        assert.deepEqual(fixture.errors, []);
      });
    }).timeout(10000);
  }

  it("rebinds an external executed notebook when its original source notebook stays unchanged", async () => {
    await withCommands({}, async (fixture) => {
      const external = mkdtempSync(join(tmpdir(), "qbs-notebook-rebind-"));
      const [first, second] = ["first", "second"].map((name) => join(external, `${name}.ipynb`));
      for (const file of [first, second]) writeFileSync(file, "{}");
      const original = join(dirname(fixture.projects[0]), "original.ipynb");
      writeFileSync(original, "{}");
      const yaml = (file: string) => `schedule:\n  notebook: ${file.replace(/\\/g, "/")}\nsource:\n  notebook: original.ipynb\noutputs:\n  dir: .qbs_timeline\n`;
      writeFileSync(fixture.projects[0], yaml(first));
      await fixture.commands["qbsTimeline.analyzeAndOpen"]();
      writeFileSync(fixture.projects[0], yaml(second));
      await fixture.commands["qbsTimeline.refresh"]();
      writeFileSync(second, "updated"); fixture.change(second);
      await new Promise((resolve) => setTimeout(resolve, 850));
      assert.equal(fixture.analyzed.length, 3, "The executed notebook needs a new watcher even when its environment resource is unchanged");
      assert.equal(fixture.watcherCount(), 2);
    });
  });

  for (const title of ["Schedule variable", "Compiler variable"]) {
    for (const newer of ["file opening", "disposal"]) {
      it(`closes an obsolete ${title.toLowerCase()} input after ${newer}`, async () => {
        const started = deferred(), release = deferred();
        await withCommands({ autoRefreshFor: () => false, inputBox: async (promptTitle) => {
          if (promptTitle === title) { started.resolve(); await release.promise; }
        } }, async (fixture) => {
          const opening = fixture.openNotebook("original");
          try {
            await started.promise;
            assert.equal(fixture.activePrompts.has(title), true);
            if (newer === "file opening") { fixture.setActive(fixture.projects[1]); await fixture.commands["qbsTimeline.analyzeAndOpen"](); }
            else fixture.dispose();
            await settle();
            assert.equal(fixture.activePrompts.size, 0, "The old ignoreFocusOut input must close without waiting for user input");
            await opening;
            assert.equal(fixture.prompts.length, title === "Schedule variable" ? 1 : 2);
            assert.deepEqual(fixture.written, []);
            assert.equal(fixture.inputCancellations.length, 1);
            assert.equal(fixture.inputCancellations[0].disposed, true);
            assert.equal(fixture.inputCancellations[0].listeners.size, 0);
          } finally { release.resolve(); await opening; }
        });
      });
    }
  }

  it("keeps a newer notebook's environment cancellable after an older variable input closes", async () => {
    const inputStarted = deferred(), environmentStarted = deferred(), releaseInput = deferred(), releaseEnvironment = deferred();
    let latestSignal: AbortSignal | undefined;
    await withCommands({ autoRefreshFor: () => false, inputBox: async (title) => {
      if (title === "Schedule variable") { inputStarted.resolve(); await releaseInput.promise; }
    }, notebookEnvironment: async (resource, signal) => {
      if (basename(resource.fsPath) === "second.ipynb") { latestSignal = signal; environmentStarted.resolve(); await releaseEnvironment.promise; }
    } }, async (fixture) => {
      const first = fixture.openNotebook("first");
      await inputStarted.promise;
      const second = fixture.openNotebook("second");
      try {
        await environmentStarted.promise;
        await settle();
        assert.equal(fixture.activePrompts.size, 0, "A new notebook preflight must close the previous notebook's input");
        await first;
        fixture.setActive(fixture.projects[1]);
        await fixture.commands["qbsTimeline.analyzeAndOpen"]();
        assert.equal(latestSignal?.aborted, true, "An old input's finally must not detach the newer environment owner");
      } finally { releaseInput.resolve(); releaseEnvironment.resolve(); await first; await second; }
    });
  });

  for (const title of ["Schedule variable", "Compiler variable"]) {
    it(`keeps the current ${title.toLowerCase()} input open while Refresh runs`, async () => {
      const started = deferred(), release = deferred();
      let inputToken: any;
      await withCommands({ autoRefreshFor: () => false, inputBox: async (promptTitle, token) => {
        if (promptTitle === title) { inputToken = token; started.resolve(); await release.promise; }
      } }, async (fixture) => {
        await fixture.commands["qbsTimeline.analyzeAndOpen"]();
        const opening = fixture.openNotebook("original");
        try {
          await started.promise;
          await fixture.commands["qbsTimeline.refresh"]();
          assert.equal(fixture.activePrompts.has(title), true);
          assert.equal(inputToken?.isCancellationRequested, false);
        } finally { release.resolve(); await opening; }
        assert.equal(fixture.analyzed.length, 3);
        assert.equal(fixture.inputCancellations[0].disposed, true);
        assert.equal(fixture.inputCancellations[0].listeners.size, 0);
      });
    });
  }

  it("releases notebook input cancellation before its analysis waits in the FIFO", async () => {
    const started = deferred(), release = deferred();
    await withCommands({ autoRefreshFor: () => false, analyze: async ({ id }) => {
      if (id === 1) { started.resolve(); await release.promise; }
    } }, async (fixture) => {
      const opening = fixture.openNotebook("original");
      await started.promise;
      fixture.setActive(fixture.projects[1]);
      const next = fixture.commands["qbsTimeline.analyzeAndOpen"]();
      try {
        assert.equal(fixture.inputCancellations.length, 1);
        assert.equal(fixture.inputCancellations[0].disposed, true, "Input cleanup must not wait for the queued analysis");
        assert.equal(fixture.inputCancellations[0].token.isCancellationRequested, false, "A newer opening must not cancel an analysis that already entered FIFO");
      } finally { release.resolve(); await opening; await next; }
      assert.equal(fixture.shown.length, 2);
      assert.equal(fixture.shown.at(-1).projectFile, fixture.projects[1]);
    });
  });

  for (const newer of ["notebook", "file", "disposal"]) {
    it(`cancels an older notebook environment preflight on newer ${newer}`, async () => {
      const started = deferred(), release = deferred();
      let originalSignal: AbortSignal | undefined;
      await withCommands({ autoRefreshFor: () => false, notebookEnvironment: async (resource, signal) => {
        if (basename(resource.fsPath) === "first.ipynb") { originalSignal = signal; started.resolve(); await release.promise; }
      } }, async (fixture) => {
        const opening = fixture.openNotebook("first");
        try {
          await started.promise;
          if (newer === "notebook") await fixture.openNotebook("second");
          else if (newer === "file") { fixture.setActive(fixture.projects[1]); await fixture.commands["qbsTimeline.analyzeAndOpen"](); }
          else fixture.dispose();
          assert.equal(originalSignal?.aborted, true, "The superseded helper must stop its pending kernel/setup UI");
        } finally { release.resolve(); await opening; }
      });
    });
  }

  it("keeps the latest notebook preflight cancellable after the older helper finishes", async () => {
    const firstStarted = deferred(), secondStarted = deferred(), releaseFirst = deferred(), releaseSecond = deferred();
    let secondSignal: AbortSignal | undefined;
    await withCommands({ autoRefreshFor: () => false, notebookEnvironment: async (resource, signal) => {
      if (basename(resource.fsPath) === "first.ipynb") { firstStarted.resolve(); await releaseFirst.promise; }
      else { secondSignal = signal; secondStarted.resolve(); await releaseSecond.promise; }
    } }, async (fixture) => {
      const first = fixture.openNotebook("first");
      await firstStarted.promise;
      const second = fixture.openNotebook("second");
      try {
        await secondStarted.promise;
        releaseFirst.resolve(); await first;
        fixture.setActive(fixture.projects[1]);
        await fixture.commands["qbsTimeline.analyzeAndOpen"]();
        assert.equal(secondSignal?.aborted, true, "An older finally must not detach the current cancellation owner");
      } finally { releaseFirst.resolve(); releaseSecond.resolve(); await first; await second; }
    });
  });

  for (const outcome of ["success", "failure"]) {
    it(`blocks old Q1ASM navigation while the displayed output is rewritten and recovers after ${outcome}`, async () => {
      const started = deferred(), release = deferred();
      const ir = JSON.parse(readFileSync(join(__dirname, "../../test/fixtures/two-qubit-ir.json"), "utf8"));
      let renders = 0;
      await withCommands({ autoRefreshFor: () => false, render: async (irPath) => {
        writeFileSync(irPath, JSON.stringify(ir));
        if (++renders === 2) { started.resolve(); await release.promise; if (outcome === "failure") throw new Error("Renderer failed after rewriting Q1ASM"); }
      } }, async (fixture) => {
        await fixture.commands["qbsTimeline.analyzeAndOpen"]();
        const selection = { blockId: ir.symbolic_pulses[0].id };
        const sequencer = ir.q1asm_programs[0].sequencer;
        const refresh = fixture.commands["qbsTimeline.refresh"]();
        try {
          await started.promise;
          await fixture.panelHandlers.onOpenQ1Timeline(selection);
          await fixture.commands["qbsTimeline.openQ1Timeline"]();
          await fixture.panelHandlers.onOpenQ1AsmSource({ sequencer, line: 3 });
          assert.deepEqual(fixture.openedTargets, [], "The old result must not map into newly generated files");
          assert.deepEqual(fixture.openedSources, []);
          await fixture.panelHandlers.onOpenScheduleSource({});
          assert.equal(fixture.openedSources[0].file, join(dirname(fixture.projects[0]), "schedule.py"), "Original source navigation remains available");
        } finally { release.resolve(); await refresh; }
        if (outcome === "failure") {
          await fixture.panelHandlers.onOpenQ1Timeline(selection);
          await fixture.panelHandlers.onOpenQ1AsmSource({ sequencer, line: 3 });
          assert.equal(fixture.openedTargets.length, 0, "Failed rendering must not reactivate the old source mapping");
          assert.equal(fixture.openedSources.length, 1);
          await fixture.commands["qbsTimeline.refresh"]();
        }
        await fixture.panelHandlers.onOpenQ1Timeline(selection);
        await fixture.panelHandlers.onOpenQ1AsmSource({ sequencer, line: 3 });
        assert.equal(fixture.openedTargets.length, 1, "Successful publication restores generated source navigation");
        assert.equal(fixture.openedSources.length, 2);
        assert.equal(fixture.openedTargets[0].projectFile, join(dirname(fixture.projects[0]), ".qbs_timeline", "q1timeline.yml"));
      });
    });
  }

  it("keeps displayed Q1ASM navigation available while another output directory is analyzed", async () => {
    const started = deferred(), release = deferred();
    const ir = JSON.parse(readFileSync(join(__dirname, "../../test/fixtures/two-qubit-ir.json"), "utf8"));
    let renders = 0;
    await withCommands({ autoRefreshFor: () => false, render: async (irPath) => {
      writeFileSync(irPath, JSON.stringify(ir));
      if (++renders === 2) { started.resolve(); await release.promise; }
    } }, async (fixture) => {
      await fixture.commands["qbsTimeline.analyzeAndOpen"]();
      fixture.setActive(fixture.projects[1]);
      const next = fixture.commands["qbsTimeline.analyzeAndOpen"]();
      try {
        await started.promise;
        await fixture.panelHandlers.onOpenQ1Timeline({ blockId: ir.symbolic_pulses[0].id });
        await fixture.panelHandlers.onOpenQ1AsmSource({ sequencer: ir.q1asm_programs[0].sequencer, line: 3 });
        assert.equal(fixture.openedTargets[0].projectFile, join(dirname(fixture.projects[0]), ".qbs_timeline", "q1timeline.yml"));
        assert.equal(fixture.openedSources.length, 1);
      } finally { release.resolve(); await next; }
    });
  });

  for (const newer of ["notebook", "file"]) {
    it(`keeps a newer ${newer} opening when an earlier notebook kernel preflight completes late`, async () => {
      const started = deferred(), release = deferred();
      await withCommands({ autoRefreshFor: () => false, notebookEnvironment: async (resource) => {
        if (basename(resource.fsPath) === "first.ipynb") { started.resolve(); await release.promise; }
      } }, async (fixture) => {
        const first = fixture.openNotebook("first");
        await started.promise;
        if (newer === "notebook") await fixture.openNotebook("second");
        else { fixture.setActive(fixture.projects[1]); await fixture.commands["qbsTimeline.analyzeAndOpen"](); }
        const current = fixture.shown[0];
        const writes = fixture.written.length;
        release.resolve();
        await first;
        assert.equal(fixture.analyzed.length, 1, "The superseded notebook must not enter analysis after the new opening");
        assert.deepEqual(fixture.shown, [current]);
        assert.equal(fixture.written.length, writes, "An older preflight must not rewrite the new notebook's managed artifacts");
        assert.equal(fixture.prompts.length, newer === "notebook" ? 2 : 0);
        await fixture.commands["qbsTimeline.refresh"]();
        assert.equal(fixture.analyzed.length, 2);
        assert.equal(fixture.shown[1].projectFile, current.projectFile);
        if (newer === "notebook") {
          const snapshot = JSON.parse(readFileSync(join(dirname(current.projectFile), "selected.ipynb"), "utf8"));
          assert.match(snapshot.metadata.qbstimeline.source_notebook, /second\.ipynb$/);
        }
        assert.deepEqual(fixture.errors, []);
      });
    });
  }

  for (const title of ["Schedule variable", "Compiler variable"]) {
    it(`drops an older notebook after a new file opening while its ${title.toLowerCase()} prompt waits`, async () => {
      const started = deferred(), release = deferred();
      await withCommands({ autoRefreshFor: () => false, inputBox: async (promptTitle) => {
        if (promptTitle === title) { started.resolve(); await release.promise; }
      } }, async (fixture) => {
        const opening = fixture.openNotebook("original");
        await started.promise;
        fixture.setActive(fixture.projects[1]);
        await fixture.commands["qbsTimeline.analyzeAndOpen"]();
        release.resolve();
        await opening;
        assert.deepEqual(fixture.analyzed.map((call: AnalysisCall) => call.projectFile), [fixture.projects[1]]);
        assert.equal(fixture.prompts.length, title === "Schedule variable" ? 1 : 2);
        assert.deepEqual(fixture.written, []);
        assert.deepEqual(fixture.errors, []);
      });
    });
  }

  it("keeps a pending notebook opening when only the current project's Refresh runs", async () => {
    const started = deferred(), release = deferred();
    let signal: AbortSignal | undefined;
    await withCommands({ autoRefreshFor: () => false, notebookEnvironment: async (_resource, environmentSignal) => { signal = environmentSignal; started.resolve(); await release.promise; } }, async (fixture) => {
      await fixture.commands["qbsTimeline.analyzeAndOpen"]();
      const opening = fixture.openNotebook("original");
      await started.promise;
      await fixture.commands["qbsTimeline.refresh"]();
      assert.equal(signal?.aborted, false, "Refresh must leave the pending explicit notebook opening active");
      release.resolve();
      await opening;
      assert.equal(fixture.analyzed.length, 3);
      assert.deepEqual(fixture.analyzed.slice(0, 2).map((call: AnalysisCall) => call.projectFile), [fixture.projects[0], fixture.projects[0]]);
      assert.notEqual(fixture.shown.at(-1).projectFile, fixture.projects[0]);
      assert.equal(fixture.prompts.length, 2);
      assert.deepEqual(fixture.errors, []);
    });
  });

  for (const changedSource of ["file", "notebook", "source notebook", "output directory", "equivalent file path"]) {
    it(changedSource === "equivalent file path" ? "allows equivalent source path notation after environment selection"
      : `stops an old-source analysis when the same project retargets its ${changedSource} during environment selection`, async () => {
      const started = deferred(), release = deferred();
      const checkedSources: string[] = [];
      const checkEnvironment = async (resource: any) => {
        checkedSources.push(resource.fsPath);
        if (checkedSources.length === 1) { started.resolve(); await release.promise; }
      };
      const fileSchedule = changedSource === "file" || changedSource === "output directory" || changedSource === "equivalent file path";
      await withCommands({
        autoRefreshFor: () => false,
        ...(fileSchedule ? { fileEnvironment: checkEnvironment } : { notebookEnvironment: checkEnvironment }),
      }, async (fixture) => {
        const projectDir = dirname(fixture.projects[0]);
        const extension = fileSchedule ? "py" : "ipynb";
        const sources = ["original", "replacement"].map((name) => join(projectDir, `${name}.${extension}`));
        const code = "from types import SimpleNamespace\nclass Compiler:\n    def compile(self, schedule):\n        schedule.compiled_instructions = {}\n        return schedule\nschedule = SimpleNamespace(name='source', schedulables={}, operations={})\ncompiler = Compiler()\n";
        const notebook = JSON.stringify({ cells: [{ cell_type: "code", source: code, metadata: { tags: ["qbstimeline-schedule"] } }], metadata: {}, nbformat: 4, nbformat_minor: 5 });
        for (const source of sources) writeFileSync(source, fileSchedule
          ? `${code}\ndef build_schedule():\n    return schedule\ndef build_compiler():\n    return compiler\n` : notebook);
        if (changedSource === "source notebook") writeFileSync(join(projectDir, "snapshot.ipynb"), notebook);
        if (changedSource === "output directory") mkdirSync(join(projectDir, "replacement-output"));
        const projectYaml = (source: string) => changedSource === "source notebook"
          ? `schedule:\n  notebook: snapshot.ipynb\nsource:\n  notebook: ${basename(source)}\noutputs:\n  dir: .qbs_timeline\n`
          : changedSource === "output directory"
            ? `schedule:\n  file: original.py\noutputs:\n  dir: ${source === sources[0] ? ".qbs_timeline" : "replacement-output"}\n`
            : changedSource === "equivalent file path"
              ? `schedule:\n  file: ${source === sources[0] ? "original.py" : "./original.py"}\noutputs:\n  dir: .qbs_timeline\n`
              : `schedule:\n  ${changedSource}: ${basename(source)}\noutputs:\n  dir: .qbs_timeline\n`;
        writeFileSync(fixture.projects[0], projectYaml(sources[0]));
        const pending = fixture.commands["qbsTimeline.analyzeAndOpen"]();
        await started.promise;
        writeFileSync(fixture.projects[0], projectYaml(sources[1]));
        release.resolve();
        await pending;
        if (changedSource === "equivalent file path") {
          assert.deepEqual(checkedSources, [sources[0]]);
          assert.equal(fixture.analyzed.length, 1);
          assert.equal(fixture.shown.length, 1);
          assert.deepEqual(fixture.errors, []);
          return;
        }
        assert.equal(fixture.analyzed.length, 0, "A changed source must not execute using the previous source's selected environment");
        assert.deepEqual(fixture.shown, []);
        assert.equal(fixture.errors.length, 1);
        assert.match(fixture.errors[0], /project.*changed|source.*changed/i);
        await fixture.commands["qbsTimeline.analyzeAndOpen"]();
        assert.deepEqual(checkedSources, changedSource === "output directory" ? [sources[0], sources[0]] : sources);
        assert.equal(fixture.analyzed.length, 1, "A fresh request must recover using the new source's environment");
        assert.equal(fixture.shown.length, 1);
      });
    });
  }

  it("does not open a project picker after disposal while project discovery was pending", async () => {
    const started = deferred(), release = deferred();
    await withCommands({ findFiles: async () => { started.resolve(); await release.promise; } }, async (fixture) => {
      fixture.setActive(join(tmpdir(), "unrelated.py"));
      const pending = fixture.commands["qbsTimeline.analyzeAndOpen"]();
      await started.promise;
      fixture.dispose();
      release.resolve();
      await pending;
      assert.deepEqual(fixture.quickPicks, []);
      assert.deepEqual(fixture.analyzed, []);
    });
  });

  for (const pausedStage of ["environment", "Schedule variable", "Compiler variable"]) {
    it(`stops notebook preparation after disposal during ${pausedStage}`, async () => {
      const started = deferred(), release = deferred();
      await withCommands({
        notebookEnvironment: async () => { if (pausedStage === "environment") { started.resolve(); await release.promise; } },
        inputBox: async (title) => { if (title === pausedStage) { started.resolve(); await release.promise; } },
      }, async (fixture) => {
        const pending = fixture.openNotebook("original");
        await started.promise;
        const priorPrompts = [...fixture.prompts];
        fixture.dispose();
        release.resolve();
        await pending;
        assert.deepEqual(fixture.prompts, priorPrompts, "Disposal must not open another variable prompt");
        assert.deepEqual(fixture.written, []);
        assert.deepEqual(fixture.analyzed, []);
        assert.deepEqual(fixture.errors, []);
      });
    });
  }

  it("does not rewrite a managed snapshot after disposal while Refresh awaits its kernel environment", async () => {
    const started = deferred(), release = deferred();
    let checks = 0;
    await withCommands({ notebookEnvironment: async () => { if (++checks === 2) { started.resolve(); await release.promise; } } }, async (fixture) => {
      const original = fixture.notebook("original");
      await fixture.openNotebook("original");
      const pending = fixture.commands["qbsTimeline.refresh"]();
      await started.promise;
      const writesBeforeDisposal = [...fixture.written];
      original.cell.text = "schedule = Schedule('edited after shutdown')\n";
      fixture.dispose();
      release.resolve();
      await pending;
      assert.deepEqual(fixture.written, writesBeforeDisposal);
      assert.equal(fixture.analyzed.length, 1);
      assert.deepEqual(fixture.errors, []);
    });
  });

  it("stops queued analysis and later stages without publishing to disposed diagnostics", async () => {
    const started = deferred(), release = deferred();
    await withCommands({ analyze: async () => { started.resolve(); await release.promise; } }, async (fixture) => {
      const first = fixture.commands["qbsTimeline.analyzeAndOpen"]();
      await started.promise;
      fixture.setActive(fixture.projects[1]);
      const second = fixture.commands["qbsTimeline.analyzeAndOpen"]();
      fixture.dispose();
      release.resolve();
      await Promise.all([first, second]);
      assert.equal(fixture.analyzed.length, 1);
      assert.deepEqual(fixture.rendered, []);
      assert.deepEqual(fixture.published, []);
      assert.deepEqual(fixture.shown, []);
      assert.deepEqual(fixture.errors, []);
      assert.equal(fixture.panelDisposals(), 1);
    });
  });

  it("does not report an in-flight failure after its subscriptions are disposed", async () => {
    const started = deferred(), release = deferred();
    await withCommands({ analyze: async () => { started.resolve(); await release.promise; throw new Error("Compiler failed"); } }, async (fixture) => {
      const pending = fixture.commands["qbsTimeline.analyzeAndOpen"]();
      await started.promise;
      fixture.dispose();
      release.resolve();
      await pending;
      assert.deepEqual(fixture.errors, []);
    });
  });

  it("does not launch an environment retry after disposal while its notification was open", async () => {
    const notified = deferred(), release = deferred();
    await withCommands({
      analyze: async ({ id }) => { if (id === 1) throw new Error("No module named q1lens"); },
      errorMessage: async () => { notified.resolve(); await release.promise; },
    }, async (fixture) => {
      fixture.enableRetry();
      const pending = fixture.commands["qbsTimeline.analyzeAndOpen"]();
      await notified.promise;
      fixture.dispose();
      release.resolve();
      await pending;
      assert.deepEqual(fixture.retries, []);
      assert.equal(fixture.analyzed.length, 1);
    });
  });

  for (const pausedStage of ["resolveProject", "readProjectConfig", "readSettings", "analyze", "render", "loadIr", "analyzeQ1Timeline", "loadQ1TimelineIr", "loadQ1TimelineDiagnostics", "listExistingQ1asmFiles"]) {
    it(`stops after ${pausedStage} resolves if its command owner was disposed`, async () => {
      const started = deferred(), release = deferred();
      const calls: string[] = [];
      let disposed = false;
      const stage = (name: string, result: any) => async () => {
        calls.push(name);
        if (name === pausedStage) { started.resolve(); await release.promise; }
        return result;
      };
      const paths = { projectFile: "C:\\repo\\qbstimeline.yml", projectDir: "C:\\repo", outputDir: "C:\\repo\\.qbs_timeline" };
      const ir = { q1asm_programs: [{ sequencer: "seq0", file: "seq0.q1asm" }] };
      const handler = require("../src/qbs/commands").createAnalyzeAndOpenHandler({
        isDisposed: () => disposed,
        resolveProject: stage("resolveProject", paths.projectFile),
        readProjectConfig: stage("readProjectConfig", { scheduleFile: "schedule.py", outputDir: ".qbs_timeline" }),
        readSettings: stage("readSettings", { revealOutputChannel: false }),
        derivePaths: () => paths,
        analyze: stage("analyze", undefined), render: stage("render", undefined), loadIr: stage("loadIr", ir),
        analyzeQ1Timeline: stage("analyzeQ1Timeline", true), loadQ1TimelineIr: stage("loadQ1TimelineIr", {}),
        loadQ1TimelineDiagnostics: stage("loadQ1TimelineDiagnostics", []), listExistingQ1asmFiles: stage("listExistingQ1asmFiles", new Set()),
        publishDiagnostics: () => calls.push("publish"), showPanel: () => calls.push("show"), showOutput: () => calls.push("output"),
      });
      const pending = handler();
      await started.promise;
      disposed = true;
      release.resolve();
      await pending;
      assert.equal(calls.at(-1), pausedStage);
    });
  }

  it("follows the selected project's folder-scoped auto refresh setting", async () => {
    let enabled = true;
    await withCommands({ autoRefreshFor: (resource) => Boolean(enabled && resource && basename(dirname(resource.fsPath)) === "b") }, async (fixture) => {
      assert.equal(fixture.watcherCount(), 0);
      await fixture.commands["qbsTimeline.analyzeAndOpen"]();
      assert.equal(fixture.watcherCount(), 0);
      fixture.setActive(fixture.projects[1]);
      await fixture.commands["qbsTimeline.analyzeAndOpen"]();
      assert.equal(fixture.watcherCount(), 1);
      enabled = false;
      fixture.configurationChanged();
      assert.equal(fixture.watcherCount(), 0);
      enabled = true;
      fixture.configurationChanged();
      assert.equal(fixture.watcherCount(), 1);
      fixture.setActive(fixture.projects[0]);
      await fixture.commands["qbsTimeline.analyzeAndOpen"]();
      assert.equal(fixture.watcherCount(), 0);
    });
  });

  it("honors an explicit retry project or schedule resource after the active editor changes", async () => {
    await withCommands({}, async (fixture) => {
      fixture.setActive(fixture.projects[1]);
      await fixture.commands["qbsTimeline.analyzeAndOpen"]({ fsPath: fixture.projects[0] });
      await fixture.commands["qbsTimeline.analyzeAndOpen"]({ fsPath: join(dirname(fixture.projects[0]), "schedule.py") });
      assert.deepEqual(fixture.analyzed.map((call: AnalysisCall) => call.projectFile), [fixture.projects[0], fixture.projects[0]]);
    });
  });

  it("keeps a slow older project from replacing a newer project and captures its requested source", async () => {
    const started = deferred(), release = deferred();
    await withCommands({ analyze: async ({ id }) => { if (id === 1) { started.resolve(); await release.promise; } } }, async (fixture) => {
      const first = fixture.commands["qbsTimeline.analyzeAndOpen"]();
      await started.promise;
      fixture.setActive(fixture.projects[1]);
      const second = fixture.commands["qbsTimeline.analyzeAndOpen"]();
      fixture.setActive(fixture.projects[0]);
      await settle();
      release.resolve();
      await Promise.all([first, second]);
      assert.deepEqual(fixture.shown.map((item: any) => item.projectFile), fixture.projects);
      assert.deepEqual(fixture.resources, fixture.projects.map((file: string) => join(dirname(file), "schedule.py")));
    });
  });

  it("finishes loading one request's artifacts before another request overwrites the same output", async () => {
    const rendering = deferred(), release = deferred();
    let renders = 0;
    await withCommands({ render: async () => { if (++renders === 1) { rendering.resolve(); await release.promise; } } }, async (fixture) => {
      const first = fixture.commands["qbsTimeline.analyzeAndOpen"]();
      await rendering.promise;
      const second = fixture.commands["qbsTimeline.analyzeAndOpen"]();
      await settle();
      release.resolve();
      await Promise.all([first, second]);
      assert.deepEqual(fixture.shown.map((item: any) => item.name), ["a:1", "a:2"]);
    });
  });

  it("continues after canceled environment preparation and failed analysis", async () => {
    await withCommands({ analyze: async ({ id }) => { if (id === 1) throw new Error("Compiler failed"); } }, async (fixture) => {
      fixture.setEnvironment(false);
      await fixture.commands["qbsTimeline.analyzeAndOpen"]();
      fixture.setEnvironment(true);
      await fixture.commands["qbsTimeline.analyzeAndOpen"]();
      await fixture.commands["qbsTimeline.analyzeAndOpen"]();
      assert.deepEqual(fixture.shown.map((item: any) => item.name), ["a:2"]);
      assert.equal(fixture.errors.length, 1);
    });
  });

  it("allows the environment retry command to enqueue after a failed analysis", async () => {
    await withCommands({ analyze: async ({ id }) => { if (id === 1) throw new Error("No module named q1lens"); } }, async (fixture) => {
      fixture.enableRetry();
      let timer: NodeJS.Timeout | undefined;
      try {
        await Promise.race([
          fixture.commands["qbsTimeline.analyzeAndOpen"](),
          new Promise((_resolve, reject) => { timer = setTimeout(() => reject(new Error("Environment retry deadlocked")), 1000); }),
        ]);
      } finally { clearTimeout(timer); }
      assert.deepEqual(fixture.shown.map((item: any) => item.name), ["a:2"]);
    });
  });

  it("retries the failed project after the environment notification changes focus", async () => {
    await withCommands({ analyze: async ({ id }) => { if (id === 1) throw new Error("No module named q1lens"); } }, async (fixture) => {
      fixture.enableRetry(fixture.projects[1]);
      await fixture.commands["qbsTimeline.analyzeAndOpen"]();
      assert.deepEqual(fixture.analyzed.map((call: AnalysisCall) => call.projectFile), [fixture.projects[0], fixture.projects[0]]);
      assert.deepEqual(fixture.resources, [join(dirname(fixture.projects[0]), "schedule.py"), join(dirname(fixture.projects[0]), "schedule.py")]);
    });
  });

  it("keeps managed notebook snapshots unchanged until their analysis finishes", async () => {
    const started = deferred(), release = deferred();
    const analyzedNotebooks: string[] = [];
    await withCommands({ analyze: async ({ id, projectFile }) => {
      if (id === 1) { started.resolve(); await release.promise; }
      const snapshot = JSON.parse(readFileSync(join(dirname(projectFile), "selected.ipynb"), "utf8"));
      analyzedNotebooks.push(basename(snapshot.metadata.qbstimeline.source_notebook));
    } }, async (fixture) => {
      const first = fixture.openNotebook("first");
      await started.promise;
      const second = fixture.openNotebook("second");
      await settle();
      release.resolve();
      await Promise.all([first, second]);
      assert.deepEqual(analyzedNotebooks, ["first.ipynb", "second.ipynb"]);
    });
  });

  it("refreshes the original managed notebook cells and preserves the chosen variable names", async () => {
    await withCommands({}, async (fixture) => {
      const original = fixture.notebook("original");
      original.cell.text = "custom_compiler = object()\ncustom_schedule = Schedule('initial')\n";
      await fixture.openNotebook("original");
      const projectFile = fixture.analyzed[0].projectFile;
      original.cell.text = "custom_compiler = object()\ncustom_schedule = Schedule('updated')\n";
      original.cells.unshift({ kind: 1, metadata: {}, document: { getText: () => "New notes" } });
      fixture.setActive(fixture.projects[1]);
      await fixture.commands["qbsTimeline.refresh"]();
      const snapshot = JSON.parse(readFileSync(join(dirname(projectFile), "selected.ipynb"), "utf8"));
      assert.equal(snapshot.cells.length, 2, "Refresh must rebuild the snapshot from the original notebook's current cells");
      assert.equal(snapshot.cells[1].source.join(""), original.cell.text);
      assert.equal(snapshot.metadata.qbstimeline.selected_cell_index, 1);
      assert.equal(snapshot.metadata.qbstimeline.source_notebook, original.document.uri.fsPath.replace(/\\/g, "/"));
      assert.match(readFileSync(projectFile, "utf8"), /schedule_variable: custom_schedule\n  compiler_variable: custom_compiler/);
      assert.equal(fixture.prompts.length, 2, "Refresh must preserve the original variable choices without prompting again");
    });
  });

  it("keeps current selected and setup cells connected after Analyze reopens its managed project", async () => {
    const snapshots: any[] = [];
    await withCommands({ analyze: async ({ projectFile }) => {
      snapshots.push(JSON.parse(readFileSync(join(dirname(projectFile), "selected.ipynb"), "utf8")));
    } }, async (fixture) => {
      const original = fixture.notebook("original");
      original.cell.metadata = { id: "selected", metadata: { tags: [] } };
      original.cell.text = "custom_compiler = object()\ncustom_schedule = Schedule('initial')\n";
      const setup = { kind: 2, metadata: { id: "setup", metadata: { tags: ["qbstimeline-setup"] } },
        text: "setup = 'initial'\n", document: { getText: () => setup.text } };
      original.cells.unshift(setup);
      await fixture.openNotebook("original");
      const projectFile = fixture.analyzed[0].projectFile;
      original.cell.text = "custom_compiler = object()\ncustom_schedule = Schedule('reanalyzed')\n";
      setup.text = "setup = 'reanalyzed'\n";
      fixture.setActive(projectFile);
      await fixture.commands["qbsTimeline.analyzeAndOpen"]();
      const inserted = { kind: 2, metadata: { id: "inserted", metadata: { tags: ["qbstimeline-setup"] } },
        document: { getText: () => "extra_setup = True\n" } };
      original.cells.splice(1, 0, inserted);
      original.cell.text = "custom_compiler = object()\ncustom_schedule = Schedule('refreshed')\n";
      await fixture.commands["qbsTimeline.refresh"]();
      assert.deepEqual(snapshots.map((snapshot) => snapshot.cells.find((cell: any) => cell.id === "selected").source.join("")), [
        "custom_compiler = object()\ncustom_schedule = Schedule('initial')\n",
        "custom_compiler = object()\ncustom_schedule = Schedule('reanalyzed')\n",
        original.cell.text,
      ]);
      assert.equal(snapshots[1].cells[0].source.join(""), setup.text);
      assert.deepEqual(snapshots[2].cells.map((cell: any) => cell.id), ["setup", "inserted", "selected"]);
      assert.deepEqual(snapshots[2].cells[1].metadata.tags, ["qbstimeline-setup"]);
      assert.equal(snapshots[2].metadata.qbstimeline.selected_cell_index, 2);
      assert.match(readFileSync(projectFile, "utf8"), /schedule_variable: custom_schedule\n  compiler_variable: custom_compiler/);
      assert.equal(fixture.prompts.length, 2);
      assert.deepEqual(fixture.errors, []);
    });
  });

  for (const target of ["file", "notebook"]) {
    it(`does not restore the original managed snapshot after its YAML is retargeted to another ${target}`, async () => {
      await withCommands({}, async (fixture) => {
        await fixture.openNotebook("original");
        const projectFile = fixture.analyzed[0].projectFile;
        const snapshotFile = join(dirname(projectFile), "selected.ipynb");
        const snapshot = readFileSync(snapshotFile, "utf8");
        const yaml = target === "file"
          ? "schedule:\n  file: replacement.py\noutputs:\n  dir: ..\n"
          : "schedule:\n  notebook: replacement.ipynb\nsource:\n  notebook: replacement.ipynb\noutputs:\n  dir: ..\n";
        writeFileSync(projectFile, yaml);
        fixture.notebook("original").cell.text = "schedule = Schedule('must not be snapshotted')\n";
        fixture.setActive(projectFile);
        await fixture.commands["qbsTimeline.analyzeAndOpen"]();
        await fixture.commands["qbsTimeline.refresh"]();
        assert.equal(fixture.analyzed.length, 3);
        assert.equal(readFileSync(projectFile, "utf8"), yaml);
        assert.equal(readFileSync(snapshotFile, "utf8"), snapshot);
        assert.deepEqual(fixture.errors, []);
      });
    });
  }

  it("preserves edited managed YAML output and variables while Analyze and Refresh update the snapshot", async () => {
    await withCommands({}, async (fixture) => {
      const original = fixture.notebook("original");
      original.cell.text = "custom_compiler = object()\ncustom_schedule = Schedule('initial')\n";
      await fixture.openNotebook("original");
      const projectFile = fixture.analyzed[0].projectFile;
      const snapshotFile = join(dirname(projectFile), "selected.ipynb");
      const yaml = readFileSync(projectFile, "utf8")
        .replace("schedule_variable: custom_schedule", "schedule_variable: alternate_schedule")
        .replace("compiler_variable: custom_compiler", "compiler_variable: alternate_compiler")
        .replace("  dir: ..\n", "  dir: custom-artifacts\n") + "# User project settings\n";
      mkdirSync(join(dirname(projectFile), "custom-artifacts"));
      writeFileSync(projectFile, yaml);
      fixture.setActive(projectFile);
      for (const command of ["qbsTimeline.analyzeAndOpen", "qbsTimeline.refresh"]) {
        original.cell.text = `alternate_compiler = object()\nalternate_schedule = Schedule('${command}')\n`;
        await fixture.commands[command]();
        assert.equal(readFileSync(projectFile, "utf8"), yaml, "Snapshot preparation must preserve the user's existing project configuration");
        assert.equal(JSON.parse(readFileSync(snapshotFile, "utf8")).cells[0].source.join(""), original.cell.text);
        assert.equal(fixture.analyzed.at(-1).irPath, join(dirname(projectFile), "custom-artifacts", "qbs_ir.json"));
      }
      assert.equal(fixture.prompts.length, 2);
      assert.deepEqual(fixture.errors, []);
    });
  });

  for (const target of ["file", "notebook"]) {
    it(`refreshes a YAML retargeted to another ${target} after its previous managed cell becomes unavailable`, async () => {
      await withCommands({}, async (fixture) => {
        const original = fixture.notebook("original");
        await fixture.openNotebook("original");
        const projectFile = fixture.analyzed[0].projectFile;
        const snapshotFile = join(dirname(projectFile), "selected.ipynb"), snapshot = readFileSync(snapshotFile, "utf8");
        const yaml = target === "file"
          ? "schedule:\n  file: replacement.py\noutputs:\n  dir: ..\n"
          : "schedule:\n  notebook: replacement.ipynb\nsource:\n  notebook: replacement.ipynb\noutputs:\n  dir: ..\n";
        writeFileSync(projectFile, yaml);
        if (target === "file") original.document.isClosed = true;
        else original.cells.splice(0, 1);
        await fixture.commands["qbsTimeline.refresh"]();
        assert.equal(fixture.analyzed.length, 2, "Refresh must analyze the current YAML without requiring the previous original cell");
        await fixture.commands["qbsTimeline.refresh"]();
        assert.equal(fixture.analyzed.length, 3);
        assert.equal(readFileSync(projectFile, "utf8"), yaml);
        assert.equal(readFileSync(snapshotFile, "utf8"), snapshot);
        assert.deepEqual(fixture.errors, []);
      });
    });
  }

  it("recreates deleted managed YAML and initializes new variable choices when opening from a cell again", async () => {
    await withCommands({}, async (fixture) => {
      const original = fixture.notebook("original");
      original.cell.text = "first_compiler = object()\nfirst_schedule = Schedule('first')\n";
      await fixture.openNotebook("original");
      const projectFile = fixture.analyzed[0].projectFile;
      unlinkSync(projectFile);
      original.cell.text = "first_compiler = object()\nfirst_schedule = Schedule('updated')\n";
      await fixture.commands["qbsTimeline.refresh"]();
      assert.match(readFileSync(projectFile, "utf8"), /schedule_variable: first_schedule\n  compiler_variable: first_compiler/);
      original.cell.text = "second_compiler = object()\nsecond_schedule = Schedule('second')\n";
      await fixture.openNotebook("original");
      assert.match(readFileSync(projectFile, "utf8"), /schedule_variable: second_schedule\n  compiler_variable: second_compiler/);
      assert.equal(fixture.analyzed.length, 3);
      assert.equal(fixture.prompts.length, 4);
      assert.deepEqual(fixture.errors, []);
    });
  });

  it("preserves managed preparation for Analyze queued before its notebook's first publication", async () => {
    const started = deferred(), release = deferred();
    const snapshots: any[] = [];
    await withCommands({ analyze: async ({ id, projectFile }) => {
      snapshots.push(JSON.parse(readFileSync(join(dirname(projectFile), "selected.ipynb"), "utf8")));
      if (id === 1) { started.resolve(); await release.promise; }
    } }, async (fixture) => {
      const original = fixture.notebook("original");
      const opening = fixture.openNotebook("original");
      await started.promise;
      fixture.setActive(fixture.analyzed[0].projectFile);
      const reanalyzing = fixture.commands["qbsTimeline.analyzeAndOpen"]();
      original.cell.text = "compiler = object()\nschedule = Schedule('edited while queued')\n";
      release.resolve();
      await Promise.all([opening, reanalyzing]);
      assert.notEqual(snapshots[0].cells[0].source.join(""), original.cell.text);
      assert.equal(snapshots[1].cells[0].source.join(""), original.cell.text);
      original.cell.text = "compiler = object()\nschedule = Schedule('next refresh')\n";
      await fixture.commands["qbsTimeline.refresh"]();
      assert.equal(snapshots[2].cells[0].source.join(""), original.cell.text);
      assert.deepEqual(fixture.errors, []);
    });
  });

  it("keeps a queued managed Refresh's original preparation when its first analysis fails", async () => {
    const started = deferred(), release = deferred();
    const snapshots: any[] = [];
    await withCommands({ analyze: async ({ id, projectFile }) => {
      snapshots.push(JSON.parse(readFileSync(join(dirname(projectFile), "selected.ipynb"), "utf8")));
      if (id === 1) { started.resolve(); await release.promise; throw new Error("First analysis failed"); }
    } }, async (fixture) => {
      const original = fixture.notebook("original");
      const opening = fixture.openNotebook("original");
      await started.promise;
      const refresh = fixture.commands["qbsTimeline.refresh"]();
      original.cell.text = "compiler = object()\nschedule = Schedule('updated recovery')\n";
      release.resolve();
      await Promise.all([opening, refresh]);
      assert.equal(fixture.analyzed.length, 2);
      assert.equal(fixture.shown.length, 1);
      assert.equal(snapshots[1].cells[0].source.join(""), original.cell.text);
      assert.equal(fixture.errors.length, 1);
      assert.match(fixture.errors[0], /first analysis failed/i);
    });
  });

  it("reads a queued managed refresh snapshot only when its serialized analysis can start", async () => {
    const started = deferred(), release = deferred();
    const snapshots: any[] = [];
    await withCommands({ analyze: async ({ id, projectFile }) => {
      snapshots.push(JSON.parse(readFileSync(join(dirname(projectFile), "selected.ipynb"), "utf8")));
      if (id === 1) { started.resolve(); await release.promise; }
    } }, async (fixture) => {
      const original = fixture.notebook("original");
      const initialText = original.cell.text;
      const opening = fixture.openNotebook("original");
      await started.promise;
      const refresh = fixture.commands["qbsTimeline.refresh"]();
      original.cell.text = "compiler = object()\nschedule = Schedule('changed while queued')\n";
      await settle();
      assert.equal(snapshots.length, 1);
      assert.equal(snapshots[0].cells[0].source.join(""), initialText);
      release.resolve();
      await Promise.all([opening, refresh]);
      assert.equal(snapshots.length, 2);
      assert.equal(snapshots[1].cells[0].source.join(""), original.cell.text);
    });
  });

  for (const change of ["edited", "deleted"]) {
    it(`rechecks the original managed cell when it is ${change} while Refresh awaits the kernel environment`, async () => {
      const started = deferred(), release = deferred();
      let checks = 0;
      await withCommands({ notebookEnvironment: async () => {
        if (++checks === 2) { started.resolve(); await release.promise; }
      } }, async (fixture) => {
        const original = fixture.notebook("original");
        await fixture.openNotebook("original");
        const refresh = fixture.commands["qbsTimeline.refresh"]();
        await started.promise;
        if (change === "edited") original.cell.text = "compiler = object()\nschedule = Schedule('edited during environment check')\n";
        else original.cells.splice(0, 1);
        release.resolve();
        await refresh;
        if (change === "edited") {
          assert.equal(fixture.analyzed.length, 2);
          const snapshot = JSON.parse(readFileSync(join(dirname(fixture.analyzed[1].projectFile), "selected.ipynb"), "utf8"));
          assert.equal(snapshot.cells[0].source.join(""), original.cell.text);
        } else {
          assert.equal(fixture.analyzed.length, 1);
          assert.match(fixture.errors[0], /original notebook cell is no longer available/i);
        }
      });
    });
  }

  for (const unavailable of ["deleted", "replaced", "markup", "closed"]) {
    it(`does not refresh an old managed snapshot after its original notebook cell is ${unavailable}`, async () => {
      await withCommands({}, async (fixture) => {
        const original = fixture.notebook("original");
        await fixture.openNotebook("original");
        const snapshotPath = join(dirname(fixture.analyzed[0].projectFile), "selected.ipynb");
        const snapshot = readFileSync(snapshotPath, "utf8");
        if (unavailable === "deleted") original.cells.splice(0, 1);
        if (unavailable === "replaced") original.cells[0] = { ...original.cell };
        if (unavailable === "markup") original.cell.kind = 1;
        if (unavailable === "closed") original.document.isClosed = true;
        await fixture.commands["qbsTimeline.refresh"]();
        assert.equal(fixture.analyzed.length, 1);
        assert.equal(fixture.shown.length, 1);
        assert.equal(readFileSync(snapshotPath, "utf8"), snapshot);
        assert.equal(fixture.errors.length, 1);
        assert.match(fixture.errors[0], /original notebook cell is no longer available/i);
      });
    });
  }

  it("auto-refreshes the exact original notebook and ignores generated managed artifacts", async () => {
    await withCommands({}, async (fixture) => {
      const original = fixture.notebook("original");
      await fixture.openNotebook("original");
      original.cell.text = "compiler = object()\nschedule = Schedule('saved edit')\n";
      fixture.change(original.document.uri.fsPath);
      await new Promise<void>((resolve) => setTimeout(resolve, 850));
      await settle();
      assert.equal(fixture.analyzed.length, 2, "Saving the original notebook must refresh its managed snapshot");
      const projectFile = fixture.analyzed[0].projectFile;
      assert.equal(JSON.parse(readFileSync(join(dirname(projectFile), "selected.ipynb"), "utf8")).cells[0].source.join(""), original.cell.text);
      fixture.change(fixture.notebook("unrelated").document.uri.fsPath);
      fixture.change(projectFile);
      fixture.change(join(dirname(projectFile), "selected.ipynb"));
      await new Promise<void>((resolve) => setTimeout(resolve, 850));
      assert.equal(fixture.analyzed.length, 2);
    });
  });

  it("does not queue a feedback refresh when reopening a managed notebook writes its generated files", async () => {
    await withCommands({}, async (fixture) => {
      await fixture.openNotebook("original");
      fixture.emitWrites();
      await fixture.openNotebook("original");
      await new Promise<void>((resolve) => setTimeout(resolve, 850));
      await settle();
      assert.equal(fixture.analyzed.length, 2, "Writing selected.ipynb and the managed YAML must not trigger a third analysis");
    });
  });

  it("ignores generated notebook writes before switching from an ordinary project to its first managed notebook", async () => {
    const started = deferred(), release = deferred();
    await withCommands({ analyze: async ({ id }) => {
      if (id === 2) { started.resolve(); await release.promise; }
    } }, async (fixture) => {
      await fixture.commands["qbsTimeline.analyzeAndOpen"]();
      fixture.emitWrites();
      const opening = fixture.openNotebook("original");
      await started.promise;
      await new Promise<void>((resolve) => setTimeout(resolve, 850));
      release.resolve();
      await opening;
      await new Promise<void>((resolve) => setTimeout(resolve, 150));
      assert.equal(fixture.analyzed.length, 2, "The old project's broad watcher must ignore the new managed artifacts before publication");
      assert.equal(fixture.shown.length, 2);
    });
  });

  it("retains ordinary project file watching after switching away from a managed notebook", async () => {
    await withCommands({}, async (fixture) => {
      await fixture.openNotebook("original");
      fixture.setActive(fixture.projects[1]);
      await fixture.commands["qbsTimeline.analyzeAndOpen"]();
      fixture.change(join(dirname(fixture.projects[1]), "helper.py"));
      await new Promise<void>((resolve) => setTimeout(resolve, 850));
      await settle();
      assert.equal(fixture.analyzed.length, 3);
      assert.equal(fixture.analyzed[2].projectFile, fixture.projects[1]);
    });
  });

  for (const reenable of [false, true]) {
    it(`cancels an automatic refresh waiting in the analysis queue when auto-refresh is disabled${reenable ? " and enabled again" : ""}`, async () => {
      const started = deferred(), release = deferred();
      let enabled = true;
      await withCommands({
        autoRefreshFor: () => enabled,
        analyze: async ({ id }) => { if (id === 2) { started.resolve(); await release.promise; } },
      }, async (fixture) => {
        await fixture.commands["qbsTimeline.analyzeAndOpen"]();
        const manual = fixture.commands["qbsTimeline.refresh"]();
        await started.promise;
        fixture.change(join(dirname(fixture.projects[0]), "schedule.py"));
        await new Promise<void>((resolve) => setTimeout(resolve, 850));
        enabled = false;
        fixture.configurationChanged();
        assert.equal(fixture.watcherCount(), 0);
        if (reenable) {
          enabled = true;
          fixture.configurationChanged();
        }
        release.resolve();
        await manual;
        await settle();
        assert.equal(fixture.analyzed.length, 2, "Turning auto-refresh off must cancel an automatic job that has not started analysis");
        await fixture.commands["qbsTimeline.refresh"]();
        assert.equal(fixture.analyzed.length, 3, "Explicit Refresh must remain available");
        enabled = true;
        fixture.configurationChanged();
        fixture.change(join(dirname(fixture.projects[0]), "schedule.py"));
        await new Promise<void>((resolve) => setTimeout(resolve, 850));
        await settle();
        assert.equal(fixture.analyzed.length, 4, "Enabling auto-refresh must allow a new source change to analyze");
      });
    });
  }

  it("finishes an automatic analysis that already started before auto-refresh was disabled", async () => {
    const started = deferred(), release = deferred();
    let enabled = true;
    await withCommands({
      autoRefreshFor: () => enabled,
      analyze: async ({ id }) => { if (id === 2) { started.resolve(); await release.promise; } },
    }, async (fixture) => {
      await fixture.commands["qbsTimeline.analyzeAndOpen"]();
      fixture.change(join(dirname(fixture.projects[0]), "schedule.py"));
      await started.promise;
      enabled = false;
      fixture.configurationChanged();
      release.resolve();
      await settle();
      assert.equal(fixture.analyzed.length, 2);
      assert.equal(fixture.shown.length, 2, "An already running analysis must still publish its completed result");
    });
  });

  for (const scheduleKind of ["file", "notebook"]) {
    it(`auto-refreshes a configured workspace ${scheduleKind} outside the YAML project directory`, async () => {
      await withCommands({}, async (fixture) => {
        const sharedDir = join(dirname(dirname(fixture.projects[0])), "shared");
        mkdirSync(sharedDir);
        const sourceName = scheduleKind === "file" ? "schedule.py" : "experiment.ipynb";
        const source = join(sharedDir, sourceName);
        const code = "from types import SimpleNamespace\nclass Compiler:\n    def compile(self, schedule):\n        schedule.compiled_instructions = {}\n        return schedule\nschedule = SimpleNamespace(name='shared', schedulables={}, operations={})\ncompiler = Compiler()\n";
        const content = scheduleKind === "file" ? `${code}\ndef build_schedule():\n    return schedule\ndef build_compiler():\n    return compiler\n` : JSON.stringify({
          cells: [{ cell_type: "code", source: code, metadata: { tags: ["qbstimeline-schedule"] } }],
          metadata: {}, nbformat: 4, nbformat_minor: 5,
        });
        writeFileSync(source, content);
        writeFileSync(fixture.projects[0], `schedule:\n  ${scheduleKind}: ../shared/${sourceName}\noutputs:\n  dir: .qbs_timeline\n`);
        await fixture.commands["qbsTimeline.analyzeAndOpen"]();
        writeFileSync(source, content.replace("shared", "edited"));
        fixture.change(source);
        await new Promise<void>((resolve) => setTimeout(resolve, 850));
        await settle();
        assert.equal(fixture.analyzed.length, 2, "The configured source must refresh even when it is outside its YAML folder");
        assert.equal(fixture.analyzed[1].projectFile, fixture.projects[0]);
        fixture.change(join(sharedDir, "unrelated.py"));
        fixture.change(join(dirname(fixture.projects[1]), "schedule.py"));
        await new Promise<void>((resolve) => setTimeout(resolve, 850));
        assert.equal(fixture.analyzed.length, 2, "Unrelated sources outside the current project must still be ignored");
      });
    });
  }

  it("keeps old-project auto refresh from reverting a pending explicit project switch", async () => {
    const started = deferred(), release = deferred();
    await withCommands({ analyze: async ({ id }) => { if (id === 2) { started.resolve(); await release.promise; } } }, async (fixture) => {
      await fixture.commands["qbsTimeline.analyzeAndOpen"]();
      fixture.setActive(fixture.projects[1]);
      const second = fixture.commands["qbsTimeline.analyzeAndOpen"]();
      await started.promise;
      fixture.change(join(dirname(fixture.projects[0]), "schedule.py"));
      await new Promise<void>((resolve) => setTimeout(resolve, 800));
      release.resolve();
      await second;
      await settle();
      assert.equal(fixture.analyzed.at(-1).projectFile, fixture.projects[1]);
      assert.equal(fixture.shown.at(-1).projectFile, fixture.projects[1]);
    });
  });
});
