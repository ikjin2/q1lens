import pytest

from q1timeline.playback import PlaybackSession
from q1timeline.project import ConfigLoadError, load_project_config
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


def inputs(times, *, targets=("a",)):
    return {"triggers": [{"id": "scenario", "address": 1, "targets": list(targets), "times_ns": list(times)}]}


def play(source, times=None, settings=None):
    documents = {"a": source} if isinstance(source, str) else source
    session = PlaybackSession(
        {name: (f"{name}.q1asm", text) for name, text in documents.items()},
        external_inputs=None if times is None else inputs(times, targets=documents),
        hardware_parameters=settings,
    )
    assert not session.run(budget_ms=1000, max_steps=1000)["can_resume"]
    return session


def simulate(source, times, settings=None):
    states = simulate_programs({"a": parse_q1asm(source)}, hardware_parameters=settings,
                               simulation={"startup_delay_ns": 24, "inputs": {"triggers": [
                                   {"target": "a", "time_ns": time + 24, "address": 1} for time in times
                               ]}})
    assert states[0].metadata["execution"]["status"] == "completed"
    return states[0]


@pytest.mark.parametrize("times,threshold,invert,execute", [
    ([], 2, False, False), ([12], 2, False, False), ([5, 12], 2, False, True),
    ([5, 12], 2, True, False), ([12], 2, True, True), ([], 0, False, True),
])
def test_play_and_simulation_agree_on_threshold_and_boundary(times, threshold, invert, execute):
    source = "set_latch_en 1,4\nwait 8\nset_cond 1,1,0,12\nupd_param 4\nstop"
    settings = {"a": {"trigger1_count_threshold": threshold, "trigger1_threshold_invert": invert}}
    playback = play(source, times, settings)
    assert playback.status == "completed"
    for state in (playback.runners[0].state, simulate(source, times, settings)):
        packet = state.rt_packets[-1]
        assert packet.meta.get("conditional_skipped", False) is not execute
        assert packet.duration.value == (4 if execute else 12)
    decision = playback.runners[0].state.rt_packets[-1].meta["trigger_counter_assumption"]
    assert decision["time_ns"] == 12 and decision["taken"] is execute
    assert decision["counts"] == {"1": len(times)}
    assert decision["input_event_ids"] == [f"external:scenario:{index}" for index in range(len(times))]
    sources = playback.snapshot()["playback"]["external_inputs"]["triggers"]
    schedule = {f"external:{source['id']}:{index}": time
                for source in sources for index, time in enumerate(source["times_ns"])}
    assert [schedule[event_id] for event_id in decision["input_event_ids"]] == times


def test_disable_retains_the_count_and_reset_removes_it():
    source = ("set_latch_en 1,4\nwait 4\nset_latch_en 0,4\nwait 4\nset_cond 1,1,0,12\nupd_param 4\n"
              "set_cond 0,0,0,4\nlatch_rst 4\nset_cond 1,1,0,12\nupd_param 4\nstop")
    playback = play(source, [8, 13])
    for state in (playback.runners[0].state, simulate(source, [8, 13])):
        assert [packet.meta.get("conditional_skipped", False) for packet in state.rt_packets
                if packet.op == "upd_param"] == [False, True]


def test_play_blocks_only_when_missing_inputs_are_needed():
    source = "set_latch_en 1,4\nwait 8\nset_cond 1,1,0,12\nupd_param 4\nstop"
    missing = play(source)
    assert missing.status == "blocked" and "trigger-counter inputs are incomplete" in missing.reason
    assert missing.steps == 3
    assert play(source, settings={"a": {"trigger1_count_threshold": 65535}}).status == "blocked"
    assert play(source, []).status == "completed"
    assert play(source.replace("set_latch_en 1", "set_latch_en 0")).status == "completed"
    assert play(source.replace("1,1,0,12", "1,3,0,12"), [8]).status == "completed"
    assert play(source, settings={"a": {"trigger1_count_threshold": 0}}).status == "completed"


def test_skipped_waits_and_latch_effects_use_only_the_else_duration():
    source = ("set_cond 1,0,0,4\nset_latch_en 1,12\nwait_sync 12\nwait_trigger 1,12\n"
              "set_cond 1,1,0,4\nupd_param 12\nstop")
    playback = play(source, [4])
    assert playback.status == "completed"
    assert [packet.duration.value for packet in playback.runners[0].state.rt_packets] == [4, 4, 4, 4]
    assert all(packet.meta["conditional_skipped"] for packet in playback.runners[0].state.rt_packets)
    assert playback.snapshot()["execution"]["a"]["queue_timing"]["status"] == "not_detected_in_recorded_packets"


def test_skipped_update_keeps_capture_separate_from_applied_state():
    source = ("set_mrk 1\nupd_param 4\nset_mrk 3\nset_cond 1,0,0,12\nupd_param 4\n"
              "set_cond 0,0,0,4\nupd_param 4\nstop")
    playback = play(source)
    for state in (playback.runners[0].state, simulate(source, [])):
        packets = [packet for packet in state.rt_packets if packet.op == "upd_param"]
        assert packets[1].meta["captured_state"]["marker"] == 3
        assert packets[1].meta["applied_state"] == {}
        accepted = [event for event in state.events if event.kind == "upd_param"][-1]
        assert accepted.meta["applied_state"]["marker"] == 3


def test_broadcast_arrival_counts_once_per_receiver_even_when_it_releases_wait_trigger():
    source = "set_latch_en 1,4\nwait_trigger 1,4\nset_cond 1,1,0,12\nupd_param 4\nstop"
    playback = play({"a": source, "b": source}, [12])
    assert playback.status == "completed"
    for runner in playback.runners:
        wait, update = runner.state.rt_packets[1:]
        assert wait.meta["trigger_assumption"]["event_id"] == "external:scenario:0"
        assert update.meta["trigger_counter_assumption"]["counts"] == {"1": 1}
        assert update.meta["trigger_counter_assumption"]["input_event_ids"] == ["external:scenario:0"]
        assert update.meta["trigger_counter_assumption"]["taken"] is True


def test_editing_external_inputs_replays_counter_history_from_scratch():
    source = "set_latch_en 1,4\nwait 8\nset_cond 1,1,0,12\nupd_param 4\nstop"
    playback = play(source, [5, 8], {"a": {"trigger1_count_threshold": 2}})
    assert playback.runners[0].state.rt_packets[-1].meta["trigger_counter_assumption"]["taken"] is True
    playback.set_external_inputs(inputs([8]))
    assert playback.run(budget_ms=1000, max_steps=1000)["status"] == "completed"
    decision = playback.runners[0].state.rt_packets[-1].meta["trigger_counter_assumption"]
    assert decision["taken"] is False and decision["counts"] == {"1": 1}


@pytest.mark.parametrize("other", [None, "wait 20\nwait_sync 4\nstop"])
def test_replay_rejects_trigger_decision_shifted_by_queue_timing(other):
    source = "set_latch_en 1,4\n{}\nset_cond 1,1,0,12\nupd_param 4\nstop".format(
        "wait 4" if other is None else "wait_sync 4",
    )
    playback = play(source if other is None else {"a": source, "b": other}, [10 if other is None else 20])
    assert playback.status == "completed"
    assert playback.runners[0].state.rt_packets[-1].meta["conditional_skipped"] is True
    timing = playback.snapshot()["execution"]["a"]["queue_timing"]
    assert timing["status"] == "analysis_incomplete"
    assert any("set_cond decision" in reason for reason in timing["reasons"])


@pytest.mark.parametrize("parameter,valid", [
    ("trigger1_count_threshold: 0", True), ("trigger15_count_threshold: 65535", True),
    ("trigger1_threshold_invert: true", True), ("trigger1_count_threshold: true", False),
    ("trigger1_count_threshold: 65536", False), ("trigger1_threshold_invert: 1", False),
    ("trigger16_count_threshold: 1", False),
])
def test_project_validates_hardware_trigger_settings(tmp_path, parameter, valid):
    (tmp_path / "a.q1asm").write_text("stop\n", encoding="utf-8")
    path = tmp_path / "q1timeline.yml"
    path.write_text(f"sequencers:\n  - id: a\n    file: a.q1asm\n    hardware_parameters:\n      {parameter}\n", encoding="utf-8")
    if valid:
        assert parameter.split(":")[0] in load_project_config(path).sequencers[0].hardware_parameters
    else:
        with pytest.raises(ConfigLoadError):
            load_project_config(path)
