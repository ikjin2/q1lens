import { isAbsolute, join } from "node:path";
import * as vscode from "vscode";
import { QbsDiagnostic, QbsDiagnosticSeverity } from "./diagnosticsCore";

function toVscodeSeverity(severity: QbsDiagnosticSeverity): vscode.DiagnosticSeverity {
  if (severity === "error") {
    return vscode.DiagnosticSeverity.Error;
  }
  if (severity === "hint") {
    return vscode.DiagnosticSeverity.Hint;
  }
  if (severity === "information") {
    return vscode.DiagnosticSeverity.Information;
  }
  return vscode.DiagnosticSeverity.Warning;
}

function oneLineRange(line: number | undefined): vscode.Range {
  const zeroBasedLine = Math.max((line ?? 1) - 1, 0);
  return new vscode.Range(zeroBasedLine, 0, zeroBasedLine, Number.MAX_SAFE_INTEGER);
}

export function publishDiagnostics(input: {
  collection: vscode.DiagnosticCollection;
  diagnostics: QbsDiagnostic[];
  projectFile: string;
  outputDir: string;
}): void {
  const byFile = new Map<string, vscode.Diagnostic[]>();

  for (const diagnostic of input.diagnostics) {
    const file = diagnostic.file
      ? (isAbsolute(diagnostic.file) ? diagnostic.file : join(input.outputDir, diagnostic.file))
      : input.projectFile;
    const vscodeDiagnostic = new vscode.Diagnostic(
      oneLineRange(diagnostic.line),
      diagnostic.message,
      toVscodeSeverity(diagnostic.severity),
    );
    vscodeDiagnostic.code = diagnostic.code;
    vscodeDiagnostic.source = diagnostic.source ?? "qbsTimeline";

    const list = byFile.get(file) ?? [];
    list.push(vscodeDiagnostic);
    byFile.set(file, list);
  }

  input.collection.clear();
  for (const [file, list] of byFile) {
    input.collection.set(vscode.Uri.file(file), list);
  }
}
