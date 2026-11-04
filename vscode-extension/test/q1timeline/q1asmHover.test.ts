import assert from "node:assert/strict";
import type * as vscodeTypes from "vscode";
import { Q1asmHoverProvider } from "../../src/q1timeline/q1asmHover";

const { families, argumentStyles } = require("../../src/shared/timeline/instructionColors.js");

class MarkdownString {
  supportHtml = false;
  constructor(public value: string) {}
}

class Range {
  constructor(public line: number, public start: number, public endLine: number, public end: number) {}
}

function hover(text: string, character: number, kind = 2) {
  const api = {
    MarkdownString, Range,
    Hover: class {
      contents: MarkdownString[];
      constructor(content: MarkdownString, public range: Range) { this.contents = [content]; }
    },
    ColorThemeKind: { Light: 1, Dark: 2, HighContrast: 3, HighContrastLight: 4 },
    window: { activeColorTheme: { kind } },
  } as unknown as typeof vscodeTypes;
  return new Q1asmHoverProvider(api).provideHover(
    { lineAt: () => ({ text }) } as unknown as vscodeTypes.TextDocument,
    { line: 0, character } as vscodeTypes.Position,
  );
}

function content(result: vscodeTypes.Hover | undefined): vscodeTypes.MarkdownString {
  assert.ok(result);
  return result.contents[0] as vscodeTypes.MarkdownString;
}

describe("Q1ASM hover colors and operand types", () => {
  it("uses the editor palette for every command in light, dark and high contrast themes", () => {
    for (const kind of [1, 2, 3, 4]) {
      for (const family of Object.values(families) as Array<{ ops: string[]; color: string; lightColor: string }>) {
        for (const op of family.ops) {
          const markdown = content(hover(op, 0, kind));
          const color = kind === 1 || kind === 4 ? family.lightColor : family.color;
          assert.ok(markdown.value.includes(`<span style="color:${color};">${op}</span>`), `${op}, theme ${kind}`);
          assert.equal(markdown.supportHtml, true);
        }
      }
    }
  });

  it("reports actual operand kinds independently of the expected instruction signature", () => {
    for (const [operand, type, style] of [
      ["-0xFF", "immediate", "number"],
      ["R0", "register", "register"],
      ["r3", "register", "register"],
      ["R0xA", "register", "register"],
      ["@done", "label (address)", "label"],
      ["$GAIN", "alias", "symbol"],
      ["{N * 4}", "parameter", "placeholder"],
    ]) {
      for (const op of ["move", "play"]) {
        const text = `${op} ${operand}, R1`;
        const start = op.length + 1;
        const result = hover(text, start + 1);
        assert.equal(content(result).value, `<code><span style="color:${argumentStyles[style].color};">${operand}</span> · ${type}</code>`);
        assert.deepEqual(result?.range, new Range(0, start, 0, start + operand.length));
      }
    }
  });

  it("escapes parameter expressions as text in HTML hovers", () => {
    const markdown = content(hover("wait {N < 4 && M > 1}", 7));
    assert.ok(markdown.value.includes("{N &lt; 4 &amp;&amp; M &gt; 1}"));
    assert.ok(!markdown.value.includes("{N <"));
  });

  it("keeps comments, separators and unrecognized tokens out of the hover", () => {
    const text = "start: move R0, 4 # R1";
    for (const column of [5, 6, 11, 14, 15, 17, 19, 20]) {
      assert.equal(hover(text, column), undefined, `column ${column}`);
    }
    for (const source of ["# R0", "// play R0", "move R01", "constructor R0", "unknown R0"]) {
      assert.equal(hover(source, source.indexOf("R0")), undefined, source);
    }
  });
});
