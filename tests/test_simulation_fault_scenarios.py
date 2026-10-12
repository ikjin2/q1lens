from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from q1lens.cli import main as cli


ROOT = Path(__file__).parents[1] / "examples" / "simulation-verification" / "faults"
CASES = (
    "01-feedback-missing", "02-feedback-delayed", "03-feedback-duplicate",
    "04-trigger-missing", "05-queue-full", "06-feedback-too-late", "07-feedback-overflow",
)
ERROR_CASES = {"01-feedback-missing", "06-feedback-too-late", "07-feedback-overflow"}


@pytest.fixture
def verifier():
    spec = importlib.util.spec_from_file_location("fault_scenario_verify", ROOT / "verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def traces(tmp_path_factory):
    output = tmp_path_factory.mktemp("fault-scenario-ir")
    result = {}
    for case in CASES:
        path = output / f"{case}.json"
        code = cli(["q1timeline", "analyze", "--project", str(ROOT / case / "q1timeline.yml"),
                    "--out", str(path), "--format", "vscode-json", "--summary-only"])
        assert code == (2 if case in ERROR_CASES else 0)
        result[case] = json.loads(path.read_text(encoding="utf-8"))
    return result


@pytest.mark.parametrize("case", CASES)
def test_public_cli_fault_scenarios_match_independent_expectations(case, verifier, traces):
    assert verifier.check(case, traces[case])["status"] == traces[case]["execution"]["seq"]["status"]


@pytest.mark.parametrize("mutation", [
    "missing-marked-completed", "missing-no-underflow", "delayed-early-write",
    "duplicate-collapsed-input", "duplicate-wrong-writer-origin", "trigger-not-pending",
    "queue-too-deep", "queue-lost-execution", "queue-out-of-order", "late-invented-input",
    "overflow-wrong-cause", "overflow-lost-input-origin", "overflow-invented-register",
])
def test_fault_checker_rejects_corrupted_results(mutation, verifier, traces):
    case = {"missing": CASES[0], "delayed": CASES[1], "duplicate": CASES[2], "trigger": CASES[3],
            "queue": CASES[4], "late": CASES[5], "overflow": CASES[6]}[mutation.split("-")[0]]
    ir = copy.deepcopy(traces[case])
    events = lambda kind: [event for event in ir["events"] if event["kind"] == kind]
    if mutation == "missing-marked-completed":
        ir["execution"]["seq"]["status"] = "completed"
    elif mutation == "missing-no-underflow":
        ir["events"].remove(events("underflow_warning")[0])
    elif mutation == "delayed-early-write":
        ir["register_history"][0]["time"]["value"] = 407
    elif mutation == "duplicate-collapsed-input":
        ir["events"].remove(events("feedback_receive")[1])
    elif mutation == "duplicate-wrong-writer-origin":
        ir["register_history"][1]["meta"]["provenance"]["input_event_id"] = events("feedback_receive")[0]["id"]
    elif mutation == "trigger-not-pending":
        events("trigger_wait")[0]["meta"]["pending"] = False
    elif mutation == "queue-too-deep":
        events("rt_queue_enqueue")[1]["meta"]["depth"] = 3
    elif mutation == "queue-lost-execution":
        ir["events"].remove(events("wait")[2])
    elif mutation == "queue-out-of-order":
        events("rt_queue_dequeue")[0]["meta"]["rt_packet_id"] = "seq:p1"
    elif mutation == "late-invented-input":
        ir["events"].append(copy.deepcopy(verifier.events(traces[CASES[1]], "feedback_receive")[0]))
    elif mutation == "overflow-wrong-cause":
        ir["diagnostics"][0]["category"] = "rt_queue_underflow"
    elif mutation == "overflow-lost-input-origin":
        events("feedback_receive")[1]["meta"]["input_index"] = 0
    elif mutation == "overflow-invented-register":
        ir["register_history"].append(copy.deepcopy(traces[CASES[1]]["register_history"][0]))
    with pytest.raises(AssertionError):
        verifier.check(case, ir)


@pytest.mark.parametrize("fault", ["missing-output", "bad-json", "wrong-exit", "timeout", "start-failure", "different-replay"])
def test_fault_runner_cannot_reuse_a_stale_success(tmp_path, monkeypatch, verifier, traces, fault):
    (tmp_path / "report.json").write_text('{"passed": true}', encoding="utf-8")
    (tmp_path / "run-1.json").write_text(json.dumps(traces[CASES[1]]), encoding="utf-8")

    def run(command, **kwargs):
        output = Path(command[command.index("--out") + 1])
        if fault == "timeout":
            raise subprocess.TimeoutExpired(command, 60)
        if fault == "start-failure":
            raise OSError("unable to start CLI")
        ir = copy.deepcopy(traces[CASES[1]])
        if fault == "different-replay" and output.name == "run-2.json":
            ir["project"]["name"] = "changed replay metadata"
        if fault != "missing-output":
            output.write_text("broken json" if fault == "bad-json" else json.dumps(ir), encoding="utf-8")
        return subprocess.CompletedProcess(command, 1 if fault == "wrong-exit" else 0, "stdout", "stderr")

    monkeypatch.setattr(verifier.subprocess, "run", run)
    assert verifier.main(["--out-dir", str(tmp_path), "--case", CASES[1]]) == 1
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["passed"] is False
    assert report["cases"][0]["passed"] is False


def test_fault_runner_checks_both_fresh_replays_and_preserves_inputs(tmp_path, monkeypatch, verifier, traces):
    paths = []
    monkeypatch.setenv("PYTHONPATH", "existing-core-search-path")

    def run(command, **kwargs):
        assert kwargs["cwd"] == verifier.REPO
        assert kwargs["env"]["PYTHONPATH"] == str(verifier.REPO / "src") + os.pathsep + "existing-core-search-path"
        project = Path(command[command.index("--project") + 1])
        output = Path(command[command.index("--out") + 1])
        assert not output.exists()
        paths.append(output)
        case = project.parent.name
        output.write_text(json.dumps(traces[case]), encoding="utf-8")
        return subprocess.CompletedProcess(command, 2 if case in ERROR_CASES else 0, "stdout", "stderr")

    monkeypatch.setattr(verifier.subprocess, "run", run)
    assert verifier.main(["--out-dir", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["core_source_path"] == str(verifier.REPO / "src")
    assert len(paths) == len(set(paths)) == 14
    assert len(report["source_sha256"]) == 14
    assert all(result["repeatable"] for result in report["cases"])
    assert all(Path(run["trace"]).is_file() for result in report["cases"] for run in result["runs"])
    assert all(run["core_source_path"] == report["core_source_path"] for result in report["cases"] for run in result["runs"])


def test_missing_fault_source_cannot_report_success(tmp_path, monkeypatch, verifier):
    monkeypatch.setattr(verifier, "ROOT", tmp_path / "missing-scenarios")
    assert verifier.main(["--out-dir", str(tmp_path / "evidence"), "--case", CASES[0]]) == 1
    report = json.loads((tmp_path / "evidence" / "report.json").read_text(encoding="utf-8"))
    assert report["passed"] is False


def test_fault_expectations_are_enforced_under_optimized_python():
    command = (
        "import importlib.util; "
        f"spec=importlib.util.spec_from_file_location('faults', {str(ROOT / 'verify.py')!r}); "
        "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
        "m.equal('completed', 'blocked', 'must still fail')"
    )
    run = subprocess.run([sys.executable, "-O", "-c", command], capture_output=True, text=True)
    assert run.returncode != 0
    assert "AssertionError: must still fail" in run.stderr
