# Register Broadcast

This q1timeline project shows a minimal cross-sequencer feedback transfer.

The producer computes a value in `R0` and sends it on LINQ feedback channel 16 with `fb_com_data`. The consumer waits long enough for the channel-16 multicast latency budget, then receives the value into `R1` with `fb_pop_data`.

Expected analyzer signals:

- one `feedback_flows` entry from `producer` to `consumer`
- one `feedback_pop` event on the consumer sequencer
- no `feedback_latency_violation` diagnostic

Run:

```powershell
python -m q1timeline analyze --project 12-feedback-patterns/37-register-broadcast/q1timeline.yml --out tmp/register-broadcast-ir.json --diagnostics tmp/register-broadcast-diag.json
```
