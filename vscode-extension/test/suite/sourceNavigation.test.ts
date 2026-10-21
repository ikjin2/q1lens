import assert from "node:assert/strict";
import * as vscode from "vscode";

describe("Q1ASM source request ordering in VS Code", function () {
  this.timeout(30000);

  it("keeps the latest Hardware source click active after an older document finishes loading", async () => {
    const extension = vscode.extensions.getExtension("q1lens.q1lens");
    assert.ok(extension);
    await extension.activate();
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
    assert.ok(workspaceRoot);
    await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    const root = vscode.Uri.joinPath(workspaceRoot, `source-navigation-${Date.now()}`);
    const cold = vscode.Uri.joinPath(root, "cold.q1asm"), hot = vscode.Uri.joinPath(root, "hot.q1asm");
    const project = vscode.Uri.joinPath(root, "q1timeline.yml");
    await vscode.workspace.fs.createDirectory(root);
    await vscode.workspace.fs.writeFile(cold, Buffer.from("wait 4\nstop\n"));
    await vscode.workspace.fs.writeFile(hot, Buffer.from("wait 8\nstop\n"));
    await vscode.workspace.fs.writeFile(project, Buffer.from("sequencers:\n  - id: cold\n    file: cold.q1asm\n  - id: hot\n    file: hot.q1asm\n"));
    const hotDocument = await vscode.workspace.openTextDocument(hot);
    await vscode.window.showTextDocument(hotDocument, { preview: false });
    assert.ok(!vscode.workspace.textDocuments.some((document) => document.uri.toString() === cold.toString()));
    let releaseCold!: () => void;
    let coldStarted!: () => void;
    const coldWaiting = new Promise<void>((resolve) => { coldStarted = resolve; });
    const openTextDocument = async (resource: vscode.Uri) => {
      if (resource.toString() === cold.toString()) {
        await new Promise<void>((resolve) => { releaseCold = resolve; coldStarted(); });
      }
      return vscode.workspace.openTextDocument(resource);
    };
    // Defer just the cold read; editor groups, display, selection, and events remain native.
    const workspaceApi = new Proxy({}, { get(_target, key) {
      if (key === "openTextDocument") return openTextDocument;
      const value = Reflect.get(vscode.workspace, key);
      return typeof value === "function" ? value.bind(vscode.workspace) : value;
    } });
    const api = new Proxy({}, { get(_target, key) {
      return key === "workspace" ? workspaceApi : Reflect.get(vscode, key);
    } });
    const Module = require("node:module");
    const originalLoad = Module._load;
    const controllerFile = require.resolve("../../src/q1timeline/controller");
    const cached = require.cache[controllerFile];
    const subscriptions: vscode.Disposable[] = [];
    let controller: any;
    let panel: vscode.WebviewPanel | undefined;
    let first: Promise<void> | undefined;
    try {
      Module._load = function (request: string, parent: { filename?: string }, isMain: boolean) {
        return request === "vscode" && parent?.filename === controllerFile ? api
          : originalLoad.call(this, request, parent, isMain);
      };
      delete require.cache[controllerFile];
      const { TimelineController } = require(controllerFile);
      controller = new TimelineController({
        extensionPath: extension.extensionPath, extensionUri: extension.extensionUri, subscriptions,
        workspaceState: { get: (_key: string, fallback: unknown) => fallback, update: async () => undefined },
      });
      Module._load = originalLoad;
      controller.setProjectUri(project);
      controller.outputDir = vscode.Uri.joinPath(root, ".q1timeline").fsPath;
      controller.projectRelatedPaths = new Set([cold.fsPath, hot.fsPath]);
      panel = vscode.window.createWebviewPanel("q1lens.testSourceRequest", "Q1ASM source request", vscode.ViewColumn.Beside, {});
      controller.panel = panel;
      const panelDeadline = Date.now() + 3000;
      while (!vscode.window.tabGroups.all.some((group) => group.tabs.some((tab) =>
        tab.input instanceof vscode.TabInputWebview && tab.input.viewType.includes("q1lens.testSourceRequest"),
      ))) {
        assert.ok(Date.now() < panelDeadline, "The native panel tab must exist before recording the editor group count");
        await new Promise((resolve) => setTimeout(resolve, 20));
      }
      const groupCount = vscode.window.tabGroups.all.length;
      first = controller.handleWebviewMessage({ type: "sourceClick", file: cold.fsPath, line: 2, column: 1, preserveHardwareCursor: true });
      await coldWaiting;
      await controller.handleWebviewMessage({ type: "sourceClick", file: hot.fsPath, line: 2, column: 1, preserveHardwareCursor: true });
      const deadline = Date.now() + 3000;
      while (vscode.window.activeTextEditor?.document.uri.toString() !== hot.toString()
          || vscode.window.activeTextEditor?.selection.active.line !== 1) {
        assert.ok(Date.now() < deadline, "The newer source click must activate the hot source before the cold read finishes");
        await new Promise((resolve) => setTimeout(resolve, 20));
      }
      releaseCold();
      await first;
      assert.equal(vscode.window.activeTextEditor?.document.uri.toString(), hot.toString());
      assert.equal(vscode.window.activeTextEditor?.selection.active.line, 1);
      assert.equal(vscode.window.tabGroups.all.length, groupCount);
      assert.ok(!vscode.window.tabGroups.all.some((group) => group.tabs.some((tab) =>
        tab.input instanceof vscode.TabInputText && tab.input.uri.toString() === cold.toString(),
      )), "The superseded cold source may load into memory but must not open an editor");
    } finally {
      Module._load = originalLoad;
      releaseCold?.();
      await first?.catch(() => undefined);
      controller?.dispose();
      panel?.dispose();
      for (const subscription of subscriptions) subscription.dispose();
      if (cached) require.cache[controllerFile] = cached;
      else delete require.cache[controllerFile];
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  });
});
