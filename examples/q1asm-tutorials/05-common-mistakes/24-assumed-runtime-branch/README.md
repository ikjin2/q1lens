# Assumed Runtime Branch

This common-mistake example treats a feedback-derived branch as if the static
source path were obvious. The consumer receives a selector from LINQ feedback,
then branches on that runtime value.

Inspect:

- the `feedback_pop` event that makes `$SELECT` runtime-dependent
- the `branch_region` event around the unresolved comparison
- the `unresolved_branch` diagnostic

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 05-common-mistakes/24-assumed-runtime-branch/q1timeline.yml
```
