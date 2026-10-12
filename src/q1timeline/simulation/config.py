from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Iterable


def validate_simulation(config: dict[str, Any] | None, sequencer_ids: Iterable[str] | None = None) -> dict[str, Any]:
    """Validate scenario inputs before execution, returning an independent config."""
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("simulation must be a mapping")
    _known_fields(config, "simulation", {
        "queue_capacity", "feedback_queue_capacity", "startup_delay_ns", "max_time_ns",
        "inputs", "routes", "trigger_routes", "acquisition_iq_scale", "physical",
    })
    result = deepcopy(config)
    ids = set(sequencer_ids) if sequencer_ids is not None else None
    defaults = {"queue_capacity": 32, "feedback_queue_capacity": 32,
                "startup_delay_ns": 200, "max_time_ns": 1_000_000}
    for key, default in defaults.items():
        result.setdefault(key, default)
        _integer(result[key], f"simulation.{key}", minimum=0 if key == "startup_delay_ns" else 1)
    inputs = result.setdefault("inputs", {})
    if not isinstance(inputs, dict):
        raise ValueError("simulation.inputs must be a mapping")
    _known_fields(inputs, "simulation.inputs", {"feedback", "triggers", "acquisitions"})
    for kind in ("feedback", "triggers", "acquisitions"):
        entries = inputs.setdefault(kind, [])
        if not isinstance(entries, list):
            raise ValueError(f"simulation.inputs.{kind} must be a list")
        for index, entry in enumerate(entries):
            field = f"simulation.inputs.{kind}[{index}]"
            if not isinstance(entry, dict):
                raise ValueError(f"{field} must be a mapping")
            allowed = {
                "feedback": {"target", "time_ns", "id", "value"},
                "triggers": {"target", "time_ns", "address"},
                "acquisitions": {"sequencer", "acquisition_index", "bin_index", "i", "q", "thresholded"},
            }[kind]
            _known_fields(entry, field, allowed)
            target_field = "sequencer" if kind == "acquisitions" else "target"
            _sequencer(entry.get(target_field), f"{field}.{target_field}", ids)
            if kind != "acquisitions":
                _integer(entry.get("time_ns"), f"{field}.time_ns", minimum=0)
            if kind == "feedback":
                _integer(entry.get("id"), f"{field}.id", minimum=1, maximum=255)
                _integer(entry.get("value"), f"{field}.value", minimum=-(2**31), maximum=2**32 - 1)
            elif kind == "triggers":
                _integer(entry.get("address"), f"{field}.address", minimum=1, maximum=15)
            else:
                for key in ("acquisition_index", "bin_index"):
                    _integer(entry.get(key), f"{field}.{key}", minimum=0)
                for key in ("i", "q"):
                    if key in entry:
                        _finite(entry[key], f"{field}.{key}")
                if "thresholded" in entry:
                    _integer(entry["thresholded"], f"{field}.thresholded", minimum=0, maximum=1)
                if not any(key in entry for key in ("i", "q", "thresholded")):
                    raise ValueError(f"{field} must supply i/q or thresholded results")
    routes = result.setdefault("routes", [])
    if not isinstance(routes, list):
        raise ValueError("simulation.routes must be a list")
    for index, route in enumerate(routes):
        field = f"simulation.routes[{index}]"
        if not isinstance(route, dict):
            raise ValueError(f"{field} must be a mapping")
        _known_fields(route, field, {"source", "target", "id", "latency_ns"})
        for key in ("source", "target"):
            _sequencer(route.get(key), f"{field}.{key}", ids)
        if "id" in route:
            _integer(route["id"], f"{field}.id", minimum=1, maximum=255)
        _integer(route.get("latency_ns"), f"{field}.latency_ns", minimum=0)
    trigger_routes = result.setdefault("trigger_routes", [])
    if not isinstance(trigger_routes, list):
        raise ValueError("simulation.trigger_routes must be a list")
    for index, route in enumerate(trigger_routes):
        field = f"simulation.trigger_routes[{index}]"
        if not isinstance(route, dict):
            raise ValueError(f"{field} must be a mapping")
        _known_fields(route, field, {"source", "target", "address", "latency_ns", "thresholded"})
        for key in ("source", "target"):
            _sequencer(route.get(key), f"{field}.{key}", ids)
        _integer(route.get("address"), f"{field}.address", minimum=1, maximum=15)
        _integer(route.get("latency_ns"), f"{field}.latency_ns", minimum=0)
        _integer(route.setdefault("thresholded", 1), f"{field}.thresholded", minimum=0, maximum=1)
    iq_scales = result.setdefault("acquisition_iq_scale", {})
    if not isinstance(iq_scales, dict):
        raise ValueError("simulation.acquisition_iq_scale must be a mapping")
    for seq, scale in iq_scales.items():
        _sequencer(seq, "simulation.acquisition_iq_scale", ids)
        _finite(scale, f"simulation.acquisition_iq_scale.{seq}")
        if scale <= 0:
            raise ValueError("simulation.acquisition_iq_scale values must be positive")
    physical = result.setdefault("physical", {})
    if not isinstance(physical, dict):
        raise ValueError("simulation.physical must be a mapping")
    if physical:
        from q1timeline.simulation.signals import validate_physical_config

        result["physical"] = validate_physical_config(physical, ids)
    return result


def _known_fields(value: dict, field: str, allowed: set[str]) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"Unknown {field} fields: {', '.join(sorted(map(str, unknown)))}")


def _integer(value: Any, field: str, *, minimum: int, maximum: int | None = None) -> None:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        bounds = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise ValueError(f"{field} must be an integer {bounds}")


def _finite(value: Any, field: str) -> None:
    try:
        finite = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{field} must be a finite number")


def _sequencer(value: Any, field: str, ids: set[str] | None) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must name a sequencer")
    if ids is not None and value not in ids:
        raise ValueError(f"{field} references unknown sequencer {value!r}")
