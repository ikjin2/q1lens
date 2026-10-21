import assert from "node:assert/strict";
import * as vscode from "vscode";
import { registerQ1asmColors } from "../../src/q1timeline/q1asmColors";

const { families } = require("../../src/shared/timeline/instructionColors.js");

describe("Q1ASM colors in VS Code", function () {
  this.timeout(30000);

  it("updates actual editor decorations when its language changes without a text edit", async () => {
    const document = await vscode.workspace.openTextDocument({ language: "plaintext", content: "play 0, 1, 4\n" });
    await vscode.window.showTextDocument(document);
    const subscriptions: vscode.Disposable[] = [];
    const types = new Map<vscode.TextEditorDecorationType, string>();
    const applied = new Map<string, readonly vscode.Range[]>();
    const editors = new WeakMap<vscode.TextEditor, vscode.TextEditor>();
    const wrap = (actual: vscode.TextEditor): vscode.TextEditor => {
      let editor = editors.get(actual);
      if (!editor) {
        editor = {
          get document() { return actual.document; },
          setDecorations(type: vscode.TextEditorDecorationType, ranges: readonly vscode.Range[]) {
            if (actual.document.uri.toString() === document.uri.toString()) applied.set(types.get(type)!, ranges);
            actual.setDecorations(type, ranges);
          },
        } as vscode.TextEditor;
        editors.set(actual, editor);
      }
      return editor;
    };
    // Forward real events and decoration writes while recording this registration's ranges.
    const api = {
      Range: vscode.Range,
      DecorationRangeBehavior: vscode.DecorationRangeBehavior,
      window: {
        get visibleTextEditors() { return vscode.window.visibleTextEditors.map(wrap); },
        createTextEditorDecorationType(options: vscode.DecorationRenderOptions) {
          const type = vscode.window.createTextEditorDecorationType(options);
          types.set(type, options.color as string);
          return type;
        },
        onDidChangeVisibleTextEditors(listener: (items: readonly vscode.TextEditor[]) => void) {
          return vscode.window.onDidChangeVisibleTextEditors((items) => listener(items.map(wrap)));
        },
      },
      workspace: {
        onDidChangeTextDocument: vscode.workspace.onDidChangeTextDocument,
        onDidOpenTextDocument: vscode.workspace.onDidOpenTextDocument,
      },
    } as unknown as typeof vscode;
    registerQ1asmColors({ subscriptions } as vscode.ExtensionContext, api);
    try {
      const q1asm = await vscode.languages.setTextDocumentLanguage(document, "q1asm");
      assert.equal(q1asm.getText(), "play 0, 1, 4\n");
      assert.deepEqual(applied.get(families.rt.color), [new vscode.Range(0, 0, 0, 4)],
        "Selecting Q1ASM must color existing text without requiring an edit");
      await vscode.languages.setTextDocumentLanguage(q1asm, "plaintext");
      assert.deepEqual(applied.get(families.rt.color), [], "Leaving Q1ASM must clear the old instruction colors");
    } finally {
      subscriptions.forEach((subscription) => subscription.dispose());
      await vscode.commands.executeCommand("workbench.action.revertAndCloseActiveEditor");
    }
  });
});
