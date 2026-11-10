import pytest

from q1timeline.analysis.interpreter import prepare_def_program
from q1timeline.playback import PlaybackSession
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.simulation.runtime import simulate_programs


def run(source, prepared=False):
    program = parse_q1asm(source, file="phase.q1asm")
    bindings = {"drive": prepare_def_program(program, params={}, strict_q1asm=False, varying=set())} if prepared else None
    state, = simulate_programs({"drive": program}, prepared_programs=bindings,
                               simulation={"physical": {"sequencers": {"drive": {}}}})
    assert state.metadata["execution"]["status"] == "completed"
    traces = {trace["path"]: trace for trace in state.metadata["simulation"]["signals"]["traces"] if trace["stage"] == "nco"}

    def iq(packet):
        time = packet.rt_t0.value
        return tuple(traces[path]["samples"][(time - traces[path]["t0"]) // traces[path]["dt_ns"]] for path in ("i", "q"))

    return state, iq


@pytest.mark.parametrize("setter,field,value,initial,expected", [
    ("set_ph_delta 250000000", "phase_delta", 250000000, "", (0.5, 0)),
    ("reset_ph", "phase_reset", True, "set_ph 250000000\n", (0, 0.5)),
])
@pytest.mark.parametrize("prepared", [False, True])
def test_skipped_phase_action_is_captured_once_without_changing_iq(setter, field, value, initial, expected, prepared):
    source = (f"set_awg_offs 16384,0\n{initial}upd_param 4\nset_cond 1,0,0,4\n{setter}\n"
              "upd_param 4\nset_cond 0,0,0,4\nupd_param 4\nstop")
    state, iq = run(source, prepared)
    first, skipped, last = state.rt_packets
    for packet in (first, skipped, last):
        assert iq(packet) == pytest.approx(expected, abs=1e-12)
    assert skipped.meta["captured_state"][field] == value
    assert field not in skipped.meta["applied_state"] and field not in last.meta["applied_state"]
    assert not state.pending_phase_reset and state.latched_state.phase_delta is None
    if not prepared:
        setter_event, = [event for event in state.events if event.kind == "latched_state_pending" and event.meta["field"] == field]
        assert skipped.meta["captured_setter_event_ids"][field] == setter_event.id
        assert field not in last.meta["applied_setter_event_ids"]


@pytest.mark.parametrize("prepared", [False, True])
def test_phase_kicks_accumulate_once_and_offset_update_does_not_erase_them(prepared):
    source = ("set_awg_offs 16384,0\nupd_param 4\nset_ph_delta 250000000\nwait 8\nupd_param 4\nupd_param 4\n"
              "set_ph_delta 250000000\nupd_param 4\nset_ph 250000000\nupd_param 4\nreset_ph\nupd_param 4\nupd_param 4\nstop")
    state, iq = run(source, prepared)
    expected = [(0.5, 0), (0.5, 0), (0, 0.5), (0, 0.5), (-0.5, 0), (0, -0.5), (0.5, 0), (0.5, 0)]
    for packet, sample in zip(state.rt_packets, expected, strict=True):
        assert iq(packet) == pytest.approx(sample, abs=1e-12)
    assert [index for index, packet in enumerate(state.rt_packets) if "phase_delta" in packet.meta["applied_state"]] == [2, 4]
    assert [index for index, packet in enumerate(state.rt_packets) if packet.meta.get("phase_reset")] == [6]
    assert state.rt_packets[-1].meta["applied_state"]["phase"] == 0


def test_explicit_offset_and_kick_apply_after_reset_on_the_same_packet():
    source = ("set_awg_offs 16384,0\nupd_param 4\nset_ph_delta 125000000\nupd_param 4\n"
              "set_ph 250000000\nset_ph_delta 250000000\nreset_ph\nupd_param 4\nstop")
    state, iq = run(source)
    final = state.rt_packets[-1]
    assert final.meta["phase_reset"] is True
    assert iq(final) == pytest.approx((-0.5, 0), abs=1e-12)


@pytest.mark.parametrize("prepared", [False, True])
def test_same_offset_word_reissued_after_reset_stays_persistent_even_if_first_capture_is_skipped(prepared):
    source = ("set_awg_offs 16384,0\nset_ph 250000000\nupd_param 4\nreset_ph\nupd_param 4\n"
              "set_cond 1,0,0,4\nset_ph 250000000\nupd_param 4\nset_cond 0,0,0,4\nupd_param 4\nstop")
    state, iq = run(source, prepared)
    for packet, sample in zip(state.rt_packets, [(0, 0.5), (0.5, 0), (0.5, 0), (0, 0.5)], strict=True):
        assert iq(packet) == pytest.approx(sample, abs=1e-12)
    assert state.rt_packets[-1].meta["applied_updated_fields"] == ["phase"]


@pytest.mark.parametrize("setter,field", [("set_ph_delta 250000000", "phase_delta"), ("reset_ph", "phase_reset")])
def test_conditional_play_captures_and_discards_one_shot_phase_actions(setter, field):
    source = f"{setter}\nwait 8\nset_cond 1,0,0,4\nupd_param 4\nset_cond 0,0,0,4\nupd_param 4\nstop"
    session = PlaybackSession({"drive": ("phase.q1asm", source)})
    assert session.run(budget_ms=1000, max_steps=1000)["status"] == "completed"
    ir = session.snapshot()
    skip, = [event for event in ir["events"] if event["kind"] == "conditional_skip"]
    update, = [event for event in ir["events"] if event["kind"] == "upd_param"]
    setter_event, = [event for event in ir["events"] if event["kind"] == "latched_state_pending" and event["meta"]["field"] == field]
    assert skip["meta"]["captured_setter_event_ids"][field] == setter_event["id"]
    assert skip["meta"]["applied_state"] == {}
    assert field not in update["meta"].get("applied_state", {})
