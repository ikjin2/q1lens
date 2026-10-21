import assert from "node:assert/strict";
import * as vscode from "vscode";
import { openFileAtLine, openFileBelowAtLine, openNotebookAtCell } from "../../src/qbs/navigation";

describe("QBS source navigation in VS Code", function () {
  this.timeout(30000);

  it("reuses a notebook source's visible or hidden tab when source navigation is repeated", async () => {
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
    assert.ok(workspaceRoot);
    await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    const source = vscode.Uri.joinPath(workspaceRoot, `schedule-${Date.now()}.ipynb`);
    const other = vscode.Uri.joinPath(workspaceRoot, `notebook-other-${Date.now()}.py`);
    await vscode.workspace.fs.writeFile(source, Buffer.from(JSON.stringify({
      nbformat: 4, nbformat_minor: 5, metadata: {}, cells: [
        { id: "schedule-cell", cell_type: "code", source: ["first = 1\n", "second = 2\n", "third = 3\n"], metadata: {}, execution_count: null, outputs: [] },
      ],
    })));
    await vscode.workspace.fs.writeFile(other, Buffer.from("other = 1\n"));
    const notebook = await vscode.workspace.openNotebookDocument(source);
    const panel = vscode.window.createWebviewPanel("q1lens.testNotebookNavigation", "QBS notebook navigation", vscode.ViewColumn.Active, {});
    const waitForLine = async (line: number) => {
      const deadline = Date.now() + 3000;
      while (vscode.window.activeTextEditor?.document !== notebook.cellAt(0).document || vscode.window.activeTextEditor?.selection.active.line !== line) {
        assert.ok(Date.now() < deadline, "The notebook source line must become active");
        await new Promise((resolve) => setTimeout(resolve, 20));
      }
    };
    try {
      const initialGroups = vscode.window.tabGroups.all.length;
      await openNotebookAtCell(source.fsPath, 0, 1, "schedule-cell");
      await waitForLine(0);
      const sourceGroup = vscode.window.tabGroups.all.find((group) => group.tabs.some((tab) =>
        tab.input instanceof vscode.TabInputNotebook && tab.input.uri.toString() === source.toString(),
      ));
      assert.ok(sourceGroup);
      const groupsWithSource = vscode.window.tabGroups.all.length;
      assert.equal(groupsWithSource, initialGroups + 1);
      await vscode.commands.executeCommand("workbench.action.keepEditor");
      await openNotebookAtCell(source.fsPath, 0, 2, "schedule-cell");
      await waitForLine(1);
      assert.equal(vscode.window.tabGroups.all.length, groupsWithSource, "Repeated notebook navigation must reuse the existing group");
      assert.equal(vscode.window.tabGroups.activeTabGroup.viewColumn, sourceGroup.viewColumn);

      await vscode.window.showTextDocument(other, { viewColumn: sourceGroup.viewColumn, preview: false });
      await openNotebookAtCell(source.fsPath, 0, 3, "schedule-cell");
      await waitForLine(2);
      assert.equal(vscode.window.tabGroups.all.length, groupsWithSource, "A hidden notebook tab must also retain its group");
      assert.equal(vscode.window.tabGroups.activeTabGroup.viewColumn, sourceGroup.viewColumn);
    } finally {
      panel.dispose();
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
      await vscode.workspace.fs.delete(source);
      await vscode.workspace.fs.delete(other);
    }
  });

  it("reuses a Python source's visible or hidden tab when source navigation is repeated from an editor", async () => {
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
    assert.ok(workspaceRoot);
    await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    const source = vscode.Uri.joinPath(workspaceRoot, `schedule-${Date.now()}.py`);
    const other = vscode.Uri.joinPath(workspaceRoot, `other-${Date.now()}.py`);
    await vscode.workspace.fs.writeFile(source, Buffer.from("first = 1\nsecond = 2\nthird = 3\n"));
    await vscode.workspace.fs.writeFile(other, Buffer.from("other = 1\n"));
    const panel = vscode.window.createWebviewPanel("q1lens.testScheduleNavigation", "QBS schedule navigation", vscode.ViewColumn.Active, {});
    try {
      const initialGroups = vscode.window.tabGroups.all.length;
      await openFileAtLine(source.fsPath, 1);
      const sourceColumn = vscode.window.activeTextEditor?.viewColumn;
      assert.ok(sourceColumn);
      const groupsWithSource = vscode.window.tabGroups.all.length;
      assert.equal(groupsWithSource, initialGroups + 1);
      await vscode.commands.executeCommand("workbench.action.keepEditor");
      await openFileAtLine(source.fsPath, 2);
      assert.equal(vscode.window.tabGroups.all.length, groupsWithSource, "Repeated source navigation must reuse the existing group");
      assert.equal(vscode.window.activeTextEditor?.viewColumn, sourceColumn);
      assert.equal(vscode.window.activeTextEditor?.selection.active.line, 1);

      await vscode.window.showTextDocument(other, { viewColumn: sourceColumn, preview: false });
      await openFileAtLine(source.fsPath, 3);
      assert.equal(vscode.window.tabGroups.all.length, groupsWithSource, "A hidden Python source tab must also retain its group");
      assert.equal(vscode.window.activeTextEditor?.viewColumn, sourceColumn);
      assert.equal(vscode.window.activeTextEditor?.selection.active.line, 2);
    } finally {
      panel.dispose();
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
      await vscode.workspace.fs.delete(source);
      await vscode.workspace.fs.delete(other);
    }
  });

  it("reuses a generated source's editor group when its visible or hidden tab is reopened", async () => {
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
    assert.ok(workspaceRoot);
    await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    const source = vscode.Uri.joinPath(workspaceRoot, `generated-source-${Date.now()}.q1asm`);
    const other = vscode.Uri.joinPath(workspaceRoot, `other-source-${Date.now()}.q1asm`);
    await vscode.workspace.fs.writeFile(source, Buffer.from("play 0, 0, 4\nwait 4\nstop\n"));
    await vscode.workspace.fs.writeFile(other, Buffer.from("stop\n"));
    const panel = vscode.window.createWebviewPanel("q1lens.testSourceNavigation", "QBS source navigation", vscode.ViewColumn.Active, {});
    const focusPanel = async () => {
      panel.reveal();
      const deadline = Date.now() + 3000;
      const isPanelActive = () => {
        const input = vscode.window.tabGroups.activeTabGroup.activeTab?.input;
        return panel.active && input instanceof vscode.TabInputWebview && input.viewType.includes("q1lens.testSourceNavigation");
      };
      while (!isPanelActive()) {
        assert.ok(Date.now() < deadline, `The source's timeline panel must become active: ${JSON.stringify({
          panelActive: panel.active, panelVisible: panel.visible, panelColumn: panel.viewColumn,
          groups: vscode.window.tabGroups.all.map((group) => ({ column: group.viewColumn,
            active: group.isActive, tabs: group.tabs.map((tab) => ({ label: tab.label, active: tab.isActive,
              type: tab.input instanceof vscode.TabInputWebview ? tab.input.viewType : "text" })) })),
        })}`);
        await new Promise((resolve) => setTimeout(resolve, 20));
      }
    };
    try {
      await focusPanel();
      const initialGroups = vscode.window.tabGroups.all.length;
      await openFileBelowAtLine(source.fsPath, 1);
      const sourceColumn = vscode.window.activeTextEditor?.viewColumn;
      assert.ok(sourceColumn);
      const groupsWithSource = vscode.window.tabGroups.all.length;
      assert.equal(groupsWithSource, initialGroups + 1);
      await focusPanel();
      await openFileBelowAtLine(source.fsPath, 2);
      assert.equal(vscode.window.tabGroups.all.length, groupsWithSource, "Reopening the same source must not add an editor group");
      assert.equal(vscode.window.activeTextEditor?.viewColumn, sourceColumn);
      assert.equal(vscode.window.activeTextEditor?.selection.active.line, 1);

      await vscode.window.showTextDocument(other, { viewColumn: sourceColumn, preview: false });
      await focusPanel();
      await openFileBelowAtLine(source.fsPath, 3);
      assert.equal(vscode.window.tabGroups.all.length, groupsWithSource, "A hidden source tab must also retain its group");
      assert.equal(vscode.window.activeTextEditor?.viewColumn, sourceColumn);
      assert.equal(vscode.window.activeTextEditor?.selection.active.line, 2);
      const sourceTab = vscode.window.tabGroups.all.flatMap((group) => group.tabs).find((tab) =>
        tab.input instanceof vscode.TabInputText && tab.input.uri.toString() === source.toString(),
      );
      assert.ok(sourceTab);
      await focusPanel();
      console.info("Native generated-source close target:", JSON.stringify({
        source: sourceTab.label,
        active: vscode.window.tabGroups.activeTabGroup.activeTab?.label,
      }));
      assert.equal(await vscode.window.tabGroups.close(sourceTab, true), true);
      const closeDeadline = Date.now() + 3000;
      while (vscode.window.tabGroups.all.some((group) => group.tabs.some((tab) =>
        tab.input instanceof vscode.TabInputText && tab.input.uri.toString() === source.toString(),
      ))) {
        assert.ok(Date.now() < closeDeadline, "The exact source tab must close before refocusing its timeline");
        await new Promise((resolve) => setTimeout(resolve, 20));
      }
      await focusPanel();
      await openFileBelowAtLine(source.fsPath, 1);
      assert.equal(vscode.window.tabGroups.all.length, groupsWithSource + 1, "A closed source must still open below the timeline");
    } finally {
      panel.dispose();
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  });
});
