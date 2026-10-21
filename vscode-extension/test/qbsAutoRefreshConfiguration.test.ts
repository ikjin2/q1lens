import assert from "node:assert/strict";
import type * as vscode from "vscode";
import { registerQbsAutoRefresh } from "../src/qbs/commands";

describe("QBS auto refresh configuration scope", () => {
  it("keeps a pending source refresh when another workspace folder changes its setting", async () => {
    const resource = { fsPath: "C:\\workspace\\a\\schedule.py" } as vscode.Uri;
    let fileChanged!: (uri: vscode.Uri) => void;
    let configurationChanged!: (event: { affectsConfiguration(section: string, target?: vscode.Uri): boolean }) => void;
    let runs = 0;
    let created = 0;
    let disposed = 0;
    let enabled = true;
    const scopes: Array<vscode.Uri | undefined> = [];
    const registration = registerQbsAutoRefresh({
      resource: () => resource,
      readEnabled: () => enabled,
      createWatcher: () => {
        created += 1;
        return {
          onDidChange: (listener) => { fileChanged = listener; return { dispose() {} }; },
          onDidCreate: () => ({ dispose() {} }), onDidDelete: () => ({ dispose() {} }),
          dispose() { disposed += 1; },
        };
      },
      onDidChangeConfiguration: (listener) => { configurationChanged = listener; return { dispose() {} }; },
      shouldRefresh: () => true,
      run: async () => { runs += 1; },
      delayMs: 5,
    });
    try {
      fileChanged(resource);
      configurationChanged({ affectsConfiguration: (key, target) => {
        scopes.push(target);
        return key === "q1lens.qbs.autoRefresh" && (!target || target.fsPath.includes("\\b\\"));
      } });
      await new Promise((resolve) => setTimeout(resolve, 30));
      assert.equal(runs, 1, "Changing folder B must not discard the queued source edit in folder A");
      assert.equal(created, 1);
      assert.ok(scopes.every((target) => target === resource));

      enabled = false;
      configurationChanged({ affectsConfiguration: (key, target) => key === "q1lens.qbs.autoRefresh" && target === resource });
      assert.equal(disposed, 1, "Changing the active source setting must still stop its watcher");
    } finally { registration.dispose(); }
  });
});
