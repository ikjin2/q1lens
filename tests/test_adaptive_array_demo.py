from __future__ import annotations

import json
import os
import re
from pathlib import Path

from q1timeline.cli import main as q1timeline_main


ROOT = Path("examples/adaptive-array-demo")


def _analyze(tmp_path: Path) -> dict:
    out_file = tmp_path / "adaptive-array-ir.json"
    exit_code = q1timeline_main(["analyze", "--project", str(ROOT / "q1timeline.yml"), "--out", str(out_file)])

    assert exit_code == 0
    return json.loads(out_file.read_text(encoding="utf-8"))


def test_adaptive_array_demo_is_larger_than_three_peak(tmp_path: Path) -> None:
    ir = _analyze(tmp_path)

    assert len(ir["sequencers"]) >= 14
    assert len(ir["events"]) > 850
    assert len(ir["feedback_flows"]) >= 27


def test_adaptive_array_demo_has_expected_signals(tmp_path: Path) -> None:
    ir = _analyze(tmp_path)

    kinds = {event["kind"] for event in ir["events"]}
    categories = {diagnostic["category"] for diagnostic in ir["diagnostics"]}
    applied_events = [event for event in ir["events"] if event.get("meta", {}).get("applied_state")]

    assert {
        "wait_trigger",
        "play",
        "acquire",
        "feedback_com",
        "feedback_pop",
        "branch_region",
        "marker_state",
        "latched_state_pending",
        "upd_param",
    } <= kinds
    assert applied_events
    assert {"feedback_latency_violation", "unresolved_branch"} <= categories


def test_adaptive_array_demo_feedback_balance(tmp_path: Path) -> None:
    ir = _analyze(tmp_path)

    channels = ir["feedback_balance"]["channels"]

    for channel in ("20", "21", "22", "23", "30", "31", "32", "33", "40", "41", "42", "43"):
        assert channels[channel]["status"] == "balanced"

    assert channels["20"]["send_payloads"] == 2
    assert channels["20"]["matched"] == 2
    assert channels["60"]["status"] == "over_produced"
    assert channels["60"]["send_payloads"] == 8
    assert channels["60"]["matched"] == 6
    assert channels["61"]["status"] == "balanced"


def test_adaptive_array_demo_readme_and_workspace(tmp_path: Path) -> None:
    assert (ROOT / "adaptive-array-demo.code-workspace").exists()

    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    match = re.search(r"python -m q1timeline analyze --project (\S+)", text)
    assert match is not None

    old_cwd = Path.cwd()
    try:
        os.chdir(ROOT)
        assert q1timeline_main(["analyze", "--project", match.group(1), "--out", str(tmp_path / "readme-ir.json")]) == 0
    finally:
        os.chdir(old_cwd)


def test_adaptive_array_demo_has_english_html_overview() -> None:
    html = (ROOT / "demo-overview.html").read_text(encoding="utf-8")

    assert "<html lang=\"en\">" in html
    assert "What This Demo Shows" in html
    assert "17 sequencers" in html
    assert "874 timeline events" in html
    assert "27 feedback flows" in html
    assert "not a hardware recipe" in html
    assert "Channel 60" in html
    assert "Channel 61" in html
    assert "Q1Lens: Open Timeline Preview" in html
    assert "Sequencer Input/Output Map" in html
    assert "diagram-node source" in html
    assert "Readout IQ feedback" in html
    assert "Tracker decisions" in html
    assert "Arbiter grants" in html
    assert "Telemetry pressure" in html
    assert "Fault probe" in html
    assert "CH_IQ_Q0-CH_IQ_Q3" in html
    assert "CH_TRACK_Q0-CH_TRACK_Q3" in html
    assert "CH_GRANT_Q0-CH_GRANT_Q3" in html
    assert "CH_TELEMETRY" in html
    assert "CH_FAULT" in html


def test_adaptive_array_demo_uses_params_file() -> None:
    params_file = ROOT / "params.json"
    project_text = (ROOT / "q1timeline.yml").read_text(encoding="utf-8")
    source_text = "\n".join(path.read_text(encoding="utf-8") for path in ROOT.glob("*.q1asm"))
    params = json.loads(params_file.read_text(encoding="utf-8"))

    assert "params:" in project_text
    assert "file: params.json" in project_text
    assert {
        "TRIGGER_INDEX",
        "TRIGGER_TIMEOUT",
        "CH_IQ_Q0",
        "CH_TRACK_Q0",
        "CH_GRANT_Q0",
        "CH_TELEMETRY",
        "CH_FAULT",
    } <= set(params)
    assert ".DEF TRIG_IDX {TRIGGER_INDEX}" in source_text
    assert ".DEF IQ_Q0 {CH_IQ_Q0}" in source_text
    assert ".DEF GRANT_Q0 {CH_GRANT_Q0}" in source_text
    assert "wait_trigger $TRIG_IDX,$TRIG_WAIT" in source_text
    assert "fb_acq_iq_id $IQ_Q0,0" in source_text
    assert "fb_com_data $TELEMETRY" in source_text
    assert "fb_pop_data $GRANT_Q0" in source_text

    instruction_lines = [
        line
        for line in source_text.splitlines()
        if line.strip() and not line.strip().startswith((".DEF", "#", ";"))
    ]
    assert not any(re.search(r"(?<![{$])\b(?:CH_|TRIGGER_)[A-Z0-9_]*", line) for line in instruction_lines)
