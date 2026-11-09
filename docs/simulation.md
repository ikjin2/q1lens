# Event-driven simulation

Select `analysis.execution_mode: simulation` in `q1timeline.yml`, or pass
`--execution-mode simulation` to `q1lens q1timeline analyze`. In VS Code,
`q1lens.q1timeline.executionMode: project` follows the YAML. The independent
`concrete` and compact `analysis` modes keep their existing behavior.

The simulator uses Q1Lens instruction semantics and a shared discrete-event clock.
It has no q1simulator dependency. Register writes become readable after their
modeled latency, packets capture operands/settings when issued, full queues stall
Q1, and uncovered RT supply gaps produce underflow. `stop` stops Q1 and drains the RT
queue. All project sequencers participate in the modeled `wait_sync` barrier.
The trace records flags, branches, iterations, reads, writes and their provenance.

Analytical and path-playback queue checks model a finite 32-packet RT FIFO and
assume 1 ms of initial RT buffer time. This is an unmeasured modeling assumption:
the first packet still starts immediately, and the credit is consumed only when
Q1 leaves a gap in RT supply. It is shared across the whole sequencer trace and
does not renew at each packet or synchronization. Full queues still stall Q1;
original Q1/RT display coordinates and shared trigger-arrival times stay unchanged.

Supply gaps covered by the assumed 1 ms do not produce underflow warnings. If
they need more than 1 ms but fit within the tentative 2 ms scenario, the timeline
reports `possibly_safe_with_initial_buffer` and an informational estimate. The
2 ms scenario also allows prefetch at `wait_sync`. A gap exceeding that scenario
still produces an underflow diagnostic. Confirmed RT underflows and recorded-path
playback underflows are errors; possible underflows remain warnings. Analysis
with an error returns exit code 2 while retaining its timeline and diagnostics.
The timeline shows lateness as a positive duration, such as `24 ns late`.
Unknown branches, inputs, or unfinished
traces remain incomplete; neither buffer scenario verifies hardware behavior.

By default the simulator lets Q1 prefill the RT queue. The prefill window opens
at the first RT enqueue. RT starts when the queue fills, Q1 finishes, Q1 blocks
on feedback, or the tentative 2 ms prefill window expires. Prefill beyond 1 ms
produces an informational diagnostic. Once RT starts, it executes at the programmed
cadence and reports underflow if its queue empties while Q1 is still running.
Scheduled inputs with `time_ns` use the shared simulation clock. For a trigger
relative to its receiver's first RT instruction, use `rt_time_ns` in place of
`time_ns`. Align on `rt_queue_dequeue` with `first_anchor` to show RT start at
0 ns. The Q1 lane labels the prefill assumption.

Set `startup_delay_ns` to run a fixed-start scenario with immediate underflow
when the RT queue is empty. The default time limit is 3 ms for buffered runs and
1 ms for fixed-start runs. Physical simulations need sample periods and budgets
that cover their full scenario window.

## Scenario configuration

```yaml
analysis:
  execution_mode: simulation
  max_steps: 10000
alignment:
  mode: none
simulation:
  queue_capacity: 32
  feedback_queue_capacity: 32
  max_time_ns: 1000000
  routes:
    - {source: sender, target: receiver, id: 16, latency_ns: 150}
  inputs:
    feedback:
      - {target: receiver, time_ns: 400, id: 17, value: 42}
    triggers:
      - {target: receiver, time_ns: 600, address: 1}
    acquisitions:
      - {sequencer: readout, acquisition_index: 0, bin_index: 0, thresholded: 1}
```

IDs must reference project sequencers. Route latencies are explicit scenario
values; no instrument topology is inferred. Omitting `id` matches any nonzero
feedback ID from the source. Scheduled input times use the shared unaligned clock.

## LINQ arrival estimates in analysis

Analytical feedback arrows and early pop/pull warnings use the same per-flow
arrival estimate. The base delays come from the [Qblox LINQ latency table](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#latencies-of-linq-based-feedback-data-types).
IDs 1–15 use self-cast; IDs 16–255 use intra-cast when both sequencers have the
same configured `module`, otherwise the multi-cast worst-case estimate.

| Data type | Self-cast (ns) | Intra-cast (ns) | Multi-cast (ns) |
| --- | ---: | ---: | ---: |
| Q1 register / immediate | 60 | 150 | 380 |
| Thresholded bits | 160 | 250 | 472 |
| IQ values | 164 | 270 | 492 |
| High-resolution time tags | 910 | 1000 | 1260 |
| Time delta | 910 | 1000 | 1260 |
| TTL counts | 146 | 236 | 480 |
| Low-latency time tags | 146 | 236 | 480 |

The site's additional transport allowance defaults to **200 ns for non-self
routes**, including intra-cast. This is an experimental setup assumption, separate
from the official base delays. Adjust it in `q1timeline.yml`:

```yaml
analysis:
  feedback_transport_delay_ns: 200
```

Self-cast always adds zero. Explicit simulation `routes[].latency_ns` already
specifies the complete route delay; the analysis allowance is not added again.

Readout TB/IQ emission uses the configured integration end, not the acquire
instruction's RT duration. TTL count and time-delta packets use the time-tag
window close. Streaming time tags require an actual tag emission time: without
that input, analysis reports unknown timing and omits a numeric arrival marker.
Unknown readout integration length and unmodeled nonzero QTM fine delay are
handled the same way.

A pop/pull before estimated arrival raises a warning because Q1 may block.
Blocking alone is valid; the event-driven simulator reports an error only if RT
work actually runs out while Q1 waits. The arrival marker does not move to the
later Q1 consumption time.
Display alignment does not change execution.

`fb_pop_data` and `fb_pull_data` wait for matching IDs, discarding earlier FIFO
entries. The pull form reads its first register as the requested ID. Empty input
queues can stall Q1 while RT continues and may underflow. Trigger counters start
disabled, use threshold one without inversion, and require `set_latch_en`.
`wait_trigger` accepts arrivals at or after its RT start. `set_cond` checks the
modeled counters before each RT instruction. A false condition skips that
instruction and its output effects, consuming `else_duration`; Q1 instructions
continue normally. `set_cond 0,...` disables this check. Operators 0–5 are modeled;
6/7 block execution only when the condition is enabled. `else_duration` must be
in 0–65535 ns. These assumptions are retained in the IR; other trigger thresholds
and inversion settings are not modeled.

An acquisition input is consumed once by a matching sequencer/acquisition/bin.
It takes precedence over a physical model result. `fb_acq_tb_id` emits a payload
containing threshold and valid bits. To transmit modeled I/Q integrals with
`fb_acq_iq_id`, supply an explicit conversion:

```yaml
simulation:
  acquisition_iq_scale:
    readout: 32768 # raw integer words per normalized full scale * ns
  trigger_routes:
    - {source: readout, target: receiver, address: 1, latency_ns: 100, thresholded: 1}
```

Without an explicit scale, modeled integrals cannot be treated as raw firmware
integers. Acquisition-trigger routes emit only when the result equals the
configured `thresholded` value. Raw integer I/Q scenario inputs can be routed
directly. Routing and conversion are model settings, not inferred calibration.

## Physical model configuration

Numerical signals are opt-in through `simulation.physical`; waveform, weight and
acquisition memory comes from each sequencer's `sequence_json`.

```yaml
simulation:
  physical:
    sample_period_ns: 1
    max_samples: 100000
    max_export_samples: 2000
    seed: 0
    sequencers:
      drive:
        dac_bits: 16
        gain: [1, 1]
        offset: [0, 0]
        nco_frequency_hz: 0
        lo_frequency_hz: 5000000000
        fir: [0.5, 0.5]
        iir: {b: [1], a: [1]}
      readout:
        adc_bits: 12
        integration_length_ns: 64
        threshold: 16
        threshold_rotation_deg: 0
        demodulate: false
        input:
          kind: loopback
          sequencer: drive
          delay_ns: 0
          gain: 1
          phase_turns: 0
          noise_std: 0.01
```

Inputs also accept `{kind: constant, i: 0.25, q: 0}` or a supplied trace:

```yaml
input:
  kind: trace
  i: [0.125, 0.125, 0.125, 0.125]
  q: [0, 0, 0, 0]
  sample_period_ns: 1
  start_ns: 204
  source: my-measurement-description
  measured: true
  noise_std: 0
```

`measured` is the user's provenance claim. The simulator does not obtain or verify
hardware measurements. Missing samples prevent an acquisition result. Signal
stages expose normalized full-scale values; acquisition `i/q` are integrals in
full scale × ns, with `mean_i/mean_q` separately available. Thresholds compare the
rotated integral before normalization. Configure integration length explicitly;
without it the unweighted numerical model uses the RT instruction duration and
labels that fallback, rather than claiming the instrument's default setting.

The numerical pipeline models gain/offset, complex NCO, FIR/IIR, clipped/quantized
DAC, loopback gain/phase/delay, seeded Gaussian noise, quantized ADC, optional NCO
demodulation and integration weights. RF is an ideal complex envelope tagged with
LO frequency. GHz carrier samples, mixer imbalance, measured frequency response,
hardware filter delays, noise spectra and quantum-device dynamics require separate
models/calibration. Explicit sample caps bound computation and trace export;
truncation and export stride are visible in metadata.

Physical export is a snapshot of all samples and results already calculated.
Its end time requests additional sampling; it does not rewind or discard history.
`sampled_end_ns` records the greatest rendered horizon. The Hardware cursor
separately filters register/acquisition state at the selected time.
Unknown nested scenario keys and physical-input fields that do not apply to the
selected input kind are rejected with their configuration path.

## Results and limits

`TimelineIR.execution[seq]` records status, steps, reason, timing/address model and
assumptions. `TimelineIR.simulation[seq]` records queue settings, acquisitions and
signal traces. Actual feedback/trigger arrows link their source and arrival events.
The Hardware view exposes queues, stalls, inputs, signal stages and completed bins
alongside register history and pending/applied settings.

Execution is bounded by instruction and time limits. Unknown inputs and unsupported
instructions stop explicitly. Source PCs are not assembled firmware addresses:
numeric/register branch addresses remain unsupported when legacy macro expansion
would change them. QTM-specific protocols, write-combine feedback formats and
undocumented firmware behavior are outside the current instruction model.

Use the [verification workspace](../examples/simulation-verification/README.md)
for runnable examples and expected values, and [continuous validation](ci-validation.md)
for the automated checks. Passing these checks validates the software model, not
equivalence to a physical instrument.
