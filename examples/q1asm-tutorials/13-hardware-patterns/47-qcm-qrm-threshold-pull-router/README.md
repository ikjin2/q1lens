# QCM to QRM Threshold Pull Router

This q1timeline project models a hardware feedback pattern where a QCM output is
physically patched into a QRM input. The QRM acquires the input, emits a
thresholded-bit feedback payload, pulls the next feedback FIFO entry with
`fb_pull_data`, and routes its own output based on the pulled threshold bit.

Program shape:

- `qcm_drive.q1asm` emits the input stimulus on the QCM output.
- `qrm_threshold_router.q1asm` sets threshold index 0, enables thresholded-bit
  feedback on self-cast ID 4, and acquires bin 0.
- `fb_pull_data R4,R1` stores the first pulled feedback ID in `R4` and the
  threshold payload used for routing in `R1`.
- `R1 >= 1` selects the high-output path; otherwise the QRM emits the low-output
  path.

Expected analyzer signals:

- one thresholded acquisition feedback flow from `acquire` to `fb_pull_data`
- channel 4 marked `balanced` in `feedback_balance`
- a runtime-dependent branch region at the high/low output decision
- no `feedback_latency_violation` diagnostic

Run:

```powershell
python -m q1timeline analyze --project 13-hardware-patterns/47-qcm-qrm-threshold-pull-router/q1timeline.yml --out tmp/threshold-pull-router-ir.json --diagnostics tmp/threshold-pull-router-diag.json
```
