// @ts-nocheck
const { mapAnalyzerDiagnostic } = require("./diagnosticMapper");

class DiagnosticsManager {
  constructor(vscodeApi, collection, resolveSourcePath) {
    this.vscode = vscodeApi;
    this.collection = collection;
    this.resolveSourcePath = resolveSourcePath;
  }

  apply(diagnostics) {
    const byUri = new Map();
    for (const item of diagnostics) {
      const mapped = mapAnalyzerDiagnostic(item, (sourceFile) => this.resolveSourcePath(sourceFile));
      if (!mapped) {
        continue;
      }
      const uri = this.vscode.Uri.file(mapped.file);
      const range = new this.vscode.Range(
        mapped.range.startLine,
        mapped.range.startColumn,
        mapped.range.endLine,
        mapped.range.endColumn
      );
      const diagnostic = new this.vscode.Diagnostic(range, mapped.message, this.toSeverity(mapped.severity));
      diagnostic.source = mapped.source;
      diagnostic.code = mapped.code;
      const relatedInformation = this.toRelatedInformation(mapped);
      if (relatedInformation.length) {
        diagnostic.relatedInformation = relatedInformation;
      }
      const entries = byUri.get(uri.toString()) || { uri, diagnostics: [] };
      entries.diagnostics.push(diagnostic);
      byUri.set(uri.toString(), entries);
    }
    this.collection.clear();
    for (const entry of byUri.values()) {
      this.collection.set(entry.uri, entry.diagnostics);
    }
    return diagnostics;
  }

  applyFallback(projectUri, message) {
    if (!projectUri) {
      return;
    }
    const diagnostic = new this.vscode.Diagnostic(
      new this.vscode.Range(0, 0, 0, 1),
      message,
      this.vscode.DiagnosticSeverity.Error
    );
    diagnostic.source = "q1timeline";
    diagnostic.code = "invalid_analyzer_json";
    this.collection.clear();
    this.collection.set(projectUri, [diagnostic]);
  }

  toRelatedInformation(item) {
    const relatedItems = item.relatedInformation || [];
    if (!Array.isArray(relatedItems)) {
      return [];
    }
    return relatedItems
      .filter((related) => related.file)
      .map((related) => {
        const position = new this.vscode.Position(
          related.range.startLine,
          related.range.startColumn
        );
        const location = new this.vscode.Location(
          this.vscode.Uri.file(related.file),
          new this.vscode.Range(position, position)
        );
        return new this.vscode.DiagnosticRelatedInformation(location, related.message);
      });
  }

  toSeverity(severity) {
    if (severity === "error") {
      return this.vscode.DiagnosticSeverity.Error;
    }
    if (severity === "warning") {
      return this.vscode.DiagnosticSeverity.Warning;
    }
    if (severity === "hint") {
      return this.vscode.DiagnosticSeverity.Hint;
    }
    return this.vscode.DiagnosticSeverity.Information;
  }
}

export {
  DiagnosticsManager,
};