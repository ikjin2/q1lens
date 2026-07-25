from __future__ import annotations

import platform
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from importlib import metadata
from math import isfinite
from typing import Any


QBS_TO_Q1TIMELINE_NS = 1e9
SPAN_TOLERANCE_NS = 0.001
DEBUG_EVENT_KINDS = {
    "branch_region",
    "loop_block",
    "loop_iteration_preview",
    "q1_issue",
    "queue_depth",
    "slack",
    "stop",
    "underflow_warning",
    "unknown_region",
}
TIMED_Q1ASM_OPS = {"play", "wait", "upd_param"}


def build_consistency_report(
    qbs_ir: Mapping[str, Any],
    q1timeline_ir: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    findings = _span_drift_findings(qbs_ir, q1timeline_ir or {})
    return {
        "version": "0.1.0",
        "summary": {
            "schedule": _nested_text(qbs_ir, "schedule", "name"),
            "finding_count": len(findings),
            "scheduler_bug_candidate_count": len(findings),
        },
        "coverage": _coverage(qbs_ir),
        "findings": findings,
        "scheduler_bug_candidates": [_candidate_from_finding(finding) for finding in findings],
    }


def versions_payload() -> dict[str, Any]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": {
            "q1lens": _package_version("q1lens"),
            "qblox-scheduler": _package_version("qblox-scheduler"),
            "quantify-scheduler": _package_version("quantify-scheduler"),
        },
    }


def _span_drift_findings(
    qbs_ir: Mapping[str, Any],
    q1timeline_ir: Mapping[str, Any],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for block in _records(qbs_ir.get("symbolic_pulses")):
        qbs_span = _block_span_ns(block)
        if qbs_span is None:
            continue
        for row in _records(qbs_ir.get("q1asm_provenance")):
            if not _provenance_matches_block(row, block):
                continue
            q1_span = _q1timeline_span_for_row(qbs_ir, q1timeline_ir, row)
            if q1_span is None or not _span_drifts(qbs_span, q1_span):
                continue
            line_range = _line_range(row)
            findings.append(
                {
                    "kind": "span_drift",
                    "severity": "warning",
                    "message": (
                        f"Symbolic block {_text(block.get('id')) or '<unknown>'} spans "
                        f"QBS {_span_label(qbs_span)}, q1timeline {_span_label(q1_span)}."
                    ),
                    "source_id": _text(block.get("id")),
                    "operation_id": _text(block.get("operation_id")),
                    "schedulable_id": _text(block.get("schedulable_id")),
                    "sequencer_id": _sequencer_id(row),
                    "qbs_span_ns": qbs_span,
                    "q1timeline_span_ns": q1_span,
                    "delta_ns": _span_delta(qbs_span, q1_span),
                    "q1asm_line_range": {"start": line_range[0], "end": line_range[1]} if line_range else None,
                    "instruction_roles": _instruction_roles(row),
                    "provenance_confidence": _text(row.get("confidence")) or "unknown",
                    "evidence": {
                        "root_cause": "unassigned",
                        "classification": "cross_layer_mismatch_candidate",
                    },
                }
            )
            break
    return findings


def _coverage(qbs_ir: Mapping[str, Any]) -> dict[str, Any]:
    symbolic_blocks = _records(qbs_ir.get("symbolic_pulses"))
    provenance_rows = _records(qbs_ir.get("q1asm_provenance"))
    mapped_blocks = [
        block
        for block in symbolic_blocks
        if any(_provenance_matches_block(row, block) for row in provenance_rows)
    ]
    confidence = Counter(_text(row.get("confidence")) or "unknown" for row in provenance_rows)
    q1asm_lines = _q1asm_line_coverage(qbs_ir, provenance_rows)
    return {
        "symbolic_blocks": {
            "total": len(symbolic_blocks),
            "mapped": len(mapped_blocks),
            "unmapped": len(symbolic_blocks) - len(mapped_blocks),
        },
        "provenance_rows": {
            "total": len(provenance_rows),
        },
        "provenance_confidence": dict(sorted(confidence.items())),
        "q1asm_lines": q1asm_lines,
    }


def _q1asm_line_coverage(
    qbs_ir: Mapping[str, Any],
    provenance_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    timed_lines: set[tuple[str, int]] = set()
    mapped_timed_lines: set[tuple[str, int]] = set()
    line_texts: dict[tuple[str, int], str] = {}
    for sequencer_id, text in _q1asm_texts(qbs_ir).items():
        lines = text.splitlines()
        for index, line in enumerate(lines, start=1):
            if _is_timed_q1asm_line(line):
                key = (sequencer_id, index)
                timed_lines.add(key)
                line_texts[key] = line
        for row in provenance_rows:
            if _sequencer_id(row) != sequencer_id:
                continue
            line_range = _line_range(row)
            if line_range is None:
                continue
            start, end = line_range
            for line_no in range(start, end + 1):
                if 1 <= line_no <= len(lines) and _is_timed_q1asm_line(lines[line_no - 1]):
                    mapped_timed_lines.add((sequencer_id, line_no))
    orphan_lines = sorted(timed_lines - mapped_timed_lines)
    return {
        "timed_instruction_total": len(timed_lines),
        "mapped_timed_instruction_lines": len(mapped_timed_lines),
        "orphan_timed_instruction_lines": sorted({line_no for _, line_no in orphan_lines}),
        "orphan_timed_instruction_locations": [
            {
                "sequencer_id": sequencer_id,
                "line": line_no,
                "instruction": _opcode(line_texts.get((sequencer_id, line_no), "")),
            }
            for sequencer_id, line_no in orphan_lines
        ],
    }


def _candidate_from_finding(finding: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": "candidate",
        "confidence": "needs_minimal_repro",
        "kind": "scheduler_span_mismatch_candidate",
        "source_id": finding.get("source_id"),
        "operation_id": finding.get("operation_id"),
        "schedulable_id": finding.get("schedulable_id"),
        "sequencer_id": finding.get("sequencer_id"),
        "q1asm_line_range": finding.get("q1asm_line_range"),
        "qbs_span_ns": finding.get("qbs_span_ns"),
        "q1timeline_span_ns": finding.get("q1timeline_span_ns"),
        "delta_ns": finding.get("delta_ns"),
        "required_next_step": "Create a minimal qblox-scheduler-only reproduction before assigning root cause.",
    }


def _q1timeline_span_for_row(
    qbs_ir: Mapping[str, Any],
    q1timeline_ir: Mapping[str, Any],
    row: Mapping[str, Any],
) -> dict[str, float] | None:
    line_range = _line_range(row)
    sequencer = _sequencer_id(row)
    if line_range is None or sequencer is None:
        return None
    start_line, end_line = line_range
    aliases = _sequencer_aliases(qbs_ir, sequencer)
    roles = _instruction_roles(row)
    spans: list[tuple[float, float]] = []
    for event in _records(q1timeline_ir.get("events")):
        event_seq = _text(event.get("sequencer_id") or event.get("sequencer"))
        event_line = _int(_nested_value(event, "source", "line"))
        event_span = _event_span(event)
        if (
            event_seq not in aliases
            or event_line is None
            or event_line < start_line
            or event_line > end_line
            or event_span is None
            or not _event_matches_roles(event, roles)
        ):
            continue
        spans.append(event_span)
    if not spans:
        return None
    start = min(span[0] for span in spans)
    end = max(span[1] for span in spans)
    return _span(start, end)


def _event_matches_roles(event: Mapping[str, Any], roles: Sequence[str]) -> bool:
    kind = _text(event.get("kind")) or ""
    if kind in DEBUG_EVENT_KINDS:
        return False
    if not roles:
        return True
    raw = _text(_nested_value(event, "source", "raw")) or ""
    opcode = _opcode(raw)
    return any(kind == role or opcode == role or (role == "acquire" and (kind.startswith("acquire") or opcode.startswith("acquire_"))) for role in roles)


def _event_span(event: Mapping[str, Any]) -> tuple[float, float] | None:
    start = _concrete_value(event.get("t0"))
    end = _concrete_value(event.get("t1"))
    if start is None or end is None or end < start:
        return None
    return (start, end)


def _block_span_ns(block: Mapping[str, Any]) -> dict[str, float] | None:
    start_s = _number(block.get("abs_time"))
    duration_s = _number(block.get("duration"))
    if start_s is None or duration_s is None or duration_s < 0:
        return None
    start = start_s * QBS_TO_Q1TIMELINE_NS
    return _span(start, start + duration_s * QBS_TO_Q1TIMELINE_NS)


def _span(start: float, end: float) -> dict[str, float]:
    return {
        "start": _clean_float(start),
        "end": _clean_float(end),
        "duration": _clean_float(end - start),
    }


def _span_delta(left: Mapping[str, float], right: Mapping[str, float]) -> dict[str, float]:
    return {
        "start": _clean_float(float(right["start"]) - float(left["start"])),
        "end": _clean_float(float(right["end"]) - float(left["end"])),
        "duration": _clean_float(float(right["duration"]) - float(left["duration"])),
    }


def _span_drifts(left: Mapping[str, float], right: Mapping[str, float]) -> bool:
    return any(
        abs(float(right[key]) - float(left[key])) > SPAN_TOLERANCE_NS
        for key in ("start", "end", "duration")
    )


def _span_label(span: Mapping[str, float]) -> str:
    return f"{_format_ns(float(span['start']))}-{_format_ns(float(span['end']))} ns"


def _format_ns(value: float) -> str:
    rounded = round(value)
    if abs(value - rounded) < 1e-6:
        return str(rounded)
    return str(_clean_float(value))


def _clean_float(value: float) -> float:
    return float(f"{value:.12g}")


def _q1asm_texts(qbs_ir: Mapping[str, Any]) -> dict[str, str]:
    texts: dict[str, str] = {}
    by_sequencer = qbs_ir.get("q1asm_by_sequencer")
    if isinstance(by_sequencer, Mapping):
        for key, value in by_sequencer.items():
            if isinstance(value, str):
                texts[str(key)] = value
    for program in _records(qbs_ir.get("q1asm_programs")):
        sequencer = _text(program.get("sequencer_id") or program.get("sequencer"))
        text = program.get("text")
        if sequencer and isinstance(text, str):
            texts.setdefault(sequencer, text)
    return texts


def _is_timed_q1asm_line(line: str) -> bool:
    opcode = _opcode(line)
    return opcode in TIMED_Q1ASM_OPS or opcode.startswith("acquire_") or opcode == "acquire"


def _opcode(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return ""
    return stripped.replace(",", " ").split()[0]


def _provenance_matches_block(row: Mapping[str, Any], block: Mapping[str, Any]) -> bool:
    source_id = _text(row.get("source_id"))
    if source_id:
        return source_id == _text(block.get("id"))
    candidates = {
        value
        for value in (
            _text(block.get("id")),
            _text(block.get("operation_id")),
            _text(block.get("schedulable_id")),
        )
        if value
    }
    return bool((_text(row.get("operation_id")) in candidates) or (_text(row.get("schedulable_id")) in candidates))


def _line_range(row: Mapping[str, Any]) -> tuple[int, int] | None:
    start = _int(row.get("q1asm_line_start", row.get("line")))
    end = _int(row.get("q1asm_line_end", row.get("line_end", row.get("line"))))
    if start is None or end is None or start > end:
        return None
    return (start, end)


def _instruction_roles(row: Mapping[str, Any]) -> list[str]:
    roles: list[str] = []
    instruction = _text(row.get("instruction"))
    if instruction:
        roles.append(instruction)
    raw_roles = row.get("instruction_roles")
    if isinstance(raw_roles, Sequence) and not isinstance(raw_roles, str):
        for role in raw_roles:
            text = _text(role)
            if text and text not in roles:
                roles.append(text)
    return roles


def _sequencer_aliases(qbs_ir: Mapping[str, Any], sequencer: str) -> set[str]:
    aliases = {sequencer}
    for program in _records(qbs_ir.get("q1asm_programs")):
        names = {
            name
            for name in (
                _text(program.get("sequencer_id")),
                _text(program.get("sequencer")),
            )
            if name
        }
        if sequencer in names:
            aliases.update(names)
    return aliases


def _sequencer_id(row: Mapping[str, Any]) -> str | None:
    return _text(row.get("sequencer_id") or row.get("sequencer"))


def _records(value: Any) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _nested_value(value: Mapping[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _nested_text(value: Mapping[str, Any], *keys: str) -> str | None:
    return _text(_nested_value(value, *keys))


def _concrete_value(value: Any) -> float | None:
    if isinstance(value, Mapping):
        return _number(value.get("value"))
    return _number(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
        return number if isfinite(number) else None
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _text(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None
