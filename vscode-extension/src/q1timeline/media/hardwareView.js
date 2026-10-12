(function hardwareViewFactory(root) {
  const PAGE_SIZE = 16;
  const BLOCKS = [
    { id: "registers", title: "Registers", x: 16, y: 144, w: 138, h: 68 },
    { id: "q1", title: "Q1 core", x: 16, y: 32, w: 138, h: 80 },
    { id: "queue", title: "RT queue", x: 190, y: 32, w: 138, h: 80 },
    { id: "rt", title: "RT core", x: 364, y: 32, w: 138, h: 80 },
    { id: "awg", title: "AWG", x: 538, y: 32, w: 156, h: 80 },
    { id: "acquisition", title: "Acquisition", x: 364, y: 144, w: 156, h: 68 },
    { id: "bins", title: "Bins", x: 556, y: 144, w: 138, h: 68 },
  ];

  function escapeHtml(value) {
    return String(value === undefined || value === null ? "" : value)
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  }

  function display(value) {
    if (value === undefined || value === null) return "unknown";
    if (typeof value === "object") return value.display !== undefined ? String(value.display) : JSON.stringify(value);
    return String(value);
  }

  function short(value, limit = 22) {
    const text = display(value);
    return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
  }

  function eventLabel(event) {
    return event ? event.label || event.source && event.source.raw || event.kind : "No active event";
  }

  function moduleId(sequencer) {
    return sequencer.module === undefined || sequencer.module === null ? "" : String(sequencer.module);
  }

  function moduleLabel(id) {
    return id || "Module unassigned";
  }

  function statusLabel(status) {
    return String(status || "unknown").replaceAll("-", " ").replaceAll("_", " ");
  }

  function executionLabel(execution) {
    if (!execution) return "Static analysis";
    const parts = [execution.mode === "simulation" ? "Event-driven simulation" : execution.mode === "concrete" ? "Concrete execution" : statusLabel(execution.mode), statusLabel(execution.status)];
    if (Number.isFinite(execution.steps)) parts.push(`${execution.steps}${Number.isFinite(execution.max_steps) ? ` / ${execution.max_steps}` : ""} instructions`);
    if (execution.reason) parts.push(execution.reason);
    return parts.join(" · ");
  }

  function inputSource(source) {
    if (!source) return "Source unknown";
    const labels = { numerical_model: "Numerical model", supplied_trace: "Supplied trace", measured_input: "User-designated measured input", loopback: "Simulated loopback", scenario: "Scenario input", physical: "Physical model" };
    return `${labels[source.kind] || statusLabel(source.kind)}${source.label ? ` · ${source.label}` : ""}`;
  }

  function replayTrackHtml(prefix) {
    return `<div class="hardware-replay-body">
      <div class="hardware-replay-code"><div class="hardware-replay-heading">Q1ASM · recorded source lines <span data-value="${prefix}-source-location"></span></div><div class="hardware-replay-lines" data-value="${prefix}-source-lines"></div></div>
      <div class="hardware-replay-values"><div class="hardware-replay-heading">Registers · readable values</div><table class="hardware-table"><thead><tr><th>Register</th><th>Value</th><th>Change</th></tr></thead><tbody data-value="${prefix}-registers"></tbody></table><p class="hardware-panel-hint" data-value="${prefix}-pending"></p>${prefix === "project" ? "" : '<p class="hardware-panel-hint" data-value="replay-cores"></p>'}</div>
    </div>`;
  }

  function mount(container, ir, options = {}) {
    const model = root.q1lensHardwareState;
    const index = model.createIndex(ir);
    const sequencers = index.sequencers;
    const modules = [...new Set(sequencers.map(moduleId))];
    const ranges = {};
    for (const basis of ["local", "aligned"]) {
      const steps = sequencers.map((seq) => model.stepTimes(index, seq.id, basis)).filter((times) => times.length);
      ranges[basis] = steps.length
        ? { min: Math.min(...steps.map((times) => times[0])), max: Math.max(...steps.map((times) => times[times.length - 1])) }
        : { min: 0, max: 0 };
    }
    const events = new Map((ir.events || []).map((event) => [String(event.id), event]));
    const initial = options.initialState || {};
    const state = {
      sequencerId: sequencers.some((seq) => seq.id === initial.sequencerId) ? initial.sequencerId : sequencers[0] && sequencers[0].id,
      time: Number.isFinite(initial.time) ? Math.round(initial.time) : 0,
      basis: initial.basis === "aligned" ? "aligned" : "local",
      selectedBlock: BLOCKS.some((block) => block.id === initial.selectedBlock) || initial.selectedBlock === "simulation" ? initial.selectedBlock : "registers",
      signalStage: initial.signalStage || "dac",
      registerFormat: initial.registerFormat === "hex" ? "hex" : "decimal",
      showAllRegisters: initial.showAllRegisters === true,
      replaySpeed: [100, 450, 900].includes(initial.replaySpeed) ? initial.replaySpeed : 450,
      replayScope: sequencers.length > 1 && initial.replayScope !== "sequencer" ? "project" : "sequencer",
      binOffset: 0,
      level: ["modules", "sequencers", "detail"].includes(initial.level) ? initial.level : "modules",
      moduleId: modules.includes(initial.moduleId) ? initial.moduleId : undefined,
    };
    const initialSequencer = sequencers.find((seq) => seq.id === state.sequencerId);
    if (state.moduleId === undefined || state.level === "detail") state.moduleId = initialSequencer ? moduleId(initialSequencer) : "";
    if (!initialSequencer) state.level = "modules";
    let snapshot;
    let selectedRegister = "R0";
    let selectedBin;
    let frame;
    let destroyed = false;
    let sourceTarget;
    let times = [];
    let overviewKey;
    let overviewCards = [];
    let signalChartKey;
    let replayOpen = initial.replayOpen === true && state.level === "detail";
    let preparingReplay = false;
    let replayTimer;
    let replayTrace;
    let replayPosition = -1;
    let replayRegisterNames = [];
    const replaySourceTraces = new WeakMap();
    const projectReplayTrace = model.projectExecutionTrace(index);
    let projectReplayTracks;
    let replayEnd = 0;
    const overviewSteps = new Map();
    container.classList.add("q1-hardware");
    container.innerHTML = `
      <div class="hardware-heading">
        <div class="hardware-title">Hardware state <span class="hardware-badge" data-value="trace-kind">ANALYZED TRACE</span></div>
        <div class="hardware-selectors" data-view="selectors">
          <label>Module <select data-control="module" aria-label="Hardware module"></select></label>
          <label>Sequencer <select data-control="sequencer" aria-label="Hardware sequencer"></select></label>
        </div>
      </div>
      <div class="hardware-navigation">
        <button type="button" data-action="zoom-out" title="Show the enclosing hardware level" aria-label="Zoom out one hardware level"><span aria-hidden="true">↖</span> Zoom out</button>
        <nav class="hardware-breadcrumb" aria-label="Hardware hierarchy">
          <button type="button" data-level="modules">Modules</button>
          <span data-crumb="module" aria-hidden="true">/</span><button type="button" data-level="sequencers"></button>
          <span data-crumb="sequencer" aria-hidden="true">/</span><button type="button" data-level="detail"></button>
        </nav>
        <button type="button" data-action="replay-toggle" data-view="detail" aria-expanded="false">Execution replay</button>
      </div>
      <div class="hardware-controls">
        <button type="button" data-action="previous" title="Previous state change" aria-label="Previous state change">‹</button>
        <button type="button" data-action="next" title="Next state change" aria-label="Next state change">›</button>
        <label class="hardware-time">Time <input data-control="time" type="number" step="any" aria-label="Selected time in nanoseconds"> ns</label>
        <input data-control="scrubber" type="range" step="1" aria-label="Explore sequencer state over time">
        <span data-value="basis" class="hardware-muted"></span>
      </div>
      <p class="hardware-warnings" data-value="warnings" role="status" hidden></p>
      <section class="hardware-replay" data-view="replay" aria-label="Execution replay" hidden>
        <label class="hardware-replay-scope" data-replay-scope>Replay <select data-control="replay-scope" aria-label="Execution replay scope"><option value="project">All sequencers</option><option value="sequencer">Selected sequencer</option></select></label>
        <div class="hardware-replay-toolbar" data-replay-controls>
          <button type="button" data-action="replay-run">Run</button>
          <button type="button" data-action="replay-step">Step</button>
          <label>Speed <select data-control="replay-speed" aria-label="Execution replay speed"><option value="900">Slow</option><option value="450">Normal</option><option value="100">Fast</option></select></label>
          <span data-value="replay-progress"></span>
          <button type="button" data-action="replay-source">Open instruction source</button>
        </div>
        <div data-replay-single>${replayTrackHtml("replay")}</div>
        <div class="hardware-replay-project" data-replay-project hidden></div>
        <p class="hardware-replay-status" data-value="replay-status" role="status" aria-live="polite"></p>
        <div data-replay-empty hidden><button type="button" data-action="replay-prepare">Generate execution trace</button></div>
        <p class="hardware-panel-hint" data-replay-note>Replays the recorded execution. Playback speed does not change Q1/RT timing.</p>
      </section>
      <section class="hardware-overview" data-view="overview" aria-label="Hardware overview">
        <div class="hardware-overview-heading"><h3 data-value="overview-title" tabindex="-1"></h3><span data-value="overview-hint"></span></div>
        <div class="hardware-overview-grid" data-value="overview-cards"></div>
      </section>
      <div class="hardware-body" data-view="detail">
        <div class="hardware-diagram-panel">
          <div class="hardware-diagram-scroll" tabindex="0" role="region" aria-label="Sequencer diagram, scroll horizontally to see all blocks">
          <svg class="hardware-diagram" viewBox="0 0 710 234" role="group" aria-label="Sequencer command and acquisition data flow">
            <defs><marker id="hardware-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z"></path></marker></defs>
            <g class="hardware-links" marker-end="url(#hardware-arrow)">
              <path d="M154 72H186"></path><path d="M328 72H360"></path><path d="M502 72H534"></path>
              <path d="M85 144V116"></path><path d="M433 112V140"></path>
              <path class="hardware-data-link" d="M520 178H552"></path>
            </g>
            ${BLOCKS.map((block) => `<g class="hardware-block" data-block="${block.id}" role="button" tabindex="0" aria-label="Inspect ${block.title}" transform="translate(${block.x} ${block.y})"><rect width="${block.w}" height="${block.h}" rx="7"></rect><circle cx="${block.w - 14}" cy="17" r="3"></circle><text class="hardware-block-title" x="12" y="23">${block.title}</text><text class="hardware-block-value" data-block-value="${block.id}" x="12" y="45"></text><title data-block-title="${block.id}">${block.title}</title></g>`).join("")}
            <text class="hardware-flow-label" x="16" y="17">COMMAND EXECUTION</text>
            <text class="hardware-flow-label" x="556" y="230">ACQUISITION TARGETS</text>
          </svg>
          </div>
          <div class="hardware-core-status">
            <button type="button" data-action="q1-event"><span>Q1</span><span data-value="q1-event"></span></button>
            <button type="button" data-action="rt-event"><span>RT</span><span data-value="rt-event"></span></button>
          </div>
          <p class="hardware-execution" data-value="execution" role="status"></p>
          <div class="hardware-instruction-detail" data-value="instruction-detail"></div>
          <p class="hardware-footnote" data-value="timing-note"></p>
        </div>
        <div class="hardware-inspector">
          <div class="hardware-tabs" role="tablist" aria-label="Sequencer details">
            <button type="button" role="tab" data-tab="registers">Registers</button>
            <button type="button" role="tab" data-tab="parameters">Parameters</button>
            <button type="button" role="tab" data-tab="bins">Bins</button>
            <button type="button" role="tab" data-tab="simulation">Simulation</button>
          </div>
          <div class="hardware-tab-panel" data-panel="registers" role="tabpanel" aria-label="Registers">
            <div class="hardware-panel-toolbar"><span data-value="register-count"></span><label><input type="checkbox" data-control="all-registers">All 64</label><label>Format <select data-control="register-format" aria-label="Register number format"><option value="decimal">Decimal</option><option value="hex">Hex</option></select></label></div>
            <div class="hardware-register-grid">${Array.from({ length: 64 }, (_, i) => `<button type="button" class="hardware-register" data-register="R${i}" aria-label="Register R${i}"><span>R${i}</span><strong>?</strong></button>`).join("")}</div>
            <div class="hardware-register-detail" data-value="register-detail"></div>
            <div class="hardware-register-pager"><button type="button" data-action="register-previous">‹ Previous change</button><button type="button" data-action="register-next">Next change ›</button><span data-value="register-history-count"></span></div>
            <div class="hardware-register-history" data-value="register-history"></div>
          </div>
          <div class="hardware-tab-panel" data-panel="parameters" role="tabpanel" aria-label="Parameters" hidden>
            <p class="hardware-panel-hint">Q1 settings waiting to apply and parameters applied by RT.</p>
            <div class="hardware-table-scroll"><table class="hardware-table"><thead><tr><th>Parameter</th><th>Pending</th><th>Applied</th></tr></thead><tbody data-value="parameters"></tbody></table></div>
            <p class="hardware-panel-hint" data-value="queue-note"></p>
          </div>
          <div class="hardware-tab-panel" data-panel="bins" role="tabpanel" aria-label="Acquisition bins" hidden>
            <div class="hardware-panel-toolbar"><span data-value="bin-count"></span><div class="hardware-bin-pager"><button type="button" data-action="bins-previous" aria-label="Previous bins">‹</button><button type="button" data-action="bins-next" aria-label="Next bins">›</button></div></div>
            <p class="hardware-panel-hint" data-value="bins-note"></p>
            <div class="hardware-table-scroll"><table class="hardware-table"><thead><tr><th>Acquisition</th><th>Bin</th><th>Writes</th><th>Last target</th><th>Latest result</th></tr></thead><tbody data-value="bins"></tbody></table></div>
            <div class="hardware-bin-history" data-value="bin-history"></div>
          </div>
          <div class="hardware-tab-panel hardware-simulation" data-panel="simulation" role="tabpanel" aria-label="Simulation" hidden>
            <p class="hardware-panel-hint" data-value="simulation-queue"></p>
            <div class="hardware-panel-toolbar"><label>Signal stage <select data-control="signal-stage" aria-label="Signal stage"></select></label></div>
            <div data-value="signal-chart"></div>
            <div class="hardware-signal-values" data-value="signal-values"></div>
            <details class="hardware-signal-model"><summary>Model and assumptions</summary><div data-value="signal-model"></div></details>
            <h4>Input and execution events</h4>
            <div class="hardware-communications" data-value="communications"></div>
          </div>
        </div>
      </div>
      <div class="hardware-source" data-view="detail"><span data-value="selection-detail"></span><button type="button" data-action="source">Open source</button></div>`;

    const find = (selector) => container.querySelector(selector);
    const controls = Object.fromEntries(["module", "sequencer", "time", "scrubber", "register-format", "all-registers", "signal-stage", "replay-speed", "replay-scope"].map((name) => [name, find(`[data-control="${name}"]`)]));
    const values = Object.fromEntries(Array.from(container.querySelectorAll("[data-value]")).map((node) => [node.dataset.value, node]));
    const registerNodes = Array.from(container.querySelectorAll("[data-register]"));
    const blockNodes = Object.fromEntries(BLOCKS.map((block) => [block.id, find(`[data-block="${block.id}"]`)]));
    const blockValues = Object.fromEntries(BLOCKS.map((block) => [block.id, find(`[data-block-value="${block.id}"]`)]));
    const blockTitles = Object.fromEntries(BLOCKS.map((block) => [block.id, find(`[data-block-title="${block.id}"]`)]));

    function text(node, value) {
      const next = String(value);
      if (node.textContent !== next) node.textContent = next;
    }

    function html(node, value) {
      if (node.innerHTML !== value) node.innerHTML = value;
    }

    function notify() {
      if (options.onStateChange) options.onStateChange({ ...state, replayOpen });
    }

    function setSequencerOptions() {
      const selected = sequencers.find((seq) => seq.id === state.sequencerId);
      controls.module.innerHTML = modules.map((module) => `<option value="${escapeHtml(module)}">${escapeHtml(moduleLabel(module))}${module ? "" : " (module unspecified)"}</option>`).join("");
      controls.module.value = state.moduleId;
      controls.module.disabled = modules.length < 2;
      controls.sequencer.innerHTML = sequencers.filter((seq) => moduleId(seq) === state.moduleId).map((seq) => `<option value="${escapeHtml(seq.id)}">${escapeHtml(seq.label || seq.id)}</option>`).join("");
      controls.sequencer.value = state.sequencerId || "";
      controls.sequencer.disabled = !selected;
      times = stepTimesForView();
      updateReplayTrace();
    }

    function pauseReplay() {
      if (replayTimer !== undefined) root.clearTimeout(replayTimer);
      replayTimer = undefined;
    }

    function isProjectReplay() { return state.replayScope === "project"; }

    function replayCount() { return isProjectReplay() ? projectReplayTrace.frames.length : replayTrace.instructions.length; }

    function instructionPosition(trace, time = state.time) {
      const instructions = trace.instructions;
      let low = 0;
      let high = instructions.length;
      while (low < high) {
        const middle = (low + high) >>> 1;
        if (instructions[middle].end <= time) low = middle + 1;
        else high = middle;
      }
      return low - 1;
    }

    function syncReplayPosition() {
      if (!isProjectReplay()) { replayPosition = instructionPosition(replayTrace); return; }
      const frames = projectReplayTrace.frames;
      let low = 0;
      let high = frames.length;
      while (low < high) {
        const middle = (low + high) >>> 1;
        if (frames[middle] <= state.time) low = middle + 1;
        else high = middle;
      }
      replayPosition = low - 1;
    }

    function registerNames(trace) {
      const names = new Set();
      for (const instruction of trace.instructions) {
        for (const name of Object.keys(instruction.event.meta.register_reads || {})) if (/^R\d+$/.test(name)) names.add(name);
        for (const write of instruction.writes) names.add(write.register);
      }
      return [...names].sort((left, right) => Number(left.slice(1)) - Number(right.slice(1)));
    }

    function updateReplayTrace() {
      replayTrace = model.executionTrace(index, state.sequencerId, state.basis);
      replayEnd = -Infinity;
      for (const instruction of replayTrace.instructions) {
        replayEnd = Math.max(replayEnd, instruction.end);
        for (const write of instruction.writes) replayEnd = Math.max(replayEnd, write.time);
      }
      replayRegisterNames = registerNames(replayTrace);
      syncReplayPosition();
    }

    function useProjectClock(notifyScope = true) {
      if (state.basis === "aligned") {
        const offset = model.numericValue((index.alignment[state.sequencerId] || {}).offset);
        if (!alignmentUnknown() && offset !== undefined) state.time -= offset;
        state.basis = "local";
      }
      if (notifyScope && options.onReplayScopeChange) options.onReplayScopeChange("project");
      times = stepTimesForView();
      updateReplayTrace();
    }

    function advanceReplay() {
      if (replayPosition >= replayCount() - 1) return;
      replayPosition++;
      const instruction = isProjectReplay() ? undefined : replayTrace.instructions[replayPosition];
      state.time = isProjectReplay() ? projectReplayTrace.frames[replayPosition] : replayPosition === replayCount() - 1 ? replayEnd : instruction.end;
      state.binOffset = 0;
      if (replayPosition === replayCount() - 1) pauseReplay();
      render();
      const selected = instruction || replayTrace.instructions[instructionPosition(replayTrace)];
      if (selected && options.onSelectEvent) options.onSelectEvent(String(selected.event.id), { replay: true });
    }

    function scheduleReplay() {
      replayTimer = root.setTimeout(() => {
        if (destroyed || !replayOpen || state.level !== "detail" || !container.isConnected || root.document.hidden) { pauseReplay(); render(); return; }
        advanceReplay();
        if (replayPosition < replayCount() - 1) scheduleReplay();
      }, state.replaySpeed);
    }

    function renderReplay() {
      const showing = replayOpen && state.level === "detail";
      find('[data-view="replay"]').hidden = !showing;
      find('[data-action="replay-toggle"]').setAttribute("aria-expanded", String(showing));
      if (!showing) return;
      const instructions = replayTrace.instructions;
      const project = isProjectReplay();
      const count = replayCount();
      controls["replay-scope"].value = state.replayScope;
      find('[data-replay-scope]').hidden = sequencers.length < 2;
      find('[data-replay-controls]').hidden = !count;
      find('[data-replay-single]').hidden = !count || project;
      find('[data-replay-project]').hidden = !count || !project;
      find('[data-replay-note]').hidden = !count;
      text(find('[data-replay-note]'), project ? "All sequencers share the simulation clock. Q1 may advance while RT waits at wait_sync. Playback speed does not change execution timing." : "Replays the recorded execution. Playback speed does not change Q1/RT timing.");
      find('[data-replay-empty]').hidden = !!count || !options.onPrepareReplay;
      const prepareButton = find('[data-action="replay-prepare"]');
      prepareButton.disabled = preparingReplay;
      text(prepareButton, preparingReplay ? "Generating execution trace…" : "Generate execution trace");
      if (!count) { text(values["replay-status"], project ? projectReplayTrace.reason : replayTrace.reason); return; }
      const instruction = instructions[replayPosition];
      const finished = replayPosition === count - 1;
      find('[data-action="replay-run"]').disabled = false;
      find('[data-action="replay-step"]').disabled = finished;
      text(find('[data-action="replay-run"]'), replayTimer !== undefined ? "Pause" : finished ? "Replay" : "Run");
      controls["replay-speed"].value = String(state.replaySpeed);
      text(values["replay-progress"], `${replayPosition + 1} / ${count} ${project ? "frames" : "instructions"}`);
      find('[data-action="replay-source"]').hidden = project;
      if (project) { renderProjectReplay(finished); return; }
      const source = instruction && instruction.event.source;
      find('[data-action="replay-source"]').disabled = !(source && source.file && options.onSourceClick);
      const previousTime = replayPosition > 0 ? instructions[replayPosition - 1].end : instructions[0].time;
      renderReplayTrack(find('[data-replay-single]'), "replay", replayTrace, replayRegisterNames, snapshot, replayPosition, previousTime, state.basis);
      const execution = snapshot.execution;
      text(values["replay-status"], finished ? execution && execution.status !== "completed" ? `Recorded trace ended · ${executionLabel(execution)}` : "Execution replay complete" : instruction ? `Instruction ${replayPosition + 1} · ${instruction.event.source && instruction.event.source.raw || eventLabel(instruction.event)}` : "Ready to replay");
    }

    function renderReplayTrack(target, prefix, trace, names, current, position, previousTime, basis) {
      const node = (name) => target.querySelector(`[data-value="${prefix}-${name}"]`);
      const instruction = trace.instructions[position];
      const source = prefix === "project" ? (current.q1.event || instruction && instruction.event || {}).source : instruction && instruction.event.source;
      const rtSource = prefix === "project" && current.rt.status === "waiting" && current.rt.event && current.rt.event.source;
      text(node("source-location"), source && source.file ? `${source.file.split(/[\\/]/).pop()}:${source.line}` : "");
      const lines = node("source-lines");
      if (replaySourceTraces.get(lines) !== trace) {
        html(lines, trace.sourceLines.map((line) => `<div class="hardware-replay-source" data-replay-source="${escapeHtml(line.file)}" data-source-line="${line.line}" title="${escapeHtml(line.file)}:${line.line}"><span aria-hidden="true"></span><span>${line.line}</span><code>${escapeHtml(line.raw)}</code></div>`).join(""));
        replaySourceTraces.set(lines, trace);
      }
      for (const row of lines.children) {
        const active = !!source && row.dataset.replaySource === source.file && Number(row.dataset.sourceLine) === source.line;
        const waiting = !!rtSource && row.dataset.replaySource === rtSource.file && Number(row.dataset.sourceLine) === rtSource.line;
        const changed = row.classList.contains("is-active") !== active;
        row.classList.toggle("is-active", active);
        row.classList.toggle("is-rt-wait", waiting);
        row.firstElementChild.textContent = waiting ? "Ⅱ" : active ? "›" : "";
        if (active && changed) {
          if (row.offsetTop < lines.scrollTop || row.offsetTop + row.offsetHeight > lines.scrollTop + lines.clientHeight) lines.scrollTop = row.offsetTop;
        }
      }
      const pending = [];
      const registers = new Map(current.registers.map((register) => [register.register, register]));
      html(node("registers"), names.map((name) => {
        const register = registers.get(name);
        const previous = model.registerAt(index, current.sequencerId, name, previousTime, basis);
        const currentValue = register.known ? registerValue(register) : "?";
        const previousValue = previous && previous.known ? registerValue(previous) : "?";
        const changed = currentValue !== previousValue || (register.eventId || null) !== (previous && previous.eventId || null);
        const next = model.registerHistory(index, current.sequencerId, name, state.time, basis).next;
        if (next && next.event.meta && next.event.meta.execution_index <= (instruction ? instruction.event.meta.execution_index : -1)) pending.push(`${name} ← ${registerValue(next)} · readable at ${next.time} ns`);
        return `<tr data-replay-register="${name}"${changed ? ' class="is-changed"' : ""}><th scope="row">${name}</th><td><strong>${escapeHtml(currentValue)}</strong></td><td>${changed ? `${escapeHtml(previousValue)} → ${escapeHtml(currentValue)}` : "—"}</td></tr>`;
      }).join("") || '<tr><td colspan="3" class="hardware-empty">No register operands in this trace.</td></tr>');
      text(node("pending"), pending.join(" · ") || "No pending register writes.");
      if (prefix === "project") {
        for (const core of ["q1", "rt"]) {
          const event = current[core].event;
          text(node(core), `${core.toUpperCase()}: ${current[core].status} · ${event && event.source && event.source.raw || eventLabel(event)}`);
        }
      } else text(node("cores"), `Q1: ${current.q1.status} · RT: ${current.rt.status} · ${eventLabel(current.rt.event)}`);
    }

    function renderProjectReplay(finished) {
      if (!projectReplayTracks) {
        const grid = find('[data-replay-project]');
        html(grid, projectReplayTrace.traces.map(({ sequencerId }) => {
          const seq = sequencers.find((item) => item.id === sequencerId);
          return `<section class="hardware-replay-sequencer" data-replay-sequencer="${escapeHtml(sequencerId)}" aria-label="Replay ${escapeHtml(seq.label || seq.id)}"><div class="hardware-replay-track-heading"><strong>${escapeHtml(seq.label || seq.id)} <span>${escapeHtml(moduleLabel(moduleId(seq)))}</span></strong><span data-value="project-progress"></span><button type="button" data-action="project-replay-source" data-sequencer-id="${escapeHtml(sequencerId)}">Open Q1 source</button></div><div class="hardware-replay-core-status"><p data-value="project-q1"></p><p data-value="project-rt"></p></div>${replayTrackHtml("project")}<p class="hardware-panel-hint" data-value="project-execution"></p></section>`;
        }).join(""));
        projectReplayTracks = projectReplayTrace.traces.map((track, number) => ({ ...track, element: grid.children[number], names: registerNames(track.trace) }));
      }
      const previousTime = projectReplayTrace.frames[Math.max(0, replayPosition - 1)];
      for (const track of projectReplayTracks) {
        const current = model.snapshot(index, track.sequencerId, state.time, "local", { limit: 1 });
        const position = instructionPosition(track.trace);
        text(track.element.querySelector('[data-value="project-progress"]'), `${position + 1} / ${track.trace.instructions.length} instructions`);
        renderReplayTrack(track.element, "project", track.trace, track.names, current, position, previousTime, "local");
        track.element.querySelector('[data-action="project-replay-source"]').disabled = !(current.q1.event && current.q1.event.source && options.onSourceClick);
        text(track.element.querySelector('[data-value="project-execution"]'), finished ? executionLabel(current.execution) : "");
      }
      text(values["replay-status"], finished ? projectReplayTrace.traces.every(({ sequencerId }) => index.execution[sequencerId].status === "completed") ? "All sequencers finished" : "Recorded trace ended · inspect each sequencer's stop reason." : `Shared simulation time · ${state.time} ns`);
    }

    function stepTimesForView() {
      if (replayOpen && isProjectReplay()) return projectReplayTrace.frames;
      if (state.level === "detail") return model.stepTimes(index, state.sequencerId, state.basis);
      const key = JSON.stringify([state.basis, state.level === "modules" ? null : state.moduleId]);
      if (!overviewSteps.has(key)) {
        const merged = new Set();
        for (const seq of sequencers) {
          if (state.level !== "modules" && moduleId(seq) !== state.moduleId) continue;
          for (const time of model.stepTimes(index, seq.id, state.basis)) merged.add(time);
        }
        overviewSteps.set(key, [...merged].sort((left, right) => left - right));
      }
      return overviewSteps.get(key);
    }

    function renderNavigation() {
      const detail = state.level === "detail";
      const selected = sequencers.find((seq) => seq.id === state.sequencerId);
      container.dataset.navigationLevel = state.level;
      container.querySelectorAll('[data-view="detail"]').forEach((node) => { node.hidden = !detail; });
      find(".hardware-body").hidden = !detail || replayOpen;
      find(".hardware-source").hidden = !detail || replayOpen;
      find('[data-view="replay"]').hidden = !detail || !replayOpen;
      find('[data-view="overview"]').hidden = detail;
      find('[data-view="selectors"]').hidden = !detail;
      find('[data-action="zoom-out"]').disabled = state.level === "modules";
      const moduleCrumb = find('[data-level="sequencers"]');
      const sequencerCrumb = find('[data-level="detail"]');
      text(moduleCrumb, moduleLabel(state.moduleId));
      moduleCrumb.title = state.moduleId || "Module assignment is not specified in this project";
      text(sequencerCrumb, selected ? selected.label || selected.id : "Sequencer");
      moduleCrumb.hidden = state.level === "modules";
      find('[data-crumb="module"]').hidden = state.level === "modules";
      sequencerCrumb.hidden = !detail;
      find('[data-crumb="sequencer"]').hidden = !detail;
      container.querySelectorAll("[data-level]").forEach((node) => {
        const current = node.dataset.level === state.level;
        node.disabled = current;
        if (current) node.setAttribute("aria-current", "page");
        else node.removeAttribute("aria-current");
      });
    }

    function renderOverview() {
      const showingModules = state.level === "modules";
      const visibleSequencers = sequencers.filter((seq) => showingModules || moduleId(seq) === state.moduleId);
      const summaries = new Map(visibleSequencers.map((seq) => [seq.id, model.summarize(index, seq.id, state.time, state.basis)]));
      text(values["overview-title"], showingModules ? "Modules" : `${moduleLabel(state.moduleId)} · Sequencers`);
      text(values["overview-hint"], showingModules ? "Select a module to explore its sequencers" : "Select a sequencer to inspect its internal state");
      const key = showingModules ? "modules" : `sequencers:${state.moduleId}`;
      if (overviewKey !== key) {
        overviewKey = key;
        const cards = showingModules ? modules.map((id) => {
          const children = sequencers.filter((seq) => moduleId(seq) === id);
          return `<button type="button" class="hardware-module-card" data-module-card="${escapeHtml(id)}" aria-label="Open ${escapeHtml(moduleLabel(id))}: ${children.length} sequencer${children.length === 1 ? "" : "s"}">
            <span class="hardware-card-top"><span class="hardware-card-kind">${id ? "MODULE" : "MODULE UNSPECIFIED"}</span><span class="hardware-card-count">${children.length} sequencer${children.length === 1 ? "" : "s"}</span></span>
            <strong class="hardware-card-name">${escapeHtml(moduleLabel(id))}</strong>
            <span class="hardware-module-slots">${children.slice(0, 6).map((seq) => `<span class="hardware-module-slot" data-preview-sequencer="${escapeHtml(seq.id)}"><i aria-hidden="true"></i>${escapeHtml(seq.label || seq.id)}</span>`).join("")}${children.length > 6 ? `<span class="hardware-module-more">+${children.length - 6} more</span>` : ""}</span>
            <span class="hardware-card-status" data-module-status></span><span class="hardware-card-action">Explore sequencers <span aria-hidden="true">→</span></span>
          </button>`;
        }) : visibleSequencers.map((seq) => `<button type="button" class="hardware-sequencer-card" data-sequencer-card="${escapeHtml(seq.id)}" aria-label="Inspect sequencer ${escapeHtml(seq.label || seq.id)}">
          <span class="hardware-card-top"><span class="hardware-card-kind">SEQUENCER</span><span class="hardware-card-selection" data-card-selection>Selected</span></span>
          <strong class="hardware-card-name">${escapeHtml(seq.label || seq.id)}</strong>
          <span class="hardware-card-cores"><span data-card-core="q1"><span>Q1 core</span><strong data-card-status="q1"></strong></span><span class="hardware-card-core-arrow" aria-hidden="true">→</span><span data-card-core="rt"><span>RT core</span><strong data-card-status="rt"></strong></span></span>
          <span class="hardware-card-status" data-sequencer-command></span><span class="hardware-card-action">Inspect internal state <span aria-hidden="true">→</span></span>
          <span class="hardware-card-execution" data-sequencer-execution></span>
        </button>`);
        html(values["overview-cards"], cards.join("") || '<p class="hardware-empty">No sequencers are available in this analysis.</p>');
        overviewCards = Array.from(values["overview-cards"].querySelectorAll("[data-module-card], [data-sequencer-card]"));
      }
      for (const card of overviewCards) {
        if (card.dataset.moduleCard !== undefined) {
          const children = visibleSequencers.filter((seq) => moduleId(seq) === card.dataset.moduleCard);
          const active = children.filter((seq) => { const summary = summaries.get(seq.id); return summary.q1.status === "active" || summary.rt.status === "active"; }).length;
          const uncertain = children.filter((seq) => { const summary = summaries.get(seq.id); return summary.q1.status === "unknown" || summary.rt.status === "unknown"; }).length;
          text(card.querySelector("[data-module-status]"), `${active} executing${uncertain ? ` · ${uncertain} unknown` : ""} · at ${state.time} ns`);
          card.classList.toggle("is-selected", card.dataset.moduleCard === state.moduleId);
          card.querySelectorAll("[data-preview-sequencer]").forEach((node) => {
            const summary = summaries.get(node.dataset.previewSequencer);
            const active = summary.q1.status === "active" || summary.rt.status === "active";
            node.classList.toggle("is-active", active);
            node.title = `Q1: ${statusLabel(summary.q1.status)} · RT: ${statusLabel(summary.rt.status)}`;
          });
        } else {
          const summary = summaries.get(card.dataset.sequencerCard);
          const selected = card.dataset.sequencerCard === state.sequencerId;
          card.classList.toggle("is-selected", selected);
          card.querySelector("[data-card-selection]").hidden = !selected;
          for (const core of ["q1", "rt"]) {
            text(card.querySelector(`[data-card-status="${core}"]`), statusLabel(summary[core].status));
            card.querySelector(`[data-card-core="${core}"]`).classList.toggle("is-active", summary[core].status === "active");
          }
          const command = summary.rt.event || summary.q1.event;
          text(card.querySelector("[data-sequencer-command]"), command ? eventLabel(command) : "No resolved command at this time");
          text(card.querySelector("[data-sequencer-execution]"), executionLabel(index.execution[card.dataset.sequencerCard]));
          card.title = `Q1: ${statusLabel(summary.q1.status)} · RT: ${statusLabel(summary.rt.status)}${command ? ` · ${eventLabel(command)}` : ""}`;
        }
      }
      return [...new Set([...summaries.values()].flatMap((summary) => summary.warnings || []))];
    }

    function navigate(level, module, sequencer) {
      pauseReplay();
      if (level !== "detail" || sequencer !== undefined && sequencer !== state.sequencerId) replayOpen = false;
      if (module !== undefined) state.moduleId = module;
      if (sequencer !== undefined) state.sequencerId = sequencer;
      state.level = level;
      setSequencerOptions();
      render();
      const focusTarget = level === "detail" ? replayOpen ? find('[data-action="replay-toggle"]') : find(".hardware-diagram-scroll") : values["overview-title"];
      focusTarget.focus({ preventScroll: true });
    }

    function zoomOut() {
      if (state.level === "modules") return;
      navigate(state.level === "detail" ? "sequencers" : "modules");
    }

    function activeTab() {
      if (["acquisition", "bins"].includes(state.selectedBlock)) return "bins";
      if (state.selectedBlock === "simulation" || state.selectedBlock === "queue" && snapshot.simulation) return "simulation";
      return ["registers", "q1"].includes(state.selectedBlock) ? "registers" : "parameters";
    }

    function registerValue(register) {
      if (!register.known) return register.display || display(register.value);
      const number = model.numericValue(register.value);
      if (state.registerFormat === "hex" && number !== undefined) return `0x${(number >>> 0).toString(16).toUpperCase().padStart(8, "0")}`;
      return register.display === undefined ? display(register.value) : String(register.display);
    }

    function hasRegisterWrite(register) {
      return !!register.event || register.known || register.value !== undefined && register.value !== null;
    }

    function registerDetail(name, register) {
      if (!register) return `${name} = unknown · no register write at this time`;
      const value = registerValue(register);
      const parts = [`${name} = ${value}`];
      const meta = register.meta || {};
      if (register.confidence) parts.push(statusLabel(register.confidence));
      const reason = meta.reason || register.value && typeof register.value === "object" && (register.value.reason || register.value.source || register.value.expr);
      if (reason && !value.includes(String(reason))) parts.push(String(reason));
      if (register.time !== undefined) parts.push(`${hasRegisterWrite(register) && !meta.invalidated ? "readable at" : "state unknown since"} ${display(register.time)} ns (${state.basis})`);
      else parts.push("no register write at this time");
      if (state.basis === "aligned" && register.localTime !== undefined) parts.push(`local Q1 ${register.localTime} ns`);
      if (Array.isArray(meta.branch_assumptions) && meta.branch_assumptions.length) parts.push(`assumptions: ${meta.branch_assumptions.map((entry) => `${entry.branch_id}: ${entry.assumed_branch_path}`).join(", ")}`);
      if (register.source && register.source.file) parts.push(`${register.source.file}:${register.source.line || 1}`);
      if (register.changed) parts.push("changed now");
      return parts.join(" · ");
    }

    function registerInput(name, value, writerId) {
      const label = `${name} = ${registerValue({ value, known: model.numericValue(value) !== undefined })}`;
      const event = events.get(String(writerId));
      const writer = event && model.registerWrite(index, event.sequencer_id, name, writerId, state.basis);
      return writer ? `<button type="button" data-event="${escapeHtml(writerId)}" data-writer-register="${escapeHtml(name)}" title="Inspect ${escapeHtml(name)} when readable at ${writer.time} ns (${state.basis}) and its source instruction">${escapeHtml(label)} ↗</button>` : `<span>${escapeHtml(label)}</span>`;
    }

    function renderRegisters() {
      const registers = new Map(snapshot.registers.map((register) => [register.register, register]));
      text(values["register-count"], `${snapshot.registers.filter((register) => register.known).length} / 64 known`);
      registerNodes.forEach((node) => {
        const register = registers.get(node.dataset.register) || { known: false };
        const value = registerValue(register);
        node.hidden = !state.showAllRegisters && !hasRegisterWrite(register) && node.dataset.register !== selectedRegister;
        text(node.querySelector("strong"), hasRegisterWrite(register) ? value : "?");
        node.classList.toggle("is-unknown", !register.known);
        node.classList.toggle("is-assumed", register.confidence === "assumed");
        node.classList.toggle("is-changed", !!register.changed);
        node.classList.toggle("is-selected", node.dataset.register === selectedRegister);
        node.setAttribute("aria-pressed", String(node.dataset.register === selectedRegister));
        node.title = registerDetail(node.dataset.register, register);
        node.setAttribute("aria-label", node.title);
      });
      const register = registers.get(selectedRegister);
      const meta = register && register.meta || {};
      const provenance = meta.provenance || {};
      const valueText = (value) => registerValue({ value, known: model.numericValue(value) !== undefined });
      const change = Object.prototype.hasOwnProperty.call(meta, "previous_value") ? `<p class="hardware-register-change">${escapeHtml(selectedRegister)}: ${escapeHtml(valueText(meta.previous_value))} → ${escapeHtml(registerValue(register))}</p>` : "";
      const expression = provenance.expression ? `<p>Calculation: <code>${escapeHtml(provenance.expression)}</code>${provenance.symbol && provenance.symbol !== selectedRegister ? ` · ${escapeHtml(provenance.symbol)}` : ""}</p>` : "";
      const inputs = provenance.inputs && Object.entries(provenance.inputs);
      const operands = inputs && inputs.length ? `<p class="hardware-register-inputs">Values read: ${inputs.map(([name, input]) => registerInput(name, input.value, input.event_id)).join(" · ")}</p>` : "";
      const steps = Array.isArray(provenance.steps) ? provenance.steps : [];
      const start = Math.max(0, steps.length - 8);
      const trace = steps.length ? `<details><summary>Calculation steps${start ? ` (last 8 of ${steps.length})` : ` (${steps.length})`}</summary><ol>${steps.slice(start).map((step, offset) => `<li><code>${escapeHtml(step.expression)} = ${escapeHtml(valueText(step.value))}</code>${step.source && step.source.file ? ` <button type="button" data-provenance-step="${start + offset}"${options.onSourceClick ? "" : " disabled"}>${escapeHtml(step.source.file)}:${escapeHtml(step.source.line || 1)}</button>` : ""}</li>`).join("")}</ol></details>` : "";
      const inputEvent = provenance.input_event_id ? `<p><button type="button" data-event="${escapeHtml(provenance.input_event_id)}" data-communication-event="${escapeHtml(provenance.input_event_id)}">Show received input that supplied this value</button></p>` : "";
      html(values["register-detail"], `${change}<p>${escapeHtml(registerDetail(selectedRegister, register))}</p>${expression}${operands}${inputEvent}${trace}`);
      const history = model.registerHistory(index, state.sequencerId, selectedRegister, state.time, state.basis);
      find('[data-action="register-previous"]').disabled = !history.previous;
      find('[data-action="register-next"]').disabled = !history.next;
      text(values["register-history-count"], `${selectedRegister} · ${history.count} / ${history.total} changes`);
      html(values["register-history"], history.entries.length ? `<span>Recent changes${history.count > history.entries.length ? ` · latest ${history.entries.length}` : ""}</span><div>${history.entries.map((entry) => `<button type="button" data-register-time="${entry.time}" title="${escapeHtml(registerDetail(selectedRegister, entry))}"><span>${entry.time} ns</span><strong>${escapeHtml(registerValue(entry))}</strong><span>${escapeHtml(statusLabel(entry.confidence || (entry.known ? "exact" : "unknown")))}</span></button>`).join("")}</div>` : '<span>No register changes at this cursor.</span>');
      return register;
    }

    function renderInstruction() {
      const event = snapshot.q1.event;
      if (!event) { text(values["instruction-detail"], "Q1 operands are unavailable at this cursor."); return; }
      const meta = event.meta || {};
      const reads = meta.register_reads;
      const parts = [];
      if (Number.isInteger(meta.execution_index)) parts.push(`<strong>Instruction ${meta.execution_index + 1}</strong>`);
      if (reads && Object.keys(reads).length) parts.push(`<span>Read at issue: ${Object.entries(reads).map(([name, value]) => {
        const writer = model.registerAt(index, event.sequencer_id, name, model.eventTime(index, event, state.basis), state.basis);
        return registerInput(name, value, writer && writer.eventId);
      }).join(" · ")}</span>`);
      if (meta.input_event_ids && meta.input_event_ids.length) parts.push(`<span>Received input: ${meta.input_event_ids.map((id) => `<button type="button" data-event="${escapeHtml(id)}">${escapeHtml(id)}</button>`).join(" ")}</span>`);
      if (Object.prototype.hasOwnProperty.call(meta, "flags_before") || Object.prototype.hasOwnProperty.call(meta, "flags_after")) {
        const flags = (value) => value ? ["zf", "nf", "cf", "of"].map((name) => `${name.toUpperCase()}=${value[name] === undefined ? "?" : Number(value[name])}`).join(" ") : "unknown";
        parts.push(`<span>Flags before → after: ${escapeHtml(flags(meta.flags_before))} → ${escapeHtml(flags(meta.flags_after))}</span>`);
      }
      html(values["instruction-detail"], parts.join("") || '<span>Register reads and flags were not recorded for this instruction.</span>');
    }

    function renderParameters() {
      html(values.parameters, snapshot.parameters.length ? snapshot.parameters.map((parameter) => {
        const cell = (entry, applied) => {
          if (!entry) return '<span class="hardware-muted">—</span>';
          const origin = entry.issueEventId ? ` data-instruction-event="${escapeHtml(entry.issueEventId)}"` : "";
          const timing = applied && Number.isFinite(entry.appliedAt) ? `<br><small class="hardware-muted">Applied at ${entry.appliedAt} ns (${state.basis})${entry.time !== entry.appliedAt ? ` · RT event at ${entry.time} ns` : ""}</small>` : "";
          return `<button type="button" data-event="${escapeHtml(entry.issueEventId || entry.eventId || "")}"${origin} title="${entry.issueEventId ? "Show parameter instruction" : "Show RT event"}">${escapeHtml(entry.display === undefined ? display(entry.value) : entry.display)}</button>${timing}`;
        };
        return `<tr><th>${escapeHtml(parameter.field)}</th><td>${cell(parameter.pending, false)}</td><td>${cell(parameter.applied, true)}</td></tr>`;
      }).join("") : '<tr><td colspan="3" class="hardware-empty">No resolved parameter state at this time.</td></tr>');
      text(values["queue-note"], snapshot.simulation ? `Simulated RT queue: ${display(snapshot.simulation.queue.depth)} / ${display(snapshot.simulation.queue.capacity)} packets. Pending settings become applied when their captured RT packet executes.` : "RT queue occupancy is unavailable in analytical timing. Parameter values follow the analyzed execution path.");
    }

    function resultLabel(result) {
      if (!result || result === "unknown") return "unknown";
      return `I ${display(result.i)} · Q ${display(result.q)}${result.units ? ` (${result.units})` : result.source && result.source.kind === "scenario" ? " (scenario values)" : ""} · threshold ${display(result.thresholded)}`;
    }

    function renderSimulation() {
      const simulation = snapshot.simulation;
      if (!simulation) {
        text(values["simulation-queue"], "Simulation data is unavailable. Select simulation execution mode and refresh the preview.");
        controls["signal-stage"].disabled = true;
        for (const name of ["signal-chart", "signal-values", "signal-model", "communications"]) text(values[name], "");
        signalChartKey = undefined;
        return;
      }
      const queue = simulation.queue;
      const stall = simulation.stall;
      text(values["simulation-queue"], `RT queue ${display(queue.depth)} / ${display(queue.capacity)} packets${stall ? ` · Q1 stalled: ${statusLabel(stall.event.meta.reason)} (${stall.time}–${stall.end} ns)` : ""}. Startup ${display(simulation.config.startup_delay_ns)} ns · simulation end ${display(simulation.config.end_time_ns)} ns (local).`);
      const stages = index.signalStages.get(state.sequencerId) || new Map();
      if (!stages.has(state.signalStage)) state.signalStage = stages.keys().next().value;
      html(controls["signal-stage"], [...stages.keys()].map((stage) => `<option value="${escapeHtml(stage)}">${escapeHtml(statusLabel(stage))}</option>`).join(""));
      controls["signal-stage"].value = state.signalStage || "";
      controls["signal-stage"].disabled = !stages.size;
      const signal = model.signalAt(index, state.sequencerId, state.signalStage, state.time, state.basis);
      if (signal) {
        // Paths are prepared once by the state index; cursor changes read only one sample per path.
        const chartKey = JSON.stringify([state.sequencerId, state.signalStage, state.basis]);
        if (signalChartKey !== chartKey) {
          const paths = signal.traces.flatMap((trace) => trace.segments.map((points) => {
            const attributes = `class="hardware-signal-path" data-path="${escapeHtml(trace.path)}"`;
            if (points.includes(" ")) return `<polyline ${attributes} points="${points}"></polyline>`;
            const [x, y] = points.split(",");
            return `<circle ${attributes} cx="${x}" cy="${y}" r="2"></circle>`;
          })).join("");
          html(values["signal-chart"], `<svg class="hardware-signal-chart" viewBox="0 0 600 160" role="img" aria-label="${escapeHtml(state.signalStage)} signal in normalized full scale"><line class="hardware-signal-axis" x1="0" y1="150" x2="600" y2="150"></line>${paths}<line class="hardware-signal-cursor" x1="0" y1="0" x2="0" y2="160"></line></svg><div class="hardware-signal-range"><span>${signal.start + signal.offset} ns</span><span>${signal.min.toPrecision(3)}…${signal.max.toPrecision(3)} full scale</span><span>${signal.end + signal.offset} ns</span></div>`);
          signalChartKey = chartKey;
        }
        const cursor = values["signal-chart"].querySelector(".hardware-signal-cursor");
        const cursorX = (signal.localTime - signal.start) / (signal.end - signal.start) * 600;
        cursor.setAttribute("visibility", Number.isFinite(cursorX) && cursorX >= 0 && cursorX <= 600 ? "visible" : "hidden");
        cursor.setAttribute("x1", String(Number.isFinite(cursorX) ? cursorX : 0));
        cursor.setAttribute("x2", String(Number.isFinite(cursorX) ? cursorX : 0));
        html(values["signal-values"], signal.values.map((entry) => `<p><strong>${escapeHtml(entry.path.toUpperCase())}: ${escapeHtml(display(entry.value))}</strong>${entry.sampleTime === undefined ? signal.localTime === undefined ? " · alignment unavailable" : " · outside available trace" : ` · sample ${entry.sampleTime} ns`}<br>${escapeHtml(inputSource(entry.source))}</p>`).join(""));
      } else {
        text(values["signal-chart"], "No physical signal trace was exported for this sequencer.");
        signalChartKey = undefined;
        text(values["signal-values"], "");
      }
      const signals = simulation.signals;
      html(values["signal-model"], signals && signals.model ? `<p>${escapeHtml(signals.model)} · integration: ${escapeHtml(signals.units && signals.units.integration || "unknown units")}</p><ul>${(signals.assumptions || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul><p>Curves show the exported trace; the cursor shows the sample at the selected time. Exported samples may be decimated.</p>` : "Signal model unavailable.");
      const communication = simulation.communications;
      html(values.communications, `<p>${communication.count} / ${communication.total} events${communication.count > communication.entries.length ? ` · latest ${communication.entries.length}` : ""}${communication.next ? ` · <button type="button" data-event="${escapeHtml(communication.next.event.id)}">Next at ${communication.next.time} ns</button>` : ""}</p>${communication.entries.map((entry) => {
        const meta = entry.event.meta || {};
        const details = entry.event.kind === "acquisition_result" ? resultLabel(meta) : [meta.id === undefined ? null : `id ${display(meta.id)}`, meta.address === undefined ? null : `address ${display(meta.address)}`, meta.value === undefined ? null : `value ${display(meta.value)}`, meta.targets ? `to ${display(meta.targets)}` : null, meta.reason && statusLabel(meta.reason), meta.condition === undefined ? null : `condition ${display(meta.condition)}`].filter(Boolean).join(" · ");
        return `<div class="hardware-communication"><button type="button" data-event="${escapeHtml(entry.event.id)}">${entry.time} ns · ${escapeHtml(statusLabel(entry.event.kind))}</button><span>${escapeHtml(details)}</span>${meta.input_index === undefined ? "" : `<span>Scenario input ${escapeHtml(meta.input_index)}</span>`}${meta.source || meta.input_source ? `<span>${escapeHtml(inputSource(meta.input_source || meta.source))}</span>` : ""}${meta.source_event_id ? `<button type="button" data-event="${escapeHtml(meta.source_event_id)}">Show sending event</button>` : ""}${entry.event.kind === "feedback_send" && meta.q1_issue_event_id ? `<button type="button" data-event="${escapeHtml(meta.q1_issue_event_id)}" data-instruction-event="${escapeHtml(meta.q1_issue_event_id)}">Show sending instruction</button>` : ""}</div>`;
      }).join("")}`);
    }

    function renderBins() {
      const bins = snapshot.bins;
      text(values["bins-note"], snapshot.simulation ? "Writes count executed acquire commands. Results appear only after acquisition completion; their input source is shown below." : "Observed acquire commands, not hardware average counts. I/Q and threshold results are unknown in analytical timing.");
      text(values["bin-count"], bins.total ? `${state.binOffset + 1}–${Math.min(state.binOffset + PAGE_SIZE, bins.total)} of ${bins.total} targets` : "No acquisition targets yet");
      find('[data-action="bins-previous"]').disabled = state.binOffset === 0;
      find('[data-action="bins-next"]').disabled = state.binOffset + PAGE_SIZE >= bins.total;
      html(values.bins, bins.records.length ? bins.records.map((bin) => `<tr><td>${escapeHtml(display(bin.acquisitionIndex))}</td><td><button type="button" data-bin="${escapeHtml(`${bin.acquisitionIndex}:${bin.binIndex}`)}" title="Inspect this bin's target history">${escapeHtml(display(bin.binIndex))}</button></td><td>${escapeHtml(bin.count)}</td><td><button type="button" data-event="${escapeHtml(bin.eventId || "")}" title="Select last acquisition targeting this bin">${escapeHtml(display(bin.lastTime))} ns</button></td><td>${bin.result === "unknown" ? "unknown" : `<button type="button" data-event="${escapeHtml(bin.result.eventId)}" title="Ready at ${bin.result.readyTime} ns; ${escapeHtml(inputSource(bin.result.input_source || bin.result.source))}">${escapeHtml(resultLabel(bin.result))}</button>`}</td></tr>`).join("") : '<tr><td colspan="5" class="hardware-empty">No resolved bin targets at this time.</td></tr>');
      const bin = bins.records.find((entry) => `${entry.acquisitionIndex}:${entry.binIndex}` === selectedBin);
      html(values["bin-history"], bin ? `<span>Acquisition ${escapeHtml(bin.acquisitionIndex)} · bin ${escapeHtml(bin.binIndex)} · latest ${bin.history.length} targets</span><div>${bin.history.map((entry) => `<button type="button" data-event="${escapeHtml(entry.eventId)}">${escapeHtml(entry.time)} ns</button>`).join("")}</div>${bin.result === "unknown" ? "<p>No completed result at this cursor.</p>" : `<p>${escapeHtml(resultLabel(bin.result))} · ready ${bin.result.readyTime} ns<br>${escapeHtml(inputSource(bin.result.input_source || bin.result.source))}</p>`}` : '<span>Select a bin number to inspect its target history.</span>');
    }

    function setBlock(id, value, active, detail) {
      text(blockValues[id], short(value));
      text(blockTitles[id], detail || value);
      blockNodes[id].classList.toggle("is-active", !!active);
      blockNodes[id].classList.toggle("is-selected", state.selectedBlock === id);
      blockNodes[id].setAttribute("aria-pressed", String(state.selectedBlock === id));
      blockNodes[id].setAttribute("aria-label", `${BLOCKS.find((block) => block.id === id).title}: ${value}. Inspect details.`);
    }

    function selectedEvent() {
      if (state.selectedBlock === "q1") return snapshot.q1.event;
      if (state.selectedBlock === "rt" || state.selectedBlock === "queue") return snapshot.rt.event;
      if (state.selectedBlock === "awg") return (snapshot.awg.find((entry) => entry.event) || {}).event;
      if (state.selectedBlock === "acquisition" || state.selectedBlock === "bins") return snapshot.acquisition.event;
      return undefined;
    }

    function alignmentUnknown() {
      const alignment = index.alignment[state.sequencerId] || {};
      return alignment.unaligned || alignment.confidence === "unknown" || (alignment.offset !== undefined && model.numericValue(alignment.offset) === undefined);
    }

    function render(notifyChange = true) {
      if (destroyed) return;
      const range = ranges[state.basis];
      const unavailableAlignment = state.basis === "aligned" && alignmentUnknown();
      const clampedTime = unavailableAlignment ? state.time : Math.min(range.max, Math.max(range.min, state.time));
      if (state.time !== clampedTime) { state.time = clampedTime; syncReplayPosition(); }
      for (const control of [controls.time, controls.scrubber]) {
        control.min = String(unavailableAlignment ? Math.min(range.min, state.time) : range.min);
        control.max = String(unavailableAlignment ? Math.max(range.max, state.time) : range.max);
        control.value = String(state.time);
        control.disabled = !sequencers.length || range.max === range.min;
      }
      controls["register-format"].value = state.registerFormat;
      controls["all-registers"].checked = state.showAllRegisters;
      text(values.basis, state.basis === "aligned" ? "Aligned time" : "Local time");
      find('[data-action="previous"]').disabled = !times.length || times[0] >= state.time;
      find('[data-action="next"]').disabled = !times.length || times[times.length - 1] <= state.time;
      const stepScope = state.level === "detail" ? state.sequencerId || "no sequencer" : "visible sequencers";
      find('[data-action="previous"]').title = `Previous state change · ${stepScope}`;
      find('[data-action="next"]').title = `Next state change · ${stepScope}`;
      renderNavigation();
      find('[data-action="replay-toggle"]').setAttribute("aria-expanded", String(replayOpen && state.level === "detail"));
      text(values["trace-kind"], sequencers.some((seq) => index.simulation[seq.id]) ? "SIMULATED TRACE" : "ANALYZED TRACE");
      if (state.level !== "detail") {
        const warnings = renderOverview();
        values.warnings.hidden = warnings.length === 0;
        text(values.warnings, warnings.join(" · "));
        sourceTarget = undefined;
        if (notifyChange) notify();
        return;
      }
      snapshot = model.snapshot(index, state.sequencerId, state.time, state.basis, { offset: state.binOffset, limit: PAGE_SIZE });
      if (state.binOffset && state.binOffset >= snapshot.bins.total) {
        state.binOffset = 0;
        snapshot = model.snapshot(index, state.sequencerId, state.time, state.basis, { offset: 0, limit: PAGE_SIZE });
      }
      const warnings = snapshot.warnings.map((warning) => typeof warning === "string" ? warning : warning.message || display(warning));
      values.warnings.hidden = warnings.length === 0;
      text(values.warnings, warnings.join(" · "));
      renderReplay();
      if (replayOpen) { sourceTarget = undefined; if (notifyChange) notify(); return; }
      const known = snapshot.registers.filter((register) => register.known).length;
      text(values.execution, executionLabel(snapshot.execution));
      text(values["trace-kind"], snapshot.simulation ? "SIMULATED TRACE" : "ANALYZED TRACE");
      text(values["timing-note"], snapshot.simulation ? "Q1 registers advance independently of RT execution. Timing uses an event-driven model of queues and configured inputs. Signal and result sources identify modeled values, supplied traces, or supplied measurements." : "Q1 registers advance independently of RT execution. Timing is analytical; RT queue stalls are not modeled. Values are computed from the program, not measured on hardware.");
      values.execution.classList.toggle("is-incomplete", !!snapshot.execution && snapshot.execution.status !== "completed");
      renderInstruction();
      setBlock("registers", `${known} / 64 known`, snapshot.registers.some((register) => register.changed));
      setBlock("q1", snapshot.q1.status, ["active", "stalled"].includes(snapshot.q1.status), eventLabel(snapshot.q1.event));
      setBlock("queue", snapshot.simulation ? `${display(snapshot.simulation.queue.depth)} / ${display(snapshot.simulation.queue.capacity)} packets` : "Depth unknown", snapshot.simulation && snapshot.simulation.queue.depth > 0, snapshot.simulation ? "Simulated RT packet queue. Inspect queue stalls, input events and signals." : "RT command queue. Occupancy is unavailable in analytical timing.");
      setBlock("rt", snapshot.rt.status, snapshot.rt.status === "active", eventLabel(snapshot.rt.event));
      const plays = snapshot.awg.filter((entry) => entry.event);
      setBlock("awg", plays.length ? "Last play command" : "No resolved play", false, plays.map((entry) => `Path ${entry.path}: ${eventLabel(entry.event)}`).join(" · ") || "No resolved play command. Waveform duration is not inferred from RT command duration.");
      setBlock("acquisition", snapshot.acquisition.event ? "Last acquire command" : "No resolved acquire", snapshot.acquisition.status === "active", eventLabel(snapshot.acquisition.event));
      setBlock("bins", `${snapshot.bins.total} targets`, snapshot.acquisition.status === "active", snapshot.simulation ? "Acquisition targets, completed I/Q and threshold results, with input provenance." : "Acquisition targets and their write history; measured results are unavailable.");
      for (const core of ["q1", "rt"]) {
        text(values[`${core}-event`], `${snapshot[core].status} · ${eventLabel(snapshot[core].event)}`);
        find(`[data-action="${core}-event"]`).disabled = !snapshot[core].event;
      }
      const tab = activeTab();
      container.querySelectorAll("[data-tab]").forEach((node) => {
        const selected = node.dataset.tab === tab;
        node.setAttribute("aria-selected", String(selected));
        node.tabIndex = selected ? 0 : -1;
      });
      container.querySelectorAll("[data-panel]").forEach((node) => { node.hidden = node.dataset.panel !== tab; });
      const register = renderRegisters();
      if (tab === "parameters") renderParameters();
      if (tab === "bins") renderBins();
      if (tab === "simulation") renderSimulation();
      const event = selectedEvent();
      sourceTarget = state.selectedBlock === "registers" && register ? { source: register.source, eventId: register.eventId } : event ? { source: event.source, eventId: event.id } : undefined;
      const bin = snapshot.bins.records.find((entry) => `${entry.acquisitionIndex}:${entry.binIndex}` === selectedBin);
      if (state.selectedBlock === "bins" && bin) sourceTarget = { source: bin.source, eventId: bin.eventId };
      const source = sourceTarget && sourceTarget.source;
      text(values["selection-detail"], source && source.file ? `${source.file}:${source.line || 1}` : "Select a register or active block to inspect its source instruction.");
      find('[data-action="source"]').disabled = !(source && source.file && options.onSourceClick);
      if (notifyChange) notify();
    }

    function setTime(time, notifyChange = true) {
      if (!Number.isFinite(time)) return;
      pauseReplay();
      state.time = Math.round(time);
      syncReplayPosition();
      state.binOffset = 0;
      render(notifyChange);
    }

    function chooseEvent(eventId, notifyEvent = true) {
      const event = events.get(String(eventId));
      if (!event) return;
      const time = model.eventTime(index, event, state.basis);
      const seqId = String(event.sequencer_id);
      const sequencer = sequencers.find((seq) => seq.id === seqId);
      if (!sequencer) return;
      pauseReplay();
      if (state.sequencerId !== seqId) replayOpen = false;
      state.sequencerId = seqId;
      state.moduleId = moduleId(sequencer);
      state.level = "detail";
      setSequencerOptions();
      if (Number.isFinite(time)) setTime(time);
      else render();
      if (notifyEvent && options.onSelectEvent) options.onSelectEvent(String(event.id));
    }

    function chooseRegisterWriter(register, eventId) {
      const event = events.get(String(eventId));
      if (!event) return;
      const writer = model.registerWrite(index, event.sequencer_id, register, eventId, state.basis);
      const sequencer = sequencers.find((seq) => seq.id === String(event.sequencer_id));
      if (!writer || !sequencer) return;
      pauseReplay();
      if (state.sequencerId !== sequencer.id) replayOpen = false;
      selectedRegister = register;
      state.selectedBlock = "registers";
      state.sequencerId = sequencer.id;
      state.moduleId = moduleId(sequencer);
      state.level = "detail";
      setSequencerOptions();
      setTime(writer.time);
      if (options.onSelectEvent) options.onSelectEvent(String(event.id));
    }

    function step(direction) {
      let low = 0;
      let high = times.length;
      while (low < high) {
        const middle = (low + high) >>> 1;
        if (times[middle] < state.time || (direction > 0 && times[middle] === state.time)) low = middle + 1;
        else high = middle;
      }
      const next = times[direction < 0 ? low - 1 : low];
      if (next !== undefined) setTime(next);
    }

    function notifySelection() {
      if (sourceTarget && sourceTarget.eventId && options.onSelectEvent) options.onSelectEvent(String(sourceTarget.eventId));
    }

    function onClick(event) {
      const node = event.target.closest("[data-action], [data-register], [data-register-time], [data-provenance-step], [data-block], [data-tab], [data-event], [data-bin], [data-module-card], [data-sequencer-card], [data-level]");
      if (!node || !container.contains(node)) return;
      if (node.dataset.action === "replay-toggle") { pauseReplay(); replayOpen = !replayOpen; if (replayOpen && isProjectReplay()) useProjectClock(); times = stepTimesForView(); render(); return; }
      if (node.dataset.action === "replay-prepare") {
        if (!options.onPrepareReplay || preparingReplay) return;
        pauseReplay(); preparingReplay = true; render();
        options.onPrepareReplay(state.sequencerId, state.replayScope);
        return;
      }
      if (node.dataset.action === "replay-step") { pauseReplay(); advanceReplay(); return; }
      if (node.dataset.action === "replay-run") {
        if (replayTimer !== undefined) pauseReplay();
        else if (replayCount()) {
          if (replayPosition === replayCount() - 1) { state.time = isProjectReplay() ? projectReplayTrace.frames[0] : replayTrace.instructions[0].time; replayPosition = isProjectReplay() ? 0 : -1; }
          scheduleReplay();
        }
        render(); return;
      }
      if (node.dataset.action === "replay-source") {
        const instruction = replayTrace.instructions[replayPosition];
        if (instruction && options.onSourceClick) options.onSourceClick(instruction.event.source, instruction.event.id);
        return;
      }
      if (node.dataset.action === "project-replay-source") {
        const current = model.snapshot(index, node.dataset.sequencerId, state.time, "local", { limit: 1 });
        if (current.q1.event && options.onSourceClick) options.onSourceClick(current.q1.event.source, current.q1.event.id);
        return;
      }
      if (node.dataset.moduleCard !== undefined) { navigate("sequencers", node.dataset.moduleCard); return; }
      if (node.dataset.sequencerCard !== undefined) { navigate("detail", state.moduleId, node.dataset.sequencerCard); return; }
      if (node.dataset.level) { navigate(node.dataset.level); return; }
      if (node.dataset.registerTime !== undefined) { setTime(Number(node.dataset.registerTime)); notifySelection(); return; }
      if (node.dataset.provenanceStep !== undefined) {
        const register = snapshot.registers.find((entry) => entry.register === selectedRegister);
        const step = register && register.meta && register.meta.provenance && register.meta.provenance.steps[Number(node.dataset.provenanceStep)];
        if (step && step.source && options.onSourceClick) options.onSourceClick(step.source);
        return;
      }
      if (node.dataset.action === "zoom-out") { zoomOut(); return; }
      if (node.dataset.writerRegister) { chooseRegisterWriter(node.dataset.writerRegister, node.dataset.event); return; }
      if (node.dataset.instructionEvent) { state.selectedBlock = "q1"; chooseEvent(node.dataset.instructionEvent); return; }
      if (node.dataset.communicationEvent) { state.selectedBlock = "simulation"; chooseEvent(node.dataset.communicationEvent); return; }
      if (node.dataset.event) { chooseEvent(node.dataset.event); return; }
      if (node.dataset.bin) { selectedBin = node.dataset.bin; state.selectedBlock = "bins"; render(); notifySelection(); return; }
      if (node.dataset.register) { selectedRegister = node.dataset.register; state.selectedBlock = "registers"; render(); notifySelection(); return; }
      if (node.dataset.block || node.dataset.tab) { state.selectedBlock = node.dataset.block || (node.dataset.tab === "parameters" ? "awg" : node.dataset.tab); render(); if (node.dataset.block) notifySelection(); return; }
      if (node.dataset.action === "previous") step(-1);
      if (node.dataset.action === "next") step(1);
      if (node.dataset.action === "register-previous" || node.dataset.action === "register-next") {
        const history = model.registerHistory(index, state.sequencerId, selectedRegister, state.time, state.basis);
        const entry = node.dataset.action === "register-previous" ? history.previous : history.next;
        if (entry) { setTime(entry.time); notifySelection(); }
      }
      if (node.dataset.action === "bins-previous" || node.dataset.action === "bins-next") {
        state.binOffset = Math.max(0, state.binOffset + (node.dataset.action === "bins-next" ? PAGE_SIZE : -PAGE_SIZE));
        render();
      }
      if (node.dataset.action === "q1-event" && snapshot.q1.event) chooseEvent(snapshot.q1.event.id);
      if (node.dataset.action === "rt-event" && snapshot.rt.event) chooseEvent(snapshot.rt.event.id);
      if (node.dataset.action === "source" && sourceTarget && options.onSourceClick) options.onSourceClick(sourceTarget.source, sourceTarget.eventId);
    }

    function onChange(event) {
      const control = event.target.dataset.control;
      if (control === "module") {
        navigate("sequencers", controls.module.value);
      }
      if (control === "sequencer") { state.binOffset = 0; navigate("detail", state.moduleId, controls.sequencer.value); }
      if (control === "register-format") { state.registerFormat = controls[control].value; render(); }
      if (control === "all-registers") { state.showAllRegisters = controls[control].checked; render(); }
      if (control === "signal-stage") { state.signalStage = controls[control].value; render(); }
      if (control === "replay-scope") { pauseReplay(); state.replayScope = controls[control].value; if (isProjectReplay()) useProjectClock(); times = stepTimesForView(); updateReplayTrace(); render(); }
      if (control === "replay-speed") { state.replaySpeed = Number(controls[control].value); if (replayTimer !== undefined) { pauseReplay(); scheduleReplay(); } render(); }
      if (control === "time") setTime(Number(controls.time.value));
    }

    function onInput(event) {
      if (event.target !== controls.scrubber) return;
      if (frame !== undefined) root.cancelAnimationFrame(frame);
      frame = root.requestAnimationFrame(() => { frame = undefined; setTime(Number(controls.scrubber.value)); });
    }

    function onKeyDown(event) {
      if (event.key === "Escape" && !event.target.closest("input, select, textarea, [contenteditable='true']") && event.target.closest('[data-view="overview"], [data-view="detail"], [data-view="replay"]')) {
        event.preventDefault();
        zoomOut();
        return;
      }
      if (event.target === controls.time && event.key === "Enter") {
        event.preventDefault();
        setTime(Number(controls.time.value));
      }
      const block = event.target.closest("[data-block]");
      if (block && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); state.selectedBlock = block.dataset.block; render(); notifySelection(); }
      if (event.target.dataset.tab && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        const tabs = Array.from(container.querySelectorAll("[data-tab]"));
        const current = tabs.indexOf(event.target);
        const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
        tabs[next].click();
        tabs[next].focus();
      }
    }

    function onVisibilityChange() { if (root.document.hidden) { pauseReplay(); render(); } }

    root.document.addEventListener("visibilitychange", onVisibilityChange);
    container.addEventListener("click", onClick);
    container.addEventListener("change", onChange);
    container.addEventListener("input", onInput);
    container.addEventListener("keydown", onKeyDown);
    if (replayOpen && isProjectReplay()) useProjectClock(false);
    setSequencerOptions();
    render(false);
    return {
      selectEvent(eventId) { chooseEvent(eventId, false); },
      setTime,
      setTimeBasis(basis) {
        const nextBasis = basis === "aligned" ? "aligned" : "local";
        if (state.basis === nextBasis) return;
        pauseReplay();
        if (replayOpen && isProjectReplay() && nextBasis === "aligned") replayOpen = false;
        const alignment = index.alignment[state.sequencerId] || {};
        const offset = model.numericValue(alignment.offset);
        if (!alignmentUnknown() && offset !== undefined) state.time += nextBasis === "aligned" ? offset : -offset;
        state.basis = nextBasis;
        times = stepTimesForView();
        updateReplayTrace();
        render();
      },
      getState() { return { ...state, replayOpen }; },
      destroy() {
        destroyed = true;
        pauseReplay();
        root.document.removeEventListener("visibilitychange", onVisibilityChange);
        if (frame !== undefined) root.cancelAnimationFrame(frame);
        container.removeEventListener("click", onClick);
        container.removeEventListener("change", onChange);
        container.removeEventListener("input", onInput);
        container.removeEventListener("keydown", onKeyDown);
      },
    };
  }

  root.q1lensHardwareView = { mount };
})(typeof globalThis !== "undefined" ? globalThis : this);
