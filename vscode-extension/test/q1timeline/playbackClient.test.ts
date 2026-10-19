import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough, Writable } from "node:stream";
import { PlaybackClient } from "../../src/q1timeline/playbackClient";

function fixture() {
  const child = Object.assign(new EventEmitter(), {
    stdout: new PassThrough(),
    stderr: new PassThrough(),
    stdin: new Writable({ write(chunk, _encoding, callback) { writes.push(chunk.toString()); callback(); } }),
    killed: false,
    exitCode: null as number | null,
    signalCode: null,
    killCount: 0,
    kill() { this.killed = true; this.killCount += 1; return true; },
  });
  const writes: string[] = [];
  const stderr: string[] = [];
  const launches: any[] = [];
  const client = new PlaybackClient("python", ["-u", "-m", "q1timeline.playback"], {
    cwd: "C:/project", env: { Q1LENS_PLAYBACK_TEST: "yes" },
    onStderr: (chunk: string) => stderr.push(chunk),
    spawn: (...args: any[]) => { launches.push(args); return child; },
  });
  return { client, child, writes, stderr, launches };
}

describe("persistent playback client", () => {
  it("launches without a visible window, preserves environment, and separates stderr", async () => {
    const { client, child, stderr, launches } = fixture();
    try {
      assert.equal(launches.length, 1);
      assert.deepEqual(launches[0].slice(0, 2), ["python", ["-u", "-m", "q1timeline.playback"]]);
      assert.equal(launches[0][2].cwd, "C:/project");
      assert.equal(launches[0][2].windowsHide, true);
      assert.deepEqual(launches[0][2].env, { ...process.env, Q1LENS_PLAYBACK_TEST: "yes" });
      child.stderr.write("diagnostic only\n");
      const response = client.request({ command: "snapshot" });
      child.stdout.write('{"id":1,"ir":{}}\n');
      assert.deepEqual(await response, { id: 1, ir: {} });
      assert.deepEqual(stderr, ["diagnostic only\n"]);
    } finally { client.dispose(); }
  });

  it("correlates out-of-order replies and buffers split lines and UTF-8 characters", async () => {
    const { client, child, writes } = fixture();
    try {
      const first = client.request({ command: "run", budget_ms: 100, max_steps: 1000, id: 99 });
      const second = client.request({ command: "snapshot" });
      assert.deepEqual(writes.map((line) => JSON.parse(line)), [
        { command: "run", budget_ms: 100, max_steps: 1000, id: 1 }, { command: "snapshot", id: 2 },
      ]);
      assert.ok(writes.every((line) => line.endsWith("\n")));
      const bytes = Buffer.from('{"id":2,"ir":{"label":"경로"}}\r\n{"id":1,"playback":{"status":"paused"}}\n');
      const split = bytes.indexOf(Buffer.from("경")) + 1;
      child.stdout.write(bytes.subarray(0, split));
      child.stdout.write(bytes.subarray(split, split + 2));
      child.stdout.write(bytes.subarray(split + 2));
      assert.deepEqual(await first, { id: 1, playback: { status: "paused" } });
      assert.deepEqual(await second, { id: 2, ir: { label: "경로" } });
    } finally { client.dispose(); }
  });

  it("rejects a command error without preventing the next request", async () => {
    const { client, child } = fixture();
    try {
      const rejected = assert.rejects(client.request({ command: "choose" }), /Branch not found/);
      child.stdout.write('{"id":1,"error":{"message":"Branch not found"}}\n');
      await rejected;
      const next = client.request({ command: "snapshot" });
      child.stdout.write('{"id":2,"ir":{}}\n');
      assert.deepEqual(await next, { id: 2, ir: {} });
      assert.equal(child.killCount, 0);
    } finally { client.dispose(); }
  });

  for (const [label, line, expected] of [
    ["non-JSON stdout", "debug print\n", /Invalid JSON/],
    ["unknown response", '{"id":900}\n', /Unexpected playback response ID/],
    ["missing response ID", '{"ir":{}}\n', /Unexpected playback response ID/],
  ] as const) {
    it(`fails every pending request on ${label}`, async () => {
      const { client, child } = fixture();
      const first = assert.rejects(client.request({ command: "run" }), expected);
      const second = assert.rejects(client.request({ command: "snapshot" }), expected);
      child.stdout.write(line);
      await Promise.all([first, second]);
      await assert.rejects(client.request({ command: "snapshot" }), expected);
      assert.equal(child.killCount, 1);
    });
  }

  it("bounds an unterminated response instead of retaining unlimited stdout", async () => {
    const { client, child } = fixture();
    const rejected = assert.rejects(client.request({ command: "snapshot" }), /50 MiB limit/);
    child.stdout.write("x".repeat(50 * 1024 * 1024 + 1));
    await rejected;
    assert.equal(child.killCount, 1);
  });

  it("kills the session on timeout so an unacknowledged mutation cannot continue", async () => {
    const { client, child } = fixture();
    const first = assert.rejects(client.request({ command: "run" }, 5), /timed out/);
    const second = assert.rejects(client.request({ command: "snapshot" }), /timed out/);
    await Promise.all([first, second]);
    child.stdout.write('{"id":1,"playback":{}}\n');
    await assert.rejects(client.request({ command: "run" }), /timed out/);
    assert.equal(child.killCount, 1);
  });

  it("rejects pending requests on spawn errors and pipe failures", async () => {
    for (const stream of ["process", "stdin", "stdout", "stderr"] as const) {
      const { client, child } = fixture();
      const rejected = assert.rejects(client.request({ command: "snapshot" }), /unavailable/);
      (stream === "process" ? child : child[stream]).emit("error", new Error("unavailable"));
      await rejected;
      assert.equal(child.killCount, 1);
    }
    const client = new PlaybackClient("missing", [], { spawn() { throw new Error("spawn failed"); } });
    await assert.rejects(client.request({ command: "snapshot" }), /spawn failed/);
    client.dispose();
  });

  it("reports unexpected exit without trying to kill an already exited process", async () => {
    const { client, child } = fixture();
    const rejected = assert.rejects(client.request({ command: "snapshot" }), /exited \(2\)/);
    child.exitCode = 2;
    child.emit("close", 2, null);
    await rejected;
    assert.equal(child.killCount, 0);
  });

  it("disposes once, rejects all pending work, and ignores late output", async () => {
    const { client, child } = fixture();
    const first = assert.rejects(client.request({ command: "run" }), /disposed/);
    const second = assert.rejects(client.request({ command: "snapshot" }), /disposed/);
    client.dispose();
    client.dispose();
    child.stdout.write('{"id":1,"playback":{}}\n');
    await Promise.all([first, second]);
    await assert.rejects(client.request({ command: "run" }), /disposed/);
    assert.equal(child.killCount, 1);
  });
});
