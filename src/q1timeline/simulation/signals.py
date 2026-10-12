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
from typing import Any


MODEL = "q1lens-sampled-envelope-v1"
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
    "Without integration_length_ns, nonweighted acquisition uses its instruction duration as an explicit model fallback, not the hardware default of 1024 ns.",
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
            "threshold_rotation_deg", "demodulate",
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


def _quantize(value: float, bits: int) -> float:
    scale = 2 ** (bits - 1)
    return min(scale - 1, max(-scale, round(min(1, max(-1, value)) * scale))) / scale


def _clips(value: float, bits: int) -> bool:
    if value < -1 or value >= 1:
        return True
    scale = 2 ** (bits - 1)
    return not -scale <= round(value * scale) < scale


def _rotation(turns: float) -> complex:
    angle = math.tau * (turns % 1)
    return complex(math.cos(angle), math.sin(angle))


class _Signal:
    def __init__(self, config: dict, seed: str):
        self.config = config
        self.events: list[tuple[int, str, list[int], dict]] = []
        self.event_index = 0
        self.stages = {stage: (array("d"), array("d")) for stage in ("waveform", "gain_offset", "nco", "fir", "filtered", "dac", "adc", "demodulated")}
        self.phases = array("d")
        self.gain = config["gain"]
        self.offset = config["offset"]
        self.frequency = config["nco_frequency_hz"]
        self.phase_offset = config["nco_phase_turns"]
        self.phase_time = 0
        self.accumulated_phase = 0.0
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

    def apply(self, seq: str, time_ns: int, op: str, args: list[int], settings: dict) -> None:
        signal = self.signals[seq]
        _integer(time_ns, "RT signal time", 0, 2**53 - 1)
        if op == "stop":
            return  # Stop is not a latch update or a waveform-memory rewrite.
        if signal.events and time_ns < signal.events[-1][0]:
            raise ValueError("Signal events must be supplied in time order per sequencer")
        if signal.phases and time_ns <= (len(signal.phases) - 1) * self.dt:
            raise ValueError("Signal event would modify already sampled history")
        if op == "play" and (len(args) < 2 or any(index not in self.waveforms[seq] for index in args[:2])):
            raise ValueError(f"Missing waveform data for {seq} play")
        signal.events.append((time_ns, op, list(args), copy.deepcopy(settings)))

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
            while len(signal.phases) < count:
                self._sample_output(seq, signal, len(signal.phases) * self.dt)
        for seq, signal in self.signals.items():
            while len(signal.stages["adc"][0]) < count:
                index = len(signal.stages["adc"][0])
                sample = self._input_sample(seq, signal, index * self.dt)
                if sample is None:
                    signal.adc_missing.add(index)
                    sample = complex(float("nan"), float("nan"))
                else:
                    noise = signal.config.get("input", {}).get("noise_std", 0)
                    if noise:
                        sample += complex(signal.random.gauss(0, noise), signal.random.gauss(0, noise))
                    if not math.isfinite(sample.real) or not math.isfinite(sample.imag):
                        raise ValueError("Input model produced a non-finite sample")
                    bits = signal.config["adc_bits"]
                    signal.adc_clipped += int(_clips(sample.real, bits) or _clips(sample.imag, bits))
                    sample = complex(_quantize(sample.real, bits), _quantize(sample.imag, bits))
                self._append(signal, "adc", sample)
                demodulated = sample * _rotation(-signal.phases[index]) if signal.config["demodulate"] else sample
                self._append(signal, "demodulated", demodulated)
        self._rendered_end_ns = max(self._rendered_end_ns, end_ns)

    def _sample_output(self, seq: str, signal: _Signal, time_ns: int) -> None:
        while signal.event_index < len(signal.events) and signal.events[signal.event_index][0] <= time_ns:
            event_time, op, args, settings = signal.events[signal.event_index]
            signal.event_index += 1
            signal.accumulated_phase += signal.frequency * (event_time - signal.phase_time) * 1e-9
            signal.phase_time = event_time
            updated = settings.get("_updated_fields", list(settings))
            if settings.get("_phase_reset"):
                signal.accumulated_phase = 0
                signal.phase_offset = 0
            if "frequency" in updated and settings.get("frequency") is not None:
                signal.frequency = settings["frequency"] / 4
                if abs(signal.frequency) >= 0.5e9 / self.dt:
                    raise ValueError("NCO frequency reaches the configured sample-grid Nyquist limit")
            if "phase" in updated and settings.get("phase") is not None:
                signal.phase_offset = settings["phase"] / 1e9
            if "phase_delta" in updated and settings.get("phase_delta") is not None:
                signal.phase_offset += settings["phase_delta"] / 1e9
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
        phase = signal.accumulated_phase + signal.frequency * (time_ns - signal.phase_time) * 1e-9 + signal.phase_offset
        modulated = gained * _rotation(phase)
        signal.fir_history.appendleft(modulated)
        filtered = sum(coefficient * sample for coefficient, sample in zip(signal.config["fir"], signal.fir_history))
        fir = filtered
        signal.iir_inputs.appendleft(filtered)
        coefficients = signal.config["iir"]
        filtered = (sum(coefficient * sample for coefficient, sample in zip(coefficients["b"], signal.iir_inputs))
                    - sum(coefficient * sample for coefficient, sample in zip(coefficients["a"][1:], signal.iir_outputs))) / coefficients["a"][0]
        if not math.isfinite(filtered.real) or not math.isfinite(filtered.imag):
            raise ValueError("IIR/FIR output is non-finite; verify the configured filter stability")
        signal.iir_outputs.appendleft(filtered)
        signal.dac_clipped += int(_clips(filtered.real, signal.config["dac_bits"]) or _clips(filtered.imag, signal.config["dac_bits"]))
        dac = complex(_quantize(filtered.real, signal.config["dac_bits"]), _quantize(filtered.imag, signal.config["dac_bits"]))
        for stage, sample in (("waveform", raw), ("gain_offset", gained), ("nco", modulated), ("fir", fir), ("filtered", filtered), ("dac", dac)):
            self._append(signal, stage, sample)
        signal.phases.append(phase % 1)

    @staticmethod
    def _append(signal: _Signal, stage: str, value: complex) -> None:
        signal.stages[stage][0].append(value.real)
        signal.stages[stage][1].append(value.imag)

    def _input_sample(self, seq: str, signal: _Signal, time_ns: int) -> complex | None:
        inp = signal.config.get("input")
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
        return complex(source[0][index], source[1][index]) * inp["gain"] * _rotation(inp["phase_turns"])

    def acquire(self, seq: str, start_ns: int, duration_ns: int, acquisition_index: int, bin_index: int, weights=None) -> dict | None:
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
        if any(index in signal.adc_missing for index in range(first, last)):
            return None
        total_i = total_q = 0.0
        paths = signal.stages["demodulated"]
        for index in range(first, last):
            t0, t1 = max(start_ns, index * self.dt), min(end, (index + 1) * self.dt)
            weight_i = weight_q = t1 - t0
            if weights is not None:
                relative_start, relative_end = t0 - start_ns, t1 - start_ns
                wi, wq = (self.weights[seq][weight] for weight in weights)
                weight_i = sum(wi[relative_start:relative_end])
                weight_q = sum(wq[relative_start:relative_end])
            total_i += paths[0][index] * weight_i
            total_q += paths[1][index] * weight_q
        rotated = complex(total_i, total_q) * _rotation(signal.config["threshold_rotation_deg"] / 360)
        result = {
            "start_ns": start_ns, "duration_ns": duration_ns, "end_ns": end,
            "acquisition_index": acquisition_index, "bin_index": bin_index,
            "i": total_i, "q": total_q, "mean_i": total_i / duration_ns, "mean_q": total_q / duration_ns,
            "thresholded": int(rotated.real >= signal.config["threshold"]),
            "rotated_i": rotated.real, "threshold": signal.config["threshold"],
            "threshold_units": "normalized full scale * ns",
            "integration_length_source": "weight_memory" if weights is not None else "configured" if "integration_length_ns" in signal.config else "instruction_duration_fallback",
            "weights": list(weights) if weights is not None else None,
            "source": self._source(signal), "model": MODEL,
            "units": "normalized full scale * ns", "sample_period_ns": self.dt,
        }
        signal.acquisitions.append(result)
        key = (acquisition_index, bin_index)
        item = signal.bins.setdefault(key, {"acquisition_index": acquisition_index, "bin_index": bin_index, "count": 0, "sum_i": 0.0, "sum_q": 0.0, "thresholded_count": 0})
        item["count"] += 1
        item["sum_i"] += total_i
        item["sum_q"] += total_q
        item["thresholded_count"] += result["thresholded"]
        item.update(mean_i=item["sum_i"] / item["count"], mean_q=item["sum_q"] / item["count"])
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

    def export(self, end_ns: int) -> dict[str, dict]:
        """Export the full rendered snapshot; end_ns extends sampling, never rewinds it."""
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
                    traces.append({"stage": stage, "path": path, "t0": 0, "dt_ns": self.dt * stride,
                                   "samples": [value if math.isfinite(value) else None for value in samples[::stride]],
                                   "source": self._source(signal) if stage in {"adc", "demodulated"} else {"kind": "numerical_model", "label": "Calculated signal"}})
            for path, samples in zip(("i", "q"), signal.stages["dac"]):
                traces.append({"stage": "rf", "path": path, "t0": 0, "dt_ns": self.dt * stride,
                               "samples": list(samples[::stride]), "representation": "complex_envelope",
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
        return output
