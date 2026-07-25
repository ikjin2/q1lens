# QCM to QRM IQ Pull Router

This q1timeline project models a hardware feedback pattern where a QCM output is
physically patched into a QRM input. The QRM acquires the input, emits IQ-value
feedback payloads, pulls the next feedback FIFO entries with `fb_pull_data`, and
routes its own output based on the pulled I-like payload.

Program shape:

- `qcm_drive.q1asm` emits the input stimulus on the QCM output.
- `qrm_iq_router.q1asm` enables IQ acquisition feedback on self-cast ID 4 and
  acquires bin 0.
- `fb_pull_data R4,R1` stores the first pulled feedback ID in `R4` and the first
  IQ payload used for routing in `R1`.
- `fb_pull_data R5,R2` stores the next feedback ID in `R5` and the second IQ
  payload in `R2`, keeping the timeline's feedback FIFO balanced.
- `R1 >= 0` selects the high-output path; otherwise the QRM emits the low-output
  path.

Expected analyzer signals:

- two IQ acquisition feedback flows from `acquire` to `fb_pull_data`
- channel 4 marked `balanced` in `feedback_balance`
- a runtime-dependent branch region at the high/low output decision
- no `feedback_latency_violation` diagnostic

Run:

```powershell
python -m q1timeline analyze --project 13-hardware-patterns/48-qcm-qrm-iq-pull-router/q1timeline.yml --out tmp/iq-pull-router-ir.json --diagnostics tmp/iq-pull-router-diag.json
```
