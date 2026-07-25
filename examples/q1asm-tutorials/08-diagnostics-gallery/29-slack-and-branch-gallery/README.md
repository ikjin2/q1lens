# Slack And Branch Gallery

This compact example intentionally combines two analyzer warnings in one place:
an unresolved branch and a possible RT underflow after a short sync gap. It is a
diagnostics browsing fixture, not a program pattern to copy into hardware code.

Inspect:

- the unresolved branch diagnostic source line
- the negative slack attached to the first `play`
- how debug mode separates Q1 issue time from RT packet time

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 08-diagnostics-gallery/29-slack-and-branch-gallery/q1timeline.yml
```
