from copy import deepcopy
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from q1timeline.analysis.values import Concrete
from q1timeline.playback import PlaybackSession
from q1timeline.external_inputs import validate_external_inputs
from q1timeline.project import ConfigLoadError, load_project_config


def inputs(*times, targets=("a", "b"), address=1):
    return {"triggers": [{"id": "shot_start", "address": address,
                          "targets": list(targets), "times_ns": list(times)}]}


def run(session):
    for _ in range(100):
        if not session.run(budget_ms=1000, max_steps=1000)["can_resume"]:
            return session.snapshot()
    pytest.fail("Playback did not reach a bounded outcome")


def test_shared_event_is_broadcast_with_independent_post_trigger_durations():
    session = PlaybackSession({"a": ("a.q1asm", "wait_trigger 1,4\nstop"),
                               "b": ("b.q1asm", "wait 20\nwait_trigger 1,12\nstop")},
                              external_inputs=inputs(100))
    ir = run(session)
    waits = [event for event in ir["events"] if event["kind"] == "wait_trigger"]
    assert ir["playback"]["status"] == "completed"
    assert {event["meta"]["trigger_assumption"]["event_id"] for event in waits} == {"external:shot_start:0"}
    assert [runner.state.rt_time_ns for runner in session.runners] == [Concrete(104), Concrete(112)]
    assert all(event["confidence"] == "assumed" for event in waits)
    assert all(event["meta"]["trigger_assumption"]["arrival_ns"] == 100 for event in waits)


def test_waiting_peer_does_not_stop_independent_progress_and_no_timeout_is_invented():
    session = PlaybackSession({"a": ("a.q1asm", "wait_trigger 1,4\nstop"),
                               "b": ("b.q1asm", "wait 1000\nstop")})
    ir = run(session)
    assert ir["playback"]["status"] == "waiting"
    assert ir["playback"]["can_resume"] is False
    assert session.runners[1].state.stopped
    assert not session.runners[0].state.stopped
    wait = next(event for event in ir["events"] if event["kind"] == "wait_trigger")
    assert wait["meta"]["trigger_assumption"]["status"] == "waiting"
    assert "address 1" in ir["playback"]["reason"]


@pytest.mark.parametrize("arrival,expected", [(19, "waiting"), (20, "completed"), (21, "completed")])
def test_arrival_boundaries_use_raw_rt_clock(arrival, expected):
    session = PlaybackSession({"a": ("a.q1asm", "wait 20\nwait_trigger 1,4\nstop")},
                              external_inputs=inputs(arrival, targets=("a",)),
                              alignment_mode="first_wait_trigger")
    assert run(session)["playback"]["status"] == expected
    if expected == "completed":
        assert session.runners[0].state.rt_time_ns == Concrete(arrival + 4)


def test_event_cannot_release_second_wait_and_missing_address_stays_waiting():
    session = PlaybackSession({"a": ("a.q1asm", "wait_trigger 1,4\nwait_trigger 1,4\nstop"),
                               "b": ("b.q1asm", "wait_trigger 2,4\nstop")},
                              external_inputs=inputs(100))
    assert run(session)["playback"]["status"] == "waiting"
    assert session.runners[0].state.pc == 1
    assert session.runners[1].state.pc == 0
    session.set_external_inputs(inputs(100, 200))
    assert run(session)["playback"]["status"] == "waiting"
    assert session.runners[0].state.stopped


def test_reconfigure_is_atomic_and_reset_restores_saved_inputs():
    session = PlaybackSession({"a": ("a.q1asm", "wait_trigger 1,4\nstop")},
                              external_inputs=inputs(100, targets=("a",)))
    before = run(session)
    invalid = inputs(200, targets=("missing",))
    with pytest.raises(ValueError, match="unknown sequencer"):
        session.set_external_inputs(invalid)
    assert session.snapshot() == before
    session.set_external_inputs(inputs(200, targets=("a",)))
    assert run(session)["playback"]["external_inputs"]["overridden"]
    assert session.runners[0].state.rt_time_ns == Concrete(204)
    session.set_external_inputs(None)
    assert not run(session)["playback"]["external_inputs"]["overridden"]
    assert session.runners[0].state.rt_time_ns == Concrete(104)


def test_adaptive_demo_shared_start_and_reconfiguration():
    session = PlaybackSession.from_project(Path("examples/adaptive-array-demo/q1timeline.yml"))
    first = run(session)
    assert first["playback"]["status"] == "completed"
    waits = [event for event in first["events"] if event["kind"] == "wait_trigger"]
    assert len(waits) == 17
    assert len({event["meta"]["trigger_assumption"]["event_id"] for event in waits}) == 1
    assert {event["meta"]["trigger_assumption"]["arrival_ns"] for event in waits} == {1000}
    initial_times = {r.state.sequencer_id: r.state.rt_time_ns.value for r in session.runners}
    changed = deepcopy(session.external_inputs)
    changed["triggers"][0]["times_ns"] = [1500]
    session.set_external_inputs(changed)
    second = run(session)
    assert second["playback"]["status"] == "completed"
    assert all(r.state.rt_time_ns.value == initial_times[r.state.sequencer_id] + 500 for r in session.runners)
    session.set_external_inputs({"triggers": []})
    assert run(session)["playback"]["status"] == "waiting"
    session.set_external_inputs(None)
    assert run(session)["playback"]["status"] == "completed"


def test_address_parameter_override_changes_source_and_receivers_together():
    session = PlaybackSession.from_project("examples/adaptive-array-demo/q1timeline.yml", params={"TRIGGER_INDEX": 2})
    ir = run(session)
    assert ir["playback"]["status"] == "completed"
    assert ir["playback"]["external_inputs"]["triggers"][0]["resolved_address"] == 2
    assert {event["meta"]["trigger_assumption"]["address"] for event in ir["events"]
            if event["kind"] == "wait_trigger"} == {2}


@pytest.mark.parametrize("field,value", [
    ("id", ""), ("id", "x" * 129), ("address", True), ("address", 16),
    ("address", "{PARAM}"), ("targets", []), ("targets", ["a", "a"]),
    ("targets", ["missing"]), ("times_ns", [1, 1]), ("times_ns", [2, 1]),
    ("times_ns", [-1]), ("times_ns", [True]), ("times_ns", [1.5]),
    ("times_ns", [9007199254740992]),
])
def test_invalid_scenarios_are_rejected(field, value):
    config = inputs(100)
    config["triggers"][0][field] = value
    with pytest.raises(ValueError):
        validate_external_inputs(config, ["a", "b"])


def test_project_rejects_unknown_targets_with_configuration_diagnostic(tmp_path):
    (tmp_path / "a.q1asm").write_text("stop")
    project = tmp_path / "q1timeline.yml"
    project.write_text("sequencers: [{id: a, file: a.q1asm}]\nexternal_inputs:\n  triggers:\n"
                       "    - {id: start, address: 1, targets: [missing], times_ns: [100]}\n")
    with pytest.raises(ConfigLoadError) as error:
        load_project_config(project)
    assert error.value.diagnostics[0].category == "invalid_external_inputs"


def test_zero_duration_still_consumes_shared_occurrence_only_once_per_recipient():
    session = PlaybackSession({"a": ("a.q1asm", "wait_trigger 1,0\nwait_trigger 1,0\nstop")},
                              external_inputs=inputs(100, targets=("a",)))
    assert run(session)["playback"]["status"] == "waiting"
    assert session.runners[0].state.pc == 1
    assert session.runners[0].state.rt_time_ns == Concrete(100)


@pytest.mark.parametrize("source,status", [
    ("move 1,R0\nwait_trigger R0,R0\nstop", "error"),
    ("wait_trigger 16,4\nstop", "error"),
    ("wait_trigger 1,-4\nstop", "error"),
    ("wait_trigger R0,R1\nstop", "blocked"),
    ("move 17,R0\nmove 4,R1\nnop\nwait_trigger R0,R1\nstop", "blocked"),
])
def test_trigger_validation_precedes_scenario_waiting(source, status):
    session = PlaybackSession({"a": ("a.q1asm", source)}, external_inputs=inputs(100, targets=("a",)))
    ir = run(session)
    assert ir["playback"]["status"] == status
    assert not session.triggers


def test_reconfiguration_retains_feedback_and_chosen_branch_and_accept_sets_reset_base():
    source = "wait_trigger 1,4\nfb_pop_data 1,R0\nnop\njge R0,1,@done\nwait 20\ndone: stop"
    session = PlaybackSession({"a": ("a.q1asm", source)}, external_inputs=inputs(100, targets=("a",)))
    run(session)
    session.set_feedback(session.feedback[0]["feedback_id"], 2, 10)
    run(session)
    session.choose(session.branches[0]["branch_id"], "fallthrough")
    run(session)
    choices = deepcopy((session.feedback_choices, session.choices))
    session.set_external_inputs(inputs(200, targets=("a",)))
    ir = run(session)
    assert (session.feedback_choices, session.choices) == choices
    assert ir["playback"]["branches"][0]["path"] == "fallthrough"
    assert session.runners[0].state.rt_time_ns == Concrete(224)
    assert not session.accept_external_inputs()["playback"]["external_inputs"]["overridden"]
    session.set_external_inputs(inputs(300, targets=("a",)))
    run(session)
    session.set_external_inputs(None)
    run(session)
    assert session.runners[0].state.rt_time_ns == Concrete(224)


def test_adaptive_demo_worker_protocol_applies_rejects_and_accepts_shared_inputs():
    root = Path(__file__).resolve().parents[1]
    project = root / "examples/adaptive-array-demo/q1timeline.yml"
    config = load_project_config(project).external_inputs
    config["triggers"][0]["times_ns"] = [1500]
    commands = [
        {"command": "run", "budget_ms": 1000},
        {"command": "set_external_inputs", "external_inputs": config},
        {"command": "run", "budget_ms": 1000},
        {"command": "snapshot"},
        {"command": "set_external_inputs", "external_inputs": {"triggers": "invalid"}},
        {"command": "snapshot"},
        {"command": "accept_external_inputs"},
        {"command": "set_external_inputs", "external_inputs": None},
        {"command": "run", "budget_ms": 1000},
        {"command": "snapshot"},
    ]
    result = subprocess.run(
        [sys.executable, "-m", "q1timeline.playback", "--project", str(project)],
        input="".join(json.dumps({"id": index, **command}) + "\n" for index, command in enumerate(commands)),
        text=True, capture_output=True, timeout=15, cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")}, check=True,
    )
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert [reply["id"] for reply in replies] == list(range(len(commands)))
    assert replies[0]["playback"]["status"] == replies[2]["playback"]["status"] == "completed"
    assert "error" in replies[4]
    assert replies[3]["ir"] == replies[5]["ir"]
    assert not replies[6]["ir"]["playback"]["external_inputs"]["overridden"]
    final = replies[9]["ir"]["playback"]
    assert final["status"] == "completed"
    assert len(final["triggers"]) == 17
    assert {trigger["arrival_ns"] for trigger in final["triggers"]} == {1500}
