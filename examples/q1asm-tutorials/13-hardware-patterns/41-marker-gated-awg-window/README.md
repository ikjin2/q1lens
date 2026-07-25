# Marker Gated AWG Window

This hardware-adjacent example pairs a marker-gated AWG window with a delayed
QRM acquisition window. It stays at the timing-inspection level: use it to see
where marker changes are latched and where the acquire sits relative to the AWG
window.

Inspect:

- marker high and marker low `marker_state` events
- `latched_state_pending` and `latched_state_applied` around `upd_param`
- overlap between the QCM play window and QRM acquire window

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 13-hardware-patterns/41-marker-gated-awg-window/q1timeline.yml
```
