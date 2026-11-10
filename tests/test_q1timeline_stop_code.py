import pytest

from q1timeline.analysis.api import analyze_documents, analyze_text
from q1timeline.analysis.interpreter import interpret_program, prepare_def_program
from q1timeline.playback import PlaybackSession
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


def analyze(source, mode):
    if mode == "playback":
        session = PlaybackSession({"seq0": ("stop.q1asm", source)})
        assert session.run(budget_ms=1000, max_steps=1000)["status"] == "completed"
        return session.snapshot()
    return analyze_text(source, sequencer_id="seq0", alignment_mode="none", execution_mode=mode,
                        simulation={"startup_delay_ns": 16} if mode == "simulation" else None)


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation", "playback"])
@pytest.mark.parametrize("operand,expected", [("", 0), (" 17", 17), (" R7", -1)])
def test_stop_reports_signed_code_and_real_source_without_moving_rt_drain(mode, operand, expected):
    ir = analyze(f"move 0xffffffff,R7\nnop\nwait 20\nstop{operand}\nwait 80\nstop 91\n", mode)
    stop, = [event for event in ir["events"] if event["kind"] == "stop"]
    issue, = [event for event in ir["events"] if event["kind"] == "q1_issue" and event["meta"]["op"] == "stop"]
    assert stop["source"]["line"] == 4 and stop["source"]["raw"] == f"stop{operand}"
    assert stop["meta"]["stop_code"] == issue["meta"]["stop_code"] == expected
    assert stop["meta"]["q1_issue_event_id"] == issue["id"]
    assert issue["t0"]["value"] == 12 and issue["t1"]["value"] == 16
    assert stop["t0"]["value"] == stop["t1"]["value"] == (36 if mode == "simulation" else 20)
    if mode == "analysis":
        assert "execution" not in ir
    else:
        execution = ir["execution"]["seq0"]
        assert execution["status"] == "completed" and execution["stop_code"] == expected
        assert execution["stop_source"] == stop["source"]


def test_static_stop_preserves_unknown_when_register_is_unresolved_or_unready():
    for source in ("stop R7", "move 1,R7\nnop\nmove 2,R7\nstop R7"):
        ir = analyze(source, "analysis")
        stop, = [event for event in ir["events"] if event["kind"] == "stop"]
        assert stop["meta"]["stop_code"]["kind"] == "unknown"


@pytest.mark.parametrize("mode", ["concrete", "simulation"])
def test_prepared_q1_only_execution_keeps_stop_code_without_rt_trace(mode):
    program = parse_q1asm(".DEF CODE 7\nstop $CODE\nstop 91\n", file="code.q1asm")
    prepared = prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
    if mode == "simulation":
        state, = simulate_programs({"a": program}, prepared_programs={"a": prepared})
    else:
        state = interpret_program(program, sequencer_id="a", execution_mode=mode, prepared=prepared)
    assert not state.events
    assert state.metadata["execution"]["stop_code"] == 7
    assert state.metadata["execution"]["stop_source"]["line"] == 2
    assert state.q1_time_ns.value == 4 and state.rt_time_ns.value == 0


def test_simulated_q1_only_stop_preserves_existing_rt_lane_behavior():
    ir = analyze_text("stop 17", sequencer_id="seq0", alignment_mode="none", execution_mode="simulation")
    assert ir["execution"]["seq0"]["stop_code"] == 17
    assert not any(event["kind"] == "stop" for event in ir["events"])


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize(("source", "startup", "q1_end"), [
    ("stop 17", 20, 4),
    ("nop\nnop\nstop 17", 4, 12),
    ("stop 17\nwait 4", 20, 4),
    ("stop 17\nwait 4", 4, 4),
])
def test_fixed_start_q1_only_stop_does_not_start_rt(source, startup, q1_end, prepared):
    program = parse_q1asm(source, file="stop.q1asm")
    bindings = {"s": prepare_def_program(program, params={}, strict_q1asm=False, varying=set())} if prepared else None
    state, = simulate_programs({"s": program}, simulation={"startup_delay_ns": startup}, prepared_programs=bindings)
    execution = state.metadata["execution"]
    assert execution["status"] == "completed" and execution["stop_code"] == 17
    assert state.q1_time_ns.value == q1_end and state.rt_time_ns.value == 0
    assert state.metadata["simulation"]["end_time_ns"] == q1_end
    assert not state.rt_packets
    assert not any(event.lane.startswith("rt.") or event.kind.startswith("rt_queue_") for event in state.events)
    if prepared:
        assert not state.events
    else:
        issue, = [event for event in state.events if event.kind == "q1_issue" and event.meta["op"] == "stop"]
        assert issue.t1.value == q1_end and issue.meta["stop_code"] == 17


def test_fixed_start_q1_only_peer_keeps_real_rt_origin_and_drain():
    ir = analyze_documents({"q1.q1asm": "stop 17", "peer.q1asm": "wait 20\nstop 5"},
                           execution_mode="simulation", simulation={"startup_delay_ns": 20})
    assert ir["execution"]["q1"]["stop_code"] == 17
    assert ir["alignment"]["q1"]["anchor_sequencer_id"] == "peer"
    assert not any(event["sequencer_id"] == "q1" and (event["lane"].startswith("rt.") or
                   event["kind"].startswith("rt_queue_")) for event in ir["events"])
    wait, stop = [event for event in ir["events"] if event["kind"] in {"wait", "stop"}]
    assert wait["sequencer_id"] == stop["sequencer_id"] == "peer"
    assert (wait["meta"]["aligned_t0"], wait["meta"]["aligned_t1"], stop["meta"]["aligned_t0"]) == (0, 20, 20)
    assert stop["t0"]["value"] == 40 and stop["meta"]["stop_code"] == 5


def test_fixed_start_q1_only_reports_the_q1_limit_or_fault():
    loop, = simulate_programs({"s": parse_q1asm("body: nop\njmp @body")}, max_steps=5,
                              simulation={"startup_delay_ns": 4})
    execution = loop.metadata["execution"]
    assert execution["status"] == "step_limit" and execution["steps"] == 5
    assert execution["terminal"]["local_time_ns"] == loop.q1_time_ns.value
    assert not loop.rt_packets and loop.rt_time_ns.value == 0

    fault, = simulate_programs({"s": parse_q1asm("nop\nnop\nillegal\nstop 17")}, simulation={"startup_delay_ns": 4})
    assert fault.metadata["execution"]["terminal"]["category"] == "illegal_instruction"
    assert fault.metadata["execution"]["fault"]["q1_time_ns"] == 12
    assert not fault.rt_packets


def test_fixed_start_keeps_rt_starvation_and_zero_duration_work():
    late, = simulate_programs({"s": parse_q1asm("nop\nnop\nwait 4\nstop 17")}, simulation={"startup_delay_ns": 4})
    terminal = late.metadata["execution"]["terminal"]
    assert (terminal["category"], terminal["local_time_ns"]) == ("rt_queue_underflow", 4)

    zero, = simulate_programs({"s": parse_q1asm("wait 0\nstop 17")}, simulation={"startup_delay_ns": 4})
    dequeue, wait, stop = [event for event in zero.events if event.kind in {"rt_queue_dequeue", "wait", "stop"}]
    assert dequeue.t0.value == wait.t0.value == wait.t1.value == 4
    assert stop.t0.value == 8 and stop.meta["stop_code"] == 17


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation", "playback"])
def test_illegal_instruction_records_q1_fault_without_normal_stop(mode):
    source = "nop\nillegal\nstop 17"
    if mode == "playback":
        playback = PlaybackSession({"seq0": ("fault.q1asm", source)})
        assert playback.run()["status"] == "error"
        ir = playback.snapshot()
    else:
        ir = analyze(source, mode)
    prefix, issue, fault = ir["events"]
    assert prefix["meta"]["op"] == "nop"
    assert issue["meta"]["op"] == "illegal"
    assert (issue["t0"]["value"], issue["t1"]["value"], issue["duration"]["value"]) == (4, 8, 4)
    assert fault["kind"] == "illegal" and fault["t0"]["value"] == fault["t1"]["value"] == 8
    assert issue["meta"]["fault"] == fault["meta"]["fault"]
    assert fault["meta"]["q1_issue_event_id"] == issue["id"]
    assert ir["source_map"]["by_event_id"][fault["id"]] == issue["source"] == fault["source"]
    diagnostic, = [item for item in ir["diagnostics"] if item["category"] == "illegal_instruction"]
    details = diagnostic["details"]["fault"]
    assert details["flag"] == "SEQUENCE_PROCESSOR_RT_EXEC_ILLEGAL_INSTRUCTION"
    assert details["sequencer_status"] == "STOPPED"
    assert details["q1_time_ns"] == 8
    assert details["source"] == diagnostic["source"] == fault["source"]
    assert set(diagnostic["related_events"]) == {issue["id"], fault["id"]}
    assert diagnostic["severity"] == "error"
    if mode != "analysis":
        execution = ir["execution"]["seq0"]
        assert execution["status"] == "error" and execution["steps"] == 2 and execution["fault"] == details
        assert "stop_code" not in execution


@pytest.mark.parametrize("duration", [8, 20])
def test_simulated_fault_preserves_started_work_and_discards_same_tick_dequeue(duration):
    fault = parse_q1asm(f"wait {duration}\nplay 0,1,40\nillegal\nstop 17", file="fault.q1asm")
    peer = parse_q1asm("wait 32\nstop 5", file="peer.q1asm")
    bad, good = simulate_programs({"fault": fault, "peer": peer}, simulation={"startup_delay_ns": 4})
    wait, = [event for event in bad.events if event.kind == "wait"]
    assert (wait.t0.value, wait.t1.value, wait.duration.value) == (4, 12, 8)
    assert bool(wait.meta.get("interrupted_by_fault")) is (duration == 20)
    assert not any(event.kind in {"play", "stop"} for event in bad.events)
    assert bad.rt_packets[1].op == "play" and bad.rt_packets[1].meta["discarded_by_fault"]
    assert bad.rt_packets[1].rt_t0 is None
    assert bad.metadata["execution"]["fault"]["q1_time_ns"] == 12
    assert good.metadata["execution"]["stop_code"] == 5
    assert good.rt_time_ns.value == 36


def test_fault_during_default_prefill_never_starts_queued_rt_work():
    program = parse_q1asm("wait 100\nillegal")
    state, = simulate_programs({"seq0": program})
    assert state.metadata["execution"]["status"] == "error"
    assert state.rt_packets[0].meta["discarded_by_fault"]
    assert not any(event.kind in {"wait", "rt_queue_dequeue", "stop"} for event in state.events)


def test_playback_fault_is_local_and_queue_timing_stays_unverified():
    playback = PlaybackSession({"bad": ("bad.q1asm", "wait 100\nillegal"),
                                "peer": ("peer.q1asm", "wait 32\nnop\nstop 5")})
    assert playback.run(max_steps=3)["status"] == "paused"
    assert playback.run()["status"] == "error"
    ir = playback.snapshot()
    assert ir["execution"]["bad"]["queue_timing"]["status"] == "analysis_incomplete"
    assert ir["execution"]["peer"]["status"] == "completed"
    assert [event["sequencer_id"] for event in ir["events"] if event["kind"] == "stop"] == ["peer"]


@pytest.mark.parametrize("mode", ["concrete", "simulation"])
def test_prepared_illegal_keeps_fault_and_step_without_trace(mode):
    program = parse_q1asm("illegal\nstop 5", file="fault.q1asm")
    prepared = prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
    if mode == "concrete":
        state = interpret_program(program, sequencer_id="seq0", execution_mode="concrete", prepared=prepared)
    else:
        state, = simulate_programs({"seq0": program}, prepared_programs={"seq0": prepared})
    assert not state.events
    execution = state.metadata["execution"]
    assert execution["status"] == "error" and execution["steps"] == 1
    assert execution["fault"]["q1_time_ns"] == 4 and execution["fault"]["source"]["file"] == "fault.q1asm"
    assert "stop_code" not in execution


@pytest.mark.parametrize("mode", ["analysis", "concrete", "simulation", "playback"])
@pytest.mark.parametrize("source", ["jmp @end\nillegal\nend: stop 5", "illegal 7\nstop 5"])
def test_unexecuted_and_malformed_illegal_do_not_raise_a_runtime_fault(mode, source):
    ir = (analyze_text(source, sequencer_id="seq0", execution_mode="simulation")
          if mode == "simulation" else analyze(source, mode))
    assert not any(item["category"] == "illegal_instruction" for item in ir["diagnostics"])
    if "execution" in ir:
        assert ir["execution"]["seq0"]["status"] == "completed"
        assert ir["execution"]["seq0"]["stop_code"] == 5


def test_illegal_on_one_static_comparison_arm_is_assumed():
    ir = analyze_text("cmp R0,5\njz @end\nillegal\nend: stop", sequencer_id="seq0",
                      branch_policy="explore_both_with_depth_limit")
    fault, = [event for event in ir["events"] if event["kind"] == "illegal"]
    diagnostic, = [item for item in ir["diagnostics"] if item["category"] == "illegal_instruction"]
    assert fault["confidence"] == "assumed" and fault["meta"]["branch_comparison_path"] == "fallthrough"
    assert diagnostic["severity"] == "warning" and diagnostic["details"]["branch_comparison_path"] == "fallthrough"
