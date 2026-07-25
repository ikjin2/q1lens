# Forgot LINQ Wait Fixed

This before/after example fixes `05-common-mistakes/20-forgot-linq-wait` by delaying
the feedback receive until the LINQ channel latency budget has elapsed.

Inspect:

- the producer-side `feedback_com`
- the delayed consumer-side `feedback_pop`
- channel 16 marked `balanced`

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 06-before-after-fixes/21-forgot-linq-wait-fixed/q1timeline.yml
```
