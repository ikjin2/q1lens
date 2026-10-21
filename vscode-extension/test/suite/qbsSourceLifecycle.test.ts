import assert from "node:assert/strict";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, relative } from "node:path";
import * as vscode from "vscode";

const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

async function waitFor(description: string, check: () => Promise<boolean>): Promise<void> {
  const deadline = Date.now() + 15000;
  let lastError: unknown;
  while (Date.now() < deadline) {
    try { if (await check()) return; } catch (error) { lastError = error; }
    await pause(100);
  }
  assert.fail(`Timed out waiting for ${description}: ${String(lastError ?? "condition not met")}`);
}

function scheduleSource(name: string): string {
  return `class Schedule:
    name = ${JSON.stringify(name)}

    def add(self, operation, *, label):
        return None

def build_schedule():
    schedule = Schedule()
    schedule.add(object(), label="measure")
    return schedule

class Compiler:
    def compile(self, schedule):
        return {
            "name": schedule.name,
            "schedulables": {"measure": {"operation_id": "measure_op", "abs_time": 0.0}},
            "operations": {"measure_op": {"name": "Measure(q0)", "duration": 4e-9}},
            "compiled_instructions": {},
        }

def build_compiler():
    return Compiler()
`;
}

describe("QBS external source lifecycle in VS Code", function () {
  this.timeout(90000);

  it("automatically recovers a closed external schedule after its parent is recreated", async () => {
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const workspace = vscode.workspace.workspaceFolders?.[0]?.uri;
    assert.ok(workspace);
    await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    const name = `qbs-source-lifecycle-${Date.now()}`;
    const projectRoot = vscode.Uri.joinPath(workspace, name);
    const parent = vscode.Uri.joinPath(workspace, "..", name);
    const source = vscode.Uri.joinPath(parent, "schedule.py");
    const project = vscode.Uri.joinPath(projectRoot, "qbstimeline.yml");
    const irFile = vscode.Uri.joinPath(projectRoot, ".qbs_timeline", "qbs_ir.json");
    assert.equal(vscode.workspace.getWorkspaceFolder(source), undefined);
    await mkdir(projectRoot.fsPath);
    await mkdir(parent.fsPath);
    await writeFile(source.fsPath, scheduleSource("initial"));
    await writeFile(project.fsPath, `schedule:\n  file: ${JSON.stringify(source.fsPath.replace(/\\/g, "/"))}\noutputs:\n  dir: .qbs_timeline\nlow_level:\n  q1timeline: false\n`);
    const config = vscode.workspace.getConfiguration("q1lens", project);
    const pythonPath = config.get<string>("pythonPath");
    assert.ok(pythonPath);
    const keys = ["pythonPath", "qbs.autoRefresh", "qbs.outputDirOverride", "revealOutputChannel"];
    const original = keys.map((key) => config.inspect(key)?.globalValue);
    const update = (key: string, value: unknown) => config.update(key, value, vscode.ConfigurationTarget.Global);
    const currentName = async () => JSON.parse(await readFile(irFile.fsPath, "utf8")).schedule.name;
    const sourceClosed = () => !vscode.workspace.textDocuments.some((document) => relative(source.fsPath, document.uri.fsPath) === "");
    try {
      await update("pythonPath", pythonPath);
      await update("qbs.autoRefresh", true);
      await update("qbs.outputDirOverride", ".qbs_timeline");
      await update("revealOutputChannel", false);
      await vscode.window.showTextDocument(project);
      await vscode.commands.executeCommand("qbsTimeline.analyzeAndOpen");
      await waitFor("the initial registered QBS analysis", async () => await currentName() === "initial");
      assert.ok(sourceClosed());
      await pause(500);
      await writeFile(source.fsPath, scheduleSource("watched"));
      await waitFor("the ready external QBS source watcher", async () => await currentName() === "watched");
      await pause(500);
      assert.equal(relative(dirname(workspace.fsPath), parent.fsPath), name);
      await rm(parent.fsPath, { recursive: true });
      await pause(1500);
      await mkdir(parent.fsPath);
      await writeFile(source.fsPath, scheduleSource("restored"));
      let automaticError: unknown;
      try { await waitFor("automatic QBS analysis after external parent recreation", async () => await currentName() === "restored"); }
      catch (error) { automaticError = error; }
      await update("qbs.autoRefresh", false);
      const beforeManual = await currentName();
      await writeFile(source.fsPath, scheduleSource("manual"));
      await pause(1500);
      assert.equal(await currentName(), beforeManual, "Disabling auto refresh must retain the previous analysis");
      await vscode.commands.executeCommand("qbsTimeline.refresh");
      await waitFor("explicit QBS Refresh after recreation", async () => await currentName() === "manual");
      assert.ok(sourceClosed());
      console.log("Native QBS source lifecycle:", JSON.stringify({ automaticRestoration: !automaticError, manualRefresh: await currentName(), sourceClosed: sourceClosed() }));
      if (automaticError) throw automaticError;
    } finally {
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
      for (let index = 0; index < keys.length; index += 1) await update(keys[index], original[index]);
      await vscode.commands.executeCommand("notifications.clearAll");
    }
  });
});
