from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "examples/cursor-frequency-demo/verification/verify_boundaries.py"
spec = importlib.util.spec_from_file_location("cursor_frequency_boundaries", SCRIPT)
boundaries = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boundaries)
SHORT_PROFILES = ("bin-wrap-preview", "deadband-boundaries", "delay-min", "delay-max")


@pytest.fixture(scope="module", params=SHORT_PROFILES)
def verified(request, tmp_path_factory):
    output = tmp_path_factory.mktemp("cursor-boundary")
    before = boundaries.base.original_hashes()
    evidence = boundaries.run_profile(request.param, output)
    folder = Path(evidence["ir_path"]).parent
    ir = json.loads((folder / "timeline_ir.json").read_text(encoding="utf-8"))
    expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
    p = json.loads((folder / "params.json").read_text(encoding="utf-8"))
    assert boundaries.base.original_hashes() == before
    return evidence, ir, expected, p


@pytest.mark.parametrize("seed", [0, 1, 0x80000000, 0xFFFFFFFF, 24681357, 97531864])
def test_prng_oracle_matches_independent_32_bit_vector_transform(seed) -> None:
    bits = [(seed >> index) & 1 for index in range(32)]
    expected_stages = []
    for displacement in (13, -17, 5):
        previous = bits
        bits = [bit ^ (previous[index-displacement] if 0 <= index-displacement < 32 else 0)
                for index, bit in enumerate(previous)]
        expected_stages.append(sum(bit * 2**index for index, bit in enumerate(bits)))
    assert boundaries.xorshift_stages(seed) == expected_stages


def test_original_prng_parameters_have_explicit_first_update_expectations() -> None:
    expected = boundaries.prng_expectation(boundaries.base.parameters())
    assert expected["position_seed_stages"] == [302594957, 302592649, 1395614633]
    assert expected["gain_seed_stages"] == [53163992, 53163597, 1716387821]
    assert (expected["position_step"], expected["position_backwards"], expected["offset"]) == (937, True, 937)
    assert (expected["gain_step"], expected["gain_down"], expected["gain"]) == (1005, True, 5995)
    assert (expected["update_after_shot"], expected["first_changed_output_shot"]) == (1000, 1001)


def test_long_profiles_do_not_accelerate_original_prng_and_declare_bin_override(tmp_path) -> None:
    project, expected, p = boundaries.prepare("prng-1001", tmp_path)
    assert expected["profile"]["shots"] == 1001
    assert expected["profile"]["parameter_overrides"] == {}
    assert expected["profile"]["sequencers"] == ["blue_peak"]
    assert p == boundaries.base.parameters()
    project, expected, p = boundaries.prepare("bin-wrap-1024", tmp_path)
    assert expected["profile"]["shots"] == 513
    assert expected["profile"]["parameter_overrides"] == {"EDGE_ACQ_NUM_BINS": 1024}
    assert expected["profile"]["sequencers"] == ["red_tracker"]
    assert boundaries.base.parameters()["EDGE_ACQ_NUM_BINS"] == 65536
    assert p["EDGE_ACQ_NUM_BINS"] == 1024


def test_fresh_cli_checks_boundaries_with_unmodified_sources_and_no_physical_claim(verified) -> None:
    evidence, ir, expected, p = verified
    assert evidence["passed"] is True
    assert len(evidence["ir_sha256"]) == 64
    assert evidence["original_parameter_bin_count"] == 65536
    assert evidence["selected_sequencers"] == list(boundaries.base.SEQUENCERS)
    assert all(value["status"] == "blocked" for value in ir["execution"].values())
    assert all(value["physical_enabled"] is False for value in ir["simulation"].values())
    shots = expected["controller"]["shots"]
    if evidence["profile"] == "deadband-boundaries":
        assert [s["slope"] for s in shots] == [-65, -64, -63, 0, 63, 64, 65]
        assert [s["hold"] for s in shots] == [False, False, True, True, True, False, False]
    elif evidence["profile"] == "delay-min":
        assert [s["delay_after"] for s in shots] == [p["TRACKER_MIN_DELAY"], p["TRACKER_MIN_DELAY"], p["TRACKER_MIN_DELAY"]+p["TRACK_STEP"]]
    elif evidence["profile"] == "delay-max":
        assert [s["delay_after"] for s in shots] == [p["TRACKER_MAX_DELAY"], p["TRACKER_MAX_DELAY"], p["TRACKER_MAX_DELAY"]-p["TRACK_STEP"]]
    else:
        results = evidence["checkpoints"]["acquisitions"]
        assert [e["meta"]["bin_index"] for e in results] == [0, 1, 2, 3, 0, 1]
        assert [e["meta"]["input_index"] for e in results] == list(range(6))
        assert results[4]["time_ns"] == 84072
        assert results[0]["meta"]["i"] == 24576000 and results[4]["meta"]["i"] == 28672000


def test_verifier_rejects_wrong_center_or_premature_limit_success(verified) -> None:
    _evidence, original, expected, p = verified
    ir = deepcopy(original)
    write = next(w for w in ir["register_history"] if w["sequencer_id"] == "red_tracker" and w["register"] == "R15")
    write["value"]["value"] += p["TRACK_STEP"]
    with pytest.raises(boundaries.base.VerificationFailure, match="Tracked center"):
        boundaries.check(ir, expected, p)
    ir = deepcopy(original)
    ir["execution"]["red_tracker"]["status"] = "step_limit"
    with pytest.raises(boundaries.base.VerificationFailure, match="terminal status"):
        boundaries.check(ir, expected, p)


def test_verifier_rejects_reused_input_or_wrong_strict_deadband(verified) -> None:
    _evidence, original, expected, p = verified
    ir = deepcopy(original)
    results = [e for e in ir["events"] if e["kind"] == "acquisition_result"]
    results[-1]["meta"]["input_index"] = 0
    with pytest.raises(boundaries.base.VerificationFailure, match="reused or skipped"):
        boundaries.check(ir, expected, p)
    ir = deepcopy(original)
    branch = next(e for e in ir["events"] if e["kind"] == "q1_issue" and any(
        text in (e.get("source") or {}).get("raw", "") for text in ("jlt $SLOPE,", "jlt $ABS_SLOPE,")))
    branch["meta"]["branch_taken"] = not branch["meta"]["branch_taken"]
    with pytest.raises(boundaries.base.VerificationFailure, match="Deadband strict boundary"):
        boundaries.check(ir, expected, p)


def test_verifier_rejects_missing_or_incorrect_applied_setter_ancestry(verified) -> None:
    _evidence, original, expected, p = verified
    for mutation in ("missing-setter", "missing-q1", "wrong-sequencer", "wrong-op", "wrong-value", "wrong-operand"):
        ir = deepcopy(original)
        by_id = {event["id"]: event for event in ir["events"]}
        play = next(e for e in ir["events"] if e["kind"] == "play" and e["sequencer_id"] == "orange_drive")
        pending = by_id[play["meta"]["applied_setter_event_ids"]["frequency"]]
        issue = by_id[pending["meta"]["q1_issue_event_id"]]
        if mutation == "missing-setter":
            for event in ir["events"]:
                if event["kind"] == "play":
                    event["meta"]["applied_setter_event_ids"] = {field: "missing-writer-id" for field in event["meta"]["applied_setter_event_ids"]}
        elif mutation == "missing-q1":
            pending["meta"]["q1_issue_event_id"] = "missing-q1-id"
        elif mutation == "wrong-sequencer":
            pending["sequencer_id"] = "blue_peak"
        elif mutation == "wrong-op":
            issue["meta"]["op"] = "set_awg_gain"
        elif mutation == "wrong-value":
            pending["meta"]["value"] += 512
        else:
            issue["meta"]["register_reads"]["R2"]["value"] += 512
        with pytest.raises(boundaries.base.VerificationFailure, match="Applied setter"):
            boundaries.check(ir, expected, p)


@pytest.mark.parametrize("failure", ["cli", "timeout", "interrupt"])
def test_failed_new_run_cannot_leave_previous_success_evidence(tmp_path, monkeypatch, failure) -> None:
    name = "bin-wrap-preview"
    folder = tmp_path / name
    folder.mkdir()
    (folder / "evidence.json").write_text(json.dumps({"profile": name, "passed": True}))
    (folder / "timeline_ir.json").write_text("old passing output")
    def fail_cli(command, **_kwargs):
        current = json.loads((folder / "evidence.json").read_text())
        assert current["passed"] is False and current["status"] == "in_progress"
        assert not (folder / "timeline_ir.json").exists()
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 300)
        if failure == "interrupt":
            raise KeyboardInterrupt("test interruption")
        return subprocess.CompletedProcess(command, 7, "", "test CLI failure")
    monkeypatch.setattr(boundaries.subprocess, "run", fail_cli)
    exception = {"cli": boundaries.base.VerificationFailure, "timeout": subprocess.TimeoutExpired, "interrupt": KeyboardInterrupt}[failure]
    with pytest.raises(exception):
        boundaries.run_profile(name, tmp_path)
    evidence = json.loads((folder / "evidence.json").read_text())
    assert evidence["passed"] is False
    assert evidence["status"] == ("interrupted" if failure == "interrupt" else "failed")
    assert evidence["reason"]


def test_preparation_failure_also_invalidates_previous_success(tmp_path, monkeypatch) -> None:
    folder = tmp_path / "bin-wrap-preview"
    folder.mkdir()
    (folder / "evidence.json").write_text(json.dumps({"passed": True}))
    def fail_prepare(*_args):
        assert json.loads((folder / "evidence.json").read_text())["passed"] is False
        raise ValueError("test preparation failure")
    monkeypatch.setattr(boundaries, "prepare", fail_prepare)
    with pytest.raises(ValueError, match="preparation failure"):
        boundaries.run_profile("bin-wrap-preview", tmp_path)
    assert json.loads((folder / "evidence.json").read_text())["status"] == "failed"


@pytest.mark.parametrize("failure", ["timeout", "interrupt"])
def test_main_updates_aggregate_before_each_case_and_preserves_incomplete_state(tmp_path, monkeypatch, failure) -> None:
    names = ["bin-wrap-preview", "delay-min", "delay-max"]
    (tmp_path / "report.json").write_text(json.dumps([{"profile": name, "passed": True} for name in names]))
    def fake_run(name, output):
        current = json.loads((output / "report.json").read_text())
        index = names.index(name)
        assert current[index]["passed"] is False and current[index]["status"] == "in_progress"
        assert all(row["passed"] is False for row in current[index:])
        if name == "delay-min":
            if failure == "timeout":
                raise subprocess.TimeoutExpired("test", 300)
            raise KeyboardInterrupt("test interruption")
        return {"profile": name, "shots": 3, "event_count": 10, "elapsed_seconds": 0.1,
                "ir_path": "test-only", "last_validated_utc": "test-only"}
    monkeypatch.setattr(boundaries, "run_profile", fake_run)
    monkeypatch.setattr(boundaries.sys, "argv", [str(SCRIPT), "--out-dir", str(tmp_path), *[arg for name in names for arg in ("--profile", name)]])
    if failure == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            boundaries.main()
    else:
        assert boundaries.main() == 1
    report = json.loads((tmp_path / "report.json").read_text())
    assert report[0]["passed"] is True
    assert report[1]["passed"] is False
    assert report[1]["status"] == ("interrupted" if failure == "interrupt" else "failed")
    assert report[2]["passed"] is (failure == "timeout")
    assert report[2]["status"] == ("queued" if failure == "interrupt" else "passed")


def test_prepare_only_failure_returns_nonzero_and_records_failure(tmp_path, monkeypatch) -> None:
    def fail_prepare(*_args):
        raise OSError("test preparation write failure")
    monkeypatch.setattr(boundaries, "prepare", fail_prepare)
    monkeypatch.setattr(boundaries.sys, "argv", [str(SCRIPT), "--profile", "bin-wrap-preview", "--out-dir", str(tmp_path), "--prepare-only"])
    assert boundaries.main() == 1
    report = json.loads((tmp_path / "report.json").read_text())
    assert report[0]["passed"] is False and report[0]["status"] == "failed"
    assert "test preparation write failure" in report[0]["reason"]
    assert json.loads((tmp_path / "bin-wrap-preview/evidence.json").read_text())["passed"] is False
