const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect } = require("./helpers");

const assets = process.env.Q1LENS_BROWSER_ASSETS || path.resolve(__dirname, "../../src");

function fixtures() {
  const repo = path.resolve(__dirname, "../../..");
  return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from copy import deepcopy",
    "from q1timeline.analysis.api import analyze_text",
    "from q1timeline.render.html import render_html",
    "def run(op='play_pulse 0,4', alignment='none'):",
    "    return analyze_text(f'wait 8\\n{op}\\nstop', file='unverified.q1asm', sequencer_id='s', alignment_mode=alignment)",
    "pulse = run()",
    "invalid = deepcopy(pulse)",
    "next(e for e in invalid['events'] if e['kind']=='unknown_region')['meta']['planned_duration_ns'] = -4",
    "cases = dict(pulse=pulse, shifted=run(alignment='manual:20'), request=run('sw_req 1'), invalid=invalid)",
    "print(json.dumps({name: dict(ir=ir, html=render_html(ir)) for name, ir in cases.items()}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
}

async function open(page, data, viewer) {
  await page.goto("about:blank");
  await page.setContent(viewer === "standalone" ? data.html : data.html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi,
    (tag, attributes) => /type=["']application\/json["']/i.test(attributes) ? tag : ""));
  if (viewer === "standalone") return;
  await page.evaluate(() => {
    let saved = {};
    window.acquireVsCodeApi = () => ({postMessage() {}, getState: () => saved, setState: (value) => { saved = value; }});
  });
  for (const file of ["shared/timeline/renderer.css", "q1timeline/media/timeline.css", "q1timeline/media/hardwareView.css"]) {
    await page.addStyleTag({path: path.join(assets, file)});
  }
  for (const file of ["shared/timeline/instructionColors.js", "shared/timeline/renderer.js", "q1timeline/media/timelineAdapter.js",
    "q1timeline/media/hardwareState.js", "q1timeline/media/hardwareView.js", "q1timeline/media/timeline.js"]) {
    await page.addScriptTag({path: path.join(assets, file)});
  }
}

test("unverified RT commands expose encoded duration without completing the timeline", async ({page}) => {
  const cases = fixtures();
  const fields = page.locator("#event-inspector-fields");
  const field = (name) => fields.getByText(name, {exact: true}).locator("xpath=following-sibling::dd[1]");
  for (const [name, offset, planned] of [["pulse", 0, 4], ["shifted", 20, 4], ["request", 0, undefined], ["invalid", 0, -4]]) {
    const data = cases[name];
    const event = data.ir.events.find((item) => item.kind === "unknown_region");
    expect(event).toMatchObject({t0: {value: 8}, t1: null, duration: {kind: "unknown"}, confidence: "unknown",
      meta: {reason: "unverified_instruction", aligned_t0: 8 + offset}});
    expect(event.meta.planned_duration_ns).toBe(planned);

    for (const viewer of ["standalone", "extension"]) {
      await open(page, data, viewer);
      const svg = page.locator("#timeline-root > svg");
      const window = page.locator("#q1timeline-time-window");
      const action = page.locator(`.diagnostic a[data-related-event-id="${event.id}"]`);
      await action.click();
      await expect(field("Source")).toHaveText("unverified.q1asm:2");
      await expect(field("Time range")).toHaveText(`${8 + offset} ->`);
      if (planned === 4) await expect(field("Planned duration")).toHaveText("4 ns");
      else await expect(field("Planned duration")).toHaveCount(0);

      if (viewer === "standalone") {
        await expect(svg).toHaveAttribute("data-time-min", String(offset));
        await expect(svg).toHaveAttribute("data-time-max", String(8 + offset));
      } else {
        await expect(window).toHaveAttribute("title", `X (aligned): ${offset}..${8 + offset} ns`);
        if (offset) {
          await page.getByRole("button", {name: "Local time basis"}).click();
          await action.click();
          await expect(field("Time range")).toHaveText("8 ->");
          await expect(field("Planned duration")).toHaveText("4 ns");
          await expect(window).toHaveAttribute("title", "X (local): 0..8 ns");
        }
      }
    }
  }
});
