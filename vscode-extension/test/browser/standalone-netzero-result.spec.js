const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect, analysisDetails } = require("./helpers");

function fixtures() {
  const repo = path.resolve(__dirname, "../../..");
  return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from copy import deepcopy", "from q1timeline.analysis.api import analyze_text", "from q1timeline.render.html import render_html",
    "prefix = 'set_awg_offs 16384,0\\nupd_param 8\\nset_awg_offs 0,0\\n'",
    "pulse = 'play_netzero 100,0\\n'",
    "physical = {'sample_period_ns': 1, 'max_samples': 20000, 'sequencers': {'s': {'netzero': {",
    "    'module_type': 'QCM', 'outputs': [{'output': 0, 'path': 0, 'enabled': True, 'ihp_enabled': True,",
    "    'amplitude': 0.5, 'slew_rate_per_s': 2.5e8}]}}}}",
    "def run(source=prefix + pulse + 'stop', alignment=None, branches=None, model=physical, **limits):",
    "    return analyze_text(source, file='netzero.q1asm', sequencer_id='s', execution_mode='simulation',",
    "        alignment_mode=alignment, branch_assumptions=branches,",
    "        simulation={'startup_delay_ns': 100, 'physical': model, **limits})",
    "aligned = run(max_time_ns=112)",
    "local = run(alignment='none', max_time_ns=112)",
    "compare = run(prefix + 'set_cond 1,1,0,4\\n' + pulse + 'stop', branches={'s:set_cond:3:1': 'both'}, max_time_ns=112)",
    "equal = run(prefix + pulse + 'wait 100\\nstop', feedback_queue_capacity=1, inputs={'feedback': [",
    "    {'target': 's', 'time_ns': 117, 'id': key, 'value': key} for key in (1, 2)]})",
    "completed = run()",
    "unmodeled = run(model={})",
    "dual = deepcopy(physical)",
    "outputs = dual['sequencers']['s']['netzero']['outputs']",
    "outputs.append({**outputs[0], 'output': 1, 'path': 1})",
    "multiple = run(prefix.replace('16384,0', '16384,8192') + pulse + 'stop', model=dual)",
    "def export(ir, mode):",
    "    results = [event for event in ir['events'] if event['kind'] == 'netzero_result']",
    "    terminal = next((event['id'] for event in ir['events'] if event['kind'] == 'branch_terminal'), None)",
    "    wait = next((event['id'] for event in ir['events'] if event['kind'] == 'wait'), None)",
    "    skip = next((event['id'] for event in ir['events'] if event['kind'] == 'conditional_skip' and event['meta'].get('branch_comparison_path') == 'fallthrough'), None)",
    "    return dict(html=render_html(ir, default_mode=mode), result=results[0], results=results, terminal=terminal, wait=wait, skip=skip)",
    "print(json.dumps({key: export(ir, 'debug' if key in ('equal', 'completed', 'unmodeled') else 'normal')",
    "    for key, ir in dict(aligned=aligned, local=local, compare=compare, equal=equal, completed=completed, unmodeled=unmodeled, multiple=multiple).items()}))",
  ].join("\n")], { cwd: repo, encoding: "utf8", timeout: 10000, maxBuffer: 4 * 1024 * 1024,
    env: { ...process.env, PYTHONPATH: path.join(repo, "src") } }));
}

async function open(page, dataset) {
  await page.goto("about:blank");
  await page.setContent(dataset.html);
  const inspector = page.locator("#event-inspector-fields");
  return {
    inspector,
    block: (id) => page.locator(`#timeline-root > svg [data-event-id="${id}"]`),
    field: (name) => inspector.getByText(name, {exact: true}).locator("xpath=following-sibling::dd[1]"),
  };
}

async function expectObservation(field, cutoff, planned, observedArea, plannedArea) {
  await expect(field("Compensation timing")).toHaveText(`Observation cutoff ${cutoff} ns · Planned end ${planned} ns`);
  await expect(field("Observed compensation area")).toHaveText(`${observedArea} normalized full scale × ns`);
  await expect(field("Planned compensation area")).toHaveText(`${plannedArea} normalized full scale × ns`);
}

test("standalone NetZero diagnostic opens the observed result in the active time basis", async ({page}) => {
  const {aligned, local} = fixtures();
  for (const [dataset, cutoff, planned] of [[aligned, 12, 17], [local, 112, 117]]) {
    const {block, field} = await open(page, dataset);
    await page.locator('[data-diagnostic-category="simulation_time_limit"]')
      .getByRole("link", {name: "Show event", exact: true}).click();
    await expect(block(dataset.result.id)).toHaveClass(/is-selected/);
    await expect(field("Source")).toHaveText("netzero.q1asm:4");
    await expectObservation(field, cutoff, planned, -1.75, -4);
    if (dataset === aligned) await expect(field("Time range")).toHaveText("8 ns → 12 ns");
  }
});

test("standalone NetZero observation fields follow the comparison path and completion evidence", async ({page}) => {
  const {compare, equal, completed, unmodeled} = fixtures();
  let {inspector, block, field} = await open(page, compare);
  expect(compare.result.meta.branch_comparison_path).toBe("taken");
  await block(compare.terminal).press("Enter");
  await inspector.getByRole("button", {name: "Show evidence", exact: true}).click();
  await expect(block(compare.result.id)).toHaveClass(/is-selected/);
  await expectObservation(field, 12, 17, -1.75, -4);

  ({block, field} = await open(page, equal));
  expect(equal.result.meta.status).toBe("incomplete");
  await block(equal.result.id).locator("rect").click();
  await expectObservation(field, 17, 17, -4, -4);

  for (const [dataset, status] of [[completed, "modeled"], [unmodeled, "unmodeled"]]) {
    expect(dataset.result.meta.status).toBe(status);
    ({block, field} = await open(page, dataset));
    await block(dataset.result.id).locator("rect").click();
    await expect(field("Source")).toHaveText("netzero.q1asm:4");
    await expect(field("Compensation timing")).toHaveCount(0);
    await expect(field("Observed compensation area")).toHaveCount(0);
    await expect(field("Planned compensation area")).toHaveCount(0);
  }
});

test("standalone Normal NetZero command and adjacent wait keep their pointer selection in a narrow viewport", async ({page}) => {
  const {equal} = fixtures();
  await page.setViewportSize({width: 720, height: 1000});
  const {block, field, inspector} = await open(page, equal);
  const origin = block(equal.result.meta.source_event_id);
  const result = block(equal.result.id);
  await analysisDetails(page);
  await page.getByRole("button", {name: "Normal", exact: true}).click();
  await origin.locator("rect").click();
  await expect(origin).toHaveClass(/is-selected/);
  await inspector.getByRole("button", {name: "Show NetZero output 0", exact: true}).click();
  await expect(result).toHaveClass(/is-selected/);
  await expect(field("Time range")).toHaveText("8 ns → 17 ns");
  await block(equal.wait).locator("rect").click();
  await expect(block(equal.wait)).toHaveClass(/is-selected/);
  await expect(field("Time range")).toContainText("Start 12 ns · Observation cutoff 17 ns");
  await page.getByRole("button", {name: "Debug", exact: true}).click();
  for (const target of [origin, result]) {
    await target.locator("rect").click();
    await expect(target).toHaveClass(/is-selected/);
  }
});

test("standalone NetZero actions select each retained output and respect the comparison path", async ({page}) => {
  const {multiple, compare, unmodeled} = fixtures();
  for (const [dataset, labels] of [[multiple, ["Show NetZero output 0", "Show NetZero output 1"]],
    [compare, ["Show NetZero output 0"]], [unmodeled, ["Show NetZero result"]]]) {
    const {block, inspector} = await open(page, dataset);
    await analysisDetails(page);
    await page.getByRole("button", {name: "Normal", exact: true}).click();
    const origin = block(dataset.result.meta.source_event_id);
    const actions = inspector.getByRole("button", {name: /^Show NetZero /});
    for (const [index, label] of labels.entries()) {
      await origin.locator("rect").click();
      await expect(origin).toHaveClass(/is-selected/);
      await expect(actions).toHaveText(labels);
      await actions.getByText(label, {exact: true}).click();
      await expect(block(dataset.results[index].id)).toHaveClass(/is-selected/);
      if (dataset.skip) await expect(block(dataset.skip)).not.toHaveClass(/is-selected/);
    }
    if (dataset.skip) {
      await block(dataset.skip).locator("rect").click();
      await expect(block(dataset.skip)).toHaveClass(/is-selected/);
      await expect(actions).toHaveCount(0);
    }
  }
});
