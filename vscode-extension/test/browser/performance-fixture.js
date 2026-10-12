// Deterministic synthetic mixed-event IR, not sampled hardware data.
function performanceFixture(sequencerCount, totalEvents = 10000) {
  const concrete = (value) => ({ kind: "concrete", value });
  const ir = { sequencers: [], events: [], register_history: [], execution: {}, simulation: {}, alignment: {} };
  const cycles = Array(sequencerCount).fill(0);
  for (let n = 0; n < sequencerCount; n++) {
    const id = `seq${n}`;
    ir.sequencers.push({ id, module: `module${Math.floor(n / 6)}` });
    ir.execution[id] = { mode: "simulation", status: "completed", timing_model: "event_driven" };
    ir.simulation[id] = { queue_capacity: 32 };
    ir.alignment[id] = { offset: 0, confidence: "exact" };
  }
  for (let group = 0; ir.events.length < totalEvents; group++) {
    const seq = group % sequencerCount, id = `seq${seq}`, cycle = cycles[seq]++, start = cycle * 16;
    const source = (line) => ({ file: `${id}.q1asm`, line, column: 1 });
    const qmove = `${id}:move:${cycle}`, qset = `${id}:set:${cycle}`, setter = `${id}:pending:${cycle}`, packet = `${id}:packet:${cycle}`;
    const event = (suffix, kind, t0, t1, line, meta = {}) => ({
      id: `${id}:${suffix}:${cycle}`, sequencer_id: id, kind, t0: concrete(t0), t1: concrete(t1),
      source: source(line), confidence: "exact", lane: `rt.${kind}`, meta,
    });
    const records = [
      event("move", "q1_issue", start, start + 4, 1, { op: "move", execution_index: cycle * 3 }),
      event("set", "q1_issue", start + 4, start + 8, 2, { op: "set_freq", execution_index: cycle * 3 + 1 }),
      event("pending", "latched_state_pending", start + 8, start + 8, 2, { field: "frequency", value: cycle, q1_issue_event_id: qset }),
      event("issue", "q1_issue", start + 8, start + 12, 3, { op: "upd_param", rt_packet_id: packet, execution_index: cycle * 3 + 2 }),
      event("enq", "rt_queue_enqueue", start + 12, start + 12, 3, { depth: 1, capacity: 32, rt_packet_id: packet }),
      event("deq", "rt_queue_dequeue", start + 200, start + 200, 3, { depth: 0, capacity: 32, rt_packet_id: packet }),
      event("upd", "upd_param", start + 200, start + 204, 3, { rt_packet_id: packet, applied_state: { frequency: cycle }, applied_setter_event_ids: { frequency: setter } }),
      event("acq", "acquire", start + 204, start + 208, 4, { acquisition_index: 0, bin_index: cycle % 200, rt_packet_id: `${packet}:acq` }),
      event("result", "acquisition_result", start + 208, start + 208, 4, { acquisition_index: 0, bin_index: cycle % 200, i: cycle, q: -cycle, thresholded: cycle % 2, source: { kind: "scenario" }, rt_packet_id: `${packet}:acq` }),
      event("feedback", "feedback_receive", start + 208, start + 208, 5, { id: 5, value: cycle }),
    ];
    ir.events.push(...records.slice(0, totalEvents - ir.events.length));
    ir.register_history.push({ sequencer_id: id, register: `R${cycle % 64}`, time: concrete(start + 8), value: concrete(cycle), event_id: qmove, source: source(1), meta: {} });
  }
  return { ir, horizon: Math.max(...cycles) * 16 + 216 };
}
module.exports = { performanceFixture };
