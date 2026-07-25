# LINQ Throughput Simulator

This example is a q1timeline stress pattern for LINQ feedback pressure. It does
not claim a hardware throughput number. Instead, it makes feedback sends,
receives, payload capacity, route latency, and channel balance visible in one
timeline.

The four lanes model:

- channel 16: an eight-message register-data burst with only six drained pops,
  producing an `over_produced` balance state and FIFO imbalance warning
- channel 18: one deliberately early pop, producing a
  `feedback_latency_violation`
- channel 19: one acquisition-derived IQ feedback event with two payload slots,
  drained by two pops

Inspect:

- `feedback_flows` ordering across dense sends and delayed receives
- `feedback_balance.channels["16"]` for leftover payload pressure
- the channel 16 `feedback_fifo_imbalance` warning
- channel 19 `send_payloads` to see IQ pair capacity
- the channel 18 latency diagnostic source line

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 12-feedback-patterns/39-linq-throughput-simulator/q1timeline.yml
```
