# Short Loop Underflow

This q1timeline project is a minimal timing-pathology example. The program enters
a short loop whose first real-time `play` packet is issued too late for its RT
start time, so underflow analysis should report `definite_underflow`.

Run:

```powershell
python -m q1timeline analyze --project 07-timing-pathologies/27-short-loop-underflow/q1timeline.yml --out tmp/short-loop-ir.json --diagnostics tmp/short-loop-diag.json
```

Expected diagnostic category:

- `definite_underflow`
