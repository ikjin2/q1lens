import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

function sourceFunction(script: string, name: string) {
  const start = script.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `Missing ${name}`);
  let depth = 0;
  for (let index = script.indexOf(") {", start) + 2; index < script.length; index += 1) {
    if (script[index] === "{") depth += 1;
    if (script[index] === "}" && --depth === 0) return script.slice(start, index + 1);
  }
  throw new Error(`Unclosed ${name}`);
}

const source = { file: "branch.q1asm", line: 2 };
function event(id: string, meta: Record<string, unknown> = {}, kind = "q1_issue", sequencer = "seq") {
  return { id, kind, sequencer_id: sequencer, source,
    meta: { op: "jlt", branch_id: "branch", branch_decision: "runtime_dependent", condition: "R0 < 5", ...meta } };
}
function graph(sequencer = "seq", target = "pulse") {
  return { sequencer_id: sequencer,
    nodes: [{ id: "from", end_pc: 1 }, { id: "to", source: { file: source.file, line: 8 }, labels: [target] },
      { id: "next", source: { file: source.file, line: 3 } }],
    edges: [{ kind: "branch_taken", source, target_label: target, target_pc: 7, from_node_id: "from", to_node_id: "to" },
      { kind: "branch_fallthrough", source, from_node_id: "from", to_node_id: "next" }] };
}
function load(events: any[], sequencers = [graph()], playback?: any) {
  const script = readFileSync(join(__dirname, "..", "src/q1timeline/media/timeline.js"), "utf8");
  const names = ["eventHasBranchControl", "branchAssumptionLabel", "normalizedSource", "sameSourceLine", "controlFlowGraphSequencers",
    "controlFlowGraphNode", "branchControlMeta", "branchPathDescriptor", "branchComparisonSupported", "renderBranchControls"];
  return Function("timelineIr", `
    const actions = [], messages = [], status = [];
    const timelineExecutionState = timelineIr.playback || {status: "idle"};
    const document = { createElement: () => ({}) };
    const vscode = { postMessage: message => messages.push(message) };
    let restoredFocus = false;
    const closeTimelineControlPopover = restore => { restoredFocus = restore; };
    const zoomTimelineToEvent = () => {};
    const addTimelineControlAction = (popover, icon, label, click, options) => actions.push({icon, label, click, options});
    ${names.map(name => sourceFunction(script, name)).join("\n")}
    return { eligible: eventHasBranchControl, meta: branchControlMeta, target: branchPathDescriptor, supported: branchComparisonSupported,
      render(event) {
        renderBranchControls({classList: {add() {}}, setAttribute() {}, append(node) {status.push(node.textContent);}}, event);
        return {actions, messages, status, restoredFocus: () => restoredFocus};
      }
    };
  `)({ events, control_flow_graph: { sequencers }, ...(playback ? {playback} : {}) });
}

describe("Q1 branch controls", () => {
  it("does not give every instruction inside a compared path a branch selector", () => {
    const issue = event("branch");
    const setter = event("setter", {op: "set_awg_offs", branch_comparison_branch_id: "branch", branch_comparison_path: "taken"});
    delete (setter.meta as any).branch_id;
    const runtime = load([issue, setter]);
    assert.equal(runtime.eligible(issue), true);
    assert.equal(runtime.eligible(setter), false);
  });

  it("uses the selected occurrence and sequencer rather than the first branch result", () => {
    const first = event("first", { branch_decision: "concrete", branch_taken: false });
    const second = event("second", { branch_decision: "concrete", branch_taken: true });
    const other = event("other", { branch_taken: false }, "q1_issue", "other");
    const region = event("region", {}, "branch_region");
    delete (region.meta as any).branch_decision;
    const runtime = load([first, second, other, region]);
    assert.equal(runtime.meta(second).branch_taken, true);
    assert.equal(runtime.meta(region).branch_taken, true);
  });

  it("does not borrow metadata from another source, preview iteration or comparison path", () => {
    const issue = event("issue", { loop_preview: "loop", loop_iteration_index: 1, branch_taken: true });
    for (const meta of [{loop_preview: "loop", loop_iteration_index: 2}, {branch_comparison_path: "taken"}]) {
      const region = event("region", meta, "branch_region");
      assert.equal(load([issue, region]).meta(region).branch_taken, undefined);
    }
    const region = { ...event("region", {}, "branch_region"), source: {...source, line: 9} };
    assert.equal(load([issue, region]).meta(region).branch_taken, undefined);
  });

  it("scopes destination labels to the correct sequencer for shared source files", () => {
    const issue = event("issue");
    const runtime = load([issue], [graph("other", "wrong"), graph()]);
    assert.equal(runtime.target(issue, "taken").label, "@pulse");
    assert.equal(runtime.target(issue, "fallthrough").label, "L3");
  });

  it("enables both paths only for supported forward comparison branches", () => {
    const issue = event("issue");
    const runtime = load([issue]);
    const target = runtime.target(issue, "taken");
    assert.equal(runtime.supported(issue.meta, target), true);
    for (const meta of [{op: "jge", condition: "jge status flags"}, {op: "loop"},
      {branch_comparison_path: "taken"}, {branch_decision: "concrete"}]) {
      assert.equal(runtime.supported({...issue.meta, ...meta}, target), false);
    }
    assert.equal(runtime.supported(issue.meta, {...target, edge: {...target.edge, target_pc: 0}}), false);
    assert.equal(runtime.supported(issue.meta, {}), false);
  });

  it("sends only the branch choice and event identity without opening the source", () => {
    const issue = event("selected", { assumed_branch_path: "fallthrough", assumed_branch_taken: false });
    const result = load([issue]).render(issue);
    assert.deepEqual(result.actions.map((action: any) => action.label),
      ["Jump · @pulse", "Continue · L3", "Compare both", "Collapse", "Zoom around branch"]);
    assert.equal(result.actions[1].options.active, true);
    assert.deepEqual(result.status, ["Assumed path"]);
    result.actions[0].click();
    assert.deepEqual(result.messages, [{type: "setBranchAssumption", branchId: "branch", path: "taken", eventId: "selected"}]);
    assert.equal(result.restoredFocus(), true);
  });

  it("shows calculated results without allowing assumptions to replace them", () => {
    const issue = event("known", {branch_decision: "concrete", branch_taken: false});
    const result = load([issue]).render(issue);
    assert.deepEqual(result.status, ["Calculated path"]);
    assert.deepEqual(result.actions.slice(0, 2).map((action: any) => action.options.disabled), [true, true]);
    assert.equal(result.actions[1].options.active, true);
    assert.equal(result.actions.length, 3);
  });

  it("shows the assume_false project policy as the Continue assumption", () => {
    const issue = event("policy", {assumed_branch_path: "not_taken", assumed_branch_taken: false});
    const result = load([issue]).render(issue);
    assert.deepEqual(result.status, ["Assumed path"]);
    assert.equal(result.actions[1].options.active, true);
  });

  it("allows changing a calculated playback branch without offering parallel paths", () => {
    const issue = event("known", {playback_branch_id: "branch:occurrence:3", branch_decision: "concrete", branch_taken: false});
    const result = load([issue], [graph()], {status: "completed"}).render(issue);
    assert.deepEqual(result.actions.map((action: any) => action.label), ["Jump · @pulse", "Continue · L3", "Zoom around branch"]);
    assert.deepEqual(result.actions.slice(0, 2).map((action: any) => action.options.disabled), [false, false]);
    assert.equal(result.actions[1].options.active, true);
    assert.match(result.actions[0].options.title, /this occurrence.*recompute/);
    result.actions[0].click();
    assert.deepEqual(result.messages, [{type: "setExecutionBranch", branchId: "branch:occurrence:3", eventId: "known", path: "taken"}]);
  });

  it("marks an unknown playback condition as the default Continue path", () => {
    const issue = event("default", {playback_branch_id: "branch:0", branch_decision: "default", branch_taken: false,
      assumed_branch_path: "fallthrough", assumption_source: "default"});
    const result = load([issue], [graph()], {status: "completed"}).render(issue);
    assert.deepEqual(result.status, ["Default path"]);
    assert.equal(result.actions[1].options.active, true);
    assert.equal(result.actions[0].options.disabled, false);
  });

  it("matches playback branch markers by occurrence and prevents changes while running", () => {
    const first = event("first", {playback_branch_id: "branch:0", branch_decision: "concrete", branch_taken: false});
    const second = event("second", {playback_branch_id: "branch:1", branch_decision: "assumed", branch_taken: true});
    const marker = event("marker", {playback_branch_id: "branch:0"}, "branch_region");
    delete (marker.meta as any).branch_decision;
    const runtime = load([first, second, marker], [graph()], {status: "running"});
    assert.equal(runtime.meta(marker).branch_taken, false);
    assert.deepEqual(runtime.render(marker).actions.slice(0, 2).map((action: any) => action.options.disabled), [true, true]);
  });

  it("does not offer a conditional choice for an unconditional playback jump", () => {
    const jump = event("jump", {op: "jmp", playback_branch_id: "branch:0", branch_taken: true});
    const marker = event("marker", {playback_branch_id: "branch:0"}, "branch_region");
    delete (marker.meta as any).op;
    const runtime = load([jump, marker], [graph()], {status: "paused"});
    assert.equal(runtime.eligible(jump), false);
    assert.equal(runtime.eligible(marker), false);
  });

  it("opens a branch chip without notifying the host to jump to source", () => {
    const script = readFileSync(join(__dirname, "..", "src/q1timeline/media/timeline.js"), "utf8");
    let clicked: any;
    let selection: any;
    let opened = false;
    const chip = {dataset: {}, style: {}, setAttribute() {}, getAttribute() {return "branch";},
      addEventListener(_type: string, handler: any) {clicked = handler;} };
    const sourceNode = {closest: () => ({append() {}})};
    Function("document", "sourceNode", "selectEventNode", "openTimelineControlPopover", `
      const branchSourceJumpLabel = () => "branch", timelineControlChipLeft = () => "0%";
      const timelineControlChipTop = () => "0px", timelineControlSvgIcon = () => "";
      const branchAssumptionIconName = () => "branch", branchAssumptionLabel = () => "";
      ${sourceFunction(script, "addTimelineControlChip")}
      addTimelineControlChip(sourceNode, {id: "branch", meta: {}}, "branch");
    `)({createElement: () => chip}, sourceNode,
      (node: any, options: any) => {selection = {node, options};}, () => {opened = true;});
    clicked({preventDefault() {}, stopPropagation() {}});
    assert.deepEqual(selection, {node: sourceNode, options: {notify: false}});
    assert.equal(opened, true);
  });

  it("distinguishes a successful both-path analysis from an unavailable comparison", () => {
    for (const available of [false, true]) {
      const issue = event("both", {assumed_branch_path: "both", ...(available ? {comparison_paths: ["taken", "fallthrough"]} : {})});
      const result = load([issue]).render(issue);
      assert.deepEqual(result.status, [available ? "Comparing both paths" : "Comparison unavailable"]);
      assert.equal(result.actions.find((action: any) => action.label === "Compare both").options.active, true);
    }
  });
});
