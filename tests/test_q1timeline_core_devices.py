import pytest

from q1timeline.q1asm import core
from q1timeline.q1asm.instruction_table import SUPPORTED_OPS
from q1timeline.q1asm.parser import parse_q1asm


def run(source, external=None):
    code = core.prepare_program(parse_q1asm(source))
    state = core.CoreState()
    packets = []
    while not state.halted:
        transition = core.step(state, code, external=external)
        state = transition.state
        if transition.packet is not None:
            packets.append(transition.packet)
    return state, packets


def test_core_covers_the_source_instruction_table():
    assert core.SOURCE_OPS == SUPPORTED_OPS


def test_device_commands_capture_setters_thresholds_and_return_resolved_packet_actions():
    state, packets = run("""set_time_ref
reset_netzero
wait 12
fb_cmd 0x12,180,8
acquire_digital 0,1,4
acquire_ttl 1,2,1,4
acquire_timetags 1,2,0,511,8
play_netzero 100,4
stop""")
    assert [(p.device_op, p.device_operands) for p in packets] == [
        ("wait", (12,)), ("upd_thres", (2, 180, 8)), ("acquire_digital", (0, 1, 4)),
        ("acquire_ttl", (1, 2, 1, 4)), ("acquire_timetags", (1, 2, 0, 511, 8)), ("play_netzero", (100, 4)),
    ]
    assert packets[1].op == "fb_cmd"
    assert packets[1].q1_issue_t1 - packets[1].q1_issue_t0 == 8
    assert {field.field for field in packets[1].applied} == {"time_ref", "netzero_reset"}
    assert not any(p.applied for p in packets[2:])
    assert state.devices.thresholds == ((2, 180),)
    assert (state.devices.time_ref_ns, state.rt_ns) == (12, 40)


def test_feedback_pop_discards_preceding_ids_then_pull_writes_the_next_id_and_word():
    ext = core.ExternalInputs(feedback_arrivals=((0, 3, 31), (40, 7, 71), (50, 9, 91)))
    state, packets = run("fb_pop_data 7,R0\nnop\nwait 8\nfb_pull_data R1,R2\nnop\nstop R2", ext)
    assert state.registers[:3] == (71, 9, 91)
    assert (state.q1_ns, state.rt_ns, state.stop_code, state.devices.feedback_cursor) == (60, 8, 91, 3)
    assert (packets[0].q1_issue_t0, packets[0].rt_t0) == (44, 0)


def test_trigger_wait_consumes_one_arrival_and_zero_duration_reserves_the_next_slot():
    ext = core.ExternalInputs(trigger_arrivals=((1, 1), (20, 1), (21, 2), (30, 1)))
    state, packets = run("wait 4\nwait_trigger 1,0\nwait_trigger 1,4\nstop", ext)
    assert [(p.rt_t0, p.rt_t1) for p in packets] == [(0, 4), (4, 20), (24, 34)]
    assert state.devices.trigger_wait_cursor == 4


def test_conditional_packet_skips_unknown_effect_and_carries_persistent_setters():
    ext = core.ExternalInputs(trigger_arrivals=((4, 1),))
    state, packets = run("""set_latch_en 1,4
set_cond 1,1,0,8
wait 4
latch_rst 4
set_mrk 3
reset_ph
play_pulse 0,4
set_cond 0,0,0,4
upd_param 4
stop""", ext)
    assert [(p.op, p.skipped, p.rt_t1) for p in packets] == [
        ("set_latch_en", False, 4), ("wait", False, 8), ("latch_rst", False, 12),
        ("play_pulse", True, 20), ("upd_param", False, 24),
    ]
    assert not packets[3].applied and packets[3].device_op is None
    assert [(w.field, w.value) for w in packets[4].applied] == [("marker", 3)]
    assert state.devices.counts == ()


def test_missing_counter_schedule_and_explicitly_empty_counter_schedule_differ():
    source = "set_latch_en 1,4\nset_cond 1,1,0,8\nwait 4\nstop"
    with pytest.raises(core.CoreBlocked, match="trigger inputs"):
        run(source)
    state, packets = run(source, core.ExternalInputs(trigger_arrivals=()))
    assert packets[-1].skipped and state.rt_ns == 12


def test_partial_schedule_keeps_only_unconfigured_counter_addresses_unknown():
    inputs = core.ExternalInputs(trigger_arrivals=((0, 1), (4, 1)), scheduled_addresses=frozenset({1}))
    source = "set_latch_en 1,4\nset_cond 1,{},0,8\nwait 4\nstop"
    state, packets = run(source.format(3), inputs)
    assert not packets[-1].skipped and dict(state.devices.counts) == {1: 1}
    with pytest.raises(core.CoreBlocked, match="trigger inputs"):
        run(source.format(2), inputs)


def test_feedback_fifo_capacity_and_stop_edge():
    def arrivals(last_arrival):
        return core.ExternalInputs(feedback_arrivals=((0, 1, 42), (last_arrival, 2, 43)), feedback_queue_capacity=1)

    with pytest.raises(core.FeedbackOverflow) as error:
        run("stop", arrivals(4))
    assert (error.value.time_ns, error.value.identifier, error.value.capacity) == (4, 2, 1)
    assert run("stop", arrivals(5))[0].q1_ns == 4
    with pytest.raises(core.FeedbackOverflow) as error:
        run("fb_pop_data 2,R0\nstop", arrivals(0))
    assert error.value.time_ns == 0
    state, _ = run("fb_pop_data 2,R0\nnop\nstop R0", arrivals(10))
    assert state.stop_code == 43


def test_feedback_wait_detects_simultaneous_overflow_after_discarding_old_entries():
    inputs = core.ExternalInputs(feedback_arrivals=((0, 1, 0), (10, 2, 2), (10, 3, 3)), feedback_queue_capacity=1)
    with pytest.raises(core.FeedbackOverflow) as error:
        run("fb_pop_data 2,R0\nstop", inputs)
    assert error.value.time_ns == 10


@pytest.mark.parametrize(("named", "raw"), [
    ("fb_com_cfg 0,1,2,4", "fb_cmd 0x60,0x10200,4"),
    ("fb_com_extra 0,3,4", "fb_cmd 0x61,3,4"),
    ("fb_acq_tb_cfg 1,2,1,4", "fb_cmd 0x42,0x20101,4"),
    ("fb_acq_tb_extra 0,3,4", "fb_cmd 0x43,3,4"),
    ("fb_acq_tb_id 7,4", "fb_cmd 0x40,7,4"), ("fb_acq_iq_id 8,4", "fb_cmd 0x41,8,4"),
    ("fb_acq_tb_valid 1,4", "fb_cmd 0x5b,1,4"), ("fb_acq_iq_shift 12,4", "fb_cmd 0x5c,12,4"),
    ("fb_llp_tags_id 3,4", "fb_cmd 0x20,3,4"), ("fb_llp_ttls_id 4,4", "fb_cmd 0x21,4,4"),
    ("fb_tdc_tags_id 5,4", "fb_cmd 0,5,4"), ("fb_tdc_tdelta_id 6,4", "fb_cmd 1,6,4"),
    ("fb_com_data 7,99,4", "fb_cmd 0x107,99,4"), ("upd_thres 3,6,4", "fb_cmd 0x13,6,4"),
])
def test_raw_and_named_device_commands_have_same_effect_and_timing(named, raw):
    reference, (p,) = run(named + "\nstop")
    state, (q,) = run(raw + "\nstop")
    assert state == reference
    assert (p.device_op, p.device_operands, p.applied) == (q.device_op, q.device_operands, q.applied)


@pytest.mark.parametrize(("instruction", "reason"), [
    ("sw_req 1", "unverified"), ("play_pulse 0,4", "unverified"),
    ("fb_acq_tb_mock 1,1,1,4", "duration versus count"), ("fb_cmd 0x5d,0x111,4", "duration versus count"),
    ("fb_pop_data 1,R0", "FIFO entry"), ("fb_pull_data R1,R2", "FIFO entry"),
    ("fb_pop_data 256,R0", "8-bit"), ("wait_trigger 1,4", "arrival"),
    ("fb_cmd 0x72,0,8", "no verified behavior"), ("fb_com_extra 1,2,4", "extra bytes"),
])
def test_core_blocks_before_inventing_device_evidence(instruction, reason):
    with pytest.raises(core.CoreBlocked, match=reason):
        run(instruction + "\nstop")
