const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect, analysisDetails } = require("./helpers");

const assets = process.env.Q1LENS_BROWSER_ASSETS || path.resolve(__dirname, "../../src");

function fixtures() {
  const repo = path.resolve(__dirname, "../../..");
  return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from contextlib import redirect_stdout", "from io import StringIO",
    "from q1timeline.analysis.api import analyze_documents, analyze_text", "from q1timeline.cli import main",
    "from q1timeline.render.html import render_html",
    "source = 'set_latch_en 1,4\\nset_cond 1,1,0,12\\nupd_param 12\\nupd_param 12\\nset_cond 0,0,0,4\\nstop'",
    "repeated = 'move 2,R0\\nset_latch_en 1,4\\nbody: set_cond 1,1,0,12\\nupd_param 12\\nset_cond 0,0,0,4\\nloop R0,@body\\nstop'",
    "def run(program=source, alignment=None, path=None):",
    "    return analyze_text(program, file='recorded.q1asm', sequencer_id='s', execution_mode='simulation',",
    "        alignment_mode=alignment, branch_assumptions={'s:set_cond:1:1': path} if path else None,",
    "        simulation={'startup_delay_ns': 100, 'inputs': {'triggers': [{'target': 's', 'address': 1, 'time_ns': 110}]}})",
    "output = StringIO()",
    "with redirect_stdout(output):",
    "    main(['analyze', '--project', 'examples/set-cond-review/02-trigger-execution/q1timeline.yml',",
    "        '--format', 'vscode-json', '--include-timeline-ir', '--no-render'])",
    "original = json.loads(output.getvalue())['timeline_ir']",
    "cases = dict(original=original, mixed=run(), local=run(alignment='none'), manual=run(alignment='manual:-100'),",
    "    repeated=run(repeated), play=run(source.replace('upd_param 12', 'play 0,1,12')),",
    "    empty=run(source.replace('upd_param 12\\nupd_param 12\\n', '')), compare=run(path='both'), forced=run(path='taken'))",
    "wait_source = 'set_cond 1,0,1,4\\nwait_trigger 3,4\\nstop'",
    "arrival = {'triggers': [{'target': 's', 'address': 3, 'time_ns': 120}]}",
    "def wait_run(program=wait_source, alignment=None, branches=None, **options):",
    "    return analyze_text(program, file='recorded.q1asm', sequencer_id='s', execution_mode='simulation',",
    "        alignment_mode=alignment, branch_assumptions=branches, simulation={'startup_delay_ns':100, **options})",
    "synq = analyze_documents({'s.q1asm': wait_source.replace('wait_trigger 3,4', 'wait_sync 4'),",
    "    'peer.q1asm': 'jmp @end\\nwait_sync 4\\nend: stop'}, execution_mode='simulation', simulation={'startup_delay_ns':100})",
    "unknown = analyze_documents({'s.q1asm': 'set_latch_en 1,4\\nwait 12\\nset_cond 1,1,0,8\\nwait_trigger 3,4\\nstop',",
    "    'tx.q1asm': 'acquire 0,0,4\\nstop'}, execution_mode='simulation',",
    "    sequences={'tx':{'acquisitions':{'a':{'index':0,'num_bins':1}}}}, simulation={'startup_delay_ns':100,",
    "    'trigger_routes':[{'source':'tx','target':'s','address':1,'latency_ns':8}],",
    "    'inputs':{'acquisitions':[{'sequencer':'tx','acquisition_index':0,'bin_index':0,'i':17,'q':-9}]}})",
    "cases.update(pending=wait_run(), pending_local=wait_run(alignment='none'), pending_manual=wait_run(alignment='manual:-100'),",
    "    pending_compare=wait_run(branches={'s:set_cond:0:1':'both'}), bounded=wait_run(max_time_ns=112, inputs=arrival),",
    "    released=wait_run(inputs=arrival), skipped=wait_run(wait_source.replace('1,0,1,4','1,0,0,4')), synq=synq, unknown=unknown)",
    "print(json.dumps({name:dict(ir=ir, html=render_html(ir, default_mode='debug')) for name,ir in cases.items()}))",
  ].join("\n")], { cwd: repo, encoding: "utf8", timeout: 10000, maxBuffer: 8 * 1024 * 1024,
    env: { ...process.env, PYTHONPATH: path.join(repo, "src") } }));
}

async function open(page, data, viewer, mode = "debug") {
  await page.goto("about:blank");
  const shell = viewer === "standalone" ? data.html : data.html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi,
    (tag, attributes) => /type=["']application\/json["']/i.test(attributes) ? tag : "");
  await page.setContent(shell);
  if (viewer === "standalone") return;
  await page.emulateMedia({reducedMotion: "reduce"});
  await page.evaluate(({mode, sequencers}) => {
    window.messages = [];
    let saved = {expandedQ1IssueSequencers: sequencers.map((item) => item.id)};
    window.acquireVsCodeApi = () => ({postMessage: (message) => window.messages.push(message),
      getState: () => saved, setState: (value) => {saved = value;}});
    const state = document.createElement("meta");
    state.id = "q1timeline-webview-state";
    state.dataset.state = JSON.stringify({viewMode: mode});
    document.head.append(state);
  }, {mode, sequencers: data.ir.sequencers});
  for (const file of ["shared/timeline/renderer.css", "q1timeline/media/timeline.css", "q1timeline/media/hardwareView.css"]) {
    await page.addStyleTag({path: path.join(assets, file)});
  }
  for (const file of ["shared/timeline/instructionColors.js", "shared/timeline/renderer.js", "q1timeline/media/timelineAdapter.js",
    "q1timeline/media/hardwareState.js", "q1timeline/media/hardwareView.js", "q1timeline/media/timeline.js"]) {
    await page.addScriptTag({path: path.join(assets, file)});
  }
}

function issues(data) {
  return data.ir.events.filter((event) => event.kind === "q1_issue" && event.meta.op === "set_cond" && event.meta.branch_id);
}

function packetEvents(data, issue) {
  return data.ir.events.filter((event) => event.meta.rt_condition_issue_event_id === issue.id && !event.meta.branch_comparison_path);
}

function inspectorField(page, label) {
  return page.locator("#event-inspector-fields").getByText(label, {exact: true}).locator("xpath=following-sibling::dd[1]");
}

async function inspect(page, issue, viewer) {
  const selector = viewer === "standalone" ? `#timeline-root > svg g[data-event-id="${issue.id}"]` : `.timeline-block[data-block-id="${issue.id}"]`;
  const target = page.locator(selector);
  if (viewer === "extension") {
    const collapsed = page.getByRole("button", {name: /^Expand \d+ Q1 instructions$/});
    while (!await target.count() && await collapsed.count()) await collapsed.first().press("Enter");
  }
  await target.press("Enter");
  return inspectorField(page, "Recorded RT decisions");
}

async function selectEvidence(page, link, target, viewer) {
  await expect(link).toHaveAttribute("data-related-event-id", target.id);
  await link.click();
  if (viewer === "standalone") {
    await expect(page.locator("#timeline-root > svg .is-selected")).toHaveAttribute("data-event-id", target.id);
  } else {
    expect(await page.evaluate(() => window.messages.filter((item) => item.type === "eventClick").at(-1)?.eventId)).toBe(target.id);
  }
  await expect(inspectorField(page, "Source")).toHaveText(`${path.basename(target.source.file)}:${target.source.line}`);
}

test("recorded conditions link the exact mixed packets in aligned and local time", async ({page}) => {
  const {mixed, local, manual} = fixtures();
  for (const [viewer, data, labels] of [["standalone", mixed, ["Skip at 4 ns", "Execute at 16 ns"]],
    ["standalone", local, ["Skip at 104 ns", "Execute at 116 ns"]],
    ["extension", manual, ["Skip at 104 ns", "Execute at 116 ns"]]]) {
    await open(page, data, viewer);
    if (viewer === "extension") {
      await analysisDetails(page);
      await page.locator('button[data-time-basis="local"]').click();
    }
    const [issue] = issues(data);
    const packets = packetEvents(data, issue);
    expect(packets.map((packet) => packet.kind)).toEqual(["conditional_skip", "upd_param"]);
    for (const [index, label] of labels.entries()) {
      const field = await inspect(page, issue, viewer);
      await expect(field.getByRole("button")).toHaveText(labels);
      await selectEvidence(page, field.getByRole("button", {name: label, exact: true}), packets[index], viewer);
    }
  }
});

test("recorded conditions use occurrence IDs and one decision per two-path play", async ({page}) => {
  const {repeated, play, empty} = fixtures();
  for (const viewer of ["standalone", "extension"]) {
    await open(page, repeated, viewer);
    const controls = issues(repeated);
    expect(controls.map((issue) => issue.meta.branch_id)).toEqual(["s:set_cond:2:1", "s:set_cond:2:2"]);
    for (const [index, label] of ["Skip at 4 ns", "Execute at 16 ns"].entries()) {
      const field = await inspect(page, controls[index], viewer);
      await expect(field.getByRole("button")).toHaveText([label]);
      await selectEvidence(page, field.getByRole("button"), packetEvents(repeated, controls[index])[0], viewer);
    }

    await open(page, play, viewer);
    const [issue] = issues(play);
    const packets = packetEvents(play, issue);
    expect(packets.map((packet) => packet.kind)).toEqual(["conditional_skip", "play", "play"]);
    expect(packets[1].meta.rt_packet_id).toBe(packets[2].meta.rt_packet_id);
    const field = await inspect(page, issue, viewer);
    await expect(field.getByRole("button")).toHaveText(["Skip at 4 ns", "Execute at 16 ns"]);
    await selectEvidence(page, field.getByRole("button", {name: "Execute at 16 ns", exact: true}), packets[1], viewer);

    await open(page, empty, viewer);
    await expect(await inspect(page, issues(empty)[0], viewer)).toHaveText("None observed");
  }
});

test("recorded condition evidence stays separate from Compare Both and forced paths", async ({page}) => {
  const {compare, forced} = fixtures();
  for (const viewer of ["standalone", "extension"]) {
    await open(page, compare, viewer);
    const [issue] = issues(compare);
    expect(issue.meta.comparison_paths).toEqual(["taken", "fallthrough"]);
    const field = await inspect(page, issue, viewer);
    await expect(field.getByRole("button")).toHaveText(["Skip at 4 ns", "Execute at 16 ns"]);
    expect(await field.getByRole("button").evaluateAll((nodes) => nodes.map((node) => node.dataset.relatedEventId)))
      .toEqual(packetEvents(compare, issue).map((event) => event.id));
    const pathField = viewer === "standalone" ? "Recorded branch path" : "Branch assumption";
    await expect(inspectorField(page, pathField)).toHaveText(viewer === "standalone" ? "Execute and Skip" : "Execute / Skip");

    await open(page, forced, viewer);
    await expect(await inspect(page, issues(forced)[0], viewer)).toHaveText("Unavailable under a forced assumption");
    await expect(inspectorField(page, pathField)).toHaveText("Execute");
  }
});

test("the original trigger example exposes observed packets and keeps host condition controls", async ({page}) => {
  const {original} = fixtures();
  const [issue] = issues(original);
  const packets = packetEvents(original, issue);
  const labels = ["Skip at 8 ns", "Execute at 48 ns"];
  await open(page, original, "standalone");
  await expect((await inspect(page, issue, "standalone")).getByRole("button")).toHaveText(labels);

  await open(page, original, "extension", "normal");
  const control = page.locator(`.q1timeline-control-chip-branch[data-event-id="${issue.id}"]`);
  const popover = page.getByRole("dialog", {name: "Conditional RT execution"});
  await control.click();
  await expect(popover.getByRole("button", {name: "Execute", exact: true})).toBeVisible();
  await expect(popover.getByRole("button", {name: "Skip", exact: true})).toBeVisible();
  await popover.getByRole("button", {name: "Compare both", exact: true}).click();
  expect(await page.evaluate(() => window.messages.filter((item) => item.type === "setBranchAssumption").at(-1)))
    .toEqual({type: "setBranchAssumption", branchId: issue.meta.branch_id, path: "both", eventId: issue.id});
  for (const [index, label] of labels.entries()) {
    await control.click();
    await expect(popover.getByRole("button", {name: /^(Skip|Execute) at /})).toHaveText(labels);
    await popover.getByRole("button", {name: label, exact: true}).click();
    expect(await page.evaluate(() => window.messages.filter((item) => item.type === "eventClick").at(-1)?.eventId)).toBe(packets[index].id);
  }
});

test("armed trigger and SYNQ waits retain their recorded condition in both viewers", async ({page}) => {
  const {pending, pending_local: local, pending_manual: manual, synq, bounded} = fixtures();
  for (const [viewer, localData] of [["standalone", local], ["extension", manual]]) {
    for (const [data, time, kind, timing] of [
      [pending, 0, "trigger_wait", "Arm 0 ns → waiting for trigger"],
      [localData, 100, "trigger_wait", "Arm 100 ns → waiting for trigger"],
      [synq, 0, "sync_wait", "Arm 0 ns → waiting for synchronization"],
      [bounded, 0, "trigger_wait", "observation cutoff 12 ns"],
    ]) {
      await open(page, data, viewer);
      if (viewer === "extension" && data === manual) {
        await analysisDetails(page);
        await page.locator('button[data-time-basis="local"]').click();
      }
      const [issue] = issues(data);
      const [wait] = packetEvents(data, issue);
      expect(wait.kind).toBe(kind);
      expect(wait.meta.pending).toBe(true);
      const field = await inspect(page, issue, viewer);
      await expect(field.getByRole("button")).toHaveText([`Execute at ${time} ns`]);
      await selectEvidence(page, field.getByRole("button"), wait, viewer);
      await expect(inspectorField(page, "Time range")).toContainText(timing);
    }
  }

  await open(page, pending, "extension", "normal");
  const [issue] = issues(pending);
  const [wait] = packetEvents(pending, issue);
  await page.locator(`.q1timeline-control-chip-branch[data-event-id="${issue.id}"]`).click();
  await page.getByRole("dialog", {name: "Conditional RT execution"})
    .getByRole("button", {name: "Execute at 0 ns", exact: true}).click();
  expect(await page.evaluate(() => window.messages.filter((item) => item.type === "eventClick").at(-1)?.eventId)).toBe(wait.id);
});

test("conditional waits distinguish completed, skipped, unknown and compared decisions", async ({page}) => {
  const {released, skipped, unknown, pending_compare: compare} = fixtures();
  for (const viewer of ["standalone", "extension"]) {
    for (const [data, label, kind] of [[released, "Execute at 0 ns", "wait_trigger"],
      [skipped, "Skip at 0 ns", "conditional_skip"], [compare, "Execute at 0 ns", "trigger_wait"]]) {
      await open(page, data, viewer);
      const [issue] = issues(data);
      const [wait] = packetEvents(data, issue);
      expect(wait.kind).toBe(kind);
      const field = await inspect(page, issue, viewer);
      await expect(field.getByRole("button")).toHaveText([label]);
      await selectEvidence(page, field.getByRole("button"), wait, viewer);
      if (data === released) {
        await expect(inspectorField(page, "Time range")).toHaveText("Arm 0 ns · Trigger arrival 20 ns · Complete 24 ns");
      }
    }
    await open(page, unknown, viewer);
    await expect(await inspect(page, issues(unknown)[0], viewer)).toHaveText("None observed");
  }
});
