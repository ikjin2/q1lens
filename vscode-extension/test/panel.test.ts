import assert from "node:assert/strict";
import Module = require("node:module");

describe("QBS timeline panel", () => {
  it("ignores navigation from an older render while accepting current actions and untagged refreshes", async () => {
    const posted: any[] = [];
    const opened: string[] = [];
    let refreshes = 0;
    let receive!: (message: any) => void;
    const fakePanel = {
      title: "", viewColumn: 2, reveal() {}, dispose() {}, onDidDispose: () => ({ dispose() {} }),
      webview: {
        html: "", cspSource: "vscode-resource://test",
        asWebviewUri: (uri: any) => ({ toString: () => uri.fsPath }),
        onDidReceiveMessage: (listener: typeof receive) => { receive = listener; return { dispose() {} }; },
        postMessage: async (message: any) => { posted.push(message); return true; },
      },
    };
    const vscode = {
      Uri: { joinPath: (base: any, ...parts: string[]) => ({ fsPath: [base.fsPath, ...parts].join("/") }) },
      ViewColumn: { Beside: 2 }, window: { createWebviewPanel: () => fakePanel },
    };
    const originalLoad = (Module as any)._load;
    (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
      return request === "vscode" ? vscode : originalLoad.call(this, request, parent, isMain);
    };
    const modulePath = require.resolve("../src/qbs/webview/panel");
    delete require.cache[modulePath];
    try {
      const { TimelinePanel } = require("../src/qbs/webview/panel");
      const types = ["openQ1Timeline", "openQ1AsmSource", "openScheduleSource", "openIr", "openProjectFile", "openScheduleFile", "openNotebookFile"];
      const handlers = Object.fromEntries(types.map((type) => [
        `on${type[0].toUpperCase()}${type.slice(1)}`, async () => { opened.push(type); },
      ]));
      const panel = new TimelinePanel({ fsPath: "C:/extension" }, {
        ...handlers, onRefresh: async () => { refreshes += 1; },
      });
      panel.show(emptyIr("A"), outputPaths("C:/repo/a"));
      receive({ type: "ready" });
      await new Promise((resolve) => setImmediate(resolve));
      const firstRenderId = posted[0].renderId;

      panel.show(emptyIr("B"), outputPaths("C:/repo/b"));
      for (const type of types) receive({ type, renderId: firstRenderId });
      await new Promise((resolve) => setImmediate(resolve));
      assert.deepEqual(opened, [], "A source action already in transit must not resolve against B's paths or IR");

      const secondRenderId = posted[1].renderId;
      assert.ok(secondRenderId > firstRenderId);
      for (const type of types) receive({ type, renderId: secondRenderId });
      await new Promise((resolve) => setImmediate(resolve));
      assert.deepEqual(opened, types);
      receive({ type: "openScheduleFile" });
      assert.deepEqual(opened, types, "Missing render ownership must not bypass the stale-action check");

      panel.show(emptyIr("B refreshed"), outputPaths("C:/repo/b"));
      receive({ type: "openScheduleSource", renderId: secondRenderId });
      assert.deepEqual(opened, types, "A same-source refresh can replace operation IDs and source mappings too");
      receive({ type: "openScheduleSource", renderId: posted[2].renderId });
      receive({ type: "refresh" });
      await new Promise((resolve) => setImmediate(resolve));
      assert.deepEqual(opened, [...types, "openScheduleSource"]);
      assert.equal(refreshes, 1);
      panel.dispose();
    } finally {
      delete require.cache[modulePath];
      (Module as any)._load = originalLoad;
    }
  });

  it("ignores an action's late error after its webview is closed and another panel is opened", async () => {
    const created: any[] = [];
    const errors: string[] = [];
    let refreshCalls = 0;
    let rejectOldAction!: (error: Error) => void;
    const vscode = {
      Uri: { joinPath: (base: any, ...parts: string[]) => ({ fsPath: [base.fsPath, ...parts].join("/") }) },
      ViewColumn: { Beside: 2 },
      window: {
        createWebviewPanel: () => {
          const posted: any[] = [];
          const instance: any = {
            title: "", viewColumn: 2, posted,
            webview: {
              cspSource: "vscode-resource://test", html: "",
              asWebviewUri: (uri: any) => ({ toString: () => uri.fsPath }),
              postMessage: async (message: any) => { posted.push(message); return true; },
              onDidReceiveMessage: (listener: any) => { instance.receive = listener; return { dispose() {} }; },
            },
            onDidDispose: (listener: any) => { instance.onDispose = listener; return { dispose() {} }; },
            dispose: () => instance.onDispose(), reveal() {},
          };
          created.push(instance);
          return instance;
        },
        showErrorMessage: async (message: string) => { errors.push(message); },
      },
    };
    const originalLoad = (Module as any)._load;
    (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
      return request === "vscode" ? vscode : originalLoad.call(this, request, parent, isMain);
    };
    const modulePath = require.resolve("../src/qbs/webview/panel");
    delete require.cache[modulePath];
    try {
      const { TimelinePanel } = require("../src/qbs/webview/panel");
      const panel = new TimelinePanel({ fsPath: "C:/extension" }, {
        onRefresh: async () => { refreshCalls += 1; },
        onOpenScheduleSource: async () => new Promise<void>((_resolve, reject) => { rejectOldAction = reject; }),
      });
      panel.show(emptyIr("A"));
      const oldPanel = created[0];
      oldPanel.receive({ type: "ready" });
      await new Promise((resolve) => setImmediate(resolve));
      oldPanel.receive({ type: "openScheduleSource", schedulableId: "pulse", renderId: oldPanel.posted[0].renderId });
      oldPanel.dispose();
      panel.show(emptyIr("B"));
      const currentPanel = created[1];
      assert.notEqual(currentPanel, oldPanel);

      rejectOldAction(new Error("The closed panel's source failed"));
      await new Promise((resolve) => setImmediate(resolve));
      assert.deepEqual(currentPanel.posted, []);
      assert.equal(refreshCalls, 0);
      assert.deepEqual(errors, [], "The closed webview must not report late errors against its replacement");

      currentPanel.receive({ type: "ready" });
      currentPanel.receive({ type: "refresh" });
      await new Promise((resolve) => setImmediate(resolve));
      assert.equal(currentPanel.posted.length, 1);
      assert.equal(currentPanel.posted[0].ir.schedule.name, "B");
      assert.equal(refreshCalls, 1);
      panel.dispose();
    } finally {
      delete require.cache[modulePath];
      (Module as any)._load = originalLoad;
    }
  });

  it("updates without resetting or revealing the webview unless opening it explicitly", async () => {
    const posted: any[] = [];
    let htmlAssignments = 0;
    let revealCalls = 0;
    let panelDisposals = 0;
    let onDisposed: (() => void) | undefined;
    const createdColumns: number[] = [];
    const revealColumns: Array<number | undefined> = [];
    let receiveMessage: ((message: any) => void) | undefined;
    let html = "";
    const webview = {
      cspSource: "vscode-resource://test",
      asWebviewUri: (uri: any) => ({ toString: () => `vscode-resource://${uri.fsPath}` }),
      onDidReceiveMessage: (listener: (message: any) => void) => {
        receiveMessage = listener;
        return { dispose: () => undefined };
      },
      postMessage: async (message: any) => {
        posted.push(message);
        return true;
      },
      get html() {
        return html;
      },
      set html(value: string) {
        htmlAssignments += 1;
        html = value;
      },
    };
    const fakePanel = {
      title: "",
      visible: false,
      viewColumn: 2,
      webview,
      onDidDispose: (listener: () => void) => { onDisposed = listener; return { dispose: () => undefined }; },
      dispose: () => { panelDisposals += 1; onDisposed?.(); },
      reveal: (column?: number) => {
        revealCalls += 1;
        revealColumns.push(column);
        fakePanel.visible = true;
      },
    };
    const vscode = {
      Uri: {
        joinPath: (base: any, ...parts: string[]) => ({ fsPath: [base.fsPath, ...parts].join("/") }),
      },
      ViewColumn: { Beside: 2 },
      window: { createWebviewPanel: (_viewType: string, _title: string, column: number) => {
        createdColumns.push(column);
        fakePanel.visible = true;
        return fakePanel;
      } },
    };
    const originalLoad = (Module as any)._load;
    (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
      if (request === "vscode") {
        return vscode;
      }
      return originalLoad.call(this, request, parent, isMain);
    };
    const modulePath = require.resolve("../src/qbs/webview/panel");
    delete require.cache[modulePath];
    try {
      const { TimelinePanel } = require("../src/qbs/webview/panel");
      const handlers = {
        onRefresh: async () => undefined,
        onOpenQ1Timeline: async () => undefined,
        onOpenQ1AsmSource: async () => undefined,
        onOpenScheduleSource: async () => undefined,
        onOpenIr: async () => undefined,
        onOpenProjectFile: async () => undefined,
        onOpenScheduleFile: async () => undefined,
        onOpenNotebookFile: async () => undefined,
      };
      const panel = new TimelinePanel({ fsPath: "C:/extension" }, handlers);
      const irA = emptyIr("A");
      const irB = emptyIr("B");

      panel.show(irA, outputPaths("C:/repo/project-a"));
      assert.equal(htmlAssignments, 1);
      assert.equal(fakePanel.visible, true, "Creating the webview already shows it");
      assert.equal(revealCalls, 0, "A second relative reveal could move the new panel into another group");
      assert.deepEqual(createdColumns, [2]);
      assert.equal(posted.length, 0);

      receiveMessage?.({ type: "ready" });
      await new Promise((resolve) => setImmediate(resolve));
      assert.equal(posted[0].ir.schedule.name, "A");

      fakePanel.visible = false;
      panel.show(irB, outputPaths("C:/repo/project-b"));
      await new Promise((resolve) => setImmediate(resolve));

      assert.equal(htmlAssignments, 1);
      assert.equal(revealCalls, 0);
      assert.equal(fakePanel.title, "Q1Lens: B");
      assert.equal(posted[1].ir.schedule.name, "B");
      assert.equal(posted[1].sourceContext.projectFile, "C:/repo/project-b/qbstimeline.yml");
      assert.equal(fakePanel.visible, false, "Background updates must leave the panel hidden");

      panel.show(irB, outputPaths("C:/repo/project-b"), true);
      await new Promise((resolve) => setImmediate(resolve));
      assert.equal(fakePanel.visible, true, "An explicit open must reveal the existing panel");
      assert.equal(htmlAssignments, 1);
      assert.deepEqual(revealColumns, [fakePanel.viewColumn], "Reopening must keep the panel in its existing editor group");

      fakePanel.dispose();
      panel.show(irA, outputPaths("C:/repo/project-a"), true);
      assert.deepEqual(createdColumns, [2, 2], "A normal tab close must still allow an explicit reopen");
      panel.dispose();
      panel.dispose();
      panel.show(irB, outputPaths("C:/repo/project-b"), true);
      await new Promise((resolve) => setImmediate(resolve));
      assert.equal(panelDisposals, 2, "Owner disposal must close its open tab exactly once");
      assert.deepEqual(createdColumns, [2, 2], "A disposed owner must not create another tab");
    } finally {
      delete require.cache[modulePath];
      (Module as any)._load = originalLoad;
    }
  });

  it("reports source-opening failures without blocking later webview actions", async () => {
    let receiveMessage!: (message: any) => void;
    let renderId: number | undefined;
    const errors: string[] = [];
    let openCalls = 0;
    let rejectLateAction!: (error: Error) => void;
    const fakePanel = {
      title: "",
      webview: {
        cspSource: "vscode-resource://test",
        asWebviewUri: (uri: any) => ({ toString: () => uri.fsPath }),
        onDidReceiveMessage: (listener: typeof receiveMessage) => {
          receiveMessage = listener;
          return { dispose() {} };
        },
        postMessage: async (message: any) => { renderId = message.renderId; return true; },
        html: "",
      },
      onDidDispose: () => ({ dispose() {} }),
      dispose() {},
      reveal() {},
    };
    const vscode = {
      Uri: { joinPath: (base: any, ...parts: string[]) => ({ fsPath: [base.fsPath, ...parts].join("/") }) },
      ViewColumn: { Beside: 2 },
      window: {
        createWebviewPanel: () => fakePanel,
        showErrorMessage: (message: string) => {
          errors.push(message);
          return new Promise(() => {});
        },
      },
    };
    const originalLoad = (Module as any)._load;
    (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
      return request === "vscode" ? vscode : originalLoad.call(this, request, parent, isMain);
    };
    const modulePath = require.resolve("../src/qbs/webview/panel");
    delete require.cache[modulePath];
    try {
      const { TimelinePanel } = require("../src/qbs/webview/panel");
      const panel = new TimelinePanel({ fsPath: "C:/extension" }, {
        onOpenScheduleSource: async () => {
          openCalls += 1;
          if (openCalls === 1) throw new Error("Schedule source file no longer exists");
          if (openCalls === 3) await new Promise<void>((_resolve, reject) => { rejectLateAction = reject; });
        },
      });
      panel.show(emptyIr("A"));
      receiveMessage({ type: "ready" });
      await new Promise((resolve) => setImmediate(resolve));

      receiveMessage({ type: "openScheduleSource", schedulableId: "pulse", renderId });
      await new Promise((resolve) => setImmediate(resolve));
      assert.deepEqual(errors, ["Q1Lens action failed: Schedule source file no longer exists"]);

      receiveMessage({ type: "openScheduleSource", schedulableId: "pulse", renderId });
      await new Promise((resolve) => setImmediate(resolve));
      assert.equal(openCalls, 2);
      assert.equal(errors.length, 1);

      receiveMessage({ type: "openScheduleSource", schedulableId: "pulse", renderId });
      await new Promise((resolve) => setImmediate(resolve));
      panel.dispose();
      rejectLateAction(new Error("The source failed after shutdown"));
      await new Promise((resolve) => setImmediate(resolve));
      assert.equal(errors.length, 1, "Late action failures must not show notifications after owner disposal");
    } finally {
      delete require.cache[modulePath];
      (Module as any)._load = originalLoad;
    }
  });
});

function emptyIr(name: string) {
  return {
    schedule: { name },
    operations: [],
    symbolic_values: [],
    symbolic_pulses: [],
    q1asm_programs: [],
    q1asm_provenance: [],
  };
}

function outputPaths(projectDir: string) {
  return {
    projectFile: `${projectDir}/qbstimeline.yml`,
    projectDir,
    schedulePath: `${projectDir}/schedule.py`,
    outputDir: `${projectDir}/.qbs_timeline`,
    irPath: `${projectDir}/.qbs_timeline/qbs_ir.json`,
    htmlPath: `${projectDir}/.qbs_timeline/index.html`,
    q1asmDir: `${projectDir}/.qbs_timeline/q1asm`,
  };
}
