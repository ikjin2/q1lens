const { test, expect, open, at, value, register, tab } = require("./helpers");
const { createHash } = require("node:crypto");
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
