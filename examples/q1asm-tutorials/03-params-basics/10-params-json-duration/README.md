# Params JSON Duration

This params-basics example moves wait and play durations into `params.json` so
the timeline can be retuned without editing Q1ASM source.

Inspect:

- the `params:` entry in `q1timeline.yml`
- `{DRIVE_WAIT}` and `{DRIVE_DURATION}` placeholders
- the resolved `play` duration in the timeline

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 03-params-basics/10-params-json-duration/q1timeline.yml
```
