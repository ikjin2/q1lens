// @ts-nocheck
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
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

async function withMockedVscode(vscode: any, callback: (controllerModule: any) => Promise<void>) {
  const originalLoad = Module._load;
  Module._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
    if (request === "vscode") {
      return vscode;
    }
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
    selectionEvent: (kind?: number) => selectionHandler({ textEditor: editor, kind }),
    activeEvent: () => activeHandler(editor),
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
  function playbackController(TimelineController, vscode, request) {
    const controller = new TimelineController(createContext());
    const posted = [];
    const published = [];
    const calls = [];
    let disposed = 0;
    const client = { request: async (message) => { calls.push(message); return request(message); }, dispose: () => disposed++ };
    controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
    controller.outputDir = "C:\\repo\\.q1timeline";
    controller.panel = { webview: { postMessage: (message) => posted.push(message) }, dispose() {} };
    controller.createPlaybackClient = () => client;
    controller.publishPlaybackSnapshot = async (ir) => { controller.timelineIr = ir; published.push(ir); };
    return { controller, client, calls, posted, published, disposed: () => disposed };
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
      assert.deepEqual(revealArgs, [[]]);
      controller.dispose();
    });
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
  it("offers a Python environment check with a retry when the analyzer is missing", async () => {
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
      await controller.showAnalyzerMissingMessage(new Error("No module named q1lens"));
      assert.deepEqual(calls, [["q1lens.checkEnvironment", "q1timeline.refreshPreview"]]);
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
      saved(editor.document);
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
