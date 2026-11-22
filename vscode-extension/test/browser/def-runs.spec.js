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
      if (message.action === "plan") {
        const axes = message.axes.map(({ name, min, max, step }) => ({ name, min, last: max, step,
          count: String(Math.floor((Number(max) - Number(min)) / Number(step)) + 1) }));
        const total = axes.reduce((n, axis) => n * Number(axis.count), 1);
        window.postMessage({ type: "defSweepPlan", project, token: message.token,
          plan: { ...plan, axes, total: String(total), limit: 4096, allowed: total <= 4096 } }, "*");
      }
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
  const start = page.getByRole("button", { name: "Start sweep" });
  const range = (name) => page.getByRole("textbox", { name, exact: true });
  await expect(page.locator('[data-field="plan"]')).toHaveText("10 combinations");
  await expect(start).toBeEnabled();
  await range("X maximum").fill("1");
  await expect(range("X minimum")).toHaveValue("0");
  await range("X minimum").fill("");
  await expect(range("X minimum")).toHaveAttribute("placeholder", "0");
  await range("Y maximum").fill("11");
  await expect(page.locator('[data-field="plan"]')).toHaveText("4 combinations");
  await expect(page.locator('[data-def-row="X"] [data-axis-count]')).toHaveText("2");
  await expect(start).toBeEnabled();
  await r0.uncheck();
  await expect(page.getByText("Select at least one register or Q1 time.")).toBeVisible();
  await expect(start).toBeDisabled();
  await r0.check();
  await r2.check();
  await time.check();
  await start.click();
  expect(await page.evaluate(() => window.requests.filter((request) => request.action === "sweep").at(-1).axes)).toEqual([
    { name: "X", min: "0", max: "1", step: "1" }, { name: "Y", min: "10", max: "11", step: "1" }]);
  await expect(page.locator('[data-status="cancelled"]')).toHaveText("Cancelled");
  await expect(page.locator('[data-cases-reported]')).toHaveText("3 / 4 cases");
  await expect(page.getByText("1 blocked", { exact: true })).toBeVisible();
  await expect(page.locator(".def-counts li")).toHaveText(["1 readable", "1 undetermined", "1 blocked", "1 unexecuted"]);
  await expect(page.locator(".def-summary")).toHaveAttribute("title", "s · Observed R0, R2, Q1 time · concrete · 10000 instruction limit · Engine completed 2");
  const cases = page.getByRole("table", { name: "Sweep cases" });
  await expect(page.getByRole("button", { name: "Needs attention 3" })).toHaveAttribute("aria-pressed", "true");
  await expect(cases.locator("tbody tr")).toHaveCount(3);
  await expect(cases.locator("tbody tr td:nth-child(3)")).toHaveText(["", "Blocked", "Final output undetermined"]);
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
    const plot = panel.querySelector(".def-plot-scroll"), inputs = panel.querySelector(".def-input-table");
    return { inputsStayInside: inputs.getBoundingClientRect().right <= panel.getBoundingClientRect().right,
      panelFits: panel.scrollWidth <= panel.clientWidth, plotFits: plot.scrollWidth <= plot.clientWidth };
  });
  expect(layout).toEqual({ inputsStayInside: true, panelFits: true, plotFits: true });
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
  await range("X maximum").fill("4096");
  await expect(page.locator('[data-field="plan"]')).toHaveText("8194 combinations · Maximum 4,096 combinations.");
  await expect(start).toBeDisabled();
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
  await expect(page.getByRole("region", { name: "Random sample results" })).toBeVisible();
  await expect(page.locator('[data-cases-reported]')).toHaveText("4 / 5 samples");
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

test("DEF sweep keeps one input table, a one-line result and exceptions first", async ({ page }) => {
  await page.goto("/?case=01-digital");
  const src = path.join(__dirname, "../../src");
  await page.addStyleTag({ path: path.join(src, "q1timeline/media/timeline.css") });
  await page.addScriptTag({ path: path.join(src, "q1timeline/media/defRuns.js") });
  await page.evaluate(() => {
    const project = "fixture.yml", uri = "file:///fixture.q1asm";
    const report = (patch) => window.postMessage({ type: "defRunState", project, patch }, "*");
    window.requests = [];
    const bridge = { postMessage(message) {
      window.requests.push(message);
      if (message.action === "info") report({ info: { revision: "sha256", mode: "concrete", max_steps: 10000, sequencers: [{ id: "s", uri,
        file: "fixture.q1asm", registers: ["R0"], defs: [{ name: "N", value: "4", literal: "0x4", line: 1 },
          { name: "DEST", value: null, literal: "R0", line: 2, reason: "Register alias." },
          { name: "GAIN", value: "2", literal: "{gain}", line: 3 }, { name: "OFFSET", value: "10", literal: "10", line: 4 }] },
        { id: "unresolved", uri: "file:///unresolved.q1asm", file: "unresolved.q1asm", registers: ["R1"],
          defs: [{ name: "SIZE", value: null, literal: "{size}", line: 1, needs_input: true }] },
        { id: "large", uri: "file:///large.q1asm", file: "large.q1asm", registers: [],
          defs: [{ name: "ADDR", value: null, literal: "@done", line: 1, reason: "Label address." },
            { name: "LIMIT", value: "2147483647", literal: "0x7fffffff", line: 2 },
            { name: "MIXED", value: "12", literal: "1٢", line: 3 }] },
        { id: "aliases", uri: "file:///aliases.q1asm", file: "aliases.q1asm", registers: ["R1"],
          defs: [{ name: "REG", value: null, literal: "R1", line: 1, reason: "Register alias." }] }] } });
      const axes = (message.axes || []).map(({ name, min, max, step }) => ({ name, min, last: max, step,
        count: String((Number(max) - Number(min)) / Number(step) + 1) }));
      const plan = { axes, total: String(axes.reduce((total, axis) => total * Number(axis.count), 1)), allowed: true, sample_limit: 25_000 };
      if (message.action === "plan") window.postMessage({ type: "defSweepPlan", project, token: message.token, plan }, "*");
      if (message.sequencer === "unresolved" && message.action === "sweep") {
        const rows = [2, 3, 4].map((value, i) => ({ id: i + 1, status: "completed", engine_completed: true,
          defs: { SIZE: String(value) }, words: { R1: value }, diagnostics: [] }));
        report({ sweep: { id: "manual", status: "completed", plan, sequencer: "unresolved", mode: "concrete", maxSteps: 10000,
          registers: message.registers, observeTime: false, defs: { SIZE: message.axes[0].min }, rows } });
        return;
      }
      if (message.sweepId === "manual" && message.action === "case") {
        const value = message.caseId + 1;
        report({ trace: { id: "manual-case", sweepId: "manual", sequencer: "unresolved", registers: ["R1"], mode: "concrete", maxSteps: 10000,
          ir: { sequencers: [{ id: "unresolved" }], events: [] }, literals: { SIZE: String(value) },
          case: { id: message.caseId, status: "completed", engine_completed: true, defs: { SIZE: String(value) }, words: { R1: value }, diagnostics: [] } } });
        return;
      }
      if (message.action === "run") report({ trace: { id: "run", sequencer: "s", registers: ["R0"], observeTime: false, mode: "concrete", maxSteps: 10000,
        ir: { sequencers: [{ id: "s" }], events: [] }, literals: message.overrides,
        case: { id: 0, status: "completed", engine_completed: true, defs: message.overrides, words: { R0: 1 }, diagnostics: [] } } });
      if (message.action !== "sweep") return;
      // Five cases: three plain results, one with a warning, one blocked.
      const rows = [[1, "completed", 1], [2, "completed", 2], [3, "completed", 4], [4, "completed", 4], [5, "blocked", null]].map(([id, status, word]) => ({
        id, status, engine_completed: status === "completed", defs: { GAIN: "0", N: String(id) }, words: { R0: word }, q1_ns: 16 * id,
        diagnostics: id === 4 ? [{ severity: "warning", message: "Sample warning" }] : [] }));
      report({ sweep: { id: "five", status: "completed", plan: { ...plan, total: "5" }, sequencer: "s", mode: "concrete", maxSteps: 10000,
        registers: message.registers, observeTime: message.observe_time, defs: { N: "4", GAIN: "2", OFFSET: "10" }, rows } });
    } };
    const toolbar = document.createElement("div");
    toolbar.innerHTML = '<span aria-label="Timeline layout"><button>Instruction</button></span>';
    document.getElementById("hardware").before(toolbar);
    q1lensDefRuns.mount(toolbar.firstElementChild, bridge, project, { open: true });
  });
  const panel = page.locator("#q1timeline-def-runs");
  const input = (name) => panel.getByRole("textbox", { name, exact: true });
  const axis = (name) => panel.getByRole("checkbox", { name: `Sweep ${name}`, exact: true });
  await expect(panel.getByRole("heading")).toHaveText(["Sweep"]);
  await expect(panel.getByRole("table", { name: "DEF inputs" }).locator("thead th:visible")).toHaveText(["Sweep DEF", "Declaration", "Run input", "Min", "Max", "Step", ""]);
  await expect(input("N DEF value")).toHaveValue("0x4");
  await expect(axis("N")).toBeChecked();
  await expect(axis("GAIN")).not.toBeChecked();
  await expect(input("N minimum")).toHaveValue("4");
  await expect(input("N maximum")).toHaveValue("13");
  await expect(input("N step")).toHaveValue("1");
  await expect(panel.locator('[data-field="plan"]')).toHaveText("10 combinations");
  await axis("GAIN").check();
  await expect(panel.locator('[data-field="plan"]')).toHaveText("100 combinations");
  await expect(axis("OFFSET")).toBeDisabled();
  await axis("N").uncheck();
  await expect(panel.locator('[data-field="plan"]')).toHaveText("10 combinations");
  await expect(panel.locator('[data-def-row="GAIN"] td').first()).toHaveText("{gain}");
  await expect(input("GAIN DEF value")).toHaveValue("2");
  const alias = panel.locator('[data-def-row="DEST"]');
  await expect(alias).toContainText("Register alias.");
  await expect(alias.locator("input")).toHaveCount(0);
  await alias.getByRole("button", { name: "Open DEST at line 2" }).click();
  expect(await page.evaluate(() => window.requests.at(-1))).toMatchObject({ type: "sourceClick", line: 2 });

  // Reset controls and the last-run column appear only when they have something to act on or show.
  const reset = panel.getByRole("button", { name: "Reset OFFSET DEF" }), resetAll = panel.getByRole("button", { name: "Reset all DEFs" });
  await expect(reset).toBeHidden();
  await expect(resetAll).toBeHidden();
  await input("OFFSET DEF value").fill("11");
  await expect(reset).toBeVisible();
  await expect(resetAll).toBeVisible();
  await panel.getByRole("button", { name: "Run from start" }).click();
  expect(await page.evaluate(() => window.requests.find((request) => request.action === "run").overrides))
    .toEqual({ N: "0x4", GAIN: "2", OFFSET: "11" });
  await expect(panel.locator('[data-def-row="OFFSET"] [data-executed]')).toHaveText("11");
  await expect(panel.getByRole("columnheader", { name: "Last run" })).toBeVisible();
  await reset.click();
  await expect(input("OFFSET DEF value")).toHaveValue("10");
  await expect(reset).toBeHidden();

  // Editing a range selects it. The first selected row is the horizontal axis.
  await input("GAIN maximum").fill("3");
  await expect(input("GAIN minimum")).toHaveValue("2");
  await input("N minimum").fill("1");
  await input("N maximum").fill("5");
  await expect(input("OFFSET minimum")).toBeDisabled();
  await expect(axis("OFFSET")).toBeDisabled();
  await expect(panel.locator('[data-field="plan"]')).toHaveText("10 combinations");
  await expect.poll(() => page.evaluate(() => window.requests.filter((request) => request.action === "plan").at(-1).axes)).toEqual([
    { name: "GAIN", min: "2", max: "3", step: "1" }, { name: "N", min: "1", max: "5", step: "1" }]);
  await axis("GAIN").uncheck();
  await expect(input("GAIN maximum")).toHaveValue("3");
  await expect(input("OFFSET minimum")).toBeEnabled();
  await expect(panel.locator('[data-def-row="GAIN"] [data-axis-count]')).toBeEmpty();
  await expect(panel.locator('[data-def-row="N"] [data-axis-count]')).toHaveText("5");
  const primary = await panel.getByRole("button").evaluateAll((buttons) => {
    const fill = getComputedStyle(buttons.find((button) => button.dataset.action === "sweep")).backgroundColor;
    return buttons.filter((button) => button.offsetParent && getComputedStyle(button).backgroundColor === fill).map((button) => button.textContent);
  });
  expect(primary).toEqual(["Start sweep"]);
  await panel.getByRole("checkbox", { name: "Q1 time", exact: true }).check();
  await panel.getByRole("button", { name: "Start sweep" }).click();
  expect(await page.evaluate(() => window.requests.at(-1))).toMatchObject({ action: "sweep", statistics_only: false,
    axes: [{ name: "N", min: "1", max: "5", step: "1" }], overrides: { N: "0x4", GAIN: "2", OFFSET: "10" } });

  // One result line: completion mark, reported cases, proportions and non-zero counts. Settings stay in its tooltip.
  const summary = panel.locator(".def-summary");
  await expect(summary.locator('[data-status="completed"]')).toHaveAccessibleName("Completed");
  await expect(summary.locator("[data-cases-reported]")).toHaveText("5 / 5 cases");
  await expect(summary.locator(".def-counts li")).toHaveText(["4 readable", "1 blocked"]);
  expect(await summary.locator(".def-bar i").evaluateAll((parts) => parts.map((part) => part.style.flexGrow))).toEqual(["4", "1"]);
  await expect(summary).toHaveAttribute("title", "s · Observed R0, Q1 time · concrete · 10000 instruction limit · Fixed DEFs GAIN=2, OFFSET=10 · Engine completed 4");
  await expect(panel.locator('[data-field="progress"]')).not.toContainText("instruction limit");

  // Rounded statistics keep their exact value.
  const mean = panel.locator('[data-statistics="R0"] [data-stat="mean"]'), deviation = panel.locator('[data-statistics="R0"] [data-stat="deviation"]');
  await expect(panel.getByRole("region", { name: "Observation statistics" }).locator("thead th")).toHaveText(["", "n", "min", "max", "mean", "sd"]);
  await expect(mean).toHaveText("2.75");
  await expect(deviation).toHaveText("1.299");
  await expect(deviation).toHaveAttribute("title", "1.29903810568");
  await expect(panel.locator('[data-time-statistics] [data-stat="mean"]')).toHaveText("40");
  await expect(panel.locator('[data-time-statistics] [data-stat="mean"]')).not.toHaveAttribute("title");

  // Cases open on the ones that need a look. The full list is one click away.
  const rows = panel.getByRole("table", { name: "Sweep cases" }).locator("tbody tr");
  const attention = panel.getByRole("button", { name: "Needs attention 2" }), all = panel.getByRole("button", { name: "All 5" });
  await expect(attention).toHaveAttribute("aria-pressed", "true");
  await expect(rows.locator("th")).toHaveText(["4", "5"]);
  await expect(rows.nth(0)).toContainText("warning: Sample warning");
  await expect(rows.nth(1)).toContainText("Blocked");
  await all.click();
  await expect(all).toHaveAttribute("aria-pressed", "true");
  await expect(rows).toHaveCount(5);
  await expect(rows.locator("td:nth-child(3)")).toHaveText(["", "", "", "", "Blocked"]);
  await panel.getByRole("button", { name: "Case 2", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.requests.at(-1))).toMatchObject({ action: "case", sweepId: "five", caseId: 2 });

  // Plot text is drawn at its own size instead of growing with the panel.
  const plot = panel.locator('svg[aria-label="DEF input versus R0"]');
  expect(await plot.evaluate((svg) => svg.getBoundingClientRect().width <= svg.viewBox.baseVal.width)).toBe(true);
  const seq = panel.getByRole("combobox", { name: "Sequencer" }), start = panel.getByRole("button", { name: "Start sweep" });
  await seq.selectOption("aliases");
  await expect(panel.locator('[data-def-row="REG"]')).toContainText("Register alias.");
  await expect(panel.locator('[data-field="plan"]')).toBeEmpty();
  await expect(start).toBeDisabled();

  await seq.selectOption("large");
  await expect(axis("LIMIT")).toBeChecked();
  await expect(input("LIMIT maximum")).toHaveValue("2147483647");
  const min = BigInt(await input("LIMIT minimum").inputValue());
  const step = BigInt(await input("LIMIT step").inputValue());
  expect(step).toBeGreaterThan(1n);
  expect(min + 9n * step).toBe(2147483647n);
  await expect(panel.locator('[data-field="plan"]')).toHaveText("10 combinations");
  await expect(panel.getByRole("checkbox", { name: "Q1 time", exact: true })).toBeChecked();
  await expect(start).toBeEnabled();
  await axis("LIMIT").uncheck();
  await expect(start).toBeDisabled();
  await axis("MIXED").check();
  await expect(input("MIXED minimum")).toHaveValue("12");
  await expect(input("MIXED maximum")).toHaveValue("21");
  await expect(panel.locator('[data-field="plan"]')).toHaveText("10 combinations");

  await seq.selectOption("unresolved");
  await expect(input("SIZE DEF value")).toHaveValue("");
  await expect(axis("SIZE")).toBeChecked();
  await expect(input("SIZE minimum")).toHaveValue("0");
  await expect(input("SIZE maximum")).toHaveValue("9");
  await expect(panel.locator('[data-field="plan"]')).toHaveText("10 combinations");
  await input("SIZE maximum").fill("");
  await expect(start).toBeDisabled();
  await input("SIZE minimum").fill("2");
  await input("SIZE maximum").fill("2");
  await expect(panel.locator('[data-def-row="SIZE"] [data-axis-count]')).toHaveText("1");
  await input("SIZE maximum").fill("4");
  await expect(panel.locator('[data-field="plan"]')).toHaveText("3 combinations");
  await start.click();
  expect(await page.evaluate(() => window.requests.at(-1))).toMatchObject({ action: "sweep", sequencer: "unresolved",
    axes: [{ name: "SIZE", min: "2", max: "4", step: "1" }] });
  expect(await page.evaluate(() => window.requests.at(-1).overrides)).toEqual({});
  await expect(panel.getByRole("table", { name: "Sweep cases" }).locator("tbody tr td:nth-child(2)")).toHaveText(["SIZE=2", "SIZE=3", "SIZE=4"]);
  await input("Formula").fill("$SIZE * 2");
  await panel.getByRole("button", { name: "Plot formula" }).click();
  await expect(panel.locator('svg[aria-label="DEF input versus Formula"] [data-case="2"]')).toHaveAccessibleName(/Formula 6/);
  await panel.getByRole("button", { name: "Case 2", exact: true }).click();
  await expect(panel.locator('[data-def-row="SIZE"] [data-executed]')).toHaveText("3");
  await expect(input("SIZE DEF value")).toHaveValue("");
  await input("SIZE DEF value").fill("8");
  await expect(input("SIZE minimum")).toHaveValue("2");
  await expect(input("SIZE maximum")).toHaveValue("4");
  await axis("SIZE").uncheck();
  await axis("SIZE").check();
  await expect(panel.locator('[data-field="plan"]')).toHaveText("3 combinations");
  await panel.getByRole("button", { name: "Run from start" }).click();
  expect(await page.evaluate(() => window.requests.at(-1).overrides)).toEqual({ SIZE: "8" });
});
