import json
from types import SimpleNamespace

import pytest
import yaml

from q1timeline.output.modules import ModuleConfig
from q1timeline.project import ConfigLoadError, load_project_config
from q1timeline.watch.watcher import collect_watch_paths, run_watch_once
from qbstimeline.adapters.q1timeline import write_q1timeline_project
from qbstimeline.compile_worker import _write_q1asm_files, extract_q1asm_programs


MEMORY = {"program": "play 0,0,4\nstop\n", "waveforms": {"flat": {"index": 0, "data": [1, 1, 1, 1]}}}


def test_compiled_sequence_and_settings_reach_hardware_port(tmp_path):
    settings = SimpleNamespace(sequence=MEMORY, channel_name="real_output_1",
                               connected_output_indices=(1,), init_gain_awg_path_I=0.5, nco_en=False)
    compiled = {"cluster": {"slot2": {"instrument_type": "QRM", "settings": {"offset_ch0_path_Q": 0.125},
                                      "sequencers": {"seq0": settings}}}}
    programs = extract_q1asm_programs(compiled)
    _write_q1asm_files(tmp_path, programs)
    project = write_q1timeline_project(tmp_path, programs)
    config = load_project_config(project)
    seq = config.sequencers[0]
    stored = json.loads(seq.sequence_json.read_text())
    assert {k: stored[k] for k in MEMORY} == MEMORY
    assert stored["compiled_settings"]["connected_output_indices"] == [1]
    assert stored["output_settings"]["static_gain"] == [0.5, 1]
    assert stored["compiled_module_settings"] == {"offset_ch0_path_Q": 0.125}
    assert config.modules["cluster/slot2"].channel_map == {seq.id: {"out1": "I"}}
    assert run_watch_once(project, tmp_path / "view").ok
    port, = json.loads((tmp_path / "view/timeline_ir.json").read_text())["output"]["ports"]
    assert port["preview"]["values"] == [0.75] * 4


def test_snapshot_volts_and_connections_match_explicit_yaml(tmp_path):
    def param(value):
        return {"value": value}

    (tmp_path / "s.q1asm").write_text("stop\n")
    snapshot = {"parameters": {"out0_offset": param(-0.25), "out1_offset": param(0.0)},
                "submodules": {"sequencer2": {"parameters": {"connect_out0": param("Q"), "connect_out1": param("off")}}}}
    station = tmp_path / "station.json"
    station.write_text(json.dumps({"station": {"instruments": {"board": snapshot}}}))
    config = {"sequencers": [{"id": "s", "file": "s.q1asm", "module": "board", "sequencer_index": 2}],
              "modules": {"board": {"type": "QRM", "snapshot": "station.json",
                                    "snapshot_path": ["station", "instruments", "board"]}}}
    project = tmp_path / "q1timeline.yml"
    project.write_text(yaml.safe_dump(config))
    module = load_project_config(project).modules["board"]
    assert module.channel_map == {"s": {"out0": "Q"}}
    assert module.output_offsets == {"out0": -0.5, "out1": 0.0}
    assert station in collect_watch_paths(project)
    config["modules"]["board"]["snapshot_path"] = ["missing"]
    project.write_text(yaml.safe_dump(config))
    with pytest.raises(ConfigLoadError):
        load_project_config(project)
    assert station in collect_watch_paths(project)


def test_sum_paths_then_module_offset_then_clip():
    import q1lens

    # One contribution is above full scale by itself. Early saturation changes the sum.
    a = q1lens.output({**MEMORY, "program": "set_awg_offs 16384,0\n" + MEMORY["program"]})
    b = q1lens.output({**MEMORY, "program": "set_awg_gain 0,-24576\n" + MEMORY["program"]})
    module = ModuleConfig("qcm", "QCM", {"a": {"out0": "I"}, "b": {"out0": "Q"}})
    assert module.samples({"a": a, "b": b}, "out0", 0, 4) == [0.75] * 4
    module = ModuleConfig("qrm", "QRM", {"a": {"out0": "I"}}, {"out0": 0.25})
    assert module.samples({"a": a}, "out0", 0, 4, clip=False) == [1.75] * 4
    assert module.samples({"a": a}, "out0", 0, 4) == [1.0] * 4


def test_snapshot_static_signal_controls_reach_watch_cli_and_verification(tmp_path):
    from q1timeline.cli import main
    from q1timeline.verification import verify_file

    seq = tmp_path / "s.q1asm"
    seq.write_text(";@assert peak(out0) <= .75\nplay 0,0,4\nset_awg_gain 16384,0\n"
                   "set_awg_offs -4096,0\nplay 0,0,4\nstop")
    (tmp_path / "waves.json").write_text(json.dumps(MEMORY))
    params = {"connect_out0": "I", "connect_out1": "off", "gain_awg_path0": .5, "gain_awg_path1": 1,
              "offset_awg_path0": .25, "offset_awg_path1": 0, "mod_en_awg": False,
              "nco_freq": 250000000, "nco_phase_offs": 90}
    snapshot = {"parameters": {"out0_offset": {"value": 0}, "out1_offset": {"value": 0}},
                "submodules": {"sequencer0": {"parameters": {k: {"value": v} for k, v in params.items()}}}}
    (tmp_path / "snapshot.json").write_text(json.dumps(snapshot))
    project = tmp_path / "q1timeline.yml"
    project.write_text(yaml.safe_dump({"sequencers": [{"id": "s", "file": "s.q1asm", "module": "board",
                                                      "sequencer_index": 0, "sequence_json": "waves.json"}],
                                       "modules": {"board": {"type": "QRM", "snapshot": "snapshot.json"}}}))
    assert run_watch_once(project, tmp_path / "watch").ok
    assert main(["analyze", "--project", str(project), "--out", str(tmp_path / "cli.json")]) == 0
    for file in (tmp_path / "watch/timeline_ir.json", tmp_path / "cli.json"):
        ports = json.loads(file.read_text())["output"]["ports"]
        assert ports[0]["preview"]["values"] == [.75] * 4 + [.375] * 4
    assert verify_file(seq, project=project)["checks"][0]["status"] == "proved"


def test_snapshot_phase_is_added_to_q1asm_phase_and_reset_together():
    import q1lens

    src = ("set_ph 250000000\nplay 0,0,4\nreset_ph\nplay 0,0,4\nstop")
    model = q1lens.output({**MEMORY, "program": src, "output_settings": {
        "nco_enabled": True, "static_phase_steps": 250000000, "static_gain": [1, 0],
    }})
    i, q = model.samples(0, 8, stage="nco")
    assert i == pytest.approx([-1] * 4 + [1] * 4)
    assert q == pytest.approx([0] * 8, abs=1e-15)


def test_conflicting_sequencer_membership_is_rejected(tmp_path):
    (tmp_path / "s.q1asm").write_text("stop\n")
    module = {"type": "QRM", "channel_map": {"s": {"out0": "I"}}}
    project = tmp_path / "q1timeline.yml"
    project.write_text(yaml.safe_dump({"sequencers": [{"id": "s", "file": "s.q1asm"}],
                                       "modules": {"one": module, "two": module}}))
    with pytest.raises(ConfigLoadError) as error:
        load_project_config(project)
    assert any("conflicting sequencer" in d.message for d in error.value.diagnostics)


def test_aligned_mix_gates_paths_at_local_zero_and_retains_module_dc():
    from q1timeline.analysis.alignment import align_timelines
    from q1timeline.analysis.interpreter import interpret_program
    from q1timeline.output import from_state
    from q1timeline.output.timeline import output_section
    from q1timeline.q1asm.parser import parse_q1asm

    states = []
    outputs = {}
    for seq, wait, offset in (("a", 4, 0.125), ("b", 8, 0.25)):
        program = parse_q1asm(f"wait {wait}\nplay 0,0,4\nstop", file=seq)
        state = interpret_program(program, sequencer_id=seq)
        mem = {"waveforms": {"pulse": {"index": 0, "data": [.25] * 4}}, "output_settings": {"offset": [offset, 0]}}
        state.metadata["output_sequence"] = mem
        outputs[seq] = from_state(state, mem)
        states.append(state)
    module = ModuleConfig("card", "QCM", {"a": {"out0": "I"}, "b": {"out0": "I"}}, {"out0": -.125})
    align_timelines(states, mode="first_play")
    preview = output_section(states, {"card": module})["ports"][0]["preview"]
    expected = [.125] * 4 + [.25] * 4 + [.75] * 4
    assert preview == {"t0": -8, "dt_ns": 1, "time_basis": "aligned", "values": expected}
    assert module.samples(outputs, "out0", -10, 4, time_offsets={"a": -4, "b": -8}) == [-.125] * 2 + expected
    assert module.samples(outputs, "out0", 6, 8, time_offsets={"a": -4, "b": -8}) == [.25] * 2
    with pytest.raises(ValueError, match="alignment offset for b"):
        module.samples(outputs, "out0", 0, 2, time_offsets={"a": -4})
