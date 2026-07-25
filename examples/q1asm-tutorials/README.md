# Q1ASM Tutorial Corpus

This corpus is a set of Qblox-docs-adjacent Q1ASM examples for q1timeline.
The examples intentionally avoid hardware setup, waveform upload, and run-time
instrument control. They focus on program behavior that is easier to understand
when the Q1ASM timeline is visible.

From the repository root, open this tutorial workspace in VSCode with:

```powershell
code --extensionDevelopmentPath "$PWD\vscode-extension" "$PWD\examples\q1asm-tutorials\q1asm-tutorials.code-workspace"
```

Then open an example `q1timeline.yml` and run `Q1Lens: Open Timeline Preview`.

## Example Catalog

| # | Path | What to inspect |
| --- | --- | --- |
| 01 | `01-getting-started/01-hello-timeline` | A single sequencer with one sync anchor, wait gap, and play block. |
| 02 | `01-getting-started/02-play-then-acquire` | A minimal readout-shaped sequence with a play window followed by acquisition. |
| 03 | `01-getting-started/03-two-lane-alignment` | QCM and QRM lanes aligned by `wait_sync` with different offsets. |
| 04 | `01-getting-started/04-feedback-round-trip` | A balanced producer-to-consumer LINQ feedback transfer. |
| 05 | `02-debug-reading/05-q1-issue-vs-rt-time` | Q1 issue events separated from the real-time packets they prepare. |
| 06 | `02-debug-reading/06-queue-depth-basics` | A short train of real-time packets for reading queue depth. |
| 07 | `02-debug-reading/07-slack-basics` | Debug slack lanes with enough wait budget before RT packets. |
| 08 | `02-debug-reading/08-source-line-navigation` | Short play/acquire source lines that are easy to jump to from the timeline. |
| 09 | `03-params-basics/09-define-constants` | Local `.DEF` constants used by wait and play instructions. |
| 10 | `03-params-basics/10-params-json-duration` | Wait and play durations supplied by `params.json`. |
| 11 | `03-params-basics/11-params-json-feedback-channel` | Feedback channel and receive wait supplied by `params.json`. |
| 12 | `03-params-basics/12-retune-without-editing-q1asm` | Latched drive settings retuned through JSON values. |
| 13 | `04-hardware-first/13-qcm-drive-only` | A single QCM drive lane before readout or triggering. |
| 14 | `04-hardware-first/14-qrm-readout-only` | A single QRM readout lane with play and acquisition windows. |
| 15 | `04-hardware-first/15-qcm-qrm-basic-readout` | A simple QCM drive plus QRM readout pair. |
| 16 | `04-hardware-first/16-marker-and-scope-basics` | Marker gate and QRM scope/acquisition timing. |
| 17 | `04-hardware-first/17-triggered-shot-basics` | A single triggered readout shot. |
| 18 | `05-common-mistakes/18-too-tight-loop` | A compact loop missing enough wait budget before real-time work. |
| 19 | `06-before-after-fixes/19-too-tight-loop-fixed` | The tight-loop example with wait budget inserted before RT work. |
| 20 | `05-common-mistakes/20-forgot-linq-wait` | A feedback receive that happens before the LINQ latency budget has elapsed. |
| 21 | `06-before-after-fixes/21-forgot-linq-wait-fixed` | A delayed feedback receive that satisfies the LINQ latency budget. |
| 22 | `05-common-mistakes/22-feedback-channel-imbalance` | A channel with more produced feedback payloads than drained pops. |
| 23 | `06-before-after-fixes/23-feedback-channel-imbalance-fixed` | A feedback channel with matching send and receive counts. |
| 24 | `05-common-mistakes/24-assumed-runtime-branch` | A feedback-derived branch that cannot be resolved statically. |
| 25 | `05-common-mistakes/25-forgot-upd-param` | Latched gain, frequency, and phase changes staged before a play but committed late. |
| 26 | `06-before-after-fixes/26-forgot-upd-param-fixed` | Latched state committed before each play. |
| 27 | `07-timing-pathologies/27-short-loop-underflow` | A compact loop whose RT work outruns Q1 issue timing, producing a definite underflow diagnostic. |
| 28 | `07-timing-pathologies/28-branch-housekeeping-underflow` | Classical branch bookkeeping that leaves zero slack before short RT work. |
| 29 | `08-diagnostics-gallery/29-slack-and-branch-gallery` | A small fixture combining unresolved branch and possible-underflow diagnostics. |
| 30 | `09-latched-state-patterns/30-atomic-parameter-window` | Gain, frequency, and phase changes staged together and applied by `upd_param`. |
| 31 | `10-stateful-programs/31-bounded-random-walk` | Register-derived state, branch assumptions, and bounded updates. |
| 32 | `10-stateful-programs/32-round-robin-bin-writer` | A register-derived acquisition bin index in a compact loop. |
| 33 | `10-stateful-programs/33-hysteresis-threshold-controller` | Feedback-derived high/low threshold branches with pending gain changes. |
| 34 | `10-stateful-programs/34-saturating-counter` | Runtime counter update and clamp branches. |
| 35 | `11-control-flow-patterns/35-bounded-retry-loop` | A bounded retry loop with feedback-driven early exit and marker application on the accepted path. |
| 36 | `11-control-flow-patterns/36-branch-table-selector` | A compact selector that exposes multiple unresolved branch regions. |
| 37 | `12-feedback-patterns/37-register-broadcast` | A producer-to-consumer feedback flow with a matched pop. |
| 38 | `12-feedback-patterns/38-latency-violation` | A deliberately early feedback pop that q1timeline flags. |
| 39 | `12-feedback-patterns/39-linq-throughput-simulator` | A dense LINQ feedback pressure pattern with over-production, latency violation, and IQ pair payload drain. |
| 40 | `13-hardware-patterns/40-qcm-qrm-triggered-readout` | QCM drive and QRM readout lanes aligned to a trigger with staggered play/acquire windows. |
| 41 | `13-hardware-patterns/41-marker-gated-awg-window` | Marker high/low events around an AWG window and QRM scope/acquire timing. |
| 42 | `13-hardware-patterns/42-acquisition-telemetry-sidecar` | A primary readout lane plus a shorter telemetry sidecar acquisition. |
| 43 | `13-hardware-patterns/43-readout-feedback-reset-forensics` | Conditional-reset-adjacent feedback forensics with branch and latency visibility. |
| 44 | `14-multi-sequencer-coordination/44-two-qubit-staggered-readout` | Four lanes with staggered drive, readout, and acquisition windows. |
| 45 | `14-multi-sequencer-coordination/45-trigger-skew-comparison` | Three trigger-aligned lanes with explicit post-trigger skew. |
| 46 | `14-multi-sequencer-coordination/46-feedback-arbitration` | Two clients, one arbiter, and an under-produced grant channel. |
| 47 | `13-hardware-patterns/47-qcm-qrm-threshold-pull-router` | QCM stimulus into QRM thresholded feedback, pulled with `fb_pull_data` to choose the QRM output. |
| 48 | `13-hardware-patterns/48-qcm-qrm-iq-pull-router` | QCM stimulus into QRM IQ feedback, pulled with `fb_pull_data` to choose the QRM output. |

## Suggested Walkthrough

1. Start with `01-getting-started/01-hello-timeline`.
2. Move to `01-getting-started/02-play-then-acquire` to see the readout and
   acquisition shape.
3. Open `01-getting-started/03-two-lane-alignment` to compare two aligned lanes.
4. Use `01-getting-started/04-feedback-round-trip` for the normal feedback path.
5. Switch to `02-debug-reading/05-q1-issue-vs-rt-time`.
6. Use `02-debug-reading/06-queue-depth-basics` and `02-debug-reading/07-slack-basics` to
   read the debug lanes.
7. Open `02-debug-reading/08-source-line-navigation` and jump from events back to
   source lines.
8. Switch to `07-timing-pathologies/27-short-loop-underflow`.
9. In debug mode, inspect Q1 issue events, queue depth, and slack.
10. Open the source line for the underflow warning.
11. Edit the short RT duration or the loop body and re-run the preview.
12. Compare it with `05-common-mistakes/18-too-tight-loop`.
13. Open `06-before-after-fixes/19-too-tight-loop-fixed` to compare the fixed timing.
14. Open `05-common-mistakes/25-forgot-upd-param` and compare the play before and
   after `upd_param`.
15. Compare it with `06-before-after-fixes/26-forgot-upd-param-fixed`.
16. Use `04-hardware-first/13-qcm-drive-only`, `04-hardware-first/14-qrm-readout-only`, and
   `04-hardware-first/15-qcm-qrm-basic-readout` before the advanced hardware examples.
17. Use `03-params-basics/10-params-json-duration` and
   `03-params-basics/12-retune-without-editing-q1asm` before editing larger projects.
18. Move to `14-multi-sequencer-coordination/44-two-qubit-staggered-readout`.
19. Finish with `13-hardware-patterns/43-readout-feedback-reset-forensics` and compare
   it with `12-feedback-patterns/38-latency-violation`.
20. Use `12-feedback-patterns/39-linq-throughput-simulator` as the advanced LINQ
   pressure example once feedback flows and balance are familiar.

These examples are written as small program-pattern labs. They are not official
hardware recipes and should not be read as instrument configuration guidance.
