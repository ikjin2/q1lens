import assert from "node:assert/strict";
import type * as vscodeTypes from "vscode";
import { resolveFilePythonEnvironment, selectVSCodeInterpreter } from "../src/pythonEnvironment";

function uri(fsPath: string): vscodeTypes.Uri {
  return { fsPath, toString: () => `file://${fsPath}` } as vscodeTypes.Uri;
}

function harness(values: Record<string, unknown> = {}) {
  const resources: vscodeTypes.Uri[] = [];
  const resolutions: unknown[] = [];
  const configurations: vscodeTypes.Uri[] = [];
  let activations = 0;
  let executable: string | undefined = "/project/.venv/bin/python";
  const api = {
    environments: {
      getActiveEnvironmentPath(resource: vscodeTypes.Uri) {
        resources.push(resource);
        return { id: "selected-env", path: "/project/.venv" };
      },
      async resolveEnvironment(environment: unknown) {
        resolutions.push(environment);
        return { executable: { uri: executable ? uri(executable) : undefined } };
      },
    },
  };
  const vscode = {
    workspace: {
      getConfiguration(section: string, resource: vscodeTypes.Uri) {
        configurations.push(resource);
        return {
          get(key: string, fallback?: unknown) {
            if (`${section}.${key}` in values) return values[`${section}.${key}`];
            if (key.endsWith("pythonPath")) return "python";
            if (key.endsWith("pythonArgs")) return [];
            return fallback;
          },
          inspect(key: string) {
            return {
              defaultValue: key.endsWith("pythonPath") ? "python" : key.endsWith("pythonArgs") ? [] : undefined,
              workspaceFolderValue: values[`${section}.${key}`],
            };
          },
        };
      },
    },
    extensions: {
      getExtension(id: string) {
        assert.equal(id, "ms-python.python");
        return { activate: async () => { activations += 1; return api; } };
      },
    },
  } as unknown as typeof vscodeTypes;
  return {
    vscode, api, resources, configurations, resolutions,
    activations: () => activations,
    setExecutable: (value: string | undefined) => { executable = value; },
  };
}

describe("file Python environment resolution", () => {
  for (const timing of ["before opening", "while opening"] as const) {
    it(`does not open a cancelled interpreter picker ${timing} the original file`, async () => {
      const resource = uri("/original-project/file.q1asm");
      const actions: string[] = [];
      let release!: () => void;
      const opening = new Promise<void>((resolve) => { release = resolve; });
      const vscode = {
        workspace: {},
        window: { showTextDocument: async () => { actions.push("open"); await opening; } },
        commands: { executeCommand: async () => { actions.push("select"); } },
      } as unknown as typeof vscodeTypes;
      const cancellation = new AbortController();
      if (timing === "before opening") cancellation.abort();
      const pending = selectVSCodeInterpreter(vscode, resource, cancellation.signal);
      cancellation.abort();
      release();
      await pending;
      assert.deepEqual(actions, timing === "before opening" ? [] : ["open"]);
    });
  }

  it("focuses the original file before opening Python's interpreter picker without unsupported arguments", async () => {
    const resource = uri("/original-project/file.q1asm");
    const actions: unknown[] = [];
    const vscode = {
      workspace: {},
      window: { showTextDocument: async (target: vscodeTypes.Uri) => { actions.push(["showTextDocument", target]); } },
      commands: { executeCommand: async (...args: unknown[]) => { actions.push(args); } },
    } as unknown as typeof vscodeTypes;
    await selectVSCodeInterpreter(vscode, resource);
    assert.deepEqual(actions, [["showTextDocument", resource], ["python.setInterpreter"]]);
  });

  it("opens the interpreter picker for a workspace target without trying to edit its directory", async () => {
    const resource = uri("/original-project");
    const actions: unknown[] = [];
    const vscode = {
      workspace: { workspaceFolders: [{ uri: resource }] },
      window: { showTextDocument: async (target: vscodeTypes.Uri) => { actions.push(["showTextDocument", target]); } },
      commands: { executeCommand: async (...args: unknown[]) => { actions.push(args); } },
    } as unknown as typeof vscodeTypes;
    await selectVSCodeInterpreter(vscode, resource);
    assert.deepEqual(actions, [["python.setInterpreter"]]);
  });

  for (const kind of ["qbs", "q1asm"] as const) {
    it(`uses the VS Code selection for ${kind}, ignoring contributed python defaults`, async () => {
      const test = harness();
      const resource = uri("/project/timeline.yml");
      const result = await resolveFilePythonEnvironment(test.vscode, resource, kind);
      const { assertCurrentSelection, ...details } = result;
      assert.equal(typeof assertCurrentSelection, "function");
      assert.deepEqual(details, {
        pythonPath: "/project/.venv/bin/python",
        pythonArgs: [],
        source: "vscode",
        sourceLabel: "VS Code selected interpreter",
        resource,
      });
      assert.deepEqual(test.resources, [resource, resource]);
      assert.deepEqual(test.resolutions, [{ id: "selected-env", path: "/project/.venv" }]);
      assert.ok(test.configurations.every(value => value === resource));
    });

    it(`preserves an explicit python launcher for ${kind} without loading the Python extension`, async () => {
      const test = harness({ "q1lens.pythonPath": "python", "q1lens.pythonArgs": ["-X", "utf8"] });
      const result = await resolveFilePythonEnvironment(test.vscode, uri("/project/file"), kind);
      assert.equal(result.pythonPath, "python");
      assert.deepEqual(result.pythonArgs, ["-X", "utf8"]);
      assert.equal(result.source, "manual");
      assert.equal(result.manualSetting, "q1lens.pythonPath");
      assert.equal(test.activations(), 0);
    });
  }

  it("preserves launcher arguments as a manual pair even without a path override", async () => {
    const test = harness({ "qbsTimeline.pythonArgs": ["-3.12"] });
    const result = await resolveFilePythonEnvironment(test.vscode, uri("/project/file"), "qbs");
    assert.equal(result.pythonPath, "python");
    assert.deepEqual(result.pythonArgs, ["-3.12"]);
    assert.equal(result.source, "manual");
    assert.equal(result.manualSetting, "qbsTimeline.pythonArgs");
    assert.equal(test.activations(), 0);
  });

  for (const kind of ["qbs", "q1asm"] as const) {
    it(`follows the scoped VS Code opt-in for ${kind} without changing saved manual settings`, async () => {
      const values = {
        "q1lens.pythonEnvironment": "vscode",
        "q1lens.pythonPath": "/old/shared/python",
        "q1lens.pythonArgs": ["-3.12"],
        "q1lens.q1timeline.pythonPath": "/old/timeline/python",
        "q1lens.q1timeline.pythonArgs": ["-X", "utf8"],
        "qbloxTimeline.pythonPath": "/old/qblox/python",
        "qbsTimeline.pythonPath": "/old/qbs/python",
        "q1timeline.pythonPath": "/old/legacy/python",
        "q1lens.q1timelineCommand": "custom-command",
      };
      const originalValues = structuredClone(values);
      const test = harness(values);
      const resource = uri("/second-workspace/project.yml");
      const result = await resolveFilePythonEnvironment(test.vscode, resource, kind);
      assert.equal(result.pythonPath, "/project/.venv/bin/python");
      assert.deepEqual(result.pythonArgs, []);
      assert.equal(result.source, "vscode");
      assert.equal(result.manualSetting, undefined);
      assert.ok(test.configurations.every(value => value === resource));
      assert.deepEqual(test.resources, [resource, resource]);
      assert.deepEqual(values, originalValues);
    });

    it(`restores saved legacy settings for ${kind} when changing the preference back to auto`, async () => {
      const setting = kind === "qbs" ? "qbsTimeline.pythonPath" : "q1timeline.pythonPath";
      const values = { "q1lens.pythonEnvironment": "vscode", [setting]: "/saved/legacy/python" };
      const test = harness(values);
      const resource = uri("/project/file");
      assert.equal((await resolveFilePythonEnvironment(test.vscode, resource, kind)).source, "vscode");
      values["q1lens.pythonEnvironment"] = "auto";
      const restored = await resolveFilePythonEnvironment(test.vscode, resource, kind);
      assert.equal(restored.source, "manual");
      assert.equal(restored.pythonPath, "/saved/legacy/python");
      assert.equal(restored.manualSetting, setting);
      assert.equal(test.activations(), 1);
    });
  }

  it("ignores a custom-command-only override when explicitly following VS Code", async () => {
    const test = harness({ "q1lens.pythonEnvironment": "vscode", "qbsTimeline.q1timelineCommand": "legacy-command" });
    const result = await resolveFilePythonEnvironment(test.vscode, uri("/file"), "qbs");
    assert.equal(result.source, "vscode");
    assert.equal(result.pythonPath, "/project/.venv/bin/python");
    assert.deepEqual(result.pythonArgs, []);
  });

  it("does not treat empty argument overrides as a manually selected interpreter", async () => {
    const test = harness({ "q1lens.pythonArgs": [], "qbloxTimeline.pythonArgs": ["-3.12"] });
    const result = await resolveFilePythonEnvironment(test.vscode, uri("/project/file"), "qbs");
    assert.equal(result.source, "vscode");
    assert.deepEqual(result.pythonArgs, []);
  });

  for (const setting of ["q1lens.pythonPath", "qbloxTimeline.pythonPath", "qbsTimeline.pythonPath"]) {
    it(`honors the ${setting} QBS override`, async () => {
      const test = harness({ [setting]: "/custom/bin/python" });
      const result = await resolveFilePythonEnvironment(test.vscode, uri("/project/file"), "qbs");
      assert.equal(result.pythonPath, "/custom/bin/python");
      assert.equal(result.manualSetting, setting);
      assert.equal(result.sourceLabel, `Manual setting: ${setting}`);
    });
  }

  it("keeps canonical QBS settings ahead of legacy aliases", async () => {
    const test = harness({
      "q1lens.pythonPath": "/canonical/python",
      "qbloxTimeline.pythonPath": "/qblox/python",
      "qbsTimeline.pythonPath": "/legacy/python",
      "q1lens.pythonArgs": ["-I"],
      "qbsTimeline.pythonArgs": ["-3.12"],
    });
    const result = await resolveFilePythonEnvironment(test.vscode, uri("/project/file"), "qbs");
    assert.equal(result.pythonPath, "/canonical/python");
    assert.deepEqual(result.pythonArgs, ["-I"]);
  });

  const q1asmKeys = [
    "q1lens.q1timeline.pythonPath", "qbloxTimeline.q1timeline.pythonPath",
    "q1timeline.pythonPath", "q1lens.pythonPath", "qbloxTimeline.pythonPath",
  ];
  q1asmKeys.forEach((setting, index) => {
    it(`preserves Q1ASM setting precedence starting with ${setting}`, async () => {
      const test = harness(Object.fromEntries(q1asmKeys.slice(index).map((key, n) => [key, `/env${n}/python`])));
      const result = await resolveFilePythonEnvironment(test.vscode, uri("/project/file.q1asm"), "q1asm");
      assert.equal(result.pythonPath, "/env0/python");
      assert.equal(result.manualSetting, setting);
      assert.equal(test.activations(), 0);
    });
  });

  it("keeps the Q1ASM-specific launcher arguments ahead of shared arguments", async () => {
    const test = harness({ "q1lens.q1timeline.pythonArgs": ["-3.12"], "q1lens.pythonArgs": ["-I"] });
    const result = await resolveFilePythonEnvironment(test.vscode, uri("/project/file.q1asm"), "q1asm");
    assert.equal(result.pythonPath, "python");
    assert.deepEqual(result.pythonArgs, ["-3.12"]);
    assert.equal(result.manualSetting, "q1lens.q1timeline.pythonArgs");
  });

  for (const setting of ["q1lens.q1timelineCommand", "qbsTimeline.q1timelineCommand"]) {
    it(`preserves the existing launcher when ${setting} is configured`, async () => {
      const test = harness({ [setting]: "custom-timeline-command" });
      const result = await resolveFilePythonEnvironment(test.vscode, uri("/project/file"), "qbs");
      assert.equal(result.pythonPath, "python");
      assert.equal(result.manualSetting, setting);
      assert.equal(result.source, "manual");
      assert.equal(test.activations(), 0);
    });
  }

  it("does not apply a custom QBS timeline command to standalone Q1ASM", async () => {
    const test = harness({ "q1lens.q1timelineCommand": "custom-timeline-command" });
    assert.equal((await resolveFilePythonEnvironment(test.vscode, uri("/file.q1asm"), "q1asm")).source, "vscode");
  });

  it("keeps an explicitly empty canonical custom command ahead of its legacy alias", async () => {
    const test = harness({ "q1lens.q1timelineCommand": "", "qbsTimeline.q1timelineCommand": "legacy-command" });
    assert.equal((await resolveFilePythonEnvironment(test.vscode, uri("/file"), "qbs")).source, "vscode");
  });

  it("queries the original resource again and uses its changed VS Code interpreter", async () => {
    const test = harness();
    const resource = uri("/second-workspace/file.q1asm");
    await resolveFilePythonEnvironment(test.vscode, resource, "q1asm");
    test.setExecutable("/second-workspace/.new-venv/bin/python");
    const refreshed = await resolveFilePythonEnvironment(test.vscode, resource, "q1asm");
    assert.equal(refreshed.pythonPath, "/second-workspace/.new-venv/bin/python");
    assert.deepEqual(test.resources, [resource, resource, resource, resource]);
    assert.ok(test.configurations.every(value => value === resource));
  });

  for (const kind of ["qbs", "q1asm"] as const) {
    for (const failed of [false, true]) {
      it(`rejects an obsolete ${kind} interpreter when its ${failed ? "failed" : "successful"} resolution finishes after selection changes`, async () => {
        const test = harness();
        const resource = uri("/original-project/source.py");
        let active = { id: "python-a", path: "/env/a/python" };
        test.api.environments.getActiveEnvironmentPath = (target) => {
          assert.equal(target, resource);
          return { ...active };
        };
        let release!: () => void;
        const gate = new Promise<void>((resolve) => { release = resolve; });
        test.api.environments.resolveEnvironment = async () => {
          await gate;
          if (failed) throw new Error("Obsolete A resolution failed");
          return { executable: { uri: uri("/env/a/python") } };
        };
        const pending = resolveFilePythonEnvironment(test.vscode, resource, kind);
        await new Promise<void>((resolve) => setImmediate(resolve));
        active = { id: "python-b", path: "/env/b/python" };
        release();
        await assert.rejects(pending, /Python selection changed.*try again/i);

        test.api.environments.resolveEnvironment = async () => ({ executable: { uri: uri("/env/b/python") } });
        assert.equal((await resolveFilePythonEnvironment(test.vscode, resource, kind)).pythonPath, "/env/b/python");
      });
    }

    it(`accepts a fresh ${kind} selection wrapper with the same environment and equivalent path`, async () => {
      const test = harness();
      const resource = uri("/original-project/source.py");
      const initialPath = process.platform === "win32" ? "C:\\Project\\.venv\\python.exe" : "/project/.venv/python";
      const equivalentPath = process.platform === "win32" ? "c:/project/.venv/python.exe" : initialPath;
      let activePath = initialPath;
      test.api.environments.getActiveEnvironmentPath = (target) => {
        assert.equal(target, resource);
        return { id: "same-environment", path: activePath };
      };
      test.api.environments.resolveEnvironment = async () => {
        activePath = equivalentPath;
        return { executable: { uri: uri(initialPath) } };
      };
      assert.equal((await resolveFilePythonEnvironment(test.vscode, resource, kind)).pythonPath, initialPath);
    });
  }

  it("returns Windows executable paths without modifying them", async () => {
    const test = harness();
    test.setExecutable("C:\\My Project\\.venv\\Scripts\\python.exe");
    const result = await resolveFilePythonEnvironment(test.vscode, uri("C:\\My Project\\file.q1asm"), "q1asm");
    assert.equal(result.pythonPath, "C:\\My Project\\.venv\\Scripts\\python.exe");
  });

  it("asks for the Python extension if it is unavailable instead of using PATH", async () => {
    const test = harness();
    test.vscode.extensions.getExtension = () => undefined;
    await assert.rejects(resolveFilePythonEnvironment(test.vscode, uri("/file"), "qbs"), /Install or enable the Microsoft Python extension/);
  });

  it("explains a missing environment API instead of using PATH", async () => {
    const test = harness();
    (test.api as { environments?: unknown }).environments = undefined;
    await assert.rejects(resolveFilePythonEnvironment(test.vscode, uri("/file"), "qbs"), /environment API is unavailable/);
  });

  it("rejects a selected environment that has no Python executable", async () => {
    const test = harness();
    test.setExecutable(undefined);
    await assert.rejects(resolveFilePythonEnvironment(test.vscode, uri("/file"), "qbs"), /has no usable Python executable/);
  });

  it("rejects an unresolved environment without running its configured folder path", async () => {
    const test = harness();
    (test.api.environments as { resolveEnvironment: (environment: unknown) => Promise<unknown> }).resolveEnvironment = async () => undefined;
    await assert.rejects(resolveFilePythonEnvironment(test.vscode, uri("/file"), "qbs"), /Python: Select Interpreter/);
  });

  it("does not resolve or fall back when VS Code has no environment selection", async () => {
    const test = harness();
    (test.api.environments as { getActiveEnvironmentPath: (resource: vscodeTypes.Uri) => unknown }).getActiveEnvironmentPath = () => undefined;
    await assert.rejects(resolveFilePythonEnvironment(test.vscode, uri("/file"), "qbs"), /Python: Select Interpreter/);
    assert.equal(test.resolutions.length, 0);
  });
});
