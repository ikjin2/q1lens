// @ts-nocheck
function hasOnlyKeys(message, keys) {
  const allowed = new Set(keys);
  return Object.keys(message).every((key) => allowed.has(key));
}

const VALID_BRANCH_ASSUMPTION_PATHS = new Set(["collapsed", "taken", "fallthrough", "both"]);

function parseWebviewMessage(message) {
  if (!message || typeof message !== "object" || Array.isArray(message)) {
    return { valid: false, reason: "message must be an object" };
  }

  if (
    message.type === "eventClick" &&
    typeof message.eventId === "string" &&
    message.eventId.length > 0 &&
    hasOnlyKeys(message, ["type", "eventId"])
  ) {
    return { valid: true, type: "eventClick", eventId: message.eventId };
  }

  if (message.type === "requestRefresh" && hasOnlyKeys(message, ["type"])) {
    return { valid: true, type: "requestRefresh" };
  }

  if (message.type === "webviewReady" && hasOnlyKeys(message, ["type"])) {
    return { valid: true, type: "webviewReady" };
  }

  if (
    message.type === "diagnosticClick" &&
    Number.isInteger(message.diagnosticIndex) &&
    message.diagnosticIndex >= 0 &&
    hasOnlyKeys(message, ["type", "diagnosticIndex"])
  ) {
    return { valid: true, type: "diagnosticClick", diagnosticIndex: message.diagnosticIndex };
  }

  if (
    message.type === "sourceClick" &&
    typeof message.file === "string" &&
    message.file.length > 0 &&
    Number.isInteger(message.line) &&
    message.line > 0 &&
    Number.isInteger(message.column) &&
    message.column > 0 &&
    hasOnlyKeys(message, ["type", "file", "line", "column"])
  ) {
    return { valid: true, type: "sourceClick", file: message.file, line: message.line, column: message.column };
  }

  if (
    message.type === "setViewMode" &&
    (message.mode === "normal" || message.mode === "debug") &&
    hasOnlyKeys(message, ["type", "mode"])
  ) {
    return { valid: true, type: "setViewMode", mode: message.mode };
  }

  if (
    message.type === "setBranchAssumption" &&
    typeof message.branchId === "string" &&
    message.branchId.length > 0 &&
    VALID_BRANCH_ASSUMPTION_PATHS.has(message.path) &&
    hasOnlyKeys(message, ["type", "branchId", "path"])
  ) {
    return { valid: true, type: "setBranchAssumption", branchId: message.branchId, path: message.path };
  }

  if (
    message.type === "setLoopPreview" &&
    typeof message.loopKey === "string" &&
    message.loopKey.length > 0 &&
    Number.isInteger(message.visibleIterations) &&
    message.visibleIterations > 0 &&
    hasOnlyKeys(message, ["type", "loopKey", "visibleIterations"])
  ) {
    return {
      valid: true,
      type: "setLoopPreview",
      loopKey: message.loopKey,
      visibleIterations: message.visibleIterations,
    };
  }

  return { valid: false, reason: "unknown webview message shape" };
}

export {
  parseWebviewMessage,
};
