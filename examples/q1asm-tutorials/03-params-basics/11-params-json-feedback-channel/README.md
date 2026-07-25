# Params JSON Feedback Channel

This params-basics example puts the LINQ feedback channel and consumer wait
budget in `params.json`.

Inspect:

- `{FEEDBACK_CHANNEL}` used by both sequencers
- the producer `feedback_com`
- the delayed consumer `feedback_pop` and balanced channel summary

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 03-params-basics/11-params-json-feedback-channel/q1timeline.yml
```
