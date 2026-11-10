# Event-driven simulation

Select `analysis.execution_mode: simulation` in `q1timeline.yml`, or pass
`--execution-mode simulation` to `q1lens q1timeline analyze`. In VS Code,
`q1lens.q1timeline.executionMode: project` follows the YAML. The independent
`concrete` and compact `analysis` modes keep their existing behavior.

The simulator uses Q1Lens instruction semantics and a shared discrete-event clock.
It has no q1simulator dependency. Register writes become readable after their
modeled latency, packets capture operands/settings when issued, full queues stall
Q1, and uncovered RT supply gaps produce underflow. `stop` stops Q1 and drains the RT
queue. The modeled `wait_sync` barrier includes every project sequencer by
default. Set `hardware_parameters.sync_en: false` on an independent sequencer
to exclude it. Each enabled peer must execute the matching barrier before RT
continues. An excluded sequencer that executes `wait_sync` reports an
unsupported configuration at that instruction.
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
`time_ns`. The default display origin is the earliest RT dequeue across the
project. Normal starts at 0 ns and Debug can show negative Q1 preparation
times. A Q1-only run uses simulation time zero. An explicit alignment anchor
uses the earliest concrete matching time across the project, with ties broken
by sequencer ID and then event ID. `label:<name>` uses the labeled instruction's
RT execution edge. `first_wait_trigger` and `first_wait_sync` can use a pending
wait's arm time. `after_first_wait_sync` uses the end of a completed barrier.
A missing anchor falls back to the first RT dequeue when one exists. When
alignment selects that dequeue, Normal starts at 0 ns. For
another resolved RT anchor, Normal starts at the earliest visible RT event,
including events before the anchor. With either RT origin, Normal ends at the
latest visible RT activity. Debug includes the full Q1 and RT extent.
Proven Q1-only loop summaries are available in Debug. Normal retains loops
that enqueue RT packets, including zero-duration packets.
Explicit `none` or `manual:<offset>` controls the shared display offset. Scenario
inputs and execution retain their original clock. The Q1 lane labels the prefill
assumption.

Set `startup_delay_ns` to run a fixed-start scenario with immediate underflow
when an RT-producing sequencer reaches the fixed start with an empty queue.
Q1-only runs keep RT idle. The default time limit is 3 ms for buffered runs and
1 ms for fixed-start runs. A time-limited trace ends active RT packets and Q1
issues at the observation cutoff and records their planned completion separately.
Only register writes ready by the cutoff enter the readable history. Future latch
changes are recorded as planned data on the interrupted issue. Physical simulations
need sample periods and budgets that cover their full scenario window.

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
IDs 1–15 use self-cast. Their words enter only the originating sequencer's FIFO.
Analysis uses estimated arrival order for pulls, selective pops, and discards,
keeping I before Q within one IQ transmission. Consecutive readout results
of the same type keep their order when absolute arrival times are unknown.
A unique source can still be linked when the receive is early or its
arrival time is unknown. If competing words have unresolved order, the
affected matches and discards remain incomplete. An unmatched selective pop
reports a route mismatch when its ID appears only on other sequencers. IDs
16–255 use intra-cast when both sequencers have the same configured `module`,
otherwise the multi-cast
worst-case estimate. Ordering between inferred non-self routes and self-cast
keeps the existing source-order inference.

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
Single-word TB combine estimates can use a common SYNQ release and validated
project routes to count one FIFO word for compatible same-time contributions.
Both acquisition sources remain available. An unresolved combine round keeps
its FIFO word count and matches incomplete.

Readout TB and IQ IDs are independent. An acquisition can emit one TB payload
and two IQ payloads in I, Q order, with separate arrival estimates tied to the
same acquisition. ID zero disables the corresponding stream. A TB validation
bit update leaves its ID in place. Both readout streams use the configured
integration end, independent of the RT instruction duration.

QTM TTL count and time-delta streams each use the window close. Individual
streaming time tags need actual emission times. Analysis leaves their arrival
times unknown when those observations are absent. Missing readout integration
length and unmodeled nonzero QTM fine delay also leave arrival times unknown.
See the [Qblox LINQ data types](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#data-sharing).

When a LINQ setting's payload format is unverified, Analysis keeps the programmed
RT duration and reports an incomplete FIFO balance for affected sends. A later
supported setting restores estimates for new sends. Earlier unresolved FIFO
words can still affect receive order and matching.

A pop/pull before estimated arrival raises a warning because Q1 may block.
Blocking alone is valid; the event-driven simulator reports an error only if RT
work actually runs out while Q1 waits. The arrival marker does not move to the
later Q1 consumption time. A routed FIFO rejection's inspector can reveal its
sender when the trace identifies the sending RT command.
Display alignment does not change execution.

`fb_pop_data id, Rdata` discards entries ahead of the requested ID, waits if
the matching entry has yet to arrive, and writes its payload to `Rdata`. It
takes 4 ns on Q1 once the entry is available. `fb_pull_data Rid, Rdata` takes
the oldest available FIFO entry regardless of ID and writes its ID to `Rid`
and its payload to `Rdata`. Both operands are destination registers. It waits
when the queue is empty and takes 8 ns on Q1 once an entry is available. Q1
can stall while RT continues and may underflow. See the [Qblox LINQ receive
semantics](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#data-receiving).
Analysis, Simulation and scheduled Play report unresolved firmware behavior for
a selective pop above 255. Analysis retains earlier FIFO matches and leaves
the affected receiver's remaining FIFO state incomplete.

Trigger counters start disabled and require `set_latch_en`. For each address
`N` from 1 to 15, project sequencer `hardware_parameters` may set
`triggerN_count_threshold` to an integer from 0 to 65535 and
`triggerN_threshold_invert` to a boolean. The default threshold is one without
inversion. The comparison uses greater than or equal, then applies inversion.
`wait_trigger` accepts arrivals at or after its RT start. A blocked RT wait's
diagnostic links to the recorded wait. Its tooltip and inspector show the arm
time, unresolved completion and observation cutoff. A completed trigger or
sync wait shows its arm, release and completion in one command slot. The
trigger arrival also stays visible as a point.
Coincident trigger arrivals share a point whose tooltip lists the addresses and
counts. In VS Code, a consumed scenario arrival can reveal its source command.
`set_cond` checks the modeled counters before each RT instruction. A false
condition skips that instruction and its output effects, consuming
`else_duration`. Q1 instructions continue normally. `set_cond 0,...` disables
this check. Operators 0–5 are modeled. Operators 6 and 7 block execution only
when the condition is enabled. `else_duration` must be
in 0–65535 ns. These settings and assumptions are retained in the IR.
The condition inspector links each recorded Execute or Skip decision to its
RT packet in the active time basis. An armed trigger or SYNQ wait retains
its Execute decision while the release is unresolved.

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

Numerical integrals need an explicit scale to convert them to accumulator
values. Raw integer I/Q scenario inputs can be supplied directly. The
simulator validates a signed 48-bit accumulator, applies the captured
`fb_acq_iq_shift`, and sends the resulting 32-bit words in I/Q order. The
signed shift extends bit 47 beyond the accumulator as a model assumption.
Acquisition-trigger routes match the configured `thresholded` value. A supplied
I/Q result without a thresholded bit retains its acquisition data and contributes
possible trigger arrivals. Counter bounds treat the bit values as alternatives.
When the entire acquisition result is unavailable, the sender blocks and
the declared routes still contribute possible arrivals from its completion edge.
Matching fixed routes for both values prove a single arrival at the shared edge.
Multiple independent unknown acquisitions use conservative per-address bounds.
A possible request on a shared trigger network can make a pending delivery
uncertain without establishing a collision.
Natural completion can affect RT decisions on the same tick. A zero-delay
trigger produced by an RT acquisition interruption keeps the same timestamp
and arrives after the RT decisions on that tick.
Routing and conversion are explicit model settings.
See [Qblox LINQ IQ values](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#iq-values).

### Weighted integration in analytical timelines

Analysis, concrete execution, and Play resolve the weight operands at each
`acquire_weighted` or `acquire_weighed` instruction. Two valid uploaded weights
of the same length give an integration duration of one nanosecond per sample.
The timeline uses the project `sequence_json` weights and keeps the RT packet
duration, unweighted integration setting, and scope capture independent. A
new readout acquisition can end the preceding integration early. Analytical
timing preserves a completed window when the confirmed RT prefix puts
later uncertainty beyond its end. LINQ uses the integration end for its
send-time estimate. Missing, ambiguous, or unequal weights leave both the
integration end and feedback arrival unknown. See the [Qblox weighted acquisition tutorial](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QRM/020_binned_acquisition.html#weighted-acquisition).

When an analytical path reaches `illegal`, its metadata retains the
nominal integration and scope lengths. Effective completion and LINQ
availability remain unresolved because the Q1 fault and RT execution
lack a shared timing model.

In event-driven simulation, a supplied result or an acquisition without a
modeled input can use the instruction duration when weight memory is unavailable.
Numerical weighted integration requires valid weights for the acquiring sequencer.

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
rotated integral before normalization. For unweighted acquisitions, an explicit
`simulation.physical.sequencers[id].integration_length_ns` overrides the
sequencer's `hardware_parameters.integration_length_acq`. With neither set,
the simulator uses the positive RT instruction duration and labels that
fallback. The same timing applies to supplied results and numerical integration.
Weighted acquisition uses the weight lengths when available. A zero-duration
RT acquisition needs a configured integration length or integration weights.
The acquisition block records its modeled integration window separately
from RT packet spacing and scope. Execution that ends before integration
completes leaves an incomplete window through the sequencer's terminal edge.
Hardware Bins shows the number of modeled I/Q windows, their mean integrals,
and their threshold count as of the cursor. Scenario values are per-window
outcomes. When a window completes without a physical or supplied result, the
simulator records the unavailable outcome with or without a trigger route.
Hardware shows its reason and observation time while preserving any previous
modeled aggregate.

The numerical pipeline models gain/offset, complex NCO, FIR/IIR, clipped/quantized
DAC, loopback gain/phase/delay, seeded Gaussian noise, quantized ADC, optional NCO
demodulation and integration weights. RF is an ideal complex envelope tagged with
LO frequency. GHz carrier samples, mixer imbalance, measured frequency response,
hardware filter delays, noise spectra and quantum-device dynamics require separate
models/calibration. Explicit sample caps bound computation and trace export;
truncation and export stride are visible in metadata.
Timing-only and digital-only RT effects retain their fractional coordinates
with the numerical model enabled. Sampled waveform and state changes still
use integer-nanosecond event timestamps.

`reset_ph` and `set_ph_delta` are captured once by the next latch-updating
packet. Plain waits leave them pending. A conditional skip records the
captured setter but leaves the NCO unchanged. `set_ph_delta` adds to the
accumulated phase, while `set_ph` replaces the separate phase offset. Frequency
changes keep phase continuous. When one packet captures a reset and an explicit
offset or kick, the numerical model resets first and then applies those values.
See the [Qblox NCO instruction reference](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#latched-instructions).

Physical export is a snapshot of all samples and results already calculated.
Its end time requests additional sampling; it does not rewind or discard history.
`sampled_end_ns` records the greatest rendered horizon. The Hardware cursor
separately filters register/acquisition state at the selected time.
Unknown nested scenario keys and physical-input fields that do not apply to the
selected input kind are rejected with their configuration path.

## Results and limits

### Zero-duration packets and full-product multiplication

An RT duration of zero executes the packet once, suspends the underflow guard,
and attempts the next dequeue 4 ns later. An empty FIFO then waits for enqueue
without polling. A nonzero packet restores the guard; 1–3 ns durations remain
invalid. The 4 ns starts at barrier release for `wait_sync 0` and at the matching
arrival for `wait_trigger addr,0`. A skipped packet uses its selected else
duration and applies no output, acquisition, or feedback effects. Packet metadata
keeps `operand_snapshot`, `queue_duration_ns`, `rt_execution_ns`, and
`next_dequeue_not_before_ns` separate. The timeline shows the earliest eligible
dequeue in the active time basis. The following packet can arrive later.

The assembler can encode 1–3 ns. Analysis reports those values as invalid RT
dwells and leaves the affected timeline incomplete. Concrete execution and Play
report an error at the offending instruction. The same rule applies to a
resolved duration and to the `set_cond` else duration.
[Qblox RT core reference](https://docs.qblox.com/en/main/products/architecture/sequencers/sequencer.html#real-time-core)

A zero-width packet and its duration stay zero in Analysis, Concrete execution,
and Play. Their analytical RT dequeue cursor uses the 4 ns floor after zero.
For `wait_trigger addr,0`, Play adds the 4 ns after the selected arrival.
Static analysis leaves arrival availability unknown. Play evaluates trigger
counters and conditions at the updated cursor. Compact loop and branch totals
include known zero-duration gaps. Totals remain uncertain when the number of
zero-duration iterations cannot be proven. A terminal `stop` after a
zero-duration packet uses the nominal empty-FIFO check. Queue replay can still
find later dequeues due to packet availability or synchronization.

`mulu32` and `muls32` execute on Q1 in 24 ns, with both destinations readable
4 ns after issue completion. Both sources are read before either destination is
written. Identical physical destinations are rejected because write priority is
unverified. Analysis keeps that destination unknown. ZF uses the full product,
NF uses bit 63, and CF/OF indicate overflow outside the selected signed/unsigned
32-bit range. This interprets the reference's overflow wording; hardware flag
parity has not been verified.

### Net-zero output model

`play_netzero maxt,duration` captures `maxt` at issue and updates latches at RT
execution. Queue duration and compensation end are independent. Without an output
model, compensation is explicitly unmodeled and the conservative `maxt` overlap
warning remains available. Configure each eligible baseband QCM output explicitly:

```yaml
simulation:
  physical:
    sample_period_ns: 1
    max_samples: 20000
    sequencers:
      drive:
        netzero:
          module_type: QCM
          outputs:
            - output: 0
              path: 0
              enabled: true
              ihp_enabled: true
              amplitude: 0.5
              slew_rate_per_s: 2000000
```

The ideal model accumulates signed area per mapped path at the output of the
configured FIR/IIR, before DAC quantization, in normalized full scale × ns. It
replaces that path with an opposite-area sampled pulse, bounded by amplitude,
slew, and `maxt`. This is a numerical area model, not the hardware pulse generator.
An explicitly disabled output generates no compensation. Missing settings,
insufficient capacity, unsupported retriggering, and off-grid compensation starts
have explicit results. Waveform memory is still required for `play`; offset-only
sequences need no waveform samples. A zero-area accumulator produces no pulse.

`reset_netzero` is a one-shot Q1 latch carried by the next latch-updating packet.
Plain waits leave it pending; a conditional skip discards the captured reset.
Mapped outputs reset only when `ihp_enabled` is true. The model tracks the ideal
IHP accumulator separately; it does not implement the hardware IHP transfer
function or reset generic FIR/IIR or NCO state. Exact IHP, quantization, overflow,
and retrigger parity require hardware vectors. Results are retained in
`simulation[seq].netzero` in both traced and prepared execution, with waveform
end events independent of RT drain. If a time limit or sequencer failure
interrupts observation before the completion callback, the incomplete result
contains the observed samples and area. It keeps the planned end and planned
area separately. The timeline inspector shows both ends in the active time
basis and lists both compensation areas. A longer peer can extend the
project's numerical signal traces. The numerical fixture follows the pulse areas
in the [Qblox worked example](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QCM/202_net_zeroing.html).

### Digital acquisition inputs

Digital traces are separate from analog/IQ inputs. Timestamps are integer ticks
of 1/128 ns on the shared simulation clock. Transitions must strictly increase
in time and change the binary level. An explicit empty transition list supplies
a known flat signal. With no trace, a routed QTM edge or QRM TTL gate supplies a
possible trigger interval from effective open up to the exclusive close. A fixed
route shifts both boundaries by its latency. The receiver accounts for latch
enable and reset without inventing edge counts or arrivals.
With a supplied TTL aggregate, each route is bounded by that count. Zero
excludes any transmission. A normally closed fixed-route window can establish
the count once the receiver latch covers the whole arrival interval. Individual
arrival times remain unknown.

Hardware shows TTL and QTM gates as open between their effective start and
exclusive end. QTM fine delays move those edges independently of the RT
packets. When execution ends with a gate open, the recorded cutoff appears
as an incomplete gate. Digital level samples are observations at their
recorded tick.

```yaml
simulation:
  startup_delay_ns: 100
  inputs:
    digital:
      readout:
        initial_level: 0
        transitions:
          - {time_ticks: 13312, level: 1} # 104 ns
          - {time_ticks: 13440, level: 0} # 105 ns
          - {time_ticks: 14848, level: 1} # 116 ns
          - {time_ticks: 14976, level: 0} # 117 ns
  digital:
    readout:
      time_source: first
      time_reference: start
      ttl_acq_auto_bin_incr_en: false
      count_thresholds: {low: 1, high: 3}
```

The sequence memory must define the addressed acquisition and its `num_bins`.
`acquire_timetags` uses an ideal half-open window `[open, close)`. Each boundary
is the packet's RT execution time plus its own nominal `fine_delay` in 1/128 ns
ticks. The closing operands select the result acquisition and bin. Packet timing
is independent of the effective boundaries. A scheduled close can finish after
RT drains. Equal or reversed effective boundaries are unsupported. Hardware
fine-delay quantization, TDC dead-time and analog edge detection are unverified.

`time_source` selects `first`, `second`, or `last`. `time_reference` selects
`start`, `end`, `sequencer`, or `first0` through `first7`. The defaults are
`first` and `start`. `firstN` uses explicit `module` and `channel` values in
`simulation.digital` to identify channel N on the same module. A reference to
the current channel uses the current window's first edge. A different channel
uses the first edge of its latest closed window. Coincident cross-channel
closes leave the reference unavailable. `set_time_ref` captures a one-shot flag
for the next latch update in `sequencer` mode, including a close packet.

Results retain counts, effective boundary ticks, selected delta and edge
provenance. An observed empty window has count zero and no valid delta. The
optional `invalid_time_delta` policy applies to observed invalid results.
`record_0` records a zero with `valid: false`, `discard` omits the invalid bin
write, and `error` reports an acquisition error. Missing input or unresolved
references remain unavailable. See the [Qblox QTM channel API](https://docs.qblox.com/en/main/products/qblox_instruments/api/modules/qtm/output.html).

`acquire_digital` samples the level ideally at RT start and observes the new
level of a simultaneous scenario transition. Hardware sampling offset and
transition ties remain unverified.

`acquire_ttl` uses a separate QRM gate. Its enable instruction selects acquisition
and bin, and counting continues across waits until disable. Fixed-bin counts
accumulate across gates. Explicit `ttl_acq_auto_bin_incr_en: true` advances the
bin for each detected rising edge. Out-of-bounds bins fail; an unclosed QTM or
TTL gate is reported incomplete and execution is blocked.

Use `source_kind: ttl_edge` to route each accepted rising edge while a QRM TTL
gate is open. The edge at the opening tick is included and the closing tick is
excluded. The trigger can release a receiver before the count is available.

```yaml
simulation:
  trigger_routes:
    - {source: readout, target: controller, address: 3, source_kind: ttl_edge, latency_ns: 5}
```

The route can also use a declared trigger network and its `extra_latency_ns`.
Aggregate count inputs carry no edge timestamps. Accepted scenario edges
have source and gate packet links in `simulation[seq].ttl_trigger_transmissions`.
Without a digital input trace, the gate interval defines when an edge could
have occurred. A supplied aggregate count bounds how many edges could arrive.
A fixed route can establish the latch count once the complete arrival window
has passed with the latch enabled throughout. Physical threshold detection,
input latency and hardware TTL retrigger behavior require device measurements.
See the [Qblox TTL acquisition reference](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#acquire_ttl-instt).

`upd_thres 0,value,duration` updates the low count threshold at RT execution;
index 1 updates high. Other indices are unsupported. Classification is `low`
below low, `high` at or above high, and `mid` between. Missing thresholds or
low greater than high produce an unknown classification. An explicitly invalid
count has its own `invalid` classification. Configure a QTM truth table to route
these classifications to trigger addresses.

Supplied results use `inputs.digital_acquisitions`, consumed once by sequencer,
instruction kind, acquisition, and bin. They take precedence over a trace:

```yaml
digital_acquisitions:
  - sequencer: readout
    kind: acquire_timetags
    acquisition_index: 0
    bin_index: 1
    count: 2
    timetag_ticks: 512 # already relative to the selected reference
```

TTL results supply `count`; digital samples supply `level`. Generic
`inputs.acquisitions` never supply digital results. Results are retained in
`simulation[seq].acquisitions` and `digital_bins`, including prepared execution.
Each `acquire_timetags` window keeps its own result. The bin summarizes accepted
writes with `avg_cnt`, `count_sum`, `timetag_ticks_sum`, and their means in
`count` and `timetag_ticks`. An unavailable contribution leaves the affected
aggregate unavailable. A zero recorded by policy participates in the mean.
The Hardware Bins view shows the aggregate observed at the cursor separately
from the latest window result. The QTM close command supplies the target bin.
With a fine delay, the target appears at RT command execution and the outcome
appears at the effective close tick. TTL rows show the cumulative accepted
edge count beside the latest gate result, including bins written by
auto-increment. The model uses 1/128 ns ticks for QTM deltas. See the
[Qblox QTM binning reference](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QTM/150_binned_mode_qtm.html).

### QTM digital output and loopback

`set_digital value,1,fine_out_delay` captures the selected output bit for the
next latch-updating packet. The output starts low in the ideal model and changes
at RT execution plus `fine_out_delay` ticks. Each tick is 1/128 ns. The RT packet
keeps its own duration. The trace includes actual level changes and their
setter and packet links. Unchanged levels and skipped packets produce no edge.
Other masks are unsupported. Hardware fine-delay quantization is unverified.
See [Qblox QTM digital output](https://docs.qblox.com/en/main/products/architecture/modules/qtm.html#digital-output).

```yaml
simulation:
  digital_routes:
    - {source: qtm_out, target: qtm_in, delay_ticks: 128}
```

Each routed input starts low and follows its single source after the indicated
nonnegative tick delay, with zero as the default. An explicit digital trace and
a route cannot drive the same input. Routed transitions feed digital sampling,
timetag windows and their LINQ streams. At a shared tick, RT packets open or
close their windows first, routed transitions then reach their targets, and
acquisition evaluates the resulting input. This preserves the half-open window
and lets an ideal sample observe a zero-delay edge at its start. Delayed output
and arrival events can outlive the RT queue. The simulator retains them on the
same clock in the trace and exposes the output transitions under
`simulation[seq].digital_output`.

### Scope capture

On a QRM or QRM-RF, an executed readout acquisition from the selected
sequencer starts a scope capture on the paths configured for sequencer
triggering. The 16,384-sample window uses a 1 ns grid and can extend past the RT
queue. The acquisition's RT duration and binned integration length remain
independent. The example selects path 0 on readout sequencer 2.

```yaml
sequencers:
  - id: readout
    file: readout.q1asm
    module: readout-module
    module_type: QRM
    sequencer_index: 2
    hardware_parameters:
      scope_acq_sequencer_select: 2
      scope_acq_trigger_mode_path0: sequencer
      integration_length_acq: 200
```

Analysis, Play and simulation resolve scope settings across the sequencers
sharing a named module. Scope settings may be split across its sequencer
entries. Conflicting values suppress the analytical scope window and produce a
diagnostic. Simulation rejects that configuration and keeps one scope buffer
per module in valid configurations.
A retrigger before completion shortens the preceding capture. A later
retrigger overwrites the completed buffer. The scope subtrack uses the
existing `scope_acquisition_window` IR, and `simulation[seq].qrm_scope`
retains capture history and the latest modeled ADC samples. Available samples
come from the ADC stage before demodulation. An absent input or an incompatible
grid leaves the sample result unavailable while preserving known window bounds.
An incomplete capture retains its configured length and ends at the
sequencer's terminal edge.
The physical sample budget and export stride limit numerical results. Hardware
scope averaging, level triggering, host storage and ADC overrange flags are
outside the model. See the [Qblox readout guide](https://docs.qblox.com/en/main/products/architecture/sequencers/readout.html#scope-path)
and [QRM scope tutorial](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QRM/030_scope_acquisition.html).

For a QTM, configure a scope mode and apply `set_scope_en 1` through a
latch-updating packet by the time the gate opens. `timetags-windowed` records
`OPEN`, each rising edge inside the effective gate, and `CLOSE`. A latched
disable ends the scope capture independently of the bin. Skipped packets
leave the scope unchanged. Configure `scope_mode: timetags` for continuous
capture and set the exclusive observation horizon in shared simulation ticks.

```yaml
simulation:
  digital:
    tagger:
      scope_mode: timetags
      scope_end_ticks: 256000 # 2000 ns on the shared scenario clock
```

Continuous capture records rising edges from the effective open until the
horizon, a latched disable, or capacity. It can continue after both the
binned window and a normal RT stop. A failed sequencer ends an open
digital capture at its own terminal edge. The scope trace in
`simulation[seq].qtm_scope` has its own 2,048-entry capacity, including
`OPEN` and `CLOSE` markers in windowed mode. Bin counts continue independently.
Entries link to the corresponding packets or digital input transitions.
Their `time_ticks` use the model's 1/128 ns scenario clock. Hardware scope
data uses the module clock at 1/2048 ns. The ideal model leaves hardware
threshold detection, TDC dead time, and low-latency burst behavior unverified.
See the [Qblox QTM scope tutorial](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QTM/130_scope_mode_qtm.html).

### QTM trigger forwarding

Configure each input channel explicitly. `rising` and `falling` forward matching
digital transitions inside the effective `acquire_timetags` window. The open
boundary is inclusive and the close boundary is exclusive. `sampled-high` and
`sampled-low` forward matching `acquire_digital` results. An unavailable
sample contributes one possible arrival when sampled forwarding and a route
are configured. An edge gate without a digital input trace contributes
possible arrivals over the gate interval shifted by the route timing.
Disabled forwarding and skipped packets contribute none.
The sample or edge can release a `wait_trigger` before the bin closes. See the
[Qblox QTM channel API](https://docs.qblox.com/en/main/products/qblox_instruments/api/modules/qtm/output.html#qblox_instruments.qcodes_drivers.io_channel_qtm.IOChannelQTM.forward_trigger_mode).

```yaml
simulation:
  digital:
    tagger:
      forward_trigger_en: true
      forward_trigger_mode: rising
      forward_trigger_address: 3
  trigger_routes:
    - {source: tagger, target: controller, address: 3, latency_ns: 50}
```

Truth-table rules use explicit digital module and channel identities. A rule
matches only when all required channels close at the same effective tick and
provide the specified classifications. Overlapping rules are rejected. A
missing peer yields `incomplete_round` without borrowing a result from another
tick. An unavailable classification in a complete round contributes a
possible arrival for each compatible routed address. If every assignment
of the unavailable classifications emits the same address, the round
produces one definite request for it. Rules for the same address contribute
at most one request. Alternative addresses from one round cannot conflict
with each other. A receiver condition can use that exclusivity even when
the selected address remains unknown. To supply an observed invalid count,
set `count_valid: false` and omit `count` in
an `inputs.digital_acquisitions` entry for `acquire_timetags`. Count validity
and timetag validity are independent. This follows the multi-channel setup in
the [Qblox photon-count example](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QTM/count_based_feedback.html).

```yaml
simulation:
  digital:
    tagger_a: {module: qtm0, channel: 4, count_thresholds: {low: 1, high: 2}}
    tagger_b: {module: qtm0, channel: 6, count_thresholds: {low: 1, high: 2}}
  trigger_truth_tables:
    qtm0:
      - {when: {4: high, 6: mid}, address: 3}
  trigger_routes:
    - {source_module: qtm0, target: controller, address: 3, latency_ns: 50}
```

Each module table uses up to four input channels. At most one rule emits per
round. Positive route latency is applied to the exact scenario timestamp. A
zero-latency event is delivered after the digital effects that produced it. It
can release a waiting RT instruction at that tick. Counters follow the usual
`set_latch_en` and `set_cond` rules. Scheduled arrivals remain visible after
RT drain. `simulation[seq].qtm_triggers` records transmissions and truth-table
decisions with source event links. Hardware network launch grids, trigger
conflict outcomes and rate limits beyond the modeled grid require hardware
timing vectors.

### Qblox trigger transport

Generated analog and QTM requests use fixed route latency by default. A route
can opt into a declared network and add a separate path delay.

```yaml
simulation:
  trigger_networks:
    rack:
      grid_epoch_ns: 100
  trigger_routes:
    - {source: readout, target: control_a, address: 3, network: rack}
    - {source: readout, target: control_b, address: 3, network: rack, extra_latency_ns: 12}
```

The first launch slot at or after a request is selected from the 28 ns grid,
starting at the epoch. Propagation takes 212 ns plus any extra path delay. In
the example, a request at 101 ns launches at 128 ns and reaches the two receivers
at 340 ns and 352 ns. Fanout of one request shares one launch. Use
`grid_epoch: synq` to select the first resolved multi-sequencer SYNQ release in
the scenario. A request without an available epoch produces a diagnostic and
has no known launch or arrival. Its possible slot can affect nearby requests
after the epoch resolves. Display alignment does not shift the network
epoch or any scheduled input. Existing `latency_ns` routes and explicit scenario
trigger arrivals keep their current timing.

Launch slots less than 252 ns apart conflict, including requests for the same
slot. A conflict diagnostic links the source events and their launch times.
Pending deliveries become unknown. An arrival already observed stays valid,
and independent routes continue. If uncertain delivery can change
`wait_trigger` or `set_cond`, execution stops at that affected decision and
retains the earlier trace. A definite matching arrival can release a wait
on the same tick as a possible arrival. Each modeled possible delivery
contributes at most one latch count. An unobserved digital gate can
contribute an unknown number of arrivals over a conservative window on the
launch grid and make pending deliveries uncertain. A threshold result is
known when the definite and possible counts give the same result. The model
records requests, launch slots and individual route outcomes in
`simulation[seq].trigger_network_transmissions`.
The launch grid and propagation follow the [Qblox trigger network guide](https://docs.qblox.com/en/main/products/architecture/sequencers/trigger_handling.html#trigger-network).
Conflict arbitration and recovery require hardware evidence. See the
[instrument troubleshooting guide](https://docs.qblox.com/en/main/troubleshooting.html).

### LINQ configuration and QTM streams

`fb_acq_tb_cfg` supports disabled mode and one-word write-combine with 1–4
payload bytes. Each enabled two-bit contribution starts at an even position
inside that width. Simulation checks the position when the configuration
executes on RT, including packed-register and raw forms. The assembler also
accepts encodings outside the supported RT model.

Contributors with the same ID, receiver, and completion tick merge into one
FIFO entry after all scheduled completions and RT interruptions at that tick.
Routes must agree on latency, applied once from the completion tick. A zero
route delay delivers at that same tick. Collisions and incompatible widths
fail explicitly. Unoccupied positions and their valid bits stay zero.
Different completion times stay separate. Each combined entry retains its
contributors' acquisition and packet links. TB configuration, ID and valid bit
are captured when acquisition starts. See the [Qblox QRM write-combine tutorial](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QRM/423_linq_write_combine.html).

Common-data and TB configuration are separate. `fb_com_cfg` supports disabled
write-combine, and `fb_com_extra`/`fb_acq_tb_extra` support disabled extra bytes.
Plain sends remain unchanged. Enabled common-data combination, multiword packing,
enabled extra bytes, and `fb_acq_tb_mock` remain blocked pending encoding or
emission vectors. The reference describes the mock's fourth operand as both
duration and count.

Simulation resolves packed register configuration and supported raw `fb_cmd`
aliases using the Qblox Instruments 1.4.1 assembler encoding. The original
mnemonic and source remain on the Q1 issue and RT packet. For example,
`fb_cmd 0x12a,R1,4` sends register `R1` with LINQ ID 42, just like
`fb_com_data 42,R1,4`. Reserved payload bits are invalid. Commands without a
verified effect report an explicit limit when executed.

QTM stream IDs are independently configured at RT execution; ID zero disables
that stream. `fb_llp_tags_id` and `fb_tdc_tags_id` send on each valid window edge.
Their absolute module clock needs explicit
`digital[seq].linq_timestamp_origin_ticks`: module-clock ticks at simulation
time zero. LLP words encode ns; TDC words encode 1/128 ns ticks, as raw 32-bit
words. Emission time remains the edge's scenario time, separate from its encoded
timestamp. Fractional-ns emission and LLP rounding remain blocked until receiver
scheduling/rounding vectors establish their behavior. Fractional input edges are
still supported for acquisition counts and selected timetags. When an edge
stream refusal stops the model, the retained edge and any observed RT cutoff
are available as diagnostic evidence.

`fb_llp_ttls_id` sends the raw count at effective window close.
`fb_tdc_tdelta_id` sends a valid source-minus-reference delta as a signed
32-bit word in 1/128 ns ticks at that same close. The close packet captures the
enabled IDs, sequencer reference and thresholds when it executes on RT. When
both streams are enabled, the model sends count then delta. Invalid deltas
with an enabled delta stream and fractional close emissions report an explicit
limitation. If the model stops after recording the QTM result, its diagnostic
links that result and any active RT cutoff retained in the trace. Local bin
policies cannot establish the invalid LINQ wire word.
See the [Qblox LINQ time-delta reference](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#time-delta).
Transport uses the existing explicit route latency. These are reference-based
ideal models, without hardware buffering, rate limits, or hardware parity claims.
See the [QTM LINQ reference examples](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QTM/424_linq_qtm.html).

### Export

`TimelineIR.execution[seq]` records status, steps, reason, timing/address model and
assumptions. `TimelineIR.simulation[seq]` records queue settings, acquisitions and
signal traces. Feedback and trigger arrows link the send to the actual arrival
time. In VS Code, the target source action opens the executed Q1ASM command
that consumed the arrival, when one exists.
Numeric and register branches use compiled Q1ASM addresses, including the two
words emitted by `jlt`, three-operand `jge`, and `loop`. The Q1 issue events keep
their source location and report `compiled_pc` in programs with these expansions.
Static analysis follows a determinate, safe forward branch into the second word
of a macro. It also follows known backward conditional branches to loaded
instruction entries. The `--max-steps` setting bounds repeated instructions on
those paths. An unresolved or unsafe path gets an incomplete region, as does
a path that reaches the limit. At the repeat limit, the Q1 boundary is the end
of the last recorded issue. The RT boundary follows the recorded RT work.
Concrete execution, Play and simulation execute the lowered status branch using
the current flags.
Analysis decodes the signed frequency and the marker's four output bits at the
setter. Register history keeps the raw words. A ready AWG gain, AWG offset or
phase register value outside the supported range ends exact analysis at the
setter and preserves the recorded RT prefix. A ready RT register operand
outside the supported range ends analysis before that packet enters the queue.
Queue checking retains the recorded prefix and marks further coverage incomplete.
Analysis reports `play_pulse` and `sw_req` as incomplete when their behavior
could affect execution. A proven conditional skip keeps its known RT dwell.
When a known back edge revisits a compact loop, Analysis uses that visit's
first-iteration evidence. It retains an incomplete boundary if the current
body cannot be isolated.
When that body contains a compact child with proven timing and a stable
count within the parent, the new summary uses that visit's child evidence.
Analysis can sum changing child counts and wait durations when it proves the
recurrences and the elapsed work on both cores. A zero-duration packet contributes
4 ns of dequeue spacing on each zero visit. A changing child count can also
preserve fixed AWG gain or offset when both signed path values and their RT
commit are proven. The compact loop inspector shows the first preview range
and the proven total RT duration separately.
For a loaded numeric or ready register target, Analysis can summarize a
backward `loop` using the same proof as for a label. An unconditional
`jmp` can produce a forever summary when the period and target are stable.
An exact positive RT period gives an `Unbounded` total. An exact Q1-only loop
shows `0 ns` in Debug and leaves the recorded RT state intact in Hardware.
A loop with unresolved timing keeps an `Unknown` total.
An executed `stop` records its signed 32-bit `stop_code` and `stop_source` in
the execution summary. The code defaults to zero and does not change the
execution outcome. When there is a terminal RT event, it remains at queue
drain and links to the original `stop` instruction. Static analysis shows the
code on its stop event and keeps unresolved codes explicit.
An executed `illegal` takes 4 ns on Q1 and records
`SEQUENCE_PROCESSOR_RT_EXEC_ILLEGAL_INSTRUCTION` at Q1 issue completion.
The event-driven model handles the fault before a new RT dequeue at that tick,
halts both cores, and discards queued RT work. It retains effects applied
before the fault and does not model an analog reset or tail after the halt.
Concrete execution and Play report the fault with its source and retain the
analytical prefix. That prefix does not establish which packets physically
reached RT before the halt. Independent sequencers continue in simulation and
Play. [Qblox `illegal` instruction](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#illegal-instt)

In Simulation, an active Q1 issue ends at the modeled error edge. LINQ FIFO
overflow also closes the active RT packet there. If Q1 stops at a validation
error, reaches the end of the program without `stop`, or hits the instruction
limit, the trace closes the active RT packet at that edge and keeps its
programmed end as planned data. An explicit `stop` allows the queue to drain.
An RT dispatch or acquisition result refusal can also stop both cores. The
diagnostic keeps the original source and links the observed RT cutoff when
available. Acquisition windows and results already observed remain in the
trace. A delayed QTM gate refusal links the originating acquisition command
and closes active spans at its effective boundary. A refusal before RT
admission can close an active Q1 issue while the
refused packet retains only its Q1 and dequeue evidence. A TB write-combine
rejection closes the affected RT spans. Its diagnostic provides separate
actions for the retained contributions and the local cutoff.
Both timeline viewers show the observation cutoff and planned end of a clipped
RT command in the active time basis. The plotted span ends at the cutoff.
If a time limit interrupts only deferred signal work after the RT queue drains,
the diagnostic points to the active acquisition, scope capture, or NetZero
result when its source is available. The raw limit remains in the trace.
Hardware projects each recorded sequencer's observation end into its active
time basis, independently of the overall simulation horizon.

The Hardware view exposes queues, stalls, inputs, signal stages and bin
outcomes alongside register history and pending/applied settings. The timeline
links a Q1 setter to the RT packet that commits it. Later RT snapshots record
the origin of the setting for inspection. Digital bins show their sampled
level, count or time delta. Unavailable outcomes retain their status, reason
and event time.

Execution is bounded by instruction and time limits. Unknown inputs and unsupported
instructions stop explicitly. Unverified QTM timing and encodings, multiword
feedback formats, and undocumented firmware behavior remain outside the
instruction model.

Use the [verification workspace](../examples/simulation-verification/README.md)
for runnable examples and expected values, and [continuous validation](ci-validation.md)
for the automated checks. Passing these checks validates the software model, not
equivalence to a physical instrument.
