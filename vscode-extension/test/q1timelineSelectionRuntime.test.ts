import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

function functionSource(script: string, name: string) {
  const start = script.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `Missing ${name}`);
  const body = script.indexOf(") {", start) + 2;
  let depth = 0;
  for (let index = body; index < script.length; index += 1) {
    if (script[index] === "{") depth += 1;
    if (script[index] === "}" && --depth === 0) return script.slice(start, index + 1);
  }
  throw new Error(`Unclosed ${name}`);
}

function selectionEvents() {
  const event = (id: string, kind: string, start: number, meta: Record<string, unknown>, lane?: string) => ({
    id, kind, sequencer_id: "seq0", t0: start, t1: start + 4, confidence: "exact",
    lane: lane || (kind === "q1_issue" ? "q1_issue.seq0" : "rt.control"),
    source: { file: "sequence.q1asm", line: kind === "q1_issue" ? 1 : 2 }, meta,
  });
  const application = (packet: string, issue: string, setter: string, frequency: number) => ({
    rt_packet_id: packet, q1_issue_event_id: issue,
    applied_state: { frequency }, applied_setter_event_ids: { frequency: setter },
  });
  return [
    event("setter-1", "q1_issue", 0, { op: "set_freq" }),
    event("pending-1", "latched_state_pending", 4, { field: "frequency", value: 100, q1_issue_event_id: "setter-1" }, "state.pending"),
    event("play-issue-1", "q1_issue", 4, { op: "play", rt_packet_id: "packet-1" }),
    event("setter-2", "q1_issue", 8, { op: "set_freq" }),
    event("pending-2", "latched_state_pending", 12, { field: "frequency", value: 200, q1_issue_event_id: "setter-2" }, "state.pending"),
    event("replay-issue", "q1_issue", 12, { op: "play", rt_packet_id: "replay-packet" }),
    event("play-issue-2", "q1_issue", 16, { op: "play", rt_packet_id: "packet-2" }),
    event("wait-issue", "q1_issue", 20, { op: "wait", rt_packet_id: "wait-packet" }),
    event("play-path-0", "play", 100, { ...application("packet-1", "play-issue-1", "pending-1", 100), path: 0 }, "rt.path0"),
    event("play-path-1", "play", 100, { ...application("packet-1", "play-issue-1", "pending-1", 100), path: 1 }, "rt.path1"),
    event("replay", "play", 104, application("replay-packet", "replay-issue", "pending-1", 100), "rt.path0"),
    event("play-2", "play", 108, application("packet-2", "play-issue-2", "pending-2", 200), "rt.path0"),
    event("wait", "wait", 112, application("wait-packet", "wait-issue", "pending-2", 200)),
  ];
}

function createNode(eventId: string, eventIds?: string, classes = ["event"]) {
  const classNames = new Set(classes);
  const attributes: Record<string, string> = { "aria-selected": classes.includes("is-selected") ? "true" : "false" };
  return {
    dataset: { eventId, eventIds }, attributes, classNames,
    classList: {
      add(name: string) { classNames.add(name); },
      remove(name: string) { classNames.delete(name); },
      contains(name: string) { return classNames.has(name); },
      toggle(name: string, enabled: boolean) {
        if (enabled) classNames.add(name);
        else classNames.delete(name);
      },
    },
    setAttribute(name: string, value: string) { attributes[name] = value; },
  };
}

function loadSelectionRuntime(merged = false) {
  const script = readFileSync(join(__dirname, "..", "src", "q1timeline", "media", "timeline.js"), "utf8");
  const adapter = require("../src/q1timeline/media/timelineAdapter.js");
  const events = selectionEvents();
  const nodes = events.filter((event) => event.kind !== "latched_state_pending" &&
    !(merged && ["play-path-0", "play-path-1"].includes(event.id)))
    .map((event) => createNode(event.id, undefined, event.id === "setter-1" ? ["event", "is-selected"] : ["event"]));
  if (merged) nodes.push(createNode("merged-play:play-path-0+play-path-1", "play-path-0 play-path-1"));
  const feedback = createNode("feedback", "play-path-0 play-path-1", ["feedback-connector", "is-selected"]);
  const selectors: string[] = [];
  const document = {
    querySelectorAll(selector: string) {
      selectors.push(selector);
      if (selector === '.event.is-selected, .event[aria-selected="true"], .shared-timeline-stage .timeline-block[aria-selected="true"]') {
        return nodes.filter((node) => node.classList.contains("is-selected") || node.attributes["aria-selected"] === "true");
      }
      assert.equal(selector, ".event[data-event-id]", "Related selection must target event blocks, leaving LINQ connectors alone");
      return nodes;
    },
  };
  const runtime = Function("adapter", "document", "events", "nodes", `
    const timelineIr = { events };
    let timelineSelectionEvents;
    let timelineSelectionIndex = new Map();
    let selectedTimelineEventId;
    const selectedSourceEventIds = new Set();
    const hardwareSelections = [], inspectedEventIds = [], clickEvents = [];
    const hardwareView = { selectEvent(id) { hardwareSelections.push(id); } };
    const isTimelineEventVisible = () => true;
    const expandQ1IssueLanesForHighlight = () => false;
    const syncSharedAnnotationSelection = () => {};
    const renderSharedEventInspector = (id) => inspectedEventIds.push(id);
    const syncTimelineZoomActionState = () => {};
    const openAnalysisDetails = () => {};
    function CustomEvent(type, init) { this.type = type; this.detail = init.detail; }
    const timelineEventsById = new Map(events.map((event) => [String(event.id), event]));
    let builds = 0;
    const window = { q1timelineTimelineAdapter: {
      buildQ1TimelineSelectionIndex(ir) { builds += 1; return adapter.buildQ1TimelineSelectionIndex(ir); },
    }, dispatchEvent(event) { clickEvents.push(event.detail); } };
    ${["currentTimelineSelectionIndex", "syncTimelineRelatedSelection", "eventIsQ1Issue", "prioritizedHighlightEventIds", "sourceNavigationEvent", "selectEventNode"]
      .map((name) => functionSource(script, name)).join("\n")}
    return {
      select: syncTimelineRelatedSelection,
      prioritize: prioritizedHighlightEventIds,
      navigate: (id) => sourceNavigationEvent(timelineEventsById.get(id))?.id,
      preparation: (id) => currentTimelineSelectionIndex().get(id)?.rtPreparation,
      click: (id, options) => selectEventNode(nodes.find((node) => node.dataset.eventId === id), options),
      selectedEventId: () => selectedTimelineEventId,
      hardwareSelections, inspectedEventIds, clickEvents,
      replaceEvents(events) { timelineIr.events = events; },
      builds: () => builds,
    };
  `)(adapter, document, events, nodes);
  return {
    ...runtime, events, nodes, feedback, selectors,
    highlighted(className: string): string[] {
      return nodes.filter((node) => node.classList.contains(className)).map((node) => node.dataset.eventId).sort();
    },
  };
}

describe("Q1 timeline related selection", () => {
  it("prefers RT execution for source navigation while retaining Q1-only selection and setter navigation", () => {
    const runtime = loadSelectionRuntime();
    assert.deepEqual(runtime.prioritize(["wait-issue", "wait"]), ["wait", "wait-issue"]);
    assert.deepEqual(runtime.prioritize(["play-issue-1", "play-path-1", "play-path-0"]), ["play-path-1", "play-path-0", "play-issue-1"]);
    assert.deepEqual(runtime.prioritize(["pending-1", "setter-1"]), ["setter-1", "pending-1"]);
    assert.deepEqual(runtime.prioritize(["wait-issue"]), ["wait-issue"]);
    runtime.select("wait");
    assert.equal(runtime.builds(), 1);
  });

  it("navigates Q1 settings to the next RT instruction independently of their parameter application", () => {
    const runtime = loadSelectionRuntime();
    assert.equal(runtime.navigate("setter-2"), "replay");
    assert.equal(runtime.navigate("wait-issue"), "wait");
    assert.equal(runtime.navigate("wait"), "wait");
    assert.equal(runtime.preparation("setter-1"), true);
    assert.equal(runtime.navigate("setter-1"), "play-path-0");
  });

  it("moves the hardware cursor to the mapped RT start when a Q1 block is selected while preserving its source selection", () => {
    const runtime = loadSelectionRuntime();
    for (const id of ["setter-2", "play-issue-1"]) {
      runtime.click(id);
      assert.equal(runtime.hardwareSelections.at(-1), runtime.navigate(id));
      assert.equal(runtime.selectedEventId(), id);
      assert.equal(runtime.inspectedEventIds.at(-1), id);
      assert.deepEqual(runtime.clickEvents.at(-1), { eventId: id });

      const calls = runtime.hardwareSelections.length;
      runtime.click(id, { syncHardware: false, notify: false });
      assert.equal(runtime.hardwareSelections.length, calls, "Hardware-driven selection must preserve the replay cursor");
      assert.equal(runtime.selectedEventId(), id);
      assert.equal(runtime.inspectedEventIds.at(-1), id);
    }
  });

  it("highlights the first applying RT command for the selected setter occurrence", () => {
    const runtime = loadSelectionRuntime();
    assert.deepEqual(runtime.select("setter-1").sort(), ["play-path-0", "play-path-1"]);
    assert.deepEqual(runtime.highlighted("is-instruction-related"), ["play-path-0", "play-path-1"]);
    assert.deepEqual(runtime.highlighted("is-parameter-apply"), ["play-path-0", "play-path-1"]);

    runtime.select("setter-2");
    assert.deepEqual(runtime.highlighted("is-instruction-related"), ["play-2"]);
    assert.deepEqual(runtime.highlighted("is-parameter-apply"), ["play-2"]);
  });

  it("links Q1 play only to its RT execution and links RT back to its issue and first-applied setter", () => {
    const runtime = loadSelectionRuntime();
    runtime.select("play-issue-1");
    assert.deepEqual(runtime.highlighted("is-instruction-related"), ["play-path-0", "play-path-1"]);
    assert.deepEqual(runtime.highlighted("is-parameter-apply"), []);

    runtime.select("play-path-1");
    assert.deepEqual(runtime.highlighted("is-instruction-related"), ["play-issue-1", "setter-1"]);
    assert.deepEqual(runtime.highlighted("is-parameter-apply"), ["play-path-0", "play-path-1"]);

    runtime.select("replay");
    assert.deepEqual(runtime.highlighted("is-instruction-related"), ["replay-issue"]);
    assert.deepEqual(runtime.highlighted("is-parameter-apply"), []);
  });

  it("clears stale application highlights when selecting wait or clearing selection", () => {
    const runtime = loadSelectionRuntime();
    runtime.select("setter-2");
    runtime.select("wait");
    assert.deepEqual(runtime.highlighted("is-instruction-related"), ["wait-issue"]);
    assert.deepEqual(runtime.highlighted("is-parameter-apply"), []);
    runtime.select(undefined);
    assert.deepEqual(runtime.highlighted("is-instruction-related"), []);
    assert.deepEqual(runtime.highlighted("is-parameter-apply"), []);
  });

  it("matches either path ID on a merged play block", () => {
    const runtime = loadSelectionRuntime(true);
    const mergedId = "merged-play:play-path-0+play-path-1";
    runtime.select("setter-1");
    assert.deepEqual(runtime.highlighted("is-instruction-related"), [mergedId]);
    assert.deepEqual(runtime.highlighted("is-parameter-apply"), [mergedId]);
    runtime.select("play-path-1");
    assert.deepEqual(runtime.highlighted("is-instruction-related"), ["play-issue-1", "setter-1"]);
    assert.deepEqual(runtime.highlighted("is-parameter-apply"), [mergedId]);
  });

  it("preserves the actual selection and LINQ connector styling", () => {
    const runtime = loadSelectionRuntime();
    const attributes = runtime.nodes.map((node: ReturnType<typeof createNode>) => ({ ...node.attributes }));
    const feedbackClasses = [...runtime.feedback.classNames];
    runtime.select("setter-1");
    assert.deepEqual(runtime.highlighted("is-selected"), ["setter-1"]);
    assert.deepEqual(runtime.nodes.map((node: ReturnType<typeof createNode>) => node.attributes), attributes);
    assert.deepEqual([...runtime.feedback.classNames], feedbackClasses);
    assert.equal(runtime.feedback.attributes["aria-selected"], "true");
    assert.deepEqual(runtime.selectors, [".event[data-event-id]"]);
  });

  it("reuses the relation index until the event array is replaced", () => {
    const runtime = loadSelectionRuntime();
    runtime.select("setter-1");
    runtime.select("setter-2");
    assert.equal(runtime.builds(), 1);
    runtime.replaceEvents(runtime.events.filter((event: ReturnType<typeof selectionEvents>[number]) => !["play-path-0", "play-path-1"].includes(event.id)));
    runtime.select("setter-1");
    assert.equal(runtime.builds(), 2);
    assert.deepEqual(runtime.highlighted("is-instruction-related"), ["replay"]);
    assert.deepEqual(runtime.highlighted("is-parameter-apply"), ["replay"]);
  });
});
