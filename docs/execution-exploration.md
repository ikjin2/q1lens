# Execution exploration: accepted direction

The standalone reference is [Execution Flow](../demo-execution-flow/README.md).
The user accepted the compact flow inspector, alternate branch selection and
automatic loop history on 2026-11-04. Preserve this prototype while the existing
extension work is stabilized; implement the extension integration afterwards.

## Accepted interactions

- Select one execution occurrence to see its actual inputs, operation, result
  and next instruction. Selecting a value follows its writer and supports return.
- Keep Jump and Continue as separate rows. Click another row to rerun that
  occurrence with the same inputs and a different path. Preserve the natural
  predicate result and mark the hypothetical path and subsequent execution.
- Changing an earlier branch discards later choices. Restore the original run
  without changing source files or inputs.
- Click a loop to open its first five recorded iterations automatically. Show
  only registers and latched parameters that change in that loop; reveal more
  iterations in groups of five. Inner loops can be selected separately.
- Retain intermediate changes inside a cell, such as `2 -> 3 -> 0`. A cell value
  opens the instruction that produced it; a row opens that iteration's detail.
- Keep the loop table visible while the instruction detail scrolls. Use the
  extension's compact typography, colors and controls.

## Accuracy requirements

Use Q1Lens execution records throughout. The old Execution Explorer prototype
has a separate interpreter and is historical reference only; do not port its
execution semantics or generated Golden-section results.

Register changes become available at their recorded readiness time. Latched
setting updates and application by RT are different events. Hypothetical branch
selection must preserve operands, flags and the `loop` counter update, while
recalculating path-dependent timing, register writes and pending work.

Preserve missing inputs, execution limits and unsupported instructions as visible
boundaries. A source line alone does not identify an occurrence. Scope identities
to the sequencer and execution context, including nested/re-entered loops.

## Existing extension baseline

Retain the current Hardware UI and the existing latched values displayed where
RT instructions apply them. Keep Compare both alternatives in separate
Jump/Continue rows. Critical Path and the old separate register badges/flags
remain deferred.

The prototype has not been integrated into the extension. The current extension
save/diagnostic path changes are an independent work item.

## Implementation sequence

1. Finish and verify the existing editor/diagnostic work against the current
   `main`. Keep the new simulation branch-choice tests in the regular suite.
2. Project occurrence, writer, loop and latched-setting records from the engine
   into the timeline IR. Cover nested loops, early exits, delayed writes and
   settings that remain constant. The prototype's static backward-edge grouping
   and flag observer are reference scaffolding, not the final integration API.
3. Add the compact inspector and fixed loop-history panel to the existing
   webview. Connect source/loop selection and preserve selected occurrences on
   refresh. Keep analysis, analytical playback and event-driven simulation
   modes explicit; their timing and input guarantees differ.
4. Route branch choices through the extension controller and existing Python
   process lifecycle, with cancellation and stale-response protection. The local
   HTTP demo server is for the prototype, not an extension dependency.
5. Verify the whole path in the extension host: loop click -> first five rows ->
   cell provenance -> alternate branch -> recalculated history -> original run.
   Recheck the retained Hardware and Compare both behavior.

## Reference and verification

- `demo-execution-flow/`: canonical prototype, real-engine exporter and local server.
- `demo-execution-flow/history.test.js`: register/latched history projection checks.
- `demo-execution-flow/test_build.py` and `test_serve.py`: evidence and replay API.
- `tests/test_q1timeline_simulation_branch_choices.py`: engine-level choice contract.
- `simulation.runtime.simulate_programs(..., branch_choices=...)`: per-sequencer,
  PC and occurrence choices. Unreachable or malformed choices are rejected.
- `q1timeline/playback.py`, extension `playbackClient.ts`, controller and timeline
  adapter: existing integration points to inspect before implementation.
