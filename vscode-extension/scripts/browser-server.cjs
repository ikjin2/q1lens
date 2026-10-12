"use strict";
// Test-only loopback server. Every start creates new CLI artifacts; no old IR is reused.
const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const repo = path.resolve(__dirname, "../..");
const output = path.resolve(process.env.Q1LENS_BROWSER_OUTPUT || path.join(repo, "output/ci-baseline-validation/browser"));
const python = process.env.Q1LENS_BROWSER_PYTHON || "python";
const cases = ["01-digital", "02-communication", "03-signals", "04-underflow", "05-supplied-input"];
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
fs.mkdirSync(output, { recursive: true });
const run = fs.mkdtempSync(path.join(output, "fixtures-"));
const irFiles = new Map();
const manifest = { created_at: new Date().toISOString(), python, run, cases: [], assets: {}, model: "Actual CLI simulation IR; declared software model, not hardware measurements" };
for (const name of cases) {
  const project = path.join(repo, "examples/simulation-verification", name, "q1timeline.yml");
  const file = path.join(run, `${name}.json`);
  const args = ["-m", "q1lens", "q1timeline", "analyze", "--project", project, "--out", file, "--format", "vscode-json", "--summary-only", "--no-render"];
  assert.ok(!fs.existsSync(file), "Fresh fixture output must not exist before this invocation");
  const result = spawnSync(python, args, {
    cwd: repo, encoding: "utf8", timeout: 60000, maxBuffer: 20 * 1024 * 1024,
    env: { ...process.env, PYTHONHASHSEED: "0", PYTHONPATH: [path.join(repo, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) },
  });
  fs.writeFileSync(path.join(run, `${name}.log`), JSON.stringify({ command: [python, ...args], status: result.status, signal: result.signal, error: result.error?.message }) + "\n" + result.stdout + result.stderr);
  assert.equal(result.error, undefined, `${name}: CLI could not finish`);
  assert.equal(result.status, name === "04-underflow" ? 2 : 0, `${name}: unexpected CLI exit; see ${run}`);
  const bytes = fs.readFileSync(file);
  const ir = JSON.parse(bytes);
  assert.ok(Array.isArray(ir.events) && ir.events.length && ir.execution, `${name}: missing execution trace`);
  if (name === "04-underflow") {
    assert.equal(ir.execution.starved.reason, "RT queue underflow at 24 ns.");
    assert.equal(ir.events.find((event) => event.kind === "underflow_warning")?.t0.value, 24);
    assert.deepEqual(ir.diagnostics.filter((item) => item.severity === "error").map((item) => item.category), ["rt_queue_underflow"]);
  } else {
    assert.ok(Object.values(ir.execution).every((item) => item.status === "completed"));
  }
  irFiles.set(name, file);
  manifest.cases.push({ name, exit_code: result.status, file, sha256: hash(bytes), events: ir.events.length,
    noise_seeds: Object.fromEntries(Object.entries(ir.simulation || {}).filter(([, value]) => value.signals?.parameters).map(([id, value]) => [id, value.signals.parameters.noise_seed])) });
}
// The selected user demo has an independent arithmetic oracle and two fresh CLI
// runs per profile. It references original Q1ASM and asserts their hashes unchanged.
const cursorOutput = path.join(run, "cursor");
const cursorArgs = [path.join(repo, "examples/cursor-frequency-demo/verification/verify.py"), "--out-dir", cursorOutput];
const cursorResult = spawnSync(python, cursorArgs, {
  cwd: repo, encoding: "utf8", timeout: 120000, maxBuffer: 20 * 1024 * 1024,
  env: { ...process.env, PYTHONHASHSEED: "0", PYTHONPATH: [path.join(repo, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) },
});
fs.writeFileSync(path.join(run, "cursor.log"), JSON.stringify({ command: [python, ...cursorArgs], status: cursorResult.status, signal: cursorResult.signal, error: cursorResult.error?.message }) + "\n" + cursorResult.stdout + cursorResult.stderr);
assert.equal(cursorResult.error, undefined, "Cursor baseline verification could not finish");
assert.equal(cursorResult.status, 0, `Cursor baseline oracle failed; see ${run}`);
for (const profile of ["two-shot-feedback", "deadband-hold"]) {
  const name = `cursor-${profile}`;
  const file = path.join(cursorOutput, profile, "timeline_ir.json");
  const bytes = fs.readFileSync(file), ir = JSON.parse(bytes);
  const evidenceFile = path.join(cursorOutput, profile, "evidence.json");
  const evidence = JSON.parse(fs.readFileSync(evidenceFile));
  assert.equal(evidence.repetitions, 2);
  assert.equal(evidence.synthetic_inputs, true);
  assert.equal(evidence.hardware_measurement, false);
  assert.ok(Object.values(ir.execution).every((item) => item.status === "blocked"), "The original continuous loops must await the unsupplied third input");
  irFiles.set(name, file);
  manifest.cases.push({ name, exit_code: cursorResult.status, file, sha256: hash(bytes), events: ir.events.length, evidence: evidenceFile, expected_status: "blocked", synthetic_inputs: true, hardware_measurement: false });
}
manifest.extended_cases = [];
const replayName = "concrete-replay", replayFile = path.join(run, `${replayName}.json`);
const replayProject = path.join(repo, "examples/concrete-register-execution/q1timeline.yml");
const replayArgs = ["-m", "q1lens", "q1timeline", "analyze", "--project", replayProject, "--out", replayFile, "--format", "vscode-json", "--summary-only", "--no-render"];
assert.ok(!fs.existsSync(replayFile), "Replay fixture must be freshly generated");
const replayResult = spawnSync(python, replayArgs, {
  cwd: repo, encoding: "utf8", timeout: 60000, maxBuffer: 20 * 1024 * 1024,
  env: { ...process.env, PYTHONHASHSEED: "0", PYTHONPATH: [path.join(repo, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) },
});
fs.writeFileSync(path.join(run, `${replayName}.log`), JSON.stringify({ command: [python, ...replayArgs], status: replayResult.status, signal: replayResult.signal, error: replayResult.error?.message }) + "\n" + replayResult.stdout + replayResult.stderr);
assert.equal(replayResult.error, undefined, "Replay fixture CLI could not finish");
assert.equal(replayResult.status, 0, "Replay fixture CLI failed");
const replayBytes = fs.readFileSync(replayFile), replayIr = JSON.parse(replayBytes);
assert.equal(replayIr.execution.counter.status, "completed");
assert.equal(replayIr.events.filter((event) => event.kind === "q1_issue").length, 19);
irFiles.set(replayName, replayFile);
manifest.extended_cases.push({ name: replayName, file: replayFile, exit_code: replayResult.status,
  expected_status: "completed", events: replayIr.events.length, sha256: hash(replayBytes) });
const currentName = "current-project-replay", currentFile = path.join(run, `${currentName}.json`);
const currentArgs = ["-m", "q1lens", "q1timeline", "analyze", "--project", path.join(repo, "examples/cursor-frequency-demo/q1timeline.yml"), "--execution-mode", "simulation", "--max-steps", "10000", "--out", currentFile, "--format", "vscode-json", "--summary-only", "--no-render"];
const currentResult = spawnSync(python, currentArgs, {
  cwd: repo, encoding: "utf8", timeout: 60000, maxBuffer: 20 * 1024 * 1024,
  env: { ...process.env, PYTHONHASHSEED: "0", PYTHONPATH: [path.join(repo, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) },
});
fs.writeFileSync(path.join(run, `${currentName}.log`), JSON.stringify({ command: [python, ...currentArgs], status: currentResult.status, error: currentResult.error?.message }) + "\n" + currentResult.stdout + currentResult.stderr);
assert.equal(currentResult.error, undefined, "Current project replay CLI could not finish");
assert.equal(currentResult.status, 0, "Current project replay CLI failed");
const currentBytes = fs.readFileSync(currentFile), currentIr = JSON.parse(currentBytes);
assert.ok(Object.values(currentIr.execution).every((item) => item.mode === "simulation" && item.status === "blocked"));
irFiles.set(currentName, currentFile);
manifest.extended_cases.push({ name: currentName, file: currentFile, exit_code: currentResult.status, expected_status: "blocked", events: currentIr.events.length, sha256: hash(currentBytes) });
for (const blocked of [false, true]) {
  const name = blocked ? "joint-sync-blocked" : "joint-sync-replay", directory = path.join(run, name);
  fs.mkdirSync(directory);
  fs.writeFileSync(path.join(directory, "early.q1asm"), "move 7,R0\nwait 8\nwait_sync 4\nwait 8\nstop\n");
  fs.writeFileSync(path.join(directory, "late.q1asm"), `move 9,R0\nwait 28\n${blocked ? "" : "wait_sync 4\n"}wait 8\nstop\n`);
  const project = path.join(directory, "q1timeline.yml"), file = path.join(directory, "timeline_ir.json");
  fs.writeFileSync(project, "analysis:\n  execution_mode: simulation\nsequencers:\n  - id: early\n    module: QCM0\n    file: early.q1asm\n  - id: late\n    module: QCM0\n    file: late.q1asm\n");
  const args = ["-m", "q1lens", "q1timeline", "analyze", "--project", project, "--out", file, "--format", "vscode-json", "--summary-only", "--no-render"];
  const result = spawnSync(python, args, {
    cwd: repo, encoding: "utf8", timeout: 60000, maxBuffer: 20 * 1024 * 1024,
    env: { ...process.env, PYTHONHASHSEED: "0", PYTHONPATH: [path.join(repo, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) },
  });
  fs.writeFileSync(path.join(directory, "analysis.log"), JSON.stringify({ command: [python, ...args], status: result.status, error: result.error?.message }) + "\n" + result.stdout + result.stderr);
  assert.equal(result.error, undefined, `${name}: CLI could not finish`);
  assert.equal(result.status, 0, `${name}: CLI failed`);
  const bytes = fs.readFileSync(file), ir = JSON.parse(bytes);
  assert.equal(ir.execution.early.status, blocked ? "blocked" : "completed");
  assert.equal(ir.execution.late.status, "completed");
  assert.equal(ir.events.find((event) => event.kind === "sync_wait" && event.sequencer_id === "early")?.meta?.pending, blocked ? true : undefined);
  irFiles.set(name, file);
  manifest.extended_cases.push({ name, file, exit_code: result.status, expected_status: blocked ? "blocked" : "completed", events: ir.events.length, sha256: hash(bytes) });
}
for (const [profile, expectedStatus, expectedExit] of [
  ["01-feedback-missing", "error", 2], ["02-feedback-delayed", "completed", 0],
  ["03-feedback-duplicate", "completed", 0], ["04-trigger-missing", "blocked", 0],
  ["05-queue-full", "completed", 0], ["06-feedback-too-late", "error", 2],
  ["07-feedback-overflow", "error", 2],
]) {
  const name = `fault-${profile}`, file = path.join(run, `${name}.json`);
  const project = path.join(repo, "examples/simulation-verification/faults", profile, "q1timeline.yml");
  const args = ["-m", "q1lens", "q1timeline", "analyze", "--project", project, "--out", file, "--format", "vscode-json", "--summary-only", "--no-render"];
  const result = spawnSync(python, args, {
    cwd: repo, encoding: "utf8", timeout: 60000, maxBuffer: 20 * 1024 * 1024,
    env: { ...process.env, PYTHONHASHSEED: "0", PYTHONPATH: [path.join(repo, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) },
  });
  fs.writeFileSync(path.join(run, `${name}.log`), JSON.stringify({ command: [python, ...args], status: result.status, signal: result.signal, error: result.error?.message }) + "\n" + result.stdout + result.stderr);
  assert.equal(result.error, undefined, `${name}: CLI could not finish`);
  assert.equal(result.status, expectedExit, `${name}: unexpected CLI exit`);
  const bytes = fs.readFileSync(file), ir = JSON.parse(bytes);
  assert.equal(ir.execution.seq.status, expectedStatus, `${name}: unexpected execution status`);
  if (expectedStatus === "error") {
    const overflow = profile === "07-feedback-overflow";
    assert.equal(ir.execution.seq.reason, overflow ? "Feedback queue overflow at 400 ns." : "RT queue underflow at 1200 ns.");
    assert.deepEqual(ir.diagnostics.filter((item) => item.severity === "error").map((item) => item.category), [overflow ? "feedback_queue_overflow" : "rt_queue_underflow"]);
  }
  irFiles.set(name, file);
  manifest.extended_cases.push({ name, file, exit_code: result.status, expected_status: expectedStatus, events: ir.events.length, sha256: hash(bytes) });
}
// Only the compact three-shot/four-bin preview belongs in browser setup. The
// independent long-run profiles are exercised by the Python boundary harness.
const boundaryOutput = path.join(run, "boundaries");
const boundaryArgs = [path.join(repo, "examples/cursor-frequency-demo/verification/verify_boundaries.py"), "--profile", "bin-wrap-preview", "--out-dir", boundaryOutput];
const boundaryResult = spawnSync(python, boundaryArgs, {
  cwd: repo, encoding: "utf8", timeout: 60000, maxBuffer: 20 * 1024 * 1024,
  env: { ...process.env, PYTHONHASHSEED: "0", PYTHONPATH: [path.join(repo, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) },
});
fs.writeFileSync(path.join(run, "bin-wrap-preview.log"), JSON.stringify({ command: [python, ...boundaryArgs], status: boundaryResult.status, signal: boundaryResult.signal, error: boundaryResult.error?.message }) + "\n" + boundaryResult.stdout + boundaryResult.stderr);
assert.equal(boundaryResult.error, undefined, "Compact bin-wrap oracle could not finish");
assert.equal(boundaryResult.status, 0, `Compact bin-wrap oracle failed; see ${run}`);
const boundaryFile = path.join(boundaryOutput, "bin-wrap-preview/timeline_ir.json");
const boundaryBytes = fs.readFileSync(boundaryFile), boundaryIr = JSON.parse(boundaryBytes);
assert.equal(boundaryIr.sequencers.length, 4);
irFiles.set("bin-wrap-preview", boundaryFile);
manifest.extended_cases.push({ name: "bin-wrap-preview", file: boundaryFile, exit_code: boundaryResult.status,
  events: boundaryIr.events.length, sha256: hash(boundaryBytes),
  evidence: path.join(boundaryOutput, "bin-wrap-preview/evidence.json"),
  scope: "Three shots; explicit four-bin verification override; not the original 65536-bin capacity" });
const assets = new Map(["hardwareState.js", "hardwareView.js", "hardwareView.css"].map((name) => {
  const file = path.join(repo, "vscode-extension/src/q1timeline/media", name);
  manifest.assets[name] = { source: path.relative(repo, file).split(path.sep).join("/"), sha256: hash(fs.readFileSync(file)) };
  return [`/assets/${name}`, file];
}));
fs.writeFileSync(path.join(output, "manifest.json"), JSON.stringify(manifest, null, 2));
const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><title>Q1Lens browser regression</title><style>body{margin:20px;background:#1e1e1e;color:#dedede;font-family:sans-serif}</style><link rel="stylesheet" href="/assets/hardwareView.css"></head><body><main id="hardware"></main><script src="/assets/hardwareState.js"></script><script src="/assets/hardwareView.js"></script><script>
const name=new URLSearchParams(location.search).get('case')||'03-signals';
fetch('/ir/'+encodeURIComponent(name)+'.json').then(response=>{if(!response.ok)throw Error('IR request failed: '+response.status);return response.json()}).then(ir=>{window.demoIr=ir;window.hardwarePreview=q1lensHardwareView.mount(document.getElementById('hardware'),ir,{onSelectEvent(id){window.selectedEventId=id},onSourceClick(source){window.revealedSource=source},onPrepareReplay(id,scope){window.requestedReplaySequencer=id;window.requestedReplayScope=scope}})}).catch(error=>{console.error(error);document.getElementById('hardware').textContent=error.message});
</script></body></html>`;
const server = http.createServer((request, response) => {
  const pathname = new URL(request.url, "http://127.0.0.1").pathname;
  response.setHeader("Cache-Control", "no-store");
  if (pathname === "/") { response.setHeader("Content-Type", "text/html; charset=utf-8"); response.end(html); return; }
  if (pathname === "/manifest.json") { response.setHeader("Content-Type", "application/json"); response.end(JSON.stringify(manifest)); return; }
  const name = /^\/ir\/([\w-]+)\.json$/.exec(pathname)?.[1];
  const file = assets.get(pathname) || irFiles.get(name);
  if (!file) { response.writeHead(404); response.end("Not found"); return; }
  response.setHeader("Content-Type", file.endsWith(".css") ? "text/css" : file.endsWith(".js") ? "text/javascript" : "application/json");
  response.end(fs.readFileSync(file));
});
server.listen(Number(process.env.Q1LENS_BROWSER_PORT || 4173), "127.0.0.1", () => console.log(`Q1Lens browser fixtures ready: ${run}`));
