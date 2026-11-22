# Program verification

Automatic verification is off by default. To enable it, open a saved Q1ASM file and click **Z3 Auto: Off** in the VS Code status bar. CodeLens then shows queue boundaries on numeric DEFs and verification results on assertion comments. Click a counterexample to open the recorded timeline at the exact instruction occurrence or failed dequeue. A register assertion opens its register history. The [VS Code walkthrough](../examples/verification-demo/README.md) includes a queue boundary, a repeated feedback test, a failing assertion, two sequencers and a before/after pair.

Click **Z3 Auto: On** to turn automatic verification off again. VS Code saves your choice in `q1lens.q1timeline.verification.autoRun` for the workspace, or in user settings when no workspace is open. If you previously disabled `editor.codeLens` for Q1ASM, enable it to see automatic results. You can run **Q1Lens: Verify Program** in either state. In the timeline's **Sweep** tab, **Verify saved program** checks the selected sequencer in its project.

## Queue boundaries and assertions

Standalone queue checks use the simulator’s buffered RT startup. Pass `--startup-delay-ns` to model a fixed launch time. For a project, use `--project q1timeline.yml` and optionally `--sequencer ID` to select an instance. The project supplies the queue, startup and scenario settings. Each queue finding reports the startup policy used.

An automatic boundary search varies one numeric DEF at a time. Its default interval starts at 1 or the declared value, whichever is smaller, and ends at 4294967295. Use an assumption to select another interval, including zero or negative values. Other explicitly ranged inputs vary jointly. Other DEFs retain their source values. A first failure means the entire stated prefix was checked. A later value can become safe again.

```asm
.DEF N 10
;@assume 1 <= N <= 513
;@assert rt_time == 20000 at @readout
;@assert R2 < 1024
```

Each assumption constrains a numeric DEF or a referenced input parameter to an inclusive integer interval. Multiple constraints on an input intersect. Assertions compare an unsigned register word, signed `stop_code`, or analytical `q1_time` or `rt_time` in ns with an integer using `<`, `<=`, `==`, `!=`, `>=` or `>`. A label observes the state immediately before each executed visit. An assertion without a label observes termination. The source value is used for DEFs without a range.

### Output assertions

```asm
;@assume 4000 <= G <= 8000
;@assert no_clip
;@assert peak(out0) <= 0.25
;@assert net_zero
set_awg_gain G,G
play 0,0,16
stop
```

Provide waveform memory through the sequencer's `sequence_json`. Verification sums the mapped sequencer paths and module offset at each integer nanosecond. Waveforms can continue beyond the final packet, and captured offsets persist after the waveform ends. Peak and clipping checks cover this retained output. Area verification sums through the last waveform sample and checks that the summed port settles to zero. A potentially persistent output yields `unknown` for convergence. Sources without module routing use logical ports `out0` and `out1` for I and Q.

Mixed ports use the project's alignment. Each local time plus its sequencer's alignment offset gives the shared sample time. Checks begin when the first contributor starts, and the module DC offset applies throughout. For example, two sequencers reaching their first `wait_sync` at 20 ns and 8 ns both reach aligned time zero there. Events before that anchor have negative aligned times. A missing or uncertain anchor yields `unknown`.

| Assertion | Quantity checked before clipping |
| --- | --- |
| `no_clip` | Every configured port stays within [−1, 1] in normalized full scale. |
| `peak(out0) <= x` | The largest absolute sample on out0 is at most x. |
| `net_zero` | Each port has zero signed area in full scale × ns. |

`no_clip` uses the inclusive normalized port interval. Both −1 and +1 pass. Only a pre-clipping sum below −1 or above +1 violates the assertion. DAC sampling applies its chosen quantizer. The default signed `b`-bit model rounds to the nearest code (ties to even), with normalized rails −1 and `1 − 2**(1−b)`. For example, a +1 port sample passes `no_clip` and a 16-bit DAC represents it as 32767/32768. The per-sequencer simulation counter `clipping.dac_samples` also counts samples whose rounded code exceeds the positive rail, including +1 and values just below it.

A `peak` or `no_clip` counterexample identifies the earliest violating integer sample across the allowed parameter bindings and every port covered by the assertion. At a time shared by several violating ports, the first port in name order wins. Among feasible bindings for that time and port, the verifier minimizes each parameter in name order. It reproduces the sample in Concrete and records `earliest_sample: true`. A timeout during minimization or an NCO envelope candidate that fails Concrete replay yields `unknown`. A `net_zero` counterexample reports the signed area and its evaluation endpoint.

The verifier first bounds the waveform, gain and offset over the assumed parameter ranges. For tighter checks it uses waveform samples, prefix sums and symbolic interval boundaries, including overlap between sequencers. A counterexample carries the parameter binding, Concrete timeline, port, value and sample time. With NCO modulation, a proof can use the complex envelope magnitude to bound either rotated component. A signed-area assertion with unresolved rotation can return `unknown`.

For fixed inputs and a finite FIR filter, `no_clip` and `peak` check the filtered port sum before clipping, including samples after the waveform ends. A project using Simulation supplies its shared clock, startup timing and scheduled inputs to the FIR check and replay. Other projects use Concrete execution. For example, a unity-gain pulse with four samples of 1 and `output_settings.fir` set to `[1, 1]` gives samples 1, 2, 2, 2, 1. The first clipping counterexample is the second sample, 1 ns after playback starts. Four samples of 0.25 peak at 0.5 and pass `no_clip`. FIR assertions with a varying input range, signed-area assertions with a nontrivial FIR, and port assertions with a nontrivial IIR currently return `unknown`.

During branch analysis, testing the same received word twice can prove one continuation impossible. **Compare both** retains each feasible outer path. The source diagnostic identifies which earlier decision rules out the inner alternative. A new feedback receipt has its own value and can produce a different decision.

## Alignment and equivalence

**Verify** in the timeline toolbar (Verify alignment) simulates the project on a shared clock. Each enumerated measurement bit has a source in the JSON report. An optional scheduled trigger occurrence uses a single shared bit for all its recipients. Thresholded acquisition outcomes can differ on successive measurements. The report names its checkpoints and compares corresponding visits. A counterexample includes both sequencers' observations.

**Q1Lens: Compare RT Traces Across Inputs** compares two saved source files on shared inputs. Use `@assume` to define the DEF ranges. By default times are relative to each version's first dequeue. To use a common fixed launch on the experiment clock, pass `--startup-delay-ns N` to `equiv`. The comparison includes the issued RT sequence, dwell, operands, applied settings and stop. The witness identifies the first difference and includes both timelines.

The comparison intersects the two files’ assumptions for each shared input. An external parameter named `D` and a source-local `.DEF D` have independent values. When both have explicit ranges, the reported inputs use `D` for the parameter and `$D` for the DEF. A shared range on a numeric DEF varies that DEF in each version that declares it.

Use `q1lens compare-output` to compare the sampled result of two compiled projects at a physical module port over a fixed time window. [Port comparison and example](symbolic-output.md#compare-two-compiled-outputs)

## CLI and CI

```sh
q1lens verify program.q1asm --json
q1lens verify program.q1asm --project q1timeline.yml --sequencer drive --json
q1lens verify q1timeline.yml --alignment --json
q1lens equiv before.q1asm after.q1asm --json --include-replay
```

| Exit | JSON status | Meaning |
| --- | --- | --- |
| 0 | `proved` | All reported checks hold within the stated domain and model. |
| 1 | `counterexample` | At least one check has a reproduced violation. Read the other checks for their individual verdicts. |
| 2 | `unknown` | An unsupported operation, unresolved input, invalid program or exhausted budget prevents a conclusion. |

Read the top-level `status` and the individual `checks` array. Each check has a one-based source `line`, `file`, `kind`, `status` and `message`. A counterexample supplies the exact `inputs`, `event_id` and source location. With `--include-replay`, `counterexample.timeline` contains the TimelineIR that owns the event. An agent can inspect the first failing check, modify the source, rerun the same command and use its exit code as the gate. `--max-steps`, `--max-cases` and `--timeout-ms` bound verification. Source verification also accepts `--max-paths`.
