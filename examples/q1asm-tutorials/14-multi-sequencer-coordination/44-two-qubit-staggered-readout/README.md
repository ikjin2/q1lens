# Two-Qubit Staggered Readout

This q1timeline project shows two qubit lanes that share a `wait_sync` anchor
but intentionally do not start at the same time.

The drive sequencers prepare `q0` and `q1` with a 40 ns skew. The readout
sequencers then start their readout pulses with an 80 ns stagger, so the
matching acquisition windows are also offset by 80 ns. The example is meant for
timeline inspection of skewed multi-sequencer timing, not as a hardware recipe.

Expected analyzer signals:

- four sequencer lanes aligned on the first `wait_sync`
- `q1_readout` readout and acquisition events 80 ns after `q0_readout`
- no diagnostics

Run:

```powershell
python -m q1timeline analyze --project 14-multi-sequencer-coordination/44-two-qubit-staggered-readout/q1timeline.yml --out tmp/two-qubit-staggered-readout-ir.json --diagnostics tmp/two-qubit-staggered-readout-diag.json
```
