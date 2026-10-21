import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { hostname, tmpdir } from "node:os";
import { dirname, join } from "node:path";
import type * as vscode from "vscode";
import type { PythonEnvironmentReport } from "../src/environment";
import { beginPythonEnvironmentCheck, isCurrentPythonEnvironmentCheck, lastPythonEnvironment, publishPythonEnvironment, type PythonEnvironmentCheck } from "../src/pythonEnvironmentStatus";

const Module = require("node:module");

class CancellationTokenSource {
  private listeners = new Set<() => void>();
  token = {
    isCancellationRequested: false,
    onCancellationRequested: (listener: () => void) => {
      this.listeners.add(listener);
      return { dispose: () => this.listeners.delete(listener) };
    },
  };
  cancel() {
    this.token.isCancellationRequested = true;
    for (const listener of this.listeners) listener();
  }
  dispose() { this.listeners.clear(); }
}

async function waitFor(check: () => boolean): Promise<void> {
  const deadline = Date.now() + 2000;
  while (!check()) {
    assert.ok(Date.now() < deadline, "Timed out waiting for the notebook setup panel");
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

function harness() {
  const notebookUri = { fsPath: join(tmpdir(), "first.ipynb"), toString: () => "file:///first.ipynb" };
  const otherUri = { fsPath: join(tmpdir(), "second.ipynb"), toString: () => "file:///second.ipynb" };
  const executable = join(tmpdir(), "notebook env", "python");
  const probes: Array<{ pythonPath: string; options: { cwd: string; env: Record<string, string> } }> = [];
  const executions: unknown[][] = [];
  const messages: Array<{ message: string; actions: string[] }> = [];
  const kernelUris: unknown[] = [];
  const lines: string[] = [];
  const copied: string[] = [];
  const terminals: Array<{ options: vscode.TerminalOptions; sent: Array<{ command: string; addNewLine?: boolean }>; shown: boolean }> = [];
  const ready: PythonEnvironmentReport = { executable, version: "3.12.1", supported: true, packagePath: "q1lens/__init__.py", q1asmPath: "q1timeline/cli.py" };
  const missing: PythonEnvironmentReport = { executable, version: "3.12.1", supported: true, qbsError: "No module named 'q1lens'", q1lensInstalled: false, pipAvailable: true };
  const state = {
    trusted: true, jupyter: true, started: true, language: "python", remote: false,
    pythonPath: executable, action: undefined as string | undefined,
    report: ready, cancelProbe: false, kernelCancelled: false, kernelIdentity: 0,
    openedNotebook: undefined as unknown, onInformation: undefined as (() => void) | undefined,
    probeGate: undefined as Promise<void> | undefined,
    kernelGate: undefined as Promise<void> | undefined, kernelStarted: false,
    openNotebookGate: undefined as Promise<void> | undefined, notebookOpening: false,
    errorAction: undefined as string | undefined,
  };
  const kernel = {
    get language() { return state.language; },
    async *executeCode(code: string, token: CancellationTokenSource["token"]) {
      state.kernelStarted = true;
      token.onCancellationRequested(() => { state.kernelCancelled = true; });
      await state.kernelGate;
      if (state.cancelProbe) {
        await new Promise<void>(() => {});
        return;
      }
      const source = JSON.parse(code.slice(5, -5)) as string;
      const proofPath = JSON.parse(source.match(/pathlib\.Path\((.+)\)\.read_text/)![1]) as string;
      const marker = JSON.parse(source.match(/print\((.+) \+ json\.dumps/)![1]) as string;
      const report = JSON.stringify({ executable: state.pythonPath, hostname: hostname(), proof: state.remote ? "" : readFileSync(proofPath, "utf8") });
      for (const data of ["other kernel output\n", marker.slice(0, 8), marker.slice(8) + report + "\n"]) {
        yield { items: [{ mime: "application/vnd.code.notebook.stdout", data: Buffer.from(data) }] };
      }
    },
  };
  const otherKernel = { get language() { return state.language; }, executeCode: kernel.executeCode };
  let receiveMessage: ((message: unknown) => unknown) | undefined;
  let disposeListener: (() => unknown) | undefined;
  const panel = {
    created: false, disposed: false, options: undefined as vscode.WebviewPanelOptions & vscode.WebviewOptions | undefined,
    webview: {
      html: "", cspSource: "vscode-webview://test", messages: [] as unknown[],
      onDidReceiveMessage: (listener: (message: unknown) => unknown) => {
        receiveMessage = listener;
        return { dispose() {} };
      },
      postMessage: async (message: unknown) => { panel.webview.messages.push(message); return true; },
    },
    onDidDispose: (listener: () => unknown) => { disposeListener = listener; return { dispose() {} }; },
    dispose: () => {
      if (!panel.disposed) { panel.disposed = true; disposeListener?.(); }
    },
    reveal() {},
  };
  const fakeVscode = {
    CancellationTokenSource, CancellationError: class extends Error {}, ProgressLocation: { Notification: 15 },
    ViewColumn: { Active: -1, Beside: -2, One: 1 },
    env: { clipboard: { writeText: async (text: string) => { copied.push(text); } } },
    workspace: { get isTrusted() { return state.trusted; }, openNotebookDocument: async (uri: unknown) => {
      state.notebookOpening = true;
      await state.openNotebookGate;
      return uri;
    } },
    extensions: {
      getExtension: (id: string) => id === "q1lens.q1lens" ? { packageJSON: { version: "0.2.2" } }
        : state.jupyter ? { activate: async () => ({ kernels: { getKernel: async (uri: unknown) => {
          kernelUris.push(uri);
          return state.started ? state.kernelIdentity === 0 ? kernel : otherKernel : undefined;
        } } }) } : undefined,
    },
    window: {
      activeNotebookEditor: { notebook: { uri: otherUri } },
      showInformationMessage: async (message: string, ...actions: string[]) => {
        messages.push({ message, actions });
        state.onInformation?.();
        return state.action;
      },
      showErrorMessage: async (message: string, ...actions: string[]) => {
        messages.push({ message, actions });
        return state.errorAction;
      },
      showNotebookDocument: async (uri: unknown) => { state.openedNotebook = uri; },
      createWebviewPanel: (_viewType: string, _title: string, _column: unknown, options: vscode.WebviewPanelOptions & vscode.WebviewOptions) => {
        panel.created = true;
        panel.options = options;
        return panel;
      },
      createTerminal: (options: vscode.TerminalOptions) => {
        const terminal = { options, sent: [] as Array<{ command: string; addNewLine?: boolean }>, shown: false };
        terminals.push(terminal);
        return {
          sendText: (command: string, addNewLine?: boolean) => terminal.sent.push({ command, addNewLine }),
          show: () => { terminal.shown = true; },
        };
      },
      withProgress: async (_options: unknown, callback: (_progress: unknown, token: CancellationTokenSource["token"]) => Promise<unknown>) => {
        const cancellation = new CancellationTokenSource();
        if (state.cancelProbe) setImmediate(() => cancellation.cancel());
        return callback({}, cancellation.token);
      },
    },
    commands: { executeCommand: async () => undefined },
  };
  const originalLoad = Module._load;
  Module._load = function (request: string, parent: unknown, isMain: boolean) {
    if (request === "./environment") return {
      probePythonEnvironment: async (pythonPath: string, _args: string[], options: { cwd: string; env: Record<string, string> }) => {
        probes.push({ pythonPath, options });
        const report = state.report;
        await state.probeGate;
        return report;
      },
    };
    if (request === "node:child_process") return {
      execFile: (...args: unknown[]) => { executions.push(args); throw new Error("Onboarding must never run an installer"); },
      spawn: (...args: unknown[]) => { executions.push(args); throw new Error("Onboarding must never run an installer"); },
    };
    return originalLoad.call(this, request, parent, isMain);
  };
  const modulePath = require.resolve("../src/notebookEnvironment");
  delete require.cache[modulePath];
  const { ensureNotebookEnvironment } = require("../src/notebookEnvironment") as typeof import("../src/notebookEnvironment");
  const run = (owner?: PythonEnvironmentCheck, signal?: AbortSignal) => ensureNotebookEnvironment(fakeVscode as unknown as typeof vscode, notebookUri as vscode.Uri, {
    append: (line: string) => lines.push(line), appendLine: (line: string) => lines.push(line), show: () => undefined,
  } as unknown as vscode.OutputChannel, owner, signal);
  return {
    state, messages, probes, executions, kernelUris, notebookUri, executable, lines, ready, missing,
    panel, copied, terminals, run,
    openSetup: async (owner?: PythonEnvironmentCheck, signal?: AbortSignal) => {
      state.report = missing;
      state.action = "Show Setup";
      const pending = run(owner, signal);
      await waitFor(() => panel.created && !!receiveMessage);
      return { pending };
    },
    send: async (message: unknown) => {
      assert.ok(receiveMessage, "Setup message handler should be registered");
      await receiveMessage(message);
    },
    restore: () => { panel.dispose(); Module._load = originalLoad; delete require.cache[modulePath]; },
  };
}

describe("notebook Python environment", () => {
  let test: ReturnType<typeof harness>;
  beforeEach(() => { test = harness(); });
  afterEach(() => {
    test.restore();
    assert.equal(test.executions.length, 0, "The extension must never install packages or bootstrap pip");
  });

  it("cancels an obsolete notebook kernel check without waiting for kernel output", async () => {
    const cancellation = new AbortController();
    let release!: () => void;
    test.state.kernelGate = new Promise<void>((resolve) => { release = resolve; });
    const pending = test.run(undefined, cancellation.signal);
    try {
      await waitFor(() => test.state.kernelStarted);
      cancellation.abort();
      const result = await Promise.race([pending.then((value) => ({ value })), new Promise<undefined>((resolve) => setTimeout(() => resolve(undefined), 1000))]);
      assert.deepEqual(result, { value: undefined }, "An obsolete open must stop even if the kernel ignores cancellation");
      assert.equal(test.state.kernelCancelled, true);
      assert.equal(test.probes.length, 0);
      assert.equal(test.messages.length, 0);
    } finally { release(); await pending; }
  });

  it("suppresses a late environment failure after its notebook open is cancelled", async () => {
    const cancellation = new AbortController();
    let reject!: (error: Error) => void;
    test.state.probeGate = new Promise<void>((_resolve, fail) => { reject = fail; });
    const pending = test.run(undefined, cancellation.signal);
    await waitFor(() => test.probes.length === 1);
    cancellation.abort();
    reject(new Error("The previous notebook probe failed late"));
    assert.equal(await pending, undefined);
    assert.equal(test.messages.length, 0, "A superseded notebook must not show an error over the current file");
    assert.equal(test.panel.created, false);
  });

  it("closes the obsolete notebook setup when its open is cancelled", async () => {
    const cancellation = new AbortController();
    const { pending } = await test.openSetup(undefined, cancellation.signal);
    try {
      cancellation.abort();
      assert.equal(test.panel.disposed, true);
      assert.equal(await pending, undefined);
    } finally { test.panel.dispose(); await pending; }
  });

  it("quietly cancels an obsolete error recovery while its notebook document opens", async () => {
    const cancellation = new AbortController();
    let release!: () => void;
    test.state.started = false;
    test.state.errorAction = "Open Notebook";
    test.state.openNotebookGate = new Promise<void>((resolve) => { release = resolve; });
    const pending = test.run(undefined, cancellation.signal);
    await waitFor(() => test.state.notebookOpening);
    cancellation.abort();
    release();
    assert.equal(await pending, undefined);
    assert.equal(test.state.openedNotebook, undefined, "Cancelled error recovery must not focus the previous notebook");
  });

  it("uses the original notebook's live kernel, including after its environment changes", async () => {
    assert.deepEqual(await test.run(), { pythonPath: test.executable, pythonArgs: [] });
    test.state.pythonPath = join(tmpdir(), "new env", "python");
    assert.deepEqual(await test.run(), { pythonPath: test.state.pythonPath, pythonArgs: [] });
    assert.deepEqual(test.kernelUris, [test.notebookUri, test.notebookUri, test.notebookUri, test.notebookUri]);
    assert.equal(test.probes[0].options.cwd, tmpdir());
    assert.equal(test.probes[0].options.env.PYTHONPATH, "");
    assert.equal(test.messages.length, 0);
  });

  for (const change of ["switched", "stopped"]) {
    it(`rejects a kernel ${change} while its package probe waits and recovers on a new check`, async () => {
      let release!: () => void;
      test.state.probeGate = new Promise<void>((resolve) => { release = resolve; });
      const previous = lastPythonEnvironment();
      const pending = test.run();
      await waitFor(() => test.probes.length === 1);
      test.state.kernelIdentity = 1;
      test.state.started = change !== "stopped";
      test.state.pythonPath = join(tmpdir(), "latest kernel", "python");
      test.state.report = { ...test.ready, executable: test.state.pythonPath };
      release();
      assert.equal(await pending, undefined, "The old kernel's successful probe must not authorize the new selection");
      assert.equal(lastPythonEnvironment(), previous, "An obsolete kernel must not be published as the current environment");
      assert.match(test.messages.at(-1)?.message ?? "", /kernel.*changed|kernel.*switch/i);
      test.state.started = true;
      test.state.probeGate = undefined;
      assert.deepEqual(await test.run(), { pythonPath: test.state.pythonPath, pythonArgs: [] });
    });
  }

  it("keeps Setup open when its ready probe belongs to a kernel replaced during Check Again", async () => {
    const { pending } = await test.openSetup();
    test.state.report = test.ready;
    let release!: () => void;
    test.state.probeGate = new Promise<void>((resolve) => { release = resolve; });
    const checking = test.send({ type: "checkAgain" });
    await waitFor(() => test.probes.length === 2);
    test.state.kernelIdentity = 1;
    test.state.pythonPath = join(tmpdir(), "latest setup kernel", "python");
    test.state.report = { ...test.ready, executable: test.state.pythonPath };
    release();
    await checking;
    assert.equal(test.panel.disposed, false, "The old kernel must not complete setup for a new selection");
    assert.ok(test.panel.webview.messages.some((message: any) => message.type === "checkError" && /kernel.*changed|kernel.*switch/i.test(message.message)));
    test.state.probeGate = undefined;
    await test.send({ type: "checkAgain" });
    assert.deepEqual(await pending, { pythonPath: test.state.pythonPath, pythonArgs: [] });
  });

  it("keeps a newer analysis target when a notebook environment probe finishes late", async () => {
    let release!: () => void;
    test.state.probeGate = new Promise<void>((resolve) => { release = resolve; });
    const pending = test.run();
    await waitFor(() => test.probes.length === 1);
    const newer = { resource: { fsPath: join(tmpdir(), "newer.q1asm") } as vscode.Uri,
      pythonPath: "/newer/python", pythonArgs: [], source: "vscode" as const, sourceLabel: "VS Code selected interpreter" };
    publishPythonEnvironment(newer);
    release();
    assert.deepEqual(await pending, { pythonPath: test.executable, pythonArgs: [] });
    assert.equal(lastPythonEnvironment(), newer);
  });

  it("does not renew a stale parent command when its initial notebook check starts late", async () => {
    const owner = beginPythonEnvironmentCheck();
    const newer = { resource: { fsPath: join(tmpdir(), "newer.q1asm") } as vscode.Uri,
      pythonPath: "/newer/python", pythonArgs: [], source: "vscode" as const, sourceLabel: "VS Code selected interpreter" };
    publishPythonEnvironment(newer);
    assert.deepEqual(await test.run(owner), { pythonPath: test.executable, pythonArgs: [] });
    assert.equal(lastPythonEnvironment(), newer);
    assert.equal(isCurrentPythonEnvironmentCheck(owner), false);
  });

  it("renews the waiting notebook command when setup explicitly checks again after another target", async () => {
    const owner = beginPythonEnvironmentCheck();
    const { pending } = await test.openSetup(owner);
    publishPythonEnvironment({ resource: { fsPath: join(tmpdir(), "newer.q1asm") } as vscode.Uri,
      pythonPath: "/newer/python", pythonArgs: [], source: "vscode", sourceLabel: "VS Code selected interpreter" });
    assert.equal(isCurrentPythonEnvironmentCheck(owner), false);
    test.state.report = test.ready;
    await test.send({ type: "checkAgain" });
    assert.deepEqual(await pending, { pythonPath: test.executable, pythonArgs: [] });
    assert.equal(isCurrentPythonEnvironmentCheck(owner), true);
    assert.equal(lastPythonEnvironment()?.resource, test.notebookUri);
    assert.equal(lastPythonEnvironment()?.report, test.ready);
  });

  it("offers setup and rechecking without installing or opening a terminal", async () => {
    test.state.report = test.missing;
    assert.equal(await test.run(), undefined);
    assert.deepEqual(test.messages[0].actions, ["Show Setup", "Check Again", "Open Notebook"]);
    assert.match(test.messages[0].message, /first\.ipynb/);
    assert.equal(test.terminals.length, 0);
    assert.equal(test.panel.created, false);
  });

  it("shows diagnostics and both installer choices before taking any action", async () => {
    const { pending } = await test.openSetup();
    assert.equal(test.panel.options?.enableScripts, true);
    assert.match(test.panel.webview.html, /3\.12\.1/);
    assert.match(test.panel.webview.html, /first\.ipynb/);
    assert.ok(test.panel.webview.html.includes(test.executable));
    assert.match(test.panel.webview.html, /pip/);
    assert.match(test.panel.webview.html, /uv pip/);
    assert.match(test.panel.webview.html, /Copy Command/i);
    assert.match(test.panel.webview.html, /Insert into Terminal/i);
    assert.match(test.panel.webview.html, /Check Again/i);
    assert.equal(test.copied.length, 0);
    assert.equal(test.terminals.length, 0);
    test.panel.dispose();
    assert.equal(await pending, undefined);
  });

  it("copies pip and uv commands for the original notebook environment", async () => {
    const { pending } = await test.openSetup();
    await test.send({ type: "copyCommand", installer: "pip" });
    await test.send({ type: "copyCommand", installer: "uv" });
    assert.equal(test.copied.length, 2);
    assert.ok(test.copied.every((command) => command.includes(test.executable)));
    assert.match(test.copied[0], /-m pip install/);
    assert.match(test.copied[1], /uv pip install --python/);
    assert.ok(test.copied.every((command) => command.includes("q1lens==0.2.2")));
    assert.equal(test.terminals.length, 0);
    assert.ok(test.kernelUris.every((uri) => uri === test.notebookUri));
    assert.equal(test.probes.length, 3);
    test.panel.dispose();
    assert.equal(await pending, undefined);
  });

  it("inserts commands into a dedicated compatible shell without pressing Enter", async () => {
    const { pending } = await test.openSetup();
    await test.send({ type: "insertCommand", installer: "pip" });
    await test.send({ type: "insertCommand", installer: "uv" });
    assert.equal(test.terminals.length, 2);
    for (const terminal of test.terminals) {
      assert.equal(terminal.sent.length, 1);
      assert.equal(terminal.sent[0].addNewLine, false);
      assert.equal(terminal.shown, true);
      assert.equal(terminal.options.cwd, dirname(test.notebookUri.fsPath));
      if (process.platform === "win32") assert.match(String(terminal.options.shellPath), /powershell\.exe$/i);
      else assert.equal(terminal.options.shellPath, "/bin/sh");
      assert.ok(terminal.sent[0].command.includes(test.executable));
    }
    assert.match(test.terminals[0].sent[0].command, /-m pip install/);
    assert.match(test.terminals[1].sent[0].command, /uv pip install --python/);
    test.panel.dispose();
    assert.equal(await pending, undefined);
  });

  it("continues only after Check Again sees the externally installed package", async () => {
    const { pending } = await test.openSetup();
    test.state.report = test.ready;
    await test.send({ type: "checkAgain" });
    assert.deepEqual(await pending, { pythonPath: test.executable, pythonArgs: [] });
    assert.equal(test.panel.disposed, true);
    assert.deepEqual(test.kernelUris, [test.notebookUri, test.notebookUri, test.notebookUri, test.notebookUri]);
  });

  it("does not copy or insert after the setup panel closes during a check", async () => {
    const { pending } = await test.openSetup();
    let release: () => void = () => {};
    test.state.probeGate = new Promise<void>((resolve) => { release = resolve; });
    const action = test.send({ type: "insertCommand", installer: "pip" });
    await waitFor(() => test.probes.length === 2);
    test.panel.dispose();
    assert.equal(await pending, undefined);
    release();
    await action;
    assert.equal(test.copied.length, 0);
    assert.equal(test.terminals.length, 0);
  });

  it("ignores a second action while the first environment check is pending", async () => {
    const { pending } = await test.openSetup();
    let release: () => void = () => {};
    test.state.probeGate = new Promise<void>((resolve) => { release = resolve; });
    const action = test.send({ type: "insertCommand", installer: "uv" });
    await waitFor(() => test.probes.length === 2);
    await test.send({ type: "insertCommand", installer: "uv" });
    assert.equal(test.probes.length, 2);
    release();
    await action;
    assert.equal(test.terminals.length, 1);
    test.panel.dispose();
    assert.equal(await pending, undefined);
  });

  it("keeps setup available when Check Again still reports a missing package", async () => {
    const { pending } = await test.openSetup();
    await test.send({ type: "checkAgain" });
    assert.equal(test.panel.disposed, false);
    assert.equal(test.probes.length, 2);
    assert.equal(test.terminals.length, 0);
    test.state.report = test.ready;
    await test.send({ type: "checkAgain" });
    assert.deepEqual(await pending, { pythonPath: test.executable, pythonArgs: [] });
  });

  it("rechecks the environment from the initial notification", async () => {
    test.state.report = test.missing;
    test.state.action = "Check Again";
    test.state.onInformation = () => { test.state.report = test.ready; };
    assert.deepEqual(await test.run(), { pythonPath: test.executable, pythonArgs: [] });
    assert.equal(test.probes.length, 2);
    assert.equal(test.panel.created, false);
  });

  for (const type of ["copyCommand", "insertCommand"]) {
    it(`refreshes a changed kernel before ${type} can target its old Python`, async () => {
      const { pending } = await test.openSetup();
      const currentPython = join(tmpdir(), "switched env", "python");
      test.state.pythonPath = currentPython;
      test.state.report = { ...test.missing, executable: currentPython };
      await test.send({ type, installer: "uv" });
      assert.equal(test.copied.length, 0);
      assert.equal(test.terminals.length, 0);
      assert.equal(test.panel.disposed, false);
      assert.ok(test.probes.some((probe) => probe.pythonPath === currentPython));
      await test.send({ type, installer: "uv" });
      const commands = [...test.copied, ...test.terminals.flatMap((terminal) => terminal.sent.map((sent) => sent.command))];
      assert.equal(commands.length, 1);
      assert.ok(commands[0].includes(currentPython));
      assert.ok(!commands[0].includes(test.executable));
      test.panel.dispose();
      assert.equal(await pending, undefined);
    });
  }

  it("Check Again follows a newly selected ready kernel", async () => {
    const { pending } = await test.openSetup();
    test.state.pythonPath = join(tmpdir(), "ready env", "python");
    test.state.report = { ...test.ready, executable: test.state.pythonPath };
    await test.send({ type: "checkAgain" });
    assert.deepEqual(await pending, { pythonPath: test.state.pythonPath, pythonArgs: [] });
  });

  it("does not bootstrap missing pip and still allows the uv command", async () => {
    const { pending } = await test.openSetup();
    test.state.report = { ...test.missing, pipAvailable: false };
    await test.send({ type: "checkAgain" });
    await test.send({ type: "copyCommand", installer: "pip" });
    await test.send({ type: "insertCommand", installer: "pip" });
    assert.equal(test.copied.length, 0);
    assert.equal(test.terminals.length, 0);
    await test.send({ type: "copyCommand", installer: "uv" });
    assert.equal(test.copied.length, 1);
    assert.match(test.copied[0], /uv pip install/);
    test.panel.dispose();
    assert.equal(await pending, undefined);
  });

  it("shows import errors as diagnostic text and escapes their markup", async () => {
    test.state.report = { ...test.missing, q1lensInstalled: true, qbsError: "dependency failed <script>alert('x')</script>" };
    test.state.action = "Show Setup";
    const pending = test.run();
    await waitFor(() => test.panel.created);
    assert.match(test.panel.webview.html, /dependency failed/);
    assert.ok(!test.panel.webview.html.includes("<script>alert('x')</script>"));
    assert.match(test.panel.webview.html, /&lt;script&gt;/);
    test.panel.dispose();
    assert.equal(await pending, undefined);
  });

  it("retains the panel and permits retry after a temporarily unavailable kernel", async () => {
    const { pending } = await test.openSetup();
    test.state.started = false;
    await test.send({ type: "checkAgain" });
    assert.equal(test.panel.disposed, false);
    assert.ok(test.panel.webview.messages.some((message) =>
      (message as { type?: string; message?: string }).type === "checkError"
      && /run a cell once/.test((message as { message: string }).message)));
    test.state.started = true;
    test.state.report = test.ready;
    await test.send({ type: "checkAgain" });
    assert.deepEqual(await pending, { pythonPath: test.executable, pythonArgs: [] });
  });

  it("ignores unknown setup messages and installer values", async () => {
    const { pending } = await test.openSetup();
    await test.send({ type: "install", installer: "pip" });
    await test.send({ type: "copyCommand", installer: "arbitrary shell command" });
    assert.equal(test.copied.length, 0);
    assert.equal(test.terminals.length, 0);
    assert.equal(test.probes.length, 1);
    test.panel.dispose();
    assert.equal(await pending, undefined);
  });

  it("opens the original notebook from the setup panel", async () => {
    const { pending } = await test.openSetup();
    await test.send({ type: "openNotebook" });
    assert.equal(test.state.openedNotebook, test.notebookUri);
    test.panel.dispose();
    assert.equal(await pending, undefined);
  });

  it("does not execute a remote kernel's path on the extension host", async () => {
    test.state.remote = true;
    assert.equal(await test.run(), undefined);
    assert.equal(test.probes.length, 0);
    assert.match(test.messages[0].message, /different host/);
  });

  it("explains how to start an unstarted kernel without falling back to configured Python", async () => {
    test.state.started = false;
    assert.equal(await test.run(), undefined);
    assert.match(test.messages[0].message, /run a cell once/);
    assert.equal(test.probes.length, 0);
  });

  it("offers the Jupyter extension when it is missing", async () => {
    test.state.jupyter = false;
    assert.equal(await test.run(), undefined);
    assert.deepEqual(test.messages[0].actions, ["Open Jupyter Extension"]);
    assert.equal(test.probes.length, 0);
  });

  it("does not execute kernel code in an untrusted workspace", async () => {
    test.state.trusted = false;
    assert.equal(await test.run(), undefined);
    assert.match(test.messages[0].message, /Trust this workspace/);
    assert.equal(test.kernelUris.length, 0);
  });

  it("rejects an unusable executable returned by a same-host kernel", async () => {
    test.state.pythonPath = "python";
    assert.equal(await test.run(), undefined);
    assert.match(test.messages[0].message, /usable Python executable/);
    assert.equal(test.probes.length, 0);
  });

  it("rejects non-Python kernels and unsupported Python versions", async () => {
    test.state.language = "julia";
    assert.equal(await test.run(), undefined);
    assert.equal(test.probes.length, 0);
    test.state.language = "python";
    test.state.report = { executable: test.executable, version: "3.9.1", supported: false };
    assert.equal(await test.run(), undefined);
    assert.match(test.messages[1].message, /requires Python 3\.10/);
    assert.equal(test.panel.created, false);
  });

  it("cancels a queued kernel probe without waiting for it to finish", async () => {
    test.state.cancelProbe = true;
    assert.equal(await test.run(), undefined);
    assert.equal(test.state.kernelCancelled, true);
    assert.equal(test.probes.length, 0);
    assert.equal(test.messages.length, 0);
  });
});
