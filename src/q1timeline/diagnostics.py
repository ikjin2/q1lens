from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from q1timeline.diagnostic_catalog import describe_diagnostic


Severity = Literal["error", "warning", "info", "hint"]
SEVERITIES: tuple[Severity, ...] = ("error", "warning", "info", "hint")
DIAGNOSTICS_SCHEMA_VERSION = "0.1.0"
REQUIRED_DIAGNOSTIC_CATEGORIES = {
    "syntax_error",
    "unknown_instruction",
    "unsupported_instruction",
    "undefined_label",
    "illegal_instruction",
    "invalid_argument_count",
    "invalid_argument_type",
    "invalid_argument_value",
    "unresolved_symbol",
    "symbolic_duration",
    "unknown_duration",
    "register_not_ready",
    "unresolved_branch",
    "loop_truncated",
    "possible_underflow",
    "definite_underflow",
    "feedback_latency_violation",
    "feedback_route_mismatch",
    "feedback_fifo_imbalance",
    "alignment_missing",
    "sync_mismatch",
    "invalid_alignment_policy",
    "invalid_alignment_anchor_kinds",
    "invalid_branch_policy",
    "invalid_loop_preview",
    "invalid_loop_policy",
    "invalid_underflow_policy",
    "invalid_view_mode",
    "runtime_dependent_timing",
    "analysis_incomplete",
    "q1asm_read_error",
    "missing_required_file",
    "missing_required_field",
    "missing_optional_file",
    "invalid_config_path",
    "invalid_yaml",
    "invalid_json",
    "invalid_sequencer",
    "duplicate_sequencer_id",
    "duplicate_sequencer_name",
}


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    category: str
    message: str
    source: Any | None = None
    related_events: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def severity_counts(diagnostics: list[Diagnostic]) -> dict[str, int]:
    counts = {severity: 0 for severity in SEVERITIES}
    for diagnostic in diagnostics:
        counts[diagnostic.severity] = counts.get(diagnostic.severity, 0) + 1
    return counts


def format_summary(diagnostics: list[Diagnostic]) -> str:
    counts = severity_counts(diagnostics)
    return "Diagnostics: " + " ".join(f"{severity}={counts[severity]}" for severity in SEVERITIES)


def format_diagnostic(diagnostic: Diagnostic) -> str:
    location = _format_source(diagnostic.source)
    presentation = describe_diagnostic(diagnostic)
    parts = [diagnostic.severity, presentation["title"]]
    if location:
        parts.append(location)
    parts.append(presentation["summary"])
    if presentation["fix"]:
        parts.append(f"Fix: {presentation['fix']}")
    parts.append(f"[{diagnostic.category}]")
    return " ".join(parts)


def has_fatal_diagnostics(diagnostics: list[Diagnostic]) -> bool:
    return any(diagnostic.severity == "error" for diagnostic in diagnostics)


def has_strict_failure(diagnostics: list[Diagnostic]) -> bool:
    return any(diagnostic.severity in {"error", "warning"} for diagnostic in diagnostics)


def _format_source(source: Any | None) -> str:
    if source is None:
        return ""
    if isinstance(source, dict):
        file = source.get("file")
        line = source.get("line")
        column = source.get("column", 1)
    else:
        file = getattr(source, "file", None)
        line = getattr(source, "line", None)
        column = getattr(source, "column", 1)
    if file is None or line is None:
        return ""
    return f"{file}:{line}:{column}"
