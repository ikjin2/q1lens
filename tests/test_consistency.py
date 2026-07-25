from __future__ import annotations

import json
from pathlib import Path

from qbstimeline.consistency import build_consistency_report


def _qbs_ir() -> dict:
    return {
        "version": "0.1.0",
        "schedule": {"name": "unit"},
        "operations": [
            {"id": "x180", "operation_id": "x_q0", "label": "X(q0)", "abs_time": 20e-9, "duration": 40e-9},
            {"id": "measure", "operation_id": "measure_q0", "label": "Measure(q0)", "abs_time": 60e-9, "duration": 460e-9},
        ],
        "symbolic_pulses": [
            {
                "id": "pulse:x180:pulse:0",
                "operation_id": "x_q0",
                "schedulable_id": "x180",
                "kind": "pulse",
                "abs_time": 20e-9,
                "duration": 40e-9,
            },
            {
                "id": "pulse:measure:pulse:0",
                "operation_id": "measure_q0",
                "schedulable_id": "measure",
                "kind": "pulse",
                "abs_time": 60e-9,
                "duration": 160e-9,
            },
            {
                "id": "acq:measure:acquisition:0",
                "operation_id": "measure_q0",
                "schedulable_id": "measure",
                "kind": "acquisition",
                "abs_time": 220e-9,
                "duration": 300e-9,
            },
        ],
        "q1asm_programs": [
            {"sequencer_id": "seq0", "file": "q1asm/seq0.q1asm"},
        ],
        "q1asm_by_sequencer": {
            "seq0": "\n".join(
                [
                    "wait_sync 4",
                    "upd_param 4",
                    "wait 16",
                    "set_awg_gain 32767,0",
                    "play 0,1,4",
                    "wait 36",
                    "set_awg_gain 8192,0",
                    "play 2,3,160",
                    "acquire 0,0,300",
                    "stop",
                ]
            )
            + "\n",
        },
        "q1asm_provenance": [
            {
                "sequencer_id": "seq0",
                "source_id": "pulse:x180:pulse:0",
                "operation_id": "x_q0",
                "schedulable_id": "x180",
                "q1asm_line_start": 4,
                "q1asm_line_end": 6,
                "instruction_roles": ["set_awg_gain", "play", "wait"],
                "confidence": "compiler",
            },
            {
                "sequencer_id": "seq0",
                "source_id": "pulse:measure:pulse:0",
                "operation_id": "measure_q0",
                "schedulable_id": "measure",
                "q1asm_line_start": 7,
                "q1asm_line_end": 8,
                "instruction_roles": ["set_awg_gain", "play"],
                "confidence": "inferred",
            },
        ],
    }


def _q1timeline_ir(*, drift: bool = True, include_debug_noise: bool = True) -> dict:
    x_start = 24 if drift else 20
    x_end = 64 if drift else 60
    events = [
        _event("play", x_start, x_start + 4, 5, "play 0,1,4"),
        _event("wait", x_start + 4, x_end, 6, "wait 36"),
        _event("play", 60, 220, 8, "play 2,3,160"),
        _event("acquire", 220, 520, 9, "acquire 0,0,300"),
    ]
    if include_debug_noise:
        events.extend(
            [
                _event("q1_issue", 12, 16, 4, "set_awg_gain 32767,0"),
                _event("queue_depth", 4, 4, 2, "upd_param 4"),
                _event("slack", 4, 4, 2, "upd_param 4"),
                _event("underflow_warning", 4, 4, 2, "upd_param 4"),
            ]
        )
    return {"events": events}


def _event(kind: str, t0: float, t1: float, line: int, raw: str) -> dict:
    return {
        "id": f"seq0:{kind}:{line}:{t0}",
        "kind": kind,
        "sequencer_id": "seq0",
        "t0": {"kind": "concrete", "value": t0},
        "t1": {"kind": "concrete", "value": t1},
        "duration": {"kind": "concrete", "value": t1 - t0},
        "source": {"file": "q1asm/seq0.q1asm", "line": line, "raw": raw},
    }


def test_report_emits_span_drift_finding() -> None:
    report = build_consistency_report(_qbs_ir(), _q1timeline_ir(drift=True))

    finding = report["findings"][0]
    assert finding["kind"] == "span_drift"
    assert finding["source_id"] == "pulse:x180:pulse:0"
    assert finding["sequencer_id"] == "seq0"
    assert finding["qbs_span_ns"] == {"start": 20.0, "end": 60.0, "duration": 40.0}
    assert finding["q1timeline_span_ns"] == {"start": 24.0, "end": 64.0, "duration": 40.0}
    assert finding["q1asm_line_range"] == {"start": 4, "end": 6}


def test_report_does_not_emit_span_drift_when_spans_match() -> None:
    report = build_consistency_report(_qbs_ir(), _q1timeline_ir(drift=False))

    assert report["findings"] == []
    assert report["summary"]["finding_count"] == 0


def test_report_ignores_debug_only_q1timeline_events_for_span_comparison() -> None:
    q1timeline_ir = {"events": [_event("q1_issue", 12, 16, 4, "set_awg_gain 32767,0")]}

    report = build_consistency_report(_qbs_ir(), q1timeline_ir)

    assert report["findings"] == []


def test_report_ignores_nonfinite_q1timeline_event_values() -> None:
    q1timeline_ir = {"events": [_event("play", 24, 28, 5, "play 0,1,4")]}
    q1timeline_ir["events"][0]["t0"] = {"kind": "concrete", "value": float("nan")}

    report = build_consistency_report(_qbs_ir(), q1timeline_ir)

    assert report["findings"] == []


def test_report_includes_mapping_confidence_and_coverage_metrics() -> None:
    report = build_consistency_report(_qbs_ir(), _q1timeline_ir(drift=True))

    assert report["coverage"]["symbolic_blocks"] == {"total": 3, "mapped": 2, "unmapped": 1}
    assert report["coverage"]["provenance_confidence"] == {"compiler": 1, "inferred": 1}
    q1asm_coverage = report["coverage"]["q1asm_lines"]
    assert q1asm_coverage["timed_instruction_total"] == 6
    assert q1asm_coverage["mapped_timed_instruction_lines"] == 3
    assert q1asm_coverage["orphan_timed_instruction_lines"] == [2, 3, 9]


def test_report_projects_span_drifts_to_scheduler_bug_candidates() -> None:
    report = build_consistency_report(_qbs_ir(), _q1timeline_ir(drift=True))

    candidate = report["scheduler_bug_candidates"][0]
    assert candidate["status"] == "candidate"
    assert candidate["confidence"] == "needs_minimal_repro"
    assert candidate["source_id"] == "pulse:x180:pulse:0"
    assert candidate["sequencer_id"] == "seq0"
    assert candidate["q1asm_line_range"] == {"start": 4, "end": 6}
    assert candidate["required_next_step"] == "Create a minimal qblox-scheduler-only reproduction before assigning root cause."


def test_qbst_mismatch_004_golden_fixture_reports_span_drift_candidate() -> None:
    fixture_dir = Path(__file__).parent / "fixtures" / "qbst-mismatch-004"
    qbs_ir = json.loads((fixture_dir / "qbs_ir.json").read_text(encoding="utf-8"))
    q1timeline_ir = json.loads((fixture_dir / "q1timeline_ir.json").read_text(encoding="utf-8"))

    report = build_consistency_report(qbs_ir, q1timeline_ir)

    assert report["summary"]["finding_count"] == 1
    assert report["scheduler_bug_candidates"][0]["source_id"] == "pulse:x180:pulse:0"


def test_q1asm_line_coverage_counts_same_line_numbers_per_sequencer() -> None:
    qbs_ir = {
        "schedule": {"name": "multi"},
        "symbolic_pulses": [],
        "q1asm_programs": [
            {"sequencer_id": "seq0", "file": "q1asm/seq0.q1asm"},
            {"sequencer_id": "seq1", "file": "q1asm/seq1.q1asm"},
        ],
        "q1asm_by_sequencer": {
            "seq0": "play 0,1,4\nstop\n",
            "seq1": "play 0,1,4\nstop\n",
        },
        "q1asm_provenance": [
            {
                "sequencer_id": "seq0",
                "q1asm_line_start": 1,
                "q1asm_line_end": 1,
                "instruction_roles": ["play"],
                "confidence": "compiler",
            }
        ],
    }

    report = build_consistency_report(qbs_ir, {})

    assert report["coverage"]["q1asm_lines"]["timed_instruction_total"] == 2
    assert report["coverage"]["q1asm_lines"]["mapped_timed_instruction_lines"] == 1
    assert report["coverage"]["q1asm_lines"]["orphan_timed_instruction_locations"] == [
        {"sequencer_id": "seq1", "line": 1, "instruction": "play"}
    ]
