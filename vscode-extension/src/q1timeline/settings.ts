export interface ConfigurationLike {
  get<T>(key: string, fallback: T): T;
}

export function q1timelineConfigValue<T>(
  qbloxConfig: ConfigurationLike,
  legacyConfig: ConfigurationLike,
  key: string,
  fallback: T,
  q1lensConfig?: ConfigurationLike,
): T {
  const primaryConfig = q1lensConfig || { get: <U>(_key: string, value: U): U => value };
  if (key === "pythonPath" || key === "pythonArgs") {
    return primaryConfig.get(
      `q1timeline.${key}`,
      qbloxConfig.get(
        `q1timeline.${key}`,
        legacyConfig.get(key, primaryConfig.get(key, qbloxConfig.get(key, fallback))),
      ),
    );
  }
  return primaryConfig.get(
    `q1timeline.${key}`,
    qbloxConfig.get(
      `q1timeline.${key}`,
      legacyConfig.get(key, fallback),
    ),
  );
}
