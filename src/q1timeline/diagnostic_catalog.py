from __future__ import annotations

from collections import UserDict
import re
from typing import Any


class _SafeFormatDict(UserDict):
    def __missing__(self, key: str) -> str:
        return "unknown"


DIAGNOSTIC_PRESENTATIONS: dict[str, dict[str, str]] = {
    "alignment_missing": {
        "group": "Alignment",
        "title": "Alignment anchor was not found",
        "summary": "{message}",
        "fix": "Check the alignment mode and make sure each sequencer contains the configured anchor event.",
    },
    "analysis_incomplete": {
        "group": "Analyzer Coverage",
        "title": "Analysis is incomplete",
        "summary": "{message}",
        "fix": "Treat the affected timing as uncertain and inspect the source line before relying on this timeline section.",
    },
    "definite_underflow": {
        "group": "Timing",
        "title": "Definite RT underflow",
        "summary": "The RT queue slack is {slack_ns} ns, so this packet is scheduled too early.",
        "fix": "Increase the wait gap, reduce the preceding Q1 work, or move this RT packet later.",
    },
    "duplicate_sequencer_id": {
        "group": "Project Config",
        "title": "Duplicate sequencer id",
        "summary": "{message}",
        "fix": "Give every sequencer a unique id in q1timeline.yml.",
    },
    "duplicate_sequencer_name": {
        "group": "Project Config",
        "title": "Duplicate sequencer name",
        "summary": "{message}",
        "fix": "Give every sequencer a unique display name in q1timeline.yml.",
    },
    "feedback_latency_violation": {
        "group": "Timing",
        "title": "Feedback receive is too early",
        "summary": (
            "Channel {channel} is popped {missing_wait_ns} ns before the official LINQ {route} latency "
            "for {data_type} data."
        ),
        "fix": "Insert at least {missing_wait_ns} ns before this fb_pop_data, or move the receive later.",
    },
    "feedback_route_mismatch": {
        "group": "Timing",
        "title": "Feedback route does not match sequencers",
        "summary": (
            "Channel {channel} uses {configured_route}, but this flow is {actual_scope} "
            "from {send_sequencer_id} to {receive_sequencer_id}."
        ),
        "fix": "Use an ID in the {expected_channel_range} range for {expected_route} feedback.",
    },
    "feedback_fifo_imbalance": {
        "group": "Timing",
        "title": "Feedback FIFO is imbalanced",
        "summary": "{message}",
        "fix": "Match feedback sends and receives on this channel, or drain intentionally unused payloads.",
    },
    "illegal_instruction": {
        "group": "Q1ASM",
        "title": "Illegal instruction executed",
        "summary": "{message}",
        "fix": "Remove the illegal instruction or stop execution before it is reached.",
    },
    "invalid_alignment_anchor_kinds": {
        "group": "Project Config",
        "title": "Invalid alignment anchors",
        "summary": "{message}",
        "fix": "Set alignment.anchor_kinds to a non-empty list of event kind strings.",
    },
    "invalid_alignment_policy": {
        "group": "Project Config",
        "title": "Invalid alignment policy",
        "summary": "{message}",
        "fix": "Use a supported alignment mode such as none, first_wait_sync, first_wait_trigger, first_anchor, or manual:<offset>.",
    },
    "invalid_argument_count": {
        "group": "Q1ASM",
        "title": "Wrong number of instruction arguments",
        "summary": "{message}",
        "fix": "Match the instruction signature in the Q1ASM reference.",
    },
    "invalid_argument_type": {
        "group": "Q1ASM",
        "title": "Instruction argument has the wrong type",
        "summary": "{message}",
        "fix": "Use the expected immediate, register, or label argument type.",
    },
    "invalid_argument_value": {
        "group": "Q1ASM",
        "title": "Instruction argument value is invalid",
        "summary": "{message}",
        "fix": "Change the argument to satisfy the required range or sign constraint.",
    },
    "invalid_branch_policy": {
        "group": "Project Config",
        "title": "Invalid branch policy",
        "summary": "{message}",
        "fix": "Use a supported branch policy or remove the invalid branch assumption.",
    },
    "invalid_config_path": {
        "group": "Project Config",
        "title": "Configured path is not readable",
        "summary": "{message}",
        "fix": "Update the path so it points to a readable file.",
    },
    "invalid_json": {
        "group": "Project Config",
        "title": "Invalid JSON",
        "summary": "{message}",
        "fix": "Fix the JSON syntax or value type reported at this location.",
    },
    "invalid_loop_policy": {
        "group": "Project Config",
        "title": "Invalid loop policy",
        "summary": "{message}",
        "fix": "Use a supported loop policy in the analysis config.",
    },
    "invalid_loop_preview": {
        "group": "CLI",
        "title": "Invalid loop preview request",
        "summary": "{message}",
        "fix": "Use a loop preview selector with a valid positive iteration count.",
    },
    "invalid_sequencer": {
        "group": "Project Config",
        "title": "Invalid sequencer entry",
        "summary": "{message}",
        "fix": "Make each sequencer entry in q1timeline.yml a mapping with at least a file field.",
    },
    "invalid_underflow_policy": {
        "group": "Project Config",
        "title": "Invalid underflow policy",
        "summary": "{message}",
        "fix": "Use a supported underflow policy in the analysis config.",
    },
    "invalid_view_mode": {
        "group": "Project Config",
        "title": "Invalid view mode",
        "summary": "{message}",
        "fix": "Set view.default_mode to normal or debug.",
    },
    "invalid_yaml": {
        "group": "Project Config",
        "title": "Invalid YAML",
        "summary": "{message}",
        "fix": "Fix the YAML syntax or shape reported at this location.",
    },
    "loop_truncated": {
        "group": "Preview",
        "title": "Loop preview was truncated",
        "summary": "{message}",
        "fix": "Increase the preview count only if you need to inspect more iterations.",
    },
    "missing_optional_file": {
        "group": "Project Config",
        "title": "Optional file is missing",
        "summary": "{message}",
        "fix": "Add the optional file or remove the optional file reference.",
    },
    "missing_required_field": {
        "group": "Project Config",
        "title": "Required config field is missing",
        "summary": "{message}",
        "fix": "Add the required field to q1timeline.yml.",
    },
    "missing_required_file": {
        "group": "Project Config",
        "title": "Required file is missing",
        "summary": "{message}",
        "fix": "Create the file or update the configured path.",
    },
    "possible_underflow": {
        "group": "Timing",
        "title": "Possible RT underflow",
        "summary": "The RT queue slack is {slack_ns} ns after a sync or trigger, so underflow depends on runtime alignment.",
        "fix": "Increase the wait gap or inspect the sync/trigger relationship around this packet.",
    },
    "q1asm_read_error": {
        "group": "Project Config",
        "title": "Could not read Q1ASM file",
        "summary": "{message}",
        "fix": "Check that the file exists, is readable, and uses a supported text encoding.",
    },
    "register_not_ready": {
        "group": "Timing",
        "title": "Register read before write is ready",
        "summary": "{message}",
        "fix": "Insert an instruction gap such as nop before reading the register.",
    },
    "runtime_dependent_timing": {
        "group": "Timing",
        "title": "Timing depends on runtime data",
        "summary": "{message}",
        "fix": "Inspect the runtime-dependent branch or value and validate timing on hardware if needed.",
    },
    "symbolic_duration": {
        "group": "Timing",
        "title": "Duration is symbolic",
        "summary": "{message}",
        "fix": "Provide parameter values if you need concrete timing.",
    },
    "sync_mismatch": {
        "group": "Alignment",
        "title": "Synchronization events do not match",
        "summary": "{message}",
        "fix": "Check wait_sync/wait_trigger placement across sequencers.",
    },
    "syntax_error": {
        "group": "Q1ASM",
        "title": "Q1ASM syntax error",
        "summary": "{message}",
        "fix": "Fix the syntax at the highlighted line.",
    },
    "undefined_label": {
        "group": "Q1ASM",
        "title": "Branch label is not defined",
        "summary": "{message}",
        "fix": "Define the target label or update the branch target.",
    },
    "unknown_duration": {
        "group": "Timing",
        "title": "Duration register is unknown",
        "summary": "{message}",
        "fix": "Initialize the duration register before using it in a timing instruction.",
    },
    "unknown_instruction": {
        "group": "Q1ASM",
        "title": "Unknown instruction",
        "summary": "{message}",
        "fix": "Check the mnemonic spelling or update the instruction table if this op should be supported.",
    },
    "unresolved_branch": {
        "group": "Control Flow",
        "title": "Branch could not be resolved statically",
        "summary": "{message}",
        "fix": "Use branch assumptions or provide concrete register values to inspect a specific path.",
    },
    "unresolved_symbol": {
        "group": "Parameters",
        "title": "Symbol could not be resolved",
        "summary": "{message}",
        "fix": "Define the symbol in Q1ASM or provide it through params.",
    },
    "unsupported_instruction": {
        "group": "Q1ASM",
        "title": "Instruction is not supported by the analyzer",
        "summary": "{message}",
        "fix": "Model this instruction in the analyzer before relying on its timing effects.",
    },
}


def describe_diagnostic(diagnostic: Any) -> dict[str, str]:
    category = str(_get_diagnostic_field(diagnostic, "category", "diagnostic"))
    severity = str(_get_diagnostic_field(diagnostic, "severity", "info"))
    message = str(_get_diagnostic_field(diagnostic, "message", category))
    details = _get_diagnostic_field(diagnostic, "details", {})
    if not isinstance(details, dict):
        details = {}
    spec = DIAGNOSTIC_PRESENTATIONS.get(
        category,
        {
            "group": "Diagnostics",
            "title": _humanize(category),
            "summary": "{message}",
            "fix": "",
        },
    )
    values = _SafeFormatDict(
        {
            "category": _humanize(category),
            "severity": severity,
            "message": message,
            **{str(key): _format_value(value) for key, value in details.items()},
        }
    )
    summary_template = spec.get("summary", "{message}")
    fix_template = spec.get("fix", "")
    summary = message if _has_missing_field(summary_template, values) else _format_template(summary_template, values)
    fix = "" if _has_missing_field(fix_template, values) else _format_template(fix_template, values)
    return {
        "category": category,
        "severity": severity,
        "group": spec.get("group", "Diagnostics"),
        "title": _format_template(spec.get("title", category), values),
        "summary": summary,
        "fix": fix,
    }


def format_presentation_message(diagnostic: Any) -> str:
    presentation = describe_diagnostic(diagnostic)
    message = f"{presentation['title']}: {presentation['summary']}"
    if presentation["fix"]:
        message += f" Fix: {presentation['fix']}"
    return message


def _get_diagnostic_field(diagnostic: Any, field: str, default: Any) -> Any:
    if isinstance(diagnostic, dict):
        return diagnostic.get(field, default)
    return getattr(diagnostic, field, default)


def _format_template(template: str, values: _SafeFormatDict) -> str:
    try:
        return template.format_map(values)
    except (KeyError, ValueError):
        return template


def _has_missing_field(template: str, values: _SafeFormatDict) -> bool:
    return any(field not in values for field in re.findall(r"{([^}]+)}", template))


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        return _humanize(value)
    return str(value)


def _humanize(value: str) -> str:
    return value.replace("_", " ")
