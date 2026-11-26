const { test, expect } = require("./helpers");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const { spawn } = require("node:child_process");
const readline = require("node:readline");

const repo = path.resolve(__dirname, "../../..");
const python = process.env.Q1LENS_BROWSER_PYTHON || "python";
const pythonEnv = { ...process.env, PYTHONPATH: path.join(repo, "src") };

function analyze(project, file) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  execFileSync(python, ["-m", "q1timeline", "analyze", "--project", project, "--no-render", "--out", file], { cwd: repo, env: pythonEnv });
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function writeProject(dir, programs, config) {
  fs.mkdirSync(dir, { recursive: true });
  const gauss = Array.from({ length: 40 }, (_, n) => Number(Math.exp(-(((n - 19.5) / 8) ** 2)).toFixed(5)));
  fs.writeFileSync(path.join(dir, "wave.json"), JSON.stringify({ waveforms: {
    gauss: { index: 0, data: gauss }, square: { index: 1, data: Array(24).fill(1) },
  } }));
  for (const [name, program] of Object.entries(programs)) fs.writeFileSync(path.join(dir, name), program);
  fs.writeFileSync(path.join(dir, "q1timeline.yml"), JSON.stringify(config));
  return path.join(dir, "q1timeline.yml");
}

// Stands in for the extension: one output session answers each viewport the rack asks for.
async function mountLive(page, ir) {
  const child = spawn(python, ["-u", "-m", "q1timeline.output.session"], { env: pythonEnv, stdio: ["pipe", "pipe", "inherit"] });
  const waiting = [];
  readline.createInterface({ input: child.stdout }).on("line", (line) => waiting.shift()(JSON.parse(line)));
  const ask = (payload) => new Promise((resolve) => { waiting.push(resolve); child.stdin.write(`${JSON.stringify(payload)}\n`); });
  const loaded = ask({ id: 0, command: "load", output: ir.output });
  const requests = [];
  await page.exposeFunction("sampleOutput", async (request) => {
    await loaded;
    requests.push(request);
    const reply = await ask({ id: request.token, command: "window", t0: request.t0, t1: request.t1, bins: request.bins, params: request.params });
    return { token: request.token, window: reply.window, error: reply.error };
  });
  await page.addInitScript(() => {
    window.requestOutputWindow = (request) => window.sampleOutput(request).then((result) => window.hardwarePreview.setOutputWindow(result));
  });
  await page.route("**/ir/01-digital.json", (route) => route.fulfill({ json: ir }));
  await page.goto("/?case=01-digital");
  return { requests, close: () => child.stdin.end() };
}

test("Outputs rack nests ports and sequencer paths under their module and selects a contributing RT event", async ({ page }, info) => {
  const ir = analyze("examples/symbolic-output/q1timeline.yml", info.outputPath("output.json"));
  await page.route("**/ir/01-digital.json", (route) => route.fulfill({ json: ir }));
  await page.goto("/?case=01-digital");
  const rack = page.locator(".hardware-rack");
  await expect(rack.locator(".rack-row.is-cluster strong")).toHaveText("Cluster");
  await expect(rack.locator(".rack-row.is-module strong")).toHaveText(["Pulse", "Mix"]);
  await expect(rack.locator(".rack-row.is-port strong")).toHaveText(["O1", "O2", "O1"]);
  await expect(rack.locator('[data-lane="port|2"]').getByRole("img")).toHaveAttribute("aria-label", "Mix O1 output, 0 ns to 140 ns");
  // drive.I and boost.Q add up past full scale on the QRM output.
  await expect(rack.locator('[data-rack-clips="2"]')).toHaveText("clips at full scale");
  await expect(rack.locator('[data-rack-clips="0"]')).toHaveText("");
  await expect(rack.locator('[data-lane="port|2"] .rack-clip')).not.toHaveCount(0);

  // The project sets G, N and T, so nothing is assumed and formulas keep the names.
  await expect(rack.locator(".rack-param output")).toHaveText(["28672", "3", "12"]);
  await expect(rack.locator(".rack-param.is-assumed")).toHaveCount(0);
  await expect(rack.locator('[data-rack="assumed"]')).toBeHidden();
  const probe = rack.locator('[data-lane="path|0|probe"]');
  await expect(probe.locator(".rack-repeat")).toHaveText("× N every 20 + T ns");
  await expect(probe.locator(".rack-repeat")).toHaveAttribute("title", /Repeats 3 times, 32 ns apart\nN = 3\nA count of 0 runs 2³² times/);
  await expect(probe.locator(".rack-tag").first()).toHaveText("G/32768 × wf0");
  const ramp = rack.locator('[data-lane="path|1|ramp"]');
  await expect(ramp.locator(".rack-repeat")).toHaveText("× 5 every 20 ns");
  await expect(ramp.locator(".rack-tag").first()).toHaveAttribute("title", /^0 ns to 20 ns\n\(4096 \+ 4096 \* k\)\/32768 × wf0$/);

  const interval = rack.locator('[data-lane="path|2|drive"] .rack-tag').nth(1);
  await expect(interval).toHaveAttribute("title", "4 ns to 24 ns\nG/32768 × wf0");
  const id = await interval.getAttribute("data-rack-event");
  await interval.click();
  expect(await page.evaluate(() => window.selectedEventId)).toBe(id);
  expect(ir.events.find((event) => event.id === id).source.file).toMatch(/drive\.q1asm$/);
  await expect(page.getByRole("spinbutton")).toHaveValue("4");
  await expect(rack).toBeVisible();
  const cursor = await rack.locator('[data-rack="cursor"]').boundingBox();
  const tag = await interval.boundingBox();
  expect(Math.abs(cursor.x - tag.x)).toBeLessThan(2);

  await page.getByRole("button", { name: "Hide what feeds Mix O1", exact: true }).click();
  await expect(rack.locator('[data-lane="path|2|drive"]')).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Show what feeds Mix O1", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "Inspect sequencer probe", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "Hardware hierarchy" }).getByRole("button")).toHaveText(["Outputs", "Pulse", "probe"]);
  // The module crumb returns to the rack with that module open.
  await page.getByRole("navigation", { name: "Hardware hierarchy" }).getByRole("button", { name: "Pulse", exact: true }).click();
  await expect(page.getByRole("button", { name: "Hide Pulse", exact: true })).toBeVisible();
  // One tab stop per lane; the arrow keys walk its intervals.
  const first = rack.locator('[data-lane="path|0|probe"] .rack-tag').first();
  await first.focus();
  await page.keyboard.press("ArrowRight");
  await expect(rack.locator('[data-lane="path|0|probe"] .rack-tag').nth(1)).toBeFocused();
  expect(await page.evaluate(() => window.hardwarePreview.getState().rack.collapsed)).toEqual(["p|Mix|out0"]);
});

test("Unset parameters are drawn with assumed values that the rack names, and a typed value resamples the output", async ({ page }, info) => {
  const project = writeProject(info.outputPath("assumed"), {
    "drive.q1asm": "move $N,R0\nnop\nshot: set_awg_gain $G,$G\nplay 0,0,40\nwait $T\nloop R0,@shot\nset_awg_gain 8000,8000\nplay 1,1,24\nwait 40\nstop\n",
  }, {
    sequencers: [{ id: "drive", file: "drive.q1asm", sequence_json: "wave.json" }],
    modules: { control: { type: "QCM", cluster: "cluster0", channel_map: { drive: { out0: "I" } } } },
    alignment: { mode: "none" },
  });
  const live = await mountLive(page, analyze(project, info.outputPath("assumed.json")));
  try {
    const rack = page.locator(".hardware-rack");
    await expect(rack.locator(".rack-row.is-cluster strong")).toHaveText("cluster0");
    await expect(rack.locator('[data-rack="assumed"]')).toHaveText("G, N and T have no value in this project. The rack draws them with assumed values, marked in amber. Type a value to try another.");
    await expect(rack.locator(".rack-param.is-assumed .rack-param-source")).toHaveText(["assumed", "assumed", "assumed"]);
    await expect(rack.locator('[data-rack-uses="drive"]')).toHaveText("assumes G, N, T");
    await expect(rack.locator(".rack-repeat.is-assumed")).toHaveText("× N every 40 + T ns");
    await expect(rack.locator(".rack-tag.is-assumed").first()).toHaveAttribute("title", /Drawn with assumed G$/);
    // Three shots of 40 + 100 ns, then 24 + 40 ns: 484 ns before the 3 % margin.
    await expect(rack.locator('[data-rack="range"]')).toHaveText("0 ns to 499 ns");
    expect(live.requests).toHaveLength(0);

    const count = page.getByLabel("N, assumed");
    await count.fill("6");
    await count.press("Enter");
    await expect(rack.locator(".rack-param.is-trial")).toContainText("trying");
    await expect(rack.locator('[data-rack="assumed"]')).toHaveText(/^G and T have no value in this project\./);
    await expect(rack.locator('[data-rack="range"]')).toHaveText("0 ns to 931 ns");
    await expect(rack.locator('[data-rack-uses="drive"]')).toHaveText("assumes G, T");
    expect(live.requests[0].params).toEqual({ N: 6 });

    const wait = page.getByLabel("T, assumed");
    await wait.fill("2");
    await wait.press("Enter");
    await expect(rack.locator('[data-lane="path|0|drive"] .rack-reason')).toHaveText("T = 2 is outside [4, 65535]");
    await page.getByRole("button", { name: "Use project values", exact: true }).click();
    await expect(rack.locator(".rack-param.is-assumed")).toHaveCount(3);
    await expect(rack.locator('[data-lane="path|0|drive"] .rack-reason')).toHaveCount(0);
  } finally {
    live.close();
  }
});

test("A long looped output shows the range it covers, then every sample once zoomed in", async ({ page }, info) => {
  const project = writeProject(info.outputPath("long"), {
    "shots.q1asm": "move 20000,R0\nnop\nshot: set_awg_gain 24000,24000\nplay 0,0,40\nwait 960\nloop R0,@shot\nstop\n",
  }, {
    sequencers: [{ id: "shots", file: "shots.q1asm", sequence_json: "wave.json" }],
    modules: { control: { type: "QCM", channel_map: { shots: { out0: "I" } } } },
    alignment: { mode: "none" },
  });
  const ir = analyze(project, info.outputPath("long.json"));
  expect(ir.output.overview.exact).toBe(false);
  // One exact bound per column: the pulse peak is reached in every 28 µs column.
  expect(new Set(ir.output.overview.ports[0].traces.sum.max.slice(0, -1))).toEqual(new Set([0.72957]));
  const live = await mountLive(page, ir);
  try {
    const rack = page.locator(".hardware-rack");
    const note = rack.locator('[data-rack="note"]');
    await expect(note).toHaveText(/each column shows the range the output covers there/);
    await expect(rack.locator('[data-rack="range"]')).toHaveText("0 ns to 20.6 ms");
    await expect(rack.locator(".rack-repeat")).toHaveText("× 20000 every 1000 ns, showing k = 0");
    await rack.locator('[data-rack="body"]').focus();
    for (let step = 0; step < 14; step++) await page.keyboard.press("+");
    await expect(note).toBeHidden();
    await expect(rack.locator('[data-rack="range"]')).toHaveText(/^10\.\d+ ms to 10\.\d+ ms$/);
    await expect(rack.locator(".rack-repeat")).toHaveText("× 20000 every 1000 ns");
    await expect(rack.locator('.rack-tag[title*="0.7324 × wf0"]').first()).toBeVisible();
    const last = live.requests.at(-1);
    expect(last.t1 - last.t0).toBeLessThan(2000);
    await page.keyboard.press("Home");
    await expect(note).toBeVisible();
    await expect(rack.locator('[data-rack="range"]')).toHaveText("0 ns to 20.6 ms");
  } finally {
    live.close();
  }
});

for (const aligned of [true, false]) {
  test(`Counterexample replay shows the ${aligned ? "negative aligned" : "post-stop waveform"} sample`, async ({ page }, info) => {
    const repo = path.resolve(__dirname, "../../..");
    const dir = info.outputPath("project");
    fs.mkdirSync(dir, { recursive: true });
    const seqs = aligned ? ["drive", "peer"] : ["drive"];
    for (const name of seqs) {
      const program = aligned
        ? `;@assert no_clip\nwait ${name === "drive" ? 16 : 4}\nset_awg_gain 24576,0\nplay 0,0,4\nwait_sync 4\nstop`
        : ";@assert peak(out0) <= .25\nplay 0,0,4\nstop";
      fs.writeFileSync(path.join(dir, name + ".q1asm"), program);
    }
    const data = aligned ? [1, 1, 1, 1] : [...Array(32).fill(0), 0.5];
    fs.writeFileSync(path.join(dir, "wave.json"), JSON.stringify({ waveforms: { pulse: { index: 0, data } } }));
    fs.writeFileSync(path.join(dir, "q1timeline.yml"), JSON.stringify({
      sequencers: seqs.map((id) => ({ id, file: id + ".q1asm", sequence_json: "wave.json" })),
      alignment: { mode: aligned ? "first_wait_sync" : "none" },
      modules: { card: { type: "QCM", channel_map: Object.fromEntries(seqs.map((id) => [id, { out0: "I" }])) } },
    }));
    const report = execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c",
      "import json;from q1timeline.verification import verify_file;print(json.dumps(verify_file('drive.q1asm',project='q1timeline.yml',include_replay=True)['checks'][0]))",
    ], { cwd: dir, env: { ...process.env, PYTHONPATH: path.join(repo, "src") }, encoding: "utf8" });
    const check = JSON.parse(report);
    const time = check.counterexample.time_ns;
    if (aligned) expect(time).toBeLessThan(0);
    else expect(time).toBe(32);
    await page.setContent('<div id="finding"></div><div id="inputs"></div><div id="timeline-root"></div><div id="occurrence"></div>' +
      '<button id="source"></button><button id="focus"></button><button id="full"></button><div id="hardware"></div><script type="application/json" id="witness"></script>');
    await page.evaluate((finding) => {
      window.acquireVsCodeApi = () => ({ postMessage() {} });
      document.getElementById("witness").textContent = JSON.stringify(finding);
    }, check);
    for (const asset of ["shared/timeline/instructionColors", "shared/timeline/renderer", "q1timeline/media/timelineAdapter",
        "q1timeline/media/hardwareState", "q1timeline/media/hardwareView", "q1timeline/media/verificationReplay"]) {
      await page.addScriptTag({ path: path.join(repo, "vscode-extension/src", asset + ".js") });
    }
    for (const button of ["focus", "full"]) {
      await page.locator("#" + button).click();
      await expect(page.getByRole("spinbutton")).toHaveValue(String(time));
      await expect(page.locator("#occurrence")).toContainText(time + " ns");
      const ticks = await page.locator(".ruler .tick").allTextContents();
      expect(parseInt(ticks[0], 10)).toBeLessThanOrEqual(time);
      expect(parseInt(ticks.at(-1), 10)).toBeGreaterThan(time);
    }
  });
}

test("A selected sequencer keeps its lane, and the state table reads the output from it", async ({ page }, info) => {
  const ir = analyze("examples/symbolic-output/q1timeline.yml", info.outputPath("output.json"));
  await page.route("**/ir/01-digital.json", (route) => route.fulfill({ json: ir }));
  await page.goto("/?case=01-digital");
  const hardware = page.locator("#hardware");
  const rack = page.locator(".hardware-rack");
  const time = page.getByRole("spinbutton", { name: "Selected time in nanoseconds" });
  await page.getByRole("button", { name: "Inspect sequencer drive", exact: true }).first().click();

  // The rack narrows to this sequencer: a ruler and one lane with both paths.
  await expect(rack).toHaveClass(/is-focus/);
  await expect(rack.locator(".rack-row")).toHaveCount(2);
  const lane = rack.locator('[data-lane="seq|drive"]');
  await expect(lane.getByRole("img")).toHaveAttribute("aria-label", "drive path IQ, 0 ns to 140 ns");
  await expect(lane.locator(".rack-tag").nth(1)).toHaveText("I G/32768 × wf0, Q 0");
  await expect(page.locator('[data-control="scrubber"]')).toBeHidden();

  // A program that neither acquires nor ran in simulation has no rows or tabs for those parts.
  await expect(hardware.locator(".hardware-row:visible .hardware-row-name")).toHaveText(["Q1", "RT", "Output"]);
  await expect(page.getByRole("tab")).toHaveText(["Registers", "Parameters"]);
  await expect(page.locator('[data-value="absent"]')).toHaveText("This program has no acquisitions. RT queue depth and signals need a simulation run.");

  // The output row states the formula under the cursor and what each path holds there.
  const output = page.locator('[data-block-value="awg"]');
  await time.fill("8");
  await time.press("Enter");
  await expect(output.locator(".hardware-path").first()).toHaveText("I G/32768 × wf0 = 0.875");
  await expect(output.locator(".hardware-path").nth(1)).toHaveText("Q 0");
  await expect(page.locator('[data-value="output-detail"]')).toContainText("In effect: awg_gain 28672, 0");

  // Pressing on the lane places the cursor; dragging moves it.
  const box = await lane.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + 20);
  await page.mouse.down();
  const pressed = Number(await time.inputValue());
  expect(pressed).toBeGreaterThan(60);
  expect(pressed).toBeLessThan(80);
  await page.mouse.move(box.x + box.width * 0.75, box.y + 20);
  expect(Number(await time.inputValue())).toBeGreaterThan(100);
  await page.mouse.up();

  // A row links to its own source line.
  await time.fill("8");
  await time.press("Enter");
  const link = page.locator('[data-row-source="rt"]');
  await expect(link).toHaveText(/^drive\.q1asm:\d+$/);
  await link.click();
  expect((await page.evaluate(() => window.revealedSource)).file).toMatch(/drive\.q1asm$/);

  // Back on Outputs the rack shows every port again.
  await page.getByRole("navigation", { name: "Hardware hierarchy" }).getByRole("button", { name: "Outputs", exact: true }).click();
  await expect(rack).not.toHaveClass(/is-focus/);
  await expect(rack.locator(".rack-row.is-port strong")).toHaveText(["O1", "O2", "O1"]);
});

test("Limits of the whole analysis fold behind a count and stay open while the cursor moves", async ({ page }, info) => {
  const project = writeProject(info.outputPath("limits"), {
    "drive.q1asm": "move $N,R0\nnop\nshot: set_awg_gain $G,$G\nplay 0,0,40\nwait $T\nloop R0,@shot\nstop\n",
  }, { sequencers: [{ id: "drive", file: "drive.q1asm", sequence_json: "wave.json" }] });
  const ir = analyze(project, info.outputPath("limits.json"));
  await page.route("**/ir/01-digital.json", (route) => route.fulfill({ json: ir }));
  await page.goto("/?case=01-digital");
  await page.getByRole("button", { name: "Inspect sequencer drive", exact: true }).first().click();
  const limits = page.locator('[data-value="warnings"] details');
  await expect(limits.locator("summary")).toHaveText(/^[2-9] limits of this analysis$/);
  await expect(limits.locator("li").first()).toBeHidden();
  await limits.locator("summary").click();
  await expect(limits.locator("li").first()).toBeVisible();
  await page.getByRole("button", { name: "Next state change" }).click();
  await expect(limits.locator("li").first()).toBeVisible();
});
