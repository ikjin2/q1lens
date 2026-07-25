# Round-Robin Bin Writer

This compact Q1ASM example keeps the acquisition bin index in a register. Each loop iteration acquires into `$BIN`, increments it, and wraps back to zero after `BIN_COUNT`.

q1timeline resolves the first iteration and records the register-derived bin operand in the acquire event metadata.

Run:

```powershell
python -m q1timeline analyze --project 10-stateful-programs/32-round-robin-bin-writer/q1timeline.yml --out tmp/round-robin-bin-writer-ir.json
```
