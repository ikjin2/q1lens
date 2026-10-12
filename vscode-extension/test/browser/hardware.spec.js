const { test, expect, open, at, value, register, tab } = require("./helpers");
const { createHash } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

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
