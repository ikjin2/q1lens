import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { runInNewContext } from "node:vm";

describe("QBS webview runtime", () => {
  it("tags source jumps with the received render while leaving readiness and refresh untagged", () => {
    const script = readFileSync(join(__dirname, "../src/qbs/webview/assets/timeline.js"), "utf8");
    const posted: any[] = [];
    let receiveMessage!: (event: { data: unknown }) => void;
    let refresh!: () => void;
    const window: any = {
      qbsTimelineModel: { buildTimelineModel: () => ({}) },
      addEventListener: (_type: string, listener: typeof receiveMessage) => { receiveMessage = listener; },
    };
    runInNewContext(`${script}
      renderTimeline = renderInspector = renderSummary = () => null;
      window.jump = () => postSourceJumpMessage({ scheduleSourceMessage: {
        type: "openScheduleSource", schedulableId: "selected", blockId: "selected",
      } }, { preventDefault() {}, stopPropagation() {} });
    `, {
      document: {
        getElementById: (id: string) => id === "qbs-root" ? { replaceChildren() {} }
          : { addEventListener: (_type: string, listener: typeof refresh) => { refresh = listener; } },
        querySelector: () => ({ textContent: "" }),
      }, window, acquireVsCodeApi: () => ({ postMessage: (message: any) => posted.push(JSON.parse(JSON.stringify(message))) }),
    });
    const ir = { schedule: { name: "unit" } };
    receiveMessage({ data: { type: "render", ir, renderId: 17 } });
    window.jump();
    receiveMessage({ data: { type: "render", ir, renderId: 18 } });
    window.jump();
    refresh();
    assert.deepEqual(posted, [
      { type: "ready" },
      { type: "openScheduleSource", schedulableId: "selected", blockId: "selected", renderId: 17 },
      { type: "openScheduleSource", schedulableId: "selected", blockId: "selected", renderId: 18 },
      { type: "refresh" },
    ]);
  });

  it("preserves refresh view state and clears it when the source project changes", () => {
    const script = readFileSync(join(__dirname, "../src/qbs/webview/assets/timeline.js"), "utf8");
    const calls: any[] = [];
    let receiveMessage!: (event: { data: unknown }) => void;
    const window: any = {
      qbsTimelineModel: { buildTimelineModel: (_ir: unknown, selectedId: unknown, source: unknown, state: unknown) => {
        calls.push(JSON.parse(JSON.stringify({ selectedId, source, state })));
        return {};
      } },
      addEventListener: (_type: string, listener: typeof receiveMessage) => { receiveMessage = listener; },
    };
    runInNewContext(`${script}
      renderTimeline = renderInspector = renderSummary = () => null;
      window.inspectSelection = () => {
        selectedId = "second";
        viewport = { start: 10, end: 20 };
        selectionRange = { start: 12, end: 14 };
        expandedGroups.add("target:q0");
        expandedInlineQ1Lanes.add("inline-q1:Schedule / q0");
        activeInspectorTab = "q1asm";
      };
      window.inspectorTab = () => activeInspectorTab;
    `, {
      document: {
        getElementById: (id: string) => id === "qbs-root" ? { replaceChildren() {} } : { addEventListener() {} },
        querySelector: () => ({ textContent: "" }),
      }, window, acquireVsCodeApi: () => ({ postMessage() {} }),
    });
    const sourceA = { projectFile: "/a/qbstimeline.yml", scheduleFile: "/a/schedule.py" };
    const sourceB = { projectFile: "/b/qbstimeline.yml", scheduleFile: "/b/schedule.py" };
    const ir = { schedule: { name: "unit" } };
    receiveMessage({ data: { type: "render", ir, sourceContext: sourceA } });
    window.inspectSelection();
    receiveMessage({ data: { type: "render", ir, sourceContext: sourceA } });
    assert.equal(calls[1].selectedId, "second");
    assert.deepEqual(calls[1].state.viewport, { start: 10, end: 20 });
    assert.deepEqual(calls[1].state.expandedGroups, ["target:q0"]);
    assert.equal(window.inspectorTab(), "q1asm");

    receiveMessage({ data: { type: "render", ir, sourceContext: sourceB } });
    assert.equal(calls[2].selectedId, null);
    assert.deepEqual(calls[2].state, { viewport: null, selectionRange: null, exactSelection: true, expandedGroups: [], expandedInlineQ1Lanes: [] });
    assert.equal(window.inspectorTab(), "summary");
    receiveMessage({ data: { type: "render", ir } });
    assert.equal(calls[3].source, null);
  });

  it("updates the visible heading and document title when the schedule changes", () => {
    const script = readFileSync(join(__dirname, "../src/qbs/webview/assets/timeline.js"), "utf8");
    const heading = { textContent: "Schedule A" };
    const root = { replaceChildren: () => undefined };
    const document = {
      title: "Schedule A",
      getElementById: (id: string) => id === "qbs-root" ? root : { addEventListener: () => undefined },
      querySelector: (selector: string) => selector === ".topbar h1" ? heading : undefined,
    };
    let receiveMessage!: (event: { data: unknown }) => void;
    runInNewContext(`${script}
      renderTimeline = () => null;
      renderInspector = () => null;
      renderSummary = () => null;
    `, {
      document,
      window: {
        qbsTimelineModel: { buildTimelineModel: () => ({}) },
        addEventListener: (_type: string, listener: typeof receiveMessage) => { receiveMessage = listener; },
      },
      acquireVsCodeApi: () => ({ postMessage: () => undefined }),
    });

    for (const name of ["Schedule A", "Schedule B <updated>", ""]) {
      receiveMessage({ data: { type: "render", ir: { schedule: { name } } } });
      assert.equal(heading.textContent, name || "Q1Lens");
      assert.equal(document.title, name || "Q1Lens");
    }
  });
});
