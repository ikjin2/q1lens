/* Sequential register semantics for the standalone interaction demo.
 * This does not model Q1 pipeline hazards, RT queues, or physical acquisition.
 */
(function (root) {
  'use strict';
  const u32 = value => Number(BigInt.asUintN(32, BigInt(value)));
  const s32 = value => Number(BigInt.asIntN(32, BigInt(value)));
  const s16 = value => Number(BigInt.asIntN(16, BigInt(value)));

  function parse(source, params = {}) {
    const instructions = [], labels = {}, symbols = { ...params }, aliases = {};
    source.split(/\r?\n/).forEach((raw, index) => {
      let code = raw.split('#')[0].trim();
      if (!code) return;
      const def = code.match(/^\.DEF\s+(\w+)\s+(.+)$/i);
      if (def) {
        symbols[def[1]] = def[2].trim();
        if (/^R\d+$/.test(def[2].trim())) aliases[def[2].trim()] = def[1];
        return;
      }
      const label = code.match(/^(\w+):/);
      if (label) { labels[label[1]] = instructions.length; code = code.slice(label[0].length).trim(); }
      if (!code) return;
      const [, op, tail = ''] = code.match(/^(\S+)(?:\s+(.*))?$/);
      instructions.push({ pc: instructions.length, line: index + 1, raw, op, args: tail.split(',').map(a => a.trim()).filter(Boolean) });
    });
    function resolve(arg, seen = new Set()) {
      const key = arg.replace(/^\$/, '');
      if (key in symbols) {
        if (seen.has(key)) throw new Error(`Alias cycle: ${key}`);
        seen.add(key);
        return resolve(String(symbols[key]), seen);
      }
      return arg;
    }
    for (const instruction of instructions) instruction.args = instruction.args.map(a => resolve(a));
    const loopMap = new Map();
    for (const instruction of instructions) {
      if (!/^(jmp|loop|j\w+)$/.test(instruction.op)) continue;
      const target = instruction.args.at(-1);
      if (!target?.startsWith('@') || !(target.slice(1) in labels)) continue;
      const start = labels[target.slice(1)];
      if (start <= instruction.pc) {
        const name = target.slice(1), existing = loopMap.get(name);
        loopMap.set(name, { name, start, end: Math.max(existing?.end ?? start, instruction.pc) });
      }
    }
    return { instructions, labels, aliases, loops: [...loopMap.values()].sort((a, b) => a.start - b.start) };
  }

  function execute(program, inputs = [], maxSteps = 20000) {
    const events = [], registers = {}, writers = {};
    let pc = 0, flags = null, flagWriter = null, control = null, inputIndex = 0, status = 'complete', message = '';
    const snapshot = () => ({ values: { ...registers }, writers: { ...writers } });
    for (let step = 0; step < maxSteps; step++) {
      const instruction = program.instructions[pc];
      if (!instruction) { status = 'blocked'; message = `No instruction at PC ${pc}`; break; }
      const event = { ...instruction, id: events.length, reads: [], writes: [], parents: [], control, before: snapshot(), next: pc + 1 };
      const read = arg => {
        if (/^R\d+$/.test(arg)) {
          if (!(arg in registers)) throw new Error(`${arg} has no value`);
          const value = registers[arg], writer = writers[arg];
          event.reads.push({ register: arg, value, writer });
          if (writer !== undefined) event.parents.push(writer);
          return value;
        }
        if (arg.startsWith('@')) {
          if (!(arg.slice(1) in program.labels)) throw new Error(`Unknown label ${arg}`);
          return program.labels[arg.slice(1)];
        }
        if (!/^-?(?:\d+|0x[\da-f]+)$/i.test(arg)) throw new Error(`Unresolved operand ${arg}`);
        return u32(BigInt(arg));
      };
      const write = (register, value) => {
        if (!/^R(?:[0-5]?\d|6[0-3])$/.test(register)) throw new Error(`Invalid destination ${register}`);
        const next = u32(value);
        event.writes.push({ register, before: registers[register], value: next });
        registers[register] = next; writers[register] = event.id;
      };
      const setFlags = value => { flags = { zero: u32(value) === 0, negative: s32(value) < 0, overflow: false }; flagWriter = event.id; };
      const compare = (left, right) => {
        const result = u32(BigInt(left) - BigInt(right));
        flags = { zero: result === 0, negative: s32(result) < 0, overflow: ((left ^ right) & (left ^ result) & 0x80000000) !== 0 };
        flagWriter = event.id;
      };
      const [a, b, c, d] = instruction.args;
      try {
        switch (instruction.op) {
          case 'move': write(b, read(a)); break;
          case 'add': { const x = read(a), y = read(b), value = u32(BigInt(x) + BigInt(y)); write(c, value); setFlags(value); break; }
          case 'sub': { const x = read(a), y = read(b); write(c, BigInt(x) - BigInt(y)); compare(x, y); break; }
          case 'asr': { const x = read(a), shift = read(b); const value = s32(x) >> (shift & 31); write(c, value); setFlags(value); break; }
          case 'muls16': { const x = s16(read(a)), y = s16(read(b)), value = x * y; write(c, value); setFlags(value); break; }
          case 'muls32': {
            const x = BigInt(s32(read(a))), y = BigInt(s32(read(b))), value = x * y;
            write(c, value); write(d, value >> 32n); setFlags(value);
            flags.zero = value === 0n; flags.negative = value < 0n;
            break;
          }
          case 'cmp': compare(read(a), read(b)); break;
          case 'fb_pop_data': {
            const channel = read(a);
            if (inputIndex >= inputs.length) {
              event.blocked = true; status = 'input_required'; message = `Feedback #${inputIndex + 1} (channel ${channel}) needs a value.`;
            } else {
              event.input = { index: inputIndex, channel, value: u32(inputs[inputIndex]), kind: 'synthetic' };
              write(b, inputs[inputIndex++]);
            }
            break;
          }
          case 'jmp': event.next = read(a); break;
          case 'loop': {
            const value = u32(BigInt(read(a)) - 1n); write(a, value);
            event.taken = value !== 0; event.condition = `${a} − 1 = ${s32(value)} ≠ 0`;
            event.next = event.taken ? read(b) : pc + 1; control = event.id;
            break;
          }
          case 'jl': case 'jlt': case 'jge': case 'jnz': case 'jz': {
            let taken;
            if (instruction.args.length === 3) {
              const left = s32(read(a)), right = s32(read(b));
              taken = instruction.op === 'jge' ? left >= right : left < right;
              event.condition = `${left} ${instruction.op === 'jge' ? '≥' : '<'} ${right}`;
            } else {
              if (!flags) throw new Error('No resolved ALU flags');
              event.parents.push(flagWriter); event.flagWriter = flagWriter;
              taken = instruction.op === 'jnz' ? !flags.zero : instruction.op === 'jz' ? flags.zero : instruction.op === 'jge' ? flags.negative === flags.overflow : flags.negative !== flags.overflow;
              event.condition = `ZF=${+flags.zero} · NF=${+flags.negative} · OF=${+flags.overflow}`;
            }
            event.taken = taken; event.next = taken ? read(instruction.args.at(-1)) : pc + 1; control = event.id;
            break;
          }
          case 'nop': break;
          case 'stop': event.stop = true; break;
          case 'fb_acq_iq_id': case 'fb_acq_iq_shift':
            event.configuration = instruction.args.map(read); break;
          case 'set_awg_gain': case 'set_awg_offs': case 'set_freq': case 'set_mrk':
            event.setting = instruction.args.map(read); break;
          case 'wait': case 'wait_sync': case 'upd_param': case 'play': case 'acquire':
            event.action = instruction.args.map(read); break;
          default: throw new Error(`Unsupported instruction: ${instruction.op}`);
        }
      } catch (error) { status = 'blocked'; message = `L${instruction.line}: ${error.message}`; event.blocked = true; }
      event.parents = [...new Set(event.parents)];
      event.after = snapshot(); events.push(event);
      if (event.blocked || event.stop) break;
      pc = event.next;
      if (step === maxSteps - 1) { status = 'step_limit'; message = `Stopped at the ${maxSteps} instruction demo limit.`; }
    }
    return { events, status, message, inputCount: inputIndex, final: snapshot() };
  }

  function iterations(trace, loop) {
    const rows = [];
    let current = null;
    function finish(event, exit) {
      current.end = event.id; current.after = event.after; current.exit = exit;
      current.events = trace.events.slice(current.start, current.end + 1);
      rows.push(current); current = null;
    }
    for (const event of trace.events) {
      if (event.pc === loop.start) {
        if (current) finish(trace.events[event.id - 1], false);
        current = { index: rows.length, start: event.id, before: event.before };
      }
      if (!current) continue;
      const backedge = event.pc <= loop.end && event.pc >= loop.start && (event.next === loop.start || (event.pc === loop.end && event.taken === false));
      const exit = event.taken === true && event.pc >= loop.start && event.pc <= loop.end && event.next > loop.end;
      if (backedge || exit || event.stop || event.blocked) finish(event, exit || (backedge && event.taken === false) || event.stop || event.blocked);
    }
    if (current) { finish(trace.events.at(-1), true); rows.at(-1).partial = true; }
    return rows;
  }

  function causes(trace, id, includeControl = false) {
    const found = new Set();
    function visit(nodeId) {
      if (found.has(nodeId) || nodeId == null) return;
      found.add(nodeId);
      const event = trace.events[nodeId];
      event.parents.forEach(visit);
      if (includeControl && event.control != null) visit(event.control);
    }
    visit(id); return found;
  }
  const api = { parse, execute, iterations, causes, s32, u32 };
  if (typeof module !== 'undefined') module.exports = api;
  else root.ExecutionTrace = api;
})(typeof window === 'undefined' ? globalThis : window);
