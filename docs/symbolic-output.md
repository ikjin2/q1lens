# Symbolic waveform output

`q1lens.output(sequence)` accepts a Qblox sequence dictionary containing a Q1ASM `program` and indexed waveform arrays. Analysis supplies RT packets, captured register values and loop evidence. The output model retains intervals and repeated bodies in memory. Jupyter displays the interval table directly.

```python
import q1lens

sequence = {
    "program": "set_awg_gain $G,0\nupd_param 4\nplay 0,0,8\nstop",
    "waveforms": {"pulse": {"index": 0, "data": [1, 1, -1, -1]}},
}
out = q1lens.output(sequence)
bound = out.bind({"G": 16384})
i, q = bound.samples(4, 12, stage="gain_offset")
assert i == [0.5, 0.5, -0.5, -0.5, 0, 0, 0, 0]
```

`segments` contains half-open intervals and `Repeat` nodes. Times within a repeat are relative to its iteration start. Each interval carries the source event, waveform indices, sample offset, gain and DC words, frequency and phase. The source event identifies the packet that established the interval. Latch writes take effect at their capturing RT packet. Playing another waveform replaces the active waveform. After the last packet, waveform memory drains and the final settings remain active.

Integer expression trees support evaluation, free-symbol discovery, interval bounds and Z3 translation. The notebook and Hardware views identify the 32-bit loop wrap in the displayed count. The JSON `expr` field retains the arithmetic spelling for existing consumers. A zero loop count represents 2³² iterations, and a symbolic wait operand must bind to a length in 4–65535 ns. An unresolved branch or an unproved loop recurrence carries an explicit reason in `out.issues`.

When a loop body restores its entry state and uses the same commands and settings on every iteration, the model builds that body once. Binding checks iteration-dependent expressions at the first and last iterations and validates an iteration-independent body once.

## Sampling

`samples(t0, t1)` returns two lists on the 1 ns grid over `[t0, t1)`. The available stages are `waveform`, `gain_offset`, `nco`, `fir`, `filtered` and `dac`. The default is `dac`. `q1lens.output(sequence, params=values)` binds the same model in one call. Both public binding entry points check names and operand domains. For project-wide parameter dictionaries, select this output's inputs with `{name: values[name] for name in out.parameter_names}`. Long repeats can be sampled directly at a late time. FIR evaluation uses finite lookbehind. A nontrivial IIR can require streaming its history from the start to reconstruct its state.

Sampling uses binary search to locate ordered, non-overlapping intervals. The first sampling call checks literal endpoints. For symbolic endpoints, `bind()` checks their order after evaluation, and subsequent windows use binary search with the same binding and the default quantizer.

NCO mixing uses the SignalModel complex unit rotation. Port samples use normalized peak full scale. The `no_clip` port check accepts both endpoints of [−1, +1]. The default signed 16-bit DAC model rounds to nearest even and saturates at codes −32768 and +32767. A port sample of +1.0 therefore yields +32767/32768 at the DAC stage. Pass `dac_bits` or `quantizer(value, bits)` to change the quantizer. Gateware rounding and RF output calibration require independent hardware characterization.

## Baseband module ports

```yaml
modules:
  drive_module:
    type: QCM
    channel_map:
      drive: {out0: I}
      boost: {out0: Q}
    output_offsets: {out0: 0.125}
```

Keys under `channel_map` refer to sequencer IDs in the same project. Multiple paths routed to an output are summed after modulation and configured bound-time filtering. The module adds the normalized DC offset and clips once to [−1, 1]. `module.samples(models, "out0", t0, t1, clip=False)` exposes the pre-clipping sum.

For aligned sampling, pass `time_offsets={"drive": -20, "boost": -8}` if the sequencers reach the common anchor at local times 20 ns and 8 ns. Each sequencer contributes from its local time zero, and the module DC offset applies across the entire window. The Hardware preview records its time basis and start time, including negative times before an anchor.

A QCoDeS snapshot can supply the channel map and settings. Assign each project sequencer its `module` and `sequencer_index`, then select the module object inside the snapshot. Omit `snapshot_path` when the file is a module-level snapshot.

```yaml
modules:
  drive_module:
    type: QCM
    snapshot: station.json
    snapshot_path: [station, instruments, cluster0, submodules, module2]
```

The adapter reads `connect_outN` on the sequencer and `outN_offset` on the module. QCM ±2.5 V and QRM ±0.5 V map to ±1. It also reads recorded static path gain and offset, modulation enable, NCO frequency and phase. A static gain multiplies the dynamic gain. A static offset adds to the captured offset. [Sequencer parameters](https://docs.qblox.com/en/main/products/qblox_instruments/api/sequencer.html) · [QCM outputs](https://docs.qblox.com/en/main/products/architecture/modules/qcm.html) · [QRM outputs](https://docs.qblox.com/en/main/products/architecture/modules/qrm.html)

QBS compilation writes a sequence JSON sidecar beside the extracted Q1ASM, preserving waveform and acquisition memory, sequencer settings and module settings. Generated timeline projects reference the sidecar. Baseband routing uses the compiled channel name and connected output indices. When a compiler omits the module type, supply the hardware identity in the QBS configuration, for example `low_level: {q1timeline: true, module_types: {cluster0/cluster0_module2: QCM}}`. The compiler's raw settings are retained even when the module type is unknown. [Quantify sequencer settings](https://quantify-os.org/docs/quantify-scheduler/dev/autoapi/quantify_scheduler/backends/types/qblox/index.html)

## Compare two compiled outputs

Keep each QBS compile's generated `q1timeline.yml`, Q1ASM files and sequence JSON files together. Select a routed module port and a time window on the project clock.

```sh
q1lens compare-output before/q1timeline.yml after/q1timeline.yml --port cluster0/module2.out0 --start-ns 0 --end-ns 40 --json
```

The command compares the normalized port samples after filtering, mixing, module offset and clipping. The window above includes samples 0 through 39 at 1 ns intervals. If two versions both issue `play 0,0,4` but change the third waveform sample from 0.5 to −0.5, a pulse starting at time zero on a unity-gain path gives a first difference at 2 ns. The JSON `first_difference` contains `time_ns`, `before` and `after`. Exit codes are 0 for equal samples throughout the window, 1 for a difference and 2 for an incomplete comparison.

Both projects must identify the same module, module type, port and time basis. The command applies a common set of input values from the two projects. If their defaults disagree, supply `--params inputs.json` with the values to use. Source-local `.DEF` constants keep their own values.

## VS Code and verification

Open a Q1ASM source and run **Q1Lens: Open Timeline Preview**. Expand **Details** to find the Hardware port lanes. Each lane names its contributing paths and shows a bounded numerical preview when its timing and parameters resolve. Expand a path to inspect its intervals and select a source event. Missing parameters leave the symbolic intervals available. The Python sampling API accepts any requested time window.

The demo workspace includes a six-part notebook, a repeated burst, a gain ramp and a two-sequencer clipping counterexample. The `drive.q1asm` CodeLens opens the Concrete replay at the witness sample. [Demo workspace](../examples/symbolic-output/symbolic-output.code-workspace) · [Runnable notebook](../examples/symbolic-output/output-tour.ipynb) · [Output assertions and their units](verification.md)
