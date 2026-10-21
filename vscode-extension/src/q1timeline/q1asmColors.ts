import type * as vscodeTypes from "vscode";

interface SyntaxColors {
  color: string;
  lightColor: string;
}

const instructionColors = require("../shared/timeline/instructionColors.js") as {
  families: Record<string, SyntaxColors>;
  argumentStyles: Record<string, SyntaxColors>;
  sourceTokens(raw: string): Array<{ start: number; end: number; kind: string; family?: string }>;
};

export function registerQ1asmColors(
  context: vscodeTypes.ExtensionContext,
  vscode: typeof vscodeTypes,
): void {
  const styles = { ...instructionColors.families, ...instructionColors.argumentStyles };
  const decorations = Object.entries(styles).map(([family, colors]) => ({
    family,
    type: vscode.window.createTextEditorDecorationType({
      color: colors.color,
      light: { color: colors.lightColor },
      dark: { color: colors.color },
      rangeBehavior: vscode.DecorationRangeBehavior.ClosedClosed,
    }),
  }));
  const decoratedEditors = new WeakSet<vscodeTypes.TextEditor>();

  function update(editor: vscodeTypes.TextEditor, document = editor.document): void {
    if (document.languageId !== "q1asm") {
      if (decoratedEditors.has(editor)) {
        for (const { type } of decorations) editor.setDecorations(type, []);
        decoratedEditors.delete(editor);
      }
      return;
    }
    const ranges = new Map(decorations.map(({ family }) => [family, [] as vscodeTypes.Range[]]));
    for (let line = 0; line < document.lineCount; line += 1) {
      const text = document.lineAt(line).text;
      for (const token of instructionColors.sourceTokens(text)) {
        const familyRanges = ranges.get(token.kind === "command" ? token.family || "unknown" : token.kind);
        if (familyRanges) {
          familyRanges.push(new vscode.Range(line, token.start, line, token.end));
        }
      }
    }
    for (const { family, type } of decorations) {
      editor.setDecorations(type, ranges.get(family)!);
    }
    decoratedEditors.add(editor);
  }

  context.subscriptions.push(
    ...decorations.map(({ type }) => type),
    vscode.window.onDidChangeVisibleTextEditors((editors) => editors.forEach((editor) => update(editor))),
    vscode.workspace.onDidOpenTextDocument((document) => {
      for (const editor of vscode.window.visibleTextEditors) {
        if (editor.document.uri.toString() === document.uri.toString()) update(editor, document);
      }
    }),
    vscode.workspace.onDidChangeTextDocument(({ document }) => {
      for (const editor of vscode.window.visibleTextEditors) {
        if (editor.document === document) {
          update(editor);
        }
      }
    }),
  );
  vscode.window.visibleTextEditors.forEach((editor) => update(editor));
}
