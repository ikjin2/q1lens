# Slack Basics

This debug-reading example gives the analyzer enough wait budget before each
RT packet, making the slack lane easier to read before looking at underflows.

Inspect:

- the `slack` debug lane
- the `wait_sync` anchor
- the `play` packets that consume the wait budget

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 02-debug-reading/07-slack-basics/q1timeline.yml
```
