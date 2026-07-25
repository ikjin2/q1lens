import assert from "node:assert/strict";
import {
  buildAnalyzeInvocation,
  buildQ1TimelineAnalyzeInvocation,
  buildRenderInvocation,
  runProcessWithSpawn,
} from "../src/qbs/qbsCli";

describe("qbsCli", () => {
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
