(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.qbsTimelineModel = api;
})(typeof globalThis !== "undefined" ? globalThis : window, function () {
  const LOGICAL_CONTROL_FLOW_STEP_DURATION = 1e-9;
  const BLOCK_STACK_TOP_PX = 7;
  const BLOCK_STACK_HEIGHT_PX = 22;
  const BLOCK_STACK_GAP_PX = 4;
  const BLOCK_STACK_BOTTOM_PX = 7;
  const BLOCK_STACK_EPSILON_PERCENT = 0.001;

  function cleanNumber(value) {
    if (!Number.isFinite(value)) {
      return 0;
    }
    return Math.abs(value) < 1e-15 ? 0 : value;
  }

  function formatScaled(value, suffix) {
    const rounded = Math.round(value * 100) / 100;
    if (Number.isInteger(rounded)) {
      return `${rounded} ${suffix}`;
    }
    return `${rounded.toFixed(2).replace(/0+$/, "").replace(/\.$/, "")} ${suffix}`;
  }

  function formatDuration(seconds) {
    const value = cleanNumber(seconds);
    const abs = Math.abs(value);
    if (abs < 1e-6) {
      return formatScaled(value * 1e9, "ns");
    }
    if (abs < 1e-3) {
      return formatScaled(value * 1e6, "us");
    }
    if (abs < 1) {
      return formatScaled(value * 1e3, "ms");
    }
    return formatScaled(value, "s");
  }

  function pct(value, total) {
    if (!total) {
      return 0;
    }
    return Math.round((value / total) * 100000) / 1000;
  }

  function operationById(ir) {
    const map = new Map();
    for (const operation of ir.operations || []) {
      map.set(operation.id, operation);
    }
    return map;
  }

  function operationByOperationId(ir) {
    const map = new Map();
    for (const operation of ir.operations || []) {
      if (operation.operation_id) {
        map.set(operation.operation_id, operation);
      }
    }
    return map;
  }

  function controlBlockBySchedulableId(ir) {
    const map = new Map();
    for (const block of ir.control_flow_blocks || []) {
      if (block.schedulable_id) {
        map.set(block.schedulable_id, block);
      }
    }
    return map;
  }

  function symbolicValueById(ir) {
    const map = new Map();
    for (const value of ir.symbolic_values || []) {
      map.set(value.id, value);
    }
    return map;
  }

  function provenanceStartLine(mapping) {
    return mapping?.q1asm_line_start || mapping?.line || 1;
  }

  function provenanceEndLine(mapping) {
    return mapping?.q1asm_line_end || mapping?.line || provenanceStartLine(mapping);
  }

  function positiveDuration(value) {
    return Number.isFinite(value) && value > 0 ? value : undefined;
  }

  function timedSpan(item) {
    if (!item) {
      return undefined;
    }
    const duration = positiveDuration(item.duration);
    if (!duration) {
      return undefined;
    }
    const start = item.abs_time ?? 0;
    return { start, end: start + duration, duration };
  }

  function mergeSpan(current, next) {
    if (!next) {
      return current;
    }
    if (!current) {
      return next;
    }
    const start = Math.min(current.start, next.start);
    const end = Math.max(current.end, next.end);
    return { start, end, duration: end - start };
  }

  function totalDuration(ir, layout) {
    let end = layout?.total || 0;
    for (const operation of ir.operations || []) {
      end = Math.max(end, (operation.abs_time || 0) + (operation.duration || 0));
    }
    for (const pulse of ir.symbolic_pulses || []) {
      end = Math.max(end, (pulse.abs_time || 0) + (pulse.duration || 0));
    }
    for (const block of ir.control_flow_blocks || []) {
      const start = Number.isFinite(block.preview_abs_time) ? block.preview_abs_time : block.abs_time || 0;
      const duration = positiveDuration(block.preview_duration) || block.duration || 0;
      end = Math.max(end, start + duration);
    }
    return end || 1e-9;
  }

  function needsLogicalControlFlowLayout(ir) {
    const controlBlocks = ir.control_flow_blocks || [];
    if (!controlBlocks.length) {
      return false;
    }
    const childOperations = (ir.operations || []).filter((operation) => operation.parent_control_flow_id);
    if (childOperations.length < 2) {
      return false;
    }
    const untimedChildren = childOperations.filter(
      (operation) => !positiveDuration(operation.duration) && !(operation.abs_time > 0),
    );
    return controlBlocks.some((block) => !positiveDuration(block.duration)) && untimedChildren.length >= 2;
  }

  function controlFlowParentId(block, operationsById) {
    return block.parent_control_flow_id || operationsById.get(block.schedulable_id)?.parent_control_flow_id;
  }

  function controlFlowParentById(ir, operationsById) {
    const map = new Map();
    for (const block of ir.control_flow_blocks || []) {
      map.set(block.id, controlFlowParentId(block, operationsById));
    }
    return map;
  }

  function isNestedInControlFlow(parentId, ancestorId, controlParentsById) {
    const seen = new Set();
    let current = parentId;
    while (current && !seen.has(current)) {
      if (current === ancestorId) {
        return true;
      }
      seen.add(current);
      current = controlParentsById.get(current);
    }
    return false;
  }

  function controlFlowOperationSpan(block, operationsById, operationsByOperationId) {
    return timedSpan(operationsById.get(block.schedulable_id))
      || timedSpan(operationsById.get(block.operation_id))
      || timedSpan(operationsByOperationId.get(block.operation_id));
  }

  function controlFlowBodyTiming(ir, block, operationsById, controlParentsById) {
    let span;
    let hasUntimedDescendant = false;
    const descendantOperationKeys = new Set();

    for (const operation of ir.operations || []) {
      if (!isNestedInControlFlow(operation.parent_control_flow_id, block.id, controlParentsById)) {
        continue;
      }
      descendantOperationKeys.add(operation.id);
      if (operation.operation_id) {
        descendantOperationKeys.add(operation.operation_id);
      }
      span = mergeSpan(span, timedSpan(operation));
      if (!positiveDuration(operation.duration) && !(operation.abs_time > 0)) {
        hasUntimedDescendant = true;
      }
    }

    for (const childBlock of ir.control_flow_blocks || []) {
      if (childBlock.id === block.id) {
        continue;
      }
      const parentId = controlFlowParentId(childBlock, operationsById);
      if (isNestedInControlFlow(parentId, block.id, controlParentsById)) {
        span = mergeSpan(span, timedSpan(childBlock));
      }
    }

    for (const pulse of ir.symbolic_pulses || []) {
      if (
        descendantOperationKeys.has(pulse.schedulable_id)
        || descendantOperationKeys.has(pulse.operation_id)
      ) {
        span = mergeSpan(span, timedSpan(pulse));
      }
    }

    return { span, hasUntimedDescendant };
  }

  function controlFlowResolvedSpan(block, viewport, position, operationsById, operationsByOperationId, bodyTiming) {
    const actualDuration = positiveDuration(block.duration);
    const previewDuration = positiveDuration(block.preview_duration);
    const operationSpan = controlFlowOperationSpan(block, operationsById, operationsByOperationId);
    const bodySpan = bodyTiming.span;
    if (previewDuration) {
      return {
        start: Number.isFinite(block.preview_abs_time) ? block.preview_abs_time : block.abs_time ?? 0,
        duration: previewDuration,
        displayDuration: previewDuration,
        expandedDuration: actualDuration || operationSpan?.duration,
        preview: true,
        logical: false,
      };
    }
    if (position && !position.logical) {
      return { start: position.start, duration: position.duration, displayDuration: position.duration, logical: false };
    }
    if (actualDuration) {
      return { start: block.abs_time ?? 0, duration: actualDuration, displayDuration: actualDuration, logical: false };
    }
    if (operationSpan) {
      return { start: operationSpan.start, duration: operationSpan.duration, displayDuration: operationSpan.duration, logical: false };
    }
    if (bodySpan && (!position?.logical || !bodyTiming.hasUntimedDescendant)) {
      return { start: bodySpan.start, duration: bodySpan.duration, displayDuration: bodySpan.duration, logical: false };
    }
    if (position) {
      return {
        start: position.start,
        duration: position.duration,
        displayDuration: position.duration,
        logical: Boolean(position.logical),
      };
    }
    if (bodySpan) {
      return { start: bodySpan.start, duration: bodySpan.duration, displayDuration: bodySpan.duration, logical: false };
    }
    return {
      start: block.abs_time ?? 0,
      duration: viewportDuration(viewport) * 0.02,
      displayDuration: block.duration || 0,
      logical: false,
    };
  }

  function controlFlowDurationLabel(resolved, isLogicalUntimed) {
    if (isLogicalUntimed) {
      return "source-order first iteration";
    }
    if (
      resolved.preview
      && positiveDuration(resolved.expandedDuration)
      && Math.abs(resolved.expandedDuration - resolved.displayDuration) > 1e-15
    ) {
      return `body ${formatDuration(resolved.displayDuration)} · total ${formatDuration(resolved.expandedDuration)}`;
    }
    return formatDuration(resolved.displayDuration);
  }

  function controlFlowLabel(block) {
    const iteration = block.iteration || {};
    const variable = readableIterationVariable(iteration.variable);
    const count = formatIterationCount(block.repetitions ?? iteration.count);
    if (variable && count) {
      const prefix = block.kind === "sweep" ? "Sweep" : "Loop";
      return `${prefix} ${variable} x${count}`;
    }
    return block.label || block.id;
  }

  function readableIterationVariable(value) {
    if (typeof value !== "string") {
      return "";
    }
    const variables = value.split(",").map((part) => part.trim()).filter(Boolean);
    const readable = variables.filter((variable) => !isGeneratedQbloxVariable(variable));
    return readable.join(", ");
  }

  function isGeneratedQbloxVariable(value) {
    return /^Var[0-9a-fA-F]{32}$/.test(value);
  }

  function formatIterationCount(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      return undefined;
    }
    return Number.isInteger(value) ? String(value) : `${value}`;
  }

  function controlFlowDetail(block, resolved, isLogicalUntimed) {
    const iteration = block.iteration || {};
    const variable = readableIterationVariable(iteration.variable);
    if (variable && typeof iteration.source === "string") {
      return `${variable} in ${iteration.source}`;
    }
    if (isLogicalUntimed) {
      return "first iteration";
    }
    if (
      resolved.preview
      && positiveDuration(resolved.expandedDuration)
      && Math.abs(resolved.expandedDuration - resolved.displayDuration) > 1e-15
    ) {
      return `body ${formatDuration(resolved.displayDuration)}`;
    }
    return formatDuration(resolved.displayDuration);
  }

  function logicalControlFlowLayout(ir) {
    if (!needsLogicalControlFlowLayout(ir)) {
      return undefined;
    }
    const operationsById = operationById(ir);
    const controlBlocksBySchedulableId = controlBlockBySchedulableId(ir);
    const childrenByParent = new Map();
    const topLevelItems = [];
    const seenControlIds = new Set();

    function addChild(parentId, item) {
      if (!parentId) {
        topLevelItems.push(item);
        return;
      }
      const children = childrenByParent.get(parentId) || [];
      children.push(item);
      childrenByParent.set(parentId, children);
    }

    for (const operation of ir.operations || []) {
      const controlBlock = controlBlocksBySchedulableId.get(operation.id);
      if (controlBlock) {
        seenControlIds.add(controlBlock.id);
        addChild(operation.parent_control_flow_id, { type: "control", block: controlBlock, operation });
      } else {
        addChild(operation.parent_control_flow_id, { type: "operation", operation });
      }
    }

    for (const block of ir.control_flow_blocks || []) {
      if (seenControlIds.has(block.id)) {
        continue;
      }
      addChild(controlFlowParentId(block, operationsById), { type: "control", block, operation: undefined });
    }

    const positions = new Map();

    function layoutOperation(operation, start) {
      const duration = positiveDuration(operation.duration) || LOGICAL_CONTROL_FLOW_STEP_DURATION;
      positions.set(operation.id, {
        start,
        duration,
        logical: !positiveDuration(operation.duration),
      });
      return duration;
    }

    function layoutControl(block, operation, start) {
      const bodyDuration = layoutItems(childrenByParent.get(block.id) || [], start);
      const duration = positiveDuration(block.duration) || bodyDuration || LOGICAL_CONTROL_FLOW_STEP_DURATION;
      const logical = !positiveDuration(block.duration);
      positions.set(block.id, { start, duration, logical });
      if (operation?.id) {
        positions.set(operation.id, { start, duration, logical });
      }
      return duration;
    }

    function layoutItems(items, start) {
      let cursor = start;
      for (const item of items) {
        const duration = item.type === "control"
          ? layoutControl(item.block, item.operation, cursor)
          : layoutOperation(item.operation, cursor);
        cursor += duration;
      }
      return cursor - start;
    }

    const total = layoutItems(topLevelItems, 0);
    return total > 0 ? { positions, total } : undefined;
  }

  function normalizeViewport(total, requested) {
    const full = { start: 0, end: total };
    if (!requested || !Number.isFinite(requested.start) || !Number.isFinite(requested.end)) {
      return full;
    }
    let start = requested.start;
    let end = requested.end;
    if (start > end) {
      [start, end] = [end, start];
    }
    start = Math.max(0, Math.min(total, start));
    end = Math.max(0, Math.min(total, end));
    const minimumSpan = Math.max(total * 0.01, 1e-12);
    if (end - start < minimumSpan) {
      const center = (start + end) / 2;
      start = center - minimumSpan / 2;
      end = center + minimumSpan / 2;
    }
    if (start < 0) {
      end = Math.min(total, end - start);
      start = 0;
    }
    if (end > total) {
      start = Math.max(0, start - (end - total));
      end = total;
    }
    return { start, end };
  }

  function viewportDuration(viewport) {
    return Math.max(viewport.end - viewport.start, 1e-15);
  }

  function panViewport(total, current, deltaRatio) {
    const viewport = normalizeViewport(total, current);
    if (!Number.isFinite(deltaRatio) || deltaRatio === 0) {
      return viewport;
    }
    const span = Math.min(total, viewportDuration(viewport));
    if (!Number.isFinite(span) || span <= 0 || span >= total) {
      return normalizeViewport(total, viewport);
    }
    let start = viewport.start - deltaRatio * span;
    let end = start + span;
    if (start < 0) {
      start = 0;
      end = span;
    }
    if (end > total) {
      end = total;
      start = total - span;
    }
    return normalizeViewport(total, {
      start,
      end,
    });
  }

  function viewportSummary(total, viewport) {
    return {
      start: viewport.start,
      end: viewport.end,
      startLabel: formatDuration(viewport.start),
      endLabel: formatDuration(viewport.end),
      durationLabel: formatDuration(viewportDuration(viewport)),
      isZoomed: viewport.start > 0 || viewport.end < total,
    };
  }

  function ticks(viewport) {
    const span = viewportDuration(viewport);
    return [0, 0.25, 0.5, 0.75, 1].map((ratio) => ({
      leftPercent: Math.round(ratio * 100000) / 1000,
      label: formatDuration(viewport.start + span * ratio),
    }));
  }

  function blockStyle(start, duration, viewport) {
    const span = viewportDuration(viewport);
    const end = start + duration;
    const visibleStart = Math.max(start, viewport.start);
    const visibleEnd = Math.min(end, viewport.end);
    if (visibleEnd <= visibleStart) {
      return undefined;
    }
    return {
      leftPercent: pct(visibleStart - viewport.start, span),
      widthPercent: pct(visibleEnd - visibleStart, span),
    };
  }

  function operationBlocks(ir, viewport, layout) {
    return (ir.operations || []).flatMap((operation) => {
      const position = layout?.positions.get(operation.id);
      const start = position?.start ?? operation.abs_time ?? 0;
      const actualDuration = positiveDuration(operation.duration);
      const duration = position?.duration || actualDuration || viewportDuration(viewport) * 0.02;
      const style = blockStyle(start, duration, viewport);
      if (!style) {
        return [];
      }
      const isLogicalUntimed = Boolean(position?.logical && !actualDuration);
      const semanticOperationId = operation.operation_id || operation.id;
      const sourceSchedulableId = operation.schedulable_id || semanticOperationId;
      return [{
        id: operation.id,
        type: "operation",
        operationId: semanticOperationId,
        schedulableId: sourceSchedulableId,
        label: operation.label || operation.id,
        detail: isLogicalUntimed ? "untimed" : formatDuration(operation.duration || 0),
        startSeconds: start,
        durationSeconds: operation.duration || 0,
        parentControlFlowId: operation.parent_control_flow_id,
        depth: operation.depth,
        scheduleSourceMessage: scheduleSourceMessage({
          id: operation.id,
          operationId: semanticOperationId,
          schedulableId: sourceSchedulableId,
        }),
        startLabel: formatDuration(start),
        durationLabel: isLogicalUntimed ? "untimed source-order block" : formatDuration(operation.duration || 0),
        ...style,
      }];
    });
  }

  function controlFlowOperationIds(ir) {
    return new Set((ir.control_flow_blocks || []).map((block) => block.schedulable_id).filter(Boolean));
  }

  function operationLanes(ir, viewport, layout) {
    const lanes = new Map();
    const suppressedOperationIds = controlFlowOperationIds(ir);
    for (const block of operationBlocks(ir, viewport, layout)) {
      if (suppressedOperationIds.has(block.id)) {
        continue;
      }
      const operation = findOperation(ir, block.operationId || block.id);
      const label = operation ? groupLabelForOperation(operation) : "Schedule operations";
      const blocks = lanes.get(label) || [];
      blocks.push(block);
      lanes.set(label, blocks);
    }
    return [...lanes.entries()].map(([label, blocks]) => ({ label, kind: "operation", blocks }));
  }

  function controlFlowBlocks(ir, viewport, layout) {
    const operationsById = operationById(ir);
    const operationsByOperationId = operationByOperationId(ir);
    const controlParentsById = controlFlowParentById(ir, operationsById);
    return (ir.control_flow_blocks || []).flatMap((block) => {
      const position = layout?.positions.get(block.id);
      const actualDuration = positiveDuration(block.duration);
      const bodyTiming = controlFlowBodyTiming(ir, block, operationsById, controlParentsById);
      const resolved = controlFlowResolvedSpan(
        block,
        viewport,
        position,
        operationsById,
        operationsByOperationId,
        bodyTiming,
      );
      const start = resolved.start;
      const duration = resolved.duration;
      const style = blockStyle(start, duration, viewport);
      if (!style) {
        return [];
      }
      const visualKind = block.kind === "loop" || block.kind === "sweep" ? block.kind : "control-flow";
      const isLogicalUntimed = Boolean(resolved.logical && !actualDuration);
      const durationLabel = controlFlowDurationLabel(resolved, isLogicalUntimed);
      return [{
        id: block.id,
        type: "control-flow",
        visualKind,
        operationId: block.schedulable_id || block.operation_id || block.id,
        schedulableId: block.schedulable_id,
        label: controlFlowLabel(block),
        detail: controlFlowDetail(block, resolved, isLogicalUntimed),
        parentControlFlowId: block.parent_control_flow_id,
        depth: block.depth,
        startLabel: formatDuration(start),
        durationLabel,
        scheduleSourceMessage: scheduleSourceMessage({
          id: block.id,
          operationId: block.operation_id,
          schedulableId: block.schedulable_id,
        }),
        ...style,
      }];
    });
  }

  function controlFlowLanes(ir, viewport, expandedGroupIds, layout) {
    return controlFlowBlocks(ir, viewport, layout).map((block) => (
      {
        label: block.label,
        kind: "control-flow",
        groupId: block.id,
        parentGroupId: block.parentControlFlowId,
        depth: block.depth,
        expanded: false,
        expandable: false,
        childrenCount: 0,
        blocks: [block],
      }
    ));
  }

  function deriveOperationTarget(operation) {
    const text = `${operation.label || ""} ${operation.id || ""}`;
    const matches = [...text.matchAll(/q\d+/gi)].map((match) => match[0].toLowerCase());
    const unique = [...new Set(matches)];
    if (unique.length === 0) {
      return "";
    }
    return unique.join("_");
  }

  function groupLabelForOperation(operation) {
    const target = deriveOperationTarget(operation);
    return target ? `Schedule / ${target}` : "Schedule operations";
  }

  function targetFromLaneLabel(label) {
    const schedulePrefix = "Schedule / ";
    if (String(label || "").startsWith(schedulePrefix)) {
      return String(label).slice(schedulePrefix.length);
    }
    const matches = [...String(label || "").matchAll(/q\d+/gi)].map((match) => match[0].toLowerCase());
    const unique = [...new Set(matches)];
    return unique.join("_") || "other";
  }

  function targetGroupId(target) {
    return `target:${target || "other"}`;
  }

  function inlineQ1PreviewLaneId(label) {
    return `inline-q1:${label}`;
  }

  function inlineQ1PreviewBlocksForLane(lane) {
    return lane.blocks
      .filter((block) => block.inlineQ1Preview)
      .flatMap((block) => Array.isArray(block.inlineQ1Preview) ? block.inlineQ1Preview : [block.inlineQ1Preview]);
  }

  function q1SequencerLabel(blocks) {
    return [...new Set(blocks.map((block) => block.sequencer).filter(Boolean))].join(", ");
  }

  function withInlineQ1PreviewLaneMetadata(lane) {
    const blocks = inlineQ1PreviewBlocksForLane(lane);
    if (!blocks.length) {
      return lane;
    }
    return {
      ...lane,
      inlineQ1PreviewLaneId: inlineQ1PreviewLaneId(lane.label),
      inlineQ1PreviewLabel: q1SequencerLabel(blocks),
    };
  }

  function inlineQ1PreviewLaneForChildLane(lane, expandedInlineQ1LaneIds) {
    const expanded = new Set(expandedInlineQ1LaneIds || []);
    if (!lane.inlineQ1PreviewLaneId || !expanded.has(lane.inlineQ1PreviewLaneId)) {
      return undefined;
    }
    const blocks = inlineQ1PreviewBlocksForLane(lane);
    if (!blocks.length) {
      return undefined;
    }
    const sequencers = q1SequencerLabel(blocks);
    return {
      label: sequencers || "Sequencer",
      title: sequencers,
      kind: "q1",
      parentGroupId: lane.parentGroupId,
      depth: (lane.depth || 1) + 1,
      sourceLaneId: lane.inlineQ1PreviewLaneId,
      sourceLaneLabel: lane.label,
      sourceBlockIds: blocks.map((block) => block.sourceBlockId),
      blocks,
    };
  }

  function groupTimelineLanes(lanes, expandedGroupIds, expandedInlineQ1LaneIds) {
    const expanded = new Set(expandedGroupIds || []);
    const expandedInlineQ1Lanes = new Set(expandedInlineQ1LaneIds || []);
    const groups = new Map();
    for (const lane of lanes) {
      const target = targetFromLaneLabel(lane.label);
      const groupId = targetGroupId(target);
      if (!groups.has(groupId)) {
        groups.set(groupId, {
          target,
          groupId,
          operationBlocks: [],
          childLanes: [],
        });
      }
      const group = groups.get(groupId);
      if (lane.kind === "operation") {
        group.operationBlocks.push(...lane.blocks);
      } else {
        group.childLanes.push(lane);
      }
    }

    return [...groups.values()].flatMap((group) => {
      const isExpanded = expanded.has(group.groupId);
      const parent = {
        label: group.target === "other" ? "Schedule" : `Schedule / ${group.target}`,
        kind: "target",
        groupId: group.groupId,
        target: group.target,
        expanded: isExpanded,
        expandable: group.childLanes.length > 0,
        childrenCount: group.childLanes.length,
        blocks: group.operationBlocks,
      };
      if (!isExpanded) {
        return [parent];
      }
      const visibleChildLanes = [];
      for (const lane of group.childLanes) {
        const childLane = {
          ...lane,
          parentGroupId: group.groupId,
          depth: 1,
          inlineQ1PreviewExpanded: lane.inlineQ1PreviewLaneId ? expandedInlineQ1Lanes.has(lane.inlineQ1PreviewLaneId) : undefined,
        };
        visibleChildLanes.push(childLane);
        const inlineQ1Lane = inlineQ1PreviewLaneForChildLane(childLane, expandedInlineQ1LaneIds);
        if (inlineQ1Lane) {
          visibleChildLanes.push(inlineQ1Lane);
        }
      }
      return [
        parent,
        ...visibleChildLanes,
      ];
    });
  }

  function blockRightPercent(block) {
    return (block.leftPercent || 0) + Math.max(block.widthPercent || 0, 0);
  }

  function stackedBlockTopPx(stackIndex) {
    return BLOCK_STACK_TOP_PX + stackIndex * (BLOCK_STACK_HEIGHT_PX + BLOCK_STACK_GAP_PX);
  }

  function stackedTrackHeightPx(stackCount) {
    return (
      BLOCK_STACK_TOP_PX
      + stackCount * BLOCK_STACK_HEIGHT_PX
      + Math.max(stackCount - 1, 0) * BLOCK_STACK_GAP_PX
      + BLOCK_STACK_BOTTOM_PX
    );
  }

  function stackLaneBlocks(lane) {
    const blocks = lane.blocks || [];
    if (blocks.length < 2) {
      return lane;
    }
    const ordered = blocks
      .map((block, index) => ({
        block,
        index,
        left: block.leftPercent || 0,
        right: blockRightPercent(block),
      }))
      .sort((a, b) => (
        a.left - b.left
        || a.index - b.index
      ));
    const stackRightEdges = [];
    const stackIndexByBlockIndex = new Map();
    for (const entry of ordered) {
      let stackIndex = stackRightEdges.findIndex(
        (rightEdge) => entry.left >= rightEdge - BLOCK_STACK_EPSILON_PERCENT,
      );
      if (stackIndex === -1) {
        stackIndex = stackRightEdges.length;
      }
      stackRightEdges[stackIndex] = entry.right;
      stackIndexByBlockIndex.set(entry.index, stackIndex);
    }
    if (stackRightEdges.length <= 1) {
      return lane;
    }
    return {
      ...lane,
      trackHeightPx: stackedTrackHeightPx(stackRightEdges.length),
      blocks: blocks.map((block, index) => {
        const stackIndex = stackIndexByBlockIndex.get(index) || 0;
        return {
          ...block,
          stackIndex,
          topPx: stackedBlockTopPx(stackIndex),
        };
      }),
    };
  }

  function firstInstructionRole(mapping) {
    return (mapping?.instruction_roles || []).find((role) => typeof role === "string" && role.length > 0) || "";
  }

  function q1asmLineTextForMapping(ir, mapping) {
    const text = q1asmTextForMapping(ir, mapping);
    if (!text) {
      return "";
    }
    const eventLine = q1asmEventLineForMapping(mapping);
    if (eventLine) {
      return q1asmLineTextAt(text, eventLine);
    }
    const lines = String(text).split(/\r?\n/);
    const start = provenanceStartLine(mapping);
    const end = provenanceEndLine(mapping);
    for (let lineNumber = start; lineNumber <= end; lineNumber += 1) {
      const lineText = (lines[lineNumber - 1] || "").trim();
      if (lineText) {
        return lineText;
      }
    }
    return "";
  }

  function q1asmLineTextAt(text, lineNumber) {
    if (!lineNumber) {
      return "";
    }
    return (String(text).split(/\r?\n/)[lineNumber - 1] || "").trim();
  }

  function q1asmOpcode(lineText) {
    const uncommented = String(lineText || "").split("#")[0].trim();
    return uncommented.split(/\s+/)[0] || "";
  }

  function q1CommandAccentColor(instruction) {
    const op = String(instruction || "").toLowerCase();
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
    if (op === "feedback_pop" || op === "feedback_com" || op.startsWith("fb_")) {
      return "#ff6fb3";
    }
    if (op === "set_mrk" || op === "set_marker") {
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

  function q1asmEventLineForMapping(mapping) {
    const operandMappings = Array.isArray(mapping?.operand_mappings) ? mapping.operand_mappings : [];
    const preferred = operandMappings.find(
      (operand) => typeof operand.line === "number" && isTimedQ1Instruction(operand.instruction),
    );
    if (preferred) {
      return preferred.line;
    }
    const fallback = operandMappings.find((operand) => typeof operand.line === "number");
    return fallback?.line;
  }

  function isTimedQ1Instruction(instruction) {
    const normalized = String(instruction || "").toLowerCase();
    return ["play", "acquire", "wait", "upd_param"].includes(normalized) || normalized.startsWith("acquire_");
  }

  function roundPercent(value) {
    return Math.round(value * 1000) / 1000;
  }

  function q1timelineScalarValue(value) {
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string" && value.trim() !== "") {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : undefined;
    }
    if (value && typeof value === "object") {
      return q1timelineScalarValue(value.value);
    }
    return undefined;
  }

  function q1timelineScalarDisplay(value) {
    if (value && typeof value === "object" && value.display !== undefined && value.display !== null) {
      return String(value.display);
    }
    return "";
  }

  function q1timelineScalarLabel(value, fallbackValue) {
    const display = q1timelineScalarDisplay(value);
    if (display) {
      return /[a-zA-Z]/.test(display) ? display : `${display} ns`;
    }
    const scalar = q1timelineScalarValue(value);
    const fallback = scalar === undefined ? fallbackValue : scalar;
    if (!Number.isFinite(fallback)) {
      return "";
    }
    if (fallback !== 0 && Math.abs(fallback) < 1e-3) {
      return formatDuration(fallback);
    }
    return formatScaled(fallback, "ns");
  }

  function q1timelineEventSequencer(event) {
    return event?.sequencer_id || event?.sequencer || "";
  }

  function q1timelineMappingSequencers(mapping) {
    return new Set([mapping?.sequencer, mapping?.sequencer_id].filter(Boolean));
  }

  function q1timelineEventLine(event) {
    for (const line of [
      event?.source?.line,
      event?.source?.q1asm_line_start,
      event?.line,
      event?.q1asm_line_start,
      event?.q1asm_line,
      event?.line_number,
    ]) {
      if (typeof line === "number" && Number.isFinite(line)) {
        return line;
      }
    }
    return undefined;
  }

  function q1timelineEventRaw(event) {
    return typeof event?.source?.raw === "string" ? event.source.raw.trim() : "";
  }

  function q1timelineDisplayModes(event) {
    return Array.isArray(event?.meta?.display_modes) ? event.meta.display_modes : [];
  }

  function isVisibleQ1timelineEvent(event) {
    const kind = String(event?.kind || "").toLowerCase();
    const lane = String(event?.lane || "").toLowerCase();
    if (kind === "q1_issue" || lane.startsWith("debug.") || q1timelineDisplayModes(event).includes("debug")) {
      return false;
    }
    const start = q1timelineEventScalar(event, "t0");
    const end = q1timelineEventScalar(event, "t1");
    if (Number.isFinite(start) && Number.isFinite(end)) {
      return end > start;
    }
    const duration = q1timelineEventScalar(event, "duration");
    return Number.isFinite(duration) && duration > 0;
  }

  function q1timelineEventScalar(event, key) {
    const explicitNs = q1timelineScalarValue(event?.[`${key}_ns`]);
    if (explicitNs !== undefined) {
      return explicitNs;
    }
    const value = q1timelineScalarValue(event?.[key]);
    if (Number.isFinite(value) && value !== 0 && Math.abs(value) < 1e-3) {
      return value * 1e9;
    }
    return value;
  }

  function q1timelineEventsForMapping(ir, mapping) {
    const events = Array.isArray(ir?.q1timeline_ir?.events) ? ir.q1timeline_ir.events : [];
    if (!events.length || !mapping?.sequencer) {
      return [];
    }
    const mappingSequencers = q1timelineMappingSequencers(mapping);
    const startLine = provenanceStartLine(mapping);
    const endLine = provenanceEndLine(mapping);
    return events.filter((event) => {
      const line = q1timelineEventLine(event);
      return (
        mappingSequencers.has(q1timelineEventSequencer(event)) &&
        typeof line === "number" &&
        line >= startLine &&
        line <= endLine
      );
    });
  }

  function uniqueQ1timelineEvents(events) {
    const seen = new Set();
    const unique = [];
    for (const event of events) {
      const key = [
        q1timelineEventSequencer(event),
        q1timelineEventLine(event),
        event?.id,
        event?.lane,
        String(event?.kind || "").toLowerCase(),
        q1timelineEventScalar(event, "t0"),
        q1timelineEventScalar(event, "t1"),
        q1timelineEventRaw(event),
      ].join("|");
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      unique.push(event);
    }
    return unique;
  }

  function q1timelineEventSpan(event) {
    const start = q1timelineEventScalar(event, "t0");
    const end = q1timelineEventScalar(event, "t1");
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
      return { start, end };
    }
    const duration = q1timelineEventScalar(event, "duration");
    if (Number.isFinite(start) && Number.isFinite(duration) && duration > 0) {
      return { start, end: start + duration };
    }
    return undefined;
  }

  function q1timelineInlineBlockForEvent(ir, block, mapping, event, span, timelineStart, timelineSpan) {
    const line = q1timelineEventLine(event) || provenanceStartLine(mapping);
    const lineLabel = lineRangeLabel(line, line);
    const q1asmText = q1timelineEventRaw(event) || q1asmLineTextAt(q1asmTextForMapping(ir, mapping), line);
    const instruction = q1asmOpcode(q1asmText) || event?.kind || event?.label || "Q1ASM";
    const eventId = event?.id || `${line}:${instruction}:${span.start}`;
    const leftRatio = (span.start - timelineStart) / timelineSpan;
    const widthRatio = (span.end - span.start) / timelineSpan;
    return {
      id: `q1:${block.id}:${eventId}`,
      type: "q1",
      visualKind: "q1",
      source: "q1timeline",
      sourceBlockId: block.id,
      operationId: block.operationId,
      sequencer: q1timelineEventSequencer(event) || mapping.sequencer,
      eventId,
      eventLane: event?.lane,
      line,
      targetEndLine: line,
      lineRangeLabel: lineLabel,
      instruction,
      label: event?.label || instruction,
      detail: `L${lineLabel}`,
      accentColor: q1CommandAccentColor(instruction),
      q1asmSourceMessage: {
        type: "openQ1AsmSource",
        sequencer: q1timelineEventSequencer(event) || mapping.sequencer,
        line,
      },
      ...(q1asmText ? { q1asmText } : {}),
      startLabel: q1timelineScalarLabel(event?.t0 ?? event?.t0_ns, span.start),
      durationLabel: q1timelineScalarLabel(event?.duration ?? event?.duration_ns, span.end - span.start),
      leftPercent: roundPercent(block.leftPercent + block.widthPercent * leftRatio),
      widthPercent: roundPercent(block.widthPercent * widthRatio),
    };
  }

  function q1timelineInlinePreviewsForBlock(ir, block, mapping) {
    const events = uniqueQ1timelineEvents(q1timelineEventsForMapping(ir, mapping).filter(isVisibleQ1timelineEvent));
    const qbsStartNs = Number.isFinite(block.startSeconds) ? block.startSeconds * 1e9 : undefined;
    const qbsDurationNs = positiveDuration(block.durationSeconds) ? block.durationSeconds * 1e9 : undefined;
    const qbsEndNs = Number.isFinite(qbsStartNs) && Number.isFinite(qbsDurationNs)
      ? qbsStartNs + qbsDurationNs
      : undefined;
    const spans = events
      .map((event) => ({ event, span: q1timelineEventSpan(event) }))
      .filter((entry) => entry.span)
      .filter((entry) => {
        if (!Number.isFinite(qbsStartNs) || !Number.isFinite(qbsEndNs)) {
          return true;
        }
        return entry.span.start >= qbsStartNs && entry.span.end <= qbsEndNs;
      });
    if (!spans.length) {
      return [];
    }
    spans.sort((a, b) => {
      const lineDiff = (q1timelineEventLine(a.event) || 0) - (q1timelineEventLine(b.event) || 0);
      if (a.span.start !== b.span.start) {
        return a.span.start - b.span.start;
      }
      return lineDiff;
    });
    const timelineStart = Number.isFinite(qbsStartNs)
      ? qbsStartNs
      : Math.min(...spans.map((entry) => entry.span.start));
    const timelineEnd = Math.max(...spans.map((entry) => entry.span.end));
    const eventSpan = timelineEnd - timelineStart;
    const timelineSpan = Number.isFinite(qbsStartNs) ? Math.max(eventSpan, qbsDurationNs || 0) : eventSpan;
    if (!Number.isFinite(timelineSpan) || timelineSpan <= 0) {
      return [];
    }
    return spans.map((entry) => (
      q1timelineInlineBlockForEvent(ir, block, mapping, entry.event, entry.span, timelineStart, timelineSpan)
    ));
  }

  function inlineQ1PreviewsForBlock(ir, block, mapping) {
    const q1timelineBlocks = q1timelineInlinePreviewsForBlock(ir, block, mapping);
    if (q1timelineBlocks.length) {
      return q1timelineBlocks;
    }
    return [inlineQ1PreviewForBlock(ir, block, mapping)];
  }

  function inlineQ1PreviewForBlock(ir, block, mapping) {
    const eventLine = q1asmEventLineForMapping(mapping);
    const line = eventLine || provenanceStartLine(mapping);
    const endLine = eventLine || provenanceEndLine(mapping);
    const lineLabel = lineRangeLabel(line, endLine);
    const q1asmText = q1asmLineTextForMapping(ir, mapping);
    const instruction = q1asmOpcode(q1asmText) || mapping.instruction || firstInstructionRole(mapping) || "Q1ASM";
    return {
      id: `q1:${block.id}`,
      type: "q1",
      visualKind: "q1",
      sourceBlockId: block.id,
      operationId: block.operationId,
      sequencer: mapping.sequencer,
      line,
      targetEndLine: endLine,
      lineRangeLabel: lineLabel,
      instruction,
      label: instruction,
      detail: `L${lineLabel}`,
      accentColor: q1CommandAccentColor(instruction),
      q1asmSourceMessage: {
        type: "openQ1AsmSource",
        sequencer: mapping.sequencer,
        line,
      },
      ...(q1asmText ? { q1asmText } : {}),
      startLabel: block.startLabel,
      durationLabel: block.durationLabel,
      leftPercent: block.leftPercent,
      widthPercent: block.widthPercent,
    };
  }

  function pulseLayoutPosition(pulse, layout) {
    return layout?.positions.get(pulse.schedulable_id) || layout?.positions.get(pulse.operation_id);
  }

  function pulseLanes(ir, viewport, layout) {
    const lanes = new Map();
    for (const pulse of ir.symbolic_pulses || []) {
      const position = pulseLayoutPosition(pulse, layout);
      const start = (pulse.abs_time > 0 ? pulse.abs_time : undefined) ?? position?.start ?? pulse.abs_time ?? 0;
      const duration = positiveDuration(pulse.duration) || position?.duration || viewportDuration(viewport) * 0.02;
      const style = blockStyle(start, duration, viewport);
      if (!style) {
        continue;
      }
      const list = lanes.get(pulse.lane) || [];
      const block = {
        id: pulse.id,
        type: "pulse",
        operationId: pulse.operation_id,
        schedulableId: pulse.schedulable_id,
        role: pulse.role || "pulse",
        visualKind: pulse.role === "acquisition" ? "acquisition" : "pulse",
        label: pulse.display_label || pulse.label || pulse.kind || pulse.id,
        detail: pulse.display_subtitle || formatDuration(pulse.duration || 0),
        startSeconds: start,
        durationSeconds: pulse.duration || 0,
        scheduleSourceMessage: scheduleSourceMessage({
          id: pulse.id,
          operationId: pulse.operation_id,
          schedulableId: pulse.schedulable_id || pulse.operation_id,
        }),
        startLabel: formatDuration(start),
        durationLabel: formatDuration(pulse.duration || 0),
        ...style,
      };
      const mapping = provenanceForSelection(ir, block);
      if (mapping?.sequencer) {
        block.q1timelineMessage = q1timelinePreviewMessage(block, mapping);
        block.inlineQ1Preview = inlineQ1PreviewsForBlock(ir, block, mapping);
      }
      list.push(block);
      lanes.set(pulse.lane, list);
    }
    return [...lanes.entries()].map(([label, blocks]) => (
      withInlineQ1PreviewLaneMetadata({ label, kind: "pulse", blocks })
    ));
  }

  function findSelectedBlock(lanes, selectedId) {
    for (const lane of lanes) {
      const block = lane.blocks.find((candidate) => candidate.id === selectedId || candidate.operationId === selectedId);
      if (block) {
        return block;
      }
    }
    return lanes[0]?.blocks[0];
  }

  function findPulse(ir, id) {
    return (ir.symbolic_pulses || []).find((pulse) => pulse.id === id);
  }

  function findOperation(ir, id) {
    return (ir.operations || []).find((operation) => operation.id === id || operation.operation_id === id);
  }

  function findProvenanceForOperation(ir, ...ids) {
    const candidates = ids.filter(Boolean);
    return (ir.q1asm_provenance || []).find((mapping) => (
      candidates.includes(mapping.operation_id) ||
      candidates.includes(mapping.schedulable_id) ||
      candidates.includes(mapping.source_id)
    ));
  }

  function inspectorRows(rows) {
    return rows.filter((row) => row.value !== undefined && row.value !== null && row.value !== "");
  }

  function buildPulseInspector(ir, pulse, selectedBlock, values) {
    const mapping = provenanceForSelection(ir, selectedBlock || {
      id: pulse.id,
      operationId: pulse.operation_id,
    });
    const symbolicDuration = values.get(pulse.duration_value_id);
    const line = provenanceStartLine(mapping);
    const title = pulse.display_label || pulse.label || pulse.id;
    return {
      title,
      subtitle: "Symbolic pulse",
      rows: inspectorRows([
        { label: "Lane", value: pulse.lane },
        { label: "Summary", value: pulse.display_subtitle },
        { label: "Operation", value: pulse.operation_id },
        { label: "Start", value: formatDuration(pulse.abs_time || 0) },
        { label: "Duration", value: formatDuration(pulse.duration || 0) },
        { label: "Symbolic duration", value: symbolicDuration?.label },
        { label: "Q1ASM", value: mapping ? `${mapping.sequencer}:L${line}` : "" },
      ]),
      actions: [
        { label: "Open Full Q1ASM Timeline", message: { type: "openQ1Timeline", blockId: pulse.id, operationId: pulse.operation_id } },
      ],
      selectedId: selectedBlock.id,
    };
  }

  function buildOperationInspector(ir, operation, selectedBlock) {
    const operationId = operation.operation_id || operation.id;
    const mapping = findProvenanceForOperation(ir, operation.id, operationId, operation.schedulable_id);
    const line = provenanceStartLine(mapping);
    return {
      title: operation.label || operation.id,
      subtitle: "Schedule operation",
      rows: inspectorRows([
        { label: "Operation ID", value: operationId },
        { label: "Start", value: formatDuration(operation.abs_time || 0) },
        { label: "Duration", value: formatDuration(operation.duration || 0) },
        { label: "Q1ASM", value: mapping ? `${mapping.sequencer}:L${line}` : "" },
      ]),
      actions: [
        { label: "Open Full Q1ASM Timeline", message: { type: "openQ1Timeline", operationId } },
      ],
      selectedId: selectedBlock.id,
    };
  }

  function buildProvenanceInspector(ir, block, values) {
    const symbolicValue = values.get(block.symbolicValueId);
    return {
      title: block.label,
      subtitle: "Q1ASM provenance",
      rows: inspectorRows([
        { label: "Sequencer", value: block.sequencer },
        { label: "Line", value: block.line ? String(block.line) : "" },
        { label: "Operation", value: block.operationId },
        { label: "Symbolic value", value: symbolicValue?.label },
      ]),
      actions: [
        { label: "Open Full Q1ASM Timeline", message: { type: "openQ1Timeline", operationId: block.operationId, sequencer: block.sequencer, line: block.line } },
      ],
      selectedId: block.id,
    };
  }

  function buildInspector(ir, selectedBlock, values) {
    if (!selectedBlock) {
      return {
        title: "No selection",
        subtitle: "Select a block",
        rows: [],
        actions: [{ label: "Open QBS IR", message: { type: "openIr" } }],
        selectedId: undefined,
      };
    }
    const pulse = findPulse(ir, selectedBlock.id);
    if (pulse) {
      return buildPulseInspector(ir, pulse, selectedBlock, values);
    }
    const operation = findOperation(ir, selectedBlock.operationId || selectedBlock.id);
    if (selectedBlock.type === "operation" && operation) {
      return buildOperationInspector(ir, operation, selectedBlock);
    }
    if (selectedBlock.type === "provenance") {
      return buildProvenanceInspector(ir, selectedBlock, values);
    }
    return {
      title: selectedBlock.label,
      subtitle: selectedBlock.type,
      rows: [],
      actions: [],
      selectedId: selectedBlock.id,
    };
  }

  function provenanceForSelection(ir, selectedBlock) {
    const sourceId = selectedBlock?.id;
    const operationId = selectedBlock?.operationId || selectedBlock?.id;
    const rows = ir.q1asm_provenance || [];
    return rows.find((mapping) => mapping.source_id === sourceId)
      || rows.find((mapping) => mapping.schedulable_id === sourceId)
      || rows.find((mapping) => mapping.operation_id === operationId);
  }

  function lineRangeLabel(start, end) {
    return start === end ? String(start) : `${start}-${end}`;
  }

  function q1asmTextForMapping(ir, mapping) {
    if (!mapping?.sequencer) {
      return "";
    }
    const mappingSequencers = q1timelineMappingSequencers(mapping);
    for (const sequencer of mappingSequencers) {
      const embedded = (ir.q1asm_by_sequencer || {})[sequencer];
      if (embedded) {
        return embedded;
      }
    }
    const program = (ir.q1asm_programs || []).find((candidate) => (
      mappingSequencers.has(candidate?.sequencer) || mappingSequencers.has(candidate?.sequencer_id)
    ));
    return typeof program?.text === "string" ? program.text : "";
  }

  function q1asmSnippet(text, targetLine, targetEndLine, contextLines) {
    const lines = String(text || "").split(/\r?\n/).filter((line, index, all) => line || index < all.length - 1);
    if (!lines.length) {
      return [];
    }
    const start = Math.max(1, targetLine - contextLines);
    const end = Math.min(lines.length, targetEndLine + contextLines);
    return lines.slice(start - 1, end).map((lineText, index) => {
      const number = start + index;
      return {
        number,
        text: lineText,
        highlighted: number >= targetLine && number <= targetEndLine,
      };
    });
  }

  function q1timelinePreviewMessage(selectedBlock, mapping) {
    const message = { type: "openQ1Timeline" };
    if (selectedBlock?.type === "pulse") {
      message.blockId = selectedBlock.id;
      message.operationId = selectedBlock.operationId;
    } else if (selectedBlock?.type === "operation") {
      message.operationId = selectedBlock.operationId || selectedBlock.id;
      message.blockId = selectedBlock.id;
    } else if (selectedBlock?.operationId) {
      message.operationId = selectedBlock.operationId;
    }
    if (mapping?.sequencer) {
      message.sequencer = mapping.sequencer;
      message.line = provenanceStartLine(mapping);
    }
    return message;
  }

  function scheduleSourceMessage(block) {
    return {
      type: "openScheduleSource",
      schedulableId: block.schedulableId || block.operationId || block.id,
      operationId: block.operationId,
      blockId: block.id,
    };
  }

  function buildQ1asmDrilldown(ir, selectedBlock, mapping) {
    const line = provenanceStartLine(mapping);
    const endLine = provenanceEndLine(mapping);
    const openMessage = q1timelinePreviewMessage(selectedBlock, mapping);
    if (!mapping?.sequencer) {
      return {
        available: false,
        title: "Q1ASM Preview",
        sequencer: "",
        targetLine: line,
        targetEndLine: endLine,
        lineRangeLabel: "",
        lines: [],
        openMessage,
        emptyMessage: "No Q1ASM provenance is available for this QBS selection.",
      };
    }

    const text = q1asmTextForMapping(ir, mapping);
    const lines = q1asmSnippet(text, line, endLine, 2);
    return {
      available: lines.length > 0,
      title: "Q1ASM Preview",
      sequencer: mapping.sequencer,
      targetLine: line,
      targetEndLine: endLine,
      lineRangeLabel: lineRangeLabel(line, endLine),
      instruction: mapping.instruction,
      lines,
      openMessage,
      emptyMessage: "Q1ASM text is not embedded in this QBS IR. Open the full Q1ASM Timeline to inspect the generated file.",
    };
  }

  function attachInspectorTabs(ir, inspector, selectedBlock, values) {
    const mapping = provenanceForSelection(ir, selectedBlock);
    const line = provenanceStartLine(mapping);
    const endLine = provenanceEndLine(mapping);
    const symbolicValue = mapping?.symbolic_value_id ? values.get(mapping.symbolic_value_id) : undefined;
    return {
      ...inspector,
      tabs: [
        { id: "summary", label: "Summary" },
        { id: "lowering", label: "Lowering" },
        { id: "q1asm", label: "Q1ASM Preview" },
      ],
      loweringRows: inspectorRows([
        { label: "Sequencer", value: mapping?.sequencer },
        { label: "Q1ASM lines", value: mapping ? lineRangeLabel(line, endLine) : "" },
        { label: "Instruction", value: mapping?.instruction },
        { label: "Symbolic value", value: symbolicValue?.label || mapping?.symbolic_value_id },
        ...((mapping?.operand_mappings || []).map((operand) => ({
          label: operand.role || "Operand",
          value: operand.source_expression || operand.value_id || operand.symbolic_value_id,
        }))),
      ]),
      q1asmDrilldown: buildQ1asmDrilldown(ir, selectedBlock, mapping),
    };
  }

  function basename(path) {
    if (!path) {
      return "";
    }
    const parts = String(path).split(/[\\/]/);
    return parts[parts.length - 1] || String(path);
  }

  function buildSourceContext(sourceContext) {
    if (!sourceContext) {
      return undefined;
    }
    const hasNotebook = Boolean(sourceContext.sourceNotebook);
    return {
      projectLabel: basename(sourceContext.projectFile),
      scheduleLabel: hasNotebook ? basename(sourceContext.sourceNotebook) : basename(sourceContext.scheduleFile),
      outputLabel: basename(sourceContext.outputDir),
      actions: [
        { label: "Open qbstimeline.yml", message: { type: "openProjectFile" } },
        hasNotebook
          ? { label: "Open notebook", message: { type: "openNotebookFile" } }
          : { label: "Open schedule.py", message: { type: "openScheduleFile" } },
        ...(hasNotebook && sourceContext.scheduleFile
          ? [{ label: "Open generated schedule.py", message: { type: "openScheduleFile" } }]
          : []),
      ],
    };
  }

  function appendSourceActions(inspector, source) {
    if (!source) {
      return inspector;
    }
    return {
      ...inspector,
      actions: [...inspector.actions, ...source.actions],
    };
  }

  function applySelectionState(lanes, selectedBlock) {
    const selectedOperationId = selectedBlock?.operationId || selectedBlock?.id;
    return lanes.map((lane) => ({
      ...lane,
      blocks: lane.blocks.map((block) => ({
        ...block,
        selected: block.id === selectedBlock?.id,
        relatedSelected: Boolean(
          selectedOperationId &&
            block.id !== selectedBlock?.id &&
            (block.operationId === selectedOperationId || block.id === selectedOperationId),
        ),
      })),
    }));
  }

  function normalizeSelectionRange(viewport, selectionRange) {
    if (!selectionRange || !Number.isFinite(selectionRange.start) || !Number.isFinite(selectionRange.end)) {
      return undefined;
    }
    let start = selectionRange.start;
    let end = selectionRange.end;
    if (start > end) {
      [start, end] = [end, start];
    }
    start = Math.max(viewport.start, Math.min(viewport.end, start));
    end = Math.max(viewport.start, Math.min(viewport.end, end));
    if (end <= start) {
      return undefined;
    }
    const span = viewportDuration(viewport);
    return {
      start,
      end,
      startLabel: formatDuration(start),
      endLabel: formatDuration(end),
      durationLabel: formatDuration(end - start),
      leftPercent: pct(start - viewport.start, span),
      widthPercent: pct(end - start, span),
    };
  }

  function buildTimelineModel(ir, selectedId, sourceContext, viewState) {
    const logicalLayout = logicalControlFlowLayout(ir);
    const total = totalDuration(ir, logicalLayout);
    const values = symbolicValueById(ir);
    const viewport = normalizeViewport(total, viewState?.viewport);
    const controlFlowRawLanes = controlFlowLanes(ir, viewport, viewState?.expandedGroups, logicalLayout);
    const scheduleRawLanes = [
      ...operationLanes(ir, viewport, logicalLayout),
      ...pulseLanes(ir, viewport, logicalLayout),
    ];
    const rawLanes = [
      ...controlFlowRawLanes,
      ...scheduleRawLanes,
    ];
    const selectedBlock = findSelectedBlock(rawLanes, selectedId);
    const lanes = [
      ...applySelectionState(controlFlowRawLanes, selectedBlock),
      ...groupTimelineLanes(
        applySelectionState(scheduleRawLanes, selectedBlock),
        viewState?.expandedGroups,
        viewState?.expandedInlineQ1Lanes,
      ),
    ].map(stackLaneBlocks);
    const source = buildSourceContext(sourceContext);
    const baseInspector = appendSourceActions(buildInspector(ir, selectedBlock, values), source);
    return {
      totalSeconds: total,
      totalLabel: formatDuration(total),
      viewport: viewportSummary(total, viewport),
      selectionRange: normalizeSelectionRange(viewport, viewState?.selectionRange),
      ticks: ticks(viewport),
      lanes,
      inspector: attachInspectorTabs(ir, baseInspector, selectedBlock, values),
      source,
      artifactSummary: [
        { label: "Operations", value: String((ir.operations || []).length) },
        { label: "Symbolic pulses", value: String((ir.symbolic_pulses || []).length) },
        { label: "Q1ASM programs", value: String((ir.q1asm_programs || []).length) },
        { label: "Provenance links", value: String((ir.q1asm_provenance || []).length) },
      ],
    };
  }

  return {
    formatDuration,
    panViewport,
    buildTimelineModel,
  };
});
