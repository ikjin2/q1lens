const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { analyzeProject, demoProject, validateIr } = require("../analysis.cjs");

test("rejects non-timeline JSON and malformed event records", () => {
  assert.throws(() => validateIr({ events: [] }), /not a TimelineIR/);
  assert.throws(() => validateIr({ events: [null], sequencers: [] }), /Invalid TimelineIR/);
  assert.throws(() => validateIr({ events: [{ id: "x" }], sequencers: [] }), /Invalid TimelineIR/);
});

test("rejects malformed optional diagnostics before accepting a timeline", () => {
  for (const diagnostics of [null, {}, "error", [null], ["error"], [[]]]) {
    assert.throws(() => validateIr({ events: [], sequencers: [], diagnostics }), /Invalid TimelineIR.*diagnostics/);
  }
  const ir = { events: [], sequencers: [], diagnostics: [{ severity: "warning", message: "Incomplete timing" }] };
  assert.equal(validateIr(ir), ir);
  assert.doesNotThrow(() => validateIr({ events: [], sequencers: [] }));
});

test("loads exported JSON without requiring a Python installation", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "q1lens-desktop-test-"));
  try {
    const file = path.join(directory, "timeline_ir.json");
    const ir = { events: [{ id: "event-1", kind: "play" }], sequencers: [{ id: "seq-1" }] };
    await fs.writeFile(file, JSON.stringify(ir));
    const result = await analyzeProject(file, "missing-python");
    assert.deepEqual(result.ir, ir);
    assert.equal(result.projectPath, file);
    await fs.writeFile(file, "{broken");
    await assert.rejects(analyzeProject(file), /Could not read TimelineIR/);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("analyzes the included Q1ASM project through the real Python CLI", { timeout: 120_000 }, async () => {
  const result = await analyzeProject(demoProject);
  assert.equal(result.projectPath, demoProject);
  assert.equal(result.ir.sequencers.length, 8);
  assert.ok(result.ir.events.length > 0);
  assert.ok(result.ir.events.some((event) => event.kind === "play"));
  assert.ok(Array.isArray(result.diagnostics));
});

test("compiles a QBS project into a temporary Q1ASM timeline", { timeout: 120_000 }, async () => {
  const project = path.resolve(__dirname, "../../examples/basic-transmon/qbstimeline.yml");
  const result = await analyzeProject(project);
  assert.equal(result.projectPath, project);
  assert.ok(result.ir.events.length > 0);
  assert.ok(result.ir.sequencers.length > 0);
  assert.ok(result.ir.project.root.startsWith(os.tmpdir()));
});
