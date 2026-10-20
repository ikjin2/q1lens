import assert from "node:assert/strict";
import { mkdtemp, mkdir, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import type * as vscode from "vscode";
import { probePythonEnvironment, PythonEnvironmentReport } from "../src/environment";
import { lastPythonEnvironment, publishPythonEnvironment } from "../src/pythonEnvironmentStatus";

const Module = require("node:module");
const repoRoot = resolve(__dirname, "../../..");

describe("Python environment probe", function () {
  this.timeout(20000);

  it("reports the Python version and source package locations", async () => {
    const report = await probePythonEnvironment("python", [], {
      cwd: repoRoot,
      env: { PYTHONPATH: resolve(repoRoot, "src") },
    });

    assert.equal(report.supported, true);
    assert.match(report.version, /^3\.\d+\.\d+$/);
    assert.ok(report.executable);
    assert.equal(report.q1lensInstalled, true);
    assert.equal(report.packagePath, resolve(repoRoot, "src/q1lens/__init__.py"));
    assert.equal(report.q1asmPath, resolve(repoRoot, "src/q1timeline/cli.py"));
    assert.equal(report.qbsError, undefined);
    assert.equal(report.q1asmError, undefined);
  });

  it("reports a missing package without losing the Python details", async () => {
    const report = await probePythonEnvironment("python", ["-I", "-S"]);

    assert.equal(report.supported, true);
    assert.ok(report.executable);
    assert.equal(report.q1lensInstalled, false);
    assert.equal(report.pipAvailable, false);
    assert.match(report.qbsError ?? "", /No module named 'q1lens'/);
    assert.equal(report.packagePath, undefined);
  });

  it("distinguishes an installed Q1Lens with a missing dependency and does not import pip", async () => {
    const directory = await mkdtemp(resolve(tmpdir(), "q1lens-environment-probe-"));
    try {
      await mkdir(resolve(directory, "q1lens"));
      await writeFile(resolve(directory, "q1lens/__init__.py"), "import missing_q1lens_dependency_for_probe_test\n");
      await mkdir(resolve(directory, "pip"));
      await writeFile(resolve(directory, "pip/__init__.py"), "raise RuntimeError('pip must not be imported by the probe')\n");
      const report = await probePythonEnvironment("python", ["-S"], {
        cwd: directory, env: { PYTHONPATH: directory },
      });

      assert.equal(report.q1lensInstalled, true);
      assert.equal(report.pipAvailable, true);
      assert.equal(report.qbsError, "No module named 'missing_q1lens_dependency_for_probe_test'");
      assert.equal(report.q1asmError, undefined);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("reports an environment without pip without installing or writing anything", async () => {
    const directory = await mkdtemp(resolve(tmpdir(), "q1lens-pipless-probe-"));
    try {
      const report = await probePythonEnvironment("python", ["-I", "-S"], { cwd: directory });

      assert.equal(report.pipAvailable, false);
      assert.equal(report.q1lensInstalled, false);
      assert.deepEqual(await readdir(directory), []);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("explains when the selected executable does not exist", async () => {
    await assert.rejects(
      probePythonEnvironment(resolve(repoRoot, "missing-q1lens-python-executable"), []),
      /Python executable was not found/,
    );
  });

  it("rejects output that is not a Python environment report", async () => {
    for (const output of ["not json", "{}", "null"]) {
      await assert.rejects(
        probePythonEnvironment("python", ["-c", `print(${JSON.stringify(output)})`]),
        /Python did not return a valid environment report/,
      );
    }
  });

  it("stops a Python process that does not finish", async () => {
    await assert.rejects(
      probePythonEnvironment("python", ["-c", "import time; time.sleep(10)"], { timeoutMs: 100 }),
      /Python environment check timed out/,
    );
  });
});

function environmentHarness() {
  const values: Record<string, Record<string, unknown>> = {
    q1lens: {
      pythonPath: "py", pythonArgs: ["-3.12"],
      "q1timeline.pythonPath": "old-python", "q1timeline.pythonArgs": ["-X", "utf8"],
      "q1timeline.analyzer.env": { TEST_TOKEN: "private-test-value" },
    },
  };
  const updates: Array<{ key: string; value: unknown; target: number }> = [];
  const commands = new Map<string, (retryCommand?: string, resource?: vscode.Uri) => Promise<unknown>>();
  const executions: Array<{ command: string; source: unknown }> = [];
  const probes: Array<{ executable: string; args: string[]; env: NodeJS.ProcessEnv }> = [];
  const lines: string[] = [];
  const messages: Array<{ message: string; actions: string[] }> = [];
  const resources: unknown[] = [];
  const state = {
    selectedLabel: undefined as string | undefined,
    enteredPath: undefined as string | undefined,
    informationAction: undefined as string | undefined,
    selectedPython: "/selected/.venv/bin/python",
    failedPython: undefined as string | undefined,
    report: {
      executable: "selected-python", version: "3.12.10", supported: true,
      packagePath: "/site-packages/q1lens/__init__.py", q1asmPath: "/site-packages/q1timeline/cli.py",
    } as PythonEnvironmentReport,
  };
  const source = { fsPath: "/project/sequence.q1asm" };
  const notification = (message: string, ...actions: string[]) => {
    messages.push({ message, actions });
    return state.informationAction
      ? Promise.resolve(state.informationAction)
      : new Promise<string | undefined>(() => {});
  };
  const fakeVscode = {
    ConfigurationTarget: { Workspace: 2, Global: 1 },
    workspace: {
      workspaceFolders: [{ name: "project", uri: { fsPath: repoRoot } }],
      getConfiguration: (section: string) => ({
        get: (key: string, fallback?: unknown) => values[section]?.[key] ?? fallback,
        inspect: (key: string) => ({ workspaceValue: values[section]?.[key] }),
        update: async (key: string, value: unknown, target: number) => {
          values[section] ??= {};
          values[section][key] = value;
          updates.push({ key, value, target });
        },
      }),
    },
    window: {
      activeTextEditor: { document: { uri: source } },
      createOutputChannel: () => ({
        clear: () => { lines.length = 0; },
        show: () => undefined,
        appendLine: (line: string) => lines.push(line),
        dispose: () => undefined,
      }),
      showQuickPick: async (choices: Array<{ label: string }>) => choices.find((choice) => choice.label === state.selectedLabel),
      showInputBox: async () => state.enteredPath,
      showInformationMessage: notification,
      showErrorMessage: notification,
    },
    commands: {
      registerCommand: (name: string, callback: (retryCommand?: string, resource?: vscode.Uri) => Promise<unknown>) => {
        commands.set(name, callback);
        return { dispose: () => undefined };
      },
      executeCommand: async (command: string, selectedSource: unknown) => {
        executions.push({ command, source: selectedSource });
      },
    },
    extensions: { getExtension: () => ({ activate: async () => ({ environments: {
      getActiveEnvironmentPath: (resource: unknown) => { resources.push(resource); return { path: state.selectedPython }; },
      resolveEnvironment: async () => ({ executable: { uri: { fsPath: state.selectedPython } } }),
    } }) }) },
  };
  const originalLoad = Module._load;
  Module._load = function (request: string, parent: unknown, isMain: boolean) {
    if (request === "vscode") return fakeVscode;
    if (request === "node:child_process") return {
      execFile: (executable: string, args: string[], options: { env: NodeJS.ProcessEnv }, callback: (error: null, stdout: string, stderr: string) => void) => {
        probes.push({ executable, args, env: options.env });
        queueMicrotask(() => callback(null, JSON.stringify({ ...state.report, executable,
          ...(state.failedPython === executable ? { qbsError: "missing dependency" } : {}) }), ""));
      },
    };
    return originalLoad.call(this, request, parent, isMain);
  };
  const modulePath = require.resolve("../src/environment");
  delete require.cache[modulePath];
  const environment = require("../src/environment") as typeof import("../src/environment");
  environment.registerEnvironmentCommands({ subscriptions: [] } as unknown as vscode.ExtensionContext);
  return {
    state, updates, probes, lines, messages, executions, source, values, resources, fakeVscode,
    run: (name: string, retryCommand?: string, resource?: vscode.Uri) => {
      const command = commands.get(name);
      assert.ok(command, `${name} must be registered`);
      return command(retryCommand, resource);
    },
    restore: () => {
      delete require.cache[modulePath];
      Module._load = originalLoad;
    },
  };
}

describe("Python environment commands", () => {
  let harness: ReturnType<typeof environmentHarness>;
  beforeEach(() => { harness = environmentHarness(); });
  afterEach(() => { harness.restore(); });

  it("diagnoses the VS Code selection for the original target and reflects later selections", async () => {
    harness.values.q1lens = {};
    const target = harness.source as vscode.Uri;
    harness.fakeVscode.window.activeTextEditor.document.uri = { fsPath: "/other/project.py" };
    await harness.run("q1lens.checkEnvironment", undefined, target);
    assert.deepEqual(harness.resources, [target, target]);
    assert.ok(harness.lines.some((line) => line.includes("Selected by: VS Code selected interpreter")));
    assert.ok(harness.probes.every((probe) => probe.executable === "/selected/.venv/bin/python"));
    harness.state.selectedPython = "/changed/.venv/bin/python";
    await harness.run("q1lens.checkEnvironment", undefined, target);
    assert.ok(harness.probes.slice(2).every((probe) => probe.executable === "/changed/.venv/bin/python"));
    assert.deepEqual(harness.updates, []);
  });

  it("retains the Q1ASM workflow for a custom project filename when checking and reopening", async () => {
    const target = { fsPath: "/project/custom-timeline.yml" } as vscode.Uri;
    publishPythonEnvironment({ resource: target, kind: "q1asm", pythonPath: "old-python", pythonArgs: [], source: "manual", sourceLabel: "Manual Q1ASM" });
    harness.state.failedPython = "py";
    harness.state.informationAction = "Open Timeline";
    await harness.run("q1lens.checkEnvironment", undefined, target);
    await new Promise<void>((done) => setImmediate(done));
    assert.equal(lastPythonEnvironment()?.pythonPath, "old-python");
    assert.equal(lastPythonEnvironment()?.kind, "q1asm");
    assert.ok(harness.messages[0].actions.includes("Open Timeline"));
    assert.deepEqual(harness.executions, [{ command: "q1timeline.openPreview", source: target }]);
  });

  it("selects one launcher and its arguments for both workflows in the workspace", async () => {
    harness.state.selectedLabel = "Current QBS Python";
    await harness.run("q1lens.selectPythonEnvironment");

    assert.deepEqual(harness.updates, [
      { key: "pythonPath", value: "py", target: 2 },
      { key: "pythonArgs", value: ["-3.12"], target: 2 },
      { key: "q1timeline.pythonPath", value: "py", target: 2 },
      { key: "q1timeline.pythonArgs", value: ["-3.12"], target: 2 },
    ]);
    assert.equal(harness.probes.length, 3);
    for (const probe of harness.probes) {
      assert.equal(probe.executable, "py");
      assert.deepEqual(probe.args.slice(0, 2), ["-3.12", "-c"]);
    }
    assert.equal(harness.probes[2].env.TEST_TOKEN, "private-test-value");
  });

  it("clears stale launcher arguments when entering a direct executable path", async () => {
    harness.state.selectedLabel = "Enter Python path…";
    harness.state.enteredPath = " C:\\project with spaces\\python.exe ";
    await harness.run("q1lens.selectPythonEnvironment");

    assert.equal(harness.updates[0].value, "C:\\project with spaces\\python.exe");
    assert.deepEqual(harness.updates[1].value, []);
    assert.deepEqual(harness.updates[3].value, []);
    assert.equal(harness.probes[0].args[0], "-c");
  });

  it("leaves settings unchanged when selection or path entry is cancelled", async () => {
    await harness.run("q1lens.selectPythonEnvironment");
    harness.state.selectedLabel = "Enter Python path…";
    await harness.run("q1lens.selectPythonEnvironment");

    assert.deepEqual(harness.updates, []);
    assert.deepEqual(harness.probes, []);
  });

  it("rejects an unsupported Python before changing settings", async () => {
    harness.state.selectedLabel = "Python on PATH";
    harness.state.report = { executable: "python", version: "3.9.0", supported: false };
    await harness.run("q1lens.selectPythonEnvironment");

    assert.deepEqual(harness.updates, []);
    assert.match(harness.messages[0].message, /Python 3\.9\.0 is too old/);
    assert.deepEqual(harness.messages[0].actions, ["Select Python Environment"]);
  });

  it("returns missing-package results while the notification stays open, with install guidance", async () => {
    harness.state.report = {
      executable: "python", version: "3.12.10", supported: true, qbsError: "No module named 'q1lens'",
    };
    const results = await harness.run("q1lens.checkEnvironment");

    assert.equal((results as unknown[]).length, 2);
    const output = harness.lines.join("\n");
    assert.match(output, /QBS: NOT READY/);
    assert.match(output, /Q1ASM: NOT READY/);
    assert.match(output, /Python: py \(3\.12\.10\)/);
    assert.match(output, /-m pip install '<path-to-q1lens\.whl>'/);
    assert.match(output, /Environment override keys: TEST_TOKEN/);
    assert.doesNotMatch(output, /private-test-value/);
    assert.deepEqual(harness.messages[0].actions, ["Select Python Environment", "Check Again"]);
  });

  it("retries the requested timeline after a successful environment check", async () => {
    harness.state.informationAction = "Retry Timeline";
    await harness.run("q1lens.checkEnvironment", "q1timeline.openPreview");
    await new Promise<void>((resolveDone) => setImmediate(resolveDone));

    assert.deepEqual(harness.executions, [{ command: "q1timeline.openPreview", source: harness.source }]);
  });
});
