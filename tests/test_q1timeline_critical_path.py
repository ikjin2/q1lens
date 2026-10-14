from __future__ import annotations

from typing import Any

import pytest

from q1timeline.analysis.api import analyze_documents, analyze_text
from q1timeline.analysis.critical_path import critical_path_from_timeline


def test_one_sequencer_critical_path_uses_concrete_rt_work() -> None:
    ir = analyze_text("wait 8\nplay 0,1,12\nstop\n", file="seq.q1asm")

    critical_path = ir["critical_path"]

    assert critical_path["status"] == "exact"
    assert critical_path["duration_ns"] == 20
    assert critical_path["work_duration_ns"] == 20
    assert critical_path["dependency_wait_ns"] == 0
    assert [event["kind"] for event in critical_path["events"]] == ["wait", "play"]
    assert critical_path["sequencer_ids"] == ["seq", "seq"]


def test_two_independent_sequencers_select_the_longer_concrete_chain() -> None:
    ir = analyze_documents(
        {
            "short.q1asm": "wait 12\nstop\n",
            "long.q1asm": "wait 20\nstop\n",
        },
        alignment_mode="none",
    )

    critical_path = ir["critical_path"]

    assert critical_path["status"] == "exact"
    assert critical_path["duration_ns"] == 20
    assert critical_path["event_ids"] == ["long:e1"]
    assert critical_path["schedule_makespan_ns"] == 20


def test_cross_sequencer_feedback_is_a_dependency_edge() -> None:
    ir = analyze_documents(
        {
            "producer.q1asm": "wait 100\nfb_com_data 16,123,4\nstop\n",
            "consumer.q1asm": "wait 1000\nfb_pull_data R4,R5\nplay 0,1,20\nstop\n",
        },
        alignment_mode="none",
    )

    critical_path = ir["critical_path"]
    feedback_edges = [
        edge for edge in critical_path["dependency_edges"] if "feedback" in edge["kinds"]
    ]

    assert critical_path["status"] == "exact"
    assert critical_path["sequencer_ids"] == ["producer", "producer", "consumer", "consumer"]
    assert critical_path["duration_ns"] == 1020
    assert critical_path["dependency_wait_ns"] == 896
    assert feedback_edges == [
        {
            "from_event_id": "producer:e3",
            "to_event_id": "consumer:e3",
            "kinds": ["feedback"],
            "lag_ns": 896,
        }
    ]


def test_equal_independent_paths_use_lexicographic_event_id_tie_break() -> None:
    ir = analyze_documents(
        {
            "z.q1asm": "wait 20\nstop\n",
            "a.q1asm": "wait 20\nstop\n",
        },
        alignment_mode="none",
    )

    assert ir["critical_path"]["event_ids"] == ["a:e1"]


def test_symbolic_region_returns_a_partial_concrete_lower_bound() -> None:
    ir = analyze_text("wait 8\nwait R0\nstop\n", file="seq.q1asm")

    critical_path = ir["critical_path"]
    reason_codes = {reason["code"] for reason in critical_path["reasons"]}

    assert critical_path["status"] == "partial"
    assert critical_path["duration_ns"] == 8
    assert critical_path["event_ids"] == ["seq:e1"]
    assert {
        "analysis_incomplete",
        "non_concrete_timing",
        "runtime_dependent_control_flow",
        "unrepresented_rt_packet",
    } <= reason_codes


def test_assumed_runtime_branch_marks_the_concrete_path_partial() -> None:
    ir = analyze_text(
        "jge R0,1,@taken\n"
        "wait 4\n"
        "stop\n"
        "taken: wait 8\n"
        "stop\n",
        file="seq.q1asm",
    )

    critical_path = ir["critical_path"]

    assert critical_path["status"] == "partial"
    assert critical_path["duration_ns"] == 8
    assert critical_path["reasons"] == [
        {
            "code": "runtime_dependent_control_flow",
            "count": 1,
            "message": "A branch or unknown region depends on a runtime choice or assumption.",
        }
    ]


def test_queue_prefill_uncertainty_does_not_change_concrete_dependency_path() -> None:
    ir = analyze_text("wait_sync 4\nupd_param 4\nnop\nnop\nupd_param 4\nstop\n")
    incomplete = [item for item in ir["diagnostics"] if item["category"] == "analysis_incomplete"]
    incomplete_events = [event for event in ir["events"] if event["kind"] == "analysis_incomplete"]

    assert incomplete and incomplete_events
    assert all(item["details"]["reason"] == "sync_start_uncertain" for item in incomplete)
    assert all(item["details"]["analysis_scope"] == "rt_queue" for item in incomplete)
    assert all(event["meta"]["analysis_scope"] == "rt_queue" for event in incomplete_events)
    assert ir["critical_path"]["status"] == "exact"
    assert ir["critical_path"]["duration_ns"] == 12
    assert ir["critical_path"]["reasons"] == []


@pytest.mark.parametrize("wait", ["wait R0", "wait_trigger 1,4"])
def test_unknown_runtime_or_trigger_timing_remains_path_uncertainty(wait: str) -> None:
    ir = analyze_text(f"{wait}\nupd_param 4\nstop\n")

    assert ir["critical_path"]["status"] != "exact"
    assert "analysis_incomplete" in {reason["code"] for reason in ir["critical_path"]["reasons"]}
    incomplete = [item for item in ir["diagnostics"] if item["category"] == "analysis_incomplete"]
    assert any(item.get("details", {}).get("analysis_scope") != "rt_queue" for item in incomplete)


def test_missing_sync_participant_remains_path_uncertainty() -> None:
    ir = analyze_documents({
        "waiting.q1asm": "wait_sync 4\nupd_param 4\nstop\n",
        "missing.q1asm": "wait 8\nstop\n",
    }, alignment_mode="none")

    assert ir["critical_path"]["status"] == "partial"
    assert "analysis_incomplete" in {reason["code"] for reason in ir["critical_path"]["reasons"]}


@pytest.mark.parametrize(
    "source, diagnostic_category, reason_code, duration_ns",
    [
        ("wait 8\nfake_op 4\nstop\n", "analysis_incomplete", "analysis_incomplete", 8),
        ("wait 8\nplay 1\nstop\n", "invalid_argument_count", "invalid_input", 8),
        (
            "set_cond 1,1,0,4\nplay 0,1,40\nset_cond 0,0,0,4\nstop\n",
            "analysis_incomplete", "analysis_incomplete", 40,
        ),
    ],
)
def test_instruction_diagnostics_prevent_exact_critical_path(
    source: str, diagnostic_category: str, reason_code: str, duration_ns: int,
) -> None:
    ir = analyze_text(source, file="seq.q1asm")

    assert any(diagnostic["category"] == diagnostic_category for diagnostic in ir["diagnostics"])
    assert ir["critical_path"]["status"] == "partial"
    assert ir["critical_path"]["duration_ns"] == duration_ns
    assert reason_code in {reason["code"] for reason in ir["critical_path"]["reasons"]}


def test_dependency_cycle_is_reported_without_fabricating_a_path() -> None:
    events = [
        _event("a", sequencer_id="seq0", start_ns=0, end_ns=0),
        _event("b", sequencer_id="seq1", start_ns=0, end_ns=0),
    ]
    flows = [
        {"id": "flow-a-b", "from_event_id": "a", "to_event_id": "b"},
        {"id": "flow-b-a", "from_event_id": "b", "to_event_id": "a"},
    ]

    critical_path = critical_path_from_timeline(events, flows)

    assert critical_path["status"] == "unavailable"
    assert critical_path["duration_ns"] is None
    assert critical_path["event_ids"] == []
    assert critical_path["reasons"] == [
        {
            "code": "dependency_cycle",
            "count": 2,
            "message": "The concrete dependency graph contains a cycle.",
        }
    ]


def test_backward_feedback_edge_is_rejected_and_marks_result_partial() -> None:
    events = [
        _event("late-send", sequencer_id="producer", start_ns=10, end_ns=20),
        _event("early-receive", sequencer_id="consumer", start_ns=0, end_ns=0),
    ]
    flows = [
        {
            "id": "backward-flow",
            "from_event_id": "late-send",
            "to_event_id": "early-receive",
        }
    ]

    critical_path = critical_path_from_timeline(events, flows)

    assert critical_path["status"] == "partial"
    assert critical_path["counts"]["rejected_feedback_edge_count"] == 1
    assert critical_path["counts"]["feedback_edge_count"] == 0
    assert critical_path["reasons"][0]["code"] == "backward_or_overlapping_feedback_edge"


def test_rt_packet_mirrors_are_merged_instead_of_double_counted() -> None:
    ir = analyze_text("play 0,1,10\nstop\n", file="seq.q1asm")

    critical_path = ir["critical_path"]

    assert critical_path["duration_ns"] == 10
    assert critical_path["work_duration_ns"] == 10
    assert critical_path["counts"]["merged_event_count"] == 1
    assert critical_path["events"][0]["merged_event_ids"] == ["seq:e1", "seq:e2"]
    assert all(not event_id.startswith("seq:e0") for event_id in critical_path["event_ids"])


def _event(
    event_id: str,
    *,
    sequencer_id: str,
    start_ns: int,
    end_ns: int,
) -> dict[str, Any]:
    duration_ns = end_ns - start_ns
    return {
        "id": event_id,
        "sequencer_id": sequencer_id,
        "lane": "rt.feedback",
        "kind": "feedback_com",
        "label": event_id,
        "confidence": "exact",
        "duration": {
            "kind": "concrete",
            "value": duration_ns,
            "display": str(duration_ns),
        },
        "meta": {"aligned_t0": start_ns, "aligned_t1": end_ns},
    }
