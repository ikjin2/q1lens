# Retune Without Editing Q1ASM

This params-basics example stages drive state from `params.json`, commits it
with `upd_param`, and plays a pulse. Users can retune the example by editing
only JSON values.

Inspect:

- gain, frequency, and phase placeholders in `drive.q1asm`
- `latched_state_pending` events before `upd_param`
- the following `play` event with applied-state metadata

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 03-params-basics/12-retune-without-editing-q1asm/q1timeline.yml
```
