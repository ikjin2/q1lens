# DEF runs and sweeps

A `.DEF` gives a name to a constant. The **Sweep** panel lets you run the program with other values for those constants, without editing the file, and see how the registers and the run time respond.

Open the timeline and select **Sweep** in the toolbar. To try it with a small program, open [`sum.q1asm`](../../examples/def-register-sweep/sum.q1asm).

## Run once with other values

1. Choose a **Sequencer** if the project has several.
2. The table lists every DEF. Edit **Run input** for the ones you want to change.
3. Check the registers to observe, or **Q1 time** for the full program time. **Show all registers** lists R0–R63.
4. Select **Run from start**.

The **Execution trace** shows the run. Use **Previous instruction** and **Next instruction** to step through it and see what each instruction read and wrote. **Reset** and **Reset all DEFs** restore the source values.

Your inputs apply to this run only. The source file and the project parameters are not changed.

## Sweep one or two inputs

1. Check the DEF to sweep. The panel suggests a range of 10 points. Edit **Min**, **Max** and **Step** to change it.
2. Check a second DEF for a grid. The first one is the horizontal axis.
3. Check at least one register or **Q1 time**.
4. Select **Start sweep**. **Stop sweep** keeps what has finished.

| Result mode | Limit | Gives you |
| --- | --- | --- |
| **Plots and cases** | 4,096 combinations | A scatter plot or heatmap, a histogram, and every case in a table |
| **Statistics only** | 1,000,000 combinations | Count, minimum, maximum, mean and standard deviation |

Use **Plot metric** to switch the plot between the observed values. Use **Interpretation** to read the registers as signed or unsigned without running again.

## Read the results

The result line counts the cases. The **Cases** table opens on **Needs attention**: cases that did not finish, carry a diagnostic or failed a comparison. Choose **All** to see every case.

Select a case in the table or on a plot to run it again and open its trace.

## Check against a formula

After a **Plots and cases** sweep, enter a **Formula** and select **Plot formula**. A formula can use the observed registers, the DEF inputs, arithmetic and functions such as `abs`, `sqrt`, `log`, `sin` and `atan2`. For example, `atan2(R1, R0)`.

To test the program against an expected result, add a **Reference formula** and an **Absolute tolerance**. Each case then passes or fails. For the sum example, compare `R2` with `X + Y` and a tolerance of `0`.

## Sample a large grid

When the grid is too large to sweep, enter a **Sample count** and select **Random sample**. Each draw picks one combination from the grid. Select **Estimate time** first to see how long the sample will take.

## Prove it instead

A sweep tests the values you chose. To cover every value in a range, see [Verify a program](verification.md). The full description of the panel, with its limits, is in [Run with DEF values](../def-register-sweep.md).
