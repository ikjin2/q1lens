const vscode = acquireVsCodeApi();
const root = document.getElementById("qbs-root");
const refreshButton = document.getElementById("refresh-button");
const timelineModel = window.qbsTimelineModel;
const sharedTimeline = window.q1lensSharedTimeline || {};
const instructionColors = window.q1asmInstructionColors;

let currentIr = null;
let currentSourceContext = null;
let selectedId = null;
let activeInspectorTab = "summary";
let currentModel = null;
let viewport = null;
let selectionRange = null;
let dragSelection = null;
let dragPan = null;
let suppressNextBlockClick = false;
const expandedGroups = new Set();
const expandedInlineQ1Lanes = new Set();
const sourceJumpTypes = new Set(["openQ1AsmSource", "openScheduleSource"]);
const PAN_DRAG_THRESHOLD_PX = 3;

refreshButton.addEventListener("click", () => {
  vscode.postMessage({ type: "refresh" });
});

function element(tag, className, text) {
  if (sharedTimeline.element) {
    return sharedTimeline.element(tag, className, text);
  }
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function button(className, text, onClick) {
  if (sharedTimeline.button) {
    return sharedTimeline.button(className, text, onClick);
  }
  const node = element("button", className, text);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}

function selectBlock(id) {
  selectedId = id;
  render(currentIr);
}

function toggleLaneGroup(groupId) {
  if (expandedGroups.has(groupId)) {
    expandedGroups.delete(groupId);
  } else {
    expandedGroups.add(groupId);
  }
  render(currentIr);
}

function toggleInlineQ1Lane(laneId) {
  if (expandedInlineQ1Lanes.has(laneId)) {
    expandedInlineQ1Lanes.delete(laneId);
  } else {
    expandedInlineQ1Lanes.add(laneId);
  }
  render(currentIr);
}

function visibleViewport() {
  return currentModel?.viewport || { start: 0, end: currentModel?.totalSeconds || 1e-9 };
}

function zoomTimeline(factor, anchorRatio) {
  if (!currentModel) {
    return;
  }
  const view = visibleViewport();
  const span = view.end - view.start;
  const anchor = view.start + span * Math.max(0, Math.min(1, anchorRatio));
  const nextSpan = span * factor;
  viewport = {
    start: anchor - nextSpan * anchorRatio,
    end: anchor + nextSpan * (1 - anchorRatio),
  };
  render(currentIr);
}

function fitTimeline() {
  viewport = null;
  selectionRange = null;
  render(currentIr);
}

function zoomToSelection() {
  if (!selectionRange) {
    return;
  }
  viewport = { start: selectionRange.start, end: selectionRange.end };
  selectionRange = null;
  render(currentIr);
}

function timeAxisRect() {
  return document.querySelector(".ruler")?.getBoundingClientRect();
}

function timeFromClientX(clientX) {
  const rect = timeAxisRect();
  const view = visibleViewport();
  if (!rect || rect.width <= 0) {
    return view.start;
  }
  const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
  return view.start + (view.end - view.start) * ratio;
}

function isTimeInteractionTarget(target) {
  const node = target instanceof Element ? target : null;
  if (!node || node.closest("button, .lane-label, .zoom-controls")) {
    return false;
  }
  return Boolean(node.closest(".ruler, .lane-track"));
}

function isTimeSelectionTarget(target) {
  const node = target instanceof Element ? target : null;
  if (!node || node.closest(".timeline-block")) {
    return false;
  }
  return Boolean(node.closest(".ruler, .lane-track"));
}

function beginTimelineDrag(event) {
  if (event.button !== 0 || !isTimeInteractionTarget(event.target)) {
    return;
  }
  if (event.shiftKey) {
    beginSelectionDrag(event);
    return;
  }
  beginPanDrag(event);
}

function beginPanDrag(event) {
  const rect = timeAxisRect();
  if (!rect || rect.width <= 0 || !currentModel) {
    return;
  }
  event.preventDefault();
  dragPan = {
    startClientX: event.clientX,
    width: rect.width,
    viewport: visibleViewport(),
    totalSeconds: currentModel.totalSeconds,
    moved: false,
  };
  document.addEventListener("pointermove", updatePanDrag);
  document.addEventListener("pointerup", endPanDrag, { once: true });
}

function updatePanDrag(event) {
  if (!dragPan) {
    return;
  }
  const deltaPx = event.clientX - dragPan.startClientX;
  if (!dragPan.moved && Math.abs(deltaPx) < PAN_DRAG_THRESHOLD_PX) {
    return;
  }
  event.preventDefault();
  dragPan.moved = true;
  const deltaRatio = deltaPx / dragPan.width;
  viewport = timelineModel.panViewport(dragPan.totalSeconds, dragPan.viewport, deltaRatio);
  render(currentIr);
}

function endPanDrag() {
  if (dragPan?.moved) {
    suppressNextBlockClick = true;
    setTimeout(() => {
      suppressNextBlockClick = false;
    }, 0);
  }
  dragPan = null;
  document.removeEventListener("pointermove", updatePanDrag);
}

function beginSelectionDrag(event) {
  if (!isTimeSelectionTarget(event.target)) {
    return;
  }
  event.preventDefault();
  const start = timeFromClientX(event.clientX);
  dragSelection = { start };
  selectionRange = undefined;
  document.addEventListener("pointermove", updateSelectionDrag);
  document.addEventListener("pointerup", endSelectionDrag, { once: true });
}

function updateSelectionDrag(event) {
  if (!dragSelection) {
    return;
  }
  selectionRange = { start: dragSelection.start, end: timeFromClientX(event.clientX) };
  render(currentIr);
}

function endSelectionDrag(event) {
  if (dragSelection) {
    selectionRange = { start: dragSelection.start, end: timeFromClientX(event.clientX) };
    dragSelection = null;
    render(currentIr);
  }
  document.removeEventListener("pointermove", updateSelectionDrag);
}

function handleWheelZoom(event) {
  if (!isTimeInteractionTarget(event.target)) {
    return;
  }
  event.preventDefault();
  const rect = timeAxisRect();
  const anchorRatio = rect?.width ? Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) : 0.5;
  zoomTimeline(event.deltaY < 0 ? 0.8 : 1.25, anchorRatio);
}

function renderRuler(model) {
  const ruler = element("div", "ruler");
  const selection = renderSelectionOverlay(model);
  if (selection) {
    ruler.appendChild(selection);
  }
  for (const tick of model.ticks) {
    const tickNode = element("div", "tick");
    tickNode.style.left = `${tick.leftPercent}%`;
    tickNode.appendChild(element("span", "", tick.label));
    ruler.appendChild(tickNode);
  }
  return ruler;
}

function renderSelectionOverlay(model) {
  if (!model.selectionRange) {
    return null;
  }
  const overlay = element("div", "time-selection");
  overlay.style.left = `${model.selectionRange.leftPercent}%`;
  overlay.style.width = `${model.selectionRange.widthPercent}%`;
  overlay.title = `${model.selectionRange.startLabel} - ${model.selectionRange.endLabel}`;
  return overlay;
}

function handleBlockKeydown(event, selectionId) {
  if (event.key !== "Enter" && event.key !== " ") {
    return;
  }
  event.preventDefault();
  selectBlock(selectionId);
}

function postSourceJumpMessage(block, event) {
  const message = block.q1asmSourceMessage || block.scheduleSourceMessage;
  if (!message || !sourceJumpTypes.has(message.type)) {
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  vscode.postMessage(message);
}

function renderInlineQ1LaneToggle(lane) {
  const expanded = Boolean(lane.inlineQ1PreviewExpanded);
  const label = lane.inlineQ1PreviewLabel || "generated preview";
  const toggle = button("inline-q1-lane-toggle", expanded ? "v" : ">", (event) => {
    event.preventDefault();
    event.stopPropagation();
    toggleInlineQ1Lane(lane.inlineQ1PreviewLaneId);
  });
  toggle.addEventListener("dblclick", (event) => {
    event.preventDefault();
    event.stopPropagation();
  });
  toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
  toggle.setAttribute("aria-label", expanded ? `Hide ${label}` : `Show ${label}`);
  toggle.title = expanded ? `Hide ${label}` : `Show ${label}`;
  return toggle;
}

function renderBlockLabel(block) {
  const text = String(block.label || "");
  const label = element("span", "block-label");
  const tokens = block.accentColor ? instructionColors.sourceTokens(text) : [];
  const command = tokens.find((token) => token.kind === "command");
  if (!command || command.start !== 0 || command.family === "unknown") {
    label.textContent = text;
    return label;
  }
  let offset = 0;
  for (const token of tokens) {
    if (token.start > offset) label.appendChild(element("span", "", text.slice(offset, token.start)));
    const node = element("span", token.kind === "command" ? "q1asm-opcode" : `q1asm-argument q1asm-argument-${token.kind}`, text.slice(token.start, token.end));
    if (token.kind !== "command") node.style.color = instructionColors.argumentAccentColor(token.kind);
    label.appendChild(node);
    offset = token.end;
  }
  if (offset < text.length) label.appendChild(element("span", "", text.slice(offset)));
  return label;
}

function renderBlock(block, selectedInspectorId) {
  const visualKind = block.visualKind || block.type;
  const selectionId = block.sourceBlockId || block.id;
  const node = element("div", `timeline-block timeline-block-${visualKind}`);
  node.dataset.blockId = block.id;
  node.setAttribute("role", "button");
  node.tabIndex = 0;
  node.addEventListener("click", (event) => {
    if (suppressNextBlockClick) {
      event.preventDefault();
      event.stopPropagation();
      suppressNextBlockClick = false;
      return;
    }
    selectBlock(selectionId);
  });
  node.addEventListener("dblclick", (event) => postSourceJumpMessage(block, event));
  node.addEventListener("keydown", (event) => handleBlockKeydown(event, selectionId));
  node.style.left = `${block.leftPercent}%`;
  node.style.width = `${block.widthPercent}%`;
  if (block.accentColor) {
    node.classList.add("has-command-color");
    node.style.setProperty("--timeline-block-accent", block.accentColor);
  }
  node.setAttribute("aria-selected", block.selected ? "true" : "false");
  if (block.provenanceStatus) {
    node.dataset.provenance = block.provenanceStatus;
  }
  node.toggleAttribute("data-related", Boolean(block.relatedSelected));
  node.title = `${block.label} | ${block.startLabel} | ${block.durationLabel}`;
  node.setAttribute(
    "aria-label",
    block.provenanceLabel
      ? `${block.label}, ${block.provenanceLabel} provenance, starts ${block.startLabel}, duration ${block.durationLabel}`
      : `${block.label}, starts ${block.startLabel}, duration ${block.durationLabel}`,
  );
  if (block.provenanceDescription) {
    node.title = `${node.title} | Provenance: ${block.provenanceLabel}. ${block.provenanceDescription}`;
  }
  if (block.q1asmText) {
    node.title = `${block.q1asmText} | ${node.title}`;
  }
  if (block.q1asmSourceMessage) {
    node.title = `${node.title} | Double-click to open Q1ASM source`;
  } else if (block.scheduleSourceMessage) {
    node.title = `${node.title} | Double-click to open schedule source`;
  }
  if (Number.isFinite(block.topPx)) {
    node.style.top = `${block.topPx}px`;
  }
  node.appendChild(renderBlockLabel(block));
  node.appendChild(element("span", "block-detail", block.detail || block.durationLabel || ""));
  return node;
}

function renderLane(lane, selectedInspectorId) {
  const row = element("div", `timeline-row timeline-row-${lane.kind || "default"}`);
  if (lane.parentGroupId) {
    row.classList.add("timeline-row-child");
  }
  const label = element("div", "lane-label");
  if (lane.expandable) {
    const toggle = button("lane-toggle", lane.expanded ? "▾" : "▸", () => toggleLaneGroup(lane.groupId));
    toggle.setAttribute("aria-expanded", lane.expanded ? "true" : "false");
    toggle.title = lane.expanded ? "Collapse target lanes" : "Expand target lanes";
    const labelText = element("span", "lane-label-text", lane.label);
    if (lane.title) {
      labelText.title = lane.title;
    }
    label.appendChild(toggle);
    if (lane.inlineQ1PreviewLaneId) {
      label.appendChild(renderInlineQ1LaneToggle(lane));
    }
    label.appendChild(labelText);
    label.appendChild(element("span", "lane-child-count", String(lane.childrenCount)));
  } else {
    const labelText = element("span", "lane-label-text", lane.label);
    if (lane.title) {
      labelText.title = lane.title;
    }
    if (lane.inlineQ1PreviewLaneId) {
      label.appendChild(renderInlineQ1LaneToggle(lane));
    }
    label.appendChild(labelText);
  }
  row.appendChild(label);
  const track = element("div", `lane-track lane-track-${lane.kind || "default"}`);
  if (Number.isFinite(lane.trackHeightPx)) {
    track.style.minHeight = `${lane.trackHeightPx}px`;
  }
  const selection = renderSelectionOverlay(currentModel);
  if (selection) {
    track.appendChild(selection);
  }
  for (const block of lane.blocks) {
    track.appendChild(renderBlock(block, selectedInspectorId));
  }
  row.appendChild(track);
  return row;
}

function renderZoomControls(model) {
  const controls = element("div", "zoom-controls");
  controls.appendChild(button("icon-button", "-", () => zoomTimeline(1.25, 0.5))).title = "Zoom out";
  controls.appendChild(button("icon-button", "+", () => zoomTimeline(0.8, 0.5))).title = "Zoom in";
  controls.appendChild(button("tool-button", "Fit", fitTimeline)).title = "Show full schedule";
  const selectionButton = button("tool-button", "Zoom selection", zoomToSelection);
  selectionButton.disabled = !model.selectionRange;
  selectionButton.title = "Shift-drag a time range to enable";
  controls.appendChild(selectionButton);
  return controls;
}

function renderTimeline(model) {
  const section = element("section", "timeline-stage");
  section.addEventListener("pointerdown", beginTimelineDrag);
  section.addEventListener("wheel", handleWheelZoom, { passive: false });
  const header = element("div", "stage-header");
  const title = element("div", "stage-heading");
  title.appendChild(element("div", "stage-title", "Q1Lens"));
  title.appendChild(
    element(
      "div",
      "stage-total",
      `view ${model.viewport.startLabel} - ${model.viewport.endLabel} (${model.viewport.durationLabel}) / total ${model.totalLabel}`,
    ),
  );
  header.appendChild(title);
  header.appendChild(renderZoomControls(model));
  section.appendChild(header);
  section.appendChild(renderRuler(model));

  const lanes = element("div", "timeline-lanes");
  for (const lane of model.lanes) {
    lanes.appendChild(renderLane(lane, model.inspector.selectedId));
  }
  section.appendChild(lanes);
  return section;
}

function renderRows(rows) {
  const rowList = element("dl", "inspector-rows");
  for (const row of rows || []) {
    rowList.appendChild(element("dt", "", row.label));
    rowList.appendChild(element("dd", "", row.value));
  }
  return rowList;
}

function renderInspectorTabs(inspector) {
  const tabs = element("div", "inspector-tabs");
  for (const tab of inspector.tabs || []) {
    const tabButton = button("inspector-tab", tab.label, () => {
      if (tab.message) {
        vscode.postMessage(tab.message);
        return;
      }
      activeInspectorTab = tab.id;
      render(currentIr);
    });
    tabButton.setAttribute("aria-selected", activeInspectorTab === tab.id ? "true" : "false");
    tabs.appendChild(tabButton);
  }
  return tabs;
}

function renderInspectorBody(inspector) {
  if (activeInspectorTab === "lowering") {
    return renderRows(inspector.loweringRows || []);
  }
  if (activeInspectorTab === "q1asm") {
    return renderQ1asmDrilldown(inspector.q1asmDrilldown);
  }
  return renderRows(inspector.rows || []);
}

function renderQ1asmSourceLine(raw) {
  const text = raw || " ";
  const code = element("code", "q1asm-line-text");
  const tokens = instructionColors.sourceTokens(text);
  if (!tokens.length) {
    code.textContent = text;
    return code;
  }
  let offset = 0;
  for (const token of tokens) {
    if (token.start > offset) code.appendChild(element("span", "", text.slice(offset, token.start)));
    const value = text.slice(token.start, token.end);
    const isCommand = token.kind === "command";
    const node = element("span", isCommand ? "q1asm-opcode" : `q1asm-argument q1asm-argument-${token.kind}`, value);
    node.style.color = isCommand ? instructionColors.commandAccentColor(value) : instructionColors.argumentAccentColor(token.kind);
    code.appendChild(node);
    offset = token.end;
  }
  if (offset < text.length) code.appendChild(element("span", "", text.slice(offset)));
  return code;
}

function renderQ1asmDrilldown(drilldown) {
  const panel = element("div", "q1asm-drilldown");
  if (!drilldown) {
    panel.appendChild(element("p", "empty-state", "No Q1ASM target is available for this selection."));
    return panel;
  }

  const header = element("div", "q1asm-drilldown-header");
  const title = element("div", "q1asm-drilldown-title", drilldown.title || "Q1ASM Preview");
  const meta = element("div", "q1asm-drilldown-meta");
  if (drilldown.sequencer) {
    meta.appendChild(element("span", "q1asm-chip", drilldown.sequencer));
  }
  if (drilldown.lineRangeLabel) {
    meta.appendChild(element("span", "q1asm-chip", `L${drilldown.lineRangeLabel}`));
  }
  header.appendChild(title);
  header.appendChild(meta);
  panel.appendChild(header);

  if (!drilldown.available) {
    panel.appendChild(element("p", "empty-state", drilldown.emptyMessage || "Q1ASM text is unavailable."));
    return panel;
  }

  const lines = element("div", "q1asm-lines");
  for (const line of drilldown.lines || []) {
    const row = element("div", "q1asm-line");
    row.dataset.highlighted = line.highlighted ? "true" : "false";
    row.appendChild(element("span", "q1asm-line-number", String(line.number)));
    row.appendChild(renderQ1asmSourceLine(line.text));
    lines.appendChild(row);
  }
  panel.appendChild(lines);
  return panel;
}

function renderInspector(model) {
  const inspector = element("aside", "inspector");
  inspector.appendChild(element("div", "inspector-kicker", model.inspector.subtitle));
  inspector.appendChild(element("h2", "", model.inspector.title));
  if (model.inspector.tabs?.length) {
    const activeExists = model.inspector.tabs.some((tab) => !tab.message && tab.id === activeInspectorTab);
    if (!activeExists) {
      activeInspectorTab = "summary";
    }
    inspector.appendChild(renderInspectorTabs(model.inspector));
  }
  inspector.appendChild(renderInspectorBody(model.inspector));
  if (model.inspector.provenanceNotice) {
    const notice = element("div", "inspector-notice");
    notice.dataset.tone = model.inspector.provenanceNotice.tone || "information";
    notice.setAttribute("role", "note");
    notice.appendChild(element("div", "inspector-notice-title", model.inspector.provenanceNotice.title));
    notice.appendChild(element("p", "", model.inspector.provenanceNotice.message));
    inspector.appendChild(notice);
  }

  const actions = element("div", "inspector-actions");
  for (const action of model.inspector.actions) {
    actions.appendChild(button("action-button", action.label, () => vscode.postMessage(action.message)));
  }
  actions.appendChild(button("action-button secondary", "Open QBS IR", () => vscode.postMessage({ type: "openIr" })));
  inspector.appendChild(actions);
  return inspector;
}

function revealProvenance(status) {
  const target = timelineModel.provenanceSelection(currentIr, status, selectedId);
  if (!target) {
    return;
  }
  expandedGroups.add(target.groupId);
  // Reset zoom so a target outside the current window is included in the model.
  viewport = null;
  selectionRange = null;
  activeInspectorTab = "lowering";
  render(currentIr, target.blockId);
  const node = Array.from(root.querySelectorAll("[data-block-id]"))
    .find((candidate) => candidate.dataset.blockId === target.blockId);
  if (node) {
    node.scrollIntoView({ block: "nearest", inline: "nearest" });
    node.focus({ preventScroll: true });
  }
}

function renderProvenanceLegend(coverage) {
  const legend = element("section", "provenance-legend");
  legend.setAttribute("aria-label", coverage.ariaLabel);
  legend.appendChild(element("div", "provenance-legend-title", "Provenance coverage"));
  const items = element("div", "provenance-legend-items");
  items.setAttribute("role", "list");
  for (const entry of coverage.entries || []) {
    const item = element("div", "");
    item.setAttribute("role", "listitem");
    const control = button("provenance-legend-item", "", () => revealProvenance(entry.status));
    control.dataset.provenance = entry.status;
    control.disabled = !entry.count;
    control.title = entry.count ? `Locate next ${entry.label.toLowerCase()} symbolic block` : `No ${entry.label.toLowerCase()} symbolic blocks`;
    control.setAttribute("aria-label", `${entry.label}: ${entry.count}. ${control.title}`);
    const swatch = element("span", "provenance-swatch");
    swatch.setAttribute("aria-hidden", "true");
    control.appendChild(swatch);
    control.appendChild(element("span", "provenance-legend-label", `${entry.label} ${entry.count}`));
    item.appendChild(control);
    items.appendChild(item);
  }
  legend.appendChild(items);
  return legend;
}

function renderSummary(model) {
  const summary = element("section", "summary-strip");
  if (model.source) {
    const source = element("div", "source-card");
    const title = element("div", "source-title", "Source");
    const files = element("div", "source-files");
    files.appendChild(element("span", "", model.source.projectLabel));
    files.appendChild(element("span", "", model.source.scheduleLabel));
    files.appendChild(element("span", "", model.source.outputLabel));
    const actions = element("div", "source-actions");
    for (const action of model.source.actions) {
      actions.appendChild(button("source-button", action.label, () => vscode.postMessage(action.message)));
    }
    source.appendChild(title);
    source.appendChild(files);
    source.appendChild(actions);
    summary.appendChild(source);
  }
  summary.appendChild(renderProvenanceLegend(model.provenanceCoverage));
  for (const item of model.artifactSummary) {
    const stat = element("div", "summary-item");
    stat.appendChild(element("span", "summary-value", item.value));
    stat.appendChild(element("span", "summary-label", item.label));
    summary.appendChild(stat);
  }
  const view = element("div", "summary-item");
  view.appendChild(element("span", "summary-value", model.viewport.durationLabel));
  view.appendChild(element("span", "summary-label", "Visible window"));
  summary.appendChild(view);
  if (model.selectionRange) {
    const selection = element("div", "summary-item");
    selection.appendChild(element("span", "summary-value", model.selectionRange.durationLabel));
    selection.appendChild(element("span", "summary-label", "Selected time"));
    summary.appendChild(selection);
  }
  return summary;
}

function render(ir, explicitSelectedId, sourceContext) {
  currentIr = ir;
  currentSourceContext = sourceContext || currentSourceContext;
  if (!currentIr) {
    return;
  }
  if (explicitSelectedId) {
    selectedId = explicitSelectedId;
  }
  const model = timelineModel.buildTimelineModel(currentIr, selectedId, currentSourceContext, {
    viewport,
    selectionRange,
    expandedGroups: [...expandedGroups],
    expandedInlineQ1Lanes: [...expandedInlineQ1Lanes],
  });
  currentModel = model;
  root.replaceChildren(renderTimeline(model), renderInspector(model), renderSummary(model));
}

window.addEventListener("message", (event) => {
  const message = event.data;
  if (message.type === "render") {
    render(message.ir, message.selectedOperationId, message.sourceContext);
  }
});

vscode.postMessage({ type: "ready" });
