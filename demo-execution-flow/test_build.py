"""Check the demo's evidence boundaries against actual simulation records."""
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("execution_flow_build", Path(__file__).with_name("build.py"))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.fixture(scope="module")
def cases():
    return {case["id"]: case for case in builder.build_data()["cases"]}


def scenario(cases, case_id, scenario_id="normal"):
    return next(item for item in cases[case_id]["scenarios"] if item["id"] == scenario_id)


def at_line(run, line):
    return [step for step in run["steps"] if step["line"] == line]


def test_retry_occurrences_use_the_feedback_actually_consumed(cases):
    run = scenario(cases, "retry")
    pops, branches = at_line(run, 8), at_line(run, 10)
    assert run["status"] == "completed"
    assert [step["input"]["value"] for step in pops] == [0, 0, 1]
    assert [step["branch"]["taken"] for step in branches] == [False, False, True]
    assert len({step["id"] for step in pops}) == 3
    for pop, branch in zip(pops, branches):
        assert branch["reads"][0]["writerId"] == pop["id"]
        assert branch["reads"][0]["value"] == pop["input"]["value"]
        assert pop["q1End"] < pop["writes"][0]["readyAt"] <= branch["q1Start"]
    assert [step["nextLine"] for step in branches] == [11, 11, 17]
    early = scenario(cases, "retry", "early")
    assert len(at_line(early, 8)) == 2
    assert len(early["steps"]) < len(run["steps"])


def test_missing_feedback_reports_real_underflow_without_fabricated_execution(cases):
    run = scenario(cases, "retry", "missing")
    assert run["status"] == "error"
    assert run["reason"] == "RT queue underflow at 272 ns."
    assert run["pending"]["line"] == 8
    assert run["pending"]["op"] == "fb_pop_data"
    assert [step["input"]["value"] for step in at_line(run, 8)] == [0, 0]
    assert len(at_line(run, 10)) == 2
    assert run["steps"][-1]["line"] == 7


def test_bins_preserve_the_transient_three_before_reset(cases):
    run = scenario(cases, "bins")
    increments, branches = at_line(run, 13), at_line(run, 15)
    assert run["status"] == "completed"
    assert [step["writes"][0]["after"] for step in increments] == [1, 2, 3, 1]
    assert [step["branch"]["taken"] for step in branches] == [True, True, False, True]
    third = branches[2]
    assert third["reads"][0]["value"] == 3
    assert third["reads"][0]["writerId"] == increments[2]["id"]
    assert third["nextLine"] == 16
    reset, = at_line(run, 16)
    assert (reset["writes"][0]["before"], reset["writes"][0]["after"]) == (3, 0)
    assert [item["bin_index"] for item in run["scenario"]["inputs"]["acquisitions"]] == [0, 1, 2, 0]


def test_golden_branch_uses_actual_cmp_occurrence_and_values(cases):
    run = scenario(cases, "golden", "no_measurement")
    compare, = at_line(run, 14)
    branch, = at_line(run, 15)
    assert branch["branch"]["producerId"] == compare["id"]
    assert branch["branch"]["condition"] == "8000 < 20"
    assert branch["branch"]["taken"] is False
    assert [item["raw"] for item in branch["branch"]["operands"]] == ["R6", "R0"]
    assert [item["value"] for item in branch["branch"]["operands"]] == [8000, 20]
    assert [item["writerId"] for item in branch["branch"]["operands"]] == [
        at_line(run, 12)[0]["id"], at_line(run, 3)[0]["id"]]
    assert at_line(run, 22)[0]["writes"][0]["after"] == -944
    assert at_line(run, 23)[0]["writes"][0]["after"] == 944
    jump, = at_line(run, 29)
    assert jump["branch"]["producerId"] is None
    assert jump["branch"]["operands"] == []


def test_golden_stops_at_the_real_engine_boundary_and_retains_pending_write(cases):
    absent = scenario(cases, "golden", "no_measurement")
    assert absent["status"] == "blocked"
    assert absent["statusLine"] == 82
    assert "Acquisition requires" in absent["reason"]
    # Q1 issuance can run ahead of the RT acquisition which blocks this run.
    assert absent["steps"][-1]["line"] == 89
    supplied = scenario(cases, "golden", "provided_measurement")
    assert supplied["status"] == "blocked"
    assert supplied["statusLine"] == 100
    assert "muls32" in supplied["reason"]
    assert not any(step["op"] == "muls32" for step in supplied["steps"])
    assert [step["input"]["value"] for step in supplied["steps"] if step["input"]] == [7812, 3906]
    final_pop, = at_line(supplied, 98)
    assert final_pop["writes"] == []
    assert final_pop["pendingWrites"] == [{"register": "R10", "value": 3906}]


def test_every_read_points_to_a_committed_available_writer(cases):
    for case in cases.values():
        for run in case["scenarios"]:
            by_id = {step["id"]: step for step in run["steps"]}
            assert len(by_id) == len(run["steps"])
            for step in run["steps"]:
                for read in step["reads"]:
                    if read["writerId"] is None:
                        continue
                    writer = by_id[read["writerId"]]
                    write = next(item for item in writer["writes"] if item["register"] == read["register"])
                    assert write["readyAt"] <= step["q1Start"]
                    assert write["after"] == read["value"]


def replay(cases, case_id, choices, scenario_id="normal"):
    case = cases[case_id]
    program = builder.parse_q1asm("\n".join(case["source"]["lines"]), file=case["source"]["path"])
    config = next(item for item in builder.configurations(case_id) if item["id"] == scenario_id)
    return builder.project_scenario(case_id, program, case["params"], config, choices)


def test_selected_occurrence_recomputes_suffix_and_timing_without_changing_predicate(cases):
    original = scenario(cases, "retry")
    selected = at_line(original, 10)[1]
    choice = {"pc": selected["pc"], "occurrence": 2, "taken": True}
    changed = replay(cases, "retry", [choice])
    index = original["steps"].index(selected)
    assert changed["steps"][:index] == original["steps"][:index]
    branch = changed["steps"][index]
    assert branch["branch"]["condition"] == "0 ≥ 1"
    assert branch["branch"]["naturalTaken"] is False
    assert branch["branch"]["taken"] is True
    assert branch["branch"]["overridden"] is True
    assert branch["reads"] == selected["reads"]
    assert branch["q1End"] - branch["q1Start"] == 24
    assert selected["q1End"] - selected["q1Start"] == 4
    assert [step["line"] for step in changed["steps"][index + 1:]] == [17, 18, 19, 20]
    assert changed["steps"][index + 1]["q1Start"] == branch["q1End"]
    assert changed["choices"] == [choice]
    assert replay(cases, "retry", []) == original


def test_changed_path_preserves_missing_acquisition_and_unsupported_boundaries(cases):
    bins = scenario(cases, "bins")
    branch = at_line(bins, 15)[2]
    changed = replay(cases, "bins", [{"pc": branch["pc"], "occurrence": 3, "taken": True}])
    assert at_line(changed, 11)[3]["reads"][0]["value"] == 3
    assert changed["status"] == "blocked" and changed["statusLine"] == 11
    assert "Acquisition requires" in changed["reason"]
    golden = scenario(cases, "golden", "provided_measurement")
    branch = at_line(golden, 89)[1]
    changed = replay(cases, "golden", [{"pc": branch["pc"], "occurrence": 2, "taken": False}], "provided_measurement")
    assert changed["status"] == "blocked" and changed["statusLine"] == 100
    assert "muls32" in changed["reason"]
    assert not any(step["op"] == "muls32" for step in changed["steps"])
    assert [step["input"]["value"] for step in changed["steps"] if step["input"]] == [7812, 3906]
    assert at_line(changed, 97)[0]["q1Start"] == 392  # Real feedback arrival now stalls the shorter path.


def test_demo_rejects_duplicate_choices(cases):
    branch = at_line(scenario(cases, "retry"), 10)[0]
    choice = {"pc": branch["pc"], "occurrence": 1, "taken": True}
    with pytest.raises(ValueError, match="only have one choice"):
        replay(cases, "retry", [choice, choice])
