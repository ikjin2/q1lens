# Triggered Shot Basics

This hardware-first example introduces `wait_trigger` in a single readout lane.
The shot waits for an external trigger before issuing readout and acquisition
packets.

Inspect:

- the `wait_trigger` anchor
- the readout `play` after the trigger
- the following `acquire` window

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 04-hardware-first/17-triggered-shot-basics/q1timeline.yml
```
