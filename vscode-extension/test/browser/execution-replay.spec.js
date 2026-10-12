const { test, expect, open, value } = require("./helpers");

const replay = (page) => page.locator('[data-view="replay"]');
const step = (page) => page.locator('[data-action="replay-step"]');
const run = (page) => page.locator('[data-action="replay-run"]');
const replayRegister = (page, name) => value(page, "replay-registers").locator(`[data-replay-register="${name}"] strong`);
const projectCard = (page, id) => page.locator(`[data-replay-sequencer="${id}"]`);
const projectValue = (page, id, name) => projectCard(page, id).locator(`[data-value="project-${name}"]`);
const projectRegister = (page, id) => projectValue(page, id, "registers").locator('[data-replay-register="R0"] strong');

async function openJoint(page, scenario = "joint-sync-replay") {
  await page.goto(`/?case=${scenario}`);
  await page.getByRole("button", { name: "Open QCM0: 2 sequencers", exact: true }).click();
  await page.getByRole("button", { name: "Inspect sequencer early", exact: true }).click();
  await page.getByRole("button", { name: "Execution replay", exact: true }).click();
}

async function freezeClock(page) {
  await page.clock.install({ time: new Date("2026-10-04T12:00:00Z") });
  await page.clock.pauseAt(new Date("2026-10-04T12:00:01Z"));
}

test("execution replay is opt-in inside sequencer detail and fits a narrow view", async ({ page, request }) => {
  const manifest = await (await request.get("/manifest.json")).json();
  expect(manifest.extended_cases.find((item) => item.name === "concrete-replay")).toMatchObject({ exit_code: 0, expected_status: "completed" });
  await page.goto("/?case=concrete-replay");
  await expect(page.getByRole("button", { name: "Open QCM0: 1 sequencer", exact: true })).toBeVisible();
  await expect(replay(page)).toBeHidden();
  await expect(page.getByRole("button", { name: "Execution replay", exact: true })).toBeHidden();
  await page.getByRole("button", { name: "Open QCM0: 1 sequencer", exact: true }).click();
  await page.getByRole("button", { name: "Inspect sequencer counter", exact: true }).click();
  await expect(replay(page)).toBeHidden();
  await page.getByRole("button", { name: "Execution replay", exact: true }).click();
  await expect(replay(page)).toBeVisible();
  await expect(value(page, "replay-progress")).toHaveText("0 / 19 instructions");
  for (const name of ["R0", "R1", "R2"]) await expect(replayRegister(page, name)).toHaveText("?");
  await expect(run(page)).toHaveText("Run");
  await expect(page.getByRole("combobox", { name: "Execution replay speed" })).toHaveValue("450");
  await page.setViewportSize({ width: 480, height: 700 });
  await step(page).click();
  await expect(value(page, "replay-progress")).toHaveText("1 / 19 instructions");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("stepping follows loop occurrences and register readiness without draining RT time", async ({ page }) => {
  await open(page, "concrete-replay", "QCM0", "counter");
  await page.getByRole("button", { name: "Execution replay", exact: true }).click();
  const ends = [4, 8, 12, 16, 28, 32, 36, 60, 72, 76, 80, 104, 116, 120, 124, 128, 132, 136, 140];
  const lines = [2, 3, 4, 5, 6, 7, 8, 9, 6, 7, 8, 9, 6, 7, 8, 9, 10, 11, 12];
  for (let index = 0; index < ends.length; index++) {
    await step(page).click();
    await expect(page.getByRole("spinbutton")).toHaveValue(String(ends[index]));
    await expect(value(page, "replay-progress")).toHaveText(`${index + 1} / 19 instructions`);
    await expect(page.locator("[data-replay-source].is-active")).toHaveAttribute("data-source-line", String(lines[index]));
    if (index === 1) await expect(replayRegister(page, "R0")).toHaveText("?");
    if (index === 2) await expect(replayRegister(page, "R0")).toHaveText("3");
    if (index === 4) await expect(replayRegister(page, "R1")).toHaveText("0");
    if (index === 5) await expect(replayRegister(page, "R1")).toHaveText("1");
    if (index === 4 || index === 18) await page.screenshot({ path: test.info().outputPath(`replay-step-${index + 1}.png`), fullPage: true });
  }
  await expect(replayRegister(page, "R0")).toHaveText("0");
  await expect(replayRegister(page, "R1")).toHaveText("3");
  await expect(replayRegister(page, "R2")).toHaveText("3");
  await expect(step(page)).toBeDisabled();
  await expect(run(page)).toHaveText("Replay");
  await page.getByRole("button", { name: "Open instruction source", exact: true }).click();
  const source = await page.evaluate(() => window.revealedSource);
  expect(source.line).toBe(12);
  expect(source.file.replaceAll("\\", "/")).toMatch(/\/counter\.q1asm$/);
});

test("run can pause, finish, and replay from the beginning", async ({ page }) => {
  await freezeClock(page);
  await open(page, "concrete-replay", "QCM0", "counter");
  await page.getByRole("button", { name: "Execution replay", exact: true }).click();
  await run(page).click();
  await expect(run(page)).toHaveText("Pause");
  await page.clock.runFor(900);
  await run(page).click();
  await expect(run(page)).toHaveText("Run");
  const paused = await value(page, "replay-progress").textContent();
  expect(paused).not.toBe("0 / 19 instructions");
  await page.clock.runFor(2000);
  await expect(value(page, "replay-progress")).toHaveText(paused);
  await page.getByRole("combobox", { name: "Execution replay speed" }).selectOption("100");
  await run(page).click();
  await page.clock.runFor(2500);
  await expect(value(page, "replay-progress")).toHaveText("19 / 19 instructions");
  await expect(run(page)).toHaveText("Replay");
  await run(page).click();
  await expect(run(page)).toHaveText("Pause");
  await page.clock.runFor(100);
  await expect(value(page, "replay-progress")).not.toHaveText("19 / 19 instructions");
});

test("closing replay, leaving detail, and moving the cursor stop playback", async ({ page }) => {
  await freezeClock(page);
  await open(page, "concrete-replay", "QCM0", "counter");
  await page.getByRole("button", { name: "Execution replay", exact: true }).click();
  await run(page).click();
  await page.clock.runFor(450);
  await page.getByRole("button", { name: "Execution replay", exact: true }).click();
  await expect(replay(page)).toBeHidden();
  const closedTime = await page.getByRole("spinbutton").inputValue();
  await page.clock.runFor(2000);
  await expect(page.getByRole("spinbutton")).toHaveValue(closedTime);
  await page.getByRole("button", { name: "Execution replay", exact: true }).click();
  await expect(run(page)).toHaveText("Run");
  await run(page).click();
  await page.evaluate(() => window.hardwarePreview.setTime(32));
  await expect(run(page)).toHaveText("Run");
  await page.clock.runFor(2000);
  await expect(page.getByRole("spinbutton")).toHaveValue("32");
  await run(page).click();
  await page.getByRole("button", { name: "Zoom out one hardware level", exact: true }).click();
  await expect(replay(page)).toBeHidden();
  const overviewTime = await page.getByRole("spinbutton").inputValue();
  await page.clock.runFor(2000);
  await expect(page.getByRole("spinbutton")).toHaveValue(overviewTime);
});

test("changing sequencers stops replay and closes its detail panel", async ({ page }) => {
  await freezeClock(page);
  await page.goto("/?case=bin-wrap-preview");
  await page.getByRole("button", { name: "Open QRM0: 2 sequencers", exact: true }).click();
  await page.getByRole("button", { name: "Inspect sequencer red_tracker", exact: true }).click();
  await page.getByRole("button", { name: "Execution replay", exact: true }).click();
  await run(page).click();
  await page.clock.runFor(450);
  await page.getByRole("combobox", { name: "Hardware sequencer" }).selectOption("red_cursor");
  await expect(replay(page)).toBeHidden();
  const changedTime = await page.getByRole("spinbutton").inputValue();
  await page.clock.runFor(2000);
  await expect(page.getByRole("spinbutton")).toHaveValue(changedTime);
});

test("partial execution keeps its failure visible in replay", async ({ page }) => {
  await open(page, "04-underflow", "QCM0", "starved");
  await page.getByRole("button", { name: "Execution replay", exact: true }).click();
  await expect(replay(page)).toBeVisible();
  const count = await page.evaluate(() => window.demoIr.events.filter((event) => event.kind === "q1_issue").length);
  for (let index = 0; index < count; index++) await step(page).click();
  await expect(value(page, "replay-status")).toContainText(/underflow|partial|error/i);
});

test("missing instruction trace explains how to enable replay", async ({ page }) => {
  await page.route("**/ir/concrete-replay.json", async (route) => {
    const response = await route.fetch();
    const ir = await response.json();
    ir.events = ir.events.filter((event) => event.kind !== "q1_issue");
    await route.fulfill({ response, json: ir });
  });
  await open(page, "concrete-replay", "QCM0", "counter");
  await page.getByRole("button", { name: "Execution replay", exact: true }).click();
  await expect(value(page, "replay-status")).toContainText(/concrete|simulation/i);
  await expect(run(page)).toBeHidden();
  await expect(step(page)).toBeHidden();
  await expect(value(page, "replay-progress")).toBeHidden();
  await expect(value(page, "replay-registers")).toBeHidden();
  await expect(page.getByRole("button", { name: "Generate execution trace", exact: true })).toBeVisible();
});

for (const missing of ["static analysis", "register history"]) {
  test(`${missing} offers trace generation and restores open replay after analysis`, async ({ page }) => {
    let generatedIr;
    await page.route("**/ir/concrete-replay.json", async (route) => {
      const response = await route.fetch();
      const ir = await response.json();
      generatedIr = structuredClone(ir);
      if (missing === "static analysis") ir.execution.counter.mode = "analysis";
      else delete ir.register_history;
      await route.fulfill({ response, json: ir });
    });
    await open(page, "concrete-replay", "QCM0", "counter");
    await page.getByRole("button", { name: "Execution replay", exact: true }).click();
    await expect(run(page)).toBeHidden();
    await expect(value(page, "replay-progress")).toBeHidden();
    await expect(value(page, "replay-registers")).toBeHidden();
    await expect(value(page, "replay-status")).toContainText(missing === "static analysis" ? /No execution trace/ : /Register history/);
    await page.getByRole("button", { name: "Generate execution trace", exact: true }).click();
    await expect(page.getByRole("button", { name: "Generating execution trace…", exact: true })).toBeDisabled();
    expect(await page.evaluate(() => window.requestedReplaySequencer)).toBe("counter");
    await page.evaluate((ir) => {
      const initialState = window.hardwarePreview.getState();
      window.hardwarePreview.destroy();
      window.hardwarePreview = window.q1lensHardwareView.mount(document.getElementById("hardware"), ir, { initialState });
    }, generatedIr);
    await expect(replay(page)).toBeVisible();
    await expect(value(page, "replay-progress")).toHaveText("0 / 19 instructions");
    await expect(run(page)).toBeEnabled();
    await expect(page.locator('[data-replay-empty]')).toBeHidden();
    for (let index = 0; index < 3; index++) await step(page).click();
    await expect(replayRegister(page, "R0")).toHaveText("3");
  });
}

test("the open cursor project replays its recorded prefix and shows its input wait", async ({ page }) => {
  await freezeClock(page);
  await open(page, "current-project-replay", "QCM0", "blue_peak");
  await page.getByRole("button", { name: "Execution replay", exact: true }).click();
  await page.getByRole("combobox", { name: "Execution replay scope" }).selectOption("sequencer");
  await expect(run(page)).toBeEnabled();
  await expect(value(page, "replay-progress")).toHaveText("0 / 128 instructions");
  await page.getByRole("combobox", { name: "Execution replay speed" }).selectOption("100");
  await run(page).click();
  await page.clock.runFor(13000);
  await expect(value(page, "replay-progress")).toHaveText("128 / 128 instructions");
  await expect(replayRegister(page, "R0")).not.toHaveText("?");
  await expect(value(page, "replay-status")).toContainText(/Recorded trace ended.*queue_full/);
  await page.screenshot({ path: test.info().outputPath("current-project-replay.png"), fullPage: true });
});

test("joint replay advances registers together and preserves asymmetric wait_sync arrival", async ({ page }) => {
  await freezeClock(page);
  await openJoint(page);
  await expect(page.getByRole("combobox", { name: "Execution replay scope" })).toHaveValue("project");
  await expect(page.locator('[data-replay-sequencer]')).toHaveCount(2);
  await step(page).click();
  await expect(page.getByRole("spinbutton")).toHaveValue("4");
  for (const id of ["early", "late"]) {
    await expect(projectValue(page, id, "progress")).toHaveText("1 / 5 instructions");
    await expect(projectRegister(page, id)).toHaveText("?");
  }
  await step(page).click();
  await expect(page.getByRole("spinbutton")).toHaveValue("8");
  await expect(projectRegister(page, "early")).toHaveText("7");
  await expect(projectRegister(page, "late")).toHaveText("9");
  await page.evaluate(() => window.hardwarePreview.setTime(208));
  await expect(projectValue(page, "early", "rt")).toContainText(/waiting.*wait_sync/);
  await expect(projectValue(page, "late", "rt")).toContainText(/active.*wait 28/);
  for (const id of ["early", "late"]) await expect(projectValue(page, id, "q1")).toContainText(/stopped.*stop/);
  await expect(projectCard(page, "early").locator('.is-rt-wait')).toHaveAttribute("data-source-line", "3");
  await page.screenshot({ path: test.info().outputPath("joint-sync-wait.png"), fullPage: true });
  await step(page).click();
  await expect(page.getByRole("spinbutton")).toHaveValue("228");
  for (const id of ["early", "late"]) await expect(projectValue(page, id, "rt")).toContainText(/active.*wait_sync/);
  await step(page).click();
  await expect(page.getByRole("spinbutton")).toHaveValue("232");
  for (const id of ["early", "late"]) await expect(projectValue(page, id, "rt")).toContainText(/active.*wait 8/);
  await page.getByRole("combobox", { name: "Execution replay speed" }).selectOption("100");
  await run(page).click();
  await page.clock.runFor(1000);
  await expect(value(page, "replay-status")).toHaveText("All sequencers finished");
  await run(page).click();
  await page.clock.runFor(100);
  await expect(page.getByRole("spinbutton")).toHaveValue("4");
  await page.getByRole("combobox", { name: "Execution replay scope" }).selectOption("sequencer");
  await expect(run(page)).toHaveText("Run");
  await page.clock.runFor(1000);
  await expect(page.getByRole("spinbutton")).toHaveValue("4");
  await page.getByRole("combobox", { name: "Execution replay scope" }).selectOption("project");
  await page.evaluate(() => window.hardwarePreview.setTime(-1));
  await expect(page.getByRole("spinbutton")).toHaveValue("0");
  await expect(value(page, "replay-progress")).toContainText(/^1 \/ .* frames$/);
  await step(page).click();
  await expect(page.getByRole("spinbutton")).toHaveValue("4");
  await page.setViewportSize({ width: 480, height: 700 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("joint generation requires simulation and restores all cards after refresh", async ({ page }) => {
  let generatedIr;
  await page.route("**/ir/joint-sync-replay.json", async (route) => {
    const response = await route.fetch();
    const ir = await response.json();
    generatedIr = structuredClone(ir);
    for (const execution of Object.values(ir.execution)) execution.mode = "concrete";
    await route.fulfill({ response, json: ir });
  });
  await openJoint(page);
  await expect(run(page)).toBeHidden();
  await expect(value(page, "replay-status")).toContainText(/shared simulation clock/);
  await page.getByRole("button", { name: "Generate execution trace", exact: true }).click();
  expect(await page.evaluate(() => [window.requestedReplaySequencer, window.requestedReplayScope])).toEqual(["early", "project"]);
  await page.evaluate((ir) => {
    const initialState = window.hardwarePreview.getState();
    window.hardwarePreview.destroy();
    window.hardwarePreview = window.q1lensHardwareView.mount(document.getElementById("hardware"), ir, { initialState, onSourceClick(source) { window.revealedSource = source; } });
  }, generatedIr);
  await expect(replay(page)).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Execution replay scope" })).toHaveValue("project");
  await expect(page.locator('[data-replay-sequencer]')).toHaveCount(2);
  await expect(run(page)).toBeEnabled();
  await page.evaluate(() => window.hardwarePreview.setTime(208));
  await projectCard(page, "early").getByRole("button", { name: "Open Q1 source", exact: true }).click();
  expect((await page.evaluate(() => window.revealedSource)).line).toBe(5);
  await expect(page.getByRole("spinbutton")).toHaveValue("208");
});

test("joint replay retains an unresolved barrier instead of releasing it", async ({ page }) => {
  await freezeClock(page);
  await openJoint(page, "joint-sync-blocked");
  await page.getByRole("combobox", { name: "Execution replay speed" }).selectOption("100");
  await run(page).click();
  await page.clock.runFor(5000);
  await expect(value(page, "replay-status")).toContainText("Recorded trace ended");
  await expect(projectValue(page, "early", "execution")).toContainText(/blocked.*sync_wait/);
  await expect(projectValue(page, "early", "rt")).toContainText(/waiting.*wait_sync/);
  await expect(projectCard(page, "early").locator('.is-rt-wait')).toHaveCount(1);
  await expect(projectValue(page, "late", "rt")).toContainText(/stopped.*stop/);
});

test("all four open-project sequencers remain visible on the common clock", async ({ page }) => {
  await freezeClock(page);
  await open(page, "current-project-replay", "QCM0", "blue_peak");
  await page.getByRole("button", { name: "Execution replay", exact: true }).click();
  await expect(page.locator('[data-replay-sequencer]')).toHaveCount(4);
  await page.getByRole("combobox", { name: "Execution replay speed" }).selectOption("100");
  await run(page).click();
  await page.clock.runFor(60000);
  for (const [id, count] of [["blue_peak", 128], ["red_cursor", 75], ["red_tracker", 46], ["orange_drive", 24]]) {
    await expect(projectValue(page, id, "progress")).toHaveText(`${count} / ${count} instructions`);
    await expect(projectValue(page, id, "execution")).toContainText("blocked");
  }
  await page.screenshot({ path: test.info().outputPath("all-sequencers-replay.png"), fullPage: true });
});
