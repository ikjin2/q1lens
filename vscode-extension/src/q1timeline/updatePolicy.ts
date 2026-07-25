// @ts-nocheck
const VALID_UPDATE_MODES = new Set(["manual", "onSave", "onType"]);

function normalizeUpdateMode(mode) {
  return VALID_UPDATE_MODES.has(mode) ? mode : "onSave";
}

function shouldAnalyzeOnSave(mode) {
  const normalized = normalizeUpdateMode(mode);
  return normalized === "onSave" || normalized === "onType";
}

function shouldAnalyzeOnType(mode) {
  return normalizeUpdateMode(mode) === "onType";
}

function shouldAnalyzeWatchedFile(mode) {
  return normalizeUpdateMode(mode) !== "manual";
}

function shouldDebounceWatchedFile(mode) {
  return normalizeUpdateMode(mode) === "onType";
}

export {
  normalizeUpdateMode,
  shouldAnalyzeOnSave,
  shouldAnalyzeOnType,
  shouldAnalyzeWatchedFile,
  shouldDebounceWatchedFile,
};