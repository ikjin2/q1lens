"""Check Concrete device issues against the single-sequencer event-driven simulator."""

import pytest

from q1timeline.analysis.interpreter import interpret_program
from q1timeline.q1asm import core
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation import runtime


def run_both(source, *, triggers=(), feedback=(), startup_ns=4, digital=None, capacity=None):
    """Send identical receiver-side arrivals (absolute ns) to the two public engines."""
    program = parse_q1asm(source)
    queue_config = {} if capacity is None else {"feedback_queue_capacity": capacity}
    external = core.ExternalInputs(trigger_arrivals=tuple(triggers), feedback_arrivals=tuple(feedback), **queue_config)
    inputs = {
        "triggers": [{"target": "s", "time_ns": t, "address": address} for t, address in triggers],
        "feedback": [{"target": "s", "time_ns": t, "id": identifier, "value": word}
                     for t, identifier, word in feedback],
    }
    if digital is not None:
        inputs["digital"] = {"s": digital}
    concrete = interpret_program(program, sequencer_id="s", execution_mode="concrete", external=external)
    simulated, = runtime.simulate_programs(
        {"s": program}, simulation={"inputs": inputs, "startup_delay_ns": startup_ns, **queue_config},
        sequences={"s": {"acquisitions": {"bins": {"index": 0, "num_bins": 4}}}},
    )
    return concrete, simulated


def issue_rows(state):
    return [(e.meta["source_pc"], e.meta.get("compiled_pc"), e.meta["op"], e.t0, e.t1,
             e.meta["register_reads"], e.meta["register_snapshot"], e.meta["flags_before"], e.meta["flags_after"])
            for e in state.events if e.kind == "q1_issue"]


def check_completed(body, *, triggers=(), feedback=(), digital=None):
    # A shared trigger after both RT origins synchronizes the receiver clocks.
    # Its 64 ns dwell allows the short device programs to fill their queues.
    source = "wait_trigger 15,64\n" + body
    concrete, simulated = run_both(source, triggers=((16, 15), *triggers), feedback=feedback, digital=digital)
    for state in (concrete, simulated):
        assert state.metadata["execution"]["status"] == "completed", state.metadata["execution"]
    assert issue_rows(concrete) == issue_rows(simulated)
    assert concrete.registers == simulated.registers
    assert [(w.register, w.value, w.time, w.source) for w in concrete.register_history] == [
        (w.register, w.value, w.time, w.source) for w in simulated.register_history
    ]
    assert concrete.metadata["execution"]["stop_code"] == simulated.metadata["execution"]["stop_code"]
    assert concrete.rt_time_ns == simulated.rt_time_ns
    for index, (a, b) in enumerate(zip(concrete.rt_packets, simulated.rt_packets, strict=True)):
        assert (a.source, a.op, a.q1_issue_t0, a.q1_issue_t1) == (b.source, b.op, b.q1_issue_t0, b.q1_issue_t1)
        if index == 0:
            assert a.op == b.op == "wait_trigger"
            assert (a.rt_t0.value, b.rt_t0.value, a.rt_t1.value, b.rt_t1.value) == (0, 4, 80, 80)
        else:
            assert (a.rt_t0, a.rt_t1, a.duration) == (b.rt_t0, b.rt_t1, b.duration), a.source.raw
        assert bool(a.meta.get("conditional_skipped")) == bool(b.meta.get("conditional_skipped"))
        if a.op in core.LOCAL_OPS or a.meta.get("conditional_skipped"):
            continue
        assert a.meta["operands"] == b.meta["operand_snapshot"]
        assert a.meta["normalized_op"] == b.meta.get("normalized_op", b.op)
        assert a.meta["device_operands"] == b.meta.get("normalized_operand_snapshot", b.meta["operand_snapshot"])
    return concrete, simulated


FIFO_PROGRAM = """fb_com_data 31,305419896,8
fb_pop_data 19,R5
nop
fb_pull_data R6,R7
nop
jlt R6,32,@bad
stop R7
bad: stop 6"""

QTM_PROGRAM = """set_time_ref
reset_netzero
set_mrk 5
upd_thres 0,2,8
fb_cmd 0x11,5,8
acquire_digital 0,1,8
acquire_ttl 0,2,1,8
acquire_ttl 0,2,0,8
acquire_timetags 0,3,1,64,8
acquire_timetags 0,3,0,96,8
play_netzero 4,8
stop"""

TRIGGER_PROGRAM = """set_latch_en 1,8
set_cond 1,1,0,12
wait 4
latch_rst 8
wait 4
set_cond 0,0,0,0
wait_trigger 3,8
stop"""

UNVERIFIED = ("sw_req 1", "play_pulse 0,4", "fb_acq_tb_mock 1,1,1,4")


def test_selective_receive_and_next_fifo_word_drive_the_same_branch_and_exit_code():
    feedback = ((0, 8, 91), (2, 19, 0x12345678), (4, 33, 0xFFFFFFFD))
    concrete, simulated = check_completed(FIFO_PROGRAM, feedback=feedback)
    assert concrete.metadata["execution"]["stop_code"] == -3
    send, = [p for p in concrete.rt_packets if p.op == "fb_com_data"]
    sent, = [e for e in simulated.events if e.kind == "feedback_send"]
    assert (send.rt_t0, *send.meta["device_operands"][:2]) == (sent.t0, sent.meta["id"], sent.meta["value"])
    received = [(e.meta["arrival_ns"], e.meta["id"], e.meta["value"]) for e in concrete.events if e.kind == "feedback_pop"]
    inputs = {e.id: e for e in simulated.events if e.kind == "feedback_receive"}
    consumed = [inputs[e.meta["input_event_ids"][0]] for e in simulated.events
                if e.kind == "q1_issue" and e.meta["op"] in {"fb_pull_data", "fb_pop_data"}]
    assert received == [(e.t0.value, e.meta["id"], e.meta["value"]) for e in consumed] == list(feedback[1:])


def test_trigger_arrival_selects_a_packet_and_reset_selects_the_else_duration():
    concrete, simulated = check_completed(TRIGGER_PROGRAM, triggers=((84, 1), (129, 3)))
    waits = [p for p in concrete.rt_packets if p.op == "wait"]
    assert [(p.rt_t0.value, p.rt_t1.value, bool(p.meta.get("conditional_skipped"))) for p in waits] == [
        (88, 92, False), (100, 112, True)
    ]
    arrivals = {e.id: e for e in simulated.events if e.kind == "trigger_receive"}
    for a, b in zip(concrete.rt_packets, simulated.rt_packets, strict=True):
        if a.op == "wait_trigger":
            expected = a.meta["trigger_assumption"]
            actual = arrivals[b.meta["input_event_ids"][0]]
            assert (expected["address"], expected["arrival_ns"], expected["resume_ns"]) == (
                actual.meta["address"], actual.t0.value, b.rt_t1.value,
            )


def test_qtm_capture_one_shots_thresholds_and_gate_operands(monkeypatch):
    final = []
    step = core.step

    def capture_stop(*args, **kwargs):
        result = step(*args, **kwargs)
        if result.state.halted:
            final.append(result.state.devices)
        return result

    monkeypatch.setattr(core, "step", capture_stop)
    concrete, simulated = check_completed(QTM_PROGRAM, digital={"initial_level": 1, "transitions": []})
    devices, = final
    first, second = [p for p in concrete.rt_packets if p.meta.get("normalized_op") == "upd_thres"]
    committed, following = [e for e in simulated.events if e.kind == "upd_thres"]
    assert first.meta["applied_state"] == {"time_ref": True, "netzero_reset": True, "marker": 5}
    assert committed.meta["netzero_reset"] and committed.meta["applied_state"]["marker"] == 5
    assert devices.time_ref_ns * 128 == committed.meta["time_reference_ticks"]
    assert "applied_state" not in second.meta
    assert "time_reference_ticks" not in following.meta and "netzero_reset" not in following.meta
    # Only Simulation models the samples. Compare the gate addresses, thresholds
    # and fine-delay requests carried into its results with the concrete issues.
    commands = [p for p in concrete.rt_packets if p.op.startswith("acquire")]
    results = [e for e in simulated.events if e.kind == "acquisition_result"]
    expected = [(p.op, *p.meta["device_operands"][:2]) for p in commands
                if p.op == "acquire_digital" or p.meta["device_operands"][2] == 0]
    assert expected == [(e.meta["kind"], e.meta["acquisition_index"], e.meta["bin_index"]) for e in results]
    tags = results[-1].meta
    assert [p.meta["device_operands"][3] for p in commands if p.op == "acquire_timetags"] == [
        tags["open_delay_ticks"], tags["close_delay_ticks"],
    ]
    assert dict(devices.thresholds) == {0: tags["count_thresholds"]["low"], 1: tags["count_thresholds"]["high"]}
    pulse, = [p for p in concrete.rt_packets if p.op == "play_netzero"]
    scheduled, = [e for e in simulated.events if e.kind == "play_netzero"]
    assert pulse.meta["device_operands"][0] == scheduled.meta["maxt_ns"]


def test_packed_register_configurations_decode_the_same_fields():
    concrete, _ = check_completed(
        ".DEF WORD R0\nmove 0x20201,R0\nnop\nfb_acq_tb_cfg $WORD,8\n"
        "move 0x60300,R0\nnop\nfb_com_cfg $WORD,8\n"
        "move 5,R0\nnop\nfb_acq_tb_extra $WORD,8\nfb_com_extra $WORD,8\nstop"
    )
    assert [p.meta["device_operands"] for p in concrete.rt_packets[1:]] == [
        [1, 2, 2, 8], [0, 6, 3, 8], [0, 5, 8], [0, 5, 8],
    ]


@pytest.mark.parametrize("line", UNVERIFIED)
def test_unverified_device_operation_stops_both_engines_at_the_named_instruction(line):
    for state in run_both(line + "\nstop", startup_ns=8):
        execution = state.metadata["execution"]
        assert (execution["status"], execution["pc"], execution["source"]["raw"]) == ("blocked", 0, line)
        assert line.split()[0] in execution["reason"]
        assert any(d.category == "analysis_incomplete" and d.source.raw == line for d in state.diagnostics)
        assert not any(e.kind == line.split()[0] for e in state.events)


# Source spellings, raw LINQ command IDs and payloads are fixed vectors, including
# nonzero fields. They do not come from either runtime's shared command decoder.
LINQ = [
    ("fb_acq_tb_id 23,8", 0x40, 23, "tb_id"),
    ("fb_acq_iq_id 24,8", 0x41, 24, "iq_id"),
    ("fb_acq_tb_valid 0,8", 0x5B, 0, "tb_valid"),
    ("fb_acq_iq_shift 7,8", 0x5C, 7, "iq_shift"),
    ("fb_acq_tb_cfg 1,2,2,8", 0x42, 0x20201, "tb_cfg"),
    ("fb_acq_tb_extra 0,5,8", 0x43, 5, "tb_extra"),
    ("fb_com_cfg 0,6,3,8", 0x60, 0x60300, "com_cfg"),
    ("fb_com_extra 0,7,8", 0x61, 7, "com_extra"),
    ("fb_llp_tags_id 33,8", 0x20, 33, None),
    ("fb_llp_ttls_id 34,8", 0x21, 34, None),
    ("fb_tdc_tags_id 35,8", 0x00, 35, None),
    ("fb_tdc_tdelta_id 36,8", 0x01, 36, None),
]


def test_every_device_mnemonic_has_a_differential_scenario():
    programs = [FIFO_PROGRAM, QTM_PROGRAM, TRIGGER_PROGRAM, *UNVERIFIED, *(row[0] for row in LINQ)]
    tested = {item.op for program in programs for item in parse_q1asm(program).instructions}
    assert tested - core.LOCAL_OPS == core.SOURCE_OPS - core.LOCAL_OPS


@pytest.mark.parametrize("raw", [False, True], ids=["named", "raw"])
def test_linq_settings_update_independent_device_state_at_each_packet(monkeypatch, raw):
    # The timelines expose decoded requests; observe the actual stored values as
    # well so that dropping a write in either implementation fails this test.
    attributes = {line.split()[0]: attribute for line, _, _, attribute in LINQ}
    concrete_writes, simulation_writes = [], []
    step, dispatch = core.step, runtime._Runtime.dispatch_rt

    def observe_step(*args, **kwargs):
        transition = step(*args, **kwargs)
        if transition.packet and transition.packet.device_op in attributes:
            op = transition.packet.device_op
            concrete_writes.append((op, dict(transition.state.devices.linq_config)[op]))
        return transition

    def observe_dispatch(self, seq, command, *args, **kwargs):
        dispatch(self, seq, command, *args, **kwargs)
        op = command.op
        if op in attributes:
            attr = attributes[op]
            value = getattr(seq, attr) if attr else seq.qtm_streams[op]
            simulation_writes.append((op, value if isinstance(value, tuple) else (value,)))

    monkeypatch.setattr(core, "step", observe_step)
    monkeypatch.setattr(runtime._Runtime, "dispatch_rt", observe_dispatch)
    body = [f"fb_cmd {command},{word},8" if raw else named for named, command, word, _ in LINQ]
    check_completed("\n".join([*body, "stop"]))
    expected = []
    for named, _, _, _ in LINQ:
        op, args = named.split()
        expected.append((op, tuple(int(x) for x in args.split(",")[:-1])))
    assert concrete_writes == simulation_writes == expected


@pytest.mark.parametrize("arrival_ns", [3, 4, 5])
def test_feedback_overflow_at_the_stop_completion_edge(arrival_ns):
    for state in run_both("stop -9", feedback=((0, 3, 10), (arrival_ns, 7, 11)), capacity=1):
        execution = state.metadata["execution"]
        rejected = [e for e in state.events if e.kind == "feedback_rejected"]
        if arrival_ns > 4:
            assert execution["status"] == "completed" and execution["stop_code"] == -9
            assert not rejected
        else:
            assert execution["status"] == "error"
            assert "stop_code" not in execution and "stop_source" not in execution
            assert not any(e.kind == "stop" or "stop_code" in e.meta for e in state.events)
            event, = rejected
            assert (event.t0.value, event.meta["id"], event.meta["capacity"]) == (arrival_ns, 7, 1)
            error, = [d for d in state.diagnostics if d.category == "feedback_queue_overflow"]
            assert event.id in error.related_events


@pytest.mark.parametrize("count", [32, 33])
def test_documented_receiver_depth_with_an_arrival_batch_before_a_waiting_pop(count):
    # Qblox's LINQ guide specifies 32 entries. The model admits the entire
    # same-time batch before the waiting Q1 pop; hardware ordering is a HIL case.
    batch = tuple((8, n, 0x80000000 + n) for n in range(1, count + 1))
    for state in run_both("fb_pop_data 32,R0\nnop\nstop R0", feedback=batch):
        if count == 32:
            assert state.metadata["execution"]["status"] == "completed"
            assert state.registers["R0"].value == 0x80000020
            pop, = [e for e in state.events if e.kind == "q1_issue" and e.meta["op"] == "fb_pop_data"]
            assert pop.t1.value == 8
        else:
            assert state.metadata["execution"]["status"] == "error" and not state.register_history
            rejected, = [e for e in state.events if e.kind == "feedback_rejected"]
            assert (rejected.t0.value, rejected.meta["id"], rejected.meta["capacity"]) == (8, 33, 32)


def test_selective_receive_stalls_discards_and_preserves_same_tick_fifo_order():
    source = "fb_pop_data 9,R2\nfb_pull_data R0,R1\nnop\nstop R1"
    arrivals = ((1, 3, 48), (11, 9, 0xFFFFFFFB), (11, 7, 0x80000001))
    concrete, simulated = run_both(source, feedback=arrivals, capacity=2)
    for state in (concrete, simulated):
        execution = state.metadata["execution"]
        assert (execution["status"], execution["stop_code"]) == ("completed", -2147483647)
        assert {reg: value.value for reg, value in state.registers.items()} == {
            "R2": 0xFFFFFFFB, "R0": 7, "R1": 0x80000001,
        }
        issues = [e for e in state.events if e.kind == "q1_issue"]
        assert [(e.t0.value, e.t1.value) for e in issues] == [(0, 8), (8, 12), (12, 16), (16, 20)]
        assert [set(e.meta["register_snapshot"]) for e in issues] == [set(), set(), {"R2"}, {"R2", "R0", "R1"}]
    assert [(e.t0.value, e.t1.value) for e in simulated.events if e.kind == "q1_stall"] == [(4, 8)]
    assert [(e.meta["arrival_ns"], e.meta["id"]) for e in concrete.events if e.kind == "feedback_pop"] == [(11, 9), (11, 7)]
    discarded, = [e for e in simulated.events if e.kind == "feedback_discard"]
    first, = [e for e in simulated.events if e.kind == "feedback_receive" and e.meta["id"] == 3]
    assert discarded.meta["input_event_ids"] == [first.id]


@pytest.mark.parametrize("receive", ["fb_pop_data 1,R0", "fb_pull_data R3,R0"])
@pytest.mark.parametrize("arrival,end", [(0, 8), (7, 8), (8, 8), (11, 8), (12, 12), (41, 40)])
def test_q1simulator_receiver_completes_after_fetch_or_on_the_arrival_tick(receive, arrival, end):
    states = run_both(f"move 2,R2\n{receive}\nnop\nstop R0", feedback=((arrival, 1, 18),))
    for state in states:
        assert state.metadata["execution"]["stop_code"] == 18
        _, read, nop, stop = [e for e in state.events if e.kind == "q1_issue"]
        assert (read.t0.value, read.t1.value, nop.t0.value, stop.t0.value) == (4, end, end, end + 4)
        assert "R2" not in read.meta["register_snapshot"] and "R2" in nop.meta["register_snapshot"]


@pytest.mark.parametrize("second_ns", [20, 23, 24])
def test_zero_duration_trigger_wait_accepts_the_next_arrival_at_its_dequeue_edge(second_ns):
    source = "wait_trigger 1,0\nwait_trigger 1,0\nstop"
    for state in run_both(source, triggers=((20, 1), (second_ns, 1))):
        # A physical receiver may have a queued, still-unexecuted packet at the blocked edge.
        ends = [p.rt_t1.value for p in state.rt_packets if p.rt_t1 is not None]
        if second_ns < 24:
            assert state.metadata["execution"]["status"] == "blocked" and ends == [20]
        else:
            assert state.metadata["execution"]["status"] == "completed" and ends == [20, 24]
            assert state.rt_time_ns.value == 28


@pytest.mark.parametrize("line,reason", [
    ("fb_pop_data 257,R0", "8-bit"), ("fb_acq_tb_extra 1,5,8", "FIFO word-order"),
])
def test_unverified_linq_encodings_do_not_produce_device_results(line, reason):
    for state in run_both(line + "\nstop", feedback=((0, 1, 99),), startup_ns=8):
        execution = state.metadata["execution"]
        assert execution["status"] == "blocked" and reason in execution["reason"]
        assert execution["source"]["raw"] == line
        assert not state.register_history


@pytest.mark.parametrize("index", [2, 3])
def test_threshold_registers_beyond_low_and_high_have_no_simulated_classifier(index):
    line = f"upd_thres {index},18,8"
    concrete, simulated = run_both(line + "\nstop", startup_ns=8)
    assert concrete.metadata["execution"]["status"] == "completed"
    packet, = concrete.rt_packets
    assert packet.meta["device_operands"] == [index, 18, 8]
    execution = simulated.metadata["execution"]
    assert execution["status"] == "blocked" and execution["source"]["raw"] == line
    assert f"Count threshold index {index} is unsupported" in execution["reason"]
