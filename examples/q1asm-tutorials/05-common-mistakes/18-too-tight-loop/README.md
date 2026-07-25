# Too-Tight Loop

This common-mistake example puts a real-time `play` packet in a compact loop
without enough Q1 issue slack. The program is intentionally tiny so the timeline
focuses on the missing wait budget rather than on experiment logic.

Inspect:

- the `definite_underflow` diagnostic on the loop body `play`
- the `loop_truncated` preview diagnostic
- the debug slack lane before the RT packet

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 05-common-mistakes/18-too-tight-loop/q1timeline.yml
```
