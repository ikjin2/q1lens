import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import * as vscode from "vscode";
import { DiagnosticsManager } from "../../src/q1timeline/diagnosticsManager";

describe("Q1ASM diagnostic ranges in VS Code", () => {
  it("reports a missing move operand after saving with a preview open and clears it after a fix", async function () {
    this.timeout(30000);
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const root = vscode.workspace.workspaceFolders![0].uri.fsPath;
    const project = mkdtempSync(join(root, "syntax-on-save-"));
    const file = join(project, "sequence.q1asm");
    writeFileSync(file, "move 1,R0\nstop\n");
    writeFileSync(join(project, "q1timeline.yml"), "sequencers:\n  - id: seq0\n    file: sequence.q1asm\n");
    const document = await vscode.workspace.openTextDocument(file);
    await vscode.window.showTextDocument(document);
    await vscode.commands.executeCommand("q1timeline.openPreview", document.uri);
    const timeline = join(project, ".q1timeline", "timeline.html");
    const waitFor = async (description: string, check: () => boolean) => {
      const deadline = Date.now() + 10000;
      while (!check()) {
        assert.ok(Date.now() < deadline, `Timed out waiting for ${description}`);
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
    };
    await waitFor("the initial timeline", () => existsSync(timeline));
    const edit = new vscode.WorkspaceEdit();
    edit.insert(document.uri, document.positionAt(document.getText().length), "move 1\n");
    assert.equal(await vscode.workspace.applyEdit(edit), true);
    assert.equal(await document.save(), true);
    await waitFor("the saved syntax error", () => vscode.languages.getDiagnostics(document.uri)
      .some((item) => item.code === "invalid_argument_count"));
    const diagnostic = vscode.languages.getDiagnostics(document.uri)
      .find((item) => item.code === "invalid_argument_count")!;
    assert.equal(diagnostic.severity, vscode.DiagnosticSeverity.Error);
    assert.equal(diagnostic.range.start.line, 2);
    assert.equal(document.getText(diagnostic.range), "move 1");
    assert.match(diagnostic.message, /move expects 2 argument\(s\), got 1/);

    const fix = new vscode.WorkspaceEdit();
    fix.replace(document.uri, diagnostic.range, "move 1,R1");
    assert.equal(await vscode.workspace.applyEdit(fix), true);
    assert.equal(await document.save(), true);
    await waitFor("the corrected timeline and cleared syntax error", () => existsSync(timeline)
      && !vscode.languages.getDiagnostics(document.uri).some((item) => item.severity === vscode.DiagnosticSeverity.Error));
  });

  it("shows confirmed RT underflow as an error while retaining its timeline", async function () {
    this.timeout(30000);
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const root = vscode.workspace.workspaceFolders![0].uri.fsPath;
    const project = mkdtempSync(join(root, "underflow-severity-"));
    const file = join(project, "sequence.q1asm");
    writeFileSync(file, "upd_param 4\nmove 89286,R0\nbody: nop\nloop R0,@body\nupd_param 1000 # late RT command\nstop\n");
    writeFileSync(join(project, "q1timeline.yml"), "analysis:\n  execution_mode: analysis\nsequencers:\n  - id: seq0\n    file: sequence.q1asm\n");
    const document = await vscode.workspace.openTextDocument(file);
    await vscode.window.showTextDocument(document);
    await vscode.commands.executeCommand("q1timeline.openPreview", document.uri);
    const output = join(project, ".q1timeline");
    const deadline = Date.now() + 10000;
    while (!existsSync(join(output, "timeline.html")) || !vscode.languages.getDiagnostics(document.uri).some((item) => item.code === "definite_underflow")) {
      assert.ok(Date.now() < deadline, "Timed out waiting for the underflow timeline and diagnostic");
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    const ir = JSON.parse(readFileSync(join(output, "timeline_ir.json"), "utf8"));
    assert.equal(ir.diagnostics.find((item: any) => item.category === "definite_underflow").severity, "error");
    const html = readFileSync(join(output, "timeline.html"), "utf8");
    assert.ok(html.includes('class="diagnostic severity-error"'));
    assert.ok(html.includes("RT underflow"));
    const diagnostic = vscode.languages.getDiagnostics(document.uri).find((item) => item.code === "definite_underflow")!;
    assert.equal(diagnostic.severity, vscode.DiagnosticSeverity.Error);
    assert.equal(document.getText(diagnostic.range), "upd_param 1000");
    assert.ok(vscode.window.tabGroups.all.some((group) => group.tabs.some((tab) =>
      tab.input instanceof vscode.TabInputWebview && tab.input.viewType.includes("q1timeline.preview"))));
  });

  it("underlines commands and arguments without their labels or trailing comments", async () => {
    const output = process.env.Q1LENS_EXTENSION_HOST_OUTPUT!;
    mkdirSync(output, { recursive: true });
    const file = join(mkdtempSync(join(output, "diagnostic-ranges-")), "sequence.q1asm");
    const lines = [
      "body: upd_param 4   # first RT command",
      "  play 0, 1, 4 // waveform",
      "  wait {DURATION // 2} # floor division is part of the argument",
      "  stop   ",
    ];
    writeFileSync(file, lines.join("\n"));
    const document = await vscode.workspace.openTextDocument(file);
    const collection = vscode.languages.createDiagnosticCollection("q1lens-underline-test");
    try {
      const manager = new DiagnosticsManager(vscode, collection, (sourceFile: string) => sourceFile);
      manager.apply(["error", "warning", "information", "hint"].map((severity, index) => ({
        severity, category: "timing", message: "Instruction diagnostic",
        source: { file, line: index + 1, column: 8, raw: "stop # stale analysis source" },
      })));
      const diagnostics = collection.get(document.uri)!;
      assert.deepEqual(diagnostics.map((diagnostic) => document.getText(diagnostic.range)), [
        "upd_param 4", "play 0, 1, 4", "wait {DURATION // 2}", "stop",
      ]);
      assert.deepEqual(diagnostics.map((diagnostic) => diagnostic.severity), [0, 1, 2, 3]);
    } finally {
      collection.dispose();
    }
  });

  it("checks acquisition buffers and pending settings without optional hardware hints", async function () {
    this.timeout(30000);
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    assert.equal(vscode.workspace.getConfiguration("q1lens.q1timeline").get("hardwareReadiness"), false);
    const root = vscode.workspace.workspaceFolders![0].uri.fsPath;
    for (const example of [
      { source: "acquire 0,0,4\n".repeat(9) + "stop\n", profile: "QRM", category: "acquisition_buffer_pressure", severity: vscode.DiagnosticSeverity.Warning },
      { source: "shot: acquire 0,0,120\njmp @shot\n", profile: "QRM", category: "acquisition_buffer_sustained_overload", severity: vscode.DiagnosticSeverity.Error },
      { source: "shot: acquire 0,0,120\njmp @shot\n", profile: undefined, category: "acquisition_buffer_pressure", severity: vscode.DiagnosticSeverity.Warning },
      { source: "wait 100\nset_freq 100\nstop\n", profile: undefined, category: "pending_hardware_state", severity: vscode.DiagnosticSeverity.Warning },
    ]) {
      const project = mkdtempSync(join(root, "default-hardware-checks-"));
      const file = join(project, "sequence.q1asm");
      writeFileSync(file, example.source);
      writeFileSync(join(project, "q1timeline.yml"), "alignment:\n  mode: none\nsequencers:\n  - id: seq0\n    file: sequence.q1asm\n    module: readout_module\n"
        + (example.profile ? `    module_type: ${example.profile}\n` : ""));
      const document = await vscode.workspace.openTextDocument(file);
      await vscode.window.showTextDocument(document);
      await vscode.commands.executeCommand("q1timeline.openPreview", document.uri);
      const deadline = Date.now() + 10000;
      const finding = () => vscode.languages.getDiagnostics(document.uri).find((item) => item.code === example.category);
      while (!finding() || !existsSync(join(project, ".q1timeline", "timeline.html"))) {
        assert.ok(Date.now() < deadline, `Timed out waiting for ${example.category} and its rendered timeline`);
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      assert.equal(finding()!.severity, example.severity);
      if (example.category === "pending_hardware_state") {
        const fixes = await vscode.commands.executeCommand<vscode.CodeAction[]>("vscode.executeCodeActionProvider", document.uri, finding()!.range);
        const fix = fixes?.find((item) => item.title === "Apply pending hardware state before stop");
        assert.ok(fix?.edit, "The concise diagnostic must retain its actionable Quick Fix");
        assert.equal(await vscode.workspace.applyEdit(fix.edit), true);
        assert.equal(await document.save(), true);
        while (finding()) {
          assert.ok(Date.now() < deadline, "Pending warning did not clear after applying its Quick Fix");
          await new Promise((resolve) => setTimeout(resolve, 50));
        }
        assert.match(document.getText(), /upd_param 4\nstop/);
      }
    }
  });
});
