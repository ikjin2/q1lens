import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { chromium, expect, type Browser } from "@playwright/test";
import * as vscode from "vscode";

async function frameWith(browser: Browser, selector: string) {
  const deadline = Date.now() + 25000;
  while (Date.now() < deadline) {
    for (const page of browser.contexts().flatMap((context) => context.pages())) {
      page.setDefaultTimeout(10000);
      for (const frame of page.frames()) {
        if (await frame.locator(selector).first().isVisible().catch(() => false)) return frame;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 80));
  }
  throw new Error(`Missing verification control: ${selector}`);
}

async function enableAutomaticVerification() {
  const config = vscode.workspace.getConfiguration("q1lens.q1timeline.verification");
  const original = config.inspect<boolean>("autoRun")?.workspaceValue;
  await config.update("autoRun", true, vscode.ConfigurationTarget.Workspace);
  return () => config.update("autoRun", original, vscode.ConfigurationTarget.Workspace);
}

async function checkQueueReplay(browser: Browser) {
  const replay = await frameWith(browser, "#inputs:text-matches('N=38')");
  assert.match(await replay.locator("#finding").innerText(), /N=1\.\.37 safe/);
  assert.match(await replay.locator("#finding").getAttribute("title") || "", /N=1\.\.37 safe.*N=38/);
  const snapshot = JSON.parse((await replay.locator("#witness").textContent())!).counterexample;
  assert.equal(await replay.locator("#occurrence").getAttribute("data-event-id"), snapshot.event_id);
  const failure = snapshot.timeline.events.find((e: { id: string }) => e.id === snapshot.event_id);
  assert.equal(snapshot.inputs.N, 38);
  assert.equal(snapshot.time_ns, 1188);
  assert.equal(failure.kind, "underflow_warning");
  assert.equal(failure.t0.kind, "concrete");
  assert.equal(failure.t0.value, snapshot.time_ns);
  await expect(replay.locator("#occurrence")).toContainText("1188 ns");
}

describe("Verification in VS Code", function () {
  this.timeout(90000);

  it("toggles automatic verification from the Q1ASM status bar and keeps manual verification available", async () => {
    const source = vscode.Uri.joinPath(vscode.workspace.workspaceFolders![0].uri, "verification-toggle.q1asm");
    await writeFile(source.fsPath, "move 17,R0\nnop\n;@assert R0 == 10\nstop\n");
    const config = () => vscode.workspace.getConfiguration("q1lens.q1timeline.verification", source);
    const savedChoice = () => config().inspect<boolean>("autoRun")?.workspaceValue;
    const original = savedChoice();
    const lenses = async () => (await vscode.commands.executeCommand<vscode.CodeLens[]>("vscode.executeCodeLensProvider", source))
      .flatMap((lens) => lens.command?.command === "q1timeline.openVerificationWitness" ? [lens.command.title] : []);
    const manual = () => vscode.commands.executeCommand<{ checks: { text: string }[] } | undefined>(
      "q1timeline.verify", source, undefined, undefined, true);
    const diagnostics = () => vscode.languages.getDiagnostics(source).filter((d) => d.source === "Q1Lens verification");
    const browser = await chromium.connectOverCDP(`http://127.0.0.1:${process.env.Q1LENS_DEF_WEBVIEW_CDP_PORT}`);
    try {
      assert.equal(original, undefined);
      assert.equal(config().inspect<boolean>("autoRun")?.defaultValue, false);
      const editor = await vscode.window.showTextDocument(source);
      const frame = await frameWith(browser, '.statusbar-item:has-text("Z3 Auto: Off")');
      const toggle = frame.locator(".statusbar-item").filter({ hasText: "Z3 Auto:" });
      assert.deepEqual(await lenses(), []);
      assert.deepEqual(diagnostics(), []);
      await toggle.click();
      await expect(toggle).toContainText("On");
      assert.equal(savedChoice(), true);
      await expect.poll(lenses).toContainEqual(expect.stringMatching(/Counterexample.*R0=17/));
      assert.equal(diagnostics().length, 1);
      await toggle.click();
      await expect(toggle).toContainText("Off");
      assert.equal(savedChoice(), false);
      await expect.poll(diagnostics).toHaveLength(0);
      assert.match((await manual())?.checks[0].text || "", /Counterexample.*R0=17/);
      assert.deepEqual(await lenses(), []);

      await editor.edit((edit) => edit.replace(editor.document.lineAt(0).range, "move 18,R0"));
      await editor.document.save();
      await vscode.commands.executeCommand("workbench.action.closeActiveEditor");
      await vscode.window.showTextDocument(source);
      assert.deepEqual(await lenses(), []);
      assert.deepEqual(diagnostics(), []);
      assert.match((await manual())?.checks[0].text || "", /Counterexample.*R0=18/);

      await toggle.click();
      await expect(toggle).toContainText("On");
      await expect.poll(lenses).toContainEqual(expect.stringMatching(/Counterexample.*R0=18/));
      await Promise.all([vscode.commands.executeCommand("q1timeline.toggleAutoVerification"),
        vscode.commands.executeCommand("q1timeline.toggleAutoVerification")]);
      assert.equal(savedChoice(), true);
      await vscode.window.showTextDocument(await vscode.workspace.openTextDocument({ language: "plaintext" }));
      await expect(toggle).toBeHidden();
    } finally {
      await config().update("autoRun", original, vscode.ConfigurationTarget.Workspace);
      await browser.close();
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  });

  it("refreshes a proved result after a closed workspace parameter file changes on disk", async () => {
    const dir = vscode.Uri.joinPath(vscode.workspace.workspaceFolders![0].uri, "verification-input-watch");
    const source = vscode.Uri.joinPath(dir, "read.q1asm");
    const project = vscode.Uri.joinPath(dir, "q1timeline.yml");
    const params = vscode.Uri.joinPath(dir, "inputs.data");
    await mkdir(dir.fsPath, { recursive: true });
    await writeFile(project.fsPath, "params: {file: inputs.data}\nsequencers: [{id: seq, file: read.q1asm}]\n");
    await writeFile(params.fsPath, '{"VALUE":17}');
    await writeFile(source.fsPath, "move {VALUE},R0\nnop\n;@assert R0 == 17\nstop\n");
    const verify = () => vscode.commands.executeCommand<{ checks: { text: string }[] } | undefined>(
      "q1timeline.verify", source, project, "seq", true);
    const waitForResult = async (expected: RegExp) => {
      const deadline = Date.now() + 10000;
      let message = "";
      do {
        message = (await verify())?.checks[0]?.text || "";
        if (expected.test(message)) return;
        await new Promise((resolve) => setTimeout(resolve, 150));
      } while (Date.now() < deadline);
      assert.match(message, expected);
    };
    await waitForResult(/Proved/);
    await writeFile(params.fsPath, '{"VALUE":31}');
    await waitForResult(/Counterexample.*R0=31/);
  });

  it("shows three physical ports and replays the first clipping sample from the source CodeLens", async () => {
    const source = vscode.Uri.joinPath(vscode.workspace.workspaceFolders![0].uri, "examples", "symbolic-output", "drive.q1asm");
    const browser = await chromium.connectOverCDP(`http://127.0.0.1:${process.env.Q1LENS_DEF_WEBVIEW_CDP_PORT}`);
    const restore = await enableAutomaticVerification();
    try {
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
      await vscode.commands.executeCommand("q1timeline.openPreview", source);
      const frame = await frameWith(browser, "#q1timeline-analysis-details");
      const details = frame.locator("#q1timeline-analysis-details");
      if (await details.getAttribute("open") === null) await details.locator(":scope > summary").click();
      const ports = frame.locator(".hardware-rack");
      await ports.waitFor({ state: "visible" });
      assert.deepEqual(await ports.locator(".rack-row.is-module strong").allTextContents(), ["Pulse", "Mix"]);
      assert.deepEqual(await ports.locator(".rack-row.is-port strong").allTextContents(), ["O1", "O2", "O1"]);
      // Mix O1 is the third port; drive feeds its I path from 4 ns to 24 ns.
      const interval = ports.locator('[data-lane="path|2|drive"] .rack-tag').nth(1);
      const event = await interval.getAttribute("data-rack-event");
      assert.ok(event);
      const output = process.env.Q1LENS_EXTENSION_HOST_OUTPUT!;
      await ports.screenshot({ path: join(output, "hardware-ports.png") });
      await interval.click();
      await frame.locator(`.timeline-block.is-selected[data-event-id="${event}"]`).first().waitFor({ state: "attached" });
      // A typed value travels through the extension to the output session and returns resampled.
      const range = ports.locator('[data-rack="range"]');
      await expect(range).toHaveText("0 ns to 140 ns");
      const count = ports.getByLabel("N, set by the project");
      await count.fill("6");
      await count.press("Enter");
      await expect(range).toHaveText("0 ns to 239 ns");
      await expect(ports.locator(".rack-param.is-trial")).toContainText("trying");
      await expect(ports.locator('[data-rack="note"]')).toBeHidden();
      await ports.screenshot({ path: join(output, "hardware-ports-trial.png") });
      await ports.getByRole("button", { name: "Use project values", exact: true }).click();
      await expect(range).toHaveText("0 ns to 140 ns");
      await vscode.window.showTextDocument(source);
      const lenses = await vscode.commands.executeCommand<vscode.CodeLens[]>("vscode.executeCodeLensProvider", source);
      const replay = lenses.find((item) => item.range.start.line === 3)?.command;
      assert.ok(replay);
      assert.match(replay.title, /4 ns/);
      await vscode.commands.executeCommand(replay.command, ...replay.arguments!);
      const witness = await frameWith(browser, "#occurrence[data-event-id]");
      const { counterexample } = JSON.parse((await witness.locator("#witness").textContent())!);
      assert.equal(counterexample.time_ns, 4);
      assert.deepEqual(counterexample.inputs, { G: 28664 });
      assert.equal(counterexample.value, 1.124755859375);
      assert.match(await witness.locator("#source").innerText(), /drive\.q1asm:7/);
      assert.equal(await witness.locator("#occurrence").getAttribute("data-event-id"), counterexample.event_id);
      await witness.locator(".cx-head").screenshot({ path: join(output, "first-clip.png") });
    } finally {
      await restore();
      await browser.close();
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  });

  it("opens the failing assertion at its exact occurrence with the observed register history", async () => {
    const root = vscode.workspace.workspaceFolders![0].uri;
    const source = vscode.Uri.joinPath(root, "examples", "verification-demo", "assertions.q1asm");
    const browser = await chromium.connectOverCDP(`http://127.0.0.1:${process.env.Q1LENS_DEF_WEBVIEW_CDP_PORT}`);
    const restore = await enableAutomaticVerification();
    try {
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
      await vscode.window.showTextDocument(source);
      const lenses = await vscode.commands.executeCommand<vscode.CodeLens[]>("vscode.executeCodeLensProvider", source);
      const pass = lenses.find((l) => l.range.start.line === 2);
      assert.match(pass?.command?.title || "", /Proved.*20000/);
      const lens = lenses.find((l) => l.range.start.line === 3);
      assert.match(lens?.command?.title || "", /Counterexample.*R2=1024.*N=513/);
      assert.ok(vscode.languages.getDiagnostics(source).some((d) => d.source === "Q1Lens verification" && d.range.start.line === 3));
      const lensCommand = lens!.command!;
      await vscode.commands.executeCommand(lensCommand.command, ...lensCommand.arguments!);
      const frame = await frameWith(browser, "#occurrence[data-event-id]");
      const finding = JSON.parse((await frame.locator("#witness").textContent())!).counterexample;
      assert.equal(await frame.locator("#occurrence").getAttribute("data-event-id"), finding.event_id);
      assert.equal(await frame.locator(".timeline-block.is-selected").first().getAttribute("data-event-id"), finding.event_id);
      assert.match(await frame.locator('[data-value="register-history-count"]').innerText(), /R2.*1 \/ 1/);
      assert.match(await frame.locator('[data-value="register-history"]').innerText(), /1024/);
      // The header names the counterexample's source line; that link opens it.
      assert.match(await frame.locator("#source").innerText(), /^assertions\.q1asm:\d+$/);
      assert.match(await frame.locator("#inputs").innerText(), /N=513/);
      await frame.locator("#source").click();
      const editor = vscode.window.visibleTextEditors.find((item) => item.document.uri.toString() === source.toString());
      await new Promise<void>((resolve, reject) => {
        const deadline = Date.now() + 5000;
        const timer = setInterval(() => {
          if (editor?.selection.active.line === finding.line - 1) { clearInterval(timer); resolve(); }
          else if (Date.now() > deadline) { clearInterval(timer); reject(new Error("Witness source selection did not update.")); }
        }, 50);
      });
    } finally {
      await restore();
      await browser.close();
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  });

  it("finds the first queue failure from Sweep without choosing a range and focuses the underflow", async () => {
    const source = vscode.Uri.joinPath(vscode.workspace.workspaceFolders![0].uri, "examples", "verification-demo", "queue.q1asm");
    const browser = await chromium.connectOverCDP(`http://127.0.0.1:${process.env.Q1LENS_DEF_WEBVIEW_CDP_PORT}`);
    try {
      await vscode.commands.executeCommand("q1timeline.openPreview", source);
      const frame = await frameWith(browser, "#q1timeline-sweep-toggle");
      await frame.locator("#q1timeline-sweep-toggle").click();
      await frame.locator('[data-field="seq"]').selectOption("queue");
      await frame.getByRole("button", { name: "Verify saved program" }).click();
      const results = frame.locator('[data-field="verification"]');
      await results.getByRole("button", { name: "Open counterexample timeline" }).click({ timeout: 25000 });
      await checkQueueReplay(browser);
    } finally {
      await browser.close();
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  });

  it("opens the queue underflow from Verify Program at 1188 ns", async () => {
    const source = vscode.Uri.joinPath(vscode.workspace.workspaceFolders![0].uri, "examples", "verification-demo", "queue.q1asm");
    const browser = await chromium.connectOverCDP(`http://127.0.0.1:${process.env.Q1LENS_DEF_WEBVIEW_CDP_PORT}`);
    try {
      await vscode.window.showTextDocument(source);
      const verify = vscode.commands.executeCommand("q1timeline.verify");
      const frame = await frameWith(browser, '.notifications-toasts .monaco-button:text-is("Open counterexample timeline")');
      await frame.getByRole("button", { name: "Open counterexample timeline", exact: true }).click();
      await verify;
      await checkQueueReplay(browser);
    } finally {
      await browser.close();
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  });

  it("checks sixteen shared outcomes from the timeline and opens the late sequencer on the project clock", async () => {
    const dir = vscode.Uri.joinPath(vscode.workspace.workspaceFolders![0].uri, "examples", "verification-demo", "alignment");
    const browser = await chromium.connectOverCDP(`http://127.0.0.1:${process.env.Q1LENS_DEF_WEBVIEW_CDP_PORT}`);
    try {
      for (const [folder, expected] of [[dir, /Proved.*16/], [vscode.Uri.joinPath(dir, "late"), /Counterexample.*20 ns/]] as const) {
        await vscode.commands.executeCommand("workbench.action.closeAllEditors");
        await vscode.commands.executeCommand("q1timeline.openPreview", vscode.Uri.joinPath(folder, "drive.q1asm"));
        const preview = await frameWith(browser, '#q1timeline-toolbar button[aria-label="Verify alignment"]');
        await preview.getByRole("button", { name: "Verify alignment" }).click();
        const report = preview.locator("#q1timeline-verified-alignment");
        await report.getByText(expected).waitFor({ timeout: 25000 });
        if (folder !== dir) {
          await report.getByRole("button", { name: "Open counterexample timeline" }).click();
          const replay = await frameWith(browser, "#finding:text-matches('20 ns')");
          assert.match(await replay.locator("#occurrence").innerText(), /seq3.*268 ns/);
          assert.match(await replay.locator("#inputs").innerText(), /m3=1/);
        }
      }
    } finally {
      await browser.close();
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  });

  it("opens both versions of a differing RT trace at the first mismatched command", async () => {
    const dir = vscode.Uri.joinPath(vscode.workspace.workspaceFolders![0].uri, "examples", "verification-demo");
    const browser = await chromium.connectOverCDP(`http://127.0.0.1:${process.env.Q1LENS_DEF_WEBVIEW_CDP_PORT}`);
    try {
      const task = vscode.commands.executeCommand("q1timeline.verifyEquivalence",
        vscode.Uri.joinPath(dir, "before.q1asm"), vscode.Uri.joinPath(dir, "changed.q1asm"));
      const notification = await frameWith(browser, '.notifications-toasts button:text-is("Open counterexample timeline"), .notifications-toasts .monaco-button:text-is("Open counterexample timeline")');
      await notification.getByRole("button", { name: "Open counterexample timeline", exact: true }).click();
      await task;
      const replay = await frameWith(browser, "#inputs:text-matches('N=7')");
      assert.match(await replay.locator("#occurrence").innerText(), /after.*100 ns/);
      const snapshot = JSON.parse((await replay.locator("#witness").textContent())!).counterexample;
      assert.deepEqual(snapshot.timeline.sequencers.map((seq: { id: string }) => seq.id).sort(), ["after", "before"]);
      assert.equal(snapshot.observations.after.end_ns - snapshot.observations.before.end_ns, 4);
    } finally {
      await browser.close();
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  });
});
