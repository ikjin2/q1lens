# Shared external inputs in path playback

The main Play control accepts project-scoped trigger and LINQ scenarios in
`q1timeline.yml`. These are explicit assumptions for analytical path playback.
The Hardware event-driven simulation uses `simulation.inputs`.

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
ID shared by every recipient. An occurrence can release one `wait_trigger` per
recipient and also increment its enabled trigger counter once.

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
Each consumed occurrence appears as a point at its arrival on the receiver's
timeline and shares the scenario identity recorded by the wait.
The wait inspector records source/event identity, address, arm/arrival/resume
times, and the reason when no arrival matches. The inspector and Play waiting
summary use the active time basis for each receiver. Configure and TimelineIR
retain the unaligned schedule used for matching.
In Hardware, an armed trigger wait appears on RT and an armed LINQ wait appears
on Q1. A terminal Play error with a known Q1 edge ends that core's observation.
Already recorded RT work keeps its analytical timing.
If a local failure ends project playback, Hardware shows an unfinished peer
as a partial recording. Completed and waiting peers keep their own states.

Play evaluates an enabled `set_cond` before each affected RT packet using these
same arrivals. `set_latch_en` controls counting and `latch_rst` clears the
counters. Each executing packet applies its effect after any arrivals at the
same time. Per-sequencer `hardware_parameters` can set
`triggerN_count_threshold` to an integer from 0 to 65535 and
`triggerN_threshold_invert` to a boolean, for addresses `N` 1 through 15.
The default threshold is one without inversion. An empty `times_ns` list for
an address models zero arrivals there. If a decision depends on a missing
source, Play stops with an incomplete reason. A false decision advances RT by
the else duration while Q1 continues.

The duration operand does not include trigger-network transport delay. Scenario
times already describe arrivals; physical trigger distribution, startup and RT
queue backpressure are not simulated by path playback. Queue diagnostics use a
finite 32-packet FIFO, assume 1 ms of initial buffer time, and show an informational
estimate when only the tentative 2 ms scenario covers Q1 supply gaps. These are
unmeasured assumptions, not a fixed wait before the first RT instruction. Buffer
credit is consumed across the trace and does not renew at each packet or trigger.
The tentative scenario also permits prefetch at `wait_sync`. Trigger arrivals
retain their absolute values on the shared analytical RT clock in both scenarios.
A dequeue after the assumed arrival makes that queue check incomplete. The
queue check also becomes incomplete if shifted dequeue times change a recorded
`set_cond` decision or prevent it from being established. A completed path is
not hardware verification. Existing branch and feedback assumption controls
keep their existing meaning.

## LINQ receive schedules

```yaml
external_inputs:
  feedback:
    - target: readout
      arrivals:
        - {time_ns: 16, id: 9, value: -1}
        - {time_ns: 16, id: 5, value: 123}
```

Each receiver gets one schedule on its own **unaligned analytical Q1 clock**.
Entries are sorted by arrival time. Equal-time entries keep their listed order
and arrive before Q1 can receive at that timestamp. IDs range from 1 to 255.
Words accept signed or unsigned 32-bit integers and are written as raw unsigned
register bits. Configure accepts the same entries as comma-separated time, ID,
and word on each line. An empty receiver schedule represents a known empty FIFO.
The receive queue defaults to 32 entries and uses `simulation.feedback_queue_capacity`
when configured. A schedule that fills the FIFO before Q1 can consume an entry
produces an overflow diagnostic linked to the rejected arrival. The point
follows the active time basis. Play checks arrivals through the completion
of the receiver's `stop` instruction, including arrivals at that completion time.

`fb_pull_data` reads the oldest entry into its ID and data destinations and
takes 8 ns once data is available. `fb_pop_data` discards entries up to the
requested ID and takes 4 ns once that ID is available. The wait delays Q1 and
later enqueues. Already recorded RT work keeps its timing. The inspector shows
the scheduled entry, source instruction, register destinations and discarded
entries. Its timestamps follow the active time basis for the receiver. When a
receiver has no schedule, `fb_pop_data` retains the editable
per-occurrence assumption and `fb_pull_data` reports missing input. Play marks
queue timing incomplete when backpressure shifts Q1 before a scheduled receipt
or leaves a terminal feedback wait unchecked. [Qblox LINQ data receiving](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#data-receiving)
