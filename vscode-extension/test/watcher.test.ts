import assert from "node:assert/strict";
import { DebouncedRefreshQueue } from "../src/qbs/watcher";

describe("DebouncedRefreshQueue", () => {
  it("queues one rerun while a refresh is active", async () => {
    const calls: string[] = [];
    const queue = new DebouncedRefreshQueue(1, async () => {
      calls.push("run");
      if (calls.length === 1) {
        queue.request();
      }
    });

    queue.request();
    await new Promise((resolve) => setTimeout(resolve, 30));

    assert.deepEqual(calls, ["run", "run"]);
  });

  it("lets an active refresh finish but suppresses its queued rerun after disposal", async () => {
    const calls: string[] = [];
    let finishActive!: () => void;
    const active = new Promise<void>((resolve) => {
      finishActive = resolve;
    });
    const queue = new DebouncedRefreshQueue(1, async () => {
      calls.push("run");
      await active;
    });

    queue.request();
    await new Promise((resolve) => setTimeout(resolve, 10));
    queue.request();
    queue.dispose();
    finishActive();
    await new Promise((resolve) => setTimeout(resolve, 20));
    queue.request();
    await new Promise((resolve) => setTimeout(resolve, 10));

    assert.deepEqual(calls, ["run"]);
  });
});
