/// <reference lib="dom" />
import assert from "node:assert/strict";
import { chromium, expect, type Browser, type Frame } from "@playwright/test";
import * as vscode from "vscode";

const LOOP = `        move 3, R0
        move 0, R2
        nop
body:   wait 100
        fb_pop_data 16, R1
        nop
        cmp R1, 5
        jge @high
        add R2, 1, R2
        jmp @next
high:   add R2, 10, R2
next:   nop
        loop R0, @body
        stop
`;

async function lanesFrame(browser: Browser): Promise<Frame> {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    for (const context of browser.contexts()) {
      for (const page of context.pages()) {
        for (const frame of page.frames()) {
          try {
            if (await frame.locator("#decision-lanes #dl-controls").isVisible()) return frame;
          } catch {
            // The webview frame may still be loading.
          }
        }
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 75));
  }
  throw new Error("The Decision lanes webview did not open");
}

describe("Decision lanes in VS Code", function () {
  this.timeout(120000);

  it("records a Q1ASM file with the Python worker, follows typed feedback words, saved edits and source lines", async () => {
    const workspace = vscode.workspace.workspaceFolders?.[0]?.uri;
    assert.ok(workspace);
    const folder = vscode.Uri.joinPath(workspace, "decision-lanes-host");
    const source = vscode.Uri.joinPath(folder, "loop.q1asm");
    await vscode.workspace.fs.createDirectory(folder);
    await vscode.workspace.fs.writeFile(source, Buffer.from(LOOP));
    const document = await vscode.workspace.openTextDocument(source);
    await vscode.window.showTextDocument(document, vscode.ViewColumn.One);
    const port = process.env.Q1LENS_DEF_WEBVIEW_CDP_PORT;
    assert.ok(port, "Run this suite through npm run test:extension");
    const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
    const fold = (frame: Frame) => frame.locator("#dl-fold");
    try {
      await vscode.commands.executeCommand("q1timeline.openDecisionLanes", source);
      const frame = await lanesFrame(browser);
      // Without remembered inputs every feedback word reads 0: all three passes continue.
      await expect(frame.locator("#dl-detail .dhead h2")).toHaveText("Decision 1 / 3", { timeout: 30000 });
      await expect(fold(frame)).toHaveText("3 loop passes = L9 × 3 (L8 Continue)");
      await expect(frame.locator("#dl-feedback")).toHaveValue("0");
      // The worker then records the other arm of each decision.
      await expect(frame.locator("#dl-detail .badge")).toHaveText("Changes state only", { timeout: 30000 });
      await expect(frame.locator("#dl-run option")).toHaveCount(4, { timeout: 30000 });
      // Colours are inline styles; they have to survive the panel's content security policy.
      const stroke = await frame.locator("#dl-lanes-svg .series").first().evaluate((path) => getComputedStyle(path).stroke);
      assert.match(stroke, /^rgb/);
      const tab = vscode.window.tabGroups.all.flatMap((group) => group.tabs).find((item) => item.label.startsWith("Decision lanes"));
      assert.equal(tab?.label, "Decision lanes · loop.q1asm");

      // Typed feedback words make a new record.
      await frame.locator("#dl-feedback").fill("7, 2");
      await frame.locator("#dl-feedback").press("Enter");
      await expect(fold(frame)).toHaveText("3 loop passes = high × 2 (L8 Jump) + L9 × 1 (L8 Continue)", { timeout: 30000 });
      await expect(frame.locator("#dl-controls .status")).toContainText("3 feedback words read: 7, 2 (repeating)");

      // Selecting a decision selects its branch line in the editor that shows the file.
      await frame.locator('#dl-lanes-svg [data-action="select"]').nth(1).click();
      await expect(frame.locator("#dl-detail .dhead h2")).toHaveText("Decision 2 / 3");
      await expect.poll(() => vscode.window.visibleTextEditors.find((editor) => editor.document.uri.fsPath === source.fsPath)?.selection.start.line,
        { timeout: 15000 }).toBe(7);
      // A step of the data flow opens its own line.
      await frame.locator("#dl-detail .chain button.input").first().click();
      await expect.poll(() => vscode.window.visibleTextEditors.find((editor) => editor.document.uri.fsPath === source.fsPath)?.selection.start.line,
        { timeout: 15000 }).toBe(4);

      // An unsaved edit is announced; saving records the new program with the same words.
      const edit = new vscode.WorkspaceEdit();
      edit.replace(source, new vscode.Range(6, 16, 6, 17), "9");          // cmp R1, 5 -> cmp R1, 9
      assert.equal(await vscode.workspace.applyEdit(edit), true);
      await expect(frame.locator("#dl-banner")).toContainText("Unsaved changes are not included", { timeout: 15000 });
      await expect(fold(frame)).toHaveText("3 loop passes = high × 2 (L8 Jump) + L9 × 1 (L8 Continue)");
      assert.equal(await document.save(), true);
      await expect(fold(frame)).toHaveText("3 loop passes = L9 × 3 (L8 Continue)", { timeout: 30000 });
      await expect(frame.locator("#dl-banner")).toBeHidden();
      await expect(frame.locator("#dl-feedback")).toHaveValue("7, 2");
    } finally {
      await browser.close();
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  });

  it("explains that only a saved Q1ASM file can be recorded", async () => {
    const workspace = vscode.workspace.workspaceFolders?.[0]?.uri;
    assert.ok(workspace);
    const notes = vscode.Uri.joinPath(workspace, "decision-lanes-host", "notes.txt");
    await vscode.workspace.fs.writeFile(notes, Buffer.from("not a program\n"));
    const before = vscode.window.tabGroups.all.flatMap((group) => group.tabs).length;
    await vscode.commands.executeCommand("q1timeline.openDecisionLanes", notes);
    assert.equal(vscode.window.tabGroups.all.flatMap((group) => group.tabs).length, before);
  });
});
