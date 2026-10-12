const { test, expect, open, at, value, register, tab } = require("./helpers");

test("cursor provenance follows operand register writes at their readable times", async ({ page }) => {
  await open(page, "cursor-two-shot-feedback", "QCM1", "orange_drive");
  await tab(page, "Registers");
  await at(page, 8464);
  await page.locator('[data-register="R2"]').click();
  await expect(value(page, "register-detail")).toContainText("R2 = 782336");
  await value(page, "register-detail").getByRole("button", { name: "R3 = 782336 ↗", exact: true }).click();
  await expect(page.getByRole("spinbutton")).toHaveValue("8448");
  await expect(page.locator('[data-register="R3"]')).toHaveAttribute("aria-pressed", "true");
  await expect(value(page, "register-detail")).toContainText("R3 = 782336");
  await expect(value(page, "register-detail")).toContainText("readable at 8448 ns");
  await expect(value(page, "register-detail")).toContainText("Calculation: R3 << FREQ_CURSOR_SHIFT");
  await page.getByRole("button", { name: "Open source", exact: true }).click();
  expect(await page.evaluate(() => window.selectedEventId)).toBe("orange_drive:e65");
  expect((await page.evaluate(() => window.revealedSource)).line).toBe(66);
  await value(page, "register-detail").getByRole("button", { name: "R3 = 1528 ↗", exact: true }).click();
  await expect(page.getByRole("spinbutton")).toHaveValue("8432");
  await expect(value(page, "register-detail")).toContainText("R3 = 1528");
  await expect(value(page, "register-detail")).toContainText("readable at 8432 ns");
  await expect(value(page, "register-detail")).toContainText("Calculation: R1 - TRACKER_MIN_CENTER");
  await page.getByRole("button", { name: "Open source", exact: true }).click();
  expect(await page.evaluate(() => window.selectedEventId)).toBe("orange_drive:e63");
  expect((await page.evaluate(() => window.revealedSource)).line).toBe(64);
});

test("cursor applied frequency navigates to the originating setter instead of the later play", async ({ page }) => {
  await open(page, "cursor-two-shot-feedback", "QCM1", "orange_drive");
  await tab(page, "Parameters");
  await at(page, 41040);
  const row = value(page, "parameters").locator("tr").filter({ has: page.getByRole("rowheader", { name: "frequency", exact: true }) });
  const applied = row.locator("td").nth(1).getByRole("button", { name: "782336", exact: true });
  await applied.click();
  await expect(page.getByRole("spinbutton")).toHaveValue("8472");
  expect(await page.evaluate(() => window.selectedEventId)).toBe("orange_drive:e73");
  await expect(value(page, "instruction-detail")).toContainText("R2 = 782336");
  await page.getByRole("button", { name: "Open source", exact: true }).click();
  const source = await page.evaluate(() => window.revealedSource);
  expect(source.line).toBe(72);
  expect(source.raw.trim()).toBe("set_freq $FREQ_WORD");
  await tab(page, "Parameters");
  await at(page, 41040);
  await expect(row).toContainText("Applied at 17040 ns (local)");
  await expect(row).toContainText("RT event at 41040 ns");
});

test("cursor feedback provenance links receiver to sender instruction and its register writer", async ({ page }) => {
  await open(page, "cursor-two-shot-feedback", "QCM1", "orange_drive");
  await tab(page, "Registers");
  await at(page, 8368);
  await page.locator('[data-register="R1"]').click();
  await expect(value(page, "register-detail")).toContainText("R1 = 5528");
  await page.getByRole("button", { name: "Show received input that supplied this value", exact: true }).click();
  await expect(page.getByRole("tab", { name: "Simulation", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("spinbutton")).toHaveValue("8360");
  await page.getByRole("button", { name: "Show sending event", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Hardware sequencer" })).toHaveValue("red_tracker");
  await expect(page.getByRole("spinbutton")).toHaveValue("7980");
  const send = page.locator(".hardware-communication").filter({ has: page.getByRole("button", { name: "7980 ns · feedback send", exact: true }) });
  await send.getByRole("button", { name: "Show sending instruction", exact: true }).click();
  await expect(page.getByRole("spinbutton")).toHaveValue("7764");
  await expect(value(page, "instruction-detail")).toContainText("R15 = 5528");
  await value(page, "instruction-detail").getByRole("button", { name: "R15 = 5528 ↗", exact: true }).click();
  await expect(page.getByRole("spinbutton")).toHaveValue("7728");
  await expect(page.locator('[data-register="R15"]')).toHaveAttribute("aria-pressed", "true");
  await expect(value(page, "register-detail")).toContainText("R15 = 5528");
  await expect(value(page, "register-detail")).toContainText("readable at 7728 ns");
  await page.getByRole("button", { name: "Open source", exact: true }).click();
  const source = await page.evaluate(() => window.revealedSource);
  expect(source.line).toBe(182);
  expect(source.file.replaceAll("\\", "/")).toMatch(/\/red_tracker\.q1asm$/);
});

// Hand-derived values from the original program's integer update rules; the
// fixture runner separately checks its arithmetic, source hashes and two replays.
for (const profile of ["two-shot-feedback", "deadband-hold"]) {
  test(`cursor demo ${profile}: register readiness, one-shot latency, module navigation and rewind`, async ({ page }) => {
    const changes = profile === "two-shot-feedback";
    await open(page, `cursor-${profile}`, "QCM1", "orange_drive");
    await tab(page, "Registers");
    await page.getByRole("checkbox", { name: "All 64" }).check();
    for (const [time, frequency] of [[99, "?"], [100, "780288"], [8463, "780288"],
      [8464, changes ? "782336" : "780288"], [changes ? 47467 : 47463, changes ? "782336" : "780288"],
      [changes ? 47468 : 47464, "780288"]]) {
      await at(page, time);
      await expect(register(page, "R2")).toHaveText(frequency);
    }
    await at(page, 8464);
    await page.locator('[data-register="R2"]').click();
    await expect(value(page, "register-detail")).toContainText("readable at 8464 ns");
    await page.getByRole("button", { name: "Open source", exact: true }).click();
    const source = await page.evaluate(() => window.revealedSource);
    expect(source.line).toBe(68);
    expect(source.file.replaceAll("\\", "/")).toMatch(/\/examples\/cursor-frequency-demo\/orange_drive\.q1asm$/);

    await tab(page, "Parameters");
    const applied = (name) => value(page, "parameters").locator("tr").filter({ has: page.getByRole("rowheader", { name, exact: true }) }).locator("td").nth(1).getByRole("button");
    await at(page, 2040);
    await expect(applied("frequency")).toHaveText("780288");
    await expect(applied("awg_gain")).toHaveText("1750, 0");
    await at(page, 41040);
    await expect(applied("frequency")).toHaveText(changes ? "782336" : "780288");
    await expect(applied("awg_gain")).toHaveText(changes ? "2000, 0" : "1754, 0");

    await page.getByRole("combobox", { name: "Hardware module" }).selectOption("QRM0");
    await expect(page.getByRole("button", { name: "Inspect sequencer red_cursor", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Inspect sequencer red_tracker", exact: true }).click();
    await tab(page, "Registers");
    await at(page, changes ? 7728 : 7720);
    await expect(register(page, "R15")).toHaveText(changes ? "5528" : "5524");
    await at(page, changes ? 46780 : 46752);
    await expect(register(page, "R15")).toHaveText("5524");
    if (changes) {
      // Profile IQ is defined after the program's 12-bit right shift; bins
      // display the supplied raw acquisition words before that calculation.
      const rawResult = `I ${6000 * 2 ** 12} · Q ${-1000 * 2 ** 12}`;
      await tab(page, "Bins");
      await at(page, 6071);
      await expect(value(page, "bins")).toContainText("unknown");
      await expect(value(page, "bins")).not.toContainText(rawResult);
      await at(page, 6072);
      await expect(value(page, "bins")).toContainText(rawResult);
      await page.locator('[data-bin="0:0"]').click();
      await expect(value(page, "bin-history")).toContainText("Scenario input · acquisition input 0");
      await at(page, 6071);
      await expect(value(page, "bins")).not.toContainText(rawResult);
    }
    await at(page, 60000);
    await expect(value(page, "execution")).toContainText("blocked");
  });
}
