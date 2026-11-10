import pytest

from q1timeline.analysis.api import analyze_text
from q1timeline.analysis.interpreter import interpret_program
from q1timeline.analysis.values import Concrete
from q1timeline.q1asm.parser import parse_q1asm


FILE = "status.q1asm"
SEQ = "seq0"


def compare(source, op, target="end", line=2):
    assumptions = {f"{SEQ}:branch:{FILE}:{line}:{op}:{target}": "both"}
    ir = analyze_text(source, file=FILE, sequencer_id=SEQ, branch_assumptions=assumptions)
    state = interpret_program(parse_q1asm(source, file=FILE), sequencer_id=SEQ, branch_assumptions=assumptions)
    return ir, state


@pytest.mark.parametrize("op,jump_duration", [("jz", 16), ("jge", 24), ("jb", 16)])
def test_compare_status_paths_keep_independent_q1_and_rt_times(op, jump_duration):
    ir, state = compare(f"cmp R0,5\n{op} @end\nwait 12\nend: upd_param 4\nstop", op)
    common, = [event for event in ir["events"] if event["kind"] == "q1_issue" and event["meta"].get("op") == op]
    branch_id = common["meta"]["branch_id"]
    assert common["meta"]["branch_comparison_supported"]
    assert common["meta"]["comparison_paths"] == ["taken", "fallthrough"]
    assert common["meta"]["comparison_q1_durations_ns"] == {"taken": jump_duration, "fallthrough": 4}
    assert common["t0"]["value"] == 12

    packets_by_path = {
        path: [packet for packet in state.rt_packets if packet.meta.get("branch_comparison_path") == path]
        for path in ("taken", "fallthrough")
    }
    assert [(p.op, p.q1_issue_t0, p.rt_t0) for p in packets_by_path["taken"]] == [
        ("upd_param", Concrete(12 + jump_duration), Concrete(0)),
    ]
    assert [(p.op, p.q1_issue_t0, p.rt_t0) for p in packets_by_path["fallthrough"]] == [
        ("wait", Concrete(16), Concrete(0)), ("upd_param", Concrete(20), Concrete(12)),
    ]
    stops = {event["meta"]["branch_comparison_path"]: event["t0"]["value"]
             for event in ir["events"] if event["kind"] == "stop"}
    assert stops == {"taken": 4, "fallthrough": 16}

    events = {event["id"]: event for event in ir["events"]}
    for packet in state.rt_packets:
        event, = [event for event in events.values() if event["kind"] == packet.op
                  and event["meta"].get("rt_packet_id") == packet.id]
        issue = events[event["meta"]["q1_issue_event_id"]]
        assert event["source"] == issue["source"] == ir["source_map"]["by_event_id"][event["id"]]
        assert issue["meta"]["rt_packet_id"] == packet.id
        assert event["meta"]["branch_comparison_branch_id"] == branch_id


def test_compare_status_paths_keep_branch_local_setter_sources():
    ir, _ = compare("cmp R0,5\njz @end\nset_freq 400\nend: upd_param 4\nstop", "jz")
    by_path = {event["meta"]["branch_comparison_path"]: event for event in ir["events"] if event["kind"] == "upd_param"}
    setter, = [event for event in ir["events"] if event["kind"] == "latched_state_pending"]
    assert setter["meta"]["branch_comparison_path"] == "fallthrough"
    assert setter["source"]["line"] == 3
    assert "applied_setter_event_ids" not in by_path["taken"]["meta"]
    assert by_path["fallthrough"]["meta"]["applied_state"]["frequency"] == 400
    assert by_path["fallthrough"]["meta"]["applied_setter_event_ids"]["frequency"] == setter["id"]


@pytest.mark.parametrize("op,complement", [("jz", "jnz"), ("jge", "jl"), ("jb", "jae")])
def test_unchanged_flags_prove_the_complement_inside_each_arm(op, complement):
    ir, _ = compare(f"cmp R0,5\n{op} @join\nwait 12\njoin: {complement} @other\nupd_param 4\nstop\nother: wait 8\nstop", op, "join")
    downstream = {event["meta"]["branch_comparison_path"]: event for event in ir["events"]
                  if event["kind"] == "q1_issue" and event["meta"].get("op") == complement}
    for path, taken, duration in [("taken", False, 4), ("fallthrough", True, 16)]:
        assert downstream[path]["meta"]["branch_decision"] == "concrete"
        assert downstream[path]["meta"]["branch_taken"] is taken
        assert downstream[path]["duration"]["value"] == duration


def test_new_flag_writer_replaces_an_assumed_predicate():
    ir, _ = compare("cmp R0,5\njz @end\nnop\nend: cmp R0,R0\njnz @other\nwait 4\nstop\nother: wait 8\nstop", "jz")
    downstream = [event for event in ir["events"] if event["kind"] == "q1_issue" and event["meta"].get("op") == "jnz"]
    assert len(downstream) == 2
    assert all(event["meta"]["branch_decision"] == "concrete" and event["meta"]["branch_taken"] is False
               for event in downstream)


def test_known_status_branch_ignores_an_explicit_both_request():
    ir, _ = compare("cmp R0,R0\njz @end\nwait 12\nend: upd_param 4\nstop", "jz")
    branch, = [event for event in ir["events"] if event["kind"] == "q1_issue" and event["meta"].get("op") == "jz"]
    assert branch["meta"]["branch_decision"] == "concrete"
    assert branch["meta"]["branch_taken"] is True
    assert branch["duration"]["value"] == 16
    assert not any(event["meta"].get("branch_comparison_path") for event in ir["events"])


@pytest.mark.parametrize("operand,target", [("@entry", "entry"), ("R1", "R1")])
def test_unsupported_status_target_keeps_explicit_comparison_incomplete(operand, target):
    ir, _ = compare(f"entry: cmp R0,5\njz {operand}\nwait 12\nstop", "jz", target)
    branch, = [event for event in ir["events"] if event["kind"] == "q1_issue" and event["meta"].get("op") == "jz"]
    assert branch["meta"]["branch_comparison_supported"] is False
    assert not any(event["meta"].get("branch_comparison_path") for event in ir["events"])
    assert any(diagnostic["category"] == "analysis_incomplete" and "known forward target" in diagnostic["message"]
               for diagnostic in ir["diagnostics"])


def test_automatic_comparison_keeps_the_existing_depth_limit():
    source = "cmp R0,5\njz @join\nwait 12\njoin: cmp R1,7\njb @end\nwait 8\nend: stop"
    ir = analyze_text(source, file=FILE, sequencer_id=SEQ, branch_policy="explore_both_with_depth_limit")
    downstream = [event for event in ir["events"] if event["kind"] == "q1_issue" and event["meta"].get("op") == "jb"]
    assert {event["meta"]["branch_comparison_path"] for event in downstream} == {"taken", "fallthrough"}
    assert all(event["meta"]["branch_comparison_supported"] is False for event in downstream)
    assert all("comparison_paths" not in event["meta"] for event in downstream)
    assert any(diagnostic["category"] == "analysis_incomplete" and "comparison depth limit" in diagnostic["message"]
               for diagnostic in ir["diagnostics"])


@pytest.mark.parametrize("assumption", [None, "both"])
def test_nested_direct_comparison_reports_the_depth_limit_in_each_path(assumption):
    source = "cmp R0,5\njz @join\nwait 12\njoin: jlt R1,7,@end\nwait 8\nend: stop"
    branch_id = f"{SEQ}:branch:{FILE}:4:jlt:end"
    ir = analyze_text(source, file=FILE, sequencer_id=SEQ, branch_policy="explore_both_with_depth_limit",
                      branch_assumptions={branch_id: assumption} if assumption else None)
    issues = [event for event in ir["events"] if event["kind"] == "q1_issue"]
    outer, = [event for event in issues if event["meta"]["op"] == "jz"]
    assert outer["meta"]["comparison_paths"] == ["taken", "fallthrough"]
    inner = [event for event in issues if event["meta"]["op"] == "jlt"]
    assert {event["meta"]["branch_comparison_path"] for event in inner} == {"taken", "fallthrough"}
    assert all(event["meta"]["branch_comparison_supported"] is False and "comparison_paths" not in event["meta"]
               for event in inner)
    diagnostics = [item for item in ir["diagnostics"] if item["category"] == "analysis_incomplete"
                   and item.get("details", {}).get("branch_id") == branch_id]
    assert len(diagnostics) == 2
    assert {item["details"]["branch_comparison_path"] for item in diagnostics} == {"taken", "fallthrough"}
    assert all(item["source"] == inner[0]["source"] and "comparison depth limit" in item["message"] for item in diagnostics)


@pytest.mark.parametrize("operand,target", [("@entry", "entry"), ("R2", "R2"), ("1000", "1000")])
@pytest.mark.parametrize("explicit", [False, True])
def test_direct_comparison_requires_a_known_forward_target(operand, target, explicit):
    source = f"entry: nop\njge R0,7,{operand}\nwait 8\nstop"
    branch_id = f"{SEQ}:branch:{FILE}:2:jge:{target}"
    ir = analyze_text(source, file=FILE, sequencer_id=SEQ,
                      branch_policy="collapse_unresolved" if explicit else "explore_both_with_depth_limit",
                      branch_assumptions={branch_id: "both"} if explicit else None)
    branch, = [event for event in ir["events"] if event["kind"] == "q1_issue" and event["meta"]["op"] == "jge"]
    assert branch["meta"]["branch_comparison_supported"] is False
    assert "comparison_paths" not in branch["meta"]
    diagnostic, = [item for item in ir["diagnostics"] if item["category"] == "analysis_incomplete"
                   and item.get("details", {}).get("branch_id") == branch_id]
    assert diagnostic["source"] == branch["source"]
    assert "known forward target" in diagnostic["message"]


@pytest.mark.parametrize("path", [None, "collapsed", "taken", "fallthrough"])
def test_direct_branch_choice_precedes_automatic_comparison(path):
    source = "jge R0,7,@end\nwait 8\nend: stop"
    branch_id = f"{SEQ}:branch:{FILE}:1:jge:end"
    ir = analyze_text(source, file=FILE, sequencer_id=SEQ, branch_policy="explore_both_with_depth_limit",
                      branch_assumptions={branch_id: path} if path else None)
    branch, = [event for event in ir["events"] if event["kind"] == "q1_issue" and event["meta"]["op"] == "jge"]
    assert branch["meta"]["branch_comparison_supported"] is True
    assert branch["meta"]["assumed_branch_path"] == (path or "both")
    if path is None:
        assert branch["meta"]["comparison_paths"] == ["taken", "fallthrough"]
        assert branch["meta"]["comparison_q1_durations_ns"] == {"taken": 24, "fallthrough": 4}
    else:
        assert "comparison_paths" not in branch["meta"]
        assert branch["meta"]["assumption_source"] == "branch_assumption"
    assert not any(item["category"] == "analysis_incomplete" and item.get("details", {}).get("branch_id") == branch_id
                   for item in ir["diagnostics"])
