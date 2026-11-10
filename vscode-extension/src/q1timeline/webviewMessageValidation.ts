// @ts-nocheck
const { validExternalFeedback, validExternalTriggers } = require("./externalInputs");
function hasOnlyKeys(message, keys) {
  const allowed = new Set(keys);
  return Object.keys(message).every((key) => allowed.has(key));
}

const VALID_BRANCH_ASSUMPTION_PATHS = new Set(["collapsed", "taken", "fallthrough", "both"]);

function parseDefRunMessage(message) {
  const { action, project, revision, sequencer, uri, registers, observe_time, overrides, axes, token, sweepId, caseId, statistics_only, sample_count } = message;
  const literal = (value) => typeof value === "string" && value.length <= 16384;
  const base = ["type", "action", "project"];
  const target = ["revision", "sequencer", "uri"];
  if (typeof project !== "string" || !project) return { valid: false };
  if (["info", "cancel"].includes(action) && hasOnlyKeys(message, base)) return { ...message, valid: true };
  if (action === "case" && typeof sweepId === "string" && Number.isSafeInteger(caseId) && caseId > 0 &&
      hasOnlyKeys(message, [...base, "sweepId", "caseId"])) return { ...message, valid: true };
  if (![revision, sequencer, uri].every((value) => typeof value === "string" && value.length > 0)) return { valid: false };
  const validAxes = Array.isArray(axes) && axes.length >= 1 && axes.length <= 2 && axes.every((axis) =>
    axis && typeof axis === "object" && hasOnlyKeys(axis, ["name", "min", "max", "step"]) &&
    [axis.name, axis.min, axis.max, axis.step].every(literal));
  const validStatistics = statistics_only === undefined || typeof statistics_only === "boolean";
  if (action === "plan" && validAxes && validStatistics && Number.isSafeInteger(token) &&
      hasOnlyKeys(message, [...base, ...target, "axes", "token", "statistics_only"])) return { ...message, valid: true };
  const validObservations = Array.isArray(registers) && registers.length <= 64 &&
    registers.every((name) => typeof name === "string" && /^R(?:\d|[1-5]\d|6[0-3])$/.test(name)) &&
    new Set(registers).size === registers.length && typeof observe_time === "boolean";
  if (!["run", "sweep", "estimate"].includes(action) || !validObservations ||
      !overrides || typeof overrides !== "object" || Array.isArray(overrides) || !Object.values(overrides).every(literal)) {
    return { valid: false };
  }
  const keys = [...base, ...target, "registers", "observe_time", "overrides"];
  const validSampleCount = sample_count === undefined || (Number.isSafeInteger(sample_count) && sample_count >= 1 &&
    sample_count <= (statistics_only ? 1_000_000 : 25_000));
  const valid = action === "run" ? hasOnlyKeys(message, keys) : (registers.length > 0 || observe_time) &&
    validAxes && validStatistics && validSampleCount && hasOnlyKeys(message,
      [...keys, "axes", "statistics_only", ...(action === "sweep" ? ["sample_count"] : [])]);
  return valid ? { ...message, valid: true } : { valid: false };
}

function parseWebviewMessage(message) {
  if (!message || typeof message !== "object" || Array.isArray(message)) {
    return { valid: false, reason: "message must be an object" };
  }

  if (message.type === "defRun") return parseDefRunMessage(message);

  if (
    message.type === "eventClick" &&
    typeof message.eventId === "string" &&
    message.eventId.length > 0 &&
    (message.preserveFocus === undefined || typeof message.preserveFocus === "boolean") &&
    hasOnlyKeys(message, ["type", "eventId", "preserveFocus"])
  ) {
    return { valid: true, type: "eventClick", eventId: message.eventId,
      ...(typeof message.preserveFocus === "boolean" ? { preserveFocus: message.preserveFocus } : {}) };
  }

  if (message.type === "feedbackFlowClick" &&
      typeof message.flowId === "string" && message.flowId.trim().length > 0 &&
      (message.role === undefined || message.role === "source" || message.role === "target") &&
      hasOnlyKeys(message, ["type", "flowId", "role"])) {
    return { valid: true, type: "feedbackFlowClick", flowId: message.flowId,
      ...(message.role === undefined ? {} : { role: message.role }) };
  }

  if (message.type === "requestRefresh" && hasOnlyKeys(message, ["type"])) {
    return { valid: true, type: "requestRefresh" };
  }

  if (message.type === "executionControl" && ["play", "pause"].includes(message.action) &&
      hasOnlyKeys(message, ["type", "action"])) {
    return { valid: true, type: message.type, action: message.action };
  }

  if (message.type === "setExternalInputs" && validExternalTriggers(message.triggers) &&
      (message.feedback === undefined || validExternalFeedback(message.feedback)) &&
      hasOnlyKeys(message, ["type", "triggers", "feedback"])) {
    return { valid: true, type: message.type, triggers: message.triggers,
      ...(message.feedback === undefined ? {} : {feedback: message.feedback}) };
  }

  if (["saveExternalInputs", "resetExternalInputs"].includes(message.type) && hasOnlyKeys(message, ["type"])) {
    return { valid: true, type: message.type };
  }

  if (message.type === "setExecutionBranch" &&
      typeof message.eventId === "string" && message.eventId.trim().length > 0 &&
      typeof message.branchId === "string" && message.branchId.trim().length > 0 &&
      ["taken", "fallthrough"].includes(message.path) &&
      hasOnlyKeys(message, ["type", "eventId", "branchId", "path"])) {
    return { valid: true, type: message.type, eventId: message.eventId, branchId: message.branchId, path: message.path };
  }

  if (message.type === "setExecutionFeedback" &&
      typeof message.eventId === "string" && message.eventId.trim().length > 0 &&
      typeof message.feedbackId === "string" && message.feedbackId.trim().length > 0 &&
      (message.value === null || Number.isInteger(message.value) && message.value >= -2147483648 && message.value <= 4294967295) &&
      Number.isSafeInteger(message.delayNs) && message.delayNs >= 0 &&
      hasOnlyKeys(message, ["type", "eventId", "feedbackId", "value", "delayNs"])) {
    return { valid: true, type: message.type, eventId: message.eventId, feedbackId: message.feedbackId,
      value: message.value, delayNs: message.delayNs };
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
