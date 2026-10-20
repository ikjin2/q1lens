import assert from "node:assert/strict";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import type * as vscode from "vscode";
import type { PythonEnvironmentReport } from "../src/environment";
import type { ResolvedPythonEnvironment } from "../src/pythonEnvironment";
import { pythonEnvironmentHtml } from "../src/pythonEnvironmentStatus";
import { notebookSetupHtml } from "../src/notebookSetupHtml";

const Module = require("node:module");

function harness() {
  const resource = { fsPath: join(tmpdir(), "project", "sequence.q1asm") } as vscode.Uri;
  const state = {
    selected: { resource, pythonPath: join(tmpdir(), "project", ".venv", "bin", "python"), pythonArgs: [],
      source: "vscode", sourceLabel: "VS Code selected interpreter" } as ResolvedPythonEnvironment,
    report: { executable: "/resolved/python", version: "3.12.10", supported: true, q1lensInstalled: true, packageVersion: "0.2.2" } as PythonEnvironmentReport,
    action: undefined as string | undefined,
    error: undefined as Error | undefined,
    setup: undefined as (() => void) | undefined,
    trusted: true,
  };
  const resolutions: unknown[] = [], probes: any[] = [], displays: any[] = [], setupCalls: any[] = [], messages: string[] = [];
  const fakeVscode = {
    workspace: { get isTrusted() { return state.trusted; }, getConfiguration: (_section: string, target: vscode.Uri) => {
      assert.equal(target, resource);
      return { get: (key: string, fallback: unknown) => key === "q1timeline.analyzer.env" ? { EXAMPLE_PATH: "/example" } : fallback };
    } },
    window: {
      showInformationMessage: async (message: string) => { messages.push(message); const action = state.action; state.action = undefined; return action; },
      showErrorMessage: async (message: string) => { messages.push(message); return undefined; },
      showTextDocument: async () => undefined,
    },
    extensions: { getExtension: () => ({ packageJSON: { version: "0.2.2" } }) },
  } as unknown as typeof vscode;
  const originalLoad = Module._load;
  Module._load = function (request: string, parent: unknown, isMain: boolean) {
    if (request === "./pythonEnvironment") return {
      resolveFilePythonEnvironment: async (_vscode: unknown, target: vscode.Uri, kind: string) => {
        resolutions.push({ target, kind });
        if (state.error) throw state.error;
        return { ...state.selected };
      },
    };
    if (request === "./environment") return { probePythonEnvironment: async (pythonPath: string, args: string[], options: unknown) => {
      probes.push({ pythonPath, args, options }); return { ...state.report };
    } };
    if (request === "./pythonEnvironmentStatus") return { publishPythonEnvironment: (display: unknown) => displays.push(display) };
    if (request === "./notebookEnvironment") return { showNotebookSetup: async (...args: any[]) => {
      setupCalls.push(args); state.setup?.();
      const checked = await args[4]();
      return checked.report.qbsError ? undefined : { pythonPath: checked.environment.executable, pythonArgs: [] };
    } };
    return originalLoad.call(this, request, parent, isMain);
  };
  const modulePath = require.resolve("../src/fileEnvironment");
  delete require.cache[modulePath];
  const { ensureFilePythonEnvironment } = require("../src/fileEnvironment") as typeof import("../src/fileEnvironment");
  const output = { appendLine: () => undefined, show: () => undefined } as unknown as vscode.OutputChannel;
  return { resource, state, resolutions, probes, displays, setupCalls, messages,
    run: (kind: "qbs" | "q1asm" = "q1asm") => ensureFilePythonEnvironment(fakeVscode, resource, kind, output),
    restore: () => { Module._load = originalLoad; delete require.cache[modulePath]; },
  };
}

describe("File Python environment setup", () => {
  let test: ReturnType<typeof harness>;
  beforeEach(() => { test = harness(); });
  afterEach(() => test.restore());

  it("checks and publishes the selected target environment with scoped analyzer variables", async () => {
    assert.deepEqual(await test.run(), test.state.selected);
    assert.deepEqual(test.resolutions, [{ target: test.resource, kind: "q1asm" }]);
    assert.deepEqual(test.probes[0], { pythonPath: test.state.selected.pythonPath, args: [],
      options: { cwd: dirname(test.resource.fsPath), env: { EXAMPLE_PATH: "/example" } } });
    assert.equal(test.displays.at(-1).sourceLabel, "VS Code selected interpreter");
    assert.equal(test.displays.at(-1).report.packageVersion, "0.2.2");
    assert.equal(test.setupCalls.length, 0);
  });

  it("preserves a manual launcher and arguments instead of replacing it with probe executable", async () => {
    test.state.selected = { ...test.state.selected, pythonPath: "uv", pythonArgs: ["run", "python"], source: "manual", sourceLabel: "Manual setting: q1lens.pythonPath" };
    assert.deepEqual(await test.run("qbs"), test.state.selected);
    assert.deepEqual(test.probes[0].args, ["run", "python"]);
    assert.deepEqual(test.probes[0].options.env, {});
  });

  it("rechecks the original file selection after user-managed setup and returns the new environment", async () => {
    test.state.report.qbsError = "No module named 'q1lens'";
    test.state.report.q1lensInstalled = false;
    test.state.action = "Show Setup";
    test.state.setup = () => {
      test.state.selected = { ...test.state.selected, pythonPath: "/new/venv/bin/python" };
      test.state.report = { ...test.state.report, executable: "/new/venv/bin/python", q1lensInstalled: true, qbsError: undefined };
    };
    assert.equal((await test.run())?.pythonPath, "/new/venv/bin/python");
    assert.equal(test.resolutions.length, 2);
    assert.equal(test.setupCalls[0][1], test.resource);
    assert.equal(test.setupCalls[0][6], true);
    assert.equal(test.setupCalls[0][2].sourceLabel, "VS Code selected interpreter");
    assert.match(test.messages[0], /not installed/);
  });

  it("distinguishes broken imports and lets users dismiss setup without executing a fallback", async () => {
    test.state.report.qbsError = "No module named dependency";
    assert.equal(await test.run(), undefined);
    assert.match(test.messages[0], /could not be imported/);
    assert.equal(test.probes.length, 1);
    assert.equal(test.setupCalls.length, 0);
  });

  it("stops when resolution fails, instead of probing python on PATH", async () => {
    test.state.error = new Error("Select an interpreter for this project.");
    assert.equal(await test.run(), undefined);
    assert.equal(test.probes.length, 0);
    assert.match(test.messages[0], /Select an interpreter/);
  });

  it("does not resolve or execute Python in an untrusted workspace", async () => {
    test.state.trusted = false;
    assert.equal(await test.run(), undefined);
    assert.equal(test.resolutions.length, 0);
    assert.equal(test.probes.length, 0);
  });

  it("rejects unsupported Python without offering to install Q1Lens", async () => {
    test.state.report.supported = false;
    test.state.report.version = "3.9.0";
    assert.equal(await test.run(), undefined);
    assert.match(test.messages[0], /Select Python 3.10/);
    assert.equal(test.setupCalls.length, 0);
  });
});

describe("Python environment UI", () => {
  it("shows escaped source, path, target and Refresh behavior", () => {
    const html = pythonEnvironmentHtml({ pythonPath: "/env/<python>", pythonArgs: [], source: "vscode", sourceLabel: "VS Code selected interpreter",
      resource: { fsPath: "/project/<script>.py" } as vscode.Uri,
      report: { executable: "/env/<python>", version: "3.12", supported: true, packageVersion: "0.2.2" } });
    assert.match(html, /VS Code selected interpreter/);
    assert.match(html, /\/env\/&lt;python&gt;/);
    assert.match(html, /\/project\/&lt;script&gt;.py/);
    assert.match(html, /Refresh checks the VS Code Python selection/);
    assert.match(html, /Select Interpreter/);
    assert.doesNotMatch(html, /<script>.py/);
  });

  it("identifies manual settings and keeps their launcher visible", () => {
    const html = pythonEnvironmentHtml({ pythonPath: "uv", pythonArgs: ["run", "python"], source: "manual", sourceLabel: "Manual setting: q1lens.pythonPath",
      resource: { fsPath: "/project/script.py" } as vscode.Uri });
    assert.match(html, /Manual setting: q1lens.pythonPath/);
    assert.match(html, /Launch command/);
    assert.match(html, /Open Python Settings/);
    assert.match(html, /Follow VS Code Selection/);
    assert.match(html, /Keeps your saved Q1Lens paths/);
  });

  it("reuses user-managed installation for file environments without notebook wording", () => {
    const html = notebookSetupHtml({ notebook: "sequence.q1asm", pythonPath: "/venv/bin/python", issue: "Q1Lens is not installed",
      report: { executable: "/venv/bin/python", version: "3.12", supported: true }, version: "0.2.2", installer: "uv", notice: "",
      fileEnvironment: true, sourceLabel: "VS Code selected interpreter" });
    assert.match(html, /VS Code selected interpreter/);
    assert.match(html, /Open File/);
    assert.match(html, /Copy Command/);
    assert.match(html, /Insert into Terminal/);
    assert.doesNotMatch(html, /notebook's|Open Notebook/);
  });
});
