(function (root) {
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
  const hex = (word) => word == null ? "—" : `0x${word.toString(16).toUpperCase().padStart(8, "0")}`;
  const interpret = (word, mode) => word == null ? null : mode === "signed" && word >= 2 ** 31 ? word - 2 ** 32 : word;
  const statusName = (status) => ({ completed: "Completed", error: "Failed", blocked: "Blocked", step_limit: "Limit reached",
    undetermined: "Final output undetermined", cancelled: "Cancelled", running: "Running", stale: "Stale", unrun: "Unexecuted" })[status] || status;
  const format = (number) => Number.isInteger(number) ? String(number) : String(Number(number.toPrecision(12)));
  const assignments = (defs) => Object.entries(defs).map(([name, value]) => `${name}=${value}`).join(", ");
  const diagnostics = (row) => [row.reason, ...row.diagnostics.map((item) => `${item.severity}: ${item.message}`)].filter(Boolean).join(" · ");
  const metricNames = new Map([["time", "Q1 time"], ["formula", "Formula"], ["reference", "Reference"], ["error", "Error"]]);
  const formulaMetrics = ["formula", "reference", "error"];
  const verdictNames = { pass: "Pass", fail: "Fail", unavailable: "Unavailable", compared: "Compared" };

  const formulaOperators = {
    "+": [1, (a, b) => a + b], "-": [1, (a, b) => a - b],
    "*": [2, (a, b) => a * b], "/": [2, (a, b) => a / b], "%": [2, (a, b) => a % b],
    "**": [4, Math.pow], "^": [4, Math.pow],
  };
  const formulaFunctions = new Map([
    ...["abs", "acos", "asin", "atan", "ceil", "cos", "exp", "floor", "log", "log2", "log10", "round", "sin", "sqrt", "tan"]
      .map((name) => [name, 1]),
    ...["atan2", "hypot", "max", "min", "pow"].map((name) => [name, 2]),
  ]);

  function compileFormula(source, { registers, defs }) {
    source = source.trim();
    if (!source || source.length > 512) throw new Error("Enter a formula of up to 512 characters.");
    const lexer = /\s*(0[xX][\da-fA-F]+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|\$?[A-Za-z_][\w]*|\*\*|[+*/%^(),-])/y;
    const tokens = [];
    while (lexer.lastIndex < source.length) {
      const match = lexer.exec(source);
      if (!match) throw new Error("Unsupported character in the formula.");
      tokens.push(match[1]);
    }
    const readers = new Map();
    let cursor = 0;
    const peek = () => tokens[cursor];
    const expect = (token) => {
      if (tokens[cursor++] !== token) throw new Error(`Expected "${token}".`);
    };
    const numeric = (value) => Number.isFinite(value) ? value : NaN;

    function prefix() {
      const token = tokens[cursor++];
      if (token === "+" || token === "-") {
        const operand = expression(3);
        return token === "+" ? operand : (values) => -operand(values);
      }
      if (token === "(") {
        const inner = expression();
        expect(")");
        return inner;
      }
      if (/^[\d.]/.test(token ?? "")) {
        const number = Number(token);
        if (!Number.isFinite(number)) throw new Error("A numeric literal is out of range.");
        return () => number;
      }
      if (!/^\$?[A-Za-z_]/.test(token ?? "")) throw new Error("Expected a number, variable, or function.");
      if (peek() === "(") {
        const arity = formulaFunctions.get(token);
        if (!arity) throw new Error(`Unknown function: ${token}.`);
        cursor += 1;
        const args = [expression()];
        while (peek() === ",") { cursor += 1; args.push(expression()); }
        expect(")");
        if (args.length !== arity) throw new Error(`${token} needs ${arity} argument(s).`);
        return (values) => {
          const operands = args.map((arg) => arg(values));
          return operands.every(Number.isFinite) ? numeric(Math[token](...operands)) : NaN;
        };
      }
      const name = token.startsWith("$") ? token.slice(1) : token;
      if (/^R(?:\d|[1-5]\d|6[0-3])$/.test(token)) {
        if (!registers.includes(token)) throw new Error(`Select ${token} and run a new sweep to use it.`);
        readers.set(token, (row, mode) => interpret(row.words?.[token], mode));
      } else if (Object.hasOwn(defs, name)) {
        readers.set(token, (row) => {
          const value = Number(row.defs?.[name]);
          return Number.isSafeInteger(value) ? value : NaN;
        });
      } else if (["pi", "PI", "e", "E"].includes(token)) {
        return () => token.toLowerCase() === "pi" ? Math.PI : Math.E;
      } else {
        throw new Error(`Unknown variable: ${token}.`);
      }
      return (values) => values.get(token);
    }

    function expression(minimum = 1) {
      let left = prefix();
      while (Object.hasOwn(formulaOperators, peek())) {
        const [precedence, operation] = formulaOperators[peek()];
        if (precedence < minimum) break;
        cursor += 1;
        const right = expression(precedence === 4 ? precedence : precedence + 1);
        const previous = left;
        left = (values) => {
          const a = previous(values), b = right(values);
          return Number.isFinite(a) && Number.isFinite(b) ? numeric(operation(a, b)) : NaN;
        };
      }
      return left;
    }

    const evaluate = expression();
    if (cursor !== tokens.length) throw new Error(`Unexpected token: ${peek()}.`);
    return (row, mode) => {
      if (!["completed", "undetermined"].includes(row.status)) return null;
      const values = new Map();
      for (const [name, read] of readers) {
        const value = read(row, mode);
        if (!Number.isFinite(value)) return null;
        values.set(name, value);
      }
      const value = evaluate(values);
      return Number.isFinite(value) ? value : null;
    };
  }

  function parseTolerance(text) {
    const literal = text.trim();
    if (!literal) return null;
    const value = Number(literal);
    if (!/^\+?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(literal) || !Number.isFinite(value)) {
      throw new Error("Enter a finite, non-negative absolute tolerance.");
    }
    return value;
  }

  function compareValues(actual, reference, tolerance) {
    const error = actual == null || reference == null ? NaN : actual - reference;
    if (!Number.isFinite(error)) return { formula: actual, reference, error: null, verdict: "unavailable" };
    const verdict = tolerance === null ? "compared" : Math.abs(error) <= tolerance ? "pass" : "fail";
    return { formula: actual, reference, error, verdict };
  }

  function estimatedDuration(ms) {
    const seconds = Math.ceil(ms / 1000);
    if (seconds < 60) return `${seconds} s`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} min ${seconds % 60} s`;
    return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
  }

  function plotValue(row, mode, metric) {
    if (metric === "time") {
      return row.engine_completed && ["completed", "undetermined"].includes(row.status) && Number.isFinite(row.q1_ns) ? row.q1_ns : null;
    }
    return row.status === "completed" ? interpret(row.words?.[metric], mode) : null;
  }

  function statistics(rows, mode, metric) {
    return summarize(rows.map((row) => plotValue(row, mode, metric)));
  }

  function fraction(value, min, max) {
    if (max === min) return 0.5;
    const span = max - min;
    return Number.isFinite(span) ? (value - min) / span : (value / 2 - min / 2) / (max / 2 - min / 2);
  }

  function summarize(samples, continuous = false) {
    const values = samples.filter((value) => value !== null);
    if (!values.length) return null;
    const min = Math.min(...values), max = Math.max(...values);
    // Center before summing to keep small differences between large words precise.
    const scale = continuous ? Math.max(Math.abs(min), Math.abs(max)) || 1 : 1;
    const base = min / scale;
    const offset = values.reduce((sum, value) => sum + (value / scale - base), 0) / values.length;
    const deviation = Math.sqrt(values.reduce((sum, value) => sum + (value / scale - base - offset) ** 2, 0) / values.length);
    const mean = Math.min(max, Math.max(min, (base + offset) * scale));
    const binCount = Math.min(12, Math.ceil(Math.sqrt(values.length)));
    if (continuous) {
      const count = min === max ? 1 : binCount;
      const edge = (index) => min * (1 - index / count) + max * (index / count);
      const bins = Array.from({ length: count }, (_, i) => ({ min: edge(i), max: edge(i + 1), count: 0 }));
      for (const value of values) bins[Math.min(count - 1, Math.floor(fraction(value, min, max) * count))].count += 1;
      return { count: values.length, min, max, mean, deviation: Math.min(1, deviation) * scale, bins };
    }
    const width = Math.ceil((max - min + 1) / binCount);
    const bins = Array.from({ length: Math.floor((max - min) / width) + 1 }, (_, i) => ({
      min: min + i * width, max: Math.min(max, min + (i + 1) * width - 1), count: 0,
    }));
    for (const value of values) bins[Math.floor((value - min) / width)].count += 1;
    return { count: values.length, min, max, mean, deviation, bins };
  }

  function sampledInputs(sweep) {
    const axes = sweep.plan.axes;
    const projections = axes.map((axis) => {
      const min = BigInt(axis.min), span = BigInt(axis.last) - min;
      const scale = Number(span);
      return (value) => {
        if (!span) return 0.5;
        const offset = BigInt(value) - min;
        return Number.isFinite(scale) ? Number(offset) / scale : Number(offset * 1_000_000n / span) / 1_000_000;
      };
    });
    const inputs = new Map();
    for (const row of sweep.rows) {
      const values = axes.map((axis) => row.defs[axis.name]);
      const key = values.join(",");
      const sample = inputs.get(key);
      if (sample) sample.repeats += 1;
      else inputs.set(key, { row, repeats: 1, position: values.map((value, i) => projections[i](value)) });
    }
    return [...inputs.values()];
  }

  function plot(sweep, stats, mode, metric, derived, samples) {
    const [xAxis, yAxis] = sweep.plan.axes;
    const columns = Number(xAxis.count), rows = yAxis ? Number(yAxis.count) : 1;
    const width = yAxis ? samples ? 430 : Math.max(430, columns * 8) : 340;
    const height = yAxis && !samples ? Math.max(180, rows * 8) : 180;
    const cells = yAxis && (!samples || (columns <= width / 4 && rows <= height / 4));
    const left = yAxis ? 8 : derived ? 126 : 96, top = 20;
    const end = left + width, bottom = top + height;
    const xLabel = `${xAxis.name}: ${xAxis.min} → ${xAxis.last}`;
    const time = metric === "time";
    const quantity = metricNames.get(metric) ?? metric;
    const valueLabel = time ? "Q1 time (ns)" : derived ? quantity : `${metric} (${mode})`;
    const yLabel = yAxis ? `${yAxis.name}: ${yAxis.min} → ${yAxis.last}` : valueLabel;
    const chartLabel = yAxis ? `Two-axis DEF ${cells ? "heatmap" : "scatter plot"} by ${quantity}` : `DEF input versus ${quantity}`;
    const spread = stats && stats.max !== stats.min;
    const scale = (value) => stats ? fraction(value, stats.min, stats.max) : 0.5;
    const marks = [], missing = new Set();
    let hasFailures = false;
    if (cells) marks.push(`<rect class="def-plot-unrun" x="${left}" y="${top}" width="${width}" height="${height}"/>`);
    const points = samples ?? sweep.rows.map((row) => ({ row, repeats: 1 }));
    for (const [index, { row, repeats, position }] of points.entries()) {
      const i = row.id - 1;
      const x = position ? position[0] : columns === 1 ? 0.5 : Math.floor(i / rows) / (columns - 1);
      const y = position ? position[1] : rows === 1 ? 0.5 : (i % rows) / (rows - 1);
      const result = derived?.get(row.id);
      const value = derived ? result?.[metric] : plotValue(row, mode, metric);
      const verdict = result?.verdict;
      hasFailures ||= verdict === "fail";
      const missingStatus = value == null ? (row.status === "completed" ? "undetermined" : row.status) : "";
      if (missingStatus) missing.add(missingStatus);
      const title = `Case ${row.id} · ${assignments(row.defs)}${repeats > 1 ? ` · ${repeats} samples` : ""} · ${statusName(row.status)}${value == null ? "" : ` · ${quantity} ${format(value)}${time ? " ns" : ""}`}${verdict ? ` · ${verdictNames[verdict]}` : ""}`;
      const attributes = `data-case="${row.id}" class="def-plot-point ${esc(missingStatus)}"${verdict ? ` data-verdict="${verdict}"` : ""} role="button" tabindex="${index === 0 ? "0" : "-1"}" aria-label="${esc(title)}"`;
      const label = `<title>${esc(title)}</title>`;
      const color = yAxis && value != null ? ` style="fill:hsl(209 80% ${18 + 60 * scale(value)}%)"` : "";
      if (cells) {
        const w = width / columns, h = height / rows;
        marks.push(`<rect ${attributes}${color} x="${left + Math.round(x * (columns - 1)) * w}" y="${bottom - (Math.round(y * (rows - 1)) + 1) * h}" width="${w}" height="${h}">${label}</rect>`);
      } else {
        const cx = left + 5 + x * (width - 10);
        const cy = yAxis ? bottom - 6 - y * (height - 12) : value == null ? bottom + 14 : bottom - 6 - scale(value) * (height - 12);
        marks.push(`<circle ${attributes}${color} cx="${cx}" cy="${cy}" r="4">${label}</circle>`);
      }
    }
    if (!samples && !yAxis && sweep.rows.length < columns) {
      const x = left + (sweep.rows.length / columns) * width;
      marks.unshift(`<rect class="def-plot-unrun" x="${x}" y="${top}" width="${end - x}" height="${height}"/>`);
    }
    const tick = (value, y) => `<text x="${left - 6}" y="${y}" text-anchor="end">${format(value)}</text>`;
    const yTicks = !yAxis && stats ? (spread ? tick(stats.max, top + 8) + tick(stats.min, bottom) : tick(stats.min, top + height / 2 + 4)) : "";
    const colorRange = stats && (spread ? `${format(stats.min)} to ${format(stats.max)}` : format(stats.min));
    const minWidth = Math.max(230, yAxis && !samples ? left + columns * 8 : 0);
    const keys = [...missing].map((status) => `<span><i class="def-key ${esc(status)}"></i>${esc((time || derived) && status === "undetermined" ? `${quantity} unavailable` : statusName(status))}</span>`);
    if (hasFailures) keys.push('<span><i class="def-key def-outside-tolerance"></i>Outside tolerance</span>');
    if (points.length < columns * rows && (!samples || cells)) {
      keys.push(`<span><i class="def-key def-plot-unrun"></i>${samples ? "Unsampled inputs" : "Unexecuted"}</span>`);
    }
    if (yAxis && stats) keys.unshift(`<span><i class="def-plot-scale${spread ? "" : " is-constant"}"></i>${esc(valueLabel)} ${colorRange}</span>`);
    return `<div class="def-plot-scroll"><svg viewBox="0 0 ${end + 12} ${bottom + 35}" style="min-width:${minWidth}px" aria-label="${chartLabel}">
      <text x="${left}" y="14">${esc(yLabel)}</text>${yTicks}
      <path d="M${left} ${top}V${bottom}H${end}" class="def-plot-axis"/>${marks.join("")}
      <text x="${left}" y="${bottom + 29}">${esc(xLabel)}</text></svg></div>
      ${keys.length ? `<div class="def-plot-legend">${keys.join("")}</div>` : ""}`;
  }

  function mount(layoutGroup, vscode, project, saved = {}, persist = () => {}, unsaved = false) {
    const toggle = document.createElement("button");
    toggle.id = "q1timeline-sweep-toggle";
    toggle.type = "button";
    toggle.className = "q1timeline-layout-button";
    toggle.textContent = "Sweep";
    toggle.title = "Run with DEF values and sweep";
    toggle.setAttribute("aria-controls", "q1timeline-def-runs");
    layoutGroup.after(toggle);
    const panel = document.createElement("section");
    panel.id = "q1timeline-def-runs";
    panel.setAttribute("aria-label", "DEF runs and sweep");
    panel.innerHTML = `<div class="def-body">
      <h2>DEF sweep</h2>
      <div class="def-setup">
        <section class="def-card" aria-labelledby="def-inputs-heading">
          <div class="def-heading"><h3 id="def-inputs-heading">Run inputs</h3><button data-action="info">Reload DEFs</button></div>
          <div class="def-toolbar"><label>Sequencer <select data-field="seq"></select></label><code data-field="source"></code></div>
          <div data-field="defs" class="def-table-scroll def-input-table"></div>
          <fieldset class="def-observation">
            <legend>Observed registers <span data-field="observed-count" class="def-muted"></span></legend>
            <div id="q1lens-def-register-choices" class="def-register-choices">${Array.from({ length: 64 }, (_, i) => `<label hidden><input type="checkbox" data-register="R${i}">R${i}</label>`).join("")}</div>
            <p data-field="no-registers" class="def-muted" hidden>No registers referenced in this program.</p>
            <button type="button" data-action="toggle-registers" aria-controls="q1lens-def-register-choices" aria-expanded="false" hidden>Show all registers</button>
          </fieldset>
          <div class="def-toolbar">
            <label class="def-checkbox"><input type="checkbox" data-field="observe-time">Q1 time</label>
            <label>Interpretation <select data-field="mode"><option value="signed">Signed 32-bit</option><option value="unsigned">Unsigned 32-bit</option></select></label>
          </div>
          <div class="def-toolbar"><button data-action="run">Run from start</button><button data-action="reset">Reset all DEFs</button><span data-field="edited"></span></div>
        </section>
        <section class="def-card" aria-labelledby="def-range-heading">
          <h3 id="def-range-heading">Sweep ranges</h3><div data-field="axes"></div>
          <p data-field="plan" role="status" class="def-muted"></p>
          <p data-field="observation-required" class="def-muted" hidden>Select at least one register or Q1 time.</p>
          <div class="def-toolbar"><label>Results <select data-field="sweep-output"><option value="cases">Plots and cases</option><option value="statistics">Statistics only</option></select></label>
          <button data-action="sweep">Start sweep</button></div>
          <div class="def-toolbar"><label>Sample count <input type="number" data-field="sample-count" value="1000" min="1" step="1" aria-describedby="q1lens-sweep-sample-help"></label>
          <button data-action="estimate">Estimate time</button><button data-action="sample" aria-describedby="q1lens-sweep-sample-help">Random sample</button>
          <span data-field="setup-actions"><button data-action="cancel" hidden>Stop sweep</button></span></div>
          <p id="q1lens-sweep-sample-help" class="def-muted" data-field="sample-help" aria-live="polite"></p>
          <p data-field="estimate" role="status"></p>
        </section>
      </div>
      <p data-field="message" role="status"></p>
      <section class="def-card" data-field="sweep" aria-labelledby="def-results-heading" hidden>
        <div class="def-heading"><h3 id="def-results-heading" data-field="sweep-heading">Sweep results</h3><span data-field="progress-actions"></span></div>
        <div data-field="progress" role="status"></div><progress data-field="bar" aria-label="Sweep progress"></progress>
        <div data-field="register-summary"></div>
        <div data-field="plot-controls" class="def-heading" hidden><h4>Plots</h4><label>Plot metric <select data-field="metric"></select></label></div>
        <form data-field="formula-form" class="def-formula" hidden>
          <label>Formula <input type="text" data-field="formula" maxlength="512" spellcheck="false" placeholder="R2" aria-describedby="q1lens-sweep-formula-help"></label>
          <label>Reference formula <input type="text" data-field="reference-formula" maxlength="512" spellcheck="false" placeholder="Optional"></label>
          <label>Absolute tolerance <input type="text" data-field="tolerance" maxlength="64" spellcheck="false" placeholder="Optional"></label>
          <button type="submit">Plot formula</button>
          <p id="q1lens-sweep-formula-help" class="def-muted">Use observed final registers or DEF inputs. Trigonometric functions use radians. With a tolerance, a case passes when |error| ≤ tolerance.</p>
          <p data-field="formula-status" role="status"></p>
        </form>
        <div data-field="comparison" hidden></div>
        <div data-field="sweep-results" class="def-plots"></div><div data-field="table"></div>
      </section>
      <section class="def-card" data-field="trace" hidden>
        <h3 data-field="trace-heading" tabindex="-1">Execution trace</h3><div data-field="result"></div>
        <p data-field="trace-diags"></p>
        <div data-field="steps" hidden><p class="def-muted">Executed instructions · block width is independent of time</p>
        <div data-field="blocks" class="def-blocks" aria-label="Executed instructions"></div>
        <p class="def-toolbar"><button data-action="prev">Previous instruction</button><button data-action="next">Next instruction</button>
          <span data-field="position"></span><button data-action="instruction-source">Open instruction source</button></p>
        <div data-field="instruction" class="def-instruction"></div></div>
      </section></div>`;
    layoutGroup.parentElement.after(panel);
    const field = (name) => panel.querySelector(`[data-field="${name}"]`);
    const button = (name) => panel.querySelector(`[data-action="${name}"]`);
    const send = (action, data = {}) => vscode.postMessage({ type: "defRun", project, action, ...data });
    const state = {};
    const model = root.q1lensHardwareState;
    const seqField = field("seq"), timeField = field("observe-time"), modeField = field("mode"), metricField = field("metric");
    const registerFields = [...panel.querySelectorAll("input[data-register]")];
    const observedRegisters = () => registerFields.filter((input) => input.checked).map((input) => input.dataset.register);
    const outputField = field("sweep-output"), sampleField = field("sample-count");
    let edits = saved.edits || {}, selected = saved.sequencer, plan, planError, planTimer, token = 0, page = 0;
    let traceId, index, instructions = [], used = new Set(), pendingAt = [], position = saved.position || 0;
    let showAllRegisters = false;
    let hasRegisterChoice = Array.isArray(saved.registers) || typeof saved.register === "string";
    const savedRegisters = new Set(saved.registers ?? (saved.register ? [saved.register] : []));
    for (const input of registerFields) input.checked = savedRegisters.has(input.dataset.register);
    timeField.checked = saved.observeTime === true;
    modeField.value = saved.mode || "signed";
    let selectedMetric = saved.metric;
    let activeFormula = typeof saved.formula === "string" ? saved.formula : "";
    let activeReference = typeof saved.referenceFormula === "string" ? saved.referenceFormula : "";
    let activeTolerance = Number.isFinite(saved.tolerance) ? saved.tolerance : null;
    let derived = null;
    const formulaField = field("formula"), formulaStatus = field("formula-status");
    const referenceField = field("reference-formula"), toleranceField = field("tolerance");
    formulaField.value = activeFormula;
    referenceField.value = activeReference;
    toleranceField.value = activeTolerance ?? "";
    outputField.value = saved.sweepOutput === "statistics" ? "statistics" : "cases";
    panel.hidden = !saved.open;
    toggle.setAttribute("aria-expanded", String(!panel.hidden));
    const sequencer = () => state.info?.sequencers.find((item) => item.id === selected);
    const editKey = () => JSON.stringify([selected, sequencer()?.uri]);
    const currentEdits = () => edits[editKey()] || {};
    const save = () => persist({ open: !panel.hidden, sequencer: selected,
      ...(hasRegisterChoice ? { registers: observedRegisters() } : {}), observeTime: timeField.checked,
      mode: modeField.value, metric: selectedMetric, formula: activeFormula, referenceFormula: activeReference,
      tolerance: activeTolerance, sweepOutput: outputField.value, edits, traceId, position });
    const statisticsOnly = () => outputField.value === "statistics";
    const target = () => ({ revision: state.info.revision, sequencer: selected, uri: sequencer().uri });
    const inputNodes = () => [...field("defs").querySelectorAll("input[data-def]")];
    const runInputs = () => ({ ...target(), registers: observedRegisters(), observe_time: timeField.checked,
      overrides: Object.fromEntries(inputNodes().map((input) => [input.dataset.def, input.value])) });
    const sweepInputs = () => ({ ...runInputs(), axes: readAxes(), statistics_only: statisticsOnly() });

    function renderControls() {
      const available = Boolean(sequencer()) && !unsaved && !state.busy;
      const observedCount = observedRegisters().length;
      const hasObservations = observedCount > 0 || timeField.checked;
      const hiddenCount = registerFields.filter((input) => input.checked && input.parentElement.hidden).length;
      field("observed-count").textContent = `· ${observedCount} selected${hiddenCount ? ` · ${hiddenCount} hidden` : ""}`;
      field("observation-required").hidden = hasObservations;
      button("run").disabled = !available;
      button("reset").disabled = !sequencer()?.defs.length;
      button("info").disabled = Boolean(unsaved || state.busy);
      const stop = button("cancel");
      const sweeping = state.busy === "sweep", estimating = state.busy === "estimate";
      const stopSlot = field(sweeping && state.sweep?.status === "running" ? "progress-actions" : "setup-actions");
      if (stop.parentElement !== stopSlot) stopSlot.append(stop);
      stop.hidden = !sweeping && !estimating;
      stop.textContent = estimating ? "Stop estimate" : state.sampling ? "Stop sampling" : "Stop sweep";
      stop.disabled = state.message?.startsWith("Stopping") || false;
      outputField.disabled = sweeping || estimating;
      button("sweep").disabled = !available || !plan?.allowed || !hasObservations;
      const sampleLimit = plan?.sample_limit ?? plan?.limit ?? (statisticsOnly() ? 1_000_000 : 25_000);
      sampleField.max = sampleLimit;
      sampleField.disabled = sweeping || estimating;
      const validSampleCount = Number.isSafeInteger(sampleField.valueAsNumber) && sampleField.validity.valid;
      button("sample").disabled = !available || !plan || !validSampleCount || !hasObservations;
      button("estimate").disabled = button("sample").disabled;
      field("sample-help").textContent = validSampleCount ? "Draws from the selected ranges. The same combination can appear more than once." :
        `Enter a whole sample count from 1 to ${sampleLimit.toLocaleString("en-US")}.`;
      const estimate = state.estimate;
      const matchingEstimate = plan && estimate && sequencer() && !unsaved &&
        JSON.stringify(estimate.inputs) === JSON.stringify(sweepInputs());
      field("estimate").textContent = estimating ? "Measuring sample time…" : matchingEstimate && validSampleCount ?
        `Estimated time: about ${estimatedDuration(estimate.setupMs + estimate.sampleMs * sampleField.valueAsNumber)} (measured on ${estimate.count} samples).` : "";
      const error = planError || (plan && !plan.allowed ? `Maximum ${(plan.limit ?? 4096).toLocaleString("en-US")} combinations.` : "");
      field("plan").textContent = [plan && `${plan.axes.map((axis) => `${axis.name}: ${axis.count}`).join(" × ")} · ${plan.total} combinations`, error].filter(Boolean).join(" · ");
      field("message").textContent = unsaved ? "Save the project and Q1ASM sources before calculating execution." :
        state.message || ({ info: "Reading project DEFs…", run: "Running from the start…", sweep: "Calculating sweep…", case: "Reproducing the selected case…" })[state.busy] || "";
    }

    function renderRegisterChoices() {
      const seq = sequencer();
      const related = new Set(seq?.registers);
      for (const input of registerFields) {
        const name = input.dataset.register;
        if (!hasRegisterChoice) input.checked = name === seq?.registers?.[0];
        input.parentElement.hidden = !seq || (!showAllRegisters && !related.has(name));
      }
      field("no-registers").hidden = !seq || showAllRegisters || related.size > 0;
      const toggle = button("toggle-registers");
      toggle.hidden = !seq || related.size === registerFields.length;
      toggle.textContent = showAllRegisters ? "Show referenced registers" : "Show all registers";
      toggle.setAttribute("aria-expanded", String(showAllRegisters));
    }

    function updateEdited() {
      const trace = state.trace && state.trace.sequencer === selected && !state.trace.stale ? state.trace : null;
      let edited = false;
      for (const input of inputNodes()) {
        const name = input.dataset.def, declaration = input.dataset.default;
        const executed = trace && Object.hasOwn(trace.case.defs, name) ? trace.case.defs[name] : undefined;
        const lastLiteral = trace?.literals && Object.hasOwn(trace.literals, name) ? trace.literals[name] :
          executed === sequencer()?.defs.find((def) => def.name === name)?.value ? declaration : executed;
        input.closest("tr").querySelector("[data-executed]").textContent = executed ?? "—";
        edited ||= input.value.trim() !== (lastLiteral ?? declaration).trim();
      }
      field("edited").textContent = edited ? "Changes have not been run" : "";
    }

    function readAxes() {
      return [...field("axes").querySelectorAll("[data-axis]")].flatMap((row) => {
        const name = row.querySelector("select").value;
        return name ? [{ name, ...Object.fromEntries([...row.querySelectorAll("input")].map((input) => [input.name, input.value])) }] : [];
      });
    }

    function requestPlan() {
      clearTimeout(planTimer);
      const requestToken = ++token;
      plan = undefined;
      planError = undefined;
      renderControls();
      if (!sequencer()?.defs.length || unsaved || state.busy) return;
      planTimer = setTimeout(() => send("plan", { ...target(), token: requestToken, axes: readAxes(), statistics_only: statisticsOnly() }), 120);
    }

    function renderInputs() {
      const items = state.info?.sequencers || [];
      if (!items.some((item) => item.id === selected)) selected = items[0]?.id || selected;
      seqField.innerHTML = items.map((item) => `<option value="${esc(item.id)}">${esc(item.id)}</option>`).join("");
      seqField.value = selected || "";
      seqField.disabled = items.length < 2;
      renderRegisterChoices();
      const seq = sequencer(), defs = seq?.defs || [];
      const source = field("source");
      source.title = seq?.file || "";
      source.textContent = source.title.split(/[\\/]/).at(-1);
      field("defs").innerHTML = defs.length ? `<table aria-label="DEF inputs"><thead><tr><th scope="col">DEF</th><th scope="col">Default</th><th scope="col">Run input</th><th scope="col">Last run</th></tr></thead><tbody>
        ${defs.map((def, i) => `<tr><th scope="row"><code>${esc(def.name)}</code><button data-location="${i}">Line ${def.line}</button></th><td><code>${esc(def.literal)}</code></td><td><div class="def-input-cell"><input type="text" aria-label="${esc(def.name)} DEF value" spellcheck="false" data-def="${esc(def.name)}" data-default="${esc(def.literal)}" value="${esc(Object.hasOwn(currentEdits(), def.name) ? currentEdits()[def.name] : def.literal)}"><button data-reset="${i}" aria-label="Reset ${esc(def.name)} DEF">Reset</button></div></td>
        <td data-executed>—</td></tr>`).join("")}</tbody></table>` :
        `<p>${seq ? "No editable numeric DEFs." : "Load the project’s numeric DEFs to begin."}</p>`;
      field("axes").innerHTML = defs.length ? [0, 1].map((i) => `<div class="def-axis" data-axis="${i}"><label>Axis ${i + 1} <select aria-label="Sweep axis ${i + 1}">
        ${i ? '<option value="">None</option>' : ""}${defs.map((def) => `<option>${esc(def.name)}</option>`).join("")}</select></label>
        ${[["min", "Min"], ["max", "Max"], ["step", "Step"]].map(([name, label]) => `<label>${label} <input type="text" name="${name}" aria-label="Axis ${i + 1} ${name}" value="${i ? "" : name === "step" ? 1 : esc(defs[0].literal)}" spellcheck="false" ${i ? "disabled" : ""}></label>`).join("")}</div>`).join("") : '<p class="def-muted">Select a sequencer with numeric DEFs to set sweep ranges.</p>';
      const second = field("axes").querySelector('[data-axis="1"]');
      if (second) second.hidden = defs.length < 2;
      updateEdited();
      requestPlan();
    }

    function issueTiming({ event, time, end }) {
      const range = `${time}–${end} ns`;
      const { observation_cutoff_ns: cutoff, planned_q1_end_ns: planned } = event.meta || {};
      return cutoff === end && time <= cutoff && Number.isFinite(planned) && cutoff < planned
        ? `${range} observed · planned end ${planned} ns` : range;
    }

    function renderStep(scroll = false) {
      const step = instructions[position];
      field("steps").hidden = !step;
      if (!step) return;
      const { event, time, writes } = step;
      const blocks = field("blocks");
      blocks.querySelector('[aria-pressed="true"]')?.setAttribute("aria-pressed", "false");
      const selectedBlock = blocks.children[position];
      selectedBlock.setAttribute("aria-pressed", "true");
      if (scroll) selectedBlock.scrollIntoView({ block: "nearest", inline: "nearest" });
      button("prev").disabled = position === 0;
      button("next").disabled = position + 1 === instructions.length;
      button("instruction-source").disabled = Boolean(state.trace.stale);
      field("position").textContent = `${position + 1} / ${instructions.length} · ${issueTiming(step)} · line ${event.source?.line ?? "—"}`;
      const display = (value) => {
        const number = model.numericValue(value);
        return number === undefined ? model.displayValue(value) : String(interpret(number >>> 0, modeField.value));
      };
      const meta = event.meta || {};
      const values = (entries) => entries.length ? entries.map(([name, value]) => `<span><code>${esc(name)}</code> ${esc(display(value))}</span>`).join(" ") : "—";
      const written = [...writes, ...(pendingAt[position]?.issued || [])];
      const writeList = (items) => items.length ? items.map((item) => {
        const availability = item.pending
          ? (Number.isFinite(item.time) ? `pending · planned ready at ${item.time} ns` : "pending · availability unknown")
          : (item.time == null ? "availability outside trace" : `readable at ${item.time} ns`);
        return `<span><code>${esc(item.register)}</code> ${esc(display(item.value))} · ${esc(availability)}</span>`;
      }).join(" ") : "—";
      const readable = [...used].sort((a, b) => Number(a.slice(1)) - Number(b.slice(1))).map((register) =>
        [register, meta.register_snapshot?.[register] ?? model.registerAt(index, event.sequencer_id, register, time)?.value]);
      field("instruction").innerHTML = `<div><strong>Instruction</strong><p><code>${esc(event.source?.raw || meta.op)}</code></p></div>
        <div><strong>Values read</strong><p>${values(Object.entries(meta.register_reads || {}))}</p></div>
        <div><strong>Computed writes</strong><p>${writeList(written)}</p></div>
        <div><strong>Pending at instruction start</strong><p>${writeList(pendingAt[position]?.before || [])}</p></div>
        <div><strong>Registers readable at start</strong><p>${values(readable)}</p></div>`;
      save();
    }

    function renderTrace(focus = false) {
      const trace = state.trace;
      field("trace").hidden = !trace;
      if (!trace) return;
      const row = trace.case;
      field("trace-heading").textContent = trace.sweepId ? `Execution trace · sweep case ${row.id}` : "Execution trace";
      const complete = row.status === "completed" || row.engine_completed;
      field("result").innerHTML = `<dl class="def-metrics">
        ${trace.registers.map((name) => {
          const word = row.words[name];
          return `<div><dt>${esc(name)} · ${modeField.value} 32-bit</dt><dd data-result="word" data-observed="${esc(name)}">${interpret(word, modeField.value) ?? "—"}</dd>${word == null ? "" : `<dd class="def-muted"><code>${hex(word)}</code></dd>`}</div>`;
        }).join("")}
        ${trace.observeTime ? `<div><dt>${complete ? "Full" : "Recorded"} Q1 time</dt><dd data-result="time">${row.q1_ns == null ? "—" : `${row.q1_ns} ns`}</dd></div>` : ""}
        <div><dt>Status</dt><dd>${esc(statusName(row.status))}</dd>${trace.stale ? '<dd class="def-muted">Stale snapshot</dd>' : ""}</div>
      </dl><p class="def-muted">${esc([trace.sequencer, trace.mode, `${trace.maxSteps} instruction limit`, assignments(row.defs)].filter(Boolean).join(" · "))}</p>`;
      field("trace-diags").textContent = diagnostics(row);
      if (traceId !== trace.id) {
        position = saved.traceId === trace.id ? saved.position || 0 : 0;
        traceId = trace.id;
        index = model.createIndex(trace.ir);
        instructions = model.executionTrace(index, trace.sequencer).instructions;
        position = Math.max(0, Math.min(instructions.length - 1, position));
        used = new Set(trace.registers);
        const finalWrites = new Map();
        for (const write of trace.pending ?? []) {
          const list = finalWrites.get(write.event_id) || [];
          list.push({ ...write, time: Number.isFinite(write.ready_ns) ? write.ready_ns : undefined, pending: true });
          finalWrites.set(write.event_id, list);
        }
        let pending = [];
        pendingAt = instructions.map(({ event, time, writes }) => {
          const before = pending.filter((item) => item.time == null || item.time > time);
          const issued = finalWrites.get(event.id) || [];
          pending = [...before, ...writes, ...issued];
          for (const register of [...Object.keys(event.meta.register_reads || {}), ...writes.map((item) => item.register), ...issued.map((item) => item.register)]) used.add(register);
          return { before, issued };
        });
        field("blocks").innerHTML = instructions.map((step, i) => {
          const { event } = step;
          const op = root.q1asmInstructionColors.eventCommandToken(event);
          return `<button data-step="${i}" aria-pressed="false" style="--def-command:${root.q1asmInstructionColors.commandAccentColor(op)}" title="${esc(`#${i + 1} · ${event.source?.raw || op} · ${issueTiming(step)}`)}">${esc(op)}</button>`;
        }).join("");
      }
      renderStep();
      updateEdited();
      if (focus) field("trace-heading").focus();
    }

    function renderProgress() {
      const sweep = state.sweep;
      field("sweep").hidden = !sweep;
      if (!sweep) return;
      const random = sweep.plan.sampling === "random";
      field("sweep-heading").textContent = random ? "Random sample results" : "Sweep results";
      const count = (status) => sweep.statisticsOnly ? sweep.summary?.statuses[status] ?? 0 : sweep.rows.filter((row) => row.status === status).length;
      const ended = sweep.summary?.count ?? sweep.rows.length, planned = Number(sweep.plan.total), remaining = planned - ended;
      field("bar").max = planned;
      field("bar").value = ended;
      field("bar").hidden = sweep.status === "completed";
      const valid = count("completed"), completed = sweep.summary?.engine_completed ?? sweep.rows.filter((row) => row.engine_completed).length;
      const counts = [
        ["Failed", count("error")], ["Blocked", count("blocked")], ["Limit reached", count("step_limit")],
        ["Output undetermined", count("undetermined")], [sweep.incompleteBatch ? "Unreported" : "Unexecuted", remaining],
      ].filter(([, n]) => n > 0);
      if (completed !== valid) counts.unshift(["Engine completed", completed]);
      if (valid !== planned) counts.unshift(["Cases with readable output", valid]);
      const observed = sweep.registers.length > 6 ? [`${sweep.registers.length} registers`] : sweep.registers;
      field("progress").innerHTML = `<div class="def-toolbar">
        <strong class="def-status" data-status="${esc(sweep.status)}">${esc(statusName(sweep.status))}</strong>
        <span>${esc([sweep.sequencer, ...observed, ...(sweep.observeTime ? ["Q1 time"] : [])].join(" · "))}</span>
        <span data-cases-reported><strong>${ended} / ${planned}</strong> ${random ? "samples" : "cases"} reported</span>
        </div>${counts.length ? `<ul class="def-counts">${counts.map(([label, n]) => `<li>${label} <strong>${n}</strong></li>`).join("")}</ul>` : ""}
        ${sweep.incompleteBatch ? '<p class="def-muted">Last batch interrupted.</p>' : ""}`;
    }

    function renderTable() {
      const sweep = state.sweep;
      if (!sweep || sweep.status === "running" || sweep.statisticsOnly) { field("table").innerHTML = ""; return; }
      const pages = Math.max(1, Math.ceil(sweep.rows.length / 50));
      page = Math.min(page, pages - 1);
      const disabled = sweep.status === "stale" || state.busy;
      const mode = modeField.value;
      const comparing = Boolean(activeReference && derived);
      field("table").innerHTML = `<div class="def-heading"><h4>Cases</h4><div class="def-toolbar">
        <button data-page="-1" ${page ? "" : "disabled"}>Previous results</button><span>Page ${page + 1} of ${pages}</span>
        <button data-page="1" ${page + 1 < pages ? "" : "disabled"}>Next results</button></div></div>
        <div class="def-table-scroll def-case-table" tabindex="0" role="region" aria-label="Sweep case table"><table aria-label="Sweep cases"><thead><tr><th scope="col">Case</th><th scope="col">Effective DEFs</th><th scope="col">Status</th>
        ${comparing ? '<th scope="col">Actual</th><th scope="col">Reference</th><th scope="col">Error</th><th scope="col">Verdict</th>' : ""}
        ${sweep.registers.map((name) => `<th scope="col">${esc(name)} · ${mode} / raw</th>`).join("")}
        ${sweep.observeTime ? '<th scope="col">Q1 time</th>' : ""}<th scope="col">Diagnostics</th></tr></thead><tbody>
        ${sweep.rows.slice(page * 50, (page + 1) * 50).map((row) => `<tr><th scope="row"><button data-case="${row.id}" ${disabled ? "disabled" : ""}>Case ${row.id}</button></th>
        <td><code>${esc(assignments(row.defs))}</code></td><td>${esc(statusName(row.status))}</td>
        ${comparing ? comparisonCells(derived.get(row.id)) : ""}
        ${sweep.registers.map((name) => {
          const word = row.words[name];
          return `<td class="def-observed-value">${word == null ? "—" : `${interpret(word, mode)} <code class="def-muted">${hex(word)}</code>`}</td>`;
        }).join("")}
        ${sweep.observeTime ? `<td class="def-observed-value">${row.q1_ns == null ? "—" : `${row.q1_ns} ns`}</td>` : ""}
        <td>${esc(diagnostics(row)) || "—"}</td></tr>`).join("")}</tbody></table></div>`;
    }

    function comparisonCells(result) {
      const { formula, reference, error, verdict } = result;
      return [formula, reference, error].map((value) => `<td class="def-observed-value">${value == null ? "—" : format(value)}</td>`).join("") +
        `<td data-verdict="${verdict}">${verdictNames[verdict]}</td>`;
    }

    function renderComparison() {
      const summary = field("comparison");
      summary.hidden = !activeReference || !derived;
      if (summary.hidden) return;
      const counts = { pass: 0, fail: 0, compared: 0, unavailable: 0 };
      let max = null;
      for (const { verdict, error } of derived.values()) {
        counts[verdict] += 1;
        if (error !== null) max = Math.max(max ?? 0, Math.abs(error));
      }
      summary.innerHTML = `<h4>Reference comparison</h4>
        <p class="def-muted"><code>${esc(activeFormula)}</code> versus <code>${esc(activeReference)}</code></p>
        <ul class="def-counts">
          ${activeTolerance === null ? `<li>Compared <strong data-comparison="compared">${counts.compared}</strong></li>` : `
            <li>Pass <strong data-comparison="pass">${counts.pass}</strong></li>
            <li>Fail <strong data-comparison="fail">${counts.fail}</strong></li>
            <li>Absolute tolerance <strong data-comparison="tolerance">${format(activeTolerance)}</strong></li>`}
          <li>Unavailable <strong data-comparison="unavailable">${counts.unavailable}</strong></li>
          <li>Max |error| <strong data-comparison="max-error">${max === null ? "—" : format(max)}</strong></li>
        </ul>`;
    }

    function metricSummary(observations, mode) {
      const metrics = [["count", "Valid samples"], ["min", "Min"], ["max", "Max"], ["mean", "Mean"], ["deviation", "Population SD"]];
      return `<h4>Observation statistics</h4><div class="def-table-scroll def-statistics-table" role="region" aria-label="Observation statistics" tabindex="0">
        <table><thead><tr><th scope="col">Metric</th>${metrics.map(([, label]) => `<th scope="col">${label}</th>`).join("")}</tr></thead><tbody>
        ${[...observations].map(([name, stats]) => `<tr ${name === "time" ? "data-time-statistics" : `data-statistics="${esc(name)}"`}>
          <th scope="row">${name === "time" ? "Q1 time (ns)" : `${esc(name)} <span class="def-muted">${mode} 32-bit</span>`}</th>
          ${metrics.map(([key]) => `<td data-stat="${key}">${stats ? format(stats[key]) : key === "count" ? 0 : "—"}</td>`).join("")}</tr>`).join("")}
        </tbody></table></div>`;
    }

    function renderSweep() {
      renderProgress();
      const sweep = state.sweep, output = field("sweep-results");
      const summary = field("register-summary");
      derived = null;
      field("comparison").hidden = true;
      field("plot-controls").hidden = !sweep || sweep.status === "running" || sweep.statisticsOnly;
      field("formula-form").hidden = field("plot-controls").hidden;
      if (!sweep || sweep.status === "running") { summary.innerHTML = ""; output.innerHTML = ""; renderTable(); return; }
      const mode = modeField.value;
      const metrics = [...sweep.registers, ...(sweep.observeTime ? ["time"] : [])];
      const observations = new Map(metrics.map((metric) => [metric, sweep.statisticsOnly ?
        (metric === "time" ? sweep.summary?.time : sweep.summary?.registers?.[metric]?.[mode]) : statistics(sweep.rows, mode, metric)]));
      const swept = new Set(sweep.plan.axes.map((axis) => axis.name));
      const fixed = assignments(Object.fromEntries(Object.entries(sweep.defs).filter(([name]) => !swept.has(name))));
      summary.innerHTML = `<p class="def-muted">${sweep.statisticsOnly ? "Statistics only · " : ""}${esc(sweep.mode)} · ${sweep.maxSteps} instruction limit${fixed ? ` · Fixed DEFs ${esc(fixed)}` : ""}</p>
        ${metricSummary(observations, mode)}`;
      if (sweep.statisticsOnly) { output.innerHTML = ""; renderTable(); return; }
      formulaStatus.textContent = "";
      if (activeFormula) {
        try {
          const evaluate = compileFormula(activeFormula, sweep);
          const evaluateReference = activeReference ? compileFormula(activeReference, sweep) : null;
          derived = new Map(sweep.rows.map((row) => {
            const actual = evaluate(row, mode);
            return [row.id, evaluateReference ? compareValues(actual, evaluateReference(row, mode), activeTolerance) : { formula: actual }];
          }));
          if (!evaluateReference) {
            const count = [...derived.values()].filter((result) => result.formula !== null).length;
            if (count < sweep.rows.length) formulaStatus.textContent = `${count} of ${sweep.rows.length} cases produced a finite formula value.`;
          }
        } catch (error) {
          formulaStatus.textContent = error.message;
        }
      }
      const plotMetrics = derived ? [...metrics, ...(activeReference ? formulaMetrics : ["formula"])] : metrics;
      metricField.innerHTML = plotMetrics.map((metric) => `<option value="${esc(metric)}">${esc(metricNames.get(metric) ?? metric)}</option>`).join("");
      const metric = plotMetrics.includes(selectedMetric) ? selectedMetric : plotMetrics[0];
      metricField.value = metric;
      renderComparison();
      renderPlots(sweep, observations.get(metric));
      renderTable();
    }

    function renderPlots(sweep, plotStats) {
      const metric = metricField.value, time = metric === "time";
      const computed = formulaMetrics.includes(metric) ? derived : null;
      const quantity = metricNames.get(metric) ?? metric;
      const summarizeFormula = (name) => summarize([...computed.values()].map((result) => result[name]), true);
      if (computed) plotStats = summarizeFormula(metric);
      const paired = Boolean(computed && activeReference && metric !== "reference");
      const samples = sweep.plan.sampling === "random" ? sampledInputs(sweep) : null;
      const charts = (paired ? ["formula", "error"] : [metric]).map((name) => {
        const caption = name === "formula" ? `Actual value <code>${esc(activeFormula)}</code>` :
          name === "reference" ? `Reference <code>${esc(activeReference)}</code>` :
            name === "error" ? "Error (actual − reference)" : `Inputs and ${quantity}`;
        const stats = name === metric ? plotStats : summarizeFormula(name);
        return `<figure class="def-chart"><figcaption>${caption}</figcaption>${plot(sweep, stats, modeField.value, name, computed, samples)}</figure>`;
      });
      const maxFrequency = plotStats ? Math.max(...plotStats.bins.map((bin) => bin.count)) : 0;
      const histogram = plotStats ? `<ul class="def-histogram">${plotStats.bins.map((bin) => `<li><span>${bin.min === bin.max ? format(bin.min) : `${format(bin.min)} to ${format(bin.max)}`}${time ? " ns" : ""}</span> <meter max="${maxFrequency}" value="${bin.count}" aria-label="${quantity} frequency"></meter> ${bin.count}</li>`).join("")}</ul>` : "";
      const output = field("sweep-results");
      output.classList.toggle("def-paired-plots", paired);
      output.innerHTML = `${charts.join("")}
        <section class="def-chart"><h4>${quantity} frequency</h4>${histogram || '<p class="def-muted">No values</p>'}</section>`;
    }

    field("formula-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const sweep = state.sweep;
      if (!sweep || sweep.statisticsOnly || sweep.status === "running") return;
      const formula = formulaField.value.trim(), reference = referenceField.value.trim();
      let tolerance;
      try {
        compileFormula(formula, sweep);
        if (reference) compileFormula(reference, sweep);
        tolerance = parseTolerance(toleranceField.value);
        if (tolerance !== null && !reference) throw new Error("Enter a reference formula to apply an absolute tolerance.");
      } catch (error) {
        formulaStatus.textContent = error.message;
        return;
      }
      activeFormula = formula;
      activeReference = reference;
      activeTolerance = tolerance;
      selectedMetric = reference ? "error" : "formula";
      renderSweep();
      save();
    });

    function openCase(id) {
      if (!state.sweep || state.busy || state.sweep.status === "stale" || unsaved) return;
      send("case", { sweepId: state.sweep.id, caseId: Number(id) });
    }

    function navigate(source) {
      if (source?.file) vscode.postMessage({ type: "sourceClick", file: source.file, line: source.line, column: source.column || 1, preserveHardwareCursor: true });
    }

    function loadInfo() {
      if (!panel.hidden && !state.info && !state.busy && !unsaved && !state.message) send("info");
    }
    toggle.addEventListener("click", () => {
      panel.hidden = !panel.hidden;
      toggle.setAttribute("aria-expanded", String(!panel.hidden));
      save();
      loadInfo();
    });
    panel.addEventListener("input", (event) => {
      if (event.target.matches("input[data-def]")) {
        const { def, default: declaration } = event.target.dataset;
        const values = { ...currentEdits(), [def]: event.target.value };
        if (values[def] === declaration) delete values[def];
        edits = { ...edits, [editKey()]: values };
        updateEdited(); renderControls(); save();
      } else if (event.target.closest("[data-axis]")) requestPlan();
      else if (event.target === sampleField) renderControls();
    });
    panel.addEventListener("change", (event) => {
      if (event.target === seqField) { selected = seqField.value; showAllRegisters = false; renderInputs(); }
      if (event.target === outputField) requestPlan();
      if (event.target === modeField) { renderTrace(); renderSweep(); }
      if (event.target === metricField) {
        selectedMetric = metricField.value;
        const stats = formulaMetrics.includes(selectedMetric) ? null : statistics(state.sweep.rows, modeField.value, selectedMetric);
        renderPlots(state.sweep, stats);
      }
      if (event.target.matches("input[data-register]")) { hasRegisterChoice = true; renderControls(); }
      if (event.target === timeField) renderControls();
      if (event.target.matches("[data-axis] select")) {
        const row = event.target.closest("[data-axis]");
        const def = sequencer().defs.find((item) => item.name === event.target.value);
        for (const input of row.querySelectorAll("input")) {
          input.disabled = !def;
          input.value = input.name === "step" ? "1" : def?.literal ?? "";
        }
        requestPlan();
      }
      save();
    });
    panel.addEventListener("click", (event) => {
      const node = event.target.closest("button, [data-case]");
      if (!node) return;
      const { action, location, reset, step, case: id, page: offset } = node.dataset;
      if (action === "info" || action === "cancel") send(action);
      if (action === "toggle-registers") {
        showAllRegisters = !showAllRegisters;
        renderRegisterChoices();
        renderControls();
      }
      if (["run", "sweep", "sample", "estimate"].includes(action) && sequencer()) {
        send(action === "sample" ? "sweep" : action, { ...(action === "run" ? runInputs() : sweepInputs()),
          ...(action === "sample" ? { sample_count: sampleField.valueAsNumber } : {}) });
      }
      if (action === "reset" || reset !== undefined) {
        const inputs = inputNodes();
        for (const input of action === "reset" ? inputs : [inputs[Number(reset)]]) {
          input.value = input.dataset.default;
          input.dispatchEvent(new Event("input", { bubbles: true }));
        }
      }
      if (location !== undefined) navigate({ ...sequencer().defs[Number(location)], file: sequencer().file });
      if (step !== undefined || action === "prev" || action === "next") {
        position = step === undefined ? position + (action === "prev" ? -1 : 1) : Number(step);
        renderStep(true);
      }
      if (action === "instruction-source") navigate(instructions[position]?.event.source);
      if (id !== undefined) openCase(id);
      if (offset !== undefined) { page += Number(offset); renderTable(); }
    });
    panel.addEventListener("keydown", (event) => {
      const point = event.target.closest(".def-plot-point");
      if (!point) return;
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openCase(point.dataset.case); }
      if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
        event.preventDefault();
        const next = ["ArrowLeft", "ArrowUp"].includes(event.key) ? point.previousElementSibling : point.nextElementSibling;
        if (next?.matches(".def-plot-point")) { point.setAttribute("tabindex", "-1"); next.setAttribute("tabindex", "0"); next.focus(); }
      }
    });
    window.addEventListener("message", ({ data }) => {
      if (data?.type === "setUnsavedChanges") { unsaved = data.hasUnsavedChanges; renderControls(); return; }
      if (data?.project !== project) return;
      if (data.type === "defRunState") {
        const oldInfo = state.info, wasBusy = state.busy, previousTrace = state.trace?.id;
        if (data.patch.sweep?.id && state.sweep?.id !== data.patch.sweep.id) page = 0;
        Object.assign(state, data.patch);
        if (oldInfo !== state.info) renderInputs();
        else if (wasBusy && !state.busy && !plan) requestPlan();
        if (Object.hasOwn(data.patch, "trace")) renderTrace(["run", "case"].includes(wasBusy) && previousTrace !== state.trace?.id);
        if (Object.hasOwn(data.patch, "sweep")) renderSweep();
        else if (wasBusy !== state.busy && state.sweep?.status !== "running") renderTable();
        renderControls();
      } else if (data.type === "defSweepRows" && state.sweep?.id === data.sweepId) {
        const lastId = state.sweep.rows.at(-1)?.id || 0;
        state.sweep.rows.push(...data.rows.filter((row) => row.id > lastId));
        renderProgress();
      } else if (data.type === "defSweepSummary" && state.sweep?.id === data.sweepId) {
        state.sweep.summary = data.summary;
        renderProgress();
      } else if (data.type === "defSweepPlan" && data.token === token) {
        plan = data.plan; planError = data.error; renderControls();
      }
    });
    renderInputs();
    renderControls();
    if (!panel.hidden) setTimeout(loadInfo, 0);
  }

  const api = { mount, statistics, interpret, compileFormula, summarize, parseTolerance, compareValues };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.q1lensDefRuns = api;
})(typeof window !== "undefined" ? window : undefined);
