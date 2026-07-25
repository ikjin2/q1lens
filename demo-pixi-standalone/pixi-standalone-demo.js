(function () {
  const state = {
    app: null,
    root: null,
    staticLayer: null,
    dynamicLayer: null,
    dynamicGraphics: null,
    playheadText: null,
    ir: null,
    layout: null,
    viewMode: "showcase",
    playing: true,
    animationFrame: 0,
    lastAnimationMs: 0,
    sceneTimeMs: 0,
    playheadNs: 0,
    fullTimeExtent: null,
    timeWindow: null,
    feedbackFlows: [],
    annotations: [],
    frameCount: 0,
    fpsStartedAt: 0,
    fps: 0,
    selected: null,
    hovered: null,
    camera: { y: 0 },
    drag: null,
  };

  const colors = {
    background: 0x080b12,
    panel: 0x121823,
    panelEdge: 0x2a3444,
    lane: 0x17202e,
    laneRule: 0x334155,
    grid: 0x273244,
    text: 0xe9eef7,
    muted: 0x9aa6b8,
    cursor: 0x55d6f4,
    feedback: 0xb58cff,
    feedbackAlt: 0xff8bd1,
    warning: 0xffbd5b,
    error: 0xff7d72,
    event: {
      exact: 0x87bfff,
      symbolic: 0xf2c85b,
      assumed: 0xd4dae5,
      unknown: 0xadb8c8,
      runtime_dependent: 0xffbd5b,
    },
    kind: {
      play: 0x87bfff,
      acquire: 0x45d68a,
      wait_trigger: 0xf2c85b,
      wait_sync: 0x6ee7f9,
      feedback_pop: 0xb58cff,
      feedback_com: 0xff8bd1,
      fb_acq_iq_id: 0xb58cff,
      fb_acq_iq_shift: 0xb58cff,
      loop_block: 0xd4dae5,
      loop_iteration_preview: 0xd4dae5,
      branch_region: 0xffbd5b,
    },
  };

  const laneOrder = [
    "rt.sync",
    "rt.trigger",
    "rt.wait",
    "rt.play",
    "rt.acquire",
    "rt.feedback",
    "rt.branch",
    "rt.loop",
    "debug.q1_issue",
    "debug.queue_depth",
    "debug.slack",
  ];

  const PLAYHEAD_NS_PER_MS = 1.5;
  const FEEDBACK_PACKET_FLIGHT_NS = 220;
  const FEEDBACK_POP_PULSE_NS = 140;

  const els = {};

  window.addEventListener("DOMContentLoaded", () => {
    cacheElements();
    bindControls();
    window.__threePeakTimelineDebug = () => ({
      fullTimeExtent: state.fullTimeExtent ? { ...state.fullTimeExtent } : null,
      timeWindow: state.timeWindow ? { ...state.timeWindow } : null,
      ticks: state.layout && state.layout.timeScale ? [...state.layout.timeScale.majorTicks] : [],
      rootScaleX: state.root ? state.root.scale.x : null,
      rootScaleY: state.root ? state.root.scale.y : null,
    });
    startDemo().catch((error) => renderFatal(error));
  });

  function cacheElements() {
    els.stage = document.getElementById("pixi-stage");
    els.tooltip = document.getElementById("pixi-tooltip");
    els.inspector = document.getElementById("inspector-details");
    els.loadStatus = document.getElementById("load-status");
    els.fpsMeter = document.getElementById("fps-meter");
    els.normalMode = document.getElementById("normal-mode");
    els.debugMode = document.getElementById("debug-mode");
    els.showcaseMode = document.getElementById("showcase-mode");
    els.playbackToggle = document.getElementById("playback-toggle");
    els.fitAll = document.getElementById("fit-all");
    els.resetView = document.getElementById("reset-view");
    els.metricEvents = document.getElementById("metric-events");
    els.metricFeedback = document.getElementById("metric-feedback");
    els.metricSequencers = document.getElementById("metric-sequencers");
  }

  function bindControls() {
    els.normalMode.addEventListener("click", () => setMode("normal"));
    els.debugMode.addEventListener("click", () => setMode("debug"));
    els.showcaseMode.addEventListener("click", () => setMode("showcase"));
    els.playbackToggle.addEventListener("click", togglePlayback);
    els.fitAll.addEventListener("click", fitToAll);
    els.resetView.addEventListener("click", () => {
      state.camera = { y: 0 };
      state.timeWindow = state.fullTimeExtent ? { ...state.fullTimeExtent } : null;
      applyLayout({ preserveCamera: false });
    });
    window.addEventListener("resize", () => {
      resizeApp();
      applyLayout({ preserveCamera: true });
    });
  }

  async function startDemo() {
    if (!window.PIXI) {
      throw new Error("PixiJS failed to load from the CDN.");
    }
    const response = await fetch("../examples/three-peak-demo1/.q1timeline/timeline_ir.json");
    if (!response.ok) {
      throw new Error(`TimelineIR load failed: HTTP ${response.status}`);
    }
    state.ir = await response.json();
    await mountPixiApp(window.PIXI);
    applyLayout();
    updateMetrics();
    els.loadStatus.textContent = `IR: Three Peak Demo 1 timeline_ir.json | ${state.ir.events.length} events`;
    startAnimationLoop();
  }

  async function mountPixiApp(PIXI) {
    const app = new PIXI.Application();
    await app.init({
      antialias: true,
      autoDensity: true,
      backgroundAlpha: 0,
      resolution: window.devicePixelRatio || 1,
      resizeTo: els.stage,
    });
    state.app = app;
    state.root = new PIXI.Container();
    state.staticLayer = new PIXI.Container();
    state.dynamicLayer = new PIXI.Container();
    state.dynamicGraphics = new PIXI.Graphics();
    state.playheadText = makeText(PIXI, "", 11, colors.cursor, 700);
    state.dynamicLayer.addChild(state.dynamicGraphics, state.playheadText);
    state.root.addChild(state.staticLayer, state.dynamicLayer);
    app.stage.addChild(state.root);
    els.stage.appendChild(app.canvas);

    app.canvas.addEventListener("pointerdown", beginDrag);
    app.canvas.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", endDrag);
    app.canvas.addEventListener("click", handleClick);
    app.canvas.addEventListener("wheel", handleWheel, { passive: false });
  }

  function setMode(mode) {
    state.viewMode = mode;
    for (const button of [els.normalMode, els.debugMode, els.showcaseMode]) {
      button.setAttribute("aria-pressed", String(button.id === `${mode}-mode`));
    }
    applyLayout({ preserveCamera: false });
  }

  function togglePlayback() {
    state.playing = !state.playing;
    els.playbackToggle.textContent = state.playing ? "Pause" : "Play";
    els.playbackToggle.setAttribute("aria-pressed", String(state.playing));
  }

  function applyLayout(options = {}) {
    if (!state.ir || !state.app) {
      return;
    }
    const previousCamera = { ...state.camera };
    const mode = state.viewMode === "debug" ? "debug" : "normal";
    const width = Math.max(900, Math.floor(els.stage.getBoundingClientRect().width));
    state.fullTimeExtent = computeFullTimeExtent(state.ir, mode);
    if (!options.preserveCamera || !state.timeWindow) {
      state.timeWindow = { ...state.fullTimeExtent };
    } else {
      state.timeWindow = clampTimeWindow(state.timeWindow, state.fullTimeExtent);
    }
    state.layout = computeTimelineLayout(state.ir, {
      mode,
      width,
      timeWindow: state.timeWindow,
    });
    state.feedbackFlows = createFeedbackFlows(state.ir, state.layout);
    if (!options.preservePlayhead) {
      state.playheadNs = state.layout.timeScale.tMinNs;
    }
    if (options.preserveCamera) {
      state.camera = previousCamera;
      clampCamera();
    } else {
      state.camera.y = 0;
      fitVerticalToAll();
    }
    drawTimelineScene();
  }

  function computeTimelineLayout(ir, options) {
    const mode = options.mode || "normal";
    const width = Math.max(1200, options.width || 1400);
    const plotLeft = 196;
    const plotRight = width - 34;
    const top = isCompactMode(mode) ? 68 : 78;
    const laneHeight = isCompactMode(mode) ? 50 : 31;
    const lanes = [];
    const laneMap = new Map();
    const sequencerOrder = sequencerIds(ir);
    const visibleEvents = records(ir.events)
      .filter((event) => isVisibleEvent(event, mode))
      .map((event) => normalizeEvent(event));

    for (const event of visibleEvents) {
      const key = isCompactMode(mode) ? event.sequencerId : laneKey(event);
      if (!laneMap.has(key)) {
        const lane = {
          key,
          sequencerId: event.sequencerId,
          lane: isCompactMode(mode) ? "compact" : event.lane,
          events: [],
        };
        laneMap.set(key, lane);
        lanes.push(lane);
      }
      laneMap.get(key).events.push(event);
    }

    lanes.sort((a, b) => {
      const seqDiff = sequencerOrder.indexOf(a.sequencerId) - sequencerOrder.indexOf(b.sequencerId);
      if (seqDiff !== 0) {
        return seqDiff;
      }
      if (isCompactMode(mode)) {
        return 0;
      }
      return laneRank(a.lane) - laneRank(b.lane) || a.lane.localeCompare(b.lane);
    });

    const fallbackExtent = computeTimeExtent(visibleEvents);
    const requestedWindow = options.timeWindow || fallbackExtent;
    const minTime = Number.isFinite(Number(requestedWindow.minNs)) ? Number(requestedWindow.minNs) : fallbackExtent.minNs;
    const maxTime = Number.isFinite(Number(requestedWindow.maxNs)) ? Number(requestedWindow.maxNs) : fallbackExtent.maxNs;

    const panels = [];
    let currentSequencer = "";
    for (let index = 0; index < lanes.length; index += 1) {
      const lane = lanes[index];
      lane.y = top + index * laneHeight;
      lane.height = laneHeight;
      if (lane.sequencerId !== currentSequencer) {
        currentSequencer = lane.sequencerId;
        panels.push({ sequencerId: currentSequencer, y: lane.y, height: laneHeight, lanes: 1 });
      } else {
        const panel = panels[panels.length - 1];
        panel.height += laneHeight;
        panel.lanes += 1;
      }
    }

    const eventLayouts = [];
    for (const event of visibleEvents) {
      const lane = laneMap.get(isCompactMode(mode) ? event.sequencerId : laneKey(event));
      const x0 = scaleTime(event.t0Ns, minTime, maxTime, plotLeft, plotRight);
      const x1 = scaleTime(event.t1Ns, minTime, maxTime, plotLeft, plotRight);
      const minWidth = event.kind === "q1_issue" ? 3 : event.durationNs === 0 ? 8 : 6;
      const widthPx = Math.max(minWidth, x1 - x0);
      const compact = isCompactMode(mode);
      const heightPx = compact ? compactEventHeight(event) : Math.max(12, lane.height - 15);
      const y = compact ? lane.y + compactEventOffset(event) : lane.y + 2;
      const labelText = compact ? compactLabel(event, widthPx) : eventLabel(event, widthPx);
      const eventLayout = {
        ...event,
        x: x0,
        y,
        width: widthPx,
        height: heightPx,
        labelLayout: {
          visible: Boolean(labelText) && (compact ? compactLabelVisible(event, widthPx, labelText) : widthPx >= 38 && heightPx >= 15),
          text: labelText,
          x: x0 + 6,
          y: compact ? y + 1 : lane.y + 11,
        },
      };
      eventLayouts.push(eventLayout);
    }
    applyEventLabelLayout(eventLayouts);

    const height = top + lanes.length * laneHeight + 64;
    return {
      width,
      height,
      mode,
      lanes,
      panels,
      events: eventLayouts,
      diagnostics: records(ir.diagnostics),
      timeScale: {
        tMinNs: minTime,
        tMaxNs: maxTime,
        plotLeft,
        plotRight,
        majorTicks: majorTicks(minTime, maxTime),
      },
    };
  }

  function computeFullTimeExtent(ir, mode) {
    const visibleEvents = records(ir.events)
      .filter((event) => isVisibleEvent(event, mode))
      .map((event) => normalizeEvent(event));
    return computeTimeExtent(visibleEvents);
  }

  function computeTimeExtent(events) {
    let minTime = Infinity;
    let maxTime = -Infinity;
    for (const event of events) {
      minTime = Math.min(minTime, event.t0Ns);
      maxTime = Math.max(maxTime, event.t1Ns);
    }
    if (!Number.isFinite(minTime) || !Number.isFinite(maxTime) || minTime === maxTime) {
      minTime = 0;
      maxTime = 1;
    }
    const margin = Math.max(12, Math.round((maxTime - minTime) * 0.025));
    return {
      minNs: minTime - margin,
      maxNs: maxTime + margin,
    };
  }

  function isVisibleEvent(event, mode) {
    const lane = String(event.lane || "");
    const kind = String(event.kind || "");
    if (mode === "debug") {
      return true;
    }
    if (lane.startsWith("debug.") || kind === "q1_issue" || kind === "queue_depth" || kind === "slack") {
      return false;
    }
    if (kind === "latched_state_pending" || kind === "loop_iteration_preview") {
      return false;
    }
    return true;
  }

  function normalizeEvent(event) {
    const meta = isRecord(event.meta) ? event.meta : {};
    const localT0 = timeValue(event.t0);
    const localT1 = timeValue(event.t1);
    const alignedT0 = numeric(meta.aligned_t0, localT0);
    const alignedT1 = numeric(meta.aligned_t1, localT1);
    return {
      raw: event,
      id: String(event.id || ""),
      kind: String(event.kind || "event"),
      label: String(event.label || event.kind || "event"),
      sequencerId: String(event.sequencer_id || event.sequencerId || "sequencer"),
      lane: String(event.lane || "rt"),
      confidence: String(event.confidence || "unknown"),
      source: isRecord(event.source) ? event.source : {},
      meta,
      t0Ns: alignedT0,
      t1Ns: Math.max(alignedT0, alignedT1),
      localT0Ns: localT0,
      localT1Ns: localT1,
      durationNs: Math.max(0, numeric(event.duration && event.duration.value, Math.max(0, alignedT1 - alignedT0))),
    };
  }

  function createFeedbackFlows(ir, layout) {
    const layoutEvents = new Map(layout.events.map((event) => [event.id, event]));
    return records(ir.feedback_flows)
      .map((flow, index) => {
        const from = layoutEvents.get(flow.from_event_id);
        const to = layoutEvents.get(flow.to_event_id);
        if (!from || !to) {
          return null;
        }
        return {
          id: String(flow.id || `feedback-flow-${index}`),
          channel: String(flow.channel || ""),
          label: String(flow.label || "feedback"),
          source: String(flow.source || ""),
          target: String(flow.target || ""),
          fromKind: from.kind,
          toKind: to.kind,
          from,
          to,
          emitNs: from.t1Ns,
          consumeNs: to.t0Ns,
        };
      })
      .filter(Boolean);
  }

  function drawTimelineScene() {
    if (!state.app || !state.layout) {
      return;
    }
    const PIXI = window.PIXI;
    clearLayer(state.staticLayer);
    const root = state.staticLayer;
    const g = new PIXI.Graphics();
    root.addChild(g);
    state.annotations = [];

    roundedRect(g, 0, 0, state.layout.width, state.layout.height, 0, colors.background, 1);
    drawGrid(PIXI, root, g);
    drawSequencerPanels(PIXI, root, g);
    drawLanes(PIXI, root, g);
    drawLoopBrackets(PIXI, root, g);
    drawEvents(PIXI, root, g);
    drawDiagnostics(PIXI, root, g);
    applyCamera();
    updateDebugDataset();
    drawDynamicScene();
  }

  function drawGrid(PIXI, root, g) {
    const scale = state.layout.timeScale;
    for (const tick of scale.majorTicks) {
      const x = scaleTime(tick, scale.tMinNs, scale.tMaxNs, scale.plotLeft, scale.plotRight);
      line(g, x, 46, x, state.layout.height - 34, colors.grid, 1, 0.72);
      const label = makeText(PIXI, `${tick} ns`, 11, colors.muted, 500);
      label.x = x + 4;
      label.y = 47;
      root.addChild(label);
    }
  }

  function drawSequencerPanels(PIXI, root, g) {
    for (const panel of state.layout.panels) {
      roundedRect(g, 12, panel.y - 9, state.layout.width - 24, panel.height + 18, 8, colors.panel, 0.78);
      strokeRect(g, 12, panel.y - 9, state.layout.width - 24, panel.height + 18, 8, colors.panelEdge, 1, 0.92);
      const label = makeText(PIXI, sequencerLabel(panel.sequencerId), 12, colors.text, 650);
      label.x = 22;
      label.y = panel.y + 2;
      root.addChild(label);
    }
  }

  function drawLanes(PIXI, root, g) {
    const scale = state.layout.timeScale;
    const compact = isCompactMode(state.layout.mode);
    for (const lane of state.layout.lanes) {
      roundedRect(g, scale.plotLeft, lane.y + 5, scale.plotRight - scale.plotLeft, lane.height - 10, 6, colors.lane, compact ? 0.42 : 0.55);
      if (compact) {
        const centerY = lane.y + lane.height / 2 + 2;
        line(g, scale.plotLeft, centerY, scale.plotRight, centerY, colors.laneRule, 1, 0.42);
      } else {
        line(g, scale.plotLeft, lane.y + lane.height - 4, scale.plotRight, lane.y + lane.height - 4, colors.laneRule, 1, 0.72);
        const label = makeText(PIXI, laneDisplay(lane.lane), 11, colors.muted, 500);
        label.x = 92;
        label.y = lane.y + 10;
        root.addChild(label);
      }
    }
  }

  function drawLoopBrackets(PIXI, root, g) {
    if (!isCompactMode(state.layout.mode)) {
      return;
    }
    for (const event of state.layout.events) {
      if (!isLoopOverlayEvent(event)) {
        continue;
      }
      const x0 = event.x;
      const x1 = event.x + event.width;
      const y = event.y + 1;
      const capY = y + 13;
      const color = colors.kind.loop_block;
      roundedRect(g, x0, y + 5, event.width, 18, 7, 0xd4dae5, 0.055);
      drawBracket(g, x0, x1, y, capY, 0x050914, 4, 0.72);
      drawBracket(g, x0, x1, y, capY, color, 1.6, 0.88);
      if (event.width >= 90) {
        const text = `${event.meta.loop_id || "loop"} ${event.meta.count || ""}`.trim();
        const label = makeText(PIXI, text, 10, colors.text, 650);
        const labelWidth = Math.min(112, Math.max(48, text.length * 6 + 14));
        const labelX = x0 + 10;
        const labelY = y - 15;
        roundedRect(g, labelX, labelY, labelWidth, 16, 5, 0x080b12, 0.9);
        strokeRect(g, labelX, labelY, labelWidth, 16, 5, color, 1, 0.45);
        label.x = labelX + 7;
        label.y = labelY + 1;
        root.addChild(label);
        registerAnnotation({
          id: `loop:${event.id}`,
          type: "loop",
          label: text,
          x: labelX,
          y: labelY,
          width: labelWidth,
          height: 16,
          event,
        });
      }
    }
  }

  function drawEvents(PIXI, root, g) {
    for (const event of state.layout.events) {
      if (isCompactMode(state.layout.mode) && isLoopOverlayEvent(event)) {
        continue;
      }
      const fill = eventFill(event);
      const alpha = event.kind === "loop_block" || event.kind === "branch_region" ? 0.25 : 0.94;
      roundedRect(g, event.x, event.y + 5, event.width, event.height, 5, fill, alpha);
      const stroke = selectedId() === event.id ? colors.cursor : state.hovered && state.hovered.id === event.id ? colors.text : 0x0b1020;
      strokeRect(g, event.x, event.y + 5, event.width, event.height, 5, stroke, selectedId() === event.id ? 2 : 1, 0.95);
      if (event.labelLayout.visible) {
        const label = makeText(PIXI, event.labelLayout.text, 10, 0x07111f, 650);
        label.x = event.labelLayout.x;
        label.y = event.labelLayout.y;
        root.addChild(label);
      }
    }
  }

  function drawDiagnostics(PIXI, root, g) {
    const byEvent = new Map(state.layout.events.map((event) => [event.id, event]));
    for (const diagnostic of state.layout.diagnostics) {
      const related = Array.isArray(diagnostic.related_events) ? diagnostic.related_events : [];
      const event = byEvent.get(related[0]);
      if (!event) {
        continue;
      }
      const color = diagnostic.severity === "error" ? colors.error : colors.warning;
      const x = event.x + event.width - 5;
      const y = event.y + 2;
      roundedRect(g, x, y, 10, 10, 3, color, 1);
      registerAnnotation({
        id: String(diagnostic.id || diagnostic.code || `diagnostic:${event.id}`),
        type: "diagnostic",
        label: String(diagnostic.message || diagnostic.code || diagnostic.severity || "diagnostic"),
        x,
        y,
        width: 10,
        height: 10,
        event,
        diagnostic,
      });
    }
  }

  function drawDynamicScene() {
    if (!state.dynamicLayer || !state.dynamicGraphics || !state.layout) {
      return;
    }
    const PIXI = window.PIXI;
    const root = state.dynamicLayer;
    const g = state.dynamicGraphics;
    g.clear();
    drawPlayhead(PIXI, root, g);
    drawFeedbackExchange(PIXI, root, g);
  }

  function drawPlayhead(PIXI, root, g) {
    const scale = state.layout.timeScale;
    const x = scaleTime(state.playheadNs, scale.tMinNs, scale.tMaxNs, scale.plotLeft, scale.plotRight);
    line(g, x, 42, x, state.layout.height - 28, colors.cursor, 2, 0.95);
    circle(g, x, 42, 5, colors.cursor, 0.95);
    state.playheadText.text = `${Math.round(state.playheadNs)} ns`;
    state.playheadText.x = x + 8;
    state.playheadText.y = 26;
    state.playheadText.visible = true;
    if (els.stage) {
      els.stage.dataset.playheadNs = String(Math.round(state.playheadNs));
    }
  }

  function drawFeedbackExchange(PIXI, root, g) {
    let activeCount = 0;
    for (let index = 0; index < state.feedbackFlows.length; index += 1) {
      const flow = state.feedbackFlows[index];
      const from = eventCenter(flow.from);
      const to = eventCenter(flow.to);
      const lift = Math.min(92, Math.max(34, Math.abs(to.y - from.y) * 0.4 + 28));
      const mid = {
        x: (from.x + to.x) / 2,
        y: Math.min(from.y, to.y) - lift - (index % 3) * 9,
      };
      const color = feedbackColor(flow.channel, index);
      line(g, from.x, from.y, mid.x, mid.y, color, 1.25, 0.22);
      line(g, mid.x, mid.y, to.x, to.y, color, 1.25, 0.22);
      const phase = feedbackPacketProgress(flow);
      if (phase !== null) {
        activeCount += 1;
        const role = feedbackFlowRole(flow);
        const radius = role === "feedback_com" ? 5.6 : 4.8;
        drawFeedbackPacket(g, from, mid, to, phase, color, radius);
        drawFeedbackLaunchMarker(g, from, color, phase);
      }
      const popPhase = feedbackPopProgress(flow);
      if (popPhase !== null) {
        drawFeedbackPopMarker(g, to, color, popPhase);
      }
    }
    if (els.stage) {
      els.stage.dataset.activeFeedbackCount = String(activeCount);
    }
  }

  function drawFeedbackPacket(g, from, mid, to, phase, color, radius) {
    const head = quadraticPoint(from, mid, to, phase);
    const tail1 = quadraticPoint(from, mid, to, clamp(phase - 0.08, 0, 1));
    const tail2 = quadraticPoint(from, mid, to, clamp(phase - 0.16, 0, 1));
    line(g, tail2.x, tail2.y, tail1.x, tail1.y, color, 3.6, 0.18);
    line(g, tail1.x, tail1.y, head.x, head.y, color, 2.6, 0.62);
    circle(g, tail1.x, tail1.y, Math.max(2.2, radius * 0.48), color, 0.28);
    circle(g, head.x, head.y, radius + 4.2, 0x07101c, 0.74);
    circle(g, head.x, head.y, radius + 1.6, 0xffffff, 0.18);
    circle(g, head.x, head.y, radius, color, 0.98);
  }

  function drawFeedbackLaunchMarker(g, point, color, phase) {
    const alpha = clamp(1 - phase / 0.36, 0, 1);
    if (alpha <= 0) {
      return;
    }
    g.circle(point.x, point.y, 7 + alpha * 9).stroke({ color, width: 1.5, alpha: alpha * 0.62 });
    line(g, point.x - 8, point.y, point.x + 8, point.y, color, 1.2, alpha * 0.48);
    line(g, point.x, point.y - 8, point.x, point.y + 8, color, 1.2, alpha * 0.48);
  }

  function drawFeedbackPopMarker(g, point, color, phase) {
    const alpha = phase < 0.45 ? phase / 0.45 : 1 - (phase - 0.45) / 0.55;
    const eased = easeOutCubic(phase);
    g.circle(point.x, point.y, 6 + eased * 12).stroke({ color, width: 1.6, alpha: alpha * 0.72 });
    g.circle(point.x, point.y, 12 + eased * 7).stroke({ color: 0xffffff, width: 1, alpha: alpha * 0.18 });
    circle(g, point.x, point.y, 3.4, color, 0.88);
  }

  function startAnimationLoop() {
    state.fpsStartedAt = performance.now();
    const tick = (now) => {
      const delta = state.lastAnimationMs ? Math.min(48, now - state.lastAnimationMs) : 16;
      state.lastAnimationMs = now;
      if (state.playing && state.layout) {
        state.sceneTimeMs += delta;
        const scale = state.layout.timeScale;
        const span = Math.max(1, scale.tMaxNs - scale.tMinNs);
        state.playheadNs = scale.tMinNs + ((state.sceneTimeMs * PLAYHEAD_NS_PER_MS) % span);
      }
      updateFps(now);
      drawDynamicScene();
      state.animationFrame = requestAnimationFrame(tick);
    };
    state.animationFrame = requestAnimationFrame(tick);
  }

  function updateFps(now) {
    state.frameCount += 1;
    const elapsed = now - state.fpsStartedAt;
    if (elapsed >= 500) {
      state.fps = Math.round((state.frameCount * 1000) / elapsed);
      state.frameCount = 0;
      state.fpsStartedAt = now;
      els.fpsMeter.textContent = `FPS ${state.fps}`;
    }
  }

  function resizeApp() {
    if (state.app && state.app.renderer) {
      state.app.renderer.resize(els.stage.clientWidth, els.stage.clientHeight);
    }
  }

  function fitToAll() {
    if (!els.stage) {
      return;
    }
    if (state.fullTimeExtent) {
      state.timeWindow = { ...state.fullTimeExtent };
    }
    state.camera.y = 0;
    fitVerticalToAll();
    applyLayout({ preserveCamera: true });
  }

  function fitVerticalToAll() {
    if (!state.layout || !els.stage) {
      return;
    }
    state.camera.y = 0;
  }

  function applyCamera() {
    if (!state.root) {
      return;
    }
    clampCamera();
    state.root.scale.set(1, 1);
    state.root.position.set(0, -state.camera.y);
  }

  function beginDrag(event) {
    state.drag = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      cameraY: state.camera.y,
      timeWindow: state.timeWindow ? { ...state.timeWindow } : null,
      moved: false,
    };
  }

  function handlePointerMove(event) {
    if (state.drag && state.drag.pointerId === event.pointerId) {
      const dx = event.clientX - state.drag.x;
      const dy = event.clientY - state.drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) {
        state.drag.moved = true;
      }
      if (state.drag.timeWindow) {
        state.timeWindow = panTimeWindowByPixels(state.drag.timeWindow, -dx);
      }
      state.camera.y = state.drag.cameraY - dy;
      applyLayout({ preserveCamera: true, preservePlayhead: true });
      return;
    }
    const hit = hitTest(screenToWorld(event));
    if (!sameSelection(hit, state.hovered)) {
      state.hovered = hit;
      renderTooltip(hit, event);
      drawTimelineScene();
    } else if (hit) {
      renderTooltip(hit, event);
    } else {
      renderTooltip(null);
    }
  }

  function endDrag() {
    state.drag = null;
  }

  function handleClick(event) {
    if (state.drag && state.drag.moved) {
      return;
    }
    const hit = hitTest(screenToWorld(event));
    state.selected = hit;
    drawTimelineScene();
    renderInspector(hit);
  }

  function handleWheel(event) {
    event.preventDefault();
    const before = screenToWorld(event);
    if (event.shiftKey) {
      state.camera.y += event.deltaY;
      applyCamera();
    } else {
      const factor = event.deltaY < 0 ? 0.76 : 1 / 0.76;
      zoomTimeWindowAt(before.timeNs, factor);
      applyLayout({ preserveCamera: true, preservePlayhead: true });
    }
  }

  function hitTest(point) {
    const playhead = hitTestPlayheadBadge(point, state.layout, state.playheadNs);
    if (playhead) {
      return { type: "playhead", playhead };
    }
    const annotation = hitTestAnnotations(point, state.annotations);
    if (annotation) {
      return { type: "annotation", annotation };
    }
    const feedback = hitTestFeedback(point);
    if (feedback) {
      return { type: "feedback", flow: feedback };
    }
    const events = state.layout ? state.layout.events : [];
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index];
      if (
        point.x >= event.x &&
        point.x <= event.x + event.width &&
        point.y >= event.y + 5 &&
        point.y <= event.y + 5 + event.height
      ) {
        return { type: "event", id: event.id, event };
      }
    }
    return null;
  }

  function hitTestAnnotations(point, annotations) {
    if (!point || !Array.isArray(annotations)) {
      return null;
    }
    for (let index = annotations.length - 1; index >= 0; index -= 1) {
      const annotation = annotations[index];
      if (
        point.x >= annotation.x &&
        point.x <= annotation.x + annotation.width &&
        point.y >= annotation.y &&
        point.y <= annotation.y + annotation.height
      ) {
        return annotation;
      }
    }
    return null;
  }

  function hitTestPlayheadBadge(point, layout, playheadNs) {
    if (!point || !layout || !layout.timeScale) {
      return null;
    }
    const scale = layout.timeScale;
    const x = scaleTime(playheadNs, scale.tMinNs, scale.tMaxNs, scale.plotLeft, scale.plotRight);
    const label = `${Math.round(playheadNs)} ns`;
    const bounds = {
      x: x - 7,
      y: 24,
      width: measureLabelWidth(label) + 24,
      height: 26,
    };
    if (
      point.x >= bounds.x &&
      point.x <= bounds.x + bounds.width &&
      point.y >= bounds.y &&
      point.y <= bounds.y + bounds.height
    ) {
      return {
        type: "playhead",
        id: "playhead",
        label: "Timeline playhead",
        playheadNs: Math.round(playheadNs),
        x: bounds.x,
        y: bounds.y,
        width: bounds.width,
        height: bounds.height,
      };
    }
    return null;
  }

  function hitTestFeedback(point) {
    for (let index = 0; index < state.feedbackFlows.length; index += 1) {
      const flow = state.feedbackFlows[index];
      const phase = feedbackPacketProgress(flow);
      if (phase === null) {
        continue;
      }
      const from = eventCenter(flow.from);
      const to = eventCenter(flow.to);
      const lift = Math.min(92, Math.max(34, Math.abs(to.y - from.y) * 0.4 + 28));
      const mid = {
        x: (from.x + to.x) / 2,
        y: Math.min(from.y, to.y) - lift - (index % 3) * 9,
      };
      const current = quadraticPoint(from, mid, to, phase);
      if (distance(point, current) <= 13) {
        return flow;
      }
    }
    return null;
  }

  function screenToWorld(event) {
    const rect = els.stage.getBoundingClientRect();
    const x = event.clientX - rect.left;
    return {
      x,
      y: (event.clientY - rect.top) + state.camera.y,
      timeNs: pixelToTime(x),
    };
  }

  function renderTooltip(hit, pointerEvent) {
    if (!hit || !pointerEvent) {
      els.tooltip.hidden = true;
      return;
    }
    let lines;
    if (hit.type === "feedback") {
      lines = [
        hit.flow.label,
        `role: ${feedbackFlowRole(hit.flow)}`,
        `channel: ${hit.flow.channel}`,
        `${hit.flow.from.id} -> ${hit.flow.to.id}`,
      ];
    } else if (hit.type === "annotation") {
      lines = annotationTooltipLines(hit.annotation);
    } else if (hit.type === "playhead") {
      lines = [
        hit.playhead.label,
        `time: ${formatNumber(hit.playhead.playheadNs)} ns`,
        "animated timeline cursor",
      ];
    } else {
      const event = hit.event;
      const source = event.source && event.source.file ? `${basename(event.source.file)}:${event.source.line || 1}` : "source unavailable";
      lines = [
        event.label,
        `kind: ${event.kind}`,
        `time: ${formatNumber(event.t0Ns)} -> ${formatNumber(event.t1Ns)} ns`,
        `sequencer: ${event.sequencerId}`,
        source,
      ];
    }
    els.tooltip.innerHTML = lines.map((line) => `<div>${escapeHtml(line)}</div>`).join("");
    els.tooltip.hidden = false;
    const x = Math.min(window.innerWidth - 24, pointerEvent.clientX + 14);
    const y = Math.min(window.innerHeight - 24, pointerEvent.clientY + 14);
    els.tooltip.style.left = `${x}px`;
    els.tooltip.style.top = `${y}px`;
  }

  function renderInspector(hit) {
    if (!hit) {
      els.inspector.innerHTML = field("Selection", "No selection");
      return;
    }
    if (hit.type === "feedback") {
      const flow = hit.flow;
      els.inspector.innerHTML = [
        field("Feedback", flow.label),
        field("Role", feedbackFlowRole(flow)),
        field("Channel", flow.channel),
        field("Lifecycle", `${numberText(flow.emitNs)} -> ${numberText(flow.consumeNs)} ns`),
        field("From", flow.from.id),
        field("To", flow.to.id),
      ].join("");
      return;
    }
    if (hit.type === "annotation") {
      const annotation = hit.annotation;
      const event = annotation.event || {};
      els.inspector.innerHTML = [
        field(annotation.type === "loop" ? "Loop" : "Annotation", annotation.label),
        field("Type", annotation.type),
        field("Event", event.id || "unavailable"),
        field("Kind", event.kind || "unavailable"),
      ].join("");
      return;
    }
    if (hit.type === "playhead") {
      els.inspector.innerHTML = [
        field("Marker", hit.playhead.label),
        field("Time", `${numberText(hit.playhead.playheadNs)} ns`),
        field("Role", "Animated timeline cursor"),
      ].join("");
      return;
    }
    const event = hit.event;
    const source = event.source || {};
    els.inspector.innerHTML = [
      field("Event", event.id),
      field("Kind", event.kind),
      field("Sequencer", event.sequencerId),
      field("Lane", event.lane),
      field("Time", `${numberText(event.t0Ns)} -> ${numberText(event.t1Ns)} ns`),
      field("Confidence", event.confidence),
      field("Source", source.file ? `${basename(source.file)}:${source.line || 1}` : "unavailable"),
      field("Q1ASM", source.raw || ""),
    ].join("");
  }

  function updateMetrics() {
    const ir = state.ir || {};
    els.metricEvents.textContent = String(records(ir.events).length);
    els.metricFeedback.textContent = String(records(ir.feedback_flows).length);
    els.metricSequencers.textContent = String(sequencerIds(ir).length);
  }

  function renderFatal(error) {
    const message = error && error.message ? error.message : String(error);
    els.loadStatus.textContent = message;
    els.inspector.innerHTML = field("Error", message);
  }

  function updateDebugDataset() {
    if (!els.stage || !state.layout || !state.layout.timeScale) {
      return;
    }
    const scale = state.layout.timeScale;
    els.stage.dataset.timeMinNs = String(Math.round(scale.tMinNs));
    els.stage.dataset.timeMaxNs = String(Math.round(scale.tMaxNs));
    els.stage.dataset.ticks = scale.majorTicks.join(",");
    els.stage.dataset.rootScaleX = state.root ? String(state.root.scale.x) : "";
    els.stage.dataset.rootScaleY = state.root ? String(state.root.scale.y) : "";
  }

  function selectedId() {
    return state.selected && state.selected.type === "event" ? state.selected.id : null;
  }

  function sameSelection(a, b) {
    if (!a && !b) {
      return true;
    }
    if (!a || !b || a.type !== b.type) {
      return false;
    }
    if (a.type === "event") {
      return a.id === b.id;
    }
    if (a.type === "feedback") {
      return a.flow.id === b.flow.id;
    }
    if (a.type === "annotation") {
      return a.annotation.id === b.annotation.id;
    }
    if (a.type === "playhead") {
      return true;
    }
    return false;
  }

  function eventCenter(event) {
    return {
      x: Number(event.x) + Number(event.width) / 2,
      y: Number(event.y) + 5 + Number(event.height) / 2,
    };
  }

  function isCompactMode(mode) {
    return mode !== "debug";
  }

  function feedbackFlowRole(flow) {
    if (flow && flow.fromKind === "feedback_com" && flow.toKind === "feedback_pop") {
      return "feedback_com";
    }
    if (flow && flow.toKind === "feedback_pop") {
      return "acquire_to_pop";
    }
    return "feedback";
  }

  function feedbackPacketProgress(flow) {
    if (!flow) {
      return null;
    }
    const emitNs = Number(flow.emitNs);
    const consumeNs = Number(flow.consumeNs);
    if (!Number.isFinite(emitNs) || !Number.isFinite(consumeNs) || consumeNs <= emitNs) {
      return null;
    }
    const ageNs = feedbackTimelineAgeNs(emitNs);
    const latestArrivalNs = Math.min(consumeNs, emitNs + FEEDBACK_PACKET_FLIGHT_NS);
    const flightSpanNs = latestArrivalNs - emitNs;
    if (ageNs === null || flightSpanNs <= 0 || ageNs > flightSpanNs) {
      return null;
    }
    return easeOutCubic(clamp(ageNs / flightSpanNs, 0, 1));
  }

  function feedbackPopProgress(flow) {
    if (!flow) {
      return null;
    }
    const consumeNs = Number(flow.consumeNs);
    if (!Number.isFinite(consumeNs)) {
      return null;
    }
    const ageNs = feedbackTimelineAgeNs(consumeNs);
    if (ageNs === null || ageNs > FEEDBACK_POP_PULSE_NS) {
      return null;
    }
    return clamp(ageNs / FEEDBACK_POP_PULSE_NS, 0, 1);
  }

  function feedbackTimelineAgeNs(anchorNs) {
    if (!state.layout || !state.layout.timeScale) {
      return null;
    }
    const scale = state.layout.timeScale;
    const minNs = Number(scale.tMinNs);
    const maxNs = Number(scale.tMaxNs);
    const currentNs = Number(state.playheadNs);
    const eventNs = Number(anchorNs);
    if (
      !Number.isFinite(minNs) ||
      !Number.isFinite(maxNs) ||
      !Number.isFinite(currentNs) ||
      !Number.isFinite(eventNs) ||
      maxNs <= minNs ||
      eventNs < minNs ||
      eventNs > maxNs
    ) {
      return null;
    }
    const spanNs = maxNs - minNs;
    return (currentNs - eventNs + spanNs) % spanNs;
  }

  function easeOutCubic(value) {
    const t = clamp(value, 0, 1);
    return 1 - (1 - t) ** 3;
  }

  function isLoopOverlayEvent(event) {
    return event && event.kind === "loop_block";
  }

  function compactEventHeight(event) {
    if (isLoopOverlayEvent(event)) {
      return 18;
    }
    if (event.kind === "wait" || event.kind === "wait_trigger" || event.kind === "wait_sync") {
      return 10;
    }
    if (event.kind === "play" || event.kind === "acquire") {
      return 11;
    }
    if (event.kind === "branch_region") {
      return 7;
    }
    return 9;
  }

  function compactEventOffset(event) {
    if (isLoopOverlayEvent(event)) {
      return 7;
    }
    if (event.kind === "marker_state" || event.kind === "upd_param") {
      return 13;
    }
    if (event.kind === "play") {
      return 17;
    }
    if (event.kind === "wait" || event.kind === "wait_trigger" || event.kind === "wait_sync") {
      return 25;
    }
    if (event.kind === "acquire") {
      return 34;
    }
    if (event.kind === "feedback_pop" || event.kind === "feedback_com" || event.kind.startsWith("fb_")) {
      return 41;
    }
    if (event.kind === "branch_region") {
      return 43;
    }
    return 28;
  }

  function compactLabelVisible(event, widthPx, text) {
    if (isLoopOverlayEvent(event)) {
      return false;
    }
    if (!text || measureLabelWidth(text) > widthPx - 6) {
      return false;
    }
    if (event.kind === "wait" || event.kind === "wait_trigger") {
      return widthPx >= 44;
    }
    return widthPx >= 36 && !String(event.kind).startsWith("fb_");
  }

  function applyEventLabelLayout(events) {
    const occupied = [];
    const visibleEvents = events
      .filter((event) => event.labelLayout && event.labelLayout.visible)
      .map((event, index) => ({ event, index }))
      .sort((a, b) => eventLabelPriority(b.event) - eventLabelPriority(a.event) || a.index - b.index);

    for (const item of visibleEvents) {
      const event = item.event;
      const rect = labelRect(event);
      if (!rect || rect.width > Math.max(0, event.width - 6)) {
        event.labelLayout.visible = false;
        continue;
      }
      if (occupied.some((existing) => rectsOverlap(rect, existing))) {
        event.labelLayout.visible = false;
        continue;
      }
      occupied.push(rect);
    }
  }

  function labelRect(event) {
    if (!event || !event.labelLayout || !event.labelLayout.text) {
      return null;
    }
    return {
      x: event.labelLayout.x,
      y: event.labelLayout.y,
      width: measureLabelWidth(event.labelLayout.text),
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

  function measureLabelWidth(text) {
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

  function registerAnnotation(annotation) {
    if (!annotation || !Array.isArray(state.annotations)) {
      return;
    }
    state.annotations.push(annotation);
  }

  function annotationTooltipLines(annotation) {
    if (!annotation) {
      return ["Annotation"];
    }
    if (annotation.type === "loop") {
      return [
        annotation.label || "Loop",
        `event: ${annotation.event && annotation.event.id ? annotation.event.id : "unavailable"}`,
        "loop region marker",
      ];
    }
    const diagnostic = annotation.diagnostic || {};
    return [
      annotation.label || "Diagnostic",
      `severity: ${diagnostic.severity || "unknown"}`,
      `event: ${annotation.event && annotation.event.id ? annotation.event.id : "unavailable"}`,
    ];
  }

  function clearLayer(layer) {
    if (!layer) {
      return;
    }
    for (const child of [...layer.children]) {
      if (child.destroy) {
        child.destroy({ children: true });
      } else {
        layer.removeChild(child);
      }
    }
  }

  function roundedRect(g, x, y, width, height, radius, color, alpha) {
    g.roundRect(x, y, width, height, radius).fill({ color, alpha });
  }

  function strokeRect(g, x, y, width, height, radius, color, strokeWidth, alpha) {
    g.roundRect(x, y, width, height, radius).stroke({ color, width: strokeWidth, alpha });
  }

  function line(g, x1, y1, x2, y2, color, width, alpha) {
    g.moveTo(x1, y1).lineTo(x2, y2).stroke({ color, width, alpha });
  }

  function drawBracket(g, x0, x1, y, capY, color, width, alpha) {
    g.moveTo(x0, capY).lineTo(x0, y).lineTo(x1, y).lineTo(x1, capY).stroke({ color, width, alpha });
  }

  function circle(g, x, y, radius, color, alpha) {
    g.circle(x, y, radius).fill({ color, alpha });
  }

  function makeText(PIXI, text, size, color, weight) {
    return new PIXI.Text({
      text,
      style: {
        fill: color,
        fontFamily: "Inter, Segoe UI, sans-serif",
        fontSize: size,
        fontWeight: String(weight || 500),
      },
    });
  }

  function field(label, value) {
    return `<div class="field"><span>${escapeHtml(label)}</span><code>${escapeHtml(value || "")}</code></div>`;
  }

  function sequencerIds(ir) {
    const ids = records(ir.sequencers).map((seq) => String(seq.id || "")).filter(Boolean);
    if (ids.length) {
      return ids;
    }
    return [...new Set(records(ir.events).map((event) => String(event.sequencer_id || "")).filter(Boolean))];
  }

  function laneKey(event) {
    return `${event.sequencerId}::${event.lane}`;
  }

  function laneRank(lane) {
    const index = laneOrder.indexOf(lane);
    if (index >= 0) {
      return index;
    }
    if (lane.startsWith("debug.")) {
      return 100 + lane.charCodeAt(0);
    }
    return 40 + lane.charCodeAt(0);
  }

  function laneDisplay(lane) {
    return lane.replace(/^rt\./, "").replace(/^debug\./, "debug ");
  }

  function sequencerLabel(id) {
    return id.replace(/^qcm_/, "QCM ").replace(/^qrm/, "QRM");
  }

  function eventLabel(event, widthPx) {
    return fitLabelToWidth(event.label || event.kind, widthPx - 10);
  }

  function compactLabel(event, widthPx) {
    return fitLabelToWidth(eventLabelToken(event), widthPx - 10);
  }

  function eventLabelToken(event) {
    if (!event) {
      return "";
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

  function fitLabelToWidth(text, maxWidth) {
    const raw = String(text || "");
    if (!raw || maxWidth < measureLabelWidth("fb")) {
      return "";
    }
    if (measureLabelWidth(raw) <= maxWidth) {
      return raw;
    }
    const maxChars = Math.floor((maxWidth - 4) / 6);
    if (maxChars < 4) {
      return "";
    }
    return `${raw.slice(0, maxChars - 1)}...`;
  }

  function eventFill(event) {
    return colors.kind[event.kind] || colors.event[event.confidence] || colors.event.unknown;
  }

  function feedbackColor(channel, index) {
    const palette = {
      "1": 0xb58cff,
      "2": 0x55d6f4,
      "3": 0xff8bd1,
      "11": 0xf2c85b,
      "12": 0x45d68a,
      "13": 0xff7d72,
    };
    return palette[channel] || (index % 2 ? colors.feedbackAlt : colors.feedback);
  }

  function majorTicks(minTime, maxTime) {
    const span = Math.max(1, maxTime - minTime);
    const raw = span / 8;
    const step = niceStep(raw);
    const first = Math.ceil(minTime / step) * step;
    const ticks = [];
    for (let value = first; value <= maxTime + step * 0.25; value += step) {
      ticks.push(Math.round(value));
    }
    return ticks;
  }

  function niceStep(value) {
    const exponent = Math.floor(Math.log10(Math.max(1, value)));
    const base = 10 ** exponent;
    const ratio = value / base;
    if (ratio <= 1) {
      return base;
    }
    if (ratio <= 2) {
      return base * 2;
    }
    if (ratio <= 5) {
      return base * 5;
    }
    return base * 10;
  }

  function scaleTime(value, minTime, maxTime, left, right) {
    const span = Math.max(1, maxTime - minTime);
    return left + ((value - minTime) / span) * (right - left);
  }

  function timeValue(value) {
    if (isRecord(value) && Number.isFinite(Number(value.value))) {
      return Number(value.value);
    }
    return 0;
  }

  function numeric(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function records(value) {
    return Array.isArray(value) ? value.filter(isRecord) : [];
  }

  function isRecord(value) {
    return Boolean(value && typeof value === "object" && !Array.isArray(value));
  }

  function lerpPoint(a, b, t) {
    return {
      x: a.x + (b.x - a.x) * t,
      y: a.y + (b.y - a.y) * t,
    };
  }

  function quadraticPoint(a, b, c, t) {
    const p0 = lerpPoint(a, b, t);
    const p1 = lerpPoint(b, c, t);
    return lerpPoint(p0, p1, t);
  }

  function distance(a, b) {
    const dx = a.x - b.x;
    const dy = a.y - b.y;
    return Math.sqrt(dx * dx + dy * dy);
  }

  function zoomTimeWindowAt(anchorNs, factor) {
    if (!state.timeWindow || !state.fullTimeExtent) {
      return;
    }
    const current = state.timeWindow;
    const span = current.maxNs - current.minNs;
    const fullSpan = state.fullTimeExtent.maxNs - state.fullTimeExtent.minNs;
    const nextSpan = clamp(span * factor, Math.max(8, fullSpan / 5000), fullSpan);
    const anchor = clamp(anchorNs, current.minNs, current.maxNs);
    const ratio = span > 0 ? (anchor - current.minNs) / span : 0.5;
    state.timeWindow = clampTimeWindow(
      {
        minNs: anchor - nextSpan * ratio,
        maxNs: anchor + nextSpan * (1 - ratio),
      },
      state.fullTimeExtent,
    );
  }

  function panTimeWindowByPixels(window, deltaPixels) {
    if (!state.layout || !state.fullTimeExtent) {
      return window;
    }
    const scale = state.layout.timeScale;
    const nsPerPixel = (window.maxNs - window.minNs) / Math.max(1, scale.plotRight - scale.plotLeft);
    const deltaNs = deltaPixels * nsPerPixel;
    return clampTimeWindow(
      {
        minNs: window.minNs + deltaNs,
        maxNs: window.maxNs + deltaNs,
      },
      state.fullTimeExtent,
    );
  }

  function clampTimeWindow(window, extent) {
    const fullSpan = Math.max(1, extent.maxNs - extent.minNs);
    const minSpan = Math.max(8, fullSpan / 5000);
    let span = clamp(window.maxNs - window.minNs, minSpan, fullSpan);
    let minNs = Number(window.minNs);
    let maxNs = minNs + span;
    if (minNs < extent.minNs) {
      minNs = extent.minNs;
      maxNs = minNs + span;
    }
    if (maxNs > extent.maxNs) {
      maxNs = extent.maxNs;
      minNs = maxNs - span;
    }
    if (minNs < extent.minNs) {
      minNs = extent.minNs;
      span = fullSpan;
      maxNs = extent.maxNs;
    }
    return { minNs, maxNs };
  }

  function pixelToTime(x) {
    if (!state.layout || !state.layout.timeScale) {
      return 0;
    }
    const scale = state.layout.timeScale;
    const ratio = (x - scale.plotLeft) / Math.max(1, scale.plotRight - scale.plotLeft);
    return scale.tMinNs + ratio * (scale.tMaxNs - scale.tMinNs);
  }

  function clampCamera() {
    if (!state.layout || !els.stage) {
      return;
    }
    const rect = els.stage.getBoundingClientRect();
    const maxY = Math.max(0, state.layout.height - rect.height);
    state.camera.y = clamp(state.camera.y, 0, maxY);
    if (state.timeWindow && state.fullTimeExtent) {
      state.timeWindow = clampTimeWindow(state.timeWindow, state.fullTimeExtent);
    }
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function numberText(value) {
    return Number.isFinite(Number(value)) ? String(Math.round(Number(value))) : "unknown";
  }

  function formatNumber(value) {
    return Number.isFinite(Number(value)) ? Math.round(Number(value)).toLocaleString() : "unknown";
  }

  function basename(path) {
    return String(path).replace(/\\/g, "/").split("/").pop() || String(path);
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  if (typeof window !== "undefined") {
    window.__threePeakTimelineTestHooks = {
      applyEventLabelLayout,
      hitTestAnnotations,
      hitTestPlayheadBadge,
      measureLabelWidth,
    };
  }
})();
