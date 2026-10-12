# Changelog

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
