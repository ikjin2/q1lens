"""Regenerate the gallery's real project-analysis and bounded-playback fixtures.

cases.json contains hand-authored source/project paths, playback step/status
expectations, summary checks (mode/name/source_end/selected inclusive lines),
ordinary opcode points (mode/line/op), and modes which must have no summaries.
This validates backend provenance and coverage, not the editor's range algorithm;
verify-host.cjs checks actual editor selections against the authored ranges.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


GALLERY = Path(__file__).resolve().parent
REPOSITORY = GALLERY.parents[1]
OUTPUT = REPOSITORY / "output" / "loop-selection-review"
sys.path.insert(0, str(REPOSITORY / "src"))

from q1timeline.playback import PlaybackSession
from q1timeline.q1asm.parser import parse_q1asm


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def same_source(source: dict, path: Path, lines: list[str]) -> None:
    assert Path(source["file"]).resolve() == path, f"Unexpected source file: {source}"
    line = source["line"]
    assert type(line) is int and 1 <= line <= len(lines), f"Invalid source line: {source}"
    assert source.get("raw") == lines[line - 1], f"Source text differs from file at line {line}"


def check_summary(check: dict, ir: dict, source: Path, lines: list[str], program) -> dict:
    candidates = [event for event in ir["events"] if event["kind"] == "loop_block"
                  and event.get("meta", {}).get("source_end", {}).get("line") == check["source_end"]]
    assert candidates, f"Missing loop summary ending at source line {check['source_end']}"
    first, last = check["selected"]
    assert 1 <= first <= last <= len(lines), "Authored selection exceeds the source"
    by_id = {event["id"]: event for event in ir["events"]}
    provenance = []
    for event in candidates:
        meta = event["meta"]
        sources = [event["source"], meta["source_start"], meta["source_end"]]
        members = set(meta.get("first_iteration_event_ids", [])) | set(meta.get("playback_event_ids", []))
        assert members <= by_id.keys(), "Loop references missing member events"
        for event_id in members:
            member = by_id[event_id]
            assert member["sequencer_id"] == event["sequencer_id"], "Cross-sequencer loop member"
            sources.append(member["source"])
        for location in sources:
            same_source(location, source, lines)
            assert first <= location["line"] <= last, f"Authored selection misses source line {location['line']}"
        source_lines = sorted({location["line"] for location in sources})
        assert last in source_lines, "Authored selection extends beyond the proven loop source"
        if first not in source_lines:
            label = re.fullmatch(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?:#.*)?", lines[first - 1])
            assert label and label[1] in program.labels, "Extra first line is not a parsed target label"
            target = program.instructions[program.labels[label[1]]]
            assert target.source.line in source_lines, "Selected label does not target a loop member"
        provenance.append({"event_id": event["id"], "source_start": meta["source_start"],
                           "source_end": meta["source_end"], "member_source_lines": source_lines})
    return {**check, "status": "passed", "matched_event_ids": [event["id"] for event in candidates],
            "provenance": provenance}


def check_point(point: dict, ir: dict, source: Path, lines: list[str], program) -> dict:
    instructions = [instruction for instruction in program.instructions
                    if instruction.source.line == point["line"] and instruction.op == point["op"]]
    assert instructions, "Authored point does not identify a parsed source opcode"
    events = [event for event in ir["events"] if event["kind"] == "q1_issue"
              and event.get("source", {}).get("line") == point["line"]
              and event.get("meta", {}).get("op") == point["op"]]
    assert events, f"No issued {point['op']} at source line {point['line']}"
    for event in events:
        same_source(event["source"], source, lines)
    return {**point, "status": "passed", "event_ids": [event["id"] for event in events]}


def run_case(case: dict) -> dict:
    source = (GALLERY / case["source"]).resolve()
    project = (GALLERY / case["project"]).resolve()
    destination = OUTPUT / "fixtures" / case["id"]
    destination.mkdir(parents=True, exist_ok=True)
    paths = {mode: destination / f"{mode}.json" for mode in ("analysis", "playback")}
    for path in paths.values():
        path.unlink(missing_ok=True)
    result = {"case": case["id"], "status": "failed", "source": str(source), "project": str(project),
              "checks": [], "points": [], "no_loop_checks": [], "failures": []}
    try:
        text = source.read_text(encoding="utf-8-sig")
        lines = text.splitlines()
        program = parse_q1asm(text, file=str(source))
        assert not program.diagnostics, f"Source parse diagnostics: {program.diagnostics}"
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPOSITORY / "src") + os.pathsep + environment.get("PYTHONPATH", "")
        command = [sys.executable, "-m", "q1timeline", "analyze", "--project", str(project),
                   "--out", str(paths["analysis"]), "--no-render", "--mode", "debug"]
        completed = subprocess.run(command, cwd=REPOSITORY, env=environment, capture_output=True, text=True)
        log = destination / "analysis-cli.log"
        log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
        assert completed.returncode == 0, f"Project CLI exited {completed.returncode}; see {log}"
        analysis = json.loads(paths["analysis"].read_text(encoding="utf-8"))
        result["analysis"] = {"ir": str(paths["analysis"]), "cli_exit_code": completed.returncode,
                              "log": str(log), "loop_count": sum(e["kind"] == "loop_block" for e in analysis["events"]),
                              "diagnostics": dict(Counter(d["category"] for d in analysis.get("diagnostics", [])))}
        session = PlaybackSession.from_project(project)
        session.run(budget_ms=5000, max_steps=case["playback"]["max_steps"])
        playback = session.snapshot()
        write_json(paths["playback"], playback)
        result["playback"] = {"ir": str(paths["playback"]), "status": playback["playback"]["status"],
                              "expected_status": case["playback"]["status"], "steps": playback["playback"]["steps"],
                              "max_steps": case["playback"]["max_steps"],
                              "loop_count": sum(e["kind"] == "loop_block" for e in playback["events"])}
        assert result["playback"]["status"] == case["playback"]["status"], "Playback status differs from authored expectation"
        modes = {"analysis": analysis, "playback": playback}
        for check in case["checks"]:
            try:
                result["checks"].append(check_summary(check, modes[check["mode"]], source, lines, program))
            except (AssertionError, KeyError, TypeError, ValueError) as error:
                result["checks"].append({**check, "status": "failed", "error": str(error)})
                result["failures"].append(f"{check['mode']} {check['name']}: {error}")
        for point in case["points"]:
            try:
                result["points"].append(check_point(point, modes[point["mode"]], source, lines, program))
            except (AssertionError, KeyError, TypeError, ValueError) as error:
                result["points"].append({**point, "status": "failed", "error": str(error)})
                result["failures"].append(f"{point['mode']} {point['op']}:{point['line']}: {error}")
        for mode in case["no_loop_modes"]:
            count = sum(event["kind"] == "loop_block" for event in modes[mode]["events"])
            result["no_loop_checks"].append({"mode": mode, "status": "passed" if count == 0 else "failed", "loop_count": count})
            if count:
                result["failures"].append(f"{mode}: expected no loop summaries, found {count}")
        result["status"] = "failed" if result["failures"] else "passed"
    except (AssertionError, OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as error:
        result["failures"].append(str(error))
    return result


def main() -> int:
    if not __debug__:
        raise RuntimeError("Verification requires Python assertions; remove -O and PYTHONOPTIMIZE.")
    cases = json.loads((GALLERY / "cases.json").read_text(encoding="utf-8"))["cases"]
    results = []
    for case in cases:
        result = run_case(case)
        results.append(result)
        print(f"{result['status'].upper():6} {case['id']}")
        for failure in result["failures"]:
            print(f"       {failure}")
    status = "passed" if all(result["status"] == "passed" for result in results) else "failed"
    report = {"status": status, "generated_at": datetime.now(timezone.utc).isoformat(),
              "cases_path": str(GALLERY / "cases.json"), "results": results}
    path = OUTPUT / "backend-results.json"
    write_json(path, report)
    print(f"{len(results)} cases: {status}; {path}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
