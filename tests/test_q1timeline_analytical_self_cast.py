from __future__ import annotations

import pytest

from q1timeline.analysis.api import analyze_documents, analyze_text


@pytest.mark.parametrize("file_order", [("a", "b"), ("b", "a")])
@pytest.mark.parametrize("first_receiver", ["a", "b"])
@pytest.mark.parametrize("receive", ["fb_pop_data 5,R0", "fb_pull_data R1,R0"])
def test_same_self_cast_id_matches_each_origin_independently(file_order, first_receiver, receive):
    values = {"a": 11, "b": 22}
    documents = {
        f"{name}.q1asm": (f"fb_com_data 5,{values[name]},4\n"
                          f"wait {100 if name == first_receiver else 200}\n{receive}\nstop")
        for name in file_order
    }
    ir = analyze_documents(documents, alignment_mode="none")
    events = {event["id"]: event for event in ir["events"]}
    flows = ir["feedback_flows"]
    last_receiver = next(name for name in file_order if name != first_receiver)
    assert [(events[flow["from_event_id"]]["sequencer_id"], events[flow["to_event_id"]]["sequencer_id"])
            for flow in flows] == [(first_receiver, first_receiver), (last_receiver, last_receiver)]
    for flow in flows:
        sender = events[flow["from_event_id"]]["sequencer_id"]
        assert flow["source"] == str(values[sender])
        assert flow["timing"]["route"] == "self_cast" and flow["timing"]["arrival_ns"] == 60
    balance = ir["feedback_balance"]["channels"]["5"]
    assert balance["sends"] == balance["matched"] == balance["receives"] == 2
    assert balance["discarded_payloads"] == balance["unmatched_receives"] == balance["unconsumed_payloads"] == 0
    assert not [item for item in ir["diagnostics"] if item["category"] == "feedback_route_mismatch"]


def test_selective_pop_discards_only_its_own_preceding_self_cast_word():
    ir = analyze_documents({
        "a.q1asm": "fb_com_data 5,11,4\nfb_com_data 6,13,4\nwait 100\nfb_pop_data 6,R0\nstop",
        "b.q1asm": "fb_com_data 5,22,4\nwait 200\nfb_pull_data R1,R2\nstop",
    }, alignment_mode="none")
    assert [(flow["channel"], flow["source"], flow["target"]) for flow in ir["feedback_flows"]] == [
        ("6", "13", "R0"), ("5", "22", "R2"),
    ]
    channels = ir["feedback_balance"]["channels"]
    assert channels["5"]["discarded_payloads"] == channels["5"]["matched"] == 1
    assert ir["feedback_balance"]["status"] == "balanced"


@pytest.mark.parametrize("receive,balance_key,expects_hint", [
    ("fb_pop_data 5,R0", "5", True), ("fb_pull_data R1,R0", "fifo", False),
])
def test_remote_only_self_cast_cannot_satisfy_or_mark_a_receive(receive, balance_key, expects_hint):
    ir = analyze_documents({
        "a.q1asm": "fb_com_data 5,11,4\nstop",
        "b.q1asm": f"wait 100\n{receive}\nstop",
    }, alignment_mode="none")
    assert ir["feedback_flows"] == []
    channels = ir["feedback_balance"]["channels"]
    assert channels["5"]["unconsumed_payloads"] == 1
    assert channels[balance_key]["unmatched_receives"] == 1
    hints = [item for item in ir["diagnostics"] if item["category"] == "feedback_route_mismatch"]
    assert bool(hints) is expects_hint
    if expects_hint:
        hint, = hints
        assert hint["source"]["file"] == "b.q1asm"
        assert hint["details"]["reason"] == "remote_only_self_cast"
        assert hint["details"]["send_sequencer_ids"] == ["a"]
        assert "configure a route with an ID from 16 to 255" in hint["message"]


def test_non_self_and_explicit_simulation_routes_keep_their_existing_models():
    routed = analyze_documents({
        "a.q1asm": "fb_com_data 16,11,4\nstop",
        "b.q1asm": "wait 700\nfb_pop_data 16,R0\nstop",
    }, alignment_mode="none")
    flow, = routed["feedback_flows"]
    assert flow["timing"]["route"] == "multi_cast_worst_case" and flow["timing"]["arrival_ns"] == 580
    simulated = analyze_documents({
        "a.q1asm": "fb_com_data 5,11,4\nstop",
        "b.q1asm": "wait 0\nfb_pop_data 5,R0\nstop",
    }, execution_mode="simulation", simulation={"routes": [
        {"source": "a", "target": "b", "id": 5, "latency_ns": 20},
    ]})
    flow, = simulated["feedback_flows"]
    assert (flow["channel"], flow["source"], flow["target"], flow["timing_model"]) == ("5", "a", "b", "event_driven")


@pytest.mark.parametrize(("setup", "selector"), [("", "257"), (".DEF SELECTOR 257\n", "$SELECTOR")])
def test_wide_receive_selector_cuts_off_analytical_fifo_matching(setup, selector):
    ir = analyze_text(f"{setup}fb_com_data 1,42,4\nfb_com_data 2,99,4\nwait 100\n"
                      f"fb_pop_data {selector},R0\nfb_pop_data 2,R1\nupd_param 4\nstop")
    wait, = [event for event in ir["events"] if event["kind"] == "wait"]
    boundary, = [event for event in ir["events"] if event["kind"] == "unknown_region"]
    issue = next(event for event in ir["events"] if event["id"] == boundary["meta"]["q1_issue_event_id"])
    diagnostic, = [item for item in ir["diagnostics"]
                   if item["details"].get("reason") == "unsupported_feedback_selector"]

    assert wait["t0"]["value"] == 8 and wait["t1"]["value"] == 108
    assert boundary["t0"] == wait["t1"] and boundary["t1"] is None
    assert boundary["meta"]["q1_proof_cutoff"] is True
    assert issue["kind"] == "q1_issue" and issue["meta"]["op"] == "fb_pop_data"
    assert issue["source"] == boundary["source"] == diagnostic["source"]
    assert diagnostic["source"]["line"] == 4 + bool(setup)
    assert diagnostic["category"] == "analysis_incomplete"
    assert "selector 257 exceeds the documented 8-bit LINQ range" in diagnostic["message"]
    assert diagnostic["related_events"] == [boundary["id"]]
    assert not any(event["kind"] in {"feedback_pop", "upd_param", "stop"} for event in ir["events"])
    assert ir["register_history"] == [] and ir["feedback_flows"] == []
    balance = ir["feedback_balance"]
    assert balance["status"] == "incomplete" and set(balance["channels"]) == {"1", "2"}
    for channel in balance["channels"].values():
        assert channel["status"] == "incomplete"
        assert channel["discarded_payloads"] == 0 and channel["unconsumed_payloads"] is None


def test_supported_pop_before_wide_selector_keeps_its_proven_match():
    prefix = "fb_com_data 1,42,4\nfb_com_data 2,99,4\nwait 100\nfb_pop_data 1,R0\n"
    valid = analyze_text(prefix + "fb_pop_data 2,R1\nstop")
    limited = analyze_text(prefix + "fb_pop_data 257,R1\nfb_pop_data 2,R1\nstop")

    assert [(flow["channel"], flow["timing"]["arrival_ns"]) for flow in valid["feedback_flows"]] == [
        ("1", 60), ("2", 64),
    ]
    assert limited["feedback_flows"] == valid["feedback_flows"][:1]
    balance = limited["feedback_balance"]
    assert balance["status"] == "incomplete"
    assert balance["channels"]["1"]["status"] == "balanced"
    assert balance["channels"]["2"]["status"] == "incomplete"
    assert [write["register"] for write in limited["register_history"]] == ["R0"]


def test_wide_selector_does_not_make_a_peer_self_cast_queue_incomplete():
    ir = analyze_documents({
        "a.q1asm": "fb_com_data 1,42,4\nwait 100\nfb_pop_data 1,R0\nfb_pop_data 257,R1\nstop",
        "b.q1asm": "fb_com_data 1,99,4\nstop",
    }, alignment_mode="none")

    assert [flow["source"] for flow in ir["feedback_flows"]] == ["42"]
    balance = ir["feedback_balance"]
    assert balance["status"] == "incomplete"
    assert balance["channels"]["1"]["unconsumed_payloads"] == 1


def test_wide_selector_only_limits_its_comparison_path():
    ir = analyze_text("fb_com_data 1,42,4\nwait 100\njlt R7,1,@taken\nfb_pop_data 257,R0\nstop\n"
                      "taken: fb_pop_data 1,R1\nstop", branch_policy="explore_both_with_depth_limit")
    branch, = [event for event in ir["events"] if event["kind"] == "branch_region"]
    branch_id = branch["meta"]["branch_id"]
    flow, = ir["feedback_flows"]
    taken, fallthrough = ir["feedback_balance"]["alternatives"]

    assert flow["comparison_assumptions"] == [{branch_id: "taken"}]
    assert taken["assumptions"] == {branch_id: "taken"} and taken["status"] == "balanced"
    assert fallthrough["assumptions"] == {branch_id: "fallthrough"} and fallthrough["status"] == "incomplete"
    assert fallthrough["channels"]["1"]["unconsumed_payloads"] is None


def test_analytical_pop_preserves_the_supported_selector_upper_boundary():
    ir = analyze_text("fb_com_data 255,42,4\nwait 700\nfb_pop_data 255,R0\nstop")
    flow, = ir["feedback_flows"]

    assert flow["channel"] == "255" and flow["timing"]["arrival_ns"] == 580
    assert ir["feedback_balance"]["status"] == "balanced"
    assert any(event["kind"] == "stop" for event in ir["events"])
