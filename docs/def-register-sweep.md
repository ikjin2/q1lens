# Run with DEF values in VS Code

1. Open a `.q1asm` file and run **Q1Lens: Open Timeline Preview**. Click **Sweep** next to **Instruction** in the toolbar. To try it with a small program, open [sum.q1asm](../examples/def-register-sweep/sum.q1asm).
2. In **Run inputs**, choose a sequencer if the project has several. The register checkboxes show those referenced by the selected program. Choose **Show all registers** to see R0–R63. Edit a numeric DEF, check the registers you want to observe, and click **Run from start**. Check **Q1 time** to include the full program time in the results. The table shows the declaration, the draft input, and the value from the last displayed execution. **Reset** or **Reset all DEFs** restores the draft from the declaration. Run again to apply it.
3. Select an executed instruction or use **Previous instruction** and **Next instruction**. The trace shows register reads and readable values at issue. Pending writes show their planned availability, and a clipped issue shows its planned end alongside the observed interval. **Q1 time** measures elapsed time from the first instruction through `stop`, including waits for feedback and a full 32-entry RT FIFO. Instruction costs follow q1simulator 1.4.1 ISA 2 for supported forms. Simulation trace events use the project's physical schedule.
4. In **Sweep ranges**, choose one or two axes. Enter integer bounds and a positive step, check the combination count, and select **Start sweep**. The sweep requires at least one checked register or **Q1 time**. **Plots and cases** supports up to 4,096 combinations. **Statistics only** supports up to 1,000,000 combinations and reuses fixed program preparation. Both modes run every grid point, hold the other DEFs fixed, and report only the selected metrics. **Stop sweep** keeps the results reported through the current batch.
5. Use **Plot metric** to switch the scatter plot or heatmap and histogram among the selected metrics. A completed case can supply Q1 time even if a selected register value is undetermined. The plots use the saved results.
6. Select a case in the results table, scatter plot, or heatmap to reproduce its trace. The extension checks the reproduced raw words and any selected Q1 time against the saved case. The sweep remains visible while you inspect the trace.

After a **Plots and cases** sweep, enter a **Formula** and select **Plot formula**. For example, `atan2(R1, R0)` uses the observed final register values with the selected signed or unsigned interpretation. Use `atan2(INPUT_Y, INPUT_X)` for programs with those DEF inputs. Use `$NAME` to refer explicitly to a DEF. Formulas support arithmetic, parentheses, powers (`**` or `^`), constants `pi` and `e`, and functions such as `abs`, `sqrt`, `log`, `sin`, and `atan2`. Trigonometric functions use radians. Formulas use the saved cases and omit values that are unavailable or non-finite.

To compare a formula with an expected value, enter a **Reference formula** and optionally an **Absolute tolerance** in the same units. **Plot formula** opens the **Actual value** and **Error** (actual minus reference) plots together. With a tolerance, a case passes when the absolute error is at most the tolerance. The case table shows both values, the error, and the verdict. The comparison summary reports pass, fail, and unavailable counts and the largest absolute error. For the sum example, compare `R2` with `X + Y` using a tolerance of `0`.

To sample inputs, set the same axis bounds and step, enter a **Sample count** (default 1,000), and click **Random sample**. Each draw picks a combination uniformly from the selected grid. Repeated combinations share a mark on the plots and count once per draw in the statistics. The result mode limits the sample count, so a grid larger than the full sweep limit can still be sampled. **Statistics only** reports aggregates for the selected registers and Q1 time. **Plots and cases** also shows sample plots and histograms, supports formula comparison, and lets you open a sampled case's trace.

**Plots and cases** supports up to 25,000 random samples and **Statistics only** supports up to 1,000,000. Before starting, select **Estimate time** to measure a small batch with the current settings. Changing only the sample count updates the estimate from the same measurement. Inputs can take different amounts of time to run, so this is an estimate of wall-clock time. **Stop sampling** finishes the current batch and shows statistics and any formula comparisons for the samples already reported.

The fixture produces `R2 = 12` with `X = 2, Y = 10`. Sweeping `X` from 0 to 2 in steps of 1 with `Y = 10` gives outputs 10, 11, and 12. Their mean is 11 and their population standard deviation is approximately 0.8165.

Each register has its own valid sample count. A completed case with no error diagnostics contributes to each selected register whose final word is readable. Repeated values count once per case. Failed, blocked, limited, undetermined, and unexecuted cases have their own statuses. **Interpretation** switches between signed and unsigned 32-bit views of the same stored words and updates the register statistics without rerunning the sweep.

In **Statistics only**, the worker binds fixed instruction metadata once and skips instruction snapshots and register history. It retains RT timing and acquisition data used by runtime checks. The worker sends cumulative counts, minima, maxima, means, and population standard deviations. Individual plots, rows, and case traces are available in **Plots and cases**. A completed case with an undetermined register can still contribute a Q1 time sample.

DEF inputs apply to the selected sequencer instance and leave source files and project parameters intact. Save source edits before running. The extension marks existing results stale when it detects changes to project inputs. Explicit VS Code execution settings take precedence over the project mode. An analysis preview uses concrete execution for this action, and simulation uses the project's configured scenario.

## Prove a property or find a reachable path

The `q1timeline prove` command uses Z3 to explore one sequencer over an inclusive range of one numeric DEF. Other DEFs keep their source values. Arithmetic and status flags follow the Q1 core, including 32-bit wraparound, wide multiplication and register write delays. Conditional jumps split execution on their actual flag or register predicates. Loops consume the instruction budget each time they execute.

Install the optional Z3 dependency from the repository root, then check the sum example with its declared `Y=10`.

```shell
python -m pip install -e ".[smt]"
q1lens q1timeline prove examples/def-register-sweep/sum.q1asm --range X=0..1013 --assert "R2 < 1024"
q1lens q1timeline prove examples/def-register-sweep/sum.q1asm --range X=0..1014 --assert "R2 < 1024" --replay-ir output/smt-def-sweep/x1014.json
q1lens q1timeline render --ir output/smt-def-sweep/x1014.json --out output/smt-def-sweep/x1014.html --mode debug
```

The first query returns `proved` (exit 0). The second returns `counterexample` (exit 1) after Concrete replays `X=1014` and reads `R2=1024`. The optional `--replay-ir` saves that execution for the timeline renderer. Other final assertions can compare signed `stop_code`, unsigned `R0` through `R63`, or the analytical clocks `q1_time` and `rt_time` in ns. Assertions accept `==`, `!=`, `<`, `<=`, `>` and `>=`.

The [branch example](../examples/def-register-sweep/branches.q1asm) tests the same value twice. The first command below finds `X=1014` and confirms that Concrete reaches `limit` and executes `stop 1`. The second asks whether the same input can take both jumps. Taking the first requires `R2 < 1024`, while taking the second requires `R2 >= 1024`, so the path is unreachable.

```shell
q1lens q1timeline prove examples/def-register-sweep/branches.q1asm --range X=0..1014 --reach limit --replay-ir output/branch.json
q1lens q1timeline prove examples/def-register-sweep/branches.q1asm --range X=0..1014 --path "check=taken,below=taken"
```

`--reach` and `--path` accept source labels or compiled Q1 instruction addresses. Path decisions occur in the specified order and can have intervening instructions and branches. Repeating a decision asks for separate occurrences. Concrete replays through the instruction or last decision that establishes the witness, so a reachable prefix can be confirmed even when execution would continue looping. A confirmed witness returns `reachable` (exit 0). Exhausting all feasible paths without a witness returns `unreachable` (exit 1).

`--max-steps` bounds Q1 issues per path (default 10,000). `--max-paths` bounds the number of explored paths (default 256). An unfinished path, solver timeout, unsupported operation such as a LINQ receive, or unreadable register can leave the answer `unknown` (exit 2). A verified witness on any explored path suffices for an existential query or a counterexample.

### Check a fixed-start RT queue

In the [queue example](../examples/def-register-sweep/queue.q1asm), the first wait occupies the RT core for 1,184 ns. The Q1 core counts down `N` before it can enqueue the next wait. With RT starting at 4 ns, the next dequeue is due at 1,188 ns. The 32-slot queue model accounts for full-queue stalls, enqueue/dequeue ties, and the 4 ns dequeue spacing of a zero-dwell command.

```shell
q1lens q1timeline prove examples/def-register-sweep/queue.q1asm --range N=1..37 --underflow-at 4
q1lens q1timeline prove examples/def-register-sweep/queue.q1asm --range N=1..38 --underflow-at 4 --replay-ir output/underflow.json
```

For `N=37`, the next packet arrives exactly at the dequeue deadline. Extending the range to 38 produces the witness `N=38`. Concrete replays its Q1 instruction path and Simulation independently confirms the underflow at 1,188 ns. For this query the exported timeline is the fixed-start Simulation trace. The queue result is specific to the supplied startup delay and the modeled local instructions. Scenarios involving external triggers, LINQ traffic or acquisition hardware need their own Simulation inputs.

### Cross-check Analysis summaries

With the `dev` and `smt` extras installed, run `python -m pytest -q tests/test_q1timeline_analysis_smt_oracle.py`. The tests take time and register bounds reported by Analysis and ask Z3 whether any permitted input violates them. Analysis summarizes the loop or merges the branches. The verifier follows the individual instructions on each feasible path.

The cases cover loop durations that vary by iteration, zero-duration RT packets, and the Q1 clock at the 32-entry FIFO boundary. The branch tests cover equal clocks across both paths and a join with equal RT time but different Q1 completion times. For the symbolic-input cases, Z3 ranges over all 32-bit input words.
