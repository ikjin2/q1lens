# Set up a project

A single `.q1asm` file is enough to open a timeline. A project file, `q1timeline.yml`, tells Q1Lens about everything around the source: the other sequencers, the parameter values, the waveforms and the hardware.

## Without a project file

When Q1Lens finds no `q1timeline.yml` in the folder or above it, it creates a temporary one in a `.q1timeline` folder next to your source. That project includes every `.q1asm` file in the folder. If the sources use `{NAME}` parameters, Q1Lens also writes placeholder values for them.

- Run **Q1Lens: Select Q1ASM Files in Folder...** to choose which files to include.
- The `.q1timeline` folder is generated. You can delete it at any time.

## A minimal project

Create `q1timeline.yml` next to your sources:

```yaml
sequencers:
  - id: drive
    name: Drive
    file: drive.q1asm
    module: QCM0
```

| Sequencer key | Meaning |
| --- | --- |
| `file` | The Q1ASM source. Required. |
| `id` | The name other settings use to refer to this sequencer |
| `name` | The label on the timeline row |
| `module` | The physical module. Sequencers with the same value share one module. |
| `module_type` | The kind of module, such as `QCM` or `QRM` |
| `sequencer_index` | The sequencer's index on its module, 0 to 5 |
| `sequence_json` | A Qblox sequence file with the waveforms, weights and acquisitions |
| `hardware_parameters` | Instrument settings the model needs, such as `integration_length_acq` and `sync_en` |

If a workspace has several project files, run **Q1Lens: Select Project File** to pick one.

## Parameters

Use parameters to change values without editing the Q1ASM. Write a placeholder in the source:

```asm
.DEF DRIVE_WAIT {DRIVE_WAIT}
wait $DRIVE_WAIT
play 0,1,$DRIVE_DURATION
```

Put the values in a JSON file:

```json
{ "DRIVE_WAIT": 96, "DRIVE_DURATION": 72 }
```

Point the project at it:

```yaml
params:
  file: params.json
```

A plain `.DEF DRIVE_WAIT 80` fixes the value in the source. A `{DRIVE_WAIT}` placeholder takes it from the project.

To try a value for the current session, run **Q1Lens: Set Analysis Parameter...** and enter a name and a value. **Q1Lens: Clear Analysis Parameter Overrides** removes all of them. The files are not changed.

## Alignment

`alignment.mode` chooses the event that all sequencers share as time zero on the **Aligned** time basis.

| Mode | Time zero is |
| --- | --- |
| `first_wait_sync` | The first `wait_sync`. This is the default. |
| `after_first_wait_sync` | The end of the first `wait_sync` |
| `first_wait_trigger` | The first `wait_trigger` |
| `first_play`, `first_acquire`, `first_marker_rise` | The first event of that kind |
| `label:<name>` | The instruction at that label |
| `manual:<ns>` | A fixed offset |
| `none` | No shared anchor |

A sequencer without the anchor gets an "Alignment anchor was not found" warning.

## Analysis options

```yaml
analysis:
  execution_mode: analysis
  max_steps: 10000
  branch_policy: collapse_unresolved
  feedback_transport_delay_ns: 200
```

| Key | Meaning |
| --- | --- |
| `execution_mode` | `analysis`, `concrete` or `simulation`. See [Execution modes](execution-modes.md). |
| `max_steps` | The most instructions to execute per sequencer |
| `branch_policy` | What to draw for a branch that cannot be resolved: `collapse_unresolved`, `assume_true`, `assume_false`, `assume_fallthrough` or `explore_both_with_depth_limit` |
| `feedback_transport_delay_ns` | Extra delay assumed for feedback between sequencers. See [Feedback and external inputs](feedback-and-inputs.md). |

## View options

```yaml
view:
  default_mode: normal
```

`default_mode` opens the timeline in `normal` or `debug`.

## More sections

| Section | Purpose | Described in |
| --- | --- | --- |
| `external_inputs` | Triggers and feedback words that arrive from outside | [Feedback and external inputs](feedback-and-inputs.md) |
| `simulation` | Routes, scheduled inputs and the signal model for Simulation | [Event-driven simulation](../simulation.md) |
| `modules` | Which sequencer paths drive which output ports | [Hardware view](hardware.md#outputs) |

## Examples

The tutorial folder [03-params-basics](../../examples/q1asm-tutorials/README.md) has four small projects that use `.DEF` and `params.json`.
