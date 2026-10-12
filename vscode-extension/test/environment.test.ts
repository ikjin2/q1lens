import assert from "node:assert/strict";
import { resolve } from "node:path";
import type * as vscode from "vscode";
import { probePythonEnvironment, PythonEnvironmentReport } from "../src/environment";

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
    assert.equal(report.packagePath, resolve(repoRoot, "src/q1lens/__init__.py"));
    assert.equal(report.q1asmPath, resolve(repoRoot, "src/q1timeline/cli.py"));
    assert.equal(report.qbsError, undefined);
    assert.equal(report.q1asmError, undefined);
  });

  it("reports a missing package without losing the Python details", async () => {
    const report = await probePythonEnvironment("python", ["-I", "-S"]);

    assert.equal(report.supported, true);
    assert.ok(report.executable);
    assert.match(report.qbsError ?? "", /No module named 'q1lens'/);
    assert.equal(report.packagePath, undefined);
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
  const commands = new Map<string, (retryCommand?: string) => Promise<unknown>>();
  const executions: Array<{ command: string; source: unknown }> = [];
  const probes: Array<{ executable: string; args: string[]; env: NodeJS.ProcessEnv }> = [];
  const lines: string[] = [];
  const messages: Array<{ message: string; actions: string[] }> = [];
  const state = {
    selectedLabel: undefined as string | undefined,
    enteredPath: undefined as string | undefined,
    informationAction: undefined as string | undefined,
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
      registerCommand: (name: string, callback: (retryCommand?: string) => Promise<unknown>) => {
        commands.set(name, callback);
        return { dispose: () => undefined };
      },
      executeCommand: async (command: string, selectedSource: unknown) => {
        executions.push({ command, source: selectedSource });
      },
    },
  };
  const originalLoad = Module._load;
  Module._load = function (request: string, parent: unknown, isMain: boolean) {
    if (request === "vscode") return fakeVscode;
    if (request === "node:child_process") return {
      execFile: (executable: string, args: string[], options: { env: NodeJS.ProcessEnv }, callback: (error: null, stdout: string, stderr: string) => void) => {
        probes.push({ executable, args, env: options.env });
        queueMicrotask(() => callback(null, JSON.stringify({ ...state.report, executable }), ""));
      },
    };
    return originalLoad.call(this, request, parent, isMain);
  };
  const modulePath = require.resolve("../src/environment");
  delete require.cache[modulePath];
  const environment = require("../src/environment") as typeof import("../src/environment");
  environment.registerEnvironmentCommands({ subscriptions: [] } as unknown as vscode.ExtensionContext);
  return {
    state, updates, probes, lines, messages, executions, source,
    run: (name: string, retryCommand?: string) => {
      const command = commands.get(name);
      assert.ok(command, `${name} must be registered`);
      return command(retryCommand);
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
