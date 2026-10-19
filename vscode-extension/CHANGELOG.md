# Changelog

## 0.2.2

- Add bounded Run playback with automatic branch choices and editable feedback value and arrival-time assumptions.
- Display Compare both branches in parallel Jump/Continue rows and show latched settings where RT instructions apply them.
- Detect repeated execution, collapse loops by default, and fold long waits with matching time-axis compression.
- Keep loop counts in the loop bar and limit expansion to the fold icon.
- Preserve Q1/RT lane selection independently when selecting source lines, and keep RT block navigation in its lane.
- Correct disabled and unresolved set_cond handling, queue-aware timing, and initial framing of folded timelines.
- Pair with Python package 0.2.2 and add set_cond review examples.

## 0.2.1

- Fold unchanged wait intervals across sequencers, with visible duration markers and per-interval expand/collapse controls.
- Fit the timeline to selected Q1ASM source lines automatically.
- Add an Instruction view with equal-width blocks in separate Q1 core and RT lanes, selection fitting, and wheel zoom.
- Keep Time scale as the default view and move Hardware state below Analysis details.
- Correct initial timeline framing for compact wait loops and clarify Q1 core labels.
- Pair with Python package 0.2.1 to account for wait_sync and RT queue state when detecting underflow, while retaining real underflow warnings.

## 0.2.0

- Check and select the Python environment used by both timeline views.
- Respect explicit Python settings instead of shadowing them with defaults.
- Correct cross-layer timing comparisons around synchronization preludes.
- Refresh the displayed project while preserving the current timeline view.
- Edit temporary analysis parameters without changing project files.
- Highlight full Q1ASM instruction lines for diagnostics.
- Inspect cursor-time registers, simulated signal stages, acquisitions, and feedback in the Hardware view.
- Replay bounded concrete and simulated instruction traces, including partial failures and input waits.
- Preserve missing signal samples as gaps, including single samples and sampled-time boundaries.
- Save a persistent comparison baseline and inspect before/after values for changed events.
- Navigate Compiler, Inferred, and Unmapped provenance evidence through the coverage legend.
- Apply validated Q1ASM Quick Fixes with opt-in hardware-readiness checks.
- Keep schedule summaries and provenance controls usable in narrow views.

## 0.1.2

- Bundle runtime dependencies for Marketplace distribution.

## 0.1.1

- Make the release test suite reproducible from a clean checkout.

## 0.1.0

- Initial public release.
- Inspect Qblox schedules and symbolic pulse timing.
- Preview Q1ASM instruction timelines and lowering provenance.
- Navigate between timeline events and source locations in VS Code.
