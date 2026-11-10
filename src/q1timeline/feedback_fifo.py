"""Common LINQ FIFO selection for both playback and event-driven execution."""

DEFAULT_FEEDBACK_QUEUE_CAPACITY = 32


def feedback_pop_limitation(identifier: int) -> str | None:
    if 0xFF < identifier <= 0xFFFF:
        return (f"fb_pop_data selector {identifier} exceeds the documented 8-bit LINQ range. "
                "Device behavior for this encodable selector is unverified.")
    return None


def take_feedback(queue: list[dict], identifier: int | None) -> tuple[dict | None, list[dict]]:
    """Pull the oldest entry, or discard entries preceding a selective pop."""
    index = 0 if identifier is None else next(
        (index for index, item in enumerate(queue) if item["id"] == identifier), len(queue)
    )
    feedback = queue[index] if index < len(queue) else None
    discarded = queue[:index]
    del queue[:index + (feedback is not None)]
    return feedback, discarded
