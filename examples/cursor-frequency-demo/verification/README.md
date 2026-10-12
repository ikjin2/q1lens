# Cursor-frequency verification profiles

These profiles run the **four original Q1ASM files and original `params.json` unchanged** through Q1Lens simulation. They preserve the module hierarchy and source links used by the editor. They do not connect to hardware, execute the notebook, or import q1simulator.

```powershell
python examples/cursor-frequency-demo/verification/verify.py
python -m pytest tests/test_cursor_frequency_verification.py -q
```

The first command prepares sequence memory and projects, runs each profile twice through fresh CLI output files, checks independent expectations, and compares the two IR results for determinism. It returns nonzero if any required check fails. Checks use explicit exceptions and remain enabled under `python -O`. Every run checks the hashes of all original top-level demo files before and after execution.

To prepare one profile in a separate CI/browser fixture directory:

```powershell
python examples/cursor-frequency-demo/verification/verify.py --profile two-shot-feedback --out-dir output/cursor-verification
```

Use `--profile deadband-hold` for the other profile. `--prepare-only` generates projects/memory/expectations without claiming a verification pass. Generated files are ignored under the default `verification/generated/` directory. The `--out-dir` directory is owned by this generator; each profile's previous `timeline_ir.json` is removed before fresh verification to prevent stale results being mistaken for a new pass.

For the ordinary Q1Timeline CLI and VS Code project flow, first prepare the files, then open or analyze the generated project:

```powershell
python -m q1lens q1timeline analyze --project examples/cursor-frequency-demo/verification/generated/two-shot-feedback/q1timeline.yml --out output/cursor-ir.json
```

The generated project refers directly to the original Q1ASM and parameter paths. `timeline_ir.json`, `expected.json`, `evidence.json` and `cli-0.log` / `cli-1.log` are placed beside the generated project. `evidence.json` contains the verified event IDs, local times, source locations, original hashes, instruction counts and sample comparisons. Event IDs identify this particular execution; tests locate the relevant events by their semantics rather than treating IDs as mathematical golden values.

## Scenario contract

| Module | Sequencer | Original role |
| --- | --- | --- |
| QCM0 | blue_peak | Broad Gaussian waveform and marker; slowly changing peak state |
| QRM0 | red_tracker | Left/right IQ acquisition, absolute magnitudes, slope/deadband, tracked center/gain |
| QRM0 | red_cursor | Narrow cursor; consumes center/gain and applies them on the next shot |
| QCM1 | orange_drive | NCO burst; maps tracked center to frequency and gain to amplitude |

- Two scenario triggers arrive at **1,000 and 40,000 ns** on address 1 of each sequencer. Their 39 µs spacing exceeds the original 30 µs shot period. There are no original parameter overrides.
- The original continuous loops have no stop instruction. The expected terminal state is **blocked, waiting for the unsupplied third trigger/input**. Four completed acquisitions and two emitted output pulses per output sequencer must precede that state. An unsupported instruction, runtime error, underflow, unexpected step/time limit, or missing third trigger-wait event fails verification.
- Q1 may prepare commands/register values for another iteration while RT waits for its trigger. A final register value therefore need not be the value applied to the last output. The checks distinguish register-readable times, captured setters, RT application and output times.
- Queue capacity is 32, startup delay is a modeled 200 ns, and the safety limits are 10,000 Q1 instructions per sequencer and 80,000 ns. These budgets are not reached in the verified profiles.
- All five routes originate at red_tracker: IQ ID 1 returns to red_tracker; IDs 16/18 reach red_cursor; IDs 17/19 reach orange_drive. Route latency is the original parameter `LINQ_FEEDBACK_LATENCY=380 ns`, explicitly used as a scenario assumption for every route, including self IQ. It is not a calibrated hardware latency claim.
- Acquisition values are **synthetic signed integer IQ feedback words**. `profiles.json` gives the desired values after `IQ_SHIFT`; the scenario supplies those integers multiplied by `2**IQ_SHIFT`, then the original `fb_acq_iq_shift` instruction shifts them back. They are not ADC measurements or values calculated from the blue waveform. Acquisition result provenance must say `scenario`.
- Output waveform memories are explicitly generated from the notebook's documented shapes: a Gaussian with standard deviation `0.18*PEAK_DUR`, a unit rectangular cursor and a unit orange envelope. The notebook itself is not executed. The output model samples every 4 ns and exports at most 2,000 points per trace. All output traces must remain untruncated.

## Independent expectations

For left/right pairs `(I_L,Q_L)` and `(I_R,Q_R)`:

```text
L = abs(I_L) + abs(Q_L)
R = abs(I_R) + abs(Q_R)
slope = R - L
delay' = delay                         if abs(slope) < SLOPE_DEADBAND
         delay + TRACK_STEP            if slope >= SLOPE_DEADBAND
         delay - TRACK_STEP            otherwise
delay' = clamp(delay', TRACKER_MIN_DELAY, TRACKER_MAX_DELAY)
center = delay' + TRACKER_CENTER_OFFSET
gain = floor((L + R)/2)
cursor_gain = floor(previous_gain * CURSOR_GAIN_NUMERATOR / 2**CURSOR_GAIN_SHIFT)
orange_gain = floor(previous_gain * RF_GAIN_NUMERATOR / 2**RF_GAIN_SHIFT)
frequency_word = FREQ_BASE + (previous_center - TRACKER_MIN_CENTER) * 2**FREQ_CURSOR_SHIFT
```

The use of **previous** center/gain is checked at each RT play event. Integer frequency words are converted to Hz by dividing by four. Expected acquisition and output times are sums of trigger time and the original programmed waits/durations. Each output also has an exported DAC sample checked against a separate closed-form Gaussian/rectangle/NCO cosine calculation with 16-bit clipping/quantization and tolerance of one DAC LSB.

With the current original parameters:

| Profile | Shot slopes | Tracked centers | Tracked gain | Second cursor start/gain | Second orange frequency word/gain |
| --- | --- | --- | --- | --- | --- |
| two-shot-feedback | +2000, −4000 | 5528, 5524 | 8000, 8000 | 45496 ns / 5718 | 782336 / 2000 |
| deadband-hold | +32, −32 | 5524, 5524 | 7016, 7016 | 45492 ns / 5015 | 780288 / 1754 |

Both profiles include negative Q values and both slope-sign branches. The first profile takes the movement branches; the second takes both deadband hold branches. Tests additionally corrupt frequency application, a branch outcome and a register-writer reference to ensure the verifier rejects an incorrect trace.

## Editor walkthrough

Open `cursor-frequency-validation.code-workspace` in VS Code. It selects project-defined execution and manual refresh, and provides **Tasks: Run Test Task → Q1Lens: Verify cursor frequency profiles**. Its Python command defaults to `python`; use **Q1Lens: Select Python Environment** if your Q1Lens installation is in another environment. Open `generated/two-shot-feedback/q1timeline.yml` after the task finishes, then run **Q1Lens: Open Timeline Preview**. The same steps apply to `generated/deadband-hold/q1timeline.yml`.

After opening the generated project and its Hardware view:

Open the generated profile's `q1timeline.yml` first, then run **Q1Lens: Open Timeline Preview** from that editor. A register's **Open source** action opens the original Q1ASM file while keeping the verification project in the existing preview. Do not run **Open Timeline Preview** again from that original source editor: project discovery would select the original demo configuration. To recreate the verification preview, run the command from the generated YAML again.

1. Select **QRM0 → red_tracker**. Bin 0 is unknown at 6071 ns and shows scenario I=24576000, Q=−4096000 at completion, 6072 ns. The second completion is 7072 ns.
2. Inspect the slope branch at 7636 ns (`red_tracker.q1asm:140`) and the R15 center write (`red_tracker.q1asm:182`). In the movement profile R15 becomes 5528 at 7728 ns; in the hold profile it becomes 5524 at 7720 ns.
3. Follow feedback to **QCM1 → orange_drive**. R2 initially becomes 780288 at 100 ns, then updates at 8464 ns (`orange_drive.q1asm:68`). In the movement profile its new value is 782336; the first burst still used 780288. Click **Values read: R3 = 782336** to select its writer at readable time 8448 ns, source line 66, and inspect the shift calculation and its input R3=1528.
4. In the movement profile, Parameters at 17039 ns shows pending frequency 782336 but applied 780288. At **17040 ns**, `upd_param` first applies frequency 782336 and gain `2000, 0`. At the second orange play, **41040 ns**, those values remain applied: distinguish **Applied at 17040 ns** from **RT event at 41040 ns**. Click the applied frequency to follow its setter to Q1 time 8472 ns (`orange_drive.q1asm:72`). The hold profile's second play uses frequency 780288 and gain 1754.
5. Trace the feedback backward in the movement profile: select orange R1=5528 at 8368 ns, then **Show received input that supplied this value** → receive 8360 ns → **Show sending event** → red_tracker send 7980 ns → **Show sending instruction** → Q1 time 7764 ns, source line 189. Click its **Read at issue: R15 = 5528** to reach the register writer at 7728 ns, source line 182.
6. Select **QRM0 → red_cursor** and compare the second cursor's position and gain with the table. Rewind across a readable-time boundary to distinguish unavailable, previous and newly readable register values.

Check that **Open source** reaches line 66 while preserving the 8448 ns cursor and R3=782336. A deliberate click on source line 64 should follow that instruction to its issue time, 8416 ns.

These two profiles verify the original feedback path. The separate `verify_boundaries.py` campaign now exercises the 1,000-shot blue PRNG update, acquisition-bin ring wrap with explicit smaller-bin overrides, delay clamps and exact deadband boundaries. Run it with `python examples/cursor-frequency-demo/verification/verify_boundaries.py` from the repository root. Its default output is `output/extended-simulation-validation/boundaries`; `--profile bin-wrap-preview` selects a compact three-shot project for the editor. The long profiles isolate blue_peak or red_tracker and disable physical sampling.

Use [extended-validation.code-workspace](../../simulation-verification/extended-validation.code-workspace) for the boundary campaign and [input-fault campaign](../../simulation-verification/faults/README.md) together. Physical cable calibration, trigger jitter and real quantum/hardware response remain outside these checks. Generated signals and acquisition inputs are numerical/synthetic evidence.
