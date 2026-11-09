const path = require("node:path");
const { test, expect } = require("./helpers");

test("DEF webview changes heatmap and histogram metrics using saved results", async ({ page }) => {
  await page.goto("/?case=01-digital");
  const src = path.join(__dirname, "../../src");
  await page.addStyleTag({ path: path.join(src, "q1timeline/media/timeline.css") });
  await page.addScriptTag({ path: path.join(src, "q1timeline/media/defRuns.js") });
  await page.evaluate(() => {
    const project = "fixture.yml", uri = "file:///fixture.q1asm";
    const axes = [{ name: "X", min: "0", last: "1", step: "1", count: "2" }, { name: "Y", min: "10", last: "11", step: "1", count: "2" }];
    const plan = { axes, total: "4", allowed: true };
    const report = (patch) => window.postMessage({ type: "defRunState", project, patch }, "*");
    window.requests = [];
    const bridge = { postMessage(message) {
      window.requests.push(message);
      if (message.action === "info") report({ info: { revision: "sha256", mode: "concrete", max_steps: 10000, sequencers: [{ id: "s", uri,
        file: "fixture.q1asm", registers: ["R0", "R2"], defs: [{ name: "X", value: "0", literal: "0", line: 1 }, { name: "Y", value: "10", literal: "10", line: 2 }] },
      { id: "t", uri: "file:///other.q1asm", file: "other.q1asm", registers: ["R8"], defs: [] }] } });
      if (message.action === "plan") window.postMessage({ type: "defSweepPlan", project, token: message.token, plan }, "*");
      if (message.action !== "sweep") return;
      const { registers, observe_time: observeTime } = message;
      const output = (words, time) => ({ words: Object.fromEntries(registers.map((name) => [name, words[name] ?? null])),
        ...(observeTime ? { q1_ns: time } : {}) });
      if (message.action === "sweep" && message.statistics_only) {
        const metrics = (value) => ({ count: 1, min: value, max: value, mean: value, deviation: 0 });
        report({ sweep: { id: "stats-sweep", status: "cancelled", plan, sequencer: "s", mode: "concrete", maxSteps: 10000,
          registers, observeTime, defs: { X: "0", Y: "10" }, rows: [], statisticsOnly: true,
          summary: { count: 1, engine_completed: 1, statuses: { completed: 1 },
            registers: { R0: { signed: metrics(3), unsigned: metrics(3) }, R2: { signed: metrics(-1), unsigned: metrics(4294967295) } },
            ...(observeTime ? { time: metrics(32) } : {}) },
        } });
      } else report({ sweep: { id: "test-sweep", status: "cancelled", plan, sequencer: "s", mode: "concrete", maxSteps: 10000,
        registers, observeTime, defs: { X: "0", Y: "10" }, rows: [
          { id: 1, status: "completed", engine_completed: true, defs: { X: "0", Y: "10" }, ...output({ R0: 3, R2: 4294967295 }, 32), diagnostics: [{ severity: "warning", message: "Sample warning" }] },
          { id: 2, status: "blocked", engine_completed: false, defs: { X: "0", Y: "11" }, ...output({}, 16), diagnostics: [] },
          { id: 3, status: "undetermined", engine_completed: true, defs: { X: "1", Y: "10" }, ...output({}, 48), diagnostics: [] },
        ] } });
    } };
    const toolbar = document.createElement("div");
    toolbar.innerHTML = '<span aria-label="Timeline layout"><button>Instruction</button></span>';
    document.getElementById("hardware").before(toolbar);
    q1lensDefRuns.mount(toolbar.firstElementChild, bridge, project);
  });
  const sweepToggle = page.getByRole("button", { name: "Sweep", exact: true });
  await expect(sweepToggle).toHaveAttribute("aria-expanded", "false");
  await sweepToggle.click();
  await expect(sweepToggle).toHaveAttribute("aria-expanded", "true");
  const r0 = page.getByRole("checkbox", { name: "R0", exact: true });
  const r2 = page.getByRole("checkbox", { name: "R2", exact: true });
  const r63 = page.getByRole("checkbox", { name: "R63", exact: true });
  const time = page.getByRole("checkbox", { name: "Q1 time", exact: true });
  const r2Stats = page.locator('[data-statistics="R2"]');
  await page.getByRole("combobox", { name: "Sequencer" }).selectOption("t");
  await expect(page.getByRole("checkbox", { name: "R8", exact: true })).toBeChecked();
  await expect(r2).toBeHidden();
  await page.getByRole("combobox", { name: "Sequencer" }).selectOption("s");
  await expect(r0).toBeChecked();
  await expect(r63).toBeHidden();
  await page.getByRole("button", { name: "Show all registers" }).click();
  await expect(page.locator(".def-register-choices").getByRole("checkbox")).toHaveCount(64);
  await r63.check();
  await page.getByRole("button", { name: "Show referenced registers" }).click();
  await expect(r63).toBeHidden();
  await expect(page.locator('[data-field="observed-count"]')).toHaveText("· 2 selected · 1 hidden");
  await page.getByRole("button", { name: "Show all registers" }).click();
  await expect(r63).toBeChecked();
  await r63.uncheck();
  await page.getByRole("button", { name: "Show referenced registers" }).click();
  await expect(time).not.toBeChecked();
  await r0.uncheck();
  await expect(page.getByText("Select at least one register or Q1 time.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Start sweep" })).toBeDisabled();
  await r0.check();
  await r2.check();
  await time.check();
  await page.getByRole("combobox", { name: "Sweep axis 2" }).selectOption("Y");
  await page.getByRole("button", { name: "Start sweep" }).click();
  await expect(page.locator('[data-status="cancelled"]')).toHaveText("Cancelled");
  await expect(page.locator('[data-cases-reported]')).toHaveText("3 / 4 cases reported");
  await expect(page.getByText("Blocked 1", { exact: true })).toBeVisible();
  await expect(r2Stats.locator('[data-stat="count"]')).toHaveText("1");
  await expect(r2Stats.locator('[data-stat="mean"]')).toHaveText("-1");
  await expect(page.locator('[data-statistics="R0"] [data-stat="mean"]')).toHaveText("3");
  await page.getByLabel("Plot metric").selectOption("R2");
  await expect(page.locator('[aria-label="Two-axis DEF heatmap by R2"] .def-plot-point')).toHaveCount(3);
  await expect(page.locator('[aria-label="Two-axis DEF heatmap by R2"] .def-plot-unrun')).toBeVisible();
  await expect(page.getByText("warning: Sample warning")).toBeVisible();
  await page.getByRole("combobox", { name: "Interpretation" }).selectOption("unsigned");
  await expect(r2Stats.locator('[data-stat="mean"]')).toHaveText("4294967295");
  await expect(r2Stats.locator('[data-stat="deviation"]')).toHaveText("0");
  await page.getByLabel("Plot metric").selectOption("time");
  const timePlot = page.locator('svg[aria-label="Two-axis DEF heatmap by Q1 time"]');
  await expect(timePlot.locator('[data-case="2"]')).toHaveClass(/blocked/);
  await expect(timePlot.locator('[data-case="3"]')).toHaveAttribute("aria-label", /Q1 time 48 ns/);
  await expect(page.getByRole("heading", { name: "Q1 time frequency" })).toBeVisible();
  await expect(page.locator(".def-histogram")).toContainText("ns");
  await expect(r2Stats.locator('[data-stat="mean"]')).toHaveText("4294967295");
  await page.setViewportSize({ width: 390, height: 780 });
  const layout = await page.locator("#q1timeline-def-runs").evaluate((panel) => {
    const cards = [...panel.querySelector(".def-setup").children].map((card) => card.getBoundingClientRect());
    const plot = panel.querySelector(".def-plot-scroll");
    return { stacked: cards[1].top >= cards[0].bottom,
      panelFits: panel.scrollWidth <= panel.clientWidth, plotFits: plot.scrollWidth <= plot.clientWidth };
  });
  expect(layout).toEqual({ stacked: true, panelFits: true, plotFits: true });
  await sweepToggle.click();
  await expect(timePlot).toBeHidden();
  await sweepToggle.click();
  await expect(timePlot).toBeVisible();
  await timePlot.locator('[data-case="3"]').focus();
  await page.keyboard.press("Enter");
  await expect.poll(() => page.evaluate(() => window.requests.at(-1))).toMatchObject({ action: "case", sweepId: "test-sweep", caseId: 3 });
  const formula = page.getByRole("textbox", { name: "Formula", exact: true });
  await formula.fill("atan2(Y, X)");
  await page.getByRole("button", { name: "Plot formula" }).click();
  const formulaPlot = page.locator('svg[aria-label="Two-axis DEF heatmap by Formula"]');
  await expect(formulaPlot.locator('[data-case="1"]')).toHaveAttribute("aria-label", /Formula 1.57079632679/);
  await expect(formulaPlot.locator('[data-case="3"]')).toHaveAttribute("aria-label", /Formula 1.4711276743/);
  await expect(page.locator('[data-field="formula-status"]')).toHaveText("2 of 3 cases produced a finite formula value.");
  await formulaPlot.locator('[data-case="1"]').click();
  await expect.poll(() => page.evaluate(() => window.requests.at(-1))).toMatchObject({ action: "case", sweepId: "test-sweep", caseId: 1 });
  await formula.fill("atan2(R1, R0)");
  await formula.press("Enter");
  await expect(page.locator('[data-field="formula-status"]')).toHaveText("Select R1 and run a new sweep to use it.");
  expect(await page.evaluate(() => window.requests.filter((request) => request.action === "sweep").length)).toBe(1);
  await page.getByRole("combobox", { name: "Results", exact: true }).selectOption("statistics");
  await time.uncheck();
  await page.getByRole("button", { name: "Start sweep" }).click();
  await expect(page.locator('[data-time-statistics]')).toHaveCount(0);
  await expect(r2Stats.locator('[data-stat="mean"]')).toHaveText("4294967295");
  await expect.poll(() => page.evaluate(() => window.requests.filter((request) => request.action === "sweep").at(-1)))
    .toMatchObject({ registers: ["R0", "R2"], observe_time: false });
  await expect(page.getByLabel("Plot metric")).toBeHidden();
  await expect(formula).toBeHidden();
  await expect(page.locator('svg [data-case], button[data-case]')).toHaveCount(0);
});
