const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect, analysisDetails } = require("./helpers");

const assets = process.env.Q1LENS_BROWSER_ASSETS || path.resolve(__dirname, "../../src");
const source = { file: "playback.q1asm", line: 2, raw: "jl @done" };
const event = (id, kind, start, end, meta = {}) => ({ id, kind, label: meta.op || kind, sequencer_id: "seq",
  lane: kind === "q1_issue" ? "q1.issue" : "rt.control", t0: {kind: "concrete", value: start}, t1: {kind: "concrete", value: end}, confidence: "exact", source, meta });

async function mount(page, status = "completed", actualIr, initialState = {}, hostState = {}, actualHtml) {
  await page.emulateMedia({reducedMotion: "reduce"});
  await page.goto("about:blank");
  await page.setContent(actualHtml || '<!doctype html><html><head><style>:root{--vscode-foreground:#d4d4d4;--vscode-descriptionForeground:#999;--vscode-editor-background:#1e1e1e;--vscode-editor-foreground:#d4d4d4;--vscode-editorWidget-background:#252526;--vscode-charts-green:#89d185;--vscode-charts-blue:#75beff;--vscode-focusBorder:#007fd4;--vscode-panel-border:#404040;--vscode-button-secondaryHoverBackground:#333}body{margin:16px;background:#1e1e1e;color:#d4d4d4;font:13px sans-serif}</style></head><body class="mode-debug vscode-dark" data-mode="debug"><main id="timeline-root"></main></body></html>');
  const ir = actualIr || {sequencers: [{id: "seq"}], playback: {status, steps: 7}, events: [
    event("q0", "q1_issue", 0, 4, {op: "wait"}), event("w0", "wait", 0, 100),
    event("q1", "q1_issue", 4, 8, {op: "wait"}), event("w1", "wait", 100, 200),
    event("branch", "q1_issue", 8, 12, {op: "jl", branch_id: "source:branch", playback_branch_id: "branch:2", branch_decision: "default",
      branch_taken: false, assumed_branch_path: "fallthrough", assumption_source: "default", target_label: "done"}),
    event("stop", "stop", 200, 200),
    event("loop", "loop_block", 0, 200, {playback: true, loop_id: "wait_loop", loop_preview_key: "loop", count: 2,
      completed_iterations: 2, complete: true, first_iteration_end: 100, first_iteration_event_ids: ["q0", "w0"], hidden_event_ids: ["q1", "w1"]}),
  ]};
  await page.evaluate(({timelineIr, initialState, hostState}) => {
    window.messages = [];
    let saved = {expandedQ1IssueSequencers: timelineIr.sequencers.map((sequencer) => sequencer.id), ...initialState};
    window.getSavedTimelineState = () => saved;
    window.acquireVsCodeApi = () => ({postMessage: (message) => window.messages.push(message), getState: () => saved, setState: (state) => {saved = state;} });
    document.getElementById("timeline-ir")?.remove();
    const data = document.createElement("script");
    data.id = "timeline-ir"; data.type = "application/json"; data.textContent = JSON.stringify(timelineIr); document.body.append(data);
    const state = document.createElement("meta"); state.id = "q1timeline-webview-state";
    state.dataset.state = JSON.stringify({viewMode: "debug", executionState: timelineIr.playback, ...hostState}); document.head.append(state);
  }, {timelineIr: ir, initialState, hostState});
  await page.addStyleTag({path: path.join(assets, "shared/timeline/renderer.css")});
  await page.addStyleTag({path: path.join(assets, "q1timeline/media/timeline.css")});
  await page.addStyleTag({path: path.join(assets, "q1timeline/media/hardwareView.css")});
  await page.addScriptTag({path: path.join(assets, "shared/timeline/instructionColors.js")});
  await page.addScriptTag({path: path.join(assets, "shared/timeline/renderer.js")});
  await page.addScriptTag({path: path.join(assets, "q1timeline/media/timelineAdapter.js")});
  await page.addScriptTag({path: path.join(assets, "q1timeline/media/hardwareState.js")});
  await page.addScriptTag({path: path.join(assets, "q1timeline/media/hardwareView.js")});
  await page.addScriptTag({path: path.join(assets, "q1timeline/media/timeline.js")});
  if (initialState.analysisDetailsOpen !== false) await analysisDetails(page);
}

function timeFoldFixture() {
  const acquire = (id, start, bin) => event(id, "acquire", start, start + 4, {op: "acquire", acquisition_index: 0, bin_index: bin,
    acquisition_window: {start_ns: start, end_ns: start + 984, configured_duration_ns: 984,
      effective_duration_ns: 984, source: "hardware_parameters", status: "completed"}});
  return {sequencers: [{id: "seq"}], events: [acquire("acq0", 0, 0),
    event("wait0", "wait", 4, 9016, {op: "wait"}), acquire("acq1", 9016, 1),
    event("wait1", "wait", 9020, 10000, {op: "wait"}), event("stop", "stop", 10000, 10000)]};
}

async function expandQ1Sections(page) {
  const sections = page.getByRole("button", {name: /^Expand \d+ Q1 instructions$/});
  while (await sections.count()) await sections.first().press("Enter");
}

test("Normal time scale groups adjacent waits while preserving separate RT actions", async ({page}) => {
  const ir = {sequencers: [{id: "seq"}], events: [
    event("w0", "wait", 0, 100, {op: "wait"}), event("w1", "wait", 100, 300, {op: "wait"}),
    event("p0", "play", 300, 400), event("p1", "play", 400, 500),
    event("trigger", "wait_trigger", 500, 600),
    event("w2", "wait", 600, 700), event("w3", "wait", 704, 800),
  ]};
  await mount(page, "idle", ir, {analysisDetailsOpen: false, timelineWaitsCollapsed: false}, {viewMode: "normal"});
  const group = page.getByRole("button", {name: "Expand 2 wait commands", exact: true});
  const block = (id) => page.locator(`.timeline-block[data-event-id="${id}"]`);
  await expect(group).toHaveText("wait 300 ns × 2");
  for (const id of ["p0", "p1", "trigger", "w2", "w3"]) await expect(block(id)).toBeVisible();
  await expect(block("w0")).toBeHidden();
  await group.press("Enter");
  await expect(block("w0")).toBeVisible();
  await expect(block("w1")).toBeVisible();
  await page.getByRole("button", {name: "Collapse 2 wait commands", exact: true}).click();
  await expect(block("w1")).toBeHidden();
  await page.getByRole("button", {name: "Instruction", exact: true}).click();
  await expect(block("w0")).toBeVisible();
  await expect(block("w1")).toBeVisible();
});

test("Debug folds Q1 sections and keeps stall and error positions visible", async ({page}) => {
  const ir = {sequencers: [{id: "seq"}], events: [
    event("q0", "q1_issue", 0, 4, {op: "nop"}), event("q1", "q1_issue", 4, 8, {op: "nop"}),
    {...event("stall", "q1_stall", 8, 20, {reason: "RT queue full"}), lane: "debug.q1_stall"},
    event("q2", "q1_issue", 20, 24, {op: "move"}), event("q3", "q1_issue", 24, 28, {op: "add"}),
    event("error", "q1_issue", 28, 32, {op: "add"}), event("wait", "wait", 0, 40),
  ], diagnostics: [{severity: "error", message: "Q1 fault", related_events: ["error"]}]};
  await mount(page, "idle", ir, {analysisDetailsOpen: false, timelineWaitsCollapsed: false});
  const groups = page.getByRole("button", {name: "Expand 2 Q1 instructions", exact: true});
  const block = (id) => page.locator(`.timeline-block[data-event-id="${id}"]`);
  await expect(groups).toHaveCount(2);
  await expect(block("stall")).toBeVisible();
  await expect(block("stall")).toHaveAttribute("title", "Q1 stall · 8–20 ns · RT queue full");
  await expect(block("error").locator(".timeline-error-mark")).toBeVisible();
  await groups.first().click();
  await expect(block("q0")).toBeVisible();
  await expect(block("q2")).toBeHidden();
  await page.getByRole("button", {name: "Collapse 2 Q1 instructions", exact: true}).press("Space");
  await expect(block("q0")).toBeHidden();
  await page.getByRole("button", {name: "Instruction", exact: true}).click();
  await expect(block("stall")).toHaveAttribute("data-instruction-index", "2");
  await expect(groups).toHaveCount(2);
  await page.evaluate(() => window.postMessage({type: "highlightEventIds", highlightEventIds: ["q2"]}, "*"));
  await expect(block("q2")).toBeVisible();
  await expect(block("q0")).toBeHidden();
});

test("RT wait evidence preserves one command slot and pending Q1 selection", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const [complete, sameTime, pending] = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json",
    "from q1timeline.analysis.api import analyze_text",
    "from q1timeline.render.html import render_html",
    "source = 'wait 20\\nwait_trigger 3,4\\nwait_sync 4\\nwait 8\\nstop'",
    "def fixture(arrival):",
    "    triggers = [] if arrival is None else [{'target': 'seq', 'address': 3, 'time_ns': arrival}]",
    "    ir = analyze_text(source, file='waits.q1asm', sequencer_id='seq', execution_mode='simulation',",
    "                      simulation={'startup_delay_ns': 100, 'inputs': {'triggers': triggers}})",
    "    return {'ir': ir, 'html': render_html(ir)}",
    "print(json.dumps([fixture(arrival) for arrival in (150, 120, None)]))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = (html) => html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  const svg = (id) => page.locator(`#timeline-root > svg [data-event-id="${id}"]`);
  const block = (id) => page.locator(`.timeline-block[data-block-id="${id}"]`);
  for (const {ir, html} of [complete, sameTime]) {
    const primary = ir.events.find((event) => event.kind === "wait_trigger");
    const auxiliary = ir.events.find((event) => event.kind === "trigger_wait");
    const arrival = ir.events.find((event) => event.kind === "trigger_receive");
    const sync = ir.events.find((event) => event.kind === "sync_wait");
    await page.goto("about:blank");
    await page.setContent(html);
    await expect(svg(primary.id)).toBeVisible();
    await expect(svg(arrival.id).locator("circle")).toBeVisible();
    if (auxiliary) await expect(svg(auxiliary.id)).toHaveCount(0);
    await expect(svg(sync.id)).toHaveCount(0);
    await mount(page, "idle", ir, {}, {viewMode: "normal"}, shell(html));
    if (auxiliary) {
      await expect(block(primary.id)).toHaveAttribute("data-event-ids", `${primary.id} ${auxiliary.id}`);
      await expect(block(auxiliary.id)).toHaveCount(0);
    }
    await expect(block(primary.id)).toHaveAttribute("title", new RegExp(`20 ns.*${primary.meta.aligned_t1} ns`));
    await page.getByRole("button", {name: "Instruction", exact: true}).click();
    const row = page.locator('[data-lane="sequencer:seq"]');
    await expect(row.locator(".timeline-block:not(.timeline-point-marker)")).toHaveCount(5);
    await expect(block(primary.id)).toHaveAttribute("data-instruction-index", "1");
    const ordinal = Number(await block(arrival.id).getAttribute("data-instruction-index"));
    expect(ordinal).toBeGreaterThanOrEqual(1);
    expect(ordinal).toBeLessThan(2);
  }

  const wait = pending.ir.events.find((event) => event.kind === "trigger_wait");
  const issue = pending.ir.events.find((event) => event.id === wait.meta.q1_issue_event_id);
  await page.goto("about:blank");
  await page.setContent(pending.html);
  await page.getByRole("link", {name: "Show event"}).click();
  await expect(svg(wait.id)).toHaveClass(/is-selected/);
  await page.getByRole("button", {name: "Debug", exact: true}).click();
  await svg(issue.id).click();
  await expect(svg(wait.id)).toHaveClass(/is-related/);
  await mount(page, "idle", pending.ir, {}, {viewMode: "normal"}, shell(pending.html));
  await page.evaluate((id) => window.postMessage({type: "highlightEventIds", highlightEventIds: [id]}, "*"), issue.id);
  await expect(block(wait.id)).toHaveAttribute("aria-selected", "true");
  await page.getByRole("button", {name: "Debug view", exact: true}).click();
  await expandQ1Sections(page);
  await block(issue.id).press("Enter");
  await expect(block(wait.id)).toHaveClass(/is-instruction-related/);
  await page.getByRole("link", {name: "Show event"}).click();
  await expect(block(wait.id)).toHaveAttribute("aria-selected", "true");
});

test("keeps the trigger hit target centered and narrow in Instruction layout", async ({page}) => {
  const ir = {sequencers: [{id: "seq"}], events: [
    event("wait", "wait", 0, 100),
    event("trigger", "trigger_receive", 30, 30, {address: 1}),
    event("stop", "stop", 100, 100),
  ]};
  await mount(page, "idle", ir, {analysisDetailsOpen: false});
  const marker = page.getByRole("button", {name: "Trigger address 1 · 30 ns"});
  expect((await marker.boundingBox()).width).toBe(10);
  await page.getByRole("button", {name: "Instruction", exact: true}).click();
  const wait = page.locator('.timeline-block[data-event-id="wait"]');
  const [point, slot] = await Promise.all([marker.boundingBox(), wait.boundingBox()]);
  expect(point.width).toBe(10);
  expect(slot.width).toBeGreaterThan(20);
  expect(Math.abs(point.x + point.width / 2 - (slot.x + 0.3 * slot.width))).toBeLessThan(1);
  await wait.click({position: {x: slot.width / 2, y: slot.height / 2}});
  await expect(wait).toHaveAttribute("aria-selected", "true");
});

test("coincident trigger arrivals share one point and preserve each linked address", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json",
    "from q1timeline.analysis.api import analyze_text",
    "from q1timeline.render.html import render_html",
    "inputs = {'triggers': [{'target': 'seq', 'time_ns': time, 'address': address}",
    "    for time, address in [(120, 1), (120, 2), (120, 1), (140, 3)]]}",
    "ir = analyze_text('wait 100\\nstop', file='arrivals.q1asm', sequencer_id='seq', execution_mode='simulation',",
    "    alignment_mode='first_anchor', alignment_anchor_kinds=['wait'],",
    "    simulation={'startup_delay_ns': 100, 'inputs': inputs})",
    "arrivals = [event for event in ir['events'] if event['kind'] == 'trigger_receive']",
    "ir['diagnostics'].append({'severity': 'warning', 'category': 'trigger_arrival_fixture',",
    "    'message': 'Second original arrival.', 'related_events': [arrivals[1]['id']]})",
    "print(json.dumps({'ir': ir, 'html': render_html(ir)}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const [first, second, duplicate, later] = ir.events.filter((item) => item.kind === "trigger_receive");
  const title = (time) => `Trigger addresses 1 ×2, 2 · ${time} ns`;
  const address = page.locator("#event-inspector-fields").getByText("Trigger address", {exact: true}).locator("xpath=following-sibling::dd[1]");
  const show = page.locator('.diagnostic[data-diagnostic-category="trigger_arrival_fixture"]').getByRole("link", {name: "Show event"});

  await page.setContent(html);
  const svgPoints = page.locator('#timeline-root > svg [data-kind="trigger_receive"]');
  const svgPoint = svgPoints.locator(`xpath=self::*[@data-event-id="${first.id}"]`);
  await expect(svgPoints).toHaveCount(2);
  await expect(svgPoint.locator("title")).toHaveText(title(20));
  await expect(svgPoint.locator("circle")).toHaveAttribute("r", "3");
  await expect(svgPoint).toHaveAttribute("data-event-ids", `${second.id} ${duplicate.id}`);
  await expect(svgPoints.locator(`xpath=self::*[@data-event-id="${later.id}"]`).locator("title")).toHaveText("Trigger address 3 · 40 ns");
  await analysisDetails(page);
  await show.click();
  await expect(svgPoint).toHaveClass(/is-selected/);
  await expect(address).toHaveText("2");
  await page.getByRole("button", {name: "Debug", exact: true}).click();
  await expect(svgPoint).toHaveClass(/is-selected/);
  await expect(address).toHaveText("2");

  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", ir, {}, {viewMode: "normal"}, shell);
  const points = page.locator('.timeline-point-marker[title^="Trigger address"]');
  const point = page.locator(`.timeline-point-marker[data-block-id="${first.id}"]`);
  await expect(points).toHaveCount(2);
  await expect(point).toHaveAttribute("title", title(20));
  await expect(point).toHaveAttribute("data-event-ids", `${first.id} ${second.id} ${duplicate.id}`);
  await show.click();
  await expect(point).toHaveAttribute("aria-selected", "true");
  await expect(address).toHaveText("2");
  await point.click();
  await expect(address).toHaveText("1");
  await page.evaluate((id) => window.postMessage({type: "highlightEventIds", highlightEventIds: [id]}, "*"), second.id);
  await expect(address).toHaveText("2");
  await page.getByRole("button", {name: "Local time basis"}).click();
  await expect(point).toHaveAttribute("title", title(120));
  await expect(address).toHaveText("2");
  await page.getByRole("button", {name: "Instruction", exact: true}).click();
  await expect(points).toHaveCount(2);
  await expect(point).toHaveAttribute("title", title(120));
  await expect(point).toHaveCSS("width", "10px");
  await expect(address).toHaveText("2");
  await expect(page.locator(`.timeline-point-marker[data-block-id="${later.id}"]`)).toHaveAttribute("title", "Trigger address 3 · 140 ns");
  await page.getByRole("button", {name: "Time scale", exact: true}).click();
  await page.getByRole("button", {name: "Aligned time basis"}).click();
  await expect(point).toHaveAttribute("title", title(20));
  await expect(address).toHaveText("2");
});

test("time fold tabs keep only icon and duration on the ruler and preserve keyboard focus and source state", async ({page}) => {
  const ir = timeFoldFixture();
  await page.setViewportSize({width: 1000, height: 800});
  await mount(page, "idle", ir, {analysisDetailsOpen: false});
  const toggle = page.locator('.ruler .q1timeline-wait-fold .q1timeline-fold-toggle').filter({hasText: /^8 µs$/});
  await expect(toggle).toHaveCount(1);
  await expect(toggle).toHaveText("8 µs");
  await expect(toggle.locator(".q1timeline-fold-icon svg")).toBeVisible();
  await expect(page.locator('.lane-track .q1timeline-fold-toggle')).toHaveCount(0);
  await expect(page.locator('.q1timeline-fold-slashes')).toHaveCount(0);
  const acquisition = page.locator('[data-block-id="acq0:acquisition-window"]');
  await acquisition.click();
  await expect(acquisition).toHaveAttribute("aria-selected", "true");
  await page.locator('.shared-timeline-stage').screenshot({path: path.resolve(__dirname, "../../../output/playwright/time-fold-implemented.png")});
  await toggle.focus();
  await page.keyboard.press("Enter");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(toggle).toBeFocused();
  await expect(toggle).toHaveText("8 µs");
  await expect(toggle.locator(".q1timeline-fold-icon")).toHaveClass(/is-collapse/);
  await expect(page.locator('.timeline-block[data-event-id="acq0"][aria-selected="true"]')).toHaveCount(1);
  expect(await page.evaluate(() => window.getSavedTimelineState().timelineExpandedGaps.map(({start, end}) => [start, end]))).toEqual([[1000, 9000]]);
  await page.locator('.shared-timeline-stage').screenshot({path: path.resolve(__dirname, "../../../output/playwright/time-fold-implemented-expanded.png")});
  await page.keyboard.press("Space");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(toggle).toBeFocused();
  await expect(toggle.locator(".q1timeline-fold-icon")).toHaveClass(/is-expand/);
  expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
});

test("time fold animation grows the interval through an intermediate width", async ({page}) => {
  await mount(page, "idle", timeFoldFixture(), {analysisDetailsOpen: false});
  await page.emulateMedia({reducedMotion: "no-preference"});
  const toggle = page.locator('.ruler .q1timeline-wait-fold .q1timeline-fold-toggle').filter({hasText: /^8 µs$/});
  const motion = await toggle.evaluate(button => {
    const selector = '.ruler .q1timeline-wait-fold[data-time-start="1000"]';
    const before = document.querySelector(selector).getBoundingClientRect().width;
    button.click();
    const block = document.querySelector(selector);
    const animation = block.getAnimations()[0];
    if (!animation) throw new Error("The folded interval should animate when expanding time");
    const duration = animation.effect.getTiming().duration;
    animation.pause();
    animation.currentTime = duration / 2;
    const middle = block.getBoundingClientRect().width;
    animation.finish();
    const after = block.getBoundingClientRect().width;
    document.getAnimations().forEach(item => item.finish());
    return {before, middle, after, duration};
  });
  expect(motion.duration).toBe(160);
  expect(motion.middle).toBeGreaterThan(motion.before + 1);
  expect(motion.middle).toBeLessThan(motion.after - 1);
  await expect(toggle).toHaveText("8 µs");
});

test("time fold expansion respects reduced motion", async ({page}) => {
  await mount(page, "idle", timeFoldFixture(), {analysisDetailsOpen: false});
  const toggle = page.locator('.ruler .q1timeline-wait-fold .q1timeline-fold-toggle').filter({hasText: /^8 µs$/});
  const result = await toggle.evaluate(button => {
    const selector = '.ruler .q1timeline-wait-fold[data-time-start="1000"]';
    const before = document.querySelector(selector).getBoundingClientRect().width;
    button.click();
    const block = document.querySelector(selector);
    return {before, after: block.getBoundingClientRect().width, animations: block.getAnimations().length};
  });
  expect(result.animations).toBe(0);
  expect(result.after).toBeGreaterThan(result.before * 2);
});

test("time fold tabs use compact icons when many durations would overlap", async ({page}) => {
  await page.setViewportSize({width: 1000, height: 800});
  const events = [];
  for (let index = 0; index < 10; index += 1) {
    const start = index * 10000;
    events.push(event(`a${index}`, "acquire", start, start + 4, {op: "acquire",
      acquisition_window: {start_ns: start, end_ns: start + 984, effective_duration_ns: 984,
        configured_duration_ns: 984, status: "completed", source: "hardware_parameters"}}),
      event(`w${index}`, "wait", start + 4, start + 10000, {op: "wait"}));
  }
  await mount(page, "idle", {sequencers: [{id: "seq"}], events}, {analysisDetailsOpen: false});
  const tabs = page.locator('.ruler .q1timeline-fold-toggle');
  await expect(tabs).toHaveCount(20);
  expect(await page.locator('.ruler .q1timeline-fold-toggle.is-crowded').count()).toBeGreaterThan(0);
  const boxes = (await Promise.all((await tabs.all()).map(tab => tab.boundingBox()))).sort((a, b) => a.x - b.x);
  for (let index = 1; index < boxes.length; index += 1) expect(boxes[index - 1].x + boxes[index - 1].width).toBeLessThanOrEqual(boxes[index].x);
});

test("analysis details contain selection and hardware while compact diagnostics remain visible", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text", "from q1timeline.render.html import render_html",
    'ir=analyze_text("wait 100\\nstop\\n",file="C:/project/readout.q1asm",sequencer_id="seq")',
    'ir["diagnostics"]=[{"severity":"warning","category":"analysis_incomplete","message":"Underflow analysis incomplete: runtime input is missing.","source":{"file":"C:/project/readout.q1asm","line":1},"related_events":[next(e["id"] for e in ir["events"] if e["kind"]=="wait")]}]',
    "print(json.dumps({'ir':ir,'html':render_html(ir)}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", ir, {analysisDetailsOpen: false}, {}, shell);
  const details = page.locator("#q1timeline-analysis-details");
  const diagnostic = page.locator(".diagnostics .diagnostic");
  await expect(details.locator("#event-inspector")).toBeHidden();
  await expect(details.locator("#q1timeline-hardware")).toBeHidden();
  await expect(diagnostic).toBeVisible();
  await expect(details.locator(".diagnostics")).toHaveCount(0);
  await expect(diagnostic).toContainText("Analysis is incomplete");
  await expect(diagnostic).toContainText("readout.q1asm:1");
  await expect(diagnostic).not.toContainText("analysis_incomplete");
  await expect(diagnostic).not.toContainText("C:/project");
  await expect(diagnostic).not.toContainText("Fix:");
  await expect(diagnostic.locator(".diagnostic-message")).toHaveText("runtime input is missing.");
  await diagnostic.getByRole("link", {name: "Show event"}).click();
  await expect(details).toHaveAttribute("open", "");
  await expect(details.locator("#event-inspector-fields")).toContainText("readout.q1asm:1");
  await expect(details.locator("#q1timeline-hardware")).toBeVisible();
  await details.locator(":scope > summary").click();
  await expect(diagnostic).toBeVisible();
  await expect(details.locator("#q1timeline-hardware")).toBeHidden();
  await expect.poll(() => page.evaluate(() => window.getSavedTimelineState().analysisDetailsOpen)).toBe(false);
  const saved = await page.evaluate(() => window.getSavedTimelineState());
  await mount(page, "idle", ir, saved, {}, shell);
  await expect(page.locator("#q1timeline-analysis-details")).not.toHaveAttribute("open", "");
  await expect(page.locator(".diagnostics")).toBeVisible();
});

test("baseline simulation underflow opens the marker in the active time basis", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html, manual} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text", "from q1timeline.render.html import render_html",
    'def simulation(mode=None): return analyze_text("wait 4\\n" + "nop\\n" * 50 + "stop", file="underflow.q1asm", sequencer_id="seq", execution_mode="simulation", simulation={"startup_delay_ns": 100}, alignment_mode=mode)',
    "ir=simulation()",
    "print(json.dumps({'ir':ir,'html':render_html(ir),'manual':simulation('manual:-100')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const warning = ir.events.find((item) => item.kind === "underflow_warning");
  const diagnostics = page.locator('.diagnostic[data-diagnostic-category="rt_queue_underflow"]');
  const showEvent = diagnostics.getByRole("link", {name: "Show event"});
  const inspector = page.locator("#event-inspector-fields");
  const time = inspector.getByText("Time range", {exact: true}).locator("xpath=following-sibling::dd[1]");
  const reason = inspector.locator(".diagnostic-error-message");
  const description = "The RT queue ran out of commands.";

  await page.setContent(html);
  await analysisDetails(page);
  const wait = ir.events.find((item) => item.kind === "wait");
  const svgWait = page.locator(`#timeline-root > svg [data-event-id="${wait.id}"]`);
  const svgMarker = page.locator(`#timeline-root > svg [data-event-id="${warning.id}"]`);
  for (const mode of ["Normal", "Debug"]) {
    await page.getByRole("button", {name: mode, exact: true}).click();
    await expect(svgMarker).toHaveCount(1);
    await expect(svgMarker.locator("title")).toContainText("aligned time: 4 ns");
    await expect(svgMarker.locator("title")).not.toContainText("104 ns");
    await expect(svgWait.locator("title")).toContainText("aligned time: 0 -> 4");
    await svgWait.click();
    await expect(time).toHaveText("0 ns → 4 ns");
    await showEvent.click();
    await expect(time).toHaveText("4 ns");
    await expect(reason).toHaveText(description);
  }

  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", manual, {}, {}, shell);
  const marker = page.locator(`.diagnostic-point[data-event-id="${warning.id}"]`);
  await expect(diagnostics.locator(".diagnostic-message")).toHaveText(description);
  for (const mode of ["Normal", "Debug"]) {
    await page.getByRole("button", {name: `${mode} view`, exact: true}).click();
    await expect(marker).toHaveCount(1);
    await expect(page.locator(".shared-timeline-stage .q1-error-marker, .shared-timeline-stage .timeline-block[data-diagnostic-message]:not(.diagnostic-point)")).toHaveCount(0);
    await showEvent.click();
    await expect(marker).toHaveAttribute("aria-selected", "true");
    await expect(time).toHaveText("4 ns");
    await expect(reason).toHaveText(description);
    await expect(marker).toHaveAttribute("title", `RT underflow · 4 ns\n${description}`);
    await page.getByRole("button", {name: "Local time basis"}).click();
    await expect(time).toHaveText("104 ns");
    await expect(reason).toHaveText(description);
    await expect(marker).toHaveAttribute("title", `RT underflow · 104 ns\n${description}`);
    await page.getByRole("button", {name: "Aligned time basis"}).click();
    await expect(time).toHaveText("4 ns");
  }
});

test("the actual fallback RT dequeue anchors Normal and Debug in both viewers", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const datasets = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json",
    "from q1timeline.analysis.api import analyze_text",
    "from q1timeline.render.html import render_html",
    "programs = ['nop\\nwait 20\\nstop', 'nop\\nwait 20\\nplay 0,1,4\\nstop', 'nop\\nstop']",
    "def dataset(program):",
    "    ir = analyze_text(program, file='seq.q1asm', sequencer_id='seq', execution_mode='simulation',",
    "        alignment_mode='first_play', simulation={'startup_delay_ns': 100})",
    "    return {'ir': ir, 'html': render_html(ir)}",
    "print(json.dumps([dataset(program) for program in programs]))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const windowStatus = page.locator("#q1timeline-time-window");
  const axis = page.locator("#timeline-root > svg");
  const local = page.getByRole("button", {name: "Local time basis"});
  for (const [index, {ir, html}] of datasets.entries()) {
    const wait = ir.events.find((event) => event.kind === "wait");
    const activeAnchor = ir.events.find((event) => event.id === ir.alignment.seq.anchor_event_id);
    expect(activeAnchor?.kind).toBe(["rt_queue_dequeue", "play", undefined][index]);
    expect(ir.project.alignment_mode).toBe("first_play");
    await page.goto("about:blank");
    await page.setContent(html);
    await expect(axis).toHaveAttribute("data-time-min", ["0", "-20", "0"][index]);
    const standaloneWait = wait && axis.locator(`[data-event-id="${wait.id}"]`);
    if (index === 0) {
      await expect(standaloneWait).toHaveAttribute("data-t0-x", "190.00");
      await expect(axis.locator(".tick-label").first()).toHaveText("0 ns");
      await analysisDetails(page);
      await page.getByRole("button", {name: "Debug", exact: true}).click();
      await expect(axis).toHaveAttribute("data-time-min", "-100");
      await expect(axis.locator(".tick-label").first()).toHaveText("-100 ns");
      await expect(standaloneWait).not.toHaveAttribute("data-t0-x", "190.00");
      await page.getByRole("button", {name: "Normal", exact: true}).click();
      await expect(axis).toHaveAttribute("data-time-min", "0");
      await expect(standaloneWait).toHaveAttribute("data-t0-x", "190.00");
    } else if (wait) {
      await expect(page.locator('template[data-timeline-mode="debug"]')).toHaveCount(1);
      await expect(axis.locator(".tick-label").first()).toHaveText("-20 ns");
    } else {
      expect(ir.events.filter((event) => event.kind === "stop" || event.kind === "rt_queue_dequeue")).toHaveLength(0);
      await expect(axis).toHaveAttribute("data-time-max", "8");
      await expect(page.locator("template[data-timeline-mode]")).toHaveCount(0);
    }

    const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    await mount(page, "idle", ir, index === 0 ? {timelineTimeBasis: "local"} : {}, {viewMode: "normal"}, shell);
    const block = wait && page.locator(`.timeline-block[data-block-id="${wait.id}"]`);
    await expect(windowStatus).toHaveAttribute("title", ["X (aligned): 0..20 ns", "X (aligned): -20..4 ns", "X (aligned): 0..8 ns"][index]);
    if (index === 0) {
      await expect(local).toBeHidden();
      await expect(block).toHaveCSS("left", "0px");
      await expect(page.locator(".ruler .tick").first()).toHaveText("0 ns");
      await page.getByRole("button", {name: "Debug view", exact: true}).click();
      await expect(windowStatus).toHaveAttribute("title", "X (aligned): -100..20 ns");
      await expect(page.locator(".ruler .tick").first()).toHaveText("-100 ns");
      await expect(block).not.toHaveCSS("left", "0px");
      await page.getByRole("button", {name: "Normal view", exact: true}).click();
      await expect(windowStatus).toHaveAttribute("title", "X (aligned): 0..20 ns");
      await expect(block).toHaveCSS("left", "0px");
    } else {
      await expect(local).toBeVisible();
      if (wait) await expect(block).toHaveAttribute("title", /wait \| -20 ns -> 0 ns/);
    }
  }
});

test("resolved RT anchors frame early RT and arrival events separately from Q1 prefill", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const datasets = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json",
    "from q1timeline.analysis.api import analyze_text",
    "from q1timeline.render.html import render_html",
    "def fixture(mode, kinds, normal, debug, arrival=None):",
    "    program = ('wait 20\\n' if mode != 'first_wait_sync' else '') + 'wait_sync 4\\nwait 8\\nstop'",
    "    options = {'startup_delay_ns': 100}",
    "    if arrival is not None:",
    "        options['inputs'] = {'triggers': [{'target': 'seq', 'address': 1, 'time_ns': arrival}]}",
    "    ir = analyze_text(program, file='seq.q1asm', sequencer_id='seq', execution_mode='simulation',",
    "        alignment_mode=mode, alignment_anchor_kinds=kinds, simulation=options)",
    "    return {'ir': ir, 'html': render_html(ir), 'normal': normal, 'debug': debug, 'arrival': arrival}",
    "print(json.dumps([fixture('after_first_wait_sync', [], -24, -124),",
    "    fixture('after_first_wait_sync', [], -34, -124, 90), fixture('first_wait_sync', [], 0, -100),",
    "    fixture('first_anchor', ['rt_queue_dequeue', 'play'], 0, -100),",
    "    fixture('first_anchor', ['rt_queue_dequeue', 'q1_issue'], 0, None)]))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const status = page.locator("#q1timeline-time-window");
  const axis = page.locator("#timeline-root > svg");
  const ticks = page.locator(".ruler .tick");
  const local = page.getByRole("button", {name: "Local time basis"});
  for (const {ir, html, normal, debug, arrival} of datasets) {
    const anchor = ir.events.find((event) => event.id === ir.alignment.seq.anchor_event_id);
    const first = ir.events.find((event) => event.kind === (arrival ? "trigger_receive" :
      ir.project.alignment_mode === "first_wait_sync" ? "wait_sync" : "wait"));
    await page.goto("about:blank");
    await page.setContent(html);
    await expect(axis).toHaveAttribute("data-time-min", String(normal));
    if (debug !== null) {
      const firstEvent = axis.locator(`[data-event-id="${first.id}"]`);
      await expect(firstEvent).toHaveAttribute("data-t0-x", "190.00");
      await expect(axis.locator(".tick-label").first()).toHaveText(`${normal} ns`);
      await analysisDetails(page);
      await page.getByRole("button", {name: "Debug", exact: true}).click();
      await expect(axis).toHaveAttribute("data-time-min", String(debug));
      await expect(axis.locator(".tick-label").first()).toHaveText(`${debug} ns`);
      await expect(firstEvent).toBeVisible();
      await page.getByRole("button", {name: "Normal", exact: true}).click();
      await expect(firstEvent).toHaveAttribute("data-t0-x", "190.00");
    } else {
      await expect(page.locator("template[data-timeline-mode]")).toHaveCount(0);
    }
    const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    await mount(page, "idle", ir, {}, {viewMode: "normal"}, shell);
    await expect(status).toHaveAttribute("title", new RegExp(`^X \\(aligned\\): ${normal}\\.\\.`));
    if (debug === null) {
      expect(anchor.kind).toBe("q1_issue");
      await expect(local).toBeVisible();
      continue;
    }
    const block = page.locator(`.timeline-block[data-block-id="${first.id}"]`);
    await expect(block).toHaveCSS("left", "0px");
    await expect(ticks.first()).toHaveText(`${normal} ns`);
    await page.getByRole("button", {name: "Debug view", exact: true}).click();
    await expect(status).toHaveAttribute("title", new RegExp(`^X \\(aligned\\): ${debug}\\.\\.`));
    await expect(ticks.first()).toHaveText(`${debug} ns`);
    await expect(block).toBeVisible();
    await page.getByRole("button", {name: "Normal view", exact: true}).click();
    await expect(block).toHaveCSS("left", "0px");
    if (anchor.kind === "rt_queue_dequeue") {
      await expect(local).toBeHidden();
    } else {
      await local.click();
      await expect(status).toHaveAttribute("title", new RegExp(`^X \\(local\\): ${arrival || 100}\\.\\.`));
      await expect(block).toHaveCSS("left", "0px");
      if (ir.project.alignment_mode === "after_first_wait_sync") {
        const wait = ir.events.find((event) => event.kind === "wait");
        await expect(page.locator(`.timeline-block[data-block-id="${wait.id}"]`)).toHaveAttribute("title", /wait \| 100 -> 120/);
      }
    }
  }
});

test("Normal excludes the Q1 tail while retaining visible RT endpoints", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const datasets = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json",
    "from q1timeline.analysis.alignment import align_timelines",
    "from q1timeline.analysis.api import analyze_documents",
    "from q1timeline.ir.serialize import timeline_ir_from_states",
    "from q1timeline.q1asm.parser import parse_q1asm",
    "from q1timeline.render.html import render_html",
    "from q1timeline.simulation.runtime import simulate_programs",
    "peer = 'nop\\n' * 100 + 'stop'",
    "def analyze(**kwargs):",
    "    return analyze_documents(kwargs.pop('documents', {}) | {'peer.q1asm': peer}, execution_mode='simulation', **kwargs)",
    "def data(name, ir, normal, debug, scope=None):",
    "    return {'name': name, 'ir': ir, 'html': render_html(ir), 'normal': normal, 'debug': debug, 'scope': scope}",
    "scope_programs = {'rx': parse_q1asm('acquire 0,0,4\\nstop'), 'peer': parse_q1asm(peer)}",
    "scope_states = simulate_programs(scope_programs,",
    "    hardware_parameters={'rx': {'integration_length_acq': 200, 'scope_acq_sequencer_select': 0,",
    "                                 'scope_acq_trigger_mode_path0': 'sequencer'}},",
    "    sequencer_metadata={'rx': {'module_type': 'QRM', 'sequencer_index': 0}},",
    "    simulation={'inputs': {'acquisitions': [{'sequencer': 'rx', 'acquisition_index': 0, 'bin_index': 0, 'i': 1}]}})",
    "align_timelines(scope_states)",
    "scope_ir = timeline_ir_from_states(scope_states, project={'alignment_mode': 'first_rt_dequeue'})",
    "print(json.dumps([",
    "    data('buffered', analyze(documents={'a.q1asm': 'wait 20\\nstop'}), [0, 20], [-8, 396]),",
    "    data('explicit', analyze(documents={'a.q1asm': 'wait 20\\nplay 0,1,4\\nstop'},",
    "        alignment_mode='first_play'), [-20, 4], [-32, 372]),",
    "    data('comparison', analyze(documents={'a.q1asm': 'set_cond 1,1,0,4\\nwait 60\\nstop'},",
    "        branch_assumptions={'a:set_cond:0:1': 'both'}), [0, 60], [-12, 392]),",
    "    data('flow', analyze(documents={'sender.q1asm': 'fb_com_data 16,7,4\\nstop', 'rx.q1asm': 'fb_pull_data R0,R1\\nstop'},",
    "        simulation={'routes': [{'source': 'sender', 'target': 'rx', 'id': 16, 'latency_ns': 80}]}), [0, 80], [-8, 396]),",
    "    data('scope', scope_ir, [0, 200], [-8, 396], 16384),",
    "    data('q1-only', analyze(), [0, 404], [0, 404]),",
    "]))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, maxBuffer: 8 * 1024 * 1024,
    env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const axis = page.locator("#timeline-root > svg");
  const status = page.locator("#q1timeline-time-window");
  const windowTitle = ([min, max], scope) => `X (aligned): ${min}..${scope ?? max} ns`;
  for (const {name, ir, html, normal, debug, scope} of datasets) {
    await page.goto("about:blank");
    await page.setContent(html);
    await expect(axis).toHaveAttribute("data-time-max", String(normal[1]));
    await analysisDetails(page);
    await page.getByRole("button", {name: "Debug", exact: true}).click();
    await expect(axis).toHaveAttribute("data-time-max", String(debug[1]));
    await page.getByRole("button", {name: "Normal", exact: true}).click();
    await expect(axis).toHaveAttribute("data-time-max", String(normal[1]));

    const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    await mount(page, "idle", ir, {}, {viewMode: "normal"}, shell);
    await expect(status).toHaveAttribute("title", windowTitle(normal, scope));
    if (name === "flow") {
      const flow = ir.feedback_flows[0];
      const endpoint = page.locator(`.feedback-endpoint[data-flow-id="${flow.id}"][data-feedback-role="target"]`);
      await expect(endpoint).toBeVisible();
      await expect(endpoint).not.toHaveClass(/is-clipped/);
    } else if (scope) {
      const acquire = ir.events.find((event) => event.kind === "acquire");
      for (const blockId of [`${acquire.id}:acquisition-window`, `${acquire.id}:scope:acquisition-window`]) {
        const window = page.locator(`.timeline-block[data-block-id="${blockId}"]`);
        await expect(window).toBeVisible();
        await expect(window).toHaveAttribute("data-end-clipped", "false");
      }
    }
    await page.getByRole("button", {name: "Debug view", exact: true}).click();
    await expect(status).toHaveAttribute("title", windowTitle(debug, scope));
    await page.getByRole("button", {name: "Normal view", exact: true}).click();
    await expect(status).toHaveAttribute("title", windowTitle(normal, scope));
  }
});

test("initial RT buffer summary stays hidden without a related warning or error", async ({page}) => {
  await mount(page);
  await expect(page.locator("#q1timeline-initial-buffer-summary")).toHaveCount(0);
  const ir = {sequencers: [{id: "seq"}], events: [event("wait", "wait", 0, 100)], assumptions: {
    underflow: {initial_buffer_assumed_ns: 1000000, initial_buffer_possible_ns: 2000000}}};
  await mount(page, "completed", ir);
  await expect(page.locator("#q1timeline-initial-buffer-summary")).toHaveCount(0);
  for (const diagnostic of [
    {severity: "info", category: "initial_rt_buffer_estimate"},
    {severity: "warning", category: "register_not_ready"},
    {severity: "warning", category: "analysis_incomplete", details: {analysis_scope: "rt_queue"}},
    {severity: "error", category: "acquisition_buffer_sustained_overload"},
  ]) {
    await mount(page, "completed", {...ir, diagnostics: [diagnostic]});
    await expect(page.locator("#q1timeline-initial-buffer-summary")).toHaveCount(0);
  }
  const firstRt = page.locator('.timeline-block[data-event-id="wait"]').first();
  await expect(firstRt).toBeVisible();
  await expect(firstRt).toHaveAttribute("title", /^wait \| 0 ns -> 100 ns\n/);
  await expect(firstRt).toHaveCSS("left", "0px");
});

test("initial RT buffer summary describes tentative playback consumption", async ({page}) => {
  const ir = {sequencers: [{id: "seq"}], events: [event("wait", "wait", 0, 100)],
    assumptions: {underflow: {initial_buffer_assumed_ns: 1000000, initial_buffer_possible_ns: 2000000}},
    diagnostics: [{severity: "warning", category: "possible_underflow", message: "Buffer may be exhausted"}],
    playback: {status: "completed", steps: 7},
    execution: {seq: {queue_timing: {initial_buffer: {
      assumed_ns: 1000000, possible_ns: 2000000, required_ns: 1500000, status: "tentative"}}}}};
  await mount(page, "completed", ir);
  const summary = page.locator("#q1timeline-initial-buffer-summary");
  await expect(summary).toHaveText("Initial buffer ≈ 1 ms · up to 2 ms probably OK");
  await expect(summary).toHaveAttribute("title", /seq: 1.5 ms of initial buffer required \(tentative\)/);
  await expect(summary).toHaveAttribute("title", /Up to 2 ms may be sufficient, but is unconfirmed/);
  for (const category of ["definite_underflow", "playback_queue_underflow", "rt_queue_underflow"]) {
    await mount(page, "completed", {...ir, diagnostics: [{severity: "error", category, message: "Buffer exhausted"}]});
    await expect(page.locator("#q1timeline-initial-buffer-summary")).toBeVisible();
  }
});

test("external input editor keeps confirmed settings until rerun succeeds and recovers from errors", async ({page}) => {
  const trigger = {id: "shot_start", address: "TRIGGER_INDEX", targets: ["seq"], times_ns: [1000]};
  const ir = {sequencers: [{id: "seq"}, {id: "other"}], events: [event("wait", "wait_trigger", 0, 0)],
    playback: {status: "waiting", can_resume: false, reason: "No matching external input", external_inputs: {
      triggers: [{...trigger, resolved_address: 1}], overridden: false}}};
  const base = {triggers: [trigger], overridden: false, canSave: true};
  await mount(page, "waiting", ir, {}, {externalInputs: base});
  await expect(page.getByRole("button", {name: "Waiting for external input", exact: true})).toBeDisabled();
  const summary = page.locator("#q1timeline-external-inputs-summary");
  await expect(summary).toContainText("≈ Scenario input · shot_start · address 1 · 1 sequencers · 1000 ns");
  await expect(summary).toHaveAttribute("title", /TRIGGER_INDEX → 1/);
  await page.getByRole("button", {name: "Configure external inputs"}).click();
  const form = page.getByRole("form", {name: "External input configuration"});
  await form.getByLabel("Trigger 1 times (ns)").fill("-1");
  await form.getByRole("button", {name: "Apply & rerun"}).click();
  await expect(form.getByRole("status")).toHaveText("Event times must be nonnegative integers in ns.");
  expect(await page.evaluate(() => window.messages.filter((message) => message.type === "setExternalInputs"))).toEqual([]);
  await form.getByLabel("Trigger 1 times (ns)").fill("2000, 4000");
  await form.locator("summary").click();
  await form.getByRole("checkbox", {name: "other", exact: true}).check();
  await expect(form.locator("summary")).toHaveText("Targets (2)");
  await form.getByRole("button", {name: "Apply & rerun"}).click();
  await expect(form.getByLabel("Trigger 1 times (ns)")).toBeDisabled();
  await expect(summary).toContainText("1000 ns");
  const request = await page.evaluate(() => window.messages.find((message) => message.type === "setExternalInputs"));
  expect(request).toEqual({type: "setExternalInputs", triggers: [{...trigger, targets: ["seq", "other"], times_ns: [2000, 4000]}]});
  await page.evaluate((state) => window.dispatchEvent(new MessageEvent("message", {data: {type: "externalInputsState", state}})), {...base, error: "Parameter TRIGGER_INDEX is unavailable"});
  await expect(form.getByLabel("Trigger 1 times (ns)")).toBeEnabled();
  await expect(form.getByLabel("Trigger 1 times (ns)")).toHaveValue("2000, 4000");
  await expect(form.getByRole("status")).toHaveText("Parameter TRIGGER_INDEX is unavailable");
  await expect(form.getByRole("button", {name: "Save to project"})).toBeDisabled();
  await form.getByRole("button", {name: "Apply & rerun"}).click();
  const applied = {triggers: request.triggers, overridden: true, canSave: true};
  await page.evaluate((state) => window.dispatchEvent(new MessageEvent("message", {data: {type: "externalInputsState", state}})), applied);
  await expect(summary).toContainText("2000, 4000 ns · Unsaved changes");
  await form.getByRole("button", {name: "Save to project"}).click();
  expect(await page.evaluate(() => window.messages.at(-1))).toEqual({type: "saveExternalInputs"});
  await page.evaluate((state) => window.dispatchEvent(new MessageEvent("message", {data: {type: "externalInputsState", state}})), {...applied, overridden: false});
  await expect(summary).not.toContainText("Unsaved changes");
  await form.getByLabel("Trigger 1 times (ns)").fill("7000");
  await form.getByRole("button", {name: "Reset changes"}).click();
  await expect(form.getByLabel("Trigger 1 times (ns)")).toHaveValue("2000, 4000");
  await page.screenshot({path: test.info().outputPath("shared-trigger-configuration.png"), fullPage: true});
});

test("unconfigured projects can add and remove shared inputs without saving unapplied drafts", async ({page}) => {
  await mount(page, "idle", undefined, {}, {externalInputs: {triggers: [], overridden: false, canSave: false}});
  await expect(page.locator("#q1timeline-external-inputs-summary")).toHaveText("External inputs · not configured");
  await page.getByRole("button", {name: "Configure external inputs"}).click();
  const form = page.getByRole("form", {name: "External input configuration"});
  await form.getByRole("button", {name: "Add trigger"}).click();
  await form.getByLabel("Trigger 1 name").fill("shared_start");
  await form.getByLabel("Trigger 1 times (ns)").fill("");
  await form.getByRole("button", {name: "Apply & rerun"}).click();
  expect(await page.evaluate(() => window.messages.at(-1))).toEqual({type: "setExternalInputs", triggers: [{id: "shared_start", address: 1, targets: ["seq"], times_ns: []}]});
  await page.evaluate(() => window.dispatchEvent(new MessageEvent("message", {data: {type: "externalInputsState", state: {
    triggers: [{id: "shared_start", address: 1, targets: ["seq"], times_ns: []}], overridden: true, canSave: false}}})));
  await expect(form.getByRole("button", {name: "Save to project"})).toBeDisabled();
  await expect(page.locator("#q1timeline-external-inputs-summary")).toContainText("no events");
  await form.getByRole("button", {name: "Remove trigger 1"}).click();
  await form.getByRole("button", {name: "Apply & rerun"}).click();
  expect(await page.evaluate(() => window.messages.at(-1))).toEqual({type: "setExternalInputs", triggers: []});
});

test("Configure preserves ordered LINQ arrivals and the inspector links the receipt", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.playback import PlaybackSession", "from q1timeline.render.html import render_html",
    "inputs={'feedback':[{'target':'seq','arrivals':[{'time_ns':16,'id':9,'value':-1}]}]}",
    "s=PlaybackSession({'seq':('linq.q1asm','wait 100\\nfb_pull_data R0,R1\\nnop\\nwait 4\\nstop')},external_inputs=inputs)",
    "s.run(budget_ms=1000)", "ir=s.snapshot()", "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  const trigger = {id: "shot", address: 1, targets: ["seq"], times_ns: [1000]};
  const base = {triggers: [trigger], overridden: false, canSave: true};
  await mount(page, "completed", ir, {}, {externalInputs: base}, shell);
  await page.evaluate((eventId) => window.dispatchEvent(new MessageEvent("message", {
    data: {type: "highlightEventIds", highlightEventIds: [eventId]},
  })), ir.playback.feedback[0].event_id);
  const inspector = page.locator("#event-inspector-fields");
  await expect(inspector).toContainText(ir.playback.feedback[0].input_event_id);
  await expect(inspector).toContainText("data → R1, id → R0");
  await page.getByRole("button", {name: "Configure external inputs"}).click();
  const form = page.getByRole("form", {name: "External input configuration"});
  await form.getByRole("button", {name: "Add LINQ receiver"}).click();
  const arrivals = form.getByLabel("LINQ receiver 1 arrivals");
  await arrivals.fill("16, 9, -1\n16, 5, 0x7b");
  await form.getByRole("button", {name: "Apply & rerun"}).click();
  const feedback = [{target: "seq", arrivals: [{time_ns: 16, id: 9, value: -1}, {time_ns: 16, id: 5, value: 123}]}];
  expect(await page.evaluate(() => window.messages.at(-1))).toEqual({type: "setExternalInputs", triggers: [trigger], feedback});
  const update = (state) => page.evaluate((state) => window.dispatchEvent(new MessageEvent("message", {
    data: {type: "externalInputsState", state},
  })), state);
  await update({...base, feedback, overridden: true});
  await expect(arrivals).toHaveValue("16, 9, -1\n16, 5, 123");
  await form.getByRole("button", {name: "Save to project"}).click();
  expect(await page.evaluate(() => window.messages.at(-1))).toEqual({type: "saveExternalInputs"});
  await update({...base, feedback});
  await arrivals.fill("");
  await form.getByRole("button", {name: "Apply & rerun"}).click();
  expect(await page.evaluate(() => window.messages.at(-1))).toEqual({type: "setExternalInputs", triggers: [trigger], feedback: [{target: "seq", arrivals: []}]});
  await update({...base, feedback: [{target: "seq", arrivals: []}], overridden: true});
  await form.getByRole("button", {name: "Reset changes"}).click();
  expect(await page.evaluate(() => window.messages.at(-1))).toEqual({type: "resetExternalInputs"});
  await update({...base, feedback});
  await expect(arrivals).toHaveValue("16, 9, -1\n16, 5, 123");
});

test("external input inspection follows the selected time basis for each receiver", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {manual, perReceiver, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.playback import PlaybackSession", "from q1timeline.render.html import render_html",
    "inputs={'triggers':[{'id':'shot','address':1,'targets':['a','b'],'times_ns':[16]}], 'feedback':[{'target':seq,'arrivals':[{'time_ns':16,'id':9,'value':123}]} for seq in ('a','b')]}",
    "body='wait_trigger 1,4\\nfb_pull_data R0,R1\\nnop\\nstop'",
    "s=PlaybackSession({'a':('a.q1asm','origin: wait 12\\n'+body),'b':('b.q1asm','wait 12\\norigin: '+body),'c':('c.q1asm','wait 12\\nwait_trigger 2,4\\nstop')},alignment_mode='manual:-8',external_inputs=inputs)",
    "s.run(budget_ms=1000)", "manual=s.snapshot()", "s.alignment_mode='label:origin'",
    "print(json.dumps({'manual':manual,'perReceiver':s.snapshot(),'html':render_html(manual,default_mode='debug')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  const inspector = page.locator("#event-inspector-fields");
  const field = (label) => inspector.getByText(label, {exact: true}).locator("xpath=following-sibling::dd[1]");
  const select = async (ir, sequencer, kind, input) => {
    const event = ir.events.find((item) => item.sequencer_id === sequencer && item.kind === kind && item.meta[input]);
    await page.evaluate((eventId) => window.dispatchEvent(new MessageEvent("message", {
      data: {type: "highlightEventIds", highlightEventIds: [eventId]},
    })), event.id);
    await expect(field("Sequencer")).toHaveText(sequencer);
    await expect(field("Lane")).toHaveText(event.lane);
  };
  const triggerTimes = async (arm, arrival, resume) => {
    await expect(field("Wait armed (project ns)")).toHaveText(String(arm));
    await expect(field("Trigger arrival (project ns)")).toHaveText(String(arrival));
    await expect(field("Resume (project ns)")).toHaveText(String(resume));
    await expect(field("Wait after trigger (ns)")).toHaveText("4");
  };
  const linqTimes = async (arm, arrival, complete) => {
    await expect(field("Wait armed (Q1 ns)")).toHaveText(String(arm));
    await expect(field("LINQ arrival (Q1 ns)")).toHaveText(String(arrival));
    await expect(field("Read completed (Q1 ns)")).toHaveText(String(complete));
  };

  await mount(page, "waiting", manual, {}, {}, shell);
  await select(manual, "a", "trigger_receive", "trigger_assumption");
  await expect(field("Trigger address")).toHaveText("1");
  await select(manual, "a", "wait_trigger", "trigger_assumption");
  await triggerTimes(4, 8, 12);
  await page.getByRole("button", {name: "Local time basis"}).click();
  await triggerTimes(12, 16, 20);
  await page.getByRole("button", {name: "Aligned time basis"}).click();
  await triggerTimes(4, 8, 12);
  for (const kind of ["q1_issue", "feedback_pop"]) {
    await select(manual, "a", kind, "feedback_input");
    await linqTimes(0, 8, 16);
    await expect(field("Input event")).toHaveText("external-linq:a:0");
  }
  await page.getByRole("button", {name: "Local time basis"}).click();
  await linqTimes(8, 16, 24);
  await page.getByRole("button", {name: "Aligned time basis"}).click();
  await select(manual, "c", "wait_trigger", "trigger_assumption");
  await expect(field("Wait armed (project ns)")).toHaveText("4");
  await expect(field("Trigger status")).toContainText("for address 2 at 4 ns.");
  await expect(field("Trigger arrival (project ns)")).toHaveCount(0);
  await expect(field("Resume (project ns)")).toHaveCount(0);
  await page.getByRole("button", {name: "Configure external inputs"}).click();
  await expect(page.getByLabel("Trigger 1 times (ns)")).toHaveValue("16");
  await expect(page.getByLabel("LINQ receiver 1 arrivals")).toHaveValue("16, 9, 123");

  await mount(page, "waiting", perReceiver, {}, {}, shell);
  await select(perReceiver, "a", "wait_trigger", "trigger_assumption");
  await triggerTimes(12, 16, 20);
  await select(perReceiver, "b", "wait_trigger", "trigger_assumption");
  await triggerTimes(0, 4, 8);
  await select(perReceiver, "b", "q1_issue", "feedback_input");
  await linqTimes(-4, 4, 12);
});

test("Play waiting tooltips use each receiver's time basis and current snapshot", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {manual, perReceiver, completed} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.playback import PlaybackSession",
    "sources={'a':('a.q1asm','origin: wait 12\\nwait_trigger 1,4\\nstop'),'b':('b.q1asm','wait 12\\norigin: wait_trigger 1,4\\nstop'),'readout':('readout.q1asm','origin: wait 4\\nfb_pull_data R0,R1\\nnop\\nstop')}",
    "inputs={'triggers':[{'id':'shot','address':1,'targets':['a','b'],'times_ns':[]}], 'feedback':[{'target':'readout','arrivals':[]}]}",
    "s=PlaybackSession(sources,alignment_mode='manual:-8',external_inputs=inputs)",
    "s.run(budget_ms=1000)", "manual=s.snapshot()", "s.alignment_mode='label:origin'", "per_receiver=s.snapshot()",
    "inputs['triggers'][0]['times_ns']=[16]", "inputs['feedback'][0]['arrivals']=[{'time_ns':16,'id':9,'value':123}]",
    "s.set_external_inputs(inputs)", "s.run(budget_ms=1000)",
    "print(json.dumps({'manual':manual,'perReceiver':per_receiver,'completed':s.snapshot()}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const status = page.locator("#q1timeline-execution-status");
  const run = page.getByRole("button", {name: "Waiting for external input"});
  const basis = (name) => page.getByRole("button", {name: `${name} time basis`}).click();
  const triggerReason = (time) => `No scheduled trigger for address 1 at ${time} ns. Configure external inputs.`;
  const linqReason = "No scheduled arrival supplies a LINQ word for readout. Configure external inputs.";
  const waitSummary = (times) => `3 sequencer(s) waiting. ${times.map(triggerReason).join(" ")} ${linqReason}`;
  const checkTooltips = async (times) => {
    const summary = waitSummary(times);
    await expect(status).toHaveAttribute("title", `Waiting for external input · ${manual.playback.steps} instructions · ${summary}`);
    await expect(status).toHaveAttribute("aria-label", `Waiting for external input · ${manual.playback.steps} instructions · ${summary}`);
    await expect(run).toHaveAttribute("title", `${summary} Change a branch, feedback, or external input assumption to recalculate.`);
    await expect(run).toBeDisabled();
  };
  const sendState = (state) => page.evaluate((state) => window.dispatchEvent(new MessageEvent("message", {
    data: {type: "setExecutionState", state},
  })), state);

  await mount(page, "waiting", manual);
  await checkTooltips([4]);
  await basis("Local");
  await checkTooltips([12]);
  await basis("Aligned");
  await checkTooltips([4]);
  expect(manual.playback.reason).toEqual(waitSummary([12]));

  await mount(page, "waiting", perReceiver);
  await checkTooltips([12, 0]);
  await basis("Local");
  await checkTooltips([12]);
  await basis("Aligned");
  await sendState({...perReceiver.playback, steps: perReceiver.playback.steps + 1});
  await expect(status).toHaveAttribute("title", /at 12 ns\./);
  await expect(run).not.toHaveAttribute("title", /at 0 ns\./);
  await sendState(completed.playback);
  await expect(status).toHaveAttribute("title", /^Completed · \d+ instructions$/);
  await expect(page.getByRole("button", {name: "Execution complete"})).toHaveAttribute("title", /^Execution complete\./);
  await mount(page, "completed", completed);
  await basis("Local");
  await expect(status).toHaveAttribute("title", /^Completed · \d+ instructions$/);
});

test("unresolved trigger time is nominal in standalone and VS Code timelines", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text", "from q1timeline.render.html import render_html",
    "ir=analyze_text('wait_trigger 1,4\\nwait 100\\nstop',file='trigger.q1asm',sequencer_id='seq')",
    "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='normal')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const [trigger, wait, stop] = ir.events.filter((event) => ["wait_trigger", "wait", "stop"].includes(event.kind));
  const inspector = page.locator("#event-inspector-fields");
  const waiting = /Arm 0(?: ns)? → waiting for trigger \(completion unresolved\)/;
  const nominal = /Nominal RT 4(?: ns)? → 104(?: ns)? \(trigger arrival unresolved\)/;
  await page.setContent(html);
  const svgEvent = (item) => page.locator(`#timeline-root > svg [data-event-id="${item.id}"]`);
  await expect(svgEvent(trigger).locator("title")).toContainText(waiting);
  await expect(svgEvent(wait).locator("title")).toContainText(nominal);
  await expect(svgEvent(stop).locator("title")).toContainText(/Nominal RT 104/);
  await svgEvent(trigger).click();
  await expect(inspector).toContainText(waiting);
  await expect(inspector).toContainText("Wait after trigger (ns)4");
  await svgEvent(wait).click();
  await expect(inspector).toContainText(nominal);

  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", ir, {}, {}, shell);
  const block = (item) => page.locator(`.timeline-block[data-event-id="${item.id}"]`);
  await expect(block(trigger)).toHaveAttribute("title", waiting);
  await expect(block(wait)).toHaveAttribute("title", nominal);
  await expect(block(stop)).toHaveAttribute("title", /Nominal RT 104/);
  await block(trigger).click();
  await expect(inspector).toContainText(waiting);
  await expect(inspector).toContainText("Wait after trigger (ns)4");
  await block(wait).click();
  await expect(inspector).toContainText(nominal);
});

test("scheduled LINQ overflow is a linked rejection point on the active clock", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json",
    "from q1timeline.playback import PlaybackSession",
    "from q1timeline.render.html import render_html",
    "inputs = {'feedback': [{'target': 'seq', 'arrivals': [{'time_ns': 8, 'id': 9, 'value': 81}, {'time_ns': 8, 'id': 5, 'value': 25}]}]}",
    "session = PlaybackSession({'seq': ('linq.q1asm', 'fb_pull_data R0,R1\\nstop')}, external_inputs=inputs,",
    "    feedback_queue_capacity=1, alignment_mode='manual:-4')",
    "session.run()",
    "ir = session.snapshot()",
    "print(json.dumps({'ir': ir, 'html': render_html(ir)}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const rejected = ir.events.find((item) => item.kind === "feedback_rejected");
  const rawReason = "LINQ feedback queue overflow at Q1 8 ns (capacity 1).";
  const cause = "LINQ feedback queue overflow (capacity 1).";
  expect(ir.playback.reason).toBe(rawReason);
  const fields = page.locator("#event-inspector-fields");
  const field = (label) => fields.getByText(label, {exact: true}).locator("xpath=following-sibling::dd[1]");
  await page.setContent(html);
  const svgPoint = page.locator(`#timeline-root > svg [data-event-id="${rejected.id}"]`);
  await expect(svgPoint.locator("circle")).toBeVisible();
  await expect(svgPoint.locator("title")).toHaveText("Rejected LINQ arrival · ID 5 · 4 ns");
  const diagnostic = page.locator('.diagnostic[data-diagnostic-category="feedback_queue_overflow"]');
  await expect(diagnostic).toContainText(cause);
  await expect(diagnostic).not.toContainText("8 ns");
  await diagnostic.getByRole("link", {name: "Show event"}).click();
  await expect(svgPoint).toHaveClass(/is-selected/);
  await expect(field("Time range")).toHaveText("4 ns");
  await expect(field("Error")).toHaveText(cause);
  await expect(field("Source")).toHaveText("linq.q1asm:1");
  await expect(field("Input event")).toHaveText("external-linq:seq:1");

  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "error", ir, {}, {viewMode: "normal"}, shell);
  const point = page.locator(`.timeline-point-marker[data-event-id="${rejected.id}"]`);
  const toolbar = page.locator("#q1timeline-execution-status");
  await expect(point).toBeVisible();
  await expect(point).toHaveAttribute("title", `Rejected LINQ arrival · ID 5 · 4 ns\n${cause}`);
  await expect(toolbar).toHaveAttribute("title", `Execution stopped · 0 instructions · ${cause}`);
  await point.click();
  await expect(field("Time range")).toHaveText("4 ns");
  await expect(field("Error")).toHaveText(cause);
  await expect(field("Input event")).toHaveText("external-linq:seq:1");
  await expect(field("Source")).toHaveText("linq.q1asm:1");
  await page.getByRole("button", {name: "Local time basis"}).click();
  await expect(point).toHaveAttribute("title", `Rejected LINQ arrival · ID 5 · 8 ns\n${cause}`);
  await expect(field("Time range")).toHaveText("8 ns");
  await expect(toolbar).toHaveAttribute("title", `Execution stopped · 0 instructions · ${cause}`);
});

test("simulated LINQ rejection displays one active clock and retains Compare Both markers", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {datasets, comparison} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json",
    "from q1timeline.analysis.alignment import align_timelines",
    "from q1timeline.analysis.api import analyze_documents",
    "from q1timeline.ir.serialize import timeline_ir_from_states",
    "from q1timeline.q1asm.parser import parse_q1asm",
    "from q1timeline.render.html import render_html",
    "from q1timeline.simulation.runtime import simulate_programs",
    "rx = parse_q1asm('wait 100\\nstop', file='rx.q1asm')",
    "config = {'startup_delay_ns': 100, 'feedback_queue_capacity': 1, 'inputs': {",
    "    'feedback': [{'target': 'rx', 'time_ns': 120, 'id': i, 'value': i * 11} for i in (1, 2)]}}",
    "states = simulate_programs({'rx': rx}, simulation=config)",
    "datasets = []",
    "for mode, time in ((None, 120), ('first_rt_dequeue', 20), ('manual:-4', 116)):",
    "    if mode:",
    "        align_timelines(states, mode=mode)",
    "    ir = timeline_ir_from_states(states, project={'alignment_mode': mode or 'none'})",
    "    datasets.append({'ir': ir, 'html': render_html(ir), 'mode': mode, 'time': time})",
    "compare = analyze_documents({'a.q1asm': 'set_cond 1,1,0,8\\nfb_com_data 1,42,4\\nstop',",
    "    'b.q1asm': 'wait 100\\nstop'}, execution_mode='simulation', branch_assumptions={'a:set_cond:0:1': 'both'},",
    "    simulation={'startup_delay_ns': 20, 'feedback_queue_capacity': 1,",
    "        'routes': [{'source': 'a', 'target': 'b', 'id': 1, 'latency_ns': 4}],",
    "        'inputs': {'feedback': [{'target': 'b', 'time_ns': 0, 'id': 2, 'value': 8}]}})",
    "print(json.dumps({'datasets': datasets, 'comparison': {'ir': compare, 'html': render_html(compare)}}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const cause = "LINQ feedback queue overflow (capacity 1).";
  const title = (time) => `Rejected LINQ arrival · ID 2 · ${time} ns`;
  const fields = page.locator("#event-inspector-fields");
  const field = (label) => fields.getByText(label, {exact: true}).locator("xpath=following-sibling::dd[1]");
  const diagnostic = page.locator('.diagnostic[data-diagnostic-category="feedback_queue_overflow"]');
  const showEvent = diagnostic.getByRole("link", {name: "Show event"});
  const svg = (id) => page.locator(`#timeline-root > svg [data-event-id="${id}"]`);
  const point = (id) => page.locator(`.timeline-point-marker[data-block-id="${id}"]`);
  const shell = (html) => html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  for (const {ir, html, mode, time} of datasets) {
    const [accepted] = ir.events.filter((event) => event.kind === "feedback_receive");
    const [rejected] = ir.events.filter((event) => event.kind === "feedback_rejected");
    expect([accepted.meta.id, accepted.meta.value, rejected.meta.id, rejected.meta.value, rejected.meta.input_index])
      .toEqual([1, 11, 2, 22, 1]);
    expect(ir.execution.rx).toMatchObject({status: "error", reason: "Feedback queue overflow at 120 ns."});
    expect(ir.diagnostics.find((item) => item.category === "feedback_queue_overflow").message)
      .toBe("Simulation execution error: Feedback queue overflow at 120 ns.");
    expect(ir.events.some((event) => event.kind === "stop")).toBe(false);
    expect(ir.register_history).toEqual([]);
    await page.goto("about:blank");
    await page.setContent(html);
    await expect(svg(rejected.id).locator("circle")).toBeVisible();
    await expect(svg(rejected.id).locator("title")).toHaveText(title(time));
    await expect(diagnostic.locator(".diagnostic-message")).toHaveText(cause);
    await showEvent.click();
    await expect(svg(rejected.id)).toHaveClass(/is-selected/);
    await expect(field("Time range")).toHaveText(`${time} ns`);
    await expect(field("Error")).toHaveText(cause);
    if (!mode) continue;
    await mount(page, "idle", ir, {}, {viewMode: "normal"}, shell(html));
    await expect(point(rejected.id)).toHaveAttribute("title", `${title(time)}\n${cause}`);
    await showEvent.click();
    await expect(point(rejected.id)).toHaveAttribute("aria-selected", "true");
    await expect(field("Time range")).toHaveText(`${time} ns`);
    await expect(field("Error")).toHaveText(cause);
    if (mode === "manual:-4") {
      await page.getByRole("button", {name: "Local time basis"}).click();
      await expect(point(rejected.id)).toHaveAttribute("title", `${title(120)}\n${cause}`);
      await expect(field("Time range")).toHaveText("120 ns");
      await expect(field("Error")).toHaveText(cause);
    }
  }
  const rejection = comparison.ir.events.find((event) => event.kind === "feedback_rejected");
  const comparisonTitle = "Execution error · 4 ns · Feedback queue overflow · LINQ ID 1";
  expect(rejection.meta.branch_comparison_path).toBe("taken");
  await page.goto("about:blank");
  await page.setContent(comparison.html);
  await expect(svg(rejection.id).locator("title")).toHaveText(comparisonTitle);
  await mount(page, "idle", comparison.ir, {}, {viewMode: "normal"}, shell(comparison.html));
  await expect(point(rejection.id)).toHaveAttribute("title", comparisonTitle);
});

test("network conflict diagnostics follow the simulation timeline clock", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const datasets = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json",
    "from q1timeline.analysis.alignment import align_timelines",
    "from q1timeline.ir.serialize import timeline_ir_from_states",
    "from q1timeline.q1asm.parser import parse_q1asm",
    "from q1timeline.render.html import render_html",
    "from q1timeline.simulation.runtime import simulate_programs",
    "source = {'a': 'acquire 0,0,4\\nstop', 'b': 'acquire 0,0,4\\nstop', 'rx': 'wait_trigger 3,4\\nstop'}",
    "programs = {name: parse_q1asm(code, file=f'{name}.q1asm') for name, code in source.items()}",
    "config = {'startup_delay_ns': 100, 'trigger_networks': {'rack': {'grid_epoch_ns': 100}},",
    "    'trigger_routes': [{'source': name, 'target': 'rx', 'address': 3, 'network': 'rack'} for name in ('a', 'b')],",
    "    'inputs': {'acquisitions': [{'sequencer': name, 'acquisition_index': 0, 'bin_index': 0, 'thresholded': 1} for name in ('a', 'b')]}}",
    "states = simulate_programs(programs, simulation=config)",
    "datasets = []",
    "for mode in ('first_rt_dequeue', 'manual:-4'):",
    "    align_timelines(states, mode=mode)",
    "    ir = timeline_ir_from_states(states, project={'alignment_mode': mode})",
    "    datasets.append({'ir': ir, 'html': render_html(ir)})",
    "print(json.dumps(datasets))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const rows = page.locator('[data-diagnostic-category="trigger_network_conflict"] .diagnostic-message');
  const message = (time) => `Trigger network 'rack' has conflicting launch slots at ${time} ns and ${time} ns.`;
  for (const [index, {ir, html}] of datasets.entries()) {
    const aligned = index === 0 ? 28 : 124;
    expect(ir.diagnostics.filter((item) => item.category === "trigger_network_conflict").map((item) => item.message))
      .toEqual([message(128), message(128)]);
    await page.setContent(html);
    await expect(rows).toHaveText([message(aligned), message(aligned)]);
    const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    await mount(page, "idle", ir, {}, {viewMode: "normal"}, shell);
    const overlay = page.locator(".timeline-block[data-diagnostic-message]").first();
    await expect(rows).toHaveText([message(aligned), message(aligned)]);
    await expect(overlay).toHaveAttribute("data-diagnostic-message", message(aligned));
    if (index === 0) {
      await expect(page.getByRole("button", {name: "Local time basis"})).toBeHidden();
    } else {
      await page.getByRole("button", {name: "Local time basis"}).click();
      await expect(rows).toHaveText([message(128), message(128)]);
      await expect(overlay).toHaveAttribute("data-diagnostic-message", message(128));
      await page.getByRole("button", {name: "Aligned time basis"}).click();
      await expect(rows).toHaveText([message(aligned), message(aligned)]);
      await mount(page, "idle", ir, {timelineTimeBasis: "local"}, {viewMode: "normal"}, shell);
      await expect(rows).toHaveText([message(128), message(128)]);
    }
  }
});

test("comparison terminal evidence opens the retained event in both viewers", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const datasets = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json",
    "from q1timeline.analysis.alignment import align_timelines",
    "from q1timeline.ir.serialize import timeline_ir_from_states",
    "from q1timeline.q1asm.parser import parse_q1asm",
    "from q1timeline.render.html import render_html",
    "from q1timeline.simulation.runtime import simulate_programs",
    "cond = 'set_cond 1,1,0,4\\n'",
    "reset = 'set_cond 0,0,0,4\\n'",
    "acquire = 'acquire 0,0,4\\n'",
    "trigger = 'wait_trigger 3,4\\n'",
    "acquisitions = {'acquisitions': {'readout': {'index': 0, 'num_bins': 1}}}",
    "def fixture(sources, branch_owner, terminal_owner, route=False, variant=None):",
    "    programs = {name: parse_q1asm(code, file=f'{name}.q1asm') for name, code in sources.items()}",
    "    options = {'startup_delay_ns': 100}",
    "    if route:",
    "        options.update(trigger_routes=[{'source': 'tx', 'target': 'rx', 'address': 3, 'latency_ns': 8}],",
    "            inputs={'acquisitions': [{'sequencer': 'tx', 'acquisition_index': 0, 'bin_index': 0, 'i': 1}],",
    "                    'triggers': [{'target': 'rx', 'address': 3, 'time_ns': 150}]})",
    "    states = simulate_programs(programs, simulation=options, sequences={name: acquisitions for name in sources},",
    "        branch_assumptions={f'{branch_owner}:set_cond:0:1': 'both'})",
    "    align_timelines(states)",
    "    ir = timeline_ir_from_states(states, project={'alignment_mode': 'first_rt_dequeue'})",
    "    terminal, = [event for event in ir['events'] if event['kind'] == 'branch_terminal' and event['sequencer_id'] == terminal_owner]",
    "    if variant:",
    "        issue, = [event for event in ir['events'] if event['kind'] == 'q1_issue' and event['source']['line'] == 2]",
    "        outcome = terminal['meta']['comparison_terminal']",
    "        result_ids = outcome['source_event_ids'] if variant == 'priority' else []",
    "        outcome['source_event_ids'] = ['missing-evidence', issue['id'], *result_ids]",
    "    return {'ir': ir, 'html': render_html(ir), 'terminal': terminal, 'variant': variant}",
    "gate = {'gate': cond + 'acquire_ttl 0,0,1,4\\nstop'}",
    "print(json.dumps([",
    "    fixture(gate, 'gate', 'gate'),",
    "    fixture({'tx': cond + acquire + reset + 'stop', 'rx': trigger + 'stop'}, 'tx', 'rx', route=True),",
    "    fixture({'tx': acquire + 'stop', 'rx': cond + trigger + reset + 'stop'}, 'rx', 'rx', route=True),",
    "    fixture({'alone': cond + 'fb_acq_tb_mock 1,1,1,4\\n' + reset + 'wait 20\\nstop'}, 'alone', 'alone'),",
    "    fixture(gate, 'gate', 'gate', variant='priority'),",
    "    fixture(gate, 'gate', 'gate', variant='hidden'),",
    "]))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const fields = page.locator("#event-inspector-fields");
  const field = (label) => fields.getByText(label, {exact: true}).locator("xpath=following-sibling::dd[1]");
  const showEvidence = fields.getByRole("button", {name: "Show evidence", exact: true});
  for (const [index, {ir, html, terminal, variant}] of datasets.entries()) {
    const outcome = terminal.meta.comparison_terminal;
    const candidates = (outcome.source_event_ids || []).map((id) => ir.events.find((event) => event.id === id));
    const result = candidates.find((event) => event?.kind === "acquisition_result");
    const issue = candidates.find((event) => event?.kind === "q1_issue");
    expect(result?.sequencer_id).toBe(["gate", "tx", "tx", undefined, "gate", undefined][index]);
    expect(result?.meta.branch_comparison_path).toBe(["taken", "taken", undefined, undefined, "taken", undefined][index]);
    const checkInspector = async (evidence) => {
      await expect(field("Execution status")).toHaveText("blocked");
      await expect(field("Reason")).toHaveText(outcome.reason);
      if (evidence) {
        await showEvidence.click();
        await expect(field("Sequencer")).toHaveText(evidence.sequencer_id);
        await expect(field("Lane")).toHaveText(evidence.lane);
        await expect(showEvidence).toHaveCount(0);
      } else {
        await expect(showEvidence).toHaveCount(0);
      }
    };
    await page.setContent(html);
    for (const mode of ["Normal", "Debug"]) {
      await analysisDetails(page);
      await page.getByRole("button", {name: mode, exact: true}).click();
      const svgEvent = (event) => page.locator(`#timeline-root > svg [data-event-id="${event.id}"]`);
      await svgEvent(terminal).press("Enter");
      const evidence = mode === "Debug" ? issue || result : result || issue;
      await checkInspector(evidence);
      if (evidence && variant === "hidden" && mode === "Normal") await expect(svgEvent(evidence)).toBeHidden();
      else if (evidence) await expect(svgEvent(evidence)).toHaveClass(/is-selected/);
    }
    const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    await mount(page, "idle", ir, {}, {viewMode: "normal"}, shell);
    for (const mode of ["Normal view", "Debug view"]) {
      await page.getByRole("button", {name: mode, exact: true}).click();
      if (variant && mode === "Debug view") await expandQ1Sections(page);
      const block = (event) => page.locator(`.timeline-block[data-block-id="${event.id}"]`);
      await block(terminal).press("Enter");
      const evidence = mode === "Debug view" ? issue || result : result || issue;
      await checkInspector(evidence);
      if (evidence) {
        if (variant === "hidden" && mode === "Normal view") await expect(block(evidence)).toBeHidden();
        else await expect(block(evidence)).toHaveAttribute("aria-selected", "true");
        expect(await page.evaluate(() => window.messages.filter((item) => item.type === "eventClick").at(-1).eventId))
          .toBe(evidence.id);
      }
    }
  }
});

test("external input drafts survive unsolicited host updates and initial errors remain visible", async ({page}) => {
  const trigger = {id: "shot_start", address: 1, targets: ["seq"], times_ns: [1000]};
  const base = {triggers: [trigger], overridden: false, canSave: true};
  await mount(page, "idle", undefined, {}, {externalInputs: {...base, error: "External input calculation failed"}});
  await page.getByRole("button", {name: "Configure external inputs"}).click();
  const form = page.getByRole("form", {name: "External input configuration"});
  await expect(form.getByRole("status")).toHaveText("External input calculation failed");
  const times = form.getByLabel("Trigger 1 times (ns)");
  const summary = page.locator("#q1timeline-external-inputs-summary");
  const update = (state) => page.evaluate((state) => window.dispatchEvent(new MessageEvent("message", {
    data: {type: "externalInputsState", state},
  })), state);
  await times.fill("2500");
  await update({...base, triggers: [{...trigger, times_ns: [1200]}], overridden: true});
  await expect(summary).toContainText("1200 ns · Unsaved changes");
  await expect(times).toHaveValue("2500");
  await expect(times).toBeFocused();
  await expect(form.getByRole("status")).toHaveText("Unapplied changes");
  await expect(form.getByRole("button", {name: "Save to project"})).toBeDisabled();
  await form.getByRole("button", {name: "Apply & rerun"}).click();
  const applied = {...base, triggers: [{...trigger, times_ns: [2500]}], overridden: true};
  await update(applied);
  await expect(form.getByRole("status")).toHaveText("Unsaved changes");
  await times.fill("3000");
  await update(applied);
  await expect(times).toHaveValue("3000");
  await expect(summary).toContainText("2500 ns");
  await form.getByRole("button", {name: "Reset changes"}).click();
  await update(base);
  await expect(times).toHaveValue("1000");
  await expect(form.getByRole("status")).toHaveText("");
});

test("adaptive array plays one shared trigger and reruns changed arrival, address, and reset", async ({page}) => {
  test.setTimeout(60000);
  const repo = path.resolve(__dirname, "../../..");
  const projectFile = path.join(repo, "examples/adaptive-array-demo/q1timeline.yml");
  function analyze(inputs = null, run = true) {
    return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
      "import json,sys", "from q1timeline.playback import PlaybackSession", "from q1timeline.render.html import render_html",
      "s=PlaybackSession.from_project(sys.argv[1])", "inputs=json.loads(sys.argv[2])",
      "if inputs is not None: s.set_external_inputs(inputs)",
      "if sys.argv[3]=='run':",
      "  for _ in range(20):",
      "    s.run(budget_ms=1000,max_steps=10000)",
      "    if s.status not in {'idle','paused'}: break",
      "ir=s.snapshot()", "print(json.dumps({'ir':ir,'inputs':s.external_inputs,'html':render_html(ir,default_mode='debug')}))",
    ].join("\n"), projectFile, JSON.stringify(inputs), run ? "run" : "idle"], {
      cwd: repo, encoding: "utf8", timeout: 15000, maxBuffer: 30 * 1024 * 1024,
      env: {...process.env, PYTHONPATH: path.join(repo, "src")},
    }));
  }
  async function show(data, saved = {}) {
    const shell = data.html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    await mount(page, data.ir.playback.status, data.ir, saved, {parameterProjectFile: projectFile,
      externalInputs: {...data.inputs, overridden: data.ir.playback.external_inputs.overridden, canSave: true}}, shell);
  }
  await show(analyze(null, false));
  await page.getByRole("button", {name: "Run execution", exact: true}).click();
  expect(await page.evaluate(() => window.messages.at(-1))).toEqual({type: "executionControl", action: "play"});
  const first = analyze();
  expect(first.ir.playback.status).toBe("completed");
  const waits = first.ir.events.filter((item) => item.kind === "wait_trigger");
  expect(waits).toHaveLength(17);
  expect(new Set(waits.map((item) => item.meta.trigger_assumption.event_id)).size).toBe(1);
  expect(waits.every((item) => item.meta.trigger_assumption.arrival_ns === 1000)).toBe(true);
  await show(first);
  await expect(page.getByRole("button", {name: "Execution complete", exact: true})).toBeDisabled();
  const summary = page.locator("#q1timeline-external-inputs-summary");
  await expect(summary).toContainText("17 sequencers · 1000 ns");
  await page.evaluate((eventId) => window.dispatchEvent(new MessageEvent("message", {
    data: {type: "highlightEventIds", highlightEventIds: [eventId]},
  })), waits[0].id);
  await expect(page.locator("#event-inspector-fields")).toContainText("≈ Scenario input");
  await expect(page.locator("#event-inspector-fields")).toContainText(waits[0].meta.trigger_assumption.event_id);
  await page.getByRole("button", {name: "Configure external inputs"}).click();
  const form = page.getByRole("form", {name: "External input configuration"});
  await form.getByLabel("Trigger 1 times (ns)").fill("1500");
  await form.getByRole("button", {name: "Apply & rerun"}).click();
  const edit = await page.evaluate(() => window.messages.findLast((message) => message.type === "setExternalInputs"));
  const changed = analyze({triggers: edit.triggers});
  expect(changed.ir.playback.status).toBe("completed");
  const ends = (ir) => Object.fromEntries(ir.events.filter((item) => item.kind === "stop").map((item) => [item.sequencer_id, item.t0.value]));
  const before = ends(first.ir), after = ends(changed.ir);
  expect(Object.keys(after)).toHaveLength(17);
  for (const [sequencer, end] of Object.entries(before)) expect(after[sequencer]).toBe(end + 500);
  await show(changed, await page.evaluate(() => window.getSavedTimelineState()));
  await expect(form).toBeVisible();
  await expect(summary).toContainText("1500 ns · Unsaved changes");
  await expect(form.getByRole("button", {name: "Save to project"})).toBeEnabled();
  await form.getByLabel("Trigger 1 address").fill("2");
  await form.getByRole("button", {name: "Apply & rerun"}).click();
  const mismatch = await page.evaluate(() => window.messages.findLast((message) => message.type === "setExternalInputs"));
  const waiting = analyze({triggers: mismatch.triggers});
  expect(waiting.ir.playback.status).toBe("waiting");
  expect(waiting.ir.events.filter((item) => item.kind === "stop")).toHaveLength(0);
  await show(waiting, await page.evaluate(() => window.getSavedTimelineState()));
  await expect(page.getByRole("button", {name: "Waiting for external input", exact: true})).toBeDisabled();
  await expect(page.getByRole("button", {name: "Configure external inputs"})).toBeEnabled();
  await expect(summary).toContainText("address 2");
  await form.getByRole("button", {name: "Reset changes"}).click();
  expect(await page.evaluate(() => window.messages.at(-1))).toEqual({type: "resetExternalInputs"});
  const reset = analyze();
  expect(ends(reset.ir)).toEqual(before);
  await show(reset, await page.evaluate(() => window.getSavedTimelineState()));
  await expect(summary).toContainText("1000 ns");
  await expect(summary).not.toContainText("Unsaved changes");
  await expect(form.getByLabel("Trigger 1 address")).toHaveValue("TRIGGER_INDEX");
  await page.screenshot({path: test.info().outputPath("adaptive-array-shared-trigger-rerun.png"), fullPage: true});
});

test("restores Q1ASM view state only for the same project", async ({page}) => {
  const ir = {sequencers: [{id: "seq", module: "QCM0"}], events: [
    event("play", "play", 0, 1000), event("stop", "stop", 1000, 1000),
  ]};
  const projectA = {parameterProjectFile: "C:/project-a/q1timeline.yml"};
  await mount(page, "idle", ir, {}, projectA);
  const fullWindow = await page.locator("#q1timeline-time-window").getAttribute("title");
  await page.getByRole("button", {name: "Open QCM0: 1 sequencer", exact: true}).click();
  await page.getByRole("button", {name: "Inspect sequencer seq", exact: true}).click();
  const time = page.getByRole("spinbutton", {name: "Selected time in nanoseconds"});
  await time.fill("600");
  await time.press("Enter");
  await page.getByRole("button", {name: "Zoom in", exact: true}).click();
  await expect.poll(() => page.evaluate(() => window.getSavedTimelineState().hardwareState?.time)).toBe(600);
  const saved = await page.evaluate(() => window.getSavedTimelineState());
  const zoomedWindow = await page.locator("#q1timeline-time-window").getAttribute("title");
  expect(zoomedWindow).not.toBe(fullWindow);

  await mount(page, "idle", ir, saved, projectA);
  await expect(page.locator("#q1timeline-hardware")).toHaveAttribute("data-navigation-level", "detail");
  await expect(time).toHaveValue("600");
  await expect(page.locator("#q1timeline-time-window")).toHaveAttribute("title", zoomedWindow);

  const legacy = {...saved};
  delete legacy.timelineProjectFile;
  await mount(page, "idle", ir, legacy, projectA);
  await expect(page.locator("#q1timeline-hardware")).toHaveAttribute("data-navigation-level", "detail");
  await expect(time).toHaveValue("600");
  await expect(page.locator("#q1timeline-time-window")).toHaveAttribute("title", zoomedWindow);

  const refreshed = await page.evaluate(() => window.getSavedTimelineState());
  await mount(page, "idle", ir, refreshed, {parameterProjectFile: "C:/project-b/q1timeline.yml"});
  await expect(page.locator("#q1timeline-hardware")).toHaveAttribute("data-navigation-level", "modules");
  await expect(time).toHaveValue("0");
  await expect(page.locator("#q1timeline-time-window")).toHaveAttribute("title", fullWindow);
});

test("parameter errors restore keyboard retry without taking focus from another control", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json,tempfile", "from pathlib import Path", "from q1timeline.playback import PlaybackSession",
    "from q1timeline.render.html import render_html", "directory=tempfile.TemporaryDirectory()", "root=Path(directory.name)",
    'root.joinpath("parameters.q1asm").write_text("wait {WAIT}\\nstop\\n")',
    'root.joinpath("params.json").write_text(json.dumps({"WAIT":64}))',
    'root.joinpath("q1timeline.yml").write_text("params: {file: params.json}\\nalignment: {mode: none}\\nsequencers: [{id: seq, file: parameters.q1asm}]\\n")',
    'session=PlaybackSession.from_project(root/"q1timeline.yml")', "session.run(budget_ms=1000,max_steps=100)",
    "ir=session.snapshot()", "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "completed", ir, {parametersOpen: true}, {parameterProjectFile: "C:/project/q1timeline.yml"}, shell);
  await page.getByRole("button", {name: "Edit WAIT", exact: true}).click();
  const input = page.getByRole("textbox", {name: "WAIT JSON value", exact: true});
  await expect(input).toBeFocused();
  await input.fill("invalid");
  await input.press("Enter");
  await expect(input).toBeDisabled();
  // Flush the disabled input's browser blur before the asynchronous host reply.
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(input).not.toBeFocused();
  const error = (message) => page.evaluate((message) => window.dispatchEvent(new MessageEvent("message", {
    data: {type: "analysisParameterError", name: "WAIT", message},
  })), message);
  await error("Enter a valid JSON value.");
  await expect(input).toBeEnabled();
  await expect(input).toBeFocused();
  await expect(input).toHaveValue("invalid");
  await expect(page.locator(".parameter-error")).toHaveText("Enter a valid JSON value.");
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type("128");
  await page.keyboard.press("Enter");
  await expect(input).toBeDisabled();
  const requests = () => page.evaluate(() => window.messages.filter((message) => message.type === "setAnalysisParameter"));
  expect((await requests()).map((message) => message.valueJson)).toEqual(["invalid", "128"]);
  const zoom = page.getByRole("button", {name: "Zoom in", exact: true});
  await zoom.focus();
  await error("Analysis could not complete.");
  await expect(zoom).toBeFocused();
  await expect(input).toBeEnabled();
  await expect(input).toHaveValue("128");
  await expect(page.locator(".parameter-status")).toHaveText("");
  await expect(page.locator(".parameter-value > code")).toHaveText("64");
  expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
  await page.screenshot({path: test.info().outputPath("parameter-keyboard-error-retry.png"), fullPage: true});
});

test("reset errors keep the overridden value and restore keyboard retry", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json,tempfile", "from pathlib import Path", "from q1timeline.playback import PlaybackSession",
    "from q1timeline.render.html import render_html", "directory=tempfile.TemporaryDirectory()", "root=Path(directory.name)",
    'root.joinpath("parameters.q1asm").write_text("wait {WAIT}\\nstop\\n")',
    'root.joinpath("params.json").write_text(json.dumps({"WAIT":64}))',
    'root.joinpath("q1timeline.yml").write_text("params: {file: params.json}\\nalignment: {mode: none}\\nsequencers: [{id: seq, file: parameters.q1asm}]\\n")',
    'session=PlaybackSession.from_project(root/"q1timeline.yml",params={"WAIT":128})', "session.run(budget_ms=1000,max_steps=100)",
    "ir=session.snapshot()", "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "completed", ir, {parametersOpen: true},
    {parameterProjectFile: "C:/project/q1timeline.yml", parameterOverrideNames: ["WAIT"]}, shell);
  const edit = page.getByRole("button", {name: "Edit WAIT", exact: true});
  await edit.click();
  const input = page.getByRole("textbox", {name: "WAIT JSON value", exact: true});
  await expect(input).toHaveValue("128");
  await input.fill("256");
  await input.press("Escape");
  await expect(edit).toBeFocused();
  const requests = () => page.evaluate(() => window.messages.filter((message) => /AnalysisParameter$/.test(message.type)));
  expect(await requests()).toEqual([]);
  const reset = page.getByRole("button", {name: "Remove temporary override for WAIT", exact: true});
  await reset.focus();
  await reset.press("Enter");
  await expect(reset).toBeDisabled();
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const error = () => page.evaluate(() => window.dispatchEvent(new MessageEvent("message", {
    data: {type: "analysisParameterError", name: "WAIT", message: "Parameter change was not applied: environment unavailable"},
  })));
  await error();
  await expect(reset).toBeEnabled();
  await expect(reset).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(reset).toBeDisabled();
  expect((await requests()).map((message) => message.type)).toEqual(["resetAnalysisParameter", "resetAnalysisParameter"]);
  const zoom = page.getByRole("button", {name: "Zoom in", exact: true});
  await zoom.focus();
  await error();
  await expect(zoom).toBeFocused();
  await expect(reset).toBeEnabled();
  await expect(page.locator(".parameter-editor")).toBeHidden();
  await expect(page.locator(".parameter-status")).toHaveText("");
  await expect(page.locator(".parameter-value > code")).toHaveText("128");
  await expect(page.locator(".parameter-base")).toHaveText("← base 64");
  expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
  await page.screenshot({path: test.info().outputPath("parameter-reset-keyboard-retry.png"), fullPage: true});
});

test("successful parameter reanalysis keeps the source filter only for the same project", async ({page}) => {
  const fs = require("node:fs"), repo = path.resolve(__dirname, "../../..");
  function project(name) {
    const directory = test.info().outputPath(name);
    fs.mkdirSync(directory, {recursive: true});
    fs.writeFileSync(path.join(directory, "seq.q1asm"), "move 7,R0\nset_freq 100\nupd_param 4\nwait {WAIT}\nstop\n");
    fs.writeFileSync(path.join(directory, "params.json"), JSON.stringify({WAIT: 64}));
    const file = path.join(directory, "q1timeline.yml");
    fs.writeFileSync(file, "params: {file: params.json}\nalignment: {mode: none}\nsequencers: [{id: seq, file: seq.q1asm, module: QCM0}]\n");
    return file;
  }
  function analyze(projectFile, overrides = {}) {
    return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
      "import json,sys", "from q1timeline.playback import PlaybackSession", "from q1timeline.render.html import render_html",
      "session=PlaybackSession.from_project(sys.argv[1],params=json.loads(sys.argv[2]))", "session.run(budget_ms=1000,max_steps=100)",
      "ir=session.snapshot()", "assert ir['playback']['status']=='completed'",
      "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
    ].join("\n"), projectFile, JSON.stringify(overrides)], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  }
  const projectA = project("project-a");
  async function show(data, saved, projectFile = projectA) {
    const shell = data.html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    await mount(page, "completed", data.ir, saved, {parameterProjectFile: projectFile,
      parameterOverrideNames: data.ir.analysis.parameters.filter((item) => item.overridden).map((item) => item.name)}, shell);
  }
  const first = analyze(projectA);
  await show(first, {parametersOpen: true, hardwareState: {level: "detail", sequencerId: "seq", time: 16}});
  const stop = () => page.locator(`.timeline-block[data-event-id="${first.ir.events.find((event) => event.kind === "stop").id}"]`).first();
  await expect(stop()).toBeVisible();
  await page.getByRole("button", {name: "Search parameters", exact: true}).click();
  const parameterSearch = page.getByRole("searchbox", {name: "Search parameters by name", exact: true});
  await parameterSearch.fill("WAIT");
  await analysisDetails(page);
  const filter = page.getByRole("searchbox", {name: "Filter source or kind", exact: true});
  await filter.fill("wait");
  await expect(stop()).toBeHidden();
  const edit = page.getByRole("button", {name: "Edit WAIT", exact: true});
  await edit.click();
  const input = page.getByRole("textbox", {name: "WAIT JSON value", exact: true});
  await input.fill("999"); await input.press("Escape"); await expect(edit).toBeFocused();
  expect(await page.evaluate(() => window.messages.filter((message) => /AnalysisParameter$/.test(message.type)))).toEqual([]);
  await edit.click(); await input.fill("128"); await input.press("Enter"); await expect(input).toBeDisabled();
  const request = await page.evaluate(() => window.messages.find((message) => message.type === "setAnalysisParameter"));
  const updated = analyze(request.projectFile, {[request.name]: JSON.parse(request.valueJson)});
  await show(updated, await page.evaluate(() => window.getSavedTimelineState()));
  await expect(page.locator(".parameter-value > code")).toHaveText("128");
  await expect(page.locator(".parameter-base")).toHaveText("← base 64");
  await expect(parameterSearch).toHaveValue("WAIT");
  await expect(page.getByRole("spinbutton", {name: "Selected time in nanoseconds", exact: true})).toHaveValue("16");
  await expect(filter).toHaveValue("wait");
  await expect(stop()).toBeHidden();
  await filter.clear();
  await expect(stop()).toBeVisible();
  await show(updated, await page.evaluate(() => window.getSavedTimelineState()));
  await expect(filter).toHaveValue(""); await expect(stop()).toBeVisible();
  await filter.fill("wait");
  const projectB = project("project-b");
  await show(analyze(projectB), await page.evaluate(() => window.getSavedTimelineState()), projectB);
  await analysisDetails(page);
  await expect(filter).toHaveValue(""); await expect(stop()).toBeVisible();
  await page.screenshot({path: test.info().outputPath("parameter-success-filter-project-scope.png"), fullPage: true});
});

test("hardware register writers keep the exact instruction inspector inside a collapsed loop", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.playback import PlaybackSession", "from q1timeline.render.html import render_html",
    "session=PlaybackSession.from_project('examples/concrete-register-execution/q1timeline.yml')",
    "session.run(budget_ms=1000,max_steps=100)", "ir=session.snapshot()", "assert ir['playback']['status']=='completed'",
    "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "completed", ir, {hardwareState: {level: "detail", sequencerId: "counter", time: 140}}, {}, shell);
  const time = page.getByRole("spinbutton", {name: "Selected time in nanoseconds", exact: true});
  const register = (name) => page.locator(`[data-register="${name}"] strong`);
  const detail = page.locator('[data-value="register-detail"]');
  const sourceDetail = page.locator('[data-value="selection-detail"]');
  const source = page.getByRole("button", {name: "Open source", exact: true});
  const inspector = page.locator("#event-inspector-fields");
  const loop = page.getByRole("button", {name: "Executed loop options", exact: true});
  await expect(loop).toHaveAttribute("data-state", "1");
  await page.locator('[data-register="R2"]').click();
  await expect(register("R2")).toHaveText("3");
  const file = ir.register_history.find((entry) => entry.register === "R2").source.file;
  await expect(sourceDetail).toHaveText(`${file}:10`); await source.click();
  const writers = ir.register_history.filter((entry) => entry.register === "R1").reverse();
  expect(writers.map((entry) => [entry.time.value, entry.value.value, entry.source.line]))
    .toEqual([[120,3,6],[76,2,6],[32,1,6],[16,0,4]]);
  for (const writer of writers) {
    await detail.locator('[data-writer-register="R1"]').click();
    const event = ir.events.find((entry) => entry.id === writer.event_id);
    await expect(time).toHaveValue(String(writer.time.value));
    await expect(register("R1")).toHaveText(String(writer.value.value));
    await expect(sourceDetail).toHaveText(`${file}:${writer.source.line}`);
    await expect(inspector).toContainText(`${event.t0.value} -> ${event.t1.value}`);
    await expect(inspector).toContainText(event.label);
    await source.click(); await expect(time).toHaveValue(String(writer.time.value));
    await expect(loop).toHaveAttribute("data-state", "1");
  }
  await expect(page.locator('[data-action="register-previous"]')).toBeDisabled();
  await page.locator('[data-control="register-format"]').selectOption("hex");
  await page.getByRole("button", {name: "Next change ›", exact: true}).click();
  await expect(time).toHaveValue("32"); await expect(register("R1")).toHaveText("0x00000001");
  await expect(inspector).toContainText("16 -> 28");
  await page.getByRole("button", {name: "Next change ›", exact: true}).click();
  await expect(time).toHaveValue("76"); await expect(register("R1")).toHaveText("0x00000002");
  await expect(inspector).toContainText("60 -> 72");
  await page.getByRole("button", {name: "‹ Previous change", exact: true}).click();
  await expect(time).toHaveValue("32"); await expect(register("R1")).toHaveText("0x00000001");
  await expect(inspector).toContainText("16 -> 28");
  await expect(loop).toHaveAttribute("data-state", "1");
  const messages = await page.evaluate(() => window.messages);
  expect(messages.filter((message) => message.type === "sourceClick").map((message) => message.line)).toEqual([10,6,6,6,4]);
  expect(messages.filter((message) => message.type === "eventClick")).toEqual([]);
  await page.screenshot({path: test.info().outputPath("register-first-loop-writer-inspector.png"), fullPage: true});
});

test("concrete counter preview folds RT repetitions and keeps all register changes before Run", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const ir = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", [
    "-m", "q1lens", "q1timeline", "analyze", "--project", "examples/concrete-register-execution/q1timeline.yml",
    "--out", test.info().outputPath("counter.json"), "--format", "vscode-json", "--include-timeline-ir", "--no-render",
  ], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}})).timeline_ir;
  expect(ir.execution.counter.mode).toBe("concrete");
  await mount(page, "idle", ir, {hardwareState: {level: "detail", sequencerId: "counter", time: 16}}, {viewMode: "normal"});
  const rt = page.locator(".timeline-row-runtime");
  const loop = rt.getByRole("button", {name: "Executed loop options", exact: true});
  await expect(page.getByRole("button", {name: "Run execution", exact: true})).toBeEnabled();
  await expect(loop.locator(".loop-range-label")).toHaveText("×3");
  const waits = rt.locator(ir.events.filter((event) => event.kind === "wait")
    .map((event) => `.timeline-block[data-event-id="${event.id}"]`).join(", "));
  await expect(waits).toHaveCount(1);
  await expect(page.locator(".ruler .q1timeline-wait-fold")).toHaveCount(0);
  const repeatFold = rt.locator(".q1timeline-playback-fold");
  await expect(repeatFold).toHaveAttribute("data-time-start", "104");
  await expect(repeatFold).toHaveAttribute("data-time-end", "304");
  await expect(page.locator(".ruler .q1timeline-playback-fold .q1timeline-fold-duration")).toHaveText("200 ns");
  const [waitBounds, loopBounds, foldBounds] = await Promise.all([
    waits.boundingBox(), loop.boundingBox(), repeatFold.boundingBox(),
  ]);
  expect(loopBounds.x).toBeCloseTo(waitBounds.x, 0);
  expect(loopBounds.x + loopBounds.width).toBeCloseTo(foldBounds.x, 0);

  const register = page.locator('[data-register="R1"]');
  await register.click();
  await expect(register.locator("strong")).toHaveText("0");
  const next = page.getByRole("button", {name: "Next change ›", exact: true});
  for (const value of ["1", "2", "3"]) {
    await next.click();
    await expect(register.locator("strong")).toHaveText(value);
  }
  await expect(next).toBeDisabled();
  await expect(waits).toHaveCount(1);
  await loop.click();
  await page.getByRole("button", {name: "Expand executed iterations", exact: true}).click();
  await expect(waits).toHaveCount(3);
  await expect(repeatFold).toHaveCount(0);
  await expect(loop.locator(".loop-range-label")).toBeHidden();
  await loop.click();
  await page.getByRole("button", {name: "Collapse executed iterations", exact: true}).click();
  await expect(waits).toHaveCount(1);
  await expect(loop.locator(".loop-range-label")).toBeVisible();
  await expect(register.locator("strong")).toHaveText("3");
  expect(await page.evaluate(() => window.messages.filter((message) => message.type === "executionControl"))).toEqual([]);
});

test("keeps a source filter active across layout switches and wait folding", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json,re", "from q1timeline.analysis.interpreter import interpret_program",
    "from q1timeline.analysis.alignment import align_timelines",
    "from q1timeline.q1asm.parser import parse_q1asm_file", "from q1timeline.ir.serialize import timeline_ir_from_states",
    "from q1timeline.render.html import render_html",
    "state=interpret_program(parse_q1asm_file('examples/set-cond-review/01-disabled/disabled.q1asm'),sequencer_id='disabled')",
    "align_timelines([state],mode='none')",
    "html=render_html(timeline_ir_from_states([state]),default_mode='debug')",
    "ir=json.loads(re.search(r'<script id=\"timeline-ir\" type=\"application/json\">(.*?)</script>',html,re.S).group(1))",
    "print(json.dumps({'ir':ir,'html':html}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", ir, {}, {}, shell);
  const wait = ir.events.find((item) => item.kind === "wait");
  const waitBlock = page.locator(`.timeline-block[data-event-id="${wait.id}"]`);
  await expect(waitBlock).toBeVisible();
  await analysisDetails(page);
  const filter = page.getByRole("searchbox", {name: "Filter source or kind"});
  await filter.fill("upd_param");
  for (const layout of ["Instruction", "Time scale"]) {
    await page.getByRole("button", {name: layout, exact: true}).click();
    await expect(filter).toHaveValue("upd_param");
    await expect(waitBlock).toHaveClass(/is-filtered/);
    await expect(waitBlock).toBeHidden();
    expect(await page.locator(".timeline-block.event:not(.is-filtered)").count()).toBeGreaterThan(0);
  }
  for (let count = 0; count < 2; count += 1) {
    await page.locator("#q1timeline-wait-fold-button").click();
    await expect(waitBlock).toHaveClass(/is-filtered/);
    await expect(waitBlock).toBeHidden();
  }
  await page.screenshot({path: test.info().outputPath("retained-source-filter.png"), fullPage: true});
  await filter.fill("");
  await expect(waitBlock).not.toHaveClass(/is-filtered/);
  await expect(waitBlock).toBeVisible();
});

test("adaptive array source selection reveals wait 80 instead of the preceding trigger", async ({page}) => {
  test.setTimeout(60000);
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import contextlib,io,json,tempfile", "from pathlib import Path",
    "from q1timeline.cli import main", "from q1timeline.render.html import render_html",
    "with tempfile.TemporaryDirectory() as tmp:",
    "  output=Path(tmp)/'timeline.json'",
    "  with contextlib.redirect_stdout(io.StringIO()):",
    "    main(['analyze','--project','examples/adaptive-array-demo/q1timeline.yml','--out',str(output),'--no-render'])",
    "  ir=json.loads(output.read_text(encoding='utf-8'))",
    "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 15000, maxBuffer: 30 * 1024 * 1024,
    env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", ir, {expandedQ1IssueSequencers: []}, {}, shell);
  for (const layout of ["Time scale", "Instruction"]) {
    await page.getByRole("button", {name: layout, exact: true}).click();
    for (const sequencer of ["drive_q0", "arbiter"]) {
      const events = ir.events.filter((item) => item.sequencer_id === sequencer && item.source?.line === 18);
      const wait = events.find((item) => item.kind === "wait");
      const trigger = ir.events.find((item) => item.sequencer_id === sequencer && item.kind === "wait_trigger");
      await page.evaluate((ids) => window.dispatchEvent(new MessageEvent("message", {
        data: {type: "highlightEventIds", highlightEventIds: ids},
      })), events.map((item) => item.id));
      await expect(page.locator(`.timeline-block[data-event-id="${wait.id}"]`)).toHaveAttribute("aria-selected", "true");
      await expect(page.locator(`.timeline-block[data-event-id="${trigger.id}"]`)).not.toHaveAttribute("aria-selected", "true");
      await expect(page.locator("#event-inspector-fields")).toContainText(`${sequencer}.q1asm:18`);
      await expect(page.getByRole("spinbutton", {name: "Selected time in nanoseconds"})).toHaveValue(String(wait.t0.value));
    }
  }
  await page.screenshot({path: test.info().outputPath("adaptive-array-wait-source-selection.png"), fullPage: true});
});

test("source and Q1 commands navigate to the related RT start in both layouts", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text", "from q1timeline.render.html import render_html",
    'ir=analyze_text("move 1,R0\\nnop\\nwait 100\\nset_freq 100\\nadd R0,1,R0\\nnop\\nwait 80\\nupd_param 4\\nstop\\n",file="setup.q1asm",sequencer_id="seq")',
    "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", ir, {expandedQ1IssueSequencers: ["seq"]}, {}, shell);
  const waits = ir.events.filter((item) => item.kind === "wait");
  const application = ir.events.find((item) => item.kind === "upd_param");
  const rtTarget = (line) => line === 4 ? application : waits[line >= 4 ? 1 : 0];
  const prep = page.getByRole("button", {name: "seq: RT preparation", exact: true});
  await expect(prep).toHaveText("prep");
  expect((await prep.boundingBox()).width).toBeLessThan(45);
  for (const layout of ["Time scale", "Instruction"]) {
    await page.getByRole("button", {name: layout, exact: true}).click();
    for (const line of [1, 2, 4, 5, 6, 7, 3]) {
      const events = ir.events.filter((item) => item.source?.line === line);
      await page.evaluate((ids) => window.dispatchEvent(new MessageEvent("message", {
        data: {type: "highlightEventIds", highlightEventIds: ids},
      })), events.map((item) => item.id));
      const target = rtTarget(line);
      await expect(page.locator(`.timeline-block[data-event-id="${target.id}"]`)).toHaveAttribute("aria-selected", "true");
      await expect(prep).toHaveAttribute("aria-selected", "false");
      await expect(page.getByRole("spinbutton", {name: "Selected time in nanoseconds"})).toHaveValue(String(target.t0.value));
    }
    for (const line of [1, 2, 4, 5, 6, 7, 3]) {
      const issue = ir.events.find((item) => item.kind === "q1_issue" && item.source?.line === line);
      const block = page.locator(`.timeline-block[data-event-id="${issue.id}"]`);
      await block.evaluate((node) => node.click());
      await expect(block).toHaveAttribute("aria-selected", "true");
      await expect(page.locator("#event-inspector-fields")).toContainText(`setup.q1asm:${line === 4 ? application.source.line : line}`);
      await expect(page.getByRole("spinbutton", {name: "Selected time in nanoseconds"})).toHaveValue(String(rtTarget(line).t0.value));
    }
  }
  const setupIds = ir.events.filter((item) => item.source?.line === 2).map((item) => item.id);
  await page.evaluate((ids) => window.dispatchEvent(new MessageEvent("message", {
    data: {type: "highlightEventIds", highlightEventIds: ids},
  })), setupIds);
  for (const layout of ["Time scale", "Instruction"]) {
    await page.getByRole("button", {name: layout, exact: true}).click();
    await expect(page.locator(`.timeline-block[data-event-id="${waits[0].id}"]`)).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("spinbutton", {name: "Selected time in nanoseconds"})).toHaveValue(String(waits[0].t0.value));
  }
  await page.screenshot({path: test.info().outputPath("next-rt-source-navigation.png"), fullPage: true});
});

test("equal-duration conditional output stays uncertain while its RT block and following time remain selectable", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text", "from q1timeline.render.html import render_html",
    'ir=analyze_text("wait 100\\nset_cond 1,1,0,40\\nplay 0,1,40\\nset_cond 0,0,0,4\\nwait 100\\nstop\\n",file="conditional.q1asm",sequencer_id="seq")',
    "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", ir, {expandedQ1IssueSequencers: []}, {}, shell);
  const conditional = ir.events.find((item) => item.kind === "unknown_region");
  expect(conditional.meta.output_state_unknown).toBe(true);
  expect(conditional.confidence).toBe("unknown");
  expect(conditional.t0.value).toBe(100);
  expect(conditional.t1.value).toBe(140);
  for (const layout of ["Time scale", "Instruction"]) {
    await page.getByRole("button", {name: layout, exact: true}).click();
    for (const line of [3, 5]) {
      const events = ir.events.filter((item) => item.source?.line === line);
      const target = events.find((item) => item.kind !== "q1_issue");
      await page.evaluate((ids) => window.dispatchEvent(new MessageEvent("message", {
        data: {type: "highlightEventIds", highlightEventIds: ids},
      })), events.map((item) => item.id));
      await expect(page.locator(`.timeline-block[data-event-id="${target.id}"]`)).toHaveAttribute("aria-selected", "true");
      await expect(page.locator("#event-inspector-fields")).toContainText(`conditional.q1asm:${line}`);
      await expect(page.getByRole("spinbutton", {name: "Selected time in nanoseconds"})).toHaveValue(String(target.t0.value));
    }
  }
});

test("zero-mask conditions show executed or skipped RT blocks without inventing output application", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  for (const operator of [0, 1]) {
    const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
      "import json,sys", "from q1timeline.analysis.api import analyze_text", "from q1timeline.render.html import render_html",
      'source="set_mrk 3\\nset_cond 1,0,"+sys.argv[1]+",20\\nplay 0,1,40\\nset_cond 0,0,0,4\\nupd_param 4\\nstop\\n"',
      'ir=analyze_text(source,file="constant-condition.q1asm",sequencer_id="seq")',
      "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
    ].join("\n"), String(operator)], {cwd: repo, encoding: "utf8", timeout: 10000,
      env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
    const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    await mount(page, "idle", ir, {expandedQ1IssueSequencers: []}, {}, shell);
    const executed = operator === 1;
    const target = ir.events.find((item) => item.source?.line === 3 && item.kind === (executed ? "play" : "conditional_skip"));
    expect(target.t0.value).toBe(0);
    expect(target.t1.value).toBe(executed ? 40 : 20);
    if (!executed) {
      expect(ir.events.some((item) => item.source?.line === 3 && item.kind === "play")).toBe(false);
      expect(Object.keys(target.meta.applied_state || {})).toHaveLength(0);
    }
    const firstMarker = ir.events.find((item) => item.kind === "marker_state");
    expect(firstMarker.t0.value).toBe(executed ? 0 : 20);
    for (const layout of ["Time scale", "Instruction"]) {
      await page.getByRole("button", {name: layout, exact: true}).click();
      const ids = ir.events.filter((item) => item.source?.line === 3).map((item) => item.id);
      await page.evaluate((ids) => window.dispatchEvent(new MessageEvent("message", {
        data: {type: "highlightEventIds", highlightEventIds: ids},
      })), ids);
      await expect(page.locator(`.timeline-block[data-event-id="${target.id}"]`)).toHaveAttribute("aria-selected", "true");
      await expect(page.locator("#event-inspector-fields")).toContainText("constant-condition.q1asm:3");
      await expect(page.getByRole("spinbutton", {name: "Selected time in nanoseconds"})).toHaveValue("0");
    }
  }
});

test("an empty source highlight clears event and range selection without moving the hardware cursor or viewport", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text", "from q1timeline.render.html import render_html",
    'ir=analyze_text("play 0,0,4\\nwait 100\\nstop\\n",file="source.q1asm",sequencer_id="seq")',
    "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", ir, {}, {}, shell);
  const play = ir.events.find((item) => item.kind === "play");
  const wait = ir.events.find((item) => item.kind === "wait");
  const time = page.getByRole("spinbutton", {name: "Selected time in nanoseconds"});
  const highlight = (data) => page.evaluate((data) => window.dispatchEvent(new MessageEvent("message", {
    data: {type: "highlightEventIds", ...data},
  })), data);
  const assertCleared = async () => {
    await expect(page.locator(".timeline-block.is-selected")).toHaveCount(0);
    await expect(page.locator('.timeline-block[aria-selected="true"]')).toHaveCount(0);
    await expect(page.locator(".time-selection")).toHaveCount(0);
    await expect(page.locator("#q1timeline-zoom-event-button")).toBeDisabled();
    await expect(page.locator("#q1timeline-zoom-selection-button")).toBeDisabled();
    await expect(page.locator("#event-inspector-fields dd")).toHaveText("None");
  };
  for (const selection of ["event", "range"]) {
    if (selection === "event") await page.locator(`.timeline-block[data-event-id="${play.id}"]`).click();
    else {
      await highlight({highlightEventIds: [play.id, wait.id], zoomToRange: true,
        sourceRange: {file: play.source.file, startLine: 1, endLine: 2}});
      await expect(page.locator(".time-selection").first()).toBeVisible();
    }
    await time.fill("32");
    await time.press("Enter");
    const windowBefore = await page.locator("#q1timeline-time-window").getAttribute("title");
    await highlight({highlightEventIds: []});
    await assertCleared();
    await expect(time).toHaveValue("32");
    await expect(page.locator("#q1timeline-time-window")).toHaveAttribute("title", windowBefore);
    for (const layout of ["Instruction", "Time scale"]) {
      await page.getByRole("button", {name: layout, exact: true}).click();
      await assertCleared();
    }
    await page.getByRole("button", {name: "Zoom in", exact: true}).click();
    await assertCleared();
    await expect(time).toHaveValue("32");
    await highlight({highlightEventIds: [play.id]});
    await expect(page.locator(`.timeline-block[data-event-id="${play.id}"]`)).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#q1timeline-zoom-event-button")).toBeEnabled();
  }
});

test("a source highlight hidden by the filter updates the inspector and selection before being revealed", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text", "from q1timeline.render.html import render_html",
    'ir=analyze_text("play 0,0,4\\nwait 100\\nstop\\n",file="source.q1asm",sequencer_id="seq")',
    "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", ir, {}, {}, shell);
  const play = ir.events.find((item) => item.kind === "play");
  const wait = ir.events.find((item) => item.kind === "wait");
  const playBlock = page.locator(`.timeline-block[data-event-id="${play.id}"]`);
  const waitBlock = page.locator(`.timeline-block[data-event-id="${wait.id}"]`);
  const time = page.getByRole("spinbutton", {name: "Selected time in nanoseconds"});
  const fullWindow = await page.locator("#q1timeline-time-window").getAttribute("title");
  const assertWaitSelected = async () => {
    await expect(waitBlock).toHaveClass(/is-selected/);
    await expect(waitBlock).toHaveAttribute("aria-selected", "true");
    await expect(page.locator(`.timeline-block[data-event-id="${play.id}"][aria-selected="true"]`)).toHaveCount(0);
    await expect(page.locator('.timeline-block[aria-selected="true"]')).toHaveCount(1);
    await expect(page.locator("#event-inspector-fields")).toContainText("source.q1asm:2");
    await expect(time).toHaveValue("4");
  };
  await playBlock.click();
  await expect(playBlock).toHaveAttribute("aria-selected", "true");
  await analysisDetails(page);
  const filter = page.getByRole("searchbox", {name: "Filter source or kind"});
  await filter.fill("play");
  await expect(waitBlock).toBeHidden();
  await page.evaluate((eventId) => window.dispatchEvent(new MessageEvent("message", {
    data: {type: "highlightEventIds", highlightEventIds: [eventId]},
  })), wait.id);
  await assertWaitSelected();
  await expect(filter).toHaveValue("play");
  await expect(waitBlock).toBeHidden();
  await expect(page.locator("#q1timeline-time-window")).toHaveAttribute("title", fullWindow);
  await filter.fill("");
  await expect(waitBlock).toBeVisible();
  await assertWaitSelected();
  for (const layout of ["Instruction", "Time scale"]) {
    await page.getByRole("button", {name: layout, exact: true}).click();
    await assertWaitSelected();
  }
  await page.getByRole("button", {name: "Zoom in", exact: true}).click();
  await assertWaitSelected();
  await expect(page.locator("#q1timeline-time-window")).not.toHaveAttribute("title", fullWindow);
  await page.locator("#q1timeline-zoom-event-button").click();
  await expect(page.locator("#q1timeline-time-window")).toHaveAttribute("title", fullWindow);
  await assertWaitSelected();
});

test("diagnostic event links reveal an offscreen target and notify its source once per activation", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text", "from q1timeline.render.html import render_html",
    'ir=analyze_text("wait 4\\nmove 100000,R0\\nwork: nop\\nloop R0,@work\\nplay 0,0,4\\nwait 1000\\nstop\\n",file="diagnostic.q1asm",sequencer_id="seq")',
    "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", ir, {}, {}, shell);
  const diagnostic = ir.diagnostics.find((item) => item.category === "definite_underflow");
  const eventId = diagnostic.related_events[0];
  const target = page.locator(`.timeline-block[data-event-ids~="${eventId}"]`);
  const link = page.locator(`[data-related-event-id="${eventId}"]`).first();
  const time = page.getByRole("spinbutton", {name: "Selected time in nanoseconds"});
  await analysisDetails(page);
  await expect(target).toBeVisible();
  await link.click();
  await expect(target).toHaveClass(/is-selected/);
  await expect(page.locator("#event-inspector-fields")).toContainText("diagnostic.q1asm:5");
  await expect(time).toHaveValue("4");
  expect(await page.evaluate(() => window.messages.filter((message) => message.type === "eventClick")))
    .toEqual([{type: "eventClick", eventId}]);
  await page.evaluate(id => window.dispatchEvent(new MessageEvent("message", {data: {
    type: "highlightEventIds", highlightEventIds: [id], zoomToRange: true,
  }})), ir.events.find(item => item.kind === "stop").id);
  await expect(target).toHaveCount(0);
  await time.fill("600");
  await time.press("Enter");
  const zoomedWindow = await page.locator("#q1timeline-time-window").getAttribute("title");
  await link.click();
  await expect(target).toBeVisible();
  await expect(target).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#event-inspector-fields")).toContainText("diagnostic.q1asm:5");
  await expect(page.locator("#q1timeline-time-window")).not.toHaveAttribute("title", zoomedWindow);
  await expect(time).toHaveValue("4");
  expect(await page.evaluate(() => window.messages.filter((message) => message.type === "eventClick")))
    .toEqual([{type: "eventClick", eventId}, {type: "eventClick", eventId}]);
});

test("diagnostic event links select pending setters without adding a separate timeline row", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text", "from q1timeline.render.html import render_html",
    'ir=analyze_text("play 0,0,4\\nwait 1000\\nset_freq 100\\nstop\\n",file="pending.q1asm",sequencer_id="seq",hardware_readiness=True)',
    "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", ir, {}, {}, shell);
  const diagnostic = ir.diagnostics.find((item) => item.category === "pending_hardware_state");
  const eventId = diagnostic.related_events[0];
  const target = page.locator(`.timeline-block[data-event-id="${eventId}"]`);
  await expect(target).toHaveCount(0);
  await analysisDetails(page);
  await page.locator(`[data-related-event-id="${eventId}"]`).first().click();
  await expect(page.locator("#event-inspector-fields")).toContainText("pending.q1asm:3");
  await expect(page.getByRole("spinbutton", {name: "Selected time in nanoseconds"})).toHaveValue("1004");
  await expect(page.locator("#q1timeline-zoom-event-button")).toBeEnabled();
  await expect(target).toHaveCount(0);
  expect(await page.evaluate(() => window.messages.filter((message) => message.type === "eventClick")))
    .toEqual([{type: "eventClick", eventId}]);
});

for (const initialSelection of ["none", "source range"]) {
  test(`offscreen hardware replay selection updates zoom and clears stale source selection from ${initialSelection}`, async ({page}) => {
    const repo = path.resolve(__dirname, "../../..");
    const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
      "import json", "from q1timeline.playback import PlaybackSession", "from q1timeline.render.html import render_html",
      's=PlaybackSession({"seq":("replay.q1asm","wait 1000\\nnop\\nstop\\n")})',
      "s.run(budget_ms=1000,max_steps=50)", "ir=s.snapshot()",
      "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
    ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
    const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    await mount(page, ir.playback.status, ir, {}, {}, shell);
    const issues = ir.events.filter((item) => item.kind === "q1_issue");
    const first = page.locator(`.timeline-block[data-event-id="${issues[0].id}"]`);
    if (initialSelection === "source range") {
      await page.evaluate((ids) => window.dispatchEvent(new MessageEvent("message", {data: {
        type: "highlightEventIds", highlightEventIds: ids, zoomToRange: true,
        sourceRange: {file: "replay.q1asm", startLine: 1, endLine: 1},
      }})), ir.source_map.by_source["replay.q1asm:1"]);
      await expect(page.locator(".time-selection").first()).toBeVisible();
    }
    await page.getByRole("button", {name: "Zoom in", exact: true}).click();
    await page.getByRole("button", {name: "Zoom in", exact: true}).click();
    await expect(first).toHaveCount(0);
    await page.locator("[data-module-card]").click();
    await page.locator('[data-sequencer-card="seq"]').click();
    await page.getByRole("button", {name: "Execution replay", exact: true}).click();
    const time = page.getByRole("spinbutton", {name: "Selected time in nanoseconds"});
    const zoomEvent = page.locator("#q1timeline-zoom-event-button");
    await expect(zoomEvent).toBeDisabled();
    const windowBefore = await page.locator("#q1timeline-time-window").getAttribute("title");
    await page.locator('[data-action="replay-step"]').click();
    await expect(time).toHaveValue("4");
    await expect(page.locator("#event-inspector-fields")).toContainText("replay.q1asm:1");
    await expect(page.locator("#q1timeline-time-window")).toHaveAttribute("title", windowBefore);
    if (initialSelection === "source range") await expect(page.locator(".time-selection")).toHaveCount(0);
    await expect(zoomEvent).toBeEnabled();
    await expect(page.locator("#q1timeline-zoom-selection-button")).toBeDisabled();
    await expect(page.locator('.timeline-block[aria-selected="true"], .timeline-block.is-selected')).toHaveCount(0);
    await zoomEvent.click();
    await expect(first).toBeVisible();
    await expect(first).toHaveAttribute("aria-selected", "true");
    await expect(time).toHaveValue("4");
    await page.locator('[data-action="replay-step"]').click();
    await expect(time).toHaveValue("8");
    await expect(page.locator(`.timeline-block[data-event-id="${issues[1].id}"]`)).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#event-inspector-fields")).toContainText("replay.q1asm:2");
    expect(await page.evaluate(() => window.messages.filter((message) => message.type === "eventClick" || message.type === "sourceClick"))).toEqual([]);
  });
}

test("static disabled example renders RT commands once without overlapping loop summaries", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json,re", "from q1timeline.analysis.interpreter import interpret_program",
    "from q1timeline.analysis.alignment import align_timelines",
    "from q1timeline.q1asm.parser import parse_q1asm_file", "from q1timeline.ir.serialize import timeline_ir_from_states",
    "from q1timeline.render.html import render_html",
    "state=interpret_program(parse_q1asm_file('examples/set-cond-review/01-disabled/disabled.q1asm'),sequencer_id='disabled')",
    "align_timelines([state],mode='none')",
    "html=render_html(timeline_ir_from_states([state]),default_mode='debug')",
    "ir=json.loads(re.search(r'<script id=\"timeline-ir\" type=\"application/json\">(.*?)</script>',html,re.S).group(1))",
    "print(json.dumps({'ir':ir,'html':html}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", ir, {}, {}, shell);
  const expected = ir.events.filter((item) => ["upd_param", "wait", "stop"].includes(item.kind));
  const mirrors = ir.events.filter((item) => item.kind === "marker_state");
  const blocks = page.locator('.timeline-row[data-lane="sequencer:disabled"] .timeline-block');
  const loop = ir.events.find((item) => item.kind === "loop_block");
  const firstWait = ir.events.find((item) => item.kind === "wait");
  const following = ir.events.find((item) => item.kind === "upd_param" && item.t0.value > firstWait.t1.value);
  const collapsedScale = await page.evaluate(() => {
    const ir = JSON.parse(document.getElementById("timeline-ir").textContent);
    const adapter = window.q1timelineTimelineAdapter;
    const waits = adapter.buildWaitTimeScale(ir, {timeBasis: "aligned"});
    const scale = adapter.buildTimelineTimeScale(ir, {timeBasis: "aligned", waitGaps: waits.gaps});
    return {duration: scale.toDisplay(196613) - scale.toDisplay(0), gaps: scale.gaps};
  });
  expect(collapsedScale.duration).toBeLessThan(512);
  const repeat = page.locator('.ruler .q1timeline-playback-fold');
  await expect(repeat.getByRole("button")).toHaveText("131 µs");
  await expect(page.locator('.loop-range-label')).toHaveText(/x3/);
  await expect(page.locator('.loop-range-label')).toHaveCSS("opacity", "1");
  await expect(repeat).toHaveAttribute("data-time-start", String(firstWait.t1.value));
  const geometry = await loopGeometry(page, loop.id, firstWait.id, following.id);
  expect(geometry.firstWidth).toBeGreaterThan(0);
  await expect(page.locator(`.timeline-block[data-event-id="${firstWait.id}"] .block-label`)).toBeVisible();
  expect(geometry.afterLeft).toBeGreaterThan(geometry.loopLeft + geometry.loopWidth);
  expect(geometry.afterLeft).toBeLessThan(100);
  const repeatEnd = Number(await repeat.getAttribute("data-time-end"));
  expect(geometry.ticks.some((tick) => tick.time > firstWait.t1.value && tick.time < repeatEnd)).toBe(false);
  await page.screenshot({path: test.info().outputPath("disabled-initial-folded.png"), fullPage: true});
  await repeat.getByRole("button").click();
  await page.getByRole("button", {name: "Show next iteration", exact: true}).click();
  expect(await page.evaluate(() => window.messages.at(-1))).toEqual({type: "setLoopPreview", loopKey: loop.meta.loop_preview_key, visibleIterations: 2});
  for (const mode of ["Debug view", "Normal view"]) {
    await page.getByRole("button", {name: mode, exact: true}).click();
    for (const layout of ["Time scale", "Instruction"]) {
      await page.getByRole("button", {name: layout, exact: true}).click();
      await expect(blocks).toHaveCount(expected.length);
      expect(await blocks.evaluateAll((nodes) => nodes.map((node) => node.dataset.eventId))).toEqual(expected.map((item) => item.id));
      await expect(page.locator('.timeline-block.kind-loop_iteration_preview')).toHaveCount(0);
      await expect(page.locator('.timeline-block.kind-marker_state')).toHaveCount(0);
      await expect(page.locator('.timeline-loop-range')).toHaveCount(1);
      for (const mirror of mirrors) await expect(page.locator(`.timeline-block[data-event-ids~="${mirror.id}"]`)).toHaveCount(1);
    }
  }
  await mount(page, "idle", ir, {timelineTimeBasis: "local", timelineWindow: {
    min: 0, max: 32, fullMin: 0, fullMax: 196613, basis: "local",
  }}, {}, shell);
  await expect(blocks).toHaveCount(2); // Initial update and the wait in the user's zoomed viewport.
  await expect(page.locator('#q1timeline-time-window')).toHaveAttribute("title", /0\.\.32 ns/);
  const intervals = await blocks.evaluateAll((nodes) => nodes.map((node) => {
    const rect = node.getBoundingClientRect(); return {left: rect.left, right: rect.right};
  }));
  expect(intervals[0].right).toBeLessThanOrEqual(intervals[1].left + 1);
  await page.screenshot({path: test.info().outputPath("disabled-static-no-overlap.png"), fullPage: true});
  expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
});

async function loopGeometry(page, loopId, firstWaitId, followingId) {
  return page.evaluate(({loopId, firstWaitId, followingId}) => {
    const loop = document.querySelector(`.timeline-loop-range[data-event-id="${loopId}"]`);
    const first = document.querySelector(`.timeline-block[data-event-id="${firstWaitId}"]`);
    const after = document.querySelector(`.timeline-block[data-event-id="${followingId}"]`);
    return {loopLeft: parseFloat(loop.style.left), loopWidth: parseFloat(loop.style.width),
      firstWidth: parseFloat(first.style.width), afterLeft: parseFloat(after.style.left),
      ticks: [...document.querySelectorAll(".ruler .tick")].map((tick) => ({time: parseFloat(tick.textContent), left: parseFloat(tick.style.left)}))};
  }, {loopId, firstWaitId, followingId});
}

for (const execution of ["analysis", "playback"]) {
  test(`source selection keeps independent Q1 and RT ranges in ${execution}`, async ({page}) => {
    const repo = path.resolve(__dirname, "../../..");
    const ir = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
      "import json", "from pathlib import Path", "from q1timeline.analysis.api import analyze_text",
      "from q1timeline.playback import PlaybackSession",
      "file=Path('examples/set-cond-review/01-disabled/disabled.q1asm')",
      execution === "analysis" ? "ir=analyze_text(file.read_text(),file=str(file),sequencer_id='disabled')" :
        "session=PlaybackSession.from_project(file.with_name('q1timeline.yml'));session.run(budget_ms=1000,max_steps=100);ir=session.snapshot()",
      "print(json.dumps(ir))",
    ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
    const {lookupEventIdsForSourceRange} = require(path.join(assets, "q1timeline/sourceMapLookup.ts"));
    const file = ir.events.find((item) => item.kind === "q1_issue").source.file;
    const ids = lookupEventIdsForSourceRange(ir, file, 2, 8);
    const q1 = ir.events.filter((item) => item.kind === "q1_issue" && ids.includes(item.id));
    const update = ir.events.find((item) => item.kind === "upd_param" && ids.includes(item.id));
    expect(q1).toHaveLength(6);
    await mount(page, ir.playback?.status || "idle", ir, {expandedQ1IssueSequencers: []});
    const time = page.getByRole("spinbutton", {name: "Selected time in nanoseconds"});
    const before = await time.inputValue();
    await page.evaluate(({ids, file}) => window.dispatchEvent(new MessageEvent("message", {data: {
      type: "highlightEventIds", highlightEventIds: ids, zoomToRange: true, sourceRange: {file, startLine: 2, endLine: 8},
    }})), {ids, file});
    const rt = page.locator('.timeline-row[data-lane="sequencer:disabled"]');
    const core = page.locator('.timeline-row[data-lane="sequencer:disabled:q1_issue"]');
    await expect(core).toBeVisible();
    await expect(core.locator('.timeline-block[aria-selected="true"]')).toHaveCount(6);
    await expect(rt.locator('.timeline-block[aria-selected="true"]')).toHaveCount(1);
    await expect(rt.locator('.timeline-block-wait')).toHaveAttribute("aria-selected", "false");
    await expect(page.locator('.timeline-loop-range.is-selected')).toHaveCount(0);
    const rtRange = await rt.locator('.time-selection').boundingBox();
    const coreRange = await core.locator('.time-selection').boundingBox();
    await expect(rt.locator('.time-selection')).toHaveAttribute("data-time-start", "0");
    await expect(rt.locator('.time-selection')).toHaveAttribute("data-time-end", "4");
    await expect(core.locator('.time-selection')).toHaveAttribute("data-time-start", "0");
    await expect(core.locator('.time-selection')).toHaveAttribute("data-time-end", "24");
    expect(coreRange.width).toBeGreaterThan(rtRange.width);
    expect(rtRange.x).toBeCloseTo(coreRange.x, 0);
    const updateBounds = await rt.locator(`.timeline-block[data-event-id="${update.id}"]`).boundingBox();
    const lastIssueNode = core.locator(`.timeline-block[data-event-id="${q1.at(-1).id}"]`);
    const lastIssue = await lastIssueNode.boundingBox();
    expect(Math.abs(rtRange.x + rtRange.width - updateBounds.x - updateBounds.width)).toBeLessThan(3);
    if (await lastIssueNode.getAttribute("data-issue-marker") === "true") {
      // The 0..24 ns selection includes the issue start; its icon has a fixed hit target, even on folded time.
      expect(lastIssue.x).toBeGreaterThanOrEqual(coreRange.x - 1);
      expect(lastIssue.x).toBeLessThan(coreRange.x + coreRange.width);
    } else {
      expect(Math.abs(coreRange.x + coreRange.width - lastIssue.x - lastIssue.width)).toBeLessThan(3);
    }
    expect(await time.inputValue()).toBe(before);
    await page.screenshot({path: test.info().outputPath(`source-range-${execution}.png`), fullPage: true});
    await page.getByRole("button", {name: "Normal view", exact: true}).click();
    await expect(core).toBeHidden();
    await expect(rt.locator('.time-selection')).toHaveAttribute("data-time-end", "4");
    await expect(core.locator('.time-selection')).toHaveAttribute("data-time-end", "24");
    await rt.locator('.timeline-block-wait').click();
    await expect(page.locator('.time-selection')).toHaveCount(0);
    await expect(core.locator('.timeline-block[aria-selected="true"]')).toHaveCount(0);
    await page.evaluate(({ids, file}) => window.dispatchEvent(new MessageEvent("message", {data: {
      type: "highlightEventIds", highlightEventIds: ids, zoomToRange: true, sourceRange: {file, startLine: 2, endLine: 8},
    }})), {ids, file});
    const ruler = await page.locator('.ruler').boundingBox();
    await page.keyboard.down("Shift");
    await page.mouse.move(ruler.x + ruler.width / 4, ruler.y + 2);
    await page.mouse.down();
    await page.mouse.move(ruler.x + ruler.width * 3 / 4, ruler.y + 2, {steps: 4});
    await page.mouse.up();
    await page.keyboard.up("Shift");
    await expect(core).toBeHidden();
    await page.getByRole("button", {name: "Debug view", exact: true}).click();
    expect((await core.locator('.time-selection').boundingBox()).width).toBeCloseTo((await rt.locator('.time-selection').boundingBox()).width, 0);
    expect(await page.evaluate(() => window.getSavedTimelineState().expandedPlaybackLoops)).toEqual([]);
    expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
  });
}

function feedbackFixture(status = "completed") {
  const feedback = (id, start, value, delay, sourceName) => ({
    ...event(id, "q1_issue", start, start + 4, {op: "fb_pop_data", playback_feedback_id: `receive:${id}`,
      feedback_assumption: {value, delay_ns: delay, source: sourceName}, feedback_assumptions: [`receive:${id}`]}),
    source: {file: "playback.q1asm", line: 3, raw: "fb_pop_data R0"}, confidence: "assumed",
  });
  return {sequencers: [{id: "seq"}], playback: {status, steps: 4}, events: [
    event("wait", "wait", 0, 100), feedback("first", 4, null, 0, "default"),
    feedback("second", 40, 7, 16, "user"), event("stop", "stop", 100, 100),
  ]};
}

test("conditional analysis keeps unknown RT timing out of Time scale and retains instruction order", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const ir = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.interpreter import interpret_program",
    "from q1timeline.q1asm.parser import parse_q1asm", "from q1timeline.ir.serialize import timeline_ir_from_states",
    "source='set_mrk 3\\nupd_param 4\\nwait 1000\\nset_cond 1,1,0,20\\nwait 65535\\nset_cond 0,0,0,4\\nwait 65535\\nstop'",
    "state=interpret_program(parse_q1asm(source,file='conditional.q1asm'),sequencer_id='seq')",
    "print(json.dumps(timeline_ir_from_states([state])))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const boundary = ir.events.find((item) => item.kind === "unknown_region");
  const laterWait = ir.events.find((item) => item.kind === "wait" && item.meta.conditional_time_unknown);
  const rt = ir.events.filter((item) => item.lane.startsWith("rt.") && !["marker_state", "latched_state_applied"].includes(item.kind));
  await mount(page, "completed", ir);
  await expect(page.locator(`.timeline-block[data-event-id="${boundary.id}"]`)).toHaveAttribute("title", /unknown/);
  await expect(page.locator(`.timeline-block[data-event-id="${laterWait.id}"]`)).toHaveCount(0);
  const held = await page.evaluate(() => {
    const ir = JSON.parse(document.getElementById("timeline-ir").textContent);
    const hardware = window.q1lensHardwareState;
    const state = hardware.snapshot(hardware.createIndex(ir), "seq", 1004);
    return {rt: state.rt.status, marker: state.parameters.find((item) => item.field === "marker").applied};
  });
  expect(held.rt).toBe("unknown");
  expect(held.marker).toMatchObject({known: true, value: 3});
  await page.getByRole("button", {name: "Instruction", exact: true}).click();
  await expect(page.locator(`.timeline-block[data-event-id="${laterWait.id}"]`)).toHaveCount(1);
  const shown = await page.locator('.timeline-row[data-lane="sequencer:seq"] .timeline-block').evaluateAll((nodes) =>
    nodes.filter((node) => !["marker_state", "latched_state_applied"].some((kind) => node.classList.contains(`kind-${kind}`)))
      .map((node) => ({id: node.dataset.eventId, position: Number(node.dataset.instructionIndex)})));
  expect(shown.map((item) => item.id)).toEqual(rt.map((item) => item.id));
  for (let index = 1; index < shown.length; index += 1) expect(shown[index].position).toBeGreaterThan(shown[index - 1].position);
  await page.getByRole("button", {name: "Time scale", exact: true}).click();
  await expect(page.locator(`.timeline-block[data-event-id="${laterWait.id}"]`)).toHaveCount(0);
  expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
});

test("disabled conditional playback completes and keeps its long wait folded", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const ir = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.playback import PlaybackSession",
    "session=PlaybackSession({'seq':('conditional.q1asm','set_cond 0,0,7,4\\nwait 65535\\nstop')})",
    "session.run(budget_ms=1000,max_steps=100)", "print(json.dumps(session.snapshot()))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  expect(ir.playback.status).toBe("completed");
  const wait = ir.events.find((item) => item.kind === "wait");
  await mount(page, "completed", ir);
  await expect(page.getByRole("button", {name: "Execution complete", exact: true})).toBeDisabled();
  await expect(page.locator('#q1timeline-wait-fold-button')).toBeEnabled();
  await expect(page.locator('#q1timeline-wait-fold-button')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(`.timeline-block[data-event-id="${wait.id}"]`)).toBeVisible();
  await expect(page.locator('.ruler .q1timeline-wait-fold:not(.is-expanded)')).toHaveCount(1);
  const duration = await page.evaluate(({start, end}) => {
    const ir = JSON.parse(document.getElementById("timeline-ir").textContent);
    const scale = window.q1timelineTimelineAdapter.buildWaitTimeScale(ir);
    return scale.toDisplay(end) - scale.toDisplay(start);
  }, {start: wait.t0.value, end: wait.t1.value});
  expect(duration).toBeLessThan(65535 / 10);
  await page.locator('#q1timeline-wait-fold-button').click();
  await expect(page.locator('.ruler .q1timeline-wait-fold.is-expanded')).toHaveCount(1);
  await expect(page.locator('#q1timeline-wait-fold-button')).toHaveAttribute("aria-pressed", "false");
});

test("collapsed wait loop preserves the first wait's end before a distinct repeat symbol", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const ir = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.playback import PlaybackSession",
    "session=PlaybackSession.from_project('examples/set-cond-review/01-disabled/q1timeline.yml')",
    "session.run(budget_ms=1000,max_steps=100)", "print(json.dumps(session.snapshot()))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const loop = ir.events.find((item) => item.kind === "loop_block" && item.meta.playback);
  const waits = ir.events.filter((item) => item.kind === "wait");
  const following = ir.events.find((item) => item.kind === "upd_param" && item.t0.value === loop.t1.value);
  expect(waits).toHaveLength(3);
  await mount(page, "completed", ir);
  const row = page.locator('.timeline-row[data-lane="sequencer:disabled"]');
  const first = row.locator(`.timeline-block[data-event-id="${waits[0].id}"]`);
  const waitFold = row.locator('.q1timeline-wait-fold:not(.is-expanded)');
  const repeatFold = row.locator('.q1timeline-playback-fold');
  await expect(first).toBeVisible();
  await expect(waitFold).toHaveCount(1);
  const waitToggle = page.locator('.ruler .q1timeline-wait-fold:not(.is-expanded) .q1timeline-fold-toggle');
  await expect(waitFold).toHaveCSS("background-image", "none");
  await expect(waitFold).toHaveCSS("pointer-events", "none");
  await expect(waitFold.getByRole("button")).toHaveCount(0);
  await expect(waitToggle.locator('.q1timeline-fold-duration')).toBeVisible();
  await expect(waitToggle.locator('.q1timeline-fold-icon')).toHaveClass(/is-expand/);
  expect(await waitToggle.innerText()).not.toContain("//");
  expect(await waitToggle.innerText()).toMatch(/^\d[\d.]* (ns|µs|ms)$/);
  await waitToggle.hover();
  await expect(repeatFold.getByRole("button")).toHaveCount(0);
  const repeatToggle = page.locator('.ruler .q1timeline-playback-fold button');
  await expect(repeatToggle.locator('.q1timeline-fold-icon')).toHaveClass(/is-repeat/);
  expect(await repeatToggle.innerText()).toMatch(/^\d[\d.]* (ns|µs|ms)$/);
  await expect(row.locator('.loop-range-label')).toHaveText("×3");
  const geometry = async () => {
    const [block, waitGap, repeated, after] = await Promise.all([
      first.boundingBox(), waitFold.boundingBox(), repeatFold.boundingBox(),
      row.locator(`.timeline-block[data-event-id="${following.id}"]`).boundingBox(),
    ]);
    return {block, waitGap, repeated, after};
  };
  const folded = await geometry();
  const icon = await waitToggle.boundingBox();
  expect(icon.width).toBeLessThanOrEqual(100);
  expect(icon.x + icon.width / 2).toBeCloseTo(folded.waitGap.x + folded.waitGap.width / 2, 0);
  const rulerIcon = await page.locator('.ruler .q1timeline-wait-fold:not(.is-expanded) .q1timeline-fold-toggle').boundingBox();
  expect(rulerIcon.width).toBeLessThanOrEqual(100);
  expect(rulerIcon.x + rulerIcon.width / 2).toBeCloseTo(icon.x + icon.width / 2, 0);
  const besideIcon = {x: folded.waitGap.x + folded.waitGap.width / 5, y: folded.block.y + folded.block.height / 2};
  expect(await page.evaluate(({x, y}) => document.elementFromPoint(x, y)?.closest('.timeline-block')?.dataset.eventId, besideIcon)).toBe(waits[0].id);
  await page.mouse.click(besideIcon.x, besideIcon.y);
  await expect(first).toHaveAttribute("aria-selected", "true");
  await expect(waitFold).toHaveCount(1);
  await page.mouse.click(rulerIcon.x - 2, rulerIcon.y + rulerIcon.height / 2);
  await expect(waitFold).toHaveCount(1);
  expect(await page.evaluate(() => window.getSavedTimelineState().timelineExpandedGaps)).toEqual([]);
  expect(folded.waitGap.x - folded.block.x).toBeGreaterThan(0);
  expect(folded.block.x + folded.block.width - folded.waitGap.x - folded.waitGap.width).toBeGreaterThan(0);
  expect(folded.repeated.x).toBeCloseTo(folded.block.x + folded.block.width, 0);
  expect(folded.after.x).toBeCloseTo(folded.repeated.x + folded.repeated.width, 0);
  const loopBounds = await row.locator('.timeline-loop-range').boundingBox();
  expect(folded.block.x).toBeCloseTo(loopBounds.x, 0);
  expect(folded.repeated.x).toBeCloseTo(loopBounds.x + loopBounds.width, 0);
  await expect(repeatFold).toHaveAttribute("data-time-start", String(waits[0].t1.value));
  await expect(repeatFold).toHaveAttribute("data-time-end", String(loop.t1.value));
  for (const wait of waits.slice(1)) await expect(row.locator(`.timeline-block[data-event-id="${wait.id}"]`)).toHaveCount(0);
  await page.screenshot({path: test.info().outputPath("wait-end-and-repeats.png"), fullPage: true});
  await waitToggle.click();
  await expect(row.locator('.q1timeline-wait-fold.is-expanded')).toHaveCount(1);
  await page.locator('.ruler .q1timeline-wait-fold.is-expanded .q1timeline-fold-toggle').click();
  expect(await geometry()).toEqual(folded);
  const toggle = page.locator('#q1timeline-wait-fold-button');
  await toggle.click();
  expect((await first.boundingBox()).width).toBeGreaterThan(folded.block.width);
  await toggle.click();
  expect(await geometry()).toEqual(folded);
  await repeatToggle.click();
  for (const wait of waits) await expect(row.locator(`.timeline-block[data-event-id="${wait.id}"]`)).toHaveCount(1);
  await expect(repeatFold).toHaveCount(0);
  expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
});

test("affine compact loops fold through their proven total and keep the following RT update selectable", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  for (const [initial, step] of [[4000, 4000], [12000, -4000]]) {
    const source = `move 3,R0\nmove ${initial},R1\nnop\nbody: wait R1\nadd R1,${step},R1\nnop\nloop R0,@body\nupd_param 4\nstop\n`;
    const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
      "import json", "from q1timeline.analysis.api import analyze_text", "from q1timeline.render.html import render_html",
      `source=${JSON.stringify(source)}`,
      "ir=analyze_text(source,file='affine.q1asm',sequencer_id='seq')",
      "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
    ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
    const loop = ir.events.find((item) => item.kind === "loop_block");
    const update = ir.events.find((item) => item.kind === "upd_param");
    expect(loop.meta.total_duration.value).toBe(24000);
    expect(loop.meta.shown_iterations).toEqual([0]);
    expect(update.t0.value).toBe(24000);
    const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    await mount(page, "idle", ir, {}, {}, shell);
    const gaps = await page.evaluate(() => {
      const ir = JSON.parse(document.getElementById("timeline-ir").textContent);
      const adapter = window.q1timelineTimelineAdapter;
      const waits = adapter.buildWaitTimeScale(ir, {timeBasis: "aligned"});
      return adapter.buildTimelineTimeScale(ir, {timeBasis: "aligned", waitGaps: waits.gaps}).gaps;
    });
    const omitted = gaps.find((gap) => gap.loopKeys?.includes(loop.meta.loop_preview_key));
    expect(omitted.start).toBe(initial);
    expect(omitted.end).toBe(24000);
    await page.evaluate((ids) => window.dispatchEvent(new MessageEvent("message", {data: {
      type: "highlightEventIds", highlightEventIds: ids, zoomToRange: false,
      sourceRange: {file: "affine.q1asm", startLine: 8, endLine: 8},
    }})), ir.events.filter((item) => item.source?.line === 8).map((item) => item.id));
    await expect(page.locator(`.timeline-block[data-event-id="${update.id}"]`)).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("#event-inspector-fields")).toContainText("affine.q1asm:8");
    if (step < 0) await page.screenshot({path: test.info().outputPath("affine-loop-total.png"), fullPage: true});
  }
});

test("branch joins retain known settings and keep physical phase uncertain until an applied reset", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const program = "set_freq 4000000\nreset_ph\nupd_param 4\nwait 100\njge R7,1,@other\nreset_ph\nupd_param 4\njmp @join\nother: set_ph 0\nupd_param 4\njoin: play 0,1,40\nreset_ph\nplay 0,1,40\nstop\n";
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text", "from q1timeline.render.html import render_html",
    `program=${JSON.stringify(program)}`,
    "ir=analyze_text(program,file='branch-phase.q1asm',sequencer_id='seq')",
    "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", ir, {}, {}, shell);
  const suffix = ir.events.find((item) => item.kind === "play" && item.source.line === 11);
  const result = await page.evaluate(() => {
    const ir = JSON.parse(document.getElementById("timeline-ir").textContent);
    const hardware = window.q1lensHardwareState;
    const index = hardware.createIndex(ir);
    const suffix = ir.events.find((item) => item.kind === "play" && item.source.line === 11);
    const reset = ir.events.find((item) => item.kind === "play" && item.source.line === 13);
    const parameters = (time) => Object.fromEntries(hardware.snapshot(index, "seq", time).parameters.map((item) => [item.field, item.applied]));
    const model = window.q1timelineTimelineAdapter.buildQ1TimelineSharedModel(ir);
    const details = model.lanes.flatMap((lane) => lane.blocks).find((block) => block.eventIds.includes(suffix.id)).rtDetails;
    return {uncertain: parameters(suffix.t0.value), restored: parameters(reset.t0.value), details};
  });
  expect(result.uncertain.frequency).toMatchObject({known: true, value: 4000000});
  expect(result.uncertain.phase.known).toBe(false);
  expect(result.restored.phase).toMatchObject({known: true, value: 0});
  expect(result.details.parameters.find((item) => item.name === "frequency").valueDisplay).toBe("4000000");
  expect(result.details.parameters.find((item) => item.name === "phase").valueDisplay).toContain("?");
  await page.evaluate((ids) => window.dispatchEvent(new MessageEvent("message", {data: {
    type: "highlightEventIds", highlightEventIds: ids, zoomToRange: false,
    sourceRange: {file: "branch-phase.q1asm", startLine: 11, endLine: 11},
  }})), ir.events.filter((item) => item.source?.line === 11).map((item) => item.id));
  await expect(page.locator(`.timeline-block[data-event-id="${suffix.id}"]`)).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#event-inspector-fields")).toContainText("branch-phase.q1asm:11");
  await page.screenshot({path: test.info().outputPath("branch-phase-join.png"), fullPage: true});
});

test("a bounded symbolic loop total locates the following RT command without inventing packet times", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const program = "and R7,15,R1\nnop\nadd R1,4,R1\nnop\nmove 10000,R2\nmove 3,R0\nnop\nbody: wait R1\nsub R2,R1,R3\nnop\nwait R3\nadd R1,4,R1\nnop\nloop R0,@body\nupd_param 4\nstop\n";
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text", "from q1timeline.render.html import render_html",
    `program=${JSON.stringify(program)}`,
    "ir=analyze_text(program,file='symbolic-total.q1asm',sequencer_id='seq')",
    "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const loop = ir.events.find((item) => item.kind === "loop_block");
  const update = ir.events.find((item) => item.kind === "upd_param");
  expect(loop.meta.total_only).toBe(true);
  expect(loop.meta.total_duration.value).toBe(30000);
  expect(update.t0.value).toBe(30000);
  expect(ir.events.find((item) => item.kind === "wait" && item.source.line === 8).duration.kind).not.toBe("concrete");
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", ir, {}, {}, shell);
  await page.evaluate((ids) => window.dispatchEvent(new MessageEvent("message", {data: {
    type: "highlightEventIds", highlightEventIds: ids, zoomToRange: false,
    sourceRange: {file: "symbolic-total.q1asm", startLine: 15, endLine: 15},
  }})), ir.events.filter((item) => item.source?.line === 15).map((item) => item.id));
  await expect(page.locator(`.timeline-block[data-event-id="${update.id}"]`)).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#event-inspector-fields")).toContainText("symbolic-total.q1asm:15");
  await expect(page.locator("#event-inspector-fields")).toContainText("30000");
});

test("eventual infinite-loop underflow reports the proof without a fabricated failure time", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text", "from q1timeline.render.html import render_html",
    'ir=analyze_text("body: wait 4\\njmp @body\\n",file="cadence.q1asm",sequencer_id="seq")',
    "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const warning = ir.events.find((item) => item.kind === "underflow_warning");
  expect(warning.t0.kind).toBe("unknown");
  expect(warning.meta.slack_ns).toBeUndefined();
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", ir, {}, {}, shell);
  const diagnostic = page.locator('.diagnostic[data-diagnostic-category="definite_underflow"]');
  await expect(diagnostic).toContainText("eventually exhausts any finite initial buffer");
  await expect(diagnostic).toContainText("exact failure iteration and time are not determined");
  await expect(diagnostic).not.toContainText("ns late");
  await expect(page.locator("#q1timeline-initial-buffer-summary")).toHaveText("Initial buffer · no finite reserve sustains this loop");
  await expect(page.locator(`.timeline-block[data-event-id="${warning.id}"]`)).toHaveCount(0);
});

test("feedback assumption marks edit one receive occurrence and keep zero distinct from unknown", async ({page}) => {
  const ir = feedbackFixture();
  await mount(page, "completed", ir);
  const first = page.locator('.q1timeline-control-chip-feedback[data-event-id="first"]');
  const second = page.locator('.q1timeline-control-chip-feedback[data-event-id="second"]');
  await expect(page.locator('.q1timeline-control-chip-feedback')).toHaveCount(2);
  await expect(first).toHaveText("≈");
  await expect(first).toHaveAttribute("title", /unknown.*0 ns/);
  await expect(second).toHaveAttribute("title", /7.*16 ns/);
  await first.click();
  const dialog = page.getByRole("dialog", {name: "Feedback assumption"});
  await expect(dialog).toContainText("Assumed feedback");
  const value = dialog.getByRole("spinbutton", {name: "Value", exact: true});
  const delay = dialog.getByRole("spinbutton", {name: "Additional wait (ns)", exact: true});
  await expect(value).toBeFocused();
  await expect(value).toHaveValue("");
  await expect(delay).toHaveValue("0");
  await value.fill("0");
  await delay.fill("24");
  await page.screenshot({path: test.info().outputPath("feedback-assumption.png"), fullPage: true});
  await dialog.getByRole("button", {name: "Apply", exact: true}).click();
  expect(await page.evaluate(() => window.messages.at(-1))).toEqual({type: "setExecutionFeedback", eventId: "first", feedbackId: "receive:first", value: 0, delayNs: 24});
  await expect(first).toBeFocused();
  await second.click();
  await expect(value).toHaveValue("7");
  await expect(delay).toHaveValue("16");
  await value.fill("");
  await delay.fill("0");
  await value.press("Enter");
  expect(await page.evaluate(() => window.messages.at(-1))).toEqual({type: "setExecutionFeedback", eventId: "second", feedbackId: "receive:second", value: null, delayNs: 0});
  expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
  expect(await page.evaluate(() => window.messages.some((message) => message.type === "openSource"))).toBe(false);
});

test("feedback editing rejects invalid integers and retains accepted signed or unsigned input", async ({page}) => {
  await mount(page, "completed", feedbackFixture());
  await page.locator('.q1timeline-control-chip-feedback[data-event-id="first"]').click();
  const dialog = page.getByRole("dialog", {name: "Feedback assumption"});
  const value = dialog.getByRole("spinbutton", {name: "Value", exact: true});
  const delay = dialog.getByRole("spinbutton", {name: "Additional wait (ns)", exact: true});
  const apply = dialog.getByRole("button", {name: "Apply", exact: true});
  for (const invalid of ["-1", "0.5", "9007199254740992", ""]) {
    await delay.fill(invalid);
    await expect(apply).toBeDisabled();
  }
  await delay.fill("9007199254740991");
  for (const invalid of ["1.5", "-2147483649", "4294967296"]) {
    await value.fill(invalid);
    await expect(apply).toBeDisabled();
  }
  for (const valid of ["-2147483648", "4294967295", ""]) {
    await value.fill(valid);
    await expect(apply).toBeEnabled();
  }
  await value.fill("-1");
  await apply.click();
  expect(await page.evaluate(() => window.messages.at(-1))).toEqual({type: "setExecutionFeedback", eventId: "first", feedbackId: "receive:first", value: -1, delayNs: 9007199254740991});
});

test("feedback controls wait for execution to finish and support Escape focus return", async ({page}) => {
  await mount(page, "limit", feedbackFixture("limit"));
  const chip = page.locator('.q1timeline-control-chip-feedback[data-event-id="second"]');
  await chip.click();
  const dialog = page.getByRole("dialog", {name: "Feedback assumption"});
  await expect(dialog.getByRole("button", {name: "Apply", exact: true})).toBeEnabled();
  await page.evaluate(() => window.dispatchEvent(new MessageEvent("message", {data: {type: "setExecutionState", state: {status: "running"}}})));
  await expect(dialog).toHaveCount(0);
  await chip.click();
  await expect(dialog.getByRole("spinbutton", {name: "Value", exact: true})).toBeDisabled();
  await expect(dialog.getByRole("button", {name: "Apply", exact: true})).toBeDisabled();
  await chip.click();
  await page.evaluate(() => window.dispatchEvent(new MessageEvent("message", {data: {type: "setExecutionState", state: {status: "completed"}}})));
  await chip.click();
  await dialog.getByRole("spinbutton", {name: "Value", exact: true}).press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(chip).toBeFocused();
  expect(await page.evaluate(() => window.messages.filter((message) => message.type === "setExecutionFeedback"))).toEqual([]);
});

test("nanosecond feedback occurrences remain editable after zooming a long RT timeline", async ({page}) => {
  const ir = feedbackFixture();
  ir.events.find((item) => item.id === "wait").t1.value = 1000000;
  ir.events.find((item) => item.id === "stop").t0.value = 1000000;
  ir.events.find((item) => item.id === "stop").t1.value = 1000000;
  ir.events.find((item) => item.id === "second").t0.value = 8;
  ir.events.find((item) => item.id === "second").t1.value = 12;
  await mount(page, "completed", ir);
  for (const [id, assumedValue] of [["first", "11"], ["second", "22"]]) {
    await page.evaluate((id) => window.dispatchEvent(new MessageEvent("message", {data: {
      type: "highlightEventIds", highlightEventIds: [id], zoomToRange: true,
      sourceRange: {file: "playback.q1asm", startLine: 3, endLine: 3},
    }})), id);
    const chip = page.locator(`.q1timeline-control-chip-feedback[data-event-id="${id}"]`);
    await expect(chip).toBeVisible();
    await chip.click();
    const dialog = page.getByRole("dialog", {name: "Feedback assumption"});
    await dialog.getByRole("spinbutton", {name: "Value", exact: true}).fill(assumedValue);
    await dialog.getByRole("button", {name: "Apply", exact: true}).click();
    expect(await page.evaluate(() => window.messages.at(-1))).toMatchObject({
      type: "setExecutionFeedback", eventId: id, feedbackId: `receive:${id}`, value: Number(assumedValue),
    });
  }
  expect(await page.evaluate(() => window.messages.filter((message) => message.type === "eventClick"))).toEqual([]);
});

test("playback toolbar follows host state and allows one branch occurrence to be changed", async ({page}) => {
  await mount(page, "paused");
  await page.getByRole("button", {name: "Run execution", exact: true}).click();
  expect(await page.evaluate(() => window.messages.at(-1))).toEqual({type: "executionControl", action: "play"});
  await page.evaluate(() => window.dispatchEvent(new MessageEvent("message", {data: {type: "setExecutionState", state: {status: "running", steps: 8}}})));
  await page.getByRole("button", {name: "Pause execution", exact: true}).click();
  expect(await page.evaluate(() => window.messages.at(-1))).toEqual({type: "executionControl", action: "pause"});
  await page.evaluate(() => window.dispatchEvent(new MessageEvent("message", {data: {type: "setExecutionState", state: {status: "completed", steps: 8}}})));
  await expect(page.getByRole("button", {name: "Execution complete", exact: true})).toBeDisabled();
  await page.locator('.q1timeline-control-chip[data-event-id="branch"]').click();
  await expect(page.getByRole("dialog", {name: "Branch paths"})).toContainText("Default path");
  await expect(page.getByRole("button", {name: "Compare both", exact: true})).toHaveCount(0);
  await page.getByRole("button", {name: "Jump · @done", exact: true}).click();
  expect(await page.evaluate(() => window.messages.at(-1))).toEqual({type: "setExecutionBranch", eventId: "branch", branchId: "branch:2", path: "taken"});
});

for (const [mode, count] of [["playback", 3], ["analysis", 1], ["analysis", 3], ["preview", 3]]) {
  test(`loop options support keyboard focus and Escape in ${mode} with ${count} iterations`, async ({page}) => {
    const repo = path.resolve(__dirname, "../../..");
    const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
      "import json", "from q1timeline.analysis.api import analyze_text", "from q1timeline.playback import PlaybackSession",
      "from q1timeline.render.html import render_html",
      `program="move ${count},R0\\nnop\\nbody: wait 100\\nloop R0,@body\\nstop\\n"`,
      ...(mode === "playback" ? [
        's=PlaybackSession({"seq":("keyboard-loop.q1asm",program)})',
        "s.run(budget_ms=1000,max_steps=50)", "ir=s.snapshot()",
      ] : ['ir=analyze_text(program,file="keyboard-loop.q1asm",sequencer_id="seq")']),
      ...(mode === "preview" ? [
        "from q1timeline.analysis.interpreter import interpret_program", "from q1timeline.q1asm.parser import parse_q1asm",
        "from q1timeline.analysis.alignment import align_timelines", "from q1timeline.ir.serialize import timeline_ir_from_states",
        "key=next(e['meta']['loop_preview_key'] for e in ir['events'] if e['kind']=='loop_block')",
        'state=interpret_program(parse_q1asm(program,file="keyboard-loop.q1asm"),sequencer_id="seq",loop_preview_counts={key:2})',
        "align_timelines([state],mode='none')", "ir=timeline_ir_from_states([state])",
      ] : []),
      "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
    ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
    const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    await mount(page, ir.playback?.status || "idle", ir, {}, {}, shell);
    const loop = ir.events.find((item) => item.kind === "loop_block");
    const owner = page.locator(`.timeline-loop-range[data-event-id="${loop.id}"]`);
    const popover = page.locator("#q1timeline-control-popover");
    await owner.press("Enter");
    if (count === 1) {
      await expect(popover.getByRole("button", {name: "Show next iteration", exact: true})).toBeDisabled();
      await expect(popover.getByRole("button", {name: "Reset loop preview", exact: true})).toBeDisabled();
      await expect(popover).toBeFocused();
      await popover.press("Escape");
    } else {
      const next = popover.getByRole("button", {name: mode === "playback" ? "Expand executed iterations" : "Show next iteration", exact: true});
      await expect(next).toBeFocused();
      await next.press("Escape");
    }
    await expect(popover).toHaveCount(0);
    await expect(owner).toBeFocused();
    if (count > 1) {
      await owner.press(" ");
      const next = popover.getByRole("button", {name: mode === "playback" ? "Expand executed iterations" : "Show next iteration", exact: true});
      await expect(next).toBeFocused();
      if (mode === "preview") {
        await next.press("Tab");
        const reset = popover.getByRole("button", {name: "Reset loop preview", exact: true});
        await expect(reset).toBeFocused();
        await reset.press("Enter");
        await expect(owner).toHaveAttribute("data-state", "1");
      } else await next.press("Enter");
      await expect(owner).toBeFocused();
      if (mode === "playback") {
        const hiddenWait = ir.events.find((item) => item.kind === "wait" && loop.meta.hidden_event_ids.includes(item.id));
        const hidden = page.locator(`.timeline-block[data-event-id="${hiddenWait.id}"]`);
        await expect(hidden).toBeVisible();
        await owner.press("Enter");
        const collapse = popover.getByRole("button", {name: "Collapse executed iterations", exact: true});
        await expect(collapse).toBeFocused();
        await collapse.press("Enter");
        await expect(hidden).toHaveCount(0);
        await expect(owner).toBeFocused();
      } else {
        expect(await page.evaluate(() => window.messages.filter((message) => message.type === "setLoopPreview")))
          .toEqual([{type: "setLoopPreview", loopKey: loop.meta.loop_preview_key, visibleIterations: mode === "preview" ? 1 : 2}]);
      }
    }
    expect(await page.evaluate(() => window.messages.filter((message) => message.type === "eventClick" || message.type === "sourceClick"))).toEqual([]);
  });
}

test("loop options stay local for clicks and keyboard activation while ordinary blocks still open their source", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.playback import PlaybackSession", "from q1timeline.render.html import render_html",
    's=PlaybackSession({"seq":("loop.q1asm","move 3,R0\\nnop\\nbody: wait 100\\nloop R0,@body\\nstop\\n")})',
    "s.run(budget_ms=1000,max_steps=50)", "ir=s.snapshot()",
    "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  const loop = ir.events.find((item) => item.kind === "loop_block" && item.meta.playback);
  const hiddenWait = ir.events.find((item) => item.kind === "wait" && loop.meta.hidden_event_ids.includes(item.id));
  const firstWait = ir.events.find((item) => item.kind === "wait" && loop.meta.first_iteration_event_ids.includes(item.id));
  for (const action of ["click", "Enter", " "]) {
    await mount(page, ir.playback.status, ir, {}, {}, shell);
    const loopNode = page.locator(`.timeline-loop-range[data-event-id="${loop.id}"]`);
    const hidden = page.locator(`.timeline-block[data-event-id="${hiddenWait.id}"]`);
    await expect(hidden).toHaveCount(0);
    if (action === "click") await loopNode.click();
    else await loopNode.press(action);
    const expand = page.getByRole("button", {name: "Expand executed iterations", exact: true});
    await expect(expand).toBeVisible();
    expect(await page.evaluate(() => window.messages.filter((message) => message.type === "eventClick" || message.type === "sourceClick"))).toEqual([]);
    await expand.click();
    await expect(hidden).toBeVisible();
    await loopNode.click();
    await page.getByRole("button", {name: "Collapse executed iterations", exact: true}).click();
    await expect(hidden).toHaveCount(0);
    expect(await page.evaluate(() => window.messages.filter((message) => ["eventClick", "sourceClick", "setLoopPreview"].includes(message.type)))).toEqual([]);
    await page.locator(`.timeline-block[data-event-id="${firstWait.id}"]`).click();
    expect(await page.evaluate(() => window.messages.filter((message) => message.type === "eventClick")))
      .toEqual([{type: "eventClick", eventId: firstWait.id, preserveFocus: true}]);
  }
});

test("executed loop expands locally while the original trace stays available", async ({page}) => {
  await mount(page);
  await expect(page.locator('.timeline-block[data-event-id="w1"]')).toHaveCount(0);
  await page.locator('.timeline-loop-range[data-event-id="loop"]').click();
  await page.getByRole("button", {name: "Expand executed iterations", exact: true}).click();
  await expect(page.locator('.timeline-block[data-event-id="w1"]')).toHaveCount(1);
  await page.locator('.timeline-loop-range[data-event-id="loop"]').click();
  await page.getByRole("button", {name: "Collapse executed iterations", exact: true}).click();
  await expect(page.locator('.timeline-block[data-event-id="w1"]')).toHaveCount(0);
  expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent).events.length)).toBe(7);
  expect(await page.evaluate(() => window.messages.filter((message) => message.type === "setLoopPreview"))).toEqual([]);
  await page.evaluate(() => window.dispatchEvent(new MessageEvent("message", {data: {type: "highlightEventIds", highlightEventIds: ["q1"]}})));
  await expect(page.locator('.timeline-block[data-event-id="q1"]')).toHaveCount(0);
  await expect(page.locator('.timeline-loop-range[data-event-id="loop"]')).toHaveAttribute("aria-selected", "true");
  await expect(page.locator('.timeline-block[data-event-id="w1"]')).toHaveCount(0);
});

test("actual golden-section execution folds fifteen observed wait iterations", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const ir = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "from q1timeline.playback import PlaybackSession", "import json",
    "s=PlaybackSession.from_project('examples/golden-section/q1timeline.yml')",
    "s.run(budget_ms=1000,max_steps=100)", "print(json.dumps(s.snapshot()))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  expect(ir.playback.status).toBe("paused");
  expect(ir.playback.loops[0].count).toBe(15);
  await mount(page, "paused", ir);
  await page.locator('#q1timeline-wait-fold-button').click();
  await expect(page.getByRole("button", {name: "Run execution", exact: true})).toBeEnabled();
  const loop = ir.events.find((item) => item.kind === "loop_block" && item.meta.playback);
  const firstWait = ir.events.find((item) => item.kind === "wait" && loop.meta.first_iteration_event_ids.includes(item.id));
  const followingWait = ir.events.find((item) => item.kind === "wait" && item.t0.value === loop.t1.value);
  const hiddenWait = ir.events.find((item) => item.kind === "wait" && loop.meta.hidden_event_ids.includes(item.id));
  const hidden = page.locator(`.timeline-block[data-event-id="${hiddenWait.id}"]`);
  await expect(hidden).toHaveCount(0);
  const loopNode = page.locator(`.timeline-loop-range[data-event-id="${loop.id}"]`);
  await expect(loopNode.locator('.loop-range-label')).toHaveText("×15");
  await expect(loopNode.locator('.loop-range-label')).toBeVisible();
  await expect(loopNode.locator('.loop-range-label')).toHaveCSS("opacity", "1");
  const collapsed = await loopGeometry(page, loop.id, firstWait.id, followingWait.id);
  const repeatBounds = await page.locator('.ruler .q1timeline-playback-fold').boundingBox();
  const repeatTab = await page.locator('.ruler .q1timeline-playback-fold button').boundingBox();
  expect(repeatBounds.width).toBeLessThanOrEqual(100);
  expect(repeatBounds.width).toBeGreaterThanOrEqual(repeatTab.width);
  const repeatWidth = await page.locator('.ruler .q1timeline-playback-fold').evaluate(node => parseFloat(node.style.width));
  expect(collapsed.loopWidth).toBeCloseTo(collapsed.firstWidth, 2);
  expect(collapsed.afterLeft).toBeCloseTo(collapsed.loopLeft + collapsed.loopWidth + repeatWidth, 2);
  expect(collapsed.ticks.some((tick) => tick.time > loop.meta.first_iteration_end.value && tick.time < loop.t1.value)).toBe(false);
  const firstLeft = await page.locator(`.timeline-block[data-event-id="${firstWait.id}"]`).evaluate(node => parseFloat(node.style.left));
  const followingWidth = await page.locator(`.timeline-block[data-event-id="${followingWait.id}"]`).evaluate(node => parseFloat(node.style.width));
  for (const tick of collapsed.ticks) {
    if (tick.time >= firstWait.t0.value && tick.time <= firstWait.t1.value) {
      expect(tick.left).toBeCloseTo(firstLeft + (tick.time - firstWait.t0.value) / firstWait.duration.value * collapsed.firstWidth, 2);
    } else if (tick.time >= followingWait.t0.value && tick.time <= followingWait.t1.value) {
      expect(tick.left).toBeCloseTo(collapsed.afterLeft + (tick.time - followingWait.t0.value) / followingWait.duration.value * followingWidth, 2);
    }
  }
  await expect(page.locator('.ruler .q1timeline-playback-fold')).toHaveCount(1);
  await page.screenshot({path: test.info().outputPath("golden-collapsed.png"), fullPage: true});
  const loopBounds = await loopNode.boundingBox();
  await loopNode.click({position: {x: loopBounds.width / 2, y: loopBounds.height / 2}});
  const expand = page.getByRole("button", {name: "Expand executed iterations", exact: true});
  const popupBounds = await page.locator('#q1timeline-control-popover').boundingBox();
  expect(popupBounds.width).toBeGreaterThanOrEqual(180);
  expect((await expand.boundingBox()).height).toBeLessThanOrEqual(30);
  await page.screenshot({path: test.info().outputPath("golden-loop-options.png"), fullPage: true});
  await expand.click();
  await expect(hidden).toHaveCount(1);
  await expect(loopNode.locator(".loop-range-label")).toBeHidden();
  const expanded = await loopGeometry(page, loop.id, firstWait.id, followingWait.id);
  expect(expanded.loopWidth / expanded.firstWidth).toBeCloseTo(15, 2);
  expect(expanded.afterLeft).toBeCloseTo(expanded.loopLeft + expanded.loopWidth, 2);
  expect(expanded.afterLeft).toBeGreaterThan(collapsed.afterLeft);
  expect(expanded.ticks.some((tick) => tick.time > loop.meta.first_iteration_end.value && tick.time < loop.t1.value)).toBe(true);
  for (const tick of expanded.ticks) {
    if (tick.time < loop.t0.value || tick.time > loop.t1.value) continue;
    expect(tick.left).toBeCloseTo(expanded.loopLeft + (tick.time - loop.t0.value) / firstWait.duration.value * expanded.firstWidth, 1);
  }
  await expect(page.locator('.ruler .q1timeline-playback-fold')).toHaveCount(0);
  await expect(page.locator('#q1timeline-execution-status')).toHaveAttribute("title", /Paused.*100 instructions/);
  await expect(page.locator('.q1timeline-control-chip-feedback')).toHaveCount(2);
  await expect(page.locator('[data-value="trace-kind"]')).toHaveText("PATH EXECUTION");
  const hardwareTrace = await page.evaluate(() => {
    const ir = JSON.parse(document.getElementById("timeline-ir").textContent);
    const model = window.q1lensHardwareState;
    const trace = model.executionTrace(model.createIndex(ir), "golden_section", "local");
    return {reason: trace.reason, ids: trace.instructions.map((instruction) => instruction.event.id),
      warnings: model.snapshot(model.createIndex(ir), "golden_section", 491809, "local").warnings};
  });
  expect(hardwareTrace.reason).toBeNull();
  expect(hardwareTrace.ids).toHaveLength(100);
  expect(hardwareTrace.warnings.some((message) => message.includes("Collapsed loop"))).toBe(false);
  expect(hardwareTrace.ids).toEqual(ir.events.filter((item) => item.kind === "q1_issue").map((item) => item.id));
  await page.screenshot({path: test.info().outputPath("golden-expanded.png"), fullPage: true});
  await loopNode.click();
  await page.getByRole("button", {name: "Collapse executed iterations", exact: true}).click();
  const collapsedAgain = await loopGeometry(page, loop.id, firstWait.id, followingWait.id);
  expect(collapsedAgain).toEqual(collapsed);
  const targetTime = followingWait.t0.value + 1000;
  const position = await page.evaluate(({targetTime, wait}) => {
    const node = document.querySelector(`.timeline-block[data-event-id="${wait.id}"]`);
    const bounds = node.getBoundingClientRect();
    return {x: bounds.left + (targetTime - wait.t0.value) / wait.duration.value * bounds.width,
      y: node.closest(".lane-track").getBoundingClientRect().bottom - 2,
      nsPerPixel: wait.duration.value / bounds.width};
  }, {targetTime, wait: followingWait});
  await page.mouse.click(position.x, position.y);
  expect(Math.abs(Number(await page.getByRole("spinbutton", {name: "Selected time in nanoseconds"}).inputValue()) - targetTime)).toBeLessThanOrEqual(position.nsPerPixel);
  await page.locator('.ruler .q1timeline-playback-fold button').click();
  await expect(hidden).toHaveCount(1);
  expect(await loopGeometry(page, loop.id, firstWait.id, followingWait.id)).toEqual(expanded);
});

test("loop and unchanged-interval toggles invalidate their scales independently", async ({page}) => {
  const ir = {sequencers: [{id: "seq"}], playback: {status: "completed", steps: 6}, events: [
    event("w0", "wait", 0, 100), event("w1", "wait", 100, 200), event("update", "upd_param", 200, 204),
    event("tail", "wait", 204, 2204), event("end", "upd_param", 2204, 2208),
    event("loop", "loop_block", 0, 200, {playback: true, loop_id: "loop", loop_preview_key: "loop", count: 2,
      period: 100, wait_only: true, first_iteration_end: 100, first_iteration_event_ids: ["w0"], hidden_event_ids: ["w1"]}),
  ]};
  await mount(page, "completed", ir);
  const foldedBoth = await loopGeometry(page, "loop", "w0", "update");
  const toggle = page.locator('#q1timeline-wait-fold-button');
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator('.timeline-block[data-event-id="w1"]')).toHaveCount(0);
  await expect(page.locator('.ruler .q1timeline-playback-fold')).toHaveCount(1);
  const onlyLoop = await loopGeometry(page, "loop", "w0", "update");
  const repeated = await page.locator('.ruler .q1timeline-playback-fold').evaluate(node => parseFloat(node.style.width));
  expect(onlyLoop.loopWidth).toBeCloseTo(onlyLoop.firstWidth, 2);
  expect(onlyLoop.afterLeft).toBeCloseTo(onlyLoop.loopLeft + onlyLoop.loopWidth + repeated, 2);
  expect((await page.locator('.ruler .q1timeline-playback-fold').boundingBox()).width).toBeLessThanOrEqual(100);
  await toggle.click();
  expect(await loopGeometry(page, "loop", "w0", "update")).toEqual(foldedBoth);
  await toggle.click();
  await page.locator('.timeline-loop-range[data-event-id="loop"]').click();
  await page.getByRole("button", {name: "Expand executed iterations", exact: true}).click();
  await expect(page.locator('.ruler .q1timeline-playback-fold')).toHaveCount(0);
  const expanded = await loopGeometry(page, "loop", "w0", "update");
  expect(expanded.loopWidth / expanded.firstWidth).toBeCloseTo(2, 2);
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await toggle.click();
  await expect(page.locator('.timeline-block[data-event-id="w1"]')).toHaveCount(1);
  await expect(page.locator('.ruler .q1timeline-playback-fold')).toHaveCount(0);
  await toggle.click();
  expect(await loopGeometry(page, "loop", "w0", "update")).toEqual(expanded);
});

for (const [eventId, clock] of [["w1", "RT"], ["q1", "Q1 core"]]) {
  test(`source-range zoom highlights a hidden ${clock} occurrence's loop without unfolding`, async ({page}) => {
    await mount(page);
    const selected = page.locator(`.timeline-block[data-event-id="${eventId}"]`);
    await expect(selected).toHaveCount(0);
    await page.evaluate((eventId) => window.dispatchEvent(new MessageEvent("message", {data: {
      type: "highlightEventIds", highlightEventIds: [eventId], zoomToRange: true,
      sourceRange: {file: "playback.q1asm", startLine: 2, endLine: 2},
    }})), eventId);
    await expect(selected).toHaveCount(0);
    await expect(page.locator('.timeline-loop-range[data-event-id="loop"]')).toHaveAttribute("aria-selected", "true");
    await expect(page.locator('#q1timeline-related-block-summary')).toContainText("Loop 0..200 ns · analyzed occurrences · folds preserved");
    await expect(page.locator('.ruler .q1timeline-playback-fold')).toHaveCount(1);
    expect(await page.evaluate(() => window.getSavedTimelineState().expandedPlaybackLoops)).toEqual([]);
    expect(await page.evaluate(() => window.getSavedTimelineState().timelineExpandedGaps)).toEqual([]);
  });
}

test("legacy auto-expansions reset once and explicit choices stay only with their execution snapshot", async ({page}) => {
  const legacy = {expandedPlaybackLoops: ["loop"], timelineWaitsCollapsed: false,
    timelineExpandedGaps: [{start: 16, end: 184, fullMin: 0, fullMax: 200, basis: "local"}]};
  await mount(page, "completed", undefined, legacy, {playbackSnapshotId: "trace:1"});
  await expect(page.locator('.timeline-block[data-event-id="w1"]')).toHaveCount(0);
  const migrated = await page.evaluate(() => window.getSavedTimelineState());
  expect(migrated).toMatchObject({timelineFoldStateVersion: 2, timelineFoldSnapshotId: "trace:1",
    timelineWaitsCollapsed: true, timelineExpandedGaps: [], expandedPlaybackLoops: []});
  await page.locator('.timeline-loop-range[data-event-id="loop"]').click();
  await page.getByRole("button", {name: "Expand executed iterations", exact: true}).click();
  const explicit = await page.evaluate(() => window.getSavedTimelineState());
  await mount(page, "completed", undefined, explicit, {playbackSnapshotId: "trace:1"});
  await expect(page.locator('.timeline-block[data-event-id="w1"]')).toHaveCount(1);
  await mount(page, "completed", undefined, explicit, {playbackSnapshotId: "trace:2"});
  await expect(page.locator('.timeline-block[data-event-id="w1"]')).toHaveCount(0);
});

test("full golden execution compresses explicit waits and keeps nested loops folded during source inspection", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const ir = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "from q1timeline.playback import PlaybackSession", "import json",
    "s=PlaybackSession.from_project('examples/golden-section/q1timeline.yml')",
    "s.run(budget_ms=5000,max_steps=10000)", "print(json.dumps(s.snapshot()))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, maxBuffer: 50 * 1024 * 1024, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  expect(ir.playback.steps).toBeGreaterThan(100);
  const loop = ir.events.find((item) => item.kind === "loop_block" && item.meta.count === 15);
  const firstWait = ir.events.find((item) => item.kind === "wait" && loop.meta.first_iteration_event_ids.includes(item.id));
  const hiddenWait = ir.events.find((item) => item.kind === "wait" && loop.meta.hidden_event_ids.includes(item.id));
  await mount(page, ir.playback.status, ir);
  await expect(page.locator('#q1timeline-wait-fold-button')).toBeEnabled();
  await expect(page.locator('#q1timeline-wait-fold-button')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(`.timeline-block[data-event-id="${hiddenWait.id}"]`)).toHaveCount(0);
  const original = JSON.stringify(ir);
  const compression = await page.evaluate(({start, end}) => {
    const ir = JSON.parse(document.getElementById("timeline-ir").textContent);
    const adapter = window.q1timelineTimelineAdapter;
    const waits = adapter.buildWaitTimeScale(ir, {timeBasis: "aligned"});
    const scale = adapter.buildTimelineTimeScale(ir, {timeBasis: "aligned", waitGaps: waits.gaps});
    const ticks = [...document.querySelectorAll(".ruler .tick")].map((node) => ({time: parseFloat(node.textContent), left: parseFloat(node.style.left)}));
    return {gaps: waits.gaps.length, duration: scale.toDisplay(end) - scale.toDisplay(start),
      ticks: ticks.map((tick) => ({left: tick.left, time: tick.time,
        hidden: scale.gaps.some(gap => tick.time > gap.start && tick.time < gap.end)})),
      roundTrip: scale.fromDisplay(scale.toDisplay(start + 1000))};
  }, {start: firstWait.t0.value, end: firstWait.t1.value});
  expect(compression.gaps).toBeGreaterThan(0);
  expect(compression.duration).toBeLessThan(65535 / 10);
  expect(compression.roundTrip).toBeCloseTo(firstWait.t0.value + 1000, 6);
  for (const [index, tick] of compression.ticks.entries()) {
    expect(tick.hidden).toBe(false);
    expect(tick.left).toBeGreaterThanOrEqual(0);
    expect(tick.left).toBeLessThanOrEqual(100);
    if (index) expect(tick.left).toBeGreaterThan(compression.ticks[index - 1].left);
  }
  await page.screenshot({path: test.info().outputPath("golden-full-folded.png"), fullPage: true});
  for (const zoomToRange of [false, true]) {
    await page.evaluate(({id, zoomToRange}) => window.dispatchEvent(new MessageEvent("message", {data: {
      type: "highlightEventIds", highlightEventIds: [id], zoomToRange,
      sourceRange: {file: "golden_section.q1asm", startLine: 87, endLine: 87},
    }})), {id: hiddenWait.id, zoomToRange});
    await expect(page.locator(`.timeline-block[data-event-id="${hiddenWait.id}"]`)).toHaveCount(0);
    await expect(page.locator(`.timeline-loop-range[data-event-id="${loop.id}"]`)).toHaveAttribute("aria-selected", "true");
    expect(await page.evaluate(() => window.getSavedTimelineState().expandedPlaybackLoops)).toEqual([]);
    expect(await page.evaluate(() => window.getSavedTimelineState().timelineExpandedGaps)).toEqual([]);
  }
  expect(await page.evaluate(() => document.getElementById("timeline-ir").textContent)).toEqual(original);
});

test("Q1 packet markers connect only their RT execution across selection, layout and folding", async ({page}) => {
  const ir = {sequencers: [{id: "seq"}], playback: {status: "completed", steps: 4}, events: [
    event("setter", "q1_issue", 4000, 4004, {op: "set_freq"}),
    {...event("pending", "latched_state_pending", 4004, 4004,
      {field: "frequency", value: 100, q1_issue_event_id: "setter"}), lane: "state.pending"},
    event("issue", "q1_issue", 5004, 5008, {op: "play", rt_packet_id: "packet"}),
    event("move", "q1_issue", 5008, 5012, {op: "move"}),
    event("other-issue", "q1_issue", 8000, 8004, {op: "play", rt_packet_id: "other"}),
    event("wait", "wait", 0, 10000),
    ...[0, 1].map((path) => ({...event(`path${path}`, "play", 10000, 10100,
      {path, rt_packet_id: "packet", q1_issue_event_id: "issue", applied_state: {frequency: 100},
        applied_setter_event_ids: {frequency: "pending"}}), lane: `rt.path${path}`})),
    event("other-rt", "play", 10100, 10200, {rt_packet_id: "other", q1_issue_event_id: "other-issue"}),
  ]};
  const saved = {timelineFoldStateVersion: 2, timelineFoldSnapshotId: "markers", timelineWaitsCollapsed: false};
  const host = {playbackSnapshotId: "markers"};
  await mount(page, "completed", ir, saved, host);
  await expandQ1Sections(page);
  const marker = page.locator('.timeline-issue-marker[data-event-id="issue"]');
  const rt = page.locator('.timeline-block[data-event-ids~="path0"]');
  const other = page.locator('.timeline-block[data-event-id="other-rt"]');
  const lines = page.locator('.issue-connector');
  const time = page.getByRole("spinbutton", {name: "Selected time in nanoseconds"});
  async function expectEndpoints() {
    await expect(lines).toHaveCount(1);
    await expect.poll(() => page.evaluate(() => {
      const line = document.querySelector('.issue-connector');
      if (!line) return Infinity;
      const matrix = line.getScreenCTM();
      const start = line.getPointAtLength(0).matrixTransform(matrix);
      const end = line.getPointAtLength(line.getTotalLength()).matrixTransform(matrix);
      const issue = document.querySelector('.timeline-issue-marker[data-event-id="issue"]').getBoundingClientRect();
      const rt = document.querySelector('.timeline-block[data-event-ids~="path0"]').getBoundingClientRect();
      return Math.max(Math.abs(start.x - issue.left), Math.abs(end.x - rt.left));
    })).toBeLessThanOrEqual(1);
  }
  await expect(marker).toHaveAttribute("data-issue-marker", "true");
  expect((await marker.getAttribute("data-rt-event-ids")).split(/\s+/).sort()).toEqual(["path0", "path1"]);
  await expect(marker).toHaveText("");
  await expect(marker).toHaveAccessibleName(/Issue play.*Q1 instruction start:.*5004/);
  const markerBounds = await marker.boundingBox();
  const nextBounds = await page.locator('.timeline-block[data-event-id="move"]').boundingBox();
  expect(markerBounds.x + markerBounds.width).toBeLessThanOrEqual(nextBounds.x + 0.05);
  await expect(rt).toHaveCount(1);
  // Use instruction slots for pointer input when the full time extent makes a 4 ns issue subpixel.
  await page.getByRole("button", {name: "Instruction", exact: true}).click();
  await marker.click();
  await expect(rt).toHaveClass(/is-instruction-related/);
  await expect(page.locator('.timeline-block.is-instruction-related')).toHaveCount(1);
  await expect(other).not.toHaveClass(/is-instruction-related/);
  await expect(time).toHaveValue("10000");
  await expectEndpoints();
  await rt.click();
  await expect(marker).toHaveClass(/is-instruction-related/);
  await expect(page.locator('.timeline-issue-marker[data-event-id="other-issue"]')).not.toHaveClass(/is-instruction-related/);
  await expectEndpoints();
  await page.locator('.timeline-block[data-event-id="setter"]').click();
  await expect(rt).toHaveClass(/is-parameter-apply/);
  await expect(lines).toHaveCount(0);
  await page.locator('.timeline-block[data-event-id="move"]').click();
  await expect(lines).toHaveCount(0);
  for (const key of ["Enter", "Space"]) {
    await marker.focus();
    await marker.press(key);
    await expect(marker).toBeFocused();
    await expect(marker).toHaveAttribute("aria-selected", "true");
    await expectEndpoints();
  }
  for (const layout of ["Instruction", "Time scale"]) {
    await page.getByRole("button", {name: layout, exact: true}).evaluate((button) => button.click());
    await expect(marker).toBeFocused();
    await expectEndpoints();
  }
  await page.setViewportSize({width: 1000, height: 1000});
  await expect(marker).toBeFocused();
  await expectEndpoints();
  await page.locator('#q1timeline-wait-fold-button').evaluate((button) => button.click());
  await expect(page.locator('#q1timeline-wait-fold-button')).toHaveAttribute("aria-pressed", "true");
  await expect(marker).toBeFocused();
  await expectEndpoints();
  await expect(time).toHaveValue("10000");
  await page.screenshot({path: test.info().outputPath("packet-marker-folded-connection.png"), fullPage: true});

  await mount(page, "completed", ir, {...saved, timelineTimeBasis: "local", timelineWindow: {
    min: 5005, max: 10200, fullMin: 0, fullMax: 10200, basis: "local",
  }}, host);
  await expect(marker).toHaveCount(0);
  await rt.click();
  await expect(rt).toHaveAttribute("aria-selected", "true");
  await expect(lines).toHaveCount(0);
  expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
});

test("packet selection follows exact repeated play occurrences from a real playback trace", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const ir = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.playback import PlaybackSession",
    "session=PlaybackSession({'seq':('issue-loop.q1asm','wait_sync 4\\nmove 2,R0\\nagain:\\nplay 0,0,100\\nloop R0,@again\\nstop')})",
    "session.run(budget_ms=1000,max_steps=30)", "print(json.dumps(session.snapshot()))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const issues = ir.events.filter((item) => item.kind === "q1_issue" && item.meta.op === "play");
  expect(issues).toHaveLength(2);
  expect(issues[0].source).toEqual(issues[1].source);
  expect(issues[0].id).not.toEqual(issues[1].id);
  const loop = ir.events.find((item) => item.kind === "loop_block");
  await mount(page, ir.playback.status, ir);
  await expect(page.locator(`.timeline-issue-marker[data-event-id="${issues[1].id}"]`)).toHaveCount(0);
  await page.locator(`.timeline-loop-range[data-event-id="${loop.id}"]`).click();
  await page.getByRole("button", {name: "Expand executed iterations", exact: true}).click();
  await expandQ1Sections(page);
  for (const issue of issues) {
    const targets = ir.events.filter((item) => item.kind === "play" && item.meta.q1_issue_event_id === issue.id);
    expect(targets).toHaveLength(2);
    const marker = page.locator(`.timeline-issue-marker[data-event-id="${issue.id}"]`);
    const rt = page.locator(`.timeline-block[data-event-ids~="${targets[0].id}"]`);
    expect((await marker.getAttribute("data-rt-event-ids")).split(/\s+/).sort()).toEqual(targets.map((item) => item.id).sort());
    await marker.click();
    await expect(page.locator('.timeline-block.is-instruction-related')).toHaveCount(1);
    await expect(rt).toHaveClass(/is-instruction-related/);
    await expect(page.locator('.issue-connector')).toHaveCount(1);
    await expect(page.getByRole("spinbutton", {name: "Selected time in nanoseconds"})).toHaveValue(String(targets[0].t0.value));
    await rt.click();
    await expect(marker).toHaveClass(/is-instruction-related/);
    await expect(page.locator('.timeline-issue-marker.is-instruction-related')).toHaveCount(1);
    await expect(page.locator('.issue-connector')).toHaveCount(1);
  }
  await page.screenshot({path: test.info().outputPath("packet-markers-real-loop-occurrences.png"), fullPage: true});
  expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
});

test("simulation setter navigation selects RT commits instead of carried branch snapshots", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const datasets = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json",
    "from q1timeline.analysis.api import analyze_text",
    "from q1timeline.render.html import render_html",
    "def fixture(rewrite):",
    "    source = 'set_freq 4000000\\nupd_param 4\\nset_cond 1,1,0,4\\n'",
    "    source += 'set_freq 4000000\\n' if rewrite else ''",
    "    source += 'upd_param 8\\nset_cond 0,0,0,0\\nupd_param 4\\nstop'",
    "    ir = analyze_text(source, file='freq.q1asm', sequencer_id='seq', execution_mode='simulation',",
    "        branch_assumptions={'seq:set_cond:2:1': 'both'}, simulation={'startup_delay_ns': 100})",
    "    return {'ir': ir, 'html': render_html(ir), 'rewrite': rewrite}",
    "print(json.dumps([fixture(rewrite) for rewrite in (False, True)]))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const block = (id) => page.locator(`.timeline-block[data-block-id="${id}"]`);
  for (const {ir, html, rewrite} of datasets) {
    const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    const common = ir.events.find((event) => event.kind === "upd_param" && event.source.line === 2);
    const executed = ir.events.find((event) => event.kind === "upd_param" && event.source.line === 5 &&
      event.meta.branch_comparison_path === "taken");
    const afterSkip = ir.events.filter((event) => event.kind === "upd_param" && event.source.line === 7 &&
      event.meta.branch_comparison_path !== "taken");
    await mount(page, "idle", ir, {}, {viewMode: "normal"}, shell);
    for (const mode of ["Normal view", "Debug view"]) {
      await page.getByRole("button", {name: mode, exact: true}).click();
      for (const line of rewrite ? [1, 4] : [1]) {
        const issue = ir.events.find((event) => event.kind === "q1_issue" && event.source.line === line);
        const rt = line === 1 ? [common] : [executed, ...afterSkip];
        const expected = rt.map((event) => event.id).sort();
        const ids = ir.source_map.by_source[`freq.q1asm:${line}`];
        await page.evaluate((highlightEventIds) => window.postMessage({type: "highlightEventIds", highlightEventIds}, "*"), ids);
        await expect.poll(() => page.locator(".timeline-block.is-parameter-apply").evaluateAll((nodes) =>
          nodes.map((node) => node.dataset.blockId).sort())).toEqual(expected);
        for (const event of rt) await expect(block(event.id)).toHaveAttribute("aria-selected", "true");
        if (mode === "Debug view") await expect(block(issue.id)).toHaveAttribute("aria-selected", "true");
        else await expect(block(issue.id)).toBeHidden();
      }
    }
  }
});

test("latched parameter circles apply at the RT cursor boundary without adding a lane", async ({page}) => {
  const ir = {sequencers: [{id: "seq"}], playback: {status: "completed", steps: 4}, events: [
    event("initial", "upd_param", 0, 4, {applied_state: {frequency: 20}}),
    event("setter", "q1_issue", 5000, 5004, {op: "set_freq"}),
    {...event("pending", "latched_state_pending", 5000, 5000,
      {field: "frequency", value: 50, q1_issue_event_id: "setter"}), lane: "state.pending"},
    event("issue", "q1_issue", 5004, 5008, {op: "play", rt_packet_id: "packet"}),
    event("move", "q1_issue", 5008, 5012, {op: "move"}),
    event("wait", "wait", 4, 10000),
    ...[0, 1].map((path) => ({...event(`path${path}`, "play", 10000, 10100,
      {path, rt_packet_id: "packet", q1_issue_event_id: "issue", applied_state: {frequency: 50},
        applied_setter_event_ids: {frequency: "pending"}}), lane: `rt.path${path}`})),
  ]};
  const saved = {timelineFoldStateVersion: 2, timelineFoldSnapshotId: "latched", timelineWaitsCollapsed: false};
  const host = {playbackSnapshotId: "latched"};
  await mount(page, "completed", ir, saved, host);
  await expandQ1Sections(page);
  const pending = page.locator('.timeline-parameter-marker[data-parameter-role="pending"][data-event-id="setter"]');
  const applied = page.locator('.timeline-parameter-marker[data-parameter-role="applied"]');
  const setter = page.locator('.timeline-block[data-event-id="setter"]');
  const rt = page.locator('.timeline-block[data-event-ids~="path0"]');
  const time = page.getByRole("spinbutton", {name: "Selected time in nanoseconds"});
  const lines = page.locator('.parameter-connector');
  const rows = await page.locator('.timeline-row').evaluateAll((nodes) => nodes.map((node) => node.dataset.lane));
  expect(rows).toContain("sequencer:seq:q1_issue");
  expect(rows.every((lane) => !/gain|frequency|parameter|latched/i.test(lane))).toBe(true);
  await expect(pending).toHaveCount(1);
  await expect(applied).toHaveCount(1);
  await expect(pending).toHaveAttribute("data-parameter-time", "5004");
  await expect(applied).toHaveAttribute("data-parameter-time", "10000");
  await expect(pending).toHaveText("");
  await expect(applied).toHaveText("");
  await expect(lines).toHaveCount(0);
  for (const [cursor, isApplied] of [[5003, false], [5004, false], [9999, false], [10000, true], [10001, true], [9999, false]]) {
    await time.fill(String(cursor));
    await time.dispatchEvent("change");
    if (isApplied) await expect(applied).toHaveClass(/is-applied/);
    else await expect(applied).not.toHaveClass(/is-applied/);
    await expect(pending).not.toHaveClass(/is-applied/);
  }
  await page.getByRole("button", {name: "Instruction", exact: true}).click();
  await pending.click();
  await expect(setter).toHaveAttribute("aria-selected", "true");
  await expect(rt).toHaveClass(/is-parameter-apply/);
  await expect(lines).toHaveCount(1);
  await expect(page.locator('.issue-connector')).toHaveCount(0);
  await expect(page.locator('.parameter-connection-label')).toContainText("20");
  await expect(page.locator('.parameter-connection-label')).toContainText("50");
  for (const key of ["Enter", "Space"]) {
    await pending.focus();
    await pending.press(key);
    await expect(setter).toHaveAttribute("aria-selected", "true");
    await expect(lines).toHaveCount(1);
  }
  for (const layout of ["Time scale", "Instruction"]) {
    await page.getByRole("button", {name: layout, exact: true}).evaluate((button) => button.click());
    await expect(lines).toHaveCount(1);
    await time.fill("10000");
    await time.dispatchEvent("change");
    await expect(applied).toHaveClass(/is-applied/);
  }
  await page.setViewportSize({width: 1000, height: 1000});
  // The RT instruction slot is now outside the horizontal viewport.
  await expect(lines).toHaveCount(0);
  await page.getByRole("button", {name: "Time scale", exact: true}).evaluate((button) => button.click());
  await expect(lines).toHaveCount(1);
  await page.locator('#q1timeline-wait-fold-button').evaluate((button) => button.click());
  await expect(lines).toHaveCount(1);
  await expect(applied).toHaveClass(/is-applied/);
  await page.screenshot({path: test.info().outputPath("latched-parameter-applied.png"), fullPage: true});
  await rt.click();
  await expect(lines).toHaveCount(0);
  await expect(page.locator('.issue-connector')).toHaveCount(1);
  await page.keyboard.press("Escape");
  await page.locator('.timeline-block[data-event-id="move"]').click();
  await expect(lines).toHaveCount(0);
  await expect(page.locator('.parameter-connection-label')).toHaveCount(0);
  expect(await page.locator('.timeline-row').evaluateAll((nodes) => nodes.map((node) => node.dataset.lane))).toEqual(rows);

  await mount(page, "completed", ir, {...saved, timelineTimeBasis: "local", timelineWindow: {
    min: 5005, max: 10100, fullMin: 0, fullMax: 10100, basis: "local",
  }}, host);
  await expect(pending).toHaveCount(0);
  await rt.click();
  await expect(lines).toHaveCount(0);
  expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
});

test("an older queued parameter applies while a newer setter remains independently pending", async ({page}) => {
  const ir = {sequencers: [{id: "seq"}], playback: {status: "completed", steps: 4}, events: [
    ...[1, 2].flatMap((index) => [event(`setter${index}`, "q1_issue", index * 4, index * 4 + 4, {op: "set_freq"}),
      {...event(`pending${index}`, "latched_state_pending", index * 4 + 4, index * 4 + 4,
        {field: "frequency", value: index * 100, q1_issue_event_id: `setter${index}`}), lane: "state.pending"},
      event(`issue${index}`, "q1_issue", 12 + index * 4, 16 + index * 4, {op: "upd_param", rt_packet_id: `packet${index}`}),
      event(`rt${index}`, "upd_param", 96 + index * 4, 100 + index * 4,
        {rt_packet_id: `packet${index}`, q1_issue_event_id: `issue${index}`, applied_state: {frequency: index * 100},
          applied_setter_event_ids: {frequency: `pending${index}`}})]),
    event("wait", "wait", 0, 100),
  ]};
  await mount(page, "completed", ir, {timelineWaitsCollapsed: false});
  await page.getByRole("button", {name: "Instruction", exact: true}).click();
  await expandQ1Sections(page);
  const marker = (id) => page.locator(`.timeline-parameter-marker[data-event-id="${id}"]`);
  const time = page.getByRole("spinbutton", {name: "Selected time in nanoseconds"});
  await time.fill("100");
  await time.dispatchEvent("change");
  await expect(marker("rt1")).toHaveClass(/is-applied/);
  await expect(marker("rt2")).not.toHaveClass(/is-applied/);
  for (const index of [1, 2]) {
    await marker(`setter${index}`).click();
    await expect(page.locator('.parameter-connector')).toHaveCount(1);
    await expect(page.locator(`.timeline-block[data-event-id="rt${index}"]`)).toHaveClass(/is-parameter-apply/);
    await expect(page.locator(`.timeline-block[data-event-id="rt${index === 1 ? 2 : 1}"]`)).not.toHaveClass(/is-parameter-apply/);
    const data = JSON.parse(await marker(`setter${index}`).getAttribute("data-parameter-changes"));
    expect(data[0].applyEventIds).toEqual([`rt${index}`]);
  }
  await time.fill("104");
  await time.dispatchEvent("change");
  await expect(marker("rt2")).toHaveClass(/is-applied/);
  await time.fill("103");
  await time.dispatchEvent("change");
  await expect(marker("rt2")).not.toHaveClass(/is-applied/);
  expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
});

function feedbackNavigationFixture() {
  const feedback = (id, kind, time, sequencer, line) => ({...event(id, kind, time, time + (kind === "feedback_com" ? 8 : 0)),
    sequencer_id: sequencer, source: {file: `${sequencer}.q1asm`, line}, lane: "rt.feedback"});
  return {sequencers: [{id: "sender"}, {id: "middle"}, {id: "receiver"}], events: [
    feedback("send", "feedback_com", 80, "sender", 3),
    feedback("inline-send", "feedback_com", 200, "sender", 6),
    feedback("local", "feedback_pop", 240, "sender", 7),
    {...event("crossed-block", "wait", 0, 300), sequencer_id: "middle"},
    feedback("receive", "feedback_pop", 180, "receiver", 5),
    {...event("end", "stop", 300, 300), sequencer_id: "receiver"},
  ], feedback_flows: [
    {id: "cross-flow", from_event_id: "send", to_event_id: "receive", channel: "cross"},
    {id: "inline-flow", from_event_id: "inline-send", to_event_id: "local", channel: "local"},
  ]};
}

function feedbackTimingFixture() {
  const feedback = (id, kind, start, end, sequencer_id, line) => ({...event(id, kind, start, end),
    sequencer_id, lane: "rt.feedback", source: {file: `${sequencer_id}.q1asm`, line,
      raw: kind === "feedback_com" ? `fb_com_data ${id === "send" ? 4 : 16},7,80` : "fb_pop_data 4,R0"}});
  return {sequencers: [{id: "sender"}, {id: "receiver"}],
    alignment:{sender:{mode:"none",offset:0,confidence:"exact"},receiver:{mode:"none",offset:0,confidence:"exact"}}, events: [
    {...event("issue-send", "q1_issue", 0, 4, {op: "fb_com_data"}), sequencer_id: "sender"},
    {...event("issue-pop", "q1_issue", 4, 8, {op: "fb_pop_data"}), sequencer_id: "sender"},
    feedback("send", "feedback_com", 100, 180, "sender", 2),
    feedback("cross-send", "feedback_com", 180, 260, "sender", 3),
    {...event("wait", "wait", 260, 500), sequencer_id: "sender"},
    feedback("pop", "feedback_pop", 500, 500, "sender", 4),
    {...event("receiver-wait", "wait", 0, 700), sequencer_id: "receiver"},
    feedback("cross-pop", "feedback_pop", 400, 400, "receiver", 5),
    {...event("end", "stop", 700, 700), sequencer_id: "receiver"},
  ], feedback_flows: [
    {id: "local", from_event_id: "send", to_event_id: "pop", channel: "4"},
    {id: "cross", from_event_id: "cross-send", to_event_id: "cross-pop", channel: "16"},
  ]};
}

function feedbackCastFixture() {
  const repo=path.resolve(__dirname,"../../..");
  return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python",["-c",[
    "import contextlib,io,json,tempfile", "from pathlib import Path", "from q1timeline.cli import main",
    "with tempfile.TemporaryDirectory() as directory:", "    output=Path(directory)/'ir.json'",
    "    with contextlib.redirect_stdout(io.StringIO()):",
    "        result=main(['analyze','--project','vscode-extension/test/fixtures/feedback-casts/q1timeline.yml','--out',str(output),'--no-render'])",
    "    assert result==0", "    ir=json.loads(output.read_text(encoding='utf-8'))",
    "assert all(item['status']=='completed' for item in ir['execution'].values())",
    "assert not ir['diagnostics']", "print(json.dumps(ir))",
  ].join("\n")],{cwd:repo,encoding:"utf8",env:{...process.env,PYTHONPATH:path.join(repo,"src")}}));
}

async function feedbackTimeX(page,time) {
  const input=page.getByRole("spinbutton",{name:"Selected time in nanoseconds"});
  await input.fill(String(time));await input.dispatchEvent("change");
  return page.locator('.shared-timeline-stage .ruler .hardware-time-cursor').evaluate(node=>node.getBoundingClientRect().x);
}

function linqAnalysisFixture(op="fb_pop_data", wait=2000) {
  const repo=path.resolve(__dirname,"../../..");
  return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python",["-c",[
    "import contextlib,io,json,sys,tempfile", "from pathlib import Path", "from q1timeline.cli import main",
    "from q1timeline.render.html import render_html",
    "op=sys.argv[1]; wait=int(sys.argv[2])",
    "documents={'sender':'wait_sync 4\\nfb_com_data 16,7,4\\nfb_com_data 32,9,4\\nfb_com_data 4,11,4\\nwait 2000\\nfb_pop_data 4,R0\\nstop\\n'}",
    "for seq,channel in [('intra',16),('multi',32)]:",
    "    receive=f'fb_pop_data {channel},R0' if op=='fb_pop_data' else 'fb_pull_data R0,R1'",
    "    documents[seq]=f'wait_sync 4\\nwait {wait}\\n{receive}\\nstop\\n'",
    "with tempfile.TemporaryDirectory() as directory:", "    root=Path(directory)",
    "    for seq,text in documents.items(): (root/f'{seq}.q1asm').write_text(text)",
    "    project={'analysis':{'feedback_transport_delay_ns':200},'sequencers':[{'id':seq,'file':f'{seq}.q1asm','module':'QCM1' if seq=='multi' else 'QCM0'} for seq in documents]}",
    "    config=root/'q1timeline.yml'; config.write_text(json.dumps(project)); output=root/'ir.json'",
    "    with contextlib.redirect_stdout(io.StringIO()): result=main(['analyze','--project',str(config),'--out',str(output),'--no-render'])",
    "    assert result==0", "    ir=json.loads(output.read_text())",
    "print(json.dumps({'ir':ir,'html':render_html(ir,default_mode='debug')}))",
  ].join("\n"),op,String(wait)],{cwd:repo,encoding:"utf8",env:{...process.env,PYTHONPATH:path.join(repo,"src")}}));
}

test("analytical feedback targets follow send latency rather than late pop or pull commands",async({page})=>{
  const {ir}=linqAnalysisFixture("fb_pull_data");
  const expected={sender:72,intra:354,multi:588};
  expect(ir.feedback_flows).toHaveLength(3);
  await mount(page,"completed",ir,{analysisDetailsOpen:false,timelineWaitsCollapsed:false});
  await analysisDetails(page);
  const measurements=[];
  for(const mode of ["Normal view","Debug view"]) {
    await page.getByRole("button",{name:mode,exact:true}).click();
    for(const layout of ["Time scale","Instruction"]) {
      await page.getByRole("button",{name:layout,exact:true}).click();
      for(const folded of layout==="Time scale" ? [false,true] : [false]) {
        const fold=page.locator("#q1timeline-wait-fold-button");
        if(layout==="Time scale" && (await fold.getAttribute("aria-pressed")==="true")!==folded) await fold.click();
        for(const flow of ir.feedback_flows) {
          const from=ir.events.find(item=>item.id===flow.from_event_id),to=ir.events.find(item=>item.id===flow.to_event_id);
          expect(to.t0.value).toBeGreaterThan(expected[to.sequencer_id]);
          for(const role of ["source","target"]) {
            const endpoint=page.locator(`[data-flow-id="${flow.id}"][data-feedback-role="${role}"]`);
            if(role==="target") await expect(endpoint).toHaveAttribute("title",new RegExp(`estimated feedback queue arrival.*${expected[to.sequencer_id]} ns`));
            const x=await endpoint.evaluate(node=>{
              const marker=node.classList.contains("feedback-dot") ? node : node.querySelector(".feedback-endpoint-marker");
              const r=marker.getBoundingClientRect();return r.x+r.width/2;
            });
            const time=role==="source" ? from.t0.value : expected[to.sequencer_id];
            const reference=layout==="Time scale" ? await feedbackTimeX(page,time) : await page.evaluate(({ir,seq,time})=>{
              const rt=ir.events.find(item=>item.sequencer_id===seq && ["wait","feedback_com"].includes(item.kind) && item.t0.value<=time && item.t1.value>time);
              const block=document.querySelector(`.timeline-block[data-event-id="${rt.id}"]`);
              const track=block.closest(".lane-track").getBoundingClientRect();
              const width=parseFloat(getComputedStyle(block.closest(".shared-timeline-stage")).getPropertyValue("--instruction-cell-width"));
              return track.x+width*(Number(block.dataset.instructionIndex)+(time-rt.t0.value)/(rt.t1.value-rt.t0.value));
            },{ir,seq:role==="source" ? from.sequencer_id : to.sequencer_id,time});
            measurements.push({mode,layout,folded,flow:flow.id,role,time,error:Math.abs(x-reference)});
            expect(Math.abs(x-reference),JSON.stringify(measurements.at(-1))).toBeLessThan(.3);
          }
        }
      }
    }
  }
  await page.getByRole("button",{name:"Normal view",exact:true}).click();
  await page.getByRole("button",{name:"Time scale",exact:true}).click();
  await page.screenshot({path:test.info().outputPath("feedback-delivery-latency.png")});
  await test.info().attach("latency-endpoints",{body:JSON.stringify(measurements),contentType:"application/json"});
});

test("LINQ warnings and receiving RT lane markers include site delay for pop and pull",async({page})=>{
  for(const op of ["fb_pop_data","fb_pull_data"]) {
    const {ir,html}=linqAnalysisFixture(op,200);
    const findings=ir.diagnostics.filter(item=>item.category==="feedback_latency_violation");
    expect(findings).toHaveLength(2);
    expect(findings.map(item=>item.details.required_latency_ns).sort()).toEqual([350,580]);
    const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    await mount(page,"completed",ir,{analysisDetailsOpen:false,timelineWaitsCollapsed:false},{},shell);
    await analysisDetails(page);
    for(const mode of ["Normal view","Debug view"]) {
      await page.getByRole("button",{name:mode,exact:true}).click();
      for(const diagnostic of findings) {
        const warning=page.getByRole("region",{name:"Diagnostics"}).locator(".diagnostic.severity-warning")
          .filter({hasText:`${op.replaceAll("_"," ")} on channel ${diagnostic.details.channel} precedes estimated arrival`});
        await expect(warning).toBeVisible();
        await expect(warning).toContainText("200 ns configured transport delay");
        const flow=ir.feedback_flows.find(item=>diagnostic.related_events.includes(item.to_event_id));
        const target=ir.events.find(item=>item.id===flow.to_event_id);
        const marker=page.locator(`[data-flow-id="${flow.id}"][data-feedback-role="target"]`);
        expect(await marker.evaluate(node=>node.closest('.timeline-row').dataset.lane)).toContain(target.sequencer_id);
        await expect(marker).toHaveAttribute("title",new RegExp(`${diagnostic.details.arrival_ns} ns.*200 ns configured transport.*Consumer: ${op}`));
        const x=await marker.evaluate(node=>{
          const r=node.querySelector('.feedback-endpoint-marker').getBoundingClientRect();return r.x+r.width/2;
        });
        expect(Math.abs(x-await feedbackTimeX(page,diagnostic.details.arrival_ns))).toBeLessThan(.3);
      }
    }
    await page.screenshot({path:test.info().outputPath(`${op}-linq-warning.png`)});
  }
});

test("intra and multi cast follow RT transmission and queue reception without displaying Q1 pops as RT commands",async({page})=>{
  const ir=feedbackCastFixture();
  const byId=new Map(ir.events.map(item=>[item.id,item]));
  expect(ir.feedback_flows).toHaveLength(4);
  const expected={sender:[212,532],intra:[204,684],multi_a:[208,848],multi_b:[208,1008]};
  for(const flow of ir.feedback_flows) {
    const from=byId.get(flow.from_event_id),to=byId.get(flow.to_event_id);
    expect([from.t0.value,to.t0.value]).toEqual(expected[to.sequencer_id]);
  }
  expect(ir.sequencers.find(item=>item.id==="intra").module).toBe(ir.sequencers.find(item=>item.id==="sender").module);
  for(const id of ["multi_a","multi_b"]) expect(ir.sequencers.find(item=>item.id===id).module).not.toBe(ir.sequencers.find(item=>item.id==="sender").module);
  await mount(page,"completed",ir,{analysisDetailsOpen:false});
  await analysisDetails(page);
  const measurements=[];
  for(const mode of ["Normal view","Debug view"]) {
    await page.getByRole("button",{name:mode,exact:true}).click();
    for(const layout of ["Time scale","Instruction"]) {
      await page.getByRole("button",{name:layout,exact:true}).click();
      if(layout==="Time scale" && await page.locator("#q1timeline-wait-fold-button").getAttribute("aria-pressed")==="true") {
        await page.locator("#q1timeline-wait-fold-button").click();
      }
      for(const item of ir.events.filter(item=>item.kind==="feedback_pop")) {
        await expect(page.locator(`.timeline-row-runtime .timeline-block[data-event-id="${item.id}"]`)).toHaveCount(0);
      }
      await expect(page.locator('.timeline-row-runtime .timeline-block').filter({hasText:"fb_pop_data"})).toHaveCount(0);
      if (mode === "Debug view") await expandQ1Sections(page);
      const q1Pops=page.locator('.timeline-row-q1-issue .timeline-block').filter({hasText:"fb_pop_data"});
      await expect(q1Pops).toHaveCount(4);
      for(const node of await q1Pops.all()) {
        if(mode==="Debug view") await expect(node).toBeVisible(); else await expect(node).toBeHidden();
        await expect(node).not.toHaveAttribute("data-issue-marker","true");
      }
      const errors=await page.evaluate(({ir,layout})=>{
        const byId=new Map(ir.events.map(item=>[item.id,item]));
        const block=id=>document.querySelector(`.timeline-block[data-event-id="${id}"]`);
        const left=node=>{
          if(node.getClientRects().length) return node.getBoundingClientRect().x;
          const track=node.closest(".lane-track").getBoundingClientRect();
          return track.x+(layout==="Instruction" ? Number(node.dataset.instructionIndex)*parseFloat(getComputedStyle(node.closest(".shared-timeline-stage")).getPropertyValue("--instruction-cell-width")) : track.width*parseFloat(node.style.left)/100);
        };
        return ir.feedback_flows.map(flow=>{
          const from=byId.get(flow.from_event_id),to=byId.get(flow.to_event_id);
          const send=ir.events.find(item=>item.sequencer_id===from.sequencer_id && ["feedback_com","fb_com_data"].includes(item.kind) && item.meta.rt_packet_id===from.meta.source_packet_id);
          const wait=ir.events.find(item=>item.sequencer_id===to.sequencer_id && item.kind==="wait" && item.t0.value<=to.t0.value && item.t1.value>=to.t0.value);
          const expected=[left(block(send.id)),left(block(wait.id))+block(wait.id).getBoundingClientRect().width*(to.t0.value-wait.t0.value)/(wait.t1.value-wait.t0.value)];
          return ["source","target"].map((role,index)=>{
            const endpoint=document.querySelector(`[data-flow-id="${flow.id}"][data-feedback-role="${role}"]`);
            const marker=endpoint.classList.contains("feedback-dot") ? endpoint : endpoint.querySelector(".feedback-endpoint-marker");
            const rect=marker.getBoundingClientRect();
            return {flow:flow.id,role,x:rect.x+rect.width/2,expected:expected[index],time:index ? to.t0.value : from.t0.value};
          });
        }).flat();
      },{ir,layout});
      for(const item of errors) {
        const expected=layout==="Time scale" ? await feedbackTimeX(page,item.time) : item.expected;
        measurements.push({mode,layout,...item,expected,error:Math.abs(item.x-expected)});
        expect(Math.abs(item.x-expected),JSON.stringify({mode,layout,...item,expected})).toBeLessThan(.3);
      }
    }
  }
  expect(await page.evaluate(()=>JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
  await test.info().attach("cast-endpoints",{body:JSON.stringify(measurements),contentType:"application/json"});
});

test("folded self, intra and multi cast feedback lines remain painted across every folded band",async({page})=>{
  const ir=feedbackCastFixture();
  await mount(page,"completed",ir,{analysisDetailsOpen:false});
  const fold=page.locator("#q1timeline-wait-fold-button");
  await expect(fold).toBeEnabled();
  for(const mode of ["Normal view","Debug view"]) {
    await page.getByRole("button",{name:mode,exact:true}).click();
  for(const folded of [true,false,true]) {
    if((await fold.getAttribute("aria-pressed")==="true")!==folded) await fold.click();
    if(!folded) continue;
    const points=await page.evaluate(()=>{
      const bands=Array.from(document.querySelectorAll('.lane-track > .q1timeline-wait-fold:not(.is-expanded)')).map(node=>node.getBoundingClientRect());
      const points=[];
      const add=(node,x,y)=>{
        if(bands.some(r=>x>r.left+3 && x<r.right-3 && y>=r.top && y<r.bottom) && !document.elementFromPoint(x,y)?.closest('.timeline-block')) points.push({flow:node.dataset.flowId,x,y});
      };
      for(const path of document.querySelectorAll('.feedback-connector')) {
        for(let index=1;index<100;index++) {
          const p=path.getPointAtLength(path.getTotalLength()*index/100).matrixTransform(path.getScreenCTM());add(path,p.x,p.y);
        }
      }
      for(const node of document.querySelectorAll('.feedback-inline-capsule')) for(const rail of node.querySelectorAll('.feedback-mini-track')) {
        const r=rail.getBoundingClientRect();
        for(let index=1;index<50;index++) add(node,r.x+r.width*index/50,r.y+r.height/2);
      }
      return points;
    });
    expect(new Set(points.map(item=>item.flow)).size).toBe(4);
    const screenshot=await page.screenshot({path:test.info().outputPath("folded-feedback.png"),scale:"css"});
    const missing=await page.evaluate(async({png,points})=>{
      const img=new Image();img.src=`data:image/png;base64,${png}`;await img.decode();
      const canvas=document.createElement('canvas');canvas.width=img.width;canvas.height=img.height;
      const context=canvas.getContext('2d');context.drawImage(img,0,0);
      return points.filter(({x,y})=>{
        const pixels=context.getImageData(Math.round(x)-2,Math.round(y)-2,5,5).data;
        for(let i=0;i<pixels.length;i+=4) if(pixels[i]>pixels[i+1]+22 && pixels[i+2]>pixels[i+1]+8) return false;
        return true;
      });
    },{png:screenshot.toString("base64"),points});
    await test.info().attach("feedback-paint-probe",{body:JSON.stringify({mode,folded,points:points.length,flows:[...new Set(points.map(item=>item.flow))],missing:missing.length}),contentType:"application/json"});
    expect(missing,"Feedback stroke must remain visible through folded bands").toEqual([]);
  }
  }
});

test("feedback endpoints align with RT commands through layout, mode, zoom and endpoint labels", async ({page}) => {
  const ir = feedbackTimingFixture();
  await mount(page, "completed", ir, {timelineWaitsCollapsed: false, analysisDetailsOpen: false});
  await analysisDetails(page);
  async function checkAnchors() {
    const measurements = await page.evaluate(() => {
      const ir=JSON.parse(document.getElementById("timeline-ir").textContent);
      const view=window.getSavedTimelineState().timelineWindow || {min:0,max:700};
      const center = node => {const r = node.getBoundingClientRect(); return {x:r.x+r.width/2, y:r.y+r.height/2};};
      return [["local", "source", "send"], ["local", "target", "pop"],
        ["cross", "source", "cross-send"], ["cross", "target", "cross-pop"]].map(([flow,role,id]) => {
        const group = document.querySelector(`[data-flow-id="${flow}"][data-feedback-role="${role}"]`);
        if (!group || group.classList.contains("is-clipped") || group.closest(`.is-clipped-${role}`)) {
          return {flow,role,id,clipped:true,visible:!!group && getComputedStyle(group).visibility !== "hidden"};
        }
        const anchorId=role==="target" ? id==="pop" ? "wait" : "receiver-wait" : id;
        const block=document.querySelector(`.timeline-block[data-event-id="${anchorId}"]`);
        const anchor=ir.events.find(item=>item.id===anchorId),pointEvent=ir.events.find(item=>item.id===id);
        const track = group.closest(".lane-track").getBoundingClientRect();
        const marker = group.classList.contains("feedback-dot") ? group : group.querySelector(".feedback-endpoint-marker");
        const point = center(marker);
        const cellWidth = parseFloat(getComputedStyle(block.closest(".shared-timeline-stage")).getPropertyValue("--instruction-cell-width"));
        const fraction=role==="target" ? (pointEvent.t0.value-anchor.t0.value)/(anchor.t1.value-anchor.t0.value) : 0;
        const expected = block.dataset.instructionIndex !== undefined
          ? track.x+(Number(block.dataset.instructionIndex)+fraction)*cellWidth
          : track.x+track.width*(pointEvent.t0.value-view.min)/(view.max-view.min);
        return {flow,role,id,x:point.x,expected,time:pointEvent.t0.value,instruction:block.dataset.instructionIndex !== undefined,
          title:group.getAttribute("title"),eventId:group.dataset.eventId};
      });
    });
    for (const item of measurements) {
      if (item.clipped) {expect(item.visible, JSON.stringify(item)).toBe(false); continue;}
      const expected=item.instruction ? item.expected : await feedbackTimeX(page,item.time);
      expect(Math.abs(item.x-expected), JSON.stringify({...item,expected})).toBeLessThan(.3);
      expect(item.eventId).toBe(item.id);
      expect(item.title).toContain(item.role === "source" ? "Send" : "Receive");
    }
    const connector = page.locator('.feedback-connector[data-flow-id="cross"]');
    const errors = await connector.evaluate(path => {
      const matrix = path.getScreenCTM();
      return ["source", "target"].map((role,index) => {
        if (document.querySelector(`.feedback-endpoint[data-flow-id="cross"][data-feedback-role="${role}"]`).classList.contains("is-clipped")) return 0;
        const point = path.getPointAtLength(index ? path.getTotalLength() : 0).matrixTransform(matrix);
        const marker = document.querySelector(`.feedback-endpoint[data-flow-id="cross"][data-feedback-role="${role}"] .feedback-endpoint-marker`).getBoundingClientRect();
        return Math.hypot(point.x-marker.x-marker.width/2,point.y-marker.y-marker.height/2);
      });
    });
    for (const error of errors) expect(error).toBeLessThan(.3);
  }
  for (const mode of ["Debug view", "Normal view"]) {
    await page.getByRole("button", {name:mode,exact:true}).click();
    for (const layout of ["Time scale", "Instruction"]) {
      await page.getByRole("button", {name:layout,exact:true}).click();
      await checkAnchors();
    }
  }
  await page.getByRole("button", {name:"Time scale",exact:true}).click();
  await page.getByRole("button", {name:"Zoom in",exact:true}).click();
  await checkAnchors();
  const source = page.locator('.feedback-endpoint[data-flow-id="cross"][data-feedback-role="source"]');
  await source.focus();
  await expect(source).toHaveClass(/is-active/);
  await checkAnchors();
  await page.getByRole("button", {name:"Reset zoom",exact:true}).click();
  await page.setViewportSize({width:800,height:800});
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await checkAnchors();
  expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
  await page.screenshot({path:test.info().outputPath("feedback-rt-anchors.png"),fullPage:true});
});

test("clipped feedback continues across the viewport without false source or receive markers", async ({page}) => {
  await mount(page, "completed", feedbackTimingFixture(), {timelineWaitsCollapsed:false,analysisDetailsOpen:false,
    timelineWindow:{min:150,max:450,fullMin:0,fullMax:700,basis:"local"}});
  const local = page.locator('.feedback-inline-capsule[data-flow-id="local"]');
  await expect(local).toBeVisible();
  await expect(local.locator('.feedback-dot-source')).toBeHidden();
  await expect(local.locator('.feedback-dot-target')).toBeHidden();
  await expect(local.locator('.feedback-mini-arrow')).toBeHidden();
  await mount(page, "completed", feedbackTimingFixture(), {timelineWaitsCollapsed:false,analysisDetailsOpen:false,
    timelineWindow:{min:200,max:300,fullMin:0,fullMax:700,basis:"local"}});
  await expect(page.locator('.feedback-endpoint[data-flow-id="cross"][data-feedback-role="source"]')).toBeHidden();
  await expect(page.locator('.feedback-endpoint[data-flow-id="cross"][data-feedback-role="target"]')).toBeHidden();
  await expect(page.locator('.feedback-connector[data-flow-id="cross"]')).not.toHaveAttribute("marker-end");
});

test("short and reversed feedback retain true endpoints and point the arrow at the receiver", async ({page}) => {
  for (const [from,to] of [[200,200.01],[500,100]]) {
    const ir = feedbackTimingFixture();
    ir.events = ir.events.map(item => item.id === "send" ? {...item,t0:{value:from},t1:{value:from+4}}
      : item.id === "pop" ? {...item,t0:{value:to},t1:{value:to}} : item);
    await mount(page, "completed", ir, {timelineWaitsCollapsed:false,analysisDetailsOpen:false});
    await analysisDetails(page);
    const sourceTimeX=await feedbackTimeX(page,from),targetTimeX=await feedbackTimeX(page,to);
    const errors = await page.locator('.feedback-inline-capsule[data-flow-id="local"]').evaluate(node => {
      const center = role => {const r=node.querySelector(`.feedback-dot-${role}`).getBoundingClientRect();return r.x+r.width/2;};
      const arrow=node.querySelector('.feedback-mini-arrow');
      const tip=arrow.ownerSVGElement.createSVGPoint();tip.x=14;tip.y=0;
      return [center("source"),center("target"),
        tip.matrixTransform(arrow.getScreenCTM()).x-center("target")];
    });
    for (const error of [errors[0]-sourceTimeX,errors[1]-targetTimeX,errors[2]]) expect(Math.abs(error)).toBeLessThan(.3);
  }
});

test("self cast remains attached to RT events when its wait interval is folded and expanded", async ({page}) => {
  const ir=feedbackTimingFixture();
  ir.sequencers=ir.sequencers.filter(item=>item.id==="sender");
  ir.events=ir.events.filter(item=>item.sequencer_id==="sender");
  ir.feedback_flows=ir.feedback_flows.filter(item=>item.id==="local");
  await mount(page,"completed",ir,{timelineWaitsCollapsed:false,analysisDetailsOpen:false});
  const fold=page.locator("#q1timeline-wait-fold-button");
  await expect(fold).toBeEnabled();
  for (let index=0;index<3;index++) {
    const errors=await page.locator('.feedback-inline-capsule[data-flow-id="local"]').evaluate(node=>
      [["source","send"],["target","pop"]].map(([role,id])=>{
        const point=node.querySelector(`.feedback-dot-${role}`).getBoundingClientRect();
        const block=document.querySelector(`.timeline-block[data-event-id="${id==="pop" ? "wait" : id}"]`);
        const track=block.closest(".lane-track").getBoundingClientRect();
        const expected=track.x+track.width*parseFloat(block.style.left)/100+(id==="pop" ? block.getBoundingClientRect().width : 0);
        return point.x+point.width/2-expected;
      }));
    for(const error of errors) expect(Math.abs(error)).toBeLessThan(.3);
    if(index<2) await fold.click();
  }
});

async function feedbackPathPoint(path, target) {
  return path.evaluate((node, target) => {
    const length = node.getTotalLength();
    for (let step = 5; step < 96; step++) {
      const point = node.getPointAtLength(length * step / 100).matrixTransform(node.getScreenCTM());
      const hit = document.elementFromPoint(point.x, point.y);
      if (target === "path" ? hit === node : hit?.closest(".timeline-block")?.dataset.eventId === target) {
        return {x: point.x, y: point.y};
      }
    }
    throw new Error(`No feedback path point hits ${target}`);
  }, target);
}

test("feedback RT blocks and connectors navigate in Normal time scale", async ({page}) => {
  const ir = feedbackNavigationFixture();
  await mount(page, "completed", ir, {timelineWaitsCollapsed: false});
  await page.getByRole("button", {name: "Normal view", exact: true}).click();
  const send = page.locator('.timeline-block[data-event-id="send"]');
  await expect(send).toBeVisible();
  await expect(page.locator('.timeline-block[data-event-id="receive"]')).toBeHidden();
  const source = page.locator('.feedback-endpoint[data-flow-id="cross-flow"][data-feedback-role="source"]');
  const target = page.locator('.feedback-endpoint[data-flow-id="cross-flow"][data-feedback-role="target"]');
  const connector = page.locator('.feedback-connector[data-flow-id="cross-flow"]');
  const inline = page.locator('.feedback-inline-capsule[data-flow-id="inline-flow"]');
  const messages = () => page.evaluate(() => window.messages.filter((message) => ["eventClick", "feedbackFlowClick"].includes(message.type)));
  await page.evaluate(() => {window.messages = [];});
  await send.click();
  expect(await messages()).toEqual([{type: "eventClick", eventId: "send", preserveFocus: true}]);
  await page.keyboard.press("Escape");
  for (const [marker, role] of [[source, "source"], [target, "target"],
    [inline.locator('.feedback-dot-source'), "source"], [inline.locator('.feedback-dot-target'), "target"]]) {
    const flowId = marker === source || marker === target ? "cross-flow" : "inline-flow";
    await page.evaluate(() => {window.messages = [];});
    await marker.click(role === "source" ? {position: {x: 1, y: 2}, timeout: 3000} : {timeout: 3000});
    expect(await messages()).toEqual([{type: "feedbackFlowClick", flowId, role}]);
  }
  for (const node of [connector, inline]) {
    const flowId = node === connector ? "cross-flow" : "inline-flow";
    await page.evaluate(() => {window.messages = [];});
    if (node === connector) {
      const point = await feedbackPathPoint(connector, "path");
      await page.mouse.click(point.x, point.y);
    } else await inline.locator('.feedback-mini-track-before').click();
    expect(await messages()).toEqual([{type: "feedbackFlowClick", flowId}]);
    for (const key of ["Enter", "Space"]) {
      await page.evaluate(() => {window.messages = [];});
      await node.focus();
      await node.press(key);
      expect(await messages()).toEqual([{type: "feedbackFlowClick", flowId}]);
    }
  }
  await page.screenshot({path: test.info().outputPath("feedback-source-navigation.png"), fullPage: true});
  expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
});

test("feedback connector crossings preserve block clicks and empty overlay space preserves panning", async ({page}) => {
  await mount(page, "completed", feedbackNavigationFixture(), {timelineWaitsCollapsed: false});
  await page.getByRole("button", {name: "Normal view", exact: true}).click();
  const connector = page.locator('.feedback-connector[data-flow-id="cross-flow"]');
  const crossing = await feedbackPathPoint(connector, "crossed-block");
  await page.evaluate(() => {window.messages = [];});
  await page.mouse.click(crossing.x, crossing.y);
  expect(await page.evaluate(() => window.messages.filter((message) => ["eventClick", "feedbackFlowClick"].includes(message.type))))
    .toEqual([{type: "eventClick", eventId: "crossed-block", preserveFocus: true}]);
  const track = page.locator('.timeline-row[data-lane="sequencer:sender"] .lane-track');
  const bounds = await track.boundingBox();
  const empty = {x: bounds.x + bounds.width * .7, y: bounds.y + 2};
  expect(await page.evaluate(({x, y}) => document.elementFromPoint(x, y).classList.contains("lane-track"), empty)).toBe(true);
  await page.mouse.move(empty.x, empty.y);
  await page.mouse.wheel(0, -400);
  const before = await page.evaluate(() => window.getSavedTimelineState().timelineWindow);
  expect(before.max - before.min).toBeLessThan(300);
  await page.evaluate(() => {window.messages = [];});
  await page.mouse.move(empty.x, empty.y);
  await page.mouse.down();
  await page.mouse.move(empty.x + 60, empty.y, {steps: 4});
  await page.mouse.up();
  const after = await page.evaluate(() => window.getSavedTimelineState().timelineWindow);
  expect(after.max - after.min).toBeCloseTo(before.max - before.min, 6);
  expect(after.min).toBeLessThan(before.min);
  expect(await page.evaluate(() => window.messages.filter((message) => ["eventClick", "feedbackFlowClick"].includes(message.type)))).toEqual([]);
});

test("blocks take precedence over feedback marker overlap while exposed marker edges remain clickable", async ({page}) => {
  const ir = feedbackNavigationFixture();
  ir.events.push(...[["source-block", 70, 90, "sender"], ["target-block", 170, 190, "receiver"],
    ["inline-block", 195, 245, "sender"]].map(([id, start, end, sequencer_id]) => ({...event(id, "wait", start, end), sequencer_id})));
  await mount(page, "completed", ir, {timelineWaitsCollapsed: false});
  await page.getByRole("button", {name: "Normal view", exact: true}).click();
  for (const [selector, eventId, role] of [
    ['.feedback-endpoint[data-feedback-role="source"]', "source-block", "source"],
    ['.feedback-endpoint[data-feedback-role="target"]', "target-block", "target"],
    ['.feedback-inline-capsule', "inline-block", undefined],
  ]) {
    const marker = page.locator(selector);
    const bounds = await marker.boundingBox();
    const point = {x: bounds.x + bounds.width / 2, y: bounds.y + 2};
    expect(await page.evaluate(({x, y}) => document.elementFromPoint(x, y)?.closest('.timeline-block')?.dataset.eventId, point)).toBe(eventId);
    await page.evaluate(() => {window.messages = [];});
    await page.mouse.click(point.x, point.y);
    expect(await page.evaluate(() => window.messages.filter((message) => ["eventClick", "feedbackFlowClick"].includes(message.type))))
      .toEqual([{type: "eventClick", eventId, preserveFocus: true}]);
    await page.keyboard.press("Escape");
    const flowId = await marker.getAttribute("data-flow-id");
    const edge = await marker.boundingBox();
    await page.evaluate(() => {window.messages = [];});
    await page.mouse.click(edge.x + edge.width / 2, edge.y + edge.height - 2);
    expect(await page.evaluate(() => window.messages.filter((message) => ["eventClick", "feedbackFlowClick"].includes(message.type))))
      .toEqual([{type: "feedbackFlowClick", flowId, ...(role ? {role} : {})}]);
  }
});

test("pending RT wait tooltips and inspectors show arm and observation cutoff in both viewers", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const datasets = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_documents, analyze_text", "from q1timeline.render.html import render_html",
    "trigger = 'wait 20\\nwait_trigger 3,4\\nwait 8\\nstop'",
    "def run(source, mode, inputs=None, branches=None):",
    "    return analyze_text(source, file='seq.q1asm', sequencer_id='seq', execution_mode='simulation', alignment_mode=mode,",
    "        simulation={'startup_delay_ns': 100, 'inputs': inputs or {}}, branch_assumptions=branches)",
    "sync = analyze_documents({'seq.q1asm': 'wait 20\\nwait_sync 4\\nstop',",
    "    'peer.q1asm': 'jmp @end\\nwait_sync 4\\nend: wait 40\\nstop'}, execution_mode='simulation',",
    "    alignment_mode='first_wait_sync', simulation={'startup_delay_ns': 100})",
    "completed = run(trigger, 'first_wait_trigger', {'triggers': [{'target': 'seq', 'address': 3, 'time_ns': 150}]})",
    "comparison = run('wait 20\\nset_cond 1,1,0,4\\nwait_trigger 3,4\\nset_cond 0,0,0,4\\nwait 8\\nstop',",
    "    'manual:-120', branches={'seq:set_cond:1:1': 'both'})",
    "print(json.dumps([{'ir': ir, 'html': render_html(ir)} for ir in",
    "    (run(trigger, 'first_wait_trigger'), sync, completed, comparison)]))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const inspector = page.locator("#event-inspector-fields");
  const time = inspector.getByText("Time range", {exact: true}).locator("xpath=following-sibling::dd[1]");
  const text = (arm, cutoff, target) => `Arm ${arm} ns → waiting for ${target} (completion unresolved) · observation cutoff ${cutoff} ns`;
  for (const [index, {ir, html}] of datasets.entries()) {
    const wait = ir.events.find((item) => item.kind === ["trigger_wait", "sync_wait", "wait_trigger", "trigger_wait"][index]);
    const cutoff = [120, 140, 154, 120][index];
    const terminal = ir.events.find((item) => item.kind === "branch_terminal");
    const target = index === 1 ? "synchronization" : "trigger";
    const complete = index === 2;
    expect([wait.t0.value, wait.t1.value, wait.duration.value]).toEqual([120, cutoff, cutoff - 120]);
    expect(wait.meta.pending === true).toBe(!complete);
    if (index < 2) expect(ir.alignment.seq.anchor_event_id).toBe(wait.id);
    expect(Boolean(terminal)).toBe(index === 3);
    if (terminal) expect(terminal.meta.comparison_terminal.source_event_ids).toEqual([wait.id]);
    const aligned = text(0, cutoff - 120, target);
    const local = text(120, cutoff, target);

    await page.setContent(html);
    await analysisDetails(page);
    const svgEvent = (id) => page.locator(`#timeline-root > svg [data-event-id="${id}"]`);
    const svgWait = svgEvent(wait.id);
    await expect(svgWait.locator("title")).toContainText(complete ? "aligned time: Arm 0 ns · Trigger arrival 30 ns · Complete 34 ns" : `aligned time: ${aligned}`);
    if (!complete) await expect(svgWait.locator("title")).toContainText(`observed duration: ${cutoff - 120}`);
    if (terminal) {
      await svgEvent(terminal.id).press("Enter");
      await inspector.getByRole("button", {name: "Show evidence", exact: true}).click();
    } else {
      await svgWait.locator("rect").click();
    }
    await expect(svgWait).toHaveClass(/is-selected/);
    await expect(time).toHaveText(complete ? "Arm 0 ns · Trigger arrival 30 ns · Complete 34 ns" : aligned);

    const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    await mount(page, "idle", ir, {timelineWaitsCollapsed: false}, {viewMode: "normal"}, shell);
    const block = (id) => page.locator(`.timeline-block[data-event-id="${id}"]`);
    const waitBlock = block(wait.id);
    const alignedTiming = complete ? "Arm 0 ns · Trigger arrival 30 ns · Complete 34 ns" : aligned;
    const localTiming = complete ? "Arm 120 ns · Trigger arrival 150 ns · Complete 154 ns" : local;
    await expect(waitBlock).toHaveAttribute("title", new RegExp(alignedTiming.replace(/[()]/g, "\\$&")));
    if (terminal) {
      await block(terminal.id).press("Enter");
      await inspector.getByRole("button", {name: "Show evidence", exact: true}).click();
    } else {
      await waitBlock.press("Enter");
    }
    await expect(waitBlock).toHaveAttribute("aria-selected", "true");
    await expect(time).toHaveText(alignedTiming);
    await page.getByRole("button", {name: "Local time basis"}).click();
    await expect(waitBlock).toHaveAttribute("aria-selected", "true");
    await expect(time).toHaveText(localTiming);
    await expect(waitBlock).toHaveAttribute("title", new RegExp(localTiming.replace(/[()]/g, "\\$&")));
    expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
  }
});

test("RT phase and zero queue bounds follow the active VS Code time basis", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const datasets = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import copy, json",
    "from q1timeline.analysis.api import analyze_documents, analyze_text",
    "from q1timeline.playback import PlaybackSession",
    "from q1timeline.render.html import render_html",
    "source = 'wait 20\\nwait_trigger 2,4\\nwait 8\\nstop'",
    "sim = {'startup_delay_ns': 100, 'inputs': {'triggers': [{'target': 'seq', 'address': 2, 'time_ns': 150}]}}",
    "def run(source, scenario=sim, **options):",
    "    return analyze_text(source, file='seq.q1asm', sequencer_id='seq', execution_mode='simulation',",
    "        alignment_mode='manual:-100', simulation=scenario, **options)",
    "compare = run('wait 20\\nset_cond 1,1,0,0\\nwait_trigger 2,0\\nset_cond 0,0,0,0\\nwait 8\\nstop',",
    "    branch_assumptions={'seq:set_cond:1:1': 'both'})",
    "sync = analyze_documents({'seq.q1asm': 'wait 20\\nwait_sync 4\\nstop', 'peer.q1asm': 'wait 40\\nwait_sync 4\\nstop'},",
    "    execution_mode='simulation', alignment_mode='manual:-100', simulation={'startup_delay_ns': 100})",
    "cutoff = run(source.replace('2,4', '2,40'), scenario={**sim, 'max_time_ns': 160})",
    "sync_cutoff = analyze_documents({'seq.q1asm': 'wait 20\\nwait_sync 40\\nstop', 'peer.q1asm': 'wait 40\\nwait_sync 4\\nstop'},",
    "    execution_mode='simulation', alignment_mode='manual:-100', simulation={'startup_delay_ns': 100, 'max_time_ns': 160})",
    "limit = {'startup_delay_ns': 100, 'max_time_ns': 160}",
    "clipped_wait = run('wait 20\\nwait 100\\nwait 8\\nstop', scenario=limit)",
    "invalid_cutoff = copy.deepcopy(clipped_wait)",
    "next(e for e in invalid_cutoff['events'] if e['meta'].get('planned_rt_end_ns'))['meta']['observation_cutoff_ns'] = 156",
    "clipped_play = analyze_documents({'seq.q1asm': 'wait 20\\nplay 0,1,100\\nwait 8\\nstop',",
    "    'peer.q1asm': 'wait 40\\nstop'}, execution_mode='simulation', alignment_mode='manual:-100', simulation=limit)",
    "late = run('upd_param 0\\n' + 'nop\\n' * 28 + 'wait 8\\nstop')",
    "interrupted = run('wait 20\\nwait_trigger 2,40\\n' + 'nop\\n' * 36 + 'illegal')",
    "legacy = copy.deepcopy(late)",
    "next(event for event in legacy['events'] if event['kind'] == 'upd_param')['meta'].pop('next_dequeue_not_before_ns')",
    "nominal = analyze_text('wait_trigger 2,4\\nupd_param 0\\nwait 8\\nstop', file='seq.q1asm', sequencer_id='seq')",
    "play = PlaybackSession({'seq': ('seq.q1asm', source.replace('wait 8', 'upd_param 0\\nwait 8'))}, alignment_mode='manual:-4',",
    "    external_inputs={'triggers': [{'id': 'gate', 'address': 2, 'targets': ['seq'], 'times_ns': [50]}]})",
    "play.run()",
    "datasets = {'trigger': run(source), 'compare': compare, 'sync': sync, 'cutoff': cutoff, 'syncCutoff': sync_cutoff,",
    "    'clippedWait': clipped_wait, 'invalidCutoff': invalid_cutoff, 'clippedPlay': clipped_play,",
    "    'late': late, 'legacy': legacy,",
    "    'nominal': nominal, 'interrupted': interrupted, 'play': play.snapshot()}",
    "print(json.dumps({name: {'ir': ir, 'html': render_html(ir)} for name, ir in datasets.items()}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, maxBuffer: 4 * 1024 * 1024,
    env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const clipped = datasets.cutoff.ir.events.find((item) => item.kind === "wait_trigger");
  expect([clipped.t1.value, clipped.meta.observation_cutoff_ns, clipped.meta.planned_rt_end_ns]).toEqual([160, 160, 190]);
  const checks = {
    trigger: [["wait_trigger", "seq", undefined, "Arm 20 ns · Trigger arrival 50 ns · Complete 54 ns",
      "Arm 120 ns · Trigger arrival 150 ns · Complete 154 ns"]],
    compare: [
      ["wait_trigger", "seq", "taken", "Arm 20 ns · Trigger arrival 50 ns · Complete 50 ns · Earliest next dequeue 54 ns",
        "Arm 120 ns · Trigger arrival 150 ns · Complete 150 ns · Earliest next dequeue 154 ns"],
      ["conditional_skip", "seq", "fallthrough", "20 -> 20 · Earliest next dequeue 24 ns", "120 -> 120 · Earliest next dequeue 124 ns"],
    ],
    sync: [
      ["wait_sync", "seq", undefined, "Arm 20 ns · Barrier release 40 ns · Complete 44 ns",
        "Arm 120 ns · Barrier release 140 ns · Complete 144 ns"],
      ["wait_sync", "peer", undefined, "Arm 40 ns · Barrier release 40 ns · Complete 44 ns",
        "Arm 140 ns · Barrier release 140 ns · Complete 144 ns"],
    ],
    cutoff: [["wait_trigger", "seq", undefined, "Arm 20 ns · Trigger arrival 50 ns · Observation cutoff 60 ns · Planned end 90 ns",
      "Arm 120 ns · Trigger arrival 150 ns · Observation cutoff 160 ns · Planned end 190 ns"]],
    syncCutoff: [
      ["wait_sync", "seq", undefined, "Arm 20 ns · Barrier release 40 ns · Observation cutoff 60 ns · Planned end 80 ns",
        "Arm 120 ns · Barrier release 140 ns · Observation cutoff 160 ns · Planned end 180 ns"],
      ["wait_sync", "peer", undefined, "Arm 40 ns · Barrier release 40 ns · Complete 44 ns",
        "Arm 140 ns · Barrier release 140 ns · Complete 144 ns"],
    ],
    clippedWait: [["wait", "seq", undefined, "Start 20 ns · Observation cutoff 60 ns · Planned end 120 ns",
      "Start 120 ns · Observation cutoff 160 ns · Planned end 220 ns", 2]],
    invalidCutoff: [["wait", "seq", undefined, "20 -> 60", "120 -> 160", 2]],
    clippedPlay: [
      ["play", "seq", undefined, "Start 20 ns · Observation cutoff 60 ns · Planned end 120 ns",
        "Start 120 ns · Observation cutoff 160 ns · Planned end 220 ns"],
      ["wait", "peer", undefined, "0 -> 40", "100 -> 140"],
    ],
    late: [["upd_param", "seq", undefined, "0 -> 0 · Earliest next dequeue 4 ns", "100 -> 100 · Earliest next dequeue 104 ns"],
      ["wait", "seq", undefined, "20 -> 28", "120 -> 128"]],
    legacy: [["upd_param", "seq", undefined, "0 -> 0", "100 -> 100"]],
    nominal: [["upd_param", "seq", undefined, "Nominal RT 4 → 4 (trigger arrival unresolved) · Nominal earliest next dequeue 8 ns"]],
    interrupted: [["wait_trigger", "seq", undefined, "20 -> 56", "120 -> 156"]],
    play: [["wait_trigger", "seq", undefined, "16 -> 50", "20 -> 54"],
      ["upd_param", "seq", undefined, "50 -> 50 · Earliest next dequeue 54 ns", "54 -> 54 · Earliest next dequeue 58 ns"]],
  };
  const inspector = page.locator("#event-inspector-fields");
  const range = inspector.getByText("Time range", {exact: true}).locator("xpath=following-sibling::dd[1]");
  const timingTitle = (timing) => new RegExp(timing.replace(/[()]/g, "\\$&")
    .replace(/(\d+) (->|→) (\d+)/, "$1(?: ns)? $2 $3(?: ns)?") + "(?=\\n|$)");
  for (const [name, cases] of Object.entries(checks)) {
    const {ir, html} = datasets[name];
    const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    await mount(page, "idle", ir, {timelineWaitsCollapsed: false}, {viewMode: "normal"}, shell);
    const timeWindow = page.locator("#q1timeline-time-window");
    if (name.startsWith("clipped")) await expect(timeWindow).toHaveAttribute("title", /\.\.60 ns$/);
    if (name === "clippedWait" || name === "invalidCutoff") {
      await page.locator('.diagnostic[data-diagnostic-category="simulation_time_limit"]')
        .getByRole("link", {name: "Show event"}).click();
    }
    for (const [kind, sequencer, comparisonPath, aligned, local, sourceLine] of cases) {
      const event = ir.events.find((item) => item.kind === kind && item.sequencer_id === sequencer &&
        item.meta.branch_comparison_path === comparisonPath && (sourceLine === undefined || item.source.line === sourceLine));
      expect(event, `${name} ${kind} ${sequencer}`).toBeTruthy();
      const block = page.locator(`.timeline-block[data-event-id="${event.id}"]`);
      await expect(block).toHaveAttribute("title", timingTitle(aligned));
      if (kind === "play") {
        const ids = ir.events.filter((item) => item.kind === "play").map((item) => item.id);
        expect(ids).toHaveLength(2);
        await expect(block).toHaveAttribute("data-event-ids", ids.join(" "));
        await expect(block).toHaveAttribute("title", /^play p0 .+ \/ p1 .+ \| /);
      }
      await block.press("Enter");
      await expect(range).toHaveText(aligned);
      if (local) {
        await page.getByRole("button", {name: "Local time basis"}).click();
        await expect(block).toHaveAttribute("aria-selected", "true");
        await expect(range).toHaveText(local);
        await expect(block).toHaveAttribute("title", timingTitle(local));
        if (name.startsWith("clipped")) await expect(timeWindow).toHaveAttribute("title", /\.\.160 ns$/);
        await page.getByRole("button", {name: "Aligned time basis"}).click();
      }
    }
    expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
  }
});

test("clipped Q1 issues preserve read evidence and distinguish their cutoff and planned end", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const fixtures = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json",
    "from q1timeline.analysis.api import analyze_text",
    "from q1timeline.render.html import render_html",
    "prefix = 'move 3,R0\\nnop\\n'",
    "suffix = 'add R0,1,R1\\nstop'",
    "def run(source, cutoff, startup=100, alignment=None):",
    "    ir = analyze_text(source, file='seq.q1asm', sequencer_id='seq', execution_mode='simulation', alignment_mode=alignment,",
    "        simulation={'startup_delay_ns': startup, 'max_time_ns': cutoff})",
    "    return {'ir': ir, 'html': render_html(ir, default_mode='debug')}",
    "print(json.dumps({'clipped': run(prefix + suffix, 12), 'exact': run(prefix + suffix, 20),",
    "    'shared': run('wait 20\\n' + prefix + 'cmp R0,3\\n' + suffix, 32, 28, 'manual:-28'),",
    "    'fault': run(prefix + 'illegal', 24)}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const timing = (start, cutoff, planned) => `Issue ${start} ns · Observation cutoff ${cutoff} ns · Planned end ${planned} ns`;
  const inspector = page.locator("#event-inspector-fields");
  const time = inspector.getByText("Time range", {exact: true}).locator("xpath=following-sibling::dd[1]");
  const q1Detail = page.locator('[data-value="instruction-detail"]');
  for (const [name, {ir, html}] of Object.entries(fixtures)) {
    const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    const issue = ir.events.find((item) => item.kind === "q1_issue" && item.meta.op === (name === "fault" ? "illegal" : "add"));
    const incomplete = name === "clipped" || name === "shared";
    expect(Boolean(issue.meta.planned_q1_end_ns)).toBe(incomplete);
    if (incomplete) {
      expect(issue.meta.pending_register_ready_ns.R1).toBe(issue.meta.planned_q1_end_ns + 4);
      expect(ir.register_history.some((write) => write.register === "R1")).toBe(false);
    }
    await mount(page, "idle", ir, {timelineWaitsCollapsed: false, hardwareState: {level: "detail", sequencerId: "seq", time: 0}}, {}, shell);
    await expandQ1Sections(page);
    const block = page.locator(`.timeline-block[data-block-id="${issue.id}"]`);
    await block.press("Enter");
    if (incomplete) {
      const expected = name === "clipped" ? timing(8, 12, 20) : timing(-4, 4, 8);
      await expect(time).toHaveText(expected);
      await expect(block).toHaveAttribute("title", new RegExp(expected));
      await expect(q1Detail).toContainText("Read at issue: R0 = 3");
      await expect(q1Detail).toContainText(name === "clipped" ? "Flags before → after: unknown → unobserved"
        : "Flags before → after: ZF=1 NF=0 CF=0 OF=0 → unobserved");
      await expect(page.locator('[data-register="R0"] strong')).toHaveText("3");
      await expect(page.locator('[data-register="R1"] strong')).toHaveText("?");
      if (name === "shared") {
        await page.getByRole("button", {name: "Local time basis"}).click();
        await expect(block).toHaveAttribute("aria-selected", "true");
        await expect(time).toHaveText(timing(24, 32, 36));
        await expect(block).toHaveAttribute("title", new RegExp(timing(24, 32, 36)));
        await expect(q1Detail).toContainText("ZF=1 NF=0 CF=0 OF=0 → unobserved");
      }
    } else if (name === "exact") {
      await expect(time).toHaveText("8 -> 20");
      await expect(q1Detail).toContainText("Flags before → after: unknown → ZF=0 NF=0 CF=0 OF=0");
    } else {
      await expect(time).toHaveText("8 -> 12");
      await expect(inspector).toContainText("SEQUENCE_PROCESSOR_RT_EXEC_ILLEGAL_INSTRUCTION");
      await expect(q1Detail).not.toContainText("unobserved");
    }
    expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
  }
});

test("routed LINQ rejection reveals the exact sending RT command", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const fixtures = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json",
    "from q1timeline.analysis.api import analyze_documents",
    "from q1timeline.render.html import render_html",
    "base = {'startup_delay_ns': 100, 'feedback_queue_capacity': 1}",
    "route = {**base, 'routes': [{'source': 'tx', 'target': 'rx', 'id': 16, 'latency_ns': 20}]}",
    "rx = {'rx.q1asm': 'wait 100\\nstop'}",
    "def run(documents, scenario, branches=None):",
    "    ir = analyze_documents(documents, execution_mode='simulation', simulation=scenario, branch_assumptions=branches)",
    "    return {'ir': ir, 'html': render_html(ir)}",
    "first = 'fb_com_data 16,11,4\\n'",
    "second = 'fb_com_data 16,22,4\\n'",
    "routed = run({**rx, 'tx.q1asm': first + second + 'stop'}, route)",
    "compare = run({**rx, 'tx.q1asm': first + 'set_cond 1,1,0,4\\n' + second + 'set_cond 0,0,0,0\\nstop'},",
    "    route, {'tx:set_cond:1:1': 'both'})",
    "scenario = run(rx, {**base, 'inputs': {'feedback': [{'target': 'rx', 'time_ns': time, 'id': 16, 'value': time}",
    "    for time in (120, 124)]}})",
    "print(json.dumps({'routed': routed, 'compare': compare, 'scenario': scenario}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const rejection = (ir) => ir.events.find((item) => item.kind === "feedback_rejected");
  const sender = (ir) => {
    const send = ir.events.find((item) => item.id === rejection(ir).meta.source_event_id);
    return ir.events.find((item) => item.kind === "fb_com_data" && item.meta.rt_packet_id === send.meta.source_packet_id);
  };
  for (const name of ["missing", "ambiguous"]) {
    const fixture = structuredClone(fixtures.routed);
    if (name === "missing") rejection(fixture.ir).meta.source_event_id = "unretained:send";
    else fixture.ir.events.push({...sender(fixture.ir), id: "tx:duplicate-sender"});
    fixtures[name] = fixture;
  }

  const fields = page.locator("#event-inspector-fields");
  const showSender = fields.getByRole("button", {name: "Show sender", exact: true});
  const block = (id) => page.locator(`.timeline-block[data-block-id="${id}"]`);
  for (const [name, {ir, html}] of Object.entries(fixtures)) {
    const rejected = rejection(ir);
    expect(rejected.t0.value).toBe(124);
    const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    await mount(page, "idle", ir, {}, {viewMode: "normal"}, shell);
    await block(rejected.id).press("Enter");
    await expect(block(rejected.id)).toHaveAttribute("aria-selected", "true");
    if (name !== "routed" && name !== "compare") {
      await expect(showSender).toHaveCount(0);
      continue;
    }
    const command = sender(ir);
    const line = name === "compare" ? 3 : 2;
    expect(command.source.line).toBe(line);
    expect(command.meta.branch_comparison_path).toBe(name === "compare" ? "taken" : undefined);
    await expect(block(rejected.meta.source_event_id)).toHaveCount(0);
    await page.evaluate(() => {window.messages = [];});
    await showSender.click();
    await expect(block(command.id)).toHaveAttribute("aria-selected", "true");
    await expect(block(rejected.id)).toHaveAttribute("aria-selected", "false");
    await expect(fields).toContainText(`tx.q1asm:${line}`);
    expect(await page.evaluate(() => window.messages.filter((message) => message.type === "eventClick")))
      .toEqual([{type: "eventClick", eventId: command.id}]);
    if (name === "routed") {
      await page.locator('.diagnostic[data-diagnostic-category="feedback_queue_overflow"]')
        .getByRole("link", {name: "Show event"}).click();
      await expect(block(rejected.id)).toHaveAttribute("aria-selected", "true");
      await expect(showSender).toBeVisible();
    }
  }
});

test("comparison terminal actions reveal distinct RT cutoff and acquisition evidence", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const {ir, html} = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json",
    "from q1timeline.analysis.api import analyze_text",
    "from q1timeline.render.html import render_html",
    "program = ('set_cond 1,1,0,4\\nfb_tdc_tdelta_id 16,4\\nset_cond 0,0,0,4\\nacquire_timetags 0,0,1,0,8\\n'",
    "    'acquire_timetags 0,0,0,1025,4\\nwait 4\\nwait 100\\nstop')",
    "edge = 107 * 128 + 1",
    "trace = {'initial_level': 0, 'transitions': [{'time_ticks': edge, 'level': 1}, {'time_ticks': edge + 1, 'level': 0}]}",
    "ir = analyze_text(program, file='tags.q1asm', sequencer_id='tags', execution_mode='simulation',",
    "    branch_assumptions={'tags:set_cond:0:1': 'both'}, sequence={'acquisitions': {'result': {'index': 0, 'num_bins': 1}}},",
    "    simulation={'startup_delay_ns': 100, 'inputs': {'digital': {'tags': trace}}})",
    "print(json.dumps({'ir': ir, 'html': render_html(ir)}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const terminal = ir.events.find((event) => event.kind === "branch_terminal");
  const outcome = terminal.meta.comparison_terminal;
  const [result, cutoff] = outcome.source_event_ids.map((id) => ir.events.find((event) => event.id === id));
  const skip = ir.events.find((event) => event.kind === "wait" && event.source.line === cutoff.source.line &&
    event.meta.branch_comparison_path === "fallthrough");
  const shared = ir.events.find((event) => event.kind === "q1_issue" && event.meta.op === "set_cond" && event.source.line === 3);
  expect([result.kind, result.meta.timetag_ticks, cutoff.kind, cutoff.meta.observation_cutoff_ns])
    .toEqual(["acquisition_result", 385, "wait", 120 + 1 / 128]);
  outcome.source_event_ids = [skip.id, "missing-evidence", result.id, cutoff.id, cutoff.id, shared.id];

  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", ir, {}, {viewMode: "normal"}, shell);
  const fields = page.locator("#event-inspector-fields");
  const block = (event) => page.locator(`.timeline-block[data-block-id="${event.id}"]`);
  const action = (name) => fields.getByRole("button", {name, exact: true});
  const show = async (label, event) => {
    await page.evaluate(() => {window.messages = [];});
    await action(label).click();
    await expect(block(event)).toHaveAttribute("aria-selected", "true");
    expect(await page.evaluate(() => window.messages.filter((item) => item.type === "eventClick")))
      .toEqual([{type: "eventClick", eventId: event.id}]);
  };
  for (const mode of ["Normal view", "Debug view"]) {
    await page.getByRole("button", {name: mode, exact: true}).click();
    if (mode === "Normal view") {
      await expect(block(cutoff)).toBeHidden();
      const filter = page.locator("#event-filter");
      await filter.fill("branch_terminal");
      await block(terminal).press("Enter");
      await expect(action("Show RT cutoff")).toHaveCount(0);
      await filter.fill("");
    }
    await block(terminal).press("Enter");
    await expect(action("Show evidence")).toHaveCount(1);
    await expect(action("Show RT cutoff")).toHaveCount(1);
    await expect(action("Show Q1 issue")).toHaveCount(mode === "Debug view" ? 1 : 0);
    await expect(action("Show wait")).toHaveCount(0);
    await show("Show evidence", result);
    await block(terminal).press("Enter");
    await show("Show RT cutoff", cutoff);
    await expect(fields).toContainText("Observation cutoff 20.0078125 ns");
    await expect(page.locator(`.timeline-block[data-block-id="${skip.id}"][aria-selected="true"]`)).toHaveCount(0);
    if (mode === "Debug view") {
      await expect(block(shared)).toBeHidden();
      await block(terminal).press("Enter");
      await show("Show Q1 issue", shared);
    }
  }
});

test("NetZero result inspector separates observed compensation and its plan", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const fixtures = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json",
    "from q1timeline.analysis.api import analyze_text",
    "from q1timeline.render.html import render_html",
    "source = 'set_awg_offs 16384,0\\nupd_param 8\\nset_awg_offs 0,0\\nplay_netzero 100,0\\nstop'",
    "physical = {'sample_period_ns': 1, 'sequencers': {'seq': {'netzero': {'module_type': 'QCM', 'outputs': [",
    "    {'output': 0, 'path': 0, 'enabled': True, 'ihp_enabled': True, 'amplitude': .5, 'slew_rate_per_s': 2.5e8}]}}}}",
    "def run(program, cutoff, options=None, branches=None):",
    "    ir = analyze_text(program, file='seq.q1asm', sequencer_id='seq', execution_mode='simulation',",
    "        alignment_mode='manual:-100', branch_assumptions=branches, simulation={",
    "            'startup_delay_ns': 100, 'max_time_ns': cutoff, **(options if options is not None else {'physical': physical})})",
    "    return {'ir': ir, 'html': render_html(ir)}",
    "print(json.dumps({'limited': run(source, 112), 'compared': run(source.replace('play_netzero',",
    "    'set_cond 1,1,0,0\\nplay_netzero'), 112, branches={'seq:set_cond:3:1': 'both'}),",
    "    'equal': run(source.replace('stop', 'wait 100\\nstop'), 200, {'physical': physical, 'feedback_queue_capacity': 1,",
    "        'inputs': {'feedback': [{'target': 'seq', 'time_ns': 117, 'id': i, 'value': i} for i in (1, 2)]}}),",
    "    'completed': run(source, 200), 'unmodeled': run(source, 200, {})}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, maxBuffer: 4 * 1024 * 1024,
    env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const inspector = page.locator("#event-inspector-fields");
  const field = (name) => inspector.getByText(name, {exact: true}).locator("xpath=following-sibling::dd[1]");
  const block = (id) => page.locator(`.timeline-block[data-block-id="${id}"]`);
  const timing = (cutoff, planned) => `Observation cutoff ${cutoff} ns · Planned end ${planned} ns`;
  for (const [name, {ir, html}] of Object.entries(fixtures)) {
    const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    const result = ir.events.find((event) => event.kind === "netzero_result" &&
      (name !== "compared" || event.meta.branch_comparison_path === "taken"));
    await mount(page, "idle", ir, {}, {viewMode: "normal"}, shell);
    const viewport = page.locator("#q1timeline-time-window");
    if (name === "limited") {
      await expect(viewport).toHaveAttribute("title", /\.\.12 ns$/);
      await page.locator('.diagnostic[data-diagnostic-category="simulation_time_limit"]')
        .getByRole("link", {name: "Show event"}).click();
    } else if (name === "compared") {
      const terminal = ir.events.find((event) => event.kind === "branch_terminal" && event.meta.branch_comparison_path === "taken");
      await block(terminal.id).press("Enter");
      await inspector.getByRole("button", {name: "Show evidence", exact: true}).click();
    } else await block(result.id).press("Enter");
    await expect(block(result.id)).toHaveAttribute("aria-selected", "true");
    if (name === "completed" || name === "unmodeled") {
      await expect(field("Compensation timing")).toHaveCount(0);
      await expect(field("Planned compensation area")).toHaveCount(0);
      continue;
    }
    const cutoff = name === "equal" ? 17 : 12;
    await expect(field("Time range")).toHaveText(`8 -> ${cutoff}`);
    await expect(field("Compensation timing")).toHaveText(timing(cutoff, 17));
    await expect(field("Observed compensation area")).toHaveText(`${name === "equal" ? -4 : -1.75} normalized full scale × ns`);
    await expect(field("Planned compensation area")).toHaveText("-4 normalized full scale × ns");
    await page.getByRole("button", {name: "Local time basis"}).click();
    await expect(block(result.id)).toHaveAttribute("aria-selected", "true");
    await expect(field("Compensation timing")).toHaveText(timing(cutoff + 100, 117));
    if (name === "limited") await expect(viewport).toHaveAttribute("title", /\.\.112 ns$/);
  }
});

test("TB rejection diagnostic links reveal each retained contributor and local cutoff", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const fixtures = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json",
    "from q1timeline.analysis.alignment import align_timelines",
    "from q1timeline.analysis.interpreter import prepare_def_program",
    "from q1timeline.ir.serialize import timeline_ir_from_states",
    "from q1timeline.q1asm.parser import parse_q1asm",
    "from q1timeline.render.html import render_html",
    "from q1timeline.simulation.runtime import simulate_programs",
    "sender = 'fb_acq_tb_id 16,4\\nfb_acq_tb_cfg 1,0,1,4\\nacquire 0,0,8\\nwait 100\\nstop'",
    "def run(shift=0, prepared=False, partial=False):",
    "    sources = {'a': sender, 'b': sender.replace('1,0,1,4', f'1,{shift},1,4'), 'rx': 'wait 100\\nstop'}",
    "    programs = {name: parse_q1asm(source, file=f'{name}.q1asm') for name, source in sources.items()}",
    "    bindings = {name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())",
    "        for name, program in programs.items()} if prepared else None",
    "    states = simulate_programs(programs, prepared_programs=bindings, simulation={'startup_delay_ns': 100,",
    "        'routes': [{'source': name, 'target': 'rx', 'id': 16, 'latency_ns': 5} for name in ('a', 'b')],",
    "        'inputs': {'acquisitions': [{'sequencer': name, 'acquisition_index': 0, 'bin_index': 0,",
    "            'thresholded': 1} for name in ('a', 'b')]}})",
    "    align_timelines(states)",
    "    ir = timeline_ir_from_states(states, project={'alignment_mode': 'first_rt_dequeue'})",
    "    if partial:",
    "        ir['events'] = [e for e in ir['events'] if not (e['kind'] == 'feedback_contribution' and e['sequencer_id'] == 'b')]",
    "        retained = {event['id'] for event in ir['events']}",
    "        for diagnostic in ir['diagnostics']:",
    "            diagnostic['related_events'] = [id for id in diagnostic.get('related_events', ()) if id in retained]",
    "    return {'ir': ir, 'html': render_html(ir)}",
    "print(json.dumps({'collision': run(), 'partial': run(partial=True), 'prepared': run(prepared=True), 'success': run(2)}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, maxBuffer: 4 * 1024 * 1024,
    env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const row = (name) => page.locator('.diagnostic[data-diagnostic-category="feedback_write_combine"]').filter({hasText: `${name}.q1asm:`});
  const block = (id) => page.locator(`.timeline-block[data-block-id="${id}"]`);
  for (const [name, {ir, html}] of Object.entries(fixtures)) {
    const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
    await mount(page, "idle", ir, {}, {viewMode: "normal"}, shell);
    if (name !== "collision") {
      const links = page.locator("a[data-tb-evidence]:visible");
      if (name === "partial") {
        await expect(row("rx").getByRole("link", {name: "Show a contribution", exact: true})).toBeVisible();
        await expect(row("rx").getByRole("link", {name: "Show b contribution", exact: true})).toHaveCount(0);
        await expect(row("rx").getByRole("link", {name: "Show rx RT cutoff", exact: true})).toBeVisible();
      } else await expect(links).toHaveCount(0);
      continue;
    }
    const [a, b] = ir.events.filter((event) => event.kind === "feedback_contribution");
    const cutoff = (sequencer) => ir.events.find((event) => event.sequencer_id === sequencer &&
      event.kind === "wait" && event.meta.observation_cutoff_ns === 116);
    const reveal = async (owner, label, target) => {
      await page.evaluate(() => {window.messages = [];});
      await row(owner).getByRole("link", {name: label, exact: true}).click();
      await expect(block(target.id)).toHaveAttribute("aria-selected", "true");
      expect(await page.evaluate(() => window.messages.filter((item) => item.type === "eventClick")))
        .toEqual([{type: "eventClick", eventId: target.id}]);
    };
    await reveal("rx", "Show event", a);
    for (const mode of ["Normal view", "Debug view"]) {
      await page.getByRole("button", {name: mode, exact: true}).click();
      await reveal("rx", "Show a contribution", a);
      await reveal("rx", "Show b contribution", b);
      await reveal("rx", "Show rx RT cutoff", cutoff("rx"));
      await reveal("a", "Show a RT cutoff", cutoff("a"));
    }
    const filter = page.locator("#event-filter");
    await filter.fill("feedback_contribution");
    await expect(row("rx").getByRole("link", {name: "Show a contribution", exact: true})).toBeVisible();
    await expect(row("rx").getByRole("link", {name: "Show rx RT cutoff", exact: true})).toHaveCount(0);
    await expect(row("rx").getByRole("link", {name: "Show event", exact: true})).toBeVisible();
  }
});
