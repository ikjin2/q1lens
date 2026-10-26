// @ts-nocheck
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const Module = require("node:module");

function createUri(fsPath: string) {
  return {
    fsPath,
    toString() {
      return `file://${fsPath}`;
    },
  };
}

function createMockVscode() {
  const vscode: any = {
    Uri: {
      file: (fsPath: string) => createUri(fsPath),
      parse: (value: string) => createUri(value.replace(/^file:\/\//, "")),
      joinPath: (base: any, ...parts: string[]) => createUri([base.fsPath, ...parts].join("\\")),
    },
    ViewColumn: { Active: -1, Beside: 2 },
    RelativePattern: class { constructor(public baseUri, public pattern) {} },
    DiagnosticSeverity: { Error: 0, Warning: 1, Information: 2, Hint: 3 },
    window: {
      activeTextEditor: undefined,
      visibleTextEditors: [],
      tabGroups: { all: [] },
      createOutputChannel: () => ({
        appendLine: () => {},
        show: () => {},
        dispose: () => {},
      }),
      createTextEditorDecorationType: () => ({ dispose: () => {} }),
      onDidChangeTextEditorSelection: () => ({ dispose: () => {} }),
      onDidChangeActiveTextEditor: () => ({ dispose: () => {} }),
      showWarningMessage: async () => undefined,
      showInformationMessage: async () => undefined,
      showErrorMessage: async () => undefined,
      showInputBox: async () => undefined,
      showQuickPick: async (items: any[]) => items[0],
      createWebviewPanel: () => ({
        webview: {
          html: "",
          onDidReceiveMessage: () => ({ dispose: () => {} }),
          asWebviewUri: (uri: any) => uri,
        },
        reveal: () => {},
        onDidDispose: () => ({ dispose: () => {} }),
        dispose: () => {},
      }),
    },
    workspace: {
      workspaceFolders: [],
      textDocuments: [],
      getConfiguration: () => ({ get: (_key: string, fallback: any) => fallback }),
      getWorkspaceFolder: () => undefined,
      findFiles: async () => [],
      asRelativePath: (uri: any) => uri.fsPath,
      createFileSystemWatcher: () => ({
        onDidChange: () => ({ dispose: () => {} }),
        onDidCreate: () => ({ dispose: () => {} }),
        onDidDelete: () => ({ dispose: () => {} }),
        dispose: () => {},
      }),
      onDidSaveTextDocument: () => ({ dispose: () => {} }),
      onDidChangeTextDocument: () => ({ dispose: () => {} }),
    },
    languages: {
      createDiagnosticCollection: () => ({
        clear: () => {},
        set: () => {},
        dispose: () => {},
      }),
    },
    commands: {
      executeCommand: async () => undefined,
    },
    Range: class {
      constructor(public startLine: any, public startColumn: any, public endLine?: any, public endColumn?: any) {}
      get start() { return typeof this.startLine === "number" ? { line: this.startLine, character: this.startColumn } : this.startLine; }
      get end() { return typeof this.startLine === "number" ? { line: this.endLine, character: this.endColumn } : this.startColumn; }
    },
    Position: class {
      constructor(public line: number, public character: number) {}
    },
    Selection: class {
      constructor(public anchor: any, public active: any) {}
      get start() { return this.anchor; }
      get end() { return this.active; }
      get isEmpty() { return this.anchor.line === this.active.line && this.anchor.character === this.active.character; }
    },
    TextEditorRevealType: { InCenter: 1 },
    Diagnostic: class {
      constructor(public range: any, public message: string, public severity: number) {}
    },
    Location: class {
      constructor(public uri: any, public range: any) {}
    },
    DiagnosticRelatedInformation: class {
      constructor(public location: any, public message: string) {}
    },
    MarkdownString: class {
      value = "";
      appendMarkdown(text: string) {
        this.value += text;
      }
    },
    Hover: class {
      constructor(public markdown: any, public range: any) {}
    },
  };
  return vscode;
}

function createContext(storedProjectFile?: string) {
  return {
    subscriptions: [],
    extensionPath: "C:\\extension",
    extensionUri: createUri("C:\\extension"),
    workspaceState: {
      get: () => storedProjectFile,
      update: async () => undefined,
    },
  };
}

function fileWatchSubscriptions(vscode) {
  const path = require("node:path"), { minimatch } = require("minimatch");
  const watchers = [];
  const relativePath = (root, file) => {
    const relative = path.relative(root, file);
    return relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)
      ? undefined : relative.replace(/\\/g, "/");
  };
  vscode.RelativePattern = class { constructor(public baseUri, public pattern) {} };
  vscode.workspace.getWorkspaceFolder = (uri) => vscode.workspace.workspaceFolders.find((folder) => relativePath(folder.uri.fsPath, uri.fsPath) !== undefined);
  vscode.workspace.createFileSystemWatcher = (pattern) => {
    const watcher = { pattern, disposed: false, handlers: {},
      baseExistedAtRegistration: typeof pattern === "string" || existsSync(pattern.baseUri.fsPath),
      onDidChange(listener) { this.handlers.change = listener; return { dispose() {} }; },
      onDidCreate(listener) { this.handlers.create = listener; return { dispose() {} }; },
      onDidDelete(listener) { this.handlers.delete = listener; return { dispose() {} }; },
      dispose() { this.disposed = true; },
    };
    watchers.push(watcher);
    return watcher;
  };
  return { watchers, async dispatch(file, change = "change") {
    const pending = [];
    for (const watcher of [...watchers]) {
      if (watcher.disposed) continue;
      if (change === "delete" && typeof watcher.pattern !== "string" && !existsSync(watcher.pattern.baseUri.fsPath)) watcher.baseWasDeleted = true;
      // Both native host probes miss the first create when this RelativePattern base does not exist.
      if (change === "create" && (!watcher.baseExistedAtRegistration || watcher.baseWasDeleted)) continue;
      const roots = typeof watcher.pattern === "string" ? vscode.workspace.workspaceFolders.map((folder) => folder.uri.fsPath)
        : [watcher.pattern.baseUri.fsPath];
      const pattern = typeof watcher.pattern === "string" ? watcher.pattern : watcher.pattern.pattern;
      if (roots.some((root) => {
        const relative = relativePath(root, file);
        // Native VS Code also reports deletion of a RelativePattern's base directory itself.
        if (typeof watcher.pattern !== "string" && relative === "" && change === "delete") return true;
        return relative !== undefined && minimatch(relative, pattern, { nocase: process.platform !== "linux", dot: true });
      })) pending.push(watcher.handlers[change]?.(vscode.Uri.file(file)));
    }
    await Promise.all(pending);
  } };
}

function resolvedPython(resource, pythonPath = "python", pythonArgs = []) {
  return { pythonPath, pythonArgs, source: "vscode", sourceLabel: "VS Code selection", resource };
}

async function withMockedVscode(vscode: any, callback: (controllerModule: any) => Promise<void>) {
  const originalLoad = Module._load;
  Module._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
    if (request === "vscode") {
      return vscode;
    }
    if (request === "../fileEnvironment") {
      return { ensureFilePythonEnvironment: vscode.resolveFileEnvironment ||
        (async (_api, resource) => resolvedPython(resource)) };
    }
    if (request === "./playbackClient" && vscode.PlaybackClient) return { PlaybackClient: vscode.PlaybackClient };
    return originalLoad.call(this, request, parent, isMain);
  };
  const modulePath = require.resolve("../../src/q1timeline/controller");
  delete require.cache[modulePath];
  try {
    await callback(require("../../src/q1timeline/controller"));
  } finally {
    delete require.cache[modulePath];
    Module._load = originalLoad;
  }
}

async function withControllerFileEnvironment(vscode, probe, callback) {
  const file = require.resolve("../../src/fileEnvironment");
  const originalLoad = Module._load;
  Module._load = function (request, parent, isMain) {
    if (parent?.filename === file && request === "./environment") return { probePythonEnvironment: probe };
    if (parent?.filename === file && request === "./pythonEnvironment") return {
      resolveFilePythonEnvironment: async (_api, resource) => resolvedPython(resource, resource.fsPath),
      selectVSCodeInterpreter: vscode.selectFileInterpreter || (async () => vscode.commands.executeCommand("python.setInterpreter")),
    };
    return originalLoad.call(this, request, parent, isMain);
  };
  delete require.cache[file];
  try {
    vscode.resolveFileEnvironment = require(file).ensureFilePythonEnvironment;
    await withMockedVscode(vscode, callback);
  } finally { Module._load = originalLoad; delete require.cache[file]; }
}

function automaticSourceFollow(vscode: any, TimelineController: any) {
  const sourcePath = "C:\\repo\\q1asm\\seq0.q1asm";
  const posted: any[] = [];
  const editor = { document: { uri: vscode.Uri.file(sourcePath), version: 1, isDirty: false }, selection: sourceSelection() };
  vscode.window.activeTextEditor = editor;
  const controller = new TimelineController(createContext());
  controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
  controller.projectRelatedPaths = new Set([sourcePath]);
  controller.panel = { webview: { postMessage: (message) => posted.push(message) }, dispose() {} };
  controller.webviewReady = true;
  controller.timelineIr = {
    source_map: { by_source: { [`${sourcePath}:3`]: ["e3", "removed", "missing"], [`${sourcePath}:4`]: ["e4"], [`${sourcePath}:5`]: ["e5"] } },
    events: [{ id: "e3" }, { id: "e4" }, { id: "e5" }, { id: "removed", meta: { diff_status: "removed" } }],
  };
  return { controller, editor, posted, sourcePath };
}

function sourceSelection(startLine = 2, endLine = 4, endCharacter = 0, backwards = false) {
  const start = { line: startLine, character: 0 };
  const end = { line: endLine, character: endCharacter };
  return { start, end, anchor: backwards ? end : start, active: backwards ? start : end,
    isEmpty: startLine === endLine && endCharacter === 0 };
}

function loopSourceNavigation(vscode: any, TimelineController: any, lines: string[]) {
  const sourcePath = "C:\\repo\\q1asm\\seq0.q1asm";
  const posted: any[] = [];
  const decorated: any[] = [];
  const revealed: any[] = [];
  let selectionHandler: any;
  let activeHandler: any;
  vscode.TextEditorSelectionChangeKind = { Keyboard: 1, Mouse: 2, Command: 3 };
  vscode.window.onDidChangeTextEditorSelection = (handler: any) => { selectionHandler = handler; return { dispose() {} }; };
  vscode.window.onDidChangeActiveTextEditor = (handler: any) => { activeHandler = handler; return { dispose() {} }; };
  const document = {
    uri: vscode.Uri.file(sourcePath), version: 1, isDirty: false, lineCount: lines.length,
    lineAt(line: number) {
      assert.ok(line >= 0 && line < lines.length, `line ${line} is outside the source document`);
      return { text: lines[line], range: new vscode.Range(line, 0, line, lines[line].length) };
    },
  };
  let selection: any = sourceSelection(0, 0, 0);
  const editor = {
    document, viewColumn: 1,
    setDecorations(_type: any, ranges: any[]) { decorated.push(ranges); },
    revealRange(range: any) { revealed.push(range); },
    get selection() { return selection; },
    set selection(value) { selection = value; selectionHandler({ textEditor: editor, kind: 3 }); },
  };
  vscode.workspace.textDocuments = [document];
  vscode.window.visibleTextEditors = [editor];
  vscode.window.activeTextEditor = editor;
  vscode.window.showTextDocument = async () => { activeHandler(editor); return editor; };
  const controller = new TimelineController(createContext());
  controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
  controller.projectRelatedPaths = new Set([sourcePath]);
  controller.panel = { webview: { postMessage: (message: any) => posted.push(message) }, dispose() {} };
  controller.webviewReady = true;
  return {
    controller, editor, document, sourcePath, posted, decorated, revealed,
    source: (line: number, extra: any = {}) => ({ file: sourcePath, line, column: 1, ...extra }),
    selectionEvent: (kind?: number, target = editor) => selectionHandler({ textEditor: target, kind }),
    activeEvent: (target = editor) => activeHandler(target),
  };
}

async function withFakeTimers(callback: (clock: any) => Promise<void>) {
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;
  const pending = new Map<number, { at: number; callback: () => void }>();
  let now = 0;
  let nextId = 1;
  global.setTimeout = ((callback, delay = 0) => {
    const id = nextId++;
    pending.set(id, { at: now + Number(delay), callback });
    return id;
  }) as any;
  global.clearTimeout = ((id) => { pending.delete(Number(id)); }) as any;
  try {
    await callback({ advance(milliseconds: number) {
      const end = now + milliseconds;
      while (true) {
        const due = [...pending.entries()].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        now = due[1].at;
        pending.delete(due[0]);
        due[1].callback();
      }
      now = end;
    } });
  } finally {
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
  }
}

describe("q1timeline controller", () => {
  function fileEnvironmentController(TimelineController, vscode) {
    const controller = new TimelineController(createContext());
    const outputDir = mkdtempSync(join(tmpdir(), "q1-python-selection-"));
    const calls = [];
    controller.projectUri = vscode.Uri.file(join(outputDir, "q1timeline.yml"));
    controller.outputDir = outputDir;
    controller.panel = vscode.window.createWebviewPanel();
    controller.panel.webview.postMessage = () => {};
    controller.refreshWebview = async () => true;
    controller.execFile = async (pythonPath, args, _timeout, cwd, env, stage) => {
      calls.push({ pythonPath, args, stage, cwd, env });
      writeFileSync(args[args.indexOf("--out") + 1], stage === "analyzer"
        ? JSON.stringify({ events: [] }) : "<html><body></body></html>");
      return { stdout: JSON.stringify({ schema_version: "0.2.0", status: "ok" }), stderr: "", exitCode: 0, elapsedMs: 1 };
    };
    return { controller, calls };
  }

  it("hides Windows subprocess windows for Q1ASM analysis and rendering", async () => {
    const vscode = createMockVscode();
    const childProcess = require("node:child_process");
    const originalExecFile = childProcess.execFile;
    const launches: any[] = [];
    childProcess.execFile = (command, args, options, callback) => {
      const stage = args[args.indexOf("q1timeline") + 1];
      launches.push({ command, stage, options });
      writeFileSync(args[args.indexOf("--out") + 1], stage === "analyze"
        ? JSON.stringify({ events: [] }) : "<html><head></head><body>Timeline</body></html>");
      queueMicrotask(() => callback(null, stage === "analyze"
        ? JSON.stringify({ schema_version: "0.2.0", status: "ok" }) : "", ""));
    };
    try {
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const { controller } = fileEnvironmentController(TimelineController, vscode);
        controller.execFile = TimelineController.prototype.execFile.bind(controller);
        try {
          await controller.runAnalysis();
          assert.equal(controller.analysisStatus.status, "idle");
          assert.deepEqual(launches.map((launch) => launch.stage), ["analyze", "render"]);
          assert.deepEqual(launches.map((launch) => launch.options.windowsHide), [true, true],
            "Q1ASM analyzer and renderer must request hidden console windows on Windows");
        } finally {
          controller.dispose();
        }
      });
    } finally {
      childProcess.execFile = originalExecFile;
    }
  });

  for (const scenario of [
    ...["missing package", "unsupported Python", "open Setup", "pending notification", "pending error", "Setup recheck", "pending interpreter focus"].map((failure) => ({ failure })),
    { failure: "open Setup", sameProject: true },
  ]) {
    const failure = scenario.failure;
    it(scenario.sameProject ? "releases an obsolete Setup when Refresh is queued for the same project"
      : `cancels an obsolete file environment's ${failure} recovery when another project opens`, async () => {
      const vscode = createMockVscode();
      const messages: string[] = [];
      let release!: (action?: string) => void;
      let started!: () => void;
      let setupStarted!: () => void;
      const waiting = new Promise<void>((resolve) => { started = resolve; });
      const setupWaiting = new Promise<void>((resolve) => { setupStarted = resolve; });
      const notification = async (message) => {
        messages.push(message);
        if (failure === "pending notification" || failure === "pending error") {
          return new Promise<string | undefined>((resolve) => { release = resolve; started(); });
        }
        if (failure === "pending interpreter focus") return "Select Python Environment";
        return failure === "open Setup" || failure === "Setup recheck" ? "Show Setup" : undefined;
      };
      vscode.window.showInformationMessage = vscode.window.showErrorMessage = notification;
      vscode.extensions = { getExtension: () => ({ packageJSON: { version: "0.2.2" } }) };
      let selections = 0;
      vscode.commands.executeCommand = async () => { selections += 1; };
      if (failure === "pending interpreter focus") {
        vscode.selectFileInterpreter = require("../../src/pythonEnvironment").selectVSCodeInterpreter;
        vscode.window.showTextDocument = async () => new Promise<void>((resolve) => { release = resolve; started(); });
      }
      const createPanel = vscode.window.createWebviewPanel;
      let setupPanel: any;
      let setupReceive!: (message: any) => Promise<void>;
      let setupDisposed = false;
      const setupPosts: any[] = [];
      vscode.window.createWebviewPanel = (type, ...args) => {
        if (type !== "q1lens.notebookSetup") return createPanel(type, ...args);
        let disposed;
        setupPanel = {
          webview: { html: "", onDidReceiveMessage: (listener) => { setupReceive = listener; return { dispose() {} }; },
            postMessage: async (message) => { setupPosts.push(message); return true; } },
          onDidDispose: (listener) => { disposed = listener; return { dispose() {} }; },
          dispose: () => { if (!setupDisposed) { setupDisposed = true; disposed?.(); } },
        };
        setupStarted();
        if (failure === "open Setup") started();
        return setupPanel;
      };
      let checks = 0;
      await withControllerFileEnvironment(vscode, async (executable) => {
        const count = ++checks;
        if (count === 1 && ["missing package", "unsupported Python"].includes(failure)) {
          await new Promise<void>((resolve) => { release = resolve; started(); });
        }
        if (count === 2 && failure === "Setup recheck") {
          await new Promise<void>((resolve) => { release = resolve; started(); });
          throw new Error("Obsolete Setup recheck failed");
        }
        const unsupported = count === 1 && ["unsupported Python", "pending error"].includes(failure);
        return { executable, version: unsupported ? "3.9.0" : "3.12.0", supported: !unsupported, q1lensInstalled: count !== 1,
          ...(count === 1 && !unsupported ? { qbsError: "missing q1lens" } : {}) };
      }, async ({ TimelineController }) => {
        const setup = sourceFileController(TimelineController, vscode);
        const { controller, calls } = setup;
        const projectA = controller.projectUri.fsPath;
        const rootB = mkdtempSync(join(tmpdir(), "q1-old-environment-"));
        const projectB = join(rootB, "q1timeline.yml");
        for (const projectFile of [projectA, projectB]) writeFileSync(projectFile, "sequencers:\n  - id: seq0\n    file: seq0.q1asm\n");
        writeFileSync(join(rootB, "seq0.q1asm"), "stop\n");
        let opening: Promise<void> | undefined;
        let rechecking: Promise<void> | undefined;
        try {
          opening = controller.openTarget({ projectFile: projectA });
          if (failure === "Setup recheck") {
            await setupWaiting;
            rechecking = setupReceive({ type: "checkAgain" });
          }
          await waiting;
          const oldMessages = messages.length;
          if (scenario.sameProject) await controller.refreshPreview();
          else await controller.openTarget({ projectFile: projectB });
          if (!scenario.sameProject && failure !== "missing package" && failure !== "unsupported Python") {
            assert.equal(calls.filter((call) => call.stage === "analyzer").length, 1,
              "B must finish without waiting for A's visible recovery UI");
          }
          if (setupPanel) assert.equal(setupDisposed, true, "Only the obsolete analysis's Setup should be closed");
          release?.("Select Python Environment");
          await opening;
          await rechecking;
          await setup.settle();
          const analyzers = calls.filter((call) => call.stage === "analyzer");
          assert.equal(analyzers.length, 1);
          assert.equal(analyzers[0].args[analyzers[0].args.indexOf("--project") + 1], scenario.sameProject ? projectA : projectB);
          assert.equal(messages.length, oldMessages, "Obsolete checks must not open another recovery notification");
          assert.equal(selections, 0, "A late response to the old notification must not select another interpreter");
          assert.deepEqual(setupPosts, [], "An aborted Setup recheck must not publish a late error or idle response");
          assert.equal(controller.analysisQueued, false);
          assert.equal(controller.analysisInFlight, false);
        } finally {
          release?.();
          setupPanel?.dispose();
          await opening;
          await rechecking;
          await setup.settle();
          controller.dispose();
        }
      });
    });
  }

  for (const failure of ["missing package", "unsupported Python", "open Setup", "pending notification"]) {
    it(`cancels playback's obsolete ${failure} recovery when another project opens`, async () => {
      const vscode = createMockVscode();
      let phase = "initial";
      let projectA: string;
      const messages: string[] = [];
      let release!: (action?: string) => void;
      let started!: () => void;
      const waiting = new Promise<void>((resolve) => { started = resolve; });
      const notification = async (message) => {
        if (phase === "initial") return undefined;
        messages.push(message);
        if (failure === "pending notification" && messages.length === 1) return new Promise<string | undefined>((resolve) => { release = resolve; started(); });
        return failure === "open Setup" ? "Show Setup" : undefined;
      };
      vscode.window.showInformationMessage = vscode.window.showErrorMessage = notification;
      vscode.extensions = { getExtension: () => ({ packageJSON: { version: "0.2.2" } }) };
      let selections = 0;
      vscode.commands.executeCommand = async () => { selections += 1; };
      const createPanel = vscode.window.createWebviewPanel;
      let setupPanel: any;
      let setupDisposed = false;
      vscode.window.createWebviewPanel = (type, ...args) => {
        if (type !== "q1lens.notebookSetup") return createPanel(type, ...args);
        let disposed;
        setupPanel = {
          webview: { html: "", onDidReceiveMessage: () => ({ dispose() {} }), postMessage: async () => true },
          onDidDispose: (listener) => { disposed = listener; return { dispose() {} }; },
          dispose: () => { if (!setupDisposed) { setupDisposed = true; disposed?.(); } },
        };
        started();
        return setupPanel;
      };
      await withControllerFileEnvironment(vscode, async (executable) => {
        const oldPlayback = executable === projectA && phase === "playback";
        if (oldPlayback && ["missing package", "unsupported Python"].includes(failure)) {
          await new Promise<void>((resolve) => { release = resolve; started(); });
        }
        const missing = executable === projectA;
        const unsupported = oldPlayback && failure === "unsupported Python";
        return { executable, version: unsupported ? "3.9.0" : "3.12.0", supported: !unsupported,
          q1lensInstalled: !missing, ...(missing && !unsupported ? { qbsError: "missing q1lens" } : {}) };
      }, async ({ TimelineController }) => {
        const setup = sourceFileController(TimelineController, vscode);
        const { controller, calls } = setup;
        projectA = controller.projectUri.fsPath;
        const rootB = mkdtempSync(join(tmpdir(), "q1-old-playback-environment-"));
        const projectB = join(rootB, "q1timeline.yml");
        for (const projectFile of [projectA, projectB]) writeFileSync(projectFile, "sequencers:\n  - id: seq0\n    file: seq0.q1asm\n");
        writeFileSync(join(rootB, "seq0.q1asm"), "stop\n");
        let running: Promise<void> | undefined;
        try {
          await controller.openTarget({ projectFile: projectA });
          assert.equal(controller.pythonEnvironment, undefined, "Dismissed setup leaves Run able to check this file's environment again");
          phase = "playback";
          running = controller.handleWebviewMessage({ type: "executionControl", action: "play" });
          await waiting;
          const oldMessages = messages.length;
          await controller.openTarget({ projectFile: projectB });
          await setup.settle();
          assert.equal(calls.filter((call) => call.stage === "analyzer").length, 1, "B must analyze without waiting for A's playback recovery");
          if (setupPanel) assert.equal(setupDisposed, true, "Resetting playback must close only its obsolete Setup");
          release?.("Select Python Environment");
          await running;
          assert.equal(messages.length, oldMessages, "A late playback check must not open recovery UI over B");
          assert.equal(selections, 0, "An obsolete playback notification must not open another interpreter picker");
          assert.equal(controller.pythonEnvironment.resource.fsPath, projectB);
          assert.equal(controller.playbackInFlight, false);
          assert.equal(controller.executionState.status, "idle");
        } finally {
          release?.();
          setupPanel?.dispose();
          await running;
          await setup.settle();
          controller.dispose();
        }
      });
    });
  }

  it("uses the original file's selected Python for analysis and rendering, scoped to its workspace", async () => {
    const vscode = createMockVscode();
    const resolutions = [];
    const scopes = [];
    const selectedPython = join(tmpdir(), "project with spaces", ".venv", "bin", "python");
    vscode.resolveFileEnvironment = async (_api, resource, kind) => {
      resolutions.push({ resource, kind });
      return resolvedPython(resource, selectedPython);
    };
    vscode.workspace.getConfiguration = (_name, resource) => {
      scopes.push(resource);
      return { get: (key, fallback) => key === "pythonPath" ? "wrong-system-python" : fallback };
    };
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const { controller, calls } = fileEnvironmentController(TimelineController, vscode);
      controller.singleFileUri = vscode.Uri.file(join(tmpdir(), "original-project", "seq0.q1asm"));
      vscode.window.activeTextEditor = { document: { uri: vscode.Uri.file(join(tmpdir(), "other-project", "main.py")) } };
      await controller.runAnalysis();
      assert.deepEqual(resolutions, [{ resource: controller.singleFileUri, kind: "q1asm" }]);
      assert.ok(scopes.every((resource) => resource === controller.singleFileUri));
      assert.deepEqual(calls.map(({ pythonPath, stage }) => ({ pythonPath, stage })), [
        { pythonPath: selectedPython, stage: "analyzer" }, { pythonPath: selectedPython, stage: "renderer" },
      ]);
      controller.dispose();
    });
  });

  it("keeps view changes and playback on the analyzed Python, then re-resolves the original project on Refresh", async () => {
    const vscode = createMockVscode();
    const resolutions = [];
    const playbackStarts = [];
    let selectedPython = join(tmpdir(), "first", ".venv", "bin", "python");
    const firstPython = selectedPython;
    vscode.resolveFileEnvironment = async (_api, resource) => {
      resolutions.push(resource);
      return resolvedPython(resource, selectedPython, ["-X", "utf8"]);
    };
    vscode.PlaybackClient = class {
      constructor(command, args) { playbackStarts.push({ command, args }); }
      async request(message) {
        const playback = { status: "completed", can_resume: false, steps: 1 };
        return message.command === "snapshot" ? { ir: { events: [], playback } } : { playback };
      }
      dispose() {}
    };
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const { controller, calls } = fileEnvironmentController(TimelineController, vscode);
      const originalProject = controller.projectUri;
      await controller.runAnalysis();
      selectedPython = join(tmpdir(), "second", ".venv", "bin", "python");
      vscode.window.activeTextEditor = { document: { uri: vscode.Uri.file(join(tmpdir(), "unrelated", "main.py")) } };
      await controller.setViewMode("normal");
      await controller.runPlayback();
      assert.deepEqual(resolutions, [originalProject]);
      assert.ok(calls.every((call) => call.pythonPath === firstPython));
      assert.ok(calls.every((call) => call.args[0] === "-X" && call.args[1] === "utf8"));
      assert.equal(playbackStarts[0].command, firstPython);
      assert.deepEqual(playbackStarts[0].args.slice(0, 2), ["-X", "utf8"]);
      assert.equal(calls.filter((call) => call.stage === "renderer").length, 3);
      await controller.refreshPreview();
      assert.deepEqual(resolutions, [originalProject, originalProject]);
      assert.deepEqual(calls.slice(-2).map((call) => call.pythonPath), [selectedPython, selectedPython]);
      controller.dispose();
    });
  });

  it("does not fall back to configured or cached Python when environment setup is cancelled", async () => {
    const vscode = createMockVscode();
    let available = true;
    vscode.resolveFileEnvironment = async (_api, resource) => available ? resolvedPython(resource, "previous-python") : undefined;
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const { controller, calls } = fileEnvironmentController(TimelineController, vscode);
      await controller.runAnalysis();
      available = false;
      calls.length = 0;
      await controller.refreshPreview();
      assert.equal(controller.analysisInFlight, false);
      assert.equal(controller.analysisStatus.status, "error");
      assert.equal(controller.pythonEnvironment, undefined);
      assert.deepEqual(calls, []);
      controller.createPlaybackClient = () => assert.fail("No playback process may start without a resolved environment");
      await controller.runPlayback();
      assert.equal(controller.executionState.status, "blocked");
      await controller.setViewMode("normal");
      assert.deepEqual(calls, []);
      controller.dispose();
    });
  });

  for (const missingIr of [false, true]) {
    it(`drops a view mode change after switching projects during ${missingIr ? "missing" : "existing"} IR inspection`, async () => {
      const vscode = createMockVscode();
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const { controller, calls } = fileEnvironmentController(TimelineController, vscode);
        controller.pythonEnvironment = resolvedPython(controller.projectUri);
        const timelineIrPath = join(controller.outputDir, "timeline_ir.json");
        writeFileSync(timelineIrPath, JSON.stringify({ events: [] }));
        const fsPromises = require("node:fs").promises;
        const originalAccess = fsPromises.access;
        let release!: () => void;
        let started!: () => void;
        const waiting = new Promise<void>((resolve) => { started = resolve; });
        fsPromises.access = async (file, ...args) => {
          if (file === timelineIrPath) {
            await new Promise<void>((resolve) => { release = resolve; started(); });
            if (missingIr) throw Object.assign(new Error("No timeline IR"), { code: "ENOENT" });
          }
          return originalAccess(file, ...args);
        };
        let analyses = 0;
        controller.runAnalysis = async () => { analyses += 1; };
        try {
          const changing = controller.setViewMode("normal");
          await waiting;
          const outputB = mkdtempSync(join(tmpdir(), "q1-view-project-b-"));
          controller.setProjectUri(vscode.Uri.file(join(outputB, "q1timeline.yml")));
          controller.outputDir = outputB;
          controller.analysisStatus = { status: "idle", message: "Project B" };
          const requestB = controller.analysisRequestId;
          release();
          await changing;
          assert.deepEqual(calls, [], "A view change for A must not launch a renderer after switching to B");
          assert.equal(analyses, 0, "Missing A output must not start an unsolicited analysis of B");
          assert.equal(controller.analysisRequestId, requestB);
          assert.deepEqual(controller.analysisStatus, { status: "idle", message: "Project B" });
        } finally {
          fsPromises.access = originalAccess;
          controller.dispose();
        }
      });
    });
  }

  it("keeps the latest view mode when IR inspection finishes out of order", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const { controller } = fileEnvironmentController(TimelineController, vscode);
      controller.pythonEnvironment = resolvedPython(controller.projectUri);
      const timelineIrPath = join(controller.outputDir, "timeline_ir.json");
      writeFileSync(timelineIrPath, JSON.stringify({ events: [] }));
      const fsPromises = require("node:fs").promises;
      const originalAccess = fsPromises.access;
      const releases: Array<() => void> = [];
      fsPromises.access = async (file, ...args) => {
        if (file === timelineIrPath) await new Promise<void>((resolve) => { releases.push(resolve); });
        return originalAccess(file, ...args);
      };
      const renderedModes: string[] = [];
      controller.runRender = async (...args) => { renderedModes.push(args[7]); };
      try {
        const older = controller.setViewMode("debug");
        const newer = controller.setViewMode("normal");
        assert.equal(releases.length, 2);
        releases[1]();
        await newer;
        releases[0]();
        await older;
        assert.deepEqual(renderedModes, ["normal"]);
        assert.equal(controller.currentViewMode, "normal");
      } finally {
        fsPromises.access = originalAccess;
        controller.dispose();
      }
    });
  });

  for (const trigger of ["Refresh", "view mode change"]) {
    for (const renderFails of [false, true]) {
      it(`serializes ${trigger} behind an active view renderer${renderFails ? " that fails" : ""}`, async () => {
        const vscode = createMockVscode();
        await withMockedVscode(vscode, async ({ TimelineController }) => {
          const { controller } = fileEnvironmentController(TimelineController, vscode);
          controller.refreshWebview = TimelineController.prototype.refreshWebview.bind(controller);
          controller.pythonEnvironment = resolvedPython(controller.projectUri);
          const irPath = join(controller.outputDir, "timeline_ir.json");
          const htmlPath = join(controller.outputDir, "timeline.html");
          const ir = (id) => ({ events: [{ id, kind: "wait", lane: "seq0", t0: 0, t1: 4 }], diagnostics: [] });
          writeFileSync(irPath, JSON.stringify(ir("before-edit")));
          const launches: Array<{ stage: string; mode: string }> = [];
          let releaseRender!: () => void;
          let renderStarted!: () => void;
          const rendering = new Promise<void>((resolve) => { renderStarted = resolve; });
          let firstRender = true;
          controller.execFile = async (_python, args, _timeout, _cwd, _env, stage) => {
            launches.push({ stage, mode: args[args.indexOf("--mode") + 1] });
            if (stage === "analyzer") {
              writeFileSync(irPath, JSON.stringify(ir("after-edit")));
            } else {
              const capturedIr = JSON.parse(readFileSync(irPath, "utf8"));
              if (firstRender) {
                firstRender = false;
                await new Promise<void>((resolve) => { releaseRender = resolve; renderStarted(); });
                if (renderFails) throw new Error("View renderer failed");
              }
              writeFileSync(htmlPath, '<html><head></head><body><section id="timeline-root"></section>' +
                '<script id="timeline-ir" type="application/json">' + JSON.stringify(capturedIr) + '</script></body></html>');
            }
            return { stdout: JSON.stringify({ schema_version: "0.2.0", status: "ok" }), stderr: "", exitCode: 0, elapsedMs: 1 };
          };
          const changing = controller.setViewMode("debug");
          try {
            await rendering;
            if (trigger === "Refresh") {
              controller.sourceRevision += 1;
              await controller.runAnalysis();
            } else {
              await controller.setViewMode("normal");
            }
            assert.deepEqual(launches.map((call) => call.stage), ["renderer"], "The queued request must wait for the view renderer to finish writing");
            releaseRender();
            await changing;
            while (controller.analysisInFlight) await new Promise((resolve) => setImmediate(resolve));

            assert.deepEqual(launches.map((call) => call.stage), ["renderer", "analyzer", "renderer"]);
            assert.equal(launches[2].mode, trigger === "Refresh" ? "debug" : "normal");
            const embeddedIr = JSON.parse(controller.panel.webview.html.match(/<script id="timeline-ir" type="application\/json">([\s\S]*?)<\/script>/)[1]);
            assert.equal(controller.timelineIr.events[0].id, "after-edit");
            assert.equal(embeddedIr.events[0].id, "after-edit");
            assert.equal(controller.analyzedSourceRevision, controller.sourceRevision);
            assert.equal(controller.analysisQueued, false);
            assert.equal(controller.analysisStatus.status, "idle");
          } finally {
            releaseRender?.();
            await changing;
            while (controller.analysisInFlight) await new Promise((resolve) => setImmediate(resolve));
            controller.dispose();
            require("node:fs").rmSync(controller.outputDir, { recursive: true, force: true });
          }
        });
      });
    }
  }

  it("checks relative manual launchers with the same working directory and environment as analysis and playback", async () => {
    const vscode = createMockVscode();
    const checks = [];
    const playbackStarts = [];
    vscode.resolveFileEnvironment = async (_api, resource, _kind, _output, options) => {
      checks.push(options);
      return resolvedPython(resource, "./.venv/bin/python");
    };
    vscode.PlaybackClient = class {
      constructor(_command, _args, options) { playbackStarts.push(options); }
      async request(message) {
        const playback = { status: "completed", can_resume: false, steps: 1 };
        return message.command === "snapshot" ? { ir: { events: [], playback } } : { playback };
      }
      dispose() {}
    };
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const { controller, calls } = fileEnvironmentController(TimelineController, vscode);
      const env = { PYTHONPATH: "project-modules" };
      controller.config = () => ({ get: (key, fallback) => key === "analyzer.env" ? env : fallback });
      const cwd = controller.projectRootCwd();
      await controller.runPlayback();
      assert.deepEqual(checks.map(({ cwd, env }) => ({ cwd, env })), [{ cwd, env }]);
      assert.ok(checks[0].signal instanceof AbortSignal);
      assert.equal(checks[0].signal.aborted, false);
      assert.equal(playbackStarts[0].cwd, cwd);
      assert.equal(playbackStarts[0].env.PYTHONPATH, env.PYTHONPATH);
      await controller.runAnalysis();
      assert.deepEqual(checks.map(({ cwd, env }) => ({ cwd, env })), [{ cwd, env }, { cwd, env }]);
      assert.ok(checks[1].signal instanceof AbortSignal);
      assert.equal(checks[1].signal.aborted, false);
      assert.ok(calls.every((call) => call.cwd === cwd && call.env.PYTHONPATH === env.PYTHONPATH));
      controller.dispose();
    });
  });

  it("drops a Python selection that completes after the analysis project has changed", async () => {
    const vscode = createMockVscode();
    let release;
    let started;
    const resolving = new Promise((resolve) => { started = resolve; });
    vscode.resolveFileEnvironment = (_api, resource) => new Promise((resolve) => {
      release = () => resolve(resolvedPython(resource, "old-project-python"));
      started();
    });
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const { controller, calls } = fileEnvironmentController(TimelineController, vscode);
      const analysis = controller.runAnalysis();
      await resolving;
      controller.setProjectUri(vscode.Uri.file(join(tmpdir(), "new-project", "q1timeline.yml")));
      release();
      await analysis;
      assert.deepEqual(calls, []);
      assert.equal(controller.pythonEnvironment, undefined);
      assert.equal(controller.analysisInFlight, false);
      controller.dispose();
    });
  });

  it("drops analyzer results when the project changes before its replacement analysis starts", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const { controller, calls } = fileEnvironmentController(TimelineController, vscode);
      let releaseAnalyzer!: () => void;
      let analyzerStarted!: () => void;
      const analyzing = new Promise<void>((resolve) => { analyzerStarted = resolve; });
      const execFile = controller.execFile;
      let first = true;
      controller.execFile = async (...args) => {
        if (first) {
          first = false;
          await new Promise<void>((resolve) => { releaseAnalyzer = resolve; analyzerStarted(); });
        }
        return execFile(...args);
      };
      const analysisA = controller.runAnalysis();
      await analyzing;
      const projectB = join(mkdtempSync(join(tmpdir(), "q1-switch-before-analysis-")), "q1timeline.yml");
      controller.setProjectUri(vscode.Uri.file(projectB));
      let finishProjectLoading!: () => void;
      const loading = new Promise<void>((resolve) => { finishProjectLoading = resolve; });
      controller.refreshProjectRelatedPaths = async () => loading;
      const openingB = controller.openPreview({ preserveProject: true });
      releaseAnalyzer();
      try {
        await analysisA;
        assert.equal(controller.lastAnalyzerTimelineIr, undefined, "Project A's result must not become B's comparison history");
        assert.deepEqual(controller.comparison, { mode: "none" });
        assert.equal(calls.filter((call) => call.stage === "renderer").length, 0, "Project A must not render after B was selected");
      } finally {
        finishProjectLoading();
        await openingB;
        controller.dispose();
      }
    });
  });

  it("does not launch analysis after disposal while Python setup is pending", async () => {
    const vscode = createMockVscode();
    let release!: () => void;
    let started!: () => void;
    const resolving = new Promise<void>((resolve) => { started = resolve; });
    vscode.resolveFileEnvironment = (_api, resource) => new Promise((resolve) => {
      release = () => resolve(resolvedPython(resource));
      started();
    });
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const { controller, calls } = fileEnvironmentController(TimelineController, vscode);
      const analysis = controller.runAnalysis();
      await resolving;
      controller.dispose();
      release();
      await analysis;
      assert.deepEqual(calls, [], "A disposed controller must not start analyzer or renderer processes");
      assert.equal(controller.pythonEnvironment, undefined);
    });
  });

  it("does not launch analysis after disposal during output preparation", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const { controller, calls } = fileEnvironmentController(TimelineController, vscode);
      let release!: () => void;
      let started!: () => void;
      const preparing = new Promise<void>((resolve) => { started = resolve; });
      controller.clearAnalyzerOutputFiles = async () => new Promise<void>((resolve) => {
        release = resolve;
        started();
      });
      const analysis = controller.runAnalysis();
      await preparing;
      controller.dispose();
      release();
      await analysis;
      assert.deepEqual(calls, [], "Disposal during awaited output cleanup must not start the analyzer");
    });
  });

  it("drops a queued refresh when its preview closes during analysis", async () => {
    const vscode = createMockVscode();
    const createPanel = vscode.window.createWebviewPanel;
    let closePanel!: () => void;
    vscode.window.createWebviewPanel = () => ({ ...createPanel(), onDidDispose: (listener) => {
      closePanel = listener;
      return { dispose() {} };
    } });
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const { controller, calls } = fileEnvironmentController(TimelineController, vscode);
      await controller.createPreviewPanel();
      controller.panel.webview.postMessage = () => {};
      let release!: () => void;
      let started!: () => void;
      const running = new Promise<void>((resolve) => { started = resolve; });
      const execFile = controller.execFile;
      controller.execFile = async (...args) => {
        if (!release) await new Promise<void>((resolve) => { release = resolve; started(); });
        return execFile(...args);
      };
      const analysis = controller.runAnalysis();
      await running;
      await controller.runAnalysis();
      closePanel();
      release();
      await analysis;
      while (controller.analysisInFlight) await new Promise((resolve) => setImmediate(resolve));
      assert.equal(controller.panel, undefined);
      assert.equal(controller.analysisQueued, false);
      assert.equal(calls.filter((call) => call.stage === "analyzer").length, 1, "Closing must discard queued subprocess work");
      controller.dispose();
    });
  });

  it("cancels an open when its preview closes during project loading and allows an explicit reopen", async () => {
    const vscode = createMockVscode();
    const createPanel = vscode.window.createWebviewPanel;
    let closePanel!: () => void;
    let panels = 0;
    vscode.window.createWebviewPanel = (...args) => {
      panels += 1;
      return { ...createPanel(...args), onDidDispose(listener) {
        closePanel = listener;
        return { dispose() {} };
      } };
    };
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const projectDir = mkdtempSync(join(tmpdir(), "q1-close-pending-open-"));
      const projectFile = join(projectDir, "q1timeline.yml");
      writeFileSync(projectFile, "sequencers: []\n");
      const controller = new TimelineController(createContext());
      let analyses = 0;
      controller.runAnalysis = async () => { analyses += 1; };
      await controller.openTarget({ projectFile });
      const fsPromises = require("node:fs").promises;
      const originalRead = fsPromises.readFile;
      let started!: () => void;
      let release!: () => void;
      const reading = new Promise<void>((resolve) => { started = resolve; });
      let first = true;
      fsPromises.readFile = async (...args) => {
        if (args[0] === projectFile && first) {
          first = false;
          await new Promise<void>((resolve) => { release = resolve; started(); });
        }
        return originalRead(...args);
      };
      try {
        const opening = controller.openPreview({ preserveProject: true });
        await reading;
        closePanel();
        release();
        await opening;
        assert.equal(controller.panel, undefined, "The pending open must not recreate the preview the user closed");
        assert.equal(panels, 1);
        assert.equal(analyses, 1);
        await controller.openPreview({ preserveProject: true });
        assert.ok(controller.panel, "A later explicit open must continue working");
        assert.equal(panels, 2);
        assert.equal(analyses, 2);
      } finally {
        fsPromises.readFile = originalRead;
        controller.dispose();
      }
    });
  });

  for (const nextMode of ["manual", "onSave"]) {
    it(`drops an on-type refresh when update mode changes to ${nextMode} before its debounce expires`, async () => {
      const vscode = createMockVscode();
      let changed: any;
      vscode.workspace.onDidChangeTextDocument = (handler) => { changed = handler; return { dispose() {} }; };
      await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
        const controller = new TimelineController(createContext());
        controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
        controller.outputDir = "C:\\repo\\.q1timeline";
        const document = { uri: vscode.Uri.file("C:\\repo\\seq0.q1asm"), isDirty: true };
        controller.projectRelatedPaths.add(document.uri.fsPath);
        let mode = "onType";
        controller.config = () => ({ get: (key, fallback) => key === "updateMode" ? mode : fallback });
        let analyses = 0;
        controller.runAnalysis = async () => { analyses += 1; };
        changed({ document });
        assert.notEqual(controller.changeTimer, undefined);
        mode = nextMode;
        clock.advance(1000);
        assert.equal(analyses, 0, "Turning off on-type updates must prevent the pending edit from triggering analysis");
        assert.equal(controller.changeTimer, undefined);
        mode = "onType";
        changed({ document });
        clock.advance(1000);
        assert.equal(analyses, 1, "Turning on-type updates back on must keep future edits working");
        controller.dispose();
      }));
    });
  }

  for (const scenario of [
    { mode: "manual", explicit: undefined, analyses: 0 },
    { mode: "onSave", explicit: undefined, analyses: 0 },
    { mode: "onType", explicit: undefined, analyses: 1 },
    { mode: "manual", explicit: "before", analyses: 1 },
    { mode: "manual", explicit: "after", analyses: 1 },
    { mode: "manual", explicit: "after", request: "view-mode change", analyses: 1 },
    { mode: "manual", explicit: "after", request: "parameter change", analyses: 1 },
  ]) {
    const name = scenario.explicit
      ? `preserves an explicit ${scenario.request || "Refresh"} queued ${scenario.explicit} an on-type refresh when updates become manual`
      : `rechecks a queued on-type refresh when the current update mode is ${scenario.mode}`;
    it(name, async () => {
      const vscode = createMockVscode();
      const notifications = sourceFileNotifications(vscode);
      let mode = "onType";
      let release!: () => void;
      let started!: () => void;
      let checks = 0;
      const waiting = new Promise<void>((resolve) => { started = resolve; });
      vscode.resolveFileEnvironment = async (_api, resource) => {
        if (++checks === 1) await new Promise<void>((resolve) => { release = resolve; started(); });
        return resolvedPython(resource);
      };
      await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
        const setup = sourceFileController(TimelineController, vscode);
        const { controller, calls, document } = setup;
        controller.config = () => ({ get: (key, fallback) => key === "updateMode" ? mode : fallback });
        let first: Promise<void> | undefined;
        try {
          first = controller.runAnalysis();
          await waiting;
          if (scenario.explicit === "before") await controller.refreshPreview();
          document.isDirty = true;
          document.text = "wait_sync 4\nwait 32\nstop\n";
          notifications.edit({ document, contentChanges: [{ text: document.text }] });
          clock.advance(1000);
          assert.equal(controller.analysisQueued, true);
          if (scenario.explicit === "after") {
            if (scenario.request === "view-mode change") await controller.setViewMode("debug");
            else if (scenario.request === "parameter change") {
              const inputs = ["DURATION", "64"];
              vscode.window.showInputBox = async () => inputs.shift();
              await controller.setAnalysisParameter();
            } else await controller.refreshPreview();
          }
          mode = scenario.mode;
          release();
          await first;
          await setup.settle();
          assert.equal(calls.filter((call) => call.stage === "analyzer").length, scenario.analyses);
          assert.equal(checks, scenario.analyses + 1, "A cancelled automatic refresh must not start another environment check");
          assert.equal(controller.analysisQueued, false);
          assert.equal(controller.analysisInFlight, false);
        } finally {
          release?.();
          await first;
          await setup.settle();
          controller.dispose();
        }
      }));
    });
  }

  for (const scenario of [
    { trigger: "save", mode: "manual", analyses: 0 },
    { trigger: "watcher", mode: "manual", analyses: 0 },
    { trigger: "save", mode: "onSave", analyses: 1 },
    { trigger: "watcher", mode: "onSave", analyses: 1 },
    { trigger: "save", mode: "onType", analyses: 1 },
    { trigger: "watcher", mode: "onType", analyses: 1 },
    { trigger: "save", mode: "manual", explicit: "before", analyses: 1 },
    { trigger: "save", mode: "manual", explicit: "after", analyses: 1 },
  ]) {
    const name = scenario.explicit
      ? `preserves an explicit Refresh queued ${scenario.explicit} a queued on-save refresh when updates become manual`
      : `rechecks a queued on-save ${scenario.trigger} refresh when the current update mode is ${scenario.mode}`;
    it(name, async () => {
      const vscode = createMockVscode();
      const notifications = sourceFileNotifications(vscode);
      let mode = "onSave";
      let release!: () => void;
      let started!: () => void;
      let checks = 0;
      const waiting = new Promise<void>((resolve) => { started = resolve; });
      vscode.resolveFileEnvironment = async (_api, resource) => {
        if (++checks === 1) await new Promise<void>((resolve) => { release = resolve; started(); });
        return resolvedPython(resource);
      };
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const setup = sourceFileController(TimelineController, vscode);
        const { controller, calls, document, sourcePath } = setup;
        controller.config = () => ({ get: (key, fallback) => key === "updateMode" ? mode : fallback });
        let first: Promise<void> | undefined;
        try {
          first = controller.runAnalysis();
          await waiting;
          if (scenario.explicit === "before") await controller.refreshPreview();
          document.text = "wait_sync 4\nwait 32\nstop\n";
          writeFileSync(sourcePath, document.text);
          if (scenario.trigger === "save") await notifications.save(document);
          else await notifications.change(document.uri);
          assert.equal(controller.analysisQueued, true);
          if (scenario.explicit === "after") await controller.refreshPreview();
          mode = scenario.mode;
          release();
          await first;
          await setup.settle();
          assert.equal(calls.filter((call) => call.stage === "analyzer").length, scenario.analyses);
          assert.equal(checks, scenario.analyses + 1);
          assert.equal(controller.analysisQueued, false);
          assert.equal(controller.analysisInFlight, false);
        } finally {
          release?.();
          await first;
          await setup.settle();
          controller.dispose();
        }
      });
    });
  }

  for (const scenario of [
    { mode: "onSave" },
    { mode: "onSave", edit: "before" },
    { mode: "onSave", edit: "after" },
    { mode: "onSave", edit: "queued before" },
    { mode: "onSave", edit: "queued after" },
    { mode: "onSave", switchBeforeTimer: true },
    { mode: "manual" },
    { mode: "manual", explicit: "before" },
    { mode: "manual", explicit: "after" },
  ]) {
    const detail = scenario.edit ? ` with an edit ${scenario.edit} the saved change`
      : scenario.explicit ? ` with an explicit Refresh ${scenario.explicit} the saved change`
      : scenario.switchBeforeTimer ? " before its debounce fires" : " after its debounce fires";
    it(`preserves a debounced watcher's saved-source policy when updates become ${scenario.mode}${detail}`, async () => {
      const vscode = createMockVscode();
      const notifications = sourceFileNotifications(vscode);
      let mode = "onType";
      let release!: () => void;
      let started!: () => void;
      let checks = 0;
      const waiting = new Promise<void>((resolve) => { started = resolve; });
      vscode.resolveFileEnvironment = async (_api, resource) => {
        if (++checks === 1) await new Promise<void>((resolve) => { release = resolve; started(); });
        return resolvedPython(resource);
      };
      await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
        const setup = sourceFileController(TimelineController, vscode);
        const { controller, calls, document, sourcePath } = setup;
        controller.config = () => ({ get: (key, fallback) => key === "updateMode" ? mode : fallback });
        const edit = () => {
          document.isDirty = true;
          document.text = "wait_sync 4\nwait 64\nstop\n";
          notifications.edit({ document, contentChanges: [{ text: document.text }] });
        };
        let first: Promise<void> | undefined;
        try {
          first = controller.runAnalysis();
          await waiting;
          if (scenario.explicit === "before") await controller.refreshPreview();
          if (scenario.edit === "before" || scenario.edit === "queued before") edit();
          if (scenario.edit === "queued before") clock.advance(1000);
          writeFileSync(sourcePath, "wait_sync 4\nwait 32\nstop\n");
          await notifications.change(document.uri);
          assert.notEqual(controller.changeTimer, undefined);
          if (scenario.edit === "after") edit();
          if (scenario.switchBeforeTimer) mode = scenario.mode;
          clock.advance(1000);
          assert.equal(controller.analysisQueued, true);
          if (scenario.edit === "queued after") {
            edit();
            clock.advance(1000);
          }
          if (scenario.explicit === "after") await controller.refreshPreview();
          mode = scenario.mode;
          release();
          await first;
          await setup.settle();
          const analyses = scenario.mode === "manual" && !scenario.explicit ? 0 : 1;
          assert.equal(calls.filter((call) => call.stage === "analyzer").length, analyses);
          assert.equal(checks, analyses + 1);
          assert.equal(controller.analysisQueued, false);
          assert.equal(controller.analysisInFlight, false);
        } finally {
          release?.();
          await first;
          await setup.settle();
          controller.dispose();
        }
      }));
    });
  }

  it("runs a saved-source watcher debounce after changing from on-type to on-save with no active analysis", async () => {
    const vscode = createMockVscode();
    const notifications = sourceFileNotifications(vscode);
    await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = sourceFileController(TimelineController, vscode);
      const { controller, calls, document, sourcePath } = setup;
      let mode = "onType";
      controller.config = () => ({ get: (key, fallback) => key === "updateMode" ? mode : fallback });
      try {
        writeFileSync(sourcePath, "wait_sync 4\nwait 32\nstop\n");
        await notifications.change(document.uri);
        assert.notEqual(controller.changeTimer, undefined);
        mode = "onSave";
        clock.advance(1000);
        await setup.settle();
        assert.equal(calls.filter((call) => call.stage === "analyzer").length, 1);
        assert.equal(controller.analysisInFlight, false);
      } finally {
        await setup.settle();
        controller.dispose();
      }
    }));
  });

  it("preserves a queued saved-source refresh after an on-type refresh when updates become on-save", async () => {
    const vscode = createMockVscode();
    const notifications = sourceFileNotifications(vscode);
    let mode = "onType";
    let release!: () => void;
    let started!: () => void;
    let checks = 0;
    const waiting = new Promise<void>((resolve) => { started = resolve; });
    vscode.resolveFileEnvironment = async (_api, resource) => {
      if (++checks === 1) await new Promise<void>((resolve) => { release = resolve; started(); });
      return resolvedPython(resource);
    };
    await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = sourceFileController(TimelineController, vscode);
      const { controller, calls, document, sourcePath } = setup;
      controller.config = () => ({ get: (key, fallback) => key === "updateMode" ? mode : fallback });
      let first: Promise<void> | undefined;
      try {
        first = controller.runAnalysis();
        await waiting;
        document.isDirty = true;
        document.text = "wait_sync 4\nwait 32\nstop\n";
        notifications.edit({ document, contentChanges: [{ text: document.text }] });
        clock.advance(1000);
        assert.equal(controller.analysisQueued, true);
        document.isDirty = false;
        writeFileSync(sourcePath, document.text);
        await notifications.save(document);
        mode = "onSave";
        release();
        await first;
        await setup.settle();
        assert.equal(calls.filter((call) => call.stage === "analyzer").length, 1);
        assert.equal(checks, 2);
        assert.equal(controller.analysisQueued, false);
        assert.equal(controller.analysisInFlight, false);
      } finally {
        release?.();
        await first;
        await setup.settle();
        controller.dispose();
      }
    }));
  });

  it("rolls back an inline parameter edit when its queued on-type analysis is cancelled by manual mode", async () => {
    const vscode = createMockVscode();
    const notifications = sourceFileNotifications(vscode);
    let mode = "onType";
    let release!: () => void;
    let started!: () => void;
    let checks = 0;
    const waiting = new Promise<void>((resolve) => { started = resolve; });
    vscode.resolveFileEnvironment = async (_api, resource) => {
      if (++checks === 1) await new Promise<void>((resolve) => { release = resolve; started(); });
      return resolvedPython(resource);
    };
    await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = sourceFileController(TimelineController, vscode);
      const { controller, calls, document } = setup;
      controller.config = () => ({ get: (key, fallback) => key === "updateMode" ? mode : fallback });
      const previousIr = { analysis: { parameters: [{ name: "DURATION" }] }, events: [{ id: "previous" }] };
      controller.timelineIr = controller.lastAnalyzerTimelineIr = previousIr;
      controller.parameterOverrides.set("DURATION", "128");
      controller.panel.webview.html = "previous editable preview";
      const messages: any[] = [];
      controller.panel.webview.postMessage = (message) => messages.push(message);
      let editing: Promise<void> | undefined;
      try {
        editing = controller.handleWebviewMessage({ type: "setAnalysisParameter", name: "DURATION", valueJson: "256", projectFile: controller.projectUri.fsPath });
        await waiting;
        document.isDirty = true;
        notifications.edit({ document, contentChanges: [{ text: "wait 32" }] });
        clock.advance(1000);
        assert.equal(controller.analysisQueued, true);
        mode = "manual";
        release();
        await editing;
        await setup.settle();
        assert.equal(calls.filter((call) => call.stage === "analyzer").length, 0);
        assert.equal(controller.parameterOverrides.get("DURATION"), "128");
        assert.equal(controller.timelineIr, previousIr);
        assert.equal(controller.lastAnalyzerTimelineIr, previousIr);
        assert.equal(controller.panel.webview.html, "previous editable preview");
        const errors = messages.filter((message) => message.type === "analysisParameterError");
        assert.equal(errors.length, 1, "The retained parameter editor must receive a failure reply to leave its busy state");
        assert.match(errors[0].message, /automatic refresh.*cancelled/i);
      } finally {
        release?.();
        await editing;
        await setup.settle();
        controller.dispose();
      }
    }));
  });

  for (const trigger of ["save", "Refresh"]) {
    it(`consumes an on-type debounce when ${trigger} already starts the same analysis`, async () => {
      const vscode = createMockVscode();
      let changed: any;
      let saved: any;
      vscode.workspace.onDidChangeTextDocument = (handler) => { changed = handler; return { dispose() {} }; };
      vscode.workspace.onDidSaveTextDocument = (handler) => { saved = handler; return { dispose() {} }; };
      await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
        const { controller, calls } = fileEnvironmentController(TimelineController, vscode);
        const document = { uri: vscode.Uri.file(join(controller.outputDir, "seq0.q1asm")), isDirty: true };
        controller.projectRelatedPaths.add(document.uri.fsPath);
        controller.config = () => ({ get: (key, fallback) => key === "updateMode" ? "onType" : fallback });
        const analyses: Promise<void>[] = [];
        const runAnalysis = controller.runAnalysis.bind(controller);
        controller.runAnalysis = (...args) => {
          const analysis = runAnalysis(...args);
          analyses.push(analysis);
          return analysis;
        };
        changed({ document });
        assert.notEqual(controller.changeTimer, undefined);
        document.isDirty = false;
        if (trigger === "save") await saved(document);
        else await controller.refreshPreview();
        await analyses[0];
        clock.advance(1000);
        await Promise.all(analyses);
        assert.equal(calls.filter((call) => call.stage === "analyzer").length, 1, "The old edit timer must not analyze the same saved program again");
        assert.equal(calls.filter((call) => call.stage === "renderer").length, 1);
        assert.equal(controller.changeTimer, undefined);
        controller.dispose();
      }));
    });
  }

  function sourceFileNotifications(vscode) {
    const handlers = {};
    vscode.workspace.onDidSaveTextDocument = (handler) => { handlers.save = handler; return { dispose() {} }; };
    vscode.workspace.onDidChangeTextDocument = (handler) => { handlers.edit = handler; return { dispose() {} }; };
    vscode.workspace.createFileSystemWatcher = (pattern) => ({
      onDidChange(handler) { if (typeof pattern === "string") handlers.change = handler; return { dispose() {} }; },
      onDidCreate(handler) { if (typeof pattern === "string") handlers.create = handler; return { dispose() {} }; },
      onDidDelete(handler) { if (typeof pattern === "string") handlers.delete = handler; return { dispose() {} }; },
      dispose() {},
    });
    return handlers;
  }

  function closedDependencyProject(TimelineController, vscode, coverage, mode = "onSave") {
    const root = mkdtempSync(join(tmpdir(), "q1-closed-dependency-"));
    const workspace = join(root, "workspace"), external = join(root, "external");
    mkdirSync(workspace); mkdirSync(external);
    vscode.workspace.workspaceFolders = [{ uri: vscode.Uri.file(workspace) }];
    const subscriptions = fileWatchSubscriptions(vscode);
    const custom = coverage === "workspace custom extensions";
    const projectDir = coverage === "external project" ? external : workspace;
    const dependencyDir = join(custom ? workspace : external, "sources");
    mkdirSync(dependencyDir);
    const files = {
      project: join(projectDir, custom ? "timeline.project" : "q1timeline.yml"),
      file: join(dependencyDir, custom ? "seq.program" : "seq.q1asm"),
      sequence_json: join(dependencyDir, custom ? "sequence.data" : "sequence.json"),
      params: join(dependencyDir, custom ? "params.values" : "params.json"),
      display: join(dependencyDir, custom ? "display.theme" : "display.yml"),
    };
    const contents = { file: "wait_sync 4\nwait {WAIT_NS}\nstop\n", sequence_json: "{\"waveforms\": {}}\n",
      params: "{\"WAIT_NS\": 4}\n", display: "title: initial\n" };
    for (const [role, text] of Object.entries(contents)) writeFileSync(files[role], text);
    const writeProject = (paramsFile = files.params) => {
      const relative = (file) => JSON.stringify(require("node:path").relative(projectDir, file).replace(/\\/g, "/"));
      writeFileSync(files.project, `sequencers:\n  - id: seq\n    file: ${relative(files.file)}\n    sequence_json: ${relative(files.sequence_json)}\nparams:\n  file: ${relative(paramsFile)}\ndisplay:\n  file: ${relative(files.display)}\n`);
    };
    writeProject();
    const controller = new TimelineController(createContext());
    controller.config = () => ({ get: (key, fallback) => key === "updateMode" ? mode : fallback });
    const analyzed: string[] = [];
    controller.runAnalysis = async () => { analyzed.push(controller.projectUri.fsPath); };
    return { controller, root, workspace, external, dependencyDir, files, contents, analyzed, subscriptions, writeProject,
      async open() { await controller.openTarget({ projectFile: files.project }); analyzed.length = 0; } };
  }

  for (const coverage of ["external project", "external dependencies", "workspace custom extensions"]) {
    for (const mode of ["manual", "onSave", "onType"]) {
      it(`observes closed ${coverage} changes under ${mode} updates`, async () => {
        await withFakeTimers(async (clock) => {
          const vscode = createMockVscode();
          await withMockedVscode(vscode, async ({ TimelineController }) => {
            const setup = closedDependencyProject(TimelineController, vscode, coverage, mode);
            const { controller, files, subscriptions, analyzed } = setup;
            try {
              await setup.open();
              assert.deepEqual(vscode.workspace.textDocuments, []);
              for (const role of ["file", "sequence_json", "params", "display", "project"]) {
                assert.equal(controller.isProjectRelated(vscode.Uri.file(files[role])), true);
                const revision = controller.sourceRevision, analyses = analyzed.length;
                const text = readFileSync(files[role], "utf8");
                writeFileSync(files[role], role === "params" ? "{\"WAIT_NS\": 8}\n" : `${text}\n`);
                await subscriptions.dispatch(files[role]);
                clock.advance(1000);
                assert.equal(controller.sourceRevision, revision + 1, `The closed ${role} dependency must invalidate the source exactly once`);
                assert.equal(analyzed.length, analyses + (mode === "manual" ? 0 : 1));
              }
            } finally { controller.dispose(); }
          });
        });
      });
    }
  }

  for (const mode of ["manual", "onSave", "onType"]) {
    it(`observes a missing external dependency's creation, deletion and recreation under ${mode}`, async () => {
      await withFakeTimers(async (clock) => {
        const vscode = createMockVscode();
        await withMockedVscode(vscode, async ({ TimelineController }) => {
          const setup = closedDependencyProject(TimelineController, vscode, "external dependencies", mode);
          const { controller, files, contents, subscriptions, analyzed } = setup;
          require("node:fs").unlinkSync(files.params);
          try {
            await setup.open();
            for (const change of ["create", "delete", "create"]) {
              const revision = controller.sourceRevision, analyses = analyzed.length;
              if (change === "delete") require("node:fs").unlinkSync(files.params);
              else writeFileSync(files.params, contents.params);
              await subscriptions.dispatch(files.params, change);
              clock.advance(1000);
              assert.equal(controller.sourceRevision, revision + 1);
              assert.equal(analyzed.length, analyses + (mode === "manual" ? 0 : 1));
            }
          } finally { controller.dispose(); }
        });
      });
    });
  }

  for (const role of ["params", "display"]) {
    for (const mode of ["manual", "onSave", "onType"]) {
      it(`observes a new external ${role} file when its parent folder did not exist under ${mode}`, async () => {
        await withFakeTimers(async (clock) => {
          const vscode = createMockVscode();
          await withMockedVscode(vscode, async ({ TimelineController }) => {
            const setup = closedDependencyProject(TimelineController, vscode, "external dependencies", mode);
            const { controller, subscriptions, files } = setup;
            const parent = role === "params" ? join(setup.external, "later", "nested") : join(setup.external, "later");
            const file = join(parent, `${role}[new].data`);
            files[role] = file;
            writeFileSync(files.file, "move 17,R0\nstop\n");
            setup.writeProject();
            const repoRoot = require("node:path").resolve(__dirname, "../../../..");
            const analyses: any[] = [];
            controller.runAnalysis = async () => {
              const out = join(setup.root, `optional-${analyses.length}.json`);
              require("node:child_process").execFileSync("python", ["-m", "q1timeline", "analyze", "--project", files.project,
                "--out", out, "--format", "vscode-json"],
              { cwd: repoRoot, env: { ...process.env, PYTHONPATH: join(repoRoot, "src") }, encoding: "utf8", windowsHide: true });
              analyses.push(JSON.parse(readFileSync(out, "utf8")));
            };
            try {
              assert.equal(existsSync(parent), false);
              await setup.open();
              assert.ok(analyses[0].diagnostics.some((item) => item.category === "missing_optional_file" && item.details.kind === role));
              const revision = controller.sourceRevision;
              mkdirSync(parent, { recursive: true });
              writeFileSync(file, role === "params" ? "{\"NEW_VALUE\": 23}\n" : "title: newly configured\n");
              await subscriptions.dispatch(file, "create");
              clock.advance(1000);
              assert.equal(controller.sourceRevision, revision + 1, "Creating the previously missing parent and target file must invalidate the current source");
              assert.equal(analyses.length, mode === "manual" ? 1 : 2);
              if (mode === "manual") await controller.refreshPreview();
              assert.ok(!analyses.at(-1).diagnostics.some((item) => item.category === "missing_optional_file" && item.details.kind === role));
              if (role === "params") assert.equal(analyses.at(-1).analysis.parameters.find((item) => item.name === "NEW_VALUE").value_json, "23");
              else assert.equal(analyses.at(-1).project.display.title, "newly configured");
              assert.ok(subscriptions.watchers.filter((watcher) => !watcher.disposed && typeof watcher.pattern !== "string")
                .every((watcher) => watcher.pattern.pattern === "*"), "Once the parent exists, supplemental watching must return to direct non-recursive patterns");
            } finally { controller.dispose(); }
          });
        });
      });
    }
  }

  for (const role of ["params", "display"]) {
    for (const { mode, notification } of ["manual", "onSave", "onType"].flatMap((mode) => ["parent", "file"].map((notification) => ({ mode, notification })))) {
      it(`invalidates a deleted external ${role} parent and observes recreation under ${mode} with a ${notification} notification`, async () => {
        await withFakeTimers(async (clock) => {
          const vscode = createMockVscode();
          await withMockedVscode(vscode, async ({ TimelineController }) => {
            const setup = closedDependencyProject(TimelineController, vscode, "external dependencies", mode);
            const { controller, subscriptions, files } = setup;
            const parent = join(setup.external, "parent-only"), file = join(parent, `${role}.values`);
            mkdirSync(parent);
            files[role] = file;
            writeFileSync(file, role === "params" ? "{\"VALUE\":17}\n" : "title: initial\n");
            writeFileSync(files.file, "move 17,R0\nstop\n");
            setup.writeProject();
            const repoRoot = require("node:path").resolve(__dirname, "../../../..");
            const analyses: any[] = [];
            controller.runAnalysis = async () => {
              const out = join(setup.root, `lifecycle-${analyses.length}.json`);
              require("node:child_process").execFileSync("python", ["-m", "q1timeline", "analyze", "--project", files.project,
                "--out", out, "--format", "vscode-json"],
              { cwd: repoRoot, env: { ...process.env, PYTHONPATH: join(repoRoot, "src") }, encoding: "utf8", windowsHide: true });
              analyses.push(JSON.parse(readFileSync(out, "utf8")));
            };
            try {
              await setup.open();
              const initialWatcher = subscriptions.watchers.find((watcher) => typeof watcher.pattern !== "string" && watcher.pattern.baseUri.fsPath === parent);
              assert.ok(initialWatcher);
              const revision = controller.sourceRevision;
              require("node:fs").unlinkSync(file);
              require("node:fs").rmdirSync(parent);
              // Stable emits only the parent URI; minimum can emit only the target file's deletion.
              await subscriptions.dispatch(notification === "parent" ? parent : file, "delete");
              clock.advance(1000);
              assert.equal(controller.sourceRevision, revision + 1, "A directory-only deletion must invalidate its declared dependency");
              assert.equal(analyses.length, mode === "manual" ? 1 : 2);
              if (mode === "manual") await controller.refreshPreview();
              assert.ok(analyses.at(-1).diagnostics.some((item) => item.category === "missing_optional_file" && item.details.kind === role));
              const afterDelete = controller.sourceRevision;
              const sibling = `${parent}-sibling`;
              mkdirSync(sibling); require("node:fs").rmdirSync(sibling);
              await subscriptions.dispatch(sibling, "delete");
              assert.equal(controller.sourceRevision, afterDelete, "A sibling with the same filename prefix is not an ancestor");
              mkdirSync(parent);
              writeFileSync(file, role === "params" ? "{\"VALUE\":23}\n" : "title: restored\n");
              await subscriptions.dispatch(file, "create");
              clock.advance(1000);
              assert.equal(controller.sourceRevision, afterDelete + 1);
              assert.equal(analyses.length, mode === "manual" ? 2 : 3);
              if (mode === "manual") await controller.refreshPreview();
              assert.ok(!analyses.at(-1).diagnostics.some((item) => item.category === "missing_optional_file" && item.details.kind === role));
              if (role === "params") assert.equal(analyses.at(-1).analysis.parameters.find((item) => item.name === "VALUE").value_json, "23");
              else assert.equal(analyses.at(-1).project.display.title, "restored");
              const restoredRevision = controller.sourceRevision, restoredAnalyses = analyses.length;
              await initialWatcher.handlers.delete(vscode.Uri.file(parent));
              assert.equal(controller.sourceRevision, restoredRevision, "A retained callback from the disposed binding must not invalidate the recreated target");
              assert.equal(analyses.length, restoredAnalyses);
            } finally { controller.dispose(); }
          });
        });
      });
    }
  }

  for (const notification of ["parent", "file", "file across branches"]) {
    for (const pendingRead of [false, true]) {
      const separateBranches = notification === "file across branches";
      if (separateBranches && pendingRead) continue;
      it(`refreshes a same-byte restored dependency after shared-parent deletion with a ${notification === "parent" ? "parent" : "file"} notification${pendingRead ? " and a pending read" : ""}${separateBranches ? " across separate branches" : ""}`, async () => {
        const vscode = createMockVscode();
        await withMockedVscode(vscode, async ({ TimelineController }) => {
          const setup = closedDependencyProject(TimelineController, vscode, "external dependencies");
          const { controller, subscriptions, files } = setup;
          const parent = join(setup.external, "shared-parent");
          const directories = separateBranches ? [join(parent, "params"), join(parent, "display")] : [parent, parent];
          for (const directory of new Set(directories)) mkdirSync(directory, { recursive: true });
          files.params = join(directories[0], "params.values");
          files.display = join(directories[1], "display.values");
          writeFileSync(files.params, "{\"VALUE\":17}\n");
          writeFileSync(files.display, "title: original\n");
          writeFileSync(files.file, "move 17,R0\nstop\n");
          setup.writeProject();
          const repoRoot = require("node:path").resolve(__dirname, "../../../..");
          const analyses: any[] = [];
          controller.runAnalysis = async () => {
            const out = join(setup.root, `shared-${analyses.length}.json`);
            require("node:child_process").execFileSync("python", ["-m", "q1timeline", "analyze", "--project", files.project,
              "--out", out, "--format", "vscode-json"],
            { cwd: repoRoot, env: { ...process.env, PYTHONPATH: join(repoRoot, "src") }, encoding: "utf8", windowsHide: true });
            analyses.push(JSON.parse(readFileSync(out, "utf8")));
          };
          const fs = require("node:fs"), readFile = fs.promises.readFile;
          let release!: () => void, readStarted!: () => void, reading: Promise<void> | undefined;
          const waiting = new Promise<void>((resolve) => { readStarted = resolve; });
          const restoredDisplay = "title: cached\n";
          try {
            await setup.open();
            writeFileSync(files.params, "{\"VALUE\":19}\n");
            await subscriptions.dispatch(files.params);
            writeFileSync(files.display, restoredDisplay);
            await subscriptions.dispatch(files.display);
            assert.equal(analyses.at(-1).project.display.title, "cached");
            if (pendingRead) {
              fs.promises.readFile = async (...args) => {
                const bytes = await readFile(...args);
                if (args[0] === files.display) await new Promise<void>((resolve) => { release = resolve; readStarted(); });
                return bytes;
              };
              writeFileSync(files.display, restoredDisplay);
              reading = subscriptions.dispatch(files.display);
              await waiting;
            }
            fs.unlinkSync(files.params); fs.unlinkSync(files.display);
            if (separateBranches) for (const directory of directories) fs.rmdirSync(directory);
            fs.rmdirSync(parent);
            await subscriptions.dispatch(notification === "parent" ? parent : files.params, "delete");
            assert.ok(analyses.at(-1).diagnostics.some((item) => item.category === "missing_optional_file" && item.details.kind === "display"));
            if (pendingRead) { release(); await reading; fs.promises.readFile = readFile; }
            mkdirSync(require("node:path").dirname(files.display), { recursive: true });
            writeFileSync(files.display, restoredDisplay);
            const beforeRestore = analyses.length;
            await subscriptions.dispatch(files.display, "create");
            assert.equal(analyses.length, beforeRestore + 1, "Restoring a different declared file with its old bytes must still refresh the missing-file result");
            assert.ok(!analyses.at(-1).diagnostics.some((item) => item.category === "missing_optional_file" && item.details.kind === "display"));
            assert.ok(analyses.at(-1).diagnostics.some((item) => item.category === "missing_optional_file" && item.details.kind === "params"));
            assert.equal(analyses.at(-1).project.display.title, "cached");
          } finally { release?.(); await reading?.catch(() => {}); fs.promises.readFile = readFile; controller.dispose(); }
        });
      });
    }
  }

  it("rebinds closed dependency subscriptions after project edits and releases them on switching and disposal", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = closedDependencyProject(TimelineController, vscode, "external dependencies");
      const { controller, files, subscriptions, analyzed } = setup;
      const otherFolder = join(setup.external, "other");
      mkdirSync(otherFolder);
      const otherParams = join(otherFolder, "new.values");
      writeFileSync(otherParams, "{\"WAIT_NS\": 12}\n");
      try {
        await setup.open();
        setup.writeProject(otherParams);
        await subscriptions.dispatch(files.project);
        analyzed.length = 0;
        writeFileSync(files.params, "{\"WAIT_NS\": 8}\n");
        await subscriptions.dispatch(files.params);
        assert.deepEqual(analyzed, [], "A removed dependency must no longer refresh the preview");
        writeFileSync(otherParams, "{\"WAIT_NS\": 16}\n");
        await subscriptions.dispatch(otherParams);
        assert.deepEqual(analyzed, [files.project]);
        const nextProject = join(setup.workspace, "next.yml");
        writeFileSync(nextProject, "sequencers: []\n");
        await controller.openTarget({ projectFile: nextProject });
        analyzed.length = 0;
        assert.ok(subscriptions.watchers.filter((watcher) => typeof watcher.pattern !== "string").every((watcher) => watcher.disposed));
        writeFileSync(otherParams, "{\"WAIT_NS\": 20}\n");
        await subscriptions.dispatch(otherParams);
        assert.deepEqual(analyzed, []);
        controller.dispose();
        assert.ok(subscriptions.watchers.every((watcher) => watcher.disposed));
      } finally { controller.dispose(); }
    });
  });

  it("keeps workspace-covered changes singular and ignores nearby unrelated and generated files", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = closedDependencyProject(TimelineController, vscode, "workspace custom extensions");
      const { controller, subscriptions, analyzed } = setup;
      const covered = join(setup.workspace, "covered.q1asm");
      writeFileSync(covered, "stop\n");
      writeFileSync(setup.files.project, `sequencers: [{id: seq, file: covered.q1asm}]\nparams: {file: sources/params.values}\n`);
      try {
        await setup.open();
        const relatedCalls: string[] = [];
        const onWatchedFile = controller.onWatchedFile.bind(controller);
        controller.onWatchedFile = (uri, change) => { relatedCalls.push(uri.fsPath); return onWatchedFile(uri, change); };
        writeFileSync(covered, "wait 4\nstop\n");
        await subscriptions.dispatch(covered);
        assert.deepEqual(relatedCalls, [covered], "A file covered by the workspace watcher must not get a second subscription callback");
        assert.deepEqual(analyzed, [setup.files.project]);
        analyzed.length = 0;
        for (const file of [join(setup.dependencyDir, "unrelated.json"), join(controller.outputDir, "timeline.json")]) {
          mkdirSync(require("node:path").dirname(file), { recursive: true });
          writeFileSync(file, "{}\n");
          await subscriptions.dispatch(file, "create");
        }
        assert.deepEqual(analyzed, []);
      } finally { controller.dispose(); }
    });
  });

  it("accepts declared project and dependency paths with custom extensions in the actual Python analyzer", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = closedDependencyProject(TimelineController, vscode, "workspace custom extensions");
      const repoRoot = require("node:path").resolve(__dirname, "../../../..");
      try {
        require("node:child_process").execFileSync("python", ["-m", "q1timeline", "analyze", "--project", setup.files.project,
          "--out", join(setup.root, "custom-ir.json"), "--format", "vscode-json"],
        { cwd: repoRoot, env: { ...process.env, PYTHONPATH: join(repoRoot, "src") }, encoding: "utf8", windowsHide: true });
        const ir = JSON.parse(readFileSync(join(setup.root, "custom-ir.json"), "utf8"));
        assert.ok(ir.events.length > 0);
        assert.ok(ir.events.some((event) => event.source?.file &&
          realpathSync.native(event.source.file) === realpathSync.native(setup.files.file)));
      } finally { setup.controller.dispose(); }
    });
  });

  function sourceFileController(TimelineController, vscode) {
    const setup = fileEnvironmentController(TimelineController, vscode);
    const { controller } = setup;
    const sourcePath = join(controller.outputDir, "seq0.q1asm");
    const document = {
      uri: vscode.Uri.file(sourcePath), isDirty: false, version: 1, text: "wait_sync 4\nwait 16\nstop\n",
      getText() { return this.text; },
    };
    writeFileSync(sourcePath, document.text);
    vscode.workspace.textDocuments = [document];
    controller.projectRelatedPaths.add(sourcePath);
    controller.startWatchers();
    const analyses = [];
    const runAnalysis = controller.runAnalysis.bind(controller);
    controller.runAnalysis = (...args) => {
      const analysis = runAnalysis(...args);
      analyses.push(analysis);
      return analysis;
    };
    return { ...setup, sourcePath, document, analyses, async settle() {
      for (let index = 0; index < analyses.length; index += 1) await analyses[index];
    } };
  }

  for (const scenario of [
    { trigger: "edit" },
    { trigger: "save" },
    { trigger: "watcher" },
    { trigger: "edit", explicitRefresh: true },
    { trigger: "edit", finishOpen: true },
    { trigger: "edit", sameProject: true },
  ]) {
    const name = scenario.explicitRefresh ? "preserves a new project's explicit Refresh queued behind the previous project's analysis"
      : scenario.finishOpen ? "preserves a new project's completed open request queued behind the previous project's analysis"
      : scenario.sameProject ? "retains an already queued automatic refresh when reopening the same project"
      : `drops an old project's already queued ${scenario.trigger} refresh while opening a different project`;
    it(name, async () => {
      const vscode = createMockVscode();
      const notifications = sourceFileNotifications(vscode);
      let releaseEnvironment!: () => void;
      let environmentStarted!: () => void;
      const environmentWaiting = new Promise<void>((resolve) => { environmentStarted = resolve; });
      let checks = 0;
      vscode.resolveFileEnvironment = async (_api, resource) => {
        if (++checks === 1) await new Promise<void>((resolve) => { releaseEnvironment = resolve; environmentStarted(); });
        return resolvedPython(resource);
      };
      await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
        const setup = sourceFileController(TimelineController, vscode);
        const { controller, calls, document, sourcePath } = setup;
        controller.config = () => ({ get: (key, fallback) => key === "updateMode"
          ? scenario.trigger === "edit" ? "onType" : "onSave" : fallback });
        const root = mkdtempSync(join(tmpdir(), "q1-project-analysis-queue-"));
        const projectFile = scenario.sameProject ? controller.projectUri.fsPath : join(root, "q1timeline.yml");
        writeFileSync(projectFile, "sequencers:\n  - id: seq0\n    file: seq0.q1asm\n");
        writeFileSync(join(root, "seq0.q1asm"), "stop\n");
        const fsPromises = require("node:fs").promises;
        const originalReadFile = fsPromises.readFile;
        let releaseRead!: () => void;
        let readStarted!: () => void;
        const readWaiting = new Promise<void>((resolve) => { readStarted = resolve; });
        fsPromises.readFile = async (...args) => {
          if (args[0] === projectFile) await new Promise<void>((resolve) => { releaseRead = resolve; readStarted(); });
          return originalReadFile(...args);
        };
        let first: Promise<void> | undefined;
        let opening: Promise<void> | undefined;
        try {
          first = controller.runAnalysis();
          await environmentWaiting;
          document.text = "wait_sync 4\nwait 32\nstop\n";
          if (scenario.trigger === "edit") {
            document.isDirty = true;
            notifications.edit({ document, contentChanges: [{ text: document.text }] });
            clock.advance(1000);
          } else {
            writeFileSync(sourcePath, document.text);
            if (scenario.trigger === "save") await notifications.save(document);
            else await notifications.change(document.uri);
          }
          assert.equal(controller.analysisQueued, true);
          opening = controller.openTarget({ projectFile });
          await readWaiting;
          assert.equal(controller.projectUri.fsPath, projectFile);
          if (scenario.explicitRefresh) await controller.refreshPreview();
          if (scenario.finishOpen) {
            releaseRead();
            await opening;
            assert.equal(controller.analysisQueued, true);
          }
          releaseEnvironment();
          await first;
          await setup.settle();
          const expectedEarly = scenario.explicitRefresh || scenario.finishOpen || scenario.sameProject ? 1 : 0;
          assert.equal(calls.filter((call) => call.stage === "analyzer").length, expectedEarly,
            "Only a request belonging to the new project may analyze it before its open operation is ready");
          assert.equal(checks, expectedEarly + 1);
          if (!scenario.finishOpen) {
            releaseRead();
            await opening;
            await setup.settle();
          }
          const analyzers = calls.filter((call) => call.stage === "analyzer");
          assert.equal(analyzers.length, scenario.finishOpen ? 1 : expectedEarly + 1);
          assert.ok(analyzers.every((call) => call.args[call.args.indexOf("--project") + 1] === projectFile));
          assert.equal(controller.analysisQueued, false);
          assert.equal(controller.analysisInFlight, false);
        } finally {
          releaseRead?.();
          releaseEnvironment?.();
          await first;
          await opening;
          await setup.settle();
          fsPromises.readFile = originalReadFile;
          controller.dispose();
        }
      }));
    });
  }

  it("drops an old project's on-type refresh while opening a different project", async () => {
    const vscode = createMockVscode();
    const notifications = sourceFileNotifications(vscode);
    await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = sourceFileController(TimelineController, vscode);
      const { controller, calls, document } = setup;
      controller.config = () => ({ get: (key, fallback) => key === "updateMode" ? "onType" : fallback });
      const root = mkdtempSync(join(tmpdir(), "q1-project-edit-timer-"));
      const projectFile = join(root, "q1timeline.yml");
      writeFileSync(projectFile, "sequencers:\n  - id: seq0\n    file: seq0.q1asm\n");
      writeFileSync(join(root, "seq0.q1asm"), "stop\n");
      const fsPromises = require("node:fs").promises;
      const originalReadFile = fsPromises.readFile;
      let release!: () => void;
      let started!: () => void;
      const waiting = new Promise<void>((resolve) => { started = resolve; });
      fsPromises.readFile = async (...args) => {
        if (args[0] === projectFile) await new Promise<void>((resolve) => { release = resolve; started(); });
        return originalReadFile(...args);
      };
      let opening: Promise<void> | undefined;
      try {
        await controller.runAnalysis();
        calls.length = 0;
        document.text = "wait_sync 4\nwait 32\nstop\n";
        document.isDirty = true;
        document.version += 1;
        notifications.edit({ document, contentChanges: [{ text: document.text }] });
        const timer = controller.changeTimer;
        assert.notEqual(timer, undefined);
        controller.setProjectUri(vscode.Uri.file(controller.projectUri.fsPath));
        assert.equal(controller.changeTimer, timer, "Reusing the same project must retain its pending edit refresh");
        opening = controller.openTarget({ projectFile });
        await waiting;
        clock.advance(1000);
        await setup.settle();
        const analyzesBeforeProjectReadCompletes = calls.filter((call) => call.stage === "analyzer").length;
        release();
        await opening;
        assert.equal(analyzesBeforeProjectReadCompletes, 0, "An old project's edit timer must not analyze the new project while it is opening");
        assert.deepEqual(calls.filter((call) => call.stage === "analyzer")
          .map((call) => call.args[call.args.indexOf("--project") + 1]), [projectFile]);
        assert.equal(controller.changeTimer, undefined);
      } finally {
        release?.();
        await opening;
        fsPromises.readFile = originalReadFile;
        controller.dispose();
      }
    }));
  });

  for (const previousState of ["paused", "completed"]) {
    it(`consumes an on-type refresh when Run replaces a ${previousState} session after an external source change`, async () => {
      const vscode = createMockVscode();
      const notifications = sourceFileNotifications(vscode);
      const clients: any[] = [];
      let loadedSourcePath: string;
      let finishInitialRun!: () => void;
      let finishNewRun!: () => void;
      vscode.PlaybackClient = class {
        disposed = false;
        program: string;
        index: number;
        rejectPending: any;
        constructor() {
          this.program = readFileSync(loadedSourcePath, "utf8");
          this.index = clients.length;
          clients.push(this);
        }
        async request(message) {
          const playback = { status: this.index === 0 ? previousState : "completed",
            can_resume: this.index === 0 && previousState === "paused", steps: 4 };
          if (message.command === "run") return new Promise((resolve, reject) => {
            this.rejectPending = reject;
            const finish = () => { this.rejectPending = undefined; resolve({ playback }); };
            if (this.index === 0) finishInitialRun = finish;
            else finishNewRun = finish;
          });
          return { ir: { events: [], playback } };
        }
        dispose() {
          this.disposed = true;
          this.rejectPending?.(new Error("Playback client disposed."));
          this.rejectPending = undefined;
        }
      };
      await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
        const setup = sourceFileController(TimelineController, vscode);
        const { controller, calls, document, sourcePath } = setup;
        loadedSourcePath = sourcePath;
        controller.config = () => ({ get: (key, fallback) => key === "updateMode" ? "onType" : fallback });
        let initial: Promise<void> | undefined;
        let running: Promise<void> | undefined;
        try {
          await controller.runAnalysis();
          initial = controller.handleWebviewMessage({ type: "executionControl", action: "play" });
          if (previousState === "paused") await controller.handleWebviewMessage({ type: "executionControl", action: "pause" });
          finishInitialRun();
          await initial;
          assert.equal(controller.playbackClient, clients[0]);
          assert.equal(controller.executionState.status, previousState);
          const oldProgram = document.text;
          calls.length = 0;
          document.text = "wait_sync 4\nwait 32\nstop\n";
          document.version += 1;
          writeFileSync(sourcePath, document.text);
          await notifications.change(document.uri);
          assert.equal(clients[0].disposed, true, "The changed file must invalidate the cached execution before Run");
          assert.equal(controller.playbackClient, undefined);
          assert.notEqual(controller.changeTimer, undefined);
          assert.equal(controller.hasUnsavedProjectChanges(), false);
          running = controller.handleWebviewMessage({ type: "executionControl", action: "play" });
          assert.equal(clients.length, 2);
          assert.equal(clients[0].program, oldProgram);
          assert.equal(clients[1].program, document.text, "The new worker must load the changed on-disk program");
          clock.advance(1000);
          await setup.settle();
          const interrupted = clients[1].disposed;
          finishNewRun();
          await running;
          assert.equal(interrupted, false, "An older automatic refresh must not dispose the execution the user just started");
          assert.equal(calls.filter((call) => call.stage === "analyzer").length, 0);
          assert.equal(controller.executionState.status, "completed");
          assert.equal(controller.changeTimer, undefined);
        } finally {
          finishInitialRun?.();
          finishNewRun?.();
          await Promise.allSettled([initial, running].filter(Boolean));
          controller.dispose();
        }
      }));
    });
  }

  it("preserves a pending on-type refresh when Run is rejected for busy or unsaved source", async () => {
    const vscode = createMockVscode();
    await withFakeTimers(async () => withMockedVscode(vscode, async ({ TimelineController }) => {
      for (const reason of ["busy", "unsaved"]) {
        const { controller, calls } = playbackController(TimelineController, vscode, () => assert.fail("A rejected Run must not contact the worker"));
        controller.config = () => ({ get: (key, fallback) => key === "updateMode" ? "onType" : fallback });
        controller.scheduleAnalysis();
        const timer = controller.changeTimer;
        if (reason === "busy") controller.analysisInFlight = true;
        else controller.hasUnsavedProjectChanges = () => true;
        await controller.handleWebviewMessage({ type: "executionControl", action: "play" });
        assert.equal(controller.changeTimer, timer, `${reason} Run rejection must retain the automatic refresh`);
        assert.deepEqual(calls, []);
        controller.dispose();
      }
    }));
  });

  for (const firstEvent of ["save", "watcher"]) {
    it(`analyzes identical save and watcher bytes once with ${firstEvent} first`, async () => {
      const vscode = createMockVscode();
      const notifications = sourceFileNotifications(vscode);
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const setup = sourceFileController(TimelineController, vscode);
        const { controller, calls, document, sourcePath } = setup;
        const execFile = controller.execFile;
        let started!: () => void;
        let release!: () => void;
        const running = new Promise<void>((resolve) => { started = resolve; });
        let gate = true;
        controller.execFile = async (...args) => {
          if (args[5] === "analyzer" && gate) {
            gate = false;
            await new Promise<void>((resolve) => { release = resolve; started(); });
          }
          return execFile(...args);
        };
        const first = firstEvent === "save" ? notifications.save(document) : notifications.change(document.uri);
        await running;
        notifications.edit({ document, contentChanges: [] });
        if (firstEvent === "save") await notifications.change(document.uri);
        else await notifications.save(document);
        assert.equal(readFileSync(sourcePath, "utf8"), document.getText());
        release();
        await first;
        await setup.settle();
        assert.equal(calls.filter((call) => call.stage === "analyzer").length, 1);
        assert.equal(calls.filter((call) => call.stage === "renderer").length, 1);
        assert.equal(controller.sourceRevision, controller.analyzedSourceRevision, "The duplicate event must not leave successfully analyzed source stale");
        controller.dispose();
      });
    });
  }

  it("deduplicates each file independently when two saved files' watcher events arrive later", async () => {
    const vscode = createMockVscode();
    const notifications = sourceFileNotifications(vscode);
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = sourceFileController(TimelineController, vscode);
      const { controller, calls, document } = setup;
      const other = { ...document, uri: vscode.Uri.file(join(controller.outputDir, "seq1.q1asm")) };
      writeFileSync(other.uri.fsPath, other.text);
      controller.projectRelatedPaths.add(other.uri.fsPath);
      await notifications.save(document);
      await notifications.save(other);
      const revision = controller.sourceRevision;
      await notifications.change(document.uri);
      await notifications.change(other.uri);
      await setup.settle();
      assert.equal(calls.filter((call) => call.stage === "analyzer").length, 2);
      assert.equal(controller.sourceRevision, revision);
      assert.equal(controller.analyzedSourceRevision, revision);
      controller.dispose();
    });
  });

  it("analyzes different external bytes even when the open document has already reloaded them", async () => {
    const vscode = createMockVscode();
    const notifications = sourceFileNotifications(vscode);
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = sourceFileController(TimelineController, vscode);
      const { controller, calls, document, sourcePath } = setup;
      await notifications.save(document);
      document.text = "wait_sync 4\nwait 32\nstop\n";
      document.version += 1;
      writeFileSync(sourcePath, document.text);
      notifications.edit({ document, contentChanges: [{ text: document.text }] });
      assert.equal(document.isDirty, false);
      await notifications.change(document.uri);
      await setup.settle();
      assert.equal(calls.filter((call) => call.stage === "analyzer").length, 2);
      assert.equal(controller.sourceRevision, controller.analyzedSourceRevision);
      controller.dispose();
    });
  });

  it("keeps an external analysis fresh when the clean document reload arrives after its watcher", async () => {
    const vscode = createMockVscode();
    const notifications = sourceFileNotifications(vscode);
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = sourceFileController(TimelineController, vscode);
      const { controller, calls, document, sourcePath } = setup;
      await notifications.save(document);
      const externalText = "wait_sync 4\nwait 48\nstop\n";
      writeFileSync(sourcePath, externalText);
      await notifications.change(document.uri);
      const revision = controller.sourceRevision;
      document.text = externalText;
      document.version += 1;
      await notifications.edit({ document, contentChanges: [{ text: externalText }] });
      await setup.settle();
      assert.equal(calls.filter((call) => call.stage === "analyzer").length, 2);
      assert.equal(controller.sourceRevision, revision, "Reloading the exact bytes already analyzed must not invalidate that result");
      assert.equal(controller.sourceRevision, controller.analyzedSourceRevision);
      controller.dispose();
    });
  });

  it("invalidates cached source edits before VS Code marks the document dirty", async () => {
    const vscode = createMockVscode();
    const notifications = sourceFileNotifications(vscode);
    await withFakeTimers(async () => withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = sourceFileController(TimelineController, vscode);
      const { controller, document } = setup;
      controller.config = () => ({ get: (key, fallback) => key === "updateMode" ? "onType" : fallback });
      await notifications.save(document);
      const revision = controller.sourceRevision;
      let disposedPlayback = 0;
      controller.playbackClient = { dispose() { disposedPlayback += 1; } };
      controller.executionState = { status: "paused" };
      document.text = "wait_sync 4\nwait 256\nstop\n";
      notifications.edit({ document, contentChanges: [{ text: document.text }] });
      assert.equal(document.isDirty, false, "VS Code can deliver the content event before updating dirty state");
      assert.equal(controller.sourceRevision, revision + 1);
      assert.equal(disposedPlayback, 1);
      assert.notEqual(controller.changeTimer, undefined);
      document.isDirty = true;
      notifications.edit({ document, contentChanges: [] });
      assert.equal(controller.sourceRevision, revision + 1, "The following dirty metadata event must only update the UI");
      controller.dispose();
    }));
  });

  it("processes deletion and identical recreation instead of retaining an old byte snapshot", async () => {
    const vscode = createMockVscode();
    const notifications = sourceFileNotifications(vscode);
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = sourceFileController(TimelineController, vscode);
      const { controller, calls, document, sourcePath } = setup;
      await notifications.save(document);
      require("node:fs").rmSync(sourcePath);
      await notifications.delete(document.uri);
      writeFileSync(sourcePath, document.text);
      await notifications.create(document.uri);
      await notifications.change(document.uri);
      await setup.settle();
      assert.equal(calls.filter((call) => call.stage === "analyzer").length, 3);
      assert.equal(controller.sourceRevision, controller.analyzedSourceRevision);
      controller.dispose();
    });
  });

  it("retains normal change handling when disk bytes cannot be read", async () => {
    const vscode = createMockVscode();
    const notifications = sourceFileNotifications(vscode);
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = sourceFileController(TimelineController, vscode);
      const { controller, calls, document, sourcePath } = setup;
      await notifications.save(document);
      const fsPromises = require("node:fs").promises;
      const originalRead = fsPromises.readFile;
      try {
        fsPromises.readFile = async (...args) => {
          if (args[0] === sourcePath) throw Object.assign(new Error("Cannot read source"), { code: "EACCES" });
          return originalRead(...args);
        };
        await notifications.change(document.uri);
        await notifications.change(document.uri);
      } finally { fsPromises.readFile = originalRead; }
      await notifications.change(document.uri);
      await notifications.change(document.uri);
      await setup.settle();
      assert.equal(calls.filter((call) => call.stage === "analyzer").length, 4, "Unreadable notifications must not be treated as equal to the previous bytes");
      controller.dispose();
    });
  });

  for (const transition of ["project switch", "newer file notification"]) {
    it(`drops a delayed source byte read after a ${transition}`, async () => {
      const vscode = createMockVscode();
      const notifications = sourceFileNotifications(vscode);
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const setup = sourceFileController(TimelineController, vscode);
        const { controller, calls, document, sourcePath } = setup;
        const fsPromises = require("node:fs").promises;
        const originalRead = fsPromises.readFile;
        let started!: () => void;
        let release!: () => void;
        const reading = new Promise<void>((resolve) => { started = resolve; });
        let first = true;
        fsPromises.readFile = async (...args) => {
          const content = await originalRead(...args);
          if (args[0] === sourcePath && first) {
            first = false;
            await new Promise<void>((resolve) => { release = resolve; started(); });
          }
          return content;
        };
        try {
          const old = notifications.change(document.uri);
          await reading;
          if (transition === "project switch") {
            controller.setProjectUri(vscode.Uri.file(join(controller.outputDir, "other-project", "q1timeline.yml")));
          } else {
            writeFileSync(sourcePath, "wait_sync 4\nwait 64\nstop\n");
            await notifications.change(document.uri);
          }
          const revision = controller.sourceRevision;
          release();
          await old;
          await setup.settle();
          assert.equal(calls.filter((call) => call.stage === "analyzer").length, transition === "project switch" ? 0 : 1);
          assert.equal(controller.sourceRevision, revision, "The older read must not invalidate the latest source state");
          if (transition !== "project switch") {
            await notifications.change(document.uri);
            assert.equal(calls.filter((call) => call.stage === "analyzer").length, 1, "The newer bytes must remain the deduplication snapshot");
          }
        } finally {
          fsPromises.readFile = originalRead;
          controller.dispose();
        }
      });
    });
  }

  it("preserves manual updates and flushes an on-type watcher refresh on save", async () => {
    const vscode = createMockVscode();
    const notifications = sourceFileNotifications(vscode);
    await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = sourceFileController(TimelineController, vscode);
      const { controller, calls, document, sourcePath } = setup;
      let mode = "manual";
      controller.config = () => ({ get: (key, fallback) => key === "updateMode" ? mode : fallback });
      await notifications.save(document);
      await notifications.change(document.uri);
      clock.advance(1000);
      assert.equal(calls.length, 0);
      mode = "onSave";
      await notifications.save(document);
      assert.equal(calls.filter((call) => call.stage === "analyzer").length, 1);
      mode = "onType";
      writeFileSync(sourcePath, "wait_sync 4\nwait 128\nstop\n");
      await notifications.change(document.uri);
      assert.notEqual(controller.changeTimer, undefined);
      await notifications.save(document);
      await setup.settle();
      clock.advance(1000);
      await setup.settle();
      assert.equal(calls.filter((call) => call.stage === "analyzer").length, 2);
      assert.equal(controller.changeTimer, undefined);
      assert.equal(controller.sourceRevision, controller.analyzedSourceRevision);
      controller.dispose();
    }));
  });

  it("keeps reverted saved source fresh and selectable while explicit Refresh still reruns identical bytes", async () => {
    const vscode = createMockVscode();
    const notifications = sourceFileNotifications(vscode);
    await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = sourceFileController(TimelineController, vscode);
      const { controller, calls, document, sourcePath } = setup;
      const posted: any[] = [];
      controller.panel.webview.postMessage = (message) => posted.push(message);
      await notifications.save(document);
      const originalText = document.text;
      document.isDirty = true;
      document.text += "wait 4\n";
      notifications.edit({ document, contentChanges: [{ text: document.text }] });
      document.text = originalText;
      notifications.edit({ document, contentChanges: [{ text: originalText }] });
      document.isDirty = false;
      await notifications.save(document);
      await notifications.change(document.uri);
      assert.equal(calls.filter((call) => call.stage === "analyzer").length, 2);
      await controller.refreshPreview();
      assert.equal(calls.filter((call) => call.stage === "analyzer").length, 3, "Explicit Refresh must always run even when bytes did not change");
      const revision = controller.sourceRevision;
      notifications.edit({ document, contentChanges: [] });
      assert.equal(controller.sourceRevision, revision, "Save/dirty metadata events must not invalidate unchanged source");
      assert.ok(posted.some((message) => message.type === "setUnsavedChanges" && !message.hasUnsavedChanges));
      assert.equal(controller.sourceRevision, controller.analyzedSourceRevision);
      controller.timelineIr = { source_map: { by_source: { [`${sourcePath}:2`]: ["selected"] } }, events: [{ id: "selected" }] };
      vscode.window.activeTextEditor = { document, selection: sourceSelection(1, 2, 0) };
      await controller.handleWebviewMessage({ type: "webviewReady" });
      clock.advance(250);
      assert.deepEqual(posted.filter((message) => message.type === "highlightEventIds"), [{
        type: "highlightEventIds", highlightEventIds: ["selected"], zoomToRange: true,
        sourceRange: { file: sourcePath, startLine: 2, endLine: 2 },
      }]);
      controller.dispose();
    }));
  });

  function playbackController(TimelineController, vscode, request) {
    const controller = new TimelineController(createContext());
    const posted = [];
    const published = [];
    const calls = [];
    let disposed = 0;
    const client = { request: async (message) => { calls.push(message); return request(message); }, dispose: () => disposed++ };
    controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
    controller.outputDir = "C:\\repo\\.q1timeline";
    controller.pythonEnvironment = resolvedPython(controller.projectUri);
    controller.panel = { webview: { postMessage: (message) => posted.push(message) }, dispose() {} };
    controller.createPlaybackClient = () => client;
    controller.publishPlaybackSnapshot = async (ir) => { controller.timelineIr = ir; published.push(ir); };
    return { controller, client, calls, posted, published, disposed: () => disposed };
  }

  it("applies shared external inputs before rerunning, resets them and retains overrides across source refresh", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const trigger = { id: "shot", address: 1, targets: ["drive", "readout"], times_ns: [1000] };
      let inputs = { triggers: [] }, overridden = false;
      const state = () => ({ status: "completed", can_resume: false, steps: 8,
        external_inputs: { ...inputs, overridden } });
      const setup = playbackController(TimelineController, vscode, async (message) => {
        if (message.command === "set_external_inputs") {
          inputs = message.external_inputs || { triggers: [] };
          overridden = message.external_inputs !== null;
        }
        return message.command === "run" ? { playback: state() } : { ir: { events: [], playback: state() } };
      });
      await setup.controller.handleWebviewMessage({ type: "setExternalInputs", triggers: [trigger] });
      assert.deepEqual(setup.calls.map((call) => call.command), ["set_external_inputs", "run", "snapshot"]);
      assert.deepEqual(setup.controller.webviewInitialState().externalInputs, { triggers: [trigger], overridden: true, canSave: true });
      setup.controller.resetPlayback();
      assert.equal(setup.controller.externalInputsState().canSave, false, "An analysis/parameter refresh must require revalidation even before sourceRevision changes");
      setup.controller.invalidateSourceAnalysis();
      await setup.controller.runPlayback();
      assert.equal(setup.controller.externalInputsState().canSave, true);
      assert.deepEqual(setup.calls.slice(3).map((call) => call.command), ["set_external_inputs", "run", "snapshot"]);
      await setup.controller.handleWebviewMessage({ type: "resetExternalInputs" });
      assert.deepEqual(setup.calls[6], { command: "set_external_inputs", external_inputs: null });
      assert.equal(setup.controller.externalInputsState().overridden, false);
      setup.controller.setProjectUri(vscode.Uri.file("C:\\other\\q1timeline.yml"));
      assert.equal(setup.controller.externalInputsOverride, undefined);
      setup.controller.dispose();
    });
  });

  it("keeps the previous trace and worker after an invalid external input edit", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = playbackController(TimelineController, vscode, async () => { throw new Error("Unknown target: missing"); });
      const previous = { events: [], playback: { status: "completed", can_resume: false } };
      setup.controller.timelineIr = previous;
      setup.controller.executionState = previous.playback;
      setup.controller.playbackClient = setup.client;
      await setup.controller.handleWebviewMessage({ type: "setExternalInputs", triggers: [
        { id: "shot", address: 1, targets: ["missing"], times_ns: [1000] },
      ] });
      assert.equal(setup.controller.timelineIr, previous);
      assert.equal(setup.controller.playbackClient, setup.client);
      assert.equal(setup.controller.executionState.status, "completed");
      assert.equal(setup.controller.externalInputsOverride, undefined);
      assert.equal(setup.disposed(), 0);
      assert.match(setup.posted.at(-1).state.error, /Unknown target/);
      assert.equal(setup.calls.length, 1);
      setup.controller.dispose();
    });
  });

  it("saves only applied external inputs without invalidating the equivalent trace", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const trigger = { id: "shot", address: "TRIGGER_INDEX", targets: ["drive"], times_ns: [2000] };
      const state = { status: "completed", can_resume: false, external_inputs: { triggers: [trigger], overridden: false } };
      const setup = playbackController(TimelineController, vscode, async () => ({ ir: { events: [], playback: state } }));
      const project = join(mkdtempSync(join(tmpdir(), "q1-external-inputs-")), "q1timeline.yml");
      writeFileSync(project, "# project\nversion: 1 # preserve\nsequencers: []\n");
      setup.controller.projectUri = vscode.Uri.file(project);
      await setup.controller.refreshProjectRelatedPaths();
      setup.controller.externalInputsOverride = { triggers: [trigger] };
      setup.controller.externalInputsOverrideBaseline = setup.controller.externalInputsBaseline;
      setup.controller.externalInputsValidatedRevision = setup.controller.sourceRevision;
      setup.controller.externalInputsValidatedProjectText = setup.controller.externalInputsProjectText;
      setup.controller.playbackClient = setup.client;
      const revision = setup.controller.sourceRevision;
      await setup.controller.handleWebviewMessage({ type: "saveExternalInputs" });
      assert.match(readFileSync(project, "utf8"), /version: 1 # preserve/);
      assert.match(readFileSync(project, "utf8"), /times_ns:\n\s+- 2000/);
      assert.deepEqual(setup.calls, [{ command: "accept_external_inputs" }]);
      assert.equal(setup.controller.externalInputsState().overridden, false);
      await setup.controller.onWatchedFile(vscode.Uri.file(project));
      assert.equal(setup.controller.sourceRevision, revision);
      assert.equal(setup.controller.playbackClient, setup.client);
      setup.controller.dispose();
    });
  });

  it("refuses to overwrite external inputs edited on disk or save a standalone preview", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = playbackController(TimelineController, vscode, async () => ({}));
      const project = join(mkdtempSync(join(tmpdir(), "q1-external-conflict-")), "q1timeline.yml");
      writeFileSync(project, "sequencers: []\n");
      setup.controller.projectUri = vscode.Uri.file(project);
      await setup.controller.refreshProjectRelatedPaths();
      setup.controller.externalInputsOverride = { triggers: [] };
      setup.controller.externalInputsOverrideBaseline = setup.controller.externalInputsBaseline;
      setup.controller.externalInputsValidatedRevision = setup.controller.sourceRevision;
      setup.controller.externalInputsValidatedProjectText = setup.controller.externalInputsProjectText;
      const changed = "sequencers: []\nexternal_inputs: {triggers: []}\n";
      writeFileSync(project, changed);
      await setup.controller.refreshProjectRelatedPaths();
      await setup.controller.saveExternalInputs();
      assert.equal(readFileSync(project, "utf8"), changed);
      assert.match(setup.controller.externalInputsState().error, /changed on disk/);
      assert.equal(setup.controller.externalInputsState().overridden, true);
      setup.controller.singleFileMode = true;
      await setup.controller.saveExternalInputs();
      assert.match(setup.controller.externalInputsState().error, /standalone Q1ASM/);
      assert.equal(setup.calls.length, 0);
      setup.controller.dispose();
    });
  });

  it("requires retained external inputs to be revalidated after project targets change", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const project = join(mkdtempSync(join(tmpdir(), "q1-external-target-rename-")), "q1timeline.yml");
      let inputs = { triggers: [] }, target = "a";
      writeFileSync(project, "sequencers:\n  - id: a\n    file: main.q1asm\n");
      const state = () => ({ status: "completed", can_resume: false, external_inputs: { ...inputs, overridden: true } });
      const setup = playbackController(TimelineController, vscode, async (message) => {
        if (message.command === "set_external_inputs") {
          if (message.external_inputs.triggers.some((trigger) => trigger.targets.includes(target === "a" ? "b" : "a"))) {
            throw new Error("Unknown target");
          }
          inputs = message.external_inputs;
        }
        return message.command === "run" ? { playback: state() } : { ir: { events: [], playback: state() } };
      });
      const controller = setup.controller;
      controller.projectUri = vscode.Uri.file(project);
      controller.updateMode = () => "manual";
      await controller.refreshProjectRelatedPaths();
      const trigger = { id: "shot", address: 1, targets: ["a"], times_ns: [1000] };
      await controller.setExternalInputs({ triggers: [trigger] });
      assert.equal(controller.externalInputsState().canSave, true);
      target = "b";
      const renamed = "sequencers:\n  - id: b\n    file: main.q1asm\n";
      writeFileSync(project, renamed);
      await controller.onWatchedFile(vscode.Uri.file(project));
      assert.equal(controller.externalInputsState().canSave, false);
      await controller.saveExternalInputs();
      assert.match(controller.externalInputsState().error, /Apply & rerun/);
      assert.equal(readFileSync(project, "utf8"), renamed);
      await controller.setExternalInputs({ triggers: [trigger] });
      assert.equal(controller.externalInputsState().canSave, false);
      assert.deepEqual(controller.externalInputsOverride.triggers[0].targets, ["a"]);
      await controller.setExternalInputs({ triggers: [{ ...trigger, targets: ["b"] }] });
      assert.equal(controller.externalInputsState().canSave, true);
      await controller.saveExternalInputs();
      assert.match(readFileSync(project, "utf8"), /targets:\n\s+- b/);
      assert.equal(controller.externalInputsState().overridden, false);
      controller.dispose();
    });
  });

  it("refuses to save over a project edit whose watcher has not arrived yet", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const project = join(mkdtempSync(join(tmpdir(), "q1-external-save-race-")), "q1timeline.yml");
      const source = "sequencers:\n  - id: a\n    file: main.q1asm\n";
      writeFileSync(project, source);
      const trigger = { id: "shot", address: 1, targets: ["a"], times_ns: [1000] };
      const playback = { status: "completed", can_resume: false, external_inputs: { triggers: [trigger], overridden: true } };
      const setup = playbackController(TimelineController, vscode, async (message) =>
        message.command === "run" ? { playback } : { ir: { events: [], playback } });
      const controller = setup.controller;
      controller.projectUri = vscode.Uri.file(project);
      await controller.refreshProjectRelatedPaths();
      await controller.setExternalInputs({ triggers: [trigger] });
      const renamed = source.replace("id: a", "id: b");
      writeFileSync(project, renamed);
      const previousSnapshots = Array.from(controller.sourceFileSnapshots.entries());
      await controller.saveExternalInputs();
      assert.equal(readFileSync(project, "utf8"), renamed);
      assert.match(controller.externalInputsState().error, /Refresh, then Apply & rerun/);
      assert.deepEqual(Array.from(controller.sourceFileSnapshots.entries()), previousSnapshots);
      assert.equal(controller.externalInputsState().overridden, true);
      assert.equal(setup.calls.some((call) => call.command === "accept_external_inputs"), false);
      controller.dispose();
    });
  });

  it("does not mark unchanged external inputs unsaved after applying and rerunning", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const trigger = { id: "shot", address: 1, targets: ["a"], times_ns: [1000] };
      const playback = { status: "completed", can_resume: false, external_inputs: { triggers: [trigger], overridden: false } };
      const setup = playbackController(TimelineController, vscode, async (message) =>
        message.command === "run" ? { playback } : { ir: { events: [], playback } });
      setup.controller.externalInputsProject = { triggers: [trigger] };
      await setup.controller.setExternalInputs({ triggers: [trigger] });
      assert.equal(setup.controller.externalInputsState().overridden, false);
      assert.equal(setup.controller.externalInputsOverride, undefined);
      assert.deepEqual(setup.calls.map((call) => call.command), ["set_external_inputs", "run", "snapshot"]);
      setup.controller.dispose();
    });
  });

  it("clears unsaved input state when restored session inputs now match the project", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const trigger = { id: "shot", address: 1, targets: ["a"], times_ns: [1000] };
      let overridden = true;
      const playback = () => ({ status: "completed", can_resume: false, external_inputs: { triggers: [trigger], overridden } });
      const setup = playbackController(TimelineController, vscode, async (message) =>
        message.command === "run" ? { playback: playback() } : { ir: { events: [], playback: playback() } });
      await setup.controller.setExternalInputs({ triggers: [trigger] });
      assert.equal(setup.controller.externalInputsState().overridden, true);
      setup.controller.invalidateSourceAnalysis();
      overridden = false;
      await setup.controller.runPlayback();
      assert.equal(setup.controller.externalInputsState().overridden, false);
      assert.deepEqual(setup.controller.externalInputsProject, { triggers: [trigger] });
      assert.equal(setup.controller.externalInputsOverride, undefined);
      assert.equal(setup.controller.externalInputsOverrideBaseline, undefined);
      assert.equal(setup.controller.externalInputsValidatedRevision, undefined);
      assert.equal(setup.controller.externalInputsValidatedProjectText, undefined);
      assert.deepEqual(setup.calls.slice(3).map((call) => call.command), ["set_external_inputs", "run", "snapshot"]);
      setup.controller.dispose();
    });
  });

  for (const stage of ["environment setup", "snapshot rendering"]) {
    for (const outcome of ["success", "failure"]) {
      it(`runs a new project during old playback ${stage} and preserves it after a late ${outcome}`, async () => {
        const vscode = createMockVscode();
        const clients: any[] = [];
        let projectA: string;
        let projectB: string;
        let aChecks = 0;
        let started!: () => void;
        let resolveOlder!: () => void;
        let rejectOlder!: (error: Error) => void;
        let finishNewWorker!: () => void;
        const waiting = new Promise<void>((resolve) => { started = resolve; });
        const pause = () => new Promise<void>((resolve, reject) => {
          resolveOlder = resolve;
          rejectOlder = reject;
          started();
        });
        vscode.resolveFileEnvironment = async (_api, resource) => {
          if (stage === "environment setup" && resource.fsPath === projectA) {
            if (++aChecks === 1) return undefined;
            await pause();
          }
          return resolvedPython(resource);
        };
        vscode.PlaybackClient = class {
          project: string;
          disposed = false;
          rejectPending: any;
          constructor(_command, args) {
            this.project = args[args.indexOf("--project") + 1];
            clients.push(this);
          }
          async request(message) {
            const playback = { status: "completed", can_resume: false, steps: 1 };
            if (message.command === "run" && this.project === projectB) {
              return new Promise((resolve, reject) => {
                this.rejectPending = reject;
                finishNewWorker = () => { this.rejectPending = undefined; resolve({ playback }); };
              });
            }
            return message.command === "snapshot" ? { ir: { events: [], playback } } : { playback };
          }
          dispose() {
            this.disposed = true;
            this.rejectPending?.(new Error("Playback client disposed."));
            this.rejectPending = undefined;
          }
        };
        await withMockedVscode(vscode, async ({ TimelineController }) => {
          const { controller } = fileEnvironmentController(TimelineController, vscode);
          projectA = controller.projectUri.fsPath;
          const outputA = controller.outputDir;
          const bRoot = mkdtempSync(join(tmpdir(), "q1-playback-new-project-"));
          projectB = join(bRoot, "q1timeline.yml");
          writeFileSync(projectB, "sequencers: []\n");
          const execute = controller.execFile.bind(controller);
          controller.execFile = async (...args) => {
            const htmlPath = args[1][args[1].indexOf("--out") + 1];
            if (stage === "snapshot rendering" && args[5] === "renderer" &&
                htmlPath.startsWith(join(outputA, "playback-"))) await pause();
            return execute(...args);
          };
          let older: Promise<void> | undefined;
          let newer: Promise<void> | undefined;
          try {
            await controller.runAnalysis();
            older = controller.handleWebviewMessage({ type: "executionControl", action: "play" });
            await waiting;
            await controller.openTarget({ projectFile: projectB });
            assert.equal(controller.analysisStatus.status, "idle");
            newer = controller.handleWebviewMessage({ type: "executionControl", action: "play" });
            const newClient = clients.find((client) => client.project === projectB);
            if (outcome === "success") resolveOlder();
            else rejectOlder(new Error("Old playback failed late."));
            await older;
            const stateAfterOldCompletion = {
              busy: controller.playbackInFlight,
              running: controller.playbackRunning,
              status: controller.executionState.status,
              client: controller.playbackClient,
              disposed: newClient?.disposed,
            };
            finishNewWorker?.();
            await newer;
            assert.ok(newClient, "The new project's Run must start before the old request finishes");
            assert.deepEqual(stateAfterOldCompletion, {
              busy: true, running: true, status: "running", client: newClient, disposed: false,
            }, "An obsolete playback completion must not clear or dispose the new execution");
            assert.equal(controller.executionState.status, "completed");
          } finally {
            resolveOlder?.();
            finishNewWorker?.();
            await Promise.allSettled([older, newer].filter(Boolean));
            controller.dispose();
          }
        });
      });
    }
  }

  it("pauses between execution slices and resumes the same session", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      let release;
      let runs = 0;
      const paused = { status: "paused", can_resume: true, steps: 10 };
      const completed = { status: "completed", can_resume: false, steps: 20 };
      const setup = playbackController(TimelineController, vscode, async (message) => {
        if (message.command === "run" && ++runs === 1) return new Promise((resolve) => { release = resolve; });
        if (message.command === "run") return { playback: completed };
        return { ir: { events: [], playback: runs === 1 ? paused : completed } };
      });
      const first = setup.controller.runPlayback();
      setup.controller.pauseExecution();
      release({ playback: paused });
      await first;
      assert.equal(runs, 1);
      assert.equal(setup.controller.executionState.status, "paused");
      assert.equal(setup.controller.playbackClient, setup.client);
      await setup.controller.runPlayback();
      assert.equal(runs, 2);
      assert.equal(setup.published.length, 2);
      assert.equal(setup.controller.executionState.status, "completed");
      assert.equal(setup.disposed(), 0);
      assert.deepEqual(setup.calls[0], { command: "run", budget_ms: 100, max_steps: 1000 });
      setup.controller.dispose();
      assert.equal(setup.disposed(), 1);
    });
  });

  it("stops a play action at its wall-clock budget while retaining resumable state", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const state = { status: "paused", can_resume: true, steps: 20 };
      const setup = playbackController(TimelineController, vscode, async (message) =>
        message.command === "run" ? { playback: state } : { ir: { events: [], playback: state } });
      const realNow = Date.now;
      let now = 0;
      Date.now = () => (now += 2500);
      try { await setup.controller.runPlayback(); } finally { Date.now = realNow; }
      assert.equal(setup.calls.filter((call) => call.command === "run").length, 2);
      assert.equal(setup.controller.executionState.can_resume, true);
      assert.equal(setup.published.length, 1);
      setup.controller.dispose();
    });
  });

  it("keeps manual fold state for mode changes but starts each calculated trace collapsed", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const state = { status: "completed", can_resume: false, steps: 4 };
      const setup = playbackController(TimelineController, vscode, async (message) =>
        message.command === "run" ? { playback: state } : { ir: { events: [], playback: state } });
      assert.equal(setup.controller.webviewInitialState().playbackSnapshotId, undefined);
      await setup.controller.runPlayback();
      const first = setup.controller.webviewInitialState().playbackSnapshotId;
      assert.equal(typeof first, "string");
      assert.ok(first.length > 0);
      await setup.controller.setViewMode("debug");
      assert.equal(setup.controller.webviewInitialState().playbackSnapshotId, first);
      await setup.controller.runPlayback();
      assert.notEqual(setup.controller.webviewInitialState().playbackSnapshotId, first);
      setup.controller.resetPlayback();
      assert.equal(setup.controller.webviewInitialState().playbackSnapshotId, undefined);
      setup.controller.dispose();
    });
  });

  it("changes only a verified execution occurrence and recalculates one path", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const state = { status: "completed", can_resume: false, steps: 4 };
      const setup = playbackController(TimelineController, vscode, async (message) =>
        message.command === "run" ? { playback: state } : { ir: { events: [], playback: state } });
      setup.controller.playbackClient = setup.client;
      setup.controller.timelineIr = { events: [{ id: "e3", meta: { playback_branch_id: "seq:step:2" } }] };
      await setup.controller.setExecutionBranch("e3", "seq:step:1", "taken");
      await setup.controller.setExecutionBranch("e3", "seq:step:2", "both");
      assert.equal(setup.calls.length, 0);
      await setup.controller.setExecutionBranch("e3", "seq:step:2", "taken");
      assert.deepEqual(setup.calls.map((call) => call.command), ["choose", "run", "snapshot"]);
      assert.deepEqual(setup.calls[0], { command: "choose", branch_id: "seq:step:2", path: "taken" });
      assert.equal(setup.published.length, 1);
      setup.controller.dispose();
    });
  });

  it("discards execution results when source changes during a slice", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      let release;
      const setup = playbackController(TimelineController, vscode, () => new Promise((resolve) => { release = resolve; }));
      const running = setup.controller.runPlayback();
      setup.controller.invalidateSourceAnalysis();
      release({ playback: { status: "completed", can_resume: false, steps: 4 } });
      await running;
      assert.equal(setup.disposed(), 1);
      assert.equal(setup.published.length, 0);
      assert.equal(setup.calls.length, 1);
      assert.equal(setup.controller.executionState.status, "idle");
      setup.controller.dispose();
    });
  });

  it("changes a verified feedback occurrence, preserving unknown separately from zero", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const state = { status: "completed", can_resume: false, steps: 4 };
      const ir = { events: [
        { id: "i", meta: { playback_feedback_id: "seq:feedback:1" } },
        { id: "q", meta: { playback_feedback_id: "seq:feedback:2" } },
      ], playback: state };
      const setup = playbackController(TimelineController, vscode, async (message) =>
        message.command === "run" ? { playback: state } : { ir });
      setup.controller.playbackClient = setup.client;
      setup.controller.timelineIr = ir;
      const message = { type: "setExecutionFeedback", eventId: "i", feedbackId: "seq:feedback:1", value: null, delayNs: 0 };
      await setup.controller.handleWebviewMessage({ ...message, eventId: "q" });
      await setup.controller.handleWebviewMessage({ ...message, delayNs: -1 });
      setup.controller.playbackInFlight = true;
      await setup.controller.handleWebviewMessage(message);
      setup.controller.playbackInFlight = false;
      assert.equal(setup.calls.length, 0);
      await setup.controller.handleWebviewMessage(message);
      await setup.controller.handleWebviewMessage({ ...message, eventId: "q", feedbackId: "seq:feedback:2", value: 0, delayNs: 1000 });
      assert.deepEqual(setup.calls.filter((call) => call.command === "set_feedback"), [
        { command: "set_feedback", feedback_id: "seq:feedback:1", value: null, delay_ns: 0 },
        { command: "set_feedback", feedback_id: "seq:feedback:2", value: 0, delay_ns: 1000 },
      ]);
      assert.deepEqual(setup.calls.map((call) => call.command), ["set_feedback", "run", "snapshot", "set_feedback", "run", "snapshot"]);
      assert.equal(setup.published.length, 2);
      setup.controller.dispose();
    });
  });

  it("drops feedback edits when source changes before replay starts", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      let release;
      const setup = playbackController(TimelineController, vscode, () => new Promise((resolve) => { release = resolve; }));
      setup.controller.playbackClient = setup.client;
      setup.controller.timelineIr = { events: [{ id: "i", meta: { playback_feedback_id: "seq:feedback:1" } }] };
      const edit = setup.controller.setExecutionFeedback("i", "seq:feedback:1", 12, 500);
      setup.controller.invalidateSourceAnalysis();
      release({ ir: { events: [] } });
      await edit;
      assert.equal(setup.calls.length, 1);
      assert.equal(setup.published.length, 0);
      assert.equal(setup.disposed(), 1);
      setup.controller.dispose();
    });
  });

  it("keeps the existing preview on playback failure and refuses unsaved sources", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = playbackController(TimelineController, vscode, () => { throw new Error("worker exited"); });
      const previousIr = { events: [{ id: "previous" }] };
      setup.controller.timelineIr = previousIr;
      setup.controller.hasUnsavedProjectChanges = () => true;
      await setup.controller.runPlayback();
      assert.equal(setup.calls.length, 0);
      assert.equal(setup.controller.executionState.status, "blocked");
      setup.controller.hasUnsavedProjectChanges = () => false;
      await setup.controller.runPlayback();
      assert.equal(setup.controller.timelineIr, previousIr);
      assert.equal(setup.controller.executionState.status, "error");
      assert.equal(setup.disposed(), 1);
      setup.controller.dispose();
    });
  });

  it("does not overwrite analyzer files or publish a snapshot invalidated during rendering", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const outputDir = mkdtempSync(join(tmpdir(), "q1lens-playback-render-"));
      controller.projectUri = vscode.Uri.file(join(outputDir, "q1timeline.yml"));
      controller.outputDir = outputDir;
      controller.pythonEnvironment = resolvedPython(controller.projectUri);
      controller.panel = { webview: { postMessage() {} }, dispose() {} };
      const client = { dispose() {} };
      controller.playbackClient = client;
      for (const name of ["timeline_ir.json", "timeline.html", "diagnostics.json"]) writeFileSync(join(outputDir, name), "newer analysis");
      let refreshed = false;
      controller.refreshWebview = async () => { refreshed = true; };
      controller.runRender = async (_python, _args, _ir, html) => {
        controller.invalidateSourceAnalysis();
        writeFileSync(html, "old playback");
      };
      await controller.publishPlaybackSnapshot({ events: [], playback: { status: "paused" } }, client);
      assert.equal(refreshed, false);
      assert.equal(controller.analyzedSourceRevision, 0);
      for (const name of ["timeline_ir.json", "timeline.html", "diagnostics.json"]) {
        assert.equal(readFileSync(join(outputDir, name), "utf8"), "newer analysis");
      }
      controller.dispose();
    });
  });

  it("keeps execution controls busy until the resulting timeline is published", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const completed = { status: "completed", can_resume: false, steps: 2 };
      const setup = playbackController(TimelineController, vscode, async (message) =>
        message.command === "run" ? { playback: completed } : { ir: { events: [], playback: completed } });
      setup.controller.publishPlaybackSnapshot = async () => {
        assert.equal(setup.controller.playbackInFlight, true);
        assert.equal(setup.controller.executionState.status, "running");
      };
      await setup.controller.runPlayback();
      assert.equal(setup.controller.executionState.status, "completed");
      setup.controller.dispose();
    });
  });

  it("does not publish a paused view-mode render after execution resumes", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      let workerStarted!: () => void;
      let releaseWorker!: () => void;
      const working = new Promise<void>((resolve) => { workerStarted = resolve; });
      const completed = { status: "completed", can_resume: false, steps: 20 };
      const setup = playbackController(TimelineController, vscode, async (message) => {
        if (message.command === "run") {
          return new Promise((resolve) => { releaseWorker = () => resolve({ playback: completed }); workerStarted(); });
        }
        return { ir: { events: [], playback: completed } };
      });
      const { controller, client } = setup;
      controller.outputDir = mkdtempSync(join(tmpdir(), "q1-playback-view-mode-"));
      controller.playbackClient = client;
      controller.timelineIr = { events: [], playback: { status: "paused", can_resume: true, steps: 10 } };
      controller.executionState = controller.timelineIr.playback;
      controller.publishPlaybackSnapshot = TimelineController.prototype.publishPlaybackSnapshot.bind(controller);
      const publishedStates: string[] = [];
      let html = "<html>Current paused view</html>";
      Object.assign(controller.panel.webview, { cspSource: "vscode-resource:", asWebviewUri: (uri) => uri });
      Object.defineProperty(controller.panel.webview, "html", {
        get() { return html; },
        set(value) { html = value; publishedStates.push(controller.timelineIr.playback.status); },
      });
      let renderStarted!: () => void;
      let releaseRender!: () => void;
      const rendering = new Promise<void>((resolve) => { renderStarted = resolve; });
      let firstRender = true;
      controller.runRender = async (_python, _args, _ir, htmlPath) => {
        if (firstRender) {
          firstRender = false;
          await new Promise<void>((resolve) => { releaseRender = resolve; renderStarted(); });
        }
        writeFileSync(htmlPath, "<html><head></head><body>Rendered execution</body></html>");
        return { elapsedMs: 1, exitCode: 0 };
      };
      const changing = controller.setViewMode("debug");
      await rendering;
      const running = controller.runPlayback();
      await working;
      releaseRender();
      await changing;
      const publishedDuringRun = [...publishedStates];
      const stateDuringRun = controller.executionState.status;
      releaseWorker();
      await running;
      assert.deepEqual(publishedDuringRun, [], "A previous paused snapshot must not replace the view while the worker is running");
      assert.equal(stateDuringRun, "running");
      assert.deepEqual(publishedStates, ["completed"], "The new execution result must still publish normally");
      assert.equal(controller.executionState.status, "completed");
      controller.dispose();
    });
  });

  it("does not warn for supported legacy and current analyzer releases", async () => {
    const vscode = createMockVscode();
    const warnings: string[] = [];
    vscode.window.showWarningMessage = async (message: string) => warnings.push(message);
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.showIncompatibleCoreVersionWarning({ core_version: "0.1.1" });
      controller.showIncompatibleCoreVersionWarning({ core_version: "0.2.0" });
      assert.deepEqual(warnings, []);
      controller.showIncompatibleCoreVersionWarning({ core_version: "0.3.0" });
      assert.equal(warnings.length, 1);
      assert.match(warnings[0], /Supported range: 0\.1\.x or 0\.2\.x/);
      controller.dispose();
    });
  });

  it("declares hardware readiness as an opt-in Q1Lens setting", () => {
    const manifest = JSON.parse(readFileSync(join(__dirname, "../../../package.json"), "utf8"));
    const setting = manifest.contributes.configuration.properties["q1lens.q1timeline.hardwareReadiness"];

    assert.deepEqual(
      { type: setting.type, default: setting.default },
      { type: "boolean", default: false },
    );
  });

  it("cache-busts q1timeline webview assets when injecting them", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ injectWebviewAssetTags }) => {
      const webview = {
        asWebviewUri: (uri: any) => ({
          toString: () => `vscode-resource://${uri.fsPath.split("\\").pop()}`,
        }),
      };
      const html = injectWebviewAssetTags(
        "<html><head></head><body></body></html>",
        webview,
        createContext(),
        "abc123"
      );

      assert.match(html, /renderer\.css\?v=abc123/);
      assert.match(html, /timeline\.css\?v=abc123/);
      assert.match(html, /renderer\.js\?v=abc123/);
      assert.match(html, /timelineAdapter\.js\?v=abc123/);
      assert.match(html, /instructionColors\.js\?v=abc123/);
      assert.match(html, /hardwareView\.css\?v=abc123/);
      assert.match(html, /hardwareState\.js\?v=abc123/);
      assert.match(html, /hardwareView\.js\?v=abc123/);
      assert.match(html, /timeline\.js\?v=abc123/);
      assert.ok(html.indexOf("renderer.css?v=abc123") < html.indexOf("timeline.css?v=abc123"));
      assert.ok(html.indexOf("renderer.js?v=abc123") < html.indexOf("timelineAdapter.js?v=abc123"));
      assert.ok(html.indexOf("instructionColors.js?v=abc123") < html.indexOf("timelineAdapter.js?v=abc123"));
      assert.ok(html.indexOf("timelineAdapter.js?v=abc123") < html.indexOf("hardwareState.js?v=abc123"));
      assert.ok(html.indexOf("hardwareState.js?v=abc123") < html.indexOf("hardwareView.js?v=abc123"));
      assert.ok(html.indexOf("hardwareView.js?v=abc123") < html.indexOf("timeline.js?v=abc123"));
    });
  });

  it("clears legacy q1timeline SVG while keeping the rich diagnostics panel", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ injectWebviewAssetTags }) => {
      const webview = {
        asWebviewUri: (uri: any) => ({
          toString: () => `vscode-resource://${uri.fsPath.split("\\").pop()}`,
        }),
      };
      const html = injectWebviewAssetTags(
        '<html><head></head><body><section id="timeline-root" class="timeline"><svg class="timeline-svg"><g class="loop-bracket"></g></svg></section><section class="diagnostics" aria-label="Diagnostics"><ol><li>warning duplicate</li></ol></section><aside id="event-inspector"></aside></body></html>',
        webview,
        createContext(),
        "abc123"
      );

      assert.match(html, /<section id="timeline-root" class="timeline"><\/section>/);
      assert.doesNotMatch(html, /loop-bracket/);
      assert.doesNotMatch(html, /timeline-svg/);
      assert.match(html, /warning duplicate/);
      assert.match(html, /<section class="diagnostics"/);
      assert.match(html, /<aside id="event-inspector"><\/aside>/);
      assert.match(html, /renderer\.js\?v=abc123/);
    });
  });

  it("allows q1timeline webview scripts from the VS Code webview origin", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ contentSecurityPolicy }) => {
      const csp = contentSecurityPolicy({ cspSource: "vscode-webview://q1lens" }, "abc123");

      assert.match(csp, /script-src 'nonce-abc123' vscode-webview:\/\/q1lens/);
    });
  });

  it("prefers an active editor project over a stored project in another workspace root", async () => {
    const vscode = createMockVscode();
    const root = mkdtempSync(join(tmpdir(), "q1-controller-"));
    const projectA = join(root, "project-a");
    const projectB = join(root, "project-b");
    mkdirSync(projectA);
    mkdirSync(projectB);
    const storedProject = join(projectA, "q1timeline.yml");
    const activeProject = join(projectB, "q1timeline.yml");
    writeFileSync(storedProject, "sequencers: []\n", "utf8");
    writeFileSync(activeProject, "sequencers: []\n", "utf8");
    vscode.workspace.workspaceFolders = [
      { uri: vscode.Uri.file(projectA) },
      { uri: vscode.Uri.file(projectB) },
    ];
    vscode.window.activeTextEditor = {
      document: { uri: vscode.Uri.file(join(projectB, "q1asm", "seq0.q1asm")) },
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext(vscode.Uri.file(storedProject).toString()));
      controller.config = () => ({ get: () => "q1timeline.yml" });
      controller.findProjectFileUpward = () => vscode.Uri.file(activeProject);

      const project = await controller.findProjectFile();

      assert.equal(project.fsPath, activeProject);
      controller.dispose();
    });
  });

  it("uses the configured project file name in the manual project picker", async () => {
    const vscode = createMockVscode();
    let searchedProjectFile = "";

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.config = () => ({
        get: (key: string, fallback: any) => (key === "projectFile" ? "q1timeline.yaml" : fallback),
      });
      controller.findWorkspaceProjectFiles = async (projectFile: string) => {
        searchedProjectFile = projectFile;
        return [vscode.Uri.file("C:\\repo\\q1timeline.yaml")];
      };
      controller.pickProjectFile = async (matches: any[]) => matches[0];
      controller.openPreview = async () => undefined;

      await controller.selectProjectFile();

      assert.equal(searchedProjectFile, "q1timeline.yaml");
      controller.dispose();
    });
  });

  it("rediscovers the active editor project when opening an existing preview", async () => {
    const vscode = createMockVscode();
    const root = mkdtempSync(join(tmpdir(), "q1-open-preview-"));
    const projectA = join(root, "project-a");
    const projectB = join(root, "project-b");
    mkdirSync(projectA);
    mkdirSync(projectB);
    vscode.window.activeTextEditor = {
      document: { uri: vscode.Uri.file(join(projectB, "q1asm", "seq0.q1asm")) },
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file(join(projectA, "q1timeline.yml"));
      controller.outputDir = join(projectA, ".q1timeline");
      controller.panel = { reveal: () => {}, webview: { postMessage: () => {} }, dispose: () => {} };
      controller.config = () => ({ get: () => "q1timeline.yml" });
      controller.findProjectFileUpward = () => vscode.Uri.file(join(projectB, "q1timeline.yml"));
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openPreview();

      assert.equal(controller.projectUri.fsPath, join(projectB, "q1timeline.yml"));
      controller.dispose();
    });
  });

  it("discovers an explicitly requested source using its workspace folder's project settings", async () => {
    const vscode = createMockVscode();
    const root = mkdtempSync(join(tmpdir(), "q1-folder-project-settings-"));
    const projectA = join(root, "a");
    const projectB = join(root, "b");
    mkdirSync(projectA);
    mkdirSync(projectB);
    const fileA = join(projectA, "alpha.q1timeline.yml");
    const fileB = join(projectB, "beta.q1timeline.yml");
    writeFileSync(fileA, "sequencers: []\n");
    writeFileSync(fileB, "sequencers: []\n");
    vscode.window.activeTextEditor = { document: { uri: vscode.Uri.file(join(projectA, "seq.q1asm")) } };
    vscode.workspace.getWorkspaceFolder = (uri) => ({ uri: vscode.Uri.file(uri.fsPath.startsWith(projectB) ? projectB : projectA) });
    vscode.workspace.getConfiguration = (section, resource) => ({ get: (key, fallback) =>
      section === "q1lens" && key === "q1timeline.projectFile"
        ? resource?.fsPath.startsWith(projectB) ? "beta.q1timeline.yml" : "alpha.q1timeline.yml"
        : fallback,
    });
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.setProjectUri(vscode.Uri.file(fileA));
      controller.panel = { reveal() {}, dispose() {} };
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;
      let fallbacks = 0;
      controller.createSingleFileProject = async () => {
        fallbacks += 1;
        return vscode.Uri.file(join(projectB, ".q1timeline", "auto-generated.q1timeline.yml"));
      };
      await controller.openPreview({ sourceUri: vscode.Uri.file(join(projectB, "seq.q1asm")) });
      assert.equal(controller.projectUri.fsPath, fileB);
      assert.equal(fallbacks, 0, "An existing project in the requested source's folder must not be replaced by fallback analysis");
      controller.dispose();
    });
  });

  it("uses the active source folder's settings for manual project selection", async () => {
    const vscode = createMockVscode();
    const root = mkdtempSync(join(tmpdir(), "q1-manual-folder-settings-"));
    const projectA = join(root, "a", "alpha.q1timeline.yml");
    const projectB = join(root, "b", "beta.q1timeline.yml");
    const sourceB = vscode.Uri.file(join(root, "b", "seq.q1asm"));
    vscode.window.activeTextEditor = { document: { uri: sourceB } };
    vscode.workspace.getConfiguration = (section, resource) => ({ get: (key, fallback) =>
      section === "q1lens" && key === "q1timeline.projectFile"
        ? resource?.fsPath === sourceB.fsPath ? "beta.q1timeline.yml" : "alpha.q1timeline.yml"
        : fallback,
    });
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.setProjectUri(vscode.Uri.file(projectA));
      controller.findWorkspaceProjectFiles = async (name) => [vscode.Uri.file(name === "beta.q1timeline.yml" ? projectB : projectA)];
      controller.pickProjectFile = async (matches) => matches[0];
      controller.openPreview = async () => undefined;
      await controller.selectProjectFile();
      assert.equal(controller.projectUri.fsPath, projectB);
      controller.dispose();
    });
  });

  for (const method of ["selectProjectFile", "selectQ1asmFilesInFolder", "openQ1asmFilesInFolder"]) {
    for (const replacement of ["a newer target", "a return to the original target", "no newer request"]) {
      it(`${method} keeps request ownership after a delayed search and ${replacement}`, async () => {
        const vscode = createMockVscode();
        const root = mkdtempSync(join(tmpdir(), "q1-selection-request-"));
        const folder = join(root, "selection");
        mkdirSync(folder);
        const source = vscode.Uri.file(join(folder, "seq.q1asm"));
        writeFileSync(source.fsPath, "wait {WAIT_SELECTED}\nstop\n");
        const selectedProject = join(folder, "q1timeline.yml");
        if (method === "selectProjectFile") writeFileSync(selectedProject, "sequencers: []\n");
        const projects = ["a", "b"].map((name) => {
          mkdirSync(join(root, name));
          const file = join(root, name, "q1timeline.yml");
          writeFileSync(file, "sequencers: []\n");
          return file;
        });
        vscode.window.activeTextEditor = { document: { uri: source } };
        vscode.workspace.getWorkspaceFolder = (uri) => ({ uri: vscode.Uri.file(require("node:path").dirname(uri.fsPath)) });
        const picked: string[] = [], stored: any[] = [];
        vscode.window.showQuickPick = async (items, options) => {
          picked.push(options.placeHolder);
          return options.canPickMany ? items : items[0];
        };
        let release!: () => void, started!: () => void;
        const waiting = new Promise<void>((resolve) => { started = resolve; });
        const pause = () => new Promise<void>((resolve) => { release = resolve; started(); });
        const fsPromises = require("node:fs").promises, readdir = fsPromises.readdir;
        if (method === "selectProjectFile") vscode.workspace.findFiles = async () => {
          await pause();
          await fsPromises.readFile(selectedProject);
          return [vscode.Uri.file(selectedProject)];
        };
        else fsPromises.readdir = async (path, ...args) => {
          if (path === folder) await pause();
          return readdir(path, ...args);
        };
        try {
          await withMockedVscode(vscode, async ({ TimelineController }) => {
            const context = createContext();
            context.workspaceState.update = async (key, value) => { stored.push({ key, value }); };
            const controller = new TimelineController(context);
            const analyzed: string[] = [];
            controller.runAnalysis = async () => { analyzed.push(controller.projectUri.fsPath); };
            await controller.openTarget({ projectFile: projects[0] });
            analyzed.length = 0;
            const first = controller[method](source);
            try {
              await waiting;
              if (replacement !== "no newer request") await controller.openTarget({ projectFile: projects[1] });
              if (replacement === "a return to the original target") await controller.openTarget({ projectFile: projects[0] });
              release();
              await first;
              const generatedProject = join(folder, ".q1timeline", "auto-generated.q1timeline.yml");
              if (replacement === "no newer request") {
                const expected = method === "selectProjectFile" ? selectedProject : generatedProject;
                assert.equal(controller.projectUri.fsPath, expected);
                assert.deepEqual(analyzed, [expected]);
                assert.equal(picked.length, method === "openQ1asmFilesInFolder" ? 0 : 1);
                if (method !== "selectProjectFile") {
                  assert.match(readFileSync(generatedProject, "utf8"), /\.\.\/seq\.q1asm/);
                  assert.deepEqual(Object.keys(JSON.parse(readFileSync(join(folder, ".q1timeline", "auto-generated.params.json"), "utf8"))), ["WAIT_SELECTED"]);
                }
              } else {
                const expected = replacement === "a newer target" ? [projects[1]] : [projects[1], projects[0]];
                assert.equal(controller.projectUri.fsPath, expected.at(-1), "An obsolete search must not replace the newer explicit target");
                assert.deepEqual(analyzed, expected);
                assert.deepEqual(picked, [], "A superseded search must not open a late picker");
                assert.deepEqual(stored, [], "An obsolete search must not change the project restored on activation");
                assert.equal(existsSync(generatedProject), false, "An obsolete folder request must not create a fallback project");
              }
            } finally { release?.(); await first.catch(() => {}); controller.dispose(); }
          });
        } finally { fsPromises.readdir = readdir; }
      });
    }
  }

  for (const picker of ["project selection", "folder subset", "existing folder project", "folder params"]) {
    it(`ignores an obsolete ${picker} picker result after a newer target opens`, async () => {
      const vscode = createMockVscode();
      const folder = mkdtempSync(join(tmpdir(), "q1-selection-picker-"));
      const source = vscode.Uri.file(join(folder, "seq.q1asm"));
      writeFileSync(source.fsPath, "wait {WAIT_SELECTED}\nstop\n");
      const project = join(folder, "q1timeline.yml");
      if (picker === "project selection" || picker === "existing folder project") writeFileSync(project, "sequencers: []\n");
      if (picker === "existing folder project") writeFileSync(join(folder, "other.q1timeline.yml"), "sequencers: []\n");
      if (picker === "folder params") {
        writeFileSync(join(folder, "one.params.json"), "{\"WAIT_SELECTED\": 4}\n");
        writeFileSync(join(folder, "two.params.json"), "{\"WAIT_SELECTED\": 8}\n");
      }
      const nextFolder = mkdtempSync(join(tmpdir(), "q1-selection-picker-new-"));
      const nextProject = join(nextFolder, "q1timeline.yml");
      writeFileSync(nextProject, "sequencers: []\n");
      vscode.window.activeTextEditor = { document: { uri: source } };
      vscode.workspace.getWorkspaceFolder = (uri) => ({ uri: vscode.Uri.file(require("node:path").dirname(uri.fsPath)) });
      vscode.workspace.findFiles = async () => [vscode.Uri.file(project)];
      let release!: () => void, started!: () => void;
      const waiting = new Promise<void>((resolve) => { started = resolve; });
      vscode.window.showQuickPick = async (items, options) => {
        await new Promise<void>((resolve) => { release = resolve; started(); });
        return options.canPickMany ? items : items[0];
      };
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const stored: any[] = [], analyzed: string[] = [];
        const context = createContext();
        context.workspaceState.update = async (key, value) => { stored.push({ key, value }); };
        const controller = new TimelineController(context);
        controller.runAnalysis = async () => { analyzed.push(controller.projectUri.fsPath); };
        const method = picker === "project selection" ? "selectProjectFile"
          : picker === "folder subset" ? "selectQ1asmFilesInFolder" : "openQ1asmFilesInFolder";
        const first = controller[method](source);
        try {
          await waiting;
          await controller.openTarget({ projectFile: nextProject });
          release();
          await first;
          assert.equal(controller.projectUri.fsPath, nextProject, "A prior selection UI must not reclaim a newer open request");
          assert.deepEqual(analyzed, [nextProject]);
          assert.deepEqual(stored, [], "An obsolete project picker must not overwrite the restored project choice");
          assert.equal(existsSync(join(folder, ".q1timeline", "auto-generated.q1timeline.yml")), false);
        } finally { release?.(); await first.catch(() => {}); controller.dispose(); }
      });
    });
  }

  for (const method of ["selectQ1asmFilesInFolder", "openQ1asmFilesInFolder"]) {
    for (const superseded of [true, false]) {
      it(`${method} ${superseded ? "drops an obsolete" : "preserves a current"} folder search failure`, async () => {
        const vscode = createMockVscode();
        const folder = mkdtempSync(join(tmpdir(), "q1-selection-deleted-folder-"));
        const source = vscode.Uri.file(join(folder, "seq.q1asm"));
        writeFileSync(source.fsPath, "stop\n");
        const nextFolder = mkdtempSync(join(tmpdir(), "q1-selection-folder-failure-new-"));
        const nextProject = join(nextFolder, "q1timeline.yml");
        writeFileSync(nextProject, "sequencers: []\n");
        vscode.workspace.getWorkspaceFolder = (uri) => ({ uri: vscode.Uri.file(require("node:path").dirname(uri.fsPath)) });
        const fs = require("node:fs"), readdir = fs.promises.readdir;
        let release!: () => void, started!: () => void;
        const waiting = new Promise<void>((resolve) => { started = resolve; });
        fs.promises.readdir = async (path, ...args) => {
          if (path === folder) await new Promise<void>((resolve) => { release = resolve; started(); });
          return readdir(path, ...args);
        };
        try {
          await withMockedVscode(vscode, async ({ TimelineController }) => {
            const controller = new TimelineController(createContext());
            const analyzed: string[] = [], warnings: string[] = [];
            controller.runAnalysis = async () => { analyzed.push(controller.projectUri.fsPath); };
            vscode.window.showWarningMessage = async (message) => { warnings.push(message); };
            const first = controller[method](source);
            try {
              await waiting;
              if (superseded) await controller.openTarget({ projectFile: nextProject });
              fs.unlinkSync(source.fsPath);
              fs.rmdirSync(folder);
              release();
              if (superseded) {
                await assert.doesNotReject(first, "A deleted old folder must not surface a command error over the newer target");
                assert.equal(controller.projectUri.fsPath, nextProject);
                assert.deepEqual(analyzed, [nextProject]);
              } else await assert.rejects(first, { code: "ENOENT" });
              assert.deepEqual(warnings, [], "An obsolete failure is not a current empty-folder warning");
            } finally { release?.(); await first.catch(() => {}); controller.dispose(); }
          });
        } finally { fs.promises.readdir = readdir; }
      });
    }
  }

  it("keeps concurrent fallback project generation in its own folder and opens the latest request", async () => {
    const vscode = createMockVscode();
    const root = mkdtempSync(join(tmpdir(), "q1-fallback-race-"));
    const folderA = join(root, "a");
    const folderB = join(root, "b");
    mkdirSync(folderA);
    mkdirSync(folderB);
    const sourceA = vscode.Uri.file(join(folderA, "a.q1asm"));
    const sourceB = vscode.Uri.file(join(folderB, "b.q1asm"));
    writeFileSync(sourceA.fsPath, "wait {WAIT_A}\nstop\n");
    writeFileSync(sourceB.fsPath, "wait {WAIT_B}\nstop\n");
    const outputA = join(folderA, ".q1timeline");
    const outputB = join(folderB, ".q1timeline");
    const fsPromises = require("node:fs").promises;
    const original = fsPromises.mkdir;
    let release!: () => void;
    let started!: () => void;
    const waiting = new Promise<void>((resolve) => { started = resolve; });
    fsPromises.mkdir = async (...args) => {
      if (args[0] === outputA) await new Promise<void>((resolve) => { release = resolve; started(); });
      return original(...args);
    };
    try {
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const controller = new TimelineController(createContext());
        const opened: string[] = [];
        controller.openPreview = async () => { opened.push(controller.projectUri.fsPath); };
        controller.findProjectFileCandidatesUpward = () => [];
        controller.paramsFileForAutoGeneratedProject = async () => undefined;
        const first = controller.openQ1asmFilesInFolder(sourceA);
        await waiting;
        await controller.openQ1asmFilesInFolder(sourceB);
        release();
        await first;
        const projectB = join(outputB, "auto-generated.q1timeline.yml");
        assert.deepEqual(Object.keys(JSON.parse(readFileSync(join(outputB, "auto-generated.params.json"), "utf8"))), ["WAIT_B"]);
        assert.match(readFileSync(projectB, "utf8"), /\.\.\/b\.q1asm/);
        assert.equal(controller.projectUri.fsPath, projectB);
        assert.equal(controller.singleFileUri.fsPath, sourceB.fsPath);
        assert.deepEqual(opened, [projectB], "The slower old request must not reopen or replace the latest preview");
        controller.dispose();
      });
    } finally { fsPromises.mkdir = original; }
  });

  for (const stage of ["params", "yaml"]) {
    it(`keeps newer same-folder fallback files intact after a delayed ${stage} operation`, async () => {
      const vscode = createMockVscode();
      const root = mkdtempSync(join(tmpdir(), "q1-same-folder-fallback-"));
      const sourceA = vscode.Uri.file(join(root, "a.q1asm"));
      const sourceB = vscode.Uri.file(join(root, "b.q1asm"));
      writeFileSync(sourceA.fsPath, "wait {WAIT_A}\nstop\n");
      writeFileSync(sourceB.fsPath, "wait {WAIT_B}\nstop\n");
      const outputDir = join(root, ".q1timeline");
      const projectPath = join(outputDir, "auto-generated.q1timeline.yml");
      const fsPromises = require("node:fs").promises;
      const originalWrite = fsPromises.writeFile;
      let release!: () => void;
      let started!: () => void;
      const waiting = new Promise<void>((resolve) => { started = resolve; });
      const pause = () => new Promise<void>((resolve) => { release = resolve; started(); });
      if (stage === "yaml") fsPromises.writeFile = async (...args) => {
        if (args[0] === projectPath && String(args[1]).includes("a.q1asm")) await pause();
        return originalWrite(...args);
      };
      try {
        await withMockedVscode(vscode, async ({ TimelineController }) => {
          const controller = new TimelineController(createContext());
          if (stage === "params") {
            const infer = controller.inferredPlaceholderParams.bind(controller);
            controller.inferredPlaceholderParams = async (uris) => {
              if (uris[0].fsPath === sourceA.fsPath) await pause();
              return infer(uris);
            };
          }
          const first = controller.createSelectedFolderProject(sourceA, [sourceA]);
          await waiting;
          const second = controller.createSelectedFolderProject(sourceB, [sourceB]);
          let timer: NodeJS.Timeout | undefined;
          try {
            // Let an independent newer generation finish, or release a serialized one.
            await Promise.race([second, new Promise<void>((resolve) => { timer = setTimeout(resolve, 100); })]);
          } finally {
            clearTimeout(timer);
            release();
          }
          await Promise.all([first, second]);
          assert.deepEqual(Object.keys(JSON.parse(readFileSync(join(outputDir, "auto-generated.params.json"), "utf8"))), ["WAIT_B"]);
          assert.match(readFileSync(projectPath, "utf8"), /\.\.\/b\.q1asm/);
          assert.equal(controller.singleFileUri.fsPath, sourceB.fsPath);
          controller.dispose();
        });
      } finally { fsPromises.writeFile = originalWrite; }
    });
  }

  it("opens a new q1timeline preview above the current editor group", async () => {
    const vscode = createMockVscode();
    const commands: string[] = [];
    let createdViewColumn: any = undefined;
    vscode.commands.executeCommand = async (command: string) => {
      commands.push(command);
    };
    vscode.window.createWebviewPanel = (_viewType: string, _title: string, viewColumn: any) => {
      createdViewColumn = viewColumn;
      return {
        webview: {
          html: "",
          onDidReceiveMessage: () => ({ dispose: () => {} }),
          asWebviewUri: (uri: any) => uri,
        },
        reveal: () => {},
        onDidDispose: () => ({ dispose: () => {} }),
        dispose: () => {},
      };
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openPreview({ preserveProject: true });

      assert.deepEqual(commands, ["workbench.action.newGroupAbove"]);
      assert.equal(createdViewColumn, vscode.ViewColumn.Active);
      controller.dispose();
    });
  });

  it("reuses an existing q1timeline preview without moving or duplicating it", async () => {
    const vscode = createMockVscode();
    const commands: string[] = [];
    const revealArgs: any[][] = [];
    let createPanelCount = 0;
    vscode.commands.executeCommand = async (command: string) => {
      commands.push(command);
    };
    vscode.window.createWebviewPanel = () => {
      createPanelCount += 1;
      return {};
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.panel = {
        viewColumn: 3,
        reveal: (...args: any[]) => {
          revealArgs.push(args);
        },
        webview: { postMessage: () => {} },
        dispose: () => {},
      };
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openPreview({ preserveProject: true });

      assert.deepEqual(commands, []);
      assert.equal(createPanelCount, 0);
      assert.deepEqual(revealArgs, [[3]], "Reopening must target the timeline's group when a source editor group is active");
      controller.dispose();
    });
  });

  describe("preview request ownership", () => {
    function setup(vscode, TimelineController) {
      const root = mkdtempSync(join(tmpdir(), "q1-preview-request-"));
      const projects = ["a", "b"].map((name) => {
        mkdirSync(join(root, name));
        const file = join(root, name, "q1timeline.yml");
        writeFileSync(file, "sequencers: []\n");
        return file;
      });
      const stored: any[] = [];
      const context = createContext();
      context.workspaceState.update = async (key, value) => { stored.push({ key, value }); };
      const controller = new TimelineController(context);
      controller.refreshProjectRelatedPaths = async () => {};
      controller.startWatchers = () => {};
      const analyzed: string[] = [];
      controller.runAnalysis = async () => { analyzed.push(controller.projectUri.fsPath); };
      return { root, projects, stored, controller, analyzed };
    }

    it("keeps a newer explicit target when earlier project discovery finishes later", async () => {
      const vscode = createMockVscode();
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const { controller, projects, analyzed } = setup(vscode, TimelineController);
        let started!: () => void;
        let release!: () => void;
        const finding = new Promise<void>((resolve) => { started = resolve; });
        vscode.workspace.findFiles = async () => {
          await new Promise<void>((resolve) => { release = resolve; started(); });
          return [vscode.Uri.file(projects[0])];
        };
        const older = controller.openPreview();
        await finding;
        await controller.openTarget({ projectFile: projects[1] });
        release();
        await older;
        assert.equal(controller.projectUri.fsPath, projects[1]);
        assert.deepEqual(analyzed, [projects[1]]);
        controller.dispose();
      });
    });

    it("drops an earlier discovery rejection after a newer target opens", async () => {
      const vscode = createMockVscode();
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const { controller, projects, analyzed } = setup(vscode, TimelineController);
        let started!: () => void;
        let rejectDiscovery!: (error: Error) => void;
        const finding = new Promise<void>((resolve) => { started = resolve; });
        vscode.workspace.findFiles = () => new Promise((_resolve, reject) => { rejectDiscovery = reject; started(); });
        const older = controller.openPreview();
        await finding;
        await controller.openTarget({ projectFile: projects[1] });
        rejectDiscovery(new Error("Previous workspace search failed"));
        await assert.doesNotReject(older, "An obsolete search must not show a command failure over the newer preview");
        assert.equal(controller.projectUri.fsPath, projects[1]);
        assert.deepEqual(analyzed, [projects[1]]);
        controller.dispose();
      });
    });

    it("preserves discovery errors for the current open request", async () => {
      const vscode = createMockVscode();
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const { controller } = setup(vscode, TimelineController);
        const error = new Error("Current workspace search failed");
        vscode.workspace.findFiles = async () => { throw error; };
        await assert.rejects(controller.openPreview(), (actual) => actual === error);
        controller.dispose();
      });
    });

    it("does not store an obsolete discovery picker choice after a newer target opens", async () => {
      const vscode = createMockVscode();
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const { controller, projects, analyzed, stored } = setup(vscode, TimelineController);
        vscode.workspace.findFiles = async () => projects.map((file) => vscode.Uri.file(file));
        let started!: () => void;
        let release!: () => void;
        const picking = new Promise<void>((resolve) => { started = resolve; });
        vscode.window.showQuickPick = async (items) => {
          await new Promise<void>((resolve) => { release = resolve; started(); });
          return items[0];
        };
        const older = controller.openPreview();
        await picking;
        await controller.openTarget({ projectFile: projects[1] });
        release();
        await older;
        assert.deepEqual(stored, [], "An old picker must not change the project restored on the next activation");
        assert.equal(controller.projectUri.fsPath, projects[1]);
        assert.deepEqual(analyzed, [projects[1]]);
        controller.dispose();
      });
    });

    it("does not resume an older open after fallback discovery is superseded", async () => {
      const vscode = createMockVscode();
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const { controller, root, projects, analyzed } = setup(vscode, TimelineController);
        const source = vscode.Uri.file(join(root, "orphan", "seq.q1asm"));
        mkdirSync(join(root, "orphan"));
        writeFileSync(source.fsPath, "stop\n");
        let started!: () => void;
        let release!: () => void;
        const finding = new Promise<void>((resolve) => { started = resolve; });
        controller.q1asmFilesInFolder = async () => {
          await new Promise<void>((resolve) => { release = resolve; started(); });
          return [source];
        };
        const older = controller.openPreview({ sourceUri: source });
        await finding;
        await controller.openTarget({ projectFile: projects[1] });
        release();
        await older;
        assert.equal(controller.projectUri.fsPath, projects[1]);
        assert.deepEqual(analyzed, [projects[1]], "The discarded fallback must not reanalyze the newer target");
        assert.equal(existsSync(join(root, "orphan", ".q1timeline")), false);
        controller.dispose();
      });
    });

    it("keeps an obsolete fallback from changing the source and output of a newer pending discovery", async () => {
      const vscode = createMockVscode();
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const { controller, root, projects } = setup(vscode, TimelineController);
        const source = vscode.Uri.file(join(root, "orphan", "seq.q1asm"));
        mkdirSync(join(root, "orphan"));
        writeFileSync(source.fsPath, "wait {WAIT_A}\nstop\n");
        let paramsStarted!: () => void;
        let releaseParams!: () => void;
        let discoveryStarted!: () => void;
        let releaseDiscovery!: () => void;
        const generating = new Promise<void>((resolve) => { paramsStarted = resolve; });
        const discovering = new Promise<void>((resolve) => { discoveryStarted = resolve; });
        const infer = controller.inferredPlaceholderParams.bind(controller);
        controller.inferredPlaceholderParams = async (...args) => {
          const params = await infer(...args);
          await new Promise<void>((resolve) => { releaseParams = resolve; paramsStarted(); });
          return params;
        };
        vscode.workspace.findFiles = async () => {
          await new Promise<void>((resolve) => { releaseDiscovery = resolve; discoveryStarted(); });
          return [vscode.Uri.file(projects[1])];
        };
        const analyzed: any[] = [];
        controller.runAnalysis = async () => { analyzed.push({
          project: controller.projectUri.fsPath, source: controller.singleFileUri?.fsPath, output: controller.outputDir,
        }); };
        const warnings: string[] = [];
        vscode.window.showWarningMessage = async (message) => { warnings.push(message); };
        const older = controller.openPreview({ sourceUri: source });
        await generating;
        const newer = controller.openPreview();
        await discovering;
        releaseParams();
        await older;
        releaseDiscovery();
        await newer;
        assert.deepEqual(analyzed, [{ project: projects[1], source: undefined, output: join(root, "b", ".q1timeline") }]);
        assert.equal(controller.singleFileMode, false);
        assert.deepEqual(warnings, [], "The obsolete fallback must not report that the newer project was missing");
        assert.equal(existsSync(join(root, "orphan", ".q1timeline", "auto-generated.q1timeline.yml")), false);
        controller.dispose();
      });
    });

    for (const stage of ["related-path loading", "shared panel placement"]) {
      it(`analyzes only the newest open after delayed ${stage}`, async () => {
        const vscode = createMockVscode();
        await withMockedVscode(vscode, async ({ TimelineController }) => {
          const { controller, projects, analyzed } = setup(vscode, TimelineController);
          let started!: () => void;
          let release!: () => void;
          const waiting = new Promise<void>((resolve) => { started = resolve; });
          const pause = () => new Promise<void>((resolve) => { release = resolve; started(); });
          let panels = 0;
          const createPanel = vscode.window.createWebviewPanel;
          vscode.window.createWebviewPanel = (...args) => { panels += 1; return createPanel(...args); };
          if (stage === "related-path loading") {
            controller.refreshProjectRelatedPaths = async () => { if (controller.projectUri.fsPath === projects[0]) await pause(); };
          } else {
            vscode.commands.executeCommand = async () => pause();
          }
          const older = controller.openTarget({ projectFile: projects[0] });
          await waiting;
          const newer = controller.openTarget({ projectFile: projects[1] });
          if (stage === "related-path loading") await newer;
          release();
          await Promise.all([older, newer]);
          assert.deepEqual(analyzed, [projects[1]]);
          assert.equal(panels, 1, "Overlapping opens must continue sharing the first panel placement");
          controller.dispose();
        });
      });
    }
  });

  it("shares initial panel placement when preview commands overlap", async () => {
    const vscode = createMockVscode();
    const commands: string[] = [];
    let finishPlacement!: () => void;
    const placement = new Promise<void>((resolve) => { finishPlacement = resolve; });
    vscode.commands.executeCommand = async (command: string) => {
      commands.push(command);
      await placement;
    };
    const createPanel = vscode.window.createWebviewPanel;
    let panelsCreated = 0;
    vscode.window.createWebviewPanel = (...args: any[]) => {
      panelsCreated += 1;
      return createPanel(...args);
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      const first = controller.openPreview({ preserveProject: true });
      const second = controller.openPreview({ preserveProject: true });
      await new Promise((resolve) => setImmediate(resolve));
      const placementRequests = commands.length;
      finishPlacement();
      await Promise.all([first, second]);

      assert.equal(placementRequests, 1, "Concurrent opens must request only one editor group");
      assert.equal(panelsCreated, 1, "Concurrent opens must share one preview panel");
      controller.dispose();
    });
  });

  it("does not create a preview after disposal while editor group placement is pending", async () => {
    const vscode = createMockVscode();
    let finishPlacement!: () => void;
    const placement = new Promise<void>((resolve) => { finishPlacement = resolve; });
    vscode.commands.executeCommand = async () => placement;
    let panelsCreated = 0;
    const createPanel = vscode.window.createWebviewPanel;
    vscode.window.createWebviewPanel = () => { panelsCreated += 1; return createPanel(); };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      let analyses = 0;
      controller.runAnalysis = async () => { analyses += 1; };
      const opening = controller.openPreview({ preserveProject: true });
      await new Promise((resolve) => setImmediate(resolve));
      controller.dispose();
      finishPlacement();
      await opening;
      assert.equal(panelsCreated, 0, "Disposal must cancel the pending panel creation");
      assert.equal(analyses, 0);
      assert.equal(controller.panel, undefined);
    });
  });

  it("does not restart watchers or place a panel after disposal during project loading", async () => {
    const vscode = createMockVscode();
    let finishLoading!: () => void;
    const loading = new Promise<void>((resolve) => { finishLoading = resolve; });
    const placements: string[] = [];
    vscode.commands.executeCommand = async (command) => { placements.push(command); };
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.refreshProjectRelatedPaths = async () => loading;
      let watchers = 0;
      let analyses = 0;
      controller.startWatchers = () => { watchers += 1; };
      controller.runAnalysis = async () => { analyses += 1; };
      const opening = controller.openPreview({ preserveProject: true });
      await new Promise((resolve) => setImmediate(resolve));
      controller.dispose();
      finishLoading();
      await opening;
      assert.equal(watchers, 0);
      assert.deepEqual(placements, []);
      assert.equal(analyses, 0);
      assert.equal(controller.panel, undefined);
    });
  });

  it("keeps the newest watched paths when reads of the same project finish out of order", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const root = mkdtempSync(join(tmpdir(), "q1-watch-same-project-"));
      const projectPath = join(root, "q1timeline.yml");
      writeFileSync(projectPath, "sequencers:\n  - file: old.q1asm\n");
      const controller = new TimelineController(createContext());
      controller.setProjectUri(vscode.Uri.file(projectPath));
      const fsPromises = require("node:fs").promises;
      const originalRead = fsPromises.readFile;
      let started!: () => void;
      let release!: () => void;
      const reading = new Promise<void>((resolve) => { started = resolve; });
      let first = true;
      fsPromises.readFile = async (...args) => {
        const text = await originalRead(...args);
        if (args[0] === projectPath && first) {
          first = false;
          await new Promise<void>((resolve) => { release = resolve; started(); });
        }
        return text;
      };
      try {
        const older = controller.refreshProjectRelatedPaths();
        await reading;
        writeFileSync(projectPath, "sequencers:\n  - file: newest.q1asm\n");
        await controller.refreshProjectRelatedPaths();
        release();
        await older;
        assert.equal(controller.isProjectRelated(vscode.Uri.file(join(root, "newest.q1asm"))), true);
        assert.equal(controller.isProjectRelated(vscode.Uri.file(join(root, "old.q1asm"))), false);
      } finally {
        fsPromises.readFile = originalRead;
        controller.dispose();
      }
    });
  });

  it("does not overwrite the new project's watched sources with a late previous-project read", async () => {
    const vscode = createMockVscode();
    const root = mkdtempSync(join(tmpdir(), "q1-watch-project-switch-"));
    const projectA = join(root, "a", "q1timeline.yml");
    const projectB = join(root, "b", "q1timeline.yml");
    mkdirSync(join(root, "a"));
    mkdirSync(join(root, "b"));
    writeFileSync(projectA, "sequencers:\n  - file: a.q1asm\n");
    writeFileSync(projectB, "sequencers:\n  - file: b.q1asm\n");
    const fsPromises = require("node:fs").promises;
    const original = fsPromises.readFile;
    let release!: () => void;
    let started!: () => void;
    const waiting = new Promise<void>((resolve) => { started = resolve; });
    fsPromises.readFile = async (...args) => {
      if (args[0] === projectA) await new Promise<void>((resolve) => { release = resolve; started(); });
      return original(...args);
    };
    try {
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const controller = new TimelineController(createContext());
        controller.setProjectUri(vscode.Uri.file(projectA));
        const loadingA = controller.refreshProjectRelatedPaths();
        await waiting;
        controller.setProjectUri(vscode.Uri.file(projectB));
        await controller.refreshProjectRelatedPaths();
        release();
        await loadingA;
        assert.equal(controller.isProjectRelated(vscode.Uri.file(join(root, "b", "b.q1asm"))), true);
        assert.equal(controller.isProjectRelated(vscode.Uri.file(join(root, "b", "a.q1asm"))), false);
        controller.dispose();
      });
    } finally { fsPromises.readFile = original; }
  });

  it("finds active q1timeline projects for uppercase Q1ASM editor paths", async () => {
    const vscode = createMockVscode();
    const root = mkdtempSync(join(tmpdir(), "q1-uppercase-"));
    const projectDir = join(root, "ProjectA");
    const q1asmDir = join(projectDir, "q1asm");
    mkdirSync(q1asmDir, { recursive: true });
    const projectFile = join(projectDir, "q1timeline.yml");
    writeFileSync(projectFile, "sequencers: []\n", "utf8");

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const sourceUri = vscode.Uri.file(join(q1asmDir.toLowerCase(), "SEQ0.Q1ASM"));

      const discovered = controller.findProjectFileUpward(sourceUri, "q1timeline.yml");

      assert.equal(discovered?.fsPath.toLowerCase(), projectFile.toLowerCase());
      controller.dispose();
    });
  });

  it("rebuilds single-file fallback projects when the active Q1ASM file changes", async () => {
    const vscode = createMockVscode();
    const oldFile = "C:\\repo\\old\\a.q1asm";
    const newFile = "C:\\repo\\new\\b.q1asm";
    const newProject = "C:\\repo\\new\\.q1timeline\\single-file.q1timeline.yml";
    vscode.window.activeTextEditor = {
      document: { uri: vscode.Uri.file(newFile) },
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.singleFileMode = true;
      controller.singleFileUri = vscode.Uri.file(oldFile);
      controller.projectUri = vscode.Uri.file("C:\\repo\\old\\.q1timeline\\single-file.q1timeline.yml");
      controller.outputDir = "C:\\repo\\old\\.q1timeline";
      controller.panel = { webview: { postMessage: () => {} }, reveal: () => {}, dispose: () => {} };
      controller.findProjectFileUpward = () => undefined;
      controller.findProjectFile = async () => undefined;
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;
      let created = false;
      controller.createSingleFileProject = async () => {
        created = true;
        controller.singleFileUri = vscode.Uri.file(newFile);
        controller.singleFileMode = true;
        controller.outputDir = "C:\\repo\\new\\.q1timeline";
        return vscode.Uri.file(newProject);
      };

      await controller.openPreview();

      assert.equal(created, true);
      assert.equal(controller.projectUri.fsPath, newProject);
      assert.equal(controller.singleFileUri.fsPath, newFile);
      controller.dispose();
    });
  });

  it("uses single-file fallback for an active orphan Q1ASM instead of a cached project", async () => {
    const vscode = createMockVscode();
    const sourceFile = "C:\\repo\\scratch\\orphan.q1asm";
    const singleProject = "C:\\repo\\scratch\\.q1timeline\\single-file.q1timeline.yml";
    vscode.window.activeTextEditor = {
      document: { uri: vscode.Uri.file(sourceFile) },
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\project-a\\q1timeline.yml");
      controller.singleFileMode = false;
      controller.panel = { webview: { postMessage: () => {} }, reveal: () => {}, dispose: () => {} };
      controller.findProjectFileUpward = () => undefined;
      controller.findProjectFile = async () => undefined;
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;
      let created = false;
      controller.createSingleFileProject = async () => {
        created = true;
        controller.singleFileUri = vscode.Uri.file(sourceFile);
        controller.singleFileMode = true;
        controller.outputDir = "C:\\repo\\scratch\\.q1timeline";
        return vscode.Uri.file(singleProject);
      };

      await controller.openPreview();

      assert.equal(created, true);
      assert.equal(controller.projectUri.fsPath, singleProject);
      assert.equal(controller.singleFileUri.fsPath, sourceFile);
      controller.dispose();
    });
  });

  it("uses an explicit Q1ASM URI for single-file fallback instead of the active editor", async () => {
    const vscode = createMockVscode();
    const activeFile = "C:\\repo\\active\\active.q1asm";
    const requestedFile = "C:\\repo\\clicked\\clicked.q1asm";
    const singleProject = "C:\\repo\\clicked\\.q1timeline\\single-file.q1timeline.yml";
    vscode.window.activeTextEditor = {
      document: { uri: vscode.Uri.file(activeFile) },
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.findProjectFileUpward = () => undefined;
      controller.findProjectFile = async () => undefined;
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;
      let createdFor: string | undefined;
      controller.createSingleFileProject = async (sourceUri: any) => {
        createdFor = sourceUri.fsPath;
        controller.singleFileUri = sourceUri;
        controller.singleFileMode = true;
        controller.outputDir = "C:\\repo\\clicked\\.q1timeline";
        return vscode.Uri.file(singleProject);
      };

      await controller.openPreview({ sourceUri: vscode.Uri.file(requestedFile) });

      assert.equal(createdFor, requestedFile);
      assert.equal(controller.projectUri.fsPath, singleProject);
      assert.equal(controller.singleFileUri.fsPath, requestedFile);
      controller.dispose();
    });
  });

  it("auto-generates from the clicked Q1ASM folder instead of using a parent project", async () => {
    const vscode = createMockVscode();
    const root = mkdtempSync(join(tmpdir(), "q1-clicked-folder-"));
    const projectDir = join(root, "project");
    const q1asmDir = join(projectDir, "child");
    mkdirSync(q1asmDir, { recursive: true });
    const parentProject = join(projectDir, "q1timeline.yml");
    const clickedFile = join(q1asmDir, "clicked.q1asm");
    writeFileSync(parentProject, "sequencers: []\n", "utf8");
    writeFileSync(clickedFile, "wait_sync 4\nstop\n", "utf8");
    vscode.workspace.workspaceFolders = [{ uri: vscode.Uri.file(projectDir) }];
    vscode.workspace.getWorkspaceFolder = () => ({ uri: vscode.Uri.file(projectDir) });

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openPreview({ sourceUri: vscode.Uri.file(clickedFile) });

      const generatedProject = join(q1asmDir, ".q1timeline", "auto-generated.q1timeline.yml");
      assert.equal(controller.projectUri.fsPath, generatedProject);
      assert.equal(controller.singleFileMode, true);
      assert.equal(existsSync(generatedProject), true);
      controller.dispose();
    });
  });

  it("tells the user to open or select Q1ASM when no project or Q1ASM source is available", async () => {
    const vscode = createMockVscode();
    const warnings: string[] = [];
    vscode.window.showWarningMessage = async (message: string) => {
      warnings.push(message);
      return undefined;
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.findProjectFile = async () => undefined;
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openPreview();

      assert.deepEqual(warnings, [
        "Open or select a .q1asm file, or create q1timeline.yml, before opening a Q1ASM timeline.",
      ]);
      controller.dispose();
    });
  });

  it("creates an auto-generated folder project from every sibling Q1ASM file", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-session-"));
    const alpha = join(folder, "alpha.q1asm");
    const beta = join(folder, "beta.q1asm");
    const notes = join(folder, "notes.txt");
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(beta, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(notes, "ignore me\n", "utf8");

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.findProjectFileUpward = () => undefined;
      controller.findProjectFile = async () => undefined;
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openPreview({ sourceUri: vscode.Uri.file(beta) });

      const projectPath = join(folder, ".q1timeline", "auto-generated.q1timeline.yml");
      assert.equal(controller.projectUri.fsPath, projectPath);
      assert.equal(controller.singleFileMode, true);
      assert.equal(existsSync(projectPath), true);
      const project = readFileSync(projectPath, "utf8").replace(/\r\n/g, "\n");
      assert.match(project, /id: "alpha"/);
      assert.match(project, /name: "alpha"/);
      assert.match(project, /file: "\.\.\/alpha\.q1asm"/);
      assert.match(project, /id: "beta"/);
      assert.match(project, /name: "beta"/);
      assert.match(project, /file: "\.\.\/beta\.q1asm"/);
      assert.equal(project.includes("notes.txt"), false);
      controller.dispose();
    });
  });

  it("creates inferred placeholder params for auto-generated folder projects", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-params-"));
    const alpha = join(folder, "alpha.q1asm");
    const beta = join(folder, "beta.q1asm");
    writeFileSync(
      alpha,
      ".DEF T_TOTAL {T_TOTAL}\n.DEF START_ALIGN {START_ALIGN}\nwait $START_ALIGN\nplay 0,1,$T_TOTAL\nstop\n",
      "utf8",
    );
    writeFileSync(
      beta,
      ".DEF GAIN_ID {GAIN_ID}\n.DEF IQ_SHIFT {IQ_SHIFT}\nfb_pop_data $GAIN_ID,R0\nfb_acq_iq_shift $IQ_SHIFT,4\nstop\n",
      "utf8",
    );

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.findProjectFileUpward = () => undefined;
      controller.findProjectFile = async () => undefined;
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openPreview({ sourceUri: vscode.Uri.file(alpha) });

      const projectPath = join(folder, ".q1timeline", "auto-generated.q1timeline.yml");
      const paramsPath = join(folder, ".q1timeline", "auto-generated.params.json");
      const project = readFileSync(projectPath, "utf8").replace(/\r\n/g, "\n");
      const params = JSON.parse(readFileSync(paramsPath, "utf8"));

      assert.match(project, /params:\n  file: "auto-generated\.params\.json"/);
      assert.equal(params.T_TOTAL, 4000);
      assert.equal(params.START_ALIGN, 40);
      assert.equal(params.GAIN_ID, 0);
      assert.equal(params.IQ_SHIFT, 0);
      controller.dispose();
    });
  });

  it("reports auto-generated folder project contents in the output channel", async () => {
    const vscode = createMockVscode();
    const outputLines: string[] = [];
    vscode.window.createOutputChannel = () => ({
      appendLine: (message: string) => {
        outputLines.push(message);
      },
      show: () => {},
      dispose: () => {},
    });
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-output-"));
    const alpha = join(folder, "alpha.q1asm");
    const beta = join(folder, "beta.q1asm");
    writeFileSync(alpha, ".DEF T_TOTAL {T_TOTAL}\nwait $T_TOTAL\nstop\n", "utf8");
    writeFileSync(beta, "wait_sync 4\nstop\n", "utf8");

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.findProjectFileUpward = () => undefined;
      controller.findProjectFile = async () => undefined;
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openPreview({ sourceUri: vscode.Uri.file(alpha) });

      assert.ok(outputLines.some((line) => line.includes("Auto-generated q1timeline fallback includes 2 Q1ASM file(s): alpha.q1asm, beta.q1asm")));
      assert.ok(outputLines.some((line) => line.includes("Auto-generated q1timeline fallback params: auto-generated.params.json")));
      controller.dispose();
    });
  });

  it("selects a subset of folder Q1ASM files for the transient project", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-subset-"));
    const alpha = join(folder, "alpha.q1asm");
    const beta = join(folder, "beta.q1asm");
    const gamma = join(folder, "gamma.q1asm");
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(beta, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(gamma, "wait_sync 4\nstop\n", "utf8");
    let quickPickOptions: any;
    vscode.window.showQuickPick = async (items: any[], options: any) => {
      quickPickOptions = options;
      return items.filter((item) => item.label !== "gamma.q1asm");
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.findProjectFileUpward = () => undefined;
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.selectQ1asmFilesInFolder(vscode.Uri.file(alpha));

      const projectPath = join(folder, ".q1timeline", "auto-generated.q1timeline.yml");
      assert.equal(quickPickOptions.canPickMany, true);
      assert.equal(controller.projectUri.fsPath, projectPath);
      const project = readFileSync(projectPath, "utf8").replace(/\r\n/g, "\n");
      assert.match(project, /file: "\.\.\/alpha\.q1asm"/);
      assert.match(project, /file: "\.\.\/beta\.q1asm"/);
      assert.equal(project.includes("gamma.q1asm"), false);
      controller.dispose();
    });
  });

  it("opens every Q1ASM file in a folder without prompting", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-all-"));
    const alpha = join(folder, "alpha.q1asm");
    const beta = join(folder, "beta.q1asm");
    const gamma = join(folder, "gamma.q1asm");
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(beta, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(gamma, "wait_sync 4\nstop\n", "utf8");
    let quickPickCalled = false;
    const warnings: string[] = [];
    vscode.window.showQuickPick = async () => {
      quickPickCalled = true;
      return [];
    };
    vscode.window.showWarningMessage = async (message: string) => {
      warnings.push(message);
      return undefined;
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openQ1asmFilesInFolder(vscode.Uri.file(alpha));

      const projectPath = join(folder, ".q1timeline", "auto-generated.q1timeline.yml");
      assert.equal(quickPickCalled, false);
      assert.deepEqual(warnings, []);
      assert.equal(controller.projectUri.fsPath, projectPath);
      const project = readFileSync(projectPath, "utf8").replace(/\r\n/g, "\n");
      assert.match(project, /file: "\.\.\/alpha\.q1asm"/);
      assert.match(project, /file: "\.\.\/beta\.q1asm"/);
      assert.match(project, /file: "\.\.\/gamma\.q1asm"/);
      controller.dispose();
    });
  });

  it("opens an existing folder q1timeline project instead of auto-generating one", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-existing-project-"));
    const q1asmDir = join(folder, "q1asm");
    mkdirSync(q1asmDir, { recursive: true });
    const alpha = join(q1asmDir, "alpha.q1asm");
    const projectPath = join(folder, "q1timeline.yml");
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(join(folder, "params.json"), "{\"T_TOTAL\": 4}\n", "utf8");
    writeFileSync(
      projectPath,
      "sequencers:\n  - id: alpha\n    name: alpha\n    file: q1asm/alpha.q1asm\nparams:\n  file: params.json\n",
      "utf8",
    );

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openQ1asmFilesInFolder(vscode.Uri.file(alpha));

      assert.equal(controller.projectUri.fsPath, projectPath);
      assert.equal(existsSync(join(q1asmDir, ".q1timeline", "auto-generated.q1timeline.yml")), false);
      controller.dispose();
    });
  });

  it("opens an existing .q1timeline project next to current-folder Q1ASM files", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-dot-project-"));
    const dotQ1timeline = join(folder, ".q1timeline");
    mkdirSync(dotQ1timeline, { recursive: true });
    const alpha = join(folder, "alpha.q1asm");
    const projectPath = join(dotQ1timeline, "q1timeline.yml");
    const generatedProject = join(dotQ1timeline, "auto-generated.q1timeline.yml");
    let quickPickCalled = false;
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(projectPath, "sequencers:\n  - id: alpha\n    file: ../alpha.q1asm\n", "utf8");
    writeFileSync(generatedProject, "sequencers:\n  - id: stale\n    file: ../stale.q1asm\n", "utf8");
    vscode.window.showQuickPick = async (items: any[]) => {
      quickPickCalled = true;
      return items[0];
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openQ1asmFilesInFolder(vscode.Uri.file(alpha));

      assert.equal(quickPickCalled, false);
      assert.equal(controller.projectUri.fsPath, projectPath);
      controller.dispose();
    });
  });

  it("opens an existing custom q1timeline project instead of auto-generating one", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-custom-project-"));
    const q1asmDir = join(folder, "q1asm");
    mkdirSync(q1asmDir, { recursive: true });
    const alpha = join(q1asmDir, "alpha.q1asm");
    const projectPath = join(folder, "experiment.q1timeline.yml");
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(projectPath, "sequencers:\n  - id: alpha\n    file: q1asm/alpha.q1asm\n", "utf8");

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openQ1asmFilesInFolder(vscode.Uri.file(alpha));

      assert.equal(controller.projectUri.fsPath, projectPath);
      assert.equal(existsSync(join(q1asmDir, ".q1timeline", "auto-generated.q1timeline.yml")), false);
      controller.dispose();
    });
  });

  it("uses the default canonical q1timeline.yml when multiple projects are in the same folder", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-multiple-projects-"));
    const q1asmDir = join(folder, "q1asm");
    mkdirSync(q1asmDir, { recursive: true });
    const alpha = join(q1asmDir, "alpha.q1asm");
    const canonicalProject = join(folder, "q1timeline.yml");
    const customProject = join(folder, "experiment.q1timeline.yml");
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(canonicalProject, "sequencers:\n  - id: alpha\n    file: q1asm/alpha.q1asm\n", "utf8");
    writeFileSync(customProject, "sequencers:\n  - id: beta\n    file: q1asm/beta.q1asm\n", "utf8");

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openQ1asmFilesInFolder(vscode.Uri.file(alpha));

      assert.equal(controller.projectUri.fsPath, canonicalProject);
      controller.dispose();
    });
  });

  it("lets the user choose a project when multiple q1timeline yml candidates exist", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-choose-project-"));
    const q1asmDir = join(folder, "q1asm");
    mkdirSync(q1asmDir, { recursive: true });
    const alpha = join(q1asmDir, "alpha.q1asm");
    const canonicalProject = join(folder, "q1timeline.yml");
    const customProject = join(folder, "experiment.q1timeline.yml");
    const quickPickPlaceholders: string[] = [];
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(canonicalProject, "sequencers:\n  - id: alpha\n    file: q1asm/alpha.q1asm\n", "utf8");
    writeFileSync(customProject, "sequencers:\n  - id: beta\n    file: q1asm/beta.q1asm\n", "utf8");
    vscode.window.showQuickPick = async (items: any[], options: any) => {
      quickPickPlaceholders.push(options.placeHolder);
      return items.find((item) => item.uri.fsPath === customProject);
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openQ1asmFilesInFolder(vscode.Uri.file(alpha));

      assert.equal(controller.projectUri.fsPath, customProject);
      assert.deepEqual(quickPickPlaceholders, ["Select q1timeline project"]);
      controller.dispose();
    });
  });

  it("lets the user choose params for an auto-generated q1timeline project", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-choose-params-"));
    const q1asmDir = join(folder, "q1asm");
    mkdirSync(q1asmDir, { recursive: true });
    const alpha = join(q1asmDir, "alpha.q1asm");
    const defaultParams = join(folder, "params.json");
    const customParams = join(folder, "experiment.params.json");
    const quickPickPlaceholders: string[] = [];
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(defaultParams, "{\"T_TOTAL\": 4}\n", "utf8");
    writeFileSync(customParams, "{\"T_TOTAL\": 8}\n", "utf8");
    vscode.window.showQuickPick = async (items: any[], options: any) => {
      quickPickPlaceholders.push(options.placeHolder);
      return items.find((item) => item.uri?.fsPath === customParams);
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openQ1asmFilesInFolder(vscode.Uri.file(alpha));

      const projectPath = join(q1asmDir, ".q1timeline", "auto-generated.q1timeline.yml");
      const project = readFileSync(projectPath, "utf8").replace(/\r\n/g, "\n");
      assert.equal(controller.projectUri.fsPath, projectPath);
      assert.deepEqual(quickPickPlaceholders, ["Select params file"]);
      assert.match(project, /params:\n  file: "\.\.\/\.\.\/experiment\.params\.json"/);
      assert.equal(existsSync(join(q1asmDir, ".q1timeline", "auto-generated.params.json")), false);
      controller.dispose();
    });
  });

  it("lets the user choose params from a sibling .q1timeline directory", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-dot-params-"));
    const dotQ1timeline = join(folder, ".q1timeline");
    mkdirSync(dotQ1timeline, { recursive: true });
    const alpha = join(folder, "alpha.q1asm");
    const paramsFile = join(dotQ1timeline, "params.json");
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(paramsFile, "{\"T_TOTAL\": 8}\n", "utf8");
    vscode.window.showQuickPick = async (items: any[], options: any) => {
      assert.equal(options.placeHolder, "Select params file");
      return items.find((item) => item.uri?.fsPath === paramsFile);
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openQ1asmFilesInFolder(vscode.Uri.file(alpha));

      const projectPath = join(dotQ1timeline, "auto-generated.q1timeline.yml");
      const project = readFileSync(projectPath, "utf8").replace(/\r\n/g, "\n");
      assert.equal(controller.projectUri.fsPath, projectPath);
      assert.match(project, /params:\n  file: "params\.json"/);
      controller.dispose();
    });
  });

  it("clears cached analyzer diff state when opening a different project", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.lastAnalyzerTimelineIr = { events: [{ id: "old" }] };
      controller.openPreview = async () => undefined;
      controller.revealPendingTarget = async () => undefined;

      await controller.openTarget({
        projectFile: "C:\\repo\\project-b\\q1timeline.yml",
        q1asmFile: "C:\\repo\\project-b\\q1asm\\seq0.q1asm",
        sequencer: "seq0",
        line: 1,
      });

      assert.equal(controller.lastAnalyzerTimelineIr, undefined);
      controller.dispose();
    });
  });

  it("clears pending open-target highlights after one reveal attempt", async () => {
    const vscode = createMockVscode();
    const posted: any[] = [];

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.panel = { webview: { postMessage: (message: any) => posted.push(message) }, reveal: () => {}, dispose: () => {} };
      controller.timelineIr = {
        source_map: {
          by_source: { "q1asm/seq0.q1asm:3": ["seq0:e3"] },
        },
        events: [{ id: "seq0:e3" }],
      };
      controller.pendingTarget = {
        projectFile: "C:\\repo\\q1timeline.yml",
        q1asmFile: "q1asm/seq0.q1asm",
        sequencer: "seq0",
        line: 3,
      };

      await controller.revealPendingTarget();

      assert.deepEqual(posted[0], { type: "highlightEventIds", highlightEventIds: ["seq0:e3"] });
      assert.equal(controller.pendingTarget, undefined);
      controller.dispose();
    });
  });

  it("reuses a hidden already-open Q1ASM tab when a timeline event is clicked", async () => {
    const vscode = createMockVscode();
    const sourcePath = "C:\\repo\\q1asm\\seq0.q1asm";
    const existingDocument = { uri: vscode.Uri.file(sourcePath) };
    let openedDocument = false;
    let shownDocument: any = undefined;
    let shownOptions: any = undefined;
    vscode.workspace.textDocuments = [existingDocument];
    vscode.window.visibleTextEditors = [];
    vscode.window.tabGroups.all = [
      {
        viewColumn: 1,
        tabs: [{ input: { uri: vscode.Uri.file(sourcePath) } }],
      },
    ];
    vscode.workspace.openTextDocument = async () => {
      openedDocument = true;
      return { uri: vscode.Uri.file(sourcePath) };
    };
    vscode.window.showTextDocument = async (document: any, options: any) => {
      shownDocument = document;
      shownOptions = options;
      return {
        document,
        viewColumn: 1,
        setDecorations: () => {},
        revealRange: () => {},
      };
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.timelineIr = {
        project: { root: "C:\\repo" },
        events: [
          {
            id: "seq0:e3",
            source: { file: "q1asm/seq0.q1asm", line: 3, column: 1 },
          },
        ],
      };

      await controller.openSourceForEvent("seq0:e3");

      assert.equal(openedDocument, false);
      assert.equal(shownDocument, existingDocument);
      assert.deepEqual(shownOptions, { viewColumn: 1, preview: false });
      controller.dispose();
    });
  });

  for (const [timelineColumn, sourceColumn] of [[1, 1], [1, 2], [1, undefined], [3, 2], [3, 3], [3, undefined]]) {
    it(`keeps RT detail selection focused in timeline group ${timelineColumn} when the source group is ${sourceColumn ?? "missing"}`, async () => {
      const vscode = createMockVscode();
      const sourcePath = "C:\\repo\\q1asm\\seq0.q1asm";
      const document = { uri: vscode.Uri.file(sourcePath) };
      const shown: any[] = [];
      const commands: string[] = [];
      const editor = { document, viewColumn: 2, setDecorations() {}, revealRange() {} };
      vscode.workspace.textDocuments = sourceColumn === undefined ? [] : [document];
      vscode.workspace.openTextDocument = async () => document;
      vscode.window.tabGroups.all = sourceColumn === undefined ? [] : [
        { viewColumn: sourceColumn, tabs: [{ input: { uri: document.uri } }] },
      ];
      vscode.commands.executeCommand = async (command) => { commands.push(command); };
      vscode.window.showTextDocument = async (target, options) => { shown.push({ target, options }); return editor; };

      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const controller = new TimelineController(createContext());
        controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
        controller.panel = { viewColumn: timelineColumn, dispose() {} };
        controller.timelineIr = {
          project: { root: "C:\\repo" },
          events: [{ id: "seq:e3", source: { file: sourcePath, line: 3, column: 4 } }],
        };

        await controller.handleWebviewMessage({ type: "eventClick", eventId: "seq:e3", preserveFocus: true });

        assert.equal(shown.length, 1);
        assert.equal(shown[0].target, document);
        assert.deepEqual(shown[0].options, { viewColumn: sourceColumn !== undefined && sourceColumn !== timelineColumn ? sourceColumn : vscode.ViewColumn.Beside, preview: false, preserveFocus: true });
        assert.equal(editor.selection.active.line, 2);
        assert.equal(editor.selection.active.character, 3);
        assert.deepEqual(commands, [], "RT detail inspection must not activate a new editor group");

        if (sourceColumn !== undefined) {
          await controller.handleWebviewMessage({ type: "eventClick", eventId: "seq:e3" });
          assert.deepEqual(shown[1].options, { viewColumn: sourceColumn, preview: false }, "Explicit navigation must retain its existing source focus behavior");
        }
        controller.dispose();
      });
    });
  }

  it("finds a later source group when its first matching tab shares the timeline group", async () => {
    const vscode = createMockVscode();
    const sourcePath = "C:\\repo\\q1asm\\seq0.q1asm";
    vscode.window.tabGroups.all = [
      { viewColumn: 1, tabs: [{ input: { uri: vscode.Uri.file(sourcePath) } }] },
      { viewColumn: 2, tabs: [{ input: { uri: vscode.Uri.file("C:\\repo\\notes.txt") } }] },
      { viewColumn: 3, tabs: [{ input: { uri: vscode.Uri.file(sourcePath) } }] },
    ];
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.panel = { viewColumn: 1, dispose() {} };
      controller.lastSourceViewColumn = 1;
      assert.deepEqual(await controller.sourceShowTextDocumentOptions(sourcePath, true, true),
        { viewColumn: 3, preview: false, preserveFocus: true });
      controller.dispose();
    });
  });

  it("opens new Q1ASM sources in an existing Q1ASM tab group", async () => {
    const vscode = createMockVscode();
    const openTabPath = "C:\\repo\\q1asm\\seq0.q1asm";
    const targetPath = "C:\\repo\\q1asm\\seq1.q1asm";
    const targetDocument = { uri: vscode.Uri.file(targetPath) };
    let openedUri: any = undefined;
    let shownDocument: any = undefined;
    let shownOptions: any = undefined;
    vscode.window.visibleTextEditors = [];
    vscode.window.tabGroups.all = [
      {
        viewColumn: 3,
        tabs: [{ input: { uri: vscode.Uri.file(openTabPath) } }],
      },
    ];
    vscode.workspace.openTextDocument = async (uri: any) => {
      openedUri = uri;
      return targetDocument;
    };
    vscode.window.showTextDocument = async (document: any, options: any) => {
      shownDocument = document;
      shownOptions = options;
      return {
        document,
        viewColumn: 3,
        setDecorations: () => {},
        revealRange: () => {},
      };
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.timelineIr = {
        project: { root: "C:\\repo" },
        events: [
          {
            id: "seq1:e3",
            source: { file: "q1asm/seq1.q1asm", line: 3, column: 1 },
          },
        ],
      };

      await controller.openSourceForEvent("seq1:e3");

      assert.equal(openedUri.fsPath, targetPath);
      assert.equal(shownDocument, targetDocument);
      assert.deepEqual(shownOptions, { viewColumn: 3, preview: false });
      controller.dispose();
    });
  });

  it("opens new Q1ASM sources in a below editor group when no source tab exists", async () => {
    const vscode = createMockVscode();
    const targetPath = "C:\\repo\\q1asm\\seq1.q1asm";
    const targetDocument = { uri: vscode.Uri.file(targetPath) };
    const commands: string[] = [];
    let shownDocument: any = undefined;
    let shownOptions: any = undefined;
    vscode.window.visibleTextEditors = [];
    vscode.window.tabGroups.all = [];
    vscode.commands.executeCommand = async (command: string) => {
      commands.push(command);
    };
    vscode.workspace.openTextDocument = async () => targetDocument;
    vscode.window.showTextDocument = async (document: any, options: any) => {
      shownDocument = document;
      shownOptions = options;
      return {
        document,
        viewColumn: vscode.ViewColumn.Active,
        setDecorations: () => {},
        revealRange: () => {},
      };
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.timelineIr = {
        project: { root: "C:\\repo" },
        events: [
          {
            id: "seq1:e3",
            source: { file: "q1asm/seq1.q1asm", line: 3, column: 1 },
          },
        ],
      };

      await controller.openSourceForEvent("seq1:e3");

      assert.deepEqual(commands, ["workbench.action.newGroupBelow"]);
      assert.equal(shownDocument, targetDocument);
      assert.deepEqual(shownOptions, { viewColumn: vscode.ViewColumn.Active, preview: false });
      controller.dispose();
    });
  });

  it("queues reveal current line when opening the preview first", async () => {
    const vscode = createMockVscode();
    const posted: any[] = [];
    vscode.window.activeTextEditor = {
      document: { uri: vscode.Uri.file("C:\\repo\\q1asm\\seq0.q1asm") },
      selection: { active: { line: 2 } },
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.panel = undefined;
      controller.openPreview = async () => {
        controller.panel = { webview: { postMessage: (message: any) => posted.push(message) }, dispose: () => {} };
        controller.timelineIr = {
          source_map: {
            by_source: { "q1asm/seq0.q1asm:3": ["seq0:e3"] },
          },
          events: [{ id: "seq0:e3" }],
        };
        await controller.revealPendingTarget();
      };

      await controller.revealCurrentLineInTimeline();

      assert.deepEqual(posted[0], { type: "highlightEventIds", highlightEventIds: ["seq0:e3"] });
      controller.dispose();
    });
  });

  it("highlights the timeline block when a related Q1ASM editor becomes active", async () => {
    const vscode = createMockVscode();
    const posted: any[] = [];
    let activeEditorHandler: ((editor: any) => void) | undefined;
    const sourcePath = "C:\\repo\\q1asm\\seq0.q1asm";
    const editor = {
      document: { uri: vscode.Uri.file(sourcePath) },
      selection: { active: { line: 2 } },
      viewColumn: 2,
    };
    vscode.window.onDidChangeActiveTextEditor = (handler: (editor: any) => void) => {
      activeEditorHandler = handler;
      return { dispose: () => {} };
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.projectRelatedPaths = new Set([sourcePath]);
      controller.panel = { webview: { postMessage: (message: any) => posted.push(message) }, dispose: () => {} };
      controller.timelineIr = {
        source_map: {
          by_source: { "q1asm/seq0.q1asm:3": ["seq0:e3"] },
        },
        events: [{ id: "seq0:e3" }],
      };

      activeEditorHandler?.(editor);

      assert.deepEqual(posted[0], { type: "highlightEventIds", highlightEventIds: ["seq0:e3"] });
      controller.dispose();
    });
  });

  it("rediscovers the active project before revealing the current line in an existing preview", async () => {
    const vscode = createMockVscode();
    vscode.window.activeTextEditor = {
      document: { uri: vscode.Uri.file("C:\\repo\\project-b\\q1asm\\seq0.q1asm") },
      selection: { active: { line: 4 } },
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\project-a\\q1timeline.yml");
      controller.panel = { webview: { postMessage: () => {} }, reveal: () => {}, dispose: () => {} };
      controller.timelineIr = {
        source_map: {
          by_source: { "q1asm/seq0.q1asm:5": ["old:e5"] },
        },
        events: [{ id: "old:e5" }],
      };
      controller.findProjectFileUpward = () => vscode.Uri.file("C:\\repo\\project-b\\q1timeline.yml");
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      let analysisRan = false;
      controller.runAnalysis = async () => {
        analysisRan = true;
      };

      await controller.revealCurrentLineInTimeline();

      assert.equal(controller.projectUri.fsPath, "C:\\repo\\project-b\\q1timeline.yml");
      assert.equal(controller.pendingTarget?.q1asmFile, "C:\\repo\\project-b\\q1asm\\seq0.q1asm");
      assert.equal(controller.pendingTarget?.line, 5);
      assert.equal(analysisRan, true);
      controller.dispose();
    });
  });

  it("keeps explicit open targets queued while a fresher analysis is pending", async () => {
    const vscode = createMockVscode();
    const posted: any[] = [];

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.outputDir = "C:\\repo\\.q1timeline";
      controller.panel = { webview: { postMessage: (message: any) => posted.push(message) }, reveal: () => {}, dispose: () => {} };
      controller.timelineIr = {
        source_map: {
          by_source: { "q1asm/seq0.q1asm:3": ["old:e3"] },
        },
        events: [{ id: "old:e3" }],
      };
      controller.analysisInFlight = true;

      await controller.openTarget({
        projectFile: "C:\\repo\\q1timeline.yml",
        q1asmFile: "q1asm/seq0.q1asm",
        sequencer: "seq0",
        line: 3,
      });

      assert.equal(posted.some((message) => message.type === "highlightEventIds"), false);
      assert.equal(controller.pendingTarget?.line, 3);
      assert.equal(controller.analysisQueued, true);
      controller.dispose();
    });
  });

  it("refreshes related paths for case-equivalent project watcher events", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\Repo\\q1timeline.yml");
      controller.projectRelatedPaths = new Set(["C:\\Repo\\old.q1asm"]);
      controller.runAnalysis = () => undefined;
      let refreshed = false;
      controller.refreshProjectRelatedPaths = async () => {
        refreshed = true;
        controller.projectRelatedPaths = new Set(["C:\\Repo\\new.q1asm"]);
      };

      await controller.onWatchedFile(vscode.Uri.file("c:\\repo\\q1timeline.yml"));

      assert.equal(refreshed, true);
      assert.equal(controller.isProjectRelated(vscode.Uri.file("C:\\Repo\\new.q1asm")), true);
      controller.dispose();
    });
  });

  it("offers a fresh quick fix for saved source changed before the analyzer launches", async () => {
    const vscode = createMockVscode();
    vscode.CodeAction = class { constructor(public title, public kind) {} };
    vscode.CodeActionKind = { QuickFix: "quickfix" };
    vscode.WorkspaceEdit = class { replace() {} };
    let published: any[] = [];
    vscode.languages.createDiagnosticCollection = () => ({
      clear() { published = []; }, set(_uri, diagnostics) { published = diagnostics; }, dispose() {},
    });
    const notifications = sourceFileNotifications(vscode);
    let releaseEnvironment!: () => void;
    let environmentStarted!: () => void;
    const waiting = new Promise<void>((resolve) => { environmentStarted = resolve; });
    await withControllerFileEnvironment(vscode, async (executable) => {
      await new Promise<void>((resolve) => { releaseEnvironment = resolve; environmentStarted(); });
      return { executable, version: "3.12.0", supported: true, q1lensInstalled: true };
    }, async ({ TimelineController }) => {
      const setup = sourceFileController(TimelineController, vscode);
      const { controller, document, sourcePath, calls } = setup;
      controller.config = () => ({ get: (key, fallback) => key === "updateMode" ? "manual" : key === "hardwareReadiness" ? true : fallback });
      controller.refreshWebview = TimelineController.prototype.refreshWebview.bind(controller);
      document.languageId = "q1asm";
      document.uri.scheme = "file";
      Object.defineProperty(document, "lineCount", { get: () => document.text.split("\n").length });
      document.lineAt = (line) => ({ text: document.text.split("\n")[line] });
      const corrected = "set_freq 100\nupd_param 4\nstop\n", pending = "set_freq 100\nwait 4\nstop\n";
      const repoRoot = require("node:path").resolve(__dirname, "../../../..");
      const analyze = (source) => JSON.parse(require("node:child_process").execFileSync("python", ["-c",
        "import json,sys; from q1timeline.analysis.api import analyze_text; print(json.dumps(analyze_text(sys.argv[1],file=sys.argv[2],hardware_readiness=True)))",
        source, sourcePath,
      ], { cwd: repoRoot, env: { ...process.env, PYTHONPATH: join(repoRoot, "src") }, encoding: "utf8", windowsHide: true }));
      assert.ok(!analyze(corrected).diagnostics.some((item) => item.category === "pending_hardware_state"));
      const execute = controller.execFile.bind(controller);
      let analyzedText: string | undefined;
      controller.execFile = async (...args) => {
        const result = await execute(...args);
        if (args[5] === "analyzer") {
          analyzedText = readFileSync(sourcePath, "utf8");
          const ir = analyze(analyzedText);
          writeFileSync(args[1][args[1].indexOf("--out") + 1], JSON.stringify(ir));
          writeFileSync(args[1][args[1].indexOf("--diagnostics") + 1], JSON.stringify(ir.diagnostics));
        }
        return result;
      };
      let opening: Promise<void> | undefined;
      try {
        document.text = corrected;
        writeFileSync(sourcePath, corrected);
        writeFileSync(controller.projectUri.fsPath, "sequencers:\n  - id: seq0\n    file: seq0.q1asm\n");
        opening = controller.openTarget({ projectFile: controller.projectUri.fsPath });
        await waiting;
        document.text = pending;
        document.version += 1;
        writeFileSync(sourcePath, pending);
        await notifications.save(document);
        assert.equal(calls.filter((call) => call.stage === "analyzer").length, 0, "Environment preflight still precedes the analyzer's source read");
        releaseEnvironment();
        await opening; await setup.settle();
        assert.equal(analyzedText, pending);
        assert.equal(controller.analysisStatus.status, "idle");
        assert.ok(published.some((item) => item.code === "pending_hardware_state"));
        const { Q1asmQuickFixProvider } = require("../../src/q1timeline/q1asmQuickFix");
        const provider = new Q1asmQuickFixProvider(vscode);
        const range = new vscode.Range(2, 0, 2, 4);
        assert.equal(provider.provideCodeActions(document, range, { diagnostics: published }).length, 1,
          "The analyzer consumed the saved source, so its pending-state insertion is fresh");
        const transported = published.map((diagnostic) => Object.assign(new vscode.Diagnostic(diagnostic.range, diagnostic.message, diagnostic.severity), {
          source: diagnostic.source, code: diagnostic.code,
        }));
        assert.equal(provider.provideCodeActions(document, range, { diagnostics: transported }).length, 1);
        assert.equal(controller.sourceRevision, controller.analyzedSourceRevision);
      } finally { releaseEnvironment?.(); await opening; await setup.settle(); controller.dispose(); }
    });
  });

  for (const stage of ["late analyzer completion", "late analyzer failure", "stale view rerender"]) {
    it(`keeps Problems diagnostics without restoring invalidated quick fixes after ${stage}`, async () => {
      const vscode = createMockVscode();
      vscode.CodeAction = class { constructor(public title, public kind) {} };
      vscode.CodeActionKind = { QuickFix: "quickfix" };
      vscode.WorkspaceEdit = class { replace() {} };
      let published: any[] = [];
      vscode.languages.createDiagnosticCollection = () => ({
        clear() { published = []; }, set(_uri, diagnostics) { published = diagnostics; }, dispose() {},
      });
      const { Q1asmQuickFixProvider } = require("../../src/q1timeline/q1asmQuickFix");
      const provider = new Q1asmQuickFixProvider(vscode);
      const notifications = sourceFileNotifications(vscode);
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const setup = sourceFileController(TimelineController, vscode);
        const { controller, document, sourcePath } = setup;
        controller.config = () => ({ get: (key, fallback) => key === "updateMode" ? "manual" : fallback });
        controller.refreshWebview = TimelineController.prototype.refreshWebview.bind(controller);
        document.languageId = "q1asm";
        document.uri.scheme = "file";
        Object.defineProperty(document, "lineCount", { get: () => document.text.split("\n").length });
        document.lineAt = (line) => ({ text: document.text.split("\n")[line] });
        const before = "set_freq 100\nwait 4\nstop\n", after = "set_freq 100\nupd_param 4\nstop\n";
        const repoRoot = require("node:path").resolve(__dirname, "../../../..");
        const analyze = (source) => JSON.parse(require("node:child_process").execFileSync("python", ["-c",
          "import json,sys; from q1timeline.analysis.api import analyze_text; print(json.dumps(analyze_text(sys.argv[1],file=sys.argv[2],hardware_readiness=True)))",
          source, sourcePath,
        ], { cwd: repoRoot, env: { ...process.env, PYTHONPATH: join(repoRoot, "src") }, encoding: "utf8", windowsHide: true }));
        const beforeIr = analyze(before), afterIr = analyze(after);
        assert.ok(beforeIr.diagnostics.some((item) => item.category === "pending_hardware_state"));
        assert.ok(!afterIr.diagnostics.some((item) => item.category === "pending_hardware_state"));
        const actions = (diagnostics) => provider.provideCodeActions(document, new vscode.Range(2, 0, 2, 4), { diagnostics });
        const transported = (diagnostic) => Object.assign(new vscode.Diagnostic(diagnostic.range, diagnostic.message, diagnostic.severity), {
          source: diagnostic.source, code: diagnostic.code,
        });
        let gateNextAnalyzer = false;
        let release!: () => void;
        let started!: () => void;
        const waiting = new Promise<void>((resolve) => { started = resolve; });
        const execute = controller.execFile.bind(controller);
        controller.execFile = async (...args) => {
          const gated = args[5] === "analyzer" && gateNextAnalyzer;
          const ir = document.text === before ? beforeIr : afterIr;
          if (gated) {
            gateNextAnalyzer = false;
            await new Promise<void>((resolve) => { release = resolve; started(); });
          }
          const result = await execute(...args);
          if (args[5] === "analyzer") {
            writeFileSync(args[1][args[1].indexOf("--out") + 1], JSON.stringify(ir));
            writeFileSync(args[1][args[1].indexOf("--diagnostics") + 1], JSON.stringify(ir.diagnostics));
            if (gated && stage === "late analyzer failure") throw Object.assign(new Error("Analyzer stopped"), {
              stdout: JSON.stringify({ schema_version: "0.2.0", status: "error", diagnostics: ir.diagnostics }), stderr: "", stage: "analyzer",
            });
          }
          return result;
        };
        let pending: Promise<void> | undefined;
        try {
          document.text = before;
          writeFileSync(sourcePath, before);
          writeFileSync(controller.projectUri.fsPath, "sequencers:\n  - id: seq0\n    file: seq0.q1asm\n");
          await controller.openTarget({ projectFile: controller.projectUri.fsPath });
          assert.equal(actions(published).length, 1);
          const initialDiagnostics = [published[0], transported(published[0])];
          if (stage !== "stale view rerender") {
            gateNextAnalyzer = true;
            pending = controller.refreshPreview();
            await waiting;
          }
          document.text = after;
          document.version += 1;
          writeFileSync(sourcePath, after);
          await notifications.save(document);
          for (const diagnostic of initialDiagnostics) assert.deepEqual(actions([diagnostic]), [], "Source invalidation withdraws retained suggestions immediately");
          release?.();
          await pending;
          await setup.settle();
          if (stage === "stale view rerender") await controller.setViewMode("debug");
          assert.ok(published.some((item) => item.code === "pending_hardware_state"), "The stale warning remains visible in Problems until Refresh");
          assert.ok(controller.analyzerDiagnostics.length > 0);
          for (const diagnostic of [...initialDiagnostics, ...published, ...published.map(transported)]) {
            assert.deepEqual(actions([diagnostic]), [], "Neither retained diagnostics nor late output may restore an obsolete insertion");
          }
          if (stage === "stale view rerender") assert.ok(controller.sourceRevision > controller.analyzedSourceRevision);
          document.text = before;
          document.version += 1;
          writeFileSync(sourcePath, before);
          await notifications.save(document);
          await controller.refreshPreview();
          assert.equal(controller.sourceRevision, controller.analyzedSourceRevision);
          assert.equal(actions(published).length, 1, "Fresh analysis can publish a valid suggestion again");
        } finally {
          release?.(); await pending; await setup.settle(); controller.dispose();
        }
      });
    });
  }

  it("clears stale preview HTML when analyzer failure handling runs", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const webview = { html: "<html>old event</html>" };
      controller.panel = { webview, dispose: () => {} };
      controller.timelineIr = { events: [{ id: "old" }] };
      controller.lastPreviewHtml = "<html>old event</html>";

      controller.clearPreviewAfterAnalyzerFailure("Analyzer failed.");

      assert.equal(controller.timelineIr, undefined);
      assert.equal(controller.lastPreviewHtml, undefined);
      assert.equal(webview.html.includes("old event"), false);
      controller.dispose();
    });
  });

  for (const stage of ["timeline_ir.json", "diagnostics.json", "timeline.html"]) {
    it(`does not publish a previous project's preview after a delayed ${stage} read`, async () => {
      const vscode = createMockVscode();
      const published: any[] = [];
      vscode.languages.createDiagnosticCollection = () => ({
        clear() { published.push("clear"); },
        set(uri, diagnostics) { published.push({ uri, diagnostics }); },
        dispose() {},
      });
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const { controller } = fileEnvironmentController(TimelineController, vscode);
        controller.refreshWebview = TimelineController.prototype.refreshWebview.bind(controller);
        const outputA = controller.outputDir;
        writeFileSync(join(outputA, "timeline_ir.json"), JSON.stringify({ events: [{ id: "A" }] }));
        writeFileSync(join(outputA, "diagnostics.json"), JSON.stringify([
          { severity: "error", message: "Project A error", source: { file: "a.q1asm", line: 1 } },
        ]));
        writeFileSync(join(outputA, "timeline.html"), "<html><head></head><body>Project A</body></html>");
        const fsPromises = require("node:fs").promises;
        const original = fsPromises.readFile;
        let release!: () => void;
        let started!: () => void;
        const waiting = new Promise<void>((resolve) => { started = resolve; });
        fsPromises.readFile = async (...args) => {
          if (args[0] === join(outputA, stage)) await new Promise<void>((resolve) => { release = resolve; started(); });
          return original(...args);
        };
        try {
          const refreshing = controller.refreshWebview();
          await waiting;
          const outputB = mkdtempSync(join(tmpdir(), "q1-publish-project-b-"));
          controller.setProjectUri(vscode.Uri.file(join(outputB, "q1timeline.yml")));
          controller.outputDir = outputB;
          const irB = { events: [{ id: "B" }] };
          const diagnosticsB = [{ message: "Project B error", severity: "error" }];
          controller.timelineIr = irB;
          controller.analyzerDiagnostics = diagnosticsB;
          controller.lastPreviewHtml = "Project B";
          controller.panel.webview.html = "Project B";
          published.length = 0;
          release();
          await refreshing;
          assert.deepEqual(controller.timelineIr, irB);
          assert.deepEqual(controller.analyzerDiagnostics, diagnosticsB);
          assert.equal(controller.lastPreviewHtml, "Project B");
          assert.equal(controller.panel.webview.html, "Project B");
          assert.deepEqual(published, [], "Stale loads must not clear or replace the new project's Problems entries");
        } finally {
          fsPromises.readFile = original;
          controller.dispose();
        }
      });
    });
  }

  it("drops a previous project's failure prompt after delayed diagnostic inspection", async () => {
    const vscode = createMockVscode();
    const notifications: string[] = [];
    vscode.window.showErrorMessage = async (message) => { notifications.push(message); };
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const { controller } = fileEnvironmentController(TimelineController, vscode);
      controller.execFile = async () => { throw new Error("No module named 'q1lens'"); };
      let release!: () => void;
      let started!: () => void;
      const waiting = new Promise<void>((resolve) => { started = resolve; });
      controller.hasAnalyzerDiagnostics = async () => new Promise<boolean>((resolve) => {
        release = () => resolve(false);
        started();
      });
      const analysis = controller.runAnalysis();
      await waiting;
      controller.setProjectUri(vscode.Uri.file(join(tmpdir(), "replacement-project", "q1timeline.yml")));
      const diagnosticsB = [{ message: "Project B error", severity: "error" }];
      controller.analyzerDiagnostics = diagnosticsB;
      release();
      await analysis;
      assert.deepEqual(notifications, [], "An old project's failure must not offer recovery for the newly selected project");
      assert.deepEqual(controller.analyzerDiagnostics, diagnosticsB);
      controller.dispose();
    });
  });

  it("drops an old analyzer failure while opening a source without a project", async () => {
    const vscode = createMockVscode();
    const notifications: string[] = [];
    vscode.window.showErrorMessage = async (message) => { notifications.push(message); };
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const { controller } = fileEnvironmentController(TimelineController, vscode);
      let failAnalyzer!: () => void;
      let analyzerStarted!: () => void;
      const analyzing = new Promise<void>((resolve) => { analyzerStarted = resolve; });
      const execFile = controller.execFile;
      let first = true;
      controller.execFile = async (...args) => {
        if (first) {
          first = false;
          await new Promise<void>((resolve) => { failAnalyzer = resolve; analyzerStarted(); });
          throw new Error("No module named q1lens");
        }
        return execFile(...args);
      };
      const analysisA = controller.runAnalysis();
      await analyzing;
      const folderB = mkdtempSync(join(tmpdir(), "q1-fallback-before-failure-"));
      const sourceB = vscode.Uri.file(join(folderB, "b.q1asm"));
      writeFileSync(sourceB.fsPath, "stop\n");
      const fsPromises = require("node:fs").promises;
      const originalMkdir = fsPromises.mkdir;
      let releaseFallback!: () => void;
      let fallbackStarted!: () => void;
      const preparing = new Promise<void>((resolve) => { fallbackStarted = resolve; });
      fsPromises.mkdir = async (...args) => {
        if (args[0] === join(folderB, ".q1timeline")) await new Promise<void>((resolve) => {
          releaseFallback = resolve;
          fallbackStarted();
        });
        return originalMkdir(...args);
      };
      const openingB = controller.openPreview({ sourceUri: sourceB });
      try {
        await preparing;
        assert.equal(controller.projectUri, undefined, "The new source is waiting for its generated project");
        failAnalyzer();
        await assert.doesNotReject(analysisA, "The old failure must be discarded before diagnostic publication");
        assert.deepEqual(notifications, []);
      } finally {
        fsPromises.mkdir = originalMkdir;
        releaseFallback();
        await openingB;
        controller.dispose();
      }
    });
  });

  it("does not reuse cached HTML when timeline.html is missing", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-missing-html-"));
    writeFileSync(join(outputDir, "timeline_ir.json"), "{\"events\":[]}", "utf8");
    writeFileSync(join(outputDir, "diagnostics.json"), "[]", "utf8");

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const webview = {
        html: "",
        cspSource: "vscode-resource:",
        asWebviewUri: (uri: any) => uri,
        postMessage: () => {},
      };
      controller.projectUri = vscode.Uri.file(join(outputDir, "q1timeline.yml"));
      controller.outputDir = outputDir;
      controller.lastPreviewHtml = "<html>old project</html>";
      controller.panel = { webview, dispose: () => {} };

      await controller.refreshWebview();

      assert.equal(webview.html.includes("old project"), false);
      assert.equal(webview.html.includes("No timeline.html generated."), true);
      controller.dispose();
    });
  });

  it("clears stale standalone analyzer outputs before running analysis", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-stale-outputs-"));
    writeFileSync(join(outputDir, "timeline_ir.json"), "{\"events\":[{\"id\":\"old\"}]}", "utf8");
    writeFileSync(join(outputDir, "diagnostics.json"), "[]", "utf8");
    writeFileSync(join(outputDir, "timeline.html"), "<html>old</html>", "utf8");

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file(join(outputDir, "q1timeline.yml"));
      controller.outputDir = outputDir;
      controller.execFile = async () => ({ stdout: "{}", stderr: "", exitCode: 0, elapsedMs: 1 });
      controller.runRender = async () => ({ stdout: "", stderr: "", exitCode: 0, elapsedMs: 1 });
      controller.refreshWebview = async () => undefined;

      await controller.runAnalysis();

      const fs = require("node:fs");
      assert.equal(fs.existsSync(join(outputDir, "timeline_ir.json")), false);
      assert.equal(fs.existsSync(join(outputDir, "diagnostics.json")), false);
      assert.equal(fs.existsSync(join(outputDir, "timeline.html")), false);
      controller.dispose();
    });
  });

  it("adds hardware readiness plus branch and loop overrides to analyzer args", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-control-overrides-"));
    const capturedArgs: string[][] = [];

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file(join(outputDir, "q1timeline.yml"));
      controller.outputDir = outputDir;
      controller.config = () => ({
        get: (key: string, fallback: any) => {
          if (key === "analyzer.extraArgs") {
            return ["--strict"];
          }
          if (key === "hardwareReadiness") {
            return true;
          }
          return fallback;
        },
      });
      controller.execFile = async (_pythonPath: string, args: string[]) => {
        capturedArgs.push(args);
        return { stdout: "{}", stderr: "", exitCode: 0, elapsedMs: 1 };
      };
      controller.runRender = async () => ({ stdout: "", stderr: "", exitCode: 0, elapsedMs: 1 });
      controller.refreshWebview = async () => undefined;

      await controller.handleWebviewMessage({
        type: "setBranchAssumption",
        branchId: "seq0:branch:main.q1asm:4:jge:target",
        path: "both",
      });
      await controller.handleWebviewMessage({
        type: "setLoopPreview",
        loopKey: "seq0:loop:main.q1asm:2-8",
        visibleIterations: 3,
      });

      const latestArgs = capturedArgs[capturedArgs.length - 1];
      assert.ok(latestArgs);
      assert.ok(latestArgs.includes("--hardware-readiness"));
      assert.ok(latestArgs.indexOf("--hardware-readiness") < latestArgs.indexOf("--branch-assumption"));
      assert.deepEqual(
        latestArgs.slice(latestArgs.indexOf("--branch-assumption"), latestArgs.indexOf("--strict")),
        [
          "--branch-assumption",
          "seq0:branch:main.q1asm:4:jge:target=both",
          "--loop-preview",
          "seq0:loop:main.q1asm:2-8=3",
        ],
      );
      controller.dispose();
    });
  });

  it("sets, replaces, displays, and clears analysis parameter overrides", async () => {
    const vscode = createMockVscode();
    const inputs = ["scenario.duration", "24", "scenario.duration", "32"];
    const notifications: string[] = [];
    const outputLines: string[] = [];
    vscode.window.showInputBox = async () => inputs.shift();
    vscode.window.showInformationMessage = async (message: string) => {
      notifications.push(message);
    };
    vscode.window.createOutputChannel = () => ({
      appendLine: (message: string) => outputLines.push(message),
      show: () => {},
      dispose: () => {},
    });

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.outputDir = "C:\\repo\\.q1timeline";
      controller.branchAssumptionOverrides.set("branch", "taken");
      controller.loopPreviewOverrides.set("loop", 2);
      let runCount = 0;
      controller.runAnalysis = async () => {
        runCount += 1;
      };

      await controller.setAnalysisParameter();
      await controller.setAnalysisParameter();

      assert.equal(runCount, 2);
      assert.deepEqual(controller.analyzerOverrideArgs(), [
        "--branch-assumption",
        "branch=taken",
        "--loop-preview",
        "loop=2",
        "--set-param",
        "scenario.duration=32",
      ]);
      assert.ok(notifications.at(-1)?.includes("scenario.duration=32"));
      assert.ok(outputLines.some((line) => line.includes("scenario.duration=32")));

      await controller.clearAnalysisParameterOverrides();

      assert.equal(runCount, 3);
      assert.equal(controller.analyzerOverrideArgs().includes("--set-param"), false);
      assert.ok(notifications.at(-1)?.includes("cleared"));
      controller.dispose();
    });
  });

  it("does not rerun analysis for cancelled or invalid parameter input", async () => {
    const vscode = createMockVscode();
    const errors: string[] = [];
    vscode.window.showErrorMessage = async (message: string) => {
      errors.push(message);
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.outputDir = "C:\\repo\\.q1timeline";
      let runCount = 0;
      controller.runAnalysis = async () => {
        runCount += 1;
      };

      vscode.window.showInputBox = async () => undefined;
      await controller.setAnalysisParameter();

      const invalidInputs = ["DURATION", "true"];
      vscode.window.showInputBox = async () => invalidInputs.shift();
      await controller.setAnalysisParameter();

      assert.equal(runCount, 0);
      assert.deepEqual(controller.analyzerOverrideArgs(), []);
      assert.ok(errors.at(-1)?.includes("Boolean JSON values"));
      controller.dispose();
    });
  });

  for (const pendingInput of [1, 2]) {
    it(`drops analysis parameter input after switching projects during the ${pendingInput === 1 ? "name" : "value"} prompt`, async () => {
      const vscode = createMockVscode();
      const notifications: string[] = [];
      vscode.window.showInformationMessage = async (message) => { notifications.push(message); };
      let release!: () => void;
      let started!: () => void;
      const waiting = new Promise<void>((resolve) => { started = resolve; });
      let inputCount = 0;
      vscode.window.showInputBox = async () => {
        const currentInput = ++inputCount;
        if (currentInput === pendingInput) {
          await new Promise<void>((resolve) => { release = resolve; started(); });
        }
        return currentInput === 1 ? "DURATION_A" : "64";
      };
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const controller = new TimelineController(createContext());
        controller.setProjectUri(vscode.Uri.file("C:\\repo\\project-a\\q1timeline.yml"));
        controller.outputDir = "C:\\repo\\project-a\\.q1timeline";
        let runCount = 0;
        controller.runAnalysis = async () => { runCount += 1; };
        const entering = controller.setAnalysisParameter();
        await waiting;
        controller.setProjectUri(vscode.Uri.file("C:\\repo\\project-b\\q1timeline.yml"));
        controller.outputDir = "C:\\repo\\project-b\\.q1timeline";
        controller.parameterOverrides.set("DURATION_B", "96");
        const baselineB = { events: [{ id: "B" }] };
        controller.lastAnalyzerTimelineIr = baselineB;
        release();
        await entering;
        assert.deepEqual([...controller.parameterOverrides], [["DURATION_B", "96"]]);
        assert.equal(controller.lastAnalyzerTimelineIr, baselineB);
        assert.equal(runCount, 0);
        assert.equal(inputCount, pendingInput, "Switching projects must stop the remaining old-project prompts");
        assert.deepEqual(notifications, []);
        controller.dispose();
      });
    });
  }

  it("applies exact inline JSON parameter values and exposes session ownership to the webview", async () => {
    const vscode = createMockVscode();
    vscode.window.showInputBox = async () => assert.fail("Inline edits must not open an input box");

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const projectFile = "C:\\repo\\q1timeline.yml";
      controller.projectUri = vscode.Uri.file(projectFile);
      controller.outputDir = "C:\\repo\\.q1timeline";
      controller.timelineIr = { analysis: { parameters: [{ name: "DURATION" }] } };
      controller.branchAssumptionOverrides.set("branch", "taken");
      controller.loopPreviewOverrides.set("loop", 2);
      const baselinesDuringRun: any[] = [];
      controller.runAnalysis = async () => baselinesDuringRun.push(controller.lastAnalyzerTimelineIr);

      for (const valueJson of [" 128 ", "9007199254740993", "null", '{"nested":[4,"text",null,{"large":9007199254740993}]}']) {
        controller.lastAnalyzerTimelineIr = { events: [] };
        await controller.handleWebviewMessage({ type: "setAnalysisParameter", name: "DURATION", valueJson, projectFile });
        assert.equal(controller.parameterOverrides.get("DURATION"), valueJson.trim());
      }

      assert.deepEqual(baselinesDuringRun, [undefined, undefined, undefined, undefined]);
      assert.equal(controller.parameterOverrides.size, 1);
      assert.equal(controller.branchAssumptionOverrides.get("branch"), "taken");
      assert.equal(controller.loopPreviewOverrides.get("loop"), 2);
      const state = controller.webviewInitialState();
      assert.equal(state.parameterProjectFile, projectFile);
      assert.deepEqual(state.parameterOverrideNames, ["DURATION"]);
      assert.deepEqual(controller.analyzerOverrideArgs().slice(-2), [
        "--set-param", 'DURATION={"nested":[4,"text",null,{"large":9007199254740993}]}',
      ]);
      controller.dispose();
    });
  });

  it("reports invalid inline JSON without mutating overrides or rerunning analysis", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const projectFile = "C:\\repo\\q1timeline.yml";
      controller.projectUri = vscode.Uri.file(projectFile);
      controller.outputDir = "C:\\repo\\.q1timeline";
      controller.timelineIr = { analysis: { parameters: [{ name: "DURATION" }] } };
      controller.parameterOverrides.set("DURATION", "128");
      const messages: any[] = [];
      controller.postWebviewMessage = (message: any) => messages.push(message);
      controller.runAnalysis = async () => assert.fail("Invalid edits must not rerun analysis");

      for (const valueJson of ["", " ", "1.5", "true", "false", '{"nested":[false]}', "[1,2.5]", "broken", "NaN", "Infinity"]) {
        await controller.handleWebviewMessage({ type: "setAnalysisParameter", name: "DURATION", valueJson, projectFile });
        assert.equal(controller.parameterOverrides.get("DURATION"), "128");
        assert.equal(messages.at(-1).type, "analysisParameterError");
        assert.equal(messages.at(-1).name, "DURATION");
        assert.ok(messages.at(-1).message);
      }
      assert.equal(messages.length, 10);
      controller.dispose();
    });
  });

  it("rejects stale projects, unavailable outputs, and unknown inline parameter names", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const projectFile = "C:\\repo\\project-b\\q1timeline.yml";
      controller.projectUri = vscode.Uri.file(projectFile);
      controller.outputDir = "C:\\repo\\project-b\\.q1timeline";
      controller.timelineIr = { analysis: { parameters: [{ name: "DURATION" }] } };
      controller.parameterOverrides.set("DURATION", "128");
      const messages: any[] = [];
      controller.postWebviewMessage = (message: any) => messages.push(message);
      controller.runAnalysis = async () => assert.fail("Stale edits must not rerun analysis");
      const edit = { type: "setAnalysisParameter", name: "DURATION", valueJson: "64", projectFile };

      await controller.handleWebviewMessage({ ...edit, projectFile: "C:\\repo\\project-a\\q1timeline.yml" });
      await controller.handleWebviewMessage({ type: "resetAnalysisParameter", name: "DURATION", projectFile: "C:\\repo\\project-a\\q1timeline.yml" });
      await controller.handleWebviewMessage({ ...edit, name: "UNKNOWN" });
      controller.outputDir = undefined;
      await controller.handleWebviewMessage(edit);
      controller.projectUri = undefined;
      await controller.handleWebviewMessage(edit);

      assert.deepEqual(Array.from(controller.parameterOverrides), [["DURATION", "128"]]);
      assert.equal(messages.length, 5);
      assert.ok(messages.every((message) => message.type === "analysisParameterError"));
      assert.equal(controller.webviewInitialState().parameterProjectFile, undefined);
      controller.dispose();
    });
  });

  it("resets only the selected session override and rejects resets of configured values", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const projectFile = "C:\\repo\\q1timeline.yml";
      controller.projectUri = vscode.Uri.file(projectFile);
      controller.outputDir = "C:\\repo\\.q1timeline";
      controller.timelineIr = { analysis: { parameters: [{ name: "DURATION" }, { name: "WAIT" }] } };
      controller.parameterOverrides.set("DURATION", "128");
      controller.parameterOverrides.set("WAIT", "96");
      controller.branchAssumptionOverrides.set("branch", "taken");
      controller.loopPreviewOverrides.set("loop", 2);
      controller.lastAnalyzerTimelineIr = { events: [] };
      let runCount = 0;
      controller.runAnalysis = async () => { runCount += 1; };
      const messages: any[] = [];
      controller.postWebviewMessage = (message: any) => messages.push(message);
      const reset = { type: "resetAnalysisParameter", name: "DURATION", projectFile };

      await controller.handleWebviewMessage(reset);
      assert.equal(runCount, 1);
      assert.equal(controller.lastAnalyzerTimelineIr, undefined);
      assert.deepEqual(Array.from(controller.parameterOverrides), [["WAIT", "96"]]);
      assert.equal(controller.branchAssumptionOverrides.get("branch"), "taken");
      assert.equal(controller.loopPreviewOverrides.get("loop"), 2);
      assert.deepEqual(controller.webviewInitialState().parameterOverrideNames, ["WAIT"]);
      await controller.handleWebviewMessage(reset);
      assert.equal(runCount, 1);
      assert.equal(messages.at(-1).type, "analysisParameterError");
      assert.match(messages.at(-1).message, /no temporary override/);
      controller.dispose();
    });
  });

  it("emits session parameter edits after configured analyzer overrides", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-inline-param-"));
    let capturedArgs: string[] = [];

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const projectFile = join(outputDir, "q1timeline.yml");
      controller.projectUri = vscode.Uri.file(projectFile);
      controller.outputDir = outputDir;
      controller.timelineIr = { analysis: { parameters: [{ name: "DURATION" }] } };
      controller.branchAssumptionOverrides.set("branch", "taken");
      controller.loopPreviewOverrides.set("loop", 2);
      controller.config = () => ({
        get: (key: string, fallback: any) => key === "analyzer.extraArgs" ? ["--set-param", "DURATION=64", "--strict"] : fallback,
      });
      controller.execFile = async (_pythonPath: string, args: string[]) => {
        capturedArgs = args;
        return { stdout: "{}", stderr: "", exitCode: 0, elapsedMs: 1 };
      };
      controller.runRender = async () => ({ stdout: "", stderr: "", exitCode: 0, elapsedMs: 1 });
      controller.refreshWebview = async () => undefined;

      await controller.handleWebviewMessage({ type: "setAnalysisParameter", name: "DURATION", valueJson: "128", projectFile });

      assert.deepEqual(capturedArgs.slice(capturedArgs.indexOf("--branch-assumption")), [
        "--branch-assumption", "branch=taken", "--loop-preview", "loop=2",
        "--set-param", "DURATION=64", "--strict", "--set-param", "DURATION=128",
      ]);
      controller.dispose();
    });
  });

  it("rejects inline parameter edits while an analysis is already running", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const projectFile = "C:\\repo\\q1timeline.yml";
      controller.projectUri = vscode.Uri.file(projectFile);
      controller.outputDir = "C:\\repo\\.q1timeline";
      controller.timelineIr = { analysis: { parameters: [{ name: "DURATION" }] } };
      controller.analysisInFlight = true;
      controller.parameterOverrides.set("DURATION", "128");
      const messages: any[] = [];
      controller.postWebviewMessage = (message: any) => messages.push(message);
      controller.runAnalysis = async () => assert.fail("Busy edits must not queue analysis");

      await controller.handleWebviewMessage({ type: "setAnalysisParameter", name: "DURATION", valueJson: "256", projectFile });
      await controller.handleWebviewMessage({ type: "resetAnalysisParameter", name: "DURATION", projectFile });

      assert.equal(controller.parameterOverrides.get("DURATION"), "128");
      assert.equal(controller.analysisQueued, false);
      assert.equal(messages.length, 2);
      assert.ok(messages.every((message) => message.type === "analysisParameterError" && /still running/.test(message.message)));
      controller.dispose();
    });
  });

  for (const action of ["setAnalysisParameter", "resetAnalysisParameter"]) {
    it(`rolls back ${action} when Python environment setup is cancelled and permits retry`, async () => {
      const vscode = createMockVscode();
      let available = false;
      vscode.resolveFileEnvironment = async (_api, resource) => available ? resolvedPython(resource) : undefined;
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const { controller, calls } = fileEnvironmentController(TimelineController, vscode);
        const projectFile = controller.projectUri.fsPath;
        const previousIr = { analysis: { parameters: [{ name: "DURATION" }] }, events: [{ id: "previous" }] };
        controller.timelineIr = previousIr;
        controller.lastAnalyzerTimelineIr = previousIr;
        controller.lastPreviewHtml = controller.panel.webview.html = "previous editable preview";
        controller.parameterOverrides.set("DURATION", "128");
        const messages: any[] = [];
        controller.panel.webview.postMessage = (message) => messages.push(message);
        const request = { type: action, name: "DURATION", projectFile,
          ...(action === "setAnalysisParameter" ? { valueJson: "256" } : {}) };

        await controller.handleWebviewMessage(request);

        assert.equal(controller.parameterOverrides.get("DURATION"), "128");
        assert.equal(controller.lastAnalyzerTimelineIr, previousIr);
        assert.equal(controller.timelineIr, previousIr);
        assert.equal(controller.panel.webview.html, "previous editable preview");
        assert.equal(controller.analysisInFlight, false);
        assert.equal(controller.analysisStatus.status, "error");
        assert.deepEqual(calls, [], "Cancelled environment setup must not start Python");
        const errors = messages.filter((message) => message.type === "analysisParameterError");
        assert.equal(errors.length, 1, "The existing webview needs an error reply to restore its editing controls");
        assert.equal(errors[0].name, "DURATION");
        assert.match(errors[0].message, /Python environment setup is required/);

        available = true;
        await controller.handleWebviewMessage(request);

        assert.equal(controller.parameterOverrides.get("DURATION"), action === "setAnalysisParameter" ? "256" : undefined);
        assert.deepEqual(calls.map((call) => call.stage), ["analyzer", "renderer"]);
        assert.equal(controller.analysisStatus.status, "idle");
        assert.equal(messages.filter((message) => message.type === "analysisParameterError").length, 1);
        controller.dispose();
      });
    });
  }

  it("rolls back analyzer-rejected inline values while preserving a usable preview for retry", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-inline-failure-"));

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const projectFile = join(outputDir, "q1timeline.yml");
      controller.projectUri = vscode.Uri.file(projectFile);
      controller.outputDir = outputDir;
      const previousIr = { analysis: { parameters: [{ name: "DURATION" }] }, events: [{ id: "previous" }] };
      controller.timelineIr = previousIr;
      controller.lastAnalyzerTimelineIr = previousIr;
      controller.lastPreviewHtml = "<html>previous timeline and editable parameters</html>";
      const messages: any[] = [];
      const webview = { html: controller.lastPreviewHtml, postMessage: (message: any) => messages.push(message) };
      controller.panel = { webview, dispose: () => {} };
      controller.parameterOverrides.set("WAIT", "96");
      controller.execFile = async () => {
        writeFileSync(join(outputDir, "timeline_ir.json"), '{"events":[{"id":"rejected"}]}');
        throw Object.assign(new Error("Analyzer exited with code 2"), {
          exitCode: 2,
          stdout: JSON.stringify({
            error: { kind: "invalid_argument_value", message: "play duration must be non-negative" },
            diagnostics: [{ severity: "error", code: "invalid_argument_value", message: "play duration must be non-negative" }],
          }),
        });
      };

      for (const previousValue of [undefined, "128"]) {
        if (previousValue !== undefined) {
          controller.parameterOverrides.set("DURATION", previousValue);
        }
        await controller.handleWebviewMessage({ type: "setAnalysisParameter", name: "DURATION", valueJson: "-1", projectFile });

        assert.equal(controller.parameterOverrides.get("DURATION"), previousValue);
        assert.equal(controller.parameterOverrides.get("WAIT"), "96");
        assert.equal(controller.timelineIr, previousIr);
        assert.equal(controller.lastAnalyzerTimelineIr, previousIr);
        assert.equal(webview.html, "<html>previous timeline and editable parameters</html>");
        assert.equal(controller.lastPreviewHtml, webview.html);
        assert.equal(controller.analysisStatus.status, "error");
        assert.match(messages.filter((message) => message.type === "analysisParameterError").at(-1).message, /play duration must be non-negative/);
      }

      let analysisCount = 0;
      controller.runAnalysis = async () => { analysisCount += 1; };
      controller.runRender = async () => assert.fail("A rejected on-disk IR must not be rendered");
      await controller.rerenderCurrentTimelineMode("debug");
      assert.equal(analysisCount, 1);
      await controller.handleWebviewMessage({ type: "setAnalysisParameter", name: "DURATION", valueJson: "256", projectFile });
      assert.equal(controller.parameterOverrides.get("DURATION"), "256");
      assert.equal(analysisCount, 2);
      await controller.handleWebviewMessage({ type: "resetAnalysisParameter", name: "DURATION", projectFile });
      assert.equal(controller.parameterOverrides.has("DURATION"), false);
      assert.equal(analysisCount, 3);
      controller.dispose();
    });
  });

  it("keeps inline failure recovery across a queued refresh", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-inline-queued-"));

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const projectFile = join(outputDir, "q1timeline.yml");
      controller.projectUri = vscode.Uri.file(projectFile);
      controller.outputDir = outputDir;
      const previousIr = { analysis: { parameters: [{ name: "DURATION" }] }, events: [] };
      controller.timelineIr = previousIr;
      controller.lastAnalyzerTimelineIr = previousIr;
      const error = Object.assign(new Error("play duration must be non-negative"), {
        stdout: JSON.stringify({ diagnostics: [{ severity: "error", message: "play duration must be non-negative" }] }),
      });
      let rejectFirst: (error: Error) => void;
      let started: () => void;
      const firstStarted = new Promise<void>((resolve) => { started = resolve; });
      let errorReceived: () => void;
      const inlineErrorReceived = new Promise<void>((resolve) => { errorReceived = resolve; });
      const webview = {
        html: "previous timeline",
        postMessage: (message: any) => {
          if (message.type === "analysisParameterError") errorReceived();
        },
      };
      controller.panel = { webview, dispose: () => {} };
      let runCount = 0;
      controller.execFile = async () => {
        runCount += 1;
        if (runCount === 1) {
          return new Promise((_resolve, reject) => { rejectFirst = reject; started(); });
        }
        throw error;
      };

      const firstRun = controller.handleWebviewMessage({ type: "setAnalysisParameter", name: "DURATION", valueJson: "-1", projectFile });
      await firstStarted;
      await controller.runAnalysis();
      assert.equal(controller.analysisQueued, true);
      rejectFirst(error);
      await firstRun;
      await inlineErrorReceived;

      assert.equal(runCount, 2);
      assert.equal(controller.parameterOverrides.has("DURATION"), false);
      assert.equal(controller.timelineIr, previousIr);
      assert.equal(controller.lastAnalyzerTimelineIr, previousIr);
      assert.equal(webview.html, "previous timeline");
      controller.dispose();
    });
  });

  it("does not carry an old inline rollback into a new analysis after switching projects away and back", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = sourceFileController(TimelineController, vscode);
      const { controller } = setup;
      const projectA = controller.projectUri.fsPath;
      const rootB = mkdtempSync(join(tmpdir(), "q1-inline-return-project-"));
      const projectB = join(rootB, "q1timeline.yml");
      for (const projectFile of [projectA, projectB]) {
        writeFileSync(projectFile, "sequencers:\n  - id: seq0\n    file: seq0.q1asm\n");
      }
      writeFileSync(join(rootB, "seq0.q1asm"), "stop\n");
      const previousIr = { analysis: { parameters: [{ name: "DURATION" }] }, events: [{ id: "old-A" }] };
      controller.timelineIr = controller.lastAnalyzerTimelineIr = previousIr;
      controller.parameterOverrides.set("DURATION", "128");
      const messages: any[] = [];
      controller.panel.webview.postMessage = (message) => messages.push(message);
      const execFile = controller.execFile.bind(controller);
      const analyzers: any[] = [];
      let release!: () => void;
      let started!: () => void;
      const waiting = new Promise<void>((resolve) => { started = resolve; });
      controller.execFile = async (...args) => {
        if (args[5] === "analyzer") {
          analyzers.push(args[1]);
          if (analyzers.length === 1) await new Promise<void>((resolve) => { release = resolve; started(); });
          else throw new Error("New A analysis failed");
        }
        return execFile(...args);
      };
      let editing: Promise<void> | undefined;
      try {
        editing = controller.handleWebviewMessage({ type: "setAnalysisParameter", name: "DURATION", valueJson: "256", projectFile: projectA });
        await waiting;
        await controller.openTarget({ projectFile: projectB });
        await controller.openTarget({ projectFile: projectA });
        const inputs = ["DURATION", "256"];
        vscode.window.showInputBox = async () => inputs.shift();
        await controller.setAnalysisParameter();
        assert.equal(controller.parameterOverrides.get("DURATION"), "256");
        release();
        await editing;
        await setup.settle();
        assert.equal(analyzers.length, 2);
        assert.ok(analyzers.every((args) => args[args.indexOf("--project") + 1] === projectA));
        assert.equal(controller.parameterOverrides.get("DURATION"), "256",
          "Failure of the new A request must not restore the first A session's previous override");
        assert.equal(controller.lastAnalyzerTimelineIr, undefined);
        assert.equal(messages.filter((message) => message.type === "analysisParameterError").length, 0,
          "The old inline editor must not receive a failure reply belonging to the new A request");
      } finally {
        release?.();
        await editing;
        await setup.settle();
        controller.dispose();
      }
    });
  });

  it("rolls back inline changes when preparing analyzer outputs fails", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-inline-output-failure-"));

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const projectFile = join(outputDir, "q1timeline.yml");
      controller.projectUri = vscode.Uri.file(projectFile);
      controller.outputDir = outputDir;
      controller.timelineIr = { analysis: { parameters: [{ name: "DURATION" }] } };
      const messages: any[] = [];
      controller.postWebviewMessage = (message: any) => messages.push(message);
      controller.clearAnalyzerOutputFiles = async () => { throw new Error("Unable to clear output"); };

      await controller.handleWebviewMessage({ type: "setAnalysisParameter", name: "DURATION", valueJson: "128", projectFile });

      assert.equal(controller.parameterOverrides.has("DURATION"), false);
      assert.equal(controller.analysisInFlight, false);
      assert.match(messages.find((message) => message.type === "analysisParameterError").message, /Unable to clear output/);
      controller.dispose();
    });
  });

  it("opens direct source locations from timeline control messages", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      let openedSource: any = undefined;
      controller.openSourceLocation = async (source: any) => {
        openedSource = source;
      };

      await controller.handleWebviewMessage({
        type: "sourceClick",
        file: "q1asm/seq0.q1asm",
        line: 15,
        column: 1,
      });

      assert.deepEqual(openedSource, { file: "q1asm/seq0.q1asm", line: 15, column: 1 });
      controller.dispose();
    });
  });

  it("clears a loop timeline override when reset requests one visible iteration", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-control-reset-"));
    const capturedArgs: string[][] = [];

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file(join(outputDir, "q1timeline.yml"));
      controller.outputDir = outputDir;
      controller.execFile = async (_pythonPath: string, args: string[]) => {
        capturedArgs.push(args);
        return { stdout: "{}", stderr: "", exitCode: 0, elapsedMs: 1 };
      };
      controller.runRender = async () => ({ stdout: "", stderr: "", exitCode: 0, elapsedMs: 1 });
      controller.refreshWebview = async () => undefined;

      await controller.handleWebviewMessage({
        type: "setLoopPreview",
        loopKey: "seq0:loop:main.q1asm:2-8",
        visibleIterations: 3,
      });
      await controller.handleWebviewMessage({
        type: "setLoopPreview",
        loopKey: "seq0:loop:main.q1asm:2-8",
        visibleIterations: 1,
      });

      const latestArgs = capturedArgs[capturedArgs.length - 1];
      assert.ok(latestArgs);
      assert.equal(latestArgs.includes("--loop-preview"), false);
      assert.deepEqual(controller.analyzerOverrideArgs(), []);
      controller.dispose();
    });
  });

  it("clears timeline chip overrides when switching projects", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\project-a\\q1timeline.yml");
      controller.outputDir = "C:\\repo\\project-a\\.q1timeline";
      controller.runAnalysis = async () => undefined;

      await controller.handleWebviewMessage({
        type: "setBranchAssumption",
        branchId: "seq0:branch:a.q1asm:4:jge:target",
        path: "taken",
      });
      await controller.handleWebviewMessage({
        type: "setLoopPreview",
        loopKey: "seq0:loop:a.q1asm:2-8",
        visibleIterations: 2,
      });
      controller.parameterOverrides.set("DURATION", "40");

      assert.notDeepEqual(controller.analyzerOverrideArgs(), []);
      controller.setProjectUri(vscode.Uri.file("C:\\repo\\project-b\\q1timeline.yml"));
      assert.deepEqual(controller.analyzerOverrideArgs(), []);
      controller.dispose();
    });
  });

  it("clears analyzer diff baseline before timeline control reruns", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const baselinesDuringRun: any[] = [];
      controller.runAnalysis = async () => {
        baselinesDuringRun.push(controller.lastAnalyzerTimelineIr);
      };

      controller.lastAnalyzerTimelineIr = { events: [{ id: "previous-path" }] };
      await controller.handleWebviewMessage({
        type: "setBranchAssumption",
        branchId: "seq0:branch:main.q1asm:4:jge:target",
        path: "fallthrough",
      });

      controller.lastAnalyzerTimelineIr = { events: [{ id: "previous-loop-preview" }] };
      await controller.handleWebviewMessage({
        type: "setLoopPreview",
        loopKey: "seq0:loop:main.q1asm:2-8",
        visibleIterations: 3,
      });

      assert.deepEqual(baselinesDuringRun, [undefined, undefined]);
      controller.dispose();
    });
  });

  describe("branch selection restoration", () => {
    function setup(vscode: any, TimelineController: any) {
      const outputDir = mkdtempSync(join(tmpdir(), "q1-branch-selection-"));
      const controller = new TimelineController(createContext());
      const posted: any[] = [];
      const branch = (id: string, branchId = "seq0:branch:4") => ({
        id, kind: "q1_issue", sequencer_id: "seq0", lane: "q1.issue",
        source: { file: join(outputDir, "seq0.q1asm"), line: 4, column: 1, raw: "jge R0,1,@target" },
        meta: { branch_id: branchId },
      });
      let nextEvents: any[] = [branch("original")];
      controller.projectUri = vscode.Uri.file(join(outputDir, "q1timeline.yml"));
      controller.outputDir = outputDir;
      controller.timelineIr = { events: nextEvents };
      controller.panel = { webview: { html: "", cspSource: "vscode-resource:",
        asWebviewUri: (uri: any) => uri, postMessage: (message: any) => posted.push(message) }, dispose() {} };
      controller.execFile = async () => {
        writeFileSync(join(outputDir, "timeline_ir.json"), JSON.stringify({ events: nextEvents }), "utf8");
        writeFileSync(join(outputDir, "diagnostics.json"), "[]", "utf8");
        writeFileSync(join(outputDir, "timeline.html"), "<html><head></head><body></body></html>", "utf8");
        return { stdout: JSON.stringify({ schema_version: "0.2.0", status: "ok" }), stderr: "", exitCode: 0, elapsedMs: 1 };
      };
      controller.runRender = async () => ({ stdout: "", stderr: "", exitCode: 0, elapsedMs: 1 });
      controller.openSourceForEvent = () => { throw new Error("Restoring a branch must not jump to source"); };
      return { controller, branch, posted, setEvents: (events: any[]) => { nextEvents = events; },
        highlights: () => posted.filter((message) => message.type === "highlightEventIds") };
    }

    it("restores the same branch once after the fresh webview is ready without a source jump", async () => {
      const vscode = createMockVscode();
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const { controller, branch, setEvents, highlights } = setup(vscode, TimelineController);
        setEvents([branch("fallback"), branch("original")]);
        await controller.handleWebviewMessage({ type: "setBranchAssumption", branchId: "seq0:branch:4", path: "both", eventId: "original" });
        assert.deepEqual(highlights(), [], "The replacement webview has not acknowledged readiness");
        await controller.handleWebviewMessage({ type: "webviewReady" });
        await controller.handleWebviewMessage({ type: "webviewReady" });
        assert.deepEqual(highlights(), [{ type: "highlightEventIds", highlightEventIds: ["original"] }]);
        assert.equal(controller.pendingBranchSelection, undefined);
        controller.dispose();
      });
    });

    it("rejects reused IDs with a changed identity and falls back only to the same branch source", async () => {
      const vscode = createMockVscode();
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        for (const change of [
          (event: any) => { event.meta.branch_id = "another-branch"; },
          (event: any) => { event.sequencer_id = "seq1"; },
          (event: any) => { event.source.file = "another.q1asm"; },
          (event: any) => { event.source.line = 9; },
          (event: any) => { event.source.raw = "jge R1,2,@target"; },
          (event: any) => { event.kind = "play"; },
          (event: any) => { event.meta.diff_status = "removed"; },
        ]) {
          const { controller, branch, setEvents, highlights } = setup(vscode, TimelineController);
          const reused = branch("original");
          change(reused);
          setEvents([reused, { ...branch("region"), kind: "branch_region" }, branch("current-q1")]);
          await controller.setBranchAssumption("seq0:branch:4", "taken", "original");
          await controller.handleWebviewMessage({ type: "webviewReady" });
          assert.deepEqual(highlights(), [{ type: "highlightEventIds", highlightEventIds: ["current-q1"] }]);
          controller.dispose();
        }
      });
    });

    it("falls back to a current branch region and never selects unrelated events on the same line", async () => {
      const vscode = createMockVscode();
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        for (const hasRegion of [true, false]) {
          const { controller, branch, setEvents, highlights } = setup(vscode, TimelineController);
          setEvents([
            { ...branch("removed"), meta: { branch_id: "seq0:branch:4", diff_status: "removed" } },
            branch("different-branch", "seq0:branch:9"),
            { ...branch("runtime"), kind: "wait" },
            ...(hasRegion ? [{ ...branch("region"), kind: "branch_region" }] : []),
          ]);
          await controller.setBranchAssumption("seq0:branch:4", "collapsed", "original");
          await controller.handleWebviewMessage({ type: "webviewReady" });
          assert.deepEqual(highlights(), hasRegion ? [{ type: "highlightEventIds", highlightEventIds: ["region"] }] : []);
          assert.equal(controller.pendingBranchSelection, undefined);
          controller.dispose();
        }
      });
    });

    it("keeps the latest queued branch request until its fresh analysis and readiness", async () => {
      const vscode = createMockVscode();
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const { controller, branch, setEvents, highlights } = setup(vscode, TimelineController);
        controller.timelineIr.events.push(branch("second", "seq0:branch:8"));
        controller.analysisInFlight = true;
        await controller.setBranchAssumption("seq0:branch:4", "taken", "original");
        await controller.handleWebviewMessage({ type: "webviewReady" });
        await controller.setBranchAssumption("seq0:branch:8", "fallthrough", "second");
        await controller.handleWebviewMessage({ type: "webviewReady" });
        assert.deepEqual(highlights(), []);
        assert.equal(controller.analysisQueued, true);
        setEvents([branch("fresh-first"), branch("fresh-second", "seq0:branch:8")]);
        controller.analysisInFlight = false;
        controller.analysisQueued = false;
        await controller.runAnalysis();
        assert.deepEqual(highlights(), []);
        await controller.handleWebviewMessage({ type: "webviewReady" });
        assert.deepEqual(highlights(), [{ type: "highlightEventIds", highlightEventIds: ["fresh-second"] }]);
        controller.dispose();
      });
    });

    it("drops restoration after source edits, project switches, failures, or a superseding analysis request", async () => {
      const vscode = createMockVscode();
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        for (const invalidate of [
          (controller: any) => controller.invalidateSourceAnalysis(),
          (controller: any) => controller.setProjectUri(vscode.Uri.file("C:\\another\\q1timeline.yml")),
          (controller: any) => controller.clearPreviewAfterAnalyzerFailure("failed"),
          (controller: any) => { controller.analysisRequestId += 1; },
        ]) {
          const { controller, highlights } = setup(vscode, TimelineController);
          await controller.setBranchAssumption("seq0:branch:4", "both", "original");
          invalidate(controller);
          await controller.handleWebviewMessage({ type: "webviewReady" });
          assert.deepEqual(highlights(), []);
          assert.equal(controller.pendingBranchSelection, undefined);
          controller.dispose();
        }
      });
    });
  });

  it("saves a raw comparison baseline only after a successful analysis", async () => {
    const vscode = createMockVscode();
    const root = mkdtempSync(join(tmpdir(), "q1-saved-baseline-"));
    const outputDir = join(root, ".q1timeline");
    mkdirSync(outputDir);
    const baselinePath = join(outputDir, "comparison-baseline.json");
    const warnings: string[] = [];
    const notices: string[] = [];
    vscode.window.showWarningMessage = async (message: string) => warnings.push(message);
    vscode.window.showInformationMessage = async (message: string) => notices.push(message);

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.setProjectUri(vscode.Uri.file(join(root, "q1timeline.yml")));
      controller.outputDir = outputDir;

      await controller.saveComparisonBaseline();

      assert.equal(existsSync(baselinePath), false);
      assert.match(warnings[0], /successful Q1ASM timeline analysis/);

      const rawTimelineIr = {
        schema_version: "1.0",
        events: [
          {
            id: "seq0:e1",
            sequencer_id: "seq0",
            kind: "set_mrk",
            lane: "rt.path0",
            source: { file: "seq0.q1asm", line: 1, raw: "set_mrk 1" },
          },
        ],
      };
      controller.lastAnalyzerTimelineIr = rawTimelineIr;
      await controller.saveComparisonBaseline();

      assert.deepEqual(JSON.parse(readFileSync(baselinePath, "utf8")), rawTimelineIr);
      assert.equal(existsSync(join(root, "comparison-baseline.json")), false);
      assert.match(notices[0], /baseline saved/);
      assert.equal(controller.webviewInitialState().comparison.nextMode, "saved");
      controller.dispose();
    });
  });

  for (const firstAction of ["save", "clear"]) {
    const secondAction = firstAction === "save" ? "clear" : "save";
    it(`serializes comparison baseline ${firstAction} then ${secondAction} while filesystem preparation is pending`, async () => {
      const vscode = createMockVscode();
      const root = mkdtempSync(join(tmpdir(), "q1-baseline-order-"));
      const outputDir = join(root, ".q1timeline");
      mkdirSync(outputDir);
      const baselinePath = join(outputDir, "comparison-baseline.json");
      const timelinePath = join(outputDir, "timeline_ir.json");
      const previousIr = { events: [{ id: "previous" }] };
      const freshIr = { events: [{ id: "fresh" }] };
      writeFileSync(baselinePath, JSON.stringify(previousIr));
      writeFileSync(timelinePath, JSON.stringify(freshIr));
      const fsPromises = require("node:fs").promises;
      const originalMkdir = fsPromises.mkdir;
      const originalRm = fsPromises.rm;
      let release!: () => void;
      let started!: () => void;
      let secondStarted = false;
      const waiting = new Promise<void>((resolve) => { started = resolve; });
      const pause = () => new Promise<void>((resolve) => { release = resolve; started(); });
      fsPromises.mkdir = async (...args: any[]) => {
        if (args[0] === outputDir) {
          if (firstAction === "save") await pause();
          else secondStarted = true;
        }
        return originalMkdir(...args);
      };
      fsPromises.rm = async (...args: any[]) => {
        if (args[0] === baselinePath) {
          if (firstAction === "clear") await pause();
          else secondStarted = true;
        }
        return originalRm(...args);
      };
      try {
        await withMockedVscode(vscode, async ({ TimelineController }) => {
          const controller = new TimelineController(createContext());
          try {
            controller.setProjectUri(vscode.Uri.file(join(root, "q1timeline.yml")));
            controller.outputDir = outputDir;
            controller.lastAnalyzerTimelineIr = previousIr;
            if (secondAction === "save") {
              await controller.prepareTimelineIrForRender(timelinePath);
              assert.deepEqual(controller.lastAnalyzerTimelineIr, freshIr);
            }
            const first = firstAction === "save" ? controller.saveComparisonBaseline() : controller.clearComparisonBaseline();
            await waiting;
            const second = secondAction === "save" ? controller.saveComparisonBaseline() : controller.clearComparisonBaseline();
            await Promise.resolve();
            const secondStartedWhileFirstPending = secondStarted;
            release();
            await Promise.all([first, second]);
            assert.equal(secondStartedWhileFirstPending, false, "A later baseline mutation must wait for the earlier filesystem operation");
            if (secondAction === "save") {
              assert.deepEqual(JSON.parse(readFileSync(baselinePath, "utf8")), freshIr);
            } else {
              assert.equal(existsSync(baselinePath), false);
            }
            assert.equal(controller.comparison.nextMode, secondAction === "save" ? "saved" : "previous");
          } finally {
            controller.dispose();
          }
        });
      } finally {
        release?.();
        fsPromises.mkdir = originalMkdir;
        fsPromises.rm = originalRm;
      }
    });
  }

  it("finishes loading a comparison baseline before a later save or clear begins", async () => {
    const observations: any[] = [];
    for (const action of ["save", "clear"]) {
      const vscode = createMockVscode();
      const root = mkdtempSync(join(tmpdir(), "q1-baseline-read-first-"));
      const outputDir = join(root, ".q1timeline");
      mkdirSync(outputDir);
      const baselinePath = join(outputDir, "comparison-baseline.json");
      const originalIr = { events: [{ id: "original" }] };
      const nextIr = { events: [{ id: "next" }] };
      writeFileSync(baselinePath, JSON.stringify(originalIr));
      const fsPromises = require("node:fs").promises;
      const originalReadFile = fsPromises.readFile;
      const originalMkdir = fsPromises.mkdir;
      const method = action === "save" ? "writeFile" : "rm";
      const originalMutation = fsPromises[method];
      let release!: () => void;
      let started!: () => void;
      let mutationStarted = false;
      const waiting = new Promise<void>((resolve) => { started = resolve; });
      fsPromises.readFile = async (...args: any[]) => {
        if (args[0] === baselinePath) {
          await new Promise<void>((resolve) => { release = resolve; started(); });
        }
        return originalReadFile(...args);
      };
      fsPromises.mkdir = async (...args: any[]) => {
        if (args[0] === outputDir) return mkdirSync(args[0], args[1]);
        return originalMkdir(...args);
      };
      fsPromises[method] = async (...args: any[]) => {
        if (args[0] === baselinePath) mutationStarted = true;
        return originalMutation(...args);
      };
      try {
        await withMockedVscode(vscode, async ({ TimelineController }) => {
          const controller = new TimelineController(createContext());
          try {
            controller.setProjectUri(vscode.Uri.file(join(root, "q1timeline.yml")));
            controller.outputDir = outputDir;
            controller.lastAnalyzerTimelineIr = nextIr;
            const loading = controller.loadComparisonBaseline();
            await waiting;
            const mutation = action === "save" ? controller.saveComparisonBaseline() : controller.clearComparisonBaseline();
            await Promise.resolve();
            await Promise.resolve();
            const mutationStartedWhileReadPending = mutationStarted;
            release();
            const [baseline] = await Promise.all([loading, mutation]);
            observations.push({ action, mutationStartedWhileReadPending, baseline });
            if (action === "save") assert.deepEqual(JSON.parse(readFileSync(baselinePath, "utf8")), nextIr);
            else assert.equal(existsSync(baselinePath), false);
          } finally {
            controller.dispose();
          }
        });
      } finally {
        release?.();
        fsPromises.readFile = originalReadFile;
        fsPromises.mkdir = originalMkdir;
        fsPromises[method] = originalMutation;
      }
    }
    assert.deepEqual(observations, ["save", "clear"].map((action) => ({
      action, mutationStartedWhileReadPending: false, baseline: { events: [{ id: "original" }] },
    })), "Baseline writes and removals must wait for an earlier baseline read to finish");
  });

  for (const action of ["save", "clear"]) {
    it(`waits for a pending comparison baseline ${action} before loading the next analysis baseline`, async () => {
      const vscode = createMockVscode();
      const root = mkdtempSync(join(tmpdir(), "q1-baseline-load-order-"));
      const outputDir = join(root, ".q1timeline");
      mkdirSync(outputDir);
      const baselinePath = join(outputDir, "comparison-baseline.json");
      const savedIr = { events: [{ id: "saved" }] };
      if (action === "clear") writeFileSync(baselinePath, JSON.stringify(savedIr));
      const fsPromises = require("node:fs").promises;
      const method = action === "save" ? "writeFile" : "rm";
      const originalMutation = fsPromises[method];
      const originalReadFile = fsPromises.readFile;
      let release!: () => void;
      let started!: () => void;
      let readStarted = false;
      const waiting = new Promise<void>((resolve) => { started = resolve; });
      fsPromises[method] = async (...args: any[]) => {
        if (args[0] === baselinePath) {
          await new Promise<void>((resolve) => { release = resolve; started(); });
        }
        return originalMutation(...args);
      };
      fsPromises.readFile = async (...args: any[]) => {
        if (args[0] === baselinePath) readStarted = true;
        return originalReadFile(...args);
      };
      try {
        await withMockedVscode(vscode, async ({ TimelineController }) => {
          const controller = new TimelineController(createContext());
          try {
            controller.setProjectUri(vscode.Uri.file(join(root, "q1timeline.yml")));
            controller.outputDir = outputDir;
            controller.lastAnalyzerTimelineIr = savedIr;
            const mutation = action === "save" ? controller.saveComparisonBaseline() : controller.clearComparisonBaseline();
            await waiting;
            const loading = controller.loadComparisonBaseline();
            await Promise.resolve();
            const readStartedWhileMutationPending = readStarted;
            release();
            const [, baseline] = await Promise.all([mutation, loading]);
            assert.equal(readStartedWhileMutationPending, false, "The next analysis must wait for the requested baseline mutation before reading");
            assert.deepEqual(baseline, action === "save" ? savedIr : undefined);
          } finally {
            controller.dispose();
          }
        });
      } finally {
        release?.();
        fsPromises[method] = originalMutation;
        fsPromises.readFile = originalReadFile;
      }
    });
  }

  for (const action of ["save", "clear"]) {
    it(`keeps a pending comparison baseline ${action} attached to its original project`, async () => {
      const vscode = createMockVscode();
      const root = mkdtempSync(join(tmpdir(), "q1-baseline-switch-"));
      const outputA = join(root, "a", ".q1timeline");
      const outputB = join(root, "b", ".q1timeline");
      mkdirSync(outputA, { recursive: true });
      mkdirSync(outputB, { recursive: true });
      const baselineA = join(outputA, "comparison-baseline.json");
      const baselineB = join(outputB, "comparison-baseline.json");
      const irA = { events: [{ id: "a" }] };
      const irB = { events: [{ id: "b" }] };
      writeFileSync(baselineA, JSON.stringify({ events: [] }));
      const fsPromises = require("node:fs").promises;
      const method = action === "save" ? "mkdir" : "rm";
      const original = fsPromises[method];
      let release!: () => void;
      let started!: () => void;
      const waiting = new Promise<void>((resolve) => { started = resolve; });
      fsPromises[method] = async (...args: any[]) => {
        if (args[0] === (action === "save" ? outputA : baselineA)) {
          await new Promise<void>((resolve) => { release = resolve; started(); });
        }
        return original(...args);
      };
      try {
        await withMockedVscode(vscode, async ({ TimelineController }) => {
          const controller = new TimelineController(createContext());
          controller.setProjectUri(vscode.Uri.file(join(root, "a", "q1timeline.yml")));
          controller.outputDir = outputA;
          controller.lastAnalyzerTimelineIr = irA;
          const posted: any[] = [];
          controller.postWebviewMessage = (message) => posted.push(message);
          const operation = action === "save" ? controller.saveComparisonBaseline() : controller.clearComparisonBaseline();
          await waiting;
          controller.setProjectUri(vscode.Uri.file(join(root, "b", "q1timeline.yml")));
          controller.outputDir = outputB;
          controller.lastAnalyzerTimelineIr = irB;
          controller.comparison = { mode: "saved" };
          controller.comparisonBaselineWarningPath = baselineB;
          posted.length = 0;
          release();
          await operation;
          if (action === "save") assert.deepEqual(JSON.parse(readFileSync(baselineA, "utf8")), irA);
          else assert.equal(existsSync(baselineA), false);
          assert.deepEqual(controller.comparison, { mode: "saved" }, "The new project's comparison mode must stay unchanged");
          assert.equal(controller.comparisonBaselineWarningPath, baselineB);
          assert.deepEqual(posted, []);
          controller.dispose();
        });
      } finally {
        fsPromises[method] = original;
      }
    });
  }

  for (const stage of ["timeline", "baseline"]) {
    it(`drops comparison preparation after a project switch during its ${stage} read`, async () => {
      const vscode = createMockVscode();
      const root = mkdtempSync(join(tmpdir(), "q1-diff-project-switch-"));
      const outputA = join(root, "a", ".q1timeline");
      const outputB = join(root, "b", ".q1timeline");
      mkdirSync(outputA, { recursive: true });
      mkdirSync(outputB, { recursive: true });
      const timelinePath = join(outputA, "timeline_ir.json");
      const baselinePath = join(outputA, "comparison-baseline.json");
      const irA = { events: [{ id: "a" }] };
      const irB = { events: [{ id: "b" }] };
      writeFileSync(timelinePath, JSON.stringify(irA));
      writeFileSync(baselinePath, JSON.stringify({ events: [] }));
      const fsPromises = require("node:fs").promises;
      const original = fsPromises.readFile;
      let release!: () => void;
      let started!: () => void;
      const waiting = new Promise<void>((resolve) => { started = resolve; });
      fsPromises.readFile = async (...args) => {
        if (args[0] === (stage === "timeline" ? timelinePath : baselinePath)) {
          await new Promise<void>((resolve) => { release = resolve; started(); });
        }
        return original(...args);
      };
      try {
        await withMockedVscode(vscode, async ({ TimelineController }) => {
          const controller = new TimelineController(createContext());
          controller.setProjectUri(vscode.Uri.file(join(root, "a", "q1timeline.yml")));
          controller.outputDir = outputA;
          const preparation = controller.prepareTimelineIrForRender(timelinePath);
          await waiting;
          controller.setProjectUri(vscode.Uri.file(join(root, "b", "q1timeline.yml")));
          controller.outputDir = outputB;
          controller.lastAnalyzerTimelineIr = irB;
          controller.comparison = { mode: "saved" };
          release();
          await preparation;
          assert.deepEqual(controller.lastAnalyzerTimelineIr, irB, "Project A's late result must not become B's comparison history");
          assert.deepEqual(controller.comparison, { mode: "saved" });
          assert.deepEqual(JSON.parse(readFileSync(timelinePath, "utf8")), irA);
          controller.dispose();
        });
      } finally { fsPromises.readFile = original; }
    });
  }

  it("keeps a saved comparison baseline stable across controller instances and projects", async () => {
    const vscode = createMockVscode();
    const root = mkdtempSync(join(tmpdir(), "q1-persistent-baseline-"));
    const projectA = join(root, "project-a");
    const projectB = join(root, "project-b");
    const outputA = join(projectA, ".q1timeline");
    const outputB = join(projectB, ".q1timeline");
    mkdirSync(outputA, { recursive: true });
    mkdirSync(outputB, { recursive: true });
    const baselineIr = {
      events: [
        {
          id: "seq0:old",
          sequencer_id: "seq0",
          kind: "set_mrk",
          lane: "rt.path0",
          source: { file: "seq0.q1asm", line: 4, raw: "set_mrk 1" },
          t0: { value: 20 },
          t1: { value: 20 },
          duration: { value: 0 },
        },
      ],
    };
    const changedIr = {
      events: [
        {
          id: "seq0:new",
          sequencer_id: "seq0",
          kind: "set_mrk",
          lane: "rt.path0",
          source: { file: "seq0.q1asm", line: 4, raw: "set_mrk 2" },
          t0: { value: 20 },
          t1: { value: 20 },
          duration: { value: 0 },
        },
      ],
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const firstController = new TimelineController(createContext());
      firstController.setProjectUri(vscode.Uri.file(join(projectA, "q1timeline.yml")));
      firstController.outputDir = outputA;
      firstController.lastAnalyzerTimelineIr = baselineIr;
      await firstController.saveComparisonBaseline();
      firstController.dispose();

      const secondController = new TimelineController(createContext());
      secondController.setProjectUri(vscode.Uri.file(join(projectA, "q1timeline.yml")));
      secondController.outputDir = outputA;
      const timelineAPath = join(outputA, "timeline_ir.json");

      writeFileSync(timelineAPath, JSON.stringify(changedIr), "utf8");
      await secondController.prepareTimelineIrForRender(timelineAPath);
      let annotated = JSON.parse(readFileSync(timelineAPath, "utf8"));
      assert.equal(annotated.events[0].meta.diff_status, "changed");

      writeFileSync(timelineAPath, JSON.stringify(changedIr), "utf8");
      assert.equal(secondController.webviewInitialState().comparison.mode, "saved");
      await secondController.prepareTimelineIrForRender(timelineAPath);
      annotated = JSON.parse(readFileSync(timelineAPath, "utf8"));
      assert.equal(annotated.events[0].meta.diff_status, "changed");

      secondController.setProjectUri(vscode.Uri.file(join(projectB, "q1timeline.yml")));
      secondController.outputDir = outputB;
      const timelineBPath = join(outputB, "timeline_ir.json");
      writeFileSync(timelineBPath, JSON.stringify(changedIr), "utf8");
      await secondController.prepareTimelineIrForRender(timelineBPath);
      annotated = JSON.parse(readFileSync(timelineBPath, "utf8"));
      assert.equal(annotated.events[0].meta?.diff_status, undefined);
      secondController.dispose();
    });
  });

  it("warns about a malformed saved baseline and falls back to the previous run", async () => {
    const vscode = createMockVscode();
    const root = mkdtempSync(join(tmpdir(), "q1-malformed-baseline-"));
    const outputDir = join(root, ".q1timeline");
    mkdirSync(outputDir);
    writeFileSync(join(outputDir, "comparison-baseline.json"), '{"events":"invalid"}', "utf8");
    const warnings: string[] = [];
    vscode.window.showWarningMessage = async (message: string) => warnings.push(message);
    const currentIr = {
      events: [
        {
          id: "seq0:e1",
          sequencer_id: "seq0",
          kind: "wait",
          lane: "rt.wait",
          source: { file: "seq0.q1asm", line: 2 },
          t0: { value: 0 },
          t1: { value: 4 },
          duration: { value: 4 },
        },
      ],
    };
    const timelinePath = join(outputDir, "timeline_ir.json");
    writeFileSync(timelinePath, JSON.stringify(currentIr), "utf8");

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.setProjectUri(vscode.Uri.file(join(root, "q1timeline.yml")));
      controller.outputDir = outputDir;
      controller.lastAnalyzerTimelineIr = currentIr;

      await controller.prepareTimelineIrForRender(timelinePath);

      const annotated = JSON.parse(readFileSync(timelinePath, "utf8"));
      assert.equal(annotated.events[0].meta?.diff_status, undefined);
      assert.equal(warnings.length, 1);
      assert.match(warnings[0], /invalid and was ignored/);
      controller.dispose();
    });
  });

  it("returns to previous-run comparison after clearing a saved baseline", async () => {
    const vscode = createMockVscode();
    const root = mkdtempSync(join(tmpdir(), "q1-clear-baseline-"));
    const outputDir = join(root, ".q1timeline");
    mkdirSync(outputDir);
    const baselinePath = join(outputDir, "comparison-baseline.json");
    writeFileSync(baselinePath, JSON.stringify({ events: [] }), "utf8");
    const currentIr = {
      events: [
        {
          id: "seq0:e1",
          sequencer_id: "seq0",
          kind: "wait",
          lane: "rt.wait",
          source: { file: "seq0.q1asm", line: 2 },
          t0: { value: 0 },
          t1: { value: 4 },
          duration: { value: 4 },
        },
      ],
    };
    const timelinePath = join(outputDir, "timeline_ir.json");
    writeFileSync(timelinePath, JSON.stringify(currentIr), "utf8");

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.setProjectUri(vscode.Uri.file(join(root, "q1timeline.yml")));
      controller.outputDir = outputDir;
      controller.lastAnalyzerTimelineIr = currentIr;

      await controller.clearComparisonBaseline();
      assert.equal(controller.webviewInitialState().comparison.nextMode, "previous");
      assert.equal(controller.webviewInitialState().comparison.pending, true);
      await controller.prepareTimelineIrForRender(timelinePath);

      const annotated = JSON.parse(readFileSync(timelinePath, "utf8"));
      assert.equal(existsSync(baselinePath), false);
      assert.deepEqual(controller.webviewInitialState().comparison, { mode: "previous" });
      assert.equal(annotated.events[0].meta?.diff_status, undefined);
      controller.dispose();
    });
  });

  it("marks same-line raw Q1ASM event changes as changed", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-diff-change-"));
    const timelinePath = join(outputDir, "timeline_ir.json");
    writeFileSync(
      timelinePath,
      JSON.stringify({
        events: [
          {
            id: "seq0:e10:new",
            sequencer_id: "seq0",
            kind: "set_mrk",
            lane: "rt.path0",
            source: { file: "seq0.q1asm", line: 10, raw: "set_mrk 2" },
            t0: { value: 20 },
            t1: { value: 20 },
            duration: { value: 0 },
          },
        ],
      }),
      "utf8",
    );

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.outputDir = outputDir;
      controller.lastAnalyzerTimelineIr = {
        events: [
          {
            id: "seq0:e10:old",
            sequencer_id: "seq0",
            kind: "set_mrk",
            lane: "rt.path0",
            source: { file: "seq0.q1asm", line: 10, raw: "set_mrk 1" },
            t0: { value: 20 },
            t1: { value: 20 },
            duration: { value: 0 },
          },
        ],
      };

      await controller.prepareTimelineIrForRender(timelinePath);

      const annotated = JSON.parse(require("node:fs").readFileSync(timelinePath, "utf8"));
      assert.equal(annotated.events[0].meta.diff_status, "changed");
      assert.equal(annotated.events[0].meta.diff_before.source.raw, "set_mrk 1");
      assert.equal(controller.webviewInitialState().comparison.mode, "previous");
      controller.dispose();
    });
  });

  it("marks events added against an empty baseline and parameter-only value changes", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-diff-values-"));
    const timelinePath = join(outputDir, "timeline_ir.json");
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.outputDir = outputDir;
      const event = {
        id: "seq0:e1", kind: "set_awg_gain", lane: "rt.path0",
        source: { file: "seq0.q1asm", line: 1, raw: "set_awg_gain {GAIN},0" },
        t0: { value: 0 }, t1: { value: 0 },
        meta: { resolved_args: [{ index: 0, raw: "{GAIN}", value: { value: 100 } }] },
      };
      controller.lastAnalyzerTimelineIr = { events: [] };
      writeFileSync(timelinePath, JSON.stringify({ events: [event] }), "utf8");
      await controller.prepareTimelineIrForRender(timelinePath);
      assert.equal(JSON.parse(readFileSync(timelinePath, "utf8")).events[0].meta.diff_status, "added");
      event.meta.resolved_args[0].value.value = 200;
      writeFileSync(timelinePath, JSON.stringify({ events: [event] }), "utf8");
      await controller.prepareTimelineIrForRender(timelinePath);
      const changed = JSON.parse(readFileSync(timelinePath, "utf8")).events[0];
      assert.equal(changed.meta.diff_status, "changed");
      assert.equal(changed.meta.diff_before.meta.resolved_args[0].value.value, 100);
      assert.equal(changed.meta.resolved_args[0].value.value, 200);
      controller.dispose();
    });
  });

  it("keeps diff-removed event ids unique after Q1ASM line insertions", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-diff-ids-"));
    const timelinePath = join(outputDir, "timeline_ir.json");
    writeFileSync(
      timelinePath,
      JSON.stringify({
        events: [
          { id: "seq0:e0", sequencer_id: "seq0", kind: "wait", lane: "rt.wait", source: { file: "seq0.q1asm", line: 1 }, t0: { value: 0 }, t1: { value: 4 }, duration: { value: 4 } },
          { id: "seq0:e1", sequencer_id: "seq0", kind: "wait", lane: "rt.wait", source: { file: "seq0.q1asm", line: 2 }, t0: { value: 4 }, t1: { value: 8 }, duration: { value: 4 } },
          { id: "seq0:e2", sequencer_id: "seq0", kind: "play", lane: "rt.path0", source: { file: "seq0.q1asm", line: 3 }, t0: { value: 8 }, t1: { value: 16 }, duration: { value: 8 } },
        ],
      }),
      "utf8",
    );

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.outputDir = outputDir;
      controller.lastAnalyzerTimelineIr = {
        events: [
          { id: "seq0:e0", sequencer_id: "seq0", kind: "wait", lane: "rt.wait", source: { file: "seq0.q1asm", line: 1 }, t0: { value: 0 }, t1: { value: 4 }, duration: { value: 4 } },
          { id: "seq0:e1", sequencer_id: "seq0", kind: "play", lane: "rt.path0", source: { file: "seq0.q1asm", line: 2 }, t0: { value: 4 }, t1: { value: 12 }, duration: { value: 8 } },
        ],
      };

      await controller.prepareTimelineIrForRender(timelinePath);

      const annotated = JSON.parse(require("node:fs").readFileSync(timelinePath, "utf8"));
      const ids = annotated.events.map((event: { id: string }) => event.id);
      assert.equal(new Set(ids).size, ids.length);
      assert.equal(annotated.events.some((event: { id: string; meta?: { diff_status?: string } }) => event.id !== "seq0:e1" && event.meta?.diff_status === "removed"), true);
      controller.dispose();
    });
  });

  it("passes execution settings to the analyzer and lets project mode keep YAML defaults", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-execution-mode-"));
    const capturedArgs: string[][] = [];

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file(join(outputDir, "q1timeline.yml"));
      controller.outputDir = outputDir;
      let mode = "concrete";
      controller.config = () => ({ get: (key: string, fallback: any) => key === "executionMode" ? mode : key === "maxSteps" ? 250 : fallback });
      controller.execFile = async (_pythonPath: string, args: string[]) => {
        capturedArgs.push(args);
        return { stdout: JSON.stringify({ schema_version: "0.2.0", status: "ok" }), stderr: "", exitCode: 0, elapsedMs: 1 };
      };
      controller.runRender = async () => ({ stdout: "", stderr: "", exitCode: 0, elapsedMs: 1 });
      controller.refreshWebview = async () => undefined;

      await controller.runAnalysis();
      let args = capturedArgs[capturedArgs.length - 1];
      assert.deepEqual(args.slice(args.indexOf("--execution-mode"), args.indexOf("--execution-mode") + 4), ["--execution-mode", "concrete", "--max-steps", "250"]);
      mode = "analysis";
      await controller.runAnalysis();
      args = capturedArgs[capturedArgs.length - 1];
      assert.equal(args[args.indexOf("--execution-mode") + 1], "analysis");
      mode = "simulation";
      await controller.runAnalysis();
      args = capturedArgs[capturedArgs.length - 1];
      assert.deepEqual(args.slice(args.indexOf("--execution-mode"), args.indexOf("--execution-mode") + 4), ["--execution-mode", "simulation", "--max-steps", "250"]);
      mode = "project";
      await controller.runAnalysis();
      args = capturedArgs[capturedArgs.length - 1];
      assert.equal(args.includes("--execution-mode"), false);
      assert.equal(args.includes("--max-steps"), false);
      controller.dispose();
    });
  });

  it("prepares bounded simulation replay for the current preview without changing settings or source", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-prepare-replay-"));
    const projectPath = join(outputDir, "q1timeline.yml");
    const projectYaml = "sequencers:\n  - id: seq0\n    file: seq0.q1asm\n";
    writeFileSync(projectPath, projectYaml, "utf8");
    const sourceUri = vscode.Uri.file(join(outputDir, "seq0.q1asm"));
    vscode.window.activeTextEditor = { document: { uri: vscode.Uri.file(join(outputDir, "other-project", "other.q1asm")) } };
    const context = createContext();
    let settingsWrites = 0;
    context.workspaceState.update = async () => { settingsWrites++; };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(context);
      controller.projectUri = vscode.Uri.file(projectPath);
      controller.singleFileUri = sourceUri;
      controller.singleFileMode = true;
      controller.outputDir = outputDir;
      controller.panel = { webview: { postMessage: () => {} }, dispose: () => {} };
      controller.timelineIr = { sequencers: [{ id: "seq0" }], events: [] };
      controller.lastAnalyzerTimelineIr = { events: [{ id: "static-event" }] };
      controller.config = () => ({
        get: (key: string, fallback: any) => key === "maxSteps" ? 250 : fallback,
        update: async () => { settingsWrites++; },
      });
      const calls: any[] = [];
      controller.execFile = async (_pythonPath: string, args: string[], _timeoutMs: number, cwd: string) => {
        calls.push({ args, cwd, baseline: controller.lastAnalyzerTimelineIr });
        return { stdout: JSON.stringify({ schema_version: "0.2.0", status: "ok" }), stderr: "", exitCode: 0, elapsedMs: 1 };
      };
      controller.runRender = async () => ({ stdout: "", stderr: "", exitCode: 0, elapsedMs: 1 });
      controller.refreshWebview = async () => undefined;

      await controller.handleWebviewMessage({ type: "prepareExecutionReplay", sequencerId: "seq0" });
      await controller.refreshPreview();

      assert.equal(calls.length, 2);
      for (const { args, cwd, baseline } of calls) {
        assert.deepEqual(args.slice(args.indexOf("--execution-mode"), args.indexOf("--execution-mode") + 4),
          ["--execution-mode", "simulation", "--max-steps", "250"]);
        assert.equal(args[args.indexOf("--project") + 1], projectPath);
        assert.equal(cwd, outputDir);
        assert.equal(baseline, undefined);
      }
      assert.equal(controller.projectUri.fsPath, projectPath);
      assert.equal(controller.singleFileUri, sourceUri);
      assert.equal(controller.singleFileMode, true);
      assert.equal(settingsWrites, 0);
      assert.equal(readFileSync(projectPath, "utf8"), projectYaml);

      controller.singleFileUri = vscode.Uri.file(join(outputDir, "different.q1asm"));
      assert.deepEqual(controller.executionArgs(), []);
      controller.singleFileUri = sourceUri;
      controller.setProjectUri(vscode.Uri.file(join(outputDir, "other-project", "q1timeline.yml")));
      assert.deepEqual(controller.executionArgs(), []);
      controller.setProjectUri(vscode.Uri.file(projectPath));
      assert.deepEqual(controller.executionArgs(), []);
      controller.dispose();
    });
  });

  it("preserves selected runtime modes when preparing replay from an older trace", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-replay-runtime-mode-"));
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file(join(outputDir, "q1timeline.yml"));
      controller.outputDir = outputDir;
      controller.panel = { webview: { postMessage: () => {} }, dispose: () => {} };
      let configuredMode = "project";
      let analyzerArgs: string[] = [];
      controller.config = () => ({ get: (key: string, fallback: any) => key === "executionMode" ? configuredMode : fallback });
      controller.execFile = async (_pythonPath: string, args: string[]) => {
        analyzerArgs = args;
        return { stdout: JSON.stringify({ schema_version: "0.2.0", status: "ok" }), stderr: "", exitCode: 0, elapsedMs: 1 };
      };
      controller.runRender = async () => ({ stdout: "", stderr: "", exitCode: 0, elapsedMs: 1 });
      controller.refreshWebview = async () => undefined;
      for (const [configured, recorded, expected] of [
        ["project", "simulation", "simulation"],
        ["analysis", "simulation", "simulation"],
        ["concrete", "simulation", "concrete"],
        ["simulation", "concrete", "simulation"],
        ["project", "concrete", "concrete"],
        ["analysis", "concrete", "concrete"],
        ["analysis", "analysis", "simulation"],
      ]) {
        configuredMode = configured;
        controller.timelineIr = { execution: { seq0: { mode: recorded } } };
        await controller.handleWebviewMessage({ type: "prepareExecutionReplay", sequencerId: "seq0" });
        assert.deepEqual(analyzerArgs.slice(analyzerArgs.indexOf("--execution-mode"), analyzerArgs.indexOf("--execution-mode") + 4),
          ["--execution-mode", expected, "--max-steps", "10000"]);
      }
      configuredMode = "concrete";
      controller.timelineIr = { execution: { seq0: { mode: "concrete" } } };
      await controller.handleWebviewMessage({ type: "prepareExecutionReplay", sequencerId: "seq0", scope: "project" });
      assert.deepEqual(analyzerArgs.slice(analyzerArgs.indexOf("--execution-mode"), analyzerArgs.indexOf("--execution-mode") + 4),
        ["--execution-mode", "simulation", "--max-steps", "10000"]);
      await controller.refreshPreview();
      assert.equal(analyzerArgs[analyzerArgs.indexOf("--execution-mode") + 1], "simulation");
      controller.setProjectUri(vscode.Uri.file(join(outputDir, "other-project", "q1timeline.yml")));
      assert.deepEqual(controller.executionArgs(), ["--execution-mode", "concrete", "--max-steps", "10000"]);
      controller.dispose();
    });
  });

  it("ignores malformed, stale, and closed-preview replay requests", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      let refreshed = 0;
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.outputDir = "C:\\repo\\.q1timeline";
      controller.timelineIr = { sequencers: [{ id: "seq0" }] };
      controller.panel = { webview: { postMessage: () => {} }, dispose: () => {} };
      controller.refreshPreview = async () => { refreshed++; };
      await controller.handleWebviewMessage({ type: "prepareExecutionReplay", sequencerId: "seq0", extra: true });
      await controller.handleWebviewMessage({ type: "prepareExecutionReplay", sequencerId: " " });
      await controller.handleWebviewMessage({ type: "prepareExecutionReplay", sequencerId: "other-sequencer" });
      controller.setProjectUri(vscode.Uri.file("C:\\other-repo\\q1timeline.yml"));
      await controller.handleWebviewMessage({ type: "prepareExecutionReplay", sequencerId: "seq0" });
      controller.timelineIr = { sequencers: [{ id: "seq0" }] };
      controller.panel = undefined;
      await controller.handleWebviewMessage({ type: "prepareExecutionReplay", sequencerId: "seq0" });
      assert.equal(refreshed, 0);
      assert.equal(controller.executionReplayOverride, undefined);
      controller.dispose();
    });
  });

  it("renders a new partial simulation error trace while preserving failure handling for other modes", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-simulation-error-"));
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file(join(outputDir, "q1timeline.yml"));
      controller.outputDir = outputDir;
      let mode = "simulation";
      let rendered = 0;
      let refreshed = 0;
      let cleared = 0;
      controller.execFile = async () => {
        writeFileSync(join(outputDir, "timeline_ir.json"), JSON.stringify({
          events: [{ id: "underflow", kind: "underflow_warning" }],
          execution: { seq0: { mode, status: "error", reason: "RT queue empty" } },
        }));
        writeFileSync(join(outputDir, "diagnostics.json"), "[]");
        throw Object.assign(new Error("simulation error"), { stage: "analyzer", exitCode: 1, elapsedMs: 1,
          stdout: JSON.stringify({ schema_version: "0.2.0", status: "error", ok: false }), stderr: "" });
      };
      controller.runRender = async () => { rendered++; return { stdout: "", stderr: "", exitCode: 0, elapsedMs: 1 }; };
      controller.refreshWebview = async () => { refreshed++; };
      controller.clearPreviewAfterAnalyzerFailure = () => { cleared++; };
      controller.recordAnalyzerFailure = () => {};
      await controller.runAnalysis();
      assert.equal(rendered, 1);
      assert.equal(refreshed, 1);
      assert.equal(cleared, 0);
      mode = "analysis";
      await controller.runAnalysis();
      assert.equal(rendered, 1);
      assert.equal(refreshed, 1);
      assert.equal(cleared, 1);
      controller.dispose();
    });
  });

  it("adds branch and loop timeline chip overrides to analyzer args", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-control-overrides-"));
    const capturedArgs: string[][] = [];

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file(join(outputDir, "q1timeline.yml"));
      controller.outputDir = outputDir;
      controller.config = () => ({
        get: (key: string, fallback: any) => {
          if (key === "analyzer.extraArgs") {
            return ["--strict"];
          }
          return fallback;
        },
      });
      controller.execFile = async (_pythonPath: string, args: string[]) => {
        capturedArgs.push(args);
        return { stdout: "{}", stderr: "", exitCode: 0, elapsedMs: 1 };
      };
      controller.runRender = async () => ({ stdout: "", stderr: "", exitCode: 0, elapsedMs: 1 });
      controller.refreshWebview = async () => undefined;

      await controller.handleWebviewMessage({
        type: "setBranchAssumption",
        branchId: "seq0:branch:main.q1asm:4:jge:target",
        path: "both",
      });
      await controller.handleWebviewMessage({
        type: "setLoopPreview",
        loopKey: "seq0:loop:main.q1asm:2-8",
        visibleIterations: 3,
      });

      const latestArgs = capturedArgs[capturedArgs.length - 1];
      assert.ok(latestArgs);
      assert.deepEqual(
        latestArgs.slice(latestArgs.indexOf("--branch-assumption"), latestArgs.indexOf("--strict")),
        [
          "--branch-assumption",
          "seq0:branch:main.q1asm:4:jge:target=both",
          "--loop-preview",
          "seq0:loop:main.q1asm:2-8=3",
        ],
      );
      controller.dispose();
    });
  });

  it("selects full loop source lines including labels and the closing comment", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      for (const scenario of [
        { lines: [".DEF COUNT 3", "move 3,R0", "body:", "# loop body", "", "  wait 4", "  loop R0,@body # closing comment", "stop"],
          first: 6, last: 7, selectedFirst: 3, startMetadata: true },
        { lines: ["move 3,R0", "body: wait 4", "loop R0,@body # end of file"],
          first: 2, last: 3, selectedFirst: 2, startMetadata: false, textOnly: true },
      ]) {
        const setup = loopSourceNavigation(vscode, TimelineController, scenario.lines);
        const { controller, source, editor, decorated, revealed } = setup;
        if (scenario.textOnly) {
          const lineAt = setup.document.lineAt;
          setup.document.lineAt = (line) => ({ text: lineAt(line).text });
        }
        controller.timelineIr = {
          source_map: { by_event_id: { loop: source(1) } },
          events: [{ id: "loop", kind: "loop_block", source: source(scenario.first, { column: 3 }), meta: {
            ...(scenario.startMetadata ? { source_start: source(scenario.first) } : {}), source_end: source(scenario.last),
          } }],
        };
        await controller.openSourceForEvent("loop");
        assert.deepEqual([editor.selection.start.line, editor.selection.start.character, editor.selection.end.line, editor.selection.end.character],
          [scenario.selectedFirst - 1, 0, scenario.last - 1, scenario.lines[scenario.last - 1].length]);
        assert.equal(editor.selection.isEmpty, false);
        assert.equal(decorated.at(-1)[0], revealed.at(-1), "decoration and reveal must use the same full loop range");
        assert.deepEqual([revealed.at(-1).start.line, revealed.at(-1).end.line], [scenario.selectedFirst - 1, scenario.last - 1]);
        assert.deepEqual(setup.posted, [], "loop navigation must retain the clicked timeline event");
        controller.dispose();
      }
    });
  });

  it("selects independent inner and outer loop extents for static and playback summaries", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      for (const playback of [false, true]) {
        const lines = ["move 3,R0", "outer: wait 4", "move 2,R1", "inner:", "// inner body", "play 0,1,4", "loop R1,@inner", "loop R0,@outer # end"];
        const { controller, source, editor } = loopSourceNavigation(vscode, TimelineController, lines);
        controller.timelineIr = { events: [
          { id: "inner", kind: "loop_block", source: source(6), meta: { playback, source_start: source(6), source_end: source(7) } },
          { id: "outer", kind: "loop_block", source: source(2), meta: { playback, source_start: source(2), source_end: source(8) } },
          { id: "opcode", kind: "q1_issue", source: source(7, { column: 4 }), meta: { op: "loop", source_start: source(6), source_end: source(7) } },
        ] };
        await controller.openSourceForEvent("inner");
        assert.deepEqual([editor.selection.start.line, editor.selection.end.line, editor.selection.end.character], [3, 6, lines[6].length]);
        await controller.openSourceForEvent("outer");
        assert.deepEqual([editor.selection.start.line, editor.selection.end.line, editor.selection.end.character], [1, 7, lines[7].length]);
        await controller.openSourceForEvent("opcode");
        assert.equal(editor.selection.isEmpty, true, "the actual loop opcode keeps point navigation");
        assert.deepEqual([editor.selection.active.line, editor.selection.active.character], [6, 3]);
        controller.dispose();
      }
    });
  });

  it("unions reversed playback loop endpoints with explicit same-sequencer member sources", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const lines = ["move 3,R0", "latch: sub R0,1,R0", "jnz @body", "stop", "setup: move 1,R1", "jmp @body", "# gap", "body: wait 4", "jmp @latch # closing edge"];
      const { controller, source, sourcePath, editor } = loopSourceNavigation(vscode, TimelineController, lines);
      controller.timelineIr = {
        source_map: { by_event_id: { loop: source(1) } },
        events: [
          { id: "loop", kind: "loop_block", sequencer_id: "seq0", source: source(8), meta: {
            playback: true, source_start: source(8), source_end: source(3),
            first_iteration_event_ids: ["latch"], playback_event_ids: ["branch", "body", "edge", "other-sequencer", "other-file"],
          } },
          ...[["latch", 2], ["branch", 3], ["body", 8], ["edge", 9]].map(([id, line]) => ({ id, sequencer_id: "seq0", source: source(line) })),
          { id: "other-sequencer", sequencer_id: "seq1", source: source(1) },
          { id: "other-file", sequencer_id: "seq0", source: { file: `${sourcePath}.other`, line: 20 } },
          { id: "unrelated", sequencer_id: "seq0", source: source(1) },
        ],
      };
      await controller.openSourceForEvent("loop");
      assert.deepEqual([editor.selection.start.line, editor.selection.start.character, editor.selection.end.line, editor.selection.end.character],
        [1, 0, 8, lines[8].length]);
      controller.dispose();
    });
  });

  it("keeps point navigation when loop source metadata is missing, malformed or crosses files", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const { controller, source, editor } = loopSourceNavigation(vscode, TimelineController,
        ["move 3,R0", "body: wait 4", "loop R0,@body", "stop"]);
      for (const meta of [
        {}, { source_start: source(2) }, { source_start: source(0), source_end: source(3) },
        { source_start: source(2), source_end: source(2.5) },
        { source_start: { file: 4, line: 2 }, source_end: source(3) },
        { source_start: source(2), source_end: { file: "other.q1asm", line: 3 } },
      ]) {
        controller.timelineIr = {
          source_map: { by_event_id: { loop: source(4, { column: 3 }) } },
          events: [{ id: "loop", kind: "loop_block", source: source(2), meta }],
        };
        await controller.openSourceForEvent("loop");
        assert.equal(editor.selection.isEmpty, true);
        assert.deepEqual([editor.selection.active.line, editor.selection.active.character], [3, 2]);
      }
      controller.dispose();
    });
  });

  it("suppresses delayed full-loop source selection echoes without automatic timeline zoom", async () => {
    const vscode = createMockVscode();
    await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = loopSourceNavigation(vscode, TimelineController, ["move 3,R0", "body:", "wait 4", "loop R0,@body"]);
      const { controller, source, sourcePath, editor, posted } = setup;
      controller.timelineIr = {
        source_map: { by_source: { [`${sourcePath}:3`]: ["wait"], [`${sourcePath}:4`]: ["opcode"] } },
        events: [
          { id: "loop", kind: "loop_block", source: source(3), meta: { source_start: source(3), source_end: source(4) } },
          { id: "wait", source: source(3) }, { id: "opcode", source: source(4) },
        ],
      };
      editor.selection = sourceSelection(2, 3, 1);
      clock.advance(100);
      await controller.openSourceForEvent("loop");
      setup.activeEvent();
      setup.selectionEvent(vscode.TextEditorSelectionChangeKind.Command);
      setup.selectionEvent();
      clock.advance(1000);
      assert.deepEqual(posted, []);
      assert.equal(controller.sourceRangeTimer, undefined);
      assert.equal(editor.selection.isEmpty, false);

      setup.selectionEvent(vscode.TextEditorSelectionChangeKind.Mouse);
      clock.advance(250);
      assert.equal(posted.length, 1, "manual source selection must still resume the selected-range behavior");
      assert.equal(posted[0].zoomToRange, true);
      controller.dispose();
    }));
  });

  it("opens each feedback marker's endpoint and ignores unknown or malformed flows", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = loopSourceNavigation(vscode, TimelineController, ["wait 4", "fb_com_data 1", "wait 4", "fb_pop_data R0"]);
      const { controller, source, editor, decorated, posted } = setup;
      controller.timelineIr = {
        events: [{ id: "send", source: source(2, { column: 3 }) }, { id: "receive", source: source(4, { column: 5 }) }],
        feedback_flows: [{ id: "flow", from_event_id: "send", to_event_id: "receive" }],
      };
      for (const [role, line, column] of [["source", 1, 2], ["target", 3, 4]]) {
        await controller.handleWebviewMessage({ type: "feedbackFlowClick", flowId: "flow", role });
        assert.deepEqual([editor.selection.active.line, editor.selection.active.character], [line, column]);
        assert.deepEqual(decorated.at(-1).map((range) => range.start.line), [line]);
      }
      const decorationCount = decorated.length;
      await controller.handleWebviewMessage({ type: "feedbackFlowClick", flowId: "missing" });
      await controller.handleWebviewMessage({ type: "feedbackFlowClick", flowId: "flow", role: "both" });
      assert.equal(decorated.length, decorationCount);
      assert.deepEqual(posted, []);
      controller.dispose();
    });
  });

  it("highlights both discrete feedback lines in one file and clears them on an ordinary block jump", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = loopSourceNavigation(vscode, TimelineController, ["wait 4", "fb_com_data 1", "wait 4", "fb_pop_data R0"]);
      const { controller, source, sourcePath, editor, decorated, posted } = setup;
      let shown = 0;
      const showTextDocument = vscode.window.showTextDocument;
      vscode.window.showTextDocument = async (...args) => { shown += 1; return showTextDocument(...args); };
      controller.timelineIr = {
        source_map: { by_event_id: { send: source(2) }, by_source: { [`${sourcePath}:2`]: ["send"], [`${sourcePath}:4`]: ["receive"] } },
        events: [{ id: "send", source: source(1) }, { id: "receive", source: source(4) }],
        feedback_flows: [{ from_event_id: "send", to_event_id: "receive" }],
      };
      await controller.handleWebviewMessage({ type: "feedbackFlowClick", flowId: "send->receive" });
      assert.equal(shown, 1, "one file should be opened only once");
      assert.deepEqual(decorated.at(-1).map((range) => [range.start.line, range.end.line]), [[1, 1], [3, 3]]);
      assert.equal(editor.selection.isEmpty, true, "feedback navigation should not select the intervening source range");
      setup.activeEvent();
      setup.selectionEvent(vscode.TextEditorSelectionChangeKind.Command);
      setup.selectionEvent();
      assert.deepEqual(posted, [], "source reveal echoes must retain the feedback selection and hardware cursor");
      setup.selectionEvent(vscode.TextEditorSelectionChangeKind.Mouse);
      assert.deepEqual(posted.pop(), { type: "highlightEventIds", highlightEventIds: ["send"] });

      await controller.openSourceForEvent("receive");
      assert.deepEqual(decorated.at(-2), []);
      assert.deepEqual(decorated.at(-1).map((range) => range.start.line), [3]);
      controller.timelineIr.source_map.by_event_id.receive = source(2, { column: 5 });
      await controller.handleWebviewMessage({ type: "feedbackFlowClick", flowId: "send->receive" });
      assert.deepEqual(decorated.at(-1).map((range) => range.start.line), [1], "same-line endpoints should share one highlight");
      controller.dispose();
    });
  });

  it("keeps feedback highlights in both visible source editors and suppresses delayed echoes from either file", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = loopSourceNavigation(vscode, TimelineController, ["wait 4", "fb_com_data 1"]);
      const { controller, source, sourcePath, editor, decorated, posted } = setup;
      const receiverPath = "C:\\repo\\q1asm\\seq1.q1asm";
      const receiverDocument = { uri: vscode.Uri.file(receiverPath) };
      const receiverDecorations: any[] = [];
      let receiverSelection = sourceSelection(0, 0);
      const receiver = {
        document: receiverDocument, viewColumn: 3, revealRange() {},
        setDecorations(_type, ranges) { receiverDecorations.push(ranges); },
        get selection() { return receiverSelection; },
        set selection(value) { receiverSelection = value; setup.selectionEvent(3, receiver); },
      };
      const shown: any[] = [];
      vscode.workspace.textDocuments.push(receiverDocument);
      vscode.window.visibleTextEditors.push(receiver);
      vscode.window.showTextDocument = async (document, options) => {
        const target = document === receiverDocument ? receiver : editor;
        shown.push([document.uri.fsPath, options.viewColumn]);
        setup.activeEvent(target);
        return target;
      };
      controller.projectRelatedPaths.add(receiverPath);
      controller.timelineIr = {
        source_map: {},
        events: [{ id: "send", source: source(2) }, { id: "receive", source: { file: receiverPath, line: 4, column: 3 } }],
        feedback_flows: [{ id: "flow", from_event_id: "send", to_event_id: "receive" }],
      };
      await controller.handleWebviewMessage({ type: "feedbackFlowClick", flowId: "flow" });
      assert.deepEqual(shown, [[sourcePath, 1], [receiverPath, 3]]);
      assert.deepEqual(decorated.at(-1).map((range) => range.start.line), [1]);
      assert.deepEqual(receiverDecorations.at(-1).map((range) => range.start.line), [3]);
      for (const target of [editor, receiver]) {
        setup.activeEvent(target);
        setup.selectionEvent(vscode.TextEditorSelectionChangeKind.Command, target);
        setup.selectionEvent(undefined, target);
      }
      assert.deepEqual(posted, [], "neither file's reveal may move the hardware cursor or timeline selection");
      setup.selectionEvent(vscode.TextEditorSelectionChangeKind.Mouse, receiver);
      assert.deepEqual(posted.pop(), { type: "highlightEventIds", highlightEventIds: ["receive"] });
      await controller.openSourceForEvent("send");
      assert.deepEqual(receiverDecorations.at(-1), [], "ordinary block navigation must clear the other file too");
      assert.deepEqual(decorated.at(-1).map((range) => range.start.line), [1]);
      await controller.handleWebviewMessage({ type: "feedbackFlowClick", flowId: "flow" });
      controller.dispose();
      assert.deepEqual(decorated.at(-1), []);
      assert.deepEqual(receiverDecorations.at(-1), []);
    });
  });

  it("restores feedback highlights when same-group tabs create new editor instances", async () => {
    const vscode = createMockVscode();
    let visibleHandler: any;
    vscode.window.onDidChangeVisibleTextEditors = (handler) => { visibleHandler = handler; return { dispose() {} }; };
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = loopSourceNavigation(vscode, TimelineController, ["wait 4", "fb_com_data 1"]);
      const { controller, source, sourcePath, editor, posted } = setup;
      const receiverPath = "C:\\repo\\q1asm\\seq1.q1asm";
      const receiverDocument = { uri: vscode.Uri.file(receiverPath) };
      let selection = sourceSelection(0, 0);
      const receiver = {
        document: receiverDocument, viewColumn: 1, setDecorations() {}, revealRange() {},
        get selection() { return selection; },
        set selection(value) { selection = value; setup.selectionEvent(3, receiver); },
      };
      vscode.workspace.textDocuments.push(receiverDocument);
      vscode.window.tabGroups.all = [{ viewColumn: 1, tabs: [sourcePath, receiverPath].map((file) => ({ input: { uri: vscode.Uri.file(file) } })) }];
      vscode.window.showTextDocument = async (document, options) => {
        assert.equal(options.viewColumn, 1, "both tabs must keep their existing editor group");
        const target = document === receiverDocument ? receiver : editor;
        vscode.window.visibleTextEditors = [target];
        visibleHandler([target]);
        setup.activeEvent(target);
        return target;
      };
      controller.timelineIr = {
        source_map: {},
        events: [{ id: "send", source: source(2) }, { id: "receive", source: { file: receiverPath, line: 4 } }],
        feedback_flows: [{ id: "flow", from_event_id: "send", to_event_id: "receive" }],
      };
      await controller.handleWebviewMessage({ type: "feedbackFlowClick", flowId: "flow" });
      const restored: any[] = [];
      const reopened = {
        document: setup.document, viewColumn: 1, selection: editor.selection,
        setDecorations(_type, ranges) { restored.push(ranges); },
      };
      vscode.window.visibleTextEditors = [reopened];
      visibleHandler([reopened]);
      setup.activeEvent(reopened);
      assert.deepEqual(restored.at(-1).map((range) => range.start.line), [1], "a new TextEditor must recover the hidden sender's highlight");
      assert.deepEqual(posted, [], "reopening a highlighted tab must preserve feedback selection");
      await controller.openSourceForEvent("receive");
      assert.deepEqual(restored.at(-1), []);
      const restoredCount = restored.length;
      visibleHandler([reopened]);
      setup.activeEvent(reopened);
      assert.equal(restored.length, restoredCount, "an ordinary block jump must also clear saved hidden-tab highlights");
      controller.dispose();
    });
  });

  it("preserves an RT click through source reveal echoes and resumes manual source-follow", async () => {
    const vscode = createMockVscode();
    vscode.TextEditorSelectionChangeKind = { Keyboard: 1, Mouse: 2, Command: 3 };
    const posted: any[] = [];
    let selectionHandler: any;
    let activeHandler: any;
    vscode.window.onDidChangeTextEditorSelection = (handler: any) => { selectionHandler = handler; return { dispose() {} }; };
    vscode.window.onDidChangeActiveTextEditor = (handler: any) => { activeHandler = handler; return { dispose() {} }; };
    const sourcePath = "C:\\repo\\golden_section.q1asm";
    const document = { uri: vscode.Uri.file(sourcePath) };
    let selection: any = { active: { line: 0, character: 0 } };
    const editor = {
      document, viewColumn: 2, setDecorations() {}, revealRange() {},
      get selection() { return selection; },
      set selection(value) { selection = value; selectionHandler({ textEditor: editor, kind: vscode.TextEditorSelectionChangeKind.Command }); },
    };
    vscode.workspace.openTextDocument = async () => document;
    vscode.window.showTextDocument = async () => { activeHandler(editor); return editor; };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.projectRelatedPaths = new Set([sourcePath]);
      controller.panel = { webview: { postMessage: (message: any) => posted.push(message) }, dispose() {} };
      const source = { file: sourcePath, line: 7, column: 9 };
      controller.timelineIr = {
        source_map: { by_source: { [`${sourcePath}:7`]: ["golden_section:e5", "golden_section:e6"] } },
        events: [
          { id: "golden_section:e5", kind: "q1_issue", t0: 20, t1: 24, source, meta: { op: "fb_acq_iq_id" } },
          { id: "golden_section:e6", kind: "fb_acq_iq_id", t0: 0, t1: 12, source, meta: { q1_issue_event_id: "golden_section:e5" } },
        ],
      };

      await controller.openSourceForEvent("golden_section:e6");
      assert.equal(editor.selection.active.line, 6);
      assert.equal(editor.selection.active.character, 8);
      assert.deepEqual(posted, [], "RT source navigation must not replace its selection or viewport with the Q1 issue");
      activeHandler(editor);
      selectionHandler({ textEditor: editor, kind: vscode.TextEditorSelectionChangeKind.Command });
      selectionHandler({ textEditor: editor });
      assert.deepEqual(posted, [], "delayed source echoes must preserve the clicked RT event too");

      selectionHandler({ textEditor: editor, kind: vscode.TextEditorSelectionChangeKind.Mouse });
      assert.deepEqual(posted, [{ type: "highlightEventIds", highlightEventIds: ["golden_section:e5", "golden_section:e6"] }],
        "a direct source click must still reveal the source's timeline events");
      controller.dispose();
    });
  });

  async function webviewSourceRequests(TimelineController, vscode) {
    const errors: string[] = [], output: string[] = [], shown: any[] = [], requests: Promise<any>[] = [];
    let receive: any, close: any;
    vscode.window.showErrorMessage = (message) => { errors.push(message); return new Promise(() => {}); };
    vscode.window.createOutputChannel = () => ({ appendLine: (line) => output.push(line), show() {}, dispose() {} });
    const createPanel = vscode.window.createWebviewPanel;
    vscode.window.createWebviewPanel = (...args) => {
      const panel = createPanel(...args);
      panel.webview.onDidReceiveMessage = (listener) => { receive = listener; return { dispose() {} }; };
      panel.onDidDispose = (listener) => { close = listener; return { dispose() {} }; };
      return panel;
    };
    const root = mkdtempSync(join(tmpdir(), "q1-webview-source-error-"));
    const file = join(root, "source.q1asm");
    writeFileSync(file, "wait 4\nstop\n");
    vscode.workspace.openTextDocument = async (uri) => {
      const text = await require("node:fs").promises.readFile(uri.fsPath, "utf8");
      return { uri, lineCount: text.split("\n").length, lineAt: (line) => ({ text: text.split("\n")[line] }) };
    };
    vscode.window.showTextDocument = async (document) => {
      const editor = { document, viewColumn: 1, selection: sourceSelection(0, 0, 0), setDecorations() {}, revealRange() {} };
      shown.push(editor); return editor;
    };
    const controller = new TimelineController(createContext());
    controller.projectUri = vscode.Uri.file(join(root, "q1timeline.yml"));
    controller.outputDir = root;
    controller.timelineIr = { events: [{ id: "e", source: { file, line: 1 } }],
      feedback_flows: [{ id: "flow", from_event_id: "e", to_event_id: "e" }] };
    controller.analyzerDiagnostics = [{ source: { file, line: 1 } }];
    for (const method of ["openSourceForEvent", "openSourceForDiagnostic", "openSourceForFeedbackFlow"]) {
      const actual = controller[method].bind(controller);
      controller[method] = (...args) => {
        const request = actual(...args);
        requests.push(request);
        // Observe detached pre-fix promises so the regression reports the missing UI rather than leaking into Mocha.
        void request.catch(() => {});
        return request;
      };
    }
    await controller.createPreviewPanel();
    return { controller, file, root, errors, output, shown, receive: (message) => receive(message), close: () => close(),
      async settle() { for (let index = 0; index < requests.length; index += 1) await Promise.allSettled([requests[index]]); } };
  }

  for (const type of ["eventClick", "diagnosticClick", "sourceClick", "feedbackFlowClick"]) {
    it(`reports a missing source from the registered ${type} callback and accepts the next source request`, async () => {
      const vscode = createMockVscode();
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const setup = await webviewSourceRequests(TimelineController, vscode);
        const { controller, file, errors, output, shown } = setup;
        require("node:fs").unlinkSync(file);
        const message = type === "eventClick" ? { type, eventId: "e" }
          : type === "diagnosticClick" ? { type, diagnosticIndex: 0 }
          : type === "feedbackFlowClick" ? { type, flowId: "flow", role: "source" }
          : { type, file, line: 1, column: 1, preserveHardwareCursor: true };
        try {
          await assert.doesNotReject(setup.receive(message), "The registered webview callback must handle a current file-open failure");
          await setup.settle();
          assert.equal(errors.length, 1, "The missing source must be explained to the user");
          assert.ok(errors[0].includes(file));
          assert.ok(output.some((line) => line.includes(file) && /could not open source/i.test(line)));
          writeFileSync(file, "wait 4\nstop\n");
          await setup.receive({ type: "sourceClick", file, line: 2, column: 1, preserveHardwareCursor: true });
          assert.equal(shown.at(-1).document.uri.fsPath, file);
          assert.equal(shown.at(-1).selection.active.line, 1);
          assert.equal(errors.length, 1, "Source recovery must not wait for the previous error notification to be dismissed");
        } finally { await setup.settle(); controller.dispose(); }
      });
    });
  }

  for (const replacement of ["a newer source request", "a different project", "the preview closing"]) {
    it(`ignores a missing-source failure that completes after ${replacement}`, async () => {
      const vscode = createMockVscode();
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const setup = await webviewSourceRequests(TimelineController, vscode);
        const { controller, file, root, errors, shown } = setup;
        require("node:fs").unlinkSync(file);
        const openTextDocument = vscode.workspace.openTextDocument;
        let release!: () => void;
        let started!: () => void;
        const waiting = new Promise<void>((resolve) => { started = resolve; });
        vscode.workspace.openTextDocument = async (uri) => {
          if (uri.fsPath === file) await new Promise<void>((resolve) => { release = resolve; started(); });
          return openTextDocument(uri);
        };
        let pending: Promise<void> | undefined;
        try {
          pending = setup.receive({ type: "sourceClick", file, line: 1, column: 1, preserveHardwareCursor: true });
          // Observe the pre-fix rejection while another user action supersedes the request.
          void pending.catch(() => {});
          await waiting;
          if (replacement === "a newer source request") {
            const next = join(root, "next.q1asm");
            writeFileSync(next, "stop\n");
            await setup.receive({ type: "sourceClick", file: next, line: 1, column: 1, preserveHardwareCursor: true });
            assert.equal(shown.at(-1).document.uri.fsPath, next);
          } else if (replacement === "a different project") {
            const folderB = mkdtempSync(join(tmpdir(), "q1-webview-source-error-b-"));
            const projectFile = join(folderB, "q1timeline.yml");
            writeFileSync(projectFile, "sequencers: []\n");
            controller.runAnalysis = async () => {};
            await controller.openTarget({ projectFile });
          } else setup.close();
          release();
          await assert.doesNotReject(pending, "An obsolete source failure must be consumed without reaching the webview event emitter");
          assert.deepEqual(errors, [], "An old source failure must not display a notification over the current target");
        } finally { release?.(); await pending?.catch(() => {}); await setup.settle(); controller.dispose(); }
      });
    });
  }

  for (const replacement of ["another source click", "a newer line in the same file", "a project switch", "a source edit", "a superseded feedback flow", "a newer click during source display"]) {
    it(`drops pending source navigation after ${replacement}`, async () => {
      const vscode = createMockVscode();
      const notifications = sourceFileNotifications(vscode);
      const inFlightShow = replacement === "a newer click during source display";
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const { controller } = fileEnvironmentController(TimelineController, vscode);
        controller.config = () => ({ get: (key, fallback) => key === "updateMode" ? "manual" : fallback });
        const coldPath = join(controller.outputDir, "cold.q1asm"), hotPath = join(controller.outputDir, "hot.q1asm");
        const documents = [coldPath, hotPath].map((file) => {
          writeFileSync(file, "wait 4\nstop\n");
          return { uri: vscode.Uri.file(file), lineCount: 3, version: 1, isDirty: false, text: "wait 4\nstop\n",
            getText() { return this.text; }, lineAt(line) { return { text: this.text.split("\n")[line] }; } };
        });
        const decorated = [[], []], shown: string[] = [];
        const editors = documents.map((document, index) => ({ document, viewColumn: 1, selection: sourceSelection(0, 0, 0),
          setDecorations(_type, ranges) { decorated[index].push(ranges); }, revealRange() {} }));
        vscode.workspace.textDocuments = [documents[1]];
        vscode.window.visibleTextEditors = [editors[1]];
        vscode.TabInputText = class { constructor(public uri) {} };
        vscode.window.tabGroups.all = [{ viewColumn: 1, tabs: [{ input: new vscode.TabInputText(documents[1].uri) }] }];
        controller.projectRelatedPaths = new Set([coldPath, hotPath]);
        let release!: () => void;
        let started!: () => void;
        const waiting = new Promise<void>((resolve) => { started = resolve; });
        let coldReads = 0;
        vscode.workspace.openTextDocument = async (uri) => {
          assert.equal(uri.fsPath, coldPath);
          if (++coldReads === 1 && !inFlightShow) await new Promise<void>((resolve) => { release = resolve; started(); });
          if (!vscode.workspace.textDocuments.includes(documents[0])) vscode.workspace.textDocuments.push(documents[0]);
          return documents[0];
        };
        vscode.window.showTextDocument = async (document) => {
          if (document === documents[0] && inFlightShow) await new Promise<void>((resolve) => { release = resolve; started(); });
          shown.push(document.uri.fsPath);
          return document === documents[0] ? editors[0] : editors[1];
        };
        const click = (file, line) => controller.handleWebviewMessage({ type: "sourceClick", file, line, column: 1, preserveHardwareCursor: true });
        let first: Promise<void> | undefined;
        let newer: Promise<void> | undefined;
        try {
          if (replacement === "a superseded feedback flow") {
            controller.timelineIr = { events: [{ id: "send", source: { file: coldPath, line: 2 } }, { id: "receive", source: { file: hotPath, line: 2 } }],
              feedback_flows: [{ id: "flow", from_event_id: "send", to_event_id: "receive" }] };
            first = controller.handleWebviewMessage({ type: "feedbackFlowClick", flowId: "flow" });
          } else first = click(coldPath, 2);
          await waiting;
          if (replacement === "a project switch") {
            const folderB = mkdtempSync(join(tmpdir(), "q1-source-navigation-b-"));
            const projectFile = join(folderB, "q1timeline.yml");
            writeFileSync(projectFile, "sequencers: []\n");
            await controller.openTarget({ projectFile });
          } else if (replacement === "a source edit") {
            documents[1].text = "wait 8\nstop\n";
            documents[1].isDirty = true;
            documents[1].version += 1;
            notifications.edit({ document: documents[1], contentChanges: [{ text: "8" }] });
          } else if (inFlightShow) newer = click(hotPath, 1);
          else await click(replacement === "a newer line in the same file" ? coldPath : hotPath, 1);
          if (inFlightShow) await new Promise((resolve) => setImmediate(resolve));
          release(); await first; await newer;
          const expected = inFlightShow ? [coldPath, hotPath] : replacement === "a project switch" || replacement === "a source edit" ? []
            : [replacement === "a newer line in the same file" ? coldPath : hotPath];
          assert.deepEqual(shown, expected, "An obsolete document load must not reclaim focus or continue another endpoint");
          if (expected.length) assert.equal(editors[replacement === "a newer line in the same file" ? 0 : 1].selection.active.line, 0);
          if (replacement === "a source edit") {
            assert.equal(controller.hardwareSourceReveal, undefined, "A dropped reveal must not suppress future source-follow");
            await click(hotPath, 1);
            assert.deepEqual(shown, [hotPath], "A new explicit source click is still allowed in a dirty document");
          }
        } finally { release?.(); await first; await newer; controller.dispose(); }
      });
    });
  }

  it("preserves a hardware source reveal while allowing subsequent manual source-follow", async () => {
    const vscode = createMockVscode();
    vscode.TextEditorSelectionChangeKind = { Keyboard: 1, Mouse: 2, Command: 3 };
    const posted: any[] = [];
    let selectionHandler: any;
    let activeHandler: any;
    vscode.window.onDidChangeTextEditorSelection = (handler: any) => { selectionHandler = handler; return { dispose() {} }; };
    vscode.window.onDidChangeActiveTextEditor = (handler: any) => { activeHandler = handler; return { dispose() {} }; };
    const sourcePath = "C:\\repo\\orange_drive.q1asm";
    const document = { uri: vscode.Uri.file(sourcePath) };
    let selection: any = { active: { line: 63, character: 4 } };
    const editor = {
      document, viewColumn: 2, setDecorations() {}, revealRange() {},
      get selection() { return selection; },
      set selection(value) { selection = value; selectionHandler({ textEditor: editor }); },
    };
    vscode.workspace.openTextDocument = async () => document;
    vscode.window.showTextDocument = async () => { activeHandler(editor); return editor; };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.projectRelatedPaths = new Set([sourcePath]);
      controller.panel = { webview: { postMessage: (message: any) => posted.push(message) }, dispose() {} };
      controller.timelineIr = {
        source_map: { by_source: { [`${sourcePath}:64`]: ["orange_drive:e63"], [`${sourcePath}:66`]: ["orange_drive:e65"] } },
        events: [{ id: "orange_drive:e63" }, { id: "orange_drive:e65" }],
      };

      await controller.openSourceLocation({ file: sourcePath, line: 66, column: 5 }, { preserveHardwareCursor: true });
      assert.equal(editor.selection.active.line, 65);
      assert.equal(editor.selection.active.character, 4);
      assert.deepEqual(posted, [], "opening the source must not echo a highlight that rewinds the hardware cursor");
      activeHandler(editor);
      selectionHandler({ textEditor: editor, kind: vscode.TextEditorSelectionChangeKind.Command });
      selectionHandler({ textEditor: editor });
      assert.deepEqual(posted, [], "delayed automatic events at the revealed position must also be ignored");

      controller.highlightActiveSourceLine(editor);
      assert.deepEqual(posted.pop(), { type: "highlightEventIds", highlightEventIds: ["orange_drive:e65"] }, "explicit reveal remains available");
      selectionHandler({ textEditor: editor, kind: vscode.TextEditorSelectionChangeKind.Mouse });
      assert.deepEqual(posted.pop(), { type: "highlightEventIds", highlightEventIds: ["orange_drive:e65"] }, "manual click at the same position restores source-follow");
      editor.selection = { active: { line: 63, character: 4 } };
      assert.deepEqual(posted.pop(), { type: "highlightEventIds", highlightEventIds: ["orange_drive:e63"] });

      await controller.openSourceLocation({ file: sourcePath, line: 66, column: 5 }, { preserveHardwareCursor: true });
      assert.deepEqual(posted, []);
      editor.selection = { active: { line: 63, character: 4 } };
      assert.deepEqual(posted.pop(), { type: "highlightEventIds", highlightEventIds: ["orange_drive:e63"] }, "a different position without a change kind is not an echo");
      controller.dispose();
    });
  });

  it("clears hardware reveal suppression when source opening fails", async () => {
    const vscode = createMockVscode();
    let selectionHandler: any;
    vscode.window.onDidChangeTextEditorSelection = (handler: any) => { selectionHandler = handler; return { dispose() {} }; };
    vscode.workspace.openTextDocument = async () => { throw new Error("cannot open source"); };
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      let followed = false;
      controller.highlightActiveSourceLine = () => { followed = true; };
      await assert.rejects(controller.openSourceLocation({ file: "C:\\repo\\source.q1asm", line: 66, column: 5 }, { preserveHardwareCursor: true }), /cannot open source/);
      selectionHandler({ textEditor: { document: { uri: vscode.Uri.file("C:\\repo\\source.q1asm") }, selection: { active: { line: 65, character: 4 } } } });
      assert.equal(followed, true);
      controller.dispose();
    });
  });

  it("passes hardware source cursor preservation through the validated message", async () => {
    const vscode = createMockVscode();
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      let opened: any;
      controller.openSourceLocation = async (...args: any[]) => { opened = args; };
      await controller.handleWebviewMessage({ type: "sourceClick", file: "orange_drive.q1asm", line: 66, column: 5, preserveHardwareCursor: true });
      assert.deepEqual(opened, [{ file: "orange_drive.q1asm", line: 66, column: 5 }, { preserveHardwareCursor: true }]);
      controller.dispose();
    });
  });
  for (const supersededBy of ["another project", "a newer analysis", "controller disposal"]) {
    it(`ignores a missing-analyzer notification action after ${supersededBy}`, async () => {
      const vscode = createMockVscode();
      const checks: any[] = [];
      let chooseAction!: (action?: string) => void;
      let started!: () => void;
      const notifying = new Promise<void>((resolve) => { started = resolve; });
      vscode.window.showErrorMessage = async (message) => {
        if (!message.includes("Q1Lens is unavailable")) return undefined;
        return new Promise((resolve) => { chooseAction = resolve; started(); });
      };
      vscode.commands.executeCommand = async (...args) => { checks.push(args); };
      await withMockedVscode(vscode, async ({ TimelineController }) => {
        const { controller } = fileEnvironmentController(TimelineController, vscode);
        const execute = controller.execFile.bind(controller);
        let missing = true;
        controller.execFile = async (...args) => {
          if (missing && args[5] === "analyzer") throw new Error("No module named q1lens");
          return execute(...args);
        };
        try {
          const analyzing = controller.runAnalysis();
          await notifying;
          await analyzing;
          missing = false;
          if (supersededBy === "another project") {
            const root = mkdtempSync(join(tmpdir(), "q1-late-analyzer-notification-"));
            const projectFile = join(root, "q1timeline.yml");
            writeFileSync(projectFile, "sequencers: []\n");
            await controller.openTarget({ projectFile });
          } else if (supersededBy === "a newer analysis") {
            await controller.refreshPreview();
          } else {
            controller.dispose();
          }
          chooseAction("Check Python Environment");
          await new Promise((resolve) => setImmediate(resolve));
          assert.deepEqual(checks, [], "An obsolete failure notification must not check or refresh the current project");
        } finally {
          chooseAction?.();
          controller.dispose();
        }
      });
    });
  }

  it("offers a source-scoped Python environment check with a retry when the analyzer is missing", async () => {
    const vscode = createMockVscode();
    const calls: any[] = [];
    vscode.commands.executeCommand = async (...args: any[]) => calls.push(args);
    vscode.window.showErrorMessage = async (message: string, action: string) => {
      assert.match(message, /Q1Lens is unavailable/);
      assert.equal(action, "Check Python Environment");
      return action;
    };
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const sourceUri = vscode.Uri.file("C:\\repo\\seq0.q1asm");
      controller.singleFileUri = sourceUri;
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      await controller.showAnalyzerMissingMessage(new Error("No module named q1lens"));
      assert.deepEqual(calls, [["q1lens.checkEnvironment", "q1timeline.refreshPreview", sourceUri]]);
      controller.dispose();
    });
  });

  it("snapshots a backwards selected block before opening and excludes an end at column zero", async () => {
    const vscode = createMockVscode();
    const sourcePath = "C:\\repo\\q1asm\\seq0.q1asm";
    const posted: any[] = [];
    const editor = {
      document: { uri: vscode.Uri.file(sourcePath) },
      selection: { start: { line: 2, character: 3 }, end: { line: 4, character: 0 },
        anchor: { line: 4, character: 0 }, active: { line: 2, character: 3 }, isEmpty: false },
    };
    vscode.window.activeTextEditor = editor;
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.findProjectFileUpward = () => undefined;
      controller.openPreview = async (options) => {
        assert.equal(options.sourceUri.fsPath, sourcePath);
        assert.deepEqual(controller.pendingTarget, { q1asmFile: sourcePath, line: 3, endLine: 4, zoomToRange: true });
        editor.selection = { ...editor.selection, start: { line: 8, character: 0 }, end: { line: 9, character: 1 } };
        vscode.window.activeTextEditor = undefined;
        await Promise.resolve();
        controller.panel = { webview: { postMessage: (message) => posted.push(message) }, dispose: () => {} };
        controller.timelineIr = { events: [
          { id: "seq0:e3", source: { file: sourcePath, line: 3 } },
          { id: "seq0:e4", source: { file: sourcePath, line: 4 } },
          { id: "seq0:e5", source: { file: sourcePath, line: 5 } },
        ] };
        await controller.revealPendingTarget();
      };
      await controller.zoomToSelection();
      assert.deepEqual(posted, [{ type: "highlightEventIds", highlightEventIds: ["seq0:e3", "seq0:e4"],
        zoomToRange: true, sourceRange: { file: sourcePath, startLine: 3, endLine: 4 } }]);
      assert.equal(controller.pendingTarget, undefined);
      controller.dispose();
    });
  });

  it("switches projects and refreshes the saved program before revealing a selected block", async () => {
    const vscode = createMockVscode();
    const sourcePath = "C:\\repo\\project-b\\q1asm\\seq0.q1asm";
    const projectPath = "C:\\repo\\project-b\\q1timeline.yml";
    const posted: any[] = [];
    vscode.window.activeTextEditor = { document: { uri: vscode.Uri.file(sourcePath) },
      selection: { start: { line: 4, character: 0 }, end: { line: 6, character: 2 }, isEmpty: false } };
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\project-a\\q1timeline.yml");
      controller.panel = { webview: { postMessage: (message) => posted.push(message) }, reveal: () => {}, dispose: () => {} };
      controller.timelineIr = { events: [{ id: "old", source: { file: "q1asm/seq0.q1asm", line: 5 } }] };
      controller.findProjectFileUpward = () => vscode.Uri.file(projectPath);
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      let analysisCount = 0;
      controller.runAnalysis = async () => {
        analysisCount += 1;
        assert.equal(controller.timelineIr, undefined);
        assert.deepEqual(controller.pendingTarget, { q1asmFile: sourcePath, line: 5, endLine: 7, zoomToRange: true });
        controller.timelineIr = { events: [{ id: "new", source: { file: sourcePath, line: 7 } }] };
        await controller.revealPendingTarget();
      };
      await controller.zoomToSelection();
      assert.equal(controller.projectUri.fsPath, projectPath);
      assert.equal(analysisCount, 1);
      assert.deepEqual(posted, [{ type: "highlightEventIds", highlightEventIds: ["new"], zoomToRange: true,
        sourceRange: { file: sourcePath, startLine: 5, endLine: 7 } }]);
      controller.dispose();
    });
  });

  it("keeps a selected block queued until a fresher in-flight analysis completes", async () => {
    const vscode = createMockVscode();
    const sourcePath = "C:\\repo\\q1asm\\seq0.q1asm";
    const posted: any[] = [];
    vscode.window.activeTextEditor = { document: { uri: vscode.Uri.file(sourcePath) },
      selection: { start: { line: 2, character: 0 }, end: { line: 3, character: 2 }, isEmpty: false } };
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.panel = { webview: { postMessage: (message) => posted.push(message) }, reveal: () => {}, dispose: () => {} };
      controller.timelineIr = { events: [{ id: "old", source: { file: sourcePath, line: 3 } }] };
      controller.findProjectFileUpward = () => controller.projectUri;
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.analysisInFlight = true;
      await controller.zoomToSelection();
      assert.equal(controller.analysisQueued, true);
      assert.equal(controller.pendingTarget?.endLine, 4);
      assert.equal(posted.some((message) => message.type === "highlightEventIds"), false);
      controller.analysisInFlight = false;
      controller.analysisQueued = false;
      controller.timelineIr = { events: [{ id: "fresh", source: { file: sourcePath, line: 4 } }] };
      await controller.revealPendingTarget();
      assert.deepEqual(posted.find((message) => message.type === "highlightEventIds")?.highlightEventIds, ["fresh"]);
      assert.equal(controller.pendingTarget, undefined);
      controller.dispose();
    });
  });

  it("reports missing selection and unsaved Q1ASM without saving or opening a preview", async () => {
    const vscode = createMockVscode();
    const messages: string[] = [];
    vscode.window.showInformationMessage = async (message) => { messages.push(message); };
    const selection = { start: { line: 1, character: 0 }, end: { line: 2, character: 1 }, isEmpty: false };
    let saves = 0;
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      let opens = 0;
      controller.openPreview = async () => { opens += 1; };
      for (const editor of [undefined,
        { document: { uri: vscode.Uri.file("C:\\repo\\notes.txt") }, selection },
        { document: { uri: vscode.Uri.file("C:\\repo\\seq0.q1asm") }, selection: { ...selection, isEmpty: true } },
        { document: { uri: vscode.Uri.file("C:\\repo\\seq0.q1asm"), isDirty: true, save: () => { saves += 1; } }, selection },
      ]) {
        vscode.window.activeTextEditor = editor;
        await controller.zoomToSelection();
      }
      assert.equal(opens, 0);
      assert.equal(saves, 0);
      assert.equal(messages.length, 4);
      assert.match(messages[3], /Save the Q1ASM file/);
      assert.equal(controller.pendingTarget, undefined);
      controller.dispose();
    });
  });

  it("reports selected source lines with no current mapped execution", async () => {
    const vscode = createMockVscode();
    const sourcePath = "C:\\repo\\seq0.q1asm";
    const messages: string[] = [];
    const posted: any[] = [];
    vscode.window.showInformationMessage = async (message) => { messages.push(message); };
    vscode.window.activeTextEditor = { document: { uri: vscode.Uri.file(sourcePath) },
      selection: { start: { line: 1, character: 0 }, end: { line: 1, character: 3 }, isEmpty: false } };
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.findProjectFileUpward = () => undefined;
      controller.panel = { webview: { postMessage: (message) => posted.push(message) }, dispose: () => {} };
      controller.openPreview = async () => { await controller.revealPendingTarget(); };
      for (const timelineIr of [{ events: [] },
        { events: [{ id: "other", source: { file: "other.q1asm", line: 2 } }] },
        { events: [{ id: "removed", source: { file: sourcePath, line: 2 }, meta: { diff_status: "removed" } }],
          source_map: { by_source: { [`${sourcePath}:2`]: ["removed", "missing"] } } },
      ]) {
        controller.timelineIr = timelineIr;
        await controller.zoomToSelection();
        assert.equal(controller.pendingTarget, undefined);
      }
      assert.deepEqual(posted, []);
      assert.equal(messages.length, 3);
      assert.ok(messages.every((message) => /No executed timeline events/.test(message)));
      controller.dispose();
    });
  });

  it("debounces automatic selected-block zoom and normalizes backwards column-zero ranges", async () => {
    const vscode = createMockVscode();
    await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
      const { controller, editor, posted, sourcePath } = automaticSourceFollow(vscode, TimelineController);
      editor.selection = sourceSelection(1, 3, 1);
      controller.followSourceEditor(editor);
      assert.deepEqual(posted, [], "a selected block must not send an immediate caret highlight");
      clock.advance(100);
      editor.selection = sourceSelection(2, 4, 0, true);
      controller.followSourceEditor(editor);
      clock.advance(249);
      assert.deepEqual(posted, []);
      clock.advance(1);
      assert.deepEqual(posted, [{ type: "highlightEventIds", highlightEventIds: ["e3", "e4"], zoomToRange: true,
        sourceRange: { file: sourcePath, startLine: 3, endLine: 4 } }]);
      clock.advance(1000);
      assert.equal(posted.length, 1);
      controller.dispose();
    }));
  });

  it("silently ignores automatic selection zoom for unrelated, dirty, stale, or unmapped source", async () => {
    const vscode = createMockVscode();
    const notices: string[] = [];
    vscode.window.showInformationMessage = async (message) => { notices.push(message); };
    await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
      for (const [reason, change] of [
        ["unrelated file", ({ controller }) => controller.projectRelatedPaths.clear()],
        ["non-Q1ASM file", ({ controller, editor }) => { editor.document.uri = vscode.Uri.file("C:\\repo\\notes.txt"); controller.projectRelatedPaths.add(editor.document.uri.fsPath); }],
        ["dirty source", ({ editor }) => { editor.document.isDirty = true; }],
        ["dirty related source", ({ editor }) => { vscode.workspace.textDocuments = [{ uri: editor.document.uri, isDirty: true }]; }],
        ["saved but stale analysis", ({ controller }) => { controller.sourceRevision += 1; }],
        ["analysis running", ({ controller }) => { controller.analysisInFlight = true; }],
        ["analysis queued", ({ controller }) => { controller.analysisQueued = true; }],
        ["no mapped execution", ({ controller }) => { controller.timelineIr.events = []; }],
        ["missing source map", ({ controller }) => { delete controller.timelineIr.source_map; }],
      ]) {
        const setup = automaticSourceFollow(vscode, TimelineController);
        change(setup);
        setup.controller.followSourceEditor(setup.editor);
        clock.advance(250);
        assert.deepEqual(setup.posted, [], reason);
        setup.controller.dispose();
        vscode.workspace.textDocuments = [];
      }
      assert.deepEqual(notices, [], "automatic selection changes must not display repeated notifications");
    }));
  });

  it("discards delayed selected-block zoom when its source, selection, editor, or view changed", async () => {
    const vscode = createMockVscode();
    await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
      for (const [reason, change] of [
        ["document version", ({ editor }) => { editor.document.version += 1; }],
        ["selection", ({ editor }) => { editor.selection = sourceSelection(3, 4, 1); }],
        ["active editor", ({ editor }) => { vscode.window.activeTextEditor = { ...editor }; }],
        ["panel", ({ controller }) => { controller.panel = { ...controller.panel }; }],
        ["timeline IR", ({ controller }) => { controller.timelineIr = { ...controller.timelineIr }; }],
        ["webview readiness", ({ controller }) => { controller.webviewReady = false; }],
        ["source revision", ({ controller }) => controller.invalidateSourceAnalysis()],
        ["hardware source reveal", ({ controller, editor, sourcePath }) => {
          controller.hardwareSourceReveal = { path: sourcePath.toLowerCase(), position: editor.selection.active, opening: true };
          controller.followSourceEditor(editor);
        }],
      ]) {
        const setup = automaticSourceFollow(vscode, TimelineController);
        setup.controller.followSourceEditor(setup.editor);
        clock.advance(100);
        change(setup);
        clock.advance(1000);
        assert.deepEqual(setup.posted, [], reason);
        setup.controller.dispose();
      }
    }));
  });

  it("cancels selected-block zoom on caret collapse, editor exit, source navigation, and disposal", async () => {
    const vscode = createMockVscode();
    await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
      const caret = automaticSourceFollow(vscode, TimelineController);
      caret.controller.followSourceEditor(caret.editor);
      caret.editor.selection = sourceSelection(3, 3, 0);
      caret.controller.followSourceEditor(caret.editor);
      assert.deepEqual(caret.posted, [{ type: "highlightEventIds", highlightEventIds: ["e4"] }]);
      clock.advance(1000);
      assert.equal(caret.posted.length, 1, "collapsing to a caret retains the existing immediate highlight behavior");
      assert.equal(caret.controller.sourceRangeTimer, undefined);
      caret.controller.dispose();

      const exiting = automaticSourceFollow(vscode, TimelineController);
      exiting.controller.followSourceEditor(exiting.editor);
      vscode.window.activeTextEditor = undefined;
      exiting.controller.followSourceEditor(undefined);
      assert.equal(exiting.controller.sourceRangeTimer, undefined);
      clock.advance(1000);
      assert.deepEqual(exiting.posted, []);
      exiting.controller.dispose();

      const navigation = automaticSourceFollow(vscode, TimelineController);
      navigation.controller.followSourceEditor(navigation.editor);
      vscode.workspace.openTextDocument = async () => navigation.editor.document;
      vscode.window.showTextDocument = async () => ({ ...navigation.editor, setDecorations() {}, revealRange() {} });
      await navigation.controller.openSourceLocation({ file: navigation.sourcePath, line: 5 }, { preserveHardwareCursor: true });
      assert.equal(navigation.controller.sourceRangeTimer, undefined);
      clock.advance(1000);
      assert.deepEqual(navigation.posted, []);
      navigation.controller.dispose();

      const disposed = automaticSourceFollow(vscode, TimelineController);
      disposed.controller.followSourceEditor(disposed.editor);
      disposed.controller.dispose();
      assert.equal(disposed.controller.sourceRangeTimer, undefined);
      clock.advance(1000);
      assert.deepEqual(disposed.posted, []);
    }));
  });

  it("resumes the current selected block after webview readiness with explicit targets taking priority", async () => {
    const vscode = createMockVscode();
    await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
      const setup = automaticSourceFollow(vscode, TimelineController);
      setup.controller.webviewReady = false;
      setup.controller.followSourceEditor(setup.editor);
      clock.advance(250);
      assert.deepEqual(setup.posted, []);
      setup.editor.selection = sourceSelection(3, 5, 0, true);
      await setup.controller.handleWebviewMessage({ type: "webviewReady" });
      clock.advance(250);
      assert.deepEqual(setup.posted, [{ type: "highlightEventIds", highlightEventIds: ["e4", "e5"], zoomToRange: true,
        sourceRange: { file: setup.sourcePath, startLine: 4, endLine: 5 } }]);
      setup.controller.dispose();

      const explicit = automaticSourceFollow(vscode, TimelineController);
      explicit.controller.webviewReady = false;
      explicit.controller.pendingTarget = { q1asmFile: explicit.sourcePath, line: 5, endLine: 5, zoomToRange: true, awaitingWebviewReady: true };
      await explicit.controller.handleWebviewMessage({ type: "webviewReady" });
      clock.advance(1000);
      assert.deepEqual(explicit.posted, [{ type: "highlightEventIds", highlightEventIds: ["e5"], zoomToRange: true,
        sourceRange: { file: explicit.sourcePath, startLine: 5, endLine: 5 } }]);
      assert.equal(explicit.controller.pendingTarget, undefined);
      explicit.controller.dispose();

      const collapsed = automaticSourceFollow(vscode, TimelineController);
      collapsed.controller.webviewReady = false;
      collapsed.controller.followSourceEditor(collapsed.editor);
      collapsed.editor.selection = sourceSelection(3, 3, 0);
      await collapsed.controller.handleWebviewMessage({ type: "webviewReady" });
      clock.advance(1000);
      assert.deepEqual(collapsed.posted, [], "readiness must not restore a selection that has collapsed");
      collapsed.controller.dispose();
    }));
  });

  it("keeps saved manual-mode source stale through rerender until fresh analysis succeeds", async () => {
    const vscode = createMockVscode();
    let changed: any;
    let saved: any;
    vscode.workspace.onDidChangeTextDocument = (handler) => { changed = handler; return { dispose() {} }; };
    vscode.workspace.onDidSaveTextDocument = (handler) => { saved = handler; return { dispose() {} }; };
    const outputDir = mkdtempSync(join(tmpdir(), "q1-source-revision-"));
    await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
      const { controller, editor, posted } = automaticSourceFollow(vscode, TimelineController);
      controller.updateMode = () => "manual";
      const logs: string[] = [];
      controller.output.appendLine = (message) => logs.push(message);
      controller.outputDir = outputDir;
      Object.assign(controller.panel.webview, { html: "", cspSource: "vscode-resource:", asWebviewUri: (uri) => uri });
      assert.equal(controller.sourceRevision, 0);
      assert.equal(controller.analyzedSourceRevision, 0);
      controller.followSourceEditor(editor);
      editor.document.isDirty = true;
      editor.document.version += 1;
      changed({ document: editor.document });
      assert.equal(controller.sourceRangeTimer, undefined);
      editor.document.isDirty = false;
      await saved(editor.document);
      const savedRevision = controller.sourceRevision;
      assert.ok(savedRevision >= 2);
      await controller.onWatchedFile(editor.document.uri);
      assert.ok(controller.sourceRevision > savedRevision, "watch events must also invalidate saved analysis in manual mode");
      controller.followSourceEditor(editor);
      clock.advance(250);
      assert.equal(posted.some((message) => message.zoomToRange), false);

      const timelineIr = controller.timelineIr;
      const writeOutputs = () => {
        writeFileSync(join(outputDir, "timeline_ir.json"), JSON.stringify(timelineIr), "utf8");
        writeFileSync(join(outputDir, "diagnostics.json"), "[]", "utf8");
        writeFileSync(join(outputDir, "timeline.html"), "<html><head></head><body></body></html>", "utf8");
      };
      writeOutputs();
      await controller.refreshWebview();
      assert.equal(controller.analyzedSourceRevision, 0, "rerendering old IR must not make edited source current");
      await controller.handleWebviewMessage({ type: "webviewReady" });
      clock.advance(250);
      assert.equal(posted.some((message) => message.zoomToRange), false);

      controller.execFile = async () => { writeOutputs(); return { stdout: JSON.stringify({ schema_version: "0.2.0", status: "ok" }), stderr: "", exitCode: 0, elapsedMs: 1 }; };
      controller.runRender = async () => ({ stdout: "", stderr: "", exitCode: 0, elapsedMs: 1 });
      await controller.runAnalysis();
      assert.equal(controller.analyzedSourceRevision, controller.sourceRevision, logs.join("\n"));
      await controller.handleWebviewMessage({ type: "webviewReady" });
      clock.advance(250);
      assert.equal(posted.filter((message) => message.zoomToRange).length, 1);
      controller.dispose();
    }));
  });

  it("does not mark failed refreshes or source edits during analysis as current", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-source-snapshot-"));
    await withFakeTimers(async (clock) => withMockedVscode(vscode, async ({ TimelineController }) => {
      const { controller, editor, posted } = automaticSourceFollow(vscode, TimelineController);
      controller.outputDir = outputDir;
      const logs: string[] = [];
      controller.output.appendLine = (message) => logs.push(message);
      controller.invalidateSourceAnalysis();
      const result = { stdout: JSON.stringify({ schema_version: "0.2.0", status: "ok" }), stderr: "", exitCode: 0, elapsedMs: 1 };
      controller.execFile = async () => result;
      controller.runRender = async () => ({ stdout: "", stderr: "", exitCode: 0, elapsedMs: 1 });
      controller.refreshWebview = async () => false;
      await controller.runAnalysis();
      assert.equal(controller.analyzedSourceRevision, 0);

      const analyzedRevision = controller.sourceRevision;
      controller.execFile = async () => {
        controller.invalidateSourceAnalysis();
        return result;
      };
      controller.refreshWebview = async () => true;
      await controller.runAnalysis();
      assert.equal(controller.analyzedSourceRevision, analyzedRevision, logs.join("\n"));
      assert.ok(controller.sourceRevision > controller.analyzedSourceRevision);
      controller.followSourceEditor(editor);
      clock.advance(250);
      assert.equal(posted.some((message) => message.zoomToRange), false);
      controller.dispose();
    }));
  });

  it("delivers a selected range once after the freshly rendered webview reports ready", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-selection-ready-"));
    const sourcePath = join(outputDir, "seq0.q1asm");
    const timelineIr = { events: [{ id: "selected", source: { file: sourcePath, line: 3 } }] };
    writeFileSync(join(outputDir, "timeline_ir.json"), JSON.stringify(timelineIr), "utf8");
    writeFileSync(join(outputDir, "diagnostics.json"), "[]", "utf8");
    writeFileSync(join(outputDir, "timeline.html"), "<html><head></head><body></body></html>", "utf8");
    const posted: any[] = [];
    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file(join(outputDir, "q1timeline.yml"));
      controller.outputDir = outputDir;
      controller.panel = { webview: { html: "", cspSource: "vscode-resource:",
        asWebviewUri: (uri) => uri, postMessage: (message) => posted.push(message) }, dispose: () => {} };
      controller.timelineIr = { events: [{ id: "old", source: { file: sourcePath, line: 3 } }] };
      controller.pendingTarget = { q1asmFile: sourcePath, line: 3, endLine: 4, zoomToRange: true };
      await controller.handleWebviewMessage({ type: "webviewReady" });
      assert.ok(controller.pendingTarget, "Readiness from the previous view must not consume a new request");
      await controller.refreshWebview();
      assert.equal(posted.some((message) => message.type === "highlightEventIds"), false);
      assert.equal(controller.pendingTarget?.awaitingWebviewReady, true);
      await controller.handleWebviewMessage({ type: "webviewReady" });
      await controller.handleWebviewMessage({ type: "webviewReady" });
      assert.deepEqual(posted.filter((message) => message.type === "highlightEventIds"), [{
        type: "highlightEventIds", highlightEventIds: ["selected"], zoomToRange: true,
        sourceRange: { file: sourcePath, startLine: 3, endLine: 4 },
      }]);
      assert.equal(controller.pendingTarget, undefined);
      controller.dispose();
    });
  });
});
