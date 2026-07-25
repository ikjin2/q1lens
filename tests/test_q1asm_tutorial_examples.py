from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from q1timeline.cli import main as q1timeline_main


ROOT = Path("examples/q1asm-tutorials")
EXPECTED_PROJECTS = {
    "06-before-after-fixes/23-feedback-channel-imbalance-fixed",
    "06-before-after-fixes/21-forgot-linq-wait-fixed",
    "06-before-after-fixes/26-forgot-upd-param-fixed",
    "06-before-after-fixes/19-too-tight-loop-fixed",
    "05-common-mistakes/24-assumed-runtime-branch",
    "05-common-mistakes/22-feedback-channel-imbalance",
    "05-common-mistakes/20-forgot-linq-wait",
    "05-common-mistakes/25-forgot-upd-param",
    "05-common-mistakes/18-too-tight-loop",
    "11-control-flow-patterns/35-bounded-retry-loop",
    "11-control-flow-patterns/36-branch-table-selector",
    "02-debug-reading/05-q1-issue-vs-rt-time",
    "02-debug-reading/06-queue-depth-basics",
    "02-debug-reading/07-slack-basics",
    "02-debug-reading/08-source-line-navigation",
    "08-diagnostics-gallery/29-slack-and-branch-gallery",
    "12-feedback-patterns/38-latency-violation",
    "12-feedback-patterns/39-linq-throughput-simulator",
    "12-feedback-patterns/37-register-broadcast",
    "01-getting-started/04-feedback-round-trip",
    "01-getting-started/01-hello-timeline",
    "01-getting-started/02-play-then-acquire",
    "01-getting-started/03-two-lane-alignment",
    "04-hardware-first/16-marker-and-scope-basics",
    "04-hardware-first/13-qcm-drive-only",
    "04-hardware-first/15-qcm-qrm-basic-readout",
    "04-hardware-first/14-qrm-readout-only",
    "04-hardware-first/17-triggered-shot-basics",
    "13-hardware-patterns/42-acquisition-telemetry-sidecar",
    "13-hardware-patterns/41-marker-gated-awg-window",
    "13-hardware-patterns/40-qcm-qrm-triggered-readout",
    "13-hardware-patterns/43-readout-feedback-reset-forensics",
    "13-hardware-patterns/47-qcm-qrm-threshold-pull-router",
    "13-hardware-patterns/48-qcm-qrm-iq-pull-router",
    "09-latched-state-patterns/30-atomic-parameter-window",
    "14-multi-sequencer-coordination/46-feedback-arbitration",
    "14-multi-sequencer-coordination/45-trigger-skew-comparison",
    "14-multi-sequencer-coordination/44-two-qubit-staggered-readout",
    "03-params-basics/09-define-constants",
    "03-params-basics/10-params-json-duration",
    "03-params-basics/11-params-json-feedback-channel",
    "03-params-basics/12-retune-without-editing-q1asm",
    "10-stateful-programs/31-bounded-random-walk",
    "10-stateful-programs/33-hysteresis-threshold-controller",
    "10-stateful-programs/32-round-robin-bin-writer",
    "10-stateful-programs/34-saturating-counter",
    "07-timing-pathologies/28-branch-housekeeping-underflow",
    "07-timing-pathologies/27-short-loop-underflow",
}


def _tutorial_projects() -> list[Path]:
    return sorted(ROOT.glob("*/*/q1timeline.yml"))


def _analyze_project(project_file: Path, tmp_path: Path) -> dict:
    out_file = tmp_path / project_file.parent.relative_to(ROOT) / "timeline_ir.json"
    exit_code = q1timeline_main(["analyze", "--project", str(project_file), "--out", str(out_file)])

    assert exit_code == 0
    return json.loads(out_file.read_text(encoding="utf-8"))


def test_q1asm_tutorial_corpus_has_expected_first_batch() -> None:
    projects = _tutorial_projects()

    assert {str(path.parent.relative_to(ROOT)).replace("\\", "/") for path in projects} == EXPECTED_PROJECTS
    assert (ROOT / "README.md").exists()
    assert (ROOT / "q1asm-tutorials.code-workspace").exists()
    assert {path.parts[-3] for path in projects} == {
        "06-before-after-fixes",
        "11-control-flow-patterns",
        "05-common-mistakes",
        "02-debug-reading",
        "08-diagnostics-gallery",
        "07-timing-pathologies",
        "10-stateful-programs",
        "12-feedback-patterns",
        "01-getting-started",
        "04-hardware-first",
        "13-hardware-patterns",
        "09-latched-state-patterns",
        "14-multi-sequencer-coordination",
        "03-params-basics",
    }


@pytest.mark.parametrize("project_file", _tutorial_projects(), ids=lambda path: str(path.parent.relative_to(ROOT)))
def test_q1asm_tutorial_projects_analyze(project_file: Path, tmp_path: Path) -> None:
    ir = _analyze_project(project_file, tmp_path)

    assert ir["events"]
    assert ir["sequencers"]


def test_q1asm_tutorials_include_expected_teaching_signals(tmp_path: Path) -> None:
    signals: dict[str, set[str]] = {}
    for project_file in _tutorial_projects():
        ir = _analyze_project(project_file, tmp_path)
        key = str(project_file.parent.relative_to(ROOT)).replace("\\", "/")
        signals[key] = {
            *(event.get("kind", "") for event in ir.get("events", [])),
            *("latched_state_applied" for event in ir.get("events", []) if event.get("meta", {}).get("applied_state")),
            *(diagnostic.get("category", "") for diagnostic in ir.get("diagnostics", [])),
            *(
                channel.get("status", "")
                for channel in ir.get("feedback_balance", {}).get("channels", {}).values()
            ),
        }

    assert "definite_underflow" in signals["07-timing-pathologies/27-short-loop-underflow"]
    assert "possible_underflow" in signals["07-timing-pathologies/28-branch-housekeeping-underflow"]
    assert "branch_region" in signals["10-stateful-programs/31-bounded-random-walk"]
    assert "acquire" in signals["10-stateful-programs/32-round-robin-bin-writer"]
    assert "branch_region" in signals["10-stateful-programs/33-hysteresis-threshold-controller"]
    assert "branch_region" in signals["10-stateful-programs/34-saturating-counter"]
    assert "feedback_pop" in signals["12-feedback-patterns/37-register-broadcast"]
    assert "feedback_latency_violation" in signals["12-feedback-patterns/38-latency-violation"]
    assert {"feedback_com", "feedback_pop", "feedback_latency_violation", "over_produced"} <= signals[
        "12-feedback-patterns/39-linq-throughput-simulator"
    ]
    assert {"branch_region", "latched_state_pending"} <= signals["11-control-flow-patterns/35-bounded-retry-loop"]
    assert "branch_region" in signals["11-control-flow-patterns/36-branch-table-selector"]
    assert {"possible_underflow", "unresolved_branch"} <= signals["08-diagnostics-gallery/29-slack-and-branch-gallery"]
    assert {"latched_state_pending", "latched_state_applied", "play"} <= signals[
        "09-latched-state-patterns/30-atomic-parameter-window"
    ]
    assert {"wait_trigger", "play", "acquire"} <= signals["13-hardware-patterns/40-qcm-qrm-triggered-readout"]
    assert {"marker_state", "latched_state_applied", "acquire"} <= signals["13-hardware-patterns/41-marker-gated-awg-window"]
    assert {"feedback_pop", "branch_region"} <= signals["13-hardware-patterns/43-readout-feedback-reset-forensics"]
    assert {"acquire", "feedback_pop", "branch_region", "balanced"} <= signals[
        "13-hardware-patterns/47-qcm-qrm-threshold-pull-router"
    ]
    assert {"acquire", "feedback_pop", "branch_region", "balanced"} <= signals[
        "13-hardware-patterns/48-qcm-qrm-iq-pull-router"
    ]
    assert {"play", "acquire"} <= signals["13-hardware-patterns/42-acquisition-telemetry-sidecar"]
    assert {"wait_sync", "play", "acquire"} <= signals["14-multi-sequencer-coordination/44-two-qubit-staggered-readout"]
    assert {"feedback_pop", "under_produced"} <= signals["14-multi-sequencer-coordination/46-feedback-arbitration"]
    assert {"wait_trigger", "play", "acquire"} <= signals["14-multi-sequencer-coordination/45-trigger-skew-comparison"]
    assert {"definite_underflow", "loop_truncated"} <= signals["05-common-mistakes/18-too-tight-loop"]
    assert "feedback_latency_violation" in signals["05-common-mistakes/20-forgot-linq-wait"]
    assert "over_produced" in signals["05-common-mistakes/22-feedback-channel-imbalance"]
    assert {"branch_region", "unresolved_branch"} <= signals["05-common-mistakes/24-assumed-runtime-branch"]
    assert {"latched_state_pending", "latched_state_applied", "play"} <= signals[
        "05-common-mistakes/25-forgot-upd-param"
    ]
    assert {"wait_sync", "wait", "play"} <= signals["01-getting-started/01-hello-timeline"]
    assert {"play", "acquire"} <= signals["01-getting-started/02-play-then-acquire"]
    assert {"wait_sync", "play", "acquire"} <= signals["01-getting-started/03-two-lane-alignment"]
    assert {"feedback_com", "feedback_pop", "balanced"} <= signals["01-getting-started/04-feedback-round-trip"]
    assert {"q1_issue", "play", "slack"} <= signals["02-debug-reading/05-q1-issue-vs-rt-time"]
    assert {"queue_depth", "play", "wait"} <= signals["02-debug-reading/06-queue-depth-basics"]
    assert {"slack", "play", "wait_sync"} <= signals["02-debug-reading/07-slack-basics"]
    assert {"play", "acquire"} <= signals["02-debug-reading/08-source-line-navigation"]
    assert {"loop_block", "loop_iteration_preview", "play"} <= signals["06-before-after-fixes/19-too-tight-loop-fixed"]
    assert {"feedback_com", "feedback_pop", "balanced"} <= signals["06-before-after-fixes/21-forgot-linq-wait-fixed"]
    assert "balanced" in signals["06-before-after-fixes/23-feedback-channel-imbalance-fixed"]
    assert {"latched_state_pending", "latched_state_applied", "play"} <= signals[
        "06-before-after-fixes/26-forgot-upd-param-fixed"
    ]
    assert {"wait_sync", "play"} <= signals["04-hardware-first/13-qcm-drive-only"]
    assert {"play", "acquire"} <= signals["04-hardware-first/14-qrm-readout-only"]
    assert {"wait_sync", "play", "acquire"} <= signals["04-hardware-first/15-qcm-qrm-basic-readout"]
    assert {"marker_state", "acquire"} <= signals["04-hardware-first/16-marker-and-scope-basics"]
    assert {"wait_trigger", "play", "acquire"} <= signals["04-hardware-first/17-triggered-shot-basics"]
    assert {"wait_sync", "play"} <= signals["03-params-basics/09-define-constants"]
    assert {"wait_sync", "play"} <= signals["03-params-basics/10-params-json-duration"]
    assert {"feedback_com", "feedback_pop", "balanced"} <= signals["03-params-basics/11-params-json-feedback-channel"]
    assert {"latched_state_pending", "latched_state_applied", "play"} <= signals[
        "03-params-basics/12-retune-without-editing-q1asm"
    ]


def test_q1asm_tutorial_readme_commands_work_from_workspace_root(tmp_path: Path) -> None:
    readmes = sorted(path.resolve() for path in ROOT.glob("*/*/README.md"))
    assert len(readmes) == len(EXPECTED_PROJECTS)

    old_cwd = Path.cwd()
    try:
        os.chdir(ROOT)
        for readme in readmes:
            text = readme.read_text(encoding="utf-8")
            match = re.search(r"python -m q1timeline analyze --project (\S+)", text)
            assert match is not None, f"Missing analyze command in {readme}"
            project_arg = match.group(1)

            assert not project_arg.startswith("examples/q1asm-tutorials/")
            assert q1timeline_main(["analyze", "--project", project_arg, "--out", str(tmp_path / f"{readme.parent.name}.json")]) == 0
    finally:
        os.chdir(old_cwd)


def test_linq_throughput_simulator_exposes_feedback_pressure(tmp_path: Path) -> None:
    ir = _analyze_project(ROOT / "12-feedback-patterns/39-linq-throughput-simulator/q1timeline.yml", tmp_path)

    channels = ir["feedback_balance"]["channels"]
    diagnostics = {diagnostic["category"] for diagnostic in ir["diagnostics"]}

    assert len(ir["feedback_flows"]) == 9
    assert channels["16"]["status"] == "over_produced"
    assert channels["16"]["send_payloads"] == 8
    assert channels["16"]["matched"] == 6
    assert channels["18"]["status"] == "balanced"
    assert channels["19"]["status"] == "balanced"
    assert channels["19"]["send_payloads"] == 2
    assert channels["19"]["matched"] == 2
    assert "feedback_latency_violation" in diagnostics


def test_threshold_pull_router_uses_fb_pull_data_for_threshold_result(tmp_path: Path) -> None:
    ir = _analyze_project(ROOT / "13-hardware-patterns/47-qcm-qrm-threshold-pull-router/q1timeline.yml", tmp_path)

    flows = ir["feedback_flows"]
    diagnostics = {diagnostic["category"] for diagnostic in ir["diagnostics"]}

    assert flows == [
        {
            "id": "feedback-flow-0",
            "from_event_id": "qrm_threshold_router:e9",
            "to_event_id": "qrm_threshold_router:e15",
            "channel": "4",
            "source": "acq#0/bin0",
            "target": "R1",
            "label": "feedback ch 4: acq#0/bin0 -> R1",
        }
    ]
    channel = ir["feedback_balance"]["channels"]["4"]
    assert channel["send_payloads"] == 1
    assert channel["matched"] == 1
    assert channel["status"] == "balanced"
    assert "feedback_latency_violation" not in diagnostics


def test_iq_pull_router_uses_fb_pull_data_for_iq_values(tmp_path: Path) -> None:
    ir = _analyze_project(ROOT / "13-hardware-patterns/48-qcm-qrm-iq-pull-router/q1timeline.yml", tmp_path)

    flows = ir["feedback_flows"]
    diagnostics = {diagnostic["category"] for diagnostic in ir["diagnostics"]}
    events_by_id = {event["id"]: event for event in ir["events"]}

    assert [(flow["channel"], flow["source"], flow["target"]) for flow in flows] == [
        ("4", "acq#0/bin0", "R1"),
        ("4", "acq#0/bin0", "R2"),
    ]
    assert {
        events_by_id[flow["from_event_id"]]["meta"]["feedback"]["data_type"]
        for flow in flows
    } == {"iq_values"}
    assert ir["feedback_balance"]["channels"]["4"]["status"] == "balanced"
    assert "feedback_latency_violation" not in diagnostics


@pytest.mark.parametrize(
    ("project", "channel", "status"),
    [
        ("05-common-mistakes/22-feedback-channel-imbalance", "16", "over_produced"),
        ("14-multi-sequencer-coordination/46-feedback-arbitration", "18", "under_produced"),
    ],
)
def test_feedback_channel_imbalance_reports_warning_diagnostic(
    project: str,
    channel: str,
    status: str,
    tmp_path: Path,
) -> None:
    ir = _analyze_project(ROOT / project / "q1timeline.yml", tmp_path)

    diagnostics = ir["diagnostics"]
    fifo_diagnostic = next(
        diagnostic for diagnostic in diagnostics if diagnostic["category"] == "feedback_fifo_imbalance"
    )

    assert fifo_diagnostic["severity"] == "warning"
    assert fifo_diagnostic["details"]["channel"] == channel
    assert fifo_diagnostic["details"]["status"] == status
    assert not any(diagnostic["severity"] == "error" for diagnostic in diagnostics)
