from __future__ import annotations

import pytest

from q1timeline.analysis.api import analyze_documents
from q1timeline.analysis.interpreter import interpret_program, prepare_def_program
from q1timeline.analysis.values import Concrete
from q1timeline.playback import PlaybackSession
from q1timeline.q1asm.linq_commands import decode_linq_command
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


# Qblox Instruments 1.4.1 q1asm_windows.exe -l error -d. These are the
# assembler output rows, with 17 substituted when decoding a register payload.
ASSEMBLER_ROWS = [
    ("fb_tdc_tags_id 17,4", 0x00, 17, [17, 4]),
    ("fb_tdc_tdelta_id R1,4", 0x01, 17, [17, 4]),
    ("upd_thres 0,R1,4", 0x10, 17, [0, 17, 4]),
    ("upd_thres 1,7,4", 0x11, 7, [1, 7, 4]),
    ("upd_thres 2,7,4", 0x12, 7, [2, 7, 4]),
    ("upd_thres 3,7,4", 0x13, 7, [3, 7, 4]),
    ("fb_llp_tags_id 17,4", 0x20, 17, [17, 4]),
    ("fb_llp_ttls_id R1,4", 0x21, 17, [17, 4]),
    ("fb_acq_tb_id 17,4", 0x40, 17, [17, 4]),
    ("fb_acq_iq_id R1,4", 0x41, 17, [17, 4]),
    ("fb_acq_tb_cfg 1,3,4,4", 0x42, 0x30401, [1, 3, 4, 4]),
    ("fb_acq_tb_cfg R1,4", 0x42, 0x30401, [1, 3, 4, 4]),
    ("fb_cmd 0x42,0x30401,4", 0x42, 0x30401, [1, 3, 4, 4]),
    ("fb_acq_tb_extra 0,65535,4", 0x43, 0xFFFF, [0, 0xFFFF, 4]),
    ("fb_acq_tb_valid 1,4", 0x5B, 1, [1, 4]),
    ("fb_acq_iq_shift R1,4", 0x5C, 17, [17, 4]),
    ("fb_acq_tb_mock 1,1,1,4", 0x5D, 0x111, [1, 1, 1, 4]),
    ("fb_com_cfg 0,3,4,4", 0x60, 0x30400, [0, 3, 4, 4]),
    ("fb_com_extra 0,65535,4", 0x61, 0xFFFF, [0, 0xFFFF, 4]),
    ("fb_com_data 42,R1,4", 0x12A, 17, [42, 17, 4]),
    ("fb_cmd 0x12a,R1,4", 0x12A, 17, [42, 17, 4]),
]


@pytest.mark.parametrize("source,command,payload,expected", ASSEMBLER_ROWS)
def test_assembler_lowering_golden_rows(source, command, payload, expected):
    named = source.split()[0]
    expected_op = {0x42: "fb_acq_tb_cfg", 0x12A: "fb_com_data"}[command] if named == "fb_cmd" else named
    assert decode_linq_command("fb_cmd", [command, payload, 4]) == (expected_op, expected)


def run(source, *, reader=None, prepared=False, **config):
    sources = {"tx": source, **({"rx": reader} if reader else {})}
    programs = {name: parse_q1asm(text, file=f"{name}.q1asm") for name, text in sources.items()}
    bindings = {name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                for name, program in programs.items()} if prepared else None
    return simulate_programs(
        programs, simulation={"startup_delay_ns": 100, **config}, prepared_programs=bindings,
        sequences={"tx": {"acquisitions": {"data": {"index": 0, "num_bins": 4}}}},
    )


def events(state, kind):
    return [event for event in state.events if event.kind == kind]


_MOCK_FORMS = ("fb_acq_tb_mock 1,1,1,4", "fb_acq_tb_mock R1,4", "fb_cmd 0x5d,0x111,4", "fb_cmd 0x5d,0x111,1")


@pytest.mark.parametrize("operation", _MOCK_FORMS)
def test_unresolved_mock_leaves_analytical_rt_suffix_unknown(operation):
    source = f"move 0x111,R1\nnop\n{operation}\nwait 8\nstop"
    state = interpret_program(parse_q1asm(source, file="mock.q1asm"), sequencer_id="tx")
    mock = next(event for event in events(state, "unknown_region") if event.source.line == 3)
    assert mock.source.raw == operation
    assert mock.t0 == Concrete(0) and mock.t1 is None
    wait, = events(state, "wait")
    assert not isinstance(wait.t0, Concrete)
    assert all(isinstance(event.t0, Concrete) for event in events(state, "q1_issue"))
    assert any(item.category == "analysis_incomplete" and "duration versus count" in item.message
               and item.source.line == 3 for item in state.diagnostics)


@pytest.mark.parametrize("operation", _MOCK_FORMS)
@pytest.mark.parametrize("execute", [False, True])
def test_mock_execution_limitation_is_applied_after_rt_condition(operation, execute):
    source = (f"move 0x111,R1\nnop\nset_latch_en 1,4\nset_cond 1,1,0,8\n{operation}\n"
              "set_cond 0,0,0,4\nwait 8\nstop")
    tx, = run(source, inputs={"triggers": [{"target": "tx", "time_ns": 101, "address": 1}] if execute else []})
    outcome = tx.metadata["execution"]
    issue = next(event for event in events(tx, "q1_issue") if event.source.raw == operation)
    assert issue.duration == Concrete(4)
    assert outcome["status"] == ("blocked" if execute else "completed")
    assert not events(tx, "feedback_send")
    if execute:
        assert "duration versus count" in outcome["reason"]
        assert any(item.source.line == 5 and "duration versus count" in item.message for item in tx.diagnostics)
    else:
        skipped, = events(tx, "conditional_skip")
        assert (skipped.t0, skipped.t1) == (Concrete(104), Concrete(112))
        assert events(tx, "wait")[0].t0 == Concrete(112)

    play = PlaybackSession({"tx": ("tx.q1asm", source)}, external_inputs={"triggers": [
        {"id": "trigger", "address": 1, "targets": ["tx"], "times_ns": [1] if execute else []},
    ]})
    result = play.run(budget_ms=1000)
    assert result["status"] == ("blocked" if execute else "completed")
    if execute:
        assert "duration versus count" in result["reason"]


def test_invalid_mock_signature_keeps_its_validation_diagnostic():
    tx, = run("fb_acq_tb_mock 1,1,4\nwait 8\nstop")
    assert not any(event.meta.get("op") == "fb_acq_tb_mock" for event in events(tx, "q1_issue"))
    assert any(item.severity == "error" and item.source.line == 1 for item in tx.diagnostics)
    assert not any("duration versus count" in item.message for item in tx.diagnostics)


def test_matching_else_duration_does_not_make_the_unresolved_mock_exact():
    source = "set_cond 1,1,0,4\nfb_acq_tb_mock 1,1,1,4\nset_cond 0,0,0,4\nwait 8\nstop"
    state = interpret_program(parse_q1asm(source), sequencer_id="tx")
    wait, = events(state, "wait")
    assert not isinstance(wait.t0, Concrete)


@pytest.mark.parametrize("prepared", [False, True])
def test_raw_common_data_message_matches_named_command_and_keeps_links(prepared):
    observed = []
    for op in ("fb_com_data 42,R1,4", "fb_cmd 0x12a,R1,4"):
        sender, reader = run(f"move 0xDEADBEEF,R1\nnop\n{op}\nstop", reader="wait 0\nfb_pull_data R0,R1\nstop",
                             prepared=prepared, routes=[{"source": "tx", "target": "rx", "id": 42, "latency_ns": 12}])
        assert reader.registers == {"R0": Concrete(42), "R1": Concrete(0xDEADBEEF)}
        packet, = sender.rt_packets
        issue = next(event for event in events(sender, "q1_issue") if event.id == packet.meta["q1_issue_event_id"])
        assert packet.op == issue.meta["op"] == op.split()[0]
        assert packet.source == issue.source and packet.source.raw == op
        if packet.op == "fb_cmd":
            assert packet.meta["normalized_operand_snapshot"] == [42, 0xDEADBEEF, 4]
            assert issue.meta["normalized_op"] == "fb_com_data"
        if not prepared:
            send, = events(sender, "feedback_send")
            assert send.source == packet.source
            assert send.meta["source_packet_id"] == packet.id
            assert send.meta["q1_issue_event_id"] == issue.id
            observed.append((send.t0, send.meta["id"], send.meta["value"], send.meta["targets"]))
    if not prepared:
        assert observed == [(Concrete(100), 42, 0xDEADBEEF, ["rx"])] * 2


@pytest.mark.parametrize("config", ["fb_acq_tb_cfg 1,2,1,4", "fb_acq_tb_cfg R1,4", "fb_cmd 0x42,R1,4"])
def test_tb_and_iq_aliases_configure_the_same_acquisition(config):
    source = (f"move 0x20101,R1\nnop\nfb_cmd 0x40,16,4\n{config}\nfb_cmd 0x5b,1,4\n"
              "fb_cmd 0x41,17,4\nfb_cmd 0x5c,8,4\nacquire 0,0,20\nstop")
    tx, rx = run(source, reader="wait 0\nfb_pop_data 17,R1\nnop\nfb_pop_data 17,R2\nnop\nfb_pop_data 16,R0\nstop",
                 routes=[{"source": "tx", "target": "rx", "latency_ns": 4}],
                 inputs={"acquisitions": [{"sequencer": "tx", "acquisition_index": 0, "bin_index": 0,
                                           "i": 0x1234, "q": -256, "thresholded": 1}]})
    assert tx.metadata["execution"]["status"] == rx.metadata["execution"]["status"] == "completed"
    assert rx.registers == {"R0": Concrete(12), "R1": Concrete(18), "R2": Concrete(0xFFFFFFFF)}
    assert [event.meta["value"] for event in events(tx, "feedback_contribution")] == [3]
    packet = tx.rt_packets[1]
    if config != "fb_acq_tb_cfg 1,2,1,4":
        assert packet.meta["normalized_op"] == "fb_acq_tb_cfg"
        assert packet.meta["normalized_operand_snapshot"] == [1, 2, 1, 4]


@pytest.mark.parametrize("threshold", ["upd_thres 1,R1,4", "fb_cmd 0x11,R1,4"])
def test_raw_qtm_threshold_and_count_stream_use_existing_rt_effects(threshold):
    source = (f"move 1,R1\nnop\nset_mrk 3\n{threshold}\nfb_cmd 0x10,0,4\nfb_cmd 0x21,16,4\n"
              "acquire_timetags 0,0,1,0,16\nacquire_timetags 0,0,0,0,4\nstop")
    tx, rx = run(source, reader="wait 0\nfb_pop_data 16,R0\nstop",
                 routes=[{"source": "tx", "target": "rx", "latency_ns": 4}],
                 inputs={"digital": {"tx": {"initial_level": 0, "transitions": [{"time_ticks": 116 * 128, "level": 1}]}}})
    assert tx.metadata["execution"]["status"] == rx.metadata["execution"]["status"] == "completed"
    assert rx.registers["R0"] == Concrete(1)
    result, = tx.metadata["simulation"]["acquisitions"]
    assert result["classification"] == "high"
    packet = tx.rt_packets[0]
    issue = next(event for event in events(tx, "q1_issue") if event.id == packet.meta["q1_issue_event_id"])
    assert issue.duration == Concrete(8)
    assert packet.meta["applied_state"]["marker"] == 3


@pytest.mark.parametrize("op,status,reason", [
    ("fb_cmd 3,1,4", "blocked", "LINQ command 0x3"),
    ("fb_cmd 0x42,0x80,4", "error", "reserved bits"),
    ("fb_cmd 0x43,0x20000,4", "error", "reserved bits"),
    ("fb_cmd 0x41,256,4", "error", "feedback_acquisition_channel 256"),
    ("fb_cmd 0x12,1,4", "blocked", "Count threshold index 2"),
    ("fb_cmd 0x5d,0x111,4", "blocked", "duration versus count"),
    ("fb_cmd 0x61,0x10001,4", "blocked", "extra bytes"),
    ("move 0x80,R1\nnop\nfb_com_cfg R1,4", "error", "reserved bits"),
])
def test_raw_unverified_behavior_and_invalid_decoded_fields(op, status, reason):
    state, = run(f"{op}\nstop")
    execution = state.metadata["execution"]
    assert execution["status"] == status
    assert reason in execution["reason"]
    assert execution["source"]["line"] == len(op.splitlines())
    assert not events(state, "feedback_send")


@pytest.mark.parametrize("op", ["fb_cmd 0x12a,7,4", "fb_cmd 3,1,4", "fb_cmd 0x42,0x80,4"])
def test_raw_conditional_skip_has_no_linq_effect(op):
    state, = run(f"set_cond 1,1,0,12\n{op}\nstop")
    assert state.metadata["execution"]["status"] == "completed"
    assert not events(state, "feedback_send")
    skipped, = events(state, "conditional_skip")
    packet, = state.rt_packets
    assert skipped.duration == Concrete(12)
    assert skipped.meta["rt_packet_id"] == packet.id
    assert skipped.source == packet.source
    assert packet.op == "fb_cmd" and packet.meta["conditional_skipped"]


def test_static_unknown_raw_command_does_not_invent_a_feedback_message():
    program = parse_q1asm("fb_cmd 3,7,4\nfb_cmd 0x12a,7,4\nstop")
    state = interpret_program(program, sequencer_id="tx")
    feedback, = events(state, "feedback_com")
    assert feedback.meta["feedback"]["channel"] == "42"
    assert feedback.meta["normalized_op"] == "fb_com_data"
    raw, = events(state, "fb_cmd")
    assert raw.source.line == 1 and "no verified behavior" in raw.meta["reason"]


@pytest.mark.parametrize("op", ["upd_thres 1,3,4", "fb_cmd 0x11,3,4"])
def test_unresolved_conditional_threshold_marks_latched_state_unknown(op):
    program = parse_q1asm(f"set_cond 1,1,0,4\n{op}\nstop")
    state = interpret_program(program, sequencer_id="tx")
    region, = events(state, "unknown_region")
    packet, = state.rt_packets
    assert region.meta["output_state_unknown"] is packet.meta["output_state_unknown"] is True


def test_compiled_jump_to_raw_linq_packet_preserves_shared_route_times():
    ir = analyze_documents({
        "tx.q1asm": "jmp 3\nloop R1,@end\nfb_cmd 0x12a,7,4\nend: stop",
        "rx.q1asm": "nop\n" * 6 + "wait 100\nstop",
    }, execution_mode="simulation", simulation={"routes": [
        {"source": "tx", "target": "rx", "id": 42, "latency_ns": 60},
    ]})
    by_id = {event["id"]: event for event in ir["events"]}
    flow, = ir["feedback_flows"]
    send, receive = (by_id[flow[key]] for key in ("from_event_id", "to_event_id"))
    assert send["source"]["line"] == 3
    assert send["meta"]["aligned_t0"] == 0 and receive["meta"]["aligned_t0"] == 60
    issue = by_id[send["meta"]["q1_issue_event_id"]]
    assert (issue["meta"]["source_pc"], issue["meta"]["compiled_pc"]) == (2, 3)


def test_async_raw_linq_failure_uses_the_packet_instruction_pc():
    state, = run("jmp 3\nloop R1,@end\nfb_cmd 3,1,4\nend: stop")
    execution = state.metadata["execution"]
    assert execution["status"] == "blocked"
    assert (execution["source"]["line"], execution["pc"], execution["compiled_pc"]) == (3, 2, 3)
