(function hardwareStateFactory(root) {
  const ACQUISITION_OUTCOMES = new Set(["acquisition_result", "acquisition_unavailable"]);
  const SIMULATION_EVENTS = new Set(["rt_queue_enqueue", "rt_queue_dequeue", "q1_stall", "feedback_receive", "feedback_send", "trigger_receive", ...ACQUISITION_OUTCOMES, "conditional_skip", "sync_wait", "trigger_wait"]);
  const DIGITAL_ACQUISITIONS = new Set(["acquire_digital", "acquire_ttl", "acquire_timetags"]);
  const GATE_ACQUISITIONS = new Set(["acquire_ttl", "acquire_timetags"]);
  const UNREADY_ACQUISITION_STATUSES = new Set(["unavailable", "incomplete", "discarded", "invalid"]);
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

  function acquisitionOutcome(event, time) {
    const meta = event.meta || {};
    // A timetag delta can be unavailable while the edge count is valid.
    const digital = DIGITAL_ACQUISITIONS.has(meta.kind) ? {
      level: numericValue(meta.level),
      edgeCount: meta.count_valid === false ? undefined : numericValue(meta.count),
      timetagTicks: meta.valid === false ? undefined : numericValue(meta.timetag_ticks),
    } : undefined;
    let unreadyStatus = UNREADY_ACQUISITION_STATUSES.has(meta.status) ? meta.status :
      event.kind === "acquisition_unavailable" ? "unavailable" : undefined;
    if (!unreadyStatus && digital && !Object.values(digital).some(Number.isFinite)) {
      unreadyStatus = meta.count_valid === false || meta.valid === false ? "invalid" : "unavailable";
    }
    return { ...meta, eventId: event.id, outcomeTime: time, ...(digital && { digital }),
      ...(unreadyStatus ? { status: unreadyStatus } : { readyTime: time }) };
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

  function alignmentOffset(index, sequencerId, basis) {
    if (basis !== "aligned") return 0;
    const alignment = index.alignment[sequencerId] || {};
    if (alignment.unaligned || alignment.confidence === "unknown" || (alignment.offset !== undefined && numericValue(alignment.offset) === undefined)) return undefined;
    return numericValue(alignment.offset) ?? 0;
  }

  function eventTime(index, event, basis = "local", edge = "t0") {
    if (!event) return undefined;
    const offset = alignmentOffset(index, event.sequencer_id, basis);
    if (offset === undefined) return undefined;
    if (basis === "aligned") {
      const explicit = numericValue((event.meta || {})[`aligned_${edge}`]);
      if (explicit !== undefined) return explicit;
    }
    const local = numericValue(event[edge]) ?? numericValue(event[`${edge}_ns`]);
    return local === undefined ? undefined : local + offset;
  }

  function observationTerminal(index, sequencerId, basis) {
    const execution = index.execution[sequencerId];
    const terminal = execution?.terminal;
    if (execution?.mode !== "simulation" && !(execution?.mode === "playback" && terminal?.core === "q1")) return undefined;
    const local = numericValue(terminal?.local_time_ns);
    if (local === undefined) return undefined;
    const offset = alignmentOffset(index, sequencerId, basis);
    return { ...terminal, time: offset === undefined ? undefined : local + offset };
  }

  function record(event, time, value) {
    return { time, value, display: displayValue(value), known: knownValue(value), source: event.source, eventId: event.id, event };
  }

  function createSeries() {
    return {
      q1: [], rt: [], registers: new Map(), registerWrites: new Map(), pending: new Map(), applied: new Map(),
      awg: new Map(), acquisitions: [], gateWindows: new Map(), digitalSamples: [], bins: new Map(), binOrder: [],
      queue: [], stalls: [], rtWaits: [], communications: [], acquisitionResults: new Map(), resultEvents: [],
      q1Risks: [], rtRisks: [], outputRisks: [], phaseRisks: [], warnings: new Set(), steps: [], range: { min: 0, max: 0 },
      registerRisks: new Map(), pendingRisks: new Map(), appliedRisks: new Map(),
    };
  }

  function append(map, key, item) {
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(item);
  }

  function binIdentity(meta, op) {
    const acquisitionIndex = numericValue(meta.acquisition_index);
    const binIndex = numericValue(meta.bin_index);
    if (acquisitionIndex === undefined || binIndex === undefined) return undefined;
    const legacyKey = `${acquisitionIndex}:${binIndex}`;
    const kind = DIGITAL_ACQUISITIONS.has(op) ? op : undefined;
    return { acquisitionIndex, binIndex, legacyKey, key: kind ? `${legacyKey}:${kind}` : legacyKey, kind };
  }

  function addRisk(series, event, time, clock, message, inclusive = false) {
    if (time !== undefined) {
      const risk = { time, message, event, inclusive };
      series[`${clock}Risks`].push(risk);
      const loop = event.kind === "loop_block" ? event.meta : undefined;
      if (clock === "q1") {
        // A proof cutoff limits execution coverage while retaining recorded register and parameter evidence.
        if (event.meta?.q1_proof_cutoff === true) return;
        addFieldRisks(series.registerRisks, loop?.uncertain_registers, risk);
        addFieldRisks(series.pendingRisks, loop?.uncertain_latched_fields, risk);
      } else if (event.meta?.output_state_unknown !== false) {
        series.outputRisks.push(risk);
        addFieldRisks(series.appliedRisks, loop?.uncertain_latched_fields, risk);
      }
    } else series.warnings.add(message);
  }

  function addFieldRisks(risks, fields, risk) {
    // An explicit empty list proves no fields are affected. Older traces lack the list.
    for (const field of Array.isArray(fields) ? fields : [undefined]) append(risks, field, risk);
  }

  function latestFieldRisk(risks, field, time) {
    const all = latest(risks.get(undefined) || [], time);
    const specific = latest(risks.get(field) || [], time);
    return specific && (!all || specific.time > all.time) ? specific : all;
  }

  function recordedQ1Issue(index, id, sequencerId) {
    const issue = id == null ? undefined : index.eventsById.get(String(id));
    return issue?.kind === "q1_issue" && issue.sequencer_id === sequencerId && !isPreview(issue) ? issue : undefined;
  }

  function isQ1OnlyCompactLoop(index, event) {
    // Match the analyzed-loop evidence used by timelineAdapter.q1OnlyLoopIds.
    const meta = event.meta || {};
    const start = numericValue(event.t0);
    if (meta.period?.kind !== "concrete" || numericValue(meta.period) !== 0 || start === undefined ||
        start !== numericValue(event.t1) || meta.period_invariant !== true || Object.keys(meta.rt_packet_affine || {}).length) return false;
    const members = meta.first_iteration_event_ids;
    if (!Array.isArray(members) || !members.length) return false;
    const evidence = members.map((id) => index.eventsById.get(String(id)));
    const q1Ids = new Set(evidence.filter((item) => item?.kind === "q1_issue" && item.meta?.emits_rt_packet === false)
      .map((item) => String(item.id)));
    return q1Ids.size > 0 && evidence.every((item) => item && item.sequencer_id === event.sequencer_id &&
      item.meta?.rt_packet_id == null && (q1Ids.has(String(item.id)) || item.kind === "latched_state_pending" &&
        q1Ids.has(String(item.meta?.q1_issue_event_id))));
  }

  function compactLoopQ1End(index, event, basis, issuesBySource) {
    const meta = event.meta || {};
    const edge = eventTime(index, recordedQ1Issue(index, meta.loop_q1_issue_event_id, event.sequencer_id), basis, "t1");
    if (edge !== undefined) return edge;

    const firstIssues = (meta.first_iteration_event_ids || []).map((id) => recordedQ1Issue(index, id, event.sequencer_id)).filter(Boolean);
    const sourceIssues = issuesBySource.get(sourceKey(meta.source_end)) || [];
    const inSummary = sourceIssues.filter((issue) => firstIssues.includes(issue));
    const candidates = inSummary.length ? inSummary : sourceIssues;
    const issueEnds = firstIssues.map((issue) => eventTime(index, issue, basis, "t1")).filter(Number.isFinite);
    const firstEnd = issueEnds.length ? Math.max(...issueEnds) : undefined;
    // In a legacy summary the recorded body can identify the loop issue that immediately follows it.
    const matches = candidates.length <= 1 || firstEnd === undefined ? candidates :
      candidates.filter((issue) => eventTime(index, issue, basis) === firstEnd);
    return eventTime(index, matches.length === 1 ? matches[0] : undefined, basis, "t1") ?? firstEnd;
  }

  function buildSeries(index, events, history, basis, terminal) {
    const series = createSeries();
    series.terminal = terminal;
    if (terminal?.time !== undefined) series.steps.push(terminal.time);
    const issuesBySource = new Map();
    const pendingOccurrences = new Map();
    const pendingFields = new Map();
    const pendingById = new Map();
    const appliedPackets = new Set();
    const gateCommands = new Map();
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
      const unresolvedBranch = event.kind === "branch_region" && (meta.comparison_paths ||
        end === undefined && (time !== undefined || (event.t1 ?? event.t1_ns) == null));
      if (time !== undefined) series.steps.push(time);
      if (end !== undefined) series.steps.push(end);
      if (event.kind === "unknown_region" || unresolvedBranch) {
        const message = "Execution after an unresolved region is unknown.";
        if (time !== undefined) addRisk(series, event, time, "rt", message, true);
        if (unresolvedBranch || meta.q1_proof_cutoff === true) {
          // A recorded Q1 issue can finish even when the analytical RT start is symbolic.
          const sourceIssues = unresolvedBranch && meta.q1_issue_event_id == null ? issuesBySource.get(sourceKey(event.source)) : undefined;
          const issue = sourceIssues?.length === 1 ? sourceIssues[0] :
            recordedQ1Issue(index, meta.q1_issue_event_id, event.sequencer_id);
          addRisk(series, event, eventTime(index, issue, basis, "t1"), "q1", message, true);
        }
      }
      const feedbackAssumed = meta.feedback_assumption || (meta.feedback_assumptions || []).length;
      if (feedbackAssumed) series.warnings.add("State depends on assumed feedback values or timing.");
      if (meta.assumed_branch_path || (meta.branch_assumptions || []).length || event.confidence === "assumed" && !feedbackAssumed) {
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
      const playbackFeedbackWait = meta.playback && event.kind === "feedback_pop" && meta.feedback_input?.status === "waiting";
      const playbackTriggerWait = meta.playback && event.kind === "wait_trigger" && meta.trigger_assumption?.status === "waiting";
      if (playbackFeedbackWait || playbackTriggerWait) entry.pending = true;
      if (event.kind === "q1_stall" || playbackFeedbackWait) series.stalls.push(entry);
      if (event.kind === "sync_wait" || event.kind === "trigger_wait" || playbackTriggerWait) series.rtWaits.push(entry);
      if (SIMULATION_EVENTS.has(event.kind) && !event.kind.startsWith("rt_queue_")) series.communications.push(entry);
      if (ACQUISITION_OUTCOMES.has(event.kind)) {
        entry.acquisitionOutcome = acquisitionOutcome(event, time);
        series.resultEvents.push(entry);
        if (meta.kind === "acquire_digital") series.digitalSamples.push({ time, end: time, event, sample: entry.acquisitionOutcome });
      }
      if (event.kind === "q1_issue") {
        series.q1.push(entry);
        lastQ1Time = end ?? time;
      }
      if (rtCommand) {
        series.rt.push(entry);
        lastRtTime = end ?? time;
      }
      if (meta.nco_phase_uncertain) series.phaseRisks.push({time, event, inclusive: true, message: "NCO phase is uncertain."});
      else if (meta.phase_reset) series.phaseRisks.push({time, event});
      const applied = { ...(meta.applied_state || (event.kind === "latched_state_applied" && meta.field ? { [meta.field]: meta.value } : {})) };
      if (rtCommand && meta.phase_reset && applied.phase === undefined) applied.phase = 0;
      if (meta.conditional_skipped) {
        for (const field of ["phase_delta", "phase_reset"]) {
          const pending = pendingById.get(String(meta.captured_setter_event_ids?.[field]));
          if (pending) {
            pending.consumedAt = pending.consumedAt === undefined ? time : Math.min(time, pending.consumedAt);
            if (pendingFields.get(field) === pending) pendingFields.delete(field);
          }
        }
      }
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
        const kind = meta.op || meta.kind;
        const gateWindow = meta.digital_gate_window;
        if (Number.isInteger(gateWindow?.start_ticks) && Number.isInteger(gateWindow.end_ticks) &&
            gateWindow.end_ticks >= gateWindow.start_ticks) {
          const offset = alignmentOffset(index, event.sequencer_id, basis);
          const start = gateWindow.start_ticks / 128 + offset;
          const end = gateWindow.end_ticks / 128 + offset;
          append(series.gateWindows, kind, { time: start, end, event, gate: { start, end, status: gateWindow.status } });
          series.steps.push(start, end);
        } else if (meta.digital_gate_timing !== "effective" && !(kind === "acquire_digital" && index.simulation[event.sequencer_id])) {
          const window = meta.acquisition_window;
          const integrationEnd = Number.isFinite(window?.end_ns) && window.status !== "unknown"
            ? window.end_ns + time - numericValue(event.t0) : undefined;
          series.acquisitions.push({ ...entry, end: window ? integrationEnd : end,
            integrationUnknown: window ? integrationEnd === undefined : !index.simulation[event.sequencer_id] });
          if (integrationEnd !== undefined) series.steps.push(integrationEnd);
        }
        const bin = binIdentity(meta, kind);
        const gate = GATE_ACQUISITIONS.has(bin?.kind) ? numericValue(meta.operand_snapshot?.[2]) ??
          numericValue(meta.resolved_args?.find((arg) => arg.index === 2)?.value) : undefined;
        if ((gate === 0 || gate === 1) && meta.rt_packet_id != null) {
          const key = `${bin.kind}:${meta.rt_packet_id}`;
          gateCommands.set(key, gateCommands.has(key) ? null : { eventId: event.id, time, enable: gate === 1 });
        }
        const target = bin?.kind === "acquire_ttl" ? gate !== 0 : gate !== 1;
        if (bin && target) {
          if (!series.bins.has(bin.key)) series.bins.set(bin.key, { ...bin, history: [] });
          series.bins.get(bin.key).history.push(entry);
        } else if (!bin) {
          series.warnings.add("Some acquisition targets are unresolved.");
        }
      }
      if (event.kind === "loop_block" && !meta.playback && !meta.observed && meta.count !== 1) {
        const q1End = compactLoopQ1End(index, event, basis, issuesBySource);
        const q1Only = isQ1OnlyCompactLoop(index, event);
        const message = q1Only ? "Q1 state may be incomplete after collapsed loop iterations." :
          "Collapsed loop iterations are not simulated; state and acquisition counts may be incomplete.";
        if (!q1Only) addRisk(series, event, end, "rt", message);
        addRisk(series, event, q1End, "q1", message);
      }
      if (end !== undefined && meta.latched_state_at_join) {
        for (const [field, value] of Object.entries(meta.latched_state_at_join)) {
          if ((meta.pending_fields_at_join || []).includes(field)) continue;
          const item = record(event, end, value);
          item.branchJoinSources = meta.latched_sources_at_join?.[field];
          item.resolvedRiskEventId = event.id;
          append(series.applied, field, item);
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
    for (const entry of series.resultEvents) {
      const meta = entry.event.meta || {};
      const identity = binIdentity(meta, meta.kind);
      if (!identity) continue;
      const ttl = identity.kind === "acquire_ttl";
      const open = gateCommands.get(`${identity.kind}:${meta.open_packet_id}`);
      const close = gateCommands.get(`${identity.kind}:${meta.rt_packet_id}`);
      const gateOpen = open?.enable ? open : undefined;
      const gateClose = close?.enable === false && (meta.source_event_id == null || close.eventId === meta.source_event_id) ? close : undefined;
      if (gateOpen || gateClose) entry.gate = { open: gateOpen, close: gateClose };
      const legacy = !series.bins.has(identity.key) && !entry.gate && series.bins.get(identity.legacyKey);
      const key = legacy ? identity.legacyKey : identity.key;
      append(series.acquisitionResults, key, entry);
      if (ttl && (meta.status === "modeled" || meta.status === "supplied") && numericValue(meta.count) !== undefined) {
        if (!series.bins.has(key)) series.bins.set(key, { ...identity, history: [] });
        const bin = series.bins.get(key);
        (bin.writes ||= []).push(entry);
      }
    }
    const sort = (items) => items.sort((left, right) => left.time - right.time);
    for (const key of ["q1", "rt", "acquisitions", "digitalSamples", "q1Risks", "rtRisks", "outputRisks", "phaseRisks", "queue", "stalls", "rtWaits", "communications", "resultEvents"]) sort(series[key]);
    for (const key of ["registers", "pending", "applied", "awg", "gateWindows", "registerRisks", "pendingRisks", "appliedRisks"]) {
      for (const items of series[key].values()) sort(items);
    }
    for (const results of series.acquisitionResults.values()) {
      let binAggregate;
      for (const entry of sort(results)) {
        const { kind, status, bin_aggregate: aggregate } = entry.event.meta || {};
        const acceptedDigital = status === "modeled" || status === "supplied";
        if ((acceptedDigital && kind === "acquire_timetags" && Number.isInteger(aggregate?.avg_cnt) && aggregate.avg_cnt > 0) ||
          (acceptedDigital && kind === "acquire_ttl" && aggregate?.kind === "ttl" && Number.isFinite(aggregate.total_count)) ||
          (aggregate?.kind === "iq" && !DIGITAL_ACQUISITIONS.has(kind) && Number.isFinite(entry.acquisitionOutcome.readyTime) &&
            Number.isInteger(aggregate.count) && aggregate.count > 0)) {
          binAggregate = aggregate;
        }
        entry.binAggregate = binAggregate;
      }
    }
    for (const bin of series.bins.values()) {
      sort(bin.history);
      if (bin.writes) sort(bin.writes);
      series.binOrder.push({ time: Math.min(bin.history[0]?.time ?? Infinity, bin.writes?.[0].time ?? Infinity), bin });
    }
    sort(series.binOrder);
    series.steps = Array.from(new Set(series.steps)).sort((a, b) => a - b);
    if (series.steps.length) series.range = { min: series.steps[0], max: series.steps[series.steps.length - 1] };
    return series;
  }

  function buildExecutionTrace(index, sequencerId, events, series, basis) {
    const unavailable = (reason) => ({ instructions: [], sourceLines: [], reason });
    const execution = index.execution[sequencerId];
    if (!execution || !["concrete", "simulation", "playback"].includes(execution.mode)) return unavailable("No execution trace is recorded. Generate an execution trace to replay instructions.");
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
    const events = (Array.isArray(ir.events) ? ir.events : []).filter((event) => (event.meta || {}).diff_status !== "removed");
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
      const local = buildSeries(index, eventsBySequencer.get(id) || [], historyBySequencer.get(id) || [], "local", observationTerminal(index, id, "local"));
      const aligned = buildSeries(index, eventsBySequencer.get(id) || [], historyBySequencer.get(id) || [], "aligned", observationTerminal(index, id, "aligned"));
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
    const name = String(register).toUpperCase();
    const entry = valueAt(series.registers.get(name), time, latestFieldRisk(series.registerRisks, name, time));
    return entry && !(entry.meta || {}).invalidated ? entry : null;
  }

  function stateAt(items, time, risk) {
    return entryStateAt(latest(items, time), time, risk);
  }

  function entryStateAt(entry, time, risk) {
    if (risk && (!entry || entry.time < risk.time || (risk.inclusive && entry.time === risk.time))) return { event: null, status: "unknown" };
    if (!entry) return { event: null, status: "not-started" };
    if (entry.integrationUnknown) return { event: entry.event, time: entry.time, status: "unknown" };
    const op = (entry.event.meta || {}).op || entry.event.kind;
    const status = op === "stop" && time >= (entry.end ?? entry.time) ? "stopped" : entry.end !== undefined && time < entry.end ? "active" : "last-command";
    return { event: entry.event, time: entry.time, status };
  }

  function acquisitionAt(series, time, risk) {
    const command = latest(series.acquisitions, time);
    let observed = command;
    let observedTime = command?.time ?? -Infinity;
    let openGate;
    for (const windows of series.gateWindows.values()) {
      const gate = latest(windows, time);
      if (!gate) continue;
      if (time < gate.end) {
        if (!openGate || gate.time >= openGate.time) openGate = gate;
      } else if (gate.end >= observedTime) {
        observed = gate;
        observedTime = gate.end;
      }
    }
    const sample = latest(series.digitalSamples, time);
    if (sample && sample.time >= observedTime) observed = sample;
    const activeCommand = command && !command.integrationUnknown && time < command.end;
    const entry = openGate || (activeCommand ? command : observed);
    const state = entryStateAt(entry, time, risk);
    if (!state.event) return state;
    return { ...state, ...(entry.gate && { gate: entry.gate }), ...(entry.sample && { sample: entry.sample }) };
  }

  function observationEnded(series, core, time) {
    const terminal = series.terminal;
    return terminal?.time !== undefined && (!terminal.core || terminal.core === core) && time >= terminal.time;
  }

  function coreStateAt(series, core, time, risk) {
    if (observationEnded(series, core, time)) {
      const edge = series.terminal.time;
      const previous = stateAt(series[core], edge, risk && risk.time <= edge ? risk : undefined);
      const meta = previous.event?.meta;
      const unfinishedStop = core === "q1" && meta?.planned_q1_end_ns > meta?.observation_cutoff_ns;
      return previous.status === "stopped" && !unfinishedStop ? previous : { ...previous, status: "observation-ended" };
    }
    const waits = core === "q1" ? series.stalls : series.rtWaits;
    const wait = latest(waits, time);
    if (wait && (time < wait.end || wait.pending || wait.event.meta?.pending)) return { event: wait.event, time: wait.time, status: core === "q1" ? "stalled" : "waiting" };
    return stateAt(series[core], time, risk);
  }

  function simulationAt(index, sequencerId, series, time, basis) {
    const config = index.simulation[String(sequencerId)];
    if (!config) return null;
    const offset = alignmentOffset(index, sequencerId, basis);
    const unavailable = offset === undefined;
    const end = numericValue(config.end_time_ns);
    const queue = latest(series.queue, time);
    const stall = latest(series.stalls, time);
    const count = upperBound(series.communications, time);
    return {
      config, localTime: unavailable ? undefined : time - offset,
      endTime: unavailable || end === undefined ? undefined : end + offset,
      queue: { depth: unavailable ? undefined : queue ? numericValue(queue.event.meta.depth) : 0, capacity: queue && queue.event.meta.capacity || config.queue_capacity, event: queue && queue.event },
      stall: !observationEnded(series, "q1", time) && stall && (time < stall.end || (stall.event.meta || {}).pending) ? stall : null,
      communications: { count, total: series.communications.length, entries: series.communications.slice(Math.max(0, count - 10), count), next: series.communications[count] || null },
      acquisitionResult: latest(series.resultEvents, time) || null,
      signals: config.signals || null,
    };
  }

  function valueAt(items, time, risk) {
    const item = latest(items || [], time);
    if (risk && (!item || item.time < risk.time || (risk.inclusive && item.time === risk.time && item.resolvedRiskEventId !== risk.event.id))) return { value: null, display: "unknown", known: false, time: risk.time, source: risk.event.source, eventId: risk.event.id, confidence: "unknown", meta: { invalidated: true, reason: risk.message } };
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
      ...(series.terminal && { terminal: series.terminal }),
      warnings: Array.from(warnings),
    };
  }

  function snapshot(index, sequencerId, time, basis = "local", binOptions = {}) {
    const series = getSeries(index, sequencerId, basis) || createSeries();
    const q1Risk = latest(series.q1Risks, time);
    const rtRisk = latest(series.rtRisks, time);
    const outputRisk = latest(series.outputRisks, time);
    const phaseStatus = latest(series.phaseRisks, time);
    const warnings = new Set(series.warnings);
    if (q1Risk) warnings.add(q1Risk.message);
    if (rtRisk) warnings.add(rtRisk.message);
    const registers = Array.from({ length: 64 }, (_, number) => {
      const register = `R${number}`;
      const item = valueAt(series.registers.get(register), time, latestFieldRisk(series.registerRisks, register, time));
      return { register, value: null, display: "unknown", known: false, changed: false, ...item };
    });
    const fields = new Set(["frequency", "phase", "phase_delta", "awg_gain", "awg_offset", "marker", ...series.pending.keys(), ...series.applied.keys()]);
    const parameters = Array.from(fields, (field) => {
      const pending = valueAt(series.pending.get(field), time, latestFieldRisk(series.pendingRisks, field, time));
      const appliedRisk = latestFieldRisk(series.appliedRisks, field, time);
      const phaseRisk = field === "phase" && phaseStatus?.message ? phaseStatus : undefined;
      const risk = phaseRisk && (!appliedRisk || phaseRisk.time >= appliedRisk.time) ? phaseRisk : appliedRisk;
      return { field, pending: pending && (pending.consumedAt === undefined || pending.consumedAt > time) ? pending : null, applied: valueAt(series.applied.get(field), time, risk) };
    });
    const total = upperBound(series.binOrder, time);
    const offset = Math.max(0, Math.floor(Number(binOptions.offset) || 0));
    const limit = Math.min(128, Math.max(1, Math.floor(Number(binOptions.limit) || 64)));
    const records = series.binOrder.slice(offset, Math.min(total, offset + limit)).map(({ bin }) => {
      const count = upperBound(bin.history, time);
      const last = bin.history[count - 1] || latest(bin.writes || [], time);
      const result = latest(series.acquisitionResults.get(bin.key) || [], time);
      return {
        key: bin.key, legacyKey: bin.legacyKey, kind: bin.kind, acquisitionIndex: bin.acquisitionIndex, binIndex: bin.binIndex, count,
        lastTime: last.time, eventId: last.event.id, source: last.event.source, result: result?.acquisitionOutcome || "unknown",
        ...(result?.gate && { gate: result.gate }),
        ...(result?.binAggregate && { aggregate: result.binAggregate }),
        history: bin.history.slice(Math.max(0, count - 8), count).map((entry) => ({ time: entry.time, eventId: entry.event.id, source: entry.event.source })),
      };
    });
    return {
      sequencerId: String(sequencerId), time, range: series.range, warnings: Array.from(warnings), execution: index.execution[String(sequencerId)] || null,
      ...(series.terminal && { terminal: series.terminal }),
      q1: coreStateAt(series, "q1", time, q1Risk), rt: coreStateAt(series, "rt", time, rtRisk), registers, parameters,
      simulation: simulationAt(index, sequencerId, series, time, basis),
      awg: Array.from(series.awg, ([path, items]) => ({ path, ...stateAt(items, time, outputRisk) })),
      acquisition: acquisitionAt(series, time, outputRisk),
      bins: { total, records, partial: Boolean(outputRisk), offset, limit },
    };
  }

  const api = { createIndex, snapshot, summarize, stepTimes, executionTrace, projectExecutionTrace, registerHistory, registerWrite, registerAt, signalAt, eventTime, numericValue, displayValue };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.q1lensHardwareState = api;
})(typeof window !== "undefined" ? window : undefined);
