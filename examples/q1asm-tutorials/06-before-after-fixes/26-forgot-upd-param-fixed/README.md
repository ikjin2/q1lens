# Forgot `upd_param` Fixed

This before/after example fixes `05-common-mistakes/25-forgot-upd-param` by committing
each staged gain, frequency, and phase change before the following `play`.

Inspect:

- `latched_state_pending` events before each commit
- each `upd_param` packet
- the following `play` events with applied-state metadata

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 06-before-after-fixes/26-forgot-upd-param-fixed/q1timeline.yml
```
