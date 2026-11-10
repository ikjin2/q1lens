const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect, analysisDetails } = require("./helpers");

function fixtures() {
  const repo = path.resolve(__dirname, "../../..");
  return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text", "from q1timeline.render.html import render_html",
    "setter = 'set_freq 4000000\\n'",
    "update = 'upd_param 4\\n'",
    "cond = 'set_cond 1,1,0,4\\n'",
    "reset = 'set_cond 0,0,0,0\\n'",
    "def run(source, branch=None, alignment=None):",
    "    return analyze_text(source, file='setter.q1asm', sequencer_id='seq', execution_mode='simulation',",
    "        simulation={'startup_delay_ns': 100}, branch_assumptions={branch: 'both'} if branch else None, alignment_mode=alignment)",
    "rewrite_source = (setter + update * 2) * 2 + 'stop'",
    "rewrite = run(rewrite_source)",
    "imported = run(rewrite_source, alignment='none')",
    "del imported['simulation']",
    "for event in imported['events']:",
    "    event['meta'].pop('committed_setter_event_ids', None)",
    "shared = run(setter + update + cond + 'upd_param 8\\n' + reset + update + 'stop', 'seq:set_cond:2:1')",
    "separate = run(cond + setter + 'upd_param 8\\n' + reset + update + 'stop', 'seq:set_cond:0:1')",
    "legacy_path = analyze_text('jlt R0,1,@end\\n' + setter + update * 2 + 'end: wait 4\\nstop',",
    "    file='setter.q1asm', sequencer_id='seq', branch_policy='explore_both_with_depth_limit')",
    "first, carry = [event for event in legacy_path['events'] if event['kind'] == 'upd_param']",
    "for field in ('applied_state', 'applied_setter_event_ids'):",
    "    carry['meta'][field] = first['meta'][field]",
    "print(json.dumps({key: {'ir': ir, 'html': render_html(ir, default_mode='debug' if key == 'legacy_path' else 'normal')}",
    "    for key, ir in dict(rewrite=rewrite, imported=imported, shared=shared, separate=separate, legacy_path=legacy_path).items()}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
}

async function openView(page, html) {
  await page.goto("about:blank");
  await page.setContent(html);
  await analysisDetails(page);
}

function view(page) {
  const svg = page.locator("#timeline-root > svg");
  const node = (event) => svg.locator(`[data-event-id="${event.id}"]`);
  const source = page.locator("#event-inspector-fields").getByText("Source", {exact: true}).locator("xpath=following-sibling::dd[1]");
  const click = (event) => node(event).locator("rect").click();
  const relatedUpdates = () => svg.locator('.is-related[data-kind="upd_param"]').evaluateAll((nodes) => nodes.map((item) => item.dataset.eventId).sort());
  return {node, source, click, relatedUpdates};
}

test("standalone setter and RT commit link both ways without replacing the selected event", async ({page}) => {
  const {rewrite, imported} = fixtures();
  for (const {ir, html} of [rewrite, imported]) {
    const setters = ir.events.filter((event) => event.kind === "q1_issue" && event.meta.op === "set_freq");
    const updates = ir.events.filter((event) => event.kind === "upd_param");
    await openView(page, html);
    const {node, source, click, relatedUpdates} = view(page);
    await click(updates[0]);
    await expect(node(updates[0])).toHaveClass(/is-selected/);
    await expect(node(setters[0])).toBeHidden();
    await expect(source).toHaveText("setter.q1asm:2");
    await page.getByRole("button", {name: "Debug", exact: true}).click();
    await expect(node(updates[0])).toHaveClass(/is-selected/);
    await expect(node(setters[0])).toHaveClass(/is-related/);
    await expect(source).toHaveText("setter.q1asm:2");

    for (const [index, setter] of setters.entries()) {
      await click(setter);
      await expect(node(setter)).toHaveClass(/is-selected/);
      await expect(source).toHaveText(`setter.q1asm:${setter.source.line}`);
      await expect.poll(relatedUpdates).toEqual([updates[index * 2].id]);
      await click(updates[index * 2 + 1]);
      await expect(node(setter)).not.toHaveClass(/is-related/);
    }
  }
});

test("standalone setter selection respects shared and separate Compare Both commits", async ({page}) => {
  const {shared, separate, legacy_path: legacyPath} = fixtures();
  for (const [dataset, expected] of [[shared, [[2, undefined]]],
    [separate, [[5, undefined], [3, "taken"], [5, "fallthrough"]]], [legacyPath, [[3, "fallthrough"]]]]) {
    const {ir, html} = dataset;
    const setter = ir.events.find((event) => event.kind === "q1_issue" && event.meta.op === "set_freq");
    const commits = expected.map(([line, comparisonPath]) => ir.events.find((event) =>
      event.kind === "upd_param" && event.source.line === line && event.meta.branch_comparison_path === comparisonPath));
    await openView(page, html);
    await page.getByRole("button", {name: "Debug", exact: true}).click();
    const {node, source, click, relatedUpdates} = view(page);
    await click(setter);
    await expect.poll(relatedUpdates).toEqual(commits.map((event) => event.id).sort());
    for (const commit of commits) {
      await click(commit);
      await expect(node(commit)).toHaveClass(/is-selected/);
      await expect(node(setter)).toHaveClass(/is-related/);
      await expect(source).toHaveText(`setter.q1asm:${commit.source.line}`);
      await expect.poll(relatedUpdates).toEqual([]);
    }
  }
});
