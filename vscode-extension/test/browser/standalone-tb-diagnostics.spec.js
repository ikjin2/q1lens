const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect, analysisDetails } = require("./helpers");

function fixtures() {
  const repo = path.resolve(__dirname, "../../..");
  return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from copy import deepcopy", "from q1timeline.analysis.api import analyze_documents",
    "from q1timeline.render.html import render_html",
    "sender = 'fb_acq_tb_id 16,4\\nfb_acq_tb_cfg 1,0,1,4\\nacquire 0,0,8\\nwait 100\\nstop'",
    "def run(shift):",
    "    return analyze_documents({'a.q1asm': sender, 'b.q1asm': sender.replace('1,0,1,4', f'1,{shift},1,4'),",
    "        'rx.q1asm': 'wait 100\\nstop'}, execution_mode='simulation', simulation={'startup_delay_ns': 100,",
    "        'inputs': {'acquisitions': [{'sequencer': name, 'acquisition_index': 0, 'bin_index': 0, 'thresholded': 1} for name in ('a', 'b')]},",
    "        'routes': [{'source': name, 'target': 'rx', 'id': 16, 'latency_ns': 5} for name in ('a', 'b')]})",
    "ir = run(0)",
    "rows = {item['source']['file']: item['related_events'] for item in ir['diagnostics'] if item['category'] == 'feedback_write_combine'}",
    "a, b, receiver_cutoff = rows['rx.q1asm']",
    "sender_cutoff = rows['a.q1asm'][-1]",
    "partial = deepcopy(ir)",
    "for item in partial['diagnostics']:",
    "    if item['category'] != 'feedback_write_combine':",
    "        continue",
    "    if item['source']['file'] == 'rx.q1asm':",
    "        item['related_events'] = [b, b, receiver_cutoff]",
    "    elif item['source']['file'] == 'a.q1asm':",
    "        item['related_events'] = [a]",
    "print(json.dumps(dict(full=render_html(ir), partial=render_html(partial), success=render_html(run(2)),",
    "    a=a, b=b, receiver_cutoff=receiver_cutoff, sender_cutoff=sender_cutoff)))",
  ].join("\n")], { cwd: repo, encoding: "utf8", timeout: 10000, maxBuffer: 4 * 1024 * 1024,
    env: { ...process.env, PYTHONPATH: path.join(repo, "src") } }));
}

async function open(page, html) {
  await page.goto("about:blank");
  await page.setContent(html);
  await analysisDetails(page);
}

function row(page, location) {
  return page.locator('[data-diagnostic-category="feedback_write_combine"]')
    .filter({has: page.getByText(location, {exact: true})});
}

test("standalone TB rejection links open each retained contributor and local cutoff", async ({page}) => {
  const {full, a, b, receiver_cutoff: receiverCutoff, sender_cutoff: senderCutoff} = fixtures();
  await open(page, full);
  const rx = row(page, "rx.q1asm:1");
  const selected = page.locator("#timeline-root > svg .is-selected");
  for (const mode of ["Normal", "Debug"]) {
    await page.getByRole("button", {name: mode, exact: true}).click();
    for (const [label, id] of [["Show event", a], ["Show a contribution", a], ["Show b contribution", b], ["Show rx RT cutoff", receiverCutoff]]) {
      await rx.getByRole("link", {name: label, exact: true}).click();
      await expect(selected).toHaveAttribute("data-event-id", id);
    }
    await row(page, "a.q1asm:3").getByRole("link", {name: "Show a RT cutoff", exact: true}).click();
    await expect(selected).toHaveAttribute("data-event-id", senderCutoff);
  }
  const filter = page.getByRole("searchbox", {name: "Filter source or kind"});
  await filter.fill("feedback_contribution");
  await expect(rx.getByRole("link", {name: "Show rx RT cutoff", exact: true})).toHaveCount(0);
  await expect(rx.getByRole("link", {name: "Show b contribution", exact: true})).toBeVisible();
  await filter.clear();
  await expect(rx.getByRole("link", {name: "Show rx RT cutoff", exact: true})).toBeVisible();
});

test("standalone TB rejection actions keep only distinct retained evidence", async ({page}) => {
  const {partial, success} = fixtures();
  await open(page, partial);
  await expect(row(page, "rx.q1asm:1").getByRole("link")).toHaveText(["Show event", "Show b contribution", "Show rx RT cutoff"]);
  await expect(row(page, "a.q1asm:3").getByRole("link")).toHaveText(["Show event"]);
  await open(page, success);
  await expect(page.locator("a[data-tb-evidence]")).toHaveCount(0);
});
