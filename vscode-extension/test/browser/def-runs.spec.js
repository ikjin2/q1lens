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
    const plan = { axes, total: "4", allowed: true, sample_limit: 25_000 };
    const report = (patch) => window.postMessage({ type: "defRunState", project, patch }, "*");
    window.requests = [];
    const bridge = { postMessage(message) {
      window.requests.push(message);
      if (message.action === "info") report({ info: { revision: "sha256", mode: "concrete", max_steps: 10000, sequencers: [{ id: "s", uri,
        file: "fixture.q1asm", registers: ["R0", "R2"], defs: [{ name: "X", value: "0", literal: "0", line: 1 }, { name: "Y", value: "10", literal: "10", line: 2 }] },
      { id: "t", uri: "file:///other.q1asm", file: "other.q1asm", registers: ["R8"], defs: [] }] } });
      if (message.action === "plan") window.postMessage({ type: "defSweepPlan", project, token: message.token,
        plan: message.axes[0].max === "4096" ? { ...plan, total: "8194", allowed: false, limit: 4096 } : plan }, "*");
      if (message.action === "estimate") {
        const { type, project, action, ...inputs } = message;
        report({ estimate: { inputs, setupMs: 10, sampleMs: 10, count: 16 } });
      }
      if (message.action !== "sweep") return;
      const { registers, observe_time: observeTime } = message;
      const jobPlan = message.sample_count ? { ...plan, total: String(message.sample_count), sampling: "random" } : plan;
      const output = (words, time) => ({ words: Object.fromEntries(registers.map((name) => [name, words[name] ?? null])),
        ...(observeTime ? { q1_ns: time } : {}) });
      if (message.action === "sweep" && message.statistics_only) {
        const metrics = (value) => ({ count: 1, min: value, max: value, mean: value, deviation: 0 });
        report({ sweep: { id: "stats-sweep", status: "cancelled", plan: jobPlan, sequencer: "s", mode: "concrete", maxSteps: 10000,
          registers, observeTime, defs: { X: "0", Y: "10" }, rows: [], statisticsOnly: true,
          summary: { count: 1, engine_completed: 1, statuses: { completed: 1 },
            registers: { R0: { signed: metrics(3), unsigned: metrics(3) }, R2: { signed: metrics(-1), unsigned: metrics(4294967295) } },
            ...(observeTime ? { time: metrics(32) } : {}) },
        } });
      } else {
        const rows = [
          { id: 1, status: "completed", engine_completed: true, defs: { X: "0", Y: "10" }, ...output({ R0: 3, R2: 4294967295 }, 32), diagnostics: [{ severity: "warning", message: "Sample warning" }] },
          { id: 2, status: "blocked", engine_completed: false, defs: { X: "0", Y: "11" }, ...output({}, 16), diagnostics: [] },
          { id: 3, status: "undetermined", engine_completed: true, defs: { X: "1", Y: "10" }, ...output({}, 48), diagnostics: [] },
        ];
        if (message.sample_count) {
          rows[0].defs = { X: "1", Y: "11" };
          rows.push({ ...rows[0], id: 4 });
        }
        report({ sweep: { id: message.sample_count ? "random-sweep" : "test-sweep", status: "cancelled", plan: jobPlan,
          sequencer: "s", mode: "concrete", maxSteps: 10000, registers, observeTime, defs: { X: "0", Y: "10" }, rows } });
      }
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
  await formula.fill("R0");
  await page.getByLabel("Reference formula").fill("Y / 5");
  const tolerance = page.getByLabel("Absolute tolerance");
  await tolerance.fill("1");
  await formula.press("Enter");
  const errorPlot = page.locator('svg[aria-label="Two-axis DEF heatmap by Error"]');
  await expect(formulaPlot.locator('[data-case="1"]')).toHaveAttribute("aria-label", /Formula 3 · Pass/);
  await expect(errorPlot.locator('[data-case="1"]')).toHaveAttribute("aria-label", /Error 1 · Pass/);
  await expect(page.locator('[data-comparison="pass"]')).toHaveText("1");
  await expect(page.locator('[data-comparison="unavailable"]')).toHaveText("2");
  await tolerance.fill("0.5");
  await formula.press("Enter");
  await expect(errorPlot.locator('[data-case="1"]')).toHaveAttribute("data-verdict", "fail");
  await expect(page.getByRole("table", { name: "Sweep cases" }).locator('td[data-verdict="fail"]')).toHaveText("Fail");
  await expect(page.locator('[data-comparison="fail"]')).toHaveText("1");
  await expect(page.locator('[data-comparison="max-error"]')).toHaveText("1");
  await page.getByLabel("Plot metric").selectOption("reference");
  await expect(page.locator('svg[aria-label="Two-axis DEF heatmap by Reference"] [data-case="1"]')).toHaveAttribute("aria-label", /Reference 2 · Fail/);
  await tolerance.fill("-1");
  await formula.press("Enter");
  await expect(page.locator('[data-field="formula-status"]')).toContainText("non-negative absolute tolerance");
  await expect(page.locator('[data-comparison="tolerance"]')).toHaveText("0.5");
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
  await expect(page.getByRole("heading", { name: "Reference comparison" })).toBeHidden();
  await expect(page.locator('svg [data-case], button[data-case]')).toHaveCount(0);

  await page.getByRole("combobox", { name: "Results", exact: true }).selectOption("cases");
  await page.getByLabel("Axis 1 max").fill("4096");
  await expect(page.getByRole("button", { name: "Start sweep" })).toBeDisabled();
  const random = page.getByRole("button", { name: "Random sample" });
  const count = page.getByRole("spinbutton", { name: "Sample count" });
  await expect(random).toBeEnabled();
  await count.fill("25001");
  await expect(random).toBeDisabled();
  await expect(page.locator('[data-field="sample-help"]')).toContainText("1 to 25,000");
  await count.fill("1000");
  await page.getByRole("button", { name: "Estimate time" }).click();
  const estimate = page.locator('[data-field="estimate"]');
  await expect(estimate).toHaveText("Estimated time: about 11 s (measured on 16 samples).");
  await count.fill("25000");
  await expect(estimate).toHaveText("Estimated time: about 4 min 11 s (measured on 16 samples).");
  await time.check();
  await expect(estimate).toBeEmpty();
  await time.uncheck();
  await count.fill("5");
  await random.click();
  await expect(page.getByRole("heading", { name: "Random sample results" })).toBeVisible();
  await expect(page.locator('[data-cases-reported]')).toHaveText("4 / 5 samples reported");
  await formula.press("Enter");
  await expect(page.locator('[data-field="formula-status"]')).toContainText("non-negative absolute tolerance");
  await tolerance.fill("0.5");
  await formula.press("Enter");
  await expect(formulaPlot.locator('[data-case="1"]')).toHaveAttribute("aria-label", /X=1, Y=11 · 2 samples.*Formula 3/);
  const sample = errorPlot.locator('[data-case="1"]');
  await expect(sample).toHaveAttribute("aria-label", /Error 0.8 · Fail/);
  expect(Number(await sample.getAttribute("x"))).toBeGreaterThan(Number(await errorPlot.locator('[data-case="2"]').getAttribute("x")));
  expect(Number(await sample.getAttribute("y"))).toBeLessThan(Number(await errorPlot.locator('[data-case="3"]').getAttribute("y")));
  await expect(errorPlot.locator('[data-case="4"]')).toHaveCount(0);
  await expect(page.getByText("Unsampled inputs").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Error frequency" })).toBeVisible();
  await expect(page.locator('[data-comparison="fail"]')).toHaveText("2");
  await sample.click();
  await expect.poll(() => page.evaluate(() => window.requests.at(-1))).toMatchObject({ action: "case", sweepId: "random-sweep", caseId: 1 });
  await expect.poll(() => page.evaluate(() => window.requests.filter((request) => request.action === "sweep").at(-1)))
    .toMatchObject({ sample_count: 5, statistics_only: false });
});
