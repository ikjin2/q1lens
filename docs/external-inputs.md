# Shared external inputs in path playback

The main Play control accepts project-scoped external trigger scenarios in
`q1timeline.yml`. These are explicit assumptions for analytical path playback;
the Hardware event-driven simulation continues to use `simulation.inputs`.

```yaml
external_inputs:
  triggers:
    - id: shot_start
      address: TRIGGER_INDEX
      targets: [drive, readout]
      times_ns: [1000]
```

`address` is an integer from 1 to 15 or a project parameter name. Parameter
overrides resolve both the input address and Q1ASM references. `targets` is an
explicit list of project sequencer IDs; hiding a lane does not disconnect it.
`times_ns` is a strictly increasing list of nonnegative integer receiver-arrival
times on the shared **unaligned analytical RT clock**. An empty list supplies no
events. Sources must have unique, nonempty IDs. Each occurrence has one stable
ID shared by every recipient, and each recipient can use it only once.

The Scenario input summary beside Play opens Configure. Apply & rerun validates
the entire configuration before replacing the active scenario and recomputes the
trace from the start. Branch and feedback choices are retained. Unsaved changes
are marked explicitly. Save to project persists the applied configuration; Reset
returns to the saved one and reruns. Saving is available for YAML projects.

## Timing semantics

The [Qblox Q1ASM 2026.10.0 wait_trigger description](https://docs.qblox.com/en/v2026.10.0/products/qblox_instruments/q1/index.html#wait-trigger-instt)
specifies waiting for the addressed trigger, followed by the duration operand.
Accordingly `wait_trigger 1,2000` with an arrival at 1000 ns ends at 3000 ns.
The older 2026.04 guide's timeout wording is not the model used here. No missing
trigger is replaced with an invented timeout or automatic release.

An arrival before the analytical wait start cannot release that wait; an arrival
at the start can. The earliest unused matching arrival is selected independently
for each recipient. There is no implicit wait-for-all barrier. A sequencer with
no matching future arrival remains waiting while other sequencers can proceed.
The wait inspector records source/event identity, address, arm/arrival/resume
times, and the reason when no arrival matches. Display alignment never changes
this matching.

The duration operand does not include trigger-network transport delay. Scenario
times already describe arrivals; physical trigger distribution, startup and RT
queue backpressure are not simulated by path playback. Queue diagnostics use a
finite 32-packet FIFO, assume 1 ms of initial buffer time, and show an informational
estimate when only the tentative 2 ms scenario covers Q1 supply gaps. These are
unmeasured assumptions, not a fixed wait before the first RT instruction. Buffer
credit is consumed across the trace and does not renew at each packet or trigger.
The tentative scenario also permits prefetch at `wait_sync`. Trigger arrivals
retain their absolute values on the shared analytical RT clock in both scenarios.
A dequeue after the assumed arrival makes that queue check incomplete. A completed
path is not hardware verification. Existing branch and feedback assumption controls
keep their existing meaning.
