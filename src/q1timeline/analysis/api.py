from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from q1timeline.analysis.alignment import align_timelines
from q1timeline.analysis.interpreter import AnalysisState, interpret_program
from q1timeline.analysis.underflow import analyze_underflow
from q1timeline.ir.serialize import timeline_ir_from_states
from q1timeline.project import is_valid_alignment_mode, is_valid_branch_policy, validate_params_json_mapping
from q1timeline.q1asm.parser import parse_q1asm


def analyze_text(
    text: str,
    *,
    file: str = "<untitled>",
    sequencer_id: str | None = None,
    params: dict[str, Any] | None = None,
    alignment_mode: str = "none",
    alignment_anchor_kinds: list[str] | tuple[str, ...] | None = None,
    branch_policy: str = "collapse_unresolved",
    branch_assumptions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    validated_text, validated_file, sequencer = _validate_analyze_text_inputs(text, file, sequencer_id)
    _validate_branch_policy(branch_policy)
    anchor_kinds = _validate_alignment_options(alignment_mode, alignment_anchor_kinds)
    _validate_params(params)
    state = _analyze_program_text(
        validated_text,
        file=validated_file,
        sequencer_id=sequencer,
        params=params,
        branch_policy=branch_policy,
        branch_assumptions=branch_assumptions,
    )
    align_timelines([state], mode=alignment_mode, anchor_kinds=anchor_kinds)
    return timeline_ir_from_states(
        [state],
        project={
            "root": "",
            "alignment_mode": alignment_mode,
            "alignment_anchor_kinds": list(anchor_kinds),
            "virtual": True,
        },
    )


def analyze_documents(
    documents: Mapping[str, str],
    *,
    params: dict[str, Any] | None = None,
    alignment_mode: str = "first_wait_sync",
    alignment_anchor_kinds: list[str] | tuple[str, ...] | None = None,
    branch_policy: str = "collapse_unresolved",
    branch_assumptions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    _validate_branch_policy(branch_policy)
    anchor_kinds = _validate_alignment_options(alignment_mode, alignment_anchor_kinds)
    _validate_params(params)
    validated_documents = _validate_documents(documents)
    sequencer_ids = _unique_sequencer_ids(validated_documents.keys())
    states = [
        _analyze_program_text(
            text,
            file=file,
            sequencer_id=sequencer_ids[file],
            params=params,
            branch_policy=branch_policy,
            branch_assumptions=branch_assumptions,
        )
        for file, text in validated_documents.items()
    ]
    align_timelines(states, mode=alignment_mode, anchor_kinds=anchor_kinds)
    return timeline_ir_from_states(
        states,
        project={
            "root": "",
            "alignment_mode": alignment_mode,
            "alignment_anchor_kinds": list(anchor_kinds),
            "virtual": True,
        },
    )


def _analyze_program_text(
    text: str,
    *,
    file: str,
    sequencer_id: str,
    params: dict[str, Any] | None,
    branch_policy: str,
    branch_assumptions: Mapping[str, str] | None,
) -> AnalysisState:
    state = interpret_program(
        parse_q1asm(text, file=file),
        sequencer_id=sequencer_id,
        params=params,
        branch_policy=branch_policy,
        branch_assumptions=branch_assumptions,
    )
    analyze_underflow(state)
    return state


def _validate_analyze_text_inputs(text: Any, file: Any, sequencer_id: Any) -> tuple[str, str, str]:
    if not isinstance(text, str):
        raise ValueError(f"Invalid text: expected a string, got {type(text).__name__}")
    try:
        normalized_file = os.fspath(file)
    except TypeError as exc:
        raise ValueError(
            f"Invalid file: expected a string or path-like object, got {type(file).__name__}"
        ) from exc
    if not isinstance(normalized_file, str):
        raise ValueError(f"Invalid file: expected a string or path-like object, got {type(file).__name__}")
    if sequencer_id is None:
        return text, normalized_file, _sequencer_id_from_file(normalized_file)
    if not isinstance(sequencer_id, str):
        raise ValueError(f"Invalid sequencer_id: expected a non-empty string, got {type(sequencer_id).__name__}")
    if not sequencer_id.strip():
        raise ValueError("Invalid sequencer_id: expected a non-empty string")
    return text, normalized_file, sequencer_id


def _validate_branch_policy(branch_policy: str) -> None:
    if not is_valid_branch_policy(branch_policy):
        raise ValueError(f"Invalid branch policy: {branch_policy}")


def _validate_params(params: Any) -> None:
    if params is None:
        return
    if not isinstance(params, Mapping):
        raise ValueError(f"Invalid params: expected a mapping/object, got {type(params).__name__}")
    try:
        validate_params_json_mapping(dict(params))
    except ValueError as exc:
        raise ValueError(f"Invalid params: {exc}") from exc


def _validate_documents(documents: Any) -> dict[str, str]:
    if not isinstance(documents, Mapping):
        raise ValueError(f"Invalid documents: expected a mapping/object, got {type(documents).__name__}")
    validated: dict[str, str] = {}
    for file, text in documents.items():
        try:
            normalized_file = os.fspath(file)
        except TypeError as exc:
            raise ValueError(
                f"Invalid documents: document keys must be strings or path-like objects, got {type(file).__name__}"
            ) from exc
        if not isinstance(normalized_file, str):
            raise ValueError(
                f"Invalid documents: document keys must be strings or path-like objects, got {type(file).__name__}"
            )
        if not isinstance(text, str):
            raise ValueError(f"Invalid documents: document contents must be strings, got {type(text).__name__}")
        validated[normalized_file] = text
    return validated


def _validate_alignment_options(
    alignment_mode: str,
    alignment_anchor_kinds: list[str] | tuple[str, ...] | None,
) -> tuple[str, ...]:
    if not isinstance(alignment_mode, str):
        raise ValueError(f"Invalid alignment policy: {alignment_mode}")
    if not is_valid_alignment_mode(alignment_mode):
        raise ValueError(f"Invalid alignment policy: {alignment_mode}")
    anchor_kinds = _normalize_alignment_anchor_kinds(alignment_anchor_kinds)
    if alignment_mode == "first_anchor" and not anchor_kinds:
        raise ValueError("alignment.anchor_kinds must list at least one event kind when alignment_mode is first_anchor")
    return anchor_kinds


def _normalize_alignment_anchor_kinds(
    alignment_anchor_kinds: list[str] | tuple[str, ...] | None,
) -> tuple[str, ...]:
    if alignment_anchor_kinds is None:
        return ()
    if isinstance(alignment_anchor_kinds, str):
        raise ValueError("alignment.anchor_kinds must be a list or tuple of non-empty strings")
    anchor_kinds: list[str] = []
    for item in alignment_anchor_kinds:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("alignment.anchor_kinds entries must be non-empty strings")
        anchor_kinds.append(item.strip())
    return tuple(dict.fromkeys(anchor_kinds))


def _sequencer_id_from_file(file: str) -> str:
    stem = Path(file).stem or "seq"
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").lower()
    if not normalized:
        return "seq"
    if normalized[0].isdigit():
        return f"seq_{normalized}"
    return normalized


def _unique_sequencer_ids(files: Any) -> dict[str, str]:
    seen: dict[str, int] = {}
    assigned: set[str] = set()
    sequencer_ids: dict[str, str] = {}
    for file in files:
        base = _sequencer_id_from_file(file)
        count = seen.get(base, 0) + 1
        candidate = base if count == 1 else f"{base}_{count}"
        while candidate in assigned:
            count += 1
            candidate = f"{base}_{count}"
        seen[base] = count
        assigned.add(candidate)
        sequencer_ids[file] = candidate
    return sequencer_ids
