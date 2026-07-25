// @ts-nocheck
function analyzerFailurePayload(error) {
  const candidates = [error && error.stdout, error && error.stderr, error && error.message].filter(Boolean);
  for (const candidate of candidates) {
    const text = String(candidate).trim();
    const start = text.indexOf("{");
    const end = text.lastIndexOf("}");
    if (start === -1 || end <= start) {
      continue;
    }
    try {
      const payload = JSON.parse(text.slice(start, end + 1));
      if (payload && typeof payload === "object" && (payload.error || Array.isArray(payload.diagnostics))) {
        return payload;
      }
    } catch (parseError) {
      continue;
    }
  }
  return undefined;
}

function errorFromAnalyzerFailure(error) {
  const payload = analyzerFailurePayload(error);
  return payload && payload.error && typeof payload.error === "object" ? payload.error : undefined;
}

function diagnosticsFromAnalyzerFailure(error) {
  const payload = analyzerFailurePayload(error);
  return payload && Array.isArray(payload.diagnostics) ? payload.diagnostics : [];
}

export {
  analyzerFailurePayload,
  diagnosticsFromAnalyzerFailure,
  errorFromAnalyzerFailure,
};