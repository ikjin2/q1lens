const { test, expect, open, at, value, register, tab } = require("./helpers");

for (const profile of ["01-feedback-missing", "06-feedback-too-late"]) {
  test(`input fault ${profile}: pending feedback and underflow retain exact time and source`, async ({ page }) => {
    await open(page, `fault-${profile}`, "QCM0", "seq");
    await at(page, 1199);
    await tab(page, "Simulation");
    await expect(value(page, "simulation-queue")).toContainText("Q1 stalled: feedback input (4–1200 ns)");
    await expect(value(page, "q1-event")).toContainText("stalled");
    await expect(value(page, "communications")).not.toContainText("feedback receive");
    await expect(value(page, "communications")).not.toContainText("value 7");
    await at(page, 1200);
    await expect(value(page, "execution")).toContainText("error");
    await expect(value(page, "execution")).toContainText("RT queue underflow at 1200 ns.");
    await expect(page.getByRole("button", { name: "Next at 1600 ns", exact: true })).toHaveCount(0);
    await tab(page, "Registers");
    await page.getByRole("checkbox", { name: "All 64" }).check();
    await expect(register(page, "R0")).toHaveText("?");
    await page.locator('[data-block="q1"]').click();
    await page.getByRole("button", { name: "Open source", exact: true }).click();
    const source = await page.evaluate(() => window.revealedSource);
    expect(source.line).toBe(2);
    expect(source.file.replaceAll("\\", "/")).toContain(`/faults/${profile}/program.q1asm`);
  });
}

test("delayed feedback becomes readable only after arrival and register latency", async ({ page }) => {
  await open(page, "fault-02-feedback-delayed", "QCM0", "seq");
  await at(page, 399);
  await tab(page, "Simulation");
  await expect(value(page, "simulation-queue")).toContainText("Q1 stalled: feedback input (4–400 ns)");
  await expect(value(page, "communications")).not.toContainText("value 7");
  await at(page, 400);
  await expect(value(page, "simulation-queue")).not.toContainText("Q1 stalled");
  await expect(value(page, "communications")).toContainText("400 ns · feedback receive");
  await expect(value(page, "communications")).toContainText("id 5 · value 7");
  await tab(page, "Registers");
  await page.getByRole("checkbox", { name: "All 64" }).check();
  await at(page, 407);
  await expect(register(page, "R0")).toHaveText("?");
  await at(page, 408);
  await expect(register(page, "R0")).toHaveText("7");
  await page.locator('[data-register="R0"]').click();
  await page.getByRole("button", { name: "Open source", exact: true }).click();
  expect((await page.evaluate(() => window.revealedSource)).line).toBe(2);
  await page.getByRole("button", { name: "Show received input that supplied this value", exact: true }).click();
  await expect(page.getByRole("spinbutton")).toHaveValue("400");
  await expect(value(page, "communications")).toContainText("Scenario input 0");
  await expect(page.getByRole("button", { name: "Show sending event", exact: true })).toHaveCount(0);
  await expect(value(page, "execution")).toContainText("completed");
});

test("duplicate feedback preserves FIFO occurrences even when two payloads are equal", async ({ page }) => {
  await open(page, "fault-03-feedback-duplicate", "QCM0", "seq");
  const inputEvents = [];
  for (const [name, ready, expected, inputIndex, line] of [["R0", 408, "7", 0, 2], ["R1", 416, "7", 1, 4], ["R2", 424, "9", 2, 6]]) {
    await tab(page, "Registers");
    await page.getByRole("checkbox", { name: "All 64" }).check();
    await at(page, ready - 1);
    await expect(register(page, name)).toHaveText("?");
    await at(page, ready);
    await expect(register(page, name)).toHaveText(expected);
    await page.locator(`[data-register="${name}"]`).click();
    await expect(value(page, "register-detail")).toContainText(`readable at ${ready} ns`);
    await page.getByRole("button", { name: "Open source", exact: true }).click();
    expect((await page.evaluate(() => window.revealedSource)).line).toBe(line);
    await page.getByRole("button", { name: "Show received input that supplied this value", exact: true }).click();
    await expect(page.getByRole("spinbutton")).toHaveValue("400");
    inputEvents.push(await page.evaluate(() => window.selectedEventId));
    const selected = await page.evaluate(() => {
      const event = window.demoIr.events.find((item) => item.id === window.selectedEventId);
      return { kind: event.kind, time: event.t0.value, inputIndex: event.meta.input_index, value: event.meta.value };
    });
    expect(selected).toEqual({ kind: "feedback_receive", time: 400, inputIndex, value: Number(expected) });
    await expect(value(page, "communications")).toContainText(`Scenario input ${inputIndex}`);
  }
  expect(new Set(inputEvents).size).toBe(3);
  const receives = page.locator(".hardware-communication").filter({ has: page.getByRole("button", { name: "400 ns · feedback receive", exact: true }) });
  await expect(receives).toHaveCount(3);
  await expect(receives.nth(0)).toContainText("id 5 · value 7");
  await expect(receives.nth(1)).toContainText("id 5 · value 7");
  await expect(receives.nth(2)).toContainText("id 5 · value 9");
});

test("missing trigger remains blocked and waiting at its source without fabricating an arrival", async ({ page }) => {
  await open(page, "fault-04-trigger-missing", "QCM0", "seq");
  await at(page, 200);
  await expect(value(page, "execution")).toContainText("blocked");
  await expect(value(page, "execution")).toContainText("trigger_wait");
  await expect(value(page, "rt-event")).toContainText("waiting");
  await tab(page, "Simulation");
  await expect(value(page, "communications")).toContainText("200 ns · trigger wait");
  await expect(value(page, "communications")).not.toContainText("trigger receive");
  await page.locator('[data-block="rt"]').click();
  await page.getByRole("button", { name: "Open source", exact: true }).click();
  expect((await page.evaluate(() => window.revealedSource)).line).toBe(1);
});

test("feedback queue overflow exposes accepted inputs and no uncommitted register result", async ({ page }) => {
  await open(page, "fault-07-feedback-overflow", "QCM0", "seq");
  await tab(page, "Simulation");
  await at(page, 399);
  await expect(value(page, "communications")).not.toContainText("feedback receive");
  await at(page, 400);
  await expect(value(page, "execution")).toContainText("error");
  await expect(value(page, "execution")).toContainText("Feedback queue overflow at 400 ns.");
  const receives = page.locator(".hardware-communication").filter({ has: page.getByRole("button", { name: "400 ns · feedback receive", exact: true }) });
  await expect(receives).toHaveCount(2);
  await expect(receives.nth(0)).toContainText("id 5 · value 11");
  await expect(receives.nth(0)).toContainText("Scenario input 0");
  await expect(receives.nth(1)).toContainText("id 5 · value 22");
  await expect(receives.nth(1)).toContainText("Scenario input 1");
  await expect(value(page, "communications")).not.toContainText("value 33");
  await tab(page, "Registers");
  await page.getByRole("checkbox", { name: "All 64" }).check();
  await expect(register(page, "R0")).toHaveText("?");
});

test("full RT queue displays stall and resumes exactly when the first packet starts", async ({ page }) => {
  await open(page, "fault-05-queue-full", "QCM0", "seq");
  await tab(page, "Simulation");
  await at(page, 199);
  await expect(value(page, "simulation-queue")).toContainText("RT queue 2 / 2 packets · Q1 stalled: queue full (8–200 ns)");
  await at(page, 200);
  await expect(value(page, "simulation-queue")).toContainText("RT queue 1 / 2 packets");
  await expect(value(page, "simulation-queue")).not.toContainText("Q1 stalled");
  await at(page, 204);
  await expect(value(page, "simulation-queue")).toContainText("RT queue 2 / 2 packets · Q1 stalled: queue full (204–240 ns)");
  await at(page, 199);
  await page.locator('[data-block="q1"]').click();
  await page.getByRole("button", { name: "Open source", exact: true }).click();
  expect((await page.evaluate(() => window.revealedSource)).line).toBe(3);
  await at(page, 440);
  await tab(page, "Simulation");
  await expect(value(page, "simulation-queue")).toContainText("RT queue 0 / 2 packets");
  await expect(value(page, "execution")).toContainText("completed");
});

test("four-bin preview retains the previous result until wrapped acquisition completes, and rewinds", async ({ page }) => {
  await page.goto("/?case=bin-wrap-preview");
  await page.getByRole("button", { name: "Open QRM0: 2 sequencers", exact: true }).click();
  await page.getByRole("button", { name: "Inspect sequencer red_tracker", exact: true }).click();
  await tab(page, "Bins");
  const bin0 = value(page, "bins").locator("tr").filter({ has: page.locator('[data-bin="0:0"]') });
  await at(page, 84031);
  await expect(bin0.locator("td").nth(2)).toHaveText("1");
  await expect(bin0).toContainText("I 24576000 · Q -4096000");
  await at(page, 84032);
  await expect(bin0.locator("td").nth(2)).toHaveText("2");
  await expect(bin0.locator("td").nth(3)).toHaveText("84032 ns");
  await at(page, 84071);
  await expect(bin0).toContainText("I 24576000 · Q -4096000");
  await expect(bin0).not.toContainText("I 28672000");
  await page.locator('[data-bin="0:0"]').click();
  await expect(value(page, "bin-history")).toContainText("ready 6072 ns");
  await at(page, 84072);
  await expect(bin0).toContainText("I 28672000 · Q -4096000");
  await expect(value(page, "bin-history")).toContainText("ready 84072 ns");
  await expect(value(page, "bin-history")).toContainText("Scenario input");
  await bin0.locator("td").nth(4).getByRole("button").click();
  await expect(page.getByRole("spinbutton")).toHaveValue("84072");
  const selected = await page.evaluate(() => {
    const event = window.demoIr.events.find((item) => item.id === window.selectedEventId);
    return { kind: event.kind, time: event.t0.value, bin: event.meta.bin_index, i: event.meta.i };
  });
  expect(selected).toEqual({ kind: "acquisition_result", time: 84072, bin: 0, i: 28672000 });
  await page.getByRole("button", { name: "Open source", exact: true }).click();
  expect((await page.evaluate(() => window.revealedSource)).line).toBe(92);
  await at(page, 84071);
  await expect(bin0).toContainText("I 24576000 · Q -4096000");
  await expect(value(page, "bin-history")).toContainText("ready 6072 ns");
  await expect(value(page, "bin-history")).not.toContainText("28672000");
});
