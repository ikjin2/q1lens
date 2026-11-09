const path = require("node:path");
const fs = require("node:fs");
const { execFileSync } = require("node:child_process");
const { test, expect } = require("./helpers");

const repo = path.resolve(__dirname, "../../..");
const assets = process.env.Q1LENS_BROWSER_ASSETS || path.join(repo, "vscode-extension/src");
function fixture(project = "examples/acquisition-window-prototype/q1timeline.yml") {
  return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import contextlib, io, json, tempfile, sys", "from pathlib import Path",
    "from q1timeline.cli import main", "from q1timeline.render.html import render_html",
    "with tempfile.TemporaryDirectory() as directory:",
    "    output = Path(directory) / 'timeline.json'",
    "    with contextlib.redirect_stdout(io.StringIO()):",
    "        result = main(['analyze', '--project', sys.argv[1], '--out', str(output), '--no-render'])",
    "    assert result == 0", "    ir = json.loads(output.read_text(encoding='utf-8'))",
    "print(json.dumps({'ir': ir, 'html': render_html(ir)}))",
  ].join("\n"), project], { cwd: repo, encoding: "utf8", env: { ...process.env, PYTHONPATH: path.join(repo, "src") } }));
}

async function mount(page, data) {
  await page.goto("about:blank");
  await page.setContent(data.html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : ""));
  await page.evaluate(ir => {
    window.messages = [];
    let saved = { analysisDetailsOpen: false, timelineWaitsCollapsed: false };
    window.acquireVsCodeApi = () => ({ postMessage: message => window.messages.push(message),
      getState: () => saved, setState: value => { saved = value; } });
    document.body.classList.add("vscode-dark");
    document.getElementById("timeline-ir").textContent = JSON.stringify(ir);
    const state = document.createElement("meta");
    state.id = "q1timeline-webview-state";
    state.dataset.state = JSON.stringify({ viewMode: "normal" });
    document.head.append(state);
  }, data.ir);
  await page.addStyleTag({ content: `:root {
    --vscode-font-family: "Segoe UI", sans-serif; --vscode-font-size: 13px;
    --vscode-foreground: #d4d4d4; --vscode-editor-foreground: #d4d4d4;
    --vscode-descriptionForeground: #aaa; --vscode-editor-background: #1e1e1e;
    --vscode-editorWidget-background: #252526; --vscode-sideBar-background: #252526;
    --vscode-panel-border: #404040; --vscode-focusBorder: #007fd4;
    --vscode-charts-green: #89d185; --vscode-editorWarning-foreground: #cca700;
    --vscode-button-secondaryBackground: #333; --vscode-button-secondaryForeground: #ddd;
  } body { color: var(--vscode-foreground); background: var(--vscode-editor-background); }` });
  for (const file of ["shared/timeline/renderer.css", "q1timeline/media/timeline.css", "q1timeline/media/hardwareView.css"]) {
    await page.addStyleTag({ path: path.join(assets, file) });
  }
  for (const file of ["shared/timeline/instructionColors.js", "shared/timeline/renderer.js", "q1timeline/media/timelineAdapter.js",
    "q1timeline/media/hardwareState.js", "q1timeline/media/hardwareView.js", "q1timeline/media/timeline.js"]) {
    await page.addScriptTag({ path: path.join(assets, file) });
  }
}

test("real extension assets show conditional ACQ rows, original source selection and integration details", async ({ page }) => {
  const data = fixture();
  await mount(page, data);
  const rows = page.locator('[data-lane-role="acquisition"]');
  await expect(rows).toHaveCount(3);
  await expect(page.locator('[data-lane="sequencer:readout:acquisition"] .timeline-block')).toHaveCount(3);
  const acquire = data.ir.events.find(event => event.kind === "acquire" && event.sequencer_id === "readout");
  const band = page.locator(`[data-block-id="${acquire.id}:acquisition-window"]`);
  await expect(band).toHaveClass(/acquisition-interrupted/);
  expect(await band.locator(".acquisition-bracket").evaluate(node => getComputedStyle(node, "::after").backgroundColor)).toBe("rgb(204, 167, 0)");
  await expect(band).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  await expect(band).toHaveAttribute("data-acquisition-mode", "integration");
  await expect(band.locator(".acquisition-duration")).toHaveText("600 ns");
  expect((await rows.first().boundingBox()).height).toBeLessThanOrEqual(24);
  expect((await page.locator('[data-lane="sequencer:readout"]').boundingBox()).height).toBeLessThanOrEqual(32);
  await band.click();
  await expect(page.locator("#event-inspector-fields")).toContainText("600 ns / 1000 ns configured");
  await expect(page.locator("#event-inspector-fields")).toContainText("Interrupted by next Acquire");
  await expect(page.locator("#event-inspector-fields")).toContainText("4 ns");
  expect(await page.evaluate(() => window.messages.some(message => message.type === "eventClick"))).toBe(true);
  expect(await page.evaluate(id => window.messages.filter(message => message.type === "eventClick").at(-1).eventId === id, acquire.id)).toBe(true);
  await expect(band).not.toHaveAttribute("aria-haspopup", "dialog");
  const output = path.join(repo, "output/playwright");
  fs.mkdirSync(output, { recursive: true });
  await page.screenshot({ path: path.join(output, "vscode-acquisition.png"), fullPage: true });
  await page.locator(".shared-timeline-stage").screenshot({ path: path.join(output, "vscode-acquisition-brackets.png") });
  await page.getByRole("button", { name: "Instruction", exact: true }).click();
  await expect(rows).toHaveCount(0);
  await page.getByRole("button", { name: "Time scale", exact: true }).click();
  await expect(rows).toHaveCount(3);
  await page.getByRole("button", { name: "Debug view", exact: true }).click();
  await expect(rows).toHaveCount(3);
  await page.getByRole("button", { name: "Normal view", exact: true }).click();
  await expect(rows).toHaveCount(3);
  const assumed = page.locator('[data-lane="sequencer:unknown:acquisition"] .timeline-block').first();
  await expect(assumed).toHaveClass(/acquisition-assumed/);
  await expect(assumed.locator(".acquisition-duration")).toHaveText("≈ 4 ns");
  await expect(assumed.locator(".acquisition-bracket")).toHaveCSS("border-top-style", "dashed");
  await expect(assumed).toHaveAttribute("aria-label", /4 ns \(assumed from RT duration\)/);
  await assumed.click();
  await expect(page.locator("#event-inspector-fields")).toContainText("4 ns (assumed)");
  await expect(page.locator("#event-inspector-fields")).toContainText("Assumed from RT duration");
});

test("saved integration settings and feedback setup alone do not create an ACQ row", async ({ page }) => {
  const data = fixture();
  data.ir.events = data.ir.events.filter(event => event.kind !== "acquire");
  await mount(page, data);
  await expect(page.locator('[data-lane-role="acquisition"]')).toHaveCount(0);
  await page.getByRole("button", { name: "Debug view", exact: true }).click();
  await expect(page.locator('[data-lane-role="acquisition"]')).toHaveCount(0);
});

test("acquisition duration moves to its fold tab and returns to the bracket on expansion", async ({ page }) => {
  const data = fixture("examples/time-fold-demo/q1timeline.yml");
  await mount(page, data);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page.locator("#q1timeline-wait-fold-button")).toHaveAttribute("aria-pressed", "true");
  const tabs = page.getByRole("button", { name: /^Expand 1 µs acquisition across all sequencers/ });
  await expect(tabs).toHaveCount(3);
  const windows = page.locator('.acquisition-window[data-acquisition-mode="integration"]');
  await expect(windows).toHaveCount(3);
  for (const window of await windows.all()) {
    await expect(window).toHaveClass(/acquisition-time-folded/);
    await expect(window.locator(".acquisition-duration")).toBeHidden();
    await expect(window.locator(".acquisition-mode-icon svg")).toBeVisible();
  }
  const first = tabs.first();
  await expect(first).toHaveText("1 µs");
  await expect(first).toHaveAttribute("title", /Folded 964 ns.*Acquisition duration: 1 µs/);
  await first.focus();
  await page.keyboard.press("Enter");
  const expanded = page.getByRole("button", { name: /^Collapse 1 µs acquisition across all sequencers/ });
  await expect(expanded).toHaveCount(1);
  await expect(expanded).toBeFocused();
  await expect(windows.first().locator(".acquisition-duration")).toBeVisible();
  await expect(windows.first().locator(".acquisition-duration")).toHaveText("1 μs");
  await expect(page.getByRole("button", { name: /^Expand 8 µs interval/ })).toHaveCount(1);
  await expect(page.getByRole("button", { name: /^Expand 4 µs interval/ })).toHaveCount(1);
  await page.keyboard.press("Space");
  await expect(tabs).toHaveCount(3);
  await expect(windows.first().locator(".acquisition-duration")).toBeHidden();
  expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(data.ir);
  await page.locator(".shared-timeline-stage").screenshot({ path: path.join(repo, "output/playwright/time-fold-acquisition.png") });
});

test("folds keep label width, leave right-side space and fit without horizontal scrolling", async ({ page }) => {
  const data = fixture("examples/time-fold-demo/q1timeline.yml");
  await mount(page, data);
  await page.emulateMedia({ reducedMotion: "reduce" });
  const stage = page.locator(".shared-timeline-stage");
  const ruler = page.locator(".ruler");
  const initialWidth = (await ruler.boundingBox()).width;
  expect(initialWidth).toBeLessThan((await stage.boundingBox()).width / 2);
  await expect(page.locator(".q1timeline-rt-disclosure")).toHaveCount(0);
  const names = page.locator('[data-lane="sequencer:readout"] .q1timeline-command-name .block-label');
  await expect(names).toHaveText(["wait_sync", "wait", "acquire", "wait", "acquire", "wait", "acquire", "wait", "stop"]);
  const nameBounds = await names.evaluateAll(nodes => nodes.map(node => {
    const bounds = node.getBoundingClientRect();
    return { left: bounds.left, right: bounds.right, width: bounds.width, visible: getComputedStyle(node).display !== "none" };
  }));
  expect(nameBounds.every(bounds => bounds.visible && bounds.width > 10)).toBe(true);
  for (let index = 1; index < nameBounds.length; index += 1) expect(nameBounds[index - 1].right).toBeLessThanOrEqual(nameBounds[index].left + 1);
  const tickBounds = await ruler.locator(".tick span").evaluateAll(nodes => nodes.map(node => {
    const bounds = node.getBoundingClientRect(); return { left: bounds.left, right: bounds.right };
  }));
  for (let index = 1; index < tickBounds.length; index += 1) expect(tickBounds[index - 1].right + 4).toBeLessThanOrEqual(tickBounds[index].left);
  for (const width of [1440, 1000, 640, 1800]) {
    await page.setViewportSize({ width, height: 1000 });
    await expect.poll(() => stage.evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
    expect(await page.locator("#timeline-root").evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
    for (const marker of await ruler.locator(".q1timeline-wait-fold:not(.is-expanded)").all()) {
      const band = await marker.boundingBox();
      const tab = await marker.locator("button").boundingBox();
      expect(band.width).toBeLessThanOrEqual(await marker.locator("button").evaluate(node => node.classList.contains("is-crowded")) ? 100 : tab.width + 16);
      expect(tab.x).toBeGreaterThanOrEqual((await ruler.boundingBox()).x);
      expect(tab.x + tab.width).toBeLessThanOrEqual((await ruler.boundingBox()).x + (await ruler.boundingBox()).width + 1);
    }
  }
  await page.getByRole("button", { name: /^Expand 8 µs interval/ }).click();
  expect((await ruler.boundingBox()).width).toBeGreaterThan(initialWidth + 500);
  const wait = data.ir.events.find(event => event.kind === "wait" && event.source?.raw.startsWith("wait 9028"));
  const block = page.locator(`.timeline-block[data-event-id="${wait.id}"]`);
  await block.click();
  await expect(block).toHaveAttribute("aria-expanded", "true");
  await page.keyboard.press("Escape");
  await page.locator("#q1timeline-wait-fold-button").click();
  await expect(page.locator(".ruler .q1timeline-fold-toggle[aria-expanded='false']")).toHaveCount(0);
  expect(await stage.evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
  await page.locator("#timeline-root").evaluate(node => { node.style.width = "650px"; });
  await expect.poll(() => stage.evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
  await page.locator("#timeline-root").evaluate(node => { node.style.width = ""; });
  await expect.poll(() => stage.evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
  const before = await page.locator("#q1timeline-time-window").getAttribute("title");
  await page.locator("#q1timeline-zoom-in-button").click();
  expect(await page.locator("#q1timeline-time-window").getAttribute("title")).not.toBe(before);
  await page.locator("#q1timeline-fit-button").click();
  await expect(page.locator("#q1timeline-time-window")).toHaveAttribute("title", before);
  await page.locator("#q1timeline-wait-fold-button").click();
  await page.locator("#q1timeline-zoom-in-button").click();
  const plot = await ruler.boundingBox();
  const point = { x: plot.x + plot.width / 2, y: plot.y + 3 };
  await page.evaluate(() => document.addEventListener("pointerdown", event => { window.testPointerId = event.pointerId; }, { once: true }));
  await page.mouse.move(point.x, point.y);
  await page.mouse.down();
  await page.mouse.move(point.x - 8, point.y);
  const panned = await page.evaluate(() => window.acquireVsCodeApi().getState().timelineWindow);
  await page.evaluate(point => document.dispatchEvent(new PointerEvent("pointermove", {
    clientX: point.x - 8, clientY: point.y, pointerId: window.testPointerId,
  })), point);
  expect(await page.evaluate(() => window.acquireVsCodeApi().getState().timelineWindow)).toEqual(panned);
  await page.mouse.up();
  await page.locator("#q1timeline-fit-button").click();
  fs.mkdirSync(path.join(repo, "output/playwright"), { recursive: true });
  await stage.screenshot({ path: path.join(repo, "output/playwright/time-fold-compact.png") });
});

test("scope and integration share one compact row with distinct icons and independent durations", async ({ page }) => {
  const data = fixture("examples/acquisition-window-prototype/scope/q1timeline.yml");
  await mount(page, data);
  const row = page.locator('[data-lane-role="acquisition"]');
  await expect(row).toHaveCount(1);
  await expect(row).toHaveClass(/has-scope/);
  const scope = row.locator('[data-acquisition-mode="scope"]');
  const integration = row.locator('[data-acquisition-mode="integration"]');
  await expect(scope).toHaveCount(1);
  await expect(integration).toHaveCount(1);
  await expect(scope.locator(".acquisition-duration")).toHaveText("16.384 μs");
  expect((await scope.boundingBox()).y - (await integration.boundingBox()).y).toBe(20);
  expect((await row.boundingBox()).height).toBeLessThanOrEqual(44);
  await expect(scope.locator(".acquisition-mode-icon svg")).toBeVisible();
  await scope.click();
  await expect(page.locator("#event-inspector-fields")).toContainText("1000 ns / 1000 ns configured");
  await expect(page.locator("#event-inspector-fields")).toContainText("16384 ns / 16384 ns configured");
  const acquire = data.ir.events.find(event => event.kind === "acquire");
  expect(await page.evaluate(id => window.messages.filter(message => message.type === "eventClick").at(-1).eventId === id, acquire.id)).toBe(true);
  await page.screenshot({ path: path.join(repo, "output/playwright/vscode-acquisition-scope.png"), fullPage: true });
  await page.locator(".shared-timeline-stage").screenshot({ path: path.join(repo, "output/playwright/vscode-acquisition-scope-brackets.png") });
  await page.getByRole("button", { name: "Instruction", exact: true }).click();
  await expect(row).toHaveCount(0);
});

test("unknown weighted acquisition keeps its mode icon without inventing a bracket end", async ({ page }) => {
  const data = fixture();
  const acquire = data.ir.events.find(event => event.kind === "acquire" && event.sequencer_id === "readout");
  acquire.meta.op = "acquire_weighed";
  acquire.meta.acquisition_window = { start_ns: 100, end_ns: null, status: "unknown" };
  await mount(page, data);
  const marker = page.locator(`[data-block-id="${acquire.id}:acquisition-window"]`);
  await expect(marker).toHaveAttribute("data-acquisition-mode", "weighted");
  await expect(marker.locator(".acquisition-mode-icon svg")).toBeVisible();
  await expect(marker.locator(".acquisition-duration")).toHaveText("?");
  await marker.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#event-inspector-fields")).toContainText("Weighted integration");
  await expect(page.locator("#event-inspector-fields")).toContainText("Unknown integration length");
});

test("time fold tabs stay within both ends of a narrow ruler", async ({ page }) => {
  await page.setViewportSize({width: 640, height: 800});
  await mount(page, fixture("examples/acquisition-window-prototype/scope/q1timeline.yml"));
  const ruler = page.locator('.ruler');
  const toggles = ruler.locator('.q1timeline-fold-toggle');
  expect(await toggles.count()).toBeGreaterThanOrEqual(2);
  for (const width of [640, 1000, 560]) {
    await page.setViewportSize({width, height: 800});
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const bounds = await ruler.boundingBox();
    for (const toggle of await toggles.all()) {
      const box = await toggle.boundingBox();
      expect(box.x).toBeGreaterThanOrEqual(bounds.x);
      expect(box.x + box.width).toBeLessThanOrEqual(bounds.x + bounds.width);
      if (await toggle.evaluate(node => node.classList.contains("is-crowded"))) {
        await expect(toggle.locator(".q1timeline-fold-icon")).toBeVisible();
        await expect(toggle).toHaveAttribute("aria-label", /\d[\d.]* (ns|µs|ms)/);
      } else expect(await toggle.innerText()).toMatch(/^\d[\d.]* (ns|µs|ms)$/);
    }
  }
});
