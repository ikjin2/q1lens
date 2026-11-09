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

  function plot(sweep, stats, mode, metric, formulaValues) {
    const [xAxis, yAxis] = sweep.plan.axes;
    const columns = Number(xAxis.count), rows = yAxis ? Number(yAxis.count) : 1;
    const left = yAxis ? 8 : formulaValues ? 126 : 96, top = 20;
    const width = yAxis ? Math.max(430, columns * 8) : 340, height = yAxis ? Math.max(180, rows * 8) : 180;
    const end = left + width, bottom = top + height;
    const xLabel = `${xAxis.name}: ${xAxis.min} → ${xAxis.last}`;
    const time = metric === "time";
    const quantity = time ? "Q1 time" : formulaValues ? "Formula" : metric;
    const valueLabel = time ? "Q1 time (ns)" : formulaValues ? "Formula value" : `${metric} (${mode})`;
    const yLabel = yAxis ? `${yAxis.name}: ${yAxis.min} → ${yAxis.last}` : valueLabel;
    const chartLabel = yAxis ? `Two-axis DEF heatmap by ${quantity}` : `DEF input versus ${quantity}`;
    const spread = stats && stats.max !== stats.min;
    const scale = (value) => stats ? fraction(value, stats.min, stats.max) : 0.5;
    const marks = [], missing = new Set();
    if (yAxis) marks.push(`<rect class="def-plot-unrun" x="${left}" y="${top}" width="${width}" height="${height}"/>`);
    for (const row of sweep.rows) {
      const i = row.id - 1, x = Math.floor(i / rows), y = i % rows;
      const value = formulaValues ? formulaValues.get(row.id) : plotValue(row, mode, metric);
      const missingStatus = value == null ? (row.status === "completed" ? "undetermined" : row.status) : "";
      if (missingStatus) missing.add(missingStatus);
      const title = `Case ${row.id} · ${assignments(row.defs)} · ${statusName(row.status)}${value == null ? "" : ` · ${quantity} ${format(value)}${time ? " ns" : ""}`}`;
      const attributes = `data-case="${row.id}" class="def-plot-point ${esc(missingStatus)}" role="button" tabindex="${i === 0 ? "0" : "-1"}" aria-label="${esc(title)}"`;
      const label = `<title>${esc(title)}</title>`;
      if (yAxis) {
        const w = width / columns, h = height / rows;
        const color = value == null ? "" : ` style="fill:hsl(209 80% ${18 + 60 * scale(value)}%)"`;
        marks.push(`<rect ${attributes}${color} x="${left + x * w}" y="${bottom - (y + 1) * h}" width="${w}" height="${h}">${label}</rect>`);
      } else {
        const cx = left + 5 + (columns === 1 ? 0.5 : x / (columns - 1)) * (width - 10);
        const cy = value == null ? bottom + 14 : bottom - 6 - scale(value) * (height - 12);
        marks.push(`<circle ${attributes} cx="${cx}" cy="${cy}" r="4">${label}</circle>`);
      }
    }
    if (!yAxis && sweep.rows.length < columns) {
      const x = left + (sweep.rows.length / columns) * width;
      marks.unshift(`<rect class="def-plot-unrun" x="${x}" y="${top}" width="${end - x}" height="${height}"/>`);
    }
    const tick = (value, y) => `<text x="${left - 6}" y="${y}" text-anchor="end">${format(value)}</text>`;
    const yTicks = !yAxis && stats ? (spread ? tick(stats.max, top + 8) + tick(stats.min, bottom) : tick(stats.min, top + height / 2 + 4)) : "";
    const colorRange = stats && (spread ? `${format(stats.min)} to ${format(stats.max)}` : format(stats.min));
    const minWidth = Math.max(230, yAxis ? left + columns * 8 : 0);
    const keys = [...missing].map((status) => `<span><i class="def-key ${esc(status)}"></i>${esc((time || formulaValues) && status === "undetermined" ? `${quantity} unavailable` : statusName(status))}</span>`);
    if (sweep.rows.length < columns * rows) keys.push('<span><i class="def-key def-plot-unrun"></i>Unexecuted</span>');
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
          <button data-action="sweep">Start sweep</button><button data-action="cancel" hidden>Stop sweep</button></div>
        </section>
      </div>
      <p data-field="message" role="status"></p>
      <section class="def-card" data-field="sweep" aria-labelledby="def-results-heading" hidden>
        <h3 id="def-results-heading">Sweep results</h3>
        <div data-field="progress" role="status"></div><progress data-field="bar" aria-label="Sweep progress"></progress>
        <div data-field="register-summary"></div>
        <div data-field="plot-controls" class="def-heading" hidden><h4>Plots</h4><label>Plot metric <select data-field="metric"></select></label></div>
        <form data-field="formula-form" class="def-formula" hidden>
          <label for="q1lens-sweep-formula">Formula</label>
          <input id="q1lens-sweep-formula" type="text" data-field="formula" maxlength="512" spellcheck="false" placeholder="atan2(R1, R0)" aria-describedby="q1lens-sweep-formula-help">
          <button type="submit">Plot formula</button>
          <p id="q1lens-sweep-formula-help" class="def-muted">Use observed final registers or DEF input names. Trigonometric functions use radians.</p>
          <p data-field="formula-status" role="status"></p>
        </form>
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
    const outputField = field("sweep-output");
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
    const formulaField = field("formula"), formulaStatus = field("formula-status");
    formulaField.value = activeFormula;
    outputField.value = saved.sweepOutput === "statistics" ? "statistics" : "cases";
    panel.hidden = !saved.open;
    toggle.setAttribute("aria-expanded", String(!panel.hidden));
    const sequencer = () => state.info?.sequencers.find((item) => item.id === selected);
    const editKey = () => JSON.stringify([selected, sequencer()?.uri]);
    const currentEdits = () => edits[editKey()] || {};
    const save = () => persist({ open: !panel.hidden, sequencer: selected,
      ...(hasRegisterChoice ? { registers: observedRegisters() } : {}), observeTime: timeField.checked,
      mode: modeField.value, metric: selectedMetric, formula: activeFormula, sweepOutput: outputField.value, edits, traceId, position });
    const statisticsOnly = () => outputField.value === "statistics";
    const target = () => ({ revision: state.info.revision, sequencer: selected, uri: sequencer().uri });
    const inputNodes = () => [...field("defs").querySelectorAll("input[data-def]")];

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
      button("cancel").hidden = state.busy !== "sweep";
      button("cancel").disabled = state.message?.startsWith("Stopping") || false;
      outputField.disabled = state.busy === "sweep";
      button("sweep").disabled = !available || !plan?.allowed || !hasObservations;
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

    function renderStep(scroll = false) {
      const step = instructions[position];
      field("steps").hidden = !step;
      if (!step) return;
      const { event, time, end, writes } = step;
      const blocks = field("blocks");
      blocks.querySelector('[aria-pressed="true"]')?.setAttribute("aria-pressed", "false");
      const selectedBlock = blocks.children[position];
      selectedBlock.setAttribute("aria-pressed", "true");
      if (scroll) selectedBlock.scrollIntoView({ block: "nearest", inline: "nearest" });
      button("prev").disabled = position === 0;
      button("next").disabled = position + 1 === instructions.length;
      button("instruction-source").disabled = Boolean(state.trace.stale);
      field("position").textContent = `${position + 1} / ${instructions.length} · ${time}–${end} ns · line ${event.source?.line ?? "—"}`;
      const display = (value) => {
        const number = model.numericValue(value);
        return number === undefined ? model.displayValue(value) : String(interpret(number >>> 0, modeField.value));
      };
      const meta = event.meta || {};
      const values = (entries) => entries.length ? entries.map(([name, value]) => `<span><code>${esc(name)}</code> ${esc(display(value))}</span>`).join(" ") : "—";
      const written = [...writes, ...(pendingAt[position]?.issued || [])];
      const writeList = (items) => items.length ? items.map((item) => `<span><code>${esc(item.register)}</code> ${esc(display(item.value))} · ${item.time == null ? "availability outside trace" : `readable at ${item.time} ns`}</span>`).join(" ") : "—";
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
        for (const write of trace.pending) {
          const list = finalWrites.get(write.event_id) || [];
          list.push({ ...write, time: write.ready_ns });
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
        field("blocks").innerHTML = instructions.map(({ event, time, end }, i) => {
          const op = root.q1asmInstructionColors.eventCommandToken(event);
          return `<button data-step="${i}" aria-pressed="false" style="--def-command:${root.q1asmInstructionColors.commandAccentColor(op)}" title="${esc(`#${i + 1} · ${event.source?.raw || op} · ${time}–${end} ns`)}">${esc(op)}</button>`;
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
        <span data-cases-reported><strong>${ended} / ${planned}</strong> cases reported</span>
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
      field("table").innerHTML = `<div class="def-heading"><h4>Cases</h4><div class="def-toolbar">
        <button data-page="-1" ${page ? "" : "disabled"}>Previous results</button><span>Page ${page + 1} of ${pages}</span>
        <button data-page="1" ${page + 1 < pages ? "" : "disabled"}>Next results</button></div></div>
        <div class="def-table-scroll def-case-table" tabindex="0" role="region" aria-label="Sweep case table"><table aria-label="Sweep cases"><thead><tr><th scope="col">Case</th><th scope="col">Effective DEFs</th><th scope="col">Status</th>
        ${sweep.registers.map((name) => `<th scope="col">${esc(name)} · ${mode} / raw</th>`).join("")}
        ${sweep.observeTime ? '<th scope="col">Q1 time</th>' : ""}<th scope="col">Diagnostics</th></tr></thead><tbody>
        ${sweep.rows.slice(page * 50, (page + 1) * 50).map((row) => `<tr><th scope="row"><button data-case="${row.id}" ${disabled ? "disabled" : ""}>Case ${row.id}</button></th>
        <td><code>${esc(assignments(row.defs))}</code></td><td>${esc(statusName(row.status))}</td>
        ${sweep.registers.map((name) => {
          const word = row.words[name];
          return `<td class="def-observed-value">${word == null ? "—" : `${interpret(word, mode)} <code class="def-muted">${hex(word)}</code>`}</td>`;
        }).join("")}
        ${sweep.observeTime ? `<td class="def-observed-value">${row.q1_ns == null ? "—" : `${row.q1_ns} ns`}</td>` : ""}
        <td>${esc(diagnostics(row)) || "—"}</td></tr>`).join("")}</tbody></table></div>`;
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
      const plotMetrics = activeFormula ? [...metrics, "formula"] : metrics;
      metricField.innerHTML = plotMetrics.map((metric) => `<option value="${esc(metric)}">${metric === "time" ? "Q1 time" : metric === "formula" ? "Formula" : esc(metric)}</option>`).join("");
      const metric = plotMetrics.includes(selectedMetric) ? selectedMetric : plotMetrics[0];
      metricField.value = metric;
      renderPlots(sweep, observations.get(metric));
      renderTable();
    }

    function renderPlots(sweep, plotStats) {
      const metric = metricField.value, time = metric === "time";
      const formula = metric === "formula";
      const quantity = time ? "Q1 time" : formula ? "Formula" : metric;
      let formulaValues;
      formulaStatus.textContent = "";
      if (formula) {
        try {
          const evaluate = compileFormula(activeFormula, sweep);
          formulaValues = new Map(sweep.rows.map((row) => [row.id, evaluate(row, modeField.value)]));
          plotStats = summarize([...formulaValues.values()], true);
          const count = plotStats?.count ?? 0;
          if (count < sweep.rows.length) formulaStatus.textContent = `${count} of ${sweep.rows.length} cases produced a finite formula value.`;
        } catch (error) {
          formulaStatus.textContent = error.message;
          field("sweep-results").innerHTML = "";
          return;
        }
      }
      const maxFrequency = plotStats ? Math.max(...plotStats.bins.map((bin) => bin.count)) : 0;
      const histogram = plotStats ? `<ul class="def-histogram">${plotStats.bins.map((bin) => `<li><span>${bin.min === bin.max ? format(bin.min) : `${format(bin.min)} to ${format(bin.max)}`}${time ? " ns" : ""}</span> <meter max="${maxFrequency}" value="${bin.count}" aria-label="${quantity} frequency"></meter> ${bin.count}</li>`).join("")}</ul>` : "";
      field("sweep-results").innerHTML = `<figure class="def-chart"><figcaption>${formula ? `Formula <code>${esc(activeFormula)}</code>` : `Inputs and ${quantity}`}</figcaption>${plot(sweep, plotStats, modeField.value, metric, formulaValues)}</figure>
        <section class="def-chart"><h4>${quantity} frequency</h4>${histogram || '<p class="def-muted">No values</p>'}</section>`;
    }

    field("formula-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const sweep = state.sweep;
      if (!sweep || sweep.statisticsOnly || sweep.status === "running") return;
      try {
        compileFormula(formulaField.value, sweep);
      } catch (error) {
        formulaStatus.textContent = error.message;
        return;
      }
      activeFormula = formulaField.value.trim();
      selectedMetric = "formula";
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
        updateEdited(); save();
      } else if (event.target.closest("[data-axis]")) requestPlan();
    });
    panel.addEventListener("change", (event) => {
      if (event.target === seqField) { selected = seqField.value; showAllRegisters = false; renderInputs(); }
      if (event.target === outputField) requestPlan();
      if (event.target === modeField) { renderTrace(); renderSweep(); }
      if (event.target === metricField) {
        selectedMetric = metricField.value;
        const stats = selectedMetric === "formula" ? null : statistics(state.sweep.rows, modeField.value, selectedMetric);
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
      if (["run", "sweep"].includes(action) && sequencer()) {
        send(action, { ...target(), registers: observedRegisters(), observe_time: timeField.checked,
          overrides: Object.fromEntries(inputNodes().map((input) => [input.dataset.def, input.value])),
          ...(action === "sweep" ? { axes: readAxes(), statistics_only: statisticsOnly() } : {}) });
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

  const api = { mount, statistics, interpret, compileFormula, summarize };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.q1lensDefRuns = api;
})(typeof window !== "undefined" ? window : undefined);
