const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect } = require("./helpers");

const assets = process.env.Q1LENS_BROWSER_ASSETS || path.resolve(__dirname, "../../src");

function fixtures() {
  const repo = path.resolve(__dirname, "../../..");
  return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json",
    "from contextlib import redirect_stdout",
    "from io import StringIO",
    "from pathlib import Path",
    "from tempfile import TemporaryDirectory",
    "from q1timeline.analysis.api import analyze_text",
    "from q1timeline.cli import main",
    "from q1timeline.playback import PlaybackSession",
    "from q1timeline.render.html import render_html",
    "source = 'move 3,R0\\nmove 4,R1\\nnop\\nbody: wait R1\\nadd R1,4,R1\\nnop\\nloop R0,@body\\nstop'",
    "nested = 'move 3,R1\\nmove 4,R3\\nnop\\nmiddle: move R1,R2\\ninner: wait R3\\nloop R2,@inner\\nadd R3,4,R3\\nnop\\nloop R1,@middle\\nstop'",
    "late = 'move 4,R1\\nmove 12,R3\\nmove 4,R4\\nnop\\nmiddle: move 2,R2\\ninner: wait R3\\nwait R4\\nloop R2,@inner\\nsub R3,4,R3\\nadd R4,4,R4\\nnop\\nloop R1,@middle\\nstop'",
    "constant = source.replace('wait R1', 'wait 4')",
    "unresolved = 'move 8,R1\\nnop\\nbody: wait 4\\nsub R1,2,R1\\nnop\\njge R1,5,@tail\\nwait 8\\ntail: jmp @body'",
    "def run(program, **options):",
    "    return analyze_text(program, file='loop.q1asm', sequencer_id='s', **options)",
    "play = PlaybackSession({'s': ('loop.q1asm', source)})",
    "play.run(budget_ms=1000, max_steps=80)",
    "def analyze_file(file, *options):",
    "    output = StringIO()",
    "    with redirect_stdout(output):",
    "        assert main(['analyze', str(file), '--format', 'vscode-json', '--include-timeline-ir', '--no-render', *options]) == 0",
    "    return json.loads(output.getvalue())['timeline_ir']",
    "def expanded_preview():",
    "    with TemporaryDirectory(prefix='q1lens-loop-timing-') as work:",
    "        file = Path(work) / 'loop.q1asm'",
    "        file.write_text(constant, encoding='utf-8')",
    "        key = next(e for e in analyze_file(file)['events'] if e['kind']=='loop_block')['meta']['loop_preview_key']",
    "        return analyze_file(file, '--loop-preview', f'{key}=2')",
    "partial = run(nested.replace('move 3,R1', 'move 5,R1').replace('move R1,R2', 'move 2,R2').replace('add R3,4,R3', 'nop'))",
    "parent = next(e for e in partial['events'] if e['kind']=='loop_block' and e['meta'].get('child_loop_event_ids'))",
    "parent['meta'].update(period_invariant=False, iteration_durations=[8, 8, 8])",
    "cases = dict(varying=run(source), nested=run(nested), invariant=run(constant), expanded=expanded_preview(), unknown=run(unresolved),",
    "    nested_equal=run(nested.replace('move 3,R1', 'move 2,R1')), nested_late=run(late), partial=partial,",
    "    aligned=run(source, alignment_mode='manual:20'), observed=play.snapshot(), forever_address=run('wait 100\\njmp 0'),",
    "    forever_label=run('body: wait 100\\njmp @body'), forever_zero=run('body: wait 0\\njmp @body'),",
    "    forever_q1=run('body: nop\\njmp @body'), forever_unknown=run(source.replace('move 3,R0\\n', '').replace('loop R0,@body\\nstop', 'jmp @body')))",
    "print(json.dumps({name: dict(ir=ir, html=render_html(ir)) for name, ir in cases.items()}))",
  ].join("\n")], { cwd: repo, encoding: "utf8", timeout: 10000, maxBuffer: 8 * 1024 * 1024,
    env: { ...process.env, PYTHONPATH: path.join(repo, "src") } }));
}

async function open(page, data, viewer) {
  await page.goto("about:blank");
  const html = viewer === "standalone" ? data.html : data.html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi,
    (tag, attributes) => /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await page.setContent(html);
  if (viewer === "extension") {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.evaluate(() => {
      let saved = {};
      window.messages = [];
      window.acquireVsCodeApi = () => ({ postMessage: (message) => window.messages.push(message),
        getState: () => saved, setState: (value) => { saved = value; } });
    });
    for (const file of ["shared/timeline/renderer.css", "q1timeline/media/timeline.css", "q1timeline/media/hardwareView.css"]) {
      await page.addStyleTag({ path: path.join(assets, file) });
    }
    for (const file of ["shared/timeline/instructionColors.js", "shared/timeline/renderer.js", "q1timeline/media/timelineAdapter.js",
      "q1timeline/media/hardwareState.js", "q1timeline/media/hardwareView.js", "q1timeline/media/timeline.js"]) {
      await page.addScriptTag({ path: path.join(assets, file) });
    }
  }
  await page.locator("#q1timeline-analysis-details > summary").click();
}

function inspectorField(page, name) {
  return page.locator("#event-inspector-fields").getByText(name, { exact: true }).locator("xpath=following-sibling::dd[1]");
}

async function selectLoop(page, data, viewer, loop = data.ir.events.find((event) => event.kind === "loop_block")) {
  const type = viewer === "standalone" ? ".loop-bracket-start" : ".timeline-loop-range";
  await page.locator(`#timeline-root ${type}[data-event-id="${loop.id}"]`).press("Enter");
  return loop;
}

test("both viewers report the varying total of a jointly changing nested loop", async ({ page }) => {
  const { nested } = fixtures();
  const parent = nested.ir.events.find((event) => event.kind === "loop_block" && event.meta.count === 3 &&
    event.meta.child_loop_event_ids?.length);
  expect(parent.meta.iteration_durations).toEqual([12, 16, 12]);
  expect(parent.meta.total_duration).toMatchObject({ kind: "concrete", value: 40 });
  expect(parent.meta.period_invariant).toBe(false);
  expect(parent.meta).not.toHaveProperty("period_affine");
  expect(nested.ir.events.find((event) => event.kind === "stop")).toMatchObject({
    confidence: "exact", t0: { kind: "concrete", value: 40 },
  });

  for (const viewer of ["standalone", "extension"]) {
    await open(page, nested, viewer);
    await selectLoop(page, nested, viewer, parent);
    await expect(inspectorField(page, "RT preview range")).toHaveText("0 ns → 12 ns");
    await expect(inspectorField(page, "Total RT duration")).toHaveText("40 ns");
    await expect(inspectorField(page, "RT period")).toHaveText("Varying");
    await expect(inspectorField(page, "Confidence")).toHaveText("exact");
  }
});

test("both viewers distinguish complete and partial equal loop periods", async ({ page }) => {
  const fixturesByName = fixtures();
  for (const [name, count, samples, total, label] of [
    ["nested_equal", 2, [8, 8], 16, "Invariant · 8 ns"],
    ["nested_late", 4, [32, 32, 32], 136, "Varying"],
    ["partial", 5, [8, 8, 8], 40, "Unknown"],
  ]) {
    const data = fixturesByName[name];
    const parent = data.ir.events.find((event) => event.kind === "loop_block" && event.meta.child_loop_event_ids?.length);
    expect(parent.meta).toMatchObject({ count, iteration_durations: samples, period_invariant: false,
      shown_iterations: [0], total_duration: { kind: "concrete", value: total } });
    expect(parent.meta).not.toHaveProperty("period_affine");
    expect(data.ir.events.find((event) => event.kind === "stop")).toMatchObject({
      confidence: "exact", t0: { kind: "concrete", value: total },
    });

    for (const viewer of ["standalone", "extension"]) {
      await open(page, data, viewer);
      await selectLoop(page, data, viewer, parent);
      await expect(inspectorField(page, "RT preview range")).toHaveText(`0 ns → ${samples[0]} ns`);
      await expect(inspectorField(page, "Total RT duration")).toHaveText(`${total} ns`);
      await expect(inspectorField(page, "RT period")).toHaveText(label);
    }
  }
});

for (const viewer of ["standalone", "extension"]) {
  test(`${viewer} separates compact loop preview timing from the proven total`, async ({ page }) => {
    page.setDefaultTimeout(5000);
    const data = fixtures();
    for (const [name, total, period] of [["varying", "24 ns", "Varying"], ["invariant", "12 ns", "Invariant · 4 ns"],
      ["expanded", "12 ns", "Invariant · 4 ns"], ["unknown", "Unknown", "Unknown"]]) {
      await open(page, data[name], viewer);
      const loop = await selectLoop(page, data[name], viewer);
      expect([loop.t0.value, loop.t1.value]).toEqual([0, 4]);
      await expect(inspectorField(page, "RT preview range")).toHaveText("0 ns → 4 ns");
      await expect(inspectorField(page, "Total RT duration")).toHaveText(total);
      await expect(inspectorField(page, "RT period")).toHaveText(period);
      await expect(inspectorField(page, "Time range")).toHaveCount(0);
      if (name === "varying") {
        expect(data[name].ir.events.find((event) => event.kind === "stop").t0.value).toBe(24);
      } else if (name === "expanded") {
        expect(loop.meta.shown_iterations).toEqual([0, 1]);
        if (viewer === "standalone") await expect(inspectorField(page, "Loop iterations")).toHaveText("2/3");
      } else if (name === "invariant") {
        if (viewer === "standalone") {
          const first = data[name].ir.events.find((event) => event.kind === "wait");
          await page.getByRole("button", { name: "Open first iteration" }).click();
          await expect(page.locator(`#timeline-root .is-selected[data-event-id="${first.id}"]`)).toBeVisible();
        } else {
          await page.getByRole("button", { name: "Show next iteration", exact: true }).click();
          await expect.poll(() => page.evaluate(() => window.messages.find((message) => message.type === "setLoopPreview")))
            .toMatchObject({ loopKey: loop.meta.loop_preview_key, visibleIterations: 2 });
        }
      }
    }
  });

  test(`${viewer} uses the active time basis and keeps fully observed loop timing`, async ({ page }) => {
    page.setDefaultTimeout(5000);
    const data = fixtures();
    await open(page, data.aligned, viewer);
    await selectLoop(page, data.aligned, viewer);
    await expect(inspectorField(page, "RT preview range")).toHaveText("20 ns → 24 ns");
    await expect(inspectorField(page, "Total RT duration")).toHaveText("24 ns");
    if (viewer === "extension") {
      await page.getByRole("button", { name: "Local time basis" }).click();
      await selectLoop(page, data.aligned, viewer);
      await expect(inspectorField(page, "RT preview range")).toHaveText("0 ns → 4 ns");
      await expect(inspectorField(page, "Total RT duration")).toHaveText("24 ns");
    }

    await open(page, data.observed, viewer);
    const loop = await selectLoop(page, data.observed, viewer);
    expect([loop.t0.value, loop.t1.value, loop.meta.completed_iterations]).toEqual([0, 24, 3]);
    if (viewer === "extension") {
      await page.getByRole("button", { name: "Expand executed iterations", exact: true }).click();
      await selectLoop(page, data.observed, viewer);
    }
    await expect(inspectorField(page, "Time range")).toHaveText(/^0(?: ns)? (?:->|→) 24(?: ns)?$/);
    await expect(inspectorField(page, "RT preview range")).toHaveCount(0);
    await expect(inspectorField(page, "Total RT duration")).toHaveCount(0);
  });

  test(`${viewer} distinguishes proven forever RT duration from an unresolved loop`, async ({ page }) => {
    page.setDefaultTimeout(5000);
    const data = fixtures();
    for (const [name, span, total, period, confidence] of [
      ["forever_address", 100, "Unbounded", "Invariant · 100 ns", "exact"],
      ["forever_label", 100, "Unbounded", "Invariant · 100 ns", "exact"],
      ["forever_zero", 4, "Unbounded", "Invariant · 4 ns", "exact"],
      ["forever_unknown", 4, "Unknown", "Unknown", "unknown"],
    ]) {
      await open(page, data[name], viewer);
      const loop = await selectLoop(page, data[name], viewer);
      expect(loop.meta.count).toBe("forever");
      expect(loop.meta.total_duration).toBeUndefined();
      await expect(inspectorField(page, "RT preview range")).toHaveText(`0 ns → ${span} ns`);
      await expect(inspectorField(page, "Total RT duration")).toHaveText(total);
      await expect(inspectorField(page, "RT period")).toHaveText(period);
      await expect(inspectorField(page, "Confidence")).toHaveText(confidence);
    }

    await open(page, data.forever_q1, viewer);
    const loop = data.forever_q1.ir.events.find((event) => event.kind === "loop_block");
    const type = viewer === "standalone" ? ".kind-loop_block" : ".timeline-loop-range";
    const annotation = page.locator(`#timeline-root ${type}[data-event-id="${loop.id}"]`);
    await expect(annotation).toHaveCount(0);
    await page.getByRole("button", { name: viewer === "standalone" ? "Debug" : "Debug view", exact: true }).click();
    await annotation.press("Enter");
    await expect(inspectorField(page, "RT preview range")).toHaveText("0 ns → 0 ns");
    await expect(inspectorField(page, "Total RT duration")).toHaveText("0 ns");
    await expect(inspectorField(page, "RT period")).toHaveText("Invariant · 0 ns");
    await expect(inspectorField(page, "Confidence")).toHaveText("exact");
  });
}
