import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  DecisionLanes, chooseSequencer, decisionLanesArgs, decisionLanesHtml, parseDecisionLanesMessage, rememberInputs, workerFailureMessage,
} from "../src/q1timeline/decisionLanes";

const Model = require("../src/q1timeline/media/decisionLanesModel.js");
const View = require("../src/q1timeline/media/decisionLanes.js");

// Records made by q1timeline.decision_lanes from the .q1asm files next to them; a Python test
// (tests/test_q1timeline_decision_lanes.py) fails when they no longer match the recorder.
const fixture = (name: string): any => JSON.parse(readFileSync(join(__dirname, "..", "..", "test", "fixtures", "decision-lanes", `${name}.json`), "utf8"));
const baseOnly = (data: any): any => ({ ...data, runs: { base: data.runs.base }, scenarios: [{ ...data.scenarios[0], flips: [] }] });
const text = (html: string) => html.replace(/<title>[^<]*<\/title>/g, "").replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ")
  .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
const count = (html: string, needle: string) => html.split(needle).length - 1;
const BROKEN = /NaN|undefined|Infinity|\[object|null/;
const lineOf = (model: any, pc: number) => pc === model.END ? "end" : model.ins.get(pc).line;

function renderAll(view: any, label: string): Record<string, string> {
  const out: Record<string, string> = { lanes: view.html.lanes(1300), stack: view.html.stack(1300), controls: view.html.controls(), chips: view.html.chips(),
    detail: view.html.detail(), sites: view.html.sites(), source: view.html.source(), table: view.html.table(), fold: view.html.fold(), notes: view.html.notes() };
  for (const [name, html] of Object.entries(out)) {
    assert.ok(!BROKEN.test(html), `${label}: ${name} shows a broken value: ${(html.match(new RegExp(".{0,60}(" + BROKEN.source + ").{0,40}")) || [""])[0]}`);
  }
  assert.ok(out.lanes.startsWith("<svg") && out.lanes.endsWith("</svg>") && out.stack.startsWith("<svg") && out.stack.endsWith("</svg>"));
  return out;
}

describe("decision lanes model", () => {
  it("classifies branches by what the condition depends on and what the arms write", () => {
    const sites = (name: string) => {
      const model = Model.create(fixture(name));
      return [...model.sites.values()].sort((a: any, b: any) => a.line - b.line).map((site: any) =>
        [site.line, site.cls, lineOf(model, site.join), [...site.footprint].map(([reg, arms]: any) => `R${reg}:${arms.taken ? "J" : ""}${arms.fall ? "C" : ""}`).join(" ")]);
    };
    assert.deepEqual(sites("loop"), [[8, "input", 12, "R2:JC"], [13, "counted", 14, ""]]);
    // The sign test only rewrites the register it compared: it shapes a value, it does not decide.
    assert.deepEqual(sites("shaping"), [[6, "shaping", 12, "R1:C"], [12, "input", 16, ""], [16, "counted", 17, ""]]);
    assert.deepEqual(sites("chain"), [[5, "input", "end", ""], [6, "input", "end", ""]]);
    assert.deepEqual(sites("once"), [[5, "input", "end", ""]]);
  });

  it("names paths from the labels a decision lands on, in every run", () => {
    const names = (name: string) => {
      const data = fixture(name), model = Model.create(data);
      return Object.fromEntries(Object.keys(data.runs).map((id) => {
        const run = model.run(id);
        return [id, run.pathLabel ?? run.passes.map((pass: any) => `${pass.kind}:${pass.name}`).join(" ")];
      }));
    };
    assert.deepEqual(names("chain"), { base: "case_one", "base~3": "case_zero", "base~4": "case_default" });
    assert.deepEqual(names("once"), { base: "L6", "base~3": "skip" });
    // A last pass that takes an arm no earlier pass took is still an iteration with a name.
    assert.deepEqual(names("loop"), { base: "iter:high iter:L9 iter:high", "base~7": "iter:L9 iter:L9 iter:high",
      "base~15": "iter:high iter:high iter:high", "base~24": "iter:high iter:L9 iter:L9" });
    assert.deepEqual(names("shaping").base, "iter:L13 iter:small iter:L13");
    assert.deepEqual(names("shaping")["base~10"], "iter:small iter:small iter:L13");
  });

  it("marks a pass the record ends in, and counts decisions only up to there", () => {
    const data = baseOnly(fixture("loop"));
    data.runs.base = { ...data.runs.base, steps: data.runs.base.steps.slice(0, 23), truncated: true, status: "step_limit", reason: "Stopped after 23 instructions (step limit)." };
    const run = Model.create(data).run("base");
    assert.deepEqual(run.passes.map((pass: any) => [pass.kind, pass.name]), [["iter", "high"], ["iter", "L9"], ["cut", null]]);
    assert.deepEqual([run.decisions.length, run.truncated, run.status], [2, true, "step_limit"]);
  });

  it("pairs runs that first differ at one branch occurrence", () => {
    const model = Model.create(fixture("loop"));
    assert.equal(model.partner("base", 7), "base~7");
    assert.equal(model.partner("base~7", 7), "base");
    assert.equal(model.partner("base~24", 15), "base~15");
    assert.equal(model.partner("base~7", 16), null);          // nothing recorded forces that later occurrence
    assert.equal(model.partner("base", 10), null);            // the counted loop is not a recorded pair
  });

  it("tells what the arms of a decision change by the join", () => {
    const loop = Model.create(fixture("loop")), state = loop.armDiff("base", 7);
    assert.deepEqual([state.cls, state.otherId, state.mine.rt, state.other.rt, lineOf(loop, state.mine.joinPc)], ["state", "base~7", 0, 0, 12]);
    assert.deepEqual(state.state, [{ reg: 2, before: 0, mine: 10, other: 1 }]);
    assert.equal(loop.firstRtDifference("base", 7), null);
    assert.deepEqual(loop.counterfactual("base", 7), { otherId: "base~7", later: 2, differ: 0, rtEnd: [300, 300], q1End: [272, 276], decisions: [3, 3] });

    const once = Model.create(fixture("once")), time = once.armDiff("base", 3);
    assert.deepEqual([time.cls, time.mine.rt, time.other.rt, time.mine.atEnd, time.mine.open], ["time", 20, 80, true, false]);
    assert.deepEqual(time.mine.rtVisible, ["wait 20 · 20 ns"]);
    assert.deepEqual(once.firstRtDifference("base", 3), { mine: 4, other: 4, kind: "instruction" });
    assert.deepEqual(once.counterfactual("base", 3).rtEnd, [620, 680]);

    const chain = Model.create(fixture("chain")), output = chain.armDiff("base", 3);
    assert.deepEqual([output.cls, output.mine.rt, output.other.rt], ["output", 4, 4]);
    assert.deepEqual([output.mine.rtVisible, output.other.rtVisible], [["set_mrk 2 · 0 ns", "upd_param 4 · 4 ns"], ["set_mrk 1 · 0 ns", "upd_param 4 · 4 ns"]]);

    const shaping = Model.create(fixture("shaping"));
    assert.deepEqual(["time", 40, 20], [shaping.armDiff("base", 10).cls, shaping.armDiff("base", 10).mine.rt, shaping.armDiff("base", 10).other.rt]);
    // Without a recorded partner there is nothing to compare with.
    assert.deepEqual([shaping.armDiff("base~10", 17).cls, shaping.armDiff("base~10", 17).other], [null, null]);
  });

  it("explains a direction from the compared words and their recorded data flow", () => {
    const loop = Model.create(fixture("loop")), signed = loop.why("base", 7);
    assert.deepEqual([signed.producer, signed.operator, signed.unsigned, signed.signTest, signed.calculated, signed.taken, signed.forced], [6, "≥", false, false, true, true, false]);
    assert.deepEqual(signed.operands.map((o: any) => [o.label, o.reg, o.value, o.writer]), [["R1", 1, 7, 4], ["5", null, 5, null]]);
    assert.deepEqual(signed.operands[0].sources, [{ acquire: null, pops: [{ step: 4, reg: 1, value: 7 }], rule: null }]);
    assert.deepEqual([loop.why("base~7", 7).taken, loop.why("base~7", 7).calculated, loop.why("base~7", 7).forced], [false, true, true]);

    const shaping = Model.create(fixture("shaping"));
    const sign = shaping.why("base", 5), compare = shaping.why("base", 10);
    // jlt R1, 2147483648 on unsigned words asks whether R1 is negative.
    assert.deepEqual([sign.signTest, sign.unsigned, sign.operands[0].value, sign.operands[0].signed], [true, true, 4294967146, -150]);
    assert.deepEqual([compare.signTest, compare.unsigned, compare.operands[0].value, compare.operands[0].chain], [false, true, 150, [8, 6]]);
    assert.deepEqual(compare.operands[0].sources[0].pops, [{ step: 3, reg: 1, value: -150 }]);
  });

  it("reads branches on status flags by the instruction that set them", () => {
    const data = fixture("flags"), model = Model.create(data), view = View.createView(data, { headless: true });
    const whys = model.run("base").decisions.map((d: any) => model.why("base", d.step));
    const shape = (w: any) => [w.op, model.opOf(model.run("base").steps[w.producer]), w.operator, w.flagTest, w.unsigned, w.calculated,
      w.operands.map((o: any) => [o.label, o.value])];
    assert.deepEqual(whys.map(shape), [
      ["jnz", "test", "≠", null, true, false, [["R1 & 1", 0], ["0", 0]]],             // a bit test: the AND of the two operands
      ["jb", "cmp", "<", null, true, false, [["R1", 6], ["5", 5]]],                   // below: an unsigned compare
      ["jz", "sub", "=", null, false, true, [["R2", 0], ["0", 0]]],                   // any other producer: its result against zero
      ["js", "cmp", null, "negative", false, false, [["R1", 6], ["0", 0]]],           // one flag of the difference
    ]);
    // Both operands of the bit test lead back to the feedback word.
    assert.deepEqual(whys[0].operands[0].sources[0].pops, [{ step: 1, reg: 1, value: 6 }]);
    const verdicts = whys.map((_: any, n: number) => { view.select(n); return text(view.html.detail()).match(/Why this way.*?→ (?:true|false)/)![0]; });
    assert.deepEqual(verdicts, ["Why this way L4 test R1, 1 R1 & 1 0 0 0 0 ≠ 0 → false", "Why this way L7 cmp R1, 5 R1 6 5 5 6 < 5 → false",
      "Why this way L10 sub R1, 6, R2 R2 0 0 0 0 = 0 → true", "Why this way L13 cmp R1, 0 R1 6 0 0 6 − 0: negative → false"]);
    assert.equal(view.html.fold(), "Path L15 (L5 Continue, L8 Continue, L11 Jump, L14 Continue)");
  });

  it("keeps waits apart when a decision runs between them", () => {
    const blocks = (name: string) => Model.create(fixture(name)).run("base").blocks.map((b: any) => [b.op, b.t0, b.t1, b.count]);
    assert.deepEqual(blocks("once"), [["wait", 0, 600, 1], ["wait", 600, 620, 1]]);
    assert.deepEqual(blocks("loop"), [["wait", 0, 100, 1], ["wait", 100, 200, 1], ["wait", 200, 300, 1]]);
    // The same wait repeated with nothing but a counted loop in between is one block.
    const data = baseOnly(fixture("loop"));
    data.instructions = [{ pc: 0, line: 1, op: "move", text: "move 3, R0", args: ["3", "R0"] }, { pc: 1, line: 2, op: "wait", text: "wait 100", args: ["100"] },
      { pc: 2, line: 3, op: "loop", text: "loop R0, @body", args: ["R0", "@body"] }, { pc: 3, line: 4, op: "stop", text: "stop", args: [] }];
    data.labels = { body: 1 };
    const loop = (i: number, taken: boolean) => [2, 0, 0, 100 * (i + 1), 100 * (i + 1), [[0, 3 - i, null]], [[0, 3 - i, 2 - i, 0]], null, [taken, taken, false, 2 + 2 * i, 1, 3], taken ? 1 : 3];
    data.runs.base = { ...data.runs.base, steps: [[0, 0, 0, 0, 0, [], [[0, null, 3, 0]], null, null, 1],
      [1, 0, 0, 0, 100, [], [], null, null, 2], loop(0, true), [1, 0, 0, 100, 200, [], [], null, null, 2], loop(1, true),
      [1, 0, 0, 200, 300, [], [], null, null, 2], loop(2, false), [3, 0, 0, 300, 300, [], [], null, null, null]], rtEnd: 300, q1End: 0 };
    assert.deepEqual(Model.create(data).run("base").blocks.map((b: any) => [b.op, b.t0, b.t1, b.count]), [["wait", 0, 300, 3]]);
  });

  it("finds immediate post-dominators as the set definition does, on random flow graphs", () => {
    const bruteForce = (succ: Set<number>[]) => {
      const n = succ.length, size = n + 1, pdom = Array.from({ length: size }, () => new Uint8Array(size).fill(1));
      pdom[n].fill(0);
      pdom[n][n] = 1;
      for (let changed = true; changed;) {
        changed = false;
        for (let k = n - 1; k >= 0; k--) {
          const meet = new Uint8Array(size).fill(1);
          for (const s of succ[k]) for (let j = 0; j < size; j++) meet[j] &= pdom[s][j];
          meet[k] = 1;
          if (meet.some((value, j) => value !== pdom[k][j])) { pdom[k] = meet; changed = true; }
        }
      }
      const total = pdom.map((row) => row.reduce((a, b) => a + b, 0));
      return Array.from({ length: n }, (_, k) => pdom[k].findIndex((member, j) => j !== k && member === 1 && total[j] === total[k] - 1));
    };
    let seed = 20261124;
    const random = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
    for (let trial = 0; trial < 400; trial++) {
      const n = 1 + Math.floor(random() * 22), succ = Array.from({ length: n }, () => new Set<number>());
      for (let k = 0; k < n; k++) {
        const kind = random();
        if (kind < 0.12) { succ[k].add(n); continue; }
        succ[k].add(k + 1 < n ? k + 1 : n);
        if (kind > 0.55) succ[k].add(Math.floor(random() * (n + 1)));
        if (kind > 0.9) succ[k].add(Math.floor(random() * n));
      }
      const reaches = new Uint8Array(n + 1);
      reaches[n] = 1;
      for (let changed = true; changed;) { changed = false; for (let k = 0; k < n; k++) if (!reaches[k] && [...succ[k]].some((s) => reaches[s])) { reaches[k] = 1; changed = true; } }
      for (let k = 0; k < n; k++) if (!reaches[k]) succ[k].add(n);
      assert.deepEqual(Array.from(Model.immediatePostDominators(succ)).slice(0, n), bruteForce(succ), `graph ${trial}`);
    }
  });
});

describe("decision lanes view", () => {
  it("renders every recorded run and decision without a broken value", () => {
    for (const name of ["chain", "flags", "loop", "once", "shaping"]) {
      const data = fixture(name), view = View.createView(data, { headless: true });
      for (const id of Object.keys(data.runs)) {
        view.setRun(id);
        const run = view.model.run(id);
        for (let n = 0; n < run.decisions.length; n++) {
          view.select(n);
          const out = renderAll(view, `${name} ${id} decision ${n}`);
          assert.equal(count(out.lanes, 'data-action="select"'), run.decisions.length);
          assert.equal(count(out.lanes, 'class="selring"'), 1);
          assert.equal(count(out.lanes, 'class="forced"'), run.forced.filter((d: any) => d.n != null).length);
          assert.equal(count(out.table, "<tr class="), run.decisions.length);
          assert.equal(/has not been recorded|is being recorded/.test(out.detail), !view.model.partner(id, run.decisions[n].step));
        }
      }
      view.setRun("base");
      view.select(0);
      view.state.show.operands = true;
      view.state.show.regs = [0];
      view.state.yFit = false;
      view.focusSelection();
      renderAll(view, `${name} zoomed`);
    }
  });

  it("draws one row per decision site and puts a differing other arm on its own RT row", () => {
    const lanes = (name: string) => View.createView(fixture(name), { headless: true }).html.lanes(1300);
    const loop = lanes("loop"), once = lanes("once"), shaping = lanes("shaping"), chain = lanes("chain");
    // State-only decision: the other arm is a dashed register line, not a second RT row.
    assert.deepEqual([count(loop, 'class="rowband"'), count(loop, 'class="series"'), count(loop, 'class="ghost"'), count(loop, 'class="block other"')], [1, 1, 1, 0]);
    assert.deepEqual([count(loop, 'class="pass'), count(loop, 'class="block"')], [3, 3]);
    assert.deepEqual([count(once, 'class="rowband"'), count(once, 'class="series"'), count(once, 'class="block other"')], [1, 0, 1]);
    assert.match(once, /Other arm RT/);
    assert.deepEqual([count(chain, 'class="rowband"'), count(chain, 'data-action="select"')], [2, 2]);
    assert.deepEqual([count(shaping, 'class="rowband"'), count(shaping, 'class="minor'), /L6 · Value shaping/.test(shaping)], [1, 3, true]);
  });

  it("gives a narrow panel a smaller right gutter with shorter labels and no range bars", () => {
    const view = View.createView(fixture("loop"), { headless: true });
    const plotWidth = (svg: string) => Number(svg.match(/<clipPath id="dl-plot"><rect x="150" y="0" width="([\d.]+)"/)![1]);
    const wide = view.html.lanes(1300), narrow = view.html.lanes(600);
    assert.deepEqual([plotWidth(wide), plotWidth(narrow)], [1300 - 150 - 178, 600 - 150 - 136]);
    assert.ok(wide.includes(">other arm 12<") && narrow.includes(">other 12<") && !narrow.includes("other arm"));
    assert.deepEqual([count(view.html.stack(1300), 'class="outcome"'), count(view.html.stack(600), 'class="outcome"')], [4, 0]);
    assert.equal(count(view.html.stack(600), 'class="end"'), 4);              // the final values are still listed
    assert.equal(count(view.html.stack(600), 'class="rowhit"'), 4);
  });

  it("summarises the passes of a loop by path and a run without a loop by its one path", () => {
    const fold = (name: string, id = "base") => { const view = View.createView(fixture(name), { headless: true }); view.setRun(id); return view.html.fold(); };
    assert.equal(fold("loop"), "3 loop passes = high × 2 (L8 Jump) + L9 × 1 (L8 Continue)");
    assert.equal(fold("loop", "base~15"), "3 loop passes = high × 3 (L8 Jump)");
    assert.equal(fold("shaping"), "3 loop passes = L13 × 2 (L12 Continue) + small × 1 (L12 Jump)");
    assert.equal(fold("chain"), "Path case_one (L5 Continue, L6 Jump)");
    assert.equal(fold("once", "base~3"), "Path skip (L5 Jump)");
  });

  it("explains the selected decision: why, what the arms change, and the recorded other arm", () => {
    const detail = (name: string, id = "base") => { const view = View.createView(fixture(name), { headless: true }); view.setRun(id); return text(view.html.detail()); };
    const loop = detail("loop");
    for (const part of ["Decision 1 / 3", "L8 jge @high · pass 1 · path high", "7 ≥ 5 → true", "The condition gives Jump.", "L5 fb_pop_data 16, R1 = 7",
      "join @next (L12)", "Changes state only", "R2 at the join 10 was 0 1 was 0", "First RT difference none", "Later decisions 0 of 2 change",
      "R2 at the end 21 other arm: 12", "RT end 300 ns other arm: 300 ns"]) assert.ok(loop.includes(part), `loop detail lacks "${part}": ${loop}`);
    const forced = detail("loop", "base~7");
    assert.ok(forced.includes("forced (assumed)") && forced.includes("The condition gives Jump; this run forces Continue as an assumption."), forced);
    const once = detail("once");
    for (const part of ["1 < 1 → false", "join end of program · the arms never rejoin", "Changes timing", "RT time 80 ns 20 ns",
      "L6 wait 20 runs a different instruction · RT 600 ns", "RT end 620 ns other arm: 680 ns"]) assert.ok(once.includes(part), `once detail lacks "${part}": ${once}`);
    assert.ok(detail("chain").includes("Changes RT instructions"));
    const shaping = detail("shaping");
    assert.ok(shaping.includes("R1 = 150 L9 add R1, 1, R1 ← L7 not R1, R1 ← L4 fb_pop_data 16, R1 = −150"), shaping);
  });

  it("lists the branches, the source around the selection and the decisions as a table", () => {
    const view = View.createView(fixture("shaping"), { headless: true });
    const sites = text(view.html.sites()), table = text(view.html.table()), source = view.html.source();
    assert.ok(sites.startsWith("3 conditional branches"), sites);
    assert.ok(sites.includes("L6 jlt R1, 2147483648, @positive Value shaping @positive L12 Jump 2 · Continue 1"), sites);
    assert.ok(sites.includes("L16 loop R0, @body Counted loop L17 Jump 2 · Continue 1 ×3, entered 1 time"), sites);
    assert.ok(table.includes("1 1 · L13 100 ns Continue Continue 150 < 100 → false Decision 1 flipped"), table);
    assert.equal(count(source, 'class="code-line active"'), 1);
    assert.match(source, /<div class="code-line active" data-action="reveal" data-value="12"/);
    assert.match(text(view.html.controls()), /3 feedback words read: −150, 30, 250/);
    assert.match(text(View.createView(fixture("loop"), { headless: true }).html.controls()), /3 feedback words read: 7, 2 \(repeating\)/);
  });

  it("reads the lanes at a time: loop pass, RT instruction, registers and the other arm", () => {
    const view = View.createView(fixture("loop"), { headless: true });
    assert.deepEqual(view.readout(150), { head: "RT 150 ns", rows: [{ k: "Loop", v: "pass 2 · path L9" }, { k: "RT instruction", v: "wait · 100 ns" },
      { k: "R2", v: "10 · other arm 1", color: "var(--ink)" }] });
    assert.deepEqual(view.readout(50).rows[2], { k: "R2", v: "0", color: "var(--ink)" });
  });

  it("asks for missing other arms: the selection first, one per direction of each branch, then the rest", () => {
    const full = fixture("shaping"), posted: any[] = [];
    const view = View.createView(baseOnly(full), { headless: true, post: (message: any) => posted.push(message) });
    const steps = (list: any[]) => list.map((item: any) => `${item.run}~${item.step}`);
    assert.deepEqual(steps(view.wantedFlips()), ["base~10", "base~5", "base~17", "base~18", "base~25"]);
    view.pumpFlips(false);
    view.pumpFlips(false);                                         // one request at a time
    assert.equal(posted.length, 1);
    assert.deepEqual([posted[0].type, posted[0].token, steps(posted[0].requests)], ["flips", null, ["base~10", "base~5", "base~17", "base~18", "base~25"]]);
    assert.match(text(view.html.detail()), /The other arm of this decision is being recorded…/);

    // The worker answers what fits its time budget; the view asks again for what is still missing.
    const answer = (ids: string[]) => ({ runs: Object.fromEntries(ids.map((id) => [id, full.runs[id]])), failed: [] });
    view.addRuns(answer(["base~10", "base~5"]));
    assert.deepEqual(steps(posted[1].requests), ["base~17", "base~18", "base~25"]);
    assert.equal(view.model.partner("base", 10), "base~10");
    assert.match(text(view.html.detail()), /Changes timing/);
    view.addRuns(answer(["base~17", "base~18", "base~25"]));
    assert.equal(posted.length, 2);
    assert.deepEqual(view.wantedFlips(), []);
    assert.deepEqual(view.data.scenarios[0].flips, ["base~10", "base~5", "base~17", "base~18", "base~25"]);
    assert.deepEqual([...view.model.sites.values()].map((site: any) => site.cls).sort(), ["counted", "input", "shaping"]);
    assert.match(text(view.html.controls()), /Decision 3 flipped L6 occurrence 1 flipped L6 occurrence 2 flipped/);
  });

  it("keeps the selection when runs arrive, and does not ask again for a flip that failed", () => {
    const full = fixture("loop"), posted: any[] = [];
    const view = View.createView(baseOnly(full), { headless: true, post: (message: any) => posted.push(message) });
    view.select(2, true);
    assert.deepEqual(posted.map((message) => message.type), ["reveal", "flips"]);
    assert.deepEqual(posted[0], { type: "reveal", line: 8 });
    assert.deepEqual(posted[1].requests[0], { run: "base", step: 24 });           // the selected decision goes first
    view.addRuns({ runs: { "base~24": full.runs["base~24"] }, failed: [{ run: "base", step: 7, reason: "boom" }] });
    assert.deepEqual([view.state.sel, view.model.run("base").decisions[view.state.sel].step], [2, 24]);
    assert.deepEqual(posted[2].requests, [{ run: "base", step: 15 }]);
    view.select(0);
    assert.match(text(view.html.detail()), /Recording the other arm failed: boom/);
  });

  it("stops recording in the background at its limit but still records the selected decision", () => {
    const posted: any[] = [];
    const view = View.createView(baseOnly(fixture("loop")), { headless: true, post: (message: any) => posted.push(message) });
    view.flips.auto = false;
    view.pumpFlips(false);
    assert.deepEqual(posted, []);
    assert.match(text(view.html.detail()), /has not been recorded\. Record it/);
    view.select(1, true);
    assert.deepEqual(posted, [{ type: "reveal", line: 8 }, { type: "flips", token: null, requests: [{ run: "base", step: 15 }] }]);
  });

  it("opens a recorded run, restores the base run and reveals source lines", () => {
    const posted: any[] = [];
    const view = View.createView(fixture("loop"), { headless: true, post: (message: any) => posted.push(message) });
    view.handlers.change({ target: { id: "dl-run", value: "base~15" } });
    assert.equal(view.state.runId, "base~15");
    assert.match(text(view.html.controls()), /Decision 2 flipped · assumed/);
    assert.equal(count(view.html.lanes(1300), 'class="hyp"'), 1);             // everything after the forced choice is marked
    view.setRun("missing");
    assert.equal(view.state.runId, "base~15");
    view.setRun("base");
    assert.equal(count(view.html.lanes(1300), 'class="hyp"'), 0);
    assert.deepEqual(posted, []);
  });

  it("keeps the lanes shown, the selection and the zoom when the same sequencer is recorded again", () => {
    const first = View.createView(fixture("loop"), { headless: true });
    first.select(2);
    Object.assign(first.state, { yFit: false, tableOpen: true, ghost: false, show: { operands: true, regs: [0, 99] } });
    const restore = () => ({ state: first.state, full: first.fullView() });
    const whole = View.createView(fixture("loop"), { headless: true, restore: restore() });
    assert.deepEqual([whole.state.sel, whole.state.show, whole.state.yFit, whole.state.tableOpen, whole.state.ghost], [2, { operands: true, regs: [0] }, false, true, false]);
    assert.deepEqual(whole.state.view, whole.fullView());            // the whole run was in view, so the whole new run is
    first.focusSelection();
    assert.notDeepEqual(first.state.view, first.fullView());
    assert.deepEqual(View.createView(fixture("loop"), { headless: true, restore: restore() }).state.view, first.state.view);
    assert.equal(View.createView(fixture("once"), { headless: true, restore: restore() }).state.sel, 0);   // fewer decisions
  });

  it("validates typed feedback words before asking for a new record", () => {
    assert.deepEqual(View.parseFeedback("1, 0x10 -3\n2_000_000"), { values: [1, 16, -3, 2000000] });
    assert.deepEqual(View.parseFeedback("-2147483648;4294967295"), { values: [-2147483648, 4294967295] });
    for (const bad of ["", " , ", "1.5", "0b1", "4294967296", "-2147483649", "1 two"]) assert.ok(View.parseFeedback(bad).error, bad);
    assert.ok(View.parseFeedback(Array(4097).fill("1").join(",")).error);

    const posted: any[] = [];
    const view = View.createView(fixture("loop"), { headless: true, post: (message: any) => posted.push(message) });
    assert.equal(view.state.draft, "7, 2");
    view.handlers.input({ target: { id: "dl-feedback", value: "3, 0x10" } });
    view.applyInputs();
    assert.deepEqual(posted, [{ type: "open", sequencer: "loop", feedback: [3, 16] }]);
    view.handlers.input({ target: { id: "dl-feedback", value: "3, x" } });
    view.applyInputs();
    assert.equal(posted.length, 1);
    assert.equal(view.state.inputError, '"x" is not an integer.');
    assert.match(view.html.controls(), /aria-invalid="true"[\s\S]*role="alert">&quot;x&quot; is not an integer\./);
  });

  it("offers the project LINQ schedule and other sequencers only when the project has them", () => {
    const plain = View.createView(fixture("loop"), { headless: true }).html.controls();
    assert.ok(!plain.includes("dl-sequencer") && !plain.includes("dl-feedback-source"));
    const data = fixture("loop"), posted: any[] = [];
    data.sequencers.push({ id: "other", name: "other", file: "other.q1asm" });
    data.inputs = { ...data.inputs, feedback: null, scheduled: true, usesSchedule: true };
    const view = View.createView(data, { headless: true, post: (message: any) => posted.push(message), history: ["7, 2", "<1>"] });
    const controls = view.html.controls();
    assert.match(controls, /<select id="dl-sequencer"><option value="loop" selected>loop<\/option><option value="other">other<\/option>/);
    assert.match(controls, /<option value="schedule" selected>Project LINQ schedule<\/option>/);
    assert.match(controls, /id="dl-feedback"[^>]* disabled/);
    assert.match(controls, /<datalist id="dl-feedback-history"><option value="7, 2"><\/option><option value="&lt;1&gt;"><\/option>/);
    assert.match(text(controls), /Feedback from the project LINQ schedule · 3 words read/);
    view.handlers.change({ target: { id: "dl-sequencer", value: "other" } });
    view.handlers.change({ target: { id: "dl-feedback-source", value: "schedule" } });
    assert.deepEqual(posted, [{ type: "open", sequencer: "other" }, { type: "open", sequencer: "loop", feedback: null }]);
  });

  it("shows an unknown compared value as an assumption, not as a computed direction", () => {
    const data = fixture("once"), steps = data.runs.base.steps;
    steps[1][6][0][2] = null;             // the popped word
    steps[3][5][0][1] = null;             // the value the branch read
    steps[3][8][1] = null;                // no computed direction
    steps[3][8][6][0][2] = null;
    data.inputs = { ...data.inputs, feedback: null };
    const view = View.createView(data, { headless: true }), detail = text(view.html.detail());
    assert.ok(detail.includes("unknown value (assumed)") && detail.includes("? < 1 → unknown"), detail);
    assert.ok(detail.includes("The compared value is unknown, so Playback assumed Continue."), detail);
    assert.match(view.html.lanes(1300), /class="cell cont unknown"/);
    assert.match(text(view.html.table()), /Continue \(assumed\) unknown/);
    assert.match(text(view.html.controls()), /1 feedback word read with unknown values/);
  });

  it("says why a run has no input decision, and where a partial run stopped", () => {
    const data: any = { schema: 1, sequencer: "s", sequencers: [{ id: "s", name: "s", file: "s.q1asm" }], sourceFile: "s.q1asm", source: ["wait 20", "stop"],
      instructions: [{ pc: 0, line: 1, op: "wait", text: "wait 20", args: ["20"] }, { pc: 1, line: 2, op: "stop", text: "stop", args: [] }], labels: {},
      registers: Array.from({ length: 64 }, (_, i) => `R${i}`), inputs: { feedback: [0], scheduled: false, usesSchedule: false, pops: 0, triggers: 0 },
      scenarios: [{ id: "inputs", base: "base", flips: [] }], engineModel: { timingModel: "analytical", assumptions: ["One executed path."] },
      runs: { base: { scenario: "inputs", kind: "base", forced: {}, steps: [[0, 0, 4, 0, 20, [], [], null, null, 1], [1, 4, 8, 20, 20, [], [], null, null, null]],
        registers: Array(64).fill(null), status: "completed", reason: null, truncated: false, feedbackCount: 0, q1End: 8, rtEnd: 20 } } };
    const complete = View.createView(data, { headless: true }), out = renderAll(complete, "no decisions");
    assert.match(text(out.detail), /No input decision in this run The run reads no feedback word/);
    assert.match(out.lanes, /No branch in the recorded runs depends on a feedback word\./);
    assert.equal(out.fold, "");
    assert.equal(out.notes, "One executed path.");
    assert.deepEqual(complete.wantedFlips(), []);
    data.runs.base = { ...data.runs.base, steps: [data.runs.base.steps[0]], status: "waiting", truncated: true, rtEnd: 20, q1End: 4,
      reason: "No connected trigger source for address 1 at 20 ns. Configure external inputs." };
    const partial = View.createView(data, { headless: true });
    assert.match(text(partial.html.detail()), /The run stopped early: No connected trigger source for address 1 at 20 ns\./);
    assert.match(text(partial.html.controls()), /Partial · Waiting for an input/);
    assert.match(partial.html.notes(), /This run is partial: No connected trigger source/);
  });

  it("escapes text that comes from the source file and from the engine", () => {
    const data = fixture("once");
    data.instructions[3].text = "jlt <u>$RESULT</u>, 1, @skip";
    data.source[4] = "jlt <img src=x onerror=alert(1)>";
    data.labels = { "<skip>": 6 };
    data.runs.base = { ...data.runs.base, truncated: true, status: "blocked", reason: "<script>alert(1)</script>" };
    const view = View.createView(data, { headless: true });
    // fold and notes are plain text (the page assigns them with textContent); everything else is markup.
    const { fold, notes, ...markup } = renderAll(view, "escaping");
    const html = Object.values(markup).join("\n");
    assert.ok(!/<u>|<img|<script>|<skip>/.test(html));
    assert.ok(html.includes("&lt;u&gt;$RESULT&lt;/u&gt;") && html.includes("@&lt;skip&gt;") && html.includes("&lt;script&gt;alert(1)&lt;/script&gt;"));
    assert.equal(fold, "Path L6 (L5 Continue)");
    assert.ok(notes.endsWith("This run is partial: <script>alert(1)</script>"));
  });
});

describe("decision lanes host", () => {
  it("accepts only the messages the page sends, in exactly their shape", () => {
    const valid = [{ type: "ready" }, { type: "refresh" }, { type: "open", sequencer: "seq" }, { type: "open", sequencer: "seq", feedback: null },
      { type: "open", sequencer: "seq", feedback: [0, -2147483648, 4294967295] }, { type: "flips", token: 3, requests: [{ run: "base", step: 0 }] },
      { type: "flips", token: null, requests: [{ run: "base~7", step: 16 }] }, { type: "reveal", line: 1 }];
    for (const message of valid) assert.deepEqual(parseDecisionLanesMessage(message), message);
    const invalid = [null, "ready", [], {}, { type: "ready", extra: 1 }, { type: "open" }, { type: "open", sequencer: "" }, { type: "open", sequencer: 3 },
      { type: "open", sequencer: "seq", feedback: [] }, { type: "open", sequencer: "seq", feedback: [1.5] }, { type: "open", sequencer: "seq", feedback: [4294967296] },
      { type: "open", sequencer: "seq", feedback: "1" }, { type: "open", sequencer: "seq", project: "/etc" }, { type: "flips", token: 1, requests: [] },
      { type: "flips", token: "1", requests: [{ run: "base", step: 0 }] }, { type: "flips", token: 1, requests: [{ run: "base", step: -1 }] },
      { type: "flips", token: 1, requests: [{ run: "", step: 1 }] }, { type: "flips", token: 1, requests: [{ run: "base", step: 1, command: "open" }] },
      { type: "flips", token: 1, requests: Array(65).fill({ run: "base", step: 1 }) }, { type: "reveal", line: 0 }, { type: "reveal", line: "3" },
      { type: "reveal", line: 2, file: "/etc/passwd" }, { type: "unknown" }];
    for (const message of invalid) assert.equal(parseDecisionLanesMessage(message), undefined, JSON.stringify(message));
  });

  it("builds a page that runs only its two nonce-carrying scripts", () => {
    const html = decisionLanesHtml({ cspSource: "vscode-resource:", nonce: "N0nce", styleUri: "style.css", scriptUris: ["model.js", "view.js"] });
    assert.match(html, /default-src 'none'; style-src vscode-resource: 'unsafe-inline'; script-src 'nonce-N0nce'"/);
    assert.deepEqual([...html.matchAll(/<script[^>]*>/g)].map((match) => match[0]), ['<script nonce="N0nce" src="model.js">', '<script nonce="N0nce" src="view.js">']);
    assert.match(html, /<link rel="stylesheet" href="style\.css">[\s\S]*<main id="decision-lanes"><\/main>/);
  });

  it("derives worker arguments, the sequencer to show and remembered inputs", () => {
    assert.deepEqual(decisionLanesArgs("p.yml", "project", 500), ["-u", "-m", "q1timeline.decision_lanes", "--project", "p.yml"]);
    assert.deepEqual(decisionLanesArgs("p.yml", "concrete", 500).slice(-2), ["--max-steps", "500"]);
    assert.deepEqual(decisionLanesArgs("p.yml", "concrete", 5e6).slice(-2), ["--max-steps", "100000"]);
    assert.equal(decisionLanesArgs("p.yml", "concrete", "many").length, 5);
    const sequencers = [{ id: "a", name: "a", file: join("dir", "a.q1asm"), scheduled: false }, { id: "b", name: "b", file: join("dir", "b.q1asm"), scheduled: true }];
    assert.equal(chooseSequencer(sequencers, "b", join("dir", "a.q1asm"))?.id, "b");
    assert.equal(chooseSequencer(sequencers, "gone", join("dir", "b.q1asm"))?.id, "b");
    assert.equal(chooseSequencer(sequencers, undefined, join("dir", "c.q1asm"))?.id, "a");
    assert.equal(chooseSequencer([], undefined, undefined), undefined);

    let stored = rememberInputs({}, "p::a", [0]);
    stored = rememberInputs(stored, "p::a", [1, 2]);
    stored = rememberInputs(stored, "p::a", [0]);
    stored = rememberInputs(stored, "p::a", null);                       // the schedule keeps the typed history
    assert.deepEqual(stored, { "p::a": { feedback: null, history: ["0", "1, 2"] } });
    for (let i = 0; i < 12; i++) stored = rememberInputs(stored, "p::a", [i + 10]);
    assert.deepEqual(stored["p::a"].history, ["21", "20", "19", "18", "17", "16", "15", "14"]);
    for (let i = 0; i < 60; i++) stored = rememberInputs(stored, `q${i}::s`, [i]);
    assert.deepEqual([Object.keys(stored).length, "p::a" in stored, Object.keys(stored).at(-1)], [50, false, "q59::s"]);

    assert.match(workerFailureMessage(new Error("Decision lanes process exited (1)."), "python: No module named q1timeline.decision_lanes\n"),
      /does not include Decision lanes\. Update the q1lens package/);
    assert.equal(workerFailureMessage(new Error("Decision lanes process exited (1)."), "Traceback\nValueError: bad\n"), "Decision lanes process exited (1). ValueError: bad");
    assert.equal(workerFailureMessage(new Error("Unknown sequencer: x"), "noise"), "Unknown sequencer: x");
  });

  function harness(options: { scheduled?: boolean; fail?: (payload: any) => string | undefined; python?: boolean } = {}) {
    const source = join("C:", "work", "probe.q1asm"), project = join("C:", "work", "q1timeline.yml");
    const posted: any[] = [], requests: any[] = [], warnings: string[] = [], shown: any[] = [], log: string[] = [];
    let receive: (message: unknown) => void = () => {}, closePanel: () => void = () => {};
    let created = 0, clients = 0, disposed = 0;
    const store: Record<string, unknown> = {};
    const panel: any = { title: "", viewColumn: 2, reveal() {},
      webview: { cspSource: "vscode-resource:", html: "", asWebviewUri: (uri: any) => ({ toString: () => `webview:${uri.path}` }),
        postMessage: async (message: any) => { posted.push(message); return true; },
        onDidReceiveMessage: (listener: any) => { receive = listener; return { dispose() {} }; } },
      onDidDispose: (listener: any) => { closePanel = listener; return { dispose() {} }; }, dispose() { closePanel(); } };
    const uri = (file: string) => ({ fsPath: file, scheme: "file", path: file.replace(/\\/g, "/") });
    const vscode: any = {
      Uri: { file: uri, joinPath: (base: any, ...parts: string[]) => ({ fsPath: join(base.fsPath, ...parts), scheme: "file", path: [base.path, ...parts].join("/") }) },
      ViewColumn: { One: 1, Two: 2, Beside: -2 }, TextEditorRevealType: { InCenterIfOutsideViewport: 2 },
      window: { activeTextEditor: { document: { uri: uri(source) } }, visibleTextEditors: [{ document: { uri: uri(source) }, viewColumn: 1 }],
        createWebviewPanel: () => { created++; return panel; }, showWarningMessage: async (message: string) => { warnings.push(message); },
        showTextDocument: async (document: any, how: any) => { shown.push({ file: document.uri.fsPath, how }); return { revealRange() {} }; } },
      workspace: { isTrusted: true, textDocuments: [] as any[], getConfiguration: () => ({ get: (_key: string, fallback: unknown) => fallback }),
        openTextDocument: async (target: any) => ({ uri: target, lineCount: 10, lineAt: (index: number) => ({ range: { line: index } }) }) },
    };
    const context: any = { extensionUri: uri(join("C:", "ext")),
      workspaceState: { get: (key: string, fallback: unknown) => key in store ? store[key] : fallback, update: async (key: string, value: unknown) => { store[key] = value; } } };
    const sequencers = [{ id: "readout", name: "readout", file: join("C:", "work", "readout.q1asm"), scheduled: false },
      { id: "probe", name: "probe", file: source, scheduled: !!options.scheduled }];
    const lanes = new DecisionLanes(vscode, context, { appendLine: (line: string) => log.push(line) }, {
      ensureEnvironment: async () => options.python === false ? undefined : { pythonPath: "python", pythonArgs: ["-X", "utf8"] },
      findProject: async () => uri(project) as any,
      relatedFiles: async () => [join("C:", "work", "params.yml")],
      createClient: (pythonPath, args, clientOptions) => {
        clients++;
        const client: any = { command: [pythonPath, ...args], cwd: clientOptions.cwd,
          request: async (payload: any) => {
            requests.push(payload);
            const failure = options.fail?.(payload);
            if (failure) throw new Error(failure);
            if (payload.command === "info") return { info: { sequencers } };
            if (payload.command === "open") return { lanes: { sequencer: payload.sequencer, sourceFile: sequencers.find((item) => item.id === payload.sequencer)!.file,
              inputs: { feedback: payload.feedback } } };
            return { runs: Object.fromEntries(payload.requests.map((item: any) => [`${item.run}~${item.step}`, { kind: "flip" }])), failed: [], pending: [] };
          },
          dispose() { disposed++; } };
        return client;
      },
    }, 0);                                                           // no delay before a changed file is recorded again
    const send = async (message: unknown) => { receive(message); await new Promise((resolve) => setImmediate(resolve)); await new Promise((resolve) => setImmediate(resolve)); };
    // File changes are recorded from a timer; give it and the record that follows time to finish.
    const settle = async () => { await new Promise((resolve) => setTimeout(resolve, 20)); await new Promise((resolve) => setImmediate(resolve)); };
    return { lanes, vscode, panel, posted, requests, warnings, shown, log, store, source, project, uri, send, settle,
      get created() { return created; }, get clients() { return clients; }, get disposed() { return disposed; } };
  }

  it("opens the sequencer written in the active file and sends the record once the page is ready", async () => {
    const h = harness();
    await h.lanes.open();
    assert.equal(h.created, 1);
    assert.match(h.panel.webview.html, /webview:.*\/out\/src\/q1timeline\/media\/decisionLanesModel\.js[\s\S]*decisionLanes\.js/);
    assert.deepEqual(h.requests, [{ command: "info" }, { command: "open", sequencer: "probe", feedback: [0] }]);
    assert.deepEqual(h.posted, []);                                // nothing is posted before the page listens
    await h.send({ type: "ready" });
    assert.deepEqual(h.posted, [{ type: "data", token: 1, payload: { sequencer: "probe", sourceFile: h.source, inputs: { feedback: [0] } }, history: ["0", "1"], notice: "" }]);
    assert.equal(h.panel.title, "Decision lanes · probe.q1asm");
    assert.deepEqual(h.store["q1lens.decisionLanes.inputs"], { [`${process.platform === "win32" ? h.project.toLowerCase() : h.project}::probe`]: { feedback: [0], history: ["0"] } });
    await h.lanes.open(h.uri(h.source) as any);                       // opening again reuses the panel and the worker
    assert.deepEqual([h.created, h.clients], [1, 1]);
  });

  it("starts the worker for the project with the selected Python command", async () => {
    const h = harness();
    let command: string[] = [], cwd = "";
    const original = (h.lanes as any).dependencies.createClient;
    (h.lanes as any).dependencies.createClient = (pythonPath: string, args: string[], options: any) => { command = [pythonPath, ...args]; cwd = options.cwd; return original(pythonPath, args, options); };
    await h.lanes.open();
    assert.deepEqual(command, ["python", "-X", "utf8", "-u", "-m", "q1timeline.decision_lanes", "--project", h.project]);
    assert.equal(cwd, join("C:", "work"));
  });

  it("uses the project LINQ schedule by default when the sequencer has one, and remembers typed words", async () => {
    const h = harness({ scheduled: true });
    await h.lanes.open();
    await h.send({ type: "ready" });
    assert.deepEqual(h.requests[1], { command: "open", sequencer: "probe", feedback: null });
    await h.send({ type: "open", sequencer: "probe", feedback: [1, 0] });
    assert.deepEqual(h.requests.at(-1), { command: "open", sequencer: "probe", feedback: [1, 0] });
    assert.deepEqual(h.posted.at(-1).history, ["1, 0", "0", "1"]);
    await h.send({ type: "refresh" });                             // the remembered words, not the default
    assert.deepEqual(h.requests.at(-1), { command: "open", sequencer: "probe", feedback: [1, 0] });
    await h.send({ type: "open", sequencer: "readout" });          // another sequencer starts from its own default
    assert.deepEqual(h.requests.at(-1), { command: "open", sequencer: "readout", feedback: [0] });
    await h.send({ type: "open", sequencer: "readout", feedback: null });   // no schedule there: unknown words are not offered
    assert.deepEqual(h.requests.at(-1), { command: "open", sequencer: "readout", feedback: [0] });
  });

  it("relays flip requests of the current record only, and reports a worker failure per request", async () => {
    let failFlips = false;
    const h = harness({ fail: (payload) => failFlips && payload.command === "flips" ? "worker stopped" : undefined });
    await h.lanes.open();
    await h.send({ type: "ready" });
    await h.send({ type: "flips", token: 1, requests: [{ run: "base", step: 7 }] });
    assert.deepEqual(h.requests.at(-1), { command: "flips", requests: [{ run: "base", step: 7 }], budget_ms: 200 });
    assert.deepEqual(h.posted.at(-1), { type: "runs", token: 1, runs: { "base~7": { kind: "flip" } }, failed: [] });
    const before = h.requests.length;
    await h.send({ type: "flips", token: 99, requests: [{ run: "base", step: 7 }] });
    await h.send({ type: "flips", token: null, requests: [{ run: "base", step: 7 }] });
    await h.send({ type: "flips", token: 1, requests: [{ run: "base", step: 7, extra: true }] });
    assert.equal(h.requests.length, before);
    assert.match(h.log.join("\n"), /ignored a malformed webview message/);
    failFlips = true;
    await h.send({ type: "flips", token: 1, requests: [{ run: "base", step: 15 }] });
    assert.deepEqual(h.posted.at(-1), { type: "runs", token: 1, runs: {}, failed: [{ run: "base", step: 15, reason: "worker stopped" }] });
    (h.lanes as any).stopWorker();                                  // the page still gets an answer when the worker is gone
    await h.send({ type: "flips", token: 1, requests: [{ run: "base", step: 24 }] });
    assert.deepEqual(h.posted.at(-1), { type: "runs", token: 1, runs: {},
      failed: [{ run: "base", step: 24, reason: "The Python worker is not running. Refresh to record again." }] });
  });

  it("opens the recorded source line in the editor that already shows the file", async () => {
    const h = harness();
    await h.send({ type: "reveal", line: 3 });                     // nothing is open yet
    await h.lanes.open();
    await h.send({ type: "ready" });
    await h.send({ type: "reveal", line: 5 });
    await h.send({ type: "reveal", line: 500 });                   // clamped to the document
    assert.deepEqual(h.shown.map((item) => [item.file, item.how.viewColumn, item.how.preserveFocus, item.how.selection.line]),
      [[h.source, 1, true, 4], [h.source, 1, true, 9]]);
    h.vscode.window.visibleTextEditors = [];
    await h.send({ type: "reveal", line: 1 });
    assert.equal(h.shown.at(-1).how.viewColumn, 1);                // the panel sits in column two
  });

  it("records again when a file of the project changes, once for changes close together, and marks unsaved edits once", async () => {
    const h = harness();
    await h.lanes.open();
    await h.send({ type: "ready" });
    const requests = h.requests.length;
    h.lanes.onFileChanged(join("C:", "elsewhere", "x.q1asm"));
    await h.settle();
    assert.equal(h.requests.length, requests);
    // The change event that carries an edit arrives before the document is marked dirty.
    h.lanes.onDocumentChanged({ uri: h.uri(h.source), isDirty: false } as any);
    assert.deepEqual(h.posted.filter((message) => message.type === "stale"), []);
    h.lanes.onDocumentChanged({ uri: h.uri(h.source), isDirty: true } as any);
    h.lanes.onDocumentChanged({ uri: h.uri(h.source), isDirty: true } as any);
    h.lanes.onDocumentChanged({ uri: h.uri(join("C:", "elsewhere", "x.q1asm")), isDirty: true } as any);
    assert.deepEqual(h.posted.filter((message) => message.type === "stale"), [{ type: "stale", message: "Unsaved changes are not included. Save the file to record again." }]);
    h.vscode.workspace.textDocuments = [{ uri: h.uri(h.source), isDirty: true }];
    h.lanes.onFileChanged(join("C:", "work", "params.yml"));        // a file the project refers to: saved in VS Code…
    h.lanes.onFileChanged(join("C:", "work", "params.yml"));        // …and reported by the file watcher
    await h.settle();
    assert.deepEqual(h.requests.slice(requests), [{ command: "info" }, { command: "open", sequencer: "probe", feedback: [0] }]);
    assert.deepEqual([h.posted.at(-1).type, h.posted.at(-1).token, h.posted.at(-1).notice], ["data", 2, "Unsaved changes are not included. Save the file to record again."]);
    await h.send({ type: "flips", token: 1, requests: [{ run: "base", step: 7 }] });   // a request for the previous record is dropped
    assert.equal(h.requests.at(-1).command, "open");
  });

  it("reports a failed record, a missing environment and an unsuitable file without leaving a stale worker", async () => {
    const failing = harness({ fail: (payload) => payload.command === "open" ? "No instruction was executed: waiting" : undefined });
    await failing.lanes.open();
    await failing.send({ type: "ready" });
    assert.deepEqual(failing.posted, [{ type: "error", message: "No instruction was executed: waiting" }]);

    const noPython = harness({ python: false });
    await noPython.lanes.open();
    await noPython.send({ type: "ready" });
    assert.deepEqual([noPython.posted[0].type, noPython.clients], ["error", 0]);
    assert.match(noPython.posted[0].message, /Select a Python environment/);

    const wrongFile = harness();
    await wrongFile.lanes.open(wrongFile.uri(join("C:", "work", "notes.txt")) as any);
    wrongFile.vscode.workspace.isTrusted = false;
    await wrongFile.lanes.open();
    assert.deepEqual([wrongFile.created, wrongFile.warnings], [0, ["Open a saved .q1asm file to show its decision lanes.", "Trust this workspace before Q1Lens runs Python."]]);

    const closed = harness();
    await closed.lanes.open();
    closed.panel.dispose();
    assert.equal(closed.disposed, 1);
    closed.lanes.onFileChanged(closed.source);
    await closed.settle();
    assert.equal(closed.requests.length, 2);
    await closed.lanes.open();                                      // opening again starts over with a new panel and worker
    assert.deepEqual([closed.created, closed.clients, closed.requests.length], [2, 2, 4]);
  });

  it("restarts the worker when the Python or execution settings change", async () => {
    const h = harness();
    await h.lanes.open();
    await h.send({ type: "ready" });
    h.lanes.onEnvironmentChanged();
    await h.send(undefined);
    assert.deepEqual([h.clients, h.disposed, h.posted.at(-1).type, h.posted.at(-1).token], [2, 1, "data", 2]);
  });
});
