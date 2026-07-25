(function q1timelineAdapterFactory(root) {
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

  function eventLabelToken(event, options = {}) {
    if (!event) {
      return "";
    }
    if (event.kind === "wait" && options.mode !== "debug") {
      return "wait";
    }
    if (event.kind === "q1_issue") {
      const raw = String((event.source && event.source.raw) || "").trim();
      if (raw) {
        return raw.split(/\s+/, 1)[0];
      }
      const op = String((event.meta && event.meta.op) || "").trim();
      if (op) {
        return op;
      }
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

  function q1CommandToken(event, options = {}) {
    const raw = String((event && event.source && event.source.raw) || "").split("#")[0].trim();
    if (raw) {
      return raw.split(/\s+/, 1)[0];
    }
    const op = String((event && event.meta && event.meta.op) || "").trim();
    if (op) {
      return op;
    }
    return eventLabelToken(event, options);
  }

  function q1CommandAccentColor(event, options = {}) {
    const op = q1CommandToken(event, options).toLowerCase();
    if (op === "play" || op === "play_pulse") {
      return "#8bcf9a";
    }
    if (op === "wait" || op === "wait_sync" || op === "wait_trigger") {
      return "#b9a7dc";
    }
    if (op === "acquire" || op.startsWith("acquire_")) {
      return "#e2b36f";
    }
    if (op === "upd_param" || op === "upd_thres") {
      return "#bfd87a";
    }
    if (op === "feedback_pop" || op === "feedback_com" || op === "fb" || op.startsWith("fb_")) {
      return "#ff6fb3";
    }
    if (op === "set_mrk" || op === "set_marker" || op === "mark") {
      return "#f0c36a";
    }
    if (op.startsWith("set_") || op === "reset_ph") {
      return "#6ec6d9";
    }
    if (op === "loop" || op === "jmp" || /^j[a-z]+$/.test(op)) {
      return "#f4a261";
    }
    if (["move", "add", "sub", "and", "or", "xor", "asl", "asr"].includes(op)) {
      return "#80b7ff";
    }
    return "#8bcf9a";
  }

  function laneBase(sequencer) {
    return `sequencer:${sequencer}`;
  }

  function eventLane(event, options) {
    const sequencer = String(event.sequencer_id || "sequencer");
    const base = laneBase(sequencer);
    const lane = String(event.lane || "");
    if (event.kind === "q1_issue") {
      return {
        id: `${base}:q1_issue`,
        label: "Q1 issue",
        kind: "q1-issue",
        role: "q1-issue",
        parentGroupId: base,
        sequencer,
        hidden: !(options && options.expandedQ1IssueSequencers && options.expandedQ1IssueSequencers.has(sequencer)),
      };
    }
    if (isPlayPathEvent(event) && !(options && options.mergedPlayEventIds && options.mergedPlayEventIds.has(String(event.id)))) {
      const path = playPathIndex(event);
      return {
        id: `${base}:${lane}`,
        label: path === undefined ? lane.replace(/^rt\./, "") : `path${path}`,
        kind: "runtime-path",
        parentGroupId: base,
        sequencer,
      };
    }
    if (options.mode === "debug" && lane.startsWith("debug.")) {
      return {
        id: `${base}:${lane}`,
        label: lane.replace(/^debug\./, "debug / "),
        kind: "debug",
        parentGroupId: base,
        sequencer,
      };
    }
    return {
      id: base,
      label: sequencer,
      kind: "runtime",
      sequencer,
    };
  }

  function shouldIncludeEvent(event, options) {
    const lane = String(event.lane || "");
    if (event.kind === "q1_issue") {
      return true;
    }
    if (options.mode !== "debug" && lane.startsWith("debug.")) {
      return false;
    }
    return true;
  }

  function isPlayPathEvent(event) {
    return Boolean(event && event.kind === "play" && /^rt\.path\d+$/.test(String(event.lane || "")));
  }

  function playPathIndex(event) {
    const laneMatch = String(event && event.lane || "").match(/^rt\.path(\d+)$/);
    if (laneMatch) {
      return Number(laneMatch[1]);
    }
    const meta = event && event.meta && typeof event.meta === "object" ? event.meta : {};
    const path = Number(meta.path);
    return Number.isInteger(path) ? path : undefined;
  }

  function eventClassNames(event) {
    return [
      `kind-${event.kind || "event"}`,
      `confidence-${event.confidence || "unknown"}`,
      event.meta && event.meta.diff_status ? `diff-${event.meta.diff_status}` : "",
      event.kind === "feedback_pop" || event.kind === "feedback_com" ? "normal-feedback-collapsed" : "",
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

  function eventTitle(event, start, end) {
    if (event && event.kind === "branch_region") {
      return `Branch effect (${branchPathLabel(event)}) | ${displayValue(event.t0, start)} -> ${displayValue(event.t1, end)}`;
    }
    return event.title || `${event.label || event.kind || event.id} | ${displayValue(event.t0, start)} -> ${displayValue(event.t1, end)}`;
  }

  function eventBlock(event, options = {}) {
    const start = eventEdgeTime(event, "t0", options);
    const end = eventEdgeTime(event, "t1", options, start);
    const duration = Math.max(eventTimeValue(event.duration, end - start), end - start, 0);
    const source = event.source || {};
    const meta = event.meta || {};
    const eventIds = Array.isArray(meta.path_event_ids) ? meta.path_event_ids.map((eventId) => String(eventId)) : [String(event.id)];
    const detail = event.detail !== undefined ? String(event.detail) : displayValue(event.duration, duration ? `${duration} ns` : "");
    return {
      id: String(event.id),
      eventId: String(eventIds[0] || event.id),
      eventIds,
      label: eventLabelToken(event, options),
      detail,
      kind: String(event.kind || "event"),
      visualKind: eventVisualKind(event),
      accentColor: q1CommandAccentColor(event, options),
      start,
      duration,
      title: eventTitle(event, start, end),
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
      classNames: eventClassNames(event),
      domain: {
        eventId: eventIds[0] || event.id,
        eventIds,
        kind: event.kind,
        source,
        meta,
      },
    };
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
      source.file || "",
      source.line || "",
      playMergeTimeKey(event, options),
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

  function feedbackVisibleLabel(flow) {
    const channel = String(flow && flow.channel !== undefined ? flow.channel : "").trim();
    return channel ? `fb ch ${channel}` : "fb";
  }

  function eventSequencer(event) {
    return String(event && event.sequencer_id ? event.sequencer_id : "sequencer");
  }

  function feedbackAnnotation(flow, eventById, options) {
    const fromEvent = eventById.get(String(flow && flow.from_event_id));
    const toEvent = eventById.get(String(flow && flow.to_event_id));
    if (!fromEvent || !toEvent) {
      return undefined;
    }
    const fromTime = eventEdgeTime(
      fromEvent,
      "t1",
      options,
      eventEdgeTime(fromEvent, "t0", options),
    );
    const toTime = eventEdgeTime(toEvent, "t0", options, fromTime);
    const fromLane = eventLane(fromEvent, options);
    const toLane = eventLane(toEvent, options);
    const id = String(flow.id || `${flow.from_event_id}->${flow.to_event_id}`);
    const base = {
      id,
      flowId: id,
      label: String(flow.label || "feedback flow"),
      labelShort: feedbackVisibleLabel(flow),
      channel: String(flow.channel || ""),
      eventIds: [String(flow.to_event_id), String(flow.from_event_id)],
      classNames: "feedback-flow-group",
      domain: {
        kind: "feedback",
        flow,
      },
    };
    if (eventSequencer(fromEvent) === eventSequencer(toEvent)) {
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

  function loopAnnotation(event, options) {
    const start = eventEdgeTime(event, "t0", options);
    const end = eventEdgeTime(event, "t1", options, start);
    const meta = event && event.meta && typeof event.meta === "object" ? event.meta : {};
    const loopId = String(meta.loop_id || event.id || "loop");
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
      end,
      label: [loopId, count].filter(Boolean).join(" "),
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

  function buildQ1TimelineSharedModel(timelineIr, options = {}) {
    const mode = options.mode === "debug" ? "debug" : "normal";
    const events = Array.isArray(timelineIr && timelineIr.events) ? timelineIr.events : [];
    const eventById = new Map(events.map((event) => [String(event.id), event]));
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
    const adapterOptions = { mode, timeBasis: options.timeBasis, mergedPlayEventIds, expandedQ1IssueSequencers };
    let maxTime = 1;

    function ensureLane(lane) {
      if (laneById.has(lane.id)) {
        return laneById.get(lane.id);
      }
      const created = { ...lane, blocks: [] };
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
      if (!shouldIncludeEvent(event, { mode })) {
        continue;
      }
      if (event.kind === "loop_block") {
        const annotation = loopAnnotation(event, adapterOptions);
        annotations.push(annotation);
        maxTime = Math.max(maxTime, annotation.end);
        continue;
      }
      if (mode !== "debug" && event.kind === "loop_iteration_preview") {
        continue;
      }
      if (mergedPlayEventIds.has(String(event.id))) {
        continue;
      }
      const block = eventBlock(event, adapterOptions);
      maxTime = Math.max(maxTime, block.start + Math.max(block.duration, 1));
      ensureLane(eventLane(event, adapterOptions)).blocks.push(block);
    }

    for (const group of playGroups) {
      const event = mergedPlayEvent(group, adapterOptions);
      const block = eventBlock(event, adapterOptions);
      maxTime = Math.max(maxTime, block.start + Math.max(block.duration, 1));
      ensureLane(eventLane(event, adapterOptions)).blocks.push(block);
    }

    for (const flow of Array.isArray(timelineIr && timelineIr.feedback_flows) ? timelineIr.feedback_flows : []) {
      const annotation = feedbackAnnotation(flow, eventById, adapterOptions);
      if (!annotation) {
        continue;
      }
      annotations.push(annotation);
      maxTime = Math.max(
        maxTime,
        annotation.end || annotation.toTime || annotation.fromTime || 1,
      );
    }

    const viewport = {
      start: 0,
      end: maxTime,
    };
    return {
      title: "Q1ASM Timeline",
      subtitle: `${lanes.length} lanes / ${events.length} events`,
      totalTime: maxTime,
      viewport,
      ticks: ticksForViewport(viewport),
      lanes,
      annotations,
      inspector: {
        title: "Selected event",
        rows: [],
      },
    };
  }

  const api = {
    buildQ1TimelineSharedModel,
    eventLabelToken,
    eventTimeValue,
  };

  root.q1timelineTimelineAdapter = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
