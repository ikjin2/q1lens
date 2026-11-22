import json
from pathlib import Path

import pytest
import yaml

from q1timeline.verification import verify_file


def verify(tmp_path, program, waveforms=(), *, peer=None, bias=0.0, settings=None, alignment="none",
           external_inputs=None, simulation_inputs=None, **options):
    source = tmp_path / "drive.q1asm"
    source.write_text(program)
    memory = {"waveforms": {str(i): {"index": i, "data": data} for i, data in enumerate(waveforms)}}
    if settings:
        memory["output_settings"] = settings
    (tmp_path / "waves.json").write_text(json.dumps(memory))
    seqs = [{"id": "drive", "file": "drive.q1asm", "sequence_json": "waves.json"}]
    routes = {"drive": {"out0": "I", "out1": "Q"}}
    if peer:
        (tmp_path / "peer.q1asm").write_text(peer)
        seqs.append({"id": "peer", "file": "peer.q1asm", "sequence_json": "waves.json"})
        routes["peer"] = {"out0": "I"}
    config = {"sequencers": seqs, "simulation": {"startup_delay_ns": 256},
              "modules": {"card": {"type": "QCM", "channel_map": routes, "output_offsets": {"out0": bias}}}}
    if alignment is not None:
        config["alignment"] = {"mode": alignment}
    if external_inputs:
        config["external_inputs"] = external_inputs
    if simulation_inputs is not None:
        config["analysis"] = {"execution_mode": "simulation"}
        config["simulation"]["inputs"] = simulation_inputs
    project = tmp_path / "q1timeline.yml"
    project.write_text(yaml.safe_dump(config))
    return verify_file(source, project=project, include_replay=True, **options)["checks"]


def test_free_parameters_interval_proof_and_concrete_witness(tmp_path):
    checks = verify(tmp_path, """;@assume 8190 <= G <= 8193
;@assert no_clip
;@assert peak(out0) <= .25
set_awg_gain $G,$G
play 0,0,8
stop""", [[1.0] * 8])
    safe, failed = checks[:2]
    assert safe["status"] == "proved" and safe["method"] == "interval"
    assert failed["status"] == "counterexample"
    witness = failed["counterexample"]
    assert witness["inputs"] == {"G": 8193} and witness["value"] == 8193 / 32768
    assert witness["earliest_sample"] and witness["time_ns"] == 0
    assert witness["line"] == 5 and witness["port"] == "out0"
    ir = witness["timeline"]
    assert ir["execution"]["drive"]["mode"] == "concrete"
    assert any(e["id"] == witness["event_id"] for e in ir["events"])
    lane = next(p for p in ir["output"]["ports"] if p["port"] == "out0")
    assert lane["preview"]["values"][witness["time_ns"]] == witness["value"]


def test_net_zero_uses_truncated_waveform_and_current_latch(tmp_path):
    text = """.DEF G 4096
;@assume 4095 <= G <= 4097
;@assert net_zero
set_awg_gain $G,$G
upd_param 4
play 0,0,4
play 1,1,4
stop"""
    safe = verify(tmp_path, text, [[1, 1, 1, 1, .5], [-1] * 4])[0]
    assert safe["status"] == "proved"
    failed = verify(tmp_path, text, [[1, 1, 1, 1, .5], [-1, 0, -1, -1]])[0]
    assert failed["status"] == "counterexample"
    witness = failed["counterexample"]
    assert witness["value"] == witness["inputs"]["G"] / 32768


@pytest.mark.parametrize("first,last,status", [(8, 12, "proved"), (4, 8, "counterexample")])
def test_symbolic_overlap_in_module_sum(tmp_path, first, last, status):
    text = f""";@assume {first} <= T <= {last}
;@assert no_clip
wait T
set_awg_gain 24576,0
play 0,0,8
stop"""
    check = verify(tmp_path, text, [[1] * 8], peer="set_awg_gain 24576,0\nplay 0,0,8\nstop")[0]
    assert check["status"] == status
    if status == "counterexample":
        w = check["counterexample"]
        assert w["inputs"]["T"] == w["time_ns"] == 4 and w["value"] == 1.5
    else:
        assert "Z3" in check["method"]


def test_nco_envelope_certificate_and_unknown_signed_area(tmp_path):
    checks = verify(tmp_path, """;@assert no_clip
;@assert net_zero
set_awg_gain 24576,0
set_ph 250000000
play 0,0,4
stop""", [[1] * 4])
    assert checks[0]["status"] == "proved"
    assert checks[1]["status"] == "unknown" and "NCO" in checks[1]["message"]


def test_offset_only_area_and_clip_boundary(tmp_path):
    checks = verify(tmp_path, ";@assert no_clip\n;@assert net_zero\nwait 8\nstop", bias=-1)
    assert checks[0]["status"] == "proved"
    assert checks[1]["status"] == "unknown" and "persistent" in checks[1]["message"]


def test_duration_domain_and_missing_memory_cannot_prove(tmp_path):
    checks = verify(tmp_path, ";@assume 3 <= T <= 5\n;@assert no_clip\nwait T\nstop")
    assert checks[0]["status"] == "unknown"
    checks = verify(tmp_path, ";@assert no_clip\nplay 0,0,8\nstop")
    assert checks[0]["status"] == "unknown"


@pytest.mark.parametrize(("body", "limit"), [
    ("set_awg_gain R0,R0\nset_awg_gain 0,0\nupd_param 4", 32767),
    ("set_awg_offs R0,R0\nset_awg_offs 0,0\nupd_param 4", 32767),
    ("acquire 0,R0,4", 16777215),
    ("acquire_digital 0,R0,4", 16777215),
    ("acquire_ttl 0,R0,1,4", 16777215),
    ("set_latch_en R0,4", 1),
])
@pytest.mark.parametrize("above_limit", [False, True])
def test_port_proof_requires_every_issued_operand_to_be_valid(tmp_path, body, limit, above_limit):
    from q1timeline.analysis.interpreter import interpret_program
    from q1timeline.q1asm.parser import parse_q1asm

    low = limit - 1 + above_limit
    program = f"move X,R0\nnop\n{body}\nstop"
    statuses = []
    for x in (low, low + 1):
        state = interpret_program(parse_q1asm(program), sequencer_id="s", params={"X": x}, execution_mode="concrete")
        statuses.append(state.metadata["execution"]["status"])
    assert statuses[0] == "completed"
    assert (statuses[1] == "completed") == (not above_limit)
    source = f";@assume {low} <= X <= {low + 1}\n;@assert no_clip\n" + program
    assert verify(tmp_path, source)[0]["status"] == ("proved" if all(s == "completed" for s in statuses) else "unknown")


@pytest.mark.parametrize("first_bin", [16777214, 16777215])
def test_port_proof_checks_acquisition_bin_on_later_loop_laps(tmp_path, first_bin):
    source = (f";@assert no_clip\nmove 2,R0\nmove {first_bin},R1\nnop\n"
              "lap: acquire 0,R1,4\nadd R1,1,R1\nnop\nloop R0,@lap\nstop")
    assert verify(tmp_path, source)[0]["status"] == ("proved" if first_bin + 1 < 2**24 else "unknown")


def test_port_proof_keeps_constant_bin_compact_when_reloaded_every_lap(tmp_path):
    source = (";@assert no_clip\nmove 100000,R0\nlap: move 1,R1\nnop\n"
              "acquire 0,R1,4\nloop R0,@lap\nstop")
    assert verify(tmp_path, source, max_steps=50)[0]["status"] == "proved"


def test_static_settings_compose_with_latched_gain_and_offset(tmp_path):
    checks = verify(tmp_path, """;@assert no_clip
;@assert peak(out0) <= .1875
;@assert net_zero
set_awg_gain 16384,0
set_awg_offs 8192,0
play 0,0,4
set_awg_offs 4096,0
upd_param 4
stop""", [[1, 0, -1, 0]], settings={"static_gain": [-.5, 1], "static_offset": [-.125, 0]})
    assert checks[0]["status"] == "proved"
    assert checks[1]["counterexample"]["value"] == .375
    assert checks[2]["counterexample"]["value"] == .5


def test_closed_repeat_area_and_late_lap_peak_witness(tmp_path):
    checks = verify(tmp_path, """;@assert net_zero
;@assert peak(out0) <= .03125
move 5,R0
move 1000,R1
nop
again: set_awg_gain R1,R1
play 0,0,8
add R1,20,R1
loop R0,@again
stop""", [[1, -1] * 4])
    assert checks[0]["status"] == "proved"
    w = checks[1]["counterexample"]
    assert w["time_ns"] == 16
    assert abs(w["value"]) == (1000 + 20 * (w["time_ns"] // 8)) / 32768


def test_local_def_does_not_bind_free_input_on_peer(tmp_path):
    checks = verify(tmp_path, """.DEF G 0
;@assume 0 <= G <= 1
;@assert no_clip
set_awg_gain $G,0
play 0,0,4
stop""", [[1] * 4], peer="set_awg_gain G,0\nplay 0,0,4\nstop")
    assert checks[0]["status"] == "unknown"


def test_fixed_fir_first_violation_and_concrete_replay(tmp_path):
    text = ";@assume T == 4\n;@assert no_clip\n;@assert peak(out1) <= 1\nplay 0,0,T\nstop"
    for check in verify(tmp_path, text, [[1] * 4], settings={"fir": [1, 1]})[:2]:
        w = check["counterexample"]
        assert (w["time_ns"], w["value"], w["inputs"], w["earliest_sample"]) == (1, 2, {"T": 4}, True)
        assert w["port"] == ("out0" if check["assertion"] == "no_clip" else "out1")
        assert w["timeline"]["execution"]["drive"]["mode"] == "concrete"
        assert any(e["id"] == w["event_id"] and e["kind"] == "play" for e in w["timeline"]["events"])


def test_fixed_fir_drain_past_stop_and_inclusive_limit(tmp_path):
    text = ";@assert no_clip\n;@assert peak(out0) <= 0.5\nplay 0,0,4\nstop"
    assert all(c["status"] == "proved" for c in verify(tmp_path, text, [[0.25] * 4], settings={"fir": [1, 1]})[:2])
    # The first overlapping delayed samples occur after the last stored sample.
    check = verify(tmp_path, text, [[0, 0, 0, 0, 1, 1]], settings={"fir": [0, 1, 1]})[0]
    w = check["counterexample"]
    assert (w["time_ns"], w["value"]) == (6, 2)


def test_fir_uses_aligned_port_sum_and_bias(tmp_path):
    drive = ";@assert no_clip\nwait 16\nplay 0,0,4\nwait_sync 4\nstop"
    peer = "wait 4\nplay 0,0,4\nwait_sync 4\nstop"
    w = verify(tmp_path, drive, [[.25] * 4], settings={"fir": [1, 1]}, peer=peer,
               bias=.125, alignment="first_wait_sync")[0]["counterexample"]
    assert (w["time_basis"], w["time_ns"], w["port"], w["value"]) == ("aligned", -3, "out0", 1.125)


def test_fir_uses_trigger_arrivals_of_the_routed_peer(tmp_path):
    drive = ";@assert no_clip\nwait 8\nstop"
    peer = "wait_trigger 1,0\nplay 0,0,4\nstop"
    triggers = [{"id": "gate", "address": 1, "targets": ["peer"], "times_ns": [40]}]
    w = verify(tmp_path, drive, [[1] * 4], settings={"fir": [1, 1]}, peer=peer,
               external_inputs={"triggers": triggers})[0]["counterexample"]
    assert (w["sequencer"], w["time_ns"], w["value"]) == ("peer", 45, 2)


@pytest.mark.parametrize(("alignment", "expected_ns"), [("none", 305), (None, 49)])
def test_fir_uses_simulated_startup_and_trigger_on_shared_clock(tmp_path, alignment, expected_ns):
    drive = ";@assert no_clip\nwait 8\nstop"
    peer = "wait_trigger 1,4\nplay 0,0,4\nstop"
    triggers = [{"target": "peer", "time_ns": 300, "address": 1}]
    check = verify(tmp_path, drive, [[1] * 4], settings={"fir": [1, 1]}, peer=peer, alignment=alignment,
                   simulation_inputs={"triggers": triggers})[0]
    w = check["counterexample"]
    assert (w["sequencer"], w["time_ns"], w["value"]) == ("peer", expected_ns, 2)
    assert w["timeline"]["execution"]["peer"]["mode"] == "simulation"
    assert w["timeline"]["alignment"]["peer"]["offset"] == expected_ns - 305
    event = next(e for e in w["timeline"]["events"] if e["id"] == w["event_id"])
    assert event["meta"]["aligned_t0"] == expected_ns - 1


def test_fir_covers_held_rotating_offset_after_stop(tmp_path):
    text = ";@assert peak(out0) <= 0.7\nset_awg_offs 0,24576\nupd_param 4\nstop"
    w = verify(tmp_path, text, settings={"fir": [0, 1], "nco_frequency_hz": 31250000})[0]["counterexample"]
    assert w["time_ns"] == 8 and w["value"] < -.7


def test_fir_sampling_respects_verification_deadline(tmp_path, monkeypatch):
    from q1timeline.output import properties

    monkeypatch.setattr(properties, "monotonic", lambda: float("inf"))
    check = verify(tmp_path, ";@assert no_clip\nplay 0,0,4\nstop", [[1] * 4], settings={"fir": [1, 1]})[0]
    assert check["status"] == "unknown" and "timed out" in check["message"]


@pytest.mark.parametrize(("program", "settings", "reason"), [
    (";@assume 4 <= T <= 8\n;@assert no_clip\nplay 0,0,T", {"fir": [1, 1]}, "fixed inputs"),
    (";@assert net_zero\nplay 0,0,4", {"fir": [1, 1]}, "peak or no_clip"),
    (";@assert no_clip\nplay 0,0,4", {"fir": [1, 1], "iir": {"a": [1, -.5]}}, "IIR"),
])
def test_unsupported_filtered_properties_remain_unknown(tmp_path, program, settings, reason):
    check = verify(tmp_path, program + "\nstop", [[1] * 4], settings=settings)[0]
    assert check["status"] == "unknown" and reason in check["message"]


def test_waveform_after_final_packet_participates_in_peak_and_area(tmp_path):
    checks = verify(tmp_path, """;@assert peak(out0) <= .25
;@assert net_zero
play 0,0,4
stop""", [[0, 0, 0, 0, -.5, .5]])
    witness = checks[0]["counterexample"]
    assert witness["value"] == -.5 and witness["time_ns"] == 4
    assert witness["line"] == 3
    assert checks[1]["status"] == "proved"


def test_module_dc_cancels_held_sequencer_offset(tmp_path):
    checks = verify(tmp_path, ";@assert net_zero\nset_awg_offs 8192,0\nupd_param 8\nstop", bias=-.25)
    assert checks[0]["status"] == "proved"


def test_pre_anchor_overlap_and_concrete_replay(tmp_path):
    drive = ";@assert no_clip\nwait 16\nset_awg_gain 24576,0\nplay 0,0,4\nwait_sync 4\nstop"
    peer = "wait 4\nset_awg_gain 24576,0\nplay 0,0,4\nwait_sync 4\nstop"
    assert verify(tmp_path, drive, [[1]*4], peer=peer)[0]["status"] == "proved"
    check = verify(tmp_path, drive, [[1]*4], peer=peer, alignment="first_wait_sync")[0]
    w = check["counterexample"]
    assert w["time_basis"] == "aligned" and w["time_ns"] == -4 and w["value"] == 1.5
    ir = w["timeline"]
    assert {seq: data["offset"] for seq, data in ir["alignment"].items()} == {"drive": -20, "peer": -8}
    event = next(e for e in ir["events"] if e["id"] == w["event_id"])
    assert event["kind"] == "play" and event["meta"]["aligned_t0"] <= w["time_ns"] < event["meta"]["aligned_t1"]
    preview = next(p for p in ir["output"]["ports"] if p["port"] == "out0")["preview"]
    assert preview["values"][w["time_ns"] - preview["t0"]] == 1


def test_post_anchor_overlap_stays_symbolic(tmp_path):
    drive = (";@assume 4 <= T <= 12\n;@assert no_clip\nwait 20\nwait_sync 4\nwait T\n"
             "set_awg_gain 24576,0\nplay 0,0,4\nstop")
    peer = "wait 4\nwait_sync 4\nwait 4\nset_awg_gain 24576,0\nplay 0,0,4\nstop"
    check = verify(tmp_path, drive, [[1]*4], peer=peer, alignment="first_wait_sync")[0]
    w = check["counterexample"]
    assert w["time_basis"] == "aligned" and 4 + w["inputs"]["T"] <= w["time_ns"] < 12
    assert w["value"] == 1.5


def test_port_dc_before_a_later_start_counts_toward_area(tmp_path):
    drive = ";@assert net_zero\nwait 12\nwait_sync 4\nstop"
    peer = "set_awg_offs -8192,0\nupd_param 4\nwait_sync 4\nstop"
    w = verify(tmp_path, drive, peer=peer, bias=.25, alignment="first_wait_sync")[0]["counterexample"]
    assert w["port"] == "out0" and w["value"] == 2 and w["time_basis"] == "aligned"


def test_unresolved_peer_anchor_cannot_establish_overlap(tmp_path):
    drive = ";@assume 4 <= T <= 8\n;@assert no_clip\nwait T\nwait_sync 4\nstop"
    check = verify(tmp_path, drive, peer="wait_sync 4\nstop", alignment="first_wait_sync")[0]
    assert check["status"] == "unknown" and "alignment" in check["message"]


def test_no_clip_chooses_earliest_port_even_when_listed_last(tmp_path):
    check = verify(tmp_path, ";@assert no_clip\nset_awg_offs 8192,8192\nplay 0,1,4\nstop",
                   [[0, 0, 0, 1], [0, 1, 1, 1]])[0]
    w = check["counterexample"]
    assert (w["port"], w["time_ns"], w["value"]) == ("out1", 1, 1.25)


def test_peak_minimizes_time_before_parameter_values(tmp_path):
    check = verify(tmp_path, """;@assume 16384 <= G <= 24576
;@assert peak(out0) <= .75
set_awg_gain G,0
set_awg_offs 8192,0
play 0,0,4
stop""", [[.75, 1, 1, 1]], max_cases=1)[0]
    w = check["counterexample"]
    # Smaller gains can first fail at t=1. At t=0: .75*G/32768 + .25 > .75.
    assert w["time_ns"] == 0 and w["inputs"] == {"G": 21846}


def test_ties_choose_port_then_lexicographic_parameter_binding(tmp_path):
    checks = verify(tmp_path, """;@assume 16384 <= G <= 16390
;@assume 16374 <= A <= 16390
;@assert no_clip
set_awg_gain G,G
set_awg_offs A,A
play 0,0,4
stop""", [[1] * 4], max_cases=1)
    w = checks[0]["counterexample"]
    assert (w["time_ns"], w["port"], w["inputs"]) == (0, "out0", {"A": 16379, "G": 16390})


def test_shipped_mixed_port_demo_reports_reproducible_first_sample():
    directory = Path(__file__).resolve().parents[1] / "examples" / "symbolic-output"
    kwargs = {"project": directory / "q1timeline.yml", "include_replay": True}
    for _ in range(2):
        checks = verify_file(directory / "drive.q1asm", **kwargs)["checks"]
        w = next(c for c in checks if c.get("assertion") == "no_clip")["counterexample"]
        assert (w["time_ns"], w["port"], w["inputs"]) == (4, "out0", {"G": 28664})
        assert w["value"] == (28664 + 8192) / 32768


def test_full_scale_is_valid_for_port_and_positive_dac_rail_is_lower(tmp_path):
    from q1lens import output

    source = ";@assert no_clip\nplay 0,0,4\nstop"
    wave = [-1, 1, -1, 1]
    assert verify(tmp_path, source, [wave])[0]["status"] == "proved"
    model = output({"program": source, "waveforms": {"w": {"index": 0, "data": wave}}})
    assert model.samples(0, 2, stage="dac", dac_bits=16)[0] == [-1, 32767 / 32768]


def test_timeout_after_finding_a_sample_does_not_claim_it_is_first(tmp_path, monkeypatch):
    from q1timeline.output.properties import _Proof

    minimum = _Proof.minimum

    def expire_before_prefix_check(self, *args):
        self.deadline = 0
        return minimum(self, *args)

    monkeypatch.setattr(_Proof, "minimum", expire_before_prefix_check)
    check = verify(tmp_path, ";@assert peak(out0) <= .5\nplay 0,0,4\nstop", [[0, 0, 0, 1]])[0]
    assert check["status"] == "unknown" and "timed out" in check["message"]
    assert "counterexample" not in check
