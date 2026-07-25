# Feedback Channel Imbalance Fixed

This before/after example fixes `05-common-mistakes/22-feedback-channel-imbalance` by
draining the same number of channel-16 payloads that the producer sends.

Inspect:

- two producer `feedback_com` events
- two consumer `feedback_pop` events
- channel 16 marked `balanced`

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 06-before-after-fixes/23-feedback-channel-imbalance-fixed/q1timeline.yml
```
