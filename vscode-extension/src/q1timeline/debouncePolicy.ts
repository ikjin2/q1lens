// @ts-nocheck
function eventCount(analyzerResult) {
  const raw = analyzerResult && analyzerResult.stats ? analyzerResult.stats.event_count : 0;
  const count = Number(raw);
  return Number.isFinite(count) && count > 0 ? count : 0;
}

function effectiveDebounceMs(configuredMs, analyzerResult) {
  const configured = Number(configuredMs);
  const base = Number.isFinite(configured) && configured >= 0 ? configured : 400;
  const count = eventCount(analyzerResult);
  if (count >= 10000) {
    return Math.max(base, 1200);
  }
  if (count >= 1000) {
    return Math.max(Math.min(base, 700), 700);
  }
  return Math.min(base, 300);
}

export {
  effectiveDebounceMs,
};