# Too-Tight Loop Fixed

This before/after example fixes `05-common-mistakes/18-too-tight-loop` by adding a
wait budget inside the loop before the RT `play` packet.

Inspect:

- the compact loop preview
- the `play` packet after the added wait
- the absence of the tight-loop underflow warning

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 06-before-after-fixes/19-too-tight-loop-fixed/q1timeline.yml
```
