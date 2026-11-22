import json
from pathlib import Path

import pytest

from q1lens.cli import main
from q1timeline.q1asm.ast import with_def_values
from q1timeline.q1asm.parser import parse_q1asm_file
from q1timeline.simulation.runtime import simulate_programs


pytest.importorskip("z3")
EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "def-register-sweep"


def prove(capsys, file, bounds, *query):
    status = main(["q1timeline", "prove", str(EXAMPLES / file), "--range", bounds, *query])
    return status, json.loads(capsys.readouterr().out)


def test_ordered_branch_path_and_instruction_witness(capsys, tmp_path):
    source = "branches.q1asm"
    bounds = "X=0..1014"
    for query in [("--path", "check=taken,below=taken"), ("--reach", "@contradiction")]:
        code, result = prove(capsys, source, bounds, *query)
        assert (code, result["status"]) == (1, "unreachable")
    code, result = prove(capsys, source, bounds, "--path", "check=taken,below=fallthrough")
    assert (code, result["status"]) == (0, "reachable")
    assert 0 <= result["witness"]["X"] <= 1013
    assert result["replay"]["branches"] == [{"pc": 5, "taken": True}, {"pc": 8, "taken": False}]

    timeline = tmp_path / "witness.json"
    code, result = prove(capsys, source, bounds, "--reach", "limit", "--replay-ir", str(timeline))
    assert (code, result["status"], result["witness"]) == (0, "reachable", {"X": 1014})
    assert result["replay"]["branches"] == [{"pc": 5, "taken": False}]
    execution, = json.loads(timeline.read_text(encoding="utf-8"))["execution"].values()
    assert (execution["mode"], execution["status"], execution["stop_code"]) == ("concrete", "completed", 1)


def test_bounds_and_final_state_after_branches(capsys):
    source = "branches.q1asm"
    for limit in [("--max-steps", "5"), ("--max-paths", "1")]:
        code, result = prove(capsys, source, "X=0..1014", "--reach", "contradiction", *limit)
        assert (code, result["status"]) == (2, "unknown")
    for property_ in ["stop_code == 0", "q1_time > 0", "rt_time == 0", "R2 <= 1023"]:
        code, result = prove(capsys, source, "X=0..1013", "--assert", property_)
        assert (code, result["status"]) == (0, "proved")
    code, result = prove(capsys, source, "X=0..1014", "--assert", "stop_code == 0")
    assert (code, result["counterexample"], result["replay"]["value"]) == (1, {"X": 1014}, 1)


def test_queue_threshold_against_every_input_in_simulation(capsys, tmp_path):
    program = parse_q1asm_file(EXAMPLES / "queue.q1asm")
    violations = {}
    for n in range(1, 39):
        run, = simulate_programs({"s": with_def_values(program, {"N": n})}, simulation={"startup_delay_ns": 4})
        if run.metadata["execution"]["status"] == "error":
            marker, = [e for e in run.events if e.kind == "underflow_warning"]
            violations[n] = marker.t0.value
        else:
            assert run.metadata["execution"]["status"] == "completed"
    assert violations == {38: 1188}
    code, result = prove(capsys, "queue.q1asm", "N=1..38", "--underflow-at", "4", "--max-steps", "1")
    assert (code, result["status"]) == (2, "unknown")
    code, result = prove(capsys, "queue.q1asm", "N=1..37", "--underflow-at", "4")
    assert (code, result["status"]) == (0, "unreachable")
    timeline = tmp_path / "underflow.json"
    code, result = prove(capsys, "queue.q1asm", "N=1..38", "--underflow-at", "4", "--replay-ir", str(timeline))
    assert (code, result["status"], result["witness"]) == (1, "reachable", {"N": 38})
    assert result["replay"]["underflow_ns"] == violations[38]
    assert any(row["kind"] == "underflow_warning" for row in json.loads(timeline.read_text())["events"])
