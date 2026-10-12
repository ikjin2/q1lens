import * as path from "node:path";
import type * as vscodeTypes from "vscode";

export const DIAGNOSTIC_SUGGESTED_EDIT_PROPERTY = "q1lensSuggestedEdit";

interface TextPoint {
  line: number;
  column: number;
}

interface TextRange {
  start: TextPoint;
  end: TextPoint;
}

export interface SuggestedTextEdit {
  title: string;
  file: string;
  range: TextRange;
  newText: string;
  expectedLine?: string;
}

interface MappedDiagnosticRange {
  startLine: number;
  startColumn: number;
  endLine: number;
  endColumn: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeFilePath(file: string): string {
  const normalized = path.resolve(file);
  return process.platform === "win32" ? normalized.toLowerCase() : normalized;
}

function isSameQ1asmFile(left: string, right: string): boolean {
  return (
    path.extname(left).toLowerCase() === ".q1asm"
    && path.extname(right).toLowerCase() === ".q1asm"
    && normalizeFilePath(left) === normalizeFilePath(right)
  );
}

function position(value: unknown, base: 0 | 1): TextPoint | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  const line = value.line;
  const column = value.column;
  if (
    !Number.isInteger(line)
    || !Number.isInteger(column)
    || (line as number) < base
    || (column as number) < base
  ) {
    return undefined;
  }
  return { line: (line as number) - base, column: (column as number) - base };
}

function compare(left: TextPoint, right: TextPoint): number {
  return left.line === right.line ? left.column - right.column : left.line - right.line;
}

function validRange(range: TextRange): boolean {
  return compare(range.start, range.end) <= 0;
}

function contains(container: TextRange, candidate: TextRange): boolean {
  return validRange(candidate)
    && compare(container.start, candidate.start) <= 0
    && compare(candidate.end, container.end) <= 0;
}

function mappedRange(range: MappedDiagnosticRange): TextRange {
  return {
    start: { line: range.startLine, column: range.startColumn },
    end: { line: range.endLine, column: range.endColumn },
  };
}

/** Validate a one-based analyzer edit before retaining it on a diagnostic. */
export function mapAnalyzerSuggestedEdit(input: {
  value: unknown;
  diagnosticFile: string;
  diagnosticRange: MappedDiagnosticRange;
  diagnosticSourceRaw?: unknown;
  resolveSourcePath(sourceFile: string): string;
}): SuggestedTextEdit | undefined {
  const value = input.value;
  if (
    !isRecord(value)
    || !isRecord(value.range)
    || typeof value.title !== "string"
    || value.title.trim().length === 0
    || typeof value.file !== "string"
    || value.file.trim().length === 0
    || typeof value.new_text !== "string"
  ) {
    return undefined;
  }
  const start = position(value.range.start, 1);
  const end = position(value.range.end, 1);
  if (!start || !end) {
    return undefined;
  }

  let file: string;
  try {
    file = input.resolveSourcePath(value.file);
  } catch {
    return undefined;
  }
  const range = { start, end };
  if (
    typeof file !== "string"
    || !isSameQ1asmFile(file, input.diagnosticFile)
    || !contains(mappedRange(input.diagnosticRange), range)
  ) {
    return undefined;
  }
  return {
    title: value.title.trim(), file, range, newText: value.new_text,
    ...(typeof input.diagnosticSourceRaw === "string" ? { expectedLine: input.diagnosticSourceRaw } : {}),
  };
}

function validatedEdit(value: unknown): SuggestedTextEdit | undefined {
  if (
    !isRecord(value)
    || typeof value.title !== "string"
    || value.title.trim().length === 0
    || typeof value.file !== "string"
    || typeof value.newText !== "string"
    || !isRecord(value.range)
  ) {
    return undefined;
  }
  const start = position(value.range.start, 0);
  const end = position(value.range.end, 0);
  return start && end && validRange({ start, end })
    ? {
        title: value.title, file: value.file, range: { start, end }, newText: value.newText,
        ...(typeof value.expectedLine === "string" ? { expectedLine: value.expectedLine } : {}),
      }
    : undefined;
}

function diagnosticRange(diagnostic: vscodeTypes.Diagnostic): TextRange | undefined {
  const start = diagnostic.range && position({
    line: diagnostic.range.start.line,
    column: diagnostic.range.start.character,
  }, 0);
  const end = diagnostic.range && position({
    line: diagnostic.range.end.line,
    column: diagnostic.range.end.character,
  }, 0);
  return start && end && validRange({ start, end }) ? { start, end } : undefined;
}

function rangeFitsDocument(document: vscodeTypes.TextDocument, range: TextRange): boolean {
  if (range.start.line >= document.lineCount || range.end.line >= document.lineCount) {
    return false;
  }
  try {
    return (
      range.start.column <= document.lineAt(range.start.line).text.length
      && range.end.column <= document.lineAt(range.end.line).text.length
    );
  } catch {
    return false;
  }
}

type DiagnosticWithSuggestedEdit = vscodeTypes.Diagnostic & {
  [DIAGNOSTIC_SUGGESTED_EDIT_PROPERTY]?: unknown;
};

const suggestedEdits = new Map<string, SuggestedTextEdit>();

function diagnosticKey(file: string, diagnostic: vscodeTypes.Diagnostic): string {
  const range = diagnosticRange(diagnostic);
  const code = isRecord(diagnostic.code) ? diagnostic.code.value : diagnostic.code;
  return JSON.stringify([
    normalizeFilePath(file),
    range,
    diagnostic.message,
    diagnostic.source,
    code,
  ]);
}

export function clearSuggestedEdits(): void {
  suggestedEdits.clear();
}

export function preserveSuggestedEdit(
  file: string,
  diagnostic: vscodeTypes.Diagnostic,
  edit: SuggestedTextEdit,
): void {
  (diagnostic as DiagnosticWithSuggestedEdit)[DIAGNOSTIC_SUGGESTED_EDIT_PROPERTY] = edit;
  suggestedEdits.set(diagnosticKey(file, diagnostic), edit);
}

function editForDiagnostic(file: string, diagnostic: vscodeTypes.Diagnostic): SuggestedTextEdit | undefined {
  const attached = (diagnostic as DiagnosticWithSuggestedEdit)[DIAGNOSTIC_SUGGESTED_EDIT_PROPERTY];
  return validatedEdit(attached) || suggestedEdits.get(diagnosticKey(file, diagnostic));
}

export class Q1asmQuickFixProvider implements vscodeTypes.CodeActionProvider {
  constructor(private readonly vscode: typeof vscodeTypes) {}

  provideCodeActions(
    document: vscodeTypes.TextDocument,
    _range: vscodeTypes.Range | vscodeTypes.Selection,
    context: vscodeTypes.CodeActionContext,
  ): vscodeTypes.CodeAction[] {
    if (
      document.languageId !== "q1asm"
      || document.isDirty
      || document.uri.scheme !== "file"
      || path.extname(document.uri.fsPath).toLowerCase() !== ".q1asm"
    ) {
      return [];
    }

    return context.diagnostics.flatMap((diagnostic) => {
      const edit = editForDiagnostic(document.uri.fsPath, diagnostic);
      const sourceRange = diagnosticRange(diagnostic);
      if (
        diagnostic.source !== "q1timeline"
        || !edit
        || !sourceRange
        || !isSameQ1asmFile(edit.file, document.uri.fsPath)
        || !contains(sourceRange, edit.range)
        || !rangeFitsDocument(document, edit.range)
        || (edit.expectedLine !== undefined && document.lineAt(edit.range.start.line).text !== edit.expectedLine)
      ) {
        return [];
      }

      const action = new this.vscode.CodeAction(edit.title, this.vscode.CodeActionKind.QuickFix);
      const workspaceEdit = new this.vscode.WorkspaceEdit();
      workspaceEdit.replace(
        document.uri,
        new this.vscode.Range(
          edit.range.start.line,
          edit.range.start.column,
          edit.range.end.line,
          edit.range.end.column,
        ),
        edit.newText,
      );
      action.edit = workspaceEdit;
      action.diagnostics = [diagnostic];
      return [action];
    });
  }
}

export function registerQ1asmQuickFixes(
  context: vscodeTypes.ExtensionContext,
  vscode: typeof vscodeTypes,
): void {
  context.subscriptions.push(vscode.languages.registerCodeActionsProvider(
    { language: "q1asm", scheme: "file" },
    new Q1asmQuickFixProvider(vscode),
    { providedCodeActionKinds: [vscode.CodeActionKind.QuickFix] },
  ));
}
