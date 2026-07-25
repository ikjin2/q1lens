# Q1 Issue Versus RT Time

This debug-reading example separates Q1 instruction issue events from the
real-time `play` packet they prepare.

Inspect:

- `q1_issue` events for classical setup instructions
- the RT `play` packet after the wait budget
- the debug slack lane near the play packet

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 02-debug-reading/05-q1-issue-vs-rt-time/q1timeline.yml
```
