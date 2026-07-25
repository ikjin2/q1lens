import { resolve } from "node:path";
import type * as vscodeTypes from "vscode";

export function resolveGeneratedFile(input: { outputDir: string; relativeFile: string }): string {
  const outputDir = resolve(input.outputDir);
  const file = resolve(outputDir, input.relativeFile);
  const normalizedOutput = outputDir.replace(/\\/g, "/");
  const normalizedFile = file.replace(/\\/g, "/");
  if (normalizedFile !== normalizedOutput && !normalizedFile.startsWith(`${normalizedOutput}/`)) {
    throw new Error(`Generated file escapes output directory: ${input.relativeFile}`);
  }
  return file;
}

export async function openFileAtLine(filePath: string, line: number): Promise<void> {
  const vscode = require("vscode") as typeof vscodeTypes;
  const document = await vscode.workspace.openTextDocument(vscode.Uri.file(filePath));
  const editor = await vscode.window.showTextDocument(document, vscode.ViewColumn.Beside);
  revealEditorLine(vscode, editor, line);
}

export async function openFileBelowAtLine(filePath: string, line: number): Promise<void> {
  const vscode = require("vscode") as typeof vscodeTypes;
  const document = await vscode.workspace.openTextDocument(vscode.Uri.file(filePath));
  await vscode.commands.executeCommand("workbench.action.newGroupBelow");
  const editor = await vscode.window.showTextDocument(document, {
    viewColumn: vscode.ViewColumn.Active,
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

export async function openNotebookAtCell(filePath: string, cellIndex: number, cellLine?: number): Promise<void> {
  const vscode = require("vscode") as typeof vscodeTypes;
  const notebook = await vscode.workspace.openNotebookDocument(vscode.Uri.file(filePath));
  await vscode.window.showNotebookDocument(notebook, {
    viewColumn: vscode.ViewColumn.Beside,
    selections: [new vscode.NotebookRange(Math.max(cellIndex, 0), Math.max(cellIndex, 0) + 1)],
  });
  if (typeof cellLine !== "number" || !Number.isFinite(cellLine) || typeof notebook.cellAt !== "function") {
    return;
  }
  const cell = notebook.cellAt(Math.max(cellIndex, 0));
  if (!cell?.document) {
    return;
  }
  const editor = await vscode.window.showTextDocument(cell.document, {
    viewColumn: vscode.ViewColumn.Beside,
    preview: false,
    preserveFocus: false,
  });
  const zeroBasedLine = Math.max(cellLine - 1, 0);
  const range = new vscode.Range(zeroBasedLine, 0, zeroBasedLine, 0);
  editor.selection = new vscode.Selection(range.start, range.start);
  editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
}
