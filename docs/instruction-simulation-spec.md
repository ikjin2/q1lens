# Q1ASM simulation implementation spec

This spec covers gaps found by comparing the Q1Lens instruction table, static
analysis, timeline Play, and the event-driven simulator with Qblox documentation.
The simulator here means `analysis.execution_mode: simulation`. Play uses
analytical queue replay and has different input and timing contracts.
Reproduction and gap descriptions record the starting behavior for each
package. See [simulation behavior](simulation.md) for what is implemented.

## Delivery and shared rules

| Work package | Entry point | Dependencies |
| --- | --- | --- |
| A. Zero-duration RT packets | `simulation/runtime.py`, `analysis/queue_timing.py` | None |
| B. Full-product multiply | `analysis/interpreter.py`, `simulation/runtime.py` | None |
| C. Net-zero compensation | `simulation/runtime.py`, `simulation/signals.py` | A for zero-duration packets |
| D. Digital acquisition | `simulation/runtime.py`, `simulation/config.py` | A for zero-duration packets |
| E. LINQ payloads | `simulation/runtime.py` and existing feedback routing | D for QTM streams and window results |
| F. Configured readout integration | `cli.py`, `watch/watcher.py`, `def_runs.py`, `simulation/runtime.py` | None |
| G. IQ accumulator transmission | `simulation/runtime.py`, LINQ feedback tests | None |
| H. Trigger counters in Play and simulation | `playback.py`, `analysis/queue_timing.py`, `simulation/runtime.py` | Shared hardware settings with I |
| I. SYNQ participation | `project.py`, `analysis/queue_timing.py`, `simulation/runtime.py` | Shared hardware settings with H |
| J. QTM digital output | `simulation/runtime.py`, `simulation/config.py` | D for routed acquisition input |
| K. Stop code and source | `analysis/interpreter.py`, `simulation/runtime.py`, IR and Hardware inspector | None |
| L. QTM fine delay and time delta | `simulation/runtime.py`, `simulation/config.py` | D and E, coordinate tick ordering with J |
| M. Scope capture | `analysis/acquisition_windows.py`, `simulation/runtime.py`, `simulation/signals.py` | D for QTM input and F for separate integration timing |
| N. Compiled branch addresses | Q1ASM lowering and execution address mapping | None |
| O. LINQ assembler forms | `q1asm/instruction_table.py`, `simulation/runtime.py` | E and L for implemented payload effects |
| P. QTM trigger forwarding and truth tables | `simulation/config.py`, `simulation/runtime.py` | D, J and L for digital source events, H for receiving counters |
| Q. Shared simulation time origin | `analysis/alignment.py`, simulation entry points and RT rendering | I for explicit SYNQ participant metadata |
| R. Scheduled LINQ receipts in Play | `external_inputs.py`, `playback.py`, Configure validation and tests | H for common scenario replay conventions |
| S. One-shot NCO phase updates | `analysis/interpreter.py`, `simulation/runtime.py`, `simulation/signals.py`, Hardware phase state | H for conditional Play |
| T. Compare status-flag branches | `analysis/interpreter.py`, timeline branch controls and comparison tests | Existing bounded branch comparison |
| U. Qblox trigger transport timing | `simulation/config.py`, `simulation/runtime.py`, trigger timing tests | P for QTM-generated requests, Q for shared display time |
| V. Explicit `illegal` fault | `analysis/interpreter.py`, `simulation/runtime.py`, `playback.py` | K for terminal event and source conventions |
| W. Play timing after zero-duration packets | `playback.py`, `analysis/playback_timing.py` | A and H |
| X. Simulation comparison provenance | `simulation/runtime.py`, comparison IR and selection tests | H, J, M and U |
| Y. Weighted acquisition in analytical timelines | `sequence.py`, `analysis/acquisition_windows.py`, `ir/serialize.py`, project analysis and Play | F and M |
| Z. Module-wide readout scope selection | `analysis/acquisition_windows.py`, `ir/serialize.py`, simulation scope settings | M |
| AA. Source diagnostics for rejected Play timing | `analysis/queue_timing.py`, `analysis/playback_timing.py` | H, I and W |
| AB. Independent analytical acquisition feedback streams | `analysis/interpreter.py`, feedback IR entries and balance | E, G, L and Y |
| AC. Receiver ownership of analytical LINQ self-cast | `ir/serialize.py`, LINQ analysis and tests | E and R |
| AD. Zero-duration timing in analysis and Concrete | `analysis/interpreter.py`, `playback.py`, loop summaries | A and W |
| AE. Arrival markers and consumer source navigation | Simulation arrivals, feedback flow IR, VS Code source reveal | Q and X |
| AF. Standalone RT view switching | `render/html.py`, standalone render tests | Q, T and X |
| AG. Estimated self-cast arrival order | `ir/serialize.py`, analytical LINQ tests | AB and AC |
| AH. Conditional and analytical LINQ mock limitations | Simulation dispatch, analysis and Play timing | O and W |
| AI. Unresolved trigger time in compact analysis | `analysis/interpreter.py`, timeline IR, HTML and VS Code timing labels | H, Q and AD |
| AJ. QRM TTL edge trigger routing | `simulation/config.py`, `simulation/runtime.py` | D, P and U |
| AK. Import the public TimelineIR API first | `analysis/api.py`, `ir/__init__.py`, execution API tests | None |
| AL. Disabled LINQ common-data ID in analysis | `analysis/interpreter.py`, LINQ balance tests | O and AG |
| AM. Rejected LINQ arrivals and overflow provenance | `simulation/runtime.py`, TimelineIR diagnostics | E and AE |
| AN. Single-word TB write-combine rounds | `simulation/runtime.py`, LINQ parity tests | E, F and O |
| AO. Bounded uncertain trigger counts | `analysis/trigger_counters.py`, `simulation/runtime.py` | H and U |
| AP. RT minimum duration in analysis and Play | `analysis/interpreter.py`, `playback.py` | A, W and AD |
| AQ. Synthetic arrivals in VS Code diagnostics | Extension diagnostic mapping and source navigation | AE and AM |
| AR. Unavailable direct branch comparison | `analysis/interpreter.py`, branch comparison IR tests | T |
| AS. Reject invalid RT duration before simulation issue | `simulation/runtime.py`, duration and execution parity tests | AP |
| AT. Changing branch paths in compact loops | `analysis/interpreter.py`, compact loop and preview tests | T and AD |
| AU. Arrival markers in instruction layout | Extension timeline adapter and instruction-layout tests | AE and AQ |
| AV. Fault edge uses the displayed time basis | Standalone and extension event inspectors | Q and V |
| AW. Incomplete simulation comparison alternatives | Simulation comparison IR and timeline renderers | AH, X and AU |
| AX. Aligned external input inspection | Extension event inspector and playback input fixtures | R, Q and AV |
| AY. Latched state in compact loop previews | `analysis/interpreter.py`, loop and setter selection tests | S and AT |
| AZ. LINQ arrivals through Play stop completion | `playback.py`, scheduled feedback tests | K and R |
| BA. Point marker geometry in the extension | Extension instruction layout | AU and AW |
| BB. Displayed times in the comparison inspector | Extension event comparison inspector | AV and AX |
| BC. Time basis of the Play waiting summary | Extension execution controls and waiting fixtures | AX |
| BD. Unknown thresholded acquisition triggers | `simulation/runtime.py`, acquisition trigger and counter tests | H, U and AO |
| BE. Queue replay after a failed Play stop | `analysis/playback_timing.py`, scheduled feedback and queue replay tests | AA and AZ |
| BF. Fractional digital RT with a physical model | `simulation/signals.py`, mixed digital and physical simulation tests | J, L and P |
| BG. Unverified wide LINQ receive selectors | LINQ FIFO execution and scheduled Play | R and AM |
| BH. Zero-latency triggers from RT acquisition interruptions | `simulation/runtime.py`, trigger ordering tests | AJ, AN and BD |
| BI. Unavailable QTM sampled trigger input | `simulation/runtime.py`, QTM sampled trigger tests | P, AO and BD |
| BJ. Unavailable classifications in a QTM truth round | `simulation/runtime.py`, QTM truth-table tests | P and BD |
| BK. Arrival markers for trigger waits in Play | `playback.py`, timeline rendering tests | AE, AU and AX |
| BL. Missing analog acquisition evidence on a trigger route | `simulation/runtime.py`, acquisition trigger tests | BD and BH |
| BM. Digital acquisition results in Hardware | Extension Hardware state and bin rendering | D, L and BI |
| BN. Unobserved digital edge trigger windows | `simulation/runtime.py`, QTM and TTL trigger routing tests | AJ, BD, BH and BI |
| BO. Supplied weighted acquisition without numerical weights | `simulation/runtime.py`, weighted acquisition tests | F, Y and BF |
| BP. Baseline RT underflow diagnostic on the timeline | `simulation/runtime.py`, timeline diagnostics and marker tests | AV and AW |
| BQ. Time basis of standalone event tooltips | `render/html.py`, standalone tooltip tests | AF, AV and BP |
| BR. Definite trigger arriving late on an uncertain wait tick | `simulation/runtime.py`, trigger wait ordering tests | BD, BH, BI and BN |
| BS. Definite output of an exhaustive QTM truth round | `simulation/runtime.py`, QTM truth-table tests | P, U and BJ |
| BT. Completed acquisition before an unresolved suffix | `analysis/acquisition_windows.py`, analytical window tests | F, M, Y and AI |
| BU. Mutually exclusive QTM truth outputs in trigger conditions | `simulation/runtime.py`, QTM truth-table and counter tests | AO, BJ and BS |
| BV. Weighted acquisition without a modeled input | `simulation/runtime.py`, weighted acquisition and trigger tests | BL and BO |
| BW. Known request before the trigger-network epoch resolves | `simulation/runtime.py`, SYNQ trigger-network tests | I, U and AO |
| BX. Supplied TTL counts constrain routed edge uncertainty | `simulation/runtime.py`, TTL routing and latch tests | AJ and BN |
| BY. Integration windows on simulated analog acquisitions | `simulation/runtime.py`, acquisition-window IR tests | F, M, V and BO |
| BZ. Capture cutoff after a sequencer failure | `simulation/runtime.py`, digital acquisition and scope tests | D, M and BN |
| CA. Acquisition timing source in both inspectors | VS Code and standalone event inspectors | Y, BO and BY |
| CB. Source evidence for unresolved trigger decisions | `simulation/runtime.py`, trigger diagnostic tests | AE, BD, BJ and BN |
| CC. Acquisition completion on a faulted analytical path | `analysis/acquisition_windows.py`, analytical acquisition and Play tests | V, Y and BT |
| CD. Instruction zoom regression fixture after command folding | VS Code instruction zoom tests | AU and BA |
| CE. Valid sender diagnostic links in prepared simulation | `simulation/runtime.py`, trigger-network tests | U, BN and CB |
| CF. Diagnostic evidence for a simulated comparison terminal | `simulation/runtime.py`, comparison provenance tests | X, AW and CB |
| CG. Scheduled LINQ rejection on the Play timeline | `playback.py`, timeline rendering and scheduled-input tests | AM, AX and AZ |
| CH. Network conflict times on the active axis | Timeline diagnostic presentation and browser tests | Q, U and CG |
| CI. Visible evidence for diagnostic event links | Timeline diagnostic links and browser tests | AE, CB and CE |
| CJ. Terminal edge for a gate left open at RT stop | `simulation/runtime.py`, digital comparison tests | AW, BZ and CF |
| CK. Comparison terminal evidence in the event inspector | Standalone and extension event inspectors | AW, CF and CI |
| CL. Debug trigger traffic in standalone Normal | `render/html.py`, standalone renderer tests | AE, AF and CI |
| CM. Forward loop targets with resolved operands | `analysis/interpreter.py`, branch and compiled address tests | N and T |
| CN. Loop counter reused as its branch target | Q1 operand readiness and execution parity tests | N and CM |
| CO. Determinate forward jump into a compiled macro | `analysis/interpreter.py`, compiled address tests | N and T |
| CP. Simulation LINQ rejection on the active axis | Simulation rejection diagnostics and timeline renderers | AM, BP and CG |
| CQ. Conditional branch to an unloaded address | Concrete branch validation and Simulation branch choices | N, T and CM |
| CR. Status branch targeting a compiled macro interior | Analysis status branch routing and compiled address tests | N, T, CO and CQ |
| CS. Conditional macro targeting a compiled macro interior | Analysis loop and compatibility branch routing | CM, CN and CR |
| CT. Effective RT origin after an alignment fallback | Standalone and VS Code renderers, alignment fixtures | Q and AF |
| CU. Simulation label alignment at RT execution | `analysis/alignment.py` and simulation alignment tests | Q and CT |
| CV. Explicit RT anchor separates Normal and Debug extents | Standalone and VS Code timeline geometry | Q, AF, CT and CU |
| CW. Q1-only peer extends the Normal RT axis | Standalone and VS Code timeline extents | CT and CV |
| CX. Align to an armed RT wait | `analysis/alignment.py`, Simulation alignment fixtures | Q, CU and CV |
| CY. Source and evidence for a blocked RT wait | `simulation/runtime.py`, blocked execution and comparison fixtures | CB, CF and CI |
| CZ. Pending RT wait timing in both inspectors | Standalone and VS Code timing presentation | AI, CX and CY |
| DA. One timeline command per completed RT wait | Simulation wait provenance and timeline projections | CY and CZ |
| DB. Select the RT commit of a setter | Simulation latched provenance and VS Code selection | S, X and DA |
| DC. Link setter issues and RT commits in standalone | Standalone renderer and browser selection fixtures | AF and DB |
| DD. Coincident trigger arrivals remain inspectable | Standalone and VS Code arrival point projections | AE and AU |
| DE. Reveal the actual consumer of an external arrival | VS Code source resolution and navigation tests | AE and DD |
| DF. Show completed RT wait phases | Standalone and VS Code timing presentation | CX, CZ and DA |
| DG. Explain the next dequeue edge after a zero RT duration | Standalone and VS Code timing presentation | A, W and AD |
| DH. Attribute a full-queue stall to its pending RT wait | Simulation quiescence and comparison diagnostics | CY and CF |
| DI. Preserve pending wait evidence at a Simulation time limit | Simulation limits and comparison diagnostics | BP, CF and CY |
| DJ. Cap active RT packets at the observation limit | Simulation RT trace and both timing presentations | DF and DI |
| DK. Close an active Q1 issue at the observation limit | Simulation Q1 trace and time-limit diagnostics | DI and DJ |
| DL. Inspect an incomplete Q1 issue | Standalone and VS Code timing and Hardware views | DK |
| DM. Distinguish pending writes in a DEF run trace | VS Code DEF Runs instruction trace | DK and DL |
| DN. Keep future Q1 latch changes outside the observed trace | Simulation Q1 cutoff and pending-setting provenance | DK and DB |
| DO. Close an active Q1 issue on a terminal runtime error | Simulation fault trace and Q1 provenance | DK and DN |
| DP. Close the active RT packet on LINQ FIFO overflow | Simulation fault trace and LINQ rejection | CP and DJ |
| DQ. Reveal the sender of a rejected routed LINQ arrival | Standalone and VS Code event inspectors | AE, AM and CP |
| DR. Close RT observation when Q1 validation stops the model | Simulation Q1 validation and RT trace boundaries | DJ and DP |
| DS. Close RT observation at a Q1 program boundary | Simulation program exhaustion and Q1 instruction limit | DJ and DR |
| DT. Explain an incomplete RT command in both viewers | Standalone and VS Code timeline inspection | DJ and DL |
| DU. Separate observed NetZero compensation from its plan | Simulation signal events and physical result export | DJ |
| DV. Close both cores when RT dispatch refuses an effect | Simulation RT dispatch and terminal trace boundaries | DO, DR and DS |
| DW. Close active spans on an asynchronous acquisition refusal | Simulation acquisition completion and terminal trace boundaries | DV |
| DX. Remove the hidden Q1 row gap in standalone Normal | Standalone RT-origin lane geometry | AF and CV |
| DY. Preserve the QTM result at a terminal LINQ refusal | Simulation digital acquisition completion and cutoff evidence | L, DV and DW |
| DZ. Accumulate repeated QTM writes to the same bin | Simulation digital bin results | D and L |
| EA. Close observation on a rejected QTM edge stream | Simulation digital input and LINQ terminal evidence | DY |
| EB. Preserve evidence for a rejected TB write-combine round | Simulation LINQ round diagnostics and terminal observation | AN, DV and DW |
| EC. Close Q1 observation at an RT refusal | Simulation RT admission, trigger waits and Q1 terminal trace | AH, DO and DV |
| ED. Open distinct comparison terminal evidence | Standalone and VS Code event inspectors | CF, CK and DY |
| EE. Attribute a time limit to deferred signal work | Simulation limits and deferred signal evidence | DI, DJ, DK and DU |
| EF. Close NetZero results at a sequencer failure | Simulation signal results and per-sequencer terminal observation | DP and DU |
| EG. Close active spans on a delayed QTM gate refusal | Simulation effective gate boundaries and terminal trace | DV, DW and DY |
| EH. Open distinct TB rejection evidence | Standalone and VS Code diagnostics | EB and ED |
| EI. Inspect an incomplete NetZero result | Standalone and VS Code signal inspection | DT, DU and EE |
| EJ. Honor sequencer observation end in Hardware | Simulation terminal metadata and Hardware cursor | DP, EC and EE |
| EK. Apply Hardware sample alignment once | VS Code Hardware signal inspector | Q and BF |
| EL. Inspect QTM bin aggregates at the Hardware cursor | Simulation bin results and Hardware bin history | DZ |
| EM. Close the Play Q1 issue at a rejected arrival | Playback Q1 trace and scheduled LINQ rejection | CG, DL and DO |
| EN. Open overlapping NetZero evidence in standalone Normal | Standalone RT selection and signal provenance | EI and DC |
| EO. Keep pending setters out of the standalone RT lane | Standalone Simulation Normal and setter provenance | DC and DX |
| EP. Keep pre-origin arrivals outside standalone Normal | Standalone Simulation viewport and arrival evidence | AE, CT and EO |
| EQ. Show the Play Q1 observation end in Hardware | Playback terminal evidence and Hardware cursor | EJ and EM |
| ER. Show the recorded branch choice in standalone inspection | Standalone branch inspection and generated path context | T, X and DX |
| ES. Keep distinct digital bin histories in Hardware | Hardware acquisition identity and bin history | D and EL |
| ET. Show the bins written by a TTL gate | Hardware TTL target and result history | D and ES |
| EU. Preserve the first Hardware click after a cursor edit | VS Code Hardware cursor controls | EJ and EL |
| EV. Use the QTM close target in Hardware Bins | Hardware timetag gate targets | D, ES and ET |
| EW. Show TTL totals at the Hardware cursor | Simulation TTL bin totals and Hardware history | D, EL and ET |
| EX. Show modeled I/Q bin averages at the Hardware cursor | Physical simulation bin history and Hardware | F and EL |
| EY. Show recorded unavailable I/Q outcomes at the Hardware cursor | Simulation acquisition evidence and Hardware | U and EX |
| EZ. Open standalone Q1 evidence from the keyboard | Standalone Q1 selection and branch inspection | ER |
| FA. Follow effective digital acquisition windows in Hardware | Simulation gate timing and Hardware acquisition state | BY, BZ and EV |
| FB. Navigate the recorded loop preview in standalone HTML | Standalone loop inspection and visible evidence | AY and ER |
| FC. Follow bounded concrete back edges in Analysis | Analytical Q1 branch traversal and RT evidence | T and CQ |
| FD. Record missing I/Q outcomes without a trigger route | Simulation acquisition evidence and Hardware Bins | BY and EY |
| FE. Open standalone RT and loop evidence from the keyboard | Standalone timeline activation | EZ and FB |
| FF. Show recorded RT decisions for each set_cond | Conditional execution evidence in timeline inspection | X and ER |
| FG. Show the Q1 boundary when Play cannot continue | Playback terminal metadata and Hardware cursor | EQ |
| FH. Show live Play waits on their respective cores | Hardware replay waits and source navigation | H, R and FG |
| FI. Scope compact loop evidence to its analytical visit | Analysis loop summaries after a concrete back edge | AY and FC |
| FJ. Keep Play failure scoped to the affected sequencer | Project playback and per-sequencer Hardware status | FG |
| FK. Compact loops with loaded numeric and register targets | Analytical compiled-address traversal and loop summaries | N, CO and FI |
| FL. Recognize stable numeric jump loops in Analysis | Analytical unconditional jumps and compact loop evidence | N and FK |
| FM. Compose nested compact loops on a later analytical visit | Analysis nested loop provenance and timing | FI |
| FN. Keep the recorded condition decision when RT arms a wait | Simulation wait evidence and timeline inspection | FF |
| FO. Keep Q1-only loop summaries out of the Normal RT view | Standalone and VS Code timeline visibility | CW and EO |
| FP. Preserve proven Hardware state across compact loops | Hardware register and parameter state | AY and FI |
| FQ. Use the Q1 boundary of each compact loop visit | Hardware Q1 cursor and loop provenance | FI and FP |
| FR. Use the exact Q1 issue for an unresolved branch | Analysis branch evidence and Hardware Q1 cursor | FC and FQ |
| FS. Account for changing child counts in compact loop timing | Analytical nested loop timing and register readiness | FM |
| FT. Distinguish a compact loop preview from its total RT duration | Standalone and VS Code loop inspection | FB and FS |
| FU. Propagate changing child dwell through a compact parent loop | Analytical nested loop timing | FM and FS |
| FV. Compose invariant latched updates with changing child counts | Analytical nested loops and latched provenance | FM and FS |
| FW. Show a Q1 proof cutoff at the linked issue boundary | Analysis cutoff evidence and Hardware Q1 cursor | FR and FS |
| FX. Distinguish unbounded RT loops from unresolved totals | Standalone and VS Code loop inspection | FL and FT |
| FY. Compose jointly changing child counts and dwell | Analytical nested loop timing and register state | FS and FU |
| FZ. Keep a partial backward-branch replay visible through its last Q1 issue | Analysis repeat limits and Hardware Q1 cursor | FC and FW |
| GA. Preserve invariant two-path AWG settings with changing child counts | Analytical nested loops and latched provenance | FV |
| GB. Include zero-dwell spacing in a changing nested loop | Analytical nested loop timing | A, FU and FY |
| GC. Keep RT idle for a proven Q1-only compact loop | Hardware core state and acquisition coverage | FO, FP and FX |
| GD. Preserve an affine child count across a fixed 32-bit wrap | Analytical nested loop register proof | FS and FY |
| GE. Describe equal or late-changing periods from exact loop evidence | Standalone and VS Code loop inspection | FT, FY and GB |
| GF. Decode register frequency and marker values in Analysis | Latched operand state and RT provenance | FV and GA |
| GG. Stop exact latch analysis at unsupported register values | Analytical validation and RT provenance | GA and GF |
| GH. Expose unsupported LINQ configuration in analytical flow estimates | Analytical LINQ effects and FIFO evidence | N and AN |
| GI. Mark unverified software requests and pulses as incomplete | Analytical command coverage and latch provenance | AH, AY and GG |
| GJ. Bound compact-loop execution by future latch ranges | Analytical compact loop timing and operand proof | AT, GA and GG |
| GK. Preserve single-word TB combine counts in analytical FIFO evidence | Analytical LINQ matching and acquisition provenance | AN and GH |
| GL. Keep wide receive selectors out of exact analytical FIFO matching | Analytical LINQ receive evidence and Q1 cutoff | BG and GH |
| GM. Validate known register operands before analytical RT admission | Analytical and Simulation RT operand validation | AP, AS and GG |
| GN. Inspect collapsed LINQ diagnostic evidence in standalone Normal | Standalone diagnostic navigation | CI and GH |
| GO. Keep analytical FIFO balance independent of loop preview length | Analytical LINQ and compact-loop evidence | FB, FT and GH |
| GP. Show encoded dwell in the unverified RT command inspector | Standalone and VS Code timing inspection | DT and GI |
| GQ. Keep fixed-start simulation idle on Q1-only paths | Event-driven RT startup and terminal state | K, CW and GC |
| GR. Stop queue safety claims at an explicit Q1 proof boundary | Analytical RT queue checks and diagnostics | FW, FZ, GG and GM |

Paths in the table are relative to `src/q1timeline`. Each package can land
separately with its own tests. A owns RT dequeue and wake-up, B owns register
execution, C owns net-zero state and signal output, D owns digital acquisition
inputs and results, and E owns feedback emission and packing. Integrate their
changes to `_SUPPORTED` and the instruction-matrix fixtures together when they
touch the same lines. Start from the existing [simulation docs](simulation.md)
and [instruction matrix](../tests/test_q1timeline_instruction_matrix.py).

- Validate signatures and resolved operand ranges at Q1 issue. Preserve operand
  snapshots in queued packets and apply RT side effects at dequeue or at the
  documented signal event. A conditional skip uses the selected else duration
  and leaves output, acquisition, and feedback state alone.
- Keep Q1 issue time, RT dequeue time, and physical event time distinct. Use the
  shared simulation clock internally and the existing RT alignment for display.
  Link results to their source and RT packet. Preserve the default initial
  prefill and the current Normal and Debug lane presentation.
- Retain existing scenarios and IR consumers. Add executable signatures to the
  instruction matrix only when their specified semantics are supported. A missing
  input or unresolved device-specific behavior should yield an explicit reason
  or unknown result. A known empty input is distinct from a missing input.
- Run the relevant Python tests under `tests/` and cover traced execution and
  prepared execution wherever the package applies to both. Include an independent
  expected result for each new behavior, even when reusing analytical logic.

## A. Zero-duration RT packets

**Current gap.** The event-driven runtime rejects all nominal RT durations below
4 ns. Play already models the zero-duration queue guard in `replay_queues`.

**Required behavior.** Execute the packet's effects once at the normal execution
point. When its effective duration is zero, suspend the sequencer's underflow
guard and attempt the next dequeue 4 ns later. If the FIFO is empty at that
point, keep RT waiting. Enqueuing a packet wakes that sequencer immediately once
the 4 ns have elapsed, with no polling or duplicate dequeue. A nonzero packet
restores the guard. Durations 1–3 ns retain the current error.

Use the barrier release as the origin for the 4 ns after `wait_sync 0` and the
matching arrival for `wait_trigger addr,0`. Apply the same rule to a selected
`set_cond` else duration of zero, following Play's existing model. Treat zero
as a real queue duration, keeping the operand and the actual dequeue spacing
distinguishable in traces. If Q1 stops while RT is waiting on an empty queue,
finish the drain. If Q1 is blocked and there is no remaining input that can
wake it, report blocked execution with its cause. Leave the underflow flag
clear in both cases. See [Qblox LINQ data receiving](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#data-receiving)
and [Play queue tests](../tests/test_q1timeline_playback_timing.py).

**Acceptance.** With explicit RT startup at 4 ns, `wait 0`, twenty `nop`s,
`wait 4`, `stop` dequeues the two RT packets at 4 and 88 ns without underflow.
With both packets available, consecutive zero packets dequeue 4 ns apart.
Test `upd_param 0`, barrier and trigger arrival, a conditionally skipped zero
packet, a late `fb_pop_data` and `fb_pull_data`, an input that never arrives,
and a late enqueue after a nonzero packet that still reports underflow. Include
a same-time enqueue and wake-up, a peer that continues independently, and
default buffered startup.

## B. Full-product multiply

**Current gap.** `mulu32` and `muls32` have signatures and static low/high word
computation, but the concrete executor and simulator reject them. Static
analysis currently proves only a subset of their flags.

**Required behavior.** Use the existing three signatures for each mnemonic.
Read both operands before writing either destination. Multiply two unsigned or
signed 32-bit operands as selected by the mnemonic, split the 64-bit result
into raw low and high words, and write both through the existing register
readiness and provenance path. Issue time is 24 ns and there is no RT packet.
Use the existing write-ready convention unless a hardware trace establishes
different timing for the two destinations. An input may alias a destination.
Two destinations resolving to the same physical register have no established
write priority. Keep them unknown in analysis and reject the concrete case
with an explicit diagnostic until that priority is verified.

Compute `ZF` from the full product and `NF` from bit 63 of the 64-bit word.
Set `CF` and `OF` together when the product falls outside the corresponding
unsigned or signed 32-bit range. This is the spec's interpretation of the
reference's 32-bit overflow wording. Use the same rule for known operands
across execution modes, retain conservative flags for symbolic operands, and
verify the interpretation with a hardware vector before claiming flag parity.
Reuse `_execute_classical` instead of adding a second multiplication path.
See the [Qblox Q1ASM instruction reference](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html)
and [static multiply tests](../tests/test_q1timeline_multiply_precision.py).

**Acceptance.** Verify unsigned `0xffffffff × 2` gives low `0xfffffffe` and
high `1`. Signed `−1 × 2` gives low `0xfffffffe` and high `0xffffffff`.
Signed `−2147483648 × −1` gives low `0x80000000` and high `0` and sets overflow.
Cover a zero product, a negative in-range result, signed and unsigned flag
branches, all operand forms, both alias cases, and a read before and after the
write-ready boundary. Keep the `…32h` and `…32l` regressions passing.

## C. Net-zero compensation

**Current gap.** Static analysis can show a `play_netzero` packet and warn
about possible overlap using `maxt`. The event-driven simulator blocks on
`play_netzero` and `reset_netzero`.

**Timing and latch.** Execute `play_netzero maxt,duration` with the existing
immediate and register forms. Capture `maxt` at Q1 issue. At RT dequeue apply
the packet's normal latch update and start compensation on configured eligible
baseband QCM outputs. Advance the RT queue by `duration`. `maxt` is an
independent 32-bit limit on the compensation pulse. An explicitly disabled
output produces no compensation and the packet still consumes `duration`.
For timing-only traces with no output model, retain `maxt`, report compensation
as unmodeled, and preserve the existing conservative overlap diagnostic.

`reset_netzero` issues on Q1 as a one-shot latch. Carry its flag to the next
latch-updating packet and reset the applicable compensation and inverse
high-pass accumulators when that packet executes. Use the documented output
and inverse high-pass enable requirements. Ordinary waits leave it pending.
A conditional skip discards the captured flag without applying it. A Q1-only
reset emits no standalone RT packet. Keep the flag separate from NCO phase
reset and generic FIR/IIR state. See
the [Qblox Q1ASM reference](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html)
and [net-zero compensation guide](https://docs.qblox.com/en/main/products/architecture/modules/net_zeroing.html).

**Physical stage.** Require explicit eligible output mapping, enable state,
waveform samples, compensation amplitude and slew settings. Track signed
area independently per modeled output in normalized full-scale × ns. Generate
an opposite-area pulse within `maxt`, the amplitude limit and the slew limit.
Use the Qblox reference waveform and area example as independent fixtures.
Keep the waveform and its end time separate from the RT packet. Use an explicit
unknown or insufficient-capacity result when inputs are absent or the requested
area cannot be compensated under the supported model. Exact IHP state, hardware
quantization, overflow and retrigger behavior require hardware reference vectors
before advertising parity. See the [Qblox worked net-zero example](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QCM/202_net_zeroing.html).

**Acceptance.** Cover `maxt > duration` with additional waits, register `maxt`
changed after enqueue, an explicitly disabled output, and a conditional skip.
Test reset across a plain wait, the next latch update, and a subsequent packet
that must not reset again. For a fully specified waveform, check that the
compensation has the opposite sign, its integrated area cancels within one
sample's quantization tolerance, and its end is bounded by `maxt`. Cover no
compensation for zero accumulated area and independent output accumulators.
Keep the [overlap tests](../tests/test_q1timeline_netzero.py) passing.

## D. Digital acquisition and threshold updates

**Input contract.** Add an explicit digital trace per scenario sequencer with an
initial binary level and strictly ordered timestamped binary transitions. Use
integer ticks of 1/128 ns on the existing shared simulation clock for scenario
transitions. An explicitly flat trace can produce a modeled count of zero.
Without an input trace or applicable supplied digital result, report the result
as unavailable. Keep generic IQ acquisition inputs separate.

- `acquire_timetags acq,bin,enable,fine_delay,duration` opens and closes a QTM
  window. Associate the result with the **closing** operands. The window's
  effective edges use `fine_delay`. RT scheduling uses `duration` separately.
  Start with zero fine delay and an explicit half-open window `[open, close)`.
  Enable nonzero delays only with a documented conversion and boundary test.
  Count rising edges and report one selected relative timetag per bin. Cover
  `first` with `start` and `sequencer` reference modes first. Carry the count,
  validity, effective window, and source event provenance separately. An
  empty window has count zero and no valid selected timetag.
- `set_time_ref` carries a one-shot reference flag to the next latch-updating
  packet. In `sequencer` reference mode use that packet's RT execution time.
  A close packet may apply the flag and use the new reference on that same
  close. Keep it separate from the window start and from Q1 issue time.
- `acquire_digital acq,bin,duration` samples the QTM digital level into the
  addressed bin. Model an ideal sample at the RT start and label it as such.
  The duration is the RT wait, independent of the sample. Verify hardware
  sampling offset and exact transition ties separately from this ideal mode.
- `acquire_ttl acq,bin,enable,duration` gates the QRM TTL counting path until
  explicitly disabled. Count detected rising edges while enabled. Take the
  target acquisition and bin from enable. Support fixed bin first and use
  explicit `ttl_acq_auto_bin_incr_en` for per-edge bin advancement. Handle bin
  bounds and report an unclosed gate as incomplete. Keep this path separate
  from QTM timetag windows and analog integration.
- `upd_thres index,value,duration` updates the QTM count classification
  threshold at RT execution, keyed by the documented index. Keep the raw
  count and its classification separate. For configured low and high count
  thresholds, counts below low are `low`, counts at or above high are `high`,
  and the interval between them is `mid`. Missing or unsupported threshold
  configuration must not generate a fabricated result or trigger. Preserve
  the current binary trigger-route contract. Truth-table routing is separate.

**Acceptance.** Open at 100 ns and close at 120 ns with rising edges at 104,
116, and 120 ns. Expect two edges and a first tag 4 ns relative to start in the
ideal window model. Use a different bin on close, then test an empty window,
a skipped close, and a reference update at 96 ns that gives 8 ns for the first
tag. Test high and low digital levels, explicit TTL enable/disable across
multiple RT waits, fixed and incremented bins, and classifications around
both thresholds before and after `upd_thres`. Expose edge occurrence times
and the window result to E. See the [QTM sequencer guide](https://docs.qblox.com/en/main/products/architecture/sequencers/timetag.html),
[QTM count-based feedback tutorial](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QTM/count_based_feedback.html)
and [QRM readout guide](https://docs.qblox.com/en/main/products/architecture/sequencers/readout.html).

## E. LINQ payload configuration and QTM streams

**Current gap.** Basic common-data, TB, and IQ feedback use the existing route
and FIFO model. The extra, write-combine, mock TB, and QTM time-tag instructions
have signatures or static annotations, but no event-driven payload behavior.

- Apply `fb_com_cfg`, `fb_com_extra`, `fb_acq_tb_cfg`, and `fb_acq_tb_extra` as
  RT configuration for the relevant transmit stream. Keep common data and TB
  state separate. Capture the applicable readout configuration for each
  acquisition using the existing TB and IQ snapshot rule. Preserve plain
  sends when these features are disabled.
- Start write-combine with the documented one-word TB case. Merge same-ID
  contributors to the same receiver at the same acquisition completion time
  into their configured, non-overlapping bit ranges. Emit one FIFO entry
  carrying all contributor provenance, and apply the configured route latency
  once. Diagnose incompatible widths or colliding bit positions. Keep entries
  from different completion times separate. Establish vectors for common-data
  modes and multiword assembly before enabling them. See
  [LINQ data sharing](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#data-sharing).
- `fb_acq_tb_mock` selects mock valid and data bits for the TB stream. Before
  enabling it, establish when the mock emits and how the fourth operand is
  interpreted on the target firmware. The Qblox 2026.10.2 instruction table
  calls that operand `duration`, while its description calls it `count`.
  Likewise, require a driver or hardware vector for extra-byte placement,
  padding, and receiver FIFO word order. Unsupported encodings remain explicitly
  blocked. See the [Qblox Q1ASM reference](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html).
- `fb_llp_tags_id` and `fb_tdc_tags_id` stream from D at each valid edge inside
  the QTM window. `fb_llp_ttls_id` sends the count at close and
  `fb_tdc_tdelta_id` sends the time delta at close. The ID is per stream and
  zero disables it. Use the existing explicit route latency. Preserve the
  documented 1 ns LLP and 1/128 ns TDC payload units, and verify the wire word,
  timestamp origin, invalid-result policy, and scheduling quantization against
  the QTM reference vectors before advertising support for each form. Keep
  stream emission time separate from the time encoded in the word. See the
  [QTM LINQ tutorial](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QTM/424_linq_qtm.html).

**Acceptance.** Two valid TB contributions `0b10` at bit 0 and `0b11` at bit 2
with a shared ID and close time produce one receiver word `0x0e`. Test the
uncombined case, missing or colliding contributors, FIFO capacity accounting,
an ID set to zero, a skipped configuration packet, and unchanged existing TB
and IQ routes. For QTM, two edges yield two ordered tag sends and one count send
at close when enabled, with configured arrival delay and source provenance.
Use exact expected words for each enabled mock, extra-byte, and QTM encoding.
Include both selective pop and FIFO pull as receiver checks.

## F. Configured readout integration

**Reproduction.** With `hardware_parameters.integration_length_acq: 200`,
`acquire 0,0,8` and a supplied IQ result, static analysis reports a 200 ns
integration window. The event simulator instead completes after 8 ns. The CLI
attaches hardware parameters after running simulation. Watch and DEF run entry
points also need the timing input before execution.

**Contract.** Pass per-sequencer integration settings through the project CLI,
watch and prepared DEF paths into `simulate_programs`. Keep standalone API
callers working without project settings. Resolve the unweighted acquisition
length once at the RT start, in order from explicit
`simulation.physical.sequencers[id].integration_length_ns`, to the project's
`hardware_parameters.integration_length_acq`, to the existing positive RT
duration fallback. Record the chosen source and effective length. Preserve
weight-derived timing for weighted acquisitions. A zero-duration RT acquisition
still needs a positive integration length from configuration or its weights.
When no usable duration is known, report that cause explicitly.

Use the resolved length for both supplied results and physical integration.
Emit `acquisition_result`, LINQ and acquisition-trigger sends at the effective
integration completion. A subsequent acquisition on the same engine can
interrupt the integration, preserving the existing interrupted-result
contract. Keep RT dequeue cadence, the `acquire` instruction block and its
latched settings tied to the RT duration. Attach integration metadata to the
result using existing clock and IR conventions. See the
[Qblox LINQ acquisition timing note](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#readout-sequencer-data-types).

**Acceptance.** The 200 ns fixture completes at RT start plus 200 ns, the RT
block remains 8 ns, and any routed arrival uses that completion plus its route
latency. Test supplied and physical inputs, an explicit simulation override,
the existing fallback, a zero-duration packet with configured integration,
interruption, and two sequencers with different lengths. Exercise CLI, watch
and prepared DEF entry points. Use the existing
[acquisition window tests](../tests/test_q1timeline_acquisition_windows.py).

## G. IQ accumulator transmission

**Reproduction.** Raw scenario I/Q values `±2**35` with `fb_acq_iq_shift 8,4`
currently fail the signed 32-bit check before the shift. The shifted values
`±2**27` fit in a LINQ word.

**Contract.** Treat raw integer scenario I/Q as signed accumulator values and
keep the existing explicit scale for numerical acquisitions. Validate finite
values and the signed 48-bit accumulator bounds after conversion. Preserve
the existing numerical rounding convention for scenario conversion. Select
the 32-bit word from the accumulator after the configured shift, with the
lowest 32 bits selected at shift zero. Use the signed right-shift model and
serialize the same raw 32-bit word through the existing FIFO. Keep any sign
extension beyond bit 47 documented as a model assumption until checked with a
device vector. Emit I first and Q second at the same completion time, with
the existing ID and provenance. See [Qblox IQ transmission](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#iq-values).

**Acceptance.** Test `±2**35` at shift 8, a nonzero high accumulator with
shift zero, signed 48-bit boundaries, invalid values, and conversion with an
explicit scale. Assert exact raw receiver register words, the I/Q order and
queue capacity consumption. Verify the acquisition snapshots its configured
shift and existing small values still round-trip.

## H. Trigger counters in Play and simulation

**Reproduction.** Address 1 arriving after `set_latch_en 1,4` and at the first
conditional `wait` makes the simulator execute that packet. Play currently
blocks at the enabled `set_cond` even when Configure has the arrival.

**Contract.** Use the project sequencer's `hardware_parameters` as the shared
source for address settings. Support the Qblox names
`triggerN_count_threshold` (integer 0–65535) and
`triggerN_threshold_invert` (boolean), with `N` from 1 to 15. Omitted settings
retain the current threshold 1 and inversion false. For selected addresses,
compare the counter with `>= threshold`, then invert as configured before
applying the existing operator and mask. Preserve counter enable, reset and
disabled-condition semantics. Share the predicate and validation between the
engines. See [Qblox trigger handling](https://docs.qblox.com/en/main/products/architecture/sequencers/trigger_handling.html#set-cond)
and the [Sequencer API](https://docs.qblox.com/en/main/products/qblox_instruments/api/sequencer.html).

In Play, process configured arrivals on the raw analytical RT clock and
evaluate the predicate at each affected RT packet. Apply `set_latch_en` and
`latch_rst` only when those packets execute. Process arrivals before RT effects
at the same timestamp, as the event simulator does. The independent
`wait_trigger` consumption rule still applies. An arrival consumed by a wait
can count once in an enabled counter. An explicitly empty input source for
the sequencer permits a zero-arrival scenario. When an input needed to decide
the condition is missing, stop with a specific incomplete reason.

A false condition produces the selected else duration and skips all RT
effects while Q1 keeps executing. Preserve the captured setter values and
existing event links. Retain decisions, input provenance and assumed times in
the trace. For each queue replay scenario, validate that replayed counter
decisions still agree after any shifted dequeues or barrier releases. If they
differ or cannot be established, mark that scenario incomplete so it cannot
certify queue timing for a different path. Keep input edits and branch
assumptions deterministic when Play replays from the start.

**Acceptance.** Exercise zero, one and two arrivals with threshold 2, inverted
thresholds, threshold zero, a reset, a disabled latch and an arrival at the
dequeue boundary. Compare Play and simulation with equivalent arrivals and
known timing. Verify only the selected else duration is used and skipped
updates leave hardware state alone. Cover an arrival also consumed by
`wait_trigger`, missing and explicitly empty input, same source broadcast to
two sequencers, and a queue replay whose moved dequeue changes the decision.
Keep the [external input tests](../tests/test_q1timeline_external_inputs.py) passing.

## I. SYNQ participation

**Reproduction.** `wait_sync 4` completes for sequencer `a` alone. Adding an
independent sequencer `b` with only `wait 4` leaves `a` blocked under the
simulator's all-sequencer participation rule.

**Contract.** Accept boolean `hardware_parameters.sync_en` on each project
sequencer. Preserve the existing all-participate behavior for unspecified
values. An explicit false excludes that sequencer from a SYNQ barrier even
when it is present and active. A declared participant that never reaches the
matching barrier keeps the others waiting. Preserve independent progress and
the existing 4 ns dequeue rule for `wait_sync 0`. Treat `wait_sync` issued by
an explicitly disabled sequencer as an unsupported configuration with source
diagnostics until that behavior is verified on hardware.

Use the same resolved participant set in the event simulator, analytical
underflow replay and Play queue checks. Give those paths the project settings
before execution, including CLI, watch and prepared runs. Participants derive
from configuration and program inputs. Lane visibility and execution status
have no effect on membership. See [Qblox SYNQ](https://docs.qblox.com/en/main/products/architecture/cluster/synq_linq.html#synq)
and the [Q1 sequencer synchronization rules](https://docs.qblox.com/en/main/products/architecture/sequencers/sequencer.html).

**Acceptance.** Explicitly disable `b` in the fixture and assert `a` releases.
Set `b` true and assert `a` waits. Include two active participants with
different arrival times and durations, consecutive barriers, a missing or
failed participant, an explicitly disabled `wait_sync`, and the default
legacy behavior. Verify replay diagnostics and event simulation agree about
membership. Test a hidden lane and a zero-duration barrier.

## J. QTM digital output and loopback

**Reproduction.** `set_digital 1,1,0`, `upd_param 16`,
`set_digital 0,1,0`, `upd_param 16` records high and low in RT applied state.
The simulator emits no corresponding output transitions or connected input
events.

**Contract.** Represent QTM output level separately from analog DAC stages.
Use the existing `set_digital` latch provenance. When a latch-updating packet
executes, schedule the configured output change from that RT time and the
setter's `fine_out_delay` in the digital clock domain. A skipped packet has
no output effect. An unchanged level produces no new edge. Honor the
documented single-output mask and handle other mask behavior only when
supported by the target reference. Keep effective edge time separate from
packet duration. Start with zero fine delay and enable nominal sub-ns delay
with an explicit tick conversion and tests. Label hardware quantization as
unverified until checked against a device trace.

Allow an explicit directed digital connection between project sequencers with
a validated nonnegative tick delay. Use one digital source per input in this
model and reject simultaneous explicit trace and routed drivers for that input.
Feed the existing digital acquisition path used for scenario transitions so
`acquire_digital`, timetag windows and QTM LINQ see the same edge. Preserve
source and packet IDs, causality on zero-delay connections and the existing
window boundary policy. Retain output edges that occur after the RT FIFO
drains until they are processed. See [Qblox QTM output timing](https://docs.qblox.com/en/main/products/architecture/modules/qtm.html).

**Acceptance.** The fixture produces a 16 ns high pulse. A configured delay
moves both receiver edges by that delay without changing their separation.
Sample the receiver inside and outside the pulse and count its rising edge in
a timetag window. Test coincident window boundaries, a skipped setter packet,
repeated same-level updates, fractional-tick delay and an edge after RT drain.
Check the trace uses the shared clock and the existing RT display alignment.

## K. Stop code and source

**Reproduction.** `wait 4` followed by `stop 0` or `stop 17` produces two
indistinguishable terminal RT events. The simulator discards the code and can
attach the stop event to a later source location.

**Contract.** Resolve the optional status operand when `stop` issues on Q1.
Use signed 32-bit interpretation for both immediate and register forms and
default to zero for bare `stop`. Preserve register validation and carry an
unresolved static value explicitly. Record `stop_code` and the stop
instruction's actual source in execution metadata and in the terminal RT
event when present. Retain source linkage in IR and surface the code when the
user inspects the stop. Complete Q1 immediately according to existing Q1
timing, then drain queued RT work with the same terminal timestamp. Nonzero
codes are program results and leave the existing execution outcome intact.
See the [Qblox `stop` instruction](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#stop-instt).

**Acceptance.** Test bare `stop`, `stop 17`, a signed register value of `-1`,
and a stop reached before later source lines. Verify Q1 stop and RT drain
timestamps, source selection, static and concrete traces, and prepared
execution metadata. A Q1-only stop preserves its current timeline behavior
and still reports its code. Faults keep their existing error classification.

## L. QTM fine delay and time delta

**Current gap.** QTM input windows accept only zero fine delay, `first` source
and `start` or `sequencer` reference. `fb_tdc_tdelta_id` blocks when enabled.
Qblox publishes a test program using `second` with `first4` on the receiving
channel. Its two rising edges are 100 ns apart and the expected receiver word
is 12,800 ticks. See the [QTM LINQ tutorial](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QTM/424_linq_qtm.html)
and [QTM channel API](https://docs.qblox.com/en/main/products/qblox_instruments/api/modules/qtm/output.html).

**Contract.** Apply the nominal fine delay to each effective window edge in
integer 1/128 ns ticks. Keep RT packet time on its existing clock. Use the
same modeled tick convention as J. Process digital transitions at the exact
effective boundaries using the half-open interval. Preserve delayed window
completion after RT drain and report incompatible open/close ordering. Label
the delay as nominal where hardware quantization has no reference vector.

Select `first`, `second` or `last` from the effective window. Support `end`
and `firstN` in addition to the existing references. Resolve channel numbers
from explicit module/channel identity. The same-channel `firstN` may refer to
the first event in the current close, as in the Qblox example. A different
channel uses its most recently closed window as documented. Require explicit
tie handling for coincident cross-channel closes and keep the delta unavailable
if the available evidence cannot establish which reference applies.

For `fb_tdc_tdelta_id`, use the selected source event minus the selected
reference in signed 32-bit ticks. Emit the raw two's-complement word on
effective window close, using the existing route latency and source/packet
links. Preserve exact tick times. If the feedback receiver cannot yet schedule
fractional arrivals, report that limitation instead of rounding silently.
Handle `record_0`, `discard` and `error` explicitly for invalid deltas. Record
zero and validity separately. Enable invalid-result wire behavior only where
the reference or a device vector confirms it. A missing trace remains distinct
from an observed window with too few edges. See
[Qblox LINQ time delta](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#time-delta).

**Acceptance.** Reproduce the 100 ns example, assert exact receiver word
12,800 and provenance, then cover a negative delta, a missing second edge,
independent open and close delays, a delayed close after stop, start/end and
cross-channel references, and ID zero. Preserve the current zero-delay path
and QTM stream count and order.

## M. Scope capture

**Current gap.** QRM scope windows are described in static IR but omitted in
simulation. QTM `set_scope_en` is latched and visible, but enabling it does
not record the scope trace. Ordinary binned results cover different data.

**QRM and QRM-RF.** For a configured sequencer-triggered path, start a
separate scope window on executed acquisitions from the selected sequencer.
Use the existing module/sequence identity and selection metadata. Capture
16,384 samples per eligible path on a 1 ns grid. Use physical input at the
ADC stage when available, or retain the known scope window and an unavailable
sample result. Keep scope timing separate from the RT block and F's binned
integration. Follow the existing retrigger window treatment and report whether
the previous capture completed before overwrite. Expose the scope window via
the existing IR field and bound numerical export using the current sample
limits. Host storage, level-triggered capture, and hardware scope averaging
have separate input and state requirements. See the [Qblox readout guide](https://docs.qblox.com/en/main/products/architecture/sequencers/readout.html)
and [scope tutorial](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QRM/030_scope_acquisition.html).

**QTM.** Start with explicitly configured `timetags-windowed`. Apply
`set_scope_en` at RT and record scope `OPEN`, `RISE` and `CLOSE` observations
for the selected window, with absolute scenario ticks and source links.
Separate the trace capacity and truncation from the binned event count. The
Qblox trace limit is 2,048 timetags. Continuous `timetags` uses a separate
capture lifetime that can outlast the binned window. Implement that mode
against its published start and stop example, with a bounded scenario
horizon. See the [QTM scope tutorial](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QTM/130_scope_mode_qtm.html)
and [timetag sequencer guide](https://docs.qblox.com/en/main/products/architecture/sequencers/timetag.html).

**Acceptance.** A short QRM `acquire` with a 200 ns integration can produce
an independent 16,384 ns scope capture. Test path selection, early retrigger,
sample unavailability and cap, and traces that outlast the RT queue. For QTM,
two rising edges in a scope-enabled window produce an ordered trace and one
binned result. Test disabling the latch, a skipped window, and trace overflow
without altering the bin count.

## N. Compiled branch addresses

**Reproduction.** Q1Lens blocks numeric and register targets in any program
with a legacy macro expansion. Qblox Instruments 1.4.1's `q1asm_windows -d`
confirms `jlt R,I,target` lowers to `cmp` plus `jb`, three-operand `jge` to
`cmp` plus `jae`, and `loop` to `sub R,1,R` plus `jnz`. In the following
program, `@done` resolves to compiled address 7 and source index 5.

```q1asm
move @done,R0
nop
jmp R0
again: jlt R2,5,@done
loop R3,@again
done: stop 17
```

**Contract.** Build and use a compiled address map for documented macro
forms. Track source index separately for source navigation, diagnostics,
branch occurrences and the existing compact representation. Resolve labels
used as data to their compiled address. Interpret immediate or register jump
targets in compiled address space. Validate the address and identify whether
it lands at a source instruction entry or inside a macro. Support entry
targets first. Execute an interior target only when the lowered operation's
semantics, register readiness and source provenance can be represented. Keep
a specific incomplete reason for any remaining interior target. Preserve the
existing timing of ordinary source-level macro execution until checked with
separate timing evidence. The assembler dump proves addresses and lowered
opcodes. Use device or timing references for Q1 cycle claims. See the
[Qblox Q1ASM instruction reference](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html).

**Acceptance.** Run the example through concrete execution, Play and
simulation. It reaches `done` with `R0=7` and the terminal source line is 6.
Add numeric and register cases before and after every macro kind, a label
alias, an unknown address, and a compiled interior target. Add assembler
golden vectors as offline fixtures so standard execution needs no assembler
installation. Verify direct labels and programs without expansion continue
to behave as before.

## O. LINQ assembler forms

**Reproduction.** The shipped Qblox Instruments 1.4.1 assembler accepts
packed register forms and disassembles them as `fb_cmd`. For example,
`fb_acq_tb_cfg 1,3,4,4` becomes `fb_cmd 0x42,0x30401,4` and
`fb_acq_tb_cfg R1,4` becomes `fb_cmd 0x42,R1,4`. The runtime currently
blocks these equivalent forms. The published instruction page omits several
of the register forms. Keep the assembler evidence versioned alongside the
[published Q1ASM reference](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html).

| Source operation | Command in Qblox Instruments 1.4.1 dump |
| --- | --- |
| `fb_tdc_tags_id`, `fb_tdc_tdelta_id` | `0x00`, `0x01` |
| `upd_thres index,value,duration` | `0x10` through `0x13`, payload `value` |
| `fb_llp_tags_id`, `fb_llp_ttls_id` | `0x20`, `0x21` |
| `fb_acq_tb_id`, `fb_acq_iq_id` | `0x40`, `0x41` |
| `fb_acq_tb_cfg`, `fb_acq_tb_extra` | `0x42`, `0x43` |
| `fb_acq_tb_valid`, `fb_acq_iq_shift`, `fb_acq_tb_mock` | `0x5b`, `0x5c`, `0x5d` |
| `fb_com_cfg`, `fb_com_extra` | `0x60`, `0x61` |
| `fb_com_data id,value,duration` | `0x100 + id`, payload `value` |

For packed configuration, the verified word is
`(bit_pos << 16) | (length << 8) | wc`. `bit_pos` is 10 bits, `length` 7
bits and `wc` 2 bits. The extra-byte word is `(enable << 16) | extra` with
16 bits for `extra`. Mock flag vectors place enable, valid and data in bits
0, 8 and 4. The assembler also accepts 16-bit `fb_pop_data` operands, while
the documented network ID space is 8-bit. Keep the assembler range distinct
from the configured route ID range until upper-bit semantics are verified.

**Contract.** Use one semantic operation per verified assembler alias. Decode
operands after register resolution, preserve the original mnemonic and source
for navigation, and run existing validations on the decoded fields. Dispatch
only commands whose effects are supported by E or L. An opcode with unknown
or device-specific behavior must report the unresolved operation. The
assembler confirms field encoding, including mock command `0x5d`. It cannot
establish whether the mock's last operand controls wait duration, number of
samples, or both. Obtain an emission and timing vector before enabling mock.
The same distinction applies to enabled extra bytes and common-data packing.

Update `tools/verify_q1asm_docs_snapshot.py` to recognize the assembler-backed
`fb_cmd` alias and the exact `fb_pull_data R,R` documentation discrepancy.
The generated instruction table lists 4 ns, but the dedicated LINQ guide
specifies two writes in 8 ns. Report the exception with both sources and
continue to fail for any other unverified timing difference. Keep the public
Q1ASM signature comparison distinct from firmware behavior tests.

**Acceptance.** Capture assembler golden rows for immediate, register and
raw forms. Verify a normal `fb_com_data 42,R1,4` and its raw command
`fb_cmd 0x12a,R1,4` produce identical messages. Cover the TB, IQ and supported
QTM configurations, unknown command IDs, reserved bits, malformed values,
and a conditionally skipped packet. Assert each normalized opcode preserves
the original source and RT packet linkage. Verify the documentation snapshot
check exits successfully with only the explicitly recorded difference.

## P. QTM trigger forwarding and truth tables

**Current gap.** QTM digital acquisition records ideal edges, sampled levels,
and count classifications. These results cannot currently send hardware-style
trigger events that release `wait_trigger` or feed `set_cond`. The existing
binary readout trigger routes only cover the analog acquisition path.

**Edge and level forwarding.** Accept explicit input channel settings and
receiver routes. Map enabled rising and falling modes to the corresponding
digital transitions within the effective acquisition window. Map sampled-high
and sampled-low to the matching result from executed `acquire_digital`.
Keep forwarding independent of when a binned window completes. Edge forwarding
can release a wait in the program that will later close that same window.
Use explicit route latency as in the existing trigger routes. Preserve the
transmitting edge or sample's provenance on both send and receive. See the
[QTM trigger guide](https://docs.qblox.com/en/main/products/architecture/sequencers/timetag.html#trigger-sources)
and [QTM channel API](https://docs.qblox.com/en/main/products/qblox_instruments/api/modules/qtm/output.html).

**Count truth table.** Accept explicitly configured rules grouped by QTM
module with channel identity, required classification and trigger address.
Evaluate rules from window-close classifications for the same measurement
round. Preserve low, mid, high, and invalid as distinct outcomes. Define the
initial model for coincident effective window closes, collecting all results
before evaluating a round. Report unsupported cross-round matching or
ambiguous rules instead of mixing old and new results. Expose which source
events satisfied the rule and use the existing trigger arrival path. Use
the [Qblox photon-count example](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QTM/count_based_feedback.html)
as a reference for a two-channel rule. Network launch grid, conflict handling
and hardware limits require their own timing vectors for hardware parity.

**Acceptance.** Forward a rising edge inside a window and release a wait
before the window closes. Cover falling and sampled-level settings, disabled
and skipped instructions, an edge at the window boundary, and a positive
route delay. For coincident windows, assert that `high` and `mid` on two
configured channels send exactly the specified address once, with both
source links. Cover invalid results, missing peer, unknown rule, differing
close times, and a downstream conditional counter.

## Q. Shared simulation time origin

**Reproduction.** Start RT at 32 ns. Run `wait_sync 4; wait 4; stop` on `a`
and `wait 20; wait_sync 4; wait 4; stop` on `b`. Both post-barrier waits are
56–60 ns on the simulation clock. Current default alignment subtracts the
start of each lane's own `wait_sync`, displaying the waits at 24–28 ns on `a`
and 4–8 ns on `b`. Independent lanes without a local sync anchor can also
emit a missing-anchor diagnostic despite sharing the same simulation clock.

**Contract.** Apply a single display offset to all sequencers in an
event-driven run. Use the earliest RT dequeue anywhere in the run as the
default origin. Q1-only runs keep their own clock origin. For an explicitly
requested event anchor, pick one concrete project-wide candidate using a
documented stable earliest-time rule. An anchor present in any lane suffices.
Use the configured `none` or manual offset when explicitly selected. Preserve
per-sequencer alignment for static comparison, where local timelines have
no common simulated clock. Record the anchor and offset in IR and apply the
same transform to trigger and LINQ arrows, signal and acquisition times,
register history, fractional digital events and Hardware cursor queries.
Keep execution and scenario input times on their original clock.

Default Normal begins at zero without the Q1 prefill interval. Debug may
show negative Q1 times. An explicit later anchor must preserve access to
actual pre-anchor RT events. A shared anchor missing from the whole run has
one explicit fallback explanation. Reuse existing clock and renderer controls.

**Acceptance.** The two post-barrier waits display at the same time, and a
known send-to-receive interval is preserved across lanes with unequal initial
Q1 prefill and RT starts. Cover `first_anchor` using an RT dequeue, explicit
`none` and manual alignment, an independent `sync_en: false` lane, a Q1-only
run, and the existing single-lane trigger example with Normal at 0 ns.
Verify the IR and the visible labels for aligned fractional digital times.

## R. Scheduled LINQ receipts in Play

**Previous gap.** Configure supplied scheduled triggers. Mixed-ID LINQ FIFO
traces required the event-driven simulator.

**Implemented.** `external_inputs.feedback` adds one optional arrival schedule
per receiver, edited in Configure. Play uses the shared FIFO selection rules
and the configured feedback capacity. The scenario keeps arrival provenance and
distinguishes an empty receiver from an omitted receiver. See
[LINQ receive schedules](external-inputs.md#linq-receive-schedules).

**Contract.** Extend explicit playback external inputs with a validated
per-receiver schedule of 8-bit LINQ IDs and 32-bit data words. Define each
arrival time on that receiver's unaligned analytical Q1 clock and retain a
stable provenance ID. Use stable FIFO order for ties and respect the
configured or existing default FIFO capacity. As Q1 reaches a receive,
`fb_pop_data` discards older nonmatching entries and waits for the selected
ID. `fb_pull_data` consumes the oldest available entry and writes both ID and
data destinations. Both instructions charge 4 ns for fetch and wait until
the arrival's 4 ns Q1 tick. Reuse the event simulator's receive semantics and register
readiness. A receipt delay changes later Q1 issue times and leaves RT already
queued work alone. Use the [Qblox LINQ data receiving rules](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#data-receiving).

Expose scheduled receipts through the existing Configure round trip,
preserving existing trigger-only projects and per-occurrence pop assumptions
when no explicit FIFO schedule applies. Make scheduled consumption and any
incomplete timing assumption inspectable. A known empty FIFO can stall Q1.
An omitted source remains unknown. Keep the event-driven simulator available
for feedback produced by actual modeled transmitters, RT backpressure and
transport. Play queue checks must report incomplete if a timing assumption
could select a different receipt or leave a terminal wait unverified.

**Acceptance.** Feed IDs 9 and 5 in that order. FIFO pull writes 9 and its
data, then selective pop of 5 consumes the second word. Verify the inverse
selective-pop case discards 9, both destinations are linked, same-time arrival
order is deterministic, and an empty receiver blocks with an actionable reason.
Cover positive late delay, signed raw words, bounded FIFO, scenario edit and
reset, and unchanged manual pop behavior without a schedule.

## S. One-shot NCO phase updates

**Reproduction.** Apply `set_awg_offs 16384,0` on an unconditioned update.
Enable a false condition, issue `set_ph_delta 250000000`, and enqueue a skipped
`upd_param 4`. Disable the condition and execute another `upd_param 4`. The
second update currently applies the old phase kick, rotating the modeled
output from `(0.5, 0)` to `(0, 0.5)`. With an initially applied `set_ph`
quarter turn, a skipped `reset_ph` similarly changes the later output from
`(0, 0.5)` to `(0.5, 0)`.

**Contract.** Track `reset_ph` and `set_ph_delta` as one-shot Q1 latches
captured by the next latch-updating RT packet. Ordinary waits leave them
pending. Consume their one-shot flags on capture and apply their effects only
when the capturing packet executes. A conditional skip leaves the physical
phase unchanged. Subsequent packets carry persistent settings and only the
one-shot values newly captured for those packets. Keep `set_ph` and `set_freq`
as persistent latched settings. In particular, a skipped reset must not create
a persistent zero-phase setter. Use the existing event and source navigation
contracts for a setter and the packet that captures it.

Model `set_ph` as replacement of the phase offset. Model the phase kick as an
increment to accumulated phase so a later `set_ph` changes the offset without
discarding that increment. An executed reset clears the accumulated phase
and offset. Preserve continuity across frequency changes. Keep precedence
when reset and an explicit offset or kick are captured together covered by
the existing model or a separate device vector. See the Qblox definitions of
[`reset_ph`](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#reset_ph-instt),
[`set_ph`](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#set_ph-instt),
and [`set_ph_delta`](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#set_ph_delta-instt).

**Acceptance.** Both skipped-update fixtures preserve the previous I/Q phase
through the later unconditioned update. Test a successful quarter-turn kick
exactly once, two separately issued equal kicks, an absolute offset update
after a kick, and a successful reset after a nonzero phase. Verify a preceding
plain wait and multiple queued packets. Assert applied-state and setter
provenance, conditional Play behavior, and matching physical samples.

## T. Compare status-flag branches

**Previous gap.** An unresolved `jz` after `cmp R0,5` with unknown `R0`
omitted **Compare both**. An explicit `both` assumption collapsed the branch.

**Implemented.** A known forward target enables **Compare both** for an
unresolved status branch. Each alternative advances Q1 by its branch outcome's
cost. Jump takes 24 ns for the status form of `jge` and 16 ns for the other
status branches. Continue takes 4 ns. A chosen predicate narrows the possible
flag combinations until an ALU instruction writes the flags again. Unsupported
targets and the existing single comparison level retain incomplete diagnostics.

**Contract.** Reuse the bounded forward-branch exploration for unresolved
status-flag branches with a known forward target. Build separate Jump and
Continue alternatives with the correct Q1 issue times, path-specific events
and RT packets, and source and setter links. Let the existing budget and
join proofs govern the remainder. Only refine flags or register values when
the chosen predicate proves the fact. Keep path-dependent state separate when
the join cannot be proven, and expose the existing incomplete diagnostics
for unsupported targets or exhausted budgets. The normal control-flow
labels remain Jump and Continue. `set_cond` retains Execute and Skip.

**Acceptance.** The `jz` fixture shows Jump with no `wait 12` and Continue
with that block. Include `jge` status form and an unsigned status branch,
both taken and non-taken Q1 timing, a branch-dependent setter, and a known
branch that needs no comparison. Verify Compare both selection and separate
rows in the timeline controls. Keep the existing legacy `jge` and `jlt`
comparison and terminal limits passing.

## U. Qblox trigger transport timing

**Current gap.** Scheduled inputs already represent arrivals and generated
triggers use explicit fixed latency. The simulator has no way to model the
instrument's trigger launch grid or report a trigger-network conflict. The
[Qblox trigger network guide](https://docs.qblox.com/en/main/products/architecture/sequencers/trigger_handling.html#trigger-network)
documents a 28 ns launch grid and 212 ns propagation. The
[instrument troubleshooting guide](https://docs.qblox.com/en/main/troubleshooting.html)
specifies 252 ns minimum trigger spacing.

**Contract.** Add an opt-in transport for generated requests in a declared
shared trigger network. Use an explicit grid epoch in simulation time, or a
resolved shared SYNQ epoch when available and selected. Launch at the first
grid time at or after the request, then add 212 ns and any explicitly
configured extra path delay. Keep the existing fixed-latency routes and
scheduled receiver arrivals on their current contracts. Count fanout of a
single request to multiple receivers as one network transmission. Preserve
request, launch, arrival and route provenance separately. Keep shared
display alignment independent of the calculation.

Detect launches spaced less than 252 ns, including coincident requests.
Diagnose the conflict with both source events and the relevant launch times.
When the instrument's delivery outcome is unknown, stop claiming a known
arrival for the conflicted request. Keep unaffected sequencers and already
observed deliveries valid. An unspecified grid phase must be reported as
unknown instead of silently using a zero-phase network.

**Acceptance.** A grid epoch of 100 ns and a request at 101 ns launch at
128 ns and arrive at 340 ns. Verify an on-grid request, additional path
latency, fanout, 252 ns separation at the exact boundary, and coincident
requests. Exercise both analog and QTM trigger generators. Check that
`wait_trigger` and `set_cond` use the calculated receive times, while direct
scenario arrivals keep their configured times.

## V. Explicit `illegal` fault

**Previous gap.** The simulator treated a valid `illegal` as missing semantics
and recorded no executed instruction.

**Implemented.** All execution modes record the 4 ns Q1 issue and the source of
the fault. The event-driven model handles the fault before an RT dequeue at
that tick. It discards queued packets and ends the active packet's sequencer
span at the fault edge. Analog output behavior beyond that edge needs a device
reference. [Qblox `illegal` instruction](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#illegal-instt)

**Contract.** When execution reaches a valid `illegal`, record a 4 ns Q1
issue, the actual source and an explicit fault with the documented
`SEQUENCE_PROCESSOR_RT_EXEC_ILLEGAL_INSTRUCTION` flag. Halt that sequencer
without draining queued RT work or generating a normal `stop` result. Use
Q1 issue completion as the model's fault edge and document that convention.
Retain prior executed events and independent peer progress. Play reports the
fault as an error while retaining the executed prefix. Static analysis emits
a source-linked illegal-instruction diagnostic and ends that path. Static
path assumptions keep their existing confidence rules. Preserve the separate
parser and runtime paths for unknown or malformed source.

**Acceptance.** A Q1-only `illegal` yields the fault with no normal stop code.
With queued RT packets, verify only work reached before the modeled fault
edge can take effect and a peer still completes. Exercise a not-taken fault
path, concrete and prepared runs, Play prefix inspection and source links.

## W. Play timing after zero-duration packets

**Reproduction.** In Play, schedule arrivals on address 1 at 20, 21 and
40 ns for `wait_trigger 1,0`, `wait_trigger 1,4`, `wait 4`, `stop`.
The first wait consumes the 20 ns arrival. The second wait currently arms at
20 ns and consumes the 21 ns arrival, which queue replay rejects because its
dequeue cannot precede 24 ns. Simulation with fixed 4 ns startup arms the
second wait at 24 ns and consumes the 40 ns arrival.

**Required behavior.** In Play's analytical RT coordinates, advance the next
RT packet's earliest start by 4 ns after an effective zero-duration packet.
For `wait_trigger`, use the arrival as the start of the four-nanosecond
interval. Use the resulting start for scenario matching, latch and counter
evaluation, and the next packet's timeline coordinates. Keep the nominal
packet duration, the wait's arrival, and its earliest next dequeue distinct.
Use the effective else duration for a conditional skip. Leave Q1 timing,
packet FIFO replay, underflow suspension, and nonzero duration behavior as
specified in A. Queue replay may still report additional shifts due to actual
enqueue timing and synchronization. See [Qblox LINQ data receiving](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#data-receiving).

**Acceptance.** The second wait in the reproduction starts at 24 ns and
receives at 40 ns. With the third arrival removed, Play reports waiting for a
new trigger. A 24 ns arrival can release that wait. Also cover consecutive
`wait 0` packets, `wait_sync 0` on the nominal Play clock, an executed RT
update with duration zero, and a conditional skip with `else_duration = 0`.
Include a case where a trigger counted in the four-nanosecond interval changes
the next conditional decision. Assert the RT starts, unmodified durations,
scenario event IDs, and the unchanged Q1 issue times.

## X. Simulation comparison provenance

**Reproduction.** With a 20 ns fixed startup and a 4 ns self route, run
`set_cond 1,1,0,8`, `fb_com_data 1,42,4`, `set_cond 0,0,0,4`, `wait 20`,
`stop`. Compare both outcomes of the first condition. The Execute RT event
uses `a:p0:branch-taken`, while its `feedback_send.source_packet_id` still
names the baseline `a:p0`. Other derived records can retain baseline IDs in
nested provenance. ID reuse between reruns can make such references point to
a different event, even when the ID resolves.

**Required behavior.** Preserve links to the same rerun and comparison group.
Remap RT packet references when the corresponding RT event has been copied.
Remap simulation event links in copied results, including nested acquisition,
QTM truth-table and combined TB records. Keep references to genuinely shared
prefix events and packets. Do not mutate the recorded run or rerun metadata
while copying. Keep packet identity scoped to the specific comparison when two
conditions are compared in one IR. Preserve existing branch rows, source
selection, and baseline Hardware data.

**Acceptance.** The reproduction's Execute feedback identifies its Execute
RT packet and the matching receive references the Execute send. Include a
nested provenance case and a shared prefix reference. Check two Compare both
controls on one sequencer, baseline data preservation, and selection of the
original setter in Normal and Debug. Scope remapping to actual reference
fields, leaving payload values, identifiers, and labels alone.

## Y. Weighted acquisition in analytical timelines

**Reproduction.** Give a project `sequence_json` two uploaded weights at
indices 0 and 1, each with 80 samples. Run `fb_acq_tb_id 5,4`,
`acquire_weighted 0,0,0,1,4`, `wait 200`, `fb_pop_data 5,R0`, `nop`, `stop`
with alignment `none`. The acquire starts at 4 ns. Project analysis and Play
report an unknown integration window, and analytical LINQ has no send time.
Qblox specifies that weighted integration follows the weight length.
[QRM weighted acquisition](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QRM/020_binned_acquisition.html#weighted-acquisition)

**Required behavior.** Carry per-index lengths of valid uploaded weights
through the existing sequence-loading entry points. Use the operands resolved
at the acquire occurrence, including registers and the `acquire_weighed`
alias. For two known equal lengths, show the exact integration window in
analysis, concrete execution and Play. Preserve existing interruption and
uncertain-path handling, and let analytical LINQ use the known integration
end. Keep RT duration, scope window and nonweighted `integration_length_acq`
separate. Missing, ambiguous, or unequal weights stay unknown in analysis.
The simulator's existing unequal-weight policy is unchanged. Project CLI,
Watch, prepared DEF and Play should agree on the weight metadata.

Direct `analyze_text(sequence=...)` and `analyze_documents(sequences=...)`
should use the same metadata when these optional inputs are supplied.

**Acceptance.** In the reproduction the RT block ends at 8 ns, the integration
ends at 84 ns, and self-cast TB is estimated to arrive at 244 ns. Cover
register-selected weights, the alias, a second acquisition truncating the
first, and missing or duplicate indices. With no reliable weight metadata,
preserve the unknown acquisition window and omit a numeric LINQ arrival.

## Z. Module-wide readout scope selection

**Reproduction.** Configure two sequencers in the same QRM module. Place
`scope_acq_sequencer_select: 2` and
`scope_acq_trigger_mode_path0: sequencer` in the hardware parameters of
sequencer 0. Let sequencer 2 execute `acquire 0,0,4`, `stop`. Simulation
produces a 16,384 ns scope window for sequencer 2. Analysis and Play omit it
because they only consult hardware parameters on the acquiring sequencer.

**Required behavior.** Resolve the three module-scope settings across all
sequencers that declare the same module identity. Preserve the existing
per-sequencer behavior when module identity is absent. Use the resolved
configuration for scope capture selection without changing sequencer-specific
integration or other hardware parameters. A contradictory module configuration
must be explicit and must never silently pick a value based on file order.
Reuse the existing simulated conflict contract if practical. Scope state in
the analytical timeline stays local to the selected sequencer.

**Acceptance.** The reproduction shows a separate 16,384 ns window on path 0
in project analysis, concrete mode and Play. Verify settings split across
peers, an unrelated module with the same sequencer index, conflicting values,
and the existing selection for a standalone sequencer without module identity.
Confirm Simulation remains consistent.

## AA. Source diagnostics for rejected Play timing

**Reproduction.** Run `wait_trigger 1,4`, `wait 4`, `stop` in Play with a
receiver arrival at 1 ns. The nominal path consumes the arrival, but queue
replay finds that the first wait cannot dequeue until 4 ns. The replay reason
appears in queue metadata and the diagnostic list is empty. An unmatched
`wait_sync` has the same omission.

**Required behavior.** When replay rejects a recorded trigger because the
packet was not armed at arrival, or finds a recorded barrier with no matching
reachable peer, retain the affected RT packet as the source of incompleteness.
Emit the existing source-linked Play diagnostic for that packet. Preserve the
nominal trace and mark queue timing incomplete. If one replay has a supported
result and the other fails, preserve that distinction in the diagnostic and
leave the existing earliest/prefetched policy intact. Keep complete, partial
and waiting traces distinguishable, including their existing reason lists.

**Acceptance.** For the reproduction there is an `analysis_incomplete`
diagnostic on line 1 with the affected packet ID, arrival 1 ns and dequeue
4 ns. Verify an arrival at the dequeue boundary does not get that diagnostic.
An unmatched barrier identifies the barrier source and missing peer. Add a
normal paired barrier and an earlier incomplete peer to check that diagnostics
do not misidentify the failing sequencer or suppress an existing reason.

## AB. Independent analytical acquisition feedback streams

**Reproduction.** In analysis, configure `fb_acq_tb_id 5,4`, then
`fb_acq_iq_id 6,4`, then `acquire 0,0,4` with an 80 ns integration. The
acquisition starts at 8 ns, but a later `fb_pop_data 5,R0` has no TB source.
Both `fb_pop_data 6,R1` and `fb_pop_data 6,R2` find IQ words. Replacing the
second configuration with `fb_acq_iq_id 0,4` incorrectly removes TB too.

**Required behavior.** Keep enabled acquisition-feedback IDs independently by
stream. An ID of zero disables only its stream. A readout acquire carries one
TB word and two ordered IQ words when both are enabled. QTM edge streams and
close-result streams retain their independent IDs and existing emission-time
certainty rules. Apply only the streams supported by each acquisition type.
Keep the original acquisition event and RT packet as the shared source, and
adapt analytical feedback flow and balance readers to consume all declared
streams while preserving compatibility with the single-stream event shape.
Preserve raw `fb_cmd` equivalence, branch state isolation and conservative
unknown timing. Simulator emission and Play external receipts are unchanged.
[Qblox LINQ data types](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#data-sharing)

**Acceptance.** The reproduction yields one TB arrival estimate at 248 ns
and two IQ word estimates at 252 ns, with balanced payloads on each ID.
Cover disabling or retagging only one stream, a raw alias, and simultaneously
configured QTM count and delta streams on the close. Check that mutually
exclusive branch arms cannot overwrite one another's stream state. Timing for
unobserved individual tag emissions stays unknown.

## AC. Receiver ownership of analytical LINQ self-cast

**Reproduction.** Sequencer A sends `fb_com_data 5,11,4`, waits 200 ns,
then pops ID 5. Sequencer B sends `fb_com_data 5,22,4`, waits 100 ns,
then pops ID 5. The analytical IR pairs both consumers with the other
sequencer's send and raises two route warnings. In the instrument, IDs
1–15 are self-cast and each receiver can only get its own send.

**Required behavior.** Restrict known self-cast candidates to the originating
sequencer for selective pop, FIFO pull and discarded preceding words. Compute
match and discard counts from the same ownership rules. An ID 1–15 send on a
different sequencer must never create an arrival marker or cover an unmatched
receive. Retain an actionable route mismatch hint for an otherwise unmatched
explicit selective receive when the only plausible sources are remote.
Preserve the existing inferred model for non-self IDs and the explicit
simulation/Play routing contracts.
[Qblox LINQ routing](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#data-routing)

**Acceptance.** The reproduction links A to A and B to B without route
warnings, independently of file order and receive order. Cover FIFO pull,
discard isolation when the same self-cast channel is reused by both peers,
and a remote-only send with no valid flow. Verify channel balance and retain
existing non-self route estimates.

## AD. Zero-duration timing in analysis and Concrete

**Reproduction.** Analyze `wait 0`, `wait 0`, `upd_param 4`, `stop` in
analysis or Concrete mode. The two waits and the update all start at 0 ns.
Simulation and Play place the starts at 0, 4 and 8 ns. The zero-duration
operands themselves are valid and are used to suspend the RT underflow guard.

**Required behavior.** Advance the analytical RT cursor by 4 ns after a
known effective zero-duration packet while retaining its zero-width completion
event and packet duration for queue replay. Reuse this rule in Play where
possible and apply it once after the observed arrival for `wait_trigger`.
Use the selected conditional duration, including zero on either or both
branches. Preserve unknown wait-trigger availability, separate queue replay
coordinates, and existing first-RT alignment. Compact loop and branch
summaries must account for known zero occurrences or leave a timing quantity
uncertain when its exact spacing cannot be proven. Keep nonzero durations and
the Q1 clock unchanged.
[Qblox zero-duration queue rule](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#data-receiving)

**Acceptance.** Analysis, Concrete, Play and simulation agree on 0, 4 and
8 ns packet starts and a 12 ns terminal RT coordinate for the reproduction.
Cover an explicit zero else duration, a register resolving to zero, and a
compact constant loop of zero waits. Exercise an affine loop with a zero
iteration and a conditional branch join using zero packets. Assert the RT
cursor is consistent with any claimed exact loop total, queue replay retains
zero guards, and Play's arrival-based boundary tests still pass.

## AE. Arrival markers and consumer source navigation

**Reproduction.** Simulate `wait 100`, `stop` with a scenario LINQ arrival
before the wait ends. `feedback_receive` inherits the `stop` source line and
shows up in source selection for that line. A routed arrival that lands before
its actual consumer can similarly point to the receiver's unrelated current PC.

**Required behavior.** LINQ queue arrival uses the same synthetic source
convention as trigger arrival. Keep flow geometry on the actual receive time
and connect source navigation to the real executed `fb_pop_data`,
`fb_pull_data`, or `wait_trigger` when the arrival has a consumer. An unread
word or an unused trigger has no target source action. Preserve actual send
sources and navigation for older analytical flows. Resolve consumers within
their comparison path so an alternative cannot replace baseline navigation.

**Acceptance.** The reproducer leaves the arrival at the same RT time and
only the real stop events map to the stop source line. A routed LINQ word
received during an unrelated instruction and consumed later keeps the arrival
marker and jumps to the later pop. A matched trigger can reveal the wait
source, while an unmatched arrival has no false source reveal. Check an
alternative alongside its baseline and verify source and target flow actions.

## AF. Standalone RT view switching

**Reproduction.** Render the original trigger execution example with Compare
both to standalone HTML. The separately rendered Normal and Debug views have
the correct lower bounds, 0 ns and −60 ns. The page's in-place view switch
only changes CSS classes, so the alternate view keeps the initial geometry.
The standalone conditional rows also use taken/fallthrough captions.

**Required behavior.** For an RT-origin simulation in standalone HTML,
switch to the corresponding mode's plotted bounds and lane geometry without
reloading or recomputing the simulation. Reuse the existing SVG rendering
logic for both modes and keep a single embedded timeline IR. Keep the active
view's filter, cursor behavior, and event selection working after switching.
The event inspector shows start and end on the active simulation time axis.
Use Execute and Skip for `set_cond` comparisons, leaving ordinary branch
semantics intact. Preserve the absence of a queue lane and the small timed
trigger marker. Keep the VS Code webview and standalone analytical render
behavior outside the RT-origin view change.

**Acceptance.** Open the trigger example as Normal, switch to Debug, then
back. The active SVG lower bounds are 0, −60 and 0 ns, with the RT marker
at 30 ns on the common aligned clock. Start from Debug and verify the same
result in reverse. Execute and Skip retain separate rows, and selecting an
event still opens its inspector. Check that the Q1 lane's negative blocks
are visible only in Debug and no duplicate live DOM IDs are introduced.

## AG. Estimated self-cast arrival order

**Reproduction.** On one sequencer, configure `fb_acq_iq_id 5,4`, start
`acquire 0,0,4` with an 80 ns integration, then send `fb_com_data 6,7,4`.
After a long wait, pull three words. The acquisition starts at 4 ns and its
IQ words are estimated to arrive at 248 ns. Common data arrives at 68 ns.
The analytical flows currently connect the first two pulls to the IQ words
because the acquire instruction starts before the common-data instruction.

**Required behavior.** Use the existing per-stream arrival estimates to order
known eligible self-cast words for FIFO pull, selective pop, and discard of
preceding words. Keep the I-before-Q guarantee for one IQ transmission.
Sequential integrations of the same readout stream can retain their proven
relative order after an unknown trigger offset. Their absolute arrivals
remain unknown.
Preserve source association and early-receive warnings for uniquely known
future arrivals. Maintain the existing non-self inferred routing model.
An unknown estimate may be retained as a sole logical source, but when
unknown timing or an unresolved tie can change FIFO identity or discard
outcomes, mark the affected ordering incomplete and avoid a definite match
or discard. Reuse the same matching pass for flow and balance accounting.

**Acceptance.** The reproduction matches ID 6, then ID 5 I and Q with a
balanced trace. A selective pop of ID 5 discards the earlier ID 6 word.
A selective pop of ID 6 leaves the IQ words for later pulls. Test two
senders using the same self-cast IDs, early receives, and an unresolved
competing acquisition arrival without a misleading ordered flow. Preserve
existing singleton unknown-emission diagnostics and non-self latency tests.

## AH. Conditional and analytical LINQ mock limitations

**Reproduction.** `fb_acq_tb_mock 1,1,1,4` followed by `wait 8` appears
in analysis as a known 4 ns RT step with no limitation. The corresponding
`fb_cmd 0x5d,0x111,4` produces a diagnostic but still places the wait at a
definite 4 ns. In simulation, a false `set_cond` can skip the raw form, while
the equivalent named instruction blocks at Q1 issue.

**Required behavior.** Validate and queue the named, packed-register and raw
forms using the same Q1 contract. Let a known conditional skip consume its
else duration before any unsupported RT effect is attempted. At an executed
mock, keep the documented source and Q1 timing, explain the missing device
vector, and leave the affected RT schedule or execution incomplete. Analysis
must not use the ambiguous fourth operand to produce a definite RT suffix.
Play blocks at the mock when it would execute. Do not synthesize emitted
words, count or RT behavior for the unresolved command.

**Acceptance.** A false condition skips either mnemonic and reaches the
same following RT coordinate with no mock output. The true path reports the
same limitation with each source mnemonic. In static analysis the following
RT start is unknown while earlier and Q1 times stay available. Cover a raw
form, a packed register form, an unconditioned execution and an invalid
signature. Verify the explicit device fixture requirement remains in this spec.

## AI. Unresolved trigger time in compact analysis

**Reproduction.** In compact analysis, `wait_trigger 1,4`, `wait 100`, `stop`
produces an exact wait at 4 ns and stop at 104 ns, alongside a diagnostic
that no trigger arrival is known. Those positions omit the time spent waiting
for the trigger. The 4 ns operand applies after an actual arrival.
[Qblox wait_trigger reference](https://docs.qblox.com/en/v2026.10.0/products/qblox_instruments/q1/index.html#wait-trigger-instt)

**Required behavior.** Keep the compact analysis layout available as a
nominal preview, but distinguish its coordinates from a known RT timestamp.
Mark an executed wait as waiting for an unresolved arrival and following RT
positions on that path as nominal. Surface that distinction on the wait and
dependent blocks in HTML and VS Code tooltips and inspector timing. Keep the
known operand and relative durations. A wait proven skipped does not create
this dependency. Q1 issue timing and independent sequencers are unaffected.
Do not treat nominal acquisition or feedback times as observed absolute
arrivals. Logical FIFO links and independently provable relative order stay
available. Play and simulation still use their actual scenario timing.

**Acceptance.** The reproduction has a known arm time, an unresolved
completion, and visibly nominal coordinates for the later wait and stop.
Cover an earlier known RT prefix, repeated waits, a skipped wait, a branch
alternative, and an independent peer. Check both viewers. A Play or
simulation arrival resolves the wait and its actual completion time.

## AJ. QRM TTL edge trigger routing

**Reproduction.** Open an `acquire_ttl` gate from 100 to 140 ns and supply
a rising edge at 110 ns. A second sequencer waiting for address 3 cannot be
released through the existing integrated-readout trigger route. The TTL edge
is counted but no trigger is emitted. Qblox documents an optional trigger
action per counted threshold crossing.
[Qblox acquire_ttl reference](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#acquire_ttl-instt)

**Required behavior.** Extend `simulation.trigger_routes` with explicit
`source_kind: ttl_edge` for QRM TTL gates. The route enables a modeled trigger
request for each accepted rising edge inside the gate and uses its configured
address and existing fixed or network transport. Disallow `thresholded` and
`source_module` with this kind. Keep the existing integrated and QTM route
behavior separate. Attach edge and acquisition-packet provenance. Use exact
scenario ticks and existing same-tick ordering. Aggregate count inputs do not
identify an individual edge time or transmission. See BX for count bounds.

**Acceptance.** With a 5 ns fixed route, the 110 ns edge arrives at 115 ns and
releases the receiver before the gate closes. Check a falling edge, a rising
edge at the close boundary, a skipped gate, and existing bin results. Include
one fractional tick case, one trigger-network route, and the absence of TTL
emission from default integrated or QTM routes.

## AK. Import the public TimelineIR API first

**Reproduction.** In a fresh process, `from q1timeline.ir import
timeline_ir_to_json` raises a circular import. `q1timeline.ir.serialize`
loads the analysis package, whose eagerly imported API reads the still
initializing serializer. Test collection commonly imports analysis first
and masks this failure.

**Required behavior.** Break the initialization cycle with a local import
boundary. Preserve the public exports from `q1timeline.analysis` and
`q1timeline.ir`, and preserve CLI, Watch, simulation and playback call sites.

**Acceptance.** Fresh Python processes can import both packages in either
order, use the analyzer and serialize a timeline. Test the direct serializer
module import too. The focused tests must run independently of pytest's
collection order.

## AL. Disabled LINQ common-data ID in analysis

**Reproduction.** Analyze `fb_com_data 0,17,4`, `fb_com_data 1,42,4`,
`wait 100`, `fb_pull_data R0,R1`, `stop`. The pull is linked to ID 0 and
ID 1 is reported as unconsumed. Event-driven simulation emits no ID-0 word.
The equivalent raw command `fb_cmd 0x100,17,4` has the same discrepancy.

**Required behavior.** A resolved zero common-data ID contributes no LINQ
payload to compact analysis. Keep its RT packet, duration and source. Use
the same handling for the raw alias and a resolved parameter. Preserve
symbolic IDs and the existing receive operand validation rules.

**Acceptance.** The reproduction links to the ID-1 word, estimated to
arrive at 64 ns. Balance contains ID 1 only and reports it consumed. Verify
the raw form, a parameter-resolved zero, and the unchanged packet spacing.

## AM. Rejected LINQ arrivals and overflow provenance

**Reproduction.** With capacity one, run `wait 100`, `stop` and deliver
external LINQ words at 22 and 23 ns during RT. The second word overflows
the FIFO. The diagnostic attributes the overflow to `stop`, and no timed
record identifies the rejected word.

**Required behavior.** Record a rejected arrival at the actual overflow
time, separate from an accepted FIFO receipt. Include the receiver, ID,
capacity, and existing input or sender provenance. Use the synthetic arrival
source convention and link the diagnostic to that record. A routed arrival
retains its real sender event. Keep the existing simulated error outcome and
FIFO state. Do not count the rejected word as delivered or consumed.

**Acceptance.** The reproduction records one accepted word at 22 ns and
one rejected word at 23 ns with a source-linked diagnostic. Neither arrival
maps to `stop`. Verify a routed rejected word and its sender link, and that
successful arrivals and independent sequencers retain their behavior.

## AN. Single-word TB write-combine rounds

**Reproduction.** Configure two readout sequencers with a shared TB ID and
bit positions 0 and 2. Start both integrations at 108 ns. The first completes
at 124 ns by its configured 16 ns length. A new acquire interrupts the
second integration at the same 124 ns. The model records both results, but
emits two FIFO words because the combine callback ran before RT dispatch
produced the interrupted result. Separately, an enabled configuration with
an odd `bit_pos` can currently emit a word when no other contribution overlaps.
[Qblox QRM write-combine tutorial](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QRM/423_linq_write_combine.html)

**Required behavior.** Finalize each supported one-word group after all
modeled acquisition completions and RT interruptions at its timestamp can
contribute. The receive still uses that timestamp plus the route latency.
Preserve per-source acquisition and packet links. Validate that an enabled
two-bit TB starts at an even bit position within the configured byte width.
Apply this check consistently to named, packed-register, and raw forms at RT
execution so a skipped configuration has no effect. Keep the assembler's
encoding range distinct from the supported RT configuration. Leave enabled
common-data and multiword combination subject to the device-vector limits.

**Acceptance.** The reproduction yields a single word `0x0f` at 129 ns and
both contributors refer to 124 ns results. Reversing sequencer order keeps
the result. Check zero route delay, a different completion time, equal bit
positions as an overlap, and odd-bit rejection across the three forms.
Check the disabled and skipped forms and run traced and prepared execution.

## AO. Bounded uncertain trigger counts

**Reproduction.** Route a single thresholded acquisition result on address 1
through a network whose SYNQ epoch is unknown. Enable address counting on the
receiver and evaluate `set_cond` after the request's entire possible arrival
window. With `trigger1_count_threshold: 2` and no definite arrivals, the
model blocks because it records only that address 1 is uncertain. In this
scenario the one modeled request can contribute at most one count.

**Required behavior.** For each receiver and address, keep an upper bound
on counts that could be added by the declared uncertain network requests.
Use the definite count and the upper bound to resolve threshold comparison
whenever both produce the same result. Preserve OR/AND/XOR and inversion
behavior. Count a possible request once even if its possible arrival window
spans several latch toggles. Reset clears earlier uncertainty and retains
only requests that could arrive after reset while enabled. Play's missing
external input remains unbounded. Continue to block a wait or conditional
decision when the modeled uncertainty can actually change its outcome.

**Acceptance.** One possible arrival cannot reach threshold 2 from zero, so
the reproduced packet is skipped. Threshold 1 remains unresolved. One known
arrival plus one possible arrival can change threshold 2. Cover repeated
enable/disable, reset inside and after a possible arrival window, inverted
thresholds, and an independent address. Check fixed-route and known-epoch
cases and the existing transport diagnostics.

## AP. RT minimum duration in analysis and Play

**Reproduction.** Run `wait 1`, `wait 8`, `stop`. Simulation rejects the
first RT instruction. Analysis, Concrete and Play currently draw a known
1 ns dwell and place the next wait at 1 ns. The assembler can encode 1–3 ns,
but the RT contract allows a normal minimum of 4 ns and a special zero
duration with its separate dequeue rule.
[Qblox RT core reference](https://docs.qblox.com/en/main/products/architecture/sequencers/sequencer.html#real-time-core)

**Required behavior.** Distinguish an encodable duration from the valid RT
dwell. For known 1–3 ns RT durations, report a source-linked runtime timing
error in Concrete and Play at that instruction. Compact analysis reports the
invalid dwell and leaves affected RT timing incomplete. Cover resolved
register and parameter operands and report the invalid `set_cond` else
duration consistently with existing Play and simulation policy. Preserve
validation for ordinary valid operands and the 4 ns dequeue floor after
zero. The unresolved mock's fourth operand keeps its special limitation.

**Acceptance.** No viewer treats `wait 1` or `upd_param 3` as a known RT
execution interval. The offending source and duration appear in the
diagnostic. Verify 0, 4 and 65535 ns, a short resolved register duration,
an unresolved register operand, and an invalid else duration. A 1–3 ns
operand remains accepted by the assembler signature and encoding checks.

## AQ. Synthetic arrivals in VS Code diagnostics

**Reproduction.** A simulated FIFO overflow now correctly points to the
rejected arrival. The VS Code diagnostic mapper treats its `<simulation>`
source as a project file, publishes an editor diagnostic on that fake path,
and accepts a source-open request for it.

**Required behavior.** Keep event-only diagnostics and related event IDs in
TimelineIR. Skip synthetic source locations in the editor's Problems
collection and in source navigation, including synthetic locations nested
under a real diagnostic's related information. Keep normal Q1ASM source
navigation and timeline diagnostic overlays.

**Acceptance.** Mapping the simulated overflow never asks the editor to
resolve or open `<simulation>`. A real diagnostic in the same snapshot still
maps and opens its source. A real related source remains available when
another related source is synthetic.

## AR. Unavailable direct branch comparison

**Reproduction.** Analyze `cmp R0,5`, `jz @join`, `wait 12`,
`join: jlt R1,7,@end`, `wait 8`, `end: stop` with
`explore_both_with_depth_limit`. Both alternatives contain the unresolved
`jlt`. Each reports that the branch policy is unimplemented, even though the
outer comparison demonstrates that it works. The inner issue events also
omit comparison availability metadata that status branches already provide.

**Required behavior.** Report comparison availability for unresolved
`jlt` and `jge` using the same known-forward-target and depth limits as status
branches. If an explicit or automatic comparison cannot proceed, explain
the applicable limit at the branch source. A collapsed or single-path
choice takes precedence over automatic comparison. Keep resolved branches,
timing-only joins, and existing Jump/Continue comparison rows unchanged.

**Acceptance.** The outer `jz` still compares both paths. Both inner `jlt`
events report comparison unavailable and their diagnostics name the depth
limit. Cover an unknown or backward target, explicit Collapse with an
automatic comparison policy, and an eligible top-level `jge` or `jlt`.

## AS. Reject invalid RT duration before simulation issue

**Reproduction.** Simulate `wait 4`, `wait 1`, `wait 8`, `stop`. The
second wait is rejected, but it increments the executed step count, advances
the Q1 cursor, and emits a Q1 issue whose `rt_packet_id` does not exist.
Concrete and Play stop at the second source line before issuing that wait.
For `set_cond 0,0,0,2`, simulation advances the Q1 cursor without an issue.

**Required behavior.** Validate a known invalid RT dwell or conditional else
duration in simulation before modifying issue state or reserving a packet.
Use the source and operand details from the shared RT duration diagnostic.
Preserve all committed prefix work and keep valid duration, argument
validation, alias decoding, and conditional skip rules. In particular, keep
the mock's unresolved fourth operand and defer RT alias effects until execution
of a selected packet.

**Acceptance.** Known 1–3 ns waits, updates, and conditional else durations
report the same timing error reason and operand value as Concrete and Play.
The rejected instruction has no issue, packet, or executed step. Its Q1
cursor stays at the attempted issue time. Every Q1 issue in the accepted
prefix references an existing packet when it provides an RT packet ID.
Check direct and register durations, a resolved parameter, raw or packed
aliases where supported, traced and prepared runs, and the 0 ns control.

## AT. Changing branch paths in compact loops

**Reproduction.** Run this program in compact analysis and Concrete.
Replacing the direct branch with `cmp R1,5` followed by `jge @tail`
reproduces the same RT timing discrepancy.

```q1asm
move 4,R0
move 8,R1
nop
body: wait 4
sub R1,2,R1
nop
jge R1,5,@tail
wait 8
tail: loop R0,@body
wait 4
stop
```

Compact analysis projects the first iteration's taken path across the loop
and reports 20 ns. Concrete runs the extra wait on iterations 1 through 3
and reports 44 ns.

**Required behavior.** Prove that a branch follows the same path across
hidden iterations before reusing the first iteration's Q1 timing, RT
period, or event layout. Account for operand writes, loop counters and
condition flags, including their reaching definitions. For supported,
fully determined small loops, use bounded evaluation to sum the actual
paths. Leave unproved timing or state unknown once evaluation cannot
establish it. Keep real first-iteration events and only show later previews
when their layout is valid. Preserve proven invariant loops and existing
bounded analysis costs. A forever loop may still report its observed first
period with invariance marked false. A configured fallthrough assumption
can preserve a nominal suffix when the permitted paths reach the loop edge.

**Acceptance.** The reproduced direct and flag branch variants give
44 ns and place the final wait at 40 ns. Check both directions of a path
change, zero-duration packets, an invariant branch, a hidden branch with
an unresolved operand, and a long case outside the evaluation budget.
Verify that requested later previews never claim the first iteration's
branch decision or block layout for a different path. Confirm Concrete
and Play remain unchanged. Keep the cursor-frequency and bounded-retry
tutorial examples available with their observed timing and configured
branch assumptions.

## AU. Arrival markers in instruction layout

**Reproduction.** Build a timeline with an RT wait from 0 to 100 ns, a
trigger arrival at 30 ns, a rejected LINQ arrival at 55 ns, and a stop at
100 ns. Instruction layout gives four slots. In time layout the rejected
arrival is presented as an RT command named `feedback_rejected`.

**Required behavior.** Keep both arrival events as selectable points. A
trigger point retains its address and displayed time. The rejected arrival
retains the linked FIFO diagnostic and exposes its ID and displayed time.
In instruction layout, anchor points to their surrounding command without
increasing the lane's instruction count. At a boundary or outside a command
use the existing instruction-position mapping. Neither marker changes
which RT instruction a Q1 issue selects or contributes latched RT state.

**Acceptance.** The reproduced wait and stop occupy the two instruction
slots. The two markers have zero instruction width and sit inside the wait
in arrival order. In time layout they remain at 30 ns and 55 ns. Check
aligned time labels, a rejected arrival diagnostic, a marker at an RT
boundary, and a marker after the last instruction. Preserve comparison and
ordinary diagnostic overlays.

## AV. Fault edge uses the displayed time basis

**Reproduction.** Simulate `wait 100`, three `nop`s, and `illegal`
with a fixed RT start of 8 ns. The fault marker and the issuing Q1 event
end at 12 ns relative to the RT origin. Their inspectors show 20 ns in
the fault edge field because that field reads the raw simulation clock.

**Required behavior.** Display the fault edge in the same time basis as
the selected event. The fault occurs at the end of the Q1 issue or at
the zero-width fault event. Preserve the raw fault record in TimelineIR
and the unshifted value in a local-time view.

**Acceptance.** The extension and standalone inspectors show 12 ns for
both representations when aligned and 20 ns when local. The selected time
basis controls the display while the underlying fault time remains 20 ns.

## AW. Incomplete simulation comparison alternatives

**Reproduction.** With a 20 ns fixed startup and no trigger, compare both
outcomes of `set_cond 1,1,0,8`, `fb_acq_tb_mock 1,1,1,4`,
`set_cond 0,0,0,4`, `wait 20`, `stop`. The Skip alternative reaches the
following wait. The Execute rerun blocks at the mock, but comparison copies
only events that differ from the baseline. Its RT row and failure reason
are missing.

**Required behavior.** Retain a source-linked terminal point for each
incomplete alternative that has no visible terminal event carrying its
reason. Preserve the simulation edge, comparison ID and path, terminal
status, and explanation. Show it in the corresponding Execute or Skip
row in both viewers, including when the rest of that alternative is
shared. Points take no instruction slot. Keep the recorded run's execution
status, diagnostics, packets, and Hardware state authoritative. An
alternative fault remains local to its comparison.

**Acceptance.** The reproduction shows an Execute marker at the attempted
20 ns edge with the mock limitation and its source. Skip reaches the
normal suffix, and the baseline status remains completed. Cover a skipped
`wait_trigger` versus an unresolved Execute wait, an alternate error, and
a shared or already visible terminal event. A second comparison keeps
distinct IDs and the displayed marker uses the selected time basis.

## AX. Aligned external input inspection

**Reproduction.** In Play with `manual:-8` alignment, run `wait 12`,
`wait_trigger 1,4`, `fb_pull_data R0,R1`, `nop`, `stop`. Schedule the
trigger on address 1 at 16 ns and a LINQ entry at Q1 time 16 ns. The
trigger event is shown at 4–12 ns, but its inspector lists arm, arrival,
and resume at 12, 16, and 20 ns. The LINQ issue displays 0–16 ns while
the inspector lists 8, 16, and 24 ns.

**Required behavior.** Display each absolute scenario timestamp in the
same local or aligned basis as its event, using the receiver's offset.
Preserve durations, missing values, IDs, ordering, source resolution,
and the unaligned schedule in Configure and TimelineIR. Make any
timestamp in a waiting explanation consistent with the displayed fields.

**Acceptance.** With alignment, the trigger timestamps are 4, 8, and
12 ns and the LINQ timestamps are 0, 8, and 16 ns. The local view shows
the original values. Check a zero or negative aligned timestamp,
unresolved arrivals, and two receivers with different offsets. Selecting
the Q1 issue or its receive event uses the same displayed values.

## AY. Latched state in compact loop previews

**Reproduction.** Initialize `R1` to 100 and repeat a body containing
`set_freq R1`, `upd_param 8`, and `add R1,10,R1`, with the register
write latency satisfied. Request three previews. The copied second and
third previews both claim 100 although the corresponding setter reads are
unknown and Concrete applies 110 and 120. Separately, a single
`set_ph_delta` before a loop containing only `upd_param` appears applied
again in every copied preview.

**Required behavior.** Keep first-iteration observations. In copied
iterations, represent unproved pending and applied setter values as
unknown and retain the link to the correct in-loop setter occurrence.
Preserve known invariant fields. An entry setter consumed in the first
iteration contributes no later application unless the loop establishes a
new pending setter. Remove repeated one-shot flags and the stale source
link. Where a trailing setter can carry into a later iteration, prove
its origin or mark the affected field unknown. Keep timing, packet
selection, and register history independent of these preview changes.

**Acceptance.** In the frequency reproduction later previews retain
their 8 and 16 ns RT starts and display an unknown frequency with the
matching setter link. A constant marker in the same loop stays known.
An external phase kick appears only on the first update, while an
in-loop constant phase kick still repeats. Check a loop-carried setter
and that the original events and Concrete trace retain their values.

## AZ. LINQ arrivals through Play stop completion

**Reproduction.** Run `nop`, `stop` with a capacity-one LINQ FIFO.
Schedule words at Q1 times 5 and 6 ns. Simulation rejects the second word
at 6 ns while the 4–8 ns stop is in flight. Play checks the schedule only
before each issue, so it reports a successful stop and never checks
these arrivals.

**Required behavior.** Evaluate the receiver's declared FIFO schedule
through the stop completion edge. Arrivals at that edge precede stop
completion, in their listed order. Retain already issued Q1 work and
report the rejected arrival by its configured ID if capacity is exceeded.
An overflow while the stop is in flight must not publish a completed RT
stop or stop status code. Later scheduled arrivals are beyond Play's Q1
input horizon. Preserve active receipt and unrelated sequencer behavior.

**Acceptance.** The reproduction has two issued Q1 steps, an overflow
at 6 ns and no completed stop. A second arrival at 8 ns also overflows.
With the second arrival at 9 ns or capacity two, the stop completes.
Check that an overflow before stop issue and an ordinary scheduled
receive retain their ordering and diagnostics.

## BA. Point marker geometry in the extension

**Reproduction.** In the extension, switch a timeline with a trigger
inside a wait to Instruction layout. The shared model correctly assigns
zero slot width, but the webview's post-render positioning sets the
marker element to a full cell. In the reproduced viewport its clickable
width is 548 px. The marker's centering transform also displaces the dot.

**Required behavior.** Keep the shared renderer's point width when
assigning precise Instruction positions. Use the modeled fractional
position to center the small hit target. Preserve full slots for normal
commands and the existing widths for Q1 issue and diagnostic markers.

**Acceptance.** A point at instruction position 0.3 remains centered
there with a 10 px target in the actual webview after switching layouts.
The surrounding wait stays a full slot and remains selectable away
from the point.

## BB. Displayed times in the comparison inspector

**Reproduction.** Compare a previous wait with local edges 12 and
112 ns, aligned edges 4 and 104 ns, against a current wait with local
edges 20 and 120 ns, aligned edges 0 and 100 ns. The aligned timeline
places the current wait at 0–100 ns. The before/after inspector instead
reports both snapshots' local edges.

**Required behavior.** Display each snapshot's start and end using its
own coordinates in the selected time basis. Preserve durations, diff
classification, and snapshots. Keep compatible local or symbolic
values when aligned coordinates are unavailable.

**Acceptance.** The reproduction shows 4–104 and 0–100 ns when aligned,
and 12–112 and 20–120 ns when local. Check a removed event, a zero edge,
and a snapshot without aligned timing.

## BC. Time basis of the Play waiting summary

**Reproduction.** With `manual:-8` alignment, play `wait 12` followed
by `wait_trigger 1,4` with a known empty trigger schedule. The selected
wait inspector reports 4 ns, but the execution status and disabled Run
button still explain the wait using 12 ns.

**Required behavior.** Format terminal waiting explanations from the
current snapshot's waiter records in the selected time basis. Apply the
receiver's own offset to each trigger arm timestamp. Refresh both status
and Run tooltips when the basis changes. Preserve LINQ wait explanations,
the count of waiting sequencers, and raw scenario and protocol values.
Use the existing summary when only an older unstructured response is
available. Do not show waiters from a stale snapshot.

**Acceptance.** The example shows 4 ns when aligned and 12 ns when local.
Check two receivers with distinct offsets, a LINQ waiter alongside a
trigger waiter, and the transition from waiting to a completed rerun.

## BD. Unknown thresholded acquisition triggers

**Reproduction.** A readout runs `acquire 0,0,4` at 100 ns. Its scenario
result supplies I/Q and omits the thresholded bit. An address-1 route
would reach a receiver at 112 ns if the result were one. The receiver
enables its latch at 100 ns and uses `set_cond` at 116 ns. Simulation
currently skips the packet, treating the absent bit as a known zero.
Supplying a one makes the same packet execute.

**Required behavior.** Keep a valid I/Q acquisition result and report
that its routed threshold bit is unknown. Feed its possible arrivals
into existing bounded trigger uncertainty at the modeled arrival
edge. Account for the mutually exclusive zero and one cases when
bounding counts. Keep waits and conditions that cannot depend on
the result determinate. On a shared trigger network, an optional
request cannot establish a definite collision. If it could change an
otherwise scheduled delivery, mark that delivery uncertain as well.
Keep unrelated sequencers, known threshold results and existing TB and
IQ feedback contracts.

**Acceptance.** The threshold-one condition in the reproduction is
unresolved. Threshold two still skips when at most one arrival is
possible. Cover a wait armed before the possible arrival, a wait armed
after its window, fixed and known-epoch routes, and a shared network
delivery with a potential collision. The input's I/Q result stays
available, and explicit zero and one inputs keep their current behavior.

## BE. Queue replay after a failed Play stop

**Reproduction.** Play `wait 100`, `stop` with a capacity-one LINQ FIFO
and arrivals at 5 and 6 ns. Play correctly reports an overflow during
the stop instruction, but the queue result reports
`not_detected_in_recorded_packets` with no incomplete reason. Its
SYNQ prefetch replay can also treat that peer as a completed producer.

**Required behavior.** Distinguish a terminated runner from a successfully
completed packet producer. Keep the recorded prefix for bounded replay,
report an incomplete trace after a failed terminal issue, and prevent it
from authorizing maximal SYNQ prefetch. Preserve supported underflow
diagnostics and ordinary successful stop behavior.

**Acceptance.** The reproduction reports incomplete queue timing and a
reason alongside the existing FIFO overflow. Check a synchronized peer
and a successful stop under the same inputs with sufficient capacity.

## BF. Fractional digital RT with a physical model

**Reproduction.** Forward a QTM rising edge at `104 + 1/128 ns`
with 8 ns fixed latency while its sequencer waits for address 3.
The wait resumes at `112 + 1/128 ns`, and the digital sequence
completes. Enable the physical signal model for a separate analog
sequencer. The same digital wait now fails an integer timestamp check
even though it changes no sampled signal.

**Required behavior.** Keep timing-only and digital-only RT effects
out of numerical signal event validation when they do not change
sampled outputs. Keep fractional trigger, gate and stop edges intact.
Preserve sampled waveform and latched signal updates, numerical
acquisitions, and the explicit grid limits for effects that require
sampling. The change must not invent fractional waveform samples.

**Acceptance.** The same QTM program completes with and without the
analog model and preserves the fractional receive, wait and stop
edges. The separate analog output still renders correctly. Check a
timing-only update at a fractional edge, a sampled update still
subject to its grid check, and an already sampled history guard.

## BG. Unverified wide LINQ receive selectors

**Reproduction.** Queue one ID-1 entry at time zero, then run
`fb_pop_data 257,R0`. Simulation discards the entry and stalls. Scheduled
Play likewise reports it discarded and asks for an ID-257 entry, even
though its scenario format accepts only 8-bit IDs.

**Required behavior.** Keep encoding validation distinct from modeled
FIFO selection. The instruction reference lists a 16-bit immediate, while
the receiving guide describes an 8-bit selector. For a resolved selector
above 255 that remains encodable, report a source-linked unresolved
device behavior before changing the FIFO in Simulation or scheduled
Play. Preserve valid selective pop, `fb_pull_data`, and explicitly
assumed receipts. Keep the hardware behavior in the device-vector list.
[Qblox instruction reference](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#fb-pop-data)
and [LINQ receiving guide](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#data-receiving).

**Acceptance.** Selector 257 reports the limitation with the original
source and neither consumes nor discards the queued ID-1 word. Selector 1
receives it. Cover an immediate and a resolved parameter, traced and
prepared simulation, and the scheduled Play path.

## BH. Zero-latency triggers from RT acquisition interruptions

**Reproduction.** Start a 64 ns integration at 100 ns with `acquire
0,0,20`, then interrupt it at 120 ns with `acquire 0,1,4`. The first
result is threshold one. Route it at zero delay to a receiver whose
`set_latch_en 1,20` ends at 120 ns and whose next RT packet is
conditional on address 1. The packet executes when the sender is listed
first and skips when the receiver is listed first.

**Required behavior.** A zero-delay fixed route generated by an RT
commit arrives after the other RT decisions on that tick. Its timestamp
remains the completion tick. A natural integration completion already
scheduled in the input phase can affect the same-tick condition. Apply
the distinction to definite and possible threshold results. Preserve
positive delay, shared network transport, and wait-trigger behavior.
This ordering defines the synthetic zero-delay scenario.

**Acceptance.** Both listing orders skip the first conditional packet
in the interrupted case and see the 120 ns arrival for subsequent work.
A natural 120 ns completion executes in both orders. A wait armed at
the interruption edge can still receive at 120 ns. Cover an unknown
threshold bit and traced and prepared execution.

## BI. Unavailable QTM sampled trigger input

**Reproduction.** At 100 ns a QTM runs `acquire_digital 0,0,4` with
`sampled-high` forwarding and a fixed 8 ns route to address 1. Give it
no digital input. The acquisition is unavailable, but the receiver
uses a known count of zero and skips its threshold-one conditional at
120 ns. An explicit high input executes the packet.

**Required behavior.** An enabled sampled forwarding mode with unknown
level contributes at most one possible trigger for the modeled sample.
Use bounded counter and wait semantics at the routed edge. Preserve the
unavailable acquisition result, known levels, disabled or skipped
samples, and unrelated channels. Use optional requests on a shared
network without claiming a definite transmission or collision.

**Acceptance.** The example blocks at threshold one and skips at
threshold two. An explicitly known low still skips at one. Verify
sampled-low, a wait at the possible edge, a zero-delay route and a
network route. An arrival known independently at the same edge can
still release the wait.

## BJ. Unavailable classifications in a QTM truth round

**Reproduction.** Two QTM windows close together at 120 ns. One count
class is high and the other is unavailable. A rule for high and mid
would send address 3, arriving at 126 ns. The truth round reports
`unavailable`, but the receiver treats its threshold-one condition at
140 ns as a known skip.

**Required behavior.** A complete round with a compatible rule and an
unknown count class has a possible trigger at the rule's modeled route
edge. Exclude rules contradicted by a known class and rules missing a
same-tick close. Deduplicate possible rules for the same address. Rule
alternatives from one truth round are mutually exclusive for network
collision checks. Preserve the round status, source channels, known
invalid classification, and definite matches. Counter evaluation may
use conservative per-address bounds across several possible addresses.

**Acceptance.** The example remains unresolved at threshold one and
skips at two. An incompatible known class or a missing peer close leaves
zero arrivals. Check two compatible rules sharing one address, and
exclusive addresses routed onto the same network slot.

## BK. Arrival markers for trigger waits in Play

**Reproduction.** Play `wait 12`, `wait_trigger 1,4`, `wait 4`,
`stop` with a scenario arrival at 16 ns and alignment `manual:-8`.
The wait spans 4–12 ns on the displayed axis. Its scenario input can
be inspected, but there is no arrival marker at 8 ns.

**Required behavior.** Each scheduled occurrence that actually releases
a Play wait produces one selectable trigger point on that receiver.
Use the occurrence's arrival time, address, shared scenario identity,
and assumption confidence. Link the consuming wait without making the
marker a source instruction or a separate instruction slot. Preserve
scenario scheduling, replay and wait duration. Missing or unused
occurrences do not create consumed markers.

**Acceptance.** The reproduction has a point at 16 ns locally and 8 ns
when aligned. A zero-duration wait leaves the point at arrival and the
next RT packet 4 ns later. Two receivers consuming the same occurrence
get distinct marker IDs linked to one scenario input. Repeated snapshots
and resumed runs neither duplicate markers nor lose provenance.

## BL. Missing analog acquisition evidence on a trigger route

**Reproduction.** Simulate `acquire 0,0,4` with a declared threshold
trigger route and no acquisition input or physical input model. The
sender blocks at completion. The receiver still treats the routed
count as known zero and skips a threshold-one condition.

**Required behavior.** Preserve the sender's missing-input status.
When the modeled integration reaches its completion or interruption
edge, propagate the same bounded threshold uncertainty as for a result
without a thresholded bit. Keep a source-linked explanation and the
existing transport order. An unavailable measurement supplies no
I/Q value or bin result. Leave unrelated execution determinate.

**Acceptance.** Threshold one is unresolved, threshold two skips
when only one arrival is possible, and the sender remains blocked.
Verify fixed and shared-network routes, an interrupted zero-delay
completion, and a run without routes.

## BM. Digital acquisition results in Hardware

**Reproduction.** Simulate `acquire_digital 0,0,4` at 100 ns without
a digital input source. Hardware indexes the `unavailable` event as a
bin result ready at 100 ns. A valid sampled level or timetag count
is also rendered using the analog I/Q labels.

**Required behavior.** Retain the latest acquisition outcome and its
source for inspection. Reserve readiness for available data. Show the
status and reason of unavailable, incomplete, discarded or invalid
outcomes. Label digital results by their actual level, edge count and
timetag delta where present. Preserve analog labels and bin navigation.

**Acceptance.** The missing sample remains inspectable at 100 ns with
its explanation and no ready time. Check a level zero, count zero,
signed timetag delta, and unavailable result after an earlier successful
write to the same bin. A normal analog I/Q result still shows its
actual ready time.

## BN. Unobserved digital edge trigger windows

**Reproduction.** Open a QTM rising-edge or QRM TTL gate from 100 to
140 ns with a 5 ns route to address 3 and omit its input trace. The
sender records an unavailable acquisition. The receiver's threshold-one
condition at 120 ns still uses a known zero count, just as with an
explicit flat trace. A wait armed during the gate can also report no
scheduled trigger instead of an unresolved arrival.

**Required behavior.** Treat a gated edge source without input evidence
as a potential interval, from the effective open boundary up to the
exclusive close. Transport it without inventing edges, counts or definite
arrivals. For fixed routes shift both boundaries by the declared latency.
Respect before-RT and after-RT same-tick ordering, let a definite arrival
at the earliest possible tick resolve a wait, and avoid counting possible
edges while disabled or before a latch reset. A known count can still
decide a condition. For shared networks, use a conservative slot interval
and preserve already observed deliveries. A potential collision can
affect pending deliveries but cannot establish a definite conflict.
Keep execution bounded without stepping through every possible tick.

**Acceptance.** Both missing-input gates make the example unresolved.
An explicit flat trace still skips. Check a wait before the window,
within it, and at or after its exclusive end, a zero-delay route,
latch toggles and reset, and a possible shared-network collision.
Disabled forwarding, skipped gates and unrelated routes remain determinate.

## BO. Supplied weighted acquisition without numerical weights

**Reproduction.** Run `acquire_weighed 0,0,0,1,20` with a supplied I/Q
result and no weight memory. Simulation uses its 20 ns duration
fallback. Enabling the physical model for another sequencer makes the
same acquisition fail for missing numerical weights.

**Required behavior.** Derive timing from available weight memory.
When it is unavailable and a scenario supplies the measurement, retain
the duration fallback regardless of the physical model configuration.
Keep numerical integration's weight and grid validation and the
zero-duration fallback guard. Preserve the supplied result's precedence
and metadata.

**Acceptance.** The result is available at 120 ns with the same I/Q
values and duration source with and without an unrelated physical
model. Check physical input on the same sequencer with a supplied
override, a numerical acquisition without required weights, and valid
weight memory determining the completion edge.

## BP. Baseline RT underflow diagnostic on the timeline

**Reproduction.** Simulate `wait 4`, 50 `nop` instructions and `stop`
with the RT start fixed at 100 ns. The RT underflow marker is at 4 ns on
the aligned axis. The diagnostic reports 104 ns and has no event link.
Its source location can also attach a second error badge to the Q1
instruction active when RT runs out of work.

**Required behavior.** Link the baseline simulation diagnostic to the
underflow marker when a trace is recorded. In timeline diagnostics and
inspection, explain that the RT queue ran out of commands. Use the
linked marker to show the time in the selected basis and avoid the
duplicate Q1 badge. Retain the original execution status, reason, source
and unshifted edge in the simulation result. Trace-disabled runs still
report underflow without a dangling event ID.

**Acceptance.** The reproduction yields one underflow marker and a
diagnostic that opens it. Aligned inspection shows 4 ns. The extension's
local inspection shows 104 ns. No timeline diagnostic text presents
104 ns alongside the aligned marker. Cover Normal and Debug, and
preserve analysis and Play underflow behavior.

## BQ. Time basis of standalone event tooltips

**Reproduction.** Render a simulated 40 ns wait that starts at local
100 ns and aligned 0 ns. The standalone SVG puts the block at 0 ns.
Its native tooltip leads with 100 to 140 and then repeats the interval
under aligned and local headings.

**Required behavior.** Show one time range in a generic SVG event
tooltip, using the time basis of that SVG. Preserve symbolic or unresolved
timing text when a concrete display edge is unavailable. Keep duration,
source, instruction and status detail. Keep unshifted clock values in
TimelineIR and display them in a local-time view. Leave the compact
trigger-arrival and comparison-terminal tooltips intact.

**Acceptance.** The reproduction tooltip shows 0 to 40. A fixture
with only local geometry shows 100 to 140. The existing Normal/Debug
geometry switch keeps the active tooltip and event inspector in
agreement. Cover symbolic and nominal-trigger timing and a baseline
underflow point. Avoid printing raw metadata clocks in the tooltip.

## BR. Definite trigger arriving late on an uncertain wait tick

**Reproduction.** Start RT at 100 ns. An analog acquisition completes at
104 ns with an unknown threshold bit and a zero-delay route to receiver
address 3. A known QTM sample executes at 104 ns and forwards to the
same address with zero delay. A receiver armed at 100 or 104 ns stops
as unresolved before processing the definite post-RT arrival.

**Required behavior.** Keep possible arrivals at their existing early or
late phases for latch counts and `set_cond`. Resolve a waiting
`wait_trigger` against a possible arrival only after definite arrivals
on the same tick have been considered. At that point, an observed
matching arrival wins. An unresolved matching arrival still stops the
wait at that tick when there is no definite one. A real arrival at a
later tick cannot retroactively resolve the earlier wait. Keep
source-kind diagnostics and do not emit a receive event for uncertainty.

**Acceptance.** The reproduction completes with one receive at 104 ns,
for both arm times and either sequencer iteration order. A definite
arrival to a different address or on a later tick leaves the original
wait unresolved. Cover early optional fixed and network arrivals, the
case with no definite arrival, and condition decisions at the same tick.

## BS. Definite output of an exhaustive QTM truth round

**Reproduction.** Two QTM channels close at 120 ns. Channel 4 is high,
and channel 6 closes without a classification. Four disjoint rules
cover low, mid, high and invalid on channel 6 and all emit address 3
when channel 4 is high. A receiver with a 6 ns route blocks even
though every classification of channel 6 produces the same request.

**Required behavior.** Evaluate the compatible rules across the finite
classification domain of closed channels whose result is unavailable.
An address present for every assignment produces one definite request.
Preserve the unknown classification and record the aggregate evidence
without claiming a specific rule matched. Rules with missing or
different-tick channels retain their incomplete status. Partial
coverage remains optional and incompatible rules contribute nothing.
Use the ordinary transport and count handling for a proven request.

**Acceptance.** The reproduction emits one request and reaches address
3 at 126 ns with provenance for both contributing channels. Removing
one rule restores uncertainty. Cover an explicit invalid result, more
than one unknown closed channel, and one shared-network launch. Check
traced and prepared execution and leave separate possible addresses
mutually exclusive.

## BT. Completed acquisition before an unresolved suffix

**Reproduction.** Analyze `acquire 0,0,4`, `wait 100`, `wait R0`,
`jmp R1`, `stop` with no configured integration length. The acquisition
should end at 4 ns, before the concrete RT prefix reaches 104 ns.
The symbolic branch barrier in the suffix erases the completed window.

**Required behavior.** Keep an otherwise provable acquisition or scope
window when the baseline RT trace establishes that an unresolved
barrier occurs at or after the end. Preserve uncertainty when an
unresolved region or later acquisition could intersect the window.
Do not use comparison alternatives, collapsed loop previews, or
ambiguous time origins as proof of the baseline order. Keep
interruption, scope retrigger, and unfinished Play semantics.

**Acceptance.** The reproduction reports an integration from 0 to 4 ns
from the instruction duration. A configured 200 ns integration remains
unresolved. Check a scope capture whose fixed end is before an
unresolved suffix, and a later acquisition with unknown start that
cannot interrupt an already completed window. An unresolved region
inside the integration still prevents a concrete analytical LINQ send
time.

## BU. Mutually exclusive QTM truth outputs in trigger conditions

**Reproduction.** Close QTM channels 4 and 6 at 120 ns. Channel 4 is
high and channel 6 has no classification. Four disjoint rules emit
address 1 for low or mid and address 3 for high or invalid on channel 6.
Both addresses reach a latched receiver at 126 ns. A condition using
mask 5 is currently unresolved for OR, AND and XOR.

**Required behavior.** Carry the possible output addresses of a truth
round together, including a no-output case when the compatible rules
do not cover all assignments. Count arrivals only when the relevant
route could have arrived during the current latch epoch. Refine a
condition using the mutually exclusive outcomes of a round. Preserve
network delivery uncertainty within each outcome and stay conservative
for independent rounds or other unobserved input. Keep evaluation
bounded by the address domain, including across repeated rounds.

**Acceptance.** After both routes could arrive, OR and XOR execute and
AND skips in the reproduction. No individual trigger receive is
fabricated. An omitted rule, missing route, later arrival, or a network
conflict can leave OR unresolved. A single-address wait still depends
on the chosen output. Cover latch reset, unrelated input and traced
and prepared execution.

## BV. Weighted acquisition without a modeled input

**Reproduction.** Run `acquire_weighed 0,0,0,1,20` at 100 ns without
weight memory or an acquisition result, with a 5 ns trigger route to a
waiting peer. Simulation records unavailable evidence at 120 ns and
the receiver's wait depends on a possible 125 ns arrival. Enabling the
physical model for an unrelated peer aborts the acquisition at RT
execution for missing numerical weights and erases the possible arrival.

**Required behavior.** Require numerical weights for a weighted
acquisition only when its own numerical input is configured and a
scenario result does not override it. Without a modeled input, use the
existing weight-memory timing or instruction-duration fallback and
handle missing results at the established completion edge. Preserve
the zero-duration guard and the source and receiver diagnostics.

**Acceptance.** The reproduction preserves the same unavailable
acquisition and possible receiver arrival with no physical model, with
an unrelated physical model, and with an output-only model on the
sender. An actual numerical input without weights still errors, and
valid weight memory still determines the acquisition edge. Cover
traced and prepared execution.

## BW. Known request before the trigger-network epoch resolves

**Reproduction.** A known acquisition trigger requests a network slot
at 104 ns while a SYNQ-based grid epoch is unresolved. After SYNQ
releases at 128 ns, another known request launches at 156 ns on that
network. The later request is delivered as definite even though the
earlier request could have occupied a conflicting slot. Replacing the
earlier threshold bit with an unknown bit produces a possible conflict.

**Required behavior.** Include both definite and optional requests with
unresolved launch phase in the existing bounded interference check.
An unresolved earlier launch can make a nearby pending delivery
uncertain. Preserve the earlier request's unknown epoch and avoid
asserting a slot or a definite conflict. Requests beyond the spacing
bound and requests on other networks keep their own outcomes.

**Acceptance.** The reproduction marks the later delivery uncertain for
both supplied threshold cases that generate the earlier request and
unknown threshold input. The earlier request keeps its unknown-epoch
diagnostic and no launch marker. Check a later launch beyond the
possible conflict range and preserve the definite-conflict behavior
between two known slots.

## BX. Supplied TTL counts constrain routed edge uncertainty

**Reproduction.** Open `acquire_ttl 0,1,1,40` at 100 ns, close at
140 ns, and supply only its aggregate count. Route rising edges to
address 3 with a 5 ns delay. With a latch enabled at 100 ns, a
condition at 120 ns is unresolved for count zero and threshold one,
or count one and threshold two. Both should skip.

**Required behavior.** When a scenario supplies the count for the
opened TTL gate and no edge trace is present, bound potential routed
arrivals by that count. A zero count admits no edge or interference.
Count each possible path once per latch epoch. A normally closed
gate can establish a lower count on a fixed route after its complete
arrival window, when the latch covered that whole window without a
reset. A partial window, early termination, or uncertain network
delivery supplies no such lower bound. Keep the count row's existing
consumption order and give explicit traces precedence for routing.
Never synthesize an individual trigger time, token, or receive marker.

**Acceptance.** The two conditions in the reproduction skip. A
threshold-one condition during a count-one gate stays unresolved.
After the whole fixed arrival window, it executes when the latch
covered the window and remains unresolved when it covered only
part. A positive count still cannot locate a `wait_trigger` release.
Check zero-count network interference, latch reset, multiple gates
and transport paths, an interrupted gate, and traced and prepared
execution.

## BY. Integration windows on simulated analog acquisitions

**Reproduction.** Simulate `acquire 0,0,20` at 100 ns with a configured
100 ns integration and a supplied I/Q result. The RT packet ends at
120 ns and the result arrives at 200 ns. The acquisition block has no
integration window, so its inspector and Hardware cursor can only use
the packet span. An active integration interrupted by `illegal` also
disappears from the acquisition metadata.

**Required behavior.** Attach the integration window to the simulated
analog acquisition block, using the established TimelineIR window shape.
Keep the planned duration, actual modeled bounds and timing source
separate from the RT packet duration and from scope. Close the window
at natural completion or a retrigger. If execution terminates first,
retain the observed bounds and mark the window incomplete at that
sequencer's terminal edge. Do not publish a result or feedback for
that incomplete integration. Preserve known unavailable results that
reach their ordinary completion edge.

**Acceptance.** The reproduction exposes a 100 to 200 ns integration
on a 100 to 120 ns packet. Cover integration ending before the packet,
weighted timing, retrigger, missing-result completion, and a fault with
another sequencer continuing beyond it. Exercise prepared execution
and preserve the RT packet geometry and result/feedback order.

## BZ. Capture cutoff after a sequencer failure

**Reproduction.** With RT start at 4 ns, open a TTL gate using a 4 ns
packet and let Q1 execute 20 `nop` instructions before `stop`. RT
underflows at 8 ns. An independent peer runs to 204 ns. The
incomplete TTL result incorrectly ends at 204 ns. A QTM timetag
gate with an enabled scope has the same issue. A QRM scope opened
at 4 ns and stopped by `illegal` at 8 ns retains its projected
16,388 ns end as the effective cutoff.

**Required behavior.** When a sequencer terminates with a digital
gate or scope still open, use its recorded terminal edge for the
incomplete result and capture. Preserve the planned QRM scope
duration, unavailable sample status, and observations already
recorded at the edge. Keep normal RT stop behavior, including
valid delayed closes and continuous scope horizons. Preserve the
original failure and independent peers.

**Acceptance.** The TTL result ends at 8 ns. With a QTM gate opening
at 12 ns and underflow at 16 ns, both its incomplete result and
scope end at 16 ns while the peer continues to 212 ns. The QRM
scope ends at 8 ns with a planned duration of 16,384 ns and
unavailable samples. Cover a fractional effective open and traced
and prepared execution. A complete result or scope is unchanged.

## CA. Acquisition timing source in both inspectors

**Reproduction.** Inspect a simulated analog acquisition whose duration
comes from `simulation.physical.sequencers[id].integration_length_ns`
or weight memory. TimelineIR records `configured` or `weight_memory`.
The extension shows an integration length but reports its source as
unknown. The standalone inspector omits the source altogether.

**Required behavior.** Show the integration source from the existing
window metadata in both event inspectors. Distinguish physical-model
configuration, sequencer hardware parameters, weight memory, and the
instruction-duration fallback. Leave an absent or unsupported source
unknown. The display stays in English and keeps RT packet timing
separate from the integration window.

**Acceptance.** A real simulated acquisition from each timing source
shows the correct English provenance in both inspectors. A weighted
analytical window identifies weight memory as well. Missing metadata
remains unknown. Selecting the acquisition or switching the active
time basis keeps the existing timing and selection behavior.

## CB. Source evidence for unresolved trigger decisions

**Reproduction.** Supply I/Q without a threshold bit and route the
possible trigger to a waiting receiver. The receiver's unresolved
diagnostic points to its own wait but has no event link to the source
acquisition. A possible network delivery has the same gap even when
the sender records a request or conflict diagnostic.

**Required behavior.** Carry source-event evidence for possible point
arrivals to a dependent wait or condition. A wait links to evidence
for its address and eligible time. A condition links to evidence in
the current latch epoch for selected addresses. Preserve the receiver
instruction as the diagnostic source. Include request or acquisition
evidence that exists in the IR, with no dangling links in prepared runs.
Preserve the bounded simulation and existing count and wait decisions.

**Acceptance.** The reproduction links to the acquisition result.
Cover a QTM truth round and network conflict or unknown epoch, a
reset that removes earlier evidence, and a different address that
does not affect the diagnostic. Check traced and prepared execution.
No possible arrival becomes a trigger receive marker.

## CC. Acquisition completion on a faulted analytical path

**Reproduction.** Analyze `acquire_weighed 0,0,0,1,4` followed by
`illegal` with two 100-sample weights. The nominal acquisition starts
at 0 ns, the Q1 fault is at 8 ns, and the acquisition is reported
completed at 100 ns. Concrete execution makes the same claim.

**Required behavior.** A faulted analytical path cannot establish the
completion or interruption edge of its nominal readout integration or
scope. Keep the planned duration and its source. Mark the effective
window unresolved and avoid estimating LINQ availability from it.
Use the event-driven window for simulation, where Q1 and RT share a
clock. A fault confined to an unexecuted comparison or preview must
not invalidate the recorded path. Preserve ordinary stop and Play
pause behavior.

**Acceptance.** The reproduction preserves the 100 ns weight-derived
duration but has no effective end in analysis, Concrete or Play.
Check a QRM scope and analytical LINQ timing, a normal stop, an
alternate-only fault, and event-driven simulation's observed cutoff.

## CD. Instruction zoom regression fixture after command folding

**Reproduction.** Run the VS Code unit suite. The instruction zoom
case for rerenders and layout round trips throws a reference error
for `timelineEventsById`. The fixture extracts the production
rendering function without supplying the command-folding state
that the real webview initializes.

**Required behavior.** Supply the fixture's event index and folding
dependencies so the test executes the current rendering path. Keep
the real command folding and existing zoom, selection and scroll
assertions. Limit the change to the test harness unless the same
error can be reproduced in the real viewer.

**Acceptance.** The instruction zoom suite passes, including native
scroll through rerenders and instruction/time layout round trips.
The full extension unit suite reaches the current assertions.

## CE. Valid sender diagnostic links in prepared simulation

**Reproduction.** Run a prepared acquisition with a known threshold
trigger and no resolved SYNQ grid epoch. The sender warning references
the request and acquisition-result events that the prepared trace omits.
Two known requests colliding in a resolved slot produce the same dangling
links on both senders.

**Required behavior.** Link sender diagnostics to retained evidence.
When a prepared trace omits a request, result or digital transition, use
its retained acquisition or Q1 issue anchor if available. Keep the full
diagnostic evidence in traced runs and preserve request metadata, timing,
statuses and severity. Keep the work bounded with repeated diagnostics.

**Acceptance.** Unknown epoch, definite collision, possible collision
and unobserved gate interference link only to existing events in
prepared IR. Traced diagnostics retain request and origin links. Check
same- and cross-sequencer evidence and preserve receiver links.

## CF. Diagnostic evidence for a simulated comparison terminal

**Reproduction.** Compare Execute and Skip for a conditional
acquisition whose I/Q result lacks a threshold bit. Its possible
trigger interrupts a peer's `wait_trigger` on Execute. The peer
receives a later known trigger on Skip and on the recorded run.
The Execute terminal carries the receiver's reason but loses the
acquisition event that explains it.

**Required behavior.** Preserve the terminal diagnostic's event
references in the comparison marker's metadata. Remap evidence
from the rerun to the matching comparison path and keep references
to genuinely shared prefix events. Include only retained evidence.
Keep the recorded diagnostic list, execution state, and Hardware
results authoritative.

**Acceptance.** The Execute marker links to the Execute acquisition
result on the other sequencer. Exercise evidence in a shared prefix,
existing fault markers, and a second simultaneous comparison.
Every retained evidence ID resolves to the intended event.

## CG. Scheduled LINQ rejection on the Play timeline

**Reproduction.** In Play, schedule two LINQ words at 8 ns for a
receiver with capacity one. The second word stops execution. With
a manual alignment of −4 ns, the timeline gives no point at 4 ns.
The diagnostics and toolbar report the raw Q1 timestamp instead.

**Required behavior.** Record one rejected LINQ arrival point at the
scheduled input edge, and link the overflow diagnostic to it. Keep
the input identity, capacity, and receiver instruction location.
Use the active time basis for the point and time shown in timeline
inspection. Preserve the raw execution reason and scheduled input
in TimelineIR. Keep the existing FIFO, register, and failed-stop
behavior.

**Acceptance.** The reproduction has one selectable rejection point
at aligned 4 ns and local 8 ns, with a diagnostic link and no
normal stop or register write. The aligned diagnostic and toolbar
use the aligned edge or a cause-only message. Cover overflow while
waiting for LINQ, during stop, and at an ordinary instruction
boundary. Preserve simulation and comparison marker behavior.

## CH. Network conflict times on the active axis

**Reproduction.** Start RT at 100 ns and send two known requests
that collide in the 128 ns network slot. The RT axis places this
slot at 28 ns, while the diagnostic reports 128 ns.

**Required behavior.** Display known launch slots in the timeline's
active time basis. Use the diagnostic's structured launch ticks and
the recorded shared simulation offset. Preserve raw diagnostics,
unknown slots, and the spacing duration. Apply the same rule to a
potential conflict with two known slots.

**Acceptance.** The reproduction displays 28 ns for both slots on
the aligned axis and 128 ns on the local axis. Check the standalone
page and the extension, including a manual offset and time-basis
switch. An unresolved opposing slot stays unknown. The underlying
diagnostic ticks and machine message remain unchanged.

## CI. Visible evidence for diagnostic event links

**Reproduction.** A network collision diagnostic lists two request
events followed by their two acquisition results. The extension's
Normal model contains the results and hides the requests. The
diagnostic's Show event link still targets the first hidden request
even though an acquisition result is available to select.

**Required behavior.** Let a diagnostic's Show event action choose
the first visible event from its ordered evidence in the current
view. Preserve the primary event's reveal behavior when no candidate
is visible, including collapsed Q1 and pending-setter links. Keep
the original IR order and leave semantic links unchanged.

**Acceptance.** In Normal, the reproduction selects an acquisition
result. In Debug it selects the original request. Check a diagnostic
with one event, a previously hidden Q1 link, and a missing first
candidate. The two viewers should use the active view to pick evidence.

## CJ. Terminal edge for a gate left open at RT stop

**Reproduction.** Compare Execute and Skip for a 4 ns TTL gate
open followed by `stop`, with RT starting at 100 ns and a peer
running to 300 ns. Execute leaves the gate open and records the
incomplete result at 104 ns. Its terminal marker incorrectly
appears at the peer's 300 ns edge.

**Required behavior.** Use the sequencer's digital capture cutoff
as the terminal edge for the unclosed-gate diagnostic discovered
during cleanup. Link the diagnostic to retained gate evidence.
Preserve an earlier recorded fault or underflow, ordinary closed
gate and scope behavior, and the independent peer's timing.

**Acceptance.** The Execute marker and its incomplete result both
occur at 104 ns, with a comparison-local evidence link. The peer
still ends at 300 ns, and Skip remains complete. Cover an unclosed
QTM gate and traced and prepared evidence.

## CK. Comparison terminal evidence in the event inspector

**Reproduction.** Select the Execute terminal for an unclosed TTL
gate. TimelineIR records the reason and links the same path's
incomplete result. The event inspector has no action to open that
result.

**Required behavior.** Show a comparison terminal's status and
reason in both inspectors. Offer a Show evidence action when an
eligible related event is present. Use the active view to choose
from the ordered evidence and keep the comparison path or shared
prefix identity. Preserve the existing marker geometry and source
selection.

**Acceptance.** Selecting the Execute terminal opens its own
acquisition result through Show evidence. Check a receiver terminal
with evidence on another sequencer, shared-prefix evidence, and a
marker without evidence. Exercise Normal and Debug in both viewers.

## CL. Debug trigger traffic in standalone Normal

**Reproduction.** Simulate an analog acquisition routed through a
trigger network. Normal in the extension shows the receiver arrival
and hides `debug.trigger_send`. Standalone Normal also renders the
request and launch from that debug lane.

**Required behavior.** Keep the sender's debug trigger traffic in
standalone Debug and keep receiver arrivals visible in Normal.
Apply the same lane distinction to Compare Both. Keep all evidence
and flows in TimelineIR. Show event should still prefer a visible
acquisition or other retained origin when available.

**Acceptance.** Normal shows the receiver's arrival at its existing
RT time and Debug also shows the request and launch. In a collision,
the diagnostic can select an acquisition result in Normal and the
request in Debug. Check an alternate path and an unobserved edge
window. Normal stays anchored at zero for a simulated RT origin.

## CM. Forward loop targets with resolved operands

**Reproduction.** Analyze `move 2,R0`, `nop`, `loop R0,@end`,
`wait 20`, `end: wait 4`, `stop`. The analyzer stops at the
forward loop and records neither the counter write nor the 4 ns
wait. Concrete and simulation decrement the counter to one and
take the forward target.

**Required behavior.** When the analysis can resolve a forward
`loop` target and its counter, use the same counter, flags, timing
and target rules as concrete execution. Handle a label, numeric
address, and register that resolves to a supported forward
instruction boundary. Keep backward loop compaction and existing
unresolved and invalid target reporting. The Qblox lowering note
describes subtraction followed by `jnz`, in line with the existing
concrete execution semantics.
[Qblox Q1ASM loop reference](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#loop-instt)

**Acceptance.** Initial counter two skips the 20 ns wait and
initial counter one executes it. Verify the counter write, flag
effect and Q1 duration against concrete execution for label,
immediate and resolved register targets. An unknown counter stays
unresolved, and a backward loop remains compact.

## CN. Loop counter reused as its branch target

**Reproduction.** With R0 initialized to five, `loop R0,R0`
branches using the old register value in Concrete, Simulation and
Play. The installed Qblox assembler produces the same binary for
`sub R0,1,R0` followed by `jnz R0`. The explicit form instead
reports that R0 is still being written when the branch reads it.

**Required behavior.** Apply the existing Q1 register-readiness rule
to the internal read of a loop whose counter and register target
alias, including resolved aliases. Report the hazard on the
source macro in every execution entry point. An affected concrete
path must stop before producing RT work from a guessed branch
target. Keep the ordinary loop macro representation and valid
counter, timing and target behavior for non-aliasing operands.
This follows the documented macro expansion and the extra
register-read cycle after `sub`.
[Qblox loop expansion and Q1 read timing](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html)

**Acceptance.** The macro and explicit pair report
`register_not_ready` for aliasing input, regardless of whether the
decrement would make the branch fall through. Compare Analysis,
Concrete, Simulation and Play. Check prepared execution and
`.DEF` register aliases. Separate counter and target registers
still allow a forward loop, and a gap between explicit operations
allows the new register value to be read.

## CO. Determinate forward jump into a compiled macro

**Reproduction.** Set flags with `cmp R0,1` and use a known
`jmp` to the compiled `jnz` inside a later `loop` macro.
Concrete, Play and Simulation execute that branch without the
preceding subtraction. Analysis stops at the compiled address
even when it can resolve both the flag and the branch destination.

**Required behavior.** Let Analysis follow a determinate forward
`jmp` to an executable lowered branch. Execute only that
branch using the established operand readiness, flags and timing.
Keep the compiled slot and original source location distinct.
Preserve conservative handling when the destination or flag is
unresolved, a dependency is not ready, or the transition would
require unbounded traversal.

**Acceptance.** A forward immediate or resolved register jump into
`loop`, `jlt` and `jge` interiors agrees with Concrete for
the lowered instruction and its next forward edge. Check taken
and fallthrough, the absence of a macro counter write, and an
unknown or unsafe transition that stays unresolved.

## CP. Simulation LINQ rejection on the active axis

**Reproduction.** With RT origin at 100 ns, deliver two LINQ
words at 120 ns to a simulation receiver with capacity one. The
rejected arrival is at aligned 20 ns, while the timeline
diagnostic and tooltip still report 120 ns. Standalone renders
the ordinary rejection as a small block instead of a point.

**Required behavior.** Render the simulated rejection as a
selectable point at the active time basis, consistent with the
Play rejection. Use a cause-only diagnostic in timeline
presentation or format its timestamp in the active basis.
Preserve the raw simulator reason, rejected input provenance,
and existing compare-terminal and queue semantics.

**Acceptance.** The point shows 20 ns when aligned and 120 ns
in a local view. A diagnostic opens the point without presenting
the other clock. Check both viewers, a Compare Both rejection,
and the accepted word and receiver state.

## CQ. Conditional branch to an unloaded address

**Reproduction.** Assemble `move 1,R0`, `nop`, `cmp R0,1`,
`jnz 120`, `wait 4`, and `stop`, one instruction per line. The
Qblox assembler accepts the program and encodes address 120 as
`0x78`. Analysis and Play continue to the 4 ns wait. Concrete
and Simulation report a missing instruction at compiled PC 120
before deciding whether to take the branch. The Qblox guide
defines a 4 ns continue path and a jump only when the zero flag
is clear.
[Qblox `jnz` instruction](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#jnz-inst)

**Required behavior.** Keep operand shape, immediate encoding
range, and register-readiness checks. Apply the loaded-instruction
lookup as an execution requirement only when the effective edge
uses the destination. Calculate the natural decision from the
current flags, loop counter, or compatibility comparison, then
apply a Simulation branch choice before checking the selected
edge. An unloaded selected target must use the existing terminal
diagnostic and stop the path without an assertion or a follow-on
RT event. An unconditional `jmp` always requires a loaded target.
The fallthrough path retains Q1 timing, loop counter writes and
flag updates where applicable. Preserve existing handling of
unresolved operands and flags. In Analysis, a resolved unloaded
numeric destination also permits a known loop fallthrough after
the counter and flag updates. Keep conservative behavior for
unresolved or taken Analysis loop edges.

**Acceptance.** The example falls through in all four modes and
shows a 4 ns Q1 branch and a 4 ns RT wait. Change the compare
immediate to zero and check the taken path's terminal behavior,
including its source and compiled address in Concrete and
Simulation. Cover a ready register containing 120, an ordinary
`jmp 120`, and a fallthrough `loop R0,120` starting with R0 equal
to one. Check the loop's zero counter and flags across modes,
including the Analysis case where the same address comes from
a ready register. In Simulation, force each of the opposite edges
in the two `jnz` cases and verify that only the selected taken
edge fails. Retain operand validation and the known-target and
macro-interior fixtures.

## CR. Status branch targeting a compiled macro interior

**Reproduction.** With R0 equal to one and ready to read,
assemble `cmp R0,1`, `jnz 6`, `jmp @fallback`, `loop R1,@end`,
`fallback: wait 8`, and `end: stop` after the initial `move`
and `nop`. The Qblox listing places the second `jnz` at
compiled PC 6 and its destination at PC 8. Analysis currently
stops at the first `jnz` before evaluating the zero flag.
Concrete, Simulation and Play follow the fallthrough and
execute the 8 ns wait. With `cmp R0,0`, those modes reach
the lowered `jnz` and skip the wait.

**Required behavior.** Evaluate a status-flag branch before
deciding whether its compiled interior target needs traversal.
A known false decision continues with normal Q1 timing. A known
true decision can enter a safe forward interior using the CO
readiness and bounded traversal rules. Track the original
branch and the lowered branch as distinct issues. The lowered
branch must see the flags that reach it, and must leave the
skipped macro counter unchanged. Keep unsafe or unresolved
taken edges conservative and preserve existing branch policy
for unresolved decisions. In the shared CO traversal proof,
a lowered branch with a known false decision and a valid,
ready numeric operand may continue even if its destination
has no loaded instruction. This follows the CQ lookup rule.

**Acceptance.** Compare the two example paths with Concrete,
including 4 ns fallthrough timing, taken timing, source and
compiled PC metadata, and RT event order. Repeat with a ready
register holding PC 6. A known fallthrough may point backward
or at a lowered branch whose operand would be unavailable.
An unresolved flag or a known taken edge to an unsafe interior
still produces an incomplete region without guessed RT work.
With `cmp R0,1`, `jmp 5`, `loop R1,120`, and `wait 8` after
the same initialization, the lowered `jnz` falls through to
the wait. With `cmp R0,0` it remains unsafe to follow.

## CS. Conditional macro targeting a compiled macro interior

**Reproduction.** Assemble `move 1,R0`, `nop`, `loop R0,6`,
`jmp @fallback`, `loop R1,@end`, `fallback: wait 8`, and
`end: stop`. The first macro's target is the second macro's
lowered `jnz`, at PC 6. Analysis stops at the first macro before
decrementing R0. Concrete, Simulation and Play decrement R0
and reach the wait. Change the initial counter to two and the
same modes reach PC 6 and skip the wait. The assembler also
accepts `jlt R0,1,6` and `jge R0,1,6` in the same position.

**Required behavior.** Resolve determinate `loop` and legacy
comparison macro decisions before requiring an interior
destination. Preserve the first macro's counter and flag
effects and its existing timing. On a known taken edge, use
the updated flags and register readiness at the lowered branch's
issue to prove the forward transition. Execute only the lowered
branch at the destination. Keep the CN hazard and conservative
handling for unsafe or unresolved paths. A taken edge into the
same source macro or an earlier one remains incomplete before
modeling the macro as a forward traversal.

**Acceptance.** Compare each macro with Concrete in both
directions. Check the first macro's Q1 duration and flags,
R0's counter write where applicable, and the absence of a
write to the skipped R1 counter. Cover a ready register
destination, an unknown condition, and a taken edge whose
next address cannot be followed safely. For register visibility,
start R0 at nine and use `loop R1,R0` as the second macro.
The first macro decrements R0 to eight, so the lowered branch
at PC 6 uses PC 8 even when PC 9 also has an instruction.

## CT. Effective RT origin after an alignment fallback

**Reproduction.** Simulate `nop`, `wait 20`, and `stop` with
`startup_delay_ns: 100` and `alignment.mode: first_play`.
There is no play event. Alignment records the first RT dequeue
as the fallback and assigns the wait aligned time 0 ns. The
standalone Normal plot still begins at −100 ns and leaves an
empty interval before its first tick. VS Code also determines
its viewport mode from the requested anchor setting alone.

**Required behavior.** When shared Simulation alignment
records an actual first RT dequeue as the anchor, use that
effective origin for Normal and Debug rendering in both
viewers. This includes fallback and `first_anchor` with more
than one candidate kind. Reuse the existing first-dequeue
view behavior. Keep the requested
mode, fallback warning, anchor, and raw event times in the IR.
An explicit anchor that resolves still retains pre-anchor RT
events. A missing anchor with no RT dequeue keeps the existing
simulation-time-zero behavior.

**Acceptance.** The first wait begins at the Normal left edge
at 0 ns. Toggling to Debug shows the negative Q1 prefill axis,
and switching back restores the Normal geometry. Verify both
viewers, an explicit anchor that resolves later than the first
dequeue, and a Q1-only program with no fallback anchor. With
`first_anchor` kinds `rt_queue_dequeue` and `play`, a selected
dequeue behaves like the default origin. With `q1_issue`
instead of `play`, the selected Q1 edge keeps its own policy.

## CU. Simulation label alignment at RT execution

**Reproduction.** Simulate `wait 20`, `anchor: wait 4`, and
`stop` with `startup_delay_ns: 100` and `label:anchor`.
The labeled wait starts on RT at local 120 ns. The current
anchor instead matches the enqueue at local 8 ns, so the
labeled RT wait displays at 112 ns. A label on a Q1 setter
can similarly select its pending state update.

**Required behavior.** For a shared Simulation label anchor,
select a concrete RT event from the labeled instruction using
its source identity. Exclude Q1 pending state and RT queue
enqueues, which occur during Q1 preparation. Keep the stable
earliest candidate across the project and ignore comparison
alternatives. A label with no qualifying RT execution uses
the existing first-dequeue or time-zero fallback. Keep
non-Simulation label alignment and explicit event-kind anchors.

**Acceptance.** The second wait anchors at 0 ns and the first
wait remains visible from −20 ns. Confirm the anchor event,
shared offset, and the original 100 and 120 ns execution times.
Check a Q1-only setter label, a labeled RT stop, a same-line
synthetic input from a different source file, and an explicit
`first_anchor` that intentionally selects a queue enqueue.

## CV. Explicit RT anchor separates Normal and Debug extents

**Reproduction.** Simulate `wait 20`, `wait_sync 4`, `wait 8`,
and `stop` at startup 100 ns with `after_first_wait_sync`.
The anchor is at local 124 ns and the first RT wait at aligned
−24 ns. The standalone Normal SVG starts at −124 ns even
though its first tick is −24 ns and the earlier Q1 issues
are hidden. Debug uses the same tick start and leaves its
negative Q1 interval without a time scale. The extension's
full window for explicit alignment uses the combined geometry.

**Required behavior.** For shared Simulation with a resolved
explicit RT anchor, calculate the Normal left edge from the
earliest visible RT event in the active time basis. Include
real RT events and arrival markers before the anchor. Debug
includes the Q1 prefill and uses the full labeled axis. Keep
the two geometries distinct when switching views. Preserve
the offset, local times, time basis controls, and the CT
first-dequeue behavior. Keep existing handling for Q1-only,
non-Simulation and explicitly selected non-RT anchors.

**Acceptance.** With the example, Normal begins at −24 ns and
Debug at −124 ns. The first RT wait is present in both, and
switching back restores Normal. Check both viewers, a resolved
anchor at the first RT event, and a pre-anchor arrival marker.
Switch VS Code to local time and verify that the first RT wait
still has its original start at 100 ns.

## CW. Q1-only peer extends the Normal RT axis

**Reproduction.** In a shared buffered Simulation run, one
sequencer executes `wait 20` and `stop`. A second sequencer
executes 100 `nop` instructions and `stop`, with no RT
packets. The first lane has RT activity at aligned 0–20 ns.
Its Normal timeline nevertheless extends to 396 ns because
the geometry includes the other lane's hidden Q1 issues.

**Required behavior.** With an effective shared RT anchor and
visible RT activity, derive the Normal right edge from the
visible RT projection. Retain RT comparison alternatives,
points, acquisition and scope windows, and any other visible
RT data beyond the final packet. Debug retains its full Q1
and RT extent. Keep Q1 source selection data and existing
behavior when there is no qualifying RT event or the anchor
explicitly uses a Q1 clock edge.

**Acceptance.** Normal ends at 20 ns and Debug at 396 ns in
the example, in both viewers. Verify mode switching and a
resolved explicit RT anchor. A visible late acquisition or
scope window must still extend the Normal range. Cover the
Q1-only project fallback.

## CX. Align to an armed RT wait

**Reproduction.** Simulate `wait 20`, `wait_trigger 3,4`,
`wait 8`, and `stop` at startup 100 ns with `first_wait_trigger`
and no trigger input. RT arms at local 120 ns and emits a
pending `trigger_wait`, but alignment reports a missing anchor
and uses the first dequeue at local 100 ns. An unmatched
`wait_sync` produces the same discrepancy with `first_wait_sync`.

**Required behavior.** In shared Simulation, allow a recorded
pending RT wait to supply the arm edge for `first_wait_trigger`
or `first_wait_sync`. Keep the earliest concrete baseline edge
across sequencers, regardless of whether that wait later
completed. `after_first_wait_sync` still requires a completed
barrier and uses the existing fallback for a pending-only run.
Preserve the selected wait's incomplete state and raw times,
explicit `first_anchor` matching, and non-Simulation behavior.

**Acceptance.** The first wait displays from −20 ns and the
blocked wait arms at 0 ns. The chosen anchor is the recorded
RT wait and no missing-anchor warning appears. Check unmatched
sync and `after_first_wait_sync`, a supplied trigger and completed
sync, and a later completed wait on a peer while the earlier
trigger wait stays pending.

## CY. Source and evidence for a blocked RT wait

**Reproduction.** Simulate `wait 20`, `wait_trigger 3,4`,
`wait 8`, and `stop` with startup 100 ns and no trigger.
The RT wait at source line 2 blocks at local 120 ns. The
blocked diagnostic points to line 4 (`stop`) and has no event
reference. A missing `wait_sync` peer points to the correct
instruction, but also omits the recorded wait event.

**Required behavior.** When quiescent Simulation reports a
blocked RT wait, attribute it to the command that armed it and
link the pending wait recorded at closure. Use that source for
the terminal edge so Compare Both can remap the evidence with
the existing comparison mechanism. Keep the pending duration,
execution status and reason, and Q1-only blocker provenance.
Emit only references to retained events in sparse execution.

**Acceptance.** In both blocked cases the diagnostic resolves
to the source wait and its pending RT event. Compare a branch
whose Execute path arms the trigger wait with a Skip path that
finishes. Check that its terminal refers to Execute's wait,
that a Q1 feedback stall still identifies its own instruction,
and that sparse prepared runs have no dangling references.

## CZ. Pending RT wait timing in both inspectors

**Reproduction.** In Simulation a blocked `wait_trigger` or
`wait_sync` is retained as `trigger_wait` or `sync_wait` with
`meta.pending: true`. Its right edge is where recording stops.
Both viewers present the two edges as an ordinary time range,
even though the wait never resumed. For an unmatched barrier
armed at 120 ns with trace cutoff 140 ns, 140 ns can appear to
be a release time.

**Required behavior.** Format a pending RT wait in each
viewer's tooltip and inspector as an arm edge with unresolved
completion. Identify whether it is waiting for a trigger or
for synchronization. If the recorded end is shown, label it
as the observation cutoff. Use the active time basis and keep
the visible interval and raw event data intact. Preserve the
existing nominal Analysis trigger text and timing for completed
waits. A diagnostic or comparison terminal may link the wait
without changing its timing semantics.

**Acceptance.** Check a zero-length pending trigger and a
nonzero pending sync interval in both viewers. Inspect the
alignment from CX, and switch VS Code to local time to see
the 120 ns arm. Check a completed wait and a Compare Both
terminal's evidence link after CY.

## DA. One timeline command per completed RT wait

**Reproduction.** Simulate `wait 20`, `wait_trigger 3,4`,
`wait_sync 4`, `wait 8`, and `stop` at startup 100 ns with a
trigger at local 150 ns. `trigger_wait` covers aligned 20–50 ns
and the main `wait_trigger` covers 20–54 ns. The extension
renders both as overlapping blocks in Time and as two commands
in Instruction. It likewise gives `sync_wait` and `wait_sync`
separate slots. Standalone also receives both records. When
the trigger never arrives, the extension instead gives the Q1
issue no direct RT target and selects the preceding `wait`.

**Required behavior.** Project a completed RT wait once from
its primary `wait_trigger` or `wait_sync` execution event. Match
the auxiliary wait to the same packet occurrence and comparison
path. Earlier Simulation IR without a packet reference can use
a unique match on source and arm time in that same path.
Preserve raw auxiliary timing and input evidence in the
IR, the separate trigger arrival point, and source and event
navigation. An auxiliary wait with no completed primary still
appears as the pending RT wait and is the corresponding Q1
issue's RT selection target. Its selection link does not imply
latched settings were applied. Both views preserve the primary
wait's full duration. Both Time and Instruction layouts show
only one RT command per completed wait.

**Acceptance.** The sample has five RT command slots including
stop, with the trigger point inside the `wait_trigger` slot.
Check same-time trigger arrival, multiple dynamic occurrences
of one source wait, Compare Both path isolation, and a pending
wait selected from its source, its Q1 issue, and its diagnostic
or comparison evidence.

## DB. Select the RT commit of a setter

**Reproduction.** Simulate `set_freq 4000000`, `upd_param 4`,
`set_cond 1,1,0,4`, `upd_param 8`, `set_cond 0,0,0,0`,
`upd_param 4`, and `stop` with Compare Both on `set_cond`.
The frequency first applies in the common `upd_param` at 0 ns.
Selecting the setter also selects later packets on Execute and
Skip whose snapshots merely retain the frequency's provenance.

**Required behavior.** Distinguish a setter occurrence that
actually commits at RT from provenance carried in later snapshots.
Keep both facts in Simulation IR. The selection index uses actual
commit evidence when present and handles earlier IR through its
existing provenance fallback. A persistent setter first captured
by a skipped packet may commit later. Two setters of the same
numeric value are distinct occurrences. Retain existing one-shot
phase behavior and skipped-packet capture evidence. Remap commit
references through Compare Both using the actual shared or
path-local setter identity.

**Acceptance.** The example selects the single common RT
application. Move the setter after `set_cond` and verify the
first applied block in each path, including a persistent value
carried past a skipped candidate. Test a same-value rewrite and
two dynamic executions of one source line. Normal selects the
appropriate RT block or blocks. Debug also selects the relevant
Q1 issue. Check one-shot phase capture and earlier IR.

## DC. Link setter issues and RT commits in standalone

**Reproduction.** Render Simulation `set_freq 4000000`,
`upd_param 4`, `upd_param 4`, and `stop` in standalone Debug.
The first RT packet applies the setter and the next packet
retains its frequency. The Q1 issue and RT application each
render with selectable IDs, but selecting either one does
not show the corresponding event.

**Required behavior.** Use DB's RT commit evidence to link
the visible Q1 setter issue to its actual RT applications
in both directions. Keep selection and inspector identity
on the event the reader clicked. Debug shows the related
Q1 and RT events. Normal keeps its RT-only presentation.
Treat a common prefix and each Compare Both path according
to the recorded commit. Earlier IR can use its explicit
setter lineage and first observed application within a path.
On linked setter or commit selection, restrict settings
highlighting to the actual links. Keep wait, loop and
control-flow relationships independent.

**Acceptance.** Clicking the first RT update in Normal shows
its RT details. Switch to Debug and confirm its Q1 issue is
highlighted. Clicking the Q1 issue highlights the first update
and leaves the carried snapshot unselected. Check a same-value
rewrite, Compare Both with a shared commit and with separate
first commits, and imported IR with an explicit setter link.

## DD. Coincident trigger arrivals remain inspectable

**Reproduction.** Simulate `wait 100` and `stop` at startup
100 ns with external trigger addresses 1 and 2 arriving on
one sequencer at local 120 ns. Both viewers draw identical
points at aligned 20 ns. The top point covers the other one,
so hovering the visible marker reveals only one address.

**Required behavior.** Present coincident arrivals on the
same visible sequencer and comparison path as one small point
at their actual time. Its tooltip identifies every address
and retains multiplicity when an address repeats. Keep each
raw event and its diagnostic or consumer navigation available
through the point's event references. Prefer a uniquely consumed
arrival as the default click target in the same trace context.
Keep separate points
for distinct arrival times, sequencers and comparison paths.
The point occupies no RT command slot and uses the active
time basis. Preserve the existing single-arrival presentation.

**Acceptance.** Both viewers show one point at 20 ns with
addresses 1 and 2. VS Code local time shows 120 ns. A duplicate
of address 1 is discernible from the tooltip. Check Time and
Instruction layouts, an independent point at a later time,
and direct navigation to the second original arrival.

## DE. Reveal the actual consumer of an external arrival

**Reproduction.** Simulate `wait 20`, `wait_trigger 2,4`, and
`stop` with startup 100 ns and scenario addresses 1 and 2
arriving at local 120 ns. The executed `wait_trigger` has an
explicit `input_event_ids` link to address 2. Its arrival source
is synthetic and there is no sender flow. VS Code's direct
event action cannot reveal the consuming source line.

**Required behavior.** For a direct synthetic trigger or LINQ
arrival source action, use an explicit executed consumer link
when it identifies one compatible Q1ASM source. Match sequencer
and comparison context. Keep the arrival selected and preserve
its raw time and synthetic source in TimelineIR. An unused or
ambiguous arrival has no source action. Preserve existing routed
flow actions and real source mappings. For grouped markers use
the explicitly selected arrival ID when available.

**Acceptance.** Direct navigation for address 2 opens line 2
while address 1 remains unused. Check a scenario LINQ arrival
consumed by a real pop or pull, a comparison where each path
has its own input link, and a path with no matching consumer.
Exercise the normal event-click path and existing flow source
and target controls.

## DF. Show completed RT wait phases

**Reproduction.** In Simulation at startup 100 ns, run
`wait 20`, `wait_trigger 2,4`, `wait 8`, and `stop` with a
trigger at local 150 ns. The completed primary wait spans
aligned 20–54 ns, with the trigger at aligned 50 ns. Both
inspectors show the enclosing range only. A sync wait that
arms at 20 ns and releases at 40 ns similarly shows 20–44 ns
without identifying the barrier release.

**Required behavior.** For a completed Simulation wait with
concrete phase evidence, show its arm, trigger arrival or
barrier release, and completion in both viewers. Use the
recorded RT execution edge and active display basis. Keep
the primary block and arrival point in their existing places.
Use the existing presentation for Play assumptions, pending
waits, and older traces without concrete phase evidence.

**Acceptance.** The trigger example identifies 20, 50, and
54 ns. For the barrier, simulate two participants with 20 ns
and 40 ns waits before `wait_sync 4`. The early participant
identifies 20, 40, and 44 ns, while the later participant arms
and releases at 40 ns. Check local time in VS Code, a shared
or compared path, and the pending, interrupted and Play controls.

## DG. Explain the next dequeue edge after a zero RT duration

**Reproduction.** Simulate `upd_param 0`, `wait 8`, and `stop`
with startup 100 ns. The update is a point at aligned 0 ns and
the wait begins at aligned 4 ns. The event records
`queue_duration_ns: 0` and `next_dequeue_not_before_ns: 104`,
but both inspectors display only 0–0 ns for the update.

**Required behavior.** When a rendered RT packet has an
explicit zero queue duration and concrete next dequeue bound,
show that bound in the active display basis as an earliest
eligible dequeue. Keep the actual following packet time
separate, since the queue can be empty at the bound. Use
the recorded bound in Simulation, Analysis, Concrete or
Play where available. In a nominal trigger epoch, label the
bound nominal. Preserve all geometry and raw times.

**Acceptance.** The update shows earliest next dequeue 4 ns
while its duration is zero. Check a zero-duration wait whose
trigger arrives later, a conditional skip with zero else delay,
and a later actual enqueue or dequeue. In VS Code local time
the example bound is 104 ns. Check older events without an
explicit bound, a nonzero RT duration, and nominal Analysis
timing after an unresolved trigger.

## DH. Attribute a full-queue stall to its pending RT wait

**Reproduction.** Simulate `wait_trigger 2,4`, three `wait 4`
commands, and `stop` with queue capacity one and no trigger.
The first command arms RT. Q1 fills the queue and stalls on
the third source line. Quiescence reports that no input can
resume `queue_full`, points at the queued `wait`, and omits
the pending RT wait from the diagnostic.

**Required behavior.** When a pending RT wait is preventing
dequeue and the Q1 blocker is `queue_full`, report the RT wait
as the blocker. Keep the Q1 queue stall event and its source
as dependent evidence. Use CY's retained RT wait reference
and terminal source so a comparison path can open it. Keep
existing handling of an independent Q1 feedback wait, SYNQ
participation, and sparse traces.

**Acceptance.** The blocked reason and source name the
trigger wait at line 1, with a live pending RT event link.
The Q1 stall still records `queue_full` on its own line.
A supplied trigger allows the queue and both cores to finish.
Verify a blocked Execute comparison, a Q1 feedback wait,
and a sparse prepared run with no dangling references.

## DI. Preserve pending wait evidence at a Simulation time limit

**Reproduction.** At startup 100 ns, simulate `wait 20`,
`wait_trigger 2,4`, `wait 8`, and `stop` with a time limit of
160 ns and a trigger scheduled at 200 ns. The trace contains
a pending RT wait from local 120 to 160 ns. The time-limit
diagnostic points to `stop` and has no event link. On the
default RT axis the cutoff is 60 ns, while the diagnostic's
unqualified 160 ns is on the scenario clock.

**Required behavior.** For an active pending RT wait selected
as the relevant blocker, use its actual source and retained
closure event when reporting the time limit. Preserve the
`step_limit` outcome, actual observation cutoff, and raw reason.
Use a cause-only display message where a timeline diagnostic
or comparison marker already exposes the active cutoff time.
Retain the configured local limit in metadata. Keep Q1-only
and instruction-step limit handling and sparse trace rules.

**Acceptance.** The diagnostic opens the RT wait at line 2
with observation cutoff 60 ns in aligned time. Its raw reason
retains the configured limit of 160 ns. Check a comparison
path that reaches the limit, a completed peer, and a trace
where the Q1 instruction limit is reached instead.

## DJ. Cap active RT packets at the observation limit

**Reproduction.** Simulate `wait 20`, `wait_trigger 2,40`,
`wait 8`, and `stop` at startup 100 ns with a trigger at local
150 ns and time limit 160 ns. The trigger releases RT, which
schedules completion at 190 ns. The trace stops at 160 ns but
the primary event still ends at 190 ns and can be presented as
completed. An ordinary `wait 100` similarly extends beyond the
observation limit.

**Required behavior.** In a time-limited run, clip an admitted
RT packet and its rendered execution events to the observed
cutoff when the scheduled completion lies beyond it. Preserve
the scheduled end as separate raw metadata, the observed RT
effect, and the time-limit outcome. Attach the limit diagnostic
to the retained active RT command when available. For a released
trigger or sync wait, show the arm, actual release, and observation
cutoff in the active basis. An incomplete wait's end is ineligible
for `after_first_wait_sync`. Keep the handling of pending waits,
completed peers, sparse runs and physical faults distinct.

**Acceptance.** The wait renders to aligned 60 ns, records the
actual trigger at 50 ns, and preserves planned local end 190 ns.
The inspector identifies the 60 ns observation cutoff. Check an
ordinary wait, a two-path play, a completed peer and a comparison
terminal. Verify after-sync alignment with an incomplete delay.
The time-limited lane has no rendered primary RT span past its
cutoff.

## DK. Close an active Q1 issue at the observation limit

**Reproduction.** Simulate `move 3,R0`, `nop`, `add R0,1,R1`,
and `stop` with `max_time_ns: 12`. The `add` starts at 8 ns
and its planned Q1 end is 20 ns. Debug shows the whole 8–20 ns
issue although observation stopped at 12 ns. The time-limit
diagnostic points to `stop`, and R1 has no completed write.

**Required behavior.** Close a started but unfinished Q1
issue at the observation cutoff. Keep its planned end and
pending write timing distinguishable from observed execution.
When an active Q1 issue is the only relevant blocker, report
its source and link the retained issue. Preserve DJ's RT
evidence when both cores are active, the instruction-step
limit, and sparse traces without dangling references.

**Acceptance.** Debug shows the observed 8–12 ns interval
and the diagnostic identifies line 3. The planned end remains
20 ns in the trace and R1 remains pending. Check a dual-core
cutoff, a Q1 instruction ending exactly at the time limit,
and a Q1 stall with no active issue span.

## DL. Inspect an incomplete Q1 issue

**Reproduction.** In the DK fixture, Hardware's Q1 detail
renders `Flags before → after` from the issue metadata even
though the `add` was still executing at the observation cutoff.
A plain 8–12 ns range after clipping would also omit that
20 ns was the planned end.

**Required behavior.** Use the clipped issue's observation
and planned-end evidence to distinguish issue start, cutoff,
and planned completion in both Debug viewers. Project each
time onto the active basis. In Hardware, show observed input
registers and pre-issue flags. Mark after-flags as unobserved
for a clipped issue, and continue to derive readable register
values from the committed history. Preserve normal completed
issue details and raw trace metadata.

**Acceptance.** The fixture identifies 8 ns issue start,
12 ns cutoff and 20 ns planned end. Hardware can inspect
R0 = 3 and shows that post-issue flags are unobserved. R1
has no completed write. Check a completed issue at the exact
limit and a shared RT axis with negative aligned Q1 timing.

## DM. Distinguish pending writes in a DEF run trace

**Reproduction.** Run the DK program in DEF Runs and inspect
the limited `add`. The case has a pending R1 write whose planned
ready time is 24 ns. The instruction detail labels that computed
value `readable at 24 ns` although the run was observed only
through 12 ns. The instruction position also loses the planned
end after DK clips the observed range.

**Required behavior.** In the DEF Runs trace, label pending
writes by their planned availability and pending status. Keep
computed values separate from the readable register snapshot.
Show a clipped Q1 issue's observed interval and planned end.
Use the trace's local Q1 clock and preserve completed history,
case status, sweep comparisons and saved-trace compatibility.

**Acceptance.** The limited case shows recorded Q1 time 12 ns,
`add` planned end 20 ns and a pending R1 write planned for 24 ns.
R1 is unavailable in the final result. Check a fully observed
write, a pending write with unknown availability, and a trace
where a pending write carries over to a later issued instruction.

## DN. Keep future Q1 latch changes outside the observed trace

**Reproduction.** Simulate `wait 100`, `set_freq 4000000`, and
`stop` with startup 4 ns and `max_time_ns: 6`. DK clips the
Q1 setter from planned 4–8 ns to observed 4–6 ns. Its
`latched_state_pending` event still appears at 8 ns. Debug
can extend past the cutoff and Hardware can show the frequency
as pending at a time the run did not observe.

**Required behavior.** At a Q1 observation cutoff, keep the
active issue's scheduled latch changes as planned metadata.
Exclude its future latch transitions from the observed event
stream and pending-state projection. Preserve any earlier
observed transitions, exact-cutoff transitions, RT commit
evidence and original time-limit outcome. Keep references
valid when an issue or planned latch is sparse.

**Acceptance.** The Q1 and RT trace in the fixture ends at
6 ns. The frequency's planned ready edge at 8 ns is retained
separately from observed pending or applied values. Check an
earlier setter on the same field, an issue ending exactly at
the limit, and sparse execution without dangling references.

## DO. Close an active Q1 issue on a terminal runtime error

**Reproduction.** Simulate `move 3,R0`, `nop`, `wait 4`,
`add R0,1,R1`, `wait 4`, and `stop` at startup 12 ns. An RT
queue underflow at local 16 ns halts the sequencer. Debug still
shows the active `add` through its planned end at 24 ns. The
R1 write was never ready.

**Required behavior.** If a terminal Simulation error ends
an admitted Q1 issue, clip the observed span to the fault
edge and retain the planned end and scheduled write readiness
using DK's trace metadata. Keep the original fault category,
source and marker evidence. Future latch updates remain
planned using DN's separation. Preserve completed issues,
exact-edge behavior, independent peers, time-limit and
instruction-step limit behavior, and sparse execution.

**Acceptance.** The `add` spans local 12–16 ns and records
its planned end at 24 ns. R1 has no completed write. Check
LINQ FIFO overflow during Q1, a future latch transition,
and a completed instruction at the fault edge.
[Qblox Q1ASM guide](https://docs.qblox.com/en/2026.10.1/products/qblox_instruments/q1/index.html#instructions)

## DP. Close the active RT packet on LINQ FIFO overflow

**Reproduction.** Simulate `wait 100` and `stop` at startup
100 ns with a LINQ FIFO capacity of one and two inputs at
local 120 ns. The second word is rejected and the sequencer
fails. The admitted wait still occupies local 100–200 ns,
past its aligned 20 ns rejection marker.

**Required behavior.** Clip the active RT packet and its
rendered execution events at the overflow edge. Retain the
planned end and already observed effects. Preserve the
accepted entry, rejected input marker, diagnostic source,
and primary evidence link. Independent peers continue.
Apply the same trace semantics to a comparison rerun.

**Acceptance.** The wait ends at aligned 20 ns with planned
local end 200 ns. Check both play paths, a completed peer,
an overflow exactly at a packet end, and a comparison with
overflow on one path only.

## DQ. Reveal the sender of a rejected routed LINQ arrival

**Reproduction.** A sender issues two `fb_com_data` packets
to a receiver with capacity one. The second arrival's rejection
marker records the second `feedback_send` event ID. The
inspector exposes only the receiver-side rejection, so it
cannot reveal the RT command that sent the rejected word.

**Required behavior.** Expose an action from a routed LINQ
rejection to its retained sender evidence in both viewers.
When the send annotation has no standalone block, use its
explicit source packet to locate the matching visible RT
command. Use the recorded IDs and comparison context. Keep
the rejected point, error and receiver-side navigation.
Scenario inputs without a sender and sparse or ambiguous
provenance retain their existing inspection behavior.

**Acceptance.** Two `fb_com_data 16` commands with route
latency 20 ns and startup 100 ns reject the second delivery
at local 124 ns. The sender action highlights the second RT
command, while the overflow diagnostic still opens the
rejected point. Check a compared path, a scenario input and
a missing source reference.

## DR. Close RT observation when Q1 validation stops the model

**Reproduction.** At startup 4 ns, simulate `wait 100`,
`move 3,R0`, `add R0,1,R1`, and `stop`. The simulator stops
on the register-readiness error at local 8 ns. The RT wait
still runs to its planned end of 104 ns in the trace. A Q1
operation without modeled execution can leave the same tail.

**Required behavior.** When Q1 validation stops the modeled
run, close any observed active RT packet at that edge and
retain its scheduled end as planned data. Use observation
cutoff metadata, as this records the trace boundary. Preserve
the original Q1 reason, source and status. Link the retained
RT span as cutoff evidence when available. Report the observed
Q1 stop edge as elapsed time. Leave completed packets, sparse
references and independent peers consistent.

**Acceptance.** The readiness example keeps the `add` source
diagnostic and clips the previous wait to local 8 ns without
an issued `add` or completed R1 write. Check a blocked Q1
instruction, a packet already complete at the edge, and a
comparison alternative that stops at validation.

## DS. Close RT observation at a Q1 program boundary

**Reproduction.** At startup 4 ns, simulate `wait 100` without
a `stop`. Repeat with `wait 100`, `nop`, and `stop` using
`max_steps=1`. Both runs stop Q1 observation at local 4 ns,
but both traces show the RT wait through its planned end
at local 104 ns. The first reports program exhaustion. The
second reports the instruction limit on the `nop` line.

**Required behavior.** On Q1 program exhaustion or the Q1
instruction limit, close the admitted RT span at the actual
observation boundary and retain its scheduled end as planned
data. Use the same trace cutoff contract as DR. Include RT
effects observed at the boundary, and attach retained RT
cutoff evidence when available. Preserve the termination
status, reason, and diagnostic source. Preserve completed RT
spans, the normal queue drain after explicit `stop`, malformed
source recovery, and execution on independent peers. Sparse
traces must reference only retained events.

**Acceptance.** Both fixtures retain an observed RT point
at local 4 ns with `planned_rt_end_ns=104` and
`observation_cutoff_ns=4`. Preserve the first fixture's
`error` and missing-stop diagnostic and the second fixture's
`step_limit` and next-instruction diagnostic. Check a packet
that finishes at the boundary, sparse cutoff evidence, and
an explicit `stop` that allows a queued wait to complete.

## DT. Explain an incomplete RT command in both viewers

**Reproduction.** At startup 100 ns, simulate `wait 20`,
`wait 100`, `wait 8`, and `stop` with `max_time_ns: 160`.
The second wait starts at aligned 20 ns and observation ends
at 60 ns. Its scheduled end is local 220 ns. Both viewers
show only the observed time range. A two-path `play` has the
same omission. A released trigger wait labels the cutoff but
does not show its scheduled end.

**Required behavior.** When an RT execution event has valid
`observation_cutoff_ns` and `planned_rt_end_ns`, and the
observed event ends at that cutoff, identify its observed
start, observation cutoff, and planned end in the timeline
hover and inspector. Project all times onto the active axis.
Preserve the arm and actual release for a released trigger or
sync wait. Retain the current behavior for unresolved waits,
completed RT commands, and events without valid cutoff
evidence. The planned end is inspection text and does not
extend the plotted interval or timeline domain.

**Acceptance.** Both viewers show a cutoff of 60 ns and a
planned end of 120 ns on the aligned axis, or 160 ns and
220 ns on the local axis. Check a folded two-path play, an
already completed peer command, and a released trigger wait
with actual arrival at aligned 50 ns and planned end at 90 ns.
Keep the completed wait and pending wait inspection controls.

## DU. Separate observed NetZero compensation from its plan

**Reproduction.** Use `set_awg_offs 16384,0`, `upd_param 8`,
`set_awg_offs 0,0`, `play_netzero 100,0`, and `stop`. Enable
QCM NetZero on output and path 0 with 1 ns samples, amplitude
0.5 and slew 2.5e8 per second. At startup 100 ns and time
limit 112 ns, the modeled compensation starts at local 108 ns
with planned end 117 ns. Physical traces stop at 112 ns, but
the result event and both result summaries still report the
full pulse and compensation area.

**Required behavior.** When the Simulation time limit cuts a
modeled compensation, close its observed signal event at the
limit and mark the result incomplete. Keep the planned end
and planned compensation area separate from the observed end,
samples and area. Report the observed prefix on the signal
model's existing sample grid. Keep both result summaries and
the trace event consistent. A missing completion remains
missing. Preserve ordinary RT `stop` with a continuing
compensation, zero-area and unmodeled cases, and compensation
that completed at or before the limit.

**Acceptance.** The event ends at local 112 ns with planned
end 117 ns. The observed prefix contains the four samples
starting at 108 ns and its signed area is −1.75 normalized
full scale × ns. The planned area is −4. Check traced and
prepared results, no samples when observation ends at start,
and a limit exactly at the compensation's completion.

## DV. Close both cores when RT dispatch refuses an effect

**Reproduction.** At startup 4 ns, simulate `wait 16`,
`fb_com_cfg 1,0,4,100`, `move 3,R0`, `nop`, `add R0,1,R1`,
and `stop`. RT reaches the unsupported LINQ configuration at
local 20 ns and stops. The trace shows the config from 20 to
120 ns and Q1 `add` from 16 to 28 ns. A physical `play` with
missing waveform memory also shows both RT paths through the
planned duration after an immediate dispatch error.

**Required behavior.** If RT dispatch terminates execution
after emitting the admitted packet's RT event, end both active
core spans at the terminal edge. Keep the RT attempt as evidence
and its scheduled end as planned metadata. Preserve the original
blocked or error status, source and reason, and link the retained
RT attempt. Preserve same-edge effects already observed and
keep unobserved Q1 writes and latches out of committed state.
Do not synthesize the refused effect, a physical fault, or an
additional dequeue. Keep conditional skips and refusals detected
before RT execution consistent with their existing contracts.

**Acceptance.** The configuration is a point at local 20 ns
with planned end 120 ns. Q1 `add` is observed from 16 to 20 ns
with planned end 28 ns and no committed R1 write. Check both
paths of a failed physical `play`, a conditionally skipped
unsupported command, a completed peer and sparse evidence.

## DW. Close active spans on an asynchronous acquisition refusal

**Reproduction.** At startup 100 ns, simulate `acquire 0,0,8`,
`wait 100`, and `stop` with `integration_length_acq=20` and
no acquisition result or modeled input. The acquisition
window reaches local 120 ns and Simulation reports `blocked`
there. The later RT wait still appears to run from 108 to
208 ns. Supplying a scenario result allows the wait to finish.

**Required behavior.** When acquisition completion refuses
an unavailable result or unsupported output, close any active
RT and Q1 observation at that event edge. Keep the acquisition
source and original reason. Retain observed acquisition timing
separately from result availability, and link retained cutoff
evidence without inventing a result or an acquisition event.
Already available results and routes retain their current
processing order. Preserve successful completions and peers.

**Acceptance.** The blocked fixture retains the acquisition
window 100–120 ns, publishes no acquisition result, and ends
the wait at 120 ns with planned end 208 ns. Check the fixture
with a supplied result, a prepared run, a blocked feedback
result that already has observed acquisition data, and a
packet that completed at the same tick as the refusal.

## DX. Remove the hidden Q1 row gap in standalone Normal

**Reproduction.** Generate standalone HTML for the trigger
execution example with `Compare both`. Normal hides the Q1
detail group but reserves its 34 px row. The main RT, Execute
and Skip row rules sit at 82, 150 and 184 px. There is a blank
row between the main RT and Execute rows.

**Required behavior.** When standalone Simulation has
separate Normal and Debug geometry, reserve Normal rows and
SVG height only for the visible lanes. Keep Q1 source and
diagnostic evidence available through existing navigation.
The alternate Debug geometry retains its Q1 lane, negative
axis, and command selection. Preserve the Normal RT origin,
comparison labels and order, and timing coordinates. Apply
the same layout when a user switches from Debug to Normal.

**Acceptance.** The original example's three Normal row
rules are one row apart. Its left tick is 0 ns and trigger
address 1 remains at 30 ns. Toggle to Debug and back. Check a
project with two sequencers, Q1 diagnostic navigation, and a
standalone timeline without separate mode geometry.

## DY. Preserve the QTM result at a terminal LINQ refusal

**Reproduction.** At startup 100 ns, simulate
`fb_tdc_tdelta_id 16,4`, `acquire_timetags 0,0,1,0,8`,
`acquire_timetags 0,0,0,1025,4`, `wait 100`, and `stop`.
With one rising edge at 107 ns + 1 tick, the local window
produces a valid delta of 385 ticks. The fractional close at
120 ns + 1 tick stops the model because LINQ scheduling for
that close is unresolved. The wait still extends to 216 ns
and the diagnostic has no event evidence.

**Required behavior.** When QTM window completion stops the
model, close active RT and Q1 spans at the actual terminal
tick. Keep the already recorded QTM result, bin and its
provenance. Attach retained result and cutoff evidence to the
existing diagnostic. Preserve its close-instruction source
and status. Keep LINQ transmission unresolved, and preserve
completed packets, unaffected sequencers and sparse links.

**Acceptance.** The wait starts at 116 ns and ends at
120.0078125 ns, with planned end 216 ns. The diagnostic
retains the 385-tick result and active wait when recorded.
There is no transmitted word. Close delay 1024 and ID 0
are successful controls. Check prepared execution and the
configured invalid-delta error path.

## DZ. Accumulate repeated QTM writes to the same bin

**Reproduction.** At startup 100 ns, open and close
`acquire_timetags` windows 100–200 ns and 204–364 ns on the
same acquisition and bin. Supply rising edges at 120, 228,
273, and 318 ns. The windows report counts 1 and 3 and
time deltas 2560 and 3072 model ticks. The final bin holds
only the second result and has no write count.

**Required behavior.** Aggregate accepted `acquire_timetags`
writes by acquisition and bin. Retain the sum and arithmetic
mean of counts and time deltas, with `avg_cnt` identifying
the number of accepted writes. Keep model deltas in 1/128 ns
ticks, allowing a fractional tick in a mean. Preserve each
window's individual result, event, classification and LINQ
payload. Keep unresolved aggregate fields unavailable if a
recorded contribution lacks that field. Use the current
discard and error policies to determine whether a window
writes, and include an explicitly recorded zero. Preserve TTL
totals and digital-level behavior. The initial scope is count
and delta averages for this ideal model. Threshold aggregation,
firmware overflow and hardware units require separate evidence.

**Acceptance.** The final bin has `avg_cnt=2`, count sum 4,
mean count 2, delta sum 5632 model ticks and mean delta 2816
ticks. Per-window results still show the two original values.
Check different bin addresses, a single write, prepared runs,
supplied values, recorded zero and an unavailable contribution.
[Qblox QTM Binned Mode](https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QTM/150_binned_mode_qtm.html)

## EA. Close observation on a rejected QTM edge stream

**Reproduction.** At startup 100 ns, simulate
`fb_llp_tags_id 16,4`, `acquire_timetags 0,0,1,0,4`,
`wait 100`, `acquire_timetags 0,0,0,0,4`, and `stop`.
A rising edge at 120 ns is observed. Without an explicit
LINQ timestamp origin the model stops there, but the wait
is drawn through 208 ns. The diagnostic has no link to the
observed edge that caused the refusal. A fractional edge
with an explicit origin has the same observation problem.

**Required behavior.** If edge-stream LINQ processing stops
the model, close active RT and Q1 observation at the exact
input tick. Attach the retained edge and RT cutoff to the
existing diagnostic. Preserve the observed edge, incomplete
local capture, original diagnostic source and reason. Keep
unresolved output explicit. Preserve valid stream emission,
other peers and sparse traces with retained references only.

**Acceptance.** In the missing-origin case the wait is
observed from 108 to 120 ns and planned through 208 ns.
The edge remains at 120 ns and no word is sent. Origin zero
and an integer edge emit normally. Check a fractional edge
with an explicit origin and prepared execution.

## EB. Preserve evidence for a rejected TB write-combine round

**Reproduction.** At startup 100 ns, two senders each run
`fb_acq_tb_id 16,4`, `fb_acq_tb_cfg 1,0,1,4`,
`acquire 0,0,8`, `wait 100`, and `stop`. Route ID 16 to a
receiver running `wait 100` and `stop`, with 5 ns latency.
Both acquisitions supply thresholded 1. The round rejects
overlapping contributions at local 116 ns. The three RT
waits still extend to their planned ends. All diagnostics
refer to the first sender's file and carry no event links.

**Required behavior.** Keep the existing modeled round
rejection and affected participants. Close their active RT
observation at the rejection tick. Preserve both observed
contributions without emitting a combined word. Keep each
sender's own source when available and the receiver's local
source or retained cutoff evidence. Link the contributing
events and any retained local cutoff in stable order. An
already terminal sequencer keeps its earlier diagnostic.
Sparse references must resolve to retained events.

**Acceptance.** The receiver wait ends at local 116 ns with
planned end 200 ns. The senders' waits are points at 116 ns
with planned end 216 ns. Both contributor IDs are available
in a full trace. No word arrives. Bit positions 0 and 2
deliver the successful word at 121 ns. Check prepared runs,
reverse source order and an unaffected peer.

## EC. Close Q1 observation at an RT refusal

**Reproduction.** At startup 4 ns, simulate `wait 16`,
`fb_acq_tb_mock 1,1,1,100`, `move 3,R0`, `nop`,
`add R0,1,R1`, and `stop`. RT dequeues the mock at local
20 ns and stops the model before admitting the effect. The
previous wait ends at 20 ns, but Q1 `add` remains drawn to
28 ns. Replacing the mock with `wait_sync 4` on a sequencer
with `sync_en: false` produces the same Q1 overhang.
A missing input trace with an active possible-trigger window
also reproduces it at the `set_cond` decision or during a
pending `wait_trigger`.

**Required behavior.** End active Q1 observation at the
pre-admission refusal edge. Preserve the refused source,
original reason and observed Q1 issue/dequeue provenance.
Keep the refused packet without an RT execution interval or
effect. Leave completed RT timing, future Q1 writes and
sparse evidence consistent. Preserve conditional skips and
enabled SYNQ execution. For a trigger refusal, keep the
cross-sequencer source evidence and the pending wait through
its observed cutoff when present.

**Acceptance.** Both runs report their original `blocked`
outcome at 20 ns. The admitted `add` spans 16–20 ns, planned
end 28 ns, with no committed R1 write. The previous RT wait
remains complete and the refused command has no RT span.
Check prepared execution, a skipped mock and enabled SYNQ.
With a possible trigger window beginning at 105 ns, the
receiver's Q1 `add` beginning at 100 ns ends at 105 ns with
planned end 112 ns. Verify both a decision at 105 ns and an
armed wait, with the corresponding known-input controls.

## ED. Open distinct comparison terminal evidence

**Reproduction.** Prepend `set_cond 1,1,0,4` to the DY
program, disable the condition immediately after the stream
configuration, and compare both paths. The Execute terminal
retains the observed QTM result and the clipped RT wait as
separate path event IDs. Both viewers offer a single action
that always opens the first visible event.

**Required behavior.** Keep the primary Show evidence action
and its existing visibility fallback. When distinct evidence
events can be shown in the current view, offer additional
actions that identify and open those exact events. Preserve
the terminal's comparison path and shared-prefix context.
Avoid duplicate, missing and wrong-path actions. Reuse
existing reveal behavior for a folded command and keep one
action for the single-evidence case.

**Acceptance.** The Execute terminal opens its 385-tick
result through the primary action and the clipped Execute
wait through an additional action. The Skip command stays
unselected. Check Normal, Debug, a shared-prefix or
cross-sequencer target, and hidden or missing evidence.

## EE. Attribute a time limit to deferred signal work

**Reproduction.** Run the DU example at local limit 112 ns.
The RT stop and clipped NetZero result are at aligned 12 ns.
The diagnostic instead cites the `stop` instruction, has no
event link and shows unqualified local time 112 ns. A QRM
integration of 100 ns started by `acquire 0,0,8` at startup
100 ns has the same problem after the RT queue drains. The
NetZero Execute terminal also exposes the local value in its
reason when only that path runs the pulse.

**Required behavior.** Give every Simulation time-limit
diagnostic the existing cause-only display message and raw
limit metadata. Preserve the raw execution reason. When only
deferred signal work is active and its source is known, use
that source and link retained evidence. Use the observed
NetZero result or the acquire block carrying the incomplete
integration or scope window. Retain active Q1 and RT blocker
precedence. For sparse traces or work without an observed
anchor, preserve the actual status and omit missing links.

**Acceptance.** The NetZero time limit identifies
`play_netzero` and opens the observed result ending at
aligned 12 ns. The integration case opens `acquire` and keeps
its incomplete window. The comparison terminal reason has
no unqualified local timestamp. Check an exact signal
completion, a completed peer, prepared execution and a
time limit while the first Q1 instruction is waiting for
feedback and has yet to issue.

## EF. Close NetZero results at a sequencer failure

**Reproduction.** Use the DU pulse with startup 100 ns, but
trigger LINQ FIFO overflow on its sequencer at local 110 ns.
The pulse begins at 108 ns. The result claims all nine
samples through 117 ns even though the sequencer fails and
no completion event is observed. With a peer running to
200 ns, the shared physical renderer samples to 200 ns and
the same premature result remains in the sequencer trace.

**Required behavior.** Bound uncompleted NetZero result
events and both result summaries by their sequencer's
terminal observation edge, using DU's observed samples and
separate planned fields. A scheduled completion at the
same tick counts only if its callback ran. Preserve a pulse
that completed before failure and normal pulses that outlast
RT `stop`. Keep project-level numerical signal sampling and
peer execution separate from per-sequencer result observation.

**Acceptance.** Overflow at 110 ns retains two observed
samples with compensation area −0.75, planned end 117 ns
and planned area −4. The result agrees in full and prepared
execution, with or without a longer peer. With a following
RT wait, compare overflow at 117 ns before the completion
phase with overflow at 118 ns after completion.

## EG. Close active spans on a delayed QTM gate refusal

**Reproduction.** At startup 100 ns, simulate
`acquire_timetags 0,0,0,1025,4`, `wait 100`, and `stop`.
The effective close at 108.0078125 ns has no open gate.
The model stops there, but the later RT wait remains drawn
from 104 to 204 ns. Opening a window first and omitting its
bin definition reproduces the problem at its delayed close.

**Required behavior.** When a scheduled QTM gate action
refuses its effect, close active RT and Q1 spans at the
effective boundary. Keep the original diagnostic and source.
Link the observed gate command and the retained RT cutoff
where available. Preserve prior gate or scope observations
and leave rejected results absent. Keep sparse references
valid and preserve valid delayed gates, including those
whose effects outlast normal RT drain.

**Acceptance.** The close-without-open fixture keeps the
completed close command at 100–104 ns and clips the wait at
108.0078125 ns with planned end 204 ns. The missing-bin
case identifies the close command and has no binned result.
Check prepared execution, an independent peer and a valid
gate with the same delay and a bin definition.

## EH. Open distinct TB rejection evidence

**Reproduction.** Generate the EB collision with senders
`a` and `b` and receiver `rx`. Their diagnostics now retain
both contribution IDs and each local RT cutoff. Every
diagnostic row nevertheless offers only `Show event`,
which opens the same first contributor `a` in Normal.

**Required behavior.** Retain the generic diagnostic action.
For a TB write-combine rejection, also offer exact actions
for distinct retained contributors and the local cutoff.
Identify targets by sequencer and role. Reuse existing
timeline reveal behavior, respect the active view, and keep
single-event and unrelated diagnostic behavior. Omit
unavailable or duplicate targets.

**Acceptance.** From the receiver diagnostic, both viewers
can open `a`, `b`, and the clipped receiver wait separately.
Check the sender's local cutoff, Debug, prepared or partial
evidence, and a successful round without rejection actions.

## EI. Inspect an incomplete NetZero result

**Reproduction.** Open the NetZero result from the EE
time-limit diagnostic. The plotted interval is aligned
8–12 ns and the result is incomplete. The inspection text
omits its planned end, observed area and planned area,
although all three are present in the trace.

**Required behavior.** For a retained incomplete NetZero
result with valid observation metadata, show the observed
cutoff and planned end in the active time basis. Show the
observed and planned compensation areas with their units in
the inspector. Preserve the observed timeline geometry and
the inspection of completed or unmodeled results.

**Acceptance.** The DU fixture shows cutoff 12 ns and
planned end 17 ns on the aligned axis, or 112 ns and 117 ns
on the local axis. Its observed and planned areas are −1.75
and −4 normalized full scale × ns. Exercise the diagnostic
link, a comparison event and an ordinary completed result.

## EJ. Honor sequencer observation end in Hardware

**Reproduction.** At startup 100 ns, simulate a receiver
running `wait_trigger 2,4` then `stop`, and a peer running
`wait 100` then `stop`. Two LINQ arrivals at local 120 ns
with capacity one end the receiver run at aligned 20 ns.
The Hardware cursor still calls the receiver RT core
`waiting` at aligned 75 ns, while showing the project's
100 ns horizon and the unqualified local error time.

**Required behavior.** Export the recorded run's terminal
edge per sequencer, with its local time, category and
display reason in `execution[seq].terminal` as `local_time_ns`,
`category` and `display_reason`. Preserve the raw execution reason and
the existing project simulation horizon. In Hardware, stop
displaying an active core or pending wait at and after its
observation end. Preserve previous completed core state and
readable values. Show the cause and observation end in the
active time basis. Keep the history, peers, recorded-run
selection during Compare Both, and old traces without a
terminal edge consistent with existing behavior. A `stop`
issue whose planned end follows the cutoff is incomplete.

**Acceptance.** The receiver is waiting at aligned 19 ns.
At and after 20 ns it reports observation ended, while the
peer remains active at 75 ns. Its completed Q1 `stop` stays
completed. The corresponding local edge is 120 ns. Check
an unresolved Q1 stall, prepared output, a Q1-only failure,
and an older or analytical trace.

## EK. Apply Hardware sample alignment once

**Reproduction.** At startup 100 ns, simulate
`set_awg_offs 16384,0`, `upd_param 12`, and `stop` with a
1 ns physical sample grid. The Hardware DAC cursor at
aligned 5 ns reads the correct 0.5 sample but labels it
`sample -95 ns`. The signal state already projects the
local sample time of 105 ns.

**Required behavior.** Render the selected sample time in
the basis returned by the signal state. Keep the plotted
trace, cursor, value, source and unavailable states.

**Acceptance.** Aligned cursor 5 ns shows `sample 5 ns`.
The same local cursor 105 ns shows `sample 105 ns`. Check
the chart endpoints and an unavailable sample control.

## EL. Inspect QTM bin aggregates at the Hardware cursor

**Reproduction.** Use the DZ two-window fixture. TimelineIR
exports the final QTM bin with two writes, mean count 2 and
mean delta 2816 ticks. At the second result the Hardware Bins
view shows only the most recent window, count 3 and delta
3072 ticks. Its target history contains four open and close
commands, distinct from the two accepted writes.

**Required behavior.** Make the aggregate after each accepted
QTM bin write available as `bin_aggregate` on its result and
retained event metadata. Use the same normalized values,
totals and `avg_cnt` as the bin summary. In Hardware,
show the latest aggregate at or before the cursor separately
from the per-window result and existing target history.
Use the model's aggregation rules and bin identity. Preserve
unknown fields, discarded writes, the recorded run during
Compare Both, and older traces without aggregate history.
Apply a recorded zero as a numeric contribution even when
its original delta was invalid. Only show writes observed by
the selected time.

**Acceptance.** At the first close the bin has one accepted
write, count 1 and delta 20 ns. At the second it has two
writes, mean count 2 and mean delta 22 ns, beside the latest
window's count 3 and delta 24 ns. Check a cursor before the
first result, separate bins, a discard, and unknown data.

## EM. Close the Play Q1 issue at a rejected arrival

**Reproduction.** Play `move 3,R0`, `move 7,R2`,
`add R0,1,R1`, and `stop` with a LINQ FIFO capacity of one
and two scheduled receipts at local Q1 16 ns. Rejection
occurs at 16 ns, but the admitted `add` is still recorded
as executing from 8 to 20 ns. The next instruction never
issues. A rejection within an admitted `stop` can leave
the same planned tail.

**Required behavior.** End an already issued Play Q1 span
at the rejected arrival if the edge precedes its planned
completion. Retain the planned end using the Q1 observation
metadata recognized by both inspectors. Preserve the rejected
arrival and its diagnostic source. Keep completed issues
and recorded register history, and leave unresolved writes
uncommitted. Play's analytical RT schedule keeps its existing
meaning. An input failure before any issue creates no issue.

**Acceptance.** With alignment −4 ns the `add` has observed
range 4–12 ns and planned end 16 ns, while the rejection is
at aligned 12 ns. Local times are 8, 16 and 20 ns. Check a
rejection exactly at completion, within `stop`, and before
any issue. Keep the raw Q1 rejection reason and link.

## EN. Open overlapping NetZero evidence in standalone Normal

**Reproduction.** In the EF example with an RT wait after
zero-duration `play_netzero`, Normal draws the RT command
at aligned 8 ns, its signal result from 8 to 17 ns, and the
wait from 12 ns. The result covers the full command hit area.
The wait covers the middle of the result. Debug can select
both from their separate rows.

**Required behavior.** Keep the originating RT command
selectable in Normal when its own result overlaps it. Offer
exact inspection actions from that command to the retained
results for each output, using their recorded source event
and packet identity. Keep both time extents and the active
comparison path. Preserve diagnostic navigation and the
selection of adjacent RT commands.

**Acceptance.** Normal can select `play_netzero` and open its
result even when a following wait covers part of the signal
span. Exercise narrow geometry, a completed result without a
diagnostic, multiple outputs, and Compare Both. Debug keeps
direct selection of both events.

## EO. Keep pending setters out of the standalone RT lane

**Reproduction.** Simulate a sequencer starting with
`set_awg_offs 16384,0` and `upd_param 8` alongside a peer
that runs for 60 µs. The standalone Normal plot starts at
0 ns, but still draws the Q1 pending setter before that
edge. Its six-unit minimum rectangle extends over the
first RT command and can receive its click.

**Required behavior.** In standalone Simulation with
separate mode geometry, omit raw pending setter rectangles
from the Normal RT lanes, including comparison lanes.
Keep embedded evidence, Debug Q1 and pending state, and
explicit Normal controls. Applied values remain on their
committing RT commands. Preserve diagnostics and legacy
single-geometry switching.

**Acceptance.** The long-peer fixture has no pending
setter hit target over the Normal 0 ns edge. The original
trigger example preserves its 0 ns axis, branch controls
and applied `set_freq` selection. Debug still exposes Q1
provenance. Check an unapplied setter and Compare Both.

## EP. Keep pre-origin arrivals outside standalone Normal

**Reproduction.** At startup 100 ns, simulate `wait 12`
and `stop` with a 60 µs peer and trigger addresses 2 and 1
at local 99 and 100 ns. The Normal ruler begins at 0 ns.
Its SVG still renders address 2 at aligned −1 ns, where
the hit area overlaps the actual address 1 marker at 0 ns.
Debug legitimately shows both arrival events.

**Required behavior.** In standalone Normal at an effective
first-RT origin, exclude wholly pre-origin RT events from
the plotted viewport and hit targets. Preserve the in-domain
portion of an overlapping interval, the exact zero boundary,
and the original evidence in TimelineIR and Debug. Apply the
same visibility to connector endpoints and related navigation.
Keep resolved explicit anchors that intentionally include
negative RT events and existing Local views.

**Acceptance.** Normal retains only address 1 on the 0 ns
edge. Debug retains address 2 at −1 ns and address 1 at 0 ns.
Exercise Normal→Debug→Normal, Compare Both, a later explicit
anchor, and a pre-origin diagnostic or connector.

## EQ. Show the Play Q1 observation end in Hardware

**Reproduction.** Play `wait 100`, `move 3,R0`, `nop`,
`add R0,1,R1`, and `stop` with two arrivals at local Q1
16 ns, capacity one, and alignment −4 ns. The rejection
is at aligned 12 ns. The Hardware execution heading shows
the raw Q1 coordinate, and the Q1 state at a later cursor
looks like an ordinary last command. The analytical RT
wait still spans the later cursor.

**Required behavior.** Publish the recorded Play rejection
as an observation end with `execution[seq].terminal.core`
set to `q1`. Preserve the
raw Play reason and the analytical RT schedule. Reuse the
Hardware terminal presentation with the active time basis,
and change Q1 state only. Preserve completed Q1 commands,
known register history, rejection navigation and old traces.

**Acceptance.** At aligned 11 ns Q1 is active. At 12 ns
and later it reports that Q1 observation ended. The RT wait
remains active at 75 ns. Local mode shows 16 ns. Check a
rejection before the first issue, one within `stop`, and a
successful run.

## ER. Show the recorded branch choice in standalone inspection

**Reproduction.** Open a generated standalone HTML trace
of the trigger example, switch to Debug and select the
`set_cond` Q1 issue. The inspector offers Execute, Skip and
Clear condition override buttons. They dispatch a branch
assumption event that the standalone viewer does not consume.
Clicking leaves the recorded timeline unchanged.

**Required behavior.** Present the recorded branch condition
and selected or compared paths as inspection information in
standalone HTML. Keep generated Compare Both rows and source
selection. Execution override actions belong to the VS Code
host that handles them. Preserve the host's Execute, Skip
and Compare Both controls.

**Acceptance.** A standalone recorded trace identifies its
condition and current path. A generated Compare Both trace
still exposes Execute and Skip rows. Check a Q1 status branch
and verify the same source in VS Code retains actionable
branch controls.

## ES. Keep distinct digital bin histories in Hardware

**Reproduction.** Simulate `acquire_timetags 0,0,1,0,8`,
`acquire_timetags 0,0,0,0,4`, `acquire_digital 0,0,4`, and
`stop` with startup 100 ns, a bin definition and one input
edge during the window. The runtime exports separate timetag
and digital-level bins. Hardware combines their target
histories and places the timetag aggregate next to the latest
digital-level result under one row.

**Required behavior.** Use the modeled acquisition kind as
part of the Hardware identity for explicitly typed digital
targets and results. Keep each type's latest outcome, target
history and aggregate together, and identify their kind in
the view. Preserve analog weighted/unweighted grouping and
untyped legacy traces. Keep selection and source links tied
to the chosen row when acquisition and bin numbers repeat.

**Acceptance.** At the second result the same numeric
acquisition and bin appear as distinct timetag and digital
level rows. The timetag row shows count 1 and its aggregate.
The digital row shows level 0. Check a third kind, a saved
legacy selection and an analog control.

## ET. Show the bins written by a TTL gate

**Reproduction.** At startup 100 ns, run
`acquire_ttl 0,2,1,4`, `wait 16`, `acquire_ttl 0,7,0,4`
and `stop` with TTL auto-increment and edges at 104 and
116 ns. The model writes bins 2 and 3 at gate close.
Hardware shows bins 2 and 7 and omits the observed bin 3.

**Required behavior.** In Hardware, derive typed TTL target
history from the enable command and use the disable command
as close evidence. Show valid observed auto-increment writes
even when their bin index had no explicit target command.
Use the accepted result's time and source for those rows.
Keep command-target counts separate from observed results,
and preserve older traces without sufficient gate operands.

**Acceptance.** Before close only bin 2 is a target. After
close, both bins 2 and 3 show count 1, with source links to
their gate evidence. No target is created for the ignored
disable bin 7. Check a fixed-bin gate, an empty or incomplete
gate, and a later out-of-bounds write after an accepted one.

## EU. Preserve the first Hardware click after a cursor edit

**Reproduction.** Open Hardware Bins on the DZ trace. Enter
364 ns in the local cursor field and press Enter. Then click
bin 0. The number field fires a `change` while losing focus.
The cursor is already committed at 364 ns, but the handler
rerenders the table between pointer-down and click. The
original button is replaced and the first click is lost.

**Required behavior.** Avoid rerendering Hardware for a
redundant commit of the already active cursor time. Keep
validation and normalization, changed cursor movement,
focus behavior and replay position. Preserve the target of
the first mouse or keyboard action after a committed edit.

**Acceptance.** The first bin click after Enter opens its
history. Check the unchanged blur path, a genuinely changed
time, invalid input, a register target and the scrubber.

## EV. Use the QTM close target in Hardware Bins

**Reproduction.** At startup 100 ns, open a window with
`acquire_timetags 0,7,1,0,20` and close it with
`acquire_timetags 0,2,0,0,4`. One edge at 104 ns writes
bin 2 at close. Hardware creates an empty target row for
bin 7 and keeps it beside the actual destination.

**Required behavior.** For typed QTM gate events, use the
close command's bin as the target. Preserve the open command
as acquisition-window evidence and keep its raw operands.
Show the target from the close command's RT execution time,
then show a result only at its observed effective close.
Retain results and history for the typed bin identity and
preserve older traces without gate operands.

**Acceptance.** The example creates only target bin 2.
With a nonzero close delay, its row appears before the result
and updates at the effective tick. Check a failed close and
an unapplied conditional close.

## EW. Show TTL totals at the Hardware cursor

**Reproduction.** Run two fixed-bin TTL gates targeting
bin 2 with counts 2 and 1. The simulator's final digital bin
holds total count 3. Hardware shows only the latest gate's
count 1 at the second result.

**Required behavior.** Attach the bin total after each accepted
TTL result to its recorded event and summary. In Hardware,
show the total observed at or before the cursor beside the
latest gate result, keeping the separate typed bin identity.
Preserve incomplete or unavailable gate results without
changing the last accepted total. The same rule applies to
accepted auto-increment writes.

**Acceptance.** The first close displays total 2. The second
displays total 3 and latest count 1. Check an empty gate,
separate bins, auto-increment and a later incomplete gate.

## EX. Show modeled I/Q bin averages at the Hardware cursor

**Reproduction.** Simulate two `acquire 0,0,8` commands
at startup 100 ns with 8 ns integration and an input trace
of I = 0.25 for the first window and I = 0.5 for the second.
The windows report I integrals 2 and 4. The numerical bin
has mean I 3 after two writes. Hardware only shows the most
recent window integral.

**Required behavior.** Record the numerical bin snapshot
after each modeled I/Q result. Show its accepted window
count, mean integrals and threshold count at the cursor,
separately from the latest window result. Retain the
integral units. Use the numerical model's existing bin
arithmetic and keep scenario outcomes as per-window values.
Preserve weighted and unweighted writes to the same modeled
bin and older traces without aggregate history.

**Acceptance.** With threshold 3.5, the first result shows
mean I 2 and threshold count 0. After the second, mean I is
3, threshold count 1 and latest I is 4. Check a cursor before
the first result, weighted input, a scenario override and
an unavailable integration window.

## EY. Show recorded unavailable I/Q outcomes at the Hardware cursor

**Reproduction.** At startup 100 ns, simulate two
`acquire 0,0,4` commands to bin 0 followed by `stop`.
Supply one scenario result with I = 17, Q = −9 and
thresholded bit 0. Configure a route for thresholded bit 1
to a peer with `wait 24`. The first result arrives at aligned
4 ns. The runtime records an `acquisition_unavailable` event
at aligned 8 ns and ends observation there. Hardware still
shows I = 17 as the latest bin outcome at 8 ns.

**Required behavior.** Include recorded unavailable
acquisition evidence in Hardware's outcome history and
simulation event list. At its event time, show its status
and recorded reason with the existing source navigation.
Keep earlier successful outcomes in their cursor interval
and carry the last accepted numerical aggregate, when
available, through the unavailable outcome. Preserve
the terminal edge, command counts and trigger uncertainty.

**Acceptance.** Aligned 7 ns still shows the first result.
At 8 ns the row shows the unavailable result with its reason
and observed time. Local mode shows 108 ns. Verify the
linked acquisition instruction and an older trace that
only records a terminal diagnostic.

## EZ. Open standalone Q1 evidence from the keyboard

**Reproduction.** Generate standalone HTML for
`cmp R0,R0`, `jz @end`, `nop`, and `end: stop` and
switch to Debug. Both the Q1 issue and the Q1 branch
marker accept focus. Enter and Space leave the inspector
unchanged on either target. Clicking the branch hitbox
shows the `jz status flags` condition and the Jump path.

**Required behavior.** Make the selectable Q1 issue and
branch marker activate with Enter and Space in standalone
Debug. Give them accessible button semantics and reuse the
pointer selection path, including its event identity and
inspector. Retain existing mode visibility and pointer hit
areas.

**Acceptance.** Tab to each of the two targets. On each,
Enter and Space select the same event as a pointer click.
The branch inspector still reports Jump. Recheck trigger
arrival and timing diagnostic keyboard activation, which
already use button semantics.

## FA. Follow effective digital acquisition windows in Hardware

**Reproduction.** At startup 100 ns, simulate
`acquire_ttl 0,2,1,4`, `wait 16`,
`acquire_ttl 0,7,0,4`, and `stop`. The result records an
open gate from local 100 through 120 ns. At aligned 10 ns
Hardware reports the last acquire command. At 20 ns,
when the gate closes, it instead shows an active acquisition
using the close packet's 4 ns duration. A QTM open with
delay 1 tick and close with delay 3 ticks has the same
problem and should start and end at fractional times.
A digital level sample is recorded at a single tick, while
Hardware shows it as integrating for its whole RT packet.

**Required behavior.** Record a digital gate's observed
start and end on its effective open, close or terminal
cleanup, linked to the executed opener. Retain an observed
close even if the destination bin rejects the write. In
Hardware, derive gate activity from that half-open interval
and label it as a gate. A digital level sample is an outcome
at its recorded tick. Keep RT packet geometry and target
counts, analog integration, independent scope and older
traces with no gate evidence. A skipped or refused opener
does not establish an active gate.

**Acceptance.** The TTL gate is active at aligned 4 and
10 ns, closes at 20 ns and links to the opener while active.
The QTM gate is active from aligned 1/128 through
20 + 3/128 ns with the end excluded. Check Local basis,
a close rejected for its bin, an incomplete gate with a
longer-running peer, a skipped open, and a 20 ns
`acquire_digital` packet whose level is observed at 0 ns.
Compare Both retains the shared opener and its input links
when the two paths differ only at the close.

## FB. Navigate the recorded loop preview in standalone HTML

**Reproduction.** Analyze `move 3,R0`, `nop`,
`shot: acquire 0,0,40`, `wait 960`, `loop R0,@shot`,
and `stop`. Generate standalone HTML with the default
one-iteration preview. The loop inspector reports 1/3 and
offers Show next iteration. That button dispatches an event
with no standalone listener. Open first iteration resolves
the first recorded ID, a Q1 issue hidden in Normal, even
though the following RT acquire is visible there.

**Required behavior.** In standalone HTML, report the
number of generated iterations and navigate to the first
visible recorded event in the first iteration. Offer that
action only when it has a visible target in the active mode.
Resolve the target again when Normal or Debug changes.
Keep additional preview generation in the CLI and VS Code,
which have access to the analyzer. Preserve the generated
loop timing, iteration count and selection identity.

**Acceptance.** In Normal, Open first iteration selects
the RT acquire. In Debug it selects the first Q1 issue.
The standalone inspector reports 1/3 without an inert
expansion button. A trace generated with two iterations
reports 2/3. VS Code still requests the next iteration and
can show it after reanalysis.

## FC. Follow bounded concrete back edges in Analysis

**Reproduction.** Analyze `move 3,R0`, `nop`,
`body: play 0,1,100`, `sub R0,1,R0`, `nop`,
`jnz @body`, and `stop`. Analysis proves the first Jump
but replaces all execution after 100 ns with an unknown
region. Concrete follows the Jump twice and falls through
with R0 = 0. It records three 100 ns plays and a stop at
300 ns. Simulation produces the same RT spans relative
to its first RT edge.

**Required behavior.** In Analysis, follow a bounded
concrete conditional back edge when the target is a resolved
instruction entry and the predicate can be evaluated on
each observed visit. Use the existing analytical instruction
and register readiness rules. Preserve each source branch's
own Q1 duration and RT provenance. Keep ordinary compact
`loop` handling, explicit branch choices and the unknown
fallback. A bound or unresolved predicate ends observation
at the last known edge with an explanation.

**Acceptance.** The example reaches stop at 300 ns.
Its Q1 issue trace records Jump, Jump, Continue with
16, 16 and 4 ns branch durations, and R0 ends at 0.
Check `jnz 2`, the resolved compiled entry for the same
body. In a loop with `cmp R0,1`, `fb_pull_data R1,R0`,
`nop` and `jz @body`, the first decision can be concrete
and the next one unknown. A `cmp R0,1` loop with constant
R0 = 1 exercises the analysis bound.
Retain the existing behavior of a `loop` macro and the
ordinary backward branch comparison availability.

## FD. Record missing I/Q outcomes without a trigger route

**Reproduction.** Run the two acquisitions in EY with
the same first scenario result and remove the trigger
route. The second window completes at local 108 ns,
aligned 8 ns. Its diagnostic says an input is required,
but contains no related event. Hardware continues to
show the first window's I = 17 as the latest outcome.

**Required behavior.** When an analog integration reaches
its effective end and has no physical or scenario result,
record the existing `acquisition_unavailable` evidence
independently of trigger routing. Link the terminal
diagnostic to the recorded event, with a retained source
anchor for compact traces. Preserve successful results,
bin aggregates and routed threshold uncertainty. An
integration cut off before its effective end remains
incomplete and produces no accepted result.

**Acceptance.** Without a route, Hardware still shows
I = 17 at aligned 7 ns and the unavailable outcome at
8 ns. Its link selects the second instruction and its
local timestamp is 108 ns. Verify the terminal edge,
successful history, compact/prepared execution, and
the existing routed and incomplete controls.

## FE. Open standalone RT and loop evidence from the keyboard

**Reproduction.** Generate the standalone loop in FB.
Its RT acquire and both loop brackets are focusable. The
inspector opens on a pointer click. Enter and Space on
those focused elements leave the current selection in place.

**Required behavior.** Give selectable standalone RT
blocks and loop brackets accessible button semantics.
Use the existing Enter and Space activation and preserve
the exact event identity used by pointer selection, including
either bracket's link to the same loop. Keep the active
mode, filtering, and visible hit areas.

**Acceptance.** In Normal, use Tab then Enter and Space
on the RT acquire and each bracket. The acquire selects
its source event, and either bracket opens the loop
inspector and its recorded preview action. Check a hidden
or filtered event and the Q1 and trigger controls already
covered by EZ.

## FF. Show recorded RT decisions for each set_cond

**Reproduction.** At startup 100 ns, run
`set_latch_en 1,4`, `set_cond 1,1,0,12`, two
`upd_param 12` commands, `set_cond 0,0,0,4`, and `stop`.
Supply trigger address 1 at local 110 ns. The first
update is skipped at aligned 4 ns and the second
executes at aligned 16 ns. Both records link to the
same Q1 `set_cond` issue. Standalone inspection shows
only Recorded trigger input. The VS Code condition
control also omits the recorded outcomes.

**Required behavior.** When inspecting a simulation
`set_cond` occurrence, show the observed Execute and
Skip decisions for RT packets linked to that exact issue.
Use the active time basis and link to the retained packet
evidence. Count one decision per packet, including
multi-path RT commands. Keep generated Compare Both
paths and forced assumptions distinct from the observed
trigger-input result. Preserve the existing VS Code
branch controls and standalone read-only inspection.

**Acceptance.** The example shows Skip at 4 ns and
Execute at 16 ns, with links to their respective updates.
Local times are 104 and 116 ns. Check a repeated
`set_cond` source, a two-path `play` packet, a condition
that ends before any decision can be observed, and
Compare Both with separate hypothetical paths.

## FG. Show the Q1 boundary when Play cannot continue

**Reproduction.** Play `set_latch_en 1,4`, `wait 100`,
`set_cond 1,1,0,4`, `upd_param 4`, and `stop` without a
trigger source, with alignment −4 ns. Play blocks before
issuing the update, at local Q1 12 ns and aligned 8 ns.
The analytical RT wait remains active until aligned 100 ns.
At and after the Q1 boundary Hardware still shows an
ordinary last command.

**Required behavior.** When Play ends at a blocked or error
boundary with a known Q1 coordinate, publish that coordinate
as the affected sequencer's observation end. Retain the
blocking source and reason, the recorded Q1 and register
evidence, and the existing analytical RT schedule and
unknown region. Preserve specialized terminal evidence
such as LINQ rejection. A paused run or a live external
input wait keeps its existing status.

**Acceptance.** Q1 is active at aligned 7 ns and its
observation ends at 8 ns. At 20 ns the RT wait remains
active. Local mode reports the Q1 edge at 12 ns. Check
an invalid first `wait 2`, an external input wait, a
normal stop, an independent sequencer and the existing
LINQ rejection. An executed `illegal` retains its fault
flag and exposes the recorded Q1 issue completion edge.

## FH. Show live Play waits on their respective cores

**Reproduction.** Play `wait 12`, `wait_trigger 1,4`, and
`stop` with alignment −4 ns and no trigger source. The
session reports waiting and the trace records the armed
RT wait at aligned 8 ns. Hardware still reports the
previous RT command. Play `wait 100`, `fb_pull_data R0,R1`,
and `stop` with an empty scheduled LINQ source and the
same alignment. The feedback wait is recorded at aligned
Q1 0 ns, but Hardware shows the previous Q1 command.

**Required behavior.** Use existing Play wait evidence to
show an armed trigger wait in RT state and an armed LINQ
wait in Q1 state. Apply the active time basis and preserve
the link to the waiting instruction. Keep both clocks
independent and distinguish a live wait from a blocked
observation end. Preserve supplied-input runs and
Simulation's existing wait states.

**Acceptance.** At aligned 8 ns, the first RT state
indicates an armed trigger wait. In the second run, Q1
shows the LINQ wait from aligned 0 ns onward while
the RT wait remains active at 20 ns. Check Local mode,
a later supplied arrival and replay after changing
external inputs.

## FI. Scope compact loop evidence to its analytical visit

**Reproduction.** Run Analysis on `move 2,R0`, `nop`,
`body: move 2,R1`, `inner: wait 4`, `loop R1,@inner`,
`sub R0,1,R0`, `nop`, `jnz @body`, and `stop`.
The first compact loop spans 0–8 ns. After the outer
branch jumps back, Analysis records the first wait of
the second visit at 8 ns and stops at 12 ns with
`analytical_revisited_compact_loop`. Concrete executes
four waits and stops at 16 ns.

**Required behavior.** When a bounded concrete back edge
revisits a compact loop, use only the current visit's
first-iteration packets, issue events and input state
as the basis for its summary. Keep previous visits and
their provenance independent. Continue the analytical
path when the current visit can be summarized, and
retain a precise incomplete boundary when it cannot.

**Acceptance.** The example records separate two-iteration
inner summaries for 0–8 ns and 8–16 ns, then stops at
16 ns with R0 = R1 = 0. Each summary links to the first
iteration from its own visit. Check changed inner-loop
counts between visits, expanded previews, a compact
loop outside the repeated region and the FC repeat bound.

## FJ. Keep Play failure scoped to the affected sequencer

**Reproduction.** Use the blocked sequencer from FG and a
peer with `wait 1000`, ten `nop` instructions, and `stop`.
Play ends when the first sequencer lacks trigger input.
The peer has recorded its 1000 ns RT wait and two Q1
NOPs. The project and the affected sequencer report
blocked with a reason. The peer also reports blocked,
despite having no local blocker or diagnostic.

**Required behavior.** Keep project Play status separate
from the status of each recorded sequencer. When a local
block or error stops project observation, preserve other
sequencers' completed or waiting states. Present an
unfinished peer as a partial recording with its existing
events and state. Use a terminal boundary only where
the trace establishes a local cause.

**Acceptance.** The project and the affected sequencer
still report blocked, and Hardware for the peer describes
its partial recording. The peer's RT wait remains active
at 20 ns. Check a first-instruction error, a completed
peer, a peer waiting for external input, and ordinary
pause and resume.

## FK. Compact loops with loaded numeric and register targets

**Reproduction.** Analyze `move 3,R0`, `nop`,
`wait 100`, `loop R0,2`, and `stop`. The target is
the loaded address of the wait. Analysis records one
wait and marks the rest unknown at 100 ns. The same
body with a label has a three-iteration summary and a
stop at 300 ns. With `move 3,R1` before the `nop`,
`loop R0,R1` reproduces the gap at the shifted address.

**Required behavior.** Apply compact loop analysis when
a numeric or ready register target resolves to a loaded
backward instruction entry. Use the actual target
address and the observed iteration to calculate the
same timing and post-loop state as the label form.
Preserve provenance and preview identity. Keep existing
fallbacks for unknown addresses, unreadable registers,
and unsupported targets inside a lowered macro.

**Acceptance.** Both examples produce three iterations,
R0 = 0, and a stop at 300 ns. The first iteration links to
the wait at the resolved entry. Check a preceding macro
that shifts the compiled address, a counter of one, a
target register still awaiting a write, and an existing
label-based compact loop.

## FL. Recognize stable numeric jump loops in Analysis

**Reproduction.** Analyze `wait 100` followed by `jmp 0`.
The loaded entry and a complete first period are known,
but Analysis marks the tail unknown. The equivalent
`body: wait 100` and `jmp @body` produces a forever
loop summary. `move 1,R27`, `wait 100`, and `jmp R27`
also stops analysis after the first wait even though the
target register is ready and untouched by the loop.

**Required behavior.** Apply the existing indefinite
loop proof and summary to an unconditional numeric
jump to a loaded, previously visited entry. For a
register target, require a ready value and proof that
the body preserves the target on future iterations.
Retain the existing first-period fallback where timing
or target stability is unresolved. Preserve ordinary
forward calls and returns to unvisited backward entries.

**Acceptance.** The three examples each expose the
observed 100 ns period and a forever summary with no
stop event. Check an overwritten target register, a
pending target write, an unloaded or interior address,
a subroutine return and a period with unresolved timing.

## FM. Compose nested compact loops on a later analytical visit

**Reproduction.** Start with `move 2,R0` and `nop`.
At `outer`, set R1 to 2. At `middle`, set R2 to 2.
At `inner`, run `wait 4`, `loop R2,@inner`, and
`loop R1,@middle`. Finish with `sub R0,1,R0`, `nop`,
`jnz @outer`, and `stop`. The first middle-loop visit
consumes 16 ns. Analysis stops at 24 ns on the second
visit after summarizing only the inner loop. Concrete
executes eight waits and stops at 32 ns.

**Required behavior.** When a revisited compact loop's
observed body contains a compact child, compose the
current visit's proven child summary into the parent
using the existing nested-loop rules. Retain separate
event identities and count each child's elapsed work
once. Keep the incomplete boundary if the child's
path, duration or state cannot support a parent summary.

**Acceptance.** Each middle visit reports two iterations
of period 8 ns, using its own child summary. Analysis
stops at 32 ns with R0, R1 and R2 equal to zero.
Check a changed child count between visits, expanded
preview references and a branch that bypasses a child
header on a later visit.

## FN. Keep the recorded condition decision when RT arms a wait

**Reproduction.** At startup 100 ns, simulate
`set_cond 1,0,1,4`, `wait_trigger 3,4`, and `stop`
without a trigger. The condition permits the wait.
RT records its arm at local 100 ns and aligned 0 ns,
then blocks with no matching arrival. The pending wait
lacks the link to the condition issue, so its recorded
decision is absent from both inspectors.

**Required behavior.** When a conditional RT packet
arms a trigger or SYNQ wait, retain the decided Execute
path and exact condition and packet links on the wait
evidence. The condition inspector can show the arm
decision while the wait is pending or cut short. Preserve
the separate arrival, barrier release, and completion
times. A completed wait still contributes one decision.

**Acceptance.** Both viewers show Execute at aligned
0 ns and link to the pending trigger wait, with local
100 ns. Check a wait at a SYNQ barrier with a missing
peer, a completed wait, a Skip decision, and a condition
whose outcome is unresolved before arming.

## FO. Keep Q1-only loop summaries out of the Normal RT view

**Reproduction.** Analyze `move 3,R0`, `nop`,
`body: nop`, `loop R0,@body`, `wait 12`, and `stop`.
The loop has no RT packet and the first RT wait starts
at 0 ns. Both viewers include a selectable loop
annotation at that same RT coordinate in Normal.

**Required behavior.** Exclude a loop proven to contain
only Q1 work from Normal RT layout, hit targets and
viewport calculations. Preserve its recorded evidence
and inspection in Debug. Use the presence of RT work
to classify it, including a real zero-duration RT packet.
Retain ambiguous older summaries where the trace cannot
establish the loop's core.

**Acceptance.** Normal starts with the 12 ns RT wait at
0 ns and contains no loop annotation or Q1 time region.
Debug retains the three-iteration loop. Replacing the
body with `wait 0` keeps its RT loop in Normal. Check
mode switching, source selection, the same two bodies
in Play, and a Q1-only project.

## FP. Preserve proven Hardware state across compact loops

**Reproduction.** Analyze `move 3,R0`, `move 77,R5`,
`set_freq 4000000`, `upd_param 4`, `body: wait 100`,
`loop R0,@body`, and `stop`. The compact summary
lists R0 as uncertain and has no uncertain latched
fields. Hardware changes R5 to unknown at Q1 44 ns
and the applied frequency to unknown at RT 104 ns.
The loop never writes either value.

**Required behavior.** Use a compact loop's explicit
register and latched-field evidence to scope uncertainty
in Hardware. Keep proven register, pending parameter and
applied parameter values until a later event changes them.
Retain the loop's core-state and acquisition-count warnings
and generic uncertainty for unknown regions. Legacy loop
records without field evidence keep the conservative
fallback.

**Acceptance.** R5 stays 77 and the applied frequency
stays 4000000 in the example. R0 is unknown during
hidden writes and becomes 0 when its final write is
readable. Check a genuinely uncertain latched field,
a later applied value, both time bases and older traces.

## FQ. Use the Q1 boundary of each compact loop visit

**Reproduction.** Use the two-visit compact loop in FI.
The first Q1 loop issue ends at local 40 ns. The
second ends at 112 ns, with the next Q1 issue at
120 ns. Hardware marks Q1 unknown from 40 ns through
the first hidden interval. From 112 ns through the
second interval it reports the last command instead.
Both summaries currently use the first source match
for the loop boundary.

**Required behavior.** Resolve a compact summary's Q1
boundary from its exact `loop_q1_issue_event_id` when
available. Keep each visit's uncertainty tied to its own
edge in the active time basis. When an older trace omits
the exact link, use evidence from the same summary or an
unambiguous source occurrence.

**Acceptance.** Q1 is active at local 111 ns, unknown
from 112 through 119 ns, and active again at 120 ns.
The first hidden interval remains at 40–48 ns. Check
a manual offset, an expanded preview and older trace
fallback.

## FR. Use the exact Q1 issue for an unresolved branch

**Reproduction.** Use FC's branch that reads runtime
feedback on its second visit. With a 4 ns body wait,
the first `jz` ends at local Q1 52 ns and jumps to the
body. The second ends at 96 ns with an unknown outcome.
Its unresolved branch region is linked by source only.
Hardware reports Q1 unknown at 52 ns and reports the
last command at 96 ns, reversing the two boundaries.

**Required behavior.** Link an unresolved branch region
to the exact Q1 issue that produced it. Hardware uses
that issue's end in the active time basis. Preserve the
RT unknown-region boundary and the earlier completed
branch and register history. For an older trace, use
an unambiguous source match when available.

**Acceptance.** Q1 remains active as the first visit
continues at 52 ns. The second issue is active at 95 ns
and Q1 becomes unknown at 96 ns. Check aligned and Local
views, a repeated source, a single unresolved branch,
and legacy traces with and without a unique source match.
With a symbolic RT duration before an unresolved branch,
keep its known Q1 edge even when RT placement is unavailable.

## FS. Account for changing child counts in compact loop timing

**Reproduction.** Analyze `move 3,R1`, `nop`,
`middle: move R1,R2`, `inner: wait 4`, `loop R2,@inner`,
`loop R1,@middle`, and `stop`. The child counts are
3, 2, and 1. The old compact calculation repeats the
first child's 12 ns contribution three times and gives
an exact-looking stop at 36 ns. Concrete stops at
RT 24 ns and issues Q1 stop from 180 to 184 ns.

**Required behavior.** For a compact nested loop with
a proven affine child count, account for each count
in the parent elapsed RT and Q1 work. Preserve the
actual first child summary and the parent's varying
period, counter writes and register availability.
When the child count or period cannot be proven,
retain the observed prefix and an incomplete boundary.

**Acceptance.** The reproduction shows varying RT
periods of 12, 8, and 4 ns, a total of 24 ns, and R1
and R2 readable as zero at completion. The next Q1
issue starts at 180 ns. Replace `move R1,R2` with
`add R1,1,R2` followed by `nop` to exercise child counts
4, 3, and 2. This variant currently reports 48 ns in
Analysis and should match Concrete at RT 36 ns and
Q1 stop issue 300 ns. Check an
invariant child count, expanded previews and an unresolved
count. If an unreset child begins a later visit at zero,
retain the known parent edge and leave final state unresolved.

## FT. Distinguish a compact loop preview from its total RT duration

**Reproduction.** Analyze `move 3,R0`, `move 4,R1`,
`nop`, `body: wait R1`, `add R1,4,R1`, `nop`,
`loop R0,@body`, and `stop`. RT iteration lengths
are 4, 8, and 12 ns. The compact block spans 0–4 ns
and records a proven total duration of 24 ns. Both
inspectors show the preview range without that total.

**Required behavior.** In compact loop inspection,
identify the displayed span as the preview and show
the proven full RT duration separately. Distinguish
a varying period from an invariant period. Preserve
the compact geometry and preview actions. When the
total is unresolved, keep its uncertainty rather than
extrapolating from the first iteration.

**Acceptance.** Both viewers identify the 0–4 ns
preview, show 24 ns total RT duration, and indicate
the varying period. Check a constant period, expanded
preview, unresolved total, and a fully observed loop
whose range already covers every iteration.

## FU. Propagate changing child dwell through a compact parent loop

**Reproduction.** Start with `move 3,R1`, `move 4,R3`,
and `nop`. At `middle`, set R2 to 2. At `inner`, run
`wait R3` and `loop R2,@inner`. Then run `add R3,4,R3`,
`nop`, `loop R1,@middle`, and `stop`. Analysis knows
the first 4 ns child dwell and the Q1 stop at 232 ns,
but leaves the RT stop and final R3 unresolved.
Concrete observes parent periods 8, 16, and 24 ns,
stops at RT 48 ns, and ends with R3 equal to 16.

**Required behavior.** When a proven parent recurrence
changes the dwell of a finite child whose count is
invariant, carry the child timing contribution through
the parent summary. Keep the observed first iteration,
the varying parent periods and the independently
calculated Q1 work. Preserve the final affine register
value and its readable edge. Keep an incomplete or
symbolic result when the required dwell range or
recurrence cannot be proven.

**Acceptance.** The example reports RT 48 ns, Q1
stop issue 232 ns, and final R3 = 16. Check an invariant
child dwell, a legal decreasing dwell, a duration that
becomes invalid on a later visit, and expanded preview
provenance. When both child count and dwell vary, retain
the observed prefix unless their joint timing is proven.

## FV. Compose invariant latched updates with changing child counts

**Reproduction.** Start with `move 3,R1` and `nop`.
At `middle`, run `move R1,R2`. At `inner`, run
`set_freq 4000000`, `upd_param 4`, and `loop R2,@inner`.
Finish with `loop R1,@middle` and `stop`. Every child
commit applies the same frequency. Analysis stops its
proof at RT 12 ns. Concrete records six commits, stops
at RT 24 ns and issues Q1 stop at 204 ns.

**Required behavior.** Compose the proven fixed Q1 and
RT work of an invariant latched setter and commit with
an affine child count. Use existing setter and packet
provenance, including the observed first iteration
and the final latched state. Preserve the incomplete
boundary for unsupported changing effects or one-shot
effects whose repeated state cannot be proven.

**Acceptance.** The example reaches RT 24 ns and Q1
stop issue 204 ns, with R1 and R2 zero and frequency
4000000 applied. Check a constant marker update,
fixed count, a setter value that changes across parent
iterations, and a one-shot phase operation.

## FW. Show a Q1 proof cutoff at the linked issue boundary

**Reproduction.** Analyze `move 2,R0`, `move 2,R1`,
`nop`, `outer: nop`, `inner: nop`, `loop R1,@inner`,
`loop R0,@outer`, and `stop`. The child count reaches
zero and has no reset for the next visit. Analysis
records the first outer decrement and an unknown region
linked to that Q1 issue, which ends at 76 ns. Hardware
shows the last Q1 command from 76 ns onward.

**Required behavior.** Mark analytical cutoffs that
end Q1 proof as such in the IR. Hardware uses the
linked issue's Q1 end as the start of uncertainty,
independently of the RT region coordinate. Keep the
known issue and its register evidence available. Preserve
RT-only unknown regions and the existing RT schedule.

**Acceptance.** Q1 is active at 75 ns and unknown at
76 ns. The RT boundary stays at 0 ns for this Q1-only
example. Check a repeated-branch analysis limit, Local
and aligned coordinates, and an RT-only uncertainty
with Q1 work continuing.

## FX. Distinguish unbounded RT loops from unresolved totals

**Reproduction.** Analyze `wait 100` and `jmp 0`. The
compact summary proves an invariant 100 ns period
and records a forever loop. Both inspectors show the
total RT duration as Unknown because there is no
finite total in the IR.

**Required behavior.** Present an exact invariant forever
loop with a positive RT period as unbounded RT duration.
Keep its finite preview range and period. A proven Q1-only
forever loop has no RT work and remains inspectable in
Debug. Preserve Unknown for an unproved period or path,
and keep the separate confidence or assumption display.

**Acceptance.** `wait 100` and `jmp 0` show an unbounded
total and invariant 100 ns period in both viewers.
Check the equivalent label, a Q1-only forever loop,
and a forever preview whose register-dependent dwell
cannot be proven invariant.

## FY. Compose jointly changing child counts and dwell

**Reproduction.** Use FU's loop, with `move R1,R2` in
place of the fixed child count. The child counts are
3, 2, and 1 while its dwell is 4, 8, and 12 ns. Analysis
stops its proof after the first 12 ns. Concrete records
parent periods of 12, 16, and 12 ns, reaches RT 40 ns,
and issues Q1 stop at 232 ns.

**Required behavior.** When both child count and dwell
have a proven parent recurrence, combine them for each
parent period and sum the RT contribution independently
of Q1 elapsed time. Keep the observed first iteration,
the proven total and the varying-period evidence. Use
the existing conservative timing boundary when the joint
proof fails.

**Acceptance.** The example reaches RT 40 ns, Q1 stop
issue 232 ns, R1 = R2 = 0, and R3 = 16. Check a growing
count with decreasing dwell, an invalid later operand,
and a fixed count or dwell. Add `wait 0` to the child
body to check its 4 ns spacing on every visit. That
variant reaches RT 64 ns and Q1 stop issue 256 ns.

## FZ. Keep a partial backward-branch replay visible through its last Q1 issue

**Reproduction.** Analyze `move 1,R0`, `nop`,
`body: wait 4`, `cmp R0,1`, `jz @body`, and `stop`
with a repeat budget of one. The first `jz` ends at
Q1 40 ns. The second `wait` is recorded at Q1 40–44 ns
and RT 4–8 ns. The cutoff currently points to the
first `jz`, so Hardware marks Q1 uncertain from 40 ns.

**Required behavior.** At the repeat limit, link the
cutoff to the last recorded Q1 issue on that path.
Keep the backward branch as the diagnostic source and
keep the RT boundary at the end of recorded RT work.
Exclude preview occurrences from the Q1 boundary.

**Acceptance.** With a repeat budget of one, the
cutoff links to the second `wait`, keeps source line 5
and RT 8 ns, and leaves Q1 active through 43 ns.
Hardware marks Q1 unknown from 44 ns in Local time
and at the corresponding Aligned time. With a repeat
budget of two, the cutoff links to the second `cmp`
ending at 56 ns. With a budget of three, it links to
the second `jz` ending at 72 ns.

## GA. Preserve invariant two-path AWG settings with changing child counts

**Reproduction.** Run `move 3,R1`, `nop`,
`middle: move R1,R2`, `inner: set_awg_gain 16384,-16384`,
`upd_param 4`, `loop R2,@inner`, `loop R1,@middle`, and
`stop`. Analysis ends its proof at RT 12 ns. Concrete
records six commits with the same pair, stops at RT
24 ns, and issues Q1 stop at 204 ns. Replacing the setter
with `set_awg_offs 1024,-1024` gives the same timing.

**Required behavior.** Extend the invariant setter proof
to gain and offset when both path values are ready,
concrete, invariant over the child and parent visits,
and applied by a recorded commit. Keep signed raw values
in path order and retain the link from the observed setter
to the applying RT packet. Keep the conservative boundary
when either path or its commit cannot be proven.

**Acceptance.** Both fixtures reach RT 24 ns and Q1
stop issue 204 ns with R1 and R2 zero and the expected
two-path applied state. Check a ready invariant register
operand, one changing path operand, and the first
observed commit's source and packet links. Keep the
existing scalar setter and one-shot phase controls.

## GB. Include zero-dwell spacing in a changing nested loop

**Reproduction.** Use FU's example with initial R3 = 0.
The child's two waits per parent visit have operand values
0, 4, and 8 ns across the visits. Concrete keeps the zero
waits at RT 0 and 4 ns, with zero-width execution. The
parent periods are 8, 8, and 16 ns. Analysis leaves the
total unknown, although the same wait recurrence in a flat
loop has a proven total.

**Required behavior.** Prove a valid affine operand
sequence that has zero at one endpoint. Add 4 ns of
dequeue spacing per wait at that endpoint, weighted by
the child count for that visit. Preserve zero-width
recorded packets, the first preview and independent Q1
timing. Check the positive value nearest zero against
the allowed dwell range before accepting the total.

**Acceptance.** With a fixed child count of 2, increasing
0, 4, 8 or decreasing 8, 4, 0 reaches RT 32 ns and Q1
stop issue 232 ns. Replacing `move 2,R2` with `move R1,R2`
reaches RT 28 ns and 36 ns respectively, with the same
Q1 stop issue. An increasing dwell of 0, 5, 10 ns with
the changing count reaches RT 32 ns. A sequence of
0, 2, 4 ns stays incomplete because 2 ns is invalid.

## GC. Keep RT idle for a proven Q1-only compact loop

**Reproduction.** Analyze `body: nop` and `jmp @body`
with a manual alignment of −4 ns. Normal correctly hides
the Q1-only loop. In Hardware at local 19 ns, Q1 is active
on `jmp`, RT is Unknown and Bins is marked partial. The
loop has an exact 0 ns RT period and emits no RT packet.

**Required behavior.** Apply the same proof of Q1-only
work used for Normal loop visibility when deciding
whether a compact loop introduces Hardware RT or output
uncertainty. Keep Q1 occurrence and register uncertainty
for hidden iterations. Preserve any earlier RT work,
explicit RT uncertainty and the fallback for traces
without enough evidence to classify the loop.

**Acceptance.** The example shows RT `Not started`
and Bins has no partial flag at local 19 ns and aligned
15 ns. Q1 remains active on `jmp`. Check a Q1-only
latched setter with no commit, an earlier recorded RT
packet and a `wait 0` loop that emits RT packets. The
`wait 0` loop retains its RT and acquisition uncertainty.

## GD. Preserve an affine child count across a fixed 32-bit wrap

**Reproduction.** Start with `move 3,R1`, `move 4,R3`,
`move 0,R4` and `nop`. At `middle`, run `sub R1,4,R2`,
`nop`, `sub R4,R2,R2`, and `nop`. The child is
`inner: wait R3` and `loop R2,@inner`. Then run
`add R3,4,R3`, `nop`, `loop R1,@middle`, and `stop`.
The first subtraction stores 0xffffffff, 0xfffffffe,
and 0xfffffffd. The second produces child counts 1,
2, and 3. Analysis ends at RT 4 ns. Concrete reaches
RT 56 ns and issues Q1 stop at 320 ns.

**Required behavior.** Carry the physical 32-bit word
through the affine child-count proof when an intermediate
expression stays in one modular quotient over the proven
parent range. Validate the final child counts and dwell
after decoding the resulting word. Preserve the observed
register words and the existing cutoff when a required
value or range cannot be proven.

**Acceptance.** The example reaches RT 56 ns and Q1 stop
issue 320 ns with R1 = R2 = 0, R3 = 16, and periods
4, 16, and 36 ns. Check the equivalent direct subtraction
from a ready register containing 4 and a case where a later
child count reaches zero. The latter retains an incomplete
boundary unless its execution is separately proven.

## GE. Describe equal or late-changing periods from exact loop evidence

**Reproduction.** Use FY's loop with `move 2,R1`. The
parent visits have two waits of 4 ns and one wait of 8 ns.
Both periods are 8 ns and the total is 16 ns. Analysis
keeps both exact period samples and omits an invariant
packet layout. The inspectors currently show the period
as Unknown. In a four-visit GB variant, two waits with
operand sequences 12, 8, 4, 0 and 4, 8, 12, 16 give
periods 32, 32, 32, and 40 ns with child count 2. The
first three samples match and the total is 136 ns.

**Required behavior.** Use a complete set of equal exact
period samples to display the invariant RT period. When
the recorded first period times the count differs from
the proven total, display a varying period even if the
sampled prefix is equal. Keep the preview controls based
on packet and layout evidence. Preserve Unknown when the
available samples and total establish neither outcome.

**Acceptance.** Both inspectors show `Invariant · 8 ns`
and total `16 ns` for the two-visit loop. For the four-visit
loop they show `Varying` and total `136 ns`. A partial
uniform sample with a matching total alone leaves the
period unresolved. Both real programs keep their first
preview and their exact downstream `stop` time.

## GF. Decode register frequency and marker values in Analysis

**Reproduction.** Analyze `move -4000000,R1`,
`move 17,R2`, `nop`, `set_freq R1`, `set_mrk R2`,
`upd_param 4`, and `stop`. Analysis latches frequency
4290967296 and marker 17. Concrete and Simulation
latch frequency −4000000 and marker 1. Both register
words are readable before the setters issue.

**Required behavior.** Use the existing signed 32-bit
frequency and low-four-bit marker decoding for concrete
register operands in Analysis. Keep register history and
issue operand snapshots as raw words. Use the decoded
values for pending latches, applied RT state and setter
provenance. A ready symbolic marker register also has a
known mask if all four relevant bits are proven. Preserve
unresolved fields, readiness diagnostics and immediate
validation. [Qblox Q1ASM latched instructions](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#latched-instructions)

**Acceptance.** The example records R1 = 4290967296 and
R2 = 17 while the applying RT packet has frequency
−4000000 and marker 1 with links to both source setters.
Check an equivalent immediate pair, a ready invariant
register pair in a compact loop, and unknown register
values. `and R7,0xfffffff0,R1`, `nop`, `or R1,1,R1`,
`nop`, `set_mrk R1` and `upd_param 4` give marker 1
with a symbolic R1. Removing the final `nop` preserves
the readiness warning and gives no new marker proof.
Concrete and Simulation keep the same values.

## GG. Stop exact latch analysis at unsupported register values

**Reproduction.** Analyze `move 32768,R5`, `move -1,R6`,
`nop`, `set_awg_gain R5,R6`, `upd_param 4`, and `stop`.
Analysis records `[32768, -1]` as the applied AWG gain
and places RT stop at 4 ns. Concrete reports an unsupported
register value at the setter. With the same setter inside
GA's variable-count child, Analysis currently proves an
RT total of 24 ns. `set_awg_offs` with −32769, `set_ph`
with 1000000001 and `set_ph_delta` with a register word
of 0xffffffff show the same effect mismatch.

**Required behavior.** For these setters, use the existing
decoded operand ranges to detect a ready concrete register
value outside Q1Lens's supported latch model. Keep the observed prefix
and link the uncertainty to the setter and register. Leave
the applied field and following exact execution unproven,
including any enclosing loop total. Keep the current range
and readiness behavior for valid values and unresolved
registers. Firmware behavior outside those ranges requires
its own evidence. [Qblox latched instruction ranges](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#latched-instructions)

**Acceptance.** The four unsupported values have a
source-linked incomplete boundary and no exact applied
value or downstream RT stop. Prepend `wait 8` to verify
the recorded RT prefix remains at 0–8 ns. The compact
gain fixture leaves the parent total unresolved. Valid
boundary values, signed frequency words, marker masking,
and valid unknown operands retain their existing paths.

## GH. Expose unsupported LINQ configuration in analytical flow estimates

**Reproduction.** Analyze `wait 8`,
`fb_com_extra 1,0xA1B2,4`, `fb_com_data 1,0x12345678,4`,
`wait 100`, `fb_pop_data 1,R0`, and `stop`. Analysis
reports an exact configuration block and the same 72 ns
self-cast arrival estimate as when extra bytes are disabled.
Simulation blocks at the enabled configuration. The raw
alias `fb_cmd 0x61,0x1A1B2,4` behaves the same way.

**Required behavior.** Report the unsupported LINQ effect
at the configuration's source and carry unresolved word
count, ordering, and arrival into affected analytical FIFO
estimates. Keep nominal RT dwell when proven independently.
Apply the same rule to typed, packed register and verified
raw aliases for enabled extra bytes and unsupported
write-combine configurations. Scope uncertainty to the
affected common-data or TB stream and honor a proven
skipped command. Keep valid configurations on the existing
path. [Qblox LINQ data sharing](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#data-sharing)

**Acceptance.** The enabled examples have a source-linked
incomplete indication and no determinate 72 ns arrival or
FIFO consumption. With extras disabled, the normal estimate
remains 72 ns. Check a packed register, enabled common-data
write-combine, a TB width above one word, independent IQ
feedback, and a false `set_cond`. A later supported setting
can establish new sends while unresolved earlier FIFO words
keep any affected matches uncertain.

## GI. Mark unverified software requests and pulses as incomplete

**Reproduction.** Analyze `wait 8`, `set_freq 4000000`,
`play_pulse 0,4`, `upd_param 4`, and `stop`. Analysis
draws an exact pulse at RT 8–12 ns, removes the pending
frequency from the following update, and gives an exact
stop at 16 ns. Replacing the pulse with `sw_req 1`
silently continues to RT stop at 12 ns. Both instructions
are present in the local assembler table, and Concrete and
Simulation report unsupported execution at the source.

**Required behavior.** Keep source navigation and the
known prefix. Mark an executing `play_pulse` or `sw_req`
as incomplete when its behavior is unverified. Keep any
encoded dwell as planned information. Leave unsupported
RT effects, latch application and the affected later
execution unresolved. Preserve an independently proven
analytical skip and else dwell for an RT command. A device
reference is needed to assign actual behavior to either
instruction. [Public Q1ASM instruction table](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html)

**Acceptance.** The two examples show a source-linked
incomplete boundary after the preceding 8 ns wait. The
pulse keeps its planned duration of 4 ns and has no exact
applied frequency. Check immediate and register forms,
and the valid `upd_param` control. With `set_cond 1,0,0,12`
before the pulse and `set_cond 0,0,0,4` after it, the
skip runs from RT 8 to 20 ns and `upd_param` applies
the pending frequency at 20 ns. Preserve the existing
Concrete and Simulation unsupported outcomes.

## GJ. Bound compact-loop execution by future latch ranges

**Reproduction.** Run `wait 8`, `move 3,R1`,
`move 32767,R5`, `move 1,R6`, `nop`, and a loop whose
body is `set_awg_gain R5,R6`, `upd_param 4`,
`add R5,1,R5`, `nop`, and `loop R1,@body`. Follow it
with `stop`. Analysis reports RT stop at 20 ns. Concrete
applies the first gain at RT 8 ns and blocks when the
second visit reads 32768. The proven RT work ends at
12 ns and the Q1 validation boundary is 68 ns.

**Required behavior.** Check each ready latch operand's
proven range over the compacted visits before asserting
later RT work. For a proved affine recurrence, keep the
valid prefix through the first unsupported setter. Keep
the RT and Q1 boundaries separate and source-linked.
Preserve the compact representation of proven repeated
work. A register update after the final use can leave
an out-of-range word in the register without affecting
the preceding latch operations.

**Acceptance.** The example has a known RT prefix to
12 ns and an incomplete boundary at the second setter.
Initial gain 32766 moves the boundary to RT 16 ns and
Q1 116 ns. Initial gain 32765 reaches RT stop at 20 ns
and leaves R5 = 32768 after its final use. Check a lower
offset boundary, the phase upper bound, and existing
valid variable setter and branch paths.

## GK. Preserve single-word TB combine counts in analytical FIFO evidence

**Reproduction.** Give two senders `fb_acq_tb_id 16,4`,
`fb_acq_tb_cfg 1,0,1,4` and `fb_acq_tb_cfg 1,2,1,4`
respectively, then `acquire 0,0,16` and `stop` on each.
Use a 16 ns integration on both. A receiver performs
two pops for ID 16. Analysis predicts two payloads and
two matched pops with the same availability time, 24 ns.
In Simulation, with a common receiver, 5 ns route latency,
100 ns startup and both threshold bits set, one word
0x0f arrives at 129 ns with both source acquisitions.

**Required behavior.** Use the effective single-word TB
configuration captured for each acquisition. When completion,
destination and compatible combine settings prove one round,
count and consume one FIFO word with both contributors.
When the route or completion evidence allows multiple round
groupings, mark the affected count and matches unresolved.
Keep IQ streams and separate proven rounds independent.
Use validated project LINQ routes for destination and
latency evidence. The alignment mode controls display and
leaves the shared-clock proof independent.

**Acceptance.** The two-sender example never reports two
proven matches for one possible combine round. With a proven
common round, the balance records two contributions, one FIFO
payload and one match. Both original acquisition sources
remain navigable. Check noncoincident
completion, a conflicting bit position, disabled combination,
and independent IQ traffic. When Simulation already records
both contributors, keep them available from the same single
FIFO receipt.

For the proven common round, add a first `wait_sync` on every
participant and use the explicit common receiver routes. Set
the senders' barrier dwells to 4 and 8 ns and their integration
lengths to 16 and 12 ns respectively. Both finish 28 ns after
release. With startup at 100 ns and a 5 ns route latency,
Simulation receives one word at 133 ns. Increasing the second
integration to 16 ns gives two words at 133 and 137 ns. The
`after_first_wait_sync` display alignment can make the latter
case look coincident, so compare time relative to barrier release.

## GL. Keep wide receive selectors out of exact analytical FIFO matching

**Reproduction.** Analyze `fb_com_data 1,42,4`,
`fb_com_data 2,99,4`, `wait 100`, `fb_pop_data 257,R0`,
`fb_pop_data 2,R1`, `upd_param 4`, and `stop`. Analysis
reports channel 257 as an unmatched ordinary receive.
It then matches channel 2, reports channel 1 discarded,
and places RT stop at 112 ns. BG already preserves the
16-bit encoding and reports the unresolved device behavior
in Simulation and scheduled Play.

**Required behavior.** Use the same model boundary in
Analysis for a resolved selector above 255. Keep the
source command and the last proven RT work. Make the
affected receiver FIFO and later execution incomplete
until the selector behavior is established. Retain proven
matches before the boundary. [Q1ASM instruction table](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#fb_pop_data-instt),
[LINQ receiving guide](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#fb-pop-data)

**Acceptance.** The example reports the limitation at
source line 4 and keeps the RT prefix through 108 ns.
There is no definite discard for ID 1, match for ID 2,
or downstream RT stop. Selector 1 matches both sends
with the normal 60 and 64 ns arrival estimates. Check
a `.DEF` selector, the valid upper boundary 255, and a
supported receive before the wide selector.

## GM. Validate known register operands before analytical RT admission

**Reproduction.** Analyze `wait 8`, `move 2,R0`, `nop`,
`set_latch_en R0,4`, `upd_param 4`, and `stop`. Analysis
records a latch-enable block at 8 ns and RT stop at 16 ns.
Simulation rejects the resolved enable value before queue
admission. The same difference occurs with a `wait_trigger`
register address of 16, a `play` waveform index of 1024,
an `acquire` bin index of 16777216 and a TB feedback ID
of 256.

**Required behavior.** Apply the existing resolved ranges
for typed RT operands when a ready register proves an
out-of-range value. Link the diagnostic to the raw operand
and instruction, preserve the nominal RT prefix, and keep
the affected command and suffix out of exact RT effects.
Maintain existing paths for unresolved operands, duration
validation, and packed or raw LINQ decoding. [Qblox Q1ASM real-time instructions](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#real-time-instructions)

**Acceptance.** Each example preserves the preceding
RT 0–8 ns block and has a source-linked incomplete
boundary at the affected command. Use the same accepted
boundary values as Simulation. Verify a legal immediate
and register pair, an unresolved register and a register
readiness hazard. Keep the existing pre-admission
validation order when an RT condition would skip work.

## GN. Inspect collapsed LINQ diagnostic evidence in standalone Normal

**Reproduction.** Open the standalone HTML for GH's
enabled `fb_com_extra` example in Normal. The source-linked
diagnostic has a `Show event` action. Its target is a
detailed LINQ block collapsed in Normal. The action leaves
the inspector unchanged. The same target is inspectable
in Debug.

**Required behavior.** Keep the current priority for
visible diagnostic evidence. When the primary target is
only collapsed by Normal's LINQ detail policy, open that
event in the inspector while preserving Normal and its
layout. Keep the existing visibility restrictions for
filters, hidden comparison paths, and exact TB actions.

**Acceptance.** In Normal, `Show event` opens the enabled
configuration's source and RT `8–12 ns` in the inspector.
The LINQ detail block stays hidden. In Debug it also
selects the visible block. Check a diagnostic with a
visible alternative, a filtered target, and a pre-origin
target.

## GO. Keep analytical FIFO balance independent of loop preview length

**Reproduction.** Analyze `move 3,R1`, `nop`,
`body: fb_com_data 1,42,4`, `loop R1,@body`, `wait 100`,
three `fb_pop_data 1,R…` commands, and `stop`. The loop
has three proven visits. With one displayed visit, Analysis
reports one send and two unmatched receives. With three
displayed visits, it reports three matched receives at
estimated arrival times 60, 64 and 68 ns.

**Required behavior.** Use the proven loop count and
per-visit feedback timing for FIFO matching. The number of
displayed copies affects navigation and geometry. Preserve
the original source and the matched occurrence's iteration
evidence for a hidden send, without expanding the entire
loop. When a count, configuration, or FIFO ordering is
unproven, keep the affected balance incomplete.

**Acceptance.** The example reports three sends and three
matched receives with the same arrival estimates at preview
counts 1, 2 and 3. Check a large fixed count with only one
displayed visit, an unresolved count, and an unsupported
LINQ setting inside the loop. The UI can still select
the source of a matched hidden occurrence.

## GP. Show encoded dwell in the unverified RT command inspector

**Reproduction.** Analyze `wait 8`, `play_pulse 0,4`,
and `stop` after GI. The retained `unknown_region` links
to `play_pulse` at RT 8 ns and records `planned_duration_ns`
of 4. Both inspectors show an unresolved time range and
omit the encoded dwell already available in the trace.

**Required behavior.** Show the encoded dwell as a
separate planned duration when an unverified RT command
has a valid concrete value in its retained metadata.
Keep the actual end unresolved and preserve the visible
timeline bounds. Display time alignment changes the start
coordinate and leaves the duration unchanged.

**Acceptance.** Selecting the pulse boundary shows its
8 ns start, unknown end and `Planned duration` of 4 ns
in both viewers. Check a manual offset, the `sw_req`
control with no planned RT duration, and the existing
time-limit inspection for observed commands.

## GQ. Keep fixed-start simulation idle on Q1-only paths

**Reproduction.** Simulate `stop 17` with
`startup_delay_ns: 20`. Q1 finishes at 4 ns with code 17,
but the later fixed timer creates an RT stop at 20 ns.
With startup at 4 ns, `nop`, `nop`, `stop 17` fails on
RT underflow before reaching its Q1 stop. Neither program
can enqueue an RT packet. In a mixed project, the first
example puts an RT stop at the origin of its peer's RT lane.

**Required behavior.** Leave RT idle for a program proved
to execute only Q1 work. When Q1 completes before the fixed
start without any admitted RT packet, complete the run from
its recorded Q1 edge. Preserve explicit fixed-start
underflow tests for programs that can supply RT work.

**Acceptance.** At fixed startup 20 ns, `stop 17` keeps
its Q1 code and has no RT dequeue or stop. At startup
4 ns, two `nop`s then `stop 17` reach Q1 completion at
12 ns, with RT idle throughout. A bounded Q1-only loop
reports the Q1 limit. A peer with real RT work retains its
origin and drain time. Check a program that would enqueue
its first RT packet after fixed startup and the existing
buffered, prepared, and Q1 fault controls.

## GR. Stop queue safety claims at an explicit Q1 proof boundary

**Reproduction.** Analyze `wait 8`, `sw_req 1`,
`upd_param 4`, and `stop`. Analysis records a Q1 proof
cutoff at `sw_req` and an RT boundary at 8 ns. The RT
queue summary nevertheless reports no underflow detected
under its assumptions. The same occurs with a ready invalid
RT register operand and when the backward-branch repeat
limit stops after two known 4 ns waits.

**Required behavior.** Use explicit Q1 proof cutoff
evidence when checking RT queue coverage. Replay the known
packet prefix, then mark the rest of the queue proof
incomplete. Link the incomplete result to the actual
source and RT boundary. Preserve a definite underflow
already established within the prefix and existing behavior
for a proved packet-free program.

**Acceptance.** The examples report `analysis_incomplete`
with the incomplete marker at RT 8 ns and the known
queue records intact. The mid-body repeat case keeps
both waits. An already proven prefix underflow remains
`definite_underflow`. Check a Q1-only loop, a complete
RT trace, and a partial trace before a synchronization
barrier that cannot prove release.

## Device reference vectors still needed

The public Qblox 2026.10.2 instruction table still labels the fourth
`fb_acq_tb_mock` operand as a duration in its signature and a sample count
in the description. Its extra-byte instructions describe enabling a payload
extension. The LINQ guide's write-combine example uses one FIFO word.
[Q1ASM instruction reference](https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html#fb_acq_tb_mock-instt),
[LINQ write-combine guide](https://docs.qblox.com/en/main/products/architecture/sequencers/linq_based_feedback.html#write-combine-mode).

Include the module type, instrument firmware and ISA versions, assembler
version, input program, relevant module settings, and receiver observations
in each vector. Use the assembler listing to establish operand encoding and
the device observations to establish emitted data, phase, timing and flags.

| Behavior | Minimum observable fixture |
| --- | --- |
| `fb_acq_tb_mock` | Enabled and disabled sender cases, with and without a later acquisition. Change the fourth operand between two positive values and record the number, words, and arrival times of received entries plus the next RT marker time. |
| Enabled LINQ extra bytes | Send common word `0x12345678` with extra `0xa1b2`, then a TB with both valid/data bits explicit and the same extra value. Record all received words in FIFO order and timing relative to completion. |
| Common-data and multiword write-combine | Record two senders with the same ID and non-overlapping ranges at a shared send time, including a payload crossing one 32-bit boundary. Repeat with different send times and record the receiver FIFO entries. |
| `fb_pop_data` selector above 255 | The instruction reference and assembler accept a 16-bit immediate. The LINQ receive guide describes an 8-bit ID. Compare selectors 1 and 257 against the same scheduled ID-1 word. Record the register, resulting stall or status, and whether the word remains in the FIFO. |
| QTM fractional and invalid LINQ values | Record the output word and actual receive time for a sub-nanosecond edge and for each invalid-delta policy. Include a signed negative valid delta as a control. |
| Trigger network conflict arbitration and recovery | Launch two requests on the same slot, then repeat with separation below 252 ns and with separation of exactly 252 ns as a control. Record which receivers observed each request, the module error flags, and whether an independent later request arrives. |
| Source macro and expanded Q1 timing | The Qblox Instruments 1.4.1 assembler emits identical words for `loop` and `sub`/`jnz`, legacy `jlt` and `cmp`/`jb`, and legacy `jge` and `cmp`/`jae`. Q1Lens models q1simulator 1.4.1 source timing. A loop takes 20/32 ns on continue/jump. Legacy comparisons take 16/28 ns with a literal target or 20/32 ns with a register target. The explicit immediate-operand ALU and flag-branch pairs take 16/28 ns. Record Q1 issue timestamps for each edge, or measure the RT underflow boundary with identical queue conditions. |
| Undocumented `play_pulse` and `sw_req` forms | Provide the target firmware reference or a paired RT output and status capture that isolates the instruction from surrounding updates. |

Keep unresolved variants explicit until a vector establishes their behavior.
