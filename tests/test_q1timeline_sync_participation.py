import json

import pytest

from q1timeline.analysis.interpreter import interpret_program, prepare_def_program
from q1timeline.analysis.queue_timing import replay_queues
from q1timeline.cli import main
from q1timeline.playback import PlaybackSession
from q1timeline.project import ConfigLoadError, load_project_config
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs
from q1timeline.watch.watcher import run_watch_once


def programs(**sources):
    return {name: parse_q1asm(source, file=f"{name}.q1asm") for name, source in sources.items()}


@pytest.mark.parametrize("sync_en", [None, True, False])
def test_simulation_barrier_waits_for_declared_participants_only(sync_en):
    configuration = {} if sync_en is None else {"b": {"sync_en": sync_en}}
    a, b = simulate_programs(programs(a="wait_sync 4\nwait 4\nstop", b="wait 12\nstop"),
                             simulation={"startup_delay_ns": 32}, hardware_parameters=configuration)

    assert b.metadata["execution"]["status"] == "completed"
    if sync_en is False:
        assert a.metadata["execution"]["status"] == "completed"
        assert a.metadata["execution"]["sync_participants"] == ["a"]
        assert a.rt_packets[1].rt_t0.value == 36
    else:
        assert a.metadata["execution"]["status"] == "blocked"
        assert a.metadata["execution"]["sync_participants"] == ["a", "b"]
        assert a.metadata["execution"]["reason"] == "No matching wait_sync barrier for: b."
        assert a.diagnostics[-1].source.line == 1


@pytest.mark.parametrize("prepared", [False, True])
def test_consecutive_barriers_with_zero_duration_and_different_arrivals(prepared):
    parsed = programs(a="wait_sync 0\nwait 8\nwait_sync 4\nwait 4\nstop",
                      b="wait 12\nwait_sync 8\nwait 12\nwait_sync 0\nwait 4\nstop",
                      c="wait 40\nstop")
    bindings = {name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                for name, program in parsed.items()} if prepared else None
    states = simulate_programs(parsed, simulation={"startup_delay_ns": 40}, prepared_programs=bindings,
                               hardware_parameters={"a": {"sync_en": True}, "c": {"sync_en": False}})
    expected = {"a": [40, 56, 64, 76], "b": [40, 52, 60, 72, 76], "c": [40]}

    assert all(state.metadata["execution"]["status"] == "completed" for state in states)
    assert {state.sequencer_id: [packet.rt_t0.value for packet in state.rt_packets] for state in states} == expected
    streams = {name: interpret_program(program, sequencer_id=name).rt_packets for name, program in parsed.items()}
    replay = replay_queues(streams, sync_participants={"a", "b"})
    assert all(result.failure is None and result.incomplete_reason is None for result in replay.values())
    assert {name: [record.dequeue_ns for record in result.records] for name, result in replay.items()} == {
        name: [time - 36 for time in times] for name, times in expected.items()
    }


def test_failed_participant_keeps_barrier_pending_without_stopping_independent_peer():
    a, b, c = simulate_programs(programs(a="wait_sync 4\nstop", b="illegal", c="wait 8\nstop"),
                                hardware_parameters={"c": {"sync_en": False}})

    assert [state.metadata["execution"]["status"] for state in (a, b, c)] == ["blocked", "error", "completed"]
    assert a.metadata["execution"]["reason"] == "No matching wait_sync barrier for: b."


def project(tmp_path, b_source="wait 8\nstop", sync_en="false"):
    (tmp_path / "a.q1asm").write_text("wait_sync 4\nwait 4\nstop\n", encoding="utf-8")
    (tmp_path / "b.q1asm").write_text(b_source, encoding="utf-8")
    path = tmp_path / "q1timeline.yml"
    path.write_text(
        "analysis:\n  execution_mode: simulation\nalignment:\n  mode: none\nsequencers:\n"
        "  - id: a\n    file: a.q1asm\n  - id: b\n    file: b.q1asm\n"
        f"    hardware_parameters:\n      sync_en: {sync_en}\n", encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("value", ["0", "1", "null", "'false'", "[]"])
def test_project_sync_en_requires_a_boolean(tmp_path, value):
    with pytest.raises(ConfigLoadError, match="configuration contains errors"):
        load_project_config(project(tmp_path, sync_en=value))


def test_cli_watch_and_play_queue_checks_use_the_same_membership(tmp_path):
    path = project(tmp_path)
    assert load_project_config(path).sequencers[1].hardware_parameters == {"sync_en": False}
    output = tmp_path / "timeline.json"
    assert main(["analyze", "--project", str(path), "--out", str(output), "--no-render"]) == 0
    simulation = json.loads(output.read_text(encoding="utf-8"))
    assert simulation["execution"]["a"]["sync_participants"] == ["a"]
    assert simulation["execution"]["a"]["status"] == "completed"
    assert main(["analyze", "--project", str(path), "--execution-mode", "analysis",
                 "--out", str(output), "--no-render"]) == 0
    analytical = json.loads(output.read_text(encoding="utf-8"))
    assert analytical["assumptions"]["underflow"]["sync_participants"] == ["a"]
    assert not [event for event in analytical["events"] if event["kind"] == "analysis_incomplete"]
    watched = run_watch_once(path, tmp_path / "watch")
    assert watched.ok
    assert json.loads(watched.timeline_ir.read_text(encoding="utf-8"))["execution"]["a"]["sync_participants"] == ["a"]
    play = PlaybackSession.from_project(path)
    play.run(max_steps=100)
    timing = play.snapshot()["execution"]["a"]["queue_timing"]
    assert timing["assumptions"]["sync_participants"] == ["a"]
    assert timing["status"] == "not_detected_in_recorded_packets"

    play = PlaybackSession.from_project(project(tmp_path, sync_en="true"))
    play.run(max_steps=100)
    timing = play.snapshot()["execution"]["a"]["queue_timing"]
    assert timing["assumptions"]["sync_participants"] == ["a", "b"]
    assert timing["status"] == "analysis_incomplete"
    assert any("no matching reachable barrier for: b" in reason for reason in timing["reasons"])


def test_disabled_wait_sync_reports_its_source_in_simulation_and_queue_checks(tmp_path):
    path = project(tmp_path, "wait 4\nwait_sync 4\nstop\n")
    _, b = simulate_programs(programs(a="wait_sync 4\nstop", b="wait 4\nwait_sync 4\nstop"),
                             hardware_parameters={"b": {"sync_en": False}})
    assert b.metadata["execution"]["status"] == "blocked"
    assert b.diagnostics[-1].source.line == 2
    assert "sync_en disabled" in b.diagnostics[-1].message
    output = tmp_path / "timeline.json"
    assert main(["analyze", "--project", str(path), "--execution-mode", "analysis",
                 "--out", str(output), "--no-render"]) == 0
    analytical = json.loads(output.read_text(encoding="utf-8"))
    assert any(item["source"]["line"] == 2 and "sync_en disabled" in item.get("details", {}).get("reason", "")
               for item in analytical["diagnostics"] if item.get("source"))
    play = PlaybackSession.from_project(path)
    play.run(max_steps=100)
    assert any(item["source"]["line"] == 2 and "sync_en disabled" in item["message"]
               for item in play.snapshot()["diagnostics"] if item.get("source"))
