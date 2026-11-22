# Verify a program

A timeline shows one run. Verification answers a wider question: does a property hold for *every* input in a range? Q1Lens uses the Z3 solver to prove it, or to find an input that breaks it and replay that run.

Every result is one of three verdicts:

| Verdict | Meaning |
| --- | --- |
| **Proved** | The check holds for the whole stated range, in the model |
| **Counterexample** | Q1Lens found a failing input and reproduced it |
| **Unknown** | An unsupported instruction, a missing input or the budget prevented a conclusion |

## Run it

- **On demand**: run **Q1Lens: Verify Program** on a saved Q1ASM file. A counterexample offers **Open counterexample timeline**.
- **On every save**: click **Z3 Auto: Off** in the status bar. Results appear above the lines they check. Click a counterexample to open its run.
- **For one sequencer of a project**: open **Sweep** in the timeline toolbar, choose the sequencer and select **Verify saved program**.

Verification reads saved files. Save your edits first.

## Queue boundaries

With no annotations at all, Q1Lens checks the RT queue. For each numeric `.DEF` it searches for the largest value that keeps the queue supplied:

```asm
.DEF N 20
move $N,R0
wait 56
pulse: sub R0,1,R0
       play 0,1,4
       jnz @pulse
stop
```

The result reads `N=1..37 safe. First RT underflow at 1188 ns for N=38`. Everything up to the first failure was checked. A larger value can become safe again.

The search varies one DEF at a time and keeps the others at their source values.

## Assumptions and assertions

Write checks as comments. They can follow `;`, `#` or `//`.

```asm
.DEF N 10
;@assume 1 <= N <= 513
;@assert rt_time == 20000 at @readout
;@assert R2 < 1024
```

- `@assume` limits a numeric DEF or an input parameter to an inclusive range. Inputs with a range vary together.
- `@assert` compares a value with an integer, using `<`, `<=`, `==`, `!=`, `>=` or `>`.

| You can assert on | Meaning |
| --- | --- |
| `R0` … `R63` | A register, as an unsigned word |
| `stop_code` | The operand of `stop`, signed |
| `q1_time`, `rt_time` | The Q1 and RT clocks, in ns |

Add `at @label` to check the state each time execution reaches that label. Without a label, the assertion checks the state at the end of the program.

A register counterexample opens the timeline together with the history of that register, so you can see which write produced the value.

## Output checks

These assertions check the summed waveform at a port. They need waveform data from the sequencer's `sequence_json`.

```asm
;@assume 4000 <= G <= 8000
;@assert no_clip
;@assert peak(out0) <= 0.25
;@assert net_zero
set_awg_gain G,G
play 0,0,16
stop
```

| Assertion | Checks |
| --- | --- |
| `no_clip` | Every port stays within full scale, −1 to +1 |
| `peak(out0) <= x` | The largest absolute sample on `out0` is at most `x` |
| `net_zero` | Each port has zero signed area |

A counterexample names the earliest sample that breaks the limit.

## Alignment between sequencers

In a project with several sequencers, the paths can take different amounts of time depending on measurement results. Select **Verify** in the timeline toolbar, or run **Q1Lens: Verify Sequencer Alignment**. Q1Lens tries every combination of outcomes and checks that the sequencers reach each shared checkpoint together. A counterexample has an **Open** button that shows both sequencers.

## Compare two versions

To check that an edit did not change behavior, run **Q1Lens: Compare RT Traces Across Inputs** and choose the other version. Q1Lens compares the RT commands, their timing, their operands and the applied settings over the shared `@assume` ranges. A difference reports the first input and the first command where the two versions part.

To compare the sampled output of two compiled projects at a physical port, use [`q1lens compare-output`](cli.md#compare-compiled-output).

## Try it

The [verification demo](../../examples/verification-demo/README.md) has one file for each of these checks. For the exact rules, such as how ranges intersect and when a check returns unknown, see [Program verification](../verification.md).
