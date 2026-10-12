from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "examples/cursor-frequency-demo/verification/verify.py"
spec = importlib.util.spec_from_file_location("cursor_frequency_verification", SCRIPT)
verification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verification)


@pytest.fixture(scope="module", params=("two-shot-feedback", "deadband-hold"))
def verified(request, tmp_path_factory):
    output = tmp_path_factory.mktemp("cursor-frequency-verification")
    before = verification.original_hashes()
    evidence = verification.run_profile(request.param, output)
    ir = json.loads(Path(evidence["ir_path"]).read_text(encoding="utf-8"))
    assert verification.original_hashes() == before
    return request.param, evidence, ir


def test_independent_controller_oracle_has_movement_and_deadband_paths() -> None:
    p = verification.parameters()
    movement = verification.oracle(verification.PROFILES["two-shot-feedback"], p)["shots"]
    hold = verification.oracle(verification.PROFILES["deadband-hold"], p)["shots"]
    assert [shot["slope"] for shot in movement] == [2000, -4000]
    assert [shot["delay_after"] for shot in movement] == [p["TRACKER_INIT_DELAY"] + p["TRACK_STEP"], p["TRACKER_INIT_DELAY"]]
    assert [shot["gain"] for shot in movement] == [8000, 8000]
    assert [shot["slope"] for shot in hold] == [32, -32]
    assert all(shot["hold"] and shot["delay_after"] == p["TRACKER_INIT_DELAY"] for shot in hold)


def test_original_cli_profile_preserves_modules_sources_and_one_shot_causality(verified) -> None:
    profile, evidence, ir = verified
    assert evidence["repetitions"] == 2
    assert evidence["parameter_overrides"] == {}
    assert evidence["synthetic_inputs"] is True and evidence["hardware_measurement"] is False
    assert all(item["status"] == "blocked" for item in ir["execution"].values())
    assert not [item for item in ir["diagnostics"] if item["severity"] == "error"]
    assert {Path(point["source"]["file"]).name for point in evidence["checkpoints"]["slope_branches"]} == {"red_tracker.q1asm"}
    assert {point["source"]["line"] for point in evidence["checkpoints"]["frequency_writes"][1:]} == {68}
    expected = evidence["expected"]["shots"]
    assert expected[1]["orange_frequency_word"] == evidence["expected"]["next_frequency_words"][0]
    assert expected[1]["orange_gain"] == expected[0]["gain"] * verification.parameters()["RF_GAIN_NUMERATOR"] // 2**verification.parameters()["RF_GAIN_SHIFT"]
    assert all(len(checks) == 2 for checks in evidence["dac_sample_checks"].values())


def test_verifier_rejects_same_shot_output_or_wrong_branch_in_fresh_ir(verified) -> None:
    _profile, evidence, original = verified
    p = verification.parameters()
    ir = deepcopy(original)
    event = next(event for event in ir["events"] if event["kind"] == "play" and event["sequencer_id"] == "orange_drive")
    event["meta"]["applied_state"]["frequency"] += 512
    with pytest.raises(verification.VerificationFailure, match="frequency"):
        verification.check(ir, evidence["expected"], p)
    ir = deepcopy(original)
    event = next(event for event in ir["events"] if event["id"] == evidence["checkpoints"]["slope_branches"][0]["event_id"])
    event["meta"]["branch_taken"] = not event["meta"]["branch_taken"]
    with pytest.raises(verification.VerificationFailure, match="branch outcomes"):
        verification.check(ir, evidence["expected"], p)


def test_verifier_rejects_missing_causal_writer_link(verified) -> None:
    _profile, evidence, original = verified
    ir = deepcopy(original)
    ir["register_history"][0]["event_id"] = "missing-writer"
    with pytest.raises(verification.VerificationFailure, match="Missing register writer"):
        verification.check(ir, evidence["expected"], verification.parameters())


@pytest.mark.parametrize("failure", ["prepare", "cli", "interrupt"])
def test_failed_rerun_replaces_previous_success_evidence(tmp_path, monkeypatch, failure) -> None:
    profile = "two-shot-feedback"
    folder = tmp_path / profile
    folder.mkdir()
    evidence_path = folder / "evidence.json"
    evidence_path.write_text('{"passed": true}', encoding="utf-8")

    def fail_prepare(*args):
        raise verification.VerificationFailure("preparation failed")

    def fail_cli(command, **kwargs):
        if failure == "interrupt":
            raise KeyboardInterrupt()
        return subprocess.CompletedProcess(command, 1, "", "analyzer failed")

    if failure == "prepare":
        monkeypatch.setattr(verification, "prepare", fail_prepare)
    else:
        monkeypatch.setattr(verification.subprocess, "run", fail_cli)
    expected_error = KeyboardInterrupt if failure == "interrupt" else verification.VerificationFailure
    with pytest.raises(expected_error):
        verification.run_profile(profile, tmp_path)

    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["passed"] is False
    assert evidence["status"] == ("interrupted" if failure == "interrupt" else "failed")


def test_prepare_only_replaces_old_verification_claims(tmp_path, monkeypatch) -> None:
    profile = "two-shot-feedback"
    folder = tmp_path / profile
    folder.mkdir()
    evidence_path = folder / "evidence.json"
    evidence_path.write_text('{"passed": true}', encoding="utf-8")
    report_path = tmp_path / "report.json"
    report_path.write_text('[{"passed": true}]', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--profile", profile, "--out-dir", str(tmp_path), "--prepare-only"])

    assert verification.main() == 0

    for result in [json.loads(evidence_path.read_text(encoding="utf-8")),
                   *json.loads(report_path.read_text(encoding="utf-8"))]:
        assert result["passed"] is False
        assert result["status"] == "prepared"
