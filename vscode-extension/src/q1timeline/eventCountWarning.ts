// @ts-nocheck
function eventCount(analyzerResult) {
  const value = analyzerResult && analyzerResult.stats ? Number(analyzerResult.stats.event_count) : 0;
  return Number.isFinite(value) ? value : 0;
}

function shouldWarnForLargeEventCount(analyzerResult, threshold) {
  const limit = Number(threshold);
  return Number.isFinite(limit) && limit > 0 && eventCount(analyzerResult) > limit;
}

function largeEventCountWarningMessage(analyzerResult, threshold) {
  return `Timeline contains ${eventCount(analyzerResult)} events, above q1timeline.render.maxEventsBeforeSimplify (${threshold}).`;
}

export {
  eventCount,
  largeEventCountWarningMessage,
  shouldWarnForLargeEventCount,
};