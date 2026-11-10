const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect, analysisDetails } = require("./helpers");

function fixtures() {
  const repo = path.resolve(__dirname, "../../..");
  return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from contextlib import redirect_stdout", "from io import StringIO",
    "from q1timeline.analysis.api import analyze_documents, analyze_text", "from q1timeline.cli import main",
    "from q1timeline.render.html import render_html",
    "def trigger_case(path):",
    "    output = StringIO()",
    "    with redirect_stdout(output):",
    "        main(['analyze', '--project', 'examples/set-cond-review/02-trigger-execution/q1timeline.yml',",
    "            '--branch-assumption', f'trigger_demo:set_cond:4:1={path}', '--format', 'vscode-json',",
    "            '--include-timeline-ir', '--no-render'])",
    "    return json.loads(output.getvalue())['timeline_ir']",
    "trigger = trigger_case('both')",
    "recorded = trigger_case('taken')",
    "frequency = next(event for event in trigger['events'] if event['kind'] == 'q1_issue' and event['meta']['op'] == 'set_freq')",
    "commit = next(event for event in trigger['events'] if event['kind'] == 'upd_param')",
    "condition = next(event for event in trigger['events'] if event['meta'].get('branch_id') == 'trigger_demo:set_cond:4:1')",
    "recorded_condition = next(event for event in recorded['events'] if event['meta'].get('branch_id') == 'trigger_demo:set_cond:4:1')",
    "status = analyze_text('cmp R0,R0\\njz @end\\nwait 12\\nend: upd_param 4\\nstop', file='status.q1asm', sequencer_id='s')",
    "status_branch = next(event for event in status['events'] if event['kind'] == 'branch_region')",
    "status_issue = next(event for event in status['events'] if event['kind'] == 'q1_issue' and event['meta']['op'] == 'jz')",
    "timing = analyze_text('wait 4\\n' + 'nop\\n' * 10 + 'stop', file='timing.q1asm', sequencer_id='s',",
    "    execution_mode='simulation', simulation={'startup_delay_ns': 8})",
    "multi = analyze_documents({'fault.q1asm': 'move 1,R0\\nnop\\nillegal', 'peer.q1asm': 'wait 24\\nstop'},",
    "    execution_mode='simulation', simulation={'startup_delay_ns': 100})",
    "issue = next(event for event in multi['events'] if event['kind'] == 'q1_issue' and event['meta']['op'] == 'illegal')",
    "# Exercise an issue-only diagnostic link instead of the default primary RT fault link.",
    "diagnostic = next(item for item in multi['diagnostics'] if item['category'] == 'illegal_instruction')",
    "diagnostic['related_events'] = [issue['id']]",
    "legacy = analyze_text('set_freq 4000000\\nwait 20\\nstop', sequencer_id='seq')",
    "long_peer = analyze_documents({'s.q1asm': 'set_awg_offs 16384,0\\nupd_param 8\\nstop', 'peer.q1asm': 'wait 60000\\nstop'},",
    "    execution_mode='simulation', simulation={'startup_delay_ns': 100})",
    "edge = next(event for event in long_peer['events'] if event['kind'] == 'upd_param')",
    "unapplied = analyze_text('wait 8\\nset_freq 4000000\\nstop', file='pending.q1asm', sequencer_id='s',",
    "    execution_mode='simulation', simulation={'startup_delay_ns': 100}, hardware_readiness=True)",
    "pending = next(event for event in unapplied['events'] if event['kind'] == 'latched_state_pending')",
    "paths = analyze_text('set_cond 1,1,0,4\\nwait 100\\nset_cond 0,0,0,4\\nwait 8\\nwait 8\\nwait 8\\nset_freq 4000000\\nupd_param 8\\nstop',",
    "    sequencer_id='s', execution_mode='simulation', simulation={'startup_delay_ns': 100, 'queue_capacity': 2},",
    "    branch_assumptions={'s:set_cond:0:1': 'both'})",
    "print(json.dumps(dict(trigger=render_html(trigger), marker=next(event['id'] for event in trigger['events']",
    "    if event['kind'] == 'trigger_receive' and not event['meta'].get('branch_comparison_path')),",
    "    trigger_ids=dict(frequency=frequency['id'], commit=commit['id'], condition=condition['id']),",
    "    multi=render_html(multi, default_mode='debug'), issue=issue['id'], legacy=render_html(legacy),",
    "    long_peer=render_html(long_peer), edge=edge['id'], unapplied=render_html(unapplied), pending=pending['id'], paths=render_html(paths),",
    "    recorded=render_html(recorded, default_mode='debug'), recorded_condition=recorded_condition['id'],",
    "    status=render_html(status, default_mode='debug'), status_branch=status_branch['id'],",
    "    status_issue=status_issue['id'], timing=render_html(timing))))",
  ].join("\n")], { cwd: repo, encoding: "utf8", timeout: 10000, maxBuffer: 4 * 1024 * 1024,
    env: { ...process.env, PYTHONPATH: path.join(repo, "src") } }));
}

async function open(page, html) {
  await page.goto("about:blank");
  await page.setContent(html);
  await analysisDetails(page);
}

function visibleRules(svg) {
  return svg.locator("g.lane").evaluateAll((lanes) => lanes
    .filter((lane) => getComputedStyle(lane).display !== "none")
    .map((lane) => Number(lane.querySelector(".lane-rule").getAttribute("y1"))));
}

function inspectorField(page, label) {
  return page.locator("#event-inspector-fields").getByText(label, {exact: true}).locator("xpath=following-sibling::dd[1]");
}

async function activateWithKeys(page, target) {
  await expect(target).toHaveAttribute("role", "button");
  for (let step = 0; step < 64; step++) {
    await page.keyboard.press("Tab");
    if (await target.evaluate((node) => node === document.activeElement)) break;
  }
  await expect(target).toBeFocused();
  const id = await target.getAttribute("data-event-id");
  await page.evaluate(() => {
    window.keySelection = [];
    window.addEventListener("q1timeline:eventClick", (event) => window.keySelection.push(event.detail.eventId));
  });
  for (const key of ["Enter", "Space"]) {
    await page.keyboard.press(key);
    await expect(target).toHaveClass(/is-selected/);
  }
  expect(await page.evaluate(() => window.keySelection)).toEqual([id, id]);
}

test("standalone Normal compacts the real trigger comparison rows on both mode switches", async ({page}) => {
  const {trigger, marker, trigger_ids: ids} = fixtures();
  await open(page, trigger);
  const svg = page.locator("#timeline-root > svg");
  const arrival = svg.locator(`[data-event-id="${marker}"]`);
  const assertNormal = async () => {
    await expect.poll(() => visibleRules(svg)).toEqual([82, 116, 150]);
    await expect(svg).toHaveAttribute("viewBox", "0 0 1100 214");
    await expect(svg).toHaveAttribute("data-time-min", "0");
    await expect(svg.locator(".tick-label").first()).toHaveText("0 ns");
    await expect(svg.locator(".lane-label")).toHaveText(["Execute · RT", "Skip · RT"]);
    await expect(svg.locator(".q1-issue-lane")).toHaveCount(0);
    await expect(svg.locator('[data-kind="latched_state_pending"]')).toHaveCount(0);
    await expect(arrival.locator("title")).toHaveText("Trigger address 1 · 30 ns");
    const [max, left, right] = await svg.evaluate((node) => ["timeMax", "plotLeft", "plotRight"]
      .map((key) => Number(node.dataset[key])));
    await expect(arrival.locator("circle")).toHaveAttribute("cx", (left + 30 * (right - left) / max).toFixed(2));
  };
  await assertNormal();
  await page.getByRole("button", {name: "Debug", exact: true}).click();
  await expect(svg).toHaveAttribute("data-time-min", /^-/);
  await expect(svg.locator(".q1-issue-lane")).toBeVisible();
  await expect(arrival.locator("title")).toHaveText("Trigger address 1 · 30 ns");
  await svg.locator(`[data-event-id="${ids.frequency}"] rect`).click();
  await expect(svg.locator(`[data-event-id="${ids.commit}"]`)).toHaveClass(/is-related/);
  await svg.locator(`[data-event-id="${ids.condition}"] rect`).click();
  await expect(inspectorField(page, "Branch condition")).toHaveText(/set_cond\s+1,1,0,40/);
  await expect(inspectorField(page, "Recorded branch path")).toHaveText("Execute and Skip");
  await expect(page.getByRole("button", {name: /^(Execute|Skip|Clear condition override)$/})).toHaveCount(0);
  await page.getByRole("button", {name: "Normal", exact: true}).click();
  await assertNormal();
});

test("standalone inspection reports the recorded RT condition and Q1 status paths", async ({page}) => {
  const {recorded, recorded_condition: condition, status, status_branch: branch} = fixtures();
  const svg = page.locator("#timeline-root > svg");
  await open(page, recorded);
  await svg.locator(`[data-event-id="${condition}"] rect`).click();
  await expect(inspectorField(page, "Branch condition")).toHaveText(/set_cond\s+1,1,0,40/);
  await expect(inspectorField(page, "Recorded branch path")).toHaveText("Execute");
  await expect(inspectorField(page, "Source")).toHaveText("trigger_execution.q1asm:7");

  await open(page, status);
  await svg.locator(`.branch-marker[data-event-id="${branch}"] .branch-marker-hitbox`).click();
  await expect(inspectorField(page, "Branch condition")).toHaveText("jz status flags");
  await expect(inspectorField(page, "Recorded branch path")).toHaveText("Jump");
  await expect(inspectorField(page, "Source")).toHaveText("status.q1asm:2");
  await expect(page.getByRole("button", {name: /^(Condition true: jump target|Clear branch override)$/})).toHaveCount(0);
});

test("standalone Debug opens Q1 issues and status branches from Tab, Enter and Space", async ({page}) => {
  const {status, status_issue: issue, status_branch: branch} = fixtures();
  const svg = page.locator("#timeline-root > svg");
  for (const [target, hitbox] of [[svg.locator(`.kind-q1_issue[data-event-id="${issue}"]`), "rect"],
    [svg.locator(`.branch-marker[data-event-id="${branch}"]`), ".branch-marker-hitbox"]]) {
    await open(page, status);
    await activateWithKeys(page, target);
    await expect(inspectorField(page, "Branch condition")).toHaveText("jz status flags");
    await expect(inspectorField(page, "Recorded branch path")).toHaveText("Jump");
    await expect(inspectorField(page, "Source")).toHaveText("status.q1asm:2");
    await target.locator(hitbox).click();
    await expect(target).toHaveClass(/is-selected/);
    const id = await target.getAttribute("data-event-id");
    expect(await page.evaluate(() => window.keySelection)).toEqual([id, id, id]);
  }
});

test("standalone trigger arrivals and timing diagnostics retain keyboard activation", async ({page}) => {
  const {trigger, marker, timing} = fixtures();
  const svg = page.locator("#timeline-root > svg");
  await open(page, trigger);
  await activateWithKeys(page, svg.locator(`[data-event-id="${marker}"]`));
  await expect(inspectorField(page, "Trigger address")).toHaveText("1");
  await open(page, timing);
  await activateWithKeys(page, svg.locator(".timing-diagnostic"));
  await expect(inspectorField(page, "Error")).toHaveText("The RT queue ran out of commands.");
});

test("standalone compact Normal preserves cross-mode Q1 diagnostic navigation", async ({page}) => {
  const {multi, issue} = fixtures();
  await open(page, multi);
  const svg = page.locator("#timeline-root > svg");
  const source = page.locator("#event-inspector-fields").getByText("Source", {exact: true})
    .locator("xpath=following-sibling::dd[1]");
  await page.getByRole("button", {name: "Normal", exact: true}).click();
  await expect.poll(() => visibleRules(svg)).toEqual([82, 116]);
  await expect(svg.locator(".q1-issue-lane")).toHaveCount(0);
  await expect(svg.locator('text.sequencer-label[data-sequencer-label="sequencer:fault"]')).toBeVisible();
  await expect(svg.locator('g.lane[data-sequencer-id="peer"]')).toBeVisible();
  await page.getByRole("link", {name: "Show event", exact: true}).click();
  await expect(svg.locator(`.q1-error-marker[data-event-id="${issue}"]`)).toHaveClass(/is-selected/);
  await expect(source).toHaveText("fault.q1asm:3");
  await page.getByRole("button", {name: "Debug", exact: true}).click();
  await expect(svg.locator(`g.kind-q1_issue[data-event-id="${issue}"]`)).toHaveClass(/is-selected/);
  await expect(source).toHaveText("fault.q1asm:3");
});

test("standalone older single-SVG mode retains its original geometry", async ({page}) => {
  const {legacy} = fixtures();
  await open(page, legacy);
  const svg = page.locator("#timeline-root > svg");
  const q1 = svg.locator(".q1-issue-lane");
  const geometry = await svg.getAttribute("viewBox");
  await expect(page.locator("template[data-timeline-mode]")).toHaveCount(0);
  await expect(q1).toHaveCount(1);
  await expect(q1).toBeHidden();
  await expect(svg.locator('[data-kind="latched_state_pending"]')).toHaveCount(1);
  await page.getByRole("button", {name: "Debug", exact: true}).click();
  await expect(q1).toBeVisible();
  await expect(svg).toHaveAttribute("viewBox", geometry);
});

test("standalone separate Normal leaves no pending setter hit target at the RT origin", async ({page}) => {
  const {long_peer: html, edge, paths} = fixtures();
  await open(page, html);
  const svg = page.locator("#timeline-root > svg");
  await expect(svg).toHaveAttribute("data-time-min", "0");
  await expect(svg).toHaveAttribute("data-time-max", "60000");
  await expect(svg.locator('[data-kind="latched_state_pending"]')).toHaveCount(0);
  const hit = await svg.locator(`[data-event-id="${edge}"] rect`).evaluate((rect) => {
    const bounds = rect.getBoundingClientRect();
    return document.elementFromPoint(bounds.left + 0.04, bounds.top + bounds.height / 2)
      ?.closest("[data-event-id]")?.dataset.eventId;
  });
  expect(hit).toBe(edge);

  await open(page, paths);
  await expect(svg.locator('[data-kind="latched_state_pending"]')).toHaveCount(0);
  await expect(svg.locator(".lane-label")).toHaveText(["Execute · RT", "Skip · RT"]);
  await page.getByRole("button", {name: "Debug", exact: true}).click();
  await expect(svg.locator('[data-kind="latched_state_pending"][data-branch-comparison-path="taken"]')).toBeVisible();
  await expect(svg.locator('[data-kind="latched_state_pending"][data-branch-comparison-path="fallthrough"]')).toBeVisible();
});

test("standalone unapplied setter diagnostics open embedded evidence in Normal", async ({page}) => {
  const {unapplied, pending} = fixtures();
  await open(page, unapplied);
  const evidence = page.locator(`#timeline-root > svg [data-event-id="${pending}"]`);
  const source = page.locator("#event-inspector-fields").getByText("Source", {exact: true})
    .locator("xpath=following-sibling::dd[1]");
  await expect(evidence).toHaveCount(0);
  await page.locator('[data-diagnostic-category="pending_hardware_state"]')
    .getByRole("link", {name: "Show event", exact: true}).click();
  await expect(source).toHaveText("pending.q1asm:2");
  await page.getByRole("button", {name: "Debug", exact: true}).click();
  await expect(evidence).toHaveClass(/is-selected/);
  await page.getByRole("button", {name: "Normal", exact: true}).click();
  await expect(evidence).toHaveCount(0);
  await expect(source).toHaveText("pending.q1asm:2");
});
