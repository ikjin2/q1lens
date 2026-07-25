# Atomic Parameter Window

This example shows a small latched-state update window. Gain, frequency, and
phase are staged with Q1ASM latch instructions, then committed together by the
next `upd_param` packet before the following `play`.

Inspect:

- `latched_state_pending` events for gain, frequency, and phase
- the `upd_param` packet that makes the staged values visible to real time
- the next `play` event carrying the applied-state metadata

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 09-latched-state-patterns/30-atomic-parameter-window/q1timeline.yml
```
