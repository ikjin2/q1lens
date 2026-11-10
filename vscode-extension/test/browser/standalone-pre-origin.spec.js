const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect, analysisDetails } = require("./helpers");

function fixtures() {
  const repo = path.resolve(__dirname, "../../..");
  return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from copy import deepcopy", "from q1timeline.analysis.api import analyze_documents",
    "from q1timeline.render.html import render_html",
    "def run(source='wait 12\\nstop', alignment=None, more=(), feedback=(), branches=None):",
    "    return analyze_documents({'a.q1asm': source, 'peer.q1asm': 'wait 60000\\nstop'}, execution_mode='simulation',",
    "        alignment_mode=alignment, branch_assumptions=branches, simulation={'startup_delay_ns': 100, 'feedback_queue_capacity': 1,",
    "            'inputs': {'triggers': [{'target': 'a', 'address': address, 'time_ns': time} for address,time in ((2,99),(1,100),*more)],",
    "                       'feedback': list(feedback)}})",
    "default = run()",
    "explicit = run('wait 12\\nwait_trigger 3,4\\nstop', 'first_wait_trigger', more=((3,120),))",
    "local = run(alignment='none')",
    "compare = run('set_cond 1,1,0,4\\nwait 12\\nstop', branches={'a:set_cond:0:1':'both'})",
    "rejected = run(feedback=tuple({'target':'a','time_ns':99,'id':i,'value':i} for i in (1,2)))",
    "fallback = deepcopy(rejected)",
    "diagnostic = next(item for item in fallback['diagnostics'] if item['category'] == 'feedback_queue_overflow')",
    "peer = next(event['id'] for event in fallback['events'] if event['sequencer_id'] == 'peer' and event['kind'] == 'wait')",
    "diagnostic['related_events'].append(peer)",
    "projection = deepcopy(default)",
    "wait = next(event for event in projection['events'] if event['sequencer_id'] == 'a' and event['kind'] == 'wait')",
    "old, zero = [event['id'] for event in projection['events'] if event['kind'] == 'trigger_receive']",
    "def edge(value): return {'kind': 'concrete', 'value': value}",
    "# Imported interval evidence exercises a boundary that primary RT commands cannot normally cross.",
    "wait.update(t0=edge(98), duration=edge(14))",
    "wait['meta'].update(aligned_t0=-2, local_t0=98)",
    "ended = {**wait, 'id': 'a:ended-at-zero', 't0': edge(97), 't1': edge(100), 'duration': edge(3),",
    "    'meta': {**wait['meta'], 'aligned_t0': -3, 'aligned_t1': 0, 'local_t0': 97, 'local_t1': 100}}",
    "projection['events'].append(ended)",
    "projection['feedback_flows'] = [dict(id=name, from_event_id=source, to_event_id=wait['id']) for name, source in [('old', old), ('zero', zero)]]",
    "print(json.dumps({**{key:render_html(ir) for key,ir in dict(default=default, explicit=explicit, local=local, compare=compare,",
    "    rejected=rejected, fallback=fallback, projection=projection).items()}, 'wait':wait['id'], 'ended':ended['id'], 'peer':peer}))",
  ].join("\n")], { cwd: repo, encoding: "utf8", timeout: 10000, maxBuffer: 4 * 1024 * 1024,
    env: { ...process.env, PYTHONPATH: path.join(repo, "src") } }));
}

async function open(page, html) {
  await page.goto("about:blank");
  await page.setContent(html);
  await analysisDetails(page);
}

test("standalone first-RT Normal keeps zero arrivals and Debug keeps prior arrivals", async ({page}) => {
  const data = fixtures();
  const svg = page.locator("#timeline-root > svg");
  const arrivals = svg.locator('[data-kind="trigger_receive"] title');
  for (const key of ["default", "compare"]) {
    await open(page, data[key]);
    for (const mode of ["Normal", "Debug", "Normal"]) {
      await page.getByRole("button", {name: mode, exact: true}).click();
      const zero = svg.getByRole("button", {name: "Trigger address 1 · 0 ns", exact: true});
      if (mode === "Normal") {
        await expect(svg).toHaveAttribute("data-time-min", "0");
        await expect(svg.getByRole("button", {name: "Trigger address 2 · -1 ns", exact: true})).toHaveCount(0);
        await expect(zero.first().locator("circle")).toHaveAttribute("cx", "190.00");
        if (key === "compare") await expect(svg.locator(".lane-label")).toHaveText(["Execute · RT", "Skip · RT"]);
      } else {
        await expect(svg.getByRole("button", {name: "Trigger address 2 · -1 ns", exact: true}).first()).toBeVisible();
        await expect(zero.first()).toBeVisible();
      }
    }
  }
  await open(page, data.explicit);
  await expect(svg).toHaveAttribute("data-time-min", "-13");
  await expect(arrivals).toContainText(["Trigger address 3 · 8 ns", "Trigger address 2 · -13 ns", "Trigger address 1 · -12 ns"]);
  await open(page, data.local);
  await expect(arrivals).toHaveText(["Trigger address 2 · 99 ns", "Trigger address 1 · 100 ns"]);
});

test("standalone pre-origin diagnostics and connectors follow active visibility", async ({page}) => {
  const {rejected, fallback, projection, wait, ended, peer} = fixtures();
  const svg = page.locator("#timeline-root > svg");
  const node = (id) => svg.locator(`[data-event-id="${id}"]`);
  const link = page.locator('[data-diagnostic-category="feedback_queue_overflow"]')
    .getByRole("link", {name: "Show event", exact: true});
  await open(page, rejected);
  await expect(link).toHaveCount(0);
  await expect(svg.locator('[data-kind="feedback_rejected"]')).toHaveCount(0);
  await page.getByRole("button", {name: "Debug", exact: true}).click();
  await link.click();
  await expect(svg.locator('[data-kind="feedback_rejected"]')).toHaveClass(/is-selected/);
  await page.getByRole("button", {name: "Normal", exact: true}).click();
  await expect(link).toHaveCount(0);

  await open(page, fallback);
  await link.click();
  await expect(node(peer)).toHaveClass(/is-selected/);

  await open(page, projection);
  await expect(node(ended)).toHaveCount(0);
  await expect(node(wait).locator("rect")).toHaveAttribute("x", "190.00");
  await expect(node(wait).locator("title")).toContainText("-2 -> 12");
  await expect(svg.locator('.feedback-flow-group[data-flow-id="old"]')).toHaveCount(0);
  await expect(svg.locator('.feedback-flow-group[data-flow-id="zero"] .feedback-flow')).toHaveAttribute("d", /190\.00 83\.00$/);
  await page.getByRole("button", {name: "Debug", exact: true}).click();
  await expect(node(ended)).toBeVisible();
  await expect(svg.locator('.feedback-flow-group[data-flow-id="old"]')).toBeVisible();
});
