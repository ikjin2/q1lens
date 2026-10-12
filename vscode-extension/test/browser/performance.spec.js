const { test, expect } = require("./helpers");
const { performanceFixture } = require("./performance-fixture");

for (const sequencers of [1, 6, 24]) {
  test(`10k total events / ${sequencers} sequencers: synchronous cursor p95 <= 100ms`, async ({ page }, info) => {
    await page.goto("/?case=01-digital");
    await page.waitForFunction(() => !!window.hardwarePreview);
    const result = await page.evaluate(async ({ ir, horizon }) => {
      const container = document.getElementById("hardware");
      window.hardwarePreview.destroy();
      const start = performance.now();
      const preview = window.q1lensHardwareView.mount(container, ir, { initialState: { level: "detail", sequencerId: "seq0" } });
      container.getBoundingClientRect();
      const mountMs = performance.now() - start;
      const views = {};
      for (const tab of ["registers", "parameters", "bins", "simulation"]) {
        container.querySelector(`[data-tab="${tab}"]`).click();
        const input = container.querySelector('[data-control="time"]');
        const timings = [];
        for (let n = 0; n < 120; n++) {
          const time = Math.floor(((n * 7919) % 10007) / 10007 * horizon);
          const before = performance.now();
          input.value = String(time);
          input.dispatchEvent(new Event("change", { bubbles: true }));
          container.getBoundingClientRect();
          if (n >= 20) timings.push(performance.now() - before);
        }
        timings.sort((a, b) => a - b);
        views[tab] = { samples: timings.length, p50_ms: timings[49], p95_ms: timings[94], max_ms: timings[99] };
        await new Promise((resolve) => requestAnimationFrame(resolve));
      }
      const result = { views, mount_ms: mountMs, dom_nodes: container.querySelectorAll("*").length,
        actual_events: ir.events.length, sequencers: ir.sequencers.length, browser: navigator.userAgent,
        measurement: "20 warmups + 100 fixed-sequence cursor changes per view. Synchronous DOM event and forced layout only; excludes IPC, requestAnimationFrame scheduling and GPU paint. Shared CI host noise can affect timings; not an end-to-end interaction latency guarantee." };
      preview.destroy();
      return result;
    }, performanceFixture(sequencers));
    await info.attach("cursor-performance", { body: JSON.stringify(result, null, 2), contentType: "application/json" });
    expect(result.actual_events).toBe(10000);
    expect(result.sequencers).toBe(sequencers);
    for (const [view, timing] of Object.entries(result.views)) {
      expect(timing.samples).toBe(100);
      expect(timing.p95_ms, `${view}: ${JSON.stringify(timing)}`).toBeLessThanOrEqual(100);
    }
  });
}
