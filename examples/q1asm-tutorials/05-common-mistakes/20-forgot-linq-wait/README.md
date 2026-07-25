# Forgot LINQ Wait

This common-mistake example receives LINQ feedback before the official multicast
latency budget has elapsed. The producer sends a register value on channel 16,
but the consumer pops the same channel too soon after the shared sync anchor.

Inspect:

- the `feedback_flows` entry from `producer` to `consumer`
- the consumer-side `feedback_pop` event
- the `feedback_latency_violation` diagnostic and missing wait amount

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 05-common-mistakes/20-forgot-linq-wait/q1timeline.yml
```
