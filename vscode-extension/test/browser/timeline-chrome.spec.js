const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect } = require("./helpers");

const repo = path.resolve(__dirname, "../../..");
const assets = path.join(repo, "vscode-extension/src");
let cached;

// One small program: an overwritten register, unreachable code, a latched frequency applied by upd_param, and a plain wait.
function fixture() {
  if (!cached) {
    cached = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
      "import json",
      "from q1timeline.analysis.api import analyze_text",
      "from q1timeline.render.html import render_html",
      "program = 'move 1,R0\\nmove 2,R0\\nset_freq 4000000\\nupd_param 4\\nwait 8\\nstop\\nnop\\nnop'",
      "ir = analyze_text(program, file='chrome.q1asm', sequencer_id='s')",
      "clean = analyze_text('wait 8\\nstop', file='clean.q1asm', sequencer_id='s')",
      "print(json.dumps({'ir': ir, 'html': render_html(ir), 'clean': {'ir': clean, 'html': render_html(clean)}}))",
    ].join("\n")], { cwd: repo, encoding: "utf8", timeout: 10000, maxBuffer: 8 * 1024 * 1024,
      env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONPATH: path.join(repo, "src") } }));
  }
  return cached;
}

async function mount(page, data, hostState = {}) {
  await page.goto("about:blank");
  await page.setContent(data.html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi,
    (tag, attributes) => /type=["']application\/json["']/i.test(attributes) ? tag : ""));
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.evaluate(({ sequencers, hostState }) => {
    window.messages = [];
    let saved = { expandedQ1IssueSequencers: sequencers.map((item) => item.id) };
    window.acquireVsCodeApi = () => ({ postMessage: (message) => window.messages.push(message),
      getState: () => saved, setState: (value) => { saved = value; } });
    const state = document.createElement("meta");
    state.id = "q1timeline-webview-state";
    state.dataset.state = JSON.stringify({ viewMode: "normal", ...hostState });
    document.head.append(state);
  }, { sequencers: data.ir.sequencers, hostState });
  for (const file of ["shared/timeline/renderer.css", "q1timeline/media/timeline.css", "q1timeline/media/hardwareView.css"]) {
    await page.addStyleTag({ path: path.join(assets, file) });
  }
  for (const file of ["shared/timeline/instructionColors.js", "shared/timeline/renderer.js", "q1timeline/media/timelineAdapter.js",
    "q1timeline/media/hardwareState.js", "q1timeline/media/hardwareView.js", "q1timeline/media/timeline.js"]) {
    await page.addScriptTag({ path: path.join(assets, file) });
  }
}

const sourceMessages = (page) => page.evaluate(() => window.messages.filter((message) => message.type === "sourceClick"));

test("the toolbar groups its controls, counts issues and leaves out what has nothing to show", async ({ page }) => {
  const data = fixture();
  await mount(page, data);
  const toolbar = page.getByRole("toolbar", { name: "Timeline" });
  await expect(toolbar.locator(".q1timeline-toolbar-separator")).toHaveCount(3);
  for (const [group, names] of [["Timeline layout", ["Time scale", "Instruction"]], ["View mode", ["Normal view", "Debug view"]]]) {
    for (const name of names) await expect(toolbar.getByRole("group", { name: group }).getByRole("button", { name, exact: true })).toBeVisible();
  }
  await expect(toolbar.getByRole("button", { name: "Time scale", exact: true })).toHaveText("Time");

  const issues = page.locator("#q1timeline-issue-status");
  await expect(issues).toHaveAttribute("aria-label", "2 warnings");
  await expect(issues.locator('[data-issue-severity="warning"]')).toHaveText("2");
  await expect(issues.locator('[data-issue-severity="error"]')).toHaveCount(0);

  // Inactive state takes no room: no comparison line, no caption above the ruler, no always-on hints.
  await expect(page.locator("#q1timeline-comparison")).toBeHidden();
  await expect(page.locator(".stage-header")).toHaveCount(0);
  await expect(page.locator("#q1timeline-zoom-selection-button")).toBeHidden();
  await expect(page.locator("#q1timeline-auto-update-indicator")).toBeHidden();
  await expect(toolbar.getByRole("button", { name: "Refresh", exact: true })).toHaveAttribute("title", "Refresh · auto-update: onSave");
  await expect(page.locator("#q1timeline-time-window")).toHaveAttribute("aria-label", /^X \(\w+\): -?\d+\.\.\d+ ns$/);

  // Rarely used actions sit behind the overflow menu and stay reachable by name once it is open.
  const reset = toolbar.getByRole("button", { name: "Reset zoom", exact: true });
  await expect(reset).toBeHidden();
  await toolbar.locator("#q1timeline-toolbar-more > summary").click();
  await expect(toolbar.getByRole("button", { name: "Pan left", exact: true })).toBeVisible();
  await reset.click();
  await expect(reset).toBeHidden();
  await toolbar.locator("#q1timeline-toolbar-more > summary").click();
  await page.keyboard.press("Escape");
  await expect(reset).toBeHidden();

  // The one filter lives in the toolbar; the details header that used to repeat it is gone.
  await expect(toolbar.getByRole("searchbox", { name: "Filter source or kind" })).toBeVisible();
  await expect(page.locator("#q1timeline-analysis-details-body > header")).toBeHidden();
  await expect(page.locator("#q1timeline-analysis-details > summary")).toHaveText("Details");

  await mount(page, data.clean);
  await expect(page.locator("#q1timeline-issue-status")).toHaveAttribute("aria-label", "No issues");
  await expect(page.locator('#q1timeline-issue-status [data-issue-severity="none"]')).toBeVisible();
  await expect(page.locator(".diagnostics")).toHaveCount(0);
  await expect(page.locator("#q1timeline-parameters")).toHaveCount(0);
});

test("diagnostics list one row per kind and each line opens its source", async ({ page }) => {
  const data = fixture();
  await mount(page, data);
  const panel = page.getByRole("region", { name: "Diagnostics" });
  const rows = panel.locator(".diagnostic");
  // The defect is listed before the cleanup item.
  await expect(rows.locator(".diagnostic-title")).toHaveText(["Register value is overwritten before use", "Code is unreachable"]);
  const unreachable = panel.locator('.diagnostic[data-diagnostic-category="unreachable_code"]');
  const line = unreachable.getByRole("button", { name: "Open chrome.q1asm:7", exact: true });
  await expect(line).toHaveText("7");
  await line.click();
  await line.press("Enter");
  const file = data.ir.diagnostics.find((item) => item.category === "unreachable_code").source.file;
  expect(await sourceMessages(page)).toEqual(Array(2).fill({ type: "sourceClick", file, line: 7, column: 1 }));
  await page.locator("#q1timeline-issue-status").click();
  await expect(panel).toBeInViewport();
});

test("selecting a block shows its facts on one line without opening the details", async ({ page }) => {
  const data = fixture();
  const strip = page.getByRole("region", { name: "Selected event" });
  const details = page.locator("#q1timeline-analysis-details");
  const popover = page.getByRole("dialog", { name: "RT block values" });
  const event = (kind, ir = data.ir) => ir.events.find((item) => item.kind === kind);

  // With no latched setting in effect and no register read, a wait has nothing for a popover: only the strip appears.
  await mount(page, data.clean);
  await expect(strip).toBeHidden();
  await expect(page.locator("#q1timeline-zoom-event-button")).toBeDisabled();
  const plainWait = page.locator(`.timeline-block[data-event-id="${event("wait", data.clean.ir).id}"]`);
  await plainWait.click();
  await expect(strip.locator(".q1timeline-selection-code")).toHaveText("wait 8");
  await expect(plainWait).not.toHaveAttribute("aria-haspopup", "dialog");
  await expect(popover).toHaveCount(0);

  await mount(page, data);
  await expect(strip).toBeHidden();
  await page.locator(`.timeline-block[data-event-id="${event("wait").id}"]`).click();
  await expect(strip).toBeVisible();
  await expect(strip.locator(".q1timeline-selection-code")).toHaveText("wait 8");
  await expect(strip.locator('[data-selection-fact="Time range"]')).toHaveText("t4 → 12 ns");
  // This wait runs with the latched frequency, so its popover lists that one section and no empty Registers section.
  await expect(popover.locator("summary")).toHaveText(["Latched parameters"]);
  await expect(popover.locator('[data-value-name="frequency"] td')).toHaveText("4000000");
  await page.keyboard.press("Escape");
  await expect(details).not.toHaveAttribute("open", "");
  // Values that match the default are left out; the full list is one click away.
  await expect(strip.locator('[data-selection-fact="Confidence"]')).toHaveCount(0);
  await expect(strip.locator("[data-selection-fact]")).toHaveCount(1);

  await page.evaluate(() => { window.messages = []; });
  await strip.getByRole("button", { name: "Open chrome.q1asm:5", exact: true }).click();
  expect(await sourceMessages(page)).toEqual([{ type: "sourceClick", file: event("wait").source.file, line: 5, column: 1 }]);
  await expect(strip.getByRole("button", { name: "Zoom around selected event", exact: true })).toBeEnabled();
  await strip.getByRole("button", { name: "More", exact: true }).click();
  await expect(details).toHaveAttribute("open", "");
  await expect(page.locator("#event-inspector-fields")).toContainText("Confidence");

  await page.locator(`.timeline-block[data-event-id="${event("upd_param").id}"]`).click();
  await expect(strip.locator(".q1timeline-selection-code")).toHaveText("upd_param 4");
});

test("comparison, unsaved changes and configured inputs appear as compact status only while they apply", async ({ page }) => {
  const data = fixture();
  await mount(page, data, { comparison: { mode: "saved" }, hasUnsavedChanges: true,
    externalInputs: { triggers: [{ id: "shot", address: 1, targets: ["s"], times_ns: [1000] }], overridden: false, canSave: true } });
  const comparison = page.locator("#q1timeline-comparison");
  await expect(comparison).toBeVisible();
  await expect(comparison).toContainText("vs saved baseline");
  await expect(comparison.locator(".timeline-comparison-key")).toHaveText(["added", "changed", "shifted", "removed"]);
  const unsaved = page.locator("#q1timeline-unsaved-banner");
  await expect(unsaved).toHaveText("Unsaved");
  await expect(unsaved).toHaveAttribute("title", "Unsaved changes are not reflected in the current analysis.");
  const inputs = page.locator("#q1timeline-external-inputs-summary");
  await expect(inputs.locator(".q1timeline-toolbar-badge")).toHaveText("≈ 1");
  await expect(inputs).toContainText("≈ Scenario input · shot · address 1 · 1 sequencers · 1000 ns");
  await page.evaluate(() => window.dispatchEvent(new MessageEvent("message", { data: { type: "setComparison", comparison: { mode: "none" } } })));
  await expect(comparison).toBeHidden();
  await page.evaluate(() => window.dispatchEvent(new MessageEvent("message", { data: { type: "setUnsavedChanges", hasUnsavedChanges: false } })));
  await expect(unsaved).toBeHidden();
});
