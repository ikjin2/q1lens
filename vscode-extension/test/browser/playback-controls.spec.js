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
  const form = page.getByRole("form", {name: "External trigger configuration"});
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
  const form = page.getByRole("form", {name: "External trigger configuration"});
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

test("external input drafts survive unsolicited host updates and initial errors remain visible", async ({page}) => {
  const trigger = {id: "shot_start", address: 1, targets: ["seq"], times_ns: [1000]};
  const base = {triggers: [trigger], overridden: false, canSave: true};
  await mount(page, "idle", undefined, {}, {externalInputs: {...base, error: "External input calculation failed"}});
  await page.getByRole("button", {name: "Configure external inputs"}).click();
  const form = page.getByRole("form", {name: "External trigger configuration"});
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
  const form = page.getByRole("form", {name: "External trigger configuration"});
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
  const program = "and R7,15,R1\nmove 10000,R2\nmove 3,R0\nbody: wait R1\nsub R2,R1,R3\nnop\nwait R3\nadd R1,4,R1\nnop\nloop R0,@body\nupd_param 4\nstop\n";
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
  expect(ir.events.find((item) => item.kind === "wait" && item.source.line === 4).duration.kind).not.toBe("concrete");
  const shell = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
    /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await mount(page, "idle", ir, {}, {}, shell);
  await page.evaluate((ids) => window.dispatchEvent(new MessageEvent("message", {data: {
    type: "highlightEventIds", highlightEventIds: ids, zoomToRange: false,
    sourceRange: {file: "symbolic-total.q1asm", startLine: 11, endLine: 11},
  }})), ir.events.filter((item) => item.source?.line === 11).map((item) => item.id));
  await expect(page.locator(`.timeline-block[data-event-id="${update.id}"]`)).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#event-inspector-fields")).toContainText("symbolic-total.q1asm:11");
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
  const feedback = (id, kind, time, sequencer, line) => ({...event(id, kind, time, time),
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

test("feedback markers and connector navigate in normal view with hidden instruction blocks", async ({page}) => {
  const ir = feedbackNavigationFixture();
  await mount(page, "completed", ir, {timelineWaitsCollapsed: false});
  await page.getByRole("button", {name: "Normal view", exact: true}).click();
  await expect(page.locator('.timeline-block[data-event-id="send"]')).toBeHidden();
  await expect(page.locator('.timeline-block[data-event-id="receive"]')).toBeHidden();
  const source = page.locator('.feedback-endpoint[data-flow-id="cross-flow"][data-feedback-role="source"]');
  const target = page.locator('.feedback-endpoint[data-flow-id="cross-flow"][data-feedback-role="target"]');
  const connector = page.locator('.feedback-connector[data-flow-id="cross-flow"]');
  const inline = page.locator('.feedback-inline-capsule[data-flow-id="inline-flow"]');
  const messages = () => page.evaluate(() => window.messages.filter((message) => ["eventClick", "feedbackFlowClick"].includes(message.type)));
  for (const [marker, role] of [[source, "source"], [target, "target"],
    [inline.locator('.feedback-dot-source'), "source"], [inline.locator('.feedback-dot-target'), "target"]]) {
    const flowId = marker === source || marker === target ? "cross-flow" : "inline-flow";
    await page.evaluate(() => {window.messages = [];});
    await marker.click();
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
