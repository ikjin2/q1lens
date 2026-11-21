"""Project-scoped receiver inputs for analytical Concrete and Play execution."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Iterable

from q1timeline.feedback_fifo import DEFAULT_FEEDBACK_QUEUE_CAPACITY
from q1timeline.q1asm.core_devices import ExternalInputs
from q1timeline.q1asm.trigger_counters import TRIGGER_COUNTER_PARAMETERS


def validate_external_inputs(config: dict | None, sequencer_ids: Iterable[str]) -> dict:
    if config is None:
        config = {}
    if not isinstance(config, dict) or set(config) - {"triggers", "feedback"}:
        raise ValueError("external_inputs must contain only triggers and feedback")
    sources = config.get("triggers", [])
    if not isinstance(sources, list):
        raise ValueError("external_inputs.triggers must be a list")
    ids, seen = set(sequencer_ids), set()
    for index, source in enumerate(sources):
        field = f"external_inputs.triggers[{index}]"
        if not isinstance(source, dict) or set(source) != {"id", "address", "targets", "times_ns"}:
            raise ValueError(f"{field} must contain id, address, targets and times_ns")
        name = source["id"]
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError(f"{field}.id must be a nonempty string of at most 128 characters")
        if name in seen:
            raise ValueError(f"{field}.id duplicates {name!r}")
        seen.add(name)
        address = source["address"]
        if not (type(address) is int and 1 <= address <= 15) and not (
            isinstance(address, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", address)
        ):
            raise ValueError(f"{field}.address must be 1..15 or a parameter name")
        targets = source["targets"]
        if not isinstance(targets, list) or not targets or any(not isinstance(t, str) or not t.strip() for t in targets):
            raise ValueError(f"{field}.targets must list sequencer IDs")
        if len(set(targets)) != len(targets):
            raise ValueError(f"{field}.targets contains duplicates")
        for target in targets:
            if target not in ids:
                raise ValueError(f"{field}.targets references unknown sequencer {target!r}")
        times = source["times_ns"]
        if not isinstance(times, list) or any(type(t) is not int or not 0 <= t <= 9007199254740991 for t in times):
            raise ValueError(f"{field}.times_ns must list nonnegative safe integers")
        if any(a >= b for a, b in zip(times, times[1:])):
            raise ValueError(f"{field}.times_ns must be strictly increasing")
    receivers = config.get("feedback", [])
    if not isinstance(receivers, list):
        raise ValueError("external_inputs.feedback must be a list")
    seen.clear()
    for index, receiver in enumerate(receivers):
        field = f"external_inputs.feedback[{index}]"
        if not isinstance(receiver, dict) or set(receiver) != {"target", "arrivals"}:
            raise ValueError(f"{field} must contain target and arrivals")
        target = receiver["target"]
        if not isinstance(target, str) or target not in ids:
            raise ValueError(f"{field}.target references unknown sequencer {target!r}")
        if target in seen:
            raise ValueError(f"{field}.target duplicates {target!r}")
        seen.add(target)
        arrivals = receiver["arrivals"]
        if not isinstance(arrivals, list):
            raise ValueError(f"{field}.arrivals must be a list (empty means no arrivals)")
        for entry_index, entry in enumerate(arrivals):
            path = f"{field}.arrivals[{entry_index}]"
            if not isinstance(entry, dict) or set(entry) != {"time_ns", "id", "value"}:
                raise ValueError(f"{path} must contain time_ns, id and value")
            if type(entry["time_ns"]) is not int or not 0 <= entry["time_ns"] <= 2**53 - 1:
                raise ValueError(f"{path}.time_ns must be a nonnegative safe integer")
            if type(entry["id"]) is not int or not 1 <= entry["id"] <= 255:
                raise ValueError(f"{path}.id must be an integer from 1 to 255")
            if type(entry["value"]) is not int or not -(2**31) <= entry["value"] <= 2**32 - 1:
                raise ValueError(f"{path}.value must be a 32-bit signed or unsigned integer")
    result = {"triggers": deepcopy(sources)}
    if receivers:
        result["feedback"] = deepcopy(receivers)
    return result


def resolve_feedback_sources(config: dict) -> list[dict]:
    return [{"target": receiver["target"], "arrivals": [
        {**entry, "event_id": f"external-linq:{receiver['target']}:{index}"}
        for index, entry in enumerate(receiver["arrivals"])
    ]} for receiver in config.get("feedback", [])]


def resolve_trigger_sources(config: dict, params: dict[str, Any]) -> list[dict]:
    result = []
    for source in config["triggers"]:
        address = source["address"]
        resolved = params.get(address) if isinstance(address, str) else address
        if type(resolved) is not int or not 1 <= resolved <= 15:
            raise ValueError(f"External trigger {source['id']!r}: address {address!r} must resolve to an integer in 1..15")
        result.append({**deepcopy(source), "resolved_address": resolved})
    return result


def concrete_receiver_inputs(
    config: dict, sequencer: str, params: dict, settings: dict | None = None,
    feedback_queue_capacity: int = DEFAULT_FEEDBACK_QUEUE_CAPACITY,
) -> ExternalInputs:
    sources = [s for s in config.get("triggers", ()) if sequencer in s["targets"]]
    triggers = resolve_trigger_sources({"triggers": sources}, params)
    arrivals = sorted((time, s["resolved_address"]) for s in triggers for time in s["times_ns"])
    feedback = next((s["arrivals"] for s in config.get("feedback", ()) if s["target"] == sequencer), ())
    return ExternalInputs(
        trigger_arrivals=tuple(arrivals) if triggers else None,
        scheduled_addresses=frozenset(s["resolved_address"] for s in triggers),
        feedback_arrivals=tuple((a["time_ns"], a["id"], a["value"] & 0xFFFFFFFF)
                                for a in sorted(feedback, key=lambda a: a["time_ns"])),
        trigger_settings=tuple((key, value) for key, value in (settings or {}).items() if key in TRIGGER_COUNTER_PARAMETERS),
        feedback_queue_capacity=feedback_queue_capacity,
    )
