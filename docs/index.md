# Q1Lens user guide

Q1Lens shows what a Q1ASM program does before you run it on a Qblox sequencer. It has two parts that work together:

- The **`q1lens` Python package** parses, executes and verifies Q1ASM.
- The **Q1Lens VS Code extension** shows the result next to your source: a timeline of Q1 and real-time (RT) activity, diagnostics, register values, waveform output and verification results.

A sequencer has two cores. The Q1 core runs your instructions and puts RT commands in a queue. The RT core takes them out on the hardware clock. Most timing bugs come from the gap between when Q1 *issues* a command and when RT *executes* it. Q1Lens draws both on one time axis, so you can see where the queue runs empty, where feedback arrives too late and which source line caused it.

## What you can do

| Task | Chapter |
| --- | --- |
| Install the package and the extension and connect them | [Install](guide/install.md) |
| See a queue underflow found and replayed in five minutes | [Quick start](guide/quick-start.md) |
| Get syntax help, hovers and diagnostics while you write Q1ASM | [Write Q1ASM](guide/editing.md) |
| Read the timeline and move between events and source lines | [Read the timeline](guide/timeline.md) |
| Describe several sequencers, parameters and waveforms | [Set up a project](guide/projects.md) |
| Choose between Analysis, Concrete and Simulation | [Execution modes](guide/execution-modes.md) |
| Work with `wait_sync`, LINQ feedback and triggers | [Feedback and external inputs](guide/feedback-and-inputs.md) |
| See what each feedback-dependent branch decides and changes | [Decision lanes](guide/decision-lanes.md) |
| Inspect port output, acquisitions and scope capture | [Hardware view](guide/hardware.md) |
| Run with other DEF values and sweep them | [DEF runs and sweeps](guide/def-sweep.md) |
| Prove properties or get a counterexample | [Verify a program](guide/verification.md) |
| Open timelines from Qblox Scheduler code and notebooks | [Qblox Scheduler and notebooks](guide/scheduler-notebook.md) |
| Run the same checks in a terminal or in CI | [Command line and CI](guide/cli.md) |

The reference section lists every [command](reference/commands.md) and [setting](reference/settings.md), and has a [troubleshooting](reference/troubleshooting.md) page. The model pages describe exactly what each check assumes.

## What Q1Lens does not do

Q1Lens never talks to an instrument. Every result comes from a software model of the sequencer. A proof or a clean timeline says the program behaves that way *in the model*, under the startup, queue and latency assumptions the result names. It is not a measurement. Where the model has no verified behavior for an instruction or an input is missing, Q1Lens reports the result as unknown or incomplete.
