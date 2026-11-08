/* View projections only: instruction occurrences and times come from the engine. */
const ExecutionViews = (() => {
  function relatedPackets(step, packets) {
    return packets.filter(packet => packet.q1Id === step.id || Object.values(packet.setters).includes(step.id));
  }
  function instructionUnits(steps, projections, start = 0, end = steps.length - 1, excluded = []) {
    const units = [];
    for (let index = start; index <= end;) {
      const candidates = projections.filter(item => !excluded.includes(item.loop.id)).map(item => ({
        loop: item.loop, rows: item.rows.filter(row => row.start >= start && row.end <= end),
      })).filter(item => item.rows[0]?.start === index)
        .sort((a, b) => b.rows.at(-1).end - a.rows.at(-1).end);
      if (candidates.length) {
        const group = candidates[0];
        units.push({ kind: 'loop', ...group, key: `${group.loop.id}:${index}` });
        index = group.rows.at(-1).end + 1;
      } else units.push({ kind: 'instruction', index: index++ });
    }
    return units;
  }
  function timeWindow(steps, packets, step, focused) {
    const related = focused ? relatedPackets(step, packets) : packets;
    const starts = [...(focused ? [step.q1Start] : [0]), ...related.map(packet => packet.start)].filter(Number.isFinite);
    const ends = [...(focused ? [step.q1End] : steps.map(item => item.q1End)), ...related.map(packet => packet.end)].filter(Number.isFinite);
    const start = Math.min(...starts);
    const end = Math.max(start + 4, ...ends);
    const pad = Math.max(32, end - start) * .08;
    return { start: Math.max(0, start - (focused ? pad : 0)), end: end + pad };
  }
  function instructionLayout(steps, projections, expanded, selected, preferredLoop, iteration) {
    const blocks = [], ranges = [];
    const walk = (start, end, excluded = []) => {
      instructionUnits(steps, projections, start, end, excluded).forEach(unit => {
        if (unit.kind === 'instruction') { blocks.push(unit); return; }
        const row = unit.rows.find(row => selected >= row.start && selected <= row.end) ||
          unit.rows.find(row => unit.loop.id === preferredLoop && row.iteration === iteration) || unit.rows[0];
        const range = { ...unit, row, start: blocks.length, depth: excluded.length, expanded: expanded.has(unit.loop.id) };
        if (range.expanded) walk(row.start, row.end, [...excluded, unit.loop.id]);
        else blocks.push(unit);
        range.end = blocks.length;
        ranges.push(range);
      });
    };
    walk(0, steps.length - 1);
    return { blocks, ranges };
  }
  return { relatedPackets, instructionUnits, instructionLayout, timeWindow };
})();
if (typeof module !== 'undefined') module.exports = ExecutionViews;
