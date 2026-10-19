import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const instructionColors = require("../src/shared/timeline/instructionColors.js");

function loadBlockRenderer(): (block: any) => any {
  const script = readFileSync(join(__dirname, "../src/qbs/webview/assets/timeline.js"), "utf8");
  const sources = ["renderBlockLabel", "renderBlock"].map((name) => {
    const source = script.match(new RegExp(`function ${name}\\([^\\n]*\\) \\{[\\s\\S]*?\\n\\}`))?.[0];
    assert.ok(source, `Missing QBS ${name}`);
    return source;
  });
  const element = (tag: string, className: string, text = ""): any => {
    const node: any = {
      tag, className, textContent: text, children: [], dataset: {}, attributes: {},
      style: { setProperty(name: string, value: string) { this[name] = value; } } as Record<string, any>,
      appendChild(child: any) { this.children.push(child); },
      addEventListener() {},
      setAttribute(name: string, value: string) { this.attributes[name] = value; },
      toggleAttribute(name: string, enabled: boolean) {
        if (enabled) this.attributes[name] = "";
        else delete this.attributes[name];
      },
    };
    node.classList = { add(name: string) { node.className += ` ${name}`; } };
    return node;
  };
  return Function("element", "instructionColors", `${sources.join("\n")}\nreturn renderBlock;`)(element, instructionColors);
}

function visibleText(node: any): string {
  return node.children.length ? node.children.map(visibleText).join("") : node.textContent;
}

function q1Block(label: string, instruction = "play", selected = false): any {
  return {
    id: "q1:test", type: "q1", visualKind: "q1", label, selected,
    accentColor: instructionColors.commandAccentColor(instruction),
    leftPercent: 0, widthPercent: 30, startLabel: "0 ns", durationLabel: "4 ns", detail: "L3",
  };
}

describe("QBS command block colors", () => {
  it("marks Q1 blocks with their command color in both selected and unselected states", () => {
    const render = loadBlockRenderer();
    for (const instruction of ["move", "loop", "set_freq", "play", "wait_sync", "fb_pop_data"]) {
      for (const selected of [false, true]) {
        const block = q1Block(instruction, instruction, selected);
        const node = render(block);
        assert.ok(node.className.split(" ").includes("has-command-color"));
        assert.equal(node.style["--timeline-block-accent"], instructionColors.commandAccentColor(instruction));
        assert.equal(node.attributes["aria-selected"], String(selected));
        assert.equal(visibleText(node.children[0]), instruction);
        assert.equal(node.children[1].textContent, "L3");
      }
    }
  });

  it("colors arguments already present in the label without adding raw source arguments or coloring details", () => {
    const render = loadBlockRenderer();
    const text = "play R0, -0x10, $GAIN, @start, { DURATION // 2 }";
    const node = render({ ...q1Block(text), q1asmText: "play R9, 42, 400", detail: "R8, 100 ns" });
    const label = node.children[0];
    assert.equal(visibleText(label), text);
    assert.equal(label.children.find((child: any) => child.className === "q1asm-opcode").textContent, "play");
    for (const [kind, value] of Object.entries({ register: "R0", number: "-0x10", symbol: "$GAIN", label: "@start", placeholder: "{ DURATION // 2 }" })) {
      const token = label.children.find((child: any) => child.className === `q1asm-argument q1asm-argument-${kind}`);
      assert.equal(token.textContent, value);
      assert.equal(token.style.color, instructionColors.argumentAccentColor(kind));
    }
    assert.equal(node.children[1].textContent, "R8, 100 ns");
    assert.equal(node.children[1].children.length, 0);
    assert.equal(node.children[1].style.color, undefined);
  });

  it("preserves compact friendly labels and keeps high-level schedule blocks outside command styling", () => {
    const render = loadBlockRenderer();
    const feedback = render(q1Block("fb", "fb_pop_data"));
    assert.equal(feedback.children[0].textContent, "fb");
    assert.equal(feedback.children[0].children.length, 0);
    const pulse = render({ ...q1Block("play R0, 4"), type: "pulse", visualKind: "pulse", accentColor: undefined });
    assert.ok(!pulse.className.split(" ").includes("has-command-color"));
    assert.equal(pulse.style["--timeline-block-accent"], undefined);
    assert.equal(pulse.children[0].textContent, "play R0, 4");
    assert.equal(pulse.children[0].children.length, 0);
  });

  it("retains the command border, label hue, and fill when a Q1 block is selected", () => {
    const css = readFileSync(join(__dirname, "../src/qbs/webview/assets/timeline.css"), "utf8");
    const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)];
    const selected = rules.find((rule) => rule[1].includes('.timeline-block-q1.has-command-color[aria-selected="true"]'));
    assert.ok(selected, "Selected Q1 blocks must retain command colors");
    assert.ok(selected[1].includes(".timeline-block-q1.has-command-color,"));
    assert.match(selected[2], /background:\s*color-mix\(in srgb, var\(--timeline-block-accent\) 15%, transparent\)/);
    assert.match(selected[2], /border-color:\s*var\(--timeline-block-accent\)/);
    const label = rules.find((rule) => rule[1].trim() === ".timeline-block-q1.has-command-color .block-label");
    assert.ok(label);
    assert.match(label[2], /color:\s*var\(--timeline-block-accent\)/);
    assert.ok(selected.index! > rules.find((rule) => rule[1].trim() === '.timeline-block[aria-selected="true"]')!.index!);
    assert.match(css, /\.timeline-block\[aria-selected="true"\]\s*\{[^}]*outline:\s*2px solid var\(--vscode-focusBorder\)/);
  });
});
