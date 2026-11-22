(() => {
  const bridge = acquireVsCodeApi();
  const check = JSON.parse(document.getElementById("witness").textContent);
  const witness = check.counterexample, ir = witness.timeline;
  const events = new Map(ir.events.map((event) => [String(event.id), event]));
  const original = events.get(String(witness.event_id));
  const adapter = window.q1timelineTimelineAdapter;
  const basis = witness.time_basis === "aligned" ? "aligned" : "local";
  const edge = (event, key) => adapter.eventTimeValue(
    (basis === "aligned" ? event.meta?.[`aligned_${key}`] : undefined) ?? event[key] ?? event[`${key}_ns`], NaN);
  const model = adapter.buildQ1TimelineSharedModel(ir, {
    mode: "debug", timeBasis: basis, expandedQ1IssueSequencers: ir.sequencers.map((s) => s.id),
    expandedPlaybackLoops: ir.events.filter((e) => e.kind === "loop_block").map((e) => e.meta?.loop_preview_key || e.meta?.loop_id || e.id),
  });
  const cursor = Number.isFinite(witness.time_ns) ? witness.time_ns : edge(original, "t0");
  const start = ir.events.reduce((min, event) => {
    const t = edge(event, "t0");
    return Number.isFinite(t) ? Math.min(min, t) : min;
  }, Math.min(0, Number.isFinite(cursor) ? cursor : 0));
  const end = Math.max(model.totalTime, Number.isFinite(cursor) ? cursor + 1 : 0, start + 1);
  const inputsText = Object.entries(witness.inputs || {}).map(([name, value]) => `${name}=${value}`).join(", ");
  const kindText = { underflow: "RT underflow", assertion: "Assertion failed", equivalence: "RT traces differ",
    alignment: "Misaligned" }[check.kind] || "Counterexample";
  // The header states the inputs and time once; the rest of the finding keeps safe ranges and assumptions.
  function findingRemainder(message) {
    const literal = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    let text = String(message || "").trim();
    if (inputsText) {
      text = text.replace(new RegExp("\\s+for " + literal(inputsText) + "(?!\\w|\\.[\\d.])", "g"), "")
        .replace(new RegExp("^" + literal(inputsText) + ":\\s*"), "");
    }
    if (check.kind === "underflow" && Number.isFinite(witness.time_ns)) {
      text = text.replace(new RegExp("\\s+at " + literal(String(witness.time_ns)) + " ns(?!\\w)", "g"), "");
    }
    const parts = [];
    const note = /\s*\(([^()]*)\)\.?$/.exec(text);
    if (note) text = text.slice(0, note.index);
    for (const part of text.split(/\.(?:\s+|$)/)) {
      const value = part.trim();
      if (value && ![kindText, "First " + kindText].includes(value)) parts.push(value);
    }
    if (note && note[1].trim()) parts.push(note[1].trim());
    return parts.join(" · ");
  }
  const setText = (id, text) => {
    const node = document.getElementById(id);
    if (!node) return;
    node.textContent = text;
    node.hidden = !text;
  };
  const heading = document.getElementById("heading");
  if (heading) heading.textContent = "Counterexample: " + check.message;
  setText("kind", kindText);
  setText("inputs", inputsText);
  setText("finding", findingRemainder(check.message));
  document.getElementById("finding").title = check.message;
  let selected = original;
  const hardware = window.q1lensHardwareView.mount(document.getElementById("hardware"), ir, {
    observation: { time: cursor, basis },
    initialState: { level: "detail", sequencerId: original.sequencer_id, basis,
      selectedBlock: witness.port ? "awg" : witness.register ? "registers" : "q1", selectedRegister: witness.register,
      time: witness.time_ns ?? edge(original, "t0") },
    onSelectEvent: (id) => select(id, true, false),
    onSourceClick(source, id) {
      const event = events.get(String(id)) || ir.events.find((e) => e.source?.file === source.file && e.source.line === source.line);
      if (event) bridge.postMessage({ type: "eventSource", id: String(event.id) });
    },
  });
  function select(id, zoom, syncHardware = true) {
    const event = events.get(String(id));
    if (!event) return;
    selected = event;
    const t0 = edge(event, "t0"), t1 = edge(event, "t1");
    const time = event === original && Number.isFinite(witness.time_ns) ? witness.time_ns : t0;
    const extent = Number.isFinite(t1) && Number.isFinite(t0) ? Math.max(0, t1 - t0) : 0;
    const span = Math.max(64, extent * 3);
    const view = window.q1lensSharedTimeline.normalizeViewport(end - start, zoom && Number.isFinite(time)
      ? { start: time - start - span / 2, end: time - start + span / 2 } : undefined);
    model.viewport = { start: start + view.start, end: start + view.end };
    model.ticks = [0, 25, 50, 75, 100].map((leftPercent) => ({ leftPercent,
      label: Math.round(model.viewport.start + leftPercent / 100 * (model.viewport.end - model.viewport.start)) + " ns" }));
    for (const lane of model.lanes) {
      for (const block of lane.blocks) block.selected = (block.eventIds || [block.eventId]).some((item) => String(item) === String(id));
    }
    for (const annotation of model.annotations) annotation.selected = (annotation.eventIds || [annotation.eventId]).some((item) => String(item) === String(id));
    window.q1lensSharedTimeline.renderTimeline(document.getElementById("timeline-root"), model, {
      onBlockClick: (block) => select(block.eventId, false),
      onAnnotationClick: (annotation) => select(annotation.eventId || annotation.eventIds?.[0], true),
    });
    // The finding already names the counterexample event; any other selected event says what it is.
    const details = [ir.sequencers.length > 1 || event !== original ? event.sequencer_id : null,
      event !== original ? event.label || event.kind : null, Number.isFinite(time) ? time + " ns" : null];
    const location = document.getElementById("occurrence");
    location.textContent = details.filter(Boolean).join(" · ");
    location.dataset.eventId = String(id);
    const source = document.getElementById("source");
    source.disabled = !event.source?.file;
    source.textContent = event.source?.file ? event.source.file.split(/[\\/]/).pop() + ":" + event.source.line : "";
    source.hidden = !event.source?.file;
    if (syncHardware) {
      hardware.selectEvent(id);
      if (Number.isFinite(time)) hardware.setTime(time);
    }
  }
  document.getElementById("source").onclick = () => bridge.postMessage({ type: "eventSource", id: String(selected.id) });
  document.getElementById("focus").onclick = () => select(original.id, true);
  document.getElementById("full").onclick = () => select(selected.id, false);
  select(original.id, true);
})();
