"""Project-scoped, explicit external-input assumptions for analytical playback."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Iterable


def validate_external_inputs(config: dict | None, sequencer_ids: Iterable[str]) -> dict:
    if config is None:
        config = {}
    if not isinstance(config, dict) or set(config) - {"triggers"}:
        raise ValueError("external_inputs must contain only triggers")
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
    return {"triggers": deepcopy(sources)}


def resolve_trigger_sources(config: dict, params: dict[str, Any]) -> list[dict]:
    result = []
    for source in config["triggers"]:
        address = source["address"]
        resolved = params.get(address) if isinstance(address, str) else address
        if type(resolved) is not int or not 1 <= resolved <= 15:
            raise ValueError(f"External trigger {source['id']!r}: address {address!r} must resolve to an integer in 1..15")
        result.append({**deepcopy(source), "resolved_address": resolved})
    return result
