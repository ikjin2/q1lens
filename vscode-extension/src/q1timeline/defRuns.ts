// @ts-nocheck
import { randomUUID } from "node:crypto";
import { PlaybackClient } from "./playbackClient";
import { prependPythonArgs } from "./invocation";

export class DefRuns {
  constructor(owner) {
    this.owner = owner;
    this.state = {};
  }

  publish(patch = this.state) {
    if (!Object.keys(patch).length) return;
    Object.assign(this.state, patch);
    this.owner.postWebviewMessage({ type: "defRunState", project: this.owner.projectUri?.fsPath, patch });
  }

  stopWorker() {
    const client = this.client;
    this.client = undefined;
    client?.dispose();
  }

  invalidate(clear = false) {
    this.job?.abort.abort();
    this.job = undefined;
    this.stopWorker();
    if (clear) {
      this.state = {};
    } else if (Object.keys(this.state).length) {
      const message = "Source or project settings changed. Reload DEFs before starting a new run.";
      this.publish({ info: null, busy: null, message,
        sweep: this.state.sweep && { ...this.state.sweep, status: "stale",
          incompleteBatch: this.state.sweep.incompleteBatch || this.state.sweep.status === "running" },
        trace: this.state.trace && { ...this.state.trace, stale: true } });
    }
  }

  options() {
    const config = this.owner.config();
    const args = ["-u", "-m", "q1timeline.def_runs", "--project", this.owner.projectUri.fsPath];
    // Unlike playback, this action uses the explicit setting and never a replay override.
    const mode = config.get("executionMode", "project");
    if (mode !== "project") args.push("--execution-mode", mode, "--max-steps", String(config.get("maxSteps", 10000)));
    if (config.get("hardwareReadiness", false)) args.push("--hardware-readiness");
    const configuredArgs = config.get("analyzer.extraArgs", []);
    const extra = Array.isArray(configuredArgs) ? configuredArgs.filter((arg) => typeof arg === "string") : [];
    for (let i = 0; i < extra.length; i += 1) {
      const arg = extra[i];
      if (arg === "--q1asm-strict" || arg.startsWith("--params=") || arg.startsWith("--set-param=")) args.push(arg);
      else if (["--params", "--set-param"].includes(arg) && i + 1 < extra.length) args.push(arg, extra[++i]);
    }
    for (const [name, value] of this.owner.parameterOverrides) args.push("--set-param", `${name}=${value}`);
    return { args, cwd: this.owner.projectRootCwd(), env: this.owner.normaliseAnalyzerEnv(config.get("analyzer.env", {})),
      timeout: config.get("analyzer.timeoutMs", 30000) };
  }

  async handle(message) {
    const owner = this.owner;
    if (!owner.panel || !owner.webviewReady || !owner.projectUri || message.project !== owner.projectUri.fsPath) return;
    if (message.action === "cancel") {
      if (this.job?.action === "sweep") {
        this.job.cancelled = true;
        this.publish({ message: "Stopping after the current batch…" });
      }
      return;
    }
    if (this.job) return;
    if (owner.hasUnsavedProjectChanges()) {
      this.publish({ message: "Save the project and Q1ASM sources before calculating execution." });
      return;
    }
    const options = this.options();
    const key = JSON.stringify(options);
    if (this.optionsKey && this.optionsKey !== key) this.invalidate();
    if (message.action === "plan") {
      const client = this.client;
      const revision = this.state.info?.revision;
      if (!client || !revision || message.revision !== revision) return;
      let answer;
      try {
        answer = await client.request({ ...message, command: "plan" }, options.timeout);
      } catch (error) {
        answer = { error: error.message };
      }
      if (this.client === client && this.state.info?.revision === revision) {
        owner.postWebviewMessage({ type: "defSweepPlan", project: message.project, token: message.token,
          plan: answer.plan, error: answer.error });
      }
      return;
    }
    const sweep = this.state.sweep;
    let expected;
    if (message.action === "case") {
      expected = sweep?.id === message.sweepId && sweep.rows.find((row) => row.id === message.caseId);
      if (!expected || sweep.status === "stale" || sweep.revision !== this.state.info?.revision) {
        this.publish({ message: "This sweep snapshot is no longer available. Start a new sweep to inspect its cases." });
        return;
      }
    } else if (message.action !== "info" && message.revision !== this.state.info?.revision) {
      this.publish({ message: "Reload DEFs before starting a new run." });
      return;
    }
    const job = { id: randomUUID(), action: message.action, abort: new AbortController(), cancelled: false };
    const revision = owner.sourceRevision;
    const session = owner.projectSessionId;
    const current = () => this.job === job && owner.sourceRevision === revision && owner.projectSessionId === session;
    this.job = job;
    this.optionsKey = key;
    this.publish({ busy: job.action, message: "" });
    let workingSweep;
    try {
      if (!this.client || this.client.failure) {
        const python = await owner.defRunPython(options, job.abort.signal);
        if (!current()) return;
        if (!python) throw new Error("Select a Python environment and complete setup before calculating execution.");
        this.client = new PlaybackClient(python.pythonPath, prependPythonArgs(python.pythonArgs, options.args), {
          cwd: options.cwd, env: options.env, label: "DEF execution",
          onStderr: (text) => owner.output.appendLine(`DEF execution: ${text}`),
        });
      }
      const request = (payload) => this.client.request(payload, options.timeout);
      if (job.action === "info") {
        const { info } = await request({ command: "info" });
        if (!current()) return;
        if ((sweep && sweep.revision !== info.revision) || (this.state.trace && this.state.trace.revision !== info.revision)) {
          this.publish({ sweep: sweep && { ...sweep, status: "stale" },
            trace: this.state.trace && { ...this.state.trace, stale: true } });
        }
        this.publish({ info });
        return;
      }
      const inputs = expected ? { ...sweep.inputs, overrides: expected.defs, case_id: expected.id } : {
        revision: message.revision, sequencer: message.sequencer, uri: message.uri,
        registers: message.registers, observe_time: message.observe_time, overrides: message.overrides,
      };
      const metadata = { id: job.id, revision: inputs.revision, sequencer: inputs.sequencer, uri: inputs.uri,
        registers: inputs.registers, observeTime: inputs.observe_time, mode: this.state.info.mode, maxSteps: this.state.info.max_steps };
      if (job.action === "sweep") {
        const statisticsOnly = message.statistics_only === true;
        const { plan, defs } = await request({ ...inputs, command: "sweep", axes: message.axes, statistics_only: statisticsOnly });
        if (!current()) return;
        workingSweep = { ...metadata, inputs, plan, defs, statisticsOnly, status: "running", rows: [] };
        this.publish({ sweep: { ...workingSweep, rows: [] } });
        this.state.sweep = workingSweep;
        const reported = () => workingSweep.summary?.count ?? workingSweep.rows.length;
        const total = Number(plan.total);
        while (!job.cancelled && reported() < total) {
          const { rows, summary } = await request({ command: "batch" });
          if (!current()) return;
          if (statisticsOnly) {
            if (!Number.isSafeInteger(summary?.count) || summary.count <= reported() || summary.count > total) {
              throw new Error("The statistics worker stopped before reporting all planned cases.");
            }
            workingSweep.summary = summary;
            owner.postWebviewMessage({ type: "defSweepSummary", project: message.project, sweepId: job.id, summary });
          } else {
            if (!rows?.length) throw new Error("The sweep stopped before reporting all planned cases.");
            workingSweep.rows.push(...rows);
            owner.postWebviewMessage({ type: "defSweepRows", project: message.project, sweepId: job.id, rows });
          }
        }
        workingSweep.status = reported() === total ? "completed" : "cancelled";
        this.publish({ sweep: workingSweep, message: "" });
      } else {
        const result = await request({ ...inputs, command: "run" });
        if (!current()) return;
        if (expected && (JSON.stringify(result.case.words) !== JSON.stringify(expected.words) || result.case.q1_ns !== expected.q1_ns ||
            result.case.status !== expected.status || JSON.stringify(result.case.defs) !== JSON.stringify(expected.defs))) {
          const describe = (row) => [row.status, ...Object.entries(row.words).map(([name, word]) => `${name}=${word ?? "—"}`),
            ...(inputs.observe_time ? [`Q1 time ${row.q1_ns ?? "—"} ns`] : [])].join(", ");
          throw new Error(`Case ${expected.id} differs from its sweep result. Expected ${describe(expected)}. Received ${describe(result.case)}.`);
        }
        this.publish({ trace: { ...metadata, ...result, literals: inputs.overrides, sweepId: expected ? sweep.id : null } });
      }
    } catch (error) {
      if (!current()) return;
      if (workingSweep) {
        workingSweep.status = "error";
        workingSweep.incompleteBatch = true;
      }
      if (this.client?.failure) this.stopWorker();
      owner.output.appendLine(`DEF execution failed: ${error.message}`);
      this.publish({ message: error.message, ...(workingSweep ? { sweep: workingSweep } : {}) });
    } finally {
      if (current()) {
        this.job = undefined;
        this.publish({ busy: null });
      }
    }
  }
}
