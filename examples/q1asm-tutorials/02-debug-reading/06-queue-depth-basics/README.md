# Queue Depth Basics

This debug-reading example emits a small train of RT packets so the queue depth
lane has multiple points to inspect.

Inspect:

- the `queue_depth` debug events
- the spacing between `play` packets
- how each `wait` shifts the following RT packet

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 02-debug-reading/06-queue-depth-basics/q1timeline.yml
```
