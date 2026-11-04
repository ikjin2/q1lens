import assert from "node:assert/strict";
import { DiagnosticsManager } from "../../src/q1timeline/diagnosticsManager";

class Range {
  constructor(public startLine: number, public startColumn: number, public endLine: number, public endColumn: number) {}
}

class Diagnostic {
  source?: string;
  code?: string;
  constructor(public range: Range, public message: string, public severity: number) {}
}

function fixture() {
  const published: Diagnostic[][] = [];
  const uri = { toString: () => "file:///project/seq.q1asm" };
  const vscode = {
    Uri: { file: () => uri }, Range, Diagnostic,
    workspace: { textDocuments: [] as any[] },
    DiagnosticSeverity: { Error: 0, Warning: 1, Information: 2, Hint: 3 },
  };
  const manager = new DiagnosticsManager(vscode, {
    clear: () => undefined,
    set: (_uri: unknown, diagnostics: Diagnostic[]) => published.push(diagnostics),
  }, (file: string) => file);
  return { manager, published, uri, vscode };
}

describe("q1timeline diagnostic underlines", () => {
  it("underlines only the command and arguments, excluding labels, whitespace, and comments", () => {
    for (const [raw, expected] of [
      ["  upd_param 4                 # First RT command starts at analytical RT time 0.", "upd_param 4"],
      ["body: upd_param 4 // explanation", "upd_param 4"],
      ["\tplay 0, 1, 4#설명", "play 0, 1, 4"],
      ["  wait {DURATION // 2}  // expression argument", "wait {DURATION // 2}"],
      ["  play 0, invalid_argument, 4   # invalid operands still belong to the instruction", "play 0, invalid_argument, 4"],
      ["  stop   ", "stop"],
    ]) {
      const { manager, published } = fixture();
      manager.apply(["error", "warning", "information", "hint"].map((severity) => ({
        severity, category: "timing", message: "Instruction diagnostic",
        source: { file: "/project/seq.q1asm", line: 3, column: 8, raw },
      })));
      for (const diagnostic of published[0]) {
        const range = diagnostic.range;
        assert.deepEqual([range.startLine, range.endLine], [2, 2]);
        assert.equal(raw.slice(range.startColumn, range.endColumn), expected);
      }
    }
  });

  it("uses the open document when source text is unavailable or stale", () => {
    const { manager, published, uri, vscode } = fixture();
    const text = "  upd_param 1000  # current comment";
    vscode.workspace.textDocuments.push({ uri, lineCount: 1, lineAt: () => ({ text }) });
    for (const raw of [undefined, "upd_param 4 # previous text"]) {
      manager.apply([{ severity: "warning", category: "timing", message: "Timing",
        source: { file: "/project/seq.q1asm", line: 1, raw } }]);
      assert.deepEqual(published.at(-1)![0].range, new Range(0, 2, 0, 16));
    }
  });

  it("publishes source validation and lint messages before a program runs", () => {
    const { manager, published } = fixture();
    const cases = [
      ["syntax_error", "error", 0], ["invalid_argument_type", "error", 0],
      ["unused_definition", "hint", 3], ["unreachable_code", "warning", 1],
      ["missing_stop", "warning", 1], ["overwritten_register", "warning", 1],
      ["template_expression_syntax", "warning", 1],
    ] as const;
    manager.apply(cases.map(([category, severity], index) => ({
      category, severity, message: category,
      source: { file: "/project/seq.q1asm", line: index + 1, column: 1 },
    })));
    assert.equal(published[0].length, cases.length);
    for (const [index, diagnostic] of published[0].entries()) {
      assert.equal(diagnostic.code, cases[index][0]);
      assert.equal(diagnostic.severity, cases[index][2]);
      assert.deepEqual(diagnostic.range, new Range(index, 0, index, Number.MAX_SAFE_INTEGER));
    }
  });

  it("highlights the complete instruction line for every severity", () => {
    const { manager, published } = fixture();
    manager.apply(["error", "warning", "information", "hint"].map((severity) => ({
      severity, message: "Instruction diagnostic", code: "timing",
      source: { file: "/project/seq.q1asm", line: 3, column: 8 },
    })));
    assert.equal(published[0].length, 4);
    for (const [severity, diagnostic] of published[0].entries()) {
      assert.deepEqual(diagnostic.range, new Range(2, 0, 2, Number.MAX_SAFE_INTEGER));
      assert.equal(diagnostic.severity, severity);
      assert.equal(diagnostic.source, "q1timeline");
    }
  });

  it("highlights the complete first line for an analyzer failure", () => {
    const { manager, published, uri } = fixture();
    manager.applyFallback(uri, "Invalid analyzer output");
    assert.deepEqual(published[0][0].range, new Range(0, 0, 0, Number.MAX_SAFE_INTEGER));
    assert.equal(published[0][0].code, "invalid_analyzer_json");
  });
});
