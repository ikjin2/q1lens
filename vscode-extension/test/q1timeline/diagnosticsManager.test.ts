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
    DiagnosticSeverity: { Error: 0, Warning: 1, Information: 2, Hint: 3 },
  };
  const manager = new DiagnosticsManager(vscode, {
    clear: () => undefined,
    set: (_uri: unknown, diagnostics: Diagnostic[]) => published.push(diagnostics),
  }, (file: string) => file);
  return { manager, published, uri };
}

describe("q1timeline diagnostic underlines", () => {
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
