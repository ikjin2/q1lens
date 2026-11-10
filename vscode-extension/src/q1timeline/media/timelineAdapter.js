(function q1timelineAdapterFactory(root) {
  const instructionColors = typeof module !== "undefined" && module.exports
    ? require("../../shared/timeline/instructionColors.js") : root.q1asmInstructionColors;
  const arrivalPointKinds = new Set(["trigger_receive", "feedback_rejected"]);

  function isTimelinePoint(event) {
    return arrivalPointKinds.has(event.kind) || Boolean(event.meta?.comparison_terminal);
  }
  function topLevelAddends(expression) {
    const text = String(expression || "");
    const addends = [];
    let depth = 0;
    let start = 0;
    for (let index = 0; index < text.length; index += 1) {
      const char = text[index];
      if (char === "(") {
        depth += 1;
      } else if (char === ")") {
        depth = Math.max(0, depth - 1);
      } else if (char === "+" && depth === 0) {
        addends.push(text.slice(start, index).trim());
        start = index + 1;
      }
    }
    addends.push(text.slice(start).trim());
    return addends.filter(Boolean);
  }

  function additiveConcreteLowerBound(expression) {
    let total = 0;
    let foundConcrete = false;
    for (const addend of topLevelAddends(expression)) {
      if (/^[+-]?\d+(?:\.\d+)?$/.test(addend)) {
        total += Number(addend);
        foundConcrete = true;
      }
    }
    return foundConcrete ? total : undefined;
  }

  function eventTimeValue(value, fallback = 0) {
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string") {
      const lowerBound = additiveConcreteLowerBound(value);
      if (Number.isFinite(lowerBound)) {
        return lowerBound;
      }
    }
    if (value && typeof value === "object") {
      if (typeof value.value === "number" && Number.isFinite(value.value)) {
        return value.value;
      }
      if (typeof value.value_ns === "number" && Number.isFinite(value.value_ns)) {
        return value.value_ns;
      }
      const lowerBound = additiveConcreteLowerBound(value.expr || value.display || value.source);
      if (Number.isFinite(lowerBound)) {
        return lowerBound;
      }
    }
    return fallback;
  }

  function concreteTime(value) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (value && typeof value === "object" && (!value.kind || value.kind === "concrete")) {
      const number = value.value ?? value.value_ns;
      if (typeof number === "number" && Number.isFinite(number)) return number;
    }
    return undefined;
  }

  function eventEdgeTime(event, edge, options, fallback = 0) {
    const meta = event && event.meta && typeof event.meta === "object" ? event.meta : {};
    if (options && options.timeBasis === "aligned") {
      const aligned = eventTimeValue(meta[`aligned_${edge}`], Number.NaN);
      if (Number.isFinite(aligned)) {
        return aligned;
      }
    }
    const fallbackKey = edge === "t0" ? "t0_ns" : "t1_ns";
    return eventTimeValue(event && event[edge], eventTimeValue(event && event[fallbackKey], fallback));
  }

  function displayValue(value, fallback = "") {
    if (value === undefined || value === null) {
      return fallback;
    }
    if (value && typeof value === "object") {
      if (Object.prototype.hasOwnProperty.call(value, "display")) {
        return String(value.display);
      }
      if (Object.prototype.hasOwnProperty.call(value, "value")) {
        return `${value.value} ns`;
      }
    }
    return String(value);
  }

  function underflowLabel(event) {
    const meta = event.meta || {};
    const label = meta.status === "possible_underflow" ? "Possible RT underflow" : "RT underflow";
    return typeof meta.slack_ns === "number" && Number.isFinite(meta.slack_ns) && meta.slack_ns < 0
      ? `${label} · ${-meta.slack_ns} ns late` : label;
  }

  function networkConflictDisplayMessage(diagnostic, alignment, timeBasis) {
    if (!["trigger_network_conflict", "trigger_network_possible_conflict"].includes(diagnostic.category)) return undefined;
    const {network, launch_ticks: ticks} = diagnostic.details || {};
    if (typeof network !== "string" || !Array.isArray(ticks) || ticks.length !== 2) return undefined;
    const [first, second] = ticks;
    if (!Number.isInteger(first) || (second !== null && !Number.isInteger(second))) return undefined;
    const entries = Object.values(alignment || {});
    const sharedOffset = entries[0]?.offset;
    const offset = timeBasis === "aligned" && Number.isFinite(sharedOffset) &&
      entries.every((entry) => entry?.clock === "shared_simulation" && entry.offset === sharedOffset) ? sharedOffset : 0;
    const slot = (tick) => `${tick / 128 + offset} ns`;
    if (second === null) {
      return `Trigger network '${network}' could conflict with a request whose grid epoch is unknown. Known launch slot at ${slot(first)}.`;
    }
    const qualifier = diagnostic.category === "trigger_network_possible_conflict" ? "potentially " : "";
    return `Trigger network '${network}' has ${qualifier}conflicting launch slots at ${slot(first)} and ${slot(second)}.`;
  }

  function eventLabelToken(event, options = {}) {
    if (!event) {
      return "";
    }
    if (event.kind === "underflow_warning") return underflowLabel(event);
    if (event.kind === "q1_stall") return "Q1 stall";
    if (event.kind === "feedback_rejected") return "Rejected LINQ arrival";
    if (event.kind === "wait" && options.mode !== "debug") {
      return "wait";
    }
    if (event.kind === "q1_issue") {
      const op = instructionColors.eventCommandToken(event);
      if (op && op !== "q1_issue") return op;
    }
    if (event.kind === "acquire") {
      return "acq";
    }
    if (event.kind === "wait_trigger") {
      return "trig";
    }
    if (event.kind === "feedback_pop" || event.kind === "feedback_com" || String(event.kind || "").startsWith("fb_")) {
      return "fb";
    }
    if (event.kind === "upd_param") {
      return "upd";
    }
    if (event.kind === "marker_state") {
      return "mark";
    }
    return String(event.label || event.kind || "event");
  }

  function laneBase(sequencer) {
    return `sequencer:${sequencer}`;
  }

  function eventLaneName(event) {
    return String(event.lane || "").replace(/^branch_compare\.(taken|fallthrough)\./, "");
  }

  function eventLane(event, options) {
    const sequencer = String(event.sequencer_id || "sequencer");
    const parent = laneBase(sequencer);
    const meta = event.meta || {};
    const path = meta.branch_comparison_path;
    const comparison = path === "taken" || path === "fallthrough";
    const branch = comparison ? { branchComparisonPath: path, branchComparisonId: String(meta.branch_comparison_branch_id || ""),
      ...(meta.branch_comparison_kind ? { branchComparisonKind: meta.branch_comparison_kind } : {}) } : {};
    const base = comparison ? `${parent}:branch:${branch.branchComparisonId}:${path}` : parent;
    const prefix = branchComparisonLabel(path, meta.branch_comparison_kind);
    const lane = eventLaneName(event);
    if (event.kind === "q1_issue" || (event.kind === "q1_stall" && options.layout === "instruction")) {
      return {
        id: `${base}:q1_issue`,
        label: comparison ? `${prefix} · Q1` : "Q1 core",
        kind: "q1-issue",
        role: "q1-issue",
        parentGroupId: parent,
        sequencer,
        ...branch,
        hidden: !(options && options.mode === "debug"),
      };
    }
    if (isPlayPathEvent(event) && !(options && options.mergedPlayEventIds && options.mergedPlayEventIds.has(String(event.id)))) {
      const path = playPathIndex(event);
      return {
        id: `${base}:${lane}`,
        label: `${comparison ? `${prefix} · ` : ""}${path === undefined ? lane.replace(/^rt\./, "") : `path${path}`}`,
        kind: "runtime-path",
        parentGroupId: parent,
        sequencer,
        ...branch,
      };
    }
    if (options.mode === "debug" && lane.startsWith("debug.") && event.kind !== "underflow_warning") {
      const label = event.kind === "q1_stall" ? "Q1 stall" : lane === "debug.underflow" ? "RT underflow" : lane.replace(/^debug\./, "debug / ");
      return {
        id: `${base}:${lane}`,
        label: `${comparison ? `${prefix} · ` : ""}${label}`,
        kind: "debug",
        parentGroupId: parent,
        sequencer,
        ...branch,
      };
    }
    return {
      id: base,
      label: comparison ? `${prefix} · RT` : sequencer,
      kind: comparison ? "branch-runtime" : "runtime",
      sequencer,
      ...(comparison ? { parentGroupId: parent, ...branch } : {}),
    };
  }

  function branchComparisonLabel(path, kind) {
    return kind === "rt_condition" ? (path === "taken" ? "Execute" : "Skip") : (path === "taken" ? "Jump" : "Continue");
  }

  function shouldIncludeEvent(event, options) {
    const lane = eventLaneName(event);
    if (lane === "debug.rt_queue") return false;
    if (["feedback_pop", "feedback_send", "feedback_receive"].includes(event.kind)) return false;
    if (event.kind === "underflow_warning") return !event.meta?.eventual && event.meta?.status !== "possible_underflow";
    if (event.kind === "analysis_incomplete") return false;
    if (["queue_depth", "slack", "latched_state_pending", "control_flow_loop", "control_flow_branch"].includes(event.kind)) {
      return false;
    }
    if (event.kind === "q1_issue") {
      return true;
    }
    if (options.mode !== "debug" && lane.startsWith("debug.")) {
      return false;
    }
    return true;
  }

  const primaryWaitKindByAuxiliary = new Map([["trigger_wait", "wait_trigger"], ["sync_wait", "wait_sync"]]);

  function waitProjectionKey(event, kind, legacy) {
    const meta = event.meta || {};
    const scope = [event.sequencer_id, kind, meta.branch_comparison_kind, meta.branch_comparison_branch_id,
      meta.branch_comparison_path, meta.loop_preview, meta.loop_iteration_index, meta.diff_status];
    if (!legacy) {
      return meta.rt_packet_id == null ? undefined : JSON.stringify([...scope, "packet", meta.rt_packet_id]);
    }
    const start = concreteTime(event.t0 ?? event.t0_ns);
    const source = event.source;
    if (Number.isFinite(start) && source?.file && Number.isInteger(source.line)) {
      return JSON.stringify([...scope, "source", source.file, source.line, start]);
    }
  }

  function completedWaitAliases(events) {
    const candidates = new Map();
    for (const event of events) {
      if (!["wait_trigger", "wait_sync"].includes(event.kind) || event.meta?.pending) continue;
      const end = concreteTime(event.t1 ?? event.t1_ns);
      if (!Number.isFinite(end)) continue;
      for (const legacy of [false, true]) {
        const key = waitProjectionKey(event, event.kind, legacy);
        if (key === undefined) continue;
        if (!candidates.has(key)) candidates.set(key, []);
        candidates.get(key).push({ id: String(event.id), end });
      }
    }
    const aliases = new Map();
    for (const event of events) {
      const kind = primaryWaitKindByAuxiliary.get(event.kind);
      if (!kind || event.meta?.pending) continue;
      const legacy = event.meta?.rt_packet_id == null;
      const key = waitProjectionKey(event, kind, legacy);
      const end = legacy ? concreteTime(event.t1 ?? event.t1_ns) : undefined;
      const matches = (candidates.get(key) || []).filter((candidate) => !legacy || end <= candidate.end);
      if (matches.length !== 1) continue;
      const primary = matches[0].id;
      if (!aliases.has(primary)) aliases.set(primary, []);
      aliases.get(primary).push(String(event.id));
    }
    return aliases;
  }

  function coincidentTriggerArrivals(events, options) {
    const scope = (event) => [event.sequencer_id, event.meta?.branch_comparison_kind,
      event.meta?.branch_comparison_branch_id, event.meta?.branch_comparison_path,
      event.meta?.loop_preview, event.meta?.loop_iteration_index, event.meta?.diff_status];
    const groups = new Map();
    const consumed = new Set();
    for (const event of events) {
      if (event.kind === "wait_trigger" && Array.isArray(event.meta?.input_event_ids)) {
        for (const id of event.meta.input_event_ids) consumed.add(JSON.stringify([...scope(event), String(id)]));
      }
      if (event.kind !== "trigger_receive" || !shouldIncludeEvent(event, options)) continue;
      const time = concreteTime(event.t0 ?? event.t0_ns);
      const meta = event.meta || {};
      if (time === undefined || meta.rt_trigger_epoch || meta.conditional_time_unknown || meta.comparison_terminal) continue;
      const key = JSON.stringify([...scope(event), eventLane(event, options).id, time]);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(event);
    }
    const byEventId = new Map();
    for (const arrivals of groups.values()) {
      if (arrivals.length < 2) continue;
      const matched = arrivals.filter((event) => consumed.has(JSON.stringify([...scope(event), String(event.id)])));
      const group = { arrivals, primary: matched.length === 1 ? matched[0] : arrivals[0] };
      for (const event of arrivals) byEventId.set(String(event.id), group);
    }
    return byEventId;
  }

  function q1OnlyLoopIds(eventById) {
    const ids = new Set();
    for (const event of eventById.values()) {
      if (event.kind !== "loop_block") continue;
      const meta = event.meta || {};
      const start = concreteTime(event.t0);
      if (meta.period?.kind !== "concrete" || concreteTime(meta.period) !== 0 ||
          !Number.isFinite(start) || start !== concreteTime(event.t1) || Object.keys(meta.rt_packet_affine || {}).length) continue;
      const members = meta.playback ? (meta.complete === true && meta.playback_event_ids) :
        (meta.period_invariant === true && meta.first_iteration_event_ids);
      if (!Array.isArray(members) || !members.length) continue;
      const evidence = members.map((id) => eventById.get(String(id)));
      const q1Ids = new Set(evidence.filter((item) => item?.kind === "q1_issue" && item.meta?.emits_rt_packet === false)
        .map((item) => String(item.id)));
      if (q1Ids.size && evidence.every((item) => item && item.sequencer_id === event.sequencer_id &&
          item.meta?.rt_packet_id == null && (q1Ids.has(String(item.id)) || item.kind === "latched_state_pending" &&
            q1Ids.has(String(item.meta?.q1_issue_event_id))))) ids.add(String(event.id));
    }
    return ids;
  }

  function visibleRtExtent(eventById, { timeBasis, feedbackFlows, alignment, excludedEventIds }) {
    let min = Infinity;
    let max = -Infinity;
    const options = { mode: "normal", timeBasis, alignment };
    for (const event of eventById.values()) {
      if (excludedEventIds?.has(String(event.id)) || !eventLaneName(event).startsWith("rt.") || !shouldIncludeEvent(event, options) ||
          event.kind === "loop_iteration_preview") continue;
      const start = eventEdgeTime(event, "t0", options, Number.NaN);
      if (!Number.isFinite(start)) continue;
      min = Math.min(min, start);
      const end = isTimelinePoint(event) ? start : eventEdgeTime(event, "t1", options, start);
      max = Math.max(max, start, end);
      if (event.kind === "acquire") {
        for (const field of ["acquisition_window", "scope_acquisition_window"]) {
          const windowEnd = acquisitionWindow(event, options, field).end;
          if (Number.isFinite(windowEnd)) max = Math.max(max, windowEnd);
        }
      }
    }
    if (!Number.isFinite(min)) return undefined;
    for (const flow of feedbackFlows || []) {
      const annotation = feedbackAnnotation(flow, eventById, options);
      if (annotation) max = Math.max(max, annotation.fromTime, annotation.toTime);
    }
    return { min, max };
  }

  function isPlayPathEvent(event) {
    return Boolean(event && event.kind === "play" && /^rt\.path\d+$/.test(eventLaneName(event)));
  }

  function playPathIndex(event) {
    const laneMatch = eventLaneName(event || {}).match(/^rt\.path(\d+)$/);
    if (laneMatch) {
      return Number(laneMatch[1]);
    }
    const meta = event && event.meta && typeof event.meta === "object" ? event.meta : {};
    const path = Number(meta.path);
    return Number.isInteger(path) ? path : undefined;
  }

  function eventClassNames(event, options) {
    return [
      `kind-${event.kind || "event"}`,
      `command-family-${instructionColors.commandFamily(instructionColors.eventCommandToken(event))}`,
      `confidence-${event.confidence || "unknown"}`,
      event.meta && event.meta.diff_status ? `diff-${event.meta.diff_status}` : "",
      event.meta?.rt_trigger_epoch || options.timeBasis === "aligned" && event.meta?.aligned_time_nominal ? "rt-time-nominal" : "",
      event.meta?.comparison_terminal ? `comparison-terminal comparison-terminal-${event.meta.comparison_terminal.status}` : "",
      event.kind === "feedback_pop" ? "normal-feedback-collapsed" : "",
      event.kind === "loop_block" ? "loop-collapsed" : "",
      event.kind === "branch_region" ? "branch-effect-marker" : "",
    ].filter(Boolean).join(" ");
  }

  function branchPathLabel(event) {
    const meta = event && event.meta && typeof event.meta === "object" ? event.meta : {};
    const path = String(meta.assumed_branch_path || meta.branch_comparison_path || "").trim();
    if (path === "taken") {
      return "condition true";
    }
    if (path === "fallthrough") {
      return "condition false";
    }
    return path || "selected path";
  }

  function eventVisualKind(event) {
    if (event && event.kind === "branch_region") {
      return "branch_effect";
    }
    return String((event && event.kind) || "event");
  }

  function eventTitle(event, start, end, options) {
    if (event.kind === "q1_stall") return `Q1 stall · ${start}–${end} ns · ${event.meta?.reason || "Q1 is waiting"}`;
    const terminal = event.meta?.comparison_terminal;
    if (terminal) {
      const id = event.kind === "feedback_rejected" ? ` · LINQ ID ${event.meta.id ?? "unknown"}` : "";
      return `Execution ${terminal.status.replaceAll("_", " ")} · ${start} ns · ${terminal.reason}${id}`;
    }
    if (event.kind === "underflow_warning") {
      const label = underflowLabel(event);
      return event.meta?.diagnostic_message ? `${label} · ${start} ns` : label;
    }
    if (event.kind === "trigger_receive") return `Trigger address ${event.meta?.address ?? "unknown"} · ${start} ns`;
    if (event.kind === "feedback_rejected") return `Rejected LINQ arrival · ID ${event.meta?.id ?? "unknown"} · ${start} ns`;
    const aligned = options.timeBasis === "aligned";
    const from = aligned && Number.isFinite(event.meta?.aligned_t0) ? `${event.meta.aligned_t0} ns` : displayValue(event.t0, start);
    const endFallback = event.meta?.conditional_time_unknown ? "unknown" : end;
    const to = aligned && Number.isFinite(event.meta?.aligned_t1) ? `${event.meta.aligned_t1} ns` : displayValue(event.t1, endFallback);
    const timing = q1IssueTimingText(event, options) || rtTimingText(event, from, to, options);
    if (timing) {
      const delay = event.meta?.trigger_wait_duration_ns;
      const label = event.meta?.merged_play_paths ? `play ${event.detail}` : event.label || event.kind;
      return `${label} | ${timing}${Number.isFinite(delay) ? ` · ${delay} ns after arrival` : ""}`;
    }
    if (event && event.kind === "branch_region") {
      return `Branch effect (${branchPathLabel(event)}) | ${from} -> ${to}`;
    }
    return event.title || `${event.label || event.kind || event.id} | ${from} -> ${to}`;
  }

  function timingDisplayOffset(event, options, start) {
    if (options.timeBasis !== "aligned") return 0;
    const alignedStart = concreteTime(event.meta?.aligned_t0);
    return start !== undefined && alignedStart !== undefined ? alignedStart - start
      : concreteTime(options.alignment?.[event.sequencer_id]?.offset) ?? 0;
  }

  function q1IssueTimingText(event, options = {}) {
    if (event.kind !== "q1_issue") return undefined;
    const cutoff = concreteTime(event.meta?.observation_cutoff_ns);
    const plannedEnd = concreteTime(event.meta?.planned_q1_end_ns);
    if (cutoff === undefined || plannedEnd === undefined || plannedEnd <= cutoff) return undefined;
    const start = concreteTime(event.t0) ?? concreteTime(event.t0_ns);
    const end = concreteTime(event.t1) ?? concreteTime(event.t1_ns);
    if (start === undefined || start > cutoff || end !== cutoff) return undefined;
    const offset = timingDisplayOffset(event, options, start);
    return `Issue ${start + offset} ns · Observation cutoff ${cutoff + offset} ns · Planned end ${plannedEnd + offset} ns`;
  }

  function netzeroResultTimingText(event, options = {}) {
    if (event.kind !== "netzero_result" || event.meta?.status !== "incomplete") return undefined;
    const cutoff = concreteTime(event.meta.observation_cutoff_ns);
    const plannedEnd = concreteTime(event.meta.planned_end_ns);
    if (cutoff === undefined || plannedEnd === undefined || plannedEnd < cutoff) return undefined;
    const start = concreteTime(event.t0) ?? concreteTime(event.t0_ns);
    const end = concreteTime(event.t1) ?? concreteTime(event.t1_ns);
    if (start === undefined || start > cutoff || end !== cutoff) return undefined;
    const offset = timingDisplayOffset(event, options, start);
    return `Observation cutoff ${cutoff + offset} ns · Planned end ${plannedEnd + offset} ns`;
  }

  function rtTimingText(event, from, to, options = {}) {
    const meta = event.meta || {};
    const aligned = options.timeBasis === "aligned";
    if (meta.pending === true && (event.kind === "trigger_wait" || event.kind === "sync_wait")) {
      const edge = (key) => (aligned ? concreteTime(meta[`aligned_${key}`]) : undefined) ??
        concreteTime(event[key]) ?? concreteTime(event[`${key}_ns`]);
      const arm = edge("t0"), cutoff = edge("t1");
      const target = event.kind === "trigger_wait" ? "trigger" : "synchronization";
      const armText = Number.isFinite(arm) ? `${arm} ns` : from || "unknown";
      const cutoffText = Number.isFinite(cutoff) ? ` · observation cutoff ${cutoff} ns` : "";
      return `Arm ${armText} → waiting for ${target} (completion unresolved)${cutoffText}`;
    }
    const nominal = meta.rt_trigger_epoch || aligned && meta.aligned_time_nominal;
    let timing = meta.trigger_arrival_unknown
      ? `${nominal ? "Nominal arm" : "Arm"} ${from} → waiting for trigger (completion unresolved)`
      : nominal ? `Nominal RT ${from} → ${to} (trigger arrival unresolved)` : undefined;
    const phase = event.kind === "wait_trigger" ? "Trigger arrival" : event.kind === "wait_sync" ? "Barrier release" : undefined;
    const execution = phase && !timing && !meta.pending && !meta.trigger_assumption && !meta.conditional_skipped && !meta.interrupted_by_fault
      ? concreteTime(meta.rt_execution_ns) : undefined;
    const bound = meta.queue_duration_ns === 0 ? concreteTime(meta.next_dequeue_not_before_ns) : undefined;
    const cutoff = concreteTime(meta.observation_cutoff_ns);
    const plannedEnd = cutoff !== undefined && !timing && !meta.pending && !meta.interrupted_by_fault && eventLaneName(event).startsWith("rt.")
      ? concreteTime(meta.planned_rt_end_ns) : undefined;
    if (execution === undefined && bound === undefined && plannedEnd === undefined) return timing;

    const start = concreteTime(event.t0) ?? concreteTime(event.t0_ns);
    const end = concreteTime(event.t1) ?? concreteTime(event.t1_ns);
    const offset = timingDisplayOffset(event, options, start);
    const incomplete = start <= cutoff && end === cutoff && plannedEnd > cutoff
      ? `Observation cutoff ${cutoff + offset} ns · Planned end ${plannedEnd + offset} ns` : undefined;
    if (execution !== undefined && start <= execution && execution <= end) {
      const completion = incomplete || (cutoff === undefined ? `Complete ${end + offset} ns` : `Observation cutoff ${cutoff + offset} ns`);
      timing = `Arm ${start + offset} ns · ${phase} ${execution + offset} ns · ${completion}`;
    } else if (incomplete) {
      timing = `Start ${start + offset} ns · ${incomplete}`;
    }
    if (bound === undefined) return timing;
    const label = nominal || meta.trigger_arrival_unknown ? "Nominal earliest next dequeue" : "Earliest next dequeue";
    return `${timing || `${from} -> ${to}`} · ${label} ${bound + offset} ns`;
  }

  function eventBlock(event, options = {}) {
    const start = eventEdgeTime(event, "t0", options);
    const end = eventEdgeTime(event, "t1", options, start);
    const pointMarker = isTimelinePoint(event) || (event.kind === "q1_stall" && options.layout === "instruction");
    const duration = pointMarker ? 0 : Math.max(eventTimeValue(event.duration, end - start), end - start, 0);
    const commandToken = pointMarker ? undefined : instructionColors.eventCommandToken(event);
    const source = event.source || {};
    const meta = event.meta || {};
    const rawStart = options.timeBasis === "aligned" && meta.aligned_t0 !== undefined
      ? meta.aligned_t0 : event.t0 ?? event.t0_ns;
    const eventIds = Array.isArray(meta.path_event_ids) ? meta.path_event_ids.map((eventId) => String(eventId)) : [String(event.id)];
    const detail = event.detail !== undefined ? String(event.detail) : displayValue(event.duration, duration ? `${duration} ns` : "");
    return {
      id: String(event.id),
      eventId: String(eventIds[0] || event.id),
      eventIds,
      label: eventLabelToken(event, options),
      commandToken,
      detail,
      kind: String(event.kind || "event"),
      ...(pointMarker ? { pointMarker } : {}),
      visualKind: eventVisualKind(event),
      accentColor: instructionColors.commandAccentColor(commandToken),
      start,
      duration,
      connectionStartKnown: Number.isFinite(rawStart) || Boolean(rawStart && typeof rawStart === "object" &&
        (!rawStart.kind || rawStart.kind === "concrete") && Number.isFinite(rawStart.value ?? rawStart.value_ns)),
      title: eventTitle(event, start, end, options),
      search: [
        event.id,
        event.kind,
        event.label,
        event.detail,
        event.confidence,
        source.file,
        source.line,
        source.raw,
        ...(Array.isArray(meta.play_paths) ? meta.play_paths.map((path) => `${path.path} ${path.label} ${path.waveform_index}`) : []),
      ].filter(Boolean).join(" ").toLowerCase(),
      classNames: eventClassNames(event, options),
      domain: {
        eventId: eventIds[0] || event.id,
        eventIds,
        kind: event.kind,
        source,
        meta,
      },
    };
  }

  function acquisitionWindow(event, options = {}, field = "acquisition_window") {
    const window = event.meta?.[field] || {};
    const localStart = eventTimeValue(event.t0 ?? event.t0_ns, Number.NaN);
    const start = eventEdgeTime(event, "t0", options);
    const offset = start - localStart;
    const end = Number.isFinite(window.end_ns) && window.status !== "unknown"
      ? window.end_ns + offset : undefined;
    const nominal = options.timeBasis === "aligned" && event.meta?.aligned_time_nominal;
    return { ...window, ...(nominal ? {time_basis: "nominal"} : {}), start, end, status: end === undefined ? "unknown" : window.status };
  }

  function acquisitionMode(event) {
    const op = instructionColors.eventCommandToken(event);
    return ({ acquire_weighed: "weighted", acquire_weighted: "weighted", acquire_ttl: "ttl",
      acquire_timetags: "timetags", acquire_digital: "digital" })[op] || "integration";
  }

  function acquisitionLane(event, options) {
    const parent = eventLane(event, options);
    return { ...parent, id: `${parent.id}:acquisition`, label: parent.branchComparisonPath
      ? `${branchComparisonLabel(parent.branchComparisonPath, parent.branchComparisonKind)} · Acquisition` : "Acquisition",
      kind: "acquisition", role: "acquisition", parentGroupId: parent.parentGroupId || parent.id };
  }

  function acquisitionBlock(event, options, scope = false) {
    const window = acquisitionWindow(event, options, scope ? "scope_acquisition_window" : "acquisition_window");
    const block = eventBlock(event, options);
    const mode = scope ? "scope" : acquisitionMode(event);
    const assumed = window.source === "instruction_duration_fallback" && window.end !== undefined;
    const target = `acq ${displayValue(event.meta?.acquisition_index, "?")} · bin ${displayValue(event.meta?.bin_index, "?")}`;
    const duration = window.end === undefined ? 0 : Math.max(0, window.end - window.start);
    const label = window.end === undefined ? "?" : duration < 1000 ? `${duration} ns` : `${Number((duration / 1000).toFixed(3))} μs`;
    const length = window.end === undefined ? "Unknown length" : assumed ? `${duration} ns (assumed from RT duration)`
      : `${window.effective_duration_ns} ns / ${window.configured_duration_ns} ns configured`;
    return { ...block, id: `${event.id}${scope ? ":scope" : ""}:acquisition-window`, kind: "acquisition_window", visualKind: "acquisition_window",
      start: window.start, duration,
      label: assumed ? `≈ ${label}` : label, detail: "", accentColor: undefined, acquisitionMode: mode, acquisitionTrack: scope ? 1 : 0,
      classNames: `${block.classNames} acquisition-window acquisition-${window.status}${assumed ? " acquisition-assumed" : ""}`,
      title: `${scope ? `Scope · paths ${(window.paths || []).join(", ")}` : `${mode} · ${target}`} | ${length} | ${window.status}${window.time_basis === "nominal" ? " | Nominal RT position" : ""}` };
  }

  function playMergeTimeKey(event, options) {
    const start = eventEdgeTime(event, "t0", options);
    const end = eventEdgeTime(event, "t1", options, start);
    return `${start}->${end}`;
  }

  function playMergeKey(event, options) {
    const source = event.source || {};
    const meta = event.meta && typeof event.meta === "object" ? event.meta : {};
    return [
      event.sequencer_id || "sequencer",
      meta.rt_packet_id || "no-packet",
      meta.branch_comparison_branch_id || "",
      meta.branch_comparison_path || "",
      source.file || "",
      source.line || "",
      playMergeTimeKey(event, options),
      meta.diff_status || "unchanged",
    ].join("|");
  }

  function playPathSummary(event) {
    const meta = event.meta && typeof event.meta === "object" ? event.meta : {};
    const path = playPathIndex(event);
    return {
      event_id: String(event.id),
      path,
      waveform_index: meta.waveform_index,
      label: String(event.label || (meta.waveform_index !== undefined ? `wf#${meta.waveform_index}` : "wf")),
    };
  }

  function mergedPlayEvent(group, options) {
    const sorted = [...group].sort((left, right) => (playPathIndex(left) ?? 99) - (playPathIndex(right) ?? 99));
    const first = sorted[0];
    const start = eventEdgeTime(first, "t0", options);
    const end = eventEdgeTime(first, "t1", options, start);
    const duration = Math.max(eventTimeValue(first.duration, end - start), end - start, 0);
    const playPaths = sorted.map(playPathSummary);
    const eventIds = playPaths.map((path) => path.event_id);
    return {
      ...first,
      id: `merged-play:${eventIds.join("+")}`,
      lane: "rt.play",
      kind: "play",
      label: "play",
      detail: playPaths.map((path) => `p${path.path ?? "?"} ${path.label}`).join(" / "),
      title: `play ${playPaths.map((path) => `p${path.path ?? "?"} ${path.label}`).join(" / ")} | ${displayValue(first.t0, start)} -> ${displayValue(first.t1, end)}`,
      duration: first.duration || { value: duration },
      meta: {
        ...(first.meta || {}),
        merged_play_paths: true,
        play_paths: playPaths,
        path_event_ids: eventIds,
      },
    };
  }

  function mergedPlayGroups(events, options) {
    const byKey = new Map();
    for (const event of events) {
      if (!isPlayPathEvent(event)) {
        continue;
      }
      const key = playMergeKey(event, options);
      if (!byKey.has(key)) {
        byKey.set(key, []);
      }
      byKey.get(key).push(event);
    }
    return Array.from(byKey.values()).filter((group) => group.length > 1);
  }

  function mergePacketStateBlocks(lanes, eventById, options) {
    const stateKinds = new Set(["marker_state", "latched_state_applied"]);
    const nonCommandKinds = new Set([
      ...stateKinds, "rt_queue_enqueue", "rt_queue_dequeue", "q1_stall", "sync_wait", "trigger_wait",
      "feedback_receive", "feedback_send", "acquisition_result",
    ]);
    const timeKey = (event, edge) => {
      const meta = event.meta || {};
      const value = options.timeBasis === "aligned" && meta[`aligned_${edge}`] !== undefined
        ? meta[`aligned_${edge}`] : event[edge] ?? event[`${edge}_ns`];
      return value && typeof value === "object" ? value.value ?? value.value_ns ?? value.expr ?? value.display ?? value : value;
    };
    const packetKey = (event) => {
      const meta = event.meta || {};
      return meta.rt_packet_id === undefined || meta.rt_packet_id === null ? undefined : JSON.stringify([
        event.sequencer_id, meta.rt_packet_id, timeKey(event, "t0"), timeKey(event, "t1"),
        meta.branch_comparison_branch_id, meta.branch_comparison_path, meta.loop_preview, meta.loop_iteration_index,
      ]);
    };
    const commands = new Map();
    for (const lane of lanes) {
      for (const block of lane.blocks) {
        const event = eventById.get(block.eventId);
        if (!event || isTimelinePoint(event) || nonCommandKinds.has(event.kind) || !eventLaneName(event).startsWith("rt.")) continue;
        const key = packetKey(event);
        if (key !== undefined && !commands.has(key)) commands.set(key, block);
      }
    }
    for (const lane of lanes) {
      lane.blocks = lane.blocks.filter((block) => {
        if (!stateKinds.has(block.kind)) return true;
        const command = commands.get(packetKey(eventById.get(block.eventId)));
        if (!command) return true;
        command.eventIds.push(...block.eventIds.filter((id) => !command.eventIds.includes(id)));
        command.search += ` ${block.search}`;
        return false;
      });
    }
  }

  function feedbackVisibleLabel(flow) {
    const channel = String(flow && flow.channel !== undefined ? flow.channel : "").trim();
    return channel ? `fb ch ${channel}` : "fb";
  }

  function feedbackEndpointTitle(event, role, time, options, integrationEnd = false, latency) {
    const arrival = latency !== undefined || event.kind === "feedback_receive" || event.kind === "trigger_receive";
    const core = arrival ? "queue" : event.kind === "feedback_pop" || event.kind === "q1_issue" ? "Q1" : "RT";
    const command = latency ? "estimated feedback queue arrival"
      : arrival ? `${event.kind === "feedback_receive" ? "feedback queue" : "trigger"} arrival`
      : `${instructionColors.eventCommandToken(event)}${integrationEnd ? " integration end" : ""}`;
    const location = event.source?.file ? [String(event.source.file).split(/[\\/]/).pop(), event.source.line].filter(Boolean).join(":") : "";
    const unknownTime = latency ? latency.unknown
      : event.meta?.rt_trigger_epoch || event.meta?.conditional_time_unknown || !integrationEnd && event.t0?.kind && event.t0.kind !== "concrete";
    const timestamp = unknownTime ? "time unknown"
      : `${time} ns (${options.timeBasis === "aligned" ? "aligned" : "local"})`;
    return [`${role} (${core})`, event.sequencer_id, command, timestamp,
      latency && (Number.isFinite(latency.ns) ? `${latency.route.replace(/_/g, " ")} +${latency.ns} ns (${latency.base} ns LINQ + ${latency.extra} ns configured transport)` : "latency unknown"),
      latency && `Consumer: ${instructionColors.eventCommandToken(event)}`, location].filter(Boolean).join(" · ");
  }

  function feedbackAnnotation(flow, eventById, options) {
    const fromEvent = eventById.get(String(flow && flow.from_event_id));
    const toEvent = eventById.get(String(flow && flow.to_event_id));
    if (!fromEvent || !toEvent) {
      return undefined;
    }
    const window = fromEvent.kind === "acquire" && fromEvent.meta?.acquisition_window
      ? acquisitionWindow(fromEvent, options) : undefined;
    if (options.layout !== "instruction" && (window?.status === "unknown" || window?.source === "instruction_duration_fallback")) return undefined;
    const fromEdge = fromEvent.kind === "acquire" ? "t1" : "t0";
    const offset = (event) => eventTimeValue(options.alignment?.[event.sequencer_id]?.offset, 0);
    const fromTime = Number.isFinite(flow.timing?.send_available_ns)
      ? flow.timing.send_available_ns - (options.timeBasis === "aligned" ? 0 : offset(fromEvent))
      : window?.end ?? eventEdgeTime(
      fromEvent,
      fromEdge,
      options,
      eventEdgeTime(fromEvent, "t0", options),
    );
    const timing = flow.timing;
    const arrivalKnown = Number.isFinite(timing?.arrival_ns);
    if (timing && !arrivalKnown && options.layout !== "instruction") return undefined;
    const latency = timing ? { ns: timing.latency_ns, route: timing.route, base: timing.base_latency_ns,
      extra: timing.transport_delay_ns, unknown: !arrivalKnown } : undefined;
    const toTime = arrivalKnown ? timing.arrival_ns - (options.timeBasis === "aligned" ? 0 : offset(toEvent))
      : eventEdgeTime(toEvent, "t0", options, fromTime);
    const fromLane = window?.end !== undefined && options.layout !== "instruction"
      ? acquisitionLane(fromEvent, options) : eventLane(fromEvent, options);
    const toLane = eventLane(toEvent, options);
    const id = String(flow.id || `${flow.from_event_id}->${flow.to_event_id}`);
    const base = {
      id,
      flowId: id,
      label: String(flow.label || "feedback flow"),
      labelShort: feedbackVisibleLabel(flow),
      channel: String(flow.channel || ""),
      fromEventId: String(fromEvent.id),
      toEventId: String(toEvent.id),
      fromEdge,
      fromAtTime: Boolean(timing),
      sourceTitle: feedbackEndpointTitle(fromEvent, "Send", fromTime, options, window?.end !== undefined),
      targetTitle: feedbackEndpointTitle(toEvent, "Receive", toTime, options, false, latency),
      eventIds: [String(flow.to_event_id), String(flow.from_event_id)],
      classNames: "feedback-flow-group",
      domain: {
        kind: "feedback",
        flow,
      },
    };
    if (fromLane.id === toLane.id) {
      return {
        ...base,
        type: "feedback-inline",
        laneId: fromLane.id,
        start: Math.min(fromTime, toTime),
        end: Math.max(fromTime, toTime),
        fromTime,
        toTime,
      };
    }
    return {
      ...base,
      type: "feedback-cross",
      fromLaneId: fromLane.id,
      toLaneId: toLane.id,
      fromTime,
      toTime,
    };
  }

  function loopCountLabel(event) {
    const meta = event && event.meta && typeof event.meta === "object" ? event.meta : {};
    const count = meta.count_display || meta.count;
    if (count === undefined || count === null || count === "") {
      return "";
    }
    return String(count) === "forever" ? "forever" : `x${count}`;
  }

  function isExecutedLoop(event, playback = true) {
    return event?.kind === "loop_block" && Boolean(event.meta?.observed || playback && event.meta?.playback);
  }

  function collapsedExecutedLoopIds(events, expanded, playback) {
    return new Set(events.filter((event) => isExecutedLoop(event, playback) &&
      !expanded.has(String(event.meta.loop_preview_key || event.meta.loop_id || event.id)))
      .flatMap((event) => event.meta.hidden_event_ids || []));
  }

  function loopAnnotation(event, options, omitted) {
    const start = eventEdgeTime(event, "t0", options);
    const end = eventEdgeTime(event, "t1", options, start);
    const meta = event && event.meta && typeof event.meta === "object" ? event.meta : {};
    const executed = isExecutedLoop(event);
    const loopId = executed ? "loop" : String(meta.loop_id || event.id || "loop");
    const count = loopCountLabel(event);
    const firstIterationEventIds = Array.isArray(meta.first_iteration_event_ids)
      ? meta.first_iteration_event_ids.map((eventId) => String(eventId))
      : [];
    return {
      id: `${event.id}:loop-range`,
      type: "loop-range",
      laneId: eventLane(event, options).id,
      eventId: String(event.id),
      eventIds: [String(event.id), ...firstIterationEventIds],
      start,
      end: omitted ? Math.max(start, end - (omitted.end - omitted.start)) : end,
      label: executed ? count.replace(/^x/, "×") : [loopId, count].filter(Boolean).join(" "),
      title: String(event.label || `loop ${loopId}`),
      domain: {
        eventId: event.id,
        kind: event.kind,
        meta,
      },
    };
  }

  function ticksForViewport(viewport) {
    const span = Math.max(viewport.end - viewport.start, 1);
    return [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
      const value = viewport.start + span * ratio;
      return {
        leftPercent: Math.round(ratio * 100000) / 1000,
        label: `${Math.round(value)} ns`,
      };
    });
  }

  function applyInstructionLayout(lanes, annotations, events) {
    const eventOrder = new Map(events.map((event, index) => [String(event.id), index]));
    const eventById = new Map(events.map((event) => [String(event.id), event]));
    const warning = (block) => !block.pointMarker && (block.kind === "underflow_warning" || block.kind === "analysis_incomplete");
    const occupiesSlot = (block) => !warning(block) && !block.pointMarker;
    const packetEffects = new Set(["marker_state", "latched_state_applied", "sync_wait", "trigger_wait",
      "feedback_receive", "feedback_send", "acquisition_result"]);
    const visibleLaneIds = new Set(lanes.filter((lane) => !lane.hidden).map((lane) => lane.id));
    const positions = new Map();
    const startsByLane = new Map();
    const durationsByLane = new Map();
    const offsetsByLane = new Map();
    const blockOrder = (block) => Math.min(...block.eventIds.map((id) => eventOrder.get(id) ?? events.length));
    let maxCount = 1;
    for (const lane of lanes) {
      if (lane.kind === "runtime") lane.label = `${lane.sequencer || lane.label} · RT`;
      const conditionalTimeUnknown = lane.blocks.some((block) => !block.pointMarker && block.domain.meta.conditional_time_unknown);
      lane.blocks.sort((left, right) => (conditionalTimeUnknown ? 0 : left.start - right.start) || blockOrder(left) - blockOrder(right));
      const commands = lane.blocks.filter(occupiesSlot);
      startsByLane.set(lane.id, commands.map((block) => block.start));
      durationsByLane.set(lane.id, commands.map((block) => block.duration));
      if (!lane.hidden) maxCount = Math.max(maxCount, commands.length);
      commands.forEach((block, index) => {
        block.start = index;
        block.duration = 1;
        for (const id of block.eventIds) {
          positions.set(id, { laneId: lane.id, laneKind: lane.kind, start: index, end: index + 1 });
        }
      });
    }
    // Both alternatives begin after the shared branch, not at instruction zero.
    for (const lane of lanes.filter((item) => item.branchComparisonPath)) {
      const anchor = events.find((event) => event.sequencer_id === lane.sequencer &&
        (event.meta || {}).branch_id === lane.branchComparisonId && !(event.meta || {}).branch_comparison_path &&
        event.kind === (lane.kind === "q1-issue" ? "q1_issue" : "branch_region"));
      const offset = anchor ? (positions.get(String(anchor.id)) || {}).end || 0 : 0;
      offsetsByLane.set(lane.id, offset);
      const commands = lane.blocks.filter(occupiesSlot);
      for (const block of commands) {
        block.start += offset;
        for (const id of block.eventIds) {
          const position = positions.get(id);
          position.start += offset;
          position.end += offset;
        }
      }
      if (!lane.hidden) maxCount = Math.max(maxCount, offset + commands.length);
    }
    function insertionPosition(laneId, time) {
      const starts = startsByLane.get(laneId) || [];
      const index = starts.findIndex((start) => start >= time);
      return (offsetsByLane.get(laneId) || 0) + (index < 0 ? starts.length : index);
    }
    function positionAtTime(laneId, time) {
      const starts = startsByLane.get(laneId) || [];
      const durations = durationsByLane.get(laneId) || [];
      const index = starts.findIndex((start, index) => start <= time && time < start + durations[index]);
      return index < 0 ? insertionPosition(laneId, time)
        : (offsetsByLane.get(laneId) || 0) + index + (time - starts[index]) / durations[index];
    }
    for (const lane of lanes) {
      for (const block of lane.blocks.filter((candidate) => candidate.pointMarker)) {
        block.start = positionAtTime(lane.id, block.start);
        for (const id of block.eventIds) positions.set(id, { laneId: lane.id, laneKind: lane.kind, start: block.start, end: block.start });
      }
      // Queue warnings flag an RT boundary rather than occupying another instruction.
      for (const block of lane.blocks.filter(warning)) {
        const event = eventById.get(block.eventId);
        const meta = event.meta || {};
        const candidates = meta.rt_packet_id === undefined ? [] : events.filter((candidate) => {
          const other = candidate.meta || {};
          return candidate.sequencer_id === event.sequencer_id && eventLaneName(candidate).startsWith("rt.") && !isTimelinePoint(candidate) &&
            candidate.kind !== "underflow_warning" && candidate.kind !== "analysis_incomplete" && !packetEffects.has(candidate.kind) &&
            other.rt_packet_id === meta.rt_packet_id &&
            other.branch_comparison_branch_id === meta.branch_comparison_branch_id &&
            other.branch_comparison_path === meta.branch_comparison_path &&
            other.loop_preview === meta.loop_preview && other.loop_iteration_index === meta.loop_iteration_index &&
            (other.diff_status === "removed") === (meta.diff_status === "removed");
        });
        const anchors = candidates.map((candidate) => positions.get(String(candidate.id))).filter(Boolean);
        const starts = new Set(anchors.map((anchor) => anchor.start));
        block.start = starts.size === 1 ? anchors[0].start : insertionPosition(lane.id, block.start);
        block.duration = 0;
        for (const id of block.eventIds) positions.set(id, { laneId: lane.id, laneKind: lane.kind, start: block.start, end: block.start });
        if (!lane.hidden) maxCount = Math.max(maxCount, block.start);
      }
    }
    for (const annotation of annotations) {
      if (annotation.type === "loop-range") {
        const members = annotation.eventIds.map((id) => positions.get(id)).filter((position) => position && visibleLaneIds.has(position.laneId));
        const anchor = members.find((position) => position.laneId === annotation.laneId)
          || members.find((position) => position.laneKind !== "q1-issue") || members[0];
        if (anchor) {
          const laneMembers = members.filter((position) => position.laneId === anchor.laneId);
          annotation.laneId = anchor.laneId;
          annotation.start = Math.min(...laneMembers.map((position) => position.start));
          annotation.end = Math.max(...laneMembers.map((position) => position.end));
        } else {
          annotation.start = insertionPosition(annotation.laneId, annotation.start);
          annotation.end = annotation.start;
        }
      } else {
        const flow = annotation.domain.flow;
        const from = positions.get(String(flow.from_event_id));
        const to = positions.get(String(flow.to_event_id));
        annotation.fromTime = from && !annotation.fromAtTime ? from[annotation.fromEdge === "t1" ? "end" : "start"]
          : positionAtTime(annotation.fromLaneId || annotation.laneId, annotation.fromTime);
        annotation.toTime = to ? to.start : positionAtTime(annotation.toLaneId || annotation.laneId, annotation.toTime);
        if (annotation.type === "feedback-inline") {
          annotation.start = Math.min(annotation.fromTime, annotation.toTime);
          annotation.end = Math.max(annotation.fromTime, annotation.toTime);
        }
      }
    }
    return maxCount;
  }

  function buildQ1TimelineSelectionIndex(timelineIr) {
    const events = Array.isArray(timelineIr && timelineIr.events) ? timelineIr.events : [];
    const eventsById = new Map(events.map((event) => [String(event.id), event]));
    const index = new Map(events.map((event) => [String(event.id), {
      relatedEventIds: new Set(), appliedEventIds: new Set(), capturedEventIds: new Set(), rtEventIds: new Set(),
    }]));
    const packetKey = (event) => {
      const meta = event.meta || {};
      return JSON.stringify([event.sequencer_id, meta.rt_packet_id, meta.branch_comparison_branch_id,
        meta.branch_comparison_path, meta.loop_preview, meta.loop_iteration_index, meta.diff_status]);
    };
    const sameSequencer = (left, right) => left && right && left.sequencer_id === right.sequencer_id;
    const issuesByPacket = new Map();
    for (const event of events) {
      if (event.kind === "q1_issue" && (event.meta || {}).rt_packet_id != null && !(event.meta || {}).preview_source_event_id) {
        const key = packetKey(event);
        issuesByPacket.set(key, issuesByPacket.has(key) ? null : event);
      }
    }
    const nonCommandKinds = new Set([
      "marker_state", "latched_state_applied", "rt_queue_enqueue", "rt_queue_dequeue", "q1_stall",
      "sync_wait", "trigger_wait", "feedback_pop", "feedback_receive", "feedback_send", "acquisition_result",
    ]);
    const commands = events.filter((event) => {
      if (!/^(?:branch_compare\.(?:taken|fallthrough)\.)*rt\./.test(String(event.lane || "")) ||
        isTimelinePoint(event) || nonCommandKinds.has(event.kind)) return false;
      const meta = event.meta || {};
      if (!meta.preview_source_event_id) return true;
      const issue = eventsById.get(String(meta.q1_issue_event_id));
      const issueMeta = issue && issue.meta || {};
      return issue && issue.kind === "q1_issue" && sameSequencer(issue, event) && issueMeta.preview_source_event_id &&
        issueMeta.loop_preview === meta.loop_preview && issueMeta.loop_iteration_index === meta.loop_iteration_index;
    });
    const link = (left, right) => {
      if (left === right) return;
      index.get(left).relatedEventIds.add(right);
      index.get(right).relatedEventIds.add(left);
    };
    const pendingWaits = events.filter((event) => primaryWaitKindByAuxiliary.has(event.kind) && event.meta?.pending);
    const navigationCommands = [...commands, ...pendingWaits];
    for (const event of navigationCommands) {
      const meta = event.meta || {};
      const issue = meta.q1_issue_event_id !== undefined
        ? eventsById.get(String(meta.q1_issue_event_id)) : issuesByPacket.get(packetKey(event));
      if (issue && issue.kind === "q1_issue" && sameSequencer(issue, event)) {
        link(String(issue.id), String(event.id));
        index.get(String(issue.id)).rtEventIds.add(String(event.id));
      }
    }

    // Select the first application only among explicit setter references, never by proximity or source lines.
    const concreteStart = (event) => {
      const value = event.t0 ?? event.t0_ns;
      if (typeof value === "number") return value;
      return value && (!value.kind || value.kind === "concrete") ? value.value ?? value.value_ns : undefined;
    };
    const firstApplications = new Map();
    for (const event of commands) {
      const meta = event.meta || {};
      if (["wait", "wait_sync", "wait_trigger"].includes(event.kind)) continue;
      const skipped = event.kind === "conditional_skip" || meta.conditional_skipped;
      const setterIds = skipped ? meta.captured_setter_event_ids
        : meta.committed_setter_event_ids ?? meta.applied_setter_event_ids;
      const settings = skipped ? meta.captured_state : meta.applied_state;
      for (const [field, setterId] of Object.entries(setterIds || {})) {
        if (skipped && !["phase_delta", "phase_reset"].includes(field)) continue;
        if (!Object.prototype.hasOwnProperty.call(settings || {}, field)) continue;
        const setter = eventsById.get(String(setterId));
        const setterMeta = setter && setter.meta || {};
        const issue = eventsById.get(String(setterMeta.q1_issue_event_id));
        if (!setter || setter.kind !== "latched_state_pending" || setterMeta.field !== field ||
          !issue || issue.kind !== "q1_issue" || !sameSequencer(setter, event) || !sameSequencer(issue, event)) continue;
        if (skipped) {
          link(String(issue.id), String(event.id));
          index.get(String(issue.id)).capturedEventIds.add(String(event.id));
          continue;
        }
        const key = JSON.stringify([event.sequencer_id, setterId, field, meta.branch_comparison_branch_id, meta.branch_comparison_path]);
        const applicationKey = JSON.stringify([event.sequencer_id, meta.rt_packet_id ?? meta.q1_issue_event_id ?? event.id,
          meta.loop_preview, meta.loop_iteration_index]);
        const start = concreteStart(event);
        const previous = firstApplications.get(key);
        if (!previous || (Number.isFinite(start) && Number.isFinite(previous.start) && start < previous.start)) {
          firstApplications.set(key, { issueId: String(issue.id), applicationKey, start, eventIds: [] });
        }
        const first = firstApplications.get(key);
        if (first.applicationKey === applicationKey) first.eventIds.push(String(event.id));
      }
    }
    for (const { issueId, eventIds } of firstApplications.values()) {
      for (const eventId of eventIds) {
        link(issueId, eventId);
        for (const appliedId of eventIds) index.get(eventId).appliedEventIds.add(appliedId);
        index.get(issueId).appliedEventIds.add(eventId);
      }
    }
    const rtCommands = new Set(navigationCommands.filter((event) =>
      !["loop_block", "loop_iteration_preview", "branch_region"].includes(event.kind) &&
      (event.kind !== "unknown_region" || event.meta?.conditional_timing_known)).map((event) => String(event.id)));
    for (const id of rtCommands) index.get(id).rtNavigationEventId = id;
    for (const [primary, aliases] of completedWaitAliases(events)) {
      for (const alias of aliases) index.get(alias).rtNavigationEventId = primary;
    }
    // Navigation follows instruction order; Q1 and RT clocks can be far apart.
    const issues = events.filter((event) => event.kind === "q1_issue");
    const scope = (event, common = false) => JSON.stringify([event.sequencer_id,
      common ? undefined : event.meta?.branch_comparison_branch_id,
      common ? undefined : event.meta?.branch_comparison_path, event.meta?.diff_status === "removed",
      event.meta?.preview_source_event_id ? [event.meta.loop_preview, event.meta.loop_iteration_index] : undefined]);
    const previous = new Map();
    const leading = new Map();
    for (const issue of issues) {
      const entry = index.get(String(issue.id));
      const key = scope(issue);
      let target = [...entry.rtEventIds].find((id) => rtCommands.has(id)) || previous.get(key);
      if (!target && issue.meta?.branch_comparison_path) {
        const time = eventTimeValue(issue.t0, Number.NaN);
        const shared = issues.filter((candidate) => scope(candidate) === scope(issue, true) &&
          eventTimeValue(candidate.t0, Number.NaN) <= time && index.get(String(candidate.id)).rtEventIds.size).at(-1);
        target = shared && [...index.get(String(shared.id)).rtEventIds].find((id) => rtCommands.has(id));
      }
      if (target) {
        entry.rtNavigationEventId = target;
        previous.set(key, target);
      } else {
        if (!leading.has(key)) leading.set(key, []);
        leading.get(key).push(entry);
      }
    }
    for (const [key, entries] of leading) {
      if (previous.has(key)) for (const entry of entries) entry.rtPreparation = true;
    }
    // Q1 setup points to the next RT command; trailing instructions keep their preceding RT target.
    const parameterSetters = new Set(events.filter((event) => event.kind === "latched_state_pending" &&
      !["condition", "set_cond", "set_time_ref"].includes(event.meta?.field)).map((event) => String(event.meta?.q1_issue_event_id)));
    const next = new Map();
    for (const issue of issues.slice().reverse()) {
      const entry = index.get(String(issue.id));
      const key = scope(issue);
      const own = [...entry.rtEventIds].find((id) => rtCommands.has(id));
      if (own) next.set(key, own);
      const applicationOrCapture = [...entry.appliedEventIds, ...entry.capturedEventIds].find((id) => rtCommands.has(id));
      if (applicationOrCapture) {
        entry.rtNavigationEventId = applicationOrCapture;
        entry.rtPreparation = false;
      } else if (parameterSetters.has(String(issue.id))) {
        entry.rtNavigationEventId = undefined;
      } else if (next.has(key)) entry.rtNavigationEventId = next.get(key);
    }
    return new Map(Array.from(index, ([id, entry]) => [id, {
      relatedEventIds: [...entry.relatedEventIds], appliedEventIds: [...entry.appliedEventIds], rtEventIds: [...entry.rtEventIds],
      capturedEventIds: [...entry.capturedEventIds],
      rtNavigationEventId: entry.rtNavigationEventId, rtPreparation: entry.rtPreparation === true,
    }]));
  }

  function buildParameterChanges(events, selectionIndex, options) {
    const byId = new Map(events.map((event) => [String(event.id), event]));
    const changesById = new Map();
    const concreteTime = (event, edge, basis = options.timeBasis) => {
      const meta = event.meta || {};
      const value = basis === "aligned" && meta[`aligned_${edge}`] !== undefined
        ? meta[`aligned_${edge}`] : event[edge] ?? event[`${edge}_ns`];
      if (typeof value === "number" && Number.isFinite(value)) return value;
      if (value && (!value.kind || value.kind === "concrete") && Number.isFinite(value.value ?? value.value_ns)) return value.value ?? value.value_ns;
      return undefined;
    };
    const valueDisplay = (value) => {
      if (value === undefined || value === null) return "?";
      if (Array.isArray(value)) return value.map(valueDisplay).join(", ");
      if (typeof value === "object") return String(value.display ?? value.value ?? value.expr ?? "?");
      return String(value);
    };
    const scopeKey = (event, baseline = false) => {
      const meta = event.meta || {};
      return JSON.stringify([event.sequencer_id, baseline ? undefined : meta.branch_comparison_branch_id,
        baseline ? undefined : meta.branch_comparison_path, meta.diff_status === "removed" ? "removed" : undefined]);
    };
    const packetKey = (event) => {
      const meta = event.meta || {};
      return JSON.stringify([scopeKey(event), meta.rt_packet_id ?? event.id,
        meta.loop_preview, meta.loop_iteration_index, concreteTime(event, "t0", "local")]);
    };
    const commands = events.filter((event) => /^(?:branch_compare\.(?:taken|fallthrough)\.)?rt\./.test(String(event.lane || "")) &&
      !["wait", "wait_sync", "wait_trigger", "conditional_skip", "marker_state", "latched_state_applied"].includes(event.kind) &&
      !isTimelinePoint(event) && !event.meta?.conditional_skipped && event.meta?.applied_state);
    const unknownFields = new Set(commands.flatMap((event) => concreteTime(event, "t0", "local") === undefined
      ? Object.keys(event.meta.applied_state).map((field) => `${scopeKey(event)}:${field}`) : []));
    const previousByEvent = new Map();
    const previousByPacket = new Map();
    const appliedValues = new Map();
    for (const event of [...commands].sort((left, right) =>
      (concreteTime(left, "t0", "local") ?? Infinity) - (concreteTime(right, "t0", "local") ?? Infinity))) {
      const previous = new Map();
      const time = concreteTime(event, "t0", "local");
      for (const [field, value] of Object.entries(event.meta.applied_state)) {
        const key = `${scopeKey(event)}:${field}`;
        const baseline = `${scopeKey(event, true)}:${field}`;
        const packet = `${packetKey(event)}:${field}`;
        if (!previousByPacket.has(packet)) {
          const before = appliedValues.get(key) || appliedValues.get(baseline);
          previousByPacket.set(packet, time !== undefined && before && before.time < time &&
            !unknownFields.has(key) && !unknownFields.has(baseline) ? valueDisplay(before.value) : "?");
        }
        previous.set(field, previousByPacket.get(packet));
        if (time !== undefined) appliedValues.set(key, { time, value });
      }
      previousByEvent.set(String(event.id), previous);
    }
    const append = (id, change) => {
      if (!changesById.has(id)) changesById.set(id, []);
      changesById.get(id).push(change);
    };
    for (const setter of events.filter((event) => event.kind === "latched_state_pending")) {
      const meta = setter.meta || {};
      const issue = byId.get(String(meta.q1_issue_event_id));
      if (!issue || issue.kind !== "q1_issue" || issue.sequencer_id !== setter.sequencer_id ||
        !meta.field || ["set_cond", "set_time_ref"].includes(meta.field)) continue;
      const groups = new Map();
      for (const id of selectionIndex.get(String(issue.id))?.appliedEventIds || []) {
        const applied = byId.get(id);
        if (!applied || applied.meta?.applied_setter_event_ids?.[meta.field] !== setter.id) continue;
        const key = packetKey(applied);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(applied);
      }
      for (const group of groups.size ? groups.values() : [[]]) {
        const applied = group[0];
        const change = {
          field: meta.field,
          setterEventId: String(setter.id),
          setterIssueId: String(issue.id),
          setterTime: concreteTime(issue, "t1"),
          applyTime: applied ? concreteTime(applied, "t0") : undefined,
          applyEventIds: group.map((event) => String(event.id)),
          previousDisplay: previousByEvent.get(String(applied?.id))?.get(meta.field) || "?",
          valueDisplay: valueDisplay(applied ? applied.meta.applied_state[meta.field] : meta.value),
        };
        append(String(issue.id), change);
        for (const event of group) append(String(event.id), change);
      }
    }
    return changesById;
  }

  function buildRtDetails(events, selectionIndex) {
    const byId = new Map(events.map((event) => [String(event.id), event]));
    const issuesByRt = new Map();
    for (const event of events.filter((item) => item.kind === "q1_issue")) {
      for (const id of selectionIndex.get(String(event.id))?.rtEventIds || []) issuesByRt.set(id, event);
    }
    const valueDisplay = (value) => {
      if (value === undefined || value === null) return "?";
      if (Array.isArray(value)) return value.map(valueDisplay).join(", ");
      return typeof value === "object" ? String(value.display ?? value.value ?? value.expr ?? "?") : String(value);
    };
    const concreteValue = (value) => {
      if (Array.isArray(value)) {
        const values = value.map(concreteValue);
        return values.every((item) => item !== undefined) ? values : undefined;
      }
      if (value && typeof value === "object") return !value.kind || value.kind === "concrete" ? concreteValue(value.value) : undefined;
      return typeof value === "number" && Number.isFinite(value) || typeof value === "boolean" ? value : undefined;
    };
    const row = (name, value, previous) => {
      const currentValue = concreteValue(value), previousValue = concreteValue(previous);
      const changed = currentValue !== undefined && previousValue !== undefined && JSON.stringify(currentValue) !== JSON.stringify(previousValue);
      return { name, valueDisplay: valueDisplay(value), changed, ...(changed ? { previousDisplay: valueDisplay(previous) } : {}) };
    };
    const scopeKey = (event, common = false) => {
      const meta = event.meta || {};
      return JSON.stringify([event.sequencer_id, common ? undefined : meta.branch_comparison_branch_id,
        common ? undefined : meta.branch_comparison_path, meta.diff_status === "removed" ? "removed" : undefined]);
    };
    const excluded = new Set(["marker_state", "latched_state_applied", "rt_queue_enqueue", "rt_queue_dequeue", "q1_stall",
      "sync_wait", "trigger_wait", "feedback_pop", "feedback_receive", "feedback_send", "acquisition_result",
      "loop_block", "loop_iteration_preview", "branch_region", "control_flow_loop", "control_flow_branch"]);
    const states = new Map(), packets = new Map(), details = new Map();
    const ordered = events.map((event, index) => ({ event, index })).filter(({ event }) =>
      eventLaneName(event).startsWith("rt.") && !isTimelinePoint(event) && !excluded.has(event.kind));
    for (const [index, event] of events.entries()) {
      const end = concreteValue(event.t1 ?? event.t1_ns);
      if (event.meta?.latched_state_at_join && Number.isFinite(end)) {
        ordered.push({event: {...event, id: `${event.id}:joined-state`, kind: "branch_state_at_join", t0: end}, index: index + 0.5});
      }
    }
    const time = (event) => {
      const value = event.t0 ?? event.t0_ns;
      return typeof value === "number" ? value : value && (!value.kind || value.kind === "concrete") ? value.value ?? value.value_ns : undefined;
    };
    const eventOrder = new Map(events.map((event, index) => [String(event.id), index]));
    for (const loop of events.filter((event) => event.kind === "loop_block" &&
      (event.meta?.uncertain_latched_fields?.length || event.meta?.uncertain_registers?.length))) {
      const meta = loop.meta;
      const members = Object.values(meta.preview_iteration_event_ids || { 0: meta.first_iteration_event_ids || [] }).flat();
      const memberIndexes = members.map((id) => eventOrder.get(String(id)) ?? -1);
      const total = meta.total_duration === undefined ? time({t0: loop.duration}) * Number(meta.count) : time({t0: meta.total_duration});
      const end = time(loop) + total;
      ordered.push({event: { ...loop, t0: Number.isFinite(end) ? end : undefined }, index: Math.max(eventOrder.get(String(loop.id)), ...memberIndexes) + 0.5});
    }
    ordered.sort((a, b) => a.index - b.index);
    // IR order remains authoritative when a conditional execution makes timing unknown.
    if (ordered.every(({ event }) => Number.isFinite(time(event)))) ordered.sort((a, b) => time(a.event) - time(b.event) || a.index - b.index);
    for (const { event } of ordered) {
      const meta = event.meta || {}, scope = scopeKey(event), issue = issuesByRt.get(String(event.id));
      const key = JSON.stringify([scope, issue?.id ?? meta.rt_packet_id ?? event.id, meta.loop_preview, meta.loop_iteration_index]);
      if (packets.has(key)) {
        details.set(String(event.id), packets.get(key));
        continue;
      }
      if (!states.has(scope)) {
        const common = states.get(scopeKey(event, true));
        states.set(scope, { parameters: { ...common?.parameters }, registers: common?.registers, unknown: Boolean(common?.unknown) });
      }
      const state = states.get(scope), issueMeta = issue?.meta || {};
      if (event.kind === "branch_state_at_join") {
        for (const [field, value] of Object.entries(meta.latched_state_at_join)) {
          if (!(meta.pending_fields_at_join || []).includes(field)) state.parameters[field] = value;
        }
        continue;
      }
      if (event.kind === "loop_block") {
        for (const field of meta.uncertain_latched_fields || []) {
          if (field in state.parameters) state.parameters[field] = {kind: "unknown", display: "? (hidden loop iterations)"};
        }
        if (state.registers) state.registers = { ...state.registers, ...Object.fromEntries(
          (meta.uncertain_registers || []).map((name) => [name, {kind: "unknown", display: "?"}])) };
        continue;
      }
      const reads = issueMeta.register_reads;
      const registers = Object.entries(reads || {}).sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
        .map(([name, value]) => row(name, value, state.registers?.[name]));
      // A snapshot of every register prevents first use from being mistaken for a change.
      if (meta.rt_packet_id != null || issueMeta.rt_packet_id != null) state.registers = issueMeta.register_snapshot;
      const previous = { ...state.parameters };
      const blocked = event.kind === "unknown_region" && meta.playback && meta.rt_packet_id == null && !meta.output_state_unknown;
      const outputUnknown = meta.output_state_unknown || event.kind === "unknown_region" && !blocked && meta.output_state_unknown !== false;
      const ncoUncertain = Boolean(meta.nco_phase_uncertain);
      if (outputUnknown && !ncoUncertain) {
        state.parameters = {};
        state.unknown = true;
      }
      const skipped = event.kind === "conditional_skip" || meta.conditional_skipped;
      const applies = !skipped && !["wait", "wait_sync", "wait_trigger"].includes(event.kind) && (!outputUnknown || ncoUncertain);
      const applied = applies ? { ...meta.applied_state } : {};
      if (applies && meta.applied_state_mode === "snapshot") {
        state.parameters = {};
        state.unknown = false;
      }
      const updatedFields = meta.applied_updated_fields || Object.keys(applied);
      // An increment describes this application; retaining it would imply another increment.
      delete state.parameters.phase_delta;
      delete state.parameters.phase_reset;
      if (!updatedFields.includes("phase_delta")) delete applied.phase_delta;
      if (!updatedFields.includes("phase_reset")) delete applied.phase_reset;
      if (applies && meta.phase_reset && applied.phase === undefined) applied.phase = 0;
      for (const [name, setterId] of Object.entries(meta.applied_setter_event_ids || {})) {
        const setter = byId.get(String(setterId));
        const setterIssue = byId.get(String(setter?.meta?.q1_issue_event_id));
        if (meta.preview_source_event_id && setterIssue?.meta?.preview_source_event_id &&
          Object.values(setterIssue.meta.register_reads || {}).some((value) => concreteValue(value) === undefined) && name in applied) {
          applied[name] = { kind: "unknown", display: "? (loop iteration)" };
        }
      }
      Object.assign(state.parameters, applied);
      if (ncoUncertain) state.parameters.phase = {kind: "unknown", display: "? (NCO phase)"};
      const parameters = Object.entries(state.parameters).filter(([name]) =>
        name !== "scope_enable" || event.kind === "acquire" || event.kind === "upd_param" || event.kind === "upd_thres")
        .filter(() => !["stop", "feedback_pop", "feedback_com", "set_latch_en", "latch_rst"].includes(event.kind) && !String(event.kind).startsWith("fb_"))
        .map(([name, value]) => row(name, value, previous[name]));
      const result = { registers, parameters,
        ...(reads === undefined ? { registerNote: "Register operand values are unavailable." } : {}),
        ...(blocked ? { parameterNote: "Execution is blocked here; shown parameters are the last known applied values." }
          : state.unknown ? { parameterNote: "Some effective parameters are unknown after unresolved execution." } : {}) };
      packets.set(key, result);
      details.set(String(event.id), result);
    }
    return details;
  }

  function buildQ1TimelineSharedModel(timelineIr, options = {}) {
    const mode = options.mode === "debug" ? "debug" : "normal";
    const allEvents = Array.isArray(timelineIr && timelineIr.events) ? timelineIr.events : [];
    const playback = Boolean(timelineIr?.playback);
    const expandedLoops = new Set(options.expandedPlaybackLoops || []);
    const hidden = collapsedExecutedLoopIds(allEvents, expandedLoops, playback);
    if (mode === "normal") {
      const q1OnlyLoops = options.q1OnlyLoops || q1OnlyLoopIds(new Map(allEvents.map((event) => [String(event.id), event])));
      for (const id of q1OnlyLoops) hidden.add(id);
    }
    const events = allEvents.filter((event) => !hidden.has(String(event.id)) &&
      (options.layout === "instruction" || !event.meta?.conditional_time_unknown ||
        Number.isFinite(eventEdgeTime(event, "t0", options, Number.NaN))));
    const eventById = new Map(events.map((event) => [String(event.id), event]));
    const waitAliases = completedWaitAliases(events);
    const collapsedWaitIds = new Set([...waitAliases.values()].flat());
    const lanes = [];
    const laneById = new Map();
    const annotations = [];
    const playGroups = mergedPlayGroups(events, { timeBasis: options.timeBasis });
    const mergedPlayEventIds = new Set(playGroups.flatMap((group) => group.map((event) => String(event.id))));
    const expandedQ1IssueSequencers = new Set(
      Array.isArray(options.expandedQ1IssueSequencers)
        ? options.expandedQ1IssueSequencers.map((sequencer) => String(sequencer))
        : []
    );
    const instruction = options.layout === "instruction";
    const adapterOptions = { mode, layout: options.layout, timeBasis: options.timeBasis, mergedPlayEventIds, expandedQ1IssueSequencers,
      alignment: timelineIr?.alignment };
    const triggerArrivals = coincidentTriggerArrivals(events, adapterOptions);
    let maxTime = 1;

    function ensureLane(lane) {
      if (laneById.has(lane.id)) {
        return laneById.get(lane.id);
      }
      const created = { ...lane, blocks: [] };
      if (lane.kind === "q1-issue") {
        const simulation = timelineIr?.simulation?.[lane.sequencer];
        const underflow = timelineIr?.assumptions?.underflow;
        const assumed = simulation ? simulation.initial_buffer?.assumed_ns : underflow?.initial_buffer_assumed_ns;
        const possible = simulation ? simulation.initial_buffer?.possible_ns : underflow?.initial_buffer_possible_ns;
        if (Number.isFinite(assumed) && Number.isFinite(possible)) {
          created.label += ` · ${assumed / 1e6}–${possible / 1e6} ms buffer`;
          created.labelTitle = `Initial RT buffer assumption: ${assumed / 1e6} ms, tentatively up to ${possible / 1e6} ms.`;
        }
      }
      if (lane.parentGroupId && !laneById.has(lane.parentGroupId)) {
        ensureLane({
          id: lane.parentGroupId,
          label: lane.sequencer || lane.parentGroupId.replace(/^sequencer:/, ""),
          kind: "runtime",
          sequencer: lane.sequencer,
        });
      }
      laneById.set(lane.id, created);
      lanes.push(created);
      return created;
    }

    for (const event of events) {
      const triggerGroup = triggerArrivals.get(String(event.id));
      if (!shouldIncludeEvent(event, { mode }) || collapsedWaitIds.has(String(event.id)) || (triggerGroup && triggerGroup.primary !== event)) {
        continue;
      }
      if (event.kind === "loop_block") {
        if (instruction || event.meta?.branch_comparison_path || isExecutedLoop(event)) ensureLane(eventLane(event, adapterOptions));
        const omitted = isExecutedLoop(event, playback) && collapsedLoopInterval(event, expandedLoops, playback);
        const annotation = loopAnnotation(event, adapterOptions, omitted);
        annotations.push(annotation);
        maxTime = Math.max(maxTime, eventEdgeTime(event, "t1", adapterOptions, annotation.end));
        continue;
      }
      if (event.kind === "loop_iteration_preview") {
        continue;
      }
      if (mergedPlayEventIds.has(String(event.id))) {
        continue;
      }
      const block = eventBlock(event, adapterOptions);
      block.eventIds.push(...(waitAliases.get(block.id) || []));
      if (triggerGroup) {
        const counts = new Map();
        for (const arrival of triggerGroup.arrivals) {
          if (arrival !== event) block.eventIds.push(String(arrival.id));
          const address = arrival.meta?.address ?? "unknown";
          counts.set(address, (counts.get(address) || 0) + 1);
        }
        const addresses = [...counts].map(([address, count]) => count === 1 ? address : `${address} ×${count}`);
        block.title = `Trigger addresses ${addresses.join(", ")} · ${block.start} ns`;
        block.search += ` ${block.eventIds.join(" ")} ${block.title}`.toLowerCase();
      }
      const lane = ensureLane(eventLane(event, adapterOptions));
      if (!lane.hidden) maxTime = Math.max(maxTime, block.start + Math.max(block.duration, 1));
      lane.blocks.push(block);
      if (!instruction && event.kind === "acquire") {
        const acquisition = acquisitionBlock(event, adapterOptions);
        const row = ensureLane(acquisitionLane(event, adapterOptions));
        row.blocks.push(acquisition);
        maxTime = Math.max(maxTime, acquisition.start + acquisition.duration);
        if (event.meta?.scope_acquisition_window) {
          const scope = acquisitionBlock(event, adapterOptions, true);
          row.blocks.push(scope);
          row.acquisitionTracks = 2;
          maxTime = Math.max(maxTime, scope.start + scope.duration);
        }
      }
    }

    for (const group of playGroups) {
      const event = mergedPlayEvent(group, adapterOptions);
      const block = eventBlock(event, adapterOptions);
      maxTime = Math.max(maxTime, block.start + Math.max(block.duration, 1));
      ensureLane(eventLane(event, adapterOptions)).blocks.push(block);
    }

    mergePacketStateBlocks(lanes, eventById, adapterOptions);

    const selectionIndex = buildQ1TimelineSelectionIndex(timelineIr);
    const parameterChanges = buildParameterChanges(allEvents, selectionIndex, adapterOptions);
    const rtDetails = buildRtDetails(allEvents, selectionIndex);
    for (const lane of lanes) {
      for (const block of lane.blocks) {
        const detail = rtDetails.get(block.eventId);
        if (detail && block.kind !== "acquisition_window") block.rtDetails = detail;
        const changes = new Set((block.eventIds || [block.eventId]).flatMap((id) => parameterChanges.get(id) || []));
        if (changes.size && block.kind !== "acquisition_window") block.parameterChanges = [...changes];
        const rtEventIds = selectionIndex.get(block.eventId)?.rtEventIds || [];
        if (block.kind !== "q1_issue" || !rtEventIds.length) continue;
        const event = eventById.get(block.eventId);
        block.issueMarker = true;
        block.rtEventIds = rtEventIds;
        const issueStart = adapterOptions.timeBasis === "aligned" && Number.isFinite(event.meta?.aligned_t0)
          ? `${event.meta.aligned_t0} ns` : displayValue(event.t0 ?? event.t0_ns, "unknown");
        const timing = q1IssueTimingText(event, adapterOptions) || `Q1 instruction start: ${issueStart}`;
        block.title = `Issue ${block.label} | ${timing} | Select to trace RT execution`;
      }
    }

    for (const flow of Array.isArray(timelineIr && timelineIr.feedback_flows) ? timelineIr.feedback_flows : []) {
      const annotation = feedbackAnnotation(flow, eventById, adapterOptions);
      if (!annotation) {
        continue;
      }
      for (const id of [flow.from_event_id, flow.to_event_id]) {
        const event = eventById.get(String(id));
        if (["feedback_pop", "feedback_send", "feedback_receive"].includes(event.kind)) ensureLane(eventLane(event, adapterOptions));
      }
      annotations.push(annotation);
      maxTime = Math.max(
        maxTime,
        annotation.end || annotation.toTime || annotation.fromTime || 1,
      );
    }

    const comparisonGroups = new Map();
    for (const lane of lanes.filter((item) => item.branchComparisonPath)) {
      const key = JSON.stringify([lane.sequencer, lane.branchComparisonId]);
      if (!comparisonGroups.has(key)) comparisonGroups.set(key, []);
      comparisonGroups.get(key).push(lane);
    }
    const orderedLanes = lanes.flatMap((lane) => {
      if (!lane.branchComparisonPath) return [lane];
      const key = JSON.stringify([lane.sequencer, lane.branchComparisonId]);
      const group = comparisonGroups.get(key) || [];
      comparisonGroups.delete(key);
      const rank = (item) => (item.branchComparisonPath === "taken" ? 0 : 5) +
        ({ "branch-runtime": 0, acquisition: 1, "q1-issue": 2, "runtime-path": 3, debug: 4 }[item.kind] || 0);
      return group.sort((left, right) => rank(left) - rank(right));
    });
    lanes.splice(0, lanes.length, ...orderedLanes);
    if (instruction) maxTime = applyInstructionLayout(lanes, annotations, events);
    const commandPositions = new Map();
    for (const lane of lanes) {
      for (const block of lane.blocks) {
        if (block.acquisitionMode || ["underflow_warning", "analysis_incomplete"].includes(block.kind)) continue;
        for (const id of block.eventIds) commandPositions.set(id, { lane, block });
      }
    }
    const sameOccurrence = (left, right) => left.sequencer_id === right.sequencer_id &&
      ["branch_comparison_branch_id", "branch_comparison_path", "loop_preview", "loop_iteration_index", "diff_status"]
        .every((key) => left.meta?.[key] === right.meta?.[key]);
    function addError(target, eventId, message) {
      const errors = target.block.diagnosticErrors ||= [];
      if (errors.some((error) => error.eventId === eventId && error.message === message)) return;
      const error = { eventId, message };
      errors.push(error);
      if (target.lane.kind === "q1-issue" && target.lane.hidden) {
        const parent = laneById.get(target.lane.parentGroupId);
        (parent.q1Errors ||= []).push(error);
      }
      if (!target.block.eventIds.includes(eventId)) target.block.eventIds = [...target.block.eventIds, eventId];
    }
    for (const event of events) {
      if (event.kind !== "underflow_warning" || isTimelinePoint(event) || !shouldIncludeEvent(event, adapterOptions)) continue;
      const eventId = String(event.id);
      const message = event.meta?.diagnostic_message || underflowLabel(event);
      const lane = laneById.get(eventLane(event, adapterOptions).id);
      const block = lane.blocks.find((candidate) => candidate.id === eventId);
      const target = events.filter((candidate) => sameOccurrence(event, candidate) &&
        event.meta?.rt_packet_id !== undefined && candidate.meta?.rt_packet_id === event.meta.rt_packet_id &&
        eventLaneName(candidate).startsWith("rt.") && !isTimelinePoint(candidate) &&
        !["underflow_warning", "analysis_incomplete", "marker_state", "latched_state_applied", "sync_wait", "trigger_wait",
          "feedback_receive", "feedback_send", "acquisition_result"].includes(candidate.kind))
        .map((candidate) => commandPositions.get(String(candidate.id)))
        .find((position) => position && !position.lane.hidden);
      if (target) {
        lane.blocks.splice(lane.blocks.indexOf(block), 1);
        addError(target, eventId, message);
      } else {
        block.duration = 0;
        block.diagnosticPlacement = "point";
        block.diagnosticErrors = [{ eventId, message }];
      }
    }
    for (const diagnostic of timelineIr.diagnostics || []) {
      if (diagnostic.severity !== "error") continue;
      const related = (diagnostic.related_events || []).map((id) => allEvents.find((event) => String(event.id) === String(id))).filter(Boolean);
      if (related.some((event) => ["underflow_warning", "analysis_incomplete"].includes(event.kind))) continue;
      const feedbackDiagnostic = diagnostic.category === "feedback_latency_violation" || diagnostic.details?.analysis_scope === "feedback";
      const event = (feedbackDiagnostic ? related.find((candidate) => candidate.kind === "feedback_pop") : undefined) ||
        related.find((candidate) => commandPositions.has(String(candidate.id))) ||
        (related.length === 0 ? events.find((candidate) => candidate.kind === "q1_issue" && diagnostic.source?.file &&
          candidate.source?.file === diagnostic.source.file && candidate.source?.line === diagnostic.source.line) : undefined);
      const eventId = String(event?.id);
      const target = commandPositions.get(eventId);
      if (target) addError(target, eventId, diagnostic.details?.display_message ||
        networkConflictDisplayMessage(diagnostic, timelineIr.alignment, options.timeBasis) || diagnostic.message || diagnostic.category);
    }
    const viewport = {
      start: 0,
      end: maxTime,
    };
    return {
      title: "Q1ASM Timeline",
      layout: instruction ? "instruction" : "time",
      subtitle: instruction ? "Independent instruction order per lane; columns do not show simultaneous time" : `${lanes.filter((lane) => !lane.hidden).length} lanes / ${events.length} events`,
      totalTime: maxTime,
      viewport,
      ticks: instruction ? [...new Set([0, 0.25, 0.5, 0.75, 1].map((ratio) => Math.round(maxTime * ratio)))].map((index) => ({
        leftPercent: index / maxTime * 100,
        label: `#${index}`,
      })) : ticksForViewport(viewport),
      lanes,
      annotations,
      inspector: {
        title: "Selected event",
        rows: [],
      },
    };
  }

  function foldTimelineCommands(model, {mode, timeBasis, eventsById, expandedGroups, expandedLoops, hasQ1Control}) {
    const groupByEvent = new Map();
    const instruction = model.layout === "instruction";
    const expandedLoopMembers = new Set();
    if (mode === "normal" && !instruction && expandedLoops?.size) {
      for (const annotation of model.annotations) {
        const meta = annotation.domain?.meta;
        if (annotation.type !== "loop-range" || !expandedLoops.has(String(meta?.loop_preview_key || meta?.loop_id || annotation.eventId))) continue;
        for (const id of [...(meta?.first_iteration_event_ids || []), ...(meta?.hidden_event_ids || [])]) {
          expandedLoopMembers.add(String(id));
        }
      }
    }
    for (const lane of model.lanes) {
      const q1 = mode === "debug" && lane.kind === "q1-issue";
      const waits = mode === "normal" && !instruction && ["runtime", "branch-runtime"].includes(lane.kind);
      if (!q1 && !waits) continue;
      const replacements = new Map();
      let run = [];
      let runScope;
      let previousEnd;
      function finishRun() {
        if (!run.length) return;
        if (q1 || run.length > 1) {
          const first = run[0], last = run.at(-1);
          const id = `command-group:${first.id}`;
          const expanded = expandedGroups.has(id);
          const duration = last.start + last.duration - first.start;
          const label = q1 ? `Q1 × ${run.length}` : `wait × ${run.length}`;
          const action = `${expanded ? "Collapse" : "Expand"} ${run.length} ${q1 ? "Q1 instructions" : "wait commands"}`;
          const span = instruction ? `instructions ${first.start + 1}–${first.start + duration}` :
            `${first.start}–${first.start + duration} ns${waits ? ` (${duration} ns)` : ""}`;
          for (const block of run) {
            for (const eventId of block.eventIds) groupByEvent.set(eventId, id);
            if (!expanded) replacements.set(block, []);
          }
          const summary = {
            id, kind: "command_group", label: expanded ? "−" : label,
            start: first.start, duration, title: `${action} · ${span}`, eventIds: [],
            accentColor: waits ? first.accentColor : undefined,
            foldGroup: {expanded, waits, action, count: run.length},
            search: run.map((block) => block.search).join(" "),
            classNames: expanded ? "timeline-command-group timeline-command-group-open" : "timeline-command-group",
          };
          replacements.set(first, expanded ? [first, summary] : [summary]);
        }
        run = [];
      }
      const ordered = [...lane.blocks].sort((left, right) => left.start - right.start ||
        Number(Boolean(right.pointMarker)) - Number(Boolean(left.pointMarker)));
      for (const block of ordered) {
        const event = eventsById.get(block.eventId);
        const meta = event?.meta || {};
        const start = concreteTime(timeBasis === "aligned" ? meta.aligned_t0 : undefined) ??
          concreteTime(event?.t0 ?? event?.t0_ns);
        const end = concreteTime(timeBasis === "aligned" ? meta.aligned_t1 : undefined) ??
          concreteTime(event?.t1 ?? event?.t1_ns);
        const ordinaryWait = block.kind === "wait" && block.commandToken === "wait" && !expandedLoopMembers.has(block.eventId) &&
          !meta.conditional_time_unknown && !(timeBasis === "aligned" && meta.aligned_time_nominal);
        const eligible = (q1 ? block.kind === "q1_issue" && !hasQ1Control?.(event) : ordinaryWait) &&
          !block.diagnosticErrors?.length && block.duration > 0 &&
          (instruction || start === block.start && block.start + block.duration === end);
        const scope = JSON.stringify([meta.loop_preview, meta.loop_iteration_index, meta.diff_status, meta.rt_trigger_epoch]);
        const previous = run.at(-1);
        if (!eligible || previous && (previous.start + previous.duration !== block.start ||
          runScope !== scope || q1 && (run.length === 12 ||
            previousEnd !== undefined && start !== undefined && previousEnd !== start))) finishRun();
        if (eligible) {
          runScope = scope;
          previousEnd = end;
          run.push(block);
        }
      }
      finishRun();
      lane.blocks = lane.blocks.flatMap((block) => replacements.get(block) || [block]);
    }
    return groupByEvent;
  }

  function timeScaleFromGaps(gaps, ranges = gaps) {
    return {
      gaps,
      toDisplay(time) {
        let removed = 0;
        for (const gap of ranges) {
          if (time < gap.start) break;
          if (time <= gap.end) {
            return gap.displayStart + ((time - gap.start) / (gap.end - gap.start)) * (gap.displayEnd - gap.displayStart);
          }
          removed += gap.end - gap.start - (gap.displayEnd - gap.displayStart);
        }
        return time - removed;
      },
      fromDisplay(time) {
        let removed = 0;
        for (const gap of ranges) {
          if (time < gap.displayStart) break;
          if (time <= gap.displayEnd) {
            return gap.start + ((time - gap.displayStart) / (gap.displayEnd - gap.displayStart)) * (gap.end - gap.start);
          }
          removed += gap.end - gap.start - (gap.displayEnd - gap.displayStart);
        }
        return time + removed;
      },
    };
  }

  function exactTime(value) {
    const time = value?.kind === "concrete" ? value.value : value;
    return Number.isFinite(time) ? time : NaN;
  }

  function staticLoopHiddenInterval(event) {
    const meta = event.meta || {};
    if (event.kind !== "loop_block" || event.confidence !== "exact" ||
      !["first_iteration_only", "requested_iterations"].includes(meta.preview_policy)) return undefined;
    const count = exactTime(meta.count);
    const period = exactTime(meta.period);
    const total = meta.total_duration === undefined ? count * period : exactTime(meta.total_duration);
    const visible = Math.max(Number(meta.visible_iteration_count) || 1,
      ...(meta.shown_iterations || [0]).filter((index) => Number.isInteger(index) && index >= 0).map((index) => index + 1));
    const start = exactTime(event.t0) + visible * period;
    const end = exactTime(event.t0) + total;
    return Number.isInteger(count) && Number.isInteger(visible) && count > visible && period >= 0 && total > 0 &&
      Number.isFinite(start) && Number.isFinite(end) && Number.isFinite(exactTime(event.t1)) ? { start, end } : undefined;
  }

  function collapsedLoopInterval(event, expanded, playback) {
    if (event.kind !== "loop_block") return undefined;
    const meta = event.meta || {};
    const key = String(meta.loop_preview_key || meta.loop_id || event.id);
    if (expanded.has(key)) return undefined;
    if (isExecutedLoop(event, playback)) {
      const start = exactTime(meta.first_iteration_end);
      const end = exactTime(event.t1);
      return meta.hidden_event_ids?.length && end > start ? { start, end, key } : undefined;
    }
    const omitted = !playback && staticLoopHiddenInterval(event);
    return omitted ? { ...omitted, key } : undefined;
  }

  function buildTimelineTimeScale(timelineIr, options = {}) {
    const edge = (event, name) => {
      const local = exactTime(event[name]);
      if (options.timeBasis !== "aligned") return local;
      const aligned = event.meta && event.meta[`aligned_${name}`];
      return aligned !== undefined ? exactTime(aligned) : local + exactTime(timelineIr.alignment?.[event.sequencer_id]?.offset ?? 0);
    };
    const intervals = [];
    const expanded = new Set(options.expandedPlaybackLoops || []);
    const events = timelineIr && timelineIr.events || [];
    const playback = Boolean(timelineIr?.playback);
    const sequencers = new Set([...(timelineIr?.sequencers || []).map((sequencer) => String(sequencer.id)), ...events.map((event) => String(event.sequencer_id))]);
    const offsets = [...sequencers].map((id) => timelineIr.alignment && timelineIr.alignment[id]);
    const sharedClock = [...sequencers].every((id) => timelineIr.execution?.[id]?.timing_model === "event_driven");
    const sameOffset = new Set(offsets.map((alignment) => exactTime(alignment?.offset))).size === 1;
    const sharedAlignment = sharedClock && sameOffset && offsets.every((alignment) => alignment?.clock === "shared_simulation");
    const commonClock = sequencers.size <= 1 || [...sequencers].every((id) => events.some((event) => String(event.sequencer_id) === id)) && offsets.every((alignment) => alignment && alignment.confidence === "exact" &&
      !alignment.unaligned && Number.isFinite(exactTime(alignment.offset)) &&
      (sharedAlignment || alignment.mode === "first_wait_sync" || String(alignment.mode).startsWith("manual:") || alignment.mode === "none" && sharedClock)) &&
      (options.timeBasis === "aligned" || sameOffset);
    const hidden = collapsedExecutedLoopIds(events, expanded, playback);
    const activity = events.filter((event) => !hidden.has(String(event.id)) &&
      !["wait", "wait_sync", "wait_trigger", "loop_block", "branch_region", "loop_iteration_preview", "control_flow_loop", "control_flow_branch", "latched_state_pending", "queue_depth", "slack", "analysis_incomplete"].includes(event.kind) &&
      // Analytical Q1 timestamps use an independent clock, not the RT interval being folded.
      (event.kind !== "q1_issue" || timelineIr.execution?.[event.sequencer_id]?.timing_model !== "analytical" &&
        (event.meta?.playback_feedback_id || event.meta?.playback_branch_id)));
    for (const event of events) {
      const omitted = commonClock && collapsedLoopInterval(event, expanded, playback);
      if (!omitted) continue;
      const meta = event.meta || {};
      if (options.timeBasis === "aligned" && meta.aligned_t1 !== undefined && !Number.isFinite(exactTime(meta.aligned_t1))) continue;
      const offset = edge(event, "t0") - exactTime(event.t0);
      const start = omitted.start + offset;
      const end = omitted.end + offset;
      if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
        let parts = [{ start, end, loopKeys: [omitted.key] }];
        for (const item of activity) {
          const from = edge(item, "t0");
          const to = edge(item, "t1");
          if (!Number.isFinite(from)) { parts = []; break; }
          // Activity outside the omitted interval must not leave an uncollapsed tail.
          if (from >= end || Number.isFinite(to) && to <= start) continue;
          const left = from - 16;
          const right = Number.isFinite(to) ? to + 16 : Infinity;
          parts = parts.flatMap((part) => right <= part.start || left >= part.end ? [part] : [
            ...(left > part.start ? [{ ...part, end: left }] : []),
            ...(right < part.end ? [{ ...part, start: right }] : []),
          ]);
        }
        intervals.push(...parts);
      }
    }
    intervals.push(...(options.waitGaps || []).map(({ start, end }) => ({ start, end, loopKeys: [] })));
    // A single transform keeps simultaneous events on every sequencer aligned.
    // Overlapping/nested omissions must not subtract the same time twice.
    const merged = [];
    for (const interval of intervals.sort((a, b) => a.start - b.start || a.end - b.end)) {
      const last = merged[merged.length - 1];
      if (last && interval.start < last.end) {
        last.end = Math.max(last.end, interval.end);
        last.loopKeys = [...new Set([...last.loopKeys, ...interval.loopKeys])];
      } else merged.push({ ...interval });
    }
    let pixelsPerNs;
    let fittedWidth;
    let foldShrink = 1;
    let foldWidths;
    const viewport = options.viewport;
    if (options.plotWidth > 0 && viewport && viewport.end > viewport.start) {
      foldWidths = merged.map((interval) => options.foldWidth ? options.foldWidth(interval) : 64);
      let foldedTime = 0;
      let requestedWidth = 0;
      for (const [index, interval] of merged.entries()) {
        const visible = Math.max(0, Math.min(viewport.end, interval.end) - Math.max(viewport.start, interval.start));
        foldedTime += visible;
        requestedWidth += foldWidths[index] * visible / (interval.end - interval.start);
      }
      const unfoldedTime = viewport.end - viewport.start - foldedTime;
      // Keep folds at label width, with space for visible commands on dense traces.
      const foldedWidth = Math.min(requestedWidth, options.plotWidth * 0.75);
      foldShrink = requestedWidth ? foldedWidth / requestedWidth : 1;
      pixelsPerNs = Math.min(options.plotWidth / (viewport.end - viewport.start),
        unfoldedTime > 0 ? (options.plotWidth - foldedWidth) / unfoldedTime : Infinity);
      fittedWidth = unfoldedTime * pixelsPerNs + foldedWidth;
    }
    let removed = 0;
    const gaps = merged.map((interval, index) => {
      const duration = interval.end - interval.start;
      // Keep a nonzero break so pointer positions still map to real times.
      const width = pixelsPerNs ? foldWidths[index] * foldShrink / pixelsPerNs
        : interval.loopKeys.length ? Math.min(64, duration / 10) : 64;
      const displayStart = interval.start - removed;
      removed += duration - width;
      return { ...interval, displayStart, displayEnd: displayStart + width };
    });
    let ranges = gaps;
    if (pixelsPerNs && options.commandWidths?.length) {
      const commands = [];
      for (const command of [...options.commandWidths].sort((a, b) => a.start - b.start)) {
        if (command.end <= command.start || command.width <= (command.end - command.start) * pixelsPerNs ||
          gaps.some((gap) => gap.start < command.end && gap.end > command.start)) continue;
        const rate = command.width / (command.end - command.start);
        const last = commands[commands.length - 1];
        if (last && command.start < last.end) {
          last.end = Math.max(last.end, command.end);
          last.rate = Math.max(last.rate, rate);
        } else commands.push({ start: command.start, end: command.end, rate });
      }
      ranges = [...gaps, ...commands.map((command) => ({ ...command,
        displayStart: 0, displayEnd: (command.end - command.start) * command.rate / pixelsPerNs }))]
        .sort((a, b) => a.start - b.start);
      let flexibleTime = viewport.end - viewport.start;
      let fixedWidth = 0;
      for (const interval of ranges) {
        const visible = Math.max(0, Math.min(viewport.end, interval.end) - Math.max(viewport.start, interval.start));
        flexibleTime -= visible;
        fixedWidth += (interval.displayEnd - interval.displayStart) * pixelsPerNs * visible / (interval.end - interval.start);
      }
      // Fit flexible time around readable commands and fixed-width fold tabs.
      const fittedRate = flexibleTime > 0 && fixedWidth < options.plotWidth
        ? Math.min(pixelsPerNs, (options.plotWidth - fixedWidth) / flexibleTime) : pixelsPerNs;
      const widthRatio = pixelsPerNs / fittedRate;
      pixelsPerNs = fittedRate;
      let removed = 0;
      for (const interval of ranges) {
        const width = (interval.displayEnd - interval.displayStart) * widthRatio;
        interval.displayStart = interval.start - removed;
        interval.displayEnd = interval.displayStart + width;
        removed += interval.end - interval.start - width;
      }
      const scale = timeScaleFromGaps(gaps, ranges);
      fittedWidth = Math.min(options.plotWidth, (scale.toDisplay(viewport.end) - scale.toDisplay(viewport.start)) * pixelsPerNs);
    }
    return { ...timeScaleFromGaps(gaps, ranges), ...(fittedWidth === undefined ? {} : { plotWidth: Math.max(1, fittedWidth) }) };
  }

  function buildWaitTimeScale(timelineIr, options = {}) {
    const gaps = [];
    const acquisitionWindows = [];
    const scale = timeScaleFromGaps(gaps);
    function merge(intervals) {
      const merged = [];
      for (const interval of intervals.sort((a, b) => a.start - b.start || a.end - b.end)) {
        const last = merged[merged.length - 1];
        if (last && interval.start <= last.end) last.end = Math.max(last.end, interval.end);
        else merged.push({ ...interval });
      }
      return merged;
    }
    function intersect(left, right) {
      const result = [];
      let a = 0;
      let b = 0;
      while (a < left.length && b < right.length) {
        const start = Math.max(left[a].start, right[b].start);
        const end = Math.min(left[a].end, right[b].end);
        if (end > start) result.push({ start, end });
        if (left[a].end < right[b].end) a += 1;
        else b += 1;
      }
      return result;
    }
    function subtract(intervals, exclusions) {
      const result = [];
      const blocked = merge(exclusions);
      let index = 0;
      for (const interval of intervals) {
        let start = interval.start;
        while (index < blocked.length && blocked[index].end < start) index += 1;
        let current = index;
        while (current < blocked.length && blocked[current].start < interval.end) {
          const block = blocked[current];
          if (block.start > start) result.push({ start, end: block.start });
          start = Math.max(start, block.end);
          if (start >= interval.end) break;
          current += 1;
        }
        if (start < interval.end) result.push({ start, end: interval.end });
      }
      return result;
    }
    const events = Array.isArray(timelineIr && timelineIr.events) ? timelineIr.events : [];
    const sequencers = new Set((timelineIr && timelineIr.sequencers || []).map((seq) => String(seq.id)));
    for (const event of events) if (event.sequencer_id !== undefined) sequencers.add(String(event.sequencer_id));
    if (!sequencers.size) return scale;
    const offsets = new Map();
    const sharedClock = [...sequencers].every((id) => timelineIr.execution && timelineIr.execution[id] && timelineIr.execution[id].timing_model === "event_driven");
    const sharedAlignment = sharedClock && [...sequencers].every((id) => timelineIr.alignment?.[id]?.clock === "shared_simulation");
    for (const id of sequencers) {
      const alignment = timelineIr.alignment && timelineIr.alignment[id];
      const offset = alignment ? concreteTime(alignment.offset) : 0;
      if ((sequencers.size > 1 && !alignment) || (alignment && (alignment.confidence !== "exact" || alignment.unaligned || offset === undefined))) return scale;
      if (sequencers.size > 1 && !sharedAlignment && alignment.mode !== "first_wait_sync" && !String(alignment.mode).startsWith("manual:") && !(alignment.mode === "none" && sharedClock)) return scale;
      offsets.set(id, offset);
    }
    // Local clocks with different origins cannot establish simultaneous unchanged time.
    if ((sharedAlignment || options.timeBasis !== "aligned") && new Set(offsets.values()).size > 1) return scale;
    const playback = Boolean(timelineIr.playback);
    const expandedLoops = new Set(options.expandedPlaybackLoops || []);
    const hidden = collapsedExecutedLoopIds(events, expandedLoops, playback);
    const ignoredKinds = new Set(["queue_depth", "slack", "analysis_incomplete", "latched_state_pending", "rt_queue_enqueue", "rt_queue_dequeue", "q1_stall", "loop_iteration_preview", "control_flow_loop", "control_flow_branch"]);
    if (!playback) ignoredKinds.add("q1_issue");
    else {
      ignoredKinds.add("loop_block");
      ignoredKinds.add("branch_region");
    }
    const waitKinds = new Set(["wait", "wait_sync", "wait_trigger"]);
    const knownKinds = new Set(["upd_param", "latched_state_applied", "marker_state", "branch_region", "sync_wait", "feedback_com", "fb_com_data", "feedback_pop", "feedback_receive", "feedback_send", "acquisition_result", "fb_acq_tb_id", "fb_acq_iq_id", "fb_acq_tb_valid", "fb_acq_tb_cfg", "fb_acq_iq_shift"]);
    if (events.some((event) => event.sequencer_id === undefined && !ignoredKinds.has(event.kind))) return scale;
    let common;
    for (const id of sequencers) {
      const simulation = timelineIr.simulation && timelineIr.simulation[id];
      const parameters = simulation && simulation.signals && simulation.signals.parameters;
      // Filter settling can keep the physical output active after offset becomes zero.
      const identityFilter = (values) => Array.isArray(values) && values.length === 1 && values[0] === 1;
      if (simulation && simulation.physical_enabled && (!parameters ||
        !identityFilter(parameters.fir) || !parameters.iir || !identityFilter(parameters.iir.a) || !identityFilter(parameters.iir.b))) return scale;
      const offset = options.timeBasis === "aligned" ? offsets.get(id) : 0;
      const records = [];
      const omitted = [];
      for (const event of events) {
        if (String(event.sequencer_id) !== id) continue;
        // Repeated iterations have their own fold, even when state is unchanged.
        const interval = collapsedLoopInterval(event, expandedLoops, playback);
        if (interval) omitted.push({ start: interval.start + offset, end: interval.end + offset });
        // An enabled or unresolved static condition can change subsequent RT timing.
        const unresolvedCondition = event.kind === "latched_state_pending" && event.meta &&
          event.meta.field === "set_cond" && event.meta.conditional_enabled !== false;
        if (ignoredKinds.has(event.kind) && !unresolvedCondition) continue;
        // Hidden repetitions have their own loop fold. Including their waits here
        // would merge across the visible first wait's end and erase its end cap.
        if (hidden.has(String(event.id)) || isExecutedLoop(event, playback) || playback && event.kind === "q1_issue" &&
          !event.meta?.playback_feedback_id && !event.meta?.playback_branch_id) continue;
        const localStart = concreteTime(event.t0 ?? event.t0_ns);
        const localEnd = concreteTime(event.t1 ?? event.t1_ns);
        if (localStart === undefined || localEnd === undefined || localEnd < localStart) return scale;
        if (options.timeBasis === "aligned" && ["t0", "t1"].some((edge, index) => {
          const value = event.meta && event.meta[`aligned_${edge}`];
          return value !== undefined && concreteTime(value) !== [localStart, localEnd][index] + offset;
        })) return scale;
        records.push({ event, start: localStart + offset, end: localEnd + offset });
      }
      records.sort((a, b) => a.start - b.start);
      if (!records.length) return scale;
      const windows = records.flatMap(({ event, start }) => event.kind === "acquire" && event.confidence === "exact"
        ? [event.meta?.acquisition_window, event.meta?.scope_acquisition_window]
          .filter((window) => window && Number.isFinite(window.end_ns) && window.status !== "unknown")
          .map((window) => ({ start, end: window.end_ns + offset })) : []);
      acquisitionWindows.push(...windows);
      if (playback) {
        // Compress explicit waiting in this assumed path, not presumed physical idle.
        // Visible Q1 controls, RT commands and feedback boundaries stay outside
        // the omission; ordinary Q1 blocks use the same reversible time scale.
        const waits = merge(records.filter(({ event }) => waitKinds.has(event.kind)).map(({ start, end }) => ({ start, end })));
        const activity = records.filter(({ event }) => !waitKinds.has(event.kind)).map(({ start, end }) => ({ start, end }));
        activity.push(...windows.map(({ end }) => ({ start: end, end })));
        const unchanged = subtract(subtract(waits, activity), omitted);
        common = common === undefined ? unchanged : intersect(common, unchanged);
        if (!common.length) return scale;
        continue;
      }
      const waitLoops = records.flatMap(({ event, start }) => {
        const meta = event.meta || {};
        const count = concreteTime(meta.count);
        const period = concreteTime(meta.period);
        const total = meta.total_duration === undefined ? count * period : concreteTime(meta.total_duration);
        const end = start + total;
        return event.kind === "loop_block" && event.confidence === "exact" && meta.wait_only === true &&
          Number.isInteger(count) && count > 0 && period >= 0 && total > 0 && Number.isFinite(end)
          ? [{ start, end }] : [];
      });
      const observed = [{ start: records[0].start,
        end: [...records, ...waitLoops, ...windows].reduce((last, record) => Math.max(last, record.end), records[0].start) }];
      const blocked = [];
      const acquisitionEnds = new Map();
      for (const { event, start } of records) {
        const meta = event.meta || {};
        if (event.kind !== "acquisition_result" || event.confidence !== "exact" || !meta.rt_packet_id || !["configured", "hardware_parameters", "weight_memory"].includes(meta.integration_length_source)) continue;
        const acquiredAt = concreteTime(meta.start_ns);
        const duration = concreteTime(meta.duration_ns);
        if (acquiredAt !== undefined && duration > 0 && Math.abs(acquiredAt + offset + duration - start) < 1e-6) {
          acquisitionEnds.set(meta.rt_packet_id, { start: acquiredAt + offset, end: start });
        }
      }
      // Fold unchanged modeled state, including held nonzero or unknown levels.
      // Every applied-state boundary remains visible; this does not claim hardware idle.
      for (const { event, start, end } of records) {
        const meta = event.meta || {};
        const provenWaitCopy = waitKinds.has(event.kind) && waitLoops.some((loop) => start >= loop.start && end <= loop.end);
        if (event.confidence !== "exact" && !meta.trigger_arrival_unknown ||
            (meta.preview_source_event_id && !provenWaitCopy) || meta.assumed_branch_path || meta.comparison_paths) {
          blocked.push({ start, end: Infinity });
          continue;
        }
        if (waitKinds.has(event.kind)) continue;
        if (event.kind === "loop_block") {
          const count = concreteTime(meta.count);
          const period = concreteTime(meta.period);
          const total = meta.total_duration === undefined ? count * period : concreteTime(meta.total_duration);
          if (meta.wait_only === true && Number.isInteger(count) && count > 0 && period >= 0 && total > 0 && Number.isFinite(start + total)) continue;
          const shown = new Set(meta.shown_iterations || [0]);
          let visible = 0;
          while (shown.has(visible)) visible += 1;
          if (count === undefined || count > visible) blocked.push({ start: period > 0 ? start + period * visible : start, end: Infinity });
        } else if (event.kind === "play") {
          // The IR has no waveform end; t1 is only the RT command's blocking end.
          blocked.push({ start, end: Infinity });
        } else if (event.kind === "acquire") {
          const acquisition = acquisitionEnds.get(meta.rt_packet_id);
          const configuredWindows = [meta.acquisition_window, meta.scope_acquisition_window].filter(Boolean);
          if (configuredWindows.length && configuredWindows.every((window) => Number.isFinite(window.end_ns) &&
              window.status !== "unknown" && window.source !== "instruction_duration_fallback")) {
            // Acquiring is a constant mode between its boundaries. Keep the RT
            // command and every integration/scope end visible, but fold its interior.
            blocked.push({ start, end });
            for (const window of configuredWindows) {
              blocked.push({ start: window.end_ns + offset, end: window.end_ns + offset });
            }
          } else blocked.push({ start, end: acquisition && acquisition.start === start ? Math.max(end, acquisition.end) : Infinity });
        } else {
          const annotation = event.kind === "underflow_warning" && meta.status === "possible_underflow";
          blocked.push({ start, end: knownKinds.has(event.kind) || annotation ? end : Infinity });
        }
      }
      const unchanged = subtract(subtract(observed, blocked), omitted);
      common = common === undefined ? unchanged : intersect(common, unchanged);
      if (!common.length) return scale;
    }
    const expandedGaps = Array.isArray(options.expandedGaps) ? options.expandedGaps : [];
    let removed = 0;
    for (const interval of common || []) {
      if (interval.end - interval.start > 128) {
        // Keep 16 ns beside every change, including changes on another sequencer.
        const start = interval.start + 16;
        const end = interval.end - 16;
        if (expandedGaps.some((gap) => gap.start === start && gap.end === end)) continue;
        const displayStart = start - removed;
        const displayEnd = displayStart + 64;
        // A whole acquisition's unchanged interior carries its full duration on
        // the tab. Partial spans between other commands retain their actual length.
        const matching = acquisitionWindows.filter((window) => window.end === interval.end &&
          window.start <= interval.start && interval.start <= window.start + 16);
        const acquisition = matching.length && matching.every((window) =>
          window.start === matching[0].start && window.end === matching[0].end) ? matching[0] : undefined;
        gaps.push({ start, end, displayStart, displayEnd, ...(acquisition ? { acquisition } : {}) });
        removed += end - start - 64;
      }
    }
    return scale;
  }

  const api = {
    acquisitionWindow,
    acquisitionMode,
    branchComparisonLabel,
    buildQ1TimelineSharedModel,
    buildQ1TimelineSelectionIndex,
    buildTimelineTimeScale,
    buildWaitTimeScale,
    eventLabelToken,
    eventTimeValue,
    foldTimelineCommands,
    isExecutedLoop,
    networkConflictDisplayMessage,
    netzeroResultTimingText,
    q1IssueTimingText,
    q1OnlyLoopIds,
    rtTimingText,
    underflowLabel,
    visibleRtExtent,
  };

  root.q1timelineTimelineAdapter = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
