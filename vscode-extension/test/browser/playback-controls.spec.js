const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect } = require("./helpers");

const assets = path.resolve(__dirname, "../../src");
const source = { file: "playback.q1asm", line: 2, raw: "jl @done" };
const event = (id, kind, start, end, meta = {}) => ({ id, kind, label: meta.op || kind, sequencer_id: "seq",
  lane: kind === "q1_issue" ? "q1.issue" : "rt.control", t0: {kind: "concrete", value: start}, t1: {kind: "concrete", value: end}, confidence: "exact", source, meta });

async function mount(page, status = "completed", actualIr, initialState = {}, hostState = {}, actualHtml) {
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
  await expect(repeat.getByRole("button")).toHaveText("//");
  await expect(page.locator('.loop-range-label')).toHaveText(/x3/);
  await expect(page.locator('.loop-range-label')).toHaveCSS("opacity", "1");
  await expect(repeat).toHaveAttribute("data-time-start", String(firstWait.t1.value));
  const geometry = await loopGeometry(page, loop.id, firstWait.id, following.id);
  expect(geometry.firstWidth).toBeGreaterThan(30);
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
    expect(coreRange.width).toBeGreaterThan(rtRange.width * 4);
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
  const waitToggle = waitFold.getByRole("button");
  await expect(waitFold).toHaveCSS("background-image", "none");
  await expect(waitFold).toHaveCSS("border-left-width", "0px");
  await expect(waitToggle).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  await expect(waitToggle).toHaveCSS("border-top-width", "0px");
  await expect(waitToggle.locator('.q1timeline-fold-duration')).toBeHidden();
  expect(await waitToggle.innerText()).toBe("//");
  await waitToggle.hover();
  await expect(waitToggle).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  await expect(waitToggle).toHaveCSS("border-top-width", "0px");
  await expect(repeatFold.getByRole("button")).toHaveText("//");
  await expect(page.locator('.ruler .q1timeline-playback-fold button')).toHaveText("//");
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
  expect(icon.width).toBeLessThanOrEqual(28);
  expect(icon.width).toBeLessThan(folded.waitGap.width / 4);
  expect(icon.x + icon.width / 2).toBeCloseTo(folded.waitGap.x + folded.waitGap.width / 2, 0);
  const rulerIcon = await page.locator('.ruler .q1timeline-wait-fold:not(.is-expanded) .q1timeline-fold-toggle').boundingBox();
  expect(rulerIcon.width).toBeLessThanOrEqual(28);
  expect(rulerIcon.x + rulerIcon.width / 2).toBeCloseTo(icon.x + icon.width / 2, 0);
  const besideIcon = {x: folded.waitGap.x + folded.waitGap.width / 5, y: icon.y + icon.height / 2};
  expect(await page.evaluate(({x, y}) => document.elementFromPoint(x, y)?.closest('.timeline-block')?.dataset.eventId, besideIcon)).toBe(waits[0].id);
  await page.mouse.click(besideIcon.x, besideIcon.y);
  await expect(first).toHaveAttribute("aria-selected", "true");
  await expect(waitFold).toHaveCount(1);
  await page.mouse.click(besideIcon.x, rulerIcon.y + rulerIcon.height / 2);
  await expect(waitFold).toHaveCount(1);
  expect(await page.evaluate(() => window.getSavedTimelineState().timelineExpandedGaps)).toEqual([]);
  expect(folded.waitGap.x - folded.block.x).toBeGreaterThan(10);
  expect(folded.block.x + folded.block.width - folded.waitGap.x - folded.waitGap.width).toBeGreaterThan(10);
  expect(folded.repeated.x).toBeCloseTo(folded.block.x + folded.block.width, 0);
  expect(folded.after.x).toBeCloseTo(folded.repeated.x + folded.repeated.width, 0);
  const loopBounds = await row.locator('.timeline-loop-range').boundingBox();
  expect(folded.after.x).toBeCloseTo(loopBounds.x + loopBounds.width, 0);
  await expect(repeatFold).toHaveAttribute("data-time-start", String(waits[0].t1.value));
  await expect(repeatFold).toHaveAttribute("data-time-end", String(loop.t1.value));
  for (const wait of waits.slice(1)) await expect(row.locator(`.timeline-block[data-event-id="${wait.id}"]`)).toHaveCount(0);
  await page.screenshot({path: test.info().outputPath("wait-end-and-repeats.png"), fullPage: true});
  await waitToggle.click();
  await expect(row.locator('.q1timeline-wait-fold.is-expanded')).toHaveCount(1);
  await row.locator('.q1timeline-wait-fold.is-expanded .q1timeline-fold-toggle').click();
  expect(await geometry()).toEqual(folded);
  const toggle = page.locator('#q1timeline-wait-fold-button');
  await toggle.click();
  expect((await first.boundingBox()).width).toBeGreaterThan(folded.block.width);
  await toggle.click();
  expect(await geometry()).toEqual(folded);
  await repeatFold.getByRole("button").click();
  for (const wait of waits) await expect(row.locator(`.timeline-block[data-event-id="${wait.id}"]`)).toHaveCount(1);
  await expect(repeatFold).toHaveCount(0);
  expect(await page.evaluate(() => JSON.parse(document.getElementById("timeline-ir").textContent))).toEqual(ir);
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
  expect(collapsed.loopWidth / collapsed.firstWidth).toBeLessThan(1.002);
  expect(collapsed.afterLeft).toBeCloseTo(collapsed.loopLeft + collapsed.loopWidth, 2);
  expect(collapsed.ticks.some((tick) => tick.time > loop.meta.first_iteration_end.value && tick.time < loop.t1.value)).toBe(false);
  for (const tick of collapsed.ticks) {
    const displayedTime = tick.time >= loop.t1.value ? tick.time - (loop.t1.value - loop.meta.first_iteration_end.value) + 64 : tick.time;
    expect(tick.left).toBeCloseTo(collapsed.loopLeft + (displayedTime - loop.t0.value) / firstWait.duration.value * collapsed.firstWidth, 2);
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
  const expanded = await loopGeometry(page, loop.id, firstWait.id, followingWait.id);
  expect(expanded.loopWidth / expanded.firstWidth).toBeCloseTo(15, 2);
  expect(expanded.afterLeft).toBeCloseTo(expanded.loopLeft + expanded.loopWidth, 2);
  expect(expanded.afterLeft).toBeGreaterThan(collapsed.afterLeft);
  expect(expanded.ticks.some((tick) => tick.time > loop.meta.first_iteration_end.value && tick.time < loop.t1.value)).toBe(true);
  for (const tick of expanded.ticks) {
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
  const position = await page.evaluate(({targetTime, loopId}) => {
    const ir = JSON.parse(document.getElementById("timeline-ir").textContent);
    const scale = window.q1timelineTimelineAdapter.buildTimelineTimeScale(ir);
    const ticks = [...document.querySelectorAll(".ruler .tick")].map((tick) => parseFloat(tick.textContent));
    const min = Math.min(...ticks), max = Math.max(...ticks);
    const node = document.querySelector(`.timeline-loop-range[data-event-id="${loopId}"]`).closest(".lane-track");
    const bounds = node.getBoundingClientRect();
    return {x: bounds.left + (scale.toDisplay(targetTime) - scale.toDisplay(min)) / (scale.toDisplay(max) - scale.toDisplay(min)) * bounds.width,
      y: bounds.bottom - 2};
  }, {targetTime, loopId: loop.id});
  await page.mouse.click(position.x, position.y);
  expect(Number(await page.getByRole("spinbutton", {name: "Selected time in nanoseconds"}).inputValue())).toBeCloseTo(targetTime, 0);
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
  expect(onlyLoop.loopWidth / onlyLoop.firstWidth).toBeGreaterThan(1);
  expect(onlyLoop.loopWidth / onlyLoop.firstWidth).toBeLessThan(1.3);
  expect(onlyLoop.loopWidth).not.toEqual(foldedBoth.loopWidth);
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
    const min = ticks[0].time, max = ticks.at(-1).time;
    return {gaps: waits.gaps.length, duration: scale.toDisplay(end) - scale.toDisplay(start),
      ticks: ticks.map((tick) => ({actual: tick.left, expected: (scale.toDisplay(tick.time) - scale.toDisplay(min)) / (scale.toDisplay(max) - scale.toDisplay(min)) * 100})),
      roundTrip: scale.fromDisplay(scale.toDisplay(start + 1000))};
  }, {start: firstWait.t0.value, end: firstWait.t1.value});
  expect(compression.gaps).toBeGreaterThan(0);
  expect(compression.duration).toBeLessThan(65535 / 10);
  expect(compression.roundTrip).toBeCloseTo(firstWait.t0.value + 1000, 6);
  for (const tick of compression.ticks) expect(tick.actual).toBeCloseTo(tick.expected, 2);
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
  await expect(time).toHaveValue("5004");
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
  await expect(time).toHaveValue("5004");
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
    await expect(page.getByRole("spinbutton", {name: "Selected time in nanoseconds"})).toHaveValue(String(issue.t0.value));
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
    .toEqual([{type: "eventClick", eventId: "crossed-block"}]);
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
      .toEqual([{type: "eventClick", eventId}]);
    const flowId = await marker.getAttribute("data-flow-id");
    const edge = await marker.boundingBox();
    await page.evaluate(() => {window.messages = [];});
    await page.mouse.click(edge.x + edge.width / 2, edge.y + edge.height - 2);
    expect(await page.evaluate(() => window.messages.filter((message) => ["eventClick", "feedbackFlowClick"].includes(message.type))))
      .toEqual([{type: "feedbackFlowClick", flowId, ...(role ? {role} : {})}]);
  }
});
