const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect } = require("./helpers");

function fixtures() {
  const repo = path.resolve(__dirname, "../../..");
  return JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from copy import deepcopy", "from q1timeline.analysis.api import analyze_documents, analyze_text",
    "from q1timeline.playback import PlaybackSession", "from q1timeline.render.html import render_html",
    "trigger = 'wait 20\\nwait_trigger 2,4\\nwait 8\\nstop'",
    "arrival = {'triggers': [{'target': 'seq', 'address': 2, 'time_ns': 150}]}",
    "def sim(source, inputs=None, start=100, branches=None, **limits):",
    "    return analyze_text(source, file='timing.q1asm', sequencer_id='seq', execution_mode='simulation',",
    "        simulation={'startup_delay_ns': start, 'inputs': inputs or {}, **limits}, branch_assumptions=branches)",
    "def local(ir):",
    "    ir = deepcopy(ir)",
    "    for event in ir['events']:",
    "        for key in ('aligned_t0', 'aligned_t1'):",
    "            event['meta'].pop(key, None)",
    "    return ir",
    "completed = sim(trigger, arrival)",
    "legacy = deepcopy(completed)",
    "for event in legacy['events']:",
    "    event['meta'].pop('rt_execution_ns', None)",
    "sync_programs = {'early.q1asm': 'wait 20\\nwait_sync 4\\nstop', 'late.q1asm': 'wait 40\\nwait_sync 4\\nstop'}",
    "sync = analyze_documents(sync_programs, execution_mode='simulation', simulation={'startup_delay_ns': 100})",
    "sync_cutoff = analyze_documents(sync_programs, execution_mode='simulation', simulation={'startup_delay_ns': 100, 'max_time_ns': 142})",
    "trigger_cutoff = sim(trigger.replace('2,4', '2,40'), arrival, max_time_ns=160)",
    "wait_cutoff = analyze_documents({'seq.q1asm': 'wait 20\\nwait 100\\nwait 8\\nstop', 'peer.q1asm': 'wait 4\\nstop'},",
    "    execution_mode='simulation', simulation={'startup_delay_ns': 100, 'max_time_ns': 160})",
    "play_cutoff = sim('wait 20\\nplay 0,1,100\\nstop', max_time_ns=160)",
    "invalid_plan = deepcopy(wait_cutoff)",
    "for event in invalid_plan['events']:",
    "    if 'planned_rt_end_ns' in event['meta']:",
    "        event['meta']['planned_rt_end_ns'] = event['t1']['value']",
    "compare = sim('wait 20\\nset_cond 1,1,0,4\\nwait_trigger 2,4\\nset_cond 0,0,0,0\\nwait 8\\nstop', arrival,",
    "    branches={'seq:set_cond:1:1': 'both'})",
    "fault = sim('wait 20\\nwait_trigger 2,4\\n' + 'nop\\n' * 35 + 'illegal\\nstop', arrival)",
    "play = PlaybackSession({'seq': ('timing.q1asm', trigger.replace('2,4', '2,0'))}, external_inputs={",
    "    'triggers': [{'id': 'source', 'address': 2, 'targets': ['seq'], 'times_ns': [50]}]})",
    "play.run(max_steps=100, budget_ms=1000)",
    "late = sim('upd_param 0\\nnop\\nnop\\nnop\\nwait 8\\nstop', start=4)",
    "zero = sim('upd_param 0\\nwait 8\\nstop')",
    "old_zero = deepcopy(zero)",
    "for event in old_zero['events']:",
    "    event['meta'].pop('next_dequeue_not_before_ns', None)",
    "skip = sim('set_cond 1,1,0,0\\nupd_param 8\\nset_cond 0,0,0,0\\nwait 8\\nstop')",
    "nominal = analyze_text('wait_trigger 2,4\\nupd_param 0\\nwait 8\\nstop', sequencer_id='seq')",
    "datasets = dict(completed=completed, local_trigger=local(completed), legacy=legacy, sync=sync, compare=compare, fault=fault,",
    "    trigger_cutoff=trigger_cutoff, local_cutoff=local(trigger_cutoff), sync_cutoff=sync_cutoff,",
    "    wait_cutoff=wait_cutoff, local_wait_cutoff=local(wait_cutoff), play_cutoff=play_cutoff, invalid_plan=invalid_plan, pending=sim(trigger),",
    "    play=play.snapshot(), late=late, local_zero=local(zero), old_zero=old_zero, skip=skip, nominal=nominal,",
    "    zero_trigger=sim(trigger.replace('2,4', '2,0'), arrival))",
    "print(json.dumps({key: {'ir': ir, 'html': render_html(ir, default_mode='debug' if key == 'play_cutoff' else 'normal')}",
    "    for key, ir in datasets.items()}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, maxBuffer: 4 * 1024 * 1024,
    env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
}

async function open(page, dataset) {
  await page.goto("about:blank");
  await page.setContent(dataset.html);
}

async function inspect(page, event) {
  const block = page.locator(`#timeline-root > svg [data-event-id="${event.id}"]`);
  await block.locator("rect").click();
  const inspector = page.locator("#event-inspector-fields");
  const field = (label) => inspector.getByText(label, {exact: true}).locator("xpath=following-sibling::dd[1]");
  return {title: block.locator("title"), field};
}

test("standalone completed wait phases use the active RT time basis", async ({page}) => {
  const datasets = fixtures();
  for (const [key, kind, expected] of [
    ["completed", "wait_trigger", ["Arm 20 ns · Trigger arrival 50 ns · Complete 54 ns"]],
    ["local_trigger", "wait_trigger", ["Arm 120 ns · Trigger arrival 150 ns · Complete 154 ns"]],
    ["sync", "wait_sync", ["Arm 20 ns · Barrier release 40 ns · Complete 44 ns", "Arm 40 ns · Barrier release 40 ns · Complete 44 ns"]],
    ["compare", "wait_trigger", ["Arm 20 ns · Trigger arrival 50 ns · Complete 54 ns"]],
  ]) {
    const dataset = datasets[key];
    const waits = dataset.ir.events.filter((event) => event.kind === kind);
    expect(waits).toHaveLength(expected.length);
    if (key === "compare") expect(waits[0].meta.branch_comparison_path).toBe("taken");
    await open(page, dataset);
    for (const [index, wait] of waits.entries()) {
      const {title, field} = await inspect(page, wait);
      await expect(title).toContainText(expected[index]);
      await expect(field("Time range")).toHaveText(expected[index]);
    }
  }
  for (const key of ["legacy", "play", "fault"]) {
    const dataset = datasets[key];
    await open(page, dataset);
    const wait = dataset.ir.events.find((event) => event.kind === "wait_trigger");
    if (key === "fault") expect(wait.meta.interrupted_by_fault).toBe(true);
    const {title, field} = await inspect(page, wait);
    await expect(title).not.toContainText("Trigger arrival");
    await expect(field("Time range")).not.toContainText("Trigger arrival");
  }
});

test("standalone zero-duration packets show the eligible dequeue independently of the next command", async ({page}) => {
  const datasets = fixtures();
  for (const [key, kind, time, label = "Earliest next dequeue"] of [
    ["late", "upd_param", 4], ["local_zero", "upd_param", 104], ["zero_trigger", "wait_trigger", 54],
    ["skip", "conditional_skip", 4], ["play", "wait_trigger", 54], ["nominal", "upd_param", 8, "Nominal earliest next dequeue"],
    ["old_zero", "upd_param", null], ["late", "wait", null],
  ]) {
    const dataset = datasets[key];
    const packet = dataset.ir.events.find((event) => event.kind === kind);
    await open(page, dataset);
    const {title, field} = await inspect(page, packet);
    if (time === null) {
      await expect(title).not.toContainText(/earliest next dequeue/i);
      await expect(field(label)).toHaveCount(0);
    } else {
      await expect(title).toContainText(`${label}: ${time} ns`);
      await expect(field(label)).toHaveText(`${time} ns`);
    }
    if (key === "late" && kind === "upd_param") {
      expect(packet.duration.value).toBe(0);
      expect(dataset.ir.events.find((event) => event.kind === "wait").meta.aligned_t0).toBe(16);
      await expect(field("Time range")).toHaveText("0 ns");
    }
    if (key === "zero_trigger") await expect(field("Time range")).toHaveText("Arm 20 ns · Trigger arrival 50 ns · Complete 50 ns");
  }
});

test("standalone released waits show the observation cutoff when the run ends before completion", async ({page}) => {
  const datasets = fixtures();
  for (const [key, kind, cutoff, planned, expected] of [
    ["trigger_cutoff", "wait_trigger", 160, 190, ["Arm 20 ns · Trigger arrival 50 ns · Observation cutoff 60 ns · Planned end 90 ns"]],
    ["local_cutoff", "wait_trigger", 160, 190, ["Arm 120 ns · Trigger arrival 150 ns · Observation cutoff 160 ns · Planned end 190 ns"]],
    ["sync_cutoff", "wait_sync", 142, 144, ["Arm 20 ns · Barrier release 40 ns · Observation cutoff 42 ns · Planned end 44 ns",
      "Arm 40 ns · Barrier release 40 ns · Observation cutoff 42 ns · Planned end 44 ns"]],
  ]) {
    const dataset = datasets[key];
    const waits = dataset.ir.events.filter((event) => event.kind === kind);
    expect(waits).toHaveLength(expected.length);
    await open(page, dataset);
    for (const [index, wait] of waits.entries()) {
      expect([wait.t1.value, wait.meta.observation_cutoff_ns, wait.meta.planned_rt_end_ns]).toEqual([cutoff, cutoff, planned]);
      const {title, field} = await inspect(page, wait);
      await expect(title).toContainText(expected[index]);
      await expect(title).toContainText(`observed duration: ${wait.duration.value}`);
      await expect(field("Time range")).toHaveText(expected[index]);
    }
  }
});

test("standalone clipped RT commands explain the planned end without extending the timeline", async ({page}) => {
  const datasets = fixtures();
  for (const [key, kind, end, expected] of [
    ["wait_cutoff", "wait", 60, "Start 20 ns · Observation cutoff 60 ns · Planned end 120 ns"],
    ["local_wait_cutoff", "wait", 160, "Start 120 ns · Observation cutoff 160 ns · Planned end 220 ns"],
    ["play_cutoff", "play", 60, "Start 20 ns · Observation cutoff 60 ns · Planned end 120 ns"],
  ]) {
    const dataset = datasets[key];
    const commands = dataset.ir.events.filter((event) => event.kind === kind && event.meta.observation_cutoff_ns === 160);
    expect(commands).toHaveLength(kind === "play" ? 2 : 1);
    await open(page, dataset);
    await expect(page.locator("#timeline-root > svg")).toHaveAttribute("data-time-max", String(end));
    for (const command of commands) {
      expect([command.t1.value, command.meta.planned_rt_end_ns]).toEqual([160, 220]);
      const {title, field} = await inspect(page, command);
      await expect(title).toContainText(expected);
      await expect(field("Time range")).toHaveText(expected);
    }
    if (key === "wait_cutoff") {
      const peer = dataset.ir.events.find((event) => event.kind === "wait" && event.sequencer_id === "peer");
      const {title, field} = await inspect(page, peer);
      await expect(title).toContainText("aligned time: 0 -> 4");
      await expect(field("Time range")).toHaveText("0 ns → 4 ns");
    }
  }
  for (const [key, titleText, inspectorText] of [
    ["invalid_plan", "20 -> 60", "20 ns → 60 ns"],
    ["pending", "Arm 20 ns → waiting for trigger (completion unresolved) · observation cutoff 20 ns"],
  ]) {
    const dataset = datasets[key];
    await open(page, dataset);
    const event = dataset.ir.events.find((event) => key === "pending" ? event.kind === "trigger_wait" : event.meta.planned_rt_end_ns != null);
    const {title, field} = await inspect(page, event);
    await expect(title).toContainText(titleText);
    await expect(field("Time range")).toHaveText(inspectorText || titleText);
  }
});

test("standalone clipped Q1 issues show the observed interval and planned end", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const datasets = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from copy import deepcopy", "from q1timeline.analysis.api import analyze_documents, analyze_text",
    "from q1timeline.render.html import render_html",
    "source = 'move 3,R0\\nnop\\nadd R0,1,R1\\nstop'",
    "def run(limit):",
    "    return analyze_text(source, sequencer_id='seq', execution_mode='simulation', simulation={'max_time_ns': limit})",
    "clipped = run(12)",
    "completed = run(20)",
    "shared = analyze_documents({'seq.q1asm': 'wait 40\\nmove 3,R0\\n' + 'nop\\n' * 22 + 'add R0,1,R1\\nstop',",
    "    'peer.q1asm': 'wait 4\\nstop'}, execution_mode='simulation', simulation={'startup_delay_ns': 100, 'max_time_ns': 104})",
    "inconsistent = deepcopy(clipped)",
    "for event in inconsistent['events']:",
    "    if event['kind'] == 'q1_issue' and event['meta']['op'] == 'add':",
    "        event['meta']['observation_cutoff_ns'] = 11",
    "print(json.dumps([{'ir': ir, 'html': render_html(ir, default_mode='debug')}",
    "    for ir in (clipped, shared, completed, inconsistent)]))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  for (const [index, [start, end, cutoff, planned, titleText, inspectorText]] of [
    [8, 12, 12, 20, "Issue 8 ns · Observation cutoff 12 ns · Planned end 20 ns"],
    [96, 104, 104, 108, "Issue -4 ns · Observation cutoff 4 ns · Planned end 8 ns"],
    [8, 20, undefined, undefined, "8 -> 20", "8 ns → 20 ns"],
    [8, 12, 11, 20, "8 -> 12", "8 ns → 12 ns"],
  ].entries()) {
    const dataset = datasets[index];
    const issue = dataset.ir.events.find((event) => event.kind === "q1_issue" && event.meta.op === "add");
    expect([issue.t0.value, issue.t1.value, issue.meta.observation_cutoff_ns, issue.meta.planned_q1_end_ns])
      .toEqual([start, end, cutoff, planned]);
    await open(page, dataset);
    const {title, field} = await inspect(page, issue);
    await expect(title).toContainText(titleText);
    await expect(field("Time range")).toHaveText(inspectorText || titleText);
    await expect(field("Q1 core")).toHaveText(inspectorText || titleText);
  }
});
