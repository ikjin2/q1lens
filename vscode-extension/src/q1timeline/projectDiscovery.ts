import * as fs from "node:fs";
import * as path from "node:path";

export function findProjectFileUpward(
  sourceFile: string,
  projectFile: string,
  workspaceRoot: string,
  ignoredBasenames: string[] = [],
): string | undefined {
  return findProjectFileCandidatesUpward(sourceFile, [projectFile], workspaceRoot, ignoredBasenames)[0];
}

export function findProjectFileCandidatesUpward(
  sourceFile: string,
  projectFiles: string[],
  workspaceRoot: string,
  ignoredBasenames: string[] = [],
): string[] {
  if (!sourceFile || path.extname(sourceFile).toLowerCase() !== ".q1asm") {
    return [];
  }
  const sourceDir = path.dirname(sourceFile);
  const root = path.resolve(workspaceRoot || path.parse(sourceFile).root);
  let current = path.resolve(sourceDir);
  const patterns = normalizedProjectFilePatterns(projectFiles);
  const ignoredNames = normalizedIgnoredBasenames(ignoredBasenames);

  while (isPathInside(root, current)) {
    const matches = projectFileMatchesInDirectory(current, patterns, ignoredNames);
    if (matches.length) {
      return matches;
    }
    if (current === root) {
      return [];
    }
    const parent = path.dirname(current);
    if (parent === current) {
      return [];
    }
    current = parent;
  }

  return [];
}

function normalizedProjectFilePatterns(projectFiles: string[]): string[] {
  const patterns = (projectFiles || [])
    .map((item) => String(item || "").replace(/\\/g, "/").trim())
    .filter((item) => !!item);
  return patterns.length ? Array.from(new Set(patterns)) : ["q1timeline.yml"];
}

function normalizedIgnoredBasenames(ignoredBasenames: string[]): Set<string> {
  return new Set((ignoredBasenames || []).map((name) => path.basename(String(name)).toLowerCase()));
}

function projectFileMatchesInDirectory(directory: string, patterns: string[], ignoredNames: Set<string>): string[] {
  const exactMatches: string[] = [];
  const wildcardPatterns = patterns.filter((pattern) => pattern.includes("*"));
  for (const pattern of patterns.filter((item) => !item.includes("*"))) {
    const candidate = path.join(directory, pattern);
    if (fs.existsSync(candidate)) {
      exactMatches.push(candidate);
    }
  }
  const wildcardMatches = wildcardPatterns.length
    ? wildcardProjectFileMatches(directory, wildcardPatterns)
    : [];
  const byPath = new Map<string, string>();
  for (const candidate of [...exactMatches, ...wildcardMatches]) {
    if (ignoredNames.has(path.basename(candidate).toLowerCase())) {
      continue;
    }
    byPath.set(normalizeFsPath(candidate), candidate);
  }
  return Array.from(byPath.values()).sort(projectFilePrioritySort);
}

function wildcardProjectFileMatches(directory: string, patterns: string[]): string[] {
  const matches: string[] = [];
  for (const pattern of patterns) {
    const patternDir = path.dirname(pattern);
    const targetDir = patternDir === "." ? directory : path.join(directory, patternDir);
    const filePattern = path.basename(pattern);
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(targetDir, { withFileTypes: true });
    } catch {
      continue;
    }
    matches.push(
      ...entries
        .filter((entry) => entry.isFile())
        .map((entry) => path.join(targetDir, entry.name))
        .filter((candidate) => wildcardProjectFileMatch(path.basename(candidate), filePattern)),
    );
  }
  return matches;
}

function wildcardProjectFileMatch(filename: string, pattern: string): boolean {
  const escaped = pattern
    .replace(/[.+?^${}()|[\]\\]/g, "\\$&")
    .replace(/\*/g, ".*");
  return new RegExp(`^${escaped}$`, "i").test(filename);
}

function projectFilePrioritySort(left: string, right: string): number {
  const leftName = path.basename(left).toLowerCase();
  const rightName = path.basename(right).toLowerCase();
  if (leftName === "q1timeline.yml" && rightName !== "q1timeline.yml") {
    return -1;
  }
  if (rightName === "q1timeline.yml" && leftName !== "q1timeline.yml") {
    return 1;
  }
  return leftName.localeCompare(rightName) || normalizeFsPath(left).localeCompare(normalizeFsPath(right));
}

export function usableStoredProjectFile(input: {
  storedProjectFile: string;
  workspaceFolders: string[];
  exists?: (filePath: string) => boolean;
}): boolean {
  const stored = input.storedProjectFile ? path.resolve(input.storedProjectFile) : "";
  if (!stored) {
    return false;
  }
  const exists = input.exists || fs.existsSync;
  if (!exists(stored)) {
    return false;
  }
  const folders = input.workspaceFolders || [];
  if (!folders.length) {
    return true;
  }
  return folders.some((folder) => isPathInside(path.resolve(folder), stored));
}

function isPathInside(root: string, candidate: string): boolean {
  const normalizedRoot = normalizeFsPath(root);
  const normalizedCandidate = normalizeFsPath(candidate);
  const relativePath = path.relative(normalizedRoot, normalizedCandidate);
  return relativePath === "" || (!!relativePath && !relativePath.startsWith("..") && !path.isAbsolute(relativePath));
}

function normalizeFsPath(filePath: string): string {
  const resolved = path.resolve(filePath);
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}
