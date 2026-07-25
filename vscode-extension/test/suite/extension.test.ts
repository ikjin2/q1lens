import assert from "node:assert/strict";
import { join } from "node:path";
import * as vscode from "vscode";

describe("Q1Lens extension host", function () {
  this.timeout(20000);

  it("registers user-facing commands", async () => {
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const commands = await vscode.commands.getCommands(true);

    assert.ok(commands.includes("qbsTimeline.analyzeAndOpen"));
    assert.ok(commands.includes("qbsTimeline.refresh"));
    assert.ok(commands.includes("qbsTimeline.openIr"));
    assert.ok(commands.includes("qbsTimeline.openRenderedHtml"));
    assert.ok(commands.includes("qbsTimeline.openQ1Timeline"));
    assert.ok(commands.includes("qbsTimeline.openQ1asmFolder"));
    assert.ok(commands.includes("q1timeline.openPreview"));
    assert.ok(commands.includes("q1timeline.refreshPreview"));
  });

  it("analyzes the two-qubit example and publishes no QBST diagnostics", async () => {
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    assert.ok(workspaceRoot);

    const projectUri = vscode.Uri.file(join(workspaceRoot, "examples", "two-qubit-entangling", "qbstimeline.yml"));
    await vscode.window.showTextDocument(projectUri);
    await vscode.commands.executeCommand("qbsTimeline.analyzeAndOpen");

    const outputUri = vscode.Uri.file(join(workspaceRoot, "examples", "two-qubit-entangling", ".qbs_timeline", "qbs_ir.json"));
    await vscode.workspace.fs.stat(outputUri);
    const diagnostics = vscode.languages.getDiagnostics(projectUri).filter((diagnostic) => diagnostic.source === "qbsTimeline");
    assert.equal(
      diagnostics.length,
      0,
      diagnostics.map((diagnostic) => `${diagnostic.code}: ${diagnostic.message}`).join("\n"),
    );
  });
});
