# Concrete register execution

Open this `q1timeline.yml` with Q1Lens and refresh the preview. The project
selects concrete execution with a 1,000-instruction limit. A VS Code execution
mode override takes precedence over the project setting.

From the repository root:

```powershell
python -m q1lens q1timeline analyze --project examples/concrete-register-execution/q1timeline.yml --out output/concrete-register-execution.json --no-render
```

In **Module QCM0 → counter → Registers**, select R1 and use its previous/next
change controls. Its recorded values should be **0 → 1 → 2 → 3**. R0 counts
down to 0, and the final copy sets R2 to 3. The trace executes 19 instructions,
including all three loop iterations. Each executed instruction has its own
event ID even when it comes from the same source line.

The execution trace records operand reads, register write evidence and ALU
flags. Registers that have not been initialized remain unknown. Missing
inputs or unsupported operations stop concrete execution with an explanation;
the instruction limit produces a partial trace.

This example uses the existing analytical Q1/RT timing model. Sequencers run
independently; `wait_sync` uses a local timing origin. Queue backpressure,
external feedback and physical output signals are not simulated. No
q1simulator dependency is used.
