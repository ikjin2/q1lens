"""Physical result contract through public API, project CLI, and watch."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from q1timeline.analysis.api import analyze_documents
from q1timeline.cli import main
from q1timeline.project import load_project_config
from q1timeline.watch.watcher import run_watch_once


EXAMPLES = Path(__file__).parents[1] / "examples" / "simulation-verification"


def analyze_project_inputs(project, simulation=None):
    documents = {str(seq.file): seq.file.read_text(encoding="utf-8") for seq in project.sequencers}
    sequences = {seq.id: json.loads(seq.sequence_json.read_text(encoding="utf-8"))
                 for seq in project.sequencers if seq.sequence_json is not None}
    return analyze_documents(documents, alignment_mode="none", execution_mode="simulation",
                             simulation=simulation if simulation is not None else project.simulation,
                             sequences=sequences)


@pytest.mark.parametrize("example,end,i,q,mean_i,source", [
    ("03-signals", 272, 31.953125, 0, 0.499267578125, "loopback"),
    ("05-supplied-input", 212, 1, -2, 0.125, "supplied_trace"),
])
def test_physical_api_cli_watch_preserve_exact_results(tmp_path, capsys, example, end, i, q, mean_i, source):
    path = EXAMPLES / example / "q1timeline.yml"
    project = load_project_config(path)
    api = analyze_project_inputs(project)
    output = tmp_path / "fresh-cli" / "timeline.json"
    assert not output.exists()
    assert main(["analyze", "--project", str(path), "--out", str(output), "--format", "vscode-json", "--summary-only"]) == 0
    summary = json.loads(capsys.readouterr().out)
    cli = json.loads(output.read_text(encoding="utf-8"))
    watched = run_watch_once(path, tmp_path / "fresh-watch")
    assert watched.ok
    watch = json.loads(watched.timeline_ir.read_text(encoding="utf-8"))
    assert summary["execution"] == cli["execution"] == watch["execution"] == api["execution"]
    for seq in (item.id for item in project.sequencers):
        assert api["simulation"][seq]["signals"] == cli["simulation"][seq]["signals"] == watch["simulation"][seq]["signals"]
    for ir in (api, cli, watch):
        signal = ir["simulation"]["readout"]["signals"]
        result = signal["acquisitions"][0]
        assert (result["end_ns"], result["i"], result["q"], result["mean_i"], result["thresholded"]) == (end, i, q, mean_i, 1)
        assert result["source"]["kind"] == source
        assert result["units"] == result["threshold_units"] == signal["units"]["integration"] == "normalized full scale * ns"
        if source == "supplied_trace":
            assert result["source"]["measurement_claim"] is None
            assert "synthetic" in result["source"]["label"]
        events = [event for event in ir["events"] if event["kind"] == "acquisition_result" and event["sequencer_id"] == "readout"]
        assert len(events) == 1 and events[0]["t0"]["kind"] == "concrete"
        assert events[0]["t0"]["value"] == end
        json.dumps(ir, allow_nan=False)


def test_user_designated_measured_input_is_a_claim_not_hardware_validation():
    project = load_project_config(EXAMPLES / "05-supplied-input" / "q1timeline.yml")
    scenario = copy.deepcopy(project.simulation)
    scenario["physical"]["sequencers"]["readout"]["input"]["measured"] = True
    ir = analyze_project_inputs(project, scenario)
    signal = ir["simulation"]["readout"]["signals"]
    source = signal["acquisitions"][0]["source"]
    assert source["kind"] == "measured_input" and source["measurement_claim"] == "user-supplied"
    assert "synthetic" in source["label"]
    assert any("not measurement verification" in item for item in signal["assumptions"])


def test_missing_physical_trace_coverage_blocks_without_result_or_bin():
    project = load_project_config(EXAMPLES / "05-supplied-input" / "q1timeline.yml")
    scenario = copy.deepcopy(project.simulation)
    scenario["physical"]["sequencers"]["readout"]["input"]["start_ns"] = 205
    ir = analyze_project_inputs(project, scenario)
    assert ir["execution"]["readout"]["status"] == "blocked"
    signal = ir["simulation"]["readout"]["signals"]
    assert signal["acquisitions"] == signal["bins"] == []
    assert not any(event["kind"] == "acquisition_result" for event in ir["events"])
    json.dumps(ir, allow_nan=False)
