const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect } = require("./helpers");

function traces() {
  const repo = path.resolve(__dirname, "../../..");
  return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from argparse import Namespace", "from pathlib import Path", "from tempfile import TemporaryDirectory",
    "from q1timeline.def_runs import RunSnapshot",
    "prefix = 'move 3,R0\\nnop\\nadd R0,1,R1\\n'",
    "def trace(root, name, source, mode, limit=1000):",
    "    project = root / 'q1timeline.yml'",
    "    (root / 'seq.q1asm').write_text(source, encoding='utf-8')",
    "    project.write_text(f'alignment:\\n  mode: manual:-100\\nsimulation:\\n  max_time_ns: {limit}\\n'",
    "        'sequencers:\\n  - id: seq\\n    file: seq.q1asm\\n', encoding='utf-8')",
    "    snapshot = RunSnapshot(Namespace(project=project, execution_mode=mode, max_steps=100,",
    "        q1asm_strict=False, hardware_readiness=False, params=None, set_param=[]))",
    "    inputs = snapshot.inputs({'sequencer': 'seq', 'uri': snapshot.sources['seq']['uri'],",
    "        'registers': ['R1'], 'observe_time': True})",
    "    return dict(id=name, sequencer='seq', registers=['R1'], observeTime=True, mode=mode, maxSteps=100,",
    "        **snapshot.run(*inputs, detail=True))",
    "with TemporaryDirectory() as directory:",
    "    root = Path(directory)",
    "    print(json.dumps(dict(limited=trace(root, 'limited', prefix + 'stop', 'simulation', 12),",
    "        carried=trace(root, 'carried', prefix + 'nop\\nstop', 'simulation', 22),",
    "        completed=trace(root, 'completed', prefix + 'nop\\nnop\\nstop', 'simulation'),",
    "        unknown=trace(root, 'unknown', 'move 4,R1', 'concrete'))))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
}

test("DEF run trace separates pending writes and the planned end from observed execution", async ({page}) => {
  const trace = traces();
  await page.goto("/?case=01-digital");
  const src = path.join(__dirname, "../../src");
  await page.addStyleTag({path: path.join(src, "q1timeline/media/timeline.css")});
  await page.addScriptTag({path: path.join(src, "shared/timeline/instructionColors.js")});
  await page.addScriptTag({path: path.join(src, "q1timeline/media/defRuns.js")});
  await page.evaluate(() => {
    const toolbar = document.createElement("div");
    toolbar.innerHTML = '<span aria-label="Timeline layout"><button>Instruction</button></span>';
    document.getElementById("hardware").before(toolbar);
    q1lensDefRuns.mount(toolbar.firstElementChild, {postMessage() {}}, "def-trace", {open: true});
  });
  const panel = page.locator("#q1timeline-def-runs");
  const field = (name) => panel.locator(`[data-field="${name}"]`);
  const detail = (label) => field("instruction").getByText(label, {exact: true}).locator("xpath=following-sibling::p[1]");
  const word = panel.locator('[data-result="word"][data-observed="R1"]');
  const q1Time = panel.locator('[data-result="time"]');
  const block = (index) => panel.locator(`[data-step="${index}"]`);
  const show = (item) => page.evaluate((trace) => window.postMessage({type: "defRunState", project: "def-trace", patch: {trace}}, "*"), item);

  expect(trace.limited.ir.alignment.seq.offset).toBe(-100);
  await show(trace.limited);
  await expect(q1Time).toHaveText("12 ns");
  await expect(word).toHaveText("—");
  await block(2).click();
  await expect(detail("Computed writes")).toHaveText("R1 4 · pending · planned ready at 24 ns");
  await expect(detail("Registers readable at start")).toHaveText("R0 3 R1 unknown");
  await expect(field("position")).toContainText("8–12 ns observed · planned end 20 ns");
  await expect(block(2)).toHaveAttribute("title", /8–12 ns observed · planned end 20 ns/);

  await show(trace.carried);
  await expect(q1Time).toHaveText("22 ns");
  await block(3).click();
  await expect(detail("Pending at instruction start")).toHaveText("R1 4 · pending · planned ready at 24 ns");
  await expect(detail("Registers readable at start")).toContainText("R1 unknown");

  delete trace.completed.pending;
  await show(trace.completed);
  await expect(word).toHaveText("4");
  await block(2).click();
  await expect(detail("Computed writes")).toHaveText("R1 4 · readable at 24 ns");
  await expect(field("position")).toContainText("8–20 ns · line 3");
  await block(3).click();
  await expect(detail("Pending at instruction start")).toHaveText("R1 4 · readable at 24 ns");

  await show(trace.unknown);
  await expect(q1Time).toHaveText("4 ns");
  await expect(word).toHaveText("—");
  await expect(detail("Computed writes")).toHaveText("R1 4 · pending · availability unknown");
});
