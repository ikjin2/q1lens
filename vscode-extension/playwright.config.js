const { defineConfig } = require("@playwright/test");
const path = require("node:path");

const output = path.resolve(process.env.Q1LENS_BROWSER_OUTPUT || path.join(__dirname, "../output/ci-baseline-validation/browser"));
const port = process.env.Q1LENS_BROWSER_PORT || "4173";

module.exports = defineConfig({
  testDir: "./test/browser",
  testMatch: "**/*.spec.js",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env.CI,
  timeout: 30000,
  outputDir: path.join(output, "test-results"),
  reporter: [["list"], ["html", { outputFolder: path.join(output, "report"), open: "never" }], ["json", { outputFile: path.join(output, "results.json") }]],
  use: {
    browserName: "chromium",
    baseURL: `http://127.0.0.1:${port}`,
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "node scripts/browser-server.cjs",
    url: `http://127.0.0.1:${port}/manifest.json`,
    reuseExistingServer: false,
    timeout: 120000,
    env: { Q1LENS_BROWSER_OUTPUT: output, Q1LENS_BROWSER_PORT: port },
    stdout: "pipe",
    stderr: "pipe",
  },
});
