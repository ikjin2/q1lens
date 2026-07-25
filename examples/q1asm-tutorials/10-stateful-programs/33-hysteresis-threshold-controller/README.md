# Hysteresis Threshold Controller

This example shows a small register-state controller that reacts to a feedback
value without turning into a full active-reset recipe. The useful view is the
branch region around the high and low thresholds, plus the pending latched gain
change before the selected action.

Inspect:

- unresolved threshold branches driven by `fb_pop_data`
- `latched_state_pending` events before `upd_param`
- the difference between the hold, high, and low paths

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 10-stateful-programs/33-hysteresis-threshold-controller/q1timeline.yml
```
