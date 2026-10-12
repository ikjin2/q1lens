from __future__ import annotations

import json
from pathlib import Path

import pytest

from q1timeline.analysis.api import analyze_documents, analyze_text
from q1timeline.cli import main
from q1timeline.project import ConfigLoadError, load_project_config
from q1timeline.simulation.config import validate_simulation
from q1timeline.watch.watcher import run_watch_once


EXAMPLES = Path(__file__).parents[1] / "examples" / "simulation-verification"


@pytest.mark.parametrize("config,field", [
    ({"queue_capacity": True}, "queue_capacity"),
    ({"queue_capacity": 0}, "queue_capacity"),
    ({"max_time_ns": float("inf")}, "max_time_ns"),
    ({"inputs": []}, "inputs"),
    ({"inputs": {"feedback": [{"target": "missing", "time_ns": 0, "id": 1, "value": 0}]}}, "unknown sequencer"),
    ({"routes": [{"source": "a", "target": "a", "latency_ns": -1}]}, "latency_ns"),
    ({"inputs": {"triggers": [{"target": "a", "time_ns": 0, "address": 16}]}}, "address"),
    ({"acquisition_iq_scale": {"a": 10**1000}}, "finite number"),
])
def test_invalid_scenarios_are_rejected(config: dict, field: str) -> None:
    with pytest.raises(ValueError, match=field):
        validate_simulation(config, ["a"])


def test_scenario_defaults_do_not_mutate_caller() -> None:
    original = {"inputs": {"feedback": []}}
    normalized = validate_simulation(original, ["a"])
    assert original == {"inputs": {"feedback": []}}
    assert normalized["queue_capacity"] == 32
    assert normalized["startup_delay_ns"] == 200


def test_simulation_api_executes_shared_feedback_clock() -> None:
    ir = analyze_documents({
        "sender.q1asm": "wait_sync 4\nfb_com_data 16,7,4\nwait 800\nstop\n",
        "receiver.q1asm": "wait_sync 4\nwait 800\nfb_pop_data 16,R0\nnop\nadd R0,1,R1\nnop\nstop\n",
    }, execution_mode="simulation", alignment_mode="none", simulation={
        "routes": [{"source": "sender", "target": "receiver", "id": 16, "latency_ns": 24}],
    })
    assert all(item["status"] == "completed" for item in ir["execution"].values())
    writes = [write for write in ir["register_history"] if write["sequencer_id"] == "receiver"]
    assert {write["register"]: write["value"]["value"] for write in writes} == {"R0": 7, "R1": 8}
    assert any(event["kind"] == "feedback_receive" for event in ir["events"])
    assert all(item["timing_model"] == "event_driven" for item in ir["execution"].values())
    assert not any(item["category"] == "feedback_latency_hazard" for item in ir["diagnostics"])
    json.dumps(ir, allow_nan=False)


def test_simulation_api_exposes_queue_stalls_and_real_register_history() -> None:
    program = (EXAMPLES / "01-digital" / "counter.q1asm").read_text(encoding="utf-8")
    ir = analyze_text(program, sequencer_id="counter", execution_mode="simulation", simulation={"queue_capacity": 2})
    assert ir["execution"]["counter"]["status"] == "completed"
    assert ir["simulation"]["counter"]["queue_capacity"] == 2
    assert any(event["kind"] == "q1_stall" for event in ir["events"])
    assert [write["value"]["value"] for write in ir["register_history"] if write["register"] == "R1"] == [0, 1, 2, 3]


def test_project_cli_and_watch_preserve_simulation_results(tmp_path: Path, capsys) -> None:
    project = EXAMPLES / "02-communication" / "q1timeline.yml"
    assert load_project_config(project).analysis.execution_mode == "simulation"
    output = tmp_path / "timeline.json"
    assert main(["analyze", "--project", str(project), "--out", str(output), "--format", "vscode-json", "--summary-only"]) == 0
    summary = json.loads(capsys.readouterr().out)
    ir = json.loads(output.read_text(encoding="utf-8"))
    assert summary["execution"] == ir["execution"]
    assert ir["sequencers"] == [{"id": "sender", "module": "QCM0"}, {"id": "receiver", "module": "QRM0"}]
    result = run_watch_once(project, tmp_path / "watch")
    assert result.ok
    watched = json.loads(result.timeline_ir.read_text(encoding="utf-8"))
    assert watched["sequencers"] == ir["sequencers"]
    assert watched["execution"] == ir["execution"]
    assert watched["simulation"] == ir["simulation"]


def test_invalid_project_scenario_becomes_configuration_diagnostic(tmp_path: Path) -> None:
    (tmp_path / "a.q1asm").write_text("stop\n", encoding="utf-8")
    path = tmp_path / "q1timeline.yml"
    path.write_text("sequencers:\n  - id: a\n    file: a.q1asm\nsimulation:\n  queue_capacity: 0\n", encoding="utf-8")
    with pytest.raises(ConfigLoadError) as error:
        load_project_config(path)
    assert error.value.diagnostics[-1].category == "invalid_simulation_config"
