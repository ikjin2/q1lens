const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect } = require("./helpers");

const repo = path.resolve(__dirname, "../../..");
const assets = path.join(repo, "vscode-extension/src");
const projectFile = path.join(repo, "examples/parameters/q1timeline.yml");
let renderedHtml;

function parameterHtml() {
  if (!renderedHtml) {
    renderedHtml = execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
      "from q1timeline.render.html import render_html",
      "parameters=[",
      " {'name':'ACQ_DUR','value_json':'40','overridden':False},",
      " {'name':'CH1_INIT_DELAY','value_json':'880','overridden':True,'base_value_json':'800'},",
      " {'name':'START_ALIGN','value_json':'300','overridden':False},",
      " {'name':'CH1_MAX_DELAY','value_json':'1120','overridden':False},",
      " {'name':'CH2_PHASE','value_json':'45','overridden':False},",
      " {'name':'VERY_LONG_PARAMETER_NAME_WITH_A_READABLE_SUFFIX','value_json':'9007199254740993','overridden':False},",
      "]",
      "print(render_html({'version':'0.1.0','events':[],'analysis':{'parameters':parameters}}))",
    ].join("\n")], {
      cwd: repo, encoding: "utf8", timeout: 10000,
      env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONPATH: path.join(repo, "src") },
    }).replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attributes) =>
      /type=["']application\/json["']/i.test(attributes) ? tag : "");
  }
  return renderedHtml;
}

async function mount(page, savedState = {}) {
  await page.goto("about:blank");
  await page.setContent(parameterHtml());
  await page.evaluate(({ savedState, projectFile }) => {
    let saved = savedState;
    window.messages = [];
    window.getSavedTimelineState = () => saved;
    window.acquireVsCodeApi = () => ({
      postMessage: (message) => window.messages.push(message),
      getState: () => saved,
      setState: (value) => { saved = value; },
    });
    const state = document.createElement("meta");
    state.id = "q1timeline-webview-state";
    state.dataset.state = JSON.stringify({
      viewMode: "normal", parameterProjectFile: projectFile, parameterOverrideNames: ["CH1_INIT_DELAY"],
    });
    document.head.append(state);
  }, { savedState, projectFile });
  for (const file of ["shared/timeline/renderer.css", "q1timeline/media/timeline.css", "q1timeline/media/hardwareView.css"]) {
    await page.addStyleTag({ path: path.join(assets, file) });
  }
  for (const file of ["shared/timeline/instructionColors.js", "shared/timeline/renderer.js", "q1timeline/media/timelineAdapter.js",
    "q1timeline/media/hardwareState.js", "q1timeline/media/hardwareView.js", "q1timeline/media/timeline.js"]) {
    await page.addScriptTag({ path: path.join(assets, file) });
  }
  await expect(page.locator(".parameter-edit")).toHaveCount(6);
}

const panel = (page) => page.locator("#q1timeline-parameters");
const rows = (page) => panel(page).locator("[data-parameter-index]:visible");

async function geometry(page) {
  return rows(page).evaluateAll((nodes) => nodes.map((node) => {
    const rect = node.getBoundingClientRect();
    return { x: rect.x, y: rect.y, width: rect.width,
      nameX: node.querySelector("dt").getBoundingClientRect().x,
      valueX: node.querySelector("dd").getBoundingClientRect().x };
  }));
}

test("Parameters use aligned columns and reflow into one column in a narrow window", async ({ page }) => {
  await mount(page);
  await panel(page).locator("summary").click();
  await expect(rows(page)).toHaveCount(6);
  const wide = await geometry(page);
  expect(wide[1].y).toBeCloseTo(wide[0].y, 0);
  expect(wide[1].x).toBeGreaterThan(wide[0].x + wide[0].width);
  expect(wide[2].x).toBeCloseTo(wide[0].x, 0);
  expect(wide[2].nameX).toBeCloseTo(wide[0].nameX, 0);
  expect(wide[2].valueX).toBeCloseTo(wide[0].valueX, 0);
  expect(wide[3].nameX).toBeCloseTo(wide[1].nameX, 0);
  expect(wide[3].valueX).toBeCloseTo(wide[1].valueX, 0);
  await expect(rows(page).nth(1)).toContainText("override");
  await expect(rows(page).nth(1)).toContainText("← base 800");
  await expect(rows(page).last().locator("dd > code")).toHaveText("9007199254740993");
  await page.screenshot({ path: test.info().outputPath("parameters-two-columns.png"), fullPage: true });

  await page.setViewportSize({ width: 400, height: 900 });
  const narrow = await geometry(page);
  for (let index = 1; index < narrow.length; index += 1) {
    expect(narrow[index].x).toBeCloseTo(narrow[0].x, 0);
    expect(narrow[index].y).toBeGreaterThan(narrow[index - 1].y);
    expect(narrow[index].valueX).toBeCloseTo(narrow[0].valueX, 0);
  }
  expect(await panel(page).locator(".parameters-list").evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: test.info().outputPath("parameters-one-column.png"), fullPage: true });
});

test("parameter name search filters and reflows rows, survives refresh, and clears with Escape", async ({ page }) => {
  await mount(page);
  const toggle = () => panel(page).getByRole("button", { name: "Search parameters", exact: true });
  const input = () => panel(page).getByRole("searchbox", { name: "Search parameters by name", exact: true });
  await expect(panel(page)).not.toHaveAttribute("open", "");
  await expect(panel(page).locator(".parameters-search")).toBeHidden();
  await toggle().click();
  await expect(input()).toBeFocused();
  await input().fill("cH1");
  await expect(rows(page)).toHaveCount(2);
  await expect(rows(page).locator("dt")).toHaveText(["CH1_INIT_DELAY", "CH1_MAX_DELAY"]);
  await expect(panel(page).locator(".parameters-search-count")).toHaveText(/2\s*\/\s*6/);
  const filtered = await geometry(page);
  expect(filtered[0].y).toBeCloseTo(filtered[1].y, 0);
  expect(filtered[0].x).toBeLessThan(filtered[1].x);
  await expect(panel(page).locator(".parameters-empty")).toBeHidden();
  await panel(page).locator("summary").click();
  await expect(input()).toBeHidden();
  await toggle().click();
  await expect(input()).toBeFocused();
  await expect(input()).toHaveValue("cH1");
  await expect(rows(page)).toHaveCount(2);

  const saved = await page.evaluate(() => window.getSavedTimelineState());
  await mount(page, saved);
  await expect(input()).toBeVisible();
  await expect(input()).toHaveValue("cH1");
  await expect(rows(page)).toHaveCount(2);
  await input().fill("880");
  await expect(rows(page)).toHaveCount(0);
  await expect(panel(page).locator(".parameters-empty")).toBeVisible();
  await expect(panel(page).locator(".parameters-search-count")).toHaveText(/0\s*\/\s*6/);
  await panel(page).getByRole("button", { name: "Clear parameter search", exact: true }).click();
  await expect(input()).toHaveValue("");
  await expect(input()).toBeFocused();
  await expect(rows(page)).toHaveCount(6);
  await expect(panel(page).locator(".parameters-empty")).toBeHidden();
  await input().fill("CH2");
  await input().press("Escape");
  await expect(panel(page).locator(".parameters-search")).toBeHidden();
  await expect(toggle()).toBeFocused();
  await expect(rows(page)).toHaveCount(6);
  await toggle().click();
  await expect(input()).toHaveValue("");
});

test("Edit appears on hover or keyboard focus and keeps parameter editing intact", async ({ page }) => {
  await mount(page);
  const search = panel(page).getByRole("button", { name: "Search parameters", exact: true });
  const query = panel(page).getByRole("searchbox", { name: "Search parameters by name", exact: true });
  await search.click();
  await query.fill("a");
  await page.mouse.move(0, 0);
  const row = rows(page).first();
  const edit = row.getByRole("button", { name: "Edit ACQ_DUR", exact: true });
  const otherEdit = rows(page).nth(1).getByRole("button", { name: "Edit CH1_INIT_DELAY", exact: true });
  await expect(edit).toHaveCSS("opacity", "0");
  await row.hover();
  await expect(edit).toHaveCSS("opacity", "1");
  await page.mouse.move(0, 0);
  await expect(edit).toHaveCSS("opacity", "0");
  await panel(page).locator(".parameters-list-scroll").focus();
  await page.keyboard.press("Tab");
  await expect(edit).toBeFocused();
  await expect(edit).toHaveCSS("opacity", "1");
  await page.keyboard.press("Enter");
  const input = row.getByRole("textbox", { name: "ACQ_DUR JSON value", exact: true });
  await expect(input).toBeFocused();
  await expect(input).toHaveValue("40");
  await expect(search).toBeDisabled();
  await expect(query).toBeDisabled();
  await expect(panel(page).getByRole("button", { name: "Clear parameter search", exact: true })).toBeDisabled();
  await expect(otherEdit).toBeDisabled();
  await expect(otherEdit).toHaveCSS("opacity", "0");
  await input.fill("64");
  await input.press("Enter");
  await expect(otherEdit).toHaveCSS("opacity", "0");
  expect(await page.evaluate(() => window.messages.at(-1))).toEqual({
    type: "setAnalysisParameter", name: "ACQ_DUR", valueJson: "64", projectFile,
  });
  await page.evaluate(() => window.dispatchEvent(new MessageEvent("message", { data: {
    type: "analysisParameterError", name: "ACQ_DUR", message: "Invalid JSON value",
  } })));
  await expect(row.getByRole("alert")).toHaveText("Invalid JSON value");
  await input.press("Escape");
  await expect(input).toBeHidden();
  await expect(edit).toBeFocused();
  await expect(edit).toHaveCSS("opacity", "1");
  await expect(search).toBeEnabled();
  await expect(query).toBeEnabled();
  await expect(query).toHaveValue("a");
});

test.describe("touch Parameters", () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 400, height: 900 } });
  test("Edit remains visible without hover", async ({ page }) => {
    await mount(page);
    await panel(page).locator("summary").tap();
    await expect(panel(page).getByRole("button", { name: "Edit ACQ_DUR", exact: true })).toHaveCSS("opacity", "1");
  });
});
