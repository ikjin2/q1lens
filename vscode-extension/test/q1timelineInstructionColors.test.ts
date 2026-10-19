import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const colors = require("../src/shared/timeline/instructionColors.js");
const { buildQ1TimelineSharedModel } = require("../src/q1timeline/media/timelineAdapter.js");
const { renderTimeline, renderAnnotations, loopAnnotationLayout } = require("../src/shared/timeline/renderer.js");

function domNode(tagName = "div"): any {
  let text = "";
  const node: any = {
    tagName, className: "", children: [], dataset: {}, attributes: {}, listeners: {},
    style: { setProperty(name: string, value: string) { this[name] = value; } } as any,
    appendChild(child: any) { this.children.push(child); return child; },
    replaceChildren(...children: any[]) { this.children = children; },
    setAttribute(name: string, value: string) { this.attributes[name] = value; },
    addEventListener(name: string, listener: (event: any) => void) { this.listeners[name] = listener; },
  };
  Object.defineProperty(node, "childNodes", { get: () => node.children });
  node.classList = { add(name: string) { node.className += ` ${name}`; } };
  Object.defineProperty(node, "textContent", {
    get: () => text + node.children.map((child: any) => child.textContent).join(""),
    set(value) { text = String(value); node.children = []; },
  });
  return node;
}

function descendants(node: any): any[] {
  return [node, ...node.children.flatMap(descendants)];
}

function withDom(callback: () => void) {
  const previousDocument = (globalThis as any).document;
  (globalThis as any).document = { createElement: domNode, createElementNS: (_namespace: string, tag: string) => domNode(tag) };
  try { callback(); }
  finally {
    if (previousDocument === undefined) delete (globalThis as any).document;
    else (globalThis as any).document = previousDocument;
  }
}

describe("Q1ASM command colors", () => {
  it("gives every supported family the same color in Q1 and RT blocks in either layout", () => {
    const events = Object.entries(colors.families).filter(([family]) => family !== "directive").flatMap(([family, spec]: [string, any]) =>
      spec.ops.flatMap((op: string, index: number) => ["q1_issue", op].map((kind) => ({
        id: `${kind}:${op}`, kind, sequencer_id: "s0", lane: kind === "q1_issue" ? "q1.issue" : "rt.control",
        source: { raw: `play : ${op.toUpperCase()} 4 // comment`, file: "s0.q1asm", line: index + 1 },
        t0: { value: index * 4 }, t1: { value: index * 4 + 4 }, duration: { value: 4 }, confidence: "exact",
        label: op, meta: { expectedFamily: family },
      }))),
    );
    for (const layout of ["time", "instruction"]) {
      const blocks = buildQ1TimelineSharedModel({ events }, { layout }).lanes.flatMap((lane: any) => lane.blocks);
      assert.equal(blocks.length, events.length);
      for (const block of blocks) {
        const family = block.domain.meta.expectedFamily;
        assert.equal(block.accentColor, `var(--q1asm-${family}-color, ${colors.families[family].color})`, block.id);
        assert.ok(block.classNames.includes(`command-family-${family}`), block.id);
        if (block.kind === "q1_issue") assert.equal(block.label, block.domain.source.raw.split(" ")[2]);
      }
    }
  });

  it("renders source command and argument hues inside blocks, including selected blocks", () => {
    const previousDocument = (globalThis as any).document;
    (globalThis as any).document = { createElement: domNode };
    try {
      for (const [family, spec] of Object.entries(colors.families) as [string, any][]) {
        if (family === "directive") continue;
        for (const selected of [false, true]) {
          const root = domNode();
          const label = `${spec.ops[0]} R0, -16, $GAIN, @start, {N * 4}`;
          const accentColor = colors.commandAccentColor(spec.ops[0]);
          renderTimeline(root, { viewport: { start: 0, end: 4 }, ticks: [], lanes: [{ id: "s0", label: "s0", blocks: [
            { id: "command", kind: "q1_issue", start: 0, duration: 4, label, accentColor, selected },
          ] }] });
          const block = descendants(root).find((node) => node.dataset.blockId === "command");
          assert.ok(block.className.includes("has-command-color"));
          assert.equal(block.style["--timeline-block-accent"], accentColor);
          assert.equal(block.attributes["aria-selected"], String(selected));
          const labelNode = block.children.find((node: any) => node.className === "block-label");
          assert.equal(labelNode.textContent, label);
          const colored = labelNode.children.filter((node: any) => node.style.color);
          assert.deepEqual(colored.map((node: any) => node.style.color), [accentColor,
            ...["register", "number", "symbol", "label", "placeholder"].map(colors.argumentAccentColor),
          ]);
        }
      }
    } finally {
      if (previousDocument === undefined) delete (globalThis as any).document;
      else (globalThis as any).document = previousDocument;
    }
  });

  it("prefers explicit opcode metadata and uses semantic fallbacks for generated events", () => {
    const events = [
      { id: "metadata", kind: "q1_issue", meta: { op: "cmp" }, source: { raw: "set_freq 1" }, expected: "q1" },
      { id: "pop", kind: "feedback_pop", expected: "feedback" },
      { id: "sync", kind: "sync_wait", expected: "sync" },
      { id: "branch", kind: "branch_region", source: { raw: "play 0,1,4" }, expected: "flow" },
      { id: "unknown", kind: "q1_issue", source: { raw: "future_opcode 4" }, expected: "unknown" },
    ].map((item) => ({ ...item, sequencer_id: "s0", t0: { value: 0 }, t1: { value: 4 }, duration: { value: 4 } }));
    const blocks = buildQ1TimelineSharedModel({ events }).lanes.flatMap((lane: any) => lane.blocks);
    for (const event of events) {
      const block = blocks.find((candidate: any) => candidate.id === event.id);
      assert.ok(block.classNames.includes(`command-family-${event.expected}`), event.id);
    }
  });

  it("keeps light and dark webview palettes aligned with editor colors", () => {
    const css = readFileSync(join(__dirname, "../src/shared/timeline/renderer.css"), "utf8");
    const light = css.slice(css.indexOf(":root {"), css.indexOf("body.vscode-dark,"));
    const dark = css.slice(css.indexOf("body.vscode-dark,"), css.indexOf("}", css.indexOf("body.vscode-dark,")));
    for (const [family, spec] of Object.entries(colors.families) as [string, any][]) {
      assert.ok(light.includes(`--q1asm-${family}-color: ${spec.lightColor};`));
      assert.ok(dark.includes(`--q1asm-${family}-color: ${spec.color};`));
    }
    for (const [kind, spec] of Object.entries(colors.argumentStyles) as [string, any][]) {
      assert.ok(light.includes(`--q1asm-arg-${kind}-color: ${spec.lightColor};`));
      assert.ok(dark.includes(`--q1asm-arg-${kind}-color: ${spec.color};`));
    }
  });
});

describe("Shared timeline overlapping loop annotations", () => {
  const viewport = { start: 0, end: 100 };
  const loop = (id: string, start: number, end: number, laneId = "s0") => ({ id, eventId: id, type: "loop-range", laneId, start, end });

  it("assigns deterministic levels to nested, identical, touching and zero-duration ranges", () => {
    for (const annotations of [
      [loop("inner", 0, 8), loop("outer", 0, 16), loop("same-as-inner", 0, 8)],
      [loop("a", 0, 0), loop("b", 0, 0), loop("c", 0, 0)],
    ]) {
      const layout = loopAnnotationLayout(annotations, viewport);
      assert.equal(layout.offset, 42);
      assert.equal(new Set(layout.levels.values()).size, 3);
      assert.deepEqual(Array.from(layout.levels), Array.from(loopAnnotationLayout([...annotations].reverse(), viewport).levels));
    }
    const touching = loopAnnotationLayout([loop("first", 0, 16), loop("second", 16, 32)], viewport);
    assert.deepEqual(Array.from(touching.levels.values()), [0, 1]);
    assert.equal(touching.offset, 28);
    const separated = loopAnnotationLayout([loop("first", 0, 8), loop("second", 9, 16), loop("offscreen", 101, 110)], viewport);
    assert.deepEqual(Array.from(separated.levels.values()), [0, 0]);
    assert.equal(separated.offset, 0);
  });

  it("gives each nested summary an independent strip and preserves original block spacing below it", () => withDom(() => {
    const root = domNode();
    const clicked: string[] = [];
    renderTimeline(root, { viewport, lanes: [
      { id: "s0", label: "s0", blocks: [{ id: "wait", start: 0, duration: 16 }] },
      { id: "plain", label: "plain", blocks: [] },
    ], annotations: [loop("inner", 0, 8), loop("outer", 0, 16)] }, {
      onAnnotationClick: (annotation: any) => clicked.push(annotation.eventId),
    });
    const nodes = descendants(root);
    const row = nodes.find((node) => node.dataset.lane === "s0");
    const plain = nodes.find((node) => node.dataset.lane === "plain");
    assert.equal(row.style["--timeline-loop-offset"], "28px");
    assert.equal(row.style.minHeight, "64px");
    assert.equal(row.children.find((node: any) => node.className.startsWith("lane-track")).style.minHeight, "64px");
    assert.equal(plain.style.minHeight, undefined);
    assert.equal(plain.style["--timeline-loop-offset"], undefined);
    const annotations = nodes.filter((node) => node.className.split(" ").includes("timeline-loop-range"));
    assert.deepEqual(annotations.map((node) => [node.dataset.eventId, node.style.top]), [["inner", "14px"], ["outer", "0px"]]);
    for (const annotation of annotations) {
      assert.ok(Number.parseInt(annotation.style.top) + 14 <= Number.parseInt(row.style["--timeline-loop-offset"]) + 7);
      annotation.listeners.click({ stopPropagation() {} });
    }
    assert.deepEqual(clicked, ["inner", "outer"]);
    const css = readFileSync(join(__dirname, "../src/shared/timeline/renderer.css"), "utf8");
    assert.match(css, /\.timeline-block\s*{[^}]*top:\s*calc\(7px \+ var\(--timeline-loop-offset, 0px\)\)/s);
  }));

  it("leaves zero, single and nonoverlapping loop lanes at their original height", () => withDom(() => {
    for (const annotations of [[], [loop("single", 1, 4)], [loop("first", 1, 4), loop("second", 5, 8)]]) {
      const root = domNode();
      renderTimeline(root, { viewport, lanes: [{ id: "s0", blocks: [] }], annotations });
      const row = descendants(root).find((node) => node.dataset.lane === "s0");
      assert.equal(row.style.minHeight, undefined);
      assert.equal(row.style["--timeline-loop-offset"], undefined);
      assert.equal(row.children.find((node: any) => node.className.startsWith("lane-track")).style.minHeight, undefined);
    }
  }));

  it("places a static nested forward branch in the same offset track inherited by its control chip", () => withDom(() => {
    const common = { sequencer_id: "seq0", confidence: "exact", t0: { value: 0 }, duration: { value: 0 } };
    const model = buildQ1TimelineSharedModel({ events: [
      { ...common, id: "branch", kind: "branch_region", lane: "rt.branch", t0: { value: 8 }, t1: { value: 8 },
        source: { file: "program.q1asm", line: 7, raw: "jz @skip" }, meta: { branch_id: "branch:7:jz:skip" } },
      { ...common, id: "inner", kind: "loop_block", lane: "rt.loop", t1: { value: 12 }, meta: { count: 2 } },
      { ...common, id: "outer", kind: "loop_block", lane: "rt.loop", t1: { value: 24 }, meta: { count: 2 } },
    ] });
    const root = domNode();
    renderTimeline(root, model);
    const row = descendants(root).find((node) => node.dataset.lane === "sequencer:seq0");
    const track = row.children.find((node: any) => node.className.startsWith("lane-track"));
    const branch = track.children.find((node: any) => node.dataset.eventId === "branch");
    assert.ok(branch.className.includes("timeline-block-branch_effect"));
    assert.equal(row.style["--timeline-loop-offset"], "28px");
    assert.equal(track.children.filter((node: any) => node.dataset.annotationType === "loop-range").length, 2);
    const css = readFileSync(join(__dirname, "../src/q1timeline/media/timeline.css"), "utf8");
    assert.match(css, /\.q1timeline-control-chip\s*{[^}]*translateY\(var\(--timeline-loop-offset, 0px\)\)/s);
  }));

  it("anchors cross-lane feedback to cumulative visible heights and uses measured row geometry", () => withDom(() => {
    const root = domNode();
    const model = { viewport, lanes: [
      { id: "s0", blocks: [] }, { id: "hidden", hidden: true, blocks: [] }, { id: "s1", blocks: [] }, { id: "s2", blocks: [] },
    ], annotations: [loop("a", 0, 16), loop("b", 0, 8), loop("c", 0, 16, "s2"), loop("d", 0, 8, "s2"),
      { id: "feedback", type: "feedback-cross", fromLaneId: "s0", toLaneId: "s2", fromTime: 25, toTime: 75 },
    ] };
    renderTimeline(root, model);
    let nodes = descendants(root);
    let connector = nodes.find((node) => node.attributes.class === "feedback-connector");
    assert.equal(connector.attributes.d, "M 25 60 C 25 105 75 115 75 160");
    assert.equal(nodes.find((node) => node.attributes.class === "feedback-connector-svg").attributes.viewBox, "0 0 100 164");
    const rows = nodes.filter((node) => node.dataset.lane && !node.dataset.timelineHidden);
    for (const [index, row] of rows.entries()) {
      row.offsetHeight = [65, 37, 65][index];
      row.offsetTop = [0, 65, 102][index];
    }
    renderAnnotations(nodes.find((node) => node.className === "timeline-lanes"), model);
    nodes = descendants(root);
    connector = nodes.filter((node) => node.attributes.class === "feedback-connector").at(-1);
    assert.match(connector.attributes.d, /^M 25 61 C .* 75 163$/);
    assert.equal(nodes.filter((node) => node.attributes.class === "feedback-connector-svg").at(-1).attributes.viewBox, "0 0 100 167");
  }));
});
