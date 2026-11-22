import json
import sys
from pathlib import Path

import pytest

from q1lens.cli import main


SUM = Path(__file__).resolve().parents[1] / "examples" / "def-register-sweep" / "sum.q1asm"


def prove(capsys, source, bounds, assertion="R2 < 1024", *extra):
    pytest.importorskip("z3")
    code = main(["q1timeline", "prove", str(source), "--range", bounds, "--assert", assertion, *extra])
    return code, json.loads(capsys.readouterr().out)


def test_def_sweep_example_is_proved_through_1013(capsys):
    code, result = prove(capsys, SUM, "X=0..1013")
    assert (code, result["status"]) == (0, "proved")


def test_prove_explains_how_to_repair_an_incomplete_installation(capsys, monkeypatch):
    monkeypatch.delitem(sys.modules, "q1timeline.analysis.smt", raising=False)
    monkeypatch.delitem(sys.modules, "q1timeline.analysis.smt_execution", raising=False)
    monkeypatch.setitem(sys.modules, "z3", None)
    code = main(["q1timeline", "prove", str(SUM), "--range", "X=0..1013", "--assert", "R2 < 1024"])
    assert code == 2
    assert "pip install -e ." in capsys.readouterr().err


def test_def_sweep_counterexample_reproduces_register_writes_in_concrete_ir(tmp_path, capsys):
    timeline = tmp_path / "counterexample.json"
    code, result = prove(capsys, SUM, "X=0..1014", "R2 < 1024", "--replay-ir", str(timeline))
    assert (code, result["status"]) == (1, "counterexample")
    assert result["counterexample"] == {"X": 1014}
    replay = result["replay"]
    assert (replay["mode"], replay["register"], replay["value"]) == ("concrete", "R2", 1024)

    ir = json.loads(timeline.read_text(encoding="utf-8"))
    execution, = ir["execution"].values()
    assert execution["status"] == "completed"
    writes = {row["register"]: row["value"]["value"] for row in ir["register_history"]}
    assert writes == {"R0": 1014, "R1": 10, "R2": 1024}


def test_counterexample_requires_matching_concrete_replay(tmp_path, capsys, monkeypatch):
    from q1timeline.analysis import smt_replay

    monkeypatch.setattr(smt_replay, "replay_register", lambda *args, **kwargs: (1023, {}))
    timeline = tmp_path / "counterexample.json"
    code, result = prove(capsys, SUM, "X=0..1014", "R2 < 1024", "--replay-ir", str(timeline))
    assert (code, result["status"]) == (2, "unknown")
    assert "counterexample" not in result and not timeline.exists()


def test_unsigned_add_wraps_and_stop_retires_its_write(tmp_path, capsys):
    source = tmp_path / "wrap.q1asm"
    source.write_text(".DEF X 0\nmove $X,R0\nnop\nadd R0,-1,R2\nstop\n", encoding="utf-8")
    code, result = prove(capsys, source, "X=1..1", "R2 < 1")
    assert (code, result["status"]) == (0, "proved")
    code, result = prove(capsys, source, "X=0..1", "R2 < 1")
    assert (code, result["status"]) == (1, "counterexample")
    assert result["counterexample"] == {"X": 0}
    assert result["replay"]["value"] == 0xFFFFFFFF


@pytest.mark.parametrize(("bounds", "witness"), [
    ("X=-2..0", -1),
    ("X=1..4294967295", 4294967295),
])
def test_witness_preserves_the_def_integer_in_its_requested_range(tmp_path, capsys, bounds, witness):
    source = tmp_path / "identity.q1asm"
    source.write_text(".DEF X 0\nmove $X,R2\nstop\n", encoding="utf-8")
    code, result = prove(capsys, source, bounds, "R2 < 4294967295")
    assert (code, result["status"]) == (1, "counterexample")
    assert result["counterexample"] == {"X": witness}
    assert result["replay"]["value"] == 0xFFFFFFFF


@pytest.mark.parametrize("body", [
    "move $X,R2\nwait_trigger 1,4\nstop",
    "move $X,R0\nnop\nstop",
    "move $X,R0\nadd R0,1,R2\nstop",
    "move X,R2\nstop",
], ids=["unsupported-rt", "unwritten-result", "read-before-ready", "bare-def"])
def test_unknown_if_execution_cannot_establish_a_valid_final_register(tmp_path, capsys, body):
    source = tmp_path / "unknown.q1asm"
    source.write_text(f".DEF X 0\n{body}\n", encoding="utf-8")
    code, result = prove(capsys, source, "X=0..1")
    assert (code, result["status"]) == (2, "unknown")


def test_stop_operand_must_be_valid_for_the_entire_def_range(tmp_path, capsys):
    source = tmp_path / "exit.q1asm"
    source.write_text(".DEF X 0\nmove 0,R2\nstop $X\n", encoding="utf-8")
    code, result = prove(capsys, source, "X=0..2147483647")
    assert (code, result["status"]) == (0, "proved")
    code, result = prove(capsys, source, "X=0..2147483648")
    assert (code, result["status"]) == (2, "unknown")
