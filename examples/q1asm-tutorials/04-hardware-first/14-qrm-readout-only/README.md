# QRM Readout Only

This hardware-first example shows a single QRM-style readout lane with a pulse
window followed by acquisition.

Inspect:

- the readout `play` packet
- the `acquire` window
- the acquisition bin argument

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 04-hardware-first/14-qrm-readout-only/q1timeline.yml
```
