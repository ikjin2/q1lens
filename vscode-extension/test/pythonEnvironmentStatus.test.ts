import assert from "node:assert/strict";
import Module = require("node:module");
import { runInNewContext } from "node:vm";
import type * as vscode from "vscode";
import type { PythonEnvironmentDisplay } from "../src/pythonEnvironmentStatus";
import { beginPythonEnvironmentSelection } from "../src/pythonEnvironment";

function harness() {
  const commands = new Map<string, () => Promise<void>>();
  const executions: unknown[][] = [];
  const selections: vscode.Uri[] = [];
  const updates: unknown[] = [];
  const subscriptions: vscode.Disposable[] = [];
  const panels: any[] = [];
  const warnings: string[] = [];
  let updateGate: Promise<void> | undefined;
  const fakeVscode = {
    StatusBarAlignment: { Right: 2 }, ViewColumn: { Active: -1 },
    ConfigurationTarget: { WorkspaceFolder: 3, Global: 1 },
    window: {
      showWarningMessage: async (message: string) => { warnings.push(message); },
      createStatusBarItem: () => ({ show() {}, dispose() {} }),
      createWebviewPanel: () => {
        let receive: (message: unknown) => Promise<void>;
        let dispose: () => void;
        const panel = {
          webview: { html: "", onDidReceiveMessage: (callback: typeof receive) => { receive = callback; return { dispose() {} }; } },
          onDidDispose: (callback: () => void) => { dispose = callback; return { dispose() {} }; },
          reveal() {}, dispose: () => dispose(), send: (message: unknown) => receive(message),
        };
        panels.push(panel);
        return panel;
      },
    },
    workspace: {
      getWorkspaceFolder: () => ({ uri: { fsPath: "/project" } }),
      getConfiguration: (_section: string, resource: vscode.Uri) => ({
        update: async (...args: unknown[]) => { updates.push({ resource, args }); await updateGate; },
      }),
    },
    commands: {
      registerCommand: (name: string, callback: () => Promise<void>) => { commands.set(name, callback); return { dispose() {} }; },
      executeCommand: async (...args: unknown[]) => { executions.push(args); },
    },
  };
  const originalLoad = (Module as any)._load;
  const modulePath = require.resolve("../src/pythonEnvironmentStatus");
  const cached = require.cache[modulePath];
  (Module as any)._load = function (request: string, parent: unknown, isMain: boolean) {
    if (request === "vscode") return fakeVscode;
    if (request === "./pythonEnvironment") return { beginPythonEnvironmentSelection, selectVSCodeInterpreter: async (_vscode: unknown, resource: vscode.Uri) => { selections.push(resource); } };
    return originalLoad.call(this, request, parent, isMain);
  };
  delete require.cache[modulePath];
  const status = require("../src/pythonEnvironmentStatus") as typeof import("../src/pythonEnvironmentStatus");
  status.registerPythonEnvironmentStatus({ subscriptions, extension: { packageJSON: { version: "1.2.3" } } } as vscode.ExtensionContext);
  return {
    ...status, panels, executions, selections, updates, warnings,
    waitForUpdate: (gate: Promise<void>) => { updateGate = gate; },
    open: () => commands.get("q1lens.showPythonEnvironment")!(),
    restore: () => {
      for (const disposable of subscriptions) disposable.dispose();
      (Module as any)._load = originalLoad;
      if (cached) require.cache[modulePath] = cached;
      else delete require.cache[modulePath];
    },
  };
}

function environment(name: string, source: PythonEnvironmentDisplay["source"] = "vscode"): PythonEnvironmentDisplay {
  const path = `/project/${name}.py`;
  return { resource: { fsPath: path, toString: () => `file://${path}` } as vscode.Uri,
    pythonPath: `/env/${name}/python`, pythonArgs: [], source, sourceLabel: source };
}

function buttonMessage(html: string, type: string): unknown {
  let click!: () => void;
  let message: unknown;
  const script = html.match(/<script nonce="[^"]+">([\s\S]*?)<\/script>/)![1];
  runInNewContext(script, {
    acquireVsCodeApi: () => ({ postMessage: (value: unknown) => { message = value; } }),
    document: { querySelectorAll: () => [{ dataset: { action: type }, addEventListener: (_event: string, listener: () => void) => { click = listener; } }] },
  });
  click();
  return message;
}

describe("Python environment status panel", () => {
  let test: ReturnType<typeof harness>;
  beforeEach(() => { test = harness(); });
  afterEach(() => test.restore());

  for (const source of ["vscode", "manual", "notebook"] as const) {
    for (const packageVersion of ["1.2.2", "1.2.4"]) {
      it(`warns once when the ${source} Python package is ${packageVersion} and the extension is 1.2.3`, () => {
        const selected = { ...environment("a", source),
          report: { executable: "/env/a/python", version: "3.12.10", supported: true, packageVersion } };
        test.publishPythonEnvironment(selected);
        test.publishPythonEnvironment(selected);
        assert.equal(test.warnings.length, 1);
        assert.ok(test.warnings[0].includes(`Python package ${packageVersion}, VS Code extension 1.2.3`));
        assert.match(test.warnings[0], /both use the same version/);
        assert.match(test.warnings[0], /\/env\/a\/python/);
      });
    }
  }

  it("keeps matching, unknown and unsupported package versions quiet", () => {
    for (const report of [undefined,
      { executable: "/env/a/python", version: "3.12.10", supported: true },
      { executable: "/env/a/python", version: "3.12.10", supported: true, packageVersion: "1.2.3" },
      { executable: "/env/a/python", version: "3.9.0", supported: false, packageVersion: "1.2.2" }]) {
      test.publishPythonEnvironment({ ...environment("a"), report });
    }
    assert.deepEqual(test.warnings, []);
  });

  it("warns again for a different Python environment or package version", () => {
    for (const [name, packageVersion] of [["a", "1.2.2"], ["b", "1.2.2"], ["b", "1.2.4"]]) {
      test.publishPythonEnvironment({ ...environment(name),
        report: { executable: `/env/${name}/python`, version: "3.12.10", supported: true, packageVersion } });
    }
    assert.equal(test.warnings.length, 3);
  });

  it("does not warn for a stale check result", () => {
    const stale = test.beginPythonEnvironmentCheck();
    test.beginPythonEnvironmentCheck();
    test.publishPythonEnvironment({ ...environment("a"),
      report: { executable: "/env/a/python", version: "3.12.10", supported: true, packageVersion: "1.2.2" } }, stale);
    assert.deepEqual(test.warnings, []);
  });

  it("reopens with the latest checked target and keeps its buttons on that target", async () => {
    test.publishPythonEnvironment(environment("a"));
    await test.open();
    test.panels[0].dispose();
    const latest = environment("b");
    test.publishPythonEnvironment(latest);
    await test.open();
    assert.equal(test.panels.length, 2);
    const panel = test.panels[1];
    assert.match(panel.webview.html, /\/project\/b.py/);
    await panel.send(buttonMessage(panel.webview.html, "check"));
    await panel.send(buttonMessage(panel.webview.html, "select"));
    assert.deepEqual(test.executions, [["q1lens.checkEnvironment", undefined, latest.resource]]);
    assert.deepEqual(test.selections, [latest.resource]);
  });

  it("follows VS Code selection for the manual target displayed by the panel", async () => {
    const latest = environment("b", "manual");
    test.publishPythonEnvironment(latest);
    await test.open();
    const panel = test.panels[0];
    await panel.send(buttonMessage(panel.webview.html, "follow"));
    assert.deepEqual(test.updates, [{ resource: latest.resource, args: ["pythonEnvironment", "vscode", 3] }]);
    assert.deepEqual(test.executions, [["q1lens.checkEnvironment", undefined, latest.resource]]);
  });

  for (const next of ["another target", "a new check for the same target"]) {
    it(`does not reclaim the status after Follow waits for settings while ${next} takes ownership`, async () => {
      test.publishPythonEnvironment(environment("a", "manual"));
      await test.open();
      let release!: () => void;
      test.waitForUpdate(new Promise<void>((resolve) => { release = resolve; }));
      const panel = test.panels[0];
      const follow = panel.send(buttonMessage(panel.webview.html, "follow"));
      await new Promise<void>((done) => setImmediate(done));
      assert.equal(test.updates.length, 1);
      if (next === "another target") test.publishPythonEnvironment(environment("b"));
      else test.beginPythonEnvironmentCheck();
      release();
      await follow;
      assert.deepEqual(test.executions, []);
    });
  }

  for (const type of ["check", "select", "follow"]) {
    it(`does not apply an old target's queued ${type} button message to the new target`, async () => {
      test.publishPythonEnvironment(environment("a", "manual"));
      await test.open();
      const panel = test.panels[0];
      const queued = buttonMessage(panel.webview.html, type);
      test.publishPythonEnvironment(environment("b", "manual"));
      await panel.send(queued);
      assert.deepEqual(test.executions, []);
      assert.deepEqual(test.updates, []);
      assert.deepEqual(test.selections, []);
    });
  }
});
