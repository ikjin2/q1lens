"""Baseband output routing. All sample amplitudes and offsets use peak full scale."""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import chain, repeat
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from q1timeline.output import Output


_HARDWARE = {"QCM": (4, 2.5), "QRM": (2, 0.5)}


@dataclass(frozen=True)
class ModuleConfig:
    name: str
    module_type: str
    channel_map: dict[str, dict[str, str]]
    output_offsets: dict[str, float] = field(default_factory=dict)
    sequencer_settings: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self):
        if self.module_type not in _HARDWARE or not isinstance(self.name, str) or not self.name:
            raise ValueError("An output module needs a name and baseband type QCM or QRM")
        valid = {f"out{index}" for index in range(_HARDWARE[self.module_type][0])}
        if not isinstance(self.channel_map, Mapping) or not isinstance(self.output_offsets, Mapping):
            raise ValueError("channel_map and output_offsets must be mappings")
        for seq, routes in self.channel_map.items():
            if (not isinstance(seq, str) or not seq or not isinstance(routes, Mapping)
                    or routes.keys() - valid or any(path not in ("I", "Q") for path in routes.values())):
                raise ValueError(f"Invalid {self.module_type} output routing for {seq!r}")
        for port, value in self.output_offsets.items():
            if port not in valid or type(value) not in (float, int) or not -1 <= value <= 1:
                raise ValueError(f"Invalid normalized output offset for {self.name}.{port}")

    @property
    def ports(self) -> list[str]:
        return sorted(set(self.output_offsets).union(*(routes.keys() for routes in self.channel_map.values())))

    def samples(self, outputs: Mapping[str, Output], port: str, t0: int, t1: int, *,
                clip: bool = True, time_offsets: Mapping[str, int] | None = None) -> list[float]:
        """Mix on [t0,t1), where aligned = local + time_offsets[seq] in ns.

        A sequencer contributes from its local zero onwards. Module DC is present
        throughout the window. With no alignment map all sequencers start at zero.
        """
        if port not in self.ports:
            raise ValueError(f"No configured port {self.name}.{port}")
        if type(t0) is not int or type(t1) is not int or t1 < t0 or (time_offsets is None and t0 < 0):
            raise ValueError("Expected an ordered integer-ns window; negative times require alignment offsets")
        if time_offsets is not None and not isinstance(time_offsets, Mapping):
            raise ValueError("time_offsets must map sequencer IDs to integer ns")
        paths = [repeat(self.output_offsets.get(port, 0.0), t1 - t0)]
        for seq, routes in self.channel_map.items():
            if port in routes:
                origin = 0 if time_offsets is None else time_offsets.get(seq)
                if type(origin) is not int:
                    raise ValueError(f"Missing integer alignment offset for {seq}")
                start = max(t0, origin)
                if start >= t1:
                    continue
                path = outputs[seq].samples(start - origin, t1 - origin, stage="filtered")[int(routes[port] == "Q")]
                paths.append(chain(repeat(0.0, start - t0), path))
        # A routing table's insertion order must not change a physical port sample.
        total = map(math.fsum, zip(*paths))
        return [min(1.0, max(-1.0, sample)) for sample in total] if clip else list(total)

    @classmethod
    def from_snapshot(cls, name: str, module_type: str, snapshot: dict, sequencers: Mapping[str, int]) -> ModuleConfig:
        """Read the selected QCoDeS module snapshot and map sequencer IDs to hardware slots."""
        if module_type not in _HARDWARE:
            raise ValueError(f"Unsupported baseband module type {module_type!r}")
        count, peak_volts = _HARDWARE[module_type]
        offsets = {}
        routes = {}
        settings = {}
        for port in range(count):
            volts = _snapshot_value(snapshot, f"out{port}_offset")
            if type(volts) not in (float, int) or not math.isfinite(volts):
                raise ValueError(f"Expected numeric out{port}_offset in volts")
            offsets[f"out{port}"] = volts / peak_volts
        for seq, index in sequencers.items():
            if type(index) is not int or not 0 <= index <= 5:
                raise ValueError(f"{seq} requires sequencer_index in 0..5 for its snapshot")
            sub = snapshot.get("submodules", {}).get(f"sequencer{index}")
            routes[seq] = {}
            for port in range(count):
                path = _snapshot_value(sub, f"connect_out{port}")
                if path != "off":
                    routes[seq][f"out{port}"] = path
            # Qblox ControlSequencer / ReadoutSequencer QCoDeS parameter names.
            # Cropped snapshots can omit these settings; only import recorded values.
            params = sub["parameters"]
            static = {}
            for key, prefix, default in (("static_gain", "gain_awg_path", 1), ("static_offset", "offset_awg_path", 0)):
                if any(prefix + path in params for path in "01"):
                    pair = [_snapshot_number(sub, prefix + path, -1, 1) if prefix + path in params else default
                            for path in "01"]
                    static[key] = pair
            if "mod_en_awg" in params:
                enabled = _snapshot_value(sub, "mod_en_awg")
                if type(enabled) is not bool:
                    raise ValueError("Expected boolean QCoDeS mod_en_awg")
                static["nco_enabled"] = enabled
            if "nco_freq" in params:
                hz = _snapshot_number(sub, "nco_freq", -500_000_000, 500_000_000)
                static["nco_frequency_hz"] = round(hz * 4) / 4
            if "nco_phase_offs" in params:
                degrees = _snapshot_number(sub, "nco_phase_offs", 0, 360)
                static["static_phase_steps"] = round(degrees * (1_000_000_000 / 360))
            if static:
                settings[seq] = static
        return cls(name, module_type, routes, offsets, settings)


def _snapshot_value(node, key):
    param = node.get("parameters", {}).get(key) if isinstance(node, dict) else None
    if not isinstance(param, dict) or param.get("value") is None:
        raise ValueError(f"Missing QCoDeS snapshot parameter {key}")
    return param["value"]


def _snapshot_number(node, key, low, high):
    value = _snapshot_value(node, key)
    if type(value) not in (int, float) or not low <= value <= high:
        raise ValueError(f"Expected QCoDeS {key} in [{low}, {high}]")
    return value
