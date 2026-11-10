const { test, expect, open, at, value, register, tab, analysisDetails } = require("./helpers");
const { createHash } = require("node:crypto");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

test("replay generation can retry after an analysis failure or completion without a remount", async ({ page }) => {
  await page.goto("about:blank");
  await page.setContent('<!doctype html><html><head></head><body><main id="timeline-root"></main></body></html>');
  await page.evaluate(() => {
    let saved = { hardwareState: { level: "detail", sequencerId: "seq", replayOpen: true } };
    window.messages = [];
    window.acquireVsCodeApi = () => ({
      getState: () => saved, setState: (state) => { saved = state; },
      postMessage: (message) => window.messages.push(message),
    });
    const data = document.createElement("script");
    data.id = "timeline-ir";
    data.type = "application/json";
    data.textContent = JSON.stringify({
      sequencers: [{ id: "seq" }], register_history: [],
      execution: { seq: { mode: "analysis" } },
      events: [{ id: "issue", kind: "q1_issue", sequencer_id: "seq", lane: "q1.issue",
        t0: 0, t1: 4, meta: {}, source: { file: "seq.q1asm", line: 1 } }],
    });
    document.body.append(data);
  });
  const assets = path.resolve(__dirname, "../../src");
  for (const file of ["shared/timeline/renderer.css", "q1timeline/media/timeline.css", "q1timeline/media/hardwareView.css"]) {
    await page.addStyleTag({ path: path.join(assets, file) });
  }
  for (const file of ["shared/timeline/instructionColors.js", "shared/timeline/renderer.js", "q1timeline/media/timelineAdapter.js",
    "q1timeline/media/hardwareState.js", "q1timeline/media/hardwareView.js", "q1timeline/media/timeline.js"]) {
    await page.addScriptTag({ path: path.join(assets, file) });
  }
  await analysisDetails(page);
  const prepare = page.locator('[data-action="replay-prepare"]');
  await expect(prepare).toBeVisible();
  for (const terminalStatus of ["error", "idle"]) {
    await prepare.click();
    await expect(prepare).toHaveText("Generating execution trace…");
    await expect(prepare).toBeDisabled();
    for (const status of ["analyzing", "stale", "rendering"]) {
      await page.evaluate((status) => window.dispatchEvent(new MessageEvent("message", {
        data: { type: "setAnalysisStatus", status },
      })), status);
      await expect(prepare).toBeDisabled();
    }
    await page.evaluate((status) => window.dispatchEvent(new MessageEvent("message", {
      data: { type: "setAnalysisStatus", status },
    })), terminalStatus);
    await expect(prepare).toHaveText("Generate execution trace");
    await expect(prepare).toBeEnabled();
  }
  await prepare.click();
  expect(await page.evaluate(() => window.messages.filter((message) => message.type === "prepareExecutionReplay").length)).toBe(3);
});

test("removed comparison events do not create hardware state or move its cursor", async ({ page }) => {
  const source = { file: "seq.q1asm", line: 1, raw: "upd_param 4" };
  const event = (id, kind, t0, t1, meta) => ({
    id, kind, t0, t1, sequencer_id: "seq", source, meta,
    lane: kind === "q1_issue" ? "q1.issue" : "rt.control",
  });
  await page.route("**/ir/01-digital.json", (route) => route.fulfill({ json: {
    sequencers: [{ id: "seq", module: "QCM0" }], register_history: [],
    execution: { seq: { mode: "concrete", status: "completed" } },
    events: [
      event("current-issue", "q1_issue", 0, 4, { execution_index: 0 }),
      event("current-rt", "upd_param", 0, 4, { rt_packet_id: "current", applied_state: { frequency: 120 } }),
      event("current-stop", "stop", 8, 8, {}),
      event("removed-issue", "q1_issue", 200, 204, { execution_index: 0, diff_status: "removed" }),
      event("removed-acquire", "acquire", 200, 204, {
        diff_status: "removed", rt_packet_id: "old", acquisition_index: 0, bin_index: 0,
        applied_state: { frequency: 700 },
      }),
      { ...event("removed-sequencer", "q1_issue", 300, 304, { diff_status: "removed" }), sequencer_id: "deleted" },
    ],
  } }));
  await page.goto("/?case=01-digital");
  await expect(page.locator("[data-module-card]")).toHaveCount(1);
  await page.getByRole("button", { name: "Open QCM0: 1 sequencer", exact: true }).click();
  await page.getByRole("button", { name: "Inspect sequencer seq", exact: true }).click();
  await at(page, 4);
  await page.evaluate(() => window.hardwarePreview.selectEvent("removed-acquire"));
  await expect(page.getByRole("spinbutton")).toHaveValue("4");
  await page.getByRole("button", { name: "Execution replay", exact: true }).click();
  await expect(page.locator('[data-action="replay-run"]')).toBeVisible();
  await expect(value(page, "replay-progress")).toHaveText("1 / 1 instructions");
});

test("refresh restores the inspected register together with its cursor and number format", async ({ page }) => {
  await open(page, "01-digital", "QCM0", "counter");
  await at(page, 32);
  await page.evaluate(() => {
    const initialState = window.hardwarePreview.getState();
    window.hardwarePreview.destroy();
    window.hardwarePreview = window.q1lensHardwareView.mount(document.getElementById("hardware"), window.demoIr, {
      initialState, onStateChange(state) { window.persistedHardwareState = state; },
    });
  });
  await page.locator('[data-register="R1"]').click();
  await page.getByRole("combobox", { name: "Register number format" }).selectOption("hex");
  await page.evaluate(() => {
    window.hardwarePreview.destroy();
    window.hardwarePreview = window.q1lensHardwareView.mount(document.getElementById("hardware"), window.demoIr, {
      initialState: window.persistedHardwareState,
      onSourceClick(source) { window.revealedSource = source; },
    });
  });
  await expect(page.getByRole("spinbutton")).toHaveValue("32");
  await expect(page.getByRole("combobox", { name: "Register number format" })).toHaveValue("hex");
  await expect(page.locator('[data-register="R1"]')).toHaveAttribute("aria-pressed", "true");
  await expect(value(page, "register-detail")).toContainText("R1 = 0x00000001");
  expect(await page.evaluate(() => window.hardwarePreview.getState().selectedRegister)).toBe("R1");
  await page.getByRole("button", { name: "Open source", exact: true }).click();
  expect((await page.evaluate(() => window.revealedSource)).line).toBe(6);
});

test("refresh restores the selected bin history and its page", async ({ page }) => {
  await page.route("**/ir/01-digital.json", (route) => route.fulfill({ json: {
    sequencers: [{ id: "seq", module: "QRM0" }], register_history: [],
    events: Array.from({ length: 17 }, (_, bin) => ({
      id: `acquire-${bin}`, kind: "acquire", sequencer_id: "seq", lane: "rt.acquire",
      t0: bin * 4, t1: (bin + 1) * 4,
      source: { file: "bins.q1asm", line: bin + 1, raw: `acquire 0,${bin},4` },
      meta: { acquisition_index: 0, bin_index: bin, rt_packet_id: `packet-${bin}` },
    })),
  } }));
  await open(page, "01-digital", "QRM0", "seq");
  await at(page, 68);
  await page.evaluate(() => {
    const initialState = window.hardwarePreview.getState();
    window.hardwarePreview.destroy();
    window.hardwarePreview = window.q1lensHardwareView.mount(document.getElementById("hardware"), window.demoIr, {
      initialState, onStateChange(state) { window.persistedBinState = state; },
    });
  });
  await tab(page, "Bins");
  await page.locator('[data-action="bins-next"]').click();
  await page.locator('[data-bin="0:16"]').click();
  await expect(value(page, "bin-history")).toContainText("Acquisition 0 · bin 16");
  await page.evaluate(() => {
    window.hardwarePreview.destroy();
    window.hardwarePreview = window.q1lensHardwareView.mount(document.getElementById("hardware"), window.demoIr, {
      initialState: window.persistedBinState,
      onSourceClick(source) { window.revealedBinSource = source; },
    });
  });
  await expect(value(page, "bin-count")).toHaveText("17–17 of 17 targets");
  await expect(value(page, "bin-history")).toContainText("Acquisition 0 · bin 16");
  await page.getByRole("button", { name: "Open source", exact: true }).click();
  expect((await page.evaluate(() => window.revealedBinSource)).line).toBe(17);
  await at(page, 67);
  await expect(value(page, "bin-count")).toHaveText("17–17 of 17 targets");
  await expect(value(page, "bin-history")).toContainText("Acquisition 0 · bin 16");
  await value(page, "bins").getByRole("button", { name: "64 ns", exact: true }).click();
  await expect(page.getByRole("spinbutton")).toHaveValue("64");
  await expect(value(page, "bin-count")).toHaveText("17–17 of 17 targets");
  await expect(value(page, "bin-history")).toContainText("Acquisition 0 · bin 16");
  await at(page, 63);
  await expect(value(page, "bin-count")).toHaveText("1–16 of 16 targets");
  await expect(value(page, "bin-history")).toContainText("Select a bin number");
  await page.evaluate(() => {
    const initialState = window.hardwarePreview.getState();
    window.hardwarePreview.destroy();
    const shorterIr = { ...window.demoIr, events: window.demoIr.events.slice(0, 1) };
    window.hardwarePreview = window.q1lensHardwareView.mount(document.getElementById("hardware"), shorterIr, { initialState });
  });
  await expect(value(page, "bin-count")).toHaveText("1–1 of 1 targets");
  await expect(value(page, "bin-history")).toContainText("Select a bin number");
  await expect(page.locator('[data-action="bins-previous"]')).toBeDisabled();
});

test("bin paging resets consistently when choosing a different sequencer", async ({ page }) => {
  await page.route("**/ir/01-digital.json", (route) => route.fulfill({ json: {
    sequencers: ["first", "second"].map((id) => ({ id, module: "QRM0" })), register_history: [],
    events: ["first", "second"].flatMap((sequencer_id) => Array.from({ length: 17 }, (_, bin) => ({
      id: `${sequencer_id}-${bin}`, kind: "acquire", sequencer_id, lane: "rt.acquire",
      t0: bin * 4, t1: (bin + 1) * 4,
      source: { file: `${sequencer_id}.q1asm`, line: bin + 1 },
      meta: { acquisition_index: 0, bin_index: bin, rt_packet_id: `${sequencer_id}-${bin}` },
    }))),
  } }));
  await page.goto("/?case=01-digital");
  await page.getByRole("button", { name: "Open QRM0: 2 sequencers", exact: true }).click();
  await page.getByRole("button", { name: "Inspect sequencer first", exact: true }).click();
  await at(page, 68);
  await tab(page, "Bins");
  await page.locator('[data-action="bins-next"]').click();
  await expect(value(page, "bin-count")).toHaveText("17–17 of 17 targets");
  await page.getByRole("button", { name: "Zoom out one hardware level", exact: true }).click();
  await page.getByRole("button", { name: "Inspect sequencer second", exact: true }).click();
  await expect(value(page, "bin-count")).toHaveText("1–16 of 17 targets");
  await page.locator('[data-action="bins-next"]').click();
  await page.evaluate(() => window.hardwarePreview.selectEvent("first-16"));
  await expect(value(page, "bin-count")).toHaveText("1–16 of 17 targets");
  await expect(page.getByRole("combobox", { name: "Hardware sequencer" })).toHaveValue("first");
});

test("rewinding or refreshing before a selected bin exists cannot open another bin's source", async ({ page }) => {
  await page.route("**/ir/01-digital.json", (route) => route.fulfill({ json: {
    sequencers: [{ id: "seq", module: "QRM0" }], register_history: [],
    events: [0, 1].map((bin) => ({
      id: `acquire-${bin}`, kind: "acquire", sequencer_id: "seq", lane: "rt.acquire",
      t0: bin * 4, t1: (bin + 1) * 4, source: { file: "bins.q1asm", line: bin + 1 },
      meta: { acquisition_index: 0, bin_index: bin, rt_packet_id: `packet-${bin}` },
    })),
  } }));
  await open(page, "01-digital", "QRM0", "seq");
  await at(page, 8);
  await tab(page, "Bins");
  await page.locator('[data-bin="0:1"]').click();
  const openSource = page.getByRole("button", { name: "Open source", exact: true });
  await openSource.click();
  expect((await page.evaluate(() => window.revealedSource)).line).toBe(2);
  await at(page, 3);
  await expect(value(page, "bin-history")).toContainText("Select a bin number");
  await expect(openSource).toBeDisabled();
  await at(page, 8);
  await expect(value(page, "bin-history")).toContainText("Acquisition 0 · bin 1");
  await openSource.click();
  expect((await page.evaluate(() => window.revealedSource)).line).toBe(2);
  await page.evaluate(() => {
    const initialState = window.hardwarePreview.getState();
    window.hardwarePreview.destroy();
    window.hardwarePreview = window.q1lensHardwareView.mount(document.getElementById("hardware"), {
      ...window.demoIr, events: window.demoIr.events.slice(0, 1),
    }, { initialState, onSourceClick(source) { window.revealedSource = source; } });
  });
  await expect(value(page, "bin-history")).toContainText("Select a bin number");
  await expect(openSource).toBeDisabled();
});

test("missing signal samples leave chart gaps and real zero samples remain visible", async ({ page }) => {
  await page.route("**/ir/01-digital.json", (route) => route.fulfill({ json: {
    sequencers: [{ id: "seq", module: "QCM0" }],
    events: [{ id: "wait", kind: "wait", sequencer_id: "seq", t0: 0, t1: 9, meta: {} }],
    register_history: [],
    simulation: { seq: { signals: { traces: [
      { stage: "adc", path: "i", t0: 0, dt_ns: 1, samples: [0.5, 0.5, null, null, null, null, null, 0, 0] },
      { stage: "adc", path: "q", t0: 0, dt_ns: 1, samples: Array(9).fill(0) },
      { stage: "adc", path: "real", t0: 0, dt_ns: 1, samples: [null, 0, null, 0.5, null, null, null, null, null] },
    ] } } },
  } }));
  await open(page, "01-digital", "QCM0", "seq");
  await tab(page, "Simulation");
  const paths = page.locator('.hardware-signal-path[data-path="i"]');
  await expect(paths).toHaveCount(2);
  await expect(paths.nth(0)).toHaveAttribute("points", "0.00,10.00 66.67,10.00");
  await expect(paths.nth(1)).toHaveAttribute("points", "466.67,150.00 533.33,150.00");
  await expect(page.locator('.hardware-signal-path[data-path="q"]')).toHaveCount(1);
  const isolated = page.locator('circle.hardware-signal-path[data-path="real"]');
  await expect(isolated).toHaveCount(2);
  await expect(isolated.nth(0)).toHaveAttribute("cx", "66.67");
  await expect(isolated.nth(0)).toHaveAttribute("cy", "150.00");
  await expect(isolated.nth(0)).toHaveAttribute("r", "2");
  await expect(isolated.nth(1)).toHaveAttribute("cx", "200.00");
  await expect(isolated.nth(1)).toHaveAttribute("cy", "10.00");
  await at(page, 2);
  await expect(value(page, "signal-values")).toContainText("I: unknown");
  await at(page, 7);
  await expect(value(page, "signal-values")).toContainText("I: 0");
});

test("fixtures are fresh CLI outputs and browser assets match current production sources", async ({ request }) => {
  const manifest = await (await request.get("/manifest.json")).json();
  expect(manifest.cases.map((item) => item.name)).toEqual(["01-digital", "02-communication", "03-signals", "04-underflow", "05-supplied-input", "cursor-two-shot-feedback", "cursor-deadband-hold"]);
  expect(manifest.cases.map((item) => item.exit_code)).toEqual([0, 0, 0, 2, 0, 0, 0]);
  expect(Date.now() - Date.parse(manifest.created_at)).toBeLessThan(120000);
  for (const [name, item] of Object.entries(manifest.assets)) {
    const response = await request.get(`/assets/${name}`);
    expect(response.ok()).toBeTruthy();
    const expected = fs.readFileSync(path.resolve(__dirname, "../../..", item.source));
    expect(await response.body()).toEqual(expected);
    expect(createHash("sha256").update(expected).digest("hex")).toBe(item.sha256);
  }
  expect(manifest.cases.find((item) => item.name === "03-signals").noise_seeds.drive).toBe(1234);
  expect(manifest.cases.find((item) => item.name === "05-supplied-input").noise_seeds.readout).toBe(17);
});

test("digital register readiness, occurrence navigation, queue, latched values and responsive controls", async ({ page }) => {
  await open(page, "01-digital", "QCM0", "counter");
  await expect(page.getByRole("combobox", { name: "Hardware sequencer" })).toHaveValue("counter");
  await tab(page, "Registers");
  await at(page, 31);
  await expect(register(page, "R1")).toHaveText("0");
  await at(page, 32);
  await expect(register(page, "R1")).toHaveText("1");
  await page.locator('[data-register="R1"]').click();
  await expect(value(page, "register-detail")).toContainText("R1 = 1");
  await page.getByRole("button", { name: "Open source", exact: true }).click();
  const source = await page.evaluate(() => window.revealedSource);
  expect(source.line).toBe(6);
  expect(source.file.replaceAll("\\", "/")).toMatch(/\/counter\.q1asm$/);
  const occurrence = await page.evaluate(() => window.selectedEventId);
  await page.getByRole("button", { name: "Next change ›", exact: true }).click();
  await expect(page.getByRole("spinbutton")).toHaveValue("80");
  await expect(register(page, "R1")).toHaveText("2");
  expect(await page.evaluate(() => window.selectedEventId)).not.toBe(occurrence);
  await page.getByRole("button", { name: "‹ Previous change", exact: true }).click();
  await expect(page.getByRole("spinbutton")).toHaveValue("32");
  await page.getByRole("combobox", { name: "Register number format" }).selectOption("hex");
  await expect(register(page, "R1")).toHaveText("0x00000001");
  await page.getByRole("checkbox", { name: "All 64" }).check();
  await expect(page.locator("[data-register]:visible")).toHaveCount(64);
  await page.getByRole("checkbox", { name: "All 64" }).uncheck();
  await page.getByRole("combobox", { name: "Register number format" }).selectOption("decimal");
  await at(page, 100);
  await tab(page, "Simulation");
  await expect(value(page, "simulation-queue")).toContainText("2 / 2 packets · Q1 stalled: queue full (84–200 ns)");
  await at(page, 250);
  await tab(page, "Parameters");
  const gain = value(page, "parameters").locator("tr").filter({ has: page.getByRole("rowheader", { name: "awg_gain", exact: true }) });
  await expect(gain.locator("td").nth(0).getByRole("button")).toHaveText("3, 3");
  await expect(gain.locator("td").nth(1).getByRole("button")).toHaveText("1, 1");
  await at(page, 252);
  await expect(value(page, "instruction-detail")).toContainText("ZF=1 NF=0 CF=0 OF=0");
  await at(page, 404);
  await expect(gain.locator("td").nth(1).getByRole("button")).toHaveText("3, 3");
  await page.getByRole("slider").fill("32");
  await expect(page.getByRole("spinbutton")).toHaveValue("32");
  await tab(page, "Registers");
  await page.getByRole("tab", { name: "Registers", exact: true }).press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Parameters", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.evaluate(() => window.hardwarePreview.setTimeBasis("aligned"));
  await expect(page.getByRole("spinbutton")).toHaveValue("32");
  await expect(value(page, "basis")).toContainText("Aligned time");
  await page.evaluate(() => window.hardwarePreview.setTimeBasis("local"));
  await page.setViewportSize({ width: 480, height: 700 });
  await tab(page, "Registers");
  await page.locator('[data-register="R1"]').click();
  await page.getByRole("button", { name: "Open source", exact: true }).click();
  expect((await page.evaluate(() => window.revealedSource)).line).toBe(6);
});

test("communication input provenance, cross-sequencer navigation and trigger boundary", async ({ page }) => {
  await open(page, "02-communication", "QRM0", "receiver");
  await at(page, 227);
  await tab(page, "Simulation");
  await expect(value(page, "simulation-queue")).toContainText("Q1 stalled: feedback input (8–228 ns)");
  await at(page, 228);
  await expect(value(page, "communications")).toContainText("228 ns · feedback receive");
  await expect(value(page, "communications")).toContainText("id 16 · value 7");
  await tab(page, "Registers");
  await at(page, 235);
  await expect(register(page, "R0")).toHaveText("?");
  await at(page, 236);
  await expect(register(page, "R0")).toHaveText("7");
  await page.locator('[data-register="R0"]').click();
  await page.getByRole("button", { name: "Show received input that supplied this value", exact: true }).click();
  await expect(page.getByRole("spinbutton")).toHaveValue("228");
  await tab(page, "Simulation");
  await page.getByRole("button", { name: "Show sending event", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Hardware sequencer" })).toHaveValue("sender");
  await expect(page.getByRole("spinbutton")).toHaveValue("204");
  await page.getByRole("combobox", { name: "Hardware module" }).selectOption("QRM0");
  await page.getByRole("button", { name: "Inspect sequencer receiver", exact: true }).click();
  await at(page, 252);
  await tab(page, "Registers");
  await expect(register(page, "R1")).toHaveText("8");
  await at(page, 1099);
  await expect(value(page, "rt-event")).toContainText("waiting");
  await at(page, 1100);
  await tab(page, "Simulation");
  await expect(value(page, "communications")).toContainText("1100 ns · trigger receive");
  await expect(value(page, "communications")).toContainText("address 1");
});

test("physical stage values, finite SVG and acquisition visibility while rewinding", async ({ page }) => {
  await open(page, "03-signals", "QCM_RF0", "drive");
  await tab(page, "Simulation");
  await at(page, 204);
  for (const [stage, expected] of [["waveform", 1], ["gain_offset", 0.5], ["fir", 0.25], ["filtered", 0.125], ["dac", 0.125]]) {
    await page.getByRole("combobox", { name: "Signal stage" }).selectOption(stage);
    await expect(value(page, "signal-values").locator("strong").first()).toHaveText(`I: ${expected}`);
    await expect(page.locator(".hardware-signal-chart polyline")).toHaveCount(2);
    expect(await page.locator(".hardware-signal-chart").evaluate((svg) => /NaN|Infinity/.test(svg.outerHTML))).toBe(false);
  }
  await page.getByRole("combobox", { name: "Signal stage" }).selectOption("filtered");
  for (const [time, expected] of [[205, .3125], [206, .40625], [207, .453125], [208, .4765625]]) {
    await at(page, time);
    await expect(value(page, "signal-values").locator("strong").first()).toHaveText(`I: ${expected}`);
  }
  await page.getByRole("combobox", { name: "Hardware module" }).selectOption("QRM_RF0");
  await page.getByRole("button", { name: "Inspect sequencer readout", exact: true }).click();
  await tab(page, "Bins");
  await at(page, 271);
  await expect(value(page, "bins")).toContainText("unknown");
  await expect(value(page, "bins")).not.toContainText("31.953125");
  await at(page, 272);
  await expect(value(page, "bins")).toContainText("I 31.953125 · Q 0");
  await expect(value(page, "bins")).toContainText("threshold 1");
  await expect(value(page, "bins").getByRole("button", {name: /^I 31\.953125/})).toHaveAttribute("title", /^Ready at 272 ns/);
  await page.locator('[data-bin="0:0"]').click();
  await expect(value(page, "bin-history")).toContainText("Simulated loopback");
  await at(page, 271);
  await expect(value(page, "bins")).not.toContainText("31.953125");
  await page.getByRole("combobox", { name: "Hardware module" }).selectOption("QCM0");
  await page.getByRole("button", { name: "Inspect sequencer controller", exact: true }).click();
  await tab(page, "Registers");
  await at(page, 299);
  await expect(register(page, "R0")).toHaveText("?");
  await at(page, 300);
  await expect(register(page, "R0")).toHaveText("3");
  await at(page, 316);
  await expect(register(page, "R1")).toHaveText("1");
});

test("Hardware shows the latest digital bin outcome, timing and event link", async ({ page }) => {
  const repo = path.resolve(__dirname, "../../..");
  const ir = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.ir.serialize import timeline_ir_from_states",
    "from q1timeline.q1asm.parser import parse_q1asm", "from q1timeline.simulation.runtime import simulate_programs",
    "program=parse_q1asm('acquire_digital 0,0,4\\nacquire_digital 0,0,4\\nacquire_timetags 0,1,1,0,4\\nacquire_timetags 0,1,0,0,4\\nacquire_timetags 0,2,1,0,4\\nacquire_timetags 0,2,0,0,4\\nstop',file='tagger.q1asm')",
    "inputs={'digital_acquisitions':[{'sequencer':'tagger','kind':'acquire_digital','acquisition_index':0,'bin_index':0,'level':0}, {'sequencer':'tagger','kind':'acquire_timetags','acquisition_index':0,'bin_index':1,'count':0}, {'sequencer':'tagger','kind':'acquire_timetags','acquisition_index':0,'bin_index':2,'count':2,'timetag_ticks':-129}]}",
    "states=simulate_programs({'tagger':program},simulation={'startup_delay_ns':100,'inputs':inputs},sequences={'tagger':{'acquisitions':{'bins':{'index':0,'num_bins':3}}}})",
    "print(json.dumps(timeline_ir_from_states(states)))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const assets = process.env.Q1LENS_BROWSER_ASSETS || path.resolve(__dirname, "../../src");
  await page.setContent('<!doctype html><html><body><main id="hardware-root"></main></body></html>');
  await page.addStyleTag({path: path.join(assets, "q1timeline/media/hardwareView.css")});
  for (const script of ["hardwareState.js", "hardwareView.js"]) {
    await page.addScriptTag({path: path.join(assets, "q1timeline/media", script)});
  }
  await page.evaluate((ir) => {
    window.q1lensHardwareView.mount(document.getElementById("hardware-root"), ir, {
      initialState: {level: "detail", sequencerId: "tagger", basis: "local"},
      onSelectEvent: (eventId) => { window.selectedEvent = eventId; },
    });
  }, ir);
  await tab(page, "Bins");
  const bins = value(page, "bins");
  await at(page, 100);
  await expect(bins.getByRole("button", {name: "Level 0"})).toHaveAttribute("title", /^Ready at 100 ns/);
  await at(page, 104);
  const unavailable = bins.getByRole("button", {name: /^unavailable · No digital input/});
  await expect(unavailable).toHaveAttribute("title", /^Observed at 104 ns/);
  await expect(bins.getByRole("button", {name: "Level 0"})).toHaveCount(0);
  await unavailable.click();
  expect(await page.evaluate(() => window.selectedEvent)).toEqual(ir.events.find((event) =>
    event.kind === "acquisition_result" && event.meta.status === "unavailable").id);
  await bins.locator('[data-bin="0:0:acquire_digital"]').click();
  await expect(value(page, "bin-history")).toContainText("Observed at 104 ns");
  await expect(value(page, "bin-history")).not.toContainText(/ready/i);

  await at(page, 112);
  await expect(bins.getByRole("button", {name: /^Edge count 0/})).toHaveAttribute("title", /^Ready at 112 ns/);
  await at(page, 120);
  await expect(bins.getByRole("button", {name: "Edge count 2 · Timetag delta -1.0078125 ns"})).toHaveAttribute("title", /^Ready at 120 ns/);
  await tab(page, "Simulation");
  const communication = value(page, "communications");
  await expect(communication).toContainText("unavailable · No digital input");
  await expect(communication).toContainText("Edge count 2 · Timetag delta -1.0078125 ns");
  await expect(communication).not.toContainText("I unknown");
});

test("underflow remains a visible error with the partial queue trace", async ({ page }) => {
  await page.goto("/?case=04-underflow");
  await expect(value(page, "warnings")).toContainText("underflow");
  await page.getByRole("button", { name: "Open QCM0: 1 sequencer", exact: true }).click();
  await page.getByRole("button", { name: "Inspect sequencer starved", exact: true }).click();
  await at(page, 24);
  await expect(value(page, "execution")).toContainText("error");
  await expect(value(page, "execution")).toContainText("24 ns");
  await tab(page, "Simulation");
  await expect(value(page, "communications")).toContainText("4 ns · q1 stall");
  await expect(value(page, "simulation-queue")).toContainText("RT queue 0 / 32 packets");
});

test("supplied synthetic input retains provenance and hides future bins", async ({ page }) => {
  await open(page, "05-supplied-input", "QRM0", "readout");
  await at(page, 204);
  await tab(page, "Simulation");
  await page.getByRole("combobox", { name: "Signal stage" }).selectOption("adc");
  await expect(value(page, "signal-values").locator("strong").nth(0)).toHaveText("I: 0.125");
  await expect(value(page, "signal-values").locator("strong").nth(1)).toHaveText("Q: -0.25");
  await expect(value(page, "signal-values")).toContainText("Supplied trace · synthetic verification fixture");
  await at(page, 211);
  await tab(page, "Bins");
  await expect(value(page, "bins")).toContainText("unknown");
  await at(page, 212);
  await expect(value(page, "bins")).toContainText("I 1 · Q -2");
  await expect(value(page, "bins")).toContainText("threshold 1");
  await page.locator('[data-bin="0:0"]').click();
  await expect(value(page, "bin-history")).toContainText("Supplied trace · synthetic verification fixture");
});

test("Hardware signal sample labels follow the selected time basis once", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const ir = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text",
    "print(json.dumps(analyze_text('set_awg_offs 16384,0\\nupd_param 12\\nstop', file='drive.q1asm',",
    "    sequencer_id='drive', execution_mode='simulation', simulation={'startup_delay_ns': 100,",
    "        'physical': {'sample_period_ns': 1, 'sequencers': {'drive': {}}}})))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const assets = process.env.Q1LENS_BROWSER_ASSETS || path.resolve(__dirname, "../../src");
  await page.setContent('<!doctype html><html><body><main id="hardware-root"></main></body></html>');
  await page.addStyleTag({path: path.join(assets, "q1timeline/media/hardwareView.css")});
  for (const script of ["hardwareState.js", "hardwareView.js"]) {
    await page.addScriptTag({path: path.join(assets, "q1timeline/media", script)});
  }
  await page.evaluate((ir) => {
    window.hardwarePreview = window.q1lensHardwareView.mount(document.getElementById("hardware-root"), ir, {
      initialState: {level: "detail", sequencerId: "drive", selectedBlock: "simulation", time: 5, basis: "aligned"},
    });
  }, ir);
  const sample = value(page, "signal-values").locator("p").first();
  const range = page.locator(".hardware-signal-range span");
  const cursor = page.locator(".hardware-signal-cursor");
  await expect(sample).toContainText("I: 0.5 · sample 5 ns");
  await expect(range.first()).toHaveText("-100 ns");
  await expect(range.last()).toHaveText("12 ns");
  await expect(cursor).toHaveAttribute("x1", "562.5");
  await page.evaluate(() => window.hardwarePreview.setTimeBasis("local"));
  await expect(page.getByRole("spinbutton")).toHaveValue("105");
  await expect(sample).toContainText("I: 0.5 · sample 105 ns");
  await expect(range.first()).toHaveText("0 ns");
  await expect(range.last()).toHaveText("112 ns");
  await expect(cursor).toHaveAttribute("x1", "562.5");
  await at(page, 112);
  await expect(sample).toContainText("I: unknown · outside available trace");
  await expect(sample).not.toContainText("sample 112 ns");
});

async function mountHardwareTrace(page, ir, initialState) {
  const assets = process.env.Q1LENS_BROWSER_ASSETS || path.resolve(__dirname, "../../src");
  await page.setContent('<!doctype html><html><body><main id="hardware-root"></main></body></html>');
  await page.addStyleTag({path: path.join(assets, "q1timeline/media/hardwareView.css")});
  for (const script of ["hardwareState.js", "hardwareView.js"]) {
    await page.addScriptTag({path: path.join(assets, "q1timeline/media", script)});
  }
  await page.evaluate(({ir, initialState}) => {
    window.hardwareSelections = [];
    window.hardwareSources = [];
    window.hardwarePreview = window.q1lensHardwareView.mount(document.getElementById("hardware-root"), ir, {
      initialState, onSelectEvent: (id) => window.hardwareSelections.push(id),
      onSourceClick: (source) => window.hardwareSources.push(source),
    });
  }, {ir, initialState});
}

test("Hardware ends the failed receiver observation while the peer continues", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const ir = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_documents",
    "print(json.dumps(analyze_documents({'rx.q1asm': 'wait_trigger 2,4\\nstop', 'peer.q1asm': 'wait 100\\nstop'},",
    "    execution_mode='simulation', simulation={'startup_delay_ns': 100, 'feedback_queue_capacity': 1,",
    "        'inputs': {'feedback': [{'target': 'rx', 'time_ns': 120, 'id': i, 'value': i} for i in (1, 2)]}})))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  expect(ir.execution.rx.terminal.local_time_ns).toBe(120);
  expect(ir.execution.peer.terminal).toBeUndefined();
  await mountHardwareTrace(page, ir, {level: "detail", sequencerId: "rx", basis: "aligned", time: 19, selectedBlock: "simulation"});
  await expect(value(page, "rt-event")).toContainText("waiting");
  for (const time of [20, 75]) {
    await at(page, time);
    await expect(value(page, "rt-event")).toContainText("observation ended");
    await expect(value(page, "q1-event")).toContainText("stopped");
  }
  await expect(value(page, "execution")).toContainText("LINQ feedback queue overflow (capacity 1). · Observation end 20 ns");
  await expect(value(page, "execution")).not.toContainText("at 120 ns");
  await expect(value(page, "simulation-queue")).toContainText("simulation end 100 ns");
  await page.evaluate(() => window.hardwarePreview.setTimeBasis("local"));
  await expect(page.getByRole("spinbutton")).toHaveValue("175");
  await expect(value(page, "execution")).toContainText("Observation end 120 ns");
  await expect(value(page, "simulation-queue")).toContainText("simulation end 200 ns");
  await page.evaluate(() => window.hardwarePreview.setTimeBasis("aligned"));
  await page.locator('[data-level="sequencers"]').click();
  const card = (id) => page.locator(`[data-sequencer-card="${id}"]`);
  await expect(card("rx").locator('[data-card-status="rt"]')).toHaveText("observation ended");
  await expect(card("rx").locator("[data-sequencer-execution]")).toContainText("Observation end 20 ns");
  await expect(card("peer").locator('[data-card-status="rt"]')).toHaveText("active");
  await card("rx").click();
  await page.locator('[data-action="replay-toggle"]').click();
  const track = (id) => page.locator(`[data-replay-sequencer="${id}"]`);
  await expect(track("rx").locator('[data-value="project-rt"]')).toContainText("RT: observation ended");
  await expect(track("rx").locator('[data-value="project-execution"]')).toContainText("Observation end 20 ns");
  await expect(track("peer").locator('[data-value="project-rt"]')).toContainText("RT: active");
});

test("Hardware Bins shows the recorded QTM aggregate beside the latest window", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const datasets = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text",
    "memory = {'acquisitions': {'results': {'index': 0, 'num_bins': 2}}}",
    "def run(program, inputs, digital=None):",
    "    return analyze_text(program, file='tags.q1asm', sequencer_id='tags', execution_mode='simulation', sequence=memory,",
    "        simulation={'startup_delay_ns': 100, 'inputs': inputs, 'digital': digital or {}})",
    "def window(bin_index, length): return f'acquire_timetags 0,{bin_index},1,0,{length}\\nacquire_timetags 0,{bin_index},0,0,4\\n'",
    "trace = {'initial_level': 0, 'transitions': [{'time_ticks': time * 128 + offset, 'level': level}",
    "    for time in (120, 228, 273, 318, 370) for offset, level in ((0, 1), (1, 0))]}",
    "normal = run(window(0, 100) + window(0, 160) + window(1, 8) + 'stop', {'digital': {'tags': trace}})",
    "supplied = [{'sequencer': 'tags', 'kind': 'acquire_timetags', 'acquisition_index': 0, 'bin_index': 0, **values}",
    "    for values in ({'count': 1, 'timetag_ticks': 9}, {'count': 3})]",
    "recorded = run(window(0, 8) * 2 + 'stop', {'digital_acquisitions': supplied}, {'tags': {'invalid_time_delta': 'record_0'}})",
    "print(json.dumps({'normal': normal, 'recorded': recorded}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const binRow = (id) => page.locator('[data-value="bins"] tr').filter({has: page.locator(`[data-bin="0:${id}:acquire_timetags"]`)});
  const aggregate = (id) => binRow(id).locator("[data-bin-aggregate]");
  const initialState = {level: "detail", sequencerId: "tags", basis: "local"};
  await mountHardwareTrace(page, datasets.normal, initialState);
  await tab(page, "Bins");
  await at(page, 199);
  await expect(aggregate(0)).toHaveCount(0);
  await at(page, 200);
  await expect(aggregate(0)).toHaveText("QTM bin aggregate · 1 accepted write · Mean count 1 · Mean delta 20 ns");
  await at(page, 364);
  await expect(binRow(0).getByRole("button", {name: "Edge count 3 · Timetag delta 24 ns", exact: true})).toBeVisible();
  await expect(aggregate(0)).toHaveText("QTM bin aggregate · 2 accepted writes · Mean count 2 · Mean delta 22 ns");
  await binRow(0).locator('[data-bin="0:0:acquire_timetags"]').click();
  await expect(value(page, "bin-history")).toContainText("latest 2 targets");
  await at(page, 376);
  await expect(aggregate(1)).toHaveText("QTM bin aggregate · 1 accepted write · Mean count 1 · Mean delta 2 ns");
  await page.evaluate(() => window.hardwarePreview.setTimeBasis("aligned"));
  await expect(page.getByRole("spinbutton")).toHaveValue("276");
  await expect(aggregate(0)).toContainText("Mean delta 22 ns");

  await mountHardwareTrace(page, datasets.recorded, initialState);
  await tab(page, "Bins");
  await at(page, 120);
  await expect(aggregate(0)).toHaveText("QTM bin aggregate · 2 accepted writes · Mean count 2 · Mean delta 0.03515625 ns");
});

test("Hardware uses the QTM close target before the effective close result", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const datasets = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text",
    "gate = 'acquire_timetags 0,7,1,0,20\\n'",
    "close = 'acquire_timetags 0,2,0,256,4\\n'",
    "trace = {'initial_level': 0, 'transitions': [{'time_ticks': 104*128, 'level': 1}, {'time_ticks': 104*128+1, 'level': 0}]}",
    "def run(program):",
    "    return analyze_text(program + 'stop', file='tags.q1asm', sequencer_id='tags', execution_mode='simulation',",
    "        sequence={'acquisitions': {'bins': {'index': 0, 'num_bins': 8}}},",
    "        simulation={'startup_delay_ns': 100, 'inputs': {'digital': {'tags': trace}}})",
    "print(json.dumps({'closed': run(gate + close), 'failed': run('wait 20\\n' + close),",
    "    'skipped': run(gate + 'set_cond 1,1,0,4\\n' + close + 'set_cond 0,0,0,0\\n')}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const initialState = {level: "detail", sequencerId: "tags", basis: "local", time: 119, selectedBlock: "bins"};
  const row = page.locator('[data-value="bins"] tr').filter({has: page.locator('[data-bin="0:2:acquire_timetags"]')});
  const targets = value(page, "bins").locator("[data-bin]");
  await mountHardwareTrace(page, datasets.closed, initialState);
  await expect(targets).toHaveCount(0);
  await at(page, 120);
  await expect(targets).toHaveCount(1);
  await row.locator("[data-bin]").click();
  const history = value(page, "bin-history");
  await expect(history).toContainText("No acquisition outcome at this cursor.");
  await at(page, 121);
  await expect(history).toContainText("No acquisition outcome at this cursor.");
  await at(page, 122);
  await expect(row.getByRole("button", {name: "Edge count 1 · Timetag delta 4 ns"})).toBeVisible();
  await expect(history).toContainText("Ready at 122 ns");
  await expect(history.getByRole("button", {name: "Gate close · 120 ns"})).toBeVisible();
  await history.getByRole("button", {name: "Gate open · 100 ns"}).click();
  const open = datasets.closed.events.find((event) => event.kind === "acquire" && event.source.line === 1);
  expect(await page.evaluate(() => window.hardwareSelections.at(-1))).toBe(open.id);

  expect(datasets.failed.execution.tags.reason).toContain("has no open gate");
  await mountHardwareTrace(page, datasets.failed, {...initialState, time: 122});
  await expect(targets).toHaveCount(1);
  await expect(row).toContainText("unknown");
  expect(datasets.skipped.events.some((event) => event.kind === "conditional_skip")).toBe(true);
  await mountHardwareTrace(page, datasets.skipped, {...initialState, time: 124});
  await expect(targets).toHaveCount(0);
});

test("Hardware Play rejection ends Q1 alone and keeps the analytical RT wait", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const fixtures = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.playback import PlaybackSession",
    "def run(program, arrival=None):",
    "    inputs = {'feedback': [{'target': 'seq', 'arrivals': [{'time_ns': arrival, 'id': i, 'value': i} for i in (1, 2)]}]} if arrival is not None else None",
    "    session = PlaybackSession({'seq': ('linq.q1asm', program)}, external_inputs=inputs, alignment_mode='manual:-4', feedback_queue_capacity=1)",
    "    session.run()",
    "    return session.snapshot()",
    "program = 'wait 100\\nmove 3,R0\\nnop\\nadd R0,1,R1\\nstop'",
    "print(json.dumps({'main': run(program, 16), 'early': run(program, 0), 'stop': run('wait 100\\nstop', 6), 'success': run(program)}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  await mountHardwareTrace(page, fixtures.main, {level: "detail", sequencerId: "seq", basis: "aligned", time: 11});
  await expect(value(page, "q1-event")).toContainText("active · add");
  for (const time of [12, 75]) {
    await at(page, time);
    await expect(value(page, "q1-event")).toContainText("observation ended · add");
    await expect(value(page, "rt-event")).toContainText("active · wait");
    await expect(register(page, "R0")).toHaveText("3");
  }
  await expect(value(page, "execution")).toContainText("Q1 observation end 12 ns");
  await expect(value(page, "execution")).not.toContainText("at Q1 16 ns");
  await page.evaluate(() => window.hardwarePreview.setTimeBasis("local"));
  await expect(value(page, "execution")).toContainText("Q1 observation end 16 ns");
  for (const [name, time, expected] of [["early", 0, "observation ended · No active event"],
    ["stop", 6, "observation ended · stop"], ["success", 28, "stopped · stop"]]) {
    await mountHardwareTrace(page, fixtures[name], {level: "detail", sequencerId: "seq", basis: "local", time});
    await expect(value(page, "q1-event")).toContainText(expected);
  }
});

test("Hardware shows the blocked Play Q1 edge while RT remains active", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const ir = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.playback import PlaybackSession",
    "program = 'set_latch_en 1,4\\nwait 100\\nset_cond 1,1,0,4\\nupd_param 4\\nstop'",
    "peer = 'wait 1000\\n' + 'nop\\n' * 10 + 'stop'",
    "session = PlaybackSession({'seq': ('blocked.q1asm', program), 'peer': ('peer.q1asm', peer)}, alignment_mode='manual:-4')",
    "session.run()", "print(json.dumps(session.snapshot()))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  await mountHardwareTrace(page, ir, {level: "detail", sequencerId: "seq", basis: "aligned", time: 7});
  await expect(value(page, "q1-event")).toContainText("active · set_cond");
  for (const time of [8, 20]) {
    await at(page, time);
    await expect(value(page, "q1-event")).toContainText("observation ended · set_cond");
    await expect(value(page, "rt-event")).toContainText("active · wait");
  }
  await expect(value(page, "execution")).toContainText("Q1 observation end 8 ns");
  await page.evaluate(() => window.hardwarePreview.setTimeBasis("local"));
  await expect(value(page, "execution")).toContainText("Q1 observation end 12 ns");
  await expect(page.getByRole("spinbutton")).toHaveValue("24");

  await mountHardwareTrace(page, ir, {level: "detail", sequencerId: "peer", basis: "aligned", time: 20});
  await expect(value(page, "execution")).toContainText("Path execution · partial · 3 instructions");
  await expect(value(page, "execution")).not.toContainText("observation end");
  await expect(value(page, "rt-event")).toContainText("active · wait");
});

test("Hardware shows live Play waits on the core that is armed", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const fixtures = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.playback import PlaybackSession",
    "def run(program, filename, empty, supplied):",
    "    session = PlaybackSession({'seq': (filename, program)}, alignment_mode='manual:-4', external_inputs=empty)",
    "    session.run()",
    "    waiting = session.snapshot()",
    "    session.set_external_inputs(supplied)",
    "    session.run()",
    "    return {'waiting': waiting, 'received': session.snapshot()}",
    "triggers = {'triggers': [{'id': 'source', 'address': 1, 'targets': ['seq'], 'times_ns': [24]}]}",
    "def linq(arrivals): return {'feedback': [{'target': 'seq', 'arrivals': arrivals}]}",
    "print(json.dumps({'trigger': run('wait 12\\nwait_trigger 1,4\\nstop', 'trigger.q1asm', None, triggers),",
    "    'linq': run('wait 100\\nfb_pull_data R0,R1\\nstop', 'linq.q1asm', linq([]), linq([{'time_ns': 16, 'id': 3, 'value': 77}]))}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const view = {level: "detail", sequencerId: "seq", basis: "aligned"};
  for (const {fixture, core, arm, local, kind, filename, label, receivedAt, receivedLabel} of [
    {fixture: fixtures.trigger, core: "rt", arm: 8, local: 12, kind: "wait_trigger", filename: "trigger.q1asm",
      label: "waiting · Waiting for external trigger", receivedAt: 20, receivedLabel: "active · wait_trigger"},
    {fixture: fixtures.linq, core: "q1", arm: 0, local: 4, kind: "feedback_pop", filename: "linq.q1asm",
      label: "stalled · Waiting for LINQ feedback", receivedAt: 15, receivedLabel: "active · fb_pull_data"},
  ]) {
    expect(fixture.waiting.execution.seq.status).toBe("waiting");
    expect(fixture.waiting.execution.seq.terminal).toBeUndefined();
    await mountHardwareTrace(page, fixture.waiting, {...view, time: arm - 1, selectedBlock: core});
    await expect(value(page, `${core}-event`)).toContainText("active · wait");
    await at(page, arm);
    await expect(value(page, `${core}-event`)).toContainText(label);
    if (core === "q1") {
      await at(page, 20);
      await expect(value(page, "q1-event")).toContainText(label);
      await expect(value(page, "rt-event")).toContainText("active · wait");
    }
    await page.locator(`[data-action="${core}-event"]`).click();
    const wait = fixture.waiting.events.find((event) => event.kind === kind);
    expect(await page.evaluate(() => window.hardwareSelections.at(-1))).toBe(wait.id);
    await page.locator('[data-action="source"]').click();
    expect(await page.evaluate(() => window.hardwareSources.at(-1))).toMatchObject({file: filename, line: 2});
    await page.evaluate(() => window.hardwarePreview.setTimeBasis("local"));
    await expect(page.getByRole("spinbutton")).toHaveValue(String(local));
    await expect(value(page, `${core}-event`)).toContainText(label);

    expect(fixture.received.execution.seq.status).toBe("completed");
    await mountHardwareTrace(page, fixture.received, {...view, time: receivedAt});
    await expect(value(page, `${core}-event`)).toContainText(receivedLabel);
  }
});

test("Hardware scopes compact loop uncertainty by field and by visit", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const fixtures = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text",
    "from q1timeline.analysis.alignment import align_timelines",
    "from q1timeline.analysis.interpreter import interpret_program",
    "from q1timeline.ir import timeline_ir_from_states",
    "from q1timeline.q1asm.parser import parse_q1asm",
    "steady = 'move 3,R0\\nmove 77,R5\\nset_freq 4000000\\nupd_param 4\\nbody: wait 100\\nloop R0,@body\\nstop'",
    "varying = 'move 3,R0\\nset_freq 4000000\\nupd_param 4\\nbody: set_freq R0\\nupd_param 100\\nloop R0,@body\\nset_freq 2000000\\nupd_param 4\\nstop'",
    "visits = 'move 2,R0\\nnop\\nbody: move 2,R1\\ninner: wait 4\\nloop R1,@inner\\nsub R0,1,R0\\nnop\\njnz @body\\nstop'",
    "expanded = interpret_program(parse_q1asm(visits, file='visits.q1asm'), sequencer_id='visits',",
    "    loop_preview_counts={'visits:loop:visits.q1asm:4:5': 2})",
    "align_timelines([expanded], mode='manual:-4')",
    "print(json.dumps({'steady': analyze_text(steady, file='steady.q1asm', alignment_mode='manual:-4'),",
    "    'varying': analyze_text(varying, file='varying.q1asm', alignment_mode='manual:-4'),",
    "    'visits': timeline_ir_from_states([expanded])}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const view = {level: "detail", basis: "local"};
  const frequency = value(page, "parameters").locator("tr").filter({has: page.getByRole("rowheader", {name: "frequency", exact: true})});
  await mountHardwareTrace(page, fixtures.steady, {...view, sequencerId: "steady", time: 43});
  await expect(register(page, "R0")).toHaveText("3");
  await at(page, 44);
  await expect(register(page, "R0")).toContainText("unknown");
  await expect(register(page, "R5")).toHaveText("77");
  await at(page, 80);
  await expect(register(page, "R0")).toHaveText("0");
  await at(page, 104);
  await tab(page, "Parameters");
  await expect(frequency.getByRole("button", {name: "4000000", exact: true})).toBeVisible();
  await page.evaluate(() => window.hardwarePreview.setTimeBasis("aligned"));
  await expect(page.getByRole("spinbutton")).toHaveValue("100");
  await expect(frequency.getByRole("button", {name: "4000000", exact: true})).toBeVisible();

  await mountHardwareTrace(page, fixtures.varying, {...view, sequencerId: "varying", time: 104});
  await tab(page, "Parameters");
  await expect(frequency.getByRole("button", {name: "unknown", exact: true})).toBeVisible();
  await at(page, 304);
  await expect(frequency.getByRole("button", {name: "2000000", exact: true})).toBeVisible();

  expect(fixtures.visits.events.filter((event) => event.kind === "q1_issue" && event.meta.preview_source_event_id).length).toBeGreaterThan(0);
  await mountHardwareTrace(page, fixtures.visits, {...view, sequencerId: "visits", time: 39});
  for (const offset of [0, -4]) {
    if (offset) await page.evaluate(() => window.hardwarePreview.setTimeBasis("aligned"));
    for (const [time, label] of [[39, "active · loop"], [40, "unknown"], [47, "unknown"], [48, "active · sub"],
      [111, "active · loop"], [112, "unknown"], [119, "unknown"], [120, "active · sub"]]) {
      await at(page, time + offset);
      await expect(value(page, "q1-event")).toContainText(label);
    }
  }
});

test("Hardware shows unresolved branches at the recorded Q1 issue end", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const fixtures = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text",
    "programs = {'repeat': 'move 1,R0\\nnop\\nbody: wait 4\\ncmp R0,1\\nfb_pull_data R1,R0\\nnop\\njz @body\\nstop',",
    "    'single': 'body: wait 4\\ncmp R0,1\\njz @body\\nstop',",
    "    'symbolic': 'body: wait {DELAY}\\ncmp R0,1\\njz @body\\nstop'}",
    "print(json.dumps({seq: analyze_text(program, file=f'{seq}.q1asm', alignment_mode='manual:-4')",
    "    for seq, program in programs.items()}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));

  for (const [sequencerId, ir] of Object.entries(fixtures)) {
    const repeat = sequencerId === "repeat";
    const issueEnd = repeat ? 96 : 32;
    const issues = ir.events.filter((event) => event.kind === "q1_issue" && event.meta.op === "jz");
    const unresolved = ir.events.find((event) => event.kind === "branch_region" && event.t1 === null);
    expect(issues).toHaveLength(repeat ? 2 : 1);
    expect(unresolved.meta.q1_issue_event_id).toBe(issues.at(-1).id);
    expect(issues.at(-1).t1.value).toBe(issueEnd);
    await mountHardwareTrace(page, ir, {level: "detail", sequencerId, basis: "local", time: 7});
    if (repeat) {
      await expect(value(page, "rt-event")).toContainText("active · wait");
      await at(page, 8);
      await expect(value(page, "rt-event")).toContainText("unknown");
      await expect(register(page, "R0")).toHaveText("1");
    }
    for (const offset of [0, -4]) {
      if (offset) await page.evaluate(() => window.hardwarePreview.setTimeBasis("aligned"));
      if (repeat) {
        await at(page, 52 + offset);
        await expect(value(page, "q1-event")).toContainText("active · wait");
      }
      await at(page, issueEnd - 1 + offset);
      await expect(value(page, "q1-event")).toContainText("active · jz");
      await at(page, issueEnd + offset);
      await expect(value(page, "q1-event")).toContainText("unknown");
      if (sequencerId === "symbolic") await expect(value(page, "rt-event")).toContainText("last command");
    }
  }
});

test("Hardware separates Q1 proof cutoffs from RT-only uncertainty", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const fixtures = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text",
    "repeat = 'move 1,R0\\nnop\\nbody: wait 4\\ncmp R0,1\\njz @body\\nstop'",
    "programs = {'nested': ('move 2,R0\\nmove 2,R1\\nnop\\nouter: nop\\ninner: nop\\nloop R1,@inner\\nloop R0,@outer\\nstop', 10000),",
    "    'limit': (repeat, 6), 'partial': (repeat, 1),",
    "    'rt_only': ('move 7,R5\\nwait 2\\nnop\\nnop\\nstop', 10000)}",
    "print(json.dumps({seq: analyze_text(program, file=f'{seq}.q1asm', alignment_mode='manual:-4', max_steps=steps)",
    "    for seq, (program, steps) in programs.items()}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));

  for (const [sequencerId, issueEnd, rtBoundary, op] of [["nested", 76, 0, "loop"], ["limit", 104, 12, "jz"], ["partial", 44, 8, "wait"]]) {
    const ir = fixtures[sequencerId];
    const cutoff = ir.events.find((event) => event.kind === "unknown_region");
    expect(cutoff.meta.q1_proof_cutoff).toBe(true);
    expect(cutoff.t0.value).toBe(rtBoundary);
    if (sequencerId === "partial") {
      const waits = ir.events.filter((event) => event.kind === "q1_issue" && event.meta.op === "wait");
      expect(waits.map((event) => [event.t0.value, event.t1.value])).toEqual([[8, 12], [40, 44]]);
      expect(cutoff.source.line).toBe(5);
      expect(ir.diagnostics.find((item) => item.related_events.includes(cutoff.id)).source.line).toBe(5);
      expect(cutoff.meta.q1_issue_event_id).toBe(waits[1].id);
    }
    expect(ir.events.find((event) => event.id === cutoff.meta.q1_issue_event_id).t1.value).toBe(issueEnd);
    await mountHardwareTrace(page, ir, {level: "detail", sequencerId, basis: "local", time: issueEnd - 1});
    for (const offset of [0, -4]) {
      if (offset) await page.evaluate(() => window.hardwarePreview.setTimeBasis("aligned"));
      await at(page, issueEnd - 1 + offset);
      await expect(value(page, "q1-event")).toContainText(`active · ${op}`);
      await expect(register(page, "R0")).toHaveText(sequencerId === "nested" ? "2" : "1");
      await at(page, issueEnd + offset);
      await expect(value(page, "q1-event")).toContainText("unknown");
      await expect(value(page, "rt-event")).toContainText("unknown");
      if (sequencerId === "nested") {
        await expect(value(page, "register-history").locator(`[data-register-time="${issueEnd + offset}"] strong`, {hasText: /^1$/})).toBeVisible();
      } else {
        await expect(register(page, "R0")).toHaveText("1");
      }
    }
  }

  const ir = fixtures.rt_only;
  const rtOnly = ir.events.find((event) => event.kind === "unknown_region");
  expect(rtOnly.meta.q1_proof_cutoff).toBeUndefined();
  expect(ir.events.find((event) => event.id === rtOnly.meta.q1_issue_event_id).t1.value).toBe(8);
  await mountHardwareTrace(page, ir, {level: "detail", sequencerId: "rt_only", basis: "local", time: 8});
  await expect(value(page, "q1-event")).toContainText("active · nop");
  await expect(value(page, "rt-event")).toContainText("unknown");
  await expect(register(page, "R5")).toHaveText("7");
});

test("Hardware keeps RT and bins independent of Q1-only compact loops", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const fixtures = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text",
    "programs = {'plain': 'body: nop\\njmp @body', 'setter': 'body: set_freq 4000000\\njmp @body',",
    "    'earlier': 'acquire 0,0,4\\nbody: nop\\njmp @body', 'wait_zero': 'body: wait 0\\njmp @body'}",
    "print(json.dumps({seq: analyze_text(program, file=f'{seq}.q1asm', alignment_mode='manual:-4')",
    "    for seq, program in programs.items()}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));

  for (const [sequencerId, rtLabel] of [["plain", "not started"], ["setter", "not started"],
    ["earlier", "last command · acq#0"], ["wait_zero", "unknown"]]) {
    await mountHardwareTrace(page, fixtures[sequencerId], {level: "detail", sequencerId, basis: "local", time: 19});
    await expect(value(page, "q1-event")).toContainText("active · jmp");
    await expect(value(page, "rt-event")).toContainText(rtLabel);
    await page.evaluate(() => window.hardwarePreview.setTimeBasis("aligned"));
    await expect(page.getByRole("spinbutton")).toHaveValue("15");
    await expect(value(page, "q1-event")).toContainText("active · jmp");
    await expect(value(page, "rt-event")).toContainText(rtLabel);
    if (sequencerId === "setter") {
      await tab(page, "Parameters");
      const frequency = value(page, "parameters").locator("tr").filter({has: page.getByRole("rowheader", {name: "frequency", exact: true})});
      await expect(frequency.locator("td").first()).toHaveText("4000000");
      await expect(frequency.locator("td").nth(1)).toHaveText("—");
    }
    if (sequencerId === "earlier") {
      await tab(page, "Bins");
      await expect(value(page, "bin-count")).toContainText("1 of 1 targets");
    }
    if (sequencerId === "plain") {
      await at(page, 16);
      await expect(value(page, "q1-event")).toContainText("unknown");
      await expect(value(page, "warnings")).toContainText("Q1 state may be incomplete");
    }
    if (sequencerId === "wait_zero") {
      await expect(value(page, "warnings")).toContainText("acquisition counts may be incomplete");
    } else {
      await expect(value(page, "warnings")).not.toContainText("acquisition counts may be incomplete");
    }
  }
});

test("Hardware keeps typed bin results, histories and saved selections distinct", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const ir = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text",
    "program = 'acquire_timetags 0,0,1,0,8\\nacquire_timetags 0,0,0,0,4\\nacquire_digital 0,0,4\\nacquire_ttl 0,0,1,8\\nacquire_ttl 0,7,0,4\\nstop'",
    "trace = {'initial_level': 0, 'transitions': [{'time_ticks': time * 128 + offset, 'level': level}",
    "    for time in (104, 120) for offset, level in ((0, 1), (1, 0))]}",
    "print(json.dumps(analyze_text(program, file='mixed.q1asm', sequencer_id='mixed', execution_mode='simulation',",
    "    sequence={'acquisitions': {'bins': {'index': 0, 'num_bins': 1}}},",
    "    simulation={'startup_delay_ns': 100, 'inputs': {'digital': {'mixed': trace}}})))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  await mountHardwareTrace(page, ir, {level: "detail", sequencerId: "mixed", basis: "local", time: 108, selectedBlock: "bins", selectedBin: "0:0"});
  const history = value(page, "bin-history");
  await expect(history).toContainText("Timetags · latest 1 targets");
  expect(await page.evaluate(() => window.hardwarePreview.getState().selectedBin)).toBe("0:0:acquire_timetags");
  await at(page, 124);
  const row = (kind) => page.locator('[data-value="bins"] tr').filter({has: page.locator(`[data-bin="0:0:${kind}"]`)});
  await expect(row("acquire_timetags")).toContainText("Edge count 1");
  await expect(row("acquire_timetags")).toContainText("QTM bin aggregate · 1 accepted write");
  await expect(row("acquire_digital")).toContainText("Level 0");
  await expect(row("acquire_digital").locator("[data-bin-aggregate]")).toHaveCount(0);
  await expect(row("acquire_ttl")).toContainText("TTL counts");
  await expect(row("acquire_ttl")).toContainText("Edge count 1");
  await expect(history).toContainText("Timetags · latest 1 targets");
  await row("acquire_digital").locator("[data-bin]").click();
  await expect(history).toContainText("Digital level · latest 1 targets");
  await expect(value(page, "selection-detail")).toHaveText("mixed.q1asm:3");
  await expect(history).toContainText("Level 0");
  await expect(page.locator('[data-bin="0:7:acquire_ttl"]')).toHaveCount(0);
});

test("Hardware shows accepted TTL auto-increment writes and exact gate evidence", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const fixtures = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text",
    "gate = 'acquire_ttl 0,2,1,4\\nwait 16\\nacquire_ttl 0,7,0,4\\n'",
    "def run(program, times, count=4, increment=True):",
    "    trace = {'initial_level': 0, 'transitions': [{'time_ticks': time * 128 + i, 'level': level}",
    "        for time in times for i, level in ((0, 1), (1, 0))]}",
    "    return analyze_text(program, file='ttl.q1asm', sequencer_id='ttl', execution_mode='simulation',",
    "        sequence={'acquisitions': {'bins': {'index': 0, 'num_bins': count}}}, simulation={'startup_delay_ns': 100,",
    "            'digital': {'ttl': {'ttl_acq_auto_bin_incr_en': increment}}, 'inputs': {'digital': {'ttl': trace}}})",
    "print(json.dumps({'incremented': run(gate + 'stop', [104, 116]), 'fixed': run(gate + 'stop', [104, 116], increment=False),",
    "    'empty': run(gate + 'stop', []), 'incomplete': run('acquire_ttl 0,2,1,20\\nstop', [104]),",
    "    'laterError': run(gate * 2 + 'stop', [104, 128, 140], count=3),",
    "    'repeated': run(gate * 2 + 'stop', [104, 116, 128], increment=False),",
    "    'separate': run(gate + gate.replace('0,2,1', '0,3,1') + 'stop', [104, 116, 128], increment=False),",
    "    'laterIncomplete': run(gate + 'acquire_ttl 0,2,1,20\\nstop', [104, 116, 128], increment=False)}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const initialState = {level: "detail", sequencerId: "ttl", basis: "local", time: 119, selectedBlock: "bins"};
  const row = (bin) => page.locator('[data-value="bins"] tr').filter({has: page.locator(`[data-bin="0:${bin}:acquire_ttl"]`)});
  const aggregate = (bin) => row(bin).locator("[data-bin-aggregate]");
  await mountHardwareTrace(page, fixtures.incremented, initialState);
  await expect(row(2)).toContainText("TTL counts");
  await expect(aggregate(2)).toHaveCount(0);
  await expect(row(3)).toHaveCount(0);
  await at(page, 120);
  await expect(row(2)).toContainText("Edge count 1");
  await expect(row(3)).toContainText("Edge count 1");
  for (const bin of [2, 3]) await expect(aggregate(bin)).toHaveText("TTL bin total · Edge count 1");
  await expect(row(3).locator("td").nth(2)).toHaveText("0");
  await expect(row(3).getByRole("button", {name: "120 ns write"})).toBeVisible();
  await row(3).locator("[data-bin]").click();
  await expect(value(page, "selection-detail")).toHaveText("ttl.q1asm:3");
  const history = value(page, "bin-history");
  await expect(history.getByRole("button", {name: "Gate open · 100 ns"})).toBeVisible();
  await history.getByRole("button", {name: "Gate close · 120 ns"}).click();
  const close = fixtures.incremented.events.find((event) => event.kind === "acquire" && event.source.line === 3);
  expect(await page.evaluate(() => window.hardwareSelections.at(-1))).toBe(close.id);
  await expect(row(7)).toHaveCount(0);

  for (const [name, time, outcome, total] of [["fixed", 120, "Edge count 2", 2], ["empty", 120, "Edge count 0", 0],
    ["incomplete", 120, "incomplete", undefined], ["laterError", 140, "incomplete", 1], ["laterIncomplete", 144, "incomplete", 2]]) {
    await mountHardwareTrace(page, fixtures[name], {...initialState, time});
    await expect(row(2)).toContainText(outcome);
    if (total === undefined) await expect(aggregate(2)).toHaveCount(0);
    else await expect(aggregate(2)).toHaveText(`TTL bin total · Edge count ${total}`);
    await expect(row(3)).toHaveCount(0);
    await expect(row(7)).toHaveCount(0);
  }
  expect(fixtures.laterError.execution.ttl.status).toBe("error");
  expect(fixtures.laterError.events.some((event) => event.kind === "acquisition_result" && event.meta.status === "modeled")).toBe(true);

  await mountHardwareTrace(page, fixtures.repeated, initialState);
  await at(page, 120);
  await expect(aggregate(2)).toHaveText("TTL bin total · Edge count 2");
  await at(page, 144);
  await expect(row(2).getByRole("button", {name: "Edge count 1", exact: true})).toBeVisible();
  await expect(aggregate(2)).toHaveText("TTL bin total · Edge count 3");

  await mountHardwareTrace(page, fixtures.separate, {...initialState, time: 144});
  await expect(aggregate(2)).toHaveText("TTL bin total · Edge count 2");
  await expect(aggregate(3)).toHaveText("TTL bin total · Edge count 1");
});

test("Hardware shows modeled I/Q bin averages at each accepted result", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const datasets = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_text",
    "base = 'acquire 0,0,8\\n'",
    "memory = {'acquisitions': {'data': {'index': 0, 'num_bins': 1}},",
    "    'weights': {'i': {'index': 0, 'data': [0.5]*8}, 'q': {'index': 1, 'data': [1.0]*8}}}",
    "physical = {'sequencers': {'iq': {'threshold': 3.5, 'integration_length_ns': 8, 'input': {",
    "    'kind': 'trace', 'source': 'Two-window scenario', 'start_ns': 100,",
    "    'i': [0.25]*8 + [0.5]*8, 'q': [0.125]*8 + [-0.25]*8}}}}",
    "scenario = {'sequencer': 'iq', 'acquisition_index': 0, 'bin_index': 0, 'i': 17, 'q': -9, 'thresholded': 0}",
    "def run(suffix, inputs=()):",
    "    return analyze_text(base + suffix + 'stop', file='iq.q1asm', sequencer_id='iq', execution_mode='simulation',",
    "        sequence=memory, simulation={'startup_delay_ns': 100, 'physical': physical, 'inputs': {'acquisitions': list(inputs)}})",
    "print(json.dumps({'plain': run(base), 'weighted': run('acquire_weighed 0,0,0,1,8\\n'), 'scenario': run(base, [scenario])}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const initialState = {level: "detail", sequencerId: "iq", basis: "local", time: 107, selectedBlock: "bins"};
  const row = value(page, "bins").locator("tr").filter({has: page.locator('[data-bin="0:0"]')});
  const aggregate = row.locator("[data-bin-aggregate]");
  await mountHardwareTrace(page, datasets.plain, initialState);
  await expect(aggregate).toHaveCount(0);
  await at(page, 108);
  await expect(aggregate).toHaveText("I/Q bin aggregate · 1 accepted window · Mean I 2 · Mean Q 1 · Threshold count 0 (normalized full scale * ns)");
  await at(page, 115);
  await expect(aggregate).toContainText("1 accepted window");
  await at(page, 116);
  await expect(row.getByRole("button", {name: "I 4 · Q -2 (normalized full scale * ns) · threshold 1"})).toBeVisible();
  await expect(aggregate).toHaveText("I/Q bin aggregate · 2 accepted windows · Mean I 3 · Mean Q -0.5 · Threshold count 1 (normalized full scale * ns)");
  await page.evaluate(() => window.hardwarePreview.setTimeBasis("aligned"));
  await expect(page.getByRole("spinbutton")).toHaveValue("16");
  await expect(aggregate).toContainText("Mean I 3");

  await mountHardwareTrace(page, datasets.weighted, {...initialState, time: 116});
  await expect(value(page, "bins").locator("[data-bin]")).toHaveCount(1);
  await expect(row.getByRole("button", {name: "I 2 · Q -2 (normalized full scale * ns) · threshold 0"})).toBeVisible();
  await expect(aggregate).toContainText("2 accepted windows · Mean I 2 · Mean Q -0.5 · Threshold count 0");

  await mountHardwareTrace(page, datasets.scenario, {...initialState, time: 108});
  await expect(row.getByRole("button", {name: "I 17 · Q -9 (scenario values) · threshold 0"})).toBeVisible();
  await expect(aggregate).toHaveCount(0);
  await at(page, 116);
  await expect(aggregate).toContainText("1 accepted window · Mean I 4 · Mean Q -2 · Threshold count 1");
});

test("Hardware selects recorded unavailable I/Q evidence at its observation time", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const datasets = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_documents",
    "programs = {'iq.q1asm': 'acquire 0,0,4\\nacquire 0,0,4\\nstop', 'peer.q1asm': 'wait 24\\nstop'}",
    "first = {'sequencer': 'iq', 'acquisition_index': 0, 'bin_index': 0, 'i': 17, 'q': -9, 'thresholded': 0}",
    "def run(routes):",
    "    return analyze_documents(programs, execution_mode='simulation', simulation={'startup_delay_ns': 100,",
    "        'inputs': {'acquisitions': [first]}, 'trigger_routes': routes})",
    "route = {'source': 'iq', 'target': 'peer', 'address': 1, 'thresholded': 1, 'latency_ns': 4}",
    "print(json.dumps({'routed': run([route]), 'noRoute': run([])}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const initialState = {level: "detail", sequencerId: "iq", basis: "aligned", time: 7, selectedBlock: "bins", selectedBin: "0:0"};
  const row = value(page, "bins").locator("tr").filter({has: page.locator('[data-bin="0:0"]')});
  const success = row.getByRole("button", {name: "I 17 · Q -9 (scenario values) · threshold 0"});
  const unavailable = row.getByRole("button", {name: /unavailable · Acquisition requires a scenario result/});
  const communications = value(page, "communications");
  for (const ir of [datasets.routed, datasets.noRoute]) {
    await mountHardwareTrace(page, ir, initialState);
    await expect(success).toBeVisible();
    await expect(unavailable).toHaveCount(0);
    await at(page, 8);
    await expect(success).toHaveCount(0);
    await expect(unavailable).toBeVisible();
    await expect(row.locator("td").nth(2)).toHaveText("2");
    await expect(value(page, "bin-history")).toContainText("Observed at 8 ns");
    await expect(value(page, "execution")).toContainText("Observation end 8 ns");
    await unavailable.click();
    const evidence = ir.events.find((event) => event.kind === "acquisition_unavailable");
    expect(await page.evaluate(() => window.hardwareSelections.at(-1))).toBe(evidence.id);
    await page.locator('[data-action="source"]').click();
    expect(await page.evaluate(() => window.hardwareSources.at(-1))).toMatchObject({file: "iq.q1asm", line: 2});
    await tab(page, "Simulation");
    await expect(communications.getByRole("button", {name: "8 ns · acquisition unavailable"})).toBeVisible();
    await expect(communications).toContainText("Acquisition requires a scenario result or configured physical input.");
    await page.evaluate(() => window.hardwarePreview.setTimeBasis("local"));
    await expect(communications.getByRole("button", {name: "108 ns · acquisition unavailable"})).toBeVisible();
    await tab(page, "Bins");
    await expect(value(page, "bin-history")).toContainText("Observed at 108 ns");
  }

  const legacy = structuredClone(datasets.noRoute);
  const evidence = legacy.events.find((event) => event.kind === "acquisition_unavailable");
  legacy.events = legacy.events.filter((event) => event.id !== evidence.id);
  delete legacy.source_map.by_event_id[evidence.id];
  const sourceKey = `${evidence.source.file}:${evidence.source.line}`;
  legacy.source_map.by_source[sourceKey] = legacy.source_map.by_source[sourceKey].filter((id) => id !== evidence.id);
  for (const diagnostic of legacy.diagnostics) {
    diagnostic.related_events = diagnostic.related_events.filter((id) => id !== evidence.id);
  }
  expect(legacy.execution.iq.status).toBe("blocked");
  await mountHardwareTrace(page, legacy, {...initialState, time: 8});
  await expect(success).toBeVisible();
  await expect(unavailable).toHaveCount(0);
  await tab(page, "Simulation");
  await expect(communications).not.toContainText("acquisition unavailable");
});

test("Hardware follows the effective TTL and fractional QTM gate windows", async ({page}) => {
  const repo = path.resolve(__dirname, "../../..");
  const datasets = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from q1timeline.analysis.api import analyze_documents",
    "ttl = 'acquire_ttl 0,2,1,4\\n'",
    "close = 'acquire_ttl 0,7,0,4\\n'",
    "qtm = 'acquire_timetags 0,7,1,1,4\\nwait 16\\nacquire_timetags 0,2,0,3,4\\n'",
    "def run(program, bins=8, fail=False):",
    "    programs = {'s.q1asm': program + 'stop'}",
    "    inputs = {'digital': {'s': {'initial_level': 1, 'transitions': []}}}",
    "    if fail:",
    "        programs['peer.q1asm'] = 'wait 200\\nstop'",
    "        inputs['feedback'] = [{'target': 's', 'time_ns': 120, 'id': n, 'value': n} for n in (1, 2)]",
    "    return analyze_documents(programs, execution_mode='simulation',",
    "        sequences={'s': {'acquisitions': {'bins': {'index': 0, 'num_bins': bins}}}},",
    "        simulation={'startup_delay_ns': 100, 'feedback_queue_capacity': 1, 'inputs': inputs})",
    "mixed = ('acquire_ttl 0,1,1,4\\nacquire_timetags 0,7,1,1,4\\nacquire_digital 0,3,4\\n'",
    "         'wait 4\\nacquire_timetags 0,2,0,3,4\\n' + close)",
    "skipped = 'set_cond 1,1,0,4\\n' + ttl + 'set_cond 0,0,0,0\\nwait 16\\n'",
    "print(json.dumps({'ttl': run(ttl + 'wait 16\\n' + close), 'qtm': run(qtm), 'rejected': run(qtm, 2),",
    "    'incomplete': run(ttl + 'wait 100\\n', fail=True), 'refused': run(ttl, 2), 'skipped': run(skipped),",
    "    'sample': run('acquire_digital 0,3,20\\n'), 'mixed': run(mixed)}))",
  ].join("\n")], {cwd: repo, encoding: "utf8", timeout: 10000, env: {...process.env, PYTHONPATH: path.join(repo, "src")}}));
  const initialState = {level: "detail", sequencerId: "s", basis: "aligned", time: 0, selectedBlock: "acquisition"};
  const block = page.locator('[data-block="acquisition"]');
  const label = page.locator('[data-block-value="acquisition"]');
  const detail = page.locator('[data-block-title="acquisition"]');
  await mountHardwareTrace(page, datasets.ttl, initialState);
  for (const time of [4, 10]) {
    await at(page, time);
    await expect(label).toHaveText("Gate open");
    await expect(block).toHaveClass(/is-active/);
    await expect(value(page, "rt-event")).toContainText("active · wait");
  }
  await block.click();
  expect(await page.evaluate(() => window.hardwareSelections.at(-1))).toBe(datasets.ttl.events.find((event) =>
    event.kind === "acquire" && event.source.line === 1).id);
  await expect(value(page, "selection-detail")).toHaveText("s.q1asm:1");
  await at(page, 20);
  await expect(label).toHaveText("Gate closed");
  await expect(block).not.toHaveClass(/is-active/);
  await expect(detail).toContainText("Gate closed at 20 ns");
  await expect(value(page, "rt-event")).toHaveText("active · acquire");
  await page.locator('[data-action="rt-event"]').click();
  expect(await page.evaluate(() => window.hardwareSelections.at(-1))).toBe(datasets.ttl.events.find((event) =>
    event.kind === "acquire" && event.source.line === 3).id);
  await page.evaluate(() => window.hardwarePreview.setTimeBasis("local"));
  await expect(page.getByRole("spinbutton")).toHaveValue("120");
  await expect(detail).toContainText("Gate closed at 120 ns");

  for (const key of ["qtm", "rejected"]) {
    await mountHardwareTrace(page, datasets[key], initialState);
    await expect(label).toHaveText("No resolved acquire");
    await at(page, 1 / 128);
    await expect(label).toHaveText("Gate open");
    await expect(detail).toContainText("Opened at 0.0078125 ns");
    await at(page, 20 + 2 / 128);
    await expect(block).toHaveClass(/is-active/);
    await at(page, 20 + 3 / 128);
    await expect(label).toHaveText("Gate closed");
    await expect(detail).toContainText("Gate closed at 20.0234375 ns");
  }
  expect(datasets.rejected.execution.s.reason).toContain("bin 2 is out of bounds");
  expect(datasets.rejected.events.some((event) => event.kind === "acquisition_result")).toBe(false);

  await mountHardwareTrace(page, datasets.incomplete, {...initialState, time: 19});
  await expect(label).toHaveText("Gate open");
  await at(page, 20);
  await expect(label).toHaveText("Gate incomplete");
  await at(page, 50);
  await expect(label).toHaveText("Gate incomplete");
  await expect(detail).toContainText("Gate incomplete at 20 ns");
  await expect(block).not.toHaveClass(/is-active/);
  expect(datasets.incomplete.execution.peer.status).toBe("completed");

  for (const key of ["refused", "skipped"]) {
    await mountHardwareTrace(page, datasets[key], {...initialState, time: 1});
    await expect(label).toHaveText("No resolved acquire");
    await expect(block).not.toHaveClass(/is-active/);
  }

  await mountHardwareTrace(page, datasets.sample, initialState);
  await expect(label).toHaveText("Sampled level 1");
  await expect(detail).toContainText("Level 1 · Observed at 0 ns");
  await at(page, 10);
  await expect(label).toHaveText("Sampled level 1");
  await expect(block).not.toHaveClass(/is-active/);
  await expect(value(page, "rt-event")).toHaveText("active · acquire");

  await mountHardwareTrace(page, datasets.mixed, {...initialState, time: 9});
  await expect(label).toHaveText("Gate open");
  await expect(value(page, "selection-detail")).toHaveText("s.q1asm:2");
  await at(page, 17);
  await expect(label).toHaveText("Gate open");
  await expect(value(page, "selection-detail")).toHaveText("s.q1asm:1");
});

test("Hardware cursor commits preserve focus, validation and the first subsequent action", async ({page}) => {
  const concrete = (value) => ({kind: "concrete", value});
  const source = {file: "cursor.q1asm", line: 1};
  const ir = {sequencers: [{id: "seq"}], events: [{id: "wait", kind: "wait", lane: "rt.wait", sequencer_id: "seq",
    t0: concrete(0), t1: concrete(40), meta: {rt_packet_id: "p0"}, source}],
    register_history: ["R0", "R1"].map((register, index) => ({register, sequencer_id: "seq", event_id: "wait",
      time: concrete(8), value: concrete(index + 1), source}))};
  await mountHardwareTrace(page, ir, {level: "detail", sequencerId: "seq", basis: "local"});
  const spin = page.getByRole("spinbutton");
  await at(page, 12);
  await expect(spin).toBeFocused();
  await page.locator('[data-register="R1"]').click();
  await expect(page.locator('[data-register="R1"]')).toHaveAttribute("aria-pressed", "true");
  await expect(spin).toHaveValue("12");
  await spin.fill("");
  await spin.press("Enter");
  await expect(spin).toHaveValue("12");
  await spin.fill("8");
  await spin.blur();
  await expect(spin).toHaveValue("8");
  await spin.fill("8.0");
  await spin.blur();
  await expect(spin).toHaveValue("8");
  await page.locator('[data-control="scrubber"]').evaluate((node) => {
    node.value = "20";
    node.dispatchEvent(new Event("input", {bubbles: true}));
  });
  await expect(spin).toHaveValue("20");
});
