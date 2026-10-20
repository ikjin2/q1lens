import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { runInNewContext } from "node:vm";

describe("QBS webview runtime", () => {
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
