// @ts-nocheck
const path = require("path");

function normalizeSources(entry) {
  if (!entry) {
    return [];
  }
  if (Array.isArray(entry)) {
    return entry.filter(Boolean);
  }
  if (Array.isArray(entry.sources)) {
    return entry.sources.filter(Boolean);
  }
  if (entry.source) {
    return normalizeSources(entry.source);
  }
  return [entry];
}

function lookupSourceForEvent(timelineIr, eventId) {
  const event = (timelineIr && Array.isArray(timelineIr.events) ? timelineIr.events : [])
    .find((candidate) => String(candidate && candidate.id) === String(eventId));
  if (isRemovedEvent(event)) {
    return undefined;
  }
  const byEventId = timelineIr && timelineIr.source_map && timelineIr.source_map.by_event_id
    ? timelineIr.source_map.by_event_id
    : {};
  const mappedSource = normalizeSources(byEventId[eventId])[0];
  if (mappedSource) {
    return mappedSource;
  }
  return event && event.source ? event.source : undefined;
}

function sourceMatchesLine(source, sourceFile, line) {
  if (!source || !source.file) {
    return false;
  }
  const sourceLine = Number(source.line);
  if (!Number.isFinite(sourceLine) || sourceLine !== line) {
    return false;
  }
  return sourceFileMatches(String(source.file), sourceFile, undefined);
}

function lookupEventIdsForSourceLine(timelineIr, sourceFile, line) {
  const bySource = timelineIr && timelineIr.source_map && timelineIr.source_map.by_source
    ? timelineIr.source_map.by_source
    : {};
  const byEventId = timelineIr && timelineIr.source_map && timelineIr.source_map.by_event_id
    ? timelineIr.source_map.by_event_id
    : {};
  const sourceLine = Number(line);
  if (!Number.isFinite(sourceLine)) {
    return [];
  }

  const eventIds = [];
  const seen = new Set();
  const eventsById = new Map((timelineIr && Array.isArray(timelineIr.events) ? timelineIr.events : []).map((event) => [String(event.id), event]));
  for (const [key, value] of Object.entries(bySource)) {
    const parsed = parseSourceLineKey(key);
    if (!parsed || parsed.line !== sourceLine || !sourceFileMatches(parsed.file, sourceFile, timelineIr)) {
      continue;
    }
    const matches = Array.isArray(value) ? value : [];
    for (const eventId of matches) {
      if (isRemovedEvent(eventsById.get(String(eventId)))) {
        continue;
      }
      if (!seen.has(eventId)) {
        seen.add(eventId);
        eventIds.push(eventId);
      }
    }
  }
  for (const [eventId, entry] of Object.entries(byEventId)) {
    if (isRemovedEvent(eventsById.get(String(eventId)))) {
      continue;
    }
    const hasMatchingSource = normalizeSources(entry).some((source) =>
      sourceMatchesLineWithTimeline(source, sourceFile, sourceLine, timelineIr)
    );
    if (hasMatchingSource && !seen.has(eventId)) {
      seen.add(eventId);
      eventIds.push(eventId);
    }
  }
  for (const event of eventsById.values()) {
    if (!event || !event.id || isRemovedEvent(event)) {
      continue;
    }
    const eventId = String(event.id);
    if (seen.has(eventId)) {
      continue;
    }
    if (sourceMatchesLineWithTimeline(event.source, sourceFile, sourceLine, timelineIr)) {
      seen.add(eventId);
      eventIds.push(eventId);
    }
  }
  return eventIds;
}

function isRemovedEvent(event) {
  return event && event.meta && event.meta.diff_status === "removed";
}

function sourceMatchesLineWithTimeline(source, sourceFile, line, timelineIr) {
  if (!source || !source.file) {
    return false;
  }
  const sourceLine = Number(source.line);
  if (!Number.isFinite(sourceLine) || sourceLine !== line) {
    return false;
  }
  return sourceFileMatches(String(source.file), sourceFile, timelineIr);
}

function parseSourceLineKey(key) {
  const match = String(key).match(/^(.*):([0-9]+)$/);
  if (!match) {
    return undefined;
  }
  return { file: match[1], line: Number(match[2]) };
}

function sourceFileMatches(mappedFile, requestedFile, timelineIr) {
  const mapped = normalizePathText(mappedFile);
  const requested = normalizePathText(requestedFile);
  if (!mapped || !requested) {
    return false;
  }
  if (mapped === requested) {
    return true;
  }
  const projectRoot = normalizePathText(timelineIr?.project?.root || timelineIr?.project_root || "");
  if (projectRoot) {
    const mappedAbsolute = normalizePathText(path.resolve(projectRoot, mappedFile));
    if (mappedAbsolute === requested) {
      return true;
    }
    const requestedRelative = normalizePathText(path.relative(projectRoot, requestedFile));
    if (requestedRelative && requestedRelative === mapped) {
      return true;
    }
  }
  if (hasDirectory(mapped)) {
    return requested.endsWith(`/${mapped}`);
  }
  return path.basename(requested) === mapped;
}

function normalizePathText(filePath) {
  const normalized = String(filePath || "").replace(/\\/g, "/").replace(/\/+/g, "/");
  return process.platform === "win32" ? normalized.toLowerCase() : normalized;
}

function hasDirectory(filePath) {
  return /[\\/]/.test(String(filePath || ""));
}

export {
  lookupSourceForEvent,
  lookupEventIdsForSourceLine,
};
