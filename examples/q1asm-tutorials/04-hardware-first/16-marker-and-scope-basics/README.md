# Marker And Scope Basics

This hardware-first example pairs a simple marker gate with a short QRM scope
acquisition window.

Inspect:

- marker high and marker low `marker_state` events
- the QRM `acquire` window
- the relative timing between marker and scope capture

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 04-hardware-first/16-marker-and-scope-basics/q1timeline.yml
```
