import assert from "node:assert/strict";
import { relative } from "node:path";
import * as vscode from "vscode";

const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

async function waitFor(description: string, check: () => boolean | Promise<boolean>): Promise<void> {
  const deadline = Date.now() + 10000;
  while (!await check()) {
    assert.ok(Date.now() < deadline, `Timed out waiting for ${description}`);
    await pause(50);
  }
}

describe("Q1ASM external dependencies in VS Code", function () {
  this.timeout(45000);

  for (const externalProject of [false, true]) {
    it(externalProject ? "refreshes a closed external project's source and invalidates it in manual mode"
      : "refreshes and rebinds a closed external parameter file with an arbitrary extension", async () => {
      const extension = vscode.extensions.getExtension("q1lens.q1lens");
      assert.ok(extension);
      await extension.activate();
      const workspace = vscode.workspace.workspaceFolders?.[0]?.uri;
      assert.ok(workspace);
      const name = `external-dependencies-${Date.now()}`;
      const outside = vscode.Uri.joinPath(workspace, "..", name);
      const project = externalProject ? vscode.Uri.joinPath(outside, "project") : vscode.Uri.joinPath(workspace, name);
      const source = vscode.Uri.joinPath(project, "program.q1asm");
      const yaml = vscode.Uri.joinPath(project, "q1timeline.yml");
      const first = vscode.Uri.joinPath(outside, "first.data"), second = vscode.Uri.joinPath(outside, "second.data");
      assert.equal(vscode.workspace.getWorkspaceFolder(outside), undefined);
      await vscode.workspace.fs.createDirectory(project);
      await vscode.workspace.fs.createDirectory(outside);
      const write = (file: vscode.Uri, text: string) => vscode.workspace.fs.writeFile(file, Buffer.from(text));
      const projectText = (params: vscode.Uri) => [
        `params: {file: ${JSON.stringify(relative(project.fsPath, params.fsPath).replace(/\\/g, "/"))}}`,
        "analysis: {execution_mode: concrete}", "alignment: {mode: none}",
        "sequencers: [{id: seq, file: program.q1asm}]", "",
      ].join("\n");
      await write(first, '{"VALUE":17}');
      await write(second, '{"VALUE":31}');
      await write(source, externalProject ? "move 17,R0\nstop\n" : "move {VALUE},R0\nstop\n");
      await write(yaml, projectText(first));
      const config = vscode.workspace.getConfiguration("q1lens");
      const keys = ["q1timeline.updateMode", "q1timeline.executionMode", "q1timeline.debounceMs"];
      const original = keys.map((key) => config.inspect(key)?.globalValue);
      const update = (key: string, value: unknown) => config.update(key, value, vscode.ConfigurationTarget.Global);
      const subscriptions: vscode.Disposable[] = [];
      const stored = new Map<string, unknown>();
      const { TimelineController } = require("../../src/q1timeline/controller");
      const controller = new TimelineController({
        extensionPath: extension.extensionPath, extensionUri: extension.extensionUri, subscriptions,
        workspaceState: { get: (key: string, fallback: unknown) => stored.get(key) ?? fallback,
          update: async (key: string, value: unknown) => { stored.set(key, value); } },
      });
      const launches: string[] = [];
      const execFile = controller.execFile.bind(controller);
      controller.execFile = (...args: any[]) => { launches.push(args[5]); return execFile(...args); };
      const value = () => controller.timelineIr?.register_history?.filter((entry: any) => entry.register === "R0").at(-1)?.value?.value;
      const settled = () => !controller.analysisInFlight && !controller.analysisQueued;
      const analyzed = () => launches.filter((stage) => stage === "analyzer").length;
      const isClosed = (file: vscode.Uri) => !vscode.workspace.textDocuments.some((document) => relative(file.fsPath, document.uri.fsPath) === "");
      try {
        await update("q1timeline.updateMode", "onSave");
        await update("q1timeline.executionMode", "project");
        await update("q1timeline.debounceMs", 100);
        await controller.openTarget({ projectFile: yaml.fsPath });
        await waitFor("the initial native Python result", () => value() === 17 && settled());
        assert.ok(isClosed(first) && isClosed(source), "Filesystem watching must work without text document events");
        await pause(400);
        launches.length = 0;
        if (externalProject) {
          await write(source, "move 23,R0\nstop\n");
          await waitFor("the closed external Q1ASM source to refresh", () => value() === 23 && settled());
          await pause(400);
          assert.equal(analyzed(), 1);
          await update("q1timeline.updateMode", "manual");
          const revision = controller.sourceRevision;
          await write(source, "move 29,R0\nstop\n");
          await waitFor("manual external source invalidation", () => controller.sourceRevision > revision);
          await pause(400);
          assert.equal(value(), 23);
          assert.equal(analyzed(), 1, "Manual mode must invalidate without launching analysis");
          await controller.refreshPreview();
          assert.equal(value(), 29);
          assert.equal(analyzed(), 2);
        } else {
          await write(first, '{"VALUE":23}');
          await waitFor("the closed external params file to refresh", () => value() === 23 && settled());
          await pause(400);
          assert.equal(analyzed(), 1);
          await write(yaml, projectText(second));
          await waitFor("the YAML to bind the new external dependency", () => value() === 31 && settled());
          await pause(400);
          const beforeOldWrite = analyzed();
          await write(first, '{"VALUE":47}');
          await pause(600);
          assert.equal(analyzed(), beforeOldWrite, "An old external dependency must not trigger analysis after rebind");
          await write(second, '{"VALUE":41}');
          await waitFor("the newly bound external params file to refresh", () => value() === 41 && settled());
          await pause(400);
          assert.equal(analyzed(), beforeOldWrite + 1);
          await write(source, "move 41,R0\nstop\n");
          await waitFor("the constant source before adding optional params", () => value() === 41 && settled());
          await pause(400);
          const missing = vscode.Uri.joinPath(outside, "missing-parent", "nested", "params.values");
          const hasMissingWarning = () => controller.timelineIr?.diagnostics?.some((item: any) => item.category === "missing_optional_file");
          await write(yaml, projectText(missing));
          await waitFor("a successful analysis with missing optional params", () => hasMissingWarning() && settled());
          await pause(400);
          const beforeCreate = analyzed();
          const filesystem = require("node:fs/promises");
          await filesystem.mkdir(vscode.Uri.joinPath(missing, "..").fsPath, { recursive: true });
          await filesystem.writeFile(missing.fsPath, '{"VALUE":53}');
          await waitFor("creation of an external params file and its previously missing parent", () => !hasMissingWarning() && settled());
          await pause(400);
          assert.equal(analyzed(), beforeCreate + 1);
          assert.ok(isClosed(missing));
          await update("q1timeline.updateMode", "manual");
          const beforeParentDelete = controller.sourceRevision;
          const beforeDeleteAnalysis = analyzed();
          await filesystem.unlink(missing.fsPath);
          await filesystem.rmdir(vscode.Uri.joinPath(missing, "..").fsPath);
          await waitFor("invalidation when an external params parent is removed", () => controller.sourceRevision > beforeParentDelete);
          await pause(400);
          assert.equal(analyzed(), beforeDeleteAnalysis, "Manual mode must not analyze a removed dependency parent");
          await controller.refreshPreview();
          assert.ok(hasMissingWarning());
          const beforeParentRecreate = controller.sourceRevision;
          const beforeRecreateAnalysis = analyzed();
          await filesystem.mkdir(vscode.Uri.joinPath(missing, "..").fsPath);
          await filesystem.writeFile(missing.fsPath, '{"VALUE":59}');
          await waitFor("invalidation when an external params parent is recreated", () => controller.sourceRevision > beforeParentRecreate);
          await pause(400);
          assert.equal(analyzed(), beforeRecreateAnalysis, "Manual mode must wait for Refresh after parent recreation");
          assert.ok(hasMissingWarning());
          await controller.refreshPreview();
          assert.equal(hasMissingWarning(), false);
          assert.equal(analyzed(), beforeRecreateAnalysis + 1);
          assert.ok(isClosed(missing));
        }
        assert.ok(isClosed(first) && isClosed(second) && isClosed(source));
        console.log("Native external dependency result:", JSON.stringify({ externalProject, value: value(), launches }));
      } finally {
        controller.dispose();
        for (const subscription of subscriptions) subscription.dispose();
        await vscode.commands.executeCommand("workbench.action.closeAllEditors");
        for (let index = 0; index < keys.length; index += 1) await update(keys[index], original[index]);
      }
    });
  }
});
