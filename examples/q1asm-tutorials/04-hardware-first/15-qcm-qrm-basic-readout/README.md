# QCM/QRM Basic Readout

This hardware-first example combines one QCM drive lane with one QRM readout
lane using the same sync anchor.

Inspect:

- both lanes aligned on `wait_sync`
- the QCM drive pulse before readout
- the QRM `play` and `acquire` windows

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 04-hardware-first/15-qcm-qrm-basic-readout/q1timeline.yml
```
