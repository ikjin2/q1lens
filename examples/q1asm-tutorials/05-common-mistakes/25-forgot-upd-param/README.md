# Forgot `upd_param`

This common-mistake example stages gain, frequency, and phase changes, then
plays once before committing the latched state with `upd_param`. The second
`play` follows the commit so the timeline shows the contrast.

Inspect:

- `latched_state_pending` events before the first `play`
- the `upd_param` event that applies the staged state
- the second `play` event carrying applied-state metadata

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 05-common-mistakes/25-forgot-upd-param/q1timeline.yml
```
