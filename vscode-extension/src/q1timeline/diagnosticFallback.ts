export function fallbackAnalyzerDiagnostic(
  projectFile: string,
  message: string,
  category = "invalid_analyzer_json",
): Record<string, unknown> {
  return {
    severity: "error",
    category,
    message,
    source: { file: projectFile, line: 1, column: 1 },
    confidence: "error",
  };
}

export function normalizeAnalyzerDiagnostics(
  value: unknown,
  projectFile: string,
  message = "Invalid analyzer diagnostics JSON: expected array",
): Record<string, unknown>[] {
  return Array.isArray(value) ? value as Record<string, unknown>[] : [fallbackAnalyzerDiagnostic(projectFile, message)];
}
