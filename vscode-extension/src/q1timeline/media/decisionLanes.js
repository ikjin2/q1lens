/* Decision lanes — view. Rendering and interaction only: every value shown comes from the model
 * (decisionLanesModel.js), which reads the runs recorded by q1timeline.decision_lanes. */
(function (root) {
  'use strict';
  const Model = (root && root.q1lensDecisionLanesModel) || (typeof require === 'function' ? require('./decisionLanesModel.js') : null);

  const MAX_FEEDBACK_VALUES = 4096;
  // Other arms are recorded automatically up to these limits; beyond them only for the selected decision.
  const AUTO_FLIP_RUNS = 64, AUTO_FLIP_STEPS = 600000, FLIP_BATCH = 8, STACK_FLIP_ROWS = 24;

  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = v => v == null ? '?' : (v < 0 ? '−' : '') + Math.abs(v).toLocaleString('en-US');
  const clock = v => v == null ? '?' : v >= 1e6 ? (v / 1e6).toFixed(3) + ' ms' : v >= 1e3 ? (v / 1e3).toFixed(3) + ' µs' : v + ' ns';
  const r1 = n => Math.round(n * 10) / 10;
  const fit = (text, max) => text.length > max ? text.slice(0, max - 1) + '…' : text;
  const plural = (n, word, many) => `${num(n)} ${n === 1 ? word : many || word + 's'}`;

  // "1, 0x10 -3" → [1, 16, -3]. Words are separated by commas or spaces; underscores are ignored.
  function parseFeedback(text) {
    const tokens = String(text ?? '').split(/[\s,;]+/).filter(Boolean);
    if (!tokens.length) return { error: 'Enter at least one integer.' };
    if (tokens.length > MAX_FEEDBACK_VALUES) return { error: `Enter at most ${MAX_FEEDBACK_VALUES} values.` };
    const values = [];
    for (const token of tokens) {
      const clean = token.replace(/_/g, '');
      if (!/^[-+]?(?:0x[0-9a-f]+|\d+)$/i.test(clean)) return { error: `"${token}" is not an integer.` };
      const sign = clean.startsWith('-') ? -1 : 1, value = sign * Number(clean.replace(/^[-+]/, ''));
      if (!Number.isSafeInteger(value) || value < -(2 ** 31) || value > 2 ** 32 - 1) return { error: `${token} does not fit in a 32-bit word.` };
      values.push(value);
    }
    return { values };
  }
  const formatFeedback = values => (values || []).join(', ');

  const CLASS = {
    input: ['Input decision', 'The condition traces back to a feedback word, and the arms do different things.'],
    shaping: ['Value shaping', 'Depends on the input, but only rewrites a register it compared (absolute value, clamp).'],
    indirect: ['Indirect', 'Reads a value written inside an arm of an input-dependent branch.'],
    counted: ['Counted loop', 'Reads only constants and jumps backward: a loop with a fixed count.'],
    fixed: ['Fixed test', 'Reads only values computed from constants. It does not depend on the input.'],
  };
  const DIFF = {
    time: ['Changes timing', 'The arms take different RT time, so everything after the branch shifts. Compare Jump and Continue as separate rows.'],
    output: ['Changes RT instructions', 'The arms take the same RT time but run different RT instructions. Compare their output as separate rows.'],
    state: ['Changes state only', 'RT time and RT instructions are the same; only register values differ. Read it on the state lane.'],
    none: ['No difference at the join', 'Nothing differs at the join except Q1 time.'],
  };
  const KIND_TEXT = { value: 'reads a different value', instruction: 'runs a different instruction', time: 'starts at a different RT time',
    duration: 'has a different RT duration', length: 'One run ends first' };
  const STATUS_TEXT = { completed: 'Completed', step_limit: 'Step limit', waiting: 'Waiting for an input', blocked: 'Blocked', error: 'Error',
    limit: 'Event limit', paused: 'Paused' };

  const SKELETON = `
<section class="controls" aria-label="Record" id="dl-controls"></section>
<p class="boundary"><strong>Model</strong><span id="dl-notes"></span></p>
<div class="banner" id="dl-banner" hidden></div>
<div id="dl-content">
<section class="card" aria-label="Timeline">
<div class="card-top"><span><strong>Timeline</strong>&nbsp;&nbsp;<span id="dl-fold"></span></span><div class="toolbar"><button data-action="fit">Fit</button><button data-action="focus">Around selection</button><button data-action="zoom-out" aria-label="Zoom out">−</button><button data-action="zoom-in" aria-label="Zoom in">+</button><button data-action="yfit" id="dl-yfit"></button><span>Ctrl+wheel zooms · drag pans · ←/→ steps through decisions</span></div></div>
<div class="lanes-wrap" id="dl-lanes-wrap" tabindex="0" aria-label="Loops, RT instructions, decisions and register values on the RT clock"><div id="dl-lanes"></div><div class="tip" id="dl-tip" hidden></div></div>
<div class="chips" id="dl-chips"></div>
<div class="legend"><span><i class="sw tri"></i>acquire</span><span><i class="sw jump"></i>Jump (upper rail)</span><span><i class="sw cont"></i>Continue (lower rail)</span><span><i class="sw forced"></i>forced (assumed)</span><span><i class="sw hyp"></i>after a forced choice</span><span><i class="sw dash"></i>recorded other arm</span></div>
</section>
<section class="card" aria-label="Recorded runs">
<div class="card-top"><span><strong>Recorded runs</strong>&nbsp;&nbsp;same time axis · solid cells go a different way than the open run</span></div>
<div class="lanes-wrap"><div id="dl-stack"></div></div>
</section>
<section id="dl-detail" aria-label="Selected decision"></section>
<div class="two"><section class="card" id="dl-sites" aria-label="Conditional branches"></section><section class="card" id="dl-source" aria-label="Q1ASM source"></section></div>
<details class="card" id="dl-table"></details>
<div class="foot">
<span>The horizontal axis is the RT clock. Q1 instructions issued between two RT instructions share one position; it is not the moment a decision is physically made.</span>
<span>A flip run repeats the same inputs with one branch occurrence forced the other way. It shows what that arm does, not that the path can be reached.</span>
<span>Acquisitions and feedback words are paired by their recorded order; the record has no link between them.</span>
</div>
</div>
<div class="sr" id="dl-announce" role="status" aria-live="polite"></div>`;

  // One recorded project state. data is the record from q1timeline.decision_lanes; env.post sends a
  // message to the extension host, env.root is the mounted element (absent in headless tests).
  function createView(data, env) {
    env = env || {};
    const headless = !!env.headless, host = env.root || null, post = env.post || (() => {});
    const $ = id => host.querySelector('#dl-' + id);
    let model = Model.create(data);
    const { PC, Q0, T0, T1, READS, WRITES, BR, TAKEN } = model.index;
    const reg = r => data.registers[r];
    // Left gutter for row labels; right gutter for end labels. A panel beside the editor is narrow,
    // so there the right gutter is smaller and its labels shorter.
    const GL = 150, NARROW = 880, MIN_WIDTH = 560;
    const gutterRight = W => W < NARROW ? 136 : 178;
    const baseId = data.scenarios[0].base;

    const state = { scenario: data.scenarios[0].id, runId: baseId, sel: 0, view: null, ghost: true, yFit: true,
      show: { operands: false, regs: [] }, focusStep: null, tableOpen: false, inputError: null,
      draft: formatFeedback(data.inputs.feedback), source: data.inputs.usesSchedule ? 'schedule' : 'values' };
    const flips = { inflight: false, auto: true, failed: new Map(), recorded: 0, steps: 0 };
    const R = () => model.run(state.runId);
    const selDecision = () => R().decisions[state.sel] || null;
    const dirName = taken => taken ? 'Jump' : 'Continue';
    function dirTarget(site, taken) {
      const pc = taken ? site.target : site.fall, label = model.labelAt.get(pc), instruction = model.ins.get(pc);
      return label ? '@' + label : instruction ? 'L' + instruction.line : '?';
    }
    function runLabel(id) {
      const raw = data.runs[id];
      if (raw.kind === 'base') return 'Base run';
      const view = model.run(id), first = view.firstForced, more = view.forced.length - 1;
      const tail = more > 0 ? ` (+${more} more)` : '';
      if (!first) return 'Flip run';
      if (first.n != null) return `Decision ${first.n + 1} flipped${tail}`;
      return `L${model.sites.get(first.pc).line} occurrence ${first.k + 1} flipped${tail}`;
    }
    // A flip of a decision (class "input"), as opposed to a flip of another input-dependent branch.
    const isDecisionFlip = id => { const first = model.run(id).firstForced; return !!first && first.n != null; };
    // Path names come from the source's labels (see the model); a pass without decisions has none.
    const pathText = name => name == null ? 'no decision' : name;
    const stepLine = (run, i) => model.lineOf(run.steps[i]);
    const stepText = (run, i) => model.textOf(run.steps[i]);

    // ---- view window ---------------------------------------------------------------------------
    function fullView() { const end = Math.max(1, R().rtEnd), pad = end * 0.012; return [-pad, end + pad]; }
    function clampView(v0, v1) {
      const [a, b] = fullView(), span = Math.max(Math.min(200, b - a), Math.min(b - a, v1 - v0)), start = Math.max(a, Math.min(b - span, v0));
      return [start, start + span];
    }
    function zoomAt(factor, center) {
      const [v0, v1] = state.view, c = center == null ? (v0 + v1) / 2 : center;
      state.view = clampView(c - (c - v0) * factor, c + (v1 - c) * factor);
    }
    function focusSelection() {
      const d = selDecision(), pass = d && R().passes[d.pass];
      if (!d) return;
      if (!pass) { const span = (fullView()[1] - fullView()[0]) * 0.18; state.view = clampView(d.t - span, d.t + span); return; }
      const len = Math.max(1, pass.t1 - pass.t0);
      state.view = clampView(pass.t0 - len * 0.08, pass.t1 + len * 0.62);
    }
    function keepVisible(t) {
      const [v0, v1] = state.view, span = v1 - v0;
      if (t < v0 + span * 0.04 || t > v1 - span * 0.04) state.view = clampView(t - span / 2, t + span / 2);
    }
    state.view = fullView();
    // A new record of the same sequencer keeps what the reader had set up: the lanes shown, the
    // selected decision, and the zoom unless the whole run was in view.
    if (env.restore) {
      const old = env.restore.state, full = env.restore.full;
      state.ghost = old.ghost;
      state.yFit = old.yFit;
      state.tableOpen = old.tableOpen;
      state.show = { operands: old.show.operands, regs: old.show.regs.filter(r => r >= 0 && r < data.registers.length) };
      state.sel = Math.max(0, Math.min(R().decisions.length - 1, old.sel));
      if (Math.abs(old.view[0] - full[0]) > 1e-6 || Math.abs(old.view[1] - full[1]) > 1e-6) state.view = clampView(old.view[0], old.view[1]);
    }

    // ---- ticks and number text --------------------------------------------------------------------
    function niceStep(raw) { const pow = Math.pow(10, Math.floor(Math.log10(raw))); return [1, 2, 5, 10].map(m => m * pow).find(s => s >= raw); }
    function timeTicks(v0, v1, target) {
      const step = Math.max(1, niceStep((v1 - v0) / target)), ticks = [];
      for (let k = Math.ceil(v0 / step); k * step <= v1; k++) ticks.push(k * step);
      return { ticks, step };
    }
    function tickText(t, step, span) {
      const [div, unit] = span >= 2e6 ? [1e6, ' ms'] : span >= 2e3 ? [1e3, ' µs'] : [1, ' ns'];
      const dec = Math.max(0, Math.min(6, Math.ceil(-Math.log10(step / div) - 1e-9)));
      return (t / div || 0).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec }) + unit;
    }
    function valueTicks(lo, hi, target) {
      const step = Math.max(1, niceStep((hi - lo) / target)), ticks = [];
      for (let k = Math.ceil(lo / step); k * step <= hi; k++) ticks.push(k * step);
      return ticks;
    }
    // One style per axis: full numbers under 10,000, compact above, with just enough decimals to keep ticks distinct.
    function axisText(values) {
      const max = Math.max(...values.map(Math.abs));
      const [div, suffix] = max >= 1e9 ? [1e9, 'G'] : max >= 1e6 ? [1e6, 'M'] : max >= 1e4 ? [1e3, 'k'] : [1, ''];
      for (let dec = 0; dec <= 6; dec++) {
        const out = values.map(v => (v < 0 ? '−' : '') + (Math.abs(v) / div).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec }) + suffix);
        if (new Set(out).size === values.length) return out;
      }
      return values.map(num);
    }

    // ---- series for the state lane -------------------------------------------------------------------
    // Writes issued between two RT instructions share one RT clock; the lane keeps the last of them.
    const laneCache = new Map();
    function lanePoints(runId, r) {
      const key = runId + '|' + r;
      if (laneCache.has(key)) return laneCache.get(key);
      const out = [];
      for (const p of model.run(runId).series(r)) {
        if (p.v == null) continue;
        if (out.length && out[out.length - 1].t === p.t) out[out.length - 1] = p; else out.push(p);
      }
      laneCache.set(key, out);
      return out;
    }
    function laneValue(points, t) { let v = null; for (const p of points) { if (p.t <= t) v = p.v; else break; } return v; }
    // True when two step series agree everywhere in [t0, t1]; an other-arm line that equals the
    // recorded one is not drawn.
    function sameBetween(a, b, t0, t1) {
      if (laneValue(a, t0) !== laneValue(b, t0)) return false;
      for (const points of [a, b]) for (const pt of points) if (pt.t > t0 && pt.t <= t1 && laneValue(a, pt.t) !== laneValue(b, pt.t)) return false;
      return true;
    }
    function seriesColor(r, k) {
      const arms = model.footprint().get(r);
      if (!arms) return k ? 'var(--faint)' : 'var(--ink)';
      return arms.taken && !arms.fall ? 'var(--jump)' : arms.fall && !arms.taken ? 'var(--cont)' : 'var(--ink)';
    }
    function bandList() {
      const fp = [...model.footprint().keys()], ops = model.operandRegisters(), out = [];
      if (fp.length) out.push({ key: 'footprint', title: 'Written by the arms', regs: fp, h: 150 });
      if (state.show.operands && ops.length) out.push({ key: 'operands', title: 'Compared values', regs: ops, h: 76 });
      for (const r of state.show.regs) out.push({ key: 'r' + r, title: R().rtRead.includes(r) ? 'Read by RT instructions' : 'Register', regs: [r], h: 68 });
      return out;
    }

    const predCache = new Map();
    function predicate(runId, d) {
      const key = runId + '|' + d.step;
      if (predCache.has(key)) return predCache.get(key);
      const w = model.why(runId, d.step);
      let out;
      if (!w) out = { w: null, text: 'no recorded instruction set the flags', negative: null, diff: null, ratio: null };
      else {
        const a = w.operands[0].value, b = w.operands[1].value;
        const diff = a == null || b == null ? null : Math.abs(a - b), scale = Math.max(Math.abs(a ?? 0), Math.abs(b ?? 0));
        const result = w.calculated == null ? 'unknown' : w.calculated ? 'true' : 'false';
        // A compare against 0x80000000 on unsigned words is a sign test; say that instead of two huge numbers.
        const negative = w.signTest && w.calculated != null ? (w.operator === '≥') === !!w.calculated : null;
        const text = w.signTest ? `${w.operands[0].label} = ${num(w.operands[0].signed)} (${negative == null ? 'sign unknown' : negative ? 'negative' : 'not negative'})`
          : w.flagTest ? `${num(a)} − ${num(b)}: ${w.flagTest} → ${result}`
          : `${num(a)} ${w.operator || w.op} ${num(b)} → ${result}`;
        out = { w, text, negative, diff, ratio: w.signTest || w.flagTest || diff == null || !scale ? null : diff / scale };
      }
      predCache.set(key, out);
      return out;
    }

    // ---- the lanes ---------------------------------------------------------------------------------------
    let geo = { X0: GL, X1: GL + 600 };
    function lanesSvg(W) {
      const run = R(), S = run.steps, [v0, v1] = state.view, span = v1 - v0;
      const X0 = GL, X1 = Math.max(GL + 260, W - gutterRight(W)), PW = X1 - X0;
      const x = t => r1(X0 + (t - v0) / span * PW);
      const inside = t => t >= v0 && t <= v1;
      const sel = selDecision();
      const why = sel ? predicate(state.runId, sel).w : null;
      const partnerId = sel ? model.partner(state.runId, sel.step) : null;
      const ghost = state.ghost && partnerId ? model.run(partnerId) : null;
      const bands = bandList();
      const sites = [...model.sites.values()].sort((a, b) => a.line - b.line);
      const minorSites = sites.filter(s => s.cls === 'shaping' || s.cls === 'indirect' || s.cls === 'fixed');
      const inputSites = sites.filter(s => s.cls === 'input');
      // One row per decision site, for every program: Jump on the row's upper rail, Continue on the
      // lower. Branches issued between the same two RT instructions share one RT clock, so each
      // site needs its own row. Only the row height follows the number of rows.
      const rowOf = new Map(inputSites.map((site, k) => [site.pc, k]));
      const dense = inputSites.length > 1, off = dense ? 6.5 : 11, half = dense ? 5.5 : 8, pitch = 2 * (off + half) + (dense ? 8 : 10);
      // When the selected decision's arms differ in RT time or RT instructions, the recorded
      // other arm gets its own RT row: that is the case where one row cannot show both.
      const armDiff = sel ? model.armDiff(state.runId, sel.step) : null;
      const otherRt = ghost && armDiff && (armDiff.cls === 'time' || armDiff.cls === 'output') ? ghost : null;
      const dy = otherRt ? 30 : 0;
      const yRow = k => 98 + dy + pitch / 2 + k * pitch;
      const cellY = d => yRow(rowOf.get(d.pc)) + (d.taken ? -off : off);
      const yMinor = 98 + dy + Math.max(1, inputSites.length) * pitch + 9, yState = yMinor + minorSites.length * 13 + 8;
      const H = yState + bands.reduce((n, b) => n + b.h + 10, 0) + 2;
      const p = [`<defs><clipPath id="dl-plot"><rect x="${X0}" y="0" width="${PW}" height="${H}"/></clipPath></defs>`];

      const tt = timeTicks(v0, v1, Math.max(3, Math.floor(PW / 120)));
      for (const t of tt.ticks) p.push(`<line class="grid" x1="${x(t)}" y1="18" x2="${x(t)}" y2="${H - 2}"/><text class="tick" x="${x(t)}" y="12" text-anchor="middle">${tickText(t, tt.step, span)}</text>`);
      p.push(`<text class="lane-label" x="0" y="12">RT clock</text>`);
      if (run.firstForced && run.firstForced.t <= v1) {
        const a = Math.max(X0, x(run.firstForced.t)), b = Math.min(X1, x(run.rtEnd));
        if (b > a) p.push(`<rect class="hyp" x="${a}" y="20" width="${r1(b - a)}" height="3"><title>Everything after a forced choice is an assumption</title></rect>`);
      }

      // Loop passes.
      if (run.decisionLoop) {
        const iterations = run.passes.filter(q => q.kind === 'iter').length;
        p.push(`<text class="lane-label" x="0" y="42">Loop ${run.decisionLoop.label ? '@' + esc(fit(run.decisionLoop.label, 14)) : ''} ×${iterations}</text>`);
        for (const pass of run.passes) {
          if (pass.kind !== 'iter') {
            // The last visit to the loop header: only the exit test ran, or the record ends inside the pass.
            if (inside(pass.t0)) p.push(`<line class="cap" x1="${x(pass.t0)}" y1="26" x2="${x(pass.t0)}" y2="50"/><text class="lane-label" x="${x(pass.t0) + 5}" y="42">${pass.kind === 'cut' ? 'record ends' : 'exit test'}</text>`);
            continue;
          }
          if (pass.t1 < v0 || pass.t0 > v1) continue;
          const a = Math.max(X0, x(pass.t0)), b = Math.min(X1, x(pass.t1)), w = b - a;
          // The path name when it fits, cut short when it nearly fits, the pass number when it does not.
          const name = pathText(pass.name), room = Math.floor((w - 10) / 6.3);
          const label = [`${pass.index + 1} · ${name}`, name].find(text => text.length <= room) || (room >= 6 ? fit(name, room) : room >= 2 ? String(pass.index + 1) : '');
          p.push(`<rect class="pass${pass.index % 2 ? ' odd' : ''}" x="${a}" y="28" width="${r1(Math.max(0, w - 2))}" height="20" rx="3"><title>Pass ${pass.index + 1} · path ${esc(name)} · RT ${clock(pass.t0)} – ${clock(pass.t1)} · ${plural(pass.end - pass.start + 1, 'instruction')}</title></rect>`);
          if (label) p.push(`<text class="lane-text" x="${r1(a + w / 2 - 1)}" y="42" text-anchor="middle" pointer-events="none">${esc(label)}</text>`);
        }
      }

      // RT instructions.
      p.push(`<text class="lane-label" x="0" y="74">RT instructions</text>`);
      const drawBlocks = (view, y, h, cls, fromT) => {
        for (const block of view.blocks) {
          if (block.t1 <= fromT || block.t1 < v0 || block.t0 > v1) continue;
          const a = Math.max(X0, x(Math.max(block.t0, fromT))), b = Math.min(X1, x(block.t1)), w = b - a;
          if (w < 1.2) continue;
          const name = block.count > 1 ? `${block.op} ×${block.count}` : stepText(view, block.first), full = name + ' · ' + clock(block.t1 - block.t0);
          const label = (block.count > 1 ? [full, name, '×' + block.count] : [name, block.op]).find(text => w >= text.length * 6.3 + 10) || '';
          const lines = block.count > 1 ? `L${stepLine(view, block.first)}–L${stepLine(view, block.last)}` : `L${stepLine(view, block.first)}`;
          p.push(`<rect class="${cls}" x="${a}" y="${y}" width="${r1(Math.max(0.6, w - 2))}" height="${h}" rx="3"><title>${esc(full)} · ${lines} · RT ${clock(block.t0)} – ${clock(block.t1)}</title></rect>`);
          if (label) p.push(`<text class="lane-text" x="${r1(a + w / 2 - 1)}" y="${y + h / 2 + 4}" text-anchor="middle" pointer-events="none">${esc(label)}</text>`);
        }
      };
      drawBlocks(run, 58, 24, 'block', -Infinity);
      if (otherRt) {
        // The other arm's RT instructions from the decision on, taken step by step from its record.
        p.push(`<text class="lane-label" x="0" y="107">Other arm RT</text>`);
        drawBlocks({ blocks: model.rtBlocks(otherRt.steps, sel.step + 1), steps: otherRt.steps }, 92, 22, 'block other', -Infinity);
      }
      const sources = [];
      if (why) for (const operand of why.operands) for (const s of operand.sources || []) if (s.acquire != null) sources.push(s);
      const sourceSteps = new Set(sources.map(s => s.acquire));
      for (const i of run.acquires) {
        const t = S[i][T0];
        if (!inside(t)) continue;
        const read = S[i][READS].map(r => `${reg(r[0])} = ${num(r[1])}`).join(', ');
        p.push(`<path class="acq${sourceSteps.has(i) ? ' on' : ''}" d="M${x(t) - 4} 50 h8 l-4 6 z"><title>L${stepLine(run, i)} ${esc(stepText(run, i))}${read ? ' · ' + esc(read) : ''} · RT ${clock(t)}</title></path>`);
      }

      // Decisions: a band with two rails per site, each rail labelled with where that direction goes.
      inputSites.forEach((site, k) => {
        const up = r1(yRow(k) - off), down = r1(yRow(k) + off), hint = esc(`L${site.line} ${site.text}`);
        p.push(`<rect class="rowband" x="${X0}" y="${r1(up - half - 4)}" width="${PW}" height="${r1(2 * (off + half) + 8)}" rx="3"/>`);
        p.push(`<line class="rail" x1="${X0}" y1="${up}" x2="${X1}" y2="${up}"/><line class="rail" x1="${X0}" y1="${down}" x2="${X1}" y2="${down}"/>`);
        p.push(`<rect class="jump" x="0" y="${up - 4.5}" width="9" height="9" rx="2"/><text class="lane-strong" x="14" y="${up + 4}">${esc(fit(`L${site.line} ${dirTarget(site, true)}`, 22))}<title>${hint} · Jump</title></text>`);
        p.push(`<rect class="cont" x="0" y="${down - 4.5}" width="9" height="9" rx="2"/><text class="lane-strong" x="14" y="${down + 4}">${esc(fit(dirTarget(site, false), 22))}<title>${hint} · Continue</title></text>`);
        const points = run.decisions.filter(d => d.pc === site.pc).map(d => x(d.t) + ',' + cellY(d)).join(' ');
        if (points) p.push(`<polyline class="conn" clip-path="url(#dl-plot)" points="${points}"/>`);
      });
      if (!inputSites.length) p.push(`<text class="lane-label" x="${X0 + 6}" y="${yRow(0) + 4}">No branch in the recorded runs depends on a feedback word.</text>`);
      for (const s of sources) {
        if (s.t1 < v0 || s.t0 > v1) continue;
        const a = Math.max(X0, x(s.t0)), b = Math.min(X1, x(s.t1));
        p.push(`<line class="src" x1="${a}" y1="86" x2="${b}" y2="86"/>`);
        if (sel && inside(sel.t)) {
          const cx = x(sel.t), cy = cellY(sel), mx = r1((a + b) / 2);
          if (mx < cx - 14) p.push(`<path class="arc" d="M${mx} 87 Q${mx} ${cy} ${cx - half - 4} ${cy}"/>`);
        }
      }
      if (sel && inside(sel.t)) p.push(`<line class="selline" x1="${x(sel.t)}" y1="18" x2="${x(sel.t)}" y2="${H - 2}"/>`);
      for (const d of run.decisions) {
        if (!inside(d.t)) continue;
        const cx = x(d.t), cy = cellY(d), chosen = sel && d.n === sel.n, site = model.sites.get(d.pc);
        const ring = half + 2.5 + (d.forced ? 3 : 0);
        const title = `Decision ${d.n + 1} · L${site.line} · ${dirName(d.taken)} · ${dirTarget(site, d.taken)} · ${predicate(state.runId, d).text}${d.forced ? ' · forced (assumed)' : d.calc == null ? ' · assumed' : ''} · RT ${clock(d.t)}`;
        p.push(`<g data-action="select" data-value="${d.n}" role="button" aria-label="${esc(title)}"><title>${esc(title)}</title><rect x="${cx - 13}" y="${cy - off}" width="26" height="${2 * off}" fill="transparent"/>` +
          (d.forced ? `<rect class="forced" x="${cx - half - 2.5}" y="${cy - half - 2.5}" width="${2 * half + 5}" height="${2 * half + 5}" rx="${half / 2 + 1.5}"/>` : '') +
          (chosen ? `<rect class="selring" x="${cx - ring}" y="${cy - ring}" width="${2 * ring}" height="${2 * ring}" rx="${ring / 2}"/>` : '') +
          `<rect class="cell ${d.taken ? 'jump' : 'cont'}${d.calc == null && !d.forced ? ' unknown' : ''}" x="${cx - half}" y="${cy - half}" width="${2 * half}" height="${2 * half}" rx="${half / 2}"/></g>`);
      }
      minorSites.forEach((site, row) => {
        const y = yMinor + row * 13;
        p.push(`<text class="lane-label" x="0" y="${y + 4}">L${site.line} · ${CLASS[site.cls][0]}<title>${esc(site.text)}</title></text>`);
        for (const m of run.minor) {
          if (m.pc !== site.pc || !inside(m.t)) continue;
          p.push(`<circle class="minor${m.taken ? ' on' : ''}" cx="${x(m.t)}" cy="${y}" r="3"><title>L${site.line} ${esc(site.text)} · ${dirName(m.taken)} · occurrence ${m.k + 1} · RT ${clock(m.t)}</title></circle>`);
        }
      });

      // Register values, one shared axis per band.
      let top = yState;
      const from = Math.max(v0, 0), to = Math.min(v1, run.rtEnd);
      for (const band of bands) {
        const y0 = top + 8, y1 = top + band.h - 6;
        const sets = band.regs.map((r, k) => {
          const points = lanePoints(state.runId, r), other = ghost && sel && sel.t <= to ? lanePoints(ghost.id, r) : null;
          return { r, color: seriesColor(r, k), points, alt: other && !sameBetween(points, other, Math.max(from, sel.t), to) ? other : null };
        });
        let lo = Infinity, hi = -Infinity;
        const feed = (points, a, b) => {
          let last = null;
          for (const pt of points) {
            if (pt.t <= a) { last = pt.v; continue; }
            if (pt.t > b) break;
            if (last != null) { lo = Math.min(lo, last); hi = Math.max(hi, last); last = null; }
            lo = Math.min(lo, pt.v); hi = Math.max(hi, pt.v);
          }
          if (last != null) { lo = Math.min(lo, last); hi = Math.max(hi, last); }
        };
        for (const set of sets) {
          feed(set.points, state.yFit ? from : -Infinity, state.yFit ? to : Infinity);
          if (set.alt) feed(set.alt, state.yFit ? Math.max(from, sel.t - 1e-6) : -Infinity, state.yFit ? to : Infinity);
        }
        p.push(`<text class="lane-label" x="0" y="${y0 + 4}">${esc(band.title)}</text>`);
        band.regs.forEach((r, k) => {
          const arms = model.footprint().get(r), suffix = band.key !== 'footprint' || !arms ? '' : arms.taken && !arms.fall ? ' · Jump' : arms.fall && !arms.taken ? ' · Continue' : '';
          p.push(`<line x1="0" y1="${y0 + 16 + k * 15}" x2="12" y2="${y0 + 16 + k * 15}" style="stroke:${sets[k].color}" stroke-width="2"/><text class="lane-strong" x="17" y="${y0 + 20 + k * 15}">${reg(r)}${suffix}</text>`);
        });
        if (lo === Infinity) { top += band.h + 10; continue; }
        if (lo === hi) { lo -= 1; hi += 1; }
        const pad = (hi - lo) * 0.09, dLo = lo - pad, dHi = hi + pad;
        const y = v => r1(y1 - (v - dLo) / (dHi - dLo) * (y1 - y0));
        const ticks = valueTicks(lo, hi, band.h > 100 ? 4 : 3), labels = ticks.length ? axisText(ticks) : [];
        ticks.forEach((v, k) => p.push(`<line class="grid" x1="${X0}" y1="${y(v)}" x2="${X1}" y2="${y(v)}"/><text class="tick" x="${X0 - 8}" y="${y(v) + 4}" text-anchor="end">${labels[k]}</text>`));
        const stepPath = (points, a, b, strict) => {
          let cur = null, i = 0, d = '';
          while (i < points.length && (strict ? points[i].t < a : points[i].t <= a)) cur = points[i++].v;
          if (cur != null) d = `M${x(a)} ${y(cur)}`;
          for (; i < points.length && points[i].t <= b; i++) {
            d += d ? ` H${x(points[i].t)} V${y(points[i].v)}` : `M${x(points[i].t)} ${y(points[i].v)}`;
            cur = points[i].v;
          }
          return { d: d ? d + ` H${x(b)}` : '', end: cur };
        };
        const g = [], ends = [];
        // When the arms write several registers, tint the range they span at each moment.
        if (band.key === 'footprint' && sets.length >= 2) {
          const cuts = [...new Set([from, to, ...sets.flatMap(set => set.points.filter(pt => pt.t > from && pt.t < to).map(pt => pt.t))])].sort((a, b) => a - b);
          for (let k = 0; k + 1 < cuts.length; k++) {
            const values = sets.map(set => laneValue(set.points, cuts[k])).filter(v => v != null);
            if (values.length < 2) continue;
            const a = Math.min(...values), b = Math.max(...values);
            g.push(`<rect class="wash" x="${x(cuts[k])}" y="${Math.min(y(a), y(b))}" width="${r1(x(cuts[k + 1]) - x(cuts[k]))}" height="${r1(Math.abs(y(a) - y(b)))}"/>`);
          }
        }
        if (to > from) for (const set of sets) {
          if (set.alt) {
            const path = stepPath(set.alt, Math.max(from, sel.t), to, sel.t >= from);
            if (path.d) { g.push(`<path class="ghost" d="${path.d}" style="stroke:${set.color}"/>`); ends.push({ y: y(path.end), text: `${W < NARROW ? 'other' : 'other arm'} ${num(path.end)}`, color: set.color, alt: true }); }
          }
          const path = stepPath(set.points, from, to, false);
          if (path.d) { g.push(`<path class="series" d="${path.d}" style="stroke:${set.color}"/>`); ends.push({ y: y(path.end), text: `${reg(set.r)} ${num(path.end)}`, color: set.color, alt: false }); }
        }
        for (const set of sets) {
          const shown = set.points.filter(pt => pt.t >= from && pt.t <= to && pt.step >= 0);
          if (shown.length > 140) continue;
          for (const pt of shown) {
            const k = set.points.indexOf(pt), prev = k > 0 ? set.points[k - 1].v : null;
            g.push(`<circle class="dot" cx="${x(pt.t)}" cy="${y(pt.v)}" r="4" style="fill:${set.color}"><title>${reg(set.r)} ${num(prev)} → ${num(pt.v)} · L${stepLine(run, pt.step)} ${esc(stepText(run, pt.step))} · RT ${clock(pt.t)}</title></circle>`);
          }
        }
        p.push(`<g clip-path="url(#dl-plot)">${g.join('')}</g>`);
        // End labels in the right gutter; converging lines get leader lines instead of stacked text.
        ends.sort((a, b) => a.y - b.y);
        const placed = ends.map(e => e.y);
        for (let k = 1; k < placed.length; k++) if (placed[k] - placed[k - 1] < 13) placed[k] = placed[k - 1] + 13;
        const overflow = placed.length ? Math.max(0, placed[placed.length - 1] - (y1 + 4)) : 0;
        ends.forEach((e, k) => {
          const ly = r1(Math.max(y0 + 2, placed[k] - overflow)), ex = x(to);
          p.push(`<path class="leader" d="M${ex + 3} ${e.y} L${X1 + 10} ${ly}"/><line x1="${X1 + 12}" y1="${ly}" x2="${X1 + 24}" y2="${ly}" style="stroke:${e.color}" stroke-width="${e.alt ? 1.5 : 2}"${e.alt ? ' stroke-dasharray="4 3"' : ''}/><text class="end${e.alt ? ' alt' : ''}" x="${X1 + 29}" y="${ly + 4}">${esc(e.text)}</text>`);
        });
        top += band.h + 10;
      }
      p.push(`<line class="xhair" id="dl-xhair" x1="-9" y1="18" x2="-9" y2="${H - 2}"/>`);
      return { svg: `<svg id="dl-lanes-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="group" aria-label="Loops, RT instructions, decisions and register values on the RT clock">${p.join('')}</svg>`, geo: { X0, X1, H } };
    }

    // ---- stacked runs ----------------------------------------------------------------------------------------
    function stackRows() {
      const list = model.scenarioRuns(state.scenario), rows = [{ head: 'Computed from the inputs' }, { id: list.base, label: 'Base run' }];
      const all = list.flips.filter(isDecisionFlip);
      // Keep the open run and the selected decision's other arm in view when the list is cut short.
      const sel = selDecision(), partnerId = sel ? model.partner(state.runId, sel.step) : null;
      const shown = all.slice(0, STACK_FLIP_ROWS);
      for (const id of [state.runId, partnerId]) if (id && all.includes(id) && !shown.includes(id)) shown.push(id);
      if (shown.length) rows.push({ head: 'One decision forced the other way' });
      for (const id of shown) rows.push({ id, label: runLabel(id) });
      if (all.length > shown.length) rows.push({ note: `${plural(all.length - shown.length, 'more run')} in the Run list` });
      return rows;
    }
    function stackSvg(W) {
      const cur = R(), [v0, v1] = state.view, span = v1 - v0;
      const X0 = GL, X1 = Math.max(GL + 260, W - gutterRight(W)), PW = X1 - X0;
      // The small range bar next to each run's final values needs the wide gutter.
      const bars = W >= NARROW, valueX = X1 + (bars ? 86 : 16);
      const x = t => r1(X0 + (t - v0) / span * PW);
      const fp = [...model.footprint().keys()], rows = stackRows(), sel = selDecision();
      const partnerId = sel ? model.partner(state.runId, sel.step) : null;
      let lo = Infinity, hi = -Infinity;
      for (const row of rows) if (row.id) for (const r of fp) { const v = model.run(row.id).finals[r]; if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }
      const ox = v => r1(X1 + 16 + (hi > lo ? (v - lo) / (hi - lo) : 0.5) * 58);
      // Decisions of different sites can share an RT clock; give each site its own small column.
      const order = [...model.sites.values()].filter(s => s.cls === 'input').sort((a, b) => a.line - b.line).map(s => s.pc);
      const dx = pc => (order.indexOf(pc) - (order.length - 1) / 2) * 10;
      const p = [];
      let y = 2, firstHead = true;
      for (const row of rows) {
        if (row.head) {
          p.push(`<text class="head" x="0" y="${y + 15}">${esc(row.head)}</text>`);
          if (firstHead && fp.length) p.push(`<text class="lane-label" x="${X1 + 16}" y="${y + 15}">${cur.truncated ? 'When stopped:' : 'Final'} ${fp.map(reg).join(', ')}</text>`);
          else if (firstHead && cur.pathSignature != null) p.push(`<text class="lane-label" x="${X1 + 16}" y="${y + 15}">Path</text>`);
          firstHead = false;
          y += 23;
          continue;
        }
        if (row.note) { p.push(`<text class="lane-label" x="6" y="${y + 13}">${esc(row.note)}</text>`); y += 18; continue; }
        const run = model.run(row.id), isCur = row.id === state.runId, vals = fp.map(r => run.finals[r]).filter(v => v != null);
        const label = row.label + (row.id === partnerId ? ' · other arm' : '');
        if (isCur) p.push(`<rect class="rowbg" x="0" y="${y}" width="${W}" height="18" rx="3"/>`);
        p.push(`<text class="${isCur ? 'lane-strong' : 'lane-label'}" x="6" y="${y + 13}">${esc(label)}</text>`);
        for (const d of run.decisions) {
          if (d.t < v0 || d.t > v1) continue;
          const ref = cur.decisions[d.n], differs = !ref || ref.pc !== d.pc || ref.taken !== d.taken, cy = y + (d.taken ? 2 : 7), cx = r1(x(d.t) + dx(d.pc));
          p.push(`<rect class="${d.taken ? 'jump' : 'cont'}${isCur || differs ? '' : ' same'}" x="${cx - 4.5}" y="${cy}" width="9" height="9" rx="2"/>`);
          if (d.forced) p.push(`<rect class="forced" x="${cx - 7}" y="${cy - 2.5}" width="14" height="14" rx="3.5"/>`);
        }
        if (vals.length) {
          const a = ox(Math.min(...vals)), b = ox(Math.max(...vals));
          if (bars) p.push(`<rect class="outcome" x="${r1(a - 1.5)}" y="${y + 7}" width="${r1(Math.max(3, b - a + 3))}" height="4" rx="2"/>`);
          p.push(`<text class="end" x="${valueX}" y="${y + 13}">${vals.map(num).join(', ')}</text>`);
        } else if (run.pathSignature != null) {
          // No register outcome to show: a run without a loop is summed up by the path it took.
          p.push(`<text class="end" x="${X1 + 16}" y="${y + 13}">${esc(fit(pathText(run.pathLabel), 24))}</text>`);
        }
        const title = `${label} · ${run.decisions.map(d => d.taken ? 'J' : 'C').join('')}${fp.length ? ` · ${run.truncated ? 'when stopped' : 'final'} ` + fp.map(r => reg(r) + ' ' + num(run.finals[r])).join(', ') : ''}`;
        p.push(`<rect class="rowhit" data-action="open-run" data-value="${esc(row.id)}" x="0" y="${y}" width="${W}" height="18" role="button" tabindex="0" aria-label="${esc(title)}"><title>${esc(title)}</title></rect>`);
        y += 18;
      }
      const H = y + 4, grid = [];
      const tt = timeTicks(v0, v1, Math.max(3, Math.floor(PW / 120)));
      for (const t of tt.ticks) grid.push(`<line class="grid" x1="${x(t)}" y1="0" x2="${x(t)}" y2="${H}"/>`);
      if (sel && sel.t >= v0 && sel.t <= v1) grid.push(`<line class="selline" x1="${x(sel.t)}" y1="0" x2="${x(sel.t)}" y2="${H}"/>`);
      const here = fp.map(r => cur.finals[r]).filter(v => v != null);
      if (bars && here.length) grid.push(`<line class="grid" x1="${ox((Math.min(...here) + Math.max(...here)) / 2)}" y1="22" x2="${ox((Math.min(...here) + Math.max(...here)) / 2)}" y2="${H}"/>`);
      return `<svg id="dl-stack-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="group" aria-label="Recorded runs on the same time axis">${grid.join('')}${p.join('')}</svg>`;
    }

    // ---- controls, chips ---------------------------------------------------------------------------------------
    function inputsText() {
      const inputs = data.inputs;
      if (inputs.usesSchedule) return `Feedback from the project LINQ schedule · ${plural(inputs.pops, 'word')} read`;
      if (!inputs.pops) return 'The run reads no feedback word.';
      if (!inputs.feedback) return `${plural(inputs.pops, 'feedback word')} read with unknown values`;
      const list = inputs.feedback, shown = list.slice(0, 6).map(num).join(', ') + (list.length > 6 ? ', …' : '');
      return `${plural(inputs.pops, 'feedback word')} read: ${shown}${list.length > 1 && inputs.pops > list.length ? ' (repeating)' : ''}`;
    }
    function controlsHtml() {
      const list = model.scenarioRuns(state.scenario), raw = data.runs[state.runId], run = R();
      const option = id => `<option value="${esc(id)}"${id === state.runId ? ' selected' : ''}>${esc(runLabel(id))}</option>`;
      const decisionFlips = list.flips.filter(isDecisionFlip), otherFlips = list.flips.filter(id => !isDecisionFlip(id));
      const group = (label, ids) => ids.length ? `<optgroup label="${label}">${ids.map(option).join('')}</optgroup>` : '';
      const sequencers = data.sequencers || [];
      const picker = sequencers.length > 1 ? `<label>Sequencer<select id="dl-sequencer">${sequencers.map(s => `<option value="${esc(s.id)}"${s.id === data.sequencer ? ' selected' : ''}>${esc(s.id)}</option>`).join('')}</select></label>` : '';
      const source = data.inputs.scheduled ? `<label>Feedback source<select id="dl-feedback-source"><option value="schedule"${state.source === 'schedule' ? ' selected' : ''}>Project LINQ schedule</option><option value="values"${state.source === 'values' ? ' selected' : ''}>Typed words</option></select></label>` : '';
      const history = (env.history || []).map(item => `<option value="${esc(item)}"></option>`).join('');
      const words = `<label class="words"><span>Feedback words<span class="hint"> · read by fb_pop_data in order, repeating</span></span><span class="field"><input id="dl-feedback" type="text" spellcheck="false" autocomplete="off" list="dl-feedback-history" value="${esc(state.draft)}" placeholder="0"${state.source === 'schedule' ? ' disabled' : ''} aria-invalid="${state.inputError ? 'true' : 'false'}"><datalist id="dl-feedback-history">${history}</datalist><button data-action="apply-inputs">Apply</button></span>${state.inputError ? `<span class="input-error" role="alert">${esc(state.inputError)}</span>` : ''}</label>`;
      const pills = [raw.kind === 'base' ? '<span class="pill">Computed from the inputs</span>' : `<span class="pill warn">${esc(runLabel(state.runId))} · assumed</span>`];
      if (run.truncated) pills.push(`<span class="pill warn" title="${esc(run.reason || '')}">Partial · ${esc(STATUS_TEXT[run.status] || run.status)}</span>`);
      const progress = flipProgress();
      return picker + source + words +
        `<label>Run<select id="dl-run">${group('Computed from the inputs', [list.base])}${group('One decision forced the other way', decisionFlips)}${group('Another input-dependent branch forced', otherFlips)}</select></label>` +
        `<div class="status">${pills.join('')}<span>${esc(inputsText())}</span>${progress ? `<span class="progress">${esc(progress)}</span>` : ''}</div><span class="grow"></span>` +
        (raw.kind === 'base' ? '' : '<button data-action="restore">Base run ↶</button>') +
        '<button data-action="refresh" title="Read the saved source and record again">Refresh</button>';
    }
    function notesText() {
      const engine = data.engineModel || {}, parts = [];
      if (data.inputs.pops && !data.inputs.usesSchedule && data.inputs.feedback) parts.push('Feedback words are assumed values, already received when read (no additional wait).');
      parts.push(...(engine.assumptions || []));
      const run = R();
      if (run.truncated && run.reason) parts.push(`This run is partial: ${run.reason}`);
      return parts.join(' · ');
    }
    function foldText() {
      // Passes grouped by the path they took (the model names the paths).
      const run = R(), groups = new Map();
      for (const pass of run.passes) {
        if (pass.kind !== 'iter') continue;
        const group = groups.get(pass.signature) || { letter: pass.letter, name: pass.name, signature: pass.signature, n: 0 };
        group.n++;
        groups.set(pass.signature, group);
      }
      const describe = signature => signature.split(' ').map(key => { const [pc, taken] = key.split(':').map(Number), site = model.sites.get(pc); return site && site.cls === 'input' ? `L${site.line} ${dirName(!!taken)}` : null; }).filter(Boolean).join(', ') || 'no decision';
      // A program without a loop takes one path per run.
      if (run.pathSignature != null) return `Path ${pathText(run.pathLabel)} (${describe(run.pathSignature)})`;
      const byFirstSeen = (a, b) => a.letter.length - b.letter.length || a.letter.localeCompare(b.letter);
      const all = [...groups.values()], total = all.reduce((n, g) => n + g.n, 0);
      // Up to four paths are spelled out; with more, the four most frequent and a count of the rest.
      const shown = all.length <= 4 ? all.sort(byFirstSeen) : all.sort((a, b) => b.n - a.n || byFirstSeen(a, b)).slice(0, 4);
      const rest = all.length - shown.length, parts = shown.map(g => `${pathText(g.name)} × ${g.n} (${describe(g.signature)})`);
      if (rest > 0) parts.push(`${plural(rest, 'other path')} × ${total - shown.reduce((n, g) => n + g.n, 0)}`);
      return parts.length ? `${plural(total, 'loop pass', 'loop passes')} = ${parts.join(' + ')}` : '';
    }
    function chipsHtml() {
      const run = R(), fp = [...model.footprint().keys()], ops = model.operandRegisters();
      const taken = new Set([...fp, ...ops, ...run.rtRead]), others = run.changing.filter(r => !taken.has(r));
      const toggle = r => `<button data-action="toggle-reg" data-value="${r}" class="${state.show.regs.includes(r) ? 'on' : ''}" aria-pressed="${state.show.regs.includes(r)}">${reg(r)}</button>`;
      return `<span class="group">${fp.length ? 'Show more on the state lane' : 'The arms write no register that is read later, so the state lane is empty. Show'}</span>` +
        (ops.length ? `<span class="group">Compared values<button data-action="toggle-operands" class="${state.show.operands ? 'on' : ''}" aria-pressed="${state.show.operands}">${ops.map(reg).join(' · ')}</button></span>` : '') +
        (run.rtRead.length ? `<span class="group">Read by RT instructions${run.rtRead.map(toggle).join('')}</span>` : '') +
        `<span class="group">Other changing registers${state.show.regs.filter(r => others.includes(r)).map(toggle).join('')}<select id="dl-add-reg" aria-label="Add a register"><option value="">Add…</option>${others.filter(r => !state.show.regs.includes(r)).map(r => `<option value="${r}">${reg(r)}</option>`).join('')}</select></span>`;
    }

    // ---- the selected decision -------------------------------------------------------------------------------------
    function detailHtml() {
      const d = selDecision(), run = R(), S = run.steps;
      if (!d) {
        const why = !data.inputs.pops ? 'The run reads no feedback word, so no branch can depend on one.'
          : 'No conditional branch in this run compares a value derived from a feedback word and then does something else.';
        // A run that stopped early may simply not have reached its decisions yet.
        const partial = run.truncated ? `<p class="verdict">The run stopped early${run.reason ? ': ' + esc(run.reason) : '.'}</p>` : '';
        return `<section class="card dcard empty"><h3>No input decision in this run</h3>${partial}<p class="note">${why} The branch table below lists every conditional branch that ran.</p></section>`;
      }
      const site = model.sites.get(d.pc), pr = predicate(state.runId, d), w = pr.w, pass = run.passes[d.pass];
      const diff = model.armDiff(state.runId, d.step), cf = model.counterfactual(state.runId, d.step), first = model.firstRtDifference(state.runId, d.step);
      const dirPill = taken => `<span class="pill dir"><i class="sw ${taken ? 'jump' : 'cont'}"></i>${dirName(taken)} · ${esc(dirTarget(site, taken))}</span>`;
      const chip = (i, cls) => `<button${cls ? ` class="${cls}"` : ''} data-action="focus-step" data-value="${i}" title="Instruction #${i + 1} · Q1 ${clock(S[i][Q0])} · RT ${clock(S[i][T0])}">L${stepLine(run, i)} ${esc(stepText(run, i))}</button>`;

      const assumed = d.forced ? '<span class="pill warn">forced (assumed)</span>' : d.calc == null ? '<span class="pill warn">unknown value (assumed)</span>' : '';
      const head = `<div class="dhead"><h2>Decision ${d.n + 1} / ${run.decisions.length}</h2><span class="where">L${site.line} ${esc(site.text)} · ${pass ? `pass ${pass.index + 1} · path ${esc(pathText(pass.name))} · ` : run.pathSignature != null ? `path ${esc(pathText(run.pathLabel))} · ` : ''}RT ${clock(d.t)} · Q1 ${clock(d.q)}</span>${dirPill(d.taken)}${assumed}<div class="toolbar"><button data-action="prev"${d.n === 0 ? ' disabled' : ''}>← Previous</button><button data-action="next"${d.n === run.decisions.length - 1 ? ' disabled' : ''}>Next →</button></div></div>`;

      const choice = d.calc == null ? (d.forced ? `The compared value is unknown; this run forces ${dirName(d.taken)} as an assumption.` : 'The compared value is unknown, so Playback assumed Continue.')
        : !d.forced ? `The condition gives ${dirName(d.taken)}.`
        : d.taken === d.calc ? `The condition also gives ${dirName(d.calc)}; this run fixes the same direction as an assumption.`
        : `The condition gives ${dirName(d.calc)}; this run forces ${dirName(d.taken)} as an assumption. The compared values are unchanged.`;
      let whyCard;
      if (!w) {
        whyCard = `<section class="card dcard"><h3>Why this way</h3><p class="verdict">${esc(pr.text)}</p><p class="note">${choice}</p></section>`;
      } else {
        // A sign test shows the one register as a signed value; other compares show both operands as compared.
        const shown = w.signTest ? [w.operands[0]] : w.operands, display = o => w.signTest ? o.signed : o.value;
        const scale = Math.max(1, ...shown.map(o => Math.abs(display(o) ?? 0)));
        const wraps = w.unsigned && !w.signTest && w.operands.some(o => o.signed != null && o.signed !== o.value);
        const sameMargin = run.decisions.length > 1 && run.decisions.every(other => predicate(state.runId, other).ratio === pr.ratio);
        const provenance = w.operands.filter(o => o.reg != null).map(o => {
          const links = o.chain.slice(0, 8).map(i => chip(i)), hidden = o.chain.length - Math.min(8, o.chain.length);
          const pops = o.sources.flatMap(s => s.pops);
          if (hidden > 0 || o.truncated) links.push(`<span class="arrow">… ${hidden}${o.truncated ? ' or more' : ''} more</span>`);
          if (pops.length) links.push(pops.map(pop => `<button class="input" data-action="focus-step" data-value="${pop.step}" title="External input · instruction #${pop.step + 1}">L${stepLine(run, pop.step)} ${esc(stepText(run, pop.step))} = ${num(pop.value)}</button>`).join(''));
          const chain = links.join('<span class="arrow">←</span>');
          const pairing = rule => rule === 'order' ? ['paired by order and count', 'The record has no link between an acquisition and the feedback words. The words read after a group of acquisitions were shared out among them in order.']
            : ['paired with the previous acquire', 'The record has no link between an acquisition and the feedback words; they were paired with the most recent acquire.'];
          const measured = o.sources.filter(s => s.acquire != null).map(s => `<div class="acq-line"><span><i class="sw tri"></i>L${stepLine(run, s.acquire)} <span class="mono">${esc(stepText(run, s.acquire))}</span> · RT ${clock(s.t0)} – ${clock(s.t1)}</span>${s.settings.map(setting => `<span class="mono">${esc(setting.text)} = ${setting.reads.map(r => num(r[1])).join(', ')}</span>`).join('')}<span class="tag" title="${pairing(s.rule)[1]}">${pairing(s.rule)[0]}</span><button data-action="zoom-window" data-a="${s.t0}" data-b="${s.t1}">Show this window</button></div>`).join('');
          return `<div class="prov"><div class="prov-head">${esc(o.label)} = ${num(o.signed ?? o.value)}</div><div class="chain">${chain || '<span class="arrow">no instruction in this run wrote it</span>'}</div>${measured}</div>`;
        }).join('');
        whyCard = `<section class="card dcard"><h3>Why this way <small>L${stepLine(run, w.producer)} ${esc(fit(stepText(run, w.producer), 34))}</small></h3>` +
          `<div class="pred">${shown.map(o => `<span>${esc(o.label)}</span><span class="bar"><i style="width:${Math.round(Math.abs(display(o) ?? 0) / scale * 100)}%"></i></span><span class="val">${num(display(o))}</span>`).join('')}</div>` +
          `<p class="verdict">${esc(pr.text)}</p><p class="note">${choice}</p>` +
          (w.signTest ? `<p class="note">Sign-bit test: the unsigned word ${num(w.operands[0].value)} is compared with ${num(w.operands[1].value)}.</p>` : '') +
          (wraps ? '<p class="note">Compared as unsigned 32-bit words: a negative register reads as a large positive number.</p>' : '') +
          (pr.ratio == null ? '' : `<p class="note" title="The difference divided by the larger of the two values. The smaller it is, the less the input has to change to flip the direction.">Margin ${Math.round(pr.ratio * 100)}% · difference ${num(pr.diff)}${sameMargin ? ' · the same for every decision in this run' : ''}</p>`) +
          `<h4>Where the values come from</h4>${provenance || '<p class="note">No value was read from a register.</p>'}</section>`;
      }

      const key = flipKey(state.runId, d.step), failed = flips.failed.get(key);
      const missing = failed ? `Recording the other arm failed: ${esc(failed)}`
        : flips.inflight || flips.auto ? 'The other arm of this decision is being recorded…'
        : 'The other arm of this decision has not been recorded.';
      const recordButton = failed || flips.inflight || flips.auto ? '' : ' <button data-action="record-flip">Record it</button>';
      let armCard;
      if (diff.mine) {
        const joinLabel = model.labelAt.get(diff.mine.joinPc), when = diff.mine.atEnd ? 'at the end' : 'at the join';
        const joinText = diff.mine.open ? 'not reached in this record' : diff.mine.atEnd ? 'end of program · the arms never rejoin' : `${joinLabel ? '@' + esc(joinLabel) + ' ' : ''}(L${model.ins.get(diff.mine.joinPc).line})`;
        const listing = (arm, view) => arm.steps.slice(0, 6).map(i => `L${stepLine(view, i)} ${esc(stepText(view, i))}`).concat(arm.steps.length > 6 ? [`… ${arm.steps.length - 6} more`] : []).join('<br>') || 'none';
        const columns = d.taken ? [diff.mine, diff.other] : [diff.other, diff.mine];
        const owner = arm => arm === diff.mine ? run : model.run(diff.otherId);
        const who = arm => arm === diff.mine ? 'this run' : 'recorded: ' + runLabel(diff.otherId);
        const cell = fn => columns.map(arm => `<td>${arm ? fn(arm) : '<span class="who">no recorded other arm</span>'}</td>`).join('');
        const stateRows = diff.state.map(e => `<tr class="differs"><th>${reg(e.reg)} ${when}</th>${columns.map(arm => `<td>${arm ? num(arm === diff.mine ? e.mine : e.other) + (((arm === diff.mine ? e.mine : e.other) !== e.before) ? `<span class="who">was ${num(e.before)}</span>` : '<span class="who">unchanged</span>') : ''}</td>`).join('')}</tr>`).join('');
        armCard = `<section class="card dcard"><h3>What the arms change <small>join ${joinText}</small></h3>` +
          (diff.cls ? `<span class="badge">${DIFF[diff.cls][0]}</span>` : '<span class="badge">No recorded run to compare</span>') +
          `<table class="cmp"><thead><tr><th></th>${[true, false].map((taken, k) => `<th>${dirName(taken)} · ${esc(dirTarget(site, taken))}${columns[k] ? `<span class="who">${esc(who(columns[k]))}</span>` : ''}</th>`).join('')}</tr></thead><tbody>` +
          `<tr><th>RT time</th>${cell(arm => clock(arm.rt))}</tr><tr><th>Q1 time</th>${cell(arm => clock(arm.q1))}</tr>` +
          `<tr><th>RT instructions</th>${cell(arm => arm.rtVisible.length ? arm.rtVisible.map(esc).join('<br>') : 'none')}</tr>` +
          `<tr><th>Instructions run</th>${cell(arm => listing(arm, owner(arm)))}</tr>${stateRows}</tbody></table>` +
          `<p class="note">${diff.cls ? DIFF[diff.cls][1] : missing + recordButton}</p></section>`;
      } else {
        armCard = '<section class="card dcard"><h3>What the arms change</h3><p class="note">The join of this branch was not found in the record.</p></section>';
      }

      let otherCard;
      if (cf) {
        const P = model.run(cf.otherId), fp = [...model.footprint().keys()];
        const finals = view => fp.map(r => num(view.finals[r])).join(', ');
        let firstText;
        if (!first) firstText = '<dd>none<span>All later RT instructions and the values they read are the same.</span></dd>';
        else if (first.mine == null || first.other == null) firstText = `<dd>${KIND_TEXT.length}</dd>`;
        else {
          const a = S[first.mine], b = P.steps[first.other];
          const values = step => step[READS].length ? step[READS].map(r => `${reg(r[0])} = ${num(r[1])}`).join(', ') : model.textOf(step);
          firstText = `<dd><button class="mono" data-action="focus-step" data-value="${first.mine}">L${stepLine(run, first.mine)} ${esc(stepText(run, first.mine))}</button><span>${KIND_TEXT[first.kind]} · RT ${clock(a[T0])}</span><span>this run: ${esc(values(a))}</span><span>other arm: ${esc(values(b))}</span></dd>`;
        }
        otherCard = `<section class="card dcard"><h3>If it had gone the other way <small>${esc(runLabel(cf.otherId))}</small></h3><dl class="facts">` +
          `<dt>First RT difference</dt>${firstText}` +
          `<dt>Later decisions</dt><dd>${cf.differ} of ${cf.later} change</dd>` +
          (fp.length ? `<dt>${fp.map(reg).join(', ')} ${run.truncated ? 'when stopped' : 'at the end'}</dt><dd>${finals(run)}<span>other arm: ${finals(P)}</span></dd>` : '') +
          `<dt>RT end</dt><dd>${clock(cf.rtEnd[0])}<span>other arm: ${clock(cf.rtEnd[1])}</span></dd>` +
          `<dt>Q1 end</dt><dd>${clock(cf.q1End[0])}<span>other arm: ${clock(cf.q1End[1])}</span></dd></dl>` +
          `<div class="row-actions"><label><input type="checkbox" id="dl-ghost"${state.ghost ? ' checked' : ''}> Overlay on the timeline as dashed lines</label><button data-action="open-run" data-value="${esc(cf.otherId)}">Open this run</button></div></section>`;
      } else {
        otherCard = `<section class="card dcard"><h3>If it had gone the other way</h3><p class="note">${missing}${recordButton}</p></section>`;
      }
      return head + `<div class="detail">${whyCard}${armCard}${otherCard}</div>`;
    }

    // ---- branch sites, source, table ------------------------------------------------------------------------------------
    function sitesHtml() {
      const run = R(), sel = selDecision(), byStep = new Map(run.decisions.map(d => [d.step, d]));
      const rows = [...model.sites.values()].sort((a, b) => a.line - b.line).map(site => {
        const c = run.counts.get(site.pc) || { taken: 0, not: 0, steps: [] };
        let record;
        if (site.cls === 'counted') {
          const loop = run.loops.find(l => l.header === site.target), per = loop ? loop.instances[0].passes.length : 0;
          record = !loop ? '' : `${loop.instances.every(it => it.passes.length === per) ? '×' + per : 'varying length'}, entered ${plural(loop.instances.length, 'time')}`;
        } else {
          const marks = c.steps.slice(0, 400).map((i, k) => {
            const d = byStep.get(i), taken = run.steps[i][BR][TAKEN];
            const classes = [taken ? 'jump' : 'cont', d && d.forced ? 'forced' : '', d && sel && d.n === sel.n ? 'sel' : ''].filter(Boolean).join(' ');
            return `<button class="${classes}" data-action="${d ? 'select' : 'focus-step'}" data-value="${d ? d.n : i}" title="Occurrence ${k + 1} · ${dirName(taken)} · RT ${clock(run.steps[i][T0])}" aria-label="Occurrence ${k + 1} ${dirName(taken)}"><i></i></button>`;
          }).join('');
          record = `<div class="bc${site.cls === 'input' ? '' : ' quiet'}">${marks}</div>${c.steps.length > 400 ? `<span class="who">first 400 of ${num(c.steps.length)}</span>` : ''}`;
        }
        const joinLabel = site.join === model.END ? null : model.labelAt.get(site.join);
        const join = site.join === model.END ? 'end of program' : `${joinLabel ? '@' + joinLabel + ' ' : ''}L${model.ins.get(site.join).line}`;
        // Where recorded pairs first coincide can differ from the join when an arm holds further branches.
        const doubt = site.pairs && (site.pairAgreement === false || site.pairJoin !== site.join)
          ? ` <span class="tag" title="${esc(`In ${plural(site.pairs, 'recorded pair')} the two runs first coincide at ${site.pairJoins.map(([pc, n]) => (pc === model.END ? 'the end' : 'L' + model.ins.get(pc).line) + ' ×' + n).join(', ')}. An arm contains another branch; the join from the flow graph is used.`)}">recorded pairs disagree</span>` : '';
        return `<tr><td class="mono"><button class="link" data-action="reveal" data-value="${site.line}" title="Open in the editor">L${site.line}</button></td><td class="mono">${esc(site.text)}</td><td><span class="kind ${site.cls}" title="${esc(CLASS[site.cls][1])}">${CLASS[site.cls][0]}</span></td><td class="mono">${esc(join)}${doubt}</td><td class="mono">Jump ${num(c.taken)} · Continue ${num(c.not)}</td><td>${record}</td></tr>`;
      }).join('');
      return `<div class="card-top"><span><strong>${plural(model.sites.size, 'conditional branch', 'conditional branches')}</strong>&nbsp;&nbsp;grouped by what the condition depends on and what the arms write (recorded data flow)</span></div><div class="table-scroll"><table class="sites"><thead><tr><th>Line</th><th>Instruction</th><th>Kind</th><th>Join</th><th>This run</th><th>Directions (upper = Jump, lower = Continue)</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }
    function sourceHtml() {
      const run = R(), d = selDecision(), focus = state.focusStep != null ? state.focusStep : d ? d.step : 0, s = run.steps[focus];
      const line = model.lineOf(s), lo = Math.max(1, line - 8), hi = Math.min(data.source.length, line + 8);
      const siteByLine = new Map([...model.sites.values()].map(site => [site.line, site]));
      const lines = [];
      for (let n = lo; n <= hi; n++) {
        const site = siteByLine.get(n), text = (data.source[n - 1] || '').replace(/\t/g, '    ').replace(/^ {8}/, '').trimEnd();
        lines.push(`<div class="code-line${n === line ? ' active' : ''}" data-action="reveal" data-value="${n}" title="Open in the editor"><span class="ln">${n}</span>${esc(text) || ' '}${site ? `<span class="kind ${site.cls}">${CLASS[site.cls][0]}</span>` : ''}</div>`);
      }
      let occurrence = 0;
      for (let i = 0; i <= focus; i++) if (run.steps[i][PC] === s[PC]) occurrence++;
      const reads = s[READS].map(r => `${reg(r[0])} = ${num(r[1])}`).join(', '), writes = s[WRITES].map(w => `${reg(w[0])}: ${num(w[1])} → ${num(w[2])}`).join(', ');
      return `<div class="card-top"><span><strong>Q1ASM source</strong>&nbsp;&nbsp;around L${line}</span><span class="toolbar">${state.focusStep != null ? '<button data-action="focus-step" data-value="">Back to the decision ↶</button>' : ''}<button data-action="reveal" data-value="${line}">Open in editor</button></span></div><div class="source">${lines.join('')}</div>` +
        `<div class="position"><b>Instruction #${focus + 1} of ${num(run.steps.length)}</b> · occurrence ${occurrence} of this line · Q1 ${clock(s[Q0])} · RT clock ${clock(s[T0])}${s[T1] > s[T0] ? ' – ' + clock(s[T1]) : ''}<br>Reads ${esc(reads) || 'nothing'} · writes ${esc(writes) || 'nothing'}</div>`;
    }
    function tableHtml() {
      const run = R(), sel = selDecision(), fp = [...model.footprint().keys()];
      const rows = run.decisions.map(d => {
        const pass = run.passes[d.pass], join = run.joinIdx.get(d.step), other = model.partner(state.runId, d.step);
        return `<tr class="${sel && d.n === sel.n ? 'sel' : ''}" data-action="select" data-value="${d.n}" tabindex="0"><td>${d.n + 1}</td><td>${pass ? `${pass.index + 1} · ${esc(pathText(pass.name))}` : run.pathSignature != null ? esc(pathText(run.pathLabel)) : ''}</td><td>${clock(d.t)}</td><td>${dirName(d.taken)}${d.forced ? ' (forced)' : d.calc == null ? ' (assumed)' : ''}</td><td>${d.calc == null ? 'unknown' : dirName(d.calc)}</td><td>${esc(predicate(state.runId, d).text)}</td>` +
          fp.map(r => `<td>${num(run.valueBefore(r, d.step))} → ${join == null ? '?' : num(run.valueBefore(r, join))}</td>`).join('') + `<td>${other ? esc(runLabel(other)) : 'not recorded'}</td></tr>`;
      }).join('');
      return `<summary><strong>Decisions as a table</strong><span>${num(run.decisions.length)} · the same content as the timeline</span></summary><div class="table-scroll"><table class="dtable"><thead><tr><th>#</th><th>Pass · path</th><th>RT clock</th><th>Direction</th><th>Condition gives</th><th>Compared</th>${fp.map(r => `<th>${reg(r)} before → after the join</th>`).join('')}<th>Other arm</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    // ---- hover readout ---------------------------------------------------------------------------------------------------
    function readout(t) {
      const run = R(), sel = selDecision(), rows = [];
      const partnerId = sel ? model.partner(state.runId, sel.step) : null, ghost = state.ghost && partnerId ? partnerId : null;
      const pass = run.passes.find(q => q.kind === 'iter' && t >= q.t0 && t < q.t1), block = run.blocks.find(b => t >= b.t0 && t < b.t1);
      if (pass) rows.push({ k: 'Loop', v: `pass ${pass.index + 1} · path ${pathText(pass.name)}` });
      if (block) rows.push({ k: 'RT instruction', v: (block.op === 'wait' && block.count > 1 ? `wait ×${block.count}` : block.op) + ' · ' + clock(block.t1 - block.t0) });
      for (const band of bandList()) band.regs.forEach((r, k) => {
        const v = laneValue(lanePoints(state.runId, r), t);
        const alt = ghost && t >= sel.t ? laneValue(lanePoints(ghost, r), t) : null;
        rows.push({ k: reg(r), v: num(v) + (alt != null && alt !== v ? ` · other arm ${num(alt)}` : ''), color: seriesColor(r, k) });
      });
      return { head: 'RT ' + clock(Math.max(0, Math.round(t))), rows };
    }

    // ---- recording other arms ---------------------------------------------------------------------------------------------
    // The run that forces one branch occurrence the other way gets the id "<run>~<step>".
    const flipKey = (runId, step) => runId + '~' + step;
    function wantedFlips() {
      const out = [], seen = new Set();
      const add = (runId, step) => {
        const key = flipKey(runId, step);
        if (seen.has(key) || data.runs[key] || flips.failed.has(key) || model.partner(runId, step)) return;
        seen.add(key);
        out.push({ run: runId, step });
      };
      const d = selDecision();
      if (d) add(state.runId, d.step);                      // the selected decision first, in whichever run is open
      if (flips.auto) {
        const base = model.run(baseId), covered = new Set();
        // One recorded arm per direction of every input-dependent branch, so that each site can be
        // classified from both of its arms; then the base run's decisions in order.
        for (const b of base.branches) {
          const key = b.pc + ':' + b.taken;
          if (!b.input || covered.has(key)) continue;
          covered.add(key);
          add(baseId, b.step);
        }
        for (const decision of base.decisions) add(baseId, decision.step);
      }
      return out;
    }
    function flipProgress() {
      if (!flips.inflight) return '';
      const left = wantedFlips().length;
      return left ? `Recording other arms · ${left} left` : '';
    }
    function pumpFlips(force) {
      if (flips.inflight) return;
      let list = wantedFlips();
      if (!flips.auto && !force) list = [];
      if (!list.length) return;
      flips.inflight = true;
      post({ type: 'flips', token: data.token ?? null, requests: list.slice(0, FLIP_BATCH) });
    }
    function addRuns(message) {
      flips.inflight = false;
      const added = Object.entries((message && message.runs) || {}).filter(([id]) => !data.runs[id]);
      for (const [id, run] of added) {
        data.runs[id] = run;
        data.scenarios[0].flips.push(id);
        flips.recorded++;
        flips.steps += run.steps.length;
      }
      for (const item of (message && message.failed) || []) flips.failed.set(flipKey(item.run, item.step), item.reason || 'The run could not be recorded.');
      if (flips.recorded >= AUTO_FLIP_RUNS || flips.steps >= AUTO_FLIP_STEPS) flips.auto = false;
      const before = selDecision(), waiting = !!before && !model.partner(state.runId, before.step);
      if (added.length) rebuild();
      pumpFlips(false);
      // While runs keep arriving the page is redrawn a few times a second at most, so that an open
      // list or a selection in progress is not replaced on every arrival. It is redrawn at once
      // when the selected decision gets its other arm, and when the recording is over.
      const now = selDecision(), arrived = waiting && !!now && !!model.partner(state.runId, now.step);
      renderSoon(arrived || !flips.inflight);
    }
    function rebuild() {
      const d = selDecision(), step = d ? d.step : null;
      model = Model.create(data);
      laneCache.clear();
      predCache.clear();
      if (!data.runs[state.runId]) state.runId = baseId;
      const list = R().decisions, at = step == null ? -1 : list.findIndex(item => item.step === step);
      state.sel = at >= 0 ? at : Math.max(0, Math.min(list.length - 1, state.sel));
      state.view = clampView(state.view[0], state.view[1]);
    }

    // ---- state changes ----------------------------------------------------------------------------------------------------
    function reveal(line) { if (Number.isInteger(line) && line > 0) post({ type: 'reveal', line }); }
    function select(n, user) {
      const run = R();
      if (!run.decisions.length) return;
      state.sel = Math.max(0, Math.min(run.decisions.length - 1, n));
      state.focusStep = null;
      keepVisible(run.decisions[state.sel].t);
      render();
      if (user) reveal(model.sites.get(run.decisions[state.sel].pc).line);
      pumpFlips(true);
    }
    function setRun(id) {
      if (!data.runs[id]) return;
      state.runId = id;
      state.scenario = data.runs[id].scenario;
      state.sel = Math.max(0, Math.min(R().decisions.length - 1, state.sel));
      state.focusStep = null;
      state.view = clampView(state.view[0], state.view[1]);
      render();
      pumpFlips(true);
    }
    function setView(v0, v1) { state.view = clampView(v0, v1); renderLanes(); }
    function applyInputs() {
      if (state.source === 'schedule') { state.inputError = null; return post({ type: 'open', sequencer: data.sequencer, feedback: null }); }
      const parsed = parseFeedback(state.draft);
      state.inputError = parsed.error || null;
      if (parsed.error) { if (!headless) $('controls').innerHTML = controlsHtml(); return undefined; }
      return post({ type: 'open', sequencer: data.sequencer, feedback: parsed.values });
    }

    // ---- rendering -----------------------------------------------------------------------------------------------------------
    let width = 1200, disposed = false, lastRender = 0, renderTimer = 0;
    const REDRAW_INTERVAL_MS = 700;
    function renderSoon(now) {
      if (headless || disposed) return;
      const wait = REDRAW_INTERVAL_MS - (Date.now() - lastRender);
      if (now || wait <= 0) { render(); return; }
      if (!renderTimer) renderTimer = setTimeout(render, wait);
    }
    // The page is about to show another record: nothing of this one may be drawn any more.
    function dispose() {
      disposed = true;
      if (renderTimer) clearTimeout(renderTimer);
      if (frame) cancelAnimationFrame(frame);
    }
    function renderLanes() {
      if (headless || disposed) return;
      const el = $('lanes');
      width = Math.max(MIN_WIDTH, Math.floor(el.getBoundingClientRect().width || width));
      const out = lanesSvg(width);
      el.innerHTML = out.svg;
      geo = out.geo;
      $('stack').innerHTML = stackSvg(width);
    }
    function render() {
      if (headless || disposed) return;
      if (renderTimer) { clearTimeout(renderTimer); renderTimer = 0; }
      lastRender = Date.now();
      const active = host.ownerDocument.activeElement, typing = active && active.id === 'dl-feedback';
      const caret = typing ? [active.selectionStart, active.selectionEnd] : null;
      $('controls').innerHTML = controlsHtml();
      if (typing) { const input = $('feedback'); input.focus(); try { input.setSelectionRange(caret[0], caret[1]); } catch { /* not a text selection */ } }
      $('notes').textContent = notesText();
      $('fold').textContent = foldText();
      $('yfit').textContent = state.yFit ? 'Y axis: fit visible range' : 'Y axis: full range';
      renderLanes();
      $('chips').innerHTML = chipsHtml();
      $('detail').innerHTML = detailHtml();
      $('sites').innerHTML = sitesHtml();
      $('source').innerHTML = sourceHtml();
      $('table').innerHTML = tableHtml();
      $('table').open = state.tableOpen;
      const d = selDecision();
      $('announce').textContent = d ? `Decision ${d.n + 1} of ${R().decisions.length} · ${dirName(d.taken)} · ${runLabel(state.runId)}` : '';
    }

    // ---- events (wired once by mount) -----------------------------------------------------------------------------------------
    let frame = 0, drag = null, dragged = false;
    const schedule = () => { if (!headless && !frame) frame = requestAnimationFrame(() => { frame = 0; renderLanes(); }); };
    const timeAt = clientX => {
      const box = $('lanes-svg').getBoundingClientRect(), [v0, v1] = state.view;
      return v0 + (clientX - box.left - geo.X0) / (geo.X1 - geo.X0) * (v1 - v0);
    };
    function hideTip() { const tip = $('tip'); tip.hidden = true; const line = $('xhair'); if (line) { line.setAttribute('x1', -9); line.setAttribute('x2', -9); } }
    function showTip(event) {
      const svg = $('lanes-svg'), tip = $('tip'), wrap = $('lanes-wrap');
      if (!svg) return;
      const box = svg.getBoundingClientRect(), px = event.clientX - box.left;
      if (px < geo.X0 || px > geo.X1 || event.clientY < box.top || event.clientY > box.bottom) { hideTip(); return; }
      const [v0, v1] = state.view, perPx = (v1 - v0) / (geo.X1 - geo.X0);
      let t = v0 + (px - geo.X0) * perPx;
      const near = R().decisions.reduce((best, d) => Math.abs(d.t - t) < Math.abs((best ? best.t : Infinity) - t) ? d : best, null);
      if (near && Math.abs(near.t - t) <= 9 * perPx) t = near.t;   // the crosshair snaps to a decision
      const out = readout(t), sx = geo.X0 + (t - v0) / perPx, line = $('xhair');
      line.setAttribute('x1', sx); line.setAttribute('x2', sx);
      tip.textContent = '';
      const doc = host.ownerDocument, head = doc.createElement('div');
      head.className = 't-head';
      head.textContent = out.head + (near && near.t === t ? ` · decision ${near.n + 1} ${dirName(near.taken)}` : '');
      tip.appendChild(head);
      for (const row of out.rows) {
        const el = doc.createElement('div'), key = doc.createElement('span'), value = doc.createElement('b');
        el.className = 't-row';
        if (row.color) { const mark = doc.createElement('i'); mark.className = 'key'; mark.style.background = row.color; key.appendChild(mark); }
        key.appendChild(doc.createTextNode(row.k));
        value.textContent = row.v;
        el.append(key, value);
        tip.appendChild(el);
      }
      tip.hidden = false;
      const hostBox = wrap.getBoundingClientRect(), left = event.clientX - hostBox.left + 16, top = event.clientY - hostBox.top + 14;
      tip.style.left = Math.max(0, Math.min(left, hostBox.width - tip.offsetWidth - 8)) + 'px';
      tip.style.top = Math.max(0, Math.min(top, hostBox.height - tip.offsetHeight - 6)) + 'px';
    }
    const handlers = {
      pointerdown(event) {
        if (event.button !== 0 || !event.target.closest('#dl-lanes-svg')) return;
        drag = { x: event.clientX, view: state.view.slice() };
        dragged = false;
      },
      pointermove(event) {
        if (!drag) return;
        const dx = event.clientX - drag.x;
        if (Math.abs(dx) > 3) dragged = true;
        if (!dragged) return;
        const span = drag.view[1] - drag.view[0], dt = -dx / (geo.X1 - geo.X0) * span;
        state.view = clampView(drag.view[0] + dt, drag.view[1] + dt);
        $('lanes-svg').classList.add('dragging');
        hideTip();
        schedule();
      },
      pointerup() { drag = null; const svg = $('lanes-svg'); if (svg) svg.classList.remove('dragging'); },
      hover(event) { if (!drag) showTip(event); },
      leave() { hideTip(); },
      wheel(event) {
        if (!event.ctrlKey && !event.shiftKey) return;
        event.preventDefault();
        if (event.shiftKey && !event.ctrlKey) { const span = state.view[1] - state.view[0], dt = Math.sign(event.deltaY || event.deltaX) * span * 0.12; state.view = clampView(state.view[0] + dt, state.view[1] + dt); }
        else zoomAt(event.deltaY > 0 ? 1.3 : 1 / 1.3, timeAt(event.clientX));
        hideTip();
        schedule();
      },
      dblclick(event) { if (!event.target.closest('#dl-lanes-svg') || event.target.closest('[data-action]')) return; zoomAt(0.4, timeAt(event.clientX)); schedule(); },
      lanesKey(event) {
        if (event.key === 'ArrowLeft') { event.preventDefault(); select(state.sel - 1, true); }
        else if (event.key === 'ArrowRight') { event.preventDefault(); select(state.sel + 1, true); }
        else if (event.key === '+' || event.key === '=') { zoomAt(0.6); schedule(); }
        else if (event.key === '-') { zoomAt(1 / 0.6); schedule(); }
        else if (event.key === '0') { state.view = fullView(); schedule(); }
      },
      click(event) {
        const el = event.target.closest('[data-action]');
        if (!el) return;
        if (dragged && el.closest('#dl-lanes-svg')) { dragged = false; return; }
        act(el);
      },
      key(event) {
        if (event.key === 'Enter' && event.target.id === 'dl-feedback') { event.preventDefault(); applyInputs(); return; }
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const el = event.target.closest('svg [data-action], tr[data-action]');
        if (el) { event.preventDefault(); act(el); }
      },
      input(event) { if (event.target.id === 'dl-feedback') state.draft = event.target.value; },
      change(event) {
        const id = event.target.id, value = event.target.value;
        if (id === 'dl-sequencer') post({ type: 'open', sequencer: value });
        else if (id === 'dl-feedback-source') { state.source = value; state.inputError = null; if (value === 'schedule') applyInputs(); else render(); }
        else if (id === 'dl-run') setRun(value);
        else if (id === 'dl-ghost') { state.ghost = event.target.checked; renderLanes(); }
        else if (id === 'dl-add-reg' && value !== '') { state.show.regs.push(Number(value)); render(); }
      },
      toggle() { state.tableOpen = $('table').open; },
      resize() { schedule(); },
    };
    function act(el) {
      const action = el.dataset.action, value = el.dataset.value;
      if (action === 'select') return select(Number(value), true);
      if (action === 'prev') return select(state.sel - 1, true);
      if (action === 'next') return select(state.sel + 1, true);
      if (action === 'fit') { state.view = fullView(); return renderLanes(); }
      if (action === 'focus') { focusSelection(); return renderLanes(); }
      if (action === 'zoom-in') { zoomAt(0.6); return renderLanes(); }
      if (action === 'zoom-out') { zoomAt(1 / 0.6); return renderLanes(); }
      if (action === 'zoom-window') { const a = Number(el.dataset.a), b = Number(el.dataset.b), pad = (b - a) * 0.12; state.view = clampView(a - pad, b + pad); renderLanes(); return $('lanes-wrap').scrollIntoView({ block: 'nearest' }); }
      if (action === 'yfit') { state.yFit = !state.yFit; return render(); }
      if (action === 'open-run') return setRun(value);
      if (action === 'restore') return setRun(baseId);
      if (action === 'refresh') return post({ type: 'refresh' });
      if (action === 'apply-inputs') return applyInputs();
      if (action === 'record-flip') { pumpFlips(true); return render(); }
      if (action === 'reveal') return reveal(Number(value));
      if (action === 'focus-step') {
        state.focusStep = value === '' ? null : Number(value);
        $('source').innerHTML = sourceHtml();
        if (state.focusStep != null) reveal(stepLine(R(), state.focusStep));
        return undefined;
      }
      if (action === 'toggle-operands') { state.show.operands = !state.show.operands; return render(); }
      if (action === 'toggle-reg') { const r = Number(value), at = state.show.regs.indexOf(r); if (at >= 0) state.show.regs.splice(at, 1); else state.show.regs.push(r); return render(); }
      return undefined;
    }

    return { get model() { return model; }, data, state, flips, select, setRun, setView, render, focusSelection, fullView, readout, addRuns, pumpFlips,
      wantedFlips, applyInputs, runLabel, handlers, dispose,
      html: { lanes: W => lanesSvg(W).svg, stack: stackSvg, controls: controlsHtml, chips: chipsHtml, detail: detailHtml, sites: sitesHtml,
        source: sourceHtml, table: tableHtml, fold: foldText, notes: notesText } };
  }

  // Mounts the panel in a webview: builds the page once, then follows the host's messages.
  function mount(host, bridge) {
    const doc = host.ownerDocument, win = doc.defaultView;
    host.innerHTML = SKELETON;
    const $ = id => host.querySelector('#dl-' + id);
    const post = message => bridge.postMessage(message);
    let view = null, token = null;
    const forward = name => event => { if (view) view.handlers[name](event); };
    const wrap = $('lanes-wrap');
    wrap.addEventListener('pointerdown', forward('pointerdown'));
    win.addEventListener('pointermove', forward('pointermove'));
    win.addEventListener('pointerup', forward('pointerup'));
    wrap.addEventListener('pointermove', forward('hover'));
    wrap.addEventListener('pointerleave', forward('leave'));
    wrap.addEventListener('wheel', forward('wheel'), { passive: false });
    wrap.addEventListener('dblclick', forward('dblclick'));
    wrap.addEventListener('keydown', forward('lanesKey'));
    host.addEventListener('click', event => {
      const el = event.target.closest('[data-action]');
      if (el && !view && el.dataset.action === 'refresh') post({ type: 'refresh' });
      else if (view) view.handlers.click(event);
    });
    host.addEventListener('keydown', forward('key'));
    host.addEventListener('input', forward('input'));
    host.addEventListener('change', forward('change'));
    $('table').addEventListener('toggle', forward('toggle'));
    if (typeof win.ResizeObserver !== 'undefined') new win.ResizeObserver(() => { if (view) view.handlers.resize(); }).observe($('lanes'));

    function banner(kind, text, action) {
      const el = $('banner');
      el.hidden = !text;
      el.className = 'banner ' + (kind || '');
      el.textContent = text || '';
      if (text && action) {
        const button = doc.createElement('button');
        button.dataset.action = 'refresh';
        button.textContent = action;
        el.append(' ', button);
      }
    }
    // busy: a record is being made. outdated: the record on the page could not be replaced by a new one.
    function mark(state) { for (const name of ['busy', 'outdated']) host.classList.toggle(name, name === state); }
    function receive(message) {
      if (!message || typeof message !== 'object') return;
      if (message.type === 'loading') { mark('busy'); if (!view) { $('content').hidden = true; banner('info', message.text || 'Recording…'); } return; }
      if (message.type === 'error') {
        mark(view ? 'outdated' : '');
        if (!view) $('content').hidden = true;
        banner('error', message.message || 'Decision lanes could not be recorded.', 'Refresh');
        return;
      }
      if (message.type === 'stale') { banner('warn', message.message || 'The source changed.', null); return; }
      if (message.type === 'data') {
        mark('');
        token = message.token ?? null;
        const data = message.payload, previous = view;
        data.token = token;
        try {
          const restore = previous && previous.data.sequencer === data.sequencer && previous.data.sourceFile === data.sourceFile
            ? { state: previous.state, full: previous.fullView() } : null;
          if (previous) previous.dispose();
          view = null;
          view = createView(data, { root: host, post, history: message.history || [], restore });
          $('content').hidden = false;
          banner(message.notice ? 'warn' : '', message.notice || '', null);
          view.render();
          view.pumpFlips(false);
        } catch (error) {
          view = null;
          $('content').hidden = true;
          banner('error', 'The record could not be displayed: ' + (error && error.message ? error.message : String(error)), 'Refresh');
        }
        return;
      }
      if (message.type === 'runs' && view && (message.token ?? null) === token) {
        try { view.addRuns(message); } catch (error) { banner('error', 'A recorded run could not be displayed: ' + (error && error.message ? error.message : String(error)), 'Refresh'); }
      }
    }
    win.addEventListener('message', event => receive(event.data));
    $('content').hidden = true;
    banner('info', 'Recording…');
    post({ type: 'ready' });
    return { receive, get view() { return view; } };
  }

  const api = { createView, mount, parseFeedback, formatFeedback, SKELETON };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) {
    root.q1lensDecisionLanes = api;
    const host = root.document && root.document.getElementById('decision-lanes');
    if (host && typeof root.acquireVsCodeApi === 'function') api.mounted = mount(host, root.acquireVsCodeApi());
  }
})(typeof window !== 'undefined' ? window : undefined);
