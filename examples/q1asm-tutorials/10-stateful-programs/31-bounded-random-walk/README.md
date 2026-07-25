# Bounded Random Walk

This compact Q1ASM example keeps a walker position in a register, computes a tiny seed/update value, then lets a runtime feedback pop replace that value before the signed step is applied. The seed arithmetic gives the timeline some classical setup work; the feedback pop makes the final step runtime-dependent.

The `fb_pop_data` instruction stands in for runtime entropy. Because that value is not statically known, q1timeline marks the bound checks as branch regions instead of pretending the path is deterministic.

Run:

```powershell
python -m q1timeline analyze --project 10-stateful-programs/31-bounded-random-walk/q1timeline.yml --out tmp/bounded-random-walk-ir.json
```
