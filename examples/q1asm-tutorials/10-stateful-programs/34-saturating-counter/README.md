# Saturating Counter

This example receives a runtime counter value into a register and clamps it into
a safe range before any real-time action is emitted. It is intended for
inspecting branch assumptions and seeing how a seemingly small amount of
classical housekeeping affects the next RT packet.

Inspect:

- branch regions for the high and low clamps
- register aliases in the source gutter
- the fallthrough path versus clamp paths

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 10-stateful-programs/34-saturating-counter/q1timeline.yml
```
