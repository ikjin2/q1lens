"""Bounded, explicitly configured classical signal model (stdlib only).

Q1ASM scaling/phase concepts follow the official Q1ASM guide. This is a
floating-point sampled envelope model, not firmware, calibrated electronics,
or a quantum device model. Filter coefficients describe the numerical model,
not a translation of device predistortion configuration.
"""
from __future__ import annotations

import copy
import math
import random
from array import array
from collections import deque
from operator import mul
from typing import Any


_OUTPUT_STAGES = ("waveform", "gain_offset", "nco", "fir", "filtered", "dac")
MODEL = "q1lens-sampled-envelope-v1"
PHASE_TICKS_PER_TURN = 4_000_000_000  # quarter-Hz frequency word × elapsed ns
MAX_TOTAL_SAMPLES = 1_000_000
SOURCES = [
    "https://docs.qblox.com/en/main/products/qblox_instruments/q1/index.html",
    "https://docs.qblox.com/en/main/products/qblox_instruments/tutorials/QRM/020_binned_acquisition.html",
]
ASSUMPTIONS = [
    "Classical numerical model; no hardware calibration or quantum-device dynamics.",
    "Waveform/weight memory uses 1 ns samples; sampling uses zero-order hold on the configured grid.",
    "Play replaces the previous waveform; data ends in zero while the configured offset persists.",
    "Pipeline: waveform -> gain/offset -> ideal complex NCO -> FIR -> IIR -> clipped signed DAC.",
    "Gain/offset Q1 integers are divided by 32768; frequency integers by 4 Hz; phase integers by 1e9 turns.",
    "FIR/IIR coefficients apply identically to I and Q, with zero initial conditions; no hardware filter latency is inferred.",
    "ADC clips/quantizes normalized inputs. Integration sums sample value times elapsed ns; threshold compares the rotated integral directly, without dividing by duration.",
    "Threshold units are normalized full scale * ns. Weight samples are summed at their 1 ns memory resolution within each held ADC grid cell.",
    "Without a configured integration length, nonweighted acquisition uses its instruction duration as an explicit model fallback.",
    "RF is an ideal complex envelope with a declared LO; no GHz carrier samples or RF transfer function are inferred.",
    "Loopback transports DAC complex baseband directly; LO metadata does not create or demodulate a carrier.",
    "A supplied trace is treated as normalized ADC input; a measured label is a user claim, not measurement verification.",
    "Export is a snapshot of all rendered history; its end time requests additional sampling, not a cursor rewind.",
]


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _integer(value: Any, name: str, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"{name} must be an integer in {lower}..{upper}")
    return value


def _mapping(value: Any, name: str, keys: set[str]) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    unknown = set(value) - keys
    if unknown:
        raise ValueError(f"Unknown {name} fields: {', '.join(sorted(map(str, unknown)))}")
    return value


def _numbers(value: Any, name: str, maximum: int, *, empty: bool = False) -> list[float]:
    if not isinstance(value, list) or len(value) > maximum or (not value and not empty):
        raise ValueError(f"{name} must be a list of {'0' if empty else '1'}..{maximum} finite numbers")
    return [_number(item, name) for item in value]


def validate_physical_config(config: dict, sequencer_ids=None) -> dict:
    """Validate/copy simulation.physical, without reading files or making data."""
    _mapping(config, "physical", {"sample_period_ns", "max_samples", "max_export_samples", "seed", "sequencers"})
    result = copy.deepcopy(config)
    dt = _integer(result.setdefault("sample_period_ns", 1), "sample_period_ns", 1, 1_000_000)
    _integer(result.setdefault("max_samples", 100_000), "max_samples", 1, MAX_TOTAL_SAMPLES)
    _integer(result.setdefault("max_export_samples", 2000), "max_export_samples", 2, 10_000)
    _integer(result.setdefault("seed", 0), "seed", 0, 2**32 - 1)
    configs = result.setdefault("sequencers", {})
    if not isinstance(configs, dict) or len(configs) > 64:
        raise ValueError("physical.sequencers must be a mapping with at most 64 sequencers")
    known = set(sequencer_ids) if sequencer_ids is not None else None
    if known is not None and set(configs) - known:
        raise ValueError("physical.sequencers contains an unknown sequencer")
    input_samples = 0
    for seq, settings in configs.items():
        if not isinstance(seq, str) or not seq:
            raise ValueError("physical sequencer IDs must be nonempty strings")
        _mapping(settings, f"physical.sequencers.{seq}", {
            "dac_bits", "adc_bits", "nco_frequency_hz", "nco_phase_turns", "lo_frequency_hz",
            "gain", "offset", "fir", "iir", "input", "integration_length_ns", "threshold",
            "threshold_rotation_deg", "demodulate", "netzero",
        })
        for field, default in (("dac_bits", 16), ("adc_bits", 12)):
            _integer(settings.setdefault(field, default), field, 2, 24)
        for field in ("nco_frequency_hz", "nco_phase_turns", "lo_frequency_hz", "threshold", "threshold_rotation_deg"):
            settings[field] = _number(settings.get(field, 0), field)
        if abs(settings["nco_frequency_hz"]) >= 0.5e9 / dt:
            raise ValueError("nco_frequency_hz must be below the configured sample-grid Nyquist frequency")
        if type(settings.setdefault("demodulate", False)) is not bool:
            raise ValueError("demodulate must be a boolean")
        for field, default in (("gain", [1, 1]), ("offset", [0, 0])):
            settings[field] = _numbers(settings.get(field, default), field, 2)
            if len(settings[field]) != 2:
                raise ValueError(f"{field} needs two paths")
        settings["fir"] = _numbers(settings.get("fir", [1]), "fir", 64)
        iir = settings.setdefault("iir", {"b": [1], "a": [1]})
        _mapping(iir, "iir", {"a", "b"})
        for field in ("a", "b"):
            iir[field] = _numbers(iir.get(field, [1]), f"iir.{field}", 16)
        if iir["a"][0] == 0:
            raise ValueError("iir.a[0] must not be zero")
        if "integration_length_ns" in settings:
            _integer(settings["integration_length_ns"], "integration_length_ns", 1, dt * result["max_samples"])
        if "netzero" in settings:
            nz = _mapping(settings["netzero"], f"physical.sequencers.{seq}.netzero", {"module_type", "outputs"})
            if nz.get("module_type") != "QCM":
                raise ValueError("netzero requires explicit baseband QCM module_type")
            if not isinstance(nz.get("outputs"), list) or not nz["outputs"]:
                raise ValueError("netzero.outputs must explicitly map eligible outputs")
            outputs, paths = set(), set()
            for output in nz["outputs"]:
                _mapping(output, "netzero.outputs[]", {"output", "path", "enabled", "ihp_enabled", "amplitude", "slew_rate_per_s"})
                number = _integer(output.get("output"), "netzero.output", 0, 3)
                path = _integer(output.get("path"), "netzero.path", 0, 1)
                if number in outputs or path in paths:
                    raise ValueError("netzero outputs and paths must be unique per sequencer")
                outputs.add(number)
                paths.add(path)
                for key in ("enabled", "ihp_enabled"):
                    if type(output.get(key)) is not bool:
                        raise ValueError(f"netzero.{key} must be explicitly boolean")
                for key, maximum in (("amplitude", 1), ("slew_rate_per_s", 2.5e8)):
                    if key in output and not 0 < _number(output[key], f"netzero.{key}") <= maximum:
                        raise ValueError(f"netzero.{key} must be positive and at most {maximum}")
        if "input" not in settings:
            continue
        inp = settings["input"]
        _mapping(inp, "input", {"kind", "i", "q", "sample_period_ns", "start_ns", "source", "measured",
                                 "sequencer", "delay_ns", "gain", "phase_turns", "noise_std"})
        kind = inp.get("kind")
        if kind not in ("trace", "loopback", "constant"):
            raise ValueError("input.kind must be trace, loopback, or constant")
        fields = {
            "constant": {"kind", "i", "q", "noise_std"},
            "trace": {"kind", "i", "q", "sample_period_ns", "start_ns", "source", "measured", "noise_std"},
            "loopback": {"kind", "sequencer", "delay_ns", "gain", "phase_turns", "noise_std"},
        }
        _mapping(inp, f"physical.sequencers.{seq}.input", fields[kind])
        noise = _number(inp.setdefault("noise_std", 0), "input.noise_std")
        if noise < 0:
            raise ValueError("input.noise_std must be non-negative")
        if kind == "trace":
            for field in ("i", "q"):
                inp[field] = _numbers(inp.get(field, []), f"input.{field}", MAX_TOTAL_SAMPLES)
            if len(inp["i"]) != len(inp["q"]):
                raise ValueError("input trace I/Q lengths must match")
            input_samples += len(inp["i"]) + len(inp["q"])
            if input_samples > MAX_TOTAL_SAMPLES:
                raise ValueError("Total supplied input samples exceed the model limit")
            _integer(inp.setdefault("sample_period_ns", 1), "input.sample_period_ns", 1, 1_000_000)
            _integer(inp.setdefault("start_ns", 0), "input.start_ns", 0, 2**53 - 1)
            if type(inp.setdefault("measured", False)) is not bool:
                raise ValueError("input.measured must be a boolean")
            if not isinstance(inp.get("source"), str) or not inp["source"].strip():
                raise ValueError("input trace requires a source description")
        elif kind == "constant":
            for field in ("i", "q"):
                inp[field] = _number(inp.get(field, 0), f"input.{field}")
        else:
            if not isinstance(inp.get("sequencer"), str) or (known is not None and inp["sequencer"] not in known):
                raise ValueError("loopback input.sequencer must reference a known sequencer")
            _integer(inp.setdefault("delay_ns", 0), "input.delay_ns", 0, 2**53 - 1)
            for field, default in (("gain", 1), ("phase_turns", 0)):
                inp[field] = _number(inp.get(field, default), f"input.{field}")
    return result


def _quantize_with_clipping(value: float, scale: int) -> tuple[float, bool]:
    if -1 <= value < 1:
        code = round(value * scale)
        if code < scale:
            return code / scale, False
    # A value below +1 can still round to the first unrepresentable code.
    return ((scale - 1) / scale if value >= 0 else -1.0), True


def _quantize(value: float, bits: int) -> float:
    return _quantize_with_clipping(value, 1 << (bits - 1))[0]


def _convert_iq(i: float, q: float, bits: int) -> tuple[complex, bool]:
    scale = 1 << (bits - 1)
    i, clipped_i = _quantize_with_clipping(i, scale)
    q, clipped_q = _quantize_with_clipping(q, scale)
    return complex(i, q), clipped_i or clipped_q


def _phase_turns(ticks: int | float) -> float:
    return (ticks % PHASE_TICKS_PER_TURN) / PHASE_TICKS_PER_TURN


def _scaled_ticks(value: float, scale: int) -> int | float:
    ticks = value * scale
    return int(ticks) if ticks.is_integer() else ticks


# Common binary phases reuse the same libm values. Exact quadrants keep a zero on the orthogonal axis.
_GRID_ROTATIONS = tuple(
    (1 + 0j, 1j, -1 + 0j, -1j)[k // 8] if k % 8 == 0
    else complex(math.cos(math.tau * (k / 32)), math.sin(math.tau * (k / 32)))
    for k in range(32)
)


def _rotation(turns: float) -> complex:
    fraction = turns % 1
    slot = fraction * 32
    if slot.is_integer():
        return _GRID_ROTATIONS[int(slot) % 32]
    angle = math.tau * fraction
    return complex(math.cos(angle), math.sin(angle))


class _Signal:
    def __init__(self, config: dict, seed: str):
        self.config = config
        self.events: list[tuple[int, str, list[int], dict]] = []
        self.event_index = 0
        self.stages = {stage: (array("d"), array("d")) for stage in (*_OUTPUT_STAGES, "adc", "demodulated")}
        self.output_paths = tuple(self.stages[stage] for stage in _OUTPUT_STAGES)
        self.phases = array("d")
        self.gain = config["gain"]
        self.offset = config["offset"]
        self.frequency_word = _scaled_ticks(config["nco_frequency_hz"], 4)
        turns = config["nco_phase_turns"]
        self.phase_offset_ticks = 0 if turns.is_integer() else _scaled_ticks(turns, PHASE_TICKS_PER_TURN) % PHASE_TICKS_PER_TURN
        self.phase_time = 0
        self.accumulated_ticks = 0
        self.play: tuple[int, list[float], list[float]] | None = None
        self.fir_history = deque(maxlen=len(config["fir"]))
        self.iir_inputs = deque(maxlen=len(config["iir"]["b"]))
        self.iir_outputs = deque(maxlen=max(1, len(config["iir"]["a"]) - 1))
        self.random = random.Random(seed)
        self.adc_missing: set[int] = set()
        self.acquisitions: list[dict] = []
        self.bins: dict[tuple[int, int], dict] = {}
        self.dac_clipped = 0
        self.adc_clipped = 0
        self.netzero = {item["output"]: {"config": item, "area": 0.0, "ihp_area": 0.0, "pulse": None}
                        for item in config.get("netzero", {}).get("outputs", [])}
        self.netzero_results: list[dict] = []
        if self.netzero:
            self.stages["netzero"] = (array("d"), array("d"))


class SignalModel:
    def __init__(self, config: dict, sequences: dict[str, dict]):
        if not isinstance(sequences, dict):
            raise ValueError("sequences must be a sequencer-to-memory mapping")
        if any(not isinstance(seq, str) or not seq for seq in sequences):
            raise ValueError("Sequence memory IDs must be nonempty strings")
        self.config = validate_physical_config(config, sequences)
        self.dt = self.config["sample_period_ns"]
        self._rendered_end_ns = 0
        self.sequences = sequences
        self.signals: dict[str, _Signal] = {}
        self.waveforms: dict[str, dict[int, list[float]]] = {}
        self.weights: dict[str, dict[int, list[float]]] = {}
        self.acquisition_definitions: dict[str, dict[int, int]] = {}
        if len(sequences) > 64:
            raise ValueError("The signal model supports at most 64 sequencers")
        defaults = validate_physical_config({"sample_period_ns": self.dt, "sequencers": {"default": {}}})["sequencers"]["default"]
        memory_samples = 0
        for seq, sequence in sequences.items():
            if not isinstance(sequence, dict):
                raise ValueError(f"Sequence memory for {seq} must be a mapping")
            cfg = self.config["sequencers"].get(seq, copy.deepcopy(defaults))
            self.signals[seq] = _Signal(cfg, f"{self.config['seed']}:{seq}")
            for name, destination in (("waveforms", self.waveforms), ("weights", self.weights)):
                destination[seq] = {}
                entries = sequence.get(name, {})
                if not isinstance(entries, dict):
                    raise ValueError(f"{seq}.{name} must be a mapping")
                for entry in entries.values():
                    if not isinstance(entry, dict):
                        raise ValueError(f"{seq}.{name} entries must be mappings")
                    index = _integer(entry.get("index"), f"{seq}.{name}.index", 0, 1023 if name == "waveforms" else 63)
                    data = _numbers(entry.get("data"), f"{seq}.{name}.data", MAX_TOTAL_SAMPLES)
                    memory_samples += len(data)
                    if memory_samples > MAX_TOTAL_SAMPLES:
                        raise ValueError("Total waveform/weight memory exceeds the model sample limit")
                    if any(abs(value) > 1 for value in data):
                        raise ValueError(f"{seq}.{name} data must be normalized to [-1, 1]")
                    if index in destination[seq]:
                        raise ValueError(f"Duplicate {seq}.{name} index {index}")
                    destination[seq][index] = data
            self.acquisition_definitions[seq] = {}
            entries = sequence.get("acquisitions", {})
            if not isinstance(entries, dict):
                raise ValueError(f"{seq}.acquisitions must be a mapping")
            for entry in entries.values():
                if not isinstance(entry, dict):
                    raise ValueError(f"{seq}.acquisitions entries must be mappings")
                index = _integer(entry.get("index"), "acquisition.index", 0, 31)
                if index in self.acquisition_definitions[seq]:
                    raise ValueError(f"Duplicate {seq}.acquisitions index {index}")
                self.acquisition_definitions[seq][index] = _integer(entry.get("num_bins"), "acquisition.num_bins", 1, 2**24)

    def apply(self, seq: str, time_ns: int | float, op: str, args: list[int], settings: dict) -> list[dict]:
        signal = self.signals[seq]
        if op == "stop":
            return []  # Stop is not a latch update or a waveform-memory rewrite.
        updated = settings.get("_updated_fields", settings)
        latched_update = any(field in updated and settings.get(field) is not None
                             for field in ("frequency", "phase", "phase_delta", "awg_gain", "awg_offset"))
        if not (op in {"play", "play_netzero"} or latched_update or settings.get("_phase_reset") or settings.get("_netzero_reset")):
            return []
        time_ns = _integer(time_ns, "RT signal time", 0, 2**53 - 1)
        if signal.events and time_ns < signal.events[-1][0]:
            raise ValueError("Signal events must be supplied in time order per sequencer")
        if signal.phases and time_ns <= (len(signal.phases) - 1) * self.dt:
            raise ValueError("Signal event would modify already sampled history")
        if op == "play" and (len(args) < 2 or any(index not in self.waveforms[seq] for index in args[:2])):
            raise ValueError(f"Missing waveform data for {seq} play")
        results = []
        if settings.get("_netzero_reset") or op == "play_netzero":
            self._render(time_ns)
            if not signal.netzero:
                results.append({"status": "unmodeled", "reason": "No explicit eligible netzero output mapping."})
            for number, output in signal.netzero.items():
                cfg = output["config"]
                if settings.get("_netzero_reset") and cfg["ihp_enabled"]:
                    pulse = output["pulse"]
                    if pulse is not None and pulse[0] + len(pulse[1]) * self.dt > time_ns:
                        raise ValueError("Reset during active netzero compensation requires a hardware reference vector")
                    output.update(area=0.0, ihp_area=0.0)
                    results.append({"output": number, "status": "reset", "ihp_state": "ideal_accumulator_only"})
                if op == "play_netzero":
                    result = self._compensate(output, time_ns, args[0])
                    results.append({"output": number, **result})
            for result in results:
                result.update(start_ns=time_ns, model="ideal-netzero-area-v1",
                              rt_packet_id=settings.get("_rt_packet_id"), hardware_parity=False)
                signal.netzero_results.append(result)
        signal.events.append((time_ns, op, list(args), copy.deepcopy(settings)))
        return results

    def _compensate(self, output: dict, time_ns: int, maxt: int) -> dict:
        cfg = output["config"]
        result = {"maxt_ns": maxt, "area_before": output["area"], "units": "normalized full scale * ns"}
        if not cfg["enabled"]:
            return {**result, "status": "disabled"}
        if time_ns % self.dt:
            return {**result, "status": "unknown", "reason": "Compensation start is off the configured sample grid."}
        if output["pulse"] is not None and output["pulse"][0] + len(output["pulse"][1]) * self.dt > time_ns:
            return {**result, "status": "unknown", "reason": "Netzero retrigger behavior requires a hardware vector."}
        if any(key not in cfg for key in ("amplitude", "slew_rate_per_s")):
            return {**result, "status": "unknown", "reason": "Explicit compensation amplitude and slew rate are required."}
        area = abs(output["area"])
        if area < 1e-12:
            return {**result, "status": "zero_area", "end_ns": time_ns, "compensation_area": 0.0}
        amplitude, step = cfg["amplitude"], cfg["slew_rate_per_s"] * self.dt * 1e-9
        budget = min(self.config["max_samples"], MAX_TOTAL_SAMPLES // len(self.signals)) - time_ns // self.dt
        limit = min(maxt // self.dt, max(0, budget))

        def envelope(length):
            return [min(amplitude, step * (index + 1), step * (length - index)) for index in range(length)]

        if sum(envelope(limit)) * self.dt < area:
            return {**result, "status": "insufficient_capacity" if limit == maxt // self.dt else "unknown",
                    "reason": "Area exceeds amplitude/slew capacity within maxt or the sample budget."}
        low, high = 1, limit
        while low < high:
            middle = (low + high) // 2
            if sum(envelope(middle)) * self.dt >= area:
                high = middle
            else:
                low = middle + 1
        samples = envelope(low)
        scale = -output["area"] / (sum(samples) * self.dt)
        samples = [value * scale for value in samples]
        output["pulse"] = (time_ns, samples)
        return {**result, "status": "modeled", "end_ns": time_ns + low * self.dt,
                "compensation_area": sum(samples) * self.dt, "sample_period_ns": self.dt,
                "samples": samples}

    def acquisition_duration(self, seq: str, default_duration_ns: int, weights=None) -> int:
        if weights is not None:
            if len(weights) != 2 or any(index not in self.weights[seq] for index in weights):
                raise ValueError(f"Missing integration weights for {seq}")
            return max(len(self.weights[seq][index]) for index in weights)
        value = self.signals[seq].config.get("integration_length_ns", default_duration_ns)
        return _integer(value, "acquisition duration", 1, self.dt * self.config["max_samples"])

    def _render(self, end_ns: int) -> None:
        count = math.ceil(end_ns / self.dt)
        if count > self.config["max_samples"] or count * len(self.signals) > MAX_TOTAL_SAMPLES:
            raise ValueError("Physical signal sample limit exceeded; shorten the run or increase sample_period_ns")
        # All outputs first: zero-delay loopbacks remain independent of dict order.
        for seq, signal in self.signals.items():
            self._render_output(seq, signal, count)
        for signal in self.signals.values():
            self._render_input(signal, count)
        self._rendered_end_ns = max(self._rendered_end_ns, end_ns)

    def _render_output(self, seq: str, signal: _Signal, count: int) -> None:
        index = len(signal.phases)
        iir = signal.config["iir"]
        can_hold = (not signal.netzero and len(signal.config["fir"]) == len(iir["a"]) == len(iir["b"]) == 1
                    and signal.fir_history.maxlen == signal.iir_inputs.maxlen == signal.iir_outputs.maxlen == 1)
        while index < count:
            time_ns = index * self.dt
            previous_clipped = signal.dac_clipped
            self._sample_output(seq, signal, time_ns)
            index += 1
            stop = count
            if signal.event_index < len(signal.events):
                next_event_ns = signal.events[signal.event_index][0]
                stop = min(stop, (next_event_ns + self.dt - 1) // self.dt)
            if index < stop and can_hold and signal.frequency_word == 0:
                wave_end = 0
                if signal.play is not None:
                    start, wave_i, wave_q = signal.play
                    wave_end = start + max(len(wave_i), len(wave_q))
                if time_ns < wave_end:
                    stop = min(stop, (wave_end + self.dt - 1) // self.dt)
                else:
                    # The first post-waveform sample has established the entire one-tap filter state.
                    remaining = stop - index
                    for paths in signal.output_paths:
                        for path in paths:
                            path.extend(path[-1:] * remaining)
                    signal.phases.extend(signal.phases[-1:] * remaining)
                    signal.dac_clipped += (signal.dac_clipped - previous_clipped) * remaining
                    index = stop
            for tick in range(index, stop):
                self._sample_output(seq, signal, tick * self.dt)
            index = stop

    def _render_input(self, signal: _Signal, count: int) -> None:
        adc_i, adc_q = signal.stages["adc"]
        demod_i, demod_q = signal.stages["demodulated"]
        first = len(adc_i)
        if first >= count:
            return
        inp = signal.config.get("input")
        noise = inp.get("noise_std", 0) if inp else 0
        if not signal.config["demodulate"] and (inp is None or inp["kind"] == "constant" and not noise):
            remaining = count - first
            if inp is None:
                i = q = math.nan
                signal.adc_missing.update(range(first, count))
            else:
                if not math.isfinite(inp["i"]) or not math.isfinite(inp["q"]):
                    raise ValueError("Input model produced a non-finite sample")
                value, clipped = _convert_iq(inp["i"], inp["q"], signal.config["adc_bits"])
                i, q = value.real, value.imag
                signal.adc_clipped += remaining * clipped
            for value, adc, demod in ((i, adc_i, demod_i), (q, adc_q, demod_q)):
                samples = array("d", [value]) * remaining
                adc.extend(samples)
                demod.extend(samples)
            return
        rotation = _rotation(inp["phase_turns"]) if inp and inp["kind"] == "loopback" else 1 + 0j
        for index in range(first, count):
            sample = self._input_sample(inp, index * self.dt, rotation)
            if sample is None:
                signal.adc_missing.add(index)
                sample = complex(float("nan"), float("nan"))
            else:
                if noise:
                    sample += complex(signal.random.gauss(0, noise), signal.random.gauss(0, noise))
                if not math.isfinite(sample.real) or not math.isfinite(sample.imag):
                    raise ValueError("Input model produced a non-finite sample")
                sample, clipped = _convert_iq(sample.real, sample.imag, signal.config["adc_bits"])
                signal.adc_clipped += clipped
            adc_i.append(sample.real)
            adc_q.append(sample.imag)
            demodulated = sample * _rotation(-signal.phases[index]) if signal.config["demodulate"] else sample
            demod_i.append(demodulated.real)
            demod_q.append(demodulated.imag)

    def _sample_output(self, seq: str, signal: _Signal, time_ns: int) -> None:
        while signal.event_index < len(signal.events) and signal.events[signal.event_index][0] <= time_ns:
            event_time, op, args, settings = signal.events[signal.event_index]
            signal.event_index += 1
            signal.accumulated_ticks += signal.frequency_word * (event_time - signal.phase_time)
            signal.phase_time = event_time
            updated = settings.get("_updated_fields", list(settings))
            if settings.get("_phase_reset"):
                signal.accumulated_ticks = 0
                signal.phase_offset_ticks = 0
            if "frequency" in updated and settings.get("frequency") is not None:
                signal.frequency_word = settings["frequency"]
                if abs(signal.frequency_word) >= 2e9 / self.dt:
                    raise ValueError("NCO frequency reaches the configured sample-grid Nyquist limit")
            if "phase" in updated and settings.get("phase") is not None:
                signal.phase_offset_ticks = settings["phase"] * 4
            if "phase_delta" in updated and settings.get("phase_delta") is not None:
                signal.accumulated_ticks += settings["phase_delta"] * 4
            for field, attribute in (("awg_gain", "gain"), ("awg_offset", "offset")):
                if field in updated and settings.get(field) is not None:
                    setattr(signal, attribute, [value / 32768 for value in settings[field]])
            if op == "play":
                signal.play = (event_time, self.waveforms[seq][args[0]], self.waveforms[seq][args[1]])
        raw = 0j
        if signal.play is not None:
            start, wave_i, wave_q = signal.play
            index = time_ns - start
            raw = complex(wave_i[index] if index < len(wave_i) else 0, wave_q[index] if index < len(wave_q) else 0)
        gained = complex(raw.real * signal.gain[0] + signal.offset[0], raw.imag * signal.gain[1] + signal.offset[1])
        phase = _phase_turns(signal.accumulated_ticks + signal.frequency_word * (time_ns - signal.phase_time)
                             + signal.phase_offset_ticks)
        modulated = gained * _rotation(phase)
        signal.fir_history.appendleft(modulated)
        weights = signal.config["fir"]
        # Keep the same sum operation even for one tap, including its signed-zero behavior.
        fir = sum((weights[0] * modulated,)) if len(weights) == 1 else sum(map(mul, weights, signal.fir_history))
        signal.iir_inputs.appendleft(fir)
        coefficients = signal.config["iir"]
        a, b = coefficients["a"], coefficients["b"]
        feedforward = sum((b[0] * fir,)) if len(b) == 1 else sum(map(mul, b, signal.iir_inputs))
        feedback = sum(map(mul, a[1:], signal.iir_outputs)) if len(a) > 1 else 0
        filtered = (feedforward - feedback) / a[0]
        if not math.isfinite(filtered.real) or not math.isfinite(filtered.imag):
            raise ValueError("IIR/FIR output is non-finite; verify the configured filter stability")
        signal.iir_outputs.appendleft(filtered)
        dac_input = filtered
        if signal.netzero:
            compensated = [filtered.real, filtered.imag]
            for output in signal.netzero.values():
                cfg, pulse = output["config"], output["pulse"]
                path = cfg["path"]
                if pulse is not None and pulse[0] <= time_ns < pulse[0] + len(pulse[1]) * self.dt:
                    compensated[path] = pulse[1][(time_ns - pulse[0]) // self.dt]
                output["area"] += compensated[path] * self.dt
                if cfg["ihp_enabled"]:
                    output["ihp_area"] += compensated[path] * self.dt
            dac_input = complex(*compensated)
        dac, clipped = _convert_iq(dac_input.real, dac_input.imag, signal.config["dac_bits"])
        signal.dac_clipped += clipped
        if signal.netzero:
            i_path, q_path = signal.stages["netzero"]
            i_path.append(dac_input.real)
            q_path.append(dac_input.imag)
        for (i_path, q_path), sample in zip(signal.output_paths, (raw, gained, modulated, fir, filtered, dac)):
            i_path.append(sample.real)
            q_path.append(sample.imag)
        signal.phases.append(phase % 1)

    def _input_sample(self, inp: dict | None, time_ns: int, rotation: complex) -> complex | None:
        if inp is None:
            return None
        if inp["kind"] == "constant":
            return complex(inp["i"], inp["q"])
        if inp["kind"] == "trace":
            index = (time_ns - inp["start_ns"]) // inp["sample_period_ns"]
            if not 0 <= index < len(inp["i"]):
                return None
            return complex(inp["i"][index], inp["q"][index])
        delayed_time = time_ns - inp["delay_ns"]
        if delayed_time < 0:
            return 0j  # Declared loopback starts from zero initial conditions.
        source = self.signals[inp["sequencer"]].stages["dac"]
        index = delayed_time // self.dt
        return complex(source[0][index], source[1][index]) * inp["gain"] * rotation

    def acquire(self, seq: str, start_ns: int, duration_ns: int, acquisition_index: int, bin_index: int, weights=None,
                *, integration_length_source: str | None = None) -> dict | None:
        signal = self.signals[seq]
        if "input" not in signal.config:
            return None
        _integer(start_ns, "acquisition start", 0, 2**53 - 1)
        _integer(duration_ns, "acquisition duration", 1, self.dt * self.config["max_samples"])
        definitions = self.acquisition_definitions[seq]
        if acquisition_index not in definitions or not 0 <= bin_index < definitions[acquisition_index]:
            raise ValueError(f"Missing acquisition {acquisition_index}/bin {bin_index} definition for {seq}")
        if weights is not None:
            self.acquisition_duration(seq, duration_ns, weights)
        end = start_ns + duration_ns
        self._render(end)
        first, last = start_ns // self.dt, math.ceil(end / self.dt)
        if signal.adc_missing and any(index in signal.adc_missing for index in range(first, last)):
            return None
        total_i = total_q = 0.0
        paths = signal.stages["demodulated"]
        if self.dt == 1 and weights is None:
            i_path, q_path = paths
            for index in range(first, last):
                total_i += i_path[index]
                total_q += q_path[index]
        else:
            wi, wq = (self.weights[seq][key] for key in weights) if weights is not None else (None, None)
            left = 0
            right = self.dt - start_ns % self.dt
            for index in range(first, last):
                right = min(right, duration_ns)
                weight_i = weight_q = right - left
                if weights is not None:
                    weight_i = sum(wi[left:right])
                    weight_q = sum(wq[left:right])
                total_i += paths[0][index] * weight_i
                total_q += paths[1][index] * weight_q
                left, right = right, right + self.dt
        rotated = complex(total_i, total_q) * _rotation(signal.config["threshold_rotation_deg"] / 360)
        if integration_length_source is None:
            integration_length_source = ("weight_memory" if weights is not None else "configured"
                                         if "integration_length_ns" in signal.config else "instruction_duration_fallback")
        result = {
            "start_ns": start_ns, "duration_ns": duration_ns, "end_ns": end,
            "acquisition_index": acquisition_index, "bin_index": bin_index,
            "i": total_i, "q": total_q, "mean_i": total_i / duration_ns, "mean_q": total_q / duration_ns,
            "thresholded": int(rotated.real >= signal.config["threshold"]),
            "rotated_i": rotated.real, "threshold": signal.config["threshold"],
            "threshold_units": "normalized full scale * ns",
            "integration_length_source": integration_length_source,
            "weights": list(weights) if weights is not None else None,
            "source": self._source(signal), "model": MODEL,
            "units": "normalized full scale * ns", "sample_period_ns": self.dt,
        }
        key = (acquisition_index, bin_index)
        item = signal.bins.setdefault(key, {"acquisition_index": acquisition_index, "bin_index": bin_index, "count": 0, "sum_i": 0.0, "sum_q": 0.0, "thresholded_count": 0})
        item["count"] += 1
        item["sum_i"] += total_i
        item["sum_q"] += total_q
        item["thresholded_count"] += result["thresholded"]
        item.update(mean_i=item["sum_i"] / item["count"], mean_q=item["sum_q"] / item["count"])
        result["bin_aggregate"] = {"kind": "iq", "count": item["count"], "mean_i": item["mean_i"], "mean_q": item["mean_q"],
                                   "thresholded_count": item["thresholded_count"], "units": result["units"]}
        signal.acquisitions.append(result)
        return dict(result)

    @staticmethod
    def _source(signal: _Signal) -> dict:
        inp = signal.config.get("input", {})
        kind = inp.get("kind")
        if kind == "trace":
            return {"kind": "measured_input" if inp["measured"] else "supplied_trace", "label": inp["source"], "measurement_claim": "user-supplied" if inp["measured"] else None}
        if kind == "loopback":
            return {"kind": "loopback", "label": f"Numerical loopback from {inp['sequencer']} DAC"}
        return {"kind": "numerical_model", "label": "Configured constant input" if kind else "Input not configured"}

    def scope_samples(self, seq: str, start_ns: int, end_ns: int, paths: list[int]) -> dict:
        signal = self.signals[seq]
        if "input" not in signal.config or self.dt != 1:
            return {"sample_status": "unavailable", "reason": "Scope samples require modeled ADC input on a 1 ns grid."}
        budget = min(self.config["max_samples"], MAX_TOTAL_SAMPLES // len(self.signals))
        sampled_end = min(end_ns, budget)
        if sampled_end <= start_ns:
            return {"sample_status": "unavailable", "reason": "The scope window starts outside the physical sample budget."}
        self._render(sampled_end)
        stride = max(1, math.ceil((sampled_end - start_ns) / self.config["max_export_samples"]))
        missing = sum(index in signal.adc_missing for index in range(start_ns, sampled_end))
        if missing == sampled_end - start_ns:
            return {"sample_status": "unavailable", "reason": "No ADC input is available in the scope window."}
        return {"sample_status": "partial" if missing else "truncated" if sampled_end < end_ns else "available",
                "sample_period_ns": 1, "sampled_until_ns": sampled_end, "export_stride": stride,
                "sampled_count": sampled_end - start_ns,
                "budget_truncated": sampled_end < end_ns, "missing_samples": missing, "source": self._source(signal),
                "samples": {path: [None if index in signal.adc_missing else signal.stages["adc"][path][index]
                                   for index in range(start_ns, sampled_end, stride)] for path in paths}}

    def _observe_netzero_result(self, result: dict, cutoff_ns: int | float | None) -> dict:
        if cutoff_ns is None or result["status"] != "modeled":
            return result
        cutoff_ns = max(result["start_ns"], min(cutoff_ns, result["end_ns"]))
        # Use the same half-open sample grid as _render: sample times are below the cutoff.
        count = math.ceil((cutoff_ns - result["start_ns"]) / self.dt)
        samples = result["samples"][:count]
        return {**result, "status": "incomplete", "end_ns": cutoff_ns, "observation_cutoff_ns": cutoff_ns,
                "planned_end_ns": result["end_ns"], "planned_compensation_area": result["compensation_area"],
                "samples": samples, "compensation_area": sum(samples) * self.dt}

    def export(self, end_ns: int, *, netzero_cutoffs: dict[tuple[str, str, int], int | float] | None = None) -> dict[str, dict]:
        """Export the full rendered snapshot; end_ns extends sampling, never rewinds it."""
        netzero_cutoffs = netzero_cutoffs or {}
        cap = min(self.config["max_samples"], MAX_TOTAL_SAMPLES // max(1, len(self.signals))) * self.dt
        sampled_end = min(end_ns, cap)
        self._render(sampled_end)
        sampled_end = self._rendered_end_ns
        output = {}
        for seq, signal in self.signals.items():
            count = len(signal.phases)
            stride = max(1, math.ceil(count / self.config["max_export_samples"]))
            traces = []
            for stage, paths in signal.stages.items():
                if stage in {"adc", "demodulated"} and "input" not in signal.config:
                    continue
                for path, samples in zip(("i", "q"), paths):
                    values = samples[::stride].tolist()
                    if not all(map(math.isfinite, values)):
                        values = [value if math.isfinite(value) else None for value in values]
                    traces.append({"stage": stage, "path": path, "t0": 0, "dt_ns": self.dt * stride,
                                   "samples": values,
                                   "source": self._source(signal) if stage in {"adc", "demodulated"} else {"kind": "numerical_model", "label": "Calculated signal"}})
            for path, samples in zip(("i", "q"), signal.stages["dac"]):
                traces.append({"stage": "rf", "path": path, "t0": 0, "dt_ns": self.dt * stride,
                               "samples": samples[::stride].tolist(), "representation": "complex_envelope",
                               "lo_frequency_hz": signal.config["lo_frequency_hz"],
                               "source": {"kind": "numerical_model", "label": "Ideal RF complex envelope; LO carrier not sampled"}})
            parameters = {key: copy.deepcopy(value) for key, value in signal.config.items() if key != "input"}
            if "input" in signal.config:
                inp = signal.config["input"]
                parameters["input"] = {key: value for key, value in inp.items() if inp["kind"] != "trace" or key not in {"i", "q"}}
            parameters["noise_seed"] = self.config["seed"]
            output[seq] = {
                "model": MODEL, "sample_period_ns": self.dt, "sampled_end_ns": sampled_end,
                "truncated": end_ns > cap, "export_stride": stride,
                "units": {"time": "ns", "amplitude": "normalized full scale", "integration": "normalized full scale * ns"},
                "assumptions": list(ASSUMPTIONS), "sources": list(SOURCES),
                "parameters": parameters,
                "rf": {"representation": "complex_envelope", "lo_frequency_hz": signal.config["lo_frequency_hz"], "carrier_samples": False},
                "clipping": {"dac_samples": signal.dac_clipped, "adc_samples": signal.adc_clipped},
                "traces": traces, "acquisitions": copy.deepcopy(signal.acquisitions), "bins": copy.deepcopy(list(signal.bins.values())),
            }
            if signal.netzero:
                results = []
                for result in signal.netzero_results:
                    key = seq, result.get("rt_packet_id"), result.get("output")
                    results.append(self._observe_netzero_result(result, netzero_cutoffs.get(key)))
                output[seq]["netzero"] = {"results": copy.deepcopy(results),
                                          "areas": {number: item["area"] for number, item in signal.netzero.items()},
                                          "ihp_model": "accumulator bookkeeping only; transfer function unmodeled"}
        return output
