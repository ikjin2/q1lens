import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import Module = require("node:module");
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PassThrough } from "node:stream";
import { buildTaskDefinitions, buildTaskExecution, resolveTaskDefinition } from "../src/qbs/taskProvider";

describe("taskProvider", () => {
  it("builds analyze, render, and combined tasks for each project", () => {
    const tasks = buildTaskDefinitions(["examples/basic-transmon/qbstimeline.yml"]);

    assert.deepEqual(tasks.map((task) => task.label), [
      "Q1Lens: Analyze examples/basic-transmon",
      "Q1Lens: Render examples/basic-transmon",
      "Q1Lens: Analyze and Render examples/basic-transmon",
    ]);
  });

  it("passes required Q1Lens CLI arguments for analyze and render tasks", () => {
    const tasks = buildTaskDefinitions(["examples/basic-transmon/qbstimeline.yml"]);
    const settings = { pythonPath: "python", pythonArgs: [] };

    assert.deepEqual(buildTaskExecution(tasks[0], settings), {
      command: "python",
      args: [
        "-m",
        "q1lens",
        "analyze",
        "--project",
        "examples/basic-transmon/qbstimeline.yml",
        "--out",
        "examples/basic-transmon/.qbs_timeline/qbs_ir.json",
      ],
    });

    assert.deepEqual(buildTaskExecution(tasks[1], settings), {
      command: "python",
      args: [
        "-m",
        "q1lens",
        "render",
        "--ir",
        "examples/basic-transmon/.qbs_timeline/qbs_ir.json",
        "--out",
        "examples/basic-transmon/.qbs_timeline/index.html",
      ],
    });
  });

  it("runs analyze before render for combined tasks", () => {
    const task = buildTaskDefinitions(["examples/basic-transmon/qbstimeline.yml"])[2];
    const execution = buildTaskExecution(task, { pythonPath: "python", pythonArgs: ["-X", "utf8"] });

    assert.equal(execution.command, "python");
    assert.equal(execution.args[0], "-X");
    assert.equal(execution.args[1], "utf8");
    assert.equal(execution.args[2], "-c");
    assert.match(execution.args[3], /"analyze"/);
    assert.match(execution.args[3], /"render"/);
    assert.match(execution.args[3], /examples\/basic-transmon\/\.qbs_timeline\/qbs_ir\.json/);
  });

  it("resolves user-authored task definitions into runnable artifact paths", () => {
    const definition = resolveTaskDefinition({
      type: "qbs-timeline",
      command: "render",
      project: "examples/two-qubit-entangling/qbstimeline.yml",
    });

    assert.deepEqual(definition, {
      type: "qbs-timeline",
      command: "render",
      project: "examples/two-qubit-entangling/qbstimeline.yml",
      label: "Q1Lens: Render examples/two-qubit-entangling",
      outputDir: ".qbs_timeline",
      irPath: "examples/two-qubit-entangling/.qbs_timeline/qbs_ir.json",
      htmlPath: "examples/two-qubit-entangling/.qbs_timeline/index.html",
    });
  });

  it("applies the configured output directory override when resolving tasks", () => {
    const definition = resolveTaskDefinition(
      {
        type: "qbs-timeline",
        command: "analyze",
        project: "examples/basic-transmon/qbstimeline.yml",
      },
      "build/qbs",
    );

    assert.equal(definition?.irPath, "examples/basic-transmon/build/qbs/qbs_ir.json");
    assert.equal(definition?.htmlPath, "examples/basic-transmon/build/qbs/index.html");
  });

  it("resolves the original project's current Python each time a discovered task runs", async () => {
    await withProviderMocks(async (fixture) => {
      const tasks = await fixture.provider.provideTasks();
      assert.equal(tasks.length, 6);
      assert.deepEqual(fixture.lookups, [], "Listing tasks must not probe Python or open setup");
      assert.equal(fixture.settingsResources.includes(fixture.projectA.fsPath), true);
      assert.equal(fixture.settingsResources.includes(fixture.projectB.fsPath), true);
      const first = await fixture.start(tasks[0]);
      assert.equal(fixture.processes[0].command, "selected-python-a");
      assert.equal(fixture.processes[0].options.cwd, join(fixture.folderA.uri.fsPath, "experiment"));
      assert.equal(fixture.processes[0].args.includes(fixture.projectA.fsPath), true);
      assert.equal(fixture.processes[0].args.includes(join(fixture.folderA.uri.fsPath, "experiment", ".qbs_timeline", "qbs_ir.json")), true);
      fixture.processes[0].child.stdout.write("analysis complete\n");
      fixture.processes[0].child.emit("close", 0);
      assert.deepEqual(first.exits, [0]);
      assert.match(first.output.join(""), /Source: VS Code selected interpreter/);
      assert.match(first.output.join(""), /analysis complete\r\n/);

      fixture.select({ pythonPath: "selected-python-b", pythonArgs: [] });
      const second = await fixture.start(tasks[0]);
      assert.equal(fixture.processes[1].command, "selected-python-b");
      assert.deepEqual(fixture.lookups, [fixture.projectA.fsPath, fixture.projectA.fsPath]);
      fixture.processes[1].child.emit("close", 2);
      assert.deepEqual(second.exits, [2]);
    });
  });

  it("resolves user-authored tasks in their workspace and preserves manual launch arguments", async () => {
    await withProviderMocks(async (fixture) => {
      const definition = { type: "qbs-timeline", command: "analyze", project: "qbstimeline.yml" };
      const task = fixture.provider.resolveTask({ definition, scope: fixture.folderB, name: "My task", detail: "saved detail" });
      assert.equal(task.definition, definition);
      assert.equal(task.detail, "saved detail");
      fixture.select({ pythonPath: "manual-launcher", pythonArgs: ["-3.12"], sourceLabel: "Manual setting: q1lens.pythonPath" });
      const running = await fixture.start(task);
      assert.deepEqual(fixture.lookups, [fixture.projectB.fsPath]);
      assert.equal(fixture.processes[0].command, "manual-launcher");
      assert.deepEqual(fixture.processes[0].args.slice(0, 4), ["-3.12", "-m", "q1lens", "analyze"]);
      assert.match(running.output.join(""), /Manual setting: q1lens.pythonPath/);
      running.terminal.close();
      assert.equal(fixture.processes[0].child.killed, true);
    });
  });

  it("fails only the invoked task when its environment cannot resolve, without a Python fallback", async () => {
    await withProviderMocks(async (fixture) => {
      const tasks = await fixture.provider.provideTasks();
      fixture.fail(new Error("Use Python: Select Interpreter for this file's project."));
      const running = await fixture.start(tasks[3]);
      assert.deepEqual(running.exits, [1]);
      assert.match(running.output.join(""), /Python: Select Interpreter/);
      assert.deepEqual(fixture.processes, []);
      assert.equal((await fixture.provider.provideTasks()).length, 6);
    });
  });

  it("does not launch Python if the task is cancelled while environment selection is pending", async () => {
    await withProviderMocks(async (fixture) => {
      let release!: () => void;
      fixture.wait(new Promise<void>((resolve) => { release = resolve; }));
      const tasks = await fixture.provider.provideTasks();
      const running = await fixture.start(tasks[0]);
      running.terminal.close();
      release();
      await new Promise(setImmediate);
      assert.deepEqual(fixture.processes, []);
    });
  });
});

async function withProviderMocks(run: (fixture: any) => Promise<void>): Promise<void> {
  const originalLoad = (Module as any)._load;
  const uri = (fsPath: string) => ({ fsPath });
  const folderA = { uri: uri(join(tmpdir(), "q1lens-task-a")), name: "A", index: 0 };
  const folderB = { uri: uri(join(tmpdir(), "q1lens-task-b")), name: "B", index: 1 };
  const projectA = uri(join(folderA.uri.fsPath, "experiment", "qbstimeline.yml"));
  const projectB = uri(join(folderB.uri.fsPath, "qbstimeline.yml"));
  const lookups: string[] = [];
  const settingsResources: string[] = [];
  const processes: any[] = [];
  let selected = { pythonPath: "selected-python-a", pythonArgs: [], sourceLabel: "VS Code selected interpreter" };
  let failure: Error | undefined;
  let waiting = Promise.resolve();
  class MockEventEmitter<T> {
    private emitter = new EventEmitter();
    event = (listener: (value: T) => void) => { this.emitter.on("value", listener); return { dispose: () => {} }; };
    fire(value: T) { this.emitter.emit("value", value); }
    dispose() { this.emitter.removeAllListeners(); }
  }
  const vscode = {
    Uri: { file: uri, joinPath: (base: any, path: string) => uri(join(base.fsPath, path)) },
    TaskScope: { Workspace: 2 },
    EventEmitter: MockEventEmitter,
    CustomExecution: class { constructor(public callback: any) {} },
    Task: class { constructor(public definition: any, public scope: any, public name: any, public source: any, public execution: any) {} },
    workspace: {
      workspaceFolders: [folderA, folderB],
      getWorkspaceFolder: (resource: any) => [folderA, folderB].find((folder) => resource.fsPath.startsWith(folder.uri.fsPath)),
    },
  };
  (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
    if (request === "vscode") return vscode;
    if (request === "./settings") return { readSettings: (resource: any) => {
      if (resource) settingsResources.push(resource.fsPath);
      return { outputDirOverride: null };
    } };
    if (request === "../pythonEnvironment") return { resolveFilePythonEnvironment: async (_vscode: any, resource: any) => {
      lookups.push(resource.fsPath);
      await waiting;
      if (failure) throw failure;
      return { ...selected, resource };
    } };
    if (request === "node:child_process") return { spawn: (command: string, args: string[], options: any) => {
      const child = Object.assign(new EventEmitter(), {
        stdout: new PassThrough(), stderr: new PassThrough(), killed: false,
        kill() { this.killed = true; },
      });
      processes.push({ command, args, options, child });
      return child;
    } };
    return originalLoad.call(this, request, parent, isMain);
  };
  const modulePath = require.resolve("../src/qbs/taskProvider");
  delete require.cache[modulePath];
  try {
    const { QbsTimelineTaskProvider } = require("../src/qbs/taskProvider");
    const provider = new QbsTimelineTaskProvider(async () => [
      { project: "experiment/qbstimeline.yml", resource: projectA, outputDir: ".qbs_timeline" },
      { project: "qbstimeline.yml", resource: projectB, outputDir: ".qbs_timeline" },
    ]);
    await run({
      provider, folderA, folderB, projectA, projectB, lookups, settingsResources, processes,
      select: (value: any) => { selected = { ...selected, ...value }; },
      fail: (error: Error) => { failure = error; },
      wait: (promise: Promise<void>) => { waiting = promise; },
      start: async (task: any) => {
        const terminal = await task.execution.callback(task.definition);
        const output: string[] = [];
        const exits: number[] = [];
        terminal.onDidWrite((text: string) => output.push(text));
        terminal.onDidClose((code: number) => exits.push(code));
        terminal.open();
        await new Promise(setImmediate);
        return { terminal, output, exits };
      },
    });
  } finally {
    delete require.cache[modulePath];
    (Module as any)._load = originalLoad;
  }
}
