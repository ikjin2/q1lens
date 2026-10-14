(function hardwareStateFactory(root) {
  const SIMULATION_EVENTS = new Set(["rt_queue_enqueue", "rt_queue_dequeue", "q1_stall", "feedback_receive", "feedback_send", "trigger_receive", "acquisition_result", "conditional_skip", "sync_wait", "trigger_wait"]);
  function numericValue(value) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (value && typeof value === "object" && (!value.kind || value.kind === "concrete")) {
      if (Number.isFinite(value.value)) return value.value;
      if (Number.isFinite(value.value_ns)) return value.value_ns;
    }
    return undefined;
  }

  function displayValue(value) {
    if (value === undefined || value === null) return "unknown";
    if (Array.isArray(value)) return value.map(displayValue).join(", ");
    if (typeof value === "object") return String(value.display ?? value.value ?? value.expr ?? "unknown");
    return String(value);
  }

  function knownValue(value) {
    return Array.isArray(value) ? value.every(knownValue) : numericValue(value) !== undefined;
  }

  function upperBound(items, time, inclusive = true) {
    let low = 0;
    let high = items.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (items[middle].time < time || (inclusive && items[middle].time === time)) low = middle + 1;
      else high = middle;
    }
    return low;
  }

  function latest(items, time) {
    return items[upperBound(items, time) - 1];
  }

  function sourceKey(source) {
    return source ? `${source.file}:${source.line}:${source.column || 1}` : "";
  }

  function isPreview(event) {
    const meta = event.meta || {};
    return Boolean(meta.preview_source_event_id || meta.branch_comparison_path || meta.comparison_branch_id || meta.branch_path);
  }

  function eventTime(index, event, basis = "local", edge = "t0") {
    if (!event) return undefined;
    const alignment = index.alignment[event.sequencer_id] || {};
    if (basis === "aligned") {
      if (alignment.unaligned || alignment.confidence === "unknown" || (alignment.offset !== undefined && numericValue(alignment.offset) === undefined)) return undefined;
      const explicit = numericValue((event.meta || {})[`aligned_${edge}`]);
      if (explicit !== undefined) return explicit;
    }
    const local = numericValue(event[edge]) ?? numericValue(event[`${edge}_ns`]);
    const offset = basis === "aligned" ? numericValue(alignment.offset) ?? 0 : 0;
    return local === undefined ? undefined : local + offset;
  }

  function record(event, time, value) {
    return { time, value, display: displayValue(value), known: knownValue(value), source: event.source, eventId: event.id, event };
  }

  function createSeries() {
    return {
      q1: [], rt: [], registers: new Map(), registerWrites: new Map(), pending: new Map(), applied: new Map(),
      awg: new Map(), acquisitions: [], bins: new Map(), binOrder: [],
      queue: [], stalls: [], rtWaits: [], communications: [], acquisitionResults: new Map(), resultEvents: [],
      q1Risks: [], rtRisks: [], warnings: new Set(), steps: [], range: { min: 0, max: 0 },
    };
  }

  function append(map, key, item) {
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(item);
  }

  function addRisk(series, event, time, clock, message, inclusive = false) {
    if (time !== undefined) series[`${clock}Risks`].push({ time, message, event, inclusive });
    else series.warnings.add(message);
  }

  function buildSeries(index, events, history, basis) {
    const series = createSeries();
    const issuesBySource = new Map();
    const pendingOccurrences = new Map();
    const pendingFields = new Map();
    const pendingById = new Map();
    const appliedPackets = new Set();
    let lastQ1Time;
    let lastRtTime;
    const usableEvents = events.filter((event) => !isPreview(event));
    for (const event of usableEvents) {
      if (event.kind === "q1_issue") append(issuesBySource, sourceKey(event.source), event);
    }
    for (const event of usableEvents) {
      const meta = event.meta || {};
      const time = eventTime(index, event, basis);
      const end = eventTime(index, event, basis, "t1");
      if (time !== undefined) series.steps.push(time);
      if (end !== undefined) series.steps.push(end);
      if (event.confidence === "assumed" || meta.assumed_branch_path) {
        series.warnings.add("State follows the analyzer's branch assumptions.");
      }
      if (event.kind === "underflow_warning") {
        series.warnings.add(meta.simulated ? `Simulation reports underflow${meta.reason ? `: ${meta.reason}` : ""}.` : `Analyzer reports ${String(meta.status || event.label || "underflow").replaceAll("_", " ")}; displayed times are analysis estimates.`);
      }
      if (event.kind === "latched_state_pending") {
        // Pending events use the RT cursor in the IR. Their setter executes on Q1.
        const key = sourceKey(event.source);
        const occurrence = pendingOccurrences.get(key) || 0;
        const issue = index.eventsById.get(String(meta.q1_issue_event_id)) || (issuesBySource.get(key) || [])[occurrence];
        pendingOccurrences.set(key, occurrence + 1);
        const setTime = eventTime(index, issue, basis, "t1");
        if (setTime !== undefined) {
          const item = record(event, setTime, meta.value);
          item.issueEventId = issue.id;
          append(series.pending, meta.field, item);
          pendingFields.set(meta.field, item);
          pendingById.set(String(event.id), item);
          series.steps.push(setTime);
        } else {
          series.warnings.add("Pending parameter timing is unavailable without its Q1 core event.");
        }
      }
      const rtCommand = (String(event.lane || "").startsWith("rt.") && meta.rt_packet_id && event.kind !== "marker_state" && (!SIMULATION_EVENTS.has(event.kind) || event.kind === "conditional_skip")) || event.kind === "stop";
      if (time === undefined) {
        const message = "Events with symbolic or unknown times cannot be placed at this cursor.";
        series.warnings.add(message);
        if (event.kind === "q1_issue") addRisk(series, event, lastQ1Time, "q1", message, true);
        if (rtCommand) addRisk(series, event, lastRtTime, "rt", message, true);
        continue;
      }
      const entry = { time, end, event };
      if (event.kind === "rt_queue_enqueue" || event.kind === "rt_queue_dequeue") series.queue.push(entry);
      if (event.kind === "q1_stall") series.stalls.push(entry);
      if (event.kind === "sync_wait" || event.kind === "trigger_wait") series.rtWaits.push(entry);
      if (SIMULATION_EVENTS.has(event.kind) && !event.kind.startsWith("rt_queue_")) series.communications.push(entry);
      if (event.kind === "acquisition_result") {
        series.resultEvents.push(entry);
        append(series.acquisitionResults, `${meta.acquisition_index}:${meta.bin_index}`, entry);
      }
      if (event.kind === "q1_issue") {
        series.q1.push(entry);
        lastQ1Time = end ?? time;
      }
      if (rtCommand) {
        series.rt.push(entry);
        lastRtTime = end ?? time;
      }
      const applied = meta.applied_state || (event.kind === "latched_state_applied" && meta.field ? { [meta.field]: meta.value } : {});
      for (const [field, value] of Object.entries(applied)) {
        const packetKey = `${meta.rt_packet_id || event.id}:${field}`;
        if (appliedPackets.has(packetKey)) continue;
        appliedPackets.add(packetKey);
        const item = record(event, time, value);
        const setterIds = meta.applied_setter_event_ids;
        const pending = setterIds ? pendingById.get(String(setterIds[field])) : pendingFields.get(field);
        if (pending) {
          // A queued packet can apply an older setter while Q1 has already
          // written a newer pending value for the same field.
          pending.consumedAt = pending.consumedAt === undefined ? time : Math.min(time, pending.consumedAt);
          item.issueEventId = pending.issueEventId;
          item.setterEventId = pending.eventId;
          item.appliedAt = pending.consumedAt;
          if (pendingFields.get(field) === pending) pendingFields.delete(field);
        }
        append(series.applied, field, item);
      }
      if (event.kind === "play") append(series.awg, meta.path ?? 0, entry);
      if (event.kind === "acquire") {
        series.acquisitions.push(entry);
        const acquisitionIndex = numericValue(meta.acquisition_index);
        const binIndex = numericValue(meta.bin_index);
        if (acquisitionIndex !== undefined && binIndex !== undefined) {
          const key = `${acquisitionIndex}:${binIndex}`;
          if (!series.bins.has(key)) series.bins.set(key, { acquisitionIndex, binIndex, history: [] });
          series.bins.get(key).history.push(entry);
        } else {
          series.warnings.add("Some acquisition targets are unresolved.");
        }
      }
      if (event.kind === "loop_block" && meta.count !== 1) {
        const firstIds = meta.first_iteration_event_ids || [];
        const firstIssues = firstIds.map((id) => index.eventsById.get(String(id))).filter((item) => item && item.kind === "q1_issue");
        const issueEnds = firstIssues.map((item) => eventTime(index, item, basis, "t1")).filter(Number.isFinite);
        const loopIssue = (issuesBySource.get(sourceKey(meta.source_end)) || [])[0];
        const q1End = eventTime(index, loopIssue, basis, "t1") ?? (issueEnds.length ? Math.max(...issueEnds) : undefined);
        const message = "Collapsed loop iterations are not simulated; state and acquisition counts may be incomplete.";
        addRisk(series, event, end, "rt", message);
        addRisk(series, event, q1End, "q1", message);
      }
      if (event.kind === "unknown_region" || (event.kind === "branch_region" && (end === undefined || meta.comparison_paths))) {
        const message = "Execution after an unresolved region is unknown.";
        addRisk(series, event, time, "rt", message, true);
        if (event.kind === "branch_region") {
          const issue = (issuesBySource.get(sourceKey(event.source)) || [])[0];
          addRisk(series, event, eventTime(index, issue, basis, "t1"), "q1", message, true);
        }
      }
    }
    for (const item of history) {
      if (isPreview(item)) continue;
      const alignment = index.alignment[item.sequencer_id] || {};
      if (basis === "aligned" && (alignment.unaligned || alignment.confidence === "unknown" || (alignment.offset !== undefined && numericValue(alignment.offset) === undefined))) continue;
      const local = numericValue(item.time);
      const offset = basis === "aligned" ? numericValue(alignment.offset) ?? 0 : 0;
      if (local === undefined) {
        series.warnings.add("Some register writes have unknown availability times.");
        const register = String(item.register).toUpperCase();
        const previous = series.registers.get(register) || [];
        const boundary = lastQ1Time ?? (previous.length ? previous[previous.length - 1].time : undefined);
        if (boundary !== undefined) {
          const entry = record({ id: item.event_id, source: item.source }, boundary, { kind: "unknown", display: "unknown (write time unresolved)" });
          entry.confidence = "unknown";
          entry.meta = { ...item.meta, invalidated: true, reason: "Register write availability is unresolved." };
          append(series.registers, register, entry);
        }
        continue;
      }
      const time = local + offset;
      const event = index.eventsById.get(String(item.event_id)) || { id: item.event_id, source: item.source };
      const entry = record(event, time, item.value);
      entry.source = item.source;
      entry.localTime = local;
      entry.meta = item.meta || {};
      entry.confidence = item.confidence;
      entry.register = String(item.register).toUpperCase();
      append(series.registers, String(item.register).toUpperCase(), entry);
      series.registerWrites.set(`${String(item.register).toUpperCase()}:${item.event_id}`, entry);
      series.steps.push(time);
    }
    const sort = (items) => items.sort((left, right) => left.time - right.time);
    for (const key of ["q1", "rt", "acquisitions", "q1Risks", "rtRisks", "queue", "stalls", "rtWaits", "communications", "resultEvents"]) sort(series[key]);
    for (const key of ["registers", "pending", "applied", "awg", "acquisitionResults"]) for (const items of series[key].values()) sort(items);
    for (const bin of series.bins.values()) {
      sort(bin.history);
      series.binOrder.push({ time: bin.history[0].time, bin });
    }
    sort(series.binOrder);
    series.steps = Array.from(new Set(series.steps)).sort((a, b) => a - b);
    if (series.steps.length) series.range = { min: series.steps[0], max: series.steps[series.steps.length - 1] };
    return series;
  }

  function buildExecutionTrace(index, sequencerId, events, series, basis) {
    const unavailable = (reason) => ({ instructions: [], sourceLines: [], reason });
    const execution = index.execution[sequencerId];
    if (!execution || !["concrete", "simulation"].includes(execution.mode)) return unavailable("No execution trace is recorded. Generate a concrete or simulation trace to replay instructions.");
    if (!index.hasRegisterHistory) return unavailable("Register history is unavailable. Generate a new execution trace to inspect registers.");
    const issues = events.filter((event) => event.kind === "q1_issue" && !isPreview(event));
    if (!issues.length) return unavailable("No recorded instructions. Refresh in concrete or simulation execution mode.");
    issues.sort((left, right) => (left.meta || {}).execution_index - (right.meta || {}).execution_index);
    const writesByEvent = new Map();
    for (const write of series.registerWrites.values()) append(writesByEvent, String(write.eventId), write);
    const instructions = [];
    const sourceLines = new Map();
    for (const [position, event] of issues.entries()) {
      if ((event.meta || {}).execution_index !== position) return unavailable("Instruction order is incomplete or ambiguous. Refresh in concrete or simulation execution mode.");
      const time = eventTime(index, event, basis);
      const end = eventTime(index, event, basis, "t1");
      if (time === undefined || end === undefined) return unavailable(basis === "aligned" ? "Instruction timing is unavailable in Aligned time. Select Local time or refresh the execution trace." : "Instruction timing is unresolved. Refresh the concrete or simulation execution trace.");
      const previous = instructions[instructions.length - 1];
      if (end < time || previous && (time < previous.time || end < previous.end)) return unavailable("Recorded instruction timing is inconsistent. Refresh the concrete or simulation execution trace.");
      instructions.push({ event, time, end, writes: writesByEvent.get(String(event.id)) || [] });
      const source = event.source || {};
      if (source.file && Number.isInteger(source.line) && source.line > 0) sourceLines.set(`${source.file}:${source.line}`, { file: source.file, line: source.line, raw: String(source.raw || event.label || event.meta.op || "") });
    }
    return { instructions, sourceLines: [...sourceLines.values()].sort((left, right) => left.file.localeCompare(right.file) || left.line - right.line), reason: null };
  }

  function createIndex(ir) {
    const events = Array.isArray(ir.events) ? ir.events : [];
    const history = Array.isArray(ir.register_history) ? ir.register_history : [];
    const sequencers = new Map((ir.sequencers || []).map((item) => [String(item.id), { ...item, id: String(item.id) }]));
    const eventsBySequencer = new Map();
    const historyBySequencer = new Map();
    for (const event of events) {
      const id = String(event.sequencer_id);
      if (!sequencers.has(id)) sequencers.set(id, { id });
      append(eventsBySequencer, id, event);
    }
    for (const item of history) append(historyBySequencer, String(item.sequencer_id), item);
    const index = { sequencers: Array.from(sequencers.values()), bySequencer: new Map(), eventsById: new Map(events.map((event) => [String(event.id), event])), alignment: ir.alignment || {}, execution: ir.execution || {}, simulation: ir.simulation || {}, hasRegisterHistory: Array.isArray(ir.register_history), signalStages: new Map() };
    for (const { id } of index.sequencers) {
      index.signalStages.set(id, buildSignalStages((index.simulation[id] || {}).signals));
      const local = buildSeries(index, eventsBySequencer.get(id) || [], historyBySequencer.get(id) || [], "local");
      const aligned = buildSeries(index, eventsBySequencer.get(id) || [], historyBySequencer.get(id) || [], "aligned");
      if (!Array.isArray(ir.register_history)) {
        local.warnings.add("Register history is unavailable; refresh the analysis to inspect registers.");
        aligned.warnings.add("Register history is unavailable; refresh the analysis to inspect registers.");
      }
      const alignment = index.alignment[id] || {};
      if (alignment.unaligned || alignment.confidence === "unknown" || (alignment.offset !== undefined && numericValue(alignment.offset) === undefined)) {
        aligned.warnings.add("Aligned state is unknown because alignment is uncertain. Select Local time to inspect this sequencer.");
      }
      index.bySequencer.set(id, { local, aligned });
      local.executionTrace = buildExecutionTrace(index, id, eventsBySequencer.get(id) || [], local, "local");
      aligned.executionTrace = buildExecutionTrace(index, id, eventsBySequencer.get(id) || [], aligned, "aligned");
    }
    index.projectExecutionTrace = buildProjectExecutionTrace(index);
    return index;
  }

  function buildProjectExecutionTrace(index) {
    const traces = index.sequencers.map(({ id }) => ({ sequencerId: id, trace: executionTrace(index, id, "local") }));
    const unavailable = (reason) => ({ frames: [], traces, reason });
    if (!traces.length) return unavailable("No sequencers are recorded. Generate a project simulation trace to replay execution.");
    if (traces.length > 1 && traces.some(({ sequencerId }) => {
      const execution = index.execution[sequencerId];
      return !execution || execution.mode !== "simulation" || execution.timing_model !== "event_driven";
    })) {
      return unavailable("Replay together requires a shared simulation clock. Generate a project simulation trace for all sequencers.");
    }
    const missing = traces.find(({ trace }) => trace.reason);
    if (missing) return unavailable(`${missing.sequencerId}: ${missing.trace.reason}`);
    const frames = new Set();
    for (const { sequencerId } of traces) {
      for (const time of stepTimes(index, sequencerId, "local")) frames.add(time);
    }
    return { frames: [...frames].sort((left, right) => left - right), traces, reason: null };
  }

  function buildSignalStages(signals) {
    const stages = new Map();
    for (const trace of signals && signals.traces || []) {
      if (!Array.isArray(trace.samples) || !trace.samples.length || !(trace.dt_ns > 0)) continue;
      const end = Math.min(trace.t0 + trace.samples.length * trace.dt_ns, Number.isFinite(signals.sampled_end_ns) ? signals.sampled_end_ns : Infinity);
      if (!(end > trace.t0)) continue;
      if (!stages.has(trace.stage)) stages.set(trace.stage, { traces: [], min: 0, max: 0, start: Infinity, end: -Infinity });
      const stage = stages.get(trace.stage);
      stage.traces.push({ ...trace, end });
      stage.start = Math.min(stage.start, trace.t0);
      stage.end = Math.max(stage.end, end);
      for (const value of trace.samples) {
        if (!Number.isFinite(value)) continue;
        stage.min = Math.min(stage.min, value);
        stage.max = Math.max(stage.max, value);
      }
    }
    for (const stage of stages.values()) {
      if (stage.min === stage.max) { stage.min -= 1; stage.max += 1; }
      for (const trace of stage.traces) {
        trace.segments = [];
        let points = [];
        for (const [sample, value] of trace.samples.entries()) {
          if (Number.isFinite(value)) {
            points.push(`${((trace.t0 + sample * trace.dt_ns - stage.start) / (stage.end - stage.start) * 600).toFixed(2)},${(150 - (value - stage.min) / (stage.max - stage.min) * 140).toFixed(2)}`);
          } else if (points.length) {
            trace.segments.push(points.join(" "));
            points = [];
          }
        }
        if (points.length) trace.segments.push(points.join(" "));
      }
    }
    return stages;
  }

  function signalAt(index, sequencerId, stageName, time, basis = "local") {
    const stages = index.signalStages.get(String(sequencerId)) || new Map();
    const stage = stages.get(stageName);
    if (!stage) return null;
    const alignment = index.alignment[sequencerId] || {};
    const unavailable = basis === "aligned" && (alignment.unaligned || alignment.confidence === "unknown" || (alignment.offset !== undefined && numericValue(alignment.offset) === undefined));
    const offset = basis === "aligned" ? numericValue(alignment.offset) ?? 0 : 0;
    const localTime = unavailable ? undefined : time - offset;
    return {
      ...stage, offset, localTime,
      values: stage.traces.map((trace) => {
        const sample = localTime === undefined || localTime >= trace.end ? -1 : Math.floor((localTime - trace.t0) / trace.dt_ns);
        const value = sample >= 0 && sample < trace.samples.length ? trace.samples[sample] : undefined;
        return { path: trace.path, value: Number.isFinite(value) ? value : undefined, source: trace.source, sampleTime: sample >= 0 && sample < trace.samples.length ? trace.t0 + sample * trace.dt_ns + offset : undefined };
      }),
    };
  }

  function getSeries(index, sequencerId, basis) {
    const sequencer = index.bySequencer.get(String(sequencerId));
    return sequencer && sequencer[basis === "aligned" ? "aligned" : "local"];
  }

  function stepTimes(index, sequencerId, basis = "local") {
    return (getSeries(index, sequencerId, basis) || { steps: [] }).steps;
  }

  function executionTrace(index, sequencerId, basis = "local") {
    const series = getSeries(index, sequencerId, basis);
    return series ? series.executionTrace : { instructions: [], sourceLines: [], reason: "No sequencer selected for execution replay." };
  }

  function projectExecutionTrace(index) {
    return index.projectExecutionTrace;
  }

  function registerHistory(index, sequencerId, register, time, basis = "local") {
    const series = getSeries(index, sequencerId, basis);
    const items = series && series.registers.get(register) || [];
    const count = upperBound(items, time);
    return {
      total: items.length, count,
      entries: items.slice(Math.max(0, count - 8), count),
      previous: items[upperBound(items, time, false) - 1] || null,
      next: items[count] || null,
    };
  }

  function registerWrite(index, sequencerId, register, eventId, basis = "local") {
    const series = getSeries(index, sequencerId, basis);
    return series && series.registerWrites.get(`${String(register).toUpperCase()}:${eventId}`) || null;
  }

  function registerAt(index, sequencerId, register, time, basis = "local") {
    const series = getSeries(index, sequencerId, basis);
    if (!series) return null;
    const entry = valueAt(series.registers.get(String(register).toUpperCase()), time, latest(series.q1Risks, time));
    return entry && !(entry.meta || {}).invalidated ? entry : null;
  }

  function stateAt(items, time, risk) {
    const entry = latest(items, time);
    if (risk && (!entry || entry.time < risk.time || (risk.inclusive && entry.time === risk.time))) return { event: null, status: "unknown" };
    if (!entry) return { event: null, status: "not-started" };
    const op = (entry.event.meta || {}).op || entry.event.kind;
    const status = op === "stop" && time >= (entry.end ?? entry.time) ? "stopped" : entry.end !== undefined && time < entry.end ? "active" : "last-command";
    return { event: entry.event, time: entry.time, status };
  }

  function coreStateAt(series, core, time, risk) {
    const waits = core === "q1" ? series.stalls : series.rtWaits;
    const wait = latest(waits, time);
    if (wait && (time < wait.end || (wait.event.meta || {}).pending)) return { event: wait.event, time: wait.time, status: core === "q1" ? "stalled" : "waiting" };
    return stateAt(series[core], time, risk);
  }

  function simulationAt(index, sequencerId, series, time, basis) {
    const config = index.simulation[String(sequencerId)];
    if (!config) return null;
    const alignment = index.alignment[sequencerId] || {};
    const unavailable = basis === "aligned" && (alignment.unaligned || alignment.confidence === "unknown" || (alignment.offset !== undefined && numericValue(alignment.offset) === undefined));
    const queue = latest(series.queue, time);
    const stall = latest(series.stalls, time);
    const count = upperBound(series.communications, time);
    return {
      config, localTime: unavailable ? undefined : time - (basis === "aligned" ? numericValue(alignment.offset) ?? 0 : 0),
      queue: { depth: unavailable ? undefined : queue ? numericValue(queue.event.meta.depth) : 0, capacity: queue && queue.event.meta.capacity || config.queue_capacity, event: queue && queue.event },
      stall: stall && (time < stall.end || (stall.event.meta || {}).pending) ? stall : null,
      communications: { count, total: series.communications.length, entries: series.communications.slice(Math.max(0, count - 10), count), next: series.communications[count] || null },
      acquisitionResult: latest(series.resultEvents, time) || null,
      signals: config.signals || null,
    };
  }

  function valueAt(items, time, risk) {
    const item = latest(items || [], time);
    if (risk && (!item || item.time < risk.time || (risk.inclusive && item.time === risk.time))) return { value: null, display: "unknown", known: false, time: risk.time, source: risk.event.source, eventId: risk.event.id, confidence: "unknown", meta: { invalidated: true, reason: risk.message } };
    return item ? { ...item, changed: item.time === time } : null;
  }

  function summarize(index, sequencerId, time, basis = "local") {
    const series = getSeries(index, sequencerId, basis) || createSeries();
    const q1Risk = latest(series.q1Risks, time);
    const rtRisk = latest(series.rtRisks, time);
    const warnings = new Set(series.warnings);
    if (q1Risk) warnings.add(q1Risk.message);
    if (rtRisk) warnings.add(rtRisk.message);
    return {
      q1: coreStateAt(series, "q1", time, q1Risk),
      rt: coreStateAt(series, "rt", time, rtRisk),
      warnings: Array.from(warnings),
    };
  }

  function snapshot(index, sequencerId, time, basis = "local", binOptions = {}) {
    const series = getSeries(index, sequencerId, basis) || createSeries();
    const q1Risk = latest(series.q1Risks, time);
    const rtRisk = latest(series.rtRisks, time);
    const warnings = new Set(series.warnings);
    if (q1Risk) warnings.add(q1Risk.message);
    if (rtRisk) warnings.add(rtRisk.message);
    const registers = Array.from({ length: 64 }, (_, number) => {
      const register = `R${number}`;
      const item = valueAt(series.registers.get(register), time, q1Risk);
      return { register, value: null, display: "unknown", known: false, changed: false, ...item };
    });
    const fields = new Set(["frequency", "phase", "phase_delta", "awg_gain", "awg_offset", "marker", ...series.pending.keys(), ...series.applied.keys()]);
    const parameters = Array.from(fields, (field) => {
      const pending = valueAt(series.pending.get(field), time, q1Risk);
      return { field, pending: pending && (pending.consumedAt === undefined || pending.consumedAt > time) ? pending : null, applied: valueAt(series.applied.get(field), time, rtRisk) };
    });
    const total = upperBound(series.binOrder, time);
    const offset = Math.max(0, Math.floor(Number(binOptions.offset) || 0));
    const limit = Math.min(128, Math.max(1, Math.floor(Number(binOptions.limit) || 64)));
    const records = series.binOrder.slice(offset, Math.min(total, offset + limit)).map(({ bin }) => {
      const count = upperBound(bin.history, time);
      const last = bin.history[count - 1];
      const result = latest(series.acquisitionResults.get(`${bin.acquisitionIndex}:${bin.binIndex}`) || [], time);
      return {
        acquisitionIndex: bin.acquisitionIndex, binIndex: bin.binIndex, count,
        lastTime: last.time, eventId: last.event.id, source: last.event.source, result: result ? { ...result.event.meta, readyTime: result.time, eventId: result.event.id } : "unknown",
        history: bin.history.slice(Math.max(0, count - 8), count).map((entry) => ({ time: entry.time, eventId: entry.event.id, source: entry.event.source })),
      };
    });
    return {
      sequencerId: String(sequencerId), time, range: series.range, warnings: Array.from(warnings), execution: index.execution[String(sequencerId)] || null,
      q1: coreStateAt(series, "q1", time, q1Risk), rt: coreStateAt(series, "rt", time, rtRisk), registers, parameters,
      simulation: simulationAt(index, sequencerId, series, time, basis),
      awg: Array.from(series.awg, ([path, items]) => ({ path, ...stateAt(items, time, rtRisk) })),
      acquisition: stateAt(series.acquisitions, time, rtRisk),
      bins: { total, records, partial: Boolean(rtRisk), offset, limit },
    };
  }

  const api = { createIndex, snapshot, summarize, stepTimes, executionTrace, projectExecutionTrace, registerHistory, registerWrite, registerAt, signalAt, eventTime, numericValue, displayValue };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.q1lensHardwareState = api;
})(typeof window !== "undefined" ? window : undefined);
