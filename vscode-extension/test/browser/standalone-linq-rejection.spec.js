const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect, analysisDetails } = require("./helpers");

function fixtures() {
  const repo = path.resolve(__dirname, "../../..");
  return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from copy import deepcopy", "from q1timeline.analysis.api import analyze_documents",
    "from q1timeline.render.html import render_html",
    "tx = 'fb_com_data 16,7,4\\nfb_com_data 16,9,4\\nstop'",
    "rx = 'wait 100\\nstop'",
    "config = {'startup_delay_ns': 100, 'feedback_queue_capacity': 1}",
    "routes = [{'source': 'tx', 'target': 'rx', 'id': 16, 'latency_ns': 20}]",
    "def simulate(source, *, branches=None, **options):",
    "    return analyze_documents(source, execution_mode='simulation', branch_assumptions=branches, simulation={**config, **options})",
    "def rejected(ir):",
    "    return next(event for event in ir['events'] if event['kind'] == 'feedback_rejected')",
    "baseline = simulate({'tx.q1asm': tx, 'rx.q1asm': rx}, routes=routes)",
    "compared = simulate({'tx.q1asm': 'set_cond 1,1,0,4\\n' + tx, 'rx.q1asm': rx},",
    "    routes=routes, branches={'tx:set_cond:0:1': 'both'})",
    "scenario = simulate({'rx.q1asm': rx}, inputs={'feedback': [",
    "    {'target': 'rx', 'time_ns': time, 'id': 16, 'value': value} for time, value in ((120, 7), (124, 9))]})",
    "missing = deepcopy(baseline)",
    "rejected(missing)['meta']['source_event_id'] = 'missing-sender'",
    "annotation = deepcopy(baseline)",
    "send_id = rejected(annotation)['meta']['source_event_id']",
    "send = next(event for event in annotation['events'] if event['id'] == send_id)",
    "annotation['events'] = [event for event in annotation['events'] if not (event['kind'] == 'fb_com_data'",
    "    and event['meta']['rt_packet_id'] == send['meta']['source_packet_id'])]",
    "wrong_path = deepcopy(compared)",
    "send = next(event for event in wrong_path['events'] if event['id'] == rejected(wrong_path)['meta']['source_event_id'])",
    "send['meta']['branch_comparison_path'] = 'fallthrough'",
    "print(json.dumps({key: {'ir': ir, 'html': render_html(ir)} for key, ir in dict(baseline=baseline, compared=compared,",
    "    annotation=annotation, scenario=scenario, missing=missing, wrong_path=wrong_path).items()}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
}

test("standalone LINQ rejection reveals the exact routed sender and keeps receiver diagnostics", async ({page}) => {
  const datasets = fixtures();
  const inspector = page.locator("#event-inspector-fields");
  const field = (label) => inspector.getByText(label, {exact: true}).locator("xpath=following-sibling::dd[1]");
  const sender = inspector.getByRole("button", {name: "Show sender", exact: true});
  const node = (id) => page.locator(`#timeline-root > svg [data-event-id="${id}"]`);
  const diagnostic = page.locator('.diagnostic[data-diagnostic-category="feedback_queue_overflow"]');
  const showRejection = diagnostic.getByRole("link", {name: "Show event"});
  for (const [key, {ir, html}] of Object.entries(datasets)) {
    const rejection = ir.events.find((event) => event.kind === "feedback_rejected");
    expect(rejection.t0.value).toBe(124);
    await page.goto("about:blank");
    await page.setContent(html);
    if (rejection.meta.comparison_terminal) {
      await node(rejection.id).press("Enter");
      await expect(field("Execution status")).toHaveText("error");
      await expect(field("Reason")).toHaveText("Feedback queue overflow");
    } else {
      await showRejection.click();
      await expect(field("Error")).toHaveText("LINQ feedback queue overflow (capacity 1).");
    }
    await expect(node(rejection.id)).toHaveClass(/is-selected/);
    await expect(field("Sequencer")).toHaveText("rx");
    if (["scenario", "missing", "wrong_path"].includes(key)) {
      await expect(sender).toHaveCount(0);
      continue;
    }
    const send = ir.events.find((event) => event.id === rejection.meta.source_event_id);
    const expected = key === "annotation" ? send : ir.events.find((event) => event.kind === "fb_com_data"
      && event.meta.rt_packet_id === send.meta.source_packet_id);
    expect(send.meta.value).toBe(9);
    expect(expected.meta.branch_comparison_path).toBe(key === "compared" ? "taken" : undefined);
    await sender.click();
    await expect(node(expected.id)).toHaveClass(/is-selected/);
    await expect(field("Sequencer")).toHaveText("tx");
    await expect(field("Source")).toHaveText(`tx.q1asm:${key === "compared" ? 3 : 2}`);
    if (!rejection.meta.comparison_terminal) {
      await showRejection.click();
      await expect(node(rejection.id)).toHaveClass(/is-selected/);
    }
  }
});

function analyticalFixtures() {
  const repo = path.resolve(__dirname, "../../..");
  return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from copy import deepcopy",
    "from q1timeline.analysis.api import analyze_text",
    "from q1timeline.render.html import render_html",
    "def run(enable, alignment='none'):",
    "    source = f'wait 8\\nfb_com_extra {enable},0xA1B2,4\\nfb_com_data 1,0x12345678,4\\nwait 100\\nfb_pop_data 1,R0\\nstop'",
    "    return analyze_text(source, file='self-cast.q1asm', sequencer_id='s', alignment_mode=alignment)",
    "def view(ir, mode='normal'):",
    "    return dict(ir=ir, html=render_html(ir, default_mode=mode))",
    "enabled = run(1)",
    "alternative = deepcopy(enabled)",
    "wait_id = next(e['id'] for e in alternative['events'] if e['kind']=='wait' and e['source']['line']==1)",
    "diagnostic = next(d for d in alternative['diagnostics'] if d.get('details', {}).get('reason')=='linq_configuration_unknown')",
    "diagnostic['related_events'].append(wait_id)",
    "before_origin = run(1, 'manual:-12')",
    "before_origin['project'] = {'alignment_mode':'first_anchor', 'alignment_anchor_kinds':['rt_queue_dequeue']}",
    "before_origin['simulation'] = {'s': {}}",
    "print(json.dumps(dict(disabled=view(run(0), 'debug'), enabled=view(enabled, 'debug'), normal=view(enabled),",
    "    alternative=view(alternative), beforeOrigin=view(before_origin))))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
}

test("standalone analytical LINQ keeps dwell but withholds unsupported extra-byte delivery", async ({page}) => {
  const {disabled, enabled} = analyticalFixtures();
  for (const [name, {ir, html}] of Object.entries({disabled, enabled})) {
    const wait = ir.events.find((event) => event.kind === "wait" && event.source.line === 1);
    const config = ir.events.find((event) => event.kind === "fb_com_extra");
    const send = ir.events.find((event) => event.kind === "feedback_com");
    expect([wait.t0.value, wait.t1.value, config.t0.value, config.t1.value, send.t0.value, send.t1.value])
      .toEqual([0, 8, 8, 12, 12, 16]);

    await page.goto("about:blank");
    await page.setContent(html);
    const svg = page.locator("#timeline-root > svg");
    const block = (event) => svg.locator(`[data-event-id="${event.id}"]`);
    await expect(block(wait)).toBeVisible();
    await expect(block(config)).toBeVisible();
    await expect(block(send)).toBeVisible();

    const arrow = svg.locator(`.feedback-flow-group[data-from-event-id="${send.id}"]`);
    const flow = (ir.feedback_flows || []).find((item) => item.from_event_id === send.id);
    const diagnostic = ir.diagnostics.find((item) => item.category === "analysis_incomplete" &&
      item.details?.reason === "linq_configuration_unknown");
    const channel = ir.feedback_balance.channels["1"];
    if (name === "enabled") {
      expect(diagnostic).toMatchObject({source: {file: "self-cast.q1asm", line: 2}, related_events: [config.id]});
      expect(flow?.timing?.arrival_ns).toBeUndefined();
      await expect(arrow).toHaveCount(0);
      expect(channel).toMatchObject({status: "incomplete", send_payloads: null, unconsumed_payloads: null});

      const limitation = page.locator('.diagnostic[data-diagnostic-category="analysis_incomplete"]').filter({
        has: page.getByText("self-cast.q1asm:2", {exact: true}),
      });
      const evidence = limitation.getByRole("link", {name: "Show event", exact: true});
      await expect(evidence).toHaveAttribute("data-related-event-id", config.id);
      await evidence.click();
      await expect(block(config)).toHaveClass(/is-selected/);
    } else {
      expect(diagnostic).toBeUndefined();
      expect(flow?.timing).toMatchObject({route: "self_cast", send_available_ns: 12, arrival_ns: 72});
      await expect(arrow).toBeVisible();
      expect(channel).toMatchObject({status: "balanced", send_payloads: 1, unconsumed_payloads: 0});
    }

    await analysisDetails(page);
    const balance = page.getByLabel("Feedback FIFO balance", {exact: true});
    if (await balance.getAttribute("open") === null) await balance.locator(":scope > summary").click();
    const row = balance.locator(".feedback-balance-channel");
    await expect(row).toBeVisible();
    await expect(row).toContainText(name === "enabled" ? "sends=1; payloads=unknown;" : "sends=1; payloads=1;");
    await expect(row).toContainText(name === "enabled" ? "unconsumed payloads=unknown" : "unconsumed payloads=0");
  }
});

test("standalone Normal opens collapsed LINQ diagnostics in the inspector", async ({page}) => {
  const {normal, alternative, beforeOrigin} = analyticalFixtures();
  const config = normal.ir.events.find((event) => event.kind === "fb_com_extra");
  const wait = normal.ir.events.find((event) => event.kind === "wait" && event.source.line === 1);
  const svg = page.locator("#timeline-root > svg");
  const block = (event) => svg.locator(`[data-event-id="${event.id}"]`);
  const inspector = page.locator("#event-inspector-fields");
  const field = (label) => inspector.getByText(label, {exact: true}).locator("xpath=following-sibling::dd[1]");
  const limitation = page.locator('.diagnostic[data-diagnostic-category="analysis_incomplete"]').filter({
    has: page.getByText("self-cast.q1asm:2", {exact: true}),
  });
  const evidence = limitation.getByRole("link", {name: "Show event", exact: true});
  const open = async ({html}) => {
    await page.goto("about:blank");
    await page.setContent(html);
    await analysisDetails(page);
  };

  await open(normal);
  const viewBox = await svg.getAttribute("viewBox");
  await expect(evidence).toHaveAttribute("data-related-event-id", config.id);
  await evidence.click();
  await expect(field("Source")).toHaveText("self-cast.q1asm:2");
  await expect(field("Time range")).toHaveText("8 -> 12");
  await expect(page.locator("body")).toHaveAttribute("data-mode", "normal");
  await expect(svg).toHaveAttribute("viewBox", viewBox);
  await expect(block(config)).toBeHidden();
  await expect(block(config)).not.toHaveClass(/is-selected/);

  await page.getByRole("button", {name: "Debug", exact: true}).click();
  await evidence.click();
  await expect(block(config)).toBeVisible();
  await expect(block(config)).toHaveClass(/is-selected/);

  await open(alternative);
  await evidence.click();
  await expect(block(wait)).toHaveClass(/is-selected/);
  await expect(field("Source")).toHaveText("self-cast.q1asm:1");

  await open(normal);
  await page.getByRole("searchbox", {name: "Filter source or kind"}).fill("wait 8");
  await expect(block(config)).toHaveClass(/is-filtered/);
  await evidence.click();
  await expect(field("Selected event")).toHaveText("None");

  await open(beforeOrigin);
  await expect(svg).toHaveAttribute("data-time-min", "0");
  await expect(block(config)).toHaveCount(0);
  await expect(evidence).toHaveCount(0);
  await expect(field("Selected event")).toHaveText("None");
});
