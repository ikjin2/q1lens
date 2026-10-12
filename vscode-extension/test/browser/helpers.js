const { test: base, expect } = require("@playwright/test");

const test = base.extend({
  cleanBrowser: [async ({ page }, use, info) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
    page.on("console", (message) => { if (message.type() === "error") errors.push(`console: ${message.text()}`); });
    page.on("requestfailed", (request) => errors.push(`request: ${request.url()} ${request.failure()?.errorText}`));
    page.on("response", (response) => { if (response.status() >= 400) errors.push(`HTTP ${response.status()}: ${response.url()}`); });
    await use();
    await info.attach("browser-errors", { body: JSON.stringify(errors), contentType: "application/json" });
    expect(errors, "No browser exceptions, failed assets, or console errors").toEqual([]);
  }, { auto: true }],
});

async function open(page, scenario, module, sequencer) {
  await page.goto(`/?case=${scenario}`);
  await page.getByRole("button", { name: `Open ${module}: 1 sequencer`, exact: true }).click();
  await page.getByRole("button", { name: `Inspect sequencer ${sequencer}`, exact: true }).click();
}
async function at(page, time) {
  await page.getByRole("spinbutton").fill(String(time));
  await page.getByRole("spinbutton").press("Enter");
}
const value = (page, name) => page.locator(`[data-value="${name}"]`);
const register = (page, name) => page.locator(`[data-register="${name}"] strong`);
const tab = (page, name) => page.getByRole("tab", { name, exact: true }).click();

module.exports = { test, expect, open, at, value, register, tab };
