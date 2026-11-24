const path = require("node:path");
const readline = require("node:readline");
const { spawn } = require("node:child_process");
const { test, expect } = require("./helpers");

// The real page (decisionLanes.css, decisionLanesModel.js, decisionLanes.js) against the real
// Python worker. Only the extension host between them is replaced, by the few lines below that
// relay the same messages the host relays.
const repo = path.resolve(__dirname, "../../..");
const media = path.join(repo, "vscode-extension/src/q1timeline/media");
const python = process.env.Q1LENS_BROWSER_PYTHON || "python";
const GOLDEN = "examples/golden-section/q1timeline.yml";
const ALTERNATE = [2000000, 1000000, 4000000, 1000000, 4000000, 1000000, 2000000, 1000000];
const THEMES = {
  light: { class: "vscode-light", vars: "--vscode-editor-background:#ffffff;--vscode-foreground:#3b3b3b;--vscode-descriptionForeground:#6a6a6a;--vscode-panel-border:#e5e5e5;--vscode-input-background:#ffffff;--vscode-input-foreground:#3b3b3b;--vscode-input-border:#cecece;--vscode-button-secondaryBackground:#e5e5e5;--vscode-button-secondaryForeground:#3b3b3b;--vscode-button-secondaryHoverBackground:#cccccc;--vscode-focusBorder:#005fb8;--vscode-editorWarning-foreground:#bf8803;--vscode-errorForeground:#f85149;--vscode-textLink-foreground:#005fb8" },
  dark: { class: "vscode-dark", vars: "--vscode-editor-background:#1f1f1f;--vscode-foreground:#cccccc;--vscode-descriptionForeground:#9d9d9d;--vscode-panel-border:#2b2b2b;--vscode-input-background:#313131;--vscode-input-foreground:#cccccc;--vscode-input-border:#3c3c3c;--vscode-button-secondaryBackground:#313131;--vscode-button-secondaryForeground:#cccccc;--vscode-button-secondaryHoverBackground:#3c3c3c;--vscode-focusBorder:#0078d4;--vscode-editorWarning-foreground:#cca700;--vscode-errorForeground:#f85149;--vscode-textLink-foreground:#4daafc" },
};

function startWorker(project) {
  const child = spawn(python, ["-u", "-m", "q1timeline.decision_lanes", "--project", path.join(repo, project)], {
    cwd: repo, windowsHide: true,
    env: { ...process.env, PYTHONPATH: [path.join(repo, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) },
  });
  const waiting = new Map();
  let nextId = 1, stderr = "";
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  readline.createInterface({ input: child.stdout }).on("line", (line) => {
    const answer = JSON.parse(line), pending = waiting.get(answer.id);
    waiting.delete(answer.id);
    if (pending) pending(answer);
  });
  child.on("close", () => { for (const pending of waiting.values()) pending({ error: `worker exited: ${stderr}` }); });
  return {
    request: (payload) => new Promise((resolve) => { const id = nextId++; waiting.set(id, resolve); child.stdin.write(JSON.stringify({ id, ...payload }) + "\n"); }),
    close: () => child.kill(),
  };
}

async function openLanes(page, project, { sequencer, feedback = [0], theme = "dark" } = {}) {
  const worker = startWorker(project);
  const host = { worker, sent: [], token: 0, sequencer, feedback, flipRequests: 0 };
  const post = (message) => page.evaluate((data) => window.postMessage(data, "*"), message);
  const load = async () => {
    const token = ++host.token;
    await post({ type: "loading", text: "Recording…" });
    const info = await worker.request({ command: "info" });
    const chosen = info.info.sequencers.find((item) => item.id === host.sequencer) || info.info.sequencers[0];
    const answer = await worker.request({ command: "open", sequencer: chosen.id, feedback: host.feedback });
    if (token !== host.token) return;
    if (answer.error) await post({ type: "error", message: answer.error });
    else await post({ type: "data", token, payload: answer.lanes, history: ["0", "1"], notice: "" });
  };
  await page.exposeFunction("lanesHost", async (message) => {
    host.sent.push(message);
    if (message.type === "ready" || message.type === "refresh") await load();
    else if (message.type === "open") { host.sequencer = message.sequencer; if (message.feedback !== undefined) host.feedback = message.feedback; await load(); }
    else if (message.type === "flips") {
      host.flipRequests++;
      const token = host.token, answer = await worker.request({ command: "flips", requests: message.requests, budget_ms: 200 });
      if (token === host.token && message.token === token) await post({ type: "runs", token, runs: answer.runs || {}, failed: answer.failed || [] });
    }
  });
  const { class: bodyClass, vars } = THEMES[theme];
  await page.setContent(`<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Decision lanes</title></head>
    <body class="${bodyClass}" style="${vars};--vscode-font-family:'Segoe UI',sans-serif;--vscode-editor-font-family:Consolas,monospace"><main id="decision-lanes"></main></body></html>`);
  await page.addStyleTag({ path: path.join(media, "decisionLanes.css") });
  await page.addScriptTag({ content: "window.acquireVsCodeApi = () => ({ postMessage: (message) => { window.lanesHost(message); } });" });
  await page.addScriptTag({ path: path.join(media, "decisionLanesModel.js") });
  await page.addScriptTag({ path: path.join(media, "decisionLanes.js") });
  return host;
}

const cells = (page) => page.locator('#dl-lanes-svg [data-action="select"]');
const recorded = (page) => page.evaluate(() => Object.keys(window.q1lensDecisionLanes.mounted.view.data.runs).length);
const span = (page) => page.evaluate(() => { const [a, b] = window.q1lensDecisionLanes.mounted.view.state.view; return b - a; });

test.describe("Decision lanes with the Python worker", () => {
  test.describe.configure({ timeout: 120000 });

  test("records golden-section, its other arms, and follows selection, zoom and new feedback words", async ({ page }, info) => {
    await page.setViewportSize({ width: 1480, height: 1000 });
    const host = await openLanes(page, GOLDEN, { feedback: ALTERNATE });
    try {
      // First paint: the base run, before any other arm is recorded.
      await expect(cells(page)).toHaveCount(13, { timeout: 30000 });
      await expect(page.locator("#dl-fold")).toHaveText("13 loop passes = pick_left × 7 (L45 Jump) + pick_right × 6 (L45 Continue)");
      await expect(page.locator("#dl-detail .dhead h2")).toHaveText("Decision 1 / 13");
      await expect(page.locator("#dl-sites tbody tr")).toHaveCount(4);
      await expect(page.locator("#dl-notes")).toContainText("One executed path with assumed feedback receipts");

      // The view asks the worker for the other arms; one flip run per decision arrives in batches.
      await expect.poll(() => recorded(page), { timeout: 60000 }).toBe(14);
      expect(host.flipRequests).toBeGreaterThan(1);
      await expect(page.locator("#dl-controls .progress")).toHaveCount(0);
      await expect(page.locator("#dl-detail .badge")).toHaveText("Changes state only");
      await expect(page.locator("#dl-lanes-svg .ghost")).toHaveCount(2);
      await expect(page.locator("#dl-lanes-svg .series")).toHaveCount(2);
      await expect(page.locator("#dl-stack-svg .rowhit")).toHaveCount(14);
      await expect(page.locator("#dl-run option")).toHaveCount(14);
      await info.attach("golden-dark", { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });

      // Selecting a decision moves the detail and asks the host to open its source line.
      await cells(page).nth(3).click();
      await expect(page.locator("#dl-detail .dhead h2")).toHaveText("Decision 4 / 13");
      await expect(page.locator("#dl-detail .dhead")).toContainText("Continue");
      expect(host.sent.filter((message) => message.type === "reveal").at(-1)).toEqual({ type: "reveal", line: 45 });
      await page.locator("#dl-lanes-wrap").focus();
      await page.keyboard.press("ArrowRight");
      await expect(page.locator("#dl-detail .dhead h2")).toHaveText("Decision 5 / 13");

      // Zoom, pan and the hover readout.
      const full = await span(page);
      await page.getByRole("button", { name: "Around selection" }).click();
      expect(await span(page)).toBeLessThan(full);
      expect(await cells(page).count()).toBeLessThan(13);
      await page.getByRole("button", { name: "Fit", exact: true }).click();
      expect(await span(page)).toBe(full);
      const box = await page.locator("#dl-lanes-svg").boundingBox();
      await page.mouse.move(box.x + box.width * 0.45, box.y + 200);
      await expect(page.locator("#dl-tip")).toBeVisible();
      await expect(page.locator("#dl-tip")).toContainText(/RT .* ms/);
      await page.keyboard.down("Control");
      await page.mouse.wheel(0, -300);
      await page.keyboard.up("Control");
      await expect.poll(() => span(page)).toBeLessThan(full);
      await page.getByRole("button", { name: "Fit", exact: true }).click();
      await page.mouse.move(5, 5);

      // More registers on the state lane.
      await page.locator('[data-action="toggle-operands"]').click();
      await expect(page.locator("#dl-lanes-svg .series")).toHaveCount(4);
      await page.locator('[data-action="toggle-operands"]').click();

      // A flip run is an assumption from its forced branch on.
      await page.locator("#dl-detail [data-action='open-run']").click();
      await expect(page.locator("#dl-controls .status")).toContainText("Decision 5 flipped · assumed");
      await expect(page.locator("#dl-lanes-svg .forced")).toHaveCount(1);
      await expect(page.locator("#dl-lanes-svg .hyp")).toHaveCount(1);
      // A later decision of that run gets its own other arm on demand.
      await cells(page).nth(8).click();
      await expect(page.locator("#dl-detail .badge")).toHaveText("Changes state only", { timeout: 30000 });
      expect(await recorded(page)).toBe(15);
      await page.getByRole("button", { name: "Base run ↶" }).click();
      await expect(page.locator("#dl-lanes-svg .forced")).toHaveCount(0);

      // The table repeats the timeline.
      await page.locator("#dl-table summary").click();
      await expect(page.locator("#dl-table tbody tr")).toHaveCount(13);

      // Typed feedback words: a mistake stays on the page, a valid list makes a new record.
      await page.locator("#dl-feedback").fill("1, two");
      await page.locator("#dl-feedback").press("Enter");
      await expect(page.locator("#dl-controls [role=alert]")).toHaveText('"two" is not an integer.');
      await cells(page).nth(2).click();
      await page.locator("#dl-feedback").fill("0");
      await page.getByRole("button", { name: "Apply" }).click();
      await expect(page.locator("#dl-fold")).toHaveText("13 loop passes = pick_right × 13 (L45 Continue)", { timeout: 30000 });
      await expect(page.locator("#dl-detail .dhead h2")).toHaveText("Decision 3 / 13");     // the new record keeps the selection
      await expect(page.locator("#dl-table")).toHaveAttribute("open", "");                  // and what was opened
      expect(host.sent.filter((message) => message.type === "open").at(-1)).toEqual({ type: "open", sequencer: "golden_section", feedback: [0] });
      await expect.poll(() => recorded(page), { timeout: 60000 }).toBe(14);
    } finally {
      host.worker.close();
    }
  });

  test("keeps its layout in a light theme and in a narrow panel", async ({ page }, info) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    const host = await openLanes(page, GOLDEN, { feedback: ALTERNATE, theme: "light" });
    try {
      await expect(cells(page)).toHaveCount(13, { timeout: 30000 });
      await expect.poll(() => recorded(page), { timeout: 60000 }).toBe(14);
      await info.attach("golden-light", { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });
      await page.setViewportSize({ width: 700, height: 900 });
      await expect(cells(page).first()).toBeVisible();
      // Nothing but the two scrolling lane areas may be wider than the panel.
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
      await info.attach("golden-narrow", { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });
    } finally {
      host.worker.close();
    }
  });

  test("shows where a partial run stopped and offers the project's other sequencers", async ({ page }) => {
    const host = await openLanes(page, "examples/cursor-frequency-demo/q1timeline.yml", { sequencer: "red_tracker" });
    try {
      await expect(page.locator("#dl-controls .pill.warn")).toHaveText("Partial · Waiting for an input", { timeout: 30000 });
      await expect(page.locator("#dl-detail")).toContainText("No input decision in this run");
      await expect(page.locator("#dl-detail")).toContainText("No connected trigger source for address 1");
      await expect(page.locator("#dl-sequencer option")).toHaveText(["blue_peak", "red_cursor", "red_tracker", "orange_drive"]);
      await page.locator("#dl-sequencer").selectOption("blue_peak");
      await expect(page.locator("#dl-source")).toContainText("Instruction #1 of 8", { timeout: 30000 });
      expect(host.sent.filter((message) => message.type === "open").at(-1)).toEqual({ type: "open", sequencer: "blue_peak" });
    } finally {
      host.worker.close();
    }
  });

  test("a branch that changes timing gets the other arm on its own RT row", async ({ page }, info) => {
    const host = await openLanes(page, "examples/q1asm-tutorials/13-hardware-patterns/43-readout-feedback-reset-forensics/q1timeline.yml",
      { sequencer: "reset_probe", feedback: [1] });
    try {
      await expect(page.locator("#dl-detail .badge")).toHaveText("Changes timing", { timeout: 30000 });
      await expect(page.locator("#dl-detail")).toContainText("end of program · the arms never rejoin");
      await expect(page.locator("#dl-lanes-svg .block.other")).toHaveCount(1);
      await expect(page.locator("#dl-fold")).toHaveText("Path L8 (L7 Continue)");
      await info.attach("reset-dark", { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });
    } finally {
      host.worker.close();
    }
  });
});
