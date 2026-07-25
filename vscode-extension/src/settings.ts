type SettingsBag = Record<string, any>;

function getPath(root: SettingsBag, path: string[]): unknown {
  let current: unknown = root;
  for (const key of path) {
    if (!current || typeof current !== "object" || !(key in current)) {
      return undefined;
    }
    current = (current as SettingsBag)[key];
  }
  return current;
}

export function readMergedSetting<T>(settings: SettingsBag, canonicalPath: string[], fallback: T): T {
  const primary = getPath(settings.q1lens || {}, canonicalPath);
  if (primary !== undefined && primary !== null) {
    return primary as T;
  }
  const canonical = getPath(settings.qbloxTimeline || {}, canonicalPath);
  if (canonical !== undefined && canonical !== null) {
    return canonical as T;
  }
  if (canonicalPath[0] === "q1timeline") {
    const legacy = getPath(settings.q1timeline || {}, canonicalPath.slice(1));
    return legacy !== undefined && legacy !== null ? (legacy as T) : fallback;
  }
  if (canonicalPath[0] === "qbs") {
    const legacy = getPath(settings.qbsTimeline || {}, canonicalPath.slice(1));
    return legacy !== undefined && legacy !== null ? (legacy as T) : fallback;
  }
  const qbsLegacy = getPath(settings.qbsTimeline || {}, canonicalPath);
  return qbsLegacy !== undefined && qbsLegacy !== null ? (qbsLegacy as T) : fallback;
}
