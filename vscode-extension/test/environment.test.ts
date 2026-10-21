import assert from "node:assert/strict";
import { mkdtemp, mkdir, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import type * as vscode from "vscode";
import { probePythonEnvironment, PythonEnvironmentReport } from "../src/environment";
import { lastPythonEnvironment, publishPythonEnvironment, registerPythonEnvironmentStatus, type PythonEnvironmentCheck } from "../src/pythonEnvironmentStatus";

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
  const commands = new Map<string, (retryCommand?: string, resource?: vscode.Uri, retryTarget?: vscode.Uri) => Promise<unknown>>();
  const executions: Array<{ command: string; source: unknown }> = [];
  const probes: Array<{ executable: string; args: string[]; env: NodeJS.ProcessEnv }> = [];
  const deferredProbes: Array<() => void> = [];
  const lines: string[] = [];
  const messages: Array<{ message: string; actions: string[] }> = [];
  const resources: unknown[] = [];
  const notebooks: unknown[] = [];
  const openedNotebooks: unknown[] = [];
  const state = {
    selectedLabel: undefined as string | undefined,
    enteredPath: undefined as string | undefined,
    informationAction: undefined as string | undefined,
    informationReply: undefined as Promise<string | undefined> | undefined,
    selectedPython: "/selected/.venv/bin/python",
    selectedByResource: undefined as Record<string, string> | undefined,
    failedPython: undefined as string | undefined,
    rejectedPython: undefined as string | undefined,
    deferredPython: undefined as string | undefined,
    report: {
      executable: "selected-python", version: "3.12.10", supported: true,
      packagePath: "/site-packages/q1lens/__init__.py", q1asmPath: "/site-packages/q1timeline/cli.py",
    } as PythonEnvironmentReport,
  };
  const source = { fsPath: "/project/sequence.q1asm" };
  const notification = (message: string, ...actions: string[]) => {
    messages.push({ message, actions });
    return state.informationReply ?? (state.informationAction
      ? Promise.resolve(state.informationAction)
      : new Promise<string | undefined>(() => {}));
  };
  const fakeVscode = {
    ConfigurationTarget: { WorkspaceFolder: 3, Workspace: 2, Global: 1 },
    workspace: {
      workspaceFolders: [{ name: "project", uri: { fsPath: repoRoot } }],
      getWorkspaceFolder: (_resource: unknown) => undefined as { name: string; uri: { fsPath: string } } | undefined,
      openNotebookDocument: async (resource: unknown) => resource,
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
      activeNotebookEditor: undefined as { notebook: { uri: { fsPath: string } } } | undefined,
      showNotebookDocument: async (notebook: unknown) => { openedNotebooks.push(notebook); },
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
      registerCommand: (name: string, callback: (retryCommand?: string, resource?: vscode.Uri, retryTarget?: vscode.Uri) => Promise<unknown>) => {
        commands.set(name, callback);
        return { dispose: () => undefined };
      },
      executeCommand: async (command: string, selectedSource: unknown) => {
        executions.push({ command, source: selectedSource });
      },
    },
    extensions: { getExtension: () => ({ activate: async () => ({ environments: {
      getActiveEnvironmentPath: (resource: { fsPath: string }) => { resources.push(resource); return { path: state.selectedByResource?.[resource.fsPath] ?? state.selectedPython }; },
      resolveEnvironment: async (selected: { path: string }) => ({ executable: { uri: { fsPath: selected.path } } }),
    } }) }) },
  };
  const originalLoad = Module._load;
  Module._load = function (request: string, parent: unknown, isMain: boolean) {
    if (request === "vscode") return fakeVscode;
    if (request === "./notebookEnvironment") return {
      ensureNotebookEnvironment: async (_vscode: unknown, resource: vscode.Uri, _output: unknown, request?: PythonEnvironmentCheck) => {
        notebooks.push(resource);
        publishPythonEnvironment({ resource, pythonPath: "notebook-python", pythonArgs: [], source: "notebook", sourceLabel: "Notebook kernel", report: state.report }, request);
        return { pythonPath: "notebook-python", pythonArgs: [] };
      },
    };
    if (request === "node:child_process") return {
      execFile: (executable: string, args: string[], options: { env: NodeJS.ProcessEnv }, callback: (error: Error | null, stdout: string, stderr: string) => void) => {
        probes.push({ executable, args, env: options.env });
        const report = { ...state.report, executable,
          ...(state.failedPython === executable ? { qbsError: "missing dependency" } : {}) };
        const complete = () => state.rejectedPython === executable
          ? callback(new Error("Probe failed"), "", "Original interpreter probe failed")
          : callback(null, JSON.stringify(report), "");
        if (state.deferredPython === executable) deferredProbes.push(complete);
        else queueMicrotask(complete);
      },
    };
    return originalLoad.call(this, request, parent, isMain);
  };
  const modulePath = require.resolve("../src/environment");
  const fileModulePath = require.resolve("../src/fileEnvironment");
  const cachedFileModule = require.cache[fileModulePath];
  delete require.cache[modulePath];
  delete require.cache[fileModulePath];
  const environment = require("../src/environment") as typeof import("../src/environment");
  environment.registerEnvironmentCommands({ subscriptions: [] } as unknown as vscode.ExtensionContext);
  return {
    state, updates, probes, deferredProbes, lines, messages, executions, source, values, resources, notebooks, openedNotebooks, fakeVscode,
    run: (name: string, retryCommand?: string, resource?: vscode.Uri, retryTarget?: vscode.Uri) => {
      const command = commands.get(name);
      assert.ok(command, `${name} must be registered`);
      return command(retryCommand, resource, retryTarget);
    },
    analyze: (resource: vscode.Uri) => {
      const { ensureFilePythonEnvironment } = require("../src/fileEnvironment") as typeof import("../src/fileEnvironment");
      return ensureFilePythonEnvironment(fakeVscode as unknown as typeof vscode, resource, "q1asm", {
        appendLine() {}, show() {},
      } as unknown as vscode.OutputChannel);
    },
    restore: () => {
      delete require.cache[modulePath];
      if (cachedFileModule) require.cache[fileModulePath] = cachedFileModule;
      else delete require.cache[fileModulePath];
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
    assert.deepEqual(harness.resources, [target, target, target, target, target, target]);
    assert.ok(harness.lines.some((line) => line.includes("Selected by: VS Code selected interpreter")));
    assert.ok(harness.probes.every((probe) => probe.executable === "/selected/.venv/bin/python"));
    harness.state.selectedPython = "/changed/.venv/bin/python";
    await harness.run("q1lens.checkEnvironment", undefined, target);
    assert.ok(harness.probes.slice(2).every((probe) => probe.executable === "/changed/.venv/bin/python"));
    assert.deepEqual(harness.updates, []);
  });

  for (const failed of [false, true]) {
    it(`does not publish an obsolete interpreter after its ${failed ? "failed" : "successful"} subprocess probe finishes`, async () => {
      harness.values.q1lens = { pythonEnvironment: "vscode" };
      harness.state.selectedPython = "/env/a/python";
      harness.state.deferredPython = "/env/a/python";
      if (failed) harness.state.rejectedPython = "/env/a/python";
      const checking = harness.run("q1lens.checkEnvironment", undefined, harness.source as vscode.Uri) as Promise<any[]>;
      await new Promise<void>((resolve) => setImmediate(resolve));
      assert.equal(harness.deferredProbes.length, 2);
      harness.state.selectedPython = "/env/b/python";
      harness.deferredProbes.forEach((complete) => complete());
      const results = await checking;
      assert.ok(results.every((result) => !result.report && /Python selection changed/i.test(result.error)));
      assert.equal(lastPythonEnvironment()?.report, undefined);
      assert.match(lastPythonEnvironment()?.error ?? "", /Python selection changed/i);
      assert.ok(!harness.lines.some((line) => /\bREADY$/.test(line) && !line.includes("NOT READY")));

      harness.state.deferredPython = undefined;
      harness.state.rejectedPython = undefined;
      await harness.run("q1lens.checkEnvironment", undefined, harness.source as vscode.Uri);
      assert.equal(lastPythonEnvironment()?.report?.executable, "/env/b/python");
    });
  }

  it("keeps an explicit manual launcher when the VS Code selection changes during its probe", async () => {
    harness.state.deferredPython = "py";
    const checking = harness.run("q1lens.checkEnvironment", undefined, harness.source as vscode.Uri);
    await new Promise<void>((resolve) => setImmediate(resolve));
    assert.equal(harness.deferredProbes.length, 1);
    harness.state.selectedPython = "/env/b/python";
    harness.deferredProbes[0]();
    await checking;
    assert.equal(lastPythonEnvironment()?.source, "manual");
    assert.equal(lastPythonEnvironment()?.report?.executable, "old-python");
    assert.equal(harness.resources.length, 0);
  });

  for (const failed of [false, true]) {
    it(`keeps a newer target's environment when an older ${failed ? "failed" : "successful"} check finishes late`, async () => {
      harness.values.q1lens = {};
      const olderTarget = { fsPath: "/older/sequence.q1asm" } as vscode.Uri;
      const newerTarget = { fsPath: "/newer/sequence.q1asm" } as vscode.Uri;
      harness.state.selectedPython = "/older/venv/bin/python";
      harness.state.deferredPython = harness.state.selectedPython;
      if (failed) harness.state.failedPython = harness.state.selectedPython;
      const older = harness.run("q1lens.checkEnvironment", undefined, olderTarget);
      await new Promise<void>((done) => setImmediate(done));
      assert.equal(harness.deferredProbes.length, 2);

      harness.state.selectedPython = "/newer/venv/bin/python";
      await harness.run("q1lens.checkEnvironment", undefined, newerTarget);
      const newer = lastPythonEnvironment();
      assert.equal(newer?.resource, newerTarget);
      const output = harness.lines.join("\n");
      const notifications = harness.messages.length;

      for (const complete of harness.deferredProbes) complete();
      assert.equal((await older as unknown[]).length, 2);
      assert.equal(lastPythonEnvironment(), newer);
      assert.equal(harness.lines.join("\n"), output);
      assert.equal(harness.messages.length, notifications);
    });
  }

  it("keeps a newer analysis environment when an older environment command finishes late", async () => {
    harness.values.q1lens = {};
    const olderTarget = { fsPath: "/older/sequence.q1asm" } as vscode.Uri;
    const newerTarget = { fsPath: "/newer/sequence.q1asm" } as vscode.Uri;
    harness.state.selectedPython = "/older/venv/bin/python";
    harness.state.deferredPython = harness.state.selectedPython;
    const older = harness.run("q1lens.checkEnvironment", undefined, olderTarget);
    await new Promise<void>((done) => setImmediate(done));
    assert.equal(harness.deferredProbes.length, 2);
    harness.state.selectedPython = "/newer/venv/bin/python";
    assert.ok(await harness.analyze(newerTarget));
    const newer = lastPythonEnvironment();
    assert.equal(newer?.resource, newerTarget);
    for (const complete of harness.deferredProbes) complete();
    await older;
    assert.equal(lastPythonEnvironment(), newer);
  });

  it("preserves a newer pending environment command when an older analysis reports progress", async () => {
    harness.values.q1lens = {};
    const olderTarget = { fsPath: "/older/sequence.q1asm" } as vscode.Uri;
    const newerTarget = { fsPath: "/newer/sequence.q1asm" } as vscode.Uri;
    harness.state.selectedByResource = { [olderTarget.fsPath]: "/older/venv/bin/python", [newerTarget.fsPath]: "/newer/venv/bin/python" };
    harness.state.selectedPython = "/older/venv/bin/python";
    harness.state.deferredPython = harness.state.selectedPython;
    const older = harness.analyze(olderTarget);
    await new Promise<void>((done) => setImmediate(done));
    assert.equal(harness.deferredProbes.length, 1);
    const previous = lastPythonEnvironment();
    harness.state.selectedPython = "/newer/venv/bin/python";
    harness.state.deferredPython = harness.state.selectedPython;
    const newer = harness.run("q1lens.checkEnvironment", undefined, newerTarget);
    await new Promise<void>((done) => setImmediate(done));
    assert.equal(harness.deferredProbes.length, 3);
    harness.deferredProbes[0]();
    assert.ok(await older);
    assert.equal(lastPythonEnvironment(), previous);
    for (const complete of harness.deferredProbes.slice(1)) complete();
    await newer;
    assert.equal(lastPythonEnvironment()?.resource, newerTarget);
    assert.equal(lastPythonEnvironment()?.report?.executable, "/newer/venv/bin/python");
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

  it("checks and opens uppercase Q1ASM files through the Q1ASM environment", async () => {
    const target = { fsPath: "/project/SEQUENCE.Q1ASM" } as vscode.Uri;
    harness.state.failedPython = "py";
    harness.state.informationAction = "Open Timeline";
    await harness.run("q1lens.checkEnvironment", undefined, target);
    await new Promise<void>((done) => setImmediate(done));
    assert.equal(lastPythonEnvironment()?.kind, "q1asm");
    assert.equal(lastPythonEnvironment()?.pythonPath, "old-python");
    assert.match(harness.messages[0].message, /environment is ready/);
    assert.deepEqual(harness.executions, [{ command: "q1timeline.openPreview", source: target }]);
  });

  it("checks uppercase notebooks through their kernel and reopens the original notebook", async () => {
    const target = { fsPath: "/project/NOTEBOOK.IPYNB" } as vscode.Uri;
    harness.state.informationAction = "Open Timeline";
    await harness.run("q1lens.checkEnvironment", undefined, target);
    await new Promise<void>((done) => setImmediate(done));
    assert.deepEqual(harness.notebooks, [target]);
    assert.equal(lastPythonEnvironment()?.source, "notebook");
    assert.deepEqual(harness.probes, []);
    assert.deepEqual(harness.openedNotebooks, [target]);
    assert.deepEqual(harness.executions, [{ command: "qbsTimeline.openNotebookTimelineFromCell", source: undefined }]);
  });

  for (const scope of ["folder", "workspace", "global"] as const) {
    it(`keeps the latest ${scope} Python selection when an older executable probe finishes late`, async () => {
      const olderTarget = harness.source as vscode.Uri;
      const newerTarget = { fsPath: "/project/other.q1asm" } as vscode.Uri;
      if (scope === "folder") harness.fakeVscode.workspace.getWorkspaceFolder = () => ({ name: "project", uri: { fsPath: "/project" } });
      if (scope === "global") harness.fakeVscode.workspace.workspaceFolders = [];
      harness.state.selectedLabel = "Enter Python path…";
      harness.state.enteredPath = "/older/python";
      harness.state.deferredPython = harness.state.enteredPath;
      const older = harness.run("q1lens.selectPythonEnvironment", undefined, olderTarget);
      await new Promise<void>((done) => setImmediate(done));
      assert.equal(harness.deferredProbes.length, 1);
      harness.state.enteredPath = "/newer/python";
      await harness.run("q1lens.selectPythonEnvironment", undefined, newerTarget);
      assert.equal(harness.values.q1lens.pythonPath, "/newer/python");
      const updates = harness.updates.length;
      const displayed = lastPythonEnvironment();
      harness.state.deferredPython = undefined;
      harness.deferredProbes[0]();
      await older;

      assert.equal(harness.values.q1lens.pythonPath, "/newer/python");
      assert.equal(harness.values.q1lens["q1timeline.pythonPath"], "/newer/python");
      assert.equal(harness.updates.length, updates, "The superseded choice must not write any settings");
      assert.ok(harness.updates.every((update) => update.target === ({ folder: 3, workspace: 2, global: 1 })[scope]));
      assert.equal(lastPythonEnvironment(), displayed);
    });
  }

  it("keeps independent workspace-folder selections when their probes finish in reverse order", async () => {
    const first = { fsPath: "/first/sequence.q1asm" } as vscode.Uri;
    const second = { fsPath: "/second/sequence.q1asm" } as vscode.Uri;
    const values: Record<string, Record<string, unknown>> = {
      first: { pythonPath: "/first/initial", "q1timeline.pythonPath": "/first/initial" },
      second: { pythonPath: "/second/initial", "q1timeline.pythonPath": "/second/initial" },
    };
    const folderKey = (resource: vscode.Uri) => resource.fsPath.startsWith("/first/") ? "first" : "second";
    harness.fakeVscode.workspace.getWorkspaceFolder = (resource: unknown) => {
      const key = folderKey(resource as vscode.Uri);
      return { name: key, uri: { fsPath: `/${key}` } };
    };
    harness.fakeVscode.workspace.getConfiguration = (section: string, resource?: vscode.Uri) => {
      const settings = section === "q1lens" ? values[folderKey(resource!)] : {};
      return {
        get: (key: string, fallback?: unknown) => settings[key] ?? fallback,
        inspect: (key: string) => ({ workspaceValue: settings[key] }),
        update: async (key: string, value: unknown, target: number) => {
          assert.equal(target, 3);
          settings[key] = value;
          harness.updates.push({ key, value, target });
        },
      };
    };
    harness.state.selectedLabel = "Enter Python path…";
    harness.state.enteredPath = "/first/python";
    harness.state.deferredPython = harness.state.enteredPath;
    const older = harness.run("q1lens.selectPythonEnvironment", undefined, first);
    await new Promise<void>((done) => setImmediate(done));
    assert.equal(harness.deferredProbes.length, 1);
    harness.state.enteredPath = "/second/python";
    await harness.run("q1lens.selectPythonEnvironment", undefined, second);
    harness.state.deferredPython = undefined;
    harness.deferredProbes[0]();
    await older;

    for (const key of ["first", "second"]) {
      assert.equal(values[key].pythonPath, `/${key}/python`);
      assert.equal(values[key]["q1timeline.pythonPath"], `/${key}/python`);
    }
    assert.equal(harness.updates.length, 8);
  });

  it("does not report an obsolete Python selection failure after a newer choice succeeds", async () => {
    harness.state.selectedLabel = "Enter Python path…";
    harness.state.enteredPath = "/older/python";
    harness.state.report.supported = false;
    harness.state.deferredPython = harness.state.enteredPath;
    const older = harness.run("q1lens.selectPythonEnvironment");
    await new Promise<void>((done) => setImmediate(done));
    assert.equal(harness.deferredProbes.length, 1);
    harness.state.report.supported = true;
    harness.state.enteredPath = "/newer/python";
    await harness.run("q1lens.selectPythonEnvironment");
    const messages = harness.messages.length;
    harness.deferredProbes[0]();
    await older;

    assert.equal(harness.values.q1lens.pythonPath, "/newer/python");
    assert.equal(harness.messages.length, messages, "The superseded probe must not reopen environment selection");
  });

  it("finishes settings writes in order when Python selection overlaps an update already in flight", async () => {
    const getConfiguration = harness.fakeVscode.workspace.getConfiguration;
    let release!: () => void;
    let blocked = false;
    const waiting = new Promise<void>((resolve) => { release = resolve; });
    harness.fakeVscode.workspace.getConfiguration = (section: string) => {
      const config = getConfiguration(section);
      return { ...config, update: async (key: string, value: unknown, target: number) => {
        if (key === "pythonPath" && value === "/older/python") { blocked = true; await waiting; }
        await config.update(key, value, target);
      } };
    };
    harness.state.selectedLabel = "Enter Python path…";
    harness.state.enteredPath = "/older/python";
    const older = harness.run("q1lens.selectPythonEnvironment");
    await new Promise<void>((done) => setImmediate(done));
    assert.equal(blocked, true);
    harness.state.enteredPath = "/newer/python";
    const newer = harness.run("q1lens.selectPythonEnvironment");
    await new Promise<void>((done) => setImmediate(done));
    release();
    await Promise.all([older, newer]);

    assert.equal(harness.values.q1lens.pythonPath, "/newer/python");
    assert.equal(harness.values.q1lens["q1timeline.pythonPath"], "/newer/python");
    assert.deepEqual(harness.values.q1lens.pythonArgs, []);
    assert.deepEqual(harness.values.q1lens["q1timeline.pythonArgs"], []);
  });

  for (const scope of ["folder", "workspace", "global", "inherited"] as const) {
    it(`keeps a newer ${scope} manual choice after an older status-panel Follow settings update finishes`, async () => {
      const subscriptions: vscode.Disposable[] = [];
      let receive!: (message: unknown) => Promise<void>;
      let disposePanel = () => {};
      Object.assign(harness.fakeVscode, { StatusBarAlignment: { Right: 2 }, ViewColumn: { Active: -1 } });
      Object.assign(harness.fakeVscode.window, {
        createStatusBarItem: () => ({ show() {}, dispose() {} }),
        createWebviewPanel: () => ({
          webview: { html: "", onDidReceiveMessage: (callback: typeof receive) => { receive = callback; return { dispose() {} }; } },
          onDidDispose: (callback: () => void) => { disposePanel = callback; return { dispose() {} }; },
          dispose: () => disposePanel(),
        }),
      });
      if (scope === "folder") harness.fakeVscode.workspace.getWorkspaceFolder = () => ({ name: "project", uri: { fsPath: "/project" } });
      if (scope === "global") harness.fakeVscode.workspace.workspaceFolders = [];
      const newerTarget = scope === "inherited" ? { fsPath: "/folder/sequence.q1asm" } as vscode.Uri : harness.source as vscode.Uri;
      if (scope === "inherited") harness.fakeVscode.workspace.getWorkspaceFolder = (resource: unknown) =>
        (resource as vscode.Uri).fsPath.startsWith("/folder/") ? { name: "folder", uri: { fsPath: "/folder" } } : undefined;
      let release!: () => void;
      const waiting = new Promise<void>((done) => { release = done; });
      const getConfiguration = harness.fakeVscode.workspace.getConfiguration;
      let blocked = false;
      let folderMode: unknown;
      harness.fakeVscode.workspace.getConfiguration = (section: string, resource?: vscode.Uri) => {
        const config = getConfiguration(section);
        return { ...config,
          get: (key: string, fallback?: unknown) => scope === "inherited" && key === "pythonEnvironment" && resource?.fsPath.startsWith("/folder/")
            ? folderMode ?? config.get(key, fallback) : config.get(key, fallback),
          update: async (key: string, value: unknown, target: number) => {
            if (key === "pythonEnvironment" && value === "vscode") { blocked = true; await waiting; }
            if (scope === "inherited" && key === "pythonEnvironment" && target === 3) folderMode = value;
            else await config.update(key, value, target);
          } };
      };
      try {
        registerPythonEnvironmentStatus({ subscriptions } as vscode.ExtensionContext);
        publishPythonEnvironment({ resource: harness.source as vscode.Uri, pythonPath: "old-python", pythonArgs: [], source: "manual", sourceLabel: "Manual" });
        await harness.run("q1lens.showPythonEnvironment");
        const older = receive({ type: "follow", target: harness.source.toString() });
        await new Promise<void>((done) => setImmediate(done));
        assert.equal(blocked, true);
        harness.state.selectedLabel = "Enter Python path…";
        harness.state.enteredPath = "/newer/python";
        const newer = harness.run("q1lens.selectPythonEnvironment", undefined, newerTarget);
        await new Promise<void>((done) => setImmediate(done));
        release();
        await Promise.all([older, newer]);
        assert.equal(scope === "inherited" ? folderMode : harness.values.q1lens.pythonEnvironment, "auto");
        assert.equal(harness.values.q1lens.pythonPath, "/newer/python");
        assert.equal(harness.values.q1lens["q1timeline.pythonPath"], "/newer/python");
        assert.equal(lastPythonEnvironment()?.source, "manual");
        assert.equal(lastPythonEnvironment()?.pythonPath, "/newer/python");
      } finally {
        release();
        for (const disposable of subscriptions) disposable.dispose();
      }
    });
  }

  it("keeps a newer manual choice after an older Follow VS Code settings update finishes", async () => {
    const getConfiguration = harness.fakeVscode.workspace.getConfiguration;
    let release!: () => void;
    let blocked = false;
    const waiting = new Promise<void>((resolve) => { release = resolve; });
    harness.fakeVscode.workspace.getConfiguration = (section: string) => {
      const config = getConfiguration(section);
      return { ...config, update: async (key: string, value: unknown, target: number) => {
        if (key === "pythonEnvironment" && value === "vscode") { blocked = true; await waiting; }
        await config.update(key, value, target);
      } };
    };
    (harness.fakeVscode.window as any).showTextDocument = async () => undefined;
    harness.state.selectedLabel = "Select Interpreter in VS Code…";
    harness.state.informationAction = "Follow VS Code Selection";
    const older = harness.run("q1lens.selectPythonEnvironment");
    await new Promise<void>((done) => setImmediate(done));
    assert.equal(blocked, true);
    harness.state.selectedLabel = "Enter Python path…";
    harness.state.enteredPath = "/newer/python";
    const newer = harness.run("q1lens.selectPythonEnvironment");
    await new Promise<void>((done) => setImmediate(done));
    release();
    await Promise.all([older, newer]);

    assert.equal(harness.values.q1lens.pythonPath, "/newer/python");
    assert.equal(harness.values.q1lens["q1timeline.pythonPath"], "/newer/python");
    assert.equal(harness.values.q1lens.pythonEnvironment, "auto");
    assert.equal(lastPythonEnvironment()?.pythonPath, "/newer/python");
    assert.equal(lastPythonEnvironment()?.source, "manual");
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

  for (const folderCount of [1, 2]) {
    it(`replaces folder-level Python settings in a saved workspace with ${folderCount} folders`, async () => {
      const folderValues: Record<string, unknown> = {
        pythonPath: "old-folder-python", pythonArgs: ["-X", "dev"],
        "q1timeline.pythonPath": "old-folder-python", "q1timeline.pythonArgs": ["-X", "dev"],
      };
      const workspace = harness.fakeVscode.workspace;
      const folder = workspace.workspaceFolders[0];
      workspace.workspaceFolders = Array.from({ length: folderCount }, () => folder);
      workspace.getWorkspaceFolder = () => folder;
      const originalConfiguration = workspace.getConfiguration;
      workspace.getConfiguration = (section: string) => section !== "q1lens" ? originalConfiguration(section) : {
        get: (key: string, fallback?: unknown) => folderValues[key] ?? harness.values.q1lens[key] ?? fallback,
        inspect: (key: string) => ({ workspaceValue: harness.values.q1lens[key], workspaceFolderValue: folderValues[key] }),
        update: async (key: string, value: unknown, target: number) => {
          (target === 3 ? folderValues : harness.values.q1lens)[key] = value;
          harness.updates.push({ key, value, target });
        },
      };
      harness.state.selectedLabel = "Enter Python path…";
      harness.state.enteredPath = "selected-folder-python";

      await harness.run("q1lens.selectPythonEnvironment");

      assert.equal(folderValues.pythonPath, "selected-folder-python");
      assert.equal(folderValues["q1timeline.pythonPath"], "selected-folder-python");
      assert.deepEqual(folderValues.pythonArgs, []);
      assert.deepEqual(folderValues["q1timeline.pythonArgs"], []);
      assert.ok(harness.updates.every((update) => update.target === 3));
      assert.ok(harness.probes.every((probe) => probe.executable === "selected-folder-python"));
    });
  }

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

  it("keeps the checked schedule and retry project separate after a delayed notification", async () => {
    harness.values.q1lens = {};
    const schedule = { fsPath: "/schedules/source.py" } as vscode.Uri;
    const project = { fsPath: "/project/qbstimeline.yml" } as vscode.Uri;
    let reply!: (action: string) => void;
    harness.state.informationReply = new Promise((resolve) => { reply = resolve; });
    await harness.run("q1lens.checkEnvironment", "qbsTimeline.analyzeAndOpen", schedule, project);
    harness.fakeVscode.window.activeTextEditor.document.uri = { fsPath: "/other/qbstimeline.yml" };
    reply("Retry Timeline");
    await new Promise<void>((done) => setImmediate(done));
    assert.deepEqual(harness.resources, [schedule, schedule, schedule, schedule, schedule, schedule]);
    assert.deepEqual(harness.executions, [{ command: "qbsTimeline.analyzeAndOpen", source: project }]);
  });

  it("preserves the retry project through environment selection", async () => {
    const schedule = { fsPath: "/schedules/source.py" } as vscode.Uri;
    const project = { fsPath: "/project/qbstimeline.yml" } as vscode.Uri;
    harness.state.selectedLabel = "Current QBS Python";
    harness.state.informationAction = "Retry Timeline";
    await harness.run("q1lens.selectPythonEnvironment", "qbsTimeline.analyzeAndOpen", schedule, project);
    await new Promise<void>((done) => setImmediate(done));
    assert.deepEqual(harness.executions, [{ command: "qbsTimeline.analyzeAndOpen", source: project }]);
  });

  it("checks the notebook resource while its embedded cell editor is active", async () => {
    const notebook = { fsPath: "/project/source.ipynb" };
    harness.fakeVscode.window.activeNotebookEditor = { notebook: { uri: notebook } };
    harness.fakeVscode.window.activeTextEditor.document.uri = { fsPath: notebook.fsPath, scheme: "vscode-notebook-cell" } as any;
    await harness.run("q1lens.checkEnvironment");
    assert.deepEqual(harness.notebooks, [notebook]);
    assert.equal(lastPythonEnvironment()?.resource, notebook);
  });
});
