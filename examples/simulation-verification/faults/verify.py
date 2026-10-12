"""Reproduce fault scenarios with the public CLI and check independent expectations.

These checks describe q1lens's event-driven model, not measured Qblox behavior.
All programs and scenario inputs are the adjacent, reviewable YAML/Q1ASM files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
CASES = (
    "01-feedback-missing", "02-feedback-delayed", "03-feedback-duplicate",
    "04-trigger-missing", "05-queue-full", "06-feedback-too-late", "07-feedback-overflow",
)
UNDERFLOWS = {"01-feedback-missing", "06-feedback-too-late"}
FAILURES = UNDERFLOWS | {"07-feedback-overflow"}


def equal(actual, expected, label: str) -> None:
    # Explicit checks remain active under python -O.
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def events(ir: dict, kind: str) -> list[dict]:
    return [event for event in ir["events"] if event["kind"] == kind]


def times(items: list[dict], edge: str = "t0") -> list[int]:
    return [item[edge]["value"] for item in items]


def check(case: str, ir: dict) -> dict:
    if case not in CASES:
        raise ValueError(f"Unknown fault scenario: {case}")
    execution = ir["execution"]["seq"]
    status = "error" if case in FAILURES else "blocked" if case == "04-trigger-missing" else "completed"
    equal(execution["mode"], "simulation", "execution mode")
    equal(execution["timing_model"], "event_driven", "timing model")
    equal(execution["status"], status, "execution status")
    equal(ir["simulation"]["seq"]["startup_delay_ns"], 200, "scenario startup")
    writes = ir["register_history"]
    receives = events(ir, "feedback_receive")
    stalls = events(ir, "q1_stall")
    underflows = events(ir, "underflow_warning")
    pending = [event for event in ir["events"] if event.get("meta", {}).get("pending")]
    errors = [item for item in ir["diagnostics"] if item["severity"] == "error"]
    if case in UNDERFLOWS:
        equal(execution["reason"], "RT queue underflow at 1200 ns.", "failure reason")
        equal(times(underflows), [1200], "RT deadline failure")
        equal(underflows[0]["meta"]["depth"], 0, "empty RT queue")
        equal(underflows[0]["meta"]["simulated"], True, "model failure provenance")
        equal([(event["kind"], event["meta"].get("reason"), event["t0"]["value"], event["t1"]["value"]) for event in pending],
              [("q1_stall", "feedback_input", 4, 1200)], "unresolved feedback wait")
        equal(receives, [], "no invented or post-failure input")
        equal(writes, [], "no invented register write")
        equal([item["category"] for item in errors], ["rt_queue_underflow"], "error diagnostic")
        return {"status": status, "underflow_ns": 1200, "pending_feedback_since_ns": 4,
                "configured_feedback_arrival_ns": 1600 if case == "06-feedback-too-late" else None}
    equal(underflows, [], "no underflow")
    if case == "07-feedback-overflow":
        equal(execution["reason"], "Feedback queue overflow at 400 ns.", "overflow reason")
        equal(ir["simulation"]["seq"]["feedback_queue_capacity"], 2, "feedback capacity")
        equal(ir["simulation"]["seq"]["end_time_ns"], 400, "overflow termination")
        equal([item["category"] for item in errors], ["feedback_queue_overflow"], "error diagnostic")
        equal([(event["meta"]["input_index"], event["meta"]["value"], event["meta"]["depth"]) for event in receives],
              [(0, 11, 1), (1, 22, 2)], "accepted FIFO entries and input provenance")
        equal(times(receives), [400, 400], "accepted arrival times")
        equal(len(set(event["id"] for event in receives)), 2, "accepted inputs have distinct event IDs")
        equal(writes, [], "no Q1 consumption after input-phase overflow")
        equal(events(ir, "stop"), [], "no invented completion after overflow")
        return {"status": status, "overflow_ns": 400, "accepted_values": [11, 22], "accepted_input_indices": [0, 1], "feedback_capacity": 2}
    equal(errors, [], "no error diagnostics")
    if case == "04-trigger-missing":
        equal(execution["reason"], "No scheduled input can resume trigger_wait.", "blocked reason")
        equal([(event["kind"], event["t0"]["value"], event["t1"]["value"]) for event in pending],
              [("trigger_wait", 200, 200)], "unresolved RT trigger wait")
        equal(events(ir, "trigger_receive"), [], "no invented trigger")
        equal(events(ir, "stop"), [], "RT cannot finish before its trigger")
        equal(writes, [], "no invented register write")
        equal([item["category"] for item in ir["diagnostics"]], ["analysis_incomplete"], "blocked diagnostic")
        return {"status": status, "pending_trigger_since_ns": 200, "cli_exit": 0}
    equal(pending, [], "completed execution cannot retain a pending wait")
    if case == "05-queue-full":
        enqueued, dequeued = events(ir, "rt_queue_enqueue"), events(ir, "rt_queue_dequeue")
        packet_ids = [f"seq:p{number}" for number in range(6)]
        equal([event["meta"]["rt_packet_id"] for event in enqueued], packet_ids, "all six packets enqueued once")
        equal([event["meta"]["rt_packet_id"] for event in dequeued], packet_ids, "FIFO drain without loss or duplicates")
        equal(times(enqueued), [4, 8, 204, 244, 284, 324], "enqueue schedule under backpressure")
        equal(times(dequeued), [200, 240, 280, 320, 360, 400], "RT packet cadence")
        equal([event["meta"]["depth"] for event in enqueued], [1, 2, 2, 2, 2, 2], "bounded enqueue depth")
        equal([event["meta"]["depth"] for event in dequeued], [1, 1, 1, 1, 1, 0], "drain depth")
        equal([(event["t0"]["value"], event["t1"]["value"], event["meta"]["reason"]) for event in stalls],
              [(8, 200, "queue_full"), (204, 240, "queue_full"), (244, 280, "queue_full"), (284, 320, "queue_full")], "Q1 backpressure intervals")
        equal(times(events(ir, "wait")), [200, 240, 280, 320, 360, 400], "all six RT commands executed")
        equal(times(events(ir, "stop")), [440], "RT drain completion")
        equal(ir["simulation"]["seq"]["queue_capacity"], 2, "configured capacity")
        return {"status": status, "packets_enqueued_and_executed": 6, "max_queue_depth": 2, "q1_stalls": 4, "rt_end_ns": 440}
    values = [7, 7, 9] if case == "03-feedback-duplicate" else [7]
    equal([event["meta"]["value"] for event in receives], values, "input FIFO values")
    equal([event["meta"]["input_index"] for event in receives], list(range(len(values))), "distinct input occurrences")
    equal(times(receives), [400] * len(values), "input arrival times")
    equal([(event["t0"]["value"], event["t1"]["value"], event["meta"]["reason"]) for event in stalls], [(4, 400, "feedback_input")], "wait then resume")
    equal([(write["register"], write["time"]["value"], write["value"]["value"]) for write in writes],
          [(f"R{index}", 408 + 8 * index, value) for index, value in enumerate(values)], "FIFO register values and readiness")
    equal([write["meta"]["provenance"]["input_event_id"] for write in writes], [event["id"] for event in receives], "writer-to-input origins")
    equal(len(set(event["id"] for event in receives)), len(values), "duplicate values retain distinct event IDs")
    equal(times(events(ir, "stop")), [1200], "RT completion after its finite wait")
    return {"status": status, "feedback_arrival_ns": 400, "register_values": values,
            "readable_ns": [408 + 8 * index for index in range(len(values))], "input_indices": list(range(len(values)))}


def run_case(case: str, directory: Path) -> dict:
    project = ROOT / case / "q1timeline.yml"
    if not project.is_file():
        raise FileNotFoundError(f"Required scenario missing: {project}")
    directory.mkdir(parents=True)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO / "src") + os.pathsep + environment.get("PYTHONPATH", "")
    traces, runs = [], []
    for repetition in (1, 2):
        output = directory / f"run-{repetition}.json"
        command = [sys.executable, "-m", "q1lens", "q1timeline", "analyze", "--project", str(project),
                   "--out", str(output), "--format", "vscode-json", "--summary-only"]
        run = subprocess.run(command, cwd=REPO, env=environment, text=True, capture_output=True, encoding="utf-8", timeout=60)
        (directory / f"run-{repetition}.log").write_text(run.stdout + "\n" + run.stderr, encoding="utf-8")
        runs.append({"command": command, "cwd": str(REPO), "core_source_path": str(REPO / "src"), "exit_code": run.returncode, "trace": str(output)})
        (directory / "commands.json").write_text(json.dumps(runs, indent=2) + "\n", encoding="utf-8")
        equal(run.returncode, 2 if case in FAILURES else 0, "CLI exit code")
        ir = json.loads(output.read_text(encoding="utf-8"))
        evidence = check(case, ir)
        traces.append(ir)
    equal(traces[0], traces[1], "repeatable trace")
    return {"case": case, "passed": True, "repeatable": True, "project": str(project), "runs": runs, "evidence": evidence}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ROOT.parents[2] / "output" / "extended-simulation-validation" / "faults")
    parser.add_argument("--case", action="append", choices=CASES, help="Run only these cases (default: all seven).")
    args = parser.parse_args(argv)
    destination = args.out_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "model": "q1lens event-driven simulation; not a hardware measurement", "cases": [], "source_sha256": {},
              "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "core_source_path": str(REPO / "src")}
    report_path = destination / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    # Fresh retained paths make a previous successful IR impossible to reuse.
    campaign = Path(tempfile.mkdtemp(prefix="run-", dir=destination))
    report["run_directory"] = str(campaign)
    for case in args.case or CASES:
        try:
            sources = [ROOT / case / name for name in ("q1timeline.yml", "program.q1asm")]
            before = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
            report["source_sha256"].update(before)
            result = run_case(case, campaign / case)
            equal({str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}, before, "scenario source preservation")
        except (AssertionError, KeyError, IndexError, OSError, ValueError, TypeError, subprocess.TimeoutExpired) as error:
            result = {"case": case, "passed": False, "reason": str(error)}
        report["cases"].append(result)
        print(f"{'PASS' if result['passed'] else 'FAIL'} {case}: {json.dumps(result.get('evidence', result.get('reason')))}")
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["passed"] = bool(report["cases"]) and all(item["passed"] for item in report["cases"])
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {report_path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
