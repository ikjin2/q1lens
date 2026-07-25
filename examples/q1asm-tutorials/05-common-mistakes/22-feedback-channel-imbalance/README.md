# Feedback Channel Imbalance

This common-mistake example sends more channel-16 payloads than the consumer
drains. The timing is late enough to avoid a latency violation, so the useful
signal is the feedback balance summary and FIFO imbalance warning rather than a
timing warning.

Inspect:

- three `feedback_com` events on the producer
- one matched `feedback_pop` event on the consumer
- channel 16 marked `over_produced` in `feedback_balance`
- a `feedback_fifo_imbalance` warning diagnostic

From the tutorial workspace root:

```powershell
python -m q1timeline analyze --project 05-common-mistakes/22-feedback-channel-imbalance/q1timeline.yml
```
