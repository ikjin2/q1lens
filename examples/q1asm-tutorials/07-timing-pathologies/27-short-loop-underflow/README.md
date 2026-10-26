# Short Loop Underflow

This q1timeline project is a minimal timing-pathology example. The program enters
a loop body repeated 8192 times, with 64 Q1 `nop` instructions after each 4 ns
`play`. The sustained supply shortage exceeds the assumed 1 ms initial buffer
and the tentative 2 ms scenario. Underflow analysis should then report
`definite_underflow`; the initial buffer values are not hardware measurements.

Run:

```powershell
python -m q1timeline analyze --project 07-timing-pathologies/27-short-loop-underflow/q1timeline.yml --out tmp/short-loop-ir.json --diagnostics tmp/short-loop-diag.json
```

Expected diagnostic category:

- `definite_underflow`
