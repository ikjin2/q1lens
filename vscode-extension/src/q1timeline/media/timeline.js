(() => {
  const vscode = acquireVsCodeApi();
  const timelineIr = readTimelineIr();
  const timelineEventsById = new Map((timelineIr.events || []).map((event) => [String(event.id), event]));
  let sharedTimelineActive = false;
  let selectedTimelineEventId = undefined;
  let activeTimelineControlChip = undefined;

  function toggleClass(node, className, enabled) {
    if (!node) {
      return;
    }
    if (node.classList && typeof node.classList.toggle === "function") {
      node.classList.toggle(className, enabled);
      return;
    }
    const classes = new Set(String(node.className || "").split(/\s+/).filter(Boolean));
    if (enabled) {
      classes.add(className);
    } else {
      classes.delete(className);
    }
    node.className = Array.from(classes).join(" ");
  }

  function readTimelineIr() {
    const timelineIrNode = document.getElementById("timeline-ir");
    try {
      return JSON.parse(timelineIrNode ? timelineIrNode.textContent : "{}");
    } catch (_error) {
      return { events: [] };
    }
  }

  function readPersistedWebviewState() {
    try {
      return typeof vscode.getState === "function" ? vscode.getState() || {} : {};
    } catch (_error) {
      return {};
    }
  }

  function writePersistedWebviewState(patch) {
    if (typeof vscode.setState !== "function") {
      return;
    }
    try {
      vscode.setState({ ...readPersistedWebviewState(), ...patch });
    } catch (_error) {
      // VS Code state persistence is best-effort; the UI remains usable without it.
    }
  }

  function initialExpandedQ1IssueSequencers() {
    const state = readPersistedWebviewState();
    const sequencers = state && Array.isArray(state.expandedQ1IssueSequencers)
      ? state.expandedQ1IssueSequencers
      : [];
    return sequencers.map((sequencer) => String(sequencer)).filter(Boolean);
  }

  function writeQ1IssueExpansionState() {
    writePersistedWebviewState({ expandedQ1IssueSequencers: Array.from(expandedQ1IssueSequencers) });
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function displayInspectorValue(value) {
    if (value === undefined || value === null) {
      return "";
    }
    if (typeof value === "object" && Object.prototype.hasOwnProperty.call(value, "display")) {
      return String(value.display);
    }
    if (typeof value === "object") {
      return JSON.stringify(value);
    }
    return String(value);
  }

  function addInspectorField(fields, label, value) {
    const displayed = displayInspectorValue(value);
    if (!displayed) {
      return;
    }
    fields.push(`<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(displayed)}</dd>`);
  }

  function currentTimelineMode() {
    const mode = document.body && document.body.dataset ? document.body.dataset.mode : undefined;
    if (mode === "debug" || mode === "normal") {
      return mode;
    }
    return document.body && document.body.classList && document.body.classList.contains("mode-debug") ? "debug" : "normal";
  }

  function isTimelineEventVisible(eventNode) {
    if (!eventNode) {
      return false;
    }
    if (eventNode.closest(".debug-lane") && currentTimelineMode() === "normal") {
      return false;
    }
    if (eventNode.classList.contains("q1-dense-collapsed") && currentTimelineMode() === "normal") {
      return false;
    }
    if (eventNode.classList.contains("normal-feedback-collapsed") && currentTimelineMode() === "normal") {
      return false;
    }
    if (eventNode.classList.contains("loop-collapsed") && currentTimelineMode() === "normal") {
      return false;
    }
    return !eventNode.classList.contains("is-filtered");
  }

  function renderSharedEventInspector(eventId) {
    const inspectorFields = document.getElementById("event-inspector-fields");
    if (!inspectorFields) {
      return;
    }
    const event = timelineEventsById.get(String(eventId));
    if (!event) {
      inspectorFields.innerHTML = "<dt>Selected event</dt><dd>None</dd>";
      return;
    }
    const meta = event.meta || {};
    const source = event.source || {};
    const fields = [];
    addInspectorField(fields, "Label", event.label || event.kind);
    addInspectorField(fields, "Sequencer", event.sequencer_id);
    addInspectorField(fields, "Lane", event.lane);
    addInspectorField(fields, "Time range", `${displayInspectorValue(eventTime(event, "t0"))} -> ${displayInspectorValue(eventTime(event, "t1"))}`);
    addInspectorField(fields, "Source", source.file ? `${source.file}:${source.line || 1}` : "unavailable");
    addInspectorField(fields, "Confidence", event.confidence);
    addInspectorField(fields, "Q1 issue", event.kind === "q1_issue" ? `${displayInspectorValue(event.t0)} -> ${displayInspectorValue(event.t1)}` : meta.q1_issue_event_id);
    addInspectorField(fields, "RT packet", meta.rt_packet_id);
    addInspectorField(fields, "Queue depth", meta.estimated_depth);
      addInspectorField(fields, "Slack", meta.slack_ns !== undefined ? `${meta.slack_ns} ns` : "");
      addInspectorField(fields, "Branch condition", meta.condition);
      addInspectorField(fields, "Branch policy", meta.branch_policy);
      addInspectorField(fields, "Branch assumption", branchAssumptionDisplay(meta));
      addInspectorField(fields, "Loop context", meta.loop_context || meta.loop_id);
      inspectorFields.innerHTML = fields.join("");
  }

  function selectEventNode(eventNode, options = {}) {
    if (!isTimelineEventVisible(eventNode)) {
      return;
    }
    document.querySelectorAll('.event.is-selected, .event[aria-selected="true"]').forEach((node) => {
      node.classList.remove("is-selected");
      node.setAttribute("aria-selected", "false");
    });
    eventNode.classList.add("is-selected");
    eventNode.setAttribute("aria-selected", "true");
    const eventId = eventNode.dataset.eventId;
    selectedTimelineEventId = eventId;
    syncSharedAnnotationSelection(eventId);
    renderSharedEventInspector(eventId);
    syncTimelineZoomActionState();
    if (options.notify !== false) {
      window.dispatchEvent(new CustomEvent("q1timeline:eventClick", { detail: { eventId } }));
    }
  }

  function syncSharedAnnotationSelection(eventId) {
    const selectedId = String(eventId || "");
    for (const node of document.querySelectorAll(".shared-timeline-stage .timeline-annotation, .shared-timeline-stage .feedback-connector")) {
      const eventIds = String(node.dataset.eventIds || "").split(/\s+/).filter(Boolean);
      node.classList.toggle("is-selected", selectedId !== "" && eventIds.includes(selectedId));
    }
  }

  function eventNodeMatchesId(node, eventId) {
    if (!node || !node.dataset) {
      return false;
    }
    const selectedId = String(eventId);
    if (node.dataset.eventId === selectedId) {
      return true;
    }
    return String(node.dataset.eventIds || "").split(/\s+/).filter(Boolean).includes(selectedId);
  }

  function findVisibleEventNodeByIds(eventIds) {
    const ids = Array.isArray(eventIds) ? eventIds.map((eventId) => String(eventId)) : [];
    for (const eventId of ids) {
      const candidateNodes = Array.from(document.querySelectorAll("[data-event-id]"))
        .filter((node) => eventNodeMatchesId(node, eventId));
      const visibleNode = candidateNodes.find(isTimelineEventVisible);
      if (visibleNode) {
        return visibleNode;
      }
    }
    return undefined;
  }

  function eventIsQ1Issue(event) {
    return Boolean(event && (event.kind === "q1_issue" || String(event.lane || "").includes("q1_issue")));
  }

  function highlightedEventForIds(eventIds) {
    const ids = Array.isArray(eventIds) ? eventIds.map((eventId) => String(eventId)) : [];
    for (const eventId of ids) {
      const event = timelineEventsById.get(eventId);
      if (event) {
        return event;
      }
    }
    return undefined;
  }

  function prioritizedHighlightEventIds(eventIds) {
    const ids = Array.isArray(eventIds) ? eventIds.map((eventId) => String(eventId)) : [];
    return ids.slice().sort((left, right) => {
      const leftIsQ1Issue = eventIsQ1Issue(timelineEventsById.get(left));
      const rightIsQ1Issue = eventIsQ1Issue(timelineEventsById.get(right));
      return Number(rightIsQ1Issue) - Number(leftIsQ1Issue);
    });
  }

  function eventSequencerId(event) {
    return normalizeSequencerId((event && event.sequencer_id) || timelineSequencerFromLane(event && event.lane));
  }

  function expandQ1IssueLanesForHighlight(eventIds) {
    let changed = false;
    for (const eventId of Array.isArray(eventIds) ? eventIds : []) {
      const event = timelineEventsById.get(String(eventId));
      if (!eventIsQ1Issue(event)) {
        continue;
      }
      const sequencer = eventSequencerId(event);
      if (sequencer && !expandedQ1IssueSequencers.has(sequencer)) {
        expandedQ1IssueSequencers.add(sequencer);
        changed = true;
      }
    }
    if (changed) {
      writeQ1IssueExpansionState();
      applyTimelineScale();
      updateLaneDependentVisibility();
      syncSequencerFoldControls();
      syncSharedSequencerFoldControls();
    }
    return changed;
  }

  function loopPreviewEventIds(meta) {
    const ids = [];
    const seen = new Set();
    function addId(eventId) {
      const normalized = String(eventId || "");
      if (!normalized || seen.has(normalized)) {
        return;
      }
      seen.add(normalized);
      ids.push(normalized);
    }
    if (Array.isArray(meta && meta.first_iteration_event_ids)) {
      for (const eventId of meta.first_iteration_event_ids) {
        addId(eventId);
      }
    }
    const byIteration = meta && meta.preview_iteration_event_ids;
    if (byIteration && typeof byIteration === "object" && !Array.isArray(byIteration)) {
      for (const key of Object.keys(byIteration).sort((left, right) => Number(left) - Number(right))) {
        const iterationIds = byIteration[key];
        if (!Array.isArray(iterationIds)) {
          continue;
        }
        for (const eventId of iterationIds) {
          addId(eventId);
        }
      }
    }
    return ids;
  }

  function expandQ1IssueLanesForLoopPreview(meta) {
    return expandQ1IssueLanesForHighlight(loopPreviewEventIds(meta));
  }

  function branchAssumptionLabel(meta) {
    if (!meta || !meta.assumed_branch_path) {
      return "";
    }
    if (meta.assumed_branch_path === "taken" && meta.assumed_branch_taken === true) {
      return "taken";
    }
    if (meta.assumed_branch_path === "fallthrough" && meta.assumed_branch_taken === false) {
      return "fallthrough";
    }
    return String(meta.assumed_branch_path);
  }

  function branchAssumptionDisplay(meta) {
    const path = branchAssumptionLabel(meta);
    if (path === "taken") {
      return "true (jump target)";
    }
    if (path === "fallthrough") {
      return "false (continue)";
    }
    if (path === "both") {
      return "true and false";
    }
    if (path === "collapsed") {
      return "collapsed";
    }
    if (meta && meta.branch_taken === true) {
      return "true (jump target)";
    }
    if (meta && meta.branch_taken === false) {
      return "false (continue)";
    }
    return meta && meta.branch_taken;
  }

  function eventHasBranchControl(event) {
    const meta = event && event.meta && typeof event.meta === "object" ? event.meta : {};
    if (event && event.kind === "branch_region" && (meta.branch_id || meta.branch_comparison_branch_id)) {
      return true;
    }
    return Boolean(event && event.kind === "q1_issue" && (meta.branch_id || meta.branch_comparison_branch_id));
  }

  function branchAssumptionIconName(meta) {
    const path = branchAssumptionLabel(meta);
    if (path === "taken" || path === "fallthrough" || path === "both" || path === "collapsed") {
      return path;
    }
    return "branch";
  }

  function branchSourceJumpLabel(event) {
    const source = event && event.source && typeof event.source === "object" ? event.source : {};
    const line = Number(source.line);
    const raw = String(source.raw || "").split("#", 1)[0].trim().replace(/\s+/g, " ");
    if (Number.isFinite(line) && line > 0 && raw) {
      return `Line ${line}: ${raw}`;
    }
    if (Number.isFinite(line) && line > 0) {
      return `Line ${line}`;
    }
    return "Q1ASM source";
  }

  function normalizedSource(source) {
    if (!source || typeof source !== "object") {
      return undefined;
    }
    const file = String(source.file || "");
    const line = Number(source.line);
    const column = Number(source.column || 1);
    if (!file || !Number.isInteger(line) || line <= 0) {
      return undefined;
    }
    return { file, line, column: Number.isInteger(column) && column > 0 ? column : 1 };
  }

  function sameSourceLine(left, right) {
    const leftSource = normalizedSource(left);
    const rightSource = normalizedSource(right);
    return Boolean(leftSource && rightSource && leftSource.file === rightSource.file && leftSource.line === rightSource.line);
  }

  function controlFlowGraphSequencers() {
    const graph = timelineIr.control_flow_graph;
    return graph && Array.isArray(graph.sequencers) ? graph.sequencers : [];
  }

  function controlFlowGraphNode(sequencer, nodeId) {
    const nodes = Array.isArray(sequencer && sequencer.nodes) ? sequencer.nodes : [];
    return nodes.find((node) => String(node && node.id || "") === String(nodeId || ""));
  }

  function branchPathSource(event, path) {
    if (path === "taken" || path === "fallthrough") {
      const expectedKind = path === "taken" ? "branch_taken" : "branch_fallthrough";
      for (const sequencer of controlFlowGraphSequencers()) {
        const edges = Array.isArray(sequencer && sequencer.edges) ? sequencer.edges : [];
        for (const edge of edges) {
          if (String(edge && edge.kind || "") !== expectedKind || !sameSourceLine(edge && edge.source, event && event.source)) {
            continue;
          }
          const node = controlFlowGraphNode(sequencer, edge.to_node_id);
          const source = normalizedSource(node && node.source);
          if (source) {
            return source;
          }
        }
      }
    }
    return normalizedSource(event && event.source);
  }

  function postSourceJump(source) {
    if (!source) {
      return;
    }
    vscode.postMessage({ type: "sourceClick", file: source.file, line: source.line, column: source.column });
  }

  function timelineControlLoopVisibleIterations(meta) {
    const shownIterations = Array.isArray(meta && meta.shown_iterations) ? meta.shown_iterations : [];
    if (shownIterations.length > 0) {
      return shownIterations.length;
    }
    const visibleIterationCount = Number(meta && meta.visible_iteration_count);
    return Number.isInteger(visibleIterationCount) && visibleIterationCount > 0 ? visibleIterationCount : 1;
  }

  function timelineControlLoopCap(meta) {
    const cap = Number(meta && meta.loop_preview_cap);
    return Number.isInteger(cap) && cap > 0 ? cap : 10;
  }

  function timelineControlLoopTotalIterations(meta) {
    const rawCount = meta && meta.count;
    if (typeof rawCount === "number" && Number.isInteger(rawCount) && rawCount > 0) {
      return rawCount;
    }
    const parsed = Number(rawCount && typeof rawCount === "object" ? rawCount.value ?? rawCount.display : rawCount);
    return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
  }

  function canShowNextTimelineControlLoopIteration(meta) {
    const visible = timelineControlLoopVisibleIterations(meta);
    const total = timelineControlLoopTotalIterations(meta);
    return visible < timelineControlLoopCap(meta) && (total === undefined || visible < total);
  }

  function escapeRegExp(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function applyLoopPreviewResetOptimistically(loopKey) {
    const events = Array.isArray(timelineIr.events) ? timelineIr.events : [];
    const loopEvent = events.find((event) => {
      const meta = event && event.meta && typeof event.meta === "object" ? event.meta : {};
      return event && event.kind === "loop_block" && String(meta.loop_preview_key || "") === String(loopKey || "");
    });
    const loopMeta = loopEvent && loopEvent.meta && typeof loopEvent.meta === "object" ? loopEvent.meta : {};
    const loopId = String(loopMeta.loop_id || "");
    if (!loopEvent || !loopId) {
      return false;
    }
    const extraIterationIdPattern = new RegExp(`:loop-${escapeRegExp(loopId)}-iter-\\d+$`);
    const removedEventIds = new Set();
    timelineIr.events = events.filter((event) => {
      const eventId = String(event && event.id || "");
      if (extraIterationIdPattern.test(eventId)) {
        removedEventIds.add(eventId);
        timelineEventsById.delete(eventId);
        return false;
      }
      return true;
    });
    loopMeta.visible_iteration_count = 1;
    loopMeta.shown_iterations = [0];
    timelineEventsById.set(String(loopEvent.id), loopEvent);
    if (Array.isArray(timelineIr.feedback_flows) && removedEventIds.size) {
      timelineIr.feedback_flows = timelineIr.feedback_flows.filter((flow) => (
        !removedEventIds.has(String(flow && flow.from_event_id || "")) &&
        !removedEventIds.has(String(flow && flow.to_event_id || ""))
      ));
    }
    if (selectedTimelineEventId && removedEventIds.has(String(selectedTimelineEventId))) {
      selectedTimelineEventId = undefined;
    }
    renderSharedTimelineIfAvailable(currentTimelineMode(), { preserveSelection: true });
    return true;
  }

  function timelineControlSvgIcon(name) {
    const icons = {
      branch: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19V5"/><path d="M5 12h5a5 5 0 0 0 5-5V5"/><path d="M10 12a5 5 0 0 1 5 5v2"/><path d="m12 7 3-3 3 3"/><path d="m12 17 3 3 3-3"/></svg>',
      taken: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 18h4a8 8 0 0 0 8-8V5"/><path d="m12 9 4-4 4 4"/></svg>',
      fallthrough: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6v4a8 8 0 0 0 8 8h4"/><path d="m14 14 4 4-4 4"/></svg>',
      both: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19V5"/><path d="M5 12h5a5 5 0 0 0 5-5V5"/><path d="M10 12a5 5 0 0 1 5 5v2"/><path d="m12 7 3-3 3 3"/><path d="m12 17 3 3 3-3"/></svg>',
      collapsed: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5l14 14"/><path d="M19 5 5 19"/></svg>',
      next: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.4 5.7"/><path d="M20 4v7h-7"/><path d="M12 8v8"/><path d="M8 12h8"/></svg>',
      reset: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7v6h6"/><path d="M20 17a8 8 0 0 1-13.7-5.7L4 13"/></svg>',
      zoom: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m16 16 5 5"/><path d="M11 8v6"/><path d="M8 11h6"/></svg>',
    };
    return icons[name] || "";
  }

  function timelineControlChipLeft(node) {
    const left = Number.parseFloat(node && node.style ? node.style.left : "");
    const width = Number.parseFloat(node && node.style ? node.style.width : "");
    if (!Number.isFinite(left)) {
      return "0%";
    }
    const offset = Number.isFinite(width) ? Math.min(width / 2, 2) : 0;
    return `${Math.max(0, Math.min(99, left + offset))}%`;
  }

  function timelineControlChipStackKey(node) {
    const row = node && node.closest ? node.closest("[data-lane]") : undefined;
    const lane = row && row.dataset ? String(row.dataset.lane || "") : "";
    return `${lane}@${timelineControlChipLeft(node)}`;
  }

  function timelineControlChipTop(stackIndex) {
    const index = Math.max(0, Number.isFinite(Number(stackIndex)) ? Number(stackIndex) : 0);
    return `${1 + index * 17}px`;
  }

  function ensureTimelineControlOverlay() {
    let overlay = document.getElementById("q1timeline-control-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "q1timeline-control-overlay";
      overlay.className = "q1timeline-control-overlay";
      document.body.append(overlay);
    }
    return overlay;
  }

  function positionTimelineControlPopover(popover, chip) {
    const chipRect = chip.getBoundingClientRect();
    popover.style.left = "0px";
    popover.style.top = "0px";
    popover.style.visibility = "hidden";
    const popoverRect = popover.getBoundingClientRect();
    const viewportWidth = document.documentElement.clientWidth || window.innerWidth || 1;
    const viewportHeight = document.documentElement.clientHeight || window.innerHeight || 1;
    const centerX = chipRect.left + chipRect.width / 2;
    let left = centerX - popoverRect.width / 2;
    left = Math.max(8, Math.min(left, viewportWidth - popoverRect.width - 8));
    let top = chipRect.top - popoverRect.height - 8;
    let placement = "top";
    if (top < 8) {
      top = chipRect.bottom + 8;
      placement = "bottom";
    }
    top = Math.max(8, Math.min(top, viewportHeight - popoverRect.height - 8));
    popover.dataset.placement = placement;
    popover.style.setProperty("--q1timeline-control-anchor-x", `${Math.max(10, Math.min(centerX - left, popoverRect.width - 10))}px`);
    popover.style.left = `${left}px`;
    popover.style.top = `${top}px`;
    popover.style.visibility = "";
  }

  function repositionActiveTimelineControlPopover() {
    const popover = document.getElementById("q1timeline-control-popover");
    if (!popover || !activeTimelineControlChip || !document.body.contains(activeTimelineControlChip)) {
      return;
    }
    positionTimelineControlPopover(popover, activeTimelineControlChip);
  }

  function closeTimelineControlPopover() {
    const existing = document.getElementById("q1timeline-control-popover");
    if (existing) {
      existing.remove();
    }
    activeTimelineControlChip = undefined;
    for (const chip of document.querySelectorAll(".q1timeline-control-chip[aria-expanded='true'], .q1timeline-control-loop-lane[aria-expanded='true']")) {
      chip.setAttribute("aria-expanded", "false");
    }
  }

  function addTimelineControlAction(popover, iconName, label, onClick, options = {}) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "q1timeline-control-action";
    button.innerHTML = timelineControlSvgIcon(iconName);
    button.setAttribute("aria-label", label);
    button.title = label;
    if (options.active) {
      button.classList.add("is-active");
    }
    if (options.danger) {
      button.classList.add("is-danger");
    }
    if (options.disabled) {
      button.disabled = true;
    }
    button.addEventListener("click", (clickEvent) => {
      clickEvent.preventDefault();
      clickEvent.stopPropagation();
      if (!button.disabled) {
        onClick();
      }
    });
    popover.append(button);
  }

  function openTimelineControlPopover(chip, event, actionKind) {
    const existing = document.getElementById("q1timeline-control-popover");
    const shouldClose = existing && existing.dataset.ownerChipId === chip.dataset.controlChipId;
    closeTimelineControlPopover();
    if (shouldClose) {
      return;
    }
    const meta = event && event.meta && typeof event.meta === "object" ? event.meta : {};
    const popover = document.createElement("div");
    popover.id = "q1timeline-control-popover";
    popover.className = "q1timeline-control-popover";
    popover.dataset.ownerChipId = chip.dataset.controlChipId || "";
    chip.setAttribute("aria-expanded", "true");
    activeTimelineControlChip = chip;
    if (actionKind === "branch") {
      const branchId = String(meta.branch_id || meta.branch_comparison_branch_id || "");
      const active = branchAssumptionLabel(meta);
      for (const [path, iconName, label] of [
        ["zoom", "zoom", "Zoom around branch"],
      ]) {
        addTimelineControlAction(popover, iconName, label, () => {
          zoomTimelineToEvent(event);
          closeTimelineControlPopover();
        });
      }
      for (const [path, iconName, label] of [
        ["taken", "taken", "Condition true: jump target"],
        ["fallthrough", "fallthrough", "Condition false: continue"],
        ["collapsed", "collapsed", "Clear branch override"],
      ]) {
        addTimelineControlAction(popover, iconName, label, () => {
          vscode.postMessage({ type: "setBranchAssumption", branchId, path });
          postSourceJump(branchPathSource(event, path));
          closeTimelineControlPopover();
        }, { active: active === path, danger: path === "collapsed" });
      }
    } else if (actionKind === "loop") {
      const loopKey = String(meta.loop_preview_key || "");
      const visible = timelineControlLoopVisibleIterations(meta);
      addTimelineControlAction(popover, "next", "Show next iteration", () => {
        expandQ1IssueLanesForLoopPreview(meta);
        const visibleIterations = visible + 1;
        vscode.postMessage({ type: "setLoopPreview", loopKey, visibleIterations });
        closeTimelineControlPopover();
      }, { active: visible > 1, disabled: !canShowNextTimelineControlLoopIteration(meta) });
      addTimelineControlAction(popover, "reset", "Reset loop preview", () => {
        const visibleIterations = 1;
        applyLoopPreviewResetOptimistically(loopKey);
        vscode.postMessage({ type: "setLoopPreview", loopKey, visibleIterations });
        closeTimelineControlPopover();
        }, { danger: true, disabled: visible <= 1 });
    }
    ensureTimelineControlOverlay().append(popover);
    positionTimelineControlPopover(popover, chip);
  }

  function addTimelineControlChip(sourceNode, event, actionKind, stackIndex = 0) {
    const meta = event && event.meta && typeof event.meta === "object" ? event.meta : {};
    const track = sourceNode.closest(".lane-track");
    if (!track) {
      return;
    }
    const isBranch = actionKind === "branch";
    if (!isBranch) {
      return;
    }
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "q1timeline-control-chip q1timeline-control-chip-branch";
    chip.dataset.eventId = String(event.id);
    chip.dataset.controlChipId = `${actionKind}:${event.id}`;
    chip.setAttribute("aria-haspopup", "menu");
    chip.setAttribute("aria-expanded", "false");
    chip.setAttribute("aria-label", branchSourceJumpLabel(event));
    chip.title = chip.getAttribute("aria-label");
    chip.style.left = timelineControlChipLeft(sourceNode);
    chip.style.top = timelineControlChipTop(stackIndex);
    chip.dataset.stackIndex = String(stackIndex);
    chip.innerHTML = timelineControlSvgIcon(branchAssumptionIconName(meta));
    if (branchAssumptionLabel(meta)) {
      chip.dataset.state = branchAssumptionLabel(meta);
    }
    chip.addEventListener("click", (clickEvent) => {
      clickEvent.preventDefault();
      clickEvent.stopPropagation();
      selectEventNode(sourceNode);
      openTimelineControlPopover(chip, event, actionKind);
    });
    track.append(chip);
  }

  function handleLoopLaneTimelineControlActivation(node, domEvent) {
    const event = timelineEventsById.get(String(node.dataset.eventId));
    const meta = event && event.meta && typeof event.meta === "object" ? event.meta : {};
    if (!event || event.kind !== "loop_block" || !meta.loop_preview_key) {
      return;
    }
    domEvent.preventDefault();
    domEvent.stopPropagation();
    selectEventNode(node, { notify: false });
    openTimelineControlPopover(node, event, "loop");
  }

  function installLoopLaneTimelineControls() {
    const loopNodes = Array.from(document.querySelectorAll(".shared-timeline-stage .timeline-loop-range[data-event-id]"));
    for (const node of loopNodes) {
      const event = timelineEventsById.get(String(node.dataset.eventId));
      const meta = event && event.meta && typeof event.meta === "object" ? event.meta : {};
      node.classList.remove("q1timeline-control-loop-lane");
      delete node.dataset.controlChipId;
      node.removeAttribute("role");
      node.removeAttribute("aria-haspopup");
      node.removeAttribute("aria-expanded");
      node.removeAttribute("aria-label");
      node.removeAttribute("title");
      node.removeAttribute("data-state");
      node.removeAttribute("tabindex");
      if (event && event.kind === "loop_block" && meta.loop_preview_key) {
        node.classList.add("q1timeline-control-loop-lane");
        node.dataset.controlChipId = `loop:${event.id}`;
        node.dataset.state = String(timelineControlLoopVisibleIterations(meta));
        node.setAttribute("role", "button");
        node.setAttribute("aria-haspopup", "menu");
        node.setAttribute("aria-expanded", "false");
        node.setAttribute("aria-label", "Loop preview options");
        node.title = node.getAttribute("aria-label");
        node.tabIndex = 0;
      }
      if (node.dataset.timelineControlLoopInstalled !== "true") {
        node.dataset.timelineControlLoopInstalled = "true";
        node.addEventListener("click", (clickEvent) => {
          handleLoopLaneTimelineControlActivation(node, clickEvent);
        });
        node.addEventListener("keydown", (keyEvent) => {
          if (keyEvent.key === "Enter" || keyEvent.key === " ") {
            handleLoopLaneTimelineControlActivation(node, keyEvent);
          }
        });
      }
    }
  }

  function installTimelineControlChips() {
    closeTimelineControlPopover();
    document.querySelectorAll(".q1timeline-control-chip").forEach((node) => node.remove());
    const branchNodes = Array.from(document.querySelectorAll(".shared-timeline-stage .timeline-block[data-event-id]"));
    const chipStackCounts = new Map();
    for (const node of branchNodes) {
      const event = timelineEventsById.get(String(node.dataset.eventId));
      if (event && eventHasBranchControl(event)) {
        const stackKey = timelineControlChipStackKey(node);
        const stackIndex = chipStackCounts.get(stackKey) || 0;
        chipStackCounts.set(stackKey, stackIndex + 1);
        addTimelineControlChip(node, event, "branch", stackIndex);
      }
    }
    installLoopLaneTimelineControls();
  }

  function installSharedQ1IssueControls() {
    const q1IssueRows = Array.from(document.querySelectorAll('.shared-timeline-stage [data-lane-role="q1-issue"]'));
    const sequencers = new Set(q1IssueRows.map(q1IssueLaneParent).filter(Boolean));
    for (const sequencer of sequencers) {
      const parentRow = Array.from(document.querySelectorAll(".shared-timeline-stage [data-lane]")).find(
        (laneNode) => !isQ1IssueLaneNode(laneNode) && laneBelongsToSequencer(laneNode.dataset.lane, sequencer),
      );
      const labelNode = parentRow ? parentRow.querySelector(".lane-label") : undefined;
      if (!labelNode || labelNode.querySelector(".shared-q1-issue-toggle")) {
        continue;
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "shared-q1-issue-toggle";
      button.dataset.sequencerControl = sequencer;
      button.addEventListener("click", (event) => {
        stopSequencerControlEvent(event);
        toggleSequencerFoldControl(sequencer);
      });
      button.addEventListener("pointerdown", stopSequencerControlEvent);
      labelNode.prepend(button);
    }
  }

  function syncSharedSequencerFoldControls() {
    for (const laneNode of document.querySelectorAll('.shared-timeline-stage [data-lane-role="q1-issue"]')) {
      const sequencer = q1IssueLaneParent(laneNode);
      setTimelineNodeHidden(laneNode, !sequencerQ1IssueExpanded(sequencer));
    }
    for (const button of document.querySelectorAll(".shared-q1-issue-toggle")) {
      const sequencer = button.dataset.sequencerControl;
      const expanded = sequencerQ1IssueExpanded(sequencer);
      button.textContent = "";
      button.setAttribute("aria-expanded", String(expanded));
      button.setAttribute("aria-label", `${expanded ? "Hide" : "Show"} Q1 issue for ${sequencer}`);
      button.title = button.getAttribute("aria-label");
    }
  }

  function sharedTimelineViewport() {
    const window = currentTimelineWindow();
    return { start: window.min, end: window.max };
  }

  function sharedTimelineTicks() {
    const window = currentTimelineWindow();
    const span = Math.max(window.max - window.min, 1);
    return timelineTicks(window.min, window.max).map((tick) => ({
      leftPercent: Math.round(((tick - window.min) / span) * 100000) / 1000,
      label: `${formatWindowValue(tick)} ns`,
    }));
  }

  function normalizedTimelineSelectionRange() {
    if (!timelineSelectionRange) {
      return undefined;
    }
    const start = Number(timelineSelectionRange.start);
    const end = Number(timelineSelectionRange.end);
    if (!Number.isFinite(start) || !Number.isFinite(end)) {
      return undefined;
    }
    return { start: Math.min(start, end), end: Math.max(start, end) };
  }

  function sharedTimelineSelectionRange() {
    return normalizedTimelineSelectionRange();
  }

  function applySharedSelection(options) {
    if (options && options.preserveSelection === false) {
      renderSharedEventInspector(null);
      return;
    }
    const selectedNode = selectedTimelineEventId ? findVisibleEventNodeByIds([selectedTimelineEventId]) : undefined;
    if (selectedNode) {
      selectEventNode(selectedNode, { notify: false });
      return;
    }
    renderSharedEventInspector(null);
  }

  function renderSharedTimelineIfAvailable(mode = currentTimelineMode(), options = {}) {
    const root = document.getElementById("timeline-root");
    if (
      !root ||
      !window.q1lensSharedTimeline ||
      !window.q1lensSharedTimeline.renderTimeline ||
      !window.q1timelineTimelineAdapter ||
      !window.q1timelineTimelineAdapter.buildQ1TimelineSharedModel
    ) {
      return false;
    }
    currentTimelineWindow();
    const model = window.q1timelineTimelineAdapter.buildQ1TimelineSharedModel(timelineIr, {
      mode,
      timeBasis: timelineTimeBasis,
      expandedQ1IssueSequencers: Array.from(expandedQ1IssueSequencers || []),
    });
    model.viewport = sharedTimelineViewport();
    model.ticks = sharedTimelineTicks();
    model.selectionRange = sharedTimelineSelectionRange();
    for (const lane of model.lanes || []) {
      for (const block of lane.blocks || []) {
        block.selected = selectedTimelineEventId !== undefined && String(block.eventId || block.id) === selectedTimelineEventId;
      }
    }
    for (const annotation of model.annotations || []) {
      const eventIds = Array.isArray(annotation.eventIds) ? annotation.eventIds.map((eventId) => String(eventId)) : [];
      annotation.selected = selectedTimelineEventId !== undefined && eventIds.includes(String(selectedTimelineEventId));
    }
    window.q1lensSharedTimeline.renderTimeline(root, model, {
      onBlockClick: (_block, node, event) => {
        if (event && typeof event.stopPropagation === "function") {
          event.stopPropagation();
        }
        selectEventNode(node);
      },
      onAnnotationClick: (annotation, _node, event) => {
        if (event && typeof event.stopPropagation === "function") {
          event.stopPropagation();
        }
        const eventIds = Array.isArray(annotation.eventIds) ? annotation.eventIds : [annotation.eventId];
        const target = findVisibleEventNodeByIds(eventIds);
        if (target) {
          selectEventNode(target);
        }
      },
    });
    sharedTimelineActive = true;
    renderTimelineWindowStatus(currentTimelineWindow());
    renderTimeBasisToggle();
    installSharedQ1IssueControls();
    syncSharedSequencerFoldControls();
    installTimelineControlChips();
    installTimelineMouseInteractions();
    applySharedSelection(options);
    return true;
  }

  function initializeRenderedTimeline() {
    const modeButtons = document.querySelectorAll("button[data-mode]");
    const eventsById = timelineEventsById;
    const eventInspector = document.getElementById("event-inspector");
    const inspectorFields = document.getElementById("event-inspector-fields");
    const timeCursor = document.getElementById("time-cursor");

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function displayInspectorValue(value) {
      if (value === undefined || value === null) {
        return "";
      }
      if (typeof value === "object") {
        return JSON.stringify(value);
      }
      return String(value);
    }

    function addInspectorField(fields, label, value) {
      const displayed = displayInspectorValue(value);
      if (!displayed) {
        return;
      }
      fields.push(`<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(displayed)}</dd>`);
    }

    function displayResolvedValue(value) {
      if (value === undefined || value === null) {
        return "";
      }
      if (typeof value === "object" && Object.prototype.hasOwnProperty.call(value, "display")) {
        return displayInspectorValue(value.display);
      }
      return displayInspectorValue(value);
    }

    function displayRawParameterValue(value) {
      if (typeof value === "string") {
        return JSON.stringify(value);
      }
      return displayInspectorValue(value);
    }

    function displayResolutionStep(step) {
      if (!step || typeof step !== "object") {
        return "";
      }
      if (step.kind === "def" && step.name && step.raw) {
        return `def ${step.name}=${step.raw}`;
      }
      if (step.kind === "param" && step.name) {
        return `param ${step.name}=${displayRawParameterValue(step.raw_value)}`;
      }
      if (step.kind && step.name) {
        return `${step.kind} ${step.name}=${displayResolvedValue(step.value)}`;
      }
      return displayInspectorValue(step);
    }

    function displayResolvedArg(arg) {
      if (!arg || typeof arg !== "object") {
        return "";
      }
      let label = `arg ${arg.index}: ${arg.raw} -> ${displayResolvedValue(arg.value)}`;
      const chain = Array.isArray(arg.chain)
        ? arg.chain.map(displayResolutionStep).filter(Boolean)
        : [];
      if (chain.length) {
        label += ` (${chain.join("; ")})`;
      }
      return label;
    }

    function displayDurationProvenance(provenance) {
      if (!provenance || typeof provenance !== "object") {
        return "";
      }
      const expression = provenance.expression ? String(provenance.expression) : "";
      const value = provenance.value ? displayResolvedValue(provenance.value) : "";
      const detail = expression && value ? `${expression} = ${value} ns` : expression || value;
      const symbol = provenance.symbol || provenance.register || "";
      return symbol && detail ? `${symbol}: ${detail}` : detail;
    }

    function addResolvedArgsInspectorField(fields, resolvedArgs) {
      const items = Array.isArray(resolvedArgs)
        ? resolvedArgs.map(displayResolvedArg).filter(Boolean)
        : [];
      if (!items.length) {
        return;
      }
      fields.push(`<dt>Resolved args</dt><dd>${items.map(escapeHtml).join("<br>")}</dd>`);
    }

    function renderInspector(eventId) {
      if (!inspectorFields) {
        return;
      }
      const event = eventsById.get(String(eventId));
      if (!event) {
        inspectorFields.innerHTML = "<dt>Selected event</dt><dd>None</dd>";
        renderLoopPreviewAction(null);
        return;
      }
      const meta = event.meta || {};
      const source = event.source || {};
      const fields = [];
      addInspectorField(fields, "Label", event.label || event.kind);
      addInspectorField(fields, "Sequencer", event.sequencer_id);
      addInspectorField(fields, "Lane", event.lane);
      addInspectorField(fields, "Time range", `${displayInspectorValue(eventTime(event, "t0"))} -> ${displayInspectorValue(eventTime(event, "t1"))}`);
      addInspectorField(fields, "Local time", `${displayInspectorValue(concreteTime(event.t0))} -> ${displayInspectorValue(concreteTime(event.t1))}`);
      addInspectorField(fields, "Aligned time", meta.aligned_t0 !== undefined || meta.aligned_t1 !== undefined ? `${displayInspectorValue(meta.aligned_t0)} -> ${displayInspectorValue(meta.aligned_t1)}` : "");
      addInspectorField(fields, "Source", source.file ? `${source.file}:${source.line || 1}` : "unavailable");
      addInspectorField(fields, "Confidence", event.confidence);
      addInspectorField(fields, "Q1 issue", event.kind === "q1_issue" ? `${displayInspectorValue(event.t0)} -> ${displayInspectorValue(event.t1)}` : meta.q1_issue_event_id);
      addInspectorField(fields, "RT packet", meta.rt_packet_id);
      addInspectorField(fields, "Queue depth", meta.estimated_depth);
      addInspectorField(fields, "Slack", meta.slack_ns !== undefined ? `${meta.slack_ns} ns` : "");
      addInspectorField(fields, "Branch condition", meta.condition);
      addInspectorField(fields, "Branch policy", meta.branch_policy);
      addInspectorField(fields, "Branch assumption", branchAssumptionDisplay(meta));
      addInspectorField(fields, "Loop context", meta.loop_context || meta.loop_id);
      addInspectorField(fields, "Latched state", meta.field ? `${meta.field}=${displayInspectorValue(meta.value)}` : meta.applied_state);
      addInspectorField(fields, "Duration role", meta.duration_provenance && meta.duration_provenance.role);
      addInspectorField(fields, "Duration expression", displayDurationProvenance(meta.duration_provenance));
      addResolvedArgsInspectorField(fields, meta.resolved_args);
      inspectorFields.innerHTML = fields.join("");
      renderLoopPreviewAction(event);
    }

    function findEventNodeById(eventId) {
      for (const node of document.querySelectorAll("[data-event-id]")) {
        if (eventNodeMatchesId(node, eventId)) {
          return node;
        }
      }
      return undefined;
    }

    function renderLoopPreviewAction(event) {
      const existing = document.getElementById("q1timeline-open-loop-preview");
      if (existing) {
        existing.remove();
      }
      if (!eventInspector || !event || event.kind !== "loop_block") {
        return;
      }
      const meta = event.meta || {};
      const previewEventIds = Array.isArray(meta.first_iteration_event_ids) ? meta.first_iteration_event_ids : [];
      if (!previewEventIds.length) {
        return;
      }
      const button = document.createElement("button");
      button.id = "q1timeline-open-loop-preview";
      button.type = "button";
      button.textContent = "Open first iteration";
      button.dataset.previewEventIds = JSON.stringify(previewEventIds);
      button.addEventListener("click", () => {
        const previewNode = findEventNodeById(previewEventIds[0]);
        if (!previewNode) {
          return;
        }
        selectEventNode(previewNode);
        previewNode.scrollIntoView({ block: "center", inline: "center" });
      });
      eventInspector.append(button);
    }

    function selectEventNode(eventNode, options = {}) {
      if (!isTimelineEventVisible(eventNode)) {
        return;
      }
      document.querySelectorAll(".event.is-selected").forEach((node) => node.classList.remove("is-selected"));
      eventNode.classList.add("is-selected");
      const eventId = eventNode.dataset.eventId;
      selectedTimelineEventId = eventId;
      renderInspector(eventId);
      syncTimelineZoomActionState();
      if (options.notify !== false) {
        window.dispatchEvent(new CustomEvent("q1timeline:eventClick", { detail: { eventId } }));
      }
    }

    function findVisibleEventNodeByIds(eventIds) {
      const ids = Array.isArray(eventIds) ? eventIds.map((eventId) => String(eventId)) : [];
      for (const eventId of ids) {
        const candidateNodes = Array.from(document.querySelectorAll("[data-event-id]"))
          .filter((node) => eventNodeMatchesId(node, eventId));
        const visibleNode = candidateNodes.find(isTimelineEventVisible);
        if (visibleNode) {
          return visibleNode;
        }
      }
      return undefined;
    }

    function setMode(mode) {
      toggleClass(document.body, "mode-debug", mode === "debug");
      toggleClass(document.body, "mode-normal", mode === "normal");
      document.body.dataset.mode = mode;
      for (const button of modeButtons) {
        button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
      }
    }

    function moveTimeCursor(eventNode) {
      if (!timeCursor || !eventNode || !eventNode.dataset.t0X) {
        return;
      }
      if (!isTimelineEventVisible(eventNode)) {
        return;
      }
      timeCursor.setAttribute("x1", eventNode.dataset.t0X);
      timeCursor.setAttribute("x2", eventNode.dataset.t0X);
      timeCursor.removeAttribute("hidden");
    }

    function hideTimeCursor() {
      if (timeCursor) {
        timeCursor.setAttribute("hidden", "");
      }
    }

    for (const button of modeButtons) {
      button.addEventListener("click", () => {
        requestViewMode(button.dataset.mode);
      });
    }

    const filterInput = document.getElementById("event-filter");
    if (filterInput) {
      filterInput.addEventListener("input", () => {
        const query = filterInput.value.trim().toLowerCase();
        for (const eventNode of document.querySelectorAll(".event")) {
          const searchable = eventNode.dataset.search || "";
          eventNode.classList.toggle("is-filtered", query !== "" && !searchable.includes(query));
        }
      });
    }

    for (const eventNode of document.querySelectorAll(".event")) {
      eventNode.addEventListener("mouseenter", () => {
        moveTimeCursor(eventNode);
      });
      eventNode.addEventListener("focus", () => {
        moveTimeCursor(eventNode);
      });
      eventNode.addEventListener("mouseleave", () => {
        hideTimeCursor();
      });
      eventNode.addEventListener("blur", () => {
        hideTimeCursor();
      });
      eventNode.addEventListener("click", (event) => {
        event.stopPropagation();
        selectEventNode(eventNode);
      });
    }

    function installDiagnosticBadgeInteractions() {
      for (const badge of document.querySelectorAll(".diagnostic-badge")) {
        badge.setAttribute("tabindex", "0");
        badge.setAttribute("role", "button");
        const eventNode = badge.closest("[data-event-id]");
        if (!eventNode) {
          continue;
        }
        badge.addEventListener("mouseenter", () => {
          moveTimeCursor(eventNode);
        });
        badge.addEventListener("focus", () => {
          moveTimeCursor(eventNode);
        });
        badge.addEventListener("mouseleave", () => {
          hideTimeCursor();
        });
        badge.addEventListener("blur", () => {
          hideTimeCursor();
        });
        badge.addEventListener("click", (event) => {
          event.stopPropagation();
          selectEventNode(eventNode);
        });
      }
    }

    installDiagnosticBadgeInteractions();

    for (const link of document.querySelectorAll("[data-related-event-id]")) {
      link.addEventListener("click", () => {
        const eventId = link.dataset.relatedEventId;
        const eventNode = document.querySelector(`[data-event-id="${eventId}"]`);
        if (eventNode) {
          selectEventNode(eventNode);
        }
      });
    }

    for (const link of document.querySelectorAll("[data-semantic-event-id]")) {
      link.addEventListener("click", () => {
        const eventId = link.dataset.semanticEventId;
        const eventNode = document.querySelector(`[data-event-id="${eventId}"]`);
        if (eventNode) {
          selectEventNode(eventNode);
        }
      });
    }

    renderInspector(null);
  }

  initializeRenderedTimeline();

  function initialState() {
    const node = document.getElementById("q1timeline-webview-state");
    if (!node || !node.dataset.state) {
      return {};
    }
    try {
      return JSON.parse(node.dataset.state);
    } catch (_error) {
      return {};
    }
  }

  const state = initialState();
  const initialUnsavedChanges = Boolean(state.hasUnsavedChanges);
  const initialUpdateMode = state.updateMode || "onSave";
  const initialViewMode = state.viewMode || "normal";
  const initialAlignmentPolicy = state.alignmentPolicy || "unknown";
  const initialSingleFileMode = Boolean(state.singleFileMode);
  let timelineTimeBasis = initialTimelineTimeBasis();
  let timelineZoom = 1;
  let timelineWindow = undefined;
  let timelineDrag = undefined;
  let timelineSelectionRange = undefined;
  let timelineSelectionDrag = undefined;
  const collapsedSequencers = new Set();
  const expandedQ1IssueSequencers = new Set(initialExpandedQ1IssueSequencers());
  const minInlineLabelWidth = 20;
  const inlineLabelXPadding = 2;
  const defaultLaneHeight = 34;
  const collapsedSequencerHeight = 22;
  const timelineDragThresholdPx = 3;

  function ensureAnalysisDetailsBody() {
    let body = document.getElementById("q1timeline-analysis-details-body");
    if (body) {
      return body;
    }
    let node = document.getElementById("q1timeline-analysis-details");
    if (!node) {
      node = document.createElement("details");
      node.id = "q1timeline-analysis-details";
      node.className = "analysis-details";
      node.open = false;
      const summary = document.createElement("summary");
      summary.textContent = "Analysis details";
      node.append(summary);
      const timelineRoot = document.getElementById("timeline-root");
      if (timelineRoot && typeof timelineRoot.insertAdjacentElement === "function") {
        timelineRoot.insertAdjacentElement("afterend", node);
      } else {
        document.body.append(node);
      }
    }
    body = document.createElement("div");
    body.id = "q1timeline-analysis-details-body";
    node.append(body);
    return body;
  }

  function analysisDetailsBody() {
    return ensureAnalysisDetailsBody();
  }

  function installAnalysisDetailsPersistence() {
    const details = document.getElementById("q1timeline-analysis-details");
    if (!details || details.dataset.analysisDetailsPersistence === "true") {
      return;
    }
    const persistedState = readPersistedWebviewState();
    if (Object.prototype.hasOwnProperty.call(persistedState, "analysisDetailsOpen")) {
      details.open = Boolean(persistedState.analysisDetailsOpen);
    }
    const summary = details.querySelector("summary");
    if (summary) {
      summary.addEventListener("click", (event) => {
        if (event && typeof event.stopPropagation === "function") {
          event.stopPropagation();
        }
      });
    }
    details.addEventListener("toggle", () => {
      writePersistedWebviewState({ analysisDetailsOpen: Boolean(details.open) });
    });
    details.dataset.analysisDetailsPersistence = "true";
  }

  function toolbarSvgIcon(name) {
    const icons = {
      refresh: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></svg>',
      sync: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 0 1-15.4 6.4"/><path d="M3 12A9 9 0 0 1 18.4 5.6"/><path d="M3 19v-5h5"/><path d="M21 5v5h-5"/></svg>',
      alignment: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16"/><path d="M7 12h10"/><path d="M4 17h16"/><path d="M12 4v16"/></svg>',
      files: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 4h8l4 4v12H8z"/><path d="M16 4v5h5"/><path d="M4 8h4v12"/></svg>',
      aligned: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16"/><path d="M4 17h16"/><path d="M8 4v16"/><path d="M16 4v16"/></svg>',
      local: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 8v5l3 2"/></svg>',
      normal: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/></svg>',
      debug: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 8h8v10H8z"/><path d="M9 4l2 4"/><path d="M15 4l-2 4"/><path d="M4 13h4"/><path d="M16 13h4"/><path d="M4 18h4"/><path d="M16 18h4"/></svg>',
      fit: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5"/><path d="M20 9V4h-5"/><path d="M4 15v5h5"/><path d="M20 15v5h-5"/></svg>',
      reset: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7v6h6"/><path d="M20 17a8 8 0 0 1-13.7-5.7L4 13"/></svg>',
      zoomOut: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m16 16 5 5"/><path d="M8 11h6"/></svg>',
      zoomIn: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m16 16 5 5"/><path d="M11 8v6"/><path d="M8 11h6"/></svg>',
      zoomSelection: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5" width="10" height="8" rx="1"/><circle cx="14" cy="14" r="4"/><path d="m17 17 3 3"/></svg>',
      zoomEvent: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10" cy="10" r="5"/><path d="M10 3v3"/><path d="M10 14v3"/><path d="M3 10h3"/><path d="M14 10h3"/><path d="m15 15 5 5"/></svg>',
      panLeft: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 6 8 12l6 6"/><path d="M9 12h11"/></svg>',
      panRight: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m10 6 6 6-6 6"/><path d="M4 12h11"/></svg>',
      axis: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 18h16"/><path d="M6 18V8"/><path d="M10 18v-4"/><path d="M14 18V6"/><path d="M18 18v-7"/></svg>',
    };
    return icons[name] || icons.axis;
  }

  function setIconOnlyToolbarButton(button, iconName, label) {
    button.classList.add("q1timeline-toolbar-button");
    button.setAttribute("aria-label", label);
    button.title = label;
    button.innerHTML = toolbarSvgIcon(iconName);
  }

  function setToolbarStatusIcon(node, iconName, label) {
    node.className = "q1timeline-toolbar-status";
    node.setAttribute("aria-label", label);
    node.title = label;
    node.innerHTML = toolbarSvgIcon(iconName);
  }

  function ensureToolbarNode() {
    let node = document.getElementById("q1timeline-toolbar");
    if (!node) {
      node = document.createElement("div");
      node.id = "q1timeline-toolbar";
      node.className = "q1timeline-toolbar";
      const refreshButton = document.createElement("button");
      refreshButton.id = "q1timeline-refresh-button";
      refreshButton.type = "button";
      setIconOnlyToolbarButton(refreshButton, "refresh", "Refresh");
      refreshButton.addEventListener("click", () => vscode.postMessage({ type: "requestRefresh" }));
      node.append(refreshButton);

      const autoUpdateIndicator = document.createElement("span");
      autoUpdateIndicator.id = "q1timeline-auto-update-indicator";
      setToolbarStatusIcon(autoUpdateIndicator, "sync", `Auto-update: ${initialUpdateMode}`);
      node.append(autoUpdateIndicator);

      const alignmentPolicy = document.createElement("span");
      alignmentPolicy.id = "q1timeline-alignment-policy";
      node.append(alignmentPolicy);

      const timeBasisGroup = document.createElement("span");
      timeBasisGroup.id = "q1timeline-time-basis-toggle";
      timeBasisGroup.className = "q1timeline-toolbar-group";
      for (const basis of ["aligned", "local"]) {
        const basisButton = document.createElement("button");
        basisButton.type = "button";
        basisButton.setAttribute("data-time-basis", basis);
        setIconOnlyToolbarButton(basisButton, basis === "aligned" ? "aligned" : "local", basis === "aligned" ? "Aligned time basis" : "Local time basis");
        basisButton.addEventListener("click", () => setTimelineTimeBasis(basis));
        timeBasisGroup.append(basisButton);
      }
      node.append(timeBasisGroup);

      const modeGroup = document.createElement("span");
      modeGroup.id = "q1timeline-mode-toggle";
      modeGroup.className = "q1timeline-toolbar-group";
      const normalButton = document.createElement("button");
      normalButton.id = "q1timeline-mode-normal";
      normalButton.type = "button";
      normalButton.dataset.mode = "normal";
      normalButton.setAttribute("data-mode", "normal");
      setIconOnlyToolbarButton(normalButton, "normal", "Normal view");
      normalButton.addEventListener("click", () => requestViewMode("normal"));
      modeGroup.append(normalButton);
      const debugButton = document.createElement("button");
      debugButton.id = "q1timeline-mode-debug";
      debugButton.type = "button";
      debugButton.dataset.mode = "debug";
      debugButton.setAttribute("data-mode", "debug");
      setIconOnlyToolbarButton(debugButton, "debug", "Debug view");
      debugButton.addEventListener("click", () => requestViewMode("debug"));
      modeGroup.append(debugButton);
      node.append(modeGroup);

      const fitButton = document.createElement("button");
      fitButton.id = "q1timeline-fit-button";
      fitButton.type = "button";
      setIconOnlyToolbarButton(fitButton, "fit", "Fit timeline");
      fitButton.addEventListener("click", fitTimelineToWindow);
      node.append(fitButton);

      const resetZoomButton = document.createElement("button");
      resetZoomButton.id = "q1timeline-reset-zoom-button";
      resetZoomButton.type = "button";
      setIconOnlyToolbarButton(resetZoomButton, "reset", "Reset zoom");
      resetZoomButton.addEventListener("click", resetTimelineZoom);
      node.append(resetZoomButton);

      const zoomOutButton = document.createElement("button");
      zoomOutButton.id = "q1timeline-zoom-out-button";
      zoomOutButton.type = "button";
      setIconOnlyToolbarButton(zoomOutButton, "zoomOut", "Zoom out");
      zoomOutButton.addEventListener("click", zoomTimelineOut);
      node.append(zoomOutButton);

      const zoomInButton = document.createElement("button");
      zoomInButton.id = "q1timeline-zoom-in-button";
      zoomInButton.type = "button";
      setIconOnlyToolbarButton(zoomInButton, "zoomIn", "Zoom in");
      zoomInButton.addEventListener("click", zoomTimelineIn);
      node.append(zoomInButton);

      const zoomSelectionButton = document.createElement("button");
      zoomSelectionButton.id = "q1timeline-zoom-selection-button";
      zoomSelectionButton.type = "button";
      setIconOnlyToolbarButton(zoomSelectionButton, "zoomSelection", "Zoom selection");
      zoomSelectionButton.addEventListener("click", zoomTimelineSelection);
      node.append(zoomSelectionButton);

      const zoomEventButton = document.createElement("button");
      zoomEventButton.id = "q1timeline-zoom-event-button";
      zoomEventButton.type = "button";
      setIconOnlyToolbarButton(zoomEventButton, "zoomEvent", "Zoom selected event");
      zoomEventButton.addEventListener("click", zoomTimelineToSelectedEvent);
      node.append(zoomEventButton);

      const panLeftButton = document.createElement("button");
      panLeftButton.id = "q1timeline-pan-left-button";
      panLeftButton.type = "button";
      setIconOnlyToolbarButton(panLeftButton, "panLeft", "Pan left");
      panLeftButton.addEventListener("click", () => panTimeline(-1));
      node.append(panLeftButton);

      const panRightButton = document.createElement("button");
      panRightButton.id = "q1timeline-pan-right-button";
      panRightButton.type = "button";
      setIconOnlyToolbarButton(panRightButton, "panRight", "Pan right");
      panRightButton.addEventListener("click", () => panTimeline(1));
      node.append(panRightButton);

      const timeWindow = document.createElement("span");
      timeWindow.id = "q1timeline-time-window";
      setToolbarStatusIcon(timeWindow, "axis", "Timeline window");
      node.append(timeWindow);

      const timelineRoot = document.getElementById("timeline-root");
      if (timelineRoot && timelineRoot.parentElement) {
        timelineRoot.parentElement.insertBefore(node, timelineRoot);
      } else {
        analysisDetailsBody().append(node);
      }
    }
    return node;
  }

  function ensureRelatedBlockSummaryNode() {
    let node = document.getElementById("q1timeline-related-block-summary");
    if (!node) {
      node = document.createElement("div");
      node.id = "q1timeline-related-block-summary";
      node.style.cssText = "padding:4px 8px;font:12px sans-serif;color:var(--vscode-foreground);border-bottom:1px solid var(--vscode-panel-border);";
      analysisDetailsBody().append(node);
    }
    return node;
  }

  function ensureUnsavedChangesNode() {
    let node = document.getElementById("q1timeline-unsaved-banner");
    if (!node) {
      node = document.createElement("div");
      node.id = "q1timeline-unsaved-banner";
      node.style.cssText = "padding:4px 8px;font:12px sans-serif;background:var(--vscode-inputValidation-warningBackground);color:var(--vscode-inputValidation-warningForeground);border-bottom:1px solid var(--vscode-inputValidation-warningBorder);";
      analysisDetailsBody().append(node);
    }
    return node;
  }

  function requestViewMode(mode) {
    const currentMode = mode === "debug" ? "debug" : "normal";
    renderModeToggle(currentMode);
    vscode.postMessage({ type: "setViewMode", mode: currentMode });
  }

  function styleSegmentedToggleButton(button, active) {
    button.style.border = "none";
    button.style.background = active ? "rgba(127, 127, 127, 0.18)" : "transparent";
    button.style.color = active
      ? "var(--vscode-foreground, #17202a)"
      : "var(--vscode-descriptionForeground, var(--vscode-foreground, #17202a))";
    button.style.fontWeight = active ? "700" : "400";
    button.style.boxShadow = active ? "inset 0 -2px 0 currentColor" : "none";
    button.style.opacity = button.disabled ? "0.45" : active ? "1" : "0.78";
  }

  function renderModeToggle(mode) {
    ensureToolbarNode();
    const currentMode = mode === "debug" ? "debug" : "normal";
    toggleClass(document.body, "mode-debug", currentMode === "debug");
    toggleClass(document.body, "mode-normal", currentMode === "normal");
    document.body.dataset.mode = currentMode;
    for (const button of document.querySelectorAll("button[data-mode]")) {
      const active = button.dataset.mode === currentMode;
      button.disabled = false;
      button.dataset.active = String(active);
      button.setAttribute("aria-pressed", String(active));
      styleSegmentedToggleButton(button, active);
    }
  }

  function renderAlignmentPolicy(policy) {
    const node = document.getElementById("q1timeline-alignment-policy");
    if (!node) {
      return;
    }
    if (initialSingleFileMode) {
      node.dataset.annotationScope = "selected-files";
      setToolbarStatusIcon(node, "files", "Temporary Q1ASM selection");
      return;
    }
    setToolbarStatusIcon(node, "alignment", "Alignment: " + (policy || "unknown"));
  }

  function renderTimeBasisToggle() {
    for (const button of document.querySelectorAll("[data-time-basis]")) {
      const basis = button.getAttribute("data-time-basis");
      const enabled = basis === "local" || hasAlignedTimelineTimes();
      const active = basis === timelineTimeBasis;
      button.disabled = !enabled;
      button.dataset.active = String(active);
      button.setAttribute("aria-pressed", String(active));
      styleSegmentedToggleButton(button, active);
    }
  }

  function setTimelineNodeHidden(node, hidden) {
    node.dataset.timelineHidden = String(hidden);
    if (isQ1IssueLaneNode(node)) {
      toggleClass(node, "q1-issue-expanded", !hidden);
    }
    if (hidden) {
      node.setAttribute("hidden", "");
    } else if (typeof node.removeAttribute === "function") {
      node.removeAttribute("hidden");
    } else if (node.attributes) {
      delete node.attributes.hidden;
    }
    node.style.display = hidden ? "none" : "";
  }

  function hasHiddenAttribute(node) {
    if (typeof node.hasAttribute === "function") {
      return node.hasAttribute("hidden");
    }
    const value = typeof node.getAttribute === "function" ? node.getAttribute("hidden") : undefined;
    return value !== undefined && value !== null;
  }

  function isTimelineNodeHidden(node) {
    return node.dataset.timelineHidden === "true" || hasHiddenAttribute(node) || node.style.display === "none";
  }

  function normalizeSequencerId(value) {
    return String(value || "").replace(/^sequencer:/, "");
  }

  function laneBelongsToSequencer(laneId, sequencer) {
    const rawLaneId = String(laneId || "");
    const normalizedSequencer = normalizeSequencerId(sequencer);
    return (
      rawLaneId === normalizedSequencer ||
      rawLaneId === `sequencer:${normalizedSequencer}` ||
      rawLaneId.startsWith(`${normalizedSequencer} / `) ||
      rawLaneId.startsWith(`sequencer:${normalizedSequencer}:`)
    );
  }

  function isQ1IssueLaneNode(laneNode) {
    if (!laneNode) {
      return false;
    }
    if (laneNode.dataset && laneNode.dataset.laneRole === "q1-issue") {
      return true;
    }
    return Boolean(laneNode.classList && laneNode.classList.contains("q1-issue-lane"));
  }

  function q1IssueLaneParent(laneNode) {
    if (laneNode && laneNode.dataset && laneNode.dataset.parentLane) {
      return normalizeSequencerId(laneNode.dataset.parentLane);
    }
    return normalizeSequencerId(timelineSequencerFromLane(laneNode && laneNode.dataset ? laneNode.dataset.lane : undefined));
  }

  function q1IssueLaneNodes(sequencer) {
    return Array.from(document.querySelectorAll('[data-lane-role="q1-issue"], .q1-issue-lane')).filter(
      (laneNode) => q1IssueLaneParent(laneNode) === sequencer
    );
  }

  function sequencerHasQ1IssueLane(sequencer) {
    return q1IssueLaneNodes(sequencer).length > 0;
  }

  function sequencerLaneNodes(sequencer) {
    return Array.from(document.querySelectorAll("[data-lane]")).filter(
      (laneNode) => laneBelongsToSequencer(laneNode.dataset.lane, sequencer)
    );
  }

  function sequencerCollapsed(sequencer) {
    return collapsedSequencers.has(sequencer);
  }

  function sequencerQ1IssueExpanded(sequencer) {
    return expandedQ1IssueSequencers.has(sequencer);
  }

  function sequencerQ1IssueVisible(sequencer) {
    return q1IssueLaneNodes(sequencer).some((laneNode) => !isTimelineNodeHidden(laneNode));
  }

  function setSequencerCollapsed(sequencer, collapsed) {
    if (collapsed) {
      collapsedSequencers.add(sequencer);
    } else {
      collapsedSequencers.delete(sequencer);
    }
    applyTimelineScale();
    updateLaneDependentVisibility();
    syncSequencerFoldControls();
    syncSharedSequencerFoldControls();
  }

  function setSequencerQ1IssueExpanded(sequencer, expanded) {
    if (expanded) {
      expandedQ1IssueSequencers.add(sequencer);
    } else {
      expandedQ1IssueSequencers.delete(sequencer);
    }
    writeQ1IssueExpansionState();
    applyTimelineScale();
    updateLaneDependentVisibility();
    syncSequencerFoldControls();
    syncSharedSequencerFoldControls();
  }

  function toggleSequencerFoldControl(sequencer) {
    if (sequencerHasQ1IssueLane(sequencer)) {
      setSequencerQ1IssueExpanded(sequencer, !sequencerQ1IssueVisible(sequencer));
      return;
    }
    setSequencerCollapsed(sequencer, !sequencerCollapsed(sequencer));
  }

  function laneNodeHidden(laneId) {
    return Array.from(document.querySelectorAll("[data-lane]")).some(
      (laneNode) => laneNode.dataset.lane === laneId && isTimelineNodeHidden(laneNode)
    );
  }

  function updateLaneDependentVisibility() {
    for (const labelNode of document.querySelectorAll(".sequencer-label[data-sequencer-label]")) {
      const sequencer = labelNode.dataset.sequencerLabel;
      const sequencerLanes = sequencerLaneNodes(sequencer);
      setTimelineNodeHidden(
        labelNode,
        sequencerLanes.length > 0 && sequencerLanes.every((laneNode) => isTimelineNodeHidden(laneNode)) && !sequencerCollapsed(sequencer)
      );
    }
    for (const flowNode of document.querySelectorAll(".feedback-flow-group")) {
      const fromLaneHidden = laneNodeHidden(flowNode.dataset.fromLane);
      const toLaneHidden = laneNodeHidden(flowNode.dataset.toLane);
      setTimelineNodeHidden(flowNode, fromLaneHidden || toLaneHidden);
    }
  }

  function parentSvg(node) {
    let current = node ? node.parentElement : undefined;
    while (current) {
      if (String(current.tagName || "").toLowerCase() === "svg") {
        return current;
      }
      current = current.parentElement;
    }
    return undefined;
  }

  function removeSequencerFoldControls() {
    for (const control of document.querySelectorAll(".sequencer-fold-control")) {
      if (typeof control.remove === "function") {
        control.remove();
      } else if (control.parentElement) {
        control.parentElement.children = Array.from(control.parentElement.children).filter((child) => child !== control);
      }
    }
  }

  function syncSequencerFoldControls() {
    for (const control of document.querySelectorAll(".sequencer-fold-control")) {
      const button = control.querySelector("button");
      const sequencer = control.dataset.sequencerControl;
      if (button) {
        const q1IssueDisclosure = sequencerHasQ1IssueLane(sequencer);
        const expanded = q1IssueDisclosure ? sequencerQ1IssueVisible(sequencer) : !sequencerCollapsed(sequencer);
        button.textContent = expanded ? "▾" : "▸";
        button.setAttribute("aria-expanded", String(expanded));
        button.setAttribute(
          "aria-label",
          q1IssueDisclosure
            ? `${expanded ? "Hide" : "Show"} Q1 issue for ${sequencer}`
            : `${expanded ? "Collapse" : "Expand"} ${sequencer}`,
        );
        button.title = button.getAttribute("aria-label");
      }
    }
  }

  function stopSequencerControlEvent(event) {
    if (event && typeof event.stopPropagation === "function") {
      event.stopPropagation();
    }
  }

  function renderSequencerFoldControls() {
    removeSequencerFoldControls();
    updateLaneDependentVisibility();
    for (const labelNode of document.querySelectorAll(".sequencer-label[data-sequencer-label]")) {
      const svg = parentSvg(labelNode);
      const sequencer = labelNode.dataset.sequencerLabel;
      if (!svg || !sequencer) {
        continue;
      }
      const labelX = Number(labelNode.getAttribute("x"));
      const labelY = Number(labelNode.getAttribute("y"));
      const control = document.createElementNS("http://www.w3.org/2000/svg", "foreignObject");
      control.setAttribute("class", "sequencer-fold-control");
      control.setAttribute("x", formatCoordinate((Number.isFinite(labelX) ? labelX : 40) - 24));
      control.setAttribute("y", formatCoordinate((Number.isFinite(labelY) ? labelY : 64) - 13));
      control.setAttribute("width", "18");
      control.setAttribute("height", "18");
      control.dataset.sequencerControl = sequencer;
      control.setAttribute("data-sequencer-control", sequencer);
      const button = document.createElementNS("http://www.w3.org/1999/xhtml", "button");
      button.type = "button";
      button.style.cssText = "width:18px;height:18px;margin:0;padding:0;border:0;background:transparent;color:var(--vscode-foreground, #17202a);font:14px sans-serif;line-height:18px;cursor:pointer;";
      button.addEventListener("click", (event) => {
        stopSequencerControlEvent(event);
        toggleSequencerFoldControl(sequencer);
      });
      button.addEventListener("pointerdown", stopSequencerControlEvent);
      control.append(button);
      if (typeof svg.insertBefore === "function") {
        svg.insertBefore(control, labelNode);
      } else {
        svg.append(control);
      }
    }
    syncSequencerFoldControls();
  }

  function timelineSvg() {
    return document.querySelector(".timeline-svg");
  }

  function timelineGeometry(svg) {
    const viewBox = svg ? String(svg.getAttribute("viewBox") || "").trim().split(/\s+/) : [];
    const viewBoxWidth = Number(viewBox[2]);
    const left = Number(svg && svg.dataset ? svg.dataset.plotLeft : undefined);
    const right = Number(svg && svg.dataset ? svg.dataset.plotRight : undefined);
    const basisMin = Number(svg && svg.dataset ? svg.dataset[`${timelineTimeBasis}TimeMin`] : undefined);
    const basisMax = Number(svg && svg.dataset ? svg.dataset[`${timelineTimeBasis}TimeMax`] : undefined);
    const fallbackMin = Number(svg && svg.dataset ? svg.dataset.timeMin : undefined);
    const fallbackMax = Number(svg && svg.dataset ? svg.dataset.timeMax : undefined);
    const extent = timelineEventExtent();
    const fallbackRight = Number.isFinite(viewBoxWidth) && viewBoxWidth > 0 ? viewBoxWidth - 24 : 1076;
    return {
      left: Number.isFinite(left) ? left : 190,
      right: Number.isFinite(right) ? right : fallbackRight,
      min: Number.isFinite(basisMin) ? basisMin : Number.isFinite(fallbackMin) ? fallbackMin : extent.min,
      max: Number.isFinite(basisMax) ? basisMax : Number.isFinite(fallbackMax) ? fallbackMax : extent.max,
    };
  }

  function timelineEventExtent() {
    let min = 0;
    let max = 1;
    let found = false;
    for (const event of timelineEventsById.values()) {
      for (const key of ["t0", "t1"]) {
        const value = eventTime(event, key);
        if (value === undefined) {
          continue;
        }
        if (!found) {
          min = value;
          max = value;
          found = true;
        } else {
          min = Math.min(min, value);
          max = Math.max(max, value);
        }
      }
    }
    if (max <= min) {
      max = min + 1;
    }
    return { min, max };
  }

  function concreteTime(value) {
    if (value && value.kind === "concrete" && Number.isFinite(Number(value.value))) {
      return Number(value.value);
    }
    return undefined;
  }

  function eventTime(event, edge) {
    if (!event || typeof event !== "object") {
      return undefined;
    }
    if (timelineTimeBasis === "aligned") {
      const meta = event.meta && typeof event.meta === "object" ? event.meta : {};
      const aligned = Number(meta[`aligned_${edge}`]);
      if (Number.isFinite(aligned)) {
        return aligned;
      }
    }
    return concreteTime(event[edge]);
  }

  function hasAlignedTimelineTimes() {
    for (const event of timelineEventsById.values()) {
      const meta = event && event.meta && typeof event.meta === "object" ? event.meta : {};
      if (Number.isFinite(Number(meta.aligned_t0)) || Number.isFinite(Number(meta.aligned_t1))) {
        return true;
      }
    }
    return false;
  }

  function initialTimelineTimeBasis() {
    const persistedState = readPersistedWebviewState();
    const savedBasis = persistedState && persistedState.timelineTimeBasis;
    if (savedBasis === "local") {
      return "local";
    }
    if (savedBasis === "aligned" && hasAlignedTimelineTimes()) {
      return "aligned";
    }
    return hasAlignedTimelineTimes() ? "aligned" : "local";
  }

  function fullTimelineWindow() {
    const svg = timelineSvg();
    const geometry = timelineGeometry(svg);
    return { min: geometry.min, max: geometry.max };
  }

  function currentTimelineWindow() {
    if (!timelineWindow) {
      const full = fullTimelineWindow();
      timelineWindow = persistedTimelineWindowFor(full) || full;
    }
    return timelineWindow;
  }

  function timelineExtentMatches(left, right) {
    return (
      Number.isFinite(Number(left && left.min)) &&
      Number.isFinite(Number(left && left.max)) &&
      Number.isFinite(Number(right && right.min)) &&
      Number.isFinite(Number(right && right.max)) &&
      Math.abs(Number(left.min) - Number(right.min)) < 0.001 &&
      Math.abs(Number(left.max) - Number(right.max)) < 0.001
    );
  }

  function persistedTimelineWindowFor(full) {
    const state = readPersistedWebviewState();
    const saved = state && state.timelineWindow ? state.timelineWindow : undefined;
    if (
      !saved ||
      !Number.isFinite(Number(saved.min)) ||
      !Number.isFinite(Number(saved.max)) ||
      Number(saved.max) <= Number(saved.min)
    ) {
      return undefined;
    }
    const savedFull = { min: Number(saved.fullMin), max: Number(saved.fullMax) };
    if (saved.basis && saved.basis !== timelineTimeBasis) {
      return undefined;
    }
    if (!timelineExtentMatches(savedFull, full)) {
      return undefined;
    }
    return { min: Number(saved.min), max: Number(saved.max) };
  }

  function writeTimelineWindowState(window) {
    const full = fullTimelineWindow();
    writePersistedWebviewState({
      timelineWindow: {
        min: window.min,
        max: window.max,
        fullMin: full.min,
        fullMax: full.max,
        basis: timelineTimeBasis,
      },
    });
  }

  function formatCoordinate(value) {
    return Number(value).toFixed(2);
  }

  function snapTimelineNs(value) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.round(number) : 0;
  }

  function formatWindowValue(value) {
    return String(snapTimelineNs(value));
  }

  function scaleTimelineTime(time, window, geometry) {
    const span = Math.max(1, window.max - window.min);
    return geometry.left + ((time - window.min) / span) * (geometry.right - geometry.left);
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function baseDataKey(attributeName) {
    return `base${attributeName.charAt(0).toUpperCase()}${attributeName.slice(1)}`;
  }

  function numericAttribute(node, attributeName) {
    const value = Number(node ? node.getAttribute(attributeName) : undefined);
    return Number.isFinite(value) ? value : undefined;
  }

  function rememberBaseAttribute(node, attributeName) {
    const key = baseDataKey(attributeName);
    if (!node || node.dataset[key] !== undefined) {
      return;
    }
    const value = numericAttribute(node, attributeName);
    if (value !== undefined) {
      node.dataset[key] = String(value);
    }
  }

  function applyShiftedAttribute(node, attributeName, delta) {
    const key = baseDataKey(attributeName);
    const value = Number(node && node.dataset ? node.dataset[key] : undefined);
    if (Number.isFinite(value)) {
      node.setAttribute(attributeName, formatCoordinate(value + delta));
    }
  }

  function inferLaneY(laneNode, fallbackY) {
    const explicitY = Number(laneNode && laneNode.dataset ? laneNode.dataset.laneY : undefined);
    if (Number.isFinite(explicitY)) {
      return explicitY;
    }
    const laneRule = laneNode.querySelector(".lane-rule");
    const ruleY = numericAttribute(laneRule, "y1");
    if (ruleY !== undefined) {
      return ruleY - 10;
    }
    const rect = laneNode.querySelector("rect");
    const rectY = numericAttribute(rect, "y");
    if (rectY !== undefined) {
      return rectY;
    }
    return fallbackY;
  }

  function timelineLaneNodes(svg) {
    return Array.from(svg.querySelectorAll("[data-lane]"));
  }

  function timelineSequencerFromLane(laneId) {
    return String(laneId || "").split(" / ", 1)[0];
  }

  function initializeTimelineLayout(svg) {
    if (!svg || svg.dataset.timelineLayoutInitialized === "true") {
      return;
    }
    const viewBox = String(svg.getAttribute("viewBox") || "").trim().split(/\s+/);
    const viewBoxHeight = Number(viewBox[3]);
    if (Number.isFinite(viewBoxHeight)) {
      svg.dataset.baseViewBoxHeight = String(viewBoxHeight);
    }
    const laneNodes = timelineLaneNodes(svg);
    const inferredYs = laneNodes.map((laneNode, index) => inferLaneY(laneNode, 72 + index * defaultLaneHeight));
    const deltas = inferredYs
      .slice(1)
      .map((value, index) => value - inferredYs[index])
      .filter((value) => Number.isFinite(value) && value > 0);
    const laneHeight = deltas.length ? Math.min(...deltas) : defaultLaneHeight;
    svg.dataset.layoutLaneHeight = String(laneHeight);
    svg.dataset.layoutLaneTop = String(inferredYs.length ? inferredYs[0] : 72);
    laneNodes.forEach((laneNode, index) => {
      laneNode.dataset.baseLaneY = String(inferredYs[index]);
      for (const node of laneNode.querySelectorAll("*")) {
        rememberBaseAttribute(node, "y");
        rememberBaseAttribute(node, "y1");
        rememberBaseAttribute(node, "y2");
      }
    });
    for (const labelNode of svg.querySelectorAll(".sequencer-label[data-sequencer-label]")) {
      rememberBaseAttribute(labelNode, "y");
    }
    svg.dataset.timelineLayoutInitialized = "true";
  }

  function setTimelineSvgHeight(svg, height) {
    const viewBox = String(svg.getAttribute("viewBox") || "").trim().split(/\s+/);
    const width = Number(viewBox[2]);
    const nextHeight = Math.max(120, height);
    if (Number.isFinite(width)) {
      svg.setAttribute("viewBox", `${viewBox[0] || 0} ${viewBox[1] || 0} ${width} ${formatCoordinate(nextHeight)}`);
    }
    const cursor = document.getElementById("time-cursor");
    if (cursor) {
      cursor.setAttribute("y2", formatCoordinate(nextHeight - 20));
    }
  }

  function shiftLaneTo(laneNode, nextY) {
    const baseY = Number(laneNode.dataset.baseLaneY);
    const delta = Number.isFinite(baseY) ? nextY - baseY : 0;
    for (const node of laneNode.querySelectorAll("*")) {
      applyShiftedAttribute(node, "y", delta);
      applyShiftedAttribute(node, "y1", delta);
      applyShiftedAttribute(node, "y2", delta);
    }
  }

  function sequencerLabelNode(sequencer) {
    return Array.from(document.querySelectorAll(".sequencer-label[data-sequencer-label]")).find(
      (labelNode) => labelNode.dataset.sequencerLabel === sequencer
    );
  }

  function sequencerFoldControlNode(sequencer) {
    return Array.from(document.querySelectorAll(".sequencer-fold-control")).find(
      (control) => control.dataset.sequencerControl === sequencer
    );
  }

  function positionSequencerHeader(sequencer, laneY) {
    const labelNode = sequencerLabelNode(sequencer);
    if (labelNode) {
      setTimelineNodeHidden(labelNode, false);
      labelNode.setAttribute("y", formatCoordinate(laneY + 15));
    }
    const control = sequencerFoldControlNode(sequencer);
    if (control) {
      const labelX = Number(labelNode ? labelNode.getAttribute("x") : undefined);
      control.setAttribute("x", formatCoordinate((Number.isFinite(labelX) ? labelX : 40) - 24));
      control.setAttribute("y", formatCoordinate(laneY + 2));
    }
  }

  function reflowTimelineLanes(svg) {
    if (!svg) {
      return;
    }
    initializeTimelineLayout(svg);
    const laneNodes = timelineLaneNodes(svg);
    const laneHeight = Number(svg.dataset.layoutLaneHeight) || defaultLaneHeight;
    const laneTop = Number(svg.dataset.layoutLaneTop) || 72;
    let nextY = laneTop;
    let currentSequencer = undefined;
    for (const laneNode of laneNodes) {
      const sequencer = timelineSequencerFromLane(laneNode.dataset.lane);
      const firstForSequencer = sequencer !== currentSequencer;
      if (firstForSequencer) {
        currentSequencer = sequencer;
        positionSequencerHeader(sequencer, nextY);
      }
      if (isQ1IssueLaneNode(laneNode) && !sequencerQ1IssueExpanded(sequencer)) {
        setTimelineNodeHidden(laneNode, true);
        continue;
      }
      if (!sequencerHasQ1IssueLane(sequencer) && sequencerCollapsed(sequencer)) {
        setTimelineNodeHidden(laneNode, true);
        if (firstForSequencer) {
          nextY += collapsedSequencerHeight;
        }
        continue;
      }
      setTimelineNodeHidden(laneNode, false);
      shiftLaneTo(laneNode, nextY);
      nextY += laneHeight;
    }
    setTimelineSvgHeight(svg, nextY + 40);
  }

  function applyTimelineScale() {
    const svg = timelineSvg();
    if (!svg) {
      if (sharedTimelineActive) {
        applySharedTimelineScale();
      }
      return;
    }
    const geometry = timelineGeometry(svg);
    const window = currentTimelineWindow();
    const fullSpan = Math.max(1, geometry.max - geometry.min);
    const span = Math.max(1, window.max - window.min);
    timelineZoom = fullSpan / span;
    svg.style.minWidth = "";
    svg.style.maxWidth = "";
    svg.style.width = "";
    svg.dataset.zoom = String(timelineZoom);
    svg.dataset.timeWindowMin = formatWindowValue(window.min);
    svg.dataset.timeWindowMax = formatWindowValue(window.max);
    svg.dataset.timeBasis = timelineTimeBasis;
    svg.dataset.timeMin = formatWindowValue(geometry.min);
    svg.dataset.timeMax = formatWindowValue(geometry.max);
    renderTimelineWindowStatus(window);
    renderTimeBasisToggle();
    reflowTimelineLanes(svg);
    updateTimelineSelectionOverlay(svg, window, geometry);
    updateTimelineEvents(svg, window, geometry);
    updateLaneDependentVisibility();
    updateFeedbackFlows(svg, window, geometry);
    suppressOverlappingOverlayLabels(svg);
    updateTimelineTicks(svg, window, geometry);
    syncSequencerFoldControls();
  }

  function applySharedTimelineScale() {
    const window = currentTimelineWindow();
    const full = fullTimelineWindow();
    const fullSpan = Math.max(1, full.max - full.min);
    const span = Math.max(1, window.max - window.min);
    timelineZoom = fullSpan / span;
    renderTimelineWindowStatus(window);
    renderTimeBasisToggle();
    renderSharedTimelineIfAvailable(currentTimelineMode(), { preserveSelection: true });
  }

  function renderTimelineWindowStatus(window) {
    const node = document.getElementById("q1timeline-time-window");
    if (node) {
      setToolbarStatusIcon(node, "axis", `X (${timelineTimeBasis}): ${formatWindowValue(window.min)}..${formatWindowValue(window.max)} ns`);
    }
    syncTimelineZoomActionState();
  }

  function syncTimelineZoomActionState() {
    const selectionButton = document.getElementById("q1timeline-zoom-selection-button");
    if (selectionButton) {
      const range = normalizedTimelineSelectionRange();
      selectionButton.disabled = !range || range.end <= range.start;
      selectionButton.title = range && range.end > range.start
        ? `Zoom ${formatWindowValue(range.start)}..${formatWindowValue(range.end)} ns`
        : "Shift-drag a timeline range to enable";
      selectionButton.setAttribute("aria-label", selectionButton.title);
    }
    const eventButton = document.getElementById("q1timeline-zoom-event-button");
    if (eventButton) {
      const event = selectedTimelineEventId ? timelineEventsById.get(String(selectedTimelineEventId)) : undefined;
      eventButton.disabled = !event;
      eventButton.title = event ? "Zoom around selected event" : "Select an event to enable";
      eventButton.setAttribute("aria-label", eventButton.title);
    }
  }

  function updateTimelineSelectionOverlay(svg, window, geometry) {
    let overlay = document.getElementById("q1timeline-selection-overlay");
    const range = normalizedTimelineSelectionRange();
    if (!range || range.end <= range.start) {
      if (overlay) {
        overlay.remove();
      }
      return;
    }
    const start = Math.max(window.min, Math.min(window.max, range.start));
    const end = Math.max(window.min, Math.min(window.max, range.end));
    const left = Math.min(start, end);
    const right = Math.max(start, end);
    if (right <= left) {
      if (overlay) {
        overlay.remove();
      }
      return;
    }
    if (!overlay) {
      overlay = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      overlay.id = "q1timeline-selection-overlay";
      overlay.setAttribute("class", "timeline-selection-overlay");
      const firstLane = svg.querySelector(".lane");
      if (firstLane && typeof svg.insertBefore === "function") {
        svg.insertBefore(overlay, firstLane);
      } else {
        svg.append(overlay);
      }
    }
    const x0 = scaleTimelineTime(left, window, geometry);
    const x1 = scaleTimelineTime(right, window, geometry);
    overlay.setAttribute("x", formatCoordinate(Math.min(x0, x1)));
    overlay.setAttribute("y", "40");
    overlay.setAttribute("width", formatCoordinate(Math.max(1, Math.abs(x1 - x0))));
    overlay.setAttribute("height", formatCoordinate(Math.max(1, timelineSvgViewBoxHeight(svg) - 60)));
  }

  function updateTimelineEvents(svg, window, geometry) {
    for (const eventNode of svg.querySelectorAll("[data-event-id]")) {
      const event = timelineEventsById.get(String(eventNode.dataset.eventId));
      if (!event) {
        continue;
      }
      const t0 = eventTime(event, "t0");
      const t1 = eventTime(event, "t1") ?? t0;
      if (t0 === undefined) {
        continue;
      }
      const x0 = scaleTimelineTime(t0, window, geometry);
      const x1 = scaleTimelineTime(t1, window, geometry);
      const width = Math.max(6, x1 - x0);
      if (eventNode.classList.contains("loop-bracket")) {
        updateLoopBracket(eventNode, x0, x1);
        continue;
      }
      if (eventNode.classList.contains("branch-marker")) {
        updateBranchMarker(eventNode, x0, x1);
        continue;
      }
      const rect = eventNode.querySelector("rect");
      if (rect) {
        rect.setAttribute("x", formatCoordinate(x0));
        rect.setAttribute("width", formatCoordinate(width));
      }
      updateEventLabel(eventNode, event, rect, x0, width);
      updateEventDiagnosticBadge(eventNode, rect, x0, width);
      eventNode.dataset.t0X = formatCoordinate(x0);
    }
    suppressOverlappingEventLabels(svg);
  }

  function updateLoopBracket(eventNode, xStart, xEnd) {
    const isStart = eventNode.dataset.loopBracketEdge !== "end";
    const anchorX = isStart ? xStart : xEnd;
    const hitbox = eventNode.querySelector(".loop-bracket-hitbox");
    const stem = eventNode.querySelector(".loop-bracket-stem");
    const capTop = eventNode.querySelector(".loop-bracket-cap-top");
    const capBottom = eventNode.querySelector(".loop-bracket-cap-bottom");
    const label = eventNode.querySelector(isStart ? ".loop-bracket-id" : ".loop-bracket-count");
    const guide = eventNode.querySelector(".loop-bracket-guide");
    const cap = 9;
    if (guide) {
      guide.setAttribute("x1", formatCoordinate(xStart));
      guide.setAttribute("x2", formatCoordinate(xEnd));
    }
    if (hitbox) {
      hitbox.setAttribute("x", formatCoordinate(isStart ? xStart - 10 : xEnd - 20));
    }
    if (stem) {
      stem.setAttribute("x1", formatCoordinate(anchorX));
      stem.setAttribute("x2", formatCoordinate(anchorX));
    }
    if (capTop) {
      capTop.setAttribute("x1", formatCoordinate(anchorX));
      capTop.setAttribute("x2", formatCoordinate(anchorX + (isStart ? cap : -cap)));
    }
    if (capBottom) {
      capBottom.setAttribute("x1", formatCoordinate(anchorX));
      capBottom.setAttribute("x2", formatCoordinate(anchorX + (isStart ? cap : -cap)));
    }
    if (label) {
      label.setAttribute("x", formatCoordinate(anchorX + (isStart ? 12 : 4)));
    }
    updateTransformedBadge(eventNode, anchorX + 10, loopBracketBadgeY(eventNode, stem));
    eventNode.dataset.t0X = formatCoordinate(anchorX);
  }

  function loopBracketBadgeY(eventNode, stem) {
    const stemY = numericAttribute(stem, "y1");
    if (stemY !== undefined) {
      return stemY - 9;
    }
    const badgeY = Number(eventNode.dataset.loopBracketBadgeY);
    return Number.isFinite(badgeY) ? badgeY : 0;
  }

  function updateBranchMarker(eventNode, xStart, xEnd) {
    const hitbox = eventNode.querySelector(".branch-marker-hitbox");
    const guide = eventNode.querySelector(".branch-marker-guide");
    const diamond = eventNode.querySelector(".branch-marker-diamond");
    const label = eventNode.querySelector(".branch-marker-condition");
    const centerY = branchMarkerCenterY(eventNode, hitbox);
    const guideY = branchMarkerGuideY(eventNode, guide, hitbox);
    const diamondRadius = 6;
    if (guide) {
      guide.setAttribute("x1", formatCoordinate(xStart));
      guide.setAttribute("x2", formatCoordinate(xEnd));
      guide.setAttribute("y1", formatCoordinate(guideY));
      guide.setAttribute("y2", formatCoordinate(guideY));
    }
    if (hitbox) {
      hitbox.setAttribute("x", formatCoordinate(xStart - 14));
    }
    if (diamond) {
      diamond.setAttribute(
        "points",
        [
          `${formatCoordinate(xStart)},${formatCoordinate(centerY - diamondRadius)}`,
          `${formatCoordinate(xStart + diamondRadius)},${formatCoordinate(centerY)}`,
          `${formatCoordinate(xStart)},${formatCoordinate(centerY + diamondRadius)}`,
          `${formatCoordinate(xStart - diamondRadius)},${formatCoordinate(centerY)}`,
        ].join(" ")
      );
    }
    if (label) {
      label.setAttribute("x", formatCoordinate(xStart + 12));
      label.setAttribute("y", formatCoordinate(centerY + 4));
    }
    updateTransformedBadge(eventNode, xStart + 10, centerY - 8);
    eventNode.dataset.t0X = formatCoordinate(xStart);
  }

  function branchMarkerCenterY(eventNode, hitbox) {
    const hitboxY = numericAttribute(hitbox, "y");
    if (hitboxY !== undefined) {
      return hitboxY + 20;
    }
    const centerY = Number(eventNode.dataset.branchMarkerCenterY);
    return Number.isFinite(centerY) ? centerY : 0;
  }

  function branchMarkerGuideY(eventNode, guide, hitbox) {
    const guideY = numericAttribute(guide, "y1");
    if (guideY !== undefined) {
      return guideY;
    }
    const hitboxY = numericAttribute(hitbox, "y");
    if (hitboxY !== undefined) {
      return hitboxY + 37;
    }
    const fallback = Number(eventNode.dataset.branchMarkerGuideY);
    return Number.isFinite(fallback) ? fallback : 0;
  }

  function updateTransformedBadge(eventNode, x, y) {
    const badge = eventNode.querySelector(".diagnostic-badge");
    if (badge) {
      badge.setAttribute("transform", `translate(${formatCoordinate(x)},${formatCoordinate(y)})`);
    }
  }

  function updateEventDiagnosticBadge(eventNode, rect, x0, width) {
    if (!eventNode.classList.contains("event")) {
      return;
    }
    const y = Number(rect ? rect.getAttribute("y") : undefined);
    const badgeX = x0 + Math.max(8, width - 8);
    const badgeY = (Number.isFinite(y) ? y : 0) + 4;
    updateTransformedBadge(eventNode, badgeX, badgeY);
  }

  function updateEventLabel(eventNode, event, rect, x0, width) {
    let label = eventNode.querySelector("text");
    const text = eventInlineLabel(event, width);
    const shouldShow = Boolean(text) && width >= minInlineLabelWidth && estimateLabelWidth(text) <= width;
    if (!shouldShow) {
      if (label) {
        label.style.display = "none";
      }
      eventNode.classList.add("lod-small", "label-hidden");
      return;
    }
    if (!label) {
      label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("class", "event-label");
      eventNode.append(label);
    }
    const y = Number(rect ? rect.getAttribute("y") : undefined);
    label.textContent = text;
    label.style.display = "";
    label.setAttribute("x", formatCoordinate(x0 + inlineLabelXPadding));
    label.setAttribute("y", formatCoordinate((Number.isFinite(y) ? y : 0) + 15));
    eventNode.classList.remove("lod-small", "label-hidden");
  }

  function suppressOverlappingEventLabels(svg) {
    const occupied = [];
    const labels = Array.from(svg.querySelectorAll(".event-label"))
      .filter((label) => label.style.display !== "none")
      .map((label, index) => {
        const eventNode = label.closest(".event");
        const event = eventNode ? timelineEventsById.get(String(eventNode.dataset.eventId)) : undefined;
        return { label, eventNode, event, index, rect: labelRect(label) };
      })
      .filter(({ label, eventNode }) => labelCanReserveSpace(label, eventNode))
      .sort((a, b) => eventLabelPriority(b.event) - eventLabelPriority(a.event) || a.index - b.index);

    for (const item of labels) {
      if (!item.rect || occupied.some((rect) => rectsOverlap(item.rect, rect))) {
        item.label.style.display = "none";
        if (item.eventNode) {
          item.eventNode.classList.add("label-hidden");
        }
        continue;
      }
      occupied.push(item.rect);
    }
  }

  function labelRect(label) {
    const x = Number(label.getAttribute("x"));
    const y = Number(label.getAttribute("y"));
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      return null;
    }
    return {
      x,
      y: y - 10,
      width: estimateLabelWidth(label.textContent || ""),
      height: 12,
    };
  }

  function rectsOverlap(a, b) {
    const padding = 2;
    return (
      a.x < b.x + b.width + padding &&
      a.x + a.width + padding > b.x &&
      a.y < b.y + b.height + padding &&
      a.y + a.height + padding > b.y
    );
  }

  function labelCanReserveSpace(label, eventNode) {
    if (!eventNode || label.style.display === "none" || !isTimelineEventVisible(eventNode)) {
      return false;
    }
    const laneNode = eventNode.closest("[data-lane]");
    return !(laneNode && isTimelineNodeHidden(laneNode)) && !isTimelineNodeHidden(eventNode);
  }

  function eventInlineLabel(event, width) {
    return fitInlineLabel(eventLabelToken(event), width);
  }

  function q1IssueCommandToken(event) {
    const source = event && event.source && typeof event.source === "object" ? event.source : {};
    const raw = String(source.raw || "").trim();
    if (raw) {
      return raw.split(/\s+/, 1)[0];
    }
    const meta = event && event.meta && typeof event.meta === "object" ? event.meta : {};
    const op = String(meta.op || "").trim();
    if (op) {
      return op;
    }
    return String((event && (event.label || event.kind)) || "");
  }

  function eventLabelToken(event) {
    if (!event) {
      return "";
    }
    if (event.kind === "q1_issue") {
      return q1IssueCommandToken(event);
    }
    if (event.kind === "acquire") {
      return "acq";
    }
    if (event.kind === "wait_trigger") {
      return "trig";
    }
    if (event.kind === "wait_sync") {
      return "sync";
    }
    if (event.kind === "feedback_pop" || event.kind === "feedback_com" || String(event.kind).startsWith("fb_")) {
      return "fb";
    }
    if (event.kind === "upd_param") {
      return "upd";
    }
    if (event.kind === "marker_state") {
      return "mark";
    }
    if (event.kind === "branch_region") {
      return "branch";
    }
    return String(event.kind || event.label || "");
  }

  function fitInlineLabel(text, maxWidth) {
    const raw = String(text || "");
    if (!raw || maxWidth < estimateLabelWidth("fb")) {
      return "";
    }
    if (estimateLabelWidth(raw) <= maxWidth) {
      return raw;
    }
    const maxChars = Math.floor((maxWidth - 4) / 6);
    if (maxChars < 4) {
      return "";
    }
    return `${raw.slice(0, maxChars - 1)}...`;
  }

  function estimateLabelWidth(text) {
    return String(text || "").length * 6 + 4;
  }

  function eventLabelPriority(event) {
    if (!event) {
      return 0;
    }
    if (event.kind === "play" || event.kind === "acquire") {
      return 40;
    }
    if (event.kind === "wait_trigger" || event.kind === "wait_sync" || event.kind === "wait") {
      return 30;
    }
    if (event.kind === "feedback_pop" || event.kind === "feedback_com" || String(event.kind).startsWith("fb_")) {
      return 20;
    }
    return 10;
  }

  function updateFeedbackFlows(svg, window, geometry) {
    for (const flowNode of svg.querySelectorAll(".feedback-flow-group")) {
      const fromEvent = timelineEventsById.get(String(flowNode.dataset.fromEventId));
      const toEvent = timelineEventsById.get(String(flowNode.dataset.toEventId));
      const fromNode = fromEvent ? svg.querySelector(`[data-event-id="${fromNodeSelector(flowNode.dataset.fromEventId)}"]`) : undefined;
      const toNode = toEvent ? svg.querySelector(`[data-event-id="${fromNodeSelector(flowNode.dataset.toEventId)}"]`) : undefined;
      if (!fromEvent || !toEvent || !fromNode || !toNode) {
        continue;
      }
      const x1 = scaleTimelineTime(eventTime(fromEvent, "t1") ?? eventTime(fromEvent, "t0") ?? window.min, window, geometry);
      const x2 = scaleTimelineTime(eventTime(toEvent, "t0") ?? window.min, window, geometry);
      const y1 = eventCenterY(fromNode);
      const y2 = eventCenterY(toNode);
      const delta = x2 - x1;
      const direction = delta >= 0 ? 1 : -1;
      const control = Math.max(24, Math.abs(delta) * 0.45);
      const c1 = x1 + direction * control;
      const c2 = x2 - direction * control;
      const path = flowNode.querySelector("path");
      if (path) {
        path.setAttribute("d", `M ${formatCoordinate(x1)} ${formatCoordinate(y1)} C ${formatCoordinate(c1)} ${formatCoordinate(y1)}, ${formatCoordinate(c2)} ${formatCoordinate(y2)}, ${formatCoordinate(x2)} ${formatCoordinate(y2)}`);
      }
      const label = flowNode.querySelector("text");
      if (label) {
        label.style.display = "";
        label.textContent = feedbackFlowVisibleLabel(flowNode);
        label.setAttribute("x", formatCoordinate((x1 + x2) / 2));
        label.setAttribute("y", formatCoordinate(Math.min(y1, y2) - 7));
      }
    }
  }

  function feedbackFlowVisibleLabel(flowNode) {
    const channel = String(flowNode.dataset.channel || "").trim();
    return channel ? `fb ch ${channel}` : "fb";
  }

  function suppressOverlappingOverlayLabels(svg) {
    const occupied = [];
    for (const label of svg.querySelectorAll(".feedback-flow-label")) {
      if (label.style.display === "none") {
        continue;
      }
      const rect = labelRect(label);
      if (!rect || occupied.some((occupiedRect) => rectsOverlap(rect, occupiedRect))) {
        label.style.display = "none";
        continue;
      }
      occupied.push(rect);
    }
  }

  function fromNodeSelector(eventId) {
    return String(eventId || "").replaceAll("\\", "\\\\").replaceAll('"', '\\"');
  }

  function eventCenterY(eventNode) {
    const rect = eventNode.querySelector("rect");
    const y = Number(rect ? rect.getAttribute("y") : undefined);
    return (Number.isFinite(y) ? y : 0) + 11;
  }

  function updateTimelineTicks(svg, window, geometry) {
    for (const node of svg.querySelectorAll(".time-tick")) {
      node.style.display = "none";
    }
    for (const node of svg.querySelectorAll(".tick-label")) {
      node.style.display = "none";
    }
    let axis = document.getElementById("q1timeline-dynamic-time-axis");
    if (!axis) {
      axis = document.createElementNS("http://www.w3.org/2000/svg", "g");
      axis.id = "q1timeline-dynamic-time-axis";
      const firstLane = svg.querySelector(".lane");
      if (firstLane && typeof svg.insertBefore === "function") {
        svg.insertBefore(axis, firstLane);
      } else {
        svg.append(axis);
      }
    }
    axis.replaceChildren();
    const height = timelineSvgViewBoxHeight(svg);
    for (const tick of timelineTicks(window.min, window.max)) {
      const x = scaleTimelineTime(tick, window, geometry);
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("class", "grid time-tick");
      line.setAttribute("x1", formatCoordinate(x));
      line.setAttribute("x2", formatCoordinate(x));
      line.setAttribute("y1", "40");
      line.setAttribute("y2", formatCoordinate(height - 20));
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("class", "tick-label");
      label.setAttribute("x", formatCoordinate(x));
      label.setAttribute("y", "56");
      label.textContent = `${formatWindowValue(tick)} ns`;
      axis.append(line, label);
    }
  }

  function timelineSvgViewBoxHeight(svg) {
    const viewBox = svg ? String(svg.getAttribute("viewBox") || "").trim().split(/\s+/) : [];
    const height = Number(viewBox[3]);
    return Number.isFinite(height) && height > 0 ? height : 200;
  }

  function timelineSvgViewBoxWidth(svg) {
    const viewBox = svg ? String(svg.getAttribute("viewBox") || "").trim().split(/\s+/) : [];
    const width = Number(viewBox[2]);
    return Number.isFinite(width) && width > 0 ? width : 1100;
  }

  function timelineTicks(min, max) {
    const minNs = snapTimelineNs(min);
    const maxNs = Math.max(minNs + 1, snapTimelineNs(max));
    const span = maxNs - minNs;
    const rawStep = span / 4;
    const exponent = Math.floor(Math.log10(rawStep));
    const base = Math.pow(10, exponent);
    const multiples = [1, 2, 5, 10];
    const niceStep = multiples.find((multiple) => rawStep <= multiple * base) * base;
    const step = Math.max(1, Math.round(niceStep));
    const start = Math.ceil(minNs / step) * step;
    const ticks = [];
    for (let tick = start; tick <= maxNs; tick += step) {
      ticks.push(tick);
    }
    if (!ticks.includes(minNs)) {
      ticks.unshift(minNs);
    }
    if (!ticks.includes(maxNs)) {
      ticks.push(maxNs);
    }
    return ticks.slice(0, 8);
  }

  function timelineWindowForRange(start, end, full, minSpanRatio = 0.02) {
    const fullMin = Number(full && full.min);
    const fullMax = Number(full && full.max);
    const rawStart = Number(start);
    const rawEnd = Number(end);
    if (
      !Number.isFinite(fullMin) ||
      !Number.isFinite(fullMax) ||
      fullMax <= fullMin ||
      !Number.isFinite(rawStart) ||
      !Number.isFinite(rawEnd)
    ) {
      return undefined;
    }
    const fullSpan = fullMax - fullMin;
    const minSpan = Math.max(1, fullSpan * Math.max(0, Number(minSpanRatio) || 0));
    const left = Math.min(rawStart, rawEnd);
    const right = Math.max(rawStart, rawEnd);
    const center = (left + right) / 2;
    let span = Math.max(right - left, minSpan);
    span = Math.min(span, fullSpan);
    let min = center - span / 2;
    let max = center + span / 2;
    if (min < fullMin) {
      min = fullMin;
      max = fullMin + span;
    }
    if (max > fullMax) {
      max = fullMax;
      min = fullMax - span;
    }
    return { min, max };
  }

  function highlightedSpanNeedsZoom(start, end, window, plotPixelWidth, minPixels = 18) {
    const windowMin = Number(window && window.min);
    const windowMax = Number(window && window.max);
    const rawStart = Number(start);
    const rawEnd = Number(end);
    if (
      !Number.isFinite(windowMin) ||
      !Number.isFinite(windowMax) ||
      windowMax <= windowMin ||
      !Number.isFinite(rawStart) ||
      !Number.isFinite(rawEnd)
    ) {
      return false;
    }
    const visiblePixels = (Math.abs(rawEnd - rawStart) / Math.max(1, windowMax - windowMin)) * Math.max(1, Number(plotPixelWidth) || 1);
    return visiblePixels < Math.max(1, Number(minPixels) || 1);
  }

  function setTimelineWindow(min, max, options = {}) {
    const full = fullTimelineWindow();
    const fullMin = snapTimelineNs(full.min);
    const fullMax = Math.max(fullMin + 1, snapTimelineNs(full.max));
    const fullSpan = Math.max(1, fullMax - fullMin);
    const minSpan = Math.max(1, fullSpan / 1000);
    let span = Math.max(minSpan, max - min);
    span = Math.min(fullSpan, span);
    let nextMin = min;
    let nextMax = min + span;
    if (nextMin < fullMin) {
      nextMin = fullMin;
      nextMax = fullMin + span;
    }
    if (nextMax > fullMax) {
      nextMax = fullMax;
      nextMin = fullMax - span;
    }
    nextMin = snapTimelineNs(nextMin);
    nextMax = Math.max(nextMin + 1, snapTimelineNs(nextMax));
    if (nextMin < fullMin) {
      nextMin = fullMin;
      nextMax = fullMin + Math.min(fullSpan, nextMax - nextMin);
    }
    if (nextMax > fullMax) {
      nextMax = fullMax;
      nextMin = fullMax - Math.min(fullSpan, nextMax - nextMin);
    }
    timelineWindow = { min: nextMin, max: nextMax };
    applyTimelineScale();
    if (options.persist !== false) {
      writeTimelineWindowState(timelineWindow);
    }
  }

  function fitTimelineToWindow() {
    resetTimelineZoom();
  }

  function resetTimelineZoom(options = {}) {
    const full = fullTimelineWindow();
    timelineWindow = { min: full.min, max: full.max };
    timelineSelectionRange = undefined;
    applyTimelineScale();
    if (options.persist !== false) {
      writeTimelineWindowState(timelineWindow);
    }
  }

  function zoomTimelineSelection() {
    const range = normalizedTimelineSelectionRange();
    if (!range || range.end <= range.start) {
      syncTimelineZoomActionState();
      return;
    }
    const window = timelineWindowForRange(range.start, range.end, fullTimelineWindow(), 0);
    if (!window) {
      return;
    }
    timelineSelectionRange = undefined;
    setTimelineWindow(window.min, window.max);
  }

  function eventZoomTime(event, edge) {
    const basisValue = eventTime(event, edge);
    if (Number.isFinite(Number(basisValue))) {
      return Number(basisValue);
    }
    const localValue = concreteTime(event && event[edge]);
    if (Number.isFinite(Number(localValue))) {
      return Number(localValue);
    }
    return undefined;
  }

  function zoomTimelineToEvent(event) {
    if (!event) {
      syncTimelineZoomActionState();
      return;
    }
    const start = eventZoomTime(event, "t0");
    const end = eventZoomTime(event, "t1");
    const fallbackEnd = end === undefined ? start : end;
    const window = timelineWindowForRange(start, fallbackEnd, fullTimelineWindow());
    if (!window) {
      return;
    }
    timelineSelectionRange = undefined;
    setTimelineWindow(window.min, window.max);
  }

  function zoomTimelineToHighlightedEvent(event) {
    if (!event) {
      return;
    }
    const start = eventZoomTime(event, "t0");
    const end = eventZoomTime(event, "t1");
    const fallbackEnd = end === undefined ? start : end;
    const svg = timelineSvg();
    const geometry = svg ? timelineGeometry(svg) : undefined;
    if (highlightedSpanNeedsZoom(start, fallbackEnd, currentTimelineWindow(), timelinePlotPixelWidth(svg, geometry))) {
      zoomTimelineToEvent(event);
    }
  }

  function zoomTimelineToSelectedEvent() {
    const event = selectedTimelineEventId ? timelineEventsById.get(String(selectedTimelineEventId)) : undefined;
    zoomTimelineToEvent(event);
  }

  function setTimelineTimeBasis(basis) {
    if (basis !== "aligned" && basis !== "local") {
      return;
    }
    if (basis === "aligned" && !hasAlignedTimelineTimes()) {
      return;
    }
    if (basis === timelineTimeBasis) {
      renderTimeBasisToggle();
      return;
    }
    timelineTimeBasis = basis;
    writePersistedWebviewState({ timelineTimeBasis: basis });
    resetTimelineZoom();
  }

  function restoreTimelineZoom() {
    const full = fullTimelineWindow();
    const persisted = persistedTimelineWindowFor(full);
    if (persisted) {
      setTimelineWindow(persisted.min, persisted.max, { persist: false });
      return;
    }
    resetTimelineZoom({ persist: false });
  }

  function zoomTimelineAround(factor, anchorRatio = 0.5) {
    const current = currentTimelineWindow();
    const span = Math.max(1, current.max - current.min);
    const nextSpan = span / factor;
    const anchor = current.min + span * clamp(anchorRatio, 0, 1);
    const clampedAnchorRatio = clamp(anchorRatio, 0, 1);
    setTimelineWindow(anchor - nextSpan * clampedAnchorRatio, anchor + nextSpan * (1 - clampedAnchorRatio));
  }

  function zoomTimelineIn() {
    zoomTimelineAround(1.25);
  }

  function zoomTimelineOut() {
    zoomTimelineAround(1 / 1.25);
  }

  function timelineContainer() {
    const svg = timelineSvg();
    if (svg) {
      return svg.closest(".timeline") || svg.parentElement;
    }
    return document.querySelector(".shared-timeline-stage");
  }

  function timelinePlotPixelWidth(svg, geometry) {
    if (!svg) {
      return sharedTimelinePlotPixelWidth();
    }
    const viewBoxWidth = timelineSvgViewBoxWidth(svg);
    const rect = svg && typeof svg.getBoundingClientRect === "function" ? svg.getBoundingClientRect() : undefined;
    const renderedWidth = rect && Number.isFinite(Number(rect.width)) && Number(rect.width) > 0 ? Number(rect.width) : viewBoxWidth;
    return Math.max(1, renderedWidth * ((geometry.right - geometry.left) / viewBoxWidth));
  }

  function sharedTimelinePlotPixelWidth() {
    const track = document.querySelector(".shared-timeline-stage .lane-track");
    const rect = track && typeof track.getBoundingClientRect === "function" ? track.getBoundingClientRect() : undefined;
    return rect && Number.isFinite(Number(rect.width)) && Number(rect.width) > 0 ? Number(rect.width) : 1;
  }

  function timelinePointerAnchorRatio(event, svg, geometry) {
    if (!svg) {
      return sharedTimelinePointerAnchorRatio(event);
    }
    if (!event || !Number.isFinite(Number(event.clientX))) {
      return 0.5;
    }
    const viewBoxWidth = timelineSvgViewBoxWidth(svg);
    const rect = svg && typeof svg.getBoundingClientRect === "function" ? svg.getBoundingClientRect() : undefined;
    const renderedWidth = rect && Number.isFinite(Number(rect.width)) && Number(rect.width) > 0 ? Number(rect.width) : viewBoxWidth;
    const renderedLeft = rect && Number.isFinite(Number(rect.left)) ? Number(rect.left) : 0;
    const viewBoxX = ((Number(event.clientX) - renderedLeft) / renderedWidth) * viewBoxWidth;
    return clamp((viewBoxX - geometry.left) / Math.max(1, geometry.right - geometry.left), 0, 1);
  }

  function sharedTimelinePointerAnchorRatio(event) {
    if (!event || !Number.isFinite(Number(event.clientX))) {
      return 0.5;
    }
    const track = document.querySelector(".shared-timeline-stage .lane-track");
    const rect = track && typeof track.getBoundingClientRect === "function" ? track.getBoundingClientRect() : undefined;
    if (!rect || !Number.isFinite(Number(rect.left)) || !Number.isFinite(Number(rect.width)) || Number(rect.width) <= 0) {
      return 0.5;
    }
    return clamp((Number(event.clientX) - Number(rect.left)) / Number(rect.width), 0, 1);
  }

  function timelineTimeFromPointer(event, svg, geometry) {
    const current = currentTimelineWindow();
    const span = Math.max(1, current.max - current.min);
    return current.min + span * timelinePointerAnchorRatio(event, svg, geometry);
  }

  function panTimelineByPixels(deltaX) {
    const svg = timelineSvg();
    const geometry = svg ? timelineGeometry(svg) : undefined;
    const current = currentTimelineWindow();
    const span = Math.max(1, current.max - current.min);
    const shift = (Number(deltaX) / timelinePlotPixelWidth(svg, geometry)) * span;
    setTimelineWindow(current.min + shift, current.max + shift);
  }

  function handleTimelineWheel(event) {
    const svg = timelineSvg();
    if (!svg && !document.querySelector(".shared-timeline-stage")) {
      return;
    }
    const deltaX = Number(event.deltaX || 0);
    const deltaY = Number(event.deltaY || 0);
    if (deltaX === 0 && deltaY === 0) {
      return;
    }
    event.preventDefault();
    if (event.shiftKey || deltaX !== 0) {
      panTimelineByPixels(deltaX || deltaY);
      return;
    }
    const geometry = svg ? timelineGeometry(svg) : undefined;
    const anchorRatio = timelinePointerAnchorRatio(event, svg, geometry);
    zoomTimelineAround(deltaY < 0 ? 1.25 : 1 / 1.25, anchorRatio);
  }

  function isTimelineSelectionTarget(target) {
    const node = target && target.closest ? target : undefined;
    if (!node || node.closest("button, .q1timeline-control-popover, .q1timeline-control-chip")) {
      return false;
    }
    return Boolean(node.closest(".shared-timeline-stage .ruler, .shared-timeline-stage .lane-track, svg, .timeline"));
  }

  function applyTimelineSelectionRange(start, end) {
    timelineSelectionRange = { start, end };
    applyTimelineScale();
  }

  function beginTimelineSelectionDrag(event) {
    if (!isTimelineSelectionTarget(event.target)) {
      return;
    }
    const svg = timelineSvg();
    const container = timelineContainer();
    if (!container || (!svg && !document.querySelector(".shared-timeline-stage")) || !Number.isFinite(Number(event.clientX))) {
      return;
    }
    const geometry = svg ? timelineGeometry(svg) : undefined;
    const start = timelineTimeFromPointer(event, svg, geometry);
    timelineSelectionDrag = {
      pointerId: event.pointerId,
      start,
    };
    applyTimelineSelectionRange(start, start);
    if (typeof container.setPointerCapture === "function" && event.pointerId !== undefined) {
      container.setPointerCapture(event.pointerId);
    }
    container.style.cursor = "crosshair";
    event.preventDefault();
    document.addEventListener("pointermove", updateTimelineSelectionDrag);
    document.addEventListener("pointerup", endTimelineSelectionDrag);
    document.addEventListener("pointercancel", endTimelineSelectionDrag);
  }

  function updateTimelineSelectionDrag(event) {
    if (!timelineSelectionDrag || (timelineSelectionDrag.pointerId !== undefined && event.pointerId !== timelineSelectionDrag.pointerId)) {
      return;
    }
    const svg = timelineSvg();
    const geometry = svg ? timelineGeometry(svg) : undefined;
    if ((!svg && !document.querySelector(".shared-timeline-stage")) || !Number.isFinite(Number(event.clientX))) {
      return;
    }
    applyTimelineSelectionRange(timelineSelectionDrag.start, timelineTimeFromPointer(event, svg, geometry));
    event.preventDefault();
  }

  function endTimelineSelectionDrag(event) {
    const container = timelineContainer();
    if (timelineSelectionDrag && event && Number.isFinite(Number(event.clientX))) {
      const svg = timelineSvg();
      const geometry = svg ? timelineGeometry(svg) : undefined;
      applyTimelineSelectionRange(timelineSelectionDrag.start, timelineTimeFromPointer(event, svg, geometry));
    }
    if (container) {
      if (typeof container.releasePointerCapture === "function" && event && event.pointerId !== undefined) {
        container.releasePointerCapture(event.pointerId);
      }
      container.style.cursor = "grab";
    }
    timelineSelectionDrag = undefined;
    document.removeEventListener("pointermove", updateTimelineSelectionDrag);
    document.removeEventListener("pointerup", endTimelineSelectionDrag);
    document.removeEventListener("pointercancel", endTimelineSelectionDrag);
    syncTimelineZoomActionState();
  }

  function handleTimelinePointerDown(event) {
    if (event.button !== undefined && event.button !== 0) {
      return;
    }
    if (event.shiftKey) {
      beginTimelineSelectionDrag(event);
      return;
    }
    if (event.target && typeof event.target.closest === "function" && event.target.closest("[data-event-id]")) {
      return;
    }
    const svg = timelineSvg();
    const container = timelineContainer();
    if (!container || (!svg && !document.querySelector(".shared-timeline-stage")) || !Number.isFinite(Number(event.clientX))) {
      return;
    }
    const current = currentTimelineWindow();
    timelineDrag = {
      pointerId: event.pointerId,
      startClientX: Number(event.clientX),
      startMin: current.min,
      startMax: current.max,
      moved: false,
    };
    if (typeof container.setPointerCapture === "function" && event.pointerId !== undefined) {
      container.setPointerCapture(event.pointerId);
    }
    container.style.cursor = "grabbing";
    event.preventDefault();
    document.addEventListener("pointermove", handleTimelinePointerMove);
    document.addEventListener("pointerup", handleTimelinePointerEnd);
    document.addEventListener("pointercancel", handleTimelinePointerEnd);
  }

  function handleTimelinePointerMove(event) {
    if (!timelineDrag || (timelineDrag.pointerId !== undefined && event.pointerId !== timelineDrag.pointerId)) {
      return;
    }
    const svg = timelineSvg();
    if ((!svg && !document.querySelector(".shared-timeline-stage")) || !Number.isFinite(Number(event.clientX))) {
      return;
    }
    const geometry = svg ? timelineGeometry(svg) : undefined;
    const span = Math.max(1, timelineDrag.startMax - timelineDrag.startMin);
    const deltaX = Number(event.clientX) - timelineDrag.startClientX;
    if (!timelineDrag.moved && Math.abs(deltaX) < timelineDragThresholdPx) {
      return;
    }
    timelineDrag.moved = true;
    const shift = -(deltaX / timelinePlotPixelWidth(svg, geometry)) * span;
    setTimelineWindow(timelineDrag.startMin + shift, timelineDrag.startMax + shift);
    event.preventDefault();
  }

  function handleTimelinePointerEnd(event) {
    const container = timelineContainer();
    if (container) {
      if (typeof container.releasePointerCapture === "function" && event && event.pointerId !== undefined) {
        container.releasePointerCapture(event.pointerId);
      }
      container.style.cursor = "grab";
    }
    timelineDrag = undefined;
    document.removeEventListener("pointermove", handleTimelinePointerMove);
    document.removeEventListener("pointerup", handleTimelinePointerEnd);
    document.removeEventListener("pointercancel", handleTimelinePointerEnd);
  }

  function installTimelineMouseInteractions() {
    const container = timelineContainer();
    if (!container || container.dataset.timelineMouseInteractions === "true") {
      return;
    }
    container.dataset.timelineMouseInteractions = "true";
    container.style.cursor = "grab";
    container.style.touchAction = "none";
    container.title = "Wheel zooms the X-axis. Drag pans the zoomed timeline. Shift-drag selects a range. Use Reset to restore the full range.";
    container.addEventListener("wheel", handleTimelineWheel, { passive: false });
    container.addEventListener("pointerdown", handleTimelinePointerDown);
  }

  function panTimeline(direction) {
    const current = currentTimelineWindow();
    const span = Math.max(1, current.max - current.min);
    const shift = direction * span * 0.25;
    setTimelineWindow(current.min + shift, current.max + shift);
  }

  function renderRelatedBlockSummary(highlightEventIds) {
    const ids = Array.isArray(highlightEventIds) ? highlightEventIds.filter(Boolean) : [];
    const node = ensureRelatedBlockSummaryNode();
    node.hidden = ids.length === 0;
    node.textContent = ids.length ? "Related blocks: " + ids.join(", ") : "";
  }

  function renderUnsavedChanges(hasUnsavedChanges) {
    const node = ensureUnsavedChangesNode();
    node.hidden = !hasUnsavedChanges;
    node.textContent = "Unsaved changes are not reflected in the current analysis.";
  }

  ensureToolbarNode();
  renderAlignmentPolicy(initialAlignmentPolicy);
  renderTimeBasisToggle();
  renderModeToggle(initialViewMode);
  renderSequencerFoldControls();
  renderRelatedBlockSummary([]);
  renderUnsavedChanges(initialUnsavedChanges);
  installAnalysisDetailsPersistence();
  const sharedTimelineRendered = renderSharedTimelineIfAvailable(initialViewMode);
  if (!sharedTimelineRendered) {
    restoreTimelineZoom();
    installTimelineMouseInteractions();
  }
  vscode.postMessage({ type: "webviewReady" });

  window.addEventListener("q1timeline:eventClick", (event) => {
    const eventId = event.detail && event.detail.eventId;
    if (eventId) {
      vscode.postMessage({ type: "eventClick", eventId });
    }
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".q1timeline-control-chip, .q1timeline-control-popover")) {
      closeTimelineControlPopover();
    }
  });
  window.addEventListener("resize", repositionActiveTimelineControlPopover);
  document.addEventListener("scroll", repositionActiveTimelineControlPopover, true);

  document.addEventListener("click", (event) => {
    const target = event.target.closest("[data-event-id]");
    if (target) {
      vscode.postMessage({ type: "eventClick", eventId: target.dataset.eventId });
    }
  });

  window.addEventListener("message", (event) => {
    if (event.data && event.data.type === "setViewMode") {
      renderModeToggle(event.data.mode);
      renderSharedTimelineIfAvailable(event.data.mode);
      return;
    }
    if (event.data && event.data.type === "setUnsavedChanges") {
      renderUnsavedChanges(Boolean(event.data.hasUnsavedChanges));
      return;
    }
    if (!event.data || event.data.type !== "highlightEventIds") {
      return;
    }
    const highlightEventIds = prioritizedHighlightEventIds(event.data.highlightEventIds || []);
    expandQ1IssueLanesForHighlight(highlightEventIds);
    const highlightedEvent = highlightedEventForIds(highlightEventIds);
    zoomTimelineToHighlightedEvent(highlightedEvent);
    const target = findVisibleEventNodeByIds(highlightEventIds);
    if (target) {
      selectEventNode(target, { notify: false });
      target.scrollIntoView({ block: "center", inline: "center" });
    } else {
      const ids = new Set(highlightEventIds);
      document.querySelectorAll("[data-event-id]").forEach((node) => {
        node.classList.toggle("is-selected", ids.has(node.dataset.eventId));
      });
    }
    renderRelatedBlockSummary(highlightEventIds);
  });
})();
