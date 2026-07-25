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
});
