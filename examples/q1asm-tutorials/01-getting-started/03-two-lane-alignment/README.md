# Two-Lane Alignment

This example shows two sequencers aligned by the same `wait_sync` anchor. The
QCM lane plays first, while the QRM lane starts its readout and acquisition
later.

Inspect:

- both lanes aligned on `wait_sync`
- the QCM `play` offset
- the later QRM `play` and `acquire` windows

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 01-getting-started/03-two-lane-alignment/q1timeline.yml
```
