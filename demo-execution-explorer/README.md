# Execution Explorer demo

Historical prototype. The retained reference is
[Execution Flow](../demo-execution-flow/README.md), which uses the Q1Lens engine.
This older prototype's separate interpreter is not a product implementation.

Local browser prototype for comparing loop iterations and tracing register values
back to their actual writer occurrences, operands, branch flags and synthetic
feedback inputs. The same renderer and trace format drive all three examples.
Loops come from backward branch targets; register aliases come from `.DEF`.
The source and parameters are read from this checkout when launching.

From the repository root:

```powershell
python demo-execution-explorer/launch.py
```

The launcher writes untracked files to `output/execution-explorer` and opens a
separate browser window. No extension installation, server or network is needed.
Use `--build-only` to generate the files without opening a window.

Select a loop, then a register cell or changed-value button. Each event has its
own execution ID even if the source line runs repeatedly. The comparison defaults
to the preceding iteration's exit snapshot; the first iteration uses its entry
snapshot. You can also select another iteration as the baseline. Source highlights
follow the selected writer and its data dependencies. The inspector separately
links the last preceding conditional branch; that link is execution context,
not a complete control-dependence proof. Jump and Continue remain separate rows.

The input editor replaces the sequential `fb_pop_data` input stream and reruns
the program. Golden-section values are arbitrary synthetic I/Q words that
alternate comparisons, not a model of resonator physics or measured data. Missing
inputs stop execution instead of supplying zero. The inner `wait_loop` can be
selected independently using the same iteration projection.

This is a bounded sequential register interpreter for these examples, separate
from Q1Lens analysis/simulation. It supports the instruction subset exercised by
the examples, 32-bit wrapping, signed 16-bit multiplication, signed 32-bit product
splitting, and relevant ALU branches. It does not model Q1 pipeline availability,
RT timing/queues, feedback transport, acquisition or analog hardware. Unsupported
instructions and unknown operands stop execution. It is not a hardware validator.

Verify the trace/provenance behavior with:

```powershell
node --test demo-execution-explorer/trace.test.js
```
