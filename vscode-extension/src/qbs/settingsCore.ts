export interface ConfigurationLike {
  get<T>(key: string, fallback: T): T;
  inspect?<T>(key: string): {
    globalValue?: T;
    workspaceValue?: T;
    workspaceFolderValue?: T;
    globalLanguageValue?: T;
    workspaceLanguageValue?: T;
    workspaceFolderLanguageValue?: T;
  } | undefined;
}

export function explicitConfigValue<T>(config: ConfigurationLike, key: string): T | undefined {
  if (config.inspect) {
    const value = config.inspect<T>(key);
    if (!value || ![
      value.globalValue,
      value.workspaceValue,
      value.workspaceFolderValue,
      value.globalLanguageValue,
      value.workspaceLanguageValue,
      value.workspaceFolderLanguageValue,
    ].some((entry) => entry !== undefined)) {
      return undefined;
    }
  }
  return config.get<T | undefined>(key, undefined);
}

export function readSharedPythonSettings(
  q1lensConfig: ConfigurationLike,
  qbloxConfig: ConfigurationLike,
  qbsConfig: ConfigurationLike,
): { pythonPath: string; pythonArgs: string[] } {
  return {
    pythonPath: explicitConfigValue<string>(q1lensConfig, "pythonPath")
      ?? explicitConfigValue<string>(qbloxConfig, "pythonPath")
      ?? explicitConfigValue<string>(qbsConfig, "pythonPath")
      ?? q1lensConfig.get("pythonPath", "python"),
    pythonArgs: explicitConfigValue<string[]>(q1lensConfig, "pythonArgs")
      ?? explicitConfigValue<string[]>(qbloxConfig, "pythonArgs")
      ?? explicitConfigValue<string[]>(qbsConfig, "pythonArgs")
      ?? q1lensConfig.get<string[]>("pythonArgs", []),
  };
}
