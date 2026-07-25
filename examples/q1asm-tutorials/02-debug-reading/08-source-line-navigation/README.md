# Source Line Navigation

This debug-reading example keeps the source short and varied so users can jump
between timeline blocks and their Q1ASM lines.

Inspect:

- the first `play` source line
- the `acquire` source line
- the later `play` line after a separate wait

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 02-debug-reading/08-source-line-navigation/q1timeline.yml
```
