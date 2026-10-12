"""Independent arithmetic oracles for the declared sampled-envelope model.

These tests establish software-model behavior, not hardware agreement. Expected
values never call the production quantizer, filters, or oscillator helpers.
"""
from __future__ import annotations

import cmath
import json
import math
import statistics
from decimal import Decimal, ROUND_HALF_EVEN
from fractions import Fraction

import pytest

from q1timeline.simulation.signals import MAX_TOTAL_SAMPLES, SignalModel, validate_physical_config


def memory(i=None, q=None, weights=None):
    return {
        "waveforms": {"i": {"index": 0, "data": i or [0]}, "q": {"index": 1, "data": q or [0]}},
        "weights": weights or {},
        "acquisitions": {"a": {"index": 0, "num_bins": 2}, "b": {"index": 1, "num_bins": 2}},
    }


def trace(exported, stage, path="i"):
    return next(item for item in exported["traces"] if item["stage"] == stage and item["path"] == path)


def code(value, bits):
    """Decimal integer-code reference, including ties-to-even and rail limits."""
    scale = 2 ** (bits - 1)
    rounded = int((Decimal.from_float(float(value)) * scale).to_integral_value(rounding=ROUND_HALF_EVEN))
    return min(scale - 1, max(-scale, rounded))


@pytest.mark.parametrize("bits", range(2, 25))
@pytest.mark.parametrize("stage", ["dac", "adc"])
def test_every_converter_width_signed_ties_and_rails(bits, stage):
    scale = 2 ** (bits - 1)
    values = [-1, 1, (scale - 1) / scale, -0.5 / scale, 0.5 / scale,
              -1.5 / scale, 1.5 / scale, -2.5 / scale, 2.5 / scale]
    for midpoint in [-1.5 / scale, 0.5 / scale, 1.5 / scale]:
        values.extend([math.nextafter(midpoint, -math.inf), math.nextafter(midpoint, math.inf)])
    # At two bits some probes exceed waveform memory's normalized domain.
    values = [value for value in values if -1 <= value <= 1]
    settings = {f"{stage}_bits": bits}
    if stage == "adc":
        settings["input"] = {"kind": "trace", "i": values, "q": [-x for x in values], "source": "synthetic boundary fixture"}
    model = SignalModel({"sequencers": {"s": settings}}, {"s": memory(values, [-x for x in values])})
    model.apply("s", 0, "play", [0, 1, len(values)], {})
    result = model.export(len(values))["s"]
    for path, inputs in [("i", values), ("q", [-x for x in values])]:
        expected = [code(value, bits) / scale for value in inputs]
        assert trace(result, stage, path)["samples"] == expected
    # A complex sample is counted once even if both paths saturate.
    clipped = sum(any(not -scale <= int((Decimal.from_float(float(x)) * scale).to_integral_value(rounding=ROUND_HALF_EVEN)) < scale
                      for x in (value, -value)) for value in values)
    assert result["clipping"][f"{stage}_samples"] == clipped


@pytest.mark.parametrize("bits", [2, 12, 24])
def test_clipping_and_half_lsb_error_bound(bits):
    scale = 2 ** (bits - 1)
    values = [-1.25, -1, -0.321, 0, 0.271, (scale - 1) / scale, 1, 1.25]
    model = SignalModel({"sequencers": {"s": {"adc_bits": bits, "input": {
        "kind": "trace", "i": values, "q": [0] * len(values), "source": "synthetic clipping fixture",
    }}}}, {"s": memory()})
    result = model.export(len(values))["s"]
    actual = trace(result, "adc")["samples"]
    assert actual == [code(value, bits) / scale for value in values]
    for x, y in zip(values, actual):
        if -1 <= x <= (scale - 1) / scale:
            assert abs(x - y) <= 0.5 / scale
    assert result["clipping"]["adc_samples"] == 3


@pytest.mark.parametrize("i,q,rotation,integral", [(0.25, 0, 0, 1), (-0.25, 0, 0, -1),
    (0, 0, 0, 0), (0, 0.25, -90, 1), (0, -0.25, 90, 1), (0.25, 0, 180, -1)])
def test_threshold_below_equal_above_raw_integral(i, q, rotation, integral):
    for threshold, bit in [(integral - 2**-20, 1), (integral, 1), (integral + 2**-20, 0)]:
        model = SignalModel({"sequencers": {"s": {"threshold": threshold, "threshold_rotation_deg": rotation,
            "input": {"kind": "constant", "i": i, "q": q}}}}, {"s": memory()})
        result = model.acquire("s", 0, 4, 0, 0)
        assert (result["i"], result["q"], result["mean_i"], result["mean_q"]) == (4*i, 4*q, i, q)
        assert result["rotated_i"] == integral
        assert result["thresholded"] == bit
        assert result["units"] == result["threshold_units"] == "normalized full scale * ns"


def test_bins_are_isolated_and_average_integrals_for_unequal_windows():
    model = SignalModel({"sequencers": {"s": {"threshold": 0.75, "input": {"kind": "constant", "i": 0.25, "q": -0.5}}}}, {"s": memory()})
    for acquisition, bin_index, duration in [(0, 0, 2), (0, 0, 4), (0, 1, 1), (1, 0, 3)]:
        model.acquire("s", 0, duration, acquisition, bin_index)
    bins = {(item["acquisition_index"], item["bin_index"]): item for item in model.export(4)["s"]["bins"]}
    assert set(bins) == {(0, 0), (0, 1), (1, 0)}
    assert (bins[0, 0]["count"], bins[0, 0]["sum_i"], bins[0, 0]["mean_i"], bins[0, 0]["mean_q"], bins[0, 0]["thresholded_count"]) == (2, 1.5, 0.75, -1.5, 1)
    assert bins[0, 1]["sum_i"] == 0.25
    assert bins[1, 0]["sum_i"] == 0.75


def test_nonquadrant_threshold_rotation_uses_complex_integral():
    expected = (1 + 0.5j) * cmath.exp(1j*math.pi/6)
    model = SignalModel({"sequencers": {"s": {"threshold": 0.6, "threshold_rotation_deg": 30,
        "input": {"kind": "constant", "i": 0.25, "q": 0.125}}}}, {"s": memory()})
    result = model.acquire("s", 0, 4, 0, 0)
    assert result["rotated_i"] == pytest.approx(expected.real, abs=1e-12, rel=1e-12)
    assert result["thresholded"] == 1


def test_piecewise_area_respects_half_open_acquisition_window():
    model = SignalModel({"sequencers": {"rx": {"input": {"kind": "loopback", "sequencer": "tx"}}}}, {"rx": memory(), "tx": memory()})
    for time, offset in [(0, 4096), (2, 8192), (5, 16384), (6, 24576)]:
        model.apply("tx", time, "upd_param", [1], {"awg_offset": [offset, 0]})
    result = model.acquire("rx", 2, 4, 0, 0)
    assert result["i"] == 3*0.25 + 1*0.5  # t=6 update lies outside [2,6).
    assert model.acquire("rx", 6, 1, 0, 1)["i"] == 0.75


def test_fir_impulse_has_independent_signed_iq_coefficients():
    coefficients = [0.5, -0.25, 0.125, 0.25]
    model = SignalModel({"sequencers": {"s": {"fir": coefficients}}}, {"s": memory([0.5], [-0.25])})
    model.apply("s", 0, "play", [0, 1, 8], {})
    model.export(2)
    result = model.export(8)["s"]
    for path, amplitude in [("i", 0.5), ("q", -0.25)]:
        assert trace(result, "fir", path)["samples"] == [amplitude*c for c in coefficients] + [0]*4


@pytest.mark.parametrize("step", [False, True])
@pytest.mark.parametrize("a0", [1, 2])
def test_first_order_iir_matches_closed_form_with_nonunit_a0(step, a0):
    count, amplitude, pole = 64, 0.5, 0.75
    model = SignalModel({"sequencers": {"s": {"iir": {"b": [a0*(1-pole)], "a": [a0, -a0*pole]}}}},
                        {"s": memory([amplitude] * (count if step else 1))})
    model.apply("s", 0, "play", [0, 1, count], {})
    expected = [amplitude*(1-pole**(n+1)) if step else amplitude*(1-pole)*pole**n for n in range(count)]
    model.export(7)
    assert trace(model.export(count)["s"], "filtered")["samples"] == pytest.approx(expected, abs=1e-12, rel=1e-12)


def test_second_order_iir_matches_rational_closed_form():
    # H(z)=1/((1-z^-1/2)(1-z^-1/4)); impulse response 2*(1/2)^n-(1/4)^n.
    count = 32
    model = SignalModel({"sequencers": {"s": {"iir": {"b": [1], "a": [1, -0.75, 0.125]}}}}, {"s": memory([0.25])})
    model.apply("s", 0, "play", [0, 1, count], {})
    expected = [float(Fraction(1, 4)*(2*Fraction(1, 2)**n-Fraction(1, 4)**n)) for n in range(count)]
    assert trace(model.export(count)["s"], "filtered")["samples"] == pytest.approx(expected, abs=1e-14, rel=1e-12)


@pytest.mark.parametrize("sign", [-1, 1])
def test_nonquadrant_tone_and_frequency_change_preserve_phase(sign):
    model = SignalModel({"sequencers": {"s": {"offset": [0.5, -0.25], "nco_frequency_hz": sign*62.5e6, "nco_phase_turns": 0.125}}}, {"s": memory()})
    model.apply("s", 7, "upd_param", [4], {"frequency": sign*125_000_000, "_updated_fields": ["frequency"]})
    expected = [(0.5-0.25j)*cmath.exp(2j*math.pi*(0.125+sign*(n/16 if n<7 else 7/16+(n-7)/32))) for n in range(32)]
    result = model.export(32)["s"]
    for path, values in [("i", [x.real for x in expected]), ("q", [x.imag for x in expected])]:
        assert trace(result, "nco", path)["samples"] == pytest.approx(values, abs=1e-12, rel=1e-12)


def test_mismatched_demodulation_tone_matches_quantized_complex_oracle():
    model = SignalModel({"sequencers": {
        "tx": {"offset": [0.5, 0], "nco_frequency_hz": 62.5e6, "dac_bits": 16, "lo_frequency_hz": 5e9},
        "rx": {"nco_frequency_hz": 31.25e6, "nco_phase_turns": 0.125, "adc_bits": 12, "demodulate": True,
               "lo_frequency_hz": 6e9, "input": {"kind": "loopback", "sequencer": "tx"}},
    }}, {"rx": memory(), "tx": memory()})
    expected = []
    for n in range(32):
        wave = 0.5*cmath.exp(2j*math.pi*n/16)
        dac = complex(code(wave.real, 16)/32768, code(wave.imag, 16)/32768)
        adc = complex(code(dac.real, 12)/2048, code(dac.imag, 12)/2048)
        expected.append(adc*cmath.exp(-2j*math.pi*(n/32+0.125)))
    result = model.acquire("rx", 0, 32, 0, 0)
    assert complex(result["i"], result["q"]) == pytest.approx(sum(expected), abs=1e-11, rel=1e-12)
    exported = model.export(32)["rx"]
    assert trace(exported, "demodulated")["samples"] == pytest.approx([x.real for x in expected], abs=1e-12, rel=1e-12)
    assert exported["rf"]["carrier_samples"] is False  # LO mismatch does not invent a carrier in this model.


def test_noise_fixed_seed_statistics_for_65536_samples():
    count, sigma = 65536, 0.02
    model = SignalModel({"seed": 20261003, "max_samples": count, "sequencers": {"s": {
        "adc_bits": 24, "input": {"kind": "constant", "noise_std": sigma}}}}, {"s": memory()})
    result = model.acquire("s", 0, count, 0, 0)
    # Export deliberately decimates; inspect the full model buffer for this distribution test.
    i, q = model.signals["s"].stages["adc"]
    assert len(i) == len(q) == count
    means = [statistics.fmean(path) for path in (i, q)]
    variances = [statistics.variance(path) for path in (i, q)]
    for mean, variance in zip(means, variances):
        assert abs(mean) <= 6*sigma/math.sqrt(count) + 2**-24
        assert abs(variance/sigma**2-1) <= 6*math.sqrt(2/(count-1)) + 1e-5
    covariance = math.fsum((a-means[0])*(b-means[1]) for a, b in zip(i, q))/(count-1)
    assert abs(covariance/math.sqrt(variances[0]*variances[1])) <= 6/math.sqrt(count)
    assert result["mean_i"] == pytest.approx(means[0], abs=1e-15, rel=1e-12)
    assert model.export(count)["s"]["clipping"]["adc_samples"] == 0


def test_noise_seed_sequence_order_and_zero_noise_contract():
    settings = {seq: {"input": {"kind": "constant", "noise_std": 0.02}} for seq in ("a", "b")}
    first = SignalModel({"seed": 9, "sequencers": settings}, {"a": memory(), "b": memory()})
    reordered = SignalModel({"seed": 9, "sequencers": settings}, {"b": memory(), "a": memory()})
    changed = SignalModel({"seed": 10, "sequencers": settings}, {"a": memory(), "b": memory()})
    first.export(7)
    a, b, c = first.export(64), reordered.export(64), changed.export(64)
    for seq in settings:
        assert trace(a[seq], "adc")["samples"] == trace(b[seq], "adc")["samples"]
        assert trace(a[seq], "adc")["samples"] != trace(c[seq], "adc")["samples"]
    assert trace(a["a"], "adc")["samples"] != trace(a["b"], "adc")["samples"]
    clean = SignalModel({"sequencers": {"s": {"input": {"kind": "constant", "i": 0.25, "noise_std": 0}}}}, {"s": memory()})
    assert trace(clean.export(64)["s"], "adc")["samples"] == [0.25]*64


@pytest.mark.parametrize("measured", [False, True])
def test_trace_grid_provenance_and_missing_boundaries(measured):
    model = SignalModel({"sample_period_ns": 2, "sequencers": {"s": {"input": {
        "kind": "trace", "start_ns": 2, "sample_period_ns": 3, "i": [0.25, 0.5], "q": [-0.25, -0.5],
        "source": "synthetic provenance fixture; not a hardware measurement", "measured": measured,
    }}}}, {"s": memory()})
    assert model.acquire("s", 0, 3, 0, 0) is None
    result = model.acquire("s", 3, 4, 0, 0)
    assert (result["i"], result["q"]) == (1.25, -1.25)  # [3,4):.25, [4,6):.25, [6,7):.5.
    assert model.acquire("s", 7, 2, 0, 1) is None
    exported = model.export(10)["s"]
    assert trace(exported, "adc")["samples"] == [None, 0.25, 0.25, 0.5, None]
    assert result["source"]["kind"] == ("measured_input" if measured else "supplied_trace")
    assert result["source"]["measurement_claim"] == ("user-supplied" if measured else None)
    assert "synthetic" in result["source"]["label"]
    assert len(exported["acquisitions"]) == exported["bins"][0]["count"] == 1
    assert "i" not in exported["parameters"]["input"]
    json.dumps(exported, allow_nan=False)


def test_partial_grid_weights_match_per_nanosecond_sum():
    wi, wq = [1, -0.5, 0.25, -1, 0.5], [0.5, 1, -0.5]
    model = SignalModel({"sample_period_ns": 3, "sequencers": {"s": {"input": {"kind": "constant", "i": 0.5, "q": -0.25}}}},
                        {"s": memory(weights={"i": {"index": 0, "data": wi}, "q": {"index": 1, "data": wq}})})
    assert model.acquisition_duration("s", 100, [0, 1]) == 5
    result = model.acquire("s", 1, 5, 0, 0, [0, 1])
    assert (result["i"], result["q"]) == (0.5*sum(wi), -0.25*sum(wq))


def test_decimation_coordinates_caps_and_integral_are_independent():
    values = [n/32 for n in range(12)]
    model = SignalModel({"max_samples": 12, "max_export_samples": 3, "sequencers": {"s": {"input": {
        "kind": "trace", "i": values, "q": [0]*12, "source": "synthetic ramp"}}}}, {"s": memory()})
    result = model.acquire("s", 0, 12, 0, 0)
    assert result["i"] == sum(values)
    exported = model.export(13)["s"]
    output = trace(exported, "adc")
    assert (output["t0"], output["dt_ns"], output["samples"]) == (0, 4, [values[n] for n in (0, 4, 8)])
    assert exported["sampled_end_ns"] == 12 and exported["truncated"] is True
    assert exported["acquisitions"][0]["i"] == sum(values)
    assert all(len(item["samples"]) <= 3 for item in exported["traces"])
    with pytest.raises(ValueError, match="sample limit"):
        model.acquire("s", 1, 12, 0, 0)


def test_global_sample_limit_rejects_before_any_rendering():
    assert MAX_TOTAL_SAMPLES == 1_000_000
    model = SignalModel({"max_samples": 1_000_000, "sequencers": {"s": {"input": {"kind": "constant"}}}}, {"s": memory(), "t": memory()})
    with pytest.raises(ValueError, match="sample limit"):
        model.acquire("s", 0, 500_001, 0, 0)
    assert all(len(signal.phases) == 0 for signal in model.signals.values())


def test_sequence_count_and_total_memory_limits():
    SignalModel({}, {str(n): {} for n in range(64)})
    with pytest.raises(ValueError, match="64 sequencers"):
        SignalModel({}, {str(n): {} for n in range(65)})
    # Validation-only large arrays exercise aggregate limits without rendering.
    data = [0.0]*500_000
    with pytest.raises(ValueError, match="Total waveform/weight memory"):
        SignalModel({}, {"s": memory(data, data, {"extra": {"index": 0, "data": [0]}})})
    with pytest.raises(ValueError, match="Total supplied input samples"):
        validate_physical_config({"sequencers": {"s": {"input": {"kind": "trace", "i": data+[0], "q": data+[0], "source": "synthetic cap fixture"}}}}, ["s"])


def test_export_is_full_history_snapshot_not_cursor_rewind():
    model = SignalModel({"sequencers": {"s": {"input": {"kind": "constant", "i": 0.25}}}}, {"s": memory()})
    model.acquire("s", 4, 4, 0, 0)
    longer = model.export(12)["s"]
    shorter = model.export(3)["s"]
    assert shorter["sampled_end_ns"] == 12
    assert shorter["traces"] == longer["traces"]
    assert shorter["acquisitions"] == longer["acquisitions"]
    assert shorter["bins"] == longer["bins"]
    assert any("snapshot" in text.lower() for text in shorter["assumptions"])


@pytest.mark.parametrize("input_config", [
    {"kind": "constant", "gain": 0.5},
    {"kind": "constant", "delay_ns": 4},
    {"kind": "trace", "i": [0], "q": [0], "source": "synthetic fixture", "sequencer": "s"},
    {"kind": "loopback", "sequencer": "s", "measured": True},
])
def test_input_kind_cannot_silently_ignore_inapplicable_fields(input_config):
    with pytest.raises(ValueError, match="Unknown.*input.*fields"):
        validate_physical_config({"sequencers": {"s": {"input": input_config}}}, ["s"])
