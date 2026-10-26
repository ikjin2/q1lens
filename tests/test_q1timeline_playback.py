from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.values import Concrete, Unknown
from q1timeline.playback import PlaybackSession
from q1timeline.q1asm.parser import parse_q1asm


def session(source: str) -> PlaybackSession:
    return PlaybackSession({"seq0": ("playback.q1asm", source)})


def finish(playback: PlaybackSession) -> dict:
    while playback.run(budget_ms=1000, max_steps=1000)["can_resume"]:
        pass
    return playback.snapshot()


@pytest.mark.parametrize("edge", ["sub R31,1,R31\njnz @body", "loop R31,@body"])
def test_executed_loops_keep_concrete_timing_flags_and_register_history(edge: str) -> None:
    source = f"move 15,R31\nbody: wait 65535\n{edge}\nwait 16975\nstop"
    playback = session(source)
    ir = finish(playback)
    expected = interpret_program(parse_q1asm(source), sequencer_id="seq0", execution_mode="concrete")
    actual = playback.runners[0].state
    assert actual.q1_time_ns == expected.q1_time_ns
    assert actual.rt_time_ns == expected.rt_time_ns == Concrete(1_000_000)
    assert actual.registers == expected.registers == {"R31": Concrete(0)}
    assert actual.alu_flags == expected.alu_flags
    assert [(write.value, write.time) for write in actual.register_history] == [
        (write.value, write.time) for write in expected.register_history
    ]
    loop, = ir["playback"]["loops"]
    assert loop["count"] == 15 and loop["complete"]
    assert loop["first_iteration_end"]["value"] == 65535
    assert len([event for event in ir["events"] if event["kind"] == "wait"]) == 16
    assert len(loop["hidden_event_ids"]) > 0
    assert len({event["id"] for event in ir["events"]}) == len(ir["events"])


def test_loop_records_observed_first_iteration_end_when_durations_vary() -> None:
    playback = session("move 4,R0\nmove 3,R1\nnop\nbody: wait R0\nadd R0,4,R0\nloop R1,@body\nstop")
    ir = finish(playback)
    loop, = ir["playback"]["loops"]
    assert loop["count"] == 3 and loop["complete"]
    assert loop["period"]["kind"] == "unknown"
    assert loop["first_iteration_end"]["value"] == 4
    annotation = next(event for event in ir["events"] if event["kind"] == "loop_block")
    assert annotation["meta"]["first_iteration_end"] == loop["first_iteration_end"]
    assert annotation["t1"]["value"] == 24


def test_pause_resume_keeps_exact_prefix_and_partial_iteration_visible() -> None:
    playback = session("move 3,R0\nnop\nbody: wait 8\nloop R0,@body\nstop")
    assert playback.run(max_steps=5)["status"] == "paused"
    partial = playback.snapshot()
    loop, = partial["playback"]["loops"]
    assert loop["count"] == ">=1" and not loop["complete"]
    waits = [event for event in partial["events"] if event["kind"] == "wait"]
    assert len(waits) == 2
    assert waits[-1]["id"] not in loop["hidden_event_ids"]
    prior_ids = [event["id"] for event in partial["events"] if event["kind"] != "loop_block"]
    result = finish(playback)
    assert [event["id"] for event in result["events"][:len(prior_ids)]] == prior_ids
    assert result["playback"]["loops"][0]["count"] == 3
    assert playback.run()["status"] == "completed"  # No accidental restart from a queued chunk.


def test_unknown_branch_defaults_without_inventing_register_values_and_can_be_overridden() -> None:
    playback = session("add R0,1,R1\nnop\ncmp R1,0\njnz @jump\nwait 4\nstop\njump: wait 8\nstop")
    ir = finish(playback)
    branch, = ir["playback"]["branches"]
    assert branch["calculated_taken"] is None
    assert branch["path"] == "fallthrough" and branch["assumption_source"] == "default"
    assert playback.runners[0].state.rt_time_ns == Concrete(4)
    assert isinstance(playback.runners[0].state.registers["R1"], Unknown)
    issue = next(event for event in ir["events"] if event["id"] == branch["event_id"])
    assert issue["meta"]["branch_decision"] == "default"
    assert issue["confidence"] == "assumed"
    playback.choose(branch["branch_id"], "taken")
    ir = finish(playback)
    assert ir["playback"]["branches"][0]["assumption_source"] == "user"
    assert playback.runners[0].state.rt_time_ns == Concrete(8)


@pytest.mark.parametrize("prefix", ["cmp R0,0", "move 0,R0\nnop\ncmp R0,0"])
def test_untaken_conditional_does_not_require_its_unknown_destination(prefix: str) -> None:
    playback = session(prefix + "\njnz R7\nwait 4\nstop")
    ir = finish(playback)
    assert ir["playback"]["status"] == "completed"
    assert playback.runners[0].state.rt_time_ns == Concrete(4)
    branch, = ir["playback"]["branches"]
    assert branch["target_pc"] is None and branch["path"] == "fallthrough"
    playback.choose(branch["branch_id"], "taken")
    assert playback.run()["status"] == "blocked"
    assert "destination" in playback.reason


def test_known_branch_override_is_per_occurrence_and_discards_stale_suffix_choices() -> None:
    playback = session("move 3,R0\nnop\nbody: wait 8\nloop R0,@body\nstop")
    ir = finish(playback)
    first, second, third = ir["playback"]["branches"]
    assert len({first["branch_id"], second["branch_id"], third["branch_id"]}) == 3
    assert first["source_branch_id"] == second["source_branch_id"]
    rewound = playback.choose(second["branch_id"], "fallthrough")
    assert rewound["playback"]["steps"] == 0
    assert playback.runners[0].state.registers == {}
    ir = finish(playback)
    assert [item["path"] for item in ir["playback"]["branches"]] == ["taken", "fallthrough"]
    assert ir["playback"]["branches"][1]["calculated_taken"] is True
    assert playback.runners[0].state.registers["R0"] == Concrete(1)
    playback.choose(first["branch_id"], "fallthrough")
    assert second["branch_id"] not in playback.choices
    ir = finish(playback)
    assert len(ir["playback"]["branches"]) == 1
    assert ir["playback"]["loops"] == []  # No observed repetition.


def test_nested_loop_breaks_do_not_merge_separate_invocations() -> None:
    source = (
        "move 2,R1\nnop\nouter: move 2,R0\nnop\nbody: wait 4\nsub R0,1,R0\nnop\n"
        "jz @after\njmp @body\nafter: wait 100\nloop R1,@outer\nstop"
    )
    ir = finish(session(source))
    inner = [loop for loop in ir["playback"]["loops"] if loop["source_start"]["line"] == 5]
    assert len(inner) == 2
    by_id = {event["id"]: event for event in ir["events"]}
    for loop in inner:
        assert loop["complete"] and loop["count"] == 2
        assert all(5 <= by_id[event_id]["source"]["line"] <= 9 for event_id in loop["playback_event_ids"])


@pytest.mark.parametrize("prelude,reverse", [(False, False), (True, False), (False, True), (True, True)])
def test_outer_loop_includes_shared_out_of_line_routines_and_collapses_nested_invocations(prelude, reverse) -> None:
    prefix = "move @initdone,R27\njmp @measure\ninitdone:\n" if prelude else ""
    main = ("move 3,R0\nnop\nouter: move @after_a,R27\njmp @measure\n"
            "after_a: move @after_b,R27\njmp @measure\n"
            "after_b: sub R0,1,R0\njnz @outer\nstop\n")
    routine = "measure: move 3,R1\nnop\ninner: wait 100\nsub R1,1,R1\njnz @inner\njmp R27\n"
    source = "jmp @main\n" + routine + "main: " + prefix + main if reverse else prefix + main + routine
    playback = session(source)
    ir = finish(playback)
    expected = interpret_program(parse_q1asm(source), sequencer_id="seq0", execution_mode="concrete")
    assert playback.runners[0].state.q1_time_ns == expected.q1_time_ns
    assert playback.runners[0].state.rt_time_ns == expected.rt_time_ns
    assert playback.runners[0].state.registers == expected.registers
    line = lambda label: next(index for index, text in enumerate(source.splitlines(), 1) if label + ":" in text)
    loops = ir["playback"]["loops"]
    outer, = [loop for loop in loops if loop["source_start"]["line"] == line("outer")]
    inner = [loop for loop in loops if loop["source_start"]["line"] == line("inner")]
    assert outer["count"] == 3 and outer["complete"]
    assert len(inner) == 6 + int(prelude) and len(loops) == len(inner) + 1
    assert all(loop["count"] == 3 and loop["complete"] for loop in inner)
    hidden = {event_id for loop in loops for event_id in loop["hidden_event_ids"]}
    visible = [loop for loop in inner if loop["loop_id"] not in hidden]
    assert len(visible) == 2 + int(prelude)
    assert all(any(event_id not in hidden for event_id in loop["first_iteration_event_ids"]) for loop in visible)


def test_two_sequential_backward_layout_calls_are_not_a_loop_even_during_second_call() -> None:
    playback = session("jmp @main\nfunc: wait 100\njmp R27\nmain: move @ret1,R27\njmp @func\n"
                       "ret1: move @ret2,R27\njmp @func\nret2: stop")
    playback.run(max_steps=7)
    assert playback.snapshot()["playback"]["loops"] == []
    assert finish(playback)["playback"]["loops"] == []


def test_nested_routines_with_saved_return_registers_do_not_create_loops() -> None:
    source = ("move 3,R0\nnop\nbody: move @done,R20\njmp @first\n"
              "done: sub R0,1,R0\njnz @body\nstop\n"
              "first: move R20,R21\nnop\nmove @after_inner,R20\njmp @second\n"
              "after_inner: move R21,R20\nnop\nwait 4\njmp R20\nsecond: wait 8\njmp R20")
    ir = finish(session(source))
    loop, = ir["playback"]["loops"]
    assert loop["source_start"]["line"] == 3 and loop["count"] == 3 and loop["complete"]


def test_indirect_loop_survives_an_unrelated_register_equal_to_jump_continuation() -> None:
    playback = session("move @body,R27\nmove @done,R9\nmove 3,R0\nbody: wait 100\n"
                       "sub R0,1,R0\njz @done\njmp R27\ndone: stop")
    playback.run(max_steps=8)
    partial, = playback.snapshot()["playback"]["loops"]
    assert partial["count"] == ">=1" and not partial["complete"]
    loop, = finish(playback)["playback"]["loops"]
    assert loop["count"] == 3 and loop["complete"]


def test_loop_backedge_can_target_a_higher_instruction_address() -> None:
    source = ("jmp @setup\nlatch: sub R0,1,R0\njnz R27\nstop\n"
              "setup: move @body,R27\nmove 3,R0\nnop\nbody: wait 8\njmp @latch")
    ir = finish(session(source))
    loop, = ir["playback"]["loops"]
    assert loop["source_start"]["line"] == 8 and loop["source_end"]["line"] == 3
    assert loop["count"] == 3 and loop["complete"]
    by_id = {event["id"]: event for event in ir["events"]}
    member_lines = {by_id[event_id]["source"]["line"] for event_id in loop["playback_event_ids"]}
    assert member_lines == {2, 3, 8, 9}  # Logical target/latch bounds alone omit physical loop code.


def test_alternating_backedges_form_one_loop_and_pending_new_path_stays_visible() -> None:
    source = ("move 4,R0\nmove 0,R1\nbody: wait 8\nsub R0,1,R0\njz @done\n"
              "xor R1,1,R1\nnop\ncmp R1,0\njz @even\njmp @body\n"
              "even: wait 4\njmp @body\ndone: stop")
    playback = session(source)
    playback.run(max_steps=18)
    partial = playback.snapshot()
    loop, = partial["playback"]["loops"]
    assert loop["count"] == ">=1" and not loop["complete"]
    current_wait = [event for event in partial["events"] if event["kind"] == "wait"][-1]
    assert current_wait["id"] not in loop["hidden_event_ids"]
    loop, = finish(playback)["playback"]["loops"]
    assert loop["count"] == 4 and loop["complete"]
    assert loop["period"]["kind"] == "unknown"


def test_final_iteration_new_path_to_untaken_latch_keeps_its_actual_duration() -> None:
    source = ("move 3,R0\nnop\nbody: wait 100\ncmp R0,1\njz @last\n"
              "sub R0,1,R0\njnz @body\nstop\nlast: move @ret,R27\njmp @callee\n"
              "ret: sub R0,1,R0\njnz @body\nstop\ncallee: wait 500\njmp R27")
    ir = finish(session(source))
    loop, = ir["playback"]["loops"]
    assert loop["count"] == 3 and loop["complete"]
    assert loop["period"]["kind"] == "unknown"
    annotation = next(event for event in ir["events"] if event["kind"] == "loop_block")
    assert annotation["t1"]["value"] == 800
    assert all(event["id"] in loop["playback_event_ids"] for event in ir["events"] if event["kind"] == "wait")


@pytest.mark.parametrize("body", ["wait 100", "wait 0", "nop"])
def test_failed_top_of_loop_guard_is_not_counted_as_another_iteration(body) -> None:
    source = f"move 3,R0\nnop\nhead: cmp R0,0\njz @done\n{body}\nsub R0,1,R0\njmp @head\ndone: stop"
    ir = finish(session(source))
    loop, = ir["playback"]["loops"]
    assert loop["count"] == 3 and loop["complete"]
    assert loop["period"]["kind"] == "concrete"
    last_checks = [event for event in ir["events"] if event["kind"] == "q1_issue" and event["source"]["line"] in {3, 4}][-2:]
    assert all(event["id"] not in loop["playback_event_ids"] for event in last_checks)


def test_zero_time_q1_only_loop_still_counts_final_untaken_latch_iteration() -> None:
    loop, = finish(session("move 3,R0\nnop\nbody: sub R0,1,R0\njnz @body\nstop"))["playback"]["loops"]
    assert loop["count"] == 3 and loop["complete"]
    assert loop["period"]["value"] == 0


def test_untaken_conditional_continue_does_not_close_a_still_running_iteration() -> None:
    playback = session("move 3,R0\nmove 0,R1\nbody: wait 8\ncmp R1,1\njz @body\n"
                       "sub R0,1,R0\njnz @body\nstop")
    playback.run(max_steps=7)
    partial, = playback.snapshot()["playback"]["loops"]
    assert partial["count"] == ">=1" and not partial["complete"]
    loop, = finish(playback)["playback"]["loops"]
    assert loop["count"] == 3 and loop["complete"]


def test_zero_loop_counter_wraps_and_partial_count_is_not_claimed_complete() -> None:
    playback = session("move 0,R0\nnop\nbody: wait 4\nloop R0,@body\nstop")
    result = playback.run(max_steps=6)
    assert result["status"] == "paused"
    assert playback.runners[0].state.registers["R0"] == Concrete(0xFFFFFFFF)
    loop, = playback.snapshot()["playback"]["loops"]
    assert loop["count"] == ">=2" and not loop["complete"]
    playback.run(max_steps=1)
    assert playback.runners[0].state.registers["R0"] == Concrete(0xFFFFFFFE)


@pytest.mark.parametrize("source,reason", [
    ("wait R0\nstop", "duration"),
    ("jmp R0\nstop", "destination"),
    ("fb_pull_data R0,R1\nstop", "external timing"),
])
def test_unknown_timing_and_external_boundaries_do_not_execute(source: str, reason: str) -> None:
    playback = session(source)
    result = playback.run()
    assert result["status"] == "blocked" and result["steps"] == 0
    assert reason in result["reason"]
    assert playback.runners[0].state.registers == {}
    assert not [event for event in playback.snapshot()["events"] if event["kind"] == "q1_issue"]


@pytest.mark.parametrize("condition", [
    "set_cond 0,1,0,20", "set_cond 0,32767,6,4", "set_cond 0,0,7,4",
    "move 0,R0\nnop\nset_cond R0,R0,R0,4",
    "move 0,R0\nnop\nset_cond R0,R1,R2,4",
])
def test_disabled_condition_keeps_playback_timing_and_outputs_unconditional(condition: str) -> None:
    playback = session(f"set_mrk 3\n{condition}\nupd_param 4\nwait 8\nstop")
    ir = finish(playback)
    assert ir["playback"]["status"] == "completed"
    assert playback.runners[0].state.rt_time_ns == Concrete(12)
    update, = [event for event in ir["events"] if event["kind"] == "upd_param"]
    assert update["meta"]["applied_state"]["marker"] == 3
    assert not playback.runners[0].state.diagnostics


@pytest.mark.parametrize("condition", ["set_cond 1,1,0,20", "set_cond R0,R0,R0,20"])
def test_active_or_unknown_condition_retains_only_the_supported_playback_prefix(condition: str) -> None:
    playback = session(f"wait 100\n{condition}\nupd_param 4\nstop")
    result = playback.run()
    assert result["status"] == "blocked" and result["steps"] == 1
    assert "trigger-counter" in result["reason"]
    state = playback.runners[0].state
    assert state.rt_time_ns == Concrete(100)
    assert [event.meta["op"] for event in state.events if event.kind == "q1_issue"] == ["wait"]


@pytest.mark.parametrize("condition", [
    "set_cond 0,0,0,-1", "set_cond 1,0,0,-1", "set_cond 2,0,0,4",
    "set_cond 0,-1,0,4", "set_cond 0,0,8,4",
    "move 0,R0\nset_cond R0,R0,R0,4",
])
def test_condition_validation_precedes_unsupported_trigger_semantics(condition: str) -> None:
    playback = session(f"{condition}\nupd_param 4\nstop")
    result = playback.run()
    assert result["status"] == "error"
    assert result["steps"] == int(condition.startswith("move"))
    state = playback.runners[0].state
    assert state.rt_time_ns == Concrete(0)
    assert not any(event.kind == "q1_issue" and event.meta["op"] == "set_cond" for event in state.events)


@pytest.mark.parametrize("source", ["loop\nstop", "loop R0\nstop", "move 1\nstop"])
def test_malformed_instruction_returns_error_without_crashing(source: str) -> None:
    playback = session(source)
    assert playback.run()["status"] == "error"
    assert playback.steps == 0


def test_register_hazard_stops_without_fabricating_read_value() -> None:
    playback = session("move 7,R0\nmove R0,R1\nstop")
    assert playback.run()["status"] == "error"
    assert playback.steps == 1
    assert playback.runners[0].state.registers == {}
    assert len(playback.runners[0].state.pending_register_writes) == 1


@pytest.mark.parametrize("source", [
    "move 32768,R0\nnop\nset_awg_gain R0,R0\nupd_param 4\nstop",
    "move 1,R0\nnop\nlsl R0,33,R1\nnop\nstop",
    "illegal\nstop",
])
def test_allowing_unknown_values_preserves_known_operand_and_opcode_guards(source: str) -> None:
    playback = session(source)
    assert playback.run()["status"] in {"blocked", "error"}
    assert not any(event.kind == "upd_param" for event in playback.runners[0].state.events)


def test_project_metadata_and_parameter_overrides_survive_playback(tmp_path) -> None:
    (tmp_path / "program.q1asm").write_text("wait $DURATION\nstop", encoding="utf-8")
    (tmp_path / "params.json").write_text('{"DURATION": 8}', encoding="utf-8")
    project = tmp_path / "q1timeline.yml"
    project.write_text("sequencers:\n  - id: seq0\n    module: QCM0\n    file: program.q1asm\nparams:\n  file: params.json\n", encoding="utf-8")
    playback = PlaybackSession.from_project(project, params={"DURATION": 12})
    ir = finish(playback)
    assert ir["sequencers"][0]["module"] == "QCM0"
    assert ir["analysis"]["parameters"] == [
        {"name": "DURATION", "value_json": "12", "overridden": True, "base_value_json": "8"}
    ]
    assert playback.runners[0].state.rt_time_ns == Concrete(12)


def test_snapshot_does_not_mutate_live_events_or_duplicate_alignment_diagnostics() -> None:
    playback = PlaybackSession({"seq0": ("a.q1asm", "wait_sync 4\nwait 8\nstop")}, alignment_mode="first_wait_sync")
    playback.run(max_steps=2)
    before = [dict(event.meta) for event in playback.runners[0].state.events]
    first = playback.snapshot()
    second = playback.snapshot()
    assert first == second
    assert [event.meta for event in playback.runners[0].state.events] == before


def test_run_summary_stays_small_and_trace_limit_remains_explicit(monkeypatch) -> None:
    monkeypatch.setattr("q1timeline.playback.MAX_TRACE_EVENTS", 30)
    playback = session("body: wait 4\njmp @body")
    result = playback.run(max_steps=100)
    assert result["status"] == "limit" and not result["can_resume"]
    assert "branches" not in result and len(json.dumps(result)) < 1000
    assert not playback.snapshot()["playback"]["loops"][0]["complete"]


def test_repeated_default_assumption_provenance_has_a_bounded_recording_budget(monkeypatch) -> None:
    monkeypatch.setattr("q1timeline.playback.MAX_ASSUMPTION_REFERENCES", 5)
    playback = session("body: add R0,1,R1\nnop\ncmp R1,0\njnz @next\nnext: wait 4\njmp @body")
    result = playback.run(max_steps=100)
    assert result["status"] == "limit"
    assert result["steps"] < 100
    assert playback.snapshot()["playback"]["branches"][-1]["assumption_source"] == "default"


def test_actual_golden_wait_loop_continues_through_assumed_feedback_receipts() -> None:
    root = Path(__file__).resolve().parents[1]
    playback = PlaybackSession.from_project(root / "examples/golden-section/q1timeline.yml")
    playback.run(budget_ms=1000, max_steps=100)
    ir = playback.snapshot()
    assert ir["playback"]["status"] == "paused"
    assert len(ir["playback"]["feedback"]) == 2
    assert all(item["value"] is None and item["delay_ns"] == 0 for item in ir["playback"]["feedback"])
    loop = next(item for item in ir["playback"]["loops"] if item["source_start"]["line"] == 87)
    assert loop["count"] == 15 and loop["complete"]
    assert loop["first_iteration_end"]["value"] == 65563


def test_full_golden_trace_has_one_partial_outer_loop_and_no_return_loops_or_orphan_bars() -> None:
    root = Path(__file__).resolve().parents[1]
    playback = PlaybackSession.from_project(root / "examples/golden-section/q1timeline.yml")
    ir = finish(playback)
    loops = ir["playback"]["loops"]
    outer, = [loop for loop in loops if loop["source_start"]["line"] == 12]
    inner = [loop for loop in loops if loop["source_start"]["line"] == 87]
    assert ir["playback"]["status"] == "limit"
    assert not outer["complete"] and outer["completed_iterations"] > 2
    assert outer["count"] == f">={outer['completed_iterations']}"
    assert len(inner) > 4 and all(loop["count"] == 15 and loop["complete"] for loop in inner)
    assert len(loops) == len(inner) + 1
    hidden = {event_id for loop in loops for event_id in loop["hidden_event_ids"]}
    assert all(any(event_id not in hidden for event_id in loop["first_iteration_event_ids"])
               for loop in loops if loop["loop_id"] not in hidden)
    assert all(loop["loop_id"] in outer["hidden_event_ids"] for loop in inner
               if loop["first_iteration_event_ids"][0] in outer["hidden_event_ids"])


def test_feedback_defaults_to_received_unknown_value_and_unknown_branch_continues() -> None:
    playback = session("fb_pop_data 16,R0\nnop\ncmp R0,0\njnz @jump\nwait 4\nstop\njump: wait 8\nstop")
    ir = finish(playback)
    assert ir["playback"]["status"] == "completed"
    receipt, = ir["playback"]["feedback"]
    assert receipt["feedback_id"] == "seq0:feedback:0"
    assert receipt["value"] is None and receipt["delay_ns"] == 0
    assert receipt["assumption_source"] == "default"
    assert receipt["available_at"]["value"] == 0 and receipt["completed_at"]["value"] == 4
    assert isinstance(playback.runners[0].state.registers["R0"], Unknown)
    assert ir["playback"]["branches"][0]["path"] == "fallthrough"
    assert ir["playback"]["branches"][0]["calculated_taken"] is None
    receive_events = [event for event in ir["events"] if event["meta"].get("playback_feedback_id")]
    assert {event["kind"] for event in receive_events} == {"q1_issue", "feedback_pop"}
    assert all(event["confidence"] == "assumed" for event in receive_events)


def test_completed_assumed_receipts_do_not_claim_missing_fifo_payloads_or_verified_balance() -> None:
    source = "fb_pop_data 16,R0\nnop\nstop"
    playback = session(source)
    ir = finish(playback)
    assert not [item for item in ir["diagnostics"] if item["category"] == "feedback_fifo_imbalance"]
    assert ir["feedback_balance"]["status"] == "assumed"
    assert "not verified" in ir["feedback_balance"]["reason"]
    assert ir["playback"]["feedback"][0]["assumed"]
    from q1timeline.ir.serialize import timeline_ir_from_states
    static = timeline_ir_from_states([interpret_program(parse_q1asm(source), sequencer_id="seq0")])
    assert any(item["category"] == "feedback_fifo_imbalance" for item in static["diagnostics"])


def test_assumed_feedback_delay_stalls_q1_while_queued_rt_continues() -> None:
    playback = session("wait 100\nfb_pop_data 16,R0\nnop\nadd R0,1,R1\nnop\nwait 8\nstop")
    before = finish(playback)
    receipt, = before["playback"]["feedback"]
    playback.set_feedback(receipt["feedback_id"], -1, 40)
    after = finish(playback)
    state = playback.runners[0].state
    assert state.registers == {"R0": Concrete(0xFFFFFFFF), "R1": Concrete(0)}
    assert [(event["t0"], event["t1"]) for event in after["events"] if event["kind"] == "wait"] == [
        (event["t0"], event["t1"]) for event in before["events"] if event["kind"] == "wait"
    ]
    issue = next(event for event in after["events"] if event["id"] == receipt["event_id"])
    assert (issue["t0"]["value"], issue["t1"]["value"], issue["duration"]["value"]) == (4, 48, 44)
    annotation = next(event for event in after["events"] if event["kind"] == "feedback_pop")
    assert annotation["t0"]["value"] == 44  # Availability precedes the 4 ns register-writing instruction completion.
    assert state.rt_packets[0].q1_issue_t0 == Concrete(0)
    before_later = next(event for event in before["events"] if event["kind"] == "q1_issue" and event["source"]["line"] == 6)
    assert state.rt_packets[1].q1_issue_t0.value == before_later["t0"]["value"] + 40
    for write in after["register_history"]:
        assert write["confidence"] == "assumed"
        assert write["meta"]["feedback_assumptions"] == [receipt["feedback_id"]]
    feedback_write = next(write for write in after["register_history"] if write["register"] == "R0")
    assert feedback_write["meta"]["feedback_assumption"] == {"value": -1, "delay_ns": 40, "source": "user"}
    assert feedback_write["meta"]["provenance"]["steps"][0]["playback_feedback_id"] == receipt["feedback_id"]


def test_feedback_value_edit_recalculates_branch_without_fabricating_an_override() -> None:
    playback = session("fb_pop_data 16,R0\nnop\ncmp R0,7\njz @jump\nwait 4\nstop\njump: wait 8\nstop")
    finish(playback)
    playback.set_feedback(playback.feedback[0]["feedback_id"], 7, 0)
    ir = finish(playback)
    branch, = ir["playback"]["branches"]
    assert branch["calculated_taken"] is True and branch["path"] == "taken"
    assert branch["assumption_source"] == "calculated"
    issue = next(event for event in ir["events"] if event["id"] == branch["event_id"])
    assert issue["confidence"] == "assumed" and issue["meta"]["feedback_assumptions"]
    assert not playback.choices


def test_feedback_overrides_are_per_occurrence_and_edits_discard_all_suffix_choices() -> None:
    playback = session("move 3,R1\nnop\nbody: fb_pop_data 16,R0\nnop\nloop R1,@body\nstop")
    finish(playback)
    first, second, third = [item["feedback_id"] for item in playback.feedback]
    assert len({first, second, third}) == 3
    playback.set_feedback(first, 10, 4)
    finish(playback)
    playback.set_feedback(third, 30, 12)
    finish(playback)
    last_branch = playback.branches[-1]["branch_id"]
    playback.choose(last_branch, "fallthrough")
    finish(playback)
    assert playback.feedback_choices.keys() == {first, third}
    assert last_branch in playback.choices
    rewound = playback.set_feedback(second, 20, 8)
    assert rewound["playback"]["steps"] == 0 and rewound["playback"]["feedback"] == []
    assert playback.feedback_choices.keys() == {first, second}
    assert last_branch not in playback.choices
    ir = finish(playback)
    assert [item["value"] for item in ir["playback"]["feedback"]] == [10, 20, None]
    playback.choose(playback.branches[0]["branch_id"], "fallthrough")
    assert playback.feedback_choices.keys() == {first}
    ir = finish(playback)
    assert len(ir["playback"]["feedback"]) == 1


def test_feedback_override_preserves_register_read_hazards() -> None:
    playback = session("fb_pop_data 16,R0\nmove R0,R1\nstop")
    assert playback.run()["status"] == "error"
    playback.set_feedback(playback.feedback[0]["feedback_id"], 7, 1000)
    assert playback.run()["status"] == "error"
    assert playback.steps == 1
    assert playback.runners[0].state.pending_register_writes[0].value == Concrete(7)
    assert not playback.runners[0].state.registers


def test_feedback_delay_is_local_to_the_receiving_sequencer() -> None:
    playback = PlaybackSession({
        "receiver": ("receive.q1asm", "wait 100\nfb_pop_data 16,R0\nnop\nwait 8\nstop"),
        "peer": ("peer.q1asm", "wait 20\nnop\nwait 8\nstop"),
    })
    before = finish(playback)
    playback.set_feedback(playback.feedback[0]["feedback_id"], 2, 40)
    after = finish(playback)
    assert [event for event in after["events"] if event["sequencer_id"] == "peer"] == [
        event for event in before["events"] if event["sequencer_id"] == "peer"
    ]


@pytest.mark.parametrize("value,delay", [(True, 0), (1.5, 0), (-(1 << 31) - 1, 0), (1 << 32, 0),
                                        (0, True), (0, -1), (0, 1.5), (0, 9007199254740992)])
def test_invalid_feedback_assumptions_do_not_mutate_the_current_execution(value, delay) -> None:
    playback = session("fb_pop_data 16,R0\nnop\nstop")
    before = finish(playback)
    with pytest.raises(ValueError):
        playback.set_feedback(playback.feedback[0]["feedback_id"], value, delay)
    assert playback.snapshot() == before
    assert playback.feedback_choices == {}


def test_feedback_occurrence_must_belong_to_current_execution() -> None:
    playback = session("fb_pop_data 16,R0\nnop\nstop")
    before = finish(playback)
    with pytest.raises(ValueError, match="occurrence"):
        playback.set_feedback("old:feedback:1", None, 0)
    assert playback.snapshot() == before


@pytest.mark.parametrize("value", [-(1 << 31), 0xFFFFFFFF, None])
def test_feedback_accepts_register_endpoints_and_unknown(value) -> None:
    playback = session("fb_pop_data 16,R0\nnop\nstop")
    finish(playback)
    playback.set_feedback(playback.feedback[0]["feedback_id"], value, 0)
    finish(playback)
    actual = playback.runners[0].state.registers["R0"]
    assert isinstance(actual, Unknown) if value is None else actual == Concrete(value & 0xFFFFFFFF)


def test_json_lines_transport_keeps_state_and_returns_snapshot_only_when_requested(tmp_path) -> None:
    source = tmp_path / "play.q1asm"
    source.write_text("move 2,R0\nnop\nbody: wait 4\nloop R0,@body\nstop", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    requests = [
        {"id": 1, "command": "run", "max_steps": 3},
        {"id": 2, "command": "run"},
        {"id": 3, "command": "snapshot"},
    ]
    result = subprocess.run(
        [sys.executable, "-u", "-m", "q1timeline.playback", "--project", str(source)],
        input="".join(json.dumps(item) + "\n" for item in requests), text=True, capture_output=True,
        env={**os.environ, "PYTHONPATH": str(root / "src")}, timeout=10, check=True,
    )
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert [item["id"] for item in responses] == [1, 2, 3]
    assert responses[0]["playback"]["status"] == "paused"
    assert responses[1]["playback"]["status"] == "completed"
    assert "ir" not in responses[0]
    assert responses[2]["ir"]["playback"]["loops"][0]["count"] == 2


def test_json_lines_feedback_edit_resets_then_runs_the_new_assumption(tmp_path) -> None:
    source = tmp_path / "receive.q1asm"
    source.write_text("fb_pop_data 16,R0\nnop\nstop", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    requests = [
        {"id": 1, "command": "run"},
        {"id": 2, "command": "set_feedback", "feedback_id": "receive:feedback:0", "value": -2, "delay_ns": 24},
        {"id": 3, "command": "run"},
        {"id": 4, "command": "snapshot"},
    ]
    result = subprocess.run(
        [sys.executable, "-u", "-m", "q1timeline.playback", "--project", str(source)],
        input="".join(json.dumps(item) + "\n" for item in requests), text=True, capture_output=True,
        env={**os.environ, "PYTHONPATH": str(root / "src")}, timeout=10, check=True,
    )
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert [item["id"] for item in responses] == [1, 2, 3, 4]
    assert responses[1]["ir"]["playback"]["status"] == "idle"
    assert responses[1]["ir"]["playback"]["steps"] == 0
    assert responses[2]["playback"]["status"] == "completed"
    receipt, = responses[3]["ir"]["playback"]["feedback"]
    assert receipt["value"] == -2 and receipt["delay_ns"] == 24
    assert responses[3]["ir"]["register_history"][0]["value"]["value"] == 0xFFFFFFFE
