import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

function packageJson(): any {
  return JSON.parse(readFileSync(join(__dirname, "..", "..", "package.json"), "utf8"));
}

function extensionRoot(): string {
  return join(__dirname, "..", "..");
}

describe("package metadata", () => {
  it("uses Q1Lens as the product display name", () => {
    const manifest = packageJson();

    assert.equal(manifest.displayName, "Q1Lens");
    assert.equal(manifest.name, "q1lens");
    assert.equal(manifest.publisher, "q1lens");
  });

  it("uses Q1Lens icon assets for the extension and activity bar", () => {
    const manifest = packageJson();
    const root = extensionRoot();

    assert.equal(manifest.icon, "media/q1lens-icon.png");
    assert.ok(existsSync(join(root, manifest.icon)));
    assert.ok(
      manifest.contributes.viewsContainers.activitybar.some((entry: { id: string; icon: string }) => (
        entry.id === "qbsTimeline" &&
        entry.icon === "media/qbs-timeline.svg" &&
        existsSync(join(root, entry.icon))
      )),
    );
  });

  it("keeps QBS and q1timeline command aliases contributed", () => {
    const commands = packageJson().contributes.commands.map((entry: { command: string }) => entry.command);

    assert.ok(commands.includes("qbsTimeline.analyzeAndOpen"));
    assert.ok(commands.includes("qbsTimeline.openQ1Timeline"));
    assert.ok(commands.includes("qbsTimeline.openCurrentFolderQ1Timeline"));
    assert.ok(commands.includes("q1timeline.openPreview"));
    assert.ok(commands.includes("q1timeline.refreshPreview"));
  });

  it("uses Q1Lens as the visible command category", () => {
    const commands = packageJson().contributes.commands
      .filter((entry: { category?: string }) => entry.category !== undefined);

    assert.ok(commands.length > 0);
    assert.ok(commands.every((entry: { category: string }) => entry.category === "Q1Lens"));
  });

  it("activates and exposes q1timeline preview actions for Q1ASM files", () => {
    const manifest = packageJson();
    const commands = manifest.contributes.commands.map((entry: { command: string }) => entry.command);

    assert.ok(manifest.activationEvents.includes("onLanguage:q1asm"));
    assert.ok(manifest.activationEvents.includes("workspaceContains:**/*.q1asm"));
    assert.ok(manifest.activationEvents.includes("onCommand:q1timeline.openPreview"));
    assert.ok(manifest.activationEvents.includes("onCommand:q1timeline.refreshPreview"));
    assert.ok(manifest.activationEvents.includes("onCommand:q1timeline.selectQ1asmFilesInFolder"));
    assert.ok(commands.includes("q1timeline.selectQ1asmFilesInFolder"));

    const menus = manifest.contributes.menus;
    assert.ok(
      menus["editor/title"].some((entry: { command: string; when: string }) => (
        entry.command === "q1timeline.openPreview" &&
        entry.when.includes("resourceExtname == .q1asm")
      )),
    );
    assert.ok(
      menus["editor/context"].some((entry: { command: string; when: string }) => (
        entry.command === "q1timeline.openPreview" &&
        entry.when.includes("resourceExtname == .q1asm")
      )),
    );
    assert.ok(
      menus["explorer/context"].some((entry: { command: string; when: string }) => (
        entry.command === "q1timeline.openPreview" &&
        entry.when.includes("resourceExtname == .q1asm")
      )),
    );
    assert.ok(
      menus["editor/context"].some((entry: { command: string; when: string }) => (
        entry.command === "q1timeline.selectQ1asmFilesInFolder" &&
        entry.when.includes("resourceExtname == .q1asm")
      )),
    );
    assert.ok(
      menus["explorer/context"].some((entry: { command: string; when: string }) => (
        entry.command === "q1timeline.selectQ1asmFilesInFolder" &&
        entry.when.includes("resourceExtname == .q1asm")
      )),
    );
  });

  it("activates and exposes Q1Lens actions for notebook code cells", () => {
    const manifest = packageJson();
    const commands = new Map(
      manifest.contributes.commands.map((entry: { command: string; title: string }) => [entry.command, entry.title]),
    );

    assert.equal(commands.get("qbsTimeline.openNotebookTimelineFromCell"), "Open Q1Lens from This Cell");
    assert.equal(commands.get("qbsTimeline.markNotebookSetupCell"), "Mark Cell as Q1Lens Setup");
    assert.equal(commands.get("qbsTimeline.markNotebookScheduleCell"), "Mark Cell as Q1Lens Schedule");
    assert.ok(manifest.activationEvents.includes("onCommand:qbsTimeline.openNotebookTimelineFromCell"));
    assert.ok(manifest.activationEvents.includes("workspaceContains:**/*.ipynb"));

    const menus = manifest.contributes.menus;
    assert.ok(
      menus["notebook/cell/title"].some((entry: { command: string; when: string }) => (
        entry.command === "qbsTimeline.openNotebookTimelineFromCell" &&
        entry.when.includes("notebookType == jupyter-notebook") &&
        entry.when.includes("cellType == code")
      )),
    );
    assert.ok(
      menus["notebook/cell/context"].some((entry: { command: string; when: string }) => (
        entry.command === "qbsTimeline.markNotebookSetupCell" &&
        entry.when.includes("cellType == code")
      )),
    );
    assert.ok(
      menus["notebook/toolbar"].some((entry: { command: string; when: string }) => (
        entry.command === "qbsTimeline.openNotebookTimelineFromCell" &&
        entry.when.includes("notebookType == jupyter-notebook")
      )),
    );
  });

  it("contributes q1lens settings while preserving legacy setting aliases", () => {
    const properties = packageJson().contributes.configuration.properties;

    assert.ok(properties["q1lens.pythonPath"]);
    assert.ok(properties["q1lens.pythonArgs"]);
    assert.ok(properties["q1lens.qbs.autoRefresh"]);
    assert.ok(properties["q1lens.qbs.outputDirOverride"]);
    assert.ok(properties["q1lens.q1timeline.projectFile"]);
    assert.ok(properties["qbloxTimeline.pythonPath"]);
    assert.ok(properties["qbsTimeline.pythonPath"]);
    assert.ok(properties["q1timeline.projectFile"]);
  });

  it("exposes local reinstall and installed asset verification scripts", () => {
    const scripts = packageJson().scripts;

    assert.equal(scripts["verify:installed"], "node ./scripts/verify-installed-assets.js");
    assert.equal(
      scripts["reinstall:local"],
      "npm run package && code --install-extension q1lens-0.1.2.vsix --force && npm run verify:installed",
    );
  });

  it("bundles runtime dependencies for Marketplace distribution", () => {
    const manifest = packageJson();

    assert.equal(manifest.main, "./dist/extension.js");
    assert.ok(manifest.scripts["vscode:prepublish"].includes("npm run bundle"));
    assert.ok(manifest.scripts.bundle.includes("--external:vscode"));
  });
});
