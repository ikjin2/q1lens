# Acquisition Telemetry Sidecar

This example separates a primary readout lane from a lightweight telemetry lane.
The sidecar sequencer records a shorter status window while the main QRM lane
does the experiment-facing acquire.

Inspect:

- the sidecar acquire relative to the main readout acquire
- the QCM play offset after the trigger
- whether all three sequencers align on the same trigger event

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 13-hardware-patterns/42-acquisition-telemetry-sidecar/q1timeline.yml
```
