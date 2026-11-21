"""Common LINQ FIFO selection for both playback and event-driven execution."""
from collections.abc import Iterable

DEFAULT_FEEDBACK_QUEUE_CAPACITY = 32


def feedback_q1_completion(issue_end_ns: int, arrival_ns: int | float) -> int:
    """q1simulator 1.4.1 fetches the instruction, then waits on the arrival's Q1 tick."""
    return max(issue_end_ns, int(arrival_ns // 4) * 4)


def feedback_pop_limitation(identifier: int) -> str | None:
    if 0xFF < identifier <= 0xFFFF:
        return (f"fb_pop_data selector {identifier} exceeds the documented 8-bit LINQ range. "
                "Device behavior for this encodable selector is unverified.")
    return None


def feedback_match_index(identifiers: Iterable[int], requested: int | None) -> int | None:
    """A pull accepts the oldest word; a selective pop discards every preceding ID."""
    return next((index for index, identifier in enumerate(identifiers)
                 if requested is None or identifier == requested), None)


def take_feedback(queue: list[dict], identifier: int | None) -> tuple[dict | None, list[dict]]:
    index = feedback_match_index((item["id"] for item in queue), identifier)
    if index is None:
        discarded = queue.copy()
        queue.clear()
        return None, discarded
    feedback, discarded = queue[index], queue[:index]
    del queue[:index + 1]
    return feedback, discarded
