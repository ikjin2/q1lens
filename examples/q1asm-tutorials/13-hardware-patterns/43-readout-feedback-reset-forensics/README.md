# Readout Feedback Reset Forensics

This is a forensic timing example, not a conditional-reset recipe. It shows a
readout sequencer publishing a feedback result and a reset sequencer consuming
that result too early on purpose, so the timeline exposes the assumptions that
would be hidden in a high-level conditional-reset walkthrough.

Inspect:

- the readout `play` and `acquire` windows before feedback is published
- the reset-side `feedback_pop` and runtime-dependent `branch_region`
- the reset pulse shown on the assumed fallthrough path
- the feedback latency diagnostic that makes the unsafe wait budget visible

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 13-hardware-patterns/43-readout-feedback-reset-forensics/q1timeline.yml
```
