# QCM Drive Only

This hardware-first example shows a single QCM-style drive lane before adding
readout, triggers, markers, or feedback.

Inspect:

- the `wait_sync` anchor
- the drive-lane `play` packet
- the time gap between sync and drive

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 04-hardware-first/13-qcm-drive-only/q1timeline.yml
```
