/* Show changes recorded by the engine during each visit to a loop. */
function projectLoop(steps, loop) {
  if (!loop) return { columns: [], rows: [] };
  const entries = steps.flatMap((step, index) => step.pc === loop.startPc ? [index] : []);
  const changes = steps.flatMap((step, index) => [
    ...step.writes.map(write => ({ kind: 'register', key: write.register, before: write.before, after: write.after, at: write.readyAt, index })),
    ...(step.latched || []).map(write => ({ kind: 'latched', key: write.field, before: write.before, after: write.after, at: write.readyAt, index })),
  ]).filter(change => Number.isFinite(change.at)).sort((a, b) => a.at - b.at || a.index - b.index);
  const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  let previousEnd = -Infinity;
  const rows = entries.map((start, iteration) => {
    let end = (entries[iteration + 1] ?? steps.length) - 1;
    let status = '부분 실행';
    for (let index = start; index <= end; index++) {
      const step = steps[index];
      if (loop.endPcs.includes(step.pc)) {
        end = index;
        status = step.branch?.taken ? '다음 회차' : '루프 종료';
        break;
      }
      const exits = step.pc >= loop.startPc && step.pc <= loop.endPc && step.branch && step.op !== 'jmp' && step.nextLine != null &&
        (step.nextLine < loop.startLine || step.nextLine > loop.endLine);
      if (exits || step.op === 'stop') {
        end = index;
        status = '루프 이탈';
        break;
      }
    }
    const endTime = steps[end].q1End;
    const cells = {};
    for (const change of changes) {
      if (change.at < steps[start].q1Start || change.at > endTime ||
          (change.at <= previousEnd && change.index < start) || equal(change.before, change.after)) continue;
      const id = `${change.kind}:${change.key}`;
      const cell = cells[id] ||= { kind: change.kind, key: change.key, changes: [] };
      cell.changes.push(change);
    }
    previousEnd = endTime;
    const branch = steps.slice(start, end + 1).findIndex(step => step.branch && !['loop', 'jmp'].includes(step.op));
    return { start, end, focusIndex: branch < 0 ? end : start + branch, iteration: iteration + 1, status, cells,
      assumed: steps.slice(0, end + 1).some(step => step.branch?.overridden) };
  });
  const keys = [...new Set(rows.flatMap(row => Object.keys(row.cells)))];
  const columns = keys.filter(id => {
    const values = rows.flatMap(row => row.cells[id]?.changes || []);
    return values.some(change => change.before != null && !equal(change.before, change.after)) ||
      new Set(values.map(change => JSON.stringify(change.after))).size > 1 || values[0]?.kind === 'register';
  }).map(id => ({ id, kind: id.split(':')[0], key: id.split(':')[1] }))
    .sort((a, b) => a.kind !== b.kind ? a.kind === 'register' ? -1 : 1 :
      a.kind === 'register' ? Number(a.key.slice(1)) - Number(b.key.slice(1)) : a.key.localeCompare(b.key));
  for (const row of rows) {
    for (const column of columns) {
      if (row.cells[column.id]) continue;
      const held = changes.findLast(change => `${change.kind}:${change.key}` === column.id && change.at <= steps[row.end].q1End);
      row.cells[column.id] = { changes: [], held };
    }
  }
  return { columns, rows };
}
if (typeof module !== 'undefined') module.exports = { projectLoop };
