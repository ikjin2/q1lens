# Acquisition buffer checks

Acquisition buffers are checked automatically in the CLI, Python analysis API and
VS Code. No flag or setting is required. The `--hardware-readiness` flag and
`q1lens.q1timeline.hardwareReadiness` setting control additional scope and
bin-reuse hints; leaving them off does not disable buffer checks or warnings
about settings left unapplied at program end.
Findings use the existing diagnostics and Problems views. Successful buffered
bursts produce no acquisition buffer diagnostic.

Specify hardware identity explicitly for confirmed QRM/QRM-RF findings:

```yaml
sequencers:
  - id: readout
    file: readout.q1asm
    module: readout_module
    module_type: QRM
```

`module` groups sequencers on the same physical module; it is not a module type.
The checker counts supplied acquisition programs in that group. Include the
programs that share the module. Missing identity, unknown peer counts and other
module families remain uncertain. QTM window acquisition and TTL event-counting
buffers need separate mode-specific models and are not treated as QRM integrated
acquisition requests.

## Model and evidence

The [Qblox Instruments FAQ, version 2026.10.1](https://docs.qblox.com/en/2026.10.1/products/qblox_instruments/faq.html)
permits back-to-back acquisitions and documents bursts of up to seven acquisitions
per sequencer. Its sustained periods are 300, 600 and 900 ns per sequencer for
one/two, three/four and five/six used sequencers, respectively. This is not a
300 ns prohibition on every adjacent pair.

The checker models pending service work in nanoseconds. For an acquisition at
time `t`, with service period `S`, it updates:

```text
work = max(0, previous_work - (t - previous_t)) + S
required_burst_credits = ceil(peak_work / S)
```

Idle time drains the backlog. Initially the backlog is empty. Service is modeled
as constant while work is pending; this is a software envelope, not a measured
description of firmware arbitration or its precise FIFO capacity.

Two envelopes account for resource uncertainty. The optimistic service period is
300 ns: other sequencers may become idle. The conservative period includes the
supplied peer count, or uses 900 ns when it is unknown. Finite constant loops are
evaluated algebraically, retaining backlog across repeats and subsequent code.
Preview copies do not add requests. Sustainable infinite loops are checked across
their wrap boundary. Runtime-dependent loops, nested loops and incomplete paths
are not used to prove a failure. A known finite total of at most seven requests
remains silent even if their start times are unknown.

| Result | Diagnostic |
| --- | --- |
| Conservative peak fits the seven-request burst allowance | None |
| Finite demand exceeds that allowance | `acquisition_buffer_pressure`, warning |
| Only the conservative rate is overloaded, or timing/profile is uncertain | `acquisition_buffer_pressure`, warning |
| A fixed infinite loop on confirmed QRM/QRM-RF exceeds even the optimistic sustained rate | `acquisition_buffer_sustained_overload`, error |

The error identifies a loop whose continued modeled execution must exhaust any
finite buffer. It does not predict a physical overflow timestamp. A finite excess
is a warning because seven documented burst requests do not establish an exact
upper bound on FIFO capacity. For example, two requests 120 ns apart can be
buffered; a request every 120 ns forever cannot be sustained. Two acquisitions
per 1 us loop can remain silent even when the loop-wrap gap is only 200 ns.

The model checks the supplied workload and assumes that its RT acquisition starts
are reached. It does not establish behavior outside a partial trace or equivalence
to a physical instrument. Integration lengths, acquisition interruption, feedback
FIFO pressure and the Q1-to-RT command queue are separate checks.

## Initial RT buffer label

The timeline's `Initial buffer ≈ 1 ms` label refers to the existing Q1/RT timing
reserve, not this acquisition buffer. It appears only alongside an RT-buffer
warning/error. Declared assumptions, informational estimates, coverage notices,
unrelated warnings and acquisition-buffer findings do not show the label.
