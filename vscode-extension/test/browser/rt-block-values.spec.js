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

test("RT blocks expand captured values and highlight only known changes", async ({page}) => {
  await mount(page);
  const q1 = page.locator('[data-lane-role="q1-issue"]');
  await expect(q1).toBeHidden();
  const block = page.locator('.timeline-block[data-event-id="play"]');
  const width = (await block.boundingBox()).width;
  await block.click();
  const panel = page.getByRole("dialog", {name: "RT block values", exact: true});
  await expect(panel).toBeVisible();
  await expect(block).toHaveAttribute("aria-expanded", "true");
  await expect(q1).toBeHidden();
  const registers = panel.getByRole("table", {name: "Registers used", exact: true});
  await expect(registers.locator("tr")).toHaveCount(2);
  await expect(registers.locator('[data-value-name="R0"]')).toHaveClass("is-changed");
  await expect(registers.locator('[data-value-name="R1"]')).not.toHaveClass("is-changed");
  const parameters = panel.getByRole("table", {name: "Latched parameters", exact: true});
  await expect(parameters.locator('[data-value-name="frequency"]')).toHaveText("frequency20");
  await expect(parameters.locator('[data-value-name="frequency"]')).toHaveClass("is-changed");
  await expect(parameters.locator('[data-value-name="awg_gain"]')).not.toHaveClass("is-changed");
  await panel.locator("summary", {hasText: "Registers used"}).click();
  await expect(registers).toBeHidden();
  expect((await block.boundingBox()).width).toBe(width);
  await panel.press("Escape");
  await expect(panel).toHaveCount(0);
  await expect(block).toBeFocused();
  await block.press("Enter");
  await expect(panel).toBeVisible();
  await page.screenshot({path: path.resolve(__dirname, "../../../output/playwright/rt-block-values.png")});
});

test("Q1 is visible in debug and timing warnings stay visible in normal instruction view", async ({page}) => {
  await mount(page, fixture(), "debug");
  await expect(page.locator('[data-lane-role="q1-issue"]')).toBeVisible();
  await mount(page);
  await page.getByRole("button", {name: "Debug view", exact: true}).click();
  await expect(page.locator('[data-lane-role="q1-issue"]')).toBeVisible();
  await page.getByRole("button", {name: "Normal view", exact: true}).click();
  await expect(page.locator('[data-lane-role="q1-issue"]')).toBeHidden();
  const warning = page.getByRole("button", {name: "Underflow · slack -4 ns", exact: true});
  await expect(warning).toBeVisible();
  await page.getByRole("button", {name: "Instruction", exact: true}).click();
  await expect(warning).toBeVisible();
  await expect(page.locator('[data-lane-role="q1-issue"]')).toBeHidden();
  const pulse = await page.locator('.timeline-block[data-event-id="play"]').boundingBox();
  const flag = await warning.boundingBox();
  expect(Math.abs(pulse.x - flag.x)).toBeLessThan(1);
  expect(flag.width).toBe(16);
});

test("hidden Q1 branches and feedback retain their assumption choices", async ({page}) => {
  const ir = {sequencers: [{id: "seq"}], playback: {status: "completed"}, events: [
    event("wait", "wait", 0, 100),
    event("branch", "q1_issue", 8, 12, {op: "jl", branch_id: "source:branch", playback_branch_id: "branch:2",
      branch_decision: "default", branch_taken: false, assumed_branch_path: "fallthrough", target_label: "done"}),
    event("feedback", "q1_issue", 12, 16, {op: "fb_pop_data", playback_feedback_id: "feedback:3",
      feedback_assumption: {value: 5, delay_ns: 0}}),
  ]};
  await mount(page, ir);
  const menu = page.getByRole("button", {name: "Execution assumptions for seq", exact: true});
  await menu.click();
  await page.getByRole("dialog", {name: "Execution assumptions", exact: true}).getByRole("button").first().click();
  await expect(page.getByRole("dialog", {name: "Branch paths", exact: true})).toBeVisible();
  await page.getByRole("button", {name: "Jump · @done", exact: true}).click();
  expect(await page.evaluate(() => window.messages.some((message) => message.type === "setExecutionBranch" &&
    message.branchId === "branch:2" && message.path === "taken" && message.eventId === "branch"))).toBe(true);
  await menu.click();
  await page.getByRole("dialog", {name: "Execution assumptions", exact: true}).getByRole("button").last().click();
  await expect(page.getByRole("dialog", {name: "Feedback assumption", exact: true})).toBeVisible();
});

test("loop iteration expansion preserves each occurrence's RT values", async ({page}) => {
  const ir = fixture();
  ir.playback = {status: "completed"};
  ir.events.push(event("loop", "loop_block", 4, 108, {playback: true, loop_id: "loop", loop_preview_key: "loop",
    count: 2, completed_iterations: 2, first_iteration_end: 104, first_iteration_event_ids: ["wait", "wait-issue"],
    hidden_event_ids: ["play", "play-issue"]}));
  await mount(page, ir);
  await expect(page.locator('.timeline-block[data-event-id="play"]')).toHaveCount(0);
  await page.locator('.timeline-loop-range[data-event-id="loop"]').click();
  await page.getByRole("button", {name: "Expand executed iterations", exact: true}).click();
  await page.locator('.timeline-block[data-event-id="play"]').click();
  const panel = page.getByRole("dialog", {name: "RT block values", exact: true});
  await expect(panel.locator('[data-value-name="R0"]')).toHaveClass("is-changed");
  await expect(page.locator('[data-lane-role="q1-issue"]')).toBeHidden();
});

test("normal view retains static branch comparison choices and independent RT rows", async ({page}) => {
  const branch = event("branch", "q1_issue", 4, 8, {op: "jlt", branch_id: "branch", branch_decision: "runtime_dependent"});
  const ir = {sequencers: [{id: "seq"}], events: [event("wait", "wait", 0, 100), branch],
    control_flow_graph: {sequencers: [{sequencer_id: "seq", nodes: [
      {id: "from", end_pc: 1}, {id: "target", labels: ["done"], source: {...branch.source, line: 8}},
      {id: "next", source: {...branch.source, line: 2}},
    ], edges: [
      {kind: "branch_taken", source: branch.source, target_label: "done", target_pc: 7, from_node_id: "from", to_node_id: "target"},
      {kind: "branch_fallthrough", source: branch.source, from_node_id: "from", to_node_id: "next"},
    ]}]}};
  await mount(page, ir);
  await page.getByRole("button", {name: "Execution assumptions for seq", exact: true}).click();
  await page.getByRole("dialog", {name: "Execution assumptions", exact: true}).getByRole("button").click();
  await page.getByRole("button", {name: "Compare both", exact: true}).click();
  expect(await page.evaluate(() => window.messages.some((message) => message.type === "setBranchAssumption" &&
    message.branchId === "branch" && message.path === "both"))).toBe(true);
  for (const branchPath of ["taken", "fallthrough"]) {
    ir.events.push(event(`play-${branchPath}`, "play", 100, 104,
      {branch_comparison_branch_id: "branch", branch_comparison_path: branchPath}, {lane: `branch_compare.${branchPath}.rt.play`}));
  }
  await mount(page, ir);
  await expect(page.locator('.timeline-row-branch-runtime .lane-label')).toHaveText(["Jump · RT", "Continue · RT"]);
});
