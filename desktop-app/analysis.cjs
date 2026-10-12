const { execFile } = require("node:child_process");
const { existsSync } = require("node:fs");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { promisify } = require("node:util");

const execFileAsync = promisify(execFile);
const repoRoot = path.resolve(__dirname, "..");
const demoProject = path.join(repoRoot, "examples/three-peak-demo1/q1timeline.yml");
const activeProcesses = new Set();

function stopAnalysis() {
  for (const child of activeProcesses) child.kill();
}

function detectPython(projectPath) {
  const roots = [projectPath && path.dirname(projectPath), repoRoot].filter(Boolean);
  const relative = process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python";
  return roots.map((root) => path.join(root, relative)).find(existsSync) || "python";
}

function validateIr(ir) {
  if (!ir || typeof ir !== "object" || !Array.isArray(ir.events) || !Array.isArray(ir.sequencers)) {
    throw new Error("This file is not a TimelineIR. Choose timeline_ir.json with events and sequencers arrays.");
  }
  if (ir.sequencers.some((item) => !item || typeof item.id !== "string") ||
      ir.events.some((item) => !item || typeof item.id !== "string" || typeof item.kind !== "string")) {
    throw new Error("Invalid TimelineIR: each sequencer needs an id, and each event needs an id and kind.");
  }
  if ("diagnostics" in ir && (!Array.isArray(ir.diagnostics) ||
      ir.diagnostics.some((item) => !item || typeof item !== "object" || Array.isArray(item)))) {
    throw new Error("Invalid TimelineIR: diagnostics must be an array of diagnostic records.");
  }
  return ir;
}

async function readIr(file) {
  let value;
  try {
    value = JSON.parse((await fs.readFile(file, "utf8")).replace(/^\uFEFF/, ""));
  } catch (error) {
    throw new Error(`Could not read TimelineIR: ${error.message}`);
  }
  return validateIr(value);
}

async function runPython(pythonPath, args, cwd) {
  let running;
  try {
    running = execFileAsync(pythonPath, args, {
      cwd,
      windowsHide: true,
      shell: false,
      timeout: 120_000,
      maxBuffer: 16 * 1024 * 1024,
      env: {
        ...process.env,
        PYTHONUTF8: "1",
        PYTHONDONTWRITEBYTECODE: "1",
        PYTHONPATH: [path.join(repoRoot, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
      },
    });
    activeProcesses.add(running.child);
    return await running;
  } catch (error) {
    if (error.code === "ENOENT") {
      throw new Error(`Python was not found at ${pythonPath}. Use Select Python to choose your project interpreter.`);
    }
    if (error.killed) throw new Error("Analysis exceeded two minutes. Reduce the project size and try again.");
    let detail = String(error.stderr || error.stdout || error.message).trim();
    try {
      const payload = JSON.parse(error.stdout);
      detail = payload.error?.message || payload.error || payload.diagnostics?.map((item) => item.message).join("\n") || detail;
    } catch {}
    throw new Error(`Python analysis failed: ${String(detail).slice(-4000)}`);
  } finally {
    if (running) activeProcesses.delete(running.child);
  }
}

async function analyzeProject(projectPath, pythonPath = detectPython(projectPath)) {
  if (!projectPath) throw new Error("Choose a project or open the included demo first.");
  const extension = path.extname(projectPath).toLowerCase();
  if (![".json", ".yaml", ".yml"].includes(extension)) {
    throw new Error("Choose a q1timeline/qbstimeline YAML project or a TimelineIR JSON file.");
  }
  let ir;
  if (extension === ".json") {
    ir = await readIr(projectPath);
  } else {
    const cwd = path.dirname(projectPath);
    const { stdout } = await runPython(pythonPath, ["-c", [
      "import pathlib, sys, yaml",
      "doc = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8-sig'))",
      "assert isinstance(doc, dict), 'Project YAML must contain a mapping'",
      "assert 'schedule' in doc or 'sequencers' in doc, 'Project needs schedule or sequencers'",
      "print('qbs' if 'schedule' in doc else 'q1')",
    ].join("\n"), projectPath], cwd);
    const cacheDir = await fs.mkdtemp(path.join(os.tmpdir(), "q1lens-desktop-"));
    let timelineProject = projectPath;
    if (stdout.trim() === "qbs") {
      await runPython(pythonPath, ["-m", "q1lens", "build", "--project", projectPath, "--out-dir", cacheDir], cwd);
      timelineProject = path.join(cacheDir, "q1timeline.yml");
      if (!existsSync(timelineProject)) {
        throw new Error("This schedule did not export a Q1ASM timeline. Enable low_level.q1timeline in the project YAML.");
      }
    }
    const output = path.join(cacheDir, "timeline_ir.json");
    try {
      await runPython(pythonPath, [
        "-m", "q1lens", "q1timeline", "analyze", "--project", timelineProject,
        "--out", output, "--format", "vscode-json", "--summary-only",
        "--include-diagnostics", "--no-render",
      ], cwd);
    } catch (error) {
      // Fatal instruction diagnostics can still produce a useful, complete TimelineIR.
      if (!existsSync(output)) throw error;
    }
    ir = await readIr(output);
  }
  return {
    ir,
    projectPath,
    projectName: path.basename(path.dirname(projectPath)),
    pythonPath,
    diagnostics: Array.isArray(ir.diagnostics) ? ir.diagnostics : [],
  };
}

module.exports = { analyzeProject, demoProject, detectPython, readIr, stopAnalysis, validateIr };
