# Execution Flow demo

This is the retained design reference. See the [accepted interactions and
extension integration sequence](../docs/execution-exploration.md).

A standalone design prototype for following one Q1ASM execution occurrence from
its actual read operands to its result and next instruction. It uses the Python
Q1Lens simulation engine in this checkout; the browser does not interpret Q1ASM.

Build from the repository root:

```powershell
python demo-execution-flow/build.py --build-only
```

Open `output/execution-flow/index.html` in a separate browser window. All assets
are local. The prototype does not install or modify the VS Code extension.
The compact layout follows the extension's dark Hardware UI palette and 12px
typography, with independent source and inspector scrolling. Input values and
the source rows below them both link to the exact writer occurrence.

Click a loop marker (`↻ @name`) or its backward branch in the source to open
the first five iterations automatically. The bottom table stays visible while
the instruction detail scrolls. It includes registers and latched settings that
change inside the selected loop; constant fields are omitted. Use `5회 더 보기`
to reveal more iterations. Inner loops can be selected separately.

Each cell retains the ordered changes within its iteration, including temporary
values such as `2 → 3 → 0`. Click a value to inspect its actual writer, or a row
to inspect that iteration's branch. Register changes use their availability
time. Latched columns show setting updates, distinct from application by RT.
Changing a branch rebuilds the table from the new engine run.

To explore alternate branches, run the local engine server:

```powershell
python demo-execution-flow/serve.py
```

Open `http://127.0.0.1:8741` in a separate browser window. The static HTML keeps
read-only navigation; branch changes require this local Python process.
Click the other Jump or Continue row to rerun the same scenario with that one
branch occurrence overridden. The condition result and read values remain
unchanged at that occurrence; the selected path, timing and subsequent values
come from a new engine run. The amber indication identifies a hypothetical run.
Follow the next-instruction button, or restore the original execution with
`원래 실행`. Choosing an earlier branch discards later branch choices.

## What to inspect

- Move between executed instructions and compare the exact values read by each
  occurrence. A source line that runs twice is still two separate executions.
- For a branch, inspect the predicate and the distinct Jump and Continue
  destinations. Only the taken route is an executed path.
- Follow an operand to the instruction that produced its value, then return to
  the original instruction. The source highlights are linked to these events.
- Switch programs and input scenarios. The same rendering and navigation logic
  is used for all examples, with no algorithm-specific diagrams or formulas.
- In `Latched sweep`, select `@sweep`: gain and loop registers vary, while the
  constant frequency setting and zero register are omitted automatically.
- Inspect partial runs. Missing inputs and unsupported instructions remain
  explicit; the UI does not invent register values or a completed path.
- Try the first retry branch: `0 >= 1` remains false even when you choose Jump.
  Then try the third bin branch: choosing Jump keeps bin 3, and execution stops
  when that path needs an acquisition input not provided by the scenario.

## Evidence boundary

These are simulator results, not measured hardware traces. Scenario input data,
RT startup delay and other simulator assumptions remain attached to the run.
Register operands are captured when the engine issues each instruction. A
computed write may become readable later; the UI shows its availability time.
Q1 issue order and RT execution are independent. An RT failure can occur after
Q1 has already issued later source lines, so the failure source and last issued
source instruction need not match.

No arbitrary Q1ASM completeness or hardware compatibility is claimed. In
particular, unsupported Golden-section instructions must remain a visible
boundary of the real engine rather than being supplied by a demo-only emulator.
