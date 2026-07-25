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
  lookupSourceForEvent,
} = require("./sourceMapLookup");
const updatePolicy = require("./updatePolicy");
const { parseWebviewMessage } = require("./webviewMessageValidation");
const { q1timelineProjectOutputDir } = require("./register");

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
  const jsUri = versionedWebviewAssetUri(webviewAssetUri(webview, context, "src", "q1timeline", "media", "timeline.js"), nonce);
  const linkTag = `<link rel="stylesheet" href="${sharedCssUri}">\n<link rel="stylesheet" href="${cssUri}">`;
  const nonceAttribute = nonce ? ` nonce="${nonce}"` : "";
  const scriptTag = [
    `<script${nonceAttribute} src="${sharedJsUri}"></script>`,
    `<script${nonceAttribute} src="${adapterJsUri}"></script>`,
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

function withDiffStatus(event, status) {
  const copy = cloneJson(event);
  const meta = copy.meta && typeof copy.meta === "object" && !Array.isArray(copy.meta)
    ? { ...copy.meta }
    : {};
  meta.diff_status = status;
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
  if (!previousEvents.length) {
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
      return withDiffStatus(eventWithStableId, "shifted");
    }
    if (eventContentSignature(previousEntry.event) !== eventContentSignature(event)) {
      return withDiffStatus(eventWithStableId, "changed");
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
    this.analyzerResult = undefined;
    this.lastPreviewHtml = undefined;
    this.changeTimer = undefined;
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
    this.pendingTarget = undefined;
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
    this.output.appendLine("Q1ASM Timeline extension activated.");
    context.subscriptions.push(this.output);
    context.subscriptions.push(this.diagnostics);
    if (this.sourceSelectionDecoration) {
      context.subscriptions.push(this.sourceSelectionDecoration);
    }
    context.subscriptions.push(
      vscode.workspace.onDidSaveTextDocument((document) => {
        if (this.isProjectRelated(document.uri)) {
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
        this.highlightActiveSourceLine(event.textEditor);
      })
    );
    if (typeof vscode.window.onDidChangeActiveTextEditor === "function") {
      context.subscriptions.push(
        vscode.window.onDidChangeActiveTextEditor((editor) => {
          this.rememberSourceViewColumn(editor);
          this.highlightActiveSourceLine(editor);
        })
      );
    }
    this.rememberSourceViewColumn(vscode.window.activeTextEditor);
  }

  setProjectUri(uri) {
    if (!this.projectUri || this.projectUri.fsPath !== uri.fsPath) {
      this.lastAnalyzerTimelineIr = undefined;
      this.lastPreviewHtml = undefined;
      this.timelineIr = undefined;
      this.clearAnalyzerOverrides();
    }
    this.projectUri = uri;
  }

  clearAnalyzerOverrides() {
    if (this.branchAssumptionOverrides) {
      this.branchAssumptionOverrides.clear();
    }
    if (this.loopPreviewOverrides) {
      this.loopPreviewOverrides.clear();
    }
  }

  async openTarget(target) {
    this.setProjectUri(vscode.Uri.file(target.projectFile));
    this.outputDir = q1timelineProjectOutputDir(target.projectFile);
    this.singleFileUri = undefined;
    this.singleFileMode = false;
    this.pendingTarget = target;
    await this.openPreview({ preserveProject: true });
  }

  async openPreview(options = {}) {
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
    this.pendingTarget = {
      q1asmFile: editor.document.uri.fsPath,
      line: (editor.selection?.active?.line ?? 0) + 1,
    };
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
    const configuration = this.config();
    const pythonPath = configuration.get("pythonPath", "python");
    const pythonArgs = configuration.get("pythonArgs", []);
    const timeoutMs = configuration.get("analyzer.timeoutMs", 30000);
    const extraEnv = this.normaliseAnalyzerEnv(configuration.get("analyzer.env", {}));
    const args = prependPythonArgs(pythonArgs, ["-m", "q1lens", "q1timeline", "--help"]);
    this.output.appendLine(`Checking analyzer installation: ${[pythonPath, ...args].join(" ")}`);
    this.logAnalyzerEnv(extraEnv);
    return this.execFile(pythonPath, args, timeoutMs, this.workspaceCwd(), extraEnv, "analyzer installation check")
      .then((result) => {
        if (result.stderr) {
          this.output.appendLine(`Analyzer stderr:\n${result.stderr}`);
        }
        this.output.appendLine(`Analyzer installation check completed in ${result.elapsedMs} ms.`);
        vscode.window.showInformationMessage("Q1Lens q1timeline bridge is available.");
      })
      .catch((error) => {
        if (error.stderr) {
          this.output.appendLine(`Analyzer stderr:\n${error.stderr}`);
        }
        this.output.appendLine(`Analyzer installation check failed: ${error.message}`);
        if (this.isAnalyzerMissingError(error)) {
          this.showAnalyzerMissingMessage(error);
        } else {
          vscode.window.showErrorMessage(`q1timeline analyzer check failed: ${error.message}`);
        }
      });
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

  analyzerOverrideArgs() {
    const args = [];
    for (const [branchId, branchPath] of Array.from(this.branchAssumptionOverrides || []).sort()) {
      args.push("--branch-assumption", `${branchId}=${branchPath}`);
    }
    for (const [loopKey, visibleIterations] of Array.from(this.loopPreviewOverrides || []).sort()) {
      args.push("--loop-preview", `${loopKey}=${visibleIterations}`);
    }
    return args;
  }

  async setBranchAssumption(branchId, path) {
    this.branchAssumptionOverrides.set(branchId, path);
    this.lastAnalyzerTimelineIr = undefined;
    this.output.appendLine(`q1timeline branch assumption override: ${branchId}=${path}`);
    await this.runAnalysis();
  }

  async setLoopPreview(loopKey, visibleIterations) {
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
    if (this.analysisInFlight) {
      this.analysisQueued = true;
      this.analysisRequestId += 1;
      this.output.appendLine("Analyzer already running; queued refresh for view mode change.");
      this.setAnalysisStatus("stale", "A newer analysis request is queued.");
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

  async runAnalysis() {
    if (!this.projectUri || !this.outputDir) {
      return Promise.resolve();
    }
    if (this.analysisInFlight) {
      this.analysisQueued = true;
      this.analysisRequestId += 1;
      this.output.appendLine("Analyzer already running; queued refresh.");
      this.setAnalysisStatus("stale", "A newer analysis request is queued.");
      return Promise.resolve();
    }
    this.analysisInFlight = true;
    const analysisRequestId = ++this.analysisRequestId;
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
      ...this.analyzerOverrideArgs(),
      ...extraArgs,
    ]);
    this.output.appendLine(`Analyzer command: ${[pythonPath, ...args].join(" ")}`);
    this.logAnalyzerEnv(extraEnv);
    const cwd = this.projectRootCwd();
    this.output.appendLine(`Analyzer working directory: ${cwd}`);
    await fsPromises.mkdir(this.outputDir, { recursive: true });
    await this.clearAnalyzerOutputFiles(timelineIrPath, diagnosticsPath, htmlPath);
    return this.execFile(pythonPath, args, timeoutMs, cwd, extraEnv, "analyzer")
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
        await this.refreshWebview();
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
        this.clearPreviewAfterAnalyzerFailure("q1timeline analyzer failed.");
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
          this.runAnalysis();
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
    const annotatedTimelineIr = annotateTimelineDiff(this.lastAnalyzerTimelineIr, nextTimelineIr);
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
    vscode.window.showErrorMessage(
      `Q1Lens q1timeline bridge is unavailable. Check q1lens.pythonPath points to a Python environment with q1lens, or set QBSTIMELINE_Q1TIMELINE_PATH to the working q1timeline source checkout. ${error.message}`
    );
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

  async refreshWebview() {
    try {
      this.timelineIr = await this.readJson("timeline_ir.json");
    } catch (error) {
      this.applyFallbackDiagnostic(`Invalid analyzer TimelineIR JSON: ${error.message}`);
      return;
    }
    const diagnostics = await this.loadDiagnostics();
    this.setAnalyzerDiagnostics(diagnostics);
    this.setDiagnosticSummary(summarizeDiagnostics(diagnostics));
    if (!this.panel) {
      return;
    }
    const htmlPath = path.join(this.outputDir, "timeline.html");
    let html;
    try {
      html = await fsPromises.readFile(htmlPath, "utf-8");
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
    html = injectWebviewState(html, this.webviewInitialState());
    html = injectWebviewAssetTags(html, this.panel.webview, this.context, nonce);
    html = injectContentSecurityPolicy(html, contentSecurityPolicy(this.panel.webview, nonce));
    this.panel.webview.html = html;
    await this.revealPendingTarget();
  }

  clearPreviewAfterAnalyzerFailure(message) {
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
    if (parsedMessage.type === "webviewReady") {
      this.output.appendLine("q1timeline webview script ready.");
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
      });
      return;
    }
    if (parsedMessage.type === "setViewMode") {
      await this.setViewMode(parsedMessage.mode);
      return;
    }
    if (parsedMessage.type === "setBranchAssumption") {
      await this.setBranchAssumption(parsedMessage.branchId, parsedMessage.path);
      return;
    }
    if (parsedMessage.type === "setLoopPreview") {
      await this.setLoopPreview(parsedMessage.loopKey, parsedMessage.visibleIterations);
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
    return this.openSourceLocation(source);
  }

  async openSourceForDiagnostic(diagnosticIndex) {
    const item = this.analyzerDiagnostics[diagnosticIndex];
    if (!item || !item.source) {
      return;
    }
    return this.openSourceLocation(item.source);
  }

  async openSourceLocation(source) {
    const sourcePath = this.resolveSourcePath(source.file);
    const existingDocument = workspaceTextDocumentForPath(sourcePath, vscode.workspace.textDocuments);
    const document = existingDocument || await vscode.workspace.openTextDocument(vscode.Uri.file(sourcePath));
    const options = await this.sourceShowTextDocumentOptions(sourcePath, Boolean(existingDocument));
    const editor = await vscode.window.showTextDocument(document, options);
    this.rememberSourceViewColumn(editor);
    const line = Math.max(0, Number(source.line || 1) - 1);
    const position = new vscode.Position(line, Math.max(0, Number(source.column || 1) - 1));
    editor.selection = new vscode.Selection(position, position);
    this.applySourceDecoration(editor, line);
    editor.revealRange(new vscode.Range(position, position), vscode.TextEditorRevealType.InCenter);
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
      const highlightEventIds = lookupEventIdsForSourceLine(this.timelineIr, this.pendingTarget.q1asmFile, this.pendingTarget.line);
      if (highlightEventIds.length) {
        this.postWebviewMessage({ type: "highlightEventIds", highlightEventIds });
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

