// @ts-nocheck
const path = require("path");
const YAML = require("yaml");

function collectProjectRelatedPaths(projectPath, projectText) {
  const root = path.dirname(projectPath);
  const related = new Set([path.resolve(projectPath)]);
  const text = typeof projectText === "string" ? projectText : "";
  collectYamlProjectPaths(text, root, related);
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^\s*-?\s*(file|sequence_json|params|display)\s*:\s*(.+?)\s*$/);
    if (!match) {
      continue;
    }
    const value = yamlScalarToPath(match[2]);
    if (!value) {
      continue;
    }
    related.add(path.resolve(root, value));
  }
  return Array.from(related);
}

function collectYamlProjectPaths(text, root, related) {
  let parsed;
  try {
    parsed = YAML.parse(text);
  } catch {
    return;
  }
  collectPathValues(parsed, root, related, new Set());
}

function collectPathValues(value, root, related, seen) {
  if (!value || typeof value !== "object" || seen.has(value)) {
    return;
  }
  seen.add(value);
  if (Array.isArray(value)) {
    for (const item of value) {
      collectPathValues(item, root, related, seen);
    }
    return;
  }
  for (const [key, child] of Object.entries(value)) {
    if (["file", "sequence_json", "params", "display"].includes(String(key)) && typeof child === "string" && child) {
      related.add(path.resolve(root, child));
      continue;
    }
    collectPathValues(child, root, related, seen);
  }
}

function normalizedFsPath(filePath) {
  const resolved = path.resolve(String(filePath || ""));
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

function isProjectRelatedPath(input) {
  if (!input || !input.projectPath || !input.filePath) {
    return false;
  }
  const filePath = normalizedFsPath(input.filePath);
  if (filePath === normalizedFsPath(input.projectPath)) {
    return true;
  }
  if (input.singleFilePath && filePath === normalizedFsPath(input.singleFilePath)) {
    return true;
  }
  const relatedPaths = Array.isArray(input.projectRelatedPaths)
    ? input.projectRelatedPaths
    : Array.from(input.projectRelatedPaths || []);
  return relatedPaths.some((relatedPath) => filePath === normalizedFsPath(relatedPath));
}

function yamlScalarToPath(value) {
  const withoutComment = stripYamlInlineComment(value).trim();
  if (!withoutComment || withoutComment.startsWith("[") || withoutComment.startsWith("{")) {
    return "";
  }
  if (
    (withoutComment.startsWith('"') && withoutComment.endsWith('"')) ||
    (withoutComment.startsWith("'") && withoutComment.endsWith("'"))
  ) {
    return withoutComment.slice(1, -1);
  }
  return withoutComment;
}

function stripYamlInlineComment(value) {
  const text = String(value);
  let quote = "";
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (!quote && (character === '"' || character === "'")) {
      quote = character;
      continue;
    }
    if (quote && character === quote && text[index - 1] !== "\\") {
      quote = "";
      continue;
    }
    if (!quote && character === "#" && index > 0 && /\s/.test(text[index - 1])) {
      return text.slice(0, index);
    }
  }
  return text;
}

export {
  collectProjectRelatedPaths,
  isProjectRelatedPath,
};
