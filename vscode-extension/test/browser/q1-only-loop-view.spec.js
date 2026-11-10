const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect } = require("./helpers");

const assets = process.env.Q1LENS_BROWSER_ASSETS || path.resolve(__dirname, "../../src");

function fixtures() {
  const repo = path.resolve(__dirname, "../../..");
  return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json",
    "from q1timeline.analysis.api import analyze_text",
    "from q1timeline.playback import PlaybackSession",
    "from q1timeline.render.html import render_html",
    "def run(body, play=False, tail='\\nwait 12\\nstop'):",
    "    source = f'move 3,R0\\nnop\\nbody: {body}\\nloop R0,@body{tail}'",
    "    if not play:",
    "        return analyze_text(source, file='loop.q1asm', sequencer_id='s')",
    "    session = PlaybackSession({'s': ('loop.q1asm', source)})",
    "    session.run(budget_ms=1000, max_steps=60)",
    "    return session.snapshot()",
    "cases = {mode: dict(nop=run('nop', play), zero=run('wait 0', play), pure=run('nop', play, tail=''))",
    "    for mode, play in [('analysis', False), ('play', True)]}",
    "legacy = run('nop')",
    "for event in legacy['events']:",
    "    if event['kind'] == 'q1_issue': event['meta'].pop('emits_rt_packet', None)",
    "cases['analysis']['legacy'] = legacy",
    "print(json.dumps({mode: {name: dict(ir=ir, html=render_html(ir)) for name, ir in group.items()}",
    "    for mode, group in cases.items()}))",
  ].join("\n")], { cwd: repo, encoding: "utf8", timeout: 10000, maxBuffer: 8 * 1024 * 1024,
    env: { ...process.env, PYTHONPATH: path.join(repo, "src") } }));
}

async function open(page, data, viewer) {
  await page.goto("about:blank");
  const html = viewer === "standalone" ? data.html : data.html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi,
    (tag, attributes) => /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await page.setContent(html);
  if (viewer === "standalone") {
    await page.locator("#q1timeline-analysis-details > summary").click();
    return;
  }
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.evaluate(() => {
    let saved = { expandedQ1IssueSequencers: ["s"] };
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
  await page.locator("#q1timeline-analysis-details > summary").click();
}

function view(page, viewer) {
  const svg = page.locator("#timeline-root > svg");
  const standalone = viewer === "standalone";
  const event = (id) => standalone ? svg.locator(`g[data-event-id="${id}"]`) : page.locator(`.timeline-block[data-block-id="${id}"]`);
  const loop = (id) => standalone ? svg.locator(`.kind-loop_block[data-event-id="${id}"]`) :
    page.locator(`.timeline-loop-range[data-event-id="${id}"]`);
  const field = (name) => page.locator("#event-inspector-fields").getByText(name, { exact: true }).locator("xpath=following-sibling::dd[1]");
  const mode = (name) => page.getByRole("button", { name: standalone ? name : `${name} view`, exact: true }).click();
  async function extent(end) {
    if (standalone) {
      await expect(svg).toHaveAttribute("data-time-min", "0");
      await expect(svg).toHaveAttribute("data-time-max", String(end));
    } else {
      await expect(page.locator("#q1timeline-time-window")).toHaveAttribute("title", new RegExp(`: 0\\.\\.${end} ns$`));
    }
  }
  return { svg, event, loop, field, mode, extent };
}

for (const viewer of ["standalone", "extension"]) {
  test(`${viewer} shows the exact Q1-only loop and its source in Debug`, async ({ page }) => {
    page.setDefaultTimeout(5000);
    const timeline = view(page, viewer);
    for (const [execution, { nop }] of Object.entries(fixtures())) {
      const loop = nop.ir.events.find((event) => event.kind === "loop_block");
      const first = loop.meta.first_iteration_event_ids[0];
      const wait = nop.ir.events.find((event) => event.kind === "wait");
      expect([wait.t0.value, wait.t1.value, loop.meta.count]).toEqual([0, 12, 3]);
      await open(page, nop, viewer);

      await timeline.extent(12);
      await expect(timeline.loop(loop.id)).toHaveCount(0);
      await expect(timeline.event(first)).toBeHidden();
      await expect(timeline.event(wait.id)).toBeVisible();

      await timeline.mode("Debug");
      const annotation = timeline.loop(loop.id);
      await expect(annotation).toBeVisible();
      await annotation.press("Enter");
      await expect(timeline.field("Source")).toHaveText("loop.q1asm:3");
      if (viewer === "standalone") {
        await expect(timeline.field("Loop iterations")).toHaveText("1/3");
        await page.getByRole("button", { name: "Open first iteration" }).click();
        await expect(timeline.event(first)).toHaveClass(/\bis-selected\b/);
      } else {
        await expect(annotation).toContainText(execution === "play" ? "×3" : "x3");
        await page.keyboard.press("Escape");
        await page.evaluate((id) => window.postMessage({ type: "highlightEventIds", highlightEventIds: [id] }, "*"), first);
        await expect(timeline.event(first)).toBeVisible();
        await timeline.event(first).press("Enter");
        await expect(timeline.event(first)).toHaveClass(/\bis-selected\b/);
        await expect.poll(() => page.evaluate(() => window.messages.filter((message) => message.type === "eventClick").at(-1)?.eventId)).toBe(first);
      }
      await expect(timeline.field("Source")).toHaveText("loop.q1asm:3");

      await timeline.mode("Normal");
      await timeline.extent(12);
      await expect(timeline.loop(loop.id)).toHaveCount(0);
      await expect(timeline.event(first)).toBeHidden();
      if (viewer === "extension") {
        await page.getByRole("button", { name: "Instruction", exact: true }).click();
        await expect(timeline.loop(loop.id)).toHaveCount(0);
      }
    }
  });

  test(`${viewer} preserves zero-duration RT loops and legacy loop evidence`, async ({ page }) => {
    const timeline = view(page, viewer);
    for (const { zero, pure } of Object.values(fixtures())) {
      const loop = zero.ir.events.find((event) => event.kind === "loop_block");
      const packet = zero.ir.events.find((event) => event.kind === "wait" && event.meta.rt_packet_id);
      expect(packet.duration.value).toBe(0);
      expect(loop.meta.period.value).toBe(4);
      await open(page, zero, viewer);
      const annotation = viewer === "standalone" ? timeline.svg.locator(`.loop-bracket-start[data-event-id="${loop.id}"]`) : timeline.loop(loop.id);
      await expect(annotation).toBeVisible();

      const pureLoop = pure.ir.events.find((event) => event.kind === "loop_block");
      await open(page, pure, viewer);
      await timeline.extent(1);
      await expect(timeline.loop(pureLoop.id)).toHaveCount(0);
      await timeline.mode("Debug");
      await expect(timeline.loop(pureLoop.id)).toBeVisible();
    }

    const { legacy } = fixtures().analysis;
    await open(page, legacy, viewer);
    if (viewer === "standalone") {
      await expect(timeline.svg.locator(".kind-loop_iteration_preview")).toBeVisible();
    } else {
      const loop = legacy.ir.events.find((event) => event.kind === "loop_block");
      await expect(timeline.loop(loop.id)).toBeVisible();
    }
  });
}
