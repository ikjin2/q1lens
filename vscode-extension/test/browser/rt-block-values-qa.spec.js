const path = require("node:path");
const { test, expect } = require("./helpers");

const assets = path.resolve(__dirname, "../../src");
const value = (number) => ({kind: "concrete", value: number, display: String(number)});
function event(id, kind, start, end, meta = {}, extra = {}) {
  return {id, kind, label: kind, sequencer_id: "seq", lane: kind === "q1_issue" ? "q1.issue" : `rt.${kind}`,
    t0: value(start), t1: value(end), duration: value(end - start), confidence: "exact",
    source: {file: "values.q1asm", line: 1, raw: kind}, meta, ...extra};
}
function fixture() {
  const snapshot = {R0: value(1), R1: value(8), R63: value(999)};
  const command = (id, kind, issueTime, start, end, reads, registers, applied = {}) => [
    event(`${id}-issue`, "q1_issue", issueTime, issueTime + 4, {op: kind, rt_packet_id: id,
      register_reads: reads, register_snapshot: registers}),
    event(id, kind, start, end, {rt_packet_id: id, q1_issue_event_id: `${id}-issue`, applied_state: applied}),
  ];
  return {sequencers: [{id: "seq"}], events: [
    ...command("initial", "upd_param", 0, 0, 4, {}, snapshot, {frequency: 10, awg_gain: [1, 2]}),
    ...command("wait", "wait", 4, 4, 104, {R1: value(8)}, snapshot),
    ...command("play", "play", 12, 104, 108, {R0: value(2), R1: value(8)}, {...snapshot, R0: value(2)},
      {frequency: 20, awg_gain: [1, 2]}),
    event("pending", "latched_state_pending", 20, 20, {field: "frequency", value: 30}, {lane: "state.pending"}),
    event("warning", "underflow_warning", 104, 104, {rt_packet_id: "play", slack_ns: -4}, {lane: "debug.underflow"}),
  ]};
}

async function mount(page, ir = fixture(), mode = "normal") {
  await page.goto("about:blank");
  await page.setContent(`<!doctype html><html><head><style>
    :root{--vscode-foreground:#d4d4d4;--vscode-descriptionForeground:#aaa;--vscode-editor-background:#1e1e1e;
    --vscode-editor-foreground:#d4d4d4;--vscode-editorWidget-background:#252526;--vscode-sideBar-background:#252526;
    --vscode-charts-green:#89d185;--vscode-charts-blue:#75beff;--vscode-charts-yellow:#cca700;
    --vscode-focusBorder:#007fd4;--vscode-panel-border:#404040;--vscode-button-secondaryHoverBackground:#333}
    body{margin:16px;background:#1e1e1e;color:#d4d4d4;font:13px sans-serif}
    </style></head><body class="mode-${mode} vscode-dark" data-mode="${mode}"><main id="timeline-root"></main></body></html>`);
  await page.evaluate(({ir, mode}) => {
    window.messages = [];
    let saved = {expandedQ1IssueSequencers: ["seq"]};
    window.acquireVsCodeApi = () => ({postMessage: (message) => window.messages.push(message),
      getState: () => saved, setState: (state) => {saved = state;}});
    const data = document.createElement("script");
    data.id = "timeline-ir"; data.type = "application/json"; data.textContent = JSON.stringify(ir); document.body.append(data);
    const state = document.createElement("meta"); state.id = "q1timeline-webview-state";
    state.dataset.state = JSON.stringify({viewMode: mode, executionState: ir.playback}); document.head.append(state);
  }, {ir, mode});
  for (const file of ["shared/timeline/renderer.css", "q1timeline/media/timeline.css", "q1timeline/media/hardwareView.css"]) {
    await page.addStyleTag({path: path.join(assets, file)});
  }
  for (const file of ["shared/timeline/instructionColors.js", "shared/timeline/renderer.js", "q1timeline/media/timelineAdapter.js",
    "q1timeline/media/hardwareState.js", "q1timeline/media/hardwareView.js", "q1timeline/media/timeline.js"]) {
    await page.addScriptTag({path: path.join(assets, file)});
  }
}

test("RT detail click and keyboard actions notify source selection once", async ({page}) => {
  await mount(page);
  const block = page.locator('.timeline-block[data-event-id="play"]');
  const panel = page.getByRole("dialog", {name: "RT block values", exact: true});
  await block.click();
  await expect(panel).toBeVisible();
  expect(await page.evaluate(() => window.messages.filter((message) => message.type === "eventClick"))).toEqual([
    {type: "eventClick", eventId: "play", preserveFocus: true},
  ]);
  await panel.press("Escape");
  await block.press("Enter");
  await expect(panel).toBeVisible();
  expect(await page.evaluate(() => window.messages.filter((message) => message.type === "eventClick"))).toHaveLength(2);
  await page.getByRole("button", {name: "Close RT block values", exact: true}).click();
  await expect(panel).toHaveCount(0);
  await expect(block).toHaveAttribute("aria-expanded", "false");
  await expect(block).toBeFocused();
});

test("RT details track source highlight messages without notifying another source click", async ({page}) => {
  await mount(page);
  const block = page.locator('.timeline-block[data-event-id="play"]');
  await block.click();
  await page.evaluate(() => window.postMessage({type: "highlightEventIds", highlightEventIds: ["play"]}, "*"));
  const panel = page.getByRole("dialog", {name: "RT block values", exact: true});
  await expect(panel).toBeVisible();
  await expect(block).toHaveAttribute("aria-expanded", "true");
  await expect(panel.locator('[data-value-name="R0"]')).toHaveClass("is-changed");
  expect(await page.evaluate(() => window.messages.filter((message) => message.type === "eventClick"))).toHaveLength(1);
});

test("outside click closes RT details and next block replaces values", async ({page}) => {
  await mount(page);
  const play = page.locator('.timeline-block[data-event-id="play"]');
  const wait = page.locator('.timeline-block[data-event-id="wait"]');
  const panel = page.getByRole("dialog", {name: "RT block values", exact: true});
  await play.click();
  await expect(panel).toBeVisible();
  await wait.click();
  await expect(panel).toBeVisible();
  await expect(play).toHaveAttribute("aria-expanded", "false");
  await expect(wait).toHaveAttribute("aria-expanded", "true");
  await expect(panel.getByRole("table", {name: "Registers used", exact: true}).locator("tr")).toHaveCount(1);
  await expect(panel.locator('[data-value-name="R0"]')).toHaveCount(0);
  await page.locator("body").click({position: {x: 1, y: 1}});
  await expect(panel).toHaveCount(0);
  await expect(wait).toHaveAttribute("aria-expanded", "false");
});

test("RT details survive external mode rerender and stay anchored within a narrow viewport", async ({page}) => {
  await page.setViewportSize({width: 640, height: 480});
  await mount(page);
  await page.locator('.timeline-block[data-event-id="play"]').click();
  await page.evaluate(() => window.postMessage({type: "setViewMode", mode: "debug"}, "*"));
  const panel = page.getByRole("dialog", {name: "RT block values", exact: true});
  await expect(panel).toBeVisible();
  await expect(page.locator('.timeline-block[data-event-id="play"]')).toHaveAttribute("aria-expanded", "true");
  const box = await panel.boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(640);
  expect(box.y + box.height).toBeLessThanOrEqual(480);
});

test("instruction wheel zoom repositions expanded RT details with the block", async ({page}) => {
  await mount(page);
  await page.getByRole("button", {name: "Instruction", exact: true}).click();
  const block = page.locator('.timeline-block[data-event-id="wait"]');
  await block.click();
  const panel = page.getByRole("dialog", {name: "RT block values", exact: true});
  const before = await panel.boundingBox();
  await page.locator(".shared-timeline-stage").dispatchEvent("wheel", {deltaY: -200, clientX: 600, clientY: 200});
  await expect(panel).toBeVisible();
  const after = await panel.boundingBox();
  const anchor = await block.boundingBox();
  const arrowX = await panel.evaluate((node) => Number.parseFloat(node.style.getPropertyValue("--q1timeline-control-anchor-x")));
  expect(Math.abs(after.x + arrowX - (anchor.x + anchor.width / 2))).toBeLessThanOrEqual(2);
  expect(after.x).not.toBe(before.x);
});
