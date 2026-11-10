const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect, analysisDetails } = require("./helpers");

function fixtures() {
  const repo = path.resolve(__dirname, "../../..");
  return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from copy import deepcopy", "from q1timeline.analysis.api import analyze_text",
    "from q1timeline.render.html import render_html",
    "source = ('set_cond 1,1,0,4\\nfb_tdc_tdelta_id 16,4\\nset_cond 0,0,0,4\\n'",
    "    'acquire_timetags 0,0,1,0,8\\nacquire_timetags 0,0,0,1025,4\\nwait 100\\nstop')",
    "def analyze(alignment=None):",
    "    return analyze_text(source, file='tags.q1asm', sequencer_id='tags', execution_mode='simulation', alignment_mode=alignment,",
    "        branch_assumptions={'tags:set_cond:0:1': 'both'}, sequence={'acquisitions': {'tags': {'index': 0, 'num_bins': 1}}},",
    "        simulation={'startup_delay_ns': 100, 'inputs': {'digital': {'tags': {'initial_level': 0, 'transitions': [",
    "            {'time_ticks': 107*128+1, 'level': 1}, {'time_ticks': 107*128+2, 'level': 0}]}}}})",
    "ir = analyze()",
    "terminal, = [event for event in ir['events'] if event['kind'] == 'branch_terminal']",
    "ids = terminal['meta']['comparison_terminal']['source_event_ids']",
    "by_id = {event['id']: event for event in ir['events']}",
    "result, wait = [by_id[id] for id in ids]",
    "skip, = [event for event in ir['events'] if event['kind'] == 'wait' and event['meta'].get('branch_comparison_path') == 'fallthrough']",
    "issue, = [event for event in ir['events'] if event['kind'] == 'q1_issue' and event['source']['line'] == 4]",
    "def imported(references, source_ir=ir):",
    "    copy = deepcopy(source_ir)",
    "    event = next(event for event in copy['events'] if event['id'] == terminal['id'])",
    "    event['meta']['comparison_terminal']['source_event_ids'] = references",
    "    return render_html(copy)",
    "print(json.dumps(dict(html=render_html(ir), terminal=terminal, result=result, wait=wait, skip=skip, issue=issue,",
    "    mixed=imported(['missing', issue['id'], skip['id'], result['id'], result['id'], wait['id'], terminal['id']]),",
    "    single=imported([wait['id']]), missing=imported(['missing', skip['id']]),",
    "    legacy=imported([result['id'], issue['id'], wait['id']], analyze('none')))))",
  ].join("\n")], { cwd: repo, encoding: "utf8", timeout: 10000, maxBuffer: 4 * 1024 * 1024,
    env: { ...process.env, PYTHONPATH: path.join(repo, "src") } }));
}

async function open(page, html) {
  await page.goto("about:blank");
  await page.setContent(html);
  await analysisDetails(page);
  const inspector = page.locator("#event-inspector-fields");
  return {
    block: (event) => page.locator(`#timeline-root > svg [data-event-id="${event.id}"]`),
    buttons: inspector.locator("dt", {hasText: /^Evidence$/}).locator("xpath=following-sibling::dd[1]//button"),
    timing: inspector.getByText("Time range", {exact: true}).locator("xpath=following-sibling::dd[1]"),
  };
}

test("standalone Execute terminal opens its QTM result and exact RT cutoff", async ({page}) => {
  const {html, terminal, result, wait, skip} = fixtures();
  expect(result.meta.timetag_ticks).toBe(385);
  expect(wait.meta.observation_cutoff_ns).toBe(120.0078125);
  const {block, buttons, timing} = await open(page, html);
  for (const mode of ["Normal", "Debug"]) {
    await page.getByRole("button", {name: mode, exact: true}).click();
    await block(terminal).press("Enter");
    await expect(buttons).toHaveText(["Show evidence", "Show RT cutoff"]);
    await buttons.getByText("Show evidence", {exact: true}).click();
    await expect(block(result)).toHaveClass(/is-selected/);
    await block(terminal).press("Enter");
    await buttons.getByText("Show RT cutoff", {exact: true}).click();
    await expect(block(wait)).toHaveClass(/is-selected/);
    await expect(block(skip)).not.toHaveClass(/is-selected/);
    await expect(timing).toContainText("Observation cutoff 20.0078125 ns");
  }
});

test("standalone terminal evidence keeps a shared prefix and excludes hidden, missing, and wrong-path actions", async ({page}) => {
  const {mixed, single, missing, terminal, issue, result, wait} = fixtures();
  let {block, buttons} = await open(page, mixed);
  await block(terminal).press("Enter");
  await expect(buttons).toHaveText(["Show evidence", "Show RT cutoff"]);
  await buttons.getByText("Show evidence", {exact: true}).click();
  await expect(block(result)).toHaveClass(/is-selected/);
  await page.getByRole("button", {name: "Debug", exact: true}).click();
  await block(terminal).press("Enter");
  await expect(buttons).toHaveText(["Show evidence", "Show acquisition result", "Show RT cutoff"]);
  await buttons.getByText("Show evidence", {exact: true}).click();
  await expect(block(issue)).toHaveClass(/is-selected/);
  await block(terminal).press("Enter");
  await buttons.getByText("Show acquisition result", {exact: true}).click();
  await expect(block(result)).toHaveClass(/is-selected/);

  ({block, buttons} = await open(page, single));
  await block(terminal).press("Enter");
  await expect(buttons).toHaveText(["Show evidence"]);
  await buttons.click();
  await expect(block(wait)).toHaveClass(/is-selected/);

  ({block, buttons} = await open(page, missing));
  await block(terminal).press("Enter");
  await expect(buttons).toHaveCount(0);
});

test("standalone terminal evidence refreshes with filter and single-SVG visibility", async ({page}) => {
  const {legacy, terminal, issue} = fixtures();
  const {block, buttons} = await open(page, legacy);
  await expect(page.locator("template[data-timeline-mode]")).toHaveCount(0);
  await block(terminal).press("Enter");
  await expect(buttons).toHaveText(["Show evidence", "Show RT cutoff"]);
  await page.getByRole("searchbox", {name: "Filter source or kind"}).fill("acquisition_result");
  await expect(buttons).toHaveText(["Show evidence"]);
  await page.getByRole("searchbox", {name: "Filter source or kind"}).clear();
  await expect(buttons).toHaveText(["Show evidence", "Show RT cutoff"]);
  await page.getByRole("button", {name: "Debug", exact: true}).click();
  await expect(buttons).toHaveText(["Show evidence", "Show Q1 issue", "Show RT cutoff"]);
  await buttons.getByText("Show Q1 issue", {exact: true}).click();
  await expect(block(issue)).toHaveClass(/is-selected/);
});
