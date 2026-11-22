# Execution modes

Q1Lens can work out what a program does in three ways. They trade speed and generality against detail.

| Mode | How it works | Use it to |
| --- | --- | --- |
| **Analysis** | Reads the program without running every iteration. Loops are summarized and unknown values stay symbolic. | Get a fast overview while you edit. This is the default. |
| **Concrete** | Executes each instruction of one sequencer with real register values, up to a step limit. | See exact register values, loop counts and branch decisions. |
| **Simulation** | Runs all sequencers on one event clock, with the RT queue, register delays, triggers and feedback between sequencers. | Check queue behavior and interactions between sequencers. |

## Choose a mode

Set it in the project:

```yaml
analysis:
  execution_mode: simulation
  max_steps: 10000
```

Or override the project in VS Code with `q1lens.q1timeline.executionMode`:

| Value | Meaning |
| --- | --- |
| `project` | Use the mode in `q1timeline.yml` (default) |
| `analysis`, `concrete`, `simulation` | Use this mode for every preview |

Refresh the preview after you change the setting. `q1lens.q1timeline.maxSteps` sets the step limit when the setting overrides the project.

## What Analysis leaves open

Analysis does not guess. When a branch depends on a measurement result, or a loop count on an input, it shows the branch as unresolved or the loop as a preview. You then choose a path on the timeline, or switch to Concrete or Simulation to execute it. See [Branches](timeline.md#branches) and [Loops](timeline.md#loops).

## What Concrete adds

Concrete follows the real path through the program. It needs every input to have a value. It stops at a feedback receive whose word has not been supplied, and reports what is missing. Supply those arrivals as [external inputs](feedback-and-inputs.md#external-inputs).

The **Sweep** panel uses Concrete when the project is set to Analysis.

## What Simulation adds

Simulation models time across the whole project:

- A full queue stalls the Q1 core.
- An empty queue while Q1 is still running is an underflow.
- A `wait_sync` waits for every participating sequencer.
- Feedback and triggers travel along the routes you declare, with the latency you give them.

By default the Q1 core fills the RT queue before the RT core starts. To model a fixed start time instead, set `startup_delay_ns`:

```yaml
simulation:
  startup_delay_ns: 200
  max_time_ns: 5000
```

A simulation stops at its time limit and keeps the partial trace. The default limit is 3 ms, or 1 ms with a fixed start.

Simulation can also compute sampled signals through DAC, filters and readout when you describe them under `simulation.physical`. See [Event-driven simulation](../simulation.md).

## Queue assumptions

Analysis and Concrete do not simulate startup, so their underflow checks rest on an assumption: the RT queue holds 32 commands and starts with about 1 ms of buffered time.

| Finding | Meaning |
| --- | --- |
| No diagnostic | The supply gaps fit inside the assumed 1 ms |
| Information, `possibly_safe_with_initial_buffer` | The gaps need more than 1 ms but fit in a looser 2 ms scenario |
| **Possible RT underflow**, warning | An underflow that depends on an unresolved input or branch |
| **RT underflow**, error | The queue runs empty on a confirmed path |

The timeline shows `Initial buffer ≈ 1 ms` next to a queue warning or error to remind you which assumption it rests on. These figures are modeling assumptions, not measurements.

## Play

The Run button in the timeline toolbar plays an execution forward in time. It follows the path you choose at each open branch and pauses when it needs a trigger or a feedback word you have not supplied. The status next to the button says why it stopped, for example **Waiting for external input**.
