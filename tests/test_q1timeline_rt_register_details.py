from __future__ import annotations

import pytest

from q1timeline.analysis.api import analyze_text
from q1timeline.analysis.interpreter import interpret_program
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.playback import PlaybackSession
from q1timeline.q1asm.parser import parse_q1asm


def _ir(source: str, mode: str, **kwargs) -> dict:
    if mode != "playback":
        return analyze_text(source, execution_mode=mode, **kwargs)
    session = PlaybackSession({"seq0": ("registers.q1asm", source)}, **kwargs)
    while session.run(budget_ms=1000, max_steps=1000)["can_resume"]:
        pass
    return session.snapshot()


@pytest.mark.parametrize("mode", ["analysis", "concrete", "playback", "simulation"])
def test_rt_issue_snapshots_keep_unused_registers_and_values_at_operand_read(mode: str) -> None:
    ir = _ir(
        "move 4,R0\nmove 7,R1\nnop\nwait R0\nmove 8,R1\nnop\n"
        "wait R1\nwait R1\nmove 99,R1\nnop\nstop",
        mode,
    )
    waits = [event for event in ir["events"] if event["kind"] == "q1_issue" and event["meta"]["op"] == "wait"]
    assert len(waits) == 3
    first, second, third = [event["meta"] for event in waits]
    assert set(first["register_reads"]) == {"R0"}
    assert first["register_reads"]["R0"]["value"] == 4
    assert first["register_snapshot"]["R1"]["value"] == 7
    assert second["register_reads"]["R1"]["value"] == 8
    assert second["register_snapshot"]["R1"] == third["register_snapshot"]["R1"]
    # Later writes cannot mutate an earlier command's captured value, even when
    # simulation queues that command long before RT executes it.
    assert third["register_reads"]["R1"]["value"] == 8


@pytest.mark.parametrize("mode", ["analysis", "concrete", "playback", "simulation"])
def test_issue_metadata_is_captured_before_register_effects(mode: str) -> None:
    ir = _ir("move 4,R0\nnop\nadd R0,4,R0\nnop\nwait R0\nstop", mode)
    issues = {event["meta"]["op"]: event["meta"] for event in ir["events"] if event["kind"] == "q1_issue"}
    assert issues["add"]["register_reads"]["R0"]["value"] == 4
    assert issues["add"]["register_snapshot"]["R0"]["value"] == 4
    assert issues["wait"]["register_snapshot"]["R0"]["value"] == 8


@pytest.mark.parametrize("mode", ["analysis", "playback"])
def test_unknown_and_symbolic_register_values_stay_nonconcrete(mode: str) -> None:
    unknown = _ir("play R9,R10,4\nstop", mode)
    play = next(event for event in unknown["events"] if event["kind"] == "q1_issue" and event["meta"]["op"] == "play")
    assert play["meta"]["register_reads"]["R9"]["kind"] == "unknown"
    assert play["meta"]["register_snapshot"]["R9"]["kind"] == "unknown"
    symbolic = _ir("move $frequency,R0\nnop\nset_freq R0\nupd_param 4\nstop", mode,
                   params={"frequency": "external_frequency"})
    update = next(event for event in symbolic["events"] if event["kind"] == "q1_issue" and event["meta"]["op"] == "upd_param")
    assert update["meta"]["register_snapshot"]["R0"]["kind"] == "symbolic"
    assert update["meta"]["register_snapshot"]["R0"]["expr"] == "external_frequency"


def test_runtime_dependent_values_are_kept_in_static_issue_metadata() -> None:
    ir = _ir("fb_pop_data 0,R0\nnop\nplay R0,R0,4\nstop", "analysis")
    play = next(event for event in ir["events"] if event["kind"] == "q1_issue" and event["meta"]["op"] == "play")
    assert play["meta"]["register_reads"]["R0"]["kind"] == "runtime_dependent"
    assert play["meta"]["register_reads"]["R0"] == play["meta"]["register_snapshot"]["R0"]


def test_branch_comparison_register_snapshots_belong_to_each_path() -> None:
    ir = _ir(
        "move 4,R0\nmove 7,R1\nnop\njlt R9,1,@jump\nmove 8,R1\nnop\njump: wait R0\nstop",
        "analysis", branch_policy="explore_both_with_depth_limit",
    )
    waits = {
        event["meta"]["branch_comparison_path"]: event["meta"]
        for event in ir["events"] if event["kind"] == "q1_issue" and event["meta"]["op"] == "wait"
    }
    assert waits["taken"]["register_snapshot"]["R1"]["value"] == 7
    assert waits["fallthrough"]["register_snapshot"]["R1"]["value"] == 8


def test_static_loop_preview_does_not_reuse_first_iteration_changing_values() -> None:
    source = "move 2,R0\nmove 1,R2\nnop\nbody: play R0,R2,4\nloop R0,@body\nstop"
    state = interpret_program(parse_q1asm(source, file="registers.q1asm"), sequencer_id="seq0",
                              loop_preview_counts={"seq0:loop:registers.q1asm:4:5": 2})
    ir = timeline_ir_from_states([state])
    plays = [event["meta"] for event in ir["events"] if event["kind"] == "q1_issue" and event["meta"]["op"] == "play"]
    assert plays[0]["register_reads"]["R0"]["value"] == 2
    assert plays[1]["register_reads"]["R0"]["kind"] == "unknown"
    assert plays[1]["register_snapshot"]["R0"]["kind"] == "unknown"
    assert plays[1]["register_reads"]["R2"]["value"] == 1


def test_simulation_distinguishes_updated_fields_from_retained_snapshot() -> None:
    ir = _ir("set_ph_delta 100\nupd_param 4\nupd_param 4\nstop", "simulation")
    updates = [event["meta"] for event in ir["events"] if event["kind"] == "upd_param"]
    assert all(update["applied_state_mode"] == "snapshot" for update in updates)
    assert updates[0]["applied_updated_fields"] == ["phase_delta"]
    assert updates[1]["applied_updated_fields"] == []


def test_compact_loop_marks_changed_latched_fields_uncertain_after_hidden_iterations() -> None:
    source = "move 2,R0\nnop\nbody: set_freq R0\nupd_param 4\nloop R0,@body\nplay 0,1,4\nstop"
    state = interpret_program(parse_q1asm(source, file="registers.q1asm"), sequencer_id="seq0")
    loop = next(event for event in state.events if event.kind == "loop_block")
    assert loop.meta["uncertain_latched_fields"] == ["frequency"]
    assert loop.meta["uncertain_registers"] == ["R0"]


def test_compact_loop_pending_setter_does_not_apply_first_iteration_value_after_loop() -> None:
    source = "move 2,R0\nnop\nbody: set_freq R0\nwait 4\nloop R0,@body\nupd_param 4\nstop"
    state = interpret_program(parse_q1asm(source, file="registers.q1asm"), sequencer_id="seq0")
    loop = next(event for event in state.events if event.kind == "loop_block")
    assert loop.meta["uncertain_latched_fields"] == []  # The loop never applies it.
    update = next(event for event in state.events if event.kind == "upd_param")
    assert update.meta["applied_state"]["frequency"]["kind"] == "unknown"


def test_compact_loop_last_constant_setter_keeps_its_known_value() -> None:
    source = "move 2,R0\nnop\nbody: set_freq R0\nset_freq 4\nupd_param 4\nloop R0,@body\nstop"
    state = interpret_program(parse_q1asm(source, file="registers.q1asm"), sequencer_id="seq0")
    loop = next(event for event in state.events if event.kind == "loop_block")
    assert loop.meta["uncertain_latched_fields"] == []


@pytest.mark.parametrize("mode", ["analysis", "concrete", "playback", "simulation"])
def test_register_aliases_use_canonical_names_in_issue_metadata(mode: str) -> None:
    ir = _ir(".DEF duration R3\nmove 8,R3\nnop\nwait $duration\nstop", mode)
    wait = next(event for event in ir["events"] if event["kind"] == "q1_issue" and event["meta"]["op"] == "wait")
    assert set(wait["meta"]["register_reads"]) == {"R3"}
    assert wait["meta"]["register_snapshot"]["R3"]["value"] == 8


@pytest.mark.parametrize("mode", ["analysis", "playback"])
@pytest.mark.parametrize(
    ("instruction", "params", "kind", "indexes"),
    [
        ("play R9,R10,16", {}, "play", [{"path": 0, "waveform_index": None}, {"path": 1, "waveform_index": None}]),
        ("play $wave0,$wave1,16", {"wave0": "external_wave0", "wave1": "external_wave1"},
         "play", [{"path": 0, "waveform_index": None}, {"path": 1, "waveform_index": None}]),
        ("acquire 0,R9,16", {}, "acquire", [{"acquisition_index": 0, "bin_index": None}]),
    ],
)
def test_unresolved_rt_indexes_preserve_timed_blocks_and_applied_values(
    mode: str, instruction: str, params: dict, kind: str, indexes: list[dict],
) -> None:
    ir = _ir(
        f"set_freq 100\nplay 0,1,16\nset_freq 200\n{instruction}\nwait 16\nstop",
        mode, params=params,
    )
    assert not [diagnostic for diagnostic in ir["diagnostics"] if diagnostic["severity"] == "error"]
    blocks = [event for event in ir["events"] if event["kind"] == kind and event["source"]["line"] == 4]
    assert len(blocks) == len(indexes)
    issue = next(event for event in ir["events"] if event["kind"] == "q1_issue" and event["source"]["line"] == 4)
    for block, expected in zip(blocks, indexes):
        assert {name: block["meta"][name] for name in expected} == expected
        assert block["t0"]["value"] == 16
        assert block["t1"]["value"] == 32
        assert block["duration"]["value"] == 16
        assert block["meta"]["rt_packet_id"] == issue["meta"]["rt_packet_id"]
        assert block["meta"]["q1_issue_event_id"] == issue["id"]
        assert block["meta"]["applied_state"] == {"frequency": 200}
        setter = next(event for event in ir["events"] if event["id"] == block["meta"]["applied_setter_event_ids"]["frequency"])
        assert setter["source"]["line"] == 3
        assert "None" not in block["label"]
