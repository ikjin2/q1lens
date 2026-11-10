import pytest

from q1timeline.analysis.api import analyze_documents, analyze_text


_TRIGGER_WAIT = "set_cond 1,0,1,4\nwait_trigger 3,4\nstop"


def _run(source=_TRIGGER_WAIT, *, assumptions=None, **config):
    return analyze_text(source, file="s.q1asm", sequencer_id="s", execution_mode="simulation",
                        branch_assumptions=assumptions, simulation={"startup_delay_ns": 100, **config})


def _event(ir, kind, *, path=None):
    event, = [event for event in ir["events"] if event["sequencer_id"] == "s" and event["kind"] == kind
              and event["meta"].get("branch_comparison_path") == path]
    return event


def _condition_issue(ir):
    issue, = [event for event in ir["events"] if event["sequencer_id"] == "s" and event["kind"] == "q1_issue"
              and event["meta"].get("rt_condition_control")]
    return issue


def _assert_armed_wait(ir, kind, end):
    issue = _condition_issue(ir)
    command, = [event for event in ir["events"] if event["sequencer_id"] == "s" and event["kind"] == "q1_issue"
                and event["source"]["line"] == 2]
    wait = _event(ir, kind)
    assert (wait["t0"]["value"], wait["t1"]["value"], wait["meta"]["aligned_t0"]) == (100, end, 0)
    assert wait["meta"]["pending"] is True
    assert wait["meta"]["rt_condition_decision"] is True
    assert wait["meta"]["rt_condition_issue_event_id"] == issue["id"]
    assert wait["meta"]["rt_condition_branch_id"] == issue["meta"]["branch_id"]
    assert wait["meta"]["q1_issue_event_id"] == command["id"]
    assert wait["meta"]["rt_packet_id"] == command["meta"]["rt_packet_id"]
    assert "rt_execution_ns" not in wait["meta"]
    return wait


@pytest.mark.parametrize(("config", "status", "end"), [
    ({}, "blocked", 100),
    ({"max_time_ns": 112, "inputs": {"triggers": [{"target": "s", "address": 3, "time_ns": 120}]}},
     "step_limit", 112),
    ({"feedback_queue_capacity": 1, "inputs": {"feedback": [
        {"target": "s", "id": identifier, "time_ns": 110, "value": identifier} for identifier in (1, 2)
    ]}}, "error", 110),
])
def test_pending_conditional_trigger_wait_retains_its_execute_decision(config, status, end):
    ir = _run(**config)
    assert ir["execution"]["s"]["status"] == status
    _assert_armed_wait(ir, "trigger_wait", end)
    assert not any(event["kind"] in {"trigger_receive", "wait_trigger"} for event in ir["events"])


def test_pending_conditional_synq_wait_retains_its_execute_decision():
    ir = analyze_documents({"s.q1asm": _TRIGGER_WAIT.replace("wait_trigger 3,4", "wait_sync 4"),
                            "peer.q1asm": "jmp @end\nwait_sync 4\nend: stop"},
                           execution_mode="simulation", simulation={"startup_delay_ns": 100})
    assert ir["execution"]["s"]["status"] == "blocked"
    assert ir["execution"]["peer"]["status"] == "completed"
    _assert_armed_wait(ir, "sync_wait", 100)


def test_completed_conditional_wait_keeps_only_primary_decision_and_distinct_times():
    ir = _run(inputs={"triggers": [{"target": "s", "address": 3, "time_ns": 120}]})
    primary = _event(ir, "wait_trigger")
    auxiliary = _event(ir, "trigger_wait")
    marker = _event(ir, "trigger_receive")
    assert (primary["t0"]["value"], primary["meta"]["rt_execution_ns"], primary["t1"]["value"]) == (100, 120, 124)
    assert (auxiliary["t0"]["value"], auxiliary["t1"]["value"], marker["t0"]["value"]) == (100, 120, 120)
    assert primary["meta"]["input_event_ids"] == [marker["id"]]
    assert auxiliary["meta"]["rt_packet_id"] == primary["meta"]["rt_packet_id"]
    assert primary["meta"]["rt_condition_issue_event_id"] == _condition_issue(ir)["id"]
    assert "rt_condition_issue_event_id" not in auxiliary["meta"]


def test_skip_and_unresolved_conditions_do_not_record_a_wait_arm():
    skipped = _run(_TRIGGER_WAIT.replace("1,0,1,4", "1,0,0,4"))
    assert _event(skipped, "conditional_skip")["meta"]["conditional_skipped"] is True
    unknown = analyze_documents({
        "s.q1asm": "set_latch_en 1,4\nwait 12\nset_cond 1,1,0,8\nwait_trigger 3,4\nstop",
        "tx.q1asm": "acquire 0,0,4\nstop",
    }, execution_mode="simulation", sequences={"tx": {"acquisitions": {"a": {"index": 0, "num_bins": 1}}}},
        simulation={"startup_delay_ns": 100,
                    "trigger_routes": [{"source": "tx", "target": "s", "address": 1, "latency_ns": 8}],
                    "inputs": {"acquisitions": [{"sequencer": "tx", "acquisition_index": 0,
                                                 "bin_index": 0, "i": 17, "q": -9}]}})
    assert unknown["execution"]["s"]["status"] == "blocked"
    assert any(diagnostic["category"] == "acquisition_trigger_outcome_unknown" for diagnostic in unknown["diagnostics"])
    for ir in (skipped, unknown):
        assert not any(event["kind"] in {"wait_trigger", "trigger_wait"} or "rt_condition_decision" in event["meta"]
                       for event in ir["events"])


def test_compared_armed_wait_preserves_condition_links_and_terminal_evidence():
    ir = _run(assumptions={"s:set_cond:0:1": "both"})
    recorded = _assert_armed_wait(ir, "trigger_wait", 100)
    executed = _event(ir, "trigger_wait", path="taken")
    skipped = _event(ir, "conditional_skip", path="fallthrough")
    terminal = _event(ir, "branch_terminal", path="taken")
    for key in ("rt_condition_decision", "rt_condition_issue_event_id", "rt_condition_branch_id", "rt_packet_id"):
        assert executed["meta"][key] == recorded["meta"][key]
    assert skipped["meta"]["rt_condition_issue_event_id"] == recorded["meta"]["rt_condition_issue_event_id"]
    assert skipped["meta"]["rt_packet_id"] == f'{recorded["meta"]["rt_packet_id"]}:condition-fallthrough:s:set_cond:0:1'
    assert terminal["meta"]["comparison_terminal"]["source_event_ids"] == [executed["id"]]
