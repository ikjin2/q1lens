# Branch Housekeeping Underflow

This q1timeline project shows branch and classical housekeeping pressure after a
sync point. The analyzer assumes the unresolved branch falls through, then the
queued `play` packet has no slack under the local timing assumptions.

Run:

```powershell
python -m q1timeline analyze --project 07-timing-pathologies/28-branch-housekeeping-underflow/q1timeline.yml --out tmp/branch-housekeeping-ir.json --diagnostics tmp/branch-housekeeping-diag.json
```

Expected diagnostic categories include:

- `unresolved_branch`
- `possible_underflow`
