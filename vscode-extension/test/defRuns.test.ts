import assert from "node:assert/strict";
import { parseWebviewMessage } from "../src/q1timeline/webviewMessageValidation";

const { interpret, statistics, compileFormula, summarize, parseTolerance, compareValues } = require("../src/q1timeline/media/defRuns.js");

describe("DEF run messages and register statistics", () => {
  it("keeps literal precision on the wire and validates register and sweep shape", () => {
    const run = { type: "defRun", action: "run", project: "project.yml", revision: "abc", sequencer: "s",
      uri: "file:///source.q1asm", registers: ["R0", "R63"], observe_time: false, overrides: { X: "9007199254740993" } };
    assert.deepEqual(parseWebviewMessage(run), { ...run, valid: true });
    assert.equal(parseWebviewMessage({ ...run, overrides: { X: 2 } }).valid, false);
    assert.equal(parseWebviewMessage({ ...run, registers: ["R64"] }).valid, false);
    assert.equal(parseWebviewMessage({ ...run, registers: ["R00"] }).valid, false);
    assert.equal(parseWebviewMessage({ ...run, registers: ["R0", "R0"] }).valid, false);
    assert.equal(parseWebviewMessage({ ...run, observe_time: "false" }).valid, false);
    assert.equal(parseWebviewMessage({ ...run, action: "sweep", axes: [] }).valid, false);
    assert.equal(parseWebviewMessage({ ...run, action: "sweep", axes: [{ name: "X", min: "0", max: "2", step: "1" }] }).valid, true);
    const sweep = { ...run, action: "sweep", axes: [{ name: "X", min: "0", max: "2", step: "1" }], statistics_only: true };
    assert.equal(parseWebviewMessage(sweep).valid, true);
    assert.equal(parseWebviewMessage({ ...sweep, statistics_only: "true" }).valid, false);
    assert.equal(parseWebviewMessage({ ...sweep, registers: [], observe_time: false }).valid, false);
    assert.equal(parseWebviewMessage({ ...sweep, registers: [], observe_time: true }).valid, true);
    assert.equal(parseWebviewMessage({ ...sweep, sample_count: 1_000_000 }).valid, true);
    assert.equal(parseWebviewMessage({ ...sweep, sample_count: 1_000_001 }).valid, false);
    assert.equal(parseWebviewMessage({ ...sweep, sample_count: 25_000, statistics_only: false }).valid, true);
    assert.equal(parseWebviewMessage({ ...sweep, sample_count: 25_001, statistics_only: false }).valid, false);
    assert.equal(parseWebviewMessage({ ...sweep, action: "estimate" }).valid, true);
    assert.equal(parseWebviewMessage({ ...sweep, action: "estimate", sample_count: 32 }).valid, false);
    for (const sample_count of [0, -1, 0.5, "12", null]) {
      assert.equal(parseWebviewMessage({ ...sweep, sample_count }).valid, false);
    }
  });

  it("weights every valid case and computes population deviation", () => {
    const rows = [10, 11, 12, 12, 13, 14].map((word) => ({ words: { R2: word, R3: null }, status: "completed" }));
    const stats = statistics(rows, "signed", "R2");
    assert.deepEqual([stats.count, stats.min, stats.max, stats.mean], [6, 10, 14, 12]);
    assert.equal(stats.deviation, Math.sqrt(5 / 3));
    assert.equal(stats.bins.reduce((sum: number, bin: { count: number }) => sum + bin.count, 0), 6);
    assert.equal(statistics(rows.slice(0, 3), "signed", "R2").deviation, Math.sqrt(2 / 3));
    assert.equal(statistics(rows, "signed", "R3"), null);
  });

  it("reinterprets words without losing one-word differences near uint32 max", () => {
    const rows = [0xfffffffe, 0xffffffff].map((word) => ({ words: { R2: word }, status: "completed" }));
    const invalid = ["error", "blocked", "step_limit", "undetermined", "cancelled"].map((status) => ({ words: { R2: null }, status }));
    assert.equal(interpret(0xffffffff, "signed"), -1);
    assert.equal(interpret(0xffffffff, "unsigned"), 4294967295);
    assert.equal(statistics([...rows, ...invalid], "signed", "R2").mean, -1.5);
    const unsigned = statistics([...rows, ...invalid], "unsigned", "R2");
    assert.deepEqual([unsigned.count, unsigned.mean, unsigned.deviation], [2, 4294967294.5, 0.5]);
    assert.equal(statistics(invalid, "signed", "R2"), null);
    assert.equal(statistics(rows.slice(0, 1), "signed", "R2").deviation, 0);
  });

  it("uses full Q1 times even when a completed run has no readable register", () => {
    const rows = [
      { status: "completed", engine_completed: true, word: 0xffffffff, q1_ns: 40 },
      { status: "undetermined", engine_completed: true, word: null, q1_ns: 48 },
      { status: "blocked", engine_completed: false, word: null, q1_ns: 20 },
      { status: "error", engine_completed: true, word: null, q1_ns: 52 },
      { status: "completed", engine_completed: true, word: 3, q1_ns: null },
    ];
    const stats = statistics(rows, "signed", "time");
    assert.deepEqual([stats.count, stats.min, stats.max, stats.mean], [2, 40, 48, 44]);
    assert.deepEqual(statistics(rows, "unsigned", "time"), stats);
  });

  it("evaluates formulas against observed words and the effective inputs of each case", () => {
    const sweep = { registers: ["R0", "R1", "R2"], defs: { INPUT_X: "0", INPUT_Y: "0", R0: "0" } };
    const row = { status: "completed", words: { R0: 0xffffffff, R1: 1, R2: 421657428 },
      defs: { INPUT_X: "1", INPUT_Y: "1", R0: "8" } };
    assert.equal(compileFormula("atan2(R1, R0)", sweep)(row, "signed"), 3 * Math.PI / 4);
    assert.equal(compileFormula("R0 / 2", sweep)(row, "unsigned"), 2147483647.5);
    assert.equal(compileFormula("atan2(INPUT_Y, INPUT_X)", sweep)(row, "signed"), Math.PI / 4);
    assert.equal(compileFormula("R2 / 2**29 - atan2(INPUT_Y, INPUT_X)", sweep)(row, "signed"), 421657428 / 2**29 - Math.PI / 4);
    assert.equal(compileFormula("$R0 + -2^2 + 2^-2 + 2**3**2", sweep)(row, "signed"), 516.25);
    assert.equal(compileFormula("atan2(INPUT_Y, INPUT_X)", sweep)({ ...row, status: "undetermined" }, "signed"), Math.PI / 4);
  });

  it("rejects unsafe syntax and omits undefined case values", () => {
    const sweep = { registers: ["R0"], defs: {} };
    for (const formula of ["Math.atan2(1, 2)", "constructor(1)", "R0 = 0", "1; 2", "atan2(1)", "sin(1, 2)", "2+"]) {
      assert.throws(() => compileFormula(formula, sweep));
    }
    assert.throws(() => compileFormula("R63", sweep), /Select R63/);
    assert.throws(() => compileFormula("unknown", sweep), /Unknown variable/);
    assert.equal(compileFormula("R0^0", sweep)({ status: "completed", words: { R0: null } }, "signed"), null);
    assert.equal(compileFormula("1 / (1 / R0)", sweep)({ status: "completed", words: { R0: 0 } }, "signed"), null);
    assert.equal(compileFormula("1", sweep)({ status: "blocked" }, "signed"), null);
  });

  it("bins fractional and wide-range formula values", () => {
    const stats = summarize([-0.5, 0.1, 0.2, 0.5, null], true);
    assert.deepEqual([stats.count, stats.min, stats.max], [4, -0.5, 0.5]);
    assert.deepEqual(stats.bins.map((bin: { count: number }) => bin.count), [1, 3]);
    assert.deepEqual(summarize([0.125, 0.125], true).bins, [{ min: 0.125, max: 0.125, count: 2 }]);
    const wide = summarize([-1e308, 1e308], true);
    assert.equal(wide.mean, 0);
    assert.equal(wide.deviation, 1e308);
    assert.deepEqual(wide.bins.map((bin: { count: number }) => bin.count), [1, 1]);
  });

  it("uses an inclusive absolute tolerance and separates unavailable comparisons", () => {
    assert.equal(parseTolerance(""), null);
    assert.equal(parseTolerance("3.1e-5"), 3.1e-5);
    assert.equal(parseTolerance("0"), 0);
    for (const text of ["-1", "Infinity", "NaN", "1e400", "1 + 2"]) {
      assert.throws(() => parseTolerance(text), /non-negative absolute tolerance/);
    }
    assert.deepEqual(compareValues(2, 2.5, 0.5), { formula: 2, reference: 2.5, error: -0.5, verdict: "pass" });
    assert.equal(compareValues(2.5, 2, 0.5).verdict, "pass");
    assert.equal(compareValues(2, 2.5, 0.499).verdict, "fail");
    assert.equal(compareValues(2, 2, 0).verdict, "pass");
    assert.equal(compareValues(2, 2.5, null).verdict, "compared");
    assert.deepEqual(compareValues(2, null, 0.5), { formula: 2, reference: null, error: null, verdict: "unavailable" });
    assert.equal(compareValues(1e308, -1e308, 1).verdict, "unavailable");
  });
});
