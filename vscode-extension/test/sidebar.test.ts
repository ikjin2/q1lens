import assert from "node:assert/strict";
import { buildSidebarItems } from "../src/qbs/sidebar";

describe("sidebar", () => {
  it("exposes the Q1Lens workflow actions", () => {
    assert.deepEqual(buildSidebarItems().map((item) => [item.label, item.command]), [
      ["Analyze and Open", "qbsTimeline.analyzeAndOpen"],
      ["Refresh", "qbsTimeline.refresh"],
      ["Open QBS IR", "qbsTimeline.openIr"],
      ["Open Rendered HTML", "qbsTimeline.openRenderedHtml"],
      ["Open Q1ASM Folder", "qbsTimeline.openQ1asmFolder"],
      ["Open Q1ASM Timeline", "qbsTimeline.openQ1Timeline"],
      ["Open Current Folder Q1ASM", "qbsTimeline.openCurrentFolderQ1Timeline"],
    ]);
  });
});
