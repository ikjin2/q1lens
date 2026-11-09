/// <reference lib="dom" />
import assert from "node:assert/strict";
import { chromium, type Browser, type Frame } from "@playwright/test";
import * as vscode from "vscode";

async function webview(browser: Browser, source?: string): Promise<Frame> {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    for (const context of browser.contexts()) {
      for (const page of context.pages()) {
        for (const frame of page.frames()) {
          try {
            const toggle = frame.locator("#q1timeline-sweep-toggle");
            if (await toggle.isVisible() && (!source || (await frame.locator("#q1timeline-webview-state").getAttribute("data-state"))?.includes(source))) return frame;
          } catch {
            // The preview may be replacing its iframe during analysis.
          }
        }
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 75));
  }
  throw new Error(`Q1Lens webview did not open${source ? ` for ${source}` : ""}`);
}

describe("DEF execution and sweep in VS Code", function () {
  this.timeout(90000);

  it("runs the real Python worker from the timeline, cancels a sweep and follows a result", async () => {
    const workspace = vscode.workspace.workspaceFolders?.[0]?.uri;
    assert.ok(workspace);
    const source = vscode.Uri.joinPath(workspace, "examples", "def-register-sweep", "sum.q1asm");
    const params = vscode.Uri.joinPath(workspace, "examples", "def-register-sweep", "params.json");
    const original = await vscode.workspace.fs.readFile(source), originalParams = await vscode.workspace.fs.readFile(params);
    const port = process.env.Q1LENS_DEF_WEBVIEW_CDP_PORT;
    assert.ok(port, "Run this suite through npm run test:extension");
    const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
    try {
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
      await vscode.commands.executeCommand("q1timeline.openPreview", source);
      let frame = await webview(browser);
      const waitText = async (selector: string, value: RegExp | string) => {
        await frame.locator(selector).filter({ hasText: value }).waitFor({ state: "visible", timeout: 15000 });
      };
      const open = frame.locator('#q1timeline-layout-toggle + button[aria-controls="q1timeline-def-runs"]');
      assert.equal(await open.textContent(), "Sweep");
      await open.click();
      await waitText('[data-field="source"]', "sum.q1asm");
      const r0 = frame.getByRole("checkbox", { name: "R0", exact: true });
      const r2 = frame.getByRole("checkbox", { name: "R2", exact: true });
      const time = frame.getByRole("checkbox", { name: "Q1 time", exact: true });
      const r2Stats = '[data-statistics="R2"]';
      const registerChoices = frame.locator(".def-register-choices");
      assert.deepEqual(await registerChoices.locator("label:visible").allTextContents(), ["R0", "R1", "R2"]);
      await frame.getByRole("button", { name: "Show all registers" }).click();
      assert.equal(await registerChoices.getByRole("checkbox").count(), 64);
      await frame.getByRole("button", { name: "Show referenced registers" }).click();
      await r0.uncheck();
      await r2.check();
      assert.equal(await time.isChecked(), false);
      await frame.getByRole("textbox", { name: "X DEF value" }).fill("2");
      await waitText('[data-field="edited"]', "Changes have not been run");
      await frame.getByRole("button", { name: "Run from start" }).click();
      await waitText('[data-result="word"]', "12");
      assert.equal(await frame.locator('[data-result="time"]').count(), 0);
      const blocks = frame.locator("[data-step]");
      assert.deepEqual(await blocks.allTextContents(), ["move", "move", "nop", "add", "nop", "nop", "nop", "stop"]);
      await blocks.nth(3).click();
      await waitText('[data-field="instruction"]', /Values readR0 2 R1 10/);
      await waitText('[data-field="instruction"]', /R2 12 · readable at 28 ns/);
      await frame.getByRole("button", { name: "Next instruction" }).click();
      await waitText('[data-field="instruction"]', /Pending at instruction startR2 12/);
      assert.equal(await blocks.nth(4).getAttribute("aria-pressed"), "true");
      await frame.getByRole("button", { name: "Reset all DEFs" }).click();
      await frame.getByRole("button", { name: "Run from start" }).click();
      await waitText('[data-result="word"]', "10");
      assert.deepEqual(await vscode.workspace.fs.readFile(source), original);
      assert.deepEqual(await vscode.workspace.fs.readFile(params), originalParams);

      await frame.getByLabel("Axis 1 max").fill("4096");
      await waitText('[data-field="plan"]', /4097 combinations.*Maximum 4,096/);
      assert.equal(await frame.getByRole("button", { name: "Start sweep" }).isDisabled(), true);
      await frame.getByLabel("Axis 1 max").fill("4095");
      await waitText('[data-field="plan"]', "4096 combinations");
      await frame.getByRole("button", { name: "Start sweep" }).click();
      await waitText('[data-cases-reported]', /[1-9]\d* \/ 4096 cases reported/);
      await frame.getByRole("button", { name: "Stop sweep" }).click();
      await waitText('[data-status="cancelled"]', "Cancelled");
      await waitText('.def-counts', /Unexecuted [1-9]/);

      await frame.getByLabel("Axis 1 max").fill("2");
      await waitText('[data-field="plan"]', "3 combinations");
      await time.check();
      await frame.getByRole("button", { name: "Start sweep" }).click();
      await waitText(`${r2Stats} [data-stat="deviation"]`, "0.816496580928");
      assert.equal(await frame.locator(`${r2Stats} [data-stat="mean"]`).textContent(), "11");
      assert.equal(await blocks.nth(0).getAttribute("aria-pressed"), "true");
      await frame.getByLabel("Plot metric").selectOption("time");
      const timePoint = frame.locator('svg[aria-label="DEF input versus Q1 time"] [data-case="3"]');
      assert.match(await timePoint.getAttribute("aria-label") || "", /Q1 time 40 ns/);
      await timePoint.click();
      await waitText('[data-field="trace-heading"]', "sweep case 3");
      await waitText('[data-field="result"]', "X=2, Y=10");
      assert.equal(await frame.locator('[data-result="time"]').textContent(), "40 ns");
      assert.equal(await frame.locator('[data-field="result"] code').textContent(), "0x0000000C");
      await blocks.nth(3).click();
      await waitText('[data-field="instruction"]', /R2 12 · readable at 28 ns/);
      await frame.getByLabel("Interpretation").selectOption("unsigned");
      await waitText('[data-field="result"] dt', "R2 · unsigned 32-bit");
      assert.equal(await blocks.nth(3).getAttribute("aria-pressed"), "true");
      assert.equal(await frame.locator(`${r2Stats} [data-stat="max"]`).textContent(), "12");

      await r0.check();
      const r1 = frame.getByRole("checkbox", { name: "R1", exact: true });
      await r1.check();
      await frame.getByLabel("Sweep axis 2").selectOption("Y");
      await frame.getByLabel("Axis 2 max").fill("12");
      await frame.getByLabel("Axis 2 step").fill("2");
      await waitText('[data-field="plan"]', "6 combinations");
      await frame.getByRole("button", { name: "Start sweep" }).click();
      await waitText(`${r2Stats} [data-stat="deviation"]`, "1.29099444874");
      assert.equal(await frame.locator(`${r2Stats} [data-stat="count"]`).textContent(), "6");
      assert.equal(await blocks.nth(3).getAttribute("aria-pressed"), "true");
      await frame.locator('svg[aria-label="Two-axis DEF heatmap by Q1 time"]').waitFor({ state: "visible" });
      await frame.getByRole("textbox", { name: "Formula", exact: true }).fill("atan2(R1, R0)");
      await frame.getByRole("button", { name: "Plot formula" }).click();
      const formulaPoint = frame.locator('svg[aria-label="Two-axis DEF heatmap by Formula"] [data-case="5"]');
      assert.match(await formulaPoint.getAttribute("aria-label") || "", /Formula 1\.37340076695/);
      await formulaPoint.click();
      await waitText('[data-field="trace-heading"]', "sweep case 5");
      await waitText('[data-field="result"]', "X=2, Y=10");
      assert.equal(await frame.locator('[data-result="word"][data-observed="R2"]').textContent(), "12");

      await frame.getByLabel("Sweep axis 2").selectOption("");
      await r1.uncheck();
      const output = frame.getByRole("combobox", { name: "Results", exact: true });
      await output.selectOption("statistics");
      await r0.check();
      await time.uncheck();
      await frame.getByLabel("Axis 1 max").fill("1000000");
      await waitText('[data-field="plan"]', /1000001 combinations.*Maximum 1,000,000/);
      assert.equal(await frame.getByRole("button", { name: "Start sweep" }).isDisabled(), true);
      await frame.getByLabel("Axis 1 max").fill("4096");
      await waitText('[data-field="plan"]', "4097 combinations");
      await frame.getByRole("button", { name: "Start sweep" }).click();
      await waitText(`${r2Stats} [data-stat="count"]`, "4097");
      assert.equal(await frame.locator(`${r2Stats} [data-stat="mean"]`).textContent(), "2058");
      assert.equal(await frame.locator('[data-statistics="R0"] [data-stat="mean"]').textContent(), "2048");
      assert.equal(await frame.locator('[data-time-statistics]').count(), 0);
      assert.equal(await frame.locator('[data-field="sweep-results"] svg, button[data-case]').count(), 0);

      await frame.getByLabel("Axis 1 max").fill("999999");
      await waitText('[data-field="plan"]', "1000000 combinations");
      await frame.getByRole("button", { name: "Start sweep" }).click();
      await waitText('[data-cases-reported]', /[1-9]\d* \/ 1000000 cases reported/);
      await frame.getByRole("button", { name: "Stop sweep" }).click();
      await waitText('[data-status="cancelled"]', "Cancelled");
      assert.ok(Number(await frame.locator(`${r2Stats} [data-stat="count"]`).textContent()) > 0);
      await output.selectOption("cases");
      await frame.getByLabel("Axis 1 max").fill("4095");
      await waitText('[data-field="plan"]', "4096 combinations");
      await frame.getByRole("button", { name: "Start sweep" }).click();
      await waitText('[data-status="running"]', "Running");
      await waitText('[data-cases-reported]', /[1-9]\d* \/ 4096 cases reported/);
      const doc = await vscode.workspace.openTextDocument(source);
      const edit = new vscode.WorkspaceEdit();
      edit.insert(doc.uri, new vscode.Position(0, 0), "# source revision\n");
      assert.equal(await vscode.workspace.applyEdit(edit), true);
      await waitText('[data-field="progress"]', /Stale/);
      await waitText('[data-field="message"]', "Save the project and Q1ASM sources");
      await waitText('[data-field="trace-heading"]', "sweep case 5");
      assert.equal(await frame.locator('button[data-case="1"]').isDisabled(), true);
      assert.equal(await doc.save(), true);

      await vscode.commands.executeCommand("q1timeline.openPreview", vscode.Uri.joinPath(workspace, "examples", "simulation-verification", "01-digital", "counter.q1asm"));
      frame = await webview(browser, "01-digital");
      assert.equal(await frame.locator('[data-statistics]').count(), 0);
      await frame.getByRole("button", { name: "Sweep", exact: true }).click();
      await waitText('[data-field="defs"]', "No editable numeric DEFs");
      assert.equal(await frame.getByRole("button", { name: "Run from start" }).isEnabled(), true);
    } finally {
      await browser.close();
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  });
});
