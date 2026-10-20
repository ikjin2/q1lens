import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import Module = require("node:module");
import { tmpdir } from "node:os";
import { join } from "node:path";

describe("QBS refresh errors", () => {
  it("reports invalid saved projects from the webview, command and watcher, then recovers", async () => {
    const root = mkdtempSync(join(tmpdir(), "qbs-refresh-errors-"));
    const projectFile = join(root, "qbstimeline.yml");
    const validProject = "schedule:\n  file: schedule.py\n";
    writeFileSync(projectFile, validProject);
    mkdirSync(join(root, ".qbs_timeline"));
    writeFileSync(join(root, ".qbs_timeline", "qbs_ir.json"), JSON.stringify({
      schedule: { name: "Refresh" }, operations: [], symbolic_values: [],
      symbolic_pulses: [], q1asm_programs: [], q1asm_provenance: [],
    }));
    const commands: Record<string, () => Promise<void>> = {};
    const errors: string[] = [];
    const logs: string[] = [];
    const subscriptions: Array<{ dispose(): void }> = [];
    let panelHandlers: any;
    let saved: (uri: any) => void = () => assert.fail("Missing file watcher");
    let panelShows = 0;
    let errorReported = () => {};
    let panelRendered = () => {};
    const vscode = {
      Uri: { file: (fsPath: string) => ({ fsPath }) },
      window: {
        activeTextEditor: { document: { uri: { fsPath: projectFile } } },
        createOutputChannel: () => ({ appendLine: (text: string) => logs.push(text), show() {}, dispose() {} }),
        showErrorMessage: (message: string) => {
          errors.push(message);
          errorReported();
          return new Promise<undefined>(() => {});
        },
      },
      workspace: {
        findFiles: async () => [{ fsPath: projectFile }],
        getConfiguration: (section: string) => ({
          get: (key: string, fallback: any) => section === "q1lens" && key === "qbs.autoRefresh" ? true : fallback,
        }),
        createFileSystemWatcher: () => ({
          onDidChange: (listener: (uri: any) => void) => { saved = listener; return { dispose() {} }; },
          onDidCreate: () => ({ dispose() {} }),
          onDidDelete: () => ({ dispose() {} }),
          dispose() {},
        }),
      },
      languages: { createDiagnosticCollection: () => ({ clear() {}, set() {}, dispose() {} }) },
      commands: {
        registerCommand: (name: string, callback: () => Promise<void>) => {
          commands[name] = callback;
          return { dispose() {} };
        },
      },
    };
    const originalLoad = (Module as any)._load;
    (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
      if (request === "vscode") return vscode;
      if (request === "../fileEnvironment") return {
        ensureFilePythonEnvironment: async () => ({ pythonPath: "python", pythonArgs: [] }),
      };
      if (request === "./webview/panel") return { TimelinePanel: class {
        constructor(_extensionUri: any, handlers: any) { panelHandlers = handlers; }
        show() { panelShows += 1; panelRendered(); }
      } };
      if (request === "./diagnostics") return { publishDiagnostics() {} };
      if (request === "./qbsCli") return {
        ...originalLoad.call(this, request, parent, isMain),
        runProcessWithSpawn: async () => ({ exitCode: 0, stdout: "", stderr: "" }),
      };
      return originalLoad.call(this, request, parent, isMain);
    };
    const modulePath = require.resolve("../src/qbs/commands");
    delete require.cache[modulePath];
    try {
      require("../src/qbs/commands").registerCommands(
        { extensionUri: { fsPath: "C:/extension" }, subscriptions },
        { openQ1TimelineTarget: async () => undefined },
      );
      await commands["qbsTimeline.analyzeAndOpen"]();
      assert.equal(panelShows, 1);
      writeFileSync(projectFile, "schedule: {}\n");

      await panelHandlers.onRefresh();
      await commands["qbsTimeline.refresh"]();
      const automaticError = new Promise<void>((resolve) => { errorReported = resolve; });
      saved(vscode.Uri.file(projectFile));
      await automaticError;

      assert.equal(errors.length, 3);
      assert.ok(errors.every((message) => /schedule\.file or schedule\.notebook/.test(message)));
      assert.equal(logs.filter((message) => /schedule\.file or schedule\.notebook/.test(message)).length, 3);
      assert.equal(panelShows, 1, "Failed analysis must preserve the last successful preview");

      writeFileSync(projectFile, validProject);
      const recovered = new Promise<void>((resolve) => { panelRendered = resolve; });
      saved(vscode.Uri.file(projectFile));
      await recovered;
      assert.equal(panelShows, 2);
      assert.equal(errors.length, 3);
    } finally {
      subscriptions.forEach((subscription) => subscription.dispose());
      delete require.cache[modulePath];
      (Module as any)._load = originalLoad;
    }
  }).timeout(5000);
});
