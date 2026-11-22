const { test, expect } = require("./helpers");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

test("Hardware port lanes use the summed preview and navigate to a contributing RT event", async ({ page }, info) => {
  const repo = path.resolve(__dirname, "../../..");
  const file = info.outputPath("output.json");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", [
    "-m", "q1timeline", "analyze", "--project", "examples/symbolic-output/q1timeline.yml",
    "--no-render", "--out", file,
  ], { cwd: repo, env: { ...process.env, PYTHONPATH: path.join(repo, "src") } });
  const ir = JSON.parse(fs.readFileSync(file, "utf8"));
  await page.route("**/ir/01-digital.json", (route) => route.fulfill({ json: ir }));
  await page.goto("/?case=01-digital");
  const lanes = page.locator(".hardware-port-lane");
  await expect(lanes).toHaveCount(3);
  const mix = lanes.filter({ hasText: "Mix · out0" });
  await expect(mix.locator("header")).toContainText("drive.I");
  await expect(mix.locator("header")).toContainText("boost.Q");
  await expect(mix.getByRole("img")).toHaveAttribute("aria-label", /0 to 24 ns/);
  await expect(mix.locator("polyline")).toHaveAttribute("points", /4,2 /);
  const ramp = lanes.filter({ hasText: "Pulse · out1" }).locator("details");
  await ramp.locator("summary").click();
  await expect(ramp.locator("code")).toContainText("(4096 + 4096 * #L0)/32768");
  const drive = mix.locator("details").filter({ hasText: "drive · path I" });
  await drive.locator("summary").click();
  const interval = drive.getByRole("button", { name: "[4, 24) ns", exact: true });
  const id = await interval.getAttribute("data-event");
  await interval.click();
  expect(await page.evaluate(() => window.selectedEventId)).toBe(id);
  expect(ir.events.find((event) => event.id === id).source.file).toMatch(/drive\.q1asm$/);
  await expect(page.getByRole("spinbutton")).toHaveValue("4");
});

for (const aligned of [true, false]) {
  test(`Counterexample replay shows the ${aligned ? "negative aligned" : "post-stop waveform"} sample`, async ({ page }, info) => {
    const repo = path.resolve(__dirname, "../../..");
    const dir = info.outputPath("project");
    fs.mkdirSync(dir, { recursive: true });
    const seqs = aligned ? ["drive", "peer"] : ["drive"];
    for (const name of seqs) {
      const program = aligned
        ? `;@assert no_clip\nwait ${name === "drive" ? 16 : 4}\nset_awg_gain 24576,0\nplay 0,0,4\nwait_sync 4\nstop`
        : ";@assert peak(out0) <= .25\nplay 0,0,4\nstop";
      fs.writeFileSync(path.join(dir, name + ".q1asm"), program);
    }
    const data = aligned ? [1, 1, 1, 1] : [...Array(32).fill(0), 0.5];
    fs.writeFileSync(path.join(dir, "wave.json"), JSON.stringify({ waveforms: { pulse: { index: 0, data } } }));
    fs.writeFileSync(path.join(dir, "q1timeline.yml"), JSON.stringify({
      sequencers: seqs.map((id) => ({ id, file: id + ".q1asm", sequence_json: "wave.json" })),
      alignment: { mode: aligned ? "first_wait_sync" : "none" },
      modules: { card: { type: "QCM", channel_map: Object.fromEntries(seqs.map((id) => [id, { out0: "I" }])) } },
    }));
    const report = execFileSync(process.env.Q1LENS_BROWSER_PYTHON || "python", ["-c",
      "import json;from q1timeline.verification import verify_file;print(json.dumps(verify_file('drive.q1asm',project='q1timeline.yml',include_replay=True)['checks'][0]))",
    ], { cwd: dir, env: { ...process.env, PYTHONPATH: path.join(repo, "src") }, encoding: "utf8" });
    const check = JSON.parse(report);
    const time = check.counterexample.time_ns;
    if (aligned) expect(time).toBeLessThan(0);
    else expect(time).toBe(32);
    await page.setContent('<div id="finding"></div><div id="inputs"></div><div id="timeline-root"></div><div id="occurrence"></div>' +
      '<button id="source"></button><button id="focus"></button><button id="full"></button><div id="hardware"></div><script type="application/json" id="witness"></script>');
    await page.evaluate((finding) => {
      window.acquireVsCodeApi = () => ({ postMessage() {} });
      document.getElementById("witness").textContent = JSON.stringify(finding);
    }, check);
    for (const asset of ["shared/timeline/instructionColors", "shared/timeline/renderer", "q1timeline/media/timelineAdapter",
        "q1timeline/media/hardwareState", "q1timeline/media/hardwareView", "q1timeline/media/verificationReplay"]) {
      await page.addScriptTag({ path: path.join(repo, "vscode-extension/src", asset + ".js") });
    }
    for (const button of ["focus", "full"]) {
      await page.locator("#" + button).click();
      await expect(page.getByRole("spinbutton")).toHaveValue(String(time));
      await expect(page.locator("#occurrence")).toContainText(time + " ns");
      const ticks = await page.locator(".ruler .tick").allTextContents();
      expect(parseInt(ticks[0], 10)).toBeLessThanOrEqual(time);
      expect(parseInt(ticks.at(-1), 10)).toBeGreaterThan(time);
    }
  });
}
