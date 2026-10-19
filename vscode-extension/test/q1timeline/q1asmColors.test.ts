import assert from "node:assert/strict";
import { registerQ1asmColors } from "../../src/q1timeline/q1asmColors";

const { families, argumentStyles } = require("../../src/shared/timeline/instructionColors.js");
const styles = { ...families, ...argumentStyles };

class Range {
  constructor(public startLine: number, public startColumn: number, public endLine: number, public endColumn: number) {}
}

function document(text: string, languageId = "q1asm") {
  return {
    languageId,
    lines: text.split("\n"),
    get lineCount() { return this.lines.length; },
    lineAt(line: number) { return { text: this.lines[line] }; },
  };
}

function editor(source: ReturnType<typeof document>) {
  return {
    document: source,
    applied: new Map<string, Range[]>(),
    setDecorations(type: any, ranges: Range[]) { this.applied.set(type.options.color, ranges); },
  };
}

function setup(editors: ReturnType<typeof editor>[]) {
  let visibleChanged: (items: any[]) => void;
  let documentChanged: (event: any) => void;
  const types: any[] = [];
  const subscriptions: any[] = [];
  const vscode = {
    Range,
    DecorationRangeBehavior: { ClosedClosed: 1 },
    window: {
      visibleTextEditors: editors,
      createTextEditorDecorationType(options: any) {
        const type = { options, disposed: false, dispose() { this.disposed = true; } };
        types.push(type);
        return type;
      },
      onDidChangeVisibleTextEditors(listener: typeof visibleChanged) {
        visibleChanged = listener;
        return { dispose: () => undefined };
      },
    },
    workspace: {
      onDidChangeTextDocument(listener: typeof documentChanged) {
        documentChanged = listener;
        return { dispose: () => undefined };
      },
    },
  };
  registerQ1asmColors({ subscriptions } as any, vscode as any);
  return {
    vscode, types, subscriptions,
    show(items: typeof editors) { vscode.window.visibleTextEditors = items; visibleChanged(items); },
    change(source: ReturnType<typeof document>) { documentChanged({ document: source }); },
  };
}

function ranges(target: ReturnType<typeof editor>, family: string) {
  return target.applied.get(styles[family].color);
}

function tokenText(target: ReturnType<typeof editor>, family: string) {
  return ranges(target, family)?.map((range) => target.document.lines[range.startLine].slice(range.startColumn, range.endColumn));
}

describe("Q1ASM instruction colors", () => {
  it("keeps opcode categories separate from labels, arguments and comments", () => {
    const target = editor(document([
      "move: PLAY 0, 1, 4 # set_freq 1",
      "\tSET_FREQ 1",
      "  move play, R1 // acquire 0, 1, 4",
      "again: jmp @again",
      "wait_sync 4",
      "fb_com_data 0, 1, 4",
      "upd_param 4",
      "reset_netzero",
      "set_latch_en 1, 4",
      "latch_rst 4",
      "# play 0, 1, 4",
      "// set_freq 1",
      "play:",
      ".word play",
      "unknown set_freq 4",
      "entry : play 0, 1, 4",
      "play : wait_sync 4",
      "play :",
    ].join("\n")));
    setup([target]);

    assert.deepEqual(ranges(target, "q1"), [new Range(2, 2, 2, 6)]);
    assert.deepEqual(ranges(target, "flow"), [new Range(3, 7, 3, 10)]);
    assert.deepEqual(ranges(target, "latched"), [new Range(1, 1, 1, 9), new Range(7, 0, 7, 13)]);
    assert.deepEqual(ranges(target, "rt"), [
      new Range(0, 6, 0, 10), new Range(6, 0, 6, 9), new Range(8, 0, 8, 12), new Range(9, 0, 9, 9),
      new Range(15, 8, 15, 12),
    ]);
    assert.deepEqual(ranges(target, "sync"), [new Range(4, 0, 4, 9), new Range(16, 7, 16, 16)]);
    assert.deepEqual(ranges(target, "feedback"), [new Range(5, 0, 5, 11)]);
  });

  it("uses the shared light and dark palette and disposes all decoration types", () => {
    const { types, subscriptions } = setup([]);
    assert.equal(types.length, Object.keys(styles).length);
    for (const [index, family] of Object.values(styles).entries() as any) {
      assert.deepEqual(types[index].options, {
        color: family.color,
        light: { color: family.lightColor },
        dark: { color: family.color },
        rangeBehavior: 1,
      });
    }
    subscriptions.forEach((item) => item.dispose());
    assert.ok(types.every((type) => type.disposed));
  });

  it("keeps directive command colors separate from symbols and clears ranges after commenting", () => {
    const source = document([
      ".DEF play 4",
      "\t .def acquire R0",
      "   .DeF set_freq 4 // play",
      "# .DEF play 4",
      "// .def gain 4",
      "move .DEF, R0",
      ".DEFAULT play 4",
      "unknown .DEF play 4",
    ].join("\n"));
    const target = editor(source);
    const session = setup([target]);
    assert.deepEqual(ranges(target, "directive"), [
      new Range(0, 0, 0, 4), new Range(1, 2, 1, 6), new Range(2, 3, 2, 7),
    ]);
    assert.deepEqual(ranges(target, "q1"), [new Range(5, 0, 5, 4)]);
    assert.deepEqual(ranges(target, "rt"), []);
    assert.deepEqual(ranges(target, "latched"), []);

    source.lines = source.lines.map((line) => `# ${line}`);
    session.change(source);
    assert.deepEqual(ranges(target, "directive"), []);
    assert.deepEqual(ranges(target, "q1"), []);
  });

  it("colors arguments consistently by syntax across instructions and .DEF declarations", () => {
    const target = editor(document([
      "start : move R0, R0xA # R1, 9, @ignored",
      "add -12, 0xA, -0xF",
      "move $gain, amplitude",
      "move {gain + 2}, R3",
      "move { gain + R0 * 2 }, R4",
      "jmp @start",
      ".DEF play 4",
      ".DEF R0 R1",
      "play R2, 0b101, -0b11 // {value} R7 @done",
      "# move R5, 42",
      "// .DEF R0 @start",
      "move R01, +3, 0b10",
    ].join("\n")));
    setup([target]);

    assert.deepEqual(tokenText(target, "register"), ["R0", "R0xA", "R3", "R4", "R1", "R2"]);
    assert.deepEqual(tokenText(target, "number"), ["-12", "0xA", "-0xF", "4"]);
    assert.deepEqual(tokenText(target, "symbol"), ["$gain", "play", "R0"]);
    assert.deepEqual(tokenText(target, "label"), ["start", "@start"]);
    assert.deepEqual(tokenText(target, "placeholder"), ["{gain + 2}", "{ gain + R0 * 2 }"]);
  });

  it("replaces argument ranges after editing token types and removes comment colors", () => {
    const source = document("loop: move R0, {gain + 2}");
    const target = editor(source);
    const session = setup([target]);
    assert.deepEqual(tokenText(target, "label"), ["loop"]);
    assert.deepEqual(tokenText(target, "register"), ["R0"]);
    assert.deepEqual(tokenText(target, "placeholder"), ["{gain + 2}"]);

    source.lines[0] = "move -0xF, $gain";
    session.change(source);
    assert.deepEqual(ranges(target, "label"), []);
    assert.deepEqual(ranges(target, "register"), []);
    assert.deepEqual(ranges(target, "placeholder"), []);
    assert.deepEqual(tokenText(target, "number"), ["-0xF"]);
    assert.deepEqual(tokenText(target, "symbol"), ["$gain"]);

    source.lines[0] = "// move -0xF, $gain";
    session.change(source);
    assert.ok(Array.from(target.applied.values()).every((items) => items.length === 0));
  });

  it("refreshes newly visible editors and replaces old family ranges after edits", () => {
    const source = document("play 0, 1, 4");
    const first = editor(source);
    const second = editor(source);
    const other = editor(document("set_freq 1"));
    const unrelated = editor(document("play 0, 1, 4", "plaintext"));
    const session = setup([first, unrelated]);
    assert.equal(unrelated.applied.size, 0);

    session.show([first, second, other]);
    assert.deepEqual(ranges(second, "rt"), [new Range(0, 0, 0, 4)]);
    const untouched = other.applied.get(families.latched.color);
    source.lines[0] = "set_freq 1";
    session.change(source);
    for (const target of [first, second]) {
      assert.deepEqual(ranges(target, "rt"), []);
      assert.deepEqual(ranges(target, "latched"), [new Range(0, 0, 0, 8)]);
    }
    assert.equal(other.applied.get(families.latched.color), untouched);

    source.lines[0] = "# set_freq 1";
    session.change(source);
    assert.deepEqual(ranges(first, "latched"), []);
  });
});
