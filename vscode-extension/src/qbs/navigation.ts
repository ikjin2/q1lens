import { isAbsolute, relative, resolve, sep } from "node:path";
import type * as vscodeTypes from "vscode";

export function resolveGeneratedFile(input: { outputDir: string; relativeFile: string }): string {
  const outputDir = resolve(input.outputDir);
  const file = resolve(outputDir, input.relativeFile);
  const relativeFile = relative(outputDir, file);
  if (relativeFile === ".." || relativeFile.startsWith(`..${sep}`) || isAbsolute(relativeFile)) {
    throw new Error(`Generated file escapes output directory: ${input.relativeFile}`);
  }
  return file;
}

export async function openFileAtLine(filePath: string, line: number): Promise<void> {
  const vscode = require("vscode") as typeof vscodeTypes;
  const document = await vscode.workspace.openTextDocument(vscode.Uri.file(filePath));
  const existingGroup = vscode.window.tabGroups.all.find((group) => group.tabs.some((tab) =>
    tab.input instanceof vscode.TabInputText && tab.input.uri.toString() === document.uri.toString(),
  ));
  const editor = await vscode.window.showTextDocument(document, existingGroup?.viewColumn ?? vscode.ViewColumn.Beside);
  revealEditorLine(vscode, editor, line);
}

export async function openFileBelowAtLine(filePath: string, line: number): Promise<void> {
  const vscode = require("vscode") as typeof vscodeTypes;
  const document = await vscode.workspace.openTextDocument(vscode.Uri.file(filePath));
  const existingGroup = vscode.window.tabGroups.all.find((group) => group.tabs.some((tab) =>
    tab.input instanceof vscode.TabInputText && tab.input.uri.toString() === document.uri.toString(),
  ));
  if (!existingGroup) await vscode.commands.executeCommand("workbench.action.newGroupBelow");
  const editor = await vscode.window.showTextDocument(document, {
    viewColumn: existingGroup?.viewColumn ?? vscode.ViewColumn.Active,
    preview: false,
  });
  revealEditorLine(vscode, editor, line);
}

function revealEditorLine(vscode: typeof vscodeTypes, editor: vscodeTypes.TextEditor, line: number): void {
  const zeroBasedLine = Math.max(line - 1, 0);
  const range = new vscode.Range(zeroBasedLine, 0, zeroBasedLine, 0);
  editor.selection = new vscode.Selection(range.start, range.start);
  editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
}

export async function openNotebookAtCell(filePath: string, cellIndex: number, cellLine?: number, cellId?: string): Promise<void> {
  const vscode = require("vscode") as typeof vscodeTypes;
  const notebook = await vscode.workspace.openNotebookDocument(vscode.Uri.file(filePath));
  const existingGroup = vscode.window.tabGroups.all.find((group) => group.tabs.some((tab) =>
    tab.input instanceof vscode.TabInputNotebook && tab.input.uri.toString() === notebook.uri.toString(),
  ));
  const targetCellIndex = cellId
    ? Array.from({ length: notebook.cellCount }, (_value, index) => notebook.cellAt(index)).findIndex(
      (candidate) => candidate.metadata?.id === cellId || candidate.metadata?.custom?.id === cellId,
    )
    : Math.max(cellIndex, 0);
  const cell = targetCellIndex >= 0 && targetCellIndex < notebook.cellCount && typeof notebook.cellAt === "function" ? notebook.cellAt(targetCellIndex) : undefined;
  const notebookEditor = await vscode.window.showNotebookDocument(notebook, {
    viewColumn: existingGroup?.viewColumn ?? vscode.ViewColumn.Beside,
    selections: cell ? [new vscode.NotebookRange(targetCellIndex, targetCellIndex + 1)] : [],
  });
  if (!cell || cell.index < 0 || cell.index >= notebook.cellCount || notebook.cellAt(cell.index) !== cell || typeof cellLine !== "number" || !Number.isFinite(cellLine)) {
    return;
  }
  if (!cell.document) {
    return;
  }
  const editor = await vscode.window.showTextDocument(cell.document, {
    viewColumn: notebookEditor.viewColumn ?? vscode.ViewColumn.Active,
    preview: false,
    preserveFocus: false,
  });
  const zeroBasedLine = Math.max(cellLine - 1, 0);
  const range = new vscode.Range(zeroBasedLine, 0, zeroBasedLine, 0);
  editor.selection = new vscode.Selection(range.start, range.start);
  editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
}
