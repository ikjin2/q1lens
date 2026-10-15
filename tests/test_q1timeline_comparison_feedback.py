from __future__ import annotations

import pytest

from q1timeline.analysis.api import analyze_documents, analyze_text


def compare(source: str) -> dict:
    return analyze_text(source, file="comparison.q1asm", branch_policy="explore_both_with_depth_limit")


def alternatives(ir: dict) -> dict:
    balance = ir["feedback_balance"]
    assert balance["status"] == "comparison"
    assert balance["channels"] == {}
    return {next(iter(item["assumptions"].values())): item for item in balance["alternatives"]}


def test_mutually_exclusive_send_and_receive_never_form_a_feedback_flow() -> None:
    ir = compare(
        "jlt R7,1,@taken\nwait 1000\nfb_pop_data 1,R0\nnop\nstop\n"
        "taken: fb_com_data 1,123,4\nstop\n"
    )

    assert ir["feedback_flows"] == []
    balances = alternatives(ir)
    assert balances["taken"]["channels"]["1"]["unconsumed_payloads"] == 1
    assert balances["fallthrough"]["channels"]["1"]["unmatched_receives"] == 1
    findings = [d for d in ir["diagnostics"] if d["category"] == "feedback_fifo_imbalance"]
    assert {next(iter(d["details"]["comparison_assumptions"].values())) for d in findings} == {"taken", "fallthrough"}
    assert {d["details"]["status"] for d in findings} == {"over_produced", "under_produced"}


def test_common_prefix_payload_is_available_independently_to_both_paths() -> None:
    ir = compare(
        "fb_com_data 1,123,4\njlt R7,1,@taken\n"
        "wait 1000\nfb_pop_data 1,R0\nnop\nstop\n"
        "taken: wait 2000\nfb_pop_data 1,R0\nnop\nstop\n"
    )

    flows = ir["feedback_flows"]
    assert len(flows) == 2
    assert len({flow["id"] for flow in flows}) == 2
    assert len({flow["from_event_id"] for flow in flows}) == 1
    assert len({flow["to_event_id"] for flow in flows}) == 2
    for path, balance in alternatives(ir).items():
        assert balance["status"] == "balanced"
        assert balance["channels"]["1"]["sends"] == balance["channels"]["1"]["receives"] == 1
        flow = next(flow for flow in flows if flow["to_event_id"].endswith(f":branch-{path}"))
        assert flow["comparison_assumptions"] == [balance["assumptions"]]
    assert not [d for d in ir["diagnostics"] if d["category"] == "feedback_fifo_imbalance"]


def test_alternative_senders_do_not_compete_for_one_common_receiver() -> None:
    ir = analyze_documents({
        "producer.q1asm": "jlt R7,1,@taken\nfb_com_data 16,100,4\nstop\ntaken: fb_com_data 16,200,4\nstop\n",
        "consumer.q1asm": "wait 2000\nfb_pop_data 16,R0\nnop\nstop\n",
    }, alignment_mode="none", branch_policy="explore_both_with_depth_limit")

    assert len(ir["feedback_flows"]) == 2
    assert len({flow["to_event_id"] for flow in ir["feedback_flows"]}) == 1
    assert {flow["source"] for flow in ir["feedback_flows"]} == {"100", "200"}
    assert all(item["status"] == "balanced" for item in alternatives(ir).values())
    assert not [d for d in ir["diagnostics"] if d["category"] == "feedback_fifo_imbalance"]


def test_fifo_discards_in_one_alternative_do_not_consume_the_other_queue() -> None:
    ir = compare(
        "fb_com_data 1,111,4\nfb_com_data 2,222,4\njlt R7,1,@taken\n"
        "wait 1000\nfb_pull_data R0,R1\nstop\n"
        "taken: wait 1000\nfb_pop_data 2,R1\nfb_pull_data R2,R3\nstop\n"
    )

    balances = alternatives(ir)
    assert balances["taken"]["channels"]["1"]["discarded_payloads"] == 1
    assert balances["taken"]["channels"]["2"]["matched"] == 1
    assert balances["fallthrough"]["channels"]["1"]["matched"] == 1
    assert balances["fallthrough"]["channels"]["2"]["unconsumed_payloads"] == 1


@pytest.mark.parametrize("branch_count, scenario_count", [(2, 4), (4, 16), (5, None)])
def test_independent_sequencer_comparisons_are_bounded(branch_count: int, scenario_count: int | None) -> None:
    documents = {
        f"seq{index}.q1asm": (
            f"fb_com_data {index + 1},123,4\njlt R7,1,@taken\n"
            f"wait 1000\nfb_pop_data {index + 1},R0\nstop\n"
            f"taken: wait 2000\nfb_pop_data {index + 1},R0\nstop\n"
        )
        for index in range(branch_count)
    }
    ir = analyze_documents(documents, alignment_mode="none", branch_policy="explore_both_with_depth_limit")

    if scenario_count is None:
        assert ir["feedback_flows"] == []
        assert ir["feedback_balance"] == {"status": "unknown", "channels": {}, "reason": "branch_comparison_limit", "alternative_count": 32}
        assert any(d["details"].get("reason") == "branch_comparison_limit" for d in ir["diagnostics"])
    else:
        balance = ir["feedback_balance"]
        assert len(balance["alternatives"]) == scenario_count
        assert all(item["status"] == "balanced" for item in balance["alternatives"])
        assert len(ir["feedback_flows"]) == branch_count * 2
        assert all(len(flow["comparison_assumptions"]) == scenario_count // 2 for flow in ir["feedback_flows"])


def test_a_common_flow_is_not_duplicated_across_comparison_alternatives() -> None:
    ir = compare(
        "fb_com_data 1,123,4\nwait 1000\nfb_pop_data 1,R0\n"
        "jlt R7,1,@taken\nfb_com_data 2,100,4\nstop\n"
        "taken: fb_com_data 2,200,4\nstop\n"
    )

    assert len(ir["feedback_flows"]) == 1
    assert len(ir["feedback_flows"][0]["comparison_assumptions"]) == 2


def test_ordinary_feedback_schema_and_counts_remain_unchanged() -> None:
    ir = analyze_text("fb_com_data 1,123,4\nwait 1000\nfb_pop_data 1,R0\nnop\nstop\n")

    assert ir["feedback_balance"]["status"] == "balanced"
    assert "alternatives" not in ir["feedback_balance"]
    assert "comparison_assumptions" not in ir["feedback_flows"][0]
