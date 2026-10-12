from __future__ import annotations

import json
import math

import pytest

from q1timeline.simulation.signals import SignalModel, validate_physical_config


def sequence(i=None, q=None, weights=None):
    return {
        "waveforms": {"i": {"index": 0, "data": i or [0]}, "q": {"index": 1, "data": q or [0]}},
        "weights": weights or {},
        "acquisitions": {"result": {"index": 0, "num_bins": 2}},
    }


def samples(model, stage, path="i", end=4, seq="drive"):
    return next(trace["samples"] for trace in model.export(end)[seq]["traces"] if trace["stage"] == stage and trace["path"] == path)


def test_dac_gain_offset_clipping_and_signed_quantization():
    model = SignalModel({"sequencers": {"drive": {"dac_bits": 4}}}, {"drive": sequence([1, -1, 0.5, 0])})
    model.apply("drive", 0, "play", [0, 1, 4], {"awg_gain": [32768, 32768], "awg_offset": [16384, 0]})
    assert samples(model, "gain_offset") == [1.5, -0.5, 1, 0.5]
    assert samples(model, "dac") == [0.875, -0.5, 0.875, 0.5]
    assert model.export(4)["drive"]["clipping"]["dac_samples"] == 2


def test_play_replaces_waveform_and_zeroes_after_memory_end():
    model = SignalModel({}, {"drive": sequence([0.5, 0.25, -0.5])})
    model.apply("drive", 0, "play", [0, 1, 4], {})
    model.apply("drive", 2, "play", [1, 0, 4], {})
    assert samples(model, "waveform", end=6) == [0.5, 0.25, 0, 0, 0, 0]
    assert samples(model, "waveform", "q", end=6) == [0, 0, 0.5, 0.25, -0.5, 0]


def test_nco_keeps_phase_continuous_when_frequency_changes():
    model = SignalModel({"sequencers": {"drive": {"offset": [0.5, 0], "nco_frequency_hz": 125e6}}}, {"drive": sequence()})
    model.apply("drive", 2, "upd_param", [4], {"frequency": 0, "_updated_fields": ["frequency"]})
    assert samples(model, "nco") == pytest.approx([0.5, math.sqrt(0.125), 0, 0], abs=1e-12)
    assert samples(model, "nco", "q") == pytest.approx([0, math.sqrt(0.125), 0.5, 0.5], abs=1e-12)


def test_phase_delta_is_only_applied_on_updated_packets_and_reset_resets_all():
    model = SignalModel({"sequencers": {"drive": {"offset": [0.5, 0]}}}, {"drive": sequence()})
    model.apply("drive", 0, "upd_param", [1], {"phase_delta": 250_000_000, "_updated_fields": ["phase_delta"]})
    model.apply("drive", 1, "upd_param", [1], {"phase_delta": 250_000_000, "_updated_fields": []})
    model.apply("drive", 2, "upd_param", [1], {"phase_delta": 250_000_000, "_updated_fields": ["phase_delta"]})
    model.apply("drive", 3, "upd_param", [1], {"_phase_reset": True, "_updated_fields": []})
    assert samples(model, "nco") == pytest.approx([0, 0, -0.5, 0.5], abs=1e-12)
    assert samples(model, "nco", "q") == pytest.approx([0.5, 0.5, 0, 0], abs=1e-12)


def test_fir_impulse_and_iir_recursion_keep_state_across_render_calls():
    model = SignalModel({"sequencers": {"drive": {"fir": [1, 0.5], "iir": {"b": [1], "a": [1, -0.5]}}}}, {"drive": sequence([0.5])})
    model.apply("drive", 0, "play", [0, 1, 4], {})
    assert samples(model, "filtered", end=2) == [0.5, 0.5]
    assert samples(model, "filtered", end=4) == [0.5, 0.5, 0.25, 0.125]


def test_acquisition_completion_includes_drive_changes_during_integration():
    # The receiver is first in the mapping to verify render dependency ordering.
    model = SignalModel({"sequencers": {"readout": {"input": {"kind": "loopback", "sequencer": "drive"}, "threshold": 1.5}}}, {"readout": sequence(), "drive": sequence()})
    model.apply("drive", 0, "upd_param", [2], {"awg_offset": [8192, 0]})
    model.apply("drive", 2, "upd_param", [2], {"awg_offset": [24576, 0]})
    result = model.acquire("readout", 0, 4, 0, 0)
    assert result["i"] == 2
    assert result["q"] == 0
    assert result["thresholded"] == 1
    assert result["source"]["kind"] == "loopback"
    assert result["integration_length_source"] == "instruction_duration_fallback"


def test_loopback_delay_gain_and_phase_are_explicit():
    model = SignalModel({"sequencers": {
        "drive": {"offset": [0.5, 0]},
        "readout": {"input": {"kind": "loopback", "sequencer": "drive", "delay_ns": 2, "gain": 0.5, "phase_turns": 0.25}},
    }}, {"drive": sequence(), "readout": sequence()})
    result = model.acquire("readout", 0, 4, 0, 0)
    assert result["i"] == 0
    assert result["q"] == 0.5


def test_measured_input_provenance_and_missing_coverage_never_make_a_result():
    model = SignalModel({"sequencers": {"drive": {"input": {"kind": "trace", "i": [0.5, 0.5], "q": [0, 0], "source": "scope.csv", "measured": True}}}}, {"drive": sequence()})
    result = model.acquire("drive", 0, 2, 0, 0)
    assert result["source"] == {"kind": "measured_input", "label": "scope.csv", "measurement_claim": "user-supplied"}
    assert model.acquire("drive", 1, 3, 0, 1) is None
    exported = model.export(4)["drive"]
    assert len(exported["acquisitions"]) == 1
    assert samples(model, "adc") == [0.5, 0.5, None, None]
    json.dumps(exported, allow_nan=False)


def test_unknown_input_returns_none_and_has_no_fabricated_adc_trace():
    model = SignalModel({}, {"drive": sequence()})
    assert model.acquire("drive", 0, 4, 0, 0) is None
    assert not any(trace["stage"] == "adc" for trace in model.export(4)["drive"]["traces"])


def test_weight_duration_and_weighted_integration():
    weights = {"i": {"index": 0, "data": [1, -1, 1, -1]}, "q": {"index": 1, "data": [1, 1]}}
    model = SignalModel({"sequencers": {"drive": {"input": {"kind": "constant", "i": 0.5, "q": 0.25}}}}, {"drive": sequence(weights=weights)})
    assert model.acquisition_duration("drive", 100, [0, 1]) == 4
    result = model.acquire("drive", 0, 4, 0, 0, weights=[0, 1])
    assert (result["i"], result["q"]) == (0, 0.5)
    assert result["integration_length_source"] == "weight_memory"
    with pytest.raises(ValueError, match="Missing integration weights"):
        model.acquisition_duration("drive", 4, [0, 7])


def test_threshold_rotation_uses_unnormalized_integral_and_bins_accumulate():
    model = SignalModel({"sequencers": {"drive": {"input": {"kind": "constant", "i": 0, "q": 0.5}, "threshold_rotation_deg": -90, "threshold": 1.5, "integration_length_ns": 4}}}, {"drive": sequence()})
    assert model.acquisition_duration("drive", 100) == 4
    for _ in range(2):
        result = model.acquire("drive", 0, 4, 0, 0)
        assert result["thresholded"] == 1
        assert result["mean_q"] == 0.5
        assert result["integration_length_source"] == "configured"
    entry = model.export(4)["drive"]["bins"][0]
    assert (entry["count"], entry["sum_q"], entry["mean_q"], entry["thresholded_count"]) == (2, 4, 2, 2)


def test_seeded_noise_is_reproducible_and_render_chunk_independent():
    config = {"seed": 42, "sequencers": {"drive": {"input": {"kind": "constant", "noise_std": 0.1}}}}
    first = SignalModel(config, {"drive": sequence()})
    second = SignalModel(config, {"drive": sequence()})
    first.export(2)
    assert samples(first, "adc", end=20) == samples(second, "adc", end=20)
    assert len(set(samples(first, "adc", end=20))) > 1


def test_demodulation_uses_receiver_nco():
    model = SignalModel({"sequencers": {
        "drive": {"offset": [0.5, 0], "nco_frequency_hz": 250e6},
        "readout": {"nco_frequency_hz": 250e6, "demodulate": True, "input": {"kind": "loopback", "sequencer": "drive"}},
    }}, {"drive": sequence(), "readout": sequence()})
    result = model.acquire("readout", 0, 4, 0, 0)
    assert result["i"] == pytest.approx(2)
    assert result["q"] == pytest.approx(0, abs=1e-12)
    assert samples(model, "adc", seq="readout") == pytest.approx([0.5, 0, -0.5, 0], abs=1e-12)
    assert samples(model, "demodulated", seq="readout") == pytest.approx([0.5] * 4)


def test_sample_limits_decimation_and_rf_envelope_metadata():
    model = SignalModel({"max_samples": 10, "max_export_samples": 3, "sequencers": {"drive": {"lo_frequency_hz": 6e9, "input": {"kind": "constant"}}}}, {"drive": sequence()})
    with pytest.raises(ValueError, match="sample limit"):
        model.acquire("drive", 5, 10, 0, 0)
    exported = model.export(100)["drive"]
    assert exported["truncated"] is True
    assert all(len(trace["samples"]) <= 3 for trace in exported["traces"])
    assert exported["rf"] == {"representation": "complex_envelope", "lo_frequency_hz": 6e9, "carrier_samples": False}
    assert exported["export_stride"] == 4


@pytest.mark.parametrize("config", [
    {"sample_period_ns": 0}, {"sample_period_ns": True}, {"max_samples": 1_000_001},
    {"sequencers": {"drive": {"dac_bits": 1}}},
    {"sequencers": {"drive": {"nco_frequency_hz": math.inf}}},
    {"sequencers": {"drive": {"nco_frequency_hz": 500e6}}},
    {"sequencers": {"drive": {"iir": {"a": [0]}}}},
    {"sequencers": {"drive": {"fir": []}}},
    {"sequencers": {"drive": {"input": {"kind": "trace", "i": [0], "q": [0]}}}},
    {"sequencers": {"drive": {"input": {"kind": "loopback", "sequencer": "unknown"}}}},
    {"sequencers": {"drive": {"input": {"kind": "constant", "noise_std": -1}}}},
    {"sequencers": {"drive": {"input": {"kind": []}}}},
    {"sequencers": {"drive": {"unsupported": 3}}},
])
def test_invalid_configuration_is_rejected(config):
    with pytest.raises(ValueError):
        validate_physical_config(config, ["drive"])


def test_missing_waveform_and_acquisition_definitions_are_explicit_errors():
    model = SignalModel({"sequencers": {"drive": {"input": {"kind": "constant"}}}}, {"drive": {}})
    with pytest.raises(ValueError, match="Missing waveform"):
        model.apply("drive", 0, "play", [0, 1, 4], {})
    with pytest.raises(ValueError, match="Missing acquisition"):
        model.acquire("drive", 0, 4, 0, 0)


def test_event_cannot_rewrite_already_rendered_history():
    model = SignalModel({}, {"drive": sequence()})
    model.export(4)
    with pytest.raises(ValueError, match="already sampled"):
        model.apply("drive", 3, "play", [0, 1, 4], {})


def test_coarse_grid_integrates_partial_cells_and_accepts_later_events():
    model = SignalModel({"sample_period_ns": 2, "sequencers": {"drive": {"input": {"kind": "constant", "i": 0.5}}}}, {"drive": sequence()})
    assert model.acquire("drive", 1, 2, 0, 0)["i"] == 1
    model.apply("drive", 3, "upd_param", [1], {"awg_offset": [16384, 0]})
    assert samples(model, "dac", end=6) == [0, 0, 0.5]


def test_coarse_adc_grid_sums_all_weight_samples_in_each_cell():
    weights = {"alternating": {"index": 0, "data": [1, -1, 1, -1]}}
    model = SignalModel({"sample_period_ns": 2, "sequencers": {"drive": {"input": {"kind": "constant", "i": 0.5}}}}, {"drive": sequence(weights=weights)})
    result = model.acquire("drive", 0, 4, 0, 0, [0, 0])
    assert result["i"] == 0


def test_stop_does_not_repeat_pending_phase_delta():
    model = SignalModel({"sequencers": {"drive": {"offset": [0.5, 0]}}}, {"drive": sequence()})
    model.apply("drive", 0, "upd_param", [1], {"phase_delta": 250_000_000, "_updated_fields": ["phase_delta"]})
    model.apply("drive", 2, "stop", [], {"phase_delta": 250_000_000})
    assert samples(model, "nco", "q") == pytest.approx([0.5] * 4)


@pytest.mark.parametrize("memory", [
    {"waveforms": []}, {"waveforms": {"bad": 3}},
    {"acquisitions": []}, {"acquisitions": {"bad": []}},
    {"acquisitions": {"a": {"index": 0, "num_bins": 1}, "b": {"index": 0, "num_bins": 1}}},
    {"waveforms": {"a": {"index": 0, "data": [1.01]}}},
])
def test_malformed_or_ambiguous_sequence_memory_is_rejected(memory):
    with pytest.raises(ValueError):
        SignalModel({}, {"drive": memory})


def test_unstable_nonfinite_filter_cannot_export_infinity():
    model = SignalModel({"sequencers": {"drive": {"offset": [0.5, 0], "iir": {"a": [1, -1e300], "b": [1]}}}}, {"drive": sequence()})
    with pytest.raises(ValueError, match="non-finite"):
        model.export(4)


def test_large_finite_amplitudes_clip_without_integer_conversion_overflow():
    model = SignalModel({"sequencers": {"drive": {"offset": [1e308, -1e308], "input": {"kind": "constant", "i": 1e308, "q": -1e308}}}}, {"drive": sequence()})
    assert samples(model, "dac", end=1) == [32767 / 32768]
    assert samples(model, "adc", "q", end=1) == [-1]
    json.dumps(model.export(1), allow_nan=False)
