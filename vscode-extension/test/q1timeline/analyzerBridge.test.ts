import assert from "node:assert/strict";
import { SubprocessAnalyzerBridge } from "../../src/q1timeline/analyzerBridge";

describe("q1timeline analyzer bridge", () => {
  it("runs q1timeline analysis through the Q1Lens CLI bridge", () => {
    const bridge = new SubprocessAnalyzerBridge({ execFile: async () => ({ stdout: "{}" }) });

    const args = bridge.analyzeArgs({
      projectFile: "C:\\repo\\.qbs_timeline\\q1timeline.yml",
      includeTimelineIr: true,
      includeDiagnostics: true,
      includeSourceMap: true,
      summaryOnly: true,
      mode: "debug",
      extraArgs: ["--strict"],
    });

    assert.deepEqual(args.slice(0, 6), [
      "-m",
      "q1lens",
      "q1timeline",
      "analyze",
      "--project",
      "C:\\repo\\.qbs_timeline\\q1timeline.yml",
    ]);
    assert.ok(args.includes("--include-timeline-ir"));
    assert.ok(args.includes("--include-diagnostics"));
    assert.ok(args.includes("--include-source-map"));
    assert.ok(args.includes("--summary-only"));
    assert.deepEqual(args.slice(-3), ["--mode", "debug", "--strict"]);
  });

  it("inherits process environment while allowing request overrides", async () => {
    const original = process.env.QBSTIMELINE_TEST_ENV;
    process.env.QBSTIMELINE_TEST_ENV = "from-process";
    let capturedEnv: Record<string, string> | undefined;
    const bridge = new SubprocessAnalyzerBridge({
      execFile: async (_command: string, _args: string[], options: any) => {
        capturedEnv = options.env;
        return { stdout: "{}" };
      },
    });

    try {
      await bridge.analyze({
        projectFile: "C:\\repo\\.qbs_timeline\\q1timeline.yml",
        env: { QBSTIMELINE_TEST_ENV: "from-request" },
      });
    } finally {
      if (original === undefined) {
        delete process.env.QBSTIMELINE_TEST_ENV;
      } else {
        process.env.QBSTIMELINE_TEST_ENV = original;
      }
    }

    assert.equal(capturedEnv?.QBSTIMELINE_TEST_ENV, "from-request");
    const pathKey = Object.keys(process.env).find((key) => key.toLowerCase() === "path");
    assert.ok(pathKey);
    assert.equal(capturedEnv?.[pathKey], process.env[pathKey]);
  });
});
