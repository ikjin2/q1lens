// @ts-nocheck
function summarizeDiagnostics(diagnostics) {
  const summary = { error: 0, warning: 0, info: 0 };
  const items = Array.isArray(diagnostics) ? diagnostics : [];
  for (const item of items) {
    const severity = String(item && item.severity ? item.severity : "").toLowerCase();
    if (severity === "error") {
      summary.error += 1;
    } else if (severity === "warning") {
      summary.warning += 1;
    } else {
      summary.info += 1;
    }
  }
  return summary;
}

export {
  summarizeDiagnostics,
};