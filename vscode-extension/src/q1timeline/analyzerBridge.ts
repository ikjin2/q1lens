// @ts-nocheck
class AnalyzerBridge {
  analyze() {
    throw new Error("AnalyzerBridge.analyze must be implemented by a concrete bridge");
  }
}

class AnalyzerRunToken {
  constructor(requestId) {
    this.requestId = requestId;
    this.cancelled = false;
  }

  get isCancellationRequested() {
    return this.cancelled;
  }

  cancel() {
    this.cancelled = true;
  }

  isStale(currentRequestId) {
    return Number(this.requestId) < Number(currentRequestId);
  }

  throwIfStale(currentRequestId) {
    if (this.isStale(currentRequestId)) {
      throw new Error("Analyzer run is stale");
    }
  }

  throwIfCancelled() {
    if (this.isCancellationRequested) {
      throw new Error("Analyzer run cancelled");
    }
  }
}

class SubprocessAnalyzerBridge extends AnalyzerBridge {
  constructor(options = {}) {
    super();
    this.execFile = options.execFile;
  }

  async analyze(request, token = {}) {
    if (!this.execFile) {
      throw new Error("SubprocessAnalyzerBridge requires an execFile function");
    }
    this.throwIfCancelled(token);
    const args = this.analyzeArgs(request);
    const result = await this.execFile(request.pythonPath || "python", args, {
      cwd: request.cwd,
      timeout: request.timeoutMs,
      env: { ...process.env, ...(request.env || {}) },
    });
    this.throwIfCancelled(token);
    return typeof result.stdout === "string" ? JSON.parse(result.stdout) : result;
  }

  analyzeArgs(request) {
    const args = [
      "-m",
      "q1lens",
      "q1timeline",
      "analyze",
      "--project",
      request.projectFile,
      "--format",
      "vscode-json",
    ];
    if (request.includeTimelineIr) {
      args.push("--include-timeline-ir");
    }
    if (request.includeDiagnostics) {
      args.push("--include-diagnostics");
    }
    if (request.includeSourceMap) {
      args.push("--include-source-map");
    }
    if (request.summaryOnly) {
      args.push("--summary-only");
    }
    if (request.mode) {
      args.push("--mode", request.mode);
    }
    return args.concat(Array.isArray(request.extraArgs) ? request.extraArgs : []);
  }

  throwIfCancelled(token) {
    if (token && typeof token.throwIfCancelled === "function") {
      token.throwIfCancelled();
      return;
    }
    if (token.cancelled || token.isCancellationRequested) {
      throw new Error("Analyzer run cancelled");
    }
  }
}

export {
  AnalyzerBridge,
  AnalyzerRunToken,
  SubprocessAnalyzerBridge,
};
