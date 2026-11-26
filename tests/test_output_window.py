from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from q1timeline.cli import main
from q1timeline.output import Output, output
from q1timeline.output.modules import ModuleConfig
from q1timeline.output.session import OutputSession
from q1timeline.output.window import EXACT_SAMPLES, Scene, assume_parameters, parameter_roles, scene_from_output


ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ("set_awg_gain $G,$G\nmove $N,R0\nloop: play 0,1,20\nwait $T\nloop R0,@loop\n"
           "set_awg_gain 1000,1000\nplay 0,1,40\nstop")
MEMORY = {"a": {"index": 0, "data": [0.5] * 40}, "b": {"index": 1, "data": [0.0] * 40},
          "full": {"index": 2, "data": [1.0] * 20}}


def _model(program=PROGRAM):
    return output({"program": program, "waveforms": MEMORY})


def _port(module="card", port="out0", routes=None, **extra):
    return {"module": module, "port": port, "routes": routes or {"seq": "I"}, "offset": 0.0, "kind": "baseband",
            "aligned": True, **extra}


def test_roles_name_what_each_parameter_controls():
    assert parameter_roles(_model()) == {"G": ["gain"], "N": ["count", "time"], "T": ["time"]}


def test_open_parameters_are_assumed_and_reported():
    scene = Scene({"seq": _model()}, [_port()], {})
    assert {item["name"]: (item["value"], item["source"]) for item in scene.parameters} == {
        "G": (16384, "assumed"), "N": (3, "assumed"), "T": (100, "assumed")}
    assert scene.extent() == {"t0": 0, "t1": 3 * 120 + 40}
    view = scene.window(0, 400)
    assert view["exact"] and view["bins"] == 400
    seq = view["sequencers"]["seq"]
    repeat, = seq["repeats"]
    assert (repeat["count_label"], repeat["period_label"], repeat["count"], repeat["period"]) == ("N", "20 + T", 3, 120)
    assert repeat["wraps_at_zero"] and repeat["assumed"] == ["N", "T"]
    first = seq["intervals"][0]
    assert first["labels"] == ["G/32768 × wf0", "G/32768 × wf1"] and first["assumed"] == ["G", "T"]
    # The waveform holds 40 samples, so it keeps playing into the wait.
    assert (first["t0"], first["t1"]) == (0, 40)
    assert view["ports"][0]["traces"]["sum"]["max"][:40] == [0.25] * 40


def test_project_values_take_precedence_and_trials_replace_them():
    scene = Scene({"seq": _model()}, [_port()], {}, project={"G": 32767, "N": 2, "T": 4, "unused": 9})
    assert all(item["source"] == "project" for item in scene.parameters)
    assert scene.extent()["t1"] == 2 * 24 + 40
    scene.rebind({"N": 5})
    assert {item["name"]: item["source"] for item in scene.parameters} == {"G": "project", "N": "trial", "T": "project"}
    assert scene.extent()["t1"] == 5 * 24 + 40
    with pytest.raises(ValueError, match="Trial values"):
        scene.rebind({"missing": 1})


def test_a_value_outside_its_domain_is_reported_on_the_sequencer():
    scene = Scene({"seq": _model()}, [_port()], {}, trial={"T": 2})
    view = scene.window(0, 100)
    assert view["sequencers"]["seq"] == {"issue": "T = 2 is outside [4, 65535]"}
    assert view["ports"][0]["issue"] == "seq: T = 2 is outside [4, 65535]"


def test_assumed_values_respect_domains():
    values, roles = assume_parameters({"seq": _model("wait $T\nwait $T\nstop")}, {})
    assert roles == {"T": ["time"]} and 4 <= values["T"] <= 65535


def test_long_windows_report_the_range_of_each_column_without_sampling():
    program = "move 100000,R0\nnop\nshot: set_awg_gain 16384,16384\nplay 0,1,40\nwait 960\nloop R0,@shot\nstop"
    scene = Scene({"seq": _model(program)}, [_port()], {})
    extent = scene.extent()
    assert extent["t1"] == 100_000 * 1000
    assert extent["t1"] > EXACT_SAMPLES
    view = scene.window(0, extent["t1"], 500)
    assert not view["exact"] and view["bins"] == 500
    trace = view["ports"][0]["traces"]["sum"]
    # Every 200 µs column contains whole shots: silence and the 0.25 pulse.
    assert set(trace["min"]) == {0.0} and set(trace["max"]) == {0.25}
    repeat, = view["sequencers"]["seq"]["repeats"]
    assert repeat["collapsed"] and repeat["shown"] == 0
    assert len(view["sequencers"]["seq"]["intervals"]) == 2
    # Zoomed into one shot, the same scene returns every sample.
    close = scene.window(50_000_000, 50_001_000, 1000)
    assert close["exact"] and close["ports"][0]["traces"]["sum"]["max"][:41] == [0.25] * 40 + [0.0]


def test_range_matches_exact_envelope_for_a_gain_ramp():
    program = ("move 6,R0\nmove 2048,R1\nnop\nramp: set_awg_gain R1,R1\nplay 0,1,20\nadd R1,2048,R1\nnop\n"
               "loop R0,@ramp\nstop")
    scene = Scene({"seq": _model(program)}, [_port()], {})
    end = scene.extent()["t1"]
    samples = scene.models["seq"].samples(0, end, stage="filtered")[0]
    for a, b in ((0, end), (0, 20), (10, 70), (35, 45), (60, end)):
        assert scene._range("seq", a, b)[0] == (min(samples[a:b]), max(samples[a:b]))


def test_port_sum_clips_and_names_the_columns():
    loud = _model("set_awg_gain 32767,0\nupd_param 4\nplay 2,2,20\nstop")
    scene = Scene({"a": loud, "b": loud}, [_port(routes={"a": "I", "b": "I"})], {})
    port = scene.window(0, 24)["ports"][0]
    assert port["clip_bins"] == list(range(4, 24))
    assert max(port["traces"]["sum"]["max"]) == 1.0
    unaligned = Scene({"a": loud, "b": loud}, [_port(routes={"a": "I", "b": "I"}, aligned=False)], {})
    assert unaligned.window(0, 24)["ports"][0]["issue"] == "Exact alignment is required to mix this port's sequencers."


def test_time_offsets_place_sequencers_on_one_axis():
    model = _model("set_awg_gain 16384,0\nupd_param 4\nplay 0,0,20\nstop")
    scene = Scene({"seq": model}, [_port()], {"seq": -8})
    # Forty samples of waveform memory start at local 4 ns and outlast the 20 ns play.
    assert scene.extent() == {"t0": -8, "t1": 36}
    view = scene.window(-8, 36)
    assert view["sequencers"]["seq"]["time_offset"] == -8
    assert view["ports"][0]["traces"]["sum"]["max"][:6] == [0.0, 0.0, 0.0, 0.0, 0.25, 0.25]


def test_models_round_trip_through_json():
    model = _model()
    data = json.loads(json.dumps(model.to_json()))
    restored = Output.from_json(data, {str(index): list(samples) for index, samples in model.waveforms.items()})
    assert restored.segments == model.segments and restored.tail == model.tail
    bound = {"G": 20000, "N": 3, "T": 16}
    assert restored.bind(bound).samples(0, 150) == model.bind(bound).samples(0, 150)


def test_rf_module_routes_both_paths_as_an_envelope():
    module = ModuleConfig("mw", "qcm_rf", {"q0": {"out0": "IQ"}}, cluster="cluster0", lo_frequency_hz={"out0": 5.2e9})
    assert module.module_type == "QCM-RF" and module.rf and module.ports == ["out0"]
    with pytest.raises(ValueError, match="RF output"):
        module.samples({}, "out0", 0, 4)
    with pytest.raises(ValueError, match="Invalid QCM-RF output routing"):
        ModuleConfig("mw", "QCM-RF", {"q0": {"out0": "I"}})
    with pytest.raises(ValueError, match="RF output offsets"):
        ModuleConfig("mw", "QCM-RF", {"q0": {"out0": "IQ"}}, {"out0": 0.1})
    with pytest.raises(ValueError, match="Invalid QRM-RF output routing"):
        ModuleConfig("ro", "QRM-RF", {"q0": {"out1": "IQ"}})
    model = _model("set_awg_gain 16384,8192\nupd_param 4\nplay 0,0,20\nstop")
    port = {"module": "mw", "port": "out0", "routes": {"q0": "IQ"}, "kind": "rf", "aligned": True}
    traces = Scene({"q0": model}, [port], {}).window(0, 24)["ports"][0]["traces"]
    assert traces["i"]["max"][4] == 0.25 and traces["q"]["max"][4] == 0.125


def _write_project(tmp_path, modules, params=None):
    (tmp_path / "drive.q1asm").write_text(PROGRAM)
    (tmp_path / "wave.json").write_text(json.dumps({"waveforms": MEMORY}))
    config = {"sequencers": [{"id": "drive", "file": "drive.q1asm", "sequence_json": "wave.json"}],
              "modules": modules, "alignment": {"mode": "none"}}
    if params is not None:
        (tmp_path / "params.json").write_text(json.dumps(params))
        config["params"] = {"file": "params.json"}
    (tmp_path / "q1timeline.yml").write_text(json.dumps(config))
    out = tmp_path / "ir.json"
    assert main(["analyze", "--project", str(tmp_path / "q1timeline.yml"), "--no-render", "--out", str(out)]) == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_ir_carries_cluster_overview_and_names_for_project_values(tmp_path):
    ir = _write_project(tmp_path, {"control": {"type": "QCM", "cluster": "cluster0", "channel_map": {"drive": {"out0": "I"}}}},
                        {"G": 16000, "N": 2, "T": 8})
    section = ir["output"]
    assert section["modules"] == [{"name": "control", "type": "QCM", "cluster": "cluster0"}]
    assert ir["sequencers"][0]["module_type"] == "QCM"
    assert [(item["name"], item["value"], item["source"]) for item in section["parameters"]] == [
        ("G", 16000, "project"), ("N", 2, "project"), ("T", 8, "project")]
    lane = section["sequencers"]["drive"]
    # Analysis resolved the values; the open pass keeps their names for the formulas.
    assert "symbolic" in lane and lane["waveforms"].keys() == {"0", "1"}
    overview = section["overview"]
    assert overview["exact"] and section["extent"] == {"t0": 0, "t1": 2 * 28 + 40}
    assert overview["sequencers"]["drive"]["intervals"][0]["labels"][0] == "G/32768 × wf0"
    assert overview["sequencers"]["drive"]["intervals"][0]["assumed"] == []
    # The existing preview keeps its samples.
    assert section["ports"][0]["preview"]["values"][:2] == [16000 / 32768 * 0.5] * 2
    assert section["ports"][0]["kind"] == "baseband"


def test_ir_without_values_assumes_them_and_session_restores_the_scene(tmp_path):
    ir = _write_project(tmp_path, {"control": {"type": "QCM", "channel_map": {"drive": {"out0": "I"}}}})
    section = ir["output"]
    assert {item["source"] for item in section["parameters"]} == {"assumed"}
    scene = scene_from_output(section)
    extent = section["extent"]
    assert scene.window(extent["t0"], extent["t1"]) == section["overview"]

    session = OutputSession()
    assert session.load(section) == {"extent": extent, "parameters": section["parameters"]}
    tried = session.window(0, 100, 100, {"N": 7})
    assert tried["extent"]["t1"] == 7 * 120 + 40
    assert next(item for item in tried["parameters"] if item["name"] == "N")["source"] == "trial"
    with pytest.raises(ValueError, match="2000 bins"):
        session.window(0, 100, 5000)
    with pytest.raises(ValueError, match="t0 < t1"):
        session.window(5, 5)


def test_rf_project_reports_an_envelope_port(tmp_path):
    ir = _write_project(tmp_path, {"mw": {"type": "QCM-RF", "channel_map": {"drive": {"out0": "IQ"}},
                                          "lo_frequency_hz": {"out0": 5.2e9}}})
    section = ir["output"]
    assert section["modules"] == [{"name": "mw", "type": "QCM-RF", "lo_frequency_hz": {"out0": 5.2e9}}]
    port, = section["ports"]
    assert port["kind"] == "rf" and "preview" not in port
    assert set(section["overview"]["ports"][0]["traces"]) == {"i", "q"}


def test_session_process_answers_json_lines(tmp_path):
    ir = _write_project(tmp_path, {"control": {"type": "QCM", "channel_map": {"drive": {"out0": "I"}}}})
    requests = [{"id": 1, "command": "load", "output": ir["output"]},
                {"id": 2, "command": "window", "t0": 0, "t1": 50, "bins": 50},
                {"id": 3, "command": "window", "t0": 0, "t1": 50, "params": {"T": 2}},
                {"id": 4, "command": "sample"}]
    result = subprocess.run([sys.executable, "-m", "q1timeline.output.session"], text=True, capture_output=True,
                            input="".join(json.dumps(item) + "\n" for item in requests), cwd=ROOT / "src", check=True)
    load, window, invalid, unknown = map(json.loads, result.stdout.splitlines())
    assert load["id"] == 1 and load["extent"] == ir["output"]["extent"]
    assert window["window"]["bins"] == 50
    assert invalid["window"]["sequencers"]["drive"] == {"issue": "T = 2 is outside [4, 65535]"}
    assert unknown == {"id": 4, "error": "Unknown output command: sample"}
