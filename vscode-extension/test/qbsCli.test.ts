import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import Module = require("node:module");
import { PassThrough } from "node:stream";
import {
  buildAnalyzeInvocation,
  buildQ1TimelineAnalyzeInvocation,
  buildRenderInvocation,
  runProcessWithSpawn,
} from "../src/qbs/qbsCli";

describe("qbsCli", () => {
  it("passes an explicit artifact output directory independently of the IR output path", () => {
    const invocation = buildAnalyzeInvocation({
      pythonPath: "python", pythonArgs: [], projectFile: "C:\\repo\\qbstimeline.yml",
      irPath: "C:\\reports\\custom-ir.json", outputDir: "C:\\repo\\generated",
    });
    assert.equal(invocation.args[invocation.args.indexOf("--out-dir") + 1], "C:\\repo\\generated");
    assert.equal(invocation.args[invocation.args.indexOf("--out") + 1], "C:\\reports\\custom-ir.json");
  });

  it("builds analyze invocation with paths as separate arguments", () => {
    const invocation = buildAnalyzeInvocation({
      pythonPath: "C:\\Python 312\\python.exe",
      pythonArgs: [],
      projectFile: "C:\\repo with spaces\\qbstimeline.yml",
      irPath: "C:\\repo with spaces\\.qbs_timeline\\qbs_ir.json",
    });

    assert.equal(invocation.command, "C:\\Python 312\\python.exe");
    assert.deepEqual(invocation.args, [
      "-m",
      "q1lens",
      "analyze",
      "--project",
      "C:\\repo with spaces\\qbstimeline.yml",
      "--out",
      "C:\\repo with spaces\\.qbs_timeline\\qbs_ir.json",
    ]);
  });

  it("builds render invocation", () => {
    const invocation = buildRenderInvocation({
      pythonPath: "python",
      pythonArgs: [],
      irPath: "/repo/.qbs_timeline/qbs_ir.json",
      htmlPath: "/repo/.qbs_timeline/index.html",
    });

    assert.deepEqual(invocation.args, [
      "-m",
      "q1lens",
      "render",
      "--ir",
      "/repo/.qbs_timeline/qbs_ir.json",
      "--out",
      "/repo/.qbs_timeline/index.html",
    ]);
  });

  it("inserts configured Python launcher arguments before -m", () => {
    const invocation = buildAnalyzeInvocation({
      pythonPath: "py",
      pythonArgs: ["-3.12"],
      projectFile: "C:\\repo\\qbstimeline.yml",
      irPath: "C:\\repo\\.qbs_timeline\\qbs_ir.json",
    });

    assert.deepEqual(invocation.args.slice(0, 3), ["-3.12", "-m", "q1lens"]);
  });

  it("builds q1timeline analyzer invocation through the Q1Lens bridge", () => {
    const invocation = buildQ1TimelineAnalyzeInvocation({
      pythonPath: "python",
      pythonArgs: ["-X", "utf8"],
      projectFile: "C:\\repo\\.qbs_timeline\\q1timeline.yml",
      timelineIrPath: "C:\\repo\\.qbs_timeline\\.q1timeline\\timeline_ir.json",
      diagnosticsPath: "C:\\repo\\.qbs_timeline\\.q1timeline\\diagnostics.json",
    });

    assert.deepEqual(invocation.args.slice(0, 5), ["-X", "utf8", "-m", "q1lens", "q1timeline"]);
    assert.deepEqual(invocation.args.slice(5, 9), [
      "analyze",
      "--project",
      "C:\\repo\\.qbs_timeline\\q1timeline.yml",
      "--out",
    ]);
  });

  it("uses a configured q1timeline command when provided", () => {
    const invocation = buildQ1TimelineAnalyzeInvocation({
      pythonPath: "python",
      pythonArgs: ["-X", "utf8"],
      q1timelineCommand: "q1timeline",
      projectFile: "C:\\repo\\.qbs_timeline\\q1timeline.yml",
      timelineIrPath: "C:\\repo\\.qbs_timeline\\.q1timeline\\timeline_ir.json",
      diagnosticsPath: "C:\\repo\\.qbs_timeline\\.q1timeline\\diagnostics.json",
    });

    assert.equal(invocation.command, "q1timeline");
    assert.deepEqual(invocation.args.slice(0, 4), [
      "analyze",
      "--project",
      "C:\\repo\\.qbs_timeline\\q1timeline.yml",
      "--out",
    ]);
  });

  it("captures stdout and stderr from a spawned command", async () => {
    const result = await runProcessWithSpawn(
      process.execPath,
      ["-e", "console.log('ok'); console.error('warn')"],
      { cwd: process.cwd() },
    );

    assert.equal(result.exitCode, 0);
    assert.match(result.stdout, /ok/);
    assert.match(result.stderr, /warn/);
  });

  it("keeps UTF-8 diagnostic text intact when a pipe splits inside a character", async () => {
    const child = Object.assign(new EventEmitter(), { stdout: new PassThrough(), stderr: new PassThrough() });
    const originalLoad = (Module as any)._load;
    const modulePath = require.resolve("../src/qbs/qbsCli");
    const cachedModule = require.cache[modulePath];
    (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
      return request === "node:child_process" ? { spawn: () => child } : originalLoad.call(this, request, parent, isMain);
    };
    delete require.cache[modulePath];
    try {
      const chunks: string[] = [];
      const result = require("../src/qbs/qbsCli").runProcessWithSpawn("python", [], {
        cwd: process.cwd(), onOutput: (chunk: string) => chunks.push(chunk),
      });
      const text = "경로 확인 ⚛";
      const bytes = Buffer.from(text);
      for (const stream of [child.stdout, child.stderr]) {
        stream.write(bytes.subarray(0, 1));
        stream.write(bytes.subarray(1, 5));
        stream.write(bytes.subarray(5));
      }
      child.emit("close", 1);
      assert.deepEqual(await result, { exitCode: 1, stdout: text, stderr: text });
      assert.equal(chunks.join(""), text + text);
    } finally {
      if (cachedModule) require.cache[modulePath] = cachedModule;
      else delete require.cache[modulePath];
      (Module as any)._load = originalLoad;
    }
  });

  it("rejects spawned commands that exceed the configured timeout", async () => {
    await assert.rejects(
      () =>
        runProcessWithSpawn(
          process.execPath,
          ["-e", "setTimeout(() => {}, 1000)"],
          { cwd: process.cwd(), timeoutMs: 25 },
        ),
      /timed out after 25 ms/,
    );
  });
});
