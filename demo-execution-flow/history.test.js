const { test } = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { projectLoop } = require('./history');
const { instructionUnits, instructionLayout, relatedPackets, timeWindow } = require('./views');

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

test('Q1 overview folds all loop visits into one group and retains the suffix', () => {
  const steps = run('bins').steps;
  const projections = item('bins').loops.map(loop => ({ loop, ...projectLoop(steps, loop) }));
  const units = instructionUnits(steps, projections);
  const groups = units.filter(unit => unit.kind === 'loop');
  assert.equal(groups.length, 1);
  assert.equal(groups[0].rows.length, 4);
  assert.equal(units.at(-1).index, steps.length - 1);
  assert.equal(steps[units.at(-1).index].op, 'stop');
});

test('nested Q1 groups contain only visits from the chosen outer iteration', () => {
  const steps = run('nested').steps;
  const projections = item('nested').loops.map(loop => ({ loop, ...projectLoop(steps, loop) }));
  const outer = instructionUnits(steps, projections).find(unit => unit.kind === 'loop');
  const row = outer.rows[0];
  const inner = instructionUnits(steps, projections, row.start, row.end, [outer.loop.id]).find(unit => unit.kind === 'loop');
  assert.equal(inner.loop.name, 'inner');
  assert.equal(inner.rows.length, 2);
  assert.ok(inner.rows.every(visit => visit.start >= row.start && visit.end <= row.end));
});

test('one-row loop layout selects the actual visit and retains surrounding instructions', () => {
  const steps = run('latched').steps;
  const projections = item('latched').loops.map(loop => ({ loop, ...projectLoop(steps, loop) }));
  const loop = projections[0].loop;
  const selected = steps.findIndex(step => step.op === 'set_awg_gain' && step.occurrence === 3);
  const layout = instructionLayout(steps, projections, new Set([loop.id]), selected);
  assert.equal(layout.ranges.length, 1);
  assert.equal(layout.ranges[0].row.iteration, 3);
  assert.ok(layout.blocks.some(block => block.index === selected));
  assert.equal(layout.blocks.at(-1).index, steps.length - 1);
  assert.ok(!layout.blocks.some(block => steps[block.index]?.op === 'set_awg_gain' && block.index !== selected));
  const folded = instructionLayout(steps, projections, new Set(), selected);
  assert.equal(folded.blocks.filter(block => block.kind === 'loop').length, 1);
  assert.equal(folded.blocks.at(-1).index, steps.length - 1);
});

test('nested ranges share a horizontal sequence without mixing outer visits', () => {
  const steps = run('nested').steps;
  const projections = item('nested').loops.map(loop => ({ loop, ...projectLoop(steps, loop) }));
  const selected = projections.find(item => item.loop.name === 'inner').rows.at(-1).start;
  const layout = instructionLayout(steps, projections, new Set(projections.map(item => item.loop.id)), selected);
  const outer = layout.ranges.find(range => range.loop.name === 'outer');
  const inner = layout.ranges.find(range => range.loop.name === 'inner');
  assert.equal(outer.row.iteration, 2);
  assert.equal(inner.depth, 1);
  assert.equal(inner.rows.length, 2);
  assert.ok(inner.start >= outer.start && inner.end <= outer.end);
  assert.ok(layout.blocks.some(block => block.index === selected));
});

test('latched setter connects to its actual applying RT packet, with stable selection across views', () => {
  const scenario = run('latched');
  const step = scenario.steps.filter(step => step.op === 'set_awg_gain')[2];
  const before = JSON.stringify(scenario);
  const packets = relatedPackets(step, scenario.rtPackets);
  assert.equal(packets.length, 1);
  assert.equal(packets[0].op, 'upd_param');
  assert.notEqual(packets[0].q1Id, step.id);
  assert.deepEqual(packets[0].settings.awg_gain, [3000, 0]);
  assert.ok(packets[0].start >= packets[0].issuedAt);
  const window = timeWindow(scenario.steps, scenario.rtPackets, step, true);
  assert.ok(window.start <= step.q1Start && window.end >= packets[0].end);
  assert.equal(JSON.stringify(scenario), before);
});

test('unexecuted RT packets never create fake time intervals', () => {
  const scenario = run('golden', 'no_measurement');
  assert.ok(scenario.rtPackets.some(packet => packet.start === null));
  const window = timeWindow(scenario.steps, scenario.rtPackets, scenario.steps[0], false);
  assert.ok(Number.isFinite(window.start) && Number.isFinite(window.end));
  assert.ok(window.end >= Math.max(...scenario.steps.map(step => step.q1End)));
});
