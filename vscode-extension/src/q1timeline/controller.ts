// @ts-nocheck
const childProcess = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const vscode = require("vscode");
const fsPromises = fs.promises;
const {
  diagnosticsFromAnalyzerFailure,
  errorFromAnalyzerFailure,
} = require("./analyzerFailure");
const { parseAnalyzerResult } = require("./analyzerResult");
const {
  SUPPORTED_CORE_VERSION_RANGE,
  isSupportedCoreVersion,
} = require("./coreVersionCompatibility");
const { DiagnosticsManager } = require("./diagnosticsManager");
const { summarizeDiagnostics } = require("./diagnosticSummary");
const { effectiveDebounceMs } = require("./debouncePolicy");
const {
  largeEventCountWarningMessage,
  shouldWarnForLargeEventCount,
} = require("./eventCountWarning");
const projectDiscovery = require("./projectDiscovery");
const { collectProjectRelatedPaths, isProjectRelatedPath } = require("./projectRelatedFiles");
const { fallbackAnalyzerDiagnostic, normalizeAnalyzerDiagnostics } = require("./diagnosticFallback");
const { prependPythonArgs } = require("./invocation");
const { q1timelineConfigValue } = require("./settings");
const {
  lookupEventIdsForSourceLine,
  lookupEventIdsForSourceRange,
  lookupSourceForEvent,
} = require("./sourceMapLookup");
const updatePolicy = require("./updatePolicy");
const { parseWebviewMessage } = require("./webviewMessageValidation");
const { PlaybackClient } = require("./playbackClient");
const { q1timelineProjectOutputDir } = require("./register");

const COMPARISON_BASELINE_FILENAME = "comparison-baseline.json";

function webviewLocalResourceRoots(context) {
  if (context.extensionUri && vscode.Uri.joinPath) {
    return [
      vscode.Uri.joinPath(context.extensionUri, "out", "src", "shared", "timeline"),
      vscode.Uri.joinPath(context.extensionUri, "out", "src", "q1timeline", "media"),
    ];
  }
  const extensionPath = context.extensionPath || __dirname;
  return [
    vscode.Uri.file(path.join(extensionPath, "out", "src", "shared", "timeline")),
    vscode.Uri.file(path.join(extensionPath, "out", "src", "q1timeline", "media")),
  ];
}

function isQ1asmPath(filePath) {
  return path.extname(String(filePath || "")).toLowerCase() === ".q1asm";
}

function isTimelineIr(value) {
  return Boolean(
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Array.isArray(value.events) &&
    value.events.every((event) => event && typeof event === "object" && !Array.isArray(event))
  );
}

export function hardwareReadinessAnalyzerArgs(enabled) {
  return enabled ? ["--hardware-readiness"] : [];
}

function q1asmTemplatePlaceholders(text) {
  const names = new Set();
  const regex = /\{([A-Za-z_][A-Za-z0-9_]*)\}/g;
  let match;
  while ((match = regex.exec(String(text || ""))) !== null) {
    names.add(match[1]);
  }
  return names;
}

function inferredPlaceholderParamValue(name) {
  const upper = String(name || "").toUpperCase();
  const tokens = upper.split(/[^A-Z0-9]+/).filter(Boolean);
  const hasToken = (...values) => values.some((value) => tokens.includes(value));
  if (hasToken("TOTAL", "PERIOD", "CYCLE")) {
    return 4000;
  }
  if (hasToken("COUNT", "ITER", "LOOP", "SHOTS")) {
    return 1;
  }
  if (hasToken("ID", "IDX", "INDEX", "CHANNEL", "BIN", "SHIFT")) {
    return 0;
  }
  if (hasToken("GAIN", "OFFSET", "PHASE", "FREQ")) {
    return 0;
  }
  if (hasToken("MAX")) {
    return 1024;
  }
  if (hasToken("MIN")) {
    return 0;
  }
  if (
    hasToken("WAIT", "DUR", "DURATION", "TIME", "DELAY", "ALIGN", "POST", "DELTA") ||
    upper.endsWith("_NS") ||
    upper.startsWith("T_")
  ) {
    return 40;
  }
  return 0;
}

export function contentSecurityPolicy(webview, nonce) {
  const source = webview && webview.cspSource ? webview.cspSource : "'self'";
  const scriptSources = nonce ? [`'nonce-${nonce}'`, source] : [source];
  return [
    "default-src 'none'",
    `img-src ${source} data:`,
    `style-src ${source} 'unsafe-inline'`,
    `script-src ${scriptSources.join(" ")}`,
  ].join("; ");
}

function webviewNonce() {
  return crypto.randomBytes(16).toString("base64");
}

function webviewAssetUri(webview, context, ...segments) {
  const extensionPath = context.extensionUri || vscode.Uri.file(context.extensionPath || __dirname);
  const uri = vscode.Uri.joinPath
    ? vscode.Uri.joinPath(extensionPath, "out", ...segments)
    : vscode.Uri.file(path.join(context.extensionPath || __dirname, "out", ...segments));
  if (webview && typeof webview.asWebviewUri === "function") {
    return webview.asWebviewUri(uri).toString();
  }
  return uri.toString();
}

function versionedWebviewAssetUri(uri, version) {
  const separator = String(uri).includes("?") ? "&" : "?";
  return `${uri}${separator}v=${encodeURIComponent(version || "dev")}`;
}

function clearSharedTimelineRoot(html) {
  return html.replace(
    /(<section\b[^>]*\bid=["']timeline-root["'][^>]*>)[\s\S]*?<\/section>/i,
    "$1</section>"
  );
}

export function injectWebviewAssetTags(html, webview, context, nonce) {
  const sharedCssUri = versionedWebviewAssetUri(webviewAssetUri(webview, context, "src", "shared", "timeline", "renderer.css"), nonce);
  const cssUri = versionedWebviewAssetUri(webviewAssetUri(webview, context, "src", "q1timeline", "media", "timeline.css"), nonce);
  const sharedJsUri = versionedWebviewAssetUri(webviewAssetUri(webview, context, "src", "shared", "timeline", "renderer.js"), nonce);
  const adapterJsUri = versionedWebviewAssetUri(webviewAssetUri(webview, context, "src", "q1timeline", "media", "timelineAdapter.js"), nonce);
  const hardwareCssUri = versionedWebviewAssetUri(webviewAssetUri(webview, context, "src", "q1timeline", "media", "hardwareView.css"), nonce);
  const hardwareStateUri = versionedWebviewAssetUri(webviewAssetUri(webview, context, "src", "q1timeline", "media", "hardwareState.js"), nonce);
  const hardwareViewUri = versionedWebviewAssetUri(webviewAssetUri(webview, context, "src", "q1timeline", "media", "hardwareView.js"), nonce);
  const jsUri = versionedWebviewAssetUri(webviewAssetUri(webview, context, "src", "q1timeline", "media", "timeline.js"), nonce);
  const linkTag = `<link rel="stylesheet" href="${sharedCssUri}">\n<link rel="stylesheet" href="${cssUri}">\n<link rel="stylesheet" href="${hardwareCssUri}">`;
  const nonceAttribute = nonce ? ` nonce="${nonce}"` : "";
  const scriptTag = [
    `<script${nonceAttribute} src="${sharedJsUri}"></script>`,
    `<script${nonceAttribute} src="${adapterJsUri}"></script>`,
    `<script${nonceAttribute} src="${hardwareStateUri}"></script>`,
    `<script${nonceAttribute} src="${hardwareViewUri}"></script>`,
    `<script${nonceAttribute} src="${jsUri}"></script>`,
  ].join("\n");
  html = clearSharedTimelineRoot(html);
  let output = /<\/head>/i.test(html)
    ? html.replace(/<\/head>/i, `${linkTag}\n</head>`)
    : `${linkTag}\n${html}`;
  output = /<\/body>/i.test(output)
    ? output.replace(/<\/body>/i, `${scriptTag}\n</body>`)
    : `${output}\n${scriptTag}`;
  return output;
}

function injectContentSecurityPolicy(html, csp) {
  const meta = `<meta http-equiv="Content-Security-Policy" content="${csp}">`;
  if (html.includes('http-equiv="Content-Security-Policy"')) {
    return html;
  }
  if (/<head[^>]*>/i.test(html)) {
    return html.replace(/<head[^>]*>/i, (head) => `${head}\n  ${meta}`);
  }
  return `${meta}\n${html}`;
}

function escapeHtmlAttribute(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function injectWebviewState(html, state) {
  const json = escapeHtmlAttribute(JSON.stringify(state));
  const meta = `<meta id="q1timeline-webview-state" data-state="${json}">`;
  if (html.includes('id="q1timeline-webview-state"')) {
    return html;
  }
  if (/<head[^>]*>/i.test(html)) {
    return html.replace(/<head[^>]*>/i, (head) => `${head}\n  ${meta}`);
  }
  return `${meta}\n${html}`;
}

function stripExecutableInlineScripts(html) {
  return html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (match, attrs) => {
    if (/\bsrc\s*=/i.test(attrs)) {
      return match;
    }
    const typeMatch = attrs.match(/\btype\s*=\s*["']?([^"'\s>]+)/i);
    const type = typeMatch ? typeMatch[1].toLowerCase() : "";
    if (type && !["text/javascript", "application/javascript", "module"].includes(type)) {
      return match;
    }
    return "";
  });
}

function cloneJson(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function timeSignature(value) {
  if (value === undefined || value === null) {
    return "";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function eventPositionSignature(event) {
  return [
    event.lane || "",
    event.kind || "",
    timeSignature(event.t0 !== undefined ? event.t0 : event.t0_ns),
    timeSignature(event.t1 !== undefined ? event.t1 : event.t1_ns),
    timeSignature(event.duration !== undefined ? event.duration : event.duration_ns),
  ].join("|");
}

function eventContentSignature(event) {
  return [
    eventPositionSignature(event),
    stableStringify(event.source && event.source.raw),
    stableStringify(event.label),
    stableStringify(event.resolved_args || event.resolvedArgs),
    stableStringify(event.meta && event.meta.resolved_args),
    stableStringify(event.meta && event.meta.applied_state),
    stableStringify(event.meta && event.meta.value),
    stableStringify(event.operands),
    stableStringify(event.args),
  ].join("|");
}

function stableStringify(value) {
  if (value === undefined || value === null || value === "") {
    return "";
  }
  if (typeof value !== "object") {
    return String(value);
  }
  const keys = Object.keys(value).sort();
  return `{${keys.map((key) => `${key}:${stableStringify(value[key])}`).join(",")}}`;
}

function invalidAnalysisParameterValue(value, valuePath = "$") {
  if (typeof value === "boolean") {
    return `Boolean JSON values are not valid analysis parameters (${valuePath}).`;
  }
  if (typeof value === "number" && !Number.isInteger(value)) {
    return `Non-integer JSON numbers are not valid analysis parameters (${valuePath}).`;
  }
  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index += 1) {
      const invalid = invalidAnalysisParameterValue(value[index], `${valuePath}[${index}]`);
      if (invalid) {
        return invalid;
      }
    }
  } else if (value && typeof value === "object") {
    for (const [key, child] of Object.entries(value)) {
      const invalid = invalidAnalysisParameterValue(child, `${valuePath}.${key}`);
      if (invalid) {
        return invalid;
      }
    }
  }
  return undefined;
}

function analysisParameterJsonError(rawValue) {
  try {
    return invalidAnalysisParameterValue(JSON.parse(String(rawValue || "").trim()));
  } catch (_error) {
    return "Enter a valid JSON value.";
  }
}

function eventStableBase(event) {
  const source = event && event.source && typeof event.source === "object" ? event.source : {};
  if (!source.file || !source.line) {
    return `event-id:${event && event.id ? event.id : ""}`;
  }
  const meta = event.meta && typeof event.meta === "object" && !Array.isArray(event.meta)
    ? event.meta
    : {};
  const loopContext = meta.loop_context || meta.loop_id || meta.loop_preview || meta.dynamic_path || "";
  return [
    "source",
    event.sequencer_id || "",
    source.file || "",
    source.line || "",
    source.column || "",
    event.kind || "",
    event.lane || "",
    stableStringify(loopContext),
  ].join("|");
}

function stableEventId(base, occurrenceIndex) {
  return `stable:${crypto.createHash("sha1").update(`${base}|${occurrenceIndex}`).digest("hex").slice(0, 16)}`;
}

function eventDiffEntries(events) {
  const occurrenceCounts = new Map();
  return events.map((event) => {
    const base = eventStableBase(event);
    const occurrenceIndex = occurrenceCounts.get(base) || 0;
    occurrenceCounts.set(base, occurrenceIndex + 1);
    return {
      event,
      key: `${base}|occurrence:${occurrenceIndex}`,
      stableId: stableEventId(base, occurrenceIndex),
    };
  });
}

function withStableId(event, stableId) {
  const copy = cloneJson(event);
  const meta = copy.meta && typeof copy.meta === "object" && !Array.isArray(copy.meta)
    ? { ...copy.meta }
    : {};
  meta.stable_id = stableId;
  copy.meta = meta;
  return copy;
}

function withDiffStatus(event, status, previousEvent) {
  const copy = cloneJson(event);
  const meta = copy.meta && typeof copy.meta === "object" && !Array.isArray(copy.meta)
    ? { ...copy.meta }
    : {};
  meta.diff_status = status;
  if (previousEvent) {
    meta.diff_before = cloneJson(previousEvent);
  }
  copy.meta = meta;
  return copy;
}

function sourceMapObject(timelineIr, key) {
  return timelineIr &&
    timelineIr.source_map &&
    typeof timelineIr.source_map === "object" &&
    timelineIr.source_map[key] &&
    typeof timelineIr.source_map[key] === "object"
    ? timelineIr.source_map[key]
    : {};
}

function ensureSourceMap(timelineIr) {
  if (!timelineIr.source_map || typeof timelineIr.source_map !== "object") {
    timelineIr.source_map = {};
  }
  if (!timelineIr.source_map.by_event_id || typeof timelineIr.source_map.by_event_id !== "object") {
    timelineIr.source_map.by_event_id = {};
  }
  if (!timelineIr.source_map.by_source || typeof timelineIr.source_map.by_source !== "object") {
    timelineIr.source_map.by_source = {};
  }
  return timelineIr.source_map;
}

function preserveRemovedSourceMap(output, previousIr, removedIdMap) {
  if (!removedIdMap.size) {
    return;
  }
  const sourceMap = ensureSourceMap(output);
  const previousByEventId = sourceMapObject(previousIr, "by_event_id");
  for (const [previousId, removedId] of removedIdMap.entries()) {
    if (previousByEventId[previousId] && !sourceMap.by_event_id[removedId]) {
      sourceMap.by_event_id[removedId] = cloneJson(previousByEventId[previousId]);
    }
  }
  const previousBySource = sourceMapObject(previousIr, "by_source");
  for (const [sourceKey, eventIds] of Object.entries(previousBySource)) {
    if (!Array.isArray(eventIds)) {
      continue;
    }
    const removedForSource = eventIds
      .filter((eventId) => removedIdMap.has(eventId))
      .map((eventId) => removedIdMap.get(eventId));
    if (!removedForSource.length) {
      continue;
    }
    const existing = Array.isArray(sourceMap.by_source[sourceKey])
      ? sourceMap.by_source[sourceKey]
      : [];
    const merged = [...existing];
    for (const eventId of removedForSource) {
      if (!merged.includes(eventId)) {
        merged.push(eventId);
      }
    }
    sourceMap.by_source[sourceKey] = merged;
  }
}

function annotateTimelineDiff(previousIr, nextIr) {
  const output = cloneJson(nextIr || {});
  const nextEvents = Array.isArray(output.events) ? output.events : [];
  output.events = nextEvents;
  const previousEvents = previousIr && Array.isArray(previousIr.events) ? previousIr.events : [];
  if (!previousIr || !Array.isArray(previousIr.events)) {
    return output;
  }

  const previousEntries = eventDiffEntries(previousEvents);
  const previousByKey = new Map();
  for (const entry of previousEntries) {
    previousByKey.set(entry.key, entry);
  }

  const nextKeys = new Set();
  const usedEventIds = new Set(nextEvents.map((event) => event && event.id).filter(Boolean));
  output.events = eventDiffEntries(nextEvents).map(({ event, key, stableId }) => {
    if (!event || !event.id) {
      return event;
    }
    nextKeys.add(key);
    const eventWithStableId = withStableId(event, stableId);
    const previousEntry = previousByKey.get(key);
    if (!previousEntry) {
      return withDiffStatus(eventWithStableId, "added");
    }
    if (eventPositionSignature(previousEntry.event) !== eventPositionSignature(event)) {
      return withDiffStatus(eventWithStableId, "shifted", previousEntry.event);
    }
    if (eventContentSignature(previousEntry.event) !== eventContentSignature(event)) {
      return withDiffStatus(eventWithStableId, "changed", previousEntry.event);
    }
    return eventWithStableId;
  });

  const removedIdMap = new Map();
  for (const { event: previousEvent, key, stableId } of previousEntries) {
    if (!previousEvent || !previousEvent.id || nextKeys.has(key)) {
      continue;
    }
    let removedEvent = withDiffStatus(withStableId(previousEvent, stableId), "removed");
    if (usedEventIds.has(removedEvent.id)) {
      removedEvent.id = `removed:${stableId}:${previousEvent.id}`;
    }
    usedEventIds.add(removedEvent.id);
    removedIdMap.set(previousEvent.id, removedEvent.id);
    output.events.push(removedEvent);
  }
  preserveRemovedSourceMap(output, previousIr, removedIdMap);
  return output;
}

function q1asmTokenAt(document, position) {
  if (!document || !position || typeof document.lineAt !== "function") {
    return undefined;
  }
  const line = document.lineAt(position.line).text || "";
  const patterns = [
    /\{[A-Za-z_][A-Za-z0-9_]*\}/g,
    /\$[A-Za-z_][A-Za-z0-9_]*/g,
    /\b[A-Za-z_][A-Za-z0-9_]*\b/g,
  ];
  for (const pattern of patterns) {
    let match;
    while ((match = pattern.exec(line)) !== null) {
      const start = match.index;
      const end = start + match[0].length;
      if (position.character >= start && position.character <= end) {
        return {
          raw: match[0],
          names: tokenLookupNames(match[0]),
          range: new vscode.Range(position.line, start, position.line, end),
        };
      }
    }
  }
  return undefined;
}

function tokenLookupNames(raw) {
  const text = String(raw || "");
  if (text.startsWith("{") && text.endsWith("}") && text.length > 2) {
    return [text.slice(1, -1)];
  }
  if (text.startsWith("$") && text.length > 1) {
    return [text.slice(1), text];
  }
  return text ? [text] : [];
}

function resolvedArgsForSourceLine(timelineIr, filePath, line) {
  const eventIds = lookupEventIdsForSourceLine(timelineIr, filePath, line);
  const eventsById = new Map((timelineIr && timelineIr.events || []).map((event) => [String(event.id), event]));
  const args = [];
  const seen = new Set();
  for (const eventId of eventIds) {
    const event = eventsById.get(String(eventId));
    const resolvedArgs = event && event.meta && Array.isArray(event.meta.resolved_args)
      ? event.meta.resolved_args
      : [];
    for (const arg of resolvedArgs) {
      const key = `${arg.index}|${arg.raw}|${JSON.stringify(arg.value)}|${JSON.stringify(arg.chain || [])}`;
      if (!seen.has(key)) {
        seen.add(key);
        args.push(arg);
      }
    }
  }
  return args;
}

function allResolvedArgs(timelineIr) {
  const args = [];
  const seen = new Set();
  for (const event of timelineIr && timelineIr.events || []) {
    const resolvedArgs = event && event.meta && Array.isArray(event.meta.resolved_args)
      ? event.meta.resolved_args
      : [];
    for (const arg of resolvedArgs) {
      const key = `${arg.index}|${arg.raw}|${JSON.stringify(arg.value)}|${JSON.stringify(arg.chain || [])}`;
      if (!seen.has(key)) {
        seen.add(key);
        args.push(arg);
      }
    }
  }
  return args;
}

function resolvedArgMatchesToken(arg, token) {
  if (!arg || !token) {
    return false;
  }
  if (arg.raw === token.raw) {
    return true;
  }
  const tokenNames = new Set(token.names || []);
  for (const name of tokenLookupNames(arg.raw)) {
    if (tokenNames.has(name)) {
      return true;
    }
  }
  for (const step of Array.isArray(arg.chain) ? arg.chain : []) {
    if (step && step.name && tokenNames.has(step.name)) {
      return true;
    }
  }
  return false;
}

function displayResolvedValueForHover(value) {
  if (value === undefined || value === null) {
    return "";
  }
  if (typeof value === "object" && Object.prototype.hasOwnProperty.call(value, "display")) {
    return String(value.display);
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function displayRawParameterValueForHover(value) {
  return typeof value === "string" ? JSON.stringify(value) : displayResolvedValueForHover(value);
}

function formatResolutionStepForHover(step) {
  if (!step || typeof step !== "object") {
    return "";
  }
  if (step.kind === "def" && step.name && step.raw) {
    return `def ${step.name}=${step.raw}`;
  }
  if (step.kind === "param" && step.name) {
    return `param ${step.name}=${displayRawParameterValueForHover(step.raw_value)}`;
  }
  if (step.kind && step.name) {
    return `${step.kind} ${step.name}=${displayResolvedValueForHover(step.value)}`;
  }
  return displayResolvedValueForHover(step);
}

function escapeMarkdownCode(value) {
  return String(value).replace(/`/g, "\\`");
}

function formatResolvedArgForHover(arg) {
  const lines = [
    `arg ${arg.index}: \`${escapeMarkdownCode(arg.raw)}\` -> \`${escapeMarkdownCode(displayResolvedValueForHover(arg.value))}\``,
  ];
  for (const step of Array.isArray(arg.chain) ? arg.chain : []) {
    const formatted = formatResolutionStepForHover(step);
    if (formatted) {
      lines.push(`- \`${escapeMarkdownCode(formatted)}\``);
    }
  }
  return lines.join("\n");
}

function normalizedFsPath(filePath) {
  const resolved = path.resolve(String(filePath || ""));
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

function visibleSourceEditorColumn(sourcePath, visibleTextEditors) {
  const target = normalizedFsPath(sourcePath);
  for (const editor of visibleTextEditors || []) {
    const editorPath = editor && editor.document && editor.document.uri
      ? editor.document.uri.fsPath
      : undefined;
    if (editorPath && normalizedFsPath(editorPath) === target && editor.viewColumn !== undefined) {
      return editor.viewColumn;
    }
  }
  return undefined;
}

function workspaceTextDocumentForPath(sourcePath, textDocuments) {
  const target = normalizedFsPath(sourcePath);
  for (const document of textDocuments || []) {
    const documentPath = document && document.uri ? document.uri.fsPath : undefined;
    if (documentPath && normalizedFsPath(documentPath) === target) {
      return document;
    }
  }
  return undefined;
}

function tabInputMatchesSourcePath(input, sourcePath) {
  if (!input) {
    return false;
  }
  const target = normalizedFsPath(sourcePath);
  const candidates = tabInputUris(input);
  return candidates.some((uri) => uri && uri.fsPath && normalizedFsPath(uri.fsPath) === target);
}

function tabInputUris(input) {
  if (!input) {
    return [];
  }
  return [input.uri, input.modified, input.original].filter(Boolean);
}

function tabInputHasQ1asm(input) {
  return tabInputUris(input).some((uri) => uri && uri.fsPath && isQ1asmPath(uri.fsPath));
}

function openTabGroupColumn(sourcePath, tabGroups) {
  const groups = tabGroups && Array.isArray(tabGroups.all) ? tabGroups.all : [];
  for (const group of groups) {
    if (!group || group.viewColumn === undefined) {
      continue;
    }
    for (const tab of group.tabs || []) {
      if (tabInputMatchesSourcePath(tab && tab.input, sourcePath)) {
        return group.viewColumn;
      }
    }
  }
  return undefined;
}

function q1asmTabGroupColumn(tabGroups) {
  const groups = tabGroups && Array.isArray(tabGroups.all) ? tabGroups.all : [];
  for (const group of groups) {
    if (!group || group.viewColumn === undefined) {
      continue;
    }
    for (const tab of group.tabs || []) {
      if (tabInputHasQ1asm(tab && tab.input)) {
        return group.viewColumn;
      }
    }
  }
  return undefined;
}

export class TimelineController {
  constructor(context) {
    this.context = context;
    this.panel = undefined;
    this.panelDisposables = [];
    this.projectUri = undefined;
    this.singleFileUri = undefined;
    this.singleFileMode = false;
    this.outputDir = undefined;
    this.timelineIr = undefined;
    this.lastAnalyzerTimelineIr = undefined;
    this.comparisonBaselineWarningPath = undefined;
    this.comparison = { mode: "none" };
    this.analyzerResult = undefined;
    this.lastPreviewHtml = undefined;
    this.changeTimer = undefined;
    this.sourceRangeTimer = undefined;
    this.webviewReady = false;
    this.sourceRevision = 0;
    this.analyzedSourceRevision = 0;
    this.analysisRequestId = 0;
    this.analysisInFlight = false;
    this.analysisQueued = false;
    this.analysisFailureCount = 0;
    this.analysisStatus = { status: "idle", message: "Idle" };
    this.diagnosticSummary = summarizeDiagnostics([]);
    this.analyzerDiagnostics = [];
    this.currentViewMode = undefined;
    this.branchAssumptionOverrides = new Map();
    this.loopPreviewOverrides = new Map();
    this.executionReplayOverride = undefined;
    this.playbackClient = undefined;
    this.playbackInFlight = false;
    this.playbackRunning = false;
    this.playbackSnapshotId = undefined;
    this.executionState = { status: "idle", can_resume: true, steps: 0 };
    this.parameterOverrides = new Map();
    this.pendingTarget = undefined;
    this.pendingBranchSelection = undefined;
    this.projectRelatedPaths = new Set();
    this.lastSourceViewColumn = undefined;
    this.output = vscode.window.createOutputChannel("Q1ASM Timeline");
    this.diagnostics = vscode.languages.createDiagnosticCollection("q1timeline");
    this.diagnosticsManager = new DiagnosticsManager(
      vscode,
      this.diagnostics,
      (sourceFile) => this.resolveSourcePath(sourceFile)
    );
    this.sourceSelectionDecoration = typeof vscode.window.createTextEditorDecorationType === "function"
      ? vscode.window.createTextEditorDecorationType({
          border: "1px solid var(--vscode-editorInfo-foreground)",
          isWholeLine: true,
        })
      : undefined;
    this.activeSourceDecorationEditor = undefined;
    this.hardwareSourceReveal = undefined;
    this.output.appendLine("Q1ASM Timeline extension activated.");
    context.subscriptions.push(this.output);
    context.subscriptions.push(this.diagnostics);
    if (this.sourceSelectionDecoration) {
      context.subscriptions.push(this.sourceSelectionDecoration);
    }
    context.subscriptions.push(
      vscode.workspace.onDidSaveTextDocument((document) => {
        if (this.isProjectRelated(document.uri)) {
          this.invalidateSourceAnalysis();
          this.setUnsavedChangesStatus();
          if (this.shouldAnalyzeOnSave()) {
            this.runAnalysis();
          }
        }
      })
    );
    context.subscriptions.push(
      vscode.workspace.onDidChangeTextDocument((event) => {
        if (this.isProjectRelated(event.document.uri)) {
          this.invalidateSourceAnalysis();
          this.setUnsavedChangesStatus();
          if (this.shouldAnalyzeOnType()) {
            this.scheduleAnalysis();
          }
        }
      })
    );
    context.subscriptions.push(
      vscode.window.onDidChangeTextEditorSelection((event) => {
        this.rememberSourceViewColumn(event.textEditor);
        this.followSourceEditor(event.textEditor, event.kind);
      })
    );
    if (typeof vscode.window.onDidChangeActiveTextEditor === "function") {
      context.subscriptions.push(
        vscode.window.onDidChangeActiveTextEditor((editor) => {
          this.rememberSourceViewColumn(editor);
          this.followSourceEditor(editor);
        })
      );
    }
    this.rememberSourceViewColumn(vscode.window.activeTextEditor);
  }

  setProjectUri(uri) {
    if (!this.projectUri || this.projectUri.fsPath !== uri.fsPath) {
      this.invalidateSourceAnalysis();
      this.webviewReady = false;
      this.lastAnalyzerTimelineIr = undefined;
      this.comparisonBaselineWarningPath = undefined;
      this.comparison = { mode: "none" };
      this.lastPreviewHtml = undefined;
      this.timelineIr = undefined;
      this.clearAnalyzerOverrides();
    }
    this.projectUri = uri;
  }

  clearAnalyzerOverrides() {
    this.executionReplayOverride = undefined;
    if (this.branchAssumptionOverrides) {
      this.branchAssumptionOverrides.clear();
    }
    if (this.loopPreviewOverrides) {
      this.loopPreviewOverrides.clear();
    }
    if (this.parameterOverrides) {
      this.parameterOverrides.clear();
    }
  }

  comparisonBaselinePath() {
    if (!this.outputDir || path.basename(path.resolve(this.outputDir)).toLowerCase() !== ".q1timeline") {
      return undefined;
    }
    return path.join(this.outputDir, COMPARISON_BASELINE_FILENAME);
  }

  async saveComparisonBaseline() {
    if (!isTimelineIr(this.lastAnalyzerTimelineIr)) {
      vscode.window.showWarningMessage(
        "Run a successful Q1ASM timeline analysis before saving a comparison baseline."
      );
      return;
    }
    const baselinePath = this.comparisonBaselinePath();
    if (!this.projectUri || !baselinePath) {
      vscode.window.showWarningMessage(
        "Open a q1timeline project before saving a comparison baseline."
      );
      return;
    }
    try {
      await fsPromises.mkdir(this.outputDir, { recursive: true });
      await fsPromises.writeFile(
        baselinePath,
        `${JSON.stringify(this.lastAnalyzerTimelineIr, null, 2)}\n`,
        "utf-8"
      );
      this.comparisonBaselineWarningPath = undefined;
      this.output.appendLine(`Saved timeline comparison baseline: ${baselinePath}`);
      this.comparison = { ...this.comparison, nextMode: "saved", pending: true };
      this.postWebviewMessage({ type: "setComparison", comparison: this.comparison });
      vscode.window.showInformationMessage(
        `Timeline comparison baseline saved for ${path.basename(this.projectUri.fsPath)}.`
      );
    } catch (error) {
      this.output.appendLine(`Unable to save timeline comparison baseline: ${error.message}`);
      vscode.window.showErrorMessage(`Unable to save timeline comparison baseline: ${error.message}`);
    }
  }

  async clearComparisonBaseline() {
    const baselinePath = this.comparisonBaselinePath();
    if (!this.projectUri || !baselinePath) {
      vscode.window.showWarningMessage(
        "Open a q1timeline project before clearing its comparison baseline."
      );
      return;
    }
    try {
      await fsPromises.rm(baselinePath, { force: true });
      this.comparisonBaselineWarningPath = undefined;
      this.output.appendLine(`Cleared timeline comparison baseline: ${baselinePath}`);
      this.comparison = { ...this.comparison, nextMode: "previous", pending: true };
      this.postWebviewMessage({ type: "setComparison", comparison: this.comparison });
      vscode.window.showInformationMessage(
        "Timeline comparison baseline cleared. Future runs will compare with the previous run."
      );
    } catch (error) {
      this.output.appendLine(`Unable to clear timeline comparison baseline: ${error.message}`);
      vscode.window.showErrorMessage(`Unable to clear timeline comparison baseline: ${error.message}`);
    }
  }

  async loadComparisonBaseline() {
    const baselinePath = this.comparisonBaselinePath();
    if (!baselinePath) {
      return undefined;
    }
    let parsed;
    try {
      parsed = JSON.parse(await fsPromises.readFile(baselinePath, "utf-8"));
    } catch (error) {
      if (error && error.code === "ENOENT") {
        return undefined;
      }
      this.warnInvalidComparisonBaseline(baselinePath, error.message);
      return undefined;
    }
    if (!isTimelineIr(parsed)) {
      this.warnInvalidComparisonBaseline(baselinePath, "expected an object with an events array");
      return undefined;
    }
    this.comparisonBaselineWarningPath = undefined;
    this.output.appendLine(`Using saved timeline comparison baseline: ${baselinePath}`);
    return parsed;
  }

  warnInvalidComparisonBaseline(baselinePath, reason) {
    this.output.appendLine(`Ignoring invalid timeline comparison baseline ${baselinePath}: ${reason}`);
    if (this.comparisonBaselineWarningPath === baselinePath) {
      return;
    }
    this.comparisonBaselineWarningPath = baselinePath;
    vscode.window.showWarningMessage(
      "The saved timeline comparison baseline is invalid and was ignored; comparing with the previous run instead."
    );
  }

  async openTarget(target) {
    this.setProjectUri(vscode.Uri.file(target.projectFile));
    this.outputDir = q1timelineProjectOutputDir(target.projectFile);
    this.singleFileUri = undefined;
    this.singleFileMode = false;
    this.pendingBranchSelection = undefined;
    this.pendingTarget = target;
    await this.openPreview({ preserveProject: true });
  }

  async openPreview(options = {}) {
    this.cancelSourceRangeZoom();
    const sourceUri = this.previewSourceUri(options);
    const explicitSourceUri = options.sourceUri && options.sourceUri.fsPath ? options.sourceUri : undefined;
    let skipProjectDiscovery = false;
    if (!options.preserveProject) {
      const projectFile = this.config().get("projectFile", "q1timeline.yml").replace(/\\/g, "/");
      if (explicitSourceUri && isQ1asmPath(explicitSourceUri.fsPath)) {
        const folderProjectUri = this.findProjectFileInQ1asmFolder(explicitSourceUri, projectFile);
        if (folderProjectUri) {
          this.setProjectUri(folderProjectUri);
          this.singleFileUri = undefined;
          this.singleFileMode = false;
        } else {
          this.projectUri = undefined;
          this.outputDir = undefined;
          this.singleFileUri = undefined;
          this.singleFileMode = false;
          this.timelineIr = undefined;
          this.lastAnalyzerTimelineIr = undefined;
          this.lastPreviewHtml = undefined;
          skipProjectDiscovery = true;
        }
      } else if (this.shouldUseSingleFileForActiveQ1asm(sourceUri)) {
        this.projectUri = undefined;
        this.outputDir = undefined;
        this.singleFileUri = undefined;
        this.singleFileMode = false;
        this.timelineIr = undefined;
        this.lastAnalyzerTimelineIr = undefined;
        this.lastPreviewHtml = undefined;
      } else if (this.singleFileMode && this.activeQ1asmChanged(sourceUri)) {
        this.projectUri = undefined;
        this.outputDir = undefined;
        this.singleFileUri = undefined;
        this.singleFileMode = false;
        this.timelineIr = undefined;
        this.lastAnalyzerTimelineIr = undefined;
        this.lastPreviewHtml = undefined;
      } else {
        const activeProjectUri = this.findProjectFileUpward(
          sourceUri,
          projectFile
        );
        if (activeProjectUri) {
          this.setProjectUri(activeProjectUri);
          this.singleFileUri = undefined;
          this.singleFileMode = false;
        }
      }
    }
    if (!this.projectUri && !skipProjectDiscovery) {
      const projectUri = await this.findProjectFile(sourceUri);
      if (projectUri) {
        this.setProjectUri(projectUri);
      }
    }
    if (!this.projectUri) {
      if (!sourceUri || !isQ1asmPath(sourceUri.fsPath)) {
        vscode.window.showWarningMessage(
          "Open or select a .q1asm file, or create q1timeline.yml, before opening a Q1ASM timeline."
        );
        return;
      }
      const singleFileProject = await this.createSingleFileProject(sourceUri);
      if (singleFileProject) {
        this.setProjectUri(singleFileProject);
      }
      if (!this.projectUri) {
        vscode.window.showWarningMessage("No q1timeline.yml found in this workspace.");
        return;
      }
    }
    if (!this.singleFileMode) {
      this.outputDir = path.join(path.dirname(this.projectUri.fsPath), ".q1timeline");
    }
    await this.refreshProjectRelatedPaths();
    this.startWatchers();
    if (this.panel) {
      this.output.appendLine("Reusing existing Q1Lens preview panel.");
      this.panel.reveal();
      await this.runAnalysis();
      return;
    }
    const previewColumn = await this.previewPanelViewColumn();
    this.panel = vscode.window.createWebviewPanel(
      "q1timeline.preview",
      "Q1Lens",
      previewColumn,
      {
        // Required for toolbar controls, block clicks, and source-selection highlights.
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: webviewLocalResourceRoots(this.context),
      }
    );
    this.disposePanelDisposables();
    this.panelDisposables.push(
      this.panel.webview.onDidReceiveMessage((message) => this.handleWebviewMessage(message)),
      this.panel.onDidDispose(() => {
        this.resetPlayback();
        this.cancelSourceRangeZoom();
        this.pendingBranchSelection = undefined;
        this.webviewReady = false;
        this.disposePanelDisposables();
        this.panel = undefined;
      })
    );
    await this.runAnalysis();
  }

  async previewPanelViewColumn() {
    try {
      await vscode.commands.executeCommand("workbench.action.newGroupAbove");
      return vscode.ViewColumn.Active;
    } catch (error) {
      this.output.appendLine(`Could not create an editor group above for q1timeline preview: ${String(error)}`);
      return vscode.ViewColumn.Beside;
    }
  }

  previewSourceUri(options = {}) {
    if (options.sourceUri && options.sourceUri.fsPath) {
      return options.sourceUri;
    }
    return vscode.window.activeTextEditor ? vscode.window.activeTextEditor.document.uri : undefined;
  }

  async refreshPreview() {
    if (!this.panel) {
      await this.openPreview();
      return;
    }
    await this.runAnalysis();
  }

  async selectProjectFile() {
    const projectFile = this.config().get("projectFile", "q1timeline.yml").replace(/\\/g, "/");
    const matches = await this.findWorkspaceProjectFiles(projectFile);
    if (!matches.length) {
      vscode.window.showWarningMessage(`No ${projectFile} found in this workspace.`);
      return;
    }
    const selectedUri = await this.pickProjectFile(matches, `Select ${projectFile}`);
    if (!selectedUri) {
      return;
    }
    this.setProjectUri(selectedUri);
    this.singleFileUri = undefined;
    this.singleFileMode = false;
    await this.openPreview({ preserveProject: true });
  }

  async selectQ1asmFilesInFolder(sourceUri) {
    sourceUri = this.activeQ1asmUri(sourceUri);
    if (!sourceUri) {
      vscode.window.showWarningMessage("Open or select a .q1asm file before choosing Q1ASM files.");
      return;
    }
    const q1asmUris = await this.q1asmFilesInFolder(sourceUri);
    if (!q1asmUris.length) {
      vscode.window.showWarningMessage("No Q1ASM files found in this folder.");
      return;
    }
    const items = q1asmUris.map((uri) => ({
      label: path.basename(uri.fsPath),
      description: uri.fsPath,
      uri,
      picked: true,
    }));
    const selected = await vscode.window.showQuickPick(items, {
      canPickMany: true,
      placeHolder: "Select Q1ASM files to show in this timeline",
    });
    if (!selected || !selected.length) {
      return;
    }
    const projectUri = await this.createSelectedFolderProject(sourceUri, selected.map((item) => item.uri));
    if (!projectUri) {
      return;
    }
    this.setProjectUri(projectUri);
    await this.openPreview({ preserveProject: true });
  }

  async openQ1asmFilesInFolder(sourceUri) {
    sourceUri = this.activeQ1asmUri(sourceUri);
    if (!sourceUri) {
      vscode.window.showWarningMessage("Open or select a .q1asm file before opening Q1ASM files.");
      return;
    }
    const projectCandidates = this.findProjectFileCandidatesUpward(sourceUri);
    if (projectCandidates.length) {
      const existingProjectUri = projectCandidates.length === 1
        ? projectCandidates[0]
        : await this.pickProjectFile(projectCandidates, "Select q1timeline project");
      if (!existingProjectUri) {
        return;
      }
      this.singleFileUri = undefined;
      this.singleFileMode = false;
      this.setProjectUri(existingProjectUri);
      await this.openPreview({ preserveProject: true });
      return;
    }
    const q1asmUris = await this.q1asmFilesInFolder(sourceUri);
    if (!q1asmUris.length) {
      vscode.window.showWarningMessage("No Q1ASM files found in this folder.");
      return;
    }
    const paramsFile = await this.paramsFileForAutoGeneratedProject(sourceUri);
    const projectUri = await this.createSelectedFolderProject(sourceUri, q1asmUris, {
      paramsFile,
      showMissingProjectWarning: false,
    });
    if (!projectUri) {
      return;
    }
    this.setProjectUri(projectUri);
    await this.openPreview({ preserveProject: true });
  }

  async revealCurrentLineInTimeline() {
    const editor = vscode.window.activeTextEditor;
    const activeProjectUri = this.activeProjectUriForEditor(editor);
    if (activeProjectUri && (!this.projectUri || normalizedFsPath(activeProjectUri.fsPath) !== normalizedFsPath(this.projectUri.fsPath))) {
      this.queueActiveEditorReveal(editor);
      this.setProjectUri(activeProjectUri);
      this.singleFileUri = undefined;
      this.singleFileMode = false;
      await this.openPreview({ preserveProject: true });
      return;
    }
    if (!this.panel) {
      this.queueActiveEditorReveal(editor);
      await this.openPreview();
      return;
    }
    if (editor && this.timelineIr) {
      this.highlightActiveSourceLine(editor);
      return;
    }
    this.queueActiveEditorReveal(editor);
  }

  queueActiveEditorReveal(editor) {
    if (!editor || !editor.document || !editor.document.uri || !editor.document.uri.fsPath) {
      return;
    }
    this.pendingBranchSelection = undefined;
    this.pendingTarget = {
      q1asmFile: editor.document.uri.fsPath,
      line: (editor.selection?.active?.line ?? 0) + 1,
    };
  }

  async zoomToSelection() {
    const editor = vscode.window.activeTextEditor;
    const sourceUri = editor?.document?.uri;
    const selection = editor?.selection;
    if (!sourceUri?.fsPath || !isQ1asmPath(sourceUri.fsPath) || !selection?.start || !selection?.end || selection.isEmpty) {
      vscode.window.showInformationMessage("Select a block in a Q1ASM file to view it in the timeline.");
      return;
    }
    if (editor.document.isDirty) {
      vscode.window.showInformationMessage("Save the Q1ASM file before viewing its selection in the timeline so source lines match the analyzed program.");
      return;
    }
    const startLine = selection.start.line + 1;
    const endLine = Math.max(startLine, selection.end.line + (selection.end.character === 0 ? 0 : 1));
    this.pendingBranchSelection = undefined;
    this.pendingTarget = { q1asmFile: sourceUri.fsPath, line: startLine, endLine, zoomToRange: true };
    const activeProjectUri = this.activeProjectUriForEditor(editor);
    if (activeProjectUri) {
      this.setProjectUri(activeProjectUri);
      this.singleFileUri = undefined;
      this.singleFileMode = false;
    }
    const preserveProject = Boolean(activeProjectUri || (this.projectUri && this.isProjectRelated(sourceUri)));
    await this.openPreview({ sourceUri, preserveProject });
  }

  activeProjectUriForEditor(editor) {
    if (!editor || !editor.document || !editor.document.uri) {
      return undefined;
    }
    const projectFile = this.config().get("projectFile", "q1timeline.yml").replace(/\\/g, "/");
    return this.findProjectFileUpward(editor.document.uri, projectFile);
  }

  openSettings() {
    vscode.commands.executeCommand("workbench.action.openSettings", "q1lens.q1timeline");
  }

  showAnalyzerLog() {
    this.output.show();
  }

  setAnalysisStatus(status, message) {
    this.analysisStatus = { status, message };
    this.postWebviewMessage({ type: "setAnalysisStatus", status, message });
  }

  setDiagnosticSummary(summary) {
    this.diagnosticSummary = summary;
    this.postWebviewMessage({ type: "setDiagnosticSummary", summary });
  }

  setAnalyzerDiagnostics(diagnostics) {
    this.analyzerDiagnostics = Array.isArray(diagnostics) ? diagnostics : [];
    this.postWebviewMessage({ type: "setDiagnostics", diagnostics: this.webviewDiagnostics() });
  }

  webviewDiagnostics() {
    return this.analyzerDiagnostics.map((item, index) => ({
      index,
      severity: item && item.severity ? String(item.severity) : "info",
      category: item && item.category ? String(item.category) : "",
      message: item && item.message ? String(item.message) : "",
      confidence: item && item.confidence ? String(item.confidence) : "",
      source: item && item.source
        ? {
            file: item.source.file ? path.basename(String(item.source.file)) : "",
            line: item.source.line,
            column: item.source.column,
          }
        : undefined,
    }));
  }

  setUnsavedChangesStatus() {
    const hasUnsavedChanges = this.hasUnsavedProjectChanges();
    this.postWebviewMessage({ type: "setUnsavedChanges", hasUnsavedChanges });
  }

  hasUnsavedProjectChanges() {
    const documents = vscode.workspace.textDocuments || [];
    return documents.some((document) => document.isDirty && this.isProjectRelated(document.uri));
  }

  checkAnalyzerInstallation() {
    return vscode.commands.executeCommand("q1lens.checkEnvironment", "q1timeline.refreshPreview");
  }

  config() {
    const q1lensConfig = vscode.workspace.getConfiguration("q1lens");
    const qbloxConfig = vscode.workspace.getConfiguration("qbloxTimeline");
    const legacyConfig = vscode.workspace.getConfiguration("q1timeline");
    return {
      get(key, fallback) {
        return q1timelineConfigValue(qbloxConfig, legacyConfig, key, fallback, q1lensConfig);
      },
    };
  }

  updateMode() {
    return updatePolicy.normalizeUpdateMode(this.config().get("updateMode", "onSave"));
  }

  viewMode() {
    return this.currentViewMode || this.config().get("view.defaultMode", "normal");
  }

  analyzerOverrideArgs(extraArgs = []) {
    const args = [];
    for (const [branchId, branchPath] of Array.from(this.branchAssumptionOverrides || []).sort()) {
      args.push("--branch-assumption", `${branchId}=${branchPath}`);
    }
    for (const [loopKey, visibleIterations] of Array.from(this.loopPreviewOverrides || []).sort()) {
      args.push("--loop-preview", `${loopKey}=${visibleIterations}`);
    }
    args.push(...extraArgs);
    for (const [name, rawValue] of Array.from(this.parameterOverrides || []).sort()) {
      args.push("--set-param", `${name}=${rawValue}`);
    }
    return args;
  }

  executionArgs() {
    const configuration = this.config();
    let mode = configuration.get("executionMode", "project");
    const replayOverride = this.executionReplayOverride;
    if (
      replayOverride && (replayOverride.scope === "project" || mode !== "concrete" && mode !== "simulation") &&
      this.projectUri && replayOverride.projectPath === normalizedFsPath(this.projectUri.fsPath) &&
      replayOverride.sourcePath === (this.singleFileUri ? normalizedFsPath(this.singleFileUri.fsPath) : undefined)
    ) {
      mode = replayOverride.mode;
    }
    if (mode === "project") {
      return [];
    }
    return ["--execution-mode", mode, "--max-steps", String(configuration.get("maxSteps", 10000))];
  }

  setExecutionState(state) {
    this.executionState = state;
    this.postWebviewMessage({ type: "setExecutionState", state });
  }

  resetPlayback() {
    const client = this.playbackClient;
    const wasActive = Boolean(client || this.executionState?.status !== "idle");
    this.playbackClient = undefined;
    this.playbackRunning = false;
    this.playbackSnapshotId = undefined;
    client?.dispose();
    this.executionState = { status: "idle", can_resume: true, steps: 0 };
    if (wasActive) this.setExecutionState(this.executionState);
  }

  createPlaybackClient() {
    const configuration = this.config();
    const args = ["-u", "-m", "q1timeline.playback", "--project", this.projectUri.fsPath];
    for (const [name, value] of this.parameterOverrides) args.push("--set-param", `${name}=${value}`);
    return new PlaybackClient(configuration.get("pythonPath", "python"),
      prependPythonArgs(configuration.get("pythonArgs", []), args), {
        cwd: this.projectRootCwd(),
        env: { ...process.env, ...this.normaliseAnalyzerEnv(configuration.get("analyzer.env", {})) },
        onStderr: (text) => this.output.appendLine(`Playback: ${text}`),
      });
  }

  pauseExecution() {
    this.playbackRunning = false;
  }

  async setExecutionBranch(eventId, branchId, path) {
    if (!this.playbackClient || this.playbackInFlight || !["taken", "fallthrough"].includes(path)) return;
    const event = this.timelineIr?.events?.find((item) => item.id === eventId);
    if (event?.meta?.playback_branch_id !== branchId) return;
    await this.runPlayback({ command: "choose", branch_id: branchId, path });
  }

  async setExecutionFeedback(eventId, feedbackId, value, delayNs) {
    if (!this.playbackClient || this.playbackInFlight) return;
    const event = this.timelineIr?.events?.find((item) => item.id === eventId);
    if (event?.meta?.playback_feedback_id !== feedbackId) return;
    await this.runPlayback({ command: "set_feedback", feedback_id: feedbackId, value, delay_ns: delayNs });
  }

  async runPlayback(update) {
    if (!this.panel || !this.projectUri || !this.outputDir || this.analysisInFlight || this.playbackInFlight) return;
    if (this.hasUnsavedProjectChanges()) {
      this.setExecutionState({ status: "blocked", can_resume: false, reason: "Save the source before calculating its execution." });
      return;
    }
    const sourceRevision = this.sourceRevision;
    let client;
    this.playbackInFlight = true;
    this.playbackRunning = true;
    this.cancelSourceRangeZoom();
    this.pendingBranchSelection = undefined;
    this.setExecutionState({ ...this.executionState, status: "running", reason: "Calculating for up to 5 seconds." });
    try {
      client = this.playbackClient || (this.playbackClient = this.createPlaybackClient());
      if (update) await client.request(update);
      const deadline = Date.now() + 5000;
      while (this.playbackClient === client && this.playbackRunning) {
        const result = await client.request({ command: "run", budget_ms: 100, max_steps: 1000 });
        if (this.playbackClient !== client || this.sourceRevision !== sourceRevision) return;
        const state = result.playback;
        if (!state || typeof state.can_resume !== "boolean") throw new Error("Invalid playback state response.");
        this.setExecutionState({ ...state, status: "running" });
        if (!state.can_resume || Date.now() >= deadline) break;
      }
      if (this.playbackClient !== client || this.sourceRevision !== sourceRevision) return;
      const result = await client.request({ command: "snapshot" });
      if (this.playbackClient !== client || this.sourceRevision !== sourceRevision) return;
      if (!isTimelineIr(result.ir) || !result.ir.playback) throw new Error("Invalid playback timeline response.");
      this.playbackSnapshotId = crypto.randomUUID();
      await this.publishPlaybackSnapshot(result.ir, client);
      if (this.playbackClient === client && this.sourceRevision === sourceRevision) this.setExecutionState(result.ir.playback);
    } catch (error) {
      if (client && this.playbackClient !== client) return;
      this.output.appendLine(`Playback failed: ${error.message}`);
      this.playbackClient?.dispose();
      this.playbackClient = undefined;
      this.setExecutionState({ status: "error", can_resume: true, reason: error.message });
    } finally {
      this.playbackInFlight = false;
      this.playbackRunning = false;
    }
  }

  async publishPlaybackSnapshot(ir, client) {
    const requestId = ++this.analysisRequestId;
    const sourceRevision = this.sourceRevision;
    const configuration = this.config();
    const irPath = path.join(this.outputDir, `playback-${requestId}.json`);
    const htmlPath = path.join(this.outputDir, `playback-${requestId}.html`);
    const isCurrent = () => this.playbackClient === client && this.sourceRevision === sourceRevision && this.isCurrentAnalysis(requestId);
    try {
      await fsPromises.mkdir(this.outputDir, { recursive: true });
      if (!isCurrent()) return;
      await fsPromises.writeFile(irPath, JSON.stringify(ir));
      if (!isCurrent()) return;
      await this.runRender(configuration.get("pythonPath", "python"), configuration.get("pythonArgs", []),
        irPath, htmlPath, configuration.get("analyzer.timeoutMs", 30000), this.projectRootCwd(),
        this.normaliseAnalyzerEnv(configuration.get("analyzer.env", {})), this.viewMode(), requestId);
      if (!isCurrent()) return;
      const html = await fsPromises.readFile(htmlPath, "utf-8");
      if (!isCurrent()) return;
      this.lastAnalyzerTimelineIr = undefined;
      this.comparison = { mode: "none" };
      this.setAnalysisStatus("idle", "Execution calculated.");
      if (await this.refreshWebview({ ir, html, isCurrent }) && isCurrent()) this.analyzedSourceRevision = sourceRevision;
    } finally {
      await Promise.all([irPath, htmlPath].map((file) => fsPromises.rm(file, { force: true }).catch(() => {})));
    }
  }

  async prepareExecutionReplay(sequencerId, scope = "sequencer") {
    if (!this.panel || !this.projectUri || !this.outputDir) {
      return;
    }
    const ir = this.timelineIr;
    if (!ir || !(
      (ir.sequencers || []).some((sequencer) => String(sequencer.id) === sequencerId) ||
      (ir.events || []).some((event) => String(event.sequencer_id) === sequencerId) ||
      Object.prototype.hasOwnProperty.call(ir.execution || {}, sequencerId)
    )) {
      return;
    }
    const configuredMode = this.config().get("executionMode", "project");
    const recordedMode = ir.execution && ir.execution[sequencerId]?.mode;
    const mode = scope === "project" ? "simulation" : configuredMode === "concrete" || configuredMode === "simulation"
      ? configuredMode
      : recordedMode === "concrete" || recordedMode === "simulation" ? recordedMode : "simulation";
    this.executionReplayOverride = {
      projectPath: normalizedFsPath(this.projectUri.fsPath),
      sourcePath: this.singleFileUri ? normalizedFsPath(this.singleFileUri.fsPath) : undefined,
      mode,
      scope,
    };
    this.lastAnalyzerTimelineIr = undefined;
    this.output.appendLine(`q1timeline execution replay: ${sequencerId} (${mode}).`);
    await this.refreshPreview();
  }

  parameterOverrideSummary() {
    return Array.from(this.parameterOverrides || [])
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([name, rawValue]) => `${name}=${rawValue}`)
      .join(", ");
  }

  async setAnalysisParameter() {
    if (!this.projectUri || !this.outputDir) {
      vscode.window.showWarningMessage("Open a Q1ASM timeline project before setting analysis parameters.");
      return;
    }
    const rawName = await vscode.window.showInputBox({
      prompt: "Analysis parameter name",
      placeHolder: "DRIVE_DURATION",
      validateInput: (value) => {
        if (!String(value || "").trim()) {
          return "Parameter name cannot be blank.";
        }
        if (String(value).includes("=")) {
          return "Parameter name cannot contain '='.";
        }
        return undefined;
      },
    });
    if (rawName === undefined) {
      return;
    }
    const name = rawName.trim();
    if (!name || name.includes("=")) {
      vscode.window.showErrorMessage("Invalid analysis parameter name.");
      return;
    }
    const rawValue = await vscode.window.showInputBox({
      prompt: `JSON value for ${name}`,
      placeHolder: "40",
      validateInput: analysisParameterJsonError,
    });
    if (rawValue === undefined) {
      return;
    }
    const value = rawValue.trim();
    const error = analysisParameterJsonError(value);
    if (error) {
      vscode.window.showErrorMessage(error);
      return;
    }
    this.parameterOverrides.set(name, value);
    this.lastAnalyzerTimelineIr = undefined;
    const summary = this.parameterOverrideSummary();
    this.output.appendLine(`q1timeline analysis parameter overrides: ${summary}`);
    vscode.window.showInformationMessage(`Q1Lens analysis parameter overrides: ${summary}`);
    await this.runAnalysis();
  }

  async clearAnalysisParameterOverrides() {
    if (!this.parameterOverrides || this.parameterOverrides.size === 0) {
      vscode.window.showInformationMessage("Q1Lens has no active analysis parameter overrides.");
      return;
    }
    this.parameterOverrides.clear();
    this.lastAnalyzerTimelineIr = undefined;
    this.output.appendLine("q1timeline analysis parameter overrides cleared.");
    vscode.window.showInformationMessage("Q1Lens analysis parameter overrides cleared.");
    await this.runAnalysis();
  }

  async updateInlineAnalysisParameter(message) {
    const reject = (error) => this.postWebviewMessage({
      type: "analysisParameterError",
      name: message.name,
      message: error,
    });
    if (!this.projectUri || !this.outputDir || message.projectFile !== this.projectUri.fsPath) {
      reject("This parameter belongs to a different or closed project. Refresh the timeline and try again.");
      return;
    }
    const parameters = this.timelineIr?.analysis?.parameters;
    if (!Array.isArray(parameters) || !parameters.some((parameter) => parameter.name === message.name)) {
      reject("This parameter is no longer available. Refresh the timeline and try again.");
      return;
    }
    if (this.analysisInFlight) {
      reject("Analysis is still running. Try again when it finishes.");
      return;
    }
    const previousValue = this.parameterOverrides.get(message.name);
    const previousBaseline = this.lastAnalyzerTimelineIr;
    if (message.type === "resetAnalysisParameter") {
      if (!this.parameterOverrides.has(message.name)) {
        reject("This parameter has no temporary override to reset.");
        return;
      }
      this.parameterOverrides.delete(message.name);
      this.output.appendLine(`q1timeline analysis parameter override cleared: ${message.name}`);
    } else {
      const value = message.valueJson.trim();
      const error = analysisParameterJsonError(value);
      if (error) {
        reject(error);
        return;
      }
      this.parameterOverrides.set(message.name, value);
      this.output.appendLine(`q1timeline analysis parameter override: ${message.name}=${value}`);
    }
    this.lastAnalyzerTimelineIr = undefined;
    const attemptedValue = this.parameterOverrides.get(message.name);
    await this.runAnalysis({
      onFailure: (error) => {
        if (this.projectUri?.fsPath !== message.projectFile || this.parameterOverrides.get(message.name) !== attemptedValue) {
          return false;
        }
        if (previousValue === undefined) {
          this.parameterOverrides.delete(message.name);
        } else {
          this.parameterOverrides.set(message.name, previousValue);
        }
        this.lastAnalyzerTimelineIr = previousBaseline;
        reject(`Parameter change was not applied: ${error}`);
      },
    });
  }

  async setBranchAssumption(branchId, path, eventId) {
    this.pendingBranchSelection = undefined;
    const event = (this.timelineIr?.events || []).find((candidate) => String(candidate.id) === eventId);
    if (event && this.projectUri && event.source?.file && Number.isInteger(event.source.line) && event.source.line > 0 &&
        event.sequencer_id && this.branchSelectionEventMatches(event, {
          branchId, sequencerId: event.sequencer_id, source: event.source,
        })) {
      this.pendingBranchSelection = {
        eventId, branchId, sequencerId: event.sequencer_id, source: { ...event.source },
        projectFile: this.projectUri.fsPath, sourceRevision: this.sourceRevision,
      };
      this.pendingTarget = undefined;
    }
    this.branchAssumptionOverrides.set(branchId, path);
    this.lastAnalyzerTimelineIr = undefined;
    this.output.appendLine(`q1timeline branch assumption override: ${branchId}=${path}`);
    await this.runAnalysis();
  }

  branchSelectionEventMatches(event, selection) {
    const meta = event?.meta || {};
    const source = event?.source;
    return ["q1_issue", "branch_region"].includes(event?.kind) && meta.diff_status !== "removed" &&
      (meta.branch_id || meta.branch_comparison_branch_id) === selection.branchId &&
      event.sequencer_id === selection.sequencerId && source?.file &&
      normalizedFsPath(this.resolveSourcePath(source.file)) === normalizedFsPath(this.resolveSourcePath(selection.source.file)) &&
      source.line === selection.source.line && (source.column || 1) === (selection.source.column || 1) &&
      (source.raw || "") === (selection.source.raw || "");
  }

  revealPendingBranchSelection() {
    const selection = this.pendingBranchSelection;
    this.pendingBranchSelection = undefined;
    if (!selection || selection.projectFile !== this.projectUri?.fsPath ||
        selection.sourceRevision !== this.sourceRevision || selection.analysisRequestId !== this.analysisRequestId) return;
    const candidates = (this.timelineIr?.events || []).filter((event) => this.branchSelectionEventMatches(event, selection));
    const event = candidates.find((candidate) => String(candidate.id) === selection.eventId) ||
      candidates.find((candidate) => candidate.kind === "q1_issue") || candidates[0];
    if (event) this.postWebviewMessage({ type: "highlightEventIds", highlightEventIds: [String(event.id)] });
  }

  async setLoopPreview(loopKey, visibleIterations) {
    this.pendingBranchSelection = undefined;
    if (visibleIterations <= 1) {
      this.loopPreviewOverrides.delete(loopKey);
      this.output.appendLine(`q1timeline loop preview override cleared: ${loopKey}`);
    } else {
      this.loopPreviewOverrides.set(loopKey, visibleIterations);
      this.output.appendLine(`q1timeline loop preview override: ${loopKey}=${visibleIterations}`);
    }
    this.lastAnalyzerTimelineIr = undefined;
    await this.runAnalysis();
  }

  alignmentPolicy() {
    const project = this.timelineIr && this.timelineIr.project ? this.timelineIr.project : {};
    const mode = project.alignment_mode || (this.singleFileMode ? "first_wait_sync" : "unknown");
    const anchorKinds = Array.isArray(project.alignment_anchor_kinds)
      ? project.alignment_anchor_kinds.filter((kind) => typeof kind === "string" && kind)
      : [];
    if (anchorKinds.length) {
      return `${mode} (${anchorKinds.join(", ")})`;
    }
    return mode;
  }

  async setViewMode(mode) {
    this.currentViewMode = mode;
    this.postWebviewMessage({ type: "setViewMode", mode });
    if (this.projectUri && this.outputDir) {
      await this.rerenderCurrentTimelineMode(mode);
    }
  }

  async rerenderCurrentTimelineMode(mode) {
    if (!this.projectUri || !this.outputDir) {
      return;
    }
    if (this.timelineIr?.playback && this.playbackClient) {
      if (!this.playbackInFlight) await this.publishPlaybackSnapshot(this.timelineIr, this.playbackClient);
      return;
    }
    if (this.analysisInFlight) {
      this.analysisQueued = true;
      this.analysisRequestId += 1;
      this.output.appendLine("Analyzer already running; queued refresh for view mode change.");
      this.setAnalysisStatus("stale", "A newer analysis request is queued.");
      return;
    }
    if (this.analysisStatus.status === "error") {
      await this.runAnalysis();
      return;
    }
    const timelineIrPath = path.join(this.outputDir, "timeline_ir.json");
    const htmlPath = path.join(this.outputDir, "timeline.html");
    try {
      await fsPromises.access(timelineIrPath);
    } catch (error) {
      if (error && error.code === "ENOENT") {
        await this.runAnalysis();
        return;
      }
      throw error;
    }
    const configuration = this.config();
    const pythonPath = configuration.get("pythonPath", "python");
    const pythonArgs = configuration.get("pythonArgs", []);
    const timeoutMs = configuration.get("analyzer.timeoutMs", 30000);
    const extraEnv = this.normaliseAnalyzerEnv(configuration.get("analyzer.env", {}));
    const cwd = this.projectRootCwd();
    const renderRequestId = ++this.analysisRequestId;
    this.setAnalysisStatus("rendering", `Rendering ${mode} timeline view.`);
    this.logAnalyzerEnv(extraEnv);
    return this.runRender(pythonPath, pythonArgs, timelineIrPath, htmlPath, timeoutMs, cwd, extraEnv, mode, renderRequestId)
      .then(async (renderResult) => {
        if (!renderResult) {
          return;
        }
        if (!this.isCurrentAnalysis(renderRequestId)) {
          this.output.appendLine(`Dropping stale renderer result ${renderRequestId}; current request is ${this.analysisRequestId}.`);
          return;
        }
        if (renderResult.stderr) {
          this.output.appendLine(`Renderer stderr:\n${renderResult.stderr}`);
        }
        this.output.appendLine(`Renderer completed in ${renderResult.elapsedMs} ms with exit code ${renderResult.exitCode}.`);
        this.setAnalysisStatus("idle", "Analysis complete.");
        await this.refreshWebview();
      })
      .catch((error) => {
        if (!this.isCurrentAnalysis(renderRequestId)) {
          this.output.appendLine(`Dropping stale renderer failure ${renderRequestId}; current request is ${this.analysisRequestId}.`);
          return;
        }
        this.logAnalyzerError(error);
        this.setAnalysisStatus("error", "Renderer failed.");
        if (this.isAnalyzerTimeoutError(error)) {
          this.showSubprocessTimeoutMessage(timeoutMs, error.stage);
        } else if (this.isAnalyzerMissingError(error)) {
          this.showAnalyzerMissingMessage(error);
        } else {
          vscode.window.showErrorMessage(`q1timeline renderer failed: ${error.message}`);
        }
      });
  }

  shouldAnalyzeOnSave() {
    return updatePolicy.shouldAnalyzeOnSave(this.updateMode());
  }

  shouldAnalyzeOnType() {
    return updatePolicy.shouldAnalyzeOnType(this.updateMode());
  }

  async findProjectFile(sourceUri) {
    const projectFile = this.config().get("projectFile", "q1timeline.yml").replace(/\\/g, "/");
    const activeProjectUri = this.findProjectFileUpward(
      sourceUri || (vscode.window.activeTextEditor ? vscode.window.activeTextEditor.document.uri : undefined),
      projectFile
    );
    if (activeProjectUri) {
      this.output.appendLine(`Discovered q1timeline project file from active editor: ${activeProjectUri.fsPath}`);
      return activeProjectUri;
    }
    const storedProjectUri = this.context.workspaceState.get("q1timeline.projectFile");
    if (storedProjectUri) {
      const uri = vscode.Uri.parse(storedProjectUri);
      const workspaceFolders = (vscode.workspace.workspaceFolders || []).map((folder) => folder.uri.fsPath);
      if (projectDiscovery.usableStoredProjectFile({
        storedProjectFile: uri.fsPath,
        workspaceFolders,
      })) {
        this.output.appendLine(`Using stored q1timeline project file: ${storedProjectUri}`);
        return uri;
      }
      this.output.appendLine(`Ignoring stale q1timeline project file: ${storedProjectUri}`);
      await this.context.workspaceState.update("q1timeline.projectFile", undefined);
    }
    this.output.appendLine(`Searching for q1timeline project file: ${projectFile}`);
    const matches = await this.findWorkspaceProjectFiles(projectFile);
    if (matches.length > 1) {
      return this.pickProjectFile(matches, `Select ${projectFile}`);
    }
    if (matches[0]) {
      this.output.appendLine(`Discovered q1timeline project file: ${matches[0].fsPath}`);
    } else {
      this.output.appendLine("No q1timeline project file found.");
    }
    return matches[0];
  }

  activeQ1asmUri(sourceUri) {
    const uri = sourceUri || (vscode.window.activeTextEditor ? vscode.window.activeTextEditor.document.uri : undefined);
    if (!uri || !isQ1asmPath(uri.fsPath)) {
      return undefined;
    }
    return uri;
  }

  activeQ1asmChanged(sourceUri) {
    return sourceUri &&
      isQ1asmPath(sourceUri.fsPath) &&
      (!this.singleFileUri || normalizedFsPath(this.singleFileUri.fsPath) !== normalizedFsPath(sourceUri.fsPath));
  }

  shouldUseSingleFileForActiveQ1asm(sourceUri) {
    return sourceUri &&
      isQ1asmPath(sourceUri.fsPath) &&
      this.projectUri &&
      !this.singleFileMode &&
      !this.isProjectRelated(sourceUri);
  }

  async createSingleFileProject(sourceUri) {
    return this.createSelectedFolderProject(sourceUri);
  }

  async createSelectedFolderProject(sourceUri, selectedUris, options = {}) {
    sourceUri = this.activeQ1asmUri(sourceUri);
    if (!sourceUri) {
      return undefined;
    }
    const q1asmUris = selectedUris && selectedUris.length
      ? this.sortedUniqueQ1asmUris(selectedUris)
      : await this.q1asmFilesInFolder(sourceUri);
    if (!q1asmUris.length) {
      return undefined;
    }
    this.singleFileUri = sourceUri;
    this.singleFileMode = true;
    this.outputDir = path.join(path.dirname(sourceUri.fsPath), ".q1timeline");
    await fsPromises.mkdir(this.outputDir, { recursive: true });
    const projectPath = path.join(this.outputDir, "auto-generated.q1timeline.yml");
    const paramsFilename = await this.paramsFilenameForSelectedFolderProject(q1asmUris, options.paramsFile);
    const projectYaml = this.selectedFolderProjectYaml(q1asmUris, paramsFilename);
    await fsPromises.writeFile(projectPath, projectYaml, "utf-8");
    const includedFiles = q1asmUris.map((uri) => path.basename(uri.fsPath)).join(", ");
    this.output.appendLine(
      `Auto-generated q1timeline fallback includes ${q1asmUris.length} Q1ASM file(s): ${includedFiles}`
    );
    this.output.appendLine(`Auto-generated q1timeline fallback params: ${paramsFilename || "none"}`);
    if (options.showMissingProjectWarning !== false) {
      vscode.window.showWarningMessage(
        paramsFilename
          ? "No q1timeline.yml found; using a temporary Q1ASM folder selection with inferred placeholder params."
          : "No q1timeline.yml found; using a temporary Q1ASM folder selection. Project-level params are unavailable."
      );
    }
    this.output.appendLine(`Created auto-generated q1timeline fallback project: ${projectPath}`);
    return vscode.Uri.file(projectPath);
  }

  async paramsFilenameForSelectedFolderProject(q1asmUris, paramsFile) {
    if (paramsFile) {
      return path.relative(this.outputDir, paramsFile).replace(/\\/g, "/");
    }
    const inferredParams = await this.inferredPlaceholderParams(q1asmUris);
    if (!Object.keys(inferredParams).length) {
      return undefined;
    }
    const paramsFilename = "auto-generated.params.json";
    const paramsPath = path.join(this.outputDir, paramsFilename);
    await fsPromises.writeFile(paramsPath, `${JSON.stringify(inferredParams, null, 2)}\n`, "utf-8");
    this.output.appendLine(`Created inferred q1timeline params fallback: ${paramsPath}`);
    return paramsFilename;
  }

  async inferredPlaceholderParams(q1asmUris) {
    const names = new Set();
    for (const uri of q1asmUris) {
      const text = await fsPromises.readFile(uri.fsPath, "utf-8");
      for (const name of q1asmTemplatePlaceholders(text)) {
        names.add(name);
      }
    }
    return Object.fromEntries(
      Array.from(names)
        .sort((left, right) => left.localeCompare(right))
        .map((name) => [name, inferredPlaceholderParamValue(name)])
    );
  }

  async q1asmFilesInFolder(sourceUri) {
    const folder = path.dirname(sourceUri.fsPath);
    const entries = await fsPromises.readdir(folder, { withFileTypes: true });
    const uris = entries
      .filter((entry) => entry.isFile && entry.isFile())
      .map((entry) => vscode.Uri.file(path.join(folder, entry.name)))
      .filter((uri) => isQ1asmPath(uri.fsPath));
    return this.sortedUniqueQ1asmUris(uris);
  }

  sortedUniqueQ1asmUris(uris) {
    const byPath = new Map();
    for (const uri of uris || []) {
      if (!uri || !isQ1asmPath(uri.fsPath)) {
        continue;
      }
      byPath.set(normalizedFsPath(uri.fsPath), uri);
    }
    return Array.from(byPath.values()).sort((left, right) => {
      const leftName = path.basename(left.fsPath).toLowerCase();
      const rightName = path.basename(right.fsPath).toLowerCase();
      if (leftName !== rightName) {
        return leftName.localeCompare(rightName);
      }
      return normalizedFsPath(left.fsPath).localeCompare(normalizedFsPath(right.fsPath));
    });
  }

  selectedFolderProjectYaml(q1asmUris, paramsFilename) {
    const seenIds = new Map();
    const lines = ["sequencers:"];
    for (const uri of q1asmUris) {
      const name = path.basename(uri.fsPath, path.extname(uri.fsPath));
      const id = this.uniqueSequencerId(name, seenIds);
      const filePath = path.relative(this.outputDir, uri.fsPath).replace(/\\/g, "/");
      lines.push(
        `  - id: ${JSON.stringify(id)}`,
        `    name: ${JSON.stringify(name)}`,
        `    file: ${JSON.stringify(filePath)}`,
      );
    }
    if (paramsFilename) {
      lines.push("params:", `  file: ${JSON.stringify(paramsFilename)}`);
    }
    lines.push("alignment:", "  mode: first_wait_sync", "");
    return lines.join("\n");
  }

  uniqueSequencerId(name, seenIds) {
    const base = this.toSequencerId(name);
    const count = (seenIds.get(base) || 0) + 1;
    seenIds.set(base, count);
    return count === 1 ? base : `${base}_${count}`;
  }

  toSequencerId(name) {
    const id = name.replace(/[^A-Za-z0-9_]+/g, "_");
    return id || "single_file";
  }

  findProjectFileUpward(sourceUri, projectFile) {
    if (!sourceUri || !isQ1asmPath(sourceUri.fsPath)) {
      return undefined;
    }
    const workspaceFolder = vscode.workspace.getWorkspaceFolder(sourceUri);
    const root = workspaceFolder ? workspaceFolder.uri.fsPath : path.parse(sourceUri.fsPath).root;
    const discovered = projectDiscovery.findProjectFileUpward(sourceUri.fsPath, projectFile, root);
    return discovered ? vscode.Uri.file(discovered) : undefined;
  }

  findProjectFileInQ1asmFolder(sourceUri, projectFile) {
    if (!sourceUri || !isQ1asmPath(sourceUri.fsPath)) {
      return undefined;
    }
    const root = path.dirname(sourceUri.fsPath);
    const discovered = projectDiscovery.findProjectFileUpward(sourceUri.fsPath, projectFile, root);
    return discovered ? vscode.Uri.file(discovered) : undefined;
  }

  findProjectFileCandidateUpward(sourceUri) {
    return this.findProjectFileCandidatesUpward(sourceUri)[0];
  }

  findProjectFileCandidatesUpward(sourceUri) {
    if (!sourceUri || !isQ1asmPath(sourceUri.fsPath)) {
      return [];
    }
    const workspaceFolder = vscode.workspace.getWorkspaceFolder(sourceUri);
    const root = workspaceFolder ? workspaceFolder.uri.fsPath : path.parse(sourceUri.fsPath).root;
    const discovered = projectDiscovery.findProjectFileCandidatesUpward(
      sourceUri.fsPath,
      this.projectFileCandidatePatterns(),
      root,
      ["auto-generated.q1timeline.yml", "auto-generated.q1timeline.yaml"],
    );
    return discovered
      .filter((item) => path.basename(item).toLowerCase() !== "auto-generated.q1timeline.yml")
      .map((item) => vscode.Uri.file(item));
  }

  projectFileCandidatePatterns() {
    const configured = this.config().get("projectFile", "q1timeline.yml").replace(/\\/g, "/");
    return [
      configured,
      "q1timeline.yml",
      "q1timeline.yaml",
      "*.q1timeline.yml",
      "*.q1timeline.yaml",
      ".q1timeline/q1timeline.yml",
      ".q1timeline/q1timeline.yaml",
      ".q1timeline/*.q1timeline.yml",
      ".q1timeline/*.q1timeline.yaml",
    ];
  }

  async paramsFileForAutoGeneratedProject(sourceUri) {
    const candidates = this.findParamsFileCandidatesUpward(sourceUri);
    if (!candidates.length) {
      return undefined;
    }
    if (candidates.length === 1) {
      return candidates[0].fsPath;
    }
    const selected = await this.pickParamsFile(candidates);
    return selected?.fsPath;
  }

  findParamsFileCandidatesUpward(sourceUri) {
    if (!sourceUri || !isQ1asmPath(sourceUri.fsPath)) {
      return [];
    }
    const workspaceFolder = vscode.workspace.getWorkspaceFolder(sourceUri);
    const root = workspaceFolder ? workspaceFolder.uri.fsPath : path.parse(sourceUri.fsPath).root;
    const discovered = projectDiscovery.findProjectFileCandidatesUpward(
      sourceUri.fsPath,
      ["params.json", "*.params.json", ".q1timeline/params.json", ".q1timeline/*.params.json"],
      root,
      ["auto-generated.params.json"],
    ).filter((item) => path.basename(item).toLowerCase() !== "auto-generated.params.json");
    return discovered.map((item) => vscode.Uri.file(item));
  }

  async pickParamsFile(matches) {
    const items = matches.map((uri) => ({ label: vscode.workspace.asRelativePath(uri), uri }));
    const selected = await vscode.window.showQuickPick(items, { placeHolder: "Select params file" });
    return selected?.uri;
  }

  findWorkspaceProjectFiles(projectFile) {
    if (projectFile === "q1timeline.yml") {
      return vscode.workspace.findFiles("**/q1timeline.yml", "**/{.git,node_modules,.q1timeline}/**");
    }
    return vscode.workspace.findFiles(`**/${projectFile}`, "**/{.git,node_modules,.q1timeline}/**");
  }

  async pickProjectFile(matches, placeHolder) {
    const items = matches.map((uri) => ({ label: vscode.workspace.asRelativePath(uri), uri }));
    const selected = await vscode.window.showQuickPick(items, { placeHolder });
    if (!selected) {
      return undefined;
    }
    await this.context.workspaceState.update("q1timeline.projectFile", selected.uri.toString());
    this.output.appendLine(`Selected q1timeline project file: ${selected.uri.fsPath}`);
    return selected.uri;
  }

  startWatchers() {
    if (this.watcher) {
      this.watcher.dispose();
    }
    this.watcher = vscode.workspace.createFileSystemWatcher("**/*.{q1asm,json,yml,yaml}");
    this.context.subscriptions.push(this.watcher);
    this.watcher.onDidChange((uri) => this.onWatchedFile(uri));
    this.watcher.onDidCreate((uri) => this.onWatchedFile(uri));
    this.watcher.onDidDelete((uri) => this.onWatchedFile(uri));
  }

  async onWatchedFile(uri) {
    if (this.isProjectRelated(uri)) {
      this.invalidateSourceAnalysis();
    }
    if (this.projectUri && normalizedFsPath(uri.fsPath) === normalizedFsPath(this.projectUri.fsPath)) {
      await this.refreshProjectRelatedPaths();
    }
    if (!this.isProjectRelated(uri) || !updatePolicy.shouldAnalyzeWatchedFile(this.updateMode())) {
      return;
    }
    if (updatePolicy.shouldDebounceWatchedFile(this.updateMode())) {
      this.scheduleAnalysis();
    } else {
      this.runAnalysis();
    }
  }

  isProjectRelated(uri) {
    if (!this.projectUri) {
      return false;
    }
    return isProjectRelatedPath({
      filePath: uri.fsPath,
      projectPath: this.projectUri.fsPath,
      singleFilePath: this.singleFileUri && this.singleFileUri.fsPath,
      projectRelatedPaths: this.projectRelatedPaths,
    });
  }

  async refreshProjectRelatedPaths() {
    const related = new Set();
    if (this.projectUri) {
      try {
        const projectText = await fsPromises.readFile(this.projectUri.fsPath, "utf-8").catch((error) => {
          if (error && error.code === "ENOENT") {
            return "";
          }
          throw error;
        });
        for (const relatedPath of collectProjectRelatedPaths(this.projectUri.fsPath, projectText)) {
          related.add(relatedPath);
        }
      } catch (error) {
        related.add(path.resolve(this.projectUri.fsPath));
        this.output.appendLine(`Unable to refresh project watch paths: ${error.message}`);
      }
    }
    if (this.singleFileUri) {
      related.add(path.resolve(this.singleFileUri.fsPath));
    }
    this.projectRelatedPaths = related;
  }

  scheduleAnalysis() {
    if (this.changeTimer) {
      clearTimeout(this.changeTimer);
    }
    const debounceMs = effectiveDebounceMs(this.config().get("debounceMs", 400), this.analyzerResult);
    this.changeTimer = setTimeout(() => this.runAnalysis(), debounceMs);
  }

  async runAnalysis({ onFailure } = {}) {
    if (!this.projectUri || !this.outputDir) {
      return Promise.resolve();
    }
    this.resetPlayback();
    if (this.analysisInFlight) {
      this.analysisQueued = true;
      this.analysisRequestId += 1;
      this.output.appendLine("Analyzer already running; queued refresh.");
      this.setAnalysisStatus("stale", "A newer analysis request is queued.");
      return Promise.resolve();
    }
    this.analysisInFlight = true;
    this.cancelSourceRangeZoom();
    const sourceRevision = this.sourceRevision;
    const analysisProjectFile = this.projectUri.fsPath;
    const analysisRequestId = ++this.analysisRequestId;
    if (this.pendingBranchSelection) {
      this.pendingBranchSelection.analysisRequestId = analysisRequestId;
      this.pendingBranchSelection.awaitingWebviewReady = false;
    }
    this.setAnalysisStatus("analyzing", "Analyzing q1timeline project.");
    const configuration = this.config();
    const pythonPath = configuration.get("pythonPath", "python");
    const pythonArgs = configuration.get("pythonArgs", []);
    const timeoutMs = configuration.get("analyzer.timeoutMs", 30000);
    const extraArgs = configuration.get("analyzer.extraArgs", []);
    const extraEnv = this.normaliseAnalyzerEnv(configuration.get("analyzer.env", {}));
    const viewMode = this.viewMode();
    const timelineIrPath = path.join(this.outputDir, "timeline_ir.json");
    const diagnosticsPath = path.join(this.outputDir, "diagnostics.json");
    const htmlPath = path.join(this.outputDir, "timeline.html");
    const args = prependPythonArgs(pythonArgs, [
      "-m",
      "q1lens",
      "q1timeline",
      "analyze",
      "--project",
      this.projectUri.fsPath,
      "--out",
      timelineIrPath,
      "--diagnostics",
      diagnosticsPath,
      "--format",
      "vscode-json",
      "--include-diagnostics",
      "--summary-only",
      "--mode",
      viewMode,
      "--no-render",
      ...this.executionArgs(),
      ...hardwareReadinessAnalyzerArgs(configuration.get("hardwareReadiness", false)),
      ...this.analyzerOverrideArgs(extraArgs),
    ]);
    const parameterSummary = this.parameterOverrideSummary();
    if (parameterSummary) {
      this.output.appendLine(`Active analysis parameter overrides: ${parameterSummary}`);
    }
    this.output.appendLine(`Analyzer command: ${[pythonPath, ...args].join(" ")}`);
    this.logAnalyzerEnv(extraEnv);
    const cwd = this.projectRootCwd();
    this.output.appendLine(`Analyzer working directory: ${cwd}`);
    return Promise.resolve()
      .then(async () => {
        await fsPromises.mkdir(this.outputDir, { recursive: true });
        await this.clearAnalyzerOutputFiles(timelineIrPath, diagnosticsPath, htmlPath);
        return this.execFile(pythonPath, args, timeoutMs, cwd, extraEnv, "analyzer");
      })
      .catch(async (error) => {
        // Runtime failures can still produce a valid partial simulation trace.
        // Outputs were cleared above, so this cannot recover a stale trace.
        if (!this.isCurrentAnalysis(analysisRequestId) || this.isAnalyzerTimeoutError(error)) throw error;
        let partial;
        try { partial = JSON.parse(await fsPromises.readFile(timelineIrPath, "utf-8")); } catch { throw error; }
        if (!Array.isArray(partial.events) || !partial.events.length || !Object.values(partial.execution || {}).some((entry) => entry.mode === "simulation" && entry.status === "error")) throw error;
        try { parseAnalyzerResult(error.stdout); } catch { throw error; }
        this.output.appendLine("Simulation stopped with an error; rendering the partial execution trace and its diagnostics.");
        return { stdout: error.stdout, stderr: error.stderr, elapsedMs: error.elapsedMs, exitCode: error.exitCode };
      })
      .then(async (result) => {
        if (!this.isCurrentAnalysis(analysisRequestId)) {
          this.output.appendLine(`Dropping stale analyzer result ${analysisRequestId}; current request is ${this.analysisRequestId}.`);
          return;
        }
        if (result.stderr) {
          this.output.appendLine(`Analyzer stderr:\n${result.stderr}`);
        }
        const analyzerResult = parseAnalyzerResult(result.stdout);
        this.analyzerResult = analyzerResult;
        this.showIncompatibleCoreVersionWarning(analyzerResult);
        this.showLargeEventCountWarning(
          analyzerResult,
          configuration.get("render.maxEventsBeforeSimplify", 10000)
        );
        this.output.appendLine(`Analyzer completed in ${result.elapsedMs} ms with exit code ${result.exitCode}.`);
        this.output.appendLine(
          `Analyzer summary: status=${analyzerResult.status}, events=${analyzerResult.stats ? analyzerResult.stats.event_count : "unknown"}, sequencers=${analyzerResult.stats ? analyzerResult.stats.sequencer_count : "unknown"}.`
        );
        await this.prepareTimelineIrForRender(timelineIrPath);
        return this.runRender(pythonPath, pythonArgs, timelineIrPath, htmlPath, timeoutMs, cwd, extraEnv, viewMode, analysisRequestId);
      })
      .then(async (renderResult) => {
        if (!renderResult) {
          return;
        }
        if (!this.isCurrentAnalysis(analysisRequestId)) {
          this.output.appendLine(`Dropping stale renderer result ${analysisRequestId}; current request is ${this.analysisRequestId}.`);
          return;
        }
        if (renderResult.stderr) {
          this.output.appendLine(`Renderer stderr:\n${renderResult.stderr}`);
        }
        this.output.appendLine(`Renderer completed in ${renderResult.elapsedMs} ms with exit code ${renderResult.exitCode}.`);
        this.logAnalyzerOutputFiles();
        this.setAnalysisStatus("idle", "Analysis complete.");
        this.analysisFailureCount = 0;
        if (await this.refreshWebview()) {
          this.analyzedSourceRevision = sourceRevision;
        }
      })
      .catch(async (error) => {
        if (!this.isCurrentAnalysis(analysisRequestId)) {
          this.output.appendLine(`Dropping stale analyzer failure ${analysisRequestId}; current request is ${this.analysisRequestId}.`);
          return;
        }
        this.logAnalyzerError(error);
        const failureDiagnostics = diagnosticsFromAnalyzerFailure(error);
        const structuredError = this.parseAnalyzerError(error);
        if (structuredError) {
          this.output.appendLine(`Analyzer structured error: ${structuredError.kind || "error"}: ${structuredError.message || "No message"}`);
        }
        this.setAnalysisStatus("error", "Analyzer failed.");
        if (!onFailure || onFailure(structuredError?.message || failureDiagnostics[0]?.message || error.message) === false) {
          this.clearPreviewAfterAnalyzerFailure("q1timeline analyzer failed.");
        }
        const hasDiagnostics = failureDiagnostics.length > 0 || await this.hasAnalyzerDiagnostics();
        if (this.isAnalyzerTimeoutError(error)) {
          this.showSubprocessTimeoutMessage(timeoutMs, error.stage);
        } else if (this.isAnalyzerMissingError(error)) {
          this.showAnalyzerMissingMessage(error);
        } else if (this.isUnsupportedAnalyzerSchemaError(error)) {
          this.showUnsupportedAnalyzerSchemaMessage(error);
        } else if (hasDiagnostics) {
          this.output.appendLine("Analyzer failed with diagnostics; Problems panel updated without notification.");
        } else {
          this.recordAnalyzerFailure(error);
        }
        const diagnostics = failureDiagnostics.length ? this.applyDiagnostics(failureDiagnostics) : await this.loadDiagnostics();
        this.setAnalyzerDiagnostics(diagnostics);
        this.setDiagnosticSummary(summarizeDiagnostics(diagnostics));
      })
      .finally(() => {
        this.analysisInFlight = false;
        if (this.analysisQueued) {
          this.analysisQueued = false;
          this.runAnalysis({ onFailure: this.projectUri?.fsPath === analysisProjectFile ? onFailure : undefined });
        }
      });
  }

  runRender(pythonPath, pythonArgs, timelineIrPath, htmlPath, timeoutMs, cwd, extraEnv, viewMode, analysisRequestId) {
    if (!this.isCurrentAnalysis(analysisRequestId)) {
      this.output.appendLine(`Skipping render for stale analysis ${analysisRequestId}; current request is ${this.analysisRequestId}.`);
      return Promise.resolve(undefined);
    }
    const args = prependPythonArgs(pythonArgs, [
      "-m",
      "q1lens",
      "q1timeline",
      "render",
      "--ir",
      timelineIrPath,
      "--out",
      htmlPath,
      "--mode",
      viewMode,
      "--no-open",
    ]);
    this.output.appendLine(`Renderer command: ${[pythonPath, ...args].join(" ")}`);
    return this.execFile(pythonPath, args, timeoutMs, cwd, extraEnv, "renderer");
  }

  async clearAnalyzerOutputFiles(...paths) {
    await Promise.all(paths.map((filePath) => fsPromises.rm(filePath, { force: true })));
  }

  async prepareTimelineIrForRender(timelineIrPath) {
    let nextTimelineIr;
    try {
      nextTimelineIr = JSON.parse(await fsPromises.readFile(timelineIrPath, "utf-8"));
    } catch (error) {
      this.output.appendLine(`Unable to prepare TimelineIR diff highlights: ${error.message}`);
      return;
    }
    const savedComparisonBaseline = await this.loadComparisonBaseline();
    const diffBaseline = savedComparisonBaseline || this.lastAnalyzerTimelineIr;
    const annotatedTimelineIr = annotateTimelineDiff(diffBaseline, nextTimelineIr);
    this.comparison = { mode: savedComparisonBaseline ? "saved" : isTimelineIr(diffBaseline) ? "previous" : "none" };
    this.lastAnalyzerTimelineIr = cloneJson(nextTimelineIr);
    await fsPromises.writeFile(timelineIrPath, JSON.stringify(annotatedTimelineIr, null, 2));
  }

  isCurrentAnalysis(analysisRequestId) {
    return analysisRequestId === this.analysisRequestId;
  }

  isAnalyzerMissingError(error) {
    const text = `${error && error.message ? error.message : ""}\n${error && error.stderr ? error.stderr : ""}`;
    return Boolean(
      error &&
      (
        error.code === "ENOENT" ||
        text.includes("No module named qbstimeline") ||
        text.includes("No module named 'qbstimeline'") ||
        text.includes("No module named q1lens") ||
        text.includes("No module named 'q1lens'") ||
        text.includes("invalid choice: 'q1timeline'") ||
        text.includes("No module named q1timeline") ||
        text.includes("No module named 'q1timeline'")
      )
    );
  }

  isAnalyzerTimeoutError(error) {
    const message = error && error.message ? error.message : "";
    return Boolean(
      error &&
      (
        error.killed ||
        error.signal === "SIGTERM" ||
        message.includes("timed out") ||
        message.includes("timeout")
      )
    );
  }

  showSubprocessTimeoutMessage(timeoutMs, stage) {
    const stageLabel = stage || "subprocess";
    vscode.window.showErrorMessage(
      `q1timeline ${stageLabel} timed out after ${timeoutMs} ms. Increase q1timeline.analyzer.timeoutMs or check the analyzer log.`
    );
  }

  recordAnalyzerFailure(error) {
    this.analysisFailureCount += 1;
    this.output.appendLine(`Analyzer failure count: ${this.analysisFailureCount}. Last error: ${error.message}`);
    if (this.analysisFailureCount >= 3 && this.analysisFailureCount % 3 === 0) {
      vscode.window.showErrorMessage(
        "q1timeline analyzer has failed repeatedly. See the Q1ASM Timeline output for details."
      );
    }
  }

  showAnalyzerMissingMessage(error) {
    const pythonPath = this.config().get("pythonPath", "python");
    return vscode.window.showErrorMessage(
      `Q1Lens is unavailable in the selected Python environment (${pythonPath}). ${error.message}`,
      "Check Python Environment"
    ).then((action) => {
      if (action === "Check Python Environment") return this.checkAnalyzerInstallation();
      return undefined;
    });
  }

  isUnsupportedAnalyzerSchemaError(error) {
    return Boolean(
      error &&
      error.message &&
      error.message.includes("Unsupported AnalyzerResult schema_version")
    );
  }

  showUnsupportedAnalyzerSchemaMessage(error) {
    vscode.window.showWarningMessage(
      `Unsupported analyzer schema version. Update the integrated q1timeline analyzer or the extension. ${error.message}`
    );
  }

  showLargeEventCountWarning(analyzerResult, threshold) {
    if (!shouldWarnForLargeEventCount(analyzerResult, threshold)) {
      return;
    }
    this.output.appendLine(largeEventCountWarningMessage(analyzerResult, threshold));
  }

  showIncompatibleCoreVersionWarning(analyzerResult) {
    const coreVersion = analyzerResult ? analyzerResult.core_version : undefined;
    if (isSupportedCoreVersion(coreVersion)) {
      return;
    }
    vscode.window.showWarningMessage(
      `Unsupported q1timeline analyzer version ${coreVersion || "unknown"}. Supported range: ${SUPPORTED_CORE_VERSION_RANGE}.`
    );
  }

  workspaceCwd() {
    if (this.projectUri) {
      return path.dirname(this.projectUri.fsPath);
    }
    const folders = vscode.workspace.workspaceFolders || [];
    if (folders[0]) {
      return folders[0].uri.fsPath;
    }
    return process.cwd();
  }

  projectRootCwd() {
    if (this.singleFileUri) {
      return path.dirname(this.singleFileUri.fsPath);
    }
    const workspaceFolder = vscode.workspace.getWorkspaceFolder(this.projectUri);
    if (workspaceFolder) {
      return workspaceFolder.uri.fsPath;
    }
    return path.dirname(this.projectUri.fsPath);
  }

  normaliseAnalyzerEnv(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(value).filter((entry) => typeof entry[1] === "string")
    );
  }

  logAnalyzerEnv(extraEnv) {
    const keys = Object.keys(extraEnv);
    if (keys.length) {
      this.output.appendLine(`Analyzer environment overrides: ${keys.join(", ")}`);
    }
  }

  logAnalyzerOutputFiles() {
    this.output.appendLine(`Analyzer output directory: ${this.outputDir}`);
    for (const filename of ["timeline_ir.json", "diagnostics.json", "timeline.html"]) {
      this.output.appendLine(`Analyzer output file: ${path.join(this.outputDir, filename)}`);
    }
  }

  logAnalyzerError(error) {
    if (error.stderr) {
      this.output.appendLine(`Analyzer stderr:\n${error.stderr}`);
    }
    this.output.appendLine(`Analyzer failed in ${error.elapsedMs || "unknown"} ms with exit code ${error.exitCode || "unknown"}.`);
    this.output.appendLine(error.message);
  }

  parseAnalyzerError(error) {
    return errorFromAnalyzerFailure(error);
  }

  execFile(command, args, timeoutMs, cwd, extraEnv = {}, stage) {
    return new Promise((resolve, reject) => {
      const startedAt = Date.now();
      childProcess.execFile(command, args, { cwd, timeout: timeoutMs, maxBuffer: 50 * 1024 * 1024, env: { ...process.env, ...extraEnv } }, (error, stdout, stderr) => {
        const elapsedMs = Date.now() - startedAt;
        if (error) {
          error.stdout = stdout;
          error.stderr = stderr;
          error.elapsedMs = elapsedMs;
          error.exitCode = error.code || error.signal || "unknown";
          error.stage = stage || "subprocess";
          error.message = `${error.message}\n${stderr || stdout}`;
          reject(error);
          return;
        }
        resolve({ stdout, stderr, exitCode: 0, elapsedMs });
      });
    });
  }

  async refreshWebview(snapshot) {
    if (snapshot && !snapshot.isCurrent()) return;
    this.cancelSourceRangeZoom();
    this.webviewReady = false;
    try {
      this.timelineIr = snapshot ? snapshot.ir : await this.readJson("timeline_ir.json");
    } catch (error) {
      this.applyFallbackDiagnostic(`Invalid analyzer TimelineIR JSON: ${error.message}`);
      return;
    }
    const diagnostics = snapshot ? this.applyDiagnostics(snapshot.ir.diagnostics || []) : await this.loadDiagnostics();
    this.setAnalyzerDiagnostics(diagnostics);
    this.setDiagnosticSummary(summarizeDiagnostics(diagnostics));
    if (!this.panel) {
      return;
    }
    const htmlPath = path.join(this.outputDir, "timeline.html");
    let html;
    try {
      html = snapshot ? snapshot.html : await fsPromises.readFile(htmlPath, "utf-8");
      this.lastPreviewHtml = html;
    } catch (error) {
      if (!error || error.code !== "ENOENT") {
        this.applyFallbackDiagnostic(`Invalid analyzer HTML output: ${error.message}`);
        return;
      }
      html = this.placeholderTimelineHtml("No timeline.html generated.");
    }
    const nonce = webviewNonce();
    html = stripExecutableInlineScripts(html);
    html = injectWebviewState(html, { ...this.webviewInitialState(), ...(snapshot ? { executionState: snapshot.ir.playback } : {}) });
    html = injectWebviewAssetTags(html, this.panel.webview, this.context, nonce);
    html = injectContentSecurityPolicy(html, contentSecurityPolicy(this.panel.webview, nonce));
    if (this.pendingTarget?.zoomToRange) {
      this.pendingTarget.awaitingWebviewReady = true;
    }
    if (this.pendingBranchSelection?.analysisRequestId === this.analysisRequestId) {
      this.pendingBranchSelection.awaitingWebviewReady = true;
    }
    this.panel.webview.html = html;
    if (!snapshot && !this.pendingTarget?.zoomToRange) {
      await this.revealPendingTarget();
    }
    return Boolean(this.timelineIr);
  }

  clearPreviewAfterAnalyzerFailure(message) {
    this.cancelSourceRangeZoom();
    this.pendingBranchSelection = undefined;
    this.webviewReady = false;
    this.timelineIr = undefined;
    this.lastAnalyzerTimelineIr = undefined;
    this.lastPreviewHtml = undefined;
    if (this.panel && this.panel.webview) {
      this.panel.webview.html = this.placeholderTimelineHtml(message || "q1timeline analyzer failed.");
    }
  }

  placeholderTimelineHtml(message) {
    const text = String(message || "No timeline.html generated.")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    return `<!doctype html><html><body><p>${text}</p></body></html>`;
  }

  async loadDiagnostics() {
    let diagnostics;
    try {
      diagnostics = await this.readJson("diagnostics.json") || [];
    } catch (error) {
      const message = `Invalid analyzer diagnostics JSON: ${error.message}`;
      this.applyFallbackDiagnostic(message);
      return this.applyDiagnostics([fallbackAnalyzerDiagnostic(this.projectUri.fsPath, message)]);
    }
    diagnostics = normalizeAnalyzerDiagnostics(diagnostics, this.projectUri.fsPath);
    return this.applyDiagnostics(diagnostics);
  }

  applyDiagnostics(diagnostics) {
    return this.diagnosticsManager.apply(diagnostics);
  }

  applyFallbackDiagnostic(message) {
    if (!this.projectUri) {
      return;
    }
    this.output.appendLine(message);
    this.diagnosticsManager.applyFallback(this.projectUri, message);
  }

  async hasAnalyzerDiagnostics() {
    try {
      const diagnostics = await this.readJson("diagnostics.json");
      return Array.isArray(diagnostics) && diagnostics.length > 0;
    } catch (error) {
      this.output.appendLine(`Unable to inspect analyzer diagnostics: ${error.message}`);
      return false;
    }
  }

  async handleWebviewMessage(message) {
    const parsedMessage = parseWebviewMessage(message);
    if (!parsedMessage.valid) {
      this.logMalformedWebviewMessage(message);
      return;
    }
    if (parsedMessage.type === "eventClick") {
      this.openSourceForEvent(parsedMessage.eventId);
      return;
    }
    if (parsedMessage.type === "requestRefresh") {
      this.refreshPreview();
      return;
    }
    if (parsedMessage.type === "executionControl") {
      if (parsedMessage.action === "pause") this.pauseExecution();
      else await this.runPlayback();
      return;
    }
    if (parsedMessage.type === "setExecutionBranch") {
      await this.setExecutionBranch(parsedMessage.eventId, parsedMessage.branchId, parsedMessage.path);
      return;
    }
    if (parsedMessage.type === "setExecutionFeedback") {
      await this.setExecutionFeedback(parsedMessage.eventId, parsedMessage.feedbackId, parsedMessage.value, parsedMessage.delayNs);
      return;
    }
    if (parsedMessage.type === "prepareExecutionReplay") {
      await this.prepareExecutionReplay(parsedMessage.sequencerId, parsedMessage.scope);
      return;
    }
    if (parsedMessage.type === "webviewReady") {
      this.webviewReady = true;
      this.output.appendLine("q1timeline webview script ready.");
      if (this.pendingTarget?.zoomToRange && this.pendingTarget.awaitingWebviewReady) {
        await this.revealPendingTarget();
      } else if (this.pendingBranchSelection?.awaitingWebviewReady) {
        this.revealPendingBranchSelection();
      } else if (!this.timelineIr?.playback && !this.pendingBranchSelection && vscode.window.activeTextEditor?.selection?.isEmpty === false) {
        this.followSourceEditor(vscode.window.activeTextEditor);
      }
      return;
    }
    if (parsedMessage.type === "diagnosticClick") {
      this.openSourceForDiagnostic(parsedMessage.diagnosticIndex);
      return;
    }
    if (parsedMessage.type === "sourceClick") {
      await this.openSourceLocation({
        file: parsedMessage.file,
        line: parsedMessage.line,
        column: parsedMessage.column,
      }, { preserveHardwareCursor: parsedMessage.preserveHardwareCursor });
      return;
    }
    if (parsedMessage.type === "setViewMode") {
      await this.setViewMode(parsedMessage.mode);
      return;
    }
    if (parsedMessage.type === "setBranchAssumption") {
      await this.setBranchAssumption(parsedMessage.branchId, parsedMessage.path, parsedMessage.eventId);
      return;
    }
    if (parsedMessage.type === "setLoopPreview") {
      await this.setLoopPreview(parsedMessage.loopKey, parsedMessage.visibleIterations);
      return;
    }
    if (parsedMessage.type === "setAnalysisParameter" || parsedMessage.type === "resetAnalysisParameter") {
      await this.updateInlineAnalysisParameter(parsedMessage);
      return;
    }
  }

  isValidEventClickMessage(message) {
    const parsedMessage = parseWebviewMessage(message);
    return parsedMessage.valid && parsedMessage.type === "eventClick";
  }

  isValidRefreshMessage(message) {
    const parsedMessage = parseWebviewMessage(message);
    return parsedMessage.valid && parsedMessage.type === "requestRefresh";
  }

  isValidDiagnosticClickMessage(message) {
    const parsedMessage = parseWebviewMessage(message);
    return parsedMessage.valid && parsedMessage.type === "diagnosticClick";
  }

  isValidSetViewModeMessage(message) {
    const parsedMessage = parseWebviewMessage(message);
    return parsedMessage.valid && parsedMessage.type === "setViewMode";
  }

  logMalformedWebviewMessage(message) {
    let payload;
    try {
      payload = JSON.stringify(message);
    } catch (error) {
      payload = "<unserializable>";
    }
    this.output.appendLine(`Malformed webview message: ${payload}`);
  }

  postWebviewMessage(message) {
    if (!this.panel) {
      return;
    }
    try {
      JSON.stringify(message);
    } catch (error) {
      this.output.appendLine("Malformed webview message: <unserializable outbound payload>");
      return;
    }
    this.panel.webview.postMessage(message);
  }

  async openSourceForEvent(eventId) {
    const source = lookupSourceForEvent(this.timelineIr, eventId);
    if (!source) {
      this.output.appendLine(`q1timeline eventClick source not found: ${eventId}`);
      return;
    }
    this.output.appendLine(`q1timeline eventClick: ${eventId} -> ${source.file}:${source.line || 1}`);
    return this.openSourceLocation(source, { preserveHardwareCursor: true });
  }

  async openSourceForDiagnostic(diagnosticIndex) {
    const item = this.analyzerDiagnostics[diagnosticIndex];
    if (!item || !item.source) {
      return;
    }
    return this.openSourceLocation(item.source);
  }

  async openSourceLocation(source, options = {}) {
    this.pendingBranchSelection = undefined;
    this.cancelSourceRangeZoom();
    const sourcePath = this.resolveSourcePath(source.file);
    const line = Math.max(0, Number(source.line || 1) - 1);
    const position = new vscode.Position(line, Math.max(0, Number(source.column || 1) - 1));
    const reveal = options.preserveHardwareCursor ? { path: normalizedFsPath(sourcePath), position, opening: true } : undefined;
    this.hardwareSourceReveal = reveal;
    try {
      const existingDocument = workspaceTextDocumentForPath(sourcePath, vscode.workspace.textDocuments);
      const document = existingDocument || await vscode.workspace.openTextDocument(vscode.Uri.file(sourcePath));
      const showOptions = await this.sourceShowTextDocumentOptions(sourcePath, Boolean(existingDocument));
      const editor = await vscode.window.showTextDocument(document, showOptions);
      this.rememberSourceViewColumn(editor);
      editor.selection = new vscode.Selection(position, position);
      this.applySourceDecoration(editor, line);
      editor.revealRange(new vscode.Range(position, position), vscode.TextEditorRevealType.InCenter);
      if (reveal) {
        reveal.position = editor.selection.active;
        reveal.opening = false;
      }
    } catch (error) {
      if (this.hardwareSourceReveal === reveal) this.hardwareSourceReveal = undefined;
      throw error;
    }
  }

  async sourceShowTextDocumentOptions(sourcePath, documentAlreadyOpen) {
    const visibleColumn = visibleSourceEditorColumn(sourcePath, vscode.window.visibleTextEditors);
    if (visibleColumn !== undefined) {
      return { viewColumn: visibleColumn, preview: false };
    }
    const tabColumn = openTabGroupColumn(sourcePath, vscode.window.tabGroups);
    if (tabColumn !== undefined) {
      return { viewColumn: tabColumn, preview: false };
    }
    if (documentAlreadyOpen) {
      return { preview: false };
    }
    const q1asmColumn = q1asmTabGroupColumn(vscode.window.tabGroups);
    if (q1asmColumn !== undefined) {
      return { viewColumn: q1asmColumn, preview: false };
    }
    if (isQ1asmPath(sourcePath)) {
      await vscode.commands.executeCommand("workbench.action.newGroupBelow");
      return { viewColumn: vscode.ViewColumn.Active, preview: false };
    }
    return { viewColumn: this.preferredSourceViewColumn(sourcePath), preview: false };
  }

  preferredSourceViewColumn(sourcePath) {
    const visibleColumn = visibleSourceEditorColumn(sourcePath, vscode.window.visibleTextEditors);
    if (visibleColumn !== undefined) {
      return visibleColumn;
    }
    if (this.lastSourceViewColumn !== undefined) {
      return this.lastSourceViewColumn;
    }
    return vscode.ViewColumn.Beside;
  }

  rememberSourceViewColumn(editor) {
    if (!editor || !editor.document || !editor.document.uri || !editor.document.uri.fsPath) {
      return;
    }
    if (editor.viewColumn === undefined) {
      return;
    }
    if (isQ1asmPath(editor.document.uri.fsPath) || (this.projectUri && this.isProjectRelated(editor.document.uri))) {
      this.lastSourceViewColumn = editor.viewColumn;
    }
  }

  applySourceDecoration(editor, line) {
    this.clearSourceDecoration();
    if (!this.sourceSelectionDecoration || !editor || typeof editor.setDecorations !== "function") {
      return;
    }
    const range = new vscode.Range(line, 0, line, Number.MAX_SAFE_INTEGER);
    editor.setDecorations(this.sourceSelectionDecoration, [range]);
    this.activeSourceDecorationEditor = editor;
  }

  clearSourceDecoration() {
    if (
      this.sourceSelectionDecoration &&
      this.activeSourceDecorationEditor &&
      typeof this.activeSourceDecorationEditor.setDecorations === "function"
    ) {
      this.activeSourceDecorationEditor.setDecorations(this.sourceSelectionDecoration, []);
    }
    this.activeSourceDecorationEditor = undefined;
  }

  followSourceEditor(editor, kind) {
    this.cancelSourceRangeZoom();
    if (!editor) return;
    const reveal = this.hardwareSourceReveal;
    if (reveal) {
      const manual = kind !== undefined && (kind === vscode.TextEditorSelectionChangeKind?.Keyboard || kind === vscode.TextEditorSelectionChangeKind?.Mouse);
      const position = editor.selection?.active;
      const sameTarget = normalizedFsPath(editor.document?.uri?.fsPath) === reveal.path && position?.line === reveal.position.line && position?.character === reveal.position.character;
      // showTextDocument and assigning selection can echo after the await returns.
      // Keep the timeline event or hardware value's readable time until the user navigates the source.
      if (!manual && (reveal.opening || sameTarget)) return;
      this.hardwareSourceReveal = undefined;
    }
    if (editor.selection?.isEmpty === false) {
      this.scheduleSourceRangeZoom(editor);
      return;
    }
    this.highlightActiveSourceLine(editor);
  }

  cancelSourceRangeZoom() {
    if (this.sourceRangeTimer !== undefined) {
      clearTimeout(this.sourceRangeTimer);
      this.sourceRangeTimer = undefined;
    }
  }

  invalidateSourceAnalysis() {
    this.resetPlayback();
    this.sourceRevision += 1;
    this.pendingBranchSelection = undefined;
    this.cancelSourceRangeZoom();
  }

  scheduleSourceRangeZoom(editor) {
    const document = editor.document;
    const selection = editor.selection;
    const panel = this.panel;
    const timelineIr = this.timelineIr;
    if (!panel || !timelineIr?.source_map || !document?.uri?.fsPath ||
        !isQ1asmPath(document.uri.fsPath) || !this.isProjectRelated(document.uri) ||
        !selection?.start || !selection?.end) return;
    const version = document.version;
    this.sourceRangeTimer = setTimeout(() => {
      this.sourceRangeTimer = undefined;
      const current = editor.selection;
      // Recheck after the drag settles: analysis, source, or focus may have changed.
      if (vscode.window.activeTextEditor !== editor || document.version !== version ||
          current?.isEmpty !== false || !current.start || !current.end ||
          current.start.line !== selection.start.line || current.start.character !== selection.start.character ||
          current.end.line !== selection.end.line || current.end.character !== selection.end.character ||
          this.panel !== panel || this.timelineIr !== timelineIr || !this.webviewReady ||
          this.hardwareSourceReveal || this.pendingTarget || this.pendingBranchSelection || this.analysisInFlight || this.analysisQueued ||
          this.sourceRevision !== this.analyzedSourceRevision || document.isDirty ||
          this.hasUnsavedProjectChanges() || !this.isProjectRelated(document.uri)) return;
      const startLine = selection.start.line + 1;
      const endLine = Math.max(startLine, selection.end.line + (selection.end.character === 0 ? 0 : 1));
      const currentEventIds = new Set((timelineIr.events || []).map((event) => String(event.id)));
      const highlightEventIds = lookupEventIdsForSourceRange(timelineIr, document.uri.fsPath, startLine, endLine)
        .filter((eventId) => currentEventIds.has(String(eventId)));
      if (highlightEventIds.length) {
        this.postWebviewMessage({ type: "highlightEventIds", highlightEventIds, zoomToRange: true,
          sourceRange: { file: document.uri.fsPath, startLine, endLine } });
      }
    }, 250);
  }

  highlightActiveSourceLine(editor) {
    if (
      !this.panel ||
      !this.timelineIr ||
      !this.timelineIr.source_map ||
      !editor ||
      !editor.document ||
      !this.isProjectRelated(editor.document.uri)
    ) {
      return;
    }
    const line = editor.selection.active.line + 1;
    const highlightEventIds = lookupEventIdsForSourceLine(this.timelineIr, editor.document.uri.fsPath, line);
    this.postWebviewMessage({ type: "highlightEventIds", highlightEventIds });
  }

  async revealPendingTarget() {
    if (!this.pendingTarget?.q1asmFile || !this.pendingTarget.line) {
      return;
    }
    if (!this.timelineIr) {
      return;
    }
    try {
      const target = this.pendingTarget;
      let highlightEventIds = target.zoomToRange
        ? lookupEventIdsForSourceRange(this.timelineIr, target.q1asmFile, target.line, target.endLine)
        : lookupEventIdsForSourceLine(this.timelineIr, target.q1asmFile, target.line);
      if (target.zoomToRange) {
        const currentEventIds = new Set((this.timelineIr.events || []).map((event) => String(event.id)));
        highlightEventIds = highlightEventIds.filter((eventId) => currentEventIds.has(String(eventId)));
      }
      if (highlightEventIds.length) {
        this.postWebviewMessage({ type: "highlightEventIds", highlightEventIds, ...(target.zoomToRange ? {
          zoomToRange: true,
          sourceRange: { file: target.q1asmFile, startLine: target.line, endLine: target.endLine },
        } : {}) });
      } else if (target.zoomToRange) {
        vscode.window.showInformationMessage("No executed timeline events map to the selected Q1ASM lines.");
      }
    } finally {
      this.pendingTarget = undefined;
    }
  }

  provideQ1asmHover(document, position) {
    if (!this.timelineIr || !document || !position || !document.uri || !document.uri.fsPath) {
      return undefined;
    }
    const token = q1asmTokenAt(document, position);
    if (!token) {
      return undefined;
    }
    const sourceLine = position.line + 1;
    let resolvedArgs = resolvedArgsForSourceLine(this.timelineIr, document.uri.fsPath, sourceLine)
      .filter((arg) => resolvedArgMatchesToken(arg, token));
    if (!resolvedArgs.length) {
      resolvedArgs = allResolvedArgs(this.timelineIr).filter((arg) => resolvedArgMatchesToken(arg, token));
    }
    if (!resolvedArgs.length) {
      return undefined;
    }
    const markdown = new vscode.MarkdownString();
    markdown.appendMarkdown("**Q1Lens resolved parameter**\n\n");
    markdown.appendMarkdown(resolvedArgs.map((arg) => formatResolvedArgForHover(arg)).join("\n\n"));
    return new vscode.Hover(markdown, token.range);
  }

  resolveSourcePath(sourceFile) {
    if (path.isAbsolute(sourceFile)) {
      return sourceFile;
    }
    const root = this.timelineIr && this.timelineIr.project && this.timelineIr.project.root
      ? this.timelineIr.project.root
      : path.dirname(this.projectUri.fsPath);
    return path.resolve(root, sourceFile);
  }

  webviewInitialState() {
    return {
      analysisStatus: this.analysisStatus,
      diagnosticSummary: this.diagnosticSummary,
      diagnostics: this.webviewDiagnostics(),
      hasUnsavedChanges: this.hasUnsavedProjectChanges(),
      updateMode: this.updateMode(),
      viewMode: this.viewMode(),
      alignmentPolicy: this.alignmentPolicy(),
      singleFileMode: this.singleFileMode,
      parameterProjectFile: this.projectUri?.fsPath,
      parameterOverrideNames: Array.from(this.parameterOverrides.keys()),
      comparison: this.comparison,
      executionState: this.executionState,
      playbackSnapshotId: this.playbackSnapshotId,
    };
  }

  async readJson(filename) {
    const filePath = path.join(this.outputDir, filename);
    try {
      return JSON.parse(await fsPromises.readFile(filePath, "utf-8"));
    } catch (error) {
      if (error && error.code === "ENOENT") {
        return undefined;
      }
      throw error;
    }
  }

  disposePanelDisposables() {
    for (const disposable of this.panelDisposables) {
      disposable.dispose();
    }
    this.panelDisposables = [];
  }

  dispose() {
    this.resetPlayback();
    this.cancelSourceRangeZoom();
    this.pendingBranchSelection = undefined;
    this.webviewReady = false;
    if (this.changeTimer) {
      clearTimeout(this.changeTimer);
      this.changeTimer = undefined;
    }
    this.clearSourceDecoration();
    if (this.watcher) {
      this.watcher.dispose();
      this.watcher = undefined;
    }
    this.disposePanelDisposables();
    if (this.panel) {
      this.panel.dispose();
      this.panel = undefined;
    }
    this.diagnostics.dispose();
    this.output.dispose();
  }
}

let controller;

