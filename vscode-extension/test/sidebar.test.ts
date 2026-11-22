import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { buildSidebarItems, SIDEBAR_TITLE_COMMANDS } from "../src/qbs/sidebar";

describe("sidebar", () => {
  it("exposes the Q1Lens workflow actions", () => {
    const [environment, open, ...rest] = buildSidebarItems();
    assert.deepEqual(rest, []);
    assert.deepEqual([environment.label, environment.command, environment.description], ["Python environment", "q1lens.checkEnvironment", undefined]);
    assert.equal(open.label, "Open");
    assert.equal(open.command, undefined);
    assert.deepEqual(open.children?.map((item) => [item.label, item.command]), [
      ["Q1ASM timeline", "qbsTimeline.openQ1Timeline"],
      ["Current folder Q1ASM", "qbsTimeline.openCurrentFolderQ1Timeline"],
      ["QBS IR", "qbsTimeline.openIr"],
      ["Rendered HTML", "qbsTimeline.openRenderedHtml"],
      ["Q1ASM folder", "qbsTimeline.openQ1asmFolder"],
    ]);
  });

  it("keeps explanations in tooltips instead of row descriptions", () => {
    const [environment, open] = buildSidebarItems();
    for (const item of [environment, open, ...open.children!]) {
      assert.ok(item.tooltip.length > 0, item.label);
      assert.equal(item.description, undefined, item.label);
    }
  });

  it("shows the checked Python environment and its state", () => {
    const row = (state: "ready" | "error" | "checking") =>
      buildSidebarItems({ name: ".venv", version: "3.12.10", state, detail: "/project/.venv/bin/python" })[0];
    assert.deepEqual([row("ready").label, row("ready").description, row("ready").icon], ["Python", ".venv · 3.12", "pass"]);
    assert.equal(row("ready").command, "q1lens.checkEnvironment");
    assert.match(row("ready").tooltip, /\/project\/\.venv\/bin\/python/);
    assert.equal(row("error").icon, "error");
    assert.equal(row("checking").icon, "sync");
    assert.equal(buildSidebarItems({ name: "Not selected", state: "error" })[0].description, "Not selected");
  });

  it("contributes Analyze and Refresh as view title actions with icons", () => {
    const manifest = JSON.parse(readFileSync(join(__dirname, "../../package.json"), "utf8"));
    const title = manifest.contributes.menus["view/title"] as { command: string; when: string; group: string }[];
    assert.deepEqual(title.map((item) => item.command), [...SIDEBAR_TITLE_COMMANDS]);
    for (const item of title) {
      assert.equal(item.when, "view == qbsTimeline.sidebar");
      assert.match(item.group, /^navigation/);
      const command = manifest.contributes.commands.find((entry: { command: string }) => entry.command === item.command);
      assert.match(command.icon, /^\$\(.+\)$/);
    }
  });
});
