const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const T = require('./trace.js');

function example(relative, inputs) {
  const file = path.join(__dirname, '../examples', relative);
  const paramsFile = path.join(path.dirname(file), 'params.json');
  const program = T.parse(fs.readFileSync(file, 'utf8'), fs.existsSync(paramsFile) ? JSON.parse(fs.readFileSync(paramsFile, 'utf8')) : {});
  const trace = T.execute(program, inputs);
  return { program, trace, rows: T.iterations(trace, program.loops[0]) };
}
const retry = inputs => example('q1asm-tutorials/11-control-flow-patterns/35-bounded-retry-loop/seq0.q1asm', inputs);
const bins = () => example('q1asm-tutorials/10-stateful-programs/32-round-robin-bin-writer/seq0.q1asm', []);
const golden = () => example('golden-section/golden_section.q1asm', Array.from({ length: 16 }, (_, i) => i % 2 ? [4000000,1000000,2000000,1000000] : [2000000,1000000,4000000,1000000]).flat());

test('retry records real iteration exits, early acceptance, and input origin', () => {
  const { program, trace, rows } = retry([0,0,1]);
  assert.equal(trace.status, 'complete');
  assert.equal(program.aliases.R0, 'ATTEMPT');
  assert.deepEqual(rows.map(row => row.after.values.R0), [2,1,1]);
  assert.deepEqual(rows.map(row => row.after.values.R1), [0,0,1]);
  assert.equal(rows[2].exit, true);
  const writer = trace.events[rows[2].after.writers.R1];
  assert.equal(writer.input.index, 2);
  assert.equal(writer.input.value, 1);
  assert.equal(rows[2].events.at(-1).taken, true);
  assert.equal(retry([1]).rows.length, 1);
});

test('missing feedback blocks at its receipt without inventing a value', () => {
  const { trace, rows } = retry([0,0]);
  assert.equal(trace.status, 'input_required');
  assert.equal(trace.inputCount, 2);
  assert.equal(trace.events.at(-1).op, 'fb_pop_data');
  assert.equal(trace.events.at(-1).writes.length, 0);
  assert.equal(rows[2].events.at(-1).blocked, true);
  assert.equal(trace.final.values.R1, 0);
});

test('one loop projection handles bin wrap and loop counter completion', () => {
  const { trace, rows } = bins();
  assert.equal(trace.status, 'complete');
  assert.deepEqual(rows.map(row => row.after.values.R0), [1,2,0,1]);
  assert.deepEqual(rows.map(row => row.after.values.R1), [3,2,1,0]);
  assert.equal(rows.length, 4);
  assert.equal(rows[3].exit, true);
});

test('provenance uses the writer occurrence read before a same-register write', () => {
  const { trace } = bins();
  const adds = trace.events.filter(event => event.op === 'add');
  assert.equal(adds[1].reads[0].value, 1);
  assert.equal(adds[1].reads[0].writer, adds[0].id);
  assert.equal(adds[1].before.writers.R0, adds[0].id);
  assert.equal(adds[1].after.writers.R0, adds[1].id);
  assert.notEqual(adds[0].after.writers.R0, adds[1].after.writers.R0);
});

test('golden section executes actual integer probes, product words and convergence', () => {
  const { program, trace, rows } = golden();
  assert.equal(trace.status, 'complete');
  assert.equal(T.s32(rows[0].after.values.R8), -944);
  assert.equal(T.s32(rows[0].after.values.R9), 944);
  assert.equal(rows[0].after.values.R16, 1163);
  assert.equal(rows[0].after.values.R17, 3957);
  assert.equal(T.s32(rows[0].after.values.R5), 944);
  assert.equal(rows.at(-1).exit, true);
  assert.ok(rows.at(-1).after.values.R6 < 20);
  const inputIndices = [...T.causes(trace, rows[0].after.writers.R16)].map(id => trace.events[id].input?.index).filter(index => index !== undefined);
  assert.deepEqual(inputIndices.sort(), [0,1]);
  const inner = T.iterations(trace, program.loops.find(loop => loop.name === 'wait_loop'));
  assert.ok(inner.length > 15);
  assert.ok(inner.every(row => row.events.length === 3));
  assert.deepEqual(inner.slice(0,15).map(row => row.after.values.R31), Array.from({length:15},(_, i)=>14-i));
  assert.equal(inner[14].exit, true);
  console.log(`Golden: ${rows.length} checkpoints, ${inner.length} inner iterations, ${trace.events.length} events.`);
});

test('signed compare honors overflow, and unsupported operations stop', () => {
  const program = T.parse('move -2147483648,R0\ncmp R0,1\njl @less\nmove 0,R1\nstop\nless:\nmove 1,R1\nstop');
  assert.equal(T.execute(program).final.values.R1, 1);
  assert.equal(T.execute(T.parse('unknown_op R0')).status, 'blocked');
  assert.equal(T.execute(T.parse('move R2,R0')).status, 'blocked');
});
