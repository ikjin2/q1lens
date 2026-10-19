// Drive the real webview assets through Playwright CLI; editor ranges are verified separately by verify-host.cjs.
const { execFile } = require("node:child_process");
const { createHash } = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { promisify } = require("node:util");

const execute = promisify(execFile);
const root = path.resolve(__dirname, "../..");
const output = path.join(root, "output/loop-selection-review");
const captures = path.join(root, "output/playwright/loop-selection-review");
const assets = path.join(root, "vscode-extension/src");
const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, "cases.json"), "utf8"));
const scripts = ["shared/timeline/instructionColors.js", "shared/timeline/renderer.js", "q1timeline/media/timelineAdapter.js",
  "q1timeline/media/hardwareState.js", "q1timeline/media/hardwareView.js", "q1timeline/media/timeline.js"];
const styles = ["shared/timeline/renderer.css", "q1timeline/media/timeline.css", "q1timeline/media/hardwareView.css"];
const session = "q1lens-loop-gallery";
const hash = (file) => createHash("sha256").update(fs.readFileSync(file)).digest("hex");
const escape = (value) => String(value).replace(/[&<>\"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[character]);

function findCli() {
  if (process.env.Q1LENS_PLAYWRIGHT_CLI) return process.env.Q1LENS_PLAYWRIGHT_CLI;
  const cache = path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData/Local"), "npm-cache/_npx");
  for (const entry of fs.readdirSync(cache)) {
    const cli = path.join(cache, entry, "node_modules/@playwright/cli/playwright-cli.js");
    if (fs.existsSync(cli)) return cli;
  }
  throw new Error("Playwright CLI is missing. Run npx --yes --package @playwright/cli playwright-cli --help first.");
}

function preview(item, mode, ir) {
  const state = { viewMode: "debug", executionState: ir.playback || { status: "idle" }, playbackSnapshotId: "gallery" };
  const saved = { timelineFoldStateVersion: 2, timelineFoldSnapshotId: "gallery", timelineWaitsCollapsed: false,
    expandedQ1IssueSequencers: (ir.sequencers || []).map((sequencer) => sequencer.id),
    expandedPlaybackLoops: ir.events.filter((event) => event.kind === "loop_block").map((event) => event.meta.loop_preview_key || event.meta.loop_id || event.id) };
  return `<!doctype html><html><head><meta charset="utf-8"><link rel="icon" href="data:,"><title>${escape(item.id)} · ${mode}</title>
<style>:root{--vscode-foreground:#dedede;--vscode-descriptionForeground:#aaa;--vscode-editor-background:#1e1e1e;--vscode-editor-foreground:#dedede;--vscode-editorWidget-background:#252526;--vscode-sideBar-background:#252526;--vscode-charts-green:#89d185;--vscode-charts-blue:#75beff;--vscode-focusBorder:#007fd4;--vscode-panel-border:#444;--vscode-button-secondaryHoverBackground:#333;--vscode-font-family:Arial;--vscode-font-size:13px}body{margin:16px;background:#1e1e1e;color:#dedede;font:13px Arial}h1{font-size:20px}p{max-width:1100px;line-height:1.5}#capture-note{font-family:monospace;color:#e8c478}</style>
${styles.map((asset) => `<link rel="stylesheet" href="/assets/${asset}">`).join("\n")}</head>
<body class="mode-debug vscode-dark" data-mode="debug"><h1>${escape(item.id)} · ${escape(item.title_ko)} · ${mode}</h1><p>${escape(item.manual_note_ko)}</p>
<p id="capture-note">Browser preview captures event IDs; actual editor selection is checked by verify-host.cjs.</p>
<meta id="q1timeline-webview-state" data-state="${escape(JSON.stringify(state))}"><main id="timeline-root"></main>
<script id="timeline-ir" type="application/json">${JSON.stringify(ir).replace(/</g, "\\u003c")}</script>
<script>window.messages=[];window.browserErrors=[];window.addEventListener('error',e=>window.browserErrors.push(e.message));let saved=${JSON.stringify(saved)};window.acquireVsCodeApi=()=>({postMessage:m=>{window.messages.push(m);if(m.type==='eventClick')document.getElementById('capture-note').textContent='eventClick → '+m.eventId;},getState:()=>saved,setState:s=>{saved=s;}});</script>
${scripts.map((asset) => `<script src="/assets/${asset}"></script>`).join("\n")}</body></html>`;
}

async function main() {
  fs.mkdirSync(captures, { recursive: true });
  fs.mkdirSync(output, { recursive: true });
  const assetBytes = new Map([...scripts, ...styles].map((asset) => [asset, fs.readFileSync(path.join(assets, asset))]));
  const snapshotDirectory = path.join(captures, "assets", new Date().toISOString().replace(/[:.]/g, "-"));
  for (const [asset, bytes] of assetBytes) {
    const file = path.join(snapshotDirectory, asset);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, bytes);
  }
  fs.writeFileSync(path.join(captures, "cli.log"), "");
  const cliPath = findCli();
  let commandNumber = 0;
  async function cli(args) {
    const result = await execute(process.execPath, [cliPath, `-s=${session}`, ...args], { cwd: captures, windowsHide: true, timeout: 45000, maxBuffer: 8 * 1024 * 1024 });
    fs.appendFileSync(path.join(captures, "cli.log"), `${++commandNumber}: ${args[0]}\n${result.stdout}${result.stderr}\n`);
    if (/^### Error/m.test(result.stdout)) throw new Error(result.stdout.trim());
    return result.stdout.trim();
  }
  const evaluate = async (code) => JSON.parse(await cli(["eval", code, "--raw"]));
  const snapshot = (name) => cli(["snapshot", "--filename", path.join(captures, `${name}.yml`)]);
  const fixtures = new Map();
  for (const item of manifest.cases) {
    for (const mode of ["analysis", "playback"]) {
      const file = path.join(output, "fixtures", item.id, `${mode}.json`);
      if (fs.existsSync(file)) fixtures.set(`${item.id}/${mode}`, JSON.parse(fs.readFileSync(file, "utf8")));
    }
  }
  const result = { status: "passed", generated_at: new Date().toISOString(), results: [], failures: [],
    limits: ["Browser checks verify event routing, actual clicks, and loop options. Actual VS Code selections are verified separately by verify-host.cjs."],
    asset_snapshot: { directory: snapshotDirectory, immutable: true },
    asset_sha256: Object.fromEntries([...assetBytes].map(([asset, bytes]) => [asset, createHash("sha256").update(bytes).digest("hex")])), fixture_sha256: {}, overview: path.join(captures, "overview.png") };
  fs.writeFileSync(path.join(output, "browser-results.json"), `${JSON.stringify({ ...result, status: "running", complete: false }, null, 2)}\n`);
  const server = http.createServer((request, response) => {
    try {
      const url = new URL(request.url, "http://127.0.0.1");
      if (url.pathname.startsWith("/assets/")) {
        const relative = url.pathname.slice(8);
        if (![...scripts, ...styles].includes(relative)) { response.writeHead(404).end(); return; }
        response.setHeader("Content-Type", relative.endsWith(".css") ? "text/css" : "text/javascript");
        response.end(assetBytes.get(relative)); return;
      }
      if (url.pathname === "/preview") {
        const item = manifest.cases.find((candidate) => candidate.id === url.searchParams.get("case"));
        const mode = url.searchParams.get("mode");
        const ir = item && fixtures.get(`${item.id}/${mode}`);
        if (!ir) { response.writeHead(404).end("Fixture not found"); return; }
        response.setHeader("Content-Type", "text/html; charset=utf-8"); response.end(preview(item, mode, ir)); return;
      }
      if (url.pathname === "/overview") {
        response.setHeader("Content-Type", "text/html; charset=utf-8"); response.end(fs.readFileSync(path.join(captures, "overview.html"))); return;
      }
      response.writeHead(404).end();
    } catch (error) { response.writeHead(500).end(error.message); }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const captureByCase = new Map();
  try {
    await cli(["open", "about:blank"]);
    await cli(["resize", "1600", "1100"]);
    for (const item of manifest.cases) {
      for (const mode of ["analysis", "playback"]) {
        const checks = item.checks.filter((check) => check.mode === mode);
        const points = item.points.filter((point) => point.mode === mode);
        if (!checks.length && !points.length && !(item.no_loop_modes || []).includes(mode)) continue;
        const ir = fixtures.get(`${item.id}/${mode}`);
        if (!ir) throw new Error(`Missing fixture: ${item.id}/${mode}`);
        result.fixture_sha256[`${item.id}/${mode}`] = hash(path.join(output, "fixtures", item.id, `${mode}.json`));
        await cli(["goto", `${base}/preview?case=${encodeURIComponent(item.id)}&mode=${mode}`]);
        await snapshot(`${item.id}-${mode}-initial`);
        const errors = await evaluate("() => window.browserErrors");
        if (errors.length) throw new Error(`${item.id}/${mode}: ${errors.join("; ")}`);
        for (const check of checks) {
          const expected = ir.events.filter((event) => event.kind === "loop_block" && event.meta?.source_end?.line === check.source_end);
          if (!expected.length) throw new Error(`No loop event for ${item.id}/${mode}/${check.name}`);
          for (const event of expected) {
            const row = { case: item.id, mode, check: check.name, kind: "loop", eventId: event.id, status: "passed", selected_expected: check.selected };
            try {
              const selector = `.timeline-loop-range[data-event-id=${JSON.stringify(event.id)}]`;
              let count = await evaluate(`() => document.querySelectorAll(${JSON.stringify(selector)}).length`);
              if (!count) {
                await cli(["click", "css=button[data-timeline-layout=instruction]"]);
                await snapshot(`${item.id}-${mode}-${check.name}-instruction-fallback`);
                row.setup = { layout: "instruction", reason: "No summary node in time-scale view; verify zero-duration loop in instruction order" };
                count = await evaluate(`() => document.querySelectorAll(${JSON.stringify(selector)}).length`);
              }
              if (!count) throw new Error("Loop summary is not rendered even with enclosing playback loops expanded");
              await evaluate("() => { window.messages=[]; return true; }");
              let clicked = false;
              for (const target of [".loop-range-cap-start", ".loop-range-cap-end", ".loop-range-rail"]) {
                try { await cli(["click", `css=${selector} ${target}`]); clicked = true; break; } catch (_) {}
              }
              if (!clicked) throw new Error("All loop summary click targets were obstructed");
              await snapshot(`${item.id}-${mode}-${check.name}-${result.results.length}`);
              const observed = await evaluate(`() => ({ clicks:window.messages.filter(m=>m.type==='eventClick'), popup:document.getElementById('q1timeline-control-popover')?.dataset.ownerChipId, actions:[...document.querySelectorAll('#q1timeline-control-popover button')].map(n=>n.getAttribute('aria-label')) })`);
              row.observed = observed;
              if (observed.clicks.length !== 1 || observed.clicks[0].eventId !== event.id) throw new Error(`Unexpected click route: ${JSON.stringify(observed.clicks)}`);
              if (observed.popup !== `loop:${event.id}` || !observed.actions.length) throw new Error("Loop options did not open for the clicked summary");
              if (!captureByCase.has(item.id)) {
                row.screenshot = path.join(captures, `${item.id}-${mode}.png`);
                await cli(["screenshot", "--filename", row.screenshot, "--full-page"]);
                captureByCase.set(item.id, row.screenshot);
              }
              if (item.id === "03-nested-counted" && mode === "analysis" && check.name === "outer") {
                row.comparison_screenshot = path.join(captures, "03-nested-counted-analysis-outer-after.png");
                await cli(["screenshot", "--filename", row.comparison_screenshot, "--full-page"]);
              }
              await cli(["click", "css=h1"]);
            } catch (error) { row.status = "failed"; row.error = error.message; result.failures.push({ case: item.id, mode, check: check.name, eventId: event.id, error: error.message }); }
            result.results.push(row);
          }
        }
        if ((item.no_loop_modes || []).includes(mode)) {
          const summaryCount = await evaluate("() => document.querySelectorAll('.timeline-loop-range').length");
          const row = { case: item.id, mode, check: "no false loop summary", kind: "loop", eventId: null, status: summaryCount === 0 ? "passed" : "failed", observed: summaryCount };
          result.results.push(row);
          if (summaryCount) result.failures.push(row);
        }
        const controls = await evaluate(`() => [...document.querySelectorAll('.q1timeline-control-chip')].map(chip => {
          const row=chip.closest('.timeline-row'), track=chip.closest('.lane-track');
          const offset=parseFloat(getComputedStyle(row).getPropertyValue('--timeline-loop-offset'))||0;
          if(!offset) return null;
          const block=[...track.querySelectorAll('.timeline-block')].find(node=>node.dataset.eventId===chip.dataset.eventId);
          const chipRect=chip.getBoundingClientRect(), trackRect=track.getBoundingClientRect(), blockRect=block?.getBoundingClientRect();
          return {eventId:chip.dataset.eventId,controlId:chip.dataset.controlChipId,offset,stack:Number(chip.dataset.stackIndex)||0,
            chipTop:chipRect.top,trackTop:trackRect.top,blockTop:blockRect?.top};
        }).filter(Boolean)`);
        if (item.id === "17-nested-forward-branch" && mode === "analysis" && !controls.length) {
          const row = { case: item.id, mode, check: "control chip clears stacked loop strips", kind: "control", eventId: null, status: "failed", error: "Expected a runtime branch control chip in the row with stacked loop strips" };
          result.results.push(row);
          result.failures.push(row);
        }
        for (const control of controls) {
          const row = { case: item.id, mode, check: "control chip clears stacked loop strips", kind: "control", eventId: control.eventId, status: "passed", geometry: control };
          try {
            if (control.chipTop < control.trackTop + control.offset - 1) throw new Error("Control chip overlaps the reserved loop strips");
            if (!Number.isFinite(control.blockTop) || Math.abs(control.chipTop - (control.blockTop - 6 + control.stack * 17)) > 1) throw new Error("Control chip is displaced from its corresponding event block");
            await evaluate("() => { window.messages=[]; return true; }");
            await cli(["click", `css=.q1timeline-control-chip[data-control-chip-id=${JSON.stringify(control.controlId)}]`]);
            await snapshot(`${item.id}-${mode}-control-offset`);
            const observed = await evaluate("() => ({popup:document.getElementById('q1timeline-control-popover')?.dataset.ownerChipId,actions:document.querySelectorAll('#q1timeline-control-popover button').length,clicks:window.messages.filter(m=>m.type==='eventClick')})");
            row.observed = observed;
            if (observed.popup !== control.controlId || !observed.actions || observed.clicks.length) throw new Error("Control chip did not open its options independently of source navigation");
            row.screenshot = path.join(captures, `${item.id}-${mode}-control-offset.png`);
            await cli(["screenshot", "--filename", row.screenshot, "--full-page"]);
            await cli(["click", "css=h1"]);
          } catch (error) { row.status = "failed"; row.error = error.message; result.failures.push({ ...row }); }
          result.results.push(row);
        }
        if (points.length) {
          await cli(["click", "css=button[data-timeline-layout=instruction]"]);
          await snapshot(`${item.id}-${mode}-instruction`);
          for (const point of points) {
            const matches = ir.events.filter((event) => event.kind === "q1_issue" && event.source?.line === point.line && event.meta?.op === point.op);
            const ids = await evaluate("() => [...document.querySelectorAll('.timeline-block[data-event-id]')].map(n=>n.dataset.eventId)");
            const event = matches.find((candidate) => ids.includes(candidate.id));
            const row = { case: item.id, mode, check: `${point.op} line ${point.line}`, kind: "point", eventId: event?.id, status: "passed" };
            try {
              if (!event) throw new Error("No visible Q1 opcode block matches the expected source point");
              await evaluate("() => { window.messages=[]; return true; }");
              const selector = `.timeline-block[data-event-id=${JSON.stringify(event.id)}]`;
              await cli(["run-code", `async page => { const block=page.locator(${JSON.stringify(selector)}); const box=await block.boundingBox(); await block.click({position:{x:Math.min(10,box.width/2),y:Math.max(1,box.height-2)},timeout:3000}); }`]);
              row.setup = { layout: "instruction", click_position: "lower edge below branch control chips" };
              const clicks = await evaluate("() => window.messages.filter(m=>m.type==='eventClick')");
              if (clicks.length !== 1 || clicks[0].eventId !== event.id) throw new Error(`Unexpected point route: ${JSON.stringify(clicks)}`);
              row.observed = clicks;
            } catch (error) { row.status = "failed"; row.error = error.message; result.failures.push({ ...row }); }
            result.results.push(row);
          }
        }
        const laterLoopCheck = mode === "analysis" && item.checks.some((check) => check.mode === "playback");
        if (!captureByCase.has(item.id) && !laterLoopCheck) {
          const screenshot = path.join(captures, `${item.id}-${mode}.png`);
          await cli(["screenshot", "--filename", screenshot, "--full-page"]);
          captureByCase.set(item.id, screenshot);
          const row = result.results.find((entry) => entry.case === item.id);
          if (row) row.screenshot = screenshot;
        }
      }
      console.log(`Browser verified ${item.id}: ${result.results.filter((entry) => entry.case === item.id && entry.status === "passed").length} checks passed`);
      fs.writeFileSync(path.join(output, "browser-results.json"), `${JSON.stringify({ ...result, status: "running", complete: false }, null, 2)}\n`);
    }
    const cards = manifest.cases.map((item) => `<article><h2>${escape(item.id)} · ${escape(item.title_ko)}</h2><p>${escape(item.description_ko)}</p><p>${result.results.filter((row) => row.case === item.id && row.status === "passed").length} browser checks passed</p></article>`).join("\n");
    fs.writeFileSync(path.join(captures, "overview.html"), `<!doctype html><meta charset="utf-8"><title>Loop selection verification</title><style>body{margin:32px;background:#181a1f;color:#e2e6eb;font:16px Arial}h1{color:#f4a261}main{display:grid;grid-template-columns:1fr 1fr;gap:14px}article{border:1px solid #59636e;padding:16px;background:#242830}h2{font-size:17px}p{line-height:1.4;color:#c3cad2}</style><h1>Loop selection · browser verification</h1><p>Actual webview assets · ${manifest.cases.length} cases · ${result.results.length} click and summary checks · ${result.failures.length} failures</p><main>${cards}</main>`);
    await cli(["goto", `${base}/overview`]);
    await snapshot("overview");
    await cli(["screenshot", "--filename", result.overview, "--full-page"]);
  } catch (error) { result.failures.push({ error: error.message }); }
  finally {
    try { await cli(["close"]); } catch (error) { result.failures.push({ error: `Browser cleanup: ${error.message}` }); }
    await new Promise((resolve) => server.close(resolve));
    result.current_asset_sha256 = Object.fromEntries([...assetBytes.keys()].map((asset) => [asset, hash(path.join(assets, asset))]));
    result.source_changed_during_run = [...assetBytes.keys()].filter((asset) => result.current_asset_sha256[asset] !== result.asset_sha256[asset]);
    result.source_matches_snapshot = result.source_changed_during_run.length === 0;
    result.completed_at = new Date().toISOString();
    result.status = result.failures.length ? "failed" : "passed";
    result.complete = true;
    fs.writeFileSync(path.join(output, "browser-results.json"), `${JSON.stringify(result, null, 2)}\n`);
  }
  console.log(`${result.status}: ${result.results.length} browser checks; ${result.failures.length} failures`);
  process.exitCode = result.failures.length ? 1 : 0;
}

main().catch((error) => { console.error(error.stack); process.exitCode = 1; });
