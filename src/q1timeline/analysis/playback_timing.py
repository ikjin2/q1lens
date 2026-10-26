"""Queue checks for recorded playback packets, separate from display timing."""
from __future__ import annotations

from dataclasses import replace

from q1timeline.analysis.interpreter import AnalysisState
from q1timeline.analysis.queue_timing import ASSUMED_INITIAL_BUFFER_NS, POSSIBLE_INITIAL_BUFFER_NS, QueueReplayResult, replay_queues
from q1timeline.analysis.values import Concrete, Unknown
from q1timeline.diagnostics import Diagnostic


def annotate_playback_queue_timing(states: list[AnalysisState]) -> None:
    """Annotate snapshot copies without changing raw Q1 or RT coordinates.

    These are executed packet streams; neither static loop expansion nor the
    static unknown-feedback cutoff applies. Terminal Q1/RT stop behavior is
    outside this packet-only model, so absence of a failure is not a safety claim.
    """
    streams = {state.sequencer_id: list(state.rt_packets) for state in states}
    for state in states:
        if not state.stopped and streams[state.sequencer_id]:
            # A partial peer cannot authorize maximal SYNQ prefetch as though
            # its Q1 program had finished producing packets.
            streams[state.sequencer_id].append(replace(
                streams[state.sequencer_id][-1], duration=Unknown("Playback trace is incomplete"),
            ))
    earliest = replay_queues(streams, initial_buffer_ns=ASSUMED_INITIAL_BUFFER_NS)
    prefetched = replay_queues(streams, prefill_sync=True, initial_buffer_ns=POSSIBLE_INITIAL_BUFFER_NS)
    for state in states:
        low, high = earliest[state.sequencer_id], prefetched[state.sequencer_id]
        failure = high.failure if low.failure and high.failure else None
        reasons = list(dict.fromkeys(reason for reason in (low.incomplete_reason, high.incomplete_reason) if reason))
        if high.failure and not low.failure:
            reasons.append("Underflow depends on the unknown initial RT queue prefill or synchronization dwell.")
        if not state.stopped:
            reasons.append("The recorded execution is incomplete.")
        last_packet_end = state.rt_packets[-1].q1_issue_t1 if state.rt_packets else Concrete(0)
        if isinstance(last_packet_end, Concrete) and any(
            event.kind == "q1_issue" and event.meta.get("op") in {"fb_pop_data", "fb_pull_data"}
            and isinstance(event.t0, Concrete) and event.t0.value >= last_packet_end.value
            for event in state.events
        ):
            reasons.append("Feedback waiting after the last recorded RT packet is not checked.")
        status = ("definite_underflow" if failure else "analysis_incomplete" if reasons else
                  "possibly_safe_with_initial_buffer" if low.failure else "not_detected_in_recorded_packets")
        replay = high if low.failure else low
        initial_buffer = {
            "assumed_ns": ASSUMED_INITIAL_BUFFER_NS, "possible_ns": POSSIBLE_INITIAL_BUFFER_NS,
            "required_ns": replay.required_buffer_ns,
            "status": "exhausted" if failure else "tentative" if status == "possibly_safe_with_initial_buffer"
                      else "incomplete" if reasons else "assumed",
        }
        timing = {
            "status": status,
            "scope": "between_recorded_rt_packets",
            "terminal_timing": "not_modeled",
            "assumptions": {
                "queue_depth_limit": 32,
                "startup": "first_packet_enqueue; no_added_idle",
                "initial_buffer_assumed_ns": ASSUMED_INITIAL_BUFFER_NS,
                "initial_buffer_possible_ns": POSSIBLE_INITIAL_BUFFER_NS,
                "initial_buffer_basis": "user_assumption; not_measured",
                "buffer_duration": "one_time_cumulative_q1_supply_gap_credit; never_replenished",
                "same_time_order": "enqueue_before_dequeue",
                "sync": "all_supplied_sequencers; earliest_release_to_maximum_prefetch",
                "feedback": "playback_receipt_assumptions",
                "trigger": "scheduled_receiver_arrival; duration_after_arrival; missed_arrival_incomplete",
                "trigger_clock": "shared_analytical_receiver_arrival; no_startup_offset",
                "zero_duration": "4_ns_then_wait_for_next_packet",
                "display_coordinates": "unchanged_analytical",
            },
            "reasons": reasons,
            "initial_buffer": initial_buffer,
            "earliest_failure": _failure_json(low),
            "prefetched_failure": _failure_json(high),
            "zero_duration_waits": {"earliest": _zero_duration_waits(low), "prefetched": _zero_duration_waits(high)},
        }
        state.metadata["execution"] = {**state.metadata.get("execution", {}), "queue_timing": timing}
        if failure:
            state.diagnostics.append(Diagnostic(
                severity="warning", category="playback_queue_underflow",
                message="RT queue underflow in the recorded path under the current playback assumptions.",
                source=failure.packet.source,
                details={"scope": timing["scope"], "assumptions": timing["assumptions"], **_failure_json(high)},
            ))
        elif status == "possibly_safe_with_initial_buffer":
            state.diagnostics.append(Diagnostic(
                severity="info", category="initial_rt_buffer_estimate",
                message="Q1 supply gaps exceed the assumed 1 ms buffer, but may be covered by an initial buffer up to 2 ms.",
                source=low.failure.packet.source, details={"scope": timing["scope"], **initial_buffer},
            ))
        elif low.failure or high.failure:
            state.diagnostics.append(Diagnostic(
                severity="info", category="playback_queue_timing_uncertain",
                message="RT queue underflow depends on the unknown initial queue prefill or synchronization dwell.",
                source=(low.failure or high.failure).packet.source,
                details={"scope": timing["scope"], "assumptions": timing["assumptions"]},
            ))


def _failure_json(result: QueueReplayResult) -> dict | None:
    failure = result.failure
    return None if failure is None else {
        "rt_packet_id": failure.packet.id, "enqueue_ns": failure.enqueue_ns,
        "dequeue_ns": failure.dequeue_ns, "slack_ns": failure.slack_ns,
    }


def _zero_duration_waits(result: QueueReplayResult) -> list[dict]:
    waits = []
    for previous, current in zip(result.records, result.records[1:]):
        trigger = previous.packet.meta.get("trigger_assumption", {}) if previous.packet.op == "wait_trigger" else {}
        duration = trigger.get("duration_ns", previous.packet.duration.value)
        start = trigger.get("arrival_ns", previous.dequeue_ns)
        if duration == 0 and previous.packet.op != "wait_sync" and current.dequeue_ns > start + 4:
            waits.append({"after_packet_id": previous.packet.id, "next_packet_id": current.packet.id,
                          "wait_ns": current.dequeue_ns - start - 4})
    return waits
