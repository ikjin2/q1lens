export function prependPythonArgs(pythonArgs: unknown, moduleArgs: string[]): string[] {
  const args = Array.isArray(pythonArgs)
    ? pythonArgs.filter((item): item is string => typeof item === "string" && item.length > 0)
    : [];
  return [...args, ...moduleArgs];
}
