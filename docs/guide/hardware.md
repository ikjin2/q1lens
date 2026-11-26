# Hardware view

The **Details** section under the timeline shows the state of a sequencer at a moment in time: its registers, its settings, its acquisition bins and its output. Expand **Details**, then click a time on the timeline or select an event.

**Details** opens on **Outputs**, which lays the hardware out on one time axis: the cluster, its modules, each output port, and the sequencer paths that feed the port. Select a sequencer name to inspect that sequencer. The **Outputs** breadcrumb takes you back.

## One sequencer

This view shows one sequencer at a time. It keeps that sequencer's output lane on screen. Below the lane, one row per part of the sequencer shows its state at the cursor. To inspect another sequencer, choose it from the menu beside the sequencer's name in the breadcrumb.

| Row | Shows |
| --- | --- |
| **Q1** | The instruction Q1 is processing, the registers it read and the flags it changed |
| **RT** | The real-time command in effect |
| **RT queue** | Packets waiting for RT, and a Q1 stall if there is one. Shown for a [Simulation](execution-modes.md) run. |
| **Output** | The formula of the interval under the cursor, the value each path holds there as a fraction of full scale, the settings in effect, and any parameter drawn with an [assumed value](#parameters-without-a-value) |
| **Acquisition** | The acquisition in progress, or the last one. Shown for a program that acquires. |

| To | Do this |
| --- | --- |
| Move the time cursor | Press or drag on the lane |
| Step to the previous or next change of state | **‹** and **›** |
| Go to the start of the command in a row | Select the row's status |
| Open a row's source line | Select the file name at the end of the row |
| Open the tab that belongs to a row | Select the row's name |

When Q1Lens cannot draw a sequencer's output, for example because it has no waveform memory, the view says why in place of the lane. A slider then moves the cursor.

A warning about the selected time appears above the lane in amber. Limits that apply to the whole analysis appear beside it. Two or more of them fold into one line with their count. Select that line to read them.

When a sequencer's start relative to the others is unknown, this view uses the sequencer's own clock and says so.

### Tabs

| Tab | Shows |
| --- | --- |
| **Registers** | The value of each register at the selected time. **Previous change** and **Next change** step through a register's history. |
| **Parameters** | Sequencer settings in two columns. **Pending** holds values Q1 has set that are waiting to be applied. **Applied** holds the values in effect. |
| **Bins** | Acquisition bins and what was written to them. Shown for a program that acquires. |
| **Simulation** | Results of the event-driven simulation. Shown for a [Simulation](execution-modes.md) run. |

## Replay an execution

**Execution replay** steps through the recorded run. Choose **All sequencers** or **Selected sequencer**, then **Run**, **Pause** or **Step**. **Speed** sets how fast it plays. While replay is open, it takes the place of the lane, the rows and the tabs. It shows the recorded source lines beside the register values. Select **Execution replay** again to close it.

## Outputs

Each port row draws the waveform that leaves that connector, between the dotted full-scale lines. A port that exceeds full scale is marked in red. The rows under a port are the sequencer paths routed to it. Each path shows its own waveform and, below it, one tag per interval with its formula, such as `G/32768 × wf0`. A bracket above the tags marks a loop with its count and period.

| To | Do this |
| --- | --- |
| Go to the instruction behind an interval | Select its tag |
| Move the time cursor | Press or drag on a lane |
| Zoom | **+** and **−**, or Ctrl+scroll over a lane |
| Pan | Drag the time ruler, or Shift+scroll |
| See the whole output again | **Fit** |
| Inspect a sequencer's registers, parameters and bins | Select its name |

When the lanes have focus, the arrow keys pan, **+** and **−** zoom and **Home** fits.

To get port rows, give each sequencer its waveforms and describe the routing in the project:

```yaml
modules:
  drive_module:
    type: QCM
    cluster: cluster0
    channel_map:
      drive: {out0: I}
      boost: {out0: Q}
    output_offsets: {out0: 0.125}
```

`type` is `QCM`, `QRM`, `QCM-RF` or `QRM-RF`. An RF output takes both paths of one sequencer, written `{out0: IQ}`, and is drawn as a complex envelope. Add `lo_frequency_hz: {out0: 5.2e9}` to label it with its LO. A sequencer that is not routed to a port still shows both of its paths.

### Parameters without a value

Outputs lists every parameter the output depends on. A parameter the project does not set is drawn with an assumed value. Outputs names those parameters above the lanes and marks everything that depends on them in amber: the value, the formula tags and the loop brackets. Type a value to see the output with it. This does not change the project. **Use project values** discards what you typed.

### Long outputs

When a span holds more samples than the screen has columns, each column shows the range the output covers there, before any digital filter. Zoom in to see every sample. A loop that repeats more often than fits on screen is drawn once and labeled with its count.

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
