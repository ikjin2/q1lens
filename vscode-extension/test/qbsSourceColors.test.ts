import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const instructionColors = require("../src/shared/timeline/instructionColors.js");

interface SourceNode {
  className: string;
  textContent: string;
  style: { color?: string };
  children: SourceNode[];
  appendChild(child: SourceNode): void;
}

function loadSourceRenderer(): (text: string) => SourceNode {
  const script = readFileSync(join(__dirname, "..", "src", "qbs", "webview", "assets", "timeline.js"), "utf8");
  const source = script.match(/function renderQ1asmSourceLine\(raw\) \{[\s\S]*?\n\}/)?.[0];
  assert.ok(source, "Missing QBS Q1ASM source renderer");
  const element = (_tag: string, className: string, text = ""): SourceNode => ({
    className,
    textContent: text,
    style: {},
    children: [],
    appendChild(child) { this.children.push(child); },
  });
  return Function("element", "instructionColors", `${source}\nreturn renderQ1asmSourceLine;`)(element, instructionColors);
}

function sourceText(node: SourceNode): string {
  return node.children.length ? node.children.map((child) => child.textContent).join("") : node.textContent;
}

function opcode(node: SourceNode): SourceNode {
  const commands = node.children.filter((child) => child.className === "q1asm-opcode");
  assert.equal(commands.length, 1);
  return commands[0];
}

function argumentsOfKind(node: SourceNode, kind: string): SourceNode[] {
  return node.children.filter((child) => child.className === `q1asm-argument q1asm-argument-${kind}`);
}

function assertArgumentColors(node: SourceNode): void {
  for (const [kind, style] of Object.entries(instructionColors.argumentStyles) as [string, any][]) {
    for (const argument of argumentsOfKind(node, kind)) {
      assert.equal(argument.style.color, `var(--q1asm-arg-${kind}-color, ${style.color})`);
    }
  }
}

describe("QBS Q1ASM source coloring", () => {
  it("colors each instruction family with the palette used by timeline blocks", () => {
    const render = loadSourceRenderer();
    const cases = [
      ["muls32h", "q1", "#80b7ff"],
      ["stop", "flow", "#f4a261"],
      ["set_scope_en", "latched", "#6ec6d9"],
      ["latch_rst", "rt", "#8bcf9a"],
      ["wait_trigger", "sync", "#b9a7dc"],
      ["fb_acq_tb_valid", "feedback", "#ff6fb3"],
      ["future_instruction", "unknown", "#94a3b8"],
    ];
    for (const [instruction, family, color] of cases) {
      const text = `  entry: ${instruction.toUpperCase()} R0 # keep operands and comments`;
      const node = render(text);
      assert.equal(node.className, "q1asm-line-text");
      assert.equal(sourceText(node), text);
      assert.equal(opcode(node).textContent, instruction.toUpperCase());
      assert.equal(opcode(node).style.color, `var(--q1asm-${family}-color, ${color})`);
      assertArgumentColors(node);
    }
  });

  it("preserves comment-only and empty lines without coloring their contents", () => {
    const render = loadSourceRenderer();
    for (const text of ["# play", "  // set_freq", "# .DEF play 4", "  // .def play 4", ""]) {
      const node = render(text);
      assert.equal(sourceText(node), text || " ");
      assert.equal(node.children.length, 0);
    }
  });

  it("colors declarations on label-only lines without treating their names as commands", () => {
    const render = loadSourceRenderer();
    for (const text of ["entry:", "entry: # stop", "play : # stop"]) {
      const node = render(text);
      assert.equal(sourceText(node), text);
      assert.equal(node.children.filter((child) => child.className === "q1asm-opcode").length, 0);
      assert.equal(argumentsOfKind(node, "label").length, 1);
      assertArgumentColors(node);
    }
  });

  it("colors the opcode after spaced labels even when a label is an instruction name", () => {
    const render = loadSourceRenderer();
    const text = "  play  : WAIT_SYNC 4 # preserve spaced label";
    const node = render(text);
    assert.equal(sourceText(node), text);
    assert.equal(argumentsOfKind(node, "label")[0].textContent, "play");
    assert.equal(opcode(node).textContent, "WAIT_SYNC");
    assert.equal(opcode(node).style.color, "var(--q1asm-sync-color, #b9a7dc)");
    assertArgumentColors(node);
  });

  it("colors uppercase and lowercase .DEF directives, declaration names, and values with their own categories", () => {
    const render = loadSourceRenderer();
    for (const directive of [".DEF", ".def"]) {
      const text = `\t${directive} play 16 # .DEF set_freq 4`;
      const node = render(text);
      assert.equal(sourceText(node), text);
      assert.equal(opcode(node).textContent, directive);
      assert.equal(opcode(node).style.color, "var(--q1asm-directive-color, #dfc27d)");
      assert.deepEqual(argumentsOfKind(node, "symbol").map((argument) => argument.textContent), ["play"]);
      assert.deepEqual(argumentsOfKind(node, "number").map((argument) => argument.textContent), ["16"]);
      assert.deepEqual(node.children.at(-1)?.style, {});
      assert.equal(node.children.at(-1)?.textContent, " # .DEF set_freq 4");
      assertArgumentColors(node);
    }
  });

  it("uses the shared editor argument palette for registers, numbers, symbols, labels, and placeholders", () => {
    const render = loadSourceRenderer();
    const text = "  again : add R0x1, -0x10, -2, $PULSE_LEN, @again, {PULSE_LEN + 4} # R3 12 @fake .DEF";
    const node = render(text);
    assert.equal(sourceText(node), text);
    const expected: Record<string, string[]> = {
      register: ["R0x1"],
      number: ["-0x10", "-2"],
      symbol: ["$PULSE_LEN"],
      label: ["again", "@again"],
      placeholder: ["{PULSE_LEN + 4}"],
    };
    for (const [kind, values] of Object.entries(expected)) {
      assert.deepEqual(argumentsOfKind(node, kind).map((argument) => argument.textContent), values);
    }
    assertArgumentColors(node);
    assert.equal(node.children.at(-1)?.textContent, " # R3 12 @fake .DEF");
    assert.deepEqual(node.children.at(-1)?.style, {});
    for (const uncolored of node.children.filter((child) => !child.className)) {
      assert.deepEqual(uncolored.style, {});
    }
  });

  it("uses symbol colors for references to .DEF names including instruction-shaped names", () => {
    const render = loadSourceRenderer();
    const text = "move $play, $GAIN, R2 // play $OTHER R8 0x12 {fake}";
    const node = render(text);
    assert.equal(sourceText(node), text);
    assert.equal(opcode(node).textContent, "move");
    assert.deepEqual(argumentsOfKind(node, "symbol").map((argument) => argument.textContent), ["$play", "$GAIN"]);
    assert.deepEqual(argumentsOfKind(node, "register").map((argument) => argument.textContent), ["R2"]);
    assertArgumentColors(node);
    assert.equal(node.children.at(-1)?.textContent, " // play $OTHER R8 0x12 {fake}");
    assert.deepEqual(node.children.at(-1)?.style, {});
  });
});
