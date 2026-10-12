import assert from "node:assert/strict";
import * as vscode from "vscode";
import { isDeepStrictEqual } from "node:util";
import { readSettings } from "../../src/qbs/settings";
import { q1timelineConfigValue } from "../../src/q1timeline/settings";

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
});
