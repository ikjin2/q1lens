from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Iterable

from q1timeline.feedback_fifo import DEFAULT_FEEDBACK_QUEUE_CAPACITY


def validate_simulation(config: dict[str, Any] | None, sequencer_ids: Iterable[str] | None = None) -> dict[str, Any]:
    """Validate scenario inputs before execution, returning an independent config."""
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("simulation must be a mapping")
    _known_fields(config, "simulation", {
        "queue_capacity", "feedback_queue_capacity", "startup_delay_ns", "max_time_ns",
        "inputs", "routes", "trigger_routes", "trigger_truth_tables", "trigger_networks", "acquisition_iq_scale",
        "physical", "digital", "digital_routes",
    })
    result = deepcopy(config)
    ids = set(sequencer_ids) if sequencer_ids is not None else None
    defaults = {"queue_capacity": 32, "feedback_queue_capacity": DEFAULT_FEEDBACK_QUEUE_CAPACITY,
                "max_time_ns": 1_000_000 if "startup_delay_ns" in result else 3_000_000}
    for key, default in defaults.items():
        result.setdefault(key, default)
        _integer(result[key], f"simulation.{key}", minimum=1)
    if "startup_delay_ns" in result:
        _integer(result["startup_delay_ns"], "simulation.startup_delay_ns", minimum=0)
    inputs = result.setdefault("inputs", {})
    if not isinstance(inputs, dict):
        raise ValueError("simulation.inputs must be a mapping")
    _known_fields(inputs, "simulation.inputs", {"feedback", "triggers", "acquisitions", "digital", "digital_acquisitions"})
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
                "triggers": {"target", "time_ns", "rt_time_ns", "address"},
                "acquisitions": {"sequencer", "acquisition_index", "bin_index", "i", "q", "thresholded"},
            }[kind]
            _known_fields(entry, field, allowed)
            target_field = "sequencer" if kind == "acquisitions" else "target"
            _sequencer(entry.get(target_field), f"{field}.{target_field}", ids)
            if kind != "acquisitions":
                if "time_ns" in entry and "rt_time_ns" in entry:
                    raise ValueError(f"{field} must use either time_ns or rt_time_ns")
                time_field = "rt_time_ns" if "rt_time_ns" in entry else "time_ns"
                _integer(entry.get(time_field), f"{field}.{time_field}", minimum=0)
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
    traces = inputs.get("digital", {})
    if not isinstance(traces, dict):
        raise ValueError("simulation.inputs.digital must map sequencers to traces")
    for seq, trace in traces.items():
        field = f"simulation.inputs.digital.{seq}"
        _sequencer(seq, field, ids)
        if not isinstance(trace, dict):
            raise ValueError(f"{field} must be a mapping")
        _known_fields(trace, field, {"initial_level", "transitions"})
        _integer(trace.get("initial_level"), f"{field}.initial_level", minimum=0, maximum=1)
        if not isinstance(trace.get("transitions"), list):
            raise ValueError(f"{field}.transitions must explicitly be a list (empty is a flat trace)")
        previous, level = -1, trace["initial_level"]
        for index, transition in enumerate(trace["transitions"]):
            path = f"{field}.transitions[{index}]"
            if not isinstance(transition, dict):
                raise ValueError(f"{path} must be a mapping")
            _known_fields(transition, path, {"time_ticks", "level"})
            _integer(transition.get("time_ticks"), f"{path}.time_ticks", minimum=0, maximum=2**53-1)
            _integer(transition.get("level"), f"{path}.level", minimum=0, maximum=1)
            if transition["time_ticks"] <= previous or transition["level"] == level:
                raise ValueError(f"{path} requires strictly ordered ticks and a changed binary level")
            previous, level = transition["time_ticks"], transition["level"]
    digital_routes = result.setdefault("digital_routes", [])
    if not isinstance(digital_routes, list):
        raise ValueError("simulation.digital_routes must be a list")
    driven_inputs = set(traces)
    for index, route in enumerate(digital_routes):
        field = f"simulation.digital_routes[{index}]"
        if not isinstance(route, dict):
            raise ValueError(f"{field} must be a mapping")
        _known_fields(route, field, {"source", "target", "delay_ticks"})
        for key in ("source", "target"):
            _sequencer(route.get(key), f"{field}.{key}", ids)
        _integer(route.setdefault("delay_ticks", 0), f"{field}.delay_ticks", minimum=0, maximum=2**53-1)
        target = route["target"]
        if target in driven_inputs:
            raise ValueError(f"{field}.target already has a digital driver: {target}")
        driven_inputs.add(target)
    supplied = inputs.get("digital_acquisitions", [])
    if not isinstance(supplied, list):
        raise ValueError("simulation.inputs.digital_acquisitions must be a list")
    for index, entry in enumerate(supplied):
        field = f"simulation.inputs.digital_acquisitions[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{field} must be a mapping")
        _known_fields(entry, field, {"sequencer", "kind", "acquisition_index", "bin_index", "count", "count_valid", "timetag_ticks", "level"})
        _sequencer(entry.get("sequencer"), field, ids)
        kind = entry.get("kind")
        if kind not in {"acquire_timetags", "acquire_ttl", "acquire_digital"}:
            raise ValueError(f"{field}.kind must name a digital acquisition instruction")
        for key in ("acquisition_index", "bin_index"):
            _integer(entry.get(key), f"{field}.{key}", minimum=0, maximum=31 if key == "acquisition_index" else 2**24-1)
        if "count_valid" in entry and (kind != "acquire_timetags" or type(entry["count_valid"]) is not bool):
            raise ValueError(f"{field}.count_valid must be boolean and applies only to acquire_timetags")
        if kind == "acquire_digital":
            _integer(entry.get("level"), f"{field}.level", minimum=0, maximum=1)
            if "count" in entry or "timetag_ticks" in entry:
                raise ValueError(f"{field} digital sample accepts level only")
        else:
            if entry.get("count_valid") is False:
                if "count" in entry:
                    raise ValueError(f"{field} must omit count for an invalid count result")
            else:
                _integer(entry.get("count"), f"{field}.count", minimum=0, maximum=2**32-1)
            if "level" in entry or kind == "acquire_ttl" and "timetag_ticks" in entry:
                raise ValueError(f"{field} has fields incompatible with {kind}")
            if entry.get("timetag_ticks") is not None:
                _integer(entry["timetag_ticks"], f"{field}.timetag_ticks", minimum=-(2**63), maximum=2**63-1)
                if entry.get("count") == 0:
                    raise ValueError(f"{field} cannot supply a valid tag for zero edges")
    digital = result.setdefault("digital", {})
    if not isinstance(digital, dict):
        raise ValueError("simulation.digital must map sequencers to settings")
    channel_references = tuple(f"first{channel}" for channel in range(8))
    channels = set()
    for seq, settings in digital.items():
        field = f"simulation.digital.{seq}"
        _sequencer(seq, field, ids)
        if not isinstance(settings, dict):
            raise ValueError(f"{field} must be a mapping")
        _known_fields(settings, field, {"time_source", "time_reference", "invalid_time_delta", "module", "channel", "scope_mode", "scope_end_ticks",
                                        "ttl_acq_auto_bin_incr_en", "count_thresholds", "linq_timestamp_origin_ticks",
                                        "forward_trigger_en", "forward_trigger_mode", "forward_trigger_address"})
        enabled = settings.get("forward_trigger_en", False)
        if type(enabled) is not bool:
            raise ValueError(f"{field}.forward_trigger_en must be boolean")
        if enabled or "forward_trigger_mode" in settings:
            if settings.get("forward_trigger_mode") not in ("rising", "falling", "sampled-high", "sampled-low"):
                raise ValueError(f"{field}.forward_trigger_mode must be rising, falling, sampled-high or sampled-low")
        if enabled or "forward_trigger_address" in settings:
            _integer(settings.get("forward_trigger_address"), f"{field}.forward_trigger_address", minimum=1, maximum=15)
        if "scope_mode" in settings and settings["scope_mode"] not in ("timetags-windowed", "timetags"):
            raise ValueError(f"{field}.scope_mode must be timetags-windowed or timetags")
        if settings.get("scope_mode") == "timetags":
            _integer(settings.get("scope_end_ticks"), f"{field}.scope_end_ticks", minimum=1,
                     maximum=min(result["max_time_ns"] * 128, 2**53-1))
        elif "scope_end_ticks" in settings:
            raise ValueError(f"{field}.scope_end_ticks requires continuous timetags scope mode")
        if "linq_timestamp_origin_ticks" in settings:
            _integer(settings["linq_timestamp_origin_ticks"], f"{field}.linq_timestamp_origin_ticks", minimum=-(2**63), maximum=2**63-1)
        if settings.get("time_source", "first") not in ("first", "second", "last"):
            raise ValueError(f"{field}.time_source must be first, second or last")
        reference = settings.get("time_reference", "start")
        if reference not in ("start", "end", "sequencer", *channel_references):
            raise ValueError(f"{field}.time_reference must be start, end, sequencer or first0..first7")
        if reference in channel_references or "module" in settings or "channel" in settings:
            module = settings.get("module")
            if not isinstance(module, str) or not module.strip():
                raise ValueError(f"{field}.module must identify the module for the explicit channel")
            _integer(settings.get("channel"), f"{field}.channel", minimum=0, maximum=7)
            identity = module, settings["channel"]
            if identity in channels:
                raise ValueError(f"{field} duplicates digital channel {identity[1]} on module {module}")
            channels.add(identity)
        if "invalid_time_delta" in settings and settings["invalid_time_delta"] not in ("record_0", "discard", "error"):
            raise ValueError(f"{field}.invalid_time_delta must be record_0, discard or error")
        if type(settings.get("ttl_acq_auto_bin_incr_en", False)) is not bool:
            raise ValueError(f"{field}.ttl_acq_auto_bin_incr_en must be boolean")
        if "count_thresholds" in settings:
            thresholds = settings["count_thresholds"]
            if not isinstance(thresholds, dict):
                raise ValueError(f"{field}.count_thresholds must be a mapping")
            _known_fields(thresholds, field + ".count_thresholds", {"low", "high"})
            for key in ("low", "high"):
                _integer(thresholds.get(key), f"{field}.count_thresholds.{key}", minimum=0, maximum=2**32-1)
            if thresholds["low"] > thresholds["high"]:
                raise ValueError(f"{field}.count_thresholds requires low <= high")
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
    tables = result.setdefault("trigger_truth_tables", {})
    _validate_trigger_truth_tables(tables, channels)
    networks = result.setdefault("trigger_networks", {})
    if not isinstance(networks, dict):
        raise ValueError("simulation.trigger_networks must map names to grid settings")
    for network, settings in networks.items():
        field = f"simulation.trigger_networks.{network}"
        if not isinstance(network, str) or not network.strip() or not isinstance(settings, dict):
            raise ValueError("simulation.trigger_networks must map nonempty names to grid settings")
        _known_fields(settings, field, {"grid_epoch_ns", "grid_epoch"})
        if "grid_epoch_ns" in settings:
            _integer(settings["grid_epoch_ns"], f"{field}.grid_epoch_ns", minimum=0, maximum=(2**53-1)//128)
            if "grid_epoch" in settings:
                raise ValueError(f"{field} accepts one grid epoch source")
        elif "grid_epoch" in settings and settings["grid_epoch"] != "synq":
            raise ValueError(f"{field}.grid_epoch must be synq")
    trigger_routes = result.setdefault("trigger_routes", [])
    if not isinstance(trigger_routes, list):
        raise ValueError("simulation.trigger_routes must be a list")
    digital_receivers = set()
    for index, route in enumerate(trigger_routes):
        field = f"simulation.trigger_routes[{index}]"
        if not isinstance(route, dict):
            raise ValueError(f"{field} must be a mapping")
        _known_fields(route, field, {"source", "source_module", "source_kind", "target", "address", "latency_ns", "thresholded",
                                     "network", "extra_latency_ns"})
        ttl_edge = "source_kind" in route
        if ttl_edge and route["source_kind"] != "ttl_edge":
            raise ValueError(f"{field}.source_kind must be ttl_edge")
        _sequencer(route.get("target"), f"{field}.target", ids)
        _integer(route.get("address"), f"{field}.address", minimum=1, maximum=15)
        if "network" in route:
            network = route["network"]
            if not isinstance(network, str) or network not in networks:
                raise ValueError(f"{field}.network must name a declared trigger network")
            if "latency_ns" in route:
                raise ValueError(f"{field}.latency_ns is only valid on a fixed-latency route")
            _integer(route.setdefault("extra_latency_ns", 0), f"{field}.extra_latency_ns", minimum=0,
                     maximum=(2**53-1)//128)
        else:
            if "extra_latency_ns" in route:
                raise ValueError(f"{field}.extra_latency_ns requires a trigger network")
            _integer(route.get("latency_ns"), f"{field}.latency_ns", minimum=0)
        if ttl_edge:
            if "source_module" in route or "thresholded" in route:
                raise ValueError(f"{field}.source_kind ttl_edge cannot be combined with source_module or thresholded")
            _sequencer(route.get("source"), f"{field}.source", ids)
        elif "source_module" in route:
            module = route["source_module"]
            if "source" in route or "thresholded" in route:
                raise ValueError(f"{field}.source_module cannot be combined with source or thresholded")
            if not isinstance(module, str) or module not in tables:
                raise ValueError(f"{field}.source_module must name a configured trigger truth table")
            if not any(rule["address"] == route["address"] for rule in tables[module]):
                raise ValueError(f"{field}.address is not emitted by the {module!r} truth table")
        else:
            _sequencer(route.get("source"), f"{field}.source", ids)
            _integer(route.setdefault("thresholded", 1), f"{field}.thresholded", minimum=0, maximum=1)
        if ttl_edge or "network" in route or "source_module" in route or digital.get(route.get("source"), {}).get("forward_trigger_en"):
            receiver = (route.get("source_kind"), route.get("network"), route.get("source"), route.get("source_module"),
                        route["target"], route["address"])
            if receiver in digital_receivers:
                raise ValueError(f"{field} duplicates a generated trigger route")
            digital_receivers.add(receiver)
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


def _validate_trigger_truth_tables(tables: Any, channels: set[tuple[str, int]]) -> None:
    field = "simulation.trigger_truth_tables"
    if not isinstance(tables, dict):
        raise ValueError(f"{field} must map QTM modules to lists of rules")
    for module, rules in tables.items():
        if not isinstance(module, str) or not any(name == module for name, _ in channels):
            raise ValueError(f"{field} references an unknown digital module {module!r}")
        if not isinstance(rules, list) or not rules:
            raise ValueError(f"{field}.{module} must have a nonempty list of rules")
        used_channels: set[int] = set()
        for index, rule in enumerate(rules):
            rule_field = f"{field}.{module}[{index}]"
            if not isinstance(rule, dict):
                raise ValueError(f"{rule_field} must be a mapping")
            _known_fields(rule, rule_field, {"address", "when"})
            _integer(rule.get("address"), f"{rule_field}.address", minimum=1, maximum=15)
            conditions = rule.get("when")
            if not isinstance(conditions, dict) or not conditions:
                raise ValueError(f"{rule_field}.when must map channels to classifications")
            normalized: dict[int, str] = {}
            for channel, classification in conditions.items():
                if isinstance(channel, str) and channel in "01234567" and len(channel) == 1:
                    channel = int(channel)
                if type(channel) is not int or (module, channel) not in channels or channel in normalized:
                    raise ValueError(f"{rule_field}.when references an unknown or duplicate digital channel {channel!r}")
                if classification not in ("low", "mid", "high", "invalid"):
                    raise ValueError(f"{rule_field}.when[{channel}] must be low, mid, high or invalid")
                normalized[channel] = classification
            used_channels.update(normalized)
            if len(used_channels) > 4:
                raise ValueError(f"{field}.{module} supports at most four input channels")
            for previous in rules[:index]:
                if not any(channel in previous["when"] and previous["when"][channel] != classification
                           for channel, classification in normalized.items()):
                    raise ValueError(f"{rule_field} overlaps another rule in the same truth table")
            rule["when"] = normalized


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
