"""On shared inputs, compare observable RT edges and replay the exact witness."""
import json

import pytest

from q1timeline.verification_compare import verify_alignment, verify_equivalence


def _sources(tmp_path, old, new):
    before, after = (tmp_path / name for name in ("before.q1asm", "after.q1asm"))
    before.write_text(old, encoding="utf-8")
    after.write_text(new, encoding="utf-8")
    return before, after


def _project(tmp_path, programs, **settings):
    for name, text in programs.items():
        (tmp_path / f"{name}.q1asm").write_text(text, encoding="utf-8")
    project = tmp_path / "q1timeline.yml"
    project.write_text(json.dumps({
        "sequencers": [{"id": name, "file": f"{name}.q1asm"} for name in programs],
        "simulation": {"startup_delay_ns": 200}, **settings,
    }), encoding="utf-8")
    return project


RANGED = """.DEF N 1
;@assume 1 <= N <= 8
move $N,R0
nop
jlt R0,7,@done
wait 4
done: play 0,1,4
stop
"""


def test_equivalence_includes_first_failing_input_and_two_replay_lanes(tmp_path):
    paths = _sources(tmp_path, RANGED, RANGED.replace("wait 4", "wait 8"))
    check, = verify_equivalence(*paths, include_replay=True)["checks"]
    assert check["status"] == "counterexample" and check["checked_cases"] == 7
    witness = check["counterexample"]
    assert witness["inputs"] == {"N": 7} and witness["line"] == 6
    assert sorted(s["id"] for s in witness["timeline"]["sequencers"]) == ["after", "before"]
    assert witness["observations"]["before"]["end_ns"] == 4
    assert witness["observations"]["after"]["end_ns"] == 8
    assert any(e["id"] == witness["event_id"] for e in witness["timeline"]["events"])


def test_equivalence_accepts_q1_optimizations_and_binds_operand_values(tmp_path):
    old = "move 1,R0\nnop\nnop\nplay R0,R0,8\nstop"
    after = "play 1,1,8\nstop"
    assert verify_equivalence(*_sources(tmp_path, old, after))["status"] == "proved"
    paths = _sources(tmp_path, old, after.replace("1,1", "1,2"))
    check, = verify_equivalence(*paths)["checks"]
    assert check["status"] == "counterexample" and check["counterexample"]["line"] == 1
    assert "timeline" not in check["counterexample"]


def test_equivalence_checks_applied_settings(tmp_path):
    paths = _sources(tmp_path, "set_mrk 1\nupd_param 8\nstop", "set_mrk 2\nupd_param 8\nstop")
    check, = verify_equivalence(*paths)["checks"]
    assert check["status"] == "counterexample" and "applied_state" in check["message"]


@pytest.mark.parametrize("options", [{"max_cases": 6}, {"max_steps": 1}, {"timeout_ms": 0}])
def test_incomplete_equivalence_is_unknown(tmp_path, options):
    paths = _sources(tmp_path, RANGED, RANGED)
    assert verify_equivalence(*paths, **options)["status"] == "unknown"


def test_very_large_range_respects_budget_without_materializing_input_space(tmp_path):
    program = ".DEF N 1\n;@assume 0 <= N <= 4294967295\nmove $N,R0\nnop\nstop"
    paths = _sources(tmp_path, program, program)
    check, = verify_equivalence(*paths, max_cases=2)["checks"]
    assert check["status"] == "unknown" and check["checked_cases"] == 2


def test_named_checkpoints_detect_transient_misalignment_with_identical_stop_times(tmp_path):
    project = _project(tmp_path, {"a": "wait 20\nmeasure: wait 20\nstop",
                                  "b": "wait 24\nmeasure: wait 16\nstop"})
    check, = verify_alignment(project)["checks"]
    assert check["status"] == "counterexample" and "@measure 4 ns" in check["message"]


def test_alignment_broadcasts_each_measurement_to_all_receivers(tmp_path):
    body = "set_latch_en 1,4\nwait 20\n"
    for i in range(4):
        body += f"set_cond 1,{1 << i},0,4\nwait 24\n"
    body += "set_cond 0,0,0,4\nat_end: upd_param 4\nstop"
    programs = {"a": body, "b": body}
    settings = {"external_inputs": {"triggers": [
        {"id": f"bit{i}", "address": i + 1, "targets": ["a", "b"], "times_ns": [12 + i]} for i in range(4)]}}
    project = _project(tmp_path, programs, **settings)
    check, = verify_alignment(project)["checks"]
    assert check["status"] == "proved" and check["checked_cases"] == 16
    programs["b"] = body.replace("wait 24", "wait 44", 1)
    project = _project(tmp_path, programs, **settings)
    check, = verify_alignment(project, include_replay=True)["checks"]
    witness = check["counterexample"]
    assert witness["inputs"] == {"m0": 1, "m1": 0, "m2": 0, "m3": 0}
    assert witness["observations"]["b"]["time_ns"] - witness["observations"]["a"]["time_ns"] == 20
    assert witness["file"] == str(tmp_path / "b.q1asm")
    assert any(e["id"] == witness["event_id"] for e in witness["timeline"]["events"])


def test_alignment_branches_on_each_acquisition_occurrence(tmp_path):
    project = _project(tmp_path, {"producer": "acquire 0,0,8\nacquire 0,0,8\nstop",
                                  "clock": "wait 16\nstop"})
    check, = verify_alignment(project)["checks"]
    assert check["status"] == "proved" and check["checked_cases"] == 4
    assert check["measurements"]["m1"] == {"sequencer": "producer", "acquisition_index": 0,
                                           "bin_index": 0, "occurrence": 2}
    assert verify_alignment(project, max_cases=3)["status"] == "unknown"


def test_threshold_feedback_route_changes_only_failing_alignment_input(tmp_path):
    # The receiver starts its condition after the producer has measured and routed the bit.
    project = _project(tmp_path, {"producer": "acquire 0,0,8\nwait 24\nstop",
                                  "receiver": "set_latch_en 1,4\nwait 16\nset_cond 1,1,0,12\nwait 20\nstop"},
                       simulation={"startup_delay_ns": 200, "trigger_routes": [
                           {"source": "producer", "target": "receiver", "address": 1, "latency_ns": 0}]})
    check, = verify_alignment(project)["checks"]
    assert check["status"] == "counterexample" and check["counterexample"]["inputs"] == {"m0": 1}
    assert "receiver reaches stop 8 ns later" in check["message"]


def test_stalled_simulation_and_missing_explicit_anchor_stay_unknown(tmp_path):
    project = _project(tmp_path, {"a": "fb_pop_data 1,R0\nnop\nstop", "b": "wait 4\nstop"})
    assert verify_alignment(project)["status"] == "unknown"
    project = _project(tmp_path, {"a": "foo: wait 4\nstop", "b": "wait 4\nstop"},
                       alignment={"mode": "label:foo"})
    assert verify_alignment(project)["status"] == "unknown"


@pytest.mark.parametrize("alignment", [{"mode": "first_play"}, {"mode": "first_anchor", "anchor_kinds": ["play"]}])
def test_configured_checkpoint_detects_drift_before_equal_stop_times(tmp_path, alignment):
    project = _project(tmp_path, {"a": "wait 4\nplay 0,1,4\nwait 8\nstop",
                                  "b": "wait 8\nplay 0,1,4\nwait 4\nstop"}, alignment=alignment)
    check, = verify_alignment(project)["checks"]
    assert check["status"] == "counterexample" and alignment["mode"] in check["message"]


def test_unknown_external_condition_cannot_prove_equivalence(tmp_path):
    source = "set_latch_en 1,4\nset_cond 1,1,0,4\nupd_param 24\nstop"
    paths = _sources(tmp_path, source, source.replace("upd_param 24", "upd_param 4"))
    check, = verify_equivalence(*paths)["checks"]
    assert check["status"] == "unknown" and "trigger" in check["message"]


@pytest.mark.parametrize("setter", ["set_time_ref", "reset_netzero"])
def test_rt_one_shot_actions_are_observable(tmp_path, setter):
    paths = _sources(tmp_path, "upd_param 4\nstop", f"{setter}\nupd_param 4\nstop")
    assert verify_equivalence(*paths)["status"] == "counterexample"


@pytest.mark.parametrize("delay,basis", [(None, "aligned"), (200, "local")])
def test_equivalence_replay_uses_comparison_clock(tmp_path, delay, basis):
    paths = _sources(tmp_path, "nop\nwait 4\nplay 0,1,4\nstop", "wait 4\nplay 1,0,4\nstop")
    check, = verify_equivalence(*paths, startup_delay_ns=delay, include_replay=True)["checks"]
    witness = check["counterexample"]
    assert witness["time_basis"] == basis
    assert witness["time_ns"] == witness["observations"]["after"]["time_ns"] == (delay or 0) + 4
    event = next(e for e in witness["timeline"]["events"] if e["id"] == witness["event_id"])
    assert event["meta"]["aligned_t0"] == witness["time_ns"]
    if delay is None:
        assert witness["timeline"]["alignment"]["before"]["offset"] != witness["timeline"]["alignment"]["after"]["offset"]
    else:
        assert event["t0"]["value"] == witness["time_ns"]


def test_fixed_launch_can_invalidate_an_otherwise_matching_trace(tmp_path):
    files = _sources(tmp_path, "wait 8\nstop", "nop\nnop\nwait 8\nstop")
    assert verify_equivalence(*files)["status"] == "proved"
    check, = verify_equivalence(*files, startup_delay_ns=4)["checks"]
    assert check["status"] == "unknown" and "underflow" in check["message"]


def test_unvisited_designated_checkpoint_is_unknown(tmp_path):
    program = "jmp @done\nmark: wait 20\ndone: wait 4\nstop"
    project = _project(tmp_path, {"a": program, "b": program}, alignment={"mode": "label:mark"})
    check, = verify_alignment(project)["checks"]
    assert check["status"] == "unknown" and "@mark" in check["message"]


def test_repeated_persistent_setting_leaves_rt_outputs_equivalent(tmp_path):
    prefix = "set_freq 1000\nset_mrk 3\nupd_param 4\n"
    old = prefix + "set_mrk 3\nset_freq 1000\nupd_param 8\nstop"
    new = prefix + "upd_param 8\nstop"
    assert verify_equivalence(*_sources(tmp_path, old, new))["status"] == "proved"


@pytest.mark.parametrize("explicit,packed", [
    ("fb_com_data 42,7,4", "fb_cmd 0x12a,7,4"),
    ("upd_thres 1,3,4", "fb_cmd 0x11,3,4"),
])
def test_equivalence_compares_decoded_packet_aliases(tmp_path, explicit, packed):
    files = _sources(tmp_path, explicit + "\nstop", packed + "\nstop")
    assert verify_equivalence(*files)["status"] == "proved"


def test_binary_measurement_cannot_substitute_for_unavailable_iq_feedback(tmp_path):
    source = "fb_acq_iq_id 1,4\nacquire 0,0,8\nstop"
    check, = verify_equivalence(*_sources(tmp_path, source, source))["checks"]
    assert check["status"] == "unknown" and "I/Q" in check["message"]


def test_equivalence_shares_parameter_range_declared_on_one_side(tmp_path):
    source = "wait D\nstop"
    files = _sources(tmp_path, ";@assume 4 <= D <= 8\n" + source, source)
    check, = verify_equivalence(*files)["checks"]
    assert check["status"] == "proved" and check["checked_cases"] == 5


def test_alignment_binds_project_parameters_once_across_sequencers(tmp_path):
    (tmp_path / "params.json").write_text('{"D": 8}')
    wait = "wait D\nstop"
    project = _project(tmp_path, {"a": ";@assume 4 <= D <= 8\n" + wait, "b": wait,
                                  "c": ";@assume 6 <= D <= 9\n" + wait},
                       params={"file": "params.json"})
    check, = verify_alignment(project)["checks"]
    assert check["status"] == "proved" and check["checked_cases"] == 3
    assert check["domain"] == {"D": [6, 8]}

    # A .DEF is private to the source that declares it, even when the name matches.
    (tmp_path / "b.q1asm").write_text(".DEF D 8\nwait $D\nstop")
    check, = verify_alignment(project)["checks"]
    assert check["counterexample"]["inputs"] == {"D": 6}


def test_alignment_uses_case_parameter_for_trigger_address(tmp_path):
    (tmp_path / "params.json").write_text('{"ADDRESS": 1}')
    project = _project(tmp_path, {
        "a": ";@assume ADDRESS == 2\nmove ADDRESS,R0\nnop\nset_latch_en 1,4\nwait 20\n"
             "set_cond 1,2,0,4\nwait 24\nstop",
        "b": "wait 28\nstop",
    }, params={"file": "params.json"}, external_inputs={"triggers": [
        {"id": "bit", "address": "ADDRESS", "targets": ["a"], "times_ns": [12]}]})
    check, = verify_alignment(project)["checks"]
    assert check["status"] == "counterexample"
    assert check["counterexample"]["inputs"] == {"ADDRESS": 2, "m0": 1}


@pytest.mark.parametrize(("old_range", "new_range"), [
    ((4, 6), (5, 8)), ((4, 8), (6, 9)), ((7, 8), (4, 7)), ((4, 5), (6, 8)),
])
def test_equivalence_checks_exactly_the_joint_input_domain(tmp_path, old_range, new_range):
    def ranged(bounds, source):
        return f";@assume {bounds[0]} <= D <= {bounds[1]}\n" + source

    before = ranged(old_range, "wait D\nstop")
    after = ranged(new_range, "move D,R0\nnop\njlt R0,7,@same\nwait 4\nsame: wait D\nstop")
    check, = verify_equivalence(*_sources(tmp_path, before, after), include_replay=True)["checks"]
    shared = [d for d in range(4, 10) if old_range[0] <= d <= old_range[1] and new_range[0] <= d <= new_range[1]]
    if not shared:
        assert check["status"] == "unknown"
        return
    assert check["domain"] == {"D": [shared[0], shared[-1]]}
    # Only the fallthrough issues the extra 4 ns wait.
    first_difference = next((d for d in shared if d >= 7), None)
    if first_difference is None:
        assert check["status"] == "proved" and check["checked_cases"] == len(shared)
    else:
        assert check["status"] == "counterexample"
        witness = check["counterexample"]
        assert witness["inputs"] == {"D": first_difference}
        assert witness["observations"]["before"]["end_ns"] == first_difference
        assert witness["observations"]["after"]["end_ns"] == 4
        event = next(e for e in witness["timeline"]["events"] if e["id"] == witness["event_id"])
        assert event["kind"] == "wait" and event["meta"]["aligned_t0"] == witness["time_ns"]


@pytest.mark.parametrize("padding", [0, 1, 2])
@pytest.mark.parametrize("startup", [None, 200])
def test_q1_only_stop_reports_the_completion_edge(tmp_path, padding, startup):
    before = "nop\n" * padding + "stop 1"
    after = "nop\n" * padding + "stop 2"
    check, = verify_equivalence(*_sources(tmp_path, before, after), startup_delay_ns=startup,
                                include_replay=True)["checks"]
    assert check["status"] == "counterexample"
    witness = check["counterexample"]
    assert witness["time_ns"] == (4 * (padding + 1) if startup is not None else 0)
    for observation in witness["observations"].values():
        event = next(e for e in witness["timeline"]["events"] if e["id"] == observation["event_id"])
        assert event["kind"] == "q1_issue" and event["meta"]["op"] == "stop"
        assert event["t1"]["value"] == 4 * (padding + 1)
        assert event["meta"]["aligned_t1"] == observation["time_ns"] == witness["time_ns"]


@pytest.mark.parametrize("arrival", [8, 9, 10, 11])
@pytest.mark.parametrize("peer_dwell", [8, 12])
def test_alignment_matches_trigger_and_shared_wait_checkpoint_table(tmp_path, arrival, peer_dwell):
    def source(dwell):
        return (";@assume 4 <= D <= 6\nset_latch_en 1,4\nwait D\nset_cond 1,1,0,4\n"
                f"wait {dwell}\nset_cond 0,0,0,4\nmark: wait 4\nstop")

    project = _project(tmp_path, {"a": source(8), "b": source(peer_dwell)}, external_inputs={"triggers": [
        {"id": "edge", "address": 1, "targets": ["a", "b"], "times_ns": [arrival]}]})
    check, = verify_alignment(project, include_replay=True)["checks"]
    # A trigger at the conditional packet's dequeue edge counts on that packet.
    for wait in range(4, 7):
        for bit in (0, 1):
            start = 4 + wait
            taken = bit and arrival <= start
            times = {"a": 200 + start + (8 if taken else 4),
                     "b": 200 + start + (peer_dwell if taken else 4)}
            if times["a"] != times["b"]:
                assert check["status"] == "counterexample"
                witness = check["counterexample"]
                assert witness["inputs"] == {"D": wait, "m0": bit}
                assert {seq: ob["time_ns"] for seq, ob in witness["observations"].items()} == times
                event = next(e for e in witness["timeline"]["events"] if e["id"] == witness["event_id"])
                assert event["source"]["line"] == 7 and event["t0"]["value"] == times["b"] == witness["time_ns"]
                return
    assert check["status"] == "proved" and check["checked_cases"] == 6


@pytest.mark.parametrize("peer_wait", [4, 8])
def test_unresolved_trigger_address_cannot_hide_a_valid_alignment_witness(tmp_path, peer_wait):
    (tmp_path / "params.json").write_text('{"A":1}')
    project = _project(tmp_path, {"a": ";@assume 0 <= A <= 1\nmove A,R0\nnop\nwait 4\nstop",
                                  "b": f"wait {peer_wait}\nstop"}, params={"file": "params.json"},
                       external_inputs={"triggers": [{"id": "edge", "targets": ["a"], "address": "A",
                                                      "times_ns": [4]}]})
    check, = verify_alignment(project, include_replay=True)["checks"]
    if peer_wait == 4:
        assert check["status"] == "unknown" and "address" in check["message"]
    else:
        assert check["status"] == "counterexample"
        witness = check["counterexample"]
        assert witness["inputs"] == {"A": 1, "m0": 0}
        assert {seq: ob["time_ns"] for seq, ob in witness["observations"].items()} == {"a": 204, "b": 208}
        event = next(e for e in witness["timeline"]["events"] if e["id"] == witness["event_id"])
        assert event["kind"] == "stop" and event["t0"]["value"] == witness["time_ns"] == 208


@pytest.mark.parametrize("reverse", [False, True])
def test_external_equivalence_input_does_not_override_private_def(tmp_path, reverse):
    external = ";@assume 4 <= D <= 6\nwait D\nstop"
    local = ".DEF D 8\nwait $D\nstop"
    sides = (local, external) if reverse else (external, local)
    check, = verify_equivalence(*_sources(tmp_path, *sides), include_replay=True)["checks"]
    assert check["status"] == "counterexample"
    witness = check["counterexample"]
    assert witness["inputs"] == {"D": 4}
    for side, dwell in zip(("before", "after"), (8, 4) if reverse else (4, 8)):
        observation = witness["observations"][side]
        event = next(e for e in witness["timeline"]["events"] if e["id"] == observation["event_id"])
        assert event["kind"] == "wait" and event["meta"]["operand_snapshot"] == [dwell]
        assert event["meta"]["aligned_t1"] == observation["end_ns"] == dwell


def test_local_equivalence_range_does_not_bind_unconstrained_external_input(tmp_path):
    local = ".DEF D 8\n;@assume 4 <= D <= 6\nwait $D\nstop"
    assert verify_equivalence(*_sources(tmp_path, local, "wait D\nstop"))["status"] == "unknown"
    # A matching editable DEF in the other version intentionally shares the sweep.
    check, = verify_equivalence(*_sources(tmp_path, local, ".DEF D 9\nwait $D\nstop"))["checks"]
    assert check["status"] == "proved" and check["checked_cases"] == 3


def test_explicitly_ranged_def_and_free_parameter_are_independent_inputs(tmp_path):
    assumption = ";@assume 4 <= D <= 5\n"
    paths = _sources(tmp_path, ".DEF D 4\n" + assumption + "wait $D\nstop", assumption + "wait D\nstop")
    check, = verify_equivalence(*paths, include_replay=True)["checks"]
    assert check["status"] == "counterexample" and check["domain"] == {"D": [4, 5], "$D": [4, 5]}
    witness = check["counterexample"]
    assert witness["inputs"] == {"D": 4, "$D": 5}
    assert witness["observations"]["before"]["end_ns"] == 5
    assert witness["observations"]["after"]["end_ns"] == 4
