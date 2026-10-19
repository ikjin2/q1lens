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

  function update(editor: vscodeTypes.TextEditor): void {
    if (editor.document.languageId !== "q1asm") {
      return;
    }
    const ranges = new Map(decorations.map(({ family }) => [family, [] as vscodeTypes.Range[]]));
    for (let line = 0; line < editor.document.lineCount; line += 1) {
      const text = editor.document.lineAt(line).text;
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
  }

  context.subscriptions.push(
    ...decorations.map(({ type }) => type),
    vscode.window.onDidChangeVisibleTextEditors((editors) => editors.forEach(update)),
    vscode.workspace.onDidChangeTextDocument(({ document }) => {
      for (const editor of vscode.window.visibleTextEditors) {
        if (editor.document === document) {
          update(editor);
        }
      }
    }),
  );
  vscode.window.visibleTextEditors.forEach(update);
}
