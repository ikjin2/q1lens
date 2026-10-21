import assert from "node:assert/strict";
import * as vscode from "vscode";
import { isDeepStrictEqual } from "node:util";
import { readSettings } from "../../src/qbs/settings";
import { q1timelineConfigValue } from "../../src/q1timeline/settings";
import { registerEnvironmentCommands } from "../../src/environment";

describe("Q1Lens Python environment in VS Code", function () {
  this.timeout(60000);

  it("uses explicit shared Python settings instead of registered Q1ASM defaults", async () => {
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const folder = vscode.workspace.workspaceFolders?.[0];
    assert.ok(folder);
    const settingsFile = vscode.Uri.joinPath(folder.uri, ".vscode", "settings.json");
    let settingsBytes: Uint8Array | undefined;
    try {
      settingsBytes = await vscode.workspace.fs.readFile(settingsFile);
    } catch (error) {
      if (!(error instanceof vscode.FileSystemError) || error.code !== "FileNotFound") throw error;
    }
    const keys = ["pythonPath", "pythonArgs", "q1timeline.pythonPath", "q1timeline.pythonArgs"];
    const original = keys.map((key) => {
      const value = vscode.workspace.getConfiguration("q1lens").inspect(key);
      return { key, global: value?.globalValue, workspace: value?.workspaceValue };
    });
    const update = (key: string, value: unknown, target = vscode.ConfigurationTarget.Workspace) => (
      vscode.workspace.getConfiguration("q1lens").update(key, value, target)
    );
    const empty = { get: <T>(_key: string, fallback: T) => fallback };
    const timeline = <T>(key: string, fallback: T) => q1timelineConfigValue(
      empty, empty, key, fallback, vscode.workspace.getConfiguration("q1lens"),
    );
    try {
      for (const key of ["q1timeline.pythonPath", "q1timeline.pythonArgs"]) {
        await update(key, undefined);
        await update(key, undefined, vscode.ConfigurationTarget.Global);
      }
      await update("pythonPath", "shared-python-test");
      await update("pythonArgs", ["-X", "utf8"]);
      assert.equal(vscode.workspace.getConfiguration("q1lens").get("q1timeline.pythonPath"), "python");
      assert.equal(readSettings().pythonPath, "shared-python-test");
      assert.equal(timeline("pythonPath", "python"), "shared-python-test");
      assert.deepEqual(timeline("pythonArgs", []), ["-X", "utf8"]);

      await update("q1timeline.pythonPath", "python");
      await update("q1timeline.pythonArgs", []);
      assert.equal(timeline("pythonPath", "unused"), "python");
      assert.deepEqual(timeline("pythonArgs", ["unused"]), []);
      assert.equal(readSettings().pythonPath, "shared-python-test");
    } finally {
      for (const value of original) {
        await update(value.key, value.workspace);
        await update(value.key, value.global, vscode.ConfigurationTarget.Global);
      }
      // Restore formatting/order only when no other setting changed during the test.
      const restored = await vscode.workspace.fs.readFile(settingsFile);
      const restoredSettings = JSON.parse(Buffer.from(restored).toString());
      if (settingsBytes && isDeepStrictEqual(restoredSettings, JSON.parse(Buffer.from(settingsBytes).toString()))) {
        await vscode.workspace.fs.writeFile(settingsFile, settingsBytes);
      } else if (!settingsBytes && Object.keys(restoredSettings).length === 0) {
        await vscode.workspace.fs.delete(settingsFile);
      }
    }
  });

  it("checks actual Python and package locations through the registered command", async () => {
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const commands = await vscode.commands.getCommands(true);
    assert.ok(commands.includes("q1lens.checkEnvironment"));
    assert.ok(commands.includes("q1lens.selectPythonEnvironment"));
    const results = await vscode.commands.executeCommand<any[]>("q1lens.checkEnvironment");
    assert.ok(results);
    assert.deepEqual(results.map((item) => item.label), ["QBS", "Q1ASM"]);
    for (const result of results) {
      assert.equal(result.error, undefined, `${result.label}: ${result.error}`);
      assert.equal(result.report.supported, true);
      assert.ok(result.report.executable);
      assert.ok(result.report.version);
      assert.ok(result.report.packagePath);
      assert.ok(result.report.q1asmPath);
    }
  });

  it("applies project Python, discovery and refresh settings only to their workspace folder", async () => {
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const originalFolder = vscode.workspace.workspaceFolders?.[0];
    assert.ok(originalFolder);
    const folder = vscode.Uri.joinPath(originalFolder.uri, "..", `folder-settings-${Date.now()}`);
    const source = vscode.Uri.joinPath(folder, "sequence.q1asm");
    await vscode.workspace.fs.createDirectory(vscode.Uri.joinPath(folder, ".vscode"));
    await vscode.workspace.fs.writeFile(vscode.Uri.joinPath(folder, ".vscode", "settings.json"), Buffer.from(JSON.stringify({
      "q1lens.pythonPath": "folder-python",
      "q1lens.pythonArgs": ["-X", "utf8"],
      "q1lens.qbs.autoRefresh": true,
      "q1lens.q1timeline.projectFile": "folder.q1timeline.yml",
      "q1lens.q1timeline.executionMode": "simulation",
      "q1lens.q1timeline.analyzer.env": { FOLDER_TEST: "B" },
      "qbloxTimeline.qbs.outputDirOverride": "folder-output",
      "qbsTimeline.revealOutputChannel": false,
      "q1timeline.updateMode": "manual",
    })));
    const changeFolders = (index: number, count: number, ...folders: { uri: vscode.Uri }[]) => new Promise<void>((resolve, reject) => {
      const subscription = vscode.workspace.onDidChangeWorkspaceFolders(() => { subscription.dispose(); resolve(); });
      if (!vscode.workspace.updateWorkspaceFolders(index, count, ...folders)) {
        subscription.dispose();
        reject(new Error("Could not change test workspace folders"));
      }
    });
    await changeFolders(vscode.workspace.workspaceFolders!.length, 0, { uri: folder });
    try {
      const config = vscode.workspace.getConfiguration("q1lens", source);
      assert.equal(config.get("pythonPath"), "folder-python");
      assert.deepEqual(config.get("pythonArgs"), ["-X", "utf8"]);
      assert.equal(config.get("qbs.autoRefresh"), true);
      assert.equal(config.get("q1timeline.projectFile"), "folder.q1timeline.yml");
      assert.equal(config.get("q1timeline.executionMode"), "simulation");
      assert.deepEqual(config.get("q1timeline.analyzer.env"), { FOLDER_TEST: "B" });
      assert.equal(readSettings(source).pythonPath, "folder-python");
      assert.equal(readSettings(source).autoRefresh, true);
      assert.equal(readSettings(source).outputDirOverride, "folder-output");
      assert.equal(readSettings(source).revealOutputChannel, false);
      assert.equal(vscode.workspace.getConfiguration("q1timeline", source).get("updateMode"), "manual");
      assert.notEqual(readSettings(originalFolder.uri).pythonPath, "folder-python");
      assert.notEqual(vscode.workspace.getConfiguration("q1lens", originalFolder.uri).get("q1timeline.projectFile"), "folder.q1timeline.yml");
    } finally {
      const index = vscode.workspace.workspaceFolders!.findIndex((item) => item.uri.toString() === folder.toString());
      if (index >= 0) await changeFolders(index, 1);
    }
  });

  it("selects Python through existing folder overrides in a saved one-folder workspace", async () => {
    assert.equal(vscode.workspace.workspaceFolders?.length, 1);
    assert.equal(vscode.workspace.workspaceFile?.scheme, "file");
    const folder = vscode.workspace.workspaceFolders![0];
    const resource = vscode.Uri.joinPath(folder.uri, "python-selection.q1asm");
    const config = vscode.workspace.getConfiguration("q1lens", resource);
    const python = config.get<string>("pythonPath")!;
    const keys = ["pythonPath", "pythonArgs", "q1timeline.pythonPath", "q1timeline.pythonArgs", "pythonEnvironment"];
    const original = keys.map((key) => ({ key, folder: config.inspect(key)?.workspaceFolderValue, workspace: config.inspect(key)?.workspaceValue }));
    const callbacks = new Map<string, (...args: unknown[]) => Promise<unknown>>();
    const subscriptions: vscode.Disposable[] = [];
    const output = { clear() {}, show() {}, appendLine() {}, dispose() {} };
    const proxy = {
      ConfigurationTarget: vscode.ConfigurationTarget, workspace: vscode.workspace, extensions: vscode.extensions,
      window: { get activeTextEditor() { return vscode.window.activeTextEditor; },
        get activeNotebookEditor() { return vscode.window.activeNotebookEditor; },
        createOutputChannel: () => output,
        showQuickPick: async (choices: { label: string }[]) => choices.find((choice) => choice.label === "Enter Python path…"),
        showInputBox: async () => python,
        showInformationMessage: async () => undefined, showErrorMessage: async () => undefined },
      commands: { executeCommand: vscode.commands.executeCommand, registerCommand: (id: string, callback: (...args: unknown[]) => Promise<unknown>) => {
        callbacks.set(id, callback);
        return { dispose() {} };
      } },
    };
    const Module = require("node:module");
    const originalLoad = Module._load;
    const environmentPath = require.resolve("../../src/environment");
    try {
      await vscode.workspace.fs.writeFile(resource, Buffer.from("stop\n"));
      for (const key of keys) await config.update(key,
        key === "pythonEnvironment" ? "auto" : key.endsWith("Args") ? ["-X", "dev"] : "old-folder-python",
        vscode.ConfigurationTarget.WorkspaceFolder);
      assert.equal(vscode.workspace.getConfiguration("q1lens", resource).get("pythonPath"), "old-folder-python");
      Module._load = function (request: string, parent: { filename: string }, isMain: boolean) {
        return request === "vscode" && parent.filename === environmentPath ? proxy : originalLoad.call(this, request, parent, isMain);
      };
      try {
        registerEnvironmentCommands({ subscriptions } as unknown as vscode.ExtensionContext);
      } finally {
        Module._load = originalLoad;
      }
      await callbacks.get("q1lens.selectPythonEnvironment")!(undefined, resource);

      const selected = vscode.workspace.getConfiguration("q1lens", resource);
      for (const key of ["pythonPath", "q1timeline.pythonPath"]) assert.equal(selected.get(key), python);
      for (const key of ["pythonArgs", "q1timeline.pythonArgs"]) assert.deepEqual(selected.get(key), []);
    } finally {
      Module._load = originalLoad;
      subscriptions.forEach((item) => item.dispose());
      for (const value of original) {
        await config.update(value.key, value.folder, vscode.ConfigurationTarget.WorkspaceFolder);
        await config.update(value.key, value.workspace, vscode.ConfigurationTarget.Workspace);
      }
      await vscode.workspace.fs.delete(resource);
    }
  });
});
