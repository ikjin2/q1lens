# Branch Table Selector

This example models a compact runtime selector as a branch table. A feedback
pop fills the selector register, so q1timeline cannot know the selected arm
statically. The branch policy keeps the table visible by assuming fallthrough
while marking each selector comparison as a branch region.

Inspect:

- the `feedback_pop` event that makes `$SELECT` runtime-dependent
- one `branch_region` per selector comparison
- the default arm that remains visible under the fallthrough assumption

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 11-control-flow-patterns/36-branch-table-selector/q1timeline.yml
```
