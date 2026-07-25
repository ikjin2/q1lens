# Feedback Arbitration

This q1timeline project models a small feedback arbitration pattern without
adding scheduler or instrument setup.

Two clients send requests on separate multicast feedback channels. The arbiter
waits for both request windows, pops both requests, and emits only one grant on
channel 18. Both clients wait long enough for the grant latency budget, but only
the first grant pop can be matched because the arbiter produced a single
payload.

Expected analyzer signals:

- two request `feedback_flows` into `arbiter`
- one grant `feedback_flows` entry from `arbiter` to `client_a`
- channel 18 marked `under_produced` in `feedback_balance`
- a `feedback_fifo_imbalance` warning diagnostic
- no `feedback_latency_violation` diagnostic

Run:

```powershell
python -m q1timeline analyze --project 14-multi-sequencer-coordination/46-feedback-arbitration/q1timeline.yml --out tmp/feedback-arbitration-ir.json --diagnostics tmp/feedback-arbitration-diag.json
```
