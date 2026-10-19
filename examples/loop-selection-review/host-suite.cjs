const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const vscode = require("vscode");

const root = path.resolve(__dirname, "..", "..");
const output = process.env.Q1LENS_LOOP_REVIEW_OUTPUT || path.join(root, "output", "loop-selection-review");
const resultsPath = path.join(output, "host-results.json");
const extensionPath = path.join(root, "vscode-extension");
const controllerPath = path.join(extensionPath, "out", "src", "q1timeline", "controller.js");
const { TimelineController } = require(controllerPath);
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const samePath = (left, right) => path.resolve(left).toLowerCase() === path.resolve(right).toLowerCase();

function describeSelection(document, range) {
  const text = document.getText(range);
  return { first_line: range.start.line + 1, first_column: range.start.character,
    last_line: range.end.line + 1, last_column: range.end.character, chars: text.length, text };
}

exports.run = async () => {
  const report = JSON.parse(fs.readFileSync(resultsPath, "utf8"));
  Object.assign(report, { status: "running", vscodeVersion: vscode.version, controller: controllerPath,
    controller_sha256: crypto.createHash("sha256").update(fs.readFileSync(controllerPath)).digest("hex"),
    source_sha256: crypto.createHash("sha256").update(fs.readFileSync(path.join(extensionPath, "src", "q1timeline", "controller.ts"))).digest("hex"),
    cases: [], results: [], failures: [] });
  const save = () => {
    report.generated_at = new Date().toISOString();
    fs.writeFileSync(resultsPath, JSON.stringify(report, null, 2));
  };
  let failed = 0;
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, "cases.json"), "utf8"));
  for (const item of manifest.cases) {
    const caseResult = { id: item.id, title_ko: item.title_ko, status: "running", started_at: new Date().toISOString(), checks: [], points: [], no_loop_modes: [] };
    report.cases.push(caseResult);
    const source = path.join(__dirname, item.source);
    const sourceText = fs.readFileSync(source, "utf8");
    const sourceLines = sourceText.split(/\r?\n/);
    caseResult.source_sha256 = crypto.createHash("sha256").update(sourceText).digest("hex");
    const subscriptions = [];
    const context = { subscriptions, extensionPath, extensionUri: vscode.Uri.file(extensionPath),
      workspaceState: { get: () => undefined, update: async () => undefined } };
    const controller = new TimelineController(context);
    const posted = [];
    const actualEvents = [];
    controller.projectUri = vscode.Uri.file(path.join(__dirname, item.project));
    controller.projectRelatedPaths = new Set([source]);
    controller.webviewReady = true;
    controller.panel = { webview: { postMessage: (message) => { posted.push(message); return Promise.resolve(true); } }, dispose() {} };
    const observer = vscode.window.onDidChangeTextEditorSelection((event) => {
      if (samePath(event.textEditor.document.uri.fsPath, source)) {
        actualEvents.push({ kind: event.kind, ...describeSelection(event.textEditor.document, event.selections[0]) });
      }
    });
    const irFor = (mode) => {
      const ir = JSON.parse(fs.readFileSync(path.join(output, "fixtures", item.id, `${mode}.json`), "utf8"));
      for (const event of ir.events) {
        if (event.source?.file && samePath(event.source.file, source) && typeof event.source.raw === "string") {
          assert.equal(event.source.raw, sourceLines[event.source.line - 1], `${item.id}/${mode}: source text matches generated IR at L${event.source.line}`);
        }
      }
      return ir;
    };
    async function record(target, name, action) {
      const result = { name, status: "running", started_at: new Date().toISOString() };
      target.push(result);
      try { await action(result); result.status = "passed"; }
      catch (error) { result.status = "failed"; result.error = error.message; failed += 1; }
      result.completed_at = new Date().toISOString();
      if (result.status === "failed") report.failures.push({ case: item.id, mode: result.mode, check: name, error: result.error });
      if (target !== caseResult.no_loop_modes) {
        for (const selection of result.selections || []) {
          const actual = selection.actual;
          report.results.push({ case: item.id, mode: result.mode, check: name,
            kind: target === caseResult.checks ? "loop" : "point", eventId: selection.event_id,
            expected: result.selected_lines || result.line,
            selected: actual && [actual.first_line - 1, actual.first_column, actual.last_line - 1, actual.last_column],
            status: selection.status, error: selection.status === "failed" ? result.error : undefined });
        }
      }
      save();
    }
    async function navigate(event, result, expectedLines) {
      posted.length = 0;
      const firstEvent = actualEvents.length;
      result.status = "failed";
      result.event_id = event.id;
      await controller.openSourceForEvent(event.id);
      const editor = vscode.window.activeTextEditor;
      assert.ok(editor, "source editor opened");
      assert.ok(samePath(editor.document.uri.fsPath, source), "correct source document opened");
      result.actual = describeSelection(editor.document, editor.selection);
      if (expectedLines) {
        const [first, last] = expectedLines;
        const expected = new vscode.Range(first - 1, 0, last - 1, editor.document.lineAt(last - 1).text.length);
        result.expected = describeSelection(editor.document, expected);
        assert.deepEqual(result.actual, result.expected, "entire inclusive source block selected");
        assert.equal(editor.selection.isEmpty, false);
      } else {
        assert.equal(editor.selection.isEmpty, true, "ordinary instruction remains a point selection");
        assert.equal(result.actual.first_line, event.source.line);
      }
      await delay(300);
      controller.followSourceEditor(editor, vscode.TextEditorSelectionChangeKind.Command);
      controller.followSourceEditor(editor);
      await delay(300);
      result.selection_events = actualEvents.slice(firstEvent);
      result.posted_messages = [...posted];
      result.selection_messages = posted.filter((message) => message.type === "highlightEventIds" || message.zoomToRange);
      assert.deepEqual(result.selection_messages, [], "delayed source selection echoes must not replace or zoom the timeline selection");
      assert.equal(controller.sourceRangeTimer, undefined);
      assert.equal(controller.hardwareSourceReveal.position.line, editor.selection.active.line);
      assert.equal(controller.hardwareSourceReveal.position.character, editor.selection.active.character);
      result.status = "passed";
    }
    try {
      for (const check of item.checks) {
        controller.timelineIr = irFor(check.mode);
        const loops = controller.timelineIr.events.filter((event) => event.kind === "loop_block" && event.meta?.source_end?.line === check.source_end);
        await record(caseResult.checks, `${check.mode}: ${check.name}`, async (result) => {
          Object.assign(result, { mode: check.mode, source_end: check.source_end, selected_lines: check.selected, matching_summaries: loops.length, selections: [] });
          assert.ok(loops.length, "matching loop summaries exist");
          for (const event of loops) {
            const selection = { metadata_lines: [event.meta.source_start?.line, event.meta.source_end.line] };
            result.selections.push(selection);
            await navigate(event, selection, check.selected);
          }
        });
      }
      for (const point of item.points) {
        controller.timelineIr = irFor(point.mode);
        const events = controller.timelineIr.events.filter((event) => event.kind === "q1_issue" && event.source?.line === point.line && event.meta?.op?.toLowerCase() === point.op.toLowerCase());
        await record(caseResult.points, `${point.mode}: ${point.op} L${point.line}`, async (result) => {
          Object.assign(result, { mode: point.mode, line: point.line, op: point.op, matching_instructions: events.length, selections: [] });
          assert.ok(events.length, "matching actual instructions exist");
          for (const event of events.slice(0, 1)) {
            const selection = {};
            result.selections.push(selection);
            await navigate(event, selection);
          }
        });
      }
      for (const mode of item.no_loop_modes) {
        await record(caseResult.no_loop_modes, mode, async (result) => {
          result.mode = mode;
          result.loop_summaries = irFor(mode).events.filter((event) => event.kind === "loop_block").length;
          assert.equal(result.loop_summaries, 0, "this execution mode must not invent a loop summary");
        });
      }
    } finally {
      observer.dispose();
      controller.dispose();
      subscriptions.forEach((disposable) => disposable.dispose());
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
      caseResult.status = [...caseResult.checks, ...caseResult.points, ...caseResult.no_loop_modes].some((result) => result.status === "failed") ? "failed" : "passed";
      caseResult.completed_at = new Date().toISOString();
      save();
    }
  }
  report.status = failed ? "failed" : "passed";
  report.failed_checks = failed;
  report.completed_at = new Date().toISOString();
  save();
  assert.equal(failed, 0, `${failed} real VS Code checks failed; see ${resultsPath}`);
  console.log(`Q1LENS_LOOP_SOURCE_REAL_HOST_PASS ${report.cases.length} cases; VS Code ${vscode.version}`);
};
