"""Independent digital boundary checks for the unchanged cursor demo programs.

Long profiles isolate the sequencer whose boundary is exercised. Small profiles
run all four original sequencers. Inputs are synthetic; physical sampling is off.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

import yaml

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cursor_base_verification", HERE / "verify.py")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
require = base.require
PROFILES = ("prng-1001", "bin-wrap-1024", "bin-wrap-preview", "deadband-boundaries", "delay-min", "delay-max")
LONG_PROFILES = {"prng-1001", "bin-wrap-1024"}


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2)+"\n", encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def xorshift_stages(seed: int) -> list[int]:
    """Unsigned xorshift32 equations, independent of Q1ASM execution helpers."""
    a = (seed ^ (seed << 13)) & 0xFFFFFFFF
    b = a ^ (a >> 17)
    c = (b ^ (b << 5)) & 0xFFFFFFFF
    return [a, b, c]


def prng_expectation(p: dict) -> dict:
    position_stages = xorshift_stages(p["PRNG_SEED"])
    gain_stages = xorshift_stages(p["PEAK_GAIN_PRNG_SEED"])
    step = position_stages[-1] & p["PEAK_STEP_MASK"]
    backwards = bool((position_stages[-1] >> 5) & 1)
    offset = -step if backwards else step
    if offset < 0:
        offset = -offset
    elif offset >= p["PEAK_OFFSET_SPAN"]:
        offset = p["PEAK_OFFSET_MAX"] - (offset - p["PEAK_OFFSET_SPAN"])
    gain_step = gain_stages[-1] & p["PEAK_GAIN_STEP_MASK"]
    gain_down = bool((gain_stages[-1] >> 5) & 1)
    gain = p["PEAK_GAIN_INIT"] + (-gain_step if gain_down else gain_step)
    gain = max(p["PEAK_GAIN_MIN"], min(p["PEAK_GAIN_MAX"], gain))
    return {"position_seed_stages": position_stages, "gain_seed_stages": gain_stages,
            "position_step": step, "position_backwards": backwards, "offset": offset,
            "gain_step": gain_step, "gain_down": gain_down, "gain": gain,
            "update_after_shot": p["PEAK_UPDATE_SHOTS"], "first_changed_output_shot": p["PEAK_UPDATE_SHOTS"] + 1}


def specification(name: str) -> tuple[dict, dict]:
    require(name in PROFILES, f"Unknown boundary profile {name}")
    p = base.parameters()
    overrides, sequencers = {}, list(base.SEQUENCERS)
    slopes, shots = [0] * 3, 3
    if name == "prng-1001":
        require(p["PEAK_UPDATE_SHOTS"] == 1000, "prng-1001 requires the original 1000-shot update interval")
        sequencers, shots = ["blue_peak"], 1001
    elif name == "bin-wrap-1024":
        overrides["EDGE_ACQ_NUM_BINS"] = 1024
        sequencers, shots = ["red_tracker"], 513
    elif name == "bin-wrap-preview":
        overrides["EDGE_ACQ_NUM_BINS"] = 4
    elif name == "deadband-boundaries":
        boundary = p["SLOPE_DEADBAND"]
        slopes = [-boundary-1, -boundary, -boundary+1, 0, boundary-1, boundary, boundary+1]
        shots = len(slopes)
    elif name in {"delay-min", "delay-max"}:
        lower = name == "delay-min"
        initial = p["TRACKER_MIN_DELAY"] + p["TRACK_STEP"] if lower else p["TRACKER_MAX_DELAY"] - p["TRACK_STEP"]
        overrides.update(TRACKER_INIT_DELAY=initial, TRACKER_INIT_CENTER=initial+p["TRACKER_CENTER_OFFSET"])
        slopes = [-p["SLOPE_DEADBAND"]] * 2 + [p["SLOPE_DEADBAND"]] if lower else [p["SLOPE_DEADBAND"]] * 2 + [-p["SLOPE_DEADBAND"]]
    p.update(overrides)
    pairs = []
    for shot in range(shots):
        left = 7000 if name == "bin-wrap-preview" and shot == 2 else 6000
        slope = slopes[shot] if shot < len(slopes) else 0
        pairs.extend([[left, -1000], [left+slope, -1000]])
    profile = {"name": name, "sequencers": sequencers, "shots": shots, "parameter_overrides": overrides,
               "trigger_times_ns": [1000+39000*shot for shot in range(shots)], "iq_after_shift": pairs}
    return profile, p


def prepare(name: str, output: Path) -> tuple[Path, dict, dict]:
    profile, p = specification(name)
    folder = output.resolve() / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "params.json").write_text(json.dumps(p, indent=2)+"\n", encoding="utf-8")
    selected = profile["sequencers"]
    destinations = [("red_tracker", "IQ_FEEDBACK_CHANNEL"), ("red_cursor", "CURSOR_CHANNEL"),
                    ("orange_drive", "FREQ_CHANNEL"), ("red_cursor", "CURSOR_GAIN_CHANNEL"), ("orange_drive", "RF_GAIN_CHANNEL")]
    max_steps = max(10000, profile["shots"]*180+2000)
    scenario = {"queue_capacity": 32, "startup_delay_ns": 200,
                "max_time_ns": profile["trigger_times_ns"][-1]+80000,
                "routes": [{"source": "red_tracker", "target": target, "id": p[channel], "latency_ns": p["LINQ_FEEDBACK_LATENCY"]}
                           for target, channel in destinations if "red_tracker" in selected and target in selected],
                "inputs": {"triggers": [{"target": seq, "time_ns": t, "address": 1} for t in profile["trigger_times_ns"] for seq in selected],
                           "acquisitions": [{"sequencer": "red_tracker", "acquisition_index": p["EDGE_ACQ"], "bin_index": index % p["EDGE_ACQ_NUM_BINS"],
                                             "i": i*2**p["IQ_SHIFT"], "q": q*2**p["IQ_SHIFT"]}
                                            for index, (i, q) in enumerate(profile["iq_after_shift"]) if "red_tracker" in selected]}}
    project = {"sequencers": [{"id": seq, "name": seq, "module": base.MODULES[seq], "file": str(base.DEMO / f"{seq}.q1asm")} for seq in selected],
               "params": {"file": "params.json"}, "alignment": {"mode": "none"},
               "analysis": {"execution_mode": "simulation", "max_steps": max_steps},
               "view": {"show_q1_issue": True, "show_queue": True}, "simulation": scenario}
    path = folder / "q1timeline.yml"
    path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
    expected = {"profile": profile, "scope": "digital execution only; physical sampling disabled; synthetic IQ scenario",
                "prng": prng_expectation(p) if name == "prng-1001" else None,
                "controller": base.oracle(profile, p) if "red_tracker" in selected else None}
    (folder / "expected.json").write_text(json.dumps(expected, indent=2)+"\n", encoding="utf-8")
    return path, expected, p


def check(ir: dict, expected: dict, p: dict) -> dict:
    profile = expected["profile"]
    name, selected, shots = profile["name"], profile["sequencers"], profile["shots"]
    require({seq["id"]: seq["module"] for seq in ir["sequencers"]} == {seq: base.MODULES[seq] for seq in selected}, "Module/Sequencer selection differs")
    require(not [d for d in ir["diagnostics"] if d["severity"] == "error"], "Unexpected runtime error")
    require(set(ir["execution"]) == set(selected), "Missing sequencer execution")
    for seq in selected:
        result = ir["execution"][seq]
        require(result["status"] == "blocked" and "No scheduled input can resume" in result.get("reason", ""), f"{seq}: unexpected terminal status {result}")
        require(not ir["simulation"][seq]["physical_enabled"], "Long/boundary evidence must not claim physical samples")
    groups, history = defaultdict(list), defaultdict(list)
    by_id = {}
    for event in ir["events"]:
        require(event["id"] not in by_id, "Duplicate event ID")
        by_id[event["id"]] = event
        groups[event["sequencer_id"], event["kind"]].append(event)
        if event["kind"] in {"rt_queue_enqueue", "rt_queue_dequeue"}:
            require(0 <= event["meta"]["depth"] <= 32, "Queue depth exceeded capacity")
    for write in ir["register_history"]:
        history[write["sequencer_id"], write["register"]].append(write)
        require(write["event_id"] in by_id, "Register writer is missing")
    aliases = {seq: dict(re.findall(r"(?m)^\s*\.DEF\s+(\w+)\s+(R\d+)\s*$",
                                   (base.DEMO / f"{seq}.q1asm").read_text(encoding="utf-8"))) for seq in selected}
    for seq in selected:
        for play in groups[seq, "play"]:
            applied = play["meta"]["applied_state"]
            for field, op in (("awg_gain", "set_awg_gain"), ("frequency", "set_freq")):
                if field not in applied:
                    continue
                pending = by_id.get(play["meta"].get("applied_setter_event_ids", {}).get(field))
                require(pending is not None and pending["kind"] == "latched_state_pending", "Applied setter event is missing")
                require(pending["sequencer_id"] == seq and pending["meta"]["field"] == field, "Applied setter field/sequencer differs")
                require(pending["meta"]["value"] == applied[field], "Applied setter value differs")
                issue = by_id.get(pending["meta"].get("q1_issue_event_id"))
                require(issue is not None and issue["kind"] == "q1_issue", "Applied setter Q1 writer is missing")
                require(issue["sequencer_id"] == seq and issue["meta"]["op"] == op and issue["source"] == pending["source"], "Applied setter Q1 writer op/source/sequencer differs")
                require(issue["t1"]["value"] == pending["t0"]["value"] <= play["t0"]["value"], "Applied setter writer time differs")
                operands = [token.strip().lstrip("$") for token in issue["source"]["raw"].strip().split(None, 1)[1].split(",")]
                registers = [aliases[seq].get(token, token) for token in operands]
                reads = issue["meta"]["register_reads"]
                require(all(reg in reads and reads[reg]["kind"] == "concrete" for reg in registers), "Applied setter operand snapshot is missing")
                decoded = [reads[reg]["value"] - (2**32 if reads[reg]["value"] >= 2**31 else 0) for reg in registers]
                require(decoded == (applied[field] if field == "awg_gain" else [applied[field]]), "Applied setter operand value differs")
    def values(seq, register):
        return [w["value"]["value"] for w in history[seq, register]]
    def point(event):
        return {"event_id": event["id"], "time_ns": event["t0"]["value"], "source": event["source"], "meta": event["meta"]}
    def branch_events(seq, raw):
        return [e for e in groups[seq, "q1_issue"] if raw in e["source"]["raw"]]
    def branch_values(seq, raw):
        return [e["meta"]["branch_taken"] for e in branch_events(seq, raw)]
    checkpoints = {}
    for seq in selected:
        require(any(e["meta"].get("pending") for e in groups[seq, "trigger_wait"]), f"{seq}: no final pending trigger")
        require(len(groups[seq, "trigger_receive"]) == shots, f"{seq}: supplied trigger count differs")
    if name == "prng-1001":
        want = expected["prng"]
        require(values("blue_peak", "R5") == [p["PRNG_SEED"], *want["position_seed_stages"]], "Position PRNG xorshift32 stages differ")
        require(values("blue_peak", "R10") == [p["PEAK_GAIN_PRNG_SEED"], *want["gain_seed_stages"]], "Gain PRNG xorshift32 stages differ")
        require(values("blue_peak", "R1") == [0, want["offset"]], "PRNG reflected position differs")
        require(values("blue_peak", "R11") == [p["PEAK_GAIN_INIT"], want["gain"]], "PRNG gain differs")
        branches = branch_events("blue_peak", "jge $SHOT_COUNT,")
        taken = [e["meta"]["branch_taken"] for e in branches]
        require(len(taken) >= 1000 and taken[:1000] == [False]*999+[True] and sum(taken) == 1, "1000-shot PRNG branch boundary differs")
        played = [e for e in groups["blue_peak", "play"] if e["meta"]["path"] == 0]
        require(len(played) == 1001, "Must execute all 1001 original output shots")
        for index, event in enumerate(played):
            changed = index >= 1000
            start = profile["trigger_times_ns"][index] + p["TRIGGER_WAIT"] + p["TRIG_HIGH"] + p["TRIG_LOW"] + p["BLUE_PEAK_DELAY"] + (want["offset"] if changed else 0)
            require(event["t0"]["value"] == start, f"Blue shot {index+1} PRNG position application differs")
            require(event["meta"]["applied_state"]["awg_gain"] == [want["gain"] if changed else p["PEAK_GAIN_INIT"], 0], f"Blue shot {index+1} PRNG gain application differs")
        checkpoints.update(update_branch=point(branches[999]), outputs=[point(e) for e in played[-3:]],
                           prng_writes=[w for w in ir["register_history"] if w["register"] in {"R1", "R5", "R10", "R11"}])
    if "red_tracker" in selected:
        wants = expected["controller"]["shots"]
        acquisitions = groups["red_tracker", "acquisition_result"]
        require(len(acquisitions) == shots*2, "Acquisition count differs")
        for index, event in enumerate(acquisitions):
            want = wants[index//2]
            require(event["meta"]["bin_index"] == index % p["EDGE_ACQ_NUM_BINS"], "Acquisition bin ring sequence differs")
            require(event["meta"]["input_index"] == index, "Acquisition scenario input was reused or skipped")
            require(event["t0"]["value"] == want["acquisition_starts_ns"][index%2]+p["ACQ_DUR"], "Acquisition completion time differs")
            pair = profile["iq_after_shift"][index]
            require([event["meta"][k] for k in ("i", "q")] == [x*2**p["IQ_SHIFT"] for x in pair], "Scenario IQ result differs")
            require(event["meta"]["source"]["kind"] == "scenario", "Acquisition source is not synthetic scenario")
        require(values("red_tracker", "R15") == [s["center"] for s in wants], "Tracked center differs from clamp/deadband equation")
        require(branch_values("red_tracker", "jge $SLOPE,") == [s["negative_slope"] for s in wants], "Slope sign branch differs")
        holds = [e for e in groups["red_tracker", "q1_issue"] if any(text in e["source"]["raw"] for text in ("jlt $SLOPE,", "jlt $ABS_SLOPE,"))]
        require([e["meta"]["branch_taken"] for e in holds] == [s["hold"] for s in wants], "Deadband strict boundary differs")
        candidate = [s["delay_before"] + (0 if s["hold"] else p["TRACK_STEP"] * (-1 if s["negative_slope"] else 1)) for s in wants]
        min_taken = [c < p["TRACKER_MIN_DELAY"] for c in candidate]
        require(branch_values("red_tracker", "jlt $MEAS_DELAY,") == min_taken, "Minimum clamp branch boundary differs")
        require(branch_values("red_tracker", "jge $MEAS_DELAY,") == [c >= p["TRACKER_MAX_DELAY"] for c, taken in zip(candidate, min_taken) if not taken], "Maximum clamp branch boundary differs")
        require(branch_values("red_tracker", "jge $RIGHT_BIN,") == [(2*(index+1)) % p["EDGE_ACQ_NUM_BINS"] == 0 for index in range(shots)], "Bin-wrap branch boundary differs")
        checkpoints.update(acquisitions=[point(e) for e in acquisitions[-6:]], centers=history["red_tracker", "R15"][-7:],
                           wrap_branches=[point(e) for e in branch_events("red_tracker", "jge $RIGHT_BIN,") if e["meta"]["branch_taken"]],
                           deadband_branches=[point(e) for e in holds] if name == "deadband-boundaries" else [])
        for seq, start_key, gain_key in (("red_cursor", "cursor_start_ns", "cursor_gain"), ("orange_drive", "orange_start_ns", "orange_gain")):
            if seq not in selected:
                continue
            played = [e for e in groups[seq, "play"] if e["meta"]["path"] == 0]
            require(len(played) == shots, f"{seq}: output count differs")
            for event, want in zip(played, wants):
                require(event["t0"]["value"] == want[start_key], f"{seq}: next-shot position differs")
                require(event["meta"]["applied_state"]["awg_gain"] == [want[gain_key], 0], f"{seq}: next-shot gain differs")
                if seq == "orange_drive":
                    require(event["meta"]["applied_state"]["frequency"] == want["orange_frequency_word"], "Next-shot frequency differs")
            checkpoints[seq+"_outputs"] = [point(e) for e in played]
    return {"profile": name, "passed": True, "scope": expected["scope"], "selected_sequencers": selected,
            "shots": shots, "parameter_overrides": profile["parameter_overrides"], "original_parameter_bin_count": base.parameters()["EDGE_ACQ_NUM_BINS"],
            "event_count": len(ir["events"]), "steps": {seq: data["steps"] for seq, data in ir["execution"].items()},
            "expected_end": "blocked at unsupplied next trigger; Q1 may precompute later iterations", "checkpoints": checkpoints}


def run_profile(name: str, output: Path) -> dict:
    folder = output.resolve() / name
    folder.mkdir(parents=True, exist_ok=True)
    evidence_path = folder / "evidence.json"
    progress = {"profile": name, "passed": False, "status": "in_progress", "started_utc": utc_now()}
    write_json(evidence_path, progress)
    started = time.perf_counter()
    try:
        before = base.original_hashes()
        destination = folder / "timeline_ir.json"
        destination.unlink(missing_ok=True)
        project, expected, p = prepare(name, output)
        with tempfile.TemporaryDirectory(prefix="fresh-ir-", dir=project.parent) as temporary:
            target = Path(temporary) / "timeline_ir.json"
            command = [sys.executable, "-m", "q1lens", "q1timeline", "analyze", "--project", str(project), "--out", str(target), "--format", "vscode-json", "--summary-only"]
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(base.REPO / "src") + os.pathsep + environment.get("PYTHONPATH", "")
            result = subprocess.run(command, cwd=base.REPO, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=300)
            (project.parent / "cli.log").write_text(result.stdout+result.stderr, encoding="utf-8")
            require(result.returncode == 0, f"CLI failed ({result.returncode}): {result.stdout}{result.stderr}")
            ir = json.loads(target.read_text(encoding="utf-8"))
            evidence = check(ir, expected, p)
            target.replace(destination)
        require(base.original_hashes() == before, "Boundary verification changed an original file")
        digest = hashlib.sha256()
        with destination.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b""):
                digest.update(chunk)
        evidence.update(status="passed", started_utc=progress["started_utc"], last_validated_utc=utc_now(),
                        checker_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                        elapsed_seconds=time.perf_counter()-started, original_sha256=before, cli_project=str(project), ir_path=str(destination),
                        ir_bytes=destination.stat().st_size, ir_sha256=digest.hexdigest())
        write_json(evidence_path, evidence)
        return evidence
    except BaseException as error:
        progress.update(status="interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "failed",
                        reason=f"{type(error).__name__}: {error}", finished_utc=utc_now())
        write_json(evidence_path, progress)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, action="append")
    parser.add_argument("--out-dir", type=Path, default=base.REPO / "output/extended-simulation-validation/boundaries")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    names = args.profile or PROFILES
    args.out_dir.mkdir(parents=True, exist_ok=True)
    report = [{"profile": name, "passed": False, "status": "queued"} for name in names]
    report_path = args.out_dir / "report.json"
    write_json(report_path, report)
    for index, name in enumerate(names):
        report[index].update(status="in_progress", started_utc=utc_now())
        write_json(report_path, report)
        try:
            if args.prepare_only:
                folder = args.out_dir / name
                folder.mkdir(parents=True, exist_ok=True)
                write_json(folder / "evidence.json", report[index])
                project, _, _ = prepare(name, args.out_dir)
                report[index].update(status="prepared")
                write_json(project.parent / "evidence.json", report[index])
                print(project, flush=True)
                continue
            evidence = run_profile(name, args.out_dir)
            report[index].update(passed=True, status="passed", events=evidence["event_count"], elapsed_seconds=evidence["elapsed_seconds"],
                                 ir_path=evidence["ir_path"], last_validated_utc=evidence["last_validated_utc"])
            print(f"PASS {name}: {evidence['shots']} shots; {evidence['event_count']} events; {evidence['elapsed_seconds']:.2f}s", flush=True)
        except (base.VerificationFailure, OSError, ValueError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired) as error:
            report[index].update(passed=False, status="failed", reason=f"{type(error).__name__}: {error}")
            print(f"FAIL {name}: {error}", flush=True)
        except BaseException as error:
            report[index].update(passed=False, status="interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "failed",
                                 reason=f"{type(error).__name__}: {error}")
            raise
        finally:
            write_json(report_path, report)
    succeeded = all(row["status"] == "prepared" for row in report) if args.prepare_only else all(row["passed"] for row in report)
    return 0 if report and succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
