// @ts-nocheck
const SUPPORTED_ANALYZER_SCHEMA_VERSION = "0.2.0";

function parseAnalyzerResult(stdout) {
  let payload;
  try {
    payload = JSON.parse(String(stdout || "").trim());
  } catch (parseError) {
    const error = new Error(`Invalid AnalyzerResult JSON: ${parseError.message}`);
    error.stdout = stdout;
    throw error;
  }
  if (!payload || typeof payload !== "object") {
    throw new Error("Invalid AnalyzerResult JSON: expected an object payload.");
  }
  if (typeof payload.schema_version !== "string") {
    throw new Error("Invalid AnalyzerResult JSON: missing schema_version.");
  }
  if (payload.schema_version !== SUPPORTED_ANALYZER_SCHEMA_VERSION) {
    throw new Error(
      `Unsupported AnalyzerResult schema_version ${payload.schema_version}; expected ${SUPPORTED_ANALYZER_SCHEMA_VERSION}.`
    );
  }
  if (typeof payload.status !== "string") {
    throw new Error("Invalid AnalyzerResult JSON: missing status.");
  }
  return payload;
}

export {
  SUPPORTED_ANALYZER_SCHEMA_VERSION,
  parseAnalyzerResult,
};