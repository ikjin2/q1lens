import assert from "node:assert/strict";
import Module = require("node:module");
import type * as vscode from "vscode";
import { readSettings } from "../src/qbs/settings";

function withSettings(
  values: Record<string, Record<string, unknown>>,
  check: (resource: vscode.Uri, resources: unknown[]) => void,
): void {
  const resource = { fsPath: "C:\\workspace\\folder-b\\schedule.py" } as vscode.Uri;
  const resources: unknown[] = [];
  const defaults: Record<string, Record<string, unknown>> = {
    q1lens: { pythonPath: "python", pythonArgs: [], "qbs.autoRefresh": false, revealOutputChannel: true },
    qbloxTimeline: { "qbs.autoRefresh": false },
    qbsTimeline: { autoRefresh: false, revealOutputChannel: true },
  };
  const originalLoad = (Module as any)._load;
  (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
    if (request === "vscode") return { workspace: {
      getConfiguration: (section: string, target: unknown) => {
        resources.push(target);
        const configured = target === resource ? values[section] ?? {} : {};
        return {
          get: (key: string, fallback: unknown) => configured[key] ?? defaults[section]?.[key] ?? fallback,
          inspect: (key: string) => ({ workspaceFolderValue: configured[key], defaultValue: defaults[section]?.[key] }),
        };
      },
    } };
    return originalLoad.call(this, request, parent, isMain);
  };
  try { check(resource, resources); }
  finally { (Module as any)._load = originalLoad; }
}

describe("QBS settings compatibility", () => {
  it("honors folder-scoped legacy refresh and output visibility despite registered defaults", () => {
    withSettings({ qbsTimeline: { autoRefresh: true, revealOutputChannel: false } }, (resource, resources) => {
      const settings = readSettings(resource);
      assert.equal(settings.autoRefresh, true);
      assert.equal(settings.revealOutputChannel, false);
      assert.ok(resources.length > 0 && resources.every((target) => target === resource));
    });
  });

  it("prefers explicit integrated refresh settings ahead of legacy values", () => {
    withSettings({ qbloxTimeline: { "qbs.autoRefresh": true }, qbsTimeline: { autoRefresh: false } }, (resource) => {
      assert.equal(readSettings(resource).autoRefresh, true);
    });
  });

  it("honors explicit primary booleans even when they equal registered defaults", () => {
    withSettings({ q1lens: { "qbs.autoRefresh": false, revealOutputChannel: true },
      qbsTimeline: { autoRefresh: true, revealOutputChannel: false } }, (resource) => {
      const settings = readSettings(resource);
      assert.equal(settings.autoRefresh, false);
      assert.equal(settings.revealOutputChannel, true);
    });
  });

  it("uses the documented defaults when no scope configures the booleans", () => {
    withSettings({}, (resource) => {
      const settings = readSettings(resource);
      assert.equal(settings.autoRefresh, false);
      assert.equal(settings.revealOutputChannel, true);
    });
  });
});
