"""Run the same examples opened by VS Code and check their observable results."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CASES = ("01-digital", "02-communication", "03-signals", "04-underflow", "05-supplied-input")


def register(ir: dict, seq: str, name: str) -> int:
    return [write["value"]["value"] for write in ir["register_history"]
            if write["sequencer_id"] == seq and write["register"] == name][-1]


def events(ir: dict, kind: str) -> list[dict]:
    return [event for event in ir["events"] if event["kind"] == kind]


def check(case: str, ir: dict) -> dict:
    assert isinstance(ir, dict) and ir.get("execution"), "Missing execution results"
    if case == "04-underflow":
        assert ir["execution"]["starved"]["status"] == "error"
        assert ir["execution"]["starved"]["reason"] == "RT queue underflow at 24 ns."
        underflow, = events(ir, "underflow_warning")
        assert underflow["sequencer_id"] == "starved" and underflow["t0"]["value"] == 24
        assert underflow["meta"]["depth"] == 0 and underflow["meta"]["simulated"] is True
        errors = [item for item in ir["diagnostics"] if item["severity"] == "error"]
        assert len(errors) == 1 and errors[0]["category"] == "rt_queue_underflow"
        assert Path(errors[0]["source"]["file"]).name == "starved.q1asm"
        assert events(ir, "q1_stall"), "Expected the partial feedback-wait trace"
        return {"expected_failure": "RT queue underflow at 24 ns"}
    assert all(value["status"] == "completed" for value in ir["execution"].values())
    if case == "01-digital":
        assert register(ir, "counter", "R1") == register(ir, "counter", "R2") == 3
        assert [write["value"]["value"] for write in ir["register_history"] if write["register"] == "R1"] == [0, 1, 2, 3]
        assert [write["time"]["value"] for write in ir["register_history"] if write["register"] == "R1"] == [12, 32, 80, 244]
        assert any(event["meta"]["reason"] == "queue_full" for event in events(ir, "q1_stall"))
        assert max(event["meta"]["depth"] for event in events(ir, "rt_queue_enqueue")) == 2
        return {"R1": 3, "R2": 3, "max_queue_depth": 2, "queue_full_stalls": len(events(ir, "q1_stall"))}
    if case == "02-communication":
        assert register(ir, "receiver", "R0") == 7
        assert register(ir, "receiver", "R1") == 8
        assert events(ir, "feedback_receive")[0]["t0"]["value"] == 228
        assert events(ir, "trigger_receive")[0]["t0"]["value"] == 1100
        assert ir["feedback_flows"]
        return {"R0": 7, "R1": 8, "feedback_arrival_ns": 228, "trigger_arrival_ns": 1100}
    result = ir["simulation"]["readout"]["acquisitions"][0]
    if case == "03-signals":
        assert result["i"] == 31.953125 and result["mean_i"] == 0.499267578125
        assert result["q"] == 0 and result["thresholded"] == 1
        assert register(ir, "controller", "R0") == 3
        assert register(ir, "controller", "R1") == 1
        assert result["source"]["kind"] == "loopback"
        stages = {trace["stage"] for trace in ir["simulation"]["drive"]["signals"]["traces"]}
        assert {"waveform", "nco", "filtered", "dac", "rf"} <= stages
        traces = {trace["stage"]: trace for trace in ir["simulation"]["drive"]["signals"]["traces"] if trace["path"] == "i"}
        for stage, expected in {"waveform": 1, "gain_offset": 0.5, "fir": 0.25, "filtered": 0.125, "dac": 0.125}.items():
            trace = traces[stage]
            assert trace["t0"] == 0 and trace["dt_ns"] == 1
            assert trace["samples"][204] == expected, f"Unexpected {stage} startup sample"
        assert traces["filtered"]["samples"][204:209] == [0.125, 0.3125, 0.40625, 0.453125, 0.4765625]
        return {"mean_i": result["mean_i"], "integral_i": result["i"], "thresholded": 1, "feedback_register_R0": 3}
    assert case == "05-supplied-input"
    assert (result["i"], result["q"]) == (1.0, -2.0)
    assert (result["mean_i"], result["mean_q"]) == (0.125, -0.25)
    assert result["source"]["kind"] == "supplied_trace"
    return {"integral_i": 1.0, "integral_q": -2.0, "mean_i": 0.125, "mean_q": -0.25}


def main() -> int:
    if not __debug__:
        raise RuntimeError("Verification requires Python assertions; remove -O and PYTHONOPTIMIZE.")
    report = [{"case": case, "passed": False, "status": "queued"} for case in CASES]
    destination = ROOT / ".verification" / "report.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for index, case in enumerate(CASES):
        project = ROOT / case / "q1timeline.yml"
        try:
            assert project.is_file(), f"Required example missing: {project}"
            # Never accept a previous preview's IR after a failed analyzer run.
            with tempfile.TemporaryDirectory(prefix=f"{case}-", dir=destination.parent) as temporary:
                traces = []
                for repetition in range(2):
                    output = Path(temporary) / f"run-{repetition}.json"
                    command = [sys.executable, "-m", "q1lens", "q1timeline", "analyze",
                               "--project", str(project), "--out", str(output), "--format", "vscode-json", "--summary-only"]
                    run = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", timeout=60)
                    assert run.returncode == (2 if case == "04-underflow" else 0), run.stdout + run.stderr
                    ir = json.loads(output.read_text(encoding="utf-8"))
                    evidence = check(case, ir)
                    traces.append(ir)
                assert traces[0] == traces[1], "Repeated execution produced a different trace"
            # Retain the verified trace at the path used by the manual guide.
            preview = project.parent / ".q1timeline" / "timeline_ir.json"
            preview.parent.mkdir(exist_ok=True)
            preview.write_text(json.dumps(ir, indent=2) + "\n", encoding="utf-8")
            report[index] = {"case": case, "passed": True, "repeatable": True, "evidence": evidence}
            print(f"PASS {case}: {json.dumps(evidence)}")
        except (AssertionError, KeyError, IndexError, OSError, ValueError, TypeError, subprocess.TimeoutExpired) as error:
            report[index] = {"case": case, "passed": False, "reason": str(error) or "Expected value did not match"}
            print(f"FAIL {case}: {report[index]['reason']}")
        except BaseException as error:
            report[index].update(status="interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "failed",
                                 reason=f"{type(error).__name__}: {error}")
            raise
        finally:
            destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {destination}")
    return 0 if report and all(item["passed"] for item in report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
