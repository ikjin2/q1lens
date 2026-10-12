"""Verify original cursor-frequency sources through the public CLI, without hardware.

The mathematical oracle uses parameters and explicit synthetic IQ inputs. It does
not snapshot the interpreter's output as a golden result or change original files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
REPO = DEMO.parents[1]
SEQUENCERS = ("blue_peak", "red_cursor", "red_tracker", "orange_drive")
MODULES = {"blue_peak": "QCM0", "red_cursor": "QRM0", "red_tracker": "QRM0", "orange_drive": "QCM1"}
PROFILES = json.loads((HERE / "profiles.json").read_text(encoding="utf-8"))


class VerificationFailure(RuntimeError):
    pass


def require(condition, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def original_hashes() -> dict[str, str]:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(DEMO.iterdir()) if path.is_file()}


def parameters() -> dict:
    return json.loads((DEMO / "params.json").read_text(encoding="utf-8"))


def oracle(profile: dict, p: dict) -> dict:
    """Independent controller arithmetic and programmed RT timing equations."""
    delay = p["TRACKER_INIT_DELAY"]
    previous_center, previous_gain = p["TRACKER_INIT_CENTER"], p["TRACKER_INIT_GAIN"]
    shots = []
    for shot, trigger in enumerate(profile["trigger_times_ns"]):
        left, right = profile["iq_after_shift"][shot * 2:shot * 2 + 2]
        left_magnitude, right_magnitude = sum(map(abs, left)), sum(map(abs, right))
        slope = right_magnitude - left_magnitude
        hold = abs(slope) < p["SLOPE_DEADBAND"]
        next_delay = delay if hold else delay + (p["TRACK_STEP"] if slope >= 0 else -p["TRACK_STEP"])
        next_delay = min(p["TRACKER_MAX_DELAY"], max(p["TRACKER_MIN_DELAY"], next_delay))
        center, gain = next_delay + p["TRACKER_CENTER_OFFSET"], (left_magnitude + right_magnitude) // 2
        left_start = trigger + p["TRIGGER_WAIT"] + delay
        shots.append({
            "trigger_ns": trigger, "input_left": left, "input_right": right,
            "left_magnitude": left_magnitude, "right_magnitude": right_magnitude,
            "slope": slope, "negative_slope": slope < 0, "hold": hold,
            "delay_before": delay, "delay_after": next_delay, "center": center, "gain": gain,
            "acquisition_starts_ns": [left_start, left_start + p["ACQ_DUR"] + p["MEAS_DELTA"]],
            "feedback_send_start_ns": left_start + 2*p["ACQ_DUR"] + p["MEAS_DELTA"] + p["WAIT_FOR_IQ"],
            "cursor_start_ns": trigger + p["TRIGGER_WAIT"] + previous_center - p["CURSOR_CENTER_TO_START"],
            "cursor_gain": previous_gain * p["CURSOR_GAIN_NUMERATOR"] // 2**p["CURSOR_GAIN_SHIFT"],
            "orange_start_ns": trigger + p["TRIGGER_WAIT"] + p["SINE_START_WAIT"] + p["UPDATE_DUR"],
            "orange_gain": previous_gain * p["RF_GAIN_NUMERATOR"] // 2**p["RF_GAIN_SHIFT"],
            "orange_frequency_word": p["FREQ_BASE"] + (previous_center-p["TRACKER_MIN_CENTER"]) * 2**p["FREQ_CURSOR_SHIFT"],
            "blue_start_ns": trigger + p["TRIGGER_WAIT"] + p["TRIG_HIGH"] + p["TRIG_LOW"] + p["BLUE_PEAK_DELAY"],
        })
        delay, previous_center, previous_gain = next_delay, center, gain
    return {"shots": shots, "next_frequency_words": [p["FREQ_BASE"] + (s["center"]-p["TRACKER_MIN_CENTER"]) * 2**p["FREQ_CURSOR_SHIFT"] for s in shots]}


def check_dac_samples(seq: str, signals: dict, shots: list[dict], p: dict) -> list[dict]:
    """Check one exported DAC sample inside each output using closed-form waves."""
    trace = next(trace for trace in signals["traces"] if trace["stage"] == "dac" and trace["path"] == "i")
    checks = []
    for shot in shots:
        start = shot[{"blue_peak": "blue_start_ns", "red_cursor": "cursor_start_ns", "orange_drive": "orange_start_ns"}[seq]]
        duration = p[{"blue_peak": "PEAK_DUR", "red_cursor": "CURSOR_DUR", "orange_drive": "SINE_DUR"}[seq]]
        index = math.ceil((start + duration / 2 - trace["t0"]) / trace["dt_ns"])
        time = trace["t0"] + index * trace["dt_ns"]
        require(start <= time < start + duration, "Export grid cannot resolve selected pulse sample")
        if seq == "blue_peak":
            envelope = math.exp(-0.5*((time-start-(duration-1)/2)/max(duration*0.18, 1.0))**2)
            expected = envelope * p["PEAK_GAIN_INIT"] / 32768
        elif seq == "red_cursor":
            expected = shot["cursor_gain"] / 32768
        else:
            reset = shot["trigger_ns"] + p["TRIGGER_WAIT"] + p["SINE_START_WAIT"]
            phase = (time-reset) * shot["orange_frequency_word"] / 4 / 1e9
            expected = shot["orange_gain"] / 32768 * math.cos(math.tau * phase)
        expected = min(32767, max(-32768, round(expected * 32768))) / 32768
        require(abs(trace["samples"][index] - expected) <= 1/32768, f"{seq}: modeled DAC sample differs from waveform/gain/phase equation")
        checks.append({"time_ns": time, "expected_i": expected, "actual_i": trace["samples"][index], "tolerance": 1/32768})
    return checks


def prepare(profile_name: str, output: Path) -> tuple[Path, dict, dict]:
    profile, p = PROFILES[profile_name], parameters()
    require(len(profile["iq_after_shift"]) == 2 * len(profile["trigger_times_ns"]), "Each shot needs two IQ results")
    require(len(profile["trigger_times_ns"]) < p["PEAK_UPDATE_SHOTS"], "This profile assumes blue PRNG update is not reached")
    folder = output.resolve() / profile_name
    folder.mkdir(parents=True, exist_ok=True)
    memory = {
        "blue_peak": {"waveforms": {"blue_peak": {"index": 0, "data": [
            math.exp(-0.5*((i-(p["PEAK_DUR"]-1)/2)/max(p["PEAK_DUR"]*0.18, 1.0))**2) for i in range(p["PEAK_DUR"])
        ]}}},
        "red_cursor": {"waveforms": {"red_cursor": {"index": 0, "data": [1.0]*p["CURSOR_DUR"]}}},
        "orange_drive": {"waveforms": {"orange_envelope": {"index": 1, "data": [1.0]*p["SINE_DUR"]}}},
        "red_tracker": {"acquisitions": {"tracked_edge": {"index": p["EDGE_ACQ"], "num_bins": p["EDGE_ACQ_NUM_BINS"]}}},
    }
    for seq, sequence in memory.items():
        (folder / f"{seq}-memory.json").write_text(json.dumps(sequence) + "\n", encoding="utf-8")
    destinations = [("red_tracker", "IQ_FEEDBACK_CHANNEL"), ("red_cursor", "CURSOR_CHANNEL"),
                    ("orange_drive", "FREQ_CHANNEL"), ("red_cursor", "CURSOR_GAIN_CHANNEL"), ("orange_drive", "RF_GAIN_CHANNEL")]
    scenario = {
        "queue_capacity": 32, "startup_delay_ns": 200, "max_time_ns": 80000,
        "routes": [{"source": "red_tracker", "target": target, "id": p[channel], "latency_ns": p["LINQ_FEEDBACK_LATENCY"]} for target, channel in destinations],
        "inputs": {
            "triggers": [{"target": seq, "time_ns": time, "address": 1} for time in profile["trigger_times_ns"] for seq in SEQUENCERS],
            "acquisitions": [{"sequencer": "red_tracker", "acquisition_index": p["EDGE_ACQ"], "bin_index": index,
                "i": i * 2**p["IQ_SHIFT"], "q": q * 2**p["IQ_SHIFT"]} for index, (i, q) in enumerate(profile["iq_after_shift"])],
        },
        "physical": {"sample_period_ns": 4, "max_samples": 20000, "max_export_samples": 2000,
                     "sequencers": {"red_tracker": {"integration_length_ns": p["ACQ_DUR"]}}},
    }
    project = {"sequencers": [{"id": seq, "module": MODULES[seq], "name": seq,
        "file": str(DEMO / f"{seq}.q1asm"), "sequence_json": f"{seq}-memory.json"} for seq in SEQUENCERS],
        "params": {"file": str(DEMO / "params.json")}, "alignment": {"mode": "none"},
        "analysis": {"execution_mode": "simulation", "max_steps": 10000},
        "view": {"show_q1_issue": True, "show_queue": True}, "simulation": scenario}
    path = folder / "q1timeline.yml"
    path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
    expected = oracle(profile, p)
    (folder / "expected.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    return path, expected, p


def check(ir: dict, expected: dict, p: dict) -> dict:
    require({seq["id"]: seq.get("module") for seq in ir["sequencers"]} == MODULES, "Module/Sequencer hierarchy differs")
    errors = [item for item in ir["diagnostics"] if item["severity"] == "error"]
    require(not errors, f"Unexpected runtime errors: {errors}")
    require(set(ir["execution"]) == set(SEQUENCERS), "Missing sequencer execution")
    require(all(item["status"] == "blocked" and "No scheduled input can resume" in item.get("reason", "")
        for item in ir["execution"].values()), "Original infinite loops must end waiting for unsupplied next-shot input")
    events = ir["events"]
    by_id = {event["id"]: event for event in events}
    require(len(by_id) == len(events), "Duplicate event IDs")
    def select(seq, kind):
        return [event for event in events if event["sequencer_id"] == seq and event["kind"] == kind]
    def writes(seq, register):
        return [write for write in ir["register_history"] if write["sequencer_id"] == seq and write["register"] == register]
    def point(event):
        return {"event_id": event["id"], "time_ns": event["t0"]["value"], "source": event["source"]}
    for seq in SEQUENCERS:
        pending = [event for event in select(seq, "trigger_wait") if event["meta"].get("pending")]
        require(bool(pending), f"{seq}: missing pending third trigger wait")
        for event in select(seq, "rt_queue_enqueue") + select(seq, "rt_queue_dequeue"):
            require(0 <= event["meta"]["depth"] <= 32, "Queue capacity invariant failed")
    acquisitions = select("red_tracker", "acquisition_result")
    require(len(acquisitions) == 2 * len(expected["shots"]), "Wrong acquisition count")
    for index, event in enumerate(acquisitions):
        shot = expected["shots"][index // 2]
        pair = shot["input_left" if index % 2 == 0 else "input_right"]
        require(event["t0"]["value"] == shot["acquisition_starts_ns"][index % 2] + p["ACQ_DUR"], "Acquisition completion time differs")
        require(event["meta"]["bin_index"] == index and event["meta"]["source"]["kind"] == "scenario", "Acquisition provenance/bin differs")
        require([event["meta"][key] for key in ("i", "q")] == [value * 2**p["IQ_SHIFT"] for value in pair], "Raw synthetic IQ words differ")
    centers = writes("red_tracker", "R15")
    require([write["value"]["value"] for write in centers] == [shot["center"] for shot in expected["shots"]], "Tracked centers differ from independent control law")
    branches = [event for event in select("red_tracker", "q1_issue") if "jge $SLOPE," in event["source"]["raw"]]
    require([event["meta"]["branch_taken"] for event in branches] == [shot["negative_slope"] for shot in expected["shots"]], "Signed slope branch outcomes differ")
    holds = [event for event in select("red_tracker", "q1_issue") if any(text in event["source"]["raw"] for text in ("jlt $SLOPE,", "jlt $ABS_SLOPE,"))]
    require([event["meta"]["branch_taken"] for event in holds] == [shot["hold"] for shot in expected["shots"]], "Deadband branch outcomes differ")
    send_channels = [p[key] for key in ("CURSOR_CHANNEL", "FREQ_CHANNEL", "CURSOR_GAIN_CHANNEL", "RF_GAIN_CHANNEL")]
    for shot_index, shot in enumerate(expected["shots"]):
        for offset, channel in enumerate(send_channels):
            sends = [event for event in select("red_tracker", "feedback_send") if event["meta"]["id"] == channel]
            require(len(sends) == len(expected["shots"]), f"Wrong send count for channel {channel}")
            event = sends[shot_index]
            require(event["meta"]["value"] == shot["center" if offset < 2 else "gain"], "Feedback payload differs")
            require(event["t0"]["value"] == shot["feedback_send_start_ns"] + offset * p["FB_SEND_WAIT"], "Feedback send timing differs")
    for event in events:
        if event["kind"] == "feedback_receive":
            source = by_id[event["meta"]["source_event_id"]]
            require(source["kind"] == "feedback_send", "Feedback link does not refer to send")
            require(event["t0"]["value"] - source["t0"]["value"] == p["LINQ_FEEDBACK_LATENCY"], "Route latency differs")
    for write in ir["register_history"]:
        require(write["event_id"] in by_id, "Missing register writer event")
        source = by_id[write["event_id"]]
        require(source["kind"] == "q1_issue" and source["source"] == write["source"], "Register writer/source link differs")
        require(write["time"]["value"] <= max(ir["simulation"][seq]["end_time_ns"] for seq in SEQUENCERS), "Future register write leaked")
        provenance = write.get("meta", {}).get("provenance", {})
        if "input_event_id" in provenance:
            receive = by_id[provenance["input_event_id"]]
            require(receive["kind"] == "feedback_receive", "Register input provenance differs")
            require(write["value"]["value"] == receive["meta"]["value"] & 0xFFFFFFFF, "Register raw feedback bits differ")
        for value in provenance.get("inputs", {}).values():
            if value.get("event_id"):
                require(value["event_id"] in by_id, "Missing arithmetic input writer")
    plays, dac_checks = {}, {}
    for seq, start_key, gain_key in (("blue_peak", "blue_start_ns", None), ("red_cursor", "cursor_start_ns", "cursor_gain"), ("orange_drive", "orange_start_ns", "orange_gain")):
        played = [event for event in select(seq, "play") if event["meta"]["path"] == 0]
        require(len(played) == len(expected["shots"]), f"{seq}: output shot count differs")
        for event, shot in zip(played, expected["shots"]):
            require(event["t0"]["value"] == shot[start_key], f"{seq}: output start differs")
            applied = event["meta"]["applied_state"]
            require(applied["awg_gain"] == [shot[gain_key] if gain_key else p["PEAK_GAIN_INIT"], 0], f"{seq}: one-shot gain differs")
            for setter_id in event["meta"]["applied_setter_event_ids"].values():
                require(by_id[setter_id]["kind"] == "latched_state_pending", "Applied setter origin missing")
            if seq == "orange_drive":
                require(applied["frequency"] == shot["orange_frequency_word"], "One-shot frequency differs")
        plays[seq] = [point(event) for event in played]
        signals = ir["simulation"][seq]["signals"]
        require(not signals["truncated"], "Physical export unexpectedly truncated")
        require(any(trace["stage"] == "dac" and any(value for value in trace["samples"]) for trace in signals["traces"]), f"{seq}: missing modeled DAC output")
        dac_checks[seq] = check_dac_samples(seq, signals, expected["shots"], p)
    frequencies = writes("orange_drive", "R2")
    require([write["value"]["value"] for write in frequencies] == [expected["shots"][0]["orange_frequency_word"], *expected["next_frequency_words"]], "Frequency register history differs")
    return {"synthetic_inputs": True, "hardware_measurement": False, "parameter_overrides": {},
        "expected_end": "blocked: original continuous loops await an unsupplied third trigger/input",
        "event_count": len(events), "instruction_steps": {seq: item["steps"] for seq, item in ir["execution"].items()},
        "module_hierarchy": MODULES, "expected": expected, "dac_sample_checks": dac_checks, "checkpoints": {"plays": plays,
            "acquisitions": [point(event) for event in acquisitions], "slope_branches": [point(event) for event in branches],
            "center_writes": [{"event_id": write["event_id"], "time_ns": write["time"]["value"], "value": write["value"]["value"], "source": write["source"]} for write in centers],
            "frequency_writes": [{"event_id": write["event_id"], "time_ns": write["time"]["value"], "value": write["value"]["value"], "source": write["source"]} for write in frequencies]}}


def run_profile(profile_name: str, output: Path, *, repetitions: int = 2) -> dict:
    folder = output.resolve() / profile_name
    folder.mkdir(parents=True, exist_ok=True)
    evidence_path = folder / "evidence.json"
    progress = {"profile": profile_name, "passed": False, "status": "in_progress"}
    write_json(evidence_path, progress)
    try:
        before = original_hashes()
        destination = folder / "timeline_ir.json"
        destination.unlink(missing_ok=True)
        traces = []
        try:
            project, expected, p = prepare(profile_name, output)
            with tempfile.TemporaryDirectory(prefix="fresh-ir-", dir=project.parent) as temporary:
                for iteration in range(repetitions):
                    target = Path(temporary) / f"run-{iteration}.json"
                    command = [sys.executable, "-m", "q1lens", "q1timeline", "analyze", "--project", str(project),
                               "--out", str(target), "--format", "vscode-json", "--summary-only"]
                    environment = dict(os.environ)
                    environment["PYTHONPATH"] = str(REPO / "src") + os.pathsep + environment.get("PYTHONPATH", "")
                    result = subprocess.run(command, cwd=REPO, env=environment, text=True, capture_output=True, encoding="utf-8", timeout=60)
                    (project.parent / f"cli-{iteration}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
                    require(result.returncode == 0, f"CLI failed ({result.returncode}): {result.stdout}{result.stderr}")
                    ir = json.loads(target.read_text(encoding="utf-8"))
                    evidence = check(ir, expected, p)
                    traces.append(ir)
                require(all(trace == traces[0] for trace in traces), "Repeated fresh CLI traces differ")
            evidence.update(profile=profile_name, status="passed", repetitions=repetitions, original_sha256=before,
                            cli_project=str(project), ir_path=str(destination))
            write_json(destination, traces[-1])
        finally:
            require(original_hashes() == before, "Verification modified an original demo file")
        write_json(evidence_path, evidence)
        return evidence
    except BaseException as error:
        progress.update(status="interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "failed",
                        reason=f"{type(error).__name__}: {error}")
        write_json(evidence_path, progress)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=tuple(PROFILES), action="append")
    parser.add_argument("--out-dir", type=Path, default=HERE / "generated")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    report = [{"profile": name, "passed": False, "status": "queued"} for name in args.profile or PROFILES]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.out_dir / "report.json"
    write_json(report_path, report)
    for row in report:
        folder = args.out_dir / row["profile"]
        folder.mkdir(parents=True, exist_ok=True)
        write_json(folder / "evidence.json", row)
    for row in report:
        name = row["profile"]
        row["status"] = "in_progress"
        write_json(report_path, report)
        try:
            if args.prepare_only:
                print(prepare(name, args.out_dir)[0])
                row["status"] = "prepared"
                continue
            evidence = run_profile(name, args.out_dir)
            row.update(passed=True, status="passed", ir_path=evidence["ir_path"])
            print(f"PASS {name}: {evidence['event_count']} events; original sources preserved; expected bounded-input wait")
        except (VerificationFailure, OSError, ValueError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired) as error:
            row.update(status="failed", reason=str(error))
            print(f"FAIL {name}: {error}")
        except BaseException as error:
            row.update(status="interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "failed",
                       reason=f"{type(error).__name__}: {error}")
            raise
        finally:
            if not row["passed"]:
                write_json(args.out_dir / name / "evidence.json", row)
            write_json(report_path, report)
    succeeded = all(row["status"] == "prepared" for row in report) if args.prepare_only else all(row["passed"] for row in report)
    return 0 if report and succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
