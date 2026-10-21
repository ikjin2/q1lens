import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import {
  DIAGNOSTIC_SUGGESTED_EDIT_PROPERTY,
  Q1asmQuickFixProvider,
  registerQ1asmQuickFixes,
} from "../../src/q1timeline/q1asmQuickFix";
import { DiagnosticsManager } from "../../src/q1timeline/diagnosticsManager";

class Position {
  constructor(public line: number, public character: number) {}
}

class Range {
  start: Position;
  end: Position;

  constructor(startLine: number, startColumn: number, endLine: number, endColumn: number) {
    this.start = new Position(startLine, startColumn);
    this.end = new Position(endLine, endColumn);
  }
}

class Diagnostic {
  source?: string;
  code?: string;
  relatedInformation?: unknown[];

  constructor(public range: Range, public message: string, public severity: number) {}
}

class CodeAction {
  edit?: WorkspaceEdit;
  diagnostics?: Diagnostic[];

  constructor(public title: string, public kind: string) {}
}

class WorkspaceEdit {
  replacements: Array<{ uri: any; range: Range; newText: string }> = [];

  replace(uri: any, range: Range, newText: string): void {
    this.replacements.push({ uri, range, newText });
  }
}

function uri(file: string): any {
  return {
    scheme: "file",
    fsPath: file,
    toString: () => `file://${file}`,
  };
}

function mockVscode(): any {
  return {
    Uri: { file: uri },
    Range,
    Position,
    Diagnostic,
    DiagnosticSeverity: { Error: 0, Warning: 1, Information: 2, Hint: 3 },
    CodeAction,
    CodeActionKind: { QuickFix: "quickfix" },
    WorkspaceEdit,
    Location: class {
      constructor(public uri: any, public range: Range) {}
    },
    DiagnosticRelatedInformation: class {
      constructor(public location: any, public message: string) {}
    },
  };
}

function suggestedDiagnostic(sourceFile: string): any {
  return {
    severity: "warning",
    category: "future_safe_fix",
    message: "A future analyzer can offer a safe edit.",
    source: { file: sourceFile, line: 1, column: 1 },
    details: {
      suggested_edit: {
        title: "Apply analyzer suggestion",
        file: sourceFile,
        range: {
          start: { line: 1, column: 1 },
          end: { line: 1, column: 1 },
        },
        new_text: "upd_param 4\n",
      },
    },
  };
}

function documentFor(file: string, text = "wait 4"): any {
  const lines = text.split("\n");
  return {
    languageId: "q1asm",
    uri: uri(file),
    lineCount: lines.length,
    lineAt: (line: number) => ({ text: lines[line] }),
  };
}

describe("Q1ASM diagnostic quick fixes", () => {
  it("withdraws attached and transported suggestions after another source line changes", () => {
    const vscode = mockVscode();
    const file = join(tmpdir(), "q1lens-quick-fix", "seq0.q1asm");
    const before = "set_freq 100\nwait 4\nstop\n";
    const after = "set_freq 100\nupd_param 4\nstop\n";
    const repoRoot = resolve(__dirname, "../../../..");
    const analyze = (source: string) => JSON.parse(execFileSync("python", ["-c",
      "import json,sys; from q1timeline.analysis.api import analyze_text; print(json.dumps(analyze_text(sys.argv[1],file=sys.argv[2],hardware_readiness=True)))",
      source, file,
    ], { cwd: repoRoot, env: { ...process.env, PYTHONPATH: join(repoRoot, "src") }, encoding: "utf8", windowsHide: true }));
    const original = analyze(before);
    const corrected = analyze(after);
    const pending = original.diagnostics.find((item: any) => item.category === "pending_hardware_state");
    assert.ok(pending);
    assert.ok(!corrected.diagnostics.some((item: any) => item.category === "pending_hardware_state"));
    const published: Diagnostic[][] = [];
    const manager = new DiagnosticsManager(vscode,
      { clear() {}, set: (_target: any, items: Diagnostic[]) => published.push(items) },
      (sourceFile: string) => sourceFile);
    manager.apply([pending]);
    const diagnostic = published[0][0];
    const transported = new Diagnostic(diagnostic.range, diagnostic.message, diagnostic.severity);
    transported.source = diagnostic.source;
    transported.code = diagnostic.code;
    const provider = new Q1asmQuickFixProvider(vscode);
    const range = new Range(2, 0, 2, 4) as any;
    assert.equal(provider.provideCodeActions(documentFor(file, before), range, { diagnostics: [transported] } as any).length, 1);
    const Module = require("node:module");
    const originalLoad = Module._load;
    const controllerPath = require.resolve("../../src/q1timeline/controller");
    const cached = require.cache[controllerPath];
    Module._load = function (request: string, parent: unknown, isMain: boolean) {
      return request === "vscode" ? vscode : originalLoad.call(this, request, parent, isMain);
    };
    delete require.cache[controllerPath];
    try {
      const { TimelineController } = require("../../src/q1timeline/controller");
      TimelineController.prototype.invalidateSourceAnalysis.call({
        resetPlayback() {}, sourceRevision: 0, cancelSourceRangeZoom() {}, diagnosticsManager: manager,
      });
      for (const stale of [diagnostic, transported]) {
        assert.deepEqual(provider.provideCodeActions({ ...documentFor(file, after), isDirty: false }, range,
          { diagnostics: [stale] } as any), [], "A saved fix on another line must invalidate the old insertion");
      }
    } finally {
      Module._load = originalLoad;
      if (cached) require.cache[controllerPath] = cached;
      else delete require.cache[controllerPath];
    }
  });

  it("rejects stale suggestions after the diagnosed line changes or the document is dirty", () => {
    const vscode = mockVscode();
    const file = join(tmpdir(), "q1lens-quick-fix", "seq0.q1asm");
    const published: Diagnostic[][] = [];
    const manager = new DiagnosticsManager(vscode,
      { clear: () => undefined, set: (_target: any, items: Diagnostic[]) => published.push(items) },
      (sourceFile: string) => sourceFile);
    const input = suggestedDiagnostic(file);
    input.source.raw = "stop";
    manager.apply([input]);
    const provider = new Q1asmQuickFixProvider(vscode);
    const context = { diagnostics: published[0] } as any;
    const range = new Range(0, 0, 0, 4) as any;
    assert.equal(provider.provideCodeActions(documentFor(file, "stop"), range, context).length, 1);
    assert.deepEqual(provider.provideCodeActions(documentFor(file, "wait 4"), range, context), []);
    const dirty = { ...documentFor(file, "stop"), isDirty: true };
    assert.deepEqual(provider.provideCodeActions(dirty, range, context), []);
    const original = published[0][0];
    const transported = new Diagnostic(original.range, original.message, original.severity);
    transported.source = original.source;
    transported.code = original.code;
    assert.deepEqual(provider.provideCodeActions(documentFor(file, "wait 4"), range, { diagnostics: [transported] } as any), []);
  });

  it("underlines the whole instruction line for every diagnostic severity", () => {
    const vscode = mockVscode();
    const file = join(tmpdir(), "q1lens-quick-fix", "seq0.q1asm");
    const published: Diagnostic[][] = [];
    const manager = new DiagnosticsManager(
      vscode,
      { clear: () => undefined, set: (_target: any, diagnostics: Diagnostic[]) => published.push(diagnostics) },
      (sourceFile: string) => sourceFile,
    );
    const severities = ["error", "warning", "information", "hint"];

    manager.apply(severities.map((severity) => ({
      ...suggestedDiagnostic(file),
      severity,
      source: { file, line: 3, column: 8 },
    })));

    assert.equal(published[0].length, severities.length);
    for (const [index, diagnostic] of published[0].entries()) {
      assert.deepEqual(diagnostic.range, new Range(2, 0, 2, Number.MAX_SAFE_INTEGER));
      assert.equal(diagnostic.severity, index);
      assert.equal(diagnostic.source, "q1timeline");
    }
  });

  it("underlines the whole first line for analyzer failure diagnostics", () => {
    const vscode = mockVscode();
    const file = join(tmpdir(), "q1lens-quick-fix", "q1timeline.yml");
    const published: Diagnostic[][] = [];
    const manager = new DiagnosticsManager(
      vscode,
      { clear: () => undefined, set: (_target: any, diagnostics: Diagnostic[]) => published.push(diagnostics) },
      (sourceFile: string) => sourceFile,
    );

    manager.applyFallback(uri(file), "Invalid analyzer output");

    assert.deepEqual(published[0][0].range, new Range(0, 0, 0, Number.MAX_SAFE_INTEGER));
    assert.equal(published[0][0].code, "invalid_analyzer_json");
  });

  it("does not accept a wider edit just because the underline covers the whole line", () => {
    const vscode = mockVscode();
    const file = join(tmpdir(), "q1lens-quick-fix", "seq0.q1asm");
    const published: Diagnostic[][] = [];
    const manager = new DiagnosticsManager(
      vscode,
      { clear: () => undefined, set: (_target: any, diagnostics: Diagnostic[]) => published.push(diagnostics) },
      (sourceFile: string) => sourceFile,
    );
    const input = suggestedDiagnostic(file);
    input.details.suggested_edit.range.end.column = 5;

    manager.apply([input]);

    const diagnostic = published[0][0] as any;
    assert.deepEqual(diagnostic.range, new Range(0, 0, 0, Number.MAX_SAFE_INTEGER));
    assert.equal(diagnostic[DIAGNOSTIC_SUGGESTED_EDIT_PROPERTY], undefined);
    assert.deepEqual(new Q1asmQuickFixProvider(vscode).provideCodeActions(
      documentFor(file),
      new Range(0, 0, 0, 4) as any,
      { diagnostics: [diagnostic] } as any,
    ), []);
  });

  it("preserves a validated analyzer edit on the published VS Code diagnostic", () => {
    const vscode = mockVscode();
    const file = join(tmpdir(), "q1lens-quick-fix", "seq0.q1asm");
    const published = new Map<string, Diagnostic[]>();
    const collection = {
      clear: () => published.clear(),
      set: (target: any, diagnostics: Diagnostic[]) => published.set(target.toString(), diagnostics),
    };
    const manager = new DiagnosticsManager(vscode, collection, (sourceFile: string) => sourceFile);

    manager.apply([suggestedDiagnostic(file)]);

    const diagnostic = published.get(uri(file).toString())?.[0] as any;
    assert.ok(diagnostic);
    assert.deepEqual(diagnostic.range, new Range(0, 0, 0, Number.MAX_SAFE_INTEGER));
    assert.equal(diagnostic[DIAGNOSTIC_SUGGESTED_EDIT_PROPERTY].file, file);
    assert.deepEqual(diagnostic[DIAGNOSTIC_SUGGESTED_EDIT_PROPERTY].range, {
      start: { line: 0, column: 0 },
      end: { line: 0, column: 0 },
    });
  });

  it("offers a quick fix that edits only the diagnosed Q1ASM range", () => {
    const vscode = mockVscode();
    const file = join(tmpdir(), "q1lens-quick-fix", "seq0.q1asm");
    const published: Diagnostic[][] = [];
    const manager = new DiagnosticsManager(
      vscode,
      { clear: () => undefined, set: (_target: any, diagnostics: Diagnostic[]) => published.push(diagnostics) },
      (sourceFile: string) => sourceFile,
    );
    manager.apply([suggestedDiagnostic(file)]);
    const diagnostic = published[0][0];
    const clonedDiagnostic = new Diagnostic(diagnostic.range, diagnostic.message, diagnostic.severity);
    clonedDiagnostic.source = diagnostic.source;
    clonedDiagnostic.code = diagnostic.code;
    const provider = new Q1asmQuickFixProvider(vscode);

    const actions = provider.provideCodeActions(
      documentFor(file),
      new Range(0, 0, 0, 1) as any,
      { diagnostics: [clonedDiagnostic] } as any,
    ) as any[];

    assert.equal(actions.length, 1);
    assert.equal(actions[0].title, "Apply analyzer suggestion");
    assert.equal(actions[0].kind, "quickfix");
    assert.deepEqual(actions[0].diagnostics, [clonedDiagnostic]);
    assert.equal(actions[0].edit.replacements.length, 1);
    assert.equal(actions[0].edit.replacements[0].uri.fsPath, file);
    assert.deepEqual(actions[0].edit.replacements[0].range, new Range(0, 0, 0, 0));
    assert.equal(actions[0].edit.replacements[0].newText, "upd_param 4\n");
  });

  it("offers the hardware-readiness insertion at the diagnosed stop", () => {
    const vscode = mockVscode();
    const file = join(tmpdir(), "q1lens-quick-fix", "seq0.q1asm");
    const published: Diagnostic[][] = [];
    const manager = new DiagnosticsManager(
      vscode,
      { clear: () => undefined, set: (_target: any, diagnostics: Diagnostic[]) => published.push(diagnostics) },
      (sourceFile: string) => sourceFile,
    );
    manager.apply([{
      severity: "warning",
      category: "pending_hardware_state",
      message: "Program ends before applying pending hardware state: frequency.",
      source: { file, line: 2, column: 1 },
      details: {
        suggested_edit: {
          title: "Apply pending hardware state before stop",
          file,
          range: {
            start: { line: 2, column: 1 },
            end: { line: 2, column: 1 },
          },
          new_text: "upd_param 4\n",
        },
      },
    }]);
    const provider = new Q1asmQuickFixProvider(vscode);

    assert.deepEqual(published[0][0].range, new Range(1, 0, 1, Number.MAX_SAFE_INTEGER));

    const actions = provider.provideCodeActions(
      documentFor(file, "set_freq 100\nstop"),
      new Range(1, 3, 1, 4) as any,
      { diagnostics: [published[0][0]] } as any,
    ) as any[];

    assert.equal(actions.length, 1);
    assert.deepEqual(actions[0].edit.replacements[0].range, new Range(1, 0, 1, 0));
    assert.equal(actions[0].edit.replacements[0].newText, "upd_param 4\n");
  });

  it("revalidates diagnostic metadata before creating an edit", () => {
    const vscode = mockVscode();
    const file = join(tmpdir(), "q1lens-quick-fix", "seq0.q1asm");
    const otherFile = join(tmpdir(), "q1lens-quick-fix", "seq1.q1asm");
    const provider = new Q1asmQuickFixProvider(vscode);
    const baseDiagnostic: any = new Diagnostic(new Range(0, 0, 0, 1), "warning", 1);
    baseDiagnostic.source = "q1timeline";
    const baseEdit = {
      title: "Apply analyzer suggestion",
      file,
      range: { start: { line: 0, column: 0 }, end: { line: 0, column: 0 } },
      newText: "upd_param 4\n",
    };
    const candidates = [
      { ...baseEdit, file: otherFile },
      { ...baseEdit, range: { start: { line: 0, column: 2 }, end: { line: 0, column: 2 } } },
      { ...baseEdit, range: { start: { line: 3, column: 0 }, end: { line: 3, column: 0 } } },
      { ...baseEdit, file: 4 },
      { ...baseEdit, newText: 4 },
    ];

    for (const edit of candidates) {
      baseDiagnostic[DIAGNOSTIC_SUGGESTED_EDIT_PROPERTY] = edit;
      const actions = provider.provideCodeActions(
        documentFor(file),
        new Range(0, 0, 0, 1) as any,
        { diagnostics: [baseDiagnostic] } as any,
      );
      assert.deepEqual(actions, []);
    }
  });

  it("does not offer analyzer edits for non-Q1ASM or non-file documents", () => {
    const vscode = mockVscode();
    const file = join(tmpdir(), "q1lens-quick-fix", "seq0.q1asm");
    const diagnostic: any = new Diagnostic(new Range(0, 0, 0, 1), "warning", 1);
    diagnostic.source = "q1timeline";
    diagnostic[DIAGNOSTIC_SUGGESTED_EDIT_PROPERTY] = {
      title: "Apply analyzer suggestion",
      file,
      range: { start: { line: 0, column: 0 }, end: { line: 0, column: 0 } },
      newText: "upd_param 4\n",
    };
    const provider = new Q1asmQuickFixProvider(vscode);

    assert.deepEqual(
      provider.provideCodeActions(
        { ...documentFor(file), languageId: "plaintext" },
        new Range(0, 0, 0, 1) as any,
        { diagnostics: [diagnostic] } as any,
      ),
      [],
    );
    assert.deepEqual(
      provider.provideCodeActions(
        { ...documentFor(file), uri: { ...uri(file), scheme: "untitled" } },
        new Range(0, 0, 0, 1) as any,
        { diagnostics: [diagnostic] } as any,
      ),
      [],
    );
  });

  it("registers only file-backed Q1ASM quick fixes", () => {
    const vscode = mockVscode();
    const registrations: any[] = [];
    const disposable = { dispose: () => undefined };
    vscode.languages = {
      registerCodeActionsProvider: (selector: any, provider: any, metadata: any) => {
        registrations.push({ selector, provider, metadata });
        return disposable;
      },
    };
    const context = { subscriptions: [] as any[] };

    registerQ1asmQuickFixes(context as any, vscode);

    assert.equal(context.subscriptions[0], disposable);
    assert.deepEqual(registrations[0].selector, { language: "q1asm", scheme: "file" });
    assert.deepEqual(registrations[0].metadata, { providedCodeActionKinds: ["quickfix"] });
    assert.ok(registrations[0].provider instanceof Q1asmQuickFixProvider);
  });
});
