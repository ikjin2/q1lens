import assert from "node:assert/strict";
import * as vscode from "vscode";

describe("Acquisition windows in the VS Code extension host", function () {
  this.timeout(60000);

  it("opens saved hardware settings through the registered preview command", async () => {
    await vscode.extensions.getExtension("q1lens.q1lens")?.activate();
    const workspace = vscode.workspace.workspaceFolders?.[0]?.uri;
    assert.ok(workspace);
    const root = vscode.Uri.joinPath(workspace, `acquisition-host-check-${Date.now()}`);
    const source = vscode.Uri.joinPath(root, "readout.q1asm");
    await vscode.workspace.fs.createDirectory(root);
    await vscode.workspace.fs.writeFile(source, Buffer.from("wait_sync 4\nacquire 0,0,4\nwait 596\nacquire 1,2,4\nwait 996\nstop\n"));
    await vscode.workspace.fs.writeFile(vscode.Uri.joinPath(root, "q1timeline.yml"), Buffer.from(
      "sequencers:\n  - id: readout\n    file: readout.q1asm\n    module_type: QRM\n    sequencer_index: 0\n"
      + "    hardware_parameters:\n      integration_length_acq: 1000\n      scope_acq_sequencer_select: 0\n"
      + "      scope_acq_trigger_mode_path0: sequencer\n      scope_acq_trigger_mode_path1: sequencer\n"));
    try {
      await vscode.window.showTextDocument(source);
      await vscode.commands.executeCommand("q1timeline.openPreview", source);
      const output = vscode.Uri.joinPath(root, ".q1timeline", "timeline_ir.json");
      let ir: any;
      const deadline = Date.now() + 30000;
      while (Date.now() < deadline) {
        try {
          ir = JSON.parse(Buffer.from(await vscode.workspace.fs.readFile(output)).toString("utf8"));
          if (ir.events?.length) break;
        } catch {}
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      assert.ok(ir, "The registered preview command should produce a fresh TimelineIR");
      const acquires = ir.events.filter((event: any) => event.kind === "acquire");
      assert.equal(acquires.length, 2);
      assert.deepEqual(acquires.map((event: any) => event.duration.value), [4, 4]);
      assert.deepEqual(acquires.map((event: any) => event.meta.acquisition_window.effective_duration_ns), [600, 1000]);
      assert.equal(acquires[0].meta.acquisition_window.status, "interrupted");
      assert.equal(acquires[0].meta.acquisition_window.interrupted_by_event_id, acquires[1].id);
      assert.deepEqual(acquires.map((event: any) => event.meta.scope_acquisition_window.effective_duration_ns), [600, 16384]);
      assert.equal(acquires[0].meta.scope_acquisition_window.status, "retriggered");
      assert.ok(vscode.window.tabGroups.all.flatMap(group => group.tabs).some(tab => tab.input instanceof vscode.TabInputWebview));
    } finally {
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  });
});
