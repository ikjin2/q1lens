import pytest

from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.interpreter import prepare_def_program
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.config import validate_simulation
from q1timeline.simulation.runtime import simulate_programs


PULSE = "set_digital 1,1,0\nupd_param 16\nset_digital 0,1,0\nupd_param 16\nstop"


def _run(sources, *, delay_ticks=0, prepared=False, **options):
    programs = {name: parse_q1asm(source, file=f"{name}.q1asm") for name, source in sources.items()}
    memory = {name: {"acquisitions": {"samples": {"index": 0, "num_bins": 4}}} for name in sources}
    prepared_programs = {name: prepare_def_program(program, params={}, strict_q1asm=False, varying=set())
                         for name, program in programs.items()} if prepared else None
    states = simulate_programs(programs, sequences=memory, prepared_programs=prepared_programs,
                               simulation={"startup_delay_ns": 100,
                                           "digital_routes": [{"source": "tx", "target": "rx", "delay_ticks": delay_ticks}],
                                           **options})
    return {state.sequencer_id: state for state in states}


def _events(state, kind):
    return [event for event in state.events if event.kind == kind]


def _acquisitions(state):
    return state.metadata["simulation"]["acquisitions"]


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("receiver_first", [False, True])
def test_zero_delay_samples_observe_same_time_output_in_both_sequencer_orders(receiver_first, prepared):
    receiver = "acquire_digital 0,0,8\nacquire_digital 0,1,8\nacquire_digital 0,2,4\nstop"
    sources = {"rx": receiver, "tx": PULSE} if receiver_first else {"tx": PULSE, "rx": receiver}
    states = _run(sources, prepared=prepared)
    transmitter, receiver = states["tx"], states["rx"]
    assert all(state.metadata["execution"]["status"] == "completed" for state in states.values())
    transitions = transmitter.metadata["simulation"]["digital_output"]["transitions"]
    assert [(event["time_ticks"], event["level"]) for event in transitions] == [(100 * 128, 1), (116 * 128, 0)]
    assert [(packet.rt_t0.value, packet.rt_t1.value) for packet in transmitter.rt_packets] == [(100, 116), (116, 132)]
    assert [(item["level"], item["sample_ticks"]) for item in _acquisitions(receiver)] == [
        (1, 100 * 128), (1, 108 * 128), (0, 116 * 128),
    ]
    assert _acquisitions(receiver)[0]["source"] == {"kind": "digital_loopback", "sequencer": "tx"}
    if not prepared:
        outputs = _events(transmitter, "digital_output_transition")
        tx_packets = _events(transmitter, "upd_param")
        inputs = _events(receiver, "digital_transition")
        for output, packet, incoming in zip(outputs, tx_packets, inputs, strict=True):
            assert output.meta["source_event_id"] == packet.id
            assert output.meta["rt_packet_id"] == packet.meta["rt_packet_id"] == incoming.meta["source_rt_packet_id"]
            assert incoming.meta["source_event_id"] == output.id
        assert [item["input_event_ids"] for item in _acquisitions(receiver)] == [[inputs[0].id], [inputs[0].id], [inputs[1].id]]


def test_fractional_loopback_preserves_pulse_width_and_aligned_timetag_provenance():
    states = _run({"tx": PULSE, "rx": "acquire_timetags 0,0,1,0,20\nacquire_timetags 0,1,0,0,4\nstop"},
                  delay_ticks=129)
    output_high, output_low = _events(states["tx"], "digital_output_transition")
    high, low = _events(states["rx"], "digital_transition")
    assert high.meta["time_ticks"] == output_high.meta["time_ticks"] + 129
    assert low.meta["time_ticks"] == output_low.meta["time_ticks"] + 129
    assert low.t0.value - high.t0.value == 16
    result, = _acquisitions(states["rx"])
    edge, = _events(states["rx"], "digital_edge")
    assert result["count"] == 1 and result["timetag_ticks"] == 129
    assert result["input_event_ids"] == [edge.id] and edge.meta["source_event_id"] == high.id
    assert high.meta["source_event_id"] == output_high.id
    align_timelines(list(states.values()), mode="first_anchor", anchor_kinds=["rt_queue_dequeue"])
    ir = timeline_ir_from_states(list(states.values()))
    by_id = {event["id"]: event for event in ir["events"]}
    assert by_id[output_high.id]["meta"]["aligned_t0"] == 0
    assert by_id[high.id]["meta"]["aligned_t0"] == 129 / 128
    assert by_id[high.id]["t0"]["value"] == 100 + 129 / 128
    assert by_id[output_high.meta["setter_event_id"]]["source"]["line"] == 1


def test_routed_edges_follow_half_open_window_and_send_linq_at_the_edge():
    transmitter = ("wait 4\nset_digital 1,1,0\nupd_param 4\nset_digital 0,1,0\nupd_param 12\n"
                   "set_digital 1,1,0\nupd_param 4\nstop")
    receiver = "fb_llp_tags_id 5,4\nacquire_timetags 0,0,1,0,16\nacquire_timetags 0,1,0,0,4\nstop"
    states = _run({"rx": receiver, "tx": transmitter}, digital={"rx": {"linq_timestamp_origin_ticks": 0}})
    result, = _acquisitions(states["rx"])
    assert result["edge_times_ticks"] == [104 * 128]
    assert result["count"] == 1 and result["timetag_ticks"] == 0
    feedback, = _events(states["rx"], "feedback_send")
    assert feedback.t0.value == feedback.meta["value"] == 104
    assert feedback.meta["source_event_id"] == result["input_event_ids"][0]
    assert [event.t0.value for event in _events(states["rx"], "digital_transition")] == [104, 108, 120]


def test_output_and_routed_arrival_survive_both_rt_drains_with_nominal_fine_delay():
    states = _run({"tx": "set_digital 1,1,1025\nupd_param 4\nstop", "rx": "acquire_digital 0,0,4\nstop"},
                  delay_ticks=256)
    transmitter, receiver = states["tx"], states["rx"]
    output, = _events(transmitter, "digital_output_transition")
    incoming, = _events(receiver, "digital_transition")
    assert transmitter.rt_time_ns.value == receiver.rt_time_ns.value == 104
    assert output.t0.value == 108 + 1 / 128 and output.meta["fine_out_delay_ticks"] == 1025
    assert incoming.t0.value == 110 + 1 / 128
    assert _acquisitions(receiver)[0]["level"] == 0
    assert transmitter.metadata["simulation"]["digital_output"]["transitions"][0]["event_id"] == output.id


def test_repeated_levels_and_skipped_packet_do_not_create_edges():
    source = ("set_digital 1,1,0\nupd_param 4\nset_digital 1,1,1\nupd_param 4\n"
              "set_cond 1,1,0,4\nset_digital 0,1,0\nupd_param 4\nset_cond 0,1,0,4\nwait 4\nstop")
    states = _run({"tx": source, "rx": "wait 20\nstop"})
    output, = _events(states["tx"], "digital_output_transition")
    assert output.t0.value == 100 and output.meta["level"] == 1
    assert len(_events(states["rx"], "digital_transition")) == 1
    assert _events(states["tx"], "conditional_skip")[0].t0.value == 108


def test_unverified_output_mask_is_explicitly_unsupported():
    state = _run({"tx": "set_digital 1,2,0\nupd_param 4\nstop", "rx": "wait 4\nstop"})["tx"]
    assert state.metadata["execution"]["status"] == "blocked"
    assert "mask 1" in state.metadata["execution"]["reason"]
    assert not _events(state, "digital_output_transition")


@pytest.mark.parametrize("options,reason", [
    ({"digital_routes": [{"source": "missing", "target": "rx"}]}, "unknown sequencer"),
    ({"digital_routes": [{"source": "tx", "target": "rx", "delay_ticks": -1}]}, "delay_ticks"),
    ({"digital_routes": [{"source": "tx", "target": "rx", "delay_ticks": True}]}, "delay_ticks"),
    ({"digital_routes": [{"source": "tx", "target": "rx"}, {"source": "tx2", "target": "rx"}]}, "already has a digital driver"),
    ({"digital_routes": [{"source": "tx", "target": "rx"}],
      "inputs": {"digital": {"rx": {"initial_level": 0, "transitions": []}}}}, "already has a digital driver"),
])
def test_digital_route_validation(options, reason):
    with pytest.raises(ValueError, match=reason):
        validate_simulation(options, ["tx", "tx2", "rx"])
