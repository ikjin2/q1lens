import { ConfigurationLike, explicitConfigValue } from "../qbs/settingsCore";

export type { ConfigurationLike } from "../qbs/settingsCore";

export function q1timelineConfigValue<T>(
  qbloxConfig: ConfigurationLike,
  legacyConfig: ConfigurationLike,
  key: string,
  fallback: T,
  q1lensConfig?: ConfigurationLike,
): T {
  const primaryConfig = q1lensConfig || { get: <U>(_key: string, value: U): U => value };
  if (key === "pythonPath" || key === "pythonArgs") {
    return explicitConfigValue<T>(primaryConfig, `q1timeline.${key}`)
      ?? explicitConfigValue<T>(qbloxConfig, `q1timeline.${key}`)
      ?? explicitConfigValue<T>(legacyConfig, key)
      ?? explicitConfigValue<T>(primaryConfig, key)
      ?? explicitConfigValue<T>(qbloxConfig, key)
      ?? primaryConfig.get(key, qbloxConfig.get(key, fallback));
  }
  return explicitConfigValue<T>(primaryConfig, `q1timeline.${key}`)
    ?? explicitConfigValue<T>(qbloxConfig, `q1timeline.${key}`)
    ?? explicitConfigValue<T>(legacyConfig, key)
    ?? primaryConfig.get(
      `q1timeline.${key}`,
      qbloxConfig.get(
        `q1timeline.${key}`,
        legacyConfig.get(key, fallback),
      ),
    );
}
