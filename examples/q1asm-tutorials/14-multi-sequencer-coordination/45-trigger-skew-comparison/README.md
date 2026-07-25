# Trigger Skew Comparison

This example places three sequencers on the same trigger and gives each lane a
different post-trigger delay. It is meant for comparing alignment modes and
visualizing a small skew budget without needing any instrument configuration.

Inspect:

- the shared `wait_trigger` anchor
- drive and readout offsets after the trigger
- how changing a `wait` value moves only one lane

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 14-multi-sequencer-coordination/45-trigger-skew-comparison/q1timeline.yml
```
