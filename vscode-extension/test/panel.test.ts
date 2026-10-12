import assert from "node:assert/strict";
import Module = require("node:module");

describe("QBS timeline panel", () => {
  it("initializes once and sends later IR updates without resetting or revealing the Webview", async () => {
    const posted: any[] = [];
    let htmlAssignments = 0;
    let revealCalls = 0;
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
      webview,
      onDidDispose: () => ({ dispose: () => undefined }),
      reveal: () => {
        revealCalls += 1;
      },
    };
    const vscode = {
      Uri: {
        joinPath: (base: any, ...parts: string[]) => ({ fsPath: [base.fsPath, ...parts].join("/") }),
      },
      ViewColumn: { Beside: 2 },
      window: { createWebviewPanel: () => fakePanel },
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
      assert.equal(revealCalls, 1);
      assert.equal(posted.length, 0);

      receiveMessage?.({ type: "ready" });
      await new Promise((resolve) => setImmediate(resolve));
      assert.equal(posted[0].ir.schedule.name, "A");

      panel.show(irB, outputPaths("C:/repo/project-b"));
      await new Promise((resolve) => setImmediate(resolve));

      assert.equal(htmlAssignments, 1);
      assert.equal(revealCalls, 1);
      assert.equal(fakePanel.title, "Q1Lens: B");
      assert.equal(posted[1].ir.schedule.name, "B");
      assert.equal(posted[1].sourceContext.projectFile, "C:/repo/project-b/qbstimeline.yml");
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
