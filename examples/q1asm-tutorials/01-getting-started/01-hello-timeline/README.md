# Hello Timeline

This first example shows the smallest useful q1timeline project: one sequencer,
one sync anchor, one wait, and one real-time play packet.

Inspect:

- the `wait_sync` anchor
- the `wait` gap before real-time work
- the `play` block on the sequencer lane

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 01-getting-started/01-hello-timeline/q1timeline.yml
```
