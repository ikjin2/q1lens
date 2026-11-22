# Hardware view

The **Details** section under the timeline shows the state of a sequencer at a moment in time: its registers, its settings, its acquisition bins and its output. Expand **Details**, then click a time on the timeline or select an event.

Use the **Modules** breadcrumb to move between modules and their sequencers.

## Tabs

| Tab | Shows |
| --- | --- |
| **Registers** | The value of each register at the selected time. **Previous change** and **Next change** step through a register's history. |
| **Parameters** | Sequencer settings in two columns. **Pending** holds values Q1 has set that are waiting to be applied. **Applied** holds the values in effect. |
| **Bins** | Acquisition bins and what was written to them |
| **Simulation** | Results of the event-driven simulation, when the project uses [Simulation](execution-modes.md) |

## Replay an execution

**Execution replay** steps through the recorded run. Choose **All sequencers** or **Selected sequencer**, then **Run**, **Pause** or **Step**. **Speed** sets how fast it plays. The tabs update as the replay moves.

## Port output

When the project routes sequencer paths to module outputs, **Details** adds a lane for each port. A lane names the paths that contribute to it and shows a preview of the summed waveform. Expand a path to see its intervals and select the source event of each one.

To get these lanes, give each sequencer its waveforms and describe the routing in the project:

```yaml
modules:
  drive_module:
    type: QCM
    channel_map:
      drive: {out0: I}
      boost: {out0: Q}
    output_offsets: {out0: 0.125}
```

The preview needs every parameter to have a value. With a parameter missing, the lane still shows the symbolic intervals.

You can then assert properties of the output, such as `no_clip`, in the source. See [Verify a program](verification.md#output-checks). For the sampling stages, the DAC model and the Python API, see [Symbolic waveform output](../symbolic-output.md).

## Acquisition checks

Q1Lens always checks how fast a program requests acquisitions. A readout module buffers a short burst, but it cannot keep up with a high rate forever.

| Finding | Severity |
| --- | --- |
| A burst larger than the documented allowance, or an uncertain rate | Warning (`acquisition_buffer_pressure`) |
| An infinite loop on a known QRM that requests acquisitions faster than the module can ever serve them | Error (`acquisition_buffer_sustained_overload`) |

Name the module type so the check can be definite:

```yaml
sequencers:
  - id: readout
    file: readout.q1asm
    module: readout_module
    module_type: QRM
```

`module` groups the sequencers that share one physical module. List all of them, because the sustainable rate depends on how many acquire.

Turn on `q1lens.q1timeline.hardwareReadiness` for extra hints about scope capture and reused bins. The acquisition checks, and the warning about settings never applied before `stop`, run whether it is on or off.

The model behind these checks is in [Acquisition buffer checks](../acquisition-buffer.md).
