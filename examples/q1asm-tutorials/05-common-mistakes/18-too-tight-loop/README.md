# Too-Tight Loop

This common-mistake example puts a real-time `play` packet in a compact loop
without enough Q1 issue slack. Its 8192 iterations each include 64 Q1 `nop`
instructions after a 4 ns `play`. The sustained supply shortage exceeds both the
assumed 1 ms initial buffer and the tentative 2 ms scenario, so an underflow warning
remains visible. These buffer values are model assumptions, not measurements.

Inspect:

- the `definite_underflow` diagnostic on the loop body `play`
- the `loop_truncated` preview diagnostic
- the debug slack lane before the RT packet

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 05-common-mistakes/18-too-tight-loop/q1timeline.yml
```
