import assert from "node:assert/strict";
import Module = require("node:module");
import { join, parse, resolve } from "node:path";
import { openFileAtLine, openFileBelowAtLine, openNotebookAtCell, resolveGeneratedFile } from "../src/qbs/navigation";

async function withVscode(vscode: any, callback: () => Promise<void>): Promise<void> {
  const originalLoad = (Module as any)._load;
  (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
    return request === "vscode" ? vscode : originalLoad.call(this, request, parent, isMain);
  };
  try {
    await callback();
  } finally {
    (Module as any)._load = originalLoad;
  }
}

describe("navigation", () => {
  for (const state of ["new", "visible", "hidden"]) {
    it(`opens an ordinary source in its ${state === "new" ? "default side group" : `${state} tab's existing group`}`, async () => {
      const uri = { toString: () => "file:///project/schedule.py" };
      const otherUri = { toString: () => "file:///project/other.py" };
      class TabInputText { constructor(public uri: { toString(): string }) {} }
      const sourceTab = { input: new TabInputText(uri) };
      const shownColumns: number[] = [];
      const editor = { selection: undefined as any, revealRange() {} };
      const vscode = {
        Uri: { file: () => uri }, TabInputText,
        ViewColumn: { Beside: -2 }, TextEditorRevealType: { InCenter: 1 },
        Range: class { start: { line: number; character: number }; constructor(line: number, character: number) { this.start = { line, character }; } },
        Selection: class { constructor(public start: any, public end: any) {} },
        workspace: { openTextDocument: async () => ({ uri }) },
        window: {
          tabGroups: { all: [
            { viewColumn: 2, tabs: [{ input: { uri } }] },
            { viewColumn: 3, tabs: state === "new" ? [] : state === "visible" ? [sourceTab] : [{ input: new TabInputText(otherUri) }, sourceTab] },
          ] },
          showTextDocument: async (_document: unknown, column: number) => { shownColumns.push(column); return editor; },
        },
      };

      await withVscode(vscode, () => openFileAtLine("/project/schedule.py", 5));

      assert.deepEqual(shownColumns, [state === "new" ? -2 : 3]);
      assert.equal(editor.selection.start.line, 4);
    });
  }

  for (const state of ["new", "visible", "hidden"]) {
    it(`opens a notebook source in its ${state === "new" ? "default side group" : `${state} tab's existing group`}`, async () => {
      const uri = { toString: () => "file:///project/schedule.ipynb" };
      class TabInputNotebook { constructor(public uri: { toString(): string }) {} }
      const sourceTab = { input: new TabInputNotebook(uri) };
      const cell = { index: 0, document: {} };
      const notebook = { uri, cellCount: 1, cellAt: () => cell };
      const columns: number[] = [];
      const editor = { selection: undefined as any, revealRange() {} };
      const vscode = {
        Uri: { file: () => uri }, TabInputNotebook,
        ViewColumn: { Beside: -2 }, TextEditorRevealType: { InCenter: 1 },
        NotebookRange: class { constructor(public start: number, public end: number) {} },
        Range: class { start: any; constructor(line: number, character: number) { this.start = { line, character }; } },
        Selection: class { constructor(public start: any, public end: any) {} },
        workspace: { openNotebookDocument: async () => notebook },
        window: {
          tabGroups: { all: [
            { viewColumn: 2, tabs: [{ input: { uri } }] },
            { viewColumn: 3, tabs: state === "new" ? [] : state === "visible" ? [sourceTab] : [{ input: {} }, sourceTab] },
          ] },
          showNotebookDocument: async (_notebook: unknown, options: any) => { columns.push(options.viewColumn); return { viewColumn: 3 }; },
          showTextDocument: async (_document: unknown, options: any) => { columns.push(options.viewColumn); return editor; },
        },
      };

      await withVscode(vscode, () => openNotebookAtCell("/project/schedule.ipynb", 0, 5));

      assert.deepEqual(columns, [state === "new" ? -2 : 3, 3]);
      assert.equal(editor.selection.start.line, 4);
    });
  }

  it("resolves generated files inside filesystem and Windows share roots", () => {
    const roots = [parse(resolve(".")).root];
    if (process.platform === "win32") roots.push("\\\\server\\share\\");
    for (const outputDir of roots) {
      assert.equal(resolveGeneratedFile({ outputDir, relativeFile: "q1asm/seq0.q1asm" }), join(outputDir, "q1asm", "seq0.q1asm"));
    }
  });

  it("allows two-dot-prefixed children while rejecting parents, ancestors, and adjacent output folders", () => {
    const outputDir = resolve("artifacts", "generated");
    assert.equal(resolveGeneratedFile({ outputDir, relativeFile: "..not-parent/seq0.q1asm" }), join(outputDir, "..not-parent", "seq0.q1asm"));
    for (const relativeFile of ["..", "../..", "../generated-adjacent/seq0.q1asm", "../..not-parent/seq0.q1asm"]) {
      assert.throws(() => resolveGeneratedFile({ outputDir, relativeFile }), /Generated file escapes output directory/);
    }
  });

  it("rejects other Windows drives and shares while retaining Windows path casing semantics", () => {
    if (process.platform !== "win32") return;
    assert.equal(resolveGeneratedFile({ outputDir: "C:\\ARTIFACTS", relativeFile: "c:\\artifacts\\q1asm\\seq0.q1asm" }), "c:\\artifacts\\q1asm\\seq0.q1asm");
    for (const [outputDir, relativeFile] of [
      ["C:\\artifacts", "D:\\artifacts\\seq0.q1asm"],
      ["\\\\server\\share\\", "\\\\server\\other-share\\q1asm\\seq0.q1asm"],
      ["\\\\server\\share\\", "\\\\other-server\\share\\q1asm\\seq0.q1asm"],
    ]) {
      assert.throws(() => resolveGeneratedFile({ outputDir, relativeFile }), /Generated file escapes output directory/);
    }
  });

  for (const legacy of [false, true]) {
    for (const change of ["inserted", "deleted", "earlier cell removed"]) {
      it(`follows ${legacy ? "legacy" : "default"} notebook cell IDs after a cell is ${change}`, async () => {
        const ids = change === "inserted" ? ["inserted", "before", "target"]
          : change === "deleted" ? ["before", "replacement"] : ["target"];
        const cells = ids.map((id, index) => ({
          index, metadata: legacy ? { custom: { id, metadata: {} } } : { id, metadata: {} }, document: { id },
        }));
        const notebook = { cellCount: cells.length, cellAt: (index: number) => cells[index] };
        const opened: string[] = [];
        let selections: any[] = [];
        const vscode = {
          Uri: { file: (fsPath: string) => ({ fsPath }) },
          ViewColumn: { Active: -1, Beside: -2 },
          NotebookRange: class { constructor(public start: number, public end: number) {} },
          Range: class { start = {}; }, Selection: class {}, TextEditorRevealType: { InCenter: 1 },
          workspace: { openNotebookDocument: async () => notebook },
          window: {
            tabGroups: { all: [] },
            showNotebookDocument: async (_notebook: any, options: any) => { selections = options.selections; return { viewColumn: 2 }; },
            showTextDocument: async (document: { id: string }) => {
              opened.push(document.id);
              return { selection: undefined, revealRange() {} };
            },
          },
        };
        await withVscode(vscode, () => openNotebookAtCell("C:\\repo\\notebook.ipynb", 1, 3, "target"));
        assert.deepEqual(opened, change === "deleted" ? [] : ["target"]);
        assert.deepEqual(selections.map((selection) => selection.start), change === "deleted" ? [] : [ids.indexOf("target")]);
      });
    }
  }
  it("resolves generated Q1ASM files under the output directory", () => {
    const resolved = resolveGeneratedFile({
      outputDir: "C:\\repo\\.qbs_timeline",
      relativeFile: "q1asm/cluster0_module4_seq0.q1asm",
    });

    assert.equal(resolved.replace(/\\/g, "/"), "C:/repo/.qbs_timeline/q1asm/cluster0_module4_seq0.q1asm");
  });

  it("rejects paths that escape the output directory", () => {
    assert.throws(
      () => resolveGeneratedFile({ outputDir: "C:\\repo\\.qbs_timeline", relativeFile: "../schedule.py" }),
      /Generated file escapes output directory/,
    );
  });

  it("opens files in a below editor group", async () => {
    const commands: string[] = [];
    let shownOptions: any = undefined;
    const originalLoad = (Module as any)._load;
    (Module as any)._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
      if (request === "vscode") {
        return {
          Uri: { file: (fsPath: string) => ({ fsPath }) },
          ViewColumn: { Active: -1, Beside: 2 },
          Range: class {
            start: any;
            constructor(public startLine: number, public startColumn: number) {
              this.start = { line: startLine, character: startColumn };
            }
          },
          Selection: class {
            constructor(public start: any, public end: any) {}
          },
          workspace: {
            openTextDocument: async (uri: any) => ({ uri }),
          },
          window: {
            tabGroups: { all: [] },
            showTextDocument: async (_document: any, options: any) => {
              shownOptions = options;
              return {
                set selection(_value: any) {},
                revealRange: () => undefined,
              };
            },
          },
          commands: {
            executeCommand: async (command: string) => {
              commands.push(command);
            },
          },
          TextEditorRevealType: { InCenter: 1 },
        };
      }
      return originalLoad.call(this, request, parent, isMain);
    };
    try {
      await openFileBelowAtLine("C:\\repo\\q1asm\\seq0.q1asm", 3);
    } finally {
      (Module as any)._load = originalLoad;
    }

    assert.deepEqual(commands, ["workbench.action.newGroupBelow"]);
    assert.deepEqual(shownOptions, { viewColumn: -1, preview: false });
  });

  it("reveals a notebook cell line in the group where the notebook was opened", async () => {
    const cellDocument = { uri: { scheme: "vscode-notebook-cell" } };
    const cell = { index: 1, document: cellDocument };
    const notebook = { cellCount: 3, cellAt: () => cell };
    const notebookOptions: any[] = [];
    const textOptions: any[] = [];
    const selections: any[] = [];
    let editorGroups = 1;
    const vscode = {
      Uri: { file: (fsPath: string) => ({ fsPath }) },
      ViewColumn: { Active: -1, Beside: -2 },
      NotebookRange: class { constructor(public start: number, public end: number) {} },
      Range: class {
        start: any;
        constructor(line: number, character: number) { this.start = { line, character }; }
      },
      Selection: class { constructor(public start: any, public end: any) {} },
      TextEditorRevealType: { InCenter: 1 },
      workspace: { openNotebookDocument: async () => notebook },
      window: {
        tabGroups: { all: [] },
        showNotebookDocument: async (_notebook: any, options: any) => {
          notebookOptions.push(options);
          if (options.viewColumn === vscode.ViewColumn.Beside) editorGroups += 1;
          return { viewColumn: editorGroups };
        },
        showTextDocument: async (document: any, options: any) => {
          assert.equal(document, cellDocument);
          textOptions.push(options);
          if (options.viewColumn === vscode.ViewColumn.Beside) editorGroups += 1;
          return {
            set selection(value: any) { selections.push(value); },
            revealRange() {},
          };
        },
      },
    };

    await withVscode(vscode, () => openNotebookAtCell("C:\\repo\\notebook.ipynb", 1, 3));

    assert.equal(editorGroups, 2);
    assert.equal(notebookOptions[0].selections[0].start, 1);
    assert.equal(textOptions[0].viewColumn, 2);
    assert.deepEqual(selections[0].start, { line: 2, character: 0 });
  });

  it("opens an empty notebook without trying to reveal a nonexistent source cell", async () => {
    let shownOptions: any;
    const vscode = {
      Uri: { file: (fsPath: string) => ({ fsPath }) },
      ViewColumn: { Active: -1, Beside: -2 },
      NotebookRange: class { constructor(public start: number, public end: number) {} },
      workspace: {
        openNotebookDocument: async () => ({
          cellCount: 0,
          cellAt: () => { throw new Error("No notebook cells remain"); },
        }),
      },
      window: {
        tabGroups: { all: [] },
        showNotebookDocument: async (_notebook: any, options: any) => {
          shownOptions = options;
          return { viewColumn: 2 };
        },
        showTextDocument: async () => assert.fail("An empty notebook has no source document"),
      },
    };

    await withVscode(vscode, () => openNotebookAtCell("C:\\repo\\notebook.ipynb", 5, 3));

    assert.deepEqual(shownOptions.selections, []);
  });

  for (const removedDuringOpen of [false, true]) {
    it(`opens the notebook without revealing a cell deleted ${removedDuringOpen ? "during opening" : "since analysis"}`, async () => {
      let selections: any[] = [];
      const requestedCells: number[] = [];
      const notebook = {
        cellCount: removedDuringOpen ? 3 : 1,
        cellAt: (index: number) => {
          requestedCells.push(index);
          // VS Code adjusts an out-of-range index to an existing cell.
          return { index: Math.min(Math.max(index, 0), notebook.cellCount - 1), document: { uri: { scheme: "vscode-notebook-cell", path: "/remaining-cell" } } };
        },
      };
      const vscode = {
        Uri: { file: (fsPath: string) => ({ fsPath }) },
        ViewColumn: { Active: -1, Beside: -2 },
        NotebookRange: class { constructor(public start: number, public end: number) {} },
        workspace: { openNotebookDocument: async () => notebook },
        window: {
          tabGroups: { all: [] },
          showNotebookDocument: async (_notebook: any, options: any) => {
            selections = options.selections;
            if (removedDuringOpen) notebook.cellCount = 1;
            return { viewColumn: 2 };
          },
          showTextDocument: async () => assert.fail("Only the notebook can be opened for a deleted cell"),
        },
      };
      await withVscode(vscode, () => openNotebookAtCell("C:\\repo\\notebook.ipynb", 2, 3));
      assert.deepEqual(requestedCells, removedDuringOpen ? [2] : []);
      if (!removedDuringOpen) assert.deepEqual(selections, []);
    });
  }

  for (const deleteTarget of [true, false]) {
    it(deleteTarget ? "does not substitute another cell after its target is deleted during opening" : "follows the original notebook cell when earlier cells disappear during opening", async () => {
      const cells = ["A", "B", "C"].map((name, index) => ({ index, document: { name } }));
      const opened: string[] = [];
      const notebook = { get cellCount() { return cells.length; }, cellAt: (index: number) => cells[index] };
      const vscode = {
        Uri: { file: (fsPath: string) => ({ fsPath }) },
        ViewColumn: { Active: -1, Beside: -2 },
        NotebookRange: class { constructor(public start: number, public end: number) {} },
        Range: class { start = {}; },
        Selection: class {},
        TextEditorRevealType: { InCenter: 1 },
        workspace: { openNotebookDocument: async () => notebook },
        window: {
          tabGroups: { all: [] },
          showNotebookDocument: async () => {
            cells.splice(deleteTarget ? 1 : 0, 1)[0].index = -1;
            cells.forEach((cell, index) => { cell.index = index; });
            return { viewColumn: 2 };
          },
          showTextDocument: async (document: { name: string }) => {
            opened.push(document.name);
            return { selection: undefined, revealRange() {} };
          },
        },
      };
      await withVscode(vscode, () => openNotebookAtCell("C:\\repo\\notebook.ipynb", 1, 3));
      assert.deepEqual(opened, deleteTarget ? [] : ["B"]);
    });
  }
});
