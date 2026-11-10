from __future__ import annotations

import json

import pytest

from q1timeline.cli import main
from q1timeline.watch.watcher import run_watch_once


def _project(tmp_path, *, second_length=12, alignment="first_wait_sync", synchronized=True,
             destinations=("rx", "rx"), second_config="1,2,1", first_config="1,0,1",
             second_latency=5, iq=False, fifo=False, reads=2, prewait=(0, 0), routed_iq=False):
    sequencers, routes, acquisitions = [], [], []
    senders = (
        ("a", 4, 16, first_config, destinations[0], 5),
        ("b", 8, second_length, second_config, destinations[1], second_latency),
    )
    for (name, dwell, length, combine, destination, latency), before_sync in zip(senders, prewait):
        source = (f"wait {before_sync}\n" if before_sync else "")
        source += (f"wait_sync {dwell}\n" if synchronized else "") + "fb_acq_tb_id 16,4\n"
        source += f"fb_acq_tb_cfg {combine},4\n"
        if iq:
            source += "fb_acq_iq_id 6,4\n"
        if routed_iq:
            source += "fb_acq_iq_id 17,4\n" if name == "a" else "wait 4\n"
        source += "acquire 0,0,16\n"
        source += "wait 1200\nfb_pop_data 6,R1\nfb_pop_data 6,R2\nupd_param 4\n" if iq else "wait 16\n"
        (tmp_path / f"{name}.q1asm").write_text(source + "stop\n", encoding="utf-8")
        sequencers.append({"id": name, "file": f"{name}.q1asm", "hardware_parameters": {"integration_length_acq": length}})
        acquisitions.append({"sequencer": name, "acquisition_index": 0, "bin_index": 0, "thresholded": 1})
        if destination is not None:
            routes.append({"source": name, "target": destination, "id": 16, "latency_ns": latency})
    if routed_iq:
        routes.append({"source": "a", "target": "rx", "id": 17, "latency_ns": 9})
        acquisitions[0].update(i=2, q=-1)
    for name in ("rx", "other") if "other" in destinations else ("rx",):
        source = "wait_sync 4\n" if synchronized else ""
        if routed_iq:
            source += "wait 1000\nfb_pop_data 16,R0\nfb_pop_data 17,R1\nfb_pop_data 17,R2\n"
        else:
            source += "wait 100\n" + "".join(f"fb_pull_data R10,R{index}\n" if fifo else f"fb_pop_data 16,R{index}\n"
                                            for index in range(reads))
        source += "upd_param 4\nstop\n"
        (tmp_path / f"{name}.q1asm").write_text(source, encoding="utf-8")
        sequencers.append({"id": name, "file": f"{name}.q1asm"})
    path = tmp_path / "q1timeline.yml"
    path.write_text(json.dumps({"alignment": {"mode": alignment}, "sequencers": sequencers,
                                "simulation": {"startup_delay_ns": 100, "routes": routes,
                                               "inputs": {"acquisitions": acquisitions}}}), encoding="utf-8")
    return path


def _analyze(path, capsys, execution_mode="analysis"):
    assert main(["analyze", "--project", str(path), "--format", "vscode-json", "--include-timeline-ir",
                 "--no-render", "--execution-mode", execution_mode]) == 0
    return json.loads(capsys.readouterr().out)["timeline_ir"]


def _sources(ir):
    return {event["id"]: event for event in ir["events"]}


def _counts(ir):
    channel = ir["feedback_balance"]["channels"]["16"]
    return channel["sends"], channel["send_payloads"], channel["matched"]


@pytest.mark.parametrize("alignment,arrival", [("first_wait_sync", 33), ("after_first_wait_sync", 29), ("none", 33)])
def test_proven_round_counts_two_contributors_and_one_routed_word(tmp_path, capsys, alignment, arrival):
    ir = _analyze(_project(tmp_path, alignment=alignment), capsys)
    flow, = ir["feedback_flows"]
    assert _counts(ir) == (2, 1, 1)
    assert flow["timing"]["arrival_ns"] == arrival
    events = _sources(ir)
    assert [(events[event_id]["sequencer_id"], events[event_id]["kind"]) for event_id in flow["source_event_ids"]] == [
        ("a", "acquire"), ("b", "acquire"),
    ]
    assert set(flow["source_event_ids"]) <= ir["source_map"]["by_event_id"].keys()
    assert events[flow["to_event_id"]]["sequencer_id"] == "rx"


def test_watch_uses_the_same_proven_project_routes(tmp_path):
    result = run_watch_once(_project(tmp_path), tmp_path / "watch")
    assert result.ok
    ir = json.loads((tmp_path / "watch" / "timeline_ir.json").read_text(encoding="utf-8"))
    assert _counts(ir) == (2, 1, 1)


@pytest.mark.parametrize("fifo", [False, True])
def test_after_sync_display_cannot_merge_distinct_physical_rounds(tmp_path, capsys, fifo):
    ir = _analyze(_project(tmp_path, second_length=16, alignment="after_first_wait_sync", fifo=fifo), capsys)
    assert _counts(ir) == (2, 2, 2)
    flows = ir["feedback_flows"]
    assert [flow["timing"]["send_available_ns"] for flow in flows] == [24, 24]
    assert [flow["timing"]["arrival_ns"] for flow in flows] == [29, 33]
    assert [_sources(ir)[flow["from_event_id"]]["sequencer_id"] for flow in flows] == ["a", "b"]


def test_different_local_arm_times_share_the_same_barrier_release(tmp_path, capsys):
    ir = _analyze(_project(tmp_path, alignment="none", prewait=(20, 4)), capsys)
    assert _counts(ir) == (2, 1, 1)
    flow, = ir["feedback_flows"]
    assert flow["timing"]["send_available_ns"] == 48
    assert flow["timing"]["arrival_ns"] == 33


@pytest.mark.parametrize("options", [
    {"synchronized": False, "destinations": (None, None), "second_length": 16},
    {"synchronized": False},
    {"destinations": ("rx", None)},
    {"second_config": "1,0,1"},
    {"second_config": "1,2,2"},
    {"second_latency": 6},
])
def test_possible_or_incompatible_round_never_invents_two_words(tmp_path, capsys, options):
    ir = _analyze(_project(tmp_path, **options), capsys)
    assert _counts(ir) == (2, None, 0)
    assert ir["feedback_balance"]["channels"]["16"]["ordering_unknown_receives"] == 2
    assert ir["feedback_flows"] == []
    findings = [item for item in ir["diagnostics"] if item["details"].get("reason") == "tb_write_combine_unknown"]
    assert {event_id for item in findings for event_id in item["related_events"]} == {
        event["id"] for event in ir["events"] if event["kind"] == "acquire"
    }


def test_distinct_destinations_are_independent_without_a_common_clock(tmp_path, capsys):
    ir = _analyze(_project(tmp_path, synchronized=False, destinations=("other", "rx"), reads=1), capsys)
    assert _counts(ir) == (2, 2, 2)
    events = _sources(ir)
    assert {(events[flow["from_event_id"]]["sequencer_id"], events[flow["to_event_id"]]["sequencer_id"])
            for flow in ir["feedback_flows"]} == {("a", "other"), ("b", "rx")}


def test_disabled_combine_retains_separate_words(tmp_path, capsys):
    ir = _analyze(_project(tmp_path, first_config="0,0,1", second_config="0,2,1"), capsys)
    assert _counts(ir) == (2, 2, 2)
    assert all("source_event_ids" not in flow for flow in ir["feedback_flows"])


def test_grouping_does_not_merge_or_reassign_independent_iq_words(tmp_path, capsys):
    ir = _analyze(_project(tmp_path, iq=True), capsys)
    assert _counts(ir) == (2, 1, 1)
    events = _sources(ir)
    iq = [flow for flow in ir["feedback_flows"] if flow["channel"] == "6"]
    assert [(events[flow["from_event_id"]]["sequencer_id"], events[flow["to_event_id"]]["sequencer_id"], flow["payload_word"])
            for flow in iq] == [("a", "a", "I"), ("a", "a", "Q"), ("b", "b", "I"), ("b", "b", "Q")]


def test_same_receiver_keeps_iq_words_that_arrive_after_the_tb_round(tmp_path, capsys):
    project = _project(tmp_path, routed_iq=True)
    analytical = _analyze(project, capsys)
    assert _counts(analytical) == (2, 1, 1)
    assert [(flow["channel"], flow["timing"]["arrival_ns"], flow.get("payload_word"))
            for flow in analytical["feedback_flows"]] == [("16", 37, None), ("17", 41, "I"), ("17", 41, "Q")]
    iq = analytical["feedback_balance"]["channels"]["17"]
    assert (iq["send_payloads"], iq["discarded_payloads"], iq["matched"]) == (2, 0, 2)
    simulated = _analyze(project, capsys, "simulation")
    events = _sources(simulated)
    assert [(flow["channel"], events[flow["to_event_id"]]["t0"]["value"])
            for flow in simulated["feedback_flows"]] == [("16", 137), ("17", 141), ("17", 141)]


@pytest.mark.parametrize("length,expected", [(12, [(133, 15)]), (16, [(133, 3), (137, 12)])])
def test_simulation_receipt_keeps_its_contributors_on_one_flow(tmp_path, capsys, length, expected):
    ir = _analyze(_project(tmp_path, second_length=length, reads=len(expected)), capsys, "simulation")
    events = _sources(ir)
    assert [(events[flow["to_event_id"]]["t0"]["value"], events[flow["to_event_id"]]["meta"]["value"])
            for flow in ir["feedback_flows"]] == expected
    assert [len(flow["source_event_ids"]) for flow in ir["feedback_flows"]] == ([2] if length == 12 else [1, 1])
    assert {events[event_id]["sequencer_id"] for flow in ir["feedback_flows"] for event_id in flow["source_event_ids"]} == {"a", "b"}
