# Define Constants

This params-basics example uses local `.DEF` constants before introducing a
separate `params.json` file.

Inspect:

- `$DRIVE_WAIT` in the `wait` instruction
- `$DRIVE_DURATION` in the `play` instruction
- the resolved timing in the timeline

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 03-params-basics/09-define-constants/q1timeline.yml
```
