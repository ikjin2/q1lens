const { test } = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { projectLoop } = require('./history');

const cases = JSON.parse(execFileSync('python', ['-c', `
import json, sys
sys.path.insert(0, sys.argv[1])
import build
data = build.build_data()
program = build.parse_q1asm('move 2,R0\\nnop\\nouter: move 2,R1\\nnop\\ninner: nop\\nloop R1,@inner\\nloop R0,@outer\\nstop')
data['cases'].append({'id': 'nested', 'loops': build.loop_definitions(program), 'scenarios': [build.project_scenario('nested', program, {}, {'id': 'normal', 'title': 'nested', 'inputLabel': '', 'simulation': {}})]})
print(json.dumps(data['cases']))
`, __dirname], { encoding: 'utf8' }));
const item = id => cases.find(value => value.id === id);
const run = (id, scenario = 'normal') => item(id).scenarios.find(value => value.id === scenario);
const projection = (id, name, scenario) => projectLoop(run(id, scenario).steps, item(id).loops.find(loop => loop.name === name));

test('bin rows retain both increment and reset, including transient 3', () => {
  const { columns, rows } = projection('bins', 'loop_start');
  assert.equal(rows.length, 4);
  assert.deepEqual(columns.map(column => column.id), ['register:R0', 'register:R1']);
  const third = rows[2].cells['register:R0'].changes;
  assert.deepEqual([third[0].before, ...third.map(change => change.after)], [2, 3, 0]);
  assert.deepEqual(third.map(change => run('bins').steps[change.index].line), [13, 16]);
});

test('retry includes early exit values and excludes setters after leaving loop', () => {
  const { columns, rows } = projection('retry', 'retry');
  assert.equal(rows.length, 3);
  assert.equal(rows[1].cells['register:R1'].held.after, 0);
  assert.equal(rows[2].cells['register:R1'].changes[0].after, 1);
  assert.equal(rows[2].status, '루프 이탈');
  assert.equal(run('retry').steps[rows[2].end].line, 10);
  assert.ok(!columns.some(column => column.kind === 'latched'));
});

test('changed latched settings appear, constant registers and settings stay hidden', () => {
  assert.equal(run('latched').status, 'completed');
  const { columns, rows } = projection('latched', 'sweep');
  assert.equal(rows.length, 5);
  assert.deepEqual(columns.map(column => column.id), ['register:R0', 'register:R1', 'latched:awg_gain']);
  assert.deepEqual(rows.map(row => row.cells['latched:awg_gain'].changes[0].after), [1000, 2000, 3000, 4000, 5000].map(value => [value, 0]));
  assert.deepEqual(rows[1].cells['latched:awg_gain'].changes[0].before, [1000, 0]);
});

test('nested loop visits stay separate and calls outside an outer span remain partial', () => {
  assert.equal(projection('nested', 'outer').rows.length, 2);
  assert.equal(projection('nested', 'inner').rows.length, 4);
  const outer = projection('golden', 'check_conv', 'provided_measurement');
  assert.equal(outer.rows.length, 1);
  assert.equal(outer.rows[0].status, '부분 실행');
  assert.equal(outer.rows[0].end, run('golden', 'provided_measurement').steps.length - 1);
  const inner = projection('golden', 'wait_loop', 'provided_measurement');
  assert.equal(inner.rows.length, 15);
  assert.deepEqual(inner.columns.map(column => column.id), ['register:R31']);
});

test('register updates belong to availability time, not issue time', () => {
  const steps = [0, 1, 0, 1].map((pc, index) => ({ pc, line: pc + 1, op: pc ? 'loop' : 'nop', q1Start: index * 4, q1End: (index + 1) * 4, writes: [], latched: [], branch: pc ? { taken: index === 1 } : null }));
  steps[0].writes = [{ register: 'R0', before: 0, after: 1, readyAt: 12 }];
  const { rows } = projectLoop(steps, { startPc: 0, endPc: 1, endPcs: [1], startLine: 1, endLine: 2 });
  assert.equal(rows[0].cells['register:R0'].held, undefined);
  assert.equal(rows[1].cells['register:R0'].changes[0].index, 0);
});
