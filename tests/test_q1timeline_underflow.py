from pathlib import Path

import pytest

from q1timeline.analysis.api import analyze_documents, analyze_text
from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.queue_timing import replay_queues
from q1timeline.analysis.values import Unknown
from q1timeline.analysis.underflow import analyze_underflow, _emit_underflow_warning
from q1timeline.q1asm.parser import parse_q1asm


UNDERFLOW_CATEGORIES = {"definite_underflow", "possible_underflow"}


@pytest.mark.parametrize("status,severity", [("definite_underflow", "error"), ("possible_underflow", "warning")])
def test_underflow_severity_distinguishes_confirmed_and_possible_failures(status, severity) -> None:
    state = interpret_program(parse_q1asm("wait 4\nstop", file="program.q1asm"), sequencer_id="seq0")
    packet = state.rt_packets[0]
    _emit_underflow_warning(state, packet_id=packet.id, slack_ns=-24, t0=packet.rt_t0,
                            source=packet.source, assumptions={}, status=status)
    assert state.events[-1].meta["estimated_depth"] == 0
    assert state.events[-1].meta["slack_ns"] == -24
    assert state.diagnostics[-1].category == status
    assert state.diagnostics[-1].severity == severity


def _analyze(text: str, *, queue_depth_limit: int = 32):
    state = interpret_program(parse_q1asm(text, file="program.q1asm"), sequencer_id="seq0")
    result = analyze_underflow(state, queue_depth_limit=queue_depth_limit)
    return state, result


def test_sync_prefixes_do_not_report_startup_underflow() -> None:
    ir = analyze_documents({
        "Control.q1asm": "set_mrk 0\nwait_sync 4\nupd_param 4\nstop\n",
        "readout.q1asm": "set_mrk 2\nwait_sync 4\nupd_param 4\nstop\n",
    })

    assert not [item for item in ir["diagnostics"] if item["category"] in UNDERFLOW_CATEGORIES]
    assert not [event for event in ir["events"] if event["kind"] == "underflow_warning"]
    for sequencer in ("control", "readout"):
        updates = [event for event in ir["events"] if event["sequencer_id"] == sequencer and event["kind"] == "upd_param"]
        assert [(event["t0"]["value"], event["t1"]["value"]) for event in updates] == [(4, 8)]


@pytest.mark.parametrize("prefix", ["", "set_mrk 0\n", "nop\nnop\n"])
def test_first_rt_packet_without_sync_is_not_a_starvation_event(prefix: str) -> None:
    ir = analyze_text(f"{prefix}upd_param 4\nstop\n")

    assert not [item for item in ir["diagnostics"] if item["category"] in UNDERFLOW_CATEGORIES]
    assert not [event for event in ir["events"] if event["kind"] == "underflow_warning"]


@pytest.mark.parametrize("prefix", ["", "set_mrk 2\nwait_sync 4\n"])
def test_same_time_enqueue_is_available_to_the_rt_consumer(prefix: str) -> None:
    state = interpret_program(
        parse_q1asm(f"{prefix}upd_param 4\nupd_param 4\nupd_param 4\nstop\n", file="program.q1asm"),
        sequencer_id="seq0",
    )
    result = replay_queues({"seq0": state.rt_packets})["seq0"]

    assert result.failure is None
    assert result.incomplete_reason is None
    assert len(result.records) == len(state.rt_packets)
    assert all(record.enqueue_ns == record.dequeue_ns for record in result.records)


@pytest.mark.parametrize("prefix", ["", "wait_sync 4\n"])
@pytest.mark.parametrize("operation", ["upd_param 4", "wait 4"])
@pytest.mark.parametrize("q1_instruction_count", [2, 1000])
def test_q1_work_after_first_rt_packet_is_covered_by_assumed_initial_buffer(
    prefix: str, operation: str, q1_instruction_count: int,
) -> None:
    text = f"{prefix}{operation}\n" + "nop\n" * q1_instruction_count + f"{operation}\nstop\n"
    state, result = _analyze(text)

    assert result.status == "not_detected_under_current_assumptions"
    assert not [event for event in state.events if event.kind == "underflow_warning"]
    assert not [event for event in state.events if event.kind == "analysis_incomplete"]
    assert result.assumptions["initial_buffer_assumed_ns"] == 1_000_000
    assert result.assumptions["initial_buffer_possible_ns"] == 2_000_000
    assert state.metadata["underflow"]["initial_buffer"]["status"] == "assumed"


@pytest.mark.parametrize("operation", ["upd_param 4", "wait 4"])
@pytest.mark.parametrize("work", ["nop\n" * 64, "add R0,1,R1\nnop\n" * 22])
def test_sustained_post_sync_starvation_exhausts_initial_buffer(operation: str, work: str) -> None:
    text = "wait_sync 4\nmove 8192,R0\nbody: " + operation + "\n" + work + "loop R0,@body\nstop\n"
    state, result = _analyze(text)
    warnings = [event for event in state.events if event.kind == "underflow_warning"]

    assert result.status in UNDERFLOW_CATEGORIES
    assert all(item.severity == "error" for item in state.diagnostics if item.category == "definite_underflow")
    assert warnings
    assert all(event.source.line > 1 for event in warnings)
    assert all(event.meta["slack_ns"] < 0 for event in warnings)
    assert all(event.meta["rt_packet_id"].startswith(state.rt_packets[1].id) for event in warnings)
    assert state.metadata["underflow"]["initial_buffer"]["status"] == "exhausted"


def test_unequal_q1_arrivals_share_the_first_sync_release() -> None:
    ir = analyze_documents({
        "early.q1asm": "wait_sync 4\nupd_param 4\nstop\n",
        "late.q1asm": "nop\n" * 80 + "wait_sync 4\nupd_param 4\nstop\n",
    })

    assert not [item for item in ir["diagnostics"] if item["category"] in UNDERFLOW_CATEGORIES | {"analysis_incomplete"}]
    updates = [event for event in ir["events"] if event["kind"] == "upd_param"]
    assert len(updates) == 2
    assert all(event["t0"]["value"] == 4 for event in updates)


def test_repeated_sync_barriers_allow_unequal_rt_arrivals() -> None:
    ir = analyze_documents({
        "short.q1asm": "wait_sync 4\nwait 100\nwait_sync 4\nupd_param 4\nstop\n",
        "long.q1asm": "wait_sync 4\nwait 200\nwait_sync 4\nupd_param 4\nstop\n",
    })

    assert not [item for item in ir["diagnostics"] if item["category"] in UNDERFLOW_CATEGORIES | {"analysis_incomplete"}]
    assert {
        event["sequencer_id"]: event["t0"]["value"]
        for event in ir["events"] if event["kind"] == "upd_param"
    } == {"short": 108, "long": 208}


@pytest.mark.parametrize("participant", ["wait 8\nstop\n", "wait_sync 4\nstop\n"])
def test_missing_sync_participant_is_incomplete_instead_of_safe(participant: str) -> None:
    ir = analyze_documents({
        "waiting.q1asm": "wait_sync 4\nwait 8\nwait_sync 4\nupd_param 4\nstop\n",
        "missing.q1asm": participant,
    })

    assert any(item["category"] == "analysis_incomplete" for item in ir["diagnostics"])
    assert any(event["kind"] == "analysis_incomplete" and event["sequencer_id"] == "waiting" for event in ir["events"])
    assert not [item for item in ir["diagnostics"] if item["category"] in UNDERFLOW_CATEGORIES]


@pytest.mark.parametrize("prefix", ["", "wait_sync 4\n"])
def test_two_tight_concrete_loop_iterations_are_covered_by_initial_buffer(prefix: str) -> None:
    state = interpret_program(
        parse_q1asm(f"{prefix}move 2,R0\nbody: upd_param 4\nloop R0,@body\nstop\n", file="loop.q1asm"),
        sequencer_id="seq0", execution_mode="concrete",
    )
    result = analyze_underflow(state)

    assert len([packet for packet in state.rt_packets if packet.op == "upd_param"]) == 2
    assert result.status == "not_detected_under_current_assumptions"
    assert not [event for event in state.events if event.kind == "underflow_warning"]
    assert not [event for event in state.events if event.kind == "analysis_incomplete"]


def test_constant_timing_register_loop_matches_concrete_queue_timing() -> None:
    text = (Path(__file__).resolve().parents[1] / "examples/concrete-register-execution/counter.q1asm").read_text()
    states = [interpret_program(parse_q1asm(text), sequencer_id="seq0", execution_mode=mode)
              for mode in ("analysis", "concrete")]
    timings = []
    for state in states:
        packets = list(state.rt_packets)
        result = analyze_underflow(state)

        assert result.status == "not_detected_under_current_assumptions"
        assert not [item for item in state.diagnostics if item.category in UNDERFLOW_CATEGORIES | {"analysis_incomplete"}]
        assert state.rt_packets == packets
        timings.append([(event.meta["enqueue_ns"], event.meta["dequeue_ns"])
                        for event in state.events if event.kind == "slack"])

    assert len(timings[0]) == 4  # Synchronization and all three waits are checked.
    assert timings[0] == timings[1]
    assert len(states[0].rt_packets) == 2  # The displayed loop remains compact.
    assert len(states[1].rt_packets) == 4


def test_register_copies_and_aliases_do_not_change_constant_loop_timing() -> None:
    text = (".DEF VALUE R1\n.DEF COPY R2\n.DEF COUNTER R0\n"
            "move 3,$COUNTER\nmove 0,$VALUE\nnop\n"
            "body: add $VALUE,1,$VALUE\nnop\nmove $VALUE,$COPY\n"
            "nop\nnot $COPY,$COPY\nnop\nwait 100\nloop $COUNTER,@body\nstop\n")
    state, result = _analyze(text)

    assert result.status == "not_detected_under_current_assumptions"
    assert len([event for event in state.events if event.kind == "slack"]) == 3
    assert not [item for item in state.diagnostics if item.category == "analysis_incomplete"]


@pytest.mark.parametrize("duration", ["R1", "$DURATION", "{R1}", "{DURATION}"])
@pytest.mark.parametrize("operation", ["wait", "upd_param"])
def test_loop_invariant_register_duration_matches_concrete_queue_timing(duration, operation) -> None:
    text = (".DEF DURATION R1\nmove 3,R0\nmove 100,R1\nmove 0,R2\nnop\n"
            f"body: add R2,1,R2\nnop\n{operation} {duration}\nloop R0,@body\nstop\n")
    states = [interpret_program(parse_q1asm(text), sequencer_id="seq0", execution_mode=mode)
              for mode in ("analysis", "concrete")]
    timings = []
    for state in states:
        assert analyze_underflow(state).status == "not_detected_under_current_assumptions"
        timings.append([(event.meta["enqueue_ns"], event.meta["dequeue_ns"])
                        for event in state.events if event.kind == "slack"])

    assert len(timings[0]) == 3
    assert timings[0] == timings[1]


def test_changing_output_payload_does_not_change_constant_loop_timing() -> None:
    text = ("move 3,R0\nmove 0,R1\nmove 1,R2\nnop\nbody: add R1,1,R1\nnop\n"
            "set_awg_gain R1,R2\nplay R1,R2,100\nloop R0,@body\nstop\n")
    state, result = _analyze(text)

    assert result.status == "not_detected_under_current_assumptions"
    assert len([event for event in state.events if event.kind == "slack"]) == 3


@pytest.mark.parametrize("duration", ["R0", "$COUNTER", "{R0}", "{COUNTER}"])
def test_loop_counter_duration_stays_incomplete_even_through_aliases(duration) -> None:
    state, result = _analyze(f".DEF COUNTER R0\nmove 3,R0\nbody: wait {duration}\nloop R0,@body\nstop\n")

    assert result.status == "analysis_incomplete"
    assert any(item.category == "analysis_incomplete" for item in state.diagnostics)


@pytest.mark.parametrize("body", [
    "add R0,1,R0\nnop\nwait 100",
    "move R1,$COUNTER\nnop\nwait 100",
    "mulu32 R1,4294967295,R2,$COUNTER\nnop\nwait 100",
    "add R1,1,R1\nnop\njlt R1,2,@skip\nwait 4\nskip: wait 100",
    "move 2,R2\nnop\ninner: wait 100\nloop R2,@inner",
])
def test_timing_dependencies_and_control_flow_keep_compact_loop_incomplete(body: str) -> None:
    text = (".DEF VALUE R1\n.DEF COUNTER R0\nmove 3,R0\nmove 100,R1\nnop\n"
            f"body: {body}\nloop R0,@body\nstop\n")
    state, result = _analyze(text)

    assert result.status == "analysis_incomplete"
    assert any(item.category == "analysis_incomplete"
               and item.details.get("reason") == "compact_loop_timing_unexpanded" for item in state.diagnostics)
    assert not [item for item in state.diagnostics if item.category in UNDERFLOW_CATEGORIES]


def test_invalid_zero_loop_count_does_not_report_the_prefix_as_complete() -> None:
    state, result = _analyze("move 0,R0\nbody: wait 100\nloop R0,@body\nstop\n")

    assert any(item.category == "invalid_argument_value" for item in state.diagnostics)
    assert result.status == "analysis_incomplete"
    assert any(item.details.get("reason") == "invalid_loop_count" for item in state.diagnostics)


@pytest.mark.parametrize("tail,reason", [
    ("jmp R7\nstop", "register_branch_target"),
    ("jmp 999\nstop", "unknown_numeric_branch_target"),
])
def test_unresolved_control_after_a_real_packet_is_an_incomplete_queue_boundary(tail, reason):
    state, result = _analyze(f"wait 100\n{tail}\n")

    assert result.status == "analysis_incomplete"
    assert any(item.category == "analysis_incomplete" and item.details.get("reason") == reason
               for item in state.diagnostics)
    assert len([event for event in state.events if event.kind == "slack"]) == 1


def test_unresolved_target_before_any_packet_does_not_hide_potential_rt_commands():
    state, result = _analyze("jmp R7\nwait 100\nstop\n")

    assert not state.rt_packets
    assert result.status == "analysis_incomplete"
    assert any(item.details.get("reason") == "register_branch_target" for item in state.diagnostics)


def test_unexpanded_outer_loop_keeps_a_proved_first_iteration_queue_failure():
    source = ("wait_sync 4\nmove 2,R0\nouter: move 8192,R1\ninner: wait 4\n"
              + "nop\n" * 64 + "loop R1,@inner\nloop R0,@outer\nstop\n")
    state, result = _analyze(source)

    assert result.status == "definite_underflow"
    assert any(event.kind == "underflow_warning" for event in state.events)


@pytest.mark.parametrize("operation", ["play_netzero 0,100"])
@pytest.mark.parametrize("prefix", ["", "wait 100\n"])
def test_unmodeled_timing_effect_is_an_incomplete_queue_boundary(operation, prefix):
    state, result = _analyze(f"{prefix}{operation}\nwait 100\nstop\n")

    assert result.status == "analysis_incomplete"
    assert any(item.details.get("reason") == "unmodeled_timing_effect" for item in state.diagnostics)
    assert len([event for event in state.events if event.kind == "slack"]) == bool(prefix)


@pytest.mark.parametrize("operation", ["mystery", "jmp @missing"])
@pytest.mark.parametrize("prefix", ["", "wait 100\n"])
def test_excluded_source_line_does_not_invalidate_remaining_queue_analysis(operation, prefix):
    state, result = _analyze(f"{prefix}{operation}\nwait 100\nstop\n")

    assert result.status == "not_detected_under_current_assumptions"
    assert any(item.details.get("source_line_skipped") for item in state.diagnostics)
    assert not any(item.details.get("reason") == "unmodeled_timing_effect" for item in state.diagnostics)
    assert len([event for event in state.events if event.kind == "slack"]) == 1 + bool(prefix)


@pytest.mark.parametrize("duration,nops", [(20, 0), (100, 0), (100, 20), (1000, 64)])
def test_infinite_single_wait_with_sustainable_supply_cadence_is_proved(duration, nops):
    source = f"wait 100\nbody: wait {duration}\n" + "nop\n" * nops + "jmp @body\n"
    state, result = _analyze(source)

    assert result.status == "not_detected_under_current_assumptions"
    cadence = state.metadata["underflow"]["infinite_cadence"]
    assert cadence["q1_period_ns"] == 20 + 4 * nops
    assert cadence["rt_period_ns"] == duration
    assert cadence["tail_status"] == "sustainable"
    assert not any(event.kind == "analysis_incomplete" for event in state.events)


@pytest.mark.parametrize("nops", [0, 20, 64])
def test_infinite_unsustainable_supply_has_eventual_underflow_without_invented_failure_time(nops):
    state, result = _analyze("body: wait 4\n" + "nop\n" * nops + "jmp @body\n")

    assert result.status == "definite_underflow"
    warning = next(event for event in state.events if event.kind == "underflow_warning")
    assert warning.meta["eventual"] is True
    assert warning.meta["q1_period_ns"] == 20 + nops * 4
    assert warning.meta["rt_period_ns"] == 4
    assert isinstance(warning.t0, Unknown)
    assert "slack_ns" not in warning.meta
    assert state.metadata["underflow"]["initial_buffer"]["required_unbounded"] is True


@pytest.mark.parametrize("body", [
    "wait R1", "wait 0", "wait 100\nwait 100", "wait 100\nset_mrk 1", "wait_trigger 1,100",
])
def test_infinite_cadence_does_not_certify_unproved_or_unsupported_bodies(body):
    state, result = _analyze(f"body: {body}\njmp @body\n")

    assert result.status == "analysis_incomplete"
    assert "infinite_cadence" not in state.metadata["underflow"]


def test_infinite_cadence_does_not_certify_sync_or_multiple_sequencers():
    synced, result = _analyze("wait_sync 4\nbody: wait 100\njmp @body\n")
    assert result.status == "analysis_incomplete"
    from q1timeline.analysis.underflow import analyze_underflows
    loop = interpret_program(parse_q1asm("body: wait 100\njmp @body\n"), sequencer_id="loop")
    peer = interpret_program(parse_q1asm("wait 100\nstop\n"), sequencer_id="peer")
    assert analyze_underflows([loop, peer])[0].status == "analysis_incomplete"


@pytest.mark.parametrize("prefix,operand,params", [
    ("move 100,R1\nnop\n", "R1", {}),
    (".DEF DURATION 100\n", "$DURATION", {}),
    ("", "$DURATION", {"DURATION": 100}),
    ("move 100,R1\nnop\n", "{R1}", {}),
])
def test_infinite_cadence_accepts_proved_stable_duration_inputs(prefix, operand, params):
    state = interpret_program(parse_q1asm(f"{prefix}body: wait {operand}\njmp @body\n"),
                              sequencer_id="seq0", params=params)
    assert analyze_underflow(state).status == "not_detected_under_current_assumptions"
    assert state.metadata["underflow"]["infinite_cadence"]["rt_period_ns"] == 100


def test_infinite_cadence_does_not_repeat_a_stale_pending_duration_read():
    state, result = _analyze("move 100,R1\nnop\nmove 200,R1\nbody: wait {R1}\njmp @body\n")
    assert any(item.category == "register_not_ready" for item in state.diagnostics)
    assert result.status == "analysis_incomplete"
    assert "infinite_cadence" not in state.metadata["underflow"]


def test_infinite_cadence_can_be_reanalyzed_after_its_own_eventual_error():
    state, first = _analyze("body: wait 4\njmp @body\n")
    assert first.status == analyze_underflow(state).status == "definite_underflow"


def test_both_finite_queue_paths_can_be_safe_without_one_proven_q1_arrival():
    state, result = _analyze("wait 100\njge R7,1,@other\nwait 100\njmp @join\n"
                             "other: wait 100\njoin: wait 40\nstop\n")
    assert result.status == "not_detected_under_current_assumptions"
    assert isinstance(state.q1_time_ns, Unknown)
    assert set(state.metadata["underflow"]["all_paths_safe"]["required_buffer_by_path_ns"]) == {"taken", "fallthrough"}
    assert state.metadata["underflow"]["initial_buffer"]["required_basis"] == "all_feasible_paths"
    assert len([event for event in state.events if event.kind == "slack"]) == 1
    assert not any(event.kind == "analysis_incomplete" for event in state.events)


def test_all_path_queue_safety_does_not_make_different_suffix_durations_one_exact_timeline():
    state, result = _analyze("jge R7,1,@other\nmove 40,R1\nnop\njmp @join\n"
                             "other: move 80,R1\nnop\njoin: wait R1\nstop\n")
    assert result.status == "not_detected_under_current_assumptions"
    assert isinstance(state.rt_time_ns, Unknown)
    assert isinstance(state.q1_time_ns, Unknown)
    assert not any(event.kind == "slack" for event in state.events)


def test_all_path_queue_certificate_requires_every_trace_safe_under_both_reserves(monkeypatch):
    import q1timeline.analysis.underflow as underflow
    monkeypatch.setattr(underflow, "ASSUMED_INITIAL_BUFFER_NS", 0)
    monkeypatch.setattr(underflow, "POSSIBLE_INITIAL_BUFFER_NS", 0)
    state, result = _analyze("wait 4\njge R7,1,@other\nwait 100\njmp @join\n"
                             "other: wait 100\njoin: wait 40\nstop\n")
    assert state.queue_alternatives
    assert result.status == "analysis_incomplete"
    assert "all_paths_safe" not in state.metadata["underflow"]


def test_queue_capacity_backpressure_limits_q1_lookahead() -> None:
    text = "wait 1000\nwait 4\nwait 4\nwait 4\nnop\nnop\nnop\nnop\nupd_param 4\nstop\n"
    state = interpret_program(parse_q1asm(text, file="program.q1asm"), sequencer_id="seq0")
    roomy = replay_queues({"seq0": state.rt_packets}, queue_depth_limit=32)["seq0"]
    bounded = replay_queues({"seq0": state.rt_packets}, queue_depth_limit=2)["seq0"]

    assert roomy.failure is None
    assert bounded.failure is not None
    assert bounded.failure.packet.source.line == 9
    assert bounded.failure.packet.id == state.rt_packets[-1].id
    assert bounded.failure.slack_ns < 0


@pytest.mark.parametrize("operation", ["wait_sync R0", "wait_trigger R0,R1"])
def test_unknown_sync_or_trigger_timing_is_not_reported_as_safe(operation: str) -> None:
    state, result = _analyze(f"{operation}\nupd_param 4\nstop\n")

    assert result.status == "analysis_incomplete"
    assert any(event.kind == "analysis_incomplete" for event in state.events)
    assert any(item.category == "analysis_incomplete" for item in state.diagnostics)


@pytest.mark.parametrize("starving_fallthrough", [False, True])
def test_runtime_branch_cannot_prove_safe_or_starved_timing_from_one_path(starving_fallthrough: bool) -> None:
    safe = "wait 1000\n"
    starving = "nop\n" * 30 + "upd_param 4\n"
    fallthrough, taken = (starving, safe) if starving_fallthrough else (safe, starving)
    text = f"wait 100\njge R0,1,@danger\n{fallthrough}stop\ndanger:\n{taken}stop\n"
    state, result = _analyze(text)

    assert result.status == "analysis_incomplete"
    assert not [event for event in state.events if event.kind == "underflow_warning"]
    assert any(event.kind == "analysis_incomplete" and event.meta.get("reason") == "runtime_branch_unknown"
               for event in state.events)
    assert not [item for item in state.diagnostics if item.category in UNDERFLOW_CATEGORIES]


@pytest.mark.parametrize(
    "peer_tail,reason",
    [
        ("fb_pop_data 0,R0\nstop\n", "feedback_arrival_unknown"),
        ("move 2,R0\nmove 100,R1\nnop\nbody: wait R1\nmulu16 R7,2,R1\nloop R0,@body\nstop\n", "compact_loop_timing_unexpanded"),
    ],
)
def test_incomplete_peer_cannot_authorize_optimistic_sync_prefetch(peer_tail: str, reason: str) -> None:
    ir = analyze_documents({
        "main.q1asm": "wait_sync 4\nupd_param 4\nstop\n",
        "peer.q1asm": f"wait_sync 4\n{peer_tail}",
    })
    incomplete = [event for event in ir["events"] if event["kind"] == "analysis_incomplete"]

    assert {event["sequencer_id"] for event in incomplete} == {"main", "peer"}
    assert any(event["sequencer_id"] == "peer" and event["meta"].get("reason") == reason for event in incomplete)
    assert not [event for event in ir["events"] if event["kind"] == "underflow_warning"]
    assert not [item for item in ir["diagnostics"] if item["category"] in UNDERFLOW_CATEGORIES]


def test_queue_analysis_preserves_original_q1_and_rt_coordinates() -> None:
    text = "set_mrk 0\nwait_sync 4\nwait 1000\nwait 4\nwait 4\nwait 4\nnop\nnop\nnop\nupd_param 4\nstop\n"
    state = interpret_program(parse_q1asm(text, file="program.q1asm"), sequencer_id="seq0")
    packets = list(state.rt_packets)
    events = [(event.id, event.t0, event.t1) for event in state.events]

    analyze_underflow(state, queue_depth_limit=2)

    assert state.rt_packets == packets
    assert [(event.id, event.t0, event.t1) for event in state.events[:len(events)]] == events
