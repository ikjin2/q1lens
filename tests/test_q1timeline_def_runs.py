import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from q1timeline.def_runs import RunSnapshot, numeric_defs, sweep_plan
from q1timeline.analysis.interpreter import interpret_program
from q1timeline.q1asm.parser import parse_q1asm


FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "def-register-sweep"


def snapshot(project, **options):
    return RunSnapshot(Namespace(**{
        "project": project, "execution_mode": None, "max_steps": None,
        "q1asm_strict": False, "hardware_readiness": False, "params": None, "set_param": [], **options,
    }))


def request(run, seq="sum", register="R2", **overrides):
    return {"revision": run.revision, "sequencer": seq, "uri": run.sources[seq]["uri"],
            "register": register, "overrides": overrides}


def program(tmp_path, text, *, config="", sequencers=None):
    (tmp_path / "sum.q1asm").write_text(text, encoding="utf-8")
    project = tmp_path / "q1timeline.yml"
    project.write_text(config + (sequencers or "sequencers:\n  - id: sum\n    file: sum.q1asm\n"), encoding="utf-8")
    return project


def axis(name="X", lower="0", upper="2", step="1"):
    return {"name": name, "min": lower, "max": upper, "step": step}


def test_numeric_defs_come_from_valid_declarations():
    parsed = parse_q1asm(""".DEF X 0x10
.DEF NEG -3
.DEF REGISTER R0
.DEF DEST @end
.DEF TEMPLATE {input}
# .DEF COMMENT 1
.DEF BAD +1
.DEF FLOAT 1.0
.DEF DUP 1
.DEF DUP 2
end: stop
""")
    assert {key: value.value for key, value in numeric_defs(parsed).items()} == {"X": 16, "NEG": -3}
    assert len(parsed.diagnostics) == 3


def test_info_lists_registers_referenced_by_each_sequencer(tmp_path):
    (tmp_path / "other.q1asm").write_text("move 1,R42\nstop\n", encoding="utf-8")
    (tmp_path / "empty.q1asm").write_text("stop # R50\n", encoding="utf-8")
    project = program(tmp_path, """.DEF OUT R6
.DEF UNUSED R59
.DEF R17 4
move $R17,$OUT # R62
move $R28,$OUT
add R0xA,R2,$OUT
jmp @R43
R43: stop
""", sequencers=("sequencers:\n  - id: sum\n    file: sum.q1asm\n"
                 "  - id: other\n    file: other.q1asm\n  - id: empty\n    file: empty.q1asm\n"))
    run = snapshot(project, set_param=["R28=9"])
    assert {seq["id"]: seq["registers"] for seq in run.info()["sequencers"]} == {
        "sum": ["R2", "R6", "R10"], "other": ["R42"], "empty": [],
    }


def test_literal_overrides_win_over_params_and_trace_has_real_readiness():
    paths = [FIXTURE / name for name in ("sum.q1asm", "params.json", "q1timeline.yml")]
    original = [path.read_bytes() for path in paths]
    run = snapshot(paths[-1], set_param=["X=100"])
    assert run.mode == "concrete"
    assert run.run(*run.inputs(request(run)))["word"] == 10
    detail = run.run(*run.inputs(request(run, X="0x2")), detail=True)
    assert detail["case"]["defs"] == {"X": "2", "Y": "10"}
    assert detail["case"]["word"] == 12
    issues = [item for item in detail["ir"]["events"] if item["kind"] == "q1_issue"]
    assert [event["meta"]["op"] for event in issues] == ["move", "move", "nop", "add", "nop", "nop", "nop", "stop"]
    assert [event["meta"]["execution_index"] for event in issues] == list(range(8))
    assert detail["case"]["q1_ns"] == issues[-1]["t1"]["value"]
    assert issues[3]["meta"]["register_reads"]["R0"]["value"] == 2
    assert "R2" not in issues[4]["meta"]["register_snapshot"]
    write = next(item for item in detail["ir"]["register_history"] if item["register"] == "R2")
    assert write["event_id"] == issues[3]["id"]
    assert write["time"]["value"] == issues[5]["t0"]["value"]
    assert run.run(*run.inputs(request(run)))["word"] == 10
    assert [path.read_bytes() for path in paths] == original


def test_override_scope_includes_source_and_sequencer(tmp_path):
    (tmp_path / "other.q1asm").write_text(".DEF X 4\nmove $X,R0\nnop\nstop\n", encoding="utf-8")
    project = program(tmp_path, ".DEF X 1\nmove $X,R0\nnop\nstop\n", sequencers=(
        "sequencers:\n  - id: sum\n    file: sum.q1asm\n  - id: same\n    file: sum.q1asm\n  - id: other\n    file: other.q1asm\n"))
    run = snapshot(project)
    detail = run.run(*run.inputs(request(run, register="R0", X="2")), detail=True)
    assert {write["sequencer_id"]: write["value"]["value"] for write in detail["ir"]["register_history"]} == {
        "sum": 2, "same": 1, "other": 4,
    }
    assert run.sources["sum"]["uri"] == run.sources["same"]["uri"]
    wrong_source = {**request(run, X="3"), "uri": run.sources["other"]["uri"]}
    with pytest.raises(ValueError, match="source has changed"):
        run.inputs(wrong_source)
    with pytest.raises(ValueError, match="editable numeric DEFs"):
        run.inputs(request(run, UNKNOWN="3"))


@pytest.mark.parametrize("literal", ["0b1", "+1", "01", "1.0", "1e3", "NaN", "Infinity", "1 # comment", "1\n2"])
def test_bad_literals_are_rejected_before_execution(literal):
    run = snapshot(FIXTURE / "q1timeline.yml")
    with pytest.raises(ValueError, match="Q1ASM integer literal"):
        run.inputs(request(run, X=literal))
    with pytest.raises(ValueError, match="Q1ASM integer literal"):
        sweep_plan([axis(lower=literal)], numeric_defs(run.programs["sum"]))


def test_plan_counts_exact_integer_grid():
    defs = numeric_defs(parse_q1asm(".DEF X 0\n.DEF Y 10\n"))
    plan = sweep_plan([axis(upper="5", step="2"), axis("Y", "10", "10", "3")], defs)
    assert plan == {"axes": [{"name": "X", "count": "3", "min": "0", "step": "2", "last": "4"},
                             {"name": "Y", "count": "1", "min": "10", "step": "3", "last": "10"}],
                    "total": "3", "allowed": True, "limit": 4096, "sample_limit": 25_000}
    assert sweep_plan([axis(upper="4095")], defs)["allowed"]
    assert not sweep_plan([axis(upper="4096")], defs)["allowed"]
    assert sweep_plan([axis(upper="999999")], defs, statistics_only=True)["allowed"]
    assert not sweep_plan([axis(upper="1000000")], defs, statistics_only=True)["allowed"]
    assert not sweep_plan([axis(upper="100"), axis("Y", upper="100")], defs)["allowed"]
    assert sweep_plan([axis(lower="9007199254740993", upper="9007199254740995")], defs)["total"] == "3"
    large = "9" * 3000
    plan = sweep_plan([axis(upper=large), axis("Y", upper=large)], defs)
    assert not plan["allowed"] and plan["total"] == "1" + "0" * 6000
    for invalid in ([axis(step="0")], [axis(step="-1")], [axis(lower="2", upper="1")], [axis(), axis()]):
        with pytest.raises(ValueError):
            sweep_plan(invalid, defs)


@pytest.mark.parametrize("body,mode,limit,status,word", [
    ("stop\n", "concrete", 100, "undetermined", None),
    ("move 2,R2\nstop\n", "simulation", 100, "completed", 2),
    ("move 1,R2\nnop\nmove 2,R2\nstop\n", "simulation", 3, "step_limit", None),
    ("wait_trigger 1,4\nstop\n", "simulation", 100, "blocked", None),
    ("again: jmp @again\n", "concrete", 3, "step_limit", None),
    ("illegal\n", "concrete", 100, "error", None),
    ("move -1,R2\nnop\nstop\n", "concrete", 100, "completed", 0xFFFFFFFF),
])
def test_final_output_requires_a_completed_readable_word(tmp_path, body, mode, limit, status, word):
    run = snapshot(program(tmp_path, body), execution_mode=mode, max_steps=limit)
    detail = run.run(*run.inputs(request(run)), detail=True)
    assert detail["case"]["status"] == status
    assert detail["case"]["word"] == word
    if "move 2" in body and status == "step_limit":
        assert detail["pending"][0]["register"] == "R2"
        assert detail["pending"][0]["ready_ns"] > detail["case"]["q1_ns"]


def test_completed_engine_with_future_write_does_not_return_old_word(tmp_path, monkeypatch):
    project = program(tmp_path, "move 1,R2\nnop\nmove 2,R2\nstop\n")
    run = snapshot(project)
    state = interpret_program(run.programs["sum"], sequencer_id="sum", execution_mode="concrete", max_steps=3)
    assert state.registers["R2"].value == 1 and state.pending_register_writes[0].value.value == 2
    # Exercise the completion contract even when this engine's current stop timing matures the write.
    state.metadata["execution"]["status"] = "completed"
    state.diagnostics.clear()
    monkeypatch.setattr("q1timeline.def_runs.interpret_program", lambda *args, **kwargs: state)
    case = run.run(*run.inputs(request(run)))
    assert case["status"] == "undetermined" and case["word"] is None
    assert "pending final write" in case["reason"]


def test_execution_mode_and_fixed_simulation_scenario(tmp_path):
    config = ("analysis:\n  execution_mode: simulation\n  max_steps: 4\n"
              "simulation:\n  inputs:\n    triggers:\n      - target: sum\n        address: 1\n        time_ns: 60\n")
    project = program(tmp_path, "move 2,R2\nwait_trigger 1,4\nstop\n", config=config)
    run = snapshot(project)
    assert (run.mode, run.max_steps) == ("simulation", 4)
    first = run.run(*run.inputs(request(run)))
    assert first["word"] == 2
    assert first == run.run(*run.inputs(request(run)))
    override = snapshot(project, execution_mode="analysis", max_steps=2)
    assert (override.mode, override.max_steps) == ("concrete", 2)


def test_loop_repeats_source_line_as_separate_occurrences(tmp_path):
    project = program(tmp_path, ".DEF N 3\nmove $N,R0\nnop\nagain: nop\nloop R0,@again\nstop\n")
    run = snapshot(project)
    detail = run.run(*run.inputs(request(run, register="R0")), detail=True)
    issues = [event for event in detail["ir"]["events"] if event["kind"] == "q1_issue"]
    occurrences = [event for event in issues if event["source"]["line"] == 4]
    assert len(occurrences) == 3
    assert len({event["id"] for event in occurrences}) == 3
    assert len({event["meta"]["execution_index"] for event in occurrences}) == 3


@pytest.mark.parametrize("mode", ["concrete", "simulation"])
def test_statistics_preparation_reuses_lints_and_matches_case_execution(tmp_path, monkeypatch, mode):
    from q1timeline.analysis import interpreter

    project = program(tmp_path, ".DEF X 4\n.DEF Y 1\nmove $Y,R2\nwait $X\nnop\nstop\n")
    run = snapshot(project, execution_mode=mode)
    cases = [{"X": x, "Y": 17} for x in (4, 8, -4, 4)]
    expected = [run.run("sum", "R2", values) for values in cases]
    prepared = run.prepare_statistics("sum", cases[0], {"X"})
    assert [instruction.op for instruction in prepared["sum"].revalidate] == ["wait"]
    calls = []
    lint = interpreter.analyze_program_lints

    def tracked_lint(*args, **kwargs):
        calls.append(1)
        return lint(*args, **kwargs)

    monkeypatch.setattr(interpreter, "analyze_program_lints", tracked_lint)
    for values, original in zip(cases, expected):
        actual = run.run("sum", "R2", values, prepared=prepared)
        assert actual == {key: original[key] for key in ("status", "engine_completed", "word", "q1_ns")}
    assert expected[2]["status"] == "error"
    assert len(calls) == 1  # Only the invalid input needs normal source recovery.


@pytest.mark.parametrize("mode", ["concrete", "simulation"])
def test_statistics_runs_branches_and_wraps_words_without_building_trace(tmp_path, monkeypatch, mode):
    from q1timeline.analysis import interpreter

    project = program(tmp_path, ".DEF X 1\nmove $X,R2\nmove 3,R0\n"
                      "again: add R2,1,R2\nnop\nloop R0,@again\nstop\n")
    run = snapshot(project, execution_mode=mode)
    cases = [{"X": x} for x in (0, -1, 0xFFFFFFFF, 0x7FFFFFFF)]
    expected = [run.run("sum", "R2", values) for values in cases]
    assert [case["word"] for case in expected] == [3, 2, 2, 0x80000002]
    prepared = run.prepare_statistics("sum", cases[0], {"X"})

    def unexpected_trace(*args, **kwargs):
        pytest.fail("Statistics generated a trace entry for a Q1-only program")

    monkeypatch.setattr(interpreter, "TimelineEvent", unexpected_trace)
    monkeypatch.setattr(interpreter, "RegisterWrite", unexpected_trace)
    monkeypatch.setattr(interpreter, "_register_issue_metadata", unexpected_trace)
    for values, case in zip(cases, expected):
        actual = run.run("sum", "R2", values, prepared=prepared)
        assert actual == {key: case[key] for key in ("status", "engine_completed", "word", "q1_ns")}


@pytest.mark.parametrize("mode", ["concrete", "simulation"])
def test_statistics_keeps_runtime_register_hazards(tmp_path, mode):
    run = snapshot(program(tmp_path, ".DEF X 1\nmove $X,R0\nadd R0,1,R2\nstop\n"), execution_mode=mode)
    expected = run.run("sum", "R2", {"X": 2})
    prepared = run.prepare_statistics("sum", {"X": 1}, {"X"})
    assert expected["status"] == "error"
    assert any(item["category"] == "register_not_ready" for item in expected["diagnostics"])
    assert run.run("sum", "R2", {"X": 2}, prepared=prepared) == {
        key: expected[key] for key in ("status", "engine_completed", "word", "q1_ns")
    }


def test_statistics_keeps_simulation_feedback_and_blocking(tmp_path):
    config = ("simulation:\n  inputs:\n    feedback:\n"
              "      - {target: sum, id: 5, value: 9, time_ns: 60}\n")
    run = snapshot(program(tmp_path, ".DEF ID 5\nfb_pop_data $ID,R2\nwait 20\nstop\n", config=config),
                   execution_mode="simulation")
    prepared = run.prepare_statistics("sum", {"ID": 5}, {"ID"})
    for value, status, word in [(5, "completed", 9), (6, "blocked", None), (5, "completed", 9)]:
        values = {"ID": value}
        expected = run.run("sum", "R2", values)
        assert (expected["status"], expected["word"]) == (status, word)
        assert run.run("sum", "R2", values, prepared=prepared) == {
            key: expected[key] for key in ("status", "engine_completed", "word", "q1_ns")
        }


def test_statistics_keeps_concrete_queue_error(tmp_path, monkeypatch):
    from q1timeline.analysis import underflow

    monkeypatch.setattr(underflow, "ASSUMED_INITIAL_BUFFER_NS", 12)
    monkeypatch.setattr(underflow, "POSSIBLE_INITIAL_BUFFER_NS", 24)
    run = snapshot(program(tmp_path, ".DEF N 1\nmove 7,R2\nwait 4\nmove $N,R0\n"
                           "nop\nagain: nop\nloop R0,@again\nwait 4\nstop\n"))
    prepared = run.prepare_statistics("sum", {"N": 1}, {"N"})
    for count, status in [(1, "completed"), (3, "error")]:
        values = {"N": count}
        expected = run.run("sum", "R2", values)
        assert expected["status"] == status
        if status == "error":
            assert any(item["category"] == "definite_underflow" for item in expected["diagnostics"])
        assert run.run("sum", "R2", values, prepared=prepared) == {
            key: expected[key] for key in ("status", "engine_completed", "word", "q1_ns")
        }


def test_worker_aggregates_statistics_without_case_rows(tmp_path):
    project = program(tmp_path, ".DEF X -2\nmove $X,R2\nnop\nstop\n")
    run = snapshot(project)
    common = request(run)
    responses = subprocess.run(
        [sys.executable, "-m", "q1timeline.def_runs", "--project", str(project)],
        input="".join(json.dumps({"id": i, **payload}) + "\n" for i, payload in enumerate([
            {"command": "sweep", **common, "axes": [axis(lower="-2", upper="-1")], "statistics_only": True},
            {"command": "batch"},
        ])), capture_output=True, encoding="utf-8", timeout=10, check=True,
    )
    started, finished = map(json.loads, responses.stdout.splitlines())
    assert started["plan"]["limit"] == 1_000_000
    assert set(finished) == {"id", "summary"}
    summary = finished["summary"]
    assert summary["count"] == 2 and summary["statuses"] == {"completed": 2}
    assert summary["signed"] == {"count": 2, "min": -2, "max": -1, "mean": -1.5, "deviation": 0.5}
    assert summary["unsigned"]["mean"] == 4294967294.5
    assert summary["unsigned"]["deviation"] == 0.5
    assert summary["time"]["count"] == 2 and summary["time"]["deviation"] == 0


def test_worker_batches_and_reproduces_selected_case(tmp_path):
    project = program(tmp_path, (FIXTURE / "sum.q1asm").read_text(encoding="utf-8"))
    run = snapshot(project)
    cmd = [sys.executable, "-m", "q1timeline.def_runs", "--project", str(project)]
    with subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, encoding="utf-8") as worker:
        def call(command, **payload):
            worker.stdin.write(json.dumps({"id": 1, "command": command, **payload}, ensure_ascii=False) + "\n")
            worker.stdin.flush()
            return json.loads(worker.stdout.readline())

        assert call("info")["info"]["revision"] == run.revision
        base = request(run)
        assert call("run", **{**base, "overrides": {"X": "1٢"}})["case"]["word"] == 22
        assert "error" in call("sweep", **base, axes=[axis(upper="4096")])
        assert "error" in call("batch")
        for axes, values in [([axis()], [10, 11, 12]), ([axis(), axis("Y", "10", "12", "2")], [10, 12, 11, 13, 12, 14])]:
            assert call("sweep", **base, axes=axes)["plan"]["total"] == str(len(values))
            rows = call("batch")["rows"]
            assert [row["word"] for row in rows] == values
            assert all("ir" not in row for row in rows)
            selected = rows[2]
            detail = call("run", **{**base, "overrides": selected["defs"]}, case_id=selected["id"])
            assert detail["case"] == selected
            assert "ir" in detail
        multi = {key: value for key, value in base.items() if key != "register"}
        multi.update(registers=["R0", "R2", "R63"], observe_time=False)
        assert call("sweep", **multi, axes=[axis()])["plan"]["total"] == "3"
        rows = call("batch")["rows"]
        assert [row["words"] for row in rows] == [
            {"R0": x, "R2": x + 10, "R63": None} for x in range(3)
        ]
        assert all(row["status"] == "completed" and "q1_ns" not in row for row in rows)
        detail = call("run", **{**multi, "overrides": rows[1]["defs"]}, case_id=2)
        assert detail["case"] == rows[1]
        assert any(event["kind"] == "q1_issue" for event in detail["ir"]["events"])

        call("sweep", **multi, axes=[axis()], statistics_only=True)
        summary = call("batch")["summary"]
        assert "time" not in summary
        assert summary["registers"]["R0"]["signed"]["mean"] == 1
        assert summary["registers"]["R2"]["signed"]["mean"] == 11
        assert summary["registers"]["R63"] == {"signed": None, "unsigned": None}

        time_only = {**multi, "registers": [], "observe_time": True}
        call("sweep", **time_only, axes=[axis()], statistics_only=True)
        summary = call("batch")["summary"]
        assert summary["registers"] == {}
        assert summary["time"] == {"count": 3, "min": 40, "max": 40, "mean": 40, "deviation": 0}
        assert "error" in call("sweep", **{**time_only, "observe_time": False}, axes=[axis()])

        sampled = {**multi, "observe_time": True}
        axes = [axis(lower="-100", upper="100", step="3"), axis("Y", "-70", "60", "2")]
        for invalid in (0, -1, 1.5, "4", True, None, 25_001):
            assert "Sample count" in call("sweep", **sampled, axes=axes, sample_count=invalid)["error"]
        assert "error" in call("batch")
        assert call("sweep", **sampled, axes=axes, sample_count=25_000)["plan"]["total"] == "25000"
        plan = call("sweep", **sampled, axes=axes, sample_count=6)["plan"]
        assert plan["sampling"] == "random" and plan["total"] == "6" and plan["allowed"]
        rows = call("batch")["rows"]
        assert len(rows) == 6
        for case_id, row in enumerate(rows, 1):
            x, y = (int(row["defs"][name]) for name in ("X", "Y"))
            assert x in range(-100, 101, 3) and y in range(-70, 61, 2)
            assert row["id"] == case_id and row["words"]["R2"] == (x + y) % (2**32) and row["q1_ns"] == 40
        assert not call("batch")["rows"]
        detail = call("run", **{**sampled, "overrides": rows[-1]["defs"]}, case_id=6)
        assert detail["case"] == rows[-1] and "ir" in detail

        singleton = [axis(lower="-1", upper="-1")]
        plan = call("sweep", **sampled, axes=singleton, sample_count=8, statistics_only=True)["plan"]
        assert plan["sampling"] == "random" and plan["total"] == "8"
        summary = call("batch")["summary"]
        assert summary["count"] == 8 and summary["time"]["count"] == 8
        assert summary["registers"]["R2"]["signed"] == {"count": 8, "min": 9, "max": 9, "mean": 9, "deviation": 0}

        assert "error" in call("run", **{**multi, "registers": ["R1", "R1"]})
        (tmp_path / "sum.q1asm").write_text("stop\n", encoding="utf-8")
        assert "changed" in call("run", **base)["error"]
        (tmp_path / "sum.q1asm").write_text(".DEF X 0\nmove 1,R2\nwait $X\nstop\n", encoding="utf-8")
        base["revision"] = call("info")["info"]["revision"]
        assert call("sweep", **base, axes=[axis(lower="-4", upper="4", step="8")])["plan"]["total"] == "2"
        rows = call("batch")["rows"]
        assert [(row["status"], row["word"]) for row in rows] == [("error", None), ("completed", 1)]
        for bad_instruction in ("not_an_instruction", "move 1,R64", "jmp @missing", "move $MISSING,R2", "wait -4"):
            (tmp_path / "sum.q1asm").write_text(f".DEF X 0\n{bad_instruction}\nstop\n", encoding="utf-8")
            base["revision"] = call("info")["info"]["revision"]
            assert "Fix the source" in call("sweep", **base, axes=[axis()])["error"]
        worker.stdin.close()
        assert worker.wait(timeout=10) == 0
