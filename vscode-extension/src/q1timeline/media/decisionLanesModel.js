/* Decision lanes — model.
 *
 * Reads the runs recorded by q1timeline.decision_lanes. Nothing here interprets Q1ASM: paths,
 * values and times are the ones Playback recorded. What this file adds are derivations over
 * those records, each stated next to the code that computes it:
 *
 *   input slice      which branch conditions trace back to a feedback word
 *   join             where the two arms of a branch must meet again: the immediate
 *                    post-dominator in the instruction flow graph, or the end of the program
 *   footprint        registers an arm writes that are still read after the join
 *   partner runs     two recorded runs whose paths first differ at one conditional branch
 *                    (same inputs, same history)
 *   arm difference   Q1 time, RT time, RT-visible instructions and register values compared
 *                    at the join
 *   executed loops   back-edge passes found in the recorded PC sequence
 *
 * The same file is loaded by the webview and by the unit tests.
 */
(function (root) {
  'use strict';

  // Step tuple written by q1timeline.decision_lanes.
  const PC = 0, Q0 = 1, Q1 = 2, T0 = 3, T1 = 4, READS = 5, WRITES = 6, SNAP = 7, BR = 8, NEXT = 9;
  // Branch tuple: taken, calculated (null when the compared value is unknown), forced by the
  // user, producer step, target pc, fall-through pc, and the two compared operands as
  // [text, register, unsigned word] when a cmp, a test or a three-operand branch produced them.
  const TAKEN = 0, CALC = 1, FORCED = 2, FLAGS = 3, TARGET = 4, FALL = 5, COMPARE = 6;
  const END = -1;   // a join at the end of the program: the arms never meet again

  // Latched setters, as listed in q1timeline.analysis.interpreter._prove_timing_only_branch_join.
  const LATCHED = new Set(['set_mrk', 'set_awg_gain', 'set_awg_offs', 'set_freq', 'reset_ph', 'set_ph',
    'set_ph_delta', 'set_digital', 'set_scope_en', 'set_cond']);
  // Instructions that bring an external word into a register.
  const INPUT_OPS = new Set(['fb_pop_data', 'fb_pull_data']);
  const RELATIONS = { jl: '<', jlt: '<', jge: '≥', jz: '=', jnz: '≠', jg: '>', jle: '≤', ja: '>', jae: '≥', jb: '<', jbe: '≤', loop: '≠' };
  // Status branches that compare the cmp operands as unsigned words.
  const UNSIGNED_STATUS = new Set(['ja', 'jae', 'jb', 'jbe']);
  // Status branches that test one flag of the result instead of a relation between two operands.
  const FLAG_TESTS = { js: 'negative', jns: 'not negative', jo: 'signed overflow', jno: 'no signed overflow' };

  const F_INPUT = 1, F_ARM = 2, F_CONST = 4, F_INITIAL = 8;
  const isRegister = text => /^R\d+$/.test(text || '');
  // A, B, … Z, AA, AB, …: the n-th distinct path keeps its own name however many there are.
  function pathName(n) {
    let name = '';
    for (n += 1; n > 0; n = Math.floor((n - 1) / 26)) name = String.fromCharCode(65 + (n - 1) % 26) + name;
    return name;
  }

  // Immediate post-dominators of a flow graph. succ[k] holds the successors of node k; the exit
  // is node succ.length and every node must reach it. Cooper, Harvey and Kennedy's iterative
  // dominator algorithm, run on the reversed graph from the exit.
  function immediatePostDominators(succ) {
    const n = succ.length, exit = n;
    const pred = Array.from({ length: n + 1 }, () => []);
    for (let k = 0; k < n; k++) for (const s of succ[k]) pred[s].push(k);
    const order = new Int32Array(n + 1).fill(-1), postorder = [];
    const seen = new Uint8Array(n + 1), stack = [[exit, 0]];
    seen[exit] = 1;
    while (stack.length) {
      const top = stack[stack.length - 1], next = pred[top[0]][top[1]++];
      if (next === undefined) { postorder.push(top[0]); stack.pop(); }
      else if (!seen[next]) { seen[next] = 1; stack.push([next, 0]); }
    }
    const rpo = postorder.reverse();
    rpo.forEach((node, k) => { order[node] = k; });
    const idom = new Int32Array(n + 1).fill(-1);
    idom[exit] = exit;
    const intersect = (a, b) => {
      while (a !== b) {
        while (order[a] > order[b]) a = idom[a];
        while (order[b] > order[a]) b = idom[b];
      }
      return a;
    };
    for (let changed = true; changed;) {
      changed = false;
      for (let k = 1; k < rpo.length; k++) {
        const node = rpo[k];
        let best = -1;
        for (const s of succ[node]) {
          if (idom[s] < 0) continue;
          best = best < 0 ? s : intersect(s, best);
        }
        if (best >= 0 && idom[node] !== best) { idom[node] = best; changed = true; }
      }
    }
    return idom;
  }

  function create(data) {
    const ins = new Map(data.instructions.map(i => [i.pc, i]));
    const opOf = s => ins.get(s[PC]).op;
    const textOf = s => ins.get(s[PC]).text.replace(/\s+/g, ' ');
    const lineOf = s => ins.get(s[PC]).line;
    const isCond = s => s[BR] != null && opOf(s) !== 'jmp';
    const isIndirectJump = s => opOf(s) === 'jmp' && isRegister(ins.get(s[PC]).args[0]);
    const isAcquire = s => opOf(s).startsWith('acquire');
    const isLatched = s => LATCHED.has(opOf(s));
    const isRtVisible = s => s[T1] > s[T0] || isLatched(s) || isAcquire(s);
    const labelAt = new Map(Object.entries(data.labels || {}).map(([name, pc]) => [pc, name]));

    // ---- recorded runs -------------------------------------------------------------------
    const stepCache = new Map();
    function stepsOf(id) {
      if (stepCache.has(id)) return stepCache.get(id);
      const raw = data.runs[id];
      if (!raw) throw new Error('Unknown recorded run: ' + id);
      const steps = raw.base ? stepsOf(raw.base).slice(0, raw.shared).concat(raw.steps) : raw.steps;
      stepCache.set(id, steps);
      return steps;
    }
    const runIds = Object.keys(data.runs);
    const forcedCount = id => Object.keys(data.runs[id].forced || {}).length;
    const kindRank = id => ({ base: 0, flip: 1 })[data.runs[id].kind] ?? 2;

    // ---- partner runs --------------------------------------------------------------------
    // Two runs of one scenario get the same inputs. Where their PC sequences first differ, the
    // step before is a conditional branch that went one way in A and the other way in B with
    // identical history: each run is the other's recorded "other arm".
    function firstPathDifference(A, B) {
      const n = Math.min(A.length, B.length);
      for (let i = 0; i < n; i++) if (A[i] !== B[i] && A[i][PC] !== B[i][PC]) return i;
      return -1;
    }
    const partnerIndex = new Map(runIds.map(id => [id, new Map()]));
    function offerPartner(id, step, other) {
      const map = partnerIndex.get(id), current = map.get(step);
      const better = !current || forcedCount(other) < forcedCount(current) ||
        (forcedCount(other) === forcedCount(current) && kindRank(other) < kindRank(current));
      if (better) map.set(step, other);
    }
    const byScenario = new Map();
    for (const id of runIds) {
      const sid = data.runs[id].scenario;
      if (!byScenario.has(sid)) byScenario.set(sid, []);
      byScenario.get(sid).push(id);
    }
    for (const ids of byScenario.values()) {
      for (let a = 0; a < ids.length; a++) for (let b = a + 1; b < ids.length; b++) {
        const A = stepsOf(ids[a]), B = stepsOf(ids[b]);
        const i = firstPathDifference(A, B);
        if (i <= 0) continue;
        const d = i - 1;
        if (!isCond(A[d]) || !isCond(B[d]) || A[d][BR][TAKEN] === B[d][BR][TAKEN]) continue;
        offerPartner(ids[a], d, ids[b]);
        offerPartner(ids[b], d, ids[a]);
      }
    }

    // ---- joins ---------------------------------------------------------------------------
    // Instruction flow graph: fall-through, direct targets from the source text, and every
    // transfer observed in any run (this is how jmp R<n> gets its targets). The join of a
    // branch is its immediate post-dominator: the first instruction every path from the
    // branch to the end of the program must pass. END means the arms never meet again.
    function staticJoins() {
      const pcs = data.instructions.map(i => i.pc), n = pcs.length, at = new Map(pcs.map((pc, k) => [pc, k])), exit = n;
      const succ = Array.from({ length: n }, () => new Set());
      const next = k => k + 1 < n ? k + 1 : exit;
      data.instructions.forEach((i, k) => {
        if (i.op === 'stop') { succ[k].add(exit); return; }
        const branch = i.op === 'loop' || /^j/.test(i.op), last = i.args[i.args.length - 1] || '';
        if (branch && last.startsWith('@') && at.has(data.labels[last.slice(1)])) succ[k].add(at.get(data.labels[last.slice(1)]));
        if (!branch || i.op !== 'jmp') succ[k].add(next(k));
      });
      for (const id of runIds) for (const s of stepsOf(id)) if (s[NEXT] != null && at.has(s[PC]) && at.has(s[NEXT])) succ[at.get(s[PC])].add(at.get(s[NEXT]));
      // An endless loop has no path to the end; give its last instruction one, so that joins
      // inside the loop body are still defined.
      const pred = Array.from({ length: n + 1 }, () => []);
      for (let k = 0; k < n; k++) for (const s of succ[k]) pred[s].push(k);
      const reaches = new Uint8Array(n + 1);
      const flood = start => {
        const stack = [start];
        reaches[start] = 1;
        while (stack.length) for (const p of pred[stack.pop()]) if (!reaches[p]) { reaches[p] = 1; stack.push(p); }
      };
      flood(exit);
      for (let k = n - 1; k >= 0; k--) if (!reaches[k]) { succ[k].add(exit); pred[exit].push(k); flood(k); }
      const idom = immediatePostDominators(succ);
      const joins = new Map();
      for (let k = 0; k < n; k++) joins.set(pcs[k], idom[k] === exit || idom[k] < 0 ? END : pcs[idom[k]]);
      return joins;
    }
    const joins = staticJoins();

    // ---- branch sites and what their conditions depend on ----------------------------------
    // Reads carry the step that wrote the value, and writers always precede readers, so one
    // forward pass propagates "depends on ..." bits along the recorded data flow.
    function flagsFor(S, armOf) {
      const f = new Uint8Array(S.length);
      for (let i = 0; i < S.length; i++) {
        const s = S[i];
        let bits = 0;
        if (INPUT_OPS.has(opOf(s))) bits |= F_INPUT;
        if (armOf && armOf[i] >= 0) bits |= F_ARM;
        if (!s[READS].length && s[WRITES].length && !INPUT_OPS.has(opOf(s))) bits |= F_CONST;
        for (const read of s[READS]) bits |= read[2] == null ? F_INITIAL : f[read[2]];
        f[i] = bits;
      }
      return f;
    }
    const sites = new Map();
    function siteFor(s) {
      let site = sites.get(s[PC]);
      if (!site) {
        const i = ins.get(s[PC]);
        site = { pc: i.pc, line: i.line, op: i.op, text: i.text.replace(/\s+/g, ' '), target: s[BR][TARGET],
          fall: s[BR][FALL], backward: s[BR][TARGET] != null && s[BR][TARGET] <= i.pc, mask: 0,
          join: joins.has(i.pc) ? joins.get(i.pc) : END, cls: null, footprint: new Map(), scratch: new Set(),
          compared: new Set(), pairs: 0, pairJoin: null, pairAgreement: null, pairJoins: [] };
        sites.set(i.pc, site);
      }
      if (site.target == null && s[BR][TARGET] != null) {
        site.target = s[BR][TARGET];
        site.backward = site.target <= site.pc;
      }
      return site;
    }
    function accumulate(arms) {
      for (const site of sites.values()) site.mask = 0;
      for (const id of runIds) {
        const S = stepsOf(id), f = flagsFor(S, arms ? arms.get(id).armOf : null);
        for (const s of S) {
          if (!isCond(s)) continue;
          const site = siteFor(s), producer = s[BR][FLAGS];
          if (producer == null) continue;
          site.mask |= f[producer];
          for (const read of S[producer][READS]) site.compared.add(read[0]);
        }
      }
    }
    accumulate(null);
    const dependsOnInput = site => (site.mask & F_INPUT) !== 0;
    const inputSites = new Set([...sites.values()].filter(dependsOnInput).map(site => site.pc));

    // Steps between an input-dependent branch and its join run "inside an arm" of that branch.
    function armsFor(S) {
      const armOf = new Int32Array(S.length).fill(-1), joinIdx = new Map();
      for (let i = 0; i < S.length; i++) {
        if (!isCond(S[i]) || !inputSites.has(S[i][PC])) continue;
        const join = sites.get(S[i][PC]).join;
        let e = S.length;
        if (join !== END) for (let k = i + 1; k < S.length; k++) if (S[k][PC] === join) { e = k; break; }
        joinIdx.set(i, e);
        for (let k = i + 1; k < e; k++) armOf[k] = i;
      }
      return { armOf, joinIdx };
    }
    const arms = new Map(runIds.map(id => [id, armsFor(stepsOf(id))]));

    // Footprint: a register an arm writes whose value is read at or after the join (or is the
    // final state, when the join is the end of the program). Writes only read inside the arm
    // are scratch work and are left out.
    for (const id of runIds) {
      const S = stepsOf(id), lastRead = new Map(), { joinIdx } = arms.get(id);
      for (let i = 0; i < S.length; i++) for (const read of S[i][READS]) if (read[2] != null) lastRead.set(read[2] * 64 + read[0], i);
      for (const [d, e] of joinIdx) {
        const site = sites.get(S[d][PC]), side = S[d][BR][TAKEN] ? 'taken' : 'fall';
        for (let k = d + 1; k < e; k++) for (const w of S[k][WRITES]) {
          const live = site.join === END ? e >= S.length && !data.runs[id].truncated : (lastRead.get(k * 64 + w[0]) ?? -1) >= e;
          if (!live) { site.scratch.add(w[0]); continue; }
          const entry = site.footprint.get(w[0]) || { taken: false, fall: false };
          entry[side] = true;
          site.footprint.set(w[0], entry);
        }
      }
    }
    for (const site of sites.values()) for (const reg of site.footprint.keys()) site.scratch.delete(reg);

    // Independent evidence for the join: where the PC sequences of recorded pairs first coincide.
    // The tests compare this with the static join; arms that contain further branches can share
    // an inner block before the true join, which is why the static one is used.
    function firstCommonPc(A, B, d, limit = 600) {
      for (let total = 0; total < limit; total++) for (let i = 0; i <= total; i++) {
        const a = A[d + 1 + i], b = B[d + 1 + total - i];
        if (a && b && a[PC] === b[PC]) return a[PC];
      }
      return null;
    }
    const votes = new Map();
    for (const [id, map] of partnerIndex) for (const [d, other] of map) {
      if (id > other) continue;
      const A = stepsOf(id), site = sites.get(A[d][PC]);
      if (!site || !inputSites.has(site.pc)) continue;
      site.pairs++;
      const common = firstCommonPc(A, stepsOf(other), d);
      if (!votes.has(site.pc)) votes.set(site.pc, new Map());
      votes.get(site.pc).set(common, (votes.get(site.pc).get(common) || 0) + 1);
    }
    for (const [pc, tally] of votes) {
      const ranked = [...tally].sort((x, y) => y[1] - x[1]);
      sites.get(pc).pairJoin = ranked[0][0] == null ? END : ranked[0][0];
      sites.get(pc).pairAgreement = tally.size === 1;
      sites.get(pc).pairJoins = ranked.map(([common, n]) => [common == null ? END : common, n]);
    }

    accumulate(arms);
    for (const site of sites.values()) {
      // input     the compared values trace back to a feedback word and the arms change something else
      // shaping   input-dependent, but the branch only rewrites a register it compared
      //           (absolute value, clamp): it shapes a value rather than choosing what to do
      // indirect  it reads a value written inside an arm of an input-dependent branch
      // counted   only constants, and the branch jumps backward (a counted loop)
      // fixed     only constants, forward target (decided by the program alone)
      const shaping = site.footprint.size > 0 && [...site.footprint.keys()].every(reg => site.compared.has(reg));
      site.cls = dependsOnInput(site) ? (shaping ? 'shaping' : 'input') : site.mask & F_ARM ? 'indirect' : site.backward ? 'counted' : 'fixed';
    }

    // ---- executed loops ----------------------------------------------------------------------
    // A backward direct transfer marks a loop header. Passes are the stretches between visits to
    // the header that are chained by that back-edge. jmp R<n> is a return, not a loop.
    function detectLoops(S, truncated) {
      const sources = new Map();
      for (const s of S) {
        if (!s[BR] || s[NEXT] == null || s[NEXT] > s[PC] || isIndirectJump(s)) continue;
        if (!sources.has(s[NEXT])) sources.set(s[NEXT], new Set());
        sources.get(s[NEXT]).add(s[PC]);
      }
      const loops = [];
      for (const [header, src] of sources) {
        const instances = [];
        let inst = null;
        for (let i = 0; i < S.length; i++) {
          if (S[i][PC] !== header) continue;
          const prev = i > 0 ? S[i - 1] : null;
          const viaBack = prev && src.has(prev[PC]) && prev[NEXT] === header;
          if (!viaBack || !inst) { inst = { passes: [] }; instances.push(inst); }
          inst.passes.push({ start: i, end: i, kind: 'iter' });
        }
        const body = new Set();
        for (const it of instances) for (let k = 0; k + 1 < it.passes.length; k++) {
          it.passes[k].end = it.passes[k + 1].start - 1;
          for (let i = it.passes[k].start; i <= it.passes[k].end; i++) body.add(S[i][PC]);
        }
        for (const it of instances) {
          const last = it.passes[it.passes.length - 1];
          // The last pass does not take the back edge. It is still an iteration when it reaches a
          // back-edge source that fell through: the last such step before the header is visited
          // again ends it, whichever arms it took on the way.
          let e = src.has(S[last.start][PC]) ? last.start : -1;
          for (let k = last.start + 1; k < S.length && S[k][PC] !== header; k++) if (src.has(S[k][PC])) e = k;
          if (e >= 0) last.kind = 'iter';
          else {
            // It left before that: only an exit test ran, or the record was cut off inside the pass.
            e = last.start;
            while (e + 1 < S.length && S[e + 1][PC] !== header && body.has(S[e + 1][PC])) e++;
            last.kind = truncated && e === S.length - 1 ? 'cut' : 'exit';
          }
          last.end = e;
          it.start = it.passes[0].start;
          it.end = e;
        }
        loops.push({ header, label: labelAt.get(header) || null, sources: src, instances,
          steps: instances.reduce((n, it) => n + it.end - it.start + 1, 0) });
      }
      return loops;
    }

    // RT lane: the instructions with RT duration from step `from` on. One wait instruction that
    // runs again back to back (a counted wait loop) is one block. A latched setter, an
    // acquisition or an input-dependent branch in between keeps the waits apart.
    function rtBlocks(S, from) {
      const blocks = [];
      let open = null;
      for (let i = from; i < S.length; i++) {
        const s = S[i];
        if (s[T1] > s[T0]) {
          if (open && open.op === 'wait' && S[open.first][PC] === s[PC] && open.t1 === s[T0]) {
            open.t1 = s[T1]; open.count++; open.last = i;
          } else {
            open = { op: opOf(s), t0: s[T0], t1: s[T1], count: 1, first: i, last: i };
            blocks.push(open);
          }
        } else if (isLatched(s) || isAcquire(s) || (isCond(s) && inputSites.has(s[PC]))) open = null;
      }
      return blocks;
    }

    // ---- per-run view ------------------------------------------------------------------------
    // Path names. Nobody assigns them: two passes get the same name exactly when their decisions
    // went the same way in the same order. Names are handed out in order of first appearance,
    // from one table per record set, so a name means the same path in every run of that program.
    const signatureLetters = new Map();
    const letterFor = key => {
      if (!signatureLetters.has(key)) signatureLetters.set(key, pathName(signatureLetters.size));
      return signatureLetters.get(key);
    };
    // The readable name of a path comes from the source's own labels: for each decision, the first
    // label the recorded run reaches after it, before another branch takes over and before the
    // decision's join. A path that reaches no label is named by the line its last decision
    // continues at. If two different paths would read the same, the letter is appended.
    const signatureNames = new Map(), namesInUse = new Map();
    function nameFor(signature, compute) {
      if (signatureNames.has(signature)) return signatureNames.get(signature);
      let name = compute();
      if (name != null && namesInUse.has(name) && namesInUse.get(name) !== signature) name += ' (' + letterFor(signature) + ')';
      if (name != null) namesInUse.set(name, signature);
      signatureNames.set(signature, name);
      return name;
    }
    const runCache = new Map();
    function run(id) {
      if (runCache.has(id)) return runCache.get(id);
      const raw = data.runs[id], S = stepsOf(id), n = S.length;
      const { armOf, joinIdx } = arms.get(id);
      const loops = detectLoops(S, !!raw.truncated);

      // decisions: branches of class "input". Everything else conditional except counted loops
      // is kept as a minor mark. branches lists every executed conditional branch.
      const decisions = [], minor = [], forced = [], branches = [], counts = new Map();
      for (let i = 0; i < n; i++) {
        const s = S[i];
        if (!isCond(s)) continue;
        const site = sites.get(s[PC]);
        const c = counts.get(site.pc) || { taken: 0, not: 0, steps: [] };
        s[BR][TAKEN] ? c.taken++ : c.not++;
        const rec = { step: i, pc: site.pc, k: c.steps.length, taken: s[BR][TAKEN], calc: s[BR][CALC],
          forced: s[BR][FORCED], t: s[T0], q: s[Q0], n: null, input: inputSites.has(site.pc) };
        c.steps.push(i);
        counts.set(site.pc, c);
        branches.push(rec);
        if (site.cls === 'input') { rec.n = decisions.length; decisions.push(rec); }
        else if (site.cls !== 'counted') minor.push(rec);
        if (rec.forced) forced.push(rec);
      }

      // The loop the decisions live in: the smallest executed loop that contains them.
      let decisionLoop = null;
      for (const loop of loops) {
        const holds = decisions.filter(d => loop.instances.some(it => d.step >= it.start && d.step <= it.end)).length;
        if (holds && (!decisionLoop || loop.steps < decisionLoop.steps)) decisionLoop = loop;
      }
      // A pass is named by the directions of its decisions; shaping branches do not count,
      // or every sign pattern of the inputs would look like a different path.
      const landing = (d, limit) => {
        const join = Math.min(joinIdx.get(d.step) ?? n, limit);
        for (let k = d.step + 1; k < join; k++) {
          const label = labelAt.get(S[k][PC]);
          if (label) return label;
          if (isCond(S[k])) return null;
        }
        return null;
      };
      const readable = (list, limit) => {
        const parts = [];
        for (const d of list) { const label = landing(d, limit); if (label && parts[parts.length - 1] !== label) parts.push(label); }
        if (parts.length || !list.length) return parts.length ? parts.join(' → ') : null;
        const next = S[list[list.length - 1].step][NEXT];
        return next == null || !ins.has(next) ? null : 'L' + ins.get(next).line;
      };
      const signatureOf = list => list.map(d => d.pc + ':' + (d.taken ? 1 : 0)).join(' ');
      const passes = [];
      if (decisionLoop) for (const it of decisionLoop.instances) for (const p of it.passes) {
        const inside = decisions.filter(d => d.step >= p.start && d.step <= p.end), signature = signatureOf(inside);
        passes.push({ index: passes.length, start: p.start, end: p.end, kind: p.kind, t0: S[p.start][T0],
          t1: S[p.end][T1], signature, letter: p.kind !== 'iter' ? null : letterFor(signature),
          name: p.kind !== 'iter' ? null : nameFor(signature, () => readable(inside, p.end + 1)) });
      }
      for (const d of decisions) d.pass = passes.findIndex(p => d.step >= p.start && d.step <= p.end);
      // Without a loop the whole run is one path.
      const pathSignature = decisionLoop || !decisions.length ? null : signatureOf(decisions);
      const pathLabel = pathSignature == null ? null : nameFor(pathSignature, () => readable(decisions, n));

      const blocks = rtBlocks(S, 0), acquires = [], setters = [];
      for (let i = 0; i < n; i++) {
        if (isAcquire(S[i])) acquires.push(i);
        if (isLatched(S[i])) setters.push(i);
      }

      // Which acquisition a feedback word belongs to. Playback treats receipts as assumed
      // inputs and records no link, so this is inferred: the words read after a group of
      // acquisitions are shared out among them in order when they divide evenly ("order"),
      // otherwise they all go to the most recent acquisition ("recent").
      const popSource = new Map();
      {
        let pending = [], burst = [];
        const flush = () => {
          if (!burst.length) return;
          const even = pending.length > 0 && burst.length % pending.length === 0, per = even ? burst.length / pending.length : 0;
          burst.forEach((p, j) => popSource.set(p, { acquire: even ? pending[Math.floor(j / per)] : pending.length ? pending[pending.length - 1] : null,
            rule: even && pending.length > 1 ? 'order' : 'recent' }));
          pending = [];
          burst = [];
        };
        for (let i = 0; i < n; i++) {
          if (isAcquire(S[i])) { flush(); pending.push(i); }
          else if (INPUT_OPS.has(opOf(S[i]))) burst.push(i);
        }
        flush();
      }

      // Register history in program order: value after each write, placed at the writer's RT clock.
      const seriesCache = new Map();
      function series(reg) {
        if (seriesCache.has(reg)) return seriesCache.get(reg);
        const points = [{ step: -1, t: 0, v: S[0][SNAP] ? S[0][SNAP][reg] : null }];
        for (let i = 0; i < n; i++) for (const w of S[i][WRITES]) if (w[0] === reg) points.push({ step: i, t: S[i][T0], v: w[2], ready: w[3] });
        seriesCache.set(reg, points);
        return points;
      }
      function valueBefore(reg, step) {
        const points = series(reg);
        let lo = 0, hi = points.length - 1;
        while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (points[mid].step < step) lo = mid; else hi = mid - 1; }
        return points[lo].v;
      }
      function valueAtTime(reg, t) {
        const points = series(reg);
        let lo = 0, hi = points.length - 1;
        while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (points[mid].t <= t) lo = mid; else hi = mid - 1; }
        return points[lo].v;
      }
      const changing = [];
      for (let reg = 0; reg < data.registers.length; reg++) {
        const values = new Set(series(reg).filter(p => p.step >= 0).map(p => p.v));
        if (values.size >= 2) changing.push(reg);
      }
      const rtRead = new Set();
      for (const s of S) if (isRtVisible(s)) for (const r of s[READS]) rtRead.add(r[0]);

      const view = { id, raw, kind: raw.kind, scenario: raw.scenario, steps: S, armOf, joinIdx, loops, decisionLoop, passes,
        pathSignature, pathLabel, decisions, minor, forced, branches, counts, blocks, acquires, setters, popSource, series,
        valueBefore, valueAtTime, changing, rtRead: [...rtRead].sort((a, b) => a - b), rtEnd: raw.rtEnd, q1End: raw.q1End,
        truncated: !!raw.truncated, status: raw.status || 'completed', reason: raw.reason || null,
        firstForced: forced[0] || null, finals: raw.registers };
      runCache.set(id, view);
      return view;
    }
    for (const sc of data.scenarios) run(sc.base);

    // ---- one branch occurrence -----------------------------------------------------------------
    function partner(runId, step) {
      return partnerIndex.get(runId).get(step) || null;
    }

    function describeArm(R, d) {
      const join = R.joinIdx.get(d);
      if (join == null) return null;
      const S = R.steps, atEnd = join >= S.length, steps = [], rtVisible = [], writes = new Map();
      for (let i = d + 1; i < join; i++) {
        steps.push(i);
        if (isRtVisible(S[i])) rtVisible.push(textOf(S[i]) + ' · ' + (S[i][T1] - S[i][T0]) + ' ns');
        for (const w of S[i][WRITES]) writes.set(w[0], w[2]);
      }
      return { taken: S[d][BR][TAKEN], steps, join, atEnd, joinPc: atEnd ? null : S[join][PC],
        q1: (atEnd ? R.q1End : S[join][Q0]) - S[d][Q0], rt: (atEnd ? R.rtEnd : S[join][T0]) - S[d][T0],
        rtVisible, writes, open: atEnd && sites.get(S[d][PC]).join !== END };
    }

    // What the branch changes by the time both arms have met again (or the program has ended).
    function armDiff(runId, d) {
      const R = run(runId), site = sites.get(R.steps[d][PC]);
      const mine = describeArm(R, d);
      const otherId = partner(runId, d);
      if (!mine || !otherId) return { site, mine, other: null, otherId: null, cls: null, state: [] };
      const P = run(otherId), other = describeArm(P, d);
      if (!other) return { site, mine, other: null, otherId, cls: null, state: [] };
      const state = [];
      for (const reg of new Set([...mine.writes.keys(), ...other.writes.keys()])) {
        if (site.scratch.has(reg)) continue;
        const a = R.valueBefore(reg, mine.join), b = P.valueBefore(reg, other.join);
        if (a !== b) state.push({ reg, before: R.valueBefore(reg, d), mine: a, other: b });
      }
      state.sort((x, y) => x.reg - y.reg);
      const sameOutputs = mine.rtVisible.length === other.rtVisible.length && mine.rtVisible.every((x, i) => x === other.rtVisible[i]);
      // time: the arms take different RT time; output: same time, different RT-visible instructions;
      // state: only register values differ; none: nothing differs at the join except Q1 time.
      const cls = mine.rt !== other.rt ? 'time' : !sameOutputs ? 'output' : state.length ? 'state' : 'none';
      return { site, mine, other, otherId, cls, state, sameOutputs };
    }

    // Why this direction: the compared values, and the recorded data flow back to the inputs.
    function why(runId, d) {
      const R = run(runId), S = R.steps, s = S[d], b = s[BR], f = b[FLAGS];
      if (f == null) return null;
      const producer = S[f], producerIns = ins.get(producer[PC]), op = opOf(s);
      let operands, unsigned = false;
      const recorded = b[COMPARE] ? b[COMPARE].map(([label, reg, word]) => {
        const read = reg == null ? null : producer[READS].find(r => r[0] === reg);
        const signed = word == null ? null : word >= 0x80000000 ? word - 0x100000000 : word;
        return { label, reg, word, signed: read ? read[1] : signed, writer: read ? read[2] : null };
      }) : null;
      if (recorded && producerIns.op === 'test') {
        // test sets the flags from the bitwise AND of its operands; the branch asks about that word.
        const [x, y] = recorded, word = x.word == null || y.word == null ? null : (x.word & y.word) >>> 0;
        operands = [{ label: x.label + ' & ' + y.label, reg: x.reg ?? y.reg, value: word, signed: word == null ? null : word | 0,
          writer: x.writer ?? y.writer, parts: recorded }, { label: '0', reg: null, value: 0, signed: 0, writer: null }];
        unsigned = true;
      } else if (recorded) {
        // Recorded compare operands as 32-bit words. jlt/jge with three operands and the unsigned
        // status branches compare them unsigned; other branches on the flags of a cmp are read
        // as a signed compare.
        unsigned = f === d || UNSIGNED_STATUS.has(op);
        operands = recorded.map(o => ({ label: o.label, reg: o.reg, value: unsigned ? o.word : o.signed, signed: o.signed, writer: o.writer }));
      } else {
        // Any other producer: its result against zero.
        const w = producer[WRITES][0];
        operands = [{ label: w ? data.registers[w[0]] : producerIns.text.replace(/\s+/g, ' '), reg: w ? w[0] : null, value: w ? w[2] : null,
          signed: w ? w[2] : null, writer: f }, { label: '0', reg: null, value: 0, signed: 0, writer: null }];
      }
      for (const operand of operands) {
        const chain = [], inputs = [], seen = new Set();
        const stack = operand.parts ? operand.parts.map(part => part.writer) : [operand.writer];
        let truncated = false;
        while (stack.length) {
          const i = stack.pop();
          if (i == null || seen.has(i)) continue;
          seen.add(i);
          if (INPUT_OPS.has(opOf(S[i]))) { inputs.push(i); continue; }
          if (chain.length >= 40) { truncated = true; continue; }
          chain.push(i);
          for (const r of S[i][READS]) stack.push(r[2]);
        }
        chain.sort((x, y) => y - x);
        inputs.sort((x, y) => x - y);
        // Inputs grouped by the acquisition they are inferred to come from (see popSource).
        const sources = new Map(), rules = new Map();
        for (const i of inputs) {
          const source = R.popSource.get(i) || { acquire: null, rule: 'recent' };
          if (!sources.has(source.acquire)) sources.set(source.acquire, []);
          rules.set(source.acquire, source.rule);
          const word = S[i][WRITES][S[i][WRITES].length - 1];   // fb_pull_data writes the ID first, then the word
          sources.get(source.acquire).push({ step: i, reg: word ? word[0] : null, value: word ? word[2] : null });
        }
        operand.chain = chain;
        operand.truncated = truncated;
        operand.sources = [...sources].map(([a, pops]) => {
          if (a == null) return { acquire: null, pops, rule: null };
          const window = R.blocks.find(block => block.t0 >= S[a][T1] && block.op === 'wait') || null;
          const settings = [];
          for (const name of LATCHED) {
            let last = null;
            for (const i of R.setters) { if (i > a) break; if (opOf(S[i]) === name) last = i; }
            if (last != null && S[last][READS].length) settings.push({ step: last, text: textOf(S[last]), reads: S[last][READS] });
          }
          return { acquire: a, t0: S[a][T0], t1: window ? window.t1 : S[a][T1], reads: S[a][READS], pops, settings, rule: rules.get(a) };
        });
      }
      // x >= 0x80000000 on unsigned words asks whether x is negative as a signed word.
      const signTest = unsigned && f === d && operands[1].value === 0x80000000;
      return { step: d, producer: f, op, operator: RELATIONS[op] || null, flagTest: FLAG_TESTS[op] || null, operands, unsigned, signTest,
        calculated: b[CALC], taken: b[TAKEN], forced: b[FORCED] };
    }

    // The recorded run that took the other arm here, compared with this one.
    function counterfactual(runId, d) {
      const otherId = partner(runId, d);
      if (!otherId) return null;
      const R = run(runId), P = run(otherId), from = R.decisions.findIndex(x => x.step === d);
      let later = 0, differ = 0;
      for (let k = from + 1; k < Math.max(R.decisions.length, P.decisions.length); k++) {
        later++;
        const a = R.decisions[k], b = P.decisions[k];
        if (!a || !b || a.pc !== b.pc || a.taken !== b.taken) differ++;
      }
      return { otherId, later, differ, rtEnd: [R.rtEnd, P.rtEnd], q1End: [R.q1End, P.q1End],
        decisions: [R.decisions.length, P.decisions.length] };
    }

    // The first RT-visible instruction that is not the same in the two runs after the branch:
    // a different instruction, RT clock, RT duration, or a different value read from a register.
    function firstRtDifference(runId, d) {
      const otherId = partner(runId, d);
      if (!otherId) return null;
      const A = run(runId).steps, B = run(otherId).steps;
      const next = (S, k) => { while (k < S.length && !isRtVisible(S[k])) k++; return k; };
      let i = d + 1, j = d + 1;
      for (;;) {
        i = next(A, i); j = next(B, j);
        if (i >= A.length || j >= B.length) return i >= A.length && j >= B.length ? null : { mine: i < A.length ? i : null, other: j < B.length ? j : null, kind: 'length' };
        const a = A[i], b = B[j];
        const kind = a[PC] !== b[PC] ? 'instruction' : a[T0] !== b[T0] ? 'time' : a[T1] - a[T0] !== b[T1] - b[T0] ? 'duration' :
          a[READS].length !== b[READS].length || a[READS].some((r, k) => r[0] !== b[READS][k][0] || r[1] !== b[READS][k][1]) ? 'value' : null;
        if (kind) return { mine: i, other: j, kind };
        i++; j++;
      }
    }

    // Registers the decisions compare (the reads of their producers).
    function operandRegisters() {
      const regs = new Set();
      for (const site of sites.values()) if (site.cls === 'input') for (const reg of site.compared) regs.add(reg);
      return [...regs].sort((a, b) => a - b);
    }

    // Footprint of the decisions (class "input"), merged over sites.
    function footprint() {
      const merged = new Map();
      for (const site of sites.values()) {
        if (site.cls !== 'input') continue;
        for (const [reg, sides] of site.footprint) {
          const entry = merged.get(reg) || { taken: false, fall: false };
          entry.taken = entry.taken || sides.taken;
          entry.fall = entry.fall || sides.fall;
          merged.set(reg, entry);
        }
      }
      return new Map([...merged].sort((a, b) => a[0] - b[0]));
    }

    function scenarioRuns(sid) {
      const sc = data.scenarios.find(s => s.id === sid);
      return { base: sc.base, flips: sc.flips.slice() };
    }

    return { data, ins, sites, joins, run, partner, armDiff, why, counterfactual, firstRtDifference, operandRegisters, footprint,
      scenarioRuns, rtBlocks, stepsOf, textOf, lineOf, opOf, isCond, labelAt, END,
      index: { PC, Q0, Q1, T0, T1, READS, WRITES, SNAP, BR, NEXT, TAKEN, CALC, FORCED, FLAGS, TARGET, FALL, COMPARE } };
  }

  const api = { create, pathName, immediatePostDominators };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.q1lensDecisionLanesModel = api;
})(typeof window !== 'undefined' ? window : undefined);
