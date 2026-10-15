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

  if (
    message.type === "prepareExecutionReplay" &&
    typeof message.sequencerId === "string" &&
    message.sequencerId.trim().length > 0 &&
    (message.scope === undefined || message.scope === "project" || message.scope === "sequencer") &&
    hasOnlyKeys(message, ["type", "sequencerId", "scope"])
  ) {
    return { valid: true, type: "prepareExecutionReplay", sequencerId: message.sequencerId, ...(message.scope === undefined ? {} : { scope: message.scope }) };
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
    (message.preserveHardwareCursor === undefined || typeof message.preserveHardwareCursor === "boolean") &&
    hasOnlyKeys(message, ["type", "file", "line", "column", "preserveHardwareCursor"])
  ) {
    return { valid: true, type: "sourceClick", file: message.file, line: message.line, column: message.column,
      ...(typeof message.preserveHardwareCursor === "boolean" ? { preserveHardwareCursor: message.preserveHardwareCursor } : {}) };
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
    (message.eventId === undefined || (typeof message.eventId === "string" && message.eventId.trim().length > 0)) &&
    hasOnlyKeys(message, ["type", "branchId", "path", "eventId"])
  ) {
    return { valid: true, type: "setBranchAssumption", branchId: message.branchId, path: message.path,
      ...(message.eventId === undefined ? {} : { eventId: message.eventId }) };
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

  if (
    (message.type === "setAnalysisParameter" || message.type === "resetAnalysisParameter") &&
    typeof message.name === "string" &&
    message.name.trim().length > 0 &&
    !message.name.includes("=") &&
    typeof message.projectFile === "string" &&
    message.projectFile.trim().length > 0
  ) {
    if (
      message.type === "setAnalysisParameter" &&
      typeof message.valueJson === "string" &&
      hasOnlyKeys(message, ["type", "name", "valueJson", "projectFile"])
    ) {
      return {
        valid: true,
        type: message.type,
        name: message.name,
        valueJson: message.valueJson,
        projectFile: message.projectFile,
      };
    }
    if (
      message.type === "resetAnalysisParameter" &&
      hasOnlyKeys(message, ["type", "name", "projectFile"])
    ) {
      return { valid: true, type: message.type, name: message.name, projectFile: message.projectFile };
    }
  }

  return { valid: false, reason: "unknown webview message shape" };
}

export {
  parseWebviewMessage,
};
