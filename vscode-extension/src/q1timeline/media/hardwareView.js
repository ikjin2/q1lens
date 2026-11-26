(function hardwareViewFactory(root) {
  const PAGE_SIZE = 16;
  // Rows of the state table, in the order a command travels through the sequencer. A row is left
  // out when the program never uses that part (acquisition) or the run cannot know it (RT queue).
  const BLOCKS = [
    { id: "q1", title: "Q1" },
    { id: "rt", title: "RT" },
    { id: "queue", title: "RT queue" },
    { id: "awg", title: "Output" },
    { id: "acquisition", title: "Acquisition" },
  ];
  // A selection is a row, or a tab of the inspector that has no row of its own.
  const SELECTIONS = new Set(["registers", "bins", "simulation", ...BLOCKS.map((block) => block.id)]);

  function rowHtml(block) {
    const core = block.id === "q1" || block.id === "rt";
    const value = core
      ? `<button type="button" class="hardware-row-value" data-action="${block.id}-event" title="Move the cursor to the start of this command"><span data-value="${block.id}-event"></span></button>`
      : `<span class="hardware-row-value" data-block-value="${block.id}"></span>`;
    const more = block.id === "q1" ? '<div class="hardware-row-more" data-value="instruction-detail"></div>'
      : block.id === "awg" ? '<div class="hardware-row-more" data-value="output-detail"></div>'
      : core ? "" : `<div class="hardware-row-more" data-block-title="${block.id}"></div>`;
    return `<div class="hardware-row" data-row="${block.id}">
      <button type="button" class="hardware-row-name" data-block="${block.id}" aria-pressed="false">${block.title}</button>
      <div class="hardware-row-main">${value}${more}</div>
      <button type="button" class="hardware-row-source" data-row-source="${block.id}" hidden></button>
    </div>`;
  }

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

  function eventLabel(event) {
    if (!event) return "No active event";
    const label = event.label || event.source && event.source.raw || event.kind;
    return event.meta?.stop_code === undefined ? label : `${label} · stop code ${display(event.meta.stop_code)}`;
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

  function binKindLabel(kind) {
    return { acquire_timetags: "Timetags", acquire_digital: "Digital level", acquire_ttl: "TTL counts" }[kind];
  }

  // The compact form drops what the trace badge already says (simulation, path execution) and shows
  // the instruction limit only once it was reached; `full` keeps every recorded field for tooltips.
  function executionLabel(execution, terminal, full = false) {
    if (!execution) return "Static analysis";
    const badged = execution.mode === "simulation" || execution.mode === "playback";
    const mode = execution.mode === "simulation" ? "Event-driven simulation" : execution.mode === "concrete" ? "Concrete execution" : execution.mode === "playback" ? "Path execution" : statusLabel(execution.mode);
    const parts = full ? [mode, statusLabel(execution.status)] : badged ? [statusLabel(execution.status)] : [mode.replace(/ execution$/, ""), statusLabel(execution.status)];
    const limit = Number.isFinite(execution.max_steps) ? ` / ${execution.max_steps}` : "";
    if (Number.isFinite(execution.steps)) parts.push(full ? `${execution.steps}${limit} instructions` : `${execution.steps}${limit && execution.steps >= execution.max_steps ? limit : ""} instr`);
    if (execution.stop_code !== undefined) parts.push(`${full ? "Stop code" : "stop"} ${display(execution.stop_code)}`);
    const reason = terminal?.display_reason ?? execution.reason;
    if (reason) parts.push(reason);
    if (Number.isFinite(terminal?.time)) parts.push(`${terminal.core === "q1" ? "Q1 observation end" : "Observation end"} ${terminal.time} ns`);
    return parts.join(" · ");
  }

  function sentence(value) {
    return value.charAt(0).toUpperCase() + value.slice(1);
  }

  function baseName(file) {
    return String(file).split(/[\\/]/).pop();
  }

  // At most four significant digits for fractions; integer digits are never rounded away.
  function compactNumber(value) {
    if (typeof value !== "number" || !Number.isFinite(value) || Number.isInteger(value)) return display(value);
    return Math.abs(value) >= 1000 ? String(Math.round(value)) : String(Number(value.toPrecision(4)));
  }

  function inputSource(source) {
    if (!source) return "Source unknown";
    const labels = { numerical_model: "Numerical model", supplied_trace: "Supplied trace", measured_input: "User-designated measured input", loopback: "Simulated loopback", scenario: "Scenario input", physical: "Physical model" };
    return `${labels[source.kind] || statusLabel(source.kind)}${source.label ? ` · ${source.label}` : ""}`;
  }

  // ---- Outputs rack: cluster, module, port, sequencer path, on one time axis ----

  const DRAWN_MODULE_TYPES = new Set(["QCM", "QRM", "QCM-RF", "QRM-RF"]);

  function portLabel(port) {
    const match = /^out(\d+)$/.exec(String(port));
    return match ? `O${Number(match[1]) + 1}` : String(port);
  }

  function timeScale(span) {
    if (span < 5e3) return { unit: "ns", scale: 1 };
    if (span < 5e6) return { unit: "µs", scale: 1e3 };
    if (span < 5e9) return { unit: "ms", scale: 1e6 };
    return { unit: "s", scale: 1e9 };
  }

  function formatTime(ns) {
    const { unit, scale } = timeScale(Math.abs(ns));
    return `${Number((ns / scale).toPrecision(6))} ${unit}`;
  }

  function tickStep(span, count) {
    const raw = span / Math.max(1, count);
    const power = 10 ** Math.floor(Math.log10(Math.max(raw, 1e-9)));
    return Math.max(1, [1, 2, 5, 10].find((multiple) => multiple * power >= raw) * power);
  }

  function listNames(names) {
    return names.length < 3 ? names.join(" and ") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  }

  function createRack(host, context) {
    const { ir, sequencers, options } = context;
    const output = ir.output || {};
    const saved = context.initial && typeof context.initial === "object" ? context.initial : {};
    const collapsed = new Set(Array.isArray(saved.collapsed) ? saved.collapsed.filter((key) => typeof key === "string") : []);
    let trial = {};
    if (saved.trial && typeof saved.trial === "object") {
      for (const [name, value] of Object.entries(saved.trial)) if (Number.isSafeInteger(value)) trial[name] = value;
    }
    let data = output.overview || null;
    let extent = data ? data.extent : { t0: 0, t1: 1 };
    let view = { t0: 0, t1: 1 };
    let live = options.onRequestOutputWindow && data ? "unknown" : "off";
    let liveIssue = "";
    let token = 0;
    let requestTimer;
    let timeoutTimer;
    let frame;
    let width = 0;
    let gutter = 0;
    let rowsKey;
    let paramsKey;
    let drag;
    let scrubbing = false;
    let destroyed = false;
    // A focused rack shows one sequencer: its two paths, on that sequencer's own clock.
    let focus = null;
    const shift = () => focus === null ? 0 : context.labelShift();

    const ports =(output.ports || []).map((port, number) => ({ ...port, number }));
    const routed = new Set(ports.flatMap((port) => Object.keys(port.routes || {})));
    const described = new Map((output.modules || []).map((module) => [String(module.name), module]));
    const tree = [];
    // A project names its cluster on the modules it routes. Modules it only mentions belong to the same rack.
    const named = [...new Set((output.modules || []).map((module) => module.cluster).filter(Boolean).map(String))];
    for (const id of new Set([...sequencers.map(moduleId), ...ports.map((port) => String(port.module))])) {
      const about = described.get(id) || {};
      const members = sequencers.filter((seq) => moduleId(seq) === id);
      const name = about.cluster ? String(about.cluster) : named.length === 1 ? named[0] : "";
      let cluster = tree.find((entry) => entry.name === name);
      if (!cluster) tree.push(cluster = { name, modules: [] });
      cluster.modules.push({
        id, members, type: String(about.type || members.find((seq) => seq.module_type)?.module_type || ""),
        lo: about.lo_frequency_hz || {}, ports: ports.filter((port) => String(port.module) === id),
        loose: members.filter((seq) => !routed.has(seq.id)),
      });
    }
    const labels = new Map(sequencers.map((seq) => [seq.id, seq.label || seq.id]));

    host.innerHTML = `
      <div class="rack-bar">
        <h3 data-value="overview-title" tabindex="-1">Outputs</h3>
        <div class="rack-params" data-rack="params"></div>
        <div class="rack-zoom">
          <span data-rack="range"></span>
          <button type="button" data-rack-action="zoom-out" aria-label="Zoom out of outputs" title="Zoom out">−</button>
          <button type="button" data-rack-action="zoom-in" aria-label="Zoom in on outputs" title="Zoom in">+</button>
          <button type="button" data-rack-action="fit" title="Show the whole output">Fit</button>
        </div>
      </div>
      <p class="rack-assumed" data-rack="assumed" role="status" hidden></p>
      <div class="rack-body" data-rack="body" tabindex="0" role="group" aria-label="Output lanes. Arrow keys pan, plus and minus zoom, Home shows everything.">
        <div class="rack-rows" data-rack="rows"></div>
        <div class="rack-cursor" data-rack="cursor" hidden></div>
      </div>
      <p class="rack-note" data-rack="note" hidden></p>`;
    const part = Object.fromEntries(Array.from(host.querySelectorAll("[data-rack]")).map((node) => [node.dataset.rack, node]));

    function setHtml(node, value) { if (node.innerHTML !== value) node.innerHTML = value; }
    function setText(node, value) { if (node.textContent !== value) node.textContent = value; }

    function fullView() {
      const span = Math.max(1, extent.t1 - extent.t0);
      return { t0: extent.t0, t1: extent.t1 + Math.max(1, Math.round(span * 0.03)) };
    }

    function clampView() {
      const full = fullView();
      const limit = full.t1 - full.t0;
      const span = Math.min(limit, Math.max(Math.min(8, limit), view.t1 - view.t0));
      const t0 = Math.min(full.t1 - span, Math.max(full.t0, view.t0));
      view = { t0, t1: t0 + span };
    }

    view = Array.isArray(saved.view) && saved.view.length === 2 && saved.view.every(Number.isFinite) && saved.view[1] > saved.view[0]
      ? { t0: saved.view[0], t1: saved.view[1] } : fullView();
    clampView();

    const x = (time) => (time - view.t0) / (view.t1 - view.t0) * width;
    const edge = (j) => data.t0 + Math.floor((data.t1 - data.t0) * j / data.bins);

    function envelope(trace, height) {
      if (!trace || !trace.min || !trace.min.length) return "";
      const y = (value) => (5 + (1 - (Math.max(-1, Math.min(1, value)) + 1) / 2) * (height - 10)).toFixed(1);
      let top = "";
      let bottom = "";
      for (let j = 0; j < trace.min.length; j++) {
        const x0 = x(edge(j));
        const x1 = x(edge(j + 1));
        if (x1 < -1 || x0 > width + 1) continue;
        const a = Math.max(-1, x0).toFixed(1);
        const b = Math.min(width + 1, x1).toFixed(1);
        top += `${top ? "L" : "M"}${a},${y(trace.max[j])}H${b}`;
        bottom = `L${b},${y(trace.min[j])}H${a}${bottom}`;
      }
      return top ? `${top}${bottom}Z` : "";
    }

    function plot(height, traces, name, clipBins) {
      const middle = (height / 2).toFixed(1);
      let marks = "";
      for (const j of clipBins || []) {
        const x0 = Math.max(0, x(edge(j)));
        const x1 = Math.min(width, x(edge(j + 1)));
        if (x1 > x0) marks += `<rect class="rack-clip" x="${x0.toFixed(1)}" y="2" width="${Math.max(1, x1 - x0).toFixed(1)}" height="${height - 4}"></rect>`;
      }
      return `<svg class="rack-plot" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(name)}">
        <line class="rack-rail" x1="0" x2="${width}" y1="5" y2="5"></line><line class="rack-rail" x1="0" x2="${width}" y1="${height - 5}" y2="${height - 5}"></line>
        <line class="rack-zero" x1="0" x2="${width}" y1="${middle}" y2="${middle}"></line>${marks}
        ${traces.map(([kind, trace]) => `<path class="rack-trace is-${kind}" d="${envelope(trace, height)}"></path>`).join("")}</svg>`;
    }

    function span(from, to) {
      const x0 = Math.max(0, x(from));
      const x1 = Math.min(width, x(to));
      return x1 - x0 < 1.5 ? null : { left: x0.toFixed(1), width: (x1 - x0).toFixed(1), size: x1 - x0 };
    }

    function notes(seq, path) {
      const entry = data.sequencers[seq];
      if (!entry || entry.issue) return "";
      const origin = entry.time_offset || 0;
      const repeats = entry.repeats.filter((repeat) => repeat.depth < 3);
      const depth = repeats.reduce((most, repeat) => Math.max(most, repeat.depth + 1), 0);
      let text = "";
      for (const repeat of repeats) {
        const place = span(repeat.t0 + origin, repeat.t1 + origin);
        if (!place) continue;
        const symbolic = repeat.count_label !== String(repeat.count);
        const period = repeat.period_label === String(repeat.period) ? formatTime(repeat.period) : `${repeat.period_label} ns`;
        const label = `× ${repeat.count_label} every ${period}${repeat.collapsed ? `, showing ${repeat.index} = ${repeat.shown}` : ""}`;
        const detail = [`Repeats ${repeat.count} times, ${formatTime(repeat.period)} apart`,
          symbolic ? `${repeat.count_label} = ${repeat.count}` : "",
          repeat.wraps_at_zero ? "A count of 0 runs 2³² times" : "",
          repeat.assumed.length ? `Drawn with assumed ${listNames(repeat.assumed)}` : ""].filter(Boolean).join("\n");
        text += `<span class="rack-repeat${repeat.assumed.length ? " is-assumed" : ""}" style="left:${place.left}px;width:${place.width}px;top:${repeat.depth * 14}px" title="${escapeHtml(detail)}">${place.size >= 40 ? escapeHtml(label) : ""}</span>`;
      }
      let shown = 0;
      for (const item of entry.intervals) {
        const place = span(item.t0 + origin, item.t1 + origin);
        if (!place || shown >= 240) continue;
        shown++;
        const label = path === "IQ" ? item.labels[0] === item.labels[1] ? item.labels[0] : `I ${item.labels[0]}, Q ${item.labels[1]}` : item.labels[Number(path === "Q")];
        const detail = [`${formatTime(item.t0 + origin - shift())} to ${formatTime(item.t1 + origin - shift())}`, label, item.nco ? `NCO ${item.nco}` : "",
          item.assumed.length ? `Drawn with assumed ${listNames(item.assumed)}` : ""].filter(Boolean).join("\n");
        text += `<button type="button" class="rack-tag${item.assumed.length ? " is-assumed" : ""}${label === "0" ? " is-idle" : ""}" data-rack-event="${escapeHtml(item.event)}" data-rack-time="${item.t0 + origin}" tabindex="${shown === 1 ? 0 : -1}" style="left:${place.left}px;width:${place.width}px;top:${depth * 14}px" title="${escapeHtml(detail)}" aria-label="${escapeHtml(detail.replaceAll("\n", ". "))}">${place.size >= 22 ? escapeHtml(label) : ""}</button>`;
      }
      return `<div class="rack-notes" style="height:${depth * 14 + 20}px">${text}</div>`;
    }

    function laneHtml(lane) {
      if (!data) return '<p class="rack-reason">No waveform memory for this sequencer. Add sequence_json to draw its output.</p>';
      const [kind, first, second] = lane.split("|");
      const until = `${formatTime(view.t0 - shift())} to ${formatTime(view.t1 - shift())}`;
      if (kind === "port") {
        const port = data.ports[Number(first)];
        if (!port) return "";
        if (port.issue) return `<p class="rack-reason">${escapeHtml(port.issue)}</p>`;
        const name = `${port.module} ${portLabel(port.port)} output, ${until}`;
        if (port.kind === "rf") return plot(60, [["i", port.traces.i], ["q", port.traces.q]], name);
        return plot(60, [["sum", port.traces.sum]], name, port.clip_bins);
      }
      const seq = kind === "path" ? second : first;
      const entry = data.sequencers[seq];
      if (!entry) return '<p class="rack-reason">No waveform memory for this sequencer. Add sequence_json to draw its output.</p>';
      if (entry.issue) return `<p class="rack-reason">${escapeHtml(entry.issue)}</p>`;
      const path = kind === "path" ? ports[Number(first)].routes[seq] : "IQ";
      const traces = path === "IQ" ? [["i", entry.traces.i], ["q", entry.traces.q]] : [[path.toLowerCase(), entry.traces[path.toLowerCase()]]];
      return plot(focus === null ? 40 : 64, traces, `${labels.get(seq) || seq} path ${path}, ${until}`) + notes(seq, path);
    }

    function focusReason() {
      const module = tree.flatMap((cluster) => cluster.modules).find((entry) => entry.members.some((seq) => seq.id === focus));
      const type = module ? module.type : "";
      if (type && !DRAWN_MODULE_TYPES.has(type.toUpperCase().replace("_", "-"))) return `Q1Lens does not draw ${type} outputs.`;
      return "No waveform memory for this sequencer. Add sequence_json to draw its output.";
    }

    function hasLanes() {
      if (!data) return false;
      return focus === null || Boolean(data.sequencers[focus] && !data.sequencers[focus].issue);
    }

    function toggle(key, name) {
      const closed = collapsed.has(key);
      return `<button type="button" class="rack-toggle" data-rack-toggle="${escapeHtml(key)}" aria-expanded="${!closed}" aria-label="${closed ? "Show" : "Hide"} ${escapeHtml(name)}"></button>`;
    }

    function sequencerCell(seq, path) {
      const label = labels.get(seq) || seq;
      const badge = path ? `<span class="rack-path is-${path.toLowerCase()}" title="${path === "IQ" ? "Both sequencer paths" : `Sequencer path ${path}`}">${path}</span>` : "";
      return `<button type="button" class="rack-seq" data-rack-inspect="${escapeHtml(seq)}" aria-label="Inspect sequencer ${escapeHtml(label)}"><i class="rack-dot" data-rack-status="${escapeHtml(seq)}" aria-hidden="true"></i><span>${escapeHtml(label)}</span></button>
        ${badge}<span class="rack-state" data-rack-state="${escapeHtml(seq)}"></span><span class="rack-uses" data-rack-uses="${escapeHtml(seq)}"></span>`;
    }

    function row(kind, depth, cell, lane, extra = "") {
      return `<div class="rack-row is-${kind}" style="--rack-depth:${depth}"><div class="rack-cell">${cell}</div><div class="rack-lane"${lane ? ` data-lane="${escapeHtml(lane)}"` : ""}>${extra}</div></div>`;
    }

    function rowsHtml() {
      if (focus !== null) {
        const entry = data && data.sequencers[focus];
        if (!entry) return `<p class="rack-reason">${escapeHtml(focusReason())}</p>`;
        const cell = `<span class="rack-path is-i" title="Sequencer path I">I</span><span class="rack-path is-q" title="Sequencer path Q">Q</span><span class="rack-uses" data-rack-uses="${escapeHtml(focus)}"></span>`;
        return row("ruler", 0, '<span class="rack-axis" data-rack-axis></span>', "ruler") + row("path", 0, cell, `seq|${focus}`);
      }
      // Without waveform memory there is nothing to place in time, so the rack is only the hierarchy.
      const rows = data ? [row("ruler", 0, '<span class="rack-axis" data-rack-axis></span>', "ruler")] : [];
      for (const cluster of tree) {
        const clusterName = cluster.name || "Cluster";
        const clusterKey = `c|${cluster.name}`;
        const moduleCount = `${cluster.modules.length} module${cluster.modules.length === 1 ? "" : "s"}`;
        rows.push(row("cluster", 0, `${toggle(clusterKey, clusterName)}<strong>${escapeHtml(clusterName)}</strong><span class="rack-sub">${moduleCount}</span>`));
        if (collapsed.has(clusterKey)) continue;
        for (const module of cluster.modules) {
          const name = moduleLabel(module.id);
          const moduleKey = `m|${module.id}`;
          let reason = "";
          const drawn = !module.type || DRAWN_MODULE_TYPES.has(module.type.toUpperCase().replace("_", "-"));
          if (!drawn) reason = `Q1Lens does not draw ${module.type} outputs. Select a sequencer to inspect its state.`;
          else if (!module.ports.length && module.id) reason = "No output routing for this module. List it under modules in q1timeline.yml to draw its ports.";
          rows.push(row("module", 1, `${toggle(moduleKey, name)}<strong>${escapeHtml(name)}</strong><span class="rack-sub">${escapeHtml(module.type)}</span>`, "", reason ? `<p class="rack-reason">${escapeHtml(reason)}</p>` : ""));
          if (collapsed.has(moduleKey)) continue;
          for (const port of module.ports) {
            const portKey = `p|${module.id}|${port.port}`;
            const routes = Object.entries(port.routes || {});
            const lo = module.lo[port.port];
            const extra = port.kind === "rf" ? `complex envelope${Number.isFinite(lo) ? `, LO ${Number((lo / 1e9).toPrecision(6))} GHz` : ""}` : port.offset ? `offset ${compactNumber(port.offset)} of full scale` : "";
            rows.push(row("port", 2, `${routes.length ? toggle(portKey, `what feeds ${name} ${portLabel(port.port)}`) : '<span class="rack-toggle-gap"></span>'}<strong>${escapeHtml(portLabel(port.port))}</strong><span class="rack-sub">${escapeHtml(port.port)}</span>${extra ? `<span class="rack-extra">${escapeHtml(extra)}</span>` : ""}<span class="rack-clips" data-rack-clips="${port.number}"></span>`, `port|${port.number}`));
            if (collapsed.has(portKey)) continue;
            for (const [seq, path] of routes) rows.push(row("path", 3, sequencerCell(seq, path), `path|${port.number}|${seq}`));
          }
          // A sequencer without a port still shows both of its paths when its output is modeled.
          for (const seq of module.loose) {
            const modeled = drawn && output.sequencers && output.sequencers[seq.id];
            rows.push(row("path", 2, sequencerCell(seq.id, modeled ? "IQ" : ""), drawn ? `seq|${seq.id}` : ""));
          }
        }
      }
      if (!sequencers.length) rows.push('<p class="hardware-empty">No sequencers are available in this analysis.</p>');
      return rows.join("");
    }

    function ruler() {
      const range = view.t1 - view.t0;
      const { unit, scale } = timeScale(range);
      const step = tickStep(range, width / 96);
      let ticks = "";
      const moved = shift();
      for (let time = Math.ceil((view.t0 - moved) / step) * step; time <= view.t1 - moved; time += step) {
        const at = x(time + moved).toFixed(1);
        // Labels at either end stay inside the lane instead of being cut in half.
        const anchor = x(time + moved) < 14 ? "start" : x(time + moved) > width - 14 ? "end" : "middle";
        ticks += `<line class="rack-tick" x1="${at}" x2="${at}" y1="15" y2="22"></line><text x="${at}" y="11" text-anchor="${anchor}">${escapeHtml(String(Number((time / scale).toPrecision(9))))}</text>`;
      }
      return { unit, svg: `<svg class="rack-ruler" width="${width}" height="22" viewBox="0 0 ${width} 22" aria-hidden="true">${ticks}</svg>` };
    }

    function parameters() {
      const list = data && data.parameters || output.parameters || [];
      const key = JSON.stringify([list, live === "off", Object.keys(trial).length > 0]);
      if (paramsKey !== key) {
        paramsKey = key;
        const focused = host.ownerDocument.activeElement?.dataset?.rackParam;
        const editable = live !== "off";
        const said = { project: "set by the project", assumed: "assumed", trial: "a value you are trying" };
        setHtml(part.params, list.map((item) => {
          const name = escapeHtml(item.name);
          const value = escapeHtml(item.value ?? "");
          const control = editable
            ? `<input type="number" step="1" data-rack-param="${name}" value="${value}" style="width:${Math.max(5, String(item.value ?? "").length + 4)}ch" aria-label="${name}, ${said[item.source]}">`
            : `<output>${value}</output>`;
          const tag = item.source === "project" ? "" : `<span class="rack-param-source">${item.source === "assumed" ? "assumed" : "trying"}</span>`;
          return `<label class="rack-param is-${item.source}" title="${name} is ${said[item.source]}${item.roles?.length ? `. Sets ${listNames(item.roles)}` : ""}"><span class="rack-param-name">${name} =</span>${control}${tag}</label>`;
        }).join("") + (Object.keys(trial).length ? '<button type="button" data-rack-action="reset-params">Use project values</button>' : ""));
        if (focused) part.params.querySelector(`[data-rack-param="${CSS.escape(focused)}"]`)?.focus();
      }
      const assumed = list.filter((item) => item.source === "assumed").map((item) => item.name);
      part.assumed.hidden = !assumed.length;
      if (assumed.length) {
        const one = assumed.length === 1;
        setText(part.assumed, `${listNames(assumed)} ${one ? "has" : "have"} no value in this project. The rack draws ${one ? "it with an assumed value" : "them with assumed values"}, marked in amber.${live !== "off" ? " Type a value to try another." : ""}`);
      }
      return new Set(assumed);
    }

    function draw() {
      frame = undefined;
      if (destroyed) return;
      const key = JSON.stringify([focus, focus !== null && hasLanes(), ...collapsed]);
      if (rowsKey !== key) { rowsKey = key; part.rows.innerHTML = rowsHtml(); }
      host.classList.toggle("is-focus", focus !== null);
      host.classList.toggle("is-bare", focus !== null && !hasLanes());
      const firstLane = part.rows.querySelector(".rack-lane");
      const measured = firstLane ? Math.floor(firstLane.clientWidth) : 0;
      width = Math.max(120, measured || 640);
      gutter = firstLane ? firstLane.offsetLeft : 0;
      const assumed = parameters();
      const drawn = ruler();
      for (const node of part.rows.querySelectorAll("[data-lane]")) {
        setHtml(node, node.dataset.lane === "ruler" ? drawn.svg : laneHtml(node.dataset.lane));
      }
      const axis = part.rows.querySelector("[data-rack-axis]");
      if (axis) setText(axis, `Time in ${drawn.unit}`);
      for (const node of part.rows.querySelectorAll("[data-rack-uses]")) {
        const entry = data && data.sequencers[node.dataset.rackUses];
        const names = new Set();
        for (const item of [...(entry?.intervals || []), ...(entry?.repeats || [])]) for (const name of item.assumed) if (assumed.has(name)) names.add(name);
        setText(node, names.size ? `assumes ${[...names].sort().join(", ")}` : "");
      }
      for (const node of part.rows.querySelectorAll("[data-rack-clips]")) {
        const clips = data && data.ports[Number(node.dataset.rackClips)]?.clip_bins?.length;
        setText(node, clips ? data.exact ? "clips at full scale" : "can reach full scale" : "");
      }
      setText(part.range, `${formatTime(view.t0 - shift())} to ${formatTime(view.t1 - shift())}`);
      const remarks = [];
      if (data && !data.exact) remarks.push("This span holds more samples than fit on screen, so each column shows the range the output covers there, before any digital filter. Zoom in to see every sample.");
      if (liveIssue) remarks.push(liveIssue);
      part.note.hidden = !remarks.length;
      setText(part.note, remarks.join(" "));
      host.querySelector(".rack-zoom").hidden = !data;
      renderCursor();
    }

    function schedule() {
      if (frame === undefined && !destroyed) frame = root.requestAnimationFrame(draw);
    }

    function renderCursor() {
      const time = context.axisTime();
      const at = x(time);
      const visible = data && Number.isFinite(at) && at >= 0 && at <= width;
      part.cursor.hidden = !visible;
      if (visible) part.cursor.style.left = `${(gutter + at).toFixed(1)}px`;
      for (const node of part.rows.querySelectorAll("[data-rack-status]")) {
        const status = context.status(node.dataset.rackStatus);
        node.classList.toggle("is-active", status.active);
        node.parentElement.title = status.title;
      }
      for (const node of part.rows.querySelectorAll("[data-rack-state]")) {
        const status = context.status(node.dataset.rackState);
        setText(node, status.state);
        node.classList.toggle("is-error", status.error);
      }
    }

    function covered() {
      // The embedded overview already holds every sample of a short output.
      return data && data.exact && data.bins === data.t1 - data.t0 && view.t0 >= data.t0 && view.t1 <= data.t1 + 1;
    }

    function request(force) {
      if (live === "off" || !options.onRequestOutputWindow || !data) return;
      if (!force && !Object.keys(trial).length && covered()) return;
      root.clearTimeout(requestTimer);
      requestTimer = root.setTimeout(() => {
        const t0 = Math.floor(view.t0);
        const mine = ++token;
        options.onRequestOutputWindow({ token: mine, t0, t1: Math.max(t0 + 1, Math.ceil(view.t1)), bins: Math.max(64, Math.min(1600, Math.round(width))), params: { ...trial } });
        root.clearTimeout(timeoutTimer);
        timeoutTimer = root.setTimeout(() => {
          if (mine !== token || live === "on") return;
          live = "off";
          liveIssue = "Live sampling did not answer. The rack shows the overview stored with this analysis.";
          schedule();
        }, 8000);
      }, force ? 0 : 90);
    }

    function receive(message) {
      if (destroyed || !message || message.token !== token) return;
      root.clearTimeout(timeoutTimer);
      if (message.error || !message.window) {
        liveIssue = message.error || "Live sampling is unavailable.";
        if (live === "unknown") live = "off";
      } else {
        // A view that showed the whole output keeps showing all of it when a value changes its length.
        const full = fullView();
        const whole = view.t0 <= full.t0 && view.t1 >= full.t1;
        const grown = message.window.extent.t0 !== extent.t0 || message.window.extent.t1 !== extent.t1;
        live = "on";
        liveIssue = "";
        data = message.window;
        extent = data.extent;
        if (whole && grown) { view = fullView(); request(true); }
        clampView();
        context.received();
      }
      schedule();
    }

    function changeView(next) {
      view = next;
      clampView();
      schedule();
      request(false);
      context.changed();
    }

    function zoom(factor, anchor = (view.t0 + view.t1) / 2) {
      changeView({ t0: anchor - (anchor - view.t0) * factor, t1: anchor + (view.t1 - anchor) * factor });
    }

    function pan(fraction) {
      const shift = (view.t1 - view.t0) * fraction;
      changeView({ t0: view.t0 + shift, t1: view.t1 + shift });
    }

    function timeAt(event) {
      const lane = part.rows.querySelector(".rack-lane");
      if (!lane) return undefined;
      const offset = event.clientX - lane.getBoundingClientRect().left;
      return view.t0 + Math.max(0, Math.min(width, offset)) / width * (view.t1 - view.t0);
    }

    function onClick(event) {
      const node = event.target.closest("[data-rack-toggle], [data-rack-inspect], [data-rack-action], [data-rack-event], .rack-lane");
      if (!node || !host.contains(node)) return;
      if (node.dataset.rackToggle !== undefined) {
        const key = node.dataset.rackToggle;
        if (collapsed.has(key)) collapsed.delete(key); else collapsed.add(key);
        draw();
        part.rows.querySelector(`[data-rack-toggle="${CSS.escape(key)}"]`)?.focus();
        context.changed();
        return;
      }
      if (node.dataset.rackInspect !== undefined) { context.inspect(node.dataset.rackInspect); return; }
      if (node.dataset.rackEvent !== undefined) { context.selectEvent(node.dataset.rackEvent, Number(node.dataset.rackTime)); return; }
      if (node.dataset.rackAction === "zoom-in") { zoom(0.5); return; }
      if (node.dataset.rackAction === "zoom-out") { zoom(2); return; }
      if (node.dataset.rackAction === "fit") { changeView(fullView()); return; }
      if (node.dataset.rackAction === "reset-params") { trial = {}; context.changed(); request(true); schedule(); return; }
      if (node.classList.contains("rack-lane") && data && !drag?.moved) {
        const time = timeAt(event);
        if (time !== undefined) context.setAxisTime(Math.round(time));
      }
    }

    function onChange(event) {
      const name = event.target.dataset?.rackParam;
      if (name === undefined) return;
      const value = Number(event.target.value);
      if (event.target.value === "" || !Number.isSafeInteger(value)) delete trial[name];
      else trial[name] = value;
      context.changed();
      request(true);
    }

    function onWheel(event) {
      if (!data || !event.target.closest(".rack-rows")) return;
      if (event.ctrlKey || event.metaKey) {
        event.preventDefault();
        zoom(event.deltaY > 0 ? 1.25 : 0.8, timeAt(event));
      } else if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
        event.preventDefault();
        pan((event.deltaX || event.deltaY) / Math.max(1, width));
      }
    }

    function onKeyDown(event) {
      // One tab stop per lane; the arrow keys walk its intervals.
      if (event.target.classList?.contains("rack-tag") && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
        let next = event.target;
        do next = event.key === "ArrowRight" ? next.nextElementSibling : next.previousElementSibling;
        while (next && !next.classList.contains("rack-tag"));
        if (next) { event.preventDefault(); next.focus(); }
        return;
      }
      if (event.target !== part.body || !data) return;
      const actions = { ArrowLeft: () => pan(-0.1), ArrowRight: () => pan(0.1), "+": () => zoom(0.5), "=": () => zoom(0.5), "-": () => zoom(2), Home: () => changeView(fullView()) };
      if (!actions[event.key]) return;
      event.preventDefault();
      actions[event.key]();
    }

    function scrub(event) {
      const time = timeAt(event);
      if (time !== undefined) context.setAxisTime(Math.round(time));
    }

    // Dragging the ruler pans; pressing or dragging on a lane places the time cursor.
    function onPointerDown(event) {
      if (!data || event.button !== 0) return;
      if (event.target.closest('[data-lane="ruler"]')) {
        drag = { x: event.clientX, t0: view.t0, t1: view.t1, moved: false };
      } else if (event.target.closest(".rack-lane[data-lane]") && !event.target.closest(".rack-tag")) {
        scrubbing = true;
        scrub(event);
      } else return;
      part.rows.setPointerCapture?.(event.pointerId);
    }

    function onPointerMove(event) {
      if (scrubbing) { scrub(event); return; }
      if (!drag) return;
      const shift = (drag.x - event.clientX) / Math.max(1, width) * (drag.t1 - drag.t0);
      if (Math.abs(event.clientX - drag.x) > 2) drag.moved = true;
      if (drag.moved) changeView({ t0: drag.t0 + shift, t1: drag.t1 + shift });
    }

    function onPointerUp() {
      scrubbing = false;
      if (drag) root.setTimeout(() => { drag = undefined; }, 0);
    }

    host.addEventListener("click", onClick);
    host.addEventListener("change", onChange);
    host.addEventListener("wheel", onWheel, { passive: false });
    host.addEventListener("keydown", onKeyDown);
    part.rows.addEventListener("pointerdown", onPointerDown);
    part.rows.addEventListener("pointermove", onPointerMove);
    part.rows.addEventListener("pointerup", onPointerUp);
    part.rows.addEventListener("pointercancel", onPointerUp);
    const observer = typeof root.ResizeObserver === "function" ? new root.ResizeObserver(() => { if (!host.hidden) schedule(); }) : undefined;
    observer?.observe(part.body);
    if (Object.keys(trial).length) request(true);

    return {
      title: host.querySelector('[data-value="overview-title"]'),
      render() { if (rowsKey === undefined || !width) draw(); else renderCursor(); },
      redraw: draw,
      receive,
      hasLanes,
      // The rack narrows to one sequencer, or widens back to every output with `null`.
      focus(id) {
        const next = id === undefined || id === null ? null : String(id);
        if (focus === next) return;
        focus = next;
        draw();
      },
      // What one sequencer puts out at a time on the rack axis: the drawn interval and each path's range.
      at(seq, time) {
        const entry = data && data.sequencers[seq];
        if (!entry || entry.issue || !Number.isFinite(time)) return null;
        const local = time - (entry.time_offset || 0);
        const column = Math.floor((time - data.t0) * data.bins / Math.max(1, data.t1 - data.t0));
        const range = (trace) => trace && trace.min && column >= 0 && column < trace.min.length ? [trace.min[column], trace.max[column]] : null;
        return {
          interval: entry.intervals.find((item) => item.t0 <= local && local < item.t1) || null,
          i: range(entry.traces.i), q: range(entry.traces.q), ended: Number.isFinite(entry.end) && local >= entry.end,
        };
      },
      reveal(id) {
        const cluster = tree.find((entry) => entry.modules.some((module) => module.id === id));
        if (!cluster) return;
        collapsed.delete(`c|${cluster.name}`);
        collapsed.delete(`m|${id}`);
        draw();
        part.rows.querySelector(`[data-rack-toggle="${CSS.escape(`m|${id}`)}"]`)?.scrollIntoView({ block: "nearest" });
      },
      state() { return { view: [view.t0, view.t1], collapsed: [...collapsed], trial: { ...trial } }; },
      destroy() {
        destroyed = true;
        root.clearTimeout(requestTimer);
        root.clearTimeout(timeoutTimer);
        if (frame !== undefined) root.cancelAnimationFrame(frame);
        observer?.disconnect();
        host.removeEventListener("click", onClick);
        host.removeEventListener("change", onChange);
        host.removeEventListener("wheel", onWheel);
        host.removeEventListener("keydown", onKeyDown);
      },
    };
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
    const timeStep = Object.values(index.simulation).some((run) => run.clock_tick_ns === 1 / 128) ? 1 / 128 : 1;
    const snapTime = (time) => Math.round(time / timeStep) * timeStep;
    const sequencers = index.sequencers;
    const modules = [...new Set(sequencers.map(moduleId))];
    const ranges = {};
    for (const basis of ["local", "aligned"]) {
      const steps = sequencers.map((seq) => model.stepTimes(index, seq.id, basis)).filter((times) => times.length);
      ranges[basis] = steps.length
        ? { min: Math.min(...steps.map((times) => times[0])), max: Math.max(...steps.map((times) => times[times.length - 1])) }
        : { min: 0, max: 0 };
      if (options.observation?.basis === basis && Number.isFinite(options.observation.time)) {
        ranges[basis].min = Math.min(ranges[basis].min, options.observation.time);
        ranges[basis].max = Math.max(ranges[basis].max, options.observation.time);
      }
    }
    const events = index.eventsById;
    const initial = options.initialState || {};
    const state = {
      sequencerId: sequencers.some((seq) => seq.id === initial.sequencerId) ? initial.sequencerId : sequencers[0] && sequencers[0].id,
      time: Number.isFinite(initial.time) ? snapTime(initial.time) : 0,
      basis: initial.basis === "aligned" ? "aligned" : "local",
      selectedBlock: SELECTIONS.has(initial.selectedBlock) ? initial.selectedBlock : "registers",
      signalStage: initial.signalStage || "dac",
      registerFormat: initial.registerFormat === "hex" ? "hex" : "decimal",
      showAllRegisters: initial.showAllRegisters === true,
      replaySpeed: [100, 450, 900].includes(initial.replaySpeed) ? initial.replaySpeed : 450,
      replayScope: sequencers.length > 1 && initial.replayScope !== "sequencer" ? "project" : "sequencer",
      binOffset: Number.isInteger(initial.binOffset) && initial.binOffset >= 0 ? initial.binOffset : 0,
      level: initial.level === "detail" ? "detail" : "modules",
      moduleId: modules.includes(initial.moduleId) ? initial.moduleId : undefined,
    };
    const initialSequencer = sequencers.find((seq) => seq.id === state.sequencerId);
    if (state.moduleId === undefined || state.level === "detail") state.moduleId = initialSequencer ? moduleId(initialSequencer) : "";
    if (!initialSequencer) state.level = "modules";
    let snapshot;
    let selectedRegister = /^R([0-9]|[1-5][0-9]|6[0-3])$/.test(initial.selectedRegister) ? initial.selectedRegister : "R0";
    let selectedBin = typeof initial.selectedBin === "string" ? initial.selectedBin : undefined;
    let frame;
    let destroyed = false;
    let sourceTarget;
    let times = [];
    let rack;
    let rackSummaries = new Map();
    let rowEvents = {};
    let signalChartKey;
    let replayOpen = initial.replayOpen === true && state.level === "detail";
    let preparingReplay = false;
    let replayTimer;
    let replayTrace;
    let replayPosition = -1;
    let replayRegisterNames = [];
    const replaySourceTraces = new WeakMap();
    const projectReplayTrace = model.projectExecutionTrace(index);
    const sharedOffset = model.numericValue(index.alignment[sequencers[0]?.id]?.offset);
    const hasSharedAlignment = Number.isFinite(sharedOffset) && sequencers.every(({ id }) => {
      const alignment = index.alignment[id];
      return alignment?.clock === "shared_simulation" && alignment.confidence === "exact" &&
        !alignment.unaligned && model.numericValue(alignment.offset) === sharedOffset;
    });
    const alignedProjectFrames = hasSharedAlignment ? projectReplayTrace.frames.map((time) => time + sharedOffset) : [];
    let projectReplayTracks;
    let replayEnd = 0;
    const overviewSteps = new Map();
    container.classList.add("q1-hardware");
    container.innerHTML = `
      <div class="hardware-heading">
        <div class="hardware-title">Hardware <span class="hardware-badge" data-value="trace-kind">Analyzed</span></div>
        <nav class="hardware-breadcrumb" aria-label="Hardware hierarchy">
          <button type="button" data-level="modules">Outputs</button>
          <span data-crumb="module" aria-hidden="true">/</span><button type="button" data-crumb-module></button><span class="hardware-crumb-switch" data-switch="module"><select data-control="module" aria-label="Hardware module" title="Switch module"></select></span>
          <span data-crumb="sequencer" aria-hidden="true">/</span><button type="button" data-level="detail"></button><span class="hardware-crumb-switch" data-switch="sequencer"><select data-control="sequencer" aria-label="Hardware sequencer" title="Switch sequencer"></select></span>
        </nav>
        <button type="button" data-action="replay-toggle" data-view="detail" aria-expanded="false">Execution replay</button>
      </div>
      <div class="hardware-controls">
        <button type="button" data-action="previous" title="Previous state change" aria-label="Previous state change">‹</button>
        <button type="button" data-action="next" title="Next state change" aria-label="Next state change">›</button>
        <label class="hardware-time">Time <input data-control="time" type="number" step="any" aria-label="Selected time in nanoseconds"> ns</label>
        <input data-control="scrubber" type="range" step="${timeStep}" aria-label="Explore sequencer state over time">
        <span data-value="basis" class="hardware-muted"></span>
      </div>
      <div class="hardware-warnings" data-value="warnings" role="status" hidden></div>
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
      <section class="hardware-rack" data-view="overview" aria-label="Outputs"></section>
      <div class="hardware-body" data-view="detail">
        <div class="hardware-state" tabindex="-1" role="group" aria-label="Sequencer state at the selected time">
          <div class="hardware-rows">${BLOCKS.map(rowHtml).join("")}</div>
          <p class="hardware-absent" data-value="absent" hidden></p>
          <p class="hardware-execution" data-value="execution" role="status"></p>
          <p class="hardware-footnote" data-value="timing-note" hidden></p>
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
            <div class="hardware-register-pager" data-view="register-pager"><button type="button" data-action="register-previous">‹ Previous change</button><button type="button" data-action="register-next">Next change ›</button><span data-value="register-history-count"></span></div>
            <div class="hardware-register-history" data-value="register-history"></div>
          </div>
          <div class="hardware-tab-panel" data-panel="parameters" role="tabpanel" aria-label="Parameters" hidden>
            <div class="hardware-table-scroll"><table class="hardware-table" data-table="parameters"><thead><tr><th data-head="parameter">Parameter</th><th title="Q1 settings waiting to apply.">Pending</th><th title="Parameters applied by RT. A pending setting becomes applied when its captured RT packet executes.">Applied</th></tr></thead><tbody data-value="parameters"></tbody></table></div>
            <p class="hardware-panel-hint" data-value="queue-note" hidden></p>
          </div>
          <div class="hardware-tab-panel" data-panel="bins" role="tabpanel" aria-label="Acquisition bins" hidden>
            <div class="hardware-panel-toolbar"><span data-value="bin-count"></span><div class="hardware-bin-pager"><button type="button" data-action="bins-previous" aria-label="Previous bins">‹</button><button type="button" data-action="bins-next" aria-label="Next bins">›</button></div></div>
            <p class="hardware-panel-hint" data-value="bins-note" hidden></p>
            <div class="hardware-table-scroll"><table class="hardware-table"><thead><tr><th>Acquisition</th><th>Bin</th><th data-head="targets">Targets</th><th>Last target or write</th><th data-head="result">Latest result <span class="hardware-unit" data-value="bins-units"></span></th></tr></thead><tbody data-value="bins"></tbody></table></div>
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
    const headNodes = Object.fromEntries(Array.from(container.querySelectorAll("[data-head]")).map((node) => [node.dataset.head, node]));

    const find = (selector) => container.querySelector(selector);
    const controls = Object.fromEntries(["module", "sequencer", "time", "scrubber", "register-format", "all-registers", "signal-stage", "replay-speed", "replay-scope"].map((name) => [name, find(`[data-control="${name}"]`)]));
    const values = Object.fromEntries(Array.from(container.querySelectorAll("[data-value]")).map((node) => [node.dataset.value, node]));
    const registerNodes = Array.from(container.querySelectorAll("[data-register]"));
    const rowNodes = Object.fromEntries(BLOCKS.map((block) => [block.id, find(`[data-row="${block.id}"]`)]));
    const blockNodes = Object.fromEntries(BLOCKS.map((block) => [block.id, find(`[data-block="${block.id}"]`)]));
    const blockValues = Object.fromEntries(BLOCKS.map((block) => [block.id, find(`[data-block-value="${block.id}"]`)]));
    const blockTitles = Object.fromEntries(BLOCKS.map((block) => [block.id, find(`[data-block-title="${block.id}"]`)]));
    const rowSources = Object.fromEntries(BLOCKS.map((block) => [block.id, find(`[data-row-source="${block.id}"]`)]));

    rack = createRack(find('[data-view="overview"]'), {
      ir, sequencers, options, initial: initial.rack,
      axisTime,
      setAxisTime(time) { setTime(basis() === "aligned" ? time : time - rackOffset()); },
      // A focused lane is labeled in the time the cursor field shows.
      labelShift() { return basis() === "aligned" ? 0 : rackOffset(); },
      inspect(sequencerId) {
        const sequencer = sequencers.find((seq) => seq.id === sequencerId);
        if (sequencer) navigate("detail", moduleId(sequencer), sequencer.id);
      },
      // An interval in the rack selects its instruction and stays in the rack.
      selectEvent(eventId, axisTime) {
        const event = events.get(String(eventId));
        const sequencer = event && sequencers.find((seq) => seq.id === String(event.sequencer_id));
        if (!sequencer) return;
        state.sequencerId = sequencer.id;
        state.moduleId = moduleId(sequencer);
        setSequencerOptions();
        if (Number.isFinite(axisTime)) setTime(basis() === "aligned" ? axisTime : axisTime - rackOffset());
        else render();
        if (options.onSelectEvent) options.onSelectEvent(String(event.id));
      },
      status(sequencerId) {
        const summary = rackSummaries.get(sequencerId);
        if (!summary) return { active: false, title: "", state: "", error: false };
        const command = summary.rt.event || summary.q1.event;
        const execution = index.execution[sequencerId];
        // A run that did not complete is worth reading without opening the sequencer.
        const unfinished = execution && execution.status && execution.status !== "completed";
        return {
          active: summary.q1.status === "active" || summary.rt.status === "active",
          title: `Q1 ${statusLabel(summary.q1.status)}, RT ${statusLabel(summary.rt.status)}${command ? `, ${eventLabel(command)}` : ""}\n${sentence(executionLabel(execution, summary.terminal, true))}`,
          state: unfinished ? statusLabel(execution.status) : "",
          error: Boolean(unfinished && execution.status === "error"),
        };
      },
      changed() { notify(); },
      // Newly sampled output changes what the output row reads at the cursor.
      received() {
        if (state.level === "detail" && snapshot && !replayOpen) renderOutput(snapshot.awg.filter((entry) => entry.event));
      },
    });

    function text(node, value) {
      const next = String(value);
      if (node.textContent !== next) node.textContent = next;
    }

    function html(node, value) {
      if (node.innerHTML !== value) node.innerHTML = value;
    }

    function notify() {
      if (options.onStateChange) options.onStateChange({ ...state, selectedRegister, selectedBin, replayOpen, rack: rack?.state() });
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

    function projectFrames() {
      return state.basis === "aligned" && hasSharedAlignment ? alignedProjectFrames : projectReplayTrace.frames;
    }

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
      const frames = projectFrames();
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
      replayTrace = model.executionTrace(index, state.sequencerId, basis());
      replayEnd = -Infinity;
      for (const instruction of replayTrace.instructions) {
        replayEnd = Math.max(replayEnd, instruction.end);
        for (const write of instruction.writes) replayEnd = Math.max(replayEnd, write.time);
      }
      replayRegisterNames = registerNames(replayTrace);
      syncReplayPosition();
    }

    function useProjectClock(notifyScope = true) {
      if (state.basis === "aligned" && !hasSharedAlignment) {
        const offset = model.numericValue((index.alignment[state.sequencerId] || {}).offset);
        if (!alignmentUnknown() && offset !== undefined) state.time -= offset;
        state.basis = "local";
      }
      if (!hasSharedAlignment && notifyScope && options.onReplayScopeChange) options.onReplayScopeChange("project");
      times = stepTimesForView();
      updateReplayTrace();
    }

    function advanceReplay() {
      if (replayPosition >= replayCount() - 1) return;
      replayPosition++;
      const instruction = isProjectReplay() ? undefined : replayTrace.instructions[replayPosition];
      state.time = isProjectReplay() ? projectFrames()[replayPosition] : replayPosition === replayCount() - 1 ? replayEnd : instruction.end;
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
      renderReplayTrack(find('[data-replay-single]'), "replay", replayTrace, replayRegisterNames, snapshot, replayPosition, previousTime, basis());
      const execution = snapshot.execution;
      text(values["replay-status"], finished ? execution && execution.status !== "completed" ? `Recorded trace ended · ${executionLabel(execution, snapshot.terminal)}` : "Execution replay complete" : instruction ? `Instruction ${replayPosition + 1} · ${instruction.event.source && instruction.event.source.raw || eventLabel(instruction.event)}` : "Ready to replay");
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
          text(node(core), `${core.toUpperCase()}: ${statusLabel(current[core].status)} · ${event && event.source && event.source.raw || eventLabel(event)}`);
        }
      } else text(node("cores"), `Q1: ${statusLabel(current.q1.status)} · RT: ${statusLabel(current.rt.status)} · ${eventLabel(current.rt.event)}`);
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
      const previousTime = projectFrames()[Math.max(0, replayPosition - 1)];
      for (const track of projectReplayTracks) {
        const trace = state.basis === "aligned" ? model.executionTrace(index, track.sequencerId, "aligned") : track.trace;
        const current = model.snapshot(index, track.sequencerId, state.time, state.basis, { limit: 1 });
        const position = instructionPosition(trace);
        text(track.element.querySelector('[data-value="project-progress"]'), `${position + 1} / ${trace.instructions.length} instructions`);
        renderReplayTrack(track.element, "project", trace, track.names, current, position, previousTime, state.basis);
        track.element.querySelector('[data-action="project-replay-source"]').disabled = !(current.q1.event && current.q1.event.source && options.onSourceClick);
        const ended = Number.isFinite(current.terminal?.time) && current.time >= current.terminal.time;
        const projectExecution = track.element.querySelector('[data-value="project-execution"]');
        text(projectExecution, finished || ended ? sentence(executionLabel(current.execution, current.terminal)) : "");
        projectExecution.title = finished || ended ? executionLabel(current.execution, current.terminal, true) : "";
      }
      text(values["replay-status"], finished ? projectReplayTrace.traces.every(({ sequencerId }) => index.execution[sequencerId].status === "completed") ? "All sequencers finished" : "Recorded trace ended · inspect each sequencer's stop reason." : `Shared ${state.basis === "aligned" ? "RT" : "simulation"} time · ${state.time} ns`);
    }

    function stepTimesForView() {
      if (replayOpen && isProjectReplay()) return projectFrames();
      if (state.level === "detail") return model.stepTimes(index, state.sequencerId, basis());
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
      // The rack stays on screen for one sequencer, narrowed to that sequencer's lane.
      find('[data-view="overview"]').hidden = detail && replayOpen;
      rack.focus(detail ? state.sequencerId : null);
      // A switcher appears beside its crumb only when there is a sibling to switch to.
      find('[data-switch="module"]').hidden = state.level === "modules" || controls.module.options.length < 2;
      find('[data-switch="sequencer"]').hidden = !detail || controls.sequencer.options.length < 2;
      const moduleCrumb = find("[data-crumb-module]");
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
      rackSummaries = new Map(sequencers.map((seq) => [seq.id, model.summarize(index, seq.id, state.time, state.basis)]));
      rack.render();
      return [...new Set([...rackSummaries.values()].flatMap((summary) => summary.warnings || []))];
    }

    function rackOffset() {
      return Number((ir.output?.time_offsets || {})[state.sequencerId]) || 0;
    }

    function axisTime() {
      return basis() === "aligned" ? state.time : state.time + rackOffset();
    }

    // A sequencer whose start relative to the others is unknown has no place on the shared clock.
    // Its own view reads its own clock instead of reporting every state as unknown.
    function ownClock() {
      return state.level === "detail" && state.basis === "aligned" && alignmentUnknown();
    }

    function basis() {
      return ownClock() ? "local" : state.basis;
    }

    // The basis a sequencer's own view will use, before that view is open.
    function detailBasis(sequencerId) {
      return state.basis === "aligned" && alignmentUnknown(String(sequencerId)) ? "local" : state.basis;
    }

    function navigate(level, module, sequencer) {
      pauseReplay();
      if (level !== "detail" || sequencer !== undefined && sequencer !== state.sequencerId) replayOpen = false;
      if (sequencer !== undefined && sequencer !== state.sequencerId) state.binOffset = 0;
      if (module !== undefined) state.moduleId = module;
      if (sequencer !== undefined) state.sequencerId = sequencer;
      state.level = level;
      setSequencerOptions();
      render();
      const focusTarget = level === "detail" ? replayOpen ? find('[data-action="replay-toggle"]') : find(".hardware-state") : rack.title;
      focusTarget.focus({ preventScroll: true });
    }

    function zoomOut() {
      if (state.level === "modules") return;
      navigate("modules");
    }

    // A tab is offered only when the program has something to put in it.
    function activeTab() {
      if (["acquisition", "bins"].includes(state.selectedBlock)) return snapshot.program.acquires ? "bins" : "registers";
      if (["simulation", "queue"].includes(state.selectedBlock)) return snapshot.simulation ? "simulation" : "registers";
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

    function registerDetail(name, register, shortSource = false) {
      if (!register) return `${name} = unknown · no register write at this time`;
      const value = registerValue(register);
      const parts = [`${name} = ${value}`];
      const meta = register.meta || {};
      if (register.confidence) parts.push(statusLabel(register.confidence));
      const reason = meta.reason || register.value && typeof register.value === "object" && (register.value.reason || register.value.source || register.value.expr);
      if (reason && !value.includes(String(reason))) parts.push(String(reason));
      if (register.time !== undefined) parts.push(`${hasRegisterWrite(register) && !meta.invalidated ? "readable at" : "state unknown since"} ${display(register.time)} ns (${basis()})`);
      else parts.push("no register write at this time");
      if (Array.isArray(meta.branch_assumptions) && meta.branch_assumptions.length) parts.push(`assumptions: ${meta.branch_assumptions.map((entry) => `${entry.branch_id}: ${entry.assumed_branch_path}`).join(", ")}`);
      if (register.source && register.source.file) parts.push(`${shortSource ? baseName(register.source.file) : register.source.file}:${register.source.line || 1}`);
      if (register.changed) parts.push("changed now");
      return parts.join(" · ");
    }

    function registerInput(name, value, writerId) {
      const label = `${name} = ${registerValue({ value, known: model.numericValue(value) !== undefined })}`;
      const event = events.get(String(writerId));
      const writer = event && model.registerWrite(index, event.sequencer_id, name, writerId, basis());
      return writer ? `<button type="button" data-event="${escapeHtml(writerId)}" data-writer-register="${escapeHtml(name)}" title="Inspect ${escapeHtml(name)} when readable at ${writer.time} ns (${basis()}) and its source instruction">${escapeHtml(label)} ↗</button>` : `<span>${escapeHtml(label)}</span>`;
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
      const trace = steps.length ? `<details><summary>Calculation steps${start ? ` (last 8 of ${steps.length})` : ` (${steps.length})`}</summary><ol>${steps.slice(start).map((step, offset) => `<li><code>${escapeHtml(step.expression)} = ${escapeHtml(valueText(step.value))}</code>${step.source && step.source.file ? ` <button type="button" data-provenance-step="${start + offset}" title="${escapeHtml(step.source.file)}:${escapeHtml(step.source.line || 1)}"${options.onSourceClick ? "" : " disabled"}>${escapeHtml(baseName(step.source.file))}:${escapeHtml(step.source.line || 1)}</button>` : ""}</li>`).join("")}</ol></details>` : "";
      const inputEvent = provenance.input_event_id ? `<p><button type="button" data-event="${escapeHtml(provenance.input_event_id)}" data-communication-event="${escapeHtml(provenance.input_event_id)}">Show received input that supplied this value</button></p>` : "";
      const history = model.registerHistory(index, state.sequencerId, selectedRegister, state.time, basis());
      // A register that is never written has nothing to page through: one line says so.
      const unwritten = history.total === 0 && (!register || !hasRegisterWrite(register) && register.time === undefined);
      find('[data-view="register-pager"]').hidden = unwritten;
      values["register-history"].hidden = unwritten;
      if (unwritten) {
        html(values["register-detail"], `<p>${escapeHtml(selectedRegister)} ? · no writes</p>`);
        return register;
      }
      html(values["register-detail"], `${change}<p title="${escapeHtml(registerDetail(selectedRegister, register))}">${escapeHtml(registerDetail(selectedRegister, register, true))}</p>${expression}${operands}${inputEvent}${trace}`);
      find('[data-action="register-previous"]').disabled = !history.previous;
      find('[data-action="register-next"]').disabled = !history.next;
      text(values["register-history-count"], `${selectedRegister} · ${history.count} / ${history.total} changes`);
      html(values["register-history"], history.entries.length ? `<span>Recent changes${history.count > history.entries.length ? ` · latest ${history.entries.length}` : ""}</span><div>${history.entries.map((entry) => `<button type="button" data-register-time="${entry.time}" title="${escapeHtml(registerDetail(selectedRegister, entry))}"><span>${entry.time} ns</span><strong>${escapeHtml(registerValue(entry))}</strong><span>${escapeHtml(statusLabel(entry.confidence || (entry.known ? "exact" : "unknown")))}</span></button>`).join("")}</div>` : '<span>No register changes at this cursor.</span>');
      return register;
    }

    function renderInstruction() {
      const event = snapshot.q1.event;
      values["instruction-detail"].hidden = !event;
      if (!event) { text(values["instruction-detail"], ""); return; }
      const meta = event.meta || {};
      const reads = meta.register_reads;
      const parts = [];
      if (Number.isInteger(meta.execution_index)) parts.push(`<strong>Instruction ${meta.execution_index + 1}</strong>`);
      if (reads && Object.keys(reads).length) parts.push(`<span>Read at issue: ${Object.entries(reads).map(([name, value]) => {
        const writer = model.registerAt(index, event.sequencer_id, name, model.eventTime(index, event, basis()), basis());
        return registerInput(name, value, writer && writer.eventId);
      }).join(" · ")}</span>`);
      if (meta.input_event_ids && meta.input_event_ids.length) parts.push(`<span>Received input: ${meta.input_event_ids.map((id) => `<button type="button" data-event="${escapeHtml(id)}">${escapeHtml(id)}</button>`).join(" ")}</span>`);
      const clipped = Number.isFinite(meta.observation_cutoff_ns) && Number.isFinite(meta.planned_q1_end_ns) &&
        meta.planned_q1_end_ns > meta.observation_cutoff_ns && model.eventTime(index, event, "local", "t1") === meta.observation_cutoff_ns;
      if (clipped || Object.prototype.hasOwnProperty.call(meta, "flags_before") || Object.prototype.hasOwnProperty.call(meta, "flags_after")) {
        const flags = (value) => value ? ["zf", "nf", "cf", "of"].map((name) => `${name.toUpperCase()}=${value[name] === undefined ? "?" : Number(value[name])}`).join(" ") : "unknown";
        const after = clipped ? "unobserved" : flags(meta.flags_after);
        // "unknown → unknown" carries no information; a known side or an observation cutoff does.
        if (clipped || meta.flags_before || meta.flags_after) parts.push(`<span>Flags before → after: ${escapeHtml(flags(meta.flags_before))} → ${escapeHtml(after)}</span>`);
      }
      // An instruction with no recorded reads or flags has nothing to add under its row.
      html(values["instruction-detail"], parts.join(""));
      values["instruction-detail"].hidden = !parts.length;
    }

    function renderParameters() {
      // Rows of dashes say nothing; the table appears once any setting is pending or applied.
      const anySetting = snapshot.parameters.some((parameter) => parameter.pending || parameter.applied);
      find('[data-table="parameters"]').classList.toggle("is-empty", !anySetting);
      html(values.parameters, anySetting ? snapshot.parameters.map((parameter) => {
        const cell = (entry, applied) => {
          if (!entry) return '<span class="hardware-muted">—</span>';
          const origin = entry.issueEventId ? ` data-instruction-event="${escapeHtml(entry.issueEventId)}"` : "";
          const timing = applied && Number.isFinite(entry.appliedAt) ? `<br><small class="hardware-muted">Applied at ${entry.appliedAt} ns (${basis()})${entry.time !== entry.appliedAt ? ` · RT event at ${entry.time} ns` : ""}</small>` : "";
          return `<button type="button" data-event="${escapeHtml(entry.issueEventId || entry.eventId || "")}"${origin} title="${entry.issueEventId ? "Show parameter instruction" : "Show RT event"}">${escapeHtml(entry.display === undefined ? display(entry.value) : entry.display)}</button>${timing}`;
        };
        return `<tr><th>${escapeHtml(parameter.field)}</th><td>${cell(parameter.pending, false)}</td><td>${cell(parameter.applied, true)}</td></tr>`;
      }).join("") : '<tr><td colspan="3" class="hardware-empty">No settings yet</td></tr>');
      text(values["queue-note"], snapshot.simulation ? `Simulated RT queue: ${display(snapshot.simulation.queue.depth)} / ${display(snapshot.simulation.queue.capacity)} packets. Pending settings become applied when their captured RT packet executes.` : "RT queue occupancy is unavailable in analytical timing. Parameter values follow the analyzed execution path.");
      headNodes.parameter.title = values["queue-note"].textContent;
    }

    function resultUnits(result) {
      if (!result || result === "unknown" || !Number.isFinite(result.readyTime) || result.digital) return "";
      return result.units || (result.source && result.source.kind === "scenario" ? "scenario values" : "");
    }

    // Compact I/Q values keep four significant digits; `exact` is the full record for tooltips.
    function resultLabel(result, { exact = false, units = true } = {}) {
      if (!result || result === "unknown") return "unknown";
      const reasons = [...new Set([result.reason, result.timetag_reason, result.count_valid === false ? result.classification_reason : undefined].filter(Boolean))];
      if (!Number.isFinite(result.readyTime)) return [statusLabel(result.status), ...reasons].join(" · ");
      if (result.digital) {
        const { level, edgeCount, timetagTicks } = result.digital;
        const values = [];
        if (Number.isFinite(level)) values.push(`Level ${level}`);
        if (Number.isFinite(edgeCount)) values.push(`Edge count ${edgeCount}`);
        if (Number.isFinite(timetagTicks)) values.push(`Timetag delta ${timetagTicks / 128} ns`);
        return [...values, ...reasons].join(" · ");
      }
      const unit = resultUnits(result) ? ` (${resultUnits(result)})` : "";
      if (exact) return `I ${display(result.i)} · Q ${display(result.q)}${unit} · threshold ${display(result.thresholded)}`;
      return `I ${compactNumber(result.i)} · Q ${compactNumber(result.q)} · thr ${display(result.thresholded)}${units ? unit : ""}`;
    }

    function resultTimeLabel(result) {
      return Number.isFinite(result.readyTime) ? `Ready at ${result.readyTime} ns` : `Observed at ${result.outcomeTime} ns`;
    }

    function binAggregateLabel(aggregate, { exact = true, units = true } = {}) {
      if (aggregate.kind === "ttl") return `TTL bin total · Edge count ${display(aggregate.total_count)}`;
      if (aggregate.kind === "iq") {
        const number = exact ? display : compactNumber;
        return `I/Q bin aggregate · ${aggregate.count} accepted ${aggregate.count === 1 ? "window" : "windows"} · Mean I ${number(aggregate.mean_i)} · Mean Q ${number(aggregate.mean_q)} · Threshold count ${display(aggregate.thresholded_count)}${units && aggregate.units ? ` (${aggregate.units})` : ""}`;
      }
      const delta = Number.isFinite(aggregate.timetag_ticks) ? `${aggregate.timetag_ticks / 128} ns` : "unknown";
      return `QTM bin aggregate · ${aggregate.avg_cnt} accepted ${aggregate.avg_cnt === 1 ? "write" : "writes"} · Mean count ${display(aggregate.count)} · Mean delta ${delta}`;
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
      const startup = simulation.config.initial_buffer ? "RT queue prefill" : `Startup ${display(simulation.config.startup_delay_ns)} ns`;
      text(values["simulation-queue"], `RT queue ${display(queue.depth)} / ${display(queue.capacity)} packets${stall ? ` · Q1 stalled: ${statusLabel(stall.event.meta.reason)} (${stall.time}–${stall.end} ns)` : ""}. ${startup} · simulation end ${display(simulation.endTime)} ns.`);
      const stages = index.signalStages.get(state.sequencerId) || new Map();
      if (!stages.has(state.signalStage)) state.signalStage = stages.keys().next().value;
      html(controls["signal-stage"], [...stages.keys()].map((stage) => `<option value="${escapeHtml(stage)}">${escapeHtml(statusLabel(stage))}</option>`).join(""));
      controls["signal-stage"].value = state.signalStage || "";
      controls["signal-stage"].disabled = !stages.size;
      const signal = model.signalAt(index, state.sequencerId, state.signalStage, state.time, basis());
      if (signal) {
        // Paths are prepared once by the state index; cursor changes read only one sample per path.
        const chartKey = JSON.stringify([state.sequencerId, state.signalStage, basis()]);
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
        // Paths that share one source name it once below the values instead of under each path.
        const sources = signal.values.map((entry) => inputSource(entry.source));
        const sharedSource = sources.length > 1 && sources.every((source) => source === sources[0]);
        html(values["signal-values"], signal.values.map((entry, number) => `<p><strong>${escapeHtml(entry.path.toUpperCase())}: ${escapeHtml(display(entry.value))}</strong>${entry.sampleTime === undefined ? signal.localTime === undefined ? " · alignment unavailable" : " · outside available trace" : ` · sample ${entry.sampleTime} ns`}${sharedSource ? "" : `<br>${escapeHtml(sources[number])}`}</p>`).join("") + (sharedSource ? `<p data-signal-source>${escapeHtml(sources[0])}</p>` : ""));
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
        const exactOutcome = entry.acquisitionOutcome ? ` title="${escapeHtml(resultLabel(entry.acquisitionOutcome, { exact: true }))}"` : "";
        const details = entry.acquisitionOutcome ? resultLabel(entry.acquisitionOutcome) : [meta.id === undefined ? null : `id ${display(meta.id)}`, meta.address === undefined ? null : `address ${display(meta.address)}`, meta.value === undefined ? null : `value ${display(meta.value)}`, meta.targets ? `to ${display(meta.targets)}` : null, meta.reason && statusLabel(meta.reason), meta.condition === undefined ? null : `condition ${display(meta.condition)}`].filter(Boolean).join(" · ");
        return `<div class="hardware-communication"><button type="button" data-event="${escapeHtml(entry.event.id)}">${entry.time} ns · ${escapeHtml(statusLabel(entry.event.kind))}</button><span${exactOutcome}>${escapeHtml(details)}</span>${meta.input_index === undefined ? "" : `<span>Scenario input ${escapeHtml(meta.input_index)}</span>`}${meta.source || meta.input_source ? `<span>${escapeHtml(inputSource(meta.input_source || meta.source))}</span>` : ""}${meta.source_event_id ? `<button type="button" data-event="${escapeHtml(meta.source_event_id)}">Show sending event</button>` : ""}${entry.event.kind === "feedback_send" && meta.q1_issue_event_id ? `<button type="button" data-event="${escapeHtml(meta.q1_issue_event_id)}" data-instruction-event="${escapeHtml(meta.q1_issue_event_id)}">Show sending instruction</button>` : ""}</div>`;
      }).join("")}`);
    }

    function selectedBinRecord() {
      const records = snapshot.bins.records;
      const exact = records.find((entry) => entry.key === selectedBin);
      if (exact || selectedBin === undefined) return exact;
      const legacy = records.filter((entry) => entry.legacyKey === selectedBin);
      if (legacy.length !== 1) return undefined;
      selectedBin = legacy[0].key;
      return legacy[0];
    }

    function renderBins() {
      const bins = snapshot.bins;
      text(values["bins-note"], snapshot.simulation ? "Targets count executed commands that specify a bin destination. Acquisition outcomes show when they were observed and their input source." : "Observed acquire commands, not hardware average counts. I/Q and threshold results are unknown in analytical timing.");
      headNodes.targets.title = values["bins-note"].textContent;
      text(values["bin-count"], bins.total ? `${state.binOffset + 1}–${Math.min(state.binOffset + PAGE_SIZE, bins.total)} of ${bins.total} targets` : "No acquisition targets yet");
      find('[data-action="bins-previous"]').disabled = state.binOffset === 0;
      find('[data-action="bins-next"]').disabled = state.binOffset + PAGE_SIZE >= bins.total;
      // One unit shared by every visible I/Q value moves to the column header; mixed units stay inline.
      const unitSet = new Set(bins.records.flatMap((bin) => [resultUnits(bin.result), bin.aggregate?.kind === "iq" ? bin.aggregate.units || "" : ""]).filter(Boolean));
      const sharedUnit = unitSet.size === 1 ? [...unitSet][0] : "";
      const inlineUnits = !sharedUnit;
      text(values["bins-units"], sharedUnit ? `(${sharedUnit})` : "");
      html(values.bins, bins.records.length ? bins.records.map((bin) => {
        const result = bin.result;
        const exactResult = result === "unknown" || resultLabel(result, { exact: true }) === resultLabel(result, { units: inlineUnits }) ? "" : ` · ${resultLabel(result, { exact: true })}`;
        const outcome = result === "unknown" ? "unknown" : `<button type="button" data-event="${escapeHtml(result.eventId)}" title="${resultTimeLabel(result)} · ${escapeHtml(inputSource(result.input_source || result.source))}${escapeHtml(exactResult)}">${escapeHtml(resultLabel(result, { units: inlineUnits }))}</button>`;
        const aggregateText = bin.aggregate ? `<div data-bin-aggregate title="${escapeHtml(binAggregateLabel(bin.aggregate))}">${escapeHtml(binAggregateLabel(bin.aggregate, { exact: false, units: inlineUnits }))}</div>` : "";
        const kind = binKindLabel(bin.kind);
        const anchor = bin.count ? "Select last acquisition targeting this bin" : "Select observed bin write";
        return `<tr><td>${escapeHtml(display(bin.acquisitionIndex))}</td><td><button type="button" data-bin="${escapeHtml(bin.key)}" title="Inspect this bin's target history">${escapeHtml(display(bin.binIndex))}</button>${kind ? ` <span>${kind}</span>` : ""}</td><td>${escapeHtml(bin.count)}</td><td><button type="button" data-event="${escapeHtml(bin.eventId || "")}" title="${anchor}">${escapeHtml(display(bin.lastTime))} ns${bin.count ? "" : " write"}</button></td><td>${outcome}${aggregateText}</td></tr>`;
      }).join("") : '<tr><td colspan="5" class="hardware-empty">No resolved bin targets at this time.</td></tr>');
      const bin = selectedBinRecord();
      const kind = binKindLabel(bin?.kind);
      const gate = Object.entries(bin?.gate || {}).filter(([, entry]) => entry).map(([role, entry]) =>
        `<button type="button" data-event="${escapeHtml(entry.eventId)}">Gate ${role} · ${escapeHtml(entry.time)} ns</button>`).join("");
      html(values["bin-history"], bin ? `<span>Acquisition ${escapeHtml(bin.acquisitionIndex)} · bin ${escapeHtml(bin.binIndex)}${kind ? ` · ${kind}` : ""} · latest ${bin.history.length} targets</span><div>${bin.history.map((entry) => `<button type="button" data-event="${escapeHtml(entry.eventId)}">${escapeHtml(entry.time)} ns</button>`).join("")}</div>${gate ? `<div>${gate}</div>` : ""}${bin.result === "unknown" ? "<p>No acquisition outcome at this cursor.</p>" : `<p title="${escapeHtml(resultLabel(bin.result, { exact: true }))}">${escapeHtml(resultLabel(bin.result, { units: inlineUnits }))} · ${resultTimeLabel(bin.result)}<br>${escapeHtml(inputSource(bin.result.input_source || bin.result.source))}</p>`}` : '<span>Select a bin number to inspect its target history.</span>');
    }

    function setBlock(id, value, active, detail = "") {
      if (blockValues[id] && value !== undefined) text(blockValues[id], value);
      if (blockTitles[id]) { text(blockTitles[id], detail); blockTitles[id].hidden = !detail; }
      const selected = state.selectedBlock === id;
      for (const node of [rowNodes[id], blockNodes[id]]) {
        node.classList.toggle("is-active", !!active);
        node.classList.toggle("is-selected", selected);
      }
      blockNodes[id].setAttribute("aria-pressed", String(selected));
    }

    function pathValue(label, range) {
      const formula = label ? `<code>${escapeHtml(label)}</code>` : "";
      if (!range) return formula || "—";
      const [low, high] = range;
      if (low !== high) return `${formula ? `${formula}, ` : ""}from ${escapeHtml(compactNumber(low))} to ${escapeHtml(compactNumber(high))}`;
      const number = compactNumber(low);
      return formula && label !== number ? `${formula} = ${escapeHtml(number)}` : escapeHtml(number);
    }

    // The output row reads the lane above it: the formula of the interval under the cursor and what
    // each path holds there, as a fraction of full scale. Settings in effect follow on their own line.
    function renderOutput(plays) {
      const drawn = rack.at(state.sequencerId, axisTime());
      if (drawn && (drawn.interval || drawn.i || drawn.q)) {
        const labels = drawn.interval ? drawn.interval.labels : ["", ""];
        html(blockValues.awg, ["i", "q"].map((path, number) => `<span class="hardware-path" title="Sequencer path ${path.toUpperCase()}, as a fraction of full scale"><b class="rack-path is-${path}">${path.toUpperCase()}</b> ${pathValue(labels[number], drawn[path])}</span>`).join(""));
      } else {
        const unknown = snapshot.awg.some((entry) => entry.status === "unknown");
        text(blockValues.awg, plays.length ? `${statusLabel(plays[0].status)} · ${eventLabel(plays[0].event)}` : unknown ? "unknown" : "no play command yet");
      }
      const lines = [];
      const assumed = drawn && drawn.interval ? drawn.interval.assumed : [];
      if (assumed.length) lines.push(`<span class="hardware-assumed">Drawn with assumed ${escapeHtml(listNames(assumed))}</span>`);
      // One instruction plays on both paths; it is named once.
      const seen = new Set();
      const named = (event) => `${eventLabel(event)}|${event.source ? `${event.source.file}:${event.source.line}` : event.id}`;
      const commands = plays.filter((entry) => !seen.has(named(entry.event)) && seen.add(named(entry.event)));
      if (drawn && commands.length) lines.push(`<span>Last play: ${commands.map((entry) => `<button type="button" data-event="${escapeHtml(entry.event.id)}" title="Move the cursor to the start of this command">${escapeHtml(eventLabel(entry.event))}</button>`).join(" ")}</span>`);
      const applied = snapshot.parameters.filter((parameter) => parameter.applied && parameter.applied.known !== false);
      const pending = snapshot.parameters.filter((parameter) => parameter.pending).length;
      if (applied.length || pending) lines.push(`<span>${applied.length ? `In effect: ${applied.map((parameter) => `${escapeHtml(parameter.field)} ${escapeHtml(parameter.applied.display === undefined ? display(parameter.applied.value) : parameter.applied.display)}`).join(" · ")}` : "No setting in effect yet"}${pending ? ` · ${pending} pending` : ""}</span>`);
      html(values["output-detail"], lines.join(""));
      values["output-detail"].hidden = !lines.length;
    }

    function selectedEvent() {
      if (state.selectedBlock === "q1") return snapshot.q1.event;
      if (state.selectedBlock === "rt" || state.selectedBlock === "queue") return snapshot.rt.event;
      if (state.selectedBlock === "awg") return (snapshot.awg.find((entry) => entry.event) || {}).event;
      if (state.selectedBlock === "acquisition" || state.selectedBlock === "bins") return snapshot.acquisition.event;
      return undefined;
    }

    // The model caveat is constant for a trace kind, so it lives in the badge tooltip.
    function timingNote(simulated) {
      return simulated
        ? "Q1 registers advance independently of RT execution. Timing uses an event-driven model of queues and configured inputs. Signal and result sources identify modeled values, supplied traces, or supplied measurements."
        : "Q1 registers advance independently of RT execution. Timing is analytical; RT queue stalls are not modeled. Values are computed from the program, not measured on hardware.";
    }

    function alignmentUnknown(sequencerId = state.sequencerId) {
      const alignment = index.alignment[sequencerId] || {};
      return alignment.unaligned || alignment.confidence === "unknown" || (alignment.offset !== undefined && model.numericValue(alignment.offset) === undefined);
    }

    function render(notifyChange = true) {
      if (destroyed) return;
      const range = ranges[basis()];
      const unavailableAlignment = basis() === "aligned" && alignmentUnknown();
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
      text(values.basis, basis() === "aligned" ? "Aligned time" : "Local time");
      find('[data-action="previous"]').disabled = !times.length || times[0] >= state.time;
      find('[data-action="next"]').disabled = !times.length || times[times.length - 1] <= state.time;
      const stepScope = state.level === "detail" ? state.sequencerId || "no sequencer" : "visible sequencers";
      find('[data-action="previous"]').title = `Previous state change · ${stepScope}`;
      find('[data-action="next"]').title = `Next state change · ${stepScope}`;
      renderNavigation();
      // Where lanes are drawn they are the scrubber: press or drag on one to move the cursor.
      controls.scrubber.hidden = rack.hasLanes() && !(replayOpen && state.level === "detail");
      find('[data-action="replay-toggle"]').setAttribute("aria-expanded", String(replayOpen && state.level === "detail"));
      const simulated = sequencers.some((seq) => index.simulation[seq.id]);
      text(values["trace-kind"], simulated ? "Simulated" : sequencers.some((seq) => index.execution[seq.id]?.mode === "playback") ? "Path execution" : "Analyzed");
      values["trace-kind"].title = timingNote(simulated);
      // What holds at this time is said outright. Caveats that hold for the whole run are said once
      // when there is one, and folded behind a count when there are several.
      const showWarnings = (warnings, alerts) => {
        const said = (warning) => escapeHtml(/[.!?]$/.test(warning) ? warning : `${warning}.`);
        const now = warnings.filter((warning) => alerts.includes(warning));
        const always = warnings.filter((warning) => !alerts.includes(warning));
        const open = values.warnings.querySelector("details")?.open ? ' open=""' : "";
        values.warnings.hidden = warnings.length === 0;
        html(values.warnings, now.map((warning) => `<span class="is-alert">${said(warning)}</span>`).join(" ") +
          (always.length > 1 ? `<details${open}><summary>${always.length} limits of this analysis</summary><ul>${always.map((warning) => `<li>${said(warning)}</li>`).join("")}</ul></details>`
            : always.map((warning) => `<span>${said(warning)}</span>`).join("")));
      };
      if (state.level !== "detail") {
        showWarnings(renderOverview(), [...rackSummaries.values()].flatMap((summary) => summary.alerts || []));
        sourceTarget = undefined;
        if (notifyChange) notify();
        return;
      }
      snapshot = model.snapshot(index, state.sequencerId, state.time, basis(), { offset: state.binOffset, limit: PAGE_SIZE });
      if (state.binOffset && state.binOffset >= snapshot.bins.total) {
        state.binOffset = 0;
        snapshot = model.snapshot(index, state.sequencerId, state.time, basis(), { offset: 0, limit: PAGE_SIZE });
      }
      const warnings = snapshot.warnings.map((warning) => typeof warning === "string" ? warning : warning.message || display(warning));
      const alerts = [...(snapshot.alerts || [])];
      if (ownClock()) {
        alerts.unshift("This sequencer's start relative to the others is unknown, so times here are its own.");
        warnings.unshift(alerts[0]);
      }
      showWarnings(warnings, alerts);
      renderReplay();
      if (replayOpen) { sourceTarget = undefined; if (notifyChange) notify(); return; }
      rack.render();
      text(values.execution, sentence(executionLabel(snapshot.execution, snapshot.terminal)));
      values.execution.title = executionLabel(snapshot.execution, snapshot.terminal, true);
      text(values["trace-kind"], snapshot.simulation ? "Simulated" : snapshot.execution?.mode === "playback" ? "Path execution" : "Analyzed");
      text(values["timing-note"], timingNote(!!snapshot.simulation));
      values["trace-kind"].title = values["timing-note"].textContent;
      values.execution.classList.toggle("is-incomplete", !!snapshot.execution && snapshot.execution.status !== "completed");
      renderInstruction();
      const simulation = snapshot.simulation;
      const acquires = snapshot.program.acquires;
      setBlock("q1", undefined, ["active", "stalled"].includes(snapshot.q1.status));
      setBlock("rt", undefined, snapshot.rt.status === "active");
      rowNodes.queue.hidden = !simulation;
      if (simulation) {
        const stall = simulation.stall;
        setBlock("queue", `${display(simulation.queue.depth)} / ${display(simulation.queue.capacity)} packets`, simulation.queue.depth > 0,
          stall ? `Q1 stalled: ${statusLabel(stall.event.meta.reason)} (${stall.time}–${stall.end} ns)` : "");
      }
      const plays = snapshot.awg.filter((entry) => entry.event);
      setBlock("awg", undefined, plays.some((entry) => entry.status === "active"));
      renderOutput(plays);
      rowNodes.acquisition.hidden = !acquires;
      const absent = [];
      if (!acquires) absent.push("This program has no acquisitions.");
      if (!simulation) absent.push("RT queue depth and signals need a simulation run.");
      text(values.absent, absent.join(" "));
      values.absent.hidden = !absent.length;
      const acquisition = snapshot.acquisition;
      const activeAcquisition = acquisition.status === "active";
      const integration = acquisition.event?.meta?.acquisition_window;
      let acquisitionStatus = !acquisition.event ? "No resolved acquire" : activeAcquisition
        ? "Integrating" : acquisition.status === "unknown" ? "Integration length unknown"
        : integration?.status === "interrupted" ? "Interrupted" : "Last acquire command";
      let acquisitionDetail = eventLabel(acquisition.event);
      if (acquisition.gate) {
        const { start, end, status } = acquisition.gate;
        acquisitionStatus = activeAcquisition ? "Gate open" : status === "incomplete" ? "Gate incomplete" : "Gate closed";
        acquisitionDetail += activeAcquisition ? ` · Opened at ${start} ns` : ` · ${acquisitionStatus} at ${end} ns`;
      } else if (acquisition.sample) {
        const level = acquisition.sample.digital?.level;
        acquisitionStatus = Number.isFinite(level) ? `Sampled level ${level}` : "Level unavailable";
        acquisitionDetail = `${resultLabel(acquisition.sample, { exact: true })} · Observed at ${acquisition.sample.outcomeTime} ns`;
      }
      const assumed = integration?.source === "instruction_duration_fallback" && integration.status !== "unknown";
      setBlock("acquisition", assumed ? `${acquisitionStatus} (assumed)` : acquisitionStatus,
        activeAcquisition, `${acquisition.event ? `${acquisitionDetail} · ` : ""}${snapshot.bins.total} bin ${snapshot.bins.total === 1 ? "target" : "targets"} so far`);
      for (const core of ["q1", "rt"]) {
        const event = snapshot[core].event;
        text(values[`${core}-event`], `${statusLabel(snapshot[core].status)}${event ? ` · ${eventLabel(event)}` : ""}`);
        find(`[data-action="${core}-event"]`).disabled = !event;
      }
      rowEvents = { q1: snapshot.q1.event, rt: snapshot.rt.event, queue: simulation && simulation.queue.event, awg: plays[0] && plays[0].event, acquisition: acquisition.event };
      for (const block of BLOCKS) {
        const source = rowEvents[block.id] && rowEvents[block.id].source;
        const link = rowSources[block.id];
        link.hidden = !(source && source.file);
        if (link.hidden) continue;
        text(link, `${baseName(source.file)}:${source.line || 1}`);
        link.title = `Open ${source.file}:${source.line || 1}`;
        link.disabled = !options.onSourceClick;
      }
      const tab = activeTab();
      find('[data-tab="bins"]').hidden = !acquires;
      find('[data-tab="simulation"]').hidden = !simulation;
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
      if (state.selectedBlock === "bins" && selectedBin !== undefined) {
        const bin = selectedBinRecord();
        sourceTarget = bin ? { source: bin.source, eventId: bin.eventId } : undefined;
      }
      const source = sourceTarget && sourceTarget.source;
      text(values["selection-detail"], source && source.file ? `${baseName(source.file)}:${source.line || 1}` : "");
      values["selection-detail"].title = source && source.file ? `${source.file}:${source.line || 1}` : "";
      const sourceButton = find('[data-action="source"]');
      // Rows link to their own source lines; this bar is for a selected register, bin or row.
      find(".hardware-source").hidden = !(source && source.file);
      sourceButton.disabled = !(source && source.file && options.onSourceClick);
      if (notifyChange) notify();
    }

    function setTime(time, notifyChange = true) {
      if (!Number.isFinite(time)) return;
      pauseReplay();
      state.time = snapTime(time);
      syncReplayPosition();
      render(notifyChange);
    }

    function commitTimeInput() {
      const time = controls.time.valueAsNumber;
      if (!Number.isFinite(time) || snapTime(time) === state.time) {
        controls.time.value = String(state.time);
        return;
      }
      setTime(time);
    }

    function chooseEvent(eventId, notifyEvent = true) {
      const event = events.get(String(eventId));
      if (!event) return;
      const time = model.eventTime(index, event, detailBasis(event.sequencer_id));
      const seqId = String(event.sequencer_id);
      const sequencer = sequencers.find((seq) => seq.id === seqId);
      if (!sequencer) return;
      pauseReplay();
      if (state.sequencerId !== seqId) { replayOpen = false; state.binOffset = 0; }
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
      const writer = model.registerWrite(index, event.sequencer_id, register, eventId, detailBasis(event.sequencer_id));
      const sequencer = sequencers.find((seq) => seq.id === String(event.sequencer_id));
      if (!writer || !sequencer) return;
      pauseReplay();
      if (state.sequencerId !== sequencer.id) { replayOpen = false; state.binOffset = 0; }
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
      const node = event.target.closest("[data-action], [data-register], [data-register-time], [data-provenance-step], [data-block], [data-row-source], [data-tab], [data-event], [data-bin], [data-level], [data-crumb-module]");
      if (!node || !container.contains(node)) return;
      if (node.dataset.rowSource) {
        const target = rowEvents[node.dataset.rowSource];
        if (target && options.onSourceClick) options.onSourceClick(target.source, target.id);
        return;
      }
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
          if (replayPosition === replayCount() - 1) { state.time = isProjectReplay() ? projectFrames()[0] : replayTrace.instructions[0].time; replayPosition = isProjectReplay() ? 0 : -1; }
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
        const current = model.snapshot(index, node.dataset.sequencerId, state.time, state.basis, { limit: 1 });
        if (current.q1.event && options.onSourceClick) options.onSourceClick(current.q1.event.source, current.q1.event.id);
        return;
      }
      if (node.dataset.crumbModule !== undefined) { rack.reveal(state.moduleId); navigate("modules"); return; }
      if (node.dataset.level) { navigate(node.dataset.level); return; }
      if (node.dataset.registerTime !== undefined) { setTime(Number(node.dataset.registerTime)); notifySelection(); return; }
      if (node.dataset.provenanceStep !== undefined) {
        const register = snapshot.registers.find((entry) => entry.register === selectedRegister);
        const step = register && register.meta && register.meta.provenance && register.meta.provenance.steps[Number(node.dataset.provenanceStep)];
        if (step && step.source && options.onSourceClick) options.onSourceClick(step.source);
        return;
      }
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
        const history = model.registerHistory(index, state.sequencerId, selectedRegister, state.time, basis());
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
        // Another module is chosen from the rack, where its ports and sequencers are listed.
        rack.reveal(controls.module.value);
        navigate("modules", controls.module.value);
      }
      if (control === "sequencer") navigate("detail", state.moduleId, controls.sequencer.value);
      if (control === "register-format") { state.registerFormat = controls[control].value; render(); }
      if (control === "all-registers") { state.showAllRegisters = controls[control].checked; render(); }
      if (control === "signal-stage") { state.signalStage = controls[control].value; render(); }
      if (control === "replay-scope") { pauseReplay(); state.replayScope = controls[control].value; if (isProjectReplay()) useProjectClock(); times = stepTimesForView(); updateReplayTrace(); render(); }
      if (control === "replay-speed") { state.replaySpeed = Number(controls[control].value); if (replayTimer !== undefined) { pauseReplay(); scheduleReplay(); } render(); }
      if (control === "time") commitTimeInput();
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
        commitTimeInput();
      }
      if (event.target.dataset.tab && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        const tabs = Array.from(container.querySelectorAll("[data-tab]")).filter((tab) => !tab.hidden);
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
      finishReplayPreparation() {
        if (!preparingReplay) return;
        preparingReplay = false;
        render();
      },
      setTime,
      setTimeBasis(basis) {
        const nextBasis = basis === "aligned" ? "aligned" : "local";
        if (state.basis === nextBasis) return;
        pauseReplay();
        if (replayOpen && isProjectReplay() && nextBasis === "aligned" && !hasSharedAlignment) replayOpen = false;
        const alignment = index.alignment[state.sequencerId] || {};
        const offset = model.numericValue(alignment.offset);
        if (!alignmentUnknown() && offset !== undefined) state.time += nextBasis === "aligned" ? offset : -offset;
        state.basis = nextBasis;
        times = stepTimesForView();
        updateReplayTrace();
        render();
      },
      getState() { return { ...state, selectedRegister, selectedBin, replayOpen, rack: rack.state() }; },
      setOutputWindow(message) { rack.receive(message); },
      destroy() {
        destroyed = true;
        rack.destroy();
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
