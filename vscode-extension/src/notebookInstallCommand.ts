export type NotebookInstaller = "pip" | "uv";

export function notebookInstallCommand(
  pythonPath: string,
  version: string,
  installer: NotebookInstaller,
  platform: NodeJS.Platform = process.platform
): string {
  if (/[\x00-\x1f\x7f]/.test(pythonPath) || /[\x00-\x1f\x7f]/.test(version)) {
    throw new Error("The installation command cannot contain control characters.");
  }
  const windows = platform === "win32";
  const quote = (value: string) => `'${windows
    ? value.replace(/['\u2018\u2019\u201a\u201b]/g, character => character + character)
    : value.replace(/'/g, "'\"'\"'")}'`;
  const requirement = quote(`q1lens==${version}`);
  return installer === "uv"
    ? `uv pip install --python ${quote(pythonPath)} ${requirement}`
    : `${windows ? "& " : ""}${quote(pythonPath)} -m pip install ${requirement}`;
}
