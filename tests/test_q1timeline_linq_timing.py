from __future__ import annotations

from dataclasses import replace
import json

import pytest

from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.values import Concrete
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.q1asm.parser import parse_q1asm
from q1timeline.cli import main
from q1timeline.project import ConfigLoadError, load_project_config


# Official LINQ transport times, independently of the site's additional delay.
CASES = {
    "q1_register_or_immediate": (60, 150, 380),
    "thresholded_bits": (160, 250, 472),
    "iq_values": (164, 270, 492),
    "high_resolution_time_tags": (910, 1000, 1260),
    "time_delta": (910, 1000, 1260),
    "ttl_counts": (146, 236, 480),
    "low_latency_time_tags": (146, 236, 480),
}
ROUTES = ("self_cast", "intra_cast", "multi_cast_worst_case")


def packet_case(data_type, route_index, op, gap, *, extra_delay=200):
    channel = 4 if route_index == 0 else 16
    receive = f"fb_pop_data {channel},R0" if op == "fb_pop_data" else "fb_pull_data R0,R1"
    sender = interpret_program(parse_q1asm(
        f"wait_sync 4\nfb_com_data {channel},7,4\nwait 4000\n{receive}\nstop\n",
        file="sender.q1asm"), sequencer_id="sender")
    states = [sender]
    sender.metadata["module"] = "module0"
    consumer = sender
    if route_index:
        # No consumer on the sending sequencer for a routed packet.
        sender.events = [event for event in sender.events if event.kind != "feedback_pop"]
        consumer = interpret_program(parse_q1asm(
            f"wait_sync 4\nwait 4000\n{receive}\nstop\n", file="receiver.q1asm"), sequencer_id="receiver")
        consumer.metadata["module"] = "module0" if route_index == 1 else "module1"
        states.append(consumer)
    send = next(event for event in sender.events if event.kind == "feedback_com")
    # The timing model accepts a known emission of any LINQ packet data type.
    send.meta["feedback"]["data_type"] = data_type
    pop = next(event for event in consumer.events if event.kind == "feedback_pop")
    consumer.events = [replace(event, t0=Concrete(send.t0.value + gap), t1=Concrete(send.t0.value + gap))
                       if event.id == pop.id else event for event in consumer.events]
    align_timelines(states, mode="none")
    return timeline_ir_from_states(states, project={"feedback_transport_delay_ns": extra_delay})


@pytest.mark.parametrize("data_type", CASES)
@pytest.mark.parametrize("route_index", range(3))
@pytest.mark.parametrize("op", ["fb_pop_data", "fb_pull_data"])
@pytest.mark.parametrize("delta", [-4, 0, 4])
def test_linq_latency_boundary_and_marker_use_identical_timing(data_type, route_index, op, delta):
    base = CASES[data_type][route_index]
    extra = 0 if route_index == 0 else 200
    ir = packet_case(data_type, route_index, op, base + extra + delta)
    timing = ir["feedback_flows"][0]["timing"]
    assert timing["route"] == ROUTES[route_index]
    assert timing["data_type"] == data_type
    assert timing["base_latency_ns"] == base
    assert timing["transport_delay_ns"] == extra
    assert timing["latency_ns"] == base + extra
    assert timing["arrival_ns"] == timing["send_available_ns"] + base + extra
    findings = [item for item in ir["diagnostics"] if item["category"] == "feedback_latency_violation"]
    assert len(findings) == (1 if delta < 0 else 0)
    if findings:
        diagnostic = findings[0]
        assert diagnostic["severity"] == "warning"
        assert diagnostic["source"]["file"] == ("sender.q1asm" if route_index == 0 else "receiver.q1asm")
        assert diagnostic["details"]["required_latency_ns"] == timing["latency_ns"]
        assert diagnostic["details"]["arrival_ns"] == timing["arrival_ns"]
        assert diagnostic["details"]["missing_wait_ns"] == 4
        assert op in diagnostic["message"]


@pytest.mark.parametrize("extra_delay", [0, 84, 200, 360])
def test_site_transport_delay_is_configurable_and_never_added_to_self_cast(extra_delay):
    for route in range(3):
        ir = packet_case("q1_register_or_immediate", route, "fb_pop_data", 3000, extra_delay=extra_delay)
        timing = ir["feedback_flows"][0]["timing"]
        assert timing["latency_ns"] == CASES["q1_register_or_immediate"][route] + (extra_delay if route else 0)


@pytest.mark.parametrize("setter,data_type", [("fb_acq_tb_id", "thresholded_bits"), ("fb_acq_iq_id", "iq_values")])
def test_readout_packet_arrives_after_configured_integration_not_command_spacing(setter, data_type):
    state = interpret_program(parse_q1asm(
        f"{setter} 4,4\nacquire 0,0,4\nwait 2000\nfb_pop_data 4,R0\nstop\n"), sequencer_id="readout")
    state.metadata["hardware_parameters"] = {"integration_length_acq": 1000}
    ir = timeline_ir_from_states([state])
    timing = ir["feedback_flows"][0]["timing"]
    assert timing["send_available_ns"] == 1004
    assert timing["arrival_ns"] == 1004 + CASES[data_type][0]


@pytest.mark.parametrize("setter,data_type,streaming", [
    ("fb_llp_ttls_id", "ttl_counts", False), ("fb_tdc_tdelta_id", "time_delta", False),
    ("fb_llp_tags_id", "low_latency_time_tags", True), ("fb_tdc_tags_id", "high_resolution_time_tags", True),
])
def test_timetag_packets_use_close_time_for_aggregates_and_unknown_emission_for_streams(setter, data_type, streaming):
    state = interpret_program(parse_q1asm(
        f"{setter} 4,4\nacquire_timetags 0,0,1,0,4\nwait 100\n"
        "acquire_timetags 0,0,0,0,4\nwait 2000\nfb_pop_data 4,R0\nstop\n"), sequencer_id="tags")
    ir = timeline_ir_from_states([state])
    assert len(ir["feedback_flows"]) == 1
    timing = ir["feedback_flows"][0]["timing"]
    assert timing["data_type"] == data_type
    assert timing["latency_ns"] == CASES[data_type][0]
    if streaming:
        assert timing["send_available_ns"] is timing["arrival_ns"] is None
        assert any(item["details"].get("reason") == "feedback_timing_unknown" for item in ir["diagnostics"])
    else:
        assert timing["send_available_ns"] == 108
        assert timing["arrival_ns"] == 108 + CASES[data_type][0]


@pytest.mark.parametrize("setter", ["fb_llp_ttls_id", "fb_tdc_tdelta_id"])
def test_unmodeled_timetag_fine_delay_does_not_get_an_exact_arrival_marker(setter):
    state = interpret_program(parse_q1asm(
        f"{setter} 4,4\nacquire_timetags 0,0,1,0,4\nwait 100\n"
        "acquire_timetags 0,0,0,128,4\nwait 2000\nfb_pop_data 4,R0\nstop\n"), sequencer_id="tags")
    ir = timeline_ir_from_states([state])
    assert ir["feedback_flows"][0]["timing"]["arrival_ns"] is None
    assert any(item["details"].get("reason") == "feedback_timing_unknown" for item in ir["diagnostics"])


@pytest.mark.parametrize("op", ["fb_pop_data 16,R0", "fb_pull_data R0,R1"])
def test_project_cli_uses_module_topology_and_configured_site_delay(tmp_path, op):
    (tmp_path / "sender.q1asm").write_text("wait_sync 4\nfb_com_data 16,7,4\nwait 2000\nstop\n")
    (tmp_path / "receiver.q1asm").write_text(f"wait_sync 4\nwait 400\n{op}\nstop\n")
    project = tmp_path / "q1timeline.yml"
    project.write_text("analysis:\n  feedback_transport_delay_ns: 84\nsequencers:\n"
                       "  - {id: sender, module: mod0, file: sender.q1asm}\n"
                       "  - {id: receiver, module: mod0, file: receiver.q1asm}\n")
    output = tmp_path / "ir.json"
    assert main(["analyze", "--project", str(project), "--out", str(output), "--no-render"]) == 0
    ir = json.loads(output.read_text())
    timing = ir["feedback_flows"][0]["timing"]
    assert timing["route"] == "intra_cast"
    assert timing["latency_ns"] == 234
    assert timing["arrival_ns"] == 238
    assert not any(item["category"] == "feedback_latency_violation" for item in ir["diagnostics"])


@pytest.mark.parametrize("value", [-1, True, 1.5, "200"])
def test_invalid_site_transport_delay_is_reported_as_config_error(tmp_path, value):
    (tmp_path / "seq.q1asm").write_text("stop\n")
    project = tmp_path / "q1timeline.yml"
    project.write_text(f"analysis:\n  feedback_transport_delay_ns: {json.dumps(value)}\n"
                       "sequencers:\n  - {id: seq, file: seq.q1asm}\n")
    with pytest.raises(ConfigLoadError) as caught:
        load_project_config(project)
    assert any(item.details.get("field") == "analysis.feedback_transport_delay_ns" for item in caught.value.diagnostics)
