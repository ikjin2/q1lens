# Feedback Round Trip

This example shows a normal producer-to-consumer LINQ feedback transfer. The
consumer waits long enough before `fb_pop_data`, so channel 16 is balanced and
does not raise a feedback latency diagnostic.

Inspect:

- the producer-side `feedback_com` event
- the consumer-side `feedback_pop` event
- channel 16 marked `balanced` in `feedback_balance`

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 01-getting-started/04-feedback-round-trip/q1timeline.yml
```
