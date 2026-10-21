import assert from "node:assert/strict";
import * as vscode from "vscode";
import { TimelinePanel } from "../../src/qbs/webview/panel";

describe("QBS webview state in VS Code", function () {
  this.timeout(30000);

  it("keeps selection and zoom when another editor hides its tab", async () => {
    await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    const extension = vscode.extensions.getExtension("q1lens.q1lens");
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
    assert.ok(extension && workspaceRoot);
    const noop = async () => undefined;
    const timeline = new TimelinePanel(extension.extensionUri, {
      onRefresh: noop, onOpenQ1Timeline: noop, onOpenQ1AsmSource: noop,
      onOpenScheduleSource: noop, onOpenIr: noop, onOpenProjectFile: noop,
      onOpenScheduleFile: noop, onOpenNotebookFile: noop,
    });
    let messages: vscode.Disposable | undefined;
    try {
      const ir = {
        schedule: { name: "Tab state" },
        operations: [
          { id: "first", label: "First(q0)", abs_time: 0, duration: 40e-9 },
          { id: "second", label: "Second(q0)", abs_time: 50e-9, duration: 40e-9 },
        ],
        symbolic_values: [], symbolic_pulses: [], q1asm_programs: [], q1asm_provenance: [],
      };
      timeline.show(ir);
      const panel: vscode.WebviewPanel = (timeline as any).panel;
      const responses = new Map<number, any>();
      messages = panel.webview.onDidReceiveMessage((message) => {
        if (message.type === "testQbsState") responses.set(message.id, message.state);
      });
      const html = panel.webview.html;
      const nonce = html.match(/<script nonce="([^"]+)"/)?.[1];
      assert.ok(nonce);
      panel.webview.html = html.replace("</body>", `<script nonce="${nonce}">
window.addEventListener("message", ({data}) => {
  if (data.type !== "testQbsState") return;
  const block = document.querySelector('[data-block-id="second"]');
  if (!block) return;
  if (data.prepare) {
    block.click();
    document.querySelector('[title="Zoom in"]').click();
  }
  vscode.postMessage({type:"testQbsState",id:data.id,state:{
    selected:[...document.querySelectorAll('.timeline-block[aria-selected="true"]')].map(node=>node.dataset.blockId),
    inspector:document.querySelector(".inspector h2").textContent,
    viewport:document.querySelector(".stage-total").textContent
  }});
});
</script></body>`);
      let nextId = 0;
      const state = async (prepare = false) => {
        const deadline = Date.now() + 10000;
        while (Date.now() < deadline) {
          const id = ++nextId;
          await panel.webview.postMessage({ type: "testQbsState", id, prepare });
          for (let attempt = 0; attempt < 10; attempt += 1) {
            if (responses.has(id)) return responses.get(id);
            await new Promise((resolve) => setTimeout(resolve, 25));
          }
        }
        assert.fail("The production QBS webview did not report its rendered state");
      };
      const selected = await state(true);
      assert.deepEqual(selected.selected, ["second"]);
      assert.equal(selected.inspector, "Second(q0)");
      assert.ok(!selected.viewport.includes("view 0 ns - 90 ns"), "The test must actually zoom the production timeline");
      const other = vscode.Uri.joinPath(workspaceRoot, `hidden-qbs-state-${Date.now()}.txt`);
      await vscode.workspace.fs.writeFile(other, Buffer.from("Another editor\n"));
      await vscode.window.showTextDocument(other, { viewColumn: panel.viewColumn, preview: false });
      assert.equal(panel.visible, false);
      await new Promise((resolve) => setTimeout(resolve, 500));
      panel.reveal(panel.viewColumn);
      const restored = await state();
      assert.deepEqual(restored, selected, "Returning to the same QBS tab must preserve its selected block and viewport");
      await vscode.window.showTextDocument(other, { viewColumn: panel.viewColumn, preview: false });
      assert.equal(panel.visible, false);
      timeline.show({ ...ir, operations: [ir.operations[0], { ...ir.operations[1], label: "Updated(q0)" }] });
      await new Promise((resolve) => setTimeout(resolve, 500));
      panel.reveal(panel.viewColumn);
      const updated = await state();
      assert.deepEqual(updated.selected, ["second"]);
      assert.equal(updated.inspector, "Updated(q0)", "Analysis updates received while hidden must still appear");
      assert.equal(updated.viewport, selected.viewport);
    } finally {
      messages?.dispose();
      timeline.dispose();
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  });
});
