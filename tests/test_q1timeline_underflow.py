import pytest

from q1timeline.analysis.api import analyze_documents, analyze_text
from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.underflow import analyze_underflow
from q1timeline.q1asm.parser import parse_q1asm


UNDERFLOW_CATEGORIES = {"definite_underflow", "possible_underflow"}


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
    state, result = _analyze(f"{prefix}upd_param 4\nupd_param 4\nupd_param 4\nstop\n")

    assert result.status == "not_detected_under_current_assumptions"
    assert not [event for event in state.events if event.kind == "underflow_warning"]
    assert any(event.kind == "slack" and event.meta["slack_ns"] == 0 for event in state.events)


@pytest.mark.parametrize("prefix", ["", "wait_sync 4\n"])
@pytest.mark.parametrize("operation", ["upd_param 4", "wait 4"])
def test_short_housekeeping_starvation_depends_on_sync_prefill(prefix: str, operation: str) -> None:
    text = f"{prefix}{operation}\nnop\nnop\n{operation}\nstop\n"
    state, result = _analyze(text)
    warnings = [event for event in state.events if event.kind == "underflow_warning"]
    starved_packet = state.rt_packets[-1]

    if prefix:
        assert result.status == "analysis_incomplete"
        assert not warnings
        incomplete = [event for event in state.events if event.kind == "analysis_incomplete"]
        assert any(event.meta.get("reason") == "sync_start_uncertain" for event in incomplete)
        return
    assert result.status in UNDERFLOW_CATEGORIES
    assert len(warnings) == 1
    assert warnings[0].meta["rt_packet_id"] == starved_packet.id
    assert warnings[0].source.line == len(prefix.splitlines()) + 4
    assert warnings[0].meta["slack_ns"] < 0
    assert warnings[0].t0 == starved_packet.rt_t0


@pytest.mark.parametrize("operation", ["upd_param 4", "wait 4"])
def test_sustained_post_sync_starvation_exhausts_even_a_full_prefilled_queue(operation: str) -> None:
    text = "wait_sync 4\n" + ("nop\n" * 8 + f"{operation}\n") * 48 + "stop\n"
    state, result = _analyze(text)
    warnings = [event for event in state.events if event.kind == "underflow_warning"]

    assert result.status in UNDERFLOW_CATEGORIES
    assert warnings
    assert all(event.source.line > 1 for event in warnings)
    assert all(event.meta["slack_ns"] < 0 for event in warnings)
    assert all(event.meta["rt_packet_id"] in {packet.id for packet in state.rt_packets[1:]} for event in warnings)




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


def test_tight_concrete_loop_starves_on_its_second_iteration_without_sync() -> None:
    state = interpret_program(
        parse_q1asm("move 2,R0\nbody: upd_param 4\nloop R0,@body\nstop\n", file="loop.q1asm"),
        sequencer_id="seq0", execution_mode="concrete",
    )
    result = analyze_underflow(state)
    warnings = [event for event in state.events if event.kind == "underflow_warning"]

    assert len(state.rt_packets) == 2
    assert result.status in UNDERFLOW_CATEGORIES
    assert len(warnings) == 1
    assert warnings[0].meta["rt_packet_id"] == state.rt_packets[1].id
    assert warnings[0].source.line == 2
    assert warnings[0].meta["slack_ns"] < 0


def test_queue_capacity_backpressure_limits_q1_lookahead() -> None:
    text = "wait 1000\nwait 4\nwait 4\nwait 4\nnop\nnop\nnop\nnop\nupd_param 4\nstop\n"
    roomy, roomy_result = _analyze(text, queue_depth_limit=32)
    bounded, bounded_result = _analyze(text, queue_depth_limit=2)

    assert roomy_result.status == "not_detected_under_current_assumptions"
    assert not [event for event in roomy.events if event.kind == "underflow_warning"]
    assert bounded_result.status in UNDERFLOW_CATEGORIES
    warnings = [event for event in bounded.events if event.kind == "underflow_warning"]
    assert len(warnings) == 1
    assert warnings[0].source.line == 9
    assert warnings[0].meta["rt_packet_id"] == bounded.rt_packets[-1].id
    assert warnings[0].meta["slack_ns"] < 0
    assert bounded_result.assumptions["queue_depth_limit"] == 2


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
        ("move 2,R0\nbody: wait 100\nadd R1,1,R1\nloop R0,@body\nstop\n", "compact_loop_timing_unexpanded"),
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
