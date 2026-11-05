import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import * as vscode from "vscode";

const invalidInstructions = [
  { name: "missing operand", source: "move 1", category: "invalid_argument_count" },
  { name: "extra operand", source: "move 1,R0,R1", category: "invalid_argument_count" },
  { name: "empty operand", source: "move 1,,R0", category: "syntax_error" },
  { name: "missing commas", source: "move 1 R0", category: "syntax_error" },
  { name: "unknown instruction", source: "moev 1,R0", category: "unknown_instruction" },
  { name: "uppercase instruction", source: "MOVE 1,R0", category: "syntax_error" },
  { name: "immediate destination", source: "move 1,2", category: "invalid_argument_type" },
  { name: "register out of range", source: "move 1,R64", category: "invalid_argument_type" },
  { name: "lowercase register", source: "move 1,r0", category: "invalid_argument_type" },
  { name: "negative duration", source: "wait -1", category: "invalid_argument_value" },
  { name: "duration out of range", source: "wait 65536", category: "invalid_argument_value" },
  { name: "gain out of range", source: "set_awg_gain 32768,0", category: "invalid_argument_value" },
  { name: "integer overflow", source: "move 4294967296,R0", category: "invalid_argument_value" },
  { name: "undefined branch label", source: "jmp @missing", category: "undefined_label" },
  { name: "undefined alias", source: "move $MISSING,R0", category: "unresolved_symbol" },
  { name: "floating point literal", source: "move 1.5,R0", category: "syntax_error" },
  { name: "unclosed placeholder", source: "wait {DURATION", category: "syntax_error" },
  { name: "semicolon separator", source: "move 1,R0; stop", category: "syntax_error" },
  { name: "malformed definition", source: ".DEF COUNT", category: "syntax_error" },
  { name: "malformed label", source: "@bad: nop", category: "syntax_error" },
  { name: "invalid acquisition signature", source: "acquire R0,0,4", category: "invalid_argument_type" },
  { name: "mixed play operands", source: "play R0,1,4", category: "invalid_argument_type" },
  { name: "invalid condition flag", source: "set_cond 2,1,0,4", category: "invalid_argument_value" },
];

async function waitFor(description: string, check: () => boolean): Promise<void> {
  const deadline = Date.now() + 10000;
  while (!check()) {
    assert.ok(Date.now() < deadline, `Timed out waiting for ${description}`);
    await new Promise((resolve) => setTimeout(resolve, 30));
  }
}

function errors(document: vscode.TextDocument): vscode.Diagnostic[] {
  return vscode.languages.getDiagnostics(document.uri).filter((item) => item.source === "q1timeline"
    && item.severity === vscode.DiagnosticSeverity.Error);
}

async function replaceSource(document: vscode.TextDocument, source: string, save = true): Promise<void> {
  const edit = new vscode.WorkspaceEdit();
  edit.replace(document.uri, new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)), source);
  assert.equal(await vscode.workspace.applyEdit(edit), true);
  if (save) assert.equal(await document.save(), true);
}

async function createProject(options: { source?: string; mode?: string; name?: string; fallback?: boolean; external?: boolean } = {}) {
  const root = options.external ? process.env.Q1LENS_EXTENSION_HOST_OUTPUT! : vscode.workspace.workspaceFolders![0].uri.fsPath;
  mkdirSync(root, { recursive: true });
  const project = mkdtempSync(join(root, "diagnostic-coverage-"));
  const file = join(project, options.name ?? "sequence.q1asm");
  writeFileSync(file, options.source ?? "stop\n");
  const projectFile = join(project, "q1timeline.yml");
  if (!options.fallback) {
    writeFileSync(projectFile, `analysis:\n  execution_mode: ${options.mode ?? "analysis"}\nsequencers:\n  - id: seq0\n    file: ${JSON.stringify(options.name ?? "sequence.q1asm")}\n`);
  }
  const document = await vscode.workspace.openTextDocument(file);
  await vscode.window.showTextDocument(document);
  return { document, project, projectFile, output: join(project, ".q1timeline") };
}

async function openPreview(document: vscode.TextDocument): Promise<void> {
  await vscode.commands.executeCommand("q1timeline.openPreview", document.uri);
}

async function expectDiagnostic(document: vscode.TextDocument, category: string, line: number): Promise<vscode.Diagnostic> {
  await waitFor(`${category} at ${document.uri.fsPath}:${line + 1}; current diagnostics: ${JSON.stringify(errors(document))}`,
    () => errors(document).some((item) => item.code === category && item.range.start.line === line));
  const matches = errors(document).filter((item) => item.code === category && item.range.start.line === line);
  assert.equal(matches.length, 1, "The same source error must not appear twice");
  assert.ok(document.getText(matches[0].range).trim().length > 0, "The error must underline source text");
  return matches[0];
}

async function expectClean(document: vscode.TextDocument, output: string): Promise<void> {
  await waitFor("a regenerated timeline with no error diagnostics", () => existsSync(join(output, "timeline.html"))
    && errors(document).length === 0);
}

describe("Q1ASM editor diagnostic coverage", function () {
  this.timeout(30000);
  let previousUpdateMode: string | undefined;
  const config = () => vscode.workspace.getConfiguration("q1lens.q1timeline");

  before(async () => {
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    previousUpdateMode = config().inspect<string>("updateMode")?.workspaceValue;
    await config().update("updateMode", "onSave", vscode.ConfigurationTarget.Workspace);
  });

  after(async () => {
    await config().update("updateMode", previousUpdateMode, vscode.ConfigurationTarget.Workspace);
  });

  for (const placement of ["reachable", "after stop", "skipped by jump"]) {
    for (const item of invalidInstructions) {
      it(`reports ${item.name} ${placement} on save and clears it after repair`, async () => {
        const { document, output } = await createProject();
        await openPreview(document);
        await expectClean(document, output);
        const prefix = placement === "reachable" ? "" : placement === "after stop" ? "stop\n" : "jmp @done\n";
        const suffix = placement === "skipped by jump" ? "\ndone: stop\n" : "\nstop\n";
        await replaceSource(document, prefix + item.source + suffix);
        const diagnostic = await expectDiagnostic(document, item.category, prefix ? 1 : 0);
        assert.equal(document.getText(diagnostic.range), item.source);
        await replaceSource(document, "stop\n");
        await expectClean(document, output);
      });
    }
  }

  for (const mode of ["analysis", "concrete", "simulation"]) {
    it(`reports invalid source on first preview in ${mode} mode`, async () => {
      const { document } = await createProject({ source: "stop\nmove 1\n", mode });
      await openPreview(document);
      await expectDiagnostic(document, "invalid_argument_count", 1);
    });
  }

  for (const item of [
    { name: "duplicate label", source: "x: nop\nx: stop\n", category: "syntax_error", line: 1 },
    { name: "duplicate definition", source: ".DEF VALUE 1\n.DEF VALUE 2\nstop\n", category: "syntax_error", line: 1 },
    { name: "alias before its definition", source: "stop\nmove $VALUE,R0\n.DEF VALUE 1\n", category: "syntax_error", line: 1 },
    { name: "definition after a label", source: "stop\nbad: .DEF VALUE 1\n", category: "syntax_error", line: 1 },
    { name: "label without a target instruction", source: "jmp @end\nend:\n", category: "invalid_argument_value", line: 0 },
    { name: "undefined label used as a value", source: "stop\nmove @missing,R0\n", category: "undefined_label", line: 1 },
  ]) {
    it(`reports ${item.name} on first preview`, async () => {
      const { document } = await createProject({ source: item.source });
      await openPreview(document);
      await expectDiagnostic(document, item.category, item.line);
    });
  }

  for (const source of [
    "move 1,R0,\nstop\n",
    "stop 0\n",
    "stop 1\n",
    ".DEF REG R0\nstop\nmove 1,$REG\n",
    "stop\nwait {duration}\n",
    "stop\nwait {COUNT // 2}\n",
    "// full-line comment\nstop # trailing comment\n",
    "move 0xffffffff,R63\nstop\n",
  ]) {
    it(`accepts valid source without false errors: ${JSON.stringify(source)}`, async () => {
      const { document, output } = await createProject({ source });
      await openPreview(document);
      await expectClean(document, output);
    });
  }

  for (const options of [
    { name: "실험 sequence #100%.q1asm" },
    { name: "sequence.Q1ASM" },
    { external: true },
    { fallback: true },
  ]) {
    it(`reports errors with ${JSON.stringify(options)}`, async () => {
      const { document } = await createProject({ ...options, source: "stop\nmove 1\n" });
      await openPreview(document);
      await expectDiagnostic(document, "invalid_argument_count", 1);
    });
  }

  it("reports multiple independent errors with exact lines in a BOM/CRLF file", async () => {
    const { document } = await createProject({ source: "\uFEFFstop\r\nmove 1\r\nwait -1\r\nmoev 1,R0\r\n" });
    await openPreview(document);
    await expectDiagnostic(document, "invalid_argument_count", 1);
    await expectDiagnostic(document, "invalid_argument_value", 2);
    await expectDiagnostic(document, "unknown_instruction", 3);
    assert.equal(errors(document).length, 3);
  });

  it("reports errors in an editor opened through a directory symlink", async () => {
    const { project, output } = await createProject({ source: "stop\nmove 1\n" });
    const alias = `${project}-link`;
    symlinkSync(project, alias, process.platform === "win32" ? "junction" : "dir");
    const document = await vscode.workspace.openTextDocument(join(alias, "sequence.q1asm"));
    await vscode.window.showTextDocument(document);
    await openPreview(document);
    await expectDiagnostic(document, "invalid_argument_count", 1);
    await replaceSource(document, "stop\n");
    await expectClean(document, output);
  });

  it("reports invalid source in the untaken side of a conditional branch", async () => {
    const { document } = await createProject({ source: "move 0,R0\nnop\ncmp R0,1\nnop\njz @bad\nstop\nbad: move 1\n", mode: "concrete" });
    await openPreview(document);
    const diagnostic = await expectDiagnostic(document, "invalid_argument_count", 6);
    assert.equal(document.getText(diagnostic.range), "move 1");
  });

  it("keeps errors for both sequencers, including a file that is not open", async () => {
    const { document, project, projectFile } = await createProject({ source: "stop\nmove 1\n" });
    const other = vscode.Uri.file(join(project, "other.q1asm"));
    writeFileSync(other.fsPath, "stop\nwait -1\n");
    writeFileSync(projectFile, "sequencers:\n  - id: first\n    file: sequence.q1asm\n  - id: second\n    file: other.q1asm\n");
    await openPreview(document);
    await expectDiagnostic(document, "invalid_argument_count", 1);
    await waitFor("the unopened sequencer's error", () => vscode.languages.getDiagnostics(other)
      .some((item) => item.code === "invalid_argument_value"));
    const otherDocument = await vscode.workspace.openTextDocument(other);
    await replaceSource(document, "stop\n");
    await replaceSource(otherDocument, "stop\nmove 1\n");
    await expectDiagnostic(otherDocument, "invalid_argument_count", 1);
    await waitFor("the first file's repaired error to clear", () => errors(document).length === 0);
  });

  it("uses the newest saved source after rapid invalid, valid, and invalid saves", async () => {
    const { document, output } = await createProject();
    await openPreview(document);
    await replaceSource(document, "move 1\nstop\n");
    await replaceSource(document, "stop\n");
    await replaceSource(document, "stop\nwait -1\n");
    await expectDiagnostic(document, "invalid_argument_value", 1);
    assert.ok(!errors(document).some((item) => item.code === "invalid_argument_count"));
    await replaceSource(document, "stop\n");
    await expectClean(document, output);
  });

  it("retains a template syntax warning rather than treating an opaque parameter as an error", async () => {
    const { document } = await createProject({ source: "stop\nwait {4 + }\n" });
    await openPreview(document);
    await waitFor("the template syntax warning", () => vscode.languages.getDiagnostics(document.uri)
      .some((item) => item.code === "template_expression_syntax"));
    const diagnostic = vscode.languages.getDiagnostics(document.uri).find((item) => item.code === "template_expression_syntax");
    assert.ok(diagnostic);
    assert.equal(diagnostic.severity, vscode.DiagnosticSeverity.Warning);
    assert.equal(errors(document).length, 0);
  });

  it("does not diagnose a saved file until it becomes an analysis target", async () => {
    const { document } = await createProject();
    await replaceSource(document, "move 1\nstop\n");
    await new Promise((resolve) => setTimeout(resolve, 500));
    assert.equal(errors(document).length, 0);
    await openPreview(document);
    await expectDiagnostic(document, "invalid_argument_count", 0);
  });

  it("does not diagnose an unlisted file even after opening its preview until it is added to the project", async () => {
    const { document, project, projectFile, output } = await createProject();
    const otherFile = join(project, "unlisted.q1asm");
    writeFileSync(otherFile, "stop\n");
    await openPreview(document);
    await expectClean(document, output);
    const other = await vscode.workspace.openTextDocument(otherFile);
    await vscode.window.showTextDocument(other);
    await replaceSource(other, "move 1\nstop\n");
    await new Promise((resolve) => setTimeout(resolve, 500));
    assert.equal(errors(other).length, 0);
    await openPreview(other);
    assert.equal(errors(other).length, 0, "Opening a source in the same folder still analyzes the existing project YAML");
    const projectDocument = await vscode.workspace.openTextDocument(projectFile);
    await replaceSource(projectDocument, "sequencers:\n  - id: seq0\n    file: sequence.q1asm\n  - id: other\n    file: unlisted.q1asm\n");
    await expectDiagnostic(other, "invalid_argument_count", 0);
  });

  it("diagnoses saves only for the most recently selected project", async () => {
    const first = await createProject();
    await openPreview(first.document);
    await expectClean(first.document, first.output);
    const second = await createProject();
    await openPreview(second.document);
    await expectClean(second.document, second.output);
    await replaceSource(first.document, "move 1\nstop\n");
    await new Promise((resolve) => setTimeout(resolve, 500));
    assert.equal(errors(first.document).length, 0);
    await openPreview(first.document);
    await expectDiagnostic(first.document, "invalid_argument_count", 0);
  });

  it("requires explicit refresh after saving when update mode is manual", async () => {
    await config().update("updateMode", "manual", vscode.ConfigurationTarget.Workspace);
    try {
      const { document, output } = await createProject();
      await openPreview(document);
      await expectClean(document, output);
      await replaceSource(document, "move 1\nstop\n");
      await new Promise((resolve) => setTimeout(resolve, 500));
      assert.equal(errors(document).length, 0);
      await vscode.commands.executeCommand("q1timeline.refreshPreview");
      await expectDiagnostic(document, "invalid_argument_count", 0);
    } finally {
      await config().update("updateMode", "onSave", vscode.ConfigurationTarget.Workspace);
    }
  });

  it("analyzes saved bytes rather than unsaved edits even in onType mode", async () => {
    await config().update("updateMode", "onType", vscode.ConfigurationTarget.Workspace);
    try {
      const { document, output } = await createProject();
      await openPreview(document);
      await expectClean(document, output);
      const html = join(output, "timeline.html");
      const beforeEdit = statSync(html).mtimeMs;
      await replaceSource(document, "move 1\nstop\n", false);
      await waitFor("on-type analysis to regenerate a timeline from the saved file", () => existsSync(html) && statSync(html).mtimeMs > beforeEdit);
      assert.equal(errors(document).length, 0);
      assert.equal(readFileSync(document.uri.fsPath, "utf8"), "stop\n");
      assert.equal(await document.save(), true);
      await expectDiagnostic(document, "invalid_argument_count", 0);
    } finally {
      await config().update("updateMode", "onSave", vscode.ConfigurationTarget.Workspace);
    }
  });
});
