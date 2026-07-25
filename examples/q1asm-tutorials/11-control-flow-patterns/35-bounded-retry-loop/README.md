# Bounded Retry Loop

This example models a retry window that exits early when feedback reaches an
accepted code, otherwise it burns a bounded number of attempts. The point is not
to model a specific experiment, but to expose how retry logic appears in the
timeline.

Inspect:

- the `loop` branch region
- the early-exit branch driven by feedback
- marker state pending before the accepted-path `upd_param`

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 11-control-flow-patterns/35-bounded-retry-loop/q1timeline.yml
```
