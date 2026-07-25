# Cursor Frequency Demo

For an implementation-level explanation of the hardware topology, timing equations, tracking law, gain and frequency mappings, and acquisition semantics, see [`TECHNICAL_GUIDE.md`](./TECHNICAL_GUIDE.md).

This q1asm demo shows one feedback loop as a compact oscilloscope story:

- `blue_peak.q1asm` waits for an external trigger, raises QCM marker 1 so the oscilloscope can trigger, and plays a broad blue peak whose position and height follow independent xorshift PRNG random walks.
- `red_tracker.q1asm` uses QRM acquisition feedback: it acquires left/right IQ samples, pops the IQ values, compares `abs(I)+abs(Q)`, updates `$MEAS_DELAY`, converts that left-sample delay into `$TRACKED_CENTER`, and sends the tracked center on separate LINQ channels for the cursor and RF sequencers. It also sends `$TRACKED_GAIN = (left magnitude + right magnitude) / 2` on separate gain channels.
- `red_cursor.q1asm` centers the red cursor on the latest tracked center, scales the cursor height with a calibrated fixed-point cursor gain (`tracked_gain * CURSOR_GAIN_NUMERATOR >> CURSOR_GAIN_SHIFT`), then receives the updated center and gain for the next shot.
- `orange_drive.q1asm` runs on the second QCM, resets sine phase before each burst, plays a long low-MHz orange sine through the broad blue peak using a calibrated fixed-point RF gain (`tracked_gain * RF_GAIN_NUMERATOR >> RF_GAIN_SHIFT`), then receives the tracked center and gain for the next shot, subtracts `TRACKER_MIN_CENTER`, scales the offset with `FREQ_CURSOR_SHIFT`, and applies the resulting register with `set_freq $FREQ_WORD`.

This is intentionally a small triggered shot loop demo. The external trigger controls the demo speed. The cursor height, orange amplitude, and orange frequency use the acquisition result from the previous shot, so the demo is real acquisition feedback without claiming same-shot causality. Because the blue peak moves slowly and is broad, the oscilloscope can still show the blue peak, red cursor, and orange sine overlaid in one view.

The hardware notebook enables the Cluster external trigger input and maps it to trigger address 1. Without a trigger on that input, all four sequencers remain at `wait_trigger 1` and no marker or waveform is emitted. The default shot budget is 30 us, so keep the external trigger at or below 33.3 kHz while debugging acquisition FIFO behavior.

The tracker keeps the two acquisition starts 1 us apart and rotates the acquisition bin index through a 65536-bin ring. Qblox documents a 300 ns minimum start-to-start acquisition spacing for QRM/QRM-RF/QTM modules, but this demo uses a wider margin because acquisition feedback and bench trigger jitter made the earlier 360 ns spacing trip `ACQ_BINNING_FIFO_ERROR` on hardware. The rotating bins avoid repeatedly writing a continuous run into only bins 0 and 1 while still using real acquired IQ feedback.

Each shot consumes two bins, so the first pass through the 65536-bin ring holds 32768 left/right shot pairs. At the recommended 33.3 kHz trigger rate this is about 1.0 seconds of acquisition history before bin reuse. In the default bench workflow, start once with `start_demo_burst()`, let the external trigger run continuously, and download only when the final stop/download cell calls `finish_demo_burst(read_data=True)`. The final download does not wait for the full 65536-bin acquisition to complete, because normal bench runs usually stop with a partially filled ring. Keep the run shorter than the first-pass window if bin-number order must remain a chronological shot history. Qblox binned acquisitions are accumulative: after wrap, a reused bin contains the hardware average of all writes to that bin and its `avg_cnt` increases; it is not overwritten with only the newest sample.

For occasional monitoring, the optional `run_demo_live_plot_loop(update_s=5.0, repeats=10)` helper disables the Cluster external trigger input for a short `idle_wait_s` window while the notebook downloads and deletes the tracker acquisition data. That produces periodic dead time, so do not use it for the lowest-disturbance oscilloscope run.

For repeated finite tests, `run_demo_burst_loop(burst_s=5.0, repeats=10)` repeats a safer stop/re-arm workflow and downloads after each burst. It is useful for debugging but can look choppy on the oscilloscope.

The companion notebook `cursor-frequency-demo.ipynb` follows the same hardware-oriented pattern as `three-peak-demo.ipynb`: it connects to a Qblox Cluster, generates sequence JSON files, configures two QCMs plus one QRM, runs a q1timeline preflight check, and starts/stops the experiment.

Run:

```powershell
python -m q1lens q1timeline analyze --project examples/cursor-frequency-demo/q1timeline.yml --out tmp/cursor-frequency-demo-ir.json
```
