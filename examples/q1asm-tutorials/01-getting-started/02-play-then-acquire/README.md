# Play Then Acquire

This example introduces a minimal readout-shaped timeline. The sequencer emits
a readout pulse with `play`, then opens an acquisition window with `acquire`.

Inspect:

- the readout `play` window
- the following `acquire` window
- the acquisition bin index in the source line

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 01-getting-started/02-play-then-acquire/q1timeline.yml
```
