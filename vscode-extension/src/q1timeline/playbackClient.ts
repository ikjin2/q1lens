// @ts-nocheck
const childProcess = require("node:child_process");

const MAX_RESPONSE_BYTES = 50 * 1024 * 1024;

class PlaybackClient {
  constructor(command, args, options = {}) {
    this.pending = new Map();
    this.nextId = 1;
    this.buffer = "";
    this.failure = undefined;
    try {
      this.child = (options.spawn || childProcess.spawn)(command, args, {
        cwd: options.cwd,
        env: { ...process.env, ...(options.env || {}) },
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      });
      this.child.on("error", (error) => this.fail(error));
      this.child.on("close", (code, signal) => this.fail(new Error(
        `Playback process exited (${signal || code}).`
      )));
      this.child.stdin.on("error", (error) => this.fail(error));
      this.child.stdout.on("error", (error) => this.fail(error));
      this.child.stderr.on("error", (error) => this.fail(error));
      this.child.stdout.setEncoding("utf8");
      this.child.stderr.setEncoding("utf8");
      this.child.stdout.on("data", (chunk) => this.receive(chunk));
      this.child.stderr.on("data", (chunk) => options.onStderr?.(chunk));
    } catch (error) {
      this.fail(error);
    }
  }

  request(payload, timeoutMs = 30000) {
    if (this.failure) return Promise.reject(this.failure);
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
      return Promise.reject(new Error("Playback request timeout must be positive."));
    }
    const id = this.nextId++;
    let line;
    try {
      line = JSON.stringify({ ...payload, id }) + "\n";
    } catch (error) {
      return Promise.reject(error);
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.fail(new Error(
        `Playback request ${id} timed out after ${timeoutMs} ms.`
      )), timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.child.stdin.write(line, "utf8", (error) => {
          if (error) this.fail(error);
        });
      } catch (error) {
        this.fail(error);
      }
    });
  }

  receive(chunk) {
    if (this.failure) return;
    this.buffer += chunk;
    let end;
    while ((end = this.buffer.indexOf("\n")) !== -1) {
      const line = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + 1);
      if (Buffer.byteLength(line, "utf8") > MAX_RESPONSE_BYTES) {
        this.fail(new Error("Playback response exceeded the 50 MiB limit."));
        return;
      }
      let response;
      try {
        response = JSON.parse(line);
      } catch {
        this.fail(new Error("Invalid JSON from playback process."));
        return;
      }
      const pending = response && !Array.isArray(response) && this.pending.get(response.id);
      if (!pending) {
        this.fail(new Error("Unexpected playback response ID."));
        return;
      }
      this.pending.delete(response.id);
      clearTimeout(pending.timer);
      if (response.error !== undefined) {
        pending.reject(new Error(typeof response.error === "string"
          ? response.error : response.error?.message || "Playback request failed."));
      } else {
        pending.resolve(response);
      }
    }
    if (Buffer.byteLength(this.buffer, "utf8") > MAX_RESPONSE_BYTES) {
      this.fail(new Error("Playback response exceeded the 50 MiB limit."));
    }
  }

  fail(error) {
    if (this.failure) return;
    this.failure = error;
    this.buffer = "";
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
    try {
      this.child?.stdin.destroy();
      if (this.child && !this.child.killed && this.child.exitCode == null && this.child.signalCode == null) {
        this.child.kill();
      }
    } catch {
      // Disposal must remain safe if the process has already exited.
    }
  }

  dispose() {
    this.fail(new Error("Playback client disposed."));
  }
}

export { PlaybackClient };
