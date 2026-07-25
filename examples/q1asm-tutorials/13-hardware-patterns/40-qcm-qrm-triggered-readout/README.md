# QCM/QRM Triggered Readout

This q1timeline project sketches a bench pattern where a QCM drive sequencer and a QRM readout sequencer share the same sync anchor, then wait for the same external trigger before issuing timed packets.

The drive sequencer uses `wait_sync` and `wait_trigger` before playing an 80 ns drive pulse. The readout sequencer uses the same sync and trigger gate, waits longer, then plays a readout pulse and opens a later acquisition window. The useful detail is the gap: the acquisition is intentionally staggered behind the drive pulse instead of being placed at the same trigger edge.

Expected analyzer signals:

- `wait_sync` events on both sequencers
- `wait_trigger` events on both sequencers
- a QCM `play` event before the QRM `acquire` event
- no unsupported-instruction diagnostics

Run:

```powershell
python -m q1timeline analyze --project 13-hardware-patterns/40-qcm-qrm-triggered-readout/q1timeline.yml --out tmp/qcm-qrm-triggered-readout-ir.json --diagnostics tmp/qcm-qrm-triggered-readout-diag.json
```
