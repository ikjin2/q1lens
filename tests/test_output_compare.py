import json
import math

import pytest
import yaml

from q1lens.cli import main


def project(root, wave, *, program="play 0,0,4\nstop", params=None, fir=None,
            mode="none", routes=None, bias=0, peer=None):
    root.mkdir()
    programs = {"drive": program}
    if peer:
        programs["peer"] = peer
    sequence = {"waveforms": {"pulse": {"index": 0, "data": wave}}}
    if fir:
        sequence["output_settings"] = {"fir": fir}
    (root / "wave.sequence.json").write_text(json.dumps(sequence))
    for seq, text in programs.items():
        (root / f"{seq}.q1asm").write_text(text)
    config = {"sequencers": [{"id": seq, "file": f"{seq}.q1asm", "sequence_json": "wave.sequence.json"}
                              for seq in programs],
              "alignment": {"mode": mode},
              "modules": {"card": {"type": "QCM", "channel_map": routes or {s: {"out0": "I"} for s in programs},
                                   "output_offsets": {"out0": bias}}}}
    if params is not None:
        (root / "params.json").write_text(json.dumps(params))
        config["params"] = {"file": "params.json"}
    path = root / "q1timeline.yml"
    path.write_text(yaml.safe_dump(config))
    return path


def compare(capsys, left, right, *, start=0, end=8, port="out0", options=()):
    rc = main(["compare-output", str(left), str(right), "--port", port, "--start-ns", str(start),
               "--end-ns", str(end), *map(str, options), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert rc == {"proved": 0, "counterexample": 1, "unknown": 2}[report["status"]]
    return report


def test_same_program_compares_memory_only_inside_the_half_open_window(tmp_path, capsys):
    left = project(tmp_path / "a", [.5, .5, -.25, .125])
    right = project(tmp_path / "b", [.5, .5, .75, .25])
    assert compare(capsys, left, right, end=2)["status"] == "proved"
    difference = compare(capsys, left, right, start=1, port="card.out0")
    assert difference["port"] == "card.out0"
    assert difference["first_difference"] == {"time_ns": 2, "before": -.25, "after": .75}
    assert compare(capsys, right, left, start=3)["first_difference"] == {"time_ns": 3, "before": .25, "after": .125}
    assert compare(capsys, left, right, start=4)["status"] == "proved"


def test_fir_dc_mixing_and_clipping_use_the_project_port_model(tmp_path, capsys):
    left = project(tmp_path / "a", [.5, .5], fir=[1, 1], bias=.25, peer="play 0,0,4\nstop")
    right = project(tmp_path / "b", [.5, .25], fir=[1, 1], bias=.25, peer="play 0,0,4\nstop")
    # Both sums saturate through 1 ns. The FIR tail is the first visible difference.
    assert compare(capsys, left, right)["first_difference"] == {"time_ns": 2, "before": 1., "after": .75}


def test_reordered_routes_match_but_one_ulp_of_wave_memory_still_differs(tmp_path, capsys):
    def mix(root, order, last):
        path = project(root, [.7, .7])
        config = yaml.safe_load(path.read_text())
        config["sequencers"] = []
        for seq, value in zip("abc", (.7, -.7, .2)):
            data = [value, last if seq == "c" else value]
            sidecar = root / f"{seq}.json"
            sidecar.write_text(json.dumps({"waveforms": {"pulse": {"index": 0, "data": data}}}))
            config["sequencers"].append({"id": seq, "file": "drive.q1asm", "sequence_json": sidecar.name})
        config["modules"]["card"]["channel_map"] = {seq: {"out0": "I"} for seq in order}
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        return path

    left = mix(tmp_path / "left", "abc", .2)
    right = mix(tmp_path / "right", "acb", .2)
    assert compare(capsys, left, right)["status"] == "proved"
    next_sample = math.nextafter(.2, 1.)
    changed = mix(tmp_path / "changed", "acb", next_sample)
    assert compare(capsys, left, changed)["first_difference"] == {"time_ns": 1, "before": .2, "after": next_sample}


def test_aligned_window_can_find_a_pre_anchor_sample(tmp_path, capsys):
    before_sync = "play 0,0,4\nwait_sync 4\nstop"
    left = project(tmp_path / "a", [.25], program=before_sync, mode="first_wait_sync")
    right = project(tmp_path / "b", [.75], program=before_sync, mode="first_wait_sync")
    difference = compare(capsys, left, right, start=-8, end=0)
    assert difference["time_basis"] == "first_wait_sync"
    assert difference["first_difference"] == {"time_ns": -4, "before": .25, "after": .75}


def test_project_defaults_must_supply_one_shared_input_or_use_an_explicit_map(tmp_path, capsys):
    source = "set_awg_gain G,0\nplay 0,0,4\nstop"
    left = project(tmp_path / "a", [1], program=source, params={"G": 8192})
    right = project(tmp_path / "b", [.5], program=source)
    assert compare(capsys, left, right)["first_difference"] == {"time_ns": 0, "before": .25, "after": .125}
    right = project(tmp_path / "c", [.5], program=source, params={"G": 16384})
    assert "Conflicting project inputs: G" in compare(capsys, left, right)["message"]
    report = compare(capsys, left, right, options=["--params", right.parent / "params.json"])
    assert report["first_difference"] == {"time_ns": 0, "before": .5, "after": .25}


def test_shared_input_does_not_replace_a_local_def(tmp_path, capsys):
    left = project(tmp_path / "a", [1], program="set_awg_gain G,0\nplay 0,0,4\nstop", params={"G": 8192})
    right = project(tmp_path / "b", [1], program=".DEF G 16384\nset_awg_gain $G,0\nplay 0,0,4\nstop")
    assert compare(capsys, left, right)["first_difference"] == {"time_ns": 0, "before": .25, "after": .5}


def test_unresolved_or_missing_output_returns_unknown(tmp_path, capsys):
    left = project(tmp_path / "a", [1])
    right = project(tmp_path / "b", [1], program="wait D\nplay 0,0,4\nstop")
    assert compare(capsys, left, right)["status"] == "unknown"
    (left.parent / "wave.sequence.json").unlink()
    assert "wave.sequence.json" in compare(capsys, left, left)["message"]


def test_malformed_sidecar_settings_with_project_filter_return_json(tmp_path, capsys):
    path = project(tmp_path / "drive", [1])
    config = yaml.safe_load(path.read_text())
    config["simulation"] = {"physical": {"sequencers": {"drive": {"fir": [1, 1]}}}}
    path.write_text(yaml.safe_dump(config))
    memory = path.parent / "wave.sequence.json"
    data = json.loads(memory.read_text())
    memory.write_text(json.dumps({**data, "output_settings": 5}))

    report = compare(capsys, path, path)
    assert report["status"] == "unknown" and "settings must be a mapping" in report["message"]


def test_rejects_ambiguous_port_and_invalid_windows(tmp_path, capsys):
    path = project(tmp_path / "a", [1])
    config = yaml.safe_load(path.read_text())
    config["modules"]["other"] = {"type": "QCM", "channel_map": {}, "output_offsets": {"out0": 0}}
    path.write_text(yaml.safe_dump(config))
    assert "Ambiguous port" in compare(capsys, path, path)["message"]
    assert compare(capsys, path, path, port="card.out0")["status"] == "proved"
    assert compare(capsys, path, path, start=4, end=4)["status"] == "unknown"
    assert compare(capsys, path, path, port="absent.out0")["status"] == "unknown"
    assert compare(capsys, path, path, port="card.out0", start=-1)["status"] == "unknown"


def test_rejects_different_port_voltage_ranges_and_project_clocks(tmp_path, capsys):
    left = project(tmp_path / "a", [.25])
    right = project(tmp_path / "b", [.25])
    config = yaml.safe_load(right.read_text())
    config["modules"]["card"]["type"] = "QRM"
    right.write_text(yaml.safe_dump(config))
    assert "different hardware" in compare(capsys, left, right)["message"]
    config["modules"]["card"]["type"] = "QCM"
    config["analysis"] = {"execution_mode": "simulation"}
    right.write_text(yaml.safe_dump(config))
    assert "different time bases" in compare(capsys, left, right)["message"]


@pytest.mark.parametrize("source", ["wait 4\nstop", "wait_sync 4\nstop"])
def test_unknown_multi_sequencer_anchor_is_not_silently_zero(tmp_path, capsys, source):
    path = project(tmp_path / "a", [1], program=source, peer="play 0,0,4\nstop", mode="first_wait_sync")
    report = compare(capsys, path, path)
    assert report["status"] == "unknown"
    assert "alignment" in report["message"] or "barrier" in report["message"]
