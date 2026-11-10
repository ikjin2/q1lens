(function sharedTimelineFactory(root) {
  const instructionColors = typeof module !== "undefined" && module.exports
    ? require("./instructionColors.js") : root.q1asmInstructionColors;
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

  function timeCoordinate(time, viewport) {
    return viewport.timeScale ? viewport.timeScale.toDisplay(Number(time)) : Number(time);
  }

  function viewportDuration(viewport) {
    return Math.max(timeCoordinate(viewport.end, viewport) - timeCoordinate(viewport.start, viewport), 1e-9);
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
    const visibleWidth = Math.max(timeCoordinate(visibleEnd, viewport) - timeCoordinate(visibleStart, viewport), 0);
    const minMicroWidth = span * 0.0005;
    const width = Math.max(visibleWidth, minMicroWidth);
    return {
      leftPercent: pct(timeCoordinate(visibleStart, viewport) - timeCoordinate(viewport.start, viewport), span),
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
      leftPercent: pct(timeCoordinate(visible, viewport) - timeCoordinate(viewport.start, viewport), span),
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
    const width = Math.max(timeCoordinate(visibleEnd, viewport) - timeCoordinate(visibleStart, viewport), span * minRatio);
    return {
      leftPercent: pct(timeCoordinate(visibleStart, viewport) - timeCoordinate(viewport.start, viewport), span),
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
    const style = rangeStyle(annotation.start, annotation.end, viewport, 0);
    if (!style) {
      return undefined;
    }
    const node = element("div", "timeline-annotation feedback-inline-capsule");
    node.dataset.flowId = String(annotation.flowId || annotation.id || "");
    node.style.left = `${style.leftPercent}%`;
    node.style.width = `${style.widthPercent}%`;
    const fromTime = annotation.fromTime ?? annotation.start;
    const toTime = annotation.toTime ?? annotation.end;
    if (fromTime > toTime) node.classList.add("is-reversed");
    if (timePointStyle(fromTime, viewport)?.clipped) node.classList.add("is-clipped-source");
    if (timePointStyle(toTime, viewport)?.clipped) node.classList.add("is-clipped-target");
    if (style.clippedStart) {
      node.classList.add("is-clipped-start");
    }
    if (style.clippedEnd) {
      node.classList.add("is-clipped-end");
    }
    if (annotation.selected) {
      node.classList.add("is-selected");
    }
    node.title = [annotation.label || annotation.title, annotation.sourceTitle, annotation.targetTitle].filter(Boolean).join("\n");
    node.setAttribute("aria-label", node.title);
    const sourceDot = element("span", "feedback-dot feedback-dot-source");
    sourceDot.dataset.feedbackRole = "source";
    sourceDot.dataset.flowId = node.dataset.flowId;
    sourceDot.dataset.eventId = annotation.fromEventId || "";
    sourceDot.title = annotation.sourceTitle || node.title;
    sourceDot.setAttribute("aria-label", sourceDot.title);
    node.appendChild(sourceDot);
    const route = element("span", "feedback-inline-route");
    route.setAttribute("aria-hidden", "true");
    for (const [index, d] of ["M 0 0 L 10 10 Q 12 12 14 12", "M 0 12 Q 2 12 4 10 L 14 0"].entries()) {
      if (index) {
        route.appendChild(element("span", "feedback-mini-track feedback-mini-track-before"));
        route.appendChild(element("span", "feedback-mini-track feedback-mini-track-after"));
      }
      const bend = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      bend.setAttribute("class", "feedback-route-bend");
      bend.setAttribute("viewBox", "0 0 14 12");
      bend.setAttribute("preserveAspectRatio", "none");
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d);
      path.setAttribute("vector-effect", "non-scaling-stroke");
      bend.appendChild(path);
      if (index) {
        const arrow = document.createElementNS("http://www.w3.org/2000/svg", "path");
        arrow.setAttribute("class", "feedback-mini-arrow");
        arrow.setAttribute("d", "M 8 1 L 14 0 L 13 6 Z");
        bend.appendChild(arrow);
      }
      route.appendChild(bend);
    }
    node.appendChild(route);
    node.appendChild(element("span", "feedback-label", annotation.labelShort || "fb"));
    const targetDot = element("span", "feedback-dot feedback-dot-target");
    targetDot.dataset.feedbackRole = "target";
    targetDot.dataset.flowId = node.dataset.flowId;
    targetDot.dataset.eventId = annotation.toEventId || "";
    targetDot.title = annotation.targetTitle || node.title;
    targetDot.setAttribute("aria-label", targetDot.title);
    node.appendChild(targetDot);
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
    node.dataset.eventId = (role === "source" ? annotation.fromEventId : annotation.toEventId) || "";
    node.title = (role === "source" ? annotation.sourceTitle : annotation.targetTitle) || annotation.label || annotation.title || "";
    node.setAttribute("aria-label", node.title);
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

  function renderLoopRangeAnnotation(annotation, viewport, handlers, level = 0) {
    const style = rangeStyle(annotation.start, annotation.end, viewport, 0.004);
    if (!style) {
      return undefined;
    }
    const node = element("div", "timeline-annotation timeline-loop-range");
    node.style.left = `${style.leftPercent}%`;
    node.style.width = `${style.widthPercent}%`;
    node.style.top = `${level * 14}px`;
    node.dataset.loopLevel = String(level);
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

  function loopAnnotationLayout(annotations, viewport) {
    const loops = annotations.filter((annotation) => annotation.type === "loop-range" && rangeStyle(annotation.start, annotation.end, viewport, 0.004))
      .map((annotation) => ({ annotation, start: Math.min(Number(annotation.start), Number(annotation.end)), end: Math.max(Number(annotation.start), Number(annotation.end)) }))
      .sort((left, right) => left.start - right.start || right.end - left.end || String(left.annotation.id).localeCompare(String(right.annotation.id)));
    const ends = [];
    const levels = new Map();
    for (const { annotation, start, end } of loops) {
      let level = ends.findIndex((previousEnd) => previousEnd < start);
      if (level < 0) level = ends.length;
      ends[level] = end;
      levels.set(annotation.id, level);
    }
    return { levels, offset: ends.length > 1 ? ends.length * 14 : 0 };
  }

  function renderLaneAnnotation(annotation, lane, viewport, handlers, layout) {
    if (annotation.type === "feedback-inline") {
      return renderFeedbackInlineAnnotation(annotation, viewport, handlers);
    }
    if (annotation.type === "feedback-cross") {
      return renderFeedbackCrossAnnotation(annotation, lane, viewport, handlers);
    }
    if (annotation.type === "loop-range") {
      return renderLoopRangeAnnotation(annotation, viewport, handlers, layout.levels.get(annotation.id));
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
    overlay.dataset.timeStart = String(left);
    overlay.dataset.timeEnd = String(right);
    overlay.style.left = `${pct(timeCoordinate(left, viewport) - timeCoordinate(viewport.start, viewport), span)}%`;
    overlay.style.width = `${pct(timeCoordinate(right, viewport) - timeCoordinate(left, viewport), span)}%`;
    return overlay;
  }

  function renderRuler(model) {
    const ruler = element("div", "ruler");
    const selection = renderSelectionOverlay(model.selectionRange, model.viewport);
    if (selection) {
      ruler.appendChild(selection);
    }
    for (const [index, tick] of (model.ticks || []).entries()) {
      const tickNode = element("div", "tick");
      if (index === model.ticks.length - 1 && tick.leftPercent > 90) tickNode.classList.add("is-end");
      tickNode.style.left = `${tick.leftPercent}%`;
      tickNode.appendChild(element("span", "", tick.label));
      ruler.appendChild(tickNode);
    }
    return ruler;
  }

  function renderBlock(block, viewport, handlers) {
    const style = block.issueMarker
      ? rangeStyle(block.start, block.start + Math.max(Number(block.duration) || 0, 0), viewport, 0)
      : blockStyle(block, viewport);
    if (!style || block.issueMarker && style.clippedStart) {
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
    if (style.tiny && !block.foldGroup?.expanded) {
      node.classList.add("is-tiny");
    }
    if (style.compact) {
      node.classList.add("is-compact");
    }
    node.setAttribute("role", "button");
    node.tabIndex = 0;
    node.dataset.eventId = String(block.eventId || block.id);
    if (block.foldGroup) {
      node.setAttribute("aria-label", block.foldGroup.action);
      node.setAttribute("aria-expanded", String(block.foldGroup.expanded));
    }
    if (Array.isArray(block.eventIds) && block.eventIds.length) {
      node.dataset.eventIds = block.eventIds.map((eventId) => String(eventId)).join(" ");
    }
    node.dataset.blockId = String(block.id);
    node.dataset.startClipped = String(Number(block.start) < viewport.start);
    node.dataset.endClipped = String(Number(block.start) + (Number(block.duration) || 0) > viewport.end);
    node.dataset.connectionStartKnown = String(block.connectionStartKnown !== false);
    node.dataset.search = String(block.search || `${block.label || ""} ${block.detail || ""}`.toLowerCase());
    node.style.left = `${style.leftPercent}%`;
    node.style.width = block.diagnosticPlacement === "point" || block.pointMarker ? "10px" :
      block.issueMarker ? `min(24px, ${style.widthPercent}%)` : `${style.widthPercent}%`;
    if (block.accentColor) {
      node.classList.add("has-command-color");
      node.style.setProperty("--timeline-block-accent", block.accentColor);
    }
    if (block.acquisitionMode) {
      node.dataset.acquisitionMode = block.acquisitionMode;
      node.style.setProperty("--timeline-acquisition-offset", `${(block.acquisitionTrack || 0) * 20}px`);
      node.setAttribute("aria-label", block.title || `${block.acquisitionMode} · ${block.label}`);
    }
    node.setAttribute("aria-selected", block.selected ? "true" : "false");
    const errorMessages = (block.diagnosticErrors || []).map((error) => error.message);
    if (errorMessages.length) {
      node.classList.add("has-error");
      node.dataset.diagnosticMessage = errorMessages.join("\n");
      node.setAttribute("aria-description", node.dataset.diagnosticMessage);
    }
    const title = block.title || (block.issueMarker ? `Q1 issue start: ${block.label || ""}` : "");
    if (title || errorMessages.length) {
      node.title = [title, ...errorMessages.filter((message) => message !== title)].filter(Boolean).join("\n");
    }
    if (block.diagnosticPlacement) {
      node.classList.add("timeline-diagnostic", `diagnostic-${block.diagnosticPlacement}`);
      node.dataset.diagnosticPlacement = block.diagnosticPlacement;
      node.setAttribute("aria-label", node.title);
    } else if (block.pointMarker) {
      node.classList.add("timeline-point-marker");
      node.setAttribute("aria-label", node.title);
    } else if (block.issueMarker) {
      node.classList.add("timeline-issue-marker");
      node.dataset.issueMarker = "true";
      node.dataset.rtEventIds = (block.rtEventIds || []).map(String).join(" ");
      node.setAttribute("aria-label", node.title);
      node.innerHTML = '<svg class="issue-packet-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M2 4.5 8 1.5l6 3v7L8 14.5l-6-3Z M2 4.5l6 3 6-3 M8 7.5v7 M5 3l6 3"/></svg>';
    } else if (block.acquisitionMode) {
      const icons = {
        integration: '<path d="M18 4H6l7 8-7 8h12"/>',
        weighted: '<path d="M4 6h4m4 0h8M4 12h10m4 0h2M4 18h2m4 0h10M8 3v6m6 0v6m-4 0v6"/>',
        scope: '<path d="M2 12h4l3-8 6 16 3-8h4"/>',
        ttl: '<path d="M4 8h16M4 16h16M10 3 8 21M17 3l-2 18"/>',
        timetags: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
        digital: '<path d="M2 17h5V7h10v10h5"/>',
      };
      node.appendChild(element("span", "acquisition-bracket"));
      const label = element("span", "block-label");
      const icon = element("span", "acquisition-mode-icon");
      icon.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">${icons[block.acquisitionMode] || icons.integration}</svg>`;
      label.append(icon, element("span", "acquisition-duration", block.label));
      node.appendChild(label);
    } else {
      const labelText = block.commandLabel || block.label || "";
      const label = element("span", "block-label", labelText);
      const tokens = block.accentColor && !errorMessages.length ? instructionColors.sourceTokens(labelText) : [];
      if (tokens[0]?.kind === "command" && tokens[0].family !== "unknown" && tokens[0].start === 0) {
        label.textContent = "";
        let offset = 0;
        for (const token of tokens) {
          if (token.start > offset) label.appendChild(element("span", "", labelText.slice(offset, token.start)));
          const span = element("span", "", labelText.slice(token.start, token.end));
          span.style.color = token.kind === "command" ? block.accentColor : instructionColors.argumentAccentColor(token.kind);
          label.appendChild(span);
          offset = token.end;
        }
        if (offset < labelText.length) label.appendChild(element("span", "", labelText.slice(offset)));
      }
      node.appendChild(label);
      node.appendChild(element("span", "block-detail", block.detail || ""));
    }
    if (errorMessages.length) {
      const mark = element("span", "timeline-error-mark", "!");
      mark.setAttribute("aria-hidden", "true");
      node.prepend(mark);
    }
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

  function renderLane(lane, model, handlers, layout) {
    const row = element("div", `timeline-row timeline-row-${lane.kind || "default"}`);
    row.dataset.lane = lane.id;
    const feedbackOffset = laneAnnotations(lane, model).some((annotation) => annotation.type === "feedback-inline" &&
      rangeStyle(annotation.start, annotation.end, model.viewport, 0.0035)) ? 12 : 0;
    if (feedbackOffset) {
      row.dataset.feedbackOffset = String(feedbackOffset);
      row.style.setProperty("--timeline-feedback-offset", `${feedbackOffset}px`);
    }
    if (lane.acquisitionTracks > 1) row.classList.add("has-scope");
    const hasDiagnosticPoint = (lane.blocks || []).some((block) => block.diagnosticPlacement === "point");
    if (layout.offset || hasDiagnosticPoint) {
      row.style.setProperty("--timeline-loop-offset", `${layout.offset}px`);
      const height = lane.role === "acquisition" ? 22 + (lane.acquisitionTracks > 1 ? 20 : 0) : Number(model.rowHeight) || 36;
      row.style.minHeight = `${(hasDiagnosticPoint ? Math.max(height, 38) : height) + layout.offset + feedbackOffset}px`;
    }
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
    if (lane.labelTitle) label.title = lane.labelTitle;
    label.appendChild(element("span", "lane-label-text", lane.label));
    if (lane.q1Errors?.length) {
      const mark = element("button", "event q1-error-marker timeline-error-mark", "!");
      mark.type = "button";
      mark.dataset.eventId = lane.q1Errors[0].eventId;
      mark.dataset.eventIds = lane.q1Errors.map((error) => error.eventId).join(" ");
      mark.dataset.diagnosticMessage = lane.q1Errors.map((error) => error.message).join("\n");
      mark.title = mark.dataset.diagnosticMessage;
      mark.setAttribute("aria-label", `${lane.sequencer || lane.label} · ${lane.q1Errors.length} Q1 error${lane.q1Errors.length === 1 ? "" : "s"}`);
      mark.addEventListener("click", (event) => handlers?.onBlockClick?.({}, mark, event));
      label.appendChild(mark);
    }
    row.appendChild(label);

    const track = element("div", `lane-track lane-track-${lane.kind || "default"}`);
    if (lane.acquisitionTracks > 1) track.classList.add("has-scope");
    if (row.style.minHeight) track.style.minHeight = row.style.minHeight;
    const selection = renderSelectionOverlay(lane.selectionRange === undefined ? model.selectionRange : lane.selectionRange, model.viewport);
    if (selection) {
      track.appendChild(selection);
    }
    for (const annotation of laneAnnotations(lane, model)) {
      const annotationNode = renderLaneAnnotation(annotation, lane, model.viewport, handlers, layout);
      if (annotationNode) {
        track.appendChild(annotationNode);
      }
    }
    for (const block of lane.blocks || []) {
      const blockNode = renderBlock(block, model.viewport, handlers);
      if (blockNode) {
        track.appendChild(blockNode);
        renderParameterMarkers(track, block, blockNode, model, handlers);
      }
    }
    row.appendChild(track);
    return row;
  }

  function parameterChangeLabel(changes) {
    return changes.map((change) => `${change.field}: ${change.previousDisplay} → ${change.valueDisplay}`).join(" · ");
  }

  function renderParameterMarkers(track, block, blockNode, model, handlers) {
    const pending = block.kind === "q1_issue";
    const instruction = model.layout === "instruction";
    const groups = new Map();
    for (const change of block.parameterChanges || []) {
      const key = pending ? change.setterIssueId || change.setterEventId : change.applyTime;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(change);
    }
    for (const changes of groups.values()) {
      const time = pending ? changes[0].setterTime : changes[0].applyTime;
      const index = Number(block.start) + (pending ? Number(block.duration) || 0 : 0);
      if (!instruction && !Number.isFinite(time)) continue;
      const point = timePointStyle(instruction ? index : time, model.viewport);
      if (!point || point.clipped) continue;
      const node = element("button", "timeline-parameter-marker is-time-unknown");
      node.type = "button";
      node.dataset.parameterRole = pending ? "pending" : "applied";
      node.dataset.eventId = blockNode.dataset.eventId;
      node.dataset.eventIds = blockNode.dataset.eventIds || blockNode.dataset.eventId;
      node.dataset.parameterChanges = JSON.stringify(changes);
      if (Number.isFinite(time)) node.dataset.parameterTime = String(time);
      if (instruction) node.dataset.instructionIndex = String(index);
      node.style.left = `${point.leftPercent}%`;
      node.title = `${pending ? "Q1 setting complete" : "RT parameter apply"}${Number.isFinite(time) ? ` at ${time} ns` : " · time unknown"}\n${parameterChangeLabel(changes)}`;
      node.setAttribute("aria-label", node.title);
      const select = (event) => handlers.onBlockClick?.(block, blockNode, event);
      node.addEventListener("click", select);
      node.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          select(event);
        }
      });
      track.appendChild(node);
    }
  }

  function feedbackEndpointAnchorY(rowHeight) {
    return rowHeight - 9.5;
  }

  function renderFeedbackConnectorPath(annotation, model, laneGeometryById, handlers) {
    const fromLane = laneGeometryById.get(annotation.fromLaneId);
    const toLane = laneGeometryById.get(annotation.toLaneId);
    const fromPoint = timePointStyle(annotation.fromTime, model.viewport);
    const toPoint = timePointStyle(annotation.toTime, model.viewport);
    if (!fromLane || !toLane || !fromPoint || !toPoint) {
      return undefined;
    }
    const y1 = fromLane.top + feedbackEndpointAnchorY(fromLane.height) - fromLane.feedbackOffset;
    const y2 = toLane.top + feedbackEndpointAnchorY(toLane.height) - toLane.feedbackOffset;
    const x1 = fromPoint.leftPercent;
    const x2 = toPoint.leftPercent;
    const direction = Math.sign(y2 - y1 || 1);
    const controlOffset = Math.max(Math.abs(y2 - y1) * 0.45, 12);
    const controlY1 = y1 + direction * controlOffset;
    const controlY2 = y2 - direction * controlOffset;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("class", `feedback-connector${annotation.selected ? " is-selected" : ""}`);
    path.setAttribute("d", `M ${x1} ${y1} C ${x1} ${controlY1} ${x2} ${controlY2} ${x2} ${y2}`);
    path.setAttribute("aria-label", annotation.label || annotation.title || "Feedback flow");
    if (!toPoint.clipped) path.setAttribute("marker-end", "url(#feedback-bridge-arrow)");
    path.setAttribute("vector-effect", "non-scaling-stroke");
    installAnnotationInteractions(path, annotation, handlers);
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

  function renderAnnotations(lanesNode, model, handlers, layouts) {
    const crossAnnotations = (model.annotations || []).filter((annotation) => annotation.type === "feedback-cross");
    if (!crossAnnotations.length) {
      return;
    }
    const visibleLanes = visibleTimelineLanes(model);
    const rowHeight = Number(model.rowHeight) || 36;
    const rows = new Map(Array.from(lanesNode.children).filter((node) => node.dataset.lane).map((node) => [node.dataset.lane, node]));
    const laneGeometryById = new Map();
    let totalHeight = 0;
    for (const lane of visibleLanes) {
      const row = rows.get(lane.id);
      const layout = layouts ? layouts.get(lane.id) : loopAnnotationLayout(laneAnnotations(lane, model), model.viewport);
      const feedbackOffset = Number(row?.dataset.feedbackOffset) || 0;
      const height = row && row.offsetHeight || rowHeight + layout.offset + feedbackOffset;
      const top = row && row.offsetHeight ? row.offsetTop : totalHeight;
      laneGeometryById.set(lane.id, { top, height, feedbackOffset });
      totalHeight = top + height;
    }
    const layer = element("div", "timeline-connector-layer");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "feedback-connector-svg");
    svg.setAttribute("viewBox", `0 0 100 ${Math.max(totalHeight, rowHeight)}`);
    svg.setAttribute("preserveAspectRatio", "none");
    renderFeedbackConnectorDefs(svg);
    for (const annotation of crossAnnotations) {
      const path = renderFeedbackConnectorPath(annotation, model, laneGeometryById, handlers);
      if (path) {
        layer.appendChild(svg);
        svg.appendChild(path);
      }
    }
    if (svg.childNodes.length) {
      lanesNode.appendChild(layer);
    }
  }

  const issueConnections = new WeakMap();
  let issueConnectionSequence = 0;

  function connectionAnchor(node, bounds, stageBounds, center = false) {
    if (!node.getClientRects().length) return;
    const rect = node.getBoundingClientRect();
    const track = node.closest(".lane-track").getBoundingClientRect();
    const label = node.closest(".timeline-row").querySelector(".lane-label").getBoundingClientRect();
    const x = rect.left + (center ? rect.width / 2 : 0);
    if (!rect.width || !rect.height || x < Math.max(track.left, label.right, stageBounds.left) - 0.5 ||
        x > Math.min(track.right, stageBounds.right) || rect.bottom < stageBounds.top || rect.top > stageBounds.bottom) return;
    return { x: x - bounds.left, top: rect.top - bounds.top, bottom: rect.bottom - bounds.top };
  }

  function renderIssueConnections(rootNode, selectedEventId) {
    issueConnections.get(rootNode)?.();
    if (selectedEventId === undefined || selectedEventId === null) return;
    const stage = rootNode.querySelector(".shared-timeline-stage");
    const lanes = stage && stage.querySelector(".timeline-lanes");
    if (!lanes) return;
    const ids = (node) => (node.dataset.eventIds || node.dataset.eventId || "").split(/\s+/);
    const selected = String(selectedEventId);
    const blocks = Array.from(lanes.querySelectorAll(".timeline-block[data-event-id]"));
    const targetsById = new Map();
    for (const node of blocks.filter((block) => block.dataset.issueMarker !== "true")) {
      for (const id of ids(node)) {
        if (!targetsById.has(id)) targetsById.set(id, []);
        targetsById.get(id).push(node);
      }
    }
    const pairs = [];
    for (const issue of blocks.filter((node) => node.dataset.issueMarker === "true")) {
      const targets = new Set((issue.dataset.rtEventIds || "").split(/\s+/).flatMap((id) => targetsById.get(id) || []));
      const issueSelected = ids(issue).includes(selected);
      for (const target of targets) {
        if (issueSelected || ids(target).includes(selected)) {
          pairs.push([issue, target]);
        }
      }
    }
    if (!pairs.length) return;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "timeline-issue-connections issue-connector-layer");
    svg.setAttribute("aria-hidden", "true");
    const arrowId = `q1-issue-arrow-${++issueConnectionSequence}`;
    const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
    for (const [name, value] of Object.entries({ id: arrowId, viewBox: "0 0 6 6", refX: "6", refY: "3", markerWidth: "6", markerHeight: "6", markerUnits: "userSpaceOnUse", orient: "auto" })) {
      marker.setAttribute(name, value);
    }
    const arrow = document.createElementNS("http://www.w3.org/2000/svg", "path");
    arrow.setAttribute("class", "issue-connector-arrow");
    arrow.setAttribute("d", "M0 0 6 3 0 6Z");
    marker.appendChild(arrow);
    defs.appendChild(marker);
    lanes.appendChild(svg);
    let frame;
    let observer;
    const cleanup = () => {
      if (frame !== undefined) root.cancelAnimationFrame(frame);
      observer?.disconnect();
      stage.removeEventListener("scroll", schedule);
      root.removeEventListener("resize", schedule);
      svg.remove();
      issueConnections.delete(rootNode);
    };
    function draw() {
      frame = undefined;
      if (!stage.isConnected) { cleanup(); return; }
      const bounds = lanes.getBoundingClientRect();
      const stageBounds = stage.getBoundingClientRect();
      const instruction = Boolean(stage.closest(".instruction-view"));
      svg.setAttribute("viewBox", `0 0 ${bounds.width} ${bounds.height}`);
      const anchor = (node) => {
        if (node.dataset.startClipped === "true" ||
            !instruction && node.dataset.connectionStartKnown === "false") return;
        return connectionAnchor(node, bounds, stageBounds);
      };
      svg.replaceChildren(defs);
      for (const [issue, target] of pairs) {
        const from = anchor(issue), to = anchor(target);
        if (!from || !to) continue;
        const downward = to.top > from.top;
        const y1 = downward ? from.bottom : from.top;
        const y2 = downward ? to.top : to.bottom;
        const bend = (y2 - y1) / 2;
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("class", "issue-connector");
        path.setAttribute("data-issue-event-id", issue.dataset.eventId);
        const rtIds = new Set((issue.dataset.rtEventIds || "").split(/\s+/));
        path.setAttribute("data-rt-event-ids", ids(target).filter((id) => rtIds.has(id)).join(" "));
        path.setAttribute("d", `M ${from.x} ${y1} C ${from.x} ${y1 + bend}, ${to.x} ${y2 - bend}, ${to.x} ${y2}`);
        path.setAttribute("vector-effect", "non-scaling-stroke");
        path.setAttribute("marker-end", `url(#${arrowId})`);
        svg.appendChild(path);
        const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        dot.setAttribute("class", "issue-connector-origin");
        dot.setAttribute("cx", String(from.x));
        dot.setAttribute("cy", String(y1));
        dot.setAttribute("r", "2");
        svg.appendChild(dot);
      }
    }
    function schedule() {
      if (frame === undefined) frame = root.requestAnimationFrame(draw);
    }
    issueConnections.set(rootNode, cleanup);
    stage.addEventListener("scroll", schedule, { passive: true });
    root.addEventListener("resize", schedule);
    if (typeof root.ResizeObserver === "function") {
      observer = new root.ResizeObserver(schedule);
      for (const node of new Set([stage, lanes, ...pairs.flat()])) observer.observe(node);
    }
    draw();
  }

  const parameterConnections = new WeakMap();

  function renderParameterConnections(rootNode, selectedEventId, cursorTime) {
    const stage = rootNode.querySelector(".shared-timeline-stage");
    const current = parameterConnections.get(rootNode);
    if (current && current.stage === stage && current.selectedEventId === selectedEventId) {
      current.update(cursorTime);
      return;
    }
    current?.cleanup();
    const lanes = stage && stage.querySelector(".timeline-lanes");
    if (!lanes) return;
    const records = Array.from(lanes.querySelectorAll(".timeline-parameter-marker")).map((node) => ({
      node, changes: JSON.parse(node.dataset.parameterChanges), time: Number(node.dataset.parameterTime),
      ids: (node.dataset.eventIds || node.dataset.eventId).split(/\s+/),
    }));
    if (!records.length) return;
    const update = (time) => {
      for (const { node, time: at } of records) {
        const known = Number.isFinite(time) && Number.isFinite(at);
        node.classList.toggle("is-applied", node.dataset.parameterRole === "applied" && known && time >= at);
        node.classList.toggle("is-future", known && time < at);
        node.classList.toggle("is-time-unknown", !known);
      }
    };
    const pairs = [];
    for (const pending of records.filter(({ node, ids }) => node.dataset.parameterRole === "pending" && ids.includes(String(selectedEventId)))) {
      for (const applied of records.filter(({ node }) => node.dataset.parameterRole === "applied")) {
        const changes = pending.changes.filter((change) => (change.applyEventIds || []).some((id) => applied.ids.includes(String(id))) &&
          applied.changes.some((other) => other.setterEventId === change.setterEventId && other.field === change.field));
        if (changes.length) pairs.push({ pending: pending.node, applied: applied.node, changes });
      }
    }
    let svg;
    let frame;
    let observer;
    const cleanup = () => {
      if (frame !== undefined) root.cancelAnimationFrame(frame);
      observer?.disconnect();
      stage.removeEventListener("scroll", schedule);
      root.removeEventListener("resize", schedule);
      svg?.remove();
      parameterConnections.delete(rootNode);
    };
    function draw() {
      frame = undefined;
      if (!stage.isConnected) { cleanup(); return; }
      const bounds = lanes.getBoundingClientRect();
      const stageBounds = stage.getBoundingClientRect();
      svg.setAttribute("viewBox", `0 0 ${bounds.width} ${bounds.height}`);
      svg.replaceChildren();
      for (const { pending, applied, changes } of pairs) {
        const from = connectionAnchor(pending, bounds, stageBounds, true);
        const to = connectionAnchor(applied, bounds, stageBounds, true);
        if (!from || !to) continue;
        const y1 = (from.top + from.bottom) / 2, y2 = (to.top + to.bottom) / 2;
        const midY = (y1 + y2) / 2;
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("class", "parameter-connector");
        path.setAttribute("data-setter-event-id", changes[0].setterEventId);
        path.setAttribute("data-rt-event-ids", applied.dataset.eventIds);
        path.setAttribute("d", `M ${from.x} ${y1} C ${from.x} ${midY}, ${to.x} ${midY}, ${to.x} ${y2}`);
        path.setAttribute("vector-effect", "non-scaling-stroke");
        svg.appendChild(path);
        const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
        label.setAttribute("class", "parameter-connection-label");
        label.setAttribute("x", String((from.x + to.x) / 2));
        label.setAttribute("y", String(midY - 5));
        label.setAttribute("text-anchor", "middle");
        label.textContent = parameterChangeLabel(changes);
        svg.appendChild(label);
      }
    }
    function schedule() {
      if (frame === undefined) frame = root.requestAnimationFrame(draw);
    }
    parameterConnections.set(rootNode, { stage, selectedEventId, update, cleanup });
    update(cursorTime);
    if (pairs.length) {
      svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "parameter-connector-layer");
      svg.setAttribute("aria-hidden", "true");
      lanes.appendChild(svg);
      stage.addEventListener("scroll", schedule, { passive: true });
      root.addEventListener("resize", schedule);
      if (typeof root.ResizeObserver === "function") {
        observer = new root.ResizeObserver(schedule);
        for (const node of new Set([stage, lanes, ...pairs.flatMap(({ pending, applied }) => [pending, applied])])) observer.observe(node);
      }
      draw();
    }
  }

  function renderTimeline(rootNode, model, handlers = {}) {
    renderIssueConnections(rootNode, undefined);
    parameterConnections.get(rootNode)?.cleanup();
    const root = rootNode;
    const section = element("section", "shared-timeline-stage timeline-stage");
    if (Number.isFinite(model.plotWidth)) section.style.setProperty("--timeline-time-width", `${model.plotWidth}px`);
    const header = element("div", "stage-header");
    const heading = element("div", "stage-heading");
    heading.appendChild(element("div", "stage-title", model.title || "Timeline"));
    heading.appendChild(element("div", "stage-total", model.subtitle || ""));
    header.appendChild(heading);
    section.appendChild(header);
    section.appendChild(renderRuler(model));

    const lanes = element("div", "timeline-lanes");
    const layouts = new Map((model.lanes || []).map((lane) => [lane.id, loopAnnotationLayout(laneAnnotations(lane, model), model.viewport)]));
    for (const lane of model.lanes || []) {
      lanes.appendChild(renderLane(lane, model, handlers, layouts.get(lane.id)));
    }
    section.appendChild(lanes);
    root.replaceChildren(section);
    renderAnnotations(lanes, model, handlers, layouts);
    return section;
  }

  const api = {
    element,
    button,
    blockStyle,
    normalizeViewport,
    panViewport,
    loopAnnotationLayout,
    renderAnnotations,
    renderIssueConnections,
    renderParameterConnections,
    renderTimeline,
  };

  root.q1lensSharedTimeline = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
