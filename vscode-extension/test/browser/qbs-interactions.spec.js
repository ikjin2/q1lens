const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test, expect } = require("./helpers");

async function mount(page) {
  await page.goto("about:blank");
  await page.setContent('<!doctype html><html><head></head><body><header class="topbar"><h1>QBS</h1><button id="refresh-button">Refresh</button></header><main id="qbs-root" class="timeline-shell"></main></body></html>');
  await page.evaluate(() => {
    window.messages = [];
    window.acquireVsCodeApi = () => ({ postMessage: (message) => window.messages.push(message) });
  });
  const assets = path.resolve(__dirname, "../../src");
  for (const file of ["shared/timeline/renderer.css", "qbs/webview/assets/timeline.css"]) {
    await page.addStyleTag({ path: path.join(assets, file) });
  }
  for (const file of ["shared/timeline/instructionColors.js", "shared/timeline/renderer.js",
    "qbs/webview/assets/timelineModel.js", "qbs/webview/assets/timeline.js"]) {
    await page.addScriptTag({ path: path.join(assets, file) });
  }
  await page.evaluate(() => window.dispatchEvent(new MessageEvent("message", { data: {
    type: "render", sourceContext: { projectFile: "/project/qbstimeline.yml", scheduleFile: "/project/schedule.py" },
    ir: {
      schedule: { name: "Repeated operations" },
      operations: [
        { id: "first", operation_id: "pulse-op", schedulable_id: "first", label: "First(q0)", abs_time: 0, duration: 40e-9 },
        { id: "second", operation_id: "pulse-op", schedulable_id: "second", label: "Second(q0)", abs_time: 50e-9, duration: 40e-9 },
      ],
    },
  } })));
}

test("double-clicking a QBS block opens its source after selecting the occurrence", async ({ page }) => {
  await mount(page);
  await page.locator('[data-block-id="second"]').dblclick();
  await expect(page.locator(".inspector h2")).toHaveText("Second(q0)");
  expect(await page.evaluate(() => window.messages.filter((message) => message.type === "openScheduleSource"))).toEqual([
    { type: "openScheduleSource", schedulableId: "second", operationId: "pulse-op", blockId: "second" },
  ]);
});

test("QBS navigation buttons and source jumps carry the displayed render identity", async ({ page }) => {
  await mount(page);
  expect(await page.evaluate(() => window.messages)).toEqual([{ type: "ready" }]);
  for (const renderId of [41, 42]) {
    await page.evaluate((renderId) => {
      window.messages = [];
      window.dispatchEvent(new MessageEvent("message", { data: {
        type: "render", renderId,
        sourceContext: { projectFile: "/project/qbstimeline.yml", scheduleFile: "/project/schedule.py", outputDir: "/project/output" },
        ir: { schedule: { name: "Refresh ownership" }, operations: [
          { id: "selected", operation_id: "pulse-op", label: "Selected(q0)", abs_time: 0, duration: 40e-9 },
        ] },
      } }));
    }, renderId);
    await page.locator('.source-actions').getByRole("button", { name: "Open qbstimeline.yml", exact: true }).click();
    await page.locator('.source-actions').getByRole("button", { name: "Open schedule.py", exact: true }).click();
    await page.locator('.inspector-actions').getByRole("button", { name: "Open schedule.py", exact: true }).click();
    await page.locator('.inspector-actions').getByRole("button", { name: "Open QBS IR", exact: true }).click();
    await page.locator('.inspector-actions').getByRole("button", { name: "Open Full Q1ASM Timeline", exact: true }).click();
    await page.locator('[data-block-id="selected"]').dblclick();
    const actions = await page.evaluate(() => window.messages);
    expect(actions.map((message) => message.type)).toEqual([
      "openProjectFile", "openScheduleFile", "openScheduleFile", "openIr", "openQ1Timeline", "openScheduleSource",
    ]);
    expect(actions.every((message) => message.renderId === renderId)).toBe(true);
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    expect(await page.evaluate(() => window.messages.at(-1))).toEqual({ type: "refresh" });
  }
});

test("keyboard selection keeps focus on QBS blocks while updating the inspector", async ({ page }) => {
  await mount(page);
  const second = page.locator('[data-block-id="second"]');
  await second.focus();
  await second.press("Enter");
  await expect(second).toBeFocused();
  await expect(second).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".inspector h2")).toHaveText("Second(q0)");
  const first = page.locator('[data-block-id="first"]');
  await first.focus();
  await first.press("Space");
  await expect(first).toBeFocused();
  await expect(first).toHaveAttribute("aria-selected", "true");
  await expect(second).toHaveAttribute("aria-selected", "false");
  await expect(page.locator(".inspector h2")).toHaveText("First(q0)");
});

test("QBS zoom controls retain focus for repeated keyboard zoom without stealing it on refresh or a new source", async ({ page }) => {
  const repo = path.resolve(__dirname, "../../..");
  const data = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from pathlib import Path", "from dataclasses import replace", "from tempfile import TemporaryDirectory",
    "from qbstimeline.project import load_project_config", "from qbstimeline.compile_worker import analyze_project",
    "with TemporaryDirectory() as output:",
    ' config=replace(load_project_config("examples/basic-transmon/qbstimeline.yml"),output_dir=Path(output))',
    " ir=analyze_project(config).ir",
    "print(json.dumps({'ir':ir,'sourceContext':{'projectFile':str(config.project_file),'scheduleFile':str(config.schedule_file)}}))",
  ].join("\n")], { cwd: repo, encoding: "utf8", timeout: 20000, env: { ...process.env, PYTHONPATH: path.join(repo, "src") } }));
  await mount(page);
  const update = (sourceContext = data.sourceContext) => page.evaluate(({ir, sourceContext}) =>
    window.dispatchEvent(new MessageEvent("message", { data: { type: "render", ir, sourceContext } })),
  { ir: data.ir, sourceContext });
  await update();
  const view = page.locator(".stage-total");
  const full = await view.textContent();
  const zoomIn = page.getByRole("button", { name: "+", exact: true });
  await zoomIn.focus();
  let previous = full;
  for (let step = 0; step < 2; step += 1) {
    await page.keyboard.press("Enter");
    await expect(zoomIn).toBeFocused();
    await expect(view).not.toHaveText(previous);
    previous = await view.textContent();
  }
  const zoomOut = page.getByRole("button", { name: "-", exact: true });
  await zoomOut.focus();
  for (let step = 0; step < 2; step += 1) {
    await page.keyboard.press("Space");
    await expect(zoomOut).toBeFocused();
    await expect(view).not.toHaveText(previous);
    previous = await view.textContent();
  }
  const fit = page.getByRole("button", { name: "Fit", exact: true });
  await fit.focus();
  await page.keyboard.press("Enter");
  await expect(fit).toBeFocused();
  await expect(view).toHaveText(full);
  const refresh = page.getByRole("button", { name: "Refresh", exact: true });
  await refresh.focus();
  await update();
  await expect(refresh).toBeFocused();
  await zoomIn.focus();
  await update({ projectFile: path.join(repo, "examples/schedule-only-inference/qbstimeline.yml"),
    scheduleFile: path.join(repo, "examples/schedule-only-inference/schedule.py") });
  await expect(page.locator("body")).toBeFocused();
});

test("QBS target lane disclosures retain keyboard focus without changing selection or scrolling", async ({ page }, testInfo) => {
  const repo = path.resolve(__dirname, "../../..");
  const data = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from pathlib import Path", "from tempfile import TemporaryDirectory",
    "from qbstimeline.project import load_project_config", "from qbstimeline.compile_worker import analyze_project",
    "source = '''class Compiler:", " def compile(self,schedule): return schedule",
    "def build_compiler(): return Compiler()", "def build_schedule():",
    " operations={f'op-{i}':{'name':f'Pulse(q{i})','duration':40e-9,'pulse_info':[{'name':'SquarePulse','port':f'q{i}:mw','clock':f'q{i}.01','t0':0,'duration':40e-9}]} for i in range(80)}",
    " return {'name':'Many target lanes','operations':operations,'schedulables':{f'operation-{i}':{'operation_id':f'op-{i}','abs_time':0} for i in range(80)}}", "'''",
    "with TemporaryDirectory() as folder:", " root=Path(folder)", " (root/'schedule.py').write_text(source)",
    " (root/'qbstimeline.yml').write_text('{schedule: {file: schedule.py}}')",
    " config=load_project_config(root/'qbstimeline.yml')", " ir=analyze_project(config).ir",
    " print(json.dumps({'ir':ir,'sourceContext':{'projectFile':str(config.project_file),'scheduleFile':str(config.schedule_file)}}))",
  ].join("\n")], { cwd: repo, encoding: "utf8", timeout: 20000, env: { ...process.env, PYTHONPATH: path.join(repo, "src") } }));
  const ts = require("typescript");
  const parser = {};
  new Function("exports", ts.transpileModule(require("node:fs").readFileSync(path.resolve(__dirname, "../../src/qbs/qbsIr.ts"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText)(parser);
  data.ir = parser.parseQbsIrText(JSON.stringify(data.ir));
  expect(data.ir.ir_diagnostics).toEqual([]);
  await mount(page);
  const update = () => page.evaluate((data) => window.dispatchEvent(new MessageEvent("message", { data: { type: "render", ...data } })), data);
  await update();
  const selected = page.locator('[data-block-id="operation-60"]');
  await selected.scrollIntoViewIfNeeded();
  await selected.click();
  const stage = page.locator(".timeline-stage");
  const scrollTop = await stage.evaluate((node) => node.scrollTop);
  const view = await page.locator(".stage-total").textContent();
  expect(scrollTop).toBeGreaterThan(0);
  const row = page.locator(".timeline-row").filter({ has: page.getByText("Schedule / q60", { exact: true }) });
  const toggle = row.locator(".lane-toggle");
  await toggle.focus();
  for (const [key, expanded] of [["Enter", "true"], ["Space", "false"]]) {
    await page.keyboard.press(key);
    await expect(toggle).toHaveAttribute("aria-expanded", expanded);
    await expect(toggle).toBeFocused();
    await expect(selected).toHaveAttribute("aria-selected", "true");
    await expect(page.locator(".inspector h2")).toHaveText("Pulse(q60)");
    await expect(page.locator(".stage-total")).toHaveText(view);
    expect(await stage.evaluate((node) => node.scrollTop)).toBe(scrollTop);
  }
  await toggle.click();
  await expect(toggle).toBeFocused();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await page.screenshot({ path: testInfo.outputPath("target-lane-focus.png") });
  await selected.dblclick();
  expect(await page.evaluate(() => window.messages.filter((message) => message.type === "openScheduleSource"))).toEqual([
    { type: "openScheduleSource", schedulableId: "op-60", operationId: "op-60", blockId: "operation-60" },
  ]);
  const refresh = page.getByRole("button", { name: "Refresh", exact: true });
  await refresh.focus();
  await update();
  await expect(refresh).toBeFocused();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(selected).toHaveAttribute("aria-selected", "true");
  expect(await stage.evaluate((node) => node.scrollTop)).toBe(scrollTop);
  await toggle.focus();
  await update();
  await expect(toggle).toBeFocused();
  expect(await stage.evaluate((node) => node.scrollTop)).toBe(scrollTop);
  await page.evaluate((data) => window.dispatchEvent(new MessageEvent("message", { data: {
    type: "render", ir: data.ir, sourceContext: { ...data.sourceContext, scheduleFile: `${data.sourceContext.scheduleFile}.other.py` },
  } })), data);
  await expect(page.locator("body")).toBeFocused();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
});

test("QBS inline Q1 lane disclosures retain keyboard focus and survive same-source renders", async ({ page }) => {
  const repo = path.resolve(__dirname, "../../..");
  const data = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from pathlib import Path", "from dataclasses import replace", "from tempfile import TemporaryDirectory",
    "from qbstimeline.project import load_project_config", "from qbstimeline.compile_worker import analyze_project",
    "with TemporaryDirectory() as output:",
    ' config=replace(load_project_config("examples/basic-transmon/qbstimeline.yml"),output_dir=Path(output))',
    " ir=analyze_project(config).ir",
    "print(json.dumps({'ir':ir,'sourceContext':{'projectFile':str(config.project_file),'scheduleFile':str(config.schedule_file)}}))",
  ].join("\n")], { cwd: repo, encoding: "utf8", timeout: 20000, env: { ...process.env, PYTHONPATH: path.join(repo, "src") } }));
  const ts = require("typescript");
  const parser = {};
  new Function("exports", ts.transpileModule(require("node:fs").readFileSync(path.resolve(__dirname, "../../src/qbs/qbsIr.ts"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText)(parser);
  data.ir = parser.parseQbsIrText(JSON.stringify(data.ir));
  expect(data.ir.ir_diagnostics.filter((item) => item.severity === "error")).toEqual([]);
  await mount(page);
  const update = () => page.evaluate((data) => window.dispatchEvent(new MessageEvent("message", { data: { type: "render", ...data } })), data);
  await update();
  const fullView = await page.locator(".stage-total").textContent();
  await page.getByTitle("Expand target lanes", { exact: true }).click();
  const selected = page.locator('[data-block-id="x180"]');
  await selected.click();
  const inline = page.locator(".inline-q1-lane-toggle");
  await expect(inline).toBeVisible();
  await inline.focus();
  for (const [key, expanded] of [["Enter", "true"], ["Space", "false"]]) {
    await page.keyboard.press(key);
    await expect(inline).toHaveAttribute("aria-expanded", expanded);
    await expect(inline).toBeFocused();
  }
  await inline.click();
  await expect(inline).toHaveAttribute("aria-expanded", "true");
  await expect(inline).toBeFocused();
  for (const toggle of [page.locator(".lane-toggle"), inline]) {
    await toggle.focus();
    await update();
    await expect(toggle).toBeFocused();
    await expect(selected).toHaveAttribute("aria-selected", "true");
    await expect(page.locator(".inspector h2")).toHaveText("X(q0)");
    await expect(page.locator(".stage-total")).toHaveText(fullView);
  }
  const refresh = page.getByRole("button", { name: "Refresh", exact: true });
  await refresh.focus();
  await update();
  await expect(refresh).toBeFocused();
  await inline.focus();
  await page.evaluate((data) => window.dispatchEvent(new MessageEvent("message", { data: {
    type: "render", ir: data.ir, sourceContext: { ...data.sourceContext, scheduleFile: `${data.sourceContext.scheduleFile}.other.py` },
  } })), data);
  await expect(page.locator("body")).toBeFocused();
  await expect(inline).toHaveCount(0);
});

for (const layer of ["operation", "pulse"]) {
  test(`QBS keeps a final zero-duration ${layer} visible and selectable at the schedule boundary`, async ({ page }) => {
    const repo = path.resolve(__dirname, "../../..");
    const data = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
      "import json", "from pathlib import Path", "from tempfile import TemporaryDirectory",
      "from qbstimeline.project import load_project_config", "from qbstimeline.compile_worker import analyze_project",
      "source = '''class Compiler:",
      " def compile(self, schedule): return schedule",
      "def build_schedule():",
      " def pulse(name, duration): return {'name':name,'duration':duration,'pulse_info':[{'name':name,'port':'q0:mw','clock':'q0.01','t0':0,'duration':duration}]}",
      " return {'name':'Final clock phase','schedulables':{'warmup':{'operation_id':'warmup','abs_time':0},'drive':{'operation_id':'drive','abs_time':20e-9},'phase':{'operation_id':'phase','abs_time':40e-9}},'operations':{'warmup':pulse('X(q0)',20e-9),'drive':pulse('Y(q0)',20e-9),'phase':pulse('ShiftClockPhase(q0)',0)}}",
      "def build_compiler(): return Compiler()",
      "'''",
      "with TemporaryDirectory() as folder:",
      " root=Path(folder)", " (root/'schedule.py').write_text(source)",
      " (root/'qbstimeline.yml').write_text('{schedule: {file: schedule.py}}')",
      " config=load_project_config(root/'qbstimeline.yml')", " ir=analyze_project(config).ir",
      " print(json.dumps({'ir':ir,'sourceContext':{'projectFile':str(config.project_file),'scheduleFile':str(config.schedule_file)}}))",
    ].join("\n")], { cwd: repo, encoding: "utf8", timeout: 20000, env: { ...process.env, PYTHONPATH: path.join(repo, "src") } }));
    expect(data.ir.ir_diagnostics).toEqual([]);
    const phasePulse = data.ir.symbolic_pulses.find((pulse) => pulse.schedulable_id === "phase");
    expect(phasePulse.duration).toBe(0);
    const targetId = layer === "operation" ? "phase" : phasePulse.id;
    await mount(page);
    const update = () => page.evaluate((data) => window.dispatchEvent(new MessageEvent("message", { data: { type: "render", ...data } })), data);
    await update();
    if (layer === "pulse") await page.getByTitle("Expand target lanes", { exact: true }).click();
    const target = page.locator(`[data-block-id="${targetId}"]`);
    await expect(target).toBeVisible();
    await expect(target).toHaveAttribute("title", /40 ns \| 0 ns/);
    const fullView = await page.locator(".stage-total").textContent();
    expect(fullView).toContain("total 40 ns");
    const clippedIds = await page.evaluate(({ ir, sourceContext }) => window.qbsTimelineModel.buildTimelineModel(ir, null, sourceContext, {
      viewport: { start: 0, end: 20e-9 }, expandedGroups: ["target:q0"],
    }).lanes.flatMap((lane) => lane.blocks.map((block) => block.id)), data);
    expect(clippedIds).not.toContain("drive");
    expect(clippedIds).not.toContain(data.ir.symbolic_pulses.find((pulse) => pulse.schedulable_id === "drive").id);
    const ruler = await page.locator(".ruler").boundingBox();
    await page.keyboard.down("Shift");
    await page.mouse.move(ruler.x + ruler.width * 0.65, ruler.y + ruler.height / 2);
    await page.mouse.down();
    await page.mouse.move(ruler.x + ruler.width * 1.1, ruler.y + ruler.height / 2, { steps: 5 });
    await page.mouse.up();
    await page.keyboard.up("Shift");
    await page.getByRole("button", { name: "Zoom selection", exact: true }).click();
    await expect(page.locator(".stage-total")).not.toHaveText(fullView);
    await expect(target).toBeVisible();
    await expect(page.locator('[data-block-id="warmup"]')).toHaveCount(0);
    await target.click();
    await expect(target).toHaveAttribute("aria-selected", "true");
    await expect(page.locator(".inspector h2")).toContainText("ShiftClockPhase");
    await target.dblclick();
    expect(await page.evaluate(() => window.messages.filter((message) => message.type === "openScheduleSource"))).toEqual([
      { type: "openScheduleSource", schedulableId: "phase", operationId: "phase", blockId: targetId },
    ]);
    await update();
    await expect(target).toBeVisible();
    await expect(target).toHaveAttribute("aria-selected", "true");
    await page.getByRole("button", { name: "Fit", exact: true }).click();
    await expect(page.locator(".stage-total")).toHaveText(fullView);
    await expect(target).toBeVisible();
  });
}

test("QBS refresh clips timed inline Q1 events to the shrinking viewport and recovers on Fit", async ({ page }) => {
  const repo = path.resolve(__dirname, "../../..");
  const data = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from pathlib import Path", "from tempfile import TemporaryDirectory", "from dataclasses import replace",
    "from qbstimeline.project import load_project_config", "from qbstimeline.compile_worker import analyze_project", "from q1timeline.analysis.api import analyze_text",
    "source = '''from pathlib import Path", "from qbstimeline.provenance import ProvenanceRecorder",
    "class Compiler:", " def compile(self,schedule):",
    "  short=Path(__file__).with_name('short').exists()",
    "  lines=['wait_sync 4','play 0,0,4','wait 16','stop'] if short else ['wait_sync 4','wait 76','play 0,0,4','wait 16','wait 60','play 0,0,4','wait 36','stop']",
    "  provenance=ProvenanceRecorder()",
    "  for name,line in ([('target',2)] if short else [('target',3),('tail',6)]): provenance.record_emission(source_id='pulse:'+name+':pulse:0',source_kind='pulse',schedulable_id=name,sequencer_id='seq',q1asm_line_start=line,q1asm_line_end=line,instruction_roles=['play'])",
    "  return {**schedule,'compiled_instructions':{'sequencers':{'seq':{'sequence':{'program':chr(10).join(lines)+chr(10)}}}},'qbstimeline_provenance':provenance}",
    "def build_schedule():", " short=Path(__file__).with_name('short').exists()",
    " def pulse(name,duration): return {'name':name,'duration':duration,'pulse_info':[{'name':'SquarePulse','port':'q0:mw','clock':'q0.01','t0':0,'duration':duration}]}",
    " return {'name':'Shrinking total','schedulables':{'target':{'operation_id':'target-op','abs_time':0 if short else 80e-9},**({} if short else {'tail':{'operation_id':'tail-op','abs_time':160e-9}})},'operations':{'target-op':pulse('Target(q0)',20e-9),'tail-op':pulse('Tail(q0)',40e-9)}}",
    "def build_compiler(): return Compiler()", "'''",
    "with TemporaryDirectory() as folder:", " root=Path(folder)", " (root/'schedule.py').write_text(source)",
    " (root/'qbstimeline.yml').write_text('{schedule: {file: schedule.py}}')",
    " config=load_project_config(root/'qbstimeline.yml')", " first=analyze_project(config).ir", " (root/'short').touch()",
    " short_config=replace(config,output_dir=root/'short-output')", " second=analyze_project(short_config).ir",
    " for ir in [first,second]: ir['q1timeline_ir']=analyze_text(ir['q1asm_by_sequencer']['seq'],file='q1asm/seq.q1asm',sequencer_id='seq')",
    " print(json.dumps({'first':first,'second':second,'firstOutput':str(config.output_dir),'secondOutput':str(short_config.output_dir),'sourceContext':{'projectFile':str(config.project_file),'scheduleFile':str(config.schedule_file)}}))",
  ].join("\n")], { cwd: repo, encoding: "utf8", timeout: 20000, env: { ...process.env, PYTHONPATH: path.join(repo, "src") } }));
  const ts = require("typescript");
  const parser = {};
  new Function("exports", ts.transpileModule(require("node:fs").readFileSync(path.resolve(__dirname, "../../src/qbs/qbsIr.ts"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText)(parser);
  data.first = parser.parseQbsIrText(JSON.stringify(data.first));
  data.second = parser.parseQbsIrText(JSON.stringify(data.second));
  expect(data.first.ir_diagnostics).toEqual([]);
  expect(data.second.ir_diagnostics).toEqual([]);
  await mount(page);
  const update = (ir, renderId, outputDir) => page.evaluate((data) => window.dispatchEvent(new MessageEvent("message", { data: { type: "render", ...data } })), {
    ir, renderId, sourceContext: { ...data.sourceContext, outputDir },
  });
  const zoomRange = async (start, end) => {
    const ruler = await page.locator(".ruler").boundingBox();
    await page.keyboard.down("Shift");
    await page.mouse.move(ruler.x + ruler.width * start, ruler.y + ruler.height / 2);
    await page.mouse.down();
    await page.mouse.move(ruler.x + ruler.width * end, ruler.y + ruler.height / 2, { steps: 5 });
    await page.mouse.up();
    await page.keyboard.up("Shift");
    await page.getByRole("button", { name: "Zoom selection", exact: true }).click();
  };
  const pulse = page.locator('[data-block-id="pulse:target:pulse:0"]');
  const inline = page.locator('.timeline-row-q1 [data-block-id^="q1:pulse:target:pulse:0"]');
  await update(data.first, 1, data.firstOutput);
  await page.getByTitle("Expand target lanes", { exact: true }).click();
  await page.getByRole("button", { name: "Show seq", exact: true }).click();
  await pulse.click();
  await zoomRange(.35, .6);
  await expect(inline).toHaveCount(2);
  await update(data.second, 2, data.secondOutput);
  await expect(page.locator(".stage-total")).toContainText("view 19.8 ns - 20 ns");
  await expect(pulse).toHaveAttribute("aria-selected", "true");
  await expect(inline).toHaveCount(0);
  await page.getByRole("button", { name: "Fit", exact: true }).click();
  await expect(inline).toHaveCount(2);
  expect(await inline.first().evaluate((element) => ({ left: element.style.left, width: element.style.width }))).toEqual({ left: "20%", width: "20%" });
  await zoomRange(.25, .35);
  await expect(inline).toHaveCount(2);
  for (const node of await inline.all()) {
    expect(await node.evaluate((element) => ({ left: element.style.left, width: element.style.width }))).toEqual({ left: "0%", width: "100%" });
    await expect(node).toHaveAttribute("title", /4 ns \| 4 ns/);
  }
  await expect(pulse).toHaveAttribute("aria-selected", "true");
  await inline.first().dblclick();
  await page.getByRole("button", { name: "Open Full Q1ASM Timeline", exact: true }).click();
  expect(await page.evaluate(() => window.messages.filter((message) => message.type === "openQ1AsmSource" || message.type === "openQ1Timeline"))).toEqual([
    { type: "openQ1AsmSource", sequencer: "seq", line: 2, renderId: 2 },
    { type: "openQ1Timeline", blockId: "pulse:target:pulse:0", operationId: "target-op", renderId: 2 },
  ]);
  await expect(page.locator(".source-files")).toContainText("short-output");
  await page.getByRole("button", { name: "Fit", exact: true }).click();
  await expect(page.locator(".stage-total")).toHaveText("view 0 ns - 20 ns (20 ns) / total 20 ns");
  await expect(inline).toHaveCount(2);
});

test("QBS zoom keeps an offscreen selection and its source action until that occurrence is removed", async ({ page }) => {
  await mount(page);
  await page.locator('[data-block-id="first"]').click();
  await expect(page.locator(".inspector h2")).toHaveText("First(q0)");
  const ruler = await page.locator(".ruler").boundingBox();
  expect(ruler).not.toBeNull();
  await page.keyboard.down("Shift");
  await page.mouse.move(ruler.x + ruler.width * 0.65, ruler.y + ruler.height / 2);
  await page.mouse.down();
  await page.mouse.move(ruler.x + ruler.width * 0.95, ruler.y + ruler.height / 2, { steps: 5 });
  await page.mouse.up();
  await page.keyboard.up("Shift");
  await page.getByRole("button", { name: "Zoom selection", exact: true }).click();

  await expect(page.locator('[data-block-id="first"]')).toHaveCount(0);
  await expect(page.locator('[data-block-id="second"]')).toHaveCount(1);
  await expect(page.locator('[data-block-id="second"]')).toHaveAttribute("aria-selected", "false");
  await expect(page.locator(".inspector h2")).toHaveText("First(q0)");
  await page.getByRole("button", { name: "Open Full Q1ASM Timeline", exact: true }).click();
  expect(await page.evaluate(() => window.messages.filter((message) => message.type === "openQ1Timeline"))).toEqual([
    { type: "openQ1Timeline", operationId: "pulse-op", blockId: "first" },
  ]);
  await page.getByRole("button", { name: "Fit", exact: true }).click();
  await expect(page.locator('[data-block-id="first"]')).toHaveAttribute("aria-selected", "true");
  await page.evaluate(() => window.dispatchEvent(new MessageEvent("message", { data: {
    type: "render", sourceContext: { projectFile: "/project/qbstimeline.yml", scheduleFile: "/project/schedule.py" },
    ir: { schedule: { name: "Repeated operations" }, operations: [
      { id: "second", operation_id: "pulse-op", schedulable_id: "second", label: "Second(q0)", abs_time: 50e-9, duration: 40e-9 },
    ] },
  } })));
  await expect(page.locator(".inspector h2")).toHaveText("No selection");
  await expect(page.getByRole("button", { name: "Open Full Q1ASM Timeline", exact: true })).toHaveCount(0);
  await expect(page.locator('[data-block-id="second"]')).toHaveAttribute("aria-selected", "false");
  await page.locator('[data-block-id="second"]').click();
  await expect(page.locator(".inspector h2")).toHaveText("Second(q0)");
});

test("QBS refresh does not reinterpret a removed occurrence as a shared operation selection", async ({ page }) => {
  const repo = path.resolve(__dirname, "../../..");
  const data = JSON.parse(execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c", [
    "import json", "from pathlib import Path", "from tempfile import TemporaryDirectory",
    "from qbstimeline.project import load_project_config", "from qbstimeline.compile_worker import analyze_project",
    "source = '''from pathlib import Path",
    "class Compiler:", " def compile(self, schedule): return schedule",
    "def build_schedule():",
    " items={'shared':{'operation_id':'shared','abs_time':0},'later':{'operation_id':'shared','abs_time':50e-9}}",
    " if Path(__file__).with_name('remove-first').exists(): del items['shared']",
    " return {'name':'Repeated source refresh','schedulables':items,'operations':{'shared':{'name':'Pulse(q0)','duration':40e-9,'pulse_info':[{'name':'SquarePulse','port':'q0:mw','clock':'q0.01','t0':0,'duration':40e-9}]}}}",
    "def build_compiler(): return Compiler()", "'''",
    "with TemporaryDirectory() as folder:", " root=Path(folder)", " (root/'schedule.py').write_text(source)",
    " (root/'qbstimeline.yml').write_text('{schedule: {file: schedule.py}}')",
    " config=load_project_config(root/'qbstimeline.yml')", " first=analyze_project(config).ir",
    " (root/'remove-first').touch()", " second=analyze_project(config).ir",
    " print(json.dumps({'first':first,'second':second,'sourceContext':{'projectFile':str(config.project_file),'scheduleFile':str(config.schedule_file)}}))",
  ].join("\n")], { cwd: repo, encoding: "utf8", timeout: 20000, env: { ...process.env, PYTHONPATH: path.join(repo, "src") } }));
  expect(data.first.ir_diagnostics).toEqual([]);
  expect(data.second.ir_diagnostics).toEqual([]);
  await mount(page);
  const update = (ir, selection = {}) => page.evaluate(({ ir, sourceContext, selection }) => window.dispatchEvent(new MessageEvent("message", {
    data: { type: "render", ir, sourceContext, ...selection },
  })), { ir, sourceContext: data.sourceContext, selection });
  await update(data.first);
  await page.locator('[data-block-id="shared"]').click();
  await page.getByTitle("Expand target lanes", { exact: true }).click();
  const ruler = await page.locator(".ruler").boundingBox();
  await page.keyboard.down("Shift");
  await page.mouse.move(ruler.x + ruler.width * 0.65, ruler.y + ruler.height / 2);
  await page.mouse.down();
  await page.mouse.move(ruler.x + ruler.width * 0.95, ruler.y + ruler.height / 2, { steps: 5 });
  await page.mouse.up();
  await page.keyboard.up("Shift");
  await page.getByRole("button", { name: "Zoom selection", exact: true }).click();
  await expect(page.locator('[data-block-id="shared"]')).toHaveCount(0);
  await expect(page.locator('.inspector-rows dt:has-text("Start") + dd')).toHaveText("0 ns");
  const windowBefore = await page.locator(".stage-total").textContent();
  await update(data.first);
  await expect(page.locator('.inspector-rows dt:has-text("Start") + dd')).toHaveText("0 ns");
  await expect(page.locator(".stage-total")).toHaveText(windowBefore);
  await expect(page.locator(".timeline-row-pulse")).toHaveCount(1);
  await update(data.second);
  await expect(page.locator(".inspector h2")).toHaveText("No selection");
  await expect(page.locator('[data-block-id][aria-selected="true"]')).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Open Full Q1ASM Timeline", exact: true })).toHaveCount(0);
  await expect(page.locator(".stage-total")).toHaveText(windowBefore);
  await expect(page.locator(".timeline-row-pulse")).toHaveCount(1);
  await page.getByRole("button", { name: "+", exact: true }).click();
  await expect(page.locator(".inspector h2")).toHaveText("No selection");
  await update(data.second, { selectedOperationId: "shared" });
  await expect(page.locator('[data-block-id="later"]')).toHaveAttribute("aria-selected", "true");
  await expect(page.locator('.inspector-rows dt:has-text("Start") + dd')).toHaveText("50 ns");
  await page.getByRole("button", { name: "-", exact: true }).click();
  await update(data.first);
  const later = page.locator('[data-block-id="later"]');
  await expect(later).toHaveAttribute("aria-selected", "true");
  await expect(page.locator('.inspector-rows dt:has-text("Start") + dd')).toHaveText("50 ns");
  await page.getByRole("button", { name: "Open Full Q1ASM Timeline", exact: true }).click();
  await later.dblclick();
  expect(await page.evaluate(() => window.messages.filter((message) => message.type === "openQ1Timeline" || message.type === "openScheduleSource"))).toEqual([
    { type: "openQ1Timeline", operationId: "shared", blockId: "later" },
    { type: "openScheduleSource", schedulableId: "shared", operationId: "shared", blockId: "later" },
  ]);
});

test("QBS inspector tabs keep keyboard focus and lane scrolling after selection", async ({ page }) => {
  await mount(page);
  await page.evaluate(() => {
    window.tabsQbsIr = { schedule: { name: "Many targets" },
      operations: Array.from({ length: 80 }, (_, index) => ({
        id: `operation-${index}`, label: `Pulse(q${index})`, abs_time: 0, duration: 40e-9,
      })),
    };
    window.refreshTabsIr = () => window.dispatchEvent(new MessageEvent("message", { data: {
      type: "render", ir: window.tabsQbsIr,
      sourceContext: { projectFile: "/project/qbstimeline.yml", scheduleFile: "/project/schedule.py" },
    } }));
    window.refreshTabsIr();
  });
  const selected = page.locator('[data-block-id="operation-60"]');
  await selected.scrollIntoViewIfNeeded();
  await selected.click();
  const stage = page.locator(".timeline-stage");
  const scrollTop = await stage.evaluate((node) => node.scrollTop);
  expect(scrollTop).toBeGreaterThan(0);

  const lowering = page.getByRole("button", { name: "Lowering", exact: true });
  const preview = page.getByRole("button", { name: "Q1ASM Preview", exact: true });
  await lowering.focus();
  await lowering.press("Enter");
  await expect(lowering).toBeFocused();
  await expect(lowering).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Tab");
  await expect(preview).toBeFocused();
  await preview.press("Space");
  await expect(preview).toBeFocused();
  await expect(preview).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Shift+Tab");
  await expect(lowering).toBeFocused();
  const summary = page.getByRole("button", { name: "Summary", exact: true });
  await summary.click();
  await expect(summary).toBeFocused();
  await expect(summary).toHaveAttribute("aria-selected", "true");
  expect(await stage.evaluate((node) => node.scrollTop)).toBe(scrollTop);
  await expect(page.locator(".inspector h2")).toHaveText("Pulse(q60)");

  const refresh = page.getByRole("button", { name: "Refresh", exact: true });
  await refresh.focus();
  await page.evaluate(() => window.refreshTabsIr());
  await expect(refresh).toBeFocused();
  expect(await stage.evaluate((node) => node.scrollTop)).toBe(scrollTop);
});

test("QBS lane scrolling survives selection and same-source refresh but resets for a new source", async ({ page }) => {
  await mount(page);
  await page.evaluate(() => {
    window.largeQbsIr = {
      schedule: { name: "Many targets" },
      operations: Array.from({ length: 80 }, (_, index) => ({
        id: `operation-${index}`, label: `Pulse(q${index})`, abs_time: 0, duration: 40e-9,
      })),
    };
    window.dispatchEvent(new MessageEvent("message", { data: {
      type: "render", ir: window.largeQbsIr,
      sourceContext: { projectFile: "/project/qbstimeline.yml", scheduleFile: "/project/schedule.py" },
    } }));
  });
  const selected = page.locator('[data-block-id="operation-60"]');
  await selected.scrollIntoViewIfNeeded();
  const stage = page.locator(".timeline-stage");
  const scrollTop = await stage.evaluate((node) => node.scrollTop);
  expect(scrollTop).toBeGreaterThan(0);
  await selected.click();
  await expect(page.locator(".inspector h2")).toHaveText("Pulse(q60)");
  expect(await stage.evaluate((node) => node.scrollTop)).toBe(scrollTop);
  for (const project of ["project", "other-project"]) {
    await page.evaluate((project) => window.dispatchEvent(new MessageEvent("message", { data: {
      type: "render", ir: window.largeQbsIr,
      sourceContext: { projectFile: `/${project}/qbstimeline.yml`, scheduleFile: `/${project}/schedule.py` },
    } })), project);
    expect(await stage.evaluate((node) => node.scrollTop)).toBe(project === "project" ? scrollTop : 0);
  }
});
