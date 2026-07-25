// @ts-nocheck
function normaliseSeverity(severity) {
  if (severity === "error" || severity === "warning" || severity === "hint") {
    return severity;
  }
  return "information";
}

function sourceRange(source) {
  const line = Number(source.line || 1);
  const column = Number(source.column || 1);
  const safeLine = Number.isFinite(line) ? line : 1;
  const safeColumn = Number.isFinite(column) ? column : 1;
  const startLine = Math.max(0, safeLine - 1);
  const startColumn = Math.max(0, safeColumn - 1);
  return {
    startLine,
    startColumn,
    endLine: startLine,
    endColumn: Math.max(0, safeColumn),
  };
}

function relatedInformation(item, resolveSourcePath) {
  const relatedItems = item.related || item.related_information || [];
  if (!Array.isArray(relatedItems)) {
    return [];
  }
  return relatedItems
    .filter((related) => related && typeof related === "object" && related.source && related.source.file)
    .map((related) => ({
      file: resolveSourcePath(related.source.file),
      range: sourceRange(related.source),
      message: related.message || related.category || "Related diagnostic",
    }));
}

function mapAnalyzerDiagnostic(item, resolveSourcePath) {
  if (!item || !item.source || !item.source.file) {
    return undefined;
  }
  return {
    file: resolveSourcePath(item.source.file),
    range: sourceRange(item.source),
    message: item.message || item.category,
    severity: normaliseSeverity(item.severity),
    source: "q1timeline",
    code: item.confidence ? `${item.category}:${item.confidence}` : item.category,
    relatedInformation: relatedInformation(item, resolveSourcePath),
  };
}

export {
  mapAnalyzerDiagnostic,
  normaliseSeverity,
};
