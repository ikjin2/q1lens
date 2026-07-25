# Latency Violation

This q1timeline project intentionally receives feedback too early.

The producer sends a register value on LINQ feedback channel 16 with `fb_com_data`. The consumer waits only 80 ns after the shared `wait_sync` anchor, then pops channel 16 before the channel-16 multicast latency budget has elapsed.

Expected analyzer signals:

- one `feedback_flows` entry from `producer` to `consumer`
- one `feedback_pop` event on the consumer sequencer
- one `feedback_latency_violation` diagnostic

Run:

```powershell
python -m q1timeline analyze --project 12-feedback-patterns/38-latency-violation/q1timeline.yml --out tmp/latency-violation-ir.json --diagnostics tmp/latency-violation-diag.json
```
