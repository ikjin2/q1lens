# Read the timeline

Open a `.q1asm` file and run **Q1Lens: Open Timeline Preview**. The preview has a toolbar, the timeline, a one-line summary of the selected event, the diagnostics list and a collapsed **Details** section.

## Rows

Each sequencer has one main row. It shows the RT commands in the order and at the time the RT core executes them: `play`, `acquire`, `wait` and the settings applied with them. An **Acquisition** row under it shows integration and scope capture, which can run longer than the instruction that started them.

Switch from **Normal** to **Debug** in the toolbar to add the rows that explain timing problems:

| Row | Shows |
| --- | --- |
| **Q1 core** | When the Q1 core issued each instruction |
| **Q1 stall** | When Q1 waited for a free queue slot or for feedback |
| **RT underflow** | Where the RT core needed a command that Q1 had not issued yet |

Normal starts at 0 ns. Debug can show negative times, because Q1 prepares commands before the RT core starts.

## Markers

| Marker | Meaning |
| --- | --- |
| **RT underflow** | The queue was empty when RT needed its next command. The label gives the lateness, for example `24 ns late`. |
| **Possible RT underflow** | An underflow that depends on an input or a branch Q1Lens could not resolve |
| Arrival points | A trigger or a LINQ word reaching this sequencer |
| **Rejected LINQ arrival** | A word that arrived when the receive queue was full |
| Lines between rows | Feedback sent from one sequencer and received by another |

Errors are marked on the affected command. Warnings stay in the diagnostics list.

## Toolbar

| Control | What it does |
| --- | --- |
| Run / Pause | Plays the execution over time. See [Feedback and external inputs](feedback-and-inputs.md). |
| **Refresh** | Analyzes again |
| **Time** / **Instruction** | **Time** draws blocks to scale. **Instruction** gives every command the same width, in order, so short commands stay readable. |
| **Aligned** / **Local** | **Aligned** puts all sequencers on a shared clock at their alignment anchor. **Local** uses each sequencer's own clock. |
| **Normal** / **Debug** | Hides or shows the Q1 rows |
| **Fit timeline**, **Zoom out**, **Zoom in** | Change the visible range. **Zoom selection** appears while a range is selected. |
| **Fold all unchanged intervals** | Collapses long stretches where nothing changes |
| **Filter** | Shows only events whose source or kind matches |
| **Sweep** | Opens [DEF runs and sweeps](def-sweep.md) |
| **Verify** | Checks [sequencer alignment](verification.md#alignment-between-sequencers). Needs a project with more than one sequencer. |
| Issue count | Scrolls to the diagnostics list |

### Mouse and keyboard

- **Time** layout: the wheel zooms the time axis, dragging pans, and Shift-drag selects a range.
- **Instruction** layout: the wheel zooms around the pointer, and Shift+wheel pans.
- Press Tab to move to a block and Enter or Space to select it. Escape closes a popup.

## Inspect an event

Click a block. Three things happen:

- The editor jumps to the source line.
- The line under the timeline shows the command, its sequencer, its source location and its key facts, such as its time. Choose **More** to open every field in **Details**.
- A popup lists the **Latched parameters** in effect and the **Registers used** by that RT block.

Settings such as gain, offset, frequency and phase are *latched*: the instruction that sets them only stages the value, and the next RT command applies it. The timeline shows each setting on the RT command that applies it, so a forgotten `upd_param` is visible as a setting that arrives late or never.

### From source to timeline

- **Q1Lens: Reveal Current Line in Timeline** selects the event for the line under the cursor. The timeline also follows the cursor as you move it.
- **Q1Lens: View Selection in Timeline** zooms to the time span of the selected lines.

## Loops

Q1Lens folds repeated iterations so a long loop does not hide the rest of the program. A tab on the ruler marks a folded loop.

- For a loop that was executed, choose **Expand executed iterations** to see every pass, and **Collapse executed iterations** to fold it again.
- For a loop shown as a preview, choose **Show next iteration** to add one pass at a time, and **Reset loop preview** to go back.

## Branches

Some branches depend on a value Q1Lens cannot know, such as a measurement result. Click the branch to open **Branch paths**:

- **Jump** and **Continue** choose a path. The timeline then follows it.
- **Compare both** draws the two alternatives in separate **Jump** and **Continue** rows, so you can compare their timing.
- **Collapse** returns to the summary.

A `set_cond` condition offers **Execute** and **Skip** in the same way. The **⑂** button on a row label lists every assumption in effect for that sequencer.

When a program tests the same feedback value twice, the first choice can make the second branch unreachable. Q1Lens then explains which earlier decision rules it out.

## Diagnostics

The list under the timeline groups findings by kind. Each row has a link to its source line and a **Show event** link that selects the event on the timeline. See [Write Q1ASM](editing.md#diagnostics) for the kinds of findings.

## Compare with an earlier run

After each analysis, Q1Lens marks what changed since the previous one: **added**, **changed**, **shifted** and **removed**. Select a marked block to see its values before and after.

To compare against a fixed point instead, run **Q1Lens: Save Timeline Comparison Baseline**. Later runs are compared with that baseline until you run **Q1Lens: Clear Timeline Comparison Baseline**.

## Practice

The tutorial folders [01-getting-started](../../examples/q1asm-tutorials/README.md) and `02-debug-reading` walk through each of these in a small program. `07-timing-pathologies/27-short-loop-underflow` is a good first underflow to read in Debug.
