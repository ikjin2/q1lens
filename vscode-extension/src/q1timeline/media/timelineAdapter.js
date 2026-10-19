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

  function eventLaneName(event) {
    return String(event.lane || "").replace(/^branch_compare\.(taken|fallthrough)\./, "");
  }

  function eventLane(event, options) {
    const sequencer = String(event.sequencer_id || "sequencer");
    const parent = laneBase(sequencer);
    const meta = event.meta || {};
    const path = meta.branch_comparison_path;
    const comparison = path === "taken" || path === "fallthrough";
    const branch = comparison ? { branchComparisonPath: path, branchComparisonId: String(meta.branch_comparison_branch_id || "") } : {};
    const base = comparison ? `${parent}:branch:${branch.branchComparisonId}:${path}` : parent;
    const prefix = path === "taken" ? "Jump" : "Continue";
    const lane = eventLaneName(event);
    if (event.kind === "q1_issue") {
      return {
        id: `${base}:q1_issue`,
        label: comparison ? `${prefix} · Q1` : "Q1 core",
        kind: "q1-issue",
        role: "q1-issue",
        parentGroupId: parent,
        sequencer,
        ...branch,
        hidden: !(options && (options.layout === "instruction" || options.expandedQ1IssueSequencers && options.expandedQ1IssueSequencers.has(sequencer))),
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
    if (options.mode === "debug" && lane.startsWith("debug.")) {
      return {
        id: `${base}:${lane}`,
        label: `${comparison ? `${prefix} · ` : ""}${lane.replace(/^debug\./, "debug / ")}`,
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

  function shouldIncludeEvent(event, options) {
    const lane = eventLaneName(event);
    if (["latched_state_pending", "control_flow_loop", "control_flow_branch"].includes(event.kind)) {
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
    const endFallback = event.meta && event.meta.conditional_time_unknown ? "unknown" : end;
    return event.title || `${event.label || event.kind || event.id} | ${displayValue(event.t0, start)} -> ${displayValue(event.t1, endFallback)}`;
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
      "feedback_receive", "feedback_send", "trigger_receive", "acquisition_result",
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
        if (!event || nonCommandKinds.has(event.kind) || !eventLaneName(event).startsWith("rt.")) continue;
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

  function loopAnnotation(event, options) {
    const start = eventEdgeTime(event, "t0", options);
    const end = eventEdgeTime(event, "t1", options, start);
    const meta = event && event.meta && typeof event.meta === "object" ? event.meta : {};
    const loopId = meta.playback ? "loop" : String(meta.loop_id || event.id || "loop");
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
      label: meta.playback ? count.replace(/^x/, "×") : [loopId, count].filter(Boolean).join(" "),
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
    const positions = new Map();
    const startsByLane = new Map();
    const offsetsByLane = new Map();
    const blockOrder = (block) => Math.min(...block.eventIds.map((id) => eventOrder.get(id) ?? events.length));
    let maxCount = 1;
    for (const lane of lanes) {
      if (lane.kind === "runtime") lane.label = `${lane.sequencer || lane.label} · RT`;
      const conditionalTimeUnknown = lane.blocks.some((block) => block.domain.meta.conditional_time_unknown);
      lane.blocks.sort((left, right) => (conditionalTimeUnknown ? 0 : left.start - right.start) || blockOrder(left) - blockOrder(right));
      startsByLane.set(lane.id, lane.blocks.map((block) => block.start));
      maxCount = Math.max(maxCount, lane.blocks.length);
      lane.blocks.forEach((block, index) => {
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
      for (const block of lane.blocks) {
        block.start += offset;
        for (const id of block.eventIds) {
          const position = positions.get(id);
          position.start += offset;
          position.end += offset;
        }
      }
      maxCount = Math.max(maxCount, offset + lane.blocks.length);
    }
    function insertionPosition(laneId, time) {
      const starts = startsByLane.get(laneId) || [];
      const index = starts.findIndex((start) => start >= time);
      return (offsetsByLane.get(laneId) || 0) + (index < 0 ? starts.length : index);
    }
    for (const annotation of annotations) {
      if (annotation.type === "loop-range") {
        const members = annotation.eventIds.map((id) => positions.get(id)).filter(Boolean);
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
        annotation.fromTime = from ? from.end : insertionPosition(annotation.fromLaneId || annotation.laneId, annotation.fromTime);
        annotation.toTime = to ? to.start : insertionPosition(annotation.toLaneId || annotation.laneId, annotation.toTime);
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
    const index = new Map(events.map((event) => [String(event.id), { relatedEventIds: new Set(), appliedEventIds: new Set() }]));
    const packetKey = (event) => JSON.stringify([event.sequencer_id, (event.meta || {}).rt_packet_id]);
    const sameSequencer = (left, right) => left && right && left.sequencer_id === right.sequencer_id;
    const issuesByPacket = new Map();
    for (const event of events) {
      if (event.kind === "q1_issue" && (event.meta || {}).rt_packet_id !== undefined && !(event.meta || {}).preview_source_event_id) {
        issuesByPacket.set(packetKey(event), event);
      }
    }
    const nonCommandKinds = new Set([
      "marker_state", "latched_state_applied", "rt_queue_enqueue", "rt_queue_dequeue", "q1_stall",
      "sync_wait", "trigger_wait", "feedback_receive", "feedback_send", "trigger_receive", "acquisition_result",
    ]);
    const commands = events.filter((event) => {
      if (!/^(?:branch_compare\.(?:taken|fallthrough)\.)*rt\./.test(String(event.lane || "")) || nonCommandKinds.has(event.kind)) return false;
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
    for (const event of commands) {
      const meta = event.meta || {};
      const issue = meta.q1_issue_event_id !== undefined
        ? eventsById.get(String(meta.q1_issue_event_id)) : issuesByPacket.get(packetKey(event));
      if (issue && issue.kind === "q1_issue" && sameSequencer(issue, event)) {
        link(String(issue.id), String(event.id));
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
      if (event.kind === "conditional_skip" || meta.conditional_skipped || ["wait", "wait_sync", "wait_trigger"].includes(event.kind)) continue;
      for (const [field, setterId] of Object.entries(meta.applied_setter_event_ids || {})) {
        if (!Object.prototype.hasOwnProperty.call(meta.applied_state || {}, field)) continue;
        const setter = eventsById.get(String(setterId));
        const setterMeta = setter && setter.meta || {};
        const issue = eventsById.get(String(setterMeta.q1_issue_event_id));
        if (!setter || setter.kind !== "latched_state_pending" || setterMeta.field !== field ||
          !issue || issue.kind !== "q1_issue" || !sameSequencer(setter, event) || !sameSequencer(issue, event)) continue;
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
    return new Map(Array.from(index, ([id, entry]) => [id, {
      relatedEventIds: [...entry.relatedEventIds], appliedEventIds: [...entry.appliedEventIds],
    }]));
  }

  function buildQ1TimelineSharedModel(timelineIr, options = {}) {
    const mode = options.mode === "debug" ? "debug" : "normal";
    const allEvents = Array.isArray(timelineIr && timelineIr.events) ? timelineIr.events : [];
    const expandedPlaybackLoops = new Set(options.expandedPlaybackLoops || []);
    const hiddenPlaybackEventIds = new Set();
    if (timelineIr && timelineIr.playback) {
      for (const event of allEvents) {
        const meta = event.meta || {};
        if (event.kind === "loop_block" && meta.playback && !expandedPlaybackLoops.has(String(meta.loop_preview_key || meta.loop_id || event.id))) {
          for (const id of meta.hidden_event_ids || []) hiddenPlaybackEventIds.add(String(id));
        }
      }
    }
    const events = allEvents.filter((event) => !hiddenPlaybackEventIds.has(String(event.id)) &&
      (options.layout === "instruction" || !event.meta?.conditional_time_unknown ||
        Number.isFinite(eventEdgeTime(event, "t0", options, Number.NaN))));
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
    const instruction = options.layout === "instruction";
    const adapterOptions = { mode, layout: options.layout, timeBasis: options.timeBasis, mergedPlayEventIds, expandedQ1IssueSequencers };
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
        if (instruction || (event.meta || {}).branch_comparison_path || (event.meta || {}).playback) ensureLane(eventLane(event, adapterOptions));
        const annotation = loopAnnotation(event, adapterOptions);
        annotations.push(annotation);
        maxTime = Math.max(maxTime, annotation.end);
        continue;
      }
      if (event.kind === "loop_iteration_preview") {
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

    mergePacketStateBlocks(lanes, eventById, adapterOptions);

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
      const rank = (item) => (item.branchComparisonPath === "taken" ? 0 : 4) +
        ({ "branch-runtime": 0, "q1-issue": 1, "runtime-path": 2, debug: 3 }[item.kind] || 0);
      return group.sort((left, right) => rank(left) - rank(right));
    });
    lanes.splice(0, lanes.length, ...orderedLanes);
    if (instruction) maxTime = applyInstructionLayout(lanes, annotations, events);
    const viewport = {
      start: 0,
      end: maxTime,
    };
    return {
      title: "Q1ASM Timeline",
      subtitle: instruction ? "Independent instruction order per lane; columns do not show simultaneous time" : `${lanes.length} lanes / ${events.length} events`,
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

  function timeScaleFromGaps(gaps) {
    return {
      gaps,
      toDisplay(time) {
        let removed = 0;
        for (const gap of gaps) {
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
        for (const gap of gaps) {
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

  function staticLoopHiddenInterval(event) {
    const meta = event.meta || {};
    if (event.kind !== "loop_block" || event.confidence !== "exact" ||
      !["first_iteration_only", "requested_iterations"].includes(meta.preview_policy)) return undefined;
    const concrete = (value) => typeof value === "number" && Number.isFinite(value) ? value :
      value?.kind === "concrete" && typeof value.value === "number" && Number.isFinite(value.value) ? value.value : NaN;
    const count = concrete(meta.count);
    const period = concrete(meta.period);
    const visible = Math.max(Number(meta.visible_iteration_count) || 1,
      ...(meta.shown_iterations || [0]).filter((index) => Number.isInteger(index) && index >= 0).map((index) => index + 1));
    const start = concrete(event.t0) + visible * period;
    const end = concrete(event.t0) + count * period;
    return Number.isInteger(count) && Number.isInteger(visible) && count > visible && period > 0 &&
      Number.isFinite(start) && Number.isFinite(end) && Number.isFinite(concrete(event.t1)) ? { start, end } : undefined;
  }

  function buildTimelineTimeScale(timelineIr, options = {}) {
    const concrete = (value) => typeof value === "number" && Number.isFinite(value) ? value :
      value?.kind === "concrete" && typeof value.value === "number" && Number.isFinite(value.value) ? value.value : NaN;
    const edge = (event, name) => {
      const local = concrete(event[name]);
      if (options.timeBasis !== "aligned") return local;
      const aligned = event.meta && event.meta[`aligned_${name}`];
      return aligned !== undefined ? concrete(aligned) : local + concrete(timelineIr.alignment?.[event.sequencer_id]?.offset ?? 0);
    };
    const intervals = [];
    const expanded = new Set(options.expandedPlaybackLoops || []);
    const events = timelineIr && timelineIr.events || [];
    const sequencers = new Set([...(timelineIr?.sequencers || []).map((sequencer) => String(sequencer.id)), ...events.map((event) => String(event.sequencer_id))]);
    const offsets = [...sequencers].map((id) => timelineIr.alignment && timelineIr.alignment[id]);
    const sharedClock = [...sequencers].every((id) => timelineIr.execution?.[id]?.timing_model === "event_driven");
    const commonClock = sequencers.size <= 1 || [...sequencers].every((id) => events.some((event) => String(event.sequencer_id) === id)) && offsets.every((alignment) => alignment && alignment.confidence === "exact" &&
      !alignment.unaligned && Number.isFinite(concrete(alignment.offset)) &&
      (alignment.mode === "first_wait_sync" || String(alignment.mode).startsWith("manual:") || alignment.mode === "none" && sharedClock)) &&
      (options.timeBasis === "aligned" || new Set(offsets.map((alignment) => concrete(alignment.offset))).size === 1);
    const hidden = new Set(events.filter((event) => event.kind === "loop_block" && event.meta?.playback &&
      !expanded.has(String(event.meta.loop_preview_key || event.meta.loop_id || event.id))).flatMap((event) => event.meta.hidden_event_ids || []));
    const activity = events.filter((event) => !hidden.has(String(event.id)) &&
      !["wait", "wait_sync", "wait_trigger", "loop_block", "branch_region", "loop_iteration_preview", "control_flow_loop", "control_flow_branch", "latched_state_pending", "queue_depth", "slack", "analysis_incomplete"].includes(event.kind) &&
      (event.kind !== "q1_issue" || event.meta?.playback_feedback_id || event.meta?.playback_branch_id));
    for (const event of events) {
      const meta = event.meta || {};
      const key = String(meta.loop_preview_key || meta.loop_id || event.id);
      if (!commonClock || event.kind !== "loop_block" || expanded.has(key)) continue;
      let firstEnd;
      let localEnd;
      if (timelineIr.playback) {
        if (!meta.playback || !meta.hidden_event_ids?.length) continue;
        firstEnd = concrete(meta.first_iteration_end);
        localEnd = concrete(event.t1);
      } else {
        const omitted = staticLoopHiddenInterval(event);
        if (!omitted) continue;
        firstEnd = omitted.start;
        localEnd = omitted.end;
      }
      const times = [event.t0, event.t1, firstEnd, localEnd];
      if (options.timeBasis === "aligned") times.push(...[meta.aligned_t0, meta.aligned_t1].filter((time) => time !== undefined));
      if (times.some((time) => typeof time !== "number" && (!time || time.kind !== "concrete"))) continue;
      const localStart = eventTimeValue(event.t0, Number.NaN);
      const offset = edge(event, "t0") - localStart;
      const start = firstEnd + offset;
      const end = localEnd + offset;
      if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
        let parts = [{ start, end, loopKeys: [key] }];
        for (const item of activity) {
          const from = edge(item, "t0");
          const to = edge(item, "t1");
          if (!Number.isFinite(from)) { parts = []; break; }
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
    let removed = 0;
    const gaps = merged.map((interval) => {
      const duration = interval.end - interval.start;
      // Keep a nonzero break so pointer positions still map to real times.
      const width = interval.loopKeys.length ? Math.min(64, duration / 10) : 64;
      const displayStart = interval.start - removed;
      removed += duration - width;
      return { ...interval, displayStart, displayEnd: displayStart + width };
    });
    return timeScaleFromGaps(gaps);
  }

  function buildWaitTimeScale(timelineIr, options = {}) {
    const gaps = [];
    const scale = timeScaleFromGaps(gaps);
    function concreteTime(value) {
      if (typeof value === "number" && Number.isFinite(value)) return value;
      if (value && typeof value === "object" && (!value.kind || value.kind === "concrete")) {
        const number = value.value ?? value.value_ns;
        if (typeof number === "number" && Number.isFinite(number)) return number;
      }
      return undefined;
    }
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
    for (const id of sequencers) {
      const alignment = timelineIr.alignment && timelineIr.alignment[id];
      const offset = alignment ? concreteTime(alignment.offset) : 0;
      if ((sequencers.size > 1 && !alignment) || (alignment && (alignment.confidence !== "exact" || alignment.unaligned || offset === undefined))) return scale;
      if (sequencers.size > 1 && alignment.mode !== "first_wait_sync" && !String(alignment.mode).startsWith("manual:") && !(alignment.mode === "none" && sharedClock)) return scale;
      offsets.set(id, offset);
    }
    // Local clocks with different origins cannot establish simultaneous unchanged time.
    if (options.timeBasis !== "aligned" && new Set(offsets.values()).size > 1) return scale;
    const playback = Boolean(timelineIr.playback);
    const expandedLoops = new Set(options.expandedPlaybackLoops || []);
    const hidden = new Set(playback ? events.filter((event) => event.kind === "loop_block" && event.meta?.playback &&
      !expandedLoops.has(String(event.meta.loop_preview_key || event.meta.loop_id || event.id))).flatMap((event) => event.meta.hidden_event_ids || []) : []);
    const ignoredKinds = new Set(["queue_depth", "slack", "analysis_incomplete", "latched_state_pending", "rt_queue_enqueue", "rt_queue_dequeue", "q1_stall", "loop_iteration_preview", "control_flow_loop", "control_flow_branch"]);
    if (!playback) ignoredKinds.add("q1_issue");
    else {
      ignoredKinds.add("loop_block");
      ignoredKinds.add("branch_region");
    }
    const waitKinds = new Set(["wait", "wait_sync", "wait_trigger"]);
    const knownKinds = new Set(["upd_param", "latched_state_applied", "marker_state", "branch_region", "feedback_com", "feedback_pop", "feedback_receive", "feedback_send", "acquisition_result", "fb_acq_tb_id", "fb_acq_iq_id", "fb_acq_tb_valid", "fb_acq_tb_cfg", "fb_acq_iq_shift"]);
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
      for (const event of events) {
        // An enabled or unresolved static condition can change subsequent RT timing.
        const unresolvedCondition = event.kind === "latched_state_pending" && event.meta &&
          event.meta.field === "set_cond" && event.meta.conditional_enabled !== false;
        if (String(event.sequencer_id) !== id || (ignoredKinds.has(event.kind) && !unresolvedCondition)) continue;
        // Hidden repetitions have their own loop fold. Including their waits here
        // would merge across the visible first wait's end and erase its end cap.
        if (playback && (hidden.has(String(event.id)) || event.kind === "q1_issue" &&
          !event.meta?.playback_feedback_id && !event.meta?.playback_branch_id)) continue;
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
      if (playback) {
        // Compress explicit waiting in this assumed path, not presumed physical idle.
        // Visible Q1 controls, RT commands and feedback boundaries stay outside
        // the omission; ordinary Q1 blocks use the same reversible time scale.
        const waits = merge(records.filter(({ event }) => waitKinds.has(event.kind)).map(({ start, end }) => ({ start, end })));
        const activity = records.filter(({ event }) => !waitKinds.has(event.kind)).map(({ start, end }) => ({ start, end }));
        const unchanged = subtract(waits, activity);
        common = common === undefined ? unchanged : intersect(common, unchanged);
        if (!common.length) return scale;
        continue;
      }
      const waitLoops = records.flatMap(({ event, start }) => {
        const meta = event.meta || {};
        const count = concreteTime(meta.count);
        const period = concreteTime(meta.period);
        const end = start + count * period;
        return event.kind === "loop_block" && event.confidence === "exact" && meta.wait_only === true &&
          Number.isInteger(count) && count > 0 && period > 0 && Number.isFinite(end)
          ? [{ start, end }] : [];
      });
      const observed = [{ start: records[0].start,
        end: [...records, ...waitLoops].reduce((last, record) => Math.max(last, record.end), records[0].start) }];
      const blocked = [];
      const acquisitionEnds = new Map();
      for (const { event, start } of records) {
        const meta = event.meta || {};
        if (event.kind !== "acquisition_result" || event.confidence !== "exact" || !meta.rt_packet_id || !["configured", "weight_memory"].includes(meta.integration_length_source)) continue;
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
        if (event.confidence !== "exact" || (meta.preview_source_event_id && !provenWaitCopy) || meta.assumed_branch_path || meta.comparison_paths) {
          blocked.push({ start, end: Infinity });
          continue;
        }
        if (waitKinds.has(event.kind)) continue;
        if (event.kind === "loop_block") {
          const count = concreteTime(meta.count);
          const period = concreteTime(meta.period);
          if (meta.wait_only === true && Number.isInteger(count) && count > 0 && period > 0 && Number.isFinite(start + count * period)) continue;
          const shown = new Set(meta.shown_iterations || [0]);
          let visible = 0;
          while (shown.has(visible)) visible += 1;
          if (count === undefined || count > visible) blocked.push({ start: period > 0 ? start + period * visible : start, end: Infinity });
        } else if (event.kind === "play") {
          // The IR has no waveform end; t1 is only the RT command's blocking end.
          blocked.push({ start, end: Infinity });
        } else if (event.kind === "acquire") {
          const acquisition = acquisitionEnds.get(meta.rt_packet_id);
          blocked.push({ start, end: acquisition && acquisition.start === start ? Math.max(end, acquisition.end) : Infinity });
        } else {
          const annotation = event.kind === "underflow_warning" && meta.status === "possible_underflow";
          blocked.push({ start, end: knownKinds.has(event.kind) || annotation ? end : Infinity });
        }
      }
      // Omitted static iterations have their own loop fold. Preserve the last
      // visible wait's end instead of letting one unchanged gap swallow it.
      const omitted = records.flatMap(({ event }) => {
        const key = String(event.meta?.loop_preview_key || event.meta?.loop_id || event.id);
        const interval = !expandedLoops.has(key) && staticLoopHiddenInterval(event);
        return interval ? [{ start: interval.start + offset, end: interval.end + offset }] : [];
      });
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
        gaps.push({ start, end, displayStart, displayEnd });
        removed += end - start - 64;
      }
    }
    return scale;
  }

  const api = {
    buildQ1TimelineSharedModel,
    buildQ1TimelineSelectionIndex,
    buildTimelineTimeScale,
    buildWaitTimeScale,
    eventLabelToken,
    eventTimeValue,
  };

  root.q1timelineTimelineAdapter = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
