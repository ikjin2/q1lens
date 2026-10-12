import * as PIXI from "pixi.js";
// This Pixi fallback avoids generated functions under our strict CSP.
import "pixi.js/unsafe-eval";

window.PIXI = PIXI;

const byId = (id) => document.getElementById(id);
const host = window.q1lensDesktop;
let currentProject = null;
let busy = false;
let hasTimeline = false;

function showError(error) {
  byId("error-message").textContent = String(error.message || error).replace(/^Error invoking remote method '[^']+': (Error: )?/, "");
  byId("error-banner").hidden = false;
  byId("run-status").textContent = "ERROR";
  byId("run-status").dataset.status = "error";
  byId("load-status").textContent = hasTimeline ? "Action failed · Previous timeline retained" : "Open a project or select a Python environment to get started";
}

function setBusy(value, label = "Analyzing project") {
  busy = value;
  for (const id of ["open-project", "open-demo", "select-python"]) byId(id).disabled = value;
  byId("reanalyze").disabled = value || !currentProject;
  byId("loading-overlay").hidden = !value;
  byId("loading-label").textContent = label;
  byId("playback-toggle").disabled = value || !hasTimeline;
  byId("timeline-scrubber").disabled = value || !hasTimeline;
  if (value) {
    byId("error-banner").hidden = true;
    byId("run-status").textContent = "ANALYZING";
    byId("run-status").dataset.status = "busy";
    byId("load-status").textContent = label + "…";
  } else if (byId("error-banner").hidden) {
    byId("run-status").textContent = hasTimeline ? "READY" : "EMPTY";
    byId("run-status").dataset.status = "ready";
  }
}

function updateProject(project) {
  currentProject = project.projectPath;
  byId("project-name").textContent = project.projectName || "Untitled project";
  byId("project-name").title = project.projectPath || "";
  byId("project-path").textContent = project.projectPath || "No project selected";
  byId("python-label").textContent = project.pythonPath?.split(/[\\/]/).pop() || "Python";
  byId("select-python").title = `Python environment: ${project.pythonPath || "auto"}. Click to change.`;
  document.title = `${project.projectName || "Q1Lens"} · Q1Lens`;
}

function updateDiagnostics(ir) {
  const notes = Array.isArray(ir.diagnostics) ? ir.diagnostics : [];
  byId("diagnostic-count").textContent = String(notes.length);
  const summary = byId("diagnostic-summary");
  summary.replaceChildren();
  if (!notes.length) {
    summary.textContent = "No analysis notes for this sequence.";
    return;
  }
  const counts = new Map();
  for (const note of notes) {
    const severity = note.severity || "info";
    counts.set(severity, (counts.get(severity) || 0) + 1);
  }
  summary.append([...counts].map(([severity, count]) => `${count} ${severity}`).join(" · "));
  const seen = new Set();
  for (const note of notes) {
    const message = String(note.message || note.code || note.kind || "Analysis note");
    if (seen.has(message)) continue;
    seen.add(message);
    const paragraph = document.createElement("p");
    paragraph.textContent = message;
    summary.append(paragraph);
    if (seen.size === 3) break;
  }
  if (notes.length > seen.size) {
    const remaining = document.createElement("p");
    remaining.textContent = "Select timeline warning markers to inspect individual findings.";
    summary.append(remaining);
  }
}

async function runAction(action, label) {
  if (busy) return;
  setBusy(true, label);
  try {
    const project = await action();
    if (!project) {
      byId("load-status").textContent = hasTimeline ? "Ready · Project unchanged" : "Choose a project to get started";
      return;
    }
    if (project.ir) {
      await window.q1lensTimeline.load(project.ir, { name: project.projectName });
      hasTimeline = true;
      updateDiagnostics(project.ir);
    }
    updateProject(project);
    if (!project.ir) byId("load-status").textContent = "Python environment updated · Reanalyze to refresh the timeline";
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

window.addEventListener("q1lens:state", ({ detail }) => {
  if (detail.fullTimeExtent) {
    const duration = detail.fullTimeExtent.maxNs - detail.fullTimeExtent.minNs;
    byId("time-extent").textContent = duration >= 1000 ? `${(duration / 1000).toFixed(2)} µs span` : `${Math.round(duration)} ns span`;
  }
});
window.addEventListener("q1lens:selection", ({ detail }) => {
  byId("selection-empty").hidden = Boolean(detail);
  byId("inspector-details").hidden = !detail;
  if (!detail) byId("selection-title").textContent = "Explore a signal";
});
byId("dismiss-error").addEventListener("click", () => {
  byId("error-banner").hidden = true;
  setBusy(false);
});

async function start() {
  if (!host) throw new Error("Start this workspace through Electron with npm start.");
  await import("../../demo-pixi-standalone/pixi-standalone-demo.js");
  await window.q1lensTimeline.ready;
  byId("open-project").addEventListener("click", () => runAction(() => host.openProject(), "Opening project"));
  byId("open-demo").addEventListener("click", () => runAction(() => host.openDemo(), "Loading Three Peak demo"));
  byId("reanalyze").addEventListener("click", () => runAction(() => host.analyze(), "Analyzing project"));
  byId("select-python").addEventListener("click", () => runAction(() => host.selectPython(), "Selecting Python environment"));
  updateProject(await host.getState());
  await runAction(() => host.openDemo(), "Loading Three Peak demo");
}

start().catch((error) => {
  showError(error);
  setBusy(false);
});
