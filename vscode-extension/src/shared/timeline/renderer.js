(function sharedTimelineFactory(root) {
  function element(tag, className, text) {
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
    const node = element("button", className, text);
    node.type = "button";
    if (onClick) {
      node.addEventListener("click", onClick);
    }
    return node;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function normalizeViewport(totalTime, current) {
    const total = Math.max(Number(totalTime) || 0, 1e-9);
    if (!current || !Number.isFinite(Number(current.start)) || !Number.isFinite(Number(current.end))) {
      return { start: 0, end: total };
    }
    let start = Number(current.start);
    let end = Number(current.end);
    if (end < start) {
      [start, end] = [end, start];
    }
    const span = Math.max(end - start, total / 1000000);
    if (span >= total) {
      return { start: 0, end: total };
    }
    start = clamp(start, 0, total - span);
    return { start, end: start + span };
  }

  function viewportDuration(viewport) {
    return Math.max(Number(viewport.end) - Number(viewport.start), 1e-9);
  }

  function panViewport(totalTime, current, deltaRatio) {
    const viewport = normalizeViewport(totalTime, current);
    const span = viewportDuration(viewport);
    return normalizeViewport(totalTime, {
      start: viewport.start - Number(deltaRatio || 0) * span,
      end: viewport.end - Number(deltaRatio || 0) * span,
    });
  }

  function pct(value, total) {
    return Math.round((Number(value) / Number(total)) * 100000) / 1000;
  }

  function blockStyle(block, viewport) {
    const start = Number(block.start) || 0;
    const duration = Math.max(Number(block.duration) || 0, 0);
    const end = start + duration;
    if (end < viewport.start || start > viewport.end) {
      return undefined;
    }
    const span = viewportDuration(viewport);
    const visibleStart = Math.max(start, viewport.start);
    const visibleEnd = Math.min(end, viewport.end);
    const visibleWidth = Math.max(visibleEnd - visibleStart, 0);
    const minMicroWidth = span * 0.0005;
    const width = Math.max(visibleWidth, minMicroWidth);
    return {
      leftPercent: pct(visibleStart - viewport.start, span),
      widthPercent: pct(width, span),
      tiny: visibleWidth < span * 0.0025,
      compact: visibleWidth < span * 0.075,
    };
  }

  function timePointStyle(time, viewport) {
    const span = viewportDuration(viewport);
    const raw = Number(time);
    if (!Number.isFinite(raw)) {
      return undefined;
    }
    const clipped = raw < viewport.start || raw > viewport.end;
    const visible = clamp(raw, viewport.start, viewport.end);
    return {
      leftPercent: pct(visible - viewport.start, span),
      clipped,
    };
  }

  function rangeStyle(startValue, endValue, viewport, minRatio = 0.0025) {
    const start = Number(startValue);
    const end = Number(endValue);
    if (!Number.isFinite(start) || !Number.isFinite(end)) {
      return undefined;
    }
    const left = Math.min(start, end);
    const right = Math.max(start, end);
    if (right < viewport.start || left > viewport.end) {
      return undefined;
    }
    const span = viewportDuration(viewport);
    const visibleStart = Math.max(left, viewport.start);
    const visibleEnd = Math.min(right, viewport.end);
    const width = Math.max(visibleEnd - visibleStart, span * minRatio);
    return {
      leftPercent: pct(visibleStart - viewport.start, span),
      widthPercent: pct(width, span),
      clippedStart: left < viewport.start,
      clippedEnd: right > viewport.end,
    };
  }

  function annotationEventIds(annotation) {
    const ids = Array.isArray(annotation && annotation.eventIds) ? [...annotation.eventIds] : [];
    if (annotation && annotation.eventId !== undefined) {
      ids.push(annotation.eventId);
    }
    return Array.from(new Set(ids.map((eventId) => String(eventId)).filter(Boolean)));
  }

  function activateFeedbackAnnotation(node, active) {
    const flowId = node && node.dataset ? node.dataset.flowId : "";
    const stage = node && node.closest ? node.closest(".shared-timeline-stage") : undefined;
    if (!flowId || !stage) {
      return;
    }
    for (const related of stage.querySelectorAll("[data-flow-id]")) {
      if (related.dataset.flowId === flowId) {
        related.classList.toggle("is-active", active);
      }
    }
  }

  function installAnnotationInteractions(node, annotation, handlers) {
    node.setAttribute("role", "button");
    node.tabIndex = 0;
    node.dataset.annotationId = String(annotation.id || "");
    node.dataset.annotationType = String(annotation.type || "");
    if (annotation.eventId !== undefined) {
      node.dataset.eventId = String(annotation.eventId);
    }
    node.dataset.eventIds = annotationEventIds(annotation).join(" ");
    if (annotation.flowId || annotation.type === "feedback-inline" || annotation.type === "feedback-cross") {
      node.dataset.flowId = String(annotation.flowId || annotation.id || "");
      node.addEventListener("mouseenter", () => activateFeedbackAnnotation(node, true));
      node.addEventListener("mouseleave", () => activateFeedbackAnnotation(node, false));
      node.addEventListener("focus", () => activateFeedbackAnnotation(node, true));
      node.addEventListener("blur", () => activateFeedbackAnnotation(node, false));
    }
    const chooseAnnotation = (event) => {
      if (event && typeof event.stopPropagation === "function") {
        event.stopPropagation();
      }
      activateFeedbackAnnotation(node, true);
      if (handlers && handlers.onAnnotationClick) {
        handlers.onAnnotationClick(annotation, node, event);
      }
    };
    node.addEventListener("click", chooseAnnotation);
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        chooseAnnotation(event);
      }
    });
  }

  function renderFeedbackInlineAnnotation(annotation, viewport, handlers) {
    const style = rangeStyle(annotation.start, annotation.end, viewport, 0.0035);
    if (!style) {
      return undefined;
    }
    const node = element("div", "timeline-annotation feedback-inline-capsule");
    node.dataset.flowId = String(annotation.flowId || annotation.id || "");
    node.style.left = `${style.leftPercent}%`;
    node.style.width = `${style.widthPercent}%`;
    if (style.clippedStart) {
      node.classList.add("is-clipped-start");
    }
    if (style.clippedEnd) {
      node.classList.add("is-clipped-end");
    }
    if (annotation.selected) {
      node.classList.add("is-selected");
    }
    node.title = annotation.label || annotation.title || "";
    node.appendChild(element("span", "feedback-dot feedback-dot-source"));
    node.appendChild(element("span", "feedback-mini-track feedback-mini-track-before"));
    node.appendChild(element("span", "feedback-label", annotation.labelShort || "fb"));
    const afterTrack = element("span", "feedback-mini-track feedback-mini-track-after");
    afterTrack.appendChild(element("span", "feedback-mini-arrow"));
    node.appendChild(afterTrack);
    node.appendChild(element("span", "feedback-dot feedback-dot-target"));
    installAnnotationInteractions(node, annotation, handlers);
    return node;
  }

  function renderFeedbackEndpoint(annotation, role, viewport, handlers) {
    const time = role === "source" ? annotation.fromTime : annotation.toTime;
    const style = timePointStyle(time, viewport);
    if (!style) {
      return undefined;
    }
    const node = element("div", "timeline-annotation feedback-endpoint");
    node.dataset.flowId = String(annotation.flowId || annotation.id || "");
    node.dataset.feedbackRole = role;
    node.setAttribute("data-feedback-role", role);
    node.style.left = `${style.leftPercent}%`;
    if (style.clipped) {
      node.classList.add("is-clipped");
    }
    if (annotation.selected) {
      node.classList.add("is-selected");
    }
    node.title = annotation.label || annotation.title || "";
    node.appendChild(element("span", "feedback-endpoint-marker"));
    node.appendChild(element("span", "feedback-endpoint-label", annotation.labelShort || "fb"));
    installAnnotationInteractions(node, annotation, handlers);
    return node;
  }

  function renderFeedbackCrossAnnotation(annotation, lane, viewport, handlers) {
    if (lane.id === annotation.fromLaneId) {
      return renderFeedbackEndpoint(annotation, "source", viewport, handlers);
    }
    if (lane.id === annotation.toLaneId) {
      return renderFeedbackEndpoint(annotation, "target", viewport, handlers);
    }
    return undefined;
  }

  function renderLoopRangeAnnotation(annotation, viewport, handlers) {
    const style = rangeStyle(annotation.start, annotation.end, viewport, 0.004);
    if (!style) {
      return undefined;
    }
    const node = element("div", "timeline-annotation timeline-loop-range");
    node.style.left = `${style.leftPercent}%`;
    node.style.width = `${style.widthPercent}%`;
    if (style.clippedStart) {
      node.classList.add("is-clipped-start");
    }
    if (style.clippedEnd) {
      node.classList.add("is-clipped-end");
    }
    if (annotation.selected) {
      node.classList.add("is-selected");
    }
    node.title = annotation.title || annotation.label || "";
    node.appendChild(element("span", "loop-range-rail"));
    node.appendChild(element("span", "loop-range-cap loop-range-cap-start"));
    node.appendChild(element("span", "loop-range-cap loop-range-cap-end"));
    node.appendChild(element("span", "loop-range-label", annotation.label || "loop"));
    installAnnotationInteractions(node, annotation, handlers);
    return node;
  }

  function laneAnnotations(lane, model) {
    return (model.annotations || []).filter((annotation) => {
      if (annotation.type === "feedback-inline" || annotation.type === "loop-range") {
        return annotation.laneId === lane.id;
      }
      if (annotation.type === "feedback-cross") {
        return annotation.fromLaneId === lane.id || annotation.toLaneId === lane.id;
      }
      return false;
    });
  }

  function visibleTimelineLanes(model) {
    return (model.lanes || []).filter((lane) => !(lane && lane.hidden));
  }

  function renderLaneAnnotation(annotation, lane, viewport, handlers) {
    if (annotation.type === "feedback-inline") {
      return renderFeedbackInlineAnnotation(annotation, viewport, handlers);
    }
    if (annotation.type === "feedback-cross") {
      return renderFeedbackCrossAnnotation(annotation, lane, viewport, handlers);
    }
    if (annotation.type === "loop-range") {
      return renderLoopRangeAnnotation(annotation, viewport, handlers);
    }
    return undefined;
  }

  function renderSelectionOverlay(selectionRange, viewport) {
    if (!selectionRange) {
      return undefined;
    }
    const start = Math.max(viewport.start, Math.min(viewport.end, Number(selectionRange.start)));
    const end = Math.max(viewport.start, Math.min(viewport.end, Number(selectionRange.end)));
    const left = Math.min(start, end);
    const right = Math.max(start, end);
    const span = viewportDuration(viewport);
    const overlay = element("div", "time-selection");
    overlay.style.left = `${pct(left - viewport.start, span)}%`;
    overlay.style.width = `${pct(right - left, span)}%`;
    return overlay;
  }

  function renderRuler(model) {
    const ruler = element("div", "ruler");
    const selection = renderSelectionOverlay(model.selectionRange, model.viewport);
    if (selection) {
      ruler.appendChild(selection);
    }
    for (const tick of model.ticks || []) {
      const tickNode = element("div", "tick");
      tickNode.style.left = `${tick.leftPercent}%`;
      tickNode.appendChild(element("span", "", tick.label));
      ruler.appendChild(tickNode);
    }
    return ruler;
  }

  function renderBlock(block, viewport, handlers) {
    const style = blockStyle(block, viewport);
    if (!style) {
      return undefined;
    }
    const kind = block.visualKind || block.kind || "default";
    const classes = ["timeline-block", `timeline-block-${kind}`, "event"];
    if (block.classNames) {
      classes.push(...String(block.classNames).split(/\s+/).filter(Boolean));
    }
    if (block.selected) {
      classes.push("is-selected");
    }
    if (block.related) {
      classes.push("is-related");
    }
    const node = element("div", classes.join(" "));
    if (style.tiny) {
      node.classList.add("is-tiny");
    }
    if (style.compact) {
      node.classList.add("is-compact");
    }
    node.setAttribute("role", "button");
    node.tabIndex = 0;
    node.dataset.eventId = String(block.eventId || block.id);
    if (Array.isArray(block.eventIds) && block.eventIds.length) {
      node.dataset.eventIds = block.eventIds.map((eventId) => String(eventId)).join(" ");
    }
    node.dataset.blockId = String(block.id);
    node.dataset.search = String(block.search || `${block.label || ""} ${block.detail || ""}`.toLowerCase());
    node.style.left = `${style.leftPercent}%`;
    node.style.width = `${style.widthPercent}%`;
    if (block.accentColor) {
      node.style.setProperty("--timeline-block-accent", block.accentColor);
    }
    node.setAttribute("aria-selected", block.selected ? "true" : "false");
    if (block.title) {
      node.title = block.title;
    }
    node.appendChild(element("span", "block-label", block.label || ""));
    node.appendChild(element("span", "block-detail", block.detail || ""));
    node.addEventListener("click", (event) => {
      if (handlers && handlers.onBlockClick) {
        handlers.onBlockClick(block, node, event);
      }
    });
    node.addEventListener("keydown", (event) => {
      if ((event.key === "Enter" || event.key === " ") && handlers && handlers.onBlockClick) {
        event.preventDefault();
        handlers.onBlockClick(block, node, event);
      }
    });
    return node;
  }

  function renderLane(lane, model, handlers) {
    const row = element("div", `timeline-row timeline-row-${lane.kind || "default"}`);
    row.dataset.lane = lane.id;
    if (lane.parentGroupId) {
      row.dataset.parentLane = lane.parentGroupId;
      row.classList.add("timeline-row-child");
    }
    if (lane.role) {
      row.dataset.laneRole = lane.role;
    }
    if (lane.hidden) {
      row.dataset.timelineHidden = "true";
      row.setAttribute("hidden", "");
      row.style.display = "none";
    }
    const label = element("div", "lane-label");
    label.appendChild(element("span", "lane-label-text", lane.label));
    row.appendChild(label);

    const track = element("div", `lane-track lane-track-${lane.kind || "default"}`);
    const selection = renderSelectionOverlay(model.selectionRange, model.viewport);
    if (selection) {
      track.appendChild(selection);
    }
    for (const annotation of laneAnnotations(lane, model)) {
      const annotationNode = renderLaneAnnotation(annotation, lane, model.viewport, handlers);
      if (annotationNode) {
        track.appendChild(annotationNode);
      }
    }
    for (const block of lane.blocks || []) {
      const blockNode = renderBlock(block, model.viewport, handlers);
      if (blockNode) {
        track.appendChild(blockNode);
      }
    }
    row.appendChild(track);
    return row;
  }

  function feedbackEndpointAnchorY(rowHeight) {
    return rowHeight - 4;
  }

  function renderFeedbackConnectorPath(annotation, model, laneIndexById) {
    const fromLaneIndex = laneIndexById.get(annotation.fromLaneId);
    const toLaneIndex = laneIndexById.get(annotation.toLaneId);
    const fromPoint = timePointStyle(annotation.fromTime, model.viewport);
    const toPoint = timePointStyle(annotation.toTime, model.viewport);
    if (fromLaneIndex === undefined || toLaneIndex === undefined || !fromPoint || !toPoint) {
      return undefined;
    }
    const rowHeight = Number(model.rowHeight) || 36;
    const chipCenterY = feedbackEndpointAnchorY(rowHeight);
    const y1 = fromLaneIndex * rowHeight + chipCenterY;
    const y2 = toLaneIndex * rowHeight + chipCenterY;
    const x1 = fromPoint.leftPercent;
    const x2 = toPoint.leftPercent;
    const direction = Math.sign(y2 - y1 || 1);
    const controlOffset = Math.max(Math.abs(y2 - y1) * 0.45, 12);
    const controlY1 = y1 + direction * controlOffset;
    const controlY2 = y2 - direction * controlOffset;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("class", `feedback-connector${annotation.selected ? " is-selected" : ""}`);
    path.setAttribute("d", `M ${x1} ${y1} C ${x1} ${controlY1} ${x2} ${controlY2} ${x2} ${y2}`);
    path.setAttribute("data-flow-id", String(annotation.flowId || annotation.id || ""));
    path.setAttribute("data-event-ids", annotationEventIds(annotation).join(" "));
    path.setAttribute("marker-end", "url(#feedback-bridge-arrow)");
    path.setAttribute("vector-effect", "non-scaling-stroke");
    return path;
  }

  function renderFeedbackConnectorDefs(svg) {
    const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
    marker.setAttribute("id", "feedback-bridge-arrow");
    marker.setAttribute("viewBox", "0 0 10 10");
    marker.setAttribute("refX", "9");
    marker.setAttribute("refY", "5");
    marker.setAttribute("markerWidth", "5");
    marker.setAttribute("markerHeight", "5");
    marker.setAttribute("orient", "auto-start-reverse");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("class", "feedback-bridge-arrowhead");
    path.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
    path.setAttribute("fill", "context-stroke");
    marker.appendChild(path);
    defs.appendChild(marker);
    svg.appendChild(defs);
  }

  function renderAnnotations(lanesNode, model, handlers) {
    const crossAnnotations = (model.annotations || []).filter((annotation) => annotation.type === "feedback-cross");
    if (!crossAnnotations.length) {
      return;
    }
    const visibleLanes = visibleTimelineLanes(model);
    const laneIndexById = new Map(visibleLanes.map((lane, index) => [lane.id, index]));
    const rowHeight = Number(model.rowHeight) || 36;
    const layer = element("div", "timeline-connector-layer");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "feedback-connector-svg");
    svg.setAttribute("viewBox", `0 0 100 ${Math.max(visibleLanes.length * rowHeight, rowHeight)}`);
    svg.setAttribute("preserveAspectRatio", "none");
    renderFeedbackConnectorDefs(svg);
    for (const annotation of crossAnnotations) {
      const path = renderFeedbackConnectorPath(annotation, model, laneIndexById);
      if (path) {
        layer.appendChild(svg);
        svg.appendChild(path);
      }
    }
    if (svg.childNodes.length) {
      lanesNode.appendChild(layer);
    }
  }

  function renderTimeline(rootNode, model, handlers = {}) {
    const root = rootNode;
    const section = element("section", "shared-timeline-stage timeline-stage");
    const header = element("div", "stage-header");
    const heading = element("div", "stage-heading");
    heading.appendChild(element("div", "stage-title", model.title || "Timeline"));
    heading.appendChild(element("div", "stage-total", model.subtitle || ""));
    header.appendChild(heading);
    section.appendChild(header);
    section.appendChild(renderRuler(model));

    const lanes = element("div", "timeline-lanes");
    for (const lane of model.lanes || []) {
      lanes.appendChild(renderLane(lane, model, handlers));
    }
    renderAnnotations(lanes, model, handlers);
    section.appendChild(lanes);
    root.replaceChildren(section);
    return section;
  }

  const api = {
    element,
    button,
    blockStyle,
    normalizeViewport,
    panViewport,
    renderAnnotations,
    renderTimeline,
  };

  root.q1lensSharedTimeline = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
