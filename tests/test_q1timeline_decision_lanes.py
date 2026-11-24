from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from q1timeline.analysis.values import Concrete
from q1timeline.decision_lanes import BASE_RUN, LaneRecorder, validate_feedback
from q1timeline.playback import PlaybackSession


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
RESET = EXAMPLES / "q1asm-tutorials" / "13-hardware-patterns" / "43-readout-feedback-reset-forensics" / "q1timeline.yml"
GOLDEN = EXAMPLES / "golden-section" / "q1timeline.yml"

# Step and branch tuple positions (the record layout the view reads).
PC, Q0, Q1, T0, T1, READS, WRITES, SNAP, BRANCH, NEXT = range(10)
TAKEN, CALCULATED, FORCED, PRODUCER, TARGET, FALL, COMPARE = range(7)

LOOP = """\
        move 3, R0
        move 0, R2
        nop
body:   wait 100
        fb_pop_data 16, R1
        nop
        cmp R1, 5
        jge @high
        add R2, 1, R2
        jmp @next
high:   add R2, 10, R2
next:   nop
        loop R0, @body
        stop
"""

ONCE = """\
.DEF RESULT R0
wait 600
fb_pop_data 16,$RESULT
nop
jlt $RESULT,1,@skip
wait 20
stop
skip:
wait 80
stop
"""


def project(tmp_path: Path, text: str, *, config: str = "", name: str = "seq") -> Path:
    (tmp_path / f"{name}.q1asm").write_text(text, encoding="utf-8")
    path = tmp_path / "q1timeline.yml"
    path.write_text(f"{config}sequencers:\n  - id: {name}\n    file: {name}.q1asm\n", encoding="utf-8")
    return path


def steps_of(runs: dict, run_id: str) -> list:
    run = runs[run_id]
    return steps_of(runs, run["base"])[:run["shared"]] + run["steps"] if run.get("base") else run["steps"]


def line_of(data: dict, step: list) -> int:
    return data["instructions"][step[PC]]["line"]


def test_base_run_records_times_reads_writes_and_the_compared_words(tmp_path):
    data = LaneRecorder(project(tmp_path, ONCE)).open(feedback=[1])
    base = data["runs"][BASE_RUN]
    assert data["schema"] == 1 and data["sequencer"] == "seq" and data["registers"][63] == "R63"
    assert data["source"][2] == "fb_pop_data 16,$RESULT"
    assert [item["op"] for item in data["instructions"]] == ["wait", "fb_pop_data", "nop", "jlt", "wait", "stop", "wait", "stop"]
    assert data["instructions"][3] == {"pc": 3, "line": 5, "op": "jlt", "text": "jlt $RESULT, 1, @skip", "args": ["$RESULT", "1", "@skip"]}
    assert data["labels"] == {"skip": 6}
    steps = base["steps"]
    assert [line_of(data, step) for step in steps] == [2, 3, 4, 5, 6, 7]
    pop, branch = steps[1], steps[3]
    assert pop[WRITES] == [[0, None, 1, pop[WRITES][0][3]]]                 # R0: unknown -> 1
    assert branch[READS] == [[0, 1, 1]]                                      # reads R0 = 1, written by step 1
    taken, calculated, forced, producer, target, fall, compare = branch[BRANCH]
    assert (taken, calculated, forced) == (False, False, False)              # 1 < 1 is false
    assert producer == 3                                                     # a three-operand jlt compares by itself
    assert (target, fall) == (6, 4)
    assert compare == [["$RESULT", 0, 1], ["1", None, 1]]
    assert [step[NEXT] for step in steps] == [1, 2, 3, 4, 5, None]
    assert all(step[Q0] <= step[Q1] and step[T0] <= step[T1] for step in steps)
    assert steps[0][T1] - steps[0][T0] == 600 and steps[4][T1] - steps[4][T0] == 20
    assert (base["status"], base["truncated"], base["reason"]) == ("completed", False, None)
    assert base["rtEnd"] == steps[-1][T1] and base["q1End"] == steps[-1][Q1]
    assert base["registers"][0] == 1 and base["registers"][5] is None
    assert data["inputs"] == {"feedback": [1], "scheduled": False, "usesSchedule": False, "pops": 1, "triggers": 0}
    assert data["engineModel"]["timingModel"] == "analytical" and data["engineModel"]["assumptions"]


def test_feedback_values_cycle_over_the_executed_pops_and_flags_name_their_producer(tmp_path):
    data = LaneRecorder(project(tmp_path, LOOP.replace("add R2, 10, R2", "add R2, 10, R2   # ten"))).open(feedback=[7, 2])
    steps = data["runs"][BASE_RUN]["steps"]
    # Instruction text leaves out the label and the comment of its line.
    assert [item["text"] for item in data["instructions"] if item["line"] in (4, 11)] == ["wait 100", "add R2, 10, R2"]
    pops = [step[WRITES][0][2] for step in steps if data["instructions"][step[PC]]["op"] == "fb_pop_data"]
    assert pops == [7, 2, 7]
    assert data["runs"][BASE_RUN]["registers"][2] == 21                      # +10, +1, +10
    decisions = [(index, step) for index, step in enumerate(steps) if data["instructions"][step[PC]]["op"] == "jge"]
    assert [step[BRANCH][TAKEN] for _, step in decisions] == [True, False, True]
    for index, step in decisions:
        producer = step[BRANCH][PRODUCER]
        assert producer == index - 1 and data["instructions"][steps[producer][PC]]["op"] == "cmp"
        assert step[BRANCH][COMPARE][1] == ["5", None, 5]
        assert step[BRANCH][COMPARE][0][:2] == ["R1", 1]
    loops = [step for step in steps if data["instructions"][step[PC]]["op"] == "loop"]
    assert [step[BRANCH][TAKEN] for step in loops] == [True, True, False]
    assert all(step[BRANCH][PRODUCER] == steps.index(step) for step in loops)   # loop tests its own counter


def test_branches_on_status_flags_name_the_instruction_that_set_them(tmp_path):
    source = ("wait 100\nfb_pop_data 16, R1\nnop\n"
              "test R1, 1\njnz @odd\nwait 8\n"
              "odd: cmp R1, 5\njb @below\nwait 8\n"
              "below: sub R1, 6, R2\njz @zero\nwait 8\n"
              "zero: nop\nstop\n")
    data = LaneRecorder(project(tmp_path, source)).open(feedback=[6])
    steps = data["runs"][BASE_RUN]["steps"]
    branch = {data["instructions"][step[PC]]["op"]: step[BRANCH] for step in steps if step[BRANCH]}
    producer = {op: data["instructions"][steps[item[PRODUCER]][PC]]["op"] for op, item in branch.items()}
    assert producer == {"jnz": "test", "jb": "cmp", "jz": "sub"}
    assert [branch[op][TAKEN] for op in ("jnz", "jb", "jz")] == [False, False, True]       # 6 & 1 = 0; 6 < 5 is false; 6 - 6 = 0
    assert branch["jnz"][COMPARE] == [["R1", 1, 6], ["1", None, 1]]                         # the operands of the test
    assert branch["jb"][COMPARE] == [["R1", 1, 6], ["5", None, 5]]
    assert len(branch["jz"]) == COMPARE                                                    # no compared pair: the result of sub decides
    assert steps[branch["jz"][PRODUCER]][WRITES][0][:3] == [2, None, 0]


def test_flip_forces_one_occurrence_and_shares_the_recorded_prefix(tmp_path):
    recorder = LaneRecorder(project(tmp_path, LOOP))
    data = recorder.open(feedback=[7, 2])
    base = data["runs"][BASE_RUN]["steps"]
    second = [index for index, step in enumerate(base) if data["instructions"][step[PC]]["op"] == "jge"][1]
    out = recorder.flips([{"run": BASE_RUN, "step": second}])
    assert out["failed"] == [] and out["pending"] == [] and list(out["runs"]) == [f"base~{second}"]
    flip = out["runs"][f"base~{second}"]
    assert (flip["kind"], flip["base"], flip["shared"], flip["fork"]) == ("flip", BASE_RUN, second, second)
    assert flip["forced"] == {f"{base[second][PC]}:1": True}
    full = steps_of({**data["runs"], **out["runs"]}, f"base~{second}")
    assert full[:second] == base[:second]
    assert full[second][BRANCH][:3] == [True, False, True]                   # taken, although 2 >= 5 is false
    assert full[second][BRANCH][COMPARE] == base[second][BRANCH][COMPARE]    # the compared words are unchanged
    assert flip["registers"][2] == 30                                        # +10 on every pass
    assert flip["status"] == "completed" and flip["rtEnd"] == data["runs"][BASE_RUN]["rtEnd"]
    # Asking again returns the recorded run instead of executing it once more.
    assert recorder.flips([{"run": BASE_RUN, "step": second}])["runs"][f"base~{second}"] is flip


def test_nested_flip_keeps_the_choices_of_its_parent(tmp_path):
    recorder = LaneRecorder(project(tmp_path, LOOP))
    data = recorder.open(feedback=[7, 2])
    base = data["runs"][BASE_RUN]["steps"]
    first = next(index for index, step in enumerate(base) if data["instructions"][step[PC]]["op"] == "jge")
    parent = recorder.flips([{"run": BASE_RUN, "step": first}])["runs"][f"base~{first}"]
    assert parent["registers"][2] == 12                                      # +1, +1, +10
    parent_steps = steps_of({**data["runs"], f"base~{first}": parent}, f"base~{first}")
    third_in_parent = [index for index, step in enumerate(parent_steps) if data["instructions"][step[PC]]["op"] == "jge"][2]
    nested = recorder.flips([{"run": f"base~{first}", "step": third_in_parent}])["runs"][f"base~{first}~{third_in_parent}"]
    assert nested["base"] == f"base~{first}" and len(nested["forced"]) == 2
    assert nested["registers"][2] == 3                                       # +1 on every pass
    assert nested["shared"] == third_in_parent and nested["fork"] == third_in_parent


def test_unknown_feedback_is_recorded_as_a_default_assumption(tmp_path):
    data = LaneRecorder(project(tmp_path, ONCE)).open()
    steps = data["runs"][BASE_RUN]["steps"]
    assert data["inputs"]["feedback"] is None and not data["inputs"]["usesSchedule"]
    assert steps[1][WRITES][0][2] is None                                    # the popped word is unknown
    assert steps[3][READS] == [[0, None, 1]]
    assert steps[3][BRANCH][:3] == [False, None, False]                      # no computed direction: assumed fall-through
    assert steps[3][BRANCH][COMPARE][0] == ["$RESULT", 0, None]


def test_project_linq_schedule_supplies_feedback_until_values_are_given(tmp_path):
    path = project(tmp_path, ONCE.replace("16,$RESULT", "9,$RESULT"),
                   config="external_inputs:\n  feedback:\n"
                          "    - {target: seq, arrivals: [{time_ns: 60, id: 9, value: 7}]}\n")
    recorder = LaneRecorder(path)
    scheduled = recorder.open()
    assert scheduled["inputs"] == {"feedback": None, "scheduled": True, "usesSchedule": True, "pops": 1, "triggers": 0}
    assert scheduled["runs"][BASE_RUN]["steps"][1][WRITES][0][2] == 7
    assert scheduled["runs"][BASE_RUN]["steps"][3][BRANCH][:2] == [False, False]
    typed = recorder.open(feedback=[0])
    assert typed["inputs"]["scheduled"] and not typed["inputs"]["usesSchedule"]
    assert typed["runs"][BASE_RUN]["steps"][1][WRITES][0][2] == 0
    assert typed["runs"][BASE_RUN]["steps"][3][BRANCH][:2] == [True, True]


def test_schedule_without_a_matching_arrival_ends_the_record_with_the_engine_reason(tmp_path):
    path = project(tmp_path, ONCE, config="external_inputs:\n  feedback:\n    - {target: seq, arrivals: []}\n")
    base = LaneRecorder(path).open()["runs"][BASE_RUN]
    assert (base["status"], base["truncated"], len(base["steps"])) == ("waiting", True, 1)
    assert base["reason"].startswith("No scheduled arrival supplies LINQ ID 16")
    assert base["steps"][-1][NEXT] == 1                                      # where it would have continued


def test_trigger_sources_are_narrowed_to_the_recorded_sequencer(tmp_path):
    (tmp_path / "a.q1asm").write_text("wait_trigger 1,4\nwait 20\nstop\n", encoding="utf-8")
    (tmp_path / "b.q1asm").write_text("wait 8\nstop\n", encoding="utf-8")
    path = tmp_path / "q1timeline.yml"
    path.write_text("external_inputs:\n  triggers:\n    - {id: start, address: 1, targets: [a, b], times_ns: [100]}\n"
                    "sequencers:\n  - {id: a, file: a.q1asm}\n  - {id: b, file: b.q1asm}\n", encoding="utf-8")
    recorder = LaneRecorder(path)
    first = recorder.open()
    assert [item["id"] for item in first["sequencers"]] == ["a", "b"] and first["sequencer"] == "a"
    assert first["inputs"]["triggers"] == 1 and first["runs"][BASE_RUN]["status"] == "completed"
    assert first["runs"][BASE_RUN]["steps"][0][T1] >= 100
    second = recorder.open("b")
    assert second["sequencer"] == "b" and second["sourceFile"].endswith("b.q1asm")
    with pytest.raises(ValueError, match="Unknown sequencer: c"):
        recorder.open("c")


def test_wait_without_a_trigger_source_reports_why_the_record_is_partial(tmp_path):
    base = LaneRecorder(project(tmp_path, "wait 40\nwait_trigger 1,4\nstop\n")).open(feedback=[0])["runs"][BASE_RUN]
    assert (base["status"], base["truncated"], len(base["steps"])) == ("waiting", True, 1)
    assert "No connected trigger source for address 1" in base["reason"] and "sequencer(s) waiting" not in base["reason"]


def test_step_limit_marks_the_record_partial_and_run_without_steps_is_an_error(tmp_path):
    path = project(tmp_path, LOOP)
    base = LaneRecorder(path, max_steps=6).open(feedback=[7])["runs"][BASE_RUN]
    assert (base["status"], base["truncated"], len(base["steps"])) == ("step_limit", True, 6)
    assert base["reason"] == "Stopped after 6 instructions (step limit)."
    assert base["steps"][-1][NEXT] == 6 and base["rtEnd"] == base["steps"][-1][T1]
    with pytest.raises(ValueError, match="No instruction was executed"):
        LaneRecorder(project(tmp_path, "wait_trigger 1,4\nstop\n")).open()
    with pytest.raises(ValueError, match="max_steps"):
        LaneRecorder(path, max_steps=0)


def test_single_q1asm_file_opens_without_a_project(tmp_path):
    source = tmp_path / "alone.q1asm"
    source.write_text(ONCE, encoding="utf-8")
    data = LaneRecorder(source).open(feedback=[0])
    assert data["sourceFile"] == str(source) and data["sequencers"][0]["file"] == str(source)
    assert data["runs"][BASE_RUN]["steps"][3][BRANCH][TAKEN] is True


@pytest.mark.parametrize("value", [1, 0])
def test_recording_one_sequencer_matches_its_execution_in_the_whole_project(value):
    data = LaneRecorder(RESET).open("reset_probe", [value])
    session = PlaybackSession.from_project(RESET)
    session.feedback_choices["reset_probe:feedback:2"] = {"value": value, "delay_ns": 0, "source": "user"}
    while session.run(budget_ms=1000, max_steps=1000)["can_resume"]:
        pass
    runner = next(item for item in session.runners if item.state.sequencer_id == "reset_probe")
    issues = [event for event in runner.state.events if event.kind == "q1_issue"]
    expected = [[step.source_pc, issue.t0.value, issue.t1.value, step.rt_start.value, step.rt_end.value]
                for step, issue in zip(runner.trace, issues, strict=True)]
    assert [step[:5] for step in data["runs"][BASE_RUN]["steps"]] == expected
    assert isinstance(runner.state.rt_time_ns, Concrete) and data["runs"][BASE_RUN]["rtEnd"] == runner.state.rt_time_ns.value


def test_golden_section_flips_only_differ_after_their_forced_branch():
    recorder = LaneRecorder(GOLDEN)
    data = recorder.open(feedback=[2_000_000, 1_000_000, 4_000_000, 1_000_000])
    base = data["runs"][BASE_RUN]
    steps = base["steps"]
    assert (len(steps), base["status"], base["feedbackCount"]) == (2113, "completed", 52)
    compare_line = next(item["line"] for item in data["instructions"] if item["text"] == "cmp R16, R17")
    decisions = [index for index, step in enumerate(steps)
                 if step[BRANCH] and step[BRANCH][PRODUCER] is not None and line_of(data, steps[step[BRANCH][PRODUCER]]) == compare_line]
    assert len(decisions) == 13 and all(steps[index][BRANCH][TAKEN] for index in decisions)
    out = recorder.flips([{"run": BASE_RUN, "step": decisions[0]}, {"run": BASE_RUN, "step": decisions[12]}], budget_ms=5000)
    assert out["failed"] == [] and out["pending"] == []
    for index in (decisions[0], decisions[12]):
        flip = out["runs"][f"base~{index}"]
        full = steps_of({**data["runs"], **out["runs"]}, f"base~{index}")
        assert flip["shared"] == index and full[index][BRANCH][:3] == [False, True, True]
        assert [step[T0] for step in full if step[T1] > step[T0]] == [step[T0] for step in steps if step[T1] > step[T0]]
        assert flip["rtEnd"] == base["rtEnd"] and flip["registers"] != base["registers"]


def test_flip_requests_are_validated_and_served_within_the_budget(tmp_path):
    recorder = LaneRecorder(project(tmp_path, LOOP))
    with pytest.raises(ValueError, match="Open a sequencer"):
        recorder.flips([])
    data = recorder.open(feedback=[7, 2])
    base = data["runs"][BASE_RUN]["steps"]
    branches = [index for index, step in enumerate(base) if data["instructions"][step[PC]]["op"] == "jge"]
    out = recorder.flips([{"run": BASE_RUN, "step": 0}, {"run": "missing", "step": branches[0]}])
    assert out["runs"] == {} and [item["reason"] for item in out["failed"]] == ["Not a recorded conditional branch."] * 2
    with pytest.raises(ValueError, match="run id and a step index"):
        recorder.flips([{"run": BASE_RUN, "step": "3"}])
    with pytest.raises(ValueError, match="budget_ms"):
        recorder.flips([], budget_ms=0)
    requests = [{"run": BASE_RUN, "step": index} for index in branches]
    served: dict[str, dict] = {}
    calls = 0
    while requests:
        out = recorder.flips(requests, budget_ms=1)
        assert out["runs"] and not out["failed"]                             # at least one per call
        served.update(out["runs"])
        requests = out["pending"]
        calls += 1
    assert sorted(served) == sorted(f"base~{index}" for index in branches) and calls <= 3


@pytest.mark.parametrize("values", [[], "1", [1.5], [True], [1 << 32], [-(1 << 31) - 1], list(range(4097))])
def test_feedback_values_are_validated(values):
    with pytest.raises(ValueError):
        validate_feedback(values)


def test_feedback_accepts_signed_and_unsigned_words():
    assert validate_feedback([-(1 << 31), 0xFFFFFFFF]) == [-(1 << 31), 0xFFFFFFFF]
    assert validate_feedback(None) is None


def test_worker_answers_json_lines_and_keeps_going_after_an_error(tmp_path):
    path = project(tmp_path, LOOP)
    lines = [{"id": 1, "command": "flips", "requests": []},
             {"id": 2, "command": "open", "sequencer": None, "feedback": [7, 2]},
             {"id": 3, "command": "flips", "requests": [{"run": "base", "step": 7}], "budget_ms": 1000},
             {"id": 4, "command": "open", "sequencer": "nope"},
             {"id": 5, "command": "explode"}]
    result = subprocess.run([sys.executable, "-m", "q1timeline.decision_lanes", "--project", str(path)],
                            input="".join(json.dumps(item) + "\n" for item in lines), capture_output=True, text=True, check=True)
    first, opened, flipped, unknown_sequencer, unknown_command = map(json.loads, result.stdout.splitlines())
    assert first == {"id": 1, "error": "Open a sequencer before requesting flips."}
    assert opened["id"] == 2 and opened["lanes"]["runs"]["base"]["registers"][2] == 21
    assert flipped["id"] == 3 and list(flipped["runs"]) == ["base~7"] and flipped["runs"]["base~7"]["registers"][2] == 12
    assert unknown_sequencer == {"id": 4, "error": "Unknown sequencer: nope"}
    assert unknown_command == {"id": 5, "error": "Unknown decision lanes command: explode"}


def test_once_prints_a_single_record_and_reports_a_missing_project(tmp_path):
    path = project(tmp_path, ONCE)
    cmd = [sys.executable, "-m", "q1timeline.decision_lanes", "--once", "--feedback", "0x1, 0"]
    done = subprocess.run([*cmd, "--project", str(path)], capture_output=True, text=True, check=True)
    assert json.loads(done.stdout)["inputs"]["feedback"] == [1, 0]
    missing = subprocess.run([*cmd, "--project", str(tmp_path / "missing.yml")], capture_output=True, text=True)
    assert missing.returncode == 2 and "does not exist" in json.loads(missing.stdout)["error"]


def test_info_lists_sequencers_without_executing_and_a_failed_record_leaves_nothing_open(tmp_path):
    (tmp_path / "a.q1asm").write_text("wait_trigger 1,4\nstop\n", encoding="utf-8")
    (tmp_path / "b.q1asm").write_text(ONCE.replace("16,$RESULT", "9,$RESULT"), encoding="utf-8")
    path = tmp_path / "q1timeline.yml"
    path.write_text("external_inputs:\n  feedback:\n    - {target: b, arrivals: [{time_ns: 60, id: 9, value: 7}]}\n"
                    "sequencers:\n  - {id: a, file: a.q1asm}\n  - {id: b, name: Receiver, file: b.q1asm}\n", encoding="utf-8")
    recorder = LaneRecorder(path)
    assert recorder.info() == {"project": str(path.resolve()), "sequencers": [
        {"id": "a", "name": "a", "file": str(tmp_path / "a.q1asm"), "scheduled": False},
        {"id": "b", "name": "Receiver", "file": str(tmp_path / "b.q1asm"), "scheduled": True}]}
    recorder.open("b")
    with pytest.raises(ValueError, match="No instruction was executed"):
        recorder.open("a")
    with pytest.raises(ValueError, match="Open a sequencer"):
        recorder.flips([{"run": BASE_RUN, "step": 3}])


# ---- records the extension's unit tests read ---------------------------------------------------

FIXTURES = Path(__file__).resolve().parents[1] / "vscode-extension" / "test" / "fixtures" / "decision-lanes"
FIXTURE_INPUTS = {"chain": [1], "flags": [6], "loop": [7, 2], "once": [1], "shaping": [-150, 30, 250]}


def extension_fixture(name: str) -> dict:
    """The base run of one fixture program and a flip of every input-dependent branch occurrence."""
    source = FIXTURES / f"{name}.q1asm"
    recorder = LaneRecorder(source)
    data = recorder.open(feedback=FIXTURE_INPUTS[name])
    reaches: list[bool] = []
    requests = []
    for index, step in enumerate(data["runs"][BASE_RUN]["steps"]):
        op = data["instructions"][step[PC]]["op"]
        reaches.append(op in ("fb_pop_data", "fb_pull_data") or any(writer is not None and reaches[writer] for _, _, writer in step[READS]))
        branch = step[BRANCH]
        if branch and op != "jmp" and branch[PRODUCER] is not None and reaches[branch[PRODUCER]]:
            requests.append({"run": BASE_RUN, "step": index})
    out = recorder.flips(requests, budget_ms=5000)
    assert not out["failed"] and not out["pending"]
    data["runs"].update(out["runs"])
    data["scenarios"][0]["flips"] = list(out["runs"])
    # Paths differ per machine; the extension tests only need the file name.
    data["project"] = data["sourceFile"] = source.name
    for item in data["sequencers"]:
        item["file"] = source.name
    return data


@pytest.mark.parametrize("name", sorted(FIXTURE_INPUTS))
def test_extension_fixtures_are_what_the_recorder_produces(name):
    path = FIXTURES / f"{name}.json"
    text = json.dumps(extension_fixture(name), separators=(",", ":")) + "\n"
    if os.environ.get("Q1LENS_UPDATE_FIXTURES"):
        path.write_text(text, encoding="utf-8", newline="\n")
    assert path.read_text(encoding="utf-8") == text, "Regenerate with Q1LENS_UPDATE_FIXTURES=1"
