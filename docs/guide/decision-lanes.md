# Decision lanes

A program that reads feedback takes a different path for each measurement result. **Decision lanes** records one run of a sequencer and puts every branch that depends on a feedback word on one time axis: which way it went, why, and what the other way would have changed.

Open a `.q1asm` file and run **Q1Lens: Open Decision Lanes** from the Command Palette or the editor's context menu. To try it, open [`golden_section.q1asm`](../../examples/golden-section/golden_section.q1asm).

## Give the run its inputs

The panel records the sequencer of the file you opened. Choose another one with **Sequencer** if the project has several.

A run needs a value for every feedback word it reads:

| Source | What the run reads |
| --- | --- |
| **Feedback words** | The list you type. The first `fb_pop_data` that runs reads the first value, the next one the second, and the list repeats. Without a list every word reads `0`. |
| **Project LINQ schedule** | The arrivals in the project's [`external_inputs.feedback`](feedback-and-inputs.md#external-inputs) for this sequencer. The panel uses them when the project has them. Choose **Typed words** in **Feedback source** to try other values. |

Type decimal or hexadecimal integers separated by commas or spaces and select **Apply**. For the golden-section example, `2000000, 1000000, 4000000, 1000000` makes the first measurement of each pass the smaller one.

Triggers come from the project's external inputs. A run that waits for an input the project does not supply stops there. The panel marks it **Partial** and shows the reason.

The panel reads the saved file. It records again when you save, and **Refresh** does the same on demand. Your words are remembered for each sequencer.

## Read the timeline

The horizontal axis is the RT clock. From top to bottom:

| Row | Shows |
| --- | --- |
| **Loop** | The passes of the loop that contains the decisions. Passes that took the same directions share a name, taken from the label the branch lands on |
| **RT instructions** | Instructions with RT duration. A wait loop is one block |
| Decision rows | One row for each branch that decides. A cell on the upper rail is **Jump**, on the lower rail **Continue** |
| Small marks | Other conditional branches, by kind |
| State lane | Registers the arms write that are read again later |

Q1 instructions issued between two RT instructions share one position on the axis. A cell marks where a decision sits in the RT schedule, not the moment the Q1 core made it.

The panel sorts conditional branches by what the recorded run shows:

| Kind | Meaning |
| --- | --- |
| **Input decision** | The compared value traces back to a feedback word, and the arms do different things |
| **Value shaping** | Depends on the input, but only rewrites the register it compared, as an absolute value or a clamp does |
| **Indirect** | Reads a value written inside an arm of an input decision |
| **Counted loop** | Reads only constants and jumps backward |
| **Fixed test** | Reads only values computed from constants |

Zoom with `Ctrl` and the mouse wheel, drag to pan, and use the arrow keys to move between decisions. **Show more on the state lane** adds the compared registers and any other register that changes.

## Inspect one decision

Select a cell. The editor selects the branch line, and three cards describe the decision:

- **Why this way** shows the two compared values, the result, and the instructions the values passed through back to the feedback words.
- **What the arms change** compares the two arms up to the join, the first instruction both arms reach again.
- **If it had gone the other way** compares the rest of the run: the first RT instruction that differs, later decisions and the final register values.

The second card names what kind of difference the branch makes:

| Label | Meaning | Where to look |
| --- | --- | --- |
| **Changes timing** | The arms take different RT time | The other arm gets its own RT row |
| **Changes RT instructions** | Same RT time, different RT instructions | The other arm gets its own RT row |
| **Changes state only** | Only register values differ | Dashed lines on the state lane |
| **No difference at the join** | Only Q1 time differs | |

## The other arm is an assumption

To show the arm the run did not take, the panel records a second run with the same inputs and that one branch forced the other way. A forced cell has an outline in the theme's warning color, and a strip of that color marks everything after it.

A forced run shows what the other arm does. It does not show that any input makes the program take it: the compared values stay as they were.

The panel records up to 64 other arms on its own, starting with one for each direction of every branch, and then the other arm of any decision you select. **Recorded runs** lists them on the same time axis, and **Open this run** makes one of them the run you inspect.

## What it leaves out

- One sequencer at a time. Feedback from other sequencers enters as the values you supply.
- Decisions are Q1 branches. A `set_cond` condition runs in the RT core and appears on the [timeline](timeline.md) instead.
- The record has no link between an acquisition and the words read after it. The panel pairs them by order and says so.
- Like every Q1Lens result, a run comes from the software model described in [Execution modes](execution-modes.md), not from an instrument.
